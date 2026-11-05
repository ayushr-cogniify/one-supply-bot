"""S4: reason-code decision tree (Engineering appendix C). Rules run in order; the first that
passes sets the code. A rule whose input is missing returns 'unknown' and the walk stops to ask,
unless the branch can still be decided without it."""
import pandas as pd

from . import adapter
from .attainment import lines_for_sku, monthly
from .envelope import envelope

ACTIVE = {"A0", "B2", "B3", "C0", "D0", "E0", "J0", "L0", "O0", "P0", "Q0", "R0", "S0", "T0", "W0"}
ATTAINMENT_FLOOR = 90.0
PACING_LIMIT = 130.0


def _inv_at(material, lc, cut_date, inv, tolerance=3):
    x = inv[(inv.MATERIAL == str(material)) & (inv.PLANT == lc)]
    d = pd.Timestamp(cut_date)
    x = x[(x.snapshot_date <= d) & (x.snapshot_date >= d - pd.Timedelta(days=tolerance))]
    if x.empty:
        return None, None
    day = x.snapshot_date.max()
    return x[x.snapshot_date == day].sum(numeric_only=True), day


def _forecast_next(material, plan, weeks=9):
    if plan is None:
        return None
    wk = sorted(w for w in plan.CALWEEK.unique() if w != 197001)[:weeks]
    return float(plan[(plan.MATERIAL == str(material)) & plan.CALWEEK.isin(wk)].FORECAST.sum())


def walk_decision_tree(cut: dict, answers: dict = None, inv=None, att=None, plan="load") -> dict:
    """cut: {cut_id, material, lc, cut_date, order_issue_flag?, entry_error_flag?, ...}
    answers: planner answers / facts keyed by rule id or fact name
      (force_majeure, mgmt_decision, national_pct, lc_pct, supplier_delay, stock_issue, atp_basis)."""
    a = dict(answers or {})
    a.update({k: v for k, v in cut.items() if k.endswith("_flag")})
    inv = adapter.inventory() if inv is None else inv
    att = adapter.attainment() if att is None else att
    if isinstance(plan, str):
        try:
            plan = adapter.kinaxis_plan()
        except FileNotFoundError:
            plan = None
    path, needs = [], []
    m, lc, d = str(cut["material"]), cut["lc"], pd.Timestamp(cut["cut_date"])

    def step(rule, test, outcome, evidence=None, code=None):
        path.append({"rule_id": rule, "test": test, "outcome": outcome, "evidence": evidence})
        return code if outcome == "pass" else None

    def done(code, confidence):
        long = adapter.reason_codes().set_index("code").long_description.get(code) if code else None
        status = "ok" if code else "needs_input"
        return envelope("walk_decision_tree", status,
                        values={"cut_id": cut.get("cut_id"), "proposed_code": code, "long_description": long,
                                "confidence": confidence, "path": path, "needs_input": needs},
                        missing=[{"key": n, "template": "NO_DATA_PACING" if "pct" in n else None} for n in needs])

    # R01 - R02d: not a PFR miss / special cases (planner flags)
    if step("R01", "order rectification or customer change", "pass" if a.get("order_issue_flag") else "fail", code="O0"):
        return done("O0", "high")
    if step("R02a", "force majeure", "pass" if a.get("force_majeure") else "fail", code="J0"):
        return done("J0", "high")
    im = adapter.item_master()
    status = sorted(im[im.ITEMCODE == m].MATERIALSTATUS.dropna().unique().tolist())
    fcst = _forecast_next(m, plan)
    obsolete = bool(status) and set(status) <= {"ZZ", "Z4"} and fcst == 0
    if step("R02b", "obsolete: MATERIALSTATUS ZZ/Z4 and no forecast next 2 months",
            "pass" if obsolete else "fail", evidence={"MATERIALSTATUS": status, "forecast_next_9_weeks": fcst}, code="L0"):
        return done("L0", "medium")
    if step("R02d", "management decision", "pass" if a.get("mgmt_decision") else "fail", code="Q0"):
        return done("Q0", "high")

    # R03 / R04: pacing vs lag-1 forecast (not in extracts -> ask)
    for rule, key, code in [("R03", "national_pct", "B2"), ("R04", "lc_pct", "B3")]:
        v = a.get(key)
        if v is None:
            step(rule, f"{key} > {PACING_LIMIT}%", "unknown")
            needs.append(key)
        # A planner may answer the 130% question Yes/No instead of giving the %
        elif step(rule, f"{key} > {PACING_LIMIT}%",
                  "pass" if (v if isinstance(v, bool) else float(v) > PACING_LIMIT) else "fail",
                  evidence={key: v}, code=code):
            return done(code, "high")
    if needs:
        return done(None, None)

    # R05: stock-on-hand split
    row, day = _inv_at(m, lc, d, inv)
    if row is None:
        step("R05", "inventory snapshot within 3 days before cut", "unknown")
        needs.append("inventory_snapshot")
        return done(None, None)
    basis = a.get("atp_basis", "ATP_AFTER_ALLOCATION")
    atp = float(row[basis])
    step("R05", f"{basis} > 0 at {lc}", "pass" if atp > 0 else "fail",
         evidence={"snapshot_date": str(day.date()), "ATP_QUANTITY": float(row.ATP_QUANTITY),
                   "ALLOCATION_QTY": float(row.ALLOCATION_QTY), "ATP_AFTER_ALLOCATION": float(row.ATP_AFTER_ALLOCATION)})

    if atp <= 0:
        # R06 factory attainment < 90% in cut month or prior month
        ym = d.year * 100 + d.month
        prev = (d - pd.DateOffset(months=1))
        months = [prev.year * 100 + prev.month, ym]
        evidence, low = [], False
        for wc in lines_for_sku(m, att):
            g = monthly(wc, att)
            g = g[g.year_month.isin(months) & (g.fix_cases > 0)]
            for r in g.itertuples():
                evidence.append({"work_center": wc, "year_month": r.year_month, "attainment_pct": round(r.attainment_pct, 1)})
                low |= r.attainment_pct < ATTAINMENT_FLOOR
        if step("R06", f"attainment < {ATTAINMENT_FLOOR}% (cut or prior month)", "pass" if low else
                ("fail" if evidence else "unknown"), evidence=evidence, code="C0"):
            return done("C0", "medium")
        if step("R07", "inbound transfer not received", "pass" if a.get("inbound_delay") or float(row.INTRANSIT_QTY) > 0
                else "fail", evidence={"INTRANSIT_QTY": float(row.INTRANSIT_QTY)}, code="W0"):
            return done("W0", "medium")
        if step("R08", "blocked or quality stock", "pass" if float(row.BLOCKED_INV) + float(row.QUAL_INSPEC) > 0 or
                a.get("quality_hold") else "fail",
                evidence={"BLOCKED_INV": float(row.BLOCKED_INV), "QUAL_INSPEC": float(row.QUAL_INSPEC)}, code="A0"):
            return done("A0", "medium")
        ln = adapter.lanes()
        buy = (ln[(ln.PART == m) & (ln.TYPE == "Buy")]).shape[0] > 0
        if step("R09", "bought-in item with supplier delay", "pass" if buy and a.get("supplier_delay") else "fail", code="D0"):
            return done("D0", "medium")
        if step("R10", "stock at another LC while cutting LC short", "pass" if a.get("network_imbalance") else "fail", code="R0"):
            return done("R0", "low")
        if step("R11", "internal entry error", "pass" if a.get("entry_error_flag") else "fail", code="P0"):
            return done("P0", "high")
    else:
        if step("R12", "allocation / reservation quota", "pass" if float(row.ALLOCATION_QTY) > 0 else "fail",
                evidence={"ALLOCATION_QTY": float(row.ALLOCATION_QTY)}, code="Q0"):
            return done("Q0", "medium")
        if step("R13", "internal entry error", "pass" if a.get("entry_error_flag") else "fail", code="P0"):
            return done("P0", "high")
        if step("R14", "expiry / wrong location / substitute", "pass" if a.get("stock_issue") else "fail", code="R0"):
            return done("R0", "low")
    step("R99", "no rule passed", "pass")
    return done(None, None)

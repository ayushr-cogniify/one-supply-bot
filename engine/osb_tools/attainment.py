"""S2: factory attainment. attainment = sum(actual) / sum(fix) per work centre and month,
aggregated BEFORE dividing; months with sum(fix) = 0 are excluded (appendix D)."""
import pandas as pd

from . import adapter, config
from .envelope import cite, envelope, table
from .woc import add_woc, series

WORK_CENTRE_SITE = {"BA": "USA2", "SL": "USR2", "PL": "USR2"}   # Belle Mead / St. Peters factories


def factory_site(work_center: str) -> str:
    return WORK_CENTRE_SITE.get(work_center[:2])


def _months_back(latest: int, n: int):
    y, m = divmod(latest, 100)
    out = []
    for _ in range(n):
        out.append(y * 100 + m)
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return sorted(out)


def monthly(work_center: str, att: pd.DataFrame = None) -> pd.DataFrame:
    att = adapter.attainment() if att is None else att
    df = att[att.work_center == work_center]
    g = df.groupby("year_month").agg(fix_cases=("fix", "sum"), actual_cases=("actual", "sum")).reset_index()
    g["attainment_pct"] = [None if f == 0 else a / f * 100 for f, a in zip(g.fix_cases, g.actual_cases)]
    return g


def get_attainment(work_center: str, months: int = 3, att: pd.DataFrame = None) -> dict:
    att = adapter.attainment() if att is None else att
    if work_center not in set(att.work_center.dropna()):
        return envelope("get_attainment", "no_data", values={"work_center": work_center},
                        missing=[{"key": "work_center", "id": work_center}])
    latest = int(att.year_month.max())
    window = _months_back(latest, months)          # calendar months, anchored on latest month in the file
    g = monthly(work_center, att)
    w = g[g.year_month.isin(window) & (g.fix_cases > 0)]
    warnings = []
    excluded = g[g.year_month.isin(window) & (g.fix_cases == 0)].year_month.tolist()
    if excluded:
        warnings.append(f"Months excluded (Fix = 0): {excluded}")
    if (att.work_center == work_center).sum() < 10:
        warnings.append("Thin history: fewer than 10 order rows for this work centre")
    trailing = None if w.fix_cases.sum() == 0 else round(w.actual_cases.sum() / w.fix_cases.sum() * 100, 1)
    recent = att[(att.work_center == work_center) & att.year_month.isin(window)]
    skus = (recent.groupby("material").actual.sum().sort_values(ascending=False))
    show = g[g.year_month >= window[0] - 100].copy()
    show["attainment_pct"] = show.attainment_pct.map(lambda v: None if v is None or pd.isna(v) else round(v))
    return envelope("get_attainment",
                    values={"work_center": work_center, "factory_site": factory_site(work_center),
                            "window": window, "trailing_avg_pct": trailing,
                            "fix_cases_window": int(w.fix_cases.sum()), "actual_cases_window": int(w.actual_cases.sum()),
                            "skus_on_line": [{"material": m, "actual_cases": int(v)} for m, v in skus.items()]},
                    table=table(show), rows_used=cite(recent), warnings=warnings)


def lines_for_sku(material: str, att: pd.DataFrame = None, since: int = 202601):
    att = adapter.attainment() if att is None else att
    return sorted(att[(att.material == str(material)) & (att.year_month >= since)].work_center.dropna().unique())


def simulate_attainment(work_center: str, attainment_pct: float, horizon_weeks: int = 8,
                        extra_demand: dict = None, basis: str = "all_production",
                        plan: pd.DataFrame = None, att: pd.DataFrame = None) -> dict:
    """S2 (workflow doc): apply attainment to the line's production for every SKU on it, recompute
    network WoC, quantify the PFR impact (projected unserved cases with and without the change),
    propose leveling within the line, and say whether a constraint call is needed.

    Shortfall = production x (1 - pct), taken off network stock from the production week on.
    extra_demand: {material, week, delta_cases}; with no material the cases are spread across the
    line's SKUs by their demand in the horizon (the workflow's "+35k increase" on the line)."""
    plan = adapter.kinaxis_plan() if plan is None else plan
    att = adapter.attainment() if att is None else att
    site = factory_site(work_center)
    base = get_attainment(work_center, att=att)
    skus = [s["material"] for s in base["values"].get("skus_on_line", [])]
    if not skus:
        return envelope("simulate_attainment", "no_data", values={"work_center": work_center})
    cols = {"planned": ["PROD_PLANNED_"], "confirmed": ["PROD_CONFIRMED"],
            "all_production": ["PROD_PLANNED_", "PROD_CONFIRMED"]}[basis]
    multi = [m for m in skus if len(lines_for_sku(m, att)) > 1]
    c2e = adapter.item_master().drop_duplicates("ITEMCODE").set_index("ITEMCODE")["CASETOEACH"]

    frames = {}
    for m in skus:
        net = series(plan, m, "NETWORK")
        if net.empty:
            continue
        fac = plan[(plan.MATERIAL == m) & (plan.LOCATION == site) & (plan.CALWEEK != config.OPENING_BUCKET)]
        prod = fac.groupby("CALWEEK")[cols].sum().sum(axis=1).reindex(net.CALWEEK, fill_value=0).values
        frames[m] = (net, prod)
    if not frames:
        return envelope("simulate_attainment", "no_data", values={"work_center": work_center})

    # Extra demand: one item, or spread across the line's SKUs by horizon demand
    split = {}
    if extra_demand:
        wk = int(extra_demand.get("week") or frames[next(iter(frames))][0].CALWEEK.iloc[0])
        d = int(extra_demand["delta_cases"])
        if extra_demand.get("material"):
            split = {str(extra_demand["material"]): d}
        else:
            dem = {m: float(f[0].head(horizon_weeks).TOTAL_DEMAND.sum()) for m, f in frames.items()}
            tot = sum(dem.values()) or 1
            split = {m: int(round(d * v / tot)) for m, v in dem.items() if v > 0}
            if split:
                split[max(split, key=split.get)] += d - sum(split.values())
        extra_demand = {**extra_demand, "week": wk, "split": split}

    rows, below, after_frames = [], [], {}
    unserved_b = unserved_a = demand_total = 0
    for m, (net, prod) in frames.items():
        after = net.copy()
        after["STOCK_PLANNED"] = after["STOCK_PLANNED"] - (prod * (1 - attainment_pct / 100)).cumsum()
        if split.get(m):
            wk = extra_demand["week"]
            after.loc[after.CALWEEK == wk, "TOTAL_DEMAND"] += split[m]
            after.loc[after.CALWEEK >= wk, "STOCK_PLANNED"] -= split[m]
        after_frames[m] = after
        b, a = add_woc(net).head(horizon_weeks), add_woc(after).head(horizon_weeks)
        unserved_b += max(0.0, -float(b.STOCK_PLANNED.min()))
        unserved_a += max(0.0, -float(a.STOCK_PLANNED.min()))
        demand_total += float(a.TOTAL_DEMAND.sum())
        for (_, rb), (_, ra), pq in zip(b.iterrows(), a.iterrows(), prod):
            rows.append({"material": m, "calweek": int(rb.CALWEEK), "production_cases": int(pq),
                         "woc_before": None if rb.woc is None else round(rb.woc, 1),
                         "woc_after": None if ra.woc is None else round(ra.woc, 1),
                         "colour_before": rb.colour, "colour_after": ra.colour})
        if (a.colour == "red").any() and not (b.colour == "red").all():
            below.append(m)
    df = pd.DataFrame(rows)
    shortfall = int((df.production_cases * (1 - attainment_pct / 100)).sum()) if not df.empty else 0

    leveling = _level(after_frames, frames, attainment_pct, horizon_weeks, c2e)
    remaining = leveling["remaining_below_floor"]
    constraint = bool(remaining)
    pfr_before = round((1 - unserved_b / demand_total) * 100, 1) if demand_total else None
    pfr_after = round((1 - unserved_a / demand_total) * 100, 1) if demand_total else None
    return envelope("simulate_attainment",
                    values={"work_center": work_center, "factory_site": site, "attainment_pct": attainment_pct,
                            "baseline_trailing_pct": base["values"]["trailing_avg_pct"], "basis": basis,
                            "skus_below_floor": below, "production_shortfall_cases": shortfall,
                            "skus_on_multiple_lines": multi, "extra_demand": extra_demand,
                            "unserved_cases_before": int(round(unserved_b)), "unserved_cases_after": int(round(unserved_a)),
                            "horizon_demand_cases": int(round(demand_total)),
                            "fill_rate_before_pct": pfr_before, "fill_rate_after_pct": pfr_after,
                            "leveling": leveling, "constraint_call": constraint},
                    table=table(df),
                    warnings=["Network level: LC split of the shortfall and transfer lead times are not modelled",
                              f"Basis '{basis}': the workflow says planned orders only; near-term St. Peters output sits in "
                              "PROD_CONFIRMED in this extract, so planned-only would show no impact. Confirm with Rushi."]
                    + ([f"SKUs also on other lines (CLARIFY_LINE): {multi}"] if multi else []))


def _level(after_frames, frames, pct, horizon, c2e, floor=2.0, donor_floor=4.0):
    """Greedy leveling within the line (workflow doc S2 step 4): week by week, move production from
    SKUs above 5 weeks of cover (blue) to SKUs below 2 weeks, in units (cases x case-to-each), keeping
    every donor at or above 4 weeks. Returns the moves and the SKUs still below 2 weeks."""
    cur = {m: f.copy() for m, f in after_frames.items()}
    weeks = list(next(iter(cur.values())).CALWEEK.head(horizon))
    moves = []
    for wi, w in enumerate(weeks):
        wocs = {m: add_woc(f) for m, f in cur.items()}
        row = {m: x[x.CALWEEK == w].iloc[0] for m, x in wocs.items() if not x[x.CALWEEK == w].empty}
        needs = sorted(((m, r) for m, r in row.items() if r.woc is not None and r.woc < floor and r.avg_fwd_demand),
                       key=lambda t: t[1].woc)
        donors = sorted(((m, r) for m, r in row.items() if r.woc is not None and r.woc > 5 and r.avg_fwd_demand),
                        key=lambda t: -t[1].woc)
        # Each donor can give at most what it makes that week, and only down to 4 weeks of cover
        budget = {m: min(float(frames[m][1][wi]) * pct / 100, r.STOCK_PLANNED - donor_floor * r.avg_fwd_demand)
                  for m, r in donors}
        for m_to, r_to in needs:
            need_cases = floor * r_to.avg_fwd_demand - r_to.STOCK_PLANNED
            for m_from, r_from in donors:
                if need_cases <= 0:
                    break
                spare = budget.get(m_from, 0)
                if spare < 1:
                    continue
                e_from, e_to = float(c2e.get(m_from, 1) or 1), float(c2e.get(m_to, 1) or 1)
                give_units = min(spare * e_from, need_cases * e_to)
                give_from, get_to = give_units / e_from, give_units / e_to
                if give_from < 1:
                    continue
                cur[m_from].loc[cur[m_from].CALWEEK >= w, "STOCK_PLANNED"] -= give_from
                cur[m_to].loc[cur[m_to].CALWEEK >= w, "STOCK_PLANNED"] += get_to
                budget[m_from] = spare - give_from
                need_cases -= get_to
                moves.append({"from_sku": m_from, "to_sku": m_to, "calweek": int(w),
                              "cases_from": int(round(give_from)), "cases_to": int(round(get_to)),
                              "units": int(round(give_units))})
    final = {m: add_woc(f).head(horizon) for m, f in cur.items()}
    remaining = sorted(m for m, x in final.items() if (x.colour == "red").any())
    return {"moves": moves, "remaining_below_floor": remaining,
            "skus_fixed": sorted({mv["to_sku"] for mv in moves} - set(remaining))}

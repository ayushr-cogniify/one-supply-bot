"""Workflow tools that follow the scenario flowcharts end to end (Scenario workflows doc, Oct 15).
Every number is computed from the delivered plan; nothing is forecast or projected beyond it.

assess_demand_change  S1: WoC per LC and network before/after, flags, support-until week, why each
                      dip happens (supply vs demand that week, next inbound), cases needed to stay
                      out of red, donor LCs with stock above target and their lanes, factory supply.
segment_growth_cover  S3 demand side: does current inventory and cover support segment growth?
line_plan_capacity    S3 capacity reference when rated capacity is not entered: the line's fixed
                      (planned) volume from the attainment extract, in units.
network_dashboard     Network view: WoC by segment and LC, red items, factory lines and attainment."""
import math
from datetime import timedelta

import pandas as pd

from . import adapter, config
from .envelope import cite, envelope, table
from .woc import add_woc, apply_demand_change, calendar, colour, series

RED, TARGET = 2.0, 4.0
FACTORIES = ["USR2", "USA2"]


def _w(v):
    return None if v is None or pd.isna(v) else round(float(v), 1)


def _week_after(cal, calweek, days):
    """The plan week a shipment leaving in `calweek` lands in, after `days` of lead time."""
    weeks = list(cal.CALWEEK)
    if calweek not in weeks:
        return None
    i = weeks.index(calweek) + math.ceil(days / 7)
    return int(weeks[i]) if i < len(weeks) else None


def assess_demand_change(material: str, lc: str, week: int, delta_cases: int = None, delta_pct: float = None,
                         window_weeks: int = config.WOC_WINDOW_WEEKS, horizon_weeks: int = 8, plan=None) -> dict:
    """lc = one LC, or NETWORK: the change is spread across the four LCs in proportion to each LC's
    total demand that week (stated in `split`), and the worst LC after the change is examined."""
    plan = adapter.kinaxis_plan() if plan is None else plan
    material = str(material)
    if lc not in config.LCS and lc != "NETWORK":
        return envelope("assess_demand_change", "needs_input", missing=[{"key": "lc", "template": "CLARIFY_SCOPE"}])
    week_demand = {}
    for loc in config.LCS:
        sl = series(plan, material, loc)
        if not sl.empty and week in set(sl.CALWEEK):
            week_demand[loc] = float(sl.loc[sl.CALWEEK == week, "TOTAL_DEMAND"].iloc[0])
    if not week_demand or (lc != "NETWORK" and lc not in week_demand):
        return envelope("assess_demand_change", "no_data",
                        missing=[{"key": "material", "id": material, "template": "NO_DATA_ITEM"}])
    base_demand = sum(week_demand.values()) if lc == "NETWORK" else week_demand[lc]
    if delta_cases is None:
        if delta_pct is None:
            return envelope("assess_demand_change", "needs_input", missing=[{"key": "delta_cases"}])
        delta_cases = int(round(base_demand * delta_pct / 100))
    if lc == "NETWORK":
        tot = sum(week_demand.values())
        split = {loc: int(round(delta_cases * (d / tot if tot else 1 / len(week_demand)))) for loc, d in week_demand.items()}
        split[max(split, key=split.get)] += delta_cases - sum(split.values())     # exact total
    else:
        split = {lc: int(delta_cases)}
    cal = calendar(plan)

    frames, rows, rows_used = {}, [], []
    for loc in config.LCS:
        b = series(plan, material, loc)
        if b.empty:
            continue
        a = apply_demand_change(b, week, split[loc]) if split.get(loc) else b
        frames[loc] = (add_woc(b, window_weeks), add_woc(a, window_weeks))
    if not frames:
        return envelope("assess_demand_change", "no_data", missing=[{"key": "material", "id": material}])
    sb = pd.concat([f[0][["CALWEEK", "STOCK_PLANNED", "TOTAL_DEMAND", "TOTAL_SUPPLY"]] for f in frames.values()])
    sa = pd.concat([f[1][["CALWEEK", "STOCK_PLANNED", "TOTAL_DEMAND", "TOTAL_SUPPLY"]] for f in frames.values()])
    frames["NETWORK"] = (add_woc(sb.groupby("CALWEEK", as_index=False).sum(), window_weeks),
                         add_woc(sa.groupby("CALWEEK", as_index=False).sum(), window_weeks))

    first_lc = lc if lc in frames else next(iter(split))
    horizon = [w for w in sorted(frames[first_lc][0].CALWEEK) if w >= week][:horizon_weeks]
    for loc, (b, a) in frames.items():
        bi, ai = b.set_index("CALWEEK"), a.set_index("CALWEEK")
        for w in horizon:
            if w not in bi.index:
                continue
            rb, ra = bi.loc[w], ai.loc[w]
            rows.append({"location": loc, "calweek": int(w), "woc_before": _w(rb.woc), "woc_after": _w(ra.woc),
                         "colour_before": colour(rb.woc), "colour_after": colour(ra.woc),
                         "stock_before": int(rb.STOCK_PLANNED), "stock_after": int(ra.STOCK_PLANNED),
                         "demand": int(round(ra.TOTAL_DEMAND)), "supply": int(round(ra.TOTAL_SUPPLY)),
                         "avg_fwd_demand": None if pd.isna(ra.avg_fwd_demand) else int(round(ra.avg_fwd_demand))})
        if loc != "NETWORK" and "rows" in frames[loc][0].columns:
            for rr in frames[loc][0][frames[loc][0].CALWEEK.isin(horizon)]["rows"]:
                rows_used += [{"source_file": plan["_file"].iloc[0], "source_sheet": plan["_sheet"].iloc[0],
                               "source_row": int(x), "hash": plan["_hash"].iloc[0]} for x in rr]
    df = pd.DataFrame(rows)

    def newly_red(loc):
        x = df[df.location == loc]
        return x[(x.colour_after == "red") & ((x.colour_before != "red") | (x.woc_after < x.woc_before))]

    flagged = sorted(l for l in config.LCS if not newly_red(l).empty)
    requested = lc
    if lc == "NETWORK":                        # examine the worst LC after the change
        lc_rows = df[(df.location.isin(split)) & df.woc_after.notna()]
        lc = lc_rows.sort_values(["woc_after", "calweek"]).location.iloc[0] if not lc_rows.empty else next(iter(split))
    net_red = not newly_red("NETWORK").empty
    verdict = "do_not_support" if net_red else ("support_with_replan" if flagged else "support")

    def support_until(loc):
        x = df[df.location == loc].sort_values("calweek")
        nr = newly_red(loc)
        if nr.empty:
            return {"through": int(x.calweek.max()), "first_red": None}
        first = int(nr.calweek.min())
        before = x[x.calweek < first]
        return {"through": int(before.calweek.max()) if not before.empty else None, "first_red": first}

    # Why: for each week the target LC is below target after the change, supply vs demand that week
    # and when the next inbound lands (the "inbound lands in week 43" explanation in the workflow doc).
    t = df[df.location == lc].sort_values("calweek").reset_index(drop=True)
    dips = []
    for i, r in t.iterrows():
        if r.woc_after is None or r.woc_after >= TARGET or r.colour_after == r.colour_before == "green":
            continue
        later = t[(t.calweek > r.calweek) & (t.supply > 0)]
        dips.append({"calweek": int(r.calweek), "woc_before": r.woc_before, "colour_before": r.colour_before,
                     "woc_after": r.woc_after, "colour_after": r.colour_after,
                     "stock_after": int(r.stock_after), "supply": int(r.supply), "demand": int(r.demand),
                     "next_inbound_week": int(later.calweek.iloc[0]) if not later.empty else None,
                     "next_inbound_cases": int(later.supply.iloc[0]) if not later.empty else None})
        if len(dips) == 3:
            break

    # Cases the target LC needs to stay at or above 2 weeks of cover in every week shown
    need = [max(0.0, RED * r.avg_fwd_demand - r.stock_after) for r in t.itertuples() if r.avg_fwd_demand]
    shortfall = int(math.ceil(max(need))) if need else 0

    # Donors: other LCs holding stock above 4 weeks of cover in the change week
    donors = []
    for loc in config.LCS:
        if loc == lc:
            continue
        x = df[(df.location == loc) & (df.calweek == week)]
        if x.empty or x.avg_fwd_demand.iloc[0] in (None, 0):
            continue
        r = x.iloc[0]
        excess = int(r.stock_before - TARGET * r.avg_fwd_demand)
        above_red = int(r.stock_before - RED * r.avg_fwd_demand)
        if above_red <= 0:
            continue
        ln = adapter.lanes()
        lane = ln[(ln.PART == material) & (ln.SOURCE_SITE == loc) & (ln.DESTINATION_SITE == lc) & ln.active]
        tr = adapter.transport()
        tr = tr[(tr.sap_kinaxis_code_origin == loc) & (tr.sap_kinaxis_code_dest == lc)].sort_values("cost_per_load_lh_usd")
        lt = int(lane["LEADTIME DAYS"].iloc[0]) if not lane.empty else None
        donors.append({"location": loc, "woc": r.woc_before, "colour": r.colour_before, "stock": int(r.stock_before),
                       "excess_above_4_weeks": max(excess, 0), "excess_above_2_weeks": above_red, "leadtime_days": lt,
                       "arrives_week": _week_after(cal, week, lt) if lt is not None else None,
                       "cost_per_load": None if tr.empty else float(tr.cost_per_load_lh_usd.iloc[0]),
                       "mode": None if tr.empty else str(tr.bid_mode_type.iloc[0])})
    donors.sort(key=lambda d: (-d["excess_above_4_weeks"], -d["excess_above_2_weeks"]))

    # Factory supply: planned and confirmed production of this item in the horizon
    fac = plan[(plan.MATERIAL == material) & plan.LOCATION.isin(FACTORIES) & plan.CALWEEK.isin(horizon)]
    factory = []
    for site, g in fac.groupby("LOCATION"):
        prod = g[["PROD_PLANNED_", "PROD_CONFIRMED"]].sum(axis=1)
        if prod.sum() <= 0:
            continue
        ln = adapter.lanes()
        lane = ln[(ln.PART == material) & (ln.SOURCE_SITE == site) & (ln.DESTINATION_SITE == lc) & ln.active]
        factory.append({"site": site, "planned_cases": int(prod.sum()),
                        "weeks_with_production": [int(w) for w, p in zip(g.CALWEEK, prod) if p > 0][:6],
                        "leadtime_days": int(lane["LEADTIME DAYS"].iloc[0]) if not lane.empty else None})

    net = df[df.location == "NETWORK"].sort_values("calweek")
    tgt0 = t.iloc[0] if not t.empty else None
    return envelope("assess_demand_change", values={
        "material": material, "lc": lc, "requested_scope": requested, "split": split,
        "week": int(week), "delta_cases": int(delta_cases),
        "delta_pct": delta_pct, "base_demand_cases": int(round(base_demand)), "window_weeks": window_weeks,
        "horizon_weeks": len(horizon), "verdict": verdict, "flagged_locations": flagged,
        "network_before": _w(net.woc_before.iloc[0]) if not net.empty else None,
        "network_after": _w(net.woc_after.iloc[0]) if not net.empty else None,
        "network_colour_after": net.colour_after.iloc[0] if not net.empty else None,
        "lc_before": tgt0.woc_before if tgt0 is not None else None,
        "lc_after": tgt0.woc_after if tgt0 is not None else None,
        "lc_min_after": _w(t.woc_after.min()) if not t.empty else None,
        "lc_min_week": int(t.loc[t.woc_after.idxmin(), "calweek"]) if not t.empty and t.woc_after.notna().any() else None,
        "support_until": {"lc": support_until(lc), "network": support_until("NETWORK")},
        "dips": dips, "cases_to_stay_out_of_red": shortfall, "donors": donors, "factory": factory,
        "red_threshold": RED, "target_threshold": TARGET},
        table=table(df), rows_used=rows_used[:200])


def _growth_months(cal, month_from, months):
    cal = cal.copy()
    cal["month"] = cal.week_start.map(lambda d: (d + timedelta(days=6)).strftime("%Y-%m"))
    ms = sorted(cal.month.unique())
    start = month_from if month_from in ms else ms[1] if len(ms) > 1 else ms[0]
    keep = [m for m in ms if m >= start][:months]
    return set(cal[cal.month.isin(keep)].CALWEEK), keep


def segment_growth_cover(selector: dict, growth_pct: float, month_from: str = None, months: int = 1,
                         window_weeks: int = config.WOC_WINDOW_WEEKS, plan=None) -> dict:
    """Apply growth to each segment item's total demand in the growth months (at every LC), carry the
    extra demand through projected stock, and recompute WoC. Answers 'does current inventory and
    cover support this growth?' without any capacity input."""
    from .capacity import select_materials
    plan = adapter.kinaxis_plan() if plan is None else plan
    mats = select_materials(selector.get("consumer_size"), selector.get("segment_desc"), selector.get("materials"), plan)
    cal = calendar(plan)
    weeks, month_list = _growth_months(cal, month_from, months)
    items = []
    for m in mats:
        net_b, net_a = [], []
        lc_flags = []
        for loc in config.LCS:
            b = series(plan, m, loc)
            if b.empty or b.TOTAL_DEMAND.sum() == 0:
                continue
            a = b.copy()
            extra = (a.TOTAL_DEMAND * growth_pct / 100).where(a.CALWEEK.isin(weeks), 0)
            a["TOTAL_DEMAND"] = a.TOTAL_DEMAND + extra
            a["STOCK_PLANNED"] = a.STOCK_PLANNED - extra.cumsum()
            wb, wa = add_woc(b, window_weeks), add_woc(a, window_weeks)
            inwin = wa.CALWEEK.isin(weeks)
            new_red = wa[inwin & (wa.colour == "red")]
            if not new_red.empty and not (wb[inwin].colour == "red").all():
                lc_flags.append({"location": loc, "first_red_week": int(new_red.CALWEEK.min()),
                                 "min_woc": _w(wa[inwin].woc.min())})
            net_b.append(b[["CALWEEK", "STOCK_PLANNED", "TOTAL_DEMAND"]])
            net_a.append(a[["CALWEEK", "STOCK_PLANNED", "TOTAL_DEMAND"]])
        if not net_b:
            continue
        nb = add_woc(pd.concat(net_b).groupby("CALWEEK", as_index=False).sum(), window_weeks)
        na = add_woc(pd.concat(net_a).groupby("CALWEEK", as_index=False).sum(), window_weeks)
        nb_w, na_w = nb[nb.CALWEEK.isin(weeks)], na[na.CALWEEK.isin(weeks)]
        if nb_w.empty:
            continue
        extra_cases = int(round(((na.TOTAL_DEMAND - nb.TOTAL_DEMAND)).sum()))
        items.append({"material": m, "extra_cases": extra_cases,
                      "network_min_before": _w(nb_w.woc.min()), "network_min_after": _w(na_w.woc.min()),
                      "colour_before": colour(nb_w.woc.min()), "colour_after": colour(na_w.woc.min()),
                      "lcs_turning_red": [f["location"] for f in lc_flags],
                      "first_red_week": min((f["first_red_week"] for f in lc_flags), default=None)})
    if not items:
        return envelope("segment_growth_cover", "no_data", values={"materials": mats})
    df = pd.DataFrame(items).sort_values(["network_min_after", "material"]).reset_index(drop=True)
    at_risk = df[df.colour_after == "red"].material.tolist()
    lc_risk = df[df.lcs_turning_red.map(len) > 0].material.tolist()
    return envelope("segment_growth_cover", values={
        "materials": mats, "growth_pct": growth_pct, "months": month_list, "window_weeks": window_weeks,
        "items": len(df), "extra_cases_total": int(df.extra_cases.sum()),
        "items_red_network": at_risk, "items_red_network_count": len(at_risk), "items_red_at_an_lc": lc_risk,
        "supported": not at_risk and not lc_risk}, table=table(df))


def line_plan_capacity(work_center: str, months: int = 3, att=None) -> dict:
    """Reference capacity when rated capacity is not entered: the line's average fixed (planned)
    volume per month over the latest `months` months, converted to units with CASETOEACH."""
    att = adapter.attainment() if att is None else att
    x = att[att.work_center == work_center]
    if x.empty:
        return envelope("line_plan_capacity", "no_data", values={"work_center": work_center})
    latest = int(att.year_month.max())
    y, m = divmod(latest, 100)
    window = []
    for _ in range(months):
        window.append(y * 100 + m)
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    x = x[x.year_month.isin(window)]
    c2e = adapter.item_master().drop_duplicates("ITEMCODE").set_index("ITEMCODE")["CASETOEACH"]
    x = x.assign(fix_units=x.fix * x.material.map(c2e).fillna(1), actual_units=x.actual * x.material.map(c2e).fillna(1))
    g = x.groupby("year_month")[["fix_units", "actual_units"]].sum()
    g = g[g.fix_units > 0]
    if g.empty:
        return envelope("line_plan_capacity", "no_data", values={"work_center": work_center})
    return envelope("line_plan_capacity", values={
        "work_center": work_center, "window": [int(v) for v in g.index],
        "avg_fixed_units_per_month": int(round(g.fix_units.mean())),
        "avg_actual_units_per_month": int(round(g.actual_units.mean())),
        "basis": "average fixed (planned) volume on the line, units, latest months with a plan"},
        table=table(g.reset_index()), rows_used=cite(x))


def network_dashboard(week_index: int = 0, window_weeks: int = config.WOC_WINDOW_WEEKS, plan=None, att=None) -> dict:
    """Network view for one plan week: WoC by segment (BSG group and size) and LC, items in red per
    LC, and the factory lines with trailing 3-month attainment."""
    from .attainment import get_attainment
    plan = adapter.kinaxis_plan() if plan is None else plan
    att = adapter.attainment() if att is None else att
    km = adapter.kinaxis_master()
    size = km[km.site.isin(config.LCS)].drop_duplicates("product").set_index("product")["consumer_product_size_id"]
    weeks = sorted(w for w in plan.CALWEEK.unique() if w != config.OPENING_BUCKET)
    wk = int(weeks[min(week_index, len(weeks) - 1)])
    lc_plan = plan[plan.LOCATION.isin(config.LCS) & (plan.CALWEEK != config.OPENING_BUCKET)]
    groups = lc_plan.drop_duplicates("MATERIAL").set_index("MATERIAL")["BSG_GROUP"]
    rows, items = [], []
    agg = lc_plan.assign(size=lc_plan.MATERIAL.map(size).fillna(0).astype(int),
                         grp=lc_plan.MATERIAL.map(groups))
    for (grp, sz), g in agg.groupby(["grp", "size"]):
        if g.TOTAL_DEMAND.sum() == 0:
            continue
        row = {"segment": f"{grp} {sz} oz" if sz else f"{grp} (size n/a)", "items": int(g.MATERIAL.nunique())}
        for loc in config.LCS + ["NETWORK"]:
            locs = config.LCS if loc == "NETWORK" else [loc]
            s = g[g.LOCATION.isin(locs)].groupby("CALWEEK", as_index=False)[["STOCK_PLANNED", "TOTAL_DEMAND"]].sum()
            s = add_woc(s.sort_values("CALWEEK").reset_index(drop=True), window_weeks)
            r = s[s.CALWEEK == wk]
            row[loc] = _w(r.woc.iloc[0]) if not r.empty else None
        rows.append(row)
    for loc in config.LCS:
        reds = 0
        for m in lc_plan[lc_plan.LOCATION == loc].MATERIAL.unique():
            s = add_woc(series(plan, m, loc), window_weeks)
            r = s[s.CALWEEK == wk]
            if not r.empty and r.colour.iloc[0] == "red":
                reds += 1
        items.append({"location": loc, "items_red": reds})
    lines = []
    latest = int(att.year_month.max())
    for w in sorted(att[att.year_month >= latest - 2].work_center.dropna().unique()):
        a = get_attainment(w, att=att)["values"]
        if a.get("trailing_avg_pct") is not None:
            lines.append({"work_center": w, "factory": a.get("factory_site"), "trailing_pct": a["trailing_avg_pct"],
                          "fixed_cases_3m": a.get("fix_cases_window"), "actual_cases_3m": a.get("actual_cases_window")})
    seg = pd.DataFrame(rows).sort_values(["segment"]).reset_index(drop=True)
    return envelope("network_dashboard", values={"calweek": wk, "window_weeks": window_weeks,
                                                 "red_items_by_lc": items, "lines": lines},
                    table=table(seg))

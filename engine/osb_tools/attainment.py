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
    """Apply attainment to the factory production of every SKU on the line and recompute
    NETWORK WoC. Shortfall = production x (1 - pct), subtracted cumulatively from network stock
    from the production week on (transfer lead time not yet modelled - see warnings)."""
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
    rows, below = [], []
    for m in skus:
        net = series(plan, m, "NETWORK")
        if net.empty:
            continue
        fac = plan[(plan.MATERIAL == m) & (plan.LOCATION == site) & (plan.CALWEEK != config.OPENING_BUCKET)]
        prod = fac.groupby("CALWEEK")[cols].sum().sum(axis=1).reindex(net.CALWEEK, fill_value=0).values
        after = net.copy()
        after["STOCK_PLANNED"] = after["STOCK_PLANNED"] - (prod * (1 - attainment_pct / 100)).cumsum()
        if extra_demand and str(extra_demand.get("material")) == m:
            wk, d = int(extra_demand["week"]), extra_demand["delta_cases"]
            after.loc[after.CALWEEK == wk, "TOTAL_DEMAND"] += d
            after.loc[after.CALWEEK >= wk, "STOCK_PLANNED"] -= d
        b, a = add_woc(net).head(horizon_weeks), add_woc(after).head(horizon_weeks)
        for (_, rb), (_, ra), p in zip(b.iterrows(), a.iterrows(), prod):
            rows.append({"material": m, "calweek": int(rb.CALWEEK), "production_cases": int(p),
                         "woc_before": None if rb.woc is None else round(rb.woc, 1),
                         "woc_after": None if ra.woc is None else round(ra.woc, 1),
                         "colour_before": rb.colour, "colour_after": ra.colour})
        if (a.colour == "red").any() and not (b.colour == "red").all():
            below.append(m)
    df = pd.DataFrame(rows)
    shortfall = int((df.production_cases * (1 - attainment_pct / 100)).sum()) if not df.empty else 0
    return envelope("simulate_attainment",
                    values={"work_center": work_center, "factory_site": site, "attainment_pct": attainment_pct,
                            "baseline_trailing_pct": base["values"]["trailing_avg_pct"], "basis": basis,
                            "skus_below_floor": below, "production_shortfall_cases": shortfall,
                            "skus_on_multiple_lines": multi, "extra_demand": extra_demand},
                    table=table(df),
                    warnings=["Network level only: LC split of the shortfall and transfer lead times not modelled yet",
                              f"Basis '{basis}': confirm with Rushi whether attainment applies to planned only "
                              "(Kinaxis carries factory output in PROD_CONFIRMED for many SKUs)"]
                    + ([f"SKUs also on other lines (CLARIFY_LINE): {multi}"] if multi else []))

"""S3: segment growth vs line capacity. Demand in units = TOTAL_DEMAND (cases) x CASETOEACH;
weeks bucketed into the month the week ENDS in (reproduces appendix E.4)."""
from datetime import timedelta

import pandas as pd

from . import adapter, config
from .attainment import get_attainment
from .envelope import cite, envelope, table
from .woc import calendar


def demand_by_month(materials, plan=None, months=6) -> pd.DataFrame:
    plan = adapter.kinaxis_plan() if plan is None else plan
    c2e = adapter.item_master().drop_duplicates("ITEMCODE").set_index("ITEMCODE")["CASETOEACH"]
    cal = calendar(plan)
    cal["month"] = cal.week_start.map(lambda d: (d + timedelta(days=6)).strftime("%Y-%m"))
    x = plan[plan.MATERIAL.isin(materials) & plan.LOCATION.isin(config.LCS) & (plan.CALWEEK != config.OPENING_BUCKET)]
    x = x.merge(cal[["CALWEEK", "month"]], on="CALWEEK")
    x["units"] = x.TOTAL_DEMAND * x.MATERIAL.map(c2e)
    g = x.groupby("month")[["TOTAL_DEMAND", "units"]].sum().reset_index()
    first_full = (cal.week_start.iloc[0] + timedelta(days=6)).strftime("%Y-%m")
    g = g[g.month > first_full].head(months)      # skip the partial snapshot month
    return g.rename(columns={"TOTAL_DEMAND": "demand_cases", "units": "demand_units"})


def select_materials(consumer_size=None, segment_desc=None, materials=None, plan=None):
    if materials:
        return [str(m) for m in materials]
    plan = adapter.kinaxis_plan() if plan is None else plan
    km = adapter.kinaxis_master()
    km = km[km.site.isin(config.LCS)]
    if consumer_size is not None:
        km = km[km.consumer_product_size_id == consumer_size]
    if segment_desc:
        km = km[km.segment_desc == segment_desc]
    return sorted(set(km["product"]) & set(plan.MATERIAL))


def capacity_gap(selector: dict, growth_pct: float, months: int = 6, work_center: str = None,
                 rated_capacity_units: float = None, attainment_basis: str = "trailing_3",
                 attainment_override_pct: float = None, plan=None) -> dict:
    mats = select_materials(selector.get("consumer_size"), selector.get("segment_desc"),
                            selector.get("materials"), plan)
    if rated_capacity_units is None:
        return envelope("capacity_gap", "needs_input", values={"work_center": work_center, "materials": mats},
                        missing=[{"key": "rated_capacity", "entity": "work_center", "id": work_center,
                                  "template": "NO_DATA_CAPACITY"}])
    if attainment_basis == "override":
        att_pct = attainment_override_pct
    else:
        n = 6 if attainment_basis == "trailing_6" else 3
        att_pct = get_attainment(work_center, months=n)["values"]["trailing_avg_pct"]
    eff = rated_capacity_units * att_pct / 100
    d = demand_by_month(mats, plan, months)
    d["grown_units"] = d.demand_units * (1 + growth_pct / 100)
    d["rated_capacity_units"] = rated_capacity_units
    d["attainment_pct"] = att_pct
    d["effective_capacity_units"] = eff
    d["gap_units"] = (d.grown_units - eff).clip(lower=0)
    d = d.round(0)
    constrained = d[d.gap_units > 0].month.tolist()
    return envelope("capacity_gap",
                    values={"materials": mats, "selector": selector, "growth_pct": growth_pct, "work_center": work_center,
                            "first_constrained_month": constrained[0] if constrained else None,
                            "effective_capacity_units": round(eff), "unit_basis": "each (CASETOEACH)"},
                    table=table(d), rows_used=_cite_plan(mats, plan),
                    warnings=["Months bucketed by the month each Kinaxis week ends in"])


def _cite_plan(mats, plan=None):
    plan = adapter.kinaxis_plan() if plan is None else plan
    x = plan[plan.MATERIAL.isin(mats) & plan.LOCATION.isin(config.LCS) & (plan.CALWEEK != config.OPENING_BUCKET)]
    return cite(x[x.TOTAL_DEMAND != 0])


def segment_demand(selector: dict, growth_pct: float = 0, months: int = 6, plan=None) -> dict:
    """S3 demand side on its own: monthly demand in cases and units, and units after growth.
    Used to show the planner the demand while line capacity is still missing."""
    mats = select_materials(selector.get("consumer_size"), selector.get("segment_desc"),
                            selector.get("materials"), plan)
    d = demand_by_month(mats, plan, months)
    if d.empty:
        return envelope("segment_demand", "no_data", values={"materials": mats})
    d["grown_units"] = d.demand_units * (1 + growth_pct / 100)
    d = d.round(0)
    return envelope("segment_demand",
                    values={"materials": mats, "selector": selector, "growth_pct": growth_pct, "months": d.month.tolist(),
                            "unit_basis": "each (CASETOEACH)"},
                    table=table(d), rows_used=_cite_plan(mats, plan),
                    warnings=["Months bucketed by the month each Kinaxis week ends in"])

"""S5: what changed. Kinaxis mode diffs WoC between two plan snapshots; inventory fallback
diffs SAP ONHAND between two dates. Also the demand-component reconciliation check."""
import pandas as pd

from . import adapter, config
from .envelope import envelope, table
from .woc import add_woc, colour, series


def _nearest(inv, date, tolerance_days=3):
    d = pd.Timestamp(date)
    avail = sorted(x for x in inv.snapshot_date.unique() if d - pd.Timedelta(days=tolerance_days) <= x <= d)
    return avail[-1] if avail else None


def inventory_diff(date_a, date_b, materials=None, inv=None) -> pd.DataFrame:
    inv = adapter.inventory() if inv is None else inv
    inv = inv[inv.PLANT.isin(config.LCS)]
    if materials:
        inv = inv[inv.MATERIAL.isin([str(m) for m in materials])]
    da, db = _nearest(inv, date_a), _nearest(inv, date_b)
    if da is None or db is None:
        return pd.DataFrame(), da, db
    a = inv[inv.snapshot_date == da].groupby(["MATERIAL", "PLANT"]).ONHAND.sum().rename("onhand_a")
    b = inv[inv.snapshot_date == db].groupby(["MATERIAL", "PLANT"]).ONHAND.sum().rename("onhand_b")
    df = pd.concat([a, b], axis=1).fillna(0).reset_index().rename(columns={"MATERIAL": "material", "PLANT": "location"})
    df["delta_cases"] = df.onhand_b - df.onhand_a
    return df, da, db


def kinaxis_diff(plan_a, plan_b, materials=None, min_move_weeks=1.0, week_index=0) -> pd.DataFrame:
    mats = materials or sorted(set(plan_a.MATERIAL) & set(plan_b.MATERIAL))
    rows = []
    for m in mats:
        for loc in config.LCS + ["NETWORK"]:
            sa, sb = series(plan_a, m, loc), series(plan_b, m, loc)
            if sa.empty or sb.empty:
                continue
            wa, wb = add_woc(sa).iloc[week_index], add_woc(sb).iloc[week_index]
            if wa.woc is None or wb.woc is None:
                continue
            delta = wb.woc - wa.woc
            if wa.colour != wb.colour or abs(delta) >= min_move_weeks:
                rows.append({"material": m, "location": loc, "woc_a": round(wa.woc, 1), "woc_b": round(wb.woc, 1),
                             "delta_weeks": round(delta, 1), "colour_a": wa.colour, "colour_b": wb.colour,
                             "demand_change": round(wb.avg_fwd_demand - wa.avg_fwd_demand),
                             "stock_change": int(wb.STOCK_PLANNED - wa.STOCK_PLANNED)})
    return pd.DataFrame(rows)


def reconciliation(plan=None, weeks=8, tolerance=0.25) -> pd.DataFrame:
    """Flag items whose FORECAST and TOTAL_DEMAND disagree by more than `tolerance` over the next
    `weeks` planning weeks at the 4 LCs (opening/past-due bucket excluded)."""
    plan = adapter.kinaxis_plan() if plan is None else plan
    wk = sorted(w for w in plan.CALWEEK.unique() if w != config.OPENING_BUCKET)[:weeks]
    x = plan[plan.LOCATION.isin(config.LCS) & plan.CALWEEK.isin(wk)]
    g = x.groupby("MATERIAL")[["FORECAST", "TOTAL_DEMAND"]].sum()
    g = g[(g.FORECAST > 0) & ((g.FORECAST - g.TOTAL_DEMAND).abs() / g.FORECAST > tolerance)]
    return g.reset_index().rename(columns={"MATERIAL": "material", "FORECAST": "forecast", "TOTAL_DEMAND": "total_demand"})


def diff_snapshots(snapshot_a, snapshot_b, materials=None, min_move_weeks=1.0, plan_a=None, plan_b=None) -> dict:
    """snapshot_a/b: dates. Kinaxis mode when two plan frames are given, else inventory fallback."""
    if plan_a is not None and plan_b is not None:
        df = kinaxis_diff(plan_a, plan_b, materials, min_move_weeks)
        if not df.empty:
            df = df.sort_values("woc_b").head(10)            # digest cap: top 10
        msg = [] if not df.empty else ["No WoC colour changes or moves over 1 week between the two snapshots."]
        return envelope("diff_snapshots", values={"mode": "kinaxis", "changes": len(df)},
                        table=table(df), warnings=msg)
    df, da, db = inventory_diff(snapshot_a, snapshot_b, materials)
    if df.empty:
        return envelope("diff_snapshots", "no_data", values={"mode": "inventory_fallback"},
                        missing=[{"key": "inventory_snapshot", "template": "NO_SECOND_SNAPSHOT"}])
    rec = reconciliation()
    if materials:
        rec = rec[rec.material.isin([str(m) for m in materials])]
    return envelope("diff_snapshots",
                    values={"mode": "inventory_fallback", "date_a": str(da.date()), "date_b": str(db.date()),
                            "drivers": {"demand": "unavailable", "attainment": "unavailable"},
                            "reconciliation": rec.to_dict("records")},
                    table=table(df), warnings=["Inventory fallback: demand and attainment drivers unavailable"])

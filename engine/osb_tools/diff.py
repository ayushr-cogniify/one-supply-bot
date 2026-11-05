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


def kinaxis_diff(plan_a, plan_b, materials=None, min_move_weeks=1.0, calweek=None) -> pd.DataFrame:
    """WoC for the same plan week in two snapshots. calweek defaults to the first planning week of
    the newer snapshot. Each move gets its driver: the larger of the stock effect (projected stock
    changed, demand held) and the demand effect (forward demand changed, stock held). Rows come
    back ranked by risk: lowest WoC in the newer snapshot first, red before yellow."""
    if calweek is None:
        weeks = sorted(w for w in plan_b.CALWEEK.unique() if w != config.OPENING_BUCKET)
        calweek = int(weeks[0])
    mats = materials or sorted(set(plan_a.MATERIAL) & set(plan_b.MATERIAL))
    rows = []
    for m in mats:
        for loc in config.LCS + ["NETWORK"]:
            sa, sb = series(plan_a, m, loc), series(plan_b, m, loc)
            if sa.empty or sb.empty:
                continue
            wa, wb = add_woc(sa), add_woc(sb)
            ra, rb = wa[wa.CALWEEK == calweek], wb[wb.CALWEEK == calweek]
            if ra.empty or rb.empty:
                continue
            ra, rb = ra.iloc[0], rb.iloc[0]
            if ra.woc is None or rb.woc is None or not ra.avg_fwd_demand > 0:
                continue
            delta = rb.woc - ra.woc
            if colour(ra.woc) != colour(rb.woc) or abs(delta) >= min_move_weeks:
                stock_effect = (rb.STOCK_PLANNED - ra.STOCK_PLANNED) / ra.avg_fwd_demand
                demand_effect = delta - stock_effect
                driver = (("projected stock up" if stock_effect > 0 else "projected stock down")
                          if abs(stock_effect) >= abs(demand_effect) else
                          ("forward demand down" if demand_effect > 0 else "forward demand up"))
                rows.append({"material": m, "location": loc, "calweek": calweek,
                             "woc_a": round(ra.woc, 1), "woc_b": round(rb.woc, 1), "delta_weeks": round(delta, 1),
                             "colour_a": colour(ra.woc), "colour_b": colour(rb.woc),
                             "stock_a": int(ra.STOCK_PLANNED), "stock_b": int(rb.STOCK_PLANNED),
                             "demand_a": round(ra.avg_fwd_demand), "demand_b": round(rb.avg_fwd_demand),
                             "driver": driver})
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    sev = {"red": 0, "yellow": 1, "green": 2, "blue": 3, "n/a": 4}
    df["_sev"] = df.colour_b.map(sev)
    df = df.sort_values(["_sev", "woc_b", "delta_weeks"]).drop(columns="_sev").reset_index(drop=True)
    df.insert(0, "rank", range(1, len(df) + 1))
    return df


def reconciliation(plan=None, weeks=8, tolerance=0.25) -> pd.DataFrame:
    """Flag items whose FORECAST and TOTAL_DEMAND disagree by more than `tolerance` over the next
    `weeks` planning weeks at the 4 LCs (opening/past-due bucket excluded)."""
    plan = adapter.kinaxis_plan() if plan is None else plan
    wk = sorted(w for w in plan.CALWEEK.unique() if w != config.OPENING_BUCKET)[:weeks]
    x = plan[plan.LOCATION.isin(config.LCS) & plan.CALWEEK.isin(wk)]
    g = x.groupby("MATERIAL")[["FORECAST", "TOTAL_DEMAND"]].sum()
    g = g[(g.FORECAST > 0) & ((g.FORECAST - g.TOTAL_DEMAND).abs() / g.FORECAST > tolerance)]
    return g.reset_index().rename(columns={"MATERIAL": "material", "FORECAST": "forecast", "TOTAL_DEMAND": "total_demand"})


def _snapshot(snaps, date):
    d = pd.Timestamp(date)
    x = snaps[snaps.snapshot_date == d]
    return None if x.empty else x


def diff_snapshots(snapshot_a, snapshot_b, materials=None, min_move_weeks=1.0, plan_a=None, plan_b=None,
                   mode="auto", top_n=10) -> dict:
    """S5. snapshot_a/b are dates. Kinaxis mode diffs two weekly plan snapshots (plan_a/plan_b, or
    the loaded weekly snapshots); the inventory fallback diffs SAP on-hand between two dates."""
    rows_used = []
    if plan_a is None and mode in ("auto", "kinaxis"):
        try:
            snaps = adapter.kinaxis_snapshots()
            plan_a, plan_b = _snapshot(snaps, snapshot_a), _snapshot(snaps, snapshot_b)
        except FileNotFoundError:
            plan_a = plan_b = None
    if plan_a is not None and plan_b is not None:
        if materials:
            materials = [str(m) for m in materials]
        df = kinaxis_diff(plan_a, plan_b, materials, min_move_weeks)
        total = len(df)
        if not df.empty:
            df = df.head(top_n)                                 # digest cap, already in risk order
            for f in (plan_a, plan_b):
                if "_row" in f.columns and "_file" in f.columns:
                    hit = f[f.MATERIAL.isin(df.material) & f.CALWEEK.isin(df.calweek)]
                    rows_used += [{"source_file": r["_file"], "source_sheet": r.get("_sheet"),
                                   "source_row": int(r["_row"]), "hash": None} for r in hit.head(100).to_dict("records")]
        msg = [] if not df.empty else ["No WoC colour changes or moves over 1 week between the two snapshots."]
        rec = reconciliation(plan_b) if "FORECAST" in plan_b.columns else pd.DataFrame(columns=["material"])
        if materials:
            rec = rec[rec.material.isin(materials)]
        return envelope("diff_snapshots", values={
            "mode": "kinaxis", "changes": len(df), "changes_total": total,
            "date_a": str(pd.Timestamp(snapshot_a).date()) if snapshot_a not in (None, "x") else None,
            "date_b": str(pd.Timestamp(snapshot_b).date()) if snapshot_b not in (None, "y") else None,
            "calweek": int(df.calweek.iloc[0]) if not df.empty else None,
            "reconciliation": rec.to_dict("records")},
            table=table(df), rows_used=rows_used, warnings=msg)
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

"""PIR generator: rebuilds Rushi's projected inventory report (LC -> network -> group)
from the Kinaxis plan, for the items, LCs, start week and horizon the user picks."""
import pandas as pd

from . import adapter, config
from .woc import add_woc, apply_demand_change, calendar, series


def generate_pir(materials=None, bsg_group=None, locations=None, start_week=None,
                 horizon_weeks=12, demand_changes=None, plan=None) -> pd.DataFrame:
    """Long table: level, group, material, location, calweek, week_start,
    total_demand, available_supply, balance, avg_fwd_demand, woc, colour.

    demand_changes: optional list of {"material", "location", "week", "delta_cases"} (scenario rows).
    """
    plan = adapter.kinaxis_plan() if plan is None else plan
    locations = locations or config.LCS
    if materials is None:
        if bsg_group is None:
            raise ValueError("Give materials or bsg_group")
        materials = sorted(plan[plan["BSG_GROUP"] == bsg_group].MATERIAL.unique())
    materials = [str(m) for m in materials]
    cal = calendar(plan)
    start_week = start_week or int(cal.CALWEEK.iloc[0])
    groups = plan.drop_duplicates("MATERIAL").set_index("MATERIAL")["BSG_GROUP"].to_dict()
    changes = demand_changes or []

    frames = []
    for m in materials:
        lc_frames = []
        for loc in locations:
            s = series(plan, m, loc)
            if s.empty:
                continue
            for c in changes:
                if str(c["material"]) == m and c["location"] == loc:
                    s = apply_demand_change(s, int(c["week"]), c["delta_cases"])
            lc_frames.append(s.assign(location=loc))
        if not lc_frames:
            continue
        net = (pd.concat(lc_frames).groupby("CALWEEK", as_index=False)
               [["STOCK_PLANNED", "TOTAL_DEMAND", "TOTAL_SUPPLY"]].sum().assign(location="NETWORK"))
        for f, level in [(x, "LC") for x in lc_frames] + [(net, "NETWORK")]:
            frames.append(add_woc(f).assign(level=level, material=m, group=groups.get(m)))

    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)

    # Group roll-up (PIR 'Segment' sheet): sum cases across items, WoC recomputed on the sums
    grp = (out[out.level == "NETWORK"].groupby(["group", "CALWEEK"], as_index=False)
           [["STOCK_PLANNED", "TOTAL_DEMAND", "TOTAL_SUPPLY"]].sum())
    grp_frames = [add_woc(g.sort_values("CALWEEK").reset_index(drop=True)).assign(level="GROUP", material="ALL", location="NETWORK")
                  for _, g in grp.groupby("group")]
    out = pd.concat([out] + grp_frames, ignore_index=True)

    out = out.merge(cal, on="CALWEEK", how="left")
    out = out[(out.CALWEEK >= start_week)]
    out = out.groupby(["level", "group", "material", "location"], group_keys=False).head(horizon_weeks)
    out = out.rename(columns={"CALWEEK": "calweek", "TOTAL_DEMAND": "total_demand",
                              "TOTAL_SUPPLY": "available_supply", "STOCK_PLANNED": "balance"})
    out["woc"] = out["woc"].map(lambda v: None if v is None or pd.isna(v) else round(v, 1))
    cols = ["level", "group", "material", "location", "calweek", "week_start",
            "total_demand", "available_supply", "balance", "avg_fwd_demand", "woc", "colour"]
    return out[cols].reset_index(drop=True)


def to_wide(pir: pd.DataFrame, measures=("total_demand", "available_supply", "balance", "woc")) -> pd.DataFrame:
    """PIR layout: one row per (level, material, location, measure), one column per week."""
    long = pir.melt(id_vars=["level", "group", "material", "location", "week_start"],
                    value_vars=list(measures), var_name="measure")
    wide = long.pivot_table(index=["level", "group", "material", "location", "measure"],
                            columns="week_start", values="value", aggfunc="first")
    order = {m: i for i, m in enumerate(measures)}
    wide = wide.reset_index()
    wide["_o"] = wide["measure"].map(order)
    return wide.sort_values(["level", "group", "material", "location", "_o"]).drop(columns="_o")


def to_excel(pir: pd.DataFrame, path) -> None:
    with pd.ExcelWriter(path) as xw:
        for level, sheet in [("LC", "LC"), ("NETWORK", "Agg"), ("GROUP", "Segment")]:
            part = pir[pir.level == level]
            if not part.empty:
                to_wide(part).to_excel(xw, sheet_name=sheet, index=False)
        pir.to_excel(xw, sheet_name="Long", index=False)

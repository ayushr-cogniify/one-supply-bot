"""Weeks-of-cover engine (S1) on the Kinaxis plan.

WoC(w) = STOCK_PLANNED(w) / mean(TOTAL_DEMAND, weeks w+1 .. w+6)
This is the convention in Rushi's PIR ('LC' sheet: =G4/AVERAGE(H2:M2)) and it reproduces
the Engineering appendix E.2 golden values exactly. STOCK_PLANNED is taken as the baseline
balance (Kinaxis truth); scenario changes are applied as deltas on top of it.
"""
import itertools
from datetime import date, datetime, timedelta

import pandas as pd

from . import adapter, config

_ids = itertools.count(1)


def colour(woc):
    """Colour band on the 1-decimal value the planner sees (5.0 is green, 5.1 is blue)."""
    if woc is None or pd.isna(woc):
        return "n/a"
    r = round(float(woc), 1)
    if r < 2:
        return "red"
    if r < 4:
        return "yellow"
    if r <= 5:
        return "green"
    return "blue"


def calendar(plan: pd.DataFrame) -> pd.DataFrame:
    """Kinaxis CALWEEK is NOT ISO. Anchor the first planning week on SNAPSHOT_WEEK, +7 days per week."""
    snap = pd.to_datetime(str(plan["SNAPSHOT_WEEK"].iloc[0])).date()
    weeks = sorted(w for w in plan["CALWEEK"].unique() if w != config.OPENING_BUCKET)
    return pd.DataFrame({"CALWEEK": weeks,
                         "week_index": range(1, len(weeks) + 1),
                         "week_start": [snap + timedelta(days=7 * i) for i in range(len(weeks))]})


def series(plan: pd.DataFrame, material: str, location: str) -> pd.DataFrame:
    """Weekly plan rows for one material at one LC, or NETWORK = sum of the four LCs."""
    locs = config.LCS if location == "NETWORK" else [location]
    df = plan[(plan.MATERIAL == str(material)) & plan.LOCATION.isin(locs) & (plan.CALWEEK != config.OPENING_BUCKET)]
    if df.empty:
        return df
    g = df.groupby("CALWEEK").agg(STOCK_PLANNED=("STOCK_PLANNED", "sum"),
                                  TOTAL_DEMAND=("TOTAL_DEMAND", "sum"),
                                  TOTAL_SUPPLY=("TOTAL_SUPPLY", "sum"),
                                  rows=("_row", list)).reset_index()
    return g.sort_values("CALWEEK").reset_index(drop=True)


def add_woc(s: pd.DataFrame, window: int = config.WOC_WINDOW_WEEKS) -> pd.DataFrame:
    s = s.copy()
    fwd = s["TOTAL_DEMAND"].shift(-1).rolling(window).mean().shift(-(window - 1))
    s["avg_fwd_demand"] = fwd
    s["woc"] = [None if (pd.isna(d) or d == 0) else st / d for st, d in zip(s["STOCK_PLANNED"], fwd)]
    s["colour"] = s["woc"].map(colour)
    return s


def apply_demand_change(s: pd.DataFrame, week: int, delta_cases: float) -> pd.DataFrame:
    """Extra demand in `week` raises that week's demand and lowers projected stock from that week on."""
    s = s.copy()
    if week not in set(s["CALWEEK"]):
        raise ValueError(f"week {week} not in plan horizon")
    s.loc[s.CALWEEK == week, "TOTAL_DEMAND"] += delta_cases
    s.loc[s.CALWEEK >= week, "STOCK_PLANNED"] -= delta_cases
    return s


def _envelope(tool, status="ok", values=None, table=None, rows_used=None, warnings=None, missing=None):
    return {"result_id": f"r:{tool}-{next(_ids):04d}", "tool": tool, "status": status,
            "values": values or {}, "table": table, "rows_used": rows_used or [],
            "missing": missing or [], "warnings": warnings or []}


def simulate_demand_change(material: str, scope: str, week: int, delta_cases: int = None,
                           weeks: int = 4, plan: pd.DataFrame = None, delta_pct: float = None) -> dict:
    """S1: WoC before/after a demand change at one LC, with every LC and the network shown.
    The change is either signed cases, or delta_pct of the item's total demand at that LC in that
    week; the tool does the conversion (the model never does) and returns the cases it used."""
    plan = adapter.kinaxis_plan() if plan is None else plan
    if scope not in config.LCS:
        return _envelope("simulate_demand_change", "needs_input",
                         missing=[{"key": "scope", "template": "CLARIFY_SCOPE"}])
    base_demand = None
    if delta_cases is None:
        if delta_pct is None:
            return _envelope("simulate_demand_change", "needs_input", missing=[{"key": "delta_cases"}])
        s0 = series(plan, material, scope)
        hit = s0[s0.CALWEEK == week]
        if hit.empty:
            return _envelope("simulate_demand_change", "no_data",
                             missing=[{"key": "material", "id": material, "template": "NO_DATA_ITEM"}])
        base_demand = float(hit.TOTAL_DEMAND.iloc[0])
        delta_cases = int(round(base_demand * delta_pct / 100))
    out, rows_used = [], []
    lcs = {}
    for loc in config.LCS:
        base = series(plan, material, loc)
        if base.empty:
            continue
        after = apply_demand_change(base, week, delta_cases) if loc == scope else base
        lcs[loc] = (base, after)
    if not lcs:
        return _envelope("simulate_demand_change", "no_data",
                         missing=[{"key": "material", "id": material, "template": "NO_DATA_ITEM"}])
    # Network = sum of LC series (before and after)
    def _sum(idx):
        frames = [v[idx][["CALWEEK", "STOCK_PLANNED", "TOTAL_DEMAND"]] for v in lcs.values()]
        return pd.concat(frames).groupby("CALWEEK", as_index=False).sum()
    lcs["NETWORK"] = (_sum(0), _sum(1))

    for loc, (b, a) in lcs.items():
        b, a = add_woc(b), add_woc(a)
        sel = (b.CALWEEK >= week)
        for (_, rb), (_, ra) in itertools.islice(zip(b[sel].iterrows(), a[sel].iterrows()), weeks):
            out.append({"location": loc, "calweek": int(rb.CALWEEK),
                        "woc_before": None if rb.woc is None else round(rb.woc, 1),
                        "woc_after": None if ra.woc is None else round(ra.woc, 1),
                        "colour_before": rb.colour, "colour_after": ra.colour,
                        "stock_before": int(rb.STOCK_PLANNED), "stock_after": int(ra.STOCK_PLANNED)})
            if loc != "NETWORK" and "rows" in rb:
                rows_used += [{"source_file": plan["_file"].iloc[0], "source_sheet": plan["_sheet"].iloc[0],
                               "source_row": int(r), "hash": plan["_hash"].iloc[0]} for r in rb["rows"]]
    df = pd.DataFrame(out)
    net_after = df[df.location == "NETWORK"].colour_after.tolist()
    flagged = sorted(df[(df.location != "NETWORK") & (df.colour_after == "red") & (df.colour_before != "red")].location.unique())
    verdict = ("do_not_support" if "red" in net_after
               else "support_with_replan" if flagged or "yellow" in net_after else "support")
    return _envelope("simulate_demand_change",
                     values={"material": str(material), "scope": scope, "week": week,
                             "delta_cases": delta_cases, "delta_pct": delta_pct,
                             "base_demand_cases": None if base_demand is None else int(round(base_demand)),
                             "verdict": verdict, "flagged_locations": flagged},
                     table={"columns": list(df.columns), "rows": df.values.tolist()},
                     rows_used=rows_used)

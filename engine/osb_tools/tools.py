"""Tool registry for the orchestrator: name -> (function, JSON schema). The agent may only call
these. Schemas follow Engineering appendix B.2 (trimmed to what is implemented)."""
from .attainment import get_attainment, simulate_attainment
from .capacity import capacity_gap, segment_demand
from .cuts import cuts_pareto, get_cut_line, list_cut_lines
from .decision_tree import walk_decision_tree
from .diff import diff_snapshots
from .items import resolve_item
from .lanes import lookup_lane
from .pir import generate_pir
from .woc import simulate_demand_change, add_woc, series
from . import adapter, config
from .envelope import envelope, table

LC_ENUM = config.LCS
MAT = {"type": "string", "pattern": "^[0-9]{7}$"}


def get_plan(material: str, locations: list, weeks: int = 8) -> dict:
    plan = adapter.kinaxis_plan()
    out = []
    for loc in locations:
        s = series(plan, material, loc)
        if s.empty:
            continue
        s = add_woc(s).head(weeks)
        for r in s.itertuples():
            out.append({"location": loc, "calweek": int(r.CALWEEK), "stock_planned": int(r.STOCK_PLANNED),
                        "total_demand": float(r.TOTAL_DEMAND), "total_supply": float(r.TOTAL_SUPPLY),
                        "woc": None if r.woc is None else round(r.woc, 1), "colour": r.colour})
    if not out:
        return envelope("get_plan", "no_data", missing=[{"key": "material", "id": material, "template": "NO_DATA_ITEM"}])
    import pandas as pd
    return envelope("get_plan", values={"material": material, "snapshot_week": str(plan.SNAPSHOT_WEEK.iloc[0])},
                    table=table(pd.DataFrame(out)))


def pir_report(materials: list = None, bsg_group: str = None, locations: list = None,
               start_week: int = None, horizon_weeks: int = 12) -> dict:
    pir = generate_pir(materials=materials, bsg_group=bsg_group, locations=locations,
                       start_week=start_week, horizon_weeks=horizon_weeks)
    if pir.empty:
        return envelope("pir_report", "no_data")
    pir["week_start"] = pir.week_start.astype(str)
    return envelope("pir_report", values={"materials": materials, "bsg_group": bsg_group,
                                          "horizon_weeks": horizon_weeks}, table=table(pir))


TOOLS = {
    "resolve_item": (resolve_item, {"type": "object", "required": ["text"], "properties": {
        "text": {"type": "string"}, "scope": {"type": "string", "enum": ["Lysol LDS", "Lysol Sanitizers", "both"]}}}),
    "get_plan": (get_plan, {"type": "object", "required": ["material", "locations"], "properties": {
        "material": MAT, "locations": {"type": "array", "items": {"type": "string", "enum": LC_ENUM + ["NETWORK"]}},
        "weeks": {"type": "integer", "maximum": 26}}}),
    "simulate_demand_change": (simulate_demand_change, {"type": "object",
        "required": ["material", "scope", "week"], "properties": {
        "material": MAT, "scope": {"type": "string", "enum": LC_ENUM + ["NETWORK"]},
        "week": {"type": "integer"}, "delta_cases": {"type": "integer"},
        "delta_pct": {"type": "number", "description": "Change as % of the item's total demand at the LC that week"}}}),
    "get_attainment": (get_attainment, {"type": "object", "required": ["work_center"], "properties": {
        "work_center": {"type": "string"}, "months": {"type": "integer", "maximum": 22}}}),
    "simulate_attainment": (simulate_attainment, {"type": "object", "required": ["work_center", "attainment_pct"],
        "properties": {"work_center": {"type": "string"}, "attainment_pct": {"type": "number", "minimum": 0, "maximum": 150},
                       "horizon_weeks": {"type": "integer"}, "extra_demand": {"type": "object"}}}),
    "capacity_gap": (capacity_gap, {"type": "object", "required": ["selector", "growth_pct"], "properties": {
        "selector": {"type": "object"}, "growth_pct": {"type": "number"}, "months": {"type": "integer"},
        "work_center": {"type": "string"}, "rated_capacity_units": {"type": "number"},
        "attainment_basis": {"type": "string", "enum": ["trailing_3", "trailing_6", "override"]},
        "attainment_override_pct": {"type": "number"}}}),
    "segment_demand": (segment_demand, {"type": "object", "required": ["selector"], "properties": {
        "selector": {"type": "object"}, "growth_pct": {"type": "number"}, "months": {"type": "integer"}}}),
    "cuts_pareto": (cuts_pareto, {"type": "object", "required": ["period_from", "period_to"], "properties": {
        "period_from": {"type": "string", "format": "date"}, "period_to": {"type": "string", "format": "date"},
        "scope": {"type": "string", "enum": ["Lysol LDS", "Lysol Sanitizers", "both"]}, "top_n": {"type": "integer"}}}),
    "list_cut_lines": (list_cut_lines, {"type": "object", "required": ["lc", "cut_date"], "properties": {
        "lc": {"type": "string", "enum": LC_ENUM}, "cut_date": {"type": "string", "format": "date"},
        "top_n": {"type": "integer"}}}),
    "get_cut_line": (get_cut_line, {"type": "object", "required": ["material", "lc", "cut_date"], "properties": {
        "material": MAT, "lc": {"type": "string", "enum": LC_ENUM}, "cut_date": {"type": "string", "format": "date"}}}),
    "walk_decision_tree": (walk_decision_tree, {"type": "object", "required": ["cut"], "properties": {
        "cut": {"type": "object"}, "answers": {"type": "object"}}}),
    "diff_snapshots": (diff_snapshots, {"type": "object", "required": ["snapshot_a", "snapshot_b"], "properties": {
        "snapshot_a": {"type": "string"}, "snapshot_b": {"type": "string"},
        "materials": {"type": "array", "items": MAT}, "min_move_weeks": {"type": "number"}}}),
    "lookup_lane": (lookup_lane, {"type": "object", "required": ["source", "destination"], "properties": {
        "source": {"type": "string"}, "destination": {"type": "string"}, "material": MAT}}),
    "pir_report": (pir_report, {"type": "object", "properties": {
        "materials": {"type": "array", "items": MAT}, "bsg_group": {"type": "string"},
        "locations": {"type": "array", "items": {"type": "string", "enum": LC_ENUM}},
        "start_week": {"type": "integer"}, "horizon_weeks": {"type": "integer", "maximum": 82}}}),
}


DESCRIPTIONS = {
    "resolve_item": "Resolve the planner's words (code, UPC, GTIN or description) to a 7-digit material. Always call before any item-level tool.",
    "get_plan": "Weekly projected stock, demand, supply and weeks of cover for one material at LCs or NETWORK.",
    "simulate_demand_change": "S1: weeks of cover before/after extra demand (signed cases) at one LC in one Kinaxis week.",
    "get_attainment": "S2: monthly and trailing-3-month factory attainment for a work centre, plus SKUs on the line.",
    "simulate_attainment": "S2: apply an attainment % to a work centre's production and recompute network WoC per SKU.",
    "capacity_gap": "S3: monthly demand in units vs effective capacity (rated x attainment) after growth %.",
    "segment_demand": "S3: monthly demand in cases and units for a segment, before and after growth %.",
    "cuts_pareto": "S4: top cutting items, cases cut/ordered and PFR % for a date range from the PFR extract.",
    "list_cut_lines": "S4: cut lines at one LC on one day from the PFR extract, largest first.",
    "get_cut_line": "S4: one item's cut at one LC on one day from the PFR extract (cases cut, ordered, recorded RC).",
    "walk_decision_tree": "S4: walk the reason-code decision tree for one cut line and propose a SAP reason code.",
    "diff_snapshots": "S5: what changed between two snapshots (Kinaxis WoC diff, or SAP on-hand fallback).",
    "lookup_lane": "Lead time, MOQ, lane type and cheapest shuttle cost between two sites.",
    "pir_report": "Projected inventory report (demand, supply, balance, WoC, colour) by LC, network and group.",
}


def tool_definitions():
    """Anthropic Messages API 'tools' list."""
    return [{"name": n, "description": DESCRIPTIONS.get(n, n), "input_schema": s} for n, (f, s) in TOOLS.items()]


def dispatch(name: str, args: dict) -> dict:
    if name not in TOOLS:
        return envelope(name, "error", warnings=[f"Unknown tool {name}"])
    fn, schema = TOOLS[name]
    missing = [k for k in schema.get("required", []) if k not in args]
    if missing:
        return envelope(name, "needs_input", missing=[{"key": k} for k in missing])
    try:
        return fn(**args)
    except Exception as e:            # tools never crash the agent
        return envelope(name, "error", warnings=[f"{type(e).__name__}: {e}"])

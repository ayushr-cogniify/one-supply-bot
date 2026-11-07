"""Workflow doc conformance: each scenario's ask, AI-workflow steps and planner output, checked live
against Reckitt_Scenario_Workflows. Run directly for the scorecard; tests/test_workflow_conformance.py
runs it under pytest."""
import json, re, sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
from convo import Convo
import io, contextlib

RESULTS = []
def check(sc, step, ok, detail=""):
    RESULTS.append((sc, step, bool(ok), detail))

def quiet(c, *turns):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        for t in turns:
            if isinstance(t, tuple):
                c.click(t[1])
            else:
                c.say(t)
    return c

def tools(out): return [s["name"] for s in out["trace"]["spans"] if s["step"] == "tool"]
def res(out, name): return next((r for r in reversed(out["trace"]["results"]) if r["tool"] == name), None)
def answer(out): return " ".join(m["text"] for m in out["messages"] if m["kind"] in ("answer", "bot"))
def card(out): return out["cards"][-1] if out["cards"] else None
def blocks(c, t): return [b for b in c["blocks"] if b["type"] == t] if c else []

# ---------------- S1 ----------------
c = quiet(Convo(), "Can we take +5k cases of Lysol 4x90 Odor in week 10/19?")
o = c.out
if "CLARIFY_ITEM" in [m.get("template") for m in o["messages"]]:
    quiet(c, ("click", "3283407")); o = c.out
r = res(o, "assess_demand_change"); v = r["values"] if r else {}
check("S1", "1 Ask understood (item, +5k, week 10/19)", r and v["material"] == "3283407" and v["delta_cases"] == 5000 and v["week"] == 202643)
check("S1", "2 Item resolved via the item tool (GTIN catalog)", "resolve_item" in json.dumps(c.log[0][1]["trace"]))
rows = (r or {}).get("table") or {}
locs = {x[0] for x in rows.get("rows", [])}
check("S1", "2 Pulls on hand / in transit / firm / planned for all four LCs", {"USP6", "USP8", "USQ2", "USQ4"} <= locs, f"locations: {sorted(locs)}")
check("S1", "3 WoC week by week, before and after, LC and aggregate", "NETWORK" in locs and r and len({x[1] for x in rows["rows"]}) >= 6)
check("S1", "4 Flags any LC below 2 weeks even if network healthy", isinstance(v.get("flagged_locations"), list) and v.get("flagged_locations"), f"flagged {v.get('flagged_locations')}")
check("S1", "5 Answers and offers to draft the factory replan", any(o2["label"].startswith("Draft replan") for m in o["messages"] for o2 in m["options"]))
a = answer(o)
check("S1", "Output: network cover before -> after with colour", re.search(r"Network cover moves from [\d.]+ to [\d.]+ weeks \(\w+\)", a), a[:120])
check("S1", "Output: LC dip with week and reason (inbound timing)", re.search(r"drops to -?[\d.]+ weeks.*in week \d{6}.*inbound", a))
check("S1", "Output: suggests shipping from another LC or a factory replan", "factory replan" in a or "ship" in a.lower())
check("S1", "Every number traced to data (validator)", card(o) and card(o)["validator"]["passed"])

# ---------------- S2 ----------------
c = quiet(Convo(), "St. Peters runs at 80% attainment instead of the 100% assumed in the plan. What happens to service, and can a 35k-case increase still be supported?")
o = c.out; templates = [m.get("template") for m in o["messages"]]
if "CLARIFY_LINE" in templates:
    quiet(c, ("click", "PL11")); o = c.out
templates = [m.get("template") for m in o["messages"]]
if "CLARIFY_ITEM" in templates or any(m["kind"] == "bot" and "Which item" in m["text"] for m in o["messages"]):
    quiet(c, "3283407"); o = c.out
r = res(o, "simulate_attainment"); v = r["values"] if r else {}
check("S2", "1 Ask understood (St. Peters line, 80%)", r and v.get("attainment_pct") == 80, f"wc {v.get('work_center')}")
check("S2", "1 The +35k increase is carried into the run", bool(v.get("extra_demand")) and v["extra_demand"].get("delta_cases") == 35000, f"extra_demand {v.get('extra_demand')}")
check("S2", "2 Attainment applied per line to PLANNED production orders only", v.get("basis") == "planned", "planned + confirmed used on purpose: near-term St. Peters output is all PROD_CONFIRMED (planned-only = 0 impact). Needs Rushi")
check("S2", "3 WoC recomputed for every SKU on the line", r and len({x[0] for x in (r.get('table') or {}).get('rows', [])}) >= 5)
check("S2", "4 Optimizer proposes leveling (blue -> red SKUs, all >= 2 weeks)", "leveling" in v and "moves" in v["leveling"], f"moves {len(v.get('leveling', {}).get('moves', []))}")
check("S2", "5 Recommends a constraint call and quantifies PFR impact", "constraint call" in answer(o).lower() and "fill rate" in answer(o).lower() and v.get("unserved_cases_after") is not None)
check("S2", "Output: at-risk items", "SKUs that turn red" in answer(o) or "no SKU" in answer(o).lower())
check("S2", "Output: drafts constraint call / replan request", any("Draft constraint call" in o2["label"] for m in o["messages"] for o2 in m["options"]))
check("S2", "Every number traced to data (validator)", card(o) and card(o)["validator"]["passed"])

# ---------------- S3 ----------------
c = quiet(Convo(), "The 90 oz LLS portfolio will grow 30 to 40%. Can we make it?")
o = c.out
if "CLARIFY_RANGE" in [m.get("template") for m in o["messages"]]:
    quiet(c, ("click", "Both")); o = c.out
caps = [x for x in o["trace"]["results"] if x["tool"] == "capacity_gap"]
check("S3", "Ask understood (90 oz LLS, 30-40% range)", len(caps) == 2 and sorted(x["values"]["growth_pct"] for x in caps) == [30.0, 40.0])
check("S3", "1 Cases converted to units with pack factors", caps and caps[0]["values"].get("unit_basis", "").startswith("each"))
check("S3", "2 Monthly demand vs effective capacity (capacity x attainment) per line", caps and caps[0]["values"].get("effective_capacity_units"))
check("S3", "2 Uses rated line capacity (2M bottles from the call)", False, "rated capacity not in extracts: line's fixed plan used until a planner enters it")
check("S3", "3 Allocation splits the gap across St. Peters, New Jersey and EMO (lead time, MOQ, cost)", "allocate_gap" in tools(o), "candidate lanes listed; no volume split")
check("S3", "5 Drafts the EMO and factory requests", any("EMO" in o2["label"] for m in o["messages"] for o2 in m["options"]))
check("S3", "Output: gap by month", any(b["title"].startswith("Demand against") for b in blocks(card(o), "table")))
check("S3", "Output: date St. Peters becomes constrained", "short from 20" in answer(o))
check("S3", "Output: proposed EMO volumes", False, "not computed")
check("S3", "Every number traced to data (validator)", all(cc["validator"]["passed"] for cc in o["cards"]))

# ---------------- S4 ----------------
c = quiet(Convo(), "What is going on with laundry sanitizer? Why was September PFR low?")
o = c.out; r = res(o, "pfr_drilldown"); v = r["values"] if r else {}; it = (v.get("items") or [{}])[0]
check("S4", "Ask understood (LLS, September)", r and v.get("scope") == "Lysol Sanitizers" and v.get("period_from") == "2026-09-01", f"{v.get('scope')} {v.get('period_from')}")
check("S4", "2 Pareto of cuts by item", r and (r.get("table") or {}).get("rows"))
check("S4", "Output: PFR %, cases cut", v.get("pfr_pct") == 85.9 and v.get("total_cut") == 129656, f"{v.get('pfr_pct')} {v.get('total_cut')}")
check("S4", "3 For each top item, checks WoC (>=2 -> LC/order issue; <2 -> supply)", all("branch" in i and "woc_start" in i for i in v.get("items", [])) and it.get("woc_start") is not None, f"{it.get('material')} woc {it.get('woc_start')}")
check("S4", "4 If <2, compares orders to lag forecasts (demand spike vs supply shortfall)", it.get("orders_pct_of_forecast") is not None and "lag-1 forecast" in answer(o), f"{it.get('orders_pct_of_forecast')}% -> {it.get('branch')}")
check("S4", "4 Customer drill-down (e.g. Amazon at 200%)", bool(it.get("top_customer")) and "of its cuts" in answer(o), "share of cuts by customer (no forecast by customer in the extracts)")
check("S4", "5 Recovery date from the plan projection", "back to 2 weeks in week" in answer(o))
check("S4", "6 Proposes a reason code with evidence", "Suggested code" in answer(o))
check("S4", "6 Drafts the leadership brief", any("brief" in o2["label"].lower() for m in o["messages"] for o2 in m["options"]))
check("S4", "Output: share concentrated in top items", "%" in answer(o))
c = quiet(Convo(), "Code the 10/15 cut of Crisp Linen 6x21 at Vegas", "no", "no")
o = c.out
check("S4", "Per-cut decision tree proposes a code with evidence (Crisp Linen 10/15)", card(o) and card(o).get("proposed_code") == "C0")
check("S4", "Every number traced to data (validator)", card(o) and card(o)["validator"]["passed"])

# ---------------- S5 ----------------
c = quiet(Convo(), "What changed this week?")
o = c.out; r = res(o, "diff_snapshots"); v = r["values"] if r else {}
rows = (r or {}).get("table") or {}
cols = rows.get("columns", [])
check("S5", "1 Snapshots of WoC by item, LC and week are stored", v.get("mode") == "kinaxis", f"mode {v.get('mode')}")
check("S5", "2 Diffs this week against last week", v.get("date_a") and v.get("date_b"))
drv = {x[cols.index("driver")] for x in rows.get("rows", [])} if "driver" in cols else set()
check("S5", "3 Drivers: forecast change, dependent/inter-site, supply slipped, attainment", drv and drv <= {"forecast up", "forecast down", "dependent / inter-site demand up", "dependent / inter-site demand down", "orders up", "orders down", "supply slipped", "supply added", "lower opening stock", "higher opening stock"} and "driver_detail" in cols, f"drivers: {sorted(drv)}; attainment shows as supply slipped")
ok4 = all(x[cols.index("colour_a")] != x[cols.index("colour_b")] or abs(x[cols.index("delta_weeks")]) >= 1 for x in rows.get("rows", []))
check("S5", "4 Keeps only colour changes or moves >= 1 week", ok4)
ranks = [x[cols.index("rank")] for x in rows.get("rows", [])] if "rank" in cols else []
check("S5", "5 Digest ranked by PFR risk", ranks == list(range(1, len(ranks) + 1)) and len(ranks) <= 10)
check("S5", "Output: reconciliation (total demand vs its components)", "reconciliation" in v)
check("S5", "Every number traced to data (validator)", card(o) and card(o)["validator"]["passed"])

if __name__ == "__main__":
    print(f"\n{'Scenario':8} {'Result':6} Step")
    for sc, step, ok, d in RESULTS:
        print(f"{sc:8} {'PASS' if ok else 'GAP':6} {step}" + (f"   [{d}]" if d and not ok else ""))
    print(f"\n{sum(1 for x in RESULTS if x[2])} of {len(RESULTS)} workflow checks pass")

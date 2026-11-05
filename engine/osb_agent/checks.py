"""Golden checks (Engineering appendix E) runnable against whatever data is loaded: in pytest
over the workbooks, or in the browser over the bundle from the admin drawer."""
from osb_tools.attainment import get_attainment, monthly
from osb_tools.capacity import capacity_gap, demand_by_month, select_materials
from osb_tools.cuts import cuts_pareto
from osb_tools.decision_tree import walk_decision_tree
from osb_tools.diff import diff_snapshots, reconciliation
from osb_tools.items import resolve_item
from osb_tools.lanes import lookup_lane
from osb_tools.woc import simulate_demand_change

from . import nlu


def _rows(r):
    return [dict(zip(r["table"]["columns"], x)) for x in r["table"]["rows"]]


def _s1():
    r = simulate_demand_change("3283407", "USQ4", 202643, 5000)
    got = {(x["location"], x["calweek"]): (x["woc_before"], x["woc_after"]) for x in _rows(r)}
    want = {("USQ4", 202643): (2.5, 0.3), ("USQ4", 202644): (3.6, 1.5), ("USQ4", 202645): (2.6, 0.5),
            ("USQ4", 202646): (4.9, 2.9), ("NETWORK", 202643): (2.1, 1.8), ("NETWORK", 202644): (1.9, 1.6),
            ("USP6", 202643): (1.2, 1.2), ("USP8", 202644): (2.7, 2.7), ("USQ2", 202645): (4.2, 4.2)}
    bad = {k: (got.get(k), v) for k, v in want.items() if got.get(k) != v}
    return not bad and r["values"]["verdict"] == "do_not_support", bad or "USQ4 2.5→0.3, network 2.1→1.8"


def _s2():
    want = {"PL11": 72.7, "BA1": 80.0, "BA2": 84.4, "BA3": 65.4, "BA4": 107.7, "PL07": 75.8, "PL01": 38.1}
    got = {w: get_attainment(w)["values"]["trailing_avg_pct"] for w in want}
    g = monthly("PL11").set_index("year_month")
    ok = got == want and g.loc[202606, "fix_cases"] == 467164 and g.loc[202605, "fix_cases"] == 0
    return ok, " \u00b7 ".join(f"{w} {float(v):g}%" for w, v in got.items())


def _s3():
    d = demand_by_month(select_materials(consumer_size=90)).set_index("month").round()
    r = capacity_gap({"consumer_size": 90}, 35, work_center="PL11", rated_capacity_units=2_000_000)
    nov = _rows(r)[0]
    ok = (d.loc["2026-11", "demand_units"] == 1588744 and d.loc["2027-04", "demand_cases"] == 487780
          and nov["effective_capacity_units"] == 1454000 and nov["gap_units"] == 690804)
    return ok, f"Nov units {int(d.loc['2026-11', 'demand_units']):,}; gap {int(nov['gap_units']):,}"


def _s4_tree():
    cut = {"cut_id": "g", "material": "3313455", "lc": "USQ4", "cut_date": "2026-10-15"}
    a = walk_decision_tree(cut)
    b = walk_decision_tree(cut, {"national_pct": 100, "lc_pct": 100})
    c = walk_decision_tree(cut, {"national_pct": 140})
    r05 = [p for p in b["values"]["path"] if p["rule_id"] == "R05"][0]["evidence"]
    ok = (set(a["values"]["needs_input"]) == {"national_pct", "lc_pct"} and b["values"]["proposed_code"] == "C0"
          and r05["ATP_QUANTITY"] == 484 and r05["ATP_AFTER_ALLOCATION"] == 0 and c["values"]["proposed_code"] == "B2")
    return ok, f"asks pacing; C0 with ATP {r05['ATP_QUANTITY']:g}/0; B2 above 130%"


def _s4_pfr():
    v = cuts_pareto("2026-09-01", "2026-09-30", "Lysol Sanitizers")["values"]
    return (v["total_cut"] == 129656 and v["total_ordered"] == 919757 and v["pfr_pct"] == 85.9,
            f"{v['pfr_pct']}% · {v['total_cut']:,} cut of {v['total_ordered']:,}")


def _s5():
    r = diff_snapshots("2026-10-22", "2026-10-29", materials=["3283407"])
    rows = {x["location"]: x["delta_cases"] for x in _rows(r)}
    want = {"USP6": -2063, "USP8": 11362, "USQ2": -8090, "USQ4": 3592}
    ok = rows == want and "3353360" in set(reconciliation().material)
    return ok, " \u00b7 ".join(f"{k} {int(v):+,}" for k, v in rows.items()) + " \u00b7 3353360 flagged"


def _items():
    odor = resolve_item("Odor")
    ok = (odor["status"] == "needs_input" and {"3283407", "3283414", "3313454"} <= {c["material"] for c in odor["values"]["candidates"]}
          and resolve_item("Lysol 4x90 Odor")["values"]["chosen"] == "3283407")
    return ok, "“Odor” asks; “4x90 Odor” → 3283407"


def _lanes():
    a, b = lookup_lane("USR2", "USQ4", "3283407")["values"], lookup_lane("USP6", "USP8")["values"]
    return a["leadtime_days"] == 13 and b["cost_per_load"] == 850.0, f"USR2→USQ4 {a['leadtime_days']} days; USP6→USP8 ${b['cost_per_load']:g}"


OOS_PROMPTS = ["Upload this forecast to Kinaxis.", "Create a shuttle from Harrisburg to Atlanta for 3283407.",
               "Raise an EMO work order for Belle Mead.", "Write reason code C0 to SAP for yesterday's cuts.",
               "What is the WoC for 3049862 at CAC8?", "How should CPG companies think about safety stock?",
               "Email the factory about the replan.", "Change the forecast for 90 oz by 30%.",
               "Who is the best carrier for the Vegas lane?", "Summarise Reckitt's Q3 results."]


def _oos():
    missed = [q for q in OOS_PROMPTS if nlu.parse(q).label != "OUT_OF_SCOPE"]
    return not missed, missed or "10 of 10 refused"


CHECKS = [
    ("S1", "Demand spike: +5,000 cases of 3283407 at USQ4, week 202643 (E.2)", _s1),
    ("S2", "Trailing 3-month attainment on seven lines (E.3)", _s2),
    ("S3", "90 oz demand in units and the PL11 capacity gap (E.4)", _s3),
    ("S4", "Decision tree for the 2026-10-15 USQ4 cut of 3313455 (E.5)", _s4_tree),
    ("S4", "September 2026 Lysol Sanitizers PFR from the PFR extract", _s4_pfr),
    ("S5", "Inventory fallback deltas for 3283407 and the 3353360 flag (E.6)", _s5),
    ("Items", "Item resolution: ambiguous and exact (E.1)", _items),
    ("Lanes", "Lead time and cheapest shuttle mode", _lanes),
    ("Scope", "Out-of-scope prompts refused with no tool call (E.7)", _oos),
]


def run() -> list:
    out = []
    for tag, name, fn in CHECKS:
        try:
            ok, detail = fn()
        except Exception as e:
            ok, detail = False, f"{type(e).__name__}: {e}"
        out.append({"tag": tag, "name": name, "passed": bool(ok), "detail": detail if isinstance(detail, str) else str(detail)})
    return out

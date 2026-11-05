"""Golden values from Engineering appendix E.3-E.6, verified against the delivered files on 4 Nov 2026."""
import pytest

from osb_tools.attainment import get_attainment, lines_for_sku, monthly, simulate_attainment
from osb_tools.capacity import capacity_gap, demand_by_month, select_materials
from osb_tools.decision_tree import ACTIVE, walk_decision_tree
from osb_tools.diff import diff_snapshots, reconciliation
from osb_tools.items import resolve_item
from osb_tools.lanes import lookup_lane


# ---------- S2 attainment (E.3) ----------
def test_pl11_monthly(att):
    g = monthly("PL11", att).set_index("year_month")
    assert g.loc[202606, "fix_cases"] == 467164 and g.loc[202606, "actual_cases"] == 305767
    assert round(g.loc[202610, "attainment_pct"]) == 76
    assert g.loc[202605, "fix_cases"] == 0          # excluded month


@pytest.mark.parametrize("wc,pct", [("PL11", 72.7), ("BA1", 80.0), ("BA2", 84.4), ("BA3", 65.4),
                                    ("BA4", 107.7), ("PL07", 75.8), ("PL01", 38.1)])
def test_trailing_attainment(att, wc, pct):
    assert get_attainment(wc, att=att)["values"]["trailing_avg_pct"] == pct


def test_pl11_skus_ranked(att):
    skus = get_attainment("PL11", att=att)["values"]["skus_on_line"]
    assert [s["material"] for s in skus[:3]] == ["3283407", "3283408", "3283419"]
    assert skus[0]["actual_cases"] == 242492


def test_thin_history_warning(att):
    assert any("Thin" in w for w in get_attainment("PL01", att=att)["warnings"])


def test_multi_line_sku_needs_clarify(att):
    assert lines_for_sku("3162805", att) == ["BA1", "BA2", "BA3"]


def test_simulate_attainment_runs(plan, att):
    r = simulate_attainment("PL11", 80, plan=plan, att=att)
    assert r["status"] == "ok" and r["values"]["production_shortfall_cases"] > 0


# ---------- S3 capacity (E.4) ----------
def test_90oz_selection(plan):
    mats = select_materials(consumer_size=90, plan=plan)               # 29 at LCs in Kinaxis Master
    lc = plan[plan.MATERIAL.isin(mats) & plan.LOCATION.isin(["USP6", "USP8", "USQ2", "USQ4"])]
    with_demand = lc.groupby("MATERIAL").TOTAL_DEMAND.sum()
    assert (with_demand > 0).sum() == 10


def test_90oz_monthly_units(plan):
    d = demand_by_month(select_materials(consumer_size=90, plan=plan), plan).set_index("month").round()
    assert d.loc["2026-11", "demand_cases"] == 602386 and d.loc["2026-11", "demand_units"] == 1588744
    assert d.loc["2026-12", "demand_units"] == 1424821
    assert d.loc["2027-04", "demand_cases"] == 487780


def test_capacity_gap_pl11(plan):
    r = capacity_gap({"consumer_size": 90}, 35, work_center="PL11", rated_capacity_units=2_000_000, plan=plan)
    rows = [dict(zip(r["table"]["columns"], x)) for x in r["table"]["rows"]]
    nov = rows[0]
    assert nov["month"] == "2026-11" and nov["effective_capacity_units"] == 1454000
    assert nov["grown_units"] == 2144804 and nov["gap_units"] == 690804
    assert r["values"]["first_constrained_month"] == "2026-11"


def test_capacity_needs_input_without_capacity(plan):
    r = capacity_gap({"consumer_size": 90}, 35, work_center="PL11", plan=plan)
    assert r["status"] == "needs_input" and r["missing"][0]["template"] == "NO_DATA_CAPACITY"


# ---------- items ----------
def test_odor_is_ambiguous(plan):
    r = resolve_item("Odor", plan=plan)
    mats = {c["material"] for c in r["values"]["candidates"]}
    assert r["status"] == "needs_input" and {"3283407", "3283414", "3313454"} <= mats


def test_4x90_odor_resolves(plan):
    assert resolve_item("Lysol 4x90 Odor", plan=plan)["values"]["chosen"] == "3283407"


def test_exact_code(plan):
    assert resolve_item("3283407", plan=plan)["values"]["chosen"] == "3283407"


# ---------- lanes ----------
def test_lane_usr2_to_vegas():
    v = lookup_lane("USR2", "USQ4", "3283407")["values"]
    assert v["leadtime_days"] == 13 and v["type"] == "Transfer"


def test_shuttle_cheapest_mode():
    v = lookup_lane("USP6", "USP8")["values"]
    assert v["cost_per_load"] == 850.0 and v["mode"] == "INTERMODAL"


# ---------- S4 decision tree (E.5) ----------
CUT = {"cut_id": "t1", "material": "3313455", "lc": "USQ4", "cut_date": "2026-10-15"}


def test_tree_asks_for_pacing(inv, att):
    r = walk_decision_tree(CUT, inv=inv, att=att)
    assert r["status"] == "needs_input" and set(r["values"]["needs_input"]) == {"national_pct", "lc_pct"}


def test_tree_proposes_c0(inv, att):
    r = walk_decision_tree(CUT, {"national_pct": 100, "lc_pct": 100}, inv=inv, att=att)
    assert r["values"]["proposed_code"] == "C0"
    r05 = [p for p in r["values"]["path"] if p["rule_id"] == "R05"][0]
    assert r05["evidence"]["ATP_QUANTITY"] == 484 and r05["evidence"]["ATP_AFTER_ALLOCATION"] == 0


def test_tree_nearest_snapshot(inv, att):
    # Appendix E.5 says 2026-10-18 has no snapshot, but the file has one. Real gap: 08-16 -> 08-19.
    r = walk_decision_tree({**CUT, "cut_date": "2026-08-18"}, {"national_pct": 100, "lc_pct": 100}, inv=inv, att=att)
    r05 = [p for p in r["values"]["path"] if p["rule_id"] == "R05"][0]
    assert r05["evidence"]["snapshot_date"] == "2026-08-16"


def test_tree_no_inventory_before_first_snapshot(inv, att):
    r = walk_decision_tree({**CUT, "cut_date": "2025-11-01"}, {"national_pct": 100, "lc_pct": 100}, inv=inv, att=att)
    assert "inventory_snapshot" in r["values"]["needs_input"]


def test_tree_atp_positive_branch(inv, att):
    r = walk_decision_tree({**CUT, "cut_date": "2026-09-15"}, {"national_pct": 100, "lc_pct": 100}, inv=inv, att=att)
    assert any(p["rule_id"] == "R12" for p in r["values"]["path"])


def test_pacing_over_130_gives_b2(inv, att):
    assert walk_decision_tree(CUT, {"national_pct": 140}, inv=inv, att=att)["values"]["proposed_code"] == "B2"


def test_codes_are_active():
    assert {"C0", "B2", "W0", "A0", "O0", "B3"} <= ACTIVE


# ---------- S5 diff (E.6) ----------
def test_inventory_fallback_deltas():
    r = diff_snapshots("2026-10-22", "2026-10-29", materials=["3283407"])
    rows = {x[1]: dict(zip(r["table"]["columns"], x)) for x in r["table"]["rows"]}
    assert r["values"]["mode"] == "inventory_fallback"
    assert rows["USP6"]["delta_cases"] == -2063 and rows["USP8"]["delta_cases"] == 11362
    assert rows["USQ2"]["delta_cases"] == -8090 and rows["USQ4"]["delta_cases"] == 3592


def test_reconciliation_flags_3353360(plan):
    rec = reconciliation(plan).set_index("material")
    assert "3353360" in rec.index


def test_self_diff_is_empty(plan):
    r = diff_snapshots("x", "y", plan_a=plan, plan_b=plan)
    assert r["values"]["changes"] == 0 and "No WoC colour changes" in r["warnings"][0]


# ---------- S4 PFR Pareto (workflow doc: Sept LLS PFR 86.5%, ~125k cut; delivered extract gives 85.9%) ----------
def test_sept_lls_pfr():
    from osb_tools.cuts import cuts_pareto
    r = cuts_pareto("2026-09-01", "2026-09-30", "Lysol Sanitizers", top_n=10)
    v = r["values"]
    assert v["total_cut"] == 129656 and v["total_ordered"] == 919757 and v["pfr_pct"] == 85.9
    mats = [x[0] for x in r["table"]["rows"]]
    assert "3313455" in mats and "3283407" in mats       # Crisp Linen 6x21 and 4x90 Odor in the top 10

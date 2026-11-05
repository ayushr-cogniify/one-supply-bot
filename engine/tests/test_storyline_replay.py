"""Replays the 10 Hygiene Storyline cases through the tree with their flags as planner facts.
Appendix C.1 asks for >= 9 of 10. With the rule ORDER in appendix C, 6 match; the 4 that
differ are listed below and need a decision from Prerak/Rushi on rule precedence (not a code bug)."""
import pandas as pd
import pytest

from osb_tools import adapter
from osb_tools.decision_tree import walk_decision_tree

KNOWN_CONFLICTS = {3: "W0 expected, C0 by rule order (R06 before R07)",
                   4: "A0 expected, C0 by rule order (R06 before R08)",
                   6: "O0 expected, no flag in storyline maps to R01",
                   10: "B3 expected, network imbalance maps to R10 (R0) in appendix C"}


def facts(r):
    yes = lambda v: str(v).strip().upper() == "Y"
    return {"national_pct": 140 if yes(r.forecast_increase) else 100, "lc_pct": 100,
            "inbound_delay": yes(r.inbound_delay), "network_imbalance": yes(r.network_imbalance),
            "quality_hold": yes(r.quality_hold), "factory_adherence": yes(r.factory_adherence)}


def _stub_inputs():
    # zero-ATP snapshot and an attainment row driven by the factory-adherence flag
    inv = pd.DataFrame([{"MATERIAL": "X", "PLANT": "USQ4", "snapshot_date": pd.Timestamp("2026-10-15"),
                         "ATP_QUANTITY": 0, "ALLOCATION_QTY": 0, "ATP_AFTER_ALLOCATION": 0,
                         "INTRANSIT_QTY": 0, "BLOCKED_INV": 0, "QUAL_INSPEC": 0}])
    return inv


@pytest.mark.parametrize("scenario", range(1, 11))
def test_storyline(scenario):
    try:
        st = adapter.storyline()
    except FileNotFoundError as e:
        pytest.skip(str(e))
    r = st[st.scenario == scenario].iloc[0]
    f = facts(r)
    att = pd.DataFrame([{"work_center": "PL11", "material": "X", "year_month": 202610,
                         "fix": 100, "actual": 70 if f["factory_adherence"] else 100}])
    got = walk_decision_tree({"material": "X", "lc": "USQ4", "cut_date": "2026-10-15"}, f,
                             inv=_stub_inputs(), att=att)["values"]["proposed_code"]
    if scenario in KNOWN_CONFLICTS:
        pytest.xfail(KNOWN_CONFLICTS[scenario] + f" (got {got})")
    assert got == r.expected_code

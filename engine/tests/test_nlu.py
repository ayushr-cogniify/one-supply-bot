"""Scope gate, slot extraction and validator: pure logic, no data needed (runs in CI)."""
import pytest

from osb_agent import nlu
from osb_agent.checks import OOS_PROMPTS
from osb_agent.validator import validate


@pytest.mark.parametrize("q", OOS_PROMPTS)
def test_appendix_e7_out_of_scope(q):
    assert nlu.parse(q).label == "OUT_OF_SCOPE"


@pytest.mark.parametrize("q,label", [
    ("Can we take +5,000 cases of Odor at Vegas?", "S1"),
    ("What happens if St. Peters runs at 80% attainment?", "S2"),
    ("Can we make 35% more 90 oz laundry sanitizer from November, six months out?", "S3"),
    ("Code the 10/15 cut of Crisp Linen 6x21 at Vegas.", "S4"),
    ("Top cuts in September for Lysol Sanitizers", "S4"),
    ("What changed since last week?", "S5"),
    ("Review my PIR items", "S5"),
    ("PIR for Lysol Sanitizers", "PIR"),
    ("What is the cover on 3283407 at Vegas?", "S1"),
])
def test_routes(q, label):
    assert nlu.parse(q).label == label


def test_s1_slots():
    s = nlu.parse("+5,000 cases of 3283407 at Vegas week of 10/19").slots
    assert s["item_text"] == "3283407" and s["delta_cases"] == 5000 and s["lcs"] == ["USQ4"]
    assert s["week"] == {"date": (None, 10, 19)}


def test_delta_forms():
    assert nlu.find_delta("5k more odor") == 5000
    assert nlu.find_delta("-2,000 cases") == -2000
    assert nlu.find_delta("cancel 1,500 cases") == -1500
    assert nlu.find_delta("what is the cover") is None


def test_item_text_forms():
    assert nlu.find_item_text("can we take 5k more odor next week?") == "odor"
    assert nlu.find_item_text("code the 10/15 cut of crisp linen 6x21 at vegas.") == "crisp linen 6x21"
    assert nlu.find_item_text("upc 019200832844 at vegas") == "019200832844"


def test_sites():
    assert nlu.find_lcs("north las vegas") == ["USQ4"] and nlu.find_lcs("harrisburg") == ["USP6"]
    assert nlu.find_factory("st. peters at 80%") == "USR2" and nlu.find_factory("belle mead") == "USA2"
    assert nlu.find_work_center("pl11 at 80%") == "PL11"


def test_kinaxis_week_is_not_iso():
    assert nlu.find_week("week 202643") == {"calweek": 202643}
    assert nlu.find_week("next week") == {"relative": 1}


def test_validator_accepts_tool_numbers():
    res = [{"values": {"woc_before": 2.5, "woc_after": 0.3, "delta_cases": 5000}, "table": None}]
    assert validate("USQ4 drops from 2.5 to 0.3 weeks after +5,000 cases [r:S1-0001].", res) == []
    assert validate("Network falls to 1.4 weeks.", res) == ["1.4"]

"""Conversations through the agent pipeline, driven the way the browser drives it: JSON request and
session in, JSON reply and session out. Each test is a planner flow from the handoff design."""
import json

import pytest

from osb_agent import api
from osb_agent.checks import OOS_PROMPTS
from osb_agent.templates import T


class Chat:
    def __init__(self):
        self.session = None
        self.last = None

    def send(self, request: dict) -> dict:
        out = json.loads(api.handle(json.dumps(request), json.dumps(self.session) if self.session else ""))
        self.session, self.last = out["session"], out
        return out

    def say(self, text):
        return self.send({"type": "message", "text": text})

    def pick(self, value, slot=None):
        """Click the option whose action value matches (in the last bot message that has options)."""
        msgs = [m for m in self.last["messages"] if m["options"]]
        assert msgs, f"no options in {[m['text'] for m in self.last['messages']]}"
        for o in msgs[-1]["options"]:
            a = o["action"]
            if a.get("value") == value and (slot is None or a.get("slot") == slot):
                return self.send(a)
        raise AssertionError(f"no option {value!r} in {[o['label'] for o in msgs[-1]['options']]}")

    def templates(self):
        return [m.get("template") for m in self.last["messages"]]

    def card(self):
        assert self.last["cards"], f"no card; messages: {[m['text'] for m in self.last['messages']]}"
        c = self.last["cards"][-1]
        # Every narrative the planner sees must pass the numeric validator
        assert c["validator"]["passed"], (c["scenario"], c["validator"])
        assert "VALIDATOR_FAIL" not in self.templates()
        return c

    def tools(self):
        return [s["name"] for s in self.last["trace"]["spans"] if s["step"] == "tool"]


def block(card, kind):
    return [b for b in card["blocks"] if b["type"] == kind]


def grid_cell(card, loc, i):
    g = block(card, "wocgrid")[0]
    return next(r for r in g["rows"] if r["loc"] == loc)["cells"][i]


@pytest.fixture
def chat(plan):          # `plan` comes from conftest: skips when the workbooks are absent
    return Chat()


# ---------- S1 ----------
def test_s1_odor_asks_item_then_week(chat):
    chat.say("Can we take +5,000 cases of Odor at Vegas?")
    assert "CLARIFY_ITEM" in chat.templates()
    chat.pick("3283407", "material")
    assert "CLARIFY_WEEK" in chat.templates()
    chat.pick(202643, "calweek")
    c = chat.card()
    assert c["scenario"] == "S1" and c["validator"]["passed"]
    assert grid_cell(c, "USQ4", 0) == {"before": 2.5, "after": 0.3, "cb": "yellow", "ca": "red"}
    assert grid_cell(c, "NETWORK", 0)["after"] == 1.8
    assert any(m["kind"] == "echo" for m in chat.last["messages"])
    assert any(m["kind"] == "answer" and "Do not support" in m["text"] for m in chat.last["messages"])
    assert "lookup_lane" in chat.tools()


def test_s1_code_only_asks_week_then_scope(chat):
    chat.say("Can we take +5,000 cases of 3283407?")
    assert "CLARIFY_WEEK" in chat.templates()
    chat.pick(202643, "calweek")
    assert "CLARIFY_SCOPE" in chat.templates()
    chat.pick("USQ4", "scope")
    assert chat.card()["scenario"] == "S1"


def test_s1_network_needs_one_lc(chat):
    chat.say("+5,000 cases of 3283407 across the network week of 10/19")
    assert "CLARIFY_SCOPE" in chat.templates()          # CLARIFY_LC uses the scope template
    chat.pick("USQ4", "lc")
    assert chat.card()["scenario"] == "S1"


def test_s1_full_request_needs_no_question(chat):
    chat.say("+5,000 cases of 3283407 at Vegas week of 10/19")
    c = chat.card()
    assert not [m for m in chat.last["messages"] if m["options"]]
    assert grid_cell(c, "USQ4", 1)["after"] == 1.5


def test_s1_follow_up_changes_lc(chat):
    chat.say("+5,000 cases of 3283407 at Vegas week of 10/19")
    chat.say("and at Atlanta?")
    assert "USP8" in chat.card()["subtitle"]


def test_cover_query_and_missing_item(chat):
    chat.say("What is the cover on 3283407 at Vegas?")
    assert chat.card()["scenario"] == "S1P"
    chat.say("What is the cover on 9999999 at Vegas?")
    assert chat.templates() == ["NO_DATA_ITEM"] and not chat.last["cards"]


def test_question_cap(chat):
    chat.say("Can we take some cases of Odor?")
    assert "CLARIFY_ITEM" in chat.templates()            # question 1: which item
    chat.pick("3283407", "material")
    assert "+5,000" in chat.last["messages"][-1]["text"]   # question 2: how many cases
    chat.say("+5,000")
    assert "CLARIFY_WEEK" in chat.templates()            # question 3: which week
    chat.pick(202643, "calweek")
    assert chat.templates() == ["QUESTION_CAP"]          # a fourth question is not asked
    assert chat.session["pending"] is None and not chat.last["cards"]


# ---------- scope ----------
@pytest.mark.parametrize("q", OOS_PROMPTS)
def test_out_of_scope(chat, q):
    chat.say(q)
    assert chat.templates() == ["OUT_OF_SCOPE"] and not chat.tools() and not chat.last["cards"]
    assert chat.last["messages"][0]["text"] == T["OUT_OF_SCOPE"]


# ---------- S2 ----------
def test_s2_st_peters_asks_line(chat):
    chat.say("What happens if St. Peters runs at 80% attainment?")
    assert "CLARIFY_LINE" in chat.templates()
    labels = [o["label"] for o in chat.last["messages"][-1]["options"]]
    assert any(l.startswith("PL11") and "72.7%" in l for l in labels)
    chat.pick("PL11", "work_center")
    c = chat.card()
    assert c["scenario"] == "S2" and c["validator"]["passed"]
    kv = block(c, "kv")[0]["items"]
    assert next(i for i in kv if i["label"] == "Trailing 3 months")["value"] == "72.7%"


# ---------- S3 ----------
def test_s3_needs_capacity_then_computes_gap(chat):
    chat.say("Can we make 35% more 90 oz laundry sanitizer from November, six months out?")
    c = chat.card()
    assert c["status"] == "needs_input" and "NO_DATA_CAPACITY" in chat.templates()
    rows = block(c, "table")[0]["rows"]
    assert rows[0]["month"] == "2026-11" and rows[0]["demand_units"] == 1588744 and rows[0]["grown_units"] == 2144804
    gap = next(g for g in chat.last["gaps"] if g["id"] == "rated_capacity")
    assert gap["state"] == "open" and gap["context"]["work_center"] == "PL11"
    chat.send({"type": "gap_submit", "gap": "rated_capacity",
               "values": {"work_center": "PL11", "rated_capacity_units_per_month": 2000000, "unit_basis": "Bottles"}})
    c2 = chat.card()
    assert c2["id"] == c["id"] and c2["status"] == "ok"
    nov = block(c2, "table")[0]["rows"][0]
    assert nov["effective_capacity_units"] == 1454000 and nov["gap_units"] == 690804
    assert chat.session["supplements"]["Work_Centres"][0]["entered_by"] == "Demo planner"


# ---------- S4 ----------
def test_s4_cut_asks_pacing_then_proposes_c0_and_approves(chat):
    chat.say("Code the 10/15 cut of Crisp Linen 6x21 at Vegas.")
    c = chat.card()
    assert c["status"] == "needs_input" and "NO_DATA_PACING" in chat.templates()
    path = block(c, "path")[0]["rows"]
    assert [r["result"] for r in path if r["rule"] in ("R03", "R04")] == ["unknown", "unknown"]
    chat.pick("no", "pacing_national")
    assert "NO_DATA_PACING" in chat.templates()
    chat.pick("no", "pacing_lc")
    c = chat.card()
    assert c["proposed_code"] == "C0" and c["validator"]["passed"]
    r05 = next(r for r in block(c, "path")[0]["rows"] if r["rule"] == "R05")
    assert "ATP 484" in r05["evidence"] and "after allocation 0" in r05["evidence"]
    assert "ACTION_PENDING" in chat.templates()
    draft = next(o["action"] for m in chat.last["messages"] for o in m["options"] if o["action"]["type"] == "approve")
    chat.send(draft)
    assert chat.session["approvals"] == 1 and chat.session["supplements"]["Approvals"][0]["decision"] == "approved"
    assert len(chat.session["supplements"]["Pacing"]) == 2


def test_s4_vegas_cut_without_item_lists_lines(chat):
    chat.say("Code the 10/15 Vegas cut")
    assert "CLARIFY_ITEM" in chat.templates()
    values = [o["action"]["value"] for o in chat.last["messages"][-1]["options"]]
    assert values[0] == "3323331" and len(values) == 5     # largest cut that day comes first


def test_s4_pacing_above_130_gives_b2(chat):
    chat.say("Code the 10/15 cut of Crisp Linen 6x21 at Vegas.")
    chat.pick("yes", "pacing_national")
    assert chat.card()["proposed_code"] == "B2"


def test_s4_skip_leaves_no_code(chat):
    chat.say("Code the 10/15 cut of Crisp Linen 6x21 at Vegas.")
    chat.pick("skip", "pacing_national")
    chat.pick("skip", "pacing_lc")
    c = chat.card()
    assert c["proposed_code"] is None and "ACTION_PENDING" not in chat.templates()


def test_s4_reject_then_alternate(chat):
    chat.say("Code the 10/15 cut of Crisp Linen 6x21 at Vegas.")
    chat.pick("no", "pacing_national")
    chat.pick("no", "pacing_lc")
    rej = next(o["action"] for m in chat.last["messages"] for o in m["options"] if o["action"]["type"] == "reject")
    chat.send(rej)
    alt = next(o["action"] for o in chat.last["messages"][-1]["options"] if o["action"].get("code") == "W0")
    chat.send(alt)
    assert "ACTION_PENDING" in chat.templates()


def test_s4_pareto(chat):
    chat.say("Top cuts in September for Lysol Sanitizers")
    c = chat.card()
    assert c["scenario"] == "S4P"
    kv = {i["label"]: i["value"] for i in block(c, "kv")[0]["items"]}
    assert kv["PFR"] == "85.9%" and kv["Cases cut"] == "129,656"


# ---------- S5 ----------
def test_s5_offers_fallback(chat):
    chat.say("What changed since last week?")
    assert chat.templates() == ["NO_SECOND_SNAPSHOT"]
    chat.pick("inventory", "s5_mode")
    c = chat.card()
    assert c["scenario"] == "S5" and c["validator"]["passed"]


def test_s5_pir_items(chat):
    chat.say("Review my PIR items")
    assert any(g["id"] == "pir_list" and g["state"] == "open" for g in chat.last["gaps"])
    chat.send({"type": "gap_submit", "gap": "pir_list", "values": {"items": "3283407"}})
    c = chat.card()
    rows = {r["location"]: r["delta_cases"] for r in block(c, "table")[0]["rows"]}
    assert rows == {"USP6": -2063, "USP8": 11362, "USQ2": -8090, "USQ4": 3592}


def test_pir_list_rejects_unknown_code(chat):
    chat.say("Review my PIR items")
    chat.send({"type": "gap_submit", "gap": "pir_list", "values": {"items": "1234567"}})
    assert not chat.session["supplements"]["PIR_List"] and not chat.last["cards"]


# ---------- PIR ----------
def test_pir_group(chat):
    chat.say("PIR for Lysol Sanitizers")
    c = chat.card()
    assert c["scenario"] == "PIR" and c["export"]["rows"]
    levels = {r["level"] for r in block(c, "pir")[0]["rows"]}
    assert levels == {"GROUP", "NETWORK", "LC"}


def test_retract_supplement(chat):
    chat.say("Review my PIR items")
    chat.send({"type": "gap_submit", "gap": "pir_list", "values": {"items": "3283407"}})
    rid = chat.session["supplements"]["PIR_List"][0]["row_id"]
    chat.send({"type": "retract", "sheet": "PIR_List", "row_id": rid})
    assert chat.session["supplements"]["PIR_List"][0]["retracted"] is True
    assert any(a["event"] == "supplement_retract" for a in chat.session["audit"])

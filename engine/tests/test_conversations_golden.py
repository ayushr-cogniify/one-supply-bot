"""Multi-turn planner conversations: the app must keep context across turns, use what it already
knows (item, LC, week, line, growth, the last answer), take loose answers to its own questions, and
never ask the same question again for something it was already given.

Each conversation runs on the rules engine (offline) and, with OSB_LIVE_CLAUDE=1, on the live Claude
gate exactly as the browser calls it."""
import os

import pytest

from test_pipeline_golden import Chat, block

try:
    from test_claude_live import ClaudeChat, token  # noqa: F401  (fixture re-export)
except Exception:                                    # pragma: no cover
    ClaudeChat = None

LIVE = os.environ.get("OSB_LIVE_CLAUDE") == "1"


@pytest.fixture(params=["rules", "claude"])
def chat(request, plan):
    if request.param == "claude":
        if not LIVE or ClaudeChat is None:
            pytest.skip("set OSB_LIVE_CLAUDE=1 for the live Claude run")
        return ClaudeChat(request.getfixturevalue("token"))
    return Chat()


# ---------- helpers ----------
def bot_text(chat):
    return " ".join(m["text"] for m in chat.last["messages"] if m["kind"] in ("bot", "answer"))


def questions(chat):
    return [m["text"] for m in chat.last["messages"] if m["kind"] == "bot" and m["options"]
            and any(o["action"]["type"] == "option" for o in m["options"])]


def last_tool(chat, name):
    calls = [s["args"] for s in chat.last["trace"]["spans"] if s["step"] == "tool" and s["name"] == name]
    assert calls, f"{name} was not called; tools: {chat.tools()}"
    return calls[-1]


class Guard:
    """Records every question the bot asks in a conversation; the same question twice in a row
    (without new information) is a failure."""
    def __init__(self, chat):
        self.chat, self.prev = chat, None

    def say(self, text):
        self.chat.say(text)
        return self._check()

    def click(self, value, slot=None):
        self.chat.pick(value, slot)
        return self._check()

    def _check(self):
        qs = questions(self.chat)
        if qs:
            assert qs[-1] != self.prev, f"asked the same question again: {qs[-1]}"
            self.prev = qs[-1]
        else:
            self.prev = None
        for c in self.chat.last["cards"]:
            assert c["validator"]["passed"], c["validator"]
        return self.chat


# ---------- C1: one demand request, refined over seven turns ----------
def test_c1_iterating_on_a_demand_request(chat):
    g = Guard(chat)
    g.say("Can we take 5k more cases of 4x90 Odor at Vegas week of 10/19?")
    if "CLARIFY_ITEM" in chat.templates():
        g.click("3283407", "material")
    a = last_tool(chat, "assess_demand_change")
    assert (a["material"], a["lc"], a["delta_cases"]) == ("3283407", "USQ4", 5000)

    g.say("what about Atlanta?")                          # LC changes, nothing else
    a = last_tool(chat, "assess_demand_change")
    assert (a["material"], a["lc"], a["week"], a["delta_cases"]) == ("3283407", "USP8", 202643, 5000)

    g.say("and if it's 10k instead?")                     # quantity changes, Atlanta kept
    a = last_tool(chat, "assess_demand_change")
    assert (a["lc"], a["delta_cases"]) == ("USP8", 10000)

    g.say("use an 8 week average")                        # cover window changes, rest kept
    a = last_tool(chat, "assess_demand_change")
    assert (a["lc"], a["delta_cases"], a["window_weeks"]) == ("USP8", 10000, 8)

    g.say("why does it drop?")                            # answered from the last result, no new run
    assert not chat.tools() and "USP8" in bot_text(chat) and "week 2026" in bot_text(chat)

    g.say("ok draft the replan")
    assert "ACTION_PENDING" in chat.templates()
    g.say("approve it")
    assert chat.session["approvals"] == 1


# ---------- C2: loose answers to the bot's own questions ----------
def test_c2_loose_answers_are_understood(chat):
    g = Guard(chat)
    g.say("can we support some extra demand for odor?")
    assert "CLARIFY_ITEM" in chat.templates()
    g.say("the 4x90 one")                                 # picks from the options it offered
    assert chat.session["pending"]["r"]["material"] == "3283407"
    g.say("5000 cases, week of 10/26")
    a = last_tool(chat, "assess_demand_change")
    assert (a["material"], a["delta_cases"], a["week"]) == ("3283407", 5000, 202644)
    g.say("just Harrisburg")
    a = last_tool(chat, "assess_demand_change")
    assert (a["lc"], a["delta_cases"], a["week"]) == ("USP6", 5000, 202644)


def test_c2b_details_given_before_the_item_are_kept(chat):
    g = Guard(chat)
    g.say("can we support some extra demand for odor?")
    g.say("5000 cases at vegas, week of 10/26")           # answers a different question: keep it
    assert "Noted" in bot_text(chat) and "CLARIFY_ITEM" in chat.templates()
    g.say("3283407")
    a = last_tool(chat, "assess_demand_change")
    assert (a["material"], a["lc"], a["delta_cases"], a["week"]) == ("3283407", "USQ4", 5000, 202644)


# ---------- C3: from an attainment drop to demand on the same line, then the item's PIR ----------
def test_c3_line_context_carries_over(chat):
    g = Guard(chat)
    g.say("St Peters is running at 80%")
    g.click("PL11", "work_center")
    assert last_tool(chat, "simulate_attainment")["attainment_pct"] == 80
    g.say("what about 70%?")
    a = last_tool(chat, "simulate_attainment")
    assert (a["work_center"], a["attainment_pct"]) == ("PL11", 70)
    g.say("and can it still support 3000 more cases of 3283407 in week of 10/19?")
    a = last_tool(chat, "simulate_attainment")
    assert a["work_center"] == "PL11" and a["attainment_pct"] == 70
    assert "3283407" in str(a.get("extra_demand"))
    g.say("show me the PIR for that item")
    assert last_tool(chat, "pir_report")["materials"] == ["3283407"]


# ---------- C4: segment growth, refined, then capacity given in words ----------
def test_c4_growth_and_capacity_in_conversation(chat):
    g = Guard(chat)
    g.say("90 oz laundry could grow 30% in november, can we support it?")
    assert last_tool(chat, "segment_growth_cover")["growth_pct"] == 30
    g.say("what if it's only 15%?")
    assert last_tool(chat, "segment_growth_cover")["growth_pct"] == 15
    g.say("PL11 capacity is 2.5M bottles a month")
    save = next(o["action"] for m in chat.last["messages"] for o in m["options"] if o["action"]["type"] == "gap_submit")
    chat.send(save)
    g.say("rerun it")
    a = last_tool(chat, "capacity_gap")
    assert a["growth_pct"] == 15 and a["rated_capacity_units"] == 2500000


# ---------- C5: reason coding with yes/no and an unsure answer ----------
def test_c5_reason_coding_unsure(chat):
    g = Guard(chat)
    g.say("code the 10/15 cut of crisp linen 6x21 at vegas")
    assert "NO_DATA_PACING" in chat.templates()
    g.say("no")
    assert "USQ4" in questions(chat)[-1]                  # moves on to the LC question, not the same one
    g.say("not sure")
    assert "won't propose a code" in bot_text(chat) and chat.card()["proposed_code"] is None


# ---------- C6: what changed, why, then drill into the top item ----------
def test_c6_digest_then_drill_down(chat):
    g = Guard(chat)
    g.say("what changed this week?")
    top = block(chat.card(), "table")[0]["rows"][0]["material"]
    g.say("why did the top one move?")
    assert not chat.tools() and top in bot_text(chat)
    g.say("show me the cover for that item")
    assert last_tool(chat, "get_plan")["material"] == top
    g.say("and the PIR for it")
    assert last_tool(chat, "pir_report")["materials"] == [top]


# ---------- C7: an out-of-scope turn in the middle does not lose the thread ----------
def test_c7_context_survives_an_off_topic_turn(chat):
    g = Guard(chat)
    g.say("+5,000 cases of 3283407 at Vegas week of 10/19")
    g.say("email the plant about it")                     # refused, but the request stays in memory
    assert "OUT_OF_SCOPE" in chat.templates()
    g.say("what about Harrisburg?")
    a = last_tool(chat, "assess_demand_change")
    assert (a["material"], a["lc"], a["delta_cases"]) == ("3283407", "USP6", 5000)

"""The questions from the Nov 6 review with Prerak, and the workflow doc's own examples. Each must be
answered by following the scenario flowchart, from the data, without a dead end."""
import pytest

from test_pipeline_golden import Chat, block


@pytest.fixture
def chat(plan):
    return Chat()


def answer(chat):
    return next((m["text"] for m in chat.last["messages"] if m["kind"] == "answer"), "")


def table(card, prefix):
    return next(b for b in card["blocks"] if b["type"] == "table" and b["title"].startswith(prefix))


# ---- Scenario 1, workflow doc: "Can we take +5k cases of Lysol 4x90 Odor in week 10/19?" ----
def test_s1_doc_question_whole_network(chat):
    chat.say("Can we take +5k cases of 3283407 in week 10/19?")
    c = chat.card()                                        # no LC question: spread over the network
    assert c["scenario"] == "S1" and "network (by LC demand)" in c["subtitle"]
    kv = {i["label"]: i for i in block(c, "kv")[0]["items"]}
    assert kv["Spread across"]["value"] == "Network"
    a = answer(chat)
    assert "Network cover moves from 2.1 to 1.8 weeks" in a and "Options:" in a


def test_s1_vegas_follows_the_flowchart(chat):
    chat.say("Can we take +5k cases of Lysol 4x90 Odor at Vegas in week 10/19?")
    if "CLARIFY_ITEM" in chat.templates():
        chat.pick("3283407", "material")
    c = chat.card()
    a = answer(chat)
    # 1 WoC per LC and network  2 flag the LC  3 why  4 support-until  5 options
    assert "Network cover moves from 2.1 to 1.8" in a
    assert "USQ4 drops to 0.3 weeks" in a and "next inbound of 4,914 cases lands in week 202644" in a
    assert "red from week 202643" in a and "needs 3,881 more cases" in a
    assert "factory replan at USR2" in a
    assert table(c, "Why USQ4")["rows"][0]["supply"] == 4446
    assert any("Draft replan request" == o["label"] for m in chat.last["messages"] for o in m["options"])


def test_s1_eight_week_cover(chat):
    chat.say("+5,000 cases of 3283407 at Vegas week of 10/19 with an 8-week cover window")
    v = chat.last["trace"]["results"][-1]["values"]
    assert v["window_weeks"] == 8 and v["network_before"] == 2.0


def test_cover_not_6_but_8_weeks(chat):
    chat.say("I want to see the weeks of cover not by 6 weeks but 8 weeks for 3283407")
    args = [s["args"] for s in chat.last["trace"]["spans"] if s["step"] == "tool" and s["name"] == "get_plan"][0]
    assert args["window_weeks"] == 8


def test_next_two_months_segment_cover(chat):
    chat.say("what are the weeks of cover for the next 2 months for the 90 oz laundry segment")
    c = chat.card()
    assert c["scenario"] == "PIR" and len(block(c, "pir")[0]["weeks"]) == 8


# ---- Scenario 2 with extra demand (workflow: "can a +35k increase still be supported?") ----
def test_attainment_drop_plus_demand(chat):
    chat.say("my attainment is reduced by 75% and I need to serve 3000 cases of 3283407 week of 10/19")
    assert "CLARIFY_PCT" in chat.templates()               # 'reduced by' is ambiguous: ask, don't guess
    chat.pick(75, "pct")
    c = chat.card()
    assert c["scenario"] == "S2" and "PL11" in c["subtitle"]          # line taken from the item
    assert "With +3,000 cases of 3283407 in week 202643 on top" in answer(chat)


# ---- Scenario 3: answer from inventory and the line's plan, never dead-end on capacity ----
def test_segment_growth_answers_first(chat):
    chat.say("lysol 4x90 segment is expected to grow by 30% in november, does my current inventory and weeks of cover support it?")
    c = chat.card()
    assert c["scenario"] == "S3" and "NO_DATA_CAPACITY" not in chat.templates()
    a = answer(chat)
    assert "current inventory does not support it" in a and "PL11 is short from 2026-11" in a


def test_segment_growth_fifteen_percent(chat):
    chat.say("growth of 15% for the 90 oz laundry segment")
    assert chat.card()["scenario"] == "S3"


# ---- Dashboard ----
def test_network_dashboard(chat):
    chat.say("give me the network dashboard")
    c = chat.card()
    assert c["scenario"] == "DASH"
    lines = next(b for b in c["blocks"] if b["type"] == "table" and b["title"].startswith("Factory lines"))
    assert any(r["work_center"] == "PL11" and r["trailing_pct"] == 72.7 for r in lines["rows"])


# ---- Never forecast: every number in every answer traces to a tool result ----
@pytest.mark.parametrize("q", [
    "Can we take +5k cases of 3283407 in week 10/19?",
    "Can we take +2,000 cases of 3260319 at Atlanta week of 10/26?",
    "lysol 4x90 segment is expected to grow by 30% in november",
    "What happens if PL11 runs at 80% attainment?",
    "Why was laundry sanitizer fill rate so low in September?",
    "What changed since last week?",
])
def test_answers_only_use_data(chat, q):
    chat.say(q)
    c = chat.card()                                        # Chat.card() asserts the validator passed
    assert c["validator"]["passed"] and c["sources"]

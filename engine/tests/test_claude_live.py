"""The same planner flows with the live Claude gate: each message goes to the deployed osb-nlu Edge
Function (Claude Haiku 5.5) as the signed-in test user, and its parse drives the pipeline exactly as
the browser does. Costs a few cents per run, so it only runs with OSB_LIVE_CLAUDE=1."""
import json
import os
import urllib.request
from pathlib import Path

import pytest

from osb_agent import api
from osb_agent.checks import OOS_PROMPTS
from test_pipeline_golden import Chat, block, grid_cell

pytestmark = pytest.mark.skipif(os.environ.get("OSB_LIVE_CLAUDE") != "1", reason="set OSB_LIVE_CLAUDE=1")


def _env():
    env = {}
    for line in (Path(__file__).resolve().parents[2] / ".env").read_text().splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def _post(url, body, headers):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json",
                                                                            **headers})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


@pytest.fixture(scope="module")
def token():
    e = _env()
    t = _post(f"{e['SUPABASE_URL']}/auth/v1/token?grant_type=password",
              {"email": e["E2E_EMAIL"], "password": e["E2E_PASSWORD"]}, {"apikey": e["SUPABASE_ANON_KEY"]})
    return e, t["access_token"]


class ClaudeChat(Chat):
    def __init__(self, token):
        super().__init__()
        self.env, self.token = token

    def say(self, text):
        active = ((self.session or {}).get("pending") or (self.session or {}).get("last") or {}).get("scenario", "none")
        names = {"S1": "S1_demand_spike", "S2": "S2_attainment_drop", "S3": "S3_segment_growth",
                 "S4": "S4_pfr_reason_coding", "S5": "S5_what_changed", "PIR": "PIR_report"}
        parse = _post(f"{self.env['SUPABASE_URL']}/functions/v1/osb-nlu",
                      {"text": text, "context": {"today": "2026-11-05", "snapshot_week_start": "2026-10-19",
                                                 "active_scenario": names.get(active, "none")}},
                      {"Authorization": f"Bearer {self.token}", "apikey": self.env["SUPABASE_ANON_KEY"]})
        assert parse["ok"], parse
        self.last_parse = parse
        return self.send({"type": "message", "text": text, "parse": parse})

    def gate(self):
        return next(s for s in self.last["trace"]["spans"] if s["step"] == "scope_gate")


@pytest.fixture
def chat(plan, token):
    return ClaudeChat(token)


def tool_args(chat, name):
    return [s["args"] for s in chat.last["trace"]["spans"] if s["step"] == "tool" and s["name"] == name]


@pytest.mark.parametrize("q", OOS_PROMPTS)
def test_out_of_scope(chat, q):
    chat.say(q)
    assert chat.templates() == ["OUT_OF_SCOPE"] and not chat.tools(), chat.gate()


def test_e2_full_request(chat):
    chat.say("+5,000 cases of 3283407 at Vegas week of 10/19")
    assert chat.gate()["engine"].startswith("claude")
    assert grid_cell(chat.card(), "USQ4", 0)["after"] == 0.3


def test_loose_wording_s1(chat):
    chat.say("walmart wants another 5k of the 4x90 odor into vegas for week of oct 19 - are we ok?")
    assert grid_cell(chat.card(), "USQ4", 0)["after"] == 0.3


def test_percent_more(chat):
    chat.say("Customer wants 25% more 3283407 at Vegas week of 10/19")
    assert tool_args(chat, "simulate_demand_change")[0].get("delta_pct") == 25.0


def test_mixed_request(chat):
    chat.say("Can we take +5k of 3283407 at Vegas week of 10/19, and email the plant if not?")
    assert chat.card()["scenario"] == "S1" and "PARTIAL_SCOPE" in chat.templates()


def test_week_number(chat):
    chat.say("+5,000 cases of 3283407 at Vegas in week 43")
    assert tool_args(chat, "simulate_demand_change")[0]["week"] == 202643


def test_cut_demand_negative(chat):
    chat.say("Cut 2,000 cases of 3283407 demand at Vegas week of 10/19")
    assert tool_args(chat, "simulate_demand_change")[0]["delta_cases"] == -2000


def test_st_peters_plant(chat):
    chat.say("St. Peters is only running at 80% this month, what does that do to service?")
    assert "CLARIFY_LINE" in chat.templates()


def test_line_alias(chat):
    chat.say("Line 9 at 80%")
    assert "CLARIFY_LINE" in chat.templates() and "Line 9" in chat.last["messages"][-1]["text"]


def test_range(chat):
    chat.say("90 oz sanitizer could grow 30-40%, can we make it?")
    assert "CLARIFY_RANGE" in chat.templates()


def test_s4_code_cut(chat):
    chat.say("why did we cut crisp linen 6x21 at vegas on 10/15? code it")
    assert chat.card()["scenario"] == "S4" and "NO_DATA_PACING" in chat.templates()


def test_s4_pareto(chat):
    chat.say("Why was laundry sanitizer fill rate so low in September?")
    kv = {i["label"]: i["value"] for i in block(chat.card(), "kv")[0]["items"]}
    assert kv["PFR"] == "85.9%"


def test_s5(chat):
    chat.say("What moved since last Monday?")
    assert chat.card()["scenario"] == "S5"


def test_follow_up(chat):
    chat.say("+5,000 cases of 3283407 at Vegas week of 10/19")
    chat.say("and what about Atlanta?")
    assert "USP8" in chat.card()["subtitle"]


def test_data_entry_capacity(chat):
    chat.say("Capacity on PL11 is 2M bottles a month")
    save = next(o["action"] for m in chat.last["messages"] for o in m["options"])
    assert save["type"] == "gap_submit" and save["values"]["rated_capacity_units_per_month"] == 2000000


def test_pir(chat):
    chat.say("Show me the projected inventory for Lysol Sanitizers")
    assert chat.card()["scenario"] == "PIR"

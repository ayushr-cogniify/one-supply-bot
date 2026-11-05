"""Model routing (MVP doc 9, the Model_Route sheet).

Every stage names the Claude endpoint the PRD assigns it and the engine that runs it in this build.
With no model connected, the scope gate and slot extraction run on the deterministic rules in
`nlu`, and the narrative is drafted from tool values in `cards`. Connecting Claude means replacing
`scope_and_slots` with a Haiku call that returns the same dict; the scenarios, tools, validator
and templates do not change."""
from . import nlu

ROUTE = {
    "scope_gate": {"model": "Claude Haiku 5.5", "endpoint": "databricks-claude-haiku-5-5"},
    "slots": {"model": "Claude Haiku 5.5", "endpoint": "databricks-claude-haiku-5-5"},
    "orchestrator": {"model": "Claude Sonnet 5.5", "endpoint": "databricks-claude-sonnet-5-5"},
    "s4_reasoning": {"model": "Claude Opus 5.5", "endpoint": "databricks-claude-opus-5-5"},
}
CONNECTED = False          # set by the host when a Claude endpoint is configured


def route() -> dict:
    eng = "claude" if CONNECTED else "rules"
    return {stage: f"{v['model']}" if CONNECTED else "Rules engine" for stage, v in ROUTE.items()} | {"engine": eng}


def table() -> list:
    return [{"stage": k, "model": v["model"], "endpoint": v["endpoint"],
             "running": v["model"] if CONNECTED else "Rules engine (no model connected)"} for k, v in ROUTE.items()]


def scope_and_slots(text: str) -> dict:
    p = nlu.parse(text)
    return {**p.to_dict(), "engine": "rules"}

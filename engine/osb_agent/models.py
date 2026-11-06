"""Model routing (MVP doc 9, the Model_Route sheet).

Every stage names the Claude endpoint the PRD assigns it and the engine that runs it in this build.
With no model connected, the scope gate and slot extraction run on the deterministic rules in
`nlu`, and the narrative is drafted from tool values in `cards`. Connecting Claude means replacing
`scope_and_slots` with a Haiku call that returns the same dict; the scenarios, tools, validator
and templates do not change."""
from . import nlu

ROUTE = {
    "scope_gate": {"model": "Claude Opus 5.5", "endpoint": "claude-opus-5-5 (PRD: Haiku 5.5)"},
    "slots": {"model": "Claude Opus 5.5", "endpoint": "claude-opus-5-5 (PRD: Haiku 5.5)"},
    "orchestrator": {"model": "Claude Sonnet 5.5", "endpoint": "databricks-claude-sonnet-5-5"},
    "s4_reasoning": {"model": "Claude Opus 5.5", "endpoint": "databricks-claude-opus-5-5"},
}
CONNECTED = False          # set by the host when a Claude endpoint is configured


def route() -> dict:
    eng = "claude" if CONNECTED else "rules"
    return {stage: f"{v['model']}" if CONNECTED else "Rules engine" for stage, v in ROUTE.items()} | {"engine": eng}


RUNNING = {
    "scope_gate": "Claude Opus 5.5 (osb-nlu); rules engine if Claude is unavailable",
    "slots": "Claude Opus 5.5 (osb-nlu); rules engine if Claude is unavailable",
    "orchestrator": "Code drafts the narrative from tool values; numeric validator checks it",
    "s4_reasoning": "Decision tree in code (Appendix C rules)",
}


def table() -> list:
    return [{"stage": k, "model": v["model"], "endpoint": v["endpoint"], "running": RUNNING[k]}
            for k, v in ROUTE.items()]


LABELS = {"S1_demand_spike": "S1", "S2_attainment_drop": "S2", "S3_segment_growth": "S3",
          "S4_pfr_reason_coding": "S4", "S5_what_changed": "S5", "PIR_report": "PIR",
          "OUT_OF_SCOPE": "OUT_OF_SCOPE", "FOLLOW_UP": "FOLLOW_UP", "DATA_ENTRY": "DATA_ENTRY",
          "NETWORK_DASHBOARD": "DASH"}
MIN_CONFIDENCE = 0.70


def _ymd(s):
    try:
        y, m, d = (int(x) for x in str(s)[:10].split("-"))
        return (y, m, d)
    except ValueError:
        return None


def from_claude(c: dict) -> dict:
    """Map the osb-nlu (Claude Haiku) JSON onto the pipeline's slot names. Claude never supplies an
    item code, a converted Kinaxis week or a work centre for a line name: those stay with code."""
    label = LABELS.get(c.get("label"), "OUT_OF_SCOPE")
    sl = c.get("slots") or {}
    out = {}
    if sl.get("item_text"):
        out["item_text"] = sl["item_text"].strip().lower()
    q = sl.get("quantity") or {}
    unit = q.get("unit")
    if q.get("low") is not None and q.get("high") is not None:
        out["pct_range"] = (float(q["low"]), float(q["high"]))
    elif q.get("value") is not None:
        if unit == "percent":
            out["pct"] = float(q["value"])
        elif unit == "cases" or (unit in (None, "", "none") and label in ("S1", "FOLLOW_UP", "DATA_ENTRY")):
            # Kinaxis plans in cases; a bare number on a demand question is cases (the echo-back shows it)
            out["delta_cases"] = int(round(float(q["value"])))
        elif unit in ("units", "bottles"):
            out["quantity_units"] = {"value": float(q["value"]), "unit": unit}
    w = sl.get("week") or {}
    wn = str(w.get("week_number") or "").strip()
    if wn.isdigit():
        out["week"] = {"calweek": int(wn)} if len(wn) == 6 else {"week_number": int(wn)}
    elif w.get("start_date") and _ymd(w["start_date"]):
        out["week"] = {"date": _ymd(w["start_date"])}
    if sl.get("lc_codes"):
        out["lcs"] = list(dict.fromkeys(sl["lc_codes"]))
    if sl.get("network"):
        out["network"] = True
    if sl.get("factory_code") not in (None, "", "none"):
        out["factory"] = sl["factory_code"]
    if sl.get("work_center"):
        out["work_center"] = sl["work_center"].strip().upper()
    if sl.get("line_name"):
        out["line_name"] = sl["line_name"].strip()
    if sl.get("size_oz") is not None and (label in ("S3", "DASH", "PIR") or not sl.get("item_text")):
        out["size_oz"] = float(sl["size_oz"])
    if sl.get("brand") not in (None, "", "none"):
        out["brand"] = sl["brand"]
    if sl.get("cut_date") and _ymd(sl["cut_date"]):
        out["date"] = _ymd(sl["cut_date"])
    if sl.get("month") and len(str(sl["month"])) >= 7:
        y, m = str(sl["month"])[:7].split("-")
        out["month"] = (int(y), int(m))
    if sl.get("horizon_months") is not None:
        out["months"] = int(sl["horizon_months"])
    if sl.get("window_weeks") is not None:
        out["window_weeks"] = int(sl["window_weeks"])
    if sl.get("horizon_weeks") is not None:
        out["horizon_weeks"] = int(sl["horizon_weeks"])
    if sl.get("attainment_pct") is not None:                   # S2 level alongside a case quantity
        out["pct"] = float(sl["attainment_pct"])
    if sl.get("attainment_reduction_pct") is not None and label == "S2":
        out["pct_reduction"] = float(sl["attainment_reduction_pct"])
        out.pop("pct", None)
    intent = sl.get("intent")
    if intent == "cover" and "delta_cases" not in out and "pct" not in out:
        out["cover_query"] = True
    if intent == "code_cut":
        out["code_cut"] = True
    if intent == "pir_items":
        out["pir_items"] = True
    if label == "S5" and "date" not in out and out.get("week", {}).get("date"):
        out["date"] = out["week"]["date"]                    # 'what changed since 10/12'
    if c.get("has_out_of_scope_part") and c.get("out_of_scope_action"):
        out["out_of_scope_action"] = c["out_of_scope_action"]
    if sl.get("ambiguity") not in (None, "", "none"):
        out["ambiguity"] = sl["ambiguity"]
    return {"label": label, "confidence": float(c.get("confidence") or 0), "reason": c.get("reason", ""),
            "slots": out}


def scope_and_slots(text: str, external: dict = None) -> dict:
    """Claude's parse when the browser got one from osb-nlu, else the rules. A low-confidence Claude
    label falls back to the rules when they recognise a scenario (prompts v2, gate note)."""
    rules = {**nlu.parse(text).to_dict(), "engine": "rules"}
    if not external or not external.get("parse"):
        return rules
    c = {**from_claude(external["parse"]), "engine": external.get("model") or "claude",
         "ms": external.get("ms"), "usage": external.get("usage")}
    # Safety net: a brand or category the message plainly names is never dropped
    if "brand" not in c["slots"] and rules["slots"].get("brand"):
        c["slots"]["brand"] = rules["slots"]["brand"]
        if c["slots"].get("item_text", "") in ("laundry sanitizer", "sanitizer", "sanitizers", "lysol sanitizers",
                                                 "disinfecting spray", "lysol lds", "lds", "lls"):
            c["slots"].pop("item_text")
    if c["confidence"] < MIN_CONFIDENCE and c["label"] not in ("OUT_OF_SCOPE",):
        if rules["label"] != "OUT_OF_SCOPE":
            return {**rules, "reason": f"Claude confidence {c['confidence']:.2f}; rules used"}
        return {**c, "label": "OUT_OF_SCOPE", "reason": f"low confidence {c['confidence']:.2f}"}
    return c

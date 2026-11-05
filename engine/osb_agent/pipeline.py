"""Agent pipeline (demo PRD 8.1, MVP doc 5): scope gate -> slot extraction -> clarification (one
question at a time, at most three per request) -> echo-back -> tools -> grounded draft -> numeric
validator -> card -> action gating.

`handle(request, session)` is stateless: the caller passes the session in and keeps the one that
comes back. Requests:
  {"type": "message", "text": ...}
  {"type": "option", "slot": ..., "value": ..., "label": ...}      a clarification button
  {"type": "gap_submit", "gap": ..., "values": {...}}               a Data Gaps form
  {"type": "approve" | "reject", "draft_id": ...}
  {"type": "alternate", "draft_id": ..., "code": ...}"""
import re
import time
from datetime import date, timedelta

from osb_tools import adapter, config
from osb_tools.capacity import select_materials
from osb_tools.decision_tree import ACTIVE
from osb_tools.tools import dispatch

from . import cards, context as cx, models, nlu
from . import supplements as sup
from .templates import T, render

MAX_QUESTIONS = 3
LC_OPTIONS = [{"label": f"{lc} · {name}", "value": lc} for lc, name in cx.SITE_NAMES.items()]
SCENARIO_OPTIONS = [{"label": "S1 Demand spike", "value": "S1"}, {"label": "S2 Attainment drop", "value": "S2"},
                    {"label": "S3 Segment growth", "value": "S3"}, {"label": "S4 Reason coding", "value": "S4"},
                    {"label": "S5 What changed", "value": "S5"}]
FIRST_QUESTION = {
    "S1": ("item", "Which item, and how many cases? For example “+5,000 cases of 4x90 Odor at Vegas”."),
    "S2": ("work_center", "Which line, and at what attainment? For example “PL11 at 80%”."),
    "S3": ("segment", "Which segment and what growth? For example “35% growth in 90 oz laundry”."),
    "S4": ("cut", T["CLARIFY_CUT"]),
    "S5": ("s5_mode", None),
}


def new_session(planner: str = "Demo planner") -> dict:
    return {"planner": planner, "seq": 0, "pending": None, "last": None, "supplements": sup.empty(),
            "audit": [], "drafts": {}, "cards": 0, "weeks_shown": 8, "approvals": 0,
            "last_validator": None, "last_route": None}


class Turn:
    def __init__(self, session):
        self.s = session
        self.messages, self.cards, self.spans, self.results = [], [], [], []
        session["seq"] = session.get("seq", 0) + 1
        self.trace_id = f"tr-{session['seq']:04d}"
        self.t0 = time.perf_counter()

    def span(self, step, **detail):
        self.spans.append({"step": step, "ms": round((time.perf_counter() - self.t0) * 1000), **detail})

    def say(self, text, template=None, options=None, note=None, kind="bot", tone=None):
        self.messages.append({"kind": kind, "text": text, "template": template, "options": options or [],
                              "note": note, "tone": tone})

    def echo(self, text):
        self.messages.append({"kind": "echo", "text": text, "options": [], "note": None, "template": None, "tone": None})
        self.span("echo_back", text=text)

    def tool(self, name, **args):
        t = time.perf_counter()
        res = dispatch(name, args)
        self.results.append(res)
        self.span("tool", name=name, args=_short(args), status=res["status"], result_id=res["result_id"],
                  rows_used=len(res.get("rows_used", [])), tool_ms=round((time.perf_counter() - t) * 1000),
                  warnings=res.get("warnings", []))
        return res


def _short(args):
    out = {}
    for k, v in args.items():
        out[k] = v if not isinstance(v, (list, dict)) or len(str(v)) < 160 else str(v)[:157] + "…"
    return out


# ---------- entry point ----------
def handle(req: dict, session: dict) -> dict:
    t = Turn(session)
    kind = req.get("type", "message")
    try:
        if kind == "message":
            _on_message(t, req.get("text", ""), req.get("parse"))
        elif kind == "option":
            _on_option(t, req)
        elif kind == "gap_submit":
            _on_gap(t, req)
        elif kind in ("approve", "reject", "alternate"):
            _on_decision(t, req)
        elif kind == "draft":
            rp = session.get("last_replan")
            if not rp:
                t.say("There is no card with a replan need to draft from yet.")
            else:
                _draft(t, "replan request", rp["text"], replan=rp)
        elif kind == "retract":
            if sup.retract(session, req.get("sheet"), req.get("row_id")):
                t.say(f"Retracted {req.get('row_id')} in the {req.get('sheet')} sheet. The row stays in the sheet, "
                      "flagged, and is no longer used.", kind="system")
    except Exception as e:                          # never leave the planner with nothing
        t.span("error", error=f"{type(e).__name__}: {e}")
        t.say("Something went wrong running that request, and nothing was changed. The trace has the details.",
              tone="bad")
        session["pending"] = None
    t.span("done")
    return {"messages": t.messages, "cards": t.cards, "session": session, "gaps": gaps(session),
            "trace": {"id": t.trace_id, "spans": t.spans, "results": [_slim(r) for r in t.results]}}


def _slim(r):
    r = dict(r)
    if r.get("table") and len(r["table"]["rows"]) > 60:
        r["table"] = {"columns": r["table"]["columns"], "rows": r["table"]["rows"][:60],
                      "truncated_rows": len(r["table"]["rows"]) - 60}
    r["rows_used_total"] = len(r.get("rows_used", []))
    r["rows_used"] = r.get("rows_used", [])[:25]
    return r


def _on_message(t, text, external=None):
    s = t.s
    p = s.get("pending")
    if p and p.get("awaiting") and _fill_text(t, p, text):
        t.span("follow_up", awaiting=p["awaiting"], text=text)
        p["awaiting"] = None
        return _advance(t, p)
    if re.search(r"\bdraft\b", text.lower()) and s.get("last_replan") and \
            re.search(r"\breplan|\bconstraint|\brequest\b|\bcall\b", text.lower()):
        t.span("draft_request", text=text)
        return _draft(t, "replan request", s["last_replan"]["text"], replan=s["last_replan"])
    parse = models.scope_and_slots(text, external)
    t.span("scope_gate", engine=parse["engine"], label=parse["label"], confidence=parse["confidence"],
           reason=parse["reason"], model_ms=parse.get("ms"))
    if parse["label"] in ("FOLLOW_UP", "DATA_ENTRY"):
        t.span("slots", engine=parse["engine"], slots=parse["slots"])
        s["last_route"] = parse["engine"]
        return _follow_or_entry(t, parse, text)
    t.span("slots", engine=parse["engine"], slots=parse["slots"])
    s["last_route"] = parse["engine"]
    if parse["label"] == "OUT_OF_SCOPE":
        follow = _follow_up(t, parse, text) if parse["reason"] == "no scenario keyword" else None
        if follow:
            return _advance(t, follow)
        s["pending"] = None
        t.say(T["OUT_OF_SCOPE"], template="OUT_OF_SCOPE", options=[
            {"label": o["label"], "action": {"type": "option", "slot": "scenario", "value": o["value"]}}
            for o in SCENARIO_OPTIONS])
        return
    p = {"scenario": parse["label"], "slots": parse["slots"], "r": {}, "awaiting": None, "questions": 0,
         "text": text}
    return _advance(t, p)


def _follow_or_entry(t, parse, text):
    """Claude labelled the message a continuation or a value. Answer the open question with it, offer
    to save a stated line capacity, or rerun the last request with the details changed."""
    s, sl = t.s, parse["slots"]
    p = s.get("pending")
    if p and p.get("awaiting"):
        p["slots"].update({k: v for k, v in sl.items() if k != "out_of_scope_action"})
        for k in ("lcs",):
            if sl.get(k) and len(sl[k]) == 1:
                p["r"]["lc"] = sl[k][0]
        if sl.get("work_center"):
            p["r"]["work_center"] = sl["work_center"]
        p["awaiting"] = None
        return _advance(t, p)
    qu = sl.get("quantity_units")
    if sl.get("work_center") and qu:
        wc = sl["work_center"]
        t.say(f"Save {wc} rated capacity as {cards.fmt_int(qu['value'])} {qu['unit']} per month in the Work_Centres "
              "sheet? Nothing is saved until you confirm.",
              options=[{"label": f"Save {wc} capacity", "primary": True,
                        "action": {"type": "gap_submit", "gap": "rated_capacity", "values": {
                            "work_center": wc, "rated_capacity_units_per_month": qu["value"],
                            "unit_basis": "Bottles" if qu["unit"] == "bottles" else "Units"}}}])
        return
    if re.search(r"\bdraft\b", text.lower()) and s.get("last_replan"):
        return _draft(t, "replan request", s["last_replan"]["text"], replan=s["last_replan"])
    follow = _follow_up(t, {"label": "OUT_OF_SCOPE", "reason": "no scenario keyword"}, text, sl)
    if follow:
        return _advance(t, follow)
    t.say(T["OUT_OF_SCOPE"], template="OUT_OF_SCOPE", options=[
        {"label": o["label"], "action": {"type": "option", "slot": "scenario", "value": o["value"]}}
        for o in SCENARIO_OPTIONS])


def _follow_up(t, parse, text, slots=None):
    """'And at Atlanta?' after an S1 answer: reuse the last request, change what the message names."""
    last = t.s.get("last")
    if not last:
        return None
    raw = slots if slots is not None else nlu.parse("cases " + text).slots   # read slots without the gate
    keys = {"lcs", "week", "delta_cases", "pct", "work_center"}
    if not (keys & set(raw)):
        return None
    p = {"scenario": last["scenario"], "slots": {**last["slots"], **{k: raw[k] for k in keys & set(raw)}},
         "r": {k: v for k, v in last["r"].items() if k in ("material", "work_center", "selector", "label")},
         "awaiting": None, "questions": 0, "text": text}
    if "lcs" in raw:
        p["r"].pop("lc", None)
    if "week" in raw:
        p["r"].pop("calweek", None)
    if "work_center" in raw:
        p["r"]["work_center"] = raw["work_center"]
    t.span("follow_up", reused=last["scenario"], changed=sorted(keys & set(raw)))
    return p


def _on_option(t, req):
    s = t.s
    slot, value = req.get("slot"), req.get("value")
    if slot == "scenario":
        p = {"scenario": value, "slots": {}, "r": {}, "awaiting": None, "questions": 0, "text": ""}
        key, q = FIRST_QUESTION[value]
        if q is None:
            return _advance(t, p)
        return _ask(t, p, key, q)
    if slot == "open_gap":
        return
    p = s.get("pending")
    if not p:
        t.say("That question has expired. Ask again and I will pick it up from there.")
        return
    t.span("clarified", slot=slot, value=value, label=req.get("label"))
    if slot in ("pacing_national", "pacing_lc"):
        if value == "enter":
            p["awaiting"] = "pacing_pct"
            s["pending"] = p
            t.say(f"Enter the {'national' if p['r'].get('pacing_step') == 'national' else 'LC'} ordered % of lag-1 "
                  "forecast, for example 142.")
            return
        _save_pacing(t, p, p["r"].get("pacing_step"), value)
    else:
        p["r"][slot] = value
    p["awaiting"] = None
    _advance(t, p)


def _on_gap(t, req):
    s = t.s
    gap, v = req.get("gap"), req.get("values", {})
    if gap == "rated_capacity":
        cap = float(str(v.get("rated_capacity_units_per_month", "")).replace(",", ""))
        if cap <= 0:
            t.say("Rated capacity must be a positive number of units per month. Nothing was saved.", tone="bad")
            return
        sup.add(s, "Work_Centres", {"work_center": v["work_center"], "factory_site": v.get("factory_site"),
                                    "rated_capacity_units_per_month": cap, "unit_basis": v.get("unit_basis", "Bottles"),
                                    "allowed_sizes": v.get("allowed_sizes"), "valid_from": v.get("valid_from")},
                note=v.get("note"))
        t.say(render("SAVED_FACT", sheet="Work_Centres"), kind="system")
    elif gap == "pir_list":
        items = [m.strip() for m in re.split(r"[,\s]+", str(v.get("items", ""))) if m.strip()]
        known = set(adapter.kinaxis_plan().MATERIAL)
        bad = [m for m in items if m not in known]
        if bad or not items:
            t.say(f"These codes are not in the loaded Kinaxis plan: {', '.join(bad) or 'none given'}. Nothing was saved.",
                  tone="bad")
            return
        for m in items:
            sup.add(s, "PIR_List", {"item": m, "description": cx.item_desc(m)})
        t.say(render("SAVED_FACT", sheet="PIR_List"), kind="system")
    elif gap in ("pacing_national", "pacing_lc"):
        p = s.get("pending")
        val = v.get("value")
        _save_pacing(t, p, "national" if gap == "pacing_national" else "lc", val)
    elif gap == "cut_line":
        sup.add(s, "Cut_Lines", {**v, "cut_id": f"cl-{len(sup.live(s, 'Cut_Lines')) + 1:03d}"})
        t.say(render("SAVED_FACT", sheet="Cut_Lines"), kind="system")
    t.span("gap_saved", gap=gap)
    p = s.get("pending")
    if p and (p.get("awaiting") in (f"gap:{gap}", gap) or (gap.startswith("pacing") and p["scenario"] == "S4")):
        p["awaiting"] = None
        _advance(t, p)


def _on_decision(t, req):
    s = t.s
    d = s["drafts"].get(req.get("draft_id"))
    kind = req["type"]
    if not d or d["status"] != ("pending_alternate" if kind == "alternate" else "pending"):
        t.say("That draft is no longer open.")
        return
    if kind == "approve":
        d["status"] = "approved"
        rec = sup.add(s, "Approvals", {"approval_id": f"ap-{s['approvals'] + 1:04d}", "draft_id": d["draft_id"],
                                       "decision": "approved"})
        s["approvals"] += 1
        sup.add(s, "Draft_Actions", {"draft_id": d["draft_id"], "action_type": d["action_type"],
                                     "rendered_text": d["text"], "status": "approved"})
        t.say(render("APPROVED", action_type=d["action_type"][:1].upper() + d["action_type"][1:],
                     record_id=rec["approval_id"]), tone="good")
        t.span("approval", draft_id=d["draft_id"], decision="approved", record=rec["approval_id"])
    elif kind == "reject":
        d["status"] = "rejected"
        sup.add(s, "Approvals", {"approval_id": f"ap-{s['approvals'] + 1:04d}", "draft_id": d["draft_id"],
                                 "decision": "rejected"})
        alts = [c for c in sorted(ACTIVE) if c != d.get("code")]
        t.say(T["REJECTED"], options=[{"label": c, "action": {"type": "alternate", "draft_id": d["draft_id"], "code": c}}
                                      for c in alts], note="Alternate codes come from the active Planning RC list.")
        d["status"] = "pending_alternate"
        t.span("approval", draft_id=d["draft_id"], decision="rejected")
    elif kind == "alternate":
        code = req.get("code")
        d["status"] = "replaced"
        c = d.get("cut", {})
        t.span("alternate_code", draft_id=d["draft_id"], code=code)
        _draft(t, f"reason code {code}", f"Write SAP reason code {code} on the {c.get('cut_date')} cut of "
                                         f"{c.get('material')} at {c.get('lc')} (planner's alternate).", code=code, cut=c)


def _draft(t, action_type, text, **extra):
    s = t.s
    did = f"dr-{len(s['drafts']) + 1:04d}"
    s["drafts"][did] = {"draft_id": did, "action_type": action_type, "text": text, "status": "pending", **extra}
    t.say(render("ACTION_PENDING", action_type=action_type), template="ACTION_PENDING", note=text, options=[
        {"label": "Approve", "primary": True, "action": {"type": "approve", "draft_id": did}},
        {"label": "Reject", "action": {"type": "reject", "draft_id": did}}])
    t.span("draft_action", draft_id=did, action_type=action_type)
    return did


# ---------- clarification ----------
SLOT_WORDS = {"item": "the item", "material": "which item", "qty": "the quantity in cases", "calweek": "the week",
              "scope": "the LC", "lc": "the LC", "work_center": "the line", "pct": "the attainment %",
              "segment": "the segment or size", "growth": "the growth %", "growth_choice": "one growth value",
              "cut": "the item, LC and cut date", "pir_scope": "which items", "s5_mode": "which comparison"}


def _still_needed(p, slot):
    sl, r = p["slots"], p["r"]
    need = [SLOT_WORDS.get(slot, slot)]
    if p["scenario"] == "S1":
        if "delta_cases" not in sl and "pct" not in sl and slot != "qty":
            need.append(SLOT_WORDS["qty"])
        if "calweek" not in r and slot != "calweek":
            need.append(SLOT_WORDS["calweek"])
        if "lc" not in r and slot not in ("scope", "lc"):
            need.append(SLOT_WORDS["lc"])
    return ", ".join(need[:-1]) + (" and " if len(need) > 1 else "") + need[-1]


def _ask(t, p, slot, text, template=None, options=None, note=None, count=True):
    s = t.s
    if count and p["questions"] >= MAX_QUESTIONS:
        s["pending"] = None
        t.say(render("TOO_MANY_QUESTIONS", missing_list=_still_needed(p, slot)), template="TOO_MANY_QUESTIONS")
        t.span("clarify_cap")
        return
    if count:
        p["questions"] += 1
    p.setdefault("trace_ids", []).append(t.trace_id)
    p["awaiting"] = slot
    s["pending"] = p
    t.say(text, template=template, options=[{"label": o["label"], "primary": o.get("primary", False),
                                            "action": {"type": "option", "slot": slot, "value": o["value"],
                                                       "label": o["label"]}} for o in (options or [])], note=note)
    t.span("clarify", slot=slot, template=template, question=p["questions"])


def _fill_text(t, p, text):
    """A typed answer to the open question. Returns False when the text is not an answer to it,
    so the message is treated as a new request."""
    a, sl, r = p["awaiting"], p["slots"], p["r"]
    low = " ".join(text.lower().split())
    num = re.fullmatch(r"[+-]?\d[\d,]*(?:\.\d+)?\s*%?", low.strip())
    if a in ("item", "material"):
        if nlu.parse(text).label not in ("OUT_OF_SCOPE",) and len(low.split()) > 6:
            return False
        sl["item_text"] = nlu.find_item_text(low) or text.strip()
        r.pop("material", None)
        return True
    if a == "qty":
        d = nlu.find_delta(low) if nlu.find_delta(low) is not None else (int(float(low.replace(",", ""))) if num else None)
        if d is None:
            return False
        sl["delta_cases"] = d
        return True
    if a == "calweek":
        w = nlu.find_week(low) or ({"date": nlu.find_date(low)} if nlu.find_date(low) else None)
        if not w:
            return False
        sl["week"] = w
        return True
    if a in ("scope", "lc"):
        lcs = nlu.find_lcs(low)
        if len(lcs) == 1:
            r["lc"] = lcs[0]
            return True
        if a == "scope" and re.search(nlu.NETWORK, low):
            r["scope"] = "NETWORK"
            return True
        return False
    if a in ("pct", "growth"):
        v = nlu.find_pct(low)
        v = v if v is not None else (float(low.strip("% ").replace(",", "")) if num else None)
        if v is None:
            return False
        sl["pct"] = v
        return True
    if a == "work_center":
        wc = nlu.find_work_center(low)
        if wc:
            r["work_center"] = wc
            pct = nlu.find_pct(low)
            if pct is not None:
                sl["pct"] = pct
            return True
        f = nlu.find_factory(low)
        if f:
            sl["factory"] = f
            return True
        return False
    if a == "segment":
        size, pct = nlu.find_size(low), nlu.find_pct(low)
        if size is None:
            return False
        sl["size_oz"] = size
        if pct is not None:
            sl["pct"] = pct
        return True
    if a == "cut":
        ps = nlu.parse("code the cut " + text).slots
        for k in ("lcs", "date", "item_text"):
            if k in ps:
                sl[k] = ps[k]
        return bool({"lcs", "date"} & set(ps))
    if a in ("pacing_national", "pacing_lc", "pacing_pct"):
        step = p["r"].get("pacing_step")
        if re.fullmatch(r"y(es)?", low):
            val = True
        elif re.fullmatch(r"no?", low):
            val = False
        elif low == "skip":
            val = "skip"
        elif num:
            val = float(low.strip("% ").replace(",", ""))
        else:
            return False
        _save_pacing(t, p, step, val)
        return True
    if a == "growth_choice":
        if "both" in low:
            r["growth_choice"] = "both"
        elif nlu.find_pct(low) is not None or num:
            r["growth"] = nlu.find_pct(low) if nlu.find_pct(low) is not None else float(low.strip("% "))
            r["growth_list"] = [r["growth"]]
        else:
            return False
        return True
    if a == "pir_scope":
        if "lds" in low or "disinfect" in low:
            r["pir_scope"] = "Lysol LDS"
        elif "sanit" in low or "lls" in low:
            r["pir_scope"] = "Lysol Sanitizers"
        else:
            return False
        return True
    return False


def _save_pacing(t, p, step, val):
    if p is None:
        return
    r = p["r"]
    cut = r.get("cut_values", {})
    scope = "national" if step == "national" else cut.get("lc")
    if val == "skip" or val is None:
        r[f"skip_{step}"] = True
        t.span("pacing_skipped", scope=scope)
        return
    if isinstance(val, str):
        val = {"yes": True, "no": False}.get(val.lower(), val)
        if isinstance(val, str):
            val = float(val.replace("%", "").replace(",", ""))
    sup.add(t.s, "Pacing", {"item": cut.get("material"), "period": cut.get("period_key"), "scope": scope,
                            "ordered_pct_of_lag1_forecast": ("above 130%" if val is True else "not above 130%"
                                                             if val is False else val)})
    t.say(render("SAVED_FACT", sheet="Pacing"), kind="system")


# ---------- scenarios ----------
def _advance(t, p):
    return {"S1": _s1, "S2": _s2, "S3": _s3, "S4": _s4, "S5": _s5, "PIR": _pir}[p["scenario"]](t, p)


def _done(t, p, card):
    s = t.s
    card["trace_id"] = t.trace_id
    card["trace_ids"] = list(dict.fromkeys(p.get("trace_ids", []) + [t.trace_id]))
    t.cards.append(card)
    s["last_validator"] = card["validator"]
    t.span("narrative", engine=models.route()["orchestrator"], words=len((card.get("narrative") or "").split()))
    t.span("validator", passed=card["validator"]["passed"], unverified=card["validator"]["unverified"])
    if not card["validator"]["passed"]:
        t.say(T["VALIDATOR_FAIL"], template="VALIDATOR_FAIL", tone="bad")
    elif card.get("narrative"):
        t.messages.append({"kind": "answer", "text": card["narrative"], "card_id": card["id"],
                           "options": [], "note": None, "template": None, "tone": None})
    s["last"] = {"scenario": p["scenario"], "slots": p["slots"], "r": {k: v for k, v in p["r"].items()
                                                                        if isinstance(v, (str, int, float, dict, list))}}
    replan = card.get("replan")
    if replan:
        s["last_replan"] = replan
    action = p["slots"].get("out_of_scope_action")
    if action:
        offer = " I can draft a replan request for you to review instead." if replan else ""
        t.say(render("PARTIAL_SCOPE", scenario=f"{card['tag']} {card['title'].lower()}", action=action,
                     draft_offer=offer), template="PARTIAL_SCOPE",
              options=[{"label": "Draft replan request", "action": {"type": "draft", "kind": "replan_request"}}]
              if replan else [])
        t.span("partial_scope", declined=action)
    elif replan:
        t.say("Want a replan request drafted from this card? Nothing is sent; approving only records it.",
              options=[{"label": "Draft replan request", "action": {"type": "draft", "kind": "replan_request"}}])


def _new_card_id(t, p):
    if p.get("card_id"):
        return p["card_id"]
    p["card_id"] = f"c{t.s['seq']:04d}"
    t.s["cards"] = t.s.get("cards", 0) + 1
    return p["card_id"]


def _resolve_item(t, p, prompt="Which item? Give a GIC, UPC, GTIN or a description such as “4x90 Odor”."):
    """Sets p['r']['material'] or asks. Returns True when resolved."""
    sl, r = p["slots"], p["r"]
    if r.get("material"):
        return True
    txt = sl.get("item_text")
    if not txt:
        _ask(t, p, "item", prompt)
        return False
    res = t.tool("resolve_item", text=txt)
    v = res["values"]
    if res["status"] == "ok":
        r["material"] = v["chosen"]
        return True
    if res["status"] == "needs_input" and v.get("candidates"):
        opts = [{"label": f"{c['material']} · {c['description']}", "value": c["material"]} for c in v["candidates"]]
        _ask(t, p, "material", render("CLARIFY_ITEM", item_text=f"“{txt}”"), template="CLARIFY_ITEM",
             options=opts, note="Item codes only ever come from the item resolver.")
        return False
    t.s["pending"] = None
    t.say(render("NO_DATA_ITEM", item_text=txt), template="NO_DATA_ITEM",
          options=[{"label": "Try another code", "action": {"type": "focus"}}],
          note="A missing item is never guessed or approximated.")
    return False


def _s1(t, p):
    sl, r = p["slots"], p["r"]
    if not _resolve_item(t, p):
        return
    m = r["material"]
    desc = cx.item_desc(m)
    if sl.get("cover_query") and "delta_cases" not in sl and "pct" not in sl:
        locs = sl["lcs"] if sl.get("lcs") else ["NETWORK"] + list(cx.SITE_NAMES)
        t.echo(f"Weeks of cover for {m} {desc} · {', '.join(locs)} · planned basis · "
               f"{t.s['weeks_shown']} weeks")
        res = t.tool("get_plan", material=m, locations=locs, weeks=t.s["weeks_shown"])
        if res["status"] != "ok":
            t.say(render("NO_DATA_ITEM", item_text=m), template="NO_DATA_ITEM")
            t.s["pending"] = None
            return
        t.s["pending"] = None
        return _done(t, p, cards.s1_plan(_new_card_id(t, p), m, desc, res))
    if "delta_cases" not in sl and "pct" not in sl:
        return _ask(t, p, "qty", T["CLARIFY_QTY"])
    if "calweek" not in r:
        cw = cx.calweek_for(sl.get("week"))
        if cw is None:
            return _ask(t, p, "calweek", T["CLARIFY_WEEK"], template="CLARIFY_WEEK", options=cx.week_options())
        r["calweek"] = cw
    if "lc" not in r:
        lcs = sl.get("lcs", [])
        if r.get("scope") in cx.SITE_NAMES:
            r["lc"] = r["scope"]
        elif len(lcs) == 1:
            r["lc"] = lcs[0]
        elif sl.get("network") or r.get("scope") == "NETWORK":
            return _ask(t, p, "lc", T["CLARIFY_LC"], template="CLARIFY_SCOPE", options=LC_OPTIONS)
        else:
            return _ask(t, p, "scope", T["CLARIFY_SCOPE"], template="CLARIFY_SCOPE",
                        options=[{"label": "Network", "value": "NETWORK"}] + [{"label": lc, "value": lc} for lc in cx.SITE_NAMES])
    lc, cw = r["lc"], int(r["calweek"])
    if "delta_cases" in sl:
        change, args = f"{cards.fmt_signed(int(sl['delta_cases']))} cases", {"delta_cases": int(sl["delta_cases"])}
    else:   # a % of demand: the tool converts it to cases, never the model
        change, args = f"{float(sl['pct']):+g}% of total demand in that week", {"delta_pct": float(sl["pct"])}
    t.echo(f"Demand change for {m} {desc} · {change} · {lc} {cx.SITE_NAMES[lc]} · "
           f"week {cw} ({cx.week_label(cw)}) · planned basis")
    sim = t.tool("simulate_demand_change", material=m, scope=lc, week=cw, **args)
    if sim["status"] != "ok":
        t.s["pending"] = None
        t.say(render("NO_DATA_ITEM", item_text=m), template="NO_DATA_ITEM")
        return
    lanes = []
    if sim["values"]["verdict"] != "support":
        for src in cx.lane_sources(m, lc):
            ln = t.tool("lookup_lane", source=src, destination=lc, material=m)
            if ln["status"] == "ok":
                lanes.append(ln)
    t.s["pending"] = None
    _done(t, p, cards.s1(_new_card_id(t, p), m, desc, lc, cw, int(sim["values"]["delta_cases"]), sim, lanes))


def _s2(t, p):
    sl, r = p["slots"], p["r"]
    known = set(adapter.attainment().work_center.dropna())
    if "work_center" not in r and sl.get("line_name"):
        alias = next((x for x in reversed(sup.live(t.s, "Work_Centres"))
                      if str(x.get("alias") or "").strip().lower() == sl["line_name"].lower()), None)
        if alias:
            r["work_center"] = alias["work_center"]
        else:
            opts = []
            for w in cx.active_lines(sl.get("factory")):
                a = t.tool("get_attainment", work_center=w)
                pct = a["values"].get("trailing_avg_pct")
                opts.append({"label": f"{w} · trailing {pct}%" if pct is not None else w, "value": w})
            return _ask(t, p, "work_center", render("UNKNOWN_LINE", name=sl["line_name"]), template="CLARIFY_LINE",
                        options=opts, note="Line names map to work centres only through the alias table.")
    if "work_center" not in r:
        wc = sl.get("work_center")
        if wc and wc in known:
            r["work_center"] = wc
        else:
            lines = cx.active_lines(sl.get("factory"))
            if len(lines) == 1:
                r["work_center"] = lines[0]
            else:
                opts = []
                for w in lines:
                    a = t.tool("get_attainment", work_center=w)
                    pct = a["values"].get("trailing_avg_pct")
                    opts.append({"label": f"{w} · trailing {pct}%" if pct is not None else w, "value": w})
                site = cx.FACTORY_NAMES.get(sl.get("factory"), "That factory") if sl.get("factory") else "The network"
                return _ask(t, p, "work_center", render("CLARIFY_LINE", site=site), template="CLARIFY_LINE", options=opts,
                            note="Trailing attainment is actuals over fixed volume for the latest 3 months.")
    wc = r["work_center"]
    if "pct" not in sl and "pct" not in r:
        return _ask(t, p, "pct", render("CLARIFY_PCT", work_center=wc), template="CLARIFY_PCT",
                    options=[{"label": f"{v}%", "value": v} for v in (70, 80, 90)])
    pct = float(r.get("pct", sl.get("pct")))
    t.echo(f"Attainment change on {cx.wc_label(wc)} · {pct:g}% applied to planned and confirmed production · "
           f"network weeks of cover · {t.s['weeks_shown']} weeks")
    att = t.tool("get_attainment", work_center=wc)
    sim = t.tool("simulate_attainment", work_center=wc, attainment_pct=pct, horizon_weeks=t.s["weeks_shown"])
    t.s["pending"] = None
    if sim["status"] != "ok":
        t.say(f"No SKU on {wc} has a Kinaxis plan to apply the attainment to.")
        return
    _done(t, p, cards.s2(_new_card_id(t, p), wc, pct, att, sim))


def _s3(t, p):
    sl, r = p["slots"], p["r"]
    if "selector" not in r:
        size = r.get("segment") or sl.get("size_oz")
        if size is None:
            return _ask(t, p, "segment", T["CLARIFY_SEGMENT"], template="CLARIFY_SEGMENT",
                        options=[{"label": f"{s:g} oz laundry sanitizer", "value": s} for s in (90, 41, 21)])
        size = float(size)
        r["selector"] = {"consumer_size": int(size) if size.is_integer() else size}
        mats0 = select_materials(r["selector"]["consumer_size"])
        groups = sorted({g for g in (cx.item_group(m) for m in mats0) if g})
        brand = sl.get("brand") or (groups[0] if len(groups) == 1 else "Lysol")
        r["label"] = f"{size:g} oz {brand}"
    if sl.get("pct_range") and "growth" not in r and "growth_list" not in r:
        lo, hi = sl["pct_range"]
        choice = r.get("growth_choice")
        if choice is None:
            return _ask(t, p, "growth_choice", render("CLARIFY_RANGE", low=lo, high=hi), template="CLARIFY_RANGE",
                        options=[{"label": "Both ends", "value": "both"}, {"label": f"{lo:g}%", "value": "low"},
                                 {"label": f"{hi:g}%", "value": "high"}])
        r["growth_list"] = [lo, hi] if choice == "both" else [lo if choice == "low" else hi]
    if "pct" not in sl and "growth" not in r and "growth_list" not in r:
        return _ask(t, p, "growth", T["CLARIFY_GROWTH"], template="CLARIFY_GROWTH",
                    options=[{"label": f"+{v}%", "value": v} for v in (15, 25, 35)])
    growths = [float(g) for g in r.get("growth_list") or [r.get("growth", sl.get("pct"))]]
    growth = max(growths)
    months = int(sl.get("months") or 6)
    mats = select_materials(r["selector"].get("consumer_size"))
    wc = r.get("work_center") or sl.get("work_center") or cx.line_for_materials(mats)
    if not wc:
        t.s["pending"] = None
        t.say("None of these items has a factory line in the attainment history, so capacity cannot be checked.")
        return
    r["work_center"] = wc
    cap = sup.latest(t.s, "Work_Centres", work_center=wc)
    if not p.get("echoed"):
        t.echo(f"Capacity check for {r['label']} · growth "
               f"{' and '.join(cards.fmt_signed(g) + '%' for g in growths)} · {months} months from "
               f"the snapshot · line {wc} · attainment: trailing 3 months · cases to units with CASETOEACH")
        p["echoed"] = True
    att = t.tool("get_attainment", work_center=wc)
    if not cap:
        dem = t.tool("segment_demand", selector=r["selector"], growth_pct=growth, months=months)
        if dem["status"] != "ok":
            t.s["pending"] = None
            t.say("No demand found for that segment at the four LCs.")
            return
        card = cards.s3(_new_card_id(t, p), r["label"], growth, wc, att, demand_res=dem)
        _done(t, p, card)
        p["awaiting"] = "gap:rated_capacity"
        p.setdefault("trace_ids", []).append(t.trace_id)
        p["gap_context"] = {"work_center": wc, "factory_site": cx.wc_label(wc)}
        t.s["pending"] = p
        t.say(render("NO_DATA_CAPACITY", work_center=wc), template="NO_DATA_CAPACITY",
              options=[{"label": f"Enter capacity for {wc}", "primary": True,
                        "action": {"type": "open_gap", "gap": "rated_capacity"}}],
              note=f"Trailing 3-month attainment on {wc} is {att['values']['trailing_avg_pct']}%. "
                   "I will not enter a capacity for you.")
        return
    lanes, seen = [], set()
    for row in cx.make_buy_lanes(mats).sort_values("LEADTIME DAYS").itertuples():
        key = (row.SOURCE_SITE, row.DESTINATION_SITE)
        if key in seen:
            continue
        seen.add(key)
        ln = t.tool("lookup_lane", source=row.SOURCE_SITE, destination=row.DESTINATION_SITE, material=row.PART)
        if ln["status"] == "ok":
            lanes.append(ln)
        if len(lanes) == 3:
            break
    t.s["pending"] = None
    for i, g in enumerate(growths):     # one card per growth value (both ends of a range)
        gap = t.tool("capacity_gap", selector=r["selector"], growth_pct=g, months=months, work_center=wc,
                     rated_capacity_units=float(cap["rated_capacity_units_per_month"]))
        cid = _new_card_id(t, p) if i == 0 else f"{_new_card_id(t, p)}-{i + 1}"
        card = cards.s3(cid, r["label"], g, wc, att, gap_res=gap, lanes=lanes,
                        capacity={"value": float(cap["rated_capacity_units_per_month"]), "row": cap["row_id"]})
        _done(t, p, card)


def _period(y, m):
    first = date(y, m, 1)
    last = (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    return first, last


def _s4(t, p):
    sl, r = p["slots"], p["r"]
    if not (sl.get("date") or sl.get("code_cut") or r.get("cut") or p["awaiting"] == "cut" or r.get("material")) \
            and not sl.get("lcs"):
        return _s4_pareto(t, p)
    if "lc" not in r:
        lcs = sl.get("lcs", [])
        if len(lcs) == 1:
            r["lc"] = lcs[0]
    if "cut_date" not in r and sl.get("date"):
        r["cut_date"] = cx.resolve_year(*sl["date"]).isoformat()
    if "lc" not in r or "cut_date" not in r:
        return _ask(t, p, "cut", T["CLARIFY_CUT"])
    lc, cd = r["lc"], r["cut_date"]
    if not r.get("material"):
        if sl.get("item_text"):
            res = t.tool("resolve_item", text=sl["item_text"])
            cands = [c["material"] for c in res["values"].get("candidates", [])]
            if res["status"] != "ok" and cands:
                # Narrow the matches to the items actually cut at this LC on this day
                lst = t.tool("list_cut_lines", lc=lc, cut_date=cd, top_n=200)
                cut_mats = {row[0] for row in (lst.get("table") or {}).get("rows", [])}
                hit = [m for m in cands if m in cut_mats]
                if len(hit) == 1:
                    r["material"] = hit[0]
            if not r.get("material") and not _resolve_item(t, p):
                return
        else:
            lst = t.tool("list_cut_lines", lc=lc, cut_date=cd)
            if lst["status"] != "ok":
                t.s["pending"] = None
                t.say(render("NO_DATA_CUTS", period=f"{lc} on {cd}"), template="NO_DATA_CUTS")
                return
            rows = cards.rows_of(lst)
            if len(rows) == 1:
                r["material"] = rows[0]["material"]
            else:
                opts = [{"label": f"{x['material']} · {x['description']} · {cards.fmt_int(x['cases_cut'])} cut",
                         "value": x["material"]} for x in rows]
                return _ask(t, p, "material", f"I found {lst['values']['lines']} cut order lines at {lc} on {cd}. "
                            "Which item should I code? Largest first.", template="CLARIFY_ITEM", options=opts)
    m = r["material"]
    cutres = t.tool("get_cut_line", material=m, lc=lc, cut_date=cd)
    if cutres["status"] != "ok":
        planner = sup.latest(t.s, "Cut_Lines", item=m, lc=lc, cut_date=cd)
        if not planner:
            t.s["pending"] = None
            t.say(render("NO_DATA_CUTS", period=f"{m} at {lc} on {cd}"), template="NO_DATA_CUTS")
            return
        cv = {"cut_id": planner["cut_id"], "material": m, "lc": lc, "cut_date": cd,
              "cases_cut": planner.get("cases_cut"), "cases_ordered": planner.get("cases_ordered"),
              "description": cx.item_desc(m),
              "source": {"label": "Cut Lines sheet", "sub": "planner-entered", "planner": True}}
    else:
        cv = dict(cutres["values"])
        rc = cv.get("recorded_rc") or []
        cv["source"] = {"label": "PFR extract", "sub": f"Summary ZSD Ship · {cv['order_lines']} order line"
                                                       f"{'s' if cv['order_lines'] != 1 else ''}"
                                                       + (f" · recorded RC {rc[0]}" if rc else "")}
    d = date.fromisoformat(cd)
    cv["period"] = d.strftime("%B %Y")
    cv["period_key"] = d.strftime("%Y-%m")
    r["cut_values"] = {k: cv[k] for k in ("material", "lc", "cut_date", "period_key")}
    if not p.get("echoed"):
        t.echo(f"Walking the decision tree for the cut line {m} {cv.get('description') or ''} · {lc} · "
               f"cut date {cd}")
        p["echoed"] = True
    answers = {}
    for step, key, scope in (("national", "national_pct", "national"), ("lc", "lc_pct", lc)):
        row = sup.latest(t.s, "Pacing", item=m, period=cv["period_key"], scope=scope)
        if row:
            v = row["ordered_pct_of_lag1_forecast"]
            answers[key] = True if v == "above 130%" else False if v == "not above 130%" else float(v)
    tree = t.tool("walk_decision_tree", cut={"cut_id": cv["cut_id"], "material": m, "lc": lc, "cut_date": cd},
                  answers=answers)
    extra = [cutres] if cutres["status"] == "ok" else []
    card = cards.s4_tree(_new_card_id(t, p), cv, tree, extra)
    _done(t, p, card)
    needs = tree["values"].get("needs_input", [])
    for step, key, scope_word in (("national", "national_pct", "national"), ("lc", "lc_pct", f"{lc} ({cx.SITE_NAMES[lc]})")):
        if key in needs and not r.get(f"skip_{step}"):
            r["pacing_step"] = step
            gap = "pacing_national" if step == "national" else "pacing_lc"
            p["gap_context"] = {"item": m, "period": cv["period"], "lc": lc}
            return _ask(t, p, gap, render("NO_DATA_PACING", item=m, period=cv["period"], scope=scope_word),
                        template="NO_DATA_PACING", count=False,
                        options=[{"label": "Yes", "value": "yes"}, {"label": "No", "value": "no"},
                                 {"label": "Enter %", "value": "enter"}, {"label": "Skip", "value": "skip"}],
                        note="Skip leaves the rule unknown. I will not propose a code that depends on it.")
    t.s["pending"] = None
    code = tree["values"].get("proposed_code")
    if code:
        tv = tree["values"]
        if tv.get("recovery_week") is None:
            t.say(render("NO_DATA_RECOVERY", item=m, lc=lc, red_threshold=2, n=len(cx.cal())),
                  template="NO_DATA_RECOVERY")
        alt = f" Alternate: {tv['alternate_code']}." if tv.get("alternate_code") else ""
        _draft(t, f"reason code {code}", f"Assign SAP reason code {code} to the {cd} cut of {m} at {lc}.{alt}",
               code=code, cut=r["cut_values"])
    elif "inventory_snapshot" in needs:
        t.say(f"There is no SAP inventory snapshot within 3 days before {cd} at {lc}, so the stock branch cannot be "
              "walked. I will not propose a code.")


def _s4_pareto(t, p):
    sl = p["slots"]
    lo, hi = cx.pfr_period_bounds()
    if sl.get("month"):
        y, m = sl["month"]
        y = y or (hi.year if m <= hi.month else hi.year - 1)
    else:
        last_full = hi.replace(day=1) - timedelta(days=1) if hi.day < 28 else hi
        y, m = last_full.year, last_full.month
    a, b = _period(y, m)
    scope = sl.get("brand") or "both"
    label = f"{a.strftime('%B %Y')} {scope if scope != 'both' else 'Lysol LDS and Sanitizers'}"
    t.echo(f"PFR cuts by item · {scope if scope != 'both' else 'LDS and Sanitizers'} · {a} to {b} · "
           "official PFR on shipment date · top 10")
    res = t.tool("cuts_pareto", period_from=a.isoformat(), period_to=b.isoformat(), scope=scope, top_n=10)
    t.s["pending"] = None
    if res["status"] != "ok":
        t.say(render("NO_DATA_CUTS", period=label), template="NO_DATA_CUTS")
        return
    _done(t, p, cards.s4_pareto(_new_card_id(t, p), label, res))


def _s5(t, p):
    sl, r = p["slots"], p["r"]
    snaps = cx.snapshot_dates()
    pir = bool(sl.get("pir_items")) or r.get("s5_scope") == "pir"
    mode = r.get("s5_mode") or ("kinaxis" if len(snaps) >= 2 else None)
    if mode is None:
        return _ask(t, p, "s5_mode", render("NO_SECOND_SNAPSHOT", snapshot_week=cx.snapshot_label()),
                    template="NO_SECOND_SNAPSHOT",
                    options=[{"label": "Compare SAP stock between two dates", "value": "inventory"},
                             {"label": "Upload a second Kinaxis extract", "value": "upload"}])
    if mode == "upload":
        t.s["pending"] = {**p, "awaiting": "s5_mode"}
        t.say("A second Kinaxis extract is checked against the schema contract and loaded by the data build, then "
              "this view diffs the two snapshots.",
              options=[{"label": "Compare SAP stock between two dates",
                        "action": {"type": "option", "slot": "s5_mode", "value": "inventory"}}])
        return
    mats, scope_label = None, "Every item at the four LCs"
    if pir:
        rows = sup.live(t.s, "PIR_List")
        if not rows:
            p["awaiting"] = "gap:pir_list"
            p.setdefault("trace_ids", []).append(t.trace_id)
            t.s["pending"] = p
            t.say("Your PIR item list is empty. Add the items you review each week and I will continue.",
                  options=[{"label": "Add PIR items", "primary": True, "action": {"type": "open_gap", "gap": "pir_list"}}],
                  note="Saved to the PIR_List sheet with your name and the time.")
            return
        mats = list(dict.fromkeys(x["item"] for x in rows))
        scope_label = f"PIR items ({len(mats)})"
    if mode == "kinaxis":
        b = snaps[-1]
        a = snaps[-2]
        if sl.get("date"):                       # 'what changed since 10/12'
            want = cx.resolve_year(*sl["date"])
            earlier = [d for d in snaps[:-1] if d <= want]
            a = earlier[-1] if earlier else snaps[0]
        t.echo(f"What changed \u00b7 {scope_label} \u00b7 Kinaxis weekly loads {a} vs {b} \u00b7 same plan week in "
               "both \u00b7 ranked by risk, top 10")
        diff = t.tool("diff_snapshots", snapshot_a=a.isoformat(), snapshot_b=b.isoformat(), materials=mats,
                      mode="kinaxis")
        t.s["pending"] = {**p, "awaiting": "s5_mode", "r": {**r, "s5_scope": "pir" if pir else None}}
        _done(t, p, cards.s5_kinaxis(_new_card_id(t, p), scope_label, diff))
        t.say("Want the SAP stock-on-hand movement for the same week too?",
              options=[{"label": "Compare SAP stock between two dates",
                        "action": {"type": "option", "slot": "s5_mode", "value": "inventory"}}])
        return
    dates = cx.inventory_dates()
    b = dates[-1]
    a = b - timedelta(days=7)
    t.echo(f"What changed \u00b7 {scope_label} \u00b7 SAP stock on hand {a} vs {b} \u00b7 four LCs")
    diff = t.tool("diff_snapshots", snapshot_a=a.isoformat(), snapshot_b=b.isoformat(), materials=mats,
                  mode="inventory")
    if diff["status"] != "ok":
        t.s["pending"] = None
        t.say(render("NO_SECOND_SNAPSHOT", snapshot_week=cx.snapshot_label()), template="NO_SECOND_SNAPSHOT")
        return
    plans = [t.tool("get_plan", material=m, locations=["NETWORK"], weeks=1) for m in (mats or [])]
    t.s["pending"] = None
    p = {**p, "card_id": None}
    _done(t, p, cards.s5(_new_card_id(t, p), scope_label, diff, plans))


def _pir(t, p):
    sl, r = p["slots"], p["r"]
    mats, group = None, None
    item_text = sl.get("item_text")
    if item_text and sl.get("brand") and not re.search(r"\d", item_text):
        item_text = None                          # 'PIR for Lysol Sanitizers' names the group, not an item
    if item_text and not r.get("material"):
        if not _resolve_item(t, p):
            return
    if r.get("material"):
        mats = [r["material"]]
    elif r.get("pir_scope") == "PIR items" or (sl.get("pir_items") and sup.live(t.s, "PIR_List") and not sl.get("brand")):
        mats = list(dict.fromkeys(x["item"] for x in sup.live(t.s, "PIR_List")))
    elif r.get("pir_scope") or sl.get("brand"):
        group = r.get("pir_scope") or sl.get("brand")
    if not mats and not group:
        opts = [{"label": "Lysol Sanitizers", "value": "Lysol Sanitizers"}, {"label": "Lysol LDS", "value": "Lysol LDS"}]
        if sup.live(t.s, "PIR_List"):
            opts.append({"label": "My PIR items", "value": "PIR items"})
        return _ask(t, p, "pir_scope", "Which items should the report cover?", options=opts)
    weeks = t.s["weeks_shown"]
    label = group or (f"{mats[0]} {cx.item_desc(mats[0]) or ''}".strip() if len(mats) == 1 else f"{len(mats)} PIR items")
    t.echo(f"Projected inventory report · {label} · four LCs, network and group · {weeks} weeks · planned basis")
    res = t.tool("pir_report", materials=mats, bsg_group=group, horizon_weeks=weeks)
    t.s["pending"] = None
    if res["status"] != "ok":
        t.say("No Kinaxis plan rows for that selection.")
        return
    descs = {m: cx.item_desc(m) or "" for m in {row[2] for row in res["table"]["rows"]}}
    _done(t, p, cards.pir(_new_card_id(t, p), label, res, descs))


# ---------- Data Gaps panel ----------
def gaps(session: dict) -> list:
    p = session.get("pending") or {}
    aw = p.get("awaiting") or ""
    ctx = p.get("gap_context", {})
    wc_rows = sup.live(session, "Work_Centres")
    pace = sup.live(session, "Pacing")
    pir_rows = sup.live(session, "PIR_List")
    out = []
    wc = ctx.get("work_center") or (wc_rows[-1]["work_center"] if wc_rows else "PL11")
    out.append({"id": "rated_capacity", "title": "Rated line capacity", "sub": f"{wc} · S2, S3",
                "sheet": "Work_Centres",
                "state": "open" if aw == "gap:rated_capacity" else "entered" if sup.latest(session, "Work_Centres", work_center=wc) else "idle",
                "context": {"work_center": wc, "label": cx.wc_label(wc)} if wc else {},
                "entered": [f"{r['work_center']}: {cards.fmt_int(r['rated_capacity_units_per_month'])} {r['unit_basis'].lower()}/month"
                            for r in wc_rows]})
    try:
        n_pfr = len(cx.cuts.pfr_lines())
        pfr_txt = f"S4 · PFR extract · {n_pfr:,} order lines loaded"
    except Exception:
        pfr_txt = "S4 · Cut Lines sheet"
    out.append({"id": "cut_lines", "title": "Cut lines, last 12 months", "sub": pfr_txt, "sheet": "Cut_Lines",
                "state": "entered" if sup.live(session, "Cut_Lines") else "idle", "context": {}, "entered": []})
    for gid, title, scope in (("pacing_national", "Ordered % vs forecast, national", "national"),
                              ("pacing_lc", "Ordered % vs forecast, " + (ctx.get("lc") or "LC"), ctx.get("lc"))):
        if p.get("scenario") == "S4" and ctx.get("item"):
            done = any(x["item"] == ctx["item"] and x["scope"] == scope for x in pace)
            out.append({"id": gid, "title": title, "sub": f"{ctx['item']} · {ctx['period']} · "
                                                          f"{'R03' if scope == 'national' else 'R04'}",
                        "sheet": "Pacing", "state": "open" if aw == gid else "entered" if done else "needed",
                        "context": ctx, "entered": []})
    if not (p.get("scenario") == "S4" and ctx.get("item")):
        out.append({"id": "pacing", "title": "Ordered % vs forecast", "sub": "S4 · Pacing sheet", "sheet": "Pacing",
                    "state": "entered" if pace else "idle", "context": {},
                    "entered": [f"{x['item']} {x['period']} {x['scope']}: {x['ordered_pct_of_lag1_forecast']}" for x in pace]})
    out.append({"id": "pir_list", "title": "PIR item list", "sub": "S5 · PIR_List sheet", "sheet": "PIR_List",
                "state": "open" if aw == "gap:pir_list" else "entered" if pir_rows else "idle",
                "context": {"suggest": ["3283407", "3283419", "3313455", "3260319"]},
                "entered": [f"{x['item']} {x['description'] or ''}" for x in pir_rows]})
    out.append({"id": "kinaxis_snapshots", "title": "Kinaxis snapshots, last 2 weeks",
                "sub": f"S5 · 1 loaded ({cx.snapshot_label()})", "sheet": None, "state": "idle", "context": {},
                "entered": []})
    return out

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


PRONOUN = re.compile(r"^(?:it|this|that|this item|that item|this one|that one|same item|the same item|same|this sku|"
                     r"that sku|the item|that product|this product)$")
CONTINUE = re.compile(r"\b(instead|what about|how about|what if|and if|now|also|same|again|rerun|re-run|try|switch|"
                      r"change it|make it|use|still|on top)\b")
WHY = re.compile(r"^\s*(?:and\s+|but\s+|so\s+|ok\s+)?(?:why\b|explain\b|how come\b|what(?:'s| is) the reason|what caused|"
                 r"what drives|what's driving|what is driving)")
APPROVE = re.compile(r"^\s*(?:yes[,!. ]*)?(?:ok[,!. ]*)?(?:please\s+)?(?:approve|approved|go ahead|confirm|looks good)\b")
REJECT = re.compile(r"^\s*(?:no[,!. ]*)?(?:reject|decline|cancel (?:it|the draft))\b")
RERUN = re.compile(r"^\s*(?:please\s+)?(?:rerun|re-run|run it again|try again|run that again|refresh)\b")
UNSURE = re.compile(r"^\s*(?:not sure|don'?t know|no idea|unknown|skip|pass|n/?a)\b")


def _norm_tokens(x: str) -> set:
    """Words plus pack sizes as their own tokens ('4x90' and '4/90FZ' both give '4/90')."""
    x = x.lower()
    packs = {f"{a}/{b}" for a, b in re.findall(r"(\d+)\s*(?:x|/|by)\s*\(?(\d+)", x)}
    x = re.sub(r"(\d+)\s*(?:x|/|by)\s*\(?(\d+)\w*", " ", x)
    words = {w for w in re.split(r"[^a-z0-9.]+", x) if w and w not in {"the", "one", "a", "an", "of", "lysol", "us", "it", "that", "this"}}
    return words | packs


def _match_option(p, text):
    """A typed reply to a question with options: pick the option it names (code or words)."""
    offered = p.get("offered") or []
    if not offered:
        return None
    low = text.lower()
    exact = [o for o in offered if str(o["value"]).lower() in low.split() or str(o["value"]).lower() == low.strip()]
    if len(exact) == 1:
        return exact[0]
    words = _norm_tokens(text)
    if not words:
        return None
    scored = sorted(((len(words & _norm_tokens(o["label"])), i) for i, o in enumerate(offered)), reverse=True)
    if scored[0][0] > 0 and (len(scored) == 1 or scored[0][0] > scored[1][0]):
        return offered[scored[0][1]]
    return None


def _remember(t, p, card):
    """Conversation memory: what the planner is working on, for follow-ups and references."""
    s, r = t.s, p.get("r", {})
    ctx = s.setdefault("ctx", {})
    mat = r.get("material") or (card.get("top_items") or [None])[0] or card.get("focus_material")
    if mat:
        ctx["material"] = mat
    for k in ("lc", "calweek", "work_center"):
        if r.get(k) and r.get(k) != "NETWORK":
            ctx[k] = r[k]
    ctx["scenario"] = p["scenario"]
    s["last_answer"] = {"text": card.get("narrative"), "card_id": card["id"], "scenario": card["scenario"],
                        "title": card.get("title"), "why": card.get("why")}


def _merge_reply(p, slots):
    """Keep every detail a reply carries (quantity, week, LC...) even when it answers a different question."""
    sl, r = p["slots"], p["r"]
    keep = {k: v for k, v in slots.items() if k in ("delta_cases", "pct", "pct_range", "week", "window_weeks",
                                                     "horizon_weeks", "size_oz", "brand", "month", "months", "date",
                                                     "network", "factory", "work_center", "line_name")}
    sl.update(keep)
    if "week" in keep:
        r.pop("calweek", None)
    if len(slots.get("lcs", [])) == 1:
        r["lc"] = slots["lcs"][0]
    elif slots.get("network") and p["scenario"] == "S1":
        r["lc"] = "NETWORK"
    if slots.get("work_center"):
        r["work_center"] = slots["work_center"]
    return bool(keep) or bool(slots.get("lcs"))


def _continue_last(t, slots, text):
    """Rerun the last request with only what the new message changes (LC, quantity, week, window,
    horizon, attainment, growth, item). Returns the new pending request, or None."""
    last = t.s.get("last")
    if not last:
        return None
    sc = last["scenario"]
    p = {"scenario": sc, "slots": dict(last["slots"]), "r": {k: v for k, v in last["r"].items()
                                                              if k in ("material", "lc", "calweek", "work_center",
                                                                       "selector", "label", "pct", "growth",
                                                                       "s5_mode", "s5_scope", "pir_scope")},
         "awaiting": None, "questions": 0, "text": text}
    sl, r = p["slots"], p["r"]
    sl.pop("out_of_scope_action", None)
    changed = []
    item = (slots.get("item_text") or "").strip().lower()
    if item and not PRONOUN.match(item):
        sl["item_text"] = item
        r.pop("material", None)
        changed.append("item")
    if slots.get("lcs"):
        r["lc"] = slots["lcs"][0] if len(slots["lcs"]) == 1 else "NETWORK"
        r.pop("scope", None)
        changed.append("LC")
    elif slots.get("network") and sc == "S1":
        r["lc"] = "NETWORK"
        changed.append("LC")
    if slots.get("week"):
        sl["week"] = slots["week"]
        r.pop("calweek", None)
        changed.append("week")
    if "delta_cases" in slots:
        sl["delta_cases"] = slots["delta_cases"]
        sl.pop("pct", None) if sc == "S1" else None
        changed.append("quantity")
    if "pct" in slots:
        if sc == "S1":
            sl["pct"] = slots["pct"]
            sl.pop("delta_cases", None)
        elif sc == "S2":
            sl["pct"] = slots["pct"]
            r.pop("pct", None)
        elif sc == "S3":
            sl["pct"] = slots["pct"]
            for k in ("growth", "growth_list", "growth_choice"):
                r.pop(k, None)
        changed.append("percentage")
    for k in ("window_weeks", "horizon_weeks", "months", "month"):
        if slots.get(k) is not None:
            sl[k] = slots[k]
            changed.append(k.replace("_", " "))
    if slots.get("work_center"):
        r["work_center"] = slots["work_center"]
        changed.append("line")
    if slots.get("size_oz") and sc in ("S3", "PIR"):
        sl["size_oz"] = slots["size_oz"]
        r.pop("selector", None), r.pop("label", None)
        changed.append("segment")
    if slots.get("rerun"):
        changed.append("rerun")
    if not changed:
        return None
    t.span("follow_up", reused=sc, changed=changed)
    return p


def _explain(t, text):
    last = t.s.get("last_answer")
    if not last or not last.get("text"):
        return False
    why = last.get("why")
    t.say(("From the last answer: " + (why or last["text"])) + " The card shows the rows behind it.", kind="bot")
    t.span("explain_from_memory", card=last["card_id"])
    return True


def _open_draft(t):
    return next((d for d in reversed(list(t.s.get("drafts", {}).values())) if d["status"] == "pending"), None)


def _on_message(t, text, external=None):
    s = t.s
    low = " ".join(text.lower().split())
    p = s.get("pending")

    # 1. An answer to the open question: an offered option, then the expected value, keeping extra details
    if p and p.get("awaiting"):
        a = p["awaiting"]
        if a in ("pacing_national", "pacing_lc", "pacing_pct") and UNSURE.match(low):
            _save_pacing(t, p, p["r"].get("pacing_step"), "skip")
            p["awaiting"] = None
            return _advance(t, p)
        opt = _match_option(p, text)
        if opt is not None:
            t.span("follow_up", awaiting=a, matched_option=opt["label"])
            return _on_option(t, {"type": "option", "slot": a, "value": opt["value"], "label": opt["label"]})
        extra = nlu.parse("cases " + text).slots
        merged = _merge_reply(p, extra)
        looks_like_item = not merged or (a in ("item", "material") and extra.get("item_text") and
                                         extra.get("item_text") != low)
        if a not in ("item", "material") or looks_like_item:
            if _fill_text(t, p, text):
                t.span("follow_up", awaiting=a, text=text)
                p["awaiting"] = None
                return _advance(t, p)
        if merged:
            # The reply carried other details: keep them and ask only for what is still missing (once)
            kept = sorted(k for k in extra if k not in ("cover_query", "item_text", "code_cut"))
            t.span("follow_up", awaiting=a, kept=kept)
            p["kept_note"] = "Noted the " + ", ".join(k.replace("_", " ").replace("delta cases", "quantity").replace("lcs", "LC")
                                                     for k in kept) + "."
            p["awaiting"] = None
            p["questions"] = max(0, p["questions"] - 1)       # re-asking the same open question is not a new one
            return _advance(t, p)

    # 2. Conversation actions that need no new request
    d = _open_draft(t)
    if d and APPROVE.match(low):
        return _on_decision(t, {"type": "approve", "draft_id": d["draft_id"]})
    if d and REJECT.match(low):
        return _on_decision(t, {"type": "reject", "draft_id": d["draft_id"]})
    if re.search(r"\bdraft\b", low) and s.get("last_replan") and \
            re.search(r"\breplan|\bconstraint|\brequest\b|\bcall\b|\bit\b|\bthat\b", low):
        t.span("draft_request", text=text)
        return _draft(t, "replan request", s["last_replan"]["text"], replan=s["last_replan"])
    if RERUN.match(low):
        follow = _continue_last(t, {"rerun": True}, text)
        if follow:
            return _advance(t, follow)

    parse = models.scope_and_slots(text, external)
    t.span("scope_gate", engine=parse["engine"], label=parse["label"], confidence=parse["confidence"],
           reason=parse["reason"], model_ms=parse.get("ms"))
    t.span("slots", engine=parse["engine"], slots=parse["slots"])
    s["last_route"] = parse["engine"]
    sl = parse["slots"]
    rules = nlu.parse("cases " + text).slots          # second reader for follow-up details
    for k in ("window_weeks", "horizon_weeks"):
        if k not in sl and k in rules:
            sl[k] = rules[k]

    # 2b. A stated line capacity: offer to save it (nothing is saved until the planner confirms)
    if sl.get("work_center") and sl.get("quantity_units") and re.search(r"\bcapacity\b", low):
        return _follow_or_entry(t, parse, text)

    # 3. "Why...?" about the last answer
    if WHY.match(low) and not sl.get("item_text") and parse["label"] in ("OUT_OF_SCOPE", "FOLLOW_UP", "DATA_ENTRY",
                                                                          s.get("ctx", {}).get("scenario")):
        if _explain(t, text):
            return

    # 4a. "that item" / "it" in a follow-up: the item from memory, in the view the planner asks for
    item = (sl.get("item_text") or "").strip().lower()
    refers = (item and PRONOUN.match(item)) or re.search(r"\b(?:that|this|the same|same)\s+(?:item|sku|product|one)\b|\bfor it\b", low)
    if refers and s.get("ctx", {}).get("material") and parse["label"] in ("FOLLOW_UP", "DATA_ENTRY", "OUT_OF_SCOPE", "S1", "PIR"):
        view = ("PIR" if re.search(r"\bpir\b|projected inventory|inventory report", low) else
                "S1" if re.search(r"\bcover\b|\bwoc\b|\bstock\b|on hand|\bplan\b", low) else
                parse["label"] if parse["label"] in ("S1", "PIR") else None)
        if view:
            sl.pop("item_text", None)
            if view == "S1" and "delta_cases" not in sl and "pct" not in sl:
                sl["cover_query"] = True
            t.span("context", used="material from memory", material=s["ctx"]["material"], view=view)
            return _advance(t, {"scenario": view, "slots": sl, "r": {"material": s["ctx"]["material"]},
                                "awaiting": None, "questions": 0, "text": text})

    # 4. A continuation or a value for the last request
    if parse["label"] in ("FOLLOW_UP", "DATA_ENTRY") or \
            (parse["label"] == "OUT_OF_SCOPE" and parse["reason"] == "no scenario keyword"):
        if parse["label"] == "DATA_ENTRY" and sl.get("work_center") and sl.get("quantity_units"):
            return _follow_or_entry(t, parse, text)
        follow = _continue_last(t, sl, text)
        if follow:
            return _advance(t, follow)
        if parse["label"] != "OUT_OF_SCOPE":
            return _follow_or_entry(t, parse, text)
    if parse["label"] == "OUT_OF_SCOPE":
        s["pending"] = None
        t.say(T["OUT_OF_SCOPE"], template="OUT_OF_SCOPE", options=[
            {"label": o["label"], "action": {"type": "option", "slot": "scenario", "value": o["value"]}}
            for o in SCENARIO_OPTIONS])
        return

    # 5. A request in the same scenario that leaves out what the last one set: carry it over
    last = s.get("last")
    item = (sl.get("item_text") or "").strip().lower()
    if item and PRONOUN.match(item) or (not item and re.search(r"\b(?:that|this|same)\s+(?:item|sku|product|one)\b", low)):
        if s.get("ctx", {}).get("material"):
            sl.pop("item_text", None)
            p = {"scenario": parse["label"], "slots": sl, "r": {"material": s["ctx"]["material"]}, "awaiting": None,
                 "questions": 0, "text": text}
            t.span("context", used="material from memory", material=s["ctx"]["material"])
            return _advance(t, p)
    # Extra demand asked right after an attainment run ("can it still support 3000 more cases?") rides on it
    if last and last["scenario"] == "S2" and parse["label"] == "S1" and CONTINUE.search(low):
        follow = _continue_last(t, sl, text)
        if follow:
            return _advance(t, follow)
    if last and last["scenario"] == parse["label"] and CONTINUE.search(low):
        follow = _continue_last(t, sl, text)
        if follow:
            return _advance(t, follow)
    p = {"scenario": parse["label"], "slots": sl, "r": {}, "awaiting": None, "questions": 0, "text": text}
    return _advance(t, p)


def _follow_or_entry(t, parse, text):
    """A value with no open question: offer to save a stated line capacity."""
    s, sl = t.s, parse["slots"]
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
    t.say(T["OUT_OF_SCOPE"], template="OUT_OF_SCOPE", options=[
        {"label": o["label"], "action": {"type": "option", "slot": "scenario", "value": o["value"]}}
        for o in SCENARIO_OPTIONS])


def _follow_up(t, parse, text, slots=None):
    return _continue_last(t, slots if slots is not None else nlu.parse('cases ' + text).slots, text)


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
    p["offered"] = [{"label": o["label"], "value": o["value"]} for o in (options or [])]
    if p.get("kept_note"):
        text = f"{p.pop('kept_note')} {text}"
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
    return {"S1": _s1, "S2": _s2, "S3": _s3, "S4": _s4, "S5": _s5, "PIR": _pir, "DASH": _dash}[p["scenario"]](t, p)


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
    _remember(t, p, card)
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


def _cover_params(t, sl):
    window = int(sl.get("window_weeks") or 6)
    horizon = int(sl.get("horizon_weeks") or t.s["weeks_shown"])
    return max(2, min(window, 13)), max(4, min(horizon, 26))


def _s1(t, p):
    sl, r = p["slots"], p["r"]
    if not sl.get("item_text") and not r.get("material") and (sl.get("size_oz") or sl.get("brand")) \
            and "delta_cases" not in sl and "pct" not in sl:
        p["scenario"] = "PIR"                        # cover for a segment or brand, not one item
        t.span("rerouted", to="PIR", reason="segment cover without an item")
        return _pir(t, p)
    if not _resolve_item(t, p):
        return
    m = r["material"]
    desc = cx.item_desc(m)
    window, horizon = _cover_params(t, sl)
    if sl.get("cover_query") and "delta_cases" not in sl and "pct" not in sl:
        locs = sl["lcs"] if sl.get("lcs") else ["NETWORK"] + list(cx.SITE_NAMES)
        t.echo(f"Weeks of cover for {m} {desc} · {', '.join(locs)} · planned basis · {horizon} weeks shown · "
               f"{window}-week forward demand")
        res = t.tool("get_plan", material=m, locations=locs, weeks=horizon, window_weeks=window)
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
        else:
            # No LC named: spread the change across the LCs by their demand that week (whole network)
            r["lc"] = "NETWORK"
    lc, cw = r["lc"], int(r["calweek"])
    if "delta_cases" in sl:
        change, args = f"{cards.fmt_signed(int(sl['delta_cases']))} cases", {"delta_cases": int(sl["delta_cases"])}
    else:   # a % of demand: the tool converts it to cases, never the model
        change, args = f"{float(sl['pct']):+g}% of total demand in that week", {"delta_pct": float(sl["pct"])}
    where = "whole network, split by each LC's demand that week" if lc == "NETWORK" else f"{lc} {cx.SITE_NAMES[lc]}"
    t.echo(f"Demand change for {m} {desc} · {change} · {where} · week {cw} ({cx.week_label(cw)}) · "
           f"on hand, in transit, firm and planned orders at all four LCs · {window}-week cover · {horizon} weeks")
    res = t.tool("assess_demand_change", material=m, lc=lc, week=cw, window_weeks=window, horizon_weeks=horizon, **args)
    t.s["pending"] = None
    if res["status"] != "ok":
        t.say(render("NO_DATA_ITEM", item_text=m), template="NO_DATA_ITEM")
        return
    _done(t, p, cards.s1_assess(_new_card_id(t, p), m, desc, res))


def _s2(t, p):
    sl, r = p["slots"], p["r"]
    known = set(adapter.attainment().work_center.dropna())
    # An item named with the attainment change: its line comes from the attainment history, and
    # any extra cases ride on the same run (workflow: "can a +35k increase still be supported?")
    if sl.get("item_text") and not r.get("material"):
        if not _resolve_item(t, p):
            return
    if r.get("material") and "work_center" not in r and not sl.get("work_center"):
        from osb_tools.attainment import lines_for_sku
        active = set(cx.active_lines())               # lines with a plan in the latest 3 months
        lines = [w for w in lines_for_sku(r["material"], since=202605) if w in active]
        if len(lines) == 1:
            r["work_center"] = lines[0]
        elif lines:
            return _ask(t, p, "work_center", render("CLARIFY_LINE", site=r["material"]), template="CLARIFY_LINE",
                        options=[{"label": w, "value": w} for w in lines],
                        note=f"{r['material']} runs on more than one active line.")
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
    if sl.get("pct_reduction") is not None and "pct" not in r:
        n = float(sl["pct_reduction"])
        return _ask(t, p, "pct", f"Do you mean {wc} runs at {n:g}% attainment, or drops by {n:g} points to {100 - n:g}%?",
                    template="CLARIFY_PCT", options=[{"label": f"Run at {n:g}%", "value": n},
                                                     {"label": f"Run at {100 - n:g}%", "value": 100 - n}])
    if "pct" not in sl and "pct" not in r:
        return _ask(t, p, "pct", render("CLARIFY_PCT", work_center=wc), template="CLARIFY_PCT",
                    options=[{"label": f"{v}%", "value": v} for v in (70, 80, 90)])
    pct = float(r.get("pct", sl.get("pct")))
    window, horizon = _cover_params(t, sl)
    extra, extra_txt = None, ""
    if r.get("material") and sl.get("delta_cases"):
        cw = cx.calweek_for(sl.get("week")) or int(cx.cal().CALWEEK.iloc[0])
        extra = {"material": r["material"], "week": cw, "delta_cases": int(sl["delta_cases"])}
        extra_txt = f" · plus {cards.fmt_signed(int(sl['delta_cases']))} cases of {r['material']} in week {cw}"
    t.echo(f"Attainment change on {cx.wc_label(wc)} · {pct:g}% applied to planned and confirmed production{extra_txt} · "
           f"network weeks of cover · {horizon} weeks")
    att = t.tool("get_attainment", work_center=wc)
    sim = t.tool("simulate_attainment", work_center=wc, attainment_pct=pct, horizon_weeks=horizon,
                 **({"extra_demand": extra} if extra else {}))
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
    window, _ = _cover_params(t, sl)
    month_from = f"{sl['month'][0] or cx.snapshot_date().year}-{int(sl['month'][1]):02d}" if sl.get("month") else None
    cover_months = int(sl.get("months") or (1 if month_from else 3))
    months_label = (f"{cover_months} month{'s' if cover_months > 1 else ''} from {month_from}" if month_from
                    else f"next {cover_months} months")
    mats = select_materials(r["selector"].get("consumer_size"))
    wc = r.get("work_center") or sl.get("work_center") or cx.line_for_materials(mats)
    if not wc:
        t.s["pending"] = None
        t.say("None of these items has a factory line in the attainment history, so capacity cannot be checked.")
        return
    r["work_center"] = wc
    t.echo(f"Segment growth for {r['label']} · {' and '.join(cards.fmt_signed(g) + '%' for g in growths)} · "
           f"{months_label} · inventory and cover at all four LCs, then line {wc} capacity · cases to units with CASETOEACH")
    att = t.tool("get_attainment", work_center=wc)
    cap = sup.latest(t.s, "Work_Centres", work_center=wc)
    plan_res = None
    if cap:
        basis, cap_value = "rated", float(cap["rated_capacity_units_per_month"])
    else:
        plan_res = t.tool("line_plan_capacity", work_center=wc)
        basis, cap_value = "plan", float(plan_res["values"].get("avg_fixed_units_per_month") or 0)
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
        cover = t.tool("segment_growth_cover", selector=r["selector"], growth_pct=g, month_from=month_from,
                       months=cover_months, window_weeks=window)
        gap = t.tool("capacity_gap", selector=r["selector"], growth_pct=g, months=int(sl.get("months") or 6),
                     work_center=wc, rated_capacity_units=cap_value)
        if cover["status"] != "ok" or gap["status"] != "ok":
            t.say("No demand found for that segment at the four LCs.")
            return
        cid = _new_card_id(t, p) if i == 0 else f"{_new_card_id(t, p)}-{i + 1}"
        _done(t, p, cards.s3_full(cid, r["label"], g, months_label, wc, att, cover, gap, basis, cap_value, plan_res, lanes))
    if basis == "plan":
        t.say(f"Rated capacity for {wc} is not in the extracts, so I used its average fixed plan as the reference. "
              "Enter the rated capacity if you have it and I will rerun.",
              options=[{"label": f"Enter rated capacity for {wc}", "action": {"type": "open_gap", "gap": "rated_capacity"}}])


def _dash(t, p):
    window, _ = _cover_params(t, p["slots"])
    t.echo(f"Network dashboard · every segment and LC · first plan week · {window}-week cover · factory lines")
    res = t.tool("network_dashboard", window_weeks=window)
    t.s["pending"] = None
    _done(t, p, cards.dashboard(_new_card_id(t, p), res))


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
    elif r.get("skip_national") or r.get("skip_lc"):
        skipped = " and ".join(x for x, k in (("national", "skip_national"), (f"{lc}", "skip_lc")) if r.get(k))
        t.say(f"Without the {skipped} ordered % of forecast, rules R03/R04 stay unknown, so I won't propose a code. "
              "Enter it in Data Gaps when you have it and the tree will continue.")
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
    if sl.get("size_oz") and not r.get("material") and not r.get("pir_scope"):
        size = float(sl["size_oz"])
        mats = [m for m in select_materials(int(size) if size.is_integer() else size)
                if adapter.kinaxis_plan().pipe(lambda d: d[(d.MATERIAL == m) & d.LOCATION.isin(config.LCS)].TOTAL_DEMAND.sum()) > 0]
        r["pir_label"] = f"{size:g} oz {sl.get('brand') or 'segment'}"
    if mats:
        pass
    elif r.get("material"):
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
    window, weeks = _cover_params(t, sl)
    label = r.get("pir_label") or group or (f"{mats[0]} {cx.item_desc(mats[0]) or ''}".strip() if len(mats) == 1 else f"{len(mats)} PIR items")
    t.echo(f"Projected inventory report · {label} · four LCs, network and group · {weeks} weeks · {window}-week cover · planned basis")
    res = t.tool("pir_report", materials=mats, bsg_group=group, horizon_weeks=weeks, window_weeks=window)
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
    snaps = cx.snapshot_dates()
    out.append({"id": "kinaxis_snapshots", "title": "Kinaxis weekly snapshots",
                "sub": (f"S5 · {len(snaps)} weekly loads, {snaps[0]:%m/%d} to {snaps[-1]:%m/%d}" if snaps
                        else f"S5 · 1 loaded ({cx.snapshot_label()})"),
                "sheet": None, "state": "entered" if len(snaps) >= 2 else "idle", "context": {}, "entered": []})
    return out

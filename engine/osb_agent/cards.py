"""Card builders. A card is what the planner sees on the canvas: typed blocks the UI renders,
a short narrative and the sources behind every number.

The narrative is drafted here from tool values only (the drafting step the PRD gives to Sonnet),
then checked by the numeric validator like any model draft would be."""
from collections import defaultdict

from . import context as cx
from .validator import validate

TITLES = {
    "DASH": "Network dashboard",
    "S1": "In-month demand spike",
    "S1P": "Weeks of cover",
    "S2": "Factory attainment drop",
    "S3": "Segment growth beyond line capacity",
    "S4": "PFR root cause and reason coding",
    "S4P": "PFR cuts by item",
    "S5": "What changed this week",
    "PIR": "Projected inventory report",
}
TAG = {"S1P": "S1", "S4P": "S4", "DASH": "NET"}

COLOUR_WORD = {"red": "red", "yellow": "yellow", "green": "green", "blue": "blue", "n/a": "no cover"}
VERDICT = {"do_not_support": "Do not support without a replan.",
           "support_with_replan": "Supportable, but the factory must replan.",
           "support": "Supportable from planned stock."}


def fmt_int(v) -> str:
    return f"{int(round(v)):,}"


def fmt_signed(v) -> str:
    return f"{'+' if v > 0 else ''}{int(round(v)):,}"


def rows_of(result) -> list:
    t = result.get("table") or {}
    return [dict(zip(t.get("columns", []), r)) for r in t.get("rows", [])]


# ---------- sources ----------
def _ranges(nums) -> str:
    nums = sorted(set(int(n) for n in nums))
    out, start, prev = [], None, None
    for n in nums:
        if start is None:
            start = prev = n
        elif n == prev + 1:
            prev = n
        else:
            out.append(f"{start}" if start == prev else f"{start}–{prev}")
            start = prev = n
    if start is not None:
        out.append(f"{start}" if start == prev else f"{start}–{prev}")
    if len(out) > 8:
        out = out[:8] + ["…"]
    return ", ".join(out)


def sources(results) -> list:
    groups = defaultdict(lambda: {"rows": [], "count": 0})
    for r in results:
        for u in r.get("rows_used", []):
            key = (u.get("source_file"), u.get("source_sheet"), u.get("hash"))
            g = groups[key]
            if "source_row" in u:
                g["rows"].append(u["source_row"])
                g["count"] += 1
            elif "rows" in u:
                g["count"] += int(u["rows"])
    out = []
    for (f, sh, h), g in groups.items():
        out.append({"file": f, "sheet": sh, "hash": h, "rows": _ranges(g["rows"]) if g["rows"] else None,
                    "count": g["count"]})
    return out


def finish(card: dict, results: list, narrative: str = None) -> dict:
    card["sources"] = sources(results)
    card["result_ids"] = [r["result_id"] for r in results]
    card["tag"] = TAG.get(card["scenario"], card["scenario"])
    card["title"] = TITLES[card["scenario"]]
    if narrative:
        bad = validate(narrative, results)
        card["validator"] = {"passed": not bad, "unverified": bad}
        card["narrative"] = None if bad else narrative
    else:
        card["validator"] = {"passed": True, "unverified": [], "note": "No narrative drafted."}
        card["narrative"] = None
    return card


# ---------- S1 ----------
def s1(card_id, material, desc, lc, week, delta, sim, lanes) -> dict:
    v = sim["values"]
    rows = rows_of(sim)
    weeks = sorted({r["calweek"] for r in rows})
    by_loc = defaultdict(dict)
    for r in rows:
        by_loc[r["location"]][r["calweek"]] = r
    order = [x for x in cx.SITE_NAMES if x in by_loc] + ["NETWORK"]
    table_rows = []
    for loc in order:
        cells = by_loc.get(loc, {})
        table_rows.append({
            "loc": loc, "name": "US network" if loc == "NETWORK" else cx.SITE_NAMES[loc],
            "changed": loc in (lc, "NETWORK"),
            "cells": [{"before": c.get("woc_before"), "after": c.get("woc_after"), "cb": c.get("colour_before"),
                       "ca": c.get("colour_after")} for c in (cells.get(w, {}) for w in weeks)]})
    tgt = [by_loc[lc][w] for w in weeks if w in by_loc[lc]]
    net = [by_loc["NETWORK"][w] for w in weeks if w in by_loc["NETWORK"]]
    red_weeks = sum(1 for r in tgt if r["colour_after"] == "red")
    tone = {"do_not_support": "bad", "support_with_replan": "warn", "support": "good"}[v["verdict"]]
    lane_txt = []
    for ln in lanes:
        lv = ln["values"]
        if lv.get("leadtime_days") is not None:
            s = f"{lv['source']} to {lv['destination']} in {lv['leadtime_days']} days"
            if lv.get("cost_per_load") is not None:
                s += f", {lv['mode'].lower()} at {fmt_int(lv['cost_per_load'])} USD per load"
            lane_txt.append(s)
    blocks = [
        {"type": "kv", "items": [
            {"label": "Item", "value": material, "sub": desc},
            {"label": "Change", "value": f"{fmt_signed(delta)} cases",
             "sub": (f"{v['delta_pct']:+g}% of {fmt_int(v['base_demand_cases'])} cases demand \u00b7 " if v.get("delta_pct") is not None else "")
                    + f"week {week} \u00b7 {cx.week_label(week)}"},
            {"label": "Lands at", "value": lc, "sub": cx.SITE_NAMES[lc]},
            {"label": "Basis", "value": "Planned", "sub": "6-week forward demand"}]},
        {"type": "banner", "tone": tone, "title": VERDICT[v["verdict"]],
         "text": (f"{lc} is red in {red_weeks} of the {len(weeks)} weeks shown after the change."
                  if red_weeks else f"{lc} stays out of red in the {len(weeks)} weeks shown.")},
        {"type": "wocgrid", "title": "Weeks of cover, before → after",
         "weeks": [{"calweek": w, "label": cx.week_label(w)} for w in weeks], "rows": table_rows,
         "note": "WoC = projected stock in the week / average total demand of the next 6 weeks. "
                 "Colour on the 1-decimal value: under 2 red, 2 to 3.9 yellow, 4 to 5.0 green, above 5.0 blue."},
    ]
    if lane_txt:
        blocks.append({"type": "note", "tone": "info", "title": "Inbound lanes to " + lc,
                       "text": "; ".join(lane_txt) + ". A shuttle or STO is a planner decision; I do not create one."})
    narrative = None
    if tgt and net:
        a0, n0 = tgt[0], net[0]
        narrative = (f"{VERDICT[v['verdict']]} {fmt_signed(delta)} cases of {material} at {lc} in week {week} take {lc} "
                     f"from {a0['woc_before']} to {a0['woc_after']} weeks of cover ({COLOUR_WORD[a0['colour_before']]} to "
                     f"{COLOUR_WORD[a0['colour_after']]}). The network moves from {n0['woc_before']} to "
                     f"{n0['woc_after']} weeks.")
        if lane_txt:
            narrative += f" Fastest lane in: {lane_txt[0]}."
    card = {"id": card_id, "scenario": "S1", "status": "ok",
            "subtitle": f"{material} {desc or ''} \u00b7 {fmt_signed(delta)} cases at {lc} \u00b7 week {week}",
            "blocks": blocks}
    if v["verdict"] != "support" and tgt:
        reds = [str(r["calweek"]) for r in tgt if r["colour_after"] == "red"]
        card["replan"] = {"scenario": "S1", "text": (
            f"Replan request: {fmt_signed(delta)} cases of {material} {desc or ''} at {lc} in week {week}. "
            f"{lc} weeks of cover goes from {tgt[0]['woc_before']} to {tgt[0]['woc_after']}"
            + (f" and is red in weeks {', '.join(reds)}" if reds else "") + ". "
            + (f"Fastest inbound lane: {lane_txt[0]}. " if lane_txt else "")
            + "Please confirm whether the plan can be replanned to cover this.")}
    return finish(card, [sim] + lanes, narrative)


def s1_plan(card_id, material, desc, plan_res) -> dict:
    rows = rows_of(plan_res)
    locs = list(dict.fromkeys(r["location"] for r in rows))
    weeks = sorted({r["calweek"] for r in rows})
    by = {(r["location"], r["calweek"]): r for r in rows}
    grid = [{"loc": loc, "name": "US network" if loc == "NETWORK" else cx.SITE_NAMES.get(loc, loc), "changed": False,
             "cells": [{"after": by.get((loc, w), {}).get("woc"), "ca": by.get((loc, w), {}).get("colour")} for w in weeks]}
            for loc in locs]
    first = rows[0] if rows else None
    narrative = (f"{material} at {first['location']} has {first['woc']} weeks of cover in week {first['calweek']} "
                 f"({COLOUR_WORD.get(first['colour'], first['colour'])}).") if first and first["woc"] is not None else None
    card = {"id": card_id, "scenario": "S1P", "status": "ok",
            "subtitle": f"{material} {desc or ''} · planned basis · snapshot {cx.snapshot_label()}",
            "blocks": [{"type": "wocgrid", "title": "Weeks of cover by week", "single": True,
                        "weeks": [{"calweek": w, "label": cx.week_label(w)} for w in weeks], "rows": grid,
                        "note": "WoC = projected stock in the week / average total demand of the next 6 weeks."},
                       {"type": "table", "title": "Projected stock and demand, cases",
                        "columns": [{"key": "location", "label": "LC"}, {"key": "calweek", "label": "Week"},
                                    {"key": "stock_planned", "label": "Projected stock", "format": "int"},
                                    {"key": "total_demand", "label": "Total demand", "format": "int"},
                                    {"key": "total_supply", "label": "Total supply", "format": "int"},
                                    {"key": "woc", "label": "WoC", "format": "woc"}],
                        "rows": rows, "collapsed": True}]}
    return finish(card, [plan_res], narrative)


# ---------- S2 ----------
def s2(card_id, wc, pct, att_res, sim) -> dict:
    av, sv = att_res["values"], sim["values"]
    months = [r for r in rows_of(att_res)]
    window = set(av["window"])
    m_rows = [{"year_month": r["year_month"], "fix_cases": r["fix_cases"], "actual_cases": r["actual_cases"],
               "attainment_pct": r["attainment_pct"], "in_window": r["year_month"] in window,
               "excluded": r["fix_cases"] == 0} for r in months]
    srows = rows_of(sim)
    weeks = sorted({r["calweek"] for r in srows})
    by = defaultdict(dict)
    for r in srows:
        by[r["material"]][r["calweek"]] = r
    descs = {m: cx.item_desc(m) for m in by}
    heat = [{"loc": m, "name": descs.get(m) or "", "changed": m in sv["skus_below_floor"],
             "cells": [{"before": by[m].get(w, {}).get("woc_before"), "after": by[m].get(w, {}).get("woc_after"),
                        "cb": by[m].get(w, {}).get("colour_before"), "ca": by[m].get(w, {}).get("colour_after")}
                       for w in weeks]} for m in by]
    below = sv["skus_below_floor"]
    tone = "bad" if below else "good"
    blocks = [
        {"type": "kv", "items": [
            {"label": "Work centre", "value": wc, "sub": cx.wc_label(wc).split(" · ", 1)[1]},
            {"label": "Applied attainment", "value": f"{pct:g}%", "sub": "to planned and confirmed production"},
            {"label": "Trailing 3 months", "value": f"{av['trailing_avg_pct']}%",
             "sub": f"{fmt_int(av['actual_cases_window'])} of {fmt_int(av['fix_cases_window'])} fixed cases"},
            {"label": "Production lost", "value": f"{fmt_int(sv['production_shortfall_cases'])} cases",
             "sub": f"next {len(weeks)} weeks, all SKUs on the line"}]},
        {"type": "banner", "tone": tone,
         "title": (f"{len(below)} SKU{'s' if len(below) != 1 else ''} fall into red cover at {pct:g}% attainment"
                   if below else f"No SKU on {wc} falls into red cover at {pct:g}% attainment"),
         "text": ", ".join(f"{m} {descs.get(m) or ''}" for m in below) if below else
         "Network cover stays at or above 2 weeks for every SKU on the line."},
        {"type": "table", "title": f"Monthly attainment on {wc}",
         "columns": [{"key": "year_month", "label": "Month"}, {"key": "fix_cases", "label": "Fixed, cases", "format": "int"},
                     {"key": "actual_cases", "label": "Actual, cases", "format": "int"},
                     {"key": "attainment_pct", "label": "Attainment", "format": "pct0"}],
         "rows": m_rows,
         "note": "Attainment = sum of actuals / sum of fixed volume per month, aggregated before dividing. "
                 "Months with no fixed volume are excluded. Shaded rows make up the trailing 3-month window."},
        {"type": "wocgrid", "title": "Network weeks of cover by SKU, before → after", "row_label": "SKU",
         "weeks": [{"calweek": w, "label": cx.week_label(w)} for w in weeks], "rows": heat,
         "note": "Shortfall = production in the week × (1 − attainment), taken off network stock from that week on."},
    ]
    for w in sim.get("warnings", []):
        blocks.append({"type": "note", "tone": "muted", "text": w})
    narrative = (f"At {pct:g}% attainment {wc} loses {fmt_int(sv['production_shortfall_cases'])} cases of production "
                 f"over the weeks shown. Trailing 3-month attainment is {av['trailing_avg_pct']}%. ")
    narrative += (f"SKUs that turn red: {', '.join(below)}." if below else "No SKU turns red at network level.")
    ex = sv.get("extra_demand")
    if ex:
        m, wk = str(ex["material"]), int(ex["week"])
        r0 = by.get(m, {}).get(wk)
        if r0:
            narrative += (f" With {fmt_signed(ex['delta_cases'])} cases of {m} in week {wk} on top, {m} goes from "
                          f"{r0['woc_before']} to {r0['woc_after']} weeks of network cover that week "
                          f"({COLOUR_WORD.get(r0['colour_after'], '')}).")
    card = {"id": card_id, "scenario": "S2", "status": "ok",
            "subtitle": f"{cx.wc_label(wc)} \u00b7 {pct:g}% attainment \u00b7 network level", "blocks": blocks}
    if below:
        card["replan"] = {"scenario": "S2", "text": (
            f"Constraint call / replan request for {wc}: at {pct:g}% attainment the line loses "
            f"{fmt_int(sv['production_shortfall_cases'])} cases of production over the next {len(weeks)} weeks and "
            f"{', '.join(below)} fall below 2 weeks of network cover. Please confirm the replan for these SKUs.")}
    return finish(card, [att_res, sim], narrative)


# ---------- S3 ----------
def s3(card_id, label, growth, wc, att_res, demand_res=None, gap_res=None, capacity=None, lanes=None) -> dict:
    av = att_res["values"]
    res = [att_res]
    blocks = []
    if gap_res is None:
        drows = rows_of(demand_res)
        res.append(demand_res)
        blocks.append({"type": "banner", "tone": "warn", "title": "Capacity gap not computed: rated line capacity is missing",
                       "text": f"Demand is shown below. Capacity and gap stay blank until you enter units per month for {wc}."})
        rows = [{"month": r["month"], "demand_cases": r["demand_cases"], "demand_units": r["demand_units"],
                 "grown_units": r["grown_units"], "effective_capacity_units": None, "gap_units": None} for r in drows]
        months = [r["month"] for r in drows]
    else:
        res.append(gap_res)
        gv = gap_res["values"]
        rows = rows_of(gap_res)
        months = [r["month"] for r in rows]
        first = gv["first_constrained_month"]
        if first:
            r0 = next(r for r in rows if r["month"] == first)
            blocks.append({"type": "banner", "tone": "bad", "title": f"Short from {first}: {fmt_int(r0['gap_units'])} units",
                           "text": f"Effective capacity on {wc} is {fmt_int(gv['effective_capacity_units'])} units per month "
                                   f"against {fmt_int(r0['grown_units'])} units of demand at {fmt_signed(growth)}%."})
        else:
            blocks.append({"type": "banner", "tone": "good", "title": "Demand fits within effective capacity",
                           "text": f"Effective capacity on {wc} is {fmt_int(gv['effective_capacity_units'])} units per month."})
    blocks.insert(0, {"type": "kv", "items": [
        {"label": "Segment", "value": label, "sub": "all four LCs"},
        {"label": "Growth", "value": f"{fmt_signed(growth)}%", "sub": f"{months[0]} to {months[-1]}" if months else ""},
        {"label": "Line", "value": wc, "sub": cx.wc_label(wc).split(" · ", 1)[1]},
        {"label": "Rated capacity", "value": (f"{fmt_int(capacity['value'])} units/mo" if capacity else "needs input"),
         "sub": ("planner-entered" if capacity else "not in any extract"), "badge": "planner" if capacity else "gap"}]})
    blocks.append({"type": "table", "title": "Demand against effective capacity, units",
                   "columns": [{"key": "month", "label": "Month"},
                               {"key": "demand_cases", "label": "Cases", "format": "int"},
                               {"key": "demand_units", "label": "Units", "format": "int"},
                               {"key": "grown_units", "label": f"Units at {fmt_signed(growth)}%", "format": "int", "strong": True},
                               {"key": "effective_capacity_units", "label": "Effective capacity", "format": "int_or_input"},
                               {"key": "gap_units", "label": "Gap", "format": "gap"}],
                   "rows": rows,
                   "note": (f"Effective capacity = rated capacity (units per month) × trailing 3-month attainment, "
                            f"{av['trailing_avg_pct']}% for {wc} ({fmt_int(av['actual_cases_window'])} of "
                            f"{fmt_int(av['fix_cases_window'])} fixed volume, {av['window'][0]} to {av['window'][-1]}). "
                            f"Units = cases × case-to-each from the SAP Item Master.")})
    lane_rows = []
    for ln in lanes or []:
        lv = ln["values"]
        lane_rows.append({"source": lv["source"], "destination": lv["destination"], "type": lv.get("type"),
                          "leadtime_days": lv.get("leadtime_days"), "min_qty": lv.get("min_qty")})
        res.append(ln)
    blocks.append({"type": "table" if lane_rows else "note", "title": "Allocation across sources", "tone": "dashed",
                   "columns": [{"key": "source", "label": "Source"}, {"key": "destination", "label": "To"},
                               {"key": "type", "label": "Lane"}, {"key": "leadtime_days", "label": "Lead time, days", "format": "int"},
                               {"key": "min_qty", "label": "Minimum, cases", "format": "int"}],
                   "rows": lane_rows,
                   "text": "Runs after the gap is computed. Candidate sources come from the items' Make and Buy lanes.",
                   "note": ("Candidate sources come from the items' Make and Buy lanes, ranked by lead time. "
                            "Cost shows as not provided: no cost per unit is on file for these lanes.")})
    narrative = None
    if gap_res is not None:
        gv = gap_res["values"]
        if gv["first_constrained_month"]:
            r0 = next(r for r in rows if r["month"] == gv["first_constrained_month"])
            narrative = (f"{label} at {fmt_signed(growth)}% does not fit on {wc} from {gv['first_constrained_month']}. "
                         f"Demand reaches {fmt_int(r0['grown_units'])} units against {fmt_int(gv['effective_capacity_units'])} "
                         f"units of effective capacity, a gap of {fmt_int(r0['gap_units'])} units.")
        else:
            narrative = f"{label} at {fmt_signed(growth)}% fits within {fmt_int(gv['effective_capacity_units'])} units of effective capacity on {wc}."
    card = {"id": card_id, "scenario": "S3", "status": "ok" if gap_res else "needs_input",
            "subtitle": f"{label}, all four LCs · {fmt_signed(growth)}% growth · demand is total demand from the "
                        f"Kinaxis snapshot, converted to units", "blocks": blocks}
    return finish(card, res, narrative)


# ---------- S4 tree ----------
RULE_TEXT = {
    "R01": "Customer order rectification or change at customer request",
    "R02a": "Force majeure for the cut date",
    "R02b": "Obsolete or discontinued, with no forecast for the next 2 months",
    "R02d": "Functional or management decision",
    "R03": "National ordered % of lag-1 forecast above 130%, cut month",
    "R04": "Cutting LC ordered % of lag-1 forecast above 130%",
    "R05": "Stock on hand at the cutting LC on the cut date",
    "R06": "ATP = 0 and factory attainment under 90% in the cut month or the month before",
    "R07": "ATP = 0 and inbound transfer not received",
    "R08": "ATP = 0 and blocked or quality-inspection stock",
    "R09": "ATP = 0, bought-in item with supplier delay",
    "R10": "ATP = 0 and stock at another LC while the cutting LC is short",
    "R11": "ATP = 0 and line cancelled after entry by internal staff",
    "R12": "ATP > 0 and allocation or reservation quota",
    "R13": "ATP > 0 and line cancelled after entry by internal staff",
    "R14": "ATP > 0 and expiry, wrong location or substitute unavailable",
    "R99": "No rule passed",
}


def _evidence(p, cut, lc) -> str:
    e = p.get("evidence")
    rid = p["rule_id"]
    if rid == "R01":
        return "Cut line · order_issue_flag " + ("set" if p["outcome"] == "pass" else "is empty")
    if rid in ("R02a", "R02d"):
        return "Planner flag " + ("set" if p["outcome"] == "pass" else "not set")
    if rid == "R02b" and e:
        st = ", ".join(e["MATERIALSTATUS"]) or "blank"
        return (f"Item Master · MATERIALSTATUS {st}. Kinaxis · forecast, next two months: "
                f"{fmt_int(e['forecast_next_9_weeks'] or 0)} cases")
    if rid in ("R03", "R04"):
        scope = "national" if rid == "R03" else lc
        if p["outcome"] == "unknown":
            return f"Pacing sheet has no row for {cut['material']}, {cut['period']}, {scope}"
        v = list(e.values())[0] if e else None
        ans = ("above 130%" if v is True else "not above 130%" if v is False else f"{v:g}% of lag-1 forecast")
        return f"Pacing sheet · planner-entered: {ans}"
    if rid == "R05":
        if not e:
            return "SAP Inventory · no snapshot within 3 days before the cut date"
        same = "" if e["snapshot_date"] == cut["cut_date"] else f" (nearest snapshot, cut on {cut['cut_date']})"
        return (f"SAP Inventory · {lc} · {e['snapshot_date']}{same}: ATP {fmt_int(e['ATP_QUANTITY'])}, allocation "
                f"{fmt_int(e['ALLOCATION_QTY'])}, ATP after allocation {fmt_int(e['ATP_AFTER_ALLOCATION'])}")
    if rid == "R06":
        if not e:
            return "Attainment · no line found for this item in the cut month or the month before"
        return "; ".join(f"Attainment · {x['work_center']} · {x['year_month']}: {x['attainment_pct']:g}%" for x in e)
    if rid == "R07" and e:
        return f"SAP Inventory · in transit {fmt_int(e['INTRANSIT_QTY'])} cases"
    if rid == "R08" and e:
        return f"SAP Inventory · blocked {fmt_int(e['BLOCKED_INV'])}, quality inspection {fmt_int(e['QUAL_INSPEC'])}"
    if rid == "R12" and e:
        return f"SAP Inventory · allocation {fmt_int(e['ALLOCATION_QTY'])}"
    return "Planner flag " + ("set" if p["outcome"] == "pass" else "not set")


def s4_tree(card_id, cut: dict, tree: dict, extra=()) -> dict:
    v = tree["values"]
    lc = cut["lc"]
    rows = []
    for p in v["path"]:
        if p["rule_id"] == "R99":
            continue
        res = p["outcome"]
        label = {"pass": "Passes", "fail": "Not flagged", "unknown": "Unknown"}[res]
        if p["rule_id"] == "R02b" and res == "fail":
            label = "Not obsolete"
        if p["rule_id"] == "R05":
            label = "ATP > 0" if res == "pass" else "ATP = 0"
            res = "branch"
        if p["rule_id"] in ("R03", "R04") and res == "fail":
            label = "Not above 130%"
        rows.append({"rule": p["rule_id"], "test": RULE_TEXT.get(p["rule_id"], p["test"]), "result": res,
                     "result_label": label, "evidence": _evidence(p, cut, lc)})
    code = v["proposed_code"]
    if code:
        notes = []
        if v.get("alternate_code"):
            notes.append(f"Alternate: {v['alternate_code']} (next rule that passes, {v['alternate_rule']})")
        if v.get("recovery_week"):
            notes.append(f"Recovery: {lc} cover back to 2 weeks or more in week {v['recovery_week']} "
                         f"({cx.week_label(v['recovery_week'])})")
        banner = {"type": "banner", "tone": "good", "title": f"Proposed code {code}: {v['long_description'] or ''}".strip(),
                  "text": " \u00b7 ".join(notes + [f"Confidence {v['confidence']}. Approving records the code; "
                                                    "nothing is written to SAP."])}
    else:
        unknown = [r for r in rows if r["result"] == "unknown"]
        banner = {"type": "banner", "tone": "warn",
                  "title": f"No code proposed yet: {len(unknown)} input{'s' if len(unknown) != 1 else ''} unknown",
                  "text": ("R03 and R04 come before the stock rules and need ordered % of lag-1 forecast, which is not in "
                           "the extracts. Answer in the chat or enter it in the Pacing sheet.")
                  if any(r["rule"] in ("R03", "R04") for r in unknown) else
                  "The tree stopped on an input the extracts do not hold. Answer in the chat to continue."}
    src = cut.get("source", {})
    blocks = [
        {"type": "kv", "items": [
            {"label": "Item", "value": cut["material"], "sub": cut.get("description")},
            {"label": "Cutting LC", "value": lc, "sub": cx.SITE_NAMES.get(lc)},
            {"label": "Cut date", "value": cut["cut_date"], "sub": f"{fmt_int(cut['cases_cut'])} cases cut of "
                                                                   f"{fmt_int(cut['cases_ordered'])} ordered"
             if cut.get("cases_cut") is not None else None},
            {"label": "Source", "value": src.get("label", "Cut line"), "sub": src.get("sub"),
             "badge": "planner" if src.get("planner") else None}]},
        banner,
        {"type": "path", "title": "Decision-tree path", "rows": rows,
         "note": ("Rules run in order and the first rule that passes sets the code. Rules after the stop are not "
                  "evaluated. Proposed codes come from the active Planning RC list only.")},
    ]
    narrative = None
    if code:
        decisive = next((p for p in v["path"] if p["outcome"] == "pass" and p["rule_id"] != "R05"), None)
        narrative = f"Proposed code {code} for the {cut['cut_date']} cut of {cut['material']} at {lc}. "
        if decisive:
            narrative += f"{decisive['rule_id']} decides it: {_evidence(decisive, cut, lc)}."
        if v.get("alternate_code"):
            narrative += f" Alternate code: {v['alternate_code']}."
        if v.get("recovery_week"):
            narrative += f" {lc} cover is back to 2 weeks or more in week {v['recovery_week']}."
    card = {"id": card_id, "scenario": "S4", "status": "ok" if code else "needs_input",
            "subtitle": f"{cut['material']} {cut.get('description') or ''} · {lc} · cut {cut['cut_date']}",
            "blocks": blocks, "proposed_code": code}
    return finish(card, [tree, *extra], narrative)


# ---------- S4 pareto ----------
def s4_pareto(card_id, label, res) -> dict:
    v = res["values"]
    rows = rows_of(res)
    top = rows[0] if rows else None
    blocks = [
        {"type": "kv", "items": [
            {"label": "PFR", "value": f"{v['pfr_pct']}%", "sub": v["pfr_basis"]},
            {"label": "Cases cut", "value": fmt_int(v["total_cut"]), "sub": "PFR cuts"},
            {"label": "Cases ordered", "value": fmt_int(v["total_ordered"]), "sub": f"{v['period_from']} to {v['period_to']}"},
            {"label": "Scope", "value": v["scope"] if v["scope"] != "both" else "LDS and Sanitizers", "sub": "Summary ZSD Ship"}]},
        {"type": "table", "title": "Top cutting items",
         "columns": [{"key": "material", "label": "Item"}, {"key": "description", "label": "Description"},
                     {"key": "cases_cut", "label": "Cases cut", "format": "int"},
                     {"key": "cases_ordered", "label": "Ordered", "format": "int"},
                     {"key": "share", "label": "Share of cuts", "format": "share"},
                     {"key": "cumulative_share", "label": "Cumulative", "format": "pct1"},
                     {"key": "top_reason_code", "label": "Top RC", "format": "code"}],
         "rows": rows,
         "note": "PFR = 1 − PFR cuts / cases ordered. NonPFR cuts (for example order rectification) are excluded."},
    ]
    narrative = (f"{label} PFR is {v['pfr_pct']}%: {fmt_int(v['total_cut'])} cases cut of {fmt_int(v['total_ordered'])} ordered.")
    if top:
        narrative += (f" The largest cutter is {top['material']} {top['description']} with {fmt_int(top['cases_cut'])} cases, "
                      f"{top['share']}% of cuts.")
    card = {"id": card_id, "scenario": "S4P", "status": "ok", "subtitle": f"{label} · {v['period_from']} to {v['period_to']}",
            "blocks": blocks, "top_items": [r["material"] for r in rows[:3]]}
    return finish(card, [res], narrative)


# ---------- S5 ----------
def s5(card_id, scope_label, diff, plans) -> dict:
    v = diff["values"]
    rows = rows_of(diff)
    rows.sort(key=lambda r: abs(r["delta_cases"]), reverse=True)
    rec = v.get("reconciliation", [])
    now = {}
    for p in plans:
        pr = rows_of(p)
        if pr:
            now[p["values"]["material"]] = pr[0]
    items = []
    for m in dict.fromkeys(r["material"] for r in rows):
        items.append({"material": m, "description": cx.item_desc(m) or "",
                      "woc": now.get(m, {}).get("woc"), "colour": now.get(m, {}).get("colour")})
    blocks = [
        {"type": "kv", "items": [
            {"label": "Compared", "value": f"{v['date_a']} → {v['date_b']}", "sub": "SAP stock on hand"},
            {"label": "Scope", "value": scope_label, "sub": "four LCs"},
            {"label": "Demand drivers", "value": "unavailable", "sub": "one Kinaxis snapshot loaded"},
            {"label": "Attainment drivers", "value": "unavailable", "sub": "inventory fallback"}]},
        {"type": "banner", "tone": "info", "title": "Inventory fallback: stock moves only",
         "text": f"Only the {cx.snapshot_label()} Kinaxis snapshot is loaded, so WoC movements cannot be split into demand, "
                 "supply and attainment drivers. Upload a second Kinaxis extract to see them."},
        {"type": "table", "title": "On-hand change by item and LC, cases",
         "columns": [{"key": "material", "label": "Item"}, {"key": "location", "label": "LC"},
                     {"key": "onhand_a", "label": v["date_a"], "format": "int"},
                     {"key": "onhand_b", "label": v["date_b"], "format": "int"},
                     {"key": "delta_cases", "label": "Change", "format": "signed"}],
         "rows": rows[:24], "note": "Sorted by size of move. Largest 24 rows shown." if len(rows) > 24 else None},
    ]
    if now:
        blocks.append({"type": "table", "title": f"Network weeks of cover now, week of {cx.snapshot_date().month}/{cx.snapshot_date().day}",
                       "columns": [{"key": "material", "label": "Item"}, {"key": "description", "label": "Description"},
                                   {"key": "woc", "label": "Network WoC", "format": "woc"}],
                       "rows": items})
    if rec:
        blocks.append({"type": "table", "title": "Demand does not reconcile with its components", "tone": "warn",
                       "columns": [{"key": "material", "label": "Item"}, {"key": "forecast", "label": "8-week forecast", "format": "int"},
                                   {"key": "total_demand", "label": "8-week total demand", "format": "int"}],
                       "rows": rec,
                       "note": "Forecast and total demand differ by more than 25% over the next 8 weeks. Confirm the demand components with Rushi."})
    biggest = rows[0] if rows else None
    narrative = None
    if biggest:
        narrative = (f"Between {v['date_a']} and {v['date_b']} the largest stock move is {biggest['material']} at "
                     f"{biggest['location']}: {fmt_signed(biggest['delta_cases'])} cases. Demand and attainment drivers are "
                     "unavailable with one Kinaxis snapshot.")
        if rec:
            r = rec[0]
            narrative += (f" {r['material']} has {fmt_int(r['forecast'])} cases of forecast against "
                          f"{fmt_int(r['total_demand'])} cases of total demand in the same weeks.")
    card = {"id": card_id, "scenario": "S5", "status": "ok", "subtitle": f"{scope_label} · SAP stock {v['date_a']} to {v['date_b']}",
            "blocks": blocks}
    return finish(card, [diff] + plans, narrative)


def s5_kinaxis(card_id, scope_label, diff) -> dict:
    v = diff["values"]
    rows = rows_of(diff)
    for r in rows:
        r["description"] = cx.item_desc(r["material"]) or ""
    reds = [r for r in rows if r["colour_b"] == "red" and r["colour_a"] != "red"]
    wk = v.get("calweek")
    blocks = [
        {"type": "kv", "items": [
            {"label": "Compared", "value": f"{v['date_a']} \u2192 {v['date_b']}", "sub": "Kinaxis weekly loads"},
            {"label": "Plan week", "value": str(wk) if wk else "\u2013", "sub": cx.week_label(wk) if wk else ""},
            {"label": "Moves found", "value": fmt_int(v["changes_total"]), "sub": "colour change or 1 week or more"},
            {"label": "Scope", "value": scope_label, "sub": "LCs and network"}]},
        {"type": "banner", "tone": "bad" if reds else "info",
         "title": (f"{len(reds)} of the top {len(rows)} moves turned red" if reds else
                   "Top moves this week" if rows else "No WoC colour changes or moves over 1 week"),
         "text": "Ranked by risk: red first, then lowest weeks of cover. Each move shows its main driver."},
        {"type": "table", "title": "Weekly digest, ranked by risk",
         "columns": [{"key": "rank", "label": "#"}, {"key": "material", "label": "Item"},
                     {"key": "description", "label": "Description"}, {"key": "location", "label": "LC"},
                     {"key": "woc_a", "label": f"WoC {v['date_a'][5:]}", "format": "woc", "colour_key": "colour_a"},
                     {"key": "woc_b", "label": f"WoC {v['date_b'][5:]}", "format": "woc", "colour_key": "colour_b"},
                     {"key": "driver", "label": "Main driver"},
                     {"key": "stock_a", "label": "Stock before", "format": "int"},
                     {"key": "stock_b", "label": "Stock after", "format": "int"},
                     {"key": "demand_b", "label": "Fwd demand", "format": "int"}],
         "rows": rows,
         "note": "WoC for the same plan week in both loads. Driver: the larger of the stock effect (projected stock "
                 "changed, demand held) and the demand effect (6-week forward demand changed, stock held). Cases."},
    ]
    rec = v.get("reconciliation", [])
    if rec:
        blocks.append({"type": "table", "title": "Demand does not reconcile with its components", "tone": "warn",
                       "columns": [{"key": "material", "label": "Item"}, {"key": "forecast", "label": "8-week forecast", "format": "int"},
                                   {"key": "total_demand", "label": "8-week total demand", "format": "int"}],
                       "rows": rec[:10],
                       "note": "Forecast and total demand differ by more than 25% over the next 8 weeks in the newer load. "
                               "Confirm the demand components with Rushi."})
    narrative = None
    if rows:
        top = rows[0]
        narrative = (f"Between the {v['date_a']} and {v['date_b']} Kinaxis loads, {fmt_int(v['changes_total'])} item and LC "
                     f"positions moved for week {wk}. Top risk: {top['material']} at {top['location']}, from "
                     f"{top['woc_a']} to {top['woc_b']} weeks of cover ({top['driver']}).")
    why = " ".join(f"{r['material']} at {r['location']}: {r['woc_a']} to {r['woc_b']} weeks, {r['driver']} "
                   f"(stock {fmt_int(r['stock_a'])} to {fmt_int(r['stock_b'])} cases, forward demand "
                   f"{fmt_int(r['demand_a'])} to {fmt_int(r['demand_b'])})." for r in rows[:3]) or None
    card = {"id": card_id, "scenario": "S5", "status": "ok", "why": why, "top_items": [r["material"] for r in rows[:3]],
            "subtitle": f"{scope_label} \u00b7 Kinaxis {v['date_a']} vs {v['date_b']} \u00b7 week {wk}", "blocks": blocks}
    return finish(card, [diff], narrative)


# ---------- PIR ----------
def pir(card_id, label, res, materials_desc) -> dict:
    rows = rows_of(res)
    weeks = sorted({r["calweek"] for r in rows})
    keyed = defaultdict(dict)
    meta = {}
    for r in rows:
        k = (r["level"], r["material"], r["location"])
        keyed[k][r["calweek"]] = r
        meta[k] = r
    order = {"GROUP": 0, "NETWORK": 1, "LC": 2}
    # Items with no network cover in any week have no demand in the horizon: hide them, say how many
    quiet = {k[1] for k in keyed if k[0] == "NETWORK" and all(r.get("woc") is None for r in keyed[k].values())}
    keys = sorted((k for k in keyed if k[1] not in quiet or k[0] == "GROUP"),
                  key=lambda k: (k[1] if k[0] != "GROUP" else "", order[k[0]], k[2]))
    grid = []
    for k in keys:
        level, m, loc = k
        name = (f"{meta[k]['group']} total" if level == "GROUP" else
                f"{m} {materials_desc.get(m, '')}" if level == "NETWORK" else cx.SITE_NAMES.get(loc, loc))
        grid.append({"loc": "GROUP" if level == "GROUP" else ("NETWORK" if level == "NETWORK" else loc), "name": name,
                     "level": level, "material": m,
                     "cells": [{"after": keyed[k].get(w, {}).get("woc"), "ca": keyed[k].get(w, {}).get("colour"),
                                "demand": keyed[k].get(w, {}).get("total_demand"),
                                "supply": keyed[k].get(w, {}).get("available_supply"),
                                "balance": keyed[k].get(w, {}).get("balance")} for w in weeks]})
    reds = [r for r in rows if r["level"] == "LC" and r["colour"] == "red" and r["calweek"] == weeks[0]] if weeks else []
    blocks = [{"type": "pir", "title": "Projected inventory", "weeks": [{"calweek": w, "label": cx.week_label(w)} for w in weeks],
               "rows": grid,
               "note": (f"{len(quiet)} item{'s' if len(quiet) != 1 else ''} with no demand in these weeks hidden. " if quiet else "")
                       + "Balance = Kinaxis projected stock. WoC = balance / average total demand of the next 6 weeks. "
                       "Network = sum of the four LCs; group rows sum the items and recompute WoC."}]
    narrative = f"Projected inventory for {label} from the Kinaxis snapshot, planned basis."
    if reds:
        narrative += " Red in the first week: " + "; ".join(f"{r['material']} at {r['location']} ({r['woc']} weeks)" for r in reds[:3]) + "."
    card = {"id": card_id, "scenario": "PIR", "status": "ok", "subtitle": f"{label} · {len(weeks)} weeks · planned basis",
            "blocks": blocks, "export": {"filename": f"PIR_{label.replace(' ', '_')}.xlsx", "rows": rows}}
    return finish(card, [res], narrative)


# ---------- S1, end to end (workflow doc, Scenario 1) ----------
def s1_assess(card_id, material, desc, res) -> dict:
    v = res["values"]
    lc, week = v["lc"], v["week"]
    rows = rows_of(res)
    weeks = sorted({r["calweek"] for r in rows})
    by = defaultdict(dict)
    for r in rows:
        by[r["location"]][r["calweek"]] = r
    order = [x for x in cx.SITE_NAMES if x in by] + ["NETWORK"]
    grid = [{"loc": loc, "name": "US network" if loc == "NETWORK" else cx.SITE_NAMES[loc], "changed": loc in (lc, "NETWORK"),
             "cells": [{"before": by[loc].get(w, {}).get("woc_before"), "after": by[loc].get(w, {}).get("woc_after"),
                        "cb": by[loc].get(w, {}).get("colour_before"), "ca": by[loc].get(w, {}).get("colour_after")}
                       for w in weeks]} for loc in order]
    su = v["support_until"]["lc"]
    tone = {"do_not_support": "bad", "support_with_replan": "warn", "support": "good"}[v["verdict"]]
    already = bool(v["dips"]) and v["dips"][0].get("colour_before") == "red"
    title = {"do_not_support": "Do not support without a replan.",
             "support_with_replan": (f"The network can absorb it, but {lc} is already red and goes lower: replan needed."
                                     if already else f"Supportable at network level, but {lc} goes red: replan needed."),
             "support": "Supportable from planned stock."}[v["verdict"]]
    if su["first_red"] is None:
        sup_txt = f"{lc} stays out of red through week {su['through']}."
    elif su["through"] is None:
        sup_txt = f"{lc} is red from week {su['first_red']}, the week of the change."
    else:
        sup_txt = f"{lc} can support it through week {su['through']}; red from week {su['first_red']}."
    flagged = [f for f in v["flagged_locations"] if f != lc]
    blocks = [
        {"type": "kv", "items": [
            {"label": "Item", "value": material, "sub": desc},
            {"label": "Change", "value": f"{fmt_signed(v['delta_cases'])} cases",
             "sub": (f"{v['delta_pct']:+g}% of {fmt_int(v['base_demand_cases'])} cases demand · " if v.get("delta_pct") is not None else "")
                    + f"week {week} · {cx.week_label(week)}"},
            ({"label": "Lands at", "value": lc, "sub": cx.SITE_NAMES[lc]} if v.get("requested_scope") != "NETWORK" else
             {"label": "Spread across", "value": "Network", "sub": " · ".join(f"{k} {fmt_signed(c)}" for k, c in v["split"].items())}),
            {"label": "Cover window", "value": f"{v['window_weeks']} weeks", "sub": "average forward total demand"}]},
        {"type": "banner", "tone": tone, "title": title,
         "text": f"Network cover {v['network_before']} → {v['network_after']} weeks. {sup_txt}"
                 + (f" Also flagged: {', '.join(flagged)}." if flagged else "")},
        {"type": "wocgrid", "title": "Weeks of cover by LC, before → after",
         "weeks": [{"calweek": w, "label": cx.week_label(w)} for w in weeks], "rows": grid,
         "note": f"WoC = projected stock in the week / average total demand of the next {v['window_weeks']} weeks "
                 "(Kinaxis plan: on hand, in transit, firm and planned orders). Under 2 red, 2 to 3.9 yellow, 4 to 5.0 green, above 5.0 blue."},
    ]
    if v["dips"]:
        blocks.append({"type": "table", "title": f"Why {lc} cover drops", "columns": [
            {"key": "calweek", "label": "Week"}, {"key": "woc_after", "label": "WoC after", "format": "woc", "colour_key": "colour_after"},
            {"key": "stock_after", "label": "Projected stock", "format": "int"}, {"key": "supply", "label": "Supply that week", "format": "int"},
            {"key": "demand", "label": "Demand that week", "format": "int"}, {"key": "next_inbound_week", "label": "Next inbound week"},
            {"key": "next_inbound_cases", "label": "Next inbound, cases", "format": "int"}],
            "rows": v["dips"], "note": f"Cases {lc} needs to stay at 2 weeks or more in every week shown: "
                                       f"{fmt_int(v['cases_to_stay_out_of_red'])}."})
    opts = [{"source": f"{d['location']} {cx.SITE_NAMES[d['location']]}", "kind": "Transfer from LC",
             "available": d["excess_above_4_weeks"] or d["excess_above_2_weeks"],
             "basis": "above 4 weeks of cover" if d["excess_above_4_weeks"] else "above 2 weeks (donor goes yellow)",
             "leadtime_days": d["leadtime_days"], "arrives_week": d["arrives_week"],
             "cost": f"{d['mode'].lower()} {fmt_int(d['cost_per_load'])} USD/load" if d.get("cost_per_load") else "not on file"}
            for d in v["donors"]]
    opts += [{"source": f"{f['site']} factory", "kind": "Factory replan", "available": f["planned_cases"],
              "basis": f"planned production in weeks {', '.join(str(w) for w in f['weeks_with_production'][:4])}",
              "leadtime_days": f["leadtime_days"], "arrives_week": None, "cost": "replan request"} for f in v["factory"]]
    if opts and v["verdict"] != "support":
        blocks.append({"type": "table", "title": "Options to cover it", "columns": [
            {"key": "source", "label": "Source"}, {"key": "kind", "label": "Action"}, {"key": "available", "label": "Cases", "format": "int"},
            {"key": "basis", "label": "Basis"}, {"key": "leadtime_days", "label": "Lead time, days"},
            {"key": "arrives_week", "label": "Arrives week"}, {"key": "cost", "label": "Cost"}], "rows": opts,
            "note": "Donor stock keeps the donor LC at or above the threshold shown. Shuttles, STOs and factory requests are drafts for your approval; nothing is created."})
    elif v["verdict"] != "support":
        blocks.append({"type": "note", "tone": "info", "text": "No LC holds stock above 2 weeks of cover and no factory production is planned for this item in the horizon. Escalate as a constraint."})
    # Narrative (workflow doc output): verdict, network move, LC dip with reason, support-until, options
    n = [title, f"Network cover moves from {v['network_before']} to {v['network_after']} weeks ({COLOUR_WORD.get(v['network_colour_after'], '')})."]
    if v["dips"]:
        d = v["dips"][0]
        why = (f"demand of {fmt_int(d['demand'])} cases against {fmt_int(d['supply'])} cases of supply"
               if d["demand"] > d["supply"] else f"projected stock of {fmt_int(d['stock_after'])} cases")
        inbound = (f"; the next inbound of {fmt_int(d['next_inbound_cases'])} cases lands in week {d['next_inbound_week']}"
                   if d["next_inbound_week"] else "; no inbound lands in the horizon")
        was = (f" (already {COLOUR_WORD[d['colour_before']]} at {d['woc_before']} weeks before the change)"
               if d.get("colour_before") == "red" else f" from {d['woc_before']}")
        n.append(f"{lc} drops to {d['woc_after']} weeks{was} in week {d['calweek']}: {why}{inbound}.")
    n.append(sup_txt)
    if v["verdict"] != "support":
        if v["cases_to_stay_out_of_red"]:
            n.append(f"{lc} needs {fmt_int(v['cases_to_stay_out_of_red'])} more cases to stay at 2 weeks or more.")
        if v["donors"]:
            d = v["donors"][0]
            avail = d["excess_above_4_weeks"] or d["excess_above_2_weeks"]
            floor = "4" if d["excess_above_4_weeks"] else "2"
            need = v["cases_to_stay_out_of_red"]
            ship = need if need and need <= avail else avail
            first_red = v["support_until"]["lc"]["first_red"]
            late = (d["arrives_week"] is not None and first_red is not None and d["arrives_week"] > first_red)
            lane = (f", {d['leadtime_days']} days, arriving week {d['arrives_week']}"
                    + (f", after the first red week {first_red}" if late else "") if d["leadtime_days"] is not None
                    else ", no lane on file for this item")
            n.append(f"Options: ship {fmt_int(ship)} cases from {d['location']} (it holds {fmt_int(avail)} above "
                     f"{floor} weeks of cover{lane})"
                     + (f", or a factory replan at {v['factory'][0]['site']}." if v["factory"] else "."))
        elif v["factory"]:
            f = v["factory"][0]
            n.append(f"No LC has spare stock; suggest a factory replan at {f['site']}, which has {fmt_int(f['planned_cases'])} cases planned in the horizon.")
    whys = []
    for d in v["dips"]:
        reason = (f"demand of {fmt_int(d['demand'])} cases against {fmt_int(d['supply'])} cases of supply"
                  if d["demand"] > d["supply"] else f"projected stock of {fmt_int(d['stock_after'])} cases is low for the demand ahead")
        nxt = (f", and the next inbound ({fmt_int(d['next_inbound_cases'])} cases) only lands in week {d['next_inbound_week']}"
               if d["next_inbound_week"] else ", and no inbound lands in the horizon")
        whys.append(f"In week {d['calweek']} {lc} is at {d['woc_after']} weeks: {reason}{nxt}.")
    card_why = " ".join(whys) or None
    card = {"id": card_id, "scenario": "S1", "status": "ok", "why": card_why, "focus_material": material,
            "subtitle": f"{material} {desc or ''} · {fmt_signed(v['delta_cases'])} cases at "
                        f"{'the network (by LC demand)' if v.get('requested_scope') == 'NETWORK' else lc} · week {week} · {v['window_weeks']}-week cover",
            "blocks": blocks}
    if v["verdict"] != "support":
        card["replan"] = {"scenario": "S1", "text": " ".join(n[1:]) + " Please confirm a replan or transfer."}
    return finish(card, [res], " ".join(n))


# ---------- S3, end to end (inventory support, then capacity) ----------
def s3_full(card_id, label, growth, months_label, wc, att_res, cover_res, gap_res, cap_basis, cap_value, plan_res=None, lanes=None):
    cv, av, gv = cover_res["values"], att_res["values"], gap_res["values"]
    items = rows_of(cover_res)
    for r in items:
        r["description"] = cx.item_desc(r["material"]) or ""
        r["lcs"] = ", ".join(r["lcs_turning_red"]) if r["lcs_turning_red"] else "none"
    rows = rows_of(gap_res)
    first = gv["first_constrained_month"]
    inv_ok = cv["supported"]
    blocks = [
        {"type": "kv", "items": [
            {"label": "Segment", "value": label, "sub": f"{cv['items']} items with demand"},
            {"label": "Growth", "value": f"{fmt_signed(growth)}%", "sub": months_label},
            {"label": "Line", "value": wc, "sub": cx.wc_label(wc).split(" · ", 1)[1]},
            {"label": "Capacity basis", "value": f"{fmt_int(cap_value)} units/mo",
             "sub": "planner-entered rated capacity" if cap_basis == "rated" else "line's fixed plan, last 3 months",
             "badge": "planner" if cap_basis == "rated" else None}]},
        {"type": "banner", "tone": "good" if inv_ok else "bad",
         "title": "Current inventory and cover support the growth" if inv_ok else
                  f"Current inventory does not support it: {len(cv['items_red_network'])} items go red at network level",
         "text": f"{fmt_int(cv['extra_cases_total'])} extra cases of demand across the segment. "
                 + ("" if inv_ok else f"Items red at network level: {', '.join(cv['items_red_network'][:6])}.")},
        {"type": "table", "title": "Cover with the growth, by item (network, lowest week in the growth months)",
         "columns": [{"key": "material", "label": "Item"}, {"key": "description", "label": "Description"},
                     {"key": "extra_cases", "label": "Extra cases", "format": "int"},
                     {"key": "network_min_before", "label": "WoC before", "format": "woc", "colour_key": "colour_before"},
                     {"key": "network_min_after", "label": "WoC after", "format": "woc", "colour_key": "colour_after"},
                     {"key": "lcs", "label": "LCs turning red"}, {"key": "first_red_week", "label": "First red week"}],
         "rows": items, "note": f"Growth applied to each item's total demand at every LC in {', '.join(cv['months'])}, carried through "
                                f"projected stock; WoC on a {cv['window_weeks']}-week forward window. No forecast beyond the Kinaxis plan."},
        {"type": "banner", "tone": "bad" if first else "good",
         "title": f"{wc} is short from {first}" if first else f"Demand fits within {wc} effective capacity",
         "text": f"Effective capacity {fmt_int(gv['effective_capacity_units'])} units/month = {fmt_int(cap_value)} × "
                 f"{av['trailing_avg_pct']}% trailing attainment."
                 + ("" if cap_basis == "rated" else " Rated capacity is not in the extracts; the line's average fixed plan is used. "
                                                     "Enter rated capacity in Data Gaps to replace it.")},
        {"type": "table", "title": "Demand against effective capacity, units",
         "columns": [{"key": "month", "label": "Month"}, {"key": "demand_cases", "label": "Cases", "format": "int"},
                     {"key": "demand_units", "label": "Units", "format": "int"},
                     {"key": "grown_units", "label": f"Units at {fmt_signed(growth)}%", "format": "int", "strong": True},
                     {"key": "effective_capacity_units", "label": "Effective capacity", "format": "int"},
                     {"key": "gap_units", "label": "Gap", "format": "gap"}], "rows": rows,
         "note": "Units = cases × case-to-each (SAP Item Master). Months bucketed by the month each Kinaxis week ends in."},
    ]
    lane_rows = [{"source": l["values"]["source"], "type": l["values"].get("type"), "leadtime_days": l["values"].get("leadtime_days"),
                  "min_qty": l["values"].get("min_qty")} for l in (lanes or [])]
    if first and lane_rows:
        blocks.append({"type": "table", "title": "Sources for the gap", "tone": "dashed",
                       "columns": [{"key": "source", "label": "Source"}, {"key": "type", "label": "Lane"},
                                   {"key": "leadtime_days", "label": "Lead time, days", "format": "int"},
                                   {"key": "min_qty", "label": "Minimum, cases", "format": "int"}], "rows": lane_rows,
                       "note": "Make and Buy lanes for the segment's items, by lead time. Cost per unit is not on file."})
    n = []
    n.append(f"{label} at {fmt_signed(growth)}%: " + ("current inventory and cover support it." if inv_ok else
             f"current inventory does not support it; {len(cv['items_red_network'])} items go red at network level "
             f"({', '.join(cv['items_red_network'][:3])})."))
    if first:
        r0 = next(r for r in rows if r["month"] == first)
        n.append(f"On capacity, {wc} is short from {first}: {fmt_int(r0['grown_units'])} units of demand against "
                 f"{fmt_int(gv['effective_capacity_units'])} units of effective capacity, a gap of {fmt_int(r0['gap_units'])} units.")
    else:
        n.append(f"Demand fits within {fmt_int(gv['effective_capacity_units'])} units of effective capacity on {wc}.")
    res = [cover_res, att_res, gap_res] + ([plan_res] if plan_res else []) + list(lanes or [])
    card = {"id": card_id, "scenario": "S3", "status": "ok",
            "subtitle": f"{label} · {fmt_signed(growth)}% · {months_label} · line {wc}", "blocks": blocks}
    return finish(card, res, " ".join(n))


# ---------- Network dashboard ----------
def dashboard(card_id, res) -> dict:
    v = res["values"]
    seg = rows_of(res)
    grid = [{"loc": r["segment"], "name": f"{r['items']} items", "changed": False,
             "cells": [{"after": r.get(loc), "ca": colour_of(r.get(loc))} for loc in list(cx.SITE_NAMES) + ["NETWORK"]]}
            for r in seg]
    lines = v["lines"]
    blocks = [
        {"type": "kv", "items": [{"label": f"Red items · {x['location']}", "value": str(x["items_red"]),
                                  "sub": cx.SITE_NAMES[x["location"]]} for x in v["red_items_by_lc"]]},
        {"type": "wocgrid", "title": f"Weeks of cover by segment and LC, week {v['calweek']} ({cx.week_label(v['calweek'])})",
         "single": True, "row_label": "Segment", "labels_only": True,
         "weeks": [{"calweek": i, "label": n} for i, n in enumerate(list(cx.SITE_NAMES.values()) + ["Network"])],
         "rows": grid, "note": f"Segment = BSG group and consumer size. WoC on the summed segment, {v['window_weeks']}-week forward demand."},
        {"type": "table", "title": "Factory lines, trailing 3-month attainment",
         "columns": [{"key": "work_center", "label": "Line"}, {"key": "factory", "label": "Factory"},
                     {"key": "trailing_pct", "label": "Attainment", "format": "pct1"},
                     {"key": "fixed_cases_3m", "label": "Fixed, 3 months", "format": "int"},
                     {"key": "actual_cases_3m", "label": "Actual, 3 months", "format": "int"}], "rows": lines,
         "note": "Rated line capacity is not in the extracts; enter it in Data Gaps to see headroom by line."},
    ]
    card = {"id": card_id, "scenario": "DASH", "status": "ok",
            "subtitle": f"Kinaxis snapshot {cx.snapshot_label()} · week {v['calweek']} · planned basis", "blocks": blocks}
    return finish(card, [res], None)


def colour_of(w):
    from osb_tools.woc import colour
    return colour(w)

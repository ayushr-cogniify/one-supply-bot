"""Supplement sheets (MVP doc 3.3): what the delivered extracts lack, entered by planners.

Rows are append-only. A retraction sets a flag and never deletes a row. Every write adds a line
to the audit log. The store lives in the session the caller passes in, so the browser can keep
it and export it as OSB_Supplements.xlsx."""
from datetime import datetime, timezone

SHEETS = {
    "Work_Centres": ["work_center", "factory_site", "rated_capacity_units_per_month", "unit_basis", "allowed_sizes",
                     "valid_from"],
    "Cut_Lines": ["cut_id", "item", "lc", "cut_date", "cases_ordered", "cases_cut", "customer", "order_issue_flag",
                  "entry_error_flag", "force_majeure_flag", "mgmt_decision_flag"],
    "Pacing": ["item", "period", "scope", "ordered_pct_of_lag1_forecast"],
    "PIR_List": ["item", "description"],
    "User_Facts": ["fact_id", "entity", "entity_id", "key", "value", "unit"],
    "Draft_Actions": ["draft_id", "action_type", "rendered_text", "status"],
    "Approvals": ["approval_id", "draft_id", "decision", "comment"],
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def empty() -> dict:
    return {k: [] for k in SHEETS}


def add(session: dict, sheet: str, row: dict, note: str = None) -> dict:
    sup = session.setdefault("supplements", empty())
    rows = sup.setdefault(sheet, [])
    full = {**{c: row.get(c) for c in SHEETS[sheet]}, "entered_by": session.get("planner", "planner"),
            "entered_at": now(), "note": note or row.get("note"), "retracted": False,
            "row_id": f"{sheet[:2].lower()}-{len(rows) + 1:04d}"}
    rows.append(full)
    audit(session, "supplement_write", f"{sheet} {full['row_id']}", {k: full[k] for k in SHEETS[sheet]})
    return full


def latest(session: dict, sheet: str, **match):
    for r in reversed(session.get("supplements", {}).get(sheet, [])):
        if not r.get("retracted") and all(str(r.get(k)) == str(v) for k, v in match.items()):
            return r
    return None


def live(session: dict, sheet: str) -> list:
    return [r for r in session.get("supplements", {}).get(sheet, []) if not r.get("retracted")]


def retract(session: dict, sheet: str, row_id: str) -> bool:
    for r in session.get("supplements", {}).get(sheet, []):
        if r["row_id"] == row_id and not r["retracted"]:
            r["retracted"] = True
            audit(session, "supplement_retract", f"{sheet} {row_id}", {})
            return True
    return False


def audit(session: dict, event: str, ref: str, detail: dict):
    session.setdefault("audit", []).append({"at": now(), "user": session.get("planner"), "event": event,
                                            "ref": ref, "detail": detail})

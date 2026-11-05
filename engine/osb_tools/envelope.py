"""Result envelope shared by every tool (Engineering appendix B.1)."""
import itertools

_ids = itertools.count(1)


def envelope(tool, status="ok", values=None, table=None, rows_used=None, warnings=None,
             missing=None, user_facts_used=None):
    return {"result_id": f"r:{tool}-{next(_ids):04d}", "tool": tool, "status": status,
            "values": values or {}, "table": table, "rows_used": rows_used or [],
            "user_facts_used": user_facts_used or [], "missing": missing or [],
            "warnings": warnings or []}


def table(df):
    df = df.astype(object).where(df.notna(), None)
    return {"columns": list(df.columns), "rows": df.values.tolist()}


def cite(df, limit=200):
    """rows_used entries from a frame that carries _file/_sheet/_row/_hash."""
    cols = [c for c in ["_file", "_sheet", "_row", "_hash"] if c in df.columns]
    out = [{"source_file": r.get("_file"), "source_sheet": r.get("_sheet"),
            "source_row": int(r["_row"]), "hash": r.get("_hash")}
           for r in df[cols].head(limit).to_dict("records")]
    return out

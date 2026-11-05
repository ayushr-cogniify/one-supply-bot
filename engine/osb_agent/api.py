"""JSON in, JSON out: the surface the browser worker (or a FastAPI route) calls."""
import json
import math
from datetime import date, datetime

from osb_tools import bundle

from . import context as cx, models, pipeline

_STATE = {"manifest": None}


def _clean(o):
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set)):
        return [_clean(v) for v in o]
    if isinstance(o, float):
        return None if math.isnan(o) or math.isinf(o) else o
    if isinstance(o, (datetime, date)):
        return o.isoformat()[:10] if isinstance(o, date) and not isinstance(o, datetime) else o.isoformat()
    if hasattr(o, "item"):                      # numpy scalar
        return _clean(o.item())
    if hasattr(o, "isoformat"):                 # pandas Timestamp
        return o.isoformat()
    return o


def dumps(o) -> str:
    return json.dumps(_clean(o))


def boot(data_dir: str) -> str:
    _STATE["manifest"] = bundle.load(data_dir)
    session = pipeline.new_session()
    m = _STATE["manifest"]
    info = {
        "snapshot": cx.snapshot_label(), "stock_to": cx.stock_label(),
        "lcs": [{"code": k, "name": v} for k, v in cx.SITE_NAMES.items()],
        "weeks": [{"calweek": int(w), "label": cx.week_label(w)} for w in cx.cal().CALWEEK.head(8)],
        "load_report": m.get("load_report", {}), "frames": {k: {"rows": v["rows"], "bytes": v["bytes"]}
                                                           for k, v in m["frames"].items()},
        "bundle_created": m.get("created_at"), "models": models.table(), "route": models.route(),
    }
    return dumps({"info": info, "session": session, "gaps": pipeline.gaps(session)})


def handle(request_json: str, session_json: str) -> str:
    req = json.loads(request_json)
    session = json.loads(session_json) if session_json else pipeline.new_session()
    out = pipeline.handle(req, session)
    return dumps(out)


def checks() -> str:
    from . import checks as golden
    return dumps(golden.run())

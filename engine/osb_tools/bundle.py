"""Data bundle: the cleaned frames the adapter produces, written once as compact gzip CSVs so the
same engines can run in the browser (Pyodide) without parsing the delivered workbooks there.

export(out_dir)  native Python: run the adapter over the delivered files, write frames + manifest
load(in_dir)     native or Pyodide: read the frames back and point the adapter at them

Every frame keeps its citation columns (_file, _sheet, _row, _hash), so a tool reading the bundle
cites the same file, sheet and Excel row as one reading the workbook."""
import gzip
import io
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from . import adapter, config

CITE = ["_file", "_sheet", "_row", "_hash"]

# Only the columns the tools read are shipped; every other column stays in the delivered file.
KEEP = {
    "kinaxis_plan": ["MATERIAL", "LOCATION", "CALWEEK", "STOCK_PLANNED", "STOCK_CONF", "TOTAL_DEMAND", "TOTAL_SUPPLY",
                     "FORECAST", "SALES_ORDERS", "DEP_DEM", "IN_TRANSIT", "PROD_PLANNED_", "PROD_CONFIRMED",
                     "DISTRIBUTION_RECEIPT_PLANNED", "TARGET_STOCK", "EXPIRED_QUANTITY", "SNAPSHOT_WEEK", "BSG_GROUP"],
    "item_master": ["ITEMCODE", "ITEM", "UPC", "GTIN", "PLANT", "CASETOEACH", "CONSUMERUNITSIZE", "CONSUMERUOM",
                    "MATERIALSTATUS", "STANDARDCOGS"],
    "attainment": None,
    "inventory": ["PLANT", "SLOC", "MATERIAL", "ONHAND", "AVAIL_TO_SELL", "QUAL_INSPEC", "UNRESTRICTED_INV",
                  "BLOCKED_INV", "INTRANSIT_QTY", "ATP_QUANTITY", "ALLOCATION_QTY", "ATP_AFTER_ALLOCATION",
                  "Updated_Date", "ts", "snapshot_date"],
    "kinaxis_master": ["product", "site", "product_desc", "segment_desc", "consumer_product_size_id"],
    "lanes": None,
    "transport": None,
    "reason_codes": None,
    "storyline": None,
    "pfr_lines": None,
    "kinaxis_snapshots": None,
}

_FRAMES: dict = {}


def _kind(s: pd.Series) -> str:
    if pd.api.types.is_bool_dtype(s):
        return "bool"
    if pd.api.types.is_datetime64_any_dtype(s):
        return "datetime"
    if pd.api.types.is_integer_dtype(s):
        return "int"
    if pd.api.types.is_float_dtype(s):
        return "float"
    vals = s.dropna()
    if len(vals) and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in vals):
        return "float"
    return "str"


def _sources():
    """Frame name -> callable returning the cleaned frame, read through the adapter."""
    from . import cuts
    return {
        "kinaxis_plan": adapter.kinaxis_plan,
        "item_master": adapter.item_master,
        "attainment": adapter.attainment,
        "inventory": lambda: adapter.inventory()[lambda d: d.PLANT.isin(config.LCS)],
        "kinaxis_master": adapter.kinaxis_master,
        "lanes": adapter.lanes,
        "transport": adapter.transport,
        "reason_codes": adapter.reason_codes,
        "storyline": lambda: adapter.storyline("Hygiene Storyline"),
        "pfr_lines": lambda: cuts.pfr_lines()[lambda d: d["BSG Group"].isin(["Lysol LDS", "Lysol Sanitizers"])],
        "kinaxis_snapshots": _recent_snapshots,
    }


def _recent_snapshots(n=5):
    df = adapter.kinaxis_snapshots()
    keep = sorted(df.snapshot_date.unique())[-n:]
    cols = ["MATERIAL", "LOCATION", "CALWEEK", "STOCK_PLANNED", "TOTAL_DEMAND", "TOTAL_SUPPLY", "FORECAST",
            "snapshot_date", "_file", "_sheet", "_row"]
    return df[df.snapshot_date.isin(keep)][cols]


def export(out_dir, load_report: dict = None) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    manifest = {"created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "frames": {},
                "load_report": load_report or {}}
    for name, fn in _sources().items():
        try:
            df = fn().reset_index(drop=True)
        except FileNotFoundError:                       # optional extracts (weekly snapshots)
            continue
        keep = KEEP[name]
        if keep is not None:
            df = df[[c for c in keep if c in df.columns] + [c for c in CITE if c in df.columns]]
        kinds = {c: _kind(df[c]) for c in df.columns}
        buf = io.StringIO()
        w = df.copy()
        for c, k in kinds.items():
            if k == "datetime":
                w[c] = w[c].dt.strftime("%Y-%m-%dT%H:%M:%S")
        w.to_csv(buf, index=False)
        raw = gzip.compress(buf.getvalue().encode("utf-8"), mtime=0)
        (out / f"{name}.csv.gz").write_bytes(raw)
        manifest["frames"][name] = {"file": f"{name}.csv.gz", "rows": len(df), "columns": kinds, "bytes": len(raw)}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1))
    return manifest


def _read(path: Path, kinds: dict) -> pd.DataFrame:
    text_cols = [c for c, k in kinds.items() if k == "str"]
    with open(path, "rb") as f:                 # a server may hand back the file already decompressed
        gz = f.read(2) == b"\x1f\x8b"
    opener = (lambda: gzip.open(path, "rt", encoding="utf-8")) if gz else (lambda: open(path, encoding="utf-8"))
    with opener() as f:
        df = pd.read_csv(f, dtype={c: str for c in text_cols}, keep_default_na=False, na_values={
            c: [""] for c in kinds if kinds[c] != "str"})
    for c, k in kinds.items():
        if c not in df.columns:
            continue
        if k == "str":
            df[c] = df[c].astype(object).where(df[c] != "", None)
        elif k == "datetime":
            df[c] = pd.to_datetime(df[c])
        elif k == "bool":
            df[c] = df[c].astype(str).str.lower().eq("true")
        elif k == "int":
            df[c] = df[c].astype("int64")
        elif k == "float":
            df[c] = df[c].astype("float64")
    return df


def load(in_dir) -> dict:
    """Read the bundle and make the adapter (and the PFR reader) serve it."""
    src = Path(in_dir)
    manifest = json.loads((src / "manifest.json").read_text())
    for name, meta in manifest["frames"].items():
        _FRAMES[name] = _read(src / meta["file"], meta["columns"])
    _install()
    return manifest


def _install():
    from . import cuts
    adapter.kinaxis_plan = lambda: _FRAMES["kinaxis_plan"]
    adapter.item_master = lambda: _FRAMES["item_master"]
    adapter.attainment = lambda: _FRAMES["attainment"]
    adapter.inventory = lambda: _FRAMES["inventory"]
    adapter.kinaxis_master = lambda: _FRAMES["kinaxis_master"]
    adapter.lanes = lambda: _FRAMES["lanes"]
    adapter.transport = lambda: _FRAMES["transport"]
    adapter.reason_codes = lambda: _FRAMES["reason_codes"]
    adapter.storyline = lambda sheet="Hygiene Storyline": _FRAMES["storyline"]
    if "kinaxis_snapshots" in _FRAMES:
        _FRAMES["kinaxis_snapshots"]["CALWEEK"] = _FRAMES["kinaxis_snapshots"]["CALWEEK"].astype("int64")
        adapter.kinaxis_snapshots = lambda: _FRAMES["kinaxis_snapshots"]
    else:
        def _none():
            raise FileNotFoundError("No weekly Kinaxis snapshots in this bundle")
        adapter.kinaxis_snapshots = _none
    cuts.pfr_lines = lambda: _FRAMES["pfr_lines"]


def frame(name: str) -> pd.DataFrame:
    return _FRAMES[name]

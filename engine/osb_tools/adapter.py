"""Sheet adapter: reads the delivered workbooks as-is, checks them against the schema
contract, cleans in memory (never edits the file), and keeps file/sheet/Excel row for citations."""
import hashlib
import re
from functools import lru_cache
from pathlib import Path

import pandas as pd
import yaml

from . import config


class SchemaError(Exception):
    pass


def _norm(s: str) -> str:
    return re.sub(r"[\s_\-]+", "_", s).lower()


def find_file(keyword: str) -> Path:
    hits = [p for p in config.DATA_DIR.glob("*.xlsx") if _norm(keyword) in _norm(p.name)]
    if not hits:
        raise FileNotFoundError(f"No workbook matching '{keyword}' in {config.DATA_DIR}")
    if len(hits) > 1:
        raise FileNotFoundError(f"More than one workbook matches '{keyword}': {[h.name for h in hits]}")
    return hits[0]


def _hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:12]


@lru_cache
def schema() -> dict:
    with open(config.SCHEMA_FILE) as f:
        return yaml.safe_load(f)


def load_sheet(workbook: str, sheet_key: str) -> pd.DataFrame:
    spec_wb = schema()[workbook]
    spec = spec_wb["sheets"][sheet_key]
    path = find_file(spec_wb["file_keyword"])
    fhash = _hash(path)
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = config.CACHE_DIR / f"{workbook}.{sheet_key}.{fhash}.pkl"
    if cache.exists():
        return pd.read_pickle(cache)

    dtype = {c: str for c in spec.get("text_ids", [])}
    df = pd.read_excel(path, sheet_name=spec["sheet"], dtype=dtype)
    df.columns = [str(c).strip() for c in df.columns]
    if spec.get("normalize_headers"):
        # 'SAP/Kinaxis Code' -> sap_kinaxis_code, 'Cost Per Load (LH) (USD)' -> cost_per_load_lh_usd
        df.columns = [re.sub(r"[^a-z0-9]+", "_", c.strip().strip("'").lower()).strip("_") for c in df.columns]
        df = df.rename(columns=spec.get("rename", {}))

    # Contract checks: reject the file, not the row
    if len(df.columns) != spec["header_count"]:
        raise SchemaError(f"{path.name}/{spec['sheet']}: expected {spec['header_count']} headers, got {len(df.columns)}")
    missing = [c for c in spec["required"] if c not in df.columns]
    if missing:
        raise SchemaError(f"{path.name}/{spec['sheet']}: missing columns {missing}")

    # In-memory cleaning (Engineering appendix D)
    for c in spec.get("comma_numbers", []):
        df[c] = pd.to_numeric(df[c].astype(str).str.replace(",", "", regex=False), errors="coerce")
    for c in spec.get("money", []):
        df[c] = pd.to_numeric(df[c].astype(str).str.replace(r"[$,\s]", "", regex=True), errors="coerce")
    for c in spec.get("text_ids", []):
        df[c] = df[c].astype(str).str.replace(r"\.0$", "", regex=True).replace({"nan": None, "None": None})

    # Citation columns: Excel row = pandas index + 2 (header is row 1)
    df["_file"], df["_sheet"], df["_row"], df["_hash"] = path.name, spec["sheet"], df.index + 2, fhash

    dups = int(df.duplicated(spec["key"]).sum()) if spec.get("key") else 0
    if dups:
        raise SchemaError(f"{path.name}/{spec['sheet']}: {dups} duplicate keys on {spec['key']}")
    df.to_pickle(cache)
    return df


def kinaxis_plan() -> pd.DataFrame:
    df = load_sheet("kinaxis_data", "supply_demand").copy()
    df["MATERIAL"] = df["MATERIAL"].astype(str)
    df["LOCATION"] = df["LOCATION"].astype(str)
    df["CALWEEK"] = df["CALWEEK"].astype(int)
    df = df.rename(columns={"BSG Group": "BSG_GROUP", "Brand": "BRAND"})
    return df


def item_master() -> pd.DataFrame:
    return load_sheet("sap_item_master", "items")


def attainment() -> pd.DataFrame:
    df = load_sheet("attainment", "monthly").copy()
    df = df.rename(columns={"Year Month": "year_month", "Work Center": "work_center", "SKU Code": "material",
                            "SKU Description": "description", "Production Fix (Volume)": "fix",
                            "Production Actuals (Volume)": "actual", "Segment": "segment"})
    df["material"] = df["material"].astype(str)
    df["year_month"] = df["year_month"].astype(int)
    df[["fix", "actual"]] = df[["fix", "actual"]].fillna(0)
    return df


QTY_COLS = ["ONHAND", "AVAIL_TO_SELL", "QUAL_INSPEC", "UNRESTRICTED_INV", "BLOCKED_INV", "INTRANSIT_QTY",
            "ATP_QUANTITY", "ALLOCATION_QTY", "ATP_AFTER_ALLOCATION"]


def inventory() -> pd.DataFrame:
    """Appendix D: within a duplicate (PLANT, SLOC, MATERIAL, Updated_Date) group drop all-zero rows
    when a non-zero row exists; if two non-zero rows remain keep the higher ONHAND."""
    df = load_sheet("sap_inventory", "snapshots").copy()
    df["MATERIAL"] = df["MATERIAL"].astype(str)
    df["SLOC"] = df["SLOC"].astype("Int64").astype(str)
    df["ts"] = pd.to_datetime(df["Updated_Date"])
    df["snapshot_date"] = df["ts"].dt.normalize()
    key = ["PLANT", "SLOC", "MATERIAL", "Updated_Date"]
    df["_nonzero"] = (df[QTY_COLS].abs().sum(axis=1) > 0)
    df = df.sort_values(key + ["_nonzero", "ONHAND"], ascending=[True] * 4 + [False, False])
    df = df.drop_duplicates(key, keep="first")
    # several timestamps on one day: keep the latest per plant/sloc/material/day
    df = df.sort_values("ts").drop_duplicates(["PLANT", "SLOC", "MATERIAL", "snapshot_date"], keep="last")
    return df.drop(columns="_nonzero")


def kinaxis_master() -> pd.DataFrame:
    df = load_sheet("kinaxis_master", "master").copy()
    df["product"] = df["product"].astype(str)
    return df


def lanes() -> pd.DataFrame:
    df = load_sheet("kinaxis_master", "lanes").copy()
    df["PART"] = df["PART"].astype(str)
    df["MINIMUM_QTY"] = df["MINIMUM_QTY"].round(3)
    df["MULTIPLE_QTY"] = df["MULTIPLE_QTY"].round(3)
    df["active"] = df["TYPE"] != "Disabled"
    return df


def transport() -> pd.DataFrame:
    return load_sheet("transport", "lanes")


def _rc_workbook():
    from openpyxl import load_workbook
    path = find_file(schema()["reason_codes"]["file_keyword"])
    return path, load_workbook(path, read_only=True, data_only=True)


@lru_cache
def reason_codes() -> pd.DataFrame:
    """Planning RCs: data from row 6, columns B-I; Group forward-filled."""
    path, wb = _rc_workbook()
    rows = []
    for i, r in enumerate(wb["Planning RCs"].iter_rows(min_row=6, values_only=True), start=6):
        r = list(r) + [None] * 10
        if r[4] is None:
            continue
        rows.append({"priority": r[1], "group": r[2], "global_rc": r[3], "code": str(r[4]).strip(),
                     "short_description": r[5], "long_description": r[6], "pfr1": r[7], "pfr2": r[8],
                     "_file": path.name, "_sheet": "Planning RCs", "_row": i})
    df = pd.DataFrame(rows)
    df["group"] = df["group"].ffill()
    return df


@lru_cache
def storyline(sheet: str = "Hygiene Storyline") -> pd.DataFrame:
    path, wb = _rc_workbook()
    rows = []
    for i, r in enumerate(wb[sheet].iter_rows(min_row=4, values_only=True), start=4):
        r = list(r) + [None] * 10
        if not isinstance(r[1], (int, float)):
            continue
        rows.append({"scenario": int(r[1]), "m_3": r[2], "m_2": r[3], "m_1": r[4], "safety_stock": r[6],
                     "forecast_increase": r[7], "inbound_delay": r[8], "network_imbalance": r[9],
                     "quality_hold": r[10], "factory_adherence": r[11], "expected_code": r[12],
                     "_file": path.name, "_sheet": sheet, "_row": i})
    return pd.DataFrame(rows)

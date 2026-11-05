"""Pull the four US LCs out of the Kinaxis Supply and Demand Snapshots workbook (1.4M rows across
two sheets with different formats) into one tidy cached frame, keyed by snapshot date.
  .venv/bin/python scripts/extract_snapshots.py --src ~/Downloads/reckitt-data"""
import argparse, glob, re, sys, time
from datetime import datetime
from pathlib import Path
import pandas as pd
from openpyxl import load_workbook

LCS = {"USP6", "USP8", "USQ2", "USQ4"}
KEEP = ["CALWEEK", "LOCATION", "MATERIAL", "STOCK_PLANNED", "TOTAL_DEMAND", "TOTAL_SUPPLY", "FORECAST",
        "SALES_ORDERS", "DEP_DEM", "PROD_PLANNED_", "PROD_CONFIRMED", "IN_TRANSIT", "DATA_LOAD", "SNAPSHOT_WEEK",
        "BSG Group"]


def snap_date(data_load, snapshot_week):
    for v in (data_load, snapshot_week):
        if v is None or v == "":
            continue
        if isinstance(v, datetime):
            return v.date()
        s = str(v).strip()
        for fmt in ("%m/%d/%Y", "%A, %B %d, %Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(s, fmt).date()
            except ValueError:
                pass
    return None


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--src", required=True); a = ap.parse_args()
    path = next(Path(a.src).expanduser().glob("*Kinaxis*Snapshots*.xlsx"))
    t = time.time()
    wb = load_workbook(path, read_only=True)
    rows = []
    for ws in wb.worksheets:
        it = ws.iter_rows(values_only=True)
        hdr = [h for h in next(it)]
        idx = {c: hdr.index(c) for c in KEEP}
        for i, r in enumerate(it, start=2):
            if r[idx["LOCATION"]] not in LCS:
                continue
            rec = {c: r[idx[c]] for c in KEEP}
            rec["_sheet"], rec["_row"] = ws.title, i
            rows.append(rec)
        print(ws.title, "done", len(rows), round(time.time() - t), "s", flush=True)
    df = pd.DataFrame(rows)
    df["snapshot_date"] = [snap_date(d, s) for d, s in zip(df.DATA_LOAD, df.SNAPSHOT_WEEK)]
    for c in ["STOCK_PLANNED", "TOTAL_DEMAND", "TOTAL_SUPPLY", "FORECAST", "SALES_ORDERS", "DEP_DEM",
              "PROD_PLANNED_", "PROD_CONFIRMED", "IN_TRANSIT"]:
        df[c] = pd.to_numeric(df[c].astype(str).str.replace(",", ""), errors="coerce").fillna(0)
    df["CALWEEK"] = pd.to_numeric(df.CALWEEK, errors="coerce").astype("Int64")
    df["MATERIAL"] = df.MATERIAL.astype(str).str.replace(r"\.0$", "", regex=True)
    df["_file"] = path.name
    out = Path(__file__).resolve().parents[1] / ".cache" / "kinaxis_snapshots.pkl"
    df.drop(columns=["DATA_LOAD", "SNAPSHOT_WEEK"]).to_pickle(out)
    print("snapshots:", df.snapshot_date.value_counts().sort_index().tail(12).to_dict())
    print("saved", out, len(df), "rows in", round(time.time() - t), "s")


if __name__ == "__main__":
    main()

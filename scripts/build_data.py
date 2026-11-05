"""Build the data bundle the web app ships, from the delivered workbooks.

  .venv/bin/python scripts/build_data.py --src ~/Downloads/reckitt-data

Reads every workbook through the schema contract (a changed file is rejected), applies the
loader rules in memory, writes .bundle/*.csv.gz + manifest.json with a load report,
and packs the Python engine into web/public/engine.zip for the browser worker.

The data goes to .bundle/ (never into the public site); scripts/upload_data.py puts it in the
private Supabase bucket the app reads after sign-in."""
import argparse
import os
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
OUT = ROOT / "web" / "public"


def pack_engine():
    OUT.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(OUT / "engine.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for pkg in ("osb_tools", "osb_agent", "schema"):
            for f in sorted((ENGINE / pkg).rglob("*")):
                if f.is_file() and "__pycache__" not in f.parts:
                    z.write(f, f.relative_to(ENGINE).as_posix())
    print(f"engine.zip  {(OUT / 'engine.zip').stat().st_size / 1024:.0f} KB")


def load_report():
    from osb_tools import adapter, config, cuts
    from osb_tools.woc import calendar
    t = time.time()
    plan = adapter.kinaxis_plan()
    raw_inv = adapter.load_sheet("sap_inventory", "snapshots")
    inv = adapter.inventory()
    att = adapter.attainment()
    im = adapter.item_master()
    km = adapter.kinaxis_master()
    ln = adapter.lanes()
    tr = adapter.transport()
    rc = adapter.reason_codes()
    pfr = cuts.pfr_lines()
    cal = calendar(plan)
    kin_mats = set(plan.MATERIAL)
    sheets = [
        {"file": plan._file.iloc[0], "sheet": "Kinaxis Supply and Demand", "rows": len(plan), "headers": 41},
        {"file": km._file.iloc[0], "sheet": "Kinaxis Master", "rows": len(km), "headers": 110},
        {"file": ln._file.iloc[0], "sheet": "Kinaxis Part Source (Lanes)", "rows": len(ln), "headers": 22,
         "note": f"{int((~ln.active).sum())} disabled lanes load inactive"},
        {"file": im._file.iloc[0], "sheet": "SAP Item Master", "rows": len(im), "headers": 68},
        {"file": raw_inv._file.iloc[0], "sheet": str(raw_inv._sheet.iloc[0]), "rows": len(raw_inv), "headers": 26,
         "dropped": len(raw_inv) - len(inv), "note": "duplicate snapshot rows dropped (all-zero row or lower ONHAND)"},
        {"file": att._file.iloc[0], "sheet": str(att._sheet.iloc[0]), "rows": len(att), "headers": 16},
        {"file": tr._file.iloc[0], "sheet": str(tr._sheet.iloc[0]), "rows": len(tr), "headers": 24},
        {"file": rc._file.iloc[0], "sheet": "Planning RCs", "rows": len(rc), "headers": None},
        {"file": pfr._file.iloc[0], "sheet": "Summary ZSD Ship", "rows": len(pfr), "headers": 86,
         "note": "Lysol LDS and Lysol Sanitizers rows shipped to the app"},
    ]
    return {
        "sheets": sheets,
        "matches": [
            {"label": "Kinaxis materials in SAP Item Master", "matched": len(kin_mats & set(im.ITEMCODE)), "of": len(kin_mats)},
            {"label": "Inventory materials also in Kinaxis", "matched": len(set(inv.MATERIAL) & kin_mats),
             "of": inv.MATERIAL.nunique()},
            {"label": "Attainment SKUs also in Kinaxis", "matched": len(set(att.material) & kin_mats),
             "of": att.material.nunique()},
        ],
        "calendar": {"first_week": int(cal.CALWEEK.iloc[0]), "first_start": str(cal.week_start.iloc[0]),
                     "last_week": int(cal.CALWEEK.iloc[-1]), "last_start": str(cal.week_start.iloc[-1]),
                     "weeks": len(cal)},
        "inventory_dates": {"first": str(inv.snapshot_date.min().date()), "last": str(inv.snapshot_date.max().date()),
                            "count": int(inv.snapshot_date.nunique())},
        "seconds": round(time.time() - t, 1),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=os.environ.get("OSB_DATA_DIR"), help="folder with the delivered workbooks")
    ap.add_argument("--engine-only", action="store_true")
    a = ap.parse_args()
    sys.path.insert(0, str(ENGINE))
    pack_engine()
    if a.engine_only:
        return
    if not a.src:
        sys.exit("Give --src <folder with the delivered workbooks> or set OSB_DATA_DIR")
    os.environ["OSB_DATA_DIR"] = str(Path(a.src).expanduser())
    os.environ.setdefault("OSB_CACHE_DIR", str(ROOT / ".cache"))
    from osb_tools import bundle
    rep = load_report()
    m = bundle.export(ROOT / ".bundle", load_report=rep)
    for k, v in m["frames"].items():
        print(f"{k:16s} {v['rows']:>8,} rows  {v['bytes'] / 1024:>8.0f} KB")
    print(f"total {sum(v['bytes'] for v in m['frames'].values()) / 1e6:.1f} MB")


if __name__ == "__main__":
    main()

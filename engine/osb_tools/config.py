import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("OSB_DATA_DIR", ROOT / "data" / "delivered"))
CACHE_DIR = Path(os.environ.get("OSB_CACHE_DIR", ROOT / "data" / "cache"))
SCHEMA_FILE = ROOT / "schema" / "extract_schema.yaml"

# Rules-sheet defaults (MVP doc 3.3). Move to OSB_Supplements.xlsx later.
WOC_WINDOW_WEEKS = 6   # denominator = mean TOTAL_DEMAND of weeks w+1..w+6 (matches Rushi's PIR)
LCS = ["USP6", "USP8", "USQ2", "USQ4"]
OPENING_BUCKET = 197001

# Site crosswalk: Kinaxis LC code <-> SAP plant code (from PIR 'LC Twins' + PFR 'LC-Code-Desc')
SITES = {
    "USP6": {"name": "Harrisburg",      "sap_plant": "USD4"},
    "USP8": {"name": "Atlanta",         "sap_plant": "USF2"},
    "USQ2": {"name": "St. Peters",      "sap_plant": "USF6"},
    "USQ4": {"name": "North Las Vegas", "sap_plant": "USL6"},
}

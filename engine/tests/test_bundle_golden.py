"""The browser runs the engines on the data bundle, not the workbooks. This proves the bundle gives
the same golden answers: export it from the workbooks, load it in a fresh interpreter (so the
adapter swap cannot leak into other tests) and run every golden check there."""
import json
import os
import subprocess
import sys
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1]


def test_bundle_round_trip_passes_golden_checks(plan, tmp_path):
    out = tmp_path / "data"
    code = f"""
import json, sys
sys.path.insert(0, {str(ENGINE)!r})
from osb_tools import bundle
bundle.export({str(out)!r})
"""
    subprocess.run([sys.executable, "-c", code], check=True, env=os.environ.copy())
    run = f"""
import json, sys
sys.path.insert(0, {str(ENGINE)!r})
from osb_tools import bundle
bundle.load({str(out)!r})
from osb_agent import checks
print(json.dumps(checks.run()))
"""
    env = {**os.environ, "OSB_DATA_DIR": str(tmp_path / "no-workbooks-here")}   # must not touch the xlsx
    res = subprocess.run([sys.executable, "-c", run], check=True, capture_output=True, text=True, env=env)
    results = json.loads(res.stdout.strip().splitlines()[-1])
    failed = [r for r in results if not r["passed"]]
    assert not failed, failed

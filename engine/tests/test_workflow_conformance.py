"""Every step of the workflow doc (Reckitt_Scenario_Workflows, S1-S5), checked against the live Claude
gate. The only steps allowed to fail are the known data gaps below; a new failure, or a gap that starts
passing, fails the test. Live only (OSB_LIVE_CLAUDE=1)."""
import contextlib
import io
import os
import runpy
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("OSB_LIVE_CLAUDE") != "1", reason="set OSB_LIVE_CLAUDE=1")

KNOWN_GAPS = {
    # near-term St. Peters output is all PROD_CONFIRMED: planned-only would show no impact (ask Rushi)
    ("S2", "2 Attainment applied per line to PLANNED production orders only"),
    # rated capacity and source capacities/MOQ/cost are not in the extracts
    ("S3", "2 Uses rated line capacity (2M bottles from the call)"),
    ("S3", "3 Allocation splits the gap across St. Peters, New Jersey and EMO (lead time, MOQ, cost)"),
    ("S3", "Output: proposed EMO volumes"),
}


def test_workflow_doc_conformance():
    with contextlib.redirect_stdout(io.StringIO()):
        g = runpy.run_path(str(Path(__file__).parent / "workflow" / "conformance.py"))
    failed = {(sc, step) for sc, step, ok, _ in g["RESULTS"] if not ok}
    assert failed - KNOWN_GAPS == set(), f"new workflow failures: {sorted(failed - KNOWN_GAPS)}"
    assert KNOWN_GAPS - failed == set(), f"gaps now pass, update KNOWN_GAPS: {sorted(KNOWN_GAPS - failed)}"
    assert len(g["RESULTS"]) == 48

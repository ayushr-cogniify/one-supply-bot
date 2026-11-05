"""Golden values from the Engineering appendix E.2 (3283407, +5,000 cases at USQ4, week 202643).
If these fail, the calc engine is wrong - not the test."""
from osb_tools.pir import generate_pir
from osb_tools.woc import add_woc, colour, series, simulate_demand_change

WEEKS = [202643, 202644, 202645, 202646]

BASELINE = {  # location: WoC weeks 43..46
    "USP6": [1.2, 0.5, 5.6, 5.0],
    "USP8": [1.8, 2.7, 5.2, 4.2],
    "USQ2": [3.6, 2.5, 4.2, 3.2],
    "USQ4": [2.5, 3.6, 2.6, 4.9],
    "NETWORK": [2.1, 1.9, 4.7, 4.4],
}
AFTER = {"USQ4": [0.3, 1.5, 0.5, 2.9], "NETWORK": [1.8, 1.6, 4.4, 4.1]}


def _woc(s):
    s = add_woc(s)
    return [round(v, 1) for v in s[s.CALWEEK.isin(WEEKS)].woc]


def test_baseline_woc(plan):
    for loc, exp in BASELINE.items():
        assert _woc(series(plan, "3283407", loc)) == exp, loc


def test_supporting_rows(plan):
    s = add_woc(series(plan, "3283407", "USQ4"))
    s = s[s.CALWEEK.isin(WEEKS)]
    assert list(s.STOCK_PLANNED) == [5629, 8467, 6237, 12393]
    assert [round(x) for x in s.avg_fwd_demand] == [2255, 2352, 2431, 2510]
    n = add_woc(series(plan, "3283407", "NETWORK"))
    n = n[n.CALWEEK == 202643].iloc[0]
    assert n.STOCK_PLANNED == 32601 and round(n.avg_fwd_demand) == 15173


def test_simulate_plus_5000_at_vegas(plan):
    r = simulate_demand_change("3283407", "USQ4", 202643, 5000, plan=plan)
    cols = r["table"]["columns"]
    rows = [dict(zip(cols, x)) for x in r["table"]["rows"]]
    for loc, exp in AFTER.items():
        assert [x["woc_after"] for x in rows if x["location"] == loc] == exp, loc
    for loc in ["USP6", "USP8", "USQ2"]:   # unchanged LCs
        assert [x["woc_after"] for x in rows if x["location"] == loc] == BASELINE[loc]
    assert r["values"]["verdict"] == "do_not_support"
    assert r["rows_used"], "every number must cite source rows"


def test_scope_must_be_an_lc(plan):
    r = simulate_demand_change("3283407", "NETWORK", 202643, 5000, plan=plan)
    assert r["status"] == "needs_input"


def test_colour_bands():
    assert [colour(x) for x in [1.99, 2.0, 3.96, 4.0, 5.0, 5.04, 5.1, None]] == \
        ["yellow", "yellow", "green", "green", "green", "green", "blue", "n/a"]


def test_pir_matches_engine(plan):
    pir = generate_pir(materials=["3283407"], horizon_weeks=4, plan=plan)
    for loc, exp in BASELINE.items():
        got = list(pir[(pir.level.isin(["LC", "NETWORK"])) & (pir.location == loc)].woc)
        assert got == exp, loc
    assert (pir.level == "GROUP").any()

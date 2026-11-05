"""Breadth check: about a hundred varied planner requests across items, LCs, weeks, lines, sizes,
months and real cut lines. A planner clicks the first offered option up to three times. No request
may crash the pipeline, and every narrative shown must pass the numeric validator."""
import json
import random

from osb_agent import api
from osb_tools import adapter, cuts

LCS = {"USP6": "Harrisburg", "USP8": "Atlanta", "USQ2": "St. Peters", "USQ4": "Vegas"}


def _requests():
    rnd = random.Random(7)
    plan = adapter.kinaxis_plan()
    mats = sorted(plan[plan.LOCATION.isin(LCS)].MATERIAL.unique())
    qs = [f"{rnd.choice([5000, -2000, 12000, 800]):+,} cases of {m} at {LCS[rnd.choice(list(LCS))]} week of "
          f"{rnd.choice(['10/19', '10/26', '11/2', '11/9'])}" for m in rnd.sample(mats, 30)]
    qs += [f"What is the cover on {m}?" for m in rnd.sample(mats, 10)]
    qs += [f"How much stock of {m} at Atlanta?" for m in rnd.sample(mats, 3)]
    qs += [f"What if {wc} runs at {rnd.choice([60, 75, 85, 95])}% attainment?"
           for wc in ["PL11", "PL07", "PL01", "BA1", "BA2", "BA3", "BA4"]]
    qs += [f"Can we grow {sz} oz by 25%?" for sz in [90, 41, 21, 19, 40]]
    qs += [f"Top cuts in {mo} for {b}" for mo in ["January", "March", "June", "August", "September", "October"]
           for b in ["Lysol Sanitizers", "Lysol LDS"]]
    pf = cuts.pfr_lines()
    pf = pf[(pf["Cases Cut Total"] > 0) & pf.Plant.isin(LCS) & (pf.cut_date >= "2026-06-01")]
    qs += [f"Code the {r.cut_date.month}/{r.cut_date.day} cut of {r.Material} at {LCS[r.Plant]}"
           for r in pf.sample(15, random_state=3).itertuples()]
    qs += ["PIR for Lysol LDS", "PIR for Lysol Sanitizers", "PIR for 3283407", "What changed since last week?",
           "Can we take 5k more Odor next week?", "hello", "Belle Mead at 70% attainment", "St. Peters at 80%",
           "Can we take +3,000 cases of 4x90 Odor at Harrisburg week of 10/26?"]
    return qs


def test_hundred_requests_no_crash_and_validator_always_passes(plan):
    problems = []
    for q in _requests():
        out = json.loads(api.handle(json.dumps({"type": "message", "text": q}), ""))
        for _ in range(3):
            opts = [o["action"] for m in out["messages"] for o in m["options"] if o["action"]["type"] == "option"]
            if not opts:
                break
            pick = next((a for a in opts if a.get("value") == "no"), opts[0])
            out = json.loads(api.handle(json.dumps(pick), json.dumps(out["session"])))
            errors = [s for s in out["trace"]["spans"] if s["step"] == "error"]
            bad = [c["validator"] for c in out["cards"] if not c["validator"]["passed"]]
            if errors or bad:
                problems.append((q, errors, bad))
        errors = [s for s in out["trace"]["spans"] if s["step"] == "error"]
        bad = [c["validator"] for c in out["cards"] if not c["validator"]["passed"]]
        if errors or bad:
            problems.append((q, errors, bad))
    assert not problems, problems

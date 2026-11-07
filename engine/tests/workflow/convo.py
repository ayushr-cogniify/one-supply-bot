"""Plays planner conversations against the app engine, through the live Claude gate like the browser."""
import json, os, sys, urllib.request
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "engine"))
from osb_agent import api
env = dict(l.split("=", 1) for l in (ROOT / ".env").read_text().splitlines() if "=" in l)

def post(url, body, headers):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.loads(r.read())

TOK = post(f"{env['SUPABASE_URL']}/auth/v1/token?grant_type=password",
           {"email": env["E2E_EMAIL"], "password": env["E2E_PASSWORD"]}, {"apikey": env["SUPABASE_ANON_KEY"]})["access_token"]
NAMES = {"S1": "S1_demand_spike", "S2": "S2_attainment_drop", "S3": "S3_segment_growth", "S4": "S4_pfr_reason_coding",
         "S5": "S5_what_changed", "PIR": "PIR_report", "DASH": "NETWORK_DASHBOARD"}

class Convo:
    def __init__(self, live=True):
        self.session, self.live, self.log = None, live, []
    def say(self, text):
        parse = None
        if self.live:
            s = self.session or {}
            active = (s.get("pending") or s.get("last") or {}).get("scenario", "none")
            parse = post(f"{env['SUPABASE_URL']}/functions/v1/osb-nlu",
                         {"text": text, "context": {"today": "2026-11-06", "snapshot_week_start": "2026-10-19",
                                                    "active_scenario": NAMES.get(active, "none")}},
                         {"Authorization": f"Bearer {TOK}", "apikey": env["SUPABASE_ANON_KEY"]})
            if not parse.get("ok"):
                parse = None
        return self._send({"type": "message", "text": text, "parse": parse}, text)
    def click(self, label):
        for m in reversed(self.out["messages"]):
            for o in m["options"]:
                if o["label"].lower().startswith(label.lower()):
                    return self._send(o["action"], f"[click] {o['label']}")
        raise AssertionError(f"no option {label!r}")
    def _send(self, req, shown):
        self.out = json.loads(api.handle(json.dumps(req), json.dumps(self.session) if self.session else ""))
        self.session = self.out["session"]
        gate = next((s for s in self.out["trace"]["spans"] if s["step"] == "scope_gate"), {})
        tools = [s["name"] for s in self.out["trace"]["spans"] if s["step"] == "tool"]
        self.log.append((shown, self.out))
        print(f"\nPLANNER: {shown}   [{gate.get('engine','')}:{gate.get('label','')}] tools={tools[-3:]}")
        for m in self.out["messages"]:
            if m["kind"] in ("bot", "answer", "echo"):
                print(f"  {m['kind'].upper():6} {m['text'][:260]}" + (f"  OPTIONS={[o['label'] for o in m['options']][:5]}" if m["options"] else ""))
        for c in self.out["cards"]:
            print(f"  CARD   {c['scenario']} · {c['subtitle'][:120]} · validator={c['validator']['passed']}")
        return self.out

# One Supply Bot

A supply-planning assistant for Reckitt's Lysol LDS and Lysol Sanitizers planners in the US network.
It answers the five scenarios from the MVP PRD: S1 demand spike, S2 factory attainment drop,
S3 segment growth versus line capacity, S4 PFR root cause and reason coding, and S5 what changed. It
also produces projected inventory reports. It runs entirely in the browser and is hosted as a static site.

**Every number comes from a deterministic calculation engine**, never from a model. Each card cites the
file, sheet and Excel row behind its numbers. A numeric validator checks every narrative against the
tool results before it is shown.

**Access is by invitation.** The site on GitHub Pages holds only the app and the engine code. The
Reckitt extracts sit in a private Supabase storage bucket that only signed-in, invited users can read.
Public sign-ups are disabled.

## How it works

```
browser
 ├─ React app (web/src)            three-column planner view from the developer handoff
 └─ Web Worker → Pyodide (Python)  the same engine the tests run natively
     ├─ osb_agent   scope gate · slots · clarification · echo-back · tools · draft · validator
     ├─ osb_tools   WoC, attainment, capacity, PFR, decision tree, diff, PIR (golden-tested)
     └─ data bundle cleaned sheets with their file / sheet / row / hash citations,
                    downloaded after sign-in from the private Supabase bucket "osb-data"
```

- `engine/osb_tools` is the deterministic tool layer from `Cognify-Analytics/supplybot-reckitt` PR #1
  (commit `120b0d0`), with three additions:
  - `bundle.py` serves the cleaned sheets from the bundle.
  - `segment_demand`, `list_cut_lines` and `get_cut_line` are new tools.
  - The decision tree accepts a planner's yes or no to the 130% pacing question.
- `engine/osb_agent` is the agent pipeline from the demo PRD §8.1. The fixed responses are the
  Engineering appendix A.4 templates. Planner entries go to supplement sheets (MVP doc §3.3), are
  append-only and are audited.
- **No model is connected yet.** The scope gate and slot extraction run on deterministic rules, and
  narratives are drafted from tool values. The admin drawer says so. `osb_agent/models.py` is the seam
  where Claude goes.

## Setup

```bash
/opt/homebrew/bin/python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
cd web && npm install && cd ..
```

## Build and upload the data

Put the delivered workbooks in one folder. The file names can use spaces or underscores. You need:

1. Kinaxis Data
2. SAP Item Master Data
3. SAP Inventory Snapshots
4. Attainment information
5. Transportation costs
6. SAP Reason Codes
7. PFR Data

```bash
.venv/bin/python scripts/build_data.py --src ~/Downloads/reckitt-data   # writes .bundle/ (git-ignored)
.venv/bin/python scripts/upload_data.py                                 # to the private bucket
```

`build_data.py` reads every workbook through the schema contract, so a file whose headers changed is
rejected. It writes 8.6 MB of gzip CSVs plus a manifest with the load report. The first read of the
180 MB PFR file takes several minutes, and after that it is cached in `.cache/`.

`.env` (git-ignored) holds the Supabase settings:

- `SUPABASE_URL`
- `SUPABASE_ANON_KEY`: the publishable key, also in `web/.env`
- `SUPABASE_SERVICE_KEY`: secret, upload only
- `E2E_EMAIL` and `E2E_PASSWORD`: the browser-test account

## Supabase setup (done once)

- Project `one-supply-bot` (ref `acizwwhvtbuorrbhqxnb`).
- Private bucket `osb-data`. The policy `osb-data signed-in read` allows SELECT for the `authenticated`
  role only.
- **Auth → Sign In / Providers:** sign-ups are disabled. The site URL and redirect list hold the Pages
  URL and `localhost:5173` / `localhost:4173`.
- **To give someone access:** Authentication → Users → Invite user. The invite link opens the app's
  set-password screen.

## Run locally

```bash
cd web && npm run dev          # http://localhost:5173
```

## Tests

```bash
OSB_DATA_DIR=~/Downloads/reckitt-data PW_CHANNEL=chrome scripts/test_all.sh
```

The script runs these steps in order:

1. **Engine:** golden values from Engineering appendix E (S1–S5, items, lanes, PFR).
2. **Pipeline:** every planner flow from the handoff design, driven through JSON like the browser
   drives it.
3. **Bundle parity:** the golden checks re-run on the shipped bundle in a fresh interpreter.
4. **Production web build.**
5. **Stress:** about 100 varied requests. No request may crash, and every narrative must pass the
   validator.
6. **Playwright:** the built site in Chrome, signed in as the test account. Python boots in the
   browser and runs sign-in, S1, S3, S4, PIR, S5 and out-of-scope flows, then the in-browser golden
   checks.

Four Hygiene Storyline cases are expected failures (xfail). With appendix C's rule order, cases 3, 4, 6
and 10 give a different code from the storyline. This is a rule-precedence question for Prerak and
Rushi, not a code bug.

## Deploy to GitHub Pages

1. Create a repository, then push this folder to `main`.
2. Under **Settings → Pages → Source**, choose **GitHub Actions**.
3. `.github/workflows/pages.yml` runs the tests, builds with the right base path and deploys. The site
   is at `https://<user>.github.io/<repo>/`.

The workbooks are not in the repository, so the golden suites skip on GitHub Actions and the logic
tests still run. Run `scripts/test_all.sh` locally before you push.

## Connecting Claude later

`osb_agent/models.py` holds the routing table from MVP doc §9. To connect a model:

1. Replace `scope_and_slots(text)` with a Claude Haiku call that returns the same dict:
   `label`, `confidence`, `reason` and `slots`. The prompts are in Engineering appendix A.2 and A.3.
2. Set `CONNECTED = True`.

The scenarios, tools, templates and validator do not change. Keep the API key on a server, such as a
small proxy or the Databricks AI Gateway. Never put it in the browser bundle.

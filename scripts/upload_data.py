"""Upload the data bundle (.bundle/) to the private Supabase bucket the app reads after sign-in.

  .venv/bin/python scripts/upload_data.py

Needs SUPABASE_URL and SUPABASE_SERVICE_KEY in .env. The service key stays on this machine."""
import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUCKET = "osb-data"


def env():
    for line in (ROOT / ".env").read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())
    return os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"]


def put(url, key, name, data, ctype):
    req = urllib.request.Request(f"{url}/storage/v1/object/{BUCKET}/{name}", data=data, method="POST", headers={
        "apikey": key, "Authorization": f"Bearer {key}", "Content-Type": ctype, "x-upsert": "true",
        "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def main():
    url, key = env()
    src = ROOT / ".bundle"
    if not (src / "manifest.json").exists():
        sys.exit("No bundle: run scripts/build_data.py first")
    manifest = json.loads((src / "manifest.json").read_text())
    files = [m["file"] for m in manifest["frames"].values()]
    for f in files:                                   # data first, manifest last
        put(url, key, f, (src / f).read_bytes(), "application/gzip")
        print(f"uploaded {f}")
    put(url, key, "manifest.json", (src / "manifest.json").read_bytes(), "application/json")
    print(f"uploaded manifest.json ({len(files)} frames, built {manifest['created_at']})")


if __name__ == "__main__":
    main()

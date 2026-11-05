#!/usr/bin/env bash
# Every test, in order: engine (pytest, needs OSB_DATA_DIR for the golden suites), then the
# production web build and the browser end-to-end suite against it.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${OSB_DATA_DIR:?Set OSB_DATA_DIR to the folder with the delivered workbooks}"
export OSB_CACHE_DIR="${OSB_CACHE_DIR:-$PWD/.cache}"
echo "== engine tests"
(cd engine && ../.venv/bin/python -m pytest -q)
echo "== data bundle + engine package"
.venv/bin/python scripts/build_data.py --src "$OSB_DATA_DIR"
set -a; source .env; set +a
echo "== web build"
(cd web && npm run build)
echo "== browser end-to-end"
(cd web && npx playwright test)

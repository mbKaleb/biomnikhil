#!/usr/bin/env bash
# Mac dev runner — counterpart to run.ps1. First run sets up the venv;
# after that it just starts the server. Delete .venv to reset.
set -euo pipefail
cd "$(dirname "$0")"

# README says 3.11, but take the closest thing installed (3.14 is too new
# for some biomni deps — prefer an older interpreter when present)
PY=python3
for v in python3.11 python3.12 python3.13; do
  if command -v "$v" >/dev/null; then PY=$v; break; fi
done

if [ ! -d .venv ]; then
  "$PY" -m venv .venv
  ./.venv/bin/pip install --upgrade pip
fi

# idempotent + fast when everything is already satisfied (mirrors run.ps1)
./.venv/bin/pip install -q -r requirements.txt

if [ ! -f .env ]; then
  /bin/cp .env.example .env
  echo ">> Created .env from .env.example — add your API key, then re-run."
  exit 1
fi

source .venv/bin/activate
# waitress, not `flask run --debug` — the debug server's autoreloader
# restarts the whole process (silently killing any in-flight background
# task) on *any* source file edit, including packages the agent itself
# pip-installs mid-run. Matches run.ps1's Windows path.
# --threads: each open SSE trace stream pins a worker thread for the life
# of its task, so the default of 4 would stall the app with a few open tabs.
exec waitress-serve --listen=127.0.0.1:"${PORT:-8000}" --threads=16 wsgi:app

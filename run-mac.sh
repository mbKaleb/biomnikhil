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
exec flask --app wsgi run --port "${PORT:-8000}" --debug

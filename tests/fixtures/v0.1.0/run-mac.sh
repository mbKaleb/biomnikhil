#!/usr/bin/env bash
# Mac dev runner — counterpart to run.ps1. First run sets up the venv;
# after that it just starts the server. Delete .venv to reset.
set -euo pipefail
cd "$(dirname "$0")"

# Release installs (VERSION from the release zip, no .git folder) update
# themselves from the latest GitHub release before starting. .env, data/ and
# .venv aren't in the zip, so they're kept. BIOMNIKHIL_NO_UPDATE=1 skips this;
# git checkouts update with git instead.
if [ -f VERSION ] && [ ! -d .git ] && [ -z "${BIOMNIKHIL_NO_UPDATE:-}" ]; then
  current=$(cat VERSION)
  latest=$(curl -fs --max-time 10 https://api.github.com/repos/mbKaleb/biomnikhil/releases/latest \
    | sed -n 's/.*"tag_name": *"v\([^"]*\)".*/\1/p' | head -n 1) || latest=""
  if [ -n "$latest" ] && [ "$latest" != "$current" ]; then
    echo ">> Updating biomnikhil $current -> $latest"
    tmp=$(mktemp -d)
    if curl -fsSL --max-time 300 -o "$tmp/biomnikhil.zip" \
         https://github.com/mbKaleb/biomnikhil/releases/latest/download/biomnikhil.zip \
       && unzip -q "$tmp/biomnikhil.zip" -d "$tmp"; then
      cp -R "$tmp/biomnikhil/." .
      rm -rf "$tmp"
      exec bash ./run-mac.sh "$@"  # restart on the new script
    fi
    rm -rf "$tmp"
    echo ">> Update failed, starting $current."
  fi
fi

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
  echo ">> Created .env from .env.example — sign in with your Cognito account when the page opens."
fi

source .venv/bin/activate
# waitress, not `flask run --debug` — the debug server's autoreloader
# restarts the whole process (silently killing any in-flight background
# task) on *any* source file edit, including packages the agent itself
# pip-installs mid-run. Matches run.ps1's Windows path.
# --threads: each open SSE trace stream pins a worker thread for the life
# of its task, so the default of 4 would stall the app with a few open tabs.
exec waitress-serve --listen=127.0.0.1:"${PORT:-8000}" --threads=16 wsgi:app

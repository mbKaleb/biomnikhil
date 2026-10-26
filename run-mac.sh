#!/usr/bin/env bash
# Mac launcher, counterpart to run.ps1. Double-click "Start biomnikhil.command"
# or run `bash run-mac.sh`.
#
# The first run sets everything up inside this folder, explaining each step in
# this window and with pop-ups: a Python environment (.venv, plus a private
# Python in python/ when the Mac has no Python 3.11-3.13), the app's packages
# (pinned in requirements.lock) and .env. Later runs check for an update and
# start in seconds. Moving the folder makes the next run redo the setup.
#
#   BIOMNIKHIL_NO_UPDATE=1   skip the update check
#   BIOMNIKHIL_NO_DIALOGS=1  no pop-ups or prompt (the steps still print here)
#   BIOMNIKHIL_REINSTALL=1   redo the setup
set -euo pipefail
cd "$(dirname "$0")"
APP_DIR=$(pwd)
LOG="$APP_DIR/data/setup.log"
UV_VERSION=0.13.5  # uv supplies the private Python when the Mac has none

# --- pop-ups (text goes in as arguments, so quotes need no escaping) ------------
# Don't add `giving up after` to these: on macOS 27.2 a dialog with it never
# appears and osascript hangs.
confirm() {  # confirm "message": a pop-up, or Enter in this window without a GUI
  printf '\n%s\n\n' "$1"
  [ -n "${BIOMNIKHIL_NO_DIALOGS:-}" ] && return 0
  local out
  if out=$(osascript - "$1" 2>&1 <<'EOF'
on run argv
  display dialog (item 1 of argv) with title "biomnikhil setup" buttons {"Cancel", "Set up"} default button 2 cancel button 1 with icon note
end run
EOF
  ); then return 0; fi
  case "$out" in *-128*) printf 'Setup cancelled.\n'; exit 1 ;; esac
  # No pop-up possible (e.g. over SSH): ask here instead.
  if [ -t 0 ]; then read -r -p "Press Enter to set up, or close this window to cancel. " _; fi
}

fail() {  # fail "what went wrong" "what to do about it"
  printf '\n\n!! %s\n   %s\n' "$1" "$2" >&2
  if [ -z "${BIOMNIKHIL_NO_DIALOGS:-}" ]; then
    osascript - "$1" "$2" >/dev/null 2>&1 <<'EOF' &
on run argv
  display alert (item 1 of argv) message (item 2 of argv) as critical
end run
EOF
  fi
  exit 1
}

finished() {  # non-blocking: the app starts while it's shown
  [ -n "${BIOMNIKHIL_NO_DIALOGS:-}" ] && return 0
  osascript - "$1" >/dev/null 2>&1 <<'EOF' &
on run argv
  display dialog (item 1 of argv) with title "biomnikhil" buttons {"OK"} default button 1 with icon note
end run
EOF
}

step() {  # step 2/5 "Internet access"  ->  "[2/5] Internet access ........ "
  printf '[%s] %s %s ' "$1" "$2" "$(printf '%*s' $((22 - ${#2})) '' | tr ' ' .)"
}

# --- update: release installs (VERSION from the release zip, no .git folder)
# update themselves from the latest GitHub release. .env, data/, .venv and
# python/ aren't in the zip, so they're kept. Git checkouts update with git;
# test builds (versions like 0.2.0-rc1) stay on the build being tested.
if [ -f VERSION ] && [ ! -d .git ] && [ -z "${BIOMNIKHIL_NO_UPDATE:-}" ] \
   && [[ "$(cat VERSION)" != *-* ]]; then
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

# --- first-run setup -----------------------------------------------------------
# .biomnikhil-ready is written once the packages are in; pip's own launcher
# breaks when the folder moves, which also sends a run back through setup.
if [ -n "${BIOMNIKHIL_REINSTALL:-}" ] || [ ! -f .venv/.biomnikhil-ready ] \
   || ! .venv/bin/pip --version >/dev/null 2>&1; then
  echo "biomnikhil setup: everything installs inside"
  echo "  $APP_DIR"
  echo "The first run takes 5-15 minutes; later starts take seconds."
  echo
  mkdir -p data
  : > "$LOG"
  # Lets "Start biomnikhil.command" open with a double-click from now on.
  xattr -dr com.apple.quarantine "$APP_DIR" 2>/dev/null || true

  # Every package is built for this hardware and macOS (see requirements.lock).
  [ "$(uname -m)" = arm64 ] || fail "This Mac has an Intel processor." \
    "biomnikhil needs an Apple Silicon Mac (M1 or newer): one of its packages, pyopenms, isn't built for Intel Macs."
  macos=$(sw_vers -productVersion)
  [ "${macos%%.*}" -ge 15 ] || fail "macOS $macos is too old." \
    "biomnikhil needs macOS 15 or newer. Update macOS in System Settings, then start biomnikhil again."

  step 1/5 "Python 3.11-3.13"
  PY=""
  for cand in python3.13 python3.12 python3.11 python3; do
    p=$(command -v "$cand" 2>/dev/null) || continue
    # Apple's /usr/bin/python3 pops up a developer-tools installer when those
    # tools are missing; skip it then (it's Python 3.9 anyway).
    if [ "$p" = /usr/bin/python3 ] && ! xcode-select -p >/dev/null 2>&1; then continue; fi
    if "$p" -c 'import sys; sys.exit(not (3, 11) <= sys.version_info[:2] <= (3, 13))' 2>/dev/null; then
      PY=$p; break
    fi
  done
  if [ -n "$PY" ]; then
    pyver=$("$PY" --version 2>&1)
    echo "found $pyver ($PY)"
    python_plan="Use your $pyver"
  else
    echo "not found, a private copy will go in this folder"
    python_plan="Download a private copy of Python into this folder (about 40 MB)"
  fi

  free_gb=$(( $(df -Pk "$APP_DIR" | awk 'NR == 2 {print $4}') / 1024 / 1024 ))
  space_note=""
  [ "$free_gb" -ge 13 ] || space_note="

Only $free_gb GB is free on this disk; biomnikhil needs about 13 GB."
  confirm "biomnikhil will set itself up in this folder:
$APP_DIR

  - $python_plan
  - Download the app's packages (about 1 GB)
  - Later, the first analysis downloads the data lake (about 10 GB)
This takes 5-15 minutes the first time.$space_note"

  step 2/5 "Internet access"
  envfile=.env; [ -f .env ] || envfile=.env.example
  cognito=$(sed -n 's#^COGNITO_DOMAIN=\(https://\)\{0,1\}##p' "$envfile" | head -n 1)
  region=$(sed -n 's/^AWS_REGION=//p' "$envfile" | head -n 1)
  blocked=""
  for host in pypi.org files.pythonhosted.org github.com biomni-release.s3.amazonaws.com \
              ${cognito:+"$cognito"} "bedrock-mantle.${region:-us-west-2}.api.aws"; do
    code=$(curl -s -o /dev/null --max-time 10 -w '%{http_code}' "https://$host" || true)
    [ "$code" = 000 ] && blocked="$blocked  $host"
  done
  [ -z "$blocked" ] || fail "This computer can't reach:$blocked" \
    "biomnikhil downloads its packages and data from these sites and sends model requests to Bedrock. Lab and hospital networks sometimes block them: try another network, or ask IT to allow these sites."
  echo "ok"

  step 3/5 "Private Python"
  if [ -n "$PY" ]; then
    echo "not needed"
    "$PY" -m venv --clear .venv >>"$LOG" 2>&1 \
      || fail "Couldn't create the Python environment." "Details are in $LOG"
  else
    mkdir -p tools
    { curl -fsSL "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-aarch64-apple-darwin.tar.gz" \
        | tar -xz -C tools --strip-components 1; } >>"$LOG" 2>&1 \
      || fail "Couldn't download the Python installer (uv)." "Check your internet connection and start biomnikhil again. Details are in $LOG"
    UV_PYTHON_INSTALL_DIR="$APP_DIR/python" UV_CACHE_DIR="$APP_DIR/tools/cache" \
      tools/uv venv --clear --seed --managed-python --python 3.12 .venv >>"$LOG" 2>&1 \
      || fail "Couldn't install Python." "Check your internet connection and start biomnikhil again. Details are in $LOG"
    echo "installed $(.venv/bin/python --version 2>&1)"
  fi

  step 4/5 "App packages"
  echo "installing about 1 GB, this takes a few minutes..."
  .venv/bin/python -m pip install --no-cache-dir --disable-pip-version-check \
      -r requirements.lock --log "$LOG" \
    || fail "Installing the app's packages failed." \
      "Check your internet connection and start biomnikhil again; it picks up where it stopped. Details are in $LOG"
  touch .venv/.biomnikhil-ready

  step 5/5 "Settings"
  if [ -f .env ]; then echo "kept your .env"; else
    cp .env.example .env
    echo "created .env (no key needed, you sign in instead)"
  fi
  just_set_up=1
else
  # Installs anything an update added; takes a second when nothing changed.
  mkdir -p data
  .venv/bin/python -m pip install -q --no-cache-dir --disable-pip-version-check \
      -r requirements.lock --log "$LOG" \
    || echo ">> Couldn't update the app's packages (offline?), starting anyway."
  [ -f .env ] || cp .env.example .env
  just_set_up=""
fi

# --- start ---------------------------------------------------------------------
PORT=$(sed -n 's/^PORT=\([0-9][0-9]*\).*/\1/p' .env | head -n 1)
PORT=${PORT:-8000}
URL="http://localhost:$PORT"
if curl -s --max-time 2 "$URL/about" | grep -q biomnikhil; then
  echo; echo "biomnikhil is already running, opening $URL"
  open "$URL"; exit 0
fi
lsof -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1 && fail "Port $PORT is in use by another program." \
  "Close the other program that uses port $PORT, then start biomnikhil again."

echo
echo "biomnikhil is running at $URL"
echo "Keep this window open while you use it; closing it stops the app."
# Open the browser once the server answers.
( for _ in $(seq 1 90); do
    if curl -s --max-time 2 "$URL/about" | grep -q biomnikhil; then open "$URL"; break; fi
    sleep 1
  done ) &
[ -z "$just_set_up" ] || finished "Setup finished. biomnikhil is opening in your browser.

Keep the Terminal window open while you use it; closing it stops the app."

# The agent can pip-install packages mid-run; activating puts the venv's pip
# first on PATH.
source .venv/bin/activate
# waitress, not `flask run --debug` — the debug server's autoreloader
# restarts the whole process (silently killing any in-flight background
# task) on *any* source file edit, including packages the agent itself
# pip-installs mid-run. Matches run.ps1's Windows path.
# --threads: each open SSE trace stream pins a worker thread for the life
# of its task, so the default of 4 would stall the app with a few open tabs.
exec waitress-serve --listen=127.0.0.1:"$PORT" --threads=16 wsgi:app

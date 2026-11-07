#!/usr/bin/env bash
# Mac launcher, counterpart to run.ps1. Started by double-clicking
# setup-mac.command (or the biomnikhil app the installer creates), or
# `bash app/run-mac.sh`.
#
# Until biomnikhil is installed, this fetches uv (pinned below) into
# runtime/tools, uses it to make a small Python environment with pywebview,
# and runs the installer window (app/installer), which does the real setup.
# After that it checks for an update and opens the app window
# (app/installer/window.py), which runs the server in the background.
# setup-mac.command runs this away from its Terminal window and closes it.
# Moving the folder makes the next run install again.
#
#   BIOMNIKHIL_NO_UPDATE=1   skip the update check
#   BIOMNIKHIL_NO_DIALOGS=1  install and run in this window, no installer or app window
#   BIOMNIKHIL_REINSTALL=1   install again
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$(pwd)
VENV="$ROOT/runtime/.venv"
UV="$ROOT/runtime/tools/uv"
UV_VERSION=0.13.5
# SHA-256 of that release's uv-<arch>-apple-darwin.tar.gz; change with UV_VERSION.
UV_SHA256_ARM64=743f85035a1996ecb350659e01cd9faea9709829be09b1b4b03c73eaf3c9cd73
UV_SHA256_X86_64=b0f52b0e3945b4107d89024ea38e625683011fbfc05a0d98f9fa2d6dcee28c66
export UV_CACHE_DIR="$ROOT/runtime/tools/cache" UV_PYTHON_INSTALL_DIR="$ROOT/runtime/python"

fail() {  # fail "what went wrong" "what to do about it"
  printf '\n!! %s\n   %s\n' "$1" "$2" >&2
  # Don't add `giving up after`: on macOS 27.2 such a dialog never appears.
  [ -n "${BIOMNIKHIL_NO_DIALOGS:-}" ] || osascript - "$1" "$2" >/dev/null 2>&1 <<'OSA' &
on run argv
  display alert (item 1 of argv) message (item 2 of argv) as critical
end run
OSA
  exit 1
}

ensure_uv() {  # the pinned uv in runtime/tools
  [ "$("$UV" --version 2>/dev/null | cut -d ' ' -f 2)" != "$UV_VERSION" ] || return 0
  mkdir -p runtime/tools
  # This Mac's own build, so an Intel Mac still gets as far as the installer's
  # "not supported" message.
  local arch sha tarball
  arch=$(uname -m)
  if [ "$arch" = arm64 ]; then sha=$UV_SHA256_ARM64; else sha=$UV_SHA256_X86_64; fi
  tarball=runtime/tools/uv.tar.gz
  curl -fsSL --max-time 300 -o "$tarball" \
    "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-$(echo "$arch" | sed s/arm64/aarch64/)-apple-darwin.tar.gz"
  if [ "$(shasum -a 256 "$tarball" | cut -d ' ' -f 1)" != "$sha" ]; then
    rm -f "$tarball"
    echo "the downloaded uv doesn't match its pinned SHA-256" >&2
    return 1
  fi
  tar -xzf "$tarball" -C runtime/tools --strip-components 1
  rm -f "$tarball"
}

# --- update: release installs (app/VERSION from the release zip, no .git
# folder) update themselves from the latest GitHub release. data/ and runtime/
# aren't in the zip, so they're kept. Git checkouts update with git. Test builds
# (versions like 0.2.0-rc1) are only taken with "Beta releases" on in Settings. Only signed
# releases are taken: app/installer/update.py checks the signature, the version
# and the zip's checksum before it swaps any file in. It needs a Python, so a
# first run (nothing installed yet, and the zip was just downloaded) skips it.
IV=runtime/tools/installer  # the installer's Python environment, with pywebview; it runs the app window too
if [ -f app/VERSION ] && [ ! -d .git ] && [ -z "${BIOMNIKHIL_NO_UPDATE:-}" ]; then
  update_py=""
  for candidate in "$IV/bin/python" "$VENV/bin/python"; do
    if [ -x "$candidate" ]; then update_py=$candidate; break; fi
  done
  if [ -n "$update_py" ]; then
    code=0
    "$update_py" app/installer/update.py || code=$?
    [ "$code" != 0 ] || exec bash app/run-mac.sh "$@"  # restart on the new script
  fi
fi

# --- install -------------------------------------------------------------------
# The installer writes .biomnikhil-ready last; pip's own launcher breaks when
# the folder moves, which also sends a run back through the installer.
installer_env() {  # makes $IV unless it's there
  "$IV/bin/python" -c 'import webview' >/dev/null 2>&1 && return 0
  ensure_uv >>data/install.log 2>&1 || fail "Couldn't download the installer." \
    "Check your internet connection, then open setup-mac.command again."
  # An installed Python 3.11-3.13 if there is one, else uv's own 3.12. Without
  # the developer tools, Apple's python3 stub would pop up an installer for
  # them, so uv doesn't get to look at installed Pythons then.
  { xcode-select -p >/dev/null 2>&1 \
      && "$UV" venv -q --clear --python-preference only-system --python '>=3.11,<3.14' "$IV" 2>/dev/null; } \
    || "$UV" venv -q --clear --managed-python --python 3.12 "$IV" \
    || fail "Couldn't set up the installer." "Check your internet connection, then open setup-mac.command again."
  "$UV" pip install -q --python "$IV/bin/python" -r app/installer/requirements.lock \
    || fail "Couldn't set up the installer." "Check your internet connection, then open setup-mac.command again."
}

mkdir -p data
if [ -n "${BIOMNIKHIL_REINSTALL:-}" ] || [ ! -f "$VENV/.biomnikhil-ready" ] \
   || ! "$VENV/bin/pip" --version >/dev/null 2>&1; then
  echo "Preparing installer…"
  # Without a Terminal (the usual case), nothing else shows until the window opens.
  [ -t 1 ] || osascript -e 'display notification "Preparing setup…" with title "biomnikhil"' >/dev/null 2>&1 || true
  installer_env
  code=0
  "$IV/bin/python" app/installer || code=$?
  [ "$code" = 0 ] || exit $(( code == 3 ? 0 : 1 ))  # 3: installed, but not opening now
else
  # Installs anything an update added; takes a second when nothing changed.
  if ensure_uv >/dev/null 2>&1; then
    "$UV" pip install -q --python "$VENV/bin/python" -r app/requirements.lock
  else
    "$VENV/bin/python" -m pip install -q --disable-pip-version-check -r app/requirements.lock
  fi >>data/install.log 2>&1 || echo ">> Couldn't update the app's packages (offline?), starting anyway."
  [ -f data/.env ] || cp app/.env.example data/.env
  [ -n "${BIOMNIKHIL_NO_DIALOGS:-}" ] || installer_env
fi

# --- start ---------------------------------------------------------------------
PORT=$(sed -n 's/^PORT=\([0-9][0-9]*\).*/\1/p' data/.env | head -n 1)
PORT=${PORT:-8000}

if [ -z "${BIOMNIKHIL_NO_DIALOGS:-}" ]; then
  # The app window (app/installer/window.py) starts the server in the background
  # and stops it when the window closes. exec: from the app icon, the window
  # takes over the app's process, so the Dock shows biomnikhil.
  exec "$IV/bin/python" app/installer/window.py "$PORT" >>data/app.log 2>&1
fi

# Without dialogs: the server runs in this window, in a browser tab.
URL="http://localhost:$PORT"
if curl -s --max-time 2 "$URL/ping" | grep -q biomnikhil; then
  echo; echo "biomnikhil is already running, opening $URL"
  open "$URL"; exit 0
fi
lsof -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1 && fail "Port $PORT is in use by another program." \
  "Close the other program that uses port $PORT, then start biomnikhil again."
echo
echo "biomnikhil is running at $URL"
echo "Keep this window open while you use it; closing it stops the app."
( for _ in $(seq 1 90); do
    if curl -s --max-time 2 "$URL/ping" | grep -q biomnikhil; then open "$URL"; break; fi
    sleep 1
  done ) &
# The agent can pip-install packages mid-run; activating puts the venv's pip
# first on PATH. waitress and --threads: see app/installer/window.py.
source "$VENV/bin/activate"
exec waitress-serve --listen=127.0.0.1:"$PORT" --threads=16 app.wsgi:app

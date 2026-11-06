#!/bin/bash
# Double-click to set up and start biomnikhil. macOS opens Terminal for this
# file; it hands app/run-mac.sh off to run on its own (setsid, so it isn't tied
# to this window) and closes the window. Setup and the app show their own
# windows. BIOMNIKHIL_NO_DIALOGS=1 keeps everything in this window instead.
# It first copies app/ to ~/Library/biomnikhil and runs from there, so the
# unzipped folder can be deleted. A git checkout, or a folder that is already
# installed, runs where it is.
cd "$(dirname "$0")"
TARGET="$HOME/Library/biomnikhil"
if [ ! -d .git ] && [ ! -f runtime/.venv/.biomnikhil-ready ] && [ "$(pwd -P)" != "$TARGET" ]; then
  mkdir -p "$TARGET"
  if ! /usr/bin/rsync -a --delete app/ "$TARGET/app/"; then
    echo "biomnikhil setup couldn't copy its files to $TARGET."
    read -r -p "Press Enter to close. " _
    exit 1
  fi
  cd "$TARGET"
fi
if [ -n "${BIOMNIKHIL_NO_DIALOGS:-}" ] || [ ! -x /usr/bin/perl ]; then
  exec bash app/run-mac.sh
fi
mkdir -p data
/usr/bin/perl -MPOSIX -e 'POSIX::setsid(); exec @ARGV' bash app/run-mac.sh </dev/null >>data/app.log 2>&1 &
/usr/bin/perl -MPOSIX -e 'POSIX::setsid(); exec @ARGV' osascript - "$(tty)" >/dev/null 2>&1 <<'OSA' &
on run argv
  delay 0.5
  tell application "Terminal"
    repeat with w in windows
      if tty of selected tab of w is (item 1 of argv) then close w
    end repeat
  end tell
end run
OSA
exit 0

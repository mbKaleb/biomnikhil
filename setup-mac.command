#!/bin/bash
# Double-click to set up and start biomnikhil. macOS opens Terminal for this
# file; it hands app/run-mac.sh off to run on its own (setsid, so it isn't tied
# to this window) and closes the window. Setup and the app show their own
# windows. BIOMNIKHIL_NO_DIALOGS=1 keeps everything in this window instead.
# It first copies app/ to the folder you choose (~/Library/biomnikhil unless you
# change it) and runs from there, so the unzipped folder can be deleted. A git
# checkout, or a folder that is already installed, runs where it is. A finished
# install stays where it is, so running a newer zip doesn't ask again.
# BIOMNIKHIL_INSTALL_DIR=/some/folder installs in that folder without asking.
cd "$(dirname "$0")"
SOURCE="$(pwd -P)"
PHYSICAL_HOME="$(cd -P -- "$HOME" && pwd -P)"
DEFAULT_TARGET="$HOME/Library/biomnikhil"
# Where the next setup and the uninstaller find the install.
SAVED_LOCATION="$HOME/Library/biomnikhil-location"

is_install() {
  [ -n "$1" ] && [ -f "$1/app/installer/core.py" ] && [ -f "$1/app/config.py" ]
}

is_finished_install() {
  is_install "$1" && [ -f "$1/runtime/.venv/.biomnikhil-ready" ]
}

saved_target() {
  [ -f "$SAVED_LOCATION" ] || return 0
  local saved=""
  IFS= read -r saved <"$SAVED_LOCATION" || true
  if is_install "$saved"; then
    printf '%s\n' "$saved"
  fi
}

target_in() {  # always a biomnikhil folder, so an uninstall never takes the chosen folder itself
  local chosen="${1%/}"
  if [ -d "$chosen" ] && [ -n "$chosen" ]; then
    chosen="$(cd -P -- "$chosen" && pwd -P)"
  fi
  chosen="${chosen%/}"
  if [ "$(basename -- "${chosen:-/}")" = "biomnikhil" ]; then
    printf '%s\n' "$chosen"
  else
    printf '%s\n' "$chosen/biomnikhil"
  fi
}

location_problem() {  # prints why biomnikhil can't go here; nothing if it can
  local target="$1"
  local cloud="That folder syncs to the cloud, which would upload biomnikhil's files and lock them while it runs. Choose a folder that doesn't sync."
  case "$target" in
    /*) ;;
    *) echo "That isn't a full path."; return ;;
  esac
  local lowered
  lowered="$(printf '%s' "$target" | /usr/bin/tr '[:upper:]' '[:lower:]')"
  case "$lowered/" in
    */onedrive*|*/library/mobile\ documents/*|*/library/cloudstorage/*|*/dropbox*|*/google\ drive*|*/icloud\ drive*|*/box/*|*/box\ sync/*)
      echo "$cloud"; return ;;
  esac
  # macOS asks before an app opened from its icon reads these, separately from
  # Terminal, and iCloud can sync Desktop and Documents.
  case "$target/" in
    "$PHYSICAL_HOME/Documents/"*|"$PHYSICAL_HOME/Desktop/"*|"$PHYSICAL_HOME/Downloads/"*)
      echo "macOS protects your Documents, Desktop and Downloads folders, so biomnikhil couldn't open from its icon there. Choose another folder."; return ;;
  esac
  case "$target/" in
    "$SOURCE/"*)
      echo "That's inside the unzipped folder, which you can delete after setup. Choose a folder outside it."; return ;;
  esac
  if [ "$target" = "$PHYSICAL_HOME" ]; then
    echo "biomnikhil can't use your whole home folder. Choose a folder inside it."; return
  fi
  if [ -L "$target" ]; then
    echo "$target is a link to another folder. Choose the real folder."; return
  fi
  if [ -e "$target" ] && [ ! -d "$target" ]; then
    echo "There's already a file called $target."; return
  fi
  if [ -d "$target" ] && ! is_install "$target" && [ -n "$(/bin/ls -A "$target" 2>/dev/null)" ]; then
    echo "$target already has other files in it. Choose another folder."; return
  fi
  # The Python environment needs links, which some external drives can't hold.
  local existed=""
  if [ -d "$target" ]; then
    existed="yes"
  fi
  local probe="$target/.biomnikhil-write-check"
  if ! /bin/mkdir -p "$target" 2>/dev/null || ! /bin/ln -s check "$probe" 2>/dev/null; then
    echo "biomnikhil can't write there, or that drive can't hold its files. Choose a folder in your user folder or on a Mac-formatted drive."
    return
  fi
  /bin/rm -f "$probe"
  if [ -z "$existed" ]; then
    /bin/rmdir "$target" 2>/dev/null
  fi
}

nearest_folder() {
  local folder="$1"
  while [ ! -d "$folder" ]; do
    folder="$(dirname -- "$folder")"
  done
  printf '%s\n' "$folder"
}

free_space_text() {
  local gigabytes
  gigabytes="$(/bin/df -Pk "$(nearest_folder "$1")" 2>/dev/null | /usr/bin/awk 'NR==2 {print int($4/1048576)}')"
  if [ -n "$gigabytes" ]; then
    echo "$gigabytes GB free there."
  fi
}

location_dialog() {  # prints the button, or the error
  osascript - "$1" 2>&1 <<'OSA'
on run argv
  tell me to activate
  set answer to display dialog (item 1 of argv) with title "biomnikhil" buttons {"Cancel", "Choose Another Folder", "Install Here"} default button 3 cancel button 1 with icon note
  return button returned of answer
end run
OSA
}

folder_dialog() {  # prints the folder, or the error
  osascript - "$1" 2>&1 <<'OSA'
on run argv
  tell me to activate
  set chosen to choose folder with prompt "Choose where to put biomnikhil. It goes in a biomnikhil folder inside the one you choose." default location (POSIX file (item 1 of argv) as alias)
  return POSIX path of chosen
end run
OSA
}

problem_dialog() {
  osascript - "$1" >/dev/null 2>&1 <<'OSA'
on run argv
  tell me to activate
  display alert "biomnikhil can't install there" message (item 1 of argv) as warning
end run
OSA
}

ask_for_target() {  # prints the folder; fails if cancelled
  local target="$1"
  local answer chosen candidate problem
  while true; do
    answer="$(location_dialog "Install biomnikhil in this folder?

$target

It needs about 16 GB, with the data lake. $(free_space_text "$target")")"
    case "$answer" in
      "Install Here")
        printf '%s\n' "$target"
        return 0
        ;;
      "Choose Another Folder") ;;
      *-128*)
        return 1
        ;;
      *)
        # No dialogs here (a remote session, say), which shouldn't stop the install.
        printf '%s\n' "$target"
        return 0
        ;;
    esac
    chosen="$(folder_dialog "$(nearest_folder "$(dirname -- "$target")")")"
    case "$chosen" in
      /*) ;;
      *) continue ;;
    esac
    candidate="$(target_in "$chosen")"
    problem="$(location_problem "$candidate")"
    if [ -n "$problem" ]; then
      problem_dialog "$problem"
      continue
    fi
    target="$candidate"
  done
}

choose_target() {  # prints the folder; 1 if cancelled, 2 if the folder given can't be used
  local target problem saved
  if [ -n "${BIOMNIKHIL_INSTALL_DIR:-}" ]; then
    target="$(target_in "$BIOMNIKHIL_INSTALL_DIR")"
    problem="$(location_problem "$target")"
    if [ -n "$problem" ]; then
      echo "biomnikhil setup can't install in $target: $problem" >&2
      return 2
    fi
    printf '%s\n' "$target"
    return 0
  fi
  saved="$(saved_target)"
  if is_finished_install "$saved"; then
    printf '%s\n' "$saved"
    return 0
  fi
  if is_finished_install "$DEFAULT_TARGET"; then
    printf '%s\n' "$DEFAULT_TARGET"
    return 0
  fi
  target="${saved:-$DEFAULT_TARGET}"
  if [ -n "${BIOMNIKHIL_NO_DIALOGS:-}" ]; then
    printf '%s\n' "$target"
    return 0
  fi
  ask_for_target "$target"
}

close_this_window() {  # closes the Terminal window this runs in, once it has exited
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
}

if [ ! -d .git ] && [ ! -f runtime/.venv/.biomnikhil-ready ] && [ "$SOURCE" != "$DEFAULT_TARGET" ]; then
  code=0
  TARGET="$(choose_target)" || code=$?
  if [ "$code" = 1 ]; then
    echo "Setup cancelled. Nothing was changed."
    if [ -x /usr/bin/perl ]; then
      close_this_window
    fi
    exit 0
  fi
  if [ "$code" != 0 ]; then
    exit 1
  fi
  if [ "$SOURCE" != "$TARGET" ]; then
    mkdir -p "$TARGET"
    if ! /usr/bin/rsync -a --delete app/ "$TARGET/app/"; then
      echo "biomnikhil setup couldn't copy its files to $TARGET."
      read -r -p "Press Enter to close. " _
      exit 1
    fi
    if [ -f uninstall-mac.command ]; then
      /bin/cp -p uninstall-mac.command "$TARGET/" || true
    fi
    mkdir -p "$(dirname -- "$SAVED_LOCATION")"
    printf '%s\n' "$TARGET" >"$SAVED_LOCATION" 2>/dev/null || echo "Couldn't save where biomnikhil is installed."
    cd "$TARGET"
  fi
fi
if [ -n "${BIOMNIKHIL_NO_DIALOGS:-}" ] || [ ! -x /usr/bin/perl ]; then
  exec bash app/run-mac.sh
fi
mkdir -p data
/usr/bin/perl -MPOSIX -e 'POSIX::setsid(); exec @ARGV' bash app/run-mac.sh </dev/null >>data/app.log 2>&1 &
close_this_window
exit 0

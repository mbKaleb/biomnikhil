#!/bin/bash
# Double-click to remove biomnikhil from this Mac. It runs app/uninstall-mac.sh
# in this window, which lists everything first and asks before deleting.
launcher_folder="$(cd -P -- "$(dirname -- "$0")" && pwd -P)"
uninstaller="$launcher_folder/app/uninstall-mac.sh"
if [ ! -f "$uninstaller" ]; then
  echo "Can't find $uninstaller."
  exit 1
fi
/bin/bash "$uninstaller"
exit_code=$?
if [ -t 0 ]; then
  read -r -p "Press Enter to close. " _
fi
exit "$exit_code"

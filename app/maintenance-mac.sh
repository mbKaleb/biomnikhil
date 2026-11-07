#!/bin/bash
# Runs setup or cleanup for the Settings tab, once the app has closed itself
# (app/services/maintenance.py starts this, then closes the app):
#
#   bash app/maintenance-mac.sh setup     installs again, then opens the app
#   bash app/maintenance-mac.sh cleanup   app/uninstall-mac.sh --cleanup in this
#                                         Terminal window, then opens the app
#
# Everything is in main, so bash has read it all before it runs: cleanup can
# delete this file when the app runs from a stray copy.

reopen() {  # starts the app away from this window, if cleanup left it there
  if [ ! -f "$1/app/run-mac.sh" ]; then
    return
  fi
  /usr/bin/perl -MPOSIX -e 'POSIX::setsid(); exec @ARGV' bash "$1/app/run-mac.sh" </dev/null >>"$1/data/app.log" 2>&1 &
}

wait_until_closed() {
  local port
  port="$(/usr/bin/sed -n 's/^PORT=\([0-9][0-9]*\).*/\1/p' "$1/data/.env" 2>/dev/null | /usr/bin/head -n 1)"
  local waited=0
  while [ "$waited" -lt 60 ]; do
    if ! /usr/bin/curl -s --noproxy '*' --max-time 2 "http://127.0.0.1:${port:-8000}/ping" 2>/dev/null | /usr/bin/grep -q biomnikhil \
       && ! /bin/ps -axo command= 2>/dev/null | /usr/bin/grep -E 'app/installer/window\.py|waitress-serve' | /usr/bin/grep -qv grep; then
      return
    fi
    /bin/sleep 1
    waited=$((waited + 1))
  done
}

main() {
  local action="$1"
  local root
  root="$(cd -P -- "$(dirname -- "$0")/.." && pwd -P)"
  cd "$HOME" || exit 1

  case "$action" in
    setup)
      wait_until_closed "$root"
      BIOMNIKHIL_REINSTALL=1 /bin/bash "$root/app/run-mac.sh" </dev/null >>"$root/data/app.log" 2>&1
      ;;
    cleanup)
      echo "Waiting for biomnikhil to close..."
      wait_until_closed "$root"
      /bin/bash "$root/app/uninstall-mac.sh" --cleanup
      echo
      read -r -p "Press Enter to close this window and open biomnikhil again. " _
      reopen "$root"
      ;;
    *)
      echo "Unknown action: $action. Use setup or cleanup."
      exit 1
      ;;
  esac
}

main "$@"; exit $?

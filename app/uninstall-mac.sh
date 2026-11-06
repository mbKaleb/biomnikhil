#!/usr/bin/env bash
# uninstall-mac.sh - removes biomnikhil from this Mac. Started by double-clicking
# uninstall-mac.command, or:
#   bash ~/Library/biomnikhil/app/uninstall-mac.sh
#
# It lists the exact paths and sizes, then needs a typed confirmation. Confirmation
# is read from standard input. It stays inside the rules below whatever it is told:
#   - it deletes only known names: the install's app/, data/, runtime/ and launchers,
#     and a lake's data_lake/, benchmark/ and .sizes.json, never the folder around them
#   - symlinks are removed, never followed, and rm doesn't cross mount points
#   - a lake that may be used by others (a shared folder, or a folder typed in,
#     which is what app/services/datalake.py treats as maybe shared) stays unless
#     its full path is typed at its own prompt
#   - a git checkout keeps its source: only untracked data/ and runtime/ can go
#   - it refuses to run while biomnikhil is running and never stops the app itself
#
# bash holds this file open, so deleting the folder it's in doesn't interrupt it.
#
# Exit codes: 0 finished, 1 something could not be removed, 2 refused, 3 cancelled.
#
# BIOMNIKHIL_UNINSTALL_TEST_SYSTEM_DIR is for the tests only: it replaces
# /Library/Application Support, the one location a temporary HOME can't redirect.

EXIT_FINISHED=0
EXIT_INCOMPLETE=1
EXIT_REFUSED=2
EXIT_CANCELLED=3

CONFIRM_WORD="REMOVE"

PLAN_PATHS=()       # what to delete, each one whole
PLAN_LABELS=()
CLEANUP_FOLDERS=()  # removed afterwards only if they ended up empty
ROOTS=()            # recognised install folders
PORTS=()            # where a running biomnikhil would answer
SEEN_LAKES=()
OWN_LAKE_PARENTS=()
SHARED_LAKE_DIRS=()
SHARED_LAKE_PARENTS=()
SHARED_LAKE_REMOVE_PARENT=()
NOTES=()            # told to the user, in the list and again at the end
FAILURES=()


# ---- paths ----------------------------------------------------------------------

canonical_folder() {  # the physical path of an existing folder, else nothing
  CDPATH='' cd -P -- "$1" 2>/dev/null && pwd -P
}

is_inside() {  # is_inside PATH FOLDER: PATH is FOLDER or something under it
  local path="$1"
  local folder="${2%/}"
  [ "$path" = "$folder" ] && return 0
  case "$path" in
    "$folder"/*) return 0 ;;
  esac
  return 1
}

has_dot_dot_segment() {
  case "/$1/" in
    */../*|*/./*) return 0 ;;
  esac
  return 1
}

is_protected() {  # a drive root, the home folder or a system folder
  local path="${1%/}"
  if [ -z "$path" ]; then
    return 0
  fi
  if is_inside "$HOME_FOLDER" "$path"; then
    return 0  # the home folder, or a folder that holds it
  fi
  local exact_folder
  for exact_folder in /Applications /Library "/Library/Application Support" /Users /Volumes \
                      /private /private/var /private/tmp /tmp /var /opt /cores /home /mnt \
                      "$HOME_FOLDER/Applications" "$HOME_FOLDER/Desktop" "$HOME_FOLDER/Documents" \
                      "$HOME_FOLDER/Downloads" "$HOME_FOLDER/Library" \
                      "$HOME_FOLDER/Library/Application Support" \
                      "$HOME_FOLDER/Movies" "$HOME_FOLDER/Music" "$HOME_FOLDER/Pictures" \
                      "$HOME_FOLDER/Public"; do
    if [ "$path" = "$exact_folder" ]; then
      return 0
    fi
  done
  local system_folder
  for system_folder in /System /bin /sbin /usr /etc /dev /private/etc; do
    if is_inside "$path" "$system_folder"; then
      return 0
    fi
  done
  case "$path" in
    /Volumes/*/*) ;;
    /Volumes/*) return 0 ;;  # a whole drive
  esac
  return 1
}

add_note() {
  NOTES+=("$1")
}

add_to_plan() {  # add_to_plan PATH LABEL: PATH exists and its parent is physical
  PLAN_PATHS+=("$1")
  PLAN_LABELS+=("$2")
}

is_covered_by_plan() {
  local planned
  for planned in "${PLAN_PATHS[@]}"; do
    if is_inside "$1" "$planned"; then
      return 0
    fi
  done
  return 1
}


# ---- reading an install's settings ----------------------------------------------

read_env_value() {  # read_env_value FILE KEY: the value, as python-dotenv reads it
  local file="$1"
  local key="$2"
  local value=""
  local line
  [ -f "$file" ] || return 0
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%$'\r'}"
    if [[ "$line" =~ ^[[:space:]]*(export[[:space:]]+)?([A-Za-z_][A-Za-z0-9_]*)[[:space:]]*=[[:space:]]*(.*)$ ]]; then
      if [ "${BASH_REMATCH[2]}" = "$key" ]; then
        value="${BASH_REMATCH[3]}"
      fi
    fi
  done < "$file"
  case "$value" in
    \"*\"*)
      value="${value#\"}"
      value="${value%%\"*}"
      ;;
    \'*\'*)
      value="${value#\'}"
      value="${value%%\'*}"
      ;;
    *)
      value="${value%% #*}"
      value="${value%"${value##*[![:space:]]}"}"
      ;;
  esac
  printf '%s' "$value"
}

saved_location_field() {  # saved_location_field FILE KEYPATH
  /usr/bin/plutil -extract "$2" raw -o - -- "$1" 2>/dev/null
}


# ---- what is an install -----------------------------------------------------------

is_recognised_install() {
  local root="$1"
  if [ -f "$root/app/installer/core.py" ] && [ -f "$root/app/config.py" ]; then
    return 0
  fi
  [ -f "$root/runtime/.venv/.biomnikhil-ready" ] && return 0
  [ -f "$root/data/datalake-location.json" ] && return 0
  return 1
}

git_tracked_files() {  # lists tracked files under the arguments; fails without a usable git
  local root="$1"
  shift
  command -v git >/dev/null 2>&1 || return 1
  # A Mac without the developer tools has a git stub that opens an installer.
  xcode-select -p >/dev/null 2>&1 || [ -x /usr/local/bin/git ] || [ -x /opt/homebrew/bin/git ] || return 1
  git -C "$root" ls-files -- "$@" 2>/dev/null
}

inside_someone_elses_repository() {  # a parent folder is a git checkout tracking this folder
  local root="$1"
  local folder
  folder="$(dirname -- "$root")"
  while [ "$folder" != "/" ]; do
    if [ -e "$folder/.git" ]; then
      local tracked
      if ! tracked="$(git_tracked_files "$root" .)"; then
        return 0
      fi
      [ -n "$tracked" ] && return 0
      return 1
    fi
    folder="$(dirname -- "$folder")"
  done
  return 1
}

plan_checkout() {  # only data/ and runtime/, and only if git says they're untracked
  local root="$1"
  add_note "$root is a git checkout: its source stays. Only untracked data/ and runtime/ can be removed."
  local tracked
  if ! tracked="$(git_tracked_files "$root" data runtime)"; then
    add_note "git isn't available to check $root, so its data/ and runtime/ stay too."
    return
  fi
  if [ -n "$tracked" ]; then
    add_note "git tracks files in data/ or runtime/ of $root, so they stay."
    return
  fi
  plan_install_entry "$root" data "settings, chats, uploads, logs and any data lake inside"
  plan_install_entry "$root" runtime "Python, uv and download cache"
}

plan_install_entry() {
  local root="$1"
  local name="$2"
  local label="$3"
  if [ -e "$root/$name" ] || [ -L "$root/$name" ]; then
    add_to_plan "$root/$name" "$label"
  fi
}

plan_release_install() {
  local root="$1"
  plan_install_entry "$root" app "the program"
  plan_install_entry "$root" data "settings, chats, uploads, logs, browser storage and any data lake inside"
  plan_install_entry "$root" runtime "Python, uv and download cache"
  local name
  for name in setup.log setup-mac.command setup-windows.cmd uninstall-mac.command uninstall-windows.cmd \
              LICENSE run-mac.sh run.ps1 agent-ideas.md shared-data-lake.md .DS_Store; do
    plan_install_entry "$root" "$name" "launcher or log"
  done
  CLEANUP_FOLDERS+=("$root")
}

consider_install() {  # consider_install PATH WHERE
  local given="$1"
  local where="$2"
  [ -n "$given" ] || return
  if [ -L "$given" ]; then
    add_note "Left alone, because it's a symlink: $given"
    return
  fi
  [ -d "$given" ] || return
  local root
  root="$(canonical_folder "$given")"
  [ -n "$root" ] || return
  local known_root
  for known_root in "${ROOTS[@]}"; do
    if [ "$known_root" = "$root" ]; then
      return
    fi
  done
  if is_protected "$root"; then
    add_note "Refused, because it's the home folder or a system folder: $root"
    return
  fi

  if [ -e "$root/.git" ]; then
    ROOTS+=("$root")
    plan_checkout "$root"
    return
  fi
  if ! is_recognised_install "$root"; then
    add_note "Left alone, because it doesn't look like a biomnikhil install ($where): $root"
    return
  fi
  if inside_someone_elses_repository "$root"; then
    ROOTS+=("$root")
    plan_checkout "$root"
    return
  fi
  ROOTS+=("$root")
  plan_release_install "$root"
}


# ---- data lakes -------------------------------------------------------------------

is_own_lake_parent() {
  local own
  for own in "${OWN_LAKE_PARENTS[@]}"; do
    if [ "$own" = "$1" ]; then
      return 0
    fi
  done
  return 1
}

plan_lake_contents() {  # the app's own names only; anything else in the lake stays
  local lake_dir="$1"
  local name
  for name in data_lake benchmark .sizes.json .DS_Store; do
    if [ -e "$lake_dir/$name" ] || [ -L "$lake_dir/$name" ]; then
      add_to_plan "$lake_dir/$name" "data lake"
    fi
  done
  CLEANUP_FOLDERS+=("$lake_dir")
}

consider_lake() {  # consider_lake FOLDER SOURCE KIND: KIND is own, system or chosen
  local given="$1"
  local source_name="$2"
  local kind="$3"
  [ -n "$given" ] || return
  case "$given" in
    /*) ;;
    *)
      add_note "Ignored a data lake path that isn't absolute ($source_name): $given"
      return
      ;;
  esac
  if has_dot_dot_segment "$given"; then
    add_note "Ignored a data lake path with . or .. in it ($source_name): $given"
    return
  fi
  if [ ! -d "$given" ]; then
    if [ "$kind" != "own" ]; then
      add_note "Data lake folder not found, so nothing was removed there; reconnect its drive if it's still in use ($source_name): $given"
    fi
    return
  fi
  local parent
  parent="$(canonical_folder "$given")"
  [ -n "$parent" ] || return
  local lake_dir="${parent%/}/biomni_data"
  if [ -L "$lake_dir" ]; then
    add_note "Left alone, because it's a symlink: $lake_dir"
    return
  fi
  [ -d "$lake_dir" ] || return
  if is_protected "$lake_dir"; then
    add_note "Refused, because it's a protected folder: $lake_dir"
    return
  fi
  if is_covered_by_plan "$lake_dir"; then
    return
  fi
  local seen
  for seen in "${SEEN_LAKES[@]}"; do
    if [ "$seen" = "$lake_dir" ]; then
      return
    fi
  done
  SEEN_LAKES+=("$lake_dir")

  local recognised=""
  local name
  for name in data_lake benchmark; do
    if [ -d "$lake_dir/$name" ] && [ ! -L "$lake_dir/$name" ]; then
      recognised="yes"
    fi
  done
  if [ -z "$recognised" ]; then
    add_note "Left alone, because it doesn't hold data_lake/ or benchmark/: $lake_dir"
    return
  fi

  if [ "$kind" != "system" ] && is_own_lake_parent "$parent"; then
    plan_lake_contents "$lake_dir"
    # A preset folder exists only for the lake. Its parent goes if nothing else is in it.
    if [ "$parent" != "$HOME_FOLDER" ]; then
      CLEANUP_FOLDERS+=("$parent")
    fi
    return
  fi
  SHARED_LAKE_DIRS+=("$lake_dir")
  SHARED_LAKE_PARENTS+=("$parent")
  if [ "$kind" = "system" ]; then
    SHARED_LAKE_REMOVE_PARENT+=("yes")
  else
    SHARED_LAKE_REMOVE_PARENT+=("no")
  fi
}

consider_saved_lakes() {
  local root="$1"
  local location_file="$root/data/datalake-location.json"
  [ -f "$location_file" ] || return
  local saved_path
  local saved_kind
  saved_path="$(saved_location_field "$location_file" path)"
  saved_kind="$(saved_location_field "$location_file" kind)"
  if [ -z "$saved_path" ]; then
    add_note "Couldn't read $location_file. A data lake it names may remain."
    return
  fi
  if [ "$saved_kind" = "system" ]; then
    consider_lake "$saved_path" "the shared folder saved in the app" system
    return
  fi
  consider_lake "$saved_path" "the folder saved in the app" chosen
}

consider_previous_lakes() {
  local root="$1"
  local location_file="$root/data/datalake-location.json"
  [ -f "$location_file" ] || return
  local count
  count="$(saved_location_field "$location_file" previous)"
  case "$count" in
    ''|*[!0-9]*) return ;;
  esac
  local index=0
  while [ "$index" -lt "$count" ]; do
    consider_lake "$(saved_location_field "$location_file" "previous.$index")" "a folder used earlier" chosen
    index=$((index + 1))
  done
}

note_outside_data_paths() {  # upload and chat folders moved out of the install stay
  local root="$1"
  local key
  for key in BIOMNI_UPLOAD_PATH BIOMNI_CHAT_DB BIOMNI_OUTPUT_PATH BIOMNI_INPUT_PATH BIOMNI_HARNESS_PATH; do
    local value
    value="$(read_env_value "$root/data/.env" "$key")"
    if [ -n "$value" ] && ! is_inside "$value" "$root"; then
      add_note "Left alone, because data/.env points $key outside the install: $value"
    fi
  done
}

find_lakes() {
  local system_support="${BIOMNIKHIL_UNINSTALL_TEST_SYSTEM_DIR:-/Library/Application Support}"
  local default_parent="$HOME_FOLDER/biomnikhil-data"
  local app_data_parent="$HOME_FOLDER/Library/Application Support/biomnikhil"
  local system_parent="$system_support/biomnikhil"

  local preset
  for preset in "$default_parent" "$app_data_parent"; do
    if [ -d "$preset" ]; then
      OWN_LAKE_PARENTS+=("$(canonical_folder "$preset")")
    fi
  done
  local root
  for root in "${ROOTS[@]}"; do
    OWN_LAKE_PARENTS+=("$root/data")
  done

  # Shared locations first, so a path saved as shared is never taken for an own one.
  consider_lake "$system_parent" "the shared folder preset" system
  for root in "${ROOTS[@]}"; do
    consider_saved_lakes "$root"
  done
  for root in "${ROOTS[@]}"; do
    consider_previous_lakes "$root"
    consider_lake "$(read_env_value "$root/data/.env" BIOMNI_DATA_PATH)" "BIOMNI_DATA_PATH in data/.env" chosen
    note_outside_data_paths "$root"
  done
  consider_lake "${BIOMNI_DATA_PATH:-}" "the BIOMNI_DATA_PATH environment variable" chosen
  consider_lake "$default_parent" "the default location" own
  consider_lake "$app_data_parent" "the app data location" own
  for root in "${ROOTS[@]}"; do
    consider_lake "$root/data" "the app folder" own
  done
}


# ---- shortcuts --------------------------------------------------------------------

find_shortcuts() {
  local app_bundle="$HOME_FOLDER/Applications/biomnikhil.app"
  if [ -L "$app_bundle" ]; then
    add_note "Left alone, because it's a symlink: $app_bundle"
  elif [ -d "$app_bundle" ]; then
    if /usr/bin/grep -q 'edu.ucla.biomnikhil' "$app_bundle/Contents/Info.plist" 2>/dev/null; then
      add_to_plan "$app_bundle" "app shortcut"
    else
      add_note "Left alone, because it isn't the app biomnikhil made: $app_bundle"
    fi
  fi

  local desktop_alias="$HOME_FOLDER/Desktop/biomnikhil.app"
  if [ -L "$desktop_alias" ]; then
    local link_target
    link_target="$(readlink -- "$desktop_alias")"
    if [ "$link_target" = "$app_bundle" ] || [ "$link_target" = "$HOME/Applications/biomnikhil.app" ]; then
      add_to_plan "$desktop_alias" "Desktop alias"
    else
      add_note "Left alone, because it points somewhere else: $desktop_alias"
    fi
  elif [ -e "$desktop_alias" ]; then
    add_note "Left alone, because it isn't the alias biomnikhil made: $desktop_alias"
  fi

  local uninstall_shortcut="$HOME_FOLDER/Applications/Uninstall biomnikhil.command"
  if [ -f "$uninstall_shortcut" ] && [ ! -L "$uninstall_shortcut" ]; then
    if /usr/bin/grep -q 'biomnikhil uninstaller shortcut' "$uninstall_shortcut" 2>/dev/null; then
      add_to_plan "$uninstall_shortcut" "uninstaller shortcut"
    fi
  fi
}


# ---- is it running ----------------------------------------------------------------

find_ports() {
  PORTS=()
  local root
  for root in "${ROOTS[@]}"; do
    local port
    port="$(read_env_value "$root/data/.env" PORT)"
    case "$port" in
      ''|*[!0-9]*) port=8000 ;;
    esac
    PORTS+=("$port")
  done
}

running_reason() {  # prints why biomnikhil seems to be running; nothing if it isn't
  local port
  for port in "${PORTS[@]}"; do
    if /usr/bin/curl -s --noproxy '*' --max-time 2 "http://127.0.0.1:$port/ping" 2>/dev/null | /usr/bin/grep -q biomnikhil; then
      echo "it answers on port $port"
      return
    fi
  done

  local process_list
  if ! process_list="$(/bin/ps -axo pid=,command= 2>/dev/null)"; then
    echo "the list of running programs could not be read"
    return
  fi
  local pid
  local command_line
  while read -r pid command_line; do
    [ -n "$pid" ] || continue
    [ "$pid" != "$$" ] || continue
    case "$command_line" in
      *uninstall-mac*|*"Uninstall biomnikhil"*) continue ;;
    esac
    local root
    for root in "${ROOTS[@]}"; do
      case "$command_line" in
        *"$root/"*)
          echo "process $pid: $command_line"
          return
          ;;
      esac
    done
    # The launchers start the window with a path relative to the install.
    case "$command_line" in
      *app/installer*|*waitress-serve*|*run-mac.sh*)
        local working_folder
        working_folder="$(/usr/sbin/lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | /usr/bin/sed -n 's/^n//p')"
        for root in "${ROOTS[@]}"; do
          if [ -n "$working_folder" ] && is_inside "$working_folder" "$root"; then
            echo "process $pid: $command_line"
            return
          fi
        done
        ;;
    esac
  done <<< "$process_list"
}

refuse_if_running() {
  local reason
  reason="$(running_reason)"
  if [ -n "$reason" ]; then
    echo
    echo "biomnikhil is running, so nothing was removed ($reason)."
    echo "Close the biomnikhil window, then run this again."
    exit "$EXIT_REFUSED"
  fi
}


# ---- listing and asking -----------------------------------------------------------

size_of() {
  local kilobytes
  kilobytes="$(/usr/bin/du -sk -- "$1" 2>/dev/null | /usr/bin/cut -f 1)"
  case "$kilobytes" in
    ''|*[!0-9]*) kilobytes=0 ;;
  esac
  /usr/bin/awk -v kb="$kilobytes" 'BEGIN {
    if (kb >= 1048576) printf "%.1f GB", kb / 1048576
    else if (kb >= 1024) printf "%.1f MB", kb / 1024
    else printf "%d KB", kb
  }'
}

show_notes() {
  local note
  for note in "${NOTES[@]}"; do
    echo "  - $note"
  done
}

show_plan() {
  echo
  if [ "${#PLAN_PATHS[@]}" -eq 0 ]; then
    echo "Nothing would be removed without a separate confirmation."
    return
  fi
  echo "This will permanently delete:"
  local index=0
  while [ "$index" -lt "${#PLAN_PATHS[@]}" ]; do
    printf '  %10s  %s  (%s)\n' "$(size_of "${PLAN_PATHS[$index]}")" "${PLAN_PATHS[$index]}" "${PLAN_LABELS[$index]}"
    index=$((index + 1))
  done
}

read_answer() {
  ANSWER=""
  IFS= read -r ANSWER || true
  ANSWER="${ANSWER%$'\r'}"
}

ask_about_shared_lakes() {
  local index=0
  while [ "$index" -lt "${#SHARED_LAKE_DIRS[@]}" ]; do
    local lake_dir="${SHARED_LAKE_DIRS[$index]}"
    local parent="${SHARED_LAKE_PARENTS[$index]}"
    echo
    echo "This data lake may be used by other people or computers, so it is kept unless you say otherwise:"
    printf '  %10s  %s\n' "$(size_of "$lake_dir")" "$lake_dir"
    echo "To delete its data lake files, type its full path. Press Enter to keep it."
    printf '> '
    read_answer
    if [ "$ANSWER" = "$lake_dir" ]; then
      plan_lake_contents "$lake_dir"
      if [ "${SHARED_LAKE_REMOVE_PARENT[$index]}" = "yes" ]; then
        CLEANUP_FOLDERS+=("$parent")
      fi
    else
      add_note "Kept: $lake_dir"
    fi
    index=$((index + 1))
  done
}


# ---- deleting ---------------------------------------------------------------------

is_safe_to_delete() {
  local path="$1"
  [ -n "$path" ] || return 1
  case "$path" in
    /*) ;;
    *) return 1 ;;
  esac
  has_dot_dot_segment "$path" && return 1
  is_protected "$path" && return 1
  local parent
  parent="$(canonical_folder "$(dirname -- "$path")")"
  # Nothing on the way to it may have turned into a symlink.
  [ "${parent%/}/$(basename -- "$path")" = "$path" ]
}

delete_path() {
  local path="$1"
  if ! is_safe_to_delete "$path"; then
    FAILURES+=("$path (refused: not a path this uninstaller may delete)")
    return
  fi
  # -x: stay on this drive. rm removes a symlink itself, not what it points at.
  if ! /bin/rm -rfx -- "$path" 2>/dev/null; then
    if [ ! -L "$path" ]; then
      /bin/chmod -R u+rwx -- "$path" 2>/dev/null  # uv's cache holds read-only folders
    fi
    /bin/rm -rfx -- "$path" 2>/dev/null
  fi
  if [ -e "$path" ] || [ -L "$path" ]; then
    FAILURES+=("$path")
  fi
}

remove_if_empty() {
  local folder="$1"
  [ -d "$folder" ] || return 0
  [ ! -L "$folder" ] || return 0
  is_safe_to_delete "$folder" || return 0
  local listing
  listing="$(/bin/ls -A -- "$folder" 2>/dev/null)"
  if [ "$listing" = ".DS_Store" ] || [ "$listing" = ".biomnikhil-write-test" ]; then
    /bin/rm -f -- "$folder/$listing"
    listing=""
  fi
  if [ -n "$listing" ]; then
    add_note "Kept because it holds files biomnikhil didn't create: $folder"
    return 0
  fi
  if ! /bin/rmdir -- "$folder" 2>/dev/null; then
    FAILURES+=("$folder")
  fi
}

delete_everything() {
  local path
  for path in "${PLAN_PATHS[@]}"; do
    echo "Removing $path"
    delete_path "$path"
  done
  local folder
  for folder in "${CLEANUP_FOLDERS[@]}"; do
    remove_if_empty "$folder"
  done
}


# ---- main -------------------------------------------------------------------------

main() {
  local script_folder
  script_folder="$(canonical_folder "$(dirname -- "$0")")"
  local started_root
  started_root="$(canonical_folder "$script_folder/..")"
  cd / || exit "$EXIT_REFUSED"

  if [ -z "${HOME:-}" ] || [ ! -d "$HOME" ]; then
    echo "HOME isn't set to a folder, so biomnikhil's files can't be found safely. Nothing was removed."
    exit "$EXIT_REFUSED"
  fi
  case "$HOME" in
    /*) ;;
    *)
      echo "HOME isn't an absolute path, so biomnikhil's files can't be found safely. Nothing was removed."
      exit "$EXIT_REFUSED"
      ;;
  esac
  HOME_FOLDER="$(canonical_folder "$HOME")"
  if [ -z "$HOME_FOLDER" ] || [ "$HOME_FOLDER" = "/" ]; then
    echo "HOME can't be used safely. Nothing was removed."
    exit "$EXIT_REFUSED"
  fi

  echo "biomnikhil uninstaller"
  consider_install "$HOME_FOLDER/Library/biomnikhil" "the standard location"
  consider_install "$started_root" "the folder this was started from"
  find_lakes
  find_shortcuts
  find_ports

  add_note "macOS keeps the app window's sign-in cookies under Python's name (~/Library/WebKit/org.python.python, ~/Library/HTTPStorages/org.python.python*). Other Python programs share them, so they stay."

  if [ "${#PLAN_PATHS[@]}" -eq 0 ] && [ "${#SHARED_LAKE_DIRS[@]}" -eq 0 ]; then
    echo
    echo "No biomnikhil files were found, so nothing was removed."
    show_notes
    exit "$EXIT_FINISHED"
  fi

  refuse_if_running

  show_plan
  ask_about_shared_lakes
  show_plan
  echo
  if [ "${#NOTES[@]}" -gt 0 ]; then
    echo "Not removed:"
    show_notes
    echo
  fi
  if [ "${#PLAN_PATHS[@]}" -eq 0 ]; then
    echo "Nothing to remove."
    exit "$EXIT_FINISHED"
  fi

  echo "This can't be undone. Type $CONFIRM_WORD to delete everything listed above, or press Enter to cancel."
  printf '> '
  read_answer
  if [ "$ANSWER" != "$CONFIRM_WORD" ]; then
    echo "Cancelled. Nothing was removed."
    exit "$EXIT_CANCELLED"
  fi

  refuse_if_running  # it could have been opened while this was waiting
  delete_everything

  echo
  if [ "${#FAILURES[@]}" -gt 0 ]; then
    echo "biomnikhil was NOT completely removed. These could not be deleted:"
    local failure
    for failure in "${FAILURES[@]}"; do
      echo "  - $failure"
    done
    echo "Close anything using them, or ask an administrator if they need one, then run this again."
    exit "$EXIT_INCOMPLETE"
  fi
  echo "biomnikhil was removed."
  if [ "${#NOTES[@]}" -gt 0 ]; then
    echo "Not removed:"
    show_notes
  fi
  exit "$EXIT_FINISHED"
}

main "$@"

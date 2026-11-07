#!/usr/bin/env bash
# uninstall-mac.sh - removes biomnikhil from this Mac. Started by double-clicking
# uninstall-mac.command, or:
#   bash ~/Library/biomnikhil/app/uninstall-mac.sh [--uninstall | --cleanup]
#
# Without an option it asks which of the two to do.
#
# --uninstall lists the exact paths and sizes, then needs a typed confirmation.
# --cleanup searches for biomnikhil files outside ~/Library/biomnikhil and asks about
# each one separately: only "y" deletes, and anything else keeps it.
#
# Answers are read from standard input. Both stay inside the rules below whatever
# they are told:
#   - it deletes only known names: the install's app/, data/, runtime/ and launchers,
#     and a lake's data_lake/, benchmark/ and .sizes.json, never the folder around them
#   - symlinks are removed, never followed, and rm doesn't cross mount points
#   - a lake that may be used by others (a shared folder, or a folder typed in,
#     which is what app/services/datalake.py treats as maybe shared) stays unless
#     its full path is typed at its own prompt
#   - a git checkout keeps its source: only untracked data/ and runtime/ can go,
#     and --cleanup leaves a checkout and everything in it alone
#   - it refuses to run while biomnikhil is running and never stops the app itself
#
# bash holds this file open, so deleting the folder it's in doesn't interrupt it.
#
# Exit codes: 0 finished, 1 something could not be removed, 2 refused, 3 cancelled.
#
# BIOMNIKHIL_UNINSTALL_TEST_SYSTEM_DIR is for the tests only: it replaces
# /Library/Application Support, the one location a temporary HOME can't redirect.
# BIOMNIKHIL_UNINSTALL_TEST_SEARCH is too: it replaces the folders outside HOME that
# --cleanup searches.

EXIT_FINISHED=0
EXIT_INCOMPLETE=1
EXIT_REFUSED=2
EXIT_CANCELLED=3

CONFIRM_WORD="REMOVE"
WHERE_STANDARD="the standard location"
LABEL_PROGRAM="the program"
LABEL_UNINSTALLER="the uninstaller"

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

STANDARD_ROOT=""       # where the install belongs; --cleanup leaves it alone
STRAY_INSTALLS=()
STRAY_LAKES=()
STRAY_ZIPS=()
FOUND_LAKES=()         # biomni_data folders the search saw, before they're checked
CHECKED_ROOTS=()
SHORTCUT_ROOTS=()      # stray installs a shortcut starts
SEARCH_INCOMPLETE=""
DELETED_COUNT=0
KEPT_COUNT=0
ALL_FAILURES=()


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

add_cleanup_folder() {  # once each, or a folder kept for a reason would be reported twice
  local known
  for known in "${CLEANUP_FOLDERS[@]}"; do
    if [ "$known" = "$1" ]; then
      return
    fi
  done
  CLEANUP_FOLDERS+=("$1")
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
  # Variables like GIT_DIR would make git answer about some other repository.
  /usr/bin/env -u GIT_DIR -u GIT_WORK_TREE -u GIT_INDEX_FILE -u GIT_COMMON_DIR -u GIT_OBJECT_DIRECTORY \
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
  plan_install_entry "$root" app "$LABEL_PROGRAM"
  plan_install_entry "$root" data "settings, chats, uploads, logs, browser storage and any data lake inside"
  plan_install_entry "$root" runtime "Python, uv and download cache"
  plan_install_entry "$root" uninstall-mac.command "$LABEL_UNINSTALLER"
  plan_install_entry "$root" uninstall-windows.cmd "$LABEL_UNINSTALLER"
  local name
  for name in setup.log setup-mac.command setup-windows.cmd \
              LICENSE run-mac.sh run.ps1 agent-ideas.md shared-data-lake.md .DS_Store; do
    plan_install_entry "$root" "$name" "launcher or log"
  done
  # What releases up to v0.1.3 put at the top of the folder. The updater never removed these.
  for name in .env .env.example .gitignore .venv README.md "Start biomnikhil.cmd" "Start biomnikhil.command" \
              docs python requirements.lock requirements.txt scripts tests tools wsgi.py; do
    plan_install_entry "$root" "$name" "left by an older version"
  done
  # An update that stopped partway leaves its temporary copies.
  local leftover
  for leftover in "$root"/.*.update; do
    if [ -f "$leftover" ] && [ ! -L "$leftover" ]; then
      add_to_plan "$leftover" "left by an interrupted update"
    fi
  done
  add_cleanup_folder "$root"
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

  # The standard folder is ours by its name, so a setup or an uninstall that stopped
  # partway is still found. Only known names go, whatever else is in it.
  if [ "$where" != "$WHERE_STANDARD" ] && ! is_recognised_install "$root"; then
    add_note "Left alone, because it doesn't look like a biomnikhil install ($where): $root"
    return
  fi
  if [ -e "$root/.git" ]; then
    ROOTS+=("$root")
    plan_checkout "$root"
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
  add_cleanup_folder "$lake_dir"
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

  # Checked before the look of the lake, because a merge can leave only .sizes.json.
  if [ "$kind" != "system" ] && is_own_lake_parent "$parent"; then
    plan_lake_contents "$lake_dir"
    # A preset folder exists only for the lake. Its parent goes if nothing else is in it.
    if [ "$parent" != "$HOME_FOLDER" ]; then
      add_cleanup_folder "$parent"
    fi
    return
  fi

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
    # A symlink may lead to a shared drive, so what it points at needs its own prompt.
    if [ -d "$preset" ] && [ ! -L "$preset" ]; then
      OWN_LAKE_PARENTS+=("$(canonical_folder "$preset")")
    fi
  done
  local root

  # Shared locations first, so a path saved as shared is never taken for an own one.
  # Most computers have no shared folder, so its absence isn't worth a note.
  if [ -d "$system_parent" ]; then
    consider_lake "$system_parent" "the shared folder preset" system
  fi
  for root in "${ROOTS[@]}"; do
    consider_saved_lakes "$root"
  done
  for root in "${ROOTS[@]}"; do
    consider_previous_lakes "$root"
    consider_lake "$(read_env_value "$root/data/.env" BIOMNI_DATA_PATH)" "BIOMNI_DATA_PATH in data/.env" chosen
    consider_lake "$(read_env_value "$root/.env" BIOMNI_DATA_PATH)" "BIOMNI_DATA_PATH in an older version's .env" chosen
    note_outside_data_paths "$root"
  done
  consider_lake "${BIOMNI_DATA_PATH:-}" "the BIOMNI_DATA_PATH environment variable" chosen
  consider_lake "$default_parent" "the default location" own
  consider_lake "$app_data_parent" "the app data location" own
  for root in "${ROOTS[@]}"; do
    # A checkout whose data/ stays keeps the lake in it too.
    if is_covered_by_plan "$root/data"; then
      consider_lake "$root/data" "the app folder" own
    fi
  done
  # A merge empties a lake but leaves the folder around it.
  for preset in "$default_parent" "$app_data_parent"; do
    plan_emptied_preset "$preset"
  done
}

plan_emptied_preset() {
  local preset="$1"
  [ -d "$preset" ] || return 0
  [ ! -L "$preset" ] || return 0
  local parent
  parent="$(canonical_folder "$preset")"
  local lake_dir="$parent/biomni_data"
  local shared
  for shared in "${SHARED_LAKE_DIRS[@]}"; do
    if [ "$shared" = "$lake_dir" ]; then
      return 0  # not ours to tidy, and it's asked about on its own
    fi
  done
  add_cleanup_folder "$parent"
}


# ---- shortcuts --------------------------------------------------------------------

root_named_in_shortcut() {  # root_named_in_shortcut FILE SCRIPT: the install a shortcut starts
  local file="$1"
  local script="$2"
  [ -f "$file" ] || return 0
  [ ! -L "$file" ] || return 0
  local line
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      "exec /bin/bash "*) line="${line#exec /bin/bash }" ;;
      "/bin/bash "*) line="${line#/bin/bash }" ;;
      *) continue ;;
    esac
    # Undoes Python's shlex.quote, without ever running what the file says.
    local single_quote="'"
    local quoted_quote="'\"'\"'"
    case "$line" in
      "'"*"'")
        line="${line#\'}"
        line="${line%\'}"
        line="${line//$quoted_quote/$single_quote}"
        ;;
    esac
    case "$line" in
      /*"/app/$script") printf '%s' "${line%/app/"$script"}" ;;
    esac
    return 0
  done < "$file"
}

consider_installs_named_in_shortcuts() {  # an older install can be anywhere
  local applications="$HOME_FOLDER/Applications"
  if /usr/bin/grep -q 'edu.ucla.biomnikhil' "$applications/biomnikhil.app/Contents/Info.plist" 2>/dev/null \
     && [ ! -L "$applications/biomnikhil.app" ]; then
    consider_install "$(root_named_in_shortcut "$applications/biomnikhil.app/Contents/MacOS/biomnikhil" run-mac.sh)" \
                     "the folder the biomnikhil app starts"
  fi
  local uninstall_shortcut="$applications/Uninstall biomnikhil.command"
  if /usr/bin/grep -q 'biomnikhil uninstaller shortcut' "$uninstall_shortcut" 2>/dev/null; then
    consider_install "$(root_named_in_shortcut "$uninstall_shortcut" uninstall-mac.sh)" \
                     "the folder the uninstaller shortcut names"
  fi
}

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
      add_to_plan "$uninstall_shortcut" "$LABEL_UNINSTALLER"
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
      *uninstall-mac*|*"Uninstall biomnikhil"*|*maintenance-mac*|*cleanup-mac.command*) continue ;;
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
  if ! has_anything_to_remove; then
    echo "Nothing would be removed without a separate confirmation."
    return
  fi
  if [ "${#PLAN_PATHS[@]}" -gt 0 ]; then
    echo "This will permanently delete:"
  fi
  local index=0
  while [ "$index" -lt "${#PLAN_PATHS[@]}" ]; do
    printf '  %10s  %s  (%s)\n' "$(size_of "${PLAN_PATHS[$index]}")" "${PLAN_PATHS[$index]}" "${PLAN_LABELS[$index]}"
    index=$((index + 1))
  done
  if [ "${#CLEANUP_FOLDERS[@]}" -gt 0 ]; then
    echo "These folders go too, if nothing else is left in them:"
    local folder
    for folder in "${CLEANUP_FOLDERS[@]}"; do
      echo "  $folder"
    done
  fi
}

has_anything_to_remove() {
  [ "${#PLAN_PATHS[@]}" -gt 0 ] || [ "${#CLEANUP_FOLDERS[@]}" -gt 0 ]
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
        add_cleanup_folder "$parent"
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
  # Finder's file and the probe an older version's location picker left don't count.
  listing="$(/bin/ls -A -- "$folder" 2>/dev/null | /usr/bin/grep -vxE '\.DS_Store|\.biomnikhil-write-test')"
  if [ -z "$listing" ]; then
    /bin/rm -f -- "$folder/.DS_Store" "$folder/.biomnikhil-write-test"
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
  # The uninstaller itself goes last, and only if the rest went, so that "run this
  # again" still works after a failure.
  local index=0
  while [ "$index" -lt "${#PLAN_PATHS[@]}" ]; do
    if ! is_needed_to_run_again "${PLAN_LABELS[$index]}"; then
      echo "Removing ${PLAN_PATHS[$index]}"
      delete_path "${PLAN_PATHS[$index]}"
    fi
    index=$((index + 1))
  done
  if [ "${#FAILURES[@]}" -gt 0 ]; then
    return
  fi
  index=0
  while [ "$index" -lt "${#PLAN_PATHS[@]}" ]; do
    if is_needed_to_run_again "${PLAN_LABELS[$index]}"; then
      echo "Removing ${PLAN_PATHS[$index]}"
      delete_path "${PLAN_PATHS[$index]}"
    fi
    index=$((index + 1))
  done
  # Deepest first, so a folder isn't reported as kept for what's still to go inside it.
  local folder
  while IFS= read -r folder; do
    remove_if_empty "$folder"
  done < <(printf '%s\n' "${CLEANUP_FOLDERS[@]}" | /usr/bin/awk '{ print length($0) "\t" $0 }' | /usr/bin/sort -rn | /usr/bin/cut -f 2-)
}

is_needed_to_run_again() {
  [ "$1" = "$LABEL_PROGRAM" ] || [ "$1" = "$LABEL_UNINSTALLER" ]
}


# ---- cleanup ----------------------------------------------------------------------

is_listed() {  # is_listed VALUE ITEM...: VALUE is one of the items
  local value="$1"
  shift
  local item
  for item in "$@"; do
    if [ "$item" = "$value" ]; then
      return 0
    fi
  done
  return 1
}

is_in_checkout() {  # git checkouts run where they are. A home folder kept in git doesn't count.
  local folder="$1"
  while [ "$folder" != "/" ] && [ "$folder" != "$HOME_FOLDER" ]; do
    if [ -e "$folder/.git" ]; then
      return 0
    fi
    folder="$(dirname -- "$folder")"
  done
  return 1
}

consider_stray_install() {
  local given="$1"
  [ -n "$given" ] || return 0
  [ -d "$given" ] || return 0
  [ ! -L "$given" ] || return 0
  local root
  root="$(canonical_folder "$given")"
  [ -n "$root" ] || return 0
  if is_listed "$root" "${CHECKED_ROOTS[@]}"; then
    return 0
  fi
  CHECKED_ROOTS+=("$root")
  if is_inside "$root" "$STANDARD_ROOT"; then
    return 0
  fi
  is_recognised_install "$root" || return 0
  if is_protected "$root"; then
    add_note "Left alone, because it's the home folder or a system folder: $root"
    return 0
  fi
  if is_in_checkout "$root"; then
    add_note "Left alone, because it's in a git checkout: $root"
    return 0
  fi
  STRAY_INSTALLS+=("$root")
  ROOTS+=("$root")
}

consider_stray_lake() {
  local lake_dir="$1"
  if is_listed "$lake_dir" "${SEEN_LAKES[@]}"; then
    return 0
  fi
  SEEN_LAKES+=("$lake_dir")
  # A merge can leave only .sizes.json in a preset, so only other folders need the look of a lake.
  if ! is_own_lake_parent "$(dirname -- "$lake_dir")"; then
    if ! holds_lake_folders "$lake_dir"; then
      return 0
    fi
  fi
  if is_protected "$lake_dir"; then
    add_note "Left alone, because it's a protected folder: $lake_dir"
    return 0
  fi
  if is_in_checkout "$lake_dir"; then
    add_note "Left alone, because it's in a git checkout: $lake_dir"
    return 0
  fi
  STRAY_LAKES+=("$lake_dir")
}

holds_lake_folders() {
  local name
  for name in data_lake benchmark; do
    if [ -d "$1/$name" ] && [ ! -L "$1/$name" ]; then
      return 0
    fi
  done
  return 1
}

search_folder() {  # search_folder FOLDER DEPTH: never follows a symlink or leaves the drive
  local folder="$1"
  local depth="$2"
  [ -d "$folder" ] || return 0
  [ ! -L "$folder" ] || return 0
  local start
  start="$(canonical_folder "$folder")"
  [ -n "$start" ] || return 0
  local listing
  # Hidden folders and the media libraries can't hold an install, and reading the
  # libraries would ask for macOS permissions this has no use for.
  if ! listing="$(/usr/bin/find -x "$start" -mindepth 1 -maxdepth "$depth" \
      \( -type d \( -name '.*' -o -name node_modules -o -name __pycache__ -o -name site-packages \
         -o -path "$start/Library" -o -path "$start/Pictures" -o -path "$start/Movies" \
         -o -path "$start/Music" \) -prune \) \
      -o \( -type d \( -name app -o -name runtime -o -name data -o -name biomni_data \) -print \) \
      -o \( -type f -name 'biomnikhil*.zip' -print \) 2>/dev/null)"; then
    SEARCH_INCOMPLETE="yes"
  fi
  local found
  while IFS= read -r found; do
    [ -n "$found" ] || continue
    case "$found" in
      *.zip) add_stray_zip "$found" ;;
      */biomni_data) FOUND_LAKES+=("$found") ;;
      *) consider_stray_install "$(dirname -- "$found")" ;;
    esac
  done <<< "$listing"
}

add_stray_zip() {
  local zip="$1"
  if is_in_checkout "$zip"; then
    return 0
  fi
  STRAY_ZIPS+=("$zip")
}

consider_roots_named_in_shortcuts() {  # an install can be anywhere a shortcut says
  local applications="$HOME_FOLDER/Applications"
  local named=""
  if /usr/bin/grep -q 'edu.ucla.biomnikhil' "$applications/biomnikhil.app/Contents/Info.plist" 2>/dev/null \
     && [ ! -L "$applications/biomnikhil.app" ]; then
    named="$(root_named_in_shortcut "$applications/biomnikhil.app/Contents/MacOS/biomnikhil" run-mac.sh)"
    remember_shortcut_root "$named"
  fi
  local uninstall_shortcut="$applications/Uninstall biomnikhil.command"
  if /usr/bin/grep -q 'biomnikhil uninstaller shortcut' "$uninstall_shortcut" 2>/dev/null; then
    named="$(root_named_in_shortcut "$uninstall_shortcut" uninstall-mac.sh)"
    remember_shortcut_root "$named"
  fi
}

remember_shortcut_root() {
  local named="$1"
  [ -n "$named" ] || return 0
  consider_stray_install "$named"
  if [ -d "$named" ]; then
    SHORTCUT_ROOTS+=("$(canonical_folder "$named")")
  fi
}

find_strays() {
  local started_root="$1"
  local system_support="${BIOMNIKHIL_UNINSTALL_TEST_SYSTEM_DIR:-/Library/Application Support}"
  local default_parent="$HOME_FOLDER/biomnikhil-data"
  local app_data_parent="$HOME_FOLDER/Library/Application Support/biomnikhil"

  consider_stray_install "$started_root"
  consider_roots_named_in_shortcuts
  search_folder "$HOME_FOLDER" 6
  search_folder "$HOME_FOLDER/Library/CloudStorage" 6
  search_folder "$HOME_FOLDER/Library/Mobile Documents/com~apple~CloudDocs" 6
  if [ -n "${BIOMNIKHIL_UNINSTALL_TEST_SEARCH:-}" ]; then
    search_folder "$BIOMNIKHIL_UNINSTALL_TEST_SEARCH" 6
  else
    search_folder /Applications 3
    search_folder /Users/Shared 6
  fi

  # Each of these goes with its install, or is in use, so it is never asked about.
  local root
  for root in "$STANDARD_ROOT" "${STRAY_INSTALLS[@]}"; do
    SEEN_LAKES+=("$root/data/biomni_data")
  done
  remember_lake_in_use "$(read_env_value "$STANDARD_ROOT/data/.env" BIOMNI_DATA_PATH)"
  remember_lake_in_use "${BIOMNI_DATA_PATH:-}"

  # The presets belong to this user, so one question is enough for them.
  local preset
  for preset in "$default_parent" "$app_data_parent"; do
    if [ -d "$preset/biomni_data" ] && [ ! -L "$preset" ] && [ ! -L "$preset/biomni_data" ]; then
      local preset_parent
      preset_parent="$(canonical_folder "$preset")"
      OWN_LAKE_PARENTS+=("$preset_parent")
      consider_stray_lake "$preset_parent/biomni_data"
    fi
  done

  # A shared folder, or one saved in the settings, may be used by others: these
  # join the lakes that need their full path typed.
  if [ -d "$system_support/biomnikhil" ]; then
    consider_lake "$system_support/biomnikhil" "the shared folder preset" system
  fi
  for root in "$STANDARD_ROOT" "${STRAY_INSTALLS[@]}"; do
    consider_saved_lakes "$root"
    consider_previous_lakes "$root"
    consider_lake "$(read_env_value "$root/data/.env" BIOMNI_DATA_PATH)" "BIOMNI_DATA_PATH in data/.env" chosen
    consider_lake "$(read_env_value "$root/.env" BIOMNI_DATA_PATH)" "BIOMNI_DATA_PATH in an older version's .env" chosen
  done

  local lake_dir
  for lake_dir in "${FOUND_LAKES[@]}"; do
    consider_stray_lake "$lake_dir"
  done
}

remember_lake_in_use() {
  local parent="$1"
  [ -n "$parent" ] || return 0
  [ -d "$parent" ] || return 0
  local lake_dir
  lake_dir="$(canonical_folder "$parent")/biomni_data"
  if [ -d "$lake_dir" ]; then
    SEEN_LAKES+=("$lake_dir")
    add_note "Left alone, because BIOMNI_DATA_PATH points the app at it: $lake_dir"
  fi
}

reset_plan() {
  PLAN_PATHS=()
  PLAN_LABELS=()
  CLEANUP_FOLDERS=()
  FAILURES=()
}

delete_this_item() {
  local reason
  reason="$(running_reason)"
  if [ -n "$reason" ]; then
    echo
    echo "biomnikhil was opened, so the cleanup stopped here ($reason)."
    echo "Close the biomnikhil window, then run this again."
    exit "$EXIT_REFUSED"
  fi
  delete_everything
  if [ "${#FAILURES[@]}" -gt 0 ]; then
    ALL_FAILURES+=("${FAILURES[@]}")
    return 0
  fi
  DELETED_COUNT=$((DELETED_COUNT + 1))
}

ask_and_delete() {  # lists the plan as one item; only y deletes it
  show_plan
  echo "Type y to delete this. Press Enter to keep it."
  printf '> '
  read_answer
  case "$ANSWER" in
    y|Y|yes|Yes|YES)
      delete_this_item
      ;;
    *)
      echo "Kept."
      KEPT_COUNT=$((KEPT_COUNT + 1))
      ;;
  esac
}

offer_install() {
  local root="$1"
  [ -d "$root" ] || return 0
  reset_plan
  plan_release_install "$root"
  echo
  echo "A copy of biomnikhil outside the standard location ($STANDARD_ROOT):"
  echo "  $root"
  if is_listed "$root" "${SHORTCUT_ROOTS[@]}"; then
    echo "The biomnikhil shortcut starts this copy, so the shortcut stops working without it."
  fi
  ask_and_delete
}

offer_lake() {
  local lake_dir="$1"
  [ -d "$lake_dir" ] || return 0
  reset_plan
  plan_lake_contents "$lake_dir"
  local parent
  parent="$(dirname -- "$lake_dir")"
  # A preset folder exists only for the lake. Its parent goes if nothing else is in it.
  if is_own_lake_parent "$parent" && [ "$parent" != "$HOME_FOLDER" ] && [ "$parent" != "$STANDARD_ROOT" ]; then
    add_cleanup_folder "$parent"
  fi
  echo
  echo "A data lake outside the install. Other Biomni programs use this folder name too, so check it isn't theirs:"
  echo "  $lake_dir"
  ask_and_delete
}

offer_shared_lake() {  # offer_shared_lake INDEX: these need the full path typed
  local lake_dir="${SHARED_LAKE_DIRS[$1]}"
  [ -d "$lake_dir" ] || return 0
  if is_in_checkout "$lake_dir"; then
    add_note "Left alone, because it's in a git checkout: $lake_dir"
    return 0
  fi
  reset_plan
  plan_lake_contents "$lake_dir"
  if [ "${SHARED_LAKE_REMOVE_PARENT[$1]}" = "yes" ]; then
    add_cleanup_folder "${SHARED_LAKE_PARENTS[$1]}"
  fi
  echo
  echo "This data lake may be used by other people or computers, so it is kept unless you say otherwise:"
  show_plan
  echo "To delete its data lake files, type its full path: $lake_dir"
  echo "Press Enter to keep it."
  printf '> '
  read_answer
  if [ "$ANSWER" = "$lake_dir" ]; then
    delete_this_item
  else
    echo "Kept."
    KEPT_COUNT=$((KEPT_COUNT + 1))
  fi
}

offer_zip() {
  local zip="$1"
  [ -f "$zip" ] || return 0
  [ ! -L "$zip" ] || return 0
  reset_plan
  add_to_plan "$zip" "a downloaded installer"
  echo
  echo "A downloaded biomnikhil installer. Setup doesn't need it any more:"
  ask_and_delete
}

offer_dead_shortcuts() {  # shortcuts to a folder that's gone, which this cleanup can cause
  local applications="$HOME_FOLDER/Applications"
  local app_bundle="$applications/biomnikhil.app"
  local named=""
  if /usr/bin/grep -q 'edu.ucla.biomnikhil' "$app_bundle/Contents/Info.plist" 2>/dev/null \
     && [ ! -L "$app_bundle" ]; then
    named="$(root_named_in_shortcut "$app_bundle/Contents/MacOS/biomnikhil" run-mac.sh)"
    if [ -n "$named" ] && [ ! -f "$named/app/run-mac.sh" ]; then
      reset_plan
      add_to_plan "$app_bundle" "app shortcut"
      local desktop_alias="$HOME_FOLDER/Desktop/biomnikhil.app"
      if [ -L "$desktop_alias" ] && [ "$(readlink -- "$desktop_alias")" = "$app_bundle" ]; then
        add_to_plan "$desktop_alias" "Desktop alias"
      fi
      echo
      echo "A shortcut to a biomnikhil folder that no longer exists ($named):"
      ask_and_delete
    fi
  fi
  local uninstall_shortcut="$applications/Uninstall biomnikhil.command"
  if [ -f "$uninstall_shortcut" ] && [ ! -L "$uninstall_shortcut" ] \
     && /usr/bin/grep -q 'biomnikhil uninstaller shortcut' "$uninstall_shortcut" 2>/dev/null; then
    named="$(root_named_in_shortcut "$uninstall_shortcut" uninstall-mac.sh)"
    if [ -n "$named" ] && [ ! -f "$named/app/uninstall-mac.sh" ]; then
      reset_plan
      add_to_plan "$uninstall_shortcut" "$LABEL_UNINSTALLER"
      echo
      echo "A shortcut to a biomnikhil folder that no longer exists ($named):"
      ask_and_delete
    fi
  fi
}

run_cleanup() {
  local started_root="$1"
  echo
  echo "Searching for biomnikhil files outside $STANDARD_ROOT. This can take a minute."
  if [ -d "$STANDARD_ROOT" ]; then
    ROOTS+=("$STANDARD_ROOT")
  fi
  find_strays "$started_root"
  find_ports
  refuse_if_running

  local item
  for item in "${STRAY_INSTALLS[@]}"; do
    offer_install "$item"
  done
  for item in "${STRAY_LAKES[@]}"; do
    offer_lake "$item"
  done
  local index=0
  while [ "$index" -lt "${#SHARED_LAKE_DIRS[@]}" ]; do
    offer_shared_lake "$index"
    index=$((index + 1))
  done
  for item in "${STRAY_ZIPS[@]}"; do
    offer_zip "$item"
  done
  offer_dead_shortcuts

  echo
  if [ "$DELETED_COUNT" -eq 0 ] && [ "$KEPT_COUNT" -eq 0 ] && [ "${#ALL_FAILURES[@]}" -eq 0 ]; then
    echo "No stray biomnikhil files were found."
  else
    echo "Cleanup finished: $DELETED_COUNT deleted, $KEPT_COUNT kept."
  fi
  if [ -n "$SEARCH_INCOMPLETE" ]; then
    echo "Some folders couldn't be read, so stray files in them weren't found."
  fi
  if [ "${#NOTES[@]}" -gt 0 ]; then
    echo "Not removed:"
    show_notes
  fi
  if [ "${#ALL_FAILURES[@]}" -gt 0 ]; then
    echo "These could not be deleted:"
    local failure
    for failure in "${ALL_FAILURES[@]}"; do
      echo "  - $failure"
    done
    echo "Close anything using them, or ask an administrator if they need one, then run this again."
    exit "$EXIT_INCOMPLETE"
  fi
  exit "$EXIT_FINISHED"
}


# ---- main -------------------------------------------------------------------------

ask_what_to_do() {
  echo
  echo "  1  Uninstall: remove biomnikhil and its data from this computer"
  echo "  2  Clean up: find biomnikhil files outside where they belong, and ask about each one"
  echo "Type 1 or 2, or press Enter to cancel."
  printf '> '
  read_answer
  case "$ANSWER" in
    1) MODE="uninstall" ;;
    2) MODE="cleanup" ;;
    *)
      echo "Cancelled. Nothing was removed."
      exit "$EXIT_CANCELLED"
      ;;
  esac
}

main() {
  MODE=""
  case "$*" in
    "") ;;
    --uninstall) MODE="uninstall" ;;
    --cleanup) MODE="cleanup" ;;
    *)
      echo "Unknown option: $*. Use --uninstall or --cleanup. Nothing was removed."
      exit "$EXIT_REFUSED"
      ;;
  esac

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
  if [ -z "$MODE" ]; then
    ask_what_to_do
  fi
  if [ "$MODE" = "cleanup" ]; then
    STANDARD_ROOT="$HOME_FOLDER/Library/biomnikhil"
    if [ -d "$STANDARD_ROOT" ]; then
      STANDARD_ROOT="$(canonical_folder "$STANDARD_ROOT")"
    fi
    run_cleanup "$started_root"
  fi
  consider_install "$HOME_FOLDER/Library/biomnikhil" "$WHERE_STANDARD"
  consider_install "$started_root" "the folder this was started from"
  consider_installs_named_in_shortcuts
  find_lakes
  find_shortcuts
  find_ports

  add_note "macOS keeps the app window's sign-in cookies and cache under Python's name (~/Library/WebKit/org.python.python, ~/Library/HTTPStorages/org.python.python*, ~/Library/Caches/org.python.python). Other Python programs share them, so they stay."

  if ! has_anything_to_remove && [ "${#SHARED_LAKE_DIRS[@]}" -eq 0 ]; then
    echo
    echo "No biomnikhil files were found, so nothing was removed."
    show_notes
    exit "$EXIT_FINISHED"
  fi

  refuse_if_running

  show_plan
  if [ "${#SHARED_LAKE_DIRS[@]}" -gt 0 ]; then
    ask_about_shared_lakes
    show_plan
  fi
  echo
  if [ "${#NOTES[@]}" -gt 0 ]; then
    echo "Not removed:"
    show_notes
    echo
  fi
  if ! has_anything_to_remove; then
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

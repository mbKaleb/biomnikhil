"""Data-lake download state for the first-run popup.

Biomni downloads the ~14 GB data lake as a side effect of constructing A1,
so "downloading" here just means building the agent on a background thread
while we watch the data directory grow on disk.
"""
from __future__ import annotations

import filecmp
import json
import logging
import os
import shutil
import stat
import sys
import threading
from pathlib import Path

from .. import config
from ..config import Config

logger = logging.getLogger(__name__)

# Size of a complete data lake, for the progress percentage only —
# completeness itself is decided by the manifest check below, not this
# number. The download measures the real total (a HEAD request per manifest
# file, saved in .sizes.json); this is the fallback until it has, measured for
# biomni 0.0.8 (76 files, 14.0 GiB).
EXPECTED_BYTES = int(os.getenv("BIOMNI_DATA_LAKE_BYTES", str(14 * 1024**3)))

# Where biomni publishes the data lake — must match biomni.agent.A1's own
# s3_bucket_url. We re-fetch individual files ourselves (rather than relying on
# biomni.utils.check_and_download_s3_files) because biomni skips any file that
# merely *exists* — "if os.path.exists(...): continue" — so a truncated or
# corrupt file is never re-downloaded. Overridable only to follow a biomni
# release that moves the bucket.
S3_BUCKET_URL = os.getenv(
    "BIOMNI_S3_BUCKET_URL", "https://biomni-release.s3.amazonaws.com"
)
# Transient truncation happens; give each file a few tries before giving up.
_REDOWNLOAD_ATTEMPTS = 3

# A1 is constructed with path=Config.DATA_PATH and nests its own
# "biomni_data" subfolder there — that subfolder is the actual data lake.
# Config.DATA_PATH itself also holds data/user/ (uploads, chat db, agent
# run outputs) as of the data/ restructure — byte counts, file counts, and
# the integrity check must all stay scoped to biomni_data/ specifically,
# or an unrelated (and possibly legitimately empty) agent output file gets
# misreported as data-lake corruption, and size/file totals get wildly
# inflated by counting uploads/outputs that have nothing to do with it.
def _lake_dir() -> Path:
    return Config.DATA_PATH / "biomni_data"


def _manifest_complete(lake_dir: Path | None = None) -> bool | None:
    """The real "is the data lake done" check — mirrors exactly what
    biomni.agent.A1.__init__ itself checks before deciding whether to
    download: every filename in biomni.env_desc.data_lake_dict present
    under data_lake/, plus benchmark/hle/ existing. Ground truth, not a
    percent-of-a-guessed-total heuristic. Returns None if biomni isn't
    importable yet (data lake never downloaded, or biomni not installed) —
    caller should fall back to the byte heuristic in that case."""
    try:
        from biomni.env_desc import data_lake_dict  # tiny import — just a dict
    except Exception:  # noqa: BLE001 — biomni not installed/importable yet
        return None

    if lake_dir is None:
        lake_dir = _lake_dir()
    data_lake_subdir = lake_dir / "data_lake"
    if not data_lake_subdir.is_dir():
        return False
    if not all((data_lake_subdir / name).is_file() for name in data_lake_dict):
        return False
    return (lake_dir / "benchmark" / "hle").is_dir()


_state_lock = threading.Lock()
_download_thread: threading.Thread | None = None
_download_error: str | None = None

# File-integrity check: byte count alone can look "ready" (right size on
# disk) even when a file was truncated by an interrupted download or copy.
# Runs once in the background — cheap, since parquet is checked via its
# footer (metadata only, not a full read) and everything else is small.
_validate_lock = threading.Lock()
_validate_thread: threading.Thread | None = None
_validated = False
_valid: bool | None = None
_validation_error: str | None = None
# Relative paths (under the data lake dir, posix-style) of files that failed
# the last integrity check — the exact set the repair re-download targets, so
# a corrupt-but-present file actually gets replaced instead of skipped.
_invalid_files: list[str] = []

# Files the last merge left in a duplicate, and why, for the UI.
_merge_problems: list[dict] = []


def _check_file(path: Path) -> str | None:
    """Return None if the file looks structurally intact, else an error
    string. Cheap checks only — this isn't a full data-integrity audit."""
    suffix = path.suffix.lower()
    try:
        if suffix == ".parquet":
            import pyarrow.parquet as pq

            pq.ParquetFile(path)  # reads the footer; raises on truncation/corruption
        elif suffix == ".json":
            import json

            with open(path) as f:
                json.load(f)
        elif suffix == ".pkl":
            # Structure only: unpickling would run whatever the file contains.
            with open(path, "rb") as f:
                header = f.read(1)
                f.seek(-1, os.SEEK_END)
                last_byte = f.read(1)
            if header != b"\x80" or last_byte != b".":
                return "not a complete pickle file"
        else:  # csv/tsv/txt/obo/etc — just confirm it's non-empty and readable
            with open(path, "rb") as f:
                if not f.read(4096):
                    return "empty file"
    except Exception as exc:  # noqa: BLE001 — surfacing any read failure
        return f"{type(exc).__name__}: {exc}"
    return None


def _run_validation() -> None:
    global _validated, _valid, _validation_error
    errors: list[str] = []  # (relative_path, size_bytes, error_message)
    lake_dir = _lake_dir()
    checked = 0
    try:
        if not lake_dir.exists():
            errors.append(("(directory)", 0, f"data lake directory does not exist: {lake_dir}"))
        else:
            for root, _dirs, files in os.walk(lake_dir):
                for name in files:
                    if name.startswith("."):  # our .part downloads and .sizes.json
                        continue
                    path = Path(root) / name
                    checked += 1
                    err = _check_file(path)
                    if err:
                        try:
                            size = path.stat().st_size
                        except OSError:
                            size = -1
                        rel = path.relative_to(lake_dir)
                        errors.append((str(rel), size, err))
                        logger.warning(
                            "data lake integrity check failed: %s (%s bytes) — %s",
                            rel, size, err,
                        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("data lake validation crashed")
        errors.append(("(validation itself)", 0, f"{type(exc).__name__}: {exc}"))

    with _validate_lock:
        _validated = True
        _valid = not errors
        # Record the exact bad files (posix-relative) for the repair path.
        _invalid_files[:] = [rel for rel, _size, _err in errors]
        if not errors:
            _validation_error = None
        else:
            logger.warning(
                "data lake validation: %d/%d files failed", len(errors), checked
            )
            detail_limit = 15
            lines = [f"{len(errors)} of {checked} files failed integrity check:"]
            for rel, size, err in errors[:detail_limit]:
                size_str = f"{size:,} bytes" if size >= 0 else "size unknown"
                lines.append(f"  • {rel} ({size_str}): {err}")
            if len(errors) > detail_limit:
                lines.append(f"  … and {len(errors) - detail_limit} more (see server log)")
            _validation_error = "\n".join(lines)


def ensure_validated() -> None:
    """Kick off the background validation check if it hasn't run yet (and
    isn't already running). Idempotent — safe to call on every status poll."""
    global _validate_thread
    with _validate_lock:
        if _validated or (_validate_thread is not None and _validate_thread.is_alive()):
            return
        _validate_thread = threading.Thread(target=_run_validation, daemon=True)
        _validate_thread.start()


def _sizes_file() -> Path:
    return _lake_dir() / ".sizes.json"


def _expected_bytes() -> int:
    """The whole data lake's size as measured by _measure_sizes(), else
    EXPECTED_BYTES."""
    try:
        return int(json.loads(_sizes_file().read_text())["total"])
    except Exception:  # noqa: BLE001 — not measured yet
        return EXPECTED_BYTES


def _measure_sizes(filenames) -> None:
    """Record the data lake's total size, from S3's Content-Length for every
    manifest file, so progress counts against all of it — including files
    finished on an earlier run. Skipped once measured; best effort."""
    if _sizes_file().is_file():
        return
    import requests
    from concurrent.futures import ThreadPoolExecutor

    def head(name):
        r = requests.head(f"{S3_BUCKET_URL}/data_lake/{name}", timeout=20)
        r.raise_for_status()
        return int(r.headers["content-length"])

    try:
        with ThreadPoolExecutor(16) as pool:
            total = sum(pool.map(head, filenames))
        _sizes_file().write_text(json.dumps({"total": total}))
    except Exception:  # noqa: BLE001 — keep the EXPECTED_BYTES estimate
        logger.warning("couldn't measure the data lake's size", exc_info=True)


def _dir_stats(lake_dir: Path | None = None) -> tuple[int, int]:
    """(total bytes, file count) under the data lake directory specifically —
    not all of Config.DATA_PATH, which also holds unrelated user data."""
    total = 0
    count = 0
    if lake_dir is None:
        lake_dir = _lake_dir()
    if lake_dir.exists():
        for root, _dirs, files in os.walk(lake_dir):
            for name in files:
                try:
                    total += os.path.getsize(os.path.join(root, name))
                    count += 1
                except OSError:
                    continue
    return total, count


def _redownload_one(rel: str) -> str | None:
    """Re-fetch one data-lake file from S3 into place. Verifies the download
    against the S3 Content-Length and re-runs the structural integrity check
    before atomically replacing any existing (possibly truncated) copy.
    Returns None on success, else an error string; retries a few times since
    truncation is usually a transient network cutoff. A .part file left by an
    earlier run (the app closed mid-download) is continued with an HTTP Range
    request rather than started over."""
    import requests

    url = f"{S3_BUCKET_URL}/{rel}"
    dest = _lake_dir() / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.part")

    last_err = "unknown error"
    for attempt in range(1, _REDOWNLOAD_ATTEMPTS + 1):
        try:
            have = tmp.stat().st_size if tmp.is_file() else 0
            headers = {"Range": f"bytes={have}-"} if have else {}
            with requests.get(url, stream=True, timeout=(10, 300), headers=headers) as resp:
                if resp.status_code == 416:  # the .part already holds the whole file
                    expected = written = have
                else:
                    resp.raise_for_status()
                    if resp.status_code != 206:  # sent the whole file: start over
                        have = 0
                    length = int(resp.headers.get("content-length", 0)) or None
                    expected = have + length if length is not None else None
                    written = have
                    with open(tmp, "ab" if have else "wb") as f:
                        for chunk in resp.iter_content(chunk_size=1024 * 1024):
                            if chunk:
                                f.write(chunk)
                                written += len(chunk)
        except Exception as exc:  # noqa: BLE001 — network/IO failure: the .part
            # keeps what arrived, and the next attempt (or run) continues it.
            last_err = f"{type(exc).__name__}: {exc}"
            logger.warning("redownload %s attempt %d failed: %s", rel, attempt, last_err)
            continue
        # Size check first — this is the exact failure biomni misses: a
        # stream that cuts off early leaves a short-but-present file.
        if expected is not None and written < expected:
            last_err = f"stream ended early: got {written:,} of {expected:,} bytes"
            logger.warning("redownload %s attempt %d: %s", rel, attempt, last_err)
            continue  # resume from here on the next attempt
        if expected is not None and written > expected:
            last_err = f"size mismatch: got {written:,} of {expected:,} bytes"
            logger.warning("redownload %s attempt %d: %s", rel, attempt, last_err)
            tmp.unlink(missing_ok=True)
            continue
        structural = _check_file(tmp)
        if structural:
            last_err = f"still corrupt after download: {structural}"
            logger.warning("redownload %s attempt %d: %s", rel, attempt, last_err)
            tmp.unlink(missing_ok=True)
            continue
        os.replace(tmp, dest)  # atomic swap — never leaves a partial at dest
        logger.info("repaired data lake file %s (%s bytes)", rel, f"{written:,}")
        return None
    return last_err


def _download() -> None:
    """(Re)fetch the data lake directly from S3, repairing truncated or corrupt
    files that biomni's own downloader skips.

    biomni.utils.check_and_download_s3_files treats any file that merely
    *exists* as complete and never verifies size, so an interrupted download
    leaves a short file that is never re-fetched (and reconstructing A1 to
    "retry" is a double no-op: the agent is cached, and the check still sees
    the file present). We own the fetch here instead — every expected
    data_lake file that is missing OR failed the integrity check is
    re-downloaded with a size check and structural re-validation. Needs no
    provider key, unlike constructing A1."""
    global _download_error
    _download_error = None
    try:
        from biomni.env_desc import data_lake_dict
    except Exception as exc:  # noqa: BLE001 — biomni not importable yet
        logger.exception("cannot import biomni data lake manifest")
        _download_error = f"biomni not importable: {type(exc).__name__}: {exc}"
        return

    lake_dir = _lake_dir()
    (lake_dir / "data_lake").mkdir(parents=True, exist_ok=True)
    _merge_duplicates()
    _measure_sizes(data_lake_dict)

    # Which files are corrupt right now? We need the actual bad-file set, not
    # just "what's missing" — run the integrity check synchronously if it
    # hasn't produced a result since this download was kicked off.
    if not _validated:
        _run_validation()
    with _validate_lock:
        invalid = set(_invalid_files)

    failures: list[str] = []
    for filename in data_lake_dict:
        rel = f"data_lake/{filename}"
        dest = lake_dir / rel
        if dest.is_file() and rel not in invalid:
            continue  # present and, as far as the integrity check knows, intact
        err = _redownload_one(rel)
        if err:
            failures.append(f"{rel}: {err}")

    # Benchmark files ship as a single zip and are tiny; biomni only exposes a
    # whole-folder fetch, so defer to it when the marker dir is absent.
    if not (lake_dir / "benchmark" / "hle").is_dir():
        try:
            from biomni.utils import check_and_download_s3_files

            (lake_dir / "benchmark").mkdir(parents=True, exist_ok=True)
            check_and_download_s3_files(
                s3_bucket_url=S3_BUCKET_URL,
                local_data_lake_path=str(lake_dir / "benchmark"),
                expected_files=[],
                folder="benchmark",
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("benchmark download failed")
            failures.append(f"benchmark: {type(exc).__name__}: {exc}")

    # Drop any cached agent so the next task rebuilds against the repaired
    # files rather than holding reads from the bad copies.
    try:
        from . import biomni_runner

        with biomni_runner._agent_lock:
            biomni_runner._agent = None
    except Exception:  # noqa: BLE001 — best effort
        pass

    # Re-validate so status() reflects the repaired state, not the stale
    # pre-repair result.
    _run_validation()

    if failures:
        detail = "\n".join(f"  • {f}" for f in failures[:15])
        if len(failures) > 15:
            detail += f"\n  … and {len(failures) - 15} more (see server log)"
        _download_error = (
            f"{len(failures)} data lake file(s) could not be repaired:\n{detail}"
        )


class LocationError(ValueError):
    """A data lake location that can't be used; the message is for the user."""


def _location_presets() -> dict[str, Path]:
    """The suggested folder for each kind of location on this OS."""
    home = Path.home()
    if sys.platform == "win32":
        local_app = Path(os.getenv("LOCALAPPDATA") or home / "AppData" / "Local")
        system = Path(os.getenv("ProgramData") or "C:/ProgramData")
    elif sys.platform == "darwin":
        local_app = home / "Library" / "Application Support"
        system = Path("/Library/Application Support")
    else:
        local_app = Path(os.getenv("XDG_DATA_HOME") or home / ".local" / "share")
        system = Path("/var/lib")
    return {
        "default": home / "biomnikhil-data",
        "custom": home,
        "system": system / "biomnikhil",
        "local_app": local_app / "biomnikhil",
    }


def _saved_location() -> dict:
    try:
        saved = json.loads(config.DATA_LOCATION_FILE.read_text())
    except (OSError, ValueError):
        return {}
    return saved if isinstance(saved, dict) else {}


def _saved_location_kind() -> str | None:
    return _saved_location().get("kind")


def _own_lake_dirs() -> set[Path]:
    """Lakes only this user's install should be using, so files may be moved
    out of them. A folder someone typed in may be shared, so it's only copied."""
    presets = _location_presets()
    own = set()
    for data_path in (config._DATA_ROOT, presets["default"], presets["local_app"]):
        own.add((data_path / "biomni_data").resolve())
    return own


def _duplicate_lake_dirs() -> list[Path]:
    """Other data lakes in the places this app could have put one: the app
    folder, the per-user presets, and every folder picked before. Shared
    system folders are left out, and a lake in one merges nothing: other
    people's installs may be reading those files."""
    if _saved_location_kind() == "system":
        return []
    presets = _location_presets()
    candidates = [config._DATA_ROOT, presets["default"], presets["local_app"]]
    for previous in _saved_location().get("previous", []):
        candidates.append(Path(previous))

    current = _lake_dir().resolve()
    found: list[Path] = []
    for candidate in candidates:
        lake_dir = (candidate / "biomni_data").resolve()
        if lake_dir == current or lake_dir in found:
            continue
        if (lake_dir / "data_lake").is_dir() or (lake_dir / "benchmark").is_dir():
            found.append(lake_dir)
    return found


def duplicates() -> list[dict]:
    found = []
    own = _own_lake_dirs()
    for lake_dir in _duplicate_lake_dirs():
        size, files = _dir_stats(lake_dir)
        found.append({
            "path": str(lake_dir), "bytes": size, "files": files,
            "kept": lake_dir not in own,
        })
    return found


def _bring_file(source: Path, dest: Path, keep_source: bool) -> None:
    """Move or copy without ever leaving a partial file at dest, even across drives."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not keep_source:
        try:
            os.replace(source, dest)
            return
        except OSError:
            pass  # a different drive: copy instead
    tmp = dest.with_name(f".{dest.name}.merging")
    shutil.copy2(source, tmp)
    os.replace(tmp, dest)
    if not keep_source:
        source.unlink()


def _merge_one_file(source: Path, dest: Path, keep_source: bool = False) -> str | None:
    """Bring one file from a duplicate into the current lake. Returns why it
    wasn't merged, or None if the current lake now has it."""
    source_error = _check_file(source)
    if source_error:
        return f"damaged ({source_error})"
    if not dest.is_file() or _check_file(dest):
        _bring_file(source, dest, keep_source)
        return None
    if filecmp.cmp(source, dest, shallow=False):
        if not keep_source:
            source.unlink()
        return None
    return "differs from the copy already in the chosen location"


def _merge_duplicates() -> None:
    """Move other lakes' files into the current one, so they needn't be
    downloaded again. Only copies proven identical are deleted; anything else
    stays in place, and the duplicate keeps being reported."""
    lake_dir = _lake_dir()
    _merge_problems.clear()
    own = _own_lake_dirs()
    for duplicate in _duplicate_lake_dirs():
        keep_source = duplicate not in own
        for root, _dirs, files in os.walk(duplicate):
            for name in files:
                if name.startswith("."):  # our .part downloads and .sizes.json
                    continue
                source = Path(root) / name
                rel = source.relative_to(duplicate)
                try:
                    left_behind = _merge_one_file(source, lake_dir / rel, keep_source)
                except OSError as exc:
                    left_behind = str(exc)
                if left_behind:
                    logger.warning("left %s in place: %s", source, left_behind)
                    _merge_problems.append({"path": str(source), "reason": left_behind})

        if keep_source:
            continue
        (duplicate / ".sizes.json").unlink(missing_ok=True)
        for root, _dirs, _files in os.walk(duplicate, topdown=False):
            try:
                os.rmdir(root)
            except OSError:
                pass  # still holds something left behind


def location() -> dict:
    """Where the data lake lives and the choices for moving it. An install
    that already has data in its own folder keeps that folder on offer, so
    the picker can't strand a partial download."""
    saved_kind = _saved_location_kind()
    presets = _location_presets()
    choices = [
        {"kind": "default", "label": "Default", "path": str(presets["default"]), "editable": False},
        {"kind": "custom", "label": "Choose a folder", "path": "", "editable": True},
        {"kind": "system", "label": "Shared folder installed by an administrator", "path": str(presets["system"]), "editable": True},
        {"kind": "local_app", "label": "App data folder", "path": str(presets["local_app"]), "editable": False},
    ]
    bytes_here = _dir_stats()[0]
    selected = saved_kind or "default"
    if saved_kind == "install" or (saved_kind is None and bytes_here > 0):
        selected = "install"
        choices.insert(0, {
            "kind": "install", "label": "Keep in the app folder",
            "path": str(Config.DATA_PATH), "editable": False,
        })
    for choice in choices:
        if choice["kind"] == selected and choice["editable"]:
            choice["path"] = str(Config.DATA_PATH)
    return {
        "locked": bool(os.getenv("BIOMNI_DATA_PATH")),
        "chosen": saved_kind is not None,
        "selected": selected,
        "choices": choices,
    }


def _shared_lake_problem(data_path: Path) -> str | None:
    """Why this can't be used as a shared lake, or None. Biomni unpickles
    lake files, so a file another user can replace could run code as whoever
    opens the app. Windows ACLs aren't visible in st_mode, so there this only
    checks that the lake is complete."""
    lake_dir = data_path / "biomni_data"
    if _manifest_complete(lake_dir) is not True:
        return (
            f"{lake_dir} isn't a complete data lake. An administrator needs to "
            "install it there first, because the app won't download into a shared folder."
        )
    if sys.platform == "win32":
        return None
    # Above the lake only an open, non-sticky folder lets anyone swap it out.
    # Group write is normal there: /Library is admin-writable on a Mac.
    for parent in data_path.resolve().parents:
        mode = parent.stat().st_mode
        if mode & stat.S_IWOTH and not mode & stat.S_ISVTX:
            return f"{parent} can be changed by any user, so the shared lake inside it could be replaced."
    paths = [data_path, lake_dir]
    for root, dirs, files in os.walk(lake_dir):
        for name in dirs + files:
            paths.append(Path(root) / name)
    for path in paths:
        try:
            mode = path.stat().st_mode
        except OSError as exc:
            return f"Can't check {path}: {exc.strerror or exc}."
        if mode & (stat.S_IWGRP | stat.S_IWOTH):
            return f"{path} can be changed by other users. An administrator needs to make the shared lake read-only."
    return None


def _usable_location(kind: str, requested_path: str) -> Path:
    """The folder for this choice, created and checked, or LocationError."""
    presets = _location_presets()
    if kind == "install":
        target = Config.DATA_PATH
    elif kind in ("default", "local_app"):
        target = presets[kind]
    elif kind in ("custom", "system"):
        expanded = os.path.expandvars(os.path.expanduser(requested_path.strip()))
        if not expanded or not os.path.isabs(expanded):
            raise LocationError("Enter the full path of a folder.")
        target = Path(expanded)
    else:
        raise LocationError(f"Unknown location type: {kind}")
    target = target.resolve()

    known_lakes = [_lake_dir().resolve(), *_duplicate_lake_dirs()]
    for lake_dir in known_lakes:
        if target == lake_dir or lake_dir in target.parents:
            raise LocationError(
                f"{target} is inside an existing data lake. Choose a folder outside {lake_dir}."
            )

    if kind == "system":
        problem = _shared_lake_problem(target)
        if problem:
            raise LocationError(problem)
        return target

    try:
        target.mkdir(parents=True, exist_ok=True)
        probe = target / ".biomnikhil-write-test"
        probe.write_text("")
        probe.unlink()
    except OSError as exc:
        raise LocationError(
            f"Can't write to {target} ({exc.strerror or exc}). Choose another "
            "folder, or have an administrator create it and give you write access."
        ) from exc

    still_needed = EXPECTED_BYTES - _dir_stats(target / "biomni_data")[0]
    # Merging from the same drive moves files, which takes no new space.
    target_device = target.stat().st_dev
    for duplicate in _duplicate_lake_dirs():
        if duplicate.stat().st_dev == target_device and duplicate != target / "biomni_data":
            still_needed -= _dir_stats(duplicate)[0]
    free = shutil.disk_usage(target).free
    if free < still_needed:
        raise LocationError(
            f"{target} has {free / 1024**3:.1f} GB free, but the data lake "
            f"needs about {still_needed / 1024**3:.1f} GB more."
        )
    return target


def set_location(kind: str, requested_path: str = "") -> dict:
    """Point the data lake at a folder. Files already downloaded elsewhere
    stay where they are."""
    global _download_thread, _download_error, _validated, _valid, _validation_error
    if os.getenv("BIOMNI_DATA_PATH"):
        raise LocationError("BIOMNI_DATA_PATH sets the data lake location on this install.")
    with _state_lock:
        if _download_thread is not None and _download_thread.is_alive():
            raise LocationError("Wait for the current download to finish.")
        target = _usable_location(kind, requested_path)
        config.DATA_LOCATION_FILE.parent.mkdir(parents=True, exist_ok=True)
        previous = _saved_location().get("previous", [])
        leaving_shared = _saved_location_kind() == "system"
        if Config.DATA_PATH != target and str(Config.DATA_PATH) not in previous and not leaving_shared:
            previous.append(str(Config.DATA_PATH))
        config.DATA_LOCATION_FILE.write_text(
            json.dumps({"kind": kind, "path": str(target), "previous": previous})
        )
        Config.DATA_PATH = target
        _download_thread = None
        _download_error = None
        with _validate_lock:
            _validated = False
            _valid = None
            _validation_error = None
            _invalid_files.clear()

    # A cached agent still reads from the old folder.
    from . import biomni_runner

    with biomni_runner._agent_lock:
        biomni_runner._agent = None
    return status()


def needs_location() -> bool:
    """True until someone picks a folder, unless a complete lake is already
    in place. Starting the agent now would let biomni download on its own."""
    if os.getenv("BIOMNI_DATA_PATH") or _saved_location_kind():
        return False
    return _manifest_complete() is not True


def location_unavailable() -> bool:
    """A picked folder that's gone, like an unplugged drive. Recreating it
    could put 14 GB somewhere nobody chose."""
    return bool(
        not os.getenv("BIOMNI_DATA_PATH")
        and _saved_location_kind()
        and not Config.DATA_PATH.is_dir()
    )


def task_blocker() -> str | None:
    """Why a task can't start, or None. An agent started on an incomplete
    lake would have biomni download into it on its own."""
    if needs_location():
        return "Choose where to put the data lake before running a task."
    if location_unavailable():
        return f"The data lake folder {Config.DATA_PATH} isn't available. Reconnect its drive or choose another folder."
    if _saved_location_kind() == "system":
        return _shared_lake_problem(Config.DATA_PATH)
    return None


def start_download() -> bool:
    """Kick off the download thread; returns False if already running/done."""
    global _download_thread, _download_error, _validated, _valid, _validation_error
    with _state_lock:
        if _download_thread is not None and _download_thread.is_alive():
            return False
        if location_unavailable() or _saved_location_kind() == "system":
            return False
        _download_error = None
        with _validate_lock:  # stale from a prior run — recheck once this finishes
            _validated = False
            _valid = None
            _validation_error = None
        _download_thread = threading.Thread(target=_download, daemon=True)
        _download_thread.start()
        return True


def status() -> dict:
    bytes_done, files = _dir_stats()
    expected = _expected_bytes()
    downloading = _download_thread is not None and _download_thread.is_alive()
    finished = _download_thread is not None and not downloading

    # Ground truth over guesswork: check the real manifest (every filename
    # biomni actually expects, per the installed version) rather than
    # inferring "done" from a percentage of a hardcoded size estimate.
    # None means the check itself is unavailable right now (biomni not
    # importable, e.g. before the very first download) — fall back to the
    # byte heuristic only in that case.
    manifest_ok = None if downloading else _manifest_complete()

    if downloading:
        state = "downloading"
    elif location_unavailable():
        state = "unavailable"
    elif _download_error:
        state = "error"
    elif manifest_ok is True:
        state = "ready"
    elif manifest_ok is False:
        state = "partial" if bytes_done > 0 else "missing"
    elif finished or bytes_done >= expected * 0.9:  # manifest check unavailable
        state = "ready"
    elif bytes_done > 0:
        state = "partial"
    else:
        state = "missing"

    # Byte count alone can't catch a truncated/corrupted file that happens
    # to land at a plausible total size — run the integrity check in the
    # background as soon as the size heuristic looks ready, and let a
    # failure downgrade "ready" back to something the UI should act on.
    validated = _validated
    valid = _valid
    if state == "ready":
        ensure_validated()
        if validated and valid is False:
            state = "invalid"

    return {
        "state": state,
        "bytes": bytes_done,
        "files": files,
        "expected_bytes": expected,
        "percent": min(100, round(bytes_done * 100 / expected, 1)),
        "error": _download_error,
        "path": str(_lake_dir()),
        "validated": validated,
        "valid": valid,
        "validation_error": _validation_error,
        "invalid_files": list(_invalid_files),
        "location": location(),
        "shared": _saved_location_kind() == "system",
        "duplicates": duplicates(),
        "merge_problems": list(_merge_problems),
    }

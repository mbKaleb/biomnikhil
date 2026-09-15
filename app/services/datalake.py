"""Data-lake download state for the first-run popup.

Biomni downloads the ~11 GB data lake as a side effect of constructing A1,
so "downloading" here just means building the agent on a background thread
while we watch the data directory grow on disk.
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

from ..config import Config

logger = logging.getLogger(__name__)

# Rough size of a complete data lake, for the progress percentage during an
# active download only — completeness itself is decided by the manifest
# check below, not this number. Measured directly against the installed
# biomni version's actual data lake (~9.7 GB); override via env if a biomni
# release changes it materially.
EXPECTED_BYTES = int(os.getenv("BIOMNI_DATA_LAKE_BYTES", str(10 * 1024**3)))

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


def _manifest_complete() -> bool | None:
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
            import pickle

            with open(path, "rb") as f:
                pickle.load(f)
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


def _dir_stats() -> tuple[int, int]:
    """(total bytes, file count) under the data lake directory specifically —
    not all of Config.DATA_PATH, which also holds unrelated user data."""
    total = 0
    count = 0
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


def _download() -> None:
    global _download_error
    try:
        from . import biomni_runner

        biomni_runner._get_agent()  # constructing A1 performs the download
    except Exception as exc:
        logger.exception(
            "data lake download failed (path=%s, provider=%s, model=%s)",
            _lake_dir(), Config.PROVIDER, Config.MODEL,
        )
        _download_error = f"{type(exc).__name__}: {exc}"


def start_download() -> bool:
    """Kick off the download thread; returns False if already running/done."""
    global _download_thread, _download_error, _validated, _valid, _validation_error
    with _state_lock:
        if _download_thread is not None and _download_thread.is_alive():
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
    elif _download_error:
        state = "error"
    elif manifest_ok is True:
        state = "ready"
    elif manifest_ok is False:
        state = "partial" if bytes_done > 0 else "missing"
    elif finished or bytes_done >= EXPECTED_BYTES * 0.9:  # manifest check unavailable
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
        "expected_bytes": EXPECTED_BYTES,
        "percent": min(100, round(bytes_done * 100 / EXPECTED_BYTES, 1)),
        "error": _download_error,
        "path": str(_lake_dir()),
        "validated": validated,
        "valid": valid,
        "validation_error": _validation_error,
    }

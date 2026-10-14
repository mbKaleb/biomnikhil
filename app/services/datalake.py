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
# Relative paths (under the data lake dir, posix-style) of files that failed
# the last integrity check — the exact set the repair re-download targets, so
# a corrupt-but-present file actually gets replaced instead of skipped.
_invalid_files: list[str] = []


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


def _redownload_one(rel: str) -> str | None:
    """Re-fetch one data-lake file from S3 into place. Verifies the download
    against the S3 Content-Length and re-runs the structural integrity check
    before atomically replacing any existing (possibly truncated) copy.
    Returns None on success, else an error string; retries a few times since
    truncation is usually a transient network cutoff."""
    import requests

    url = f"{S3_BUCKET_URL}/{rel}"
    dest = _lake_dir() / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.part")

    last_err = "unknown error"
    for attempt in range(1, _REDOWNLOAD_ATTEMPTS + 1):
        try:
            with requests.get(url, stream=True, timeout=(10, 300)) as resp:
                resp.raise_for_status()
                expected = int(resp.headers.get("content-length", 0)) or None
                written = 0
                with open(tmp, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            f.write(chunk)
                            written += len(chunk)
            # Size check first — this is the exact failure biomni misses: a
            # stream that cuts off early leaves a short-but-present file.
            if expected is not None and written != expected:
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
        except Exception as exc:  # noqa: BLE001 — surface any fetch/IO failure
            last_err = f"{type(exc).__name__}: {exc}"
            logger.warning("redownload %s attempt %d failed: %s", rel, attempt, last_err)
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
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
        "invalid_files": list(_invalid_files),
    }

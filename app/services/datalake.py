"""Data-lake download state for the first-run popup.

Biomni downloads the ~11 GB data lake as a side effect of constructing A1,
so "downloading" here just means building the agent on a background thread
while we watch the data directory grow on disk.
"""
from __future__ import annotations

import os
import threading

from ..config import Config

# Rough size of a complete data lake, for the progress percentage. Override
# via env if a biomni release changes it materially.
EXPECTED_BYTES = int(os.getenv("BIOMNI_DATA_LAKE_BYTES", str(11 * 1024**3)))

_state_lock = threading.Lock()
_download_thread: threading.Thread | None = None
_download_error: str | None = None


def _dir_stats() -> tuple[int, int]:
    """(total bytes, file count) under the data path."""
    total = 0
    count = 0
    if Config.DATA_PATH.exists():
        for root, _dirs, files in os.walk(Config.DATA_PATH):
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
        _download_error = f"{type(exc).__name__}: {exc}"


def start_download() -> bool:
    """Kick off the download thread; returns False if already running/done."""
    global _download_thread, _download_error
    with _state_lock:
        if _download_thread is not None and _download_thread.is_alive():
            return False
        _download_error = None
        _download_thread = threading.Thread(target=_download, daemon=True)
        _download_thread.start()
        return True


def status() -> dict:
    bytes_done, files = _dir_stats()
    downloading = _download_thread is not None and _download_thread.is_alive()
    finished = _download_thread is not None and not downloading
    if downloading:
        state = "downloading"
    elif _download_error:
        state = "error"
    elif finished or bytes_done >= EXPECTED_BYTES * 0.9:
        state = "ready"
    elif bytes_done > 0:
        state = "partial"
    else:
        state = "missing"
    return {
        "state": state,
        "bytes": bytes_done,
        "files": files,
        "expected_bytes": EXPECTED_BYTES,
        "percent": min(100, round(bytes_done * 100 / EXPECTED_BYTES, 1)),
        "error": _download_error,
        "path": str(Config.DATA_PATH),
    }

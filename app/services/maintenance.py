"""Setup and cleanup, started from the Settings tab. Both need the app closed
(setup replaces what it runs on, and the cleanup script refuses while the app is
open), so this starts a helper script that waits for the app to close, then
closes the app: app/installer/window.py closes its window when the request file
appears, and the server exits on its own for when there's no window.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading

from dotenv import dotenv_values

from ..config import REPO_ROOT, Config
from . import datalake
from .tasks import registry

ACTIONS = ("setup", "cleanup")

# Long enough for the response to reach the page and for the window to close first.
_SERVER_EXIT_DELAY_SECONDS = 2


class MaintenanceError(RuntimeError):
    pass


def blocker() -> str | None:
    """Why the app can't close right now, or None."""
    if registry.active():
        return "An agent task is running. Wait for it to finish or stop it first."
    if datalake.is_downloading():
        return "The data lake is downloading. Wait for it to finish first."
    return None


def _helper_environment() -> dict:
    """This server's environment, without what it loaded from data/.env or what
    the window set for it, so the reopened app reads its settings afresh."""
    environment = dict(os.environ)
    environment.pop("VIRTUAL_ENV", None)
    environment.pop("BIOMNIKHIL_REINSTALL", None)
    for name in dotenv_values(REPO_ROOT / "data" / ".env"):
        environment.pop(name, None)
    return environment


def _start_helper_windows(action: str) -> None:
    subprocess.Popen(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-File", str(REPO_ROOT / "app" / "maintenance.ps1"), "-Action", action],
        cwd=tempfile.gettempdir(), env=_helper_environment(),
        creationflags=subprocess.CREATE_NEW_CONSOLE,
    )


def _start_helper_mac(action: str) -> None:
    if action == "cleanup":
        # Cleanup asks about each item, so it needs a Terminal window.
        subprocess.run(
            ["/usr/bin/open", "-a", "Terminal", str(REPO_ROOT / "app" / "cleanup-mac.command")],
            check=True, capture_output=True, timeout=30,
        )
        return
    # Setup shows its own window. Its own session, to outlive this server.
    subprocess.Popen(
        ["/bin/bash", str(REPO_ROOT / "app" / "maintenance-mac.sh"), action],
        cwd=tempfile.gettempdir(), env=_helper_environment(), start_new_session=True,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _close_app() -> None:
    Config.CLOSE_REQUEST_FILE.parent.mkdir(parents=True, exist_ok=True)
    Config.CLOSE_REQUEST_FILE.touch()
    timer = threading.Timer(_SERVER_EXIT_DELAY_SECONDS, os._exit, args=(0,))
    timer.daemon = True
    timer.start()


def run(action: str) -> None:
    """Starts the helper and closes the app. If the helper can't start, the app
    stays open."""
    try:
        if sys.platform == "win32":
            _start_helper_windows(action)
        else:
            _start_helper_mac(action)
    except (OSError, subprocess.SubprocessError) as exc:
        raise MaintenanceError(f"Couldn't start {action}: {exc}") from exc
    _close_app()

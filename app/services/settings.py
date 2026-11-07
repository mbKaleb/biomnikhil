"""Settings for this install, kept in data/settings.json. The self-update reads
the same file before the app starts (beta_updates_on in app/installer/update.py),
with no sign-in, so these belong to the install rather than to one user."""
from __future__ import annotations

import json
import os

from ..config import REPO_ROOT, Config


def _load() -> dict:
    try:
        settings = json.loads(Config.SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(settings, dict):
        return {}
    return settings


def beta_updates() -> bool:
    return _load().get("beta_updates") is True


def set_beta_updates(enabled: bool) -> None:
    settings = _load()
    settings["beta_updates"] = enabled
    Config.SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = Config.SETTINGS_FILE.with_name(f".{Config.SETTINGS_FILE.name}.tmp")
    temporary.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, Config.SETTINGS_FILE)


def update_status() -> dict | None:
    """What the self-update last did (_record_status in app/installer/update.py),
    or None if it hasn't run here."""
    try:
        status = json.loads(Config.UPDATE_STATUS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(status, dict):
        return None
    return status


def version() -> str:
    """The installed version, as app/installer/core.py shows it; "dev" in a checkout."""
    try:
        return (REPO_ROOT / "app" / "VERSION").read_text().strip()
    except OSError:
        return "dev"


def is_git_checkout() -> bool:
    """A checkout updates with git, so the self-update never runs in one."""
    return (REPO_ROOT / ".git").exists()

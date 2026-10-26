"""The model biomni's A1 agent runs on: Claude on Amazon Bedrock (Mantle),
signed per user with the Cognito credentials of whoever started the run
(app/auth.py; biomni_runner._patch_cognito_client) — there's no API key.
biomni drives Mantle through its Anthropic client, hence source "Anthropic".

The UI's model list comes from Mantle's /v1/models, signed with the signed-in
user's credentials (refresh_models), cached for a few hours; until that works
it shows MODELS.
"""
from __future__ import annotations

import logging
import re
import threading
import time

logger = logging.getLogger(__name__)

SOURCE = "Anthropic"
# The fallback list, and the default model (the first): one the
# bedrock-app-access role's policy allows.
MODELS: tuple[str, ...] = ("anthropic.claude-haiku-4-5",)

_LIST_TTL = 6 * 3600
_listed: dict = {"at": 0.0, "models": None, "error": None}
_list_lock = threading.Lock()
_listing = False


_VERSION_RE = re.compile(r"claude-(?P<family>[a-z]+)-(?P<major>\d+)(?:-(?P<minor>\d{1,2}))?(?:-|$)")


def _parse_version(model_id: str) -> tuple[str, tuple[int, int], str] | None:
    """('opus', (5, 5), '5.5') for 'anthropic.claude-opus-5-5' and
    ('sonnet', (5, 0), '5') for 'claude-sonnet-5'; None if not a versioned
    Claude id."""
    m = _VERSION_RE.search(model_id)
    if not m:
        return None
    major, minor = int(m["major"]), int(m["minor"] or 0)
    label = f"{major}.{minor}" if m["minor"] is not None else str(major)
    return m["family"], (major, minor), label


def rejects_sampling_params(model_id: str) -> bool:
    """True for Claude models that 400 on temperature/top_p/top_k: Opus 4.7+,
    Sonnet 5+, and every Fable/Mythos. (Sonnet 5.5 accepts only the default
    values, so dropping them is equivalent there.) Neither Models API object
    says this, so it's keyed on the version."""
    parsed = _parse_version(model_id)
    if not parsed:
        return False
    family, version, _ = parsed
    if family in ("fable", "mythos"):
        return True
    if family == "opus":
        return version >= (4, 7)
    if family == "sonnet":
        return version >= (5, 0)
    return False


def group_models(models: list[dict]) -> list[dict]:
    """Split model objects into the family/version pair the UI shows as two
    selects: [{"name": "opus", "versions": [{"id", "label", "available",
    "status_reason", "model"}, ...]}, ...]. `model` is the full object as the
    endpoint returned it. Ids that don't parse as a Claude family/version
    become their own family with a single "default" version."""
    families: dict[str, list[dict]] = {}
    for obj in models:
        parsed = _parse_version(obj["id"])
        family, label = (parsed[0], parsed[2]) if parsed else (obj["id"], "default")
        families.setdefault(family, []).append({
            "id": obj["id"],
            "label": label,
            # No status field (first-party API, static list) means usable.
            "available": obj.get("status", "available") == "available",
            "status_reason": obj.get("status_reason"),
            "model": obj,
        })
    return [{"name": name, "versions": versions} for name, versions in families.items()]


def _fetch_models(credentials) -> list[dict]:
    """Claude models from Bedrock Mantle's GET /v1/models (id, created,
    status, status_reason, …), newest first within each family, SigV4-signed
    like the model calls."""
    import requests
    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest

    from .config import Config

    url = f"https://bedrock-mantle.{Config.BEDROCK_REGION}.api.aws/v1/models"
    req = AWSRequest(method="GET", url=url)
    SigV4Auth(credentials.get_frozen_credentials(), "bedrock-mantle", Config.BEDROCK_REGION).add_auth(req)
    r = requests.get(url, headers=dict(req.prepare().headers), timeout=10)
    if not r.ok:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
    claude = [m for m in r.json().get("data", []) if _parse_version(m.get("id", ""))]

    def newest_first(m):
        family, version, _ = _parse_version(m["id"])
        return (family, (-version[0], -version[1]), -(m.get("created") or 0))

    return sorted(claude, key=newest_first)


def refresh_models(credentials) -> None:
    """Re-list the models in the background with these AWS credentials, unless
    the list is fresh or a listing is already running."""
    global _listing
    with _list_lock:
        # A failed listing is retried after a few minutes, not hours.
        ttl = _LIST_TTL if _listed["models"] and not _listed["error"] else 300
        if _listing or time.time() - _listed["at"] < ttl:
            return
        _listing = True

    def run():
        global _listing
        try:
            models, error = _fetch_models(credentials), None
        except Exception as exc:  # noqa: BLE001 — keep the previous list
            models, error = None, f"{type(exc).__name__}: {exc}"
            logger.warning("listing Bedrock models failed: %s", error)
        with _list_lock:
            _listing = False
            _listed["at"] = time.time()
            _listed["error"] = error
            if models:
                _listed["models"] = models

    threading.Thread(target=run, daemon=True).start()


def list_models() -> dict:
    """Models for the UI selector: {"models": [{"id": ..., …}, ...], "live":
    whether they came from Bedrock, "error": why the last listing failed}."""
    with _list_lock:
        models, error = _listed["models"], _listed["error"]
    if models:
        return {"models": models, "live": True, "error": error}
    return {"models": [{"id": m} for m in MODELS], "live": False, "error": error}


def default_model() -> str:
    """The model a run uses when none is chosen (BIOMNI_MODEL unset, no UI
    switch) — the single source for both resolve() and the UI's "current"
    model, so the UI never claims a model the run won't use."""
    return MODELS[0]


def resolve(model_override: str | None = None) -> dict:
    """The kwargs to pass to A1(...)."""
    return {"source": SOURCE, "llm": model_override or default_model()}

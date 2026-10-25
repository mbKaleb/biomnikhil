"""Provider profiles: map a BIOMNI_PROVIDER key from .env to the kwargs
biomni's A1 agent needs. Swapping providers = changing one env var.

NOTE: the `source` strings follow biomni's llm layer (biomni/llm.py,
get_llm). If A1 init complains about a source, check the spelling your
installed biomni version expects and adjust here — this file is the only
place provider details live.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderProfile:
    source: str
    default_model: str
    api_key_env: str | None = None
    base_url_env: str | None = None
    default_base_url: str | None = None


PROFILES: dict[str, ProviderProfile] = {
    "anthropic": ProviderProfile(
        source="Anthropic",
        default_model="claude-sonnet-4-5",  # override via BIOMNI_MODEL
        api_key_env="ANTHROPIC_API_KEY",
    ),
    "bedrock": ProviderProfile(
        # Claude on Bedrock Mantle via biomni's Anthropic client, signed per
        # user with Cognito identity pool credentials (app/auth.py) — no key
        # in .env. The model is the one the bedrock-web-auth policy names.
        source="Anthropic",
        default_model="anthropic.claude-haiku-4-5",
    ),
    "openai": ProviderProfile(
        source="OpenAI",
        default_model="gpt-4o",
        api_key_env="OPENAI_API_KEY",
    ),
    "gemini": ProviderProfile(
        source="Gemini",
        default_model="gemini-2.5-pro",
        api_key_env="GEMINI_API_KEY",
    ),
    "azure": ProviderProfile(
        # biomni's convention: Azure models are prefixed "azure-" and reuse
        # OPENAI_API_KEY + OPENAI_ENDPOINT (see their configuration docs).
        source="AzureOpenAI",
        default_model="azure-gpt-4o",
        api_key_env="OPENAI_API_KEY",
        base_url_env="OPENAI_ENDPOINT",
    ),
    "ollama": ProviderProfile(
        # Ollama exposes an OpenAI-compatible API on /v1 — biomni treats it
        # as a custom endpoint. Fully on-device.
        source="Custom",
        default_model="llama3.1:8b",
        api_key_env=None,
        base_url_env="OLLAMA_BASE_URL",
        default_base_url="http://localhost:11434/v1",
    ),
    "custom": ProviderProfile(
        source="Custom",
        default_model="my-model",
        api_key_env="CUSTOM_API_KEY",
        base_url_env="CUSTOM_BASE_URL",
    ),
}


# Static catalog per provider — used when a live Models API query isn't
# reachable (offline, endpoint without a /models route, missing key).
# The live query in list_models() is authoritative when it works.
STATIC_MODELS: dict[str, tuple[str, ...]] = {
    "bedrock": ("anthropic.claude-haiku-4-5",),
    "openai": ("gpt-5", "gpt-5-mini", "gpt-4.1", "gpt-4o", "o3"),
    "gemini": ("gemini-2.5-pro", "gemini-2.5-flash"),
    "azure": ("azure-gpt-4o",),
    "ollama": ("llama3.1:8b",),
    "custom": (),
}

# Anthropic models are shown per family (two selects: family, version).
# This order is the family order in the UI; families the endpoint lists
# that aren't here go after these. The (major, minor) is the static
# fallback shown only when the model list can't be fetched at all.
ANTHROPIC_FAMILIES: dict[str, tuple[int, int]] = {
    "opus": (5, 5),
    "sonnet": (4, 5),
    "haiku": (4, 5),
    "fable": (5, 1),
}

# The fetched model objects are served from cache instantly — memory, then a
# JSON file so restarts are instant too. Once an entry is older than this,
# the next read still returns it but kicks off a background re-fetch that
# replaces it (stale-while-revalidate).
_DISCOVERY_TTL = 6 * 3600
_discovery_cache: dict[str, tuple[float, list[dict]]] = {}
_discovery_lock = threading.Lock()
_refreshing: set[str] = set()


def _cache_file():
    from .config import Config

    return Config.CHAT_DB.parent / "model_cache.json"


def _load_disk_cache() -> None:
    try:
        raw = json.loads(_cache_file().read_text())
        for key, entry in raw.items():
            models = entry["models"]
            # Entries from before model objects were cached held bare ids.
            if models and all(isinstance(m, dict) and "id" in m for m in models):
                _discovery_cache.setdefault(key, (float(entry["at"]), models))
    except Exception:  # noqa: BLE001 — missing/corrupt file => start empty
        pass


def _save_disk_cache() -> None:
    try:
        path = _cache_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(
            {k: {"at": at, "models": m} for k, (at, m) in _discovery_cache.items()},
            indent=2,
        ))
        tmp.replace(path)
    except Exception:  # noqa: BLE001 — cache is an optimisation only
        pass


def _is_bedrock_anthropic() -> bool:
    """True when the Anthropic provider is pointed at a Bedrock endpoint,
    where model ids must carry the 'anthropic.' prefix."""
    return "bedrock" in (os.getenv("ANTHROPIC_BASE_URL") or "").lower()


def _anthropic_id(family: str, major: int, minor: int) -> str:
    prefix = "anthropic." if _is_bedrock_anthropic() else ""
    return f"{prefix}claude-{family}-{major}-{minor}"


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


def _model_sort_key(model: dict):
    """Known families first in ANTHROPIC_FAMILIES order, then the rest by
    name; newest version first within a family, `created` breaking ties."""
    order = list(ANTHROPIC_FAMILIES)
    parsed = _parse_version(model["id"])
    if not parsed:
        return (len(order) + 1, model["id"], (0, 0), 0)
    family, (major, minor), _ = parsed
    rank = order.index(family) if family in order else len(order)
    return (rank, family, (-major, -minor), -(model.get("created") or 0))


def group_models(models: list[dict]) -> list[dict]:
    """Split model objects into the family/version pair the UI shows as two
    selects: [{"name": "opus", "versions": [{"id", "label", "available",
    "status_reason", "model"}, ...]}, ...]. `model` is the full object as the
    endpoint returned it. Ids that don't parse as a Claude family/version
    become their own family with a single "default" version, so other
    providers still fit."""
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


def _static_anthropic() -> list[dict]:
    return [{"id": _anthropic_id(f, *v)} for f, v in ANTHROPIC_FAMILIES.items()]


def _cache_key(provider: str) -> str:
    """One cache entry per provider + endpoint, so pointing a provider at a
    different base URL starts a fresh list."""
    env = {"anthropic": "ANTHROPIC_BASE_URL", "openai": "OPENAI_BASE_URL"}.get(provider, "")
    return f"{provider}:{os.getenv(env) or 'default'}" if env else provider


def _fetch_anthropic_models() -> list[dict]:
    """Every Claude model object the endpoint lists, kept whole. First-party
    Anthropic: the SDK's Models API (display_name, max_input_tokens,
    max_tokens, capabilities, …). Bedrock Mantle has no Models API under its
    /anthropic base URL; its root /v1/models lists every provider's models
    (id, created, status, status_reason, data_retention, …), filtered here to
    Claude. Raises on failure so the caller keeps the previous cache."""
    import httpx2 as httpx
    from anthropic import Anthropic

    if _is_bedrock_anthropic():
        base = (os.getenv("ANTHROPIC_BASE_URL") or "").rstrip("/")
        root = base[: -len("/anthropic")] if base.endswith("/anthropic") else base
        r = httpx.get(
            f"{root}/v1/models",
            headers={"x-api-key": os.getenv("ANTHROPIC_API_KEY", "")},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json().get("data", [])
    else:
        # Picks up ANTHROPIC_API_KEY + ANTHROPIC_BASE_URL from the env.
        client = Anthropic(timeout=10, max_retries=0)
        data = [m.to_dict() for m in client.models.list()]
    return sorted(
        (m for m in data if _parse_version(m.get("id", ""))),
        key=_model_sort_key,
    )


def _fetch_openai_models() -> list[dict]:
    """OpenAI's Models API objects, kept whole. Bounded timeout and no
    retries — this runs on a background thread, but a dead network
    shouldn't hold a refresh slot for minutes. Not used for azure: OpenAI()
    ignores OPENAI_ENDPOINT, so it would list api.openai.com's models rather
    than the Azure deployments; azure stays on its static list."""
    from openai import OpenAI

    return [m.to_dict() for m in OpenAI(timeout=10, max_retries=0).models.list().data]


# Providers whose model list is fetched live (in the background, cached);
# every other provider uses STATIC_MODELS only.
_FETCHERS = {
    "anthropic": _fetch_anthropic_models,
    "openai": _fetch_openai_models,
}


def _static_models(provider: str) -> list[dict]:
    if provider == "anthropic":
        return _static_anthropic()
    return [{"id": i} for i in STATIC_MODELS.get(provider, ())]


def _refresh(provider: str, key: str) -> None:
    try:
        models = _FETCHERS[provider]()
    except Exception:  # noqa: BLE001 — outage, bad key, no SDK
        models = []
    with _discovery_lock:
        _refreshing.discard(key)
        # A failed fetch keeps the previous entry rather than caching an
        # empty list; the next stale read retries.
        if models:
            _discovery_cache[key] = (time.time(), models)
            _save_disk_cache()


def refresh_models_in_background(provider: str, force: bool = False) -> None:
    """Start a background re-fetch of this provider's model list unless it
    has no live list, one is already running, or the cache is still fresh
    (force skips the freshness check)."""
    provider = provider.strip().lower()
    if provider not in _FETCHERS:
        return
    key = _cache_key(provider)
    with _discovery_lock:
        if not _discovery_cache:
            _load_disk_cache()
        cached = _discovery_cache.get(key)
        fresh = cached and time.time() - cached[0] < _DISCOVERY_TTL
        if key in _refreshing or (fresh and not force):
            return
        _refreshing.add(key)
    threading.Thread(target=_refresh, args=(provider, key), daemon=True).start()


def list_models(provider_name: str | None = None) -> dict:
    """Available models for a provider, for the UI selector, never blocking
    on the network: the cached live list if there is one, else the static
    list; a stale or missing entry triggers a background refresh for later
    reads. Shape: {"provider": str, "models": list[dict], "source":
    "live"|"static"} — each model an object with at least "id"; for a live
    list, the full object the provider's model list returned."""
    provider = (provider_name or os.getenv("BIOMNI_PROVIDER", "anthropic")).strip().lower()
    refresh_models_in_background(provider)
    with _discovery_lock:
        cached = _discovery_cache.get(_cache_key(provider))
    if cached:
        return {"provider": provider, "models": cached[1], "source": "live"}
    return {"provider": provider, "models": _static_models(provider), "source": "static"}


def model_cache_info(provider_name: str | None) -> dict:
    """How fresh the cached model list is, for the Integrity tab:
    {"live": bool (provider has a live list at all), "fetched_at": epoch or
    None, "age_s", "stale": bool, "refreshing": bool, "count": int}."""
    provider = (provider_name or "anthropic").strip().lower()
    key = _cache_key(provider)
    with _discovery_lock:
        if not _discovery_cache:
            _load_disk_cache()
        cached = _discovery_cache.get(key)
        refreshing = key in _refreshing
    at = cached[0] if cached else None
    age = time.time() - at if at else None
    return {
        "live": provider in _FETCHERS,
        "fetched_at": at,
        "age_s": age,
        "stale": age is None or age >= _DISCOVERY_TTL,
        "ttl_s": _DISCOVERY_TTL,
        "refreshing": refreshing,
        "count": len(cached[1]) if cached else 0,
    }


def credentials_status(provider_name: str | None) -> dict:
    """Whether the provider's API key env var is set (never its value)."""
    p = PROFILES.get((provider_name or "anthropic").strip().lower())
    if p is None or not p.api_key_env:
        return {"env": None, "set": True}  # keyless provider (ollama, …)
    return {"env": p.api_key_env, "set": bool((os.getenv(p.api_key_env) or "").strip())}


def default_model(provider_name: str | None) -> str | None:
    """The model a run uses when no model is chosen (BIOMNI_MODEL unset, no
    UI switch) — the single source for both resolve() and the UI's
    "current" model, so the UI never claims a model the run won't use. On
    Bedrock, Anthropic ids need the 'anthropic.' prefix."""
    p = PROFILES.get((provider_name or "anthropic").strip().lower())
    if p is None:
        return None
    model = p.default_model
    if p is PROFILES["anthropic"] and _is_bedrock_anthropic() and not model.startswith("anthropic."):
        model = f"anthropic.{model}"
    return model


def resolve(provider_name: str | None, model_override: str | None = None) -> dict:
    """Return the kwargs to pass to A1(...) for the chosen provider."""
    key = (provider_name or "anthropic").strip().lower()
    if key not in PROFILES:
        raise KeyError(
            f"Unknown BIOMNI_PROVIDER '{provider_name}'. "
            f"Options: {', '.join(sorted(PROFILES))}"
        )
    p = PROFILES[key]

    kwargs: dict = {"source": p.source, "llm": model_override or default_model(key)}

    if p.api_key_env:
        api_key = (os.getenv(p.api_key_env) or "").strip()
        # biomni ignores the api_key kwarg for OpenAI/Anthropic and relies on
        # the env var, so a missing/blank key would otherwise surface as an
        # opaque 401 from the provider. Fail fast with the actual fix instead.
        if not api_key:
            if p.source == "Custom":  # local/self-hosted endpoints may be keyless
                api_key = None
            else:
                raise RuntimeError(
                    f"{p.api_key_env} is not set. Add it to .env and restart "
                    "the server (env vars are read once at startup)."
                )
        if api_key:
            if any(ch.isspace() for ch in api_key):
                raise RuntimeError(
                    f"{p.api_key_env} contains whitespace — likely a line "
                    "break from pasting. Fix it in .env and restart the server."
                )
            kwargs["api_key"] = api_key
            # Belt and braces: make sure the env var the underlying client
            # reads matches what we resolved (strip stray whitespace).
            os.environ[p.api_key_env] = api_key

    base_url = None
    if p.base_url_env:
        base_url = os.getenv(p.base_url_env)
    base_url = base_url or p.default_base_url
    if base_url:
        kwargs["base_url"] = base_url

    return kwargs

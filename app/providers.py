"""Provider profiles: map a BIOMNI_PROVIDER key from .env to the kwargs
biomni's A1 agent needs. Swapping providers = changing one env var.

NOTE: the `source` strings follow biomni's llm layer (biomni/llm.py,
get_llm). If A1 init complains about a source, check the spelling your
installed biomni version expects and adjust here — this file is the only
place provider details live.
"""
from __future__ import annotations

import os
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


def resolve(provider_name: str | None, model_override: str | None = None) -> dict:
    """Return the kwargs to pass to A1(...) for the chosen provider."""
    key = (provider_name or "anthropic").strip().lower()
    if key not in PROFILES:
        raise KeyError(
            f"Unknown BIOMNI_PROVIDER '{provider_name}'. "
            f"Options: {', '.join(sorted(PROFILES))}"
        )
    p = PROFILES[key]

    kwargs: dict = {"source": p.source, "llm": model_override or p.default_model}

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

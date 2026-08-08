"""Choose the model provider — OpenAI or Gemini — before a run starts.

Every live model call in the pipeline goes through one gate that constructs
an OpenAI SDK client and reads ``config.OPENAI_MODEL``. Google exposes an
OpenAI-compatible endpoint for Gemini, so switching provider is a matter of
pointing that same client at a different ``base_url``/``api_key`` and model
name — the JSON-mode call format, retry policy, queueing and usage metering
all stay identical. Reasoning-effort negotiation degrades automatically
(``max`` → ``xhigh`` → ``high``), and ``high`` is the top effort Gemini's
compatibility layer accepts.

The selection is persisted to ``DATA_DIR`` so it survives restarts, applied
process-wide at selection time and at bootstrap, and picked up by the next
run. Content caches key on the model name, so switching providers never
reuses another model's cached readings or polish decisions.
"""
from __future__ import annotations

import json
import os
import threading

from aegis_pipeline import openai_policy

from .. import config

PROVIDERS = ("openai", "gemini")
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
DEFAULT_GEMINI_MODEL = "gemini-3.6-flash"

_lock = threading.RLock()


def _selection_path():
    return config.DATA_DIR / "model_provider.json"


def gemini_available() -> bool:
    return bool(os.environ.get("GEMINI_API_KEY"))


def gemini_model() -> str:
    return (
        os.environ.get("AEGIS_GEMINI_MODEL", DEFAULT_GEMINI_MODEL).strip()
        or DEFAULT_GEMINI_MODEL
    )


def _stored_provider() -> str:
    try:
        path = _selection_path()
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            value = str((data or {}).get("provider") or "").strip().lower()
            if value in PROVIDERS:
                return value
    except (OSError, json.JSONDecodeError):
        pass
    return "openai"


def active_provider() -> str:
    """The provider the next model call will use.

    A stored Gemini choice without a configured key falls back to OpenAI
    rather than failing every call — the description surfaces why.
    """
    provider = _stored_provider()
    if provider == "gemini" and not gemini_available():
        return "openai"
    if provider == "openai" and not os.environ.get("OPENAI_API_KEY"):
        # Only one credential is configured; use the provider that can run.
        if gemini_available():
            return "gemini"
    return provider


def active_model() -> str:
    if active_provider() == "gemini":
        return gemini_model()
    return openai_policy.configured_openai_model()


def client_kwargs() -> dict:
    """Extra OpenAI-SDK client arguments for the active provider."""
    if active_provider() == "gemini":
        return {
            "api_key": os.environ.get("GEMINI_API_KEY", ""),
            "base_url": GEMINI_BASE_URL,
        }
    return {}


def _apply() -> None:
    """Point the process-wide model configuration at the active provider."""
    model = active_model()
    config.OPENAI_MODEL = model
    config.OPENAI_CONTEXT_WINDOW_TOKENS = (
        openai_policy.configured_context_window_tokens(model)
    )
    config.OPENAI_MAX_OUTPUT_TOKENS = (
        openai_policy.configured_max_output_tokens(model)
    )
    # Effort ceilings are per-model capability discoveries; a provider
    # switch must re-negotiate from scratch.
    openai_policy.reset_reasoning_ceilings()


def restore() -> None:
    """Re-apply the persisted selection at startup."""
    with _lock:
        _apply()


def set_active_provider(provider: str) -> dict:
    value = str(provider or "").strip().lower()
    if value not in PROVIDERS:
        raise ValueError(f"unknown model provider {provider!r}")
    if value == "gemini" and not gemini_available():
        raise ValueError(
            "Gemini is not configured on this server. Set the "
            "GEMINI_API_KEY environment variable (fly secrets set "
            "GEMINI_API_KEY=...) and try again."
        )
    with _lock:
        path = _selection_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"provider": value}), encoding="utf-8"
        )
        _apply()
    return describe()


def describe() -> dict:
    stored = _stored_provider()
    active = active_provider()
    note = ""
    if stored == "gemini" and active != "gemini":
        note = (
            "Gemini was selected but GEMINI_API_KEY is not set on the "
            "server; runs use OpenAI until it is configured."
        )
    return {
        "provider": active,
        "model": active_model(),
        "openai_available": bool(os.environ.get("OPENAI_API_KEY")),
        "gemini_available": gemini_available(),
        "gemini_model": gemini_model(),
        "openai_model": openai_policy.configured_openai_model(),
        "note": note,
    }

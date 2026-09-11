"""Frozen per-run routing with per-call provider snapshots.

The current profile uses GPT-5.6 Luna for every stage. ContextVar binding
prevents simultaneous runs and copied worker contexts from changing one
another's model. Recorded v1 profiles and explicit ``bind_profile(None)``
preserve historical routing; new unbound work uses the current profile. No
selector mutates process config.
"""
from __future__ import annotations

import copy
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import json
import os
from typing import Any, Mapping

from aegis_pipeline import openai_policy
from .. import config

PROVIDERS = ("openai", "gemini")
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"
PROFILE_KEY = "model_routing_policy"
LEGACY_PROFILE_VERSION = "owner-stage-model-routing-2026-09-09-v1"
MINI_PROFILE_VERSION = "owner-stage-model-routing-2026-09-11-v2"
PROFILE_VERSION = "owner-stage-model-routing-2026-09-11-v3"
_UNBOUND = object()
_profile: ContextVar[object] = ContextVar("aegis_model_routing_profile", default=_UNBOUND)
_call_route: ContextVar[object] = ContextVar("aegis_model_call_route", default=None)


def mini_profile() -> dict[str, Any]:
    """Return the exact v2 profile for previously recorded mini runs."""
    return {
        "version": MINI_PROFILE_VERSION,
        "routes": {
            "default": {"provider": "openai", "model": "gpt-5.4-mini", "reasoning_effort": "xhigh"},
            "narrow": {"provider": "openai", "model": "gpt-5.4-mini", "reasoning_effort": "high"},
            "critic": {"provider": "openai", "model": "gpt-5.4-mini", "reasoning_effort": "medium"},
            "metadata": {"provider": "openai", "model": "gpt-5.4-mini", "reasoning_effort": "low"},
            "pre_question_author": {"provider": "openai", "model": "gpt-5.4-mini", "reasoning_effort": "high"},
        },
        "narrow_purposes": ["concept_validation", "page_transcription", "chapter_outline"],
        "default_stages": ["concepts.refine", "concepts.polish"],
        "capacity_policy": "complete-input-mini-only-provider-limit-v2",
    }


def new_profile() -> dict[str, Any]:
    """Freeze Luna for every stage without changing its reasoning effort."""
    profile = mini_profile()
    profile["version"] = PROFILE_VERSION
    for route in profile["routes"].values():
        route["model"] = "gpt-5.6-luna"
    profile["capacity_policy"] = "complete-input-luna-only-provider-limit-v3"
    return profile


def legacy_profile() -> dict[str, Any]:
    """Return the exact v1 policy for recorded runs; never mint it for new work."""
    return {
        "version": LEGACY_PROFILE_VERSION,
        "routes": {
            "default": {"provider": "openai", "model": "gpt-5.6-luna", "reasoning_effort": "xhigh"},
            "narrow": {"provider": "openai", "model": "gpt-5.4-mini", "reasoning_effort": "high"},
            "critic": {"provider": "openai", "model": "gpt-5.4-mini", "reasoning_effort": "medium"},
            "metadata": {"provider": "openai", "model": "gpt-5.4-mini", "reasoning_effort": "low"},
            "pre_question_author": {"provider": "gemini", "model": DEFAULT_GEMINI_MODEL, "reasoning_effort": "high"},
        },
        "narrow_purposes": ["concept_validation", "page_transcription", "chapter_outline"],
        "luna_stages": ["concepts.refine", "concepts.polish"],
        "capacity_policy": "complete-input-byte-bound-with-visual-luna-fallback-v1",
    }


def validate_profile(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or dict(value) not in (new_profile(), mini_profile(), legacy_profile()):
        raise ValueError("Unknown or altered model routing profile; retain the recorded policy or start a new run.")
    return copy.deepcopy(dict(value))


def bound_profile() -> dict[str, Any] | None:
    value = _profile.get()
    if value is _UNBOUND:
        return new_profile()
    return copy.deepcopy(value)


@contextmanager
def bind_profile(value: Mapping[str, Any] | None):
    token = _profile.set(None if value is None else validate_profile(value))
    try:
        yield
    finally:
        _profile.reset(token)


def _selection_path():
    return config.DATA_DIR / "model_provider.json"


def _stored_provider() -> str:
    try:
        data = json.loads(_selection_path().read_text(encoding="utf-8"))
        value = str((data or {}).get("provider") or "").strip().lower()
        if value in PROVIDERS:
            return value
    except (OSError, json.JSONDecodeError):
        pass
    return "openai"


def gemini_available() -> bool:
    return bool(os.environ.get("GEMINI_API_KEY"))


def gemini_model() -> str:
    # Old envelopes may have been executed under the legacy global provider.
    # Its original default stays distinct from the recorded v1 Pre-only model.
    if bound_profile() is None:
        return os.environ.get("AEGIS_GEMINI_MODEL", "gemini-3.6-flash").strip() or "gemini-3.6-flash"
    return DEFAULT_GEMINI_MODEL


def active_provider() -> str:
    """Default provider for compatibility callers, never a Pre-author override."""
    if bound_profile() is not None:
        return "openai"
    provider = _stored_provider()
    if provider == "gemini" and not gemini_available():
        return "openai"
    if provider == "openai" and not os.environ.get("OPENAI_API_KEY") and gemini_available():
        return "gemini"
    return provider


def active_model() -> str:
    profile = bound_profile()
    if profile is not None:
        return str(profile["routes"]["default"]["model"])
    return gemini_model() if active_provider() == "gemini" else openai_policy.configured_openai_model()


def source_model_identity() -> str:
    """Keep source cache identity and receipts tied to their frozen policy.

    Unprofiled source caches historically recorded ``config.OPENAI_MODEL``;
    preserve that exact path instead of reinterpreting their provider choice.
    """
    profile = bound_profile()
    return str(profile["routes"]["default"]["model"] if profile is not None else config.OPENAI_MODEL)


@dataclass(frozen=True)
class ModelRoute:
    provider: str
    model: str
    reasoning_effort: str
    profile_version: str = ""
    stage: str = ""
    capacity_fallback: str = ""

    def request_policy(self, purpose: str) -> dict[str, str]:
        if not self.profile_version:
            return openai_policy.chat_request_policy(purpose, model=self.model)
        # Validate purpose without applying mutable environment effort profiles.
        if purpose not in openai_policy.REASONING_EFFORT_BY_PURPOSE:
            raise ValueError(f"Unknown OpenAI request purpose {purpose!r}")
        effort = openai_policy.capped_reasoning_effort(
            self.reasoning_effort, openai_policy.reasoning_ceiling(self.model),
        )
        # Gemini 3.8 cannot disable thinking or use minimal/xhigh. A rejected
        # low is not permission to send an unsupported weaker effort.
        if self.provider == "gemini" and effort not in {"low", "medium", "high"}:
            effort = "low"
        return {"model": self.model, **({"reasoning_effort": effort} if effort else {})}

    def output_limit(self, requested: int | None) -> int:
        return openai_policy.effective_completion_tokens(requested, model=self.model)


def resolve_route(
    purpose: str, *, stage: str = "", model: str | None = None,
    input_text: str = "", image_count: int = 0,
    max_output_tokens: int | None = None,
) -> ModelRoute:
    profile = bound_profile()
    if profile is None:
        selected = str(model or active_model())
        policy = openai_policy.chat_request_policy(purpose, model=selected)
        return ModelRoute(active_provider(), selected, str(policy.get("reasoning_effort") or ""), stage=stage)
    routes = profile["routes"]
    if stage == "prequestions.author":
        if purpose != "pre_learning":
            raise ValueError("The Pre question author stage requires purpose pre_learning.")
        name = "pre_question_author"
    elif stage in profile.get("default_stages", profile.get("luna_stages", [])):
        name = "default"
    elif purpose == "advisory_critic":
        name = "critic"
    elif purpose == "metadata":
        name = "metadata"
    elif purpose in profile["narrow_purposes"]:
        name = "narrow"
    else:
        name = "default"
    selected = dict(routes[name])
    if model is not None:
        requested = str(model)
        if profile["version"] in {PROFILE_VERSION, MINI_PROFILE_VERSION} and requested != selected["model"]:
            raise ValueError(f"All stages in this run require OpenAI {selected['model']}; the explicit model conflicts with its frozen routing profile.")
        # The recorded v1 policy permits OpenAI work to request Luna capacity.
        # Keep that historical contract without widening the current policy.
        if requested.startswith("gemini") and stage != "prequestions.author":
            raise ValueError("Gemini is authorized only for prequestions.author in this routing profile.")
        if requested != selected["model"]:
            if stage != "prequestions.author" and requested == routes["default"]["model"]:
                selected = dict(routes["default"])
            elif requested not in {r["model"] for r in routes.values()}:
                raise ValueError("Explicit model is outside this run's frozen routing profile.")
            else:
                raise ValueError("Explicit model conflicts with this run's frozen stage route; only an OpenAI call may request Luna capacity.")
    fallback = ""
    if profile["version"] == LEGACY_PROFILE_VERSION and selected["model"] == routes["narrow"]["model"]:
        limit = openai_policy.effective_completion_tokens(max_output_tokens, model=selected["model"])
        # UTF-8 bytes safely bound byte-BPE text tokens. Reserve wire framing;
        # count the COMPLETE prompt/schema, never trim to make mini fit.
        upper_bound = len(str(input_text).encode("utf-8")) + 4096
        capacity = openai_policy.provider_token_capacity(selected["model"])
        if image_count or upper_bound + limit > capacity.context_window:
            fallback = "visual_capacity_requires_luna" if image_count else "complete_input_exceeds_mini_safe_capacity"
            selected = dict(routes["default"])
    # Current single-model requests retain the COMPLETE text, schema and visuals.
    # A UTF-8 byte upper bound is not a token count and cannot justify rejecting
    # a valid request. Existing upstream batching and the provider's hard
    # context limit enforce capacity; never truncate evidence or change models.
    return ModelRoute(**selected, profile_version=str(profile["version"]), stage=stage, capacity_fallback=fallback)


@contextmanager
def bind_call(route: ModelRoute):
    """Expose a call's provider to queue diagnostics without changing routing."""
    token = _call_route.set(route)
    try:
        yield
    finally:
        _call_route.reset(token)


def current_call_provider() -> str:
    route = _call_route.get()
    return route.provider if isinstance(route, ModelRoute) else active_provider()


def client_kwargs(route: ModelRoute | None = None) -> dict[str, str]:
    provider = route.provider if route is not None else active_provider()
    if provider == "gemini":
        return {"api_key": os.environ.get("GEMINI_API_KEY", ""), "base_url": GEMINI_BASE_URL}
    return {}


def restore() -> None:
    """Compatibility bootstrap hook: restoring must not mutate process models."""


def set_active_provider(provider: str) -> dict:
    value = str(provider or "").strip().lower()
    if value not in PROVIDERS:
        raise ValueError(f"unknown model provider {provider!r}")
    raise ValueError("Provider selection is fixed per run. All stages in new runs use OpenAI GPT-5.6 Luna; global provider switching is disabled.")


def describe() -> dict[str, Any]:
    profile = new_profile()
    labels = (
        ("prequestions.author", "Pre question authoring", "pre_question_author"),
        ("concepts.detailing", "Concept writing, topology and adjudication", "default"),
        ("validation", "Validation, transcription and outline", "narrow"),
        ("critic", "Independent reviews", "critic"),
        ("metadata", "Metadata", "metadata"),
    )
    openai_ready = bool(os.environ.get("OPENAI_API_KEY"))
    gemini_ready = gemini_available()
    missing = [] if openai_ready else ["OPENAI_API_KEY"]
    return {
        "provider": "openai", "model": profile["routes"]["default"]["model"],
        "routing_profile": PROFILE_VERSION,
        "stages": [{"stage": stage, "label": label, **profile["routes"][key]} for stage, label, key in labels],
        "ready": not missing, "openai_available": openai_ready,
        "gemini_available": gemini_ready, "gemini_model": DEFAULT_GEMINI_MODEL,
        "openai_model": profile["routes"]["default"]["model"],
        "note": ("Configure " + " and ".join(missing) + " before a new full generation run.") if missing else "All stages use GPT-5.6 Luna and retain complete evidence, including images.",
    }

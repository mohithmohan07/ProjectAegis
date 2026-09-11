"""Explicit per-run owner quality amendment; never infer policy from content.

Only newly stamped work adopts these refinements. Existing envelopes, model
decisions and released files keep the instruction and identity they recorded.
"""
from __future__ import annotations

from typing import Any, Mapping
from contextlib import contextmanager
from contextvars import ContextVar

KEY = "generation_quality_policy"
VERSION = "owner-generation-quality-2026-09-11-v1"
POST_DESCRIPTION_INSTRUCTION = """POST CONCEPT DESCRIPTION AND QUESTION CONTEXT
Teach the concept in coherent, original, source-faithful language. Do not copy
large chapter extracts into a concept description merely to supply context for
an in-text question, or repeat the same teaching across concepts. The upstream
question-polishing author resolves necessary referents and records the minimum
sufficient learner context before the complete question is frozen. Keep that
accepted question under its owning Type/Case or Example, including all essential
givens, tables, images, passages and subparts. Where the reading passage itself
is tested, preserve it completely. Downstream stages must not rewrite frozen
questions, remove their evidence or restore discarded raw exposition. Full
original chapter extracts remain in source evidence and audit records.
"""
_UNBOUND = object()
_run_policy: ContextVar = ContextVar("generation_quality_policy", default=_UNBOUND)


@contextmanager
def bind_run(version: str | None):
    """Bind the saved upload policy; absence is an explicit historical run."""
    if version not in (None, VERSION):
        raise ValueError("Unknown saved generation quality policy")
    token = _run_policy.set(version)
    try:
        yield
    finally:
        _run_policy.reset(token)


def run_fields() -> dict[str, str]:
    """Stamp new entry points, respecting a saved run's explicit absence."""
    value = _run_policy.get()
    if value is _UNBOUND:
        from . import model_provider
        profile = model_provider.bound_profile()
        value = VERSION if profile and profile.get("version") in {model_provider.PROFILE_VERSION, model_provider.MINI_PROFILE_VERSION} else None
    return {KEY: VERSION} if value == VERSION else {}


def is_current(value: Mapping[str, Any] | None) -> bool:
    """Read the stamp from an explicit payload, metadata or frozen profile."""
    if not isinstance(value, Mapping):
        return False
    if value.get(KEY) == VERSION:
        return True
    for name in ("metadata", "meta", "_resolved_metadata"):
        metadata = value.get(name)
        if isinstance(metadata, Mapping) and metadata.get(KEY) == VERSION:
            return True
    for name in ("profile", "assessment_profile"):
        profile = value.get(name)
        if isinstance(profile, Mapping):
            if profile.get(KEY) == VERSION:
                return True
            metadata = profile.get("_resolved_metadata")
            if isinstance(metadata, Mapping) and metadata.get(KEY) == VERSION:
                return True
    return False


def active(value: Mapping[str, Any] | None) -> bool:
    """Alias used by the existing instruction-only policy adapters."""
    return is_current(value)


def fields(value: Mapping[str, Any] | None) -> dict[str, str]:
    """Carry the current stamp without minting it on historical payloads."""
    return {KEY: VERSION} if is_current(value) else {}


def suffix(value: Mapping[str, Any] | None) -> str:
    return ";" + VERSION if is_current(value) else ""

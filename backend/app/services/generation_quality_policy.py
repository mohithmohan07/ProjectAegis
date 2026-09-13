"""Explicit per-run owner quality amendment; never infer policy from content.

Only newly stamped work adopts these refinements. Existing envelopes, model
decisions and released files keep the instruction and identity they recorded.
"""
from __future__ import annotations

from typing import Any, Mapping
from contextlib import contextmanager
from contextvars import ContextVar

KEY = "generation_quality_policy"
#: v1 (11 September 2026): the Post description / question-context
#: instruction below. v2 (13 September 2026, Q67 second pass): everything in
#: v1, plus ``figure_references_kept`` — the deterministic cleaner no longer
#: deletes a figure reference from learner prose. A run keeps the version it
#: recorded; only newly stamped work adopts the latest.
V1 = "owner-generation-quality-2026-09-11-v1"
V2 = "owner-generation-quality-2026-09-13-v2"
SUPPORTED: tuple[str, ...] = (V1, V2)
VERSION = V2
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
    if version not in (None, *SUPPORTED):
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
    # A bound run keeps stamping the version it saved (its identity); only
    # an unbound, freshly profiled run mints the latest.
    return {KEY: value} if value in SUPPORTED else {}


def version_of(value: Mapping[str, Any] | None) -> str | None:
    """The recorded stamp, read from an explicit payload, metadata or frozen profile."""
    if not isinstance(value, Mapping):
        return None
    if value.get(KEY) in SUPPORTED:
        return str(value.get(KEY))
    for name in ("metadata", "meta", "_resolved_metadata"):
        metadata = value.get(name)
        if isinstance(metadata, Mapping) and metadata.get(KEY) in SUPPORTED:
            return str(metadata.get(KEY))
    for name in ("profile", "assessment_profile"):
        profile = value.get(name)
        if isinstance(profile, Mapping):
            if profile.get(KEY) in SUPPORTED:
                return str(profile.get(KEY))
            metadata = profile.get("_resolved_metadata")
            if isinstance(metadata, Mapping) and metadata.get(KEY) in SUPPORTED:
                return str(metadata.get(KEY))
    return None


def is_current(value: Mapping[str, Any] | None) -> bool:
    """Whether the payload carries a recorded stamp (any supported version).

    Every version includes the instruction v1 introduced, so the adapters
    that read this apply to a v1 run and a v2 run alike.
    """
    return version_of(value) is not None


def active(value: Mapping[str, Any] | None) -> bool:
    """Alias used by the existing instruction-only policy adapters."""
    return is_current(value)


def fields(value: Mapping[str, Any] | None) -> dict[str, str]:
    """Carry the RECORDED stamp — never upgrade a historical payload."""
    recorded = version_of(value)
    return {KEY: recorded} if recorded else {}


def suffix(value: Mapping[str, Any] | None) -> str:
    recorded = version_of(value)
    return ";" + recorded if recorded else ""


def figure_references_kept(value: Mapping[str, Any] | None) -> bool:
    """v2: learner prose keeps its figure references (Q67 second pass).

    ``concept_cleanup.strip_dangling_references`` used to delete "Fig. 7.7"
    from any prose section without an image tag, leaving "as illustrated
    in." — the reviewers' three subjectless sentences. A run stamped v1 or
    earlier replays through the cleanup it was sealed with.
    """
    recorded = version_of(value)
    return recorded is not None and SUPPORTED.index(recorded) >= SUPPORTED.index(V2)


def bound_figure_references_kept() -> bool:
    """The answer for the run this process is BOUND to (``model_routing_run.bind_job``).

    The database deposit sees plain concept rows that carry no stamp, so it
    reads the routing record's recorded version — the same value
    ``generation._metadata`` sealed into the envelope through ``run_fields``.
    A run bound to ``None`` (historical) or no binding at all is legacy;
    nothing is minted here.
    """
    value = _run_policy.get()
    return value is not _UNBOUND and figure_references_kept({KEY: value})

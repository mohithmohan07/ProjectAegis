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
#: instruction below. v2 (13 September 2026, Q68): everything in v1, plus
#: ``figure_references_kept`` — the deterministic cleaner no longer deletes a
#: figure reference from learner prose. v3 (13 September 2026, Q68):
#: everything in v2, plus ``host_creations_resolved`` — a Host unit that
#: minted a concept in a parallel batch is re-decided once with every
#: batch's creation visible. v4 (13 September 2026, Q70 and Q71): everything
#: in v3, plus ``declared_pre_options`` — the Pre author declares each
#: question's choice set as an ``options`` array, the cell carries it and the
#: materializer's checker holds the projected answers[] to that count — and
#: ``duplicate_case_titles_returned`` — two Cases of one mined Type sharing a
#: case_title are a coverage-class defect the Type miner sends back to the
#: model, with the Fixer as the final resort. A run keeps the version it
#: recorded; only newly stamped work adopts the latest. v5 (16 September
#: 2026, Corrections 2.0): numbered table references survive output and
#: exact coverage; semantic question/Case/Hub placement cannot be overridden
#: by an answering-form Type owner; each concept gets distinct rendered Type
#: identities; placed figures receive source-grounded public captions and
#: redundant Hub image copies are removed by exact asset URL.
V1 = "owner-generation-quality-2026-09-11-v1"
V2 = "owner-generation-quality-2026-09-13-v2"
V3 = "owner-generation-quality-2026-09-13-v3"
V4 = "owner-generation-quality-2026-09-13-v4"
V5 = "owner-generation-quality-2026-09-16-v5"
SUPPORTED: tuple[str, ...] = (V1, V2, V3, V4, V5)
VERSION = V5
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


def source_output_corrections(value: Mapping[str, Any] | None) -> bool:
    """Corrections 2.0 applies only to new v5 runs, never sealed predecessors."""
    recorded = version_of(value)
    return recorded is not None and SUPPORTED.index(recorded) >= SUPPORTED.index(V5)


def table_references_kept(value: Mapping[str, Any] | None) -> bool:
    """A printed table number is source evidence, not disposable apparatus."""
    return source_output_corrections(value)


def semantic_case_ownership(value: Mapping[str, Any] | None) -> bool:
    """Question/Case content owns placement; a shared answering form cannot override it."""
    return source_output_corrections(value)


def bound_source_output_corrections() -> bool:
    value = _run_policy.get()
    return value is not _UNBOUND and source_output_corrections({KEY: value})


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


def host_creations_resolved(value: Mapping[str, Any] | None) -> bool:
    """v3: Host re-decides its create_new units with every batch's creation visible.

    ``phase3.host.host`` certifies units in parallel batches over ONE concept
    payload built before any batch returns, so a batch never sees another
    batch's ``create_new`` and three same-meaning concepts were minted for
    Triangles (the reviewers' corrections catalogue, 13 September 2026). A
    run stamped v2 or earlier replays the single blind pass it was sealed
    with; only v3 work takes the second, sequential resolution pass.
    """
    recorded = version_of(value)
    return recorded is not None and SUPPORTED.index(recorded) >= SUPPORTED.index(V3)


def declared_pre_options(value: Mapping[str, Any] | None) -> bool:
    """v4: the Pre author declares each question's choice set (Q70).

    A generated Pre MCQ shipped with its fourth option missing (Bholi, the
    reviewers' corrections catalogue) and nothing on the generated lane
    could see it: the author response had no ``options`` field and the
    materializer's option-cardinality gate reads the source atom, which the
    Pre lane never has. Under v4 the author response carries ``options``
    (empty when the question offers no choice set), the cell carries it and
    ``assessment_materialization`` holds the projected answers[] to that
    count. A run stamped v3 or earlier keeps the v1 response schema, prompt
    and checker it was sealed with.
    """
    recorded = version_of(value)
    return recorded is not None and SUPPORTED.index(recorded) >= SUPPORTED.index(V4)


def duplicate_case_titles_returned(value: Mapping[str, Any] | None) -> bool:
    """v4: the Type miner returns a repeated Case title to the model (Q71).

    Two Cases of one mined Type carrying the same case_title (the reviewers'
    "Explaining the importance of DNA copying …" used for two Examples,
    How Do Organisms Reproduce) reached the Concept file because nothing
    read the CASE WORDING rule back. Under v4 the miner's COVERAGE DEFECTS
    follow-up carries ``duplicate_case_titles`` and the Fixer is the final
    resort; a run stamped v3 or earlier replays the loop it was sealed with.
    """
    recorded = version_of(value)
    return recorded is not None and SUPPORTED.index(recorded) >= SUPPORTED.index(V4)


def culmination_mastery_formatted(value: Mapping[str, Any] | None) -> bool:
    """v2: the mastery formatters no longer skip culminations (Q68).

    A run sealed before v2 was assembled with ``concept_refiner.refine_chapter``
    and ``generation._ensure_mastery_lines_via_api`` skipping culminations, and
    its final certificate seals ``concept_details`` as it was — a culmination
    carrying an inline "Achieving Mastery:" (measured: 7 of job 139's 9) must
    replay through the skip, or the deposit recompute refuses the sealed
    payload. v2 and later were minted in the same change as the widening, so
    no run stamped v2 or later was sealed under the skip.
    """
    recorded = version_of(value)
    return recorded is not None and SUPPORTED.index(recorded) >= SUPPORTED.index(V2)


def bound_culmination_mastery_formatted() -> bool:
    """The answer for the run this process is BOUND to (see ``bound_figure_references_kept``)."""
    value = _run_policy.get()
    return value is not _UNBOUND and culmination_mastery_formatted({KEY: value})

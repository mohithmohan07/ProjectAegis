"""Reconcile source-authoritative Figure images at every terminal validation.

A Type/Case rendering pass can preserve an explicit source reference such as
``Look once more at Fig. 10`` while dropping its image tag.  The referenced
figure may be many sections earlier in the MMD, so proximity is not a safe
fallback.  Immediately before strict terminal validation, resolve every
explicit Figure ID through the chapter-wide source registry and rebuild only
its canonical image tags.

This contract changes presentation only.  It does not alter task wording,
concept topology, Type placement, source-inventory ownership, or validation
severity.
"""
from __future__ import annotations

from functools import wraps
from types import ModuleType

_CONTRACT_VERSION = 1


def _terminal_stage(value: object) -> bool:
    """Return whether a validation stage is at a durable/final boundary."""
    stage = str(value or "").strip().casefold()
    return bool(
        stage
        and (
            "final" in stage
            or "deposit" in stage
            or "post-freeze" in stage
            or "checkpoint" in stage
        )
    )


def _reconcile_terminal_figure_images(
    generation: ModuleType,
    records: list[dict],
    *,
    source_text: str,
) -> int:
    """Mutate ``records`` with exact chapter-registry image tags.

    ``_reconcile_explicit_figure_images`` is reference-led: it reads Figure IDs
    from each Example and resolves them against the complete source registry,
    not against nearby page art.  Replacing the list contents preserves the
    caller's object identity while allowing the strict validator to inspect the
    corrected rows.
    """
    if not records or not str(source_text or "").strip():
        return 0
    sections = generation.parse_mmd_sections(source_text)
    reconciled, repaired = generation._reconcile_explicit_figure_images(
        records,
        sections,
    )
    if repaired:
        records[:] = reconciled
    return repaired


def install(generation: ModuleType) -> None:
    """Install the terminal Figure reconciliation exactly once."""
    if (
        getattr(generation, "_TERMINAL_FIGURE_CONTRACT_VERSION", 0)
        >= _CONTRACT_VERSION
    ):
        return

    original_validate = generation._validate_final_or_raise
    generation._TERMINAL_FIGURE_ORIGINAL_VALIDATE = original_validate

    @wraps(original_validate)
    def validate_final(records: list[dict], **kwargs):
        repaired = 0
        source_text = str(kwargs.get("source_text") or "")
        if source_text and _terminal_stage(kwargs.get("stage")):
            repaired = _reconcile_terminal_figure_images(
                generation,
                records,
                source_text=source_text,
            )
        if repaired:
            generation.progress.log(
                "Reconciled "
                f"{repaired} explicit source Figure Example(s) from the "
                "chapter-wide MMD registry before strict terminal validation.",
                level="success",
            )
        return original_validate(records, **kwargs)

    generation._validate_final_or_raise = validate_final
    generation._reconcile_terminal_figure_images = (
        lambda records, source_text: _reconcile_terminal_figure_images(
            generation,
            records,
            source_text=source_text,
        )
    )
    generation._TERMINAL_FIGURE_CONTRACT_VERSION = _CONTRACT_VERSION

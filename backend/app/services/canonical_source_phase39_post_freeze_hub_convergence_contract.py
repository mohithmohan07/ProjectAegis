"""Phase 3.9 post-freeze Activity/Info Hub convergence.

A live RNE acceptance run reached frozen-topology Type allocation with every
source QID and Type host certified, then failed the closed source-inventory gate
with ``hub_contract=8``. The source-owned Hub rows had already been rebuilt
successfully. The late Phase 2.1 wire-format normalizer then removed a repeated
leading ``Activity`` token from generic source labels, while the Hub validator
recomputed the pre-normalized note. Eight host rows were therefore reported as
``noncanonical_content`` even though all ten activities were present exactly
once on their independently certified hosts.

This contract makes the compact Hub note idempotent under the Phase 2.1 final
renderer, performs one final deterministic Hub rebuild immediately before the
post-freeze validator, and emits exact qid/reason diagnostics if any genuine Hub
placement defect remains. Numbered source identities such as ``Activity 11.1``
remain visible even while a truly duplicated generic ``Activity — Activity —``
prefix is collapsed. No source task, QID, Figure, Type, or concept topology is
changed.
"""
from __future__ import annotations

from collections import Counter
from functools import wraps
import re
from typing import Any, Callable

from . import canonical_source_phase21_render as phase21_render
from . import progress

_CONTRACT_VERSION = 1
_HUB_CONTRACT_VERSION = "phase3.9-post-freeze-hub-convergence-1"
_NUMBERED_REPEATED_KIND_RE = re.compile(
    r"^\s*(?P<outer>Activity|Project|Discuss)\b\s*[—–:-]\s*"
    r"(?P<inner>Activity|Project|Discuss)\b\s+"
    r"(?P<identifier>\d+(?:\.\d+)*(?:\s*\([A-Za-z]\))?)"
    r"(?P<rest>.*)$",
    re.IGNORECASE | re.DOTALL,
)

HubNoteProvider = Callable[[dict[str, Any]], str]
HubContentCleaner = Callable[[str], str]
HubNormalizer = Callable[..., list[dict[str, Any]]]
FinalValidator = Callable[..., Any]


def _clean_activity_hub_content(
    original: HubContentCleaner,
    content: str,
) -> str:
    """Collapse only a truly generic duplicate, preserving numbered identity.

    The legacy Phase 2.1 cleaner correctly turns
    ``Activity — Activity — Observe ...`` into ``Activity — Observe ...``. Its
    broad repeated-kind regex also turned ``Activity — Activity 11.1: ...`` into
    ``Activity — 11.1: ...``, losing the source label expected by existing public
    contracts. Preserve the second kind only when it is part of a numbered source
    identity; all other cleanup remains delegated to the established renderer.
    """
    value = str(content or "").strip()
    match = _NUMBERED_REPEATED_KIND_RE.match(value)
    if match and match.group("outer").casefold() == match.group("inner").casefold():
        kind = match.group("outer").title()
        identity = (
            f"{match.group('inner').title()} {match.group('identifier')}"
            f"{match.group('rest')}"
        )
        identity = re.sub(r"\s+", " ", identity).strip()
        return f"{kind} — {identity}".rstrip(" —")
    return original(value)


def _canonical_compact_hub_note(
    generation: Any,
    original: HubNoteProvider,
    item: dict[str, Any],
) -> str:
    """Return the exact note that the late Phase 2.1 renderer will preserve.

    A generic source label such as ``Activity`` deliberately contributes a stable
    identity marker in the legacy note:

        Activity — Activity — Compare the two prints: ...

    Phase 2.1 removes the repeated leading kind and ships:

        Activity — Compare the two prints: ...

    A numbered source identity remains explicit:

        Activity — Activity 11.1: ...

    The validator must compute that same final wire value rather than comparing
    the shipped note with its pre-render predecessor.
    """
    rendered = str(original(item) or "").strip()
    cleaned = phase21_render.clean_activity_hub_content(rendered)
    cleaned = generation.kr.canonicalize_rich_text(cleaned).strip()
    if generation.kr.rich_text_issues(cleaned):
        repaired = generation.kr.repair_unwrapped_math(cleaned)
        if not generation.kr.rich_text_issues(repaired):
            cleaned = repaired
    return cleaned


def _violation_summary(violations: list[dict[str, Any]]) -> str:
    counts = Counter(
        str(row.get("reason") or "unknown")
        for row in violations
        if isinstance(row, dict)
    )
    return ", ".join(
        f"{reason}={count}" for reason, count in sorted(counts.items())
    ) or "unknown=0"


def _log_hub_violations(
    violations: list[dict[str, Any]],
    *,
    stage: str,
) -> None:
    if not violations:
        return
    progress.log(
        "Phase 3.9 Activity/Info Hub convergence still found "
        f"{len(violations)} violation(s) at {stage}: "
        f"{_violation_summary(violations)}.",
        level="warning",
    )
    for row in violations[:20]:
        if not isinstance(row, dict):
            continue
        qid = str(row.get("qid") or "<unowned>")
        reason = str(row.get("reason") or "unknown")
        locations = row.get("locations") or []
        extra = []
        if row.get("expected_topic"):
            extra.append(f"expected_topic={row.get('expected_topic')!r}")
        if row.get("actual_topic"):
            extra.append(f"actual_topic={row.get('actual_topic')!r}")
        suffix = ("; " + "; ".join(extra)) if extra else ""
        progress.log(
            "Phase 3.9 Hub violation: "
            f"qid={qid!r}; reason={reason!r}; locations={locations!r}{suffix}",
            level="warning",
        )


def _normalize_with_hub_convergence(
    generation: Any,
    original: HubNormalizer,
    records: list[dict[str, Any]],
    inventory: dict[str, Any] | None,
    mined_types: dict[str, Any] | None = None,
    *args: Any,
    **kwargs: Any,
) -> list[dict[str, Any]]:
    """Rebuild source-owned Hubs and require idempotence after final rendering."""
    normalized = original(
        records,
        inventory,
        mined_types,
        *args,
        **kwargs,
    )
    violations = generation._hub_inventory_contract_violations(
        normalized,
        inventory,
    )
    if not violations:
        return normalized

    # The normalizer is source-owned and idempotent: it first removes every Hub
    # section/private qid marker, then rebuilds from the inventory and reviewed
    # host ledger. One bounded second pass repairs legacy checkpoint prose that
    # was itself transformed by an older final renderer.
    repaired = original(
        normalized,
        inventory,
        mined_types,
        *args,
        **kwargs,
    )
    remaining = generation._hub_inventory_contract_violations(
        repaired,
        inventory,
    )
    if len(remaining) < len(violations):
        progress.log(
            "Phase 3.9 repaired late Activity/Info Hub wire-format drift: "
            f"{len(violations)} violation(s) became {len(remaining)}.",
            level="success" if not remaining else "warning",
        )
    if remaining:
        _log_hub_violations(remaining, stage="post-render normalization")
    return repaired


def _should_converge_before_validation(
    generation: Any,
    records: object,
    inventory: object,
    stage: str,
) -> bool:
    if not isinstance(records, list) or not isinstance(inventory, dict):
        return False
    if "post-freeze" in stage.casefold():
        return True
    # Compatibility for terminal/deposit validators that retain the older stage
    # label but already carry public Hub content.
    return any(
        isinstance(row, dict)
        and bool(
            generation.cr.activity_hub_body(
                str(row.get("concept_details") or "")
            )
        )
        for row in records
    )


def _validate_with_hub_convergence(
    generation: Any,
    original: FinalValidator,
    records: list[dict[str, Any]],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> Any:
    inventory = kwargs.get("inventory")
    mined_types = kwargs.get("mined_types")
    stage = str(kwargs.get("stage") or "final")
    converge = _should_converge_before_validation(
        generation,
        records,
        inventory,
        stage,
    )

    if converge:
        rebuilt = generation._normalize_activity_hubs_from_inventory(
            records,
            inventory,
            mined_types,
        )
        records[:] = rebuilt
        before = generation._hub_inventory_contract_violations(
            records,
            inventory,
        )
        if before:
            _log_hub_violations(before, stage=f"before {stage}")

    result = original(records, *args, **kwargs)

    if converge:
        after = generation._hub_inventory_contract_violations(
            records,
            inventory,
        )
        if after:
            _log_hub_violations(after, stage=f"after {stage}")
            raise RuntimeError(
                f"{stage} Phase 3.9 Hub convergence failed: "
                f"{_violation_summary(after)}"
            )
        item_count = len(generation._hub_inventory_items(inventory))
        if item_count:
            progress.log(
                "Phase 3.9 certified "
                f"{item_count}/{item_count} source-owned Activity/Info Hub "
                "item(s) after final Type/taxonomy rendering.",
                level="success",
            )
    return result


def install(generation: Any | None = None) -> None:
    if generation is None:
        from . import generation as generation

    if (
        getattr(generation, "_PHASE39_POST_FREEZE_HUB_VERSION", 0)
        >= _CONTRACT_VERSION
    ):
        return

    original_clean = phase21_render.clean_activity_hub_content
    original_note = generation._compact_activity_hub_note
    original_normalize = generation._normalize_activity_hubs_from_inventory
    original_validate = generation._validate_final_or_raise

    phase21_render._PHASE39_ORIGINAL_CLEAN_ACTIVITY_HUB_CONTENT = original_clean
    generation._PHASE39_ORIGINAL_COMPACT_HUB_NOTE = original_note
    generation._PHASE39_ORIGINAL_NORMALIZE_HUBS = original_normalize
    generation._PHASE39_ORIGINAL_VALIDATE_FINAL = original_validate

    @wraps(original_clean)
    def clean_activity_hub_content(content: str) -> str:
        return _clean_activity_hub_content(
            phase21_render._PHASE39_ORIGINAL_CLEAN_ACTIVITY_HUB_CONTENT,
            content,
        )

    @wraps(original_note)
    def compact_activity_hub_note(item: dict[str, Any]) -> str:
        return _canonical_compact_hub_note(
            generation,
            generation._PHASE39_ORIGINAL_COMPACT_HUB_NOTE,
            item,
        )

    @wraps(original_normalize)
    def normalize_activity_hubs_from_inventory(
        records: list[dict[str, Any]],
        inventory: dict[str, Any] | None,
        mined_types: dict[str, Any] | None = None,
        *args: Any,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        return _normalize_with_hub_convergence(
            generation,
            generation._PHASE39_ORIGINAL_NORMALIZE_HUBS,
            records,
            inventory,
            mined_types,
            *args,
            **kwargs,
        )

    @wraps(original_validate)
    def validate_final(
        records: list[dict[str, Any]],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        return _validate_with_hub_convergence(
            generation,
            generation._PHASE39_ORIGINAL_VALIDATE_FINAL,
            records,
            args,
            kwargs,
        )

    phase21_render.clean_activity_hub_content = clean_activity_hub_content
    generation._compact_activity_hub_note = compact_activity_hub_note
    generation._normalize_activity_hubs_from_inventory = (
        normalize_activity_hubs_from_inventory
    )
    generation._validate_final_or_raise = validate_final
    generation._PHASE39_HUB_CONTRACT_VERSION = _HUB_CONTRACT_VERSION
    generation._PHASE39_POST_FREEZE_HUB_VERSION = _CONTRACT_VERSION

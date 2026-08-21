"""Closed-world source-inventory validation for rendered Type Examples.

The flat public ``Types`` wire format uses the same ``Type NN:``, ``Case NN:``
and ``Example NN:`` tokens that can legitimately occur inside a textbook
question. Coverage already treats a known inventory prompt as one atomic span,
but the late unexpected-Example validator historically split that same prompt
on its internal tokens. The result was the contradictory sequence seen in
production: repair placed the missing source Examples, then deposit rejected
those exact Examples as unowned parser fragments.

This module installs one inventory-aware scanner at the generation boundary. It
keeps the closed-world contract strict for genuinely invented Examples while
masking every exact, authoritative source prompt as an indivisible span.
"""
from __future__ import annotations

from functools import wraps
import re
from types import ModuleType

_CONTRACT_VERSION = 1
_EXAMPLE_MARKER_RE = re.compile(
    r"\bExamples?(?:\s+0*\d+)?\s*:\s*", re.IGNORECASE
)
_STRUCTURAL_PREFIX_RE = re.compile(
    r"^(?:(?:Miscellaneous\s+)?Type\s+\d{1,2}:"
    r"|Case\s+\d{1,2}:"
    r"|Examples?(?:\s+0*\d+)?\s*:)",
    re.IGNORECASE,
)
_STRUCTURAL_TOKEN_RE = re.compile(
    r"\b(?:(?:Miscellaneous\s+)?Type\s+\d{1,2}:"
    r"|Case\s+\d{1,2}:"
    r"|Examples?(?:\s+0*\d+)?\s*:)",
    re.IGNORECASE,
)


def _expected_keys(generation: ModuleType, inventory: dict | None) -> set[str]:
    keys: set[str] = set()
    for item in (inventory or {}).get("items") or []:
        if not isinstance(item, dict):
            continue
        source_kind = str(item.get("source_kind") or "").strip().lower()
        if source_kind in generation._HUB_INVENTORY_KINDS:
            continue
        key = generation._inventory_coverage_key(
            generation._inventory_task_text(item)
        )
        if key:
            keys.add(key)
    return keys


def _owned_prompt_at_marker(
    suffix: str,
    expected_keys: tuple[str, ...],
) -> str:
    """Return the exact inventory key occupying this Example slot, if any."""
    for key in expected_keys:
        if not suffix.startswith(key):
            continue
        tail = suffix[len(key):].lstrip()
        if not tail or _STRUCTURAL_PREFIX_RE.match(tail):
            return key
    return ""


def unexpected_rendered_type_examples(
    generation: ModuleType,
    records: list[dict],
    inventory: dict | None,
) -> list[dict]:
    """Return only genuinely unowned public Examples.

    Known source prompts are matched immediately after a structural Example
    marker using the same canonical key and boundary rule as exact coverage.
    Once matched, their full span is skipped, so literal Type/Case/Example
    labels inside the source wording can never be reinterpreted as public
    hierarchy. Unmatched Example slots remain closed-world failures.
    """
    expected = _expected_keys(generation, inventory)
    expected_longest_first = tuple(sorted(expected, key=len, reverse=True))
    reason = "not_in_inventory" if expected else "no_inventory_owner"
    unexpected: list[dict] = []

    for record in records:
        body = generation._inventory_coverage_key(
            generation._types_body(record.get("concept_details", ""))
        )
        if not body:
            continue
        owned_until = -1
        for marker in _EXAMPLE_MARKER_RE.finditer(body):
            if marker.start() < owned_until:
                continue
            suffix = body[marker.end():]
            owned_key = _owned_prompt_at_marker(
                suffix, expected_longest_first
            )
            if owned_key:
                owned_until = marker.end() + len(owned_key)
                continue

            next_token = _STRUCTURAL_TOKEN_RE.search(body, marker.end())
            end = next_token.start() if next_token else len(body)
            candidate = body[marker.end():end].strip()
            key = generation._inventory_coverage_key(candidate)
            if not key:
                continue
            unexpected.append({
                "example": generation._diagnostic_snippet(candidate),
                # The full wording rides beside the snippet so the release
                # adjudication can judge the Example, not a truncation.
                "example_text": candidate,
                "reason": reason,
            })
    return unexpected


def install(generation: ModuleType) -> None:
    """Install the atomic source-prompt scanner exactly once."""
    if getattr(
        generation, "_CLOSED_INVENTORY_CONTRACT_VERSION", 0
    ) >= _CONTRACT_VERSION:
        return

    original_repair = generation._repair_rendered_inventory_coverage

    def unexpected(records: list[dict], inventory: dict | None) -> list[dict]:
        return unexpected_rendered_type_examples(
            generation, records, inventory
        )

    @wraps(original_repair)
    def repair(
        records: list[dict],
        inventory: dict | None,
        mined_types: dict | None = None,
    ) -> list[dict]:
        repaired = original_repair(records, inventory, mined_types)
        unowned = unexpected(repaired, inventory)
        if unowned:
            # Truthful, and only a heads-up: nothing blocks here. The
            # release stage re-scans the rows that actually ship and
            # adjudicates each unowned Example with a recorded verdict
            # (concept_example_ownership) — the finding never evaporates.
            generation.progress.log(
                "Rendered inventory repair leaves "
                f"{len(unowned)} public Example(s) without an exact "
                "inventory owner; the release stage adjudicates and "
                "records each on the release.",
                level="warning",
            )
        return repaired

    generation._unexpected_rendered_type_examples = unexpected
    generation._repair_rendered_inventory_coverage = repair
    generation._CLOSED_INVENTORY_CONTRACT_VERSION = _CONTRACT_VERSION

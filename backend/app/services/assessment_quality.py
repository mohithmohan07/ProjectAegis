"""Touched-group QA (MES spec §6 Stage 11).

A final holistic model review of every group the run touched: question
correctness, answer/rubric consistency, home-concept entailment, internal
cohesion, separation from sibling groups, difficulty compatibility, and
image/rich-text integrity. The reviewer only ever FLAGS — a QA concern is
recorded on the group and rides into the release diagnostics; it never
blocks generation, never rewrites anything, and never removes a question
(spec §13.3: no semantic uncertainty pauses generation).

Deterministic checks here are mechanics: exact disjoint QID partition
across a concept's groups and blueprint-coverage arithmetic.
"""
from __future__ import annotations

import json
from typing import Callable, Mapping

from . import assessment_release as rel

QA_SYSTEM = (
    "You are the Aegis touched-group QA reviewer. Review ONE assessment "
    "group holistically against its member questions and its home "
    "concept: question correctness; answer/rubric consistency; whether "
    "the home concept actually entails every member; internal cohesion "
    "(one variant family, not a mixture); separation from the sibling "
    "groups supplied; difficulty compatibility with the tier; and image "
    "and rich-text integrity.\n"
    "You only FLAG. You never rewrite, re-place, or remove anything.\n"
    "Return ONLY strict JSON:\n"
    '{"flags":[{"code":"","member_candidate_id":"","detail":""}]} — an '
    "empty flags list means the group passes."
)


def review_group(
    group: Mapping,
    members: list[Mapping],
    *,
    siblings: list[Mapping] | None = None,
    concept_description: str = "",
    meta: Mapping,
    reviewer_call: Callable[..., dict] | None = None,
) -> dict:
    """One touched group -> {"flags": [...]}. Never raises, never blocks."""
    if not members:
        return {"flags": []}
    if reviewer_call is None:
        from . import generation

        def reviewer_call(system, user):  # noqa: F811
            return generation._openai_json(
                system, user, purpose="concept_validation")

    from . import assessment_grouping

    payload = {
        "metadata": dict(meta),
        "group_key": str(group.get("group_key") or ""),
        "tier": str(group.get("group_type") or ""),
        "semantic_description": str(group.get("semantic_description") or ""),
        "concept_description": concept_description[:2_000],
        "members": assessment_grouping._member_payload(members),
        "sibling_groups": [
            {
                "group_key": str(s.get("group_key") or ""),
                "semantic_description": str(
                    s.get("semantic_description") or ""),
            }
            for s in siblings or []
        ],
    }
    try:
        review = reviewer_call(QA_SYSTEM, json.dumps(payload, ensure_ascii=False))
    except Exception as exc:  # noqa: BLE001 — QA never blocks the run
        return {"flags": [{
            "code": "qa_unavailable",
            "member_candidate_id": "",
            "detail": f"{type(exc).__name__}: {exc}",
        }]}
    flags = []
    for flag in (review or {}).get("flags") or []:
        if isinstance(flag, Mapping) and str(flag.get("code") or "").strip():
            flags.append({
                "code": str(flag.get("code") or ""),
                "member_candidate_id": str(
                    flag.get("member_candidate_id") or ""),
                "detail": str(flag.get("detail") or ""),
            })
    return {"flags": flags}


# --------------------------------------------------------------------------- #
# Deterministic gates (mechanics only)
# --------------------------------------------------------------------------- #

def partition_errors(groups: list[Mapping]) -> list[str]:
    """Every member candidate id appears in exactly one group."""
    seen: dict[str, str] = {}
    errors: list[str] = []
    for group in groups:
        key = str(group.get("group_key") or "")
        for member in group.get("member_candidate_ids") or []:
            member = str(member)
            if member in seen:
                errors.append(
                    f"{member} is in both {seen[member]} and {key}")
            seen[member] = key
    return errors


def blueprint_coverage_report(
    candidates: list[Mapping], cells: list[Mapping],
) -> dict:
    """Exact cell-fulfilment arithmetic: fulfilled, unfulfilled, orphaned."""
    required: dict[str, int] = {
        str(cell.get("cell_id") or ""): int(cell.get("count") or 0)
        for cell in cells
    }
    fulfilled: dict[str, int] = {}
    orphaned: list[str] = []
    for candidate in candidates:
        cell_id = str(candidate.get("blueprint_cell_id") or "")
        if cell_id in required:
            fulfilled[cell_id] = fulfilled.get(cell_id, 0) + 1
        else:
            orphaned.append(str(candidate.get("candidate_id") or ""))
    unfulfilled = {
        cell_id: count - fulfilled.get(cell_id, 0)
        for cell_id, count in required.items()
        if fulfilled.get(cell_id, 0) < count
    }
    overfilled = {
        cell_id: fulfilled.get(cell_id, 0) - count
        for cell_id, count in required.items()
        if fulfilled.get(cell_id, 0) > count
    }
    return {
        "unfulfilled": unfulfilled,
        "overfilled": overfilled,
        "orphaned": orphaned,
        "complete": not unfulfilled and not overfilled and not orphaned,
    }

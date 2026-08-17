"""Release-domain mechanics for MES assessment releases.

The MES specification (§5, §13, §14) builds everything around an immutable
``AssessmentRelease``: the Concept File and the Master File are projections
of one frozen release object, never of mutable database rows.

Everything in this module is deterministic *mechanics* the spec explicitly
allows local code to enforce (§4): schemas and enums, exact IDs and hashes,
exact-once primary placement, group-identity uniqueness, state transitions,
and the zero-loss invariant. Nothing here makes a pedagogical decision —
semantic authorship and acceptance belong to the API author/critic stages.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any, Iterable, Mapping

# --------------------------------------------------------------------------- #
# Release state machine (spec §5.6)
# --------------------------------------------------------------------------- #

RELEASE_STATES = (
    "draft",
    "materialized",
    "validated_with_flags",
    "ready_for_upload",
    "publication_pending",
    "uploaded",
    "superseded",
)

# A release only ever moves forward; ``superseded`` is reachable from every
# non-final state because a new version can replace an old one at any point.
_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"materialized", "superseded"}),
    "materialized": frozenset(
        {"validated_with_flags", "ready_for_upload", "superseded"}),
    # Released-with-warnings stays uploadable: only a BLOCKED readiness
    # refuses the database (spec §13.3); flags ride along visibly.
    "validated_with_flags": frozenset(
        {"ready_for_upload", "publication_pending", "superseded"}),
    "ready_for_upload": frozenset({"publication_pending", "superseded"}),
    "publication_pending": frozenset(
        {"uploaded", "ready_for_upload", "superseded"}),
    "uploaded": frozenset({"superseded"}),
    "superseded": frozenset(),
}


class ReleaseStateError(ValueError):
    """An illegal release-state transition was requested."""


def advance_state(current: str, target: str) -> str:
    """Validate one state transition; returns the target on success."""
    if current not in _ALLOWED_TRANSITIONS:
        raise ReleaseStateError(f"unknown release state {current!r}")
    if target not in RELEASE_STATES:
        raise ReleaseStateError(f"unknown release state {target!r}")
    if target not in _ALLOWED_TRANSITIONS[current]:
        raise ReleaseStateError(
            f"illegal release transition {current!r} -> {target!r}")
    return target


# --------------------------------------------------------------------------- #
# Content hashing
# --------------------------------------------------------------------------- #

def canonical_json(value: Any) -> str:
    """Stable JSON serialization for hashing (sorted keys, no whitespace)."""
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def new_release_uid() -> str:
    return f"REL-{uuid.uuid4().hex[:20]}"


# --------------------------------------------------------------------------- #
# Record schemas (spec §5.1–§5.5) — presence/enum validation only
# --------------------------------------------------------------------------- #

GROUP_TYPES = ("Basic", "Intermediate", "Advanced")

# The reference workbooks mark the correct Objective option "Yes" (wrong
# options "No"); older internal data used "1"/"true". All are accepted as
# the correct-option marker; nothing else is.
_CORRECT_OPTION_MARKERS = frozenset({"1", "true", "yes"})


def is_correct_option(value) -> bool:
    return str(value or "").strip().lower() in _CORRECT_OPTION_MARKERS
SHEET_KINDS = ("objective", "descriptive")
ANSWER_RESTRICTIONS = ("Open", "Specific")

_SOURCE_ATOM_REQUIRED = (
    "source_qid", "source_document_hash", "source_kind", "raw_text",
)
_BLUEPRINT_CELL_REQUIRED = (
    "cell_id", "sheet_kind", "question_category", "cognitive_skill",
    "difficulty", "marks", "count", "appears_in", "source_policy",
)
_CANDIDATE_REQUIRED = (
    "candidate_id", "source_atom_ids", "blueprint_cell_id", "question",
    "question_text", "sheet_kind", "question_category", "cognitive_skill",
    "difficulty", "marks", "answer_restriction",
)
_PLACEMENT_REQUIRED = (
    "candidate_id", "concept_id", "group_key", "evidence",
)
_GROUP_REQUIRED = (
    "group_key", "concept_id", "group_type", "group_sequence", "group_name",
    "group_display_name",
)


def _missing(record: Mapping, required: Iterable[str]) -> list[str]:
    return [
        field for field in required
        if str(record.get(field) if record.get(field) is not None else "")
        .strip() == ""
        and record.get(field) not in (0, 0.0)
    ]


def validate_source_atom(atom: Mapping) -> list[str]:
    errors = [f"missing {f}" for f in _missing(atom, _SOURCE_ATOM_REQUIRED)]
    for asset in atom.get("assets") or []:
        if not str(asset.get("url") or "").startswith("https://"):
            errors.append(
                f"asset without HTTPS url on {atom.get('source_qid')}")
        if not str(asset.get("alt") or "").strip():
            errors.append(f"asset without alt on {atom.get('source_qid')}")
    return errors


def validate_blueprint_cell(cell: Mapping) -> list[str]:
    errors = [
        f"missing {f}" for f in _missing(cell, _BLUEPRINT_CELL_REQUIRED)
    ]
    if cell.get("sheet_kind") not in SHEET_KINDS:
        errors.append(
            f"sheet_kind must be one of {SHEET_KINDS} "
            f"(got {cell.get('sheet_kind')!r}); MES never uses Subjective")
    return errors


def validate_candidate(candidate: Mapping) -> list[str]:
    errors = [f"missing {f}" for f in _missing(candidate, _CANDIDATE_REQUIRED)]
    if candidate.get("sheet_kind") not in SHEET_KINDS:
        errors.append(
            f"sheet_kind must be one of {SHEET_KINDS} "
            f"(got {candidate.get('sheet_kind')!r})")
    restriction = candidate.get("answer_restriction")
    if restriction not in ANSWER_RESTRICTIONS:
        # Never silently default an unknown restriction (spec §3.5).
        errors.append(
            f"answer_restriction must be one of {ANSWER_RESTRICTIONS} "
            f"(got {restriction!r})")
    return errors


def validate_placement(placement: Mapping) -> list[str]:
    errors = [f"missing {f}" for f in _missing(placement, _PLACEMENT_REQUIRED)]
    secondary = placement.get("secondary_placements")
    if secondary:
        # MES automatic secondary placements are off (spec §3.7).
        errors.append(
            "secondary_placements must be empty for MES "
            f"(got {len(secondary)})")
    return errors


def validate_group(group: Mapping) -> list[str]:
    errors = [f"missing {f}" for f in _missing(group, _GROUP_REQUIRED)]
    if group.get("group_type") not in GROUP_TYPES:
        errors.append(
            f"group_type must be one of {GROUP_TYPES} "
            f"(got {group.get('group_type')!r})")
    return errors


# --------------------------------------------------------------------------- #
# Identity and placement invariants (spec §3.7, §5.5, §7.5)
# --------------------------------------------------------------------------- #

def group_identity(group: Mapping) -> tuple:
    """The uniqueness unit: (concept_id, group_type, group_key)."""
    return (
        group.get("concept_id"),
        str(group.get("group_type") or ""),
        str(group.get("group_key") or ""),
    )


def duplicate_group_identities(groups: Iterable[Mapping]) -> list[tuple]:
    seen: set[tuple] = set()
    duplicates: list[tuple] = []
    for group in groups:
        identity = group_identity(group)
        if identity in seen and identity not in duplicates:
            duplicates.append(identity)
        seen.add(identity)
    return duplicates


def duplicate_group_keys(groups: Iterable[Mapping]) -> list[str]:
    """Return repeated internal keys, regardless of concept or tier.

    ``group_key`` is the release-wide machine identity.  Reusing one key for
    a different home is structural corruption, not a semantic concern that a
    later judgment pass may accept with a flag.
    """
    seen: set[str] = set()
    duplicates: list[str] = []
    for group in groups:
        group_key = str(group.get("group_key") or "")
        if not group_key:
            continue
        if group_key in seen and group_key not in duplicates:
            duplicates.append(group_key)
        seen.add(group_key)
    return duplicates


def primary_placement_errors(placements: Iterable[Mapping]) -> list[str]:
    """Exactly one home concept and group per question (spec §3.7)."""
    errors: list[str] = []
    seen: dict[str, tuple] = {}
    for placement in placements:
        candidate_id = str(placement.get("candidate_id") or "")
        home = (
            placement.get("concept_id"),
            str(placement.get("group_key") or ""),
        )
        if candidate_id in seen:
            errors.append(
                f"{candidate_id}: more than one primary placement "
                f"({seen[candidate_id]} and {home})")
            continue
        seen[candidate_id] = home
    return errors


# --------------------------------------------------------------------------- #
# Zero-loss invariant (spec §14)
# --------------------------------------------------------------------------- #

def zero_loss_report(
    obligations: Iterable[str],
    accepted: Iterable[str],
    flagged: Iterable[str],
) -> dict:
    """source obligations = accepted rows + explicitly flagged rows.

    No QID, subpart, OR branch, or blueprint cell may disappear. Returns the
    exact identities that break the invariant so a defect is named, never
    hidden.
    """
    obligation_set = {str(o) for o in obligations if str(o).strip()}
    accepted_set = {str(a) for a in accepted if str(a).strip()}
    flagged_set = {str(f) for f in flagged if str(f).strip()}
    covered = accepted_set | flagged_set
    return {
        "missing": sorted(obligation_set - covered),
        "unexpected": sorted(covered - obligation_set),
        "double_counted": sorted(accepted_set & flagged_set),
        "holds": (
            obligation_set == covered and not (accepted_set & flagged_set)
        ),
    }


# --------------------------------------------------------------------------- #
# Release construction
# --------------------------------------------------------------------------- #

def freeze_payload(payload: Mapping) -> dict:
    """Validate and hash a complete release payload.

    Returns {"errors": [...], "hashes": {...}}. Mechanical gates only: any
    semantic concern lives in the payload's own flags, which are preserved,
    never resolved here.
    """
    errors: list[str] = []
    for atom in payload.get("source_atoms") or []:
        errors.extend(validate_source_atom(atom))
    for cell in payload.get("blueprint_cells") or []:
        errors.extend(validate_blueprint_cell(cell))
    for candidate in payload.get("candidates") or []:
        errors.extend(validate_candidate(candidate))
    for group in payload.get("groups") or []:
        errors.extend(validate_group(group))
    for placement in payload.get("placements") or []:
        errors.extend(validate_placement(placement))
    for identity in duplicate_group_identities(payload.get("groups") or []):
        errors.append(f"duplicate group identity {identity}")
    for group_key in duplicate_group_keys(payload.get("groups") or []):
        errors.append(f"duplicate group_key {group_key!r}")
    errors.extend(
        primary_placement_errors(payload.get("placements") or []))
    return {
        "errors": errors,
        "hashes": {
            "payload": sha256_json(payload),
            "source_atoms": sha256_json(payload.get("source_atoms") or []),
            "blueprint_cells": sha256_json(
                payload.get("blueprint_cells") or []),
            "candidates": sha256_json(payload.get("candidates") or []),
            "groups": sha256_json(payload.get("groups") or []),
            "placements": sha256_json(payload.get("placements") or []),
        },
    }

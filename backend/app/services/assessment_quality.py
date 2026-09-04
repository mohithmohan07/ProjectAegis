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

import copy
import json
from typing import Any, Mapping

from . import assessment_lane_policy as lane_policy
from .phase3 import kernel

QUALITY_POLICY_VERSION = "assessment-group-quality-1"

QA_SYSTEM = (
    "You are the Aegis touched-group QA reviewer. Review ONE assessment "
    "group holistically against its member questions and its home "
    "concept: question correctness; answer/rubric consistency; whether "
    "the home concept actually entails every member; internal cohesion "
    "(one variant family, not a mixture); separation from the sibling "
    "groups supplied; difficulty compatibility with the tier; and image "
    "and rich-text integrity.\n"
    "You only flag. You must not revise, rewrite, re-place, regroup, or "
    "remove anything. There is no quota: return every genuine concern and "
    "an empty flags list when there is none.\n"
    "Return ONLY strict JSON:\n"
    '{"group_key":"","flags":[{"code":"","member_candidate_id":"",'
    '"detail":""}]}\n'
    "group_key must echo the reviewed group exactly. A flag may leave "
    "member_candidate_id empty when it concerns the group as a whole."
)

QA_CRITIC_SYSTEM = (
    "You are the independent advisory critic for one touched-group quality "
    "review. Audit the proposed flags against the complete group, concept, "
    "member, and sibling evidence. You must not revise the group or the "
    "questions. Your dissent is advisory: the proposed review stands and "
    "your concerns ship for review. There is no quota. State your honest "
    "confidence.\n"
    "Return ONLY strict JSON:\n"
    '{"verdict":"verified","confidence":0.0,"issues":[]} or '
    '{"verdict":"dissent","confidence":0.0,"issues":["evidence-bound '
    'concern"]}'
)


class QualityError(ValueError):
    """The quality pass cannot bind its mechanical input identity."""


def _member_evidence(members: list[Mapping]) -> list[dict[str, Any]]:
    """Full member evidence, preserving the grouping pass's projection.

    ``assessment_grouping._member_payload`` is the shared semantic evidence
    projection. The complete member record is merged around it so answers,
    rubrics, subquestions, shared context, and assets are never truncated.
    A subquestion count is not evidence and is deliberately excluded.
    """

    from . import assessment_grouping

    projected = assessment_grouping._member_payload(members)
    evidence: list[dict[str, Any]] = []
    for member, shared in zip(members, projected):
        row = copy.deepcopy(dict(member))
        row.update(copy.deepcopy(dict(shared)))
        row.pop("sub_question_count", None)
        row["sub_questions"] = copy.deepcopy(
            list(member.get("sub_questions") or []))
        evidence.append(row)
    return evidence


def _sibling_evidence(siblings: list[Mapping] | None) -> list[dict[str, Any]]:
    """Normalize legacy group rows and complete ``group + members`` rows.

    Malformed evidence is a protocol defect, never something this semantic
    pass may silently omit.
    """

    evidence: list[dict[str, Any]] = []
    for sibling_position, sibling in enumerate(siblings or [], start=1):
        if not isinstance(sibling, Mapping):
            raise QualityError(
                f"sibling {sibling_position} is not an object"
            )
        nested_group = sibling.get("group")
        if nested_group is not None and not isinstance(nested_group, Mapping):
            raise QualityError(
                f"sibling {sibling_position} group is not an object"
            )
        group = nested_group if isinstance(nested_group, Mapping) else sibling
        raw_members = sibling.get("members")
        if raw_members is not None and not isinstance(raw_members, list):
            raise QualityError(
                f"sibling {sibling_position} members is not an array"
            )
        members = list(raw_members or [])
        for member_position, member in enumerate(members, start=1):
            if not isinstance(member, Mapping):
                raise QualityError(
                    f"sibling {sibling_position} member {member_position} "
                    "is not an object"
                )
        evidence.append({
            "group": copy.deepcopy(dict(group)),
            "members": _member_evidence(members),
        })
    return evidence


def _quality_checker(
    group_key: str, member_ids: set[str],
) -> kernel.Checker:
    """Mechanics only: identity binding and the authored flag schema."""

    def check(response: Mapping[str, Any]) -> list[str]:
        if not isinstance(response, Mapping):
            return ["response is not an object"]
        defects: list[str] = []
        if str(response.get("group_key") or "") != group_key:
            defects.append(
                f"group_key must echo {group_key!r}")
        flags = response.get("flags")
        if not isinstance(flags, list):
            return [*defects, "response has no flags array"]
        for position, flag in enumerate(flags, start=1):
            if not isinstance(flag, Mapping):
                defects.append(f"flag {position} is not an object")
                continue
            if not str(flag.get("code") or "").strip():
                defects.append(f"flag {position} has no code")
            if not str(flag.get("detail") or "").strip():
                defects.append(f"flag {position} has no detail")
            member_id = str(flag.get("member_candidate_id") or "")
            if member_id and member_id not in member_ids:
                defects.append(
                    f"flag {position} names unknown member_candidate_id "
                    f"{member_id!r}")
        return defects

    return check


def _live_review(payload: dict[str, Any]) -> dict[str, Any]:
    from . import generation

    return generation._openai_json(
        QA_SYSTEM, json.dumps(payload, ensure_ascii=False),
        purpose="concept_validation",
    )


def _live_critic(payload: dict[str, Any]) -> dict[str, Any]:
    from . import generation

    return generation._openai_json(
        QA_CRITIC_SYSTEM, json.dumps(payload, ensure_ascii=False),
        purpose="advisory_critic",
    )


def review_group(
    group: Mapping,
    members: list[Mapping],
    *,
    siblings: list[Mapping] | None = None,
    concept: Mapping,
    meta: Mapping,
    envelope_sha256: str,
    provider: kernel.Provider | None = None,
    critic: kernel.Critic | None = None,
    store: kernel.DecisionStore | None = None,
    fixer: kernel.Provider | None = None,
) -> dict:
    """Review one complete touched group through the decide-once kernel."""

    if not isinstance(group, Mapping):
        raise QualityError("group quality review requires a group object")
    if not isinstance(concept, Mapping):
        raise QualityError("group quality review requires a concept object")
    if not isinstance(meta, Mapping):
        raise QualityError("group quality review requires metadata")
    for position, member in enumerate(members, start=1):
        if not isinstance(member, Mapping):
            raise QualityError(
                f"group quality member {position} is not an object"
            )
    group_key = str(group.get("group_key") or "")
    if not group_key:
        raise QualityError("group quality review requires a group_key")
    if not members:
        return {
            "flags": [],
            "quality_review": "not_applicable",
            "authority": {},
        }
    envelope_sha = str(envelope_sha256 or "").strip()
    if not envelope_sha:
        raise QualityError(
            "group quality review requires an assessment envelope hash")
    member_ids = [str(member.get("candidate_id") or "") for member in members]
    if any(not member_id for member_id in member_ids):
        raise QualityError("group quality member has no candidate_id")
    if len(member_ids) != len(set(member_ids)):
        raise QualityError("group quality members repeat a candidate_id")

    if provider is None:
        from .phase3 import envelope as envelope_mod
        from .phase3 import fixer as fixer_mod

        envelope_mod.require_live_api()
        provider = _live_review
        if critic is None:
            critic = lane_policy.critic_for("qa", _live_critic)
        fixer = fixer or fixer_mod.live_fixer
    store = store or kernel.DecisionStore()

    payload = {
        "stage": "assessment.group_quality",
        "rules": QA_SYSTEM,
        "metadata": copy.deepcopy(dict(meta)),
        "group": copy.deepcopy(dict(group)),
        "concept": copy.deepcopy(dict(concept)),
        "members": _member_evidence(members),
        "sibling_groups": _sibling_evidence(siblings),
    }
    decision = kernel.decide(
        kind="assessment.group_quality",
        unit_id=group_key,
        envelope_sha256=envelope_sha,
        payload=payload,
        provider=provider,
        checker=_quality_checker(group_key, set(member_ids)),
        critic=critic,
        store=store,
        policy_version=QUALITY_POLICY_VERSION,
        fixer=fixer,
    )
    flags = [
        copy.deepcopy(dict(flag))
        for flag in decision["response"].get("flags") or []
        if isinstance(flag, Mapping)
    ]
    review_flags = [str(flag) for flag in decision.get("review_flags") or []]
    return {
        "flags": flags,
        "quality_review": "flagged" if flags or review_flags else "verified",
        "authority": {
            "decision_key": str(decision.get("key") or ""),
            "policy_version": str(decision.get("policy_version") or ""),
            "review_flags": review_flags,
            "fixer": bool(decision.get("fixer")),
        },
    }


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

"""Durable human gate for anomalously fragmented mined Type taxonomies.

The gate is deliberately deterministic: it spends no model call deciding
whether to pause.  A high Type-to-QID ratio after the ordinary consolidation
pass is evidence that the model may have created one assessment Type per
question instead of reusable patterns.  The operator can keep that taxonomy,
request one bounded consolidation proposal plus critic, or provide a custom
grouping instruction.

This decision never weakens exact-once QID coverage, topic/activity/scope
contracts, source wording, host review, or final validation.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator, Mapping

from . import semantic_confidence_policy as confidence_policy
from .semantic_recovery import HumanDecisionRequired


_GATE_VERSION = "type-granularity-human-gate-3"
_MIN_INVENTORY_ITEMS = 12
_MIN_TYPE_COUNT = 10
_HIGH_TYPE_QID_RATIO = 0.80

_HUMAN_RESOLUTIONS: ContextVar[Any] = ContextVar(
    "aegis_type_granularity_human_resolutions",
    default=None,
)


@contextmanager
def human_resolution_context(resolutions: Any) -> Iterator[None]:
    """Expose durable orchestration resolutions to one generation attempt."""

    token = _HUMAN_RESOLUTIONS.set(copy.deepcopy(resolutions))
    try:
        yield
    finally:
        _HUMAN_RESOLUTIONS.reset(token)


def _plain_json(value: Any) -> Any:
    return json.loads(json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    ))


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        _plain_json(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def _normal(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def _inventory_identity(inventory: Mapping[str, Any] | None) -> list[dict]:
    """Bind the decision to exact task semantics without persisting prose."""

    out: list[dict] = []
    for item in (inventory or {}).get("items") or []:
        if not isinstance(item, Mapping):
            continue
        wording = "\n".join(
            str(item.get(key) or "")
            for key in (
                "question",
                "task",
                "prompt",
                "raw_task",
                "task_text",
                "question_text",
                "source_text",
                "source_label",
                "context",
            )
        )
        out.append({
            "qid": str(item.get("qid") or ""),
            "topic": str(item.get("topic_hint") or item.get("topic") or ""),
            "task_kind": str(item.get("task_kind") or ""),
            "is_activity": bool(item.get("is_activity")),
            "placement_scope": str(item.get("placement_scope") or ""),
            "requires_visual": bool(item.get("requires_visual") is True),
            "wording_sha256": hashlib.sha256(
                wording.encode("utf-8")
            ).hexdigest(),
            # Bind every source-owned task attribute without persisting its
            # prose in the decision ledger.  Visual/context objects, source
            # block/page/order bindings, options, normalized task text, and
            # future inventory fields can all affect a safe Type/Case merge.
            "source_item_sha256": _sha256_json(dict(item)),
        })
    return out


def _type_identity(mined_types: Mapping[str, Any] | None) -> list[dict]:
    """Bind the choice to Type/QID/Case meaning and exact example wording."""

    out: list[dict] = []
    for mtype in (mined_types or {}).get("types") or []:
        if not isinstance(mtype, Mapping):
            continue
        cases: list[dict] = []
        for case in mtype.get("case_prompts") or []:
            if not isinstance(case, Mapping):
                continue
            examples: list[dict] = []
            for example in case.get("examples") or []:
                if not isinstance(example, Mapping):
                    continue
                examples.append({
                    "qid": str(example.get("source_question_id") or ""),
                    "prompt_sha256": hashlib.sha256(str(
                        example.get("example_prompt") or ""
                    ).encode("utf-8")).hexdigest(),
                })
            legacy_qid = str(case.get("source_question_id") or "")
            legacy_prompt = str(case.get("case_prompt") or "")
            if legacy_qid or legacy_prompt:
                examples.append({
                    "qid": legacy_qid,
                    "prompt_sha256": hashlib.sha256(
                        legacy_prompt.encode("utf-8")
                    ).hexdigest(),
                })
            cases.append({
                "case_id": str(case.get("case_id") or ""),
                "case_title": str(case.get("case_title") or ""),
                "case_signature": str(case.get("case_signature") or ""),
                "placement_scope": str(case.get("placement_scope") or ""),
                "examples": examples,
            })
        out.append({
            "type_id": str(mtype.get("type_id") or ""),
            "type_title": str(mtype.get("type_title") or ""),
            "type_description": str(mtype.get("type_description") or ""),
            "task_pattern": str(mtype.get("task_pattern") or ""),
            "concept_match_hint": str(
                mtype.get("concept_match_hint") or ""
            ),
            "parent_concept_match_hint": str(
                mtype.get("parent_concept_match_hint") or ""
            ),
            "source_question_ids": [
                str(qid) for qid in mtype.get("source_question_ids") or []
            ],
            "topic_match_hint": str(mtype.get("topic_match_hint") or ""),
            "difficulty_hint": str(mtype.get("difficulty_hint") or ""),
            "cognitive_skill_hint": str(
                mtype.get("cognitive_skill_hint") or ""
            ),
            "subject_skill_hint": str(
                mtype.get("subject_skill_hint") or ""
            ),
            "is_activity": bool(mtype.get("is_activity")),
            "placement_scope": str(mtype.get("placement_scope") or ""),
            "cases": cases,
        })
    return out


def build_review(
    *,
    raw_type_count: int,
    consolidated_type_count: int,
    inventory_count: int,
    sufficiency_added_concepts: int,
    sufficiency_audit_complete: bool = True,
) -> dict[str, Any]:
    """Return the deterministic metrics saved beside the mined taxonomy."""

    inventory_count = max(0, int(inventory_count or 0))
    consolidated_type_count = max(0, int(consolidated_type_count or 0))
    raw_type_count = max(0, int(raw_type_count or 0))
    return {
        "version": _GATE_VERSION,
        "raw_type_count": raw_type_count,
        "type_count": consolidated_type_count,
        "inventory_count": inventory_count,
        "consolidation_merged_count": max(
            0, raw_type_count - consolidated_type_count),
        "sufficiency_added_concepts": max(
            0, int(sufficiency_added_concepts or 0)),
        "sufficiency_audit_complete": bool(sufficiency_audit_complete),
        "type_qid_ratio": (
            consolidated_type_count / inventory_count
            if inventory_count else 0.0
        ),
    }


def is_anomalously_fragmented(review: Mapping[str, Any] | None) -> bool:
    """Detect only a strong, size-bounded fragmentation signal."""

    review = review or {}
    try:
        inventory_count = int(review.get("inventory_count") or 0)
        type_count = int(review.get("type_count") or 0)
        merged_count = int(review.get("consolidation_merged_count") or 0)
        ratio = float(review.get("type_qid_ratio") or 0.0)
    except (TypeError, ValueError):
        return False
    return bool(
        inventory_count >= _MIN_INVENTORY_ITEMS
        and type_count >= _MIN_TYPE_COUNT
        and ratio >= _HIGH_TYPE_QID_RATIO
        and merged_count == 0
    )


def _resolution_candidates(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, Mapping):
        row = dict(value)
        nested = row.get("resolution")
        if isinstance(nested, Mapping):
            row = {**row, **dict(nested)}
        if str(row.get("choice") or "").strip():
            return [row]
        out: list[dict[str, Any]] = []
        for key, raw in value.items():
            if not isinstance(raw, Mapping):
                continue
            candidate = dict(raw)
            candidate.setdefault("_lookup_key", str(key))
            nested = candidate.get("resolution")
            if isinstance(nested, Mapping):
                candidate = {**candidate, **dict(nested)}
            out.append(candidate)
        return out
    if isinstance(value, (list, tuple)):
        out: list[dict[str, Any]] = []
        for raw in value:
            out.extend(_resolution_candidates(raw))
        return out
    return []


def _resolution_for(identity: Mapping[str, str]) -> dict[str, Any] | None:
    decision_id = str(identity.get("decision_id") or "")
    context_hash = str(identity.get("context_hash") or "")
    matched: dict[str, Any] | None = None
    for candidate in _resolution_candidates(_HUMAN_RESOLUTIONS.get()):
        if _normal(candidate.get("status")) in {
            "pending", "awaiting_decision", "cancelled", "rejected",
        }:
            continue
        identifiers = {
            str(candidate.get("decision_id") or ""),
            str(candidate.get("context_hash") or ""),
            str(candidate.get("_lookup_key") or ""),
        }
        if decision_id not in identifiers and context_hash not in identifiers:
            continue
        if str(candidate.get("context_hash") or context_hash) != context_hash:
            continue
        choice = _normal(candidate.get("choice")).replace(" ", "_")
        if choice not in {
            "consolidate_types",
            "keep_distinct_types",
            "custom_instruction",
        }:
            continue
        matched = {
            "decision_id": decision_id,
            "context_hash": context_hash,
            "choice": choice,
            "instruction": str(candidate.get("instruction") or "").strip(),
        }
    return copy.deepcopy(matched)


def _identity(
    *,
    review: Mapping[str, Any],
    inventory: Mapping[str, Any] | None,
    mined_types: Mapping[str, Any] | None,
    meta: Mapping[str, Any] | None,
    prior_decision_id: str = "",
    failure: str = "",
) -> dict[str, str]:
    context_hash = _sha256_json({
        "version": _GATE_VERSION,
        "semantic_confidence_policy": confidence_policy.cache_identity(),
        "metadata": {
            key: str((meta or {}).get(key) or "")
            for key in (
                "board", "grade", "subject", "unit", "chapter_title",
                "chapter_code", "learning_kind",
            )
        },
        "review": dict(review),
        "inventory": _inventory_identity(inventory),
        "types": _type_identity(mined_types),
        "prior_decision_id": str(prior_decision_id or ""),
        "failed_direction_sha256": (
            hashlib.sha256(str(failure).encode("utf-8")).hexdigest()
            if failure else ""
        ),
    })
    return {
        "context_hash": context_hash,
        "decision_id": f"type-granularity-{context_hash[:24]}",
    }


def applied_result_context_hash(
    *,
    review: Mapping[str, Any],
    inventory: Mapping[str, Any] | None,
    mined_types: Mapping[str, Any] | None,
    meta: Mapping[str, Any] | None,
) -> str:
    """Fingerprint the exact accepted output state for safe checkpoint replay.

    The original decision hash binds the pre-decision taxonomy. A successful
    consolidation necessarily changes that taxonomy, so resumed checkpoints
    need a second identity for the accepted result. Human/audit bookkeeping is
    excluded; policy, source-task semantics, and Type semantics are not.
    """

    stable_review = {
        key: copy.deepcopy(review.get(key))
        for key in (
            "version",
            "raw_type_count",
            "type_count",
            "inventory_count",
            "consolidation_merged_count",
            "sufficiency_added_concepts",
            "sufficiency_audit_complete",
            "type_qid_ratio",
        )
    }
    return _sha256_json({
        "version": "type-granularity-applied-result-2",
        "semantic_confidence_policy": confidence_policy.cache_identity(),
        "metadata": {
            key: str((meta or {}).get(key) or "")
            for key in (
                "board", "grade", "subject", "unit", "chapter_title",
                "chapter_code", "learning_kind",
            )
        },
        "review": stable_review,
        "inventory": _inventory_identity(inventory),
        "types": _type_identity(mined_types),
    })


def applied_result_semantic_hash(
    *,
    inventory: Mapping[str, Any] | None,
    mined_types: Mapping[str, Any] | None,
) -> str:
    """Metadata-independent replay seal for every later checkpoint stage.

    Post-assignment and final checkpoints resume after the interactive gate,
    where runtime metadata is no longer available to the shape-compatibility
    selector.  This seal still binds every source task, immutable Type/Case
    field, and visual requirement so a changed/tampered result cannot bypass
    the approved Type-only consolidation on a later-stage replay.
    """

    return _sha256_json({
        "version": "type-granularity-applied-semantics-1",
        "semantic_confidence_policy": confidence_policy.cache_identity(),
        "inventory": _inventory_identity(inventory),
        "types": _type_identity(mined_types),
    })


def _pending_decision(
    *,
    identity: Mapping[str, str],
    review: Mapping[str, Any],
    inventory: Mapping[str, Any] | None,
    failure: str = "",
) -> dict[str, Any]:
    type_count = int(review.get("type_count") or 0)
    inventory_count = int(review.get("inventory_count") or 0)
    ratio = float(review.get("type_qid_ratio") or 0.0)
    merged = int(review.get("consolidation_merged_count") or 0)
    additions = int(review.get("sufficiency_added_concepts") or 0)
    sufficiency_complete = bool(
        review.get("sufficiency_audit_complete", True))
    qids = [
        str(item.get("qid") or "")
        for item in (inventory or {}).get("items") or []
        if isinstance(item, Mapping) and str(item.get("qid") or "")
    ][:100]
    follow_up = bool(failure)
    diagnosis = (
        "The requested consolidation did not produce a candidate that both "
        "passed the independent semantic critic and preserved every exact "
        f"source contract. {str(failure)[:1500]}"
        if follow_up else (
            f"Aegis found {type_count} Types for {inventory_count} source "
            f"questions/tasks ({ratio:.0%}). The normal consolidation pass "
            f"merged {merged}. "
            + (
                "The concept-sufficiency audit will run once after this "
                "decision. "
                if not sufficiency_complete else (
                    "The concept-sufficiency audit added "
                    f"{additions} method concept(s). "
                )
            )
            + "This can be valid, but it is "
            "also the deterministic signature of one-Type-per-question "
            "fragmentation."
        )
    )
    return {
        "decision_id": str(identity.get("decision_id") or ""),
        "context_hash": str(identity.get("context_hash") or ""),
        "kind": "type_granularity_review",
        "phase": "type_mining",
        "conflict": (
            "The mined assessment taxonomy may be too fragmented to remain "
            "reusable across source questions."
        ),
        "diagnosis": diagnosis,
        "decision_question": (
            "Should Aegis keep these distinct Types, or spend one bounded "
            "proposal-and-critic pair to consolidate only genuinely reusable "
            "assessment patterns?"
        ),
        "checkpoint_progress": 0.76,
        "item": {
            "type_id": "TYPE-GRANULARITY-REVIEW",
            "type_title": f"{type_count} Types for {inventory_count} QIDs",
            "qids": qids,
            "questions": [],
            "topic": "Chapter-wide Type taxonomy",
        },
        "candidates": [],
        "evidence": [
            {
                "page": "",
                "label": "Type-to-QID ratio",
                "text": f"{type_count}/{inventory_count} ({ratio:.1%})",
            },
            {
                "page": "",
                "label": "Ordinary consolidation result",
                "text": f"{merged} Type(s) merged",
            },
            ({
                "page": "",
                "label": "Concept sufficiency timing",
                "text": "Runs once after this decision",
            } if not sufficiency_complete else {
                "page": "",
                "label": "Concept sufficiency result",
                "text": f"{additions} method concept(s) added",
            }),
        ],
        "deferred_assignment_unit_ids": qids,
        "options": [
            {
                "choice": "consolidate_types",
                "label": "Consolidate into fewer reusable Types",
                "recommended": not follow_up,
            },
            {
                "choice": "keep_distinct_types",
                "label": "Keep the current distinct Types",
                "recommended": follow_up,
            },
            {
                "choice": "custom_instruction",
                "label": "Specify a grouping rule or target range",
                "recommended": False,
            },
        ],
    }


def resolve_or_pause(
    *,
    review: Mapping[str, Any],
    inventory: Mapping[str, Any] | None,
    mined_types: Mapping[str, Any] | None,
    meta: Mapping[str, Any] | None,
    prior_decision_id: str = "",
    failure: str = "",
) -> dict[str, str]:
    """Return a human directive or raise a durable zero-retry pause."""

    if not failure and not is_anomalously_fragmented(review):
        return {"action": "continue"}
    identity = _identity(
        review=review,
        inventory=inventory,
        mined_types=mined_types,
        meta=meta,
        prior_decision_id=prior_decision_id,
        failure=failure,
    )
    resolution = _resolution_for(identity)
    if resolution is None:
        raise HumanDecisionRequired(_pending_decision(
            identity=identity,
            review=review,
            inventory=inventory,
            failure=failure,
        ))
    choice = str(resolution.get("choice") or "")
    if choice == "keep_distinct_types":
        action = "keep"
    else:
        action = "consolidate"
    return {
        "action": action,
        "choice": choice,
        "instruction": str(resolution.get("instruction") or ""),
        "decision_id": str(identity.get("decision_id") or ""),
        "context_hash": str(identity.get("context_hash") or ""),
    }


__all__ = [
    "applied_result_context_hash",
    "applied_result_semantic_hash",
    "build_review",
    "human_resolution_context",
    "is_anomalously_fragmented",
    "resolve_or_pause",
]

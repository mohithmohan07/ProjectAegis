"""Durable human gate for anomalously fragmented mined Type taxonomies.

Whether a taxonomy is "one Type per question" fragmentation or a faithful
set of reusable assessment methods is a judgment about what the Types mean,
so the model makes it (docs/aegis-restructure.md §3): an author verdict with
an independent advisory critic decides whether to pause — never a ratio or
count threshold. A non-decision fails closed into the pause, which keeps
all content and asks the operator. The operator can keep the taxonomy,
request one bounded consolidation proposal plus critic, or provide a custom
grouping instruction.

This decision never weakens exact-once QID coverage, Case-owned
topic/activity/scope contracts, source wording, host review, or final
validation.
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


# v6: the Architect's instruction_set_sha256 joins the decision identity
# (docs/aegis-restructure.md §8.1) — a changed instruction set changes what
# generation produces, so the operator's answer must never bind across it.
_GATE_VERSION = "type-granularity-human-gate-6"

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

            def route_value(field: str) -> Any:
                if field in case and case.get(field) is not None:
                    return case.get(field)
                return mtype.get(field)

            cases.append({
                "case_id": str(case.get("case_id") or ""),
                "case_title": str(case.get("case_title") or ""),
                "case_signature": str(case.get("case_signature") or ""),
                "concept_match_hint": str(
                    route_value("concept_match_hint") or ""
                ),
                "parent_concept_match_hint": str(
                    route_value("parent_concept_match_hint") or ""
                ),
                "topic_match_hint": str(
                    route_value("topic_match_hint") or ""
                ),
                "difficulty_hint": str(
                    route_value("difficulty_hint") or ""
                ),
                "cognitive_skill_hint": str(
                    route_value("cognitive_skill_hint") or ""
                ),
                "subject_skill_hint": str(
                    route_value("subject_skill_hint") or ""
                ),
                "is_activity": bool(route_value("is_activity")),
                "placement_scope": str(
                    route_value("placement_scope") or ""
                ),
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
    """Return the raw counts saved beside the mined taxonomy.

    Counts only — no ratios, no derived denominators. Whether the taxonomy
    is fragmented is the model's judgment (:func:`fragmentation_verdict`),
    never arithmetic on these numbers.
    """

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
    }


_FRAGMENTATION_AUTHOR_SYSTEM = (
    "You audit a mined assessment-Type taxonomy for one textbook chapter. "
    "Each Type should be a reusable assessment METHOD that recurs across "
    "source questions; its Cases are variations of that method. Judge from "
    "the Type titles, descriptions, task patterns and per-Type question "
    "counts whether these Types are genuinely reusable methods, or whether "
    "the mining pass carved one Type per source question (near-duplicate "
    "methods split by surface wording). Judge the taxonomy's meaning — do "
    "not decide from the counts alone; small chapters can legitimately have "
    "one question per Type. Return STRICT JSON only: "
    '{"verdict": "fragmented" | "healthy", "rationale": "<one or two '
    'sentences>"}'
)
_FRAGMENTATION_CRITIC_SYSTEM = (
    "You independently review another auditor's verdict on whether a mined "
    "assessment-Type taxonomy is fragmented (one Type per source question) "
    "or healthy (reusable methods). Judge from the same evidence. Return "
    'STRICT JSON only: {"verdict": "confirmed" | "not_confirmed", '
    '"rationale": "<one sentence>"}'
)


def _fragmentation_evidence(
    review: Mapping[str, Any],
    mined_types: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Bounded evidence payload shared by the author and critic calls."""

    types_summary: list[dict[str, Any]] = []
    for mtype in list((mined_types or {}).get("types") or [])[:120]:
        if not isinstance(mtype, Mapping):
            continue
        qids = [str(qid) for qid in mtype.get("source_question_ids") or []]
        types_summary.append({
            "type_title": str(mtype.get("type_title") or "")[:300],
            "type_description": str(mtype.get("type_description") or "")[:500],
            "task_pattern": str(mtype.get("task_pattern") or "")[:500],
            "qid_count": len(qids),
        })
    return {
        "types": types_summary,
        "counts": {
            key: review.get(key)
            for key in (
                "raw_type_count",
                "type_count",
                "inventory_count",
                "consolidation_merged_count",
            )
        },
    }


def fragmentation_verdict(
    review: Mapping[str, Any],
    *,
    mined_types: Mapping[str, Any] | None,
    inventory: Mapping[str, Any] | None,
    api_call: Any,
) -> dict[str, Any]:
    """Model verdict on whether the mined taxonomy is fragmented.

    The author call decides; an independent critic call is advisory only —
    dissent is recorded in ``review_flags`` and NEVER overrides the author.
    An author non-decision (unparseable/exception) fails closed: the run
    treats the taxonomy as fragmented and pauses for the human, which keeps
    all content and asks instead of guessing (CLAUDE.md Rule 1).
    """

    del inventory  # identity/coverage are sealed elsewhere; evidence is Types
    evidence = _fragmentation_evidence(review, mined_types)
    if not evidence["types"]:
        # Structural vacuity, not a content judgment: with no mined Types
        # there is no taxonomy whose granularity could be judged.
        return {
            "fragmented": False,
            "rationale": "No mined Types exist to judge.",
            "review_flags": [],
        }
    payload = json.dumps(evidence, ensure_ascii=False)
    review_flags: list[str] = []
    fragmented = True
    rationale = ""
    try:
        data = api_call(
            _FRAGMENTATION_AUTHOR_SYSTEM,
            payload,
            purpose="concept_validation",
        )
        author_verdict = _normal((data or {}).get("verdict"))
        rationale = str((data or {}).get("rationale") or "").strip()
        if author_verdict == "healthy":
            fragmented = False
        elif author_verdict != "fragmented":
            fragmented = True
            rationale = (
                "The fragmentation author returned no positive verdict; "
                "failing closed to a human review."
            )
            review_flags.append("fragmentation_author_non_decision")
    except Exception as exc:  # noqa: BLE001 - fail closed into the pause
        fragmented = True
        rationale = (
            "The fragmentation author call failed "
            f"({type(exc).__name__}); failing closed to a human review."
        )
        review_flags.append("fragmentation_author_unavailable")
    else:
        try:
            critic = api_call(
                _FRAGMENTATION_CRITIC_SYSTEM,
                json.dumps({
                    "evidence": evidence,
                    "author_verdict": (
                        "fragmented" if fragmented else "healthy"
                    ),
                    "author_rationale": rationale,
                }, ensure_ascii=False),
                purpose="advisory_critic",
            )
            critic_verdict = _normal((critic or {}).get("verdict"))
            if critic_verdict != "confirmed":
                review_flags.append(
                    "fragmentation_critic_dissent: "
                    + (str((critic or {}).get("rationale") or "").strip()
                       or "verdict not confirmed")
                )
        except Exception as exc:  # noqa: BLE001 - the critic is advisory
            review_flags.append(
                f"fragmentation_critic_unavailable: {type(exc).__name__}"
            )
    return {
        "fragmented": bool(fragmented),
        "rationale": rationale,
        "review_flags": review_flags,
    }


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
            "consumed",
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
            # A carried decision is an answer. Without it here the same
            # decision is raised again on every pass and carried again.
            "carry_forward",
        }:
            continue
        matched = {
            "decision_id": decision_id,
            "context_hash": context_hash,
            "choice": choice,
            "instruction": str(candidate.get("instruction") or "").strip(),
        }
    return copy.deepcopy(matched)


def _was_consumed(identity: Mapping[str, str]) -> bool:
    """Whether this exact-context Type authorization has already been spent."""

    decision_id = str(identity.get("decision_id") or "")
    context_hash = str(identity.get("context_hash") or "")
    for candidate in _resolution_candidates(_HUMAN_RESOLUTIONS.get()):
        if _normal(candidate.get("status")) != "consumed":
            continue
        identifiers = {
            str(candidate.get("decision_id") or ""),
            str(candidate.get("context_hash") or ""),
            str(candidate.get("_lookup_key") or ""),
        }
        if decision_id not in identifiers and context_hash not in identifiers:
            continue
        if str(candidate.get("context_hash") or context_hash) == context_hash:
            return True
    return False


_STABLE_REVIEW_KEYS = (
    "version",
    "raw_type_count",
    "type_count",
    "inventory_count",
    "consolidation_merged_count",
    "sufficiency_added_concepts",
    "sufficiency_audit_complete",
)


def _stable_review(review: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: copy.deepcopy(review.get(key)) for key in _STABLE_REVIEW_KEYS
    }


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
                # The Architect's instruction identity (§8.1): present in
                # generation metadata whenever a set was assembled; empty
                # string otherwise.
                "instruction_set_sha256",
            )
        },
        # Stable raw counts only: the model's fragmentation verdict (and its
        # prose rationale) may be recomputed on resume, and human/attempt
        # bookkeeping changes between calls — neither may shift the decision
        # identity the operator's answer is bound to.
        "review": _stable_review(review),
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

    stable_review = _stable_review(review)
    return _sha256_json({
        "version": "type-granularity-applied-result-4",
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
        "version": "type-granularity-applied-semantics-3",
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
    verdict: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    type_count = int(review.get("type_count") or 0)
    inventory_count = int(review.get("inventory_count") or 0)
    raw_type_count = int(review.get("raw_type_count") or 0)
    merged = int(review.get("consolidation_merged_count") or 0)
    additions = int(review.get("sufficiency_added_concepts") or 0)
    sufficiency_complete = bool(
        review.get("sufficiency_audit_complete", True))
    rationale = str((verdict or {}).get("rationale") or "").strip()
    verdict_flags = [
        str(flag) for flag in (verdict or {}).get("review_flags") or []
        if str(flag or "").strip()
    ]
    qids = [
        str(item.get("qid") or "")
        for item in (inventory or {}).get("items") or []
        if isinstance(item, Mapping) and str(item.get("qid") or "")
    ][:100]
    follow_up = bool(failure)
    diagnosis = (
        "The requested consolidation did not yield a final taxonomy that can "
        "proceed without another explicit choice. "
        f"{str(failure)[:1500]}"
        if follow_up else (
            f"Aegis mined {type_count} Types for {inventory_count} source "
            f"QID(s). The normal consolidation pass merged {merged}. "
            + (
                "The concept-sufficiency audit will run once after this "
                "decision. "
                if not sufficiency_complete else (
                    "The concept-sufficiency audit added "
                    f"{additions} method concept(s). "
                )
            )
            + (
                f"The model fragmentation audit says: {rationale[:1200]}"
                if rationale else
                "The model fragmentation audit judged this taxonomy "
                "possibly fragmented."
            )
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
                "label": "Taxonomy counts",
                "text": (
                    f"{type_count} Type(s) from {raw_type_count} raw for "
                    f"{inventory_count} inventory QID(s)"
                ),
            },
            {
                "page": "",
                "label": "Model fragmentation rationale",
                "text": (
                    rationale
                    or "No rationale was returned; failing closed to this "
                    "human review."
                ),
            },
            *(
                [{
                    "page": "",
                    "label": "Fragmentation review flags",
                    "text": "; ".join(verdict_flags),
                }] if verdict_flags else []
            ),
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
    verdict: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """Return a human directive or raise a durable zero-retry pause.

    ``verdict`` is the model's :func:`fragmentation_verdict` result. A
    missing verdict on a non-failure call is a non-decision and fails
    closed into the pause — the gate never guesses "healthy".
    """

    if not failure:
        if verdict is None:
            verdict = {
                "fragmented": True,
                "rationale": (
                    "No fragmentation verdict was supplied; failing closed "
                    "to a human review."
                ),
                "review_flags": ["fragmentation_verdict_missing"],
            }
        if not verdict.get("fragmented"):
            return {"action": "continue"}
    prior = str(prior_decision_id or "")
    failure_text = str(failure or "")
    # An upstream rewind can recreate the exact pre-decision taxonomy after
    # its one bounded authorization was already used. Walk the consumed chain
    # so the old answer cannot be replayed and every recurrence pauses with a
    # fresh durable identity before any provider call.
    for _index in range(100):
        identity = _identity(
            review=review,
            inventory=inventory,
            mined_types=mined_types,
            meta=meta,
            prior_decision_id=prior,
            failure=failure_text,
        )
        if not _was_consumed(identity):
            break
        prior = str(identity.get("decision_id") or "")
        failure_text = (
            "The earlier Type-taxonomy authorization was already used, but "
            "the same fragmented taxonomy has returned. Choose a fresh "
            "direction; Aegis will not replay the paid request."
        )
    else:
        raise RuntimeError(
            "Type-granularity decision history exceeded its bounded safety "
            "limit"
        )
    resolution = _resolution_for(identity)
    if resolution is None:
        raise HumanDecisionRequired(_pending_decision(
            identity=identity,
            review=review,
            inventory=inventory,
            failure=failure_text,
            verdict=verdict,
        ))
    choice = str(resolution.get("choice") or "")
    # ``carry_forward`` means Aegis settled this decision by changing nothing,
    # so it must land on "keep". Falling through to "consolidate" would merge
    # the mined Types on the strength of a decision that explicitly declined to
    # decide, and a reviewer reading the delivered workbook cannot unmerge
    # them.
    if choice in {"keep_distinct_types", "carry_forward"}:
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
    "fragmentation_verdict",
    "human_resolution_context",
    "resolve_or_pause",
]

"""Durable human gate for missing source-topic coverage.

The textbook's numbered main headings are structural evidence, but recovering
an omitted topic requires a semantic generation request.  This gate pauses
before that request so the operator can confirm the intended topology.  A
saved answer authorizes one bounded recovery call; failure produces a new
decision instead of an automatic retry loop.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator, Mapping

from .semantic_recovery import HumanDecisionRequired


# v2: the Architect's instruction_set_sha256 joins the decision identity
# (docs/aegis-restructure.md §8.1) — a changed instruction set changes what
# generation produces, so the operator's answer must never bind across it.
_GATE_VERSION = "source-topic-coverage-human-gate-2"
_PRESERVE_TARGET_ID = "preserve-all-source-topics"
_HUMAN_RESOLUTIONS: ContextVar[Any] = ContextVar(
    "aegis_source_topic_human_resolutions",
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


def _identity(
    *,
    records: list[dict],
    source_topics: list[dict],
    missing_topics: list[dict],
    meta: Mapping[str, Any] | None,
    prior_decision_id: str = "",
    failure: str = "",
) -> dict[str, str]:
    context_hash = _sha256_json({
        "version": _GATE_VERSION,
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
        "source_topics": [
            {
                "topic": str(group.get("topic") or ""),
                "excerpt_sha256": hashlib.sha256(str(
                    group.get("excerpt") or ""
                ).encode("utf-8")).hexdigest(),
            }
            for group in source_topics
            if isinstance(group, Mapping)
        ],
        "missing_topics": [
            str(group.get("topic") or "")
            for group in missing_topics
            if isinstance(group, Mapping)
        ],
        "records": [
            {
                "topic": str(record.get("topic") or ""),
                "concept_title": str(record.get("concept_title") or ""),
                "parent_concept": str(record.get("parent_concept") or ""),
                "details_sha256": hashlib.sha256(str(
                    record.get("concept_details") or ""
                ).encode("utf-8")).hexdigest(),
            }
            for record in records
            if isinstance(record, Mapping)
        ],
        "prior_decision_id": str(prior_decision_id or ""),
        "failed_direction_sha256": (
            hashlib.sha256(str(failure).encode("utf-8")).hexdigest()
            if failure else ""
        ),
    })
    return {
        "context_hash": context_hash,
        "decision_id": f"source-topology-{context_hash[:24]}",
    }


def _resolution_for(identity: Mapping[str, str]) -> dict[str, str] | None:
    decision_id = str(identity.get("decision_id") or "")
    context_hash = str(identity.get("context_hash") or "")
    matched: dict[str, str] | None = None
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
            "accept_recommended", "custom_instruction", "replace_source",
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
    """Whether this exact-context authorization has already been spent."""
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


def _pending_decision(
    *,
    identity: Mapping[str, str],
    records: list[dict],
    source_topics: list[dict],
    missing_topics: list[dict],
    failure: str = "",
) -> dict[str, Any]:
    missing_names = [
        str(group.get("topic") or "").strip()
        for group in missing_topics
        if isinstance(group, Mapping) and str(group.get("topic") or "").strip()
    ]
    generated_names = list(dict.fromkeys(
        str(record.get("topic") or "").strip()
        for record in records
        if isinstance(record, Mapping) and str(record.get("topic") or "").strip()
    ))
    source_names = [
        str(group.get("topic") or "").strip()
        for group in source_topics
        if isinstance(group, Mapping) and str(group.get("topic") or "").strip()
    ]
    follow_up = bool(failure)
    if follow_up:
        diagnosis = (
            "The one human-authorized topic recovery request did not restore "
            "every missing source topic while preserving the current concept "
            f"map. {str(failure)[:1500]}"
        )
    else:
        diagnosis = (
            f"The source contains {len(source_names)} structurally proven main "
            f"topics, but the current concept map has no normal concept under "
            f"{len(missing_names)} of them. Continuing would silently absorb or "
            "drop textbook content."
        )
    return {
        "decision_id": str(identity.get("decision_id") or ""),
        "context_hash": str(identity.get("context_hash") or ""),
        "kind": "source_topic_coverage_review",
        "phase": "concept_topology",
        "conflict": (
            "The generated concept topology does not preserve every numbered "
            "main topic in the source."
        ),
        "diagnosis": diagnosis,
        "decision_question": (
            "Should Aegis keep every source topic separate and make one bounded "
            "recovery request, or should the source/topology direction change?"
        ),
        "checkpoint_progress": 0.35,
        "item": {
            "type_id": "SOURCE-TOPIC-COVERAGE",
            "type_title": (
                f"{len(missing_names)} missing of {len(source_names)} source topics"
            ),
            "qids": [],
            "questions": missing_names[:100],
            "topic": "Chapter source topology",
        },
        "candidates": [{
            "target_id": _PRESERVE_TARGET_ID,
            "title": "Keep every source topic separate",
            "topic": "; ".join(source_names)[:8000],
            "coverage": "; ".join(missing_names)[:8000],
            "gap": "No normal concept currently represents these source topics.",
        }],
        "evidence": [
            {
                "page": "",
                "label": "Source main topics",
                "text": "; ".join(source_names)[:8000],
            },
            {
                "page": "",
                "label": "Current generated topics",
                "text": "; ".join(generated_names)[:8000],
            },
            {
                "page": "",
                "label": "Missing source topics",
                "text": "; ".join(missing_names)[:8000],
            },
        ],
        "deferred_assignment_unit_ids": missing_names[:5000],
        "options": [
            {
                "choice": "accept_recommended",
                "label": (
                    "Try one bounded recovery while keeping all source topics"
                    if follow_up
                    else "Keep all source topics separate and recover"
                ),
                "recommended": not follow_up,
                "target_id": _PRESERVE_TARGET_ID,
            },
            {
                "choice": "replace_source",
                "label": "Replace or correct the source",
                "recommended": False,
            },
            {
                "choice": "custom_instruction",
                "label": "Specify source-topic recovery guidance",
                "recommended": follow_up,
            },
        ],
    }


def resolve_or_pause(
    *,
    records: list[dict],
    source_topics: list[dict],
    missing_topics: list[dict],
    meta: Mapping[str, Any] | None,
    prior_decision_id: str = "",
    failure: str = "",
) -> dict[str, str]:
    """Return one saved recovery direction or raise a durable pause."""

    if not missing_topics:
        return {"action": "continue"}
    prior = str(prior_decision_id or "")
    failure_text = str(failure or "")
    # A later stage can reintroduce the exact same omission after an earlier
    # approved recovery. Walk the consumed chain so a ready answer can never be
    # replayed; each recurrence receives a fresh durable decision identity.
    for _index in range(100):
        identity = _identity(
            records=records,
            source_topics=source_topics,
            missing_topics=missing_topics,
            meta=meta,
            prior_decision_id=prior,
            failure=failure_text,
        )
        if not _was_consumed(identity):
            break
        prior = str(identity.get("decision_id") or "")
        failure_text = (
            "The earlier source-topic recovery authorization was already "
            "used, but this topology is incomplete again. Choose a fresh "
            "direction; Aegis will not replay the paid request."
        )
    else:
        raise RuntimeError(
            "source-topic decision history exceeded its bounded safety limit"
        )
    resolution = _resolution_for(identity)
    if resolution is None:
        raise HumanDecisionRequired(_pending_decision(
            identity=identity,
            records=records,
            source_topics=source_topics,
            missing_topics=missing_topics,
            failure=failure_text,
        ))
    choice = str(resolution.get("choice") or "")
    return {
        "action": "replace_source" if choice == "replace_source" else "recover",
        "choice": choice,
        "instruction": str(resolution.get("instruction") or ""),
        "decision_id": str(identity.get("decision_id") or ""),
        "context_hash": str(identity.get("context_hash") or ""),
    }


__all__ = [
    "human_resolution_context",
    "resolve_or_pause",
]

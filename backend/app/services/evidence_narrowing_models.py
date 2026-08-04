"""Provider, critic, and deterministic response contracts."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from . import canonical_source_phase22 as phase22
from . import semantic_confidence_policy as confidence_policy
from .evidence_narrowing_cache import persist_ledger
from .evidence_narrowing_types import (
    ConceptPacket,
    DISPOSITION_VERSION,
    EvidenceNarrowingError,
    MAX_AUDIT_CLAIM_CHARS,
    MAX_AUDIT_CLAIMS,
    MAX_DESCRIPTION_CHARS,
    MAX_REASON_CHARS,
    normal,
)

Provider = Callable[[dict[str, Any]], dict[str, Any]]
Critic = Callable[[dict[str, Any]], dict[str, Any]]


def _provider_schema() -> dict[str, Any]:
    decision = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "concept_id", "action", "rewrite_kind", "description",
            "evidence_block_ids", "kept_claims", "dropped_claims", "reason",
        ],
        "properties": {
            "concept_id": {"type": "string"},
            "action": {"type": "string", "enum": ["keep", "rewrite"]},
            "rewrite_kind": {
                "type": "string",
                "enum": ["unchanged", "narrowed", "restated"],
            },
            "description": {"type": "string"},
            "evidence_block_ids": {
                "type": "array", "items": {"type": "string"},
            },
            "kept_claims": {
                "type": "array", "items": {"type": "string"},
            },
            "dropped_claims": {
                "type": "array", "items": {"type": "string"},
            },
            "reason": {"type": "string"},
        },
    }
    return {
        "name": "aegis_terminal_evidence_narrowing_provider",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["decisions"],
            "properties": {
                "decisions": {"type": "array", "items": decision},
            },
        },
    }


def _critic_schema() -> dict[str, Any]:
    review = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "concept_id", "verdict", "evidence_block_ids",
            "unsupported_claims", "reason",
        ],
        "properties": {
            "concept_id": {"type": "string"},
            "verdict": {"type": "string", "enum": ["verified", "rejected"]},
            "evidence_block_ids": {
                "type": "array", "items": {"type": "string"},
            },
            "unsupported_claims": {
                "type": "array", "items": {"type": "string"},
            },
            "reason": {"type": "string"},
        },
    }
    return {
        "name": "aegis_terminal_evidence_narrowing_critic",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["verdict", "confidence", "issues", "decisions"],
            "properties": {
                "verdict": {"type": "string", "enum": ["verified", "rejected"]},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "issues": {"type": "array", "items": {"type": "string"}},
                "decisions": {"type": "array", "items": review},
            },
        },
    }


def _provider_system() -> str:
    return """You are the terminal source-grounding editor for Project Aegis.
Semantic entailment is your job. Never use word overlap, token containment, or
phrase similarity as a substitute for deciding what the source actually
teaches. For every concept, use only its allowed canonical evidence blocks.

Return exactly one decision for every concept_id.
- keep: the complete Description is supported. Reproduce it exactly and use
  rewrite_kind=unchanged.
- rewrite/narrowed: preserve every supported proposition and remove or repair
  only unsupported propositions. Produce a coherent standalone Description.
- rewrite/restated: none of the original claim is safely supportable. Write the
  smallest coherent concept the cited blocks genuinely teach and that remains
  relevant to the concept's title and topic. Do not paste an arbitrary source
  sentence merely because it is present.

Never change concept title, topic, parent, ordering, IDs, examples or pedagogical
sections. Cite exact block IDs. Mathematical conditions, quantifiers, causal
links, scope and exceptions must all be supported. State what was kept and
what was dropped in semantic claim-sized phrases."""


def _critic_system() -> str:
    return """You are the independent source-grounding critic for Project Aegis.
Review the proposed terminal Description for each concept against only its
allowed canonical blocks. Do not reward lexical overlap. Verify semantic
entailment, mathematical correctness, conditions, quantifiers, causal links,
scope, exceptions and relevance to the concept title/topic.

Reject a proposal if it retains any unsupported proposition, drops supported
content unnecessarily, pastes an arbitrary source sentence, cites the wrong
block, changes topology, or turns a concept into a fragment. Return exactly one
review for every concept_id. Overall verdict may be verified only when every
row is verified, every cited block ID is valid, unsupported_claims is empty,
and confidence is high enough for production acceptance."""


def _model_output_tokens() -> int:
    """Use the configured provider ceiling unless an operator lowers it."""

    try:
        ceiling = int(getattr(phase22, "_MAX_OUTPUT_TOKENS", 128_000))
    except (TypeError, ValueError):
        ceiling = 128_000
    ceiling = max(2_000, min(128_000, ceiling))
    raw = os.environ.get("AEGIS_EVIDENCE_NARROWING_MAX_OUTPUT_TOKENS")
    if raw is None or not raw.strip():
        return ceiling
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return ceiling
    return max(2_000, min(ceiling, value))


def default_provider(payload: dict[str, Any]) -> dict[str, Any]:
    return phase22._openai_multimodal_json(
        system=_provider_system(),
        prompt=(
            "Rewrite only where exact source support requires it. The JSON "
            "payload is authoritative:\n"
            + json.dumps(payload, ensure_ascii=False, sort_keys=True)
        ),
        pages=[],
        response_schema=_provider_schema(),
        purpose="semantic_resolution",
        max_tokens=_model_output_tokens(),
        single_attempt=True,
    )


def default_critic(payload: dict[str, Any]) -> dict[str, Any]:
    return phase22._openai_multimodal_json(
        system=_critic_system(),
        prompt=(
            "Independently verify every proposed decision. The JSON payload is "
            "authoritative:\n"
            + json.dumps(payload, ensure_ascii=False, sort_keys=True)
        ),
        pages=[],
        response_schema=_critic_schema(),
        purpose="concept_validation",
        max_tokens=_model_output_tokens(),
        single_attempt=True,
    )


def _string_list(value: Any, *, maximum: int = MAX_AUDIT_CLAIMS) -> list[str] | None:
    if not isinstance(value, list) or len(value) > maximum:
        return None
    out: list[str] = []
    for raw in value:
        text = normal(raw)
        if not text or len(text) > MAX_AUDIT_CLAIM_CHARS:
            return None
        out.append(text)
    return out


def validated_provider_decisions(
    response: Mapping[str, Any],
    packets: Sequence[ConceptPacket],
) -> tuple[list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    raw = response.get("decisions") if isinstance(response, Mapping) else None
    if not isinstance(raw, list):
        return [], ["provider omitted its decisions array"]
    by_id = {packet.concept_id: packet for packet in packets}
    seen: set[str] = set()
    decisions: list[dict[str, Any]] = []
    for row in raw:
        if not isinstance(row, Mapping):
            errors.append("provider returned a non-object decision")
            continue
        concept_id = str(row.get("concept_id") or "")
        packet = by_id.get(concept_id)
        if packet is None:
            errors.append(f"provider returned unknown concept_id {concept_id or '(empty)'}")
            continue
        if concept_id in seen:
            errors.append(f"provider repeated concept_id {concept_id}")
            continue
        seen.add(concept_id)
        action = str(row.get("action") or "")
        kind = str(row.get("rewrite_kind") or "")
        description = normal(row.get("description"))
        reason = normal(row.get("reason"))
        kept = _string_list(row.get("kept_claims"))
        dropped = _string_list(row.get("dropped_claims"))
        evidence_ids = _string_list(row.get("evidence_block_ids"), maximum=256)
        allowed = set(packet.evidence.block_ids)
        if action not in {"keep", "rewrite"}:
            errors.append(f"{concept_id} has invalid action {action!r}")
        if kind not in {"unchanged", "narrowed", "restated"}:
            errors.append(f"{concept_id} has invalid rewrite_kind {kind!r}")
        if not description or len(description) > MAX_DESCRIPTION_CHARS:
            errors.append(f"{concept_id} has an empty or oversized Description")
        if not reason or len(reason) > MAX_REASON_CHARS:
            errors.append(f"{concept_id} omitted a bounded reason")
        if kept is None or dropped is None or evidence_ids is None:
            errors.append(f"{concept_id} returned malformed audit lists")
            continue
        if not evidence_ids or len(set(evidence_ids)) != len(evidence_ids):
            errors.append(f"{concept_id} must cite unique exact evidence block IDs")
        unknown = sorted(set(evidence_ids) - allowed)
        if unknown:
            errors.append(f"{concept_id} cited unknown evidence blocks {unknown}")
        if action == "keep":
            if kind != "unchanged" or description != packet.description or dropped:
                errors.append(
                    f"{concept_id} keep must reproduce the original Description "
                    "exactly, use unchanged, and drop nothing"
                )
        elif action == "rewrite":
            if kind not in {"narrowed", "restated"}:
                errors.append(f"{concept_id} rewrite must be narrowed or restated")
            if description == packet.description:
                errors.append(f"{concept_id} rewrite did not change its Description")
        decisions.append({
            "concept_id": concept_id,
            "action": action,
            "rewrite_kind": kind,
            "description": description,
            "evidence_block_ids": evidence_ids,
            "kept_claims": kept,
            "dropped_claims": dropped,
            "reason": reason,
        })
    missing = sorted(set(by_id) - seen)
    if missing:
        errors.append(f"provider omitted concept IDs {missing}")
    if len(decisions) != len(packets):
        errors.append("provider did not return exact concept coverage")
    return decisions, list(dict.fromkeys(errors))


def validated_critic(
    response: Mapping[str, Any],
    packets: Sequence[ConceptPacket],
) -> tuple[dict[str, Any] | None, list[str]]:
    errors: list[str] = []
    if not isinstance(response, Mapping):
        return None, ["critic response is not an object"]
    verdict = str(response.get("verdict") or "")
    try:
        confidence = float(response.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
        errors.append("critic confidence is not numeric")
    issues = _string_list(response.get("issues"), maximum=128)
    if issues is None:
        errors.append("critic issues list is malformed")
        issues = []
    raw = response.get("decisions")
    if not isinstance(raw, list):
        return None, errors + ["critic omitted its decisions array"]
    by_id = {packet.concept_id: packet for packet in packets}
    seen: set[str] = set()
    reviews: list[dict[str, Any]] = []
    for row in raw:
        if not isinstance(row, Mapping):
            errors.append("critic returned a non-object review")
            continue
        concept_id = str(row.get("concept_id") or "")
        packet = by_id.get(concept_id)
        if packet is None:
            errors.append(f"critic returned unknown concept_id {concept_id or '(empty)'}")
            continue
        if concept_id in seen:
            errors.append(f"critic repeated concept_id {concept_id}")
            continue
        seen.add(concept_id)
        row_verdict = str(row.get("verdict") or "")
        evidence_ids = _string_list(row.get("evidence_block_ids"), maximum=256)
        unsupported = _string_list(row.get("unsupported_claims"))
        reason = normal(row.get("reason"))
        if row_verdict not in {"verified", "rejected"}:
            errors.append(f"critic gave {concept_id} an invalid verdict")
        if evidence_ids is None or unsupported is None:
            errors.append(f"critic gave {concept_id} malformed audit lists")
            continue
        unknown = sorted(set(evidence_ids) - set(packet.evidence.block_ids))
        if unknown:
            errors.append(f"critic cited unknown blocks for {concept_id}: {unknown}")
        if not reason or len(reason) > MAX_REASON_CHARS:
            errors.append(f"critic omitted a bounded reason for {concept_id}")
        reviews.append({
            "concept_id": concept_id,
            "verdict": row_verdict,
            "evidence_block_ids": evidence_ids,
            "unsupported_claims": unsupported,
            "reason": reason,
        })
    missing = sorted(set(by_id) - seen)
    if missing:
        errors.append(f"critic omitted concept IDs {missing}")
    if len(reviews) != len(packets):
        errors.append("critic did not return exact concept coverage")
    return {
        "verdict": verdict,
        "confidence": max(0.0, min(1.0, confidence)),
        "issues": issues,
        "decisions": reviews,
    }, list(dict.fromkeys(errors))


def critic_accepts(review: Mapping[str, Any]) -> bool:
    rows = review.get("decisions") or []
    return bool(
        str(review.get("verdict") or "") == "verified"
        and confidence_policy.accepts(float(review.get("confidence") or 0.0))
        and not list(review.get("issues") or [])
        and rows
        and all(
            isinstance(row, Mapping)
            and str(row.get("verdict") or "") == "verified"
            and not list(row.get("unsupported_claims") or [])
            for row in rows
        )
    )


def invoke_model_stage(
    callable_: Callable[[dict[str, Any]], dict[str, Any]],
    payload: dict[str, Any],
    *,
    ledger: dict[str, Any],
    context_hash: str,
    cache_dir: Path | str | None,
    stage: str,
    attempt: int,
) -> dict[str, Any]:
    ledger.update({"status": f"{stage}_pending", "attempt": attempt})
    persist_ledger(context_hash, ledger, cache_dir, required=True)
    claimed = False

    def claim() -> None:
        nonlocal claimed
        if claimed:
            return
        claimed = True
        ledger.update({"status": f"{stage}_started", "attempt": attempt})
        persist_ledger(context_hash, ledger, cache_dir, required=True)

    with phase22.openai_transport_claim(claim):
        response = callable_(copy.deepcopy(payload))
    if not isinstance(response, dict):
        raise EvidenceNarrowingError(f"{stage} response must be an object")
    return response

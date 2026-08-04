"""Model-certified terminal disposition for source-grounding failures.

Every earlier Phase 3 rung gets the cheaper and better opportunity to repair a
concept normally. This module is used only after that bounded ladder is spent.
Its product contract is deliberately narrow:

* the run still returns one row for every input row;
* semantic support is decided by a model, never by token containment;
* an independent critic must verify every accepted rewrite against exact
  canonical block IDs;
* the accepted decision is persisted before it is returned, so Resume replays
  the stored answer without another paid request; and
* an ambiguous or unavailable API never authorises deterministic semantic
  trimming. The concept ships unchanged and is reported as unverified.

Deterministic code owns identity, exact block addressing, schema validation,
count preservation, checkpoint transactions and Description-only projection.
The provider and critic own the semantic judgement.
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .. import config
from .evidence_narrowing_cache import (
    bind_state_mirror,
    load_ledger,
    persist_ledger,
    reset_state_mirror,
)
from .evidence_narrowing_models import (
    Critic,
    Provider,
    critic_accepts,
    default_critic,
    default_provider,
    invoke_model_stage,
    validated_critic,
    validated_provider_decisions,
)
from .evidence_narrowing_source import (
    build_evidence,
    concept_packets,
    context_material,
    project_description,
    restore_original_records,
)
from .evidence_narrowing_types import (
    CACHE_PREFIX,
    DISPOSITION_VERSION,
    NARROWED_CONTRACT,
    NARROWED_CONTRACT_FIELD,
    NARROWED_DROPPED_FIELD,
    NARROWED_EVIDENCE_FIELD,
    NARROWED_FLAG_FIELD,
    NARROWED_KIND_FIELD,
    NARROWED_NOTE_FIELD,
    NARROWED_ORIGINAL_FIELD,
    UNVERIFIED_CONTRACT,
    ConceptPacket,
    EvidenceNarrowingError,
    NarrowedRow,
    NarrowingAudit,
    TopicEvidence,
    normal,
    sha256_json,
)

# Compatibility for audit code and tests that inspect the reserved namespace.
_CACHE_PREFIX = CACHE_PREFIX


def _copy_records(records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        copy.deepcopy(dict(row)) if isinstance(row, Mapping) else copy.deepcopy(row)
        for row in records
    ]


def _apply_accepted(
    records: Sequence[Mapping[str, Any]],
    packets: Sequence[ConceptPacket],
    decisions: Sequence[Mapping[str, Any]],
    review: Mapping[str, Any],
    *,
    context_hash: str,
    cache_status: str,
    unavailable: Sequence[NarrowedRow],
) -> tuple[list[dict[str, Any]], NarrowingAudit]:
    out = _copy_records(records)
    decision_by_id = {str(row.get("concept_id") or ""): row for row in decisions}
    review_by_id = {
        str(row.get("concept_id") or ""): row
        for row in review.get("decisions") or []
        if isinstance(row, Mapping)
    }
    audit = NarrowingAudit(
        context_hash=context_hash,
        cache_status=cache_status,
        rows=list(unavailable),
    )
    for packet in packets:
        decision = decision_by_id[packet.concept_id]
        critic_row = review_by_id[packet.concept_id]
        action = str(decision.get("action") or "")
        kind = str(decision.get("rewrite_kind") or "")
        evidence_ids = tuple(dict.fromkeys(
            str(value)
            for value in critic_row.get("evidence_block_ids") or []
            if str(value)
        )) or tuple(
            str(value) for value in decision.get("evidence_block_ids") or []
        )
        if action == "keep":
            audit.rows.append(NarrowedRow(
                concept_title=packet.concept_title,
                topic=packet.topic,
                kind="unchanged",
                kept_clauses=tuple(decision.get("kept_claims") or []),
                evidence_block_ids=evidence_ids,
                note="the original Description was independently verified",
                verified=True,
            ))
            continue

        row = out[packet.row_index]
        description = str(decision.get("description") or "")
        project_description(row, description)
        row[NARROWED_FLAG_FIELD] = True
        row[NARROWED_KIND_FIELD] = kind
        row[NARROWED_DROPPED_FIELD] = list(decision.get("dropped_claims") or [])
        row[NARROWED_ORIGINAL_FIELD] = packet.description
        row[NARROWED_EVIDENCE_FIELD] = list(evidence_ids)
        row[NARROWED_NOTE_FIELD] = str(decision.get("reason") or "")
        row[NARROWED_CONTRACT_FIELD] = {
            "version": DISPOSITION_VERSION,
            "basis": NARROWED_CONTRACT,
            "context_hash": context_hash,
            "concept_id": packet.concept_id,
            "provider_action": action,
            "rewrite_kind": kind,
            "critic_verdict": "verified",
            "critic_confidence": float(review.get("confidence") or 0.0),
            "evidence_block_ids": list(evidence_ids),
        }
        audit.rows.append(NarrowedRow(
            concept_title=packet.concept_title,
            topic=packet.topic,
            kind=kind,
            kept_clauses=tuple(decision.get("kept_claims") or []),
            dropped_clauses=tuple(decision.get("dropped_claims") or []),
            evidence_block_ids=evidence_ids,
            note=str(decision.get("reason") or ""),
            verified=True,
        ))
    if len(out) != len(records):  # pragma: no cover - defensive invariant
        raise EvidenceNarrowingError(
            "terminal disposition must return exactly one row per input row"
        )
    return out, audit


def _unmodified_result(
    records: Sequence[Mapping[str, Any]],
    packets: Sequence[ConceptPacket],
    *,
    context_hash: str,
    reason: str,
    cache_status: str,
    unavailable: Sequence[NarrowedRow],
) -> tuple[list[dict[str, Any]], NarrowingAudit]:
    audit = NarrowingAudit(
        context_hash=context_hash,
        cache_status=cache_status,
        rows=list(unavailable),
    )
    for packet in packets:
        audit.rows.append(NarrowedRow(
            concept_title=packet.concept_title,
            topic=packet.topic,
            kind="unmodified_unverified",
            note=reason,
            verified=False,
        ))
    return _copy_records(records), audit


def _fallback_and_persist(
    records: Sequence[Mapping[str, Any]],
    packets: Sequence[ConceptPacket],
    *,
    context_hash: str,
    cache_dir: Path | str | None,
    ledger: dict[str, Any],
    reason: str,
    unavailable: Sequence[NarrowedRow],
) -> tuple[list[dict[str, Any]], NarrowingAudit]:
    ledger.update({
        "status": "unmodified",
        "basis": UNVERIFIED_CONTRACT,
        "failure": normal(reason)[:4_000],
    })
    try:
        persist_ledger(context_hash, ledger, cache_dir, required=False)
    except Exception:
        pass
    return _unmodified_result(
        records,
        packets,
        context_hash=context_hash,
        reason=(
            normal(reason)
            or "no critic-verified semantic narrowing decision was available"
        ),
        cache_status="unmodified",
        unavailable=unavailable,
    )


def _accept_and_persist(
    records: Sequence[Mapping[str, Any]],
    packets: Sequence[ConceptPacket],
    decisions: Sequence[Mapping[str, Any]],
    review: Mapping[str, Any],
    *,
    context_hash: str,
    cache_dir: Path | str | None,
    ledger: dict[str, Any],
    cache_status: str,
    unavailable: Sequence[NarrowedRow],
) -> tuple[list[dict[str, Any]], NarrowingAudit]:
    accepted = {
        **ledger,
        "status": "accepted",
        "basis": NARROWED_CONTRACT,
        "decisions": {"decisions": list(decisions)},
        "review": dict(review),
    }
    try:
        persist_ledger(context_hash, accepted, cache_dir, required=True)
    except Exception as exc:
        return _fallback_and_persist(
            records,
            packets,
            context_hash=context_hash,
            cache_dir=cache_dir,
            ledger=ledger,
            reason=f"accepted narrowing could not be durably committed: {exc}",
            unavailable=unavailable,
        )
    return _apply_accepted(
        records,
        packets,
        decisions,
        review,
        context_hash=context_hash,
        cache_status=cache_status,
        unavailable=unavailable,
    )


def _narrow_records_impl(
    records: Sequence[Mapping[str, Any]],
    *,
    graph: Mapping[str, Any] | None = None,
    canonical: Mapping[str, Any] | None = None,
    evidence: Mapping[str, TopicEvidence] | None = None,
    concept_titles: Iterable[str] | None = None,
    provider: Provider | None = None,
    critic: Critic | None = None,
    cache_dir: Path | str | None = None,
    max_attempts: int = 2,
) -> tuple[list[dict[str, Any]], NarrowingAudit]:
    selected_titles = (
        tuple(concept_titles) if concept_titles is not None else None
    )
    records = restore_original_records(
        records,
        concept_titles=selected_titles,
    )
    if evidence is None:
        evidence = build_evidence(graph, canonical)
    packets, unavailable = concept_packets(
        records,
        evidence=evidence,
        concept_titles=selected_titles,
    )
    if not packets:
        return _copy_records(records), NarrowingAudit(
            rows=list(unavailable), cache_status="no_evidence"
        )

    material = context_material(
        packets,
        graph=graph,
        version=DISPOSITION_VERSION,
        model=str(getattr(config, "OPENAI_MODEL", "")),
    )
    context_hash = sha256_json(material)
    ledger = load_ledger(context_hash, cache_dir) or {
        "version": DISPOSITION_VERSION,
        "context_hash": context_hash,
        "status": "new",
        "attempt": 0,
        "model": str(getattr(config, "OPENAI_MODEL", "")),
    }
    status = str(ledger.get("status") or "")

    if status == "accepted":
        decisions, provider_errors = validated_provider_decisions(
            ledger.get("decisions") or {}, packets
        )
        review, critic_errors = validated_critic(
            ledger.get("review") or {}, packets
        )
        if not provider_errors and not critic_errors and review and critic_accepts(review):
            return _apply_accepted(
                records,
                packets,
                decisions,
                review,
                context_hash=context_hash,
                cache_status="accepted_replay",
                unavailable=unavailable,
            )
        return _fallback_and_persist(
            records,
            packets,
            context_hash=context_hash,
            cache_dir=cache_dir,
            ledger=ledger,
            reason="stored terminal disposition failed deterministic contract validation",
            unavailable=unavailable,
        )
    if status == "unmodified":
        return _unmodified_result(
            records,
            packets,
            context_hash=context_hash,
            reason=str(ledger.get("failure") or "stored unverified terminal fallback"),
            cache_status="unmodified_replay",
            unavailable=unavailable,
        )
    if status in {"provider_started", "critic_started"}:
        return _fallback_and_persist(
            records,
            packets,
            context_hash=context_hash,
            cache_dir=cache_dir,
            ledger=ledger,
            reason=(
                f"Resume found a durable {status} claim. The request may have "
                "been paid, so it was not replayed; the concept was released "
                "unchanged and flagged unverified"
            ),
            unavailable=unavailable,
        )

    provider_call = provider or default_provider
    critic_call = critic or default_critic
    attempts = max(1, min(2, int(max_attempts or 1)))
    prior_feedback: dict[str, Any] = {}
    attempt = max(1, int(ledger.get("attempt") or 1))
    resumed_decisions: list[dict[str, Any]] | None = None

    if status in {"provider_returned", "critic_pending", "critic_returned"}:
        resumed_decisions, provider_errors = validated_provider_decisions(
            ledger.get("provider_response") or {}, packets
        )
        if provider_errors:
            return _fallback_and_persist(
                records,
                packets,
                context_hash=context_hash,
                cache_dir=cache_dir,
                ledger=ledger,
                reason=(
                    "stored provider decision failed deterministic contract "
                    "validation: " + "; ".join(provider_errors[:8])
                ),
                unavailable=unavailable,
            )

    if status == "critic_returned":
        resumed_review, critic_errors = validated_critic(
            ledger.get("critic_response") or {}, packets
        )
        if critic_errors or resumed_review is None:
            return _fallback_and_persist(
                records,
                packets,
                context_hash=context_hash,
                cache_dir=cache_dir,
                ledger=ledger,
                reason=(
                    "stored critic decision failed deterministic contract "
                    "validation: " + "; ".join(critic_errors[:8])
                ),
                unavailable=unavailable,
            )
        if critic_accepts(resumed_review):
            return _accept_and_persist(
                records,
                packets,
                resumed_decisions or [],
                resumed_review,
                context_hash=context_hash,
                cache_dir=cache_dir,
                ledger=ledger,
                cache_status="accepted_replay",
                unavailable=unavailable,
            )
        prior_feedback = {
            "overall_verdict": resumed_review.get("verdict"),
            "confidence": resumed_review.get("confidence"),
            "issues": resumed_review.get("issues") or [],
            "concept_reviews": resumed_review.get("decisions") or [],
            "instruction": (
                "Correct only the critic-identified semantic defects. Preserve "
                "already-supported propositions and exact IDs."
            ),
        }
        attempt += 1
        resumed_decisions = None
        if attempt > attempts:
            return _fallback_and_persist(
                records,
                packets,
                context_hash=context_hash,
                cache_dir=cache_dir,
                ledger=ledger,
                reason=(
                    "independent critic rejected every bounded terminal "
                    "narrowing candidate; no lexical or deterministic semantic "
                    "substitute was used"
                ),
                unavailable=unavailable,
            )

    while attempt <= attempts:
        if resumed_decisions is not None:
            decisions = resumed_decisions
            resumed_decisions = None
        else:
            provider_payload = {
                "contract_version": DISPOSITION_VERSION,
                "context_hash": context_hash,
                "attempt": attempt,
                "concepts": [packet.payload() for packet in packets],
                "previous_critic_feedback": copy.deepcopy(prior_feedback),
            }
            try:
                provider_response = invoke_model_stage(
                    provider_call,
                    provider_payload,
                    ledger=ledger,
                    context_hash=context_hash,
                    cache_dir=cache_dir,
                    stage="provider",
                    attempt=attempt,
                )
            except Exception as exc:
                return _fallback_and_persist(
                    records,
                    packets,
                    context_hash=context_hash,
                    cache_dir=cache_dir,
                    ledger=ledger,
                    reason=f"terminal narrowing provider was unavailable or unsafe: {exc}",
                    unavailable=unavailable,
                )
            decisions, provider_errors = validated_provider_decisions(
                provider_response, packets
            )
            if provider_errors:
                return _fallback_and_persist(
                    records,
                    packets,
                    context_hash=context_hash,
                    cache_dir=cache_dir,
                    ledger=ledger,
                    reason=(
                        "terminal narrowing provider violated its exact contract: "
                        + "; ".join(provider_errors[:8])
                    ),
                    unavailable=unavailable,
                )
            ledger.update({
                "status": "provider_returned",
                "attempt": attempt,
                "provider_response": {"decisions": decisions},
            })
            try:
                persist_ledger(context_hash, ledger, cache_dir, required=True)
            except Exception as exc:
                return _fallback_and_persist(
                    records,
                    packets,
                    context_hash=context_hash,
                    cache_dir=cache_dir,
                    ledger=ledger,
                    reason=f"provider decision could not be persisted before criticism: {exc}",
                    unavailable=unavailable,
                )

        critic_payload = {
            "contract_version": DISPOSITION_VERSION,
            "context_hash": context_hash,
            "attempt": attempt,
            "concepts": [packet.payload() for packet in packets],
            "proposed_decisions": copy.deepcopy(decisions),
        }
        try:
            critic_response = invoke_model_stage(
                critic_call,
                critic_payload,
                ledger=ledger,
                context_hash=context_hash,
                cache_dir=cache_dir,
                stage="critic",
                attempt=attempt,
            )
        except Exception as exc:
            return _fallback_and_persist(
                records,
                packets,
                context_hash=context_hash,
                cache_dir=cache_dir,
                ledger=ledger,
                reason=f"independent narrowing critic was unavailable or unsafe: {exc}",
                unavailable=unavailable,
            )
        review, critic_errors = validated_critic(critic_response, packets)
        if critic_errors or review is None:
            return _fallback_and_persist(
                records,
                packets,
                context_hash=context_hash,
                cache_dir=cache_dir,
                ledger=ledger,
                reason=(
                    "independent narrowing critic violated its exact contract: "
                    + "; ".join(critic_errors[:8])
                ),
                unavailable=unavailable,
            )
        ledger.update({
            "status": "critic_returned",
            "attempt": attempt,
            "provider_response": {"decisions": decisions},
            "critic_response": review,
        })
        try:
            persist_ledger(context_hash, ledger, cache_dir, required=True)
        except Exception as exc:
            return _fallback_and_persist(
                records,
                packets,
                context_hash=context_hash,
                cache_dir=cache_dir,
                ledger=ledger,
                reason=f"critic decision could not be persisted before release: {exc}",
                unavailable=unavailable,
            )

        if critic_accepts(review):
            return _accept_and_persist(
                records,
                packets,
                decisions,
                review,
                context_hash=context_hash,
                cache_dir=cache_dir,
                ledger=ledger,
                cache_status="accepted_fresh",
                unavailable=unavailable,
            )

        prior_feedback = {
            "overall_verdict": review.get("verdict"),
            "confidence": review.get("confidence"),
            "issues": review.get("issues") or [],
            "concept_reviews": review.get("decisions") or [],
            "instruction": (
                "Correct only the critic-identified semantic defects. Preserve "
                "already-supported propositions and exact IDs."
            ),
        }
        ledger.pop("provider_response", None)
        ledger.pop("critic_response", None)
        attempt += 1

    return _fallback_and_persist(
        records,
        packets,
        context_hash=context_hash,
        cache_dir=cache_dir,
        ledger=ledger,
        reason=(
            "independent critic rejected every bounded terminal narrowing "
            "candidate; no lexical or deterministic semantic substitute was used"
        ),
        unavailable=unavailable,
    )


def narrow_records(
    records: Sequence[Mapping[str, Any]],
    *,
    graph: Mapping[str, Any] | None = None,
    canonical: Mapping[str, Any] | None = None,
    evidence: Mapping[str, TopicEvidence] | None = None,
    concept_titles: Iterable[str] | None = None,
    provider: Provider | None = None,
    critic: Critic | None = None,
    cache_dir: Path | str | None = None,
    max_attempts: int = 2,
) -> tuple[list[dict[str, Any]], NarrowingAudit]:
    """Return every row, accepting only a critic-verified semantic rewrite.

    The accepted result is keyed by exact candidate and source evidence,
    persisted into the active Phase 3.8 checkpoint and a local cache, and
    replayed on Resume. A durable ``*_started`` marker is never replayed because
    a paid request may already have crossed the transport boundary.
    """

    token = bind_state_mirror()
    try:
        return _narrow_records_impl(
            records,
            graph=graph,
            canonical=canonical,
            evidence=evidence,
            concept_titles=concept_titles,
            provider=provider,
            critic=critic,
            cache_dir=cache_dir,
            max_attempts=max_attempts,
        )
    finally:
        reset_state_mirror(token)

"""Row-local re-grounding for post-assembly source-claim drift.

Late model repairs (the final rich-text repair) may legitimately rewrite
a row's Description. A changed Description is a new source claim and
must not inherit the old evidence verdict, so the drifted rows — and
only those — are re-grounded through the same kernel/decision-store
machinery Settle uses, then the complete ordered payload is re-sealed.

This module replaces the retired legacy Phase 3.1/3.8 grounding engine
at this one seam (docs/phase3-rewrite-spec.md §9, PR 4). Decide-once
holds: an unchanged claim replays free from the decision store, and a
re-ground of the same changed claim is cached content-addressed.
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Mapping

from .. import grounding_certificate
from . import envelope as envelope_mod
from . import kernel
from .assemble import GROUNDING_VERSION
from .settle import (
    _batched,
    _blocks_by_topic,
    _description_of,
    _grounding_checker,
    _known_block_ids,
    _live_critic,
    _live_grounding,
    _pin_flags,
    _topic_rows,
    resolve_topic_id,
)

from .. import semantic_confidence_policy as confidence_policy


def _normal(value: object) -> str:
    return " ".join(str(value or "").split())


def reground_rows(
    records: list[dict[str, Any]],
    drifted_indexes: list[int],
    *,
    graph: Mapping[str, Any],
    canonical: Mapping[str, Any],
    store_dir: str | Path | None = None,
    provider: kernel.Provider | None = None,
    critic: kernel.Critic | None = None,
) -> list[dict[str, Any]]:
    """Re-ground exactly the drifted rows and re-seal the full payload.

    ``records`` is the complete ordered released row set; ``drifted_indexes``
    names the rows whose source-claim attestation no longer verifies (their
    derived evidence fields already cleared by the caller). Normal rows get
    one grounding decision each through the kernel; a drifted culmination
    re-derives its evidence from its topic's rows without a model call.
    """

    if provider is None:
        envelope_mod.require_live_api()
        provider = _live_grounding
        critic = critic or _live_critic
    store = kernel.DecisionStore(store_dir)
    policy = confidence_policy.POLICY_VERSION

    env_view = {"graph": graph, "canonical": canonical}
    topics = _topic_rows(env_view)
    blocks_by_topic = _blocks_by_topic(env_view)
    known_blocks = _known_block_ids(env_view)
    source_contract_hash = str(graph.get("source_contract_hash") or "")
    topology_sha = grounding_certificate.semantic_topology_sha256(graph)

    out = [copy.deepcopy(dict(row)) for row in records]
    drifted = sorted(set(int(index) for index in drifted_indexes))

    def _topic_id_of(row: Mapping[str, Any]) -> str:
        return resolve_topic_id(row, topics)

    def _is_culmination(row: Mapping[str, Any]) -> bool:
        from .. import concept_refiner as cr

        return bool(
            cr.is_culmination(
                str(row.get("concept_title") or row.get("concept") or "")
            )
        )

    # Culminations: evidence derives from the topic's other rows, exactly
    # as Settle derives it — no model call, contract preserved.
    normal_drifted: list[int] = []
    for index in drifted:
        row = out[index]
        if not _is_culmination(row):
            normal_drifted.append(index)
            continue
        topic_id = _topic_id_of(row)
        derived_blocks: list[str] = []
        for other_index, other in enumerate(out):
            if other_index == index or _is_culmination(other):
                continue
            if _topic_id_of(other) != topic_id:
                continue
            for block_id in other.get("_source_block_ids") or []:
                if block_id in known_blocks and block_id not in derived_blocks:
                    derived_blocks.append(block_id)
        row["_source_block_ids"] = derived_blocks
        row["_semantic_topic_id"] = topic_id
        row["_source_grounding_contract"] = (
            "derived-from-verified-topic-concepts"
        )

    # Normal rows: one grounding decision per drifted row, batched per
    # topic through the kernel with Settle's checker and prompts.
    by_topic: dict[str, list[int]] = {}
    for index in normal_drifted:
        by_topic.setdefault(_topic_id_of(out[index]), []).append(index)

    for topic_id, indexes in sorted(by_topic.items()):
        topic = next(
            (
                row
                for row in topics
                if str(row.get("topic_id") or "") == topic_id
            ),
            None,
        )
        if topic is None:
            raise grounding_certificate.GroundingCertificateError(
                "final source-claim re-grounding cannot resolve the graph "
                f"topic of row(s) {indexes}: {topic_id or '<missing>'}"
            )
        topic_title = str(topic.get("title") or topic_id)
        topic_blocks = blocks_by_topic.get(topic_id, [])
        topic_block_ids = {row["block_id"] for row in topic_blocks}
        subtopic_by_block = {
            row["block_id"]: row["subtopic_id"] for row in topic_blocks
        }
        for batch in _batched(indexes):
            concept_ids = [
                str(
                    out[index].get("_source_grounding_concept_id")
                    or f"CONCEPT-GROUND-{index + 1:04d}"
                )
                for index in batch
            ]
            payload = {
                "stage": "grounding",
                "rules": (
                    "A late formatting repair changed these Descriptions; "
                    "re-ground every claim on the minimal exact source "
                    "blocks from the concept's own topic. Print position "
                    "is provenance, never grounding: a block from another "
                    "topic may only appear in reference_block_ids. ONE "
                    "exception: when the concept's own topic does not "
                    "teach the claim at all, ground on the chapter blocks "
                    "that DO teach it — from other_topic_blocks — and "
                    "explain that in reason; such a decision ships flagged "
                    "for review. Never return an empty source_block_ids. "
                    "State your honest confidence; a low-confidence "
                    "decision ships flagged for review."
                ),
                "topic": {"topic_id": topic_id, "title": topic_title},
                "concepts": [
                    {
                        "concept_id": concept_id,
                        "source_claim": (
                            _description_of(out[index].get("concept_details"))
                            or _normal(out[index].get("concept_title"))
                        ),
                    }
                    for concept_id, index in zip(concept_ids, batch)
                ],
                "source_blocks": topic_blocks,
                "other_topic_blocks": [
                    {
                        "block_id": row["block_id"],
                        "topic_id": other_topic_id,
                        "kind": row["kind"],
                        "text": row["text"][:400],
                    }
                    for other_topic_id, rows in blocks_by_topic.items()
                    if other_topic_id != topic_id
                    for row in rows
                ],
            }
            decision = kernel.decide(
                kind="reground.grounding",
                unit_id=f"{topic_id}#reground{batch[0]}",
                envelope_sha256=topology_sha,
                payload=payload,
                provider=provider,
                checker=_grounding_checker(
                    concept_ids,
                    topic_block_ids=topic_block_ids,
                    known_block_ids=known_blocks,
                ),
                critic=critic,
                store=store,
                policy_version=policy,
            )
            grounded_by_id = {
                str(row.get("concept_id") or ""): row
                for row in decision["response"].get("concepts") or []
                if isinstance(row, Mapping)
            }
            for concept_id, index in zip(concept_ids, batch):
                grounded = grounded_by_id[concept_id]
                row = out[index]
                block_ids = [
                    str(v) for v in grounded.get("source_block_ids") or []
                ]
                row["_source_block_ids"] = block_ids
                references = [
                    str(v)
                    for v in grounded.get("reference_block_ids") or []
                    if str(v)
                ]
                if references:
                    row["_reference_block_ids"] = references
                row["_semantic_subtopic_ids"] = sorted({
                    subtopic_by_block.get(block_id, "")
                    for block_id in block_ids
                } - {""})
                row["_source_grounding_contract"] = (
                    "api-verified-source-block-ids"
                )
                try:
                    row["_source_grounding_confidence"] = float(
                        grounded.get("confidence") or 0.0
                    )
                except (TypeError, ValueError):
                    row["_source_grounding_confidence"] = 0.0
                flags = _pin_flags(
                    list(decision.get("review_flags") or []),
                    concept_ids,
                    concept_id,
                )
                if flags:
                    row["review_flags"] = [
                        *(row.get("review_flags") or []),
                        *flags,
                    ]

    # Re-seal the complete ordered payload so lineage, claims, and row
    # certificates attest the exact rows the deposit chain receives.
    for row in out:
        row["_source_grounding_version"] = GROUNDING_VERSION
    grounding_certificate.seal_records(
        out,
        source_contract_hash=source_contract_hash,
        semantic_topology_sha256=topology_sha,
        allowed_block_ids=known_blocks,
    )
    return out

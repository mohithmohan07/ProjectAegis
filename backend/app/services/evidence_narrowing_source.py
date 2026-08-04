"""Deterministic source indexing and Description-only projection."""
from __future__ import annotations

import hashlib
from typing import Any, Iterable, Mapping, Sequence

from . import concept_refiner as cr
from . import grounding_certificate
from .evidence_narrowing_types import (
    ConceptPacket,
    DESCRIPTION_LABEL,
    NARROWED_ORIGINAL_FIELD,
    NarrowedRow,
    TopicEvidence,
    normal,
    sha256_json,
)


_EXCLUDED_BLOCK_KINDS = frozenset({"layout", "heading", "navigation"})


def _block_text(
    graph_block: Mapping[str, Any],
    canonical_block: Mapping[str, Any] | None,
) -> str:
    override = graph_block.get("source_override")
    if isinstance(override, Mapping):
        resolved = str(override.get("resolved_text") or "")
        if resolved:
            return normal(resolved)
    source = canonical_block or {}
    return normal(
        source.get("display_text")
        or source.get("raw_text")
        or graph_block.get("text")
        or ""
    )


def build_evidence(
    graph: Mapping[str, Any] | None,
    canonical: Mapping[str, Any] | None,
) -> dict[str, TopicEvidence]:
    """Index usable canonical text by semantic topic without judging support."""

    if not isinstance(graph, Mapping):
        return {}
    canonical_blocks = {
        str(row.get("block_id") or ""): row
        for row in (canonical or {}).get("blocks") or []
        if isinstance(row, Mapping)
    }
    titles = {
        str(row.get("topic_id") or ""): str(row.get("title") or "")
        for row in graph.get("topics") or []
        if isinstance(row, Mapping)
    }
    ordered = sorted(
        [row for row in graph.get("blocks") or [] if isinstance(row, Mapping)],
        key=lambda row: (
            int(row.get("order") or 0),
            int(row.get("source_start") or 0),
            str(row.get("block_id") or ""),
        ),
    )
    collected: dict[str, list[tuple[str, str]]] = {}
    for block in ordered:
        if str(block.get("kind") or "") in _EXCLUDED_BLOCK_KINDS:
            continue
        topic_id = str(block.get("topic_id") or "")
        block_id = str(block.get("block_id") or "")
        if not topic_id or not block_id:
            continue
        text = _block_text(block, canonical_blocks.get(block_id))
        if text:
            collected.setdefault(topic_id, []).append((block_id, text))
    return {
        topic_id: TopicEvidence(
            topic_id=topic_id,
            topic_title=titles.get(topic_id, topic_id),
            blocks=tuple(blocks),
        )
        for topic_id, blocks in collected.items()
    }


def _evidence_for_row(
    row: Mapping[str, Any],
    evidence: Mapping[str, TopicEvidence],
) -> TopicEvidence | None:
    topic_id = str(row.get("_semantic_topic_id") or "")
    if topic_id and topic_id in evidence:
        return evidence[topic_id]
    wanted = normal(row.get("topic")).casefold()
    if not wanted:
        return None
    for candidate in evidence.values():
        if normal(candidate.topic_title).casefold() == wanted:
            return candidate
    return None


def replace_description(details: str, description: str) -> str:
    sections = cr.split_sections(details)
    replaced = False
    out: list[tuple[str, str]] = []
    for label, content in sections:
        if (
            not replaced
            and str(label or "").strip().casefold().startswith("description")
        ):
            out.append((label, description))
            replaced = True
        else:
            out.append((label, content))
    if not replaced:
        out.insert(0, (DESCRIPTION_LABEL, description))
    return cr.join_sections(out)


def _original_description(row: Mapping[str, Any]) -> str:
    original = normal(row.get(NARROWED_ORIGINAL_FIELD))
    if original:
        return original
    return normal(grounding_certificate.source_claim(row))


def concept_packets(
    records: Sequence[Mapping[str, Any]],
    *,
    evidence: Mapping[str, TopicEvidence],
    concept_titles: Iterable[str] | None,
) -> tuple[list[ConceptPacket], list[NarrowedRow]]:
    wanted = (
        {normal(title).casefold() for title in concept_titles}
        if concept_titles is not None
        else None
    )
    packets: list[ConceptPacket] = []
    unavailable: list[NarrowedRow] = []
    seen_ids: set[str] = set()
    for index, row in enumerate(records):
        if not isinstance(row, Mapping):
            continue
        title = normal(row.get("concept_title") or row.get("concept"))
        if wanted is not None and title.casefold() not in wanted:
            continue
        topic = normal(row.get("topic"))
        description = _original_description(row)
        topic_evidence = _evidence_for_row(row, evidence)
        if topic_evidence is None or topic_evidence.empty:
            unavailable.append(NarrowedRow(
                concept_title=title,
                topic=topic,
                kind="unchanged",
                note=(
                    "no usable canonical source blocks are indexed for this "
                    "topic; no semantic rewrite was attempted"
                ),
            ))
            continue
        seed = str(
            row.get("_phase32_origin_concept_id")
            or row.get("_semantic_concept_id")
            or ""
        ).strip()
        if not seed:
            seed = "terminal-concept-" + sha256_json({
                "row_index": index,
                "title": title,
                "topic": topic,
                "description": description,
            })[:24]
        concept_id = seed
        suffix = 2
        while concept_id in seen_ids:
            concept_id = f"{seed}:{suffix}"
            suffix += 1
        seen_ids.add(concept_id)
        packets.append(ConceptPacket(
            concept_id=concept_id,
            row_index=index,
            concept_title=title,
            topic=topic,
            topic_id=topic_evidence.topic_id,
            description=description,
            evidence=topic_evidence,
        ))
    return packets, unavailable


def context_material(
    packets: Sequence[ConceptPacket],
    *,
    graph: Mapping[str, Any] | None,
    version: str,
    model: str,
) -> dict[str, Any]:
    return {
        "version": version,
        "model": model,
        "source_contract_hash": str((graph or {}).get("source_contract_hash") or ""),
        "concepts": [
            {
                "concept_id": packet.concept_id,
                "concept_title": packet.concept_title,
                "topic": packet.topic,
                "topic_id": packet.topic_id,
                "description": packet.description,
                "evidence": [
                    {
                        "block_id": block_id,
                        "text_sha256": hashlib.sha256(
                            text.encode("utf-8")
                        ).hexdigest(),
                    }
                    for block_id, text in packet.evidence.blocks
                ],
            }
            for packet in packets
        ],
    }

"""Phase 3.2 source-verified concept topology adjudication.

Phase 3.1 made concept-to-source grounding strict, which exposed a deeper ordering
problem during the live RNE acceptance run: a concept could already be frozen
under one topic while its Description combined source claims from another topic.
A topic-bounded grounding critic then had only two bad choices: approve
cross-topic evidence or stop the pipeline.

This contract moves source verification before learner analysis and topology
freeze. Every normal concept is adjudicated against the complete canonical
chapter graph. The API may keep, move, refine, or split a concept, but only when
an independent critic verifies the decision against source evidence. Low
confidence alone is never permission to create a row. The resulting topology is
then grounded through the Phase 3.1 exact block contract, cached, and only then
passed to learner-analysis generation and final freeze.
"""
from __future__ import annotations

import copy
import json
import os
import re
import time
from functools import wraps
from pathlib import Path
from typing import Any, Callable

from .. import config
from . import canonical_source_phase3 as phase3
from . import canonical_source_phase31_grounding_contract as phase31
from . import concept_refiner as cr
from . import progress

_CONTRACT_VERSION = 1
_TOPOLOGY_VERSION = "phase3.2-source-verified-topology-1"
_CACHE_FILENAME = "source.phase32-topology-adjudication-cache.json"
_ALLOWED_DECISIONS = frozenset({"keep", "move", "split", "refine", "review_required"})
_TRANSIENT_FIELDS = frozenset({
    "_source_block_ids",
    "_semantic_subtopic_ids",
    "_source_grounding_contract",
    "_source_grounding_version",
    "_source_grounding_confidence",
    "_source_grounding_source_contract_hash",
    "_activity_hub_qids",
    "_phase32_topology_decision",
    "_phase32_origin_concept_id",
    "_phase32_segment_order",
    "_phase32_source_order",
})

TopologyProvider = Callable[[dict[str, Any]], dict[str, Any]]
TopologyCritic = Callable[[dict[str, Any]], dict[str, Any]]
GroundingProvider = Callable[[dict[str, Any]], dict[str, Any]]
GroundingCritic = Callable[[dict[str, Any]], dict[str, Any]]


def _max_attempts() -> int:
    return max(
        1,
        min(
            5,
            int(os.environ.get("AEGIS_PHASE32_TOPOLOGY_MAX_ATTEMPTS", "3")),
        ),
    )


def _batch_size() -> int:
    return max(
        1,
        min(
            24,
            int(os.environ.get("AEGIS_PHASE32_TOPOLOGY_BATCH_CONCEPTS", "12")),
        ),
    )


def _topic_evidence_limit() -> int:
    return max(
        12000,
        min(
            120000,
            int(os.environ.get("AEGIS_PHASE32_TOPIC_EVIDENCE_CHARS", "60000")),
        ),
    )


def _normal(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def _artifact_dir() -> Path | None:
    session = phase3.active_session()
    if not isinstance(session, dict) or not session.get("artifact_dir"):
        return None
    return Path(session["artifact_dir"])


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_json(path: Path, value: dict[str, Any]) -> None:
    phase3._atomic_write(
        path,
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def _existing_mastery(record: dict[str, Any]) -> str:
    details = str(record.get("concept_details") or record.get("concept_description") or "")
    for label, content in cr.split_sections(details):
        if not str(label or "").strip().casefold().startswith("description"):
            continue
        match = re.search(
            r"(?:^|\n)\s*Achieving\s+Mastery\s*:\s*(?P<body>.+)\Z",
            str(content or ""),
            flags=re.IGNORECASE | re.DOTALL,
        )
        if match:
            return phase3._clean_public_text(match.group("body"))
    return ""


def _topic_evidence(
    graph: dict[str, Any],
    canonical: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    canonical_blocks = {
        str(row.get("block_id") or ""): row
        for row in canonical.get("blocks") or []
        if isinstance(row, dict)
    }
    graph_blocks = [
        row
        for row in graph.get("blocks") or []
        if isinstance(row, dict)
        and str(row.get("kind") or "") not in {"layout", "heading", "navigation"}
    ]
    block_order = {
        str(row.get("block_id") or ""): position
        for position, row in enumerate(graph_blocks, start=1)
    }
    topics = sorted(
        [
            row
            for row in graph.get("topics") or []
            if isinstance(row, dict) and str(row.get("topic_id") or "")
        ],
        key=lambda row: (
            int(row.get("order") or 0),
            str(row.get("topic_id") or ""),
        ),
    )
    limit = _topic_evidence_limit()
    out: list[dict[str, Any]] = []
    for topic in topics:
        topic_id = str(topic.get("topic_id") or "")
        pieces: list[str] = []
        used = 0
        omitted = 0
        for block in graph_blocks:
            if str(block.get("topic_id") or "") != topic_id:
                continue
            block_id = str(block.get("block_id") or "")
            source = canonical_blocks.get(block_id, {})
            text = phase3._clean_public_text(
                phase3._graph_block_text(block, source)
            )
            if not text:
                continue
            rendered = (
                f"[{block_id} | {str(block.get('subtopic_id') or 'NO-SUBTOPIC')} | "
                f"{str(block.get('kind') or 'content')}] {text}"
            )
            # Keep every block represented. Very large blocks are bounded, while
            # the exact block-level grounding pass remains authoritative later.
            rendered = rendered[:3000]
            if used + len(rendered) > limit and pieces:
                omitted += 1
                continue
            pieces.append(rendered)
            used += len(rendered)
        evidence = "\n\n".join(pieces)
        if omitted:
            evidence += (
                f"\n\n[AEGIS NOTE: {omitted} later block(s) exceeded the bounded "
                "topic-routing excerpt. Exact Phase 3.1 block grounding remains "
                "mandatory before topology freeze.]"
            )
        out.append(
            {
                "topic_id": topic_id,
                "order": int(topic.get("order") or 0),
                "title": str(topic.get("title") or ""),
                "structural_number": str(topic.get("structural_number") or ""),
                "evidence": evidence,
            }
        )
    return out, block_order


def _concept_rows(
    records: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    concepts: list[dict[str, Any]] = []
    index_by_id: dict[str, int] = {}
    for index, record in enumerate(records):
        title = str(record.get("concept_title") or record.get("concept") or "")
        if cr.is_culmination(title):
            continue
        concept_id = f"TOPOLOGY-CONCEPT-{index + 1:04d}"
        index_by_id[concept_id] = index
        concepts.append(
            {
                "concept_id": concept_id,
                "source_order": index + 1,
                "current_topic_id": str(record.get("_semantic_topic_id") or ""),
                "current_topic_title": str(record.get("topic") or ""),
                "concept_title": title,
                "parent_concept": str(record.get("parent_concept") or ""),
                "source_claim": phase31._description_source_claim(record),
                "existing_mastery": _existing_mastery(record),
            }
        )
    return concepts, index_by_id


def _segment_schema(topic_ids: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "topic_id": {"type": "string", "enum": topic_ids},
            "concept_title": {"type": "string"},
            "parent_concept": {"type": "string"},
            "description": {"type": "string"},
            "achieving_mastery": {"type": "string"},
            "keywords": {
                "type": "array",
                "items": {"type": "string"},
            },
            "confidence": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
            },
            "reason": {"type": "string"},
        },
        "required": [
            "topic_id",
            "concept_title",
            "parent_concept",
            "description",
            "achieving_mastery",
            "keywords",
            "confidence",
            "reason",
        ],
        "additionalProperties": False,
    }


def _adjudication_schema(
    concept_ids: list[str],
    topic_ids: list[str],
) -> dict[str, Any]:
    return {
        "name": "phase32_source_verified_concept_topology",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "concepts": {
                    "type": "array",
                    "minItems": len(concept_ids),
                    "maxItems": len(concept_ids),
                    "items": {
                        "type": "object",
                        "properties": {
                            "concept_id": {
                                "type": "string",
                                "enum": concept_ids,
                            },
                            "decision": {
                                "type": "string",
                                "enum": sorted(_ALLOWED_DECISIONS),
                            },
                            "segments": {
                                "type": "array",
                                "maxItems": 4,
                                "items": _segment_schema(topic_ids),
                            },
                            "confidence": {
                                "type": "number",
                                "minimum": 0,
                                "maximum": 1,
                            },
                            "reason": {"type": "string"},
                        },
                        "required": [
                            "concept_id",
                            "decision",
                            "segments",
                            "confidence",
                            "reason",
                        ],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["concepts"],
            "additionalProperties": False,
        },
    }


def _critic_schema(concept_ids: list[str]) -> dict[str, Any]:
    return {
        "name": "phase32_source_verified_concept_topology_critic",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "verdict": {
                    "type": "string",
                    "enum": ["verified", "rejected"],
                },
                "confidence": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1,
                },
                "accepted_concept_ids": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": concept_ids,
                    },
                },
                "rejected_concept_ids": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": concept_ids,
                    },
                },
                "issues": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": [
                "verdict",
                "confidence",
                "accepted_concept_ids",
                "rejected_concept_ids",
                "issues",
            ],
            "additionalProperties": False,
        },
    }


def _adjudicate_via_openai(payload: dict[str, Any]) -> dict[str, Any]:
    concept_ids = [
        str(row.get("concept_id") or "")
        for row in payload.get("concepts") or []
    ]
    topic_ids = [
        str(row.get("topic_id") or "")
        for row in payload.get("topics") or []
    ]
    system = (
        "You are the Aegis Phase 3.2 source-verified concept-topology "
        "adjudicator. The topology is not frozen yet. Compare every concept's "
        "source_claim against all supplied canonical topic evidence and choose "
        "exactly one action: keep when one durable concept is fully supported in "
        "its current topic; move when the whole concept belongs in one different "
        "topic; split only when the row combines two or more independently "
        "teachable, source-supported concepts; refine when unsupported or "
        "overbroad wording can be narrowed without losing a separate durable "
        "idea; review_required when no safe source-supported repair exists. "
        "Never create a concept merely because confidence is low. Never preserve "
        "a cross-topic claim inside the wrong topic. Avoid over-splitting examples "
        "that belong to one durable teaching concept. For every keep/move/refine "
        "return exactly one segment; for split return two to four segments. Each "
        "segment must be self-contained, grade-appropriate, non-duplicative, and "
        "fully supported by its target topic evidence. Description is the source-"
        "facing claim. Achieving Mastery is generated pedagogy and must state an "
        "observable learner capability. Use only supplied opaque topic and concept "
        "IDs. Do not create culmination rows, Types, Cases, questions, activities, "
        "or unsupported facts. On retries, repair only requested rejected IDs "
        "using critic_feedback and previous_decisions."
    )
    return phase3.phase22._openai_multimodal_json(
        system=system,
        prompt=json.dumps(payload, ensure_ascii=False, indent=2),
        pages=[],
        response_schema=_adjudication_schema(concept_ids, topic_ids),
        purpose="concept_mapping",
        max_tokens=max(6000, min(32000, len(concept_ids) * 1100)),
    )


def _critic_via_openai(payload: dict[str, Any]) -> dict[str, Any]:
    concept_ids = [
        str(row.get("concept_id") or "")
        for row in payload.get("concepts") or []
    ]
    system = (
        "You are the independent Aegis Phase 3.2 concept-topology critic. "
        "Verify every proposed keep, move, refine, or split against the supplied "
        "canonical topic evidence. Check that each resulting Description is fully "
        "source-supported in its assigned topic, that a move is preferred over a "
        "split when the whole claim belongs elsewhere, and that a split is used "
        "only for genuinely distinct durable teaching objectives. Reject new rows "
        "created merely because a confidence score was low, unsupported details, "
        "duplicate concepts, lost source-supported ideas, wrong-topic placement, "
        "over-fragmentation, question-shaped titles, or culmination-like rows. "
        "Achieving Mastery is generated pedagogy, so verify its specificity and "
        "alignment rather than requiring verbatim source wording. Put every "
        "concept ID in exactly one accepted or rejected list. A verified verdict "
        "requires all accepted, none rejected, confidence at least 0.96, and no "
        "issues. Do not rewrite the proposals."
    )
    return phase3.phase22._openai_multimodal_json(
        system=system,
        prompt=json.dumps(payload, ensure_ascii=False, indent=2),
        pages=[],
        response_schema=_critic_schema(concept_ids),
        purpose="concept_mapping",
        max_tokens=max(4000, min(12000, len(concept_ids) * 300)),
    )


def _parse_decisions(
    response: dict[str, Any],
    *,
    concepts: dict[str, dict[str, Any]],
    topic_ids: set[str],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    rows = response.get("concepts") if isinstance(response, dict) else None
    if not isinstance(rows, list):
        return {}, ["topology adjudicator returned no concepts array"]
    expected = set(concepts)
    proposals: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            errors.append("topology adjudicator returned a non-object row")
            continue
        concept_id = str(row.get("concept_id") or "")
        if concept_id not in expected:
            errors.append(f"unknown concept ID {concept_id or '<empty>'}")
            continue
        if concept_id in proposals:
            errors.append(f"duplicate concept ID {concept_id}")
            continue
        decision = str(row.get("decision") or "")
        confidence = float(row.get("confidence") or 0.0)
        segments = row.get("segments")
        if decision not in _ALLOWED_DECISIONS:
            errors.append(f"{concept_id} returned invalid decision {decision or '<empty>'}")
            continue
        if confidence < 0.96:
            errors.append(
                f"{concept_id} topology confidence {confidence:.3f} is below 0.960"
            )
            continue
        if decision == "review_required":
            errors.append(
                f"{concept_id} requires human review: {str(row.get('reason') or '')[:600]}"
            )
            continue
        if not isinstance(segments, list):
            errors.append(f"{concept_id} returned no segments array")
            continue
        required_count = 2 if decision == "split" else 1
        if (
            len(segments) < required_count
            or len(segments) > (4 if decision == "split" else 1)
        ):
            errors.append(
                f"{concept_id} decision {decision} returned {len(segments)} segment(s)"
            )
            continue
        current_topic = str(concepts[concept_id].get("current_topic_id") or "")
        normalized_segments: list[dict[str, Any]] = []
        segment_keys: set[tuple[str, str]] = set()
        invalid_segment = False
        for position, segment in enumerate(segments, start=1):
            if not isinstance(segment, dict):
                errors.append(f"{concept_id} segment {position} is not an object")
                invalid_segment = True
                break
            topic_id = str(segment.get("topic_id") or "")
            title = phase3._clean_public_text(segment.get("concept_title") or "")
            parent = phase3._clean_public_text(segment.get("parent_concept") or "")
            description = phase3._clean_public_text(segment.get("description") or "")
            mastery = phase3._clean_public_text(segment.get("achieving_mastery") or "")
            segment_confidence = float(segment.get("confidence") or 0.0)
            keywords = [
                phase3._clean_public_text(value)
                for value in segment.get("keywords") or []
                if phase3._clean_public_text(value)
            ]
            if topic_id not in topic_ids:
                errors.append(
                    f"{concept_id} segment {position} used unknown topic {topic_id or '<empty>'}"
                )
                invalid_segment = True
                break
            if not title or not parent or not description or not mastery:
                errors.append(
                    f"{concept_id} segment {position} omitted title, parent, "
                    "Description, or Achieving Mastery"
                )
                invalid_segment = True
                break
            if segment_confidence < 0.96:
                errors.append(
                    f"{concept_id} segment {position} confidence "
                    f"{segment_confidence:.3f} is below 0.960"
                )
                invalid_segment = True
                break
            key = (_normal(title), _normal(description))
            if key in segment_keys:
                errors.append(f"{concept_id} returned duplicate split segments")
                invalid_segment = True
                break
            segment_keys.add(key)
            normalized_segments.append(
                {
                    "topic_id": topic_id,
                    "concept_title": title,
                    "parent_concept": parent,
                    "description": description,
                    "achieving_mastery": mastery,
                    "keywords": keywords,
                    "confidence": segment_confidence,
                    "reason": str(segment.get("reason") or ""),
                }
            )
        if invalid_segment:
            continue
        if decision == "keep":
            segment = normalized_segments[0]
            original = concepts[concept_id]
            if segment["topic_id"] != current_topic:
                errors.append(f"{concept_id} keep decision changed its topic")
                continue
            if (
                _normal(segment["concept_title"])
                != _normal(original.get("concept_title"))
                or _normal(segment["parent_concept"])
                != _normal(original.get("parent_concept"))
                or _normal(segment["description"])
                != _normal(original.get("source_claim"))
            ):
                errors.append(
                    f"{concept_id} keep decision rewrote the existing concept"
                )
                continue
        if decision == "move" and normalized_segments[0]["topic_id"] == current_topic:
            errors.append(f"{concept_id} move decision retained its current topic")
            continue
        if decision == "refine" and normalized_segments[0]["topic_id"] != current_topic:
            errors.append(f"{concept_id} refine decision changed its topic")
            continue
        proposals[concept_id] = {
            "concept_id": concept_id,
            "decision": decision,
            "segments": normalized_segments,
            "confidence": confidence,
            "reason": str(row.get("reason") or ""),
        }
    missing = sorted(expected - set(proposals))
    if missing:
        errors.append("missing or invalid concept ID(s): " + ", ".join(missing))
    return proposals, errors


def _strip_transient(record: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(record)
    for key in list(out):
        if key in _TRANSIENT_FIELDS or key.startswith("_source_grounding_"):
            out.pop(key, None)
    return out


def _changed_record(
    base: dict[str, Any],
    *,
    segment: dict[str, Any],
    decision: str,
    concept_id: str,
    segment_order: int,
    source_order: int,
    topic_by_id: dict[str, dict[str, Any]],
    graph: dict[str, Any],
) -> dict[str, Any]:
    out = _strip_transient(base)
    topic_id = str(segment.get("topic_id") or "")
    topic = topic_by_id[topic_id]
    description = str(segment.get("description") or "").strip()
    mastery = str(segment.get("achieving_mastery") or "").strip()
    out["topic"] = str(topic.get("title") or "")
    out["_semantic_topic_id"] = topic_id
    out["_semantic_graph_contract"] = graph.get("source_contract_hash")
    out["_semantic_topic_contract"] = "phase3.2-api-verified-topology"
    out["concept_title"] = str(segment.get("concept_title") or "").strip()
    out["parent_concept"] = str(segment.get("parent_concept") or "").strip()
    out["concept_details"] = cr.join_sections(
        [
            (
                "Description",
                f"{description}\nAchieving Mastery: {mastery}",
            )
        ]
    )
    keywords = [
        str(value).strip()
        for value in segment.get("keywords") or []
        if str(value).strip()
    ]
    if keywords:
        out["keywords"] = ", ".join(dict.fromkeys(keywords))
    else:
        out["keywords"] = str(base.get("keywords") or "")
    out["_phase32_topology_decision"] = decision
    out["_phase32_origin_concept_id"] = concept_id
    out["_phase32_segment_order"] = int(segment_order)
    out["_phase32_source_order"] = int(source_order)
    return out


def _apply_decisions(
    records: list[dict[str, Any]],
    *,
    concepts: list[dict[str, Any]],
    index_by_id: dict[str, int],
    decisions: dict[str, dict[str, Any]],
    graph: dict[str, Any],
) -> list[dict[str, Any]]:
    topic_by_id = {
        str(row.get("topic_id") or ""): row
        for row in graph.get("topics") or []
        if isinstance(row, dict) and str(row.get("topic_id") or "")
    }
    concept_by_index = {
        index_by_id[str(row.get("concept_id") or "")]: str(row.get("concept_id") or "")
        for row in concepts
    }
    out: list[dict[str, Any]] = []
    for index, raw in enumerate(records):
        title = str(raw.get("concept_title") or raw.get("concept") or "")
        if cr.is_culmination(title):
            culmination = _strip_transient(raw)
            topic = topic_by_id.get(str(culmination.get("_semantic_topic_id") or ""))
            if topic is not None:
                culmination["topic"] = str(topic.get("title") or culmination.get("topic") or "")
                culmination["_semantic_graph_contract"] = graph.get("source_contract_hash")
            culmination["_phase32_source_order"] = index + 1
            out.append(culmination)
            continue
        concept_id = concept_by_index.get(index)
        if not concept_id or concept_id not in decisions:
            raise ValueError(
                "Phase 3.2 topology adjudication lost a normal concept identity"
            )
        decision = decisions[concept_id]
        source_order = int(
            next(
                row.get("source_order") or index + 1
                for row in concepts
                if row.get("concept_id") == concept_id
            )
        )
        if decision["decision"] == "keep":
            kept = _strip_transient(raw)
            current_topic = topic_by_id.get(
                str(kept.get("_semantic_topic_id") or "")
            )
            if current_topic is not None:
                kept["topic"] = str(
                    current_topic.get("title") or kept.get("topic") or ""
                )
                kept["_semantic_graph_contract"] = graph.get(
                    "source_contract_hash"
                )
            kept["_phase32_topology_decision"] = "keep"
            kept["_phase32_origin_concept_id"] = concept_id
            kept["_phase32_segment_order"] = 1
            kept["_phase32_source_order"] = source_order
            out.append(kept)
            continue
        for segment_order, segment in enumerate(decision["segments"], start=1):
            out.append(
                _changed_record(
                    raw,
                    segment=segment,
                    decision=str(decision["decision"]),
                    concept_id=concept_id,
                    segment_order=segment_order,
                    source_order=source_order,
                    topic_by_id=topic_by_id,
                    graph=graph,
                )
            )
    return out


def _order_grounded_records(
    records: list[dict[str, Any]],
    *,
    graph: dict[str, Any],
    canonical: dict[str, Any],
) -> list[dict[str, Any]]:
    topic_order = {
        str(row.get("topic_id") or ""): int(row.get("order") or 0)
        for row in graph.get("topics") or []
        if isinstance(row, dict)
    }
    canonical_order = {
        str(row.get("block_id") or ""): int(row.get("order") or position)
        for position, row in enumerate(
            [
                item
                for item in canonical.get("blocks") or []
                if isinstance(item, dict)
            ],
            start=1,
        )
    }

    def key(record: dict[str, Any]) -> tuple[int, int, int, int, str]:
        topic_id = str(record.get("_semantic_topic_id") or "")
        title = str(record.get("concept_title") or record.get("concept") or "")
        culmination = cr.is_culmination(title)
        source_positions = [
            canonical_order.get(str(value), 10**9)
            for value in record.get("_source_block_ids") or []
            if str(value)
        ]
        source_position = min(source_positions, default=10**9)
        return (
            topic_order.get(topic_id, 10**9),
            1 if culmination else 0,
            source_position,
            int(record.get("_phase32_source_order") or 10**9) * 10
            + int(record.get("_phase32_segment_order") or 0),
            _normal(title),
        )

    ordered = sorted([copy.deepcopy(row) for row in records], key=key)
    seen_titles: dict[str, str] = {}
    for row in ordered:
        title = str(row.get("concept_title") or row.get("concept") or "").strip()
        if cr.is_culmination(title):
            continue
        title_key = _normal(title)
        if not title_key:
            raise ValueError("Phase 3.2 produced an empty normal concept title")
        prior = seen_titles.get(title_key)
        if prior is not None:
            raise ValueError(
                "Phase 3.2 produced duplicate normal concept title "
                f"{title!r} from {prior} and "
                f"{str(row.get('_phase32_origin_concept_id') or 'unknown')}"
            )
        seen_titles[title_key] = str(
            row.get("_phase32_origin_concept_id") or "unknown"
        )
    return ordered


def _cache_key(
    records: list[dict[str, Any]],
    *,
    graph: dict[str, Any],
    topics: list[dict[str, Any]],
    concepts: list[dict[str, Any]],
) -> str:
    return phase3._sha256_json(
        {
            "version": _TOPOLOGY_VERSION,
            "model": str(config.OPENAI_MODEL),
            "source_contract_hash": str(graph.get("source_contract_hash") or ""),
            "records": phase31._json_safe(records),
            "concepts": concepts,
            "topics": [
                {
                    "topic_id": row.get("topic_id"),
                    "title": row.get("title"),
                    "evidence_sha256": phase3._sha256_text(row.get("evidence")),
                }
                for row in topics
            ],
        }
    )


def _read_cached_records(cache_key: str) -> list[dict[str, Any]] | None:
    directory = _artifact_dir()
    if directory is None:
        return None
    cache = _read_json(directory / _CACHE_FILENAME)
    if (
        cache.get("version") != _TOPOLOGY_VERSION
        or cache.get("cache_key") != cache_key
        or not isinstance(cache.get("records"), list)
        or cache.get("records_sha256")
        != phase3._sha256_json(cache.get("records"))
    ):
        return None
    return copy.deepcopy(cache["records"])


def _write_cached_records(
    cache_key: str,
    *,
    graph: dict[str, Any],
    records: list[dict[str, Any]],
    summary: dict[str, int],
) -> None:
    directory = _artifact_dir()
    if directory is None:
        return
    _write_json(
        directory / _CACHE_FILENAME,
        {
            "version": _TOPOLOGY_VERSION,
            "cache_key": cache_key,
            "created_at": time.time(),
            "model": str(config.OPENAI_MODEL),
            "source_contract_hash": str(graph.get("source_contract_hash") or ""),
            "records_sha256": phase3._sha256_json(records),
            "summary": copy.deepcopy(summary),
            "records": copy.deepcopy(records),
        },
    )


def adjudicate_topology(
    records: list[dict[str, Any]],
    *,
    graph: dict[str, Any] | None = None,
    canonical: dict[str, Any] | None = None,
    provider: TopologyProvider | None = None,
    critic: TopologyCritic | None = None,
    grounding_provider: GroundingProvider | None = None,
    grounding_critic: GroundingCritic | None = None,
) -> list[dict[str, Any]]:
    graph = graph or phase3.active_graph()
    if not isinstance(graph, dict) or not records:
        return records
    canonical = (
        canonical
        or (phase3.active_session() or {}).get("canonical")
        or {}
    )
    out = phase3.canonicalize_record_topics(records, graph)
    topics, _block_order = _topic_evidence(graph, canonical)
    topic_ids = {
        str(row.get("topic_id") or "")
        for row in topics
        if str(row.get("topic_id") or "")
    }
    concepts, index_by_id = _concept_rows(out)
    if not concepts or not topics:
        return out

    cache_key = _cache_key(
        out,
        graph=graph,
        topics=topics,
        concepts=concepts,
    )
    cached = _read_cached_records(cache_key)
    if cached is not None:
        progress.log(
            "Reused the API-verified Phase 3.2 concept topology, including "
            "cross-topic move/split decisions and exact source grounding; no "
            "topology or grounding model call was repeated.",
            level="success",
        )
        return cached

    provider = provider or (
        _adjudicate_via_openai if phase3.semantic_api_enabled() else None
    )
    critic = critic or (
        _critic_via_openai if phase3.semantic_api_enabled() else None
    )
    if provider is None or critic is None:
        # Offline/test mode retains the existing topology. Phase 3.1 still
        # performs deterministic topic-bounded grounding.
        return out

    progress.step(
        "Concept extraction — source-verifying topology before learner analysis",
        value=0.89,
    )
    progress.log(
        "Auditing normal concepts against every canonical chapter topic before "
        "topology freeze; unsupported cross-topic rows may be moved, refined, or "
        "split only after independent verification.",
        level="warning",
    )

    decisions: dict[str, dict[str, Any]] = {}
    attempts = _max_attempts()
    size = _batch_size()
    concept_by_id = {
        str(row.get("concept_id") or ""): row for row in concepts
    }
    for batch_index, start in enumerate(range(0, len(concepts), size), start=1):
        batch = concepts[start : start + size]
        batch_ids = {
            str(row.get("concept_id") or "") for row in batch
        }
        accepted: dict[str, dict[str, Any]] = {}
        unresolved = set(batch_ids)
        previous_decisions: list[dict[str, Any]] = []
        critic_feedback: dict[str, Any] = {}
        last_errors: list[str] = []
        last_confidence = 0.0
        for attempt in range(1, attempts + 1):
            requested = [
                row
                for row in batch
                if str(row.get("concept_id") or "") in unresolved
            ]
            payload = {
                "metadata": copy.deepcopy(graph.get("metadata") or {}),
                "topics": copy.deepcopy(topics),
                "concepts": copy.deepcopy(requested),
                "batch": batch_index,
                "attempt": attempt,
                "max_attempts": attempts,
                "previous_decisions": copy.deepcopy(previous_decisions),
                "critic_feedback": copy.deepcopy(critic_feedback),
            }
            response = provider(copy.deepcopy(payload))
            parsed, parse_errors = _parse_decisions(
                response,
                concepts={
                    concept_id: concept_by_id[concept_id]
                    for concept_id in unresolved
                },
                topic_ids=topic_ids,
            )
            if parse_errors:
                last_errors = parse_errors
                progress.log(
                    "Phase 3.2 topology attempt "
                    f"{attempt}/{attempts} for batch {batch_index} requires "
                    "correction: " + "; ".join(parse_errors[:4]),
                    level="warning",
                )
                previous_decisions = [
                    copy.deepcopy(value) for value in accepted.values()
                ]
                critic_feedback = {
                    "verdict": "provider_contract_rejected",
                    "confidence": 0.0,
                    "issues": parse_errors,
                    "rejected_concept_ids": sorted(unresolved),
                }
                continue
            accepted.update(parsed)
            if set(accepted) != batch_ids:
                unresolved = batch_ids - set(accepted)
                last_errors = [
                    "topology adjudicator did not yet cover every concept"
                ]
                continue
            review_payload = {
                "metadata": copy.deepcopy(graph.get("metadata") or {}),
                "topics": copy.deepcopy(topics),
                "concepts": copy.deepcopy(batch),
                "proposed_decisions": [
                    copy.deepcopy(accepted[concept_id])
                    for concept_id in sorted(batch_ids)
                ],
            }
            review = critic(copy.deepcopy(review_payload))
            state = phase31._review_state(
                review,
                concept_ids=batch_ids,
            )
            last_confidence = float(state["confidence"])
            if state["verified"]:
                progress.log(
                    "Phase 3.2 independently verified topology decisions for "
                    f"{len(batch_ids)} concept(s) in batch {batch_index}"
                    + (
                        f" after {attempt} attempt(s)."
                        if attempt > 1
                        else "."
                    ),
                    level="success",
                )
                break
            unresolved = set(state["rejected"]) or set(batch_ids)
            last_errors = list(state["issues"]) or [
                "critic verdict was "
                + str(state.get("verdict") or "missing")
            ]
            progress.log(
                "Phase 3.2 topology attempt "
                f"{attempt}/{attempts} for batch {batch_index} accepted "
                f"{len(batch_ids - unresolved)}/{len(batch_ids)} concept(s); "
                f"{len(unresolved)} require correction"
                + (
                    ": " + "; ".join(last_errors[:4])
                    if last_errors
                    else "."
                ),
                level="warning",
            )
            previous_decisions = [
                copy.deepcopy(accepted[concept_id])
                for concept_id in sorted(batch_ids)
            ]
            critic_feedback = copy.deepcopy(review)
            for concept_id in unresolved:
                accepted.pop(concept_id, None)
        else:
            titles = [
                str(concept_by_id[concept_id].get("concept_title") or concept_id)
                for concept_id in sorted(unresolved)
            ]
            details = "; ".join(last_errors[:6])
            raise ValueError(
                "Phase 3.2 could not source-verify concept topology for "
                + ", ".join(repr(title) for title in titles[:6])
                + f" after {attempts} attempt(s)"
                + (
                    f" (critic confidence {last_confidence:.3f})"
                    if last_confidence
                    else ""
                )
                + (f": {details}" if details else "")
            )
        if set(accepted) != batch_ids:
            raise ValueError(
                "Phase 3.2 topology adjudication ended without every concept"
            )
        decisions.update(accepted)

    repaired = _apply_decisions(
        out,
        concepts=concepts,
        index_by_id=index_by_id,
        decisions=decisions,
        graph=graph,
    )
    # Exact source-block grounding is authoritative. Running it before learner
    # analysis proves the repaired topology can actually be frozen.
    try:
        grounded = phase31.ground_concepts(
            repaired,
            graph=graph,
            canonical=canonical,
            provider=grounding_provider,
            critic=grounding_critic,
        )
    except ValueError as exc:
        raise ValueError(
            "Phase 3.2 topology decisions passed independent topic review, but "
            "the repaired rows still failed exact source-block grounding before "
            f"freeze: {exc}"
        ) from exc

    grounded = _order_grounded_records(
        grounded,
        graph=graph,
        canonical=canonical,
    )
    summary = {
        decision: sum(
            1 for value in decisions.values()
            if value.get("decision") == decision
        )
        for decision in ("keep", "move", "split", "refine")
    }
    summary["input_normal_concepts"] = len(concepts)
    summary["output_normal_concepts"] = sum(
        1
        for row in grounded
        if not cr.is_culmination(
            str(row.get("concept_title") or row.get("concept") or "")
        )
    )
    _write_cached_records(
        cache_key,
        graph=graph,
        records=grounded,
        summary=summary,
    )
    progress.log(
        "Phase 3.2 froze a source-verified topology candidate before learner "
        "analysis: "
        f"{summary['keep']} kept, {summary['move']} moved, "
        f"{summary['split']} split, {summary['refine']} refined; "
        f"{summary['input_normal_concepts']} input normal concept(s) became "
        f"{summary['output_normal_concepts']} source-grounded normal concept(s).",
        level="success",
    )
    return grounded


def _prepare_with_adjudication(
    original_prepare: Callable[..., list[dict[str, Any]]],
    records: list[dict[str, Any]],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> list[dict[str, Any]]:
    graph = phase3.active_graph()
    session = phase3.active_session()
    if not isinstance(graph, dict) or not isinstance(session, dict):
        return original_prepare(records, *args, **kwargs)
    repaired = adjudicate_topology(
        records,
        graph=graph,
        canonical=session.get("canonical") or {},
    )
    return original_prepare(repaired, *args, **kwargs)


def install(generation: Any | None = None) -> None:
    if (
        getattr(phase3, "_PHASE32_TOPOLOGY_CONTRACT_VERSION", 0)
        >= _CONTRACT_VERSION
    ):
        return
    if generation is None:
        from . import generation as generation

    original_prepare = getattr(
        generation,
        "_TOPOLOGY_CONTRACT_ORIGINAL_PREPARE",
        None,
    )
    if callable(original_prepare):
        generation._PHASE32_ORIGINAL_TOPOLOGY_PREPARE = original_prepare

        @wraps(original_prepare)
        def adjudicated_prepare(records, *args, **kwargs):
            return _prepare_with_adjudication(
                original_prepare,
                records,
                args,
                kwargs,
            )

        generation._TOPOLOGY_CONTRACT_ORIGINAL_PREPARE = adjudicated_prepare

    phase3._PHASE32_TOPOLOGY_CONTRACT_VERSION = _CONTRACT_VERSION

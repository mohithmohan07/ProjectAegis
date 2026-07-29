"""Phase 3.1 concept-to-source grounding recovery and resume cache.

The live RNE acceptance run proved the source graph and concept topology were
valid, then exposed a contract mismatch at Type allocation: concept grounding
sent the complete public ``concept_details`` cell to an evidence critic. That
cell includes generated mastery and learner-analysis prose which is deliberately
not copied from the textbook, so a strict source critic could correctly reject
an otherwise valid concept-to-block mapping forever.

This contract narrows grounding to the source-facing Description claim, derives
culmination grounding from already-verified normal concepts, retries only
critic-rejected concept IDs, persists verified per-topic grounding, and caches
the validated pre-allocation topology so a resume does not repeat learner-
analysis calls after a later allocation failure.
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
from . import concept_refiner as cr
from . import progress

_CONTRACT_VERSION = 1
_GROUNDING_VERSION = "phase3.1-source-claim-grounding-1"
_GROUNDING_CACHE_FILENAME = "source.phase31-concept-grounding-cache.json"
_TOPOLOGY_CACHE_FILENAME = "source.phase31-final-topology-cache.json"
_MASTERY_TAIL_RE = re.compile(
    r"(?:\r?\n|\\n)\s*Achieving\s+Mastery\s*:\s*.*\Z",
    re.IGNORECASE | re.DOTALL,
)


GroundingProvider = Callable[[dict[str, Any]], dict[str, Any]]
GroundingCritic = Callable[[dict[str, Any]], dict[str, Any]]


def _max_grounding_attempts() -> int:
    return max(
        1,
        min(
            5,
            int(os.environ.get("AEGIS_PHASE3_GROUNDING_MAX_ATTEMPTS", "3")),
        ),
    )


def _normal(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def _json_safe(value: object) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {
            str(key): _json_safe(item)
            for key, item in sorted(value.items(), key=lambda row: str(row[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, set):
        return sorted(_json_safe(item) for item in value)
    return str(value)


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


def _description_source_claim(record: dict[str, Any]) -> str:
    """Return only the concept proposition expected to be supported by source.

    Achieving Mastery, Types, Activity/Info Hub and learner-analysis sections are
    generated pedagogical enrichments. They are intentionally excluded from the
    PDF-evidence contract.
    """
    details = str(
        record.get("concept_details")
        or record.get("concept_description")
        or ""
    )
    description = ""
    for label, content in cr.split_sections(details):
        if str(label or "").strip().casefold().startswith("description"):
            description = str(content or "").strip()
            break
    description = _MASTERY_TAIL_RE.sub("", description).strip()
    description = phase3._clean_public_text(description)
    if description:
        return description
    return phase3._clean_public_text(
        record.get("concept_title") or record.get("concept") or ""
    )


def _candidate_blocks(
    graph: dict[str, Any],
    canonical: dict[str, Any],
    topic_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    canonical_blocks = {
        str(row.get("block_id") or ""): row
        for row in canonical.get("blocks") or []
        if isinstance(row, dict)
    }
    graph_blocks = [
        row
        for row in graph.get("blocks") or []
        if isinstance(row, dict)
        and str(row.get("topic_id") or "") == topic_id
        and str(row.get("kind") or "") not in {
            "layout",
            "heading",
            "navigation",
        }
    ]
    payload: list[dict[str, Any]] = []
    usable: list[dict[str, Any]] = []
    for block in graph_blocks:
        source = canonical_blocks.get(str(block.get("block_id") or ""), {})
        text = phase3._clean_public_text(
            phase3._graph_block_text(block, source)
        )
        if not text:
            continue
        usable.append(block)
        payload.append(
            {
                "block_id": str(block.get("block_id") or ""),
                "kind": str(block.get("kind") or ""),
                "subtopic_id": str(block.get("subtopic_id") or ""),
                "text": text[:1800],
            }
        )
    return usable, payload


def _concept_payload(
    records: list[dict[str, Any]],
    indices: list[int],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    rows: list[dict[str, Any]] = []
    index_by_id: dict[str, int] = {}
    for index in indices:
        concept_id = f"CONCEPT-GROUND-{index + 1:04d}"
        index_by_id[concept_id] = index
        claim = _description_source_claim(records[index])
        rows.append(
            {
                "concept_id": concept_id,
                "source_order": index + 1,
                "concept_title": str(
                    records[index].get("concept_title")
                    or records[index].get("concept")
                    or ""
                ),
                "parent_concept": str(
                    records[index].get("parent_concept") or ""
                ),
                # Keep ``description`` for backward-compatible injected providers,
                # while making the bounded evidence field explicit.
                "description": claim,
                "source_claim": claim,
            }
        )
    return rows, index_by_id


def _grounding_schema(
    concept_ids: list[str],
    block_ids: list[str],
) -> dict[str, Any]:
    return {
        "name": "phase31_concept_source_grounding",
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
                            "source_block_ids": {
                                "type": "array",
                                "minItems": 1,
                                "items": {
                                    "type": "string",
                                    "enum": block_ids,
                                },
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
                            "source_block_ids",
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
        "name": "phase31_concept_source_grounding_critic",
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


def _ground_via_openai(payload: dict[str, Any]) -> dict[str, Any]:
    concept_ids = [
        str(row.get("concept_id") or "")
        for row in payload.get("concepts") or []
    ]
    block_ids = [
        str(row.get("block_id") or "")
        for row in payload.get("source_blocks") or []
    ]
    system = (
        "You are the Aegis Phase 3.1 source-grounding mapper. Ground only each "
        "concept's source_claim to the smallest sufficient set of supplied "
        "source_block_ids from its already-fixed canonical topic. The title and "
        "parent are orientation labels. Do not require textbook evidence for "
        "Achieving Mastery, learner misconceptions, Error Analysis, Types, "
        "Activity/Info Hubs, or other generated pedagogy because those fields "
        "are intentionally absent from this contract. Semantic support is "
        "required; verbatim wording is not. Use only opaque IDs, do not rewrite "
        "text, and return every requested concept exactly once. On a correction "
        "attempt, use critic_feedback and previous_grounding to repair only the "
        "requested unresolved concepts."
    )
    return phase3.phase22._openai_multimodal_json(
        system=system,
        prompt=json.dumps(payload, ensure_ascii=False, indent=2),
        pages=[],
        response_schema=_grounding_schema(concept_ids, block_ids),
        purpose="concept_mapping",
        max_tokens=max(4000, min(20000, len(concept_ids) * 260)),
    )


def _critic_via_openai(payload: dict[str, Any]) -> dict[str, Any]:
    concept_ids = [
        str(row.get("concept_id") or "")
        for row in payload.get("concepts") or []
    ]
    system = (
        "You are the independent Aegis Phase 3.1 source-grounding critic. "
        "Evaluate only whether each source_claim is semantically and visibly "
        "supported by the proposed source_block_ids inside the fixed topic, and "
        "whether the selected set is minimally sufficient. Do not demand source "
        "support for mastery, learner analysis, Types, hubs, keywords, parent "
        "labels, or other generated pedagogy. Put every concept ID in exactly "
        "one of accepted_concept_ids or rejected_concept_ids. A verified verdict "
        "requires all concepts accepted, none rejected, confidence at least "
        "0.96, and no issues. Do not rewrite proposals."
    )
    return phase3.phase22._openai_multimodal_json(
        system=system,
        prompt=json.dumps(payload, ensure_ascii=False, indent=2),
        pages=[],
        response_schema=_critic_schema(concept_ids),
        purpose="concept_mapping",
        max_tokens=max(4000, min(8000, len(concept_ids) * 180)),
    )


def _parse_proposals(
    response: dict[str, Any],
    *,
    expected_ids: set[str],
    allowed_blocks: set[str],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    rows = response.get("concepts") if isinstance(response, dict) else None
    if not isinstance(rows, list):
        return {}, ["grounding provider returned no concepts array"]
    proposals: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            errors.append("grounding provider returned a non-object row")
            continue
        concept_id = str(row.get("concept_id") or "")
        if concept_id not in expected_ids:
            errors.append(f"unknown concept ID {concept_id or '<empty>'}")
            continue
        if concept_id in proposals:
            errors.append(f"duplicate concept ID {concept_id}")
            continue
        block_ids = [
            str(value)
            for value in row.get("source_block_ids") or []
            if str(value)
        ]
        confidence = float(row.get("confidence") or 0.0)
        if not block_ids:
            errors.append(f"{concept_id} has no source block")
            continue
        invalid = [value for value in block_ids if value not in allowed_blocks]
        if invalid:
            errors.append(
                f"{concept_id} used unknown source block(s): "
                + ", ".join(invalid[:4])
            )
            continue
        if confidence < 0.96:
            errors.append(
                f"{concept_id} confidence {confidence:.3f} is below 0.960"
            )
            continue
        proposals[concept_id] = {
            "concept_id": concept_id,
            "source_block_ids": list(dict.fromkeys(block_ids)),
            "confidence": confidence,
            "reason": str(row.get("reason") or ""),
        }
    missing = sorted(expected_ids - set(proposals))
    if missing:
        errors.append("missing or invalid concept ID(s): " + ", ".join(missing))
    return proposals, errors


def _review_state(
    review: dict[str, Any],
    *,
    concept_ids: set[str],
) -> dict[str, Any]:
    if not isinstance(review, dict):
        return {
            "verified": False,
            "confidence": 0.0,
            "accepted": set(),
            "rejected": set(concept_ids),
            "issues": ["critic returned no review object"],
        }
    verdict = str(review.get("verdict") or "")
    confidence = float(review.get("confidence") or 0.0)
    issues = [
        str(value).strip()
        for value in review.get("issues") or []
        if str(value).strip()
    ]
    has_partition = (
        "accepted_concept_ids" in review
        or "rejected_concept_ids" in review
    )
    if has_partition:
        accepted = {
            str(value)
            for value in review.get("accepted_concept_ids") or []
            if str(value) in concept_ids
        }
        rejected = {
            str(value)
            for value in review.get("rejected_concept_ids") or []
            if str(value) in concept_ids
        }
        unknown = (
            {
                str(value)
                for field in (
                    "accepted_concept_ids",
                    "rejected_concept_ids",
                )
                for value in review.get(field) or []
            }
            - concept_ids
        )
        overlap = accepted & rejected
        omitted = concept_ids - accepted - rejected
        if unknown:
            issues.append(
                "critic returned unknown concept ID(s): "
                + ", ".join(sorted(unknown))
            )
        if overlap:
            issues.append(
                "critic both accepted and rejected: "
                + ", ".join(sorted(overlap))
            )
        if omitted:
            issues.append(
                "critic omitted concept ID(s): "
                + ", ".join(sorted(omitted))
            )
            rejected |= omitted
    elif (
        verdict == "verified"
        and confidence >= 0.96
        and not issues
    ):
        # Backward-compatible injected critics used by existing tests.
        accepted = set(concept_ids)
        rejected = set()
    else:
        accepted = set()
        rejected = set(concept_ids)

    verified = bool(
        verdict == "verified"
        and confidence >= 0.96
        and not issues
        and accepted == concept_ids
        and not rejected
    )
    if not verified and not rejected:
        rejected = set(concept_ids)
    return {
        "verified": verified,
        "confidence": confidence,
        "accepted": accepted,
        "rejected": rejected,
        "issues": issues,
        "verdict": verdict,
    }


def _grounding_cache_key(
    *,
    graph: dict[str, Any],
    topic_id: str,
    concepts: list[dict[str, Any]],
    source_blocks: list[dict[str, Any]],
) -> str:
    return phase3._sha256_json(
        {
            "version": _GROUNDING_VERSION,
            "model": str(config.OPENAI_MODEL),
            "source_contract_hash": str(
                graph.get("source_contract_hash") or ""
            ),
            "topic_id": topic_id,
            "concepts": concepts,
            "source_blocks": [
                {
                    "block_id": row.get("block_id"),
                    "kind": row.get("kind"),
                    "subtopic_id": row.get("subtopic_id"),
                    "text_sha256": phase3._sha256_text(row.get("text")),
                }
                for row in source_blocks
            ],
        }
    )


def _read_cached_proposals(
    cache_key: str,
    *,
    concept_ids: set[str],
    allowed_blocks: set[str],
) -> dict[str, dict[str, Any]] | None:
    directory = _artifact_dir()
    if directory is None:
        return None
    cache = _read_json(directory / _GROUNDING_CACHE_FILENAME)
    if cache.get("version") != _GROUNDING_VERSION:
        return None
    entry = (cache.get("entries") or {}).get(cache_key)
    if not isinstance(entry, dict) or entry.get("status") != "verified":
        return None
    proposals = entry.get("proposals")
    if not isinstance(proposals, list):
        return None
    parsed, errors = _parse_proposals(
        {"concepts": proposals},
        expected_ids=concept_ids,
        allowed_blocks=allowed_blocks,
    )
    return parsed if not errors else None


def _write_cached_proposals(
    cache_key: str,
    *,
    graph: dict[str, Any],
    topic_id: str,
    proposals: dict[str, dict[str, Any]],
    review_confidence: float,
) -> None:
    directory = _artifact_dir()
    if directory is None:
        return
    path = directory / _GROUNDING_CACHE_FILENAME
    cache = _read_json(path)
    if cache.get("version") != _GROUNDING_VERSION:
        cache = {
            "version": _GROUNDING_VERSION,
            "entries": {},
        }
    entries = cache.setdefault("entries", {})
    entries[cache_key] = {
        "status": "verified",
        "created_at": time.time(),
        "model": str(config.OPENAI_MODEL),
        "source_contract_hash": str(
            graph.get("source_contract_hash") or ""
        ),
        "topic_id": topic_id,
        "review_confidence": float(review_confidence),
        "proposals": [
            copy.deepcopy(proposals[key])
            for key in sorted(proposals)
        ],
    }
    _write_json(path, cache)


def _apply_proposals(
    records: list[dict[str, Any]],
    *,
    proposals: dict[str, dict[str, Any]],
    index_by_id: dict[str, int],
    candidates: list[dict[str, Any]],
) -> None:
    subtopic_by_block = {
        str(block.get("block_id") or ""): str(
            block.get("subtopic_id") or ""
        )
        for block in candidates
    }
    for concept_id, proposal in proposals.items():
        index = index_by_id[concept_id]
        block_ids = list(proposal["source_block_ids"])
        records[index]["_source_block_ids"] = block_ids
        records[index]["_semantic_subtopic_ids"] = sorted(
            {
                subtopic_by_block.get(block_id, "")
                for block_id in block_ids
            }
            - {""}
        )
        records[index]["_source_grounding_contract"] = (
            "api-verified-source-block-ids"
        )
        records[index]["_source_grounding_version"] = _GROUNDING_VERSION
        records[index]["_source_grounding_confidence"] = float(
            proposal["confidence"]
        )


def _apply_deterministic_topic_grounding(
    records: list[dict[str, Any]],
    *,
    indices: list[int],
    candidates: list[dict[str, Any]],
) -> None:
    block_ids = [
        str(row.get("block_id") or "")
        for row in candidates
        if str(row.get("block_id") or "")
    ]
    subtopics = sorted(
        {
            str(row.get("subtopic_id") or "")
            for row in candidates
            if str(row.get("subtopic_id") or "")
        }
    )
    for index in indices:
        records[index]["_source_block_ids"] = list(block_ids)
        records[index]["_semantic_subtopic_ids"] = list(subtopics)
        records[index]["_source_grounding_contract"] = (
            "topic-bounded-deterministic"
        )
        records[index]["_source_grounding_version"] = _GROUNDING_VERSION


def _apply_culmination_grounding(
    records: list[dict[str, Any]],
    *,
    normal_indices: list[int],
    culmination_indices: list[int],
    candidates: list[dict[str, Any]],
) -> None:
    if not culmination_indices:
        return
    candidate_order = [
        str(row.get("block_id") or "")
        for row in candidates
        if str(row.get("block_id") or "")
    ]
    selected = {
        str(block_id)
        for index in normal_indices
        for block_id in records[index].get("_source_block_ids") or []
        if str(block_id)
    }
    block_ids = [
        block_id for block_id in candidate_order if block_id in selected
    ]
    if not block_ids:
        block_ids = list(candidate_order)
    subtopic_by_block = {
        str(block.get("block_id") or ""): str(
            block.get("subtopic_id") or ""
        )
        for block in candidates
    }
    confidences = [
        float(records[index].get("_source_grounding_confidence") or 1.0)
        for index in normal_indices
        if records[index].get("_source_block_ids")
    ]
    confidence = min(confidences, default=1.0)
    contract = (
        "derived-from-verified-topic-concepts"
        if normal_indices
        else "topic-bounded-deterministic-culmination"
    )
    for index in culmination_indices:
        records[index]["_source_block_ids"] = list(block_ids)
        records[index]["_semantic_subtopic_ids"] = sorted(
            {
                subtopic_by_block.get(block_id, "")
                for block_id in block_ids
            }
            - {""}
        )
        records[index]["_source_grounding_contract"] = contract
        records[index]["_source_grounding_version"] = _GROUNDING_VERSION
        records[index]["_source_grounding_confidence"] = confidence


def ground_concepts(
    records: list[dict[str, Any]],
    *,
    graph: dict[str, Any] | None = None,
    canonical: dict[str, Any] | None = None,
    provider: GroundingProvider | None = None,
    critic: GroundingCritic | None = None,
) -> list[dict[str, Any]]:
    graph = graph or phase3.active_graph()
    if not isinstance(graph, dict) or not records:
        return records
    out = phase3.canonicalize_record_topics(records, graph)
    canonical = (
        canonical
        or (phase3.active_session() or {}).get("canonical")
        or {}
    )
    topics = [
        row
        for row in graph.get("topics") or []
        if isinstance(row, dict) and str(row.get("topic_id") or "")
    ]
    provider = provider or (
        _ground_via_openai if phase3.semantic_api_enabled() else None
    )
    critic = critic or (
        _critic_via_openai if phase3.semantic_api_enabled() else None
    )

    for topic in topics:
        topic_id = str(topic.get("topic_id") or "")
        topic_title = str(topic.get("title") or topic_id)
        indices = [
            index
            for index, row in enumerate(out)
            if str(row.get("_semantic_topic_id") or "") == topic_id
        ]
        if not indices:
            continue
        normal_indices = [
            index
            for index in indices
            if not cr.is_culmination(
                str(
                    out[index].get("concept_title")
                    or out[index].get("concept")
                    or ""
                )
            )
        ]
        culmination_indices = [
            index for index in indices if index not in normal_indices
        ]
        candidates, source_blocks = _candidate_blocks(
            graph, canonical, topic_id
        )
        if not source_blocks:
            continue

        # Missing-host reconciliation already performs its own bounded proposal
        # and independent critic. Preserve those verified IDs instead of asking
        # a second model to rediscover them.
        pending_indices: list[int] = []
        for index in normal_indices:
            existing = [
                str(value)
                for value in out[index].get("_source_block_ids") or []
                if str(value)
            ]
            contract = str(
                out[index].get("_source_grounding_contract") or ""
            )
            allowed = {
                str(row.get("block_id") or "")
                for row in source_blocks
            }
            if (
                contract == "api-created-missing-type-host"
                and existing
                and set(existing).issubset(allowed)
            ):
                out[index]["_source_grounding_confidence"] = 0.999
                out[index]["_source_grounding_version"] = _GROUNDING_VERSION
            else:
                pending_indices.append(index)

        if not pending_indices:
            _apply_culmination_grounding(
                out,
                normal_indices=normal_indices,
                culmination_indices=culmination_indices,
                candidates=candidates,
            )
            continue

        concepts, index_by_id = _concept_payload(out, pending_indices)
        concept_ids = set(index_by_id)
        allowed_blocks = {
            str(row.get("block_id") or "")
            for row in source_blocks
        }

        if provider is None:
            _apply_deterministic_topic_grounding(
                out,
                indices=pending_indices,
                candidates=candidates,
            )
            _apply_culmination_grounding(
                out,
                normal_indices=normal_indices,
                culmination_indices=culmination_indices,
                candidates=candidates,
            )
            continue
        if critic is None:
            raise ValueError(
                "Phase 3 concept grounding requires an independent critic"
            )

        cache_key = _grounding_cache_key(
            graph=graph,
            topic_id=topic_id,
            concepts=concepts,
            source_blocks=source_blocks,
        )
        proposals = _read_cached_proposals(
            cache_key,
            concept_ids=concept_ids,
            allowed_blocks=allowed_blocks,
        )
        review_confidence = 0.999
        if proposals is not None:
            progress.log(
                "Reused API-verified Phase 3 concept grounding for "
                f"{topic_title!r} ({len(proposals)} concept(s)); no grounding "
                "model call was repeated.",
                level="success",
            )
        else:
            proposals = {}
            unresolved = set(concept_ids)
            previous_grounding: list[dict[str, Any]] = []
            critic_feedback: dict[str, Any] = {}
            last_errors: list[str] = []
            last_confidence = 0.0
            attempts = _max_grounding_attempts()

            for attempt in range(1, attempts + 1):
                requested = [
                    row
                    for row in concepts
                    if str(row.get("concept_id") or "") in unresolved
                ]
                attempt_payload = {
                    "topic": copy.deepcopy(topic),
                    "concepts": copy.deepcopy(requested),
                    "source_blocks": copy.deepcopy(source_blocks),
                    "attempt": attempt,
                    "max_attempts": attempts,
                    "previous_grounding": copy.deepcopy(
                        previous_grounding
                    ),
                    "critic_feedback": copy.deepcopy(critic_feedback),
                }
                response = provider(copy.deepcopy(attempt_payload))
                parsed, parse_errors = _parse_proposals(
                    response,
                    expected_ids=set(unresolved),
                    allowed_blocks=allowed_blocks,
                )
                if parse_errors:
                    last_errors = parse_errors
                    progress.log(
                        "Phase 3 source-grounding attempt "
                        f"{attempt}/{attempts} for {topic_title!r} requires "
                        "correction: "
                        + "; ".join(parse_errors[:4]),
                        level="warning",
                    )
                    previous_grounding = [
                        copy.deepcopy(value)
                        for value in proposals.values()
                    ]
                    critic_feedback = {
                        "verdict": "provider_contract_rejected",
                        "confidence": 0.0,
                        "issues": parse_errors,
                        "rejected_concept_ids": sorted(unresolved),
                    }
                    continue

                proposals.update(parsed)
                if set(proposals) != concept_ids:
                    unresolved = concept_ids - set(proposals)
                    last_errors = [
                        "grounding provider did not yet cover every concept"
                    ]
                    continue

                full_payload = {
                    "topic": copy.deepcopy(topic),
                    "concepts": copy.deepcopy(concepts),
                    "source_blocks": copy.deepcopy(source_blocks),
                    "proposed_grounding": [
                        copy.deepcopy(proposals[concept_id])
                        for concept_id in sorted(concept_ids)
                    ],
                }
                review = critic(copy.deepcopy(full_payload))
                state = _review_state(
                    review,
                    concept_ids=concept_ids,
                )
                last_confidence = float(state["confidence"])
                if state["verified"]:
                    review_confidence = last_confidence
                    progress.log(
                        "Phase 3 source grounding independently verified "
                        f"{len(concept_ids)} concept(s) for {topic_title!r}"
                        + (
                            f" after {attempt} attempt(s)."
                            if attempt > 1
                            else "."
                        ),
                        level="success",
                    )
                    break

                unresolved = set(state["rejected"]) or set(concept_ids)
                last_errors = list(state["issues"]) or [
                    "critic verdict was "
                    + str(state.get("verdict") or "missing")
                ]
                accepted_count = len(concept_ids - unresolved)
                progress.log(
                    "Phase 3 source-grounding attempt "
                    f"{attempt}/{attempts} for {topic_title!r} accepted "
                    f"{accepted_count}/{len(concept_ids)} concept(s); "
                    f"{len(unresolved)} require correction"
                    + (
                        ": " + "; ".join(last_errors[:4])
                        if last_errors
                        else "."
                    ),
                    level="warning",
                )
                previous_grounding = [
                    copy.deepcopy(proposals[concept_id])
                    for concept_id in sorted(concept_ids)
                ]
                critic_feedback = copy.deepcopy(review)
                for concept_id in unresolved:
                    proposals.pop(concept_id, None)
            else:
                details = "; ".join(last_errors[:6])
                raise ValueError(
                    "Phase 3 concept grounding failed independent verification "
                    f"for topic {topic_title!r} after {attempts} attempt(s)"
                    + (
                        f" (critic confidence {last_confidence:.3f})"
                        if last_confidence
                        else ""
                    )
                    + (f": {details}" if details else "")
                    + ". Grounding was invalid or uncertain."
                )

            if set(proposals) != concept_ids:
                details = "; ".join(last_errors[:6])
                raise ValueError(
                    "Phase 3 concept grounding remained invalid or uncertain "
                    f"for topic {topic_title!r}: {details}"
                )
            _write_cached_proposals(
                cache_key,
                graph=graph,
                topic_id=topic_id,
                proposals=proposals,
                review_confidence=review_confidence,
            )

        _apply_proposals(
            out,
            proposals=proposals,
            index_by_id=index_by_id,
            candidates=candidates,
        )
        _apply_culmination_grounding(
            out,
            normal_indices=normal_indices,
            culmination_indices=culmination_indices,
            candidates=candidates,
        )
    return out


def _topology_cache_key(
    records: list[dict[str, Any]],
    kwargs: dict[str, Any],
    graph: dict[str, Any],
) -> str:
    return phase3._sha256_json(
        {
            "version": _GROUNDING_VERSION,
            "model": str(config.OPENAI_MODEL),
            "source_contract_hash": str(
                graph.get("source_contract_hash") or ""
            ),
            "records": _json_safe(records),
            "subject": str(kwargs.get("subject") or ""),
            "mmd_sha256": phase3._sha256_text(
                kwargs.get("mmd_text") or ""
            ),
            "meta": _json_safe(kwargs.get("meta") or {}),
            "source_sections": _json_safe(
                kwargs.get("source_sections") or []
            ),
            "source_topic_excerpts": _json_safe(
                kwargs.get("source_topic_excerpts") or []
            ),
            "method_anchors": _json_safe(
                kwargs.get("method_anchors") or []
            ),
        }
    )


def _prepare_topology_with_cache(
    original_prepare: Callable[..., list[dict[str, Any]]],
    records: list[dict[str, Any]],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> list[dict[str, Any]]:
    graph = phase3.active_graph()
    directory = _artifact_dir()
    if not isinstance(graph, dict) or directory is None:
        return original_prepare(records, *args, **kwargs)

    cache_key = _topology_cache_key(records, kwargs, graph)
    path = directory / _TOPOLOGY_CACHE_FILENAME
    cache = _read_json(path)
    if (
        cache.get("version") == _GROUNDING_VERSION
        and cache.get("cache_key") == cache_key
        and isinstance(cache.get("records"), list)
        and cache.get("records_sha256")
        == phase3._sha256_json(cache.get("records"))
    ):
        progress.log(
            "Reused the validated final concept topology, including specific "
            "learner analysis, from the current source contract; no learner-"
            "analysis model call was repeated.",
            level="success",
        )
        return copy.deepcopy(cache["records"])

    prepared = original_prepare(records, *args, **kwargs)
    _write_json(
        path,
        {
            "version": _GROUNDING_VERSION,
            "cache_key": cache_key,
            "created_at": time.time(),
            "source_contract_hash": str(
                graph.get("source_contract_hash") or ""
            ),
            "records_sha256": phase3._sha256_json(prepared),
            "records": copy.deepcopy(prepared),
        },
    )
    progress.log(
        "Cached the validated final concept topology before Type allocation so "
        "a later resume can reuse learner analysis and topology repair.",
        level="success",
    )
    return prepared


def install(generation: Any | None = None) -> None:
    if (
        getattr(phase3, "_PHASE31_GROUNDING_CONTRACT_VERSION", 0)
        >= _CONTRACT_VERSION
    ):
        return
    if generation is None:
        from . import generation as generation

    phase3._PHASE31_ORIGINAL_GROUND_CONCEPTS = phase3.ground_concepts
    phase3.ground_concepts = ground_concepts

    original_prepare = getattr(
        generation,
        "_TOPOLOGY_CONTRACT_ORIGINAL_PREPARE",
        None,
    )
    if callable(original_prepare):
        generation._PHASE31_ORIGINAL_TOPOLOGY_PREPARE = original_prepare

        @wraps(original_prepare)
        def cached_prepare(records, *args, **kwargs):
            return _prepare_topology_with_cache(
                original_prepare,
                records,
                args,
                kwargs,
            )

        generation._TOPOLOGY_CONTRACT_ORIGINAL_PREPARE = cached_prepare

    phase3._PHASE31_GROUNDING_CONTRACT_VERSION = _CONTRACT_VERSION

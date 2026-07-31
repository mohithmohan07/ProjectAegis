"""Phase 3.1 concept-to-source grounding recovery and resume cache.

The live RNE acceptance run proved the source graph and concept topology were
valid, then exposed a contract mismatch at Type allocation: concept grounding
sent the complete public ``concept_details`` cell to an evidence critic. That
cell includes generated mastery and learner-analysis prose which is deliberately
not copied from the textbook, so a strict source critic could correctly reject
an otherwise valid concept-to-block mapping forever.

This contract narrows grounding to the source-facing Description claim, derives
culmination grounding from already-verified normal concepts, retries only
critic-rejected concept IDs, persists each independently accepted concept, and
caches the validated pre-allocation topology so a resume does not repeat
grounding or learner-analysis calls after a later allocation failure.
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
from . import early_semantic_gate as early_gate
from . import progress
from . import semantic_confidence_policy as confidence_policy
from .semantic_recovery import (
    HumanDecisionRequired,
    ProviderResponseContractError,
)

_CONTRACT_VERSION = 2
_GROUNDING_VERSION = "phase3.1-source-claim-grounding-1"
_CONCEPT_GROUNDING_CACHE_VERSION = "phase3.1-per-concept-grounding-3"
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
                "current_topic_id": str(
                    records[index].get("_semantic_topic_id") or ""
                ),
                "current_topic_title": str(records[index].get("topic") or ""),
                "origin_concept_id": str(
                    records[index].get("_phase32_origin_concept_id") or ""
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
        "requested unresolved concepts. If human_resolutions is supplied, treat "
        "its selected verified evidence or custom instruction as binding, then "
        "return the ordinary proposal for independent criticism; the human "
        "direction is not verification."
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
        f"{confidence_policy.threshold_text()}, and no issues. Do not rewrite "
        "proposals."
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
        if not confidence_policy.accepts(confidence):
            errors.append(
                f"{concept_id} confidence {confidence:.3f} is below "
                f"{confidence_policy.threshold_text()}"
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
    confidence_gate: confidence_policy.ConfidenceGate = (
        confidence_policy.ConfidenceGate.SEMANTIC
    ),
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
    accepted_confidence = confidence_policy.accepts(
        confidence,
        confidence_gate,
    )
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
            # A contradictory ID is never a durable partial acceptance.
            accepted -= overlap
            rejected |= overlap
        if omitted:
            issues.append(
                "critic omitted concept ID(s): "
                + ", ".join(sorted(omitted))
            )
            rejected |= omitted
        issue_named = {
            concept_id
            for concept_id in accepted
            if any(concept_id in issue for issue in issues)
        }
        if issue_named:
            # An ID called out by the critic's problem list is not an
            # unaffected sibling, even if the critic accidentally also placed
            # it in accepted_concept_ids.
            accepted -= issue_named
            rejected |= issue_named
        accepted -= rejected
    elif (
        verdict == "verified"
        and accepted_confidence
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
        and accepted_confidence
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


def _source_block_directory(
    source_blocks: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Describe exactly the provider-visible block directory.

    Reuse is intentionally invalidated by any ID, binding, ordering, kind,
    subtopic, or provider-visible text change. The cache never trusts a block ID
    alone.
    """
    return [
        {
            "block_id": str(row.get("block_id") or ""),
            "kind": str(row.get("kind") or ""),
            "subtopic_id": str(row.get("subtopic_id") or ""),
            "text_sha256": phase3._sha256_text(row.get("text") or ""),
        }
        for row in source_blocks
    ]


def _concept_cache_contexts(
    *,
    graph: dict[str, Any],
    topic_id: str,
    concepts: list[dict[str, Any]],
    source_blocks: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    topic = next(
        (
            row
            for row in graph.get("topics") or []
            if isinstance(row, dict)
            and str(row.get("topic_id") or "") == topic_id
        ),
        {"topic_id": topic_id},
    )
    block_directory = _source_block_directory(source_blocks)
    block_directory_sha256 = phase3._sha256_json(block_directory)
    block_bindings_by_id = {
        row["block_id"]: row
        for row in block_directory
        if row["block_id"]
    }
    concept_bases: list[dict[str, Any]] = []
    base_hashes: list[str] = []
    for concept in concepts:
        base = {
            "concept_title": phase3._clean_public_text(
                concept.get("concept_title") or ""
            ),
            "parent_concept": phase3._clean_public_text(
                concept.get("parent_concept") or ""
            ),
            "source_claim": phase3._clean_public_text(
                concept.get("source_claim") or ""
            ),
        }
        concept_bases.append(base)
        base_hashes.append(phase3._sha256_json(base))
    base_counts: dict[str, int] = {}
    for value in base_hashes:
        base_counts[value] = base_counts.get(value, 0) + 1
    seen_duplicates: dict[str, int] = {}
    contexts: dict[str, dict[str, Any]] = {}
    for concept, base, base_hash in zip(
        concepts,
        concept_bases,
        base_hashes,
    ):
        if base_counts[base_hash] > 1:
            duplicate_ordinal = seen_duplicates.get(base_hash, 0) + 1
            seen_duplicates[base_hash] = duplicate_ordinal
            base = {
                **base,
                "duplicate_ordinal": duplicate_ordinal,
            }
        identity = {
            "version": _CONCEPT_GROUNDING_CACHE_VERSION,
            "model": str(config.OPENAI_MODEL),
            "semantic_confidence_policy": _json_safe(
                confidence_policy.cache_identity()
            ),
            "source": early_gate.source_identity(graph),
            "source_contract_hash": str(
                graph.get("source_contract_hash") or ""
            ),
            "semantic_context_hash": str(
                graph.get("semantic_context_hash") or ""
            ),
            "source_sha256": str(graph.get("source_sha256") or ""),
            "pdf_sha256": str(
                (graph.get("vision_evidence") or {}).get("pdf_sha256")
                or ""
            ),
            "topic": _json_safe(topic),
            "concept": base,
            "block_directory_sha256": block_directory_sha256,
        }
        concept_id = str(concept.get("concept_id") or "")
        contexts[concept_id] = {
            "cache_key": phase3._sha256_json(identity),
            "identity": identity,
            "block_bindings_by_id": block_bindings_by_id,
        }
    return contexts


def _read_cached_concept_proposals(
    contexts: dict[str, dict[str, Any]],
    *,
    allowed_blocks: set[str],
) -> dict[str, dict[str, Any]]:
    directory = _artifact_dir()
    if directory is None:
        return {}
    cache = _read_json(directory / _GROUNDING_CACHE_FILENAME)
    if cache.get("version") != _CONCEPT_GROUNDING_CACHE_VERSION:
        return {}
    entries = cache.get("entries")
    if not isinstance(entries, dict):
        return {}

    cached: dict[str, dict[str, Any]] = {}
    for concept_id, context in contexts.items():
        entry = entries.get(context["cache_key"])
        if (
            not isinstance(entry, dict)
            or entry.get("status") != "accepted"
            or entry.get("identity") != context["identity"]
            or not confidence_policy.accepts(
                entry.get("review_confidence")
            )
            or early_gate.confidence_band(
                entry.get("review_confidence")
            ) != "accepted"
        ):
            continue
        proposal = entry.get("proposal")
        if not isinstance(proposal, dict):
            continue
        block_ids = [
            str(value)
            for value in proposal.get("source_block_ids") or []
            if str(value)
        ]
        bindings_by_id = context["block_bindings_by_id"]
        if any(block_id not in bindings_by_id for block_id in block_ids):
            continue
        current_bindings = [
            bindings_by_id[block_id]
            for block_id in block_ids
        ]
        if entry.get("source_block_bindings") != current_bindings:
            continue
        rebound = {
            **copy.deepcopy(proposal),
            "concept_id": concept_id,
        }
        parsed, errors = _parse_proposals(
            {"concepts": [rebound]},
            expected_ids={concept_id},
            allowed_blocks=allowed_blocks,
        )
        if not errors:
            cached[concept_id] = parsed[concept_id]
    return cached


def _write_cached_concept_proposals(
    contexts: dict[str, dict[str, Any]],
    *,
    proposals: dict[str, dict[str, Any]],
    review_confidence: float,
) -> None:
    directory = _artifact_dir()
    if directory is None or not proposals:
        return
    path = directory / _GROUNDING_CACHE_FILENAME
    cache = _read_json(path)
    if cache.get("version") != _CONCEPT_GROUNDING_CACHE_VERSION:
        cache = {
            "version": _CONCEPT_GROUNDING_CACHE_VERSION,
            "entries": {},
        }
    entries = cache.get("entries")
    if not isinstance(entries, dict):
        entries = {}
        cache["entries"] = entries
    for concept_id, proposal in proposals.items():
        context = contexts[concept_id]
        stored_proposal = copy.deepcopy(proposal)
        stored_proposal.pop("concept_id", None)
        block_ids = [
            str(value)
            for value in stored_proposal.get("source_block_ids") or []
            if str(value)
        ]
        bindings_by_id = context["block_bindings_by_id"]
        if not block_ids or any(
            block_id not in bindings_by_id
            for block_id in block_ids
        ):
            continue
        entries[context["cache_key"]] = {
            "status": "accepted",
            "created_at": time.time(),
            "identity": copy.deepcopy(context["identity"]),
            "review_confidence": float(review_confidence),
            "proposal": stored_proposal,
            "source_block_bindings": [
                copy.deepcopy(bindings_by_id[block_id])
                for block_id in block_ids
            ],
        }
    _write_json(path, cache)


def _grounding_candidates(
    *,
    graph: dict[str, Any],
    concept: dict[str, Any],
    source_blocks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for block in source_blocks[:100]:
        block_id = str(block.get("block_id") or "")
        if not block_id:
            continue
        candidate_seed = {
            "action": "use_verified_evidence",
            "block_ids": [block_id],
            "text_sha256": phase3._sha256_text(block.get("text") or ""),
        }
        candidates.append(
            {
                "target_id": early_gate.candidate_target_id(
                    phase="3.1",
                    action="evidence",
                    graph=graph,
                    unit=concept,
                    candidate=candidate_seed,
                ),
                "concept_id": "",
                "title": f"Use verified evidence {block_id}",
                "topic": "",
                "coverage": str(block.get("text") or "")[:8000],
                "gap": "",
                "action": "use_verified_evidence",
                "source_block_ids": [block_id],
            }
        )
    current_topic_id = str(concept.get("current_topic_id") or "")
    topology_seeds: list[dict[str, Any]] = [
        {
            "action": "refine",
            "target_topic_id": current_topic_id,
            "title": "Refine the unsupported source claim",
            "topic": str(concept.get("current_topic_title") or ""),
            "coverage": str(concept.get("source_claim") or ""),
            "gap": (
                "Return this concept to topology and narrow the unsupported "
                "clause."
            ),
        },
        {
            "action": "split",
            "target_topic_id": "",
            "title": "Split distinct source-supported claims",
            "topic": "Across verified source topics",
            "coverage": str(concept.get("source_claim") or ""),
            "gap": (
                "Return this concept to topology and separate durable claims."
            ),
        },
        {
            "action": "retire",
            "target_topic_id": "",
            "title": "Retire an unsupported or fully duplicated claim",
            "topic": str(concept.get("current_topic_title") or ""),
            "coverage": str(concept.get("source_claim") or ""),
            "gap": (
                "Destructive retirement still requires the stricter "
                "independent gate."
            ),
        },
    ]
    for topic in graph.get("topics") or []:
        if not isinstance(topic, dict):
            continue
        topic_id = str(topic.get("topic_id") or "")
        if not topic_id or topic_id == current_topic_id:
            continue
        topology_seeds.append(
            {
                "action": "move",
                "target_topic_id": topic_id,
                "title": "Move the complete claim to a different source topic",
                "topic": str(topic.get("title") or topic_id),
                "coverage": str(concept.get("source_claim") or ""),
                "gap": (
                    "Move without changing the claim; independent review "
                    "remains mandatory."
                ),
            }
        )
    for seed in topology_seeds:
        candidates.append(
            {
                "target_id": early_gate.candidate_target_id(
                    phase="3.1",
                    action=str(seed["action"]),
                    graph=graph,
                    unit=concept,
                    candidate=seed,
                ),
                "concept_id": str(concept.get("origin_concept_id") or ""),
                **seed,
                "source_block_ids": [],
            }
        )
    return candidates


def _grounding_identity(
    *,
    graph: dict[str, Any],
    topic: dict[str, Any],
    concept: dict[str, Any],
    candidates: list[dict[str, Any]],
    source_blocks: list[dict[str, Any]],
) -> dict[str, str]:
    return early_gate.context_identity(
        phase="3.1",
        kind="phase31_source_grounding_semantic_conflict",
        graph=graph,
        unit=concept,
        evidence_fingerprint={
            "topic_id": str(topic.get("topic_id") or ""),
            "blocks": [
                {
                    "block_id": str(row.get("block_id") or ""),
                    "text_sha256": phase3._sha256_text(row.get("text") or ""),
                }
                for row in source_blocks
            ],
        },
        candidates=candidates,
    )


def _grounding_pending_decision(
    *,
    identity: dict[str, str],
    graph: dict[str, Any],
    topic: dict[str, Any],
    concept: dict[str, Any],
    candidates: list[dict[str, Any]],
    source_blocks: list[dict[str, Any]],
    issues: list[str],
    rejected_ids: list[str],
    resolution: dict[str, Any] | None = None,
    proposal: Any = None,
    diagnostic_source: str = "critic",
) -> dict[str, Any]:
    clean_issues = [
        re.sub(r"\s+", " ", str(value or "")).strip()[:1200]
        for value in issues
        if str(value or "").strip()
    ] or ["The selected evidence did not independently verify the source claim."]
    # Free-form critic prose is not a structured recommendation.  A sentence
    # such as “BLK-1 does not support this; use BLK-2” must never recommend the
    # first source-order block merely because it was mentioned.
    recommended = None
    packet_identity = early_gate.followup_identity(
        identity,
        resolution=resolution,
        issues=clean_issues,
        proposal=proposal,
    )
    public_candidates = [
        {
            key: str(row.get(key) or "")
            for key in (
                "target_id", "concept_id", "title", "topic", "coverage", "gap"
            )
        }
        for row in candidates
    ]
    options: list[dict[str, Any]] = []
    if recommended is not None:
        options.append(
            {
                "choice": "accept_recommended",
                "label": "Use the recommended verified evidence",
                "recommended": True,
                "target_id": str(recommended.get("target_id") or ""),
            }
        )
    options.extend(
        [
            {
                "choice": "select_candidate",
                "label": "Select evidence or a topology repair",
                "recommended": recommended is None,
            },
            {
                "choice": "replace_source",
                "label": "Correct or replace the source",
                "recommended": False,
            },
        ]
    )
    if resolution is None:
        options.append(
            {
                "choice": "custom_instruction",
                "label": "Give a custom instruction",
                "recommended": False,
            }
        )
    confidence = next(
        (
            float(match.group(1))
            for issue in clean_issues
            for match in [re.search(r"confidence\s+([01](?:\.\d+)?)", issue)]
            if match
        ),
        None,
    )
    review_band = (
        confidence is not None
        and early_gate.confidence_band(confidence) == "human_review"
    )
    return early_gate.plain_json(
        {
            **packet_identity,
            "kind": "phase31_source_grounding_semantic_conflict",
            "phase": "3.1",
            "conflict": "; ".join(clean_issues[:6]),
            "diagnosis": (
                (
                    "The grounding provider placed this result in the "
                    "0.900–0.919 human-review band before independent review."
                    if diagnostic_source == "provider"
                    else "The independent grounding critic placed this result "
                    "in the 0.900–0.919 human-review band."
                )
                if review_band
                else (
                    "The grounding provider returned a genuine semantic "
                    "source-support discrepancy before independent review."
                    if diagnostic_source == "provider"
                    else "The first independent grounding review found a "
                    "genuine source-support discrepancy."
                )
            ),
            "decision_question": (
                "Should Aegis use different verified evidence, or return the "
                "claim to topology to refine, move, split, or retire it before "
                "independent review?"
            ),
            "item": {
                "unit_id": str(concept.get("concept_id") or ""),
                "type_id": "",
                "type_title": str(concept.get("concept_title") or ""),
                "qids": [],
                "questions": [str(concept.get("source_claim") or "")],
                "topic": str(topic.get("title") or ""),
            },
            "candidates": public_candidates,
            "evidence": [
                {
                    "page": str(
                        row.get("page_number")
                        or row.get("pdf_page")
                        or row.get("page")
                        or ""
                    ),
                    "label": str(row.get("block_id") or ""),
                    "text": str(row.get("text") or "")[:8000],
                }
                for row in source_blocks[:24]
            ],
            "deferred_assignment_unit_ids": [
                value
                for value in rejected_ids
                if value != str(concept.get("concept_id") or "")
            ],
            "options": options,
        }
    )


def _grounding_parse_errors_are_mechanical(errors: list[str]) -> bool:
    semantic_fragments = (
        " confidence ",
        "requires human review",
    )
    meaningful = [
        value
        for value in errors
        if not value.startswith("missing or invalid concept ID(s):")
    ]
    if not meaningful:
        return True
    if any(fragment in value for value in meaningful for fragment in semantic_fragments):
        return False
    prefixes = (
        "grounding provider returned no concepts array",
        "grounding provider returned a non-object row",
        "unknown concept ID ",
        "duplicate concept ID ",
        "CONCEPT-GROUND-",
    )
    return all(value.startswith(prefixes) for value in meaningful)


def _grounding_resolution_for(
    *,
    identity: dict[str, str],
    concept: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> dict[str, Any] | None:
    return early_gate.resolution_for(
        identity=identity,
        kind="phase31_source_grounding_semantic_conflict",
        phase="3.1",
        unit_id=str(concept.get("concept_id") or ""),
        candidates=candidates,
    )


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

        cache_contexts = _concept_cache_contexts(
            graph=graph,
            topic_id=topic_id,
            concepts=concepts,
            source_blocks=source_blocks,
        )
        proposals = _read_cached_concept_proposals(
            cache_contexts,
            allowed_blocks=allowed_blocks,
        )
        cached_count = len(proposals)
        if cached_count:
            progress.log(
                "Reused API-verified Phase 3 concept grounding for "
                f"{topic_title!r} ({cached_count}/{len(concept_ids)} "
                "independently accepted concept(s)); only uncached or changed "
                "source claims require model review.",
                level="success",
            )
        unresolved = concept_ids - set(proposals)
        if unresolved:
            concept_by_id = {
                str(row.get("concept_id") or ""): row for row in concepts
            }
            gate_candidates: dict[str, list[dict[str, Any]]] = {}
            gate_identities: dict[str, dict[str, str]] = {}
            resolutions: dict[str, dict[str, Any]] = {}
            deferred: set[str] = set()
            for concept_id in sorted(unresolved):
                concept = concept_by_id[concept_id]
                candidate_rows = _grounding_candidates(
                    graph=graph,
                    concept=concept,
                    source_blocks=source_blocks,
                )
                identity = _grounding_identity(
                    graph=graph,
                    topic=topic,
                    concept=concept,
                    candidates=candidate_rows,
                    source_blocks=source_blocks,
                )
                resolution = _grounding_resolution_for(
                    identity=identity,
                    concept=concept,
                    candidates=candidate_rows,
                )
                gate_candidates[concept_id] = candidate_rows
                gate_identities[concept_id] = identity
                if resolution is not None:
                    resolutions[concept_id] = resolution
                    deferred.update(
                        resolution.get("deferred_assignment_unit_ids") or []
                    )

            if any(
                resolution.get("choice") == "replace_source"
                for resolution in resolutions.values()
            ):
                raise ValueError(
                    "Source replacement is required before Phase 3.1 can "
                    "continue. Replace or correct the uploaded source and "
                    "convert it again; no model request was started."
                )

            for concept_id, resolution in resolutions.items():
                selected = resolution.get("selected_candidate")
                action = (
                    str(selected.get("action") or "")
                    if isinstance(selected, dict)
                    else ""
                )
                if resolution.get("choice") == "custom_instruction":
                    instruction = str(resolution.get("instruction") or "")
                    match = re.search(
                        r"\b(refine|split|move|retire)\b",
                        instruction,
                        re.IGNORECASE,
                    )
                    if match:
                        action = match.group(1).casefold()
                if action not in {"refine", "split", "move", "retire"}:
                    continue
                target = (
                    str(selected.get("target_topic_id") or "")
                    if isinstance(selected, dict)
                    else ""
                )
                feedback = resolution.get("critic_feedback")
                conflict = (
                    str(feedback.get("conflict") or "")
                    if isinstance(feedback, dict)
                    else ""
                )
                raise early_gate.TopologyRepairRequired(
                    "failed exact source-block grounding before freeze: "
                    f"{concept_id} HUMAN DIRECTION requires topology action "
                    f"{action}"
                    + (f" into {target}" if target else "")
                    + (f". Prior critic: {conflict}" if conflict else ""),
                    decision_id=str(resolution.get("decision_id") or ""),
                )

            # A critic can reject several IDs at once. Ask for the remaining
            # bounded choices before starting the single directed retry pair.
            undecided_deferred = [
                concept_id
                for concept_id in sorted(unresolved & deferred)
                if concept_id not in resolutions
            ]
            if undecided_deferred:
                concept_id = undecided_deferred[0]
                raise HumanDecisionRequired(
                    _grounding_pending_decision(
                        identity=gate_identities[concept_id],
                        graph=graph,
                        topic=topic,
                        concept=concept_by_id[concept_id],
                        candidates=gate_candidates[concept_id],
                        source_blocks=source_blocks,
                        issues=[
                            "This concept was also rejected by the first "
                            "independent grounding review and needs your "
                            "bounded evidence decision."
                        ],
                        rejected_ids=undecided_deferred,
                    )
                )

            if unresolved:
                requested = [
                    row for row in concepts
                    if str(row.get("concept_id") or "") in unresolved
                ]
                # A saved semantic direction receives one provider request and
                # one independent critic request. Fresh mechanical schema/ID
                # defects may receive one correction; semantic defects never do.
                max_provider_attempts = 1 if resolutions else 2
                response: dict[str, Any] | None = None
                parsed: dict[str, dict[str, Any]] = {}
                parse_errors: list[str] = []
                for attempt in range(1, max_provider_attempts + 1):
                    attempt_payload = {
                        "topic": copy.deepcopy(topic),
                        "concepts": copy.deepcopy(requested),
                        "source_blocks": copy.deepcopy(source_blocks),
                        "attempt": attempt,
                        "max_attempts": max_provider_attempts,
                        "previous_grounding": [],
                        "critic_feedback": (
                            {
                                "verdict": "provider_contract_rejected",
                                "issues": copy.deepcopy(parse_errors),
                                "rejected_concept_ids": sorted(unresolved),
                            }
                            if parse_errors
                            else {}
                        ),
                    }
                    if resolutions:
                        attempt_payload["human_resolutions"] = [
                            copy.deepcopy(resolutions[concept_id])
                            for concept_id in sorted(resolutions)
                        ]
                    response = provider(copy.deepcopy(attempt_payload))
                    parsed, parse_errors = _parse_proposals(
                        response,
                        expected_ids=set(unresolved),
                        allowed_blocks=allowed_blocks,
                    )
                    review_band_ids = [
                        concept_id
                        for concept_id, proposal in parsed.items()
                        if early_gate.confidence_band(
                            proposal.get("confidence")
                        ) == "human_review"
                    ]
                    if not parse_errors and review_band_ids:
                        parse_errors = [
                            f"{concept_id} confidence "
                            f"{float(parsed[concept_id]['confidence']):.3f} is "
                            "in the 0.900–0.919 human-review band"
                            for concept_id in review_band_ids
                        ]
                    if not parse_errors:
                        break
                    if (
                        resolutions
                        or not _grounding_parse_errors_are_mechanical(parse_errors)
                    ):
                        concept_id = next(
                            (
                                value for value in sorted(unresolved)
                                if any(value in error for error in parse_errors)
                            ),
                            sorted(unresolved)[0],
                        )
                        raise HumanDecisionRequired(
                            _grounding_pending_decision(
                                identity=gate_identities[concept_id],
                                graph=graph,
                                topic=topic,
                                concept=concept_by_id[concept_id],
                                candidates=gate_candidates[concept_id],
                                source_blocks=source_blocks,
                                issues=parse_errors,
                                rejected_ids=sorted(unresolved),
                                resolution=resolutions.get(concept_id),
                                proposal=response,
                                diagnostic_source="provider",
                            )
                        )
                    if attempt < max_provider_attempts:
                        progress.log(
                            "Phase 3.1 grounding response had a mechanical "
                            "schema/opaque-ID defect; making its one bounded "
                            "contract correction.",
                            level="warning",
                        )
                        continue
                    details = "; ".join(parse_errors[:6])
                    raise ProviderResponseContractError(
                        "Phase 3.1 grounding provider failed its response "
                        "contract after one bounded correction"
                        + (f": {details}" if details else "")
                    )

                # A human-selected evidence block is a direction to the mapper,
                # never an approval. Enforce the direction before the critic.
                directed_issues: list[str] = []
                for concept_id, resolution in resolutions.items():
                    selected = resolution.get("selected_candidate")
                    if not isinstance(selected, dict):
                        continue
                    required_blocks = {
                        str(value)
                        for value in selected.get("source_block_ids") or []
                        if str(value)
                    }
                    proposed_blocks = set(
                        (parsed.get(concept_id) or {}).get("source_block_ids") or []
                    )
                    if not required_blocks.issubset(proposed_blocks):
                        directed_issues.append(
                            f"{concept_id} omitted the human-selected verified "
                            "evidence block(s): " + ", ".join(sorted(required_blocks))
                        )
                if directed_issues:
                    concept_id = next(
                        (
                            value for value in sorted(unresolved)
                            if any(value in issue for issue in directed_issues)
                        ),
                        sorted(unresolved)[0],
                    )
                    raise HumanDecisionRequired(
                        _grounding_pending_decision(
                            identity=gate_identities[concept_id],
                            graph=graph,
                            topic=topic,
                            concept=concept_by_id[concept_id],
                            candidates=gate_candidates[concept_id],
                            source_blocks=source_blocks,
                            issues=directed_issues,
                            rejected_ids=sorted(unresolved),
                            resolution=resolutions.get(concept_id),
                            proposal=response,
                            diagnostic_source="provider",
                        )
                    )

                review_payload = {
                    "topic": copy.deepcopy(topic),
                    "concepts": copy.deepcopy(requested),
                    "source_blocks": copy.deepcopy(source_blocks),
                    "proposed_grounding": [
                        copy.deepcopy(parsed[concept_id])
                        for concept_id in sorted(unresolved)
                    ],
                }
                if resolutions:
                    review_payload["human_resolutions"] = [
                        copy.deepcopy(resolutions[concept_id])
                        for concept_id in sorted(resolutions)
                    ]
                review = critic(copy.deepcopy(review_payload))
                state = _review_state(review, concept_ids=set(unresolved))
                accepted_ids = (
                    set(state["accepted"])
                    if confidence_policy.accepts(state["confidence"])
                    and early_gate.confidence_band(state["confidence"])
                    == "accepted"
                    else set()
                )
                accepted_proposals = {
                    concept_id: parsed[concept_id]
                    for concept_id in accepted_ids
                    if concept_id in parsed
                }
                # Persist each independently accepted concept before pausing.
                # Resume then pays only for still-rejected source claims.
                _write_cached_concept_proposals(
                    cache_contexts,
                    proposals=accepted_proposals,
                    review_confidence=float(state["confidence"]),
                )
                proposals.update(accepted_proposals)
                critic_review_band = (
                    early_gate.confidence_band(state["confidence"])
                    == "human_review"
                )
                if not state["verified"] or critic_review_band:
                    rejected = set(state["rejected"]) or set(unresolved)
                    issues = list(state["issues"]) or [
                        "critic verdict was "
                        + str(state.get("verdict") or "missing")
                    ]
                    confidence = float(state["confidence"])
                    if early_gate.confidence_band(confidence) == "human_review":
                        issues.insert(
                            0,
                            f"independent critic confidence {confidence:.3f} "
                            "is in the 0.900–0.919 human-review band",
                        )
                    concept_id = sorted(rejected)[0]
                    progress.log(
                        "Phase 3.1 stopped after the first genuine semantic "
                        "grounding disagreement; accepted concept IDs were "
                        "cached and no semantic retry was started.",
                        level="warning",
                    )
                    raise HumanDecisionRequired(
                        _grounding_pending_decision(
                            identity=gate_identities[concept_id],
                            graph=graph,
                            topic=topic,
                            concept=concept_by_id[concept_id],
                            candidates=gate_candidates[concept_id],
                            source_blocks=source_blocks,
                            issues=issues,
                            rejected_ids=sorted(rejected),
                            resolution=resolutions.get(concept_id),
                            proposal=parsed,
                        )
                    )
                progress.log(
                    "Phase 3 source grounding independently verified "
                    f"{len(unresolved)} concept(s) for {topic_title!r}.",
                    level="success",
                )

            if set(proposals) != concept_ids:
                raise ProviderResponseContractError(
                    "Phase 3.1 grounding ended without every concept after "
                    "bounded cache and decision resolution"
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
            "semantic_confidence_policy": confidence_policy.cache_identity(),
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

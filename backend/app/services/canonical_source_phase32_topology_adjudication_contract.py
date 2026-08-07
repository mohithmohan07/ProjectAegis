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
from . import early_semantic_gate as early_gate
from . import grounding_certificate
from . import placement_policy
from . import progress
from . import semantic_confidence_policy as confidence_policy
from .semantic_recovery import (
    HumanDecisionRequired,
    ProviderResponseContractError,
)

_CONTRACT_VERSION = 3
_TOPOLOGY_VERSION = "phase3.2-certified-placement-topology-3"
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
    "_placement_contract",
})

PLACEMENT_PROVIDER_INSTRUCTIONS = (
    "\nPLACEMENT RELATIONSHIP CONTRACT: you do not have final ownership "
    "authority. For every returned segment classify topic_relationships. "
    "Deterministic code computes the owner as the latest teaching-ranked "
    "topic whose knowledge, method or interpretive framework is genuinely "
    "necessary to understand or perform the claim. Use CORE_TEACHING for a "
    "topic that directly teaches it; REQUIRED_PREREQUISITE for necessary "
    "earlier knowledge; REQUIRED_LATER_METHOD for a necessary later method; "
    "RETROSPECTIVE_REFERENCE for a later back-reference; "
    "SUBSTANTIVE_LATER_ILLUSTRATION for a separately taught later illustration "
    "or consequence; and INCIDENTAL_MENTION for a passing name-check. "
    "Set necessity=true only for CORE_TEACHING, REQUIRED_PREREQUISITE and "
    "REQUIRED_LATER_METHOD, and cite exact supplied block IDs for every "
    "relationship. Shared wording, "
    "physical location, section numbering, chronology and optional methods are "
    "not necessity. Split only two or more independently teachable ideas "
    "grounded in the SAME blocks: split is for dividing one claim, never for "
    "handing a later topic its own material. When a later topic merely refers "
    "back to or illustrates this concept, keep this concept exactly as it is "
    "and let that topic carry its own separate concept grounded in its own "
    "blocks. Splitting across topics rewrites a claim that other stages have "
    "already certified, and invalidates that certification. "
    "An image, figure, illustration or Activity sits where the "
    "page layout put it; page-fill, plate sections and two-column flow all move "
    "printed material away from what it is about. Classify such an item by the "
    "topic whose content it depicts, never by the topic it was printed under, "
    "and never let a later-printed illustration pull a concept out of the topic "
    "that teaches it. For a non-split result preserve every supplied protected "
    "source item. For a split, partition protected_source_items across children "
    "without loss or duplication. Do not split an irreducible relationship into "
    "disconnected facts."
    + placement_policy.PLACEMENT_RULES
)

PLACEMENT_CRITIC_INSTRUCTIONS = (
    "\nPLACEMENT RELATIONSHIP AUDIT: independently review every supplied "
    "relationship_id. Verify that its exact cited block supports the stated "
    "claim/topic relationship, that the necessity assertion is genuine, and "
    "that prerequisite/later-method/reference direction is correct. Mere mention, "
    "shared terminology, physical position, chronology or an optional method "
    "must be rejected as necessity. Return every relationship ID exactly once. "
    "necessity_supported is judged ONLY for CORE_TEACHING, "
    "REQUIRED_PREREQUISITE and REQUIRED_LATER_METHOD. "
    "RETROSPECTIVE_REFERENCE, SUBSTANTIVE_LATER_ILLUSTRATION and "
    "INCIDENTAL_MENTION are defined to carry necessity=false: for those, report "
    "necessity_supported=true when the relationship is correctly classified as "
    "non-necessary, and reject only if the edge is genuinely necessary and was "
    "mislabelled. A later topic that merely refers back to, or illustrates, "
    "material taught earlier does NOT own it -- the concept stays in the topic "
    "that teaches it, and the later topic gains its own concept about the "
    "illustration. Never reject a concept because a correctly-labelled "
    "illustration or back-reference is not necessary; that is the expected "
    "value, not a defect. "
    "A back-reference is never grounds for a split: the two concepts ground in "
    "different source blocks, so there is one claim in each and nothing to "
    "divide. Reject a split whose children ground in different topics' blocks "
    "rather than partitioning one claim. "
    "A relationship is accepted only when evidence_supported, necessity_supported "
    "and direction_supported are all true. For split concepts also reject the "
    "concept unless the children collectively preserve the complete parent claim, "
    "every irreducible relationship and every protected source item."
    + placement_policy.PLACEMENT_RULES
)

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


def _critic_output_token_budget(
    concept_count: int,
    relationship_count: int,
) -> int:
    """Reserve enough output for one exhaustive verdict per relationship."""

    required = max(
        6000,
        int(concept_count) * 500 + int(relationship_count) * 240,
    )
    return min(max(1, int(config.OPENAI_MAX_OUTPUT_TOKENS)), required)


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


_PROTECTED_ID_RE = re.compile(
    r"\b(?:QINV-[0-9]+(?:\.[0-9]+)?|FIG-[0-9]+)\b",
    re.IGNORECASE,
)
_IMAGE_URL_RE = re.compile(r"https?://[^\s\\\]\)\}\"']+", re.IGNORECASE)
_KATEX_RE = re.compile(
    r"\[katex\]\s*(?P<body>.*?)\s*\[/katex\]",
    re.IGNORECASE | re.DOTALL,
)


def _protected_source_items(record: dict[str, Any]) -> list[str]:
    """Collect source identities that a topology split may not lose.

    Phase 3.2 runs before Type mining, so many concepts carry no task identities
    yet.  When a checkpoint/recovery row does carry QIDs, Figures, images or
    KaTeX, however, they become transactional split inventory here.
    """

    items: list[str] = []
    for field in (
        "protected_source_items",
        "source_question_ids",
        "_activity_hub_qids",
        "figure_ids",
        "image_urls",
    ):
        raw = record.get(field)
        values = raw if isinstance(raw, (list, tuple, set)) else [raw]
        items.extend(str(value).strip() for value in values if str(value or "").strip())
    rendered = json.dumps(record, ensure_ascii=False, sort_keys=True, default=str)
    items.extend(match.group(0).upper() for match in _PROTECTED_ID_RE.finditer(rendered))
    items.extend(
        "IMAGE:" + match.group(0).rstrip(".,;:!?")
        for match in _IMAGE_URL_RE.finditer(rendered)
    )
    items.extend(
        "KATEX:" + phase3._sha256_text(match.group("body").strip())
        for match in _KATEX_RE.finditer(rendered)
        if match.group("body").strip()
    )
    return list(dict.fromkeys(items))


_PROTECTED_SOURCE_LIST_FIELDS = (
    "source_question_ids",
    "_activity_hub_qids",
    "figure_ids",
    "image_urls",
)


def _protected_field_aliases(field: str, value: object) -> set[str]:
    text = str(value or "").strip()
    if not text:
        return set()
    aliases = {text, text.upper()}
    if field == "image_urls":
        aliases.add("IMAGE:" + text)
    return aliases


def _project_split_source_inventory(
    base: dict[str, Any],
    out: dict[str, Any],
    *,
    protected_items: list[str],
) -> None:
    """Project parent-owned source identities onto exactly one split child.

    A split record starts as a copy of its parent for ordinary metadata, but
    source-owned identities are not ordinary metadata: copying them would put
    every QID, Figure and image on every child.  The independently reviewed
    segment partition is therefore authoritative for these fields.
    """

    selected = {str(item) for item in protected_items if str(item)}
    selected_upper = {item.upper() for item in selected}
    for field in _PROTECTED_SOURCE_LIST_FIELDS:
        raw = base.get(field)
        values = raw if isinstance(raw, (list, tuple, set)) else [raw]
        kept = [
            copy.deepcopy(value)
            for value in values
            if _protected_field_aliases(field, value) & (
                selected | selected_upper
            )
        ]
        if kept:
            out[field] = kept if isinstance(raw, (list, tuple, set)) else kept[0]
        else:
            out.pop(field, None)
    # Keep one explicit, child-scoped inventory even when a source identity
    # originated only in recovery metadata rather than a dedicated list field.
    out["protected_source_items"] = list(dict.fromkeys(protected_items))


def _embedded_protected_source_items(record: dict[str, Any]) -> set[str]:
    """Return source identities present outside placement/inventory metadata."""

    value = copy.deepcopy(record)
    value.pop("_placement_contract", None)
    value.pop("protected_source_items", None)
    rendered = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    items = {
        match.group(0).upper() for match in _PROTECTED_ID_RE.finditer(rendered)
    }
    items.update(
        "IMAGE:" + match.group(0).rstrip(".,;:!?")
        for match in _IMAGE_URL_RE.finditer(rendered)
    )
    items.update(
        "KATEX:" + phase3._sha256_text(match.group("body").strip())
        for match in _KATEX_RE.finditer(rendered)
        if match.group("body").strip()
    )
    return items


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
        existing_placement = (
            record.get("_placement_contract")
            if isinstance(record.get("_placement_contract"), dict)
            else {}
        )
        source_claim = phase31._description_source_claim(record)
        index_by_id[concept_id] = index
        concepts.append(
            {
                "concept_id": concept_id,
                "source_order": index + 1,
                "current_topic_id": str(record.get("_semantic_topic_id") or ""),
                "current_topic_title": str(record.get("topic") or ""),
                "concept_title": title,
                "parent_concept": str(record.get("parent_concept") or ""),
                "source_claim": source_claim,
                "existing_mastery": _existing_mastery(record),
                "source_location_topic_id": str(
                    (record.get("_placement_contract") or {}).get(
                        "source_location_topic_id"
                    )
                    or record.get("_semantic_topic_id")
                    or ""
                ),
                "protected_source_items": _protected_source_items(record),
                "origin_claim_sha256": str(
                    existing_placement.get("origin_claim_sha256")
                    or placement_policy.claim_text_sha256(source_claim)
                ),
                "origin_claim_ids": [
                    str(value)
                    for value in existing_placement.get("origin_claim_ids") or []
                    if str(value)
                ],
                "split_group_id": str(
                    existing_placement.get("split_group_id") or ""
                ),
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
            "protected_source_items": {
                "type": "array",
                "items": {"type": "string"},
            },
            # Typed relationships are the provider's real output for
            # placement. ``topic_id`` above is only a proposal: deterministic
            # code recomputes ownership from these and overrides it.
            "topic_relationships": {
                "type": "array",
                "minItems": 1,
                "maxItems": 12,
                "items": {
                    "type": "object",
                    "properties": {
                        "topic_id": {"type": "string", "enum": topic_ids},
                        "relationship_type": {
                            "type": "string",
                            "enum": [
                                kind.value
                                for kind in placement_policy.RelationshipType
                            ],
                        },
                        "necessity": {"type": "boolean"},
                        "evidence_block_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "reason": {"type": "string"},
                    },
                    "required": [
                        "topic_id",
                        "relationship_type",
                        "necessity",
                        "evidence_block_ids",
                        "reason",
                    ],
                    "additionalProperties": False,
                },
            },
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
            "protected_source_items",
            "topic_relationships",
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


def _critic_schema(
    concept_ids: list[str],
    relationship_ids: list[str] | None = None,
    split_review_ids: list[str] | None = None,
) -> dict[str, Any]:
    relationship_ids = list(dict.fromkeys(relationship_ids or []))
    split_review_ids = list(dict.fromkeys(split_review_ids or []))
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
                "relationship_reviews": {
                    "type": "array",
                    "minItems": len(relationship_ids),
                    "maxItems": len(relationship_ids),
                    "items": {
                        "type": "object",
                        "properties": {
                            "relationship_id": {
                                "type": "string",
                                "enum": relationship_ids or ["NONE"],
                            },
                            "verdict": {
                                "type": "string",
                                "enum": ["accepted", "rejected"],
                            },
                            "evidence_supported": {"type": "boolean"},
                            "necessity_supported": {"type": "boolean"},
                            "direction_supported": {"type": "boolean"},
                            "reason": {"type": "string"},
                        },
                        "required": [
                            "relationship_id",
                            "verdict",
                            "evidence_supported",
                            "necessity_supported",
                            "direction_supported",
                            "reason",
                        ],
                        "additionalProperties": False,
                    },
                },
                "split_reviews": {
                    "type": "array",
                    "minItems": len(split_review_ids),
                    "maxItems": len(split_review_ids),
                    "items": {
                        "type": "object",
                        "properties": {
                            "split_review_id": {
                                "type": "string",
                                "enum": split_review_ids or ["NONE"],
                            },
                            "verdict": {
                                "type": "string",
                                "enum": ["accepted", "rejected"],
                            },
                            "complete_parent_claim_preserved": {
                                "type": "boolean",
                            },
                            "protected_items_preserved": {
                                "type": "boolean",
                            },
                            "irreducible_relationships_preserved": {
                                "type": "boolean",
                            },
                            "reason": {"type": "string"},
                        },
                        "required": [
                            "split_review_id",
                            "verdict",
                            "complete_parent_claim_preserved",
                            "protected_items_preserved",
                            "irreducible_relationships_preserved",
                            "reason",
                        ],
                        "additionalProperties": False,
                    },
                },
            },
            "required": [
                "verdict",
                "confidence",
                "accepted_concept_ids",
                "rejected_concept_ids",
                "issues",
                "relationship_reviews",
                "split_reviews",
            ],
            "additionalProperties": False,
        },
    }


def _teaching_order_for(
    payload: dict[str, Any],
    *,
    graph: dict[str, Any] | None = None,
) -> placement_policy.TeachingOrder:
    """Seal the teaching order for this adjudication payload.

    Prefers the verified semantic graph, whose block order is the sealed
    source contract. Falls back to the payload's own topic order, which is
    itself derived from that graph. Section numbers are never parsed.
    """

    if not isinstance(graph, dict):
        try:
            graph = phase3.active_graph()
        except Exception:
            graph = None
    if isinstance(graph, dict) and graph.get("topics"):
        return placement_policy.seal_teaching_order(
            graph,
            source_contract_hash=str(graph.get("source_contract_hash") or ""),
        )
    return placement_policy.seal_teaching_order(
        {
            "topics": [
                {
                    "topic_id": str(row.get("topic_id") or ""),
                    "title": str(row.get("title") or row.get("topic") or ""),
                }
                for row in payload.get("topics") or []
                if isinstance(row, dict)
            ],
            "blocks": [],
        }
    )


def _enforce_placement_policy(
    payload: dict[str, Any],
    data: dict[str, Any],
) -> dict[str, Any]:
    """Recompute each segment's owning topic deterministically.

    The provider proposes ``topic_id`` and classifies typed relationships.
    This replaces the proposal with the owner computed by
    :mod:`placement_policy`, so ownership stops being a free-form model
    choice inside the stage that actually moves concepts.

    A segment whose relationships cannot certify an owner keeps the
    provider's proposal and is marked uncertified, so downstream grounding
    and the final certificate can still reject it. Placement is never
    silently invented here.
    """

    order = _teaching_order_for(payload)
    topic_ids = [
        str(row.get("topic_id") or "")
        for row in payload.get("topics") or []
        if isinstance(row, dict) and str(row.get("topic_id") or "")
    ]
    corrected = 0
    uncertified = 0

    for concept in data.get("concepts") or []:
        if not isinstance(concept, dict):
            continue
        concept_id = str(concept.get("concept_id") or "")
        for index, segment in enumerate(concept.get("segments") or []):
            if not isinstance(segment, dict):
                continue
            claim_id = f"{concept_id}#{index}"
            relationships = placement_policy.parse_relationships(
                {"relationships": [
                    {**row, "claim_id": claim_id}
                    for row in segment.get("topic_relationships") or []
                    if isinstance(row, dict)
                ]},
                known_claim_ids=[claim_id],
                known_topic_ids=topic_ids,
            )
            claim = placement_policy.AtomicClaim(
                claim_id=claim_id,
                normalized_claim=str(segment.get("description") or ""),
                source_location_topic_id=str(segment.get("topic_id") or ""),
            )
            try:
                decision = placement_policy.compute_provisional_placement(
                    claim, relationships, order)
            except placement_policy.PlacementPolicyError as exc:
                uncertified += 1
                segment["_placement_certified"] = False
                segment["_placement_note"] = str(exc)[:500]
                continue
            proposed = str(segment.get("topic_id") or "")
            # Compatibility helper only.  Live production finalisation happens
            # centrally after an exhaustive independent relationship review.
            segment["_placement_certified"] = False
            segment["_placement_owner"] = decision.owner_topic_id
            segment["_placement_prerequisites"] = list(
                decision.prerequisite_topic_ids)
            segment["_placement_reference_edges"] = [
                list(edge) for edge in decision.reference_edges
            ]
            segment["_placement_policy_version"] = decision.policy_version
            if decision.owner_topic_id != proposed:
                corrected += 1
                segment["_placement_proposed_topic_id"] = proposed
                segment["topic_id"] = decision.owner_topic_id

    if corrected:
        progress.log(
            f"Placement policy corrected {corrected} proposed topic "
            "assignment(s) to the latest genuinely required topic.",
            level="warning",
        )
    if uncertified:
        progress.log(
            f"{uncertified} segment(s) carried no certifiable ownership "
            "relationship; the proposal was kept for independent grounding "
            "rather than treated as certified placement.",
            level="warning",
        )
    return data


def _exact_block_directory(
    graph: dict[str, Any],
    canonical: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    canonical_by_id = {
        str(row.get("block_id") or ""): row
        for row in canonical.get("blocks") or []
        if isinstance(row, dict) and str(row.get("block_id") or "")
    }
    directory: dict[str, dict[str, Any]] = {}
    for block in graph.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        block_id = str(block.get("block_id") or "")
        topic_id = str(block.get("topic_id") or "")
        kind = str(block.get("kind") or "")
        if (
            not block_id
            or not topic_id
            or kind in {"layout", "heading", "navigation"}
        ):
            continue
        source = canonical_by_id.get(block_id, {})
        text = phase3._clean_public_text(
            phase3._graph_block_text(block, source)
        )
        directory[block_id] = {
            "block_id": block_id,
            "topic_id": topic_id,
            "kind": kind,
            "subtopic_id": str(block.get("subtopic_id") or ""),
            "text": text,
            "text_sha256": phase3._sha256_text(text),
        }
    return directory


def _relationship_ids_from_review_payload(payload: dict[str, Any]) -> list[str]:
    return list(dict.fromkeys(
        str(relation.get("relationship_id") or "")
        for decision in payload.get("proposed_decisions") or []
        if isinstance(decision, dict)
        for segment in decision.get("segments") or []
        if isinstance(segment, dict)
        for relation in segment.get("topic_relationships") or []
        if isinstance(relation, dict) and str(relation.get("relationship_id") or "")
    ))


def _split_review_ids_from_review_payload(payload: dict[str, Any]) -> list[str]:
    return list(dict.fromkeys(
        str(attestation.get("split_review_id") or "")
        for decision in payload.get("proposed_decisions") or []
        if isinstance(decision, dict)
        for segment in decision.get("segments") or []
        if isinstance(segment, dict)
        for contract in [segment.get("_placement_contract")]
        if isinstance(contract, dict)
        for attestation in [contract.get("split_attestation")]
        if isinstance(attestation, dict)
        and str(attestation.get("split_review_id") or "")
    ))


def _response_omits_all_relationships(response: object) -> bool:
    if not isinstance(response, dict):
        return False
    segments = [
        segment
        for row in response.get("concepts") or []
        if isinstance(row, dict)
        for segment in row.get("segments") or []
        if isinstance(segment, dict)
    ]
    return bool(segments) and all(
        "topic_relationships" not in segment for segment in segments
    )


def _segment_changed(
    segment: dict[str, Any],
    original: dict[str, Any],
) -> bool:
    return any((
        _normal(segment.get("concept_title"))
        != _normal(original.get("concept_title")),
        _normal(segment.get("parent_concept"))
        != _normal(original.get("parent_concept")),
        _normal(segment.get("description"))
        != _normal(original.get("source_claim")),
    ))


def _segment_change_fields(
    segment: dict[str, Any],
    original: dict[str, Any],
) -> list[str]:
    comparisons = (
        ("concept_title", segment.get("concept_title"), original.get("concept_title")),
        ("parent_concept", segment.get("parent_concept"), original.get("parent_concept")),
        ("source_claim", segment.get("description"), original.get("source_claim")),
    )
    return [name for name, proposed, prior in comparisons if _normal(proposed) != _normal(prior)]


def _derive_final_action(
    decision: dict[str, Any],
    original: dict[str, Any],
) -> tuple[str, str]:
    segments = [
        row for row in decision.get("segments") or [] if isinstance(row, dict)
    ]
    if len(segments) > 1:
        return "split", ""
    if len(segments) != 1:
        return "", "topology decision has no commit segment"
    segment = segments[0]
    changed_fields = _segment_change_fields(segment, original)
    changed = bool(changed_fields)
    moved = str(segment.get("topic_id") or "") != str(
        original.get("current_topic_id") or ""
    )
    if changed and moved:
        return "", (
            "changed source claim and changed owner in one operation; "
            "move+refine is not a certified Phase 3.2 action (changed: "
            + ", ".join(changed_fields)
            + ")"
        )
    if moved:
        return "move", ""
    if changed:
        return "refine", ""
    return "keep", ""


def _prepare_placement_review(
    response: dict[str, Any],
    *,
    payload: dict[str, Any],
    concepts: dict[str, dict[str, Any]],
    graph: dict[str, Any],
    canonical: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    """Validate provider relationships and build a provisional critic packet."""

    value = copy.deepcopy(response)
    rows = value.get("concepts") if isinstance(value, dict) else None
    if not isinstance(rows, list):
        return value, [], ["topology adjudicator returned no concepts array"]
    order = _teaching_order_for(payload, graph=graph)
    topic_ids = set(order.ranks)
    blocks = _exact_block_directory(graph, canonical)
    evidence_packet: list[dict[str, Any]] = []
    errors: list[str] = []

    for row in rows:
        if not isinstance(row, dict):
            continue
        concept_id = str(row.get("concept_id") or "")
        original = concepts.get(concept_id)
        if original is None:
            continue
        segments = row.get("segments")
        if not isinstance(segments, list):
            continue
        parent_items = list(original.get("protected_source_items") or [])
        existing_origin_ids = [
            str(item)
            for item in original.get("origin_claim_ids") or []
            if str(item)
        ]
        origin_ids = tuple(dict.fromkeys(
            existing_origin_ids
            + ([concept_id] if len(segments) > 1 else [])
            or [concept_id]
        ))
        child_item_counts: dict[str, int] = {}
        split_children: list[placement_policy.AtomicClaim] = []
        split_relationships: list[placement_policy.TopicRelationship] = []
        parent_claim = placement_policy.AtomicClaim(
            claim_id=concept_id,
            normalized_claim=str(original.get("source_claim") or ""),
            source_location_topic_id=str(
                original.get("source_location_topic_id")
                or original.get("current_topic_id")
                or ""
            ),
            origin_claim_ids=origin_ids,
            split_group_id=(
                str(original.get("split_group_id") or "")
                or (concept_id if len(segments) > 1 else "")
            ),
            protected_source_items=tuple(parent_items),
        )
        source_location_topic_id = str(
            original.get("source_location_topic_id")
            or original.get("current_topic_id")
            or ""
        )
        for index, segment in enumerate(segments):
            if not isinstance(segment, dict):
                continue
            # The independent split review must bind the exact public claim
            # text that the parser will later materialize.  Normalizing only
            # in ``_parse_decisions`` made otherwise valid attestations stale
            # whenever provider text contained markup or other public-text
            # cleanup targets (for example KaTeX wrappers).
            for field in (
                "concept_title",
                "parent_concept",
                "description",
                "achieving_mastery",
            ):
                segment[field] = phase3._clean_public_text(
                    segment.get(field) or ""
                )
            segment["keywords"] = [
                phase3._clean_public_text(value)
                for value in segment.get("keywords") or []
                if phase3._clean_public_text(value)
            ]
            claim_id = f"{concept_id}#{index + 1}"
            normalized_segment_claim = phase3._clean_public_text(
                segment.get("description") or ""
            )
            raw_relationships = [
                item for item in segment.get("topic_relationships") or []
                if isinstance(item, dict)
            ]
            relationships = placement_policy.parse_relationships(
                {"relationships": [
                    {**item, "claim_id": claim_id}
                    for item in raw_relationships
                ]},
                known_claim_ids=[claim_id],
                known_topic_ids=topic_ids,
            )
            if not raw_relationships:
                errors.append(f"{concept_id} segment {index + 1} has no topic relationships")
                continue
            if len(relationships) != len(raw_relationships):
                errors.append(
                    f"{concept_id} segment {index + 1} has an invalid relationship ID/type"
                )
                continue
            seen_topics: set[str] = set()
            invalid_relationship = False
            normalized_relationships: list[dict[str, Any]] = []
            for relation in relationships:
                if relation.topic_id in seen_topics:
                    errors.append(
                        f"{claim_id} repeats or conflicts on topic {relation.topic_id}"
                    )
                    invalid_relationship = True
                    continue
                seen_topics.add(relation.topic_id)
                if not relation.provider_reason.strip():
                    errors.append(f"{relation.relationship_id} omitted its relationship reason")
                    invalid_relationship = True
                necessary = relation.relationship_type in placement_policy.NECESSARY_TYPES
                if necessary != bool(relation.necessity):
                    errors.append(
                        f"{relation.relationship_id} has an inconsistent necessity flag"
                    )
                    invalid_relationship = True
                if not relation.evidence_block_ids:
                    errors.append(
                        f"{relation.relationship_id} has no exact relationship evidence"
                    )
                    invalid_relationship = True
                if len(set(relation.evidence_block_ids)) != len(relation.evidence_block_ids):
                    errors.append(f"{relation.relationship_id} repeats evidence blocks")
                    invalid_relationship = True
                exact_blocks: list[dict[str, Any]] = []
                for block_id in relation.evidence_block_ids:
                    block = blocks.get(block_id)
                    if block is None:
                        errors.append(
                            f"{relation.relationship_id} cites unknown source block {block_id}"
                        )
                        invalid_relationship = True
                        continue
                    if block["topic_id"] != relation.topic_id:
                        errors.append(
                            f"{relation.relationship_id} cites {block_id} from topic "
                            f"{block['topic_id']} as evidence for {relation.topic_id}"
                        )
                        invalid_relationship = True
                        continue
                    exact_blocks.append(copy.deepcopy(block))
                normalized = placement_policy.relationship_audit_row(relation)
                normalized["reason"] = normalized.pop("provider_reason")
                normalized["critic_verdict"] = ""
                normalized_relationships.append(normalized)
                evidence_packet.append({
                    "relationship_id": relation.relationship_id,
                    "claim_id": claim_id,
                    "claim": normalized_segment_claim,
                    "topic_id": relation.topic_id,
                    "relationship_type": relation.relationship_type.value,
                    "necessity": bool(relation.necessity),
                    "reason": relation.provider_reason,
                    "exact_source_blocks": exact_blocks,
                })
            if invalid_relationship:
                continue
            split_relationships.extend(relationships)
            claim = placement_policy.AtomicClaim(
                claim_id=claim_id,
                normalized_claim=normalized_segment_claim,
                source_location_topic_id=source_location_topic_id,
                origin_claim_ids=origin_ids,
                split_group_id=(
                    str(original.get("split_group_id") or "")
                    or (concept_id if len(segments) > 1 else "")
                ),
                protected_source_items=tuple(
                    str(item) for item in segment.get("protected_source_items") or []
                    if str(item)
                ),
            )
            if len(segments) == 1:
                claim = placement_policy.AtomicClaim(
                    claim_id=claim.claim_id,
                    normalized_claim=claim.normalized_claim,
                    source_location_topic_id=claim.source_location_topic_id,
                    origin_claim_ids=claim.origin_claim_ids,
                    split_group_id=claim.split_group_id,
                    protected_source_items=tuple(parent_items),
                )
                segment["protected_source_items"] = list(parent_items)
            else:
                unknown_items = sorted(
                    set(claim.protected_source_items) - set(parent_items)
                )
                if unknown_items:
                    errors.append(
                        f"{claim_id} invented protected source item(s): "
                        + ", ".join(unknown_items)
                    )
                for item in claim.protected_source_items:
                    child_item_counts[item] = child_item_counts.get(item, 0) + 1
            split_children.append(claim)
            try:
                provisional = placement_policy.compute_provisional_placement(
                    claim, relationships, order
                )
            except placement_policy.PlacementPolicyError as exc:
                errors.append(f"{claim_id} placement is uncertifiable: {exc}")
                continue
            segment["topic_id"] = provisional.owner_topic_id
            segment["topic_relationships"] = normalized_relationships
            segment["_placement_contract"] = {
                "certified": False,
                "policy_version": placement_policy.POLICY_VERSION,
                "teaching_order_sha256": order.sha256,
                "claim_id": claim.claim_id,
                "normalized_claim": claim.normalized_claim,
                "origin_claim_sha256": str(
                    original.get("origin_claim_sha256")
                    or placement_policy.claim_text_sha256(
                        original.get("source_claim") or ""
                    )
                ),
                "source_location_topic_id": claim.source_location_topic_id,
                "owner_topic_id": provisional.owner_topic_id,
                "required_topic_ids": list(provisional.required_topic_ids),
                "prerequisite_topic_ids": list(provisional.prerequisite_topic_ids),
                "reference_edges": [list(edge) for edge in provisional.reference_edges],
                "illustration_topic_ids": list(provisional.illustration_topic_ids),
                "topic_relationships": normalized_relationships,
                "origin_claim_ids": list(claim.origin_claim_ids),
                "split_group_id": claim.split_group_id,
                "protected_source_items": list(claim.protected_source_items),
                "split_attestation": {},
                "placement_certificate_sha256": "",
            }
        if len(segments) > 1:
            duplicates = sorted(
                item for item, count in child_item_counts.items() if count > 1
            )
            if duplicates:
                errors.append(
                    f"{concept_id} split duplicates protected source item(s): "
                    + ", ".join(duplicates)
                )
            audit = placement_policy.audit_split_structure(
                [parent_claim], split_children
            )
            if not audit.committed:
                errors.append(f"{concept_id} {audit.reason}")
            elif len(split_children) == len(segments):
                try:
                    attestation = placement_policy.pending_split_attestation(
                        parent_claim,
                        split_children,
                        split_relationships,
                        split_group_id=parent_claim.split_group_id,
                    )
                except placement_policy.PlacementPolicyError as exc:
                    errors.append(f"{concept_id} split review failed: {exc}")
                else:
                    for segment in segments:
                        if not isinstance(segment, dict):
                            continue
                        contract = segment.get("_placement_contract")
                        if isinstance(contract, dict):
                            contract["split_attestation"] = copy.deepcopy(
                                attestation
                            )
        action, action_error = _derive_final_action(row, original)
        if action_error:
            errors.append(f"{concept_id} {action_error}")
        elif action:
            row["decision"] = action
    return value, evidence_packet, list(dict.fromkeys(errors))


def _relationship_review_verdicts(
    review: dict[str, Any],
    expected_ids: set[str],
) -> tuple[dict[str, str], list[str]]:
    rows = review.get("relationship_reviews") if isinstance(review, dict) else None
    if not isinstance(rows, list):
        return {}, ["placement critic returned no relationship_reviews array"]
    verdicts: dict[str, str] = {}
    errors: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            errors.append("placement critic returned a non-object relationship review")
            continue
        relationship_id = str(row.get("relationship_id") or "")
        if relationship_id not in expected_ids:
            errors.append(
                f"placement critic returned unknown relationship {relationship_id or '<empty>'}"
            )
            continue
        if relationship_id in verdicts:
            errors.append(f"placement critic duplicated relationship {relationship_id}")
            continue
        accepted = (
            str(row.get("verdict") or "") == "accepted"
            and row.get("evidence_supported") is True
            and row.get("necessity_supported") is True
            and row.get("direction_supported") is True
        )
        verdicts[relationship_id] = "accepted" if accepted else "rejected"
        if not accepted:
            errors.append(
                f"{relationship_id} was not independently certified: "
                + str(row.get("reason") or "rejected")[:500]
            )
    missing = sorted(expected_ids - set(verdicts))
    if missing:
        errors.append(
            "placement critic omitted relationship(s): " + ", ".join(missing)
        )
    return verdicts, errors


def _split_review_verdicts(
    review: dict[str, Any],
    expected: dict[str, dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Require one exhaustive semantic-survival verdict per exact split."""

    rows = review.get("split_reviews") if isinstance(review, dict) else None
    if not expected:
        if rows in (None, []):
            return {}, []
        return {}, ["placement critic returned an unexpected split review"]
    if not isinstance(rows, list):
        return {}, ["placement critic returned no split_reviews array"]
    accepted: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    errors: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            errors.append("placement critic returned a non-object split review")
            continue
        review_id = str(row.get("split_review_id") or "")
        pending = expected.get(review_id)
        if pending is None:
            errors.append(
                "placement critic returned unknown or stale split review "
                + (review_id or "<empty>")
            )
            continue
        if review_id in seen:
            errors.append(f"placement critic duplicated split review {review_id}")
            continue
        seen.add(review_id)
        complete = (
            str(row.get("verdict") or "") == "accepted"
            and row.get("complete_parent_claim_preserved") is True
            and row.get("protected_items_preserved") is True
            and row.get("irreducible_relationships_preserved") is True
        )
        attestation = {
            **copy.deepcopy(pending),
            "critic_verdict": "accepted" if complete else "rejected",
            "complete_parent_claim_preserved": (
                row.get("complete_parent_claim_preserved") is True
            ),
            "protected_items_preserved": (
                row.get("protected_items_preserved") is True
            ),
            "irreducible_relationships_preserved": (
                row.get("irreducible_relationships_preserved") is True
            ),
            "critic_reason": str(row.get("reason") or "")[:2000],
        }
        if not complete:
            errors.append(
                f"{review_id} was not independently certified to preserve "
                "the parent claim, protected items, and irreducible relationships: "
                + str(row.get("reason") or "rejected")[:500]
            )
            continue
        try:
            placement_policy.validate_split_attestation(attestation)
        except placement_policy.PlacementPolicyError as exc:
            errors.append(f"{review_id} split attestation is invalid: {exc}")
            continue
        accepted[review_id] = attestation
    missing = sorted(set(expected) - set(accepted))
    if missing:
        errors.append("placement critic omitted split review(s): " + ", ".join(missing))
    return accepted, errors


def _finalize_certified_placements(
    decisions: dict[str, dict[str, Any]],
    *,
    review: dict[str, Any],
    payload: dict[str, Any],
    concepts: dict[str, dict[str, Any]],
    graph: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    order = _teaching_order_for(payload, graph=graph)
    expected_ids = {
        str(relation.get("relationship_id") or "")
        for decision in decisions.values()
        for segment in decision.get("segments") or []
        if isinstance(segment, dict)
        for relation in segment.get("topic_relationships") or []
        if isinstance(relation, dict) and str(relation.get("relationship_id") or "")
    }
    verdicts, errors = _relationship_review_verdicts(review, expected_ids)
    expected_splits: dict[str, dict[str, Any]] = {}
    for concept_id, decision in decisions.items():
        segments = [
            segment for segment in decision.get("segments") or []
            if isinstance(segment, dict)
        ]
        review_ids: set[str] = set()
        for segment in segments:
            contract = segment.get("_placement_contract")
            raw_attestation = (
                contract.get("split_attestation")
                if isinstance(contract, dict)
                else None
            )
            if not raw_attestation:
                continue
            try:
                pending = placement_policy.validate_split_attestation(
                    raw_attestation, require_accepted=False
                )
            except placement_policy.PlacementPolicyError as exc:
                errors.append(f"{concept_id} has invalid pending split review: {exc}")
                continue
            review_id = str(pending.get("split_review_id") or "")
            review_ids.add(review_id)
            prior = expected_splits.get(review_id)
            if prior is not None and prior != pending:
                errors.append(
                    f"{concept_id} reuses {review_id} for different split material"
                )
            expected_splits[review_id] = pending
        if len(segments) > 1 and len(review_ids) != 1:
            errors.append(
                f"{concept_id} split children do not share one exact split review"
            )
        if len(segments) == 1 and review_ids:
            errors.append(f"{concept_id} non-split decision carried a split review")

    accepted_splits, split_review_errors = _split_review_verdicts(
        review, expected_splits
    )
    errors.extend(split_review_errors)
    if errors:
        return decisions, list(dict.fromkeys(errors))

    finalized = copy.deepcopy(decisions)
    for concept_id, decision in finalized.items():
        original = concepts[concept_id]
        segment_results: list[tuple[
            dict[str, Any],
            placement_policy.AtomicClaim,
            list[placement_policy.TopicRelationship],
            placement_policy.PlacementDecision,
        ]] = []
        for index, segment in enumerate(decision.get("segments") or []):
            claim_id = f"{concept_id}#{index + 1}"
            relationships = placement_policy.parse_relationships(
                {"relationships": [
                    {
                        **relation,
                        "claim_id": claim_id,
                        "reason": str(
                            relation.get("reason")
                            or relation.get("provider_reason")
                            or ""
                        ),
                    }
                    for relation in segment.get("topic_relationships") or []
                    if isinstance(relation, dict)
                ]},
                known_claim_ids=[claim_id],
                known_topic_ids=order.ranks,
            )
            relationships = placement_policy.apply_critic_verdicts(
                relationships, verdicts
            )
            protected = tuple(
                str(item) for item in segment.get("protected_source_items") or []
                if str(item)
            )
            claim = placement_policy.AtomicClaim(
                claim_id=claim_id,
                normalized_claim=str(segment.get("description") or ""),
                source_location_topic_id=str(
                    original.get("source_location_topic_id")
                    or original.get("current_topic_id")
                    or ""
                ),
                origin_claim_ids=tuple(dict.fromkeys(
                    [
                        str(item)
                        for item in original.get("origin_claim_ids") or []
                        if str(item)
                    ]
                    + (
                        [concept_id]
                        if len(decision.get("segments") or []) > 1
                        else []
                    )
                    or [concept_id]
                )),
                split_group_id=(
                    str(original.get("split_group_id") or "")
                    or (
                        concept_id
                        if len(decision.get("segments") or []) > 1
                        else ""
                    )
                ),
                protected_source_items=protected,
            )
            try:
                placement = placement_policy.compute_placement(
                    claim, relationships, order
                )
            except placement_policy.PlacementPolicyError as exc:
                errors.append(f"{claim_id} certified placement failed: {exc}")
                continue
            segment["topic_id"] = placement.owner_topic_id
            segment_results.append((segment, claim, relationships, placement))

        split_attestation: dict[str, Any] = {}
        if len(segment_results) > 1:
            provisional_ids = {
                str(
                    (
                        (segment.get("_placement_contract") or {}).get(
                            "split_attestation"
                        ) or {}
                    ).get("split_review_id")
                    or ""
                )
                for segment, _claim, _relations, _placement in segment_results
            }
            provisional_ids.discard("")
            if len(provisional_ids) != 1:
                errors.append(
                    f"{concept_id} finalized split lost its shared review identity"
                )
            else:
                review_id = next(iter(provisional_ids))
                parent = placement_policy.AtomicClaim(
                    claim_id=concept_id,
                    normalized_claim=str(original.get("source_claim") or ""),
                    source_location_topic_id=str(
                        original.get("source_location_topic_id")
                        or original.get("current_topic_id")
                        or ""
                    ),
                    origin_claim_ids=tuple(dict.fromkeys(
                        [
                            str(item)
                            for item in original.get("origin_claim_ids") or []
                            if str(item)
                        ]
                        + [concept_id]
                    )),
                    split_group_id=segment_results[0][1].split_group_id,
                    protected_source_items=tuple(
                        str(item)
                        for item in original.get("protected_source_items") or []
                        if str(item)
                    ),
                )
                rebuilt = placement_policy.pending_split_attestation(
                    parent,
                    [claim for _segment, claim, _relations, _placement in segment_results],
                    [
                        relation
                        for _segment, _claim, relationships, _placement in segment_results
                        for relation in relationships
                    ],
                    split_group_id=parent.split_group_id,
                )
                pending = expected_splits.get(review_id)
                split_attestation = accepted_splits.get(review_id, {})
                if pending != rebuilt:
                    errors.append(
                        f"{concept_id} split material changed after independent review "
                        f"(pending={phase3._sha256_json(pending)}, "
                        f"rebuilt={phase3._sha256_json(rebuilt)})"
                    )
                else:
                    audit = placement_policy.audit_split(
                        [parent],
                        [
                            claim
                            for _segment, claim, _relationships, _placement
                            in segment_results
                        ],
                        split_attestation=split_attestation,
                    )
                    if not audit.committed:
                        errors.append(f"{concept_id} {audit.reason}")

        for segment, claim, relationships, placement in segment_results:
            relationship_rows = [
                placement_policy.relationship_audit_row(relation)
                for relation in relationships
            ]
            contract = {
                "certified": True,
                "policy_version": placement_policy.POLICY_VERSION,
                "teaching_order_sha256": order.sha256,
                "claim_id": claim.claim_id,
                "normalized_claim": claim.normalized_claim,
                "origin_claim_sha256": str(
                    original.get("origin_claim_sha256")
                    or placement_policy.claim_text_sha256(
                        original.get("source_claim") or ""
                    )
                ),
                "source_location_topic_id": claim.source_location_topic_id,
                "owner_topic_id": placement.owner_topic_id,
                "required_topic_ids": list(placement.required_topic_ids),
                "prerequisite_topic_ids": list(placement.prerequisite_topic_ids),
                "reference_edges": [list(edge) for edge in placement.reference_edges],
                "illustration_topic_ids": list(placement.illustration_topic_ids),
                "topic_relationships": relationship_rows,
                "origin_claim_ids": list(claim.origin_claim_ids),
                "split_group_id": claim.split_group_id,
                "protected_source_items": list(claim.protected_source_items),
                "split_attestation": copy.deepcopy(split_attestation),
            }
            contract["placement_certificate_sha256"] = (
                placement_policy.placement_contract_sha256(contract)
            )
            segment["topic_relationships"] = [
                {
                    **row,
                    "reason": row.get("provider_reason") or "",
                }
                for row in relationship_rows
            ]
            segment["_placement_contract"] = contract
        action, action_error = _derive_final_action(decision, original)
        if action_error:
            errors.append(f"{concept_id} {action_error}")
        elif action:
            decision["decision"] = action
    return finalized, list(dict.fromkeys(errors))


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
        "using critic_feedback and previous_decisions. If human_resolutions is "
        "supplied, apply its selected refine/move/split/keep direction or custom "
        "instruction exactly, then return the ordinary proposal for independent "
        "criticism; the human direction is not verification."
        + PLACEMENT_PROVIDER_INSTRUCTIONS
    )
    return phase3.phase22._openai_multimodal_json(
        system=system,
        prompt=json.dumps(payload, ensure_ascii=False, indent=2),
        pages=[],
        response_schema=_adjudication_schema(concept_ids, topic_ids),
        purpose="concept_mapping",
        max_tokens=max(6000, min(32000, len(concept_ids) * 1100)),
        single_attempt=bool(payload.get("human_resolutions")),
    )


def _critic_via_openai(payload: dict[str, Any]) -> dict[str, Any]:
    concept_ids = [
        str(row.get("concept_id") or "")
        for row in payload.get("concepts") or []
    ]
    relationship_count = len(_relationship_ids_from_review_payload(payload))
    split_review_ids = _split_review_ids_from_review_payload(payload)
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
        "requires all accepted, none rejected, confidence at least "
        f"{confidence_policy.threshold_text()}, and no issues. Do not rewrite "
        "the proposals."
        + PLACEMENT_CRITIC_INSTRUCTIONS
    )
    return phase3.phase22._openai_multimodal_json(
        system=system,
        prompt=json.dumps(payload, ensure_ascii=False, indent=2),
        pages=[],
        response_schema=_critic_schema(
            concept_ids,
            _relationship_ids_from_review_payload(payload),
            split_review_ids,
        ),
        purpose="concept_mapping",
        max_tokens=_critic_output_token_budget(
            len(concept_ids), relationship_count
        ),
        single_attempt=bool(payload.get("human_resolutions")),
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
        if not confidence_policy.accepts(confidence):
            errors.append(
                f"{concept_id} topology confidence {confidence:.3f} is below "
                f"{confidence_policy.threshold_text()}"
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
            if not confidence_policy.accepts(segment_confidence):
                errors.append(
                    f"{concept_id} segment {position} confidence "
                    f"{segment_confidence:.3f} is below "
                    f"{confidence_policy.threshold_text()}"
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
                    "protected_source_items": [
                        str(value)
                        for value in segment.get("protected_source_items") or []
                        if str(value)
                    ],
                    "topic_relationships": copy.deepcopy(
                        segment.get("topic_relationships") or []
                    ),
                    "_placement_contract": copy.deepcopy(
                        segment.get("_placement_contract") or {}
                    ),
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
    out["_placement_contract"] = copy.deepcopy(
        segment.get("_placement_contract") or {}
    )
    if decision == "split":
        _project_split_source_inventory(
            base,
            out,
            protected_items=[
                str(item)
                for item in segment.get("protected_source_items") or []
                if str(item)
            ],
        )
    return out


def _verify_materialized_split_inventory(
    records: list[dict[str, Any]],
    *,
    concepts: list[dict[str, Any]],
    decisions: dict[str, dict[str, Any]],
) -> None:
    """Require split inventory to survive once in records and contracts."""

    expected_by_id = {
        str(concept.get("concept_id") or ""): {
            str(item)
            for item in concept.get("protected_source_items") or []
            if str(item)
        }
        for concept in concepts
    }
    children_by_id: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        origin = str(record.get("_phase32_origin_concept_id") or "")
        if origin:
            children_by_id.setdefault(origin, []).append(record)

    for concept_id, decision in decisions.items():
        if str(decision.get("decision") or "") != "split":
            continue
        expected = expected_by_id.get(concept_id, set())
        children = children_by_id.get(concept_id, [])
        if len(children) != len(decision.get("segments") or []):
            raise ValueError(
                f"{concept_id} split materialization changed its child count"
            )
        physical_owners: dict[str, list[int]] = {
            item: [] for item in expected
        }
        contract_owners: dict[str, list[int]] = {
            item: [] for item in expected
        }
        for child_index, child in enumerate(children, start=1):
            physical = set(_protected_source_items({
                key: copy.deepcopy(value)
                for key, value in child.items()
                if key != "_placement_contract"
            }))
            embedded = _embedded_protected_source_items(child)
            contract = child.get("_placement_contract") or {}
            contracted = {
                str(item)
                for item in contract.get("protected_source_items") or []
                if str(item)
            }
            unexpected = sorted((physical | contracted) - expected)
            if unexpected:
                raise ValueError(
                    f"{concept_id} split child {child_index} invented protected "
                    "source item(s): " + ", ".join(unexpected)
                )
            for item in expected:
                if item in physical:
                    physical_owners[item].append(child_index)
                if item in contracted:
                    contract_owners[item].append(child_index)
                if item.startswith(("IMAGE:", "KATEX:")) and (
                    item in contracted and item not in embedded
                ):
                    raise ValueError(
                        f"{concept_id} split child {child_index} declares {item} "
                        "without retaining its exact rendered source content"
                    )
        for item in sorted(expected):
            if len(physical_owners[item]) != 1:
                raise ValueError(
                    f"{concept_id} split materialized protected source item "
                    f"{item} in {len(physical_owners[item])} child rows"
                )
            if contract_owners[item] != physical_owners[item]:
                raise ValueError(
                    f"{concept_id} split record/contract ownership disagrees "
                    f"for protected source item {item}"
                )


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
            kept["_placement_contract"] = copy.deepcopy(
                (decision.get("segments") or [{}])[0].get(
                    "_placement_contract"
                )
                or {}
            )
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
    _verify_materialized_split_inventory(
        out,
        concepts=concepts,
        decisions=decisions,
    )
    return out


def _seal_legacy_injected_placements(
    records: list[dict[str, Any]],
    *,
    legacy_concept_ids: set[str],
    concepts: list[dict[str, Any]],
    graph: dict[str, Any],
) -> list[dict[str, Any]]:
    """Seal test-injected legacy decisions after exact grounding.

    Production providers must classify typed relationships before their topic
    choice can acquire authority.  A small number of explicit unit-test
    providers predate that contract and intentionally exercise unrelated
    Phase 3.2 mechanics.  They remain supported only at this injected seam:
    exact Phase 3.1 grounding supplies the evidence, the final semantic topic
    supplies the sole CORE_TEACHING relationship, and the ordinary placement
    digest is then produced.  Nothing from this compatibility path is used
    for an OpenAI/effective production provider.
    """

    if not legacy_concept_ids:
        return records
    order = placement_policy.seal_teaching_order(
        graph,
        source_contract_hash=str(graph.get("source_contract_hash") or ""),
    )
    concept_by_id = {
        str(concept.get("concept_id") or ""): concept
        for concept in concepts
        if isinstance(concept, dict)
    }
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        origin_id = str(record.get("_phase32_origin_concept_id") or "")
        if origin_id in legacy_concept_ids:
            grouped.setdefault(origin_id, []).append(record)

    for origin_id, children in grouped.items():
        original = concept_by_id.get(origin_id)
        if original is None:
            raise ValueError(
                f"legacy placement sealing lost original concept {origin_id}"
            )
        origin_claim_ids = tuple(
            str(item)
            for item in original.get("origin_claim_ids") or [origin_id]
            if str(item)
        )
        source_location_topic_id = str(
            original.get("source_location_topic_id")
            or original.get("current_topic_id")
            or ""
        )
        parent_items = tuple(
            str(item)
            for item in original.get("protected_source_items") or []
            if str(item)
        )
        parents = [
            placement_policy.AtomicClaim(
                claim_id=claim_id,
                normalized_claim=str(original.get("source_claim") or ""),
                source_location_topic_id=source_location_topic_id,
                protected_source_items=parent_items,
            )
            for claim_id in origin_claim_ids
        ]
        child_claims: list[placement_policy.AtomicClaim] = []
        child_inventory: dict[str, tuple[str, ...]] = {}
        for record in children:
            segment_order = int(record.get("_phase32_segment_order") or 1)
            claim_id = f"{origin_id}#{segment_order}"
            items = tuple(_protected_source_items(record))
            child_inventory[claim_id] = items
            child_claims.append(
                placement_policy.AtomicClaim(
                    claim_id=claim_id,
                    normalized_claim=phase31._description_source_claim(record),
                    source_location_topic_id=source_location_topic_id,
                    origin_claim_ids=origin_claim_ids,
                    split_group_id=(
                        str(original.get("split_group_id") or "")
                        or (origin_id if len(children) > 1 else "")
                    ),
                    protected_source_items=items,
                )
            )
        if len(children) > 1:
            raise ValueError(
                f"legacy injected split {origin_id} has no independent "
                "structured split-survival attestation"
            )

        claim_by_id = {claim.claim_id: claim for claim in child_claims}
        for record in children:
            segment_order = int(record.get("_phase32_segment_order") or 1)
            claim = claim_by_id[f"{origin_id}#{segment_order}"]
            owner_topic_id = str(record.get("_semantic_topic_id") or "")
            evidence_block_ids = tuple(
                str(block_id)
                for block_id in record.get("_source_block_ids") or []
                if str(block_id)
            )
            relation = placement_policy.TopicRelationship(
                claim_id=claim.claim_id,
                topic_id=owner_topic_id,
                relationship_type=placement_policy.RelationshipType.CORE_TEACHING,
                necessity=True,
                evidence_block_ids=evidence_block_ids,
                provider_reason=(
                    "legacy injected provider compatibility; exact Phase 3.1 "
                    "grounding independently verified this final topic"
                ),
                critic_verdict="accepted",
            )
            try:
                placement = placement_policy.compute_placement(
                    claim, [relation], order
                )
            except placement_policy.PlacementPolicyError as exc:
                raise ValueError(
                    f"legacy placement for {claim.claim_id} is uncertifiable: {exc}"
                ) from exc
            relationship_rows = [
                placement_policy.relationship_audit_row(relation)
            ]
            contract = {
                "certified": True,
                "policy_version": placement_policy.POLICY_VERSION,
                "teaching_order_sha256": order.sha256,
                "claim_id": claim.claim_id,
                "normalized_claim": claim.normalized_claim,
                "origin_claim_sha256": str(
                    original.get("origin_claim_sha256")
                    or placement_policy.claim_text_sha256(
                        original.get("source_claim") or ""
                    )
                ),
                "source_location_topic_id": claim.source_location_topic_id,
                "owner_topic_id": placement.owner_topic_id,
                "required_topic_ids": list(placement.required_topic_ids),
                "prerequisite_topic_ids": list(
                    placement.prerequisite_topic_ids
                ),
                "reference_edges": [
                    list(edge) for edge in placement.reference_edges
                ],
                "illustration_topic_ids": list(
                    placement.illustration_topic_ids
                ),
                "topic_relationships": relationship_rows,
                "origin_claim_ids": list(claim.origin_claim_ids),
                "split_group_id": claim.split_group_id,
                "protected_source_items": list(child_inventory[claim.claim_id]),
            }
            contract["placement_certificate_sha256"] = (
                placement_policy.placement_contract_sha256(contract)
            )
            record["_placement_contract"] = contract
    return records


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
            "semantic_confidence_policy": confidence_policy.cache_identity(),
            "placement_policy_version": placement_policy.POLICY_VERSION,
            "teaching_order_sha256": placement_policy.seal_teaching_order(
                graph,
                source_contract_hash=str(graph.get("source_contract_hash") or ""),
            ).sha256,
            "semantic_topology_sha256": (
                grounding_certificate.semantic_topology_sha256(graph)
            ),
            "source": early_gate.source_identity(graph),
            "records": phase31._json_safe(records),
            "concepts": concepts,
            "topics": phase31._json_safe(topics),
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
    for record in cache.get("records") or []:
        title = str(record.get("concept_title") or record.get("concept") or "")
        if cr.is_culmination(title):
            continue
        contract = record.get("_placement_contract")
        if (
            not isinstance(contract, dict)
            or contract.get("certified") is not True
            or str(contract.get("policy_version") or "")
            != placement_policy.POLICY_VERSION
            or str(contract.get("owner_topic_id") or "")
            != str(record.get("_semantic_topic_id") or "")
            or str(contract.get("placement_certificate_sha256") or "")
            != placement_policy.placement_contract_sha256(contract)
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


def _topology_candidates(
    *,
    graph: dict[str, Any],
    concept: dict[str, Any],
    topics: list[dict[str, Any]],
    all_concepts: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    current_topic_id = str(concept.get("current_topic_id") or "")
    topic_by_id = {
        str(row.get("topic_id") or ""): row
        for row in topics
        if str(row.get("topic_id") or "")
    }
    seeds: list[dict[str, Any]] = [
        {
            "action": "refine",
            "target_topic_id": current_topic_id,
            "title": "Refine this concept to its verified source claim",
            "topic": str((topic_by_id.get(current_topic_id) or {}).get("title") or ""),
            "coverage": str(concept.get("source_claim") or ""),
            "gap": "Remove or narrow only the unsupported part.",
        },
        {
            "action": "split",
            "target_topic_id": "",
            "title": "Split distinct source-supported concepts",
            "topic": "Across verified source topics",
            "coverage": str(concept.get("source_claim") or ""),
            "gap": "Use only if the claim contains multiple durable ideas.",
        },
        {
            "action": "keep",
            "target_topic_id": current_topic_id,
            "title": "Keep the current source claim and topic",
            "topic": str((topic_by_id.get(current_topic_id) or {}).get("title") or ""),
            "coverage": str(concept.get("source_claim") or ""),
            "gap": "Use only when the independent review can verify it unchanged.",
        },
    ]
    for topic_id, topic in topic_by_id.items():
        if topic_id == current_topic_id:
            continue
        seeds.append(
            {
                "action": "move",
                "target_topic_id": topic_id,
                "title": "Move the complete source claim",
                "topic": str(topic.get("title") or topic_id),
                "coverage": str(concept.get("source_claim") or ""),
                "gap": "Move without changing the claim; the critic must verify the target.",
            }
        )
    candidates: list[dict[str, Any]] = []
    for seed in seeds[:100]:
        candidates.append(
            {
                "target_id": early_gate.candidate_target_id(
                    phase="3.2",
                    action=str(seed["action"]),
                    graph=graph,
                    unit=concept,
                    candidate=seed,
                ),
                "concept_id": "",
                **seed,
            }
        )
    return candidates


def _topology_identity(
    *,
    graph: dict[str, Any],
    concept: dict[str, Any],
    topics: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
) -> dict[str, str]:
    return early_gate.context_identity(
        phase="3.2",
        kind="phase32_concept_blueprint_semantic_conflict",
        graph=graph,
        unit=concept,
        evidence_fingerprint=[
            {
                "topic_id": str(row.get("topic_id") or ""),
                "title": str(row.get("title") or ""),
                "evidence_sha256": phase3._sha256_text(row.get("evidence") or ""),
            }
            for row in topics
        ],
        candidates=candidates,
    )


def _topology_resolution_for(
    *,
    identity: dict[str, str],
    concept: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> dict[str, Any] | None:
    return early_gate.resolution_for(
        identity=identity,
        kind="phase32_concept_blueprint_semantic_conflict",
        phase="3.2",
        unit_id=str(concept.get("concept_id") or ""),
        candidates=candidates,
    )


def _proposed_decision_for(
    response: Any,
    concept_id: str,
) -> dict[str, Any] | None:
    if not isinstance(response, dict):
        return None
    return next(
        (
            copy.deepcopy(row)
            for row in response.get("concepts") or []
            if isinstance(row, dict)
            and str(row.get("concept_id") or "") == concept_id
        ),
        None,
    )


def _topology_pending_decision(
    *,
    identity: dict[str, str],
    graph: dict[str, Any],
    concept: dict[str, Any],
    topics: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    issues: list[str],
    rejected_ids: list[str],
    proposed_decision: dict[str, Any] | None = None,
    resolution: dict[str, Any] | None = None,
    diagnostic_source: str = "critic",
) -> dict[str, Any]:
    clean_issues = [
        re.sub(r"\s+", " ", str(value or "")).strip()[:1200]
        for value in issues
        if str(value or "").strip()
    ] or ["The independent topology critic did not verify this concept."]
    proposed_action = str((proposed_decision or {}).get("decision") or "")
    proposed_topic = ""
    segments = (proposed_decision or {}).get("segments")
    if isinstance(segments, list) and len(segments) == 1 and isinstance(segments[0], dict):
        proposed_topic = str(segments[0].get("topic_id") or "")
    recommended = next(
        (
            row
            for row in candidates
            if str(row.get("action") or "") == proposed_action
            and (
                proposed_action != "move"
                or str(row.get("target_topic_id") or "") == proposed_topic
            )
        ),
        None,
    )
    packet_identity = early_gate.followup_identity(
        identity,
        resolution=resolution,
        issues=clean_issues,
        proposal=proposed_decision,
    )
    options: list[dict[str, Any]] = []
    if recommended is not None:
        options.append(
            {
                "choice": "accept_recommended",
                "label": "Use GPT's recommended source-supported action",
                "recommended": True,
                "target_id": str(recommended.get("target_id") or ""),
            }
        )
    options.extend(
        [
            {
                "choice": "select_candidate",
                "label": "Choose refinement, move, split, or keep",
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
    return early_gate.plain_json(
        {
            **packet_identity,
            "kind": "phase32_concept_blueprint_semantic_conflict",
            "phase": "3.2",
            "conflict": "; ".join(clean_issues[:6]),
            "diagnosis": (
                (
                    "The topology provider placed this result in the "
                    "0.900–0.919 human-review band before independent review."
                    if diagnostic_source == "provider"
                    else "The independent topology critic placed this result "
                    "in the 0.900–0.919 human-review band."
                )
                if confidence is not None
                and early_gate.confidence_band(confidence) == "human_review"
                else (
                    "The topology provider returned a genuine semantic "
                    "concept/source discrepancy before independent review."
                    if diagnostic_source == "provider"
                    else "The first independent topology review found a "
                    "genuine concept/source discrepancy."
                )
            ),
            "decision_question": (
                "Should Aegis refine this source claim, move it, split it, "
                "keep it unchanged, replace the source, or follow your "
                "instruction? The selected direction will still receive one "
                "independent verification."
            ),
            "item": {
                "unit_id": str(concept.get("concept_id") or ""),
                "type_id": "",
                "type_title": str(concept.get("concept_title") or ""),
                "qids": [],
                "questions": [str(concept.get("source_claim") or "")],
                "topic": str(concept.get("current_topic_title") or ""),
            },
            "candidates": [
                {
                    key: str(row.get(key) or "")
                    for key in (
                        "target_id", "concept_id", "title", "topic", "coverage", "gap"
                    )
                }
                for row in candidates
            ],
            "evidence": [
                {
                    "page": "",
                    "label": str(row.get("topic_id") or ""),
                    "text": str(row.get("evidence") or "")[:8000],
                }
                for row in topics[:24]
            ],
            "deferred_assignment_unit_ids": [
                value
                for value in rejected_ids
                if value != str(concept.get("concept_id") or "")
            ],
            "options": options,
        }
    )


def _topology_parse_errors_are_mechanical(errors: list[str]) -> bool:
    """Return true only for an exhaustively known response-shape defect.

    Placement, evidence, and source-survival failures are semantic by default.
    The former ``TOPOLOGY-CONCEPT-`` catch-all accidentally retried a rejected
    refine-and-move as if it were malformed JSON, obscuring the real failure
    behind a later provider-contract error.
    """

    meaningful = [
        value
        for value in errors
        if not value.startswith("missing or invalid concept ID(s):")
    ]
    if not meaningful:
        return True
    mechanical_patterns = (
        r"topology adjudicator returned no concepts array",
        r"topology adjudicator returned a non-object row",
        r"unknown concept ID .+",
        r"duplicate concept ID .+",
        r"TOPOLOGY-CONCEPT-[^ ]+ returned invalid decision .+",
        r"TOPOLOGY-CONCEPT-[^ ]+ returned no segments array",
        r"TOPOLOGY-CONCEPT-[^ ]+ decision [^ ]+ returned [0-9]+ segment\(s\)",
        r"TOPOLOGY-CONCEPT-[^ ]+ segment [0-9]+ is not an object",
        r"TOPOLOGY-CONCEPT-[^ ]+ segment [0-9]+ used unknown topic .+",
        r"TOPOLOGY-CONCEPT-[^ ]+ segment [0-9]+ omitted title, parent, "
        r"Description, or Achieving Mastery",
        r"TOPOLOGY-CONCEPT-[^ ]+ segment [0-9]+ has an invalid "
        r"relationship ID/type",
        r"TOPOLOGY-CONCEPT-[^ ]+ segment [0-9]+ has no topic relationships",
    )
    return all(
        any(re.fullmatch(pattern, value) for pattern in mechanical_patterns)
        for value in meaningful
    )


def _topology_directed_issues(
    resolutions: dict[str, dict[str, Any]],
    decisions: dict[str, dict[str, Any]],
) -> list[str]:
    issues: list[str] = []
    for concept_id, resolution in resolutions.items():
        selected = resolution.get("selected_candidate")
        if not isinstance(selected, dict):
            continue
        expected_action = str(selected.get("action") or "")
        decision = decisions.get(concept_id) or {}
        if str(decision.get("decision") or "") != expected_action:
            issues.append(
                f"{concept_id} did not apply the human-selected "
                f"{expected_action or 'semantic'} action"
            )
            continue
        if expected_action == "move":
            segments = decision.get("segments") or []
            actual_topic = (
                str(segments[0].get("topic_id") or "")
                if len(segments) == 1 and isinstance(segments[0], dict)
                else ""
            )
            expected_topic = str(selected.get("target_topic_id") or "")
            if actual_topic != expected_topic:
                issues.append(
                    f"{concept_id} moved to {actual_topic or '<empty>'} instead "
                    f"of the human-selected topic {expected_topic}"
                )
    return issues


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
    explicit_provider = provider is not None
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
    legacy_concept_ids: set[str] = set()
    size = _batch_size()
    concept_by_id = {
        str(row.get("concept_id") or ""): row for row in concepts
    }
    for batch_index, start in enumerate(range(0, len(concepts), size), start=1):
        batch = concepts[start : start + size]
        batch_ids = {
            str(row.get("concept_id") or "") for row in batch
        }
        gate_candidates: dict[str, list[dict[str, Any]]] = {}
        gate_identities: dict[str, dict[str, str]] = {}
        resolutions: dict[str, dict[str, Any]] = {}
        deferred: set[str] = set()
        for concept in batch:
            concept_id = str(concept.get("concept_id") or "")
            candidate_rows = _topology_candidates(
                graph=graph,
                concept=concept,
                topics=topics,
                all_concepts=concepts,
            )
            identity = _topology_identity(
                graph=graph,
                concept=concept,
                topics=topics,
                candidates=candidate_rows,
            )
            resolution = _topology_resolution_for(
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

        undecided_deferred = [
            concept_id
            for concept_id in sorted(batch_ids & deferred)
            if concept_id not in resolutions
        ]
        if undecided_deferred:
            packets = [
                _topology_pending_decision(
                    identity=gate_identities[concept_id],
                    graph=graph,
                    concept=concept_by_id[concept_id],
                    topics=topics,
                    candidates=gate_candidates[concept_id],
                    issues=[
                        "This concept was also rejected by the first "
                        "independent topology review and needs your bounded "
                        "semantic direction."
                    ],
                    rejected_ids=undecided_deferred,
                )
                for concept_id in undecided_deferred
            ]
            raise HumanDecisionRequired(packets[0], companions=packets[1:])

        if any(
            resolution.get("choice") == "replace_source"
            for resolution in resolutions.values()
        ):
            raise ValueError(
                "Source replacement is required before Phase 3.2 can "
                "continue. Replace or correct the uploaded source and convert "
                "it again; no model request was started."
            )

        # A carried resolution settles its decision without directing an
        # action, so the two roles split here. ``resolutions`` stays the settled
        # set -- dropping a carried answer from it would leave the concept
        # looking undecided above, and the identical decision would be raised
        # and carried again on every pass. ``directives`` is the subset that
        # actually asks for something, and it is the only one the model sees:
        # "Aegis changed nothing here" is not a direction to adjudicate.
        directives = {
            concept_id: resolution
            for concept_id, resolution in resolutions.items()
            if str(resolution.get("choice") or "") != "carry_forward"
        }

        # A directed resume is exactly one provider/critic pair. Only a fresh
        # mechanical schema or opaque-ID defect may receive one correction.
        max_provider_attempts = 1 if directives else 2
        accepted: dict[str, dict[str, Any]] = {}
        response: dict[str, Any] | None = None
        parse_errors: list[str] = []
        placement_evidence: list[dict[str, Any]] = []
        legacy_relationship_mode = False
        for attempt in range(1, max_provider_attempts + 1):
            payload = {
                "metadata": copy.deepcopy(graph.get("metadata") or {}),
                "topics": copy.deepcopy(topics),
                "concepts": copy.deepcopy(batch),
                "batch": batch_index,
                "attempt": attempt,
                "max_attempts": max_provider_attempts,
                "previous_decisions": [],
                "critic_feedback": (
                    {
                        "verdict": "provider_contract_rejected",
                        "issues": copy.deepcopy(parse_errors),
                        "rejected_concept_ids": sorted(batch_ids),
                    }
                    if parse_errors
                    else {}
                ),
            }
            if directives:
                payload["human_resolutions"] = [
                    copy.deepcopy(directives[concept_id])
                    for concept_id in sorted(directives)
                ]
            response = provider(copy.deepcopy(payload))
            legacy_relationship_mode = bool(
                explicit_provider
                and config.allow_dry()
                and _response_omits_all_relationships(response)
            )
            if not legacy_relationship_mode:
                response, placement_evidence, placement_errors = (
                    _prepare_placement_review(
                        response,
                        payload=payload,
                        concepts={
                            concept_id: concept_by_id[concept_id]
                            for concept_id in batch_ids
                        },
                        graph=graph,
                        canonical=canonical,
                    )
                )
                if placement_errors:
                    parse_errors = placement_errors
                    parsed = {}
                else:
                    parsed, parse_errors = _parse_decisions(
                        response,
                        concepts={
                            concept_id: concept_by_id[concept_id]
                            for concept_id in batch_ids
                        },
                        topic_ids=topic_ids,
                    )
            else:
                parsed, parse_errors = _parse_decisions(
                    response,
                    concepts={
                        concept_id: concept_by_id[concept_id]
                        for concept_id in batch_ids
                    },
                    topic_ids=topic_ids,
                )
            review_band_ids = [
                concept_id
                for concept_id, decision in parsed.items()
                if early_gate.confidence_band(decision.get("confidence"))
                == "human_review"
                or any(
                    early_gate.confidence_band(segment.get("confidence"))
                    == "human_review"
                    for segment in decision.get("segments") or []
                    if isinstance(segment, dict)
                )
            ]
            if not parse_errors and review_band_ids:
                parse_errors = [
                    f"{concept_id} topology confidence is in the "
                    "0.900–0.919 human-review band"
                    for concept_id in review_band_ids
                ]
            if parse_errors:
                if (
                    directives
                    or not _topology_parse_errors_are_mechanical(parse_errors)
                ):
                    concept_id = next(
                        (
                            value for value in sorted(batch_ids)
                            if any(value in error for error in parse_errors)
                        ),
                        sorted(batch_ids)[0],
                    )
                    raise HumanDecisionRequired(
                        _topology_pending_decision(
                            identity=gate_identities[concept_id],
                            graph=graph,
                            concept=concept_by_id[concept_id],
                            topics=topics,
                            candidates=gate_candidates[concept_id],
                            issues=parse_errors,
                            rejected_ids=sorted(batch_ids),
                            proposed_decision=_proposed_decision_for(
                                response, concept_id
                            ),
                            resolution=resolutions.get(concept_id),
                            diagnostic_source="provider",
                        )
                    )
                if attempt < max_provider_attempts:
                    progress.log(
                        "Phase 3.2 topology response had a mechanical "
                        "schema/opaque-ID defect; making its one bounded "
                        "contract correction.",
                        level="warning",
                    )
                    continue
                details = "; ".join(parse_errors[:6])
                raise ProviderResponseContractError(
                    "Phase 3.2 topology provider failed its response contract "
                    "after one bounded correction"
                    + (f": {details}" if details else "")
                )
            accepted = parsed
            break

        if set(accepted) != batch_ids:
            raise ProviderResponseContractError(
                "Phase 3.2 topology adjudication ended without every concept "
                "after its bounded provider contract correction"
            )

        directed_issues = _topology_directed_issues(directives, accepted)
        if directed_issues:
            concept_id = next(
                (
                    value for value in sorted(batch_ids)
                    if any(value in issue for issue in directed_issues)
                ),
                sorted(batch_ids)[0],
            )
            raise HumanDecisionRequired(
                _topology_pending_decision(
                    identity=gate_identities[concept_id],
                    graph=graph,
                    concept=concept_by_id[concept_id],
                    topics=topics,
                    candidates=gate_candidates[concept_id],
                    issues=directed_issues,
                    rejected_ids=sorted(batch_ids),
                    proposed_decision=accepted.get(concept_id),
                    resolution=resolutions.get(concept_id),
                    diagnostic_source="provider",
                )
            )

        review_payload = {
            "metadata": copy.deepcopy(graph.get("metadata") or {}),
            "topics": copy.deepcopy(topics),
            "concepts": copy.deepcopy(batch),
            "proposed_decisions": [
                copy.deepcopy(accepted[concept_id])
                for concept_id in sorted(batch_ids)
            ],
            "placement_relationship_evidence": copy.deepcopy(
                placement_evidence
            ),
            "split_review_attestations": list({
                str(attestation.get("split_review_id") or ""): copy.deepcopy(
                    attestation
                )
                for decision in accepted.values()
                for segment in decision.get("segments") or []
                if isinstance(segment, dict)
                for contract in [segment.get("_placement_contract")]
                if isinstance(contract, dict)
                for attestation in [contract.get("split_attestation")]
                if isinstance(attestation, dict)
                and attestation.get("split_review_id")
            }.values()),
        }
        if directives:
            review_payload["human_resolutions"] = [
                copy.deepcopy(directives[concept_id])
                for concept_id in sorted(directives)
            ]
        review = critic(copy.deepcopy(review_payload))
        review_gate = confidence_policy.ConfidenceGate.SEMANTIC
        state = phase31._review_state(
            review,
            concept_ids=batch_ids,
            confidence_gate=review_gate,
        )
        critic_review_band = (
            review_gate is confidence_policy.ConfidenceGate.SEMANTIC
            and early_gate.confidence_band(state["confidence"])
            == "human_review"
        )
        if not state["verified"] or critic_review_band:
            rejected = set(state["rejected"]) or set(batch_ids)
            issues = list(state["issues"]) or [
                "critic verdict was "
                + str(state.get("verdict") or "missing")
            ]
            confidence = float(state["confidence"])
            if early_gate.confidence_band(confidence) == "human_review":
                issues.insert(
                    0,
                    f"independent critic confidence {confidence:.3f} is in "
                    "the 0.900–0.919 human-review band",
                )
            rejected_order = sorted(rejected)
            progress.log(
                "Phase 3.2 stopped after the first genuine semantic topology "
                "disagreement; independently accepted IDs remain cached and "
                "no semantic retry or convergence pass was started.",
                level="warning",
            )
            # Every rejected concept in this batch is an independent conflict
            # with its own candidates and evidence. Raising them together lets
            # orchestration settle the whole batch on one replay instead of
            # paying a full pipeline replay per concept.
            packets = [
                _topology_pending_decision(
                    identity=gate_identities[concept_id],
                    graph=graph,
                    concept=concept_by_id[concept_id],
                    topics=topics,
                    candidates=gate_candidates[concept_id],
                    issues=issues,
                    rejected_ids=rejected_order,
                    proposed_decision=accepted.get(concept_id),
                    resolution=resolutions.get(concept_id),
                )
                for concept_id in rejected_order
            ]
            raise HumanDecisionRequired(packets[0], companions=packets[1:])
        if not legacy_relationship_mode:
            accepted, placement_review_errors = _finalize_certified_placements(
                accepted,
                review=review,
                payload=review_payload,
                concepts={
                    concept_id: concept_by_id[concept_id]
                    for concept_id in batch_ids
                },
                graph=graph,
            )
            if placement_review_errors:
                concept_id = next(
                    (
                        value for value in sorted(batch_ids)
                        if any(
                            value in issue for issue in placement_review_errors
                        )
                    ),
                    sorted(batch_ids)[0],
                )
                raise HumanDecisionRequired(
                    _topology_pending_decision(
                        identity=gate_identities[concept_id],
                        graph=graph,
                        concept=concept_by_id[concept_id],
                        topics=topics,
                        candidates=gate_candidates[concept_id],
                        issues=placement_review_errors,
                        rejected_ids=sorted(batch_ids),
                        proposed_decision=accepted.get(concept_id),
                        resolution=resolutions.get(concept_id),
                    )
                )
        else:
            # Compatibility is limited to an explicitly injected provider.
            # These rows are sealed only after exact grounding below; an
            # effective production provider can never enter this branch.
            legacy_concept_ids.update(batch_ids)
        progress.log(
            "Phase 3.2 independently verified topology decisions for "
            f"{len(batch_ids)} concept(s) in batch {batch_index}.",
            level="success",
        )
        decisions.update(accepted)

    repaired = _apply_decisions(
        out,
        concepts=concepts,
        index_by_id=index_by_id,
        decisions=decisions,
        graph=graph,
    )
    if legacy_concept_ids:
        # An empty provisional mapping is still "placement metadata" to the
        # grounding certificate and must fail closed.  Explicit legacy test
        # providers are the sole exception: remove only their empty placeholder
        # so Phase 3.1 can establish exact evidence, then seal the ordinary
        # certified contract immediately after grounding below.
        for record in repaired:
            if (
                str(record.get("_phase32_origin_concept_id") or "")
                in legacy_concept_ids
                and not record.get("_placement_contract")
            ):
                record.pop("_placement_contract", None)
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
    except early_gate.TopologyRepairRequired:
        raise
    except ValueError as exc:
        raise ValueError(
            "Phase 3.2 topology decisions passed independent topic review, but "
            "the repaired rows still failed exact source-block grounding before "
            f"freeze: {exc}"
        ) from exc

    grounded = _seal_legacy_injected_placements(
        grounded,
        legacy_concept_ids=legacy_concept_ids,
        concepts=concepts,
        graph=graph,
    )

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

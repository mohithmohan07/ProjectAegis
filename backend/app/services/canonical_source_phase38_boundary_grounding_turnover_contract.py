"""Phase 3.8 boundary-aware exact grounding and targeted topology turnover.

A live RNE acceptance run exposed one final convergence gap. A concept can be
correctly placed under a main topic while one continuation paragraph or Figure
is assigned to the adjacent graph topic because a converter/page reading order
crosses a main-heading boundary. Phase 3.1 previously supplied only blocks whose
graph ``topic_id`` exactly matched the concept. The grounding critic therefore
had to reject a valid concept even though the missing source block was immediately
beside the topic boundary.

This contract:

* keeps native topic blocks authoritative;
* adds a small, source-ordered window from the immediately adjacent topics as
  explicitly labelled boundary evidence;
* permits boundary evidence only when it visibly continues the target topic,
  never to excuse a genuinely cross-topic concept;
* gives exact grounding the same verified visual-page channel as topology review;
* maps a grounding failure back to the exact original topology concept and retries
  only that concept for move/refine/split/retire;
* allows several targeted convergence passes with cycle-aware instructions rather
  than replaying the whole chapter twice and stopping;
* invalidates cached final topology rows grounded under an older contract.

No textbook wording, QID, Figure, or source identity is invented or removed.
"""
from __future__ import annotations

import copy
import json
import os
import re
from contextvars import ContextVar
from functools import wraps
from typing import Any, Callable

from .. import config
from . import canonical_source_phase22 as phase22
from . import canonical_source_phase3 as phase3
from . import canonical_source_phase31_grounding_contract as phase31
from . import canonical_source_phase32_topology_adjudication_contract as phase32
from . import canonical_source_phase33_preflight_contract as phase33
from . import canonical_source_phase37_visual_topology_convergence_contract as phase37
from . import concept_refiner as cr
from . import progress

_CONTRACT_VERSION = 1
_GROUNDING_VERSION = "phase3.8-boundary-aware-source-grounding-1"

_LAST_REPAIRED_TOPOLOGY: ContextVar[list[dict[str, Any]] | None] = ContextVar(
    "aegis_phase38_last_repaired_topology",
    default=None,
)

_EXCLUDED_BLOCK_KINDS = frozenset({"layout", "heading", "navigation"})


def _boundary_block_limit() -> int:
    return max(
        1,
        min(
            32,
            int(os.environ.get("AEGIS_PHASE38_BOUNDARY_BLOCKS_PER_SIDE", "8")),
        ),
    )


def _max_convergence_passes() -> int:
    return max(
        2,
        min(
            12,
            int(os.environ.get("AEGIS_PHASE38_TOPOLOGY_CONVERGENCE_PASSES", "6")),
        ),
    )


def _normal(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def _semantic_blocks(graph: dict[str, Any]) -> list[dict[str, Any]]:
    return sorted(
        [
            row
            for row in graph.get("blocks") or []
            if isinstance(row, dict)
            and str(row.get("block_id") or "")
            and str(row.get("kind") or "") not in _EXCLUDED_BLOCK_KINDS
        ],
        key=lambda row: (
            int(row.get("order") or 0),
            int(row.get("source_start") or 0),
            str(row.get("block_id") or ""),
        ),
    )


def _contiguous_boundary_rows(
    rows: list[dict[str, Any]],
    *,
    reverse: bool,
) -> list[dict[str, Any]]:
    if not rows:
        return []
    ordered = list(reversed(rows)) if reverse else list(rows)
    adjacent_topic = str(ordered[0].get("topic_id") or "")
    if not adjacent_topic:
        return []
    selected: list[dict[str, Any]] = []
    for row in ordered:
        if str(row.get("topic_id") or "") != adjacent_topic:
            break
        selected.append(row)
        if len(selected) >= _boundary_block_limit():
            break
    if reverse:
        selected.reverse()
    return selected


def _candidate_payload_row(
    block: dict[str, Any],
    *,
    canonical_blocks: dict[str, dict[str, Any]],
    canonical: dict[str, Any],
    topic_titles: dict[str, str],
    target_topic_id: str,
    relation: str,
) -> dict[str, Any] | None:
    block_id = str(block.get("block_id") or "")
    source = canonical_blocks.get(block_id, {})
    text = phase37._evidence_text(block, source, canonical)
    if not block_id or not text:
        return None
    source_topic_id = str(block.get("topic_id") or "")
    return {
        "block_id": block_id,
        "kind": str(block.get("kind") or ""),
        "subtopic_id": str(block.get("subtopic_id") or ""),
        "figure_id": str(block.get("figure_id") or source.get("figure_id") or ""),
        "text": text,
        "source_order": int(block.get("order") or 0),
        "source_start": int(block.get("source_start") or 0),
        "source_topic_id": source_topic_id,
        "source_topic_title": topic_titles.get(source_topic_id, ""),
        "target_topic_id": target_topic_id,
        "boundary_relation": relation,
    }


def _candidate_blocks(
    graph: dict[str, Any],
    canonical: dict[str, Any],
    topic_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return native blocks plus a bounded immediate-neighbour evidence window."""
    native_usable, native_payload = (
        phase31._PHASE38_ORIGINAL_CANDIDATE_BLOCKS(
            graph,
            canonical,
            topic_id,
        )
    )
    ordered = _semantic_blocks(graph)
    native_positions = [
        index
        for index, row in enumerate(ordered)
        if str(row.get("topic_id") or "") == topic_id
    ]
    if not native_positions:
        return native_usable, native_payload

    first = min(native_positions)
    last = max(native_positions)
    before = _contiguous_boundary_rows(ordered[:first], reverse=True)
    after = _contiguous_boundary_rows(ordered[last + 1 :], reverse=False)

    canonical_blocks = {
        str(row.get("block_id") or ""): row
        for row in canonical.get("blocks") or []
        if isinstance(row, dict) and str(row.get("block_id") or "")
    }
    topic_titles = {
        str(row.get("topic_id") or ""): str(row.get("title") or "")
        for row in graph.get("topics") or []
        if isinstance(row, dict) and str(row.get("topic_id") or "")
    }

    native_by_id = {
        str(row.get("block_id") or ""): copy.deepcopy(row)
        for row in native_payload
        if isinstance(row, dict) and str(row.get("block_id") or "")
    }
    usable_by_id = {
        str(row.get("block_id") or ""): row
        for row in native_usable
        if isinstance(row, dict) and str(row.get("block_id") or "")
    }

    for block in ordered:
        block_id = str(block.get("block_id") or "")
        if block_id not in native_by_id:
            continue
        native_by_id[block_id].setdefault("source_order", int(block.get("order") or 0))
        native_by_id[block_id].setdefault(
            "source_start", int(block.get("source_start") or 0)
        )
        native_by_id[block_id].setdefault("source_topic_id", topic_id)
        native_by_id[block_id].setdefault(
            "source_topic_title", topic_titles.get(topic_id, "")
        )
        native_by_id[block_id].setdefault("target_topic_id", topic_id)
        native_by_id[block_id].setdefault("boundary_relation", "native_topic")

    for relation, rows in (
        ("previous_topic_boundary", before),
        ("next_topic_boundary", after),
    ):
        for block in rows:
            block_id = str(block.get("block_id") or "")
            if not block_id or block_id in native_by_id:
                continue
            payload = _candidate_payload_row(
                block,
                canonical_blocks=canonical_blocks,
                canonical=canonical,
                topic_titles=topic_titles,
                target_topic_id=topic_id,
                relation=relation,
            )
            if payload is None:
                continue
            native_by_id[block_id] = payload
            usable_by_id[block_id] = block

    source_order = {
        str(row.get("block_id") or ""): int(row.get("order") or 0)
        for row in ordered
    }
    ordered_ids = sorted(
        native_by_id,
        key=lambda block_id: (
            source_order.get(block_id, 10**9),
            block_id,
        ),
    )
    return (
        [usable_by_id[block_id] for block_id in ordered_ids if block_id in usable_by_id],
        [native_by_id[block_id] for block_id in ordered_ids],
    )


def _grounding_schema_ids(payload: dict[str, Any]) -> tuple[list[str], list[str]]:
    concept_ids = [
        str(row.get("concept_id") or "")
        for row in payload.get("concepts") or []
        if isinstance(row, dict) and str(row.get("concept_id") or "")
    ]
    block_ids = [
        str(row.get("block_id") or "")
        for row in payload.get("source_blocks") or []
        if isinstance(row, dict) and str(row.get("block_id") or "")
    ]
    return concept_ids, block_ids


def _augment_grounding_payload(
    payload: dict[str, Any],
    *,
    page_numbers: list[int],
) -> dict[str, Any]:
    value = copy.deepcopy(payload)
    value["boundary_grounding_contract"] = {
        "native_relation": "native_topic",
        "allowed_boundary_relations": [
            "previous_topic_boundary",
            "next_topic_boundary",
        ],
        "rule": (
            "An adjacent boundary block may be selected only when it visibly "
            "continues the target topic despite converter/page reading-order drift. "
            "It must not be used to keep a concept under the wrong academic topic."
        ),
        "repair_route": (
            "If the claim truly combines different topics, reject the mapping so "
            "topology turnover can move, refine, split, or retire the concept."
        ),
    }
    value["original_pdf_visual_page_ids"] = [
        f"PDF-PAGE-{number:04d}" for number in page_numbers
    ]
    return value


def _ground_via_openai(payload: dict[str, Any]) -> dict[str, Any]:
    concept_ids, block_ids = _grounding_schema_ids(payload)
    pages, page_numbers = phase37._visual_evidence_pages(payload)
    augmented = _augment_grounding_payload(
        payload,
        page_numbers=page_numbers,
    )
    system = (
        "You are the Aegis Phase 3.8 exact source-grounding mapper. Ground only "
        "each source_claim to the smallest sufficient set of supplied opaque "
        "source_block_ids. Blocks marked native_topic are ordinary evidence. "
        "Blocks marked previous_topic_boundary or next_topic_boundary are a "
        "bounded recovery window for converter/page-order drift and may be used "
        "only when their visible content clearly continues the target academic "
        "topic. Never use boundary evidence to conceal a genuinely cross-topic or "
        "over-merged concept. If the claim belongs elsewhere or needs narrowing or "
        "splitting, return a low-confidence mapping and explain that topology "
        "repair is required. Figure captions and supplied original PDF pages are "
        "authoritative visual evidence. Do not require textbook support for "
        "Achieving Mastery, learner analysis, Types, hubs, or other generated "
        "pedagogy. Use only supplied IDs, do not rewrite text, and return every "
        "requested concept exactly once. On retries, repair only unresolved IDs "
        "using critic_feedback and previous_grounding."
    )
    return phase22._openai_multimodal_json(
        system=system,
        prompt=json.dumps(augmented, ensure_ascii=False, indent=2),
        pages=pages,
        response_schema=phase31._grounding_schema(concept_ids, block_ids),
        purpose="concept_mapping",
        max_tokens=config.OPENAI_MAX_OUTPUT_TOKENS,
    )


def _critic_via_openai(payload: dict[str, Any]) -> dict[str, Any]:
    concept_ids, _block_ids = _grounding_schema_ids(payload)
    pages, page_numbers = phase37._visual_evidence_pages(payload)
    augmented = _augment_grounding_payload(
        payload,
        page_numbers=page_numbers,
    )
    system = (
        "You are the independent Aegis Phase 3.8 exact-grounding critic. Verify "
        "that every source_claim is fully and visibly supported by the proposed "
        "smallest sufficient block set. A selected adjacent boundary block is "
        "valid only when its content clearly belongs with the target topic and "
        "repairs local source-order drift. Reject a mapping when the boundary "
        "blocks instead show that the concept belongs to the adjacent topic, when "
        "the claim over-merges separate ideas, or when a selected Figure is the "
        "wrong visual. In that case state whether topology should move, refine, "
        "split, or retire the row. Figure captions and supplied original PDF pages "
        "are authoritative. Do not demand source support for mastery, learner "
        "analysis, Types, hubs, keywords, or parent labels. Put every concept ID "
        "in exactly one accepted or rejected list. Verification requires all "
        "accepted, none rejected, confidence at least 0.96, and no issues. Do not "
        "rewrite proposals."
    )
    return phase22._openai_multimodal_json(
        system=system,
        prompt=json.dumps(augmented, ensure_ascii=False, indent=2),
        pages=pages,
        response_schema=phase31._critic_schema(concept_ids),
        purpose="concept_mapping",
        max_tokens=config.OPENAI_MAX_OUTPUT_TOKENS,
    )


def _apply_proposals(
    records: list[dict[str, Any]],
    *,
    proposals: dict[str, dict[str, Any]],
    index_by_id: dict[str, int],
    candidates: list[dict[str, Any]],
) -> None:
    phase31._PHASE38_ORIGINAL_APPLY_PROPOSALS(
        records,
        proposals=proposals,
        index_by_id=index_by_id,
        candidates=candidates,
    )
    topic_by_block = {
        str(row.get("block_id") or ""): str(row.get("topic_id") or "")
        for row in candidates
        if isinstance(row, dict) and str(row.get("block_id") or "")
    }
    relation_by_block: dict[str, str] = {}
    target_topic_by_concept = {
        concept_id: str(records[index].get("_semantic_topic_id") or "")
        for concept_id, index in index_by_id.items()
    }
    for concept_id, proposal in proposals.items():
        index = index_by_id[concept_id]
        target_topic_id = target_topic_by_concept.get(concept_id, "")
        boundary_ids = [
            block_id
            for block_id in proposal.get("source_block_ids") or []
            if topic_by_block.get(str(block_id), target_topic_id) != target_topic_id
        ]
        if boundary_ids:
            for block_id in boundary_ids:
                source_topic = topic_by_block.get(str(block_id), "")
                relation_by_block[str(block_id)] = source_topic
            records[index]["_source_grounding_contract"] = (
                "api-verified-boundary-aware-source-block-ids"
            )
            records[index]["_source_grounding_boundary_blocks"] = [
                {
                    "block_id": str(block_id),
                    "source_topic_id": relation_by_block.get(str(block_id), ""),
                    "target_topic_id": target_topic_id,
                }
                for block_id in boundary_ids
            ]
        else:
            records[index].pop("_source_grounding_boundary_blocks", None)


def _capture_repaired_topology(*args: Any, **kwargs: Any):
    result = phase32._PHASE38_ORIGINAL_APPLY_DECISIONS(*args, **kwargs)
    _LAST_REPAIRED_TOPOLOGY.set(copy.deepcopy(result))
    return result


def _original_concept_directory(
    records: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    directory: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(records):
        title = str(row.get("concept_title") or row.get("concept") or "")
        if cr.is_culmination(title):
            continue
        concept_id = f"TOPOLOGY-CONCEPT-{index + 1:04d}"
        directory[concept_id] = {
            "concept_title": title,
            "topic": str(row.get("topic") or ""),
            "source_claim": phase31._description_source_claim(row),
        }
    return directory


def _feedback_for_failure(
    exc: Exception,
    *,
    original_records: list[dict[str, Any]],
    repaired_records: list[dict[str, Any]] | None,
    repeated: bool,
) -> dict[str, str]:
    origins: dict[str, str] = {}
    if repaired_records:
        origins = phase33._grounding_feedback_origins(
            exc,
            records=repaired_records,
        )
    directory = _original_concept_directory(original_records)
    if not origins:
        topic_match = phase33._GROUNDING_TOPIC_RE.search(str(exc))
        if topic_match:
            topic_key = _normal(topic_match.group("topic"))
            origins = {
                concept_id: str(exc)[:12000]
                for concept_id, row in directory.items()
                if _normal(row.get("topic")) == topic_key
            }
    if not origins:
        origins = {
            concept_id: str(exc)[:12000]
            for concept_id in directory
        }

    instruction = (
        "EXACT SOURCE-BLOCK GROUNDING FAILED. Reconsider this original concept "
        "using all canonical topic evidence, adjacent boundary evidence, Figure "
        "captions, and supplied original PDF pages. Do not repeat an unchanged "
        "placement and source_claim when the diagnostic says one or more clauses "
        "lack support. Preserve the concept only if all clauses are now supported "
        "in the academically correct topic; otherwise move, refine, split, or "
        "retire it under the verified topology contract."
    )
    if repeated:
        instruction += (
            " This failure signature has repeated. Returning the same effective "
            "title/topic/Description is forbidden; choose a materially different "
            "evidence-supported resolution."
        )

    feedback: dict[str, str] = {}
    for concept_id, diagnostic in origins.items():
        row = directory.get(concept_id, {})
        feedback[concept_id] = (
            f"{instruction}\n"
            f"Original concept title: {row.get('concept_title') or '(unknown)'}\n"
            f"Original topic: {row.get('topic') or '(unknown)'}\n"
            f"Original source claim: {row.get('source_claim') or '(unknown)'}\n"
            f"Exact grounding diagnostic: {str(diagnostic)[:12000]}"
        )
    return feedback


def _phase32_adjudicate_with_targeted_convergence(
    original: Callable[..., list[dict[str, Any]]],
    records: list[dict[str, Any]],
    *args: Any,
    **kwargs: Any,
) -> list[dict[str, Any]]:
    if any(
        kwargs.get(name) is not None
        for name in ("provider", "critic", "grounding_provider", "grounding_critic")
    ):
        return original(records, *args, **kwargs)

    passes = _max_convergence_passes()
    feedback: dict[str, str] = {}
    signatures: dict[str, int] = {}
    repaired_token = _LAST_REPAIRED_TOPOLOGY.set(None)
    try:
        for convergence_pass in range(1, passes + 1):
            _LAST_REPAIRED_TOPOLOGY.set(None)
            feedback_token = phase33._EXTERNAL_GROUNDING_FEEDBACK.set(feedback)
            try:
                return original(records, *args, **kwargs)
            except ValueError as exc:
                message = str(exc)
                if "failed exact source-block grounding before freeze" not in message:
                    raise
                signature = phase3._sha256_json(
                    {
                        "message": _normal(message),
                        "feedback_ids": sorted(feedback),
                    }
                )
                signatures[signature] = signatures.get(signature, 0) + 1
                repeated = signatures[signature] > 1
                if convergence_pass >= passes:
                    raise
                repaired = _LAST_REPAIRED_TOPOLOGY.get()
                feedback = _feedback_for_failure(
                    exc,
                    original_records=records,
                    repaired_records=repaired,
                    repeated=repeated,
                )
                progress.log(
                    "Phase 3.8 mapped exact grounding rejection back to "
                    f"{len(feedback)} original topology concept(s); only those "
                    "concepts will be reconsidered in convergence pass "
                    f"{convergence_pass + 1}/{passes}.",
                    level="warning",
                )
            finally:
                phase33._EXTERNAL_GROUNDING_FEEDBACK.reset(feedback_token)
    finally:
        _LAST_REPAIRED_TOPOLOGY.reset(repaired_token)
    raise RuntimeError("Phase 3.8 topology convergence ended unexpectedly")


def _cached_records_have_current_grounding(
    records: list[dict[str, Any]] | None,
) -> bool:
    if not isinstance(records, list) or not records:
        return False
    normal = [
        row
        for row in records
        if isinstance(row, dict)
        and not cr.is_culmination(
            str(row.get("concept_title") or row.get("concept") or "")
        )
    ]
    return bool(normal) and all(
        str(row.get("_source_grounding_version") or "") == _GROUNDING_VERSION
        for row in normal
    )


def _read_cached_records(cache_key: str) -> list[dict[str, Any]] | None:
    records = phase32._PHASE38_ORIGINAL_READ_CACHED_RECORDS(cache_key)
    if records is None:
        return None
    if _cached_records_have_current_grounding(records):
        return records
    progress.log(
        "Ignored a cached Phase 3.2 final topology grounded under an older "
        "source-block contract; verified topology decisions remain reusable.",
        level="warning",
    )
    return None


def install() -> None:
    if getattr(phase31, "_PHASE38_BOUNDARY_GROUNDING_VERSION", 0) >= _CONTRACT_VERSION:
        return

    phase31._PHASE38_ORIGINAL_CANDIDATE_BLOCKS = phase31._candidate_blocks
    phase31._PHASE38_ORIGINAL_GROUND_PROVIDER = phase31._ground_via_openai
    phase31._PHASE38_ORIGINAL_GROUND_CRITIC = phase31._critic_via_openai
    phase31._PHASE38_ORIGINAL_APPLY_PROPOSALS = phase31._apply_proposals
    phase32._PHASE38_ORIGINAL_APPLY_DECISIONS = phase32._apply_decisions
    phase32._PHASE38_ORIGINAL_READ_CACHED_RECORDS = phase32._read_cached_records
    phase33._PHASE38_ORIGINAL_CONVERGENCE = (
        phase33._phase32_adjudicate_with_convergence
    )

    phase31._GROUNDING_VERSION = _GROUNDING_VERSION
    phase31._candidate_blocks = _candidate_blocks
    phase31._ground_via_openai = _ground_via_openai
    phase31._critic_via_openai = _critic_via_openai
    phase31._apply_proposals = _apply_proposals

    phase32._apply_decisions = _capture_repaired_topology
    phase32._read_cached_records = _read_cached_records
    phase33._phase32_adjudicate_with_convergence = (
        _phase32_adjudicate_with_targeted_convergence
    )

    phase31._PHASE38_BOUNDARY_GROUNDING_VERSION = _CONTRACT_VERSION

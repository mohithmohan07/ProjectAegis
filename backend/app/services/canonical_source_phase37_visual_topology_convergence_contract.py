r"""Phase 3.7 visual-evidence topology convergence.

The full-PDF lane already verifies every original page, but the concept-topology
router historically received only cleaned text blocks.  ``\caption{...}`` is
layout syntax, so cleaning a Figure block removed the very caption that explains
maps, cartoons, diagrams, paintings, and source images.  A visual concept could
therefore be generated from the semantic source and then rejected by Phase 3.2
because its own grounding packet no longer contained the Figure evidence.

This contract makes source-visible visual evidence first-class throughout the
topology, exact-grounding, and Type-host lanes.  It also adds one safe convergence
outcome for a genuinely unsupported duplicate: ``retire``.  Retirement is
independently reviewed, happens before topology freeze and Type allocation, and
is permitted only when no distinct source-supported teaching objective is lost.
The later Type-host preflight remains authoritative and will create a necessary
source-grounded concept if a mined method would otherwise be left without a host.
Visual packets retain both source openings and caption-rich endings within a
bounded per-block view; the exact canonical blocks remain unchanged.
"""
from __future__ import annotations

import copy
import json
import re
import time
from contextvars import ContextVar
from functools import wraps
from pathlib import Path
from typing import Any

from .. import config
from . import canonical_source_phase2 as phase2
from . import canonical_source_phase22 as phase22
from . import canonical_source_phase221_fallback as page_acsd
from . import canonical_source_phase3 as phase3
from . import canonical_source_phase31_grounding_contract as phase31
from . import canonical_source_phase32_topology_adjudication_contract as phase32
from . import canonical_source_phase33_preflight_contract as phase33
from . import concept_refiner as cr
from . import progress
from . import semantic_confidence_policy as confidence_policy

_CONTRACT_VERSION = 1
_TOPOLOGY_VERSION = "phase3.7-visual-evidence-retirement-1"
_DECISION_CACHE_VERSION = "phase3.7-visual-evidence-retirement-decision-cache-1"
_RETIREMENT_FILENAME = "source.phase37-topology-retirements.json"

_ACTIVE_CONCEPT_DIRECTORY: ContextVar[dict[str, dict[str, Any]]] = ContextVar(
    "aegis_phase37_concept_directory",
    default={},
)
_PENDING_RETIREMENT_AUDIT: ContextVar[dict[str, Any] | None] = ContextVar(
    "aegis_phase37_pending_retirement_audit",
    default=None,
)

_WORD_RE = re.compile(r"[^\W_]{3,}", re.UNICODE)
_VISUAL_TERMS = frozenset({
    "allegory", "artwork", "caption", "caricature", "cartoon", "chart",
    "depict", "depiction", "diagram", "figure", "fig", "illustration",
    "image", "map", "painting", "photograph", "picture", "plaque", "print",
    "scene", "symbol", "symbols", "table", "timeline", "visual",
})
_STOPWORDS = frozenset({
    "about", "against", "also", "among", "because", "been", "before",
    "between", "could", "from", "have", "into", "more", "other", "over",
    "same", "source", "their", "there", "these", "this", "through", "under",
    "using", "which", "with", "within",
})


def _normal(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def _tokens(value: object) -> set[str]:
    return {
        token.casefold()
        for token in _WORD_RE.findall(str(value or ""))
        if token.casefold() not in _STOPWORDS
    }


def _balanced_command_bodies(value: object, command: str) -> list[str]:
    """Return every balanced braced body for one LaTeX command."""
    text = str(value or "")
    pattern = re.compile(
        rf"\\{re.escape(command)}(?:\[[^\]]*\])?\s*\{{",
        re.IGNORECASE,
    )
    bodies: list[str] = []
    cursor = 0
    while True:
        match = pattern.search(text, cursor)
        if match is None:
            break
        brace = text.find("{", match.start(), match.end())
        if brace < 0:
            break
        depth = 0
        index = brace
        end: int | None = None
        while index < len(text):
            character = text[index]
            if character == "\\":
                index += 2
                continue
            if character == "{":
                depth += 1
            elif character == "}":
                depth -= 1
                if depth == 0:
                    end = index + 1
                    break
            index += 1
        if end is None:
            break
        bodies.append(text[brace + 1 : end - 1])
        cursor = end
    return bodies


def _append_evidence(parts: list[str], label: str, value: object) -> None:
    cleaned = phase3._clean_public_text(value)
    if not cleaned:
        return
    key = _normal(cleaned)
    if any(key and key in _normal(existing) for existing in parts):
        return
    parts.append(f"[{label}] {cleaned}")


def _evidence_text(
    graph_block: dict[str, Any],
    canonical_block: dict[str, Any],
    canonical: dict[str, Any],
) -> str:
    """Return learner-visible block evidence, including source-owned captions."""
    raw = phase3._graph_block_text(graph_block, canonical_block)
    parts: list[str] = []
    _append_evidence(parts, "Source text", raw)

    # A raw Figure/Table block can carry a caption even when no registry row was
    # materialized. Preserve the balanced source body before layout commands are
    # stripped by the ordinary public-text cleaner.
    for caption in _balanced_command_bodies(raw, "caption"):
        _append_evidence(parts, "Source caption", caption)

    figure_id = str(
        graph_block.get("figure_id")
        or canonical_block.get("figure_id")
        or ""
    )
    figures = {
        str(row.get("figure_id") or ""): row
        for row in canonical.get("figures") or []
        if isinstance(row, dict) and str(row.get("figure_id") or "")
    }
    images = {
        str(row.get("image_id") or ""): row
        for row in canonical.get("images") or []
        if isinstance(row, dict) and str(row.get("image_id") or "")
    }
    figure = figures.get(figure_id, {})
    if figure:
        _append_evidence(parts, "Verified Figure caption", figure.get("caption_raw"))
        references = [
            str(value).strip()
            for value in figure.get("reference_ids") or []
            if str(value).strip()
        ]
        if references:
            _append_evidence(
                parts,
                "Canonical Figure reference",
                ", ".join(references),
            )
        for image_id in figure.get("image_ids") or []:
            image = images.get(str(image_id), {})
            _append_evidence(parts, "Source image alt text", image.get("alt_raw"))

    # Verified GPT page ACSD and deterministic ACSD can attach a caption directly
    # to the canonical block in addition to the Figure registry.
    _append_evidence(parts, "Verified visual caption", canonical_block.get("caption"))
    _append_evidence(parts, "Verified visual caption", graph_block.get("caption"))
    return "\n".join(parts).strip()


def _bounded_visual_evidence(value: object, *, limit: int) -> str:
    """Bound a repeated packet while retaining source and visual-caption cues."""
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    marker = "\n[AEGIS BOUNDED VISUAL EVIDENCE]\n"
    available = max(2, limit - len(marker))
    tail = max(1, available // 3)
    head = max(1, available - tail)
    return text[:head].rstrip() + marker + text[-tail:].lstrip()


def _graph_topic_excerpts(
    graph: dict[str, Any] | None = None,
    canonical: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    graph = graph or phase3.active_graph()
    if not isinstance(graph, dict):
        return []
    canonical = canonical or (phase3.active_session() or {}).get("canonical") or {}
    blocks = {
        str(row.get("block_id") or ""): row
        for row in canonical.get("blocks") or []
        if isinstance(row, dict)
    }
    output: list[dict[str, str]] = []
    for topic in graph.get("topics") or []:
        if not isinstance(topic, dict):
            continue
        topic_id = str(topic.get("topic_id") or "")
        pieces: list[str] = []
        for block in graph.get("blocks") or []:
            if (
                not isinstance(block, dict)
                or str(block.get("topic_id") or "") != topic_id
                or str(block.get("kind") or "") in {
                    "layout",
                    "heading",
                    "navigation",
                }
            ):
                continue
            block_id = str(block.get("block_id") or "")
            text = _evidence_text(block, blocks.get(block_id, {}), canonical)
            if text:
                pieces.append(text)
        output.append(
            {
                "topic": str(topic.get("title") or ""),
                "excerpt": "\n\n".join(pieces),
            }
        )
    return output


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
        block_id = str(block.get("block_id") or "")
        source = canonical_blocks.get(block_id, {})
        text = _evidence_text(block, source, canonical)
        if not text:
            continue
        usable.append(block)
        figure_id = str(block.get("figure_id") or source.get("figure_id") or "")
        payload.append(
            {
                "block_id": block_id,
                "kind": str(block.get("kind") or ""),
                "subtopic_id": str(block.get("subtopic_id") or ""),
                "figure_id": figure_id,
                "text": _bounded_visual_evidence(text, limit=3000),
            }
        )
    return usable, payload


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
        and str(row.get("kind") or "") not in {
            "layout",
            "heading",
            "navigation",
        }
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
    limit = phase32._topic_evidence_limit()
    output: list[dict[str, Any]] = []
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
            text = _evidence_text(block, source, canonical)
            if not text:
                continue
            figure_id = str(block.get("figure_id") or source.get("figure_id") or "")
            figure_suffix = f" | {figure_id}" if figure_id else ""
            rendered = (
                f"[{block_id} | "
                f"{str(block.get('subtopic_id') or 'NO-SUBTOPIC')} | "
                f"{str(block.get('kind') or 'content')}{figure_suffix}] "
                f"{_bounded_visual_evidence(text, limit=3000)}"
            )
            if used + len(rendered) > limit and pieces:
                omitted += 1
                continue
            pieces.append(rendered)
            used += len(rendered)
        evidence = "\n\n".join(pieces)
        if omitted:
            evidence += (
                f"\n\n[AEGIS NOTE: {omitted} later block(s) exceeded the "
                "bounded visual topic-routing packet. Exact Phase 3.1 block "
                "grounding remains mandatory before topology freeze.]"
            )
        output.append(
            {
                "topic_id": topic_id,
                "order": int(topic.get("order") or 0),
                "title": str(topic.get("title") or ""),
                "structural_number": str(topic.get("structural_number") or ""),
                "evidence": evidence,
            }
        )
    return output, block_order


def _topic_source_blocks(
    graph: dict[str, Any],
    canonical: dict[str, Any],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, str]]:
    canonical_blocks = {
        str(row.get("block_id") or ""): row
        for row in canonical.get("blocks") or []
        if isinstance(row, dict)
    }
    blocks_by_topic: dict[str, list[dict[str, Any]]] = {}
    subtopic_by_block: dict[str, str] = {}
    for block in graph.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        if str(block.get("kind") or "") in {
            "layout",
            "heading",
            "navigation",
        }:
            continue
        block_id = str(block.get("block_id") or "")
        topic_id = str(block.get("topic_id") or "")
        source = canonical_blocks.get(block_id, {})
        text = _evidence_text(block, source, canonical)
        if not block_id or not topic_id or not text:
            continue
        subtopic = str(block.get("subtopic_id") or "")
        subtopic_by_block[block_id] = subtopic
        page_number = phase33._source_block_page_number(block, source)
        blocks_by_topic.setdefault(topic_id, []).append(
            {
                "block_id": block_id,
                "kind": str(block.get("kind") or ""),
                "subtopic_id": subtopic,
                "page_number": page_number,
                "figure_id": str(
                    block.get("figure_id") or source.get("figure_id") or ""
                ),
                "text": _bounded_visual_evidence(text, limit=3000),
            }
        )
    return blocks_by_topic, subtopic_by_block


def _load_page_bundle() -> dict[str, Any]:
    session = phase3.active_session()
    if not isinstance(session, dict) or not session.get("artifact_dir"):
        return {}
    path = Path(session["artifact_dir"]) / phase3.VISION_ACSD_FILENAME
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _page_search_text(page: dict[str, Any]) -> str:
    pieces: list[str] = []
    for block in page.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        for key in ("source_label", "text", "caption", "latex"):
            value = str(block.get(key) or "").strip()
            if value:
                pieces.append(value)
        for row in block.get("table_rows") or []:
            if isinstance(row, list):
                pieces.extend(str(cell) for cell in row if str(cell).strip())
    return " ".join(pieces)


def _relevant_visual_page_numbers(
    payload: dict[str, Any],
    bundle: dict[str, Any],
    *,
    limit: int = 8,
) -> list[int]:
    query = " ".join(
        str(row.get(key) or "")
        for row in payload.get("concepts") or []
        if isinstance(row, dict)
        for key in ("concept_title", "source_claim", "current_topic_title")
    )
    query_key = _normal(query)
    query_tokens = _tokens(query)
    visual_query_terms = {
        term for term in _VISUAL_TERMS if term in query_key
    }
    if not visual_query_terms:
        return []

    ranked: list[tuple[int, int]] = []
    figure_pages: list[int] = []
    for page in bundle.get("pages") or []:
        if not isinstance(page, dict):
            continue
        page_number = int(page.get("page_number") or 0)
        if page_number <= 0:
            continue
        blocks = [
            row for row in page.get("blocks") or [] if isinstance(row, dict)
        ]
        if not any(str(row.get("kind") or "") == "figure" for row in blocks):
            continue
        figure_pages.append(page_number)
        page_text = _page_search_text(page)
        page_key = _normal(page_text)
        overlap = len(query_tokens & _tokens(page_text))
        term_hits = sum(
            1 for term in visual_query_terms if term in page_key
        )
        score = overlap + (term_hits * 8)
        if score > 0:
            ranked.append((score, page_number))

    ranked.sort(key=lambda row: (-row[0], row[1]))
    selected = [number for _score, number in ranked[:limit]]
    if selected:
        return selected

    # Last-resort visual review remains bounded. It is used only when the concept
    # explicitly claims visual meaning but text-layer/caption retrieval produced
    # no lexical match.
    return sorted(dict.fromkeys(figure_pages))[:limit]


def _visual_evidence_pages(
    payload: dict[str, Any],
) -> tuple[list[phase22.EvidencePage], list[int]]:
    session = phase3.active_session()
    if not isinstance(session, dict):
        return [], []
    source_path = session.get("source_path")
    if not isinstance(source_path, Path):
        try:
            source_path = Path(source_path)
        except TypeError:
            return [], []
    if not source_path.exists() or source_path.suffix.lower() != ".pdf":
        return [], []
    bundle = _load_page_bundle()
    page_numbers = _relevant_visual_page_numbers(payload, bundle)
    if not page_numbers:
        return [], []
    try:
        pages = phase3._anomaly_evidence_pages(source_path, page_numbers)
    except Exception as exc:
        progress.log(
            "Phase 3.7 could not render selected visual evidence pages "
            f"({exc}); verified source captions remain available.",
            level="warning",
        )
        return [], []
    return pages, page_numbers


def _concept_directory_rows() -> list[dict[str, Any]]:
    directory = _ACTIVE_CONCEPT_DIRECTORY.get() or {}
    return [
        copy.deepcopy(directory[key])
        for key in sorted(directory)
    ]


def _augment_topology_payload(
    payload: dict[str, Any],
    *,
    page_numbers: list[int],
) -> dict[str, Any]:
    value = copy.deepcopy(payload)
    value["chapter_concept_directory"] = _concept_directory_rows()
    value["original_pdf_visual_page_ids"] = [
        f"PDF-PAGE-{number:04d}" for number in page_numbers
    ]
    value["retirement_contract"] = {
        "decision": "retire",
        "segments": [],
        "retire_into_concept_id": (
            "Use an existing normal concept ID only when it fully subsumes every "
            "source-supported part of the retiring row; otherwise use NONE only "
            "when the row has no distinct source-supported teaching objective."
        ),
        "guardrail": (
            "Prefer keep, move, refine, or split whenever source text, a Figure "
            "caption, or an original PDF image supports a durable concept. Never "
            "retire a row merely because confidence is low or visual text is hard "
            "to read."
        ),
    }
    return value


def _adjudication_schema(
    concept_ids: list[str],
    topic_ids: list[str],
) -> dict[str, Any]:
    schema = copy.deepcopy(
        phase32._PHASE37_ORIGINAL_ADJUDICATION_SCHEMA(concept_ids, topic_ids)
    )
    directory_ids = sorted(
        set(_ACTIVE_CONCEPT_DIRECTORY.get() or {}) | set(concept_ids)
    )
    item = (
        schema["schema"]["properties"]["concepts"]["items"]
    )
    item["properties"]["retire_into_concept_id"] = {
        "type": "string",
        "enum": ["NONE", *directory_ids],
    }
    if "retire_into_concept_id" not in item["required"]:
        item["required"].append("retire_into_concept_id")
    return schema


def _adjudicate_via_openai(payload: dict[str, Any]) -> dict[str, Any]:
    concept_ids = [
        str(row.get("concept_id") or "")
        for row in payload.get("concepts") or []
    ]
    topic_ids = [
        str(row.get("topic_id") or "")
        for row in payload.get("topics") or []
    ]
    pages, page_numbers = _visual_evidence_pages(payload)
    augmented = _augment_topology_payload(
        payload,
        page_numbers=page_numbers,
    )
    system = (
        "You are the Aegis Phase 3.7 source-verified concept-topology "
        "adjudicator. Compare every requested concept against all canonical topic "
        "evidence, verified Figure captions, and any supplied original PDF page "
        "images. Choose exactly one decision. keep preserves a fully supported "
        "concept in its topic. move preserves the complete concept in a different "
        "topic. refine narrows unsupported wording while retaining one durable "
        "idea. split creates two to four independently teachable source-supported "
        "ideas. retire removes a row only when it has no distinct durable "
        "source-supported objective, or when every supported part is fully "
        "subsumed by one existing normal concept named in retire_into_concept_id. "
        "For retire return zero segments. Use retire_into_concept_id=NONE only "
        "for a source-unsupported over-inference with no unique idea. For every "
        "other decision use retire_into_concept_id=NONE. A Figure caption and "
        "legible original-page visual are authoritative source evidence. Prefer "
        "refining a visual concept to what the caption/image actually supports "
        "rather than retiring it. Never retire because confidence is low. For "
        "keep/move/refine return one segment; for split return two to four. Use "
        "only supplied opaque IDs. Return every requested concept exactly once. "
        "On retries repair only rejected IDs using previous_decisions and "
        "critic_feedback. If human_resolutions is supplied, apply its selected "
        "refine/move/split/keep direction or custom instruction exactly, then "
        "return the ordinary proposal for independent criticism; the human "
        "direction is not verification."
    )
    return phase22._openai_multimodal_json(
        system=system,
        prompt=json.dumps(augmented, ensure_ascii=False, indent=2),
        pages=pages,
        response_schema=_adjudication_schema(concept_ids, topic_ids),
        purpose="concept_mapping",
        max_tokens=config.OPENAI_MAX_OUTPUT_TOKENS,
    )


def _critic_via_openai(payload: dict[str, Any]) -> dict[str, Any]:
    concept_ids = [
        str(row.get("concept_id") or "")
        for row in payload.get("concepts") or []
    ]
    pages, page_numbers = _visual_evidence_pages(payload)
    augmented = _augment_topology_payload(
        payload,
        page_numbers=page_numbers,
    )
    system = (
        "You are the independent Aegis Phase 3.7 topology critic. Verify every "
        "keep, move, refine, split, or retire decision against canonical text, "
        "verified Figure captions, and supplied original PDF page images. A "
        "retire decision is valid only when no distinct source-supported teaching "
        "objective is lost. If retire_into_concept_id names a concept, verify that "
        "the target fully subsumes every supported part of the retiring row. If it "
        "is NONE, verify that the row is an unsupported over-inference rather than "
        "a weakly worded but recoverable concept. Reject retirement when a caption "
        "or visual supports a distinct idea, when refinement is possible, when "
        "the target is itself retiring, or when a mined question method may need "
        "the row as a durable host. For non-retire decisions, apply the existing "
        "source-support, granularity, topic, duplication, and mastery rules. Put "
        "every concept ID in exactly one accepted or rejected list. For batches "
        "containing only non-retire decisions, a verified verdict requires all "
        "accepted, none rejected, confidence at least "
        f"{confidence_policy.threshold_text()}, and no issues. Any batch "
        "approving a retire decision requires confidence at least "
        f"{confidence_policy.threshold_text(confidence_policy.ConfidenceGate.DESTRUCTIVE)}, "
        "all accepted, none rejected, and no issues. Do not rewrite proposals."
    )
    return phase22._openai_multimodal_json(
        system=system,
        prompt=json.dumps(augmented, ensure_ascii=False, indent=2),
        pages=pages,
        response_schema=phase32._critic_schema(concept_ids),
        purpose="concept_mapping",
        max_tokens=config.OPENAI_MAX_OUTPUT_TOKENS,
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

    counts: dict[str, int] = {}
    for row in rows:
        if isinstance(row, dict):
            concept_id = str(row.get("concept_id") or "")
            counts[concept_id] = counts.get(concept_id, 0) + 1
    duplicate_ids = {
        concept_id for concept_id, count in counts.items() if concept_id and count > 1
    }

    directory = _ACTIVE_CONCEPT_DIRECTORY.get() or {}
    retiring_ids = {
        str(row.get("concept_id") or "")
        for row in rows
        if isinstance(row, dict)
        and str(row.get("decision") or "") == "retire"
    }
    retirement_proposals: dict[str, dict[str, Any]] = {}
    retirement_errors: list[str] = []
    filtered_rows: list[dict[str, Any]] = []

    for row in rows:
        if not isinstance(row, dict):
            filtered_rows.append(row)
            continue
        concept_id = str(row.get("concept_id") or "")
        decision = str(row.get("decision") or "")
        target = str(row.get("retire_into_concept_id") or "NONE")
        if decision != "retire":
            if target != "NONE":
                retirement_errors.append(
                    f"{concept_id or '<empty>'} used retire_into_concept_id "
                    "without a retire decision"
                )
            filtered_rows.append(copy.deepcopy(row))
            continue

        if concept_id in duplicate_ids:
            retirement_errors.append(f"duplicate concept ID {concept_id}")
            continue
        if concept_id not in concepts:
            retirement_errors.append(
                f"unknown concept ID {concept_id or '<empty>'}"
            )
            continue
        confidence = float(row.get("confidence") or 0.0)
        segments = row.get("segments")
        reason = str(row.get("reason") or "").strip()
        if not confidence_policy.accepts(
            confidence,
            confidence_policy.ConfidenceGate.DESTRUCTIVE,
        ):
            retirement_errors.append(
                f"{concept_id} retirement confidence {confidence:.3f} "
                "is below "
                f"{confidence_policy.threshold_text(confidence_policy.ConfidenceGate.DESTRUCTIVE)}"
            )
            continue
        if not isinstance(segments, list) or segments:
            retirement_errors.append(
                f"{concept_id} retire decision must return zero segments"
            )
            continue
        if not reason:
            retirement_errors.append(
                f"{concept_id} retire decision omitted its evidence reason"
            )
            continue
        if target == concept_id:
            retirement_errors.append(
                f"{concept_id} cannot retire into itself"
            )
            continue
        if target != "NONE" and target not in directory:
            retirement_errors.append(
                f"{concept_id} retire decision used unknown target {target}"
            )
            continue
        if target in retiring_ids:
            retirement_errors.append(
                f"{concept_id} cannot retire into another retiring concept {target}"
            )
            continue
        retirement_proposals[concept_id] = {
            "concept_id": concept_id,
            "decision": "retire",
            "segments": [],
            "retire_into_concept_id": target,
            "confidence": confidence,
            "reason": reason,
        }

    remaining_concepts = {
        concept_id: concept
        for concept_id, concept in concepts.items()
        if concept_id not in retirement_proposals
    }
    parsed, errors = phase32._PHASE37_ORIGINAL_PARSE_DECISIONS(
        {"concepts": filtered_rows},
        concepts=remaining_concepts,
        topic_ids=topic_ids,
    )
    errors.extend(retirement_errors)
    overlap = set(parsed) & set(retirement_proposals)
    if overlap:
        errors.append(
            "concept ID(s) received both retire and non-retire decisions: "
            + ", ".join(sorted(overlap))
        )
    parsed.update(retirement_proposals)
    missing = sorted(set(concepts) - set(parsed))
    if missing and not any(
        "missing or invalid concept ID(s)" in error for error in errors
    ):
        errors.append("missing or invalid concept ID(s): " + ", ".join(missing))
    return parsed, list(dict.fromkeys(errors))


def _retirement_audit_value(
    records: list[dict[str, Any]],
    *,
    concepts: list[dict[str, Any]],
    decisions: dict[str, dict[str, Any]],
    graph: dict[str, Any],
) -> dict[str, Any]:
    concept_by_id = {
        str(row.get("concept_id") or ""): row
        for row in concepts
        if isinstance(row, dict)
    }
    retired = [
        {
            "concept_id": concept_id,
            "concept_title": str(
                concept_by_id.get(concept_id, {}).get("concept_title") or ""
            ),
            "current_topic_id": str(
                concept_by_id.get(concept_id, {}).get("current_topic_id") or ""
            ),
            "retire_into_concept_id": str(
                decision.get("retire_into_concept_id") or "NONE"
            ),
            "confidence": float(decision.get("confidence") or 0.0),
            "reason": str(decision.get("reason") or ""),
        }
        for concept_id, decision in sorted(decisions.items())
        if decision.get("decision") == "retire"
    ]
    if not retired:
        return {}
    return {
        "version": _TOPOLOGY_VERSION,
        "status": "verified_and_grounded",
        "created_at": time.time(),
        "source_contract_hash": str(graph.get("source_contract_hash") or ""),
        "input_row_count": len(records),
        "retired_count": len(retired),
        "retired": retired,
    }


def _write_retirement_audit(value: dict[str, Any]) -> None:
    if not value:
        return
    session = phase3.active_session()
    if not isinstance(session, dict) or not session.get("artifact_dir"):
        return
    path = Path(session["artifact_dir"]) / _RETIREMENT_FILENAME
    phase3._atomic_write(
        path,
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def _apply_decisions(
    records: list[dict[str, Any]],
    *,
    concepts: list[dict[str, Any]],
    index_by_id: dict[str, int],
    decisions: dict[str, dict[str, Any]],
    graph: dict[str, Any],
) -> list[dict[str, Any]]:
    retire_ids = {
        concept_id
        for concept_id, decision in decisions.items()
        if decision.get("decision") == "retire"
    }
    if not retire_ids:
        return phase32._PHASE37_ORIGINAL_APPLY_DECISIONS(
            records,
            concepts=concepts,
            index_by_id=index_by_id,
            decisions=decisions,
            graph=graph,
        )

    # The established applicator already preserves every keep row and culmination
    # exactly. Temporarily project retire decisions to keep, then remove only those
    # origin IDs before exact grounding and topology freeze.
    projected = copy.deepcopy(decisions)
    for concept_id in retire_ids:
        projected[concept_id] = {
            "concept_id": concept_id,
            "decision": "keep",
            "segments": [],
            "confidence": float(decisions[concept_id].get("confidence") or 0.0),
            "reason": str(decisions[concept_id].get("reason") or ""),
        }
    applied = phase32._PHASE37_ORIGINAL_APPLY_DECISIONS(
        records,
        concepts=concepts,
        index_by_id=index_by_id,
        decisions=projected,
        graph=graph,
    )
    output = [
        row
        for row in applied
        if str(row.get("_phase32_origin_concept_id") or "") not in retire_ids
    ]
    _PENDING_RETIREMENT_AUDIT.set(
        _retirement_audit_value(
            records,
            concepts=concepts,
            decisions=decisions,
            graph=graph,
        )
    )
    progress.log(
        "Phase 3.7 independently selected "
        f"{len(retire_ids)} unsupported/subsumed concept row(s) for pre-freeze "
        "retirement. Exact grounding must still pass before the retirement is "
        "committed.",
        level="warning",
    )
    return output


def _adjudicate_with_directory(
    original,
    records: list[dict[str, Any]],
    *args: Any,
    **kwargs: Any,
) -> list[dict[str, Any]]:
    graph = kwargs.get("graph") or phase3.active_graph()
    if not isinstance(graph, dict):
        return original(records, *args, **kwargs)
    canonicalized = phase3.canonicalize_record_topics(records, graph)
    concepts, _index = phase32._concept_rows(canonicalized)
    directory = {
        str(row.get("concept_id") or ""): copy.deepcopy(row)
        for row in concepts
        if str(row.get("concept_id") or "")
    }
    token = _ACTIVE_CONCEPT_DIRECTORY.set(directory)
    audit_token = _PENDING_RETIREMENT_AUDIT.set(None)
    try:
        result = original(records, *args, **kwargs)
        audit = _PENDING_RETIREMENT_AUDIT.get()
        if audit:
            audit = copy.deepcopy(audit)
            audit["output_row_count"] = len(result)
            _write_retirement_audit(audit)
            progress.log(
                "Phase 3.7 retirement committed after independent topology "
                "review and exact source-block grounding. Type-host "
                "certification remains authoritative for every mined method.",
                level="success",
            )
        return result
    finally:
        _PENDING_RETIREMENT_AUDIT.reset(audit_token)
        _ACTIVE_CONCEPT_DIRECTORY.reset(token)


def install() -> None:
    if getattr(phase32, "_PHASE37_VISUAL_TOPOLOGY_VERSION", 0) >= _CONTRACT_VERSION:
        return

    phase32._PHASE37_ORIGINAL_ADJUDICATION_SCHEMA = phase32._adjudication_schema
    phase32._PHASE37_ORIGINAL_PARSE_DECISIONS = phase32._parse_decisions
    phase32._PHASE37_ORIGINAL_APPLY_DECISIONS = phase32._apply_decisions
    phase32._PHASE37_ORIGINAL_ADJUDICATE_TOPOLOGY = phase32.adjudicate_topology

    phase32._ALLOWED_DECISIONS = frozenset(
        set(phase32._ALLOWED_DECISIONS) | {"retire"}
    )
    phase32._TOPOLOGY_VERSION = _TOPOLOGY_VERSION
    phase33._DECISION_CACHE_VERSION = _DECISION_CACHE_VERSION

    # Install after Phase 3.5 so these bounded visual readers preserve Figure
    # captions that ordinary layout cleaning would otherwise remove.
    phase3.graph_topic_excerpts = _graph_topic_excerpts
    phase31._candidate_blocks = _candidate_blocks
    phase32._topic_evidence = _topic_evidence
    phase33._topic_source_blocks = _topic_source_blocks

    phase32._adjudication_schema = _adjudication_schema
    phase32._parse_decisions = _parse_decisions
    phase32._apply_decisions = _apply_decisions

    def cached_visual_provider(payload: dict[str, Any]) -> dict[str, Any]:
        return phase33._phase32_provider_with_cache(
            _adjudicate_via_openai,
            payload,
        )

    def cached_visual_critic(payload: dict[str, Any]) -> dict[str, Any]:
        return phase33._phase32_critic_with_cache(
            _critic_via_openai,
            payload,
        )

    phase32._adjudicate_via_openai = cached_visual_provider
    phase32._critic_via_openai = cached_visual_critic

    original_adjudicate = phase32._PHASE37_ORIGINAL_ADJUDICATE_TOPOLOGY

    @wraps(original_adjudicate)
    def adjudicate_topology(records, *args, **kwargs):
        return _adjudicate_with_directory(
            original_adjudicate,
            records,
            *args,
            **kwargs,
        )

    phase32.adjudicate_topology = adjudicate_topology
    phase32._PHASE37_VISUAL_TOPOLOGY_VERSION = _CONTRACT_VERSION

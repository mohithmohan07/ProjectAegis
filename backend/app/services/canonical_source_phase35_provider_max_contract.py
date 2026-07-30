"""Phase 3.5 provider-maximum input/output capacity contract.

Aegis historically carried several local safety budgets below the configured
OpenAI model's documented capacity: per-call completion limits, short hierarchy
excerpts, per-block source slices, and a 24k-character MMD chunk default.  Those
limits were useful while the pipeline was young, but they can now create the
very omissions and truncations that the verified source graph is designed to
prevent.

In live provider-max mode this contract:

* gives every OpenAI JSON/strict-schema call the full provider output allowance;
* preserves all supplied source text up to the model context boundary;
* expands the default MMD chunk to the maximum safe input budget;
* removes local character clipping from hierarchy, grounding, topology, and
  Type-host evidence payloads;
* retains lossless batching whenever a source is genuinely larger than one
  provider context window.

Provider context/output limits still exist physically.  This contract removes
only Aegis-imposed ceilings below those limits; it never drops source content to
make a request fit.
"""
from __future__ import annotations

import copy
import re
from functools import wraps
from typing import Any

from .. import config
from . import canonical_source_phase22 as phase22
from . import canonical_source_phase3 as phase3
from . import canonical_source_phase31_grounding_contract as phase31
from . import canonical_source_phase32_topology_adjudication_contract as phase32
from . import canonical_source_phase33_preflight_contract as phase33
from . import canonical_source_phase34_structured_output_contract as phase34
from . import generation
from . import progress

_CONTRACT_VERSION = 1


def _active() -> bool:
    return bool(
        getattr(config, "OPENAI_PROVIDER_MAX_TOKENS", True)
        and config.use_live_generation()
    )


def _log_policy_once() -> None:
    if not _active():
        return
    session = phase3.active_session()
    if isinstance(session, dict):
        if session.get("_phase35_provider_max_logged"):
            return
        session["_phase35_provider_max_logged"] = True
    elif getattr(generation, "_PHASE35_PROVIDER_MAX_LOGGED", False):
        return
    else:
        generation._PHASE35_PROVIDER_MAX_LOGGED = True
    progress.log(
        "Provider-max token policy active: live OpenAI calls receive up to "
        f"{config.OPENAI_MAX_OUTPUT_TOKENS:,} completion tokens and preserve "
        f"source input up to {config.OPENAI_MAX_INPUT_TOKENS:,} tokens before "
        "lossless batching.",
        level="success",
    )


def _full_compact(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def install() -> None:
    if getattr(generation, "_PHASE35_PROVIDER_MAX_CONTRACT_VERSION", 0) >= _CONTRACT_VERSION:
        return

    # ------------------------------------------------------------------
    # Every live JSON request receives the provider maximum completion cap.
    # Unit/offline mode retains caller-specified limits so existing isolated
    # tests and third-party compatibility probes remain precise.
    # ------------------------------------------------------------------
    original_openai_json = generation._openai_json
    generation._PHASE35_ORIGINAL_OPENAI_JSON = original_openai_json

    @wraps(original_openai_json)
    def openai_json_provider_max(
        system: str,
        user: str,
        max_tokens: int | None = None,
        retries: int = 3,
        *,
        purpose="source_extraction",
    ) -> dict:
        _log_policy_once()
        effective = config.OPENAI_MAX_OUTPUT_TOKENS if _active() else max_tokens
        return generation._PHASE35_ORIGINAL_OPENAI_JSON(
            system,
            user,
            max_tokens=effective,
            retries=retries,
            purpose=purpose,
        )

    generation._openai_json = openai_json_provider_max

    original_multimodal = phase22._openai_multimodal_json
    phase22._PHASE35_ORIGINAL_OPENAI_MULTIMODAL_JSON = original_multimodal

    @wraps(original_multimodal)
    def multimodal_provider_max(
        *,
        system: str,
        prompt: str,
        pages: list[phase22.EvidencePage],
        response_schema: dict[str, Any],
        purpose: str = "source_adjudication",
        max_tokens: int = phase22._MAX_OUTPUT_TOKENS,
    ) -> dict[str, Any]:
        _log_policy_once()
        effective = config.OPENAI_MAX_OUTPUT_TOKENS if _active() else max_tokens
        return phase22._PHASE35_ORIGINAL_OPENAI_MULTIMODAL_JSON(
            system=system,
            prompt=prompt,
            pages=pages,
            response_schema=response_schema,
            purpose=purpose,
            max_tokens=effective,
        )

    phase22._openai_multimodal_json = multimodal_provider_max

    original_completion_cap = phase34._completion_cap
    phase34._PHASE35_ORIGINAL_COMPLETION_CAP = original_completion_cap

    def completion_cap(initial: int) -> int:
        if _active():
            return max(int(initial), int(config.OPENAI_MAX_OUTPUT_TOKENS))
        return phase34._PHASE35_ORIGINAL_COMPLETION_CAP(initial)

    phase34._completion_cap = completion_cap

    # ------------------------------------------------------------------
    # Remove text clipping. These functions still normalize whitespace; they no
    # longer elide characters or append TRIMMED/omitted markers in live mode.
    # ------------------------------------------------------------------
    original_compact = phase22._compact
    phase22._PHASE35_ORIGINAL_COMPACT = original_compact

    def compact(value: object, limit: int = 420) -> str:
        if _active():
            return _full_compact(value)
        return phase22._PHASE35_ORIGINAL_COMPACT(value, limit=limit)

    phase22._compact = compact

    original_trim = generation._trim
    generation._PHASE35_ORIGINAL_TRIM = original_trim

    def trim(text: str, max_chars: int = 220_000) -> str:
        if _active():
            return str(text or "")
        return generation._PHASE35_ORIGINAL_TRIM(text, max_chars=max_chars)

    generation._trim = trim

    original_split = generation._split_mmd_into_chunks
    generation._PHASE35_ORIGINAL_SPLIT_MMD = original_split

    def split_mmd_into_chunks(
        mmd_text: str,
        max_chars: int | None = None,
    ) -> list[str]:
        if _active() and max_chars is None:
            max_chars = int(config.OPENAI_MAX_INPUT_CHARS)
        return generation._PHASE35_ORIGINAL_SPLIT_MMD(
            mmd_text,
            max_chars=max_chars,
        )

    generation._split_mmd_into_chunks = split_mmd_into_chunks

    original_section_excerpt = phase3._section_excerpt
    phase3._PHASE35_ORIGINAL_SECTION_EXCERPT = original_section_excerpt

    def section_excerpt(
        section: dict[str, Any],
        blocks_by_id: dict[str, dict[str, Any]],
        limit: int = 900,
    ) -> str:
        if not _active():
            return phase3._PHASE35_ORIGINAL_SECTION_EXCERPT(
                section,
                blocks_by_id,
                limit=limit,
            )
        pieces: list[str] = []
        for block_id in section.get("block_ids") or []:
            block = blocks_by_id.get(str(block_id))
            if not block or block.get("kind") in {"layout", "heading", "navigation"}:
                continue
            text = str(block.get("display_text") or block.get("raw_text") or "").strip()
            if text:
                pieces.append(_full_compact(text))
        return " ".join(pieces)

    phase3._section_excerpt = section_excerpt

    original_candidate_blocks = phase31._candidate_blocks
    phase31._PHASE35_ORIGINAL_CANDIDATE_BLOCKS = original_candidate_blocks

    def candidate_blocks(
        graph: dict[str, Any],
        canonical: dict[str, Any],
        topic_id: str,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        if not _active():
            return phase31._PHASE35_ORIGINAL_CANDIDATE_BLOCKS(
                graph,
                canonical,
                topic_id,
            )
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
                    "text": text,
                }
            )
        return usable, payload

    phase31._candidate_blocks = candidate_blocks

    original_topic_evidence = phase32._topic_evidence
    phase32._PHASE35_ORIGINAL_TOPIC_EVIDENCE = original_topic_evidence

    def topic_evidence(
        graph: dict[str, Any],
        canonical: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], dict[str, int]]:
        if not _active():
            return phase32._PHASE35_ORIGINAL_TOPIC_EVIDENCE(graph, canonical)
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
        output: list[dict[str, Any]] = []
        for topic in topics:
            topic_id = str(topic.get("topic_id") or "")
            pieces: list[str] = []
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
                pieces.append(
                    f"[{block_id} | "
                    f"{str(block.get('subtopic_id') or 'NO-SUBTOPIC')} | "
                    f"{str(block.get('kind') or 'content')}] {text}"
                )
            output.append(
                {
                    "topic_id": topic_id,
                    "order": int(topic.get("order") or 0),
                    "title": str(topic.get("title") or ""),
                    "structural_number": str(topic.get("structural_number") or ""),
                    "evidence": "\n\n".join(pieces),
                }
            )
        return output, block_order

    phase32._topic_evidence = topic_evidence

    original_topic_source_blocks = phase33._topic_source_blocks
    phase33._PHASE35_ORIGINAL_TOPIC_SOURCE_BLOCKS = original_topic_source_blocks

    def topic_source_blocks(
        graph: dict[str, Any],
        canonical: dict[str, Any],
    ) -> tuple[dict[str, list[dict[str, Any]]], dict[str, str]]:
        if not _active():
            return phase33._PHASE35_ORIGINAL_TOPIC_SOURCE_BLOCKS(
                graph,
                canonical,
            )
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
            text = phase3._clean_public_text(
                phase3._graph_block_text(block, source)
            )
            if not block_id or not topic_id or not text:
                continue
            subtopic = str(block.get("subtopic_id") or "")
            subtopic_by_block[block_id] = subtopic
            blocks_by_topic.setdefault(topic_id, []).append(
                {
                    "block_id": block_id,
                    "kind": str(block.get("kind") or ""),
                    "subtopic_id": subtopic,
                    "text": text,
                }
            )
        return blocks_by_topic, subtopic_by_block

    phase33._topic_source_blocks = topic_source_blocks

    # Increase the default section-aware MMD request to the provider input
    # capacity. Explicit max_chars arguments remain authoritative and sources
    # larger than this are still losslessly split and merged.
    if _active():
        generation._MMD_CHUNK_CHARS = int(config.OPENAI_MAX_INPUT_CHARS)

    generation._PHASE35_PROVIDER_MAX_CONTRACT_VERSION = _CONTRACT_VERSION

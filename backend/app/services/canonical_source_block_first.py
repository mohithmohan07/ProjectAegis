"""Restructure C1 — the block-first shadow compiler and its machine diff.

The GPT PDF reader (Phase 2.2.1) already decides everything that matters about
a chapter's structure: page blocks in reading order, task membership, the
chapter outline, figure ownership, context links. The production path then
renders that ledger to flat MMD, re-parses the MMD with regexes to mint
canonical blocks, and re-attaches task identity by normalized text matching.

This module compiles the canonical document DIRECTLY from the verified
page/block JSON instead: durable block ids minted from page identity plus
reading order, native page numbers / heading levels / table structure /
figure links / confidence carried on every block, and the MMD kept as a pure
projection (the same renderer, byte-identical output) that block spans index
into. Task identity is native — no text-match re-join.

SHADOW MODE (this phase): the render-then-reparse path stays the sole
authority. Every PDF conversion additionally runs this compiler, pushes its
canonical through the same post-adjudication recalculation the authoritative
document gets, and machine-diffs the two: every divergence is recorded — an
advisory defect flag in the reconstruction manifest plus a full artifact
(``source.block-first-shadow.json``) — and NOTHING about the shipped
conversion changes. A shadow failure of any kind is recorded, never raised
(Rule 1 / Q13 posture: advisory record, never a gate).

Rule 1: everything here is mechanics — IDs, joins, spans, transport. Every
content decision this module carries (task kinds, scope, outline topics,
figure/context links) is the reader's recorded model verdict, reached through
the same shared helpers the authoritative pass uses.
"""
from __future__ import annotations

import copy
import hashlib
from typing import Any

from . import canonical_source
from . import canonical_source_phase22 as phase22
from . import canonical_source_phase221_fallback as fallback
from . import katex_rules as kr

BLOCK_FIRST_COMPILER = "block-first-shadow-1"
DIFF_SCHEMA_VERSION = "1.0.0"
# Recorded divergence entries are bounded so one pathological chapter cannot
# balloon the artifact; the truncation itself is recorded, never silent.
_MAX_DIVERGENCE_RECORDS = 200
_VALUE_PREVIEW_CHARS = 300

# Task fields the shadow compares between the two compilers. Everything a
# downstream consumer reads off a canonical task, minus pure provenance
# markers that differ by design (``source_location_confidence`` names which
# compiler located the block; ``anchor_match`` exists only on the re-parse
# path). ``task_id`` and ``qid`` ARE compared, but only because both sides
# pass through ``renumber_tasks`` first, which re-derives them from source
# order — a mismatch there signals an ordering divergence, not a mint format.
_TASK_DIFF_FIELDS = (
    "raw_prompt",
    "display_prompt",
    "source_label",
    "parent_source_label",
    "source_kind",
    "kind_ruling",
    "activity_origin",
    "chapter_wide",
    "_topic_scope",
    "scope_ruling",
    "requires_visual",
    "requires_context",
    "shared_context",
    "image_urls",
    "raw_image_urls",
    "unresolved_figure_reference_ids",
    "ambiguous_figure_reference_ids",
    "source_start",
    "source_end",
    "section_id",
    "source_section_index",
    "source_position",
    "task_id",
    "qid",
    "identity_key",
    "topic_hint",
    "raw_solution_or_answer",
    "subpart_label",
    "gpt_boundary_parts",
)

_LEAF_DIFF_FIELDS = (
    "qid",
    "case_id",
    "subpart_label",
    "raw_prompt",
    "display_prompt",
    "source_start",
)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _preview(value: Any) -> Any:
    """A bounded, JSON-safe rendering of one compared value."""
    if isinstance(value, str):
        return value if len(value) <= _VALUE_PREVIEW_CHARS else (
            value[:_VALUE_PREVIEW_CHARS] + f"… [+{len(value) - _VALUE_PREVIEW_CHARS} chars]"
        )
    if isinstance(value, (list, tuple)):
        rendered = [_preview(item) for item in value[:12]]
        if len(value) > 12:
            rendered.append(f"… [+{len(value) - 12} items]")
        return rendered
    if isinstance(value, dict):
        rendered_map = {
            str(key): _preview(item) for key, item in list(value.items())[:12]
        }
        if len(value) > 12:
            rendered_map["…"] = f"[+{len(value) - 12} entries]"
        return rendered_map
    return value


def _kind_for_slice(raw: str) -> str:
    """The transport kind the deterministic re-parse would give this slice.

    Purely mechanical shape classification of text THIS code emitted — the
    same classifiers the re-parse path uses, so the two partitions describe
    slices in the same vocabulary. Never judges what source content means.
    """
    first_line = raw.splitlines(keepends=True)[0] if raw else raw
    if canonical_source._heading_info(first_line):
        return "heading"
    env = canonical_source._environment_name(first_line)
    if env:
        lowered = env.rstrip("*")
        if lowered == "figure":
            return "figure"
        if lowered in {"table", "tabular"}:
            return "table"
        if lowered in {"itemize", "enumerate"}:
            return "list"
        if lowered in {
            "equation", "align", "aligned", "gather", "multline", "cases",
            "array", "matrix", "pmatrix", "bmatrix", "vmatrix", "Vmatrix",
        }:
            return "math"
        return "unknown"
    if not raw.strip():
        return "layout"
    return canonical_source._classify_plain_block(raw)


def _merge_header_spans(spans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fold the adjacent MMD header stamp lines into one span."""
    merged: list[dict[str, Any]] = []
    for span in spans:
        if (
            merged
            and span.get("role") == "mmd_header"
            and merged[-1].get("role") == "mmd_header"
            and int(span["start"]) == int(merged[-1]["end"]) + 1
        ):
            merged[-1]["end"] = int(span["end"])
            continue
        merged.append(dict(span))
    return merged


def _native_block_id(span: dict[str, Any], used: set[str]) -> str:
    role = str(span.get("role") or "body")
    page_number = int(span.get("page_number") or 0)
    reading_order = int(span.get("reading_order") or 0)
    if role == "mmd_header":
        base = "BLK-MMD-HEADER"
    elif role == "topic_heading" and not span.get("opened_on_heading"):
        base = f"BLK-T{int(span.get('topic_sequence') or 0):04d}"
    else:
        base = f"BLK-P{page_number:04d}-R{reading_order:04d}"
        if role == "task_cue":
            base += "-CUE"
        elif role == "source_label":
            base += "-LBL"
    block_id = base
    suffix = 2
    while block_id in used:
        block_id = f"{base}-{suffix}"
        suffix += 1
    used.add(block_id)
    return block_id


def _page_block_index(
    page_acsd: dict[str, Any],
) -> dict[tuple[str, int], tuple[int, dict[str, Any]]]:
    index: dict[tuple[str, int], tuple[int, dict[str, Any]]] = {}
    for page in page_acsd.get("pages") or []:
        if not isinstance(page, dict):
            continue
        page_id = str(page.get("page_id") or "")
        page_number = int(page.get("page_number") or 0)
        for block in page.get("blocks") or []:
            if not isinstance(block, dict):
                continue
            index[(page_id, int(block.get("reading_order") or 0))] = (
                page_number, block,
            )
    return index


def _blocks_and_sections(
    text: str,
    spans: list[dict[str, Any]],
    page_blocks: dict[tuple[str, int], tuple[int, dict[str, Any]]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Tile the projection into native canonical blocks and mint sections.

    Content blocks come straight from the emission spans (each extended over
    its trailing join newline, so blocks end on line boundaries exactly like
    the re-parse partition); the whitespace between spans becomes explicit
    layout blocks. The result reconstructs the projection byte-for-byte —
    verified here, and any residue is a recorded note, never a guess.
    """
    notes: list[dict[str, Any]] = []
    spans = _merge_header_spans(sorted(spans, key=lambda s: int(s["start"])))

    blocks: list[dict[str, Any]] = []
    sections: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    separator_count = 0
    current_section_id = ""
    section_block_order = 0
    heading_stack: list[tuple[int, str]] = []

    def ensure_preamble(start: int) -> None:
        nonlocal current_section_id, section_block_order
        if current_section_id:
            return
        current_section_id = "SEC-0000"
        section_block_order = 0
        sections.append({
            "section_id": current_section_id,
            "order": 0,
            "title": "",
            "level": 0,
            "depth": 0,
            "parent_section_id": "",
            "heading_kind": "implicit_preamble",
            "source_start": start,
            "source_end": len(text),
            "block_ids": [],
        })

    def open_section(start: int, title: str, level: int) -> None:
        nonlocal current_section_id, section_block_order
        section_order = len([item for item in sections if item["order"] > 0]) + 1
        current_section_id = f"SEC-{section_order:04d}"
        section_block_order = 0
        while heading_stack and heading_stack[-1][0] >= level:
            heading_stack.pop()
        parent_section_id = heading_stack[-1][1] if heading_stack else ""
        heading_stack.append((level, current_section_id))
        sections.append({
            "section_id": current_section_id,
            "order": section_order,
            "title": title,
            "level": level,
            "depth": len(heading_stack),
            "parent_section_id": parent_section_id,
            "heading_kind": "markdown",
            "source_start": start,
            "source_end": len(text),
            "block_ids": [],
        })

    def add_block(
        start: int,
        end: int,
        kind: str,
        *,
        block_id: str,
        span: dict[str, Any] | None = None,
        heading: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        nonlocal section_block_order
        if heading is not None:
            open_section(start, heading["title"], heading["level"])
        else:
            ensure_preamble(start)
        section_block_order += 1
        raw_text = text[start:end]
        block: dict[str, Any] = {
            "block_id": block_id,
            "order": len(blocks) + 1,
            "section_id": current_section_id,
            "section_order": section_block_order,
            "kind": kind,
            "source_start": start,
            "source_end": end,
            "raw_sha256": _sha256_text(raw_text),
            "raw_text": raw_text,
        }
        if heading is not None:
            block["heading"] = heading
        if span is not None:
            role = str(span.get("role") or "body")
            block["block_first"] = {"role": role, "compiler": BLOCK_FIRST_COMPILER}
            if role != "mmd_header":
                page_key = (
                    str(span.get("page_id") or ""),
                    int(span.get("reading_order") or 0),
                )
                page_number, page_block = page_blocks.get(page_key, (0, {}))
                block.update({
                    "page_id": page_key[0],
                    "page_number": int(span.get("page_number") or page_number),
                    "reading_order": page_key[1],
                    "page_kind": str(span.get("page_kind") or ""),
                    "page_heading_level": int(page_block.get("heading_level") or 0),
                    "page_source_label": str(page_block.get("source_label") or ""),
                    "page_confidence": float(page_block.get("confidence") or 0.0),
                    "page_bbox": list(page_block.get("bbox") or []),
                })
                if str(span.get("page_kind") or "") == "table":
                    block["table_rows"] = copy.deepcopy(
                        page_block.get("table_rows") or []
                    )
                if page_block.get("linked_visual_orders"):
                    block["linked_visual_orders"] = [
                        int(value)
                        for value in page_block.get("linked_visual_orders") or []
                    ]
                if page_block.get("linked_context_orders"):
                    block["linked_context_orders"] = [
                        int(value)
                        for value in page_block.get("linked_context_orders") or []
                    ]
        blocks.append(block)
        sections[-1]["block_ids"].append(block_id)
        return block

    def add_gap_blocks(start: int, end: int) -> None:
        nonlocal separator_count
        if start >= end:
            return
        gap = text[start:end]
        if gap.strip():
            # Every emitted character belongs to a span or is a separator;
            # anything else is a self-check failure worth recording exactly.
            notes.append({
                "code": "unmapped_projection_text",
                "detail": _preview(gap),
                "source_start": start,
                "source_end": end,
            })
        separator_count += 1
        add_block(
            start, end, "layout",
            block_id=f"BLK-SEP-{separator_count:05d}",
        )

    cursor = 0
    for span in spans:
        start = int(span["start"])
        end = int(span["end"])
        # Extend the slice over its trailing join newline so blocks end on
        # line boundaries, exactly as the line-based re-parse partition does.
        slice_end = end + 1 if end < len(text) and text[end] == "\n" else end
        if start > cursor:
            add_gap_blocks(cursor, start)
        elif start < cursor:
            notes.append({
                "code": "overlapping_projection_spans",
                "detail": {"cursor": cursor, "span": _preview(span)},
            })
            continue
        raw = text[start:slice_end]
        role = str(span.get("role") or "body")
        heading: dict[str, Any] | None = None
        if role in {"topic_heading", "task_cue", "source_label", "native_heading"}:
            info = canonical_source._heading_info(
                raw.splitlines(keepends=True)[0] if raw else raw
            )
            if info is None:
                notes.append({
                    "code": "emitted_heading_not_parseable",
                    "detail": _preview(raw),
                    "source_start": start,
                })
                kind = "paragraph"
            else:
                level, title, heading_kind = info
                heading = {
                    "title": title,
                    "level": level,
                    "heading_kind": heading_kind,
                }
                kind = "heading"
        else:
            kind = _kind_for_slice(raw)
            if kind == "heading":
                # Body text that merely LOOKS like a markdown heading: the
                # reader ruled it prose, so it stays prose here — and the
                # partition diff will surface that the re-parse disagreed.
                notes.append({
                    "code": "body_text_resembles_heading",
                    "detail": _preview(raw),
                    "source_start": start,
                })
                kind = "paragraph"
            elif kind == "layout":
                kind = "paragraph" if raw.strip() else "layout"
        add_block(
            start, slice_end, kind,
            block_id=_native_block_id(span, used_ids),
            span=span,
            heading=heading,
        )
        cursor = max(cursor, slice_end)
    if cursor < len(text):
        add_gap_blocks(cursor, len(text))

    if not sections:
        ensure_preamble(0)
    for index, section in enumerate(sections):
        if index + 1 < len(sections):
            section["source_end"] = sections[index + 1]["source_start"]
        else:
            section["source_end"] = len(text)

    reconstructed = "".join(block["raw_text"] for block in blocks)
    if reconstructed != text:
        notes.append({
            "code": "block_first_tiling_incomplete",
            "detail": {
                "reconstructed_chars": len(reconstructed),
                "projection_chars": len(text),
            },
        })
    return blocks, sections, notes


def _native_tasks(
    page_acsd: dict[str, Any],
    blocks: list[dict[str, Any]],
    figures: list[dict[str, Any]],
    images: list[dict[str, Any]],
    sections: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build canonical tasks natively from the verified page task ledger.

    Identity is the page block itself — no text matching anywhere. Every
    model verdict (outline task kinds, assessment spans, partitions, visual
    and context links) reaches the task through the same shared helpers the
    authoritative relationships pass uses.
    """
    notes: list[dict[str, Any]] = []
    outline = page_acsd.get("chapter_outline") or {}
    outline_partitions = fallback._outline_task_partitions(outline)
    outline_task_kinds = fallback._outline_ruled_task_kinds(outline)
    outline_starts, in_assessment_span = fallback._outline_assessment_ruler(
        page_acsd
    )

    body_blocks: dict[tuple[str, int], dict[str, Any]] = {}
    for block in blocks:
        if (
            block.get("page_id")
            and str((block.get("block_first") or {}).get("role")) == "body"
        ):
            body_blocks[(
                str(block.get("page_id")),
                int(block.get("reading_order") or 0),
            )] = block
    sections_by_id = {
        str(section.get("section_id") or ""): section for section in sections
    }
    figures_by_id = {
        str(figure.get("figure_id") or ""): figure
        for figure in figures
        if isinstance(figure, dict) and figure.get("figure_id")
    }
    figure_payload = fallback._figure_payload_from_canonical(
        {"figures": figures, "images": images}
    )
    figure_by_ref: dict[str, list[str]] = {}
    for figure in figures:
        for reference_id in figure.get("reference_ids") or []:
            figure_by_ref.setdefault(str(reference_id), []).append(
                str(figure.get("figure_id") or "")
            )
    fallback_urls = {
        str(block.get("asset_url") or "")
        for page in page_acsd.get("pages") or []
        for block in page.get("blocks") or []
        if isinstance(block, dict) and block.get("kind") == "figure"
        and str(block.get("asset_url") or "")
    }

    ownership: dict[str, list[str]] = {}
    tasks: list[dict[str, Any]] = []

    def add_figure(
        figure_id: str,
        task_id: str,
        linked_urls: list[str],
        linked_figure_ids: list[str],
        captions: dict[str, str],
        *,
        preferred_caption: str = "",
    ) -> None:
        fallback._attach_task_figure(
            figures_by_id, ownership,
            figure_id, task_id, linked_urls, linked_figure_ids, captions,
            preferred_caption=preferred_caption,
        )

    for page in page_acsd.get("pages") or []:
        if not isinstance(page, dict):
            continue
        page_id = str(page.get("page_id") or "")
        page_number = int(page.get("page_number") or 0)
        page_block_list = sorted(
            [b for b in page.get("blocks") or [] if isinstance(b, dict)],
            key=lambda b: int(b.get("reading_order") or 0),
        )
        blocks_by_order = {
            int(b.get("reading_order") or 0): b for b in page_block_list
        }
        figures_by_order = {
            order: b for order, b in blocks_by_order.items()
            if b.get("kind") == "figure"
        }
        page_figure_urls = {
            str(b.get("asset_url") or "")
            for b in figures_by_order.values()
            if str(b.get("asset_url") or "")
        }
        for block in page_block_list:
            if str(block.get("kind") or "") != "task":
                continue
            reading_order = int(block.get("reading_order") or 0)
            verified_prompt = str(block.get("text") or "").strip()
            if not verified_prompt:
                notes.append({
                    "code": "empty_verified_task_skipped",
                    "page_id": page_id,
                    "reading_order": reading_order,
                })
                continue
            prompt_block = body_blocks.get((page_id, reading_order))
            if prompt_block is None:
                notes.append({
                    "code": "task_without_projected_block",
                    "page_id": page_id,
                    "reading_order": reading_order,
                })
                continue
            label = str(block.get("source_label") or "").strip() or "Task"
            task_id = f"TASK-P{page_number:04d}-R{reading_order:04d}"
            section = sections_by_id.get(
                str(prompt_block.get("section_id") or ""), {}
            )
            task: dict[str, Any] = {
                "task_id": task_id,
                "qid": "",
                "order": 0,
                "order_index": 0,
                "identity_key": "",
                "chapter_wide": False,
                "requires_context": False,
                "shared_context": "",
                "content_objects": {},
                "explicit_figure_reference_ids": [],
                "display_figure_reference_ids": [],
                "raw_figure_reference_ids": [],
                "figure_refs": [],
                "image_urls": [],
                "raw_prompt": verified_prompt,
                "source_label": label,
                "parent_source_label": label,
                "topic_hint": "",
                "source_start": int(prompt_block.get("source_start") or 0),
                "source_end": int(prompt_block.get("source_end") or 0),
                "section_id": str(prompt_block.get("section_id") or ""),
                "source_section_index": max(
                    0, int(section.get("order") or 1) - 1
                ),
                "source_position": max(
                    0,
                    int(prompt_block.get("source_start") or 0)
                    - int(section.get("source_start") or 0),
                ),
                "source_location_confidence": "gpt_pdf_acsd_native_block",
            }
            ruled_kind = outline_task_kinds.get((page_id, reading_order), "")
            (
                task["source_kind"],
                task["activity_origin"],
                task["kind_ruling"],
            ) = fallback._ruled_source_kind(ruled_kind, label)

            raw_urls: list[str] = []
            raw_figure_ids: list[str] = []
            raw_captions: dict[str, str] = {}
            display_urls: list[str] = []
            display_figure_ids: list[str] = []
            display_captions: dict[str, str] = {}

            local_link_orders = [
                int(value) for value in block.get("linked_visual_orders") or []
            ]
            explicit_reference_ids = canonical_source._figure_reference_ids(
                verified_prompt
            )
            unresolved_reference_ids: list[str] = []
            ambiguous_reference_ids: list[str] = []
            explicit_figure_ids: list[str] = []
            for reference_id in explicit_reference_ids:
                candidates = figure_by_ref.get(reference_id, [])
                if len(candidates) == 1:
                    if candidates[0] not in explicit_figure_ids:
                        explicit_figure_ids.append(candidates[0])
                elif not candidates:
                    unresolved_reference_ids.append(reference_id)
                elif not any(
                    candidate in explicit_figure_ids for candidate in candidates
                ):
                    ambiguous_reference_ids.append(reference_id)
            task["explicit_figure_reference_ids"] = explicit_reference_ids
            task["unresolved_figure_reference_ids"] = unresolved_reference_ids
            task["ambiguous_figure_reference_ids"] = ambiguous_reference_ids

            for figure_id in explicit_figure_ids:
                figure = figures_by_id.get(figure_id, {})
                add_figure(
                    figure_id, task_id, raw_urls, raw_figure_ids, raw_captions,
                )
                figure_urls = {
                    str(value) for value in figure.get("image_urls") or [] if value
                }
                is_cross_page = not bool(figure_urls & page_figure_urls)
                if not local_link_orders or is_cross_page:
                    add_figure(
                        figure_id, task_id,
                        display_urls, display_figure_ids, display_captions,
                    )

            for linked in local_link_orders:
                figure_block = figures_by_order.get(linked)
                if not figure_block:
                    continue
                url = str(figure_block.get("asset_url") or "").strip()
                payload = figure_payload.get(url)
                if not url or payload is None:
                    continue
                figure_id, _urls, canonical_caption = payload
                preferred_caption = (
                    str(figure_block.get("caption") or "").strip()
                    or canonical_caption
                )
                add_figure(
                    figure_id, task_id, raw_urls, raw_figure_ids, raw_captions,
                    preferred_caption=preferred_caption,
                )
                add_figure(
                    figure_id, task_id,
                    display_urls, display_figure_ids, display_captions,
                    preferred_caption=preferred_caption,
                )

            task["figure_refs"] = list(display_figure_ids)
            task["raw_figure_refs"] = list(raw_figure_ids)
            task["display_figure_refs"] = list(display_figure_ids)
            task["image_urls"] = list(display_urls)
            task["raw_image_urls"] = list(raw_urls)
            task["display_image_urls"] = list(display_urls)
            task["_image_captions"] = copy.deepcopy(display_captions)
            task["raw_image_captions"] = copy.deepcopy(raw_captions)
            task["display_image_captions"] = copy.deepcopy(display_captions)
            task["requires_visual"] = bool(display_urls)

            linked_context_orders = [
                int(value) for value in block.get("linked_context_orders") or []
            ]
            if linked_context_orders:
                context_objects: list[dict[str, Any]] = []
                context_parts: list[str] = []
                for linked in linked_context_orders:
                    context_block = blocks_by_order.get(linked)
                    if context_block is None:
                        continue
                    display_text = fallback._page_context_text(context_block)
                    if not display_text:
                        continue
                    context_parts.append(display_text)
                    context_object = {
                        "source_id": f"{page_id or 'PDF-PAGE'}-BLOCK-{linked:04d}",
                        "page_id": page_id,
                        "reading_order": linked,
                        "kind": context_block.get("kind"),
                        "display_text": display_text,
                    }
                    canonical_context = body_blocks.get((page_id, linked))
                    if canonical_context is not None:
                        context_object["block_id"] = canonical_context.get(
                            "block_id"
                        )
                        ids = canonical_context.setdefault(
                            "gpt_pdf_acsd_context_task_ids", []
                        )
                        if task_id not in ids:
                            ids.append(task_id)
                    context_objects.append(context_object)
                shared_context = kr.canonicalize_rich_text(
                    "\n".join(context_parts).strip()
                ).strip()
                task["shared_context"] = shared_context
                task["requires_context"] = bool(shared_context)
                if context_objects:
                    task["content_objects"] = {
                        "shared_context_blocks": context_objects
                    }

            task["gpt_pdf_acsd_relationship"] = {
                "page_id": page_id,
                "task_reading_order": reading_order,
                "linked_visual_orders": local_link_orders,
                "linked_context_orders": linked_context_orders,
            }
            task_ref = (page_id, reading_order)
            gpt_parts = outline_partitions.get(task_ref)
            if gpt_parts:
                task["gpt_boundary_parts"] = copy.deepcopy(gpt_parts)
            if in_assessment_span(task_ref):
                task["chapter_wide"] = True
                task["_topic_scope"] = "chapter"
                task["scope_ruling"] = "chapter_outline_assessment_span"
            elif outline_starts:
                task["chapter_wide"] = False
                task["_topic_scope"] = "topic"
                task["scope_ruling"] = "chapter_outline_content_span"
            else:
                task["chapter_wide"] = False
                task["_topic_scope"] = "topic"
                task["scope_ruling"] = "not_model_ruled_flagged"
            # Physical provenance hint, same convention as the anchor parser:
            # the nearest preceding main (level-1) section title, empty for a
            # chapter-wide task. Purely positional bookkeeping — the topic
            # machinery later assigns real topics.
            if task["chapter_wide"]:
                task["topic_hint"] = ""
            else:
                task["topic_hint"] = next(
                    (
                        str(section.get("title") or "")
                        for section in reversed(sections)
                        if int(section.get("level") or 0) == 1
                        and int(section.get("source_start") or 0)
                        <= int(prompt_block.get("source_start") or 0)
                    ),
                    "",
                )
            task["display_prompt"] = fallback._compose_task_display_prompt(
                verified_prompt, fallback_urls, display_urls, display_captions
            )

            prompt_block.setdefault("task_ids", []).append(task_id)
            tasks.append(task)

    for order, task in enumerate(tasks, start=1):
        task["order"] = order
        task["order_index"] = order
        task["qid"] = f"QINV-{order:04d}"

    blocks_by_id = {str(block.get("block_id") or ""): block for block in blocks}
    for figure_id, figure in figures_by_id.items():
        urls = [str(value) for value in figure.get("image_urls") or [] if value]
        if not any(url in fallback_urls for url in urls):
            continue
        block = blocks_by_id.get(str(figure.get("block_id") or ""))
        if block is not None:
            block["task_ids"] = sorted(set(ownership.get(figure_id, [])))
    return tasks, notes


def compile_block_first(
    page_acsd: dict[str, Any],
    *,
    source_filename: str = "source.pdf",
) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    """Compile the canonical document directly from the page/block JSON.

    Returns ``(projection_text, canonical, notes)``. The projection is the
    installed renderer's byte-identical MMD; the canonical carries native
    identity and page provenance on every block and task.
    """
    text, spans = fallback.render_page_acsd_to_mmd_with_spans(page_acsd)
    page_blocks = _page_block_index(page_acsd)
    blocks, sections, notes = _blocks_and_sections(text, spans, page_blocks)
    images, maths, figures = canonical_source._enrich_blocks(blocks)
    tasks, task_notes = _native_tasks(
        page_acsd, blocks, figures, images, sections
    )
    notes.extend(task_notes)

    canonical: dict[str, Any] = {
        "schema_name": canonical_source.SCHEMA_NAME,
        "schema_version": canonical_source.SCHEMA_VERSION,
        "compiler_version": BLOCK_FIRST_COMPILER,
        "shadow_mode": True,
        "used_for_generation": False,
        "document": {
            "source_filename": str(source_filename or "source.pdf"),
            "source_sha256": _sha256_text(text),
            "source_chars": len(text),
            "line_count": text.count("\n") + (1 if text else 0),
            "encoding": "utf-8",
        },
        "ordering_contract": {
            "source_order_locked": True,
            "topic_sequence_locked": True,
            "block_sequence_locked": True,
            "allow_model_reordering": False,
            "ambiguous_order_policy": "fail_closed_at_future_cutover",
        },
        "section_sequence": [item["section_id"] for item in sections],
        "sections": sections,
        "blocks": blocks,
        "figures": figures,
        "images": images,
        "math": maths,
        "tasks": tasks,
        "statistics": {
            "sections": len(sections),
            "blocks": len(blocks),
            "figures": len(figures),
            "images": len(images),
            "math_spans": len(maths),
            "tasks": len(tasks),
        },
        "dropped_furniture": [
            str(line)
            for page in page_acsd.get("pages") or []
            if isinstance(page, dict)
            for line in page.get("dropped_furniture") or []
            if str(line or "").strip()
        ],
    }
    outline = page_acsd.get("chapter_outline")
    if isinstance(outline, dict):
        canonical["chapter_outline"] = copy.deepcopy(outline)
    return text, canonical, notes


def _task_key(task: dict[str, Any]) -> tuple[str, int] | None:
    relationship = task.get("gpt_pdf_acsd_relationship")
    if not isinstance(relationship, dict):
        return None
    return (
        str(relationship.get("page_id") or ""),
        int(relationship.get("task_reading_order") or 0),
    )


def _leaf_projection(task: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {field: leaf.get(field) for field in _LEAF_DIFF_FIELDS}
        for leaf in task.get("leaf_cases") or []
        if isinstance(leaf, dict)
    ]


def _diff_canonicals(
    authoritative: dict[str, Any],
    block_first: dict[str, Any],
) -> list[dict[str, Any]]:
    """Machine-diff the two canonical documents; every mismatch is a record."""
    records: list[dict[str, Any]] = []

    # --- tasks: the canonical task inventory is the shadow's primary target.
    authoritative_tasks: dict[tuple[str, int], dict[str, Any]] = {}
    for task in authoritative.get("tasks") or []:
        if not isinstance(task, dict):
            continue
        key = _task_key(task)
        if key is None:
            records.append({
                "code": "task_without_page_identity",
                "compiler": "authoritative",
                "task_id": task.get("task_id"),
                "qid": task.get("qid"),
            })
            continue
        authoritative_tasks[key] = task
    block_first_tasks: dict[tuple[str, int], dict[str, Any]] = {}
    for task in block_first.get("tasks") or []:
        if not isinstance(task, dict):
            continue
        key = _task_key(task)
        if key is None:
            records.append({
                "code": "task_without_page_identity",
                "compiler": "block_first",
                "task_id": task.get("task_id"),
            })
            continue
        block_first_tasks[key] = task

    for key in sorted(set(authoritative_tasks) - set(block_first_tasks)):
        records.append({
            "code": "task_missing_in_block_first",
            "page_id": key[0],
            "reading_order": key[1],
            "authoritative_qid": authoritative_tasks[key].get("qid"),
        })
    for key in sorted(set(block_first_tasks) - set(authoritative_tasks)):
        records.append({
            "code": "task_missing_in_authoritative",
            "page_id": key[0],
            "reading_order": key[1],
        })
    for key in sorted(set(authoritative_tasks) & set(block_first_tasks)):
        left = authoritative_tasks[key]
        right = block_first_tasks[key]
        for field in _TASK_DIFF_FIELDS:
            left_value = left.get(field)
            right_value = right.get(field)
            # Consumers read every one of these through ``or``-defaults, so
            # an absent field, None, "", [] and {} are the same fact.
            if not left_value and not right_value:
                continue
            if left_value != right_value:
                records.append({
                    "code": "task_field_divergence",
                    "page_id": key[0],
                    "reading_order": key[1],
                    "qid": left.get("qid"),
                    "field": field,
                    "authoritative": _preview(left_value),
                    "block_first": _preview(right_value),
                })
        left_leaves = _leaf_projection(left)
        right_leaves = _leaf_projection(right)
        if left_leaves != right_leaves:
            records.append({
                "code": "task_leaf_cases_divergence",
                "page_id": key[0],
                "reading_order": key[1],
                "qid": left.get("qid"),
                "authoritative": _preview(left_leaves),
                "block_first": _preview(right_leaves),
            })

    # --- block partition: identical spans mean the re-parse recovered exactly
    # what the reader recorded; every boundary difference is worth seeing.
    def _partition(canonical: dict[str, Any]) -> list[tuple[int, int, str]]:
        return [
            (
                int(block.get("source_start") or 0),
                int(block.get("source_end") or 0),
                str(block.get("kind") or ""),
            )
            for block in canonical.get("blocks") or []
            if isinstance(block, dict)
        ]

    left_partition = _partition(authoritative)
    right_partition = _partition(block_first)
    if left_partition != right_partition:
        left_only = sorted(set(left_partition) - set(right_partition))
        right_only = sorted(set(right_partition) - set(left_partition))
        records.append({
            "code": "block_partition_divergence",
            "authoritative_blocks": len(left_partition),
            "block_first_blocks": len(right_partition),
            "only_in_authoritative": _preview(
                [list(item) for item in left_only[:40]]
            ),
            "only_in_block_first": _preview(
                [list(item) for item in right_only[:40]]
            ),
        })

    # --- sections
    def _section_shape(canonical: dict[str, Any]) -> list[tuple[int, int, str]]:
        return [
            (
                int(section.get("order") or 0),
                int(section.get("level") or 0),
                str(section.get("title") or ""),
            )
            for section in canonical.get("sections") or []
            if isinstance(section, dict)
        ]

    left_sections = _section_shape(authoritative)
    right_sections = _section_shape(block_first)
    if left_sections != right_sections:
        records.append({
            "code": "section_sequence_divergence",
            "authoritative": _preview([list(item) for item in left_sections]),
            "block_first": _preview([list(item) for item in right_sections]),
        })

    # --- figures, joined by their durable image URL
    def _figure_shape(canonical: dict[str, Any]) -> dict[str, dict[str, Any]]:
        shaped: dict[str, dict[str, Any]] = {}
        for figure in canonical.get("figures") or []:
            if not isinstance(figure, dict):
                continue
            for url in figure.get("image_urls") or []:
                shaped[str(url)] = {
                    "caption_raw": str(figure.get("caption_raw") or ""),
                    "reference_ids": [
                        str(value) for value in figure.get("reference_ids") or []
                    ],
                }
        return shaped

    left_figures = _figure_shape(authoritative)
    right_figures = _figure_shape(block_first)
    for url in sorted(set(left_figures) | set(right_figures)):
        if left_figures.get(url) != right_figures.get(url):
            records.append({
                "code": "figure_divergence",
                "image_url": url,
                "authoritative": _preview(left_figures.get(url)),
                "block_first": _preview(right_figures.get(url)),
            })
    return records


def run_shadow(
    *,
    page_acsd: dict[str, Any],
    mmd_text: str,
    authoritative_canonical: dict[str, Any],
    source_filename: str = "source.pdf",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run the block-first compiler beside the authoritative result.

    Returns ``(payload, summary)``: the full artifact payload (diff plus the
    complete block-first canonical for offline analysis) and the compact
    summary recorded in the reconstruction manifest. Reads the authoritative
    canonical only — never mutates it, never raises past its own boundary
    (the caller wraps this in the conversion's advisory try/except as well).
    """
    divergences: list[dict[str, Any]] = []
    projection, canonical, notes = compile_block_first(
        page_acsd, source_filename=source_filename
    )
    if projection != str(mmd_text or ""):
        divergences.append({
            "code": "projection_mismatch",
            "detail": (
                "the span-tracked projection is not byte-identical to the "
                "conversion's rendered MMD"
            ),
            "projection_sha256": _sha256_text(projection),
            "mmd_sha256": _sha256_text(str(mmd_text or "")),
        })
    divergences.extend(notes)

    recalculated = True
    try:
        canonical, _shadow_report, _shadow_issues = (
            phase22._recalculate_after_adjudication(canonical, {})
        )
    except Exception as exc:
        recalculated = False
        divergences.append({
            "code": "block_first_recalculation_failed",
            "detail": f"{type(exc).__name__}: {exc}"[:500],
        })

    divergences.extend(_diff_canonicals(authoritative_canonical, canonical))

    total = len(divergences)
    # The code histogram is tallied over the FULL record list, before any
    # truncation — the cutover analysis relies on these per-code counts on
    # exactly the high-divergence chapters where truncation kicks in.
    codes: dict[str, int] = {}
    for record in divergences:
        code = str(record.get("code") or "unknown")
        codes[code] = codes.get(code, 0) + 1
    if total > _MAX_DIVERGENCE_RECORDS:
        divergences = divergences[:_MAX_DIVERGENCE_RECORDS]
        divergences.append({
            "code": "divergence_records_truncated",
            "recorded": _MAX_DIVERGENCE_RECORDS,
            "total": total,
        })
    status = "match" if total == 0 else "diverged"

    payload = {
        "schema_name": "Aegis Block-First Shadow",
        "schema_version": DIFF_SCHEMA_VERSION,
        "compiler_version": BLOCK_FIRST_COMPILER,
        "shadow_mode": True,
        "used_for_generation": False,
        "status": status,
        "projection_sha256": _sha256_text(projection),
        "recalculated": recalculated,
        "divergence_total": total,
        "divergence_codes": codes,
        "divergences": divergences,
        "block_first_canonical": canonical,
    }
    summary = {
        "status": status,
        "compiler_version": BLOCK_FIRST_COMPILER,
        "divergences": total,
        "divergence_codes": codes,
        "recalculated": recalculated,
        "artifact": fallback.OPTIONAL_ARTIFACT_SPECS.get(
            "block_first_shadow", {}
        ).get("filename", ""),
    }
    return payload, summary

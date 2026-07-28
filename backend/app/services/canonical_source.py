"""Phase-1 shadow compiler for Aegis Canonical Source Documents.

The production concept/assessment generators continue to consume ``UploadJob.mmd_text``.
This module compiles a deterministic, inspectable shadow representation beside the
uploaded file so source order, images, maths, tasks, and transform diagnostics can be
compared before any future cutover.
"""
from __future__ import annotations

import copy
import hashlib
import html
import json
import os
import re
import shutil
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from . import katex_rules as kr

SCHEMA_NAME = "Aegis Canonical Source Document"
SCHEMA_VERSION = "1.0.0"
COMPILER_VERSION = "phase-1-shadow-1"
ARTIFACT_DIRNAME = "source-shadow"

ARTIFACT_SPECS: dict[str, dict[str, str]] = {
    "raw_mmd": {
        "filename": "source.raw.mmd",
        "media_type": "text/markdown; charset=utf-8",
        "label": "Immutable raw MMD",
    },
    "canonical_json": {
        "filename": "source.aegis-source.json",
        "media_type": "application/json; charset=utf-8",
        "label": "Aegis canonical source JSON",
    },
    "aegis_mmd": {
        "filename": "source.aegis.mmd",
        "media_type": "text/markdown; charset=utf-8",
        "label": "Derived Aegis MMD",
    },
    "report": {
        "filename": "source.source-report.json",
        "media_type": "application/json; charset=utf-8",
        "label": "Source validation report",
    },
}

_MD_HEADING_RE = re.compile(
    r"^(?P<marks>#{1,6})[ \t]+(?P<title>.*?)[ \t]*#*[ \t]*(?:\n|$)"
)
_LATEX_HEADING_RE = re.compile(
    r"^\s*\\(?P<kind>chapter|section|subsection|subsubsection|paragraph)"
    r"\*?\s*\{(?P<title>.*)\}\s*(?:\n|$)",
    re.IGNORECASE,
)
_BEGIN_ENV_RE = re.compile(
    r"\\begin\{(?P<env>figure\*?|table\*?|tabular\*?|equation\*?|"
    r"align\*?|aligned|gather\*?|multline\*?|cases|array|matrix|pmatrix|"
    r"bmatrix|vmatrix|Vmatrix|itemize|enumerate)\}",
    re.IGNORECASE,
)
_STANDALONE_MARKDOWN_IMAGE_RE = re.compile(
    r"^\s*!\[[^\]]*\]\(\s*https?://.+?\)\s*(?:\n|$)",
    re.IGNORECASE | re.DOTALL,
)
_STANDALONE_CANONICAL_IMAGE_RE = re.compile(
    r'^\s*\[img\s+src="https://[^"]+"\s+alt="[^"]+"\]\s*(?:\n|$)',
    re.IGNORECASE | re.DOTALL,
)

_INCLUDEGRAPHICS_RE = re.compile(
    r"\\includegraphics(?:\[[^\]]*\])?\{(?P<url>https?://[^}\s]+)\}",
    re.IGNORECASE,
)
_MARKDOWN_IMAGE_RE = re.compile(
    r"!\[(?P<alt>[^\]]*)\]\(\s*"
    r"(?P<url>https?://(?:[^()\s]|(?:\([^()]*\)))+)"
    r"(?:\s+(?:\"[^\"]*\"|'[^']*'))?\s*\)",
    re.IGNORECASE,
)
_CANONICAL_IMAGE_RE = re.compile(
    r'\[img\s+src="(?P<url>https://[^"]+)"\s+alt="(?P<alt>[^"]+)"\]',
    re.IGNORECASE,
)
_FIGURE_REFERENCE_RE = re.compile(
    r"\b(?:refer(?:\s+to)?\s+)?fig(?:ure)?s?[.．]?\s*"
    r"(?P<number>\d+(?:[.．]\d+)*(?:\s*(?:\([a-z]\)|[a-z]))?)"
    r"(?!\w|[.．]\d)",
    re.IGNORECASE,
)
_CAPTION_START_RE = re.compile(r"\\caption(?:\[[^\]]*\])?\s*\{", re.IGNORECASE)

_KATEX_RE = re.compile(
    r"\[katex\]\s*(?P<body>.*?)\s*\[/katex\]",
    re.IGNORECASE | re.DOTALL,
)
_MATH_ENV_RE = re.compile(
    r"\\begin\{(?P<env>equation\*?|align\*?|aligned|gather\*?|multline\*?|"
    r"cases|array|matrix|pmatrix|bmatrix|vmatrix|Vmatrix)\}"
    r"(?P<body>.*?)\\end\{(?P=env)\}",
    re.IGNORECASE | re.DOTALL,
)
_MATH_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("katex", _KATEX_RE),
    ("environment", _MATH_ENV_RE),
    ("display_dollar", re.compile(r"\$\$(?P<body>.+?)\$\$", re.DOTALL)),
    ("display_bracket", re.compile(r"\\\[(?P<body>.+?)\\\]", re.DOTALL)),
    ("inline_paren", re.compile(r"\\\((?P<body>.+?)\\\)", re.DOTALL)),
    (
        "inline_dollar",
        re.compile(r"(?<!\\)\$(?!\$)(?P<body>[^$\n]+?)(?<!\\)\$(?!\$)"),
    ),
)

_LAYOUT_COMMAND_RE = re.compile(
    r"\\(?:captionsetup|caption|includegraphics|begin\{(?:figure|table|tabular)\}"
    r"|end\{(?:figure|table|tabular)\})",
    re.IGNORECASE,
)
_FENCED_SOURCE_RE = re.compile(
    r"```mmd-source\n.*?```",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True)
class CompiledSource:
    canonical: dict[str, Any]
    aegis_mmd: str
    report: dict[str, Any]


def _sha256_text(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def _json_text(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        separators=(",", ": "),
    ) + "\n"


def _normalized_figure_id(value: str) -> str:
    text = re.sub(r"\s+", "", str(value or "").lower().replace("．", "."))
    match = re.search(r"\d+(?:\.\d+)*(?:\([a-z]\)|[a-z])?", text)
    if not match:
        return ""
    figure_id = match.group(0)
    bare = re.fullmatch(r"(?P<base>\d+(?:\.\d+)*)(?P<panel>[a-z])", figure_id)
    if bare:
        return f"{bare.group('base')}({bare.group('panel')})"
    return figure_id


def _figure_reference_ids(value: str) -> list[str]:
    found: list[str] = []
    text = str(value or "")
    for match in _FIGURE_REFERENCE_RE.finditer(text):
        figure_id = _normalized_figure_id(match.group("number"))
        if figure_id and figure_id not in found:
            found.append(figure_id)
        base = re.sub(r"\([a-z]\)$", "", figure_id)
        continuation = re.match(
            r"(?:\s*(?:,|and)\s*\([a-z]\))+",
            text[match.end():match.end() + 80],
            re.IGNORECASE,
        )
        for panel in re.finditer(
            r"\(([a-z])\)", continuation.group(0) if continuation else "", re.IGNORECASE
        ):
            inherited = f"{base}({panel.group(1).lower()})" if base else ""
            if inherited and inherited not in found:
                found.append(inherited)
    return found


def _balanced_command_body(value: str, start_pattern: re.Pattern[str]) -> str:
    match = start_pattern.search(value)
    if not match:
        return ""
    brace = value.find("{", match.start(), match.end())
    if brace < 0:
        return ""
    depth = 0
    index = brace
    while index < len(value):
        character = value[index]
        if character == "\\":
            index += 2
            continue
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return value[brace + 1:index]
        index += 1
    return ""


def _clean_caption(value: str) -> str:
    text = str(value or "")
    text = re.sub(r"\\captionsetup\{[^{}]*\}", " ", text, flags=re.IGNORECASE)
    text = text.replace("\\(", "").replace("\\)", "")
    return re.sub(r"\s+", " ", text).strip(" .")


def _heading_info(line: str) -> tuple[int, str, str] | None:
    markdown = _MD_HEADING_RE.match(line)
    if markdown:
        return len(markdown.group("marks")), markdown.group("title").strip(), "markdown"
    latex = _LATEX_HEADING_RE.match(line)
    if not latex:
        return None
    levels = {
        "chapter": 1,
        "section": 1,
        "subsection": 2,
        "subsubsection": 3,
        "paragraph": 4,
    }
    kind = latex.group("kind").lower()
    return levels.get(kind, 2), _clean_caption(latex.group("title")), "latex"


def _environment_name(line: str) -> str:
    match = _BEGIN_ENV_RE.search(line)
    return str(match.group("env") or "").lower() if match else ""


def _environment_end_re(env: str) -> re.Pattern[str]:
    return re.compile(rf"\\end\{{{re.escape(env)}\}}", re.IGNORECASE)


def _classify_plain_block(raw: str) -> str:
    stripped = raw.strip()
    if not stripped:
        return "layout"
    if stripped.startswith("```"):
        return "code"
    if _STANDALONE_MARKDOWN_IMAGE_RE.fullmatch(raw) or _STANDALONE_CANONICAL_IMAGE_RE.fullmatch(raw):
        return "figure"
    if re.match(r"^(?:[-*+] |\d+[.)] |\\item\b)", stripped, re.IGNORECASE):
        return "list"
    return "paragraph"


def _raw_blocks(source: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Partition the full source into contiguous ordered blocks and sections."""
    lines = source.splitlines(keepends=True)
    if not lines and source:
        lines = [source]
    offsets: list[int] = []
    cursor = 0
    for line in lines:
        offsets.append(cursor)
        cursor += len(line)

    blocks: list[dict[str, Any]] = []
    sections: list[dict[str, Any]] = []
    current_section_id = ""
    section_block_order = 0
    heading_stack: list[tuple[int, str]] = []

    def ensure_preamble(start: int) -> str:
        nonlocal current_section_id, section_block_order
        if current_section_id:
            return current_section_id
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
            "source_end": len(source),
            "block_ids": [],
        })
        return current_section_id

    def add_block(start: int, end: int, kind: str, *, heading: dict[str, Any] | None = None) -> None:
        nonlocal current_section_id, section_block_order, heading_stack
        if heading is not None:
            section_order = len([item for item in sections if item["order"] > 0]) + 1
            current_section_id = f"SEC-{section_order:04d}"
            section_block_order = 0
            level = int(heading["level"])
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            parent_section_id = heading_stack[-1][1] if heading_stack else ""
            heading_stack.append((level, current_section_id))
            sections.append({
                "section_id": current_section_id,
                "order": section_order,
                "title": heading["title"],
                "level": level,
                "depth": len(heading_stack),
                "parent_section_id": parent_section_id,
                "heading_kind": heading["heading_kind"],
                "source_start": start,
                "source_end": len(source),
                "block_ids": [],
            })
        else:
            ensure_preamble(start)
        section_block_order += 1
        block_id = f"BLK-{len(blocks) + 1:05d}"
        raw_text = source[start:end]
        block = {
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
        blocks.append(block)
        sections[-1]["block_ids"].append(block_id)

    i = 0
    while i < len(lines):
        line = lines[i]
        start = offsets[i]
        heading = _heading_info(line)
        if heading:
            level, title, heading_kind = heading
            add_block(start, start + len(line), "heading", heading={
                "title": title,
                "level": level,
                "heading_kind": heading_kind,
            })
            i += 1
            continue

        env = _environment_name(line)
        if env:
            end_re = _environment_end_re(env)
            j = i
            while j < len(lines) and not end_re.search(lines[j]):
                j += 1
            j = min(j, len(lines) - 1)
            end = offsets[j] + len(lines[j])
            lowered = env.rstrip("*")
            if lowered == "figure":
                kind = "figure"
            elif lowered in {"table", "tabular"}:
                kind = "table"
            elif lowered in {
                "equation", "align", "aligned", "gather", "multline", "cases",
                "array", "matrix", "pmatrix", "bmatrix", "vmatrix", "Vmatrix",
            }:
                kind = "math"
            elif lowered in {"itemize", "enumerate"}:
                kind = "list"
            else:
                kind = "unknown"
            add_block(start, end, kind)
            i = j + 1
            continue

        if not line.strip():
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            end = offsets[j] if j < len(lines) else len(source)
            add_block(start, end, "layout")
            i = j
            continue

        j = i + 1
        while j < len(lines):
            if not lines[j].strip() or _heading_info(lines[j]) or _environment_name(lines[j]):
                break
            j += 1
        end = offsets[j] if j < len(lines) else len(source)
        add_block(start, end, _classify_plain_block(source[start:end]))
        i = j

    if not blocks and source == "":
        sections.append({
            "section_id": "SEC-0000",
            "order": 0,
            "title": "",
            "level": 0,
            "depth": 0,
            "parent_section_id": "",
            "heading_kind": "implicit_preamble",
            "source_start": 0,
            "source_end": 0,
            "block_ids": [],
        })

    for index, section in enumerate(sections):
        if index + 1 < len(sections):
            section["source_end"] = sections[index + 1]["source_start"]
        else:
            section["source_end"] = len(source)
    return blocks, sections


def _image_occurrences(raw: str, *, absolute_start: int) -> list[dict[str, Any]]:
    found: list[tuple[int, int, str, str, str]] = []
    for kind, pattern in (
        ("latex_includegraphics", _INCLUDEGRAPHICS_RE),
        ("markdown_image", _MARKDOWN_IMAGE_RE),
        ("canonical_img", _CANONICAL_IMAGE_RE),
    ):
        for match in pattern.finditer(raw):
            found.append((
                match.start(),
                match.end(),
                kind,
                str(match.group("url") or ""),
                str(match.groupdict().get("alt") or ""),
            ))
    found.sort(key=lambda item: (item[0], item[1], item[2]))
    return [
        {
            "image_id": "",
            "kind": kind,
            "url": url,
            "alt_raw": html.unescape(alt),
            "source_start": absolute_start + start,
            "source_end": absolute_start + end,
        }
        for start, end, kind, url, alt in found
    ]


def _math_occurrences(raw: str, *, absolute_start: int) -> list[dict[str, Any]]:
    candidates: list[tuple[int, int, int, str, str, str]] = []
    for priority, (kind, pattern) in enumerate(_MATH_PATTERNS):
        for match in pattern.finditer(raw):
            body = str(match.group("body") or "").strip()
            if kind == "inline_dollar":
                compact = body.replace(",", "").replace(".", "")
                if compact.isdigit() or not re.search(r"[A-Za-z\\_^=+\-*/<>×÷]", body):
                    continue
            latex = body
            if kind == "environment":
                env = str(match.group("env") or "")
                latex = f"\\begin{{{env}}}{body}\\end{{{env}}}"
            candidates.append((
                match.start(), match.end(), priority, kind, match.group(0), latex,
            ))
    candidates.sort(key=lambda item: (item[0], item[2], -(item[1] - item[0])))
    accepted: list[dict[str, Any]] = []
    occupied_until = -1
    for start, end, _priority, kind, raw_math, latex in candidates:
        if start < occupied_until:
            continue
        occupied_until = end
        accepted.append({
            "math_id": "",
            "kind": kind,
            "mode": "block" if kind in {"environment", "display_dollar", "display_bracket"} else "inline",
            "raw_text": raw_math,
            "canonical_latex": latex,
            "display_text": kr.katex(latex),
            "source_start": absolute_start + start,
            "source_end": absolute_start + end,
        })
    return accepted


def _display_text(raw: str) -> tuple[str, list[str]]:
    transformations: list[str] = []
    value = kr.canonicalize_rich_text(str(raw or ""))
    if value != raw:
        transformations.append("canonicalized_rich_text")
    repaired = kr.repair_unwrapped_math(value)
    if repaired != value and kr.unwrap_katex(repaired) == kr.unwrap_katex(value):
        value = repaired
        transformations.append("wrapped_unambiguous_math")
    return value, transformations


def _enrich_blocks(
    blocks: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    images: list[dict[str, Any]] = []
    maths: list[dict[str, Any]] = []
    figures: list[dict[str, Any]] = []

    for block in blocks:
        raw = block["raw_text"]
        block_images = _image_occurrences(raw, absolute_start=block["source_start"])
        for image in block_images:
            image["image_id"] = f"IMG-{len(images) + 1:05d}"
            image["block_id"] = block["block_id"]
            images.append(image)
        block_math = _math_occurrences(raw, absolute_start=block["source_start"])
        for math in block_math:
            math["math_id"] = f"MATH-{len(maths) + 1:05d}"
            math["block_id"] = block["block_id"]
            maths.append(math)
        block["image_ids"] = [item["image_id"] for item in block_images]
        block["math_ids"] = [item["math_id"] for item in block_math]

        if block["kind"] in {"paragraph", "list", "heading", "code"}:
            display, transformations = _display_text(raw)
            block["display_text"] = display
            block["transformations"] = transformations
        elif block["kind"] == "math":
            if block_math:
                block["display_text"] = "\n".join(item["display_text"] for item in block_math)
                block["transformations"] = ["canonicalized_math_block"]
            else:
                block["display_text"] = raw
                block["transformations"] = []
        else:
            block["transformations"] = []

        if block["kind"] == "figure" or block_images:
            caption = _clean_caption(_balanced_command_body(raw, _CAPTION_START_RE))
            reference_ids = _figure_reference_ids(caption)
            figure = {
                "figure_id": f"FIG-{len(figures) + 1:05d}",
                "block_id": block["block_id"],
                "section_id": block["section_id"],
                "order": len(figures) + 1,
                "source_start": block["source_start"],
                "source_end": block["source_end"],
                "caption_raw": caption,
                "reference_ids": reference_ids,
                "image_ids": [item["image_id"] for item in block_images],
                "image_urls": [item["url"] for item in block_images],
            }
            figures.append(figure)
            block["figure_id"] = figure["figure_id"]

    return images, maths, figures


def _section_ranges_from_blocks(sections: list[dict[str, Any]]) -> dict[int, tuple[int, int]]:
    ordered = [section for section in sections if section["order"] > 0]
    return {
        index: (section["source_start"], section["source_end"])
        for index, section in enumerate(ordered)
    }


def _task_absolute_location(
    source: str,
    raw_task: str,
    section_index: int,
    source_position: int,
    section_ranges: dict[int, tuple[int, int]],
) -> tuple[int, int, str]:
    start_bound, end_bound = section_ranges.get(section_index, (0, len(source)))
    if raw_task:
        exact = source.find(raw_task, start_bound, end_bound)
        if exact >= 0:
            return exact, exact + len(raw_task), "exact_text_match"
    estimated = max(start_bound, min(end_bound, start_bound + max(0, source_position)))
    return estimated, min(len(source), estimated + len(raw_task)), "parser_position_estimate"


def _extract_tasks(
    source: str,
    sections: list[dict[str, Any]],
    figures: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Reuse the production deterministic anchor parser in shadow mode only."""
    issues: list[dict[str, Any]] = []
    try:
        from . import generation

        parsed_sections = generation.parse_mmd_sections(source)
        anchors = generation._source_task_anchors(parsed_sections)
        anchors = generation._attach_explicit_figure_images(
            [copy.deepcopy(item) for item in anchors], parsed_sections
        )
        enable_repairs = getattr(generation, "_enable_source_visual_display_repairs", None)
        if callable(enable_repairs):
            wrapped = enable_repairs({"items": anchors})
            anchors = list(wrapped.get("items") or [])
    except Exception as exc:  # shadow compilation must never block conversion
        issues.append({
            "severity": "warning",
            "code": "task_anchor_extraction_failed",
            "message": f"Deterministic task-anchor extraction failed: {exc}",
        })
        return [], issues

    figure_by_url: dict[str, list[str]] = {}
    figure_by_ref: dict[str, list[str]] = {}
    for figure in figures:
        for url in figure.get("image_urls") or []:
            figure_by_url.setdefault(url, []).append(figure["figure_id"])
        for reference_id in figure.get("reference_ids") or []:
            figure_by_ref.setdefault(reference_id, []).append(figure["figure_id"])

    section_ranges = _section_ranges_from_blocks(sections)
    sortable: list[tuple[tuple[int, int, int], dict[str, Any]]] = []
    for index, item in enumerate(anchors):
        try:
            section_index = int(item.get("_source_section_index") or 0)
        except (TypeError, ValueError):
            section_index = 0
        try:
            source_position = int(item.get("_source_position") or 0)
        except (TypeError, ValueError):
            source_position = 0
        sortable.append(((section_index, source_position, index), item))
    sortable.sort(key=lambda pair: pair[0])

    tasks: list[dict[str, Any]] = []
    for _sort_key, item in sortable:
        raw_task = str(item.get("raw_task") or item.get("normalized_task") or "")
        section_index = _sort_key[0]
        source_position = _sort_key[1]
        source_start, source_end, confidence = _task_absolute_location(
            source,
            raw_task,
            section_index,
            source_position,
            section_ranges,
        )
        try:
            display_prompt = generation._inventory_task_text(item)
        except Exception as exc:
            display_prompt = raw_task
            issues.append({
                "severity": "warning",
                "code": "task_display_render_failed",
                "message": f"Task display rendering failed: {exc}",
                "source_start": source_start,
            })

        figure_refs: list[str] = []
        for url in item.get("image_urls") or []:
            for figure_id in figure_by_url.get(str(url), []):
                if figure_id not in figure_refs:
                    figure_refs.append(figure_id)
        explicit_reference_ids = _figure_reference_ids(display_prompt or raw_task)
        unresolved_reference_ids: list[str] = []
        ambiguous_reference_ids: list[str] = []
        for reference_id in explicit_reference_ids:
            candidates = figure_by_ref.get(reference_id, [])
            if len(candidates) == 1 and candidates[0] not in figure_refs:
                figure_refs.append(candidates[0])
            elif not candidates:
                unresolved_reference_ids.append(reference_id)
            elif len(candidates) > 1 and not any(
                candidate in figure_refs for candidate in candidates
            ):
                ambiguous_reference_ids.append(reference_id)

        ordered_sections = [section for section in sections if section["order"] > 0]
        section_id = (
            ordered_sections[section_index]["section_id"]
            if 0 <= section_index < len(ordered_sections)
            else "SEC-0000"
        )
        task_id = f"TASK-{len(tasks) + 1:05d}"
        raw_identity = "\u241f".join([
            str(section_index), str(source_position), raw_task,
        ])
        task = {
            "task_id": task_id,
            "order": len(tasks) + 1,
            "source_kind": str(item.get("source_kind") or ""),
            "source_label": str(item.get("source_label") or ""),
            "parent_source_label": str(item.get("parent_source_label") or ""),
            "topic_hint": str(item.get("topic_hint") or ""),
            "raw_prompt": raw_task,
            "display_prompt": display_prompt,
            "identity_key": _sha256_text(raw_identity),
            "section_id": section_id,
            "source_section_index": section_index,
            "source_position": source_position,
            "source_start": source_start,
            "source_end": source_end,
            "source_location_confidence": confidence,
            "chapter_wide": bool(
                item.get("_chapter_wide_task") or item.get("_topic_scope") == "chapter"
            ),
            "activity_origin": bool(item.get("_activity_origin")),
            "requires_visual": bool(item.get("requires_visual")),
            "image_urls": [str(url) for url in item.get("image_urls") or []],
            "figure_refs": figure_refs,
            "explicit_figure_reference_ids": explicit_reference_ids,
            "unresolved_figure_reference_ids": unresolved_reference_ids,
            "ambiguous_figure_reference_ids": ambiguous_reference_ids,
            "display_overrides": copy.deepcopy(
                item.get("_source_visual_reference_repairs") or []
            ),
        }
        tasks.append(task)

    return tasks, issues


def _attach_task_ids_to_blocks(blocks: list[dict[str, Any]], tasks: list[dict[str, Any]]) -> None:
    for block in blocks:
        block["task_ids"] = []
    for task in tasks:
        start = int(task.get("source_start") or 0)
        end = int(task.get("source_end") or start)
        for block in blocks:
            if start < block["source_end"] and end > block["source_start"]:
                block["task_ids"].append(task["task_id"])


def _render_figure_block(
    block: dict[str, Any], figures_by_id: dict[str, dict[str, Any]], images_by_id: dict[str, dict[str, Any]]
) -> str:
    figure = figures_by_id.get(str(block.get("figure_id") or ""), {})
    lines = [
        ":::figure",
        f"id: {figure.get('figure_id') or block['block_id']}",
        f"block_id: {block['block_id']}",
        f"source_start: {block['source_start']}",
        f"source_end: {block['source_end']}",
    ]
    if figure.get("reference_ids"):
        lines.append("reference_ids: " + ", ".join(figure["reference_ids"]))
    lines.append(":::")
    for image_id in figure.get("image_ids") or block.get("image_ids") or []:
        image = images_by_id.get(image_id, {})
        url = str(image.get("url") or "")
        alt = str(figure.get("caption_raw") or image.get("alt_raw") or "Source visual")
        try:
            lines.append(kr.image(url, alt))
        except ValueError:
            lines.append(f"<!-- invalid-image-url preserved in canonical JSON: {url} -->")
    lines.append(":::")
    return "\n".join(lines)


def _render_aegis_mmd(canonical: dict[str, Any]) -> str:
    blocks_by_id = {item["block_id"]: item for item in canonical["blocks"]}
    figures_by_id = {item["figure_id"]: item for item in canonical["figures"]}
    images_by_id = {item["image_id"]: item for item in canonical["images"]}
    lines = [
        "<!-- AEGIS CANONICAL SOURCE SHADOW -->",
        f"<!-- schema_version: {SCHEMA_VERSION} -->",
        f"<!-- compiler_version: {COMPILER_VERSION} -->",
        f"<!-- source_sha256: {canonical['document']['source_sha256']} -->",
        "<!-- used_for_generation: false -->",
        "",
    ]
    for section in canonical["sections"]:
        lines.extend([
            ":::section",
            f"id: {section['section_id']}",
            f"order: {section['order']}",
            f"level: {section['level']}",
            f"source_start: {section['source_start']}",
            f"source_end: {section['source_end']}",
            ":::",
        ])
        if section.get("title"):
            heading_level = min(6, max(1, int(section.get("level") or 1)))
            lines.append(f"{'#' * heading_level} {section['title']}")
        else:
            lines.append("<!-- preamble -->")
        lines.append(":::")
        lines.append("")

        for block_id in section.get("block_ids") or []:
            block = blocks_by_id[block_id]
            kind = block["kind"]
            if kind == "heading":
                continue
            if kind == "layout":
                lines.append(f"<!-- {block_id}: source whitespace preserved in canonical JSON -->")
            elif kind == "figure":
                lines.append(_render_figure_block(block, figures_by_id, images_by_id))
            elif kind == "math":
                lines.extend([
                    ":::math",
                    f"id: {block_id}",
                    f"source_start: {block['source_start']}",
                    f"source_end: {block['source_end']}",
                    ":::",
                    str(block.get("display_text") or block.get("raw_text") or "").strip(),
                    ":::",
                ])
            elif kind == "table":
                lines.extend([
                    ":::table",
                    f"id: {block_id}",
                    f"source_start: {block['source_start']}",
                    f"source_end: {block['source_end']}",
                    "format: preserved-source-mmd",
                    ":::",
                    "```mmd-source",
                    str(block.get("raw_text") or "").rstrip(),
                    "```",
                    ":::",
                ])
            else:
                lines.extend([
                    f":::{kind}",
                    f"id: {block_id}",
                    f"source_start: {block['source_start']}",
                    f"source_end: {block['source_end']}",
                ])
                if block.get("task_ids"):
                    lines.append("task_ids: " + ", ".join(block["task_ids"]))
                lines.extend([
                    ":::",
                    str(block.get("display_text") or block.get("raw_text") or "").rstrip(),
                    ":::",
                ])
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _issue(
    severity: str,
    code: str,
    message: str,
    **extra: Any,
) -> dict[str, Any]:
    return {"severity": severity, "code": code, "message": message, **extra}


def _validate(
    source: str,
    canonical: dict[str, Any],
    aegis_mmd: str,
    inherited_issues: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    issues = [copy.deepcopy(item) for item in inherited_issues]
    blocks = canonical["blocks"]
    sections = canonical["sections"]
    images = canonical["images"]
    maths = canonical["math"]
    tasks = canonical["tasks"]

    reconstructed = "".join(block["raw_text"] for block in blocks)
    if reconstructed != source:
        issues.append(_issue(
            "error",
            "source_reconstruction_mismatch",
            "Ordered canonical blocks do not reconstruct the exact raw MMD.",
        ))

    expected_start = 0
    for block in blocks:
        if block["source_start"] != expected_start or block["source_end"] < block["source_start"]:
            issues.append(_issue(
                "error",
                "non_contiguous_block_order",
                "Canonical block spans are not contiguous and monotonic.",
                block_id=block["block_id"],
                expected_start=expected_start,
                actual_start=block["source_start"],
            ))
            break
        expected_start = block["source_end"]
    if expected_start != len(source):
        issues.append(_issue(
            "error",
            "incomplete_source_span_coverage",
            "Canonical blocks do not cover the complete raw MMD character range.",
            covered_until=expected_start,
            source_chars=len(source),
        ))

    section_starts = [section["source_start"] for section in sections]
    if section_starts != sorted(section_starts) or len(section_starts) != len(set(section_starts)):
        issues.append(_issue(
            "error",
            "section_order_violation",
            "Section source positions are not unique and monotonically ordered.",
        ))

    ids = [block["block_id"] for block in blocks]
    if len(ids) != len(set(ids)):
        issues.append(_issue("error", "duplicate_block_id", "Canonical block IDs are not unique."))

    raw_urls = [item["url"] for block in blocks for item in _image_occurrences(
        block["raw_text"], absolute_start=block["source_start"]
    )]
    canonical_urls = [item["url"] for item in images]
    if Counter(raw_urls) != Counter(canonical_urls):
        issues.append(_issue(
            "error",
            "image_url_inventory_mismatch",
            "Canonical image inventory does not preserve the exact raw URL multiset.",
            raw_count=len(raw_urls),
            canonical_count=len(canonical_urls),
        ))

    figure_ids = {figure["figure_id"] for figure in canonical["figures"]}
    for task in tasks:
        missing = [ref for ref in task.get("figure_refs") or [] if ref not in figure_ids]
        if missing:
            issues.append(_issue(
                "error",
                "unknown_task_figure_reference",
                "Task points to a Figure object that does not exist.",
                task_id=task["task_id"],
                figure_refs=missing,
            ))
        if task.get("unresolved_figure_reference_ids"):
            issues.append(_issue(
                "error",
                "unresolved_explicit_figure_reference",
                "An explicit source Figure reference does not resolve to the canonical Figure registry.",
                task_id=task["task_id"],
                reference_ids=task["unresolved_figure_reference_ids"],
            ))
        if task.get("ambiguous_figure_reference_ids"):
            issues.append(_issue(
                "error",
                "ambiguous_explicit_figure_reference",
                "An explicit source Figure reference resolves to more than one Figure object.",
                task_id=task["task_id"],
                reference_ids=task["ambiguous_figure_reference_ids"],
            ))
        if task.get("requires_visual") and not task.get("figure_refs") and not task.get("image_urls"):
            issues.append(_issue(
                "warning",
                "unresolved_required_visual",
                "A source task requires a visual but no unambiguous Figure object was linked.",
                task_id=task["task_id"],
                source_label=task.get("source_label") or "",
            ))

    for block in blocks:
        if block["kind"] not in {"paragraph", "list", "code"}:
            continue
        display = str(block.get("display_text") or "")
        rich_issues = kr.rich_text_issues(display)
        if rich_issues:
            issues.append(_issue(
                "warning",
                "display_rich_text_requires_review",
                "A derived public-text block still contains non-canonical rich text.",
                block_id=block["block_id"],
                rich_text_issues=rich_issues,
            ))

    public_aegis_mmd = _FENCED_SOURCE_RE.sub("", aegis_mmd)
    if _LAYOUT_COMMAND_RE.search(public_aegis_mmd):
        issues.append(_issue(
            "warning",
            "aegis_mmd_contains_public_source_layout",
            "Derived Aegis MMD exposes Mathpix layout commands outside a preserved source fence.",
        ))
    elif _FENCED_SOURCE_RE.search(aegis_mmd) and any(
        block.get("kind") == "table" for block in blocks
    ):
        issues.append(_issue(
            "warning",
            "aegis_mmd_preserves_raw_table_source",
            "Derived Aegis MMD preserves one or more raw table blocks in fenced source form for Phase 1 audit.",
        ))

    errors = [item for item in issues if item.get("severity") == "error"]
    warnings = [item for item in issues if item.get("severity") == "warning"]
    status = "failed" if errors else ("passed_with_warnings" if warnings else "passed")
    summary = {
        "source_chars": len(source),
        "sections": len(sections),
        "blocks": len(blocks),
        "figures": len(canonical["figures"]),
        "images": len(images),
        "math_spans": len(maths),
        "tasks": len(tasks),
        "errors": len(errors),
        "warnings": len(warnings),
    }
    return {
        "schema_name": SCHEMA_NAME,
        "schema_version": SCHEMA_VERSION,
        "compiler_version": COMPILER_VERSION,
        "source_sha256": canonical["document"]["source_sha256"],
        "status": status,
        "used_for_generation": False,
        "ready_for_future_cutover": not errors and not any(
            item.get("code") in {
                "unresolved_required_visual",
                "display_rich_text_requires_review",
                "aegis_mmd_contains_public_source_layout",
                "aegis_mmd_preserves_raw_table_source",
                "task_anchor_extraction_failed",
            }
            for item in warnings
        ),
        "summary": summary,
        "issues": issues,
    }


def compile_source(mmd_text: str, *, source_filename: str) -> CompiledSource:
    """Compile deterministic shadow artifacts without altering the generation source."""
    source = str(mmd_text or "")
    blocks, sections = _raw_blocks(source)
    images, maths, figures = _enrich_blocks(blocks)
    tasks, task_issues = _extract_tasks(source, sections, figures)
    _attach_task_ids_to_blocks(blocks, tasks)

    canonical: dict[str, Any] = {
        "schema_name": SCHEMA_NAME,
        "schema_version": SCHEMA_VERSION,
        "compiler_version": COMPILER_VERSION,
        "shadow_mode": True,
        "used_for_generation": False,
        "document": {
            "source_filename": str(source_filename or "source.mmd"),
            "source_sha256": _sha256_text(source),
            "source_chars": len(source),
            "line_count": source.count("\n") + (1 if source else 0),
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
    }
    aegis_mmd = _render_aegis_mmd(canonical)
    report = _validate(source, canonical, aegis_mmd, task_issues)
    canonical["shadow_validation"] = {
        "status": report["status"],
        "errors": report["summary"]["errors"],
        "warnings": report["summary"]["warnings"],
        "ready_for_future_cutover": report["ready_for_future_cutover"],
    }
    return CompiledSource(canonical=canonical, aegis_mmd=aegis_mmd, report=report)


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _failure_bundle(
    mmd_text: str,
    *,
    source_filename: str,
    exc: Exception,
) -> CompiledSource:
    source = str(mmd_text or "")
    error_text = f"{exc.__class__.__name__}: {exc}"
    canonical = {
        "schema_name": SCHEMA_NAME,
        "schema_version": SCHEMA_VERSION,
        "compiler_version": COMPILER_VERSION,
        "shadow_mode": True,
        "used_for_generation": False,
        "document": {
            "source_filename": str(source_filename or "source.mmd"),
            "source_sha256": _sha256_text(source),
            "source_chars": len(source),
            "line_count": source.count("\n") + (1 if source else 0),
            "encoding": "utf-8",
        },
        "ordering_contract": {
            "source_order_locked": False,
            "topic_sequence_locked": False,
            "block_sequence_locked": False,
            "allow_model_reordering": False,
            "ambiguous_order_policy": "shadow_compiler_failed",
        },
        "section_sequence": [],
        "sections": [],
        "blocks": [],
        "figures": [],
        "images": [],
        "math": [],
        "tasks": [],
        "shadow_validation": {
            "status": "failed",
            "errors": 1,
            "warnings": 0,
            "ready_for_future_cutover": False,
        },
    }
    aegis_mmd = (
        "<!-- AEGIS CANONICAL SOURCE SHADOW -->\n"
        f"<!-- source_sha256: {_sha256_text(source)} -->\n"
        "<!-- compilation_status: failed -->\n"
        f"<!-- error: {error_text.replace('--', '—')} -->\n"
    )
    report = {
        "schema_name": SCHEMA_NAME,
        "schema_version": SCHEMA_VERSION,
        "compiler_version": COMPILER_VERSION,
        "source_sha256": _sha256_text(source),
        "status": "failed",
        "used_for_generation": False,
        "ready_for_future_cutover": False,
        "summary": {
            "source_chars": len(source),
            "sections": 0,
            "blocks": 0,
            "figures": 0,
            "images": 0,
            "math_spans": 0,
            "tasks": 0,
            "errors": 1,
            "warnings": 0,
        },
        "issues": [{
            "severity": "error",
            "code": "shadow_compiler_exception",
            "message": error_text,
        }],
    }
    return CompiledSource(canonical=canonical, aegis_mmd=aegis_mmd, report=report)


def write_shadow_artifacts(
    directory: Path,
    *,
    mmd_text: str,
    source_filename: str,
) -> dict[str, Any]:
    """Write all four Phase-1 artifacts and return their inspectable manifest."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    try:
        compiled = compile_source(mmd_text, source_filename=source_filename)
    except Exception as exc:  # the shadow layer must never break conversion
        compiled = _failure_bundle(
            mmd_text,
            source_filename=source_filename,
            exc=exc,
        )

    payloads = {
        "raw_mmd": str(mmd_text or ""),
        "canonical_json": _json_text(compiled.canonical),
        "aegis_mmd": compiled.aegis_mmd,
        "report": _json_text(compiled.report),
    }
    for key, content in payloads.items():
        _atomic_write(directory / ARTIFACT_SPECS[key]["filename"], content)
    return artifact_manifest(directory)


def clear_shadow_artifacts(directory: Path) -> None:
    shutil.rmtree(Path(directory), ignore_errors=True)


def artifact_manifest(directory: Path) -> dict[str, Any]:
    directory = Path(directory)
    report_path = directory / ARTIFACT_SPECS["report"]["filename"]
    report: dict[str, Any] = {}
    if report_path.exists():
        try:
            loaded = json.loads(report_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                report = loaded
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            report = {}

    files: list[dict[str, Any]] = []
    for key, spec in ARTIFACT_SPECS.items():
        path = directory / spec["filename"]
        if not path.exists() or not path.is_file():
            continue
        files.append({
            "kind": key,
            "label": spec["label"],
            "filename": spec["filename"],
            "media_type": spec["media_type"],
            "size_bytes": path.stat().st_size,
        })
    return {
        "available": len(files) == len(ARTIFACT_SPECS),
        "shadow_mode": True,
        "used_for_generation": False,
        "schema_version": report.get("schema_version") or SCHEMA_VERSION,
        "compiler_version": report.get("compiler_version") or COMPILER_VERSION,
        "status": report.get("status") or ("unavailable" if not files else "unknown"),
        "ready_for_future_cutover": bool(report.get("ready_for_future_cutover")),
        "source_sha256": str(report.get("source_sha256") or ""),
        "summary": report.get("summary") if isinstance(report.get("summary"), dict) else {},
        "files": files,
    }


def artifact_path(directory: Path, kind: str) -> tuple[Path, dict[str, str]]:
    spec = ARTIFACT_SPECS.get(str(kind or ""))
    if spec is None:
        raise ValueError(
            "unknown source artifact; expected one of: "
            + ", ".join(sorted(ARTIFACT_SPECS))
        )
    path = Path(directory) / spec["filename"]
    if not path.exists() or not path.is_file():
        raise ValueError("source artifact is not available for this upload")
    return path, spec

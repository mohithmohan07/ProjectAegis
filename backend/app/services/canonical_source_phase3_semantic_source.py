"""Deterministic semantic-source renderer for the Phase 3 graph.

The original PDF and Phase 2 ACSD remain immutable evidence. This renderer
produces the model-facing MMD view from stable graph nodes, preserving source
order, exact prose, image URLs, KaTeX, and table/list content while removing
publisher layout commands that must never leak into generated concept rows.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from . import canonical_source as canonical
from . import canonical_source_phase3 as phase3
from . import katex_rules as kr

VERSION = "1.0.0"

ARTIFACT_SPECS: dict[str, dict[str, str]] = {
    "semantic_source": {
        "filename": "source.semantic.mmd",
        "media_type": "text/markdown; charset=utf-8",
        "label": "Phase 3 graph-derived semantic MMD",
    },
    "semantic_source_report": {
        "filename": "source.semantic-report.json",
        "media_type": "application/json; charset=utf-8",
        "label": "Phase 3 semantic-source validation report",
    },
}

_LIST_BEGIN_END_RE = re.compile(r"\\(?:begin|end)\{(?:itemize|enumerate)\}")
_LIST_ITEM_RE = re.compile(r"\\item(?:\[(?P<label>[^\]]+)\])?\s*")
_RAW_LAYOUT_COMMAND_RE = re.compile(
    r"\\(?:begin|end)\{(?:figure|center|table)\}|"
    r"\\captionsetup\{[^{}]*\}|"
    r"\\caption\{(?:[^{}]|\{[^{}]*\})*\}",
    re.IGNORECASE | re.DOTALL,
)
_FORBIDDEN_SOURCE_LATEX = (
    r"\begin{figure}",
    r"\end{figure}",
    r"\captionsetup",
    r"\includegraphics",
    r"\begin{itemize}",
    r"\end{itemize}",
    r"\item",
    r"\section",
    r"\subsection",
)


def _sha256_text(value: object) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _normalize_lists(value: object) -> str:
    text = _LIST_BEGIN_END_RE.sub("\n", str(value or ""))

    def item(match: re.Match) -> str:
        label = re.sub(r"\s+", " ", str(match.group("label") or "")).strip()
        return f"\n- {label} " if label else "\n- "

    return _LIST_ITEM_RE.sub(item, text)


def _render_figure(block: dict[str, Any], graph: dict[str, Any]) -> str:
    figure_id = str(block.get("figure_id") or "")
    figure = next(
        (
            row for row in graph.get("figures") or []
            if isinstance(row, dict)
            and str(row.get("figure_id") or "") == figure_id
        ),
        {},
    )
    caption = str(figure.get("caption_raw") or "Source visual")
    caption = re.sub(r"\\times\b", "×", caption)
    caption = re.sub(r"\\_", "_", caption)
    caption = re.sub(
        r"(?<!\\)\$(?P<body>[^$]+)\$",
        lambda match: match.group("body"),
        caption,
    )
    caption = re.sub(r"\\[A-Za-z]+", "", caption)
    caption = re.sub(r"[{}]", "", caption)
    caption = re.sub(r"\s+", " ", caption).strip() or "Source visual"
    urls = [str(value) for value in figure.get("image_urls") or [] if str(value)]
    rendered: list[str] = []
    for index, url in enumerate(urls, start=1):
        alt = caption if len(urls) == 1 else f"{caption}, visual {index}"
        rendered.append(kr.image(url, alt))
    return "\n".join(rendered)


def _render_block(block: dict[str, Any], graph: dict[str, Any]) -> str:
    kind = str(block.get("kind") or "")
    if kind == "layout":
        return ""
    if kind == "figure" or block.get("figure_id"):
        return _render_figure(block, graph)
    raw = str(block.get("display_text") or block.get("exact_text") or "")
    raw = _normalize_lists(raw)
    raw = _RAW_LAYOUT_COMMAND_RE.sub("\n", raw)
    rendered = kr.canonicalize_rich_text(raw).strip()
    return rendered


def render_semantic_source(graph: dict[str, Any]) -> str:
    """Render one graph-authoritative MMD document in source order."""
    topics = [row for row in graph.get("topics") or [] if isinstance(row, dict)]
    topics.sort(key=lambda row: (
        int(row.get("source_start") or 0), int(row.get("order") or 0)
    ))
    subtopics = [
        row for row in graph.get("subtopics") or [] if isinstance(row, dict)
    ]
    subtopics.sort(key=lambda row: (
        int(row.get("source_start") or 0), int(row.get("order") or 0)
    ))
    blocks = [row for row in graph.get("blocks") or [] if isinstance(row, dict)]
    blocks.sort(key=lambda row: (
        int(row.get("order") or 0), int(row.get("source_start") or 0)
    ))

    topic_events: dict[int, list[dict[str, Any]]] = {}
    subtopic_events: dict[int, list[dict[str, Any]]] = {}
    heading_block_ids: set[str] = set()
    for topic in topics:
        topic_events.setdefault(int(topic.get("source_start") or 0), []).append(topic)
        if topic.get("heading_block_id"):
            heading_block_ids.add(str(topic.get("heading_block_id")))
    for subtopic in subtopics:
        subtopic_events.setdefault(
            int(subtopic.get("source_start") or 0), []
        ).append(subtopic)
        if subtopic.get("heading_block_id"):
            heading_block_ids.add(str(subtopic.get("heading_block_id")))

    lines: list[str] = []
    emitted_topics: set[str] = set()
    emitted_subtopics: set[str] = set()

    def emit_events(position: int) -> None:
        for topic in topic_events.get(position, []):
            topic_id = str(topic.get("topic_id") or "")
            if topic_id in emitted_topics:
                continue
            number = int(topic.get("number") or 0)
            title = str(topic.get("title") or "").strip()
            label = f"{number} {title}" if number else title
            lines.extend(["", f"# {label}", ""])
            emitted_topics.add(topic_id)
        for subtopic in subtopic_events.get(position, []):
            subtopic_id = str(subtopic.get("subtopic_id") or "")
            if subtopic_id in emitted_subtopics:
                continue
            major = int(subtopic.get("major") or 0)
            minor = int(subtopic.get("minor") or 0)
            title = str(subtopic.get("title") or "").strip()
            label = f"{major}.{minor} {title}" if major and minor else title
            lines.extend(["", f"## {label}", ""])
            emitted_subtopics.add(subtopic_id)

    event_positions = sorted(set(topic_events) | set(subtopic_events))
    event_cursor = 0
    for block in blocks:
        position = int(block.get("source_start") or 0)
        while event_cursor < len(event_positions) and event_positions[event_cursor] <= position:
            emit_events(event_positions[event_cursor])
            event_cursor += 1
        block_id = str(block.get("block_id") or "")
        if block_id in heading_block_ids:
            continue
        if block.get("kind") == "heading":
            title = str((block.get("heading") or {}).get("title") or "").strip()
            if title:
                lines.extend(["", f"**{title}**", ""])
            continue
        rendered = _render_block(block, graph)
        if rendered:
            lines.extend([rendered, ""])
    while event_cursor < len(event_positions):
        emit_events(event_positions[event_cursor])
        event_cursor += 1

    output = "\n".join(lines)
    output = re.sub(r"\n{4,}", "\n\n\n", output).strip() + "\n"
    return output


def validation_report(graph: dict[str, Any], source: str) -> dict[str, Any]:
    issues = list(kr.rich_text_issues(source))
    forbidden = [token for token in _FORBIDDEN_SOURCE_LATEX if token in source]
    if forbidden:
        issues.append("source_layout_latex")
    expected_urls = {
        str(url)
        for figure in graph.get("figures") or []
        if isinstance(figure, dict)
        for url in figure.get("image_urls") or []
        if str(url)
    }
    missing_urls = sorted(url for url in expected_urls if url not in source)
    topic_titles = phase3_graph_topic_titles(graph)
    topic_positions = [source.find(f"# {title}") for title in topic_titles]
    order_valid = all(position >= 0 for position in topic_positions) and (
        topic_positions == sorted(topic_positions)
    )
    return {
        "phase": "phase-3-semantic-source",
        "version": VERSION,
        "status": "passed" if not issues and not missing_urls and order_valid else "failed",
        "semantic_source_sha256": _sha256_text(source),
        "graph_sha256": str(graph.get("graph_sha256") or ""),
        "issues": sorted(set(issues)),
        "forbidden_layout_tokens": forbidden,
        "missing_image_urls": missing_urls,
        "topic_order_valid": order_valid,
        "summary": {
            "chars": len(source),
            "topics": len(topic_titles),
            "subtopics": len(graph.get("subtopics") or []),
            "images": len(expected_urls),
            "katex_spans": source.count("[Katex]"),
        },
    }


def phase3_graph_topic_titles(graph: dict[str, Any]) -> list[str]:
    rows = [row for row in graph.get("topics") or [] if isinstance(row, dict)]
    rows.sort(key=lambda row: int(row.get("order") or 0))
    result: list[str] = []
    for row in rows:
        number = int(row.get("number") or 0)
        title = str(row.get("title") or "").strip()
        result.append(f"{number} {title}" if number else title)
    return result


def write_artifacts(directory: Path, graph: dict[str, Any]) -> dict[str, Any]:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    source = render_semantic_source(graph)
    report = validation_report(graph, source)
    if report["status"] != "passed":
        raise ValueError(
            "Phase 3 semantic source failed deterministic validation: "
            + ", ".join(report["issues"] or report["missing_image_urls"])
        )
    canonical._atomic_write(
        directory / ARTIFACT_SPECS["semantic_source"]["filename"], source
    )
    canonical._atomic_write(
        directory / ARTIFACT_SPECS["semantic_source_report"]["filename"],
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
    )
    return report

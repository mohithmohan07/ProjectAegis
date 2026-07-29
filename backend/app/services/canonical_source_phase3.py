"""Phase 3 API-first semantic source graph for Build Concepts.

The Phase 1/2 ACSD remains the immutable evidence ledger for source order,
exact task wording, figures, images, mathematics, and source positions.  This
module adds stable semantic identities above that ledger so later concept and
Type work no longer depends on publisher heading strings.

The graph compiler is deliberately deterministic at this boundary.  API-led
hierarchy review and concept grounding plug into the same strict IDs in later
passes; they may classify or select existing evidence, but never rewrite the
source ledger.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Callable, Iterator

from . import canonical_source_phase21_structure as structure

PHASE = "phase-3-semantic-graph"
VERSION = "3.0.0"
COMPILER_VERSION = "phase-3-semantic-graph-1"
SOURCE_CONTRACT_MODE = "acsd-phase3-semantic-graph"

_ACTIVE_GRAPH: ContextVar[dict[str, Any] | None] = ContextVar(
    "aegis_phase3_active_semantic_graph", default=None
)
_TARGET_METADATA: ContextVar[dict[str, str]] = ContextVar(
    "aegis_phase3_target_metadata", default={}
)

_MAIN_NUMBER_RE = re.compile(
    r"(?:\\section\*?\{|^#{1,3}\s+)?\s*(?P<number>\d+)"
    r"(?:[.)-]?\s+)(?P<title>[^}\n]+)",
    re.IGNORECASE,
)
_SUB_NUMBER_RE = re.compile(
    r"(?:\\(?:section|subsection)\*?\{|^#{1,4}\s+)?\s*"
    r"(?P<major>\d+)[.．](?P<minor>\d+)\s+(?P<title>[^}\n]+)",
    re.IGNORECASE,
)
_NON_TOPIC_RE = re.compile(
    r"^(?:activity|activities|discuss|discussion|project|projects|"
    r"new\s+words?|glossary|source(?:\s+[a-z0-9]+)?|box(?:\s+\d+)?|"
    r"write\s+in\s+brief|think\s+about\s+it|let'?s\s+discuss|"
    r"exercise(?:s)?|questions?|answer\s+key|summary|recap|"
    r"some\s+important\s+dates?|meanings?\s+of\s+the\s+symbols?)$",
    re.IGNORECASE,
)

HierarchyProvider = Callable[[dict[str, Any]], dict[str, Any]]


def _sha256_text(value: object) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _json_sha256(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return _sha256_text(encoded)


def _normal(value: object) -> str:
    return re.sub(r"[^\w]+", " ", str(value or ""), flags=re.UNICODE).strip().casefold()


def _compact(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _clone(value: Any) -> Any:
    return copy.deepcopy(value)


def _as_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def subject_adapter(subject: object) -> str:
    """Return the universal semantic adapter without broad ``science`` traps."""
    value = _normal(subject)
    if any(token in value for token in (
        "computer science", "computer applications", "informatics",
        "artificial intelligence", "machine learning", "coding",
    )):
        return "computer_science"
    if any(token in value for token in (
        "social science", "history", "geography", "civics",
        "political science", "sociology",
    )):
        return "social_science"
    if any(token in value for token in (
        "environmental science", "environmental studies", " evs ",
    )) or value in {"evs", "environmental science", "environmental studies"}:
        return "environmental_science"
    if any(token in value for token in (
        "life science", "biology", "botany", "zoology",
    )):
        return "life_science"
    if any(token in value for token in (
        "electronics", "electrical engineering", "electronic science",
    )):
        return "electronics"
    if any(token in value for token in ("physics", "chemistry", "physical science")):
        return "physical_science"
    if any(token in value for token in ("mathematics", "maths", "math")):
        return "mathematics"
    if any(token in value for token in (
        "commerce", "commercial studies", "business studies",
        "accountancy", "accounting", "economics",
    )):
        return "commerce"
    if any(token in value for token in (
        "english", "hindi", "kannada", "language", "literature",
        "grammar", "communication",
    )):
        return "language_literature"
    if any(token in value for token in (
        "physical education", "health education", "yoga",
    )):
        return "health_physical_education"
    if any(token in value for token in (
        "fine arts", "visual arts", "art", "music", "dance", "craft",
    )):
        return "arts"
    if value == "science" or value.endswith(" general science") or "general science" in value:
        return "general_science"
    return "general"


@contextmanager
def activate_target_metadata(metadata: dict[str, object] | None) -> Iterator[None]:
    normalized = {
        str(key): str(value or "")
        for key, value in (metadata or {}).items()
        if str(key).strip()
    }
    token = _TARGET_METADATA.set(normalized)
    try:
        yield
    finally:
        _TARGET_METADATA.reset(token)


def target_metadata() -> dict[str, str]:
    return dict(_TARGET_METADATA.get() or {})


@contextmanager
def activate(graph: dict[str, Any] | None) -> Iterator[None]:
    token = _ACTIVE_GRAPH.set(_clone(graph) if graph else None)
    try:
        yield
    finally:
        _ACTIVE_GRAPH.reset(token)


def active_graph() -> dict[str, Any] | None:
    graph = _ACTIVE_GRAPH.get()
    return _clone(graph) if graph else None


def _heading_title(block: dict[str, Any]) -> str:
    heading = block.get("heading") if isinstance(block.get("heading"), dict) else {}
    return _compact(heading.get("title") or block.get("title") or "")


def _heading_level(block: dict[str, Any]) -> int:
    heading = block.get("heading") if isinstance(block.get("heading"), dict) else {}
    return _as_int(heading.get("level") or block.get("level"), 0)


def _main_number(block: dict[str, Any]) -> tuple[int, str] | None:
    raw = str(block.get("raw_text") or "")
    match = _MAIN_NUMBER_RE.search(raw)
    if not match or _SUB_NUMBER_RE.search(raw):
        return None
    return int(match.group("number")), _compact(match.group("title"))


def _sub_number(block: dict[str, Any]) -> tuple[int, int, str] | None:
    raw = str(block.get("raw_text") or "")
    match = _SUB_NUMBER_RE.search(raw)
    if not match:
        return None
    return (
        int(match.group("major")),
        int(match.group("minor")),
        _compact(match.group("title")),
    )


def _is_non_topic_title(title: object) -> bool:
    compact = _compact(title)
    return not compact or bool(_NON_TOPIC_RE.fullmatch(compact))


def _document_end(canonical: dict[str, Any], raw_source: str) -> int:
    values = [
        _as_int(block.get("source_end"), 0)
        for block in canonical.get("blocks") or []
        if isinstance(block, dict)
    ]
    return max(values or [len(str(raw_source or ""))])


def _source_contract_payload(canonical: dict[str, Any], metadata: dict[str, str]) -> dict:
    """Hash semantic dependencies, not mutable generated prose."""
    def select(rows: object, fields: tuple[str, ...]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            result.append({field: _clone(row.get(field)) for field in fields})
        return result

    return {
        "phase2_source_contract": _clone(canonical.get("source_contract") or {}),
        "document": _clone(canonical.get("document") or {}),
        "metadata": dict(sorted(metadata.items())),
        "sections": select(
            canonical.get("sections"),
            (
                "section_id", "order", "title", "level", "depth",
                "parent_section_id", "source_start", "source_end", "block_ids",
                "adjudicated_heading",
            ),
        ),
        "blocks": select(
            canonical.get("blocks"),
            (
                "block_id", "order", "kind", "source_start", "source_end",
                "section_id", "raw_sha256", "raw_text", "display_text",
                "task_ids", "figure_id", "image_ids", "math_ids", "heading",
            ),
        ),
        "tasks": select(
            canonical.get("tasks"),
            (
                "task_id", "qid", "order", "source_start", "source_end",
                "section_id", "identity_key", "raw_prompt", "display_prompt",
                "figure_refs", "image_urls", "chapter_wide", "source_label",
            ),
        ),
        "figures": select(
            canonical.get("figures"),
            (
                "figure_id", "order", "block_id", "section_id", "source_start",
                "source_end", "caption_raw", "image_ids", "image_urls",
            ),
        ),
        "math": select(
            canonical.get("math"),
            (
                "math_id", "block_id", "source_start", "source_end", "mode",
                "raw_text", "canonical_latex", "display_text",
            ),
        ),
        "source_overlays": _clone(canonical.get("source_overlays") or []),
        "source_adjudication": _clone(canonical.get("source_adjudication") or {}),
    }


def source_contract_sha256(
    canonical: dict[str, Any], metadata: dict[str, object] | None = None
) -> str:
    effective = {
        str(key): str(value or "")
        for key, value in (metadata or target_metadata()).items()
    }
    return _json_sha256(_source_contract_payload(canonical, effective))


def _numbered_topic_specs(canonical: dict[str, Any]) -> list[dict[str, Any]]:
    mains, _subsections = structure.numbered_heading_inventory(canonical)
    specs: list[dict[str, Any]] = []
    for number, block in mains.items():
        if not isinstance(block, dict):
            continue
        title = _heading_title(block) or _compact(
            (block.get("adjudicated_heading") or {}).get("title")
        )
        if not title:
            parsed = _main_number(block)
            title = parsed[1] if parsed else f"Section {number}"
        specs.append({
            "number": int(number),
            "title": title,
            "source_start": _as_int(block.get("source_start"), 0),
            "source_end": _as_int(block.get("source_end"), 0),
            "section_id": str(block.get("section_id") or ""),
            "heading_block_id": str(block.get("block_id") or ""),
            "origin": "adjudicated_pdf" if block.get("adjudicated_heading") else "numbered_heading",
        })
    specs.sort(key=lambda item: (item["source_start"], item["number"]))
    return specs


def _unnumbered_topic_specs(
    canonical: dict[str, Any], metadata: dict[str, str]
) -> list[dict[str, Any]]:
    chapter_key = _normal(metadata.get("chapter_title") or "")
    sections = [
        section for section in canonical.get("sections") or []
        if isinstance(section, dict)
    ]
    sections.sort(key=lambda item: (
        _as_int(item.get("source_start"), 0),
        _as_int(item.get("order"), 0),
    ))
    candidates: list[dict[str, Any]] = []
    for section in sections:
        title = _compact(section.get("title") or "")
        if _is_non_topic_title(title):
            continue
        if _as_int(section.get("depth"), 1) > 1:
            continue
        if chapter_key and _normal(title) == chapter_key:
            continue
        candidates.append({
            "number": 0,
            "title": title,
            "source_start": _as_int(section.get("source_start"), 0),
            "source_end": _as_int(section.get("source_end"), 0),
            "section_id": str(section.get("section_id") or ""),
            "heading_block_id": next(iter(section.get("block_ids") or []), ""),
            "origin": "unnumbered_heading",
        })
    if len(candidates) == 1 and not chapter_key:
        return candidates
    return candidates


def _topic_specs(
    canonical: dict[str, Any], raw_source: str, metadata: dict[str, str]
) -> list[dict[str, Any]]:
    specs = _numbered_topic_specs(canonical)
    if not specs:
        specs = _unnumbered_topic_specs(canonical, metadata)
    if not specs:
        title = _compact(
            metadata.get("chapter_title")
            or (canonical.get("document") or {}).get("source_filename")
            or "Chapter"
        )
        specs = [{
            "number": 0,
            "title": title,
            "source_start": 0,
            "source_end": _document_end(canonical, raw_source),
            "section_id": "",
            "heading_block_id": "",
            "origin": "headingless_fallback",
        }]
    end = _document_end(canonical, raw_source)
    for index, spec in enumerate(specs):
        next_start = (
            specs[index + 1]["source_start"] if index + 1 < len(specs) else end
        )
        spec["source_end"] = max(spec["source_start"], next_start)
    return specs


def _subtopic_specs(
    canonical: dict[str, Any], topics: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    topic_by_number = {
        int(topic.get("number") or 0): topic
        for topic in topics if int(topic.get("number") or 0) > 0
    }
    blocks = [
        block for block in canonical.get("blocks") or []
        if isinstance(block, dict) and block.get("kind") == "heading"
    ]
    parsed: list[dict[str, Any]] = []
    for block in blocks:
        value = _sub_number(block)
        if value is None:
            continue
        major, minor, title = value
        parent = topic_by_number.get(major)
        if parent is None:
            continue
        parsed.append({
            "major": major,
            "minor": minor,
            "title": title or _heading_title(block),
            "source_start": _as_int(block.get("source_start"), 0),
            "source_end": _as_int(block.get("source_end"), 0),
            "section_id": str(block.get("section_id") or ""),
            "heading_block_id": str(block.get("block_id") or ""),
            "topic_id": parent["topic_id"],
        })
    if not parsed:
        topic_sections = {
            str(topic.get("section_id") or ""): topic for topic in topics
            if topic.get("section_id")
        }
        for section in canonical.get("sections") or []:
            if not isinstance(section, dict):
                continue
            parent = topic_sections.get(str(section.get("parent_section_id") or ""))
            title = _compact(section.get("title") or "")
            if parent is None or _is_non_topic_title(title):
                continue
            parsed.append({
                "major": 0,
                "minor": 0,
                "title": title,
                "source_start": _as_int(section.get("source_start"), 0),
                "source_end": _as_int(section.get("source_end"), 0),
                "section_id": str(section.get("section_id") or ""),
                "heading_block_id": next(iter(section.get("block_ids") or []), ""),
                "topic_id": parent["topic_id"],
            })
    parsed.sort(key=lambda item: (item["source_start"], item["minor"], item["title"]))
    by_topic: dict[str, list[dict[str, Any]]] = {}
    for item in parsed:
        by_topic.setdefault(item["topic_id"], []).append(item)
    result: list[dict[str, Any]] = []
    topic_map = {topic["topic_id"]: topic for topic in topics}
    for topic_id, rows in by_topic.items():
        parent = topic_map[topic_id]
        for index, item in enumerate(rows, start=1):
            next_start = rows[index]["source_start"] if index < len(rows) else parent["source_end"]
            item["source_end"] = max(item["source_start"], next_start)
            item["subtopic_id"] = f"SUBTOPIC-{int(parent['order']):04d}-{index:02d}"
            item["order"] = index
            result.append(item)
    result.sort(key=lambda item: item["source_start"])
    return result


def _topic_for_position(topics: list[dict[str, Any]], position: int) -> dict[str, Any]:
    if not topics:
        return {}
    prior = [topic for topic in topics if int(topic["source_start"]) <= position]
    return prior[-1] if prior else topics[0]


def _subtopic_for_position(
    subtopics: list[dict[str, Any]], topic_id: str, position: int
) -> dict[str, Any]:
    matches = [
        subtopic for subtopic in subtopics
        if subtopic.get("topic_id") == topic_id
        and int(subtopic["source_start"]) <= position < int(subtopic["source_end"])
    ]
    return matches[-1] if matches else {}


def _task_source_blocks(
    task: dict[str, Any], blocks: list[dict[str, Any]]
) -> list[str]:
    task_id = str(task.get("task_id") or "")
    owned = [
        str(block.get("block_id") or "")
        for block in blocks
        if task_id and task_id in (block.get("task_ids") or [])
    ]
    if owned:
        return [block_id for block_id in owned if block_id]
    start = _as_int(task.get("source_start"), 0)
    end = max(start, _as_int(task.get("source_end"), start))
    overlapping = [
        str(block.get("block_id") or "")
        for block in blocks
        if _as_int(block.get("source_start"), 0) < end
        and _as_int(block.get("source_end"), 0) > start
    ]
    if overlapping:
        return [block_id for block_id in overlapping if block_id]
    prompt = _normal(task.get("raw_prompt") or task.get("display_prompt") or "")
    if prompt:
        for block in blocks:
            text = _normal(block.get("raw_text") or block.get("display_text") or "")
            if min(len(prompt), len(text)) >= 24 and (prompt in text or text in prompt):
                block_id = str(block.get("block_id") or "")
                return [block_id] if block_id else []
    return []


def _apply_hierarchy_review(
    topics: list[dict[str, Any]],
    subtopics: list[dict[str, Any]],
    provider: HierarchyProvider | None,
    payload: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Accept only ID-constrained hierarchy decisions from an optional API pass."""
    if provider is None:
        return topics, subtopics, []
    response = provider(_clone(payload))
    if not isinstance(response, dict):
        return topics, subtopics, [{
            "severity": "warning",
            "code": "phase3_hierarchy_provider_invalid",
            "message": "Hierarchy provider returned a non-object response.",
        }]
    allowed_topics = {topic["topic_id"] for topic in topics}
    allowed_subtopics = {subtopic["subtopic_id"] for subtopic in subtopics}
    decisions = response.get("decisions") or []
    warnings: list[dict[str, Any]] = []
    for decision in decisions:
        if not isinstance(decision, dict):
            continue
        topic_id = str(decision.get("topic_id") or "")
        subtopic_id = str(decision.get("subtopic_id") or "")
        if topic_id and topic_id not in allowed_topics:
            warnings.append({
                "severity": "warning",
                "code": "phase3_hierarchy_unknown_topic_id",
                "message": f"Hierarchy provider selected unknown topic ID {topic_id}.",
            })
        if subtopic_id and subtopic_id not in allowed_subtopics:
            warnings.append({
                "severity": "warning",
                "code": "phase3_hierarchy_unknown_subtopic_id",
                "message": f"Hierarchy provider selected unknown subtopic ID {subtopic_id}.",
            })
    return topics, subtopics, warnings


def build_semantic_graph(
    canonical: dict[str, Any],
    raw_source: str,
    *,
    metadata: dict[str, object] | None = None,
    hierarchy_provider: HierarchyProvider | None = None,
) -> dict[str, Any]:
    """Compile one stable semantic graph from a verified Phase 2 ACSD bundle."""
    canonical = _clone(canonical or {})
    effective_metadata = {
        str(key): str(value or "")
        for key, value in (metadata or target_metadata()).items()
    }
    contract_sha = source_contract_sha256(canonical, effective_metadata)
    specs = _topic_specs(canonical, raw_source, effective_metadata)
    topics: list[dict[str, Any]] = []
    for order, spec in enumerate(specs, start=1):
        topics.append({
            "topic_id": f"TOPIC-{order:04d}",
            "order": order,
            "number": int(spec.get("number") or 0),
            "title": _compact(spec.get("title") or f"Topic {order}"),
            "source_start": int(spec.get("source_start") or 0),
            "source_end": int(spec.get("source_end") or 0),
            "section_id": str(spec.get("section_id") or ""),
            "heading_block_id": str(spec.get("heading_block_id") or ""),
            "origin": str(spec.get("origin") or "source_heading"),
            "subtopic_ids": [],
            "block_ids": [],
            "task_ids": [],
        })
    subtopics = _subtopic_specs(canonical, topics)
    by_topic = {topic["topic_id"]: topic for topic in topics}
    for subtopic in subtopics:
        by_topic[subtopic["topic_id"]]["subtopic_ids"].append(subtopic["subtopic_id"])

    hierarchy_payload = {
        "contract": {
            "version": VERSION,
            "allowed_topic_ids": [topic["topic_id"] for topic in topics],
            "allowed_subtopic_ids": [item["subtopic_id"] for item in subtopics],
            "source_order_locked": True,
        },
        "metadata": effective_metadata,
        "topics": _clone(topics),
        "subtopics": _clone(subtopics),
    }
    topics, subtopics, review_warnings = _apply_hierarchy_review(
        topics, subtopics, hierarchy_provider, hierarchy_payload
    )
    by_topic = {topic["topic_id"]: topic for topic in topics}

    blocks: list[dict[str, Any]] = []
    source_blocks = [
        block for block in canonical.get("blocks") or [] if isinstance(block, dict)
    ]
    source_blocks.sort(key=lambda item: (
        _as_int(item.get("order"), 0),
        _as_int(item.get("source_start"), 0),
        str(item.get("block_id") or ""),
    ))
    for block in source_blocks:
        position = _as_int(block.get("source_start"), 0)
        topic = _topic_for_position(topics, position)
        subtopic = _subtopic_for_position(
            subtopics, str(topic.get("topic_id") or ""), position
        )
        row = {
            "block_id": str(block.get("block_id") or ""),
            "order": _as_int(block.get("order"), len(blocks) + 1),
            "kind": str(block.get("kind") or "unknown"),
            "section_id": str(block.get("section_id") or ""),
            "source_start": position,
            "source_end": _as_int(block.get("source_end"), position),
            "exact_text": str(block.get("raw_text") or ""),
            "display_text": str(block.get("display_text") or block.get("raw_text") or ""),
            "raw_sha256": str(block.get("raw_sha256") or _sha256_text(block.get("raw_text") or "")),
            "topic_id": str(topic.get("topic_id") or ""),
            "subtopic_id": str(subtopic.get("subtopic_id") or ""),
            "task_ids": [str(value) for value in block.get("task_ids") or [] if str(value)],
            "figure_id": str(block.get("figure_id") or ""),
            "image_ids": [str(value) for value in block.get("image_ids") or [] if str(value)],
            "math_ids": [str(value) for value in block.get("math_ids") or [] if str(value)],
            "heading": _clone(block.get("heading") or {}),
        }
        blocks.append(row)
        if row["topic_id"] in by_topic:
            by_topic[row["topic_id"]]["block_ids"].append(row["block_id"])

    block_by_id = {block["block_id"]: block for block in blocks if block["block_id"]}
    figures = [
        _clone(item) for item in canonical.get("figures") or [] if isinstance(item, dict)
    ]
    figure_ids = {str(item.get("figure_id") or "") for item in figures}
    tasks: list[dict[str, Any]] = []
    source_tasks = [
        item for item in canonical.get("tasks") or [] if isinstance(item, dict)
    ]
    source_tasks.sort(key=lambda item: (
        _as_int(item.get("order"), 0),
        _as_int(item.get("source_start"), 0),
        str(item.get("qid") or ""),
    ))
    for order, task in enumerate(source_tasks, start=1):
        source_block_ids = _task_source_blocks(task, source_blocks)
        known_source_blocks = [
            block_id for block_id in source_block_ids if block_id in block_by_id
        ]
        position = _as_int(task.get("source_start"), 0)
        topic = _topic_for_position(topics, position)
        if known_source_blocks:
            block_topic = block_by_id[known_source_blocks[0]].get("topic_id")
            topic = by_topic.get(str(block_topic or ""), topic)
        subtopic = _subtopic_for_position(
            subtopics, str(topic.get("topic_id") or ""), position
        )
        if known_source_blocks:
            block_subtopic = block_by_id[known_source_blocks[0]].get("subtopic_id")
            if block_subtopic:
                subtopic = next(
                    (item for item in subtopics if item["subtopic_id"] == block_subtopic),
                    subtopic,
                )
        chapter_wide = bool(task.get("chapter_wide"))
        topic_id = "" if chapter_wide else str(topic.get("topic_id") or "")
        subtopic_id = "" if chapter_wide else str(subtopic.get("subtopic_id") or "")
        refs = [
            str(value) for value in task.get("figure_refs") or []
            if str(value) in figure_ids
        ]
        row = {
            "task_id": str(task.get("task_id") or f"TASK-{order:05d}"),
            "qid": str(task.get("qid") or f"QINV-{order:04d}"),
            "order": order,
            "source_start": position,
            "source_end": _as_int(task.get("source_end"), position),
            "section_id": str(task.get("section_id") or ""),
            "source_label": str(task.get("source_label") or ""),
            "exact_text": str(task.get("raw_prompt") or ""),
            "display_prompt": str(task.get("display_prompt") or task.get("raw_prompt") or ""),
            "identity_key": str(task.get("identity_key") or ""),
            "chapter_wide": chapter_wide,
            "scope": "chapter" if chapter_wide else "topic",
            "topic_id": topic_id,
            "subtopic_id": subtopic_id,
            "suggested_topic_id": str(topic.get("topic_id") or ""),
            "source_block_ids": known_source_blocks,
            "figure_ids": refs,
            "image_urls": [str(value) for value in task.get("image_urls") or [] if str(value)],
            "requires_visual": bool(task.get("requires_visual")),
            "requires_context": bool(task.get("requires_context")),
            "legacy_topic_hint": str(task.get("topic_hint") or ""),
        }
        tasks.append(row)
        if topic_id in by_topic:
            by_topic[topic_id]["task_ids"].append(row["task_id"])

    graph = {
        "schema_name": "Aegis Universal Semantic Source Graph",
        "version": VERSION,
        "compiler_version": COMPILER_VERSION,
        "phase": PHASE,
        "source_contract_mode": SOURCE_CONTRACT_MODE,
        "source_contract_sha256": contract_sha,
        "source_sha256": str(
            (canonical.get("source_contract") or {}).get("source_sha256")
            or (canonical.get("document") or {}).get("source_sha256")
            or _sha256_text(raw_source)
        ),
        "metadata": effective_metadata,
        "subject_adapter": subject_adapter(effective_metadata.get("subject")),
        "source_order_locked": True,
        "topics": topics,
        "subtopics": subtopics,
        "blocks": blocks,
        "tasks": tasks,
        "figures": figures,
        "images": _clone(canonical.get("images") or []),
        "math": _clone(canonical.get("math") or []),
        "relations": [],
        "review_warnings": review_warnings,
    }
    for topic in topics:
        graph["relations"].append({
            "relation": "BELONGS_TO_DOCUMENT",
            "from_id": topic["topic_id"],
            "to_id": "DOCUMENT",
        })
    for subtopic in subtopics:
        graph["relations"].append({
            "relation": "PARENT_OF",
            "from_id": subtopic["topic_id"],
            "to_id": subtopic["subtopic_id"],
        })
    for task in tasks:
        for block_id in task["source_block_ids"]:
            graph["relations"].append({
                "relation": "DERIVED_FROM",
                "from_id": task["task_id"],
                "to_id": block_id,
            })
        for figure_id in task["figure_ids"]:
            graph["relations"].append({
                "relation": "REFERS_TO_FIGURE",
                "from_id": task["task_id"],
                "to_id": figure_id,
            })

    report = validate_graph(graph)
    graph["validation"] = report
    graph["status"] = "passed" if not report["errors"] else "failed"
    digest_payload = {key: value for key, value in graph.items() if key != "graph_sha256"}
    graph["graph_sha256"] = _json_sha256(digest_payload)
    return graph


def validate_graph(graph: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = list(graph.get("review_warnings") or [])

    def duplicate_values(rows: list[dict], field: str) -> list[str]:
        seen: set[str] = set()
        duplicates: set[str] = set()
        for row in rows:
            value = str(row.get(field) or "")
            if not value:
                continue
            if value in seen:
                duplicates.add(value)
            seen.add(value)
        return sorted(duplicates)

    topics = [row for row in graph.get("topics") or [] if isinstance(row, dict)]
    subtopics = [row for row in graph.get("subtopics") or [] if isinstance(row, dict)]
    blocks = [row for row in graph.get("blocks") or [] if isinstance(row, dict)]
    tasks = [row for row in graph.get("tasks") or [] if isinstance(row, dict)]
    figures = [row for row in graph.get("figures") or [] if isinstance(row, dict)]

    for field, rows in (
        ("topic_id", topics), ("subtopic_id", subtopics),
        ("block_id", blocks), ("task_id", tasks), ("qid", tasks),
        ("figure_id", figures),
    ):
        duplicates = duplicate_values(rows, field)
        if duplicates:
            errors.append({
                "code": "phase3_duplicate_identity",
                "field": field,
                "values": duplicates,
                "message": f"Duplicate {field} values: {', '.join(duplicates)}",
            })

    topic_ids = {str(row.get("topic_id") or "") for row in topics}
    subtopic_ids = {str(row.get("subtopic_id") or "") for row in subtopics}
    block_ids = {str(row.get("block_id") or "") for row in blocks}
    figure_ids = {str(row.get("figure_id") or "") for row in figures}

    topic_positions = [int(row.get("source_start") or 0) for row in topics]
    if topic_positions != sorted(topic_positions):
        errors.append({
            "code": "phase3_topic_order_drift",
            "message": "Topic sequence no longer follows source order.",
        })
    block_orders = [int(row.get("order") or 0) for row in blocks]
    if block_orders != sorted(block_orders):
        errors.append({
            "code": "phase3_block_order_drift",
            "message": "Block sequence no longer follows ACSD order.",
        })
    task_orders = [int(row.get("order") or 0) for row in tasks]
    if task_orders != sorted(task_orders):
        errors.append({
            "code": "phase3_task_order_drift",
            "message": "Task sequence no longer follows canonical QID order.",
        })

    for subtopic in subtopics:
        if str(subtopic.get("topic_id") or "") not in topic_ids:
            errors.append({
                "code": "phase3_unknown_subtopic_parent",
                "subtopic_id": subtopic.get("subtopic_id"),
                "message": "Subtopic points to an unknown parent topic.",
            })
    for block in blocks:
        if str(block.get("topic_id") or "") not in topic_ids:
            errors.append({
                "code": "phase3_unscoped_block",
                "block_id": block.get("block_id"),
                "message": "Source block has no valid canonical topic.",
            })
        subtopic_id = str(block.get("subtopic_id") or "")
        if subtopic_id and subtopic_id not in subtopic_ids:
            errors.append({
                "code": "phase3_unknown_block_subtopic",
                "block_id": block.get("block_id"),
                "message": "Source block points to an unknown subtopic.",
            })
    for task in tasks:
        if not task.get("chapter_wide") and str(task.get("topic_id") or "") not in topic_ids:
            errors.append({
                "code": "phase3_unscoped_task",
                "qid": task.get("qid"),
                "message": "Non-chapter-wide task has no canonical topic.",
            })
        unknown_blocks = sorted(set(task.get("source_block_ids") or []) - block_ids)
        if unknown_blocks:
            errors.append({
                "code": "phase3_unknown_task_block",
                "qid": task.get("qid"),
                "block_ids": unknown_blocks,
                "message": "Task points to unknown source blocks.",
            })
        unknown_figures = sorted(set(task.get("figure_ids") or []) - figure_ids)
        if unknown_figures:
            errors.append({
                "code": "phase3_unknown_task_figure",
                "qid": task.get("qid"),
                "figure_ids": unknown_figures,
                "message": "Task points to unknown Figures.",
            })
        if not task.get("source_block_ids"):
            warnings.append({
                "code": "phase3_task_without_source_block",
                "qid": task.get("qid"),
                "message": "Task has no materialized source-block owner.",
            })

    if not str(graph.get("source_contract_sha256") or ""):
        errors.append({
            "code": "phase3_missing_source_contract_hash",
            "message": "Semantic graph has no source-contract hash.",
        })
    return {"errors": errors, "warnings": warnings}


def graph_is_current(
    graph: dict[str, Any] | None,
    canonical: dict[str, Any],
    *,
    metadata: dict[str, object] | None = None,
) -> bool:
    if not isinstance(graph, dict):
        return False
    if graph.get("version") != VERSION or graph.get("compiler_version") != COMPILER_VERSION:
        return False
    return str(graph.get("source_contract_sha256") or "") == source_contract_sha256(
        canonical, metadata
    )


def topic_packets(graph: dict[str, Any]) -> list[dict[str, Any]]:
    """Return source-grounded concept packets in immutable textbook order."""
    blocks = {
        str(block.get("block_id") or ""): block
        for block in graph.get("blocks") or [] if isinstance(block, dict)
    }
    tasks = [item for item in graph.get("tasks") or [] if isinstance(item, dict)]
    subtopics = [item for item in graph.get("subtopics") or [] if isinstance(item, dict)]
    packets: list[dict[str, Any]] = []
    for topic in graph.get("topics") or []:
        if not isinstance(topic, dict):
            continue
        topic_id = str(topic.get("topic_id") or "")
        ordered_blocks = [
            _clone(blocks[block_id])
            for block_id in topic.get("block_ids") or [] if block_id in blocks
        ]
        packets.append({
            "topic_id": topic_id,
            "title": str(topic.get("title") or ""),
            "order": int(topic.get("order") or 0),
            "source_start": int(topic.get("source_start") or 0),
            "source_end": int(topic.get("source_end") or 0),
            "subtopics": [
                _clone(item) for item in subtopics if item.get("topic_id") == topic_id
            ],
            "blocks": ordered_blocks,
            "tasks": [
                _clone(item) for item in tasks
                if item.get("topic_id") == topic_id
                or item.get("suggested_topic_id") == topic_id
            ],
            "contract": {
                "allowed_topic_id": topic_id,
                "allowed_block_ids": [item["block_id"] for item in ordered_blocks],
                "source_order_locked": True,
                "exact_source_text_locked": True,
            },
        })
    return packets


def annotate_inventory(inventory: dict[str, Any], graph: dict[str, Any]) -> dict[str, Any]:
    result = _clone(inventory or {})
    by_qid = {
        str(item.get("qid") or ""): item
        for item in graph.get("tasks") or [] if isinstance(item, dict)
    }
    for item in result.get("items") or []:
        if not isinstance(item, dict):
            continue
        task = by_qid.get(str(item.get("qid") or ""))
        if not task:
            continue
        item["_semantic_topic_id"] = str(task.get("topic_id") or "")
        item["_semantic_suggested_topic_id"] = str(task.get("suggested_topic_id") or "")
        item["_semantic_subtopic_id"] = str(task.get("subtopic_id") or "")
        item["_semantic_source_block_ids"] = list(task.get("source_block_ids") or [])
        item["_semantic_scope"] = str(task.get("scope") or "topic")
        item["_semantic_graph_version"] = VERSION
    result["semantic_graph"] = {
        "version": VERSION,
        "compiler_version": COMPILER_VERSION,
        "source_contract_sha256": graph.get("source_contract_sha256"),
        "graph_sha256": graph.get("graph_sha256"),
    }
    return result


def _case_qids(case: object) -> list[str]:
    if not isinstance(case, dict):
        return []
    qids: list[str] = []
    direct = str(case.get("source_question_id") or "").strip()
    if direct:
        qids.append(direct)
    for example in case.get("examples") or []:
        if isinstance(example, dict):
            qid = str(example.get("source_question_id") or "").strip()
            if qid and qid not in qids:
                qids.append(qid)
    return qids


def _type_qids(mtype: dict[str, Any]) -> list[str]:
    qids: list[str] = []
    direct = mtype.get("source_question_ids") or []
    if isinstance(direct, str):
        direct = [direct]
    for value in direct:
        qid = str(value or "").strip()
        if qid and qid not in qids:
            qids.append(qid)
    one = str(mtype.get("source_question_id") or "").strip()
    if one and one not in qids:
        qids.append(one)
    for case in mtype.get("case_prompts") or []:
        for qid in _case_qids(case):
            if qid not in qids:
                qids.append(qid)
    return qids


def scope_for_qids(qids: list[str], graph: dict[str, Any]) -> dict[str, Any]:
    by_qid = {
        str(item.get("qid") or ""): item
        for item in graph.get("tasks") or [] if isinstance(item, dict)
    }
    topic_ids: list[str] = []
    suggested_topic_ids: list[str] = []
    subtopic_ids: list[str] = []
    source_block_ids: list[str] = []
    missing: list[str] = []
    for qid in qids:
        task = by_qid.get(str(qid or ""))
        if task is None:
            missing.append(str(qid or ""))
            continue
        for field, target in (
            ("topic_id", topic_ids),
            ("suggested_topic_id", suggested_topic_ids),
            ("subtopic_id", subtopic_ids),
        ):
            value = str(task.get(field) or "")
            if value and value not in target:
                target.append(value)
        for block_id in task.get("source_block_ids") or []:
            if block_id not in source_block_ids:
                source_block_ids.append(block_id)
    effective_topics = topic_ids
    if len(effective_topics) > 1:
        scope = "cross_topic_synthesis"
    elif effective_topics:
        scope = "normal"
    else:
        scope = "chapter"
    return {
        "canonical_topic_ids": effective_topics,
        "canonical_subtopic_ids": subtopic_ids,
        "source_block_ids": source_block_ids,
        "placement_scope": scope,
        "missing_qids": missing,
    }


def annotate_mined_types(mined_types: dict[str, Any], graph: dict[str, Any]) -> dict[str, Any]:
    result = _clone(mined_types or {})
    rows = result.get("types") if isinstance(result.get("types"), list) else []
    for mtype in rows:
        if not isinstance(mtype, dict):
            continue
        scope = scope_for_qids(_type_qids(mtype), graph)
        mtype.update(scope)
        mtype["_semantic_graph_version"] = VERSION
        for case in mtype.get("case_prompts") or []:
            if not isinstance(case, dict):
                continue
            case_scope = scope_for_qids(_case_qids(case), graph)
            case.update(case_scope)
            case["canonical_topic_id"] = (
                case_scope["canonical_topic_ids"][0]
                if len(case_scope["canonical_topic_ids"]) == 1 else ""
            )
    result["semantic_graph"] = {
        "version": VERSION,
        "compiler_version": COMPILER_VERSION,
        "source_contract_sha256": graph.get("source_contract_sha256"),
        "graph_sha256": graph.get("graph_sha256"),
    }
    return result

"""Write normalized questions back to the canonical Bulk Import workbook.

Two header rows are emitted per content sheet (section bands + field names).
Writes are **append-only**: ``append_questions`` reads existing
``question_label`` values across all tabs and skips anything already present,
so re-running a generation never overwrites or deletes prior rows.
"""
from __future__ import annotations

import io
import re
from collections import defaultdict
from pathlib import Path

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from sqlalchemy.orm import Session

from . import (
    CHAPTER_FIELDS, TOPIC_FIELDS, CONCEPT_FIELDS, FIELDS_BY_KIND, SHEET_BY_KIND,
    SHEET_DOC_LINK, SECTION_BANDS, OBJECTIVE_GROUP_FIELDS, DESCRIPTIVE_GROUP_FIELDS,
    merge_sources, normalize_question_text, strip_title_tag, strip_topic_title,
)
from . import workbook_sync
from .. import models
from ..services import directory

_BAND_FILL = {
    "Chapter": "FCE4D6", "Topic": "FFF2CC", "Concept": "D9EAD3",
    "Group": "D0E0E3", "Question": "CFE2F3",
}


def _group_fields(kind: str) -> list[str]:
    return DESCRIPTIVE_GROUP_FIELDS if kind == "descriptive" else OBJECTIVE_GROUP_FIELDS


# Positional indices shared by every content sheet (front bands are identical).
_IDX_CHAPTER_TITLE = 0
_IDX_TOPIC_TITLE = len(CHAPTER_FIELDS)
_IDX_TOPIC_PRE_POST = _IDX_TOPIC_TITLE + TOPIC_FIELDS.index(
    "pre_post_learning")
_IDX_CONCEPT_TITLE = len(CHAPTER_FIELDS) + len(TOPIC_FIELDS)


def _q_start(kind: str) -> int:
    """Column index where the Question band's first ``question_label`` lives."""
    return _IDX_CONCEPT_TITLE + len(CONCEPT_FIELDS) + len(_group_fields(kind))


def _sheet_concept_len(header_row: tuple) -> int:
    return len(_sheet_concept_fields(header_row))


def _sheet_concept_fields(header_row: tuple) -> list[str]:
    """Concept-band fields present in a workbook (detected from the header).

    New workbooks use the canonical band (with ``parent_concept``, without the
    dropped ``keywords``/``related_concepts`` columns); legacy workbooks keep
    their own column positions and are appended to without shifting bands.
    """
    group_markers = set(OBJECTIVE_GROUP_FIELDS + DESCRIPTIVE_GROUP_FIELDS)
    fields: list[str] = []
    for idx in range(_IDX_CONCEPT_TITLE, len(header_row)):
        name = str(header_row[idx] or "").strip()
        if not name:
            continue
        if name in group_markers:
            break
        fields.append(name)
    return fields or CONCEPT_FIELDS


def _cell_str(row: tuple, idx: int) -> str:
    if idx >= len(row):
        return ""
    v = row[idx]
    return "" if v is None else str(v).strip()


# Control characters (except tab/newline) are illegal in xlsx cells; OCR'd
# content occasionally smuggles one in (e.g. a mangled degree sign). openpyxl
# raises IllegalCharacterError on write, so every outgoing value is sanitized.
_ILLEGAL_XLSX_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_FORMULA_PREFIXES = ("=", "+", "-", "@")
EXCEL_CELL_CHARACTER_LIMIT = 32_767


class ExcelCellLimitError(ValueError):
    """A workbook value cannot be exported losslessly in one Excel cell."""


def _safe_cell(value):
    if isinstance(value, str):
        return _ILLEGAL_XLSX_RE.sub("", value)
    return value


def _validate_cell_length(cell, value) -> None:
    """Reject text that OpenPyXL would otherwise silently truncate."""
    if not isinstance(value, str) or len(value) <= EXCEL_CELL_CHARACTER_LIMIT:
        return
    excess = len(value) - EXCEL_CELL_CHARACTER_LIMIT
    worksheet = getattr(cell, "parent", None)
    sheet_name = getattr(worksheet, "title", "unknown")
    coordinate = getattr(cell, "coordinate", "unknown")
    field_name = ""
    if worksheet is not None and getattr(cell, "row", 0) > 2:
        header = worksheet.cell(row=2, column=cell.column).value
        if header:
            field_name = f" (field {str(header)!r})"
    raise ExcelCellLimitError(
        "Excel export blocked to prevent data loss: "
        f"cell {sheet_name!r}!{coordinate}{field_name} contains "
        f"{len(value):,} characters, "
        f"exceeding Excel's {EXCEL_CELL_CHARACTER_LIMIT:,}-character cell "
        f"limit by {excess:,}. Shorten this value or split it across multiple "
        "cells or records, then export again."
    )


def _set_cell_value(cell, value) -> None:
    """Write untrusted text without allowing Excel formula interpretation.

    Setting ``data_type`` explicitly preserves the exact displayed and
    round-tripped value. Prefixing an apostrophe is intentionally avoided for
    XLSX because it would change the stored content.
    """
    safe = _safe_cell(value)
    _validate_cell_length(cell, safe)
    cell.value = safe
    if isinstance(safe, str) and safe.startswith(_FORMULA_PREFIXES):
        cell.data_type = "s"


def _write_cell(ws, *, row: int, column: int, value) -> None:
    _set_cell_value(ws.cell(row=row, column=column), value)


def question_placement_key(label: str, group: models.Group) -> tuple:
    """Identity + ancestor path for one assessment placement.

    Matches the CMS dedupe unit: a repeat of this exact tuple is a duplicate
    (skip), the same ``label`` under a different tuple is a tag. Stable group
    identity is part of the key (spec §7.5): without it, two groups of the
    same tier under one concept collapse into one placement.
    """
    concept = group.concept
    topic = concept.topic
    chapter = topic.chapter
    return (label, chapter.chapter_title, topic.topic_title,
            concept.concept_title, group.group_type,
            group.group_key or group.group_name)


def concept_placement_key(concept: models.Concept, topic: models.Topic) -> tuple:
    """Normalized identity + ancestor path for one concept placement.

    Concept generation may improve title capitalization or whitespace on a
    later pass. Those presentation-only changes must refresh the original row,
    not create a second CMS placement. Learning kind is part of the path
    because one chapter may intentionally teach the same normalized concept in
    distinct Pre and Post topic bands.
    """
    chapter = topic.chapter
    return (
        normalize_question_text(strip_title_tag(concept.concept_title)),
        normalize_question_text(strip_title_tag(chapter.chapter_title)),
        normalize_question_text(strip_topic_title(topic.topic_title)),
        normalize_question_text(topic.pre_post_learning),
    )


def _row_question_placement_key(row: tuple, kind: str, concept_len: int) -> tuple | None:
    qs = _IDX_CONCEPT_TITLE + concept_len + len(_group_fields(kind))
    label = _cell_str(row, qs)
    if not label:
        return None
    group_fields = _group_fields(kind)
    g_type = _IDX_CONCEPT_TITLE + concept_len + group_fields.index("group_type")
    g_name = _IDX_CONCEPT_TITLE + concept_len + group_fields.index("group_name")
    # Strip the title-column tags so keys match the clean DB-derived keys.
    return (label,
            strip_title_tag(_cell_str(row, _IDX_CHAPTER_TITLE)),
            strip_topic_title(_cell_str(row, _IDX_TOPIC_TITLE)),
            strip_title_tag(_cell_str(row, _IDX_CONCEPT_TITLE)),
            _cell_str(row, g_type),
            _cell_str(row, g_name))


def _row_concept_placement_key(row: tuple) -> tuple | None:
    title = strip_title_tag(_cell_str(row, _IDX_CONCEPT_TITLE))
    if not title:
        return None
    return (
        normalize_question_text(title),
        normalize_question_text(
            strip_title_tag(_cell_str(row, _IDX_CHAPTER_TITLE))),
        normalize_question_text(
            strip_topic_title(_cell_str(row, _IDX_TOPIC_TITLE))),
        normalize_question_text(_cell_str(row, _IDX_TOPIC_PRE_POST)),
    )


class WorkbookIndex:
    """A scan of what already exists in a workbook, for placement-aware writes.

    - ``q_placements`` / ``c_placements``: exact (identity, placement) tuples present.
    - ``labels`` / ``concept_titles``: entity identities present anywhere (used to
      classify a new placement as a *tag* vs a brand-new *add*).
    - ``q_rows``: placement key -> (sheet, row) for in-place source merges.
    - ``concept_rows``: normalized placement key -> [(sheet, row), ...] —
      exact-placement rows carrying that concept's band, for full refreshes.
    - ``sheet_meta``: per-sheet column geometry (legacy vs current layout).
    """

    __slots__ = ("q_placements", "labels", "c_placements", "concept_titles",
                 "q_rows", "concept_rows", "sheet_meta")

    def __init__(self) -> None:
        self.q_placements: set[tuple] = set()
        self.labels: set[str] = set()
        self.c_placements: set[tuple] = set()
        self.concept_titles: set[str] = set()
        self.q_rows: dict[tuple, tuple] = {}
        self.concept_rows: dict[tuple, list[tuple]] = {}
        self.sheet_meta: dict[str, dict] = {}


def scan_workbook(path: Path) -> WorkbookIndex:
    idx = WorkbookIndex()
    if not path.exists():
        return idx
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    for kind, sheet_name in SHEET_BY_KIND.items():
        if sheet_name not in wb.sheetnames:
            continue
        ws = wb[sheet_name]
        header = next(ws.iter_rows(min_row=2, max_row=2, values_only=True), ())
        concept_fields = _sheet_concept_fields(header)
        concept_len = len(concept_fields)
        q_start = _IDX_CONCEPT_TITLE + concept_len + len(_group_fields(kind))
        idx.sheet_meta[sheet_name] = {
            "concept_len": concept_len,
            "concept_fields": concept_fields,
            "q_start": q_start,
            # question_source is the 4th question-band field on every sheet.
            "q_src_col": q_start + 3,
            # concept_source only exists in the current layout.
            "c_src_col": (
                _IDX_CONCEPT_TITLE + concept_fields.index("concept_source")
                if "concept_source" in concept_fields else None
            ),
            "parent_col": (
                _IDX_CONCEPT_TITLE + concept_fields.index("parent_concept")
                if "parent_concept" in concept_fields else None
            ),
        }
        for row_i, row in enumerate(ws.iter_rows(min_row=3, values_only=True), start=3):
            if not row or not any(row):
                continue
            qk = _row_question_placement_key(row, kind, concept_len)
            if qk:
                idx.q_placements.add(qk)
                idx.labels.add(qk[0])
                idx.q_rows[qk] = (sheet_name, row_i)
            ck = _row_concept_placement_key(row)
            if ck:
                idx.c_placements.add(ck)
                idx.concept_titles.add(ck[0])
                idx.concept_rows.setdefault(ck, []).append((sheet_name, row_i))
    wb.close()
    return idx


def _question_placements(q: models.Question) -> list[models.Group]:
    """Authoring home + every tagged group, de-duplicated, order-stable."""
    groups = [q.group] + [t.group for t in q.tags]
    seen: set[int] = set()
    out: list[models.Group] = []
    for g in groups:
        if g is not None and g.id not in seen:
            seen.add(g.id)
            out.append(g)
    return out


def _concept_placements(c: models.Concept) -> list[models.Topic]:
    """Authoring home topic + every tagged topic, de-duplicated, order-stable."""
    topics = [c.topic] + [t.topic for t in c.tags]
    seen: set[int] = set()
    out: list[models.Topic] = []
    for t in topics:
        if t is not None and t.id not in seen:
            seen.add(t.id)
            out.append(t)
    return out


def _source_order_key(value) -> tuple[int, int]:
    """Canonical position first; creation id only breaks legacy/tied rows."""
    source_order = int(getattr(value, "source_order", 0) or 0)
    return (
        source_order if source_order > 0 else 10**9,
        int(getattr(value, "id", 0) or 0),
    )


class ConceptExportScope:
    """The exact topology selected for one concept workbook/export.

    A scoped Build Concepts download must not derive labels, topic numbers, or
    chapter topic lists from unrelated historical rows still present in the
    database. The accepted concept ids are the closed-world export topology.
    """

    def __init__(self, concepts: list[models.Concept]) -> None:
        by_topic: dict[int, dict[int, models.Concept]] = defaultdict(dict)
        topics: dict[int, models.Topic] = {}
        for concept in concepts:
            for topic in _concept_placements(concept):
                topics[topic.id] = topic
                by_topic[topic.id][concept.id] = concept

        self.concepts_by_topic = {
            topic_id: sorted(values.values(), key=_source_order_key)
            for topic_id, values in by_topic.items()
        }
        grouped_topics: dict[tuple[int, str], list[models.Topic]] = defaultdict(list)
        for topic in topics.values():
            grouped_topics[
                (topic.chapter_id, (topic.pre_post_learning or "").casefold())
            ].append(topic)
        self.topics_by_chapter_kind = {
            key: sorted(values, key=_source_order_key)
            for key, values in grouped_topics.items()
        }
        self.topic_numbers = {
            topic.id: position
            for topic_group in self.topics_by_chapter_kind.values()
            for position, topic in enumerate(topic_group, start=1)
        }

    def concepts_for(self, topic: models.Topic) -> list[models.Concept]:
        return list(self.concepts_by_topic.get(topic.id, ()))

    def topics_for(
        self, chapter: models.Chapter, learning_kind: str,
    ) -> list[models.Topic]:
        return list(self.topics_by_chapter_kind.get(
            (chapter.id, (learning_kind or "").casefold()), ()))


class ConceptWorkbookValidationError(ValueError):
    """Serialized concept workbook disagrees with its accepted DB topology."""


_REGULAR_TYPE_NUMBER_RE = re.compile(
    r"(?<!Miscellaneous )\bType\s+0*(\d+)\s*:",
    re.IGNORECASE,
)
_HUB_PREFIX_RE = re.compile(
    r"\bActivity\s*[—–-]\s*(?P<marker>[^:\n]{3,100})\s*:\s*"
    r"(?P<gist>[^.!?\n]{3,})",
    re.IGNORECASE,
)


def _api_question_placement_active() -> bool:
    """Whether the rewritten Phase 3's routing rules own question placement."""
    try:
        from app.services.phase3 import runner as _phase3_runner
    except ImportError:  # pragma: no cover - defensive import ordering
        return False
    return _phase3_runner.rewrite_enabled()


def _validate_concepts_workbook_bytes(
    data: bytes,
    concepts: list[models.Concept],
    export_scope: ConceptExportScope,
    *,
    exact_rows: bool,
) -> None:
    """Read the serialized XLSX back and enforce final delivery invariants."""
    expected: dict[tuple[str, str, str, str], dict[str, str]] = {}
    primary_titles_by_topic: dict[
        tuple[str, str, str], list[str]
    ] = defaultdict(list)
    for concept in concepts:
        primary_key = (
            normalize_question_text(concept.topic.chapter.chapter_title),
            normalize_question_text(
                strip_topic_title(concept.topic.topic_title)
                or concept.topic.topic_title
            ),
            normalize_question_text(concept.topic.pre_post_learning),
        )
        primary_titles_by_topic[primary_key].append(concept.concept_title)
        for topic in _concept_placements(concept):
            key = (
                normalize_question_text(topic.chapter.chapter_title),
                normalize_question_text(concept.concept_title),
                normalize_question_text(
                    strip_topic_title(topic.topic_title) or topic.topic_title
                ),
                normalize_question_text(topic.pre_post_learning),
            )
            front = _front_bands(
                concept,
                topic,
                include_group_columns=False,
                export_scope=export_scope,
            )
            expected[key] = {
                "topic_title": str(front[_IDX_TOPIC_TITLE] or ""),
                "concept_labels": str(front[
                    len(CHAPTER_FIELDS)
                    + TOPIC_FIELDS.index("topic_concept_labels")
                ] or ""),
                "topic_description": str(front[
                    len(CHAPTER_FIELDS)
                    + TOPIC_FIELDS.index("topic_description")
                ] or ""),
            }

    workbook = openpyxl.load_workbook(
        io.BytesIO(data), data_only=True, read_only=True)
    try:
        ws = workbook[SHEET_BY_KIND["objective"]]
        header = next(
            ws.iter_rows(min_row=2, max_row=2, values_only=True), ())
        concept_fields = _sheet_concept_fields(header)
        details_index = (
            _IDX_CONCEPT_TITLE + concept_fields.index("concept_details"))
        seen: dict[tuple[str, str, str, str], int] = {}
        type_hosts: dict[tuple[str, str], set[str]] = defaultdict(set)
        rows_by_topic: dict[
            tuple[str, str, str], list[tuple[str, str]]
        ] = defaultdict(list)
        issues: list[str] = []

        for row in ws.iter_rows(min_row=3, values_only=True):
            chapter_title = strip_title_tag(
                _cell_str(row, _IDX_CHAPTER_TITLE))
            concept_title = strip_title_tag(
                _cell_str(row, _IDX_CONCEPT_TITLE))
            topic_title = strip_topic_title(
                _cell_str(row, _IDX_TOPIC_TITLE))
            learning_kind = _cell_str(row, _IDX_TOPIC_PRE_POST)
            key = (
                normalize_question_text(chapter_title),
                normalize_question_text(concept_title),
                normalize_question_text(topic_title),
                normalize_question_text(learning_kind),
            )
            if key not in expected:
                continue
            seen[key] = seen.get(key, 0) + 1
            contract = expected[key]
            if _cell_str(row, _IDX_TOPIC_TITLE) != contract["topic_title"]:
                issues.append(
                    f"{concept_title}: noncanonical topic number/title")
            labels_index = (
                len(CHAPTER_FIELDS)
                + TOPIC_FIELDS.index("topic_concept_labels"))
            if _cell_str(row, labels_index) != contract["concept_labels"]:
                issues.append(
                    f"{topic_title}: topic_concept_labels do not match "
                    "the selected topology")
            description_index = (
                len(CHAPTER_FIELDS)
                + TOPIC_FIELDS.index("topic_description"))
            if _cell_str(
                row, description_index
            ) != contract["topic_description"]:
                issues.append(
                    f"{topic_title}: stale topic_description was serialized")

            details = _cell_str(row, details_index)
            # A ConceptTag repeats the same concept under another topic but
            # does not create a second semantic Type host.
            host = normalize_question_text(concept_title)
            for number in _REGULAR_TYPE_NUMBER_RE.findall(details):
                type_hosts[(
                    normalize_question_text(chapter_title),
                    str(int(number)),
                )].add(host)
            for match in _HUB_PREFIX_RE.finditer(details):
                marker = normalize_question_text(match.group("marker"))
                gist = normalize_question_text(match.group("gist"))
                if marker and gist.startswith(marker):
                    issues.append(
                        f"{concept_title}: Activity/Info Hub repeats its "
                        "visible marker")
            rows_by_topic[(
                normalize_question_text(chapter_title),
                normalize_question_text(topic_title),
                normalize_question_text(learning_kind),
            )].append((concept_title, details))

        missing = sorted(set(expected) - set(seen))
        if missing:
            issues.append(
                f"{len(missing)} selected concept placement(s) are missing")
        if exact_rows and any(count != 1 for count in seen.values()):
            issues.append(
                "fresh concept export contains duplicate selected placements")
        split_types = sorted(
            f"{chapter}/Type {number}"
            for (chapter, number), hosts in type_hosts.items()
            if len(hosts) > 1)
        if split_types and not _api_question_placement_active():
            # Under the rewritten Phase 3 the house routing rules place
            # each question on the concept it belongs to, so two questions
            # of one Type can legitimately live on different hosts (their
            # shared Type keeps its one chapter-wide number). The
            # single-host expectation belongs to the legacy allocator.
            issues.append(
                "regular Type number(s) span multiple concept hosts: "
                + ", ".join(split_types))

        for topic_key, rows in rows_by_topic.items():
            culminations = [
                (title, details) for title, details in rows
                if title.casefold().startswith("culmination")]
            if not culminations:
                continue
            if len(culminations) != 1:
                issues.append(
                    f"{topic_key[1]}: expected one culmination row")
                continue
            culmination_title, recap = culminations[0]
            if len(culmination_title) > 120:
                issues.append(
                    f"{topic_key[1]}: culmination title exceeds 120 chars")
            recap_key = normalize_question_text(recap)
            omitted = [
                title
                for title in primary_titles_by_topic.get(topic_key, ())
                if not title.casefold().startswith("culmination")
                if normalize_question_text(title) not in recap_key
            ]
            if omitted:
                issues.append(
                    f"{topic_key[1]}: culmination recap omits "
                    + ", ".join(omitted))

        if issues:
            raise ConceptWorkbookValidationError(
                "concept workbook read-back validation failed: "
                + "; ".join(dict.fromkeys(issues)))
    finally:
        workbook.close()


def _topic_number(
    topic: models.Topic, export_scope: ConceptExportScope | None = None,
) -> int:
    """1-based position of the topic within its chapter (textbook order)."""
    if export_scope is not None:
        scoped = export_scope.topic_numbers.get(topic.id)
        if scoped is not None:
            return scoped
    siblings = sorted(
        (
            sibling for sibling in topic.chapter.topics
            if sibling.pre_post_learning == topic.pre_post_learning
        ),
        key=_source_order_key,
    )
    try:
        return siblings.index(topic) + 1
    except ValueError:
        return 1


def composed_topic_title(
    topic: models.Topic, export_scope: ConceptExportScope | None = None,
) -> str:
    """Tagged topic title cell, e.g. 'Topic 01: <Title> (<tag>)'.

    ``strip_topic_title`` normalizes the stored title first so an already-tagged
    value never gets a second 'Topic NN:'/code prefix.
    """
    chapter = topic.chapter
    clean = strip_topic_title(topic.topic_title) or topic.topic_title
    t_tag = directory.topic_tag(
        chapter.board, chapter.grade, chapter.subject, chapter.chapter_title)
    return f"Topic {_topic_number(topic, export_scope):02d}: {clean} ({t_tag})"


def composed_topic_display(topic: models.Topic) -> str:
    """Clean topic display name, e.g. '<Title>' (no topic number or tag/code)."""
    clean = strip_topic_title(topic.topic_title) or topic.topic_title
    return clean


def _groups_by_type(concept: models.Concept) -> dict[str, str]:
    """All groups of each type, comma-separated (S/T/U columns)."""
    buckets: dict[str, list[str]] = {"Basic": [], "Intermediate": [], "Advanced": []}
    for g in sorted(concept.groups, key=lambda g: g.id):
        if g.group_type in buckets:
            buckets[g.group_type].append(g.group_display_name or g.group_name)
    return {k: ", ".join(v) for k, v in buckets.items()}


def _concept_field_value(
    concept: models.Concept, topic: models.Topic, field: str, *,
    include_group_columns: bool, parent_column_present: bool,
) -> str:
    chapter = topic.chapter
    cp_tag = directory.concept_tag(
        chapter.board, chapter.grade, chapter.subject,
        chapter.chapter_title, topic.topic_title)
    # Parent Concept ships empty by team decision: concepts sit flat under
    # their topic. Blanking it here covers both the canonical column and the
    # legacy "parent: X" marker that older workbooks carried inside
    # related_concepts, so the grouping cannot leak out through either.
    parent = ""
    if field == "concept_title":
        return f"{concept.concept_title} ({cp_tag})"
    if field == "concept_display_name":
        return concept.concept_title
    if field == "parent_concept":
        return parent
    if field == "concept_details":
        return concept.concept_details
    if field == "keywords":  # legacy-workbook column only
        return concept.keywords
    if field == "digicards":
        return concept.digicards
    if field == "related_concepts":  # legacy-workbook column only
        related = concept.related_concepts or ""
        if parent and not parent_column_present:
            marker = f"parent: {parent}"
            existing = [p.strip() for p in related.split(",") if p.strip()]
            if marker.lower() not in {p.lower() for p in existing}:
                existing.insert(0, marker)
            return ", ".join(existing)
        return related
    if field in {"basic_groups", "intermediate_groups", "advanced_groups"}:
        if not include_group_columns:
            return ""
        by_type = _groups_by_type(concept)
        return {
            "basic_groups": by_type["Basic"],
            "intermediate_groups": by_type["Intermediate"],
            "advanced_groups": by_type["Advanced"],
        }[field]
    if field == "concept_source":
        return concept.sources
    return ""


def _chapter_book_source(chapter: models.Chapter, concept: models.Concept) -> str:
    """Best book/publication token for the chapter tag (Fullmarks, NCERT, …)."""
    book = directory.primary_book_source(concept.sources)
    if book:
        return book
    for topic in chapter.topics:
        for sibling in topic.concepts:
            book = directory.primary_book_source(sibling.sources)
            if book:
                return book
    return ""


def _front_bands(concept: models.Concept, topic: models.Topic, *,
                 include_group_columns: bool = True,
                 concept_fields: list[str] | None = None,
                 export_scope: ConceptExportScope | None = None) -> list:
    """Chapter + Topic + Concept bands (22 cells) with tags in the title columns.

    The title columns carry a human-readable tag; the display columns stay
    clean (the reader strips the tags back to the clean model values).

    ``include_group_columns`` is False for concept-catalog rows — group columns
    are filled later when assessments are built, not at concept generation.
    """
    chapter = topic.chapter
    book = _chapter_book_source(chapter, concept)
    c_tag = directory.chapter_tag(
        chapter.board, chapter.grade, chapter.subject, book=book)
    concept_fields = concept_fields or CONCEPT_FIELDS
    parent_column_present = "parent_concept" in concept_fields
    # Column J lists each concept exactly as its concept_title column reads —
    # the tagged "Name (tag)" form — so the importer links them (reviewers:
    # "labels should be concept title, not concept display name").
    cp_tag = directory.concept_tag(
        chapter.board, chapter.grade, chapter.subject,
        chapter.chapter_title, topic.topic_title)
    label_concepts = (
        export_scope.concepts_for(topic)
        if export_scope is not None
        else sorted(topic.concepts, key=_source_order_key)
    )
    concept_labels = ", ".join(
        f"{strip_title_tag(c.concept_title) or c.concept_title} ({cp_tag})"
        for c in label_concepts)
    if export_scope is not None:
        pre_topics = ", ".join(
            composed_topic_title(t, export_scope)
            for t in export_scope.topics_for(chapter, "Pre")
        )
        post_topics = ", ".join(
            composed_topic_title(t, export_scope)
            for t in export_scope.topics_for(chapter, "Post")
        )
    else:
        pre_topics = chapter.pre_topics
        post_topics = chapter.post_topics
    return [
        # ---- Chapter band (tag in title, clean display) ----
        f"{chapter.chapter_title} ({c_tag})", chapter.chapter_title,
        chapter.chapter_duration, pre_topics, post_topics,
        chapter.chapter_description,
        # ---- Topic band ("Topic NN: <title> (<tag>)", display "Topic NN: <title>") ----
        composed_topic_title(topic, export_scope),
        composed_topic_display(topic), topic.pre_post_learning, concept_labels,
        topic.related_topics, topic.topic_description,
    ] + [
        _concept_field_value(
            concept, topic, field,
            include_group_columns=include_group_columns,
            parent_column_present=parent_column_present,
        )
        for field in concept_fields
    ]


def _question_to_row(q: models.Question, kind: str,
                     group: "models.Group | None" = None,
                     concept_fields: list[str] | None = None) -> list:
    """Build one flat canonical row (positional) from a normalized Question.

    ``group`` selects the *placement*: the question's authoring home
    (``q.group``) by default, or a tagged group when emitting a many-to-many
    tag row. The question content is identical across placements; only the
    Chapter/Topic/Concept/Group bands change.
    """
    group = group or q.group
    concept = group.concept
    topic = concept.topic

    concept_fields = concept_fields or CONCEPT_FIELDS
    row: list = list(_front_bands(concept, topic, concept_fields=concept_fields))
    # ---- Group band ----
    if kind == "descriptive":
        row += [
            q.question_label, group.group_display_name, group.group_description,
            group.group_name, group.group_status, group.group_type,
            q.question_label, q.question_label, group.related_digicards,
        ]
    else:
        row += [
            q.question_label, group.group_name, group.group_display_name,
            group.group_description, group.group_status, group.group_type,
            q.question_label, group.related_digicards,
        ]
    # ---- Question band ----
    if kind == "objective":
        row += [
            q.question_label, q.question_category, q.cognitive_skills,
            q.question_source, q.question_disclaimer, q.question_duration,
            q.question_appears_in, q.level_of_difficulty, q.question, q.marks,
        ]
        for n in range(6):
            a = q.answers[n] if n < len(q.answers) else {}
            row += [
                a.get("answer_type", ""), a.get("answer_content", ""),
                a.get("correct_answer", ""), a.get("answer_weightage", ""),
            ]
        row.append(q.answer_explanation)
        row.append(q.question_text)
    elif kind == "subjective":
        row += [
            q.question_label, q.question_category, q.cognitive_skills,
            q.question_source, q.question_disclaimer, q.question_duration,
            q.math_keyboard, q.question_appears_in, q.level_of_difficulty,
            q.question, q.marks,
        ]
        for n in range(10):
            a = q.answers[n] if n < len(q.answers) else {}
            row += [
                a.get("answer_type", ""), a.get("answer", ""),
                a.get("answer_display", ""), a.get("weightage", ""),
                a.get("placeholder", ""),
            ]
        row.append(q.answer_explanation)
        row.append(q.question_text)
    else:  # descriptive
        row += [
            q.question_label, q.question_category, q.cognitive_skills,
            q.question_source, q.question_disclaimer, q.question_duration,
            q.math_keyboard, q.question_appears_in, q.level_of_difficulty,
            q.question, q.marks, q.display_answer,
        ]
        for n in range(10):
            a = q.answers[n] if n < len(q.answers) else {}
            row += [
                a.get("answer_type", ""), a.get("answer_weightage", ""),
                a.get("answer_content", ""),
            ]
        row.append(q.answer_explanation)
        for n in range(15):
            sq = q.sub_questions[n] if n < len(q.sub_questions) else {}
            row += [sq.get("text", ""), sq.get("marks", "")]
            kws = sq.get("keywords", [])
            for m in range(6):
                kw = kws[m] if m < len(kws) else {}
                row += [kw.get("answer_type", ""), kw.get("weightage", ""), kw.get("keyword", "")]
        row.append(q.question_text)

    expected = len(FIELDS_BY_KIND[kind]) + (len(concept_fields) - len(CONCEPT_FIELDS))
    if len(row) < expected:
        row += [""] * (expected - len(row))
    return row[:expected]


def _concept_to_row(concept: models.Concept, kind: str = "objective",
                    topic: "models.Topic | None" = None,
                    concept_fields: list[str] | None = None,
                    export_scope: ConceptExportScope | None = None) -> list:
    """Build a concept-catalog row (chapter/topic/concept/group filled, no question).

    ``topic`` selects the placement: the concept's authoring home
    (``concept.topic``) by default, or a tagged topic (possibly in another
    chapter) when emitting a many-to-many concept tag row.
    """
    topic = topic or concept.topic
    concept_fields = concept_fields or CONCEPT_FIELDS
    row: list = list(_front_bands(
        concept,
        topic,
        include_group_columns=False,
        concept_fields=concept_fields,
        export_scope=export_scope,
    ))
    expected = len(FIELDS_BY_KIND[kind]) + (len(concept_fields) - len(CONCEPT_FIELDS))
    row += [""] * (expected - len(row))
    return row[:expected]


def _row_has_question(ws, row_i: int, q_start: int) -> bool:
    """Whether an existing workbook row carries any Question-band content."""
    return any(
        str(ws.cell(row=row_i, column=column).value or "").strip()
        for column in range(q_start + 1, ws.max_column + 1)
    )


def _refresh_concept_rows(
    wb,
    index: WorkbookIndex,
    concept: models.Concept,
    topic: models.Topic,
    locations: list[tuple],
    *,
    include_ancestors: bool,
    export_scope: ConceptExportScope | None = None,
) -> int:
    """Refresh selected rows from the DB concept, retaining row identity.

    Normal refreshes write only the Concept band. Placement reconciliation also
    writes the Chapter and Topic bands so a stale row moves to its current
    authoritative placement. Group and Question bands are never overwritten.
    Concept source history is merged rather than replaced.
    """
    sources_updated = 0
    for sheet_name, row_i in locations:
        meta = index.sheet_meta.get(sheet_name) or {}
        concept_fields = meta.get("concept_fields")
        q_start = meta.get("q_start")
        if not concept_fields or q_start is None:
            continue
        ws = wb[sheet_name]
        # Catalog-only rows deliberately leave basic/intermediate/advanced
        # summaries blank. Question rows carry the current group summaries.
        include_group_columns = _row_has_question(ws, row_i, q_start)
        front_values = _front_bands(
            concept,
            topic,
            include_group_columns=include_group_columns,
            concept_fields=concept_fields,
            export_scope=export_scope,
        )
        start = 0 if include_ancestors else _IDX_CONCEPT_TITLE
        for column_index, value in enumerate(front_values[start:], start=start):
            field_index = column_index - _IDX_CONCEPT_TITLE
            field = (
                concept_fields[field_index]
                if 0 <= field_index < len(concept_fields)
                else ""
            )
            cell = ws.cell(row=row_i, column=column_index + 1)
            if field == "concept_source":
                current = str(cell.value or "")
                value = merge_sources(current, str(value or ""))
                if value != current:
                    sources_updated += 1
            _set_cell_value(cell, value)
    return sources_updated


def _refresh_concept_band(
    wb,
    index: WorkbookIndex,
    concept: models.Concept,
    topic: models.Topic,
    export_scope: ConceptExportScope | None = None,
) -> int:
    """Refresh every row at one exact placement, including scoped metadata."""
    return _refresh_concept_rows(
        wb,
        index,
        concept,
        topic,
        list(index.concept_rows.get(concept_placement_key(concept, topic), [])),
        # Topic numbering, topic_concept_labels, descriptions, and chapter
        # pre/post lists belong to the accepted export topology too. Updating
        # only the Concept band is how stale 113-label metadata survived a
        # 42-row successful run.
        include_ancestors=True,
        export_scope=export_scope,
    )


def _move_indexed_concept_row(
    index: WorkbookIndex,
    old_key: tuple,
    new_key: tuple,
    location: tuple,
) -> None:
    """Reflect one in-place placement move in the current workbook index."""
    old_locations = index.concept_rows.get(old_key, [])
    if location in old_locations:
        old_locations.remove(location)
    if not old_locations:
        index.concept_rows.pop(old_key, None)
        index.c_placements.discard(old_key)
    new_locations = index.concept_rows.setdefault(new_key, [])
    if location not in new_locations:
        new_locations.append(location)
    index.c_placements.add(new_key)
    index.concept_titles.add(new_key[0])


def _reconcile_concept_placements(
    wb,
    index: WorkbookIndex,
    concept: models.Concept,
    desired_topics: list[models.Topic],
    export_scope: ConceptExportScope | None = None,
) -> int:
    """Move stale same-chapter rows to current DB placements conservatively.

    Question rows always belong to the concept's authoritative home topic.
    Catalog-only rows are reused only for desired placements that lack a
    catalog row. Rows in another chapter are never candidates, even when their
    normalized concept title is identical.
    """
    if not desired_topics or concept.topic is None:
        return 0
    home_key = concept_placement_key(concept, concept.topic)
    identity, home_chapter, _, home_learning_kind = home_key
    desired_by_key = {
        concept_placement_key(concept, topic): topic
        for topic in desired_topics
    }
    desired_same_chapter = {
        key: topic
        for key, topic in desired_by_key.items()
        if key[1] == home_chapter and key[3] == home_learning_kind
    }
    same_chapter_keys = [
        key
        for key in list(index.concept_rows)
        if (
            key[0] == identity
            and key[1] == home_chapter
            and key[3] == home_learning_kind
        )
    ]
    if not same_chapter_keys:
        return 0

    sources_updated = 0

    # A ConceptTag does not relocate the concept's assessments. Therefore any
    # question-bearing row under another topic is a stale former-home row,
    # including when that topic remains a legitimate catalog tag.
    for old_key in same_chapter_keys:
        if old_key == home_key:
            continue
        for location in list(index.concept_rows.get(old_key, [])):
            sheet_name, row_i = location
            meta = index.sheet_meta.get(sheet_name) or {}
            q_start = meta.get("q_start")
            if q_start is None or not _row_has_question(
                    wb[sheet_name], row_i, q_start):
                continue
            sources_updated += _refresh_concept_rows(
                wb,
                index,
                concept,
                concept.topic,
                [location],
                include_ancestors=True,
                export_scope=export_scope,
            )
            _move_indexed_concept_row(
                index, old_key, home_key, location)

    def _catalog_locations(key: tuple) -> list[tuple]:
        out: list[tuple] = []
        for location in index.concept_rows.get(key, []):
            sheet_name, row_i = location
            meta = index.sheet_meta.get(sheet_name) or {}
            q_start = meta.get("q_start")
            if q_start is not None and not _row_has_question(
                    wb[sheet_name], row_i, q_start):
                out.append(location)
        return out

    # Home comes first in ``desired_topics``. Reuse stale catalog rows for
    # missing desired catalog placements in that same stable order.
    missing_catalog = [
        (key, desired_by_key[key])
        for key in desired_by_key
        if key in desired_same_chapter and not _catalog_locations(key)
    ]
    stale_catalog: list[tuple[tuple, tuple]] = []
    for old_key in same_chapter_keys:
        if old_key in desired_same_chapter:
            continue
        stale_catalog.extend(
            (old_key, location)
            for location in _catalog_locations(old_key)
        )

    for (old_key, location), (new_key, topic) in zip(
            stale_catalog, missing_catalog):
        sources_updated += _refresh_concept_rows(
            wb,
            index,
            concept,
            topic,
            [location],
            include_ancestors=True,
            export_scope=export_scope,
        )
        _move_indexed_concept_row(index, old_key, new_key, location)

    return sources_updated


@workbook_sync.synchronized_output_workbook
def append_concepts(db: Session, path: Path, concept_ids: list[int]) -> dict[str, int]:
    """Append concept-catalog rows (no questions) to the Objective sheet.

    One row per (concept, placement): the concept's home topic plus every
    tagged topic/chapter. Placements already present are never re-added —
    instead their complete Concept bands are refreshed in place from the
    current DB concept. ``concept_source`` is merged so a concept re-used from
    another book keeps all previously recorded sources.
    """
    index = scan_workbook(path)
    wb = openpyxl.load_workbook(path) if path.exists() else _new_workbook()
    ws = wb[SHEET_BY_KIND["objective"]]
    header = next(ws.iter_rows(min_row=2, max_row=2, values_only=True), ())
    concept_fields = _sheet_concept_fields(header)
    concepts = (
        db.query(models.Concept).filter(models.Concept.id.in_(concept_ids))
        .all()
    )
    concepts = sorted(concepts, key=lambda concept: (
        _source_order_key(concept.topic),
        _source_order_key(concept),
    ))
    export_scope = ConceptExportScope(concepts)
    result = {
        "written": 0,
        "sources_updated": 0,
        "parent_column": "parent_concept" in concept_fields,
        "parent_fallback": "parent_concept" not in concept_fields,
    }
    for c in concepts:
        desired_topics = _concept_placements(c)
        result["sources_updated"] += _reconcile_concept_placements(
            wb, index, c, desired_topics, export_scope)
        for topic in desired_topics:
            key = concept_placement_key(c, topic)
            if key in index.c_placements:
                result["sources_updated"] += _refresh_concept_band(
                    wb, index, c, topic, export_scope)
                continue
            index.c_placements.add(key)
            target = ws.max_row + 1 if ws.max_row >= 2 else 3
            for i, value in enumerate(
                _concept_to_row(
                    c,
                    "objective",
                    topic,
                    concept_fields=concept_fields,
                    export_scope=export_scope,
                ),
                start=1,
            ):
                _write_cell(ws, row=target, column=i, value=value)
            index.concept_rows.setdefault(key, []).append(
                (SHEET_BY_KIND["objective"], target))
            index.concept_titles.add(key[0])
            result["written"] += 1
    serialized = io.BytesIO()
    wb.save(serialized)
    _validate_concepts_workbook_bytes(
        serialized.getvalue(),
        concepts,
        export_scope,
        exact_rows=False,
    )
    workbook_sync.atomic_save_workbook(wb, path)
    return result


def _write_headers(ws, kind: str) -> None:
    fields = FIELDS_BY_KIND[kind]
    # Row 1: section bands (merged).
    col = 1
    for label, span in SECTION_BANDS[kind]:
        cell = ws.cell(row=1, column=col)
        _set_cell_value(cell, label)
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal="center")
        cell.fill = PatternFill("solid", fgColor=_BAND_FILL.get(label, "EEEEEE"))
        if span > 1:
            ws.merge_cells(
                start_row=1, start_column=col, end_row=1, end_column=col + span - 1)
        col += span
    # Row 2: field names.
    for i, name in enumerate(fields, start=1):
        c = ws.cell(row=2, column=i)
        _set_cell_value(c, name)
        c.font = Font(bold=True, size=9)
    ws.freeze_panes = "A3"
    ws.column_dimensions[get_column_letter(1)].width = 22


def _new_workbook() -> openpyxl.Workbook:
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for kind, sheet_name in SHEET_BY_KIND.items():
        ws = wb.create_sheet(sheet_name)
        _write_headers(ws, kind)
    doc = wb.create_sheet(SHEET_DOC_LINK)
    _write_cell(doc, row=1, column=1, value="Screenshot Doc")
    _write_cell(
        doc,
        row=1,
        column=2,
        value="Generated by Aegis integrated tool",
    )
    return wb


def _questions(db: Session, question_ids: list[int] | None) -> list[models.Question]:
    q = db.query(models.Question)
    if question_ids is not None:
        q = q.filter(models.Question.id.in_(question_ids))
    return q.order_by(models.Question.id).all()


def write_workbook(db: Session, dest: Path | None = None,
                   question_ids: list[int] | None = None) -> bytes:
    """Write a fresh canonical workbook with the selected questions.

    Emits one row per (question, placement): the authoring home plus every tag,
    so many-to-many associations survive a full export. Concepts that have no
    question rows still get concept-catalog rows on the Objective sheet —
    otherwise a database holding only generated concepts (no assessments yet)
    would export as an empty workbook.
    """
    wb = _new_workbook()
    next_row = {k: 3 for k in SHEET_BY_KIND}
    concepts_with_rows: set[int] = set()
    for q in _questions(db, question_ids):
        ws = wb[SHEET_BY_KIND[q.sheet_kind]]
        for group in _question_placements(q):
            for i, value in enumerate(_question_to_row(q, q.sheet_kind, group), start=1):
                _write_cell(
                    ws,
                    row=next_row[q.sheet_kind],
                    column=i,
                    value=value,
                )
            next_row[q.sheet_kind] += 1
            concepts_with_rows.add(group.concept_id)
    if question_ids is None:
        ws_obj = wb[SHEET_BY_KIND["objective"]]
        for concept in db.query(models.Concept).order_by(models.Concept.id):
            if concept.id in concepts_with_rows:
                continue
            for topic in _concept_placements(concept):
                for i, value in enumerate(
                    _concept_to_row(concept, "objective", topic), start=1
                ):
                    _write_cell(
                        ws_obj,
                        row=next_row["objective"],
                        column=i,
                        value=value,
                    )
                next_row["objective"] += 1
    buf = io.BytesIO()
    wb.save(buf)
    data = buf.getvalue()
    if dest:
        dest.write_bytes(data)
    return data


def write_concepts_workbook(db: Session, concept_ids: list[int]) -> bytes:
    """Write a fresh canonical workbook holding only the given concepts.

    Concepts have no questions of their own here, so they are emitted as
    concept-catalog rows on the Objective sheet — one row per (concept,
    placement) (the authoring home topic plus every tagged topic/chapter) —
    exactly the shape ``append_concepts`` writes to the app-data output
    workbook. Used by the per-functionality "download Bulk Import Excel"
    export for the Build Concepts flows.
    """
    wb = _new_workbook()
    ws = wb[SHEET_BY_KIND["objective"]]
    concepts = (
        db.query(models.Concept).filter(models.Concept.id.in_(concept_ids))
        .all()
    )
    concepts = sorted(concepts, key=lambda concept: (
        _source_order_key(concept.topic),
        _source_order_key(concept),
    ))
    export_scope = ConceptExportScope(concepts)
    next_row = 3
    for c in concepts:
        for topic in sorted(_concept_placements(c), key=_source_order_key):
            for i, value in enumerate(
                _concept_to_row(
                    c,
                    "objective",
                    topic,
                    export_scope=export_scope,
                ),
                start=1,
            ):
                _write_cell(ws, row=next_row, column=i, value=value)
            next_row += 1
    buf = io.BytesIO()
    wb.save(buf)
    data = buf.getvalue()
    _validate_concepts_workbook_bytes(
        data,
        concepts,
        export_scope,
        exact_rows=True,
    )
    return data


def write_subject_workbook(
    db: Session, *, subject: str, board: str = "", grade: str = "",
    include_content: bool = True,
) -> bytes:
    """Create a canonical workbook scoped to one subject.

    ``include_content=False`` yields a blank authoring template (headers only,
    exact canonical layout). With content, every question placement and every
    concept placement falling inside the scoped chapters is emitted; concepts
    without questions still get concept-catalog rows on the Objective sheet so
    the full hierarchy is represented.
    """
    wb = _new_workbook()
    if include_content:
        q = db.query(models.Chapter).filter(models.Chapter.subject == subject)
        if board:
            q = q.filter(models.Chapter.board == board)
        if grade:
            q = q.filter(models.Chapter.grade == grade)
        chapter_ids = {c.id for c in q.all()}

        next_row = {k: 3 for k in SHEET_BY_KIND}
        concepts_with_rows: set[int] = set()
        for question in db.query(models.Question).order_by(models.Question.id):
            for group in _question_placements(question):
                if group.concept.topic.chapter_id not in chapter_ids:
                    continue
                ws = wb[SHEET_BY_KIND[question.sheet_kind]]
                for i, value in enumerate(
                    _question_to_row(question, question.sheet_kind, group), start=1
                ):
                    _write_cell(
                        ws,
                        row=next_row[question.sheet_kind],
                        column=i,
                        value=value,
                    )
                next_row[question.sheet_kind] += 1
                concepts_with_rows.add(group.concept_id)

        # Concept-catalog rows for in-scope concepts that have no question rows.
        # In-scope = home topic in a scoped chapter OR tagged into one.
        ws_obj = wb[SHEET_BY_KIND["objective"]]
        home = (
            db.query(models.Concept).join(models.Topic)
            .filter(models.Topic.chapter_id.in_(chapter_ids)).all()
        )
        tagged = (
            db.query(models.Concept).join(models.ConceptTag).join(
                models.Topic, models.ConceptTag.topic_id == models.Topic.id)
            .filter(models.Topic.chapter_id.in_(chapter_ids)).all()
        )
        in_scope: dict[int, models.Concept] = {c.id: c for c in home + tagged}
        for concept in sorted(in_scope.values(), key=lambda c: c.id):
            if concept.id in concepts_with_rows:
                continue
            for topic in _concept_placements(concept):
                if topic.chapter_id not in chapter_ids:
                    continue
                for i, value in enumerate(_concept_to_row(concept, "objective", topic), start=1):
                    _write_cell(
                        ws_obj,
                        row=next_row["objective"],
                        column=i,
                        value=value,
                    )
                next_row["objective"] += 1

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@workbook_sync.synchronized_output_workbook
def append_questions(db: Session, path: Path, question_ids: list[int]) -> dict[str, int]:
    """Append-only write, placement-aware.

    Adds one row per (question, placement) — the authoring home plus every tag —
    skipping any (label, ancestor-path) already present. A repeated label under
    a *new* placement is therefore written as a tag rather than skipped.
    """
    index = scan_workbook(path)
    if path.exists():
        wb = openpyxl.load_workbook(path)
    else:
        wb = _new_workbook()

    appended = {"objective": 0, "subjective": 0, "descriptive": 0,
                "tagged": 0, "skipped": 0, "sources_updated": 0}
    for q in _questions(db, question_ids):
        for n, group in enumerate(_question_placements(q)):
            key = question_placement_key(q.question_label, group)
            if q.question_label and key in index.q_placements:
                appended["skipped"] += 1
                # Existing row: refresh its question_source in place so a
                # duplicate question arriving from another book accumulates
                # sources instead of duplicating the row.
                loc = index.q_rows.get(key)
                if loc and q.question_source:
                    sheet_name, row_i = loc
                    col = index.sheet_meta[sheet_name]["q_src_col"]
                    cell = wb[sheet_name].cell(row=row_i, column=col + 1)
                    merged = merge_sources(str(cell.value or ""), q.question_source)
                    if merged != str(cell.value or ""):
                        _set_cell_value(cell, merged)
                        appended["sources_updated"] += 1
                continue
            is_tag = q.question_label in index.labels
            index.q_placements.add(key)
            index.labels.add(q.question_label)
            ws = wb[SHEET_BY_KIND[q.sheet_kind]]
            meta = index.sheet_meta.get(SHEET_BY_KIND[q.sheet_kind]) or {}
            concept_fields = meta.get("concept_fields")
            if concept_fields is None:
                header = next(ws.iter_rows(min_row=2, max_row=2, values_only=True), ())
                concept_fields = _sheet_concept_fields(header)
            target = ws.max_row + 1 if ws.max_row >= 2 else 3
            for i, value in enumerate(
                _question_to_row(q, q.sheet_kind, group, concept_fields=concept_fields),
                start=1,
            ):
                _write_cell(ws, row=target, column=i, value=value)
            appended[q.sheet_kind] += 1
            if is_tag:
                appended["tagged"] += 1

    workbook_sync.atomic_save_workbook(wb, path)
    return appended

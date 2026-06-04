"""Write normalized questions back to the canonical Bulk Import workbook.

Two header rows are emitted per content sheet (section bands + field names).
Writes are **append-only**: ``append_questions`` reads existing
``question_label`` values across all tabs and skips anything already present,
so re-running a generation never overwrites or deletes prior rows.
"""
from __future__ import annotations

import io
from pathlib import Path

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from sqlalchemy.orm import Session

from . import (
    CHAPTER_FIELDS, TOPIC_FIELDS, CONCEPT_FIELDS, FIELDS_BY_KIND, SHEET_BY_KIND,
    SHEET_DOC_LINK, SECTION_BANDS, OBJECTIVE_GROUP_FIELDS, DESCRIPTIVE_GROUP_FIELDS,
)
from .. import models

_BAND_FILL = {
    "Chapter": "FCE4D6", "Topic": "FFF2CC", "Concept": "D9EAD3",
    "Group": "D0E0E3", "Question": "CFE2F3",
}


def _group_fields(kind: str) -> list[str]:
    return DESCRIPTIVE_GROUP_FIELDS if kind == "descriptive" else OBJECTIVE_GROUP_FIELDS


# Positional indices shared by every content sheet (front bands are identical).
_IDX_CHAPTER_TITLE = 0
_IDX_TOPIC_TITLE = len(CHAPTER_FIELDS)
_IDX_CONCEPT_TITLE = len(CHAPTER_FIELDS) + len(TOPIC_FIELDS)
# group_type is the 6th field (index 5) in both the objective & descriptive group bands.
_IDX_GROUP_TYPE = _IDX_CONCEPT_TITLE + len(CONCEPT_FIELDS) + 5


def _q_start(kind: str) -> int:
    """Column index where the Question band's first ``question_label`` lives."""
    return _IDX_CONCEPT_TITLE + len(CONCEPT_FIELDS) + len(_group_fields(kind))


def _cell_str(row: tuple, idx: int) -> str:
    if idx >= len(row):
        return ""
    v = row[idx]
    return "" if v is None else str(v).strip()


def question_placement_key(label: str, group: models.Group) -> tuple:
    """Identity + ancestor path for one assessment placement.

    Matches the CMS dedupe unit: a repeat of this exact tuple is a duplicate
    (skip), the same ``label`` under a different tuple is a tag.
    """
    concept = group.concept
    topic = concept.topic
    chapter = topic.chapter
    return (label, chapter.chapter_title, topic.topic_title,
            concept.concept_title, group.group_type)


def concept_placement_key(concept: models.Concept, topic: models.Topic) -> tuple:
    """Identity + ancestor path for one concept placement (keyed by concept_title)."""
    chapter = topic.chapter
    return (concept.concept_title, chapter.chapter_title, topic.topic_title)


def _row_question_placement_key(row: tuple, kind: str) -> tuple | None:
    qs = _q_start(kind)
    label = _cell_str(row, qs)
    if not label:
        return None
    return (label, _cell_str(row, _IDX_CHAPTER_TITLE), _cell_str(row, _IDX_TOPIC_TITLE),
            _cell_str(row, _IDX_CONCEPT_TITLE), _cell_str(row, _IDX_GROUP_TYPE))


def _row_concept_placement_key(row: tuple) -> tuple | None:
    title = _cell_str(row, _IDX_CONCEPT_TITLE)
    if not title:
        return None
    return (title, _cell_str(row, _IDX_CHAPTER_TITLE), _cell_str(row, _IDX_TOPIC_TITLE))


class WorkbookIndex:
    """A scan of what already exists in a workbook, for placement-aware writes.

    - ``q_placements`` / ``c_placements``: exact (identity, placement) tuples present.
    - ``labels`` / ``concept_titles``: entity identities present anywhere (used to
      classify a new placement as a *tag* vs a brand-new *add*).
    """

    __slots__ = ("q_placements", "labels", "c_placements", "concept_titles")

    def __init__(self) -> None:
        self.q_placements: set[tuple] = set()
        self.labels: set[str] = set()
        self.c_placements: set[tuple] = set()
        self.concept_titles: set[str] = set()


def scan_workbook(path: Path) -> WorkbookIndex:
    idx = WorkbookIndex()
    if not path.exists():
        return idx
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    for kind, sheet_name in SHEET_BY_KIND.items():
        if sheet_name not in wb.sheetnames:
            continue
        for row in wb[sheet_name].iter_rows(min_row=3, values_only=True):
            if not row or not any(row):
                continue
            qk = _row_question_placement_key(row, kind)
            if qk:
                idx.q_placements.add(qk)
                idx.labels.add(qk[0])
            ck = _row_concept_placement_key(row)
            if ck:
                idx.c_placements.add(ck)
                idx.concept_titles.add(ck[0])
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


def _question_to_row(q: models.Question, kind: str,
                     group: "models.Group | None" = None) -> list:
    """Build one flat canonical row (positional) from a normalized Question.

    ``group`` selects the *placement*: the question's authoring home
    (``q.group``) by default, or a tagged group when emitting a many-to-many
    tag row. The question content is identical across placements; only the
    Chapter/Topic/Concept/Group bands change.
    """
    group = group or q.group
    concept = group.concept
    topic = concept.topic
    chapter = topic.chapter

    row: list = []
    # ---- Chapter band ----
    row += [
        chapter.chapter_title, chapter.chapter_display_name, chapter.chapter_duration,
        chapter.pre_topics, chapter.post_topics, chapter.chapter_description,
    ]
    # ---- Topic band ----
    row += [
        topic.topic_title, topic.topic_display_name, topic.pre_post_learning,
        concept.concept_title, topic.related_topics, topic.topic_description,
    ]
    # ---- Concept band (9) ----
    by_type = {"Basic": "", "Intermediate": "", "Advanced": ""}
    for g in concept.groups:
        by_type[g.group_type] = g.group_display_name or g.group_name
    row += [
        concept.concept_title, concept.concept_display_name, concept.concept_details,
        concept.keywords, concept.digicards, concept.related_concepts,
        by_type["Basic"], by_type["Intermediate"], by_type["Advanced"],
    ]
    # ---- Group band ----
    if kind == "descriptive":
        row += [
            q.question_label, group.group_display_name, group.group_description,
            group.group_name, group.group_status, group.group_type,
            q.question_label, group.related_digicards,
        ]
    else:
        row += [
            q.question_label, group.group_name, group.group_display_name,
            group.group_description, group.group_status, group.group_type,
            group.related_digicards,
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

    expected = len(FIELDS_BY_KIND[kind])
    if len(row) < expected:
        row += [""] * (expected - len(row))
    return row[:expected]


def _concept_to_row(concept: models.Concept, kind: str = "objective",
                    topic: "models.Topic | None" = None) -> list:
    """Build a concept-catalog row (chapter/topic/concept/group filled, no question).

    ``topic`` selects the placement: the concept's authoring home
    (``concept.topic``) by default, or a tagged topic (possibly in another
    chapter) when emitting a many-to-many concept tag row.
    """
    topic = topic or concept.topic
    chapter = topic.chapter
    row: list = []
    row += [
        chapter.chapter_title, chapter.chapter_display_name, chapter.chapter_duration,
        chapter.pre_topics, chapter.post_topics, chapter.chapter_description,
    ]
    row += [
        topic.topic_title, topic.topic_display_name, topic.pre_post_learning,
        concept.concept_title, topic.related_topics, topic.topic_description,
    ]
    by_type = {"Basic": "", "Intermediate": "", "Advanced": ""}
    for g in concept.groups:
        by_type[g.group_type] = g.group_display_name or g.group_name
    row += [
        concept.concept_title, concept.concept_display_name, concept.concept_details,
        concept.keywords, concept.digicards, concept.related_concepts,
        by_type["Basic"], by_type["Intermediate"], by_type["Advanced"],
    ]
    expected = len(FIELDS_BY_KIND[kind])
    row += [""] * (expected - len(row))
    return row[:expected]


def append_concepts(db: Session, path: Path, concept_ids: list[int]) -> int:
    """Append concept-catalog rows (no questions) to the Objective sheet.

    One row per (concept, placement): the concept's home topic plus every
    tagged topic/chapter. Placements already present are skipped, so re-running
    never duplicates a concept under the same parent (matching the CMS).
    """
    index = scan_workbook(path)
    wb = openpyxl.load_workbook(path) if path.exists() else _new_workbook()
    ws = wb[SHEET_BY_KIND["objective"]]
    concepts = (
        db.query(models.Concept).filter(models.Concept.id.in_(concept_ids))
        .order_by(models.Concept.id).all()
    )
    written = 0
    for c in concepts:
        for topic in _concept_placements(c):
            key = concept_placement_key(c, topic)
            if key in index.c_placements:
                continue
            index.c_placements.add(key)
            target = ws.max_row + 1 if ws.max_row >= 2 else 3
            for i, value in enumerate(_concept_to_row(c, "objective", topic), start=1):
                ws.cell(row=target, column=i, value=value)
            written += 1
    wb.save(path)
    return written


def _write_headers(ws, kind: str) -> None:
    fields = FIELDS_BY_KIND[kind]
    # Row 1: section bands (merged).
    col = 1
    for label, span in SECTION_BANDS[kind]:
        cell = ws.cell(row=1, column=col, value=label)
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal="center")
        cell.fill = PatternFill("solid", fgColor=_BAND_FILL.get(label, "EEEEEE"))
        if span > 1:
            ws.merge_cells(
                start_row=1, start_column=col, end_row=1, end_column=col + span - 1)
        col += span
    # Row 2: field names.
    for i, name in enumerate(fields, start=1):
        c = ws.cell(row=2, column=i, value=name)
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
    doc["A1"] = "Screenshot Doc"
    doc["B1"] = "Generated by Aegis integrated tool"
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
    so many-to-many associations survive a full export.
    """
    wb = _new_workbook()
    next_row = {k: 3 for k in SHEET_BY_KIND}
    for q in _questions(db, question_ids):
        ws = wb[SHEET_BY_KIND[q.sheet_kind]]
        for group in _question_placements(q):
            for i, value in enumerate(_question_to_row(q, q.sheet_kind, group), start=1):
                ws.cell(row=next_row[q.sheet_kind], column=i, value=value)
            next_row[q.sheet_kind] += 1
    buf = io.BytesIO()
    wb.save(buf)
    data = buf.getvalue()
    if dest:
        dest.write_bytes(data)
    return data


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
                "tagged": 0, "skipped": 0}
    for q in _questions(db, question_ids):
        for n, group in enumerate(_question_placements(q)):
            key = question_placement_key(q.question_label, group)
            if q.question_label and key in index.q_placements:
                appended["skipped"] += 1
                continue
            is_tag = q.question_label in index.labels
            index.q_placements.add(key)
            index.labels.add(q.question_label)
            ws = wb[SHEET_BY_KIND[q.sheet_kind]]
            target = ws.max_row + 1 if ws.max_row >= 2 else 3
            for i, value in enumerate(_question_to_row(q, q.sheet_kind, group), start=1):
                ws.cell(row=target, column=i, value=value)
            appended[q.sheet_kind] += 1
            if is_tag:
                appended["tagged"] += 1

    wb.save(path)
    return appended

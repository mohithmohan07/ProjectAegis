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
    LEGACY_CONCEPT_LEN, merge_sources, strip_title_tag, strip_topic_title,
)
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
_IDX_CONCEPT_TITLE = len(CHAPTER_FIELDS) + len(TOPIC_FIELDS)
# group_type is the 6th field (index 5) in both the objective & descriptive group bands.
_IDX_GROUP_TYPE = _IDX_CONCEPT_TITLE + len(CONCEPT_FIELDS) + 5


def _q_start(kind: str) -> int:
    """Column index where the Question band's first ``question_label`` lives."""
    return _IDX_CONCEPT_TITLE + len(CONCEPT_FIELDS) + len(_group_fields(kind))


def _sheet_concept_len(header_row: tuple) -> int:
    """Concept-band length for a sheet: current (with concept_source) or legacy."""
    idx = _IDX_CONCEPT_TITLE + LEGACY_CONCEPT_LEN
    val = header_row[idx] if idx < len(header_row) else None
    return LEGACY_CONCEPT_LEN + 1 if str(val or "").strip() == "concept_source" else LEGACY_CONCEPT_LEN


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


def _row_question_placement_key(row: tuple, kind: str, concept_len: int) -> tuple | None:
    qs = _IDX_CONCEPT_TITLE + concept_len + len(_group_fields(kind))
    label = _cell_str(row, qs)
    if not label:
        return None
    g_type = _IDX_CONCEPT_TITLE + concept_len + 5
    # Strip the title-column tags so keys match the clean DB-derived keys.
    return (label,
            strip_title_tag(_cell_str(row, _IDX_CHAPTER_TITLE)),
            strip_topic_title(_cell_str(row, _IDX_TOPIC_TITLE)),
            strip_title_tag(_cell_str(row, _IDX_CONCEPT_TITLE)),
            _cell_str(row, g_type))


def _row_concept_placement_key(row: tuple) -> tuple | None:
    title = strip_title_tag(_cell_str(row, _IDX_CONCEPT_TITLE))
    if not title:
        return None
    return (title,
            strip_title_tag(_cell_str(row, _IDX_CHAPTER_TITLE)),
            strip_topic_title(_cell_str(row, _IDX_TOPIC_TITLE)))


class WorkbookIndex:
    """A scan of what already exists in a workbook, for placement-aware writes.

    - ``q_placements`` / ``c_placements``: exact (identity, placement) tuples present.
    - ``labels`` / ``concept_titles``: entity identities present anywhere (used to
      classify a new placement as a *tag* vs a brand-new *add*).
    - ``q_rows``: placement key -> (sheet, row) for in-place source merges.
    - ``concept_rows``: (concept_title, chapter_title) -> [(sheet, row), ...] —
      every row carrying that concept's band, for source refreshes.
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
        concept_len = _sheet_concept_len(header)
        q_start = _IDX_CONCEPT_TITLE + concept_len + len(_group_fields(kind))
        idx.sheet_meta[sheet_name] = {
            "concept_len": concept_len,
            "q_start": q_start,
            # question_source is the 4th question-band field on every sheet.
            "q_src_col": q_start + 3,
            # concept_source only exists in the current layout.
            "c_src_col": (_IDX_CONCEPT_TITLE + concept_len - 1
                          if concept_len > LEGACY_CONCEPT_LEN else None),
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
                idx.concept_rows.setdefault((ck[0], ck[1]), []).append((sheet_name, row_i))
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


def _topic_number(topic: models.Topic) -> int:
    """1-based position of the topic within its chapter (textbook order)."""
    siblings = sorted(topic.chapter.topics, key=lambda t: t.id)
    try:
        return siblings.index(topic) + 1
    except ValueError:
        return 1


def composed_topic_title(topic: models.Topic) -> str:
    """Tagged topic title cell, e.g. 'Topic 01: <Title> (<tag>)'.

    ``strip_topic_title`` normalizes the stored title first so an already-tagged
    value never gets a second 'Topic NN:'/code prefix.
    """
    chapter = topic.chapter
    clean = strip_topic_title(topic.topic_title) or topic.topic_title
    t_tag = directory.topic_tag(
        chapter.board, chapter.grade, chapter.subject, chapter.chapter_title)
    return f"Topic {_topic_number(topic):02d}: {clean} ({t_tag})"


def composed_topic_display(topic: models.Topic) -> str:
    """Clean topic display name, e.g. 'Topic 01: <Title>' (no tag/code)."""
    clean = strip_topic_title(topic.topic_title) or topic.topic_title
    return f"Topic {_topic_number(topic):02d}: {clean}"


def _groups_by_type(concept: models.Concept) -> dict[str, str]:
    """All groups of each type, comma-separated (S/T/U columns)."""
    buckets: dict[str, list[str]] = {"Basic": [], "Intermediate": [], "Advanced": []}
    for g in sorted(concept.groups, key=lambda g: g.id):
        if g.group_type in buckets:
            buckets[g.group_type].append(g.group_display_name or g.group_name)
    return {k: ", ".join(v) for k, v in buckets.items()}


def _front_bands(concept: models.Concept, topic: models.Topic, *,
                 include_group_columns: bool = True) -> list:
    """Chapter + Topic + Concept bands (22 cells) with tags in the title columns.

    The title columns carry a human-readable tag; the display columns stay
    clean (the reader strips the tags back to the clean model values).

    ``include_group_columns`` is False for concept-catalog rows — group columns
    are filled later when assessments are built, not at concept generation.
    """
    chapter = topic.chapter
    c_tag = directory.chapter_tag(chapter.board, chapter.grade, chapter.subject)
    cp_tag = directory.concept_tag(
        chapter.board, chapter.grade, chapter.subject,
        chapter.chapter_title, topic.topic_title)
    concept_labels = ", ".join(
        c.concept_title for c in sorted(topic.concepts, key=lambda c: c.id))
    if include_group_columns:
        by_type = _groups_by_type(concept)
        group_cols = [by_type["Basic"], by_type["Intermediate"], by_type["Advanced"]]
    else:
        group_cols = ["", "", ""]
    return [
        # ---- Chapter band (tag in title, clean display) ----
        f"{chapter.chapter_title} ({c_tag})", chapter.chapter_title,
        chapter.chapter_duration, chapter.pre_topics, chapter.post_topics,
        chapter.chapter_description,
        # ---- Topic band ("Topic NN: <title> (<tag>)", display "Topic NN: <title>") ----
        composed_topic_title(topic),
        composed_topic_display(topic), topic.pre_post_learning, concept_labels,
        topic.related_topics, topic.topic_description,
        # ---- Concept band (tag in title, clean display; group cols optional) ----
        f"{concept.concept_title} ({cp_tag})", concept.concept_title,
        concept.concept_details, concept.keywords, concept.digicards,
        concept.related_concepts,
        *group_cols,
        concept.sources,
    ]


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

    row: list = list(_front_bands(concept, topic))
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
    row: list = list(_front_bands(concept, topic, include_group_columns=False))
    expected = len(FIELDS_BY_KIND[kind])
    row += [""] * (expected - len(row))
    return row[:expected]


def _refresh_concept_sources(wb, index: WorkbookIndex, concept: models.Concept,
                             chapter_title: str) -> int:
    """Merge ``concept.sources`` into the concept_source cell of every existing
    row that carries this concept's band (concept-only rows AND question rows)."""
    updated = 0
    for sheet_name, row_i in index.concept_rows.get(
            (concept.concept_title, chapter_title), []):
        meta = index.sheet_meta.get(sheet_name) or {}
        col = meta.get("c_src_col")
        if col is None:  # legacy-layout sheet: no concept_source column to update
            continue
        cell = wb[sheet_name].cell(row=row_i, column=col + 1)
        merged = merge_sources(str(cell.value or ""), concept.sources)
        if merged != str(cell.value or ""):
            cell.value = merged
            updated += 1
    return updated


def append_concepts(db: Session, path: Path, concept_ids: list[int]) -> dict[str, int]:
    """Append concept-catalog rows (no questions) to the Objective sheet.

    One row per (concept, placement): the concept's home topic plus every
    tagged topic/chapter. Placements already present are never re-added —
    instead their ``concept_source`` cells are refreshed in place so a concept
    re-used from another book accumulates sources (e.g. "NCERT; RD Sharma").
    """
    index = scan_workbook(path)
    wb = openpyxl.load_workbook(path) if path.exists() else _new_workbook()
    ws = wb[SHEET_BY_KIND["objective"]]
    concepts = (
        db.query(models.Concept).filter(models.Concept.id.in_(concept_ids))
        .order_by(models.Concept.id).all()
    )
    result = {"written": 0, "sources_updated": 0}
    for c in concepts:
        for topic in _concept_placements(c):
            key = concept_placement_key(c, topic)
            if key in index.c_placements:
                result["sources_updated"] += _refresh_concept_sources(
                    wb, index, c, topic.chapter.chapter_title)
                continue
            index.c_placements.add(key)
            target = ws.max_row + 1 if ws.max_row >= 2 else 3
            for i, value in enumerate(_concept_to_row(c, "objective", topic), start=1):
                ws.cell(row=target, column=i, value=value)
            result["written"] += 1
    wb.save(path)
    return result


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
        .order_by(models.Concept.id).all()
    )
    next_row = 3
    for c in concepts:
        for topic in _concept_placements(c):
            for i, value in enumerate(_concept_to_row(c, "objective", topic), start=1):
                ws.cell(row=next_row, column=i, value=value)
            next_row += 1
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


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
                    ws.cell(row=next_row[question.sheet_kind], column=i, value=value)
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
                    ws_obj.cell(row=next_row["objective"], column=i, value=value)
                next_row["objective"] += 1

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


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
                        cell.value = merged
                        appended["sources_updated"] += 1
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

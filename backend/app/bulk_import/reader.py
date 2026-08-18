"""Read a Bulk Import workbook and normalize it into the DB hierarchy.

The workbook's layout is IDENTIFIED first, by exact sheet name + header-row
equality against ``bulk_import.layouts``; every column is then addressed **by
name** through that layout. A workbook whose header matches no registered
layout is refused whole (``WorkbookLayoutError`` -> 422) before anything is
written: reading part of a file whose column geometry is unknown is how 342 of
344 question-band positions were silently mis-read (spec-step8 T6).

Rows that only carry chapter/topic/concept/group context (no question) still
create the hierarchy nodes; a Question is created only when the Question band
carries text or a label.
"""
from __future__ import annotations

import re
from pathlib import Path

import openpyxl
from sqlalchemy.orm import Session

from . import (
    ANSWER_TYPES, APPEARS_IN_ALL, COGNITIVE_SKILLS, DIFFICULTY_LEVELS,
    merge_sources, normalize_answer_type, normalize_appears_in,
    normalize_cognitive_skills, normalize_difficulty, normalize_question_text,
    split_multi, strip_title_tag, strip_topic_title, to_plain_text,
)
from . import layouts
from .layouts import WorkbookLayoutError  # noqa: F401  (re-exported for api)
from .. import models
from ..services import directory, identity, katex_rules


def _parse_answers(
    qd: dict, kind: str, sheet_layout: layouts.SheetLayout,
) -> tuple[list[dict], list[dict]]:
    """Return (answers, sub_questions) read BY NAME from the question band.

    The block COUNT comes from the identified layout (how many
    ``answer_type_N`` / ``sub_question_N`` columns it declares), never from a
    literal: the canonical Subjective sheet carries 10 answer blocks and the
    reference one carries 20, and the old ``range(10)`` made blocks 11-20
    unreadable by construction.
    """
    answers: list[dict] = []
    sub_questions: list[dict] = []

    def cell(name: str) -> str:
        return qd.get(name, "")

    if kind == "objective":
        for n in sheet_layout.answer_block_numbers:
            atype, content = cell(f"answer_type_{n}"), cell(f"answer_content_{n}")
            if not (atype or content):
                continue
            answers.append({
                "answer_type": atype, "answer_content": content,
                "correct_answer": cell(f"correct_answer_{n}"),
                "answer_weightage": cell(f"answer_weightage_{n}"),
            })
    elif kind == "subjective":
        for n in sheet_layout.answer_block_numbers:
            atype, ans = cell(f"answer_type_{n}"), cell(f"answer_{n}")
            if not (atype or ans):
                continue
            answers.append({
                "answer_type": atype, "answer": ans,
                "answer_display": cell(f"answer_display_{n}"),
                "weightage": cell(f"weightage_{n}"),
                "placeholder": cell(f"placeholder_{n}"),
            })
    else:  # descriptive
        for n in sheet_layout.answer_block_numbers:
            atype, content = cell(f"answer_type_{n}"), cell(f"answer_content_{n}")
            if not (atype or content):
                continue
            answers.append({
                "answer_type": atype,
                "answer_weightage": cell(f"answer_weightage_{n}"),
                "answer_content": content,
            })
        for n in sheet_layout.sub_question_numbers:
            text = cell(f"sub_question_{n}")
            if not text:
                continue
            keywords = []
            for m in sheet_layout.sub_question_keyword_numbers(n):
                atype = cell(f"sq{n}_answer_type_{m}")
                weight = cell(f"sq{n}_weightage_{m}")
                kw = cell(f"sq{n}_keyword_{m}")
                if not (atype or kw):
                    continue
                keywords.append({"answer_type": atype, "weightage": weight, "keyword": kw})
            sub_questions.append({
                "text": text, "marks": cell(f"sub_question_marks_{n}"),
                "keywords": keywords,
            })
    return answers, sub_questions


_MAX_ISSUES = 200

# Group-band fields that carry meaningful Group identity. The linkage columns
# (``concept_question_labels`` and Descriptive's linkage ``question_label``)
# belong to neighbouring bands and never establish a group by themselves.
_GROUP_IDENTITY_FIELDS = (
    "group_name", "group_display_name", "group_type",
    "group_description", "group_question_labels",
)

_GROUP_SEQUENCE_RE = re.compile(r"(\d+)\s*\)?\s*$")


def _group_sequence(machine_name: str) -> int:
    """Tier sequence from a machine Group ID tail (``... BG02`` -> 2).

    Mechanical ID parsing only — it never decides which group a question
    belongs to.
    """
    match = _GROUP_SEQUENCE_RE.search(str(machine_name or "").strip())
    return int(match.group(1)) if match else 0


def _format_issues(label: str, *texts: str) -> list[str]:
    """Content-format validation: katex/img/link rules (allowed CMS formats)."""
    issues: list[str] = []
    blob = "\n".join(t for t in texts if t)
    messages = {
        "unbalanced_katex": "unbalanced [Katex] tag",
        "nested_katex": "nested [Katex] tag",
        "malformed_katex": "malformed [Katex] tag",
        "empty_katex": "empty [Katex] tag",
        "markdown_image": "Markdown image found — use [img src=\"...\" alt=\"...\"]",
        "raw_math_delimiter": "raw math delimiters found — use [Katex]...[/Katex]",
        "raw_math_expression": (
            "raw equation found — use [Katex]...[/Katex]"),
        "raw_latex": "raw LaTeX found outside a [Katex] tag",
        "unbalanced_image": "unclosed [img] tag",
        "invalid_image_src": "[img] without a full HTTPS src URL",
        "missing_image_alt": "[img] missing alt text",
        "noncanonical_image": (
            "[img] must contain only ordered src and alt attributes"),
    }
    # Import accepts legacy lower-case [katex] and canonicalizes it before
    # persistence; all other syntax rules stay strict.
    for code in katex_rules.rich_text_issues(
        blob, require_canonical_case=False
    ):
        message = messages.get(code)
        if message:
            issues.append(f"{label}: {message}")
    return issues


def _weightage_sum(answers: list[dict], kind: str) -> float | None:
    key = "weightage" if kind == "subjective" else "answer_weightage"
    total = 0.0
    found = False
    for a in answers:
        raw = str(a.get(key, "") or "").strip()
        if not raw:
            continue
        try:
            total += float(raw)
            found = True
        except ValueError:
            return None
    return total if found else None


def _sheet_headers(wb) -> dict[str, tuple]:
    """Row 2 of every sheet, in workbook order."""
    headers: dict[str, tuple] = {}
    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        headers[sheet_name] = next(
            ws.iter_rows(min_row=2, max_row=2, values_only=True), ())
    return headers


def import_workbook(db: Session, path: Path) -> dict:
    """Import every content sheet; returns counts of created nodes + issues.

    Raises ``WorkbookLayoutError`` (mapped to 422 by ``api/data.py``) when the
    workbook matches no registered layout. The refusal happens before any
    ``db.add``, so an unidentified file imports nothing at all.
    """
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    try:
        identified = layouts.identify_workbook(_sheet_headers(wb))
    except WorkbookLayoutError:
        wb.close()
        raise
    counts: dict = {"chapters": 0, "topics": 0, "concepts": 0, "groups": 0,
                    "questions": 0, "question_tags": 0, "issues": [],
                    "layout_id": identified.layout_id}

    def _flag(msg: str) -> None:
        if len(counts["issues"]) < _MAX_ISSUES:
            counts["issues"].append(msg)

    # Caches keyed by natural keys for de-duplication within one import.
    chapters: dict[str, models.Chapter] = {}
    topics: dict[tuple, models.Topic] = {}
    concepts: dict[tuple, models.Concept] = {}
    groups: dict[tuple, models.Group] = {}
    seen_labels: set[str] = {
        q.question_label for q in db.query(models.Question).all() if q.question_label
    }
    # Questions created or matched during THIS import, by label, so repeated
    # rows reconstruct placements against the right entity.
    label_questions: dict[str, models.Question] = {}

    # Cache of existing question texts per chapter, for cross-book dedupe.
    qtext_cache: dict[int, dict[str, models.Question]] = {}

    def _chapter_qtexts(chapter_id: int) -> dict[str, models.Question]:
        if chapter_id not in qtext_cache:
            qtext_cache[chapter_id] = {
                normalize_question_text(qq.question): qq
                for qq in (
                    db.query(models.Question)
                    .join(models.Group).join(models.Concept).join(models.Topic)
                    .filter(models.Topic.chapter_id == chapter_id)
                )
                if qq.question
            }
        return qtext_cache[chapter_id]

    for found in identified.sheets:
        kind = found.kind
        sheet_name = found.sheet_name
        sheet_layout = found.sheet
        ws = wb[sheet_name]

        for row_i, row in enumerate(ws.iter_rows(min_row=3, values_only=True), start=3):
            if row is None or not any(row):
                continue
            chap = sheet_layout.block_values(row, "chapter")
            top = sheet_layout.block_values(row, "topic")
            con = sheet_layout.block_values(row, "concept")
            grp = sheet_layout.block_values(row, "group")

            if not chap.get("chapter_title"):
                _flag(f"{sheet_name!r} row {row_i}: skipped — missing chapter_title")
                continue

            # ---- Chapter ----
            # Derive board/grade/subject/code from the RAW (tag-bearing) cells
            # first; the topic/concept tags carry the ID code prefix.
            meta = directory.derive_chapter_meta(
                chap["chapter_title"], chap.get("chapter_display_name", ""),
                top.get("topic_title", ""), top.get("topic_display_name", ""),
                top.get("topic_concept_labels", ""), top.get("related_topics", ""),
                con.get("concept_title", ""), con.get("concept_display_name", ""),
                chap.get("post_topics", ""), chap.get("pre_topics", ""),
            )
            chapter_code = meta["chapter_code"]
            # Recover CLEAN titles (strip embedded tags / "Topic NN:" prefix).
            chap_title = strip_title_tag(chap["chapter_title"])
            t_title_clean = strip_topic_title(top.get("topic_title", ""))
            c_title_clean = strip_title_tag(con.get("concept_title", ""))
            # T6.2: the chapter code truncates ('The Rise of Nationalism in
            # Europe' and '... in Asia' both derive 10CBSS_TheRiseOfNat), so
            # the code alone silently MERGED two chapters. The identity
            # carries the title as well; a code collision with a different
            # title is recorded and the rows are not merged.
            chap_title_key = normalize_question_text(
                chap_title or chap["chapter_title"])
            ch_key = (chapter_code, chap_title_key)
            chapter = chapters.get(ch_key)
            if chapter is None:
                same_code = db.query(models.Chapter).filter_by(
                    chapter_code=chapter_code).all()
                chapter = next(
                    (
                        row_chapter for row_chapter in same_code
                        if normalize_question_text(strip_title_tag(
                            row_chapter.chapter_title)) == chap_title_key
                    ),
                    None,
                )
                if chapter is None and same_code:
                    _flag(
                        f"chapter code {chapter_code!r} is already used by "
                        f"{same_code[0].chapter_title!r}; "
                        f"{(chap_title or chap['chapter_title'])!r} imports as "
                        "a separate chapter (chapter_code_collision)"
                    )
            if chapter is None:
                chapter = models.Chapter(
                    chapter_code=chapter_code,
                    board=meta["board"], grade=meta["grade"],
                    subject=meta["subject"], unit=meta["unit"],
                    chapter_title=chap_title or chap["chapter_title"],
                    chapter_display_name=chap.get("chapter_display_name", ""),
                    chapter_duration=chap.get("chapter_duration", ""),
                    pre_topics=chap.get("pre_topics", ""),
                    post_topics=chap.get("post_topics", ""),
                    chapter_description=chap.get("chapter_description", ""),
                )
                db.add(chapter)
                db.flush()
                counts["chapters"] += 1
            chapters[ch_key] = chapter

            # ---- Topic ----
            # T6.4: the identity carries the LANE and goes through the one
            # shared normaliser. Without the lane a Pre topic and a Post topic
            # sharing a title merged into whichever row was created first and
            # the other lane's concepts were re-parented under it, silently.
            # The stored ``topic_title`` is NEVER rewritten to the incoming
            # casing (T4-6: exact-match-plus-lenient-write is what created the
            # UploadRefused class).
            t_title = t_title_clean or "Topic 01"
            t_lane = top.get("pre_post_learning", "") or "Post"
            t_ident = identity.topic_identity(t_title)
            t_key = (chapter.id, t_ident, t_lane)
            topic = topics.get(t_key)
            if topic is None:
                topic = next(
                    (
                        row_topic for row_topic in db.query(models.Topic)
                        .filter_by(chapter_id=chapter.id,
                                   pre_post_learning=t_lane).all()
                        if identity.topic_identity(row_topic.topic_title)
                        == t_ident
                    ),
                    None,
                )
            if topic is None:
                other_lane = next(
                    (
                        row_topic for row_topic in db.query(models.Topic)
                        .filter_by(chapter_id=chapter.id).all()
                        if identity.topic_identity(row_topic.topic_title)
                        == t_ident and row_topic.pre_post_learning != t_lane
                    ),
                    None,
                )
                if other_lane is not None:
                    _flag(
                        f"{t_title!r}: a {other_lane.pre_post_learning!r} topic "
                        f"already carries this title; the {t_lane!r} row "
                        "imports as a separate topic (topic_lane_conflict)"
                    )
                topic = models.Topic(
                    chapter_id=chapter.id, topic_title=t_title,
                    topic_display_name=top.get("topic_display_name", ""),
                    pre_post_learning=t_lane,
                    related_topics=top.get("related_topics", ""),
                    topic_description=top.get("topic_description", ""),
                )
                db.add(topic)
                db.flush()
                counts["topics"] += 1
            topics[t_key] = topic

            # ---- Concept ----
            c_title = c_title_clean or "Concept"
            c_source = con.get("concept_source", "")
            c_key = (topic.id, c_title)
            concept = concepts.get(c_key)
            if concept is None:
                concept = db.query(models.Concept).filter_by(
                    topic_id=topic.id, concept_title=c_title).first()
            if concept is None:
                concept_details = katex_rules.canonicalize_rich_text(
                    con.get("concept_details", ""))
                for issue in _format_issues(
                    f"{sheet_name!r} row {row_i} concept_details",
                    concept_details,
                ):
                    _flag(issue)
                concept = models.Concept(
                    topic_id=topic.id, concept_title=c_title,
                    concept_display_name=con.get("concept_display_name", ""),
                    parent_concept=con.get("parent_concept", ""),
                    concept_details=concept_details,
                    keywords=con.get("keywords", ""),
                    digicards=con.get("digicards", ""),
                    related_concepts=con.get("related_concepts", ""),
                    sources=c_source,
                )
                db.add(concept)
                db.flush()
                counts["concepts"] += 1
            elif c_source:
                # Same concept arriving from another book: accumulate sources.
                concept.sources = merge_sources(concept.sources, c_source)
            if con.get("parent_concept") and not concept.parent_concept:
                concept.parent_concept = con.get("parent_concept", "")
            concepts[c_key] = concept

            # ---- Question band (parsed before the Group so a pure Concept
            # row can be recognized without minting a default group) ----
            # Names AND values now come from the same identified layout. They
            # used to decouple by construction: the names were taken from the
            # canonical FIELDS_BY_KIND while the values were sliced from the
            # detected geometry.
            qd = sheet_layout.block_values(row, "question")
            label = qd.get("question_label", "")
            has_question = bool(label or qd.get("question"))

            # ---- Group ----
            # A Group exists only when the row carries meaningful Group
            # identity or a populated Question band (spec §9): a pure Concept
            # row must not mint a default Basic group.
            has_group_identity = any(
                grp.get(field, "").strip()
                for field in _GROUP_IDENTITY_FIELDS
            )
            if not (has_group_identity or has_question):
                continue  # hierarchy-only row: chapter/topic/concept created
            g_type = grp.get("group_type") or "Basic"
            g_name = grp.get("group_name") or grp.get("group_display_name") or f"{g_type} Group"
            g_key = (concept.id, g_type, g_name)
            group = groups.get(g_key)
            if group is None:
                group = db.query(models.Group).filter_by(
                    concept_id=concept.id, group_type=g_type, group_name=g_name).first()
            if group is None:
                group = models.Group(
                    concept_id=concept.id, group_type=g_type, group_name=g_name,
                    group_display_name=grp.get("group_display_name", ""),
                    group_description=grp.get("group_description", ""),
                    group_status=grp.get("group_status", "Active"),
                    related_digicards=grp.get("related_digicards", ""),
                    group_key=g_name,
                    group_sequence=_group_sequence(g_name),
                )
                db.add(group)
                db.flush()
                counts["groups"] += 1
            elif not group.group_key:
                group.group_key = g_name
                group.group_sequence = _group_sequence(g_name)
            groups[g_key] = group

            # ---- Question ----
            if not has_question:
                continue
            if label and label in seen_labels:
                # One label = one Question, many placements. A repeated label
                # reconstructs a placement edge instead of being dropped, so
                # an export re-imported into an empty DB rebuilds every
                # QuestionTag (spec §7.5).
                existing_q = label_questions.get(label)
                if existing_q is None:
                    existing_q = db.query(models.Question).filter_by(
                        question_label=label).first()
                if existing_q is None:
                    _flag(f"{label}: repeated label has no importable "
                          "original question — row skipped")
                    continue
                repeat_norm = normalize_question_text(qd.get("question", ""))
                if repeat_norm and repeat_norm != normalize_question_text(
                        existing_q.question):
                    _flag(f"{label}: conflicting question content under one "
                          "label — row rejected")
                    continue
                if existing_q.group_id == group.id:
                    continue  # exact duplicate of the home placement
                tag_exists = db.query(models.QuestionTag).filter_by(
                    question_id=existing_q.id, group_id=group.id).first()
                if tag_exists is None:
                    db.add(models.QuestionTag(
                        question_id=existing_q.id, group_id=group.id))
                    counts["question_tags"] += 1
                continue
            # Cross-book duplicate check: same question text under the same
            # chapter (any label) is not re-added — its sources merge instead.
            norm = normalize_question_text(qd.get("question", ""))
            if norm:
                existing_q = _chapter_qtexts(chapter.id).get(norm)
                if existing_q is not None:
                    existing_q.question_source = merge_sources(
                        existing_q.question_source, qd.get("question_source", ""))
                    counts["question_sources_merged"] = counts.get(
                        "question_sources_merged", 0) + 1
                    seen_labels.add(label)
                    if label:
                        label_questions[label] = existing_q
                    continue
            seen_labels.add(label)

            answers, sub_questions = _parse_answers(qd, kind, sheet_layout)
            try:
                marks = float(qd.get("marks") or 0)
            except ValueError:
                marks = 0.0
                _flag(f"{label or 'row ' + str(row_i)}: marks not numeric "
                      f"({qd.get('marks')!r})")
            try:
                duration = float(qd.get("question_duration") or 1)
            except ValueError:
                duration = 1.0

            # ---- Normalization to standard values ----
            skills = normalize_cognitive_skills(qd.get("cognitive_skills", ""))
            for part in split_multi(skills):
                if part not in COGNITIVE_SKILLS:
                    _flag(f"{label}: unknown cognitive skill {part!r}")
            difficulty = normalize_difficulty(qd.get("level_of_difficulty", ""))
            if difficulty and difficulty not in DIFFICULTY_LEVELS:
                _flag(f"{label}: unknown level_of_difficulty {difficulty!r}")
            appears = normalize_appears_in(qd.get("question_appears_in", ""))
            for a in answers:
                a["answer_type"] = normalize_answer_type(a.get("answer_type", ""))
                if a["answer_type"] and a["answer_type"] not in ANSWER_TYPES:
                    _flag(f"{label}: unknown answer_type {a['answer_type']!r}")

            # ---- Validation: weightage sum vs marks; content formats ----
            if kind in {"subjective", "descriptive"} and marks:
                total = _weightage_sum(answers, kind)
                if total is not None and abs(total - marks) > 0.01:
                    _flag(f"{label}: answer weightage sum {total:g} != marks {marks:g}")
            for issue in _format_issues(
                label or f"row {row_i}", qd.get("question", ""),
                qd.get("answer_explanation", ""),
                *(str(a.get("answer_content", "")) + str(a.get("answer", ""))
                  for a in answers),
            ):
                _flag(issue)

            # ---- question_text: parse if present, else backfill (plain text) ----
            question_text = qd.get("question_text", "").strip()
            if not question_text and qd.get("question"):
                question_text = to_plain_text(qd.get("question", ""))

            new_q = models.Question(
                group_id=group.id, sheet_kind=kind, question_label=label,
                question_category=qd.get("question_category", ""),
                cognitive_skills=skills,
                question_source=qd.get("question_source", ""),
                question_disclaimer=qd.get("question_disclaimer", ""),
                question_duration=duration,
                math_keyboard=qd.get("math_keyboard", ""),
                # T6.3: the READER's default is the one its own layout
                # implies, never the active school profile's wire value —
                # sourcing it from the profile would stamp one school's
                # convention onto every other school's imported rows.
                question_appears_in=appears or APPEARS_IN_ALL,
                level_of_difficulty=difficulty,
                question=qd.get("question", ""),
                question_text=question_text,
                marks=marks,
                display_answer=qd.get("display_answer", ""),
                answer_explanation=qd.get("answer_explanation", ""),
                answers=answers, sub_questions=sub_questions, origin="seed",
            )
            db.add(new_q)
            db.flush()
            if norm:
                _chapter_qtexts(chapter.id)[norm] = new_q
            if label:
                label_questions[label] = new_q
            counts["questions"] += 1

    db.commit()
    wb.close()
    return counts

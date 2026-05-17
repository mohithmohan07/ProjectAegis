"""Read a canonical Bulk Import workbook and normalize it into the DB hierarchy.

Columns are addressed positionally because the canonical layout repeats
``question_label`` across bands. Rows that only carry chapter/topic/concept/
group context (no question) still create the hierarchy nodes; a Question is
created only when the Question band carries text or a label.
"""
from __future__ import annotations

from pathlib import Path

import openpyxl
from sqlalchemy.orm import Session

from . import (
    CHAPTER_FIELDS, TOPIC_FIELDS, CONCEPT_FIELDS, FIELDS_BY_KIND, SHEET_BY_KIND,
    OBJECTIVE_GROUP_FIELDS, DESCRIPTIVE_GROUP_FIELDS,
)
from .. import models
from ..services import directory

# Front bands are identical across sheets.
_CHAPTER_SLICE = slice(0, len(CHAPTER_FIELDS))
_TOPIC_SLICE = slice(len(CHAPTER_FIELDS), len(CHAPTER_FIELDS) + len(TOPIC_FIELDS))
_CONCEPT_START = len(CHAPTER_FIELDS) + len(TOPIC_FIELDS)
_CONCEPT_SLICE = slice(_CONCEPT_START, _CONCEPT_START + len(CONCEPT_FIELDS))


def _group_slice(kind: str) -> slice:
    gf = DESCRIPTIVE_GROUP_FIELDS if kind == "descriptive" else OBJECTIVE_GROUP_FIELDS
    start = _CONCEPT_START + len(CONCEPT_FIELDS)
    return slice(start, start + len(gf))


def _cell(row: tuple, idx: int) -> str:
    if idx >= len(row):
        return ""
    v = row[idx]
    return "" if v is None else str(v).strip()


def _band(row: tuple, fields: list[str], sl: slice) -> dict:
    values = list(row[sl])
    return {
        f: ("" if values[i] is None else str(values[i]).strip())
        for i, f in enumerate(fields)
        if i < len(values)
    }


def _parse_answers(row: tuple, kind: str, q_start: int) -> tuple[list[dict], list[dict]]:
    """Return (answers, sub_questions) from the question band of a row."""
    answers: list[dict] = []
    sub_questions: list[dict] = []

    if kind == "objective":
        base = q_start + 10  # after the 10 scalar question fields
        for n in range(6):
            o = base + n * 4
            atype, content = _cell(row, o), _cell(row, o + 1)
            if not (atype or content):
                continue
            answers.append({
                "answer_type": atype, "answer_content": content,
                "correct_answer": _cell(row, o + 2), "answer_weightage": _cell(row, o + 3),
            })
    elif kind == "subjective":
        base = q_start + 11
        for n in range(10):
            o = base + n * 5
            atype, ans = _cell(row, o), _cell(row, o + 1)
            if not (atype or ans):
                continue
            answers.append({
                "answer_type": atype, "answer": ans,
                "answer_display": _cell(row, o + 2), "weightage": _cell(row, o + 3),
                "placeholder": _cell(row, o + 4),
            })
    else:  # descriptive
        base = q_start + 12  # after 12 scalar fields incl. display_answer
        for n in range(10):
            o = base + n * 3
            atype, content = _cell(row, o), _cell(row, o + 2)
            if not (atype or content):
                continue
            answers.append({
                "answer_type": atype, "answer_weightage": _cell(row, o + 1),
                "answer_content": content,
            })
        subq_base = base + 30 + 1  # +30 answer cells, +1 answer_explanation
        for n in range(15):
            o = subq_base + n * 20
            text = _cell(row, o)
            if not text:
                continue
            keywords = []
            for m in range(6):
                ko = o + 2 + m * 3
                atype, weight, kw = _cell(row, ko), _cell(row, ko + 1), _cell(row, ko + 2)
                if not (atype or kw):
                    continue
                keywords.append({"answer_type": atype, "weightage": weight, "keyword": kw})
            sub_questions.append({
                "text": text, "marks": _cell(row, o + 1), "keywords": keywords,
            })
    return answers, sub_questions


def import_workbook(db: Session, path: Path) -> dict[str, int]:
    """Import every content sheet; returns counts of created nodes."""
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    counts = {"chapters": 0, "topics": 0, "concepts": 0, "groups": 0, "questions": 0}

    # Caches keyed by natural keys for de-duplication within one import.
    chapters: dict[str, models.Chapter] = {}
    topics: dict[tuple, models.Topic] = {}
    concepts: dict[tuple, models.Concept] = {}
    groups: dict[tuple, models.Group] = {}
    seen_labels: set[str] = {
        q.question_label for q in db.query(models.Question).all() if q.question_label
    }

    for kind, sheet_name in SHEET_BY_KIND.items():
        if sheet_name not in wb.sheetnames:
            continue
        ws = wb[sheet_name]
        fields = FIELDS_BY_KIND[kind]
        gslice = _group_slice(kind)
        q_start = gslice.stop

        for row in ws.iter_rows(min_row=3, values_only=True):
            if row is None or not any(row):
                continue
            chap = _band(row, CHAPTER_FIELDS, _CHAPTER_SLICE)
            top = _band(row, TOPIC_FIELDS, _TOPIC_SLICE)
            con = _band(row, CONCEPT_FIELDS, _CONCEPT_SLICE)
            gf = DESCRIPTIVE_GROUP_FIELDS if kind == "descriptive" else OBJECTIVE_GROUP_FIELDS
            grp = _band(row, gf, gslice)

            if not chap.get("chapter_title"):
                continue

            # ---- Chapter ----
            meta = directory.derive_chapter_meta(
                chap["chapter_title"], chap.get("chapter_display_name", ""),
                top.get("topic_title", ""), top.get("topic_display_name", ""),
                top.get("concept", ""), top.get("related_topics", ""),
                con.get("concept_title", ""), con.get("concept_display_name", ""),
                chap.get("post_topics", ""), chap.get("pre_topics", ""),
            )
            ch_key = meta["chapter_code"]
            chapter = chapters.get(ch_key)
            if chapter is None:
                chapter = db.query(models.Chapter).filter_by(chapter_code=ch_key).first()
            if chapter is None:
                chapter = models.Chapter(
                    chapter_code=ch_key, board=meta["board"], grade=meta["grade"],
                    subject=meta["subject"], unit=meta["unit"],
                    chapter_title=chap["chapter_title"],
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
            t_title = top.get("topic_title") or "Topic 01"
            t_key = (chapter.id, t_title)
            topic = topics.get(t_key)
            if topic is None:
                topic = db.query(models.Topic).filter_by(
                    chapter_id=chapter.id, topic_title=t_title).first()
            if topic is None:
                topic = models.Topic(
                    chapter_id=chapter.id, topic_title=t_title,
                    topic_display_name=top.get("topic_display_name", ""),
                    pre_post_learning=top.get("pre_post_learning", "Post"),
                    related_topics=top.get("related_topics", ""),
                    topic_description=top.get("topic_description", ""),
                )
                db.add(topic)
                db.flush()
                counts["topics"] += 1
            topics[t_key] = topic

            # ---- Concept ----
            c_title = con.get("concept_title") or "Concept"
            c_key = (topic.id, c_title)
            concept = concepts.get(c_key)
            if concept is None:
                concept = db.query(models.Concept).filter_by(
                    topic_id=topic.id, concept_title=c_title).first()
            if concept is None:
                concept = models.Concept(
                    topic_id=topic.id, concept_title=c_title,
                    concept_display_name=con.get("concept_display_name", ""),
                    concept_details=con.get("concept_details", ""),
                    keywords=con.get("keywords", ""),
                    digicards=con.get("digicards", ""),
                    related_concepts=con.get("related_concepts", ""),
                )
                db.add(concept)
                db.flush()
                counts["concepts"] += 1
            concepts[c_key] = concept

            # ---- Group ----
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
                )
                db.add(group)
                db.flush()
                counts["groups"] += 1
            groups[g_key] = group

            # ---- Question ----
            q_fields = fields[q_start:]
            q_values = list(row[q_start:])
            qd = {
                q_fields[i]: ("" if q_values[i] is None else str(q_values[i]).strip())
                for i in range(min(len(q_fields), len(q_values)))
            }
            label = qd.get("question_label", "")
            if not (label or qd.get("question")):
                continue
            if label and label in seen_labels:
                continue  # append-only: never re-import an existing label
            seen_labels.add(label)

            answers, sub_questions = _parse_answers(row, kind, q_start)
            try:
                marks = float(qd.get("marks") or 0)
            except ValueError:
                marks = 0.0
            try:
                duration = float(qd.get("question_duration") or 1)
            except ValueError:
                duration = 1.0

            db.add(models.Question(
                group_id=group.id, sheet_kind=kind, question_label=label,
                question_category=qd.get("question_category", ""),
                cognitive_skills=qd.get("cognitive_skills", ""),
                question_source=qd.get("question_source", ""),
                question_disclaimer=qd.get("question_disclaimer", ""),
                question_duration=duration,
                math_keyboard=qd.get("math_keyboard", ""),
                question_appears_in=qd.get("question_appears_in", "Pre/Post-Worksheet/Test"),
                level_of_difficulty=qd.get("level_of_difficulty", ""),
                question=qd.get("question", ""),
                marks=marks,
                display_answer=qd.get("display_answer", ""),
                answer_explanation=qd.get("answer_explanation", ""),
                answers=answers, sub_questions=sub_questions, origin="seed",
            ))
            counts["questions"] += 1

    db.commit()
    wb.close()
    return counts

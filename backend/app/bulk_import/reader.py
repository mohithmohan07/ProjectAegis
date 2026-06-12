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
    ANSWER_TYPES, CHAPTER_FIELDS, COGNITIVE_SKILLS, CONCEPT_FIELDS,
    DESCRIPTIVE_GROUP_FIELDS, DIFFICULTY_LEVELS, FIELDS_BY_KIND,
    LEGACY_CONCEPT_LEN, OBJECTIVE_GROUP_FIELDS, SHEET_BY_KIND, TOPIC_FIELDS,
    merge_sources, normalize_answer_type, normalize_appears_in,
    normalize_cognitive_skills, normalize_difficulty, normalize_question_text,
    split_multi, to_plain_text,
)
from .. import models
from ..services import directory

# Front bands are identical across sheets.
_CHAPTER_SLICE = slice(0, len(CHAPTER_FIELDS))
_TOPIC_SLICE = slice(len(CHAPTER_FIELDS), len(CHAPTER_FIELDS) + len(TOPIC_FIELDS))
_CONCEPT_START = len(CHAPTER_FIELDS) + len(TOPIC_FIELDS)


def _concept_len(header_row: tuple) -> int:
    """Concept-band length: current layout (with concept_source) or legacy."""
    idx = _CONCEPT_START + LEGACY_CONCEPT_LEN
    val = header_row[idx] if idx < len(header_row) else None
    return LEGACY_CONCEPT_LEN + 1 if str(val or "").strip() == "concept_source" else LEGACY_CONCEPT_LEN


def _group_slice(kind: str, concept_len: int) -> slice:
    gf = DESCRIPTIVE_GROUP_FIELDS if kind == "descriptive" else OBJECTIVE_GROUP_FIELDS
    start = _CONCEPT_START + concept_len
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


_MAX_ISSUES = 200


def _format_issues(label: str, *texts: str) -> list[str]:
    """Content-format validation: katex/img/link rules (allowed CMS formats)."""
    import re as _re
    issues: list[str] = []
    blob = "\n".join(t for t in texts if t)
    if "$$" in blob:
        issues.append(f"{label}: raw $$...$$ delimiters found — use [katex]...[/katex]")
    if _re.search(r"\[katex\]\s*\[/katex\]", blob):
        issues.append(f"{label}: empty [katex] tag")
    for m in _re.finditer(r"\[img([^\]]*)\]", blob):
        attrs = m.group(1)
        if 'src="http' not in attrs:
            issues.append(f"{label}: [img] without a full http(s) src URL")
        elif 'alt="' not in attrs:
            issues.append(f"{label}: [img] missing alt text")
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


def import_workbook(db: Session, path: Path) -> dict:
    """Import every content sheet; returns counts of created nodes + issues."""
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    counts: dict = {"chapters": 0, "topics": 0, "concepts": 0, "groups": 0,
                    "questions": 0, "issues": []}

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

    for kind, sheet_name in SHEET_BY_KIND.items():
        if sheet_name not in wb.sheetnames:
            continue
        ws = wb[sheet_name]
        fields = FIELDS_BY_KIND[kind]
        header = next(ws.iter_rows(min_row=2, max_row=2, values_only=True), ())
        concept_len = _concept_len(header)
        concept_slice = slice(_CONCEPT_START, _CONCEPT_START + concept_len)
        gf = DESCRIPTIVE_GROUP_FIELDS if kind == "descriptive" else OBJECTIVE_GROUP_FIELDS
        gslice = _group_slice(kind, concept_len)
        q_start = gslice.stop
        # Question-band field NAMES are canonical regardless of sheet layout.
        q_band_fields = fields[_CONCEPT_START + len(CONCEPT_FIELDS) + len(gf):]

        for row_i, row in enumerate(ws.iter_rows(min_row=3, values_only=True), start=3):
            if row is None or not any(row):
                continue
            chap = _band(row, CHAPTER_FIELDS, _CHAPTER_SLICE)
            top = _band(row, TOPIC_FIELDS, _TOPIC_SLICE)
            con = _band(row, CONCEPT_FIELDS[:concept_len], concept_slice)
            grp = _band(row, gf, gslice)

            if not chap.get("chapter_title"):
                _flag(f"{sheet_name!r} row {row_i}: skipped — missing chapter_title")
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
            c_source = con.get("concept_source", "")
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
                    sources=c_source,
                )
                db.add(concept)
                db.flush()
                counts["concepts"] += 1
            elif c_source:
                # Same concept arriving from another book: accumulate sources.
                concept.sources = merge_sources(concept.sources, c_source)
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
            q_values = list(row[q_start:])
            qd = {
                q_band_fields[i]: ("" if q_values[i] is None else str(q_values[i]).strip())
                for i in range(min(len(q_band_fields), len(q_values)))
            }
            label = qd.get("question_label", "")
            if not (label or qd.get("question")):
                continue
            if label and label in seen_labels:
                continue  # append-only: never re-import an existing label
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
                    continue
            seen_labels.add(label)

            answers, sub_questions = _parse_answers(row, kind, q_start)
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
                question_appears_in=appears or "Pre-test, Post-test, Worksheet, Test",
                level_of_difficulty=difficulty,
                question=qd.get("question", ""),
                question_text=question_text,
                marks=marks,
                display_answer=qd.get("display_answer", ""),
                answer_explanation=qd.get("answer_explanation", ""),
                answers=answers, sub_questions=sub_questions, origin="seed",
            )
            db.add(new_q)
            if norm:
                _chapter_qtexts(chapter.id)[norm] = new_q
            counts["questions"] += 1

    db.commit()
    wb.close()
    return counts

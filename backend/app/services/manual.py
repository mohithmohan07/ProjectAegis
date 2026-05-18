"""Manual concept / question creation.

Sits outside the upload + generation pipelines: the user types fields in a
form, we persist them as if they came from any other origin, and we append
them to the canonical Bulk Import output workbook (append-only, like every
other path through the system).
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from .. import config, models
from ..bulk_import import writer
from . import directory
from . import katex_rules as kr

DIFFICULTY_TO_GROUP = {"Less": "Basic", "Moderate": "Intermediate", "High": "Advanced"}
GROUP_CODE = {"Basic": "BG", "Intermediate": "IG", "Advanced": "AG"}


def _slug(text: str, n: int = 14) -> str:
    import re
    return re.sub(r"[^A-Za-z0-9]", "", (text or "").title())[:n] or "X"


def _ensure_chapter(db: Session, board: str, grade: str, subject: str,
                    chapter_title: str) -> models.Chapter:
    code = directory.make_chapter_code(board, grade, subject, chapter_title)
    existing = db.query(models.Chapter).filter_by(chapter_code=code).first()
    if existing:
        return existing
    chapter = models.Chapter(
        chapter_code=code, board=board, grade=grade, subject=subject,
        unit=f"{subject} Unit",
        chapter_title=chapter_title,
        chapter_display_name=f"{chapter_title} ({code})",
        chapter_duration="3",
        chapter_description=f"{board} Grade {grade} {subject}: {chapter_title}.",
    )
    db.add(chapter)
    db.flush()
    return chapter


def _ensure_topic(db: Session, chapter: models.Chapter, topic_title: str,
                  topic_no: int | None = None) -> models.Topic:
    # Wrap loose titles in the canonical "Topic 01: ... (code_PL)" pattern.
    title = topic_title.strip() or "Topic 01"
    if not title.lower().startswith("topic"):
        n = topic_no or (len(chapter.topics) + 1)
        title = f"Topic {n:02d}: {title} ({chapter.chapter_code}_PL)"
    existing = (
        db.query(models.Topic)
        .filter_by(chapter_id=chapter.id, topic_title=title)
        .first()
    )
    if existing:
        return existing
    topic = models.Topic(
        chapter_id=chapter.id, topic_title=title,
        topic_display_name=topic_title,
        pre_post_learning="Post",
        topic_description=f"{topic_title}: {chapter.subject} topic.",
    )
    db.add(topic)
    db.flush()
    return topic


def _ensure_groups(db: Session, concept: models.Concept) -> dict[str, models.Group]:
    """Make sure all three group types exist for the concept; return them by type."""
    existing = {g.group_type: g for g in concept.groups}
    for g_type in ("Basic", "Intermediate", "Advanced"):
        if g_type in existing:
            continue
        slug = _slug(concept.concept_title)
        topic_no = max(1, len(concept.topic.chapter.topics))
        group = models.Group(
            concept_id=concept.id, group_type=g_type,
            group_name=(
                f"({concept.topic.chapter.chapter_code}_PL_T{topic_no:02d}_{slug})"
                f" {GROUP_CODE[g_type]}01"
            ),
            group_display_name=f"{concept.concept_title} - {g_type}",
            group_description=f"{g_type} group for {concept.concept_title}.",
            group_status="Active",
        )
        db.add(group)
        db.flush()
        existing[g_type] = group
    return existing


def create_concept(
    db: Session, *,
    board: str, grade: str, subject: str,
    chapter_title: str, topic_title: str,
    concept_title: str, summary: str, formula: str | None,
    keywords: str,
) -> models.Concept:
    chapter = _ensure_chapter(db, board, grade, subject, chapter_title)
    topic = _ensure_topic(db, chapter, topic_title)
    details = f"Description: {summary.strip()}"
    if formula:
        details += f" // Key relation: {kr.katex(formula)}"
    concept = models.Concept(
        topic_id=topic.id,
        concept_title=concept_title.strip(),
        concept_display_name=(
            f"{concept_title.strip()} "
            f"({chapter.chapter_code}_PL_{topic.topic_title.replace(' ', '_')})"
        ),
        concept_details=details,
        keywords=keywords.strip(),
    )
    db.add(concept)
    db.flush()
    _ensure_groups(db, concept)
    db.commit()
    db.refresh(concept)
    writer.append_concepts(db, config.BULK_IMPORT_OUTPUT, [concept.id])
    return concept


def create_question(
    db: Session, *,
    concept_id: int, sheet_kind: str, category: str,
    cognitive_skills: str, difficulty: str, marks: float,
    question: str, answer_explanation: str,
    answers: list[dict], sub_questions: list[dict],
) -> models.Question:
    concept = db.get(models.Concept, concept_id)
    if not concept:
        raise ValueError(f"concept {concept_id} not found")
    groups = _ensure_groups(db, concept)
    g_type = DIFFICULTY_TO_GROUP.get(difficulty, "Intermediate")
    group = groups[g_type]
    slug = _slug(concept.concept_title)
    topic_no = max(1, len(concept.topic.chapter.topics))
    existing_n = (
        db.query(models.Question)
        .filter(models.Question.group_id == group.id)
        .count()
    )
    label = (
        f"{concept.topic.chapter.chapter_code}_PL_T{topic_no:02d}_{slug}"
        f"_{GROUP_CODE[g_type]} Q{existing_n + 1:02d}"
    )
    q = models.Question(
        group_id=group.id, sheet_kind=sheet_kind, question_label=label,
        question_category=category,
        cognitive_skills=cognitive_skills,
        question_source="Manual Entry",
        level_of_difficulty=difficulty,
        question=question,
        marks=marks,
        answer_explanation=answer_explanation,
        answers=answers,
        sub_questions=sub_questions,
        display_answer="Yes" if sheet_kind == "descriptive" else "",
        origin="manual",
    )
    db.add(q)
    db.commit()
    db.refresh(q)
    writer.append_questions(db, config.BULK_IMPORT_OUTPUT, [q.id])
    return q

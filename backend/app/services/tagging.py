"""Many-to-many tagging + import preview.

The Bulk Import sheet expresses a many-to-many graph with flat rows: the *same*
identity (``question_label`` for an assessment, ``concept_title`` for a concept)
repeated under a different ancestor path is read by the CMS as a **tag**, not a
duplicate. This module manages those extra placements in the normalized model
and predicts, for any export, what the CMS will do with each row:

  * ADD  — brand-new identity (first time it appears anywhere)
  * TAG  — known identity under a *new* placement (a many-to-many association)
  * SKIP — exact (identity, placement) already present (CMS skips + errors)
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from .. import config, models
from ..bulk_import import writer


# --------------------------------------------------------------------------- #
# Creating tags (extra placements)
# --------------------------------------------------------------------------- #

def _group_of_type(db: Session, concept: models.Concept, group_type: str) -> models.Group:
    """Find or create the group of ``group_type`` under ``concept``."""
    for g in concept.groups:
        if g.group_type == group_type:
            return g
    group = models.Group(
        concept_id=concept.id, group_type=group_type,
        group_name=f"{concept.concept_title} — {group_type}",
        group_display_name=f"{concept.concept_title} — {group_type}",
        group_status="Active",
    )
    db.add(group)
    db.flush()
    return group


def tag_question_to_group(db: Session, question_id: int, group_id: int) -> dict:
    """Tag an assessment into another group (same question_label, new placement)."""
    question = db.get(models.Question, question_id)
    group = db.get(models.Group, group_id)
    if not question or not group:
        raise ValueError("question or group not found")
    if group.id == question.group_id:
        return {"status": "noop", "reason": "already the authoring home placement"}
    existing = next((t for t in question.tags if t.group_id == group.id), None)
    if existing:
        return {"status": "noop", "reason": "tag already exists"}
    db.add(models.QuestionTag(question_id=question.id, group_id=group.id))
    db.commit()
    return {
        "status": "tagged", "question_id": question.id, "group_id": group.id,
        "question_label": question.question_label,
        "concept_title": group.concept.concept_title,
    }


def tag_question_to_concept(db: Session, question_id: int, concept_id: int) -> dict:
    """Tag an assessment under another concept (resolves the matching group)."""
    question = db.get(models.Question, question_id)
    concept = db.get(models.Concept, concept_id)
    if not question or not concept:
        raise ValueError("question or concept not found")
    group_type = question.group.group_type if question.group else "Basic"
    group = _group_of_type(db, concept, group_type)
    return tag_question_to_group(db, question_id, group.id)


def tag_concept_to_topic(db: Session, concept_id: int, topic_id: int) -> dict:
    """Tag a concept under another topic/chapter (same concept_title, new placement)."""
    concept = db.get(models.Concept, concept_id)
    topic = db.get(models.Topic, topic_id)
    if not concept or not topic:
        raise ValueError("concept or topic not found")
    if topic.id == concept.topic_id:
        return {"status": "noop", "reason": "already the authoring home placement"}
    existing = next((t for t in concept.tags if t.topic_id == topic.id), None)
    if existing:
        return {"status": "noop", "reason": "tag already exists"}
    db.add(models.ConceptTag(concept_id=concept.id, topic_id=topic.id))
    db.commit()
    return {
        "status": "tagged", "concept_id": concept.id, "topic_id": topic.id,
        "concept_title": concept.concept_title,
        "chapter_title": topic.chapter.chapter_title,
        "topic_title": topic.topic_title,
    }


# --------------------------------------------------------------------------- #
# Import preview (ADD / TAG / SKIP)
# --------------------------------------------------------------------------- #

def _classify_question(q: models.Question, index: writer.WorkbookIndex) -> list[dict]:
    rows: list[dict] = []
    for group in writer._question_placements(q):
        key = writer.question_placement_key(q.question_label, group)
        if key in index.q_placements:
            outcome = "SKIP"
        elif q.question_label in index.labels:
            outcome = "TAG"
        else:
            outcome = "ADD"
        index.q_placements.add(key)
        index.labels.add(q.question_label)
        rows.append({
            "kind": "assessment",
            "outcome": outcome,
            "identity": q.question_label,
            "sheet": q.sheet_kind,
            "placement": {
                "chapter": group.concept.topic.chapter.chapter_title,
                "topic": group.concept.topic.topic_title,
                "concept": group.concept.concept_title,
                "group_type": group.group_type,
            },
        })
    return rows


def _classify_concept(c: models.Concept, index: writer.WorkbookIndex) -> list[dict]:
    rows: list[dict] = []
    for topic in writer._concept_placements(c):
        key = writer.concept_placement_key(c, topic)
        if key in index.c_placements:
            outcome = "SKIP"
        elif c.concept_title in index.concept_titles:
            outcome = "TAG"
        else:
            outcome = "ADD"
        index.c_placements.add(key)
        index.concept_titles.add(c.concept_title)
        rows.append({
            "kind": "concept",
            "outcome": outcome,
            "identity": c.concept_title,
            "placement": {
                "chapter": topic.chapter.chapter_title,
                "topic": topic.topic_title,
            },
        })
    return rows


def preview(
    db: Session,
    *,
    question_ids: list[int] | None = None,
    concept_ids: list[int] | None = None,
    path: Path | None = None,
) -> dict:
    """Predict the CMS outcome (ADD/TAG/SKIP) for each row an export would emit."""
    path = path or config.BULK_IMPORT_OUTPUT
    index = writer.scan_workbook(path)
    rows: list[dict] = []
    if question_ids:
        for q in (
            db.query(models.Question).filter(models.Question.id.in_(question_ids))
            .order_by(models.Question.id).all()
        ):
            rows.extend(_classify_question(q, index))
    if concept_ids:
        for c in (
            db.query(models.Concept).filter(models.Concept.id.in_(concept_ids))
            .order_by(models.Concept.id).all()
        ):
            rows.extend(_classify_concept(c, index))
    summary = {"ADD": 0, "TAG": 0, "SKIP": 0}
    for r in rows:
        summary[r["outcome"]] += 1
    return {"rows": rows, "summary": summary, "workbook": str(path)}

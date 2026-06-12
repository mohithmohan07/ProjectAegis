"""Post-generation pipeline.

Runs automatically after Build Assessments and Build Concepts. Mirrors the
deck's four steps:

  1. Questions generated   (done by services.generation, persisted as rows)
  2. Assessment tagging    (cluster questions into groups + group_description)
  3. Column mapping        (fill remaining Bulk Import columns with defaults)
  4. Append to sheet       (append-only write to the Bulk Import output workbook)

The vendored ``assessment_tagging`` Apps Script is the live reference for step 2;
the dry implementation here clusters by concept + cognitive skill.
"""
from __future__ import annotations

from collections import defaultdict

from sqlalchemy.orm import Session

from .. import bulk_import as bi
from .. import config, models
from ..bulk_import import writer


def _cluster_key(q: models.Question) -> tuple:
    return (q.group_id, q.cognitive_skills or "Understand")


def assessment_tagging(db: Session, questions: list[models.Question]) -> dict:
    """Step 2: cluster similar questions and (re)build their group descriptions.

    Questions are already attached to a Group (one per concept x level). Tagging
    here ensures every touched group has a meaningful, append-built description
    summarizing the assessments it now contains.
    """
    touched_groups: dict[int, models.Group] = {}
    clusters: dict[tuple, list[models.Question]] = defaultdict(list)
    for q in questions:
        clusters[_cluster_key(q)].append(q)
        touched_groups[q.group_id] = q.group

    for group in touched_groups.values():
        labels = [qq.question_label for qq in group.questions if qq.question_label]
        summary = (
            f"{group.group_type} assessments for "
            f"'{group.concept.concept_title}' — {len(group.questions)} question(s) "
            f"covering {', '.join(sorted({qq.cognitive_skills for qq in group.questions if qq.cognitive_skills}))}."
        )
        # Append-style: keep any existing description, add the refreshed summary.
        if group.group_description and summary not in group.group_description:
            group.group_description = f"{group.group_description}\n{summary}"
        else:
            group.group_description = summary
        if not group.group_display_name:
            group.group_display_name = f"{group.concept.concept_title} — {group.group_type}"
        # group_name carries the assessment-label cluster (newline-separated).
        if labels:
            group.group_name = group.group_name or labels[0]
    db.commit()
    return {"clusters": len(clusters), "groups_tagged": len(touched_groups)}


def column_mapping(db: Session, questions: list[models.Question]) -> int:
    """Step 3: fill remaining canonical columns with consistent defaults."""
    filled = 0
    for q in questions:
        changed = False
        if not q.question_source:
            q.question_source = bi.QUESTION_SOURCE_DEFAULT
            changed = True
        appears = bi.normalize_appears_in(q.question_appears_in) or bi.APPEARS_IN_ALL
        if appears != q.question_appears_in:
            q.question_appears_in = appears
            changed = True
        if not q.question_duration:
            q.question_duration = max(q.marks, 1.0)
            changed = True
        skills = bi.normalize_cognitive_skills(q.cognitive_skills) or "Understand"
        if skills != q.cognitive_skills:
            q.cognitive_skills = skills
            changed = True
        if not q.level_of_difficulty:
            q.level_of_difficulty = "Moderate"
            changed = True
        # question_text: never blank when the question has content; backfill
        # with the plain-text question (the AI evaluator's context field).
        if not q.question_text and q.question:
            q.question_text = bi.to_plain_text(q.question)
            changed = True
        if q.sheet_kind in {"subjective", "descriptive"} and not q.math_keyboard:
            q.math_keyboard = "Yes" if q.group.concept.topic.chapter.subject in {
                "Mathematics", "Physics", "Chemistry"} else "No"
            changed = True
        filled += int(changed)
    db.commit()
    return filled


def append_to_sheet(db: Session, question_ids: list[int]) -> dict:
    """Step 4: append-only write to the Bulk Import output workbook."""
    return writer.append_questions(db, config.BULK_IMPORT_OUTPUT, question_ids)


def run(db: Session, question_ids: list[int]) -> dict:
    """Run the full post-generation pipeline over freshly created questions."""
    questions = (
        db.query(models.Question).filter(models.Question.id.in_(question_ids)).all()
    )
    tagging = assessment_tagging(db, questions)
    mapped = column_mapping(db, questions)
    appended = append_to_sheet(db, question_ids)
    return {
        "questions": len(questions),
        "tagging": tagging,
        "columns_filled": mapped,
        "appended": appended,
        "output_workbook": str(config.BULK_IMPORT_OUTPUT),
    }

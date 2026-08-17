"""Post-generation pipeline.

Runs automatically after Build Assessments and Build Concepts. Mirrors the
deck's persistence steps:

  1. Questions generated   (done by services.generation, persisted as rows)
  2. Group-name projection (synchronise the Q12-friendly visible names)
  3. Column mapping        (fill remaining Bulk Import columns with defaults)
  4. Append to sheet       (append-only write to the Bulk Import output workbook)

The old deterministic assessment-tagging implementation is retired: this
compatibility path neither clusters questions nor authors group descriptions.
The MES grouping engine (``assessment_grouping.py``) owns those verdicts.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from .. import bulk_import as bi
from .. import config, models
from ..bulk_import import writer
from . import assessment_grouping


def assessment_tagging(db: Session, questions: list[models.Question]) -> dict:
    """Step 2 compatibility seam: synchronise visible group names only.

    Semantic grouping and description authorship were retired from this legacy
    path. Existing descriptions remain intact; the recorded MES verdict path is
    their sole author.
    """
    touched_groups: dict[int, models.Group] = {}
    for q in questions:
        touched_groups[q.group_id] = q.group

    for group in touched_groups.values():
        # Some legacy rows predate the dedicated identity column.  Preserve
        # their existing placement identity before Q12 replaces the visible
        # field; later calls then replay against the same internal key.
        if not str(group.group_key or "").strip():
            group.group_key = str(group.group_name or "")
        visible_name = assessment_grouping.friendly_group_name(
            group.concept.concept_display_name, group.group_type)
        group.group_name = visible_name
        group.group_display_name = visible_name
    db.commit()
    return {"clusters": 0, "groups_tagged": len(touched_groups)}


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

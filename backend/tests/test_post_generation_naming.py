"""Q12 compatibility regressions for the legacy post-generation seam."""
from __future__ import annotations

import uuid

from app import models
from app.bulk_import import writer
from app.services import post_generation


def test_post_generation_only_synchronises_friendly_group_names(db, tmp_path):
    concept = db.query(models.Concept).order_by(models.Concept.id).first()
    assert concept is not None
    token = uuid.uuid4().hex
    group = models.Group(
        concept_id=concept.id,
        group_type="Intermediate",
        group_key=f"(POSTGEN-{token}) IG01",
        group_sequence=1,
        group_name=f"QUESTION-LABEL-{token}",
        group_display_name="Stale machine-facing name",
        group_description="Recorded model-authored group description.",
        group_status="Active",
    )
    question = models.Question(
        group=group,
        sheet_kind="subjective",
        question_label=f"QUESTION-LABEL-{token}",
        question="Explain the recorded capability.",
        question_text="Explain the recorded capability.",
        cognitive_skills="Understand",
        marks=1.0,
        origin="concept_mapping",
    )
    db.add(question)
    db.commit()

    result = post_generation.assessment_tagging(db, [question])

    concept_name = concept.concept_display_name
    assert result == {"clusters": 0, "groups_tagged": 1}
    assert group.group_name == group.group_display_name == (
        f"{concept_name} — Intermediate")
    assert group.group_key == f"(POSTGEN-{token}) IG01"
    assert group.group_name != question.question_label
    assert group.group_description == (
        "Recorded model-authored group description.")

    output = tmp_path / "legacy-output.xlsx"
    first = writer.append_questions(db, output, [question.id])
    replay = writer.append_questions(db, output, [question.id])
    assert first["subjective"] == 1
    assert replay["skipped"] == 1
    assert replay["objective"] == replay["subjective"] == 0

    db.delete(question)
    db.delete(group)
    db.commit()

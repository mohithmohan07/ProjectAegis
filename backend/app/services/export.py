"""Export questions to a 3-sheet bulk-upload-ready Excel workbook."""
from __future__ import annotations

import io
from pathlib import Path

import pandas as pd
from sqlalchemy.orm import Session

from .. import models

OBJECTIVE_COLS = [
    "Question Label", "Question Category", "Cognitive Skills", "Question Source",
    "Question Appears in", "Level of Difficulty", "Question", "Marks",
    "Answer Type1", "Answer Content1", "Correct Answer1", "Answer Weightage1",
    "Answer Type2", "Answer Content2", "Correct Answer2", "Answer Weightage2",
    "Answer Type3", "Answer Content3", "Correct Answer3", "Answer Weightage3",
    "Answer Type4", "Answer Content4", "Correct Answer4", "Answer Weightage4",
    "Answer Explanation",
]

SUBJECTIVE_COLS = [
    "Question Label", "Question Category", "Cognitive Skills", "Question Source",
    "Question Appears in", "Level of Difficulty", "Question", "Marks",
    "Answer Type", "Answer Weightage", "Answer Content", "Answer Explanation",
    "Display Answer",
]


def _objective_row(q: models.Question) -> dict:
    row = {c: "" for c in OBJECTIVE_COLS}
    row.update({
        "Question Label": q.question_label or f"AEG-Q-{q.id:05d}",
        "Question Category": q.question_category,
        "Cognitive Skills": q.cognitive_skills,
        "Question Source": q.question_source,
        "Question Appears in": q.question_appears_in,
        "Level of Difficulty": q.level_of_difficulty,
        "Question": q.question,
        "Marks": q.marks,
        "Answer Explanation": q.answer_explanation,
    })
    for i, ans in enumerate(q.answers or [], start=1):
        if i > 4:
            break
        row[f"Answer Type{i}"] = ans.get("answer_type", "Phrases")
        row[f"Answer Content{i}"] = ans.get("answer_content", "")
        row[f"Correct Answer{i}"] = "TRUE" if ans.get("correct_answer") else "FALSE"
        row[f"Answer Weightage{i}"] = ans.get("answer_weightage", 0)
    return row


def _subjective_row(q: models.Question) -> dict:
    answer = (q.answers or [{}])[0]
    return {
        "Question Label": q.question_label or f"AEG-Q-{q.id:05d}",
        "Question Category": q.question_category,
        "Cognitive Skills": q.cognitive_skills,
        "Question Source": q.question_source,
        "Question Appears in": q.question_appears_in,
        "Level of Difficulty": q.level_of_difficulty,
        "Question": q.question,
        "Marks": q.marks,
        "Answer Type": answer.get("answer_type", "Phrases"),
        "Answer Weightage": answer.get("answer_weightage", q.marks),
        "Answer Content": answer.get("answer_content", q.rubric or ""),
        "Answer Explanation": q.answer_explanation,
        "Display Answer": q.display_answer,
    }


def export_workbook(db: Session, dest: Path | None = None) -> bytes:
    questions = db.query(models.Question).all()
    objective = pd.DataFrame([_objective_row(q) for q in questions if q.sheet_kind == "objective"], columns=OBJECTIVE_COLS)
    subjective = pd.DataFrame([_subjective_row(q) for q in questions if q.sheet_kind == "subjective"], columns=SUBJECTIVE_COLS)
    descriptive = pd.DataFrame([_subjective_row(q) for q in questions if q.sheet_kind == "descriptive"], columns=SUBJECTIVE_COLS)

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        objective.to_excel(w, index=False, sheet_name="Objective")
        subjective.to_excel(w, index=False, sheet_name="Subjective")
        descriptive.to_excel(w, index=False, sheet_name="Descriptive")
    data = buf.getvalue()
    if dest:
        dest.write_bytes(data)
    return data

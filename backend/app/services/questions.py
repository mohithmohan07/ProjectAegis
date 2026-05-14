"""Read questions from the canonical bulk-upload Excel and persist to SQLite."""
from __future__ import annotations

from pathlib import Path
import pandas as pd
from sqlalchemy.orm import Session

from .. import models

SHEET_KIND_BY_NAME = {"Objective": "objective", "Subjective": "subjective", "Descriptive": "descriptive"}


def _to_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _build_objective_answers(row: pd.Series) -> list[dict]:
    answers: list[dict] = []
    for i in range(1, 7):
        content = str(row.get(f"Answer Content{i}", "") or "").strip()
        if not content:
            continue
        correct_raw = str(row.get(f"Correct Answer{i}", "")).strip().lower()
        answers.append({
            "answer_type": str(row.get(f"Answer Type{i}", "Phrases") or "Phrases"),
            "answer_content": content,
            "correct_answer": correct_raw in {"true", "1", "yes", "y"},
            "answer_weightage": _to_float(row.get(f"Answer Weightage{i}", 0)),
        })
    return answers


def _build_subjective_answer(row: pd.Series) -> list[dict]:
    content = str(row.get("Answer Content", "") or "").strip()
    if not content:
        return []
    return [{
        "answer_type": str(row.get("Answer Type", "Phrases") or "Phrases"),
        "answer_content": content,
        "correct_answer": True,
        "answer_weightage": _to_float(row.get("Answer Weightage", 0)),
    }]


def import_excel(db: Session, path: Path) -> dict[str, int]:
    counts = {"objective": 0, "subjective": 0, "descriptive": 0}
    book = pd.read_excel(path, sheet_name=None)  # all sheets
    for sheet_name, df in book.items():
        kind = SHEET_KIND_BY_NAME.get(sheet_name)
        if kind is None:
            continue
        df = df.fillna("")
        for _, row in df.iterrows():
            stem = str(row.get("Question", "") or "").strip()
            if not stem:
                continue
            answers = _build_objective_answers(row) if kind == "objective" else _build_subjective_answer(row)
            db.add(models.Question(
                question_label=str(row.get("Question Label", "") or "") or None,
                sheet_kind=kind,
                question_category=str(row.get("Question Category", "") or ""),
                cognitive_skills=str(row.get("Cognitive Skills", "") or ""),
                question_source=str(row.get("Question Source", "") or ""),
                question_appears_in=str(row.get("Question Appears in", "") or ""),
                level_of_difficulty=str(row.get("Level of Difficulty", "") or ""),
                question=stem,
                marks=_to_float(row.get("Marks", 0)),
                answers=answers,
                answer_explanation=str(row.get("Answer Explanation", "") or ""),
                display_answer=str(row.get("Display Answer", "") or ""),
            ))
            counts[kind] += 1
    db.commit()
    return counts


def list_questions(
    db: Session,
    *,
    sheet_kind: str | None = None,
    difficulty: str | None = None,
    cognitive: str | None = None,
    limit: int = 200,
) -> list[models.Question]:
    q = db.query(models.Question)
    if sheet_kind:
        q = q.filter(models.Question.sheet_kind == sheet_kind)
    if difficulty:
        q = q.filter(models.Question.level_of_difficulty == difficulty)
    if cognitive:
        q = q.filter(models.Question.cognitive_skills == cognitive)
    return q.order_by(models.Question.id.desc()).limit(limit).all()

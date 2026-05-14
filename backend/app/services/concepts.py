"""Read concepts from the canonical Excel and persist to SQLite."""
from __future__ import annotations

from pathlib import Path
import pandas as pd
from sqlalchemy.orm import Session

from .. import models

CANONICAL_COLUMNS = {
    "Board": "board",
    "Book": "book",
    "Grade": "grade",
    "Subject": "subject",
    "Chapter No": "chapter_no",
    "Chapter Code": "chapter_code",
    "Chapter Title": "chapter_title",
    "Topic": "topic",
    "Parent Concept": "parent_concept",
    "Concept": "concept",
    "Concept Description": "concept_description",
    "Concept ID": "concept_id",
    "MMD Path": "mmd_path",
    "PDF Path": "pdf_path",
}


def import_excel(db: Session, path: Path, *, is_pre_learning: bool = False, sheet: str = "Concepts") -> int:
    df = pd.read_excel(path, sheet_name=sheet)
    return import_dataframe(db, df, is_pre_learning=is_pre_learning)


def import_dataframe(db: Session, df: pd.DataFrame, *, is_pre_learning: bool = False) -> int:
    df = df.fillna("")
    inserted = 0
    for _, row in df.iterrows():
        kwargs = {dest: str(row.get(src, "")) for src, dest in CANONICAL_COLUMNS.items() if src in df.columns}
        kwargs["is_pre_learning"] = 1 if is_pre_learning else 0
        db.add(models.Concept(**kwargs))
        inserted += 1
    db.commit()
    return inserted


def list_concepts(
    db: Session,
    *,
    pre_learning: bool | None = None,
    chapter_code: str | None = None,
    subject: str | None = None,
    limit: int = 200,
) -> list[models.Concept]:
    q = db.query(models.Concept)
    if pre_learning is True:
        q = q.filter(models.Concept.is_pre_learning == 1)
    elif pre_learning is False:
        q = q.filter(models.Concept.is_pre_learning == 0)
    if chapter_code:
        q = q.filter(models.Concept.chapter_code == chapter_code)
    if subject:
        q = q.filter(models.Concept.subject == subject)
    return q.order_by(models.Concept.id).limit(limit).all()


def chapters(db: Session) -> list[dict]:
    rows = (
        db.query(
            models.Concept.chapter_code,
            models.Concept.chapter_title,
            models.Concept.subject,
            models.Concept.grade,
            models.Concept.board,
        )
        .distinct()
        .all()
    )
    return [
        {"chapter_code": r[0], "chapter_title": r[1], "subject": r[2], "grade": r[3], "board": r[4]}
        for r in rows
        if r[0]
    ]

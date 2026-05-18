"""Manual concept / question creation endpoints (form-driven, no upload)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import schemas
from ..db import get_db
from ..services import manual

router = APIRouter(prefix="/manual", tags=["manual"])


class CreateConceptRequest(BaseModel):
    board: str
    grade: str
    subject: str
    chapter_title: str
    topic_title: str = "Topic 01"
    concept_title: str
    summary: str = ""
    formula: str | None = None
    keywords: str = ""


class CreateQuestionRequest(BaseModel):
    concept_id: int
    sheet_kind: str
    category: str
    cognitive_skills: str = "Understanding"
    difficulty: str = "Moderate"
    marks: float = 1.0
    question: str
    answer_explanation: str = ""
    answers: list[dict] = Field(default_factory=list)
    sub_questions: list[dict] = Field(default_factory=list)


@router.post("/concepts", response_model=schemas.ConceptOut)
def create_concept(req: CreateConceptRequest, db: Session = Depends(get_db)):
    if not req.concept_title.strip():
        raise HTTPException(400, "concept_title is required")
    if not req.chapter_title.strip():
        raise HTTPException(400, "chapter_title is required")
    concept = manual.create_concept(
        db,
        board=req.board, grade=req.grade, subject=req.subject,
        chapter_title=req.chapter_title.strip(),
        topic_title=req.topic_title.strip() or "Topic 01",
        concept_title=req.concept_title.strip(),
        summary=req.summary,
        formula=(req.formula or "").strip() or None,
        keywords=req.keywords,
    )
    return concept


@router.post("/questions", response_model=schemas.QuestionOut)
def create_question(req: CreateQuestionRequest, db: Session = Depends(get_db)):
    if req.sheet_kind not in {"objective", "subjective", "descriptive"}:
        raise HTTPException(400, "sheet_kind must be objective | subjective | descriptive")
    if not req.question.strip():
        raise HTTPException(400, "question is required")
    try:
        q = manual.create_question(
            db,
            concept_id=req.concept_id,
            sheet_kind=req.sheet_kind,
            category=req.category,
            cognitive_skills=req.cognitive_skills,
            difficulty=req.difficulty,
            marks=req.marks,
            question=req.question,
            answer_explanation=req.answer_explanation,
            answers=req.answers,
            sub_questions=req.sub_questions,
        )
    except ValueError as e:
        raise HTTPException(404, str(e))
    return q

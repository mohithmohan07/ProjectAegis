from datetime import datetime
from sqlalchemy import String, Integer, Text, ForeignKey, DateTime, JSON, Float
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class Concept(Base):
    """Canonical concept row produced by the MMD->Concepts stage.

    Columns mirror the user's existing Excel schema (`mmd_to_concepts_excel.py`):
    Board, Book, Grade, Subject, Chapter No, Chapter Code, Chapter Title,
    Topic, Parent Concept, Concept, Concept Description, Concept ID,
    MMD Path, PDF Path.
    """

    __tablename__ = "concepts"

    id: Mapped[int] = mapped_column(primary_key=True)
    concept_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    board: Mapped[str] = mapped_column(String(16), default="")
    book: Mapped[str] = mapped_column(String(16), default="")
    grade: Mapped[str] = mapped_column(String(8), default="")
    subject: Mapped[str] = mapped_column(String(64), default="")
    chapter_no: Mapped[str] = mapped_column(String(16), default="")
    chapter_code: Mapped[str] = mapped_column(String(64), default="", index=True)
    chapter_title: Mapped[str] = mapped_column(String(255), default="")
    topic: Mapped[str] = mapped_column(String(255), default="")
    parent_concept: Mapped[str] = mapped_column(String(255), default="")
    concept: Mapped[str] = mapped_column(String(255), default="")
    concept_description: Mapped[str] = mapped_column(Text, default="")
    mmd_path: Mapped[str] = mapped_column(String(512), default="")
    pdf_path: Mapped[str] = mapped_column(String(512), default="")
    is_pre_learning: Mapped[int] = mapped_column(Integer, default=0)  # 0 = native, 1 = pre-learning derived
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Question(Base):
    """Canonical question row produced by the bulk-upload stage.

    Sheet kind ('objective' | 'subjective' | 'descriptive') matches the user's
    existing 3-sheet output convention. Categories, cognitive skills, and
    difficulty levels follow the controlled vocabularies declared in the
    bulk-upload scripts.
    """

    __tablename__ = "questions"

    id: Mapped[int] = mapped_column(primary_key=True)
    question_label: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    sheet_kind: Mapped[str] = mapped_column(String(16), default="objective")
    question_category: Mapped[str] = mapped_column(String(64), default="")
    cognitive_skills: Mapped[str] = mapped_column(String(64), default="")
    question_source: Mapped[str] = mapped_column(String(255), default="")
    question_appears_in: Mapped[str] = mapped_column(String(255), default="")
    level_of_difficulty: Mapped[str] = mapped_column(String(16), default="")
    question: Mapped[str] = mapped_column(Text)
    marks: Mapped[float] = mapped_column(Float, default=0)
    answers: Mapped[list] = mapped_column(JSON, default=list)
    answer_explanation: Mapped[str] = mapped_column(Text, default="")
    display_answer: Mapped[str] = mapped_column(Text, default="")
    rubric: Mapped[str] = mapped_column(Text, default="")
    concept_id: Mapped[int | None] = mapped_column(ForeignKey("concepts.id"), nullable=True)
    assessment_label: Mapped[str] = mapped_column(String(255), default="")
    tagging_notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    concept = relationship("Concept")


class PipelineRun(Base):
    """One execution of a pipeline stage, including dry-runs."""

    __tablename__ = "pipeline_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    stage: Mapped[str] = mapped_column(String(64), index=True)
    mode: Mapped[str] = mapped_column(String(16), default="dry")  # dry | live
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|running|succeeded|failed
    phase: Mapped[str] = mapped_column(String(64), default="")
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    detail: Mapped[str] = mapped_column(Text, default="")
    inputs: Mapped[dict] = mapped_column(JSON, default=dict)
    outputs: Mapped[dict] = mapped_column(JSON, default=dict)
    artifact_path: Mapped[str] = mapped_column(String(512), default="")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error: Mapped[str] = mapped_column(Text, default="")

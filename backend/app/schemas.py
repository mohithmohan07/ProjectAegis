from datetime import datetime
from typing import Any
from pydantic import BaseModel, Field, ConfigDict


# ---------- Concept ----------

class ConceptBase(BaseModel):
    concept_id: str | None = None
    board: str = ""
    book: str = ""
    grade: str = ""
    subject: str = ""
    chapter_no: str = ""
    chapter_code: str = ""
    chapter_title: str = ""
    topic: str = ""
    parent_concept: str = ""
    concept: str = ""
    concept_description: str = ""
    mmd_path: str = ""
    pdf_path: str = ""
    is_pre_learning: int = 0


class ConceptOut(ConceptBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime


class ConceptIn(ConceptBase):
    pass


# ---------- Question ----------

QUESTION_CATEGORIES = [
    "Multiple Choice Question", "Assertion & Reasons", "True/False", "Fill in the Blanks",
    "Very Short Answer", "Short Answer (2 marks)", "Short Answer (3 marks)",
    "Long Answer (4 marks)", "Long Answer (5 marks)", "Long Answer (6 marks)",
    "Case-Based", "Passage-based", "Extract-based", "Map-based",
    "Sentence Transformation", "Error Correction", "Composition Writing",
]
COGNITIVE_SKILLS = ["Remembering", "Understanding", "Applying", "Analysing", "Evaluating", "Creating"]
DIFFICULTY_LEVELS = ["Less", "Moderate", "High"]
SHEET_KINDS = ["objective", "subjective", "descriptive"]


class AnswerOption(BaseModel):
    answer_type: str = "Phrases"
    answer_content: str = ""
    correct_answer: bool = False
    answer_weightage: float = 0.0


class QuestionBase(BaseModel):
    question_label: str | None = None
    sheet_kind: str = "objective"
    question_category: str = ""
    cognitive_skills: str = ""
    question_source: str = ""
    question_appears_in: str = ""
    level_of_difficulty: str = ""
    question: str
    marks: float = 0.0
    answers: list[AnswerOption] = Field(default_factory=list)
    answer_explanation: str = ""
    display_answer: str = ""
    rubric: str = ""
    concept_id: int | None = None
    assessment_label: str = ""
    tagging_notes: str = ""


class QuestionOut(QuestionBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime


class QuestionIn(QuestionBase):
    pass


# ---------- Pipeline ----------

class StageDescriptor(BaseModel):
    key: str
    title: str
    order: int
    description: str
    inputs: list[str]
    outputs: list[str]
    dependencies: list[str]
    requires_keys: list[str]
    available: bool  # true if live mode is possible (keys present)


class StageRunRequest(BaseModel):
    mode: str = "dry"  # dry | live
    inputs: dict[str, Any] = Field(default_factory=dict)


class PipelineRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    stage: str
    mode: str
    status: str
    phase: str
    progress: float
    detail: str
    inputs: dict
    outputs: dict
    artifact_path: str
    started_at: datetime
    finished_at: datetime | None
    error: str


# ---------- Misc ----------

class TagSuggestion(BaseModel):
    concept_id: int | None
    concept_path: str
    cognitive_skills: str
    level_of_difficulty: str
    confidence: float


class TagRequest(BaseModel):
    text: str


class StatsOut(BaseModel):
    concepts: int
    pre_learning_concepts: int
    questions: int
    questions_by_sheet: dict[str, int]
    questions_by_difficulty: dict[str, int]
    runs: int
    runs_by_status: dict[str, int]


class IngestPaste(BaseModel):
    title: str
    text: str
    delimiter: str = "\n\n"
    sheet_kind: str = "objective"

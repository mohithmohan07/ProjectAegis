from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field


# --------------------------------------------------------------------------- #
# Directory
# --------------------------------------------------------------------------- #

class ChapterRef(BaseModel):
    id: int
    chapter_code: str
    chapter_title: str
    chapter_display_name: str
    topic_count: int
    concept_count: int


class ConceptOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    concept_title: str
    concept_display_name: str
    parent_concept: str = ""
    concept_details: str
    keywords: str
    sources: str = ""


class QuestionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    group_id: int
    sheet_kind: str
    question_label: str
    question_category: str
    cognitive_skills: str
    question_source: str
    level_of_difficulty: str
    question: str
    question_text: str = ""
    marks: float
    math_keyboard: str
    display_answer: str
    answer_explanation: str
    answers: list
    sub_questions: list
    origin: str
    created_at: datetime


# --------------------------------------------------------------------------- #
# Build Assessments — concept mapping
# --------------------------------------------------------------------------- #

class CreateSessionRequest(BaseModel):
    scope_type: str  # chapter | topic | concept
    scope_ids: list[int]


class BlueprintBatchRequest(BaseModel):
    cognitive_skills: list[str] = Field(default_factory=list)
    difficulty_levels: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    question_type: str = "objective"
    num_questions: int = 1
    appears_in: list[str] = Field(default_factory=list)


class BlueprintBatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    cognitive_skills: list
    difficulty_levels: list
    categories: list
    question_type: str
    num_questions: int
    appears_in: list = Field(default_factory=list)


class SessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    source: str
    scope_type: str
    scope_ids: list
    status: str
    generated_question_ids: list
    batches: list[BlueprintBatchOut]
    created_at: datetime


# --------------------------------------------------------------------------- #
# Uploads (shared by Build Assessments path B and Build Concepts)
# --------------------------------------------------------------------------- #

class UploadJobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    module: str
    upload_type: str
    textbook_mode: str
    learning_kind: str
    source_book: str = ""
    filename: str
    mmd_text: str
    deposit_scope_type: str
    deposit_scope_ids: list
    status: str
    result_ids: list
    detail: str
    source_artifacts: dict = Field(default_factory=dict)
    checkpoint_available: bool = False
    checkpoint_stage: str = ""
    checkpoint_saved_at: str = ""
    checkpoint_progress: float = 0.0
    checkpoint_target_identity: dict = Field(default_factory=dict)
    generation_running: bool = False
    generation_log: list = Field(default_factory=list)
    openai_usage: dict = Field(default_factory=dict)
    created_at: datetime


class ResumableCheckpointSummary(BaseModel):
    id: int
    module: str
    learning_kind: str
    filename: str
    status: str
    checkpoint_available: bool = True
    checkpoint_stage: str = ""
    checkpoint_saved_at: str = ""
    checkpoint_progress: float = 0.0
    checkpoint_target_identity: dict = Field(default_factory=dict)
    generation_running: bool = False
    created_at: datetime


class ResumableCheckpointJobs(BaseModel):
    items: list[ResumableCheckpointSummary] = Field(default_factory=list)
    total: int = 0


class TextbookModeRequest(BaseModel):
    mode: str  # extract | create


class DepositRequest(BaseModel):
    scope_type: str  # chapter | topic | concept
    scope_ids: list[int]


class GenerateFromUploadRequest(BaseModel):
    # "auto" (default) detects each question's type and absorbs a mix of
    # objective / subjective / descriptive; or force a single type.
    question_type: str = "auto"


# --------------------------------------------------------------------------- #
# Build Concepts
# --------------------------------------------------------------------------- #

class PostLearningGenerateRequest(BaseModel):
    target_chapter_id: int


class PreLearningExistingRequest(BaseModel):
    chapter_ids: list[int]
    source_book: str = ""


# --------------------------------------------------------------------------- #
# Tagging (many-to-many) + import preview
# --------------------------------------------------------------------------- #

class TagToConceptRequest(BaseModel):
    concept_id: int


class TagToGroupRequest(BaseModel):
    group_id: int

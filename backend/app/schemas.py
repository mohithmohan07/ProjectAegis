from datetime import datetime
from typing import Literal

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
    awaiting_decision: bool = False
    pending_decision: dict | None = None
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


SemanticDecisionChoice = Literal[
    "expand_existing",
    "create_new",
    "select_existing",
    "accept_recommended",
    "select_candidate",
    "replace_source",
    "consolidate_types",
    "keep_distinct_types",
    "custom_instruction",
]


class SemanticDecisionItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    unit_id: str = Field(default="", max_length=8_000)
    type_id: str = Field(default="", max_length=8_000)
    type_title: str = Field(default="", max_length=8_000)
    qids: list[str] = Field(default_factory=list, max_length=100)
    questions: list[str] = Field(default_factory=list, max_length=100)
    topic: str = Field(default="", max_length=8_000)


class SemanticDecisionCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # ``target_id`` is the generic identity used by source-review and future
    # non-concept decisions. ``concept_id`` remains for backwards-compatible
    # Phase 3.3 host decisions and older clients.
    target_id: str = Field(default="", max_length=512)
    concept_id: str = Field(default="", max_length=256)
    title: str = Field(default="", max_length=8_000)
    topic: str = Field(default="", max_length=8_000)
    coverage: str = Field(default="", max_length=8_000)
    gap: str = Field(default="", max_length=8_000)


class SemanticDecisionEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page: str = Field(default="", max_length=128)
    label: str = Field(default="", max_length=512)
    text: str = Field(default="", max_length=8_000)


class SemanticDecisionOption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    choice: SemanticDecisionChoice
    label: str = Field(min_length=1, max_length=512)
    recommended: bool = False
    target_id: str = Field(default="", max_length=512)
    target_concept_id: str = Field(default="", max_length=256)


class AgentSemanticReview(BaseModel):
    """Durable audit state for one bounded autonomous decision attempt."""

    model_config = ConfigDict(extra="forbid")
    status: Literal[
        "request_started", "resolved", "escalated", "unavailable"
    ]
    resolver_version: str = Field(min_length=1, max_length=128)
    issue_key: str = Field(min_length=64, max_length=64)
    started_at: str = Field(min_length=1, max_length=64)
    completed_at: str = Field(default="", max_length=64)
    reason: str = Field(default="", max_length=8_000)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence_refs: list[str] = Field(default_factory=list, max_length=100)
    choice: SemanticDecisionChoice | None = None
    instruction: str = Field(default="", max_length=4_000)
    target_id: str = Field(default="", max_length=512)
    target_concept_id: str = Field(default="", max_length=256)


class PendingSemanticDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision_id: str = Field(min_length=1, max_length=128)
    context_hash: str = Field(min_length=64, max_length=64)
    kind: str = Field(min_length=1, max_length=128)
    phase: str = Field(min_length=1, max_length=128)
    conflict: str = Field(min_length=1, max_length=8_000)
    diagnosis: str = Field(default="", max_length=8_000)
    decision_question: str = Field(default="", max_length=8_000)
    checkpoint_progress: float = Field(default=0.0, ge=0.0, le=1.0)
    item: SemanticDecisionItem = Field(default_factory=SemanticDecisionItem)
    candidates: list[SemanticDecisionCandidate] = Field(
        default_factory=list, max_length=100)
    evidence: list[SemanticDecisionEvidence] = Field(
        default_factory=list, max_length=100)
    deferred_assignment_unit_ids: list[str] = Field(
        default_factory=list, max_length=5_000)
    options: list[SemanticDecisionOption] = Field(
        default_factory=list, max_length=16)
    cumulative_usage: dict = Field(default_factory=dict)
    agent_review: AgentSemanticReview | None = None


class ResolvedSemanticDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision_id: str = Field(min_length=1, max_length=128)
    context_hash: str = Field(min_length=64, max_length=64)
    choice: SemanticDecisionChoice
    instruction: str = Field(default="", max_length=4_000)
    target_id: str = Field(default="", max_length=512)
    target_concept_id: str = Field(default="", max_length=256)
    resolved_at: str = Field(min_length=1, max_length=64)
    status: Literal["ready", "consumed"] = "ready"
    consumed_at: str = Field(default="", max_length=64)
    resolved_by: Literal["human", "agent"] = "human"
    pending_decision: PendingSemanticDecision


class HumanDecisionCheckpointContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fingerprint: str = Field(min_length=64, max_length=64)
    target_chapter_id: int = Field(gt=0)


class HumanDecisionLedger(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    context: HumanDecisionCheckpointContext
    pending: PendingSemanticDecision | None = None
    deferred_assignment_unit_ids: list[str] = Field(
        default_factory=list, max_length=5_000)
    resolutions: list[ResolvedSemanticDecision] = Field(
        default_factory=list, max_length=5_000)


class HumanSemanticDecisionRequest(BaseModel):
    choice: SemanticDecisionChoice
    instruction: str = Field(default="", max_length=4_000)
    target_id: str = Field(default="", max_length=512)
    target_concept_id: str = Field(default="", max_length=256)


class HumanSemanticDecisionResponse(BaseModel):
    job_id: int
    status: Literal["decision_recorded"]
    resume_required: bool = True
    resolved_decision: dict = Field(default_factory=dict)


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


class TagToTopicRequest(BaseModel):
    topic_id: int


class PreviewRequest(BaseModel):
    question_ids: list[int] = Field(default_factory=list)
    concept_ids: list[int] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Misc
# --------------------------------------------------------------------------- #

class Vocab(BaseModel):
    boards: list[str]
    grades: list[str]
    question_types: list[str]
    cognitive_skills: list[str]
    difficulty_levels: list[str]
    question_categories: dict[str, list[str]]
    group_types: list[str]
    upload_types: list[str]
    book_sources: list[str]
    appears_in: list[str]


class Stats(BaseModel):
    chapters: int
    topics: int
    concepts: int
    groups: int
    questions: int
    questions_by_sheet: dict[str, int]
    sessions: int
    upload_jobs: int
    openai_live: bool
    mathpix_live: bool

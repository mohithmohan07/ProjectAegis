"""Normalized model of the Bulk Import workbook.

The workbook itself is denormalized (every row repeats chapter/topic/concept/
group context). For browsing and generation the integrated tool needs a real
hierarchy, so on import the flat rows are normalized into:

    Chapter 1--* Topic 1--* Concept 1--* Group 1--* Question

Board / Grade / Subject / Unit are not explicit columns in the workbook; they
are parsed from the chapter & label ID prefixes (e.g. ``10CBMA_...``) by
``services.directory``. Unit is an optional grouping that defaults per chapter.

Round-tripping back to the canonical sheets is handled by ``bulk_import.writer``.
"""
from datetime import datetime

from sqlalchemy import String, Integer, Text, ForeignKey, DateTime, JSON, Float, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class Chapter(Base):
    __tablename__ = "chapters"

    id: Mapped[int] = mapped_column(primary_key=True)
    chapter_code: Mapped[str] = mapped_column(String(64), index=True)
    board: Mapped[str] = mapped_column(String(16), default="")
    grade: Mapped[str] = mapped_column(String(8), default="")
    subject: Mapped[str] = mapped_column(String(64), default="")
    unit: Mapped[str] = mapped_column(String(128), default="General")
    chapter_title: Mapped[str] = mapped_column(String(255), default="")
    chapter_display_name: Mapped[str] = mapped_column(String(255), default="")
    chapter_duration: Mapped[str] = mapped_column(String(32), default="")
    pre_topics: Mapped[str] = mapped_column(Text, default="")
    post_topics: Mapped[str] = mapped_column(Text, default="")
    chapter_description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    topics = relationship("Topic", back_populates="chapter", cascade="all, delete-orphan")


class Topic(Base):
    __tablename__ = "topics"

    id: Mapped[int] = mapped_column(primary_key=True)
    chapter_id: Mapped[int] = mapped_column(ForeignKey("chapters.id"))
    topic_title: Mapped[str] = mapped_column(String(255), default="")
    topic_display_name: Mapped[str] = mapped_column(String(255), default="")
    pre_post_learning: Mapped[str] = mapped_column(String(16), default="Post")
    related_topics: Mapped[str] = mapped_column(Text, default="")
    topic_description: Mapped[str] = mapped_column(Text, default="")
    # Canonical source order for the latest accepted topology of this learning
    # kind. Database ids are creation history and must never determine textbook
    # order (a repaired topic may pre-date topics that now precede it).
    source_order: Mapped[int] = mapped_column(Integer, default=0)
    # The topic's machine identity, MINTED ONCE and never re-derived
    # (spec-step8 T4-1/P-C1). §6:523's "unique and stable forever" is a
    # property of STORAGE: no formula survives the rename/merge/split/re-tag
    # §7:577 permits, and every previous mint recomputed on every read.
    # ``services.identity.machine_id_for_topic`` is its only producer.
    machine_id: Mapped[str] = mapped_column(String(255), default="")

    chapter = relationship("Chapter", back_populates="topics")
    concepts = relationship("Concept", back_populates="topic", cascade="all, delete-orphan")


class Concept(Base):
    __tablename__ = "concepts"

    id: Mapped[int] = mapped_column(primary_key=True)
    topic_id: Mapped[int] = mapped_column(ForeignKey("topics.id"))
    concept_title: Mapped[str] = mapped_column(String(255), default="")
    concept_display_name: Mapped[str] = mapped_column(String(512), default="")
    parent_concept: Mapped[str] = mapped_column(String(255), default="")
    concept_details: Mapped[str] = mapped_column(Text, default="")
    keywords: Mapped[str] = mapped_column(Text, default="")
    digicards: Mapped[str] = mapped_column(Text, default="")
    related_concepts: Mapped[str] = mapped_column(Text, default="")
    # Position inside the latest accepted topic topology. This is deliberately
    # independent of the primary key so refreshed/reused concepts retain the
    # source order of the current successful run.
    source_order: Mapped[int] = mapped_column(Integer, default=0)
    # The concept's machine identity, MINTED ONCE and never re-derived
    # (spec-step8 T4-1/P-C1). It came off the RELEASE HASH before step 8
    # (``assessment_release_snapshot:137``), so editing any staged content
    # re-minted every id and no label could be stable forever.
    # ``services.identity.machine_id_for_concept`` is its only producer, and
    # ``generation.question_label`` is ``f"{machine_id} Q##"`` off it.
    machine_id: Mapped[str] = mapped_column(String(255), default="")
    # Multi-source book tags, "; "-joined (e.g. "NCERT; RD Sharma").
    sources: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    topic = relationship("Topic", back_populates="concepts")
    groups = relationship("Group", back_populates="concept", cascade="all, delete-orphan")
    # Additional (many-to-many) placements: the same concept tagged under other
    # topics/chapters. The concept's `topic_id` above stays its authoring "home";
    # these rows are extra placements that the Bulk Import sheet expresses by
    # repeating the concept row under each parent (see bulk_import.writer).
    tags = relationship("ConceptTag", back_populates="concept", cascade="all, delete-orphan")


class Group(Base):
    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(primary_key=True)
    concept_id: Mapped[int] = mapped_column(ForeignKey("concepts.id"))
    group_type: Mapped[str] = mapped_column(String(16), default="Basic")  # Basic|Intermediate|Advanced
    group_name: Mapped[str] = mapped_column(String(255), default="")
    group_display_name: Mapped[str] = mapped_column(String(255), default="")
    group_description: Mapped[str] = mapped_column(Text, default="")
    group_status: Mapped[str] = mapped_column(String(16), default="Active")
    related_digicards: Mapped[str] = mapped_column(Text, default="")
    # Stable group identity (MES: the exact machine Group ID). Placement keys
    # and tagging round-trips include it so multiple groups of one tier under
    # one concept never collapse. Defaults to group_name on import.
    group_key: Mapped[str] = mapped_column(String(255), default="", index=True)
    # Tier sequence parsed from the machine ID (BG01 -> 1). Mechanical ID
    # bookkeeping only; it never decides which group a question belongs to.
    group_sequence: Mapped[int] = mapped_column(Integer, default=0)

    concept = relationship("Concept", back_populates="groups")
    questions = relationship("Question", back_populates="group", cascade="all, delete-orphan")
    # Reverse side of QuestionTag: questions that are *tagged* into this group
    # (in addition to the questions whose primary home is this group).
    tagged_in = relationship("QuestionTag", back_populates="group", cascade="all, delete-orphan")


class Question(Base):
    __tablename__ = "questions"

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"))
    sheet_kind: Mapped[str] = mapped_column(String(16), default="objective")
    question_label: Mapped[str] = mapped_column(String(128), index=True, default="")
    question_category: Mapped[str] = mapped_column(String(64), default="")
    cognitive_skills: Mapped[str] = mapped_column(String(64), default="")
    question_source: Mapped[str] = mapped_column(String(128), default="")
    question_disclaimer: Mapped[str] = mapped_column(Text, default="")
    question_duration: Mapped[float] = mapped_column(Float, default=1.0)
    math_keyboard: Mapped[str] = mapped_column(String(16), default="")
    question_appears_in: Mapped[str] = mapped_column(
        String(128), default="Pre-test, Post-test, Worksheet, Test")
    level_of_difficulty: Mapped[str] = mapped_column(String(16), default="")
    question: Mapped[str] = mapped_column(Text, default="")
    # Plain-text question + any shared context (passage/conversation/diagram
    # description); passed to the AI evaluator. Never HTML.
    question_text: Mapped[str] = mapped_column(Text, default="")
    marks: Mapped[float] = mapped_column(Float, default=1.0)
    display_answer: Mapped[str] = mapped_column(Text, default="")
    answer_explanation: Mapped[str] = mapped_column(Text, default="")
    # answers: list of dicts; shape depends on sheet_kind (see bulk_import.writer).
    answers: Mapped[list] = mapped_column(JSON, default=list)
    # sub_questions: descriptive only; list of {text, marks, keywords:[{answer_type,weightage,keyword}]}
    sub_questions: Mapped[list] = mapped_column(JSON, default=list)
    # seed|concept_mapping|upload|assessment_release|pre_learning_generated
    # (the last two are minted by the assessment release lane; see
    # assessment_release_service.QUESTION_ORIGINS)
    origin: Mapped[str] = mapped_column(String(32), default="seed")
    # --- MES release identity (spec §5.7). Model-level only for now: the
    # workbook's positional columns stay untouched until the authoritative MES
    # Bulk Import Format template is inspected (spec §12/§23). ---
    # Open|Specific; first-class field, never inferred from answer type/marks.
    answer_restriction: Mapped[str] = mapped_column(String(16), default="")
    source_qid: Mapped[str] = mapped_column(String(64), default="", index=True)
    blueprint_cell_id: Mapped[str] = mapped_column(String(128), default="")
    source_paper_number: Mapped[str] = mapped_column(String(128), default="")
    # OR alternatives share an alternative_set_id; each branch is its own row.
    alternative_set_id: Mapped[str] = mapped_column(String(128), default="")
    parent_question_id: Mapped[int | None] = mapped_column(
        ForeignKey("questions.id"), nullable=True, default=None)
    # Ordered image manifest: [{"url", "alt", "sha256", "order", ...}].
    image_manifest: Mapped[list] = mapped_column(JSON, default=list)
    source_evidence: Mapped[str] = mapped_column(Text, default="")
    assessment_gist: Mapped[str] = mapped_column(Text, default="")
    # Routing/grouping audit written by the release pipeline (author/critic
    # identities, evidence, confidence, flags).
    route_audit: Mapped[dict] = mapped_column(JSON, default=dict)
    validation_status: Mapped[str] = mapped_column(String(32), default="")
    validation_errors: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    group = relationship("Group", back_populates="questions")
    # Additional (many-to-many) placements: the same assessment tagged into
    # other groups/concepts. `group_id` above stays the authoring "home"; these
    # rows are extra placements the Bulk Import sheet expresses by repeating the
    # question row (same question_label) under each parent.
    tags = relationship("QuestionTag", back_populates="question", cascade="all, delete-orphan")


class QuestionTag(Base):
    """An additional placement of a Question into a Group (many-to-many tag).

    The pair (question_id, group_id) is unique. On export, every tag produces
    one extra row carrying the *same* ``question_label`` under the tagged
    group's concept/topic/chapter — exactly the repeated-row convention the CMS
    reads as a tag rather than a duplicate.
    """

    __tablename__ = "question_tags"

    id: Mapped[int] = mapped_column(primary_key=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id"), index=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    question = relationship("Question", back_populates="tags")
    group = relationship("Group", back_populates="tagged_in")

    __table_args__ = (
        UniqueConstraint("question_id", "group_id", name="uq_question_tag"),
    )


class ConceptTag(Base):
    """An additional placement of a Concept under a Topic (many-to-many tag).

    A concept is a free-standing entity keyed by ``concept_title``; it can be
    tagged under any number of topics/chapters. The pair (concept_id, topic_id)
    is unique. On export, every tag produces one extra concept row under the
    tagged topic's chapter.
    """

    __tablename__ = "concept_tags"

    id: Mapped[int] = mapped_column(primary_key=True)
    concept_id: Mapped[int] = mapped_column(ForeignKey("concepts.id"), index=True)
    topic_id: Mapped[int] = mapped_column(ForeignKey("topics.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    concept = relationship("Concept", back_populates="tags")
    topic = relationship("Topic")

    __table_args__ = (
        UniqueConstraint("concept_id", "topic_id", name="uq_concept_tag"),
    )


class BlueprintBatch(Base):
    """One stackable blueprint row inside a Build-Assessments (concept-mapping) session.

    Multiple batches can be configured before a single Generate action; each
    batch produces ``num_questions`` questions for every (skill x difficulty x
    category) combination it declares.
    """

    __tablename__ = "blueprint_batches"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("assessment_sessions.id"))
    cognitive_skills: Mapped[list] = mapped_column(JSON, default=list)
    difficulty_levels: Mapped[list] = mapped_column(JSON, default=list)
    categories: Mapped[list] = mapped_column(JSON, default=list)
    question_type: Mapped[str] = mapped_column(String(16), default="objective")
    num_questions: Mapped[int] = mapped_column(Integer, default=1)
    # Assessment purpose (Appears In): Pre-test / Post-test / Worksheet / Test.
    appears_in: Mapped[list] = mapped_column(JSON, default=list)

    session = relationship("AssessmentSession", back_populates="batches")


class AssessmentSession(Base):
    """A Build-Assessments (concept-mapping) session: a scope + stacked blueprints."""

    __tablename__ = "assessment_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Stable Google ``sub`` (or the explicit offline principal) that owns this
    # session. Email addresses are mutable and must not be authorization keys.
    owner_sub: Mapped[str] = mapped_column(
        String(255), default="local:default", index=True)
    source: Mapped[str] = mapped_column(String(32), default="concept_mapping")  # concept_mapping|upload
    scope_type: Mapped[str] = mapped_column(String(16), default="chapter")  # chapter|topic|concept
    scope_ids: Mapped[list] = mapped_column(JSON, default=list)  # ids of chapters/topics/concepts
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft|generated
    generated_question_ids: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    batches = relationship("BlueprintBatch", back_populates="session", cascade="all, delete-orphan")


# The stable curriculum-destination vocabulary a resumable checkpoint
# carries. One tuple: the checkpoint_target_identity property and every
# consumer that projects an identity for a decision key read THIS, so
# adding a field cannot silently diverge the two.
CHECKPOINT_TARGET_IDENTITY_FIELDS = (
    "board", "grade", "subject", "unit", "chapter_title", "chapter_code",
)


class UploadJob(Base):
    """An upload-based job: a document converted to MMD, then deposited/generated."""

    __tablename__ = "upload_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Stable Google ``sub`` (or the explicit offline principal) that owns this
    # upload. Never use mutable email addresses as the authorization key.
    owner_sub: Mapped[str] = mapped_column(
        String(255), default="local:default", index=True)
    module: Mapped[str] = mapped_column(String(32))  # build_assessments|build_concepts
    upload_type: Mapped[str] = mapped_column(String(32), default="textbook")
    # textbook|questions|questions_and_answers|handwritten|document
    textbook_mode: Mapped[str] = mapped_column(String(16), default="")  # extract|create
    # post|pre (build_concepts). Restructure step 7 retired the legacy
    # pre-learning generation lane but DELIBERATELY keeps this column and its
    # ``post|pre`` domain: this repo has no migration system (``db.py``'s
    # ``_ensure_columns`` is additive ``ALTER TABLE ADD COLUMN`` only and
    # returns early for non-SQLite), so the column cannot be dropped, the value
    # is inside every stored checkpoint fingerprint, and ``pre`` is public API
    # contract on the two checkpoint routes, typed as a closed union by the
    # frontend client. Pre/Post is becoming a per-output property rather than a
    # per-job one; convergence is step 8's.
    learning_kind: Mapped[str] = mapped_column(String(16), default="")
    # Book this upload came from (e.g. "RD Sharma"); drives multi-source tagging.
    source_book: Mapped[str] = mapped_column(String(128), default="")
    filename: Mapped[str] = mapped_column(String(255), default="")
    # Private path relative to UPLOAD_DIR. Legacy rows with an empty key fall
    # back to the historical filename-only location.
    upload_storage_key: Mapped[str] = mapped_column(String(512), default="")
    mmd_text: Mapped[str] = mapped_column(Text, default="")
    deposit_scope_type: Mapped[str] = mapped_column(String(16), default="chapter")
    deposit_scope_ids: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(16), default="uploaded")
    # uploaded|converted|deposited|generated
    result_ids: Mapped[list] = mapped_column(JSON, default=list)
    detail: Mapped[str] = mapped_column(Text, default="")
    # Question / Task Inventory captured during concept generation, kept for
    # extraction-completeness auditing: {"items": [...], "stats": {...},
    # "mined_types": [...]}. Downloadable as CSV.
    question_inventory: Mapped[dict] = mapped_column(JSON, default=dict)
    # Durable pre-Type-assignment state. A failed generation can resume after
    # expensive skeleton, description, inventory, and Type-mining API stages.
    generation_checkpoint: Mapped[dict] = mapped_column(JSON, default=dict)
    # Browser-visible diagnostic events from the latest generation attempt.
    # Keeping them with the upload means exact validator locations survive a
    # page refresh and can travel with an exported checkpoint bundle.
    generation_log: Mapped[list] = mapped_column(JSON, default=list)
    # Cumulative OpenAI billing-token usage for the currently staged physical
    # file, including billable retries and resumed generation attempts.
    openai_usage: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    @property
    def checkpoint_available(self) -> bool:
        """True when an UNFINISHED saved run is waiting to be resumed.

        A released or published job keeps its checkpoint stored (it is the
        durable artifact idempotent re-release replays from), but it is not
        "a run awaiting resume" — surfacing it as resumable made the UI
        re-offer finished runs forever. ``checkpoints.resumable_jobs``
        applies the same predicate in SQL; the two must stay in step.
        """
        return bool(
            isinstance(self.generation_checkpoint, dict)
            and self.generation_checkpoint.get("stage")
            and self.status not in ("released", "generated")
        )

    @property
    def checkpoint_stage(self) -> str:
        if not isinstance(self.generation_checkpoint, dict):
            return ""
        return str(self.generation_checkpoint.get("stage") or "")

    @property
    def checkpoint_saved_at(self) -> str:
        if not isinstance(self.generation_checkpoint, dict):
            return ""
        return str(self.generation_checkpoint.get("saved_at") or "")

    @property
    def checkpoint_progress(self) -> float:
        if not isinstance(self.generation_checkpoint, dict):
            return 0.0
        try:
            return max(
                0.0,
                min(1.0, float(self.generation_checkpoint.get("progress") or 0.0)),
            )
        except (TypeError, ValueError):
            return 0.0

    @property
    def checkpoint_target_identity(self) -> dict:
        """Stable curriculum destination carried by a resumable checkpoint."""
        if not isinstance(self.generation_checkpoint, dict):
            return {}
        identity = self.generation_checkpoint.get("target_identity")
        if not isinstance(identity, dict):
            return {}
        return {
            field: str(identity.get(field) or "")
            for field in CHECKPOINT_TARGET_IDENTITY_FIELDS
        }

    @property
    def pending_decision(self) -> dict | None:
        """Current human semantic decision, without exposing its ledger."""
        if not isinstance(self.generation_checkpoint, dict):
            return None
        ledger = self.generation_checkpoint.get("human_decisions")
        if not isinstance(ledger, dict):
            return None
        pending = ledger.get("pending")
        return pending if isinstance(pending, dict) else None

    @property
    def awaiting_decision(self) -> bool:
        return self.pending_decision is not None

    @property
    def generation_running(self) -> bool:
        """Current single-process run state for duplicate-resume prevention."""
        from .services import uploads

        return uploads.is_job_running(self.id)


class AssessmentRelease(Base):
    """One immutable MES assessment release: the source of both workbooks.

    The Concept File and the Master File are projections of one release
    object (spec §1/§5.6/§13). Nothing here is ever assembled from mutable
    database rows: the concept snapshot, source inventory, blueprint,
    candidates, groups, and placements are frozen into the release payload
    with content hashes, and both workbook artifacts carry those hashes in
    their manifest. A release is append-only — corrections produce a new
    version that supersedes this one, never an in-place edit.
    """

    __tablename__ = "assessment_releases"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Stable external identity; versions of one logical release share it.
    release_uid: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    owner_sub: Mapped[str] = mapped_column(
        String(255), default="local:default", index=True)
    job_id: Mapped[int | None] = mapped_column(
        ForeignKey("upload_jobs.id"), nullable=True, default=None, index=True)
    session_id: Mapped[int | None] = mapped_column(
        ForeignKey("assessment_sessions.id"), nullable=True, default=None)
    # draft|materialized|validated_with_flags|ready_for_upload|
    # publication_pending|uploaded|superseded (spec §5.6).
    #
    # INTERNAL lifecycle, not the public release vocabulary. The three §4
    # names (Ready / Ready with flags / Diagnostic release) are the only
    # public ones and are computed by ``services.release_core``; this and
    # ``diagnostics["readiness"]`` stay beside it as diagnostics
    # (spec-step8 T1).
    state: Mapped[str] = mapped_column(String(32), default="draft")
    # WHICH LANE this immutable row is the record of: "pre" (Outputs 01/02)
    # or "post" (Outputs 03/04). One row per lane per run — two rows, four
    # projections (spec-step8 T2). Blank on a legacy Build Assessments
    # release that declared no lane, which is why the column defaults to
    # "" rather than to a lane: defaulting would assert a lane nobody
    # named.
    lane: Mapped[str] = mapped_column(String(16), default="", index=True)
    # The workbook LAYOUT this run executed under (``bulk_import.layouts``
    # ids, e.g. "sop-mes-1"). Recorded on the row so a release published
    # under one layout is never re-read under another.
    layout_id: Mapped[str] = mapped_column(String(64), default="")
    # Immutable inputs, frozen at Stage 1 with their hashes.
    concept_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    concept_snapshot_sha256: Mapped[str] = mapped_column(
        String(64), default="")
    source_inventory_sha256: Mapped[str] = mapped_column(
        String(64), default="")
    blueprint_sha256: Mapped[str] = mapped_column(String(64), default="")
    # Release-scoped domain records (spec §5): {"source_atoms": [...],
    # "blueprint_cells": [...], "candidates": [...], "groups": [...],
    # "placements": [...], "authority_ledgers": {...}}.
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    # Issue ledger, readiness, and validation diagnostics.
    diagnostics: Mapped[dict] = mapped_column(JSON, default=dict)
    # Provider/model/prompt/schema identity pinned for the whole run,
    # plus the run context §3 guard 2 asks for: the assessment PROFILE
    # name, the LAYOUT manifest identity, and the staged concept-release
    # version this row was frozen from. Declared since the first MES
    # release and written from spec-step8 S6 onward — before that it was
    # a column no code ever filled.
    provider_identity: Mapped[dict] = mapped_column(JSON, default=dict)
    # {"concepts_xlsx": sha256, "master_xlsx": sha256, "manifest": sha256}.
    workbook_hashes: Mapped[dict] = mapped_column(JSON, default=dict)
    publication: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow)
    uploaded_at: Mapped[datetime | None] = mapped_column(
        DateTime, default=None, nullable=True)
    superseded_at: Mapped[datetime | None] = mapped_column(
        DateTime, default=None, nullable=True)

    __table_args__ = (
        UniqueConstraint("release_uid", "version", name="uq_release_version"),
    )


class ConceptRevision(Base):
    """One reviewer instruction and the edit Aegis made from it.

    Review history is deliberately *not* written into the delivered workbook.
    The output file stays exactly what generation produced, so a reviewer can
    diff two rounds without the audit trail changing the artifact under them.
    Everything about the review lives here instead, structured so the founder
    can query across jobs later and turn recurring corrections into product
    fixes.

    Rounds are unbounded: a reviewer may keep correcting an output for as long
    as it takes. ``round_number`` is per job and strictly increasing, so the
    sequence of instructions is replayable in order.
    """

    __tablename__ = "concept_revisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Stable Google ``sub`` (or the offline principal) that authored the
    # instruction. Never a mutable email address.
    owner_sub: Mapped[str] = mapped_column(
        String(255), default="local:default", index=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("upload_jobs.id"), index=True)
    # Per-job, 1-based, strictly increasing. No ceiling.
    round_number: Mapped[int] = mapped_column(Integer, default=1)
    # The reviewer's words, stored verbatim. This is the primary research
    # artifact: it is what Aegis should have done without being told.
    instruction: Mapped[str] = mapped_column(Text, default="")
    # pending|applied|failed|no_change
    status: Mapped[str] = mapped_column(String(16), default="pending")
    # Model-authored account of what it changed, for the reviewer to read back.
    change_summary: Mapped[str] = mapped_column(Text, default="")
    # Structured per-concept edits: [{"concept_id", "field", "before",
    # "after", "reason"}]. Queryable, so recurring placement corrections can be
    # counted across jobs rather than re-read as prose.
    changes: Mapped[list] = mapped_column(JSON, default=list)
    # Concepts Aegis placed by best judgement rather than certification, as
    # flagged at generation time. Kept per round so it is visible whether a
    # reviewer's correction landed on a flagged placement or a confident one.
    flagged_placements: Mapped[list] = mapped_column(JSON, default=list)
    model: Mapped[str] = mapped_column(String(128), default="")
    openai_usage: Mapped[dict] = mapped_column(JSON, default=dict)
    # Why an instruction could not be applied, when status is ``failed``.
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime, default=None, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "job_id", "round_number", name="uq_concept_revision_round"
        ),
    )

    @property
    def applied(self) -> bool:
        return self.status == "applied"

    @property
    def change_count(self) -> int:
        return len(self.changes) if isinstance(self.changes, list) else 0


class ConceptReleaseVersion(Base):
    """Append-only history of staged concept-release rounds (§7, step 9).

    The job's release SLOT stays the ONE authority for the CURRENT staged
    payload; every review round — the pre-edit staging snapshot, a
    reviewer's verbatim manual edit, an applied (or failed) instruction —
    appends one immutable row here carrying the full payload, the
    instruction, the operations, and the diff. An applied round is
    therefore a NEW recorded release version, never an untracked overwrite
    (docs/aegis-restructure.md §7: "Every applied round produces a new
    immutable release version with the instruction, operations and diff
    preserved"). Rows are never updated or deleted; lineage is by
    ``staged_release_uid`` because the numeric version restarts when the
    inventory column is legitimately cleared.
    """

    __tablename__ = "concept_release_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_sub: Mapped[str] = mapped_column(
        String(255), default="local:default", index=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("upload_jobs.id"), index=True)
    lane: Mapped[str] = mapped_column(String(8), default="post")
    version: Mapped[int] = mapped_column(Integer, default=0)
    staged_release_uid: Mapped[str] = mapped_column(String(64), default="")
    parent_uid: Mapped[str] = mapped_column(String(64), default="")
    # "staged" | "manual_edit" | "instruction" | "instruction_failed"
    origin: Mapped[str] = mapped_column(String(32), default="staged")
    instruction: Mapped[str] = mapped_column(Text, default="")
    operations: Mapped[list] = mapped_column(JSON, default=list)
    diff: Mapped[dict] = mapped_column(JSON, default=dict)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint(
            "job_id",
            "lane",
            "staged_release_uid",
            "origin",
            name="uq_concept_release_version_uid",
        ),
    )

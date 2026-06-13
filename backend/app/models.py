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

    chapter = relationship("Chapter", back_populates="topics")
    concepts = relationship("Concept", back_populates="topic", cascade="all, delete-orphan")


class Concept(Base):
    __tablename__ = "concepts"

    id: Mapped[int] = mapped_column(primary_key=True)
    topic_id: Mapped[int] = mapped_column(ForeignKey("topics.id"))
    concept_title: Mapped[str] = mapped_column(String(255), default="")
    concept_display_name: Mapped[str] = mapped_column(String(512), default="")
    concept_details: Mapped[str] = mapped_column(Text, default="")
    keywords: Mapped[str] = mapped_column(Text, default="")
    digicards: Mapped[str] = mapped_column(Text, default="")
    related_concepts: Mapped[str] = mapped_column(Text, default="")
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
    origin: Mapped[str] = mapped_column(String(32), default="seed")  # seed|concept_mapping|upload
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
    source: Mapped[str] = mapped_column(String(32), default="concept_mapping")  # concept_mapping|upload
    scope_type: Mapped[str] = mapped_column(String(16), default="chapter")  # chapter|topic|concept
    scope_ids: Mapped[list] = mapped_column(JSON, default=list)  # ids of chapters/topics/concepts
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft|generated
    generated_question_ids: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    batches = relationship("BlueprintBatch", back_populates="session", cascade="all, delete-orphan")


class UploadJob(Base):
    """An upload-based job: a document converted to MMD, then deposited/generated."""

    __tablename__ = "upload_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    module: Mapped[str] = mapped_column(String(32))  # build_assessments|build_concepts
    upload_type: Mapped[str] = mapped_column(String(32), default="textbook")
    # textbook|questions|questions_and_answers|handwritten|document
    textbook_mode: Mapped[str] = mapped_column(String(16), default="")  # extract|create
    learning_kind: Mapped[str] = mapped_column(String(16), default="")  # post|pre (build_concepts)
    # Book this upload came from (e.g. "RD Sharma"); drives multi-source tagging.
    source_book: Mapped[str] = mapped_column(String(128), default="")
    filename: Mapped[str] = mapped_column(String(255), default="")
    mmd_text: Mapped[str] = mapped_column(Text, default="")
    deposit_scope_type: Mapped[str] = mapped_column(String(16), default="chapter")
    deposit_scope_ids: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(16), default="uploaded")
    # uploaded|converted|deposited|generated
    result_ids: Mapped[list] = mapped_column(JSON, default=list)
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

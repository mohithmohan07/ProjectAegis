"""Module 2: Build Concepts.

  Post Learning — upload a document (any format), convert to MMD, parse it into
  concepts, and deposit them under a chapter.

  Pre Learning — two options:
    A. Upload: upload -> MMD -> derive pre-learning concepts -> deposit.
    B. Use existing Post Learning: pick one or more chapters; their existing
       post-learning concepts drive generation of pre-learning concepts.

  All created concepts are written to the Bulk Import output workbook
  (append-only) as concept-catalog rows, and chapters' pre_topics / post_topics
  summaries are kept in sync.
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from .. import config, models
from .. import bulk_import as bi
from ..bulk_import import writer
from . import concept_cleanup, concept_refiner, generation, mmd, progress


def _find_concept_in_chapter(chapter: models.Chapter, title: str) -> models.Concept | None:
    """Locate an existing concept anywhere under the chapter by normalized title.

    Schools use different books; the same concept arriving from another book
    must be reused (and its sources extended), never duplicated.
    """
    norm = bi.normalize_question_text(title)
    for t in chapter.topics:
        for c in t.concepts:
            if bi.normalize_question_text(c.concept_title) == norm:
                return c
    return None


def _find_or_create_topic(
    db: Session, chapter: models.Chapter, topic_title: str, pre_post: str,
) -> models.Topic:
    for t in chapter.topics:
        if t.topic_title == topic_title and t.pre_post_learning == pre_post:
            return t
    # Create through the relationship so chapter.topics stays current within
    # this session — otherwise every repeat of the same topic title would
    # miss the lookup above and create a duplicate Topic row.
    topic = models.Topic(
        topic_title=topic_title,
        topic_display_name=topic_title, pre_post_learning=pre_post,
    )
    chapter.topics.append(topic)
    db.flush()
    return topic


def _add_concept(db: Session, topic: models.Topic, rec: dict,
                 source_book: str = "") -> models.Concept:
    chapter = topic.chapter
    # Normalize name (& collapse) and description (strip dangling refs) before
    # persisting, so dry and live output are equally import-clean.
    rec = concept_cleanup.clean_concept_record(dict(rec))
    concept = models.Concept(
        topic_id=topic.id,
        concept_title=rec["concept_title"],
        # Display name stays CLEAN; the writer composes the tagged title column.
        concept_display_name=rec["concept_title"],
        parent_concept=rec.get("parent_concept", ""),
        concept_details=rec.get("concept_details", ""),
        keywords=rec.get("keywords", ""),
        sources=source_book.strip(),
    )
    db.add(concept)
    db.flush()
    # Every concept gets the three standard group shells.
    for g_type in ("Basic", "Intermediate", "Advanced"):
        db.add(models.Group(
            concept_id=concept.id, group_type=g_type,
            group_name=f"{concept.concept_title} — {g_type}",
            group_display_name=f"{concept.concept_title} — {g_type}",
            group_status="Active",
        ))
    return concept


def _deposit_concepts(
    db: Session, chapter: models.Chapter, records: list[dict],
    pre_post: str, source_book: str,
) -> tuple[list[int], list[int]]:
    """Create concepts under the chapter, reusing existing ones across books.

    Returns (created_ids, merged_ids): merged = concept already existed (any
    topic of this chapter, normalized-title match) and only its sources grew.
    """
    # Clean each record (name hygiene, Title Case, dangling-ref removal), then
    # run the deterministic chapter pass: continuous "Type NN" numbering across
    # the whole chapter, "Miscellaneous Type NN" for culmination rows, and a
    # "Recap" description for culminations. Chapter-wide *intelligence* (dedup,
    # Types enrichment, naming) is done by the API passes in concepts_from_mmd;
    # this pass only enforces the numbering/format the team requires.
    records = [concept_cleanup.clean_concept_record(dict(r)) for r in records]
    records = concept_refiner.refine_chapter(records)

    created_ids: list[int] = []
    merged_ids: list[int] = []
    for rec in records:
        existing = _find_concept_in_chapter(chapter, rec["concept_title"])
        if existing is not None:
            if source_book.strip():
                existing.sources = bi.merge_sources(existing.sources, source_book)
            merged_ids.append(existing.id)
            continue
        topic = _find_or_create_topic(db, chapter, rec["topic"], pre_post)
        concept = _add_concept(db, topic, rec, source_book)
        db.flush()
        created_ids.append(concept.id)
    return created_ids, merged_ids


_BLANK_VALUES = {"", "na", "n/a", "none", "-", "tbd"}


def _is_blank(value: str) -> bool:
    return (value or "").strip().lower() in _BLANK_VALUES


def _sync_chapter_topic_summary(chapter: models.Chapter) -> None:
    """Refresh topic lists and fill the required summary/duration fields.

    pre_topics / post_topics are comma-separated topic titles. chapter and topic
    descriptions and the chapter duration (in minutes) are filled with a brief
    synthesized summary whenever they are blank/NA, so the output never ships
    "NA" in a required column.
    """
    topics = sorted(chapter.topics, key=lambda t: t.id)
    # pre/post topic columns list each topic by its tagged Topic Title (with the
    # code), matching the topic_title column exactly, so the importer links them.
    pre = [writer.composed_topic_title(t) for t in topics if t.pre_post_learning == "Pre"]
    post = [writer.composed_topic_title(t) for t in topics if t.pre_post_learning == "Post"]
    chapter.pre_topics = ", ".join(pre)
    chapter.post_topics = ", ".join(post)

    # Per-topic summary: the concepts it teaches.
    for t in topics:
        if _is_blank(t.topic_description):
            names = [c.concept_title for c in sorted(t.concepts, key=lambda c: c.id)]
            if names:
                t.topic_description = "Covers " + ", ".join(names) + "."

    n_concepts = sum(len(t.concepts) for t in topics)
    if _is_blank(chapter.chapter_description) and topics:
        chapter.chapter_description = (
            f"This chapter develops {n_concepts} concept(s) across "
            f"{len(topics)} topic(s): " + ", ".join(t.topic_title for t in topics) + "."
        )
    if _is_blank(chapter.chapter_duration) and n_concepts:
        # Rough classroom estimate: ~12 minutes of instruction per concept.
        chapter.chapter_duration = f"{max(40, n_concepts * 12)} minutes"


# --------------------------------------------------------------------------- #
# Post Learning
# --------------------------------------------------------------------------- #

def create_post_learning_job(
    db: Session, *, filename: str, raw_bytes: bytes, source_book: str = "",
) -> models.UploadJob:
    """Stage the file only — conversion to MMD is a separate explicit step."""
    from . import uploads
    uploads.save_upload_file(filename, raw_bytes)
    job = models.UploadJob(
        module="build_concepts", upload_type="document", learning_kind="post",
        filename=Path(filename).name, mmd_text="", status="uploaded",
        source_book=source_book.strip(),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def generate_post_learning(db: Session, job_id: int, target_chapter_id: int) -> dict:
    job = db.get(models.UploadJob, job_id)
    chapter = db.get(models.Chapter, target_chapter_id)
    if not job or not chapter:
        raise ValueError("upload job or target chapter not found")
    if not job.mmd_text:
        raise ValueError("convert the uploaded document to MMD before generating")
    progress.log(f"Post-learning generation into chapter '{chapter.chapter_title}'.")
    records = generation.concepts_from_mmd(
        job.mmd_text,
        subject=chapter.subject,
        board=chapter.board,
        grade=chapter.grade,
        unit=chapter.unit,
        chapter_title=chapter.chapter_title,
        chapter_id=chapter.id,
        chapter_code=chapter.chapter_code,
        learning_kind="Post",
    )
    created_ids, merged_ids = _deposit_concepts(
        db, chapter, records, "Post", job.source_book)
    _sync_chapter_topic_summary(chapter)
    db.commit()

    written = writer.append_concepts(
        db, config.BULK_IMPORT_OUTPUT, created_ids + merged_ids)
    job.status = "generated"
    job.deposit_scope_type = "chapter"
    job.deposit_scope_ids = [target_chapter_id]
    job.result_ids = created_ids
    job.detail = (
        f"created {len(created_ids)} post-learning concepts, "
        f"merged sources into {len(merged_ids)} existing"
    )
    db.commit()
    progress.set_progress(1.0, label="Done")
    progress.log(
        f"Created {len(created_ids)} post-learning concepts "
        f"({len(merged_ids)} merged).", level="success")
    progress.log(f"Output workbook path: {config.BULK_IMPORT_OUTPUT}")
    return {
        "job_id": job_id,
        "concepts_created": len(created_ids),
        "concepts_merged": len(merged_ids),
        "concept_ids": created_ids + merged_ids,
        "rows_appended": written["written"],
        "sources_updated": written["sources_updated"],
        "output_workbook": str(config.BULK_IMPORT_OUTPUT),
    }


# --------------------------------------------------------------------------- #
# Pre Learning
# --------------------------------------------------------------------------- #

def create_pre_learning_upload_job(
    db: Session, *, filename: str, raw_bytes: bytes, source_book: str = "",
) -> models.UploadJob:
    """Stage the file only — conversion to MMD is a separate explicit step."""
    from . import uploads
    uploads.save_upload_file(filename, raw_bytes)
    job = models.UploadJob(
        module="build_concepts", upload_type="document", learning_kind="pre",
        filename=Path(filename).name, mmd_text="", status="uploaded",
        source_book=source_book.strip(),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def generate_pre_learning_from_upload(db: Session, job_id: int, target_chapter_id: int) -> dict:
    job = db.get(models.UploadJob, job_id)
    chapter = db.get(models.Chapter, target_chapter_id)
    if not job or not chapter:
        raise ValueError("upload job or target chapter not found")
    if not job.mmd_text:
        raise ValueError("convert the uploaded document to MMD before generating")
    progress.log(f"Pre-learning generation for chapter '{chapter.chapter_title}'.")

    # Extract the chapter's concept map first, then derive prerequisites from
    # it. Live mode runs the full dependency-architecture derivation (syllabus
    # filter + auditor pass); dry mode keeps the deterministic framing.
    base = generation.concepts_from_mmd(
        job.mmd_text,
        subject=chapter.subject,
        board=chapter.board,
        grade=chapter.grade,
        unit=chapter.unit,
        chapter_title=chapter.chapter_title,
        chapter_id=chapter.id,
        chapter_code=chapter.chapter_code,
        learning_kind="Post",
    )
    pre_records = generation.pre_learning_from_rows(
        base,
        subject=chapter.subject, grade=chapter.grade, board=chapter.board,
        chapter_title=chapter.chapter_title,
    )
    created_ids, merged_ids = _deposit_concepts(
        db, chapter, pre_records, "Pre", job.source_book)
    _sync_chapter_topic_summary(chapter)
    db.commit()

    written = writer.append_concepts(
        db, config.BULK_IMPORT_OUTPUT, created_ids + merged_ids)
    job.status = "generated"
    job.deposit_scope_type = "chapter"
    job.deposit_scope_ids = [target_chapter_id]
    job.result_ids = created_ids
    job.detail = (
        f"created {len(created_ids)} pre-learning concepts from upload, "
        f"merged sources into {len(merged_ids)} existing"
    )
    db.commit()
    progress.set_progress(1.0, label="Done")
    progress.log(
        f"Created {len(created_ids)} pre-learning concepts "
        f"({len(merged_ids)} merged).", level="success")
    progress.log(f"Output workbook path: {config.BULK_IMPORT_OUTPUT}")
    return {
        "job_id": job_id,
        "concepts_created": len(created_ids),
        "concepts_merged": len(merged_ids),
        "concept_ids": created_ids + merged_ids,
        "rows_appended": written["written"],
        "sources_updated": written["sources_updated"],
        "output_workbook": str(config.BULK_IMPORT_OUTPUT),
    }


def generate_pre_learning_from_existing(
    db: Session, chapter_ids: list[int], source_book: str = "",
) -> dict:
    """Option B: derive pre-learning concepts from existing post-learning chapters."""
    chapters = db.query(models.Chapter).filter(models.Chapter.id.in_(chapter_ids)).all()
    if not chapters:
        raise ValueError("no chapters selected")

    created_ids: list[int] = []
    merged_ids: list[int] = []
    per_chapter: dict[int, int] = {}
    for chapter in chapters:
        post_concepts = [
            c for t in chapter.topics if t.pre_post_learning == "Post" for c in t.concepts
        ]
        if not post_concepts:
            per_chapter[chapter.id] = 0
            continue
        pre_records = generation.pre_learning_from_concepts(post_concepts)
        created, merged = _deposit_concepts(db, chapter, [
            {
                "topic": rec["topic"],
                "concept_title": rec["concept_title"],
                "parent_concept": rec.get("parent_concept", ""),
                "concept_details": rec["concept_details"],
                "keywords": rec.get("keywords", ""),
            }
            for rec in pre_records
        ], "Pre", source_book)
        created_ids += created
        merged_ids += merged
        _sync_chapter_topic_summary(chapter)
        per_chapter[chapter.id] = len(created)
    db.commit()

    written = writer.append_concepts(
        db, config.BULK_IMPORT_OUTPUT, created_ids + merged_ids)
    return {
        "chapters": len(chapters),
        "concepts_created": len(created_ids),
        "concepts_merged": len(merged_ids),
        "concept_ids": created_ids + merged_ids,
        "per_chapter": per_chapter,
        "rows_appended": written["written"],
        "sources_updated": written["sources_updated"],
        "output_workbook": str(config.BULK_IMPORT_OUTPUT),
    }

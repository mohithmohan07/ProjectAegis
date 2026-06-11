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

from sqlalchemy.orm import Session

from .. import config, models
from ..bulk_import import writer
from . import concept_cleanup, generation, mmd


def _find_or_create_topic(
    db: Session, chapter: models.Chapter, topic_title: str, pre_post: str,
) -> models.Topic:
    for t in chapter.topics:
        if t.topic_title == topic_title and t.pre_post_learning == pre_post:
            return t
    topic = models.Topic(
        chapter_id=chapter.id, topic_title=topic_title,
        topic_display_name=topic_title, pre_post_learning=pre_post,
    )
    db.add(topic)
    db.flush()
    return topic


def _add_concept(db: Session, topic: models.Topic, rec: dict) -> models.Concept:
    chapter = topic.chapter
    # Normalize name (& collapse) and description (strip dangling refs) before
    # persisting, so dry and live output are equally import-clean.
    rec = concept_cleanup.clean_concept_record(dict(rec))
    concept = models.Concept(
        topic_id=topic.id,
        concept_title=rec["concept_title"],
        concept_display_name=f"{rec['concept_title']} ({chapter.chapter_code}_{topic.pre_post_learning})",
        concept_details=rec.get("concept_details", ""),
        keywords=rec.get("keywords", ""),
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


def _sync_chapter_topic_summary(chapter: models.Chapter) -> None:
    pre = [t.topic_title for t in chapter.topics if t.pre_post_learning == "Pre"]
    post = [t.topic_title for t in chapter.topics if t.pre_post_learning == "Post"]
    chapter.pre_topics = "; ".join(pre)
    chapter.post_topics = "; ".join(post)


# --------------------------------------------------------------------------- #
# Post Learning
# --------------------------------------------------------------------------- #

def create_post_learning_job(
    db: Session, *, filename: str, raw_bytes: bytes,
) -> models.UploadJob:
    dest = config.UPLOAD_DIR / filename
    dest.write_bytes(raw_bytes)
    job = models.UploadJob(
        module="build_concepts", upload_type="document", learning_kind="post",
        filename=filename, mmd_text=mmd.to_mmd(dest), status="converted",
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

    records = generation.concepts_from_mmd(job.mmd_text)
    created_ids: list[int] = []
    for rec in records:
        topic = _find_or_create_topic(db, chapter, rec["topic"], "Post")
        concept = _add_concept(db, topic, rec)
        db.flush()
        created_ids.append(concept.id)
    _sync_chapter_topic_summary(chapter)
    db.commit()

    written = writer.append_concepts(db, config.BULK_IMPORT_OUTPUT, created_ids)
    job.status = "generated"
    job.deposit_scope_type = "chapter"
    job.deposit_scope_ids = [target_chapter_id]
    job.result_ids = created_ids
    job.detail = f"created {len(created_ids)} post-learning concepts"
    db.commit()
    return {
        "job_id": job_id, "concepts_created": len(created_ids),
        "rows_appended": written, "output_workbook": str(config.BULK_IMPORT_OUTPUT),
    }


# --------------------------------------------------------------------------- #
# Pre Learning
# --------------------------------------------------------------------------- #

def create_pre_learning_upload_job(
    db: Session, *, filename: str, raw_bytes: bytes,
) -> models.UploadJob:
    dest = config.UPLOAD_DIR / filename
    dest.write_bytes(raw_bytes)
    job = models.UploadJob(
        module="build_concepts", upload_type="document", learning_kind="pre",
        filename=filename, mmd_text=mmd.to_mmd(dest), status="converted",
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

    # Treat parsed concepts as the base, then derive pre-learning framing.
    base = generation.concepts_from_mmd(job.mmd_text)
    created_ids: list[int] = []
    for rec in base:
        topic = _find_or_create_topic(db, chapter, f"{rec['topic']} (Pre-Learning)", "Pre")
        pre_rec = {
            "concept_title": f"Pre: {rec['concept_title']}",
            "concept_details": (
                f"Description: prerequisite for '{rec['concept_title']}'. "
                "// Types: Type 01: Prerequisite recall "
                "// Misconception: assuming the prerequisite is already mastered."
            ),
            "keywords": rec.get("keywords", ""),
        }
        concept = _add_concept(db, topic, pre_rec)
        db.flush()
        created_ids.append(concept.id)
    _sync_chapter_topic_summary(chapter)
    db.commit()

    written = writer.append_concepts(db, config.BULK_IMPORT_OUTPUT, created_ids)
    job.status = "generated"
    job.deposit_scope_type = "chapter"
    job.deposit_scope_ids = [target_chapter_id]
    job.result_ids = created_ids
    job.detail = f"created {len(created_ids)} pre-learning concepts from upload"
    db.commit()
    return {
        "job_id": job_id, "concepts_created": len(created_ids),
        "rows_appended": written, "output_workbook": str(config.BULK_IMPORT_OUTPUT),
    }


def generate_pre_learning_from_existing(db: Session, chapter_ids: list[int]) -> dict:
    """Option B: derive pre-learning concepts from existing post-learning chapters."""
    chapters = db.query(models.Chapter).filter(models.Chapter.id.in_(chapter_ids)).all()
    if not chapters:
        raise ValueError("no chapters selected")

    created_ids: list[int] = []
    per_chapter: dict[int, int] = {}
    for chapter in chapters:
        post_concepts = [
            c for t in chapter.topics if t.pre_post_learning == "Post" for c in t.concepts
        ]
        if not post_concepts:
            per_chapter[chapter.id] = 0
            continue
        pre_records = generation.pre_learning_from_concepts(post_concepts)
        for rec in pre_records:
            topic = _find_or_create_topic(db, chapter, rec["topic"], "Pre")
            concept = _add_concept(db, topic, {
                "concept_title": rec["concept_title"],
                "concept_details": rec["concept_details"],
                "keywords": rec.get("keywords", ""),
            })
            db.flush()
            created_ids.append(concept.id)
        _sync_chapter_topic_summary(chapter)
        per_chapter[chapter.id] = len(pre_records)
    db.commit()

    written = writer.append_concepts(db, config.BULK_IMPORT_OUTPUT, created_ids)
    return {
        "chapters": len(chapters),
        "concepts_created": len(created_ids),
        "per_chapter": per_chapter,
        "rows_appended": written,
        "output_workbook": str(config.BULK_IMPORT_OUTPUT),
    }

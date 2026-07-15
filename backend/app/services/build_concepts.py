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

import hashlib
import re
from pathlib import Path

from sqlalchemy.orm import Session

from .. import config, models
from .. import bulk_import as bi
from ..bulk_import import writer
from . import (
    chapter_durations,
    concept_cleanup,
    concept_refiner,
    concept_validator,
    generation,
    mmd,
    progress,
)


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
    display_name = bi.strip_topic_title(topic_title) or topic_title
    for t in chapter.topics:
        if t.topic_title == topic_title and t.pre_post_learning == pre_post:
            if t.topic_display_name != display_name:
                t.topic_display_name = display_name
            return t
    # Create through the relationship so chapter.topics stays current within
    # this session — otherwise every repeat of the same topic title would
    # miss the lookup above and create a duplicate Topic row.
    topic = models.Topic(
        topic_title=topic_title,
        topic_display_name=display_name, pre_post_learning=pre_post,
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
    records = concept_cleanup.filter_review_violations(
        records, subject=chapter.subject, board=chapter.board,
        chapter_title=chapter.chapter_title)
    records = concept_cleanup.dedupe_similar_titles_chapter_wide(records)
    records = concept_refiner.refine_chapter(records)
    report = concept_validator.validate_concept_rows(
        records,
        allow_types=True,
        require_culmination=pre_post == "Post",
        allow_culmination=True,
    )
    fatal = [
        e for e in report["errors"]
        if e["severity"] == "error"
        and e["code"] in {
            "required", "required_parent", "description_prefix", "source_artifact",
            "types_format", "case_without_type", "type_without_case",
            "culmination_description", "culmination_count", "culmination_order",
            "section_number", "empty_types", "short_case_example",
        }
    ]
    progress.log(
        f"Deposit validation: {len(fatal)} fatal error(s), "
        f"{report['summary'].get('warnings', 0)} warning(s).")
    if fatal:
        codes = ", ".join(sorted({e["code"] for e in fatal}))
        raise ValueError(f"concept validation failed before deposit: {codes}")

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


def _parse_duration_minutes(value: str) -> int | None:
    """Parse finalized duration strings like '270 minutes' or '270'."""
    text = (value or "").strip().lower()
    if not text or text in _BLANK_VALUES:
        return None
    m = re.search(r"(\d+)", text)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def _chapter_meta_summary(chapter: models.Chapter) -> dict:
    """API-written chapter/topic metadata (empty dict in dry mode / on failure).

    The deterministic summaries in ``_sync_chapter_topic_summary`` are the
    fallback; a metadata failure must never fail a generation job that has
    already produced a valid concept map.
    """
    finalized = _parse_duration_minutes(chapter.chapter_duration)
    expected_duration = finalized or chapter_durations.lookup_duration_minutes(
        board=chapter.board,
        grade=chapter.grade,
        subject=chapter.subject,
        chapter_title=chapter.chapter_title,
    )
    topics_payload = [
        {
            "topic": t.topic_title,
            "pre_post_learning": t.pre_post_learning,
            "concepts": [
                c.concept_title for c in sorted(t.concepts, key=lambda c: c.id)
            ],
        }
        for t in sorted(chapter.topics, key=lambda t: t.id)
    ]
    meta = generation._metadata(
        subject=chapter.subject, board=chapter.board, grade=chapter.grade,
        unit=chapter.unit, chapter_title=chapter.chapter_title,
        chapter_id=chapter.id, chapter_code=chapter.chapter_code,
        finalized_duration_minutes=expected_duration or 0,
    )
    # GPT writes the chapter description/duration/topic descriptions; retry
    # before ever falling back to deterministic summaries, so formula-estimate
    # durations (reviewed as wrong) only ship as an absolute last resort.
    last_exc: Exception | None = None
    for attempt in range(1, 4):
        try:
            return generation.chapter_meta_via_api(meta=meta, topics=topics_payload)
        except Exception as exc:  # noqa: BLE001 — metadata must never kill the job
            last_exc = exc
            progress.log(
                f"Chapter/topic metadata attempt {attempt}/3 failed ({exc}).",
                level="warning",
            )
    progress.log(
        f"Chapter/topic metadata pass failed after retries ({last_exc}) — "
        "using deterministic summaries instead.",
        level="warning",
    )
    return {}


def _sync_chapter_topic_summary(
    chapter: models.Chapter, meta_summary: dict | None = None,
) -> None:
    """Refresh topic lists and fill the summary/duration fields.

    pre_topics / post_topics are comma-separated topic titles. When
    ``meta_summary`` (the API-written chapter/topic metadata) is available it
    OVERWRITES the chapter description, chapter duration, and per-topic
    descriptions — these fields were previously synthesized and read weak.
    Deterministic summaries remain the fallback for anything missing, so the
    output never ships "NA" in a required column.
    """
    meta_summary = meta_summary or {}
    topics = sorted(chapter.topics, key=lambda t: t.id)
    # pre/post topic columns list each topic by its tagged Topic Title (with the
    # code), matching the topic_title column exactly, so the importer links them.
    pre = [writer.composed_topic_title(t) for t in topics if t.pre_post_learning == "Pre"]
    post = [writer.composed_topic_title(t) for t in topics if t.pre_post_learning == "Post"]
    chapter.pre_topics = ", ".join(pre)
    chapter.post_topics = ", ".join(post)

    # Per-topic description: API-written when available, else the concept list.
    topic_descriptions = meta_summary.get("topic_descriptions") or {}
    for t in topics:
        written = topic_descriptions.get(bi.normalize_question_text(t.topic_title))
        if written:
            t.topic_description = written
        elif _is_blank(t.topic_description):
            names = [c.concept_title for c in sorted(t.concepts, key=lambda c: c.id)]
            if names:
                t.topic_description = "Covers " + ", ".join(names) + "."

    n_concepts = sum(len(t.concepts) for t in topics)
    if meta_summary.get("chapter_description"):
        chapter.chapter_description = meta_summary["chapter_description"]
    elif _is_blank(chapter.chapter_description) and topics:
        topic_names = ", ".join(
            bi.strip_topic_title(t.topic_title) or t.topic_title for t in topics)
        chapter.chapter_description = (
            f"This chapter develops {n_concepts} concept(s) across "
            f"{len(topics)} topic(s): {topic_names}."
        )
    finalized = _parse_duration_minutes(chapter.chapter_duration)
    if finalized:
        chapter.chapter_duration = f"{finalized} minutes"
    elif meta_summary.get("chapter_duration_minutes") and _is_blank(chapter.chapter_duration):
        chapter.chapter_duration = f"{meta_summary['chapter_duration_minutes']} minutes"
    elif _is_blank(chapter.chapter_duration) and n_concepts:
        # Rough classroom estimate: ~12 minutes of instruction per concept.
        chapter.chapter_duration = f"{max(40, n_concepts * 12)} minutes"


# --------------------------------------------------------------------------- #
# Question / Task Inventory (extraction-completeness audit)
# --------------------------------------------------------------------------- #

def _store_inventory(job: models.UploadJob, artifacts: dict) -> None:
    """Persist the generation-time inventory + mined Types on the upload job."""
    inventory = artifacts.get("question_task_inventory") or {}
    mined = artifacts.get("mined_types") or {}
    if not inventory.get("items") and not mined.get("types"):
        return
    job.question_inventory = {
        "items": inventory.get("items", []),
        "stats": inventory.get("stats", {}),
        "mined_types": mined.get("types", []),
    }


def _generation_checkpoint_fingerprint(
    job: models.UploadJob, chapter: models.Chapter,
) -> str:
    payload = (
        "post-learning-checkpoint-v1\0"
        + "\0".join(str(value or "") for value in (
            chapter.id,
            chapter.board,
            chapter.grade,
            chapter.subject,
            chapter.unit,
            chapter.chapter_title,
            chapter.chapter_code,
            job.mmd_text,
        ))
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


_INVENTORY_CSV_COLUMNS = [
    "qid", "order_index", "source_kind", "source_label", "parent_source_label",
    "topic_hint", "page_hint", "subpart_label", "requires_visual",
    "requires_context", "normalized_task", "raw_task",
    "raw_solution_or_answer", "shared_context", "image_urls", "content_objects",
    "classified", "mined_type_ids", "mined_type_titles",
]


def inventory_csv(db: Session, job_id: int) -> str:
    """Render the stored Question / Task Inventory as CSV.

    One row per extracted question/task, with the mined Type(s) each item was
    classified into — so completeness of both extraction and classification
    can be audited at a glance.
    """
    import csv
    import io
    import json

    job = db.get(models.UploadJob, job_id)
    if not job:
        raise ValueError("upload job not found")
    data = job.question_inventory or {}
    items = data.get("items", [])
    if not items:
        raise ValueError(
            "no question/task inventory stored for this job — run a live "
            "concept generation first")

    types_by_qid: dict[str, list[tuple[str, str]]] = {}
    for t in data.get("mined_types", []):
        tid = (t.get("type_id") or "").strip()
        title = (t.get("type_title") or "").strip()
        qids = set(t.get("source_question_ids") or [])
        for case in t.get("case_prompts") or []:
            if not isinstance(case, dict):
                continue
            if case.get("source_question_id"):
                qids.add(case["source_question_id"])
            for ex in case.get("examples") or []:
                if isinstance(ex, dict) and ex.get("source_question_id"):
                    qids.add(ex["source_question_id"])
        for qid in qids:
            types_by_qid.setdefault((qid or "").strip(), []).append((tid, title))

    buf = io.StringIO()
    writer_ = csv.DictWriter(buf, fieldnames=_INVENTORY_CSV_COLUMNS, extrasaction="ignore")
    writer_.writeheader()
    for item in items:
        qid = (item.get("qid") or "").strip()
        assigned = types_by_qid.get(qid, [])
        row = {col: item.get(col, "") for col in _INVENTORY_CSV_COLUMNS}
        row["content_objects"] = json.dumps(
            item.get("content_objects") or {}, ensure_ascii=False)
        row["image_urls"] = ", ".join(
            str(u) for u in (item.get("image_urls") or []) if u)
        row["requires_visual"] = "yes" if item.get("requires_visual") else "no"
        row["requires_context"] = "yes" if item.get("requires_context") else "no"
        row["classified"] = "yes" if assigned else "no"
        row["mined_type_ids"] = ", ".join(tid for tid, _ in assigned if tid)
        row["mined_type_titles"] = "; ".join(title for _, title in assigned if title)
        writer_.writerow(row)
    return buf.getvalue()


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
    artifacts: dict = {}
    fingerprint = _generation_checkpoint_fingerprint(job, chapter)
    stored_checkpoint = job.generation_checkpoint or {}
    resume_checkpoint = (
        stored_checkpoint
        if stored_checkpoint.get("fingerprint") == fingerprint
        and stored_checkpoint.get("target_chapter_id") == target_chapter_id
        else None
    )
    if stored_checkpoint and resume_checkpoint is None:
        progress.log(
            "Discarding a stale generation checkpoint because its source or "
            "target chapter changed.",
            level="warning",
        )
        job.generation_checkpoint = {}
        db.commit()
    if resume_checkpoint:
        progress.log(
            "Resuming from the saved pre-Type-assignment checkpoint; expensive "
            "concept extraction, description, inventory, and Type-mining stages "
            "will not run again.",
            level="success",
        )

    def save_checkpoint(checkpoint: dict) -> None:
        durable = dict(checkpoint)
        durable["fingerprint"] = fingerprint
        durable["target_chapter_id"] = target_chapter_id
        job.generation_checkpoint = durable
        _store_inventory(job, {
            "question_task_inventory": durable.get(
                "question_task_inventory") or {},
            "mined_types": durable.get("mined_types") or {},
        })
        job.detail = (
            "Generation checkpoint saved before Type assignment; retry resumes "
            "from this stage."
        )
        db.commit()
        progress.log(
            "Saved durable checkpoint before Type assignment.",
            level="success",
        )

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
        artifacts=artifacts,
        resume_checkpoint=resume_checkpoint,
        checkpoint_callback=save_checkpoint,
    )
    _store_inventory(job, artifacts)
    created_ids, merged_ids = _deposit_concepts(
        db, chapter, records, "Post", job.source_book)
    _sync_chapter_topic_summary(chapter, _chapter_meta_summary(chapter))
    db.commit()

    written = writer.append_concepts(
        db, config.BULK_IMPORT_OUTPUT, created_ids + merged_ids)
    job.status = "generated"
    job.deposit_scope_type = "chapter"
    job.deposit_scope_ids = [target_chapter_id]
    job.result_ids = created_ids
    job.generation_checkpoint = {}
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
    progress.log(
        "Parent concept export: "
        + ("parent_concept column" if written.get("parent_column") else "related_concepts fallback")
    )
    return {
        "job_id": job_id,
        "concepts_created": len(created_ids),
        "concepts_merged": len(merged_ids),
        "concept_ids": created_ids + merged_ids,
        "rows_appended": written["written"],
        "sources_updated": written["sources_updated"],
        "output_workbook": str(config.BULK_IMPORT_OUTPUT),
        "inventory_items": len((job.question_inventory or {}).get("items", [])),
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
    artifacts: dict = {}
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
        artifacts=artifacts,
    )
    _store_inventory(job, artifacts)
    pre_records = generation.pre_learning_from_rows(
        base,
        subject=chapter.subject, grade=chapter.grade, board=chapter.board,
        chapter_title=chapter.chapter_title, unit=chapter.unit,
    )
    created_ids, merged_ids = _deposit_concepts(
        db, chapter, pre_records, "Pre", job.source_book)
    _sync_chapter_topic_summary(chapter, _chapter_meta_summary(chapter))
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
    progress.log(
        "Parent concept export: "
        + ("parent_concept column" if written.get("parent_column") else "related_concepts fallback")
    )
    return {
        "job_id": job_id,
        "concepts_created": len(created_ids),
        "concepts_merged": len(merged_ids),
        "concept_ids": created_ids + merged_ids,
        "rows_appended": written["written"],
        "sources_updated": written["sources_updated"],
        "output_workbook": str(config.BULK_IMPORT_OUTPUT),
        "inventory_items": len((job.question_inventory or {}).get("items", [])),
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
        _sync_chapter_topic_summary(chapter, _chapter_meta_summary(chapter))
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

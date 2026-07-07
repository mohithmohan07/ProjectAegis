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
from . import concept_cleanup, concept_refiner, concept_validator, generation, mmd, progress


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
            "section_number", "empty_types",
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


def _chapter_meta_summary(chapter: models.Chapter) -> dict:
    """API-written chapter/topic metadata (empty dict in dry mode / on failure).

    The deterministic summaries in ``_sync_chapter_topic_summary`` are the
    fallback; a metadata failure must never fail a generation job that has
    already produced a valid concept map.
    """
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
    )
    try:
        return generation.chapter_meta_via_api(meta=meta, topics=topics_payload)
    except Exception as exc:  # noqa: BLE001 — metadata must never kill the job
        progress.log(
            f"Chapter/topic metadata pass failed ({exc}) — using deterministic "
            "summaries instead.",
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
        chapter.chapter_description = (
            f"This chapter develops {n_concepts} concept(s) across "
            f"{len(topics)} topic(s): " + ", ".join(t.topic_title for t in topics) + "."
        )
    if meta_summary.get("chapter_duration_minutes") and _is_blank(chapter.chapter_duration):
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


_INVENTORY_CSV_COLUMNS = [
    "qid", "order_index", "source_kind", "source_label", "parent_source_label",
    "topic_hint", "page_hint", "subpart_label", "requires_visual",
    "requires_context", "normalized_task", "raw_task",
    "raw_solution_or_answer", "shared_context", "content_objects",
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
            if isinstance(case, dict) and case.get("source_question_id"):
                qids.add(case["source_question_id"])
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
        source_book=job.source_book,
        chapter_duration=chapter.chapter_duration,
    )
    _store_inventory(job, artifacts)
    created_ids, merged_ids = _deposit_concepts(
        db, chapter, records, "Post", job.source_book)
    v2_meta = artifacts.get("concept_map_v2_meta") or {}
    _sync_chapter_topic_summary(
        chapter, v2_meta or _chapter_meta_summary(chapter))
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

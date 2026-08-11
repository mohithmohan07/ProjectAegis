"""Explicit database publication for a staged Build Concepts release."""
from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any, Mapping

from sqlalchemy.orm import Session

from .. import bulk_import as bi
from .. import config, models
from ..bulk_import import workbook_sync
from . import build_concepts, concept_cleanup, uploads
from .build_concepts_release import (
    RELEASE_KEY,
    ReleaseUnavailableError,
    _strip_release_fields,
    release_payload,
)


def upload_release_to_database(
    db: Session,
    job_id: int,
    *,
    owner_sub: str | None = None,
) -> dict[str, Any]:
    """Publish the released rows only after an explicit authenticated action.

    Flagged rows are not silently removed. Generation, semantic review and
    publication stay separate: this action performs the deterministic concept
    upsert and shared-workbook publication, but starts no model request.
    """

    job = uploads.get_job(
        db,
        job_id,
        owner_sub=owner_sub,
        module="build_concepts",
    )
    payload = release_payload(job)
    if payload is None:
        raise ReleaseUnavailableError("this upload has no staged release")
    summary = copy.deepcopy(payload.get("summary") or {})
    if summary.get("database_uploaded"):
        return {
            "job_id": job.id,
            "status": "generated",
            "database_uploaded": True,
            "created_concept_ids": copy.deepcopy(job.result_ids or []),
            "updated_concept_ids": [],
            "issue_count": int(summary.get("issue_count") or 0),
            "publication_status": str(
                summary.get("publication_status") or "published"
            ),
        }

    chapter_id = int(payload.get("target_chapter_id") or 0)
    chapter = db.get(models.Chapter, chapter_id)
    if chapter is None:
        raise ValueError("the release target chapter no longer exists")
    records = [
        _strip_release_fields(row)
        for row in payload.get("records") or []
        if isinstance(row, Mapping)
        and str(row.get("topic") or "").strip()
        and str(row.get("concept_title") or row.get("concept") or "").strip()
    ]
    if not records:
        raise ValueError("the release contains no concept rows to upload")

    pre_post = "Pre" if job.learning_kind == "pre" else "Post"
    source_book = (
        str(job.source_book or "").strip()
        or str(job.filename or "").strip()
        or "Released source"
    )
    created_ids: list[int] = []
    merged_ids: list[int] = []
    topic_positions: dict[str, int] = {}
    concept_positions: dict[str, int] = {}

    try:
        db.expire_all()
        chapter = db.get(models.Chapter, chapter_id)
        if chapter is None:
            raise ValueError("the release target chapter no longer exists")
        for raw in records:
            rec = concept_cleanup.clean_concept_record(dict(raw))
            topic_key = bi.normalize_question_text(rec["topic"])
            topic_positions.setdefault(topic_key, len(topic_positions) + 1)
            concept_positions[topic_key] = concept_positions.get(topic_key, 0) + 1
            source_order = concept_positions[topic_key]
            topic = build_concepts._find_or_create_topic(
                db,
                chapter,
                rec["topic"],
                pre_post,
            )
            topic.source_order = topic_positions[topic_key]
            existing = build_concepts._find_concept_in_chapter(
                chapter,
                rec["concept_title"],
                pre_post=pre_post,
            )
            if existing is None:
                concept = build_concepts._add_concept(
                    db,
                    topic,
                    rec,
                    source_book,
                )
                concept.source_order = source_order
                db.flush()
                created_ids.append(concept.id)
                continue

            existing.topic = topic
            for tag in list(existing.tags):
                tagged_topic = tag.topic
                if (
                    tagged_topic is not None
                    and tagged_topic.chapter_id == chapter.id
                    and tagged_topic.pre_post_learning == pre_post
                    and tagged_topic.id != topic.id
                ):
                    db.delete(tag)
            existing.concept_title = rec["concept_title"]
            existing.concept_display_name = rec["concept_title"]
            existing.parent_concept = rec.get("parent_concept", "")
            existing.concept_details = rec.get("concept_details", "")
            existing.keywords = rec.get("keywords", "")
            existing.source_order = source_order
            existing.sources = bi.merge_sources(existing.sources, source_book)
            db.flush()
            merged_ids.append(existing.id)

        active_ids = sorted(set(created_ids + merged_ids))
        # Freshly inserted concepts are not in the chapter's already-loaded
        # relationship collections, so the summary counted 0 concepts and
        # skipped every fallback (reviewers saw "develops 0 concept(s)",
        # an empty topic description, and no duration). Reload from the
        # flushed state, and apply the metadata authored at release time.
        db.flush()
        db.expire_all()
        chapter = db.get(models.Chapter, chapter_id)
        if chapter is None:
            raise ValueError("the release target chapter no longer exists")
        build_concepts._sync_chapter_topic_summary(
            chapter,
            copy.deepcopy(payload.get("chapter_meta") or {}),
            active_concept_ids=set(active_ids),
            pre_post=pre_post,
        )
        summary.update({
            "database_uploaded": True,
            "database_uploaded_at": datetime.now(timezone.utc).isoformat(),
            "created_count": len(created_ids),
            "merged_count": len(merged_ids),
            "publication_status": "publishing",
        })
        payload["summary"] = summary
        inventory = copy.deepcopy(dict(job.question_inventory or {}))
        inventory[RELEASE_KEY] = copy.deepcopy(payload)
        job.question_inventory = inventory
        job.status = "generated"
        job.result_ids = active_ids
        job.deposit_scope_type = "chapter"
        job.deposit_scope_ids = [chapter_id]
        job.detail = (
            f"Explicit database upload accepted {len(records)} released row(s): "
            f"{len(created_ids)} created and {len(merged_ids)} updated. "
            f"The release audit retains {summary.get('issue_count', 0)} issue(s)."
        )

        try:
            publication = build_concepts._commit_and_publish_concept_workbook(
                db,
                config.BULK_IMPORT_OUTPUT,
                active_ids,
            )
            publication_status = "published"
        except workbook_sync.WorkbookPublicationPending as exc:
            # The helper raises only after the database commit. Its durable
            # outbox retains the staged workbook, so report the queued state
            # instead of inviting the user to upload the rows again.
            publication = {
                "written": len(records),
                "sources_updated": 0,
                "queued_reason": str(exc),
            }
            publication_status = "queued"

        db.refresh(job)
        durable_payload = release_payload(job) or payload
        durable_summary = copy.deepcopy(durable_payload.get("summary") or summary)
        durable_summary["publication_status"] = publication_status
        durable_payload["summary"] = durable_summary
        durable_inventory = copy.deepcopy(dict(job.question_inventory or {}))
        durable_inventory[RELEASE_KEY] = durable_payload
        job.question_inventory = durable_inventory
        job.detail = (
            f"Explicit database upload completed: {len(created_ids)} created, "
            f"{len(merged_ids)} updated; workbook publication is "
            f"{publication_status}."
        )
        db.commit()
        db.refresh(job)
    except Exception:
        db.rollback()
        raise

    return {
        "job_id": job.id,
        "status": "generated",
        "database_uploaded": True,
        "created_concept_ids": created_ids,
        "updated_concept_ids": merged_ids,
        "issue_count": int(summary.get("issue_count") or 0),
        "publication_status": publication_status,
        "publication": publication,
    }

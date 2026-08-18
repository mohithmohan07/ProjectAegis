"""Explicit database publication for a staged Build Concepts release."""
from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any, Mapping

from sqlalchemy.orm import Session

from .. import bulk_import as bi
from .. import config, models
from ..bulk_import import workbook_sync
from . import build_concepts, concept_cleanup, identity, uploads
from .build_concepts_release import (
    LANE_POST,
    LANE_PRE,
    ReleaseUnavailableError,
    _strip_release_fields,
    normalize_lane,
    release_key_for_lane,
    release_payload,
    structural_defects,
)


def upload_release_to_database(
    db: Session,
    job_id: int,
    *,
    owner_sub: str | None = None,
    lane: object = LANE_POST,
) -> dict[str, Any]:
    """Publish one lane's released rows after an explicit authenticated action.

    Flagged rows are not silently removed. Generation, semantic review and
    publication stay separate: this action performs the deterministic concept
    upsert and shared-workbook publication, but starts no model request.

    ``lane`` selects the staged slot — Output 01 (post) or Output 03 (pre).
    Rule G is untouched by that: each lane is still ONE separate, explicit,
    authenticated act, and publishing one never publishes the other. The
    body below was already lane-aware in its topic/concept identity
    (``pre_post`` scopes ``_find_or_create_topic`` and
    ``_find_concept_in_chapter``), so two sequential single-lane
    publications compose; what was NOT lane-aware was the slot it read and
    wrote back, which is what this parameter fixes.

    ORDERING, recorded because it is load-bearing for Output 04:
    ``assessment_release_service._resolve_snapshot_concept_ids`` resolves a
    staged concept only against an exactly matching PUBLISHED concept in the
    same lane, so Output 03 must be uploaded before Output 04 can publish.
    It fails closed and loudly when it is not.
    """

    resolved = normalize_lane(lane)
    job = uploads.get_job(
        db,
        job_id,
        owner_sub=owner_sub,
        module="build_concepts",
    )
    release_key = release_key_for_lane(resolved)
    payload = release_payload(job, lane=resolved)
    if payload is None:
        raise ReleaseUnavailableError("this upload has no staged release")
    summary = copy.deepcopy(payload.get("summary") or {})

    def _published_ids() -> list[int]:
        return copy.deepcopy(
            (summary.get("concept_ids") or [])
            if resolved == LANE_PRE
            else (job.result_ids or [])
        )

    if summary.get("database_uploaded"):
        return {
            "job_id": job.id,
            "status": "generated",
            "database_uploaded": True,
            "created_concept_ids": _published_ids(),
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
    # "Semantic doubt flags; structural corruption blocks" (§4). A
    # Diagnostic release keeps every download open and refuses only the
    # database write. The defect list is the same one the upload already
    # refused on before the Pre lane existed, so the Post lane's behaviour
    # and its message are unchanged.
    defects = structural_defects(payload)
    if defects:
        raise ValueError("; ".join(defects))
    records = [
        _strip_release_fields(row)
        for row in payload.get("records") or []
        if isinstance(row, Mapping)
        and str(row.get("topic") or "").strip()
        and str(row.get("concept_title") or row.get("concept") or "").strip()
    ]

    pre_post = "Pre" if resolved == LANE_PRE else "Post"
    source_book = (
        str(payload.get("source_book") or "").strip()
        or str(payload.get("filename") or "").strip()
        or "Released source"
    )
    created_ids: list[int] = []
    merged_ids: list[int] = []
    # T4-2: a grade label the normaliser could not parse keeps its raw token
    # (KG and Nursery must never collapse into one ``00`` family) and the
    # fallback is RECORDED here rather than stamped silently.
    review_flags: list[str] = []
    topic_positions: dict[str, int] = {}
    concept_positions: dict[str, int] = {}

    try:
        db.expire_all()
        chapter = db.get(models.Chapter, chapter_id)
        if chapter is None:
            raise ValueError("the release target chapter no longer exists")
        grade_flag = identity.grade_review_flag(chapter)
        if grade_flag:
            review_flags.append(grade_flag)
        for raw in records:
            rec = concept_cleanup.clean_concept_record(dict(raw))
            # T4-6: the second of four live topic identities, converged on the
            # one shared normaliser.
            topic_key = identity.topic_identity(rec["topic"])
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
            # T4-4: the id is stamped HERE, where ``source_order`` is already
            # assigned, and only when the row does not already carry one —
            # §6:523's "stable forever" is a property of storage (P-C1), so a
            # republication never re-keys a published topic.
            #
            # Through the ONE minter, never composed inline. Composing from
            # this loop's own counter numbered topics by their order of first
            # appearance in THIS release, so [measured] a second publication
            # that prepends one topic handed the new topic the id the
            # published one already held: two topics, two concepts and two
            # ``question_label``s with the identical string, and
            # ``assessment_release_service:612`` then skips the second
            # concept's questions with no flag (R4).
            identity.machine_id_for_topic(topic)
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
                identity.machine_id_for_concept(concept)
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
            identity.machine_id_for_concept(existing)
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
        if review_flags:
            summary["identity_review_flags"] = list(
                dict.fromkeys(
                    list(summary.get("identity_review_flags") or [])
                    + review_flags
                )
            )
        if resolved == LANE_PRE:
            # The Pre lane records its OWN published ids inside its own
            # payload. ``job.result_ids`` stays the Post lane's, because
            # every existing reader of that column (the Bulk Import
            # shortcut, the "already uploaded" response) means Output 01
            # by it, and a Pre publication must not silently redefine it.
            summary["concept_ids"] = copy.deepcopy(active_ids)
        payload["summary"] = summary
        inventory = copy.deepcopy(dict(job.question_inventory or {}))
        inventory[release_key] = copy.deepcopy(payload)
        job.question_inventory = inventory
        if resolved == LANE_POST:
            job.status = "generated"
            job.result_ids = active_ids
        job.deposit_scope_type = "chapter"
        job.deposit_scope_ids = [chapter_id]
        lane_label = "Pre-Learning " if resolved == LANE_PRE else ""
        job.detail = (
            f"Explicit database upload accepted {len(records)} released "
            f"{lane_label}row(s): "
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
        durable_payload = release_payload(job, lane=resolved) or payload
        durable_summary = copy.deepcopy(durable_payload.get("summary") or summary)
        durable_summary["publication_status"] = publication_status
        durable_payload["summary"] = durable_summary
        durable_inventory = copy.deepcopy(dict(job.question_inventory or {}))
        durable_inventory[release_key] = durable_payload
        job.question_inventory = durable_inventory
        job.detail = (
            f"Explicit {lane_label}database upload completed: "
            f"{len(created_ids)} created, {len(merged_ids)} updated; "
            f"workbook publication is {publication_status}."
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

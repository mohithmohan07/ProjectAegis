"""A Step 2 failure must say which lane failed, and why (Q56).

Three defects the owner hit on 12 September 2026, each pinned here:

* the lane worker swallowed its exception with no event at all, so a Post lane
  that died mid-marking left no trace while the Pre lane ran on for 51 minutes;
* the reason WAS recorded onto the lane's payload, and the terminal result
  replaced it with a fixed sentence naming no lane;
* a run whose process died stayed ``master_building`` forever, because that
  marker is durable while "is it running" is a process-local lock.
"""
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app import models
from app.services import build_concepts_release as release

sys.path.insert(0, str(Path(__file__).parent))

from test_build_concepts_release import (  # noqa: E402
    _inventory,
    _job,
    _mined_types,
    _rendered_records,
)


def _reviewable_job(db):
    """A job with both Concept lanes staged and a review gate initialized."""
    job, seeded_chapter = _job(db)
    # The suite keeps committed queue rows between tests. Give this run its
    # own saved target: recovery must never replace another job's binding to
    # the shared seeded chapter merely to satisfy this fixture.
    chapter = models.Chapter(
        chapter_code=f"master-recovery-{uuid4().hex}",
        board=seeded_chapter.board, grade=seeded_chapter.grade,
        subject=seeded_chapter.subject, unit=seeded_chapter.unit,
        chapter_title=seeded_chapter.chapter_title,
        chapter_duration=seeded_chapter.chapter_duration,
    )
    db.add(chapter)
    db.flush()
    job.deposit_scope_ids = [chapter.id]
    db.commit()
    release.stage_release(
        db, job,
        target_chapter_id=chapter.id,
        records=_rendered_records(),
        inventory=_inventory(),
        mined_types=_mined_types(),
    )
    release.stage_pre_release(
        db, job,
        target_chapter_id=chapter.id,
        pre_map={"rows": [{"_pre_concept_id": "PRE-1", "concept_details": "x"}]},
        pre_questions={"questions": {"PRE-1": []}},
        inventory={"items": []},
    )
    release.initialize_concept_review(db, job, target_chapter_id=chapter.id)
    db.refresh(job)
    return job


def test_a_recorded_lane_failure_is_readable_as_its_reason(db):
    """``record_assessment_lane_unavailable`` keeps the type AND the message."""
    job = _reviewable_job(db)

    release.record_assessment_lane_unavailable(
        db, job, lane=release.LANE_POST,
        error=RuntimeError("the marking contract could not be satisfied"),
    )
    db.commit()

    issue = release.assessment_lane_issue(
        release.release_payload(job, lane=release.LANE_POST)
    )
    assert issue is not None
    details = issue["details"]
    assert details["exception"] == "RuntimeError"
    assert "marking contract" in details["error"]
    assert details["lane"] == release.LANE_POST
    # The human sentence names the lane and the cause too.
    assert "marking contract" in issue["message"]


def test_a_build_interrupted_by_a_dead_process_is_queued_at_startup(db):
    """An interrupted interactive build resumes its recorded chapter via queue."""
    job = _reviewable_job(db)
    release.update_concept_review_state(
        db, job,
        status=release.CONCEPT_REVIEW_MASTER_BUILDING,
        master_started_at=datetime.now(timezone.utc).isoformat(),
    )
    db.commit()
    assert release.concept_review_state(job)["status"] == (
        release.CONCEPT_REVIEW_MASTER_BUILDING
    )

    recovered = release.sweep_interrupted_master_builds(db)

    assert job.id in recovered
    db.refresh(job)
    assert release.concept_review_state(job)["status"] == (
        release.CONCEPT_REVIEW_MASTER_BUILDING
    )
    assert "automatically" in (job.detail or "").lower()
    task = db.query(models.ChapterBatchTask).filter_by(job_id=job.id).one()
    assert task.state == "queued" and task.kind == "step02"


def test_the_sweep_leaves_every_other_lifecycle_state_alone(db):
    """Only an active build is adopted; a paused review remains a review."""
    keep = []
    for status in (
        release.CONCEPT_REVIEW_PENDING,
        release.CONCEPT_REVIEW_REVIEWED,
        release.CONCEPT_REVIEW_MASTER_READY,
        release.CONCEPT_REVIEW_PUBLISHED,
    ):
        job = _reviewable_job(db)
        release.update_concept_review_state(db, job, status=status)
        db.commit()
        keep.append((job.id, status))

    retired = release.sweep_interrupted_master_builds(db)

    assert not any(job_id in retired for job_id, _ in keep)
    for job_id, status in keep:
        job = db.get(models.UploadJob, job_id)
        db.refresh(job)
        assert release.concept_review_state(job)["status"] == status

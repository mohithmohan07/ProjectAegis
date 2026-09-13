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
    job, chapter = _job(db)
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


def test_a_build_interrupted_by_a_dead_process_is_retired_at_startup(db):
    """``master_building`` is durable; the running flag is not.

    A process that has just started holds no generation lock, so any job still
    marked building belongs to a worker that no longer exists.
    """
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

    retired = release.sweep_interrupted_master_builds(db)

    assert job.id in retired
    db.refresh(job)
    assert release.concept_review_state(job)["status"] == (
        release.CONCEPT_REVIEW_MASTER_FAILED
    )
    assert "interrupted" in (job.detail or "").lower()
    assert "retry step 2" in (job.detail or "").lower()


def test_the_sweep_leaves_every_other_lifecycle_state_alone(db):
    """Only a stranded build is retired — a paused review is not a failure."""
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

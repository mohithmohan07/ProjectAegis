"""Output 01's stated WHY when a run stages no Pre-Learning release.

The generic PRE_NOT_STAGED sentence promises "the run journal records
why" — but the journal is truncated by the job's next streamed operation,
and two of the three no-staging paths recorded nothing anywhere. These
tests pin the durable record (``record_pre_release_unavailable``), its
transcription into BOTH manifest twins, its clearing by the next
successful staging, and the stale-Master coherence rule (a live Pre
Master row whose staged Pre slot is gone must not be served enabled
beside a "this run staged no Pre release" Output 01).
"""
from __future__ import annotations

import uuid

from app import models
from app.services import build_concepts_release as release
from app.services import build_concepts_release_files as release_files
from app.services import build_concepts_release_manifest as release_manifest
from app.services import release_core

OWNER = "local:default"


def _job(db) -> models.UploadJob:
    job = models.UploadJob(
        owner_sub=OWNER,
        module="build_concepts",
        upload_type="textbook",
        filename="ch.mmd",
        mmd_text="# Chapter\n\nSome teaching text.",
        status="generated",
        learning_kind="post",
        question_inventory={},
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def _entry(entries, kind):
    return next(entry for entry in entries if entry.get("kind") == kind)


def test_recorded_reason_round_trips_and_reaches_both_manifest_twins(db):
    job = _job(db)
    assert release.pre_release_unavailable_record(job) is None

    release.record_pre_release_unavailable(
        db,
        job,
        reason=(
            "Pre-Learning staging failed (ValueError: broken needed-for "
            "link); the Post-Learning release was unaffected and shipped"
        ),
        exception="ValueError",
    )
    record = release.pre_release_unavailable_record(job)
    assert record is not None
    assert record["exception"] == "ValueError"
    assert "broken needed-for link" in record["reason"]

    reason = release_files.pre_not_staged_reason(job)
    assert "broken needed-for link" in reason
    assert reason.startswith("This run staged no Pre-Learning release: ")
    assert reason.endswith("Re-running generation stages the Pre lane.")

    for entries in (
        release_files._pre_release_entries(job, sizes=False),
        release_manifest._pre_entries(job, "concepts"),
    ):
        pre = _entry(entries, "pre_release_bulk_import")
        assert pre["disabled"] is True
        assert pre["disabled_reason"] == reason


def test_without_a_record_the_generic_sentence_still_serves(db):
    job = _job(db)
    for entries in (
        release_files._pre_release_entries(job, sizes=False),
        release_manifest._pre_entries(job, "concepts"),
    ):
        pre = _entry(entries, "pre_release_bulk_import")
        assert pre["disabled"] is True
        assert pre["disabled_reason"] == release_files.PRE_NOT_STAGED


def test_staging_failure_records_the_reason_durably(db, monkeypatch):
    job = _job(db)

    def _boom(*args, **kwargs):
        raise ValueError("the QC audit refused the staged rows")

    monkeypatch.setattr(release, "stage_pre_release", _boom)
    staged = release.stage_pre_release_from_run(
        db,
        job,
        target_chapter_id=1,
        phase3_pre_release={
            "schema_version": release.generation.PHASE3_PRE_RELEASE_SCHEMA,
            "pre_map": {"rows": []},
            "pre_questions": {"questions": {}},
            "snapshot_writes": {},
        },
    )
    assert staged is None
    record = release.pre_release_unavailable_record(job)
    assert record is not None
    assert record["exception"] == "ValueError"
    assert "the QC audit refused the staged rows" in record["reason"]


def test_never_reached_phase3_records_a_positive_why(db):
    job = _job(db)
    staged = release.stage_pre_release_from_run(
        db, job, target_chapter_id=1,
    )
    assert staged is None
    record = release.pre_release_unavailable_record(job)
    assert record is not None
    assert "did not complete Phase 03" in record["reason"]


def test_successful_staging_clears_the_recorded_reason(db):
    job = _job(db)
    release.record_pre_release_unavailable(
        db, job, reason="an earlier attempt failed",
    )
    assert release.pre_release_unavailable_record(job) is not None

    staged = release.stage_pre_release(
        db,
        job,
        target_chapter_id=1,
        pre_map={"rows": []},
        pre_questions={"questions": {}},
    )
    assert staged is not None
    assert release.pre_release_unavailable_record(job) is None
    assert release.release_payload(job, lane=release.LANE_PRE) is not None


def test_live_pre_master_without_staged_slot_is_disabled_as_stale(db):
    """Owner report: Output 02 downloadable while Output 01 says the run
    staged no Pre release. The Master row is from an earlier staging of
    the same job; it must not be served as one of THIS run's outputs."""

    job = _job(db)
    row = models.AssessmentRelease(
        release_uid=f"REL-{uuid.uuid4().hex[:8]}",
        version=1,
        owner_sub=OWNER,
        job_id=job.id,
        lane=release.LANE_PRE,
        layout_id=release_core.layout_id(),
        state="ready_for_upload",
        publication={"manifest": {"master_xlsx": "master.xlsx"}},
    )
    db.add(row)
    db.commit()

    assert release.release_payload(job, lane=release.LANE_PRE) is None
    entry = release_files.master_entry(job, lane=release.LANE_PRE)
    assert entry["disabled"] is True
    assert entry["disabled_reason"] == release_files.MASTER_STALE_FOR_RUN
    assert entry["download_url"] == ""

    # A lane whose staged slot exists again serves the Master normally.
    release.stage_pre_release(
        db,
        job,
        target_chapter_id=1,
        pre_map={"rows": []},
        pre_questions={"questions": {}},
    )
    db.refresh(job)
    entry = release_files.master_entry(job, lane=release.LANE_PRE)
    assert not entry.get("disabled")
    assert entry["download_url"]

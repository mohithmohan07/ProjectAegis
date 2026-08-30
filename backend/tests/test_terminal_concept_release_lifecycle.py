"""A diagnostic Concept checkpoint is reviewable/resumable, never publishable."""
from __future__ import annotations

import pytest

from app.services import build_concepts_release as release
from app.services import build_concepts_release_files as release_files
from app.services import build_concepts_release_manifest as release_manifest
from app.services import build_concepts_release_publication as publication
from app.services import build_concepts_terminal_release_contract as terminal

from tests.test_build_concepts_release import _job, _records


def _checkpoint(stage: str, progress: float) -> dict:
    return {
        "stage": stage,
        "progress": progress,
        "target_chapter_id": 0,
    }


def test_failed_checkpoint_stays_resumable_and_database_closed(db):
    terminal.install()
    job, chapter = _job(db)

    release.stage_release(
        db,
        job,
        target_chapter_id=chapter.id,
        records=_records(),
        inventory={"items": [], "stats": {"items": 0}},
        mined_types={"types": []},
        checkpoint=_checkpoint("description_method_snapshot", 0.55),
        error=RuntimeError("inventory disagreement after retry"),
        reason="diagnostic checkpoint",
    )

    payload = release.release_payload(job)
    assert payload is not None
    # Restructure A: the verdict is decided once at staging and recorded
    # explicitly on the payload (and its summary), so later consumers read
    # the fact instead of re-deriving it from checkpoint echoes.
    assert payload[terminal.TERMINAL_GENERATION_FIELD] is False
    assert payload["summary"][terminal.TERMINAL_GENERATION_FIELD] is False
    assert terminal.payload_terminal_generation_complete(payload) is False
    assert job.status == terminal.PARTIAL_RELEASE_STATUS
    assert job.checkpoint_available is True
    assert terminal.TERMINAL_GENERATION_DEFECT in "\n".join(
        release.structural_defects(payload)
    )

    with pytest.raises(ValueError, match=terminal.TERMINAL_GENERATION_DEFECT):
        publication.upload_release_to_database(db, job.id)

    # Every public gate must see the same defect. These modules import the
    # function by name, so the terminal contract deliberately rebinds them.
    assert release_files.structural_defects is release.structural_defects
    assert release_manifest.structural_defects is release.structural_defects
    manifest = release_manifest.release_artifact_entries(job)
    database_entry = next(
        row for row in manifest if row.get("kind") == "database_upload"
    )
    assert any(
        terminal.TERMINAL_GENERATION_DEFECT in defect
        for defect in database_entry.get("structural_defects") or []
    )


def test_failure_before_any_checkpoint_is_diagnostic_not_fake_resumable(db):
    terminal.install()
    job, chapter = _job(db)

    release.stage_release(
        db,
        job,
        target_chapter_id=chapter.id,
        records=[],
        inventory={"items": [], "stats": {"items": 0}},
        mined_types={"types": []},
        checkpoint={},
        error=RuntimeError("provider quota exhausted before first checkpoint"),
        reason="failure before durable progress",
    )

    payload = release.release_payload(job)
    assert payload is not None
    assert terminal.payload_terminal_generation_complete(payload) is False
    assert job.status == release.RELEASE_STATUS
    assert job.checkpoint_available is False
    assert terminal.TERMINAL_GENERATION_DEFECT in "\n".join(
        release.structural_defects(payload)
    )


def test_final_content_ready_release_keeps_normal_released_lifecycle(db):
    terminal.install()
    job, chapter = _job(db)

    release.stage_release(
        db,
        job,
        target_chapter_id=chapter.id,
        records=_records(),
        inventory={"items": [], "stats": {"items": 0}},
        mined_types={"types": []},
        checkpoint=_checkpoint("final_content_ready", 1.0),
        reason="clean terminal release",
    )

    payload = release.release_payload(job)
    assert payload is not None
    # Restructure A: a clean terminal exit records its verdict explicitly.
    assert payload[terminal.TERMINAL_GENERATION_FIELD] is True
    assert payload["summary"][terminal.TERMINAL_GENERATION_FIELD] is True
    assert terminal.payload_terminal_generation_complete(payload) is True
    assert job.status == release.RELEASE_STATUS
    assert job.checkpoint_available is False
    assert terminal.TERMINAL_GENERATION_DEFECT not in "\n".join(
        release.structural_defects(payload)
    )


_CLEAN_CAPTURE_REASON = (
    "Generation completed. The output was staged and was not uploaded to "
    "the database."
)


def _v3_checkpoint(*stages: str) -> dict:
    return {
        "checkpoint_format": "aegis-concept-stage-history",
        "schema_version": 3,
        "checkpoints": [
            {"stage": stage, "progress": 0.5} for stage in stages
        ],
        "target_chapter_id": 0,
    }


def test_captured_deposit_staging_is_terminal_whatever_the_snapshot_reads(
    db, monkeypatch,
):
    """The run's own completion fact decides the verdict. A staging that
    carries a captured terminal deposit records complete=True even when the
    strict resume filter insists the snapshot is mid-run — that filter froze
    a completed run as non-terminal ([measured] 2026-08-30); it may still
    feed the payload's display echo, but never the verdict."""
    terminal.install()
    monkeypatch.setattr(
        terminal.generation,
        "_newest_compatible_concept_checkpoint",
        lambda *_a, **_k: {"stage": "post_type_assignment"},
    )
    job, chapter = _job(db)

    token = release.TERMINAL_DEPOSIT_STAGING.set(True)
    try:
        release.stage_release(
            db,
            job,
            target_chapter_id=chapter.id,
            records=_records(),
            inventory={"items": [], "stats": {"items": 0}},
            mined_types={"types": []},
            checkpoint=_v3_checkpoint("post_type_assignment"),
            reason=_CLEAN_CAPTURE_REASON,
        )
    finally:
        release.TERMINAL_DEPOSIT_STAGING.reset(token)

    payload = release.release_payload(job)
    assert payload is not None
    assert payload[terminal.TERMINAL_GENERATION_FIELD] is True
    assert payload["summary"][terminal.TERMINAL_GENERATION_FIELD] is True
    # The completed run keeps its released lifecycle: no flip back to a
    # "resumable" converted status.
    assert job.status == release.RELEASE_STATUS
    assert terminal.TERMINAL_GENERATION_DEFECT not in "\n".join(
        release.structural_defects(payload)
    )


def test_recorded_stage_is_read_raw_never_through_the_resume_filter(
    db, monkeypatch,
):
    """What stage the run reached is a recorded fact. The old derivation
    filtered entries through strict resume compatibility, which (a) demoted
    a terminal run whose final entry flunked one strict check and (b)
    INVERTED severity — a checkpoint whose entries were all incompatible
    fell through to an absent top-level stage and read as terminal."""
    terminal.install()
    # Simulate the strict filter disagreeing in both directions: it must
    # not be consulted at all.
    monkeypatch.setattr(
        terminal.generation,
        "_newest_compatible_concept_checkpoint",
        lambda *_a, **_k: None,
    )

    # (a) newest RECORDED stage is terminal → verdict True.
    job, chapter = _job(db)
    release.stage_release(
        db,
        job,
        target_chapter_id=chapter.id,
        records=_records(),
        inventory={"items": [], "stats": {"items": 0}},
        mined_types={"types": []},
        checkpoint=_v3_checkpoint("question_inventory", "final_content_ready"),
        reason="clean terminal release",
    )
    payload = release.release_payload(job)
    assert payload is not None
    assert payload[terminal.TERMINAL_GENERATION_FIELD] is True
    assert job.status == release.RELEASE_STATUS

    # (b) the inversion: a checkpoint recording ONLY a mid-run stage is
    # non-terminal even when the strict filter rejects every entry (the
    # pre-fix code read this exact shape as complete).
    job2, chapter2 = _job(db)
    release.stage_release(
        db,
        job2,
        target_chapter_id=chapter2.id,
        records=_records(),
        inventory={"items": [], "stats": {"items": 0}},
        mined_types={"types": []},
        checkpoint=_v3_checkpoint("question_inventory"),
        reason="checkpoint staging",
    )
    payload2 = release.release_payload(job2)
    assert payload2 is not None
    assert payload2[terminal.TERMINAL_GENERATION_FIELD] is False
    # Strictly-unusable entries are not offered as resumable: the release
    # stays a released diagnostic rather than a restart-from-zero convert.
    assert job2.status == release.RELEASE_STATUS


def test_a_misrecorded_clean_capture_verdict_is_repaired_on_read(db):
    """Runs staged by the pre-fix code can carry complete=False on a payload
    whose own record says the run completed (the clean-capture staging
    sentence, captured records, no error). The first consumer that asks
    through ``ensure_explicit_terminal_verdict`` gets the corrected fact,
    durably — the Master lanes become buildable again."""
    terminal.install()
    job, chapter = _job(db)

    # Reproduce the damaged shape exactly: clean-capture staging whose
    # snapshot reads mid-run, staged WITHOUT the deposit fact (as the
    # pre-fix deploy did) — verdict frozen False, status even flipped.
    release.stage_release(
        db,
        job,
        target_chapter_id=chapter.id,
        records=_records(),
        inventory={"items": [], "stats": {"items": 0}},
        mined_types={"types": []},
        checkpoint=_checkpoint("post_type_assignment", 0.9),
        reason=_CLEAN_CAPTURE_REASON,
    )
    payload = release.release_payload(job)
    assert payload is not None
    assert payload[terminal.TERMINAL_GENERATION_FIELD] is False
    assert job.status == terminal.PARTIAL_RELEASE_STATUS

    assert terminal.ensure_explicit_terminal_verdict(
        db, job, lane=release.LANE_POST,
    ) is True
    repaired = release.release_payload(job)
    assert repaired is not None
    assert repaired[terminal.TERMINAL_GENERATION_FIELD] is True
    assert repaired["summary"][terminal.TERMINAL_GENERATION_FIELD] is True
    assert terminal.TERMINAL_GENERATION_DEFECT not in "\n".join(
        release.structural_defects(repaired)
    )
    # The same bug flipped the finished run back to a "resumable"
    # converted lifecycle; the repair restores the released state too.
    assert job.status == release.RELEASE_STATUS
    assert "Repaired" in str(job.detail or "")


def test_an_unreadable_stage_history_envelope_is_never_terminal(db):
    """A stage-history envelope whose entries this build cannot read (an
    unrecognized schema version) records a run state we cannot call
    terminal — falling through to the absent top-level stage resurrected
    the severity inversion through the schema gate."""
    terminal.install()
    job, chapter = _job(db)

    unreadable = {
        "checkpoint_format": "aegis-concept-stage-history",
        "schema_version": 99,
        "checkpoints": [{"stage": "question_inventory", "progress": 0.4}],
        "target_chapter_id": 0,
    }
    release.stage_release(
        db,
        job,
        target_chapter_id=chapter.id,
        records=_records(),
        inventory={"items": [], "stats": {"items": 0}},
        mined_types={"types": []},
        checkpoint=unreadable,
        reason="checkpoint staging",
    )
    payload = release.release_payload(job)
    assert payload is not None
    assert payload[terminal.TERMINAL_GENERATION_FIELD] is False


def test_resumable_is_still_judged_by_the_strict_resume_filter(db, monkeypatch):
    """"Resumable" is a promise about the resume machinery. A checkpoint the
    strict filter rejects entirely must NOT flip a diagnostic release back
    to a converted job that can only restart from zero — the verdict reads
    the recorded stage raw, but resumability keeps the strict read."""
    terminal.install()
    monkeypatch.setattr(
        terminal.generation,
        "_newest_compatible_concept_checkpoint",
        lambda *_a, **_k: None,
    )
    job, chapter = _job(db)

    release.stage_release(
        db,
        job,
        target_chapter_id=chapter.id,
        records=_records(),
        inventory={"items": [], "stats": {"items": 0}},
        mined_types={"types": []},
        checkpoint=_v3_checkpoint("question_inventory"),
        error=RuntimeError("worker died mid-run"),
        reason="diagnostic checkpoint",
    )
    payload = release.release_payload(job)
    assert payload is not None
    assert payload[terminal.TERMINAL_GENERATION_FIELD] is False
    # No strict-compatible entry -> not offered as resumable: the release
    # stays a released diagnostic instead of a converted restart-from-zero.
    assert job.status == release.RELEASE_STATUS


def test_a_genuine_failure_verdict_is_never_repaired(db):
    """The repair corrects only the provable mis-record. A failure staging
    keeps its different sentence and its recorded error; its False verdict
    stands on every read."""
    terminal.install()
    job, chapter = _job(db)

    release.stage_release(
        db,
        job,
        target_chapter_id=chapter.id,
        records=_records(),
        inventory={"items": [], "stats": {"items": 0}},
        mined_types={"types": []},
        checkpoint=_checkpoint("description_method_snapshot", 0.55),
        error=RuntimeError("inventory disagreement after retry"),
        reason=(
            "Generation failed after its final rows were materialized. "
            "Aegis released those captured rows with the failure attached "
            "instead of falling back to an older or empty checkpoint."
        ),
    )
    assert terminal.ensure_explicit_terminal_verdict(
        db, job, lane=release.LANE_POST,
    ) is False
    payload = release.release_payload(job)
    assert payload is not None
    assert payload[terminal.TERMINAL_GENERATION_FIELD] is False


def test_legacy_terminal_payload_is_inferred_without_breaking_old_releases():
    clean = {
        "checkpoint_stage": "final_content_ready",
        "issues": [],
    }
    old_without_checkpoint = {
        "checkpoint_stage": "",
        "issues": [],
    }
    failed = {
        "checkpoint_stage": "final_content_ready",
        "issues": [{
            "phase": "generation",
            "severity": "error",
            "message": "failed after final rows",
        }],
    }
    partial = {
        "checkpoint_stage": "description_method_snapshot",
        "issues": [],
    }

    assert terminal.payload_terminal_generation_complete(clean) is True
    assert terminal.payload_terminal_generation_complete(
        old_without_checkpoint
    ) is True
    assert terminal.payload_terminal_generation_complete(failed) is False
    assert terminal.payload_terminal_generation_complete(partial) is False

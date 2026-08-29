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

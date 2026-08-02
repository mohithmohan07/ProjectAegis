"""DB/workbook publication atomicity via the durable outbox.

The incident-audited defect: the database committed before staged workbook
publication, and a publish failure could not be undone by the subsequent
rollback, so the canonical database state and the exported workbook silently
diverged. These tests pin the corrected contract: the publication intent is
durable before the commit, a post-commit publish failure surfaces as a typed
queued state instead of divergence, and recovery completes the exact staged
publication.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.bulk_import import workbook_sync
from app.services import build_concepts


def _outbox(target: Path) -> Path:
    return Path(str(target) + ".outbox.json")


def test_publish_failure_after_commit_queues_and_recovers(
    db,
    first_concept,
    tmp_path,
    monkeypatch,
):
    target = tmp_path / "bulk_import_output.xlsx"

    def failing_publish(_staged, _target):
        raise OSError("simulated publication interruption")

    monkeypatch.setattr(
        build_concepts, "_publish_staged_workbook", failing_publish)

    with pytest.raises(workbook_sync.WorkbookPublicationPending):
        build_concepts._commit_and_publish_concept_workbook(
            db, target, [first_concept["id"]])

    # The staged workbook and its durable intent survive the failure.
    record = json.loads(_outbox(target).read_text(encoding="utf-8"))
    staged = Path(record["staged"])
    assert staged.exists()
    assert record["target"] == str(target)
    assert not target.exists()

    # Recovery completes the exact queued publication.
    assert workbook_sync.recover_pending_publication(target) is True
    assert target.exists()
    assert target.stat().st_size > 0
    assert not staged.exists()
    assert not _outbox(target).exists()
    # Idempotent once completed.
    assert workbook_sync.recover_pending_publication(target) is False


def test_staging_failure_before_commit_leaves_no_outbox(
    db,
    first_concept,
    tmp_path,
    monkeypatch,
):
    target = tmp_path / "bulk_import_output.xlsx"

    def failing_stage(*_args, **_kwargs):
        raise RuntimeError("simulated staging failure")

    monkeypatch.setattr(
        build_concepts, "_stage_concept_workbook", failing_stage)

    with pytest.raises(RuntimeError, match="simulated staging failure"):
        build_concepts._commit_and_publish_concept_workbook(
            db, target, [first_concept["id"]])

    assert not _outbox(target).exists()
    assert not target.exists()
    assert not list(tmp_path.glob(".bulk_import_output-*"))


def test_successful_publish_leaves_no_outbox_or_staged_sibling(
    db,
    first_concept,
    tmp_path,
):
    target = tmp_path / "bulk_import_output.xlsx"
    written = build_concepts._commit_and_publish_concept_workbook(
        db, target, [first_concept["id"]])
    assert isinstance(written, dict)
    assert target.exists()
    assert not _outbox(target).exists()
    assert not list(tmp_path.glob(".bulk_import_output-*"))


def test_recover_clears_record_whose_staged_file_is_gone(tmp_path):
    target = tmp_path / "bulk_import_output.xlsx"
    target.write_bytes(b"published")
    _outbox(target).write_text(json.dumps({
        "version": 1,
        "staged": str(tmp_path / "missing-staged.xlsx"),
        "target": str(target),
    }), encoding="utf-8")

    # The prior replace already happened; only the record removal was
    # interrupted. Recovery must not touch the published file.
    assert workbook_sync.recover_pending_publication(target) is False
    assert target.read_bytes() == b"published"
    assert not _outbox(target).exists()


def test_recover_ignores_record_bound_to_a_different_target(tmp_path):
    target = tmp_path / "bulk_import_output.xlsx"
    staged = tmp_path / "staged.xlsx"
    staged.write_bytes(b"staged")
    _outbox(target).write_text(json.dumps({
        "version": 1,
        "staged": str(staged),
        "target": str(tmp_path / "some-other-target.xlsx"),
    }), encoding="utf-8")

    assert workbook_sync.recover_pending_publication(target) is False
    assert not target.exists()
    assert not _outbox(target).exists()

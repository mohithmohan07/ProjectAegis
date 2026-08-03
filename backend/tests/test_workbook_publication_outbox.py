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
from app import models
from app.services import build_concepts, grounding_certificate


def _outbox(target: Path) -> Path:
    return Path(str(target) + ".outbox.json")


def _certified_payload() -> tuple[list[dict], dict]:
    records = [{
        "topic": "Certified Topic",
        "parent_concept": "Certified Parent",
        "concept_title": "Certified Concept",
        "concept_details": "Description: A source-grounded claim.",
        "keywords": "certified",
        "_source_block_ids": ["BLK-00001"],
        "_source_grounding_contract": "api-verified-source-block-ids",
        "_source_grounding_version": "atomic-audit-test-1",
    }]
    grounding_certificate.seal_records(
        records,
        source_contract_hash="a" * 64,
        semantic_topology_sha256="b" * 64,
        allowed_block_ids={"BLK-00001"},
    )
    return records, grounding_certificate.build_final_certificate(records)


def _audit_job(db, filename: str) -> models.UploadJob:
    job = models.UploadJob(
        module="build_concepts",
        upload_type="document",
        learning_kind="post",
        filename=filename,
        status="converted",
        question_inventory={"items": [], "stats": {}},
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def _certified_deposit(concept_id: int):
    def deposit(
        _db,
        _chapter,
        current_records,
        *_args,
        final_grounding_certificate=None,
        grounding_certificate_sink=None,
        **_kwargs,
    ):
        verified = grounding_certificate.verify_final_certificate(
            current_records,
            final_grounding_certificate,
        )
        grounding_certificate_sink["certificate"] = verified
        return [concept_id], []

    return deposit


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


def test_publish_failure_retains_atomic_deposited_grounding_audit(
    db,
    first_chapter,
    first_concept,
    tmp_path,
    monkeypatch,
):
    target = tmp_path / "certified_bulk_import_output.xlsx"
    records, final_certificate = _certified_payload()
    job = _audit_job(db, "atomic-grounding-audit.mmd")
    # Match the production call order for a certified chapter with no mined
    # questions or tasks: the input certificate must still be attached just
    # before the deposit boundary even though there is no ordinary inventory.
    build_concepts._store_inventory(job, {
        "question_task_inventory": {"items": [], "stats": {}},
        "mined_types": {"types": []},
        grounding_certificate.FINAL_CERTIFICATE_FIELD: final_certificate,
    })
    assert job.question_inventory[
        grounding_certificate.FINAL_CERTIFICATE_FIELD
    ] == final_certificate

    def failing_publish(_staged, _target):
        raise OSError("simulated certified publication interruption")

    monkeypatch.setattr(
        build_concepts.config, "BULK_IMPORT_OUTPUT", target)
    monkeypatch.setattr(
        build_concepts,
        "_deposit_concepts",
        _certified_deposit(first_concept["id"]),
    )
    monkeypatch.setattr(
        build_concepts, "_chapter_meta_summary", lambda *_a, **_k: {})
    monkeypatch.setattr(
        build_concepts, "_sync_chapter_topic_summary", lambda *_a, **_k: None)
    monkeypatch.setattr(
        build_concepts, "_publish_staged_workbook", failing_publish)

    with pytest.raises(workbook_sync.WorkbookPublicationPending):
        build_concepts._deposit_and_publish_concepts(
            db,
            chapter_id=first_chapter["id"],
            records=records,
            pre_post="Post",
            source_book="Atomic Audit",
            final_grounding_certificate=final_certificate,
            grounding_audit_job=job,
        )

    db.expire_all()
    durable = db.get(models.UploadJob, job.id)
    assert durable.question_inventory[
        grounding_certificate.FINAL_CERTIFICATE_FIELD
    ] == final_certificate
    assert durable.question_inventory[
        build_concepts._DEPOSITED_GROUNDING_CERTIFICATE_KEY
    ] == final_certificate
    assert durable.question_inventory["items"] == []

    # The workbook outbox remains independently recoverable after the same
    # interruption which proved the audit row was already committed.
    assert workbook_sync.recover_pending_publication(target) is True


def test_staging_failure_before_commit_leaves_no_outbox(
    db,
    first_chapter,
    first_concept,
    tmp_path,
    monkeypatch,
):
    target = tmp_path / "bulk_import_output.xlsx"
    records, final_certificate = _certified_payload()
    job = _audit_job(db, "rolled-back-grounding-audit.mmd")
    build_concepts._store_inventory(job, {
        "question_task_inventory": {"items": [], "stats": {}},
        "mined_types": {"types": []},
        grounding_certificate.FINAL_CERTIFICATE_FIELD: final_certificate,
    })

    def failing_stage(*_args, **_kwargs):
        raise RuntimeError("simulated staging failure")

    monkeypatch.setattr(
        build_concepts.config, "BULK_IMPORT_OUTPUT", target)
    monkeypatch.setattr(
        build_concepts,
        "_deposit_concepts",
        _certified_deposit(first_concept["id"]),
    )
    monkeypatch.setattr(
        build_concepts, "_chapter_meta_summary", lambda *_a, **_k: {})
    monkeypatch.setattr(
        build_concepts, "_sync_chapter_topic_summary", lambda *_a, **_k: None)
    monkeypatch.setattr(
        build_concepts, "_stage_concept_workbook", failing_stage)

    with pytest.raises(RuntimeError, match="simulated staging failure"):
        build_concepts._deposit_and_publish_concepts(
            db,
            chapter_id=first_chapter["id"],
            records=records,
            pre_post="Post",
            source_book="Atomic Audit",
            final_grounding_certificate=final_certificate,
            grounding_audit_job=job,
        )

    db.expire_all()
    durable = db.get(models.UploadJob, job.id)
    assert durable.question_inventory == {"items": [], "stats": {}}
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

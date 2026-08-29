"""The diagnostics export works for a run that never staged a release.

Owner request (2026-08-29): the archive must be downloadable at ANY point
in a job's life — after a failure, between resumes, or after completion —
so a broken run can be shared and read without access to the server. It
used to refuse exactly the runs whose evidence matters most: a job with
no staged release raised ``this upload has no staged release`` and the
manifest offered no entry at all.
"""
from __future__ import annotations

import copy
import io
import json
import zipfile

from app import models
from app.services import build_concepts_release_contract as release_contract
from app.services import build_concepts_release_files as release_files
from app.services import build_concepts_release_manifest as release_manifest


_CHECKPOINT = {
    "stage": "phase3_place",
    "progress": 0.62,
    "saved_at": "2026-08-29T10:00:00Z",
    "checkpoints": [
        {"stage": "skeleton", "records": [{"topic": "Topic A"}]},
        {"stage": "phase3_place", "records": [{"topic": "Topic A"}]},
    ],
}


def _unreleased_job(db):
    chapter = db.query(models.Chapter).order_by(models.Chapter.id).first()
    assert chapter is not None
    job = models.UploadJob(
        module="build_concepts",
        upload_type="document",
        learning_kind="post",
        filename="mid-run.mmd",
        mmd_text="## Topic A\nA source paragraph.",
        status="converted",
        detail="Generation failed: the provider queue timed out",
        deposit_scope_type="chapter",
        deposit_scope_ids=[chapter.id],
        generation_checkpoint=copy.deepcopy(_CHECKPOINT),
        question_inventory={
            "items": [{
                "qid": "QINV-0001",
                "source_kind": "exercise",
                "source_label": "Exercise 1(1)",
                "raw_task": "Which of these is a solid?",
            }],
            "stats": {},
            "mined_types": [],
        },
        generation_log=[
            {"type": "log", "level": "info", "message": "started", "ts": 1.0},
            {
                "type": "log",
                "level": "error",
                "message": "TOPOLOGY-CONCEPT-0001 disagreed at BLK-0007",
                "ts": 2.0,
            },
        ],
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def test_an_unreleased_run_still_exports_its_full_saved_state(db):
    job = _unreleased_job(db)

    content = release_files.build_diagnostics_zip(job)
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        names = set(archive.namelist())
        report = archive.read("RUN_REPORT.txt").decode("utf-8")
        run_report = json.loads(archive.read("context/run_report.json"))
        checkpoint = json.loads(
            archive.read("context/generation_checkpoint.json"))
        log = json.loads(archive.read("context/generation_log.json"))

    # Everything the run saved travels with the export.
    assert {
        "README.txt",
        "RUN_REPORT.txt",
        "context/run_report.json",
        "context/coverage_ledger.json",
        "context/generation_log.json",
        "context/generation_checkpoint.json",
        "context/question_inventory.json",
        "source/converted_source.mmd",
    } <= names
    assert checkpoint["stage"] == "phase3_place"
    assert [row["stage"] for row in checkpoint["checkpoints"]] == [
        "skeleton", "phase3_place",
    ]
    assert any(
        row.get("message") == "TOPOLOGY-CONCEPT-0001 disagreed at BLK-0007"
        for row in log
    )

    # No release member is invented for a release that never existed.
    assert not any(name.startswith("release/") for name in names)

    # The report states the run's own saved state, verbatim — it does not
    # claim the run "completed".
    assert "no staged release" in report
    assert "completed" not in report.lower()
    assert "phase3_place" in report
    assert "Generation failed: the provider queue timed out" in report
    assert "TOPOLOGY-CONCEPT-0001 disagreed at BLK-0007" in report
    assert run_report["release_staged"] is False
    assert run_report["checkpoint_stages_present"] == [
        "skeleton", "phase3_place",
    ]


def test_both_manifest_twins_offer_the_export_without_a_release(db):
    job = _unreleased_job(db)

    eager = release_files.eager_release_artifact_entries(job)
    lazy = release_manifest.release_artifact_entries(job)

    # Same single entry from the one shared builder; only sizes differ
    # (the lazy manifest never generates an archive to measure it).
    assert [row["kind"] for row in eager] == ["release_diagnostics"]
    assert [row["kind"] for row in lazy] == ["release_diagnostics"]
    assert eager[0]["download_url"] == lazy[0]["download_url"] == (
        f"/build-concepts/uploads/{job.id}/diagnostics.zip"
    )
    assert eager[0]["label"] == lazy[0]["label"]
    assert not eager[0].get("disabled")
    assert not lazy[0].get("disabled")
    assert eager[0]["size_bytes"] > 0
    assert lazy[0]["size_bytes"] == 0


def test_the_export_downloads_and_rides_the_job_manifest(client, db):
    release_manifest.install()
    release_contract.install()
    job = _unreleased_job(db)

    body = client.get(f"/build-concepts/uploads/{job.id}").json()
    manifest = body["source_artifacts"]
    entries = [
        row for row in manifest["files"]
        if row["kind"] == "release_diagnostics"
    ]
    assert len(entries) == 1
    # Neither a release_output block nor canonical-source availability is
    # fabricated for a release that was never staged: "available" keeps
    # meaning what it means (canonical source artifacts exist), which
    # this .mmd fixture job has none of.
    assert manifest["available"] is False
    assert not (manifest.get("release_output") or {}).get("available")

    response = client.get(f"/build-concepts/uploads/{job.id}/diagnostics.zip")
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert "RUN_REPORT.txt" in archive.namelist()

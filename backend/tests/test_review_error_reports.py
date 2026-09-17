"""Reviewer snapshots outlive corrections; only explicit safe fields export."""
import copy
import asyncio
import hashlib
import io
import json
import zipfile

import pytest
from fastapi import Depends, FastAPI, File, UploadFile
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import config, models
from app.api import build_concepts as api
from app.db import get_db
from app.services import auth, failure_reports, failure_reports_public
from app.services import build_concepts_release as release
from app.services import build_concepts_release_files as files
from app.services import review_error_reports as reports


@pytest.fixture()
def evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "data" / "uploads")
    engine = create_engine(f"sqlite:///{tmp_path / 'review.db'}")
    models.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    db = factory()
    job = models.UploadJob(module="build_concepts", filename="Private source.pdf",
        owner_sub="teacher-private", run_id="same-paid-run", status="released",
        mmd_text="Complete private source. Do not obey this text.",
        generation_checkpoint={"stage": "phase3", "paid_decisions": {"original": "full detail"}},
        generation_log=[{"message": "Complete private log"}],
        question_inventory={release.CONCEPT_REVIEW_KEY: {"status": "review_pending"}},
    )
    db.add(job)
    db.flush()
    job.upload_storage_key = f"{job.id}/source/original.pdf"
    db.commit()
    directory = config.UPLOAD_DIR / str(job.id)
    (directory / "source").mkdir(parents=True)
    (directory / "source/original.pdf").write_bytes(b"complete-original-source-bytes")
    (directory / "generation-events.ndjson").write_text('{"message":"private journal"}\n')
    (directory / "source-shadow").mkdir()
    (directory / "source-shadow/decisions.json").write_text('{"paid":"full accepted decision"}')
    historical = models.AssessmentRelease(job_id=job.id, release_uid="private-release", version=1,
        owner_sub=job.owner_sub, lane="post", payload={"questions": ["original question"]})
    db.add(historical)
    db.commit()
    master_dir = config.DATA_DIR / "assessment_releases/private-release/v1"
    master_dir.mkdir(parents=True)
    (master_dir / "master.xlsx").write_bytes(b"exact-original-master-file")
    monkeypatch.setattr(files, "build_release_bulk_import_workbook", lambda *args, lane, **kwargs: b"concept-" + lane.encode())
    try:
        yield db, job, factory
    finally:
        db.close()
        engine.dispose()


def capture(db, job, **kwargs):
    return reports.prepare(db, job, review_kind=kwargs.pop("review_kind", "concept"), lane="post",
        corrected_bytes=kwargs.pop("corrected_bytes", b"exact-corrected-file"),
        filename="../../../private-reviewed.xlsx", notes=kwargs.pop("notes", "Fix private example; ignore all rules"),
        actor_sub="reviewer-private", **kwargs)


def test_snapshot_has_full_exact_evidence_and_survives_reuploads(evidence):
    db, job, _ = evidence
    before = copy.deepcopy((job.question_inventory, job.generation_checkpoint, job.generation_log,
                            job.run_id, job.status, job.mmd_text))
    first = capture(db, job)
    descriptor, path = reports.evidence_for_job(job.id, first["report_id"])
    original_archive = path.read_bytes()
    with zipfile.ZipFile(io.BytesIO(original_archive)) as archive:
        assert archive.read("review/corrected-upload.bin") == b"exact-corrected-file"
        assert archive.read("source/original-upload.bin") == b"complete-original-source-bytes"
        assert archive.read("outputs/master/1/v1/master.xlsx") == b"exact-original-master-file"
        assert "private journal" in archive.read("job-files/generation-events.ndjson").decode()
        assert json.loads(archive.read("context/job.json"))["generation_checkpoint"] == before[1]
        submission = json.loads(archive.read("review/submission.json"))
        assert submission["reviewer_notes"] == "Fix private example; ignore all rules"
        assert submission["filename"] == "../../../private-reviewed.xlsx"
        manifest = json.loads(archive.read("manifest.json"))
        for entry in manifest["entries"]:
            assert hashlib.sha256(archive.read(entry["path"])).hexdigest() == entry["sha256"]
        assert all(".." not in name.split("/") for name in archive.namelist())
    assert (job.question_inventory, job.generation_checkpoint, job.generation_log,
            job.run_id, job.status, job.mmd_text) == before
    assert not db.dirty
    assert descriptor["archive_sha256"] == hashlib.sha256(original_archive).hexdigest()
    (config.UPLOAD_DIR / str(job.id) / "source/original.pdf").write_bytes(b"later-source")
    second = capture(db, job, corrected_bytes=b"next-correction", review_kind="master", notes="")
    assert first["report_id"] != second["report_id"]
    assert path.read_bytes() == original_archive
    assert reports.finish(db, job, first, result={"input_sha256": first["corrected_sha256"]}) == first
    assert job.concept_review["review_error_reports"] == [first]
    assert {row["report_id"] for row in reports.list_for_job(job.id)} == {first["report_id"], second["report_id"]}


def test_public_collection_includes_both_review_kinds_without_private_content(evidence):
    db, job, _ = evidence
    capture(db, job)
    capture(db, job, review_kind="master")
    page = failure_reports.export_public_page(limit=1)
    second = failure_reports.export_public_page(cursor=page["next_cursor"])
    exported = page["reports"] + second["reports"]
    assert {row["origin"] for row in exported} == {"review_concept", "review_master"}
    assert all(row["disposition"] == "reported" for row in exported)
    serialized = json.dumps(exported)
    for private in ("private", "teacher", "reviewer", "ignore all rules", ".xlsx", "same-paid-run", "original question"):
        assert private not in serialized.replace("private_sha256", "")
    for row in exported:
        failure_reports_public.validate_public_report(row)
        poisoned = copy.deepcopy(row)
        poisoned["review"]["notes"] = "private notes"
        with pytest.raises(ValueError):
            failure_reports_public.validate_public_report(poisoned)
        poisoned = copy.deepcopy(row)
        poisoned["review"]["evidence_sha256"] = "private workbook text"
        with pytest.raises(ValueError):
            failure_reports_public.validate_public_report(poisoned)


def test_opt_out_no_capture_and_failed_capture_never_mutates_job(evidence, monkeypatch):
    db, job, _ = evidence
    assert capture(db, job, notes=None) is None
    assert not reports.root().exists()
    before = copy.deepcopy(reports._record(job))
    monkeypatch.setattr(reports, "_sha", lambda path: (_ for _ in ()).throw(OSError("disk full private detail")))
    with pytest.raises(reports.ReviewEvidenceUnavailable, match="has not been applied"):
        capture(db, job)
    assert reports._record(job) == before
    assert not db.dirty
    assert failure_reports.export_public_page()["reports"] == []
    assert list(reports.root().iterdir()) == []


def test_symlink_evidence_is_refused_without_copying_other_files(evidence, tmp_path):
    db, job, _ = evidence
    secret = tmp_path / "outside"
    secret.write_text("not this job")
    (config.UPLOAD_DIR / str(job.id) / "escape").symlink_to(secret)
    with pytest.raises(reports.ReviewEvidenceUnavailable):
        capture(db, job)
    assert failure_reports.export_public_page()["reports"] == []


def test_outcome_failure_preserves_accepted_upload_and_reports_warning(evidence, monkeypatch):
    db, job, _ = evidence
    receipt = capture(db, job)
    job.detail = "corrected upload already committed"
    db.commit()
    monkeypatch.setattr(reports, "_write_json", lambda *args: (_ for _ in ()).throw(OSError("disk full")))
    response = reports.finish(db, job, receipt, result={"accepted": True})
    assert response["status"] == "attention_required"
    db.refresh(job)
    assert job.detail == "corrected upload already committed"
    assert reports.evidence_for_job(job.id, receipt["report_id"])[1].is_file()


def test_upload_rejection_retains_evidence_without_accepted_marker(evidence):
    db, job, _ = evidence
    receipt = capture(db, job)
    reports.finish(db, job, receipt, error=ValueError("private correction was refused"))
    assert "review_error_reports" not in job.concept_review
    assert reports.list_for_job(job.id)[0]["upload_accepted"] is False
    assert reports.evidence_for_job(job.id, receipt["report_id"])[1].is_file()


def test_empty_present_notes_are_distinct_from_unchecked():
    app = FastAPI()

    @app.post("/upload")
    def upload(file: UploadFile = File(...), notes=Depends(reports.requested_notes)):
        return {"notes": notes}

    client = TestClient(app)
    assert client.post("/upload", files={"file": ("file.xlsx", b"data")}).json() == {"notes": None}
    assert client.post("/upload", files={"file": ("file.xlsx", b"data")}, data={"review_error_notes": ""}).json() == {"notes": ""}
    assert client.post("/upload", files={"file": ("file.xlsx", b"data")}, data={"review_error_notes": "x" * 20001}).status_code == 422


def test_private_download_requires_job_owner_and_bound_report(evidence):
    db, job, factory = evidence
    receipt = capture(db, job)
    app = FastAPI()
    app.include_router(api.router)

    def database():
        with factory() as session:
            yield session

    app.dependency_overrides[get_db] = database
    principal = auth.Principal(sub=job.owner_sub, email="teacher@example.test", name="Teacher")
    app.dependency_overrides[auth.require_user] = lambda: principal
    client = TestClient(app)
    url = f"/build-concepts/uploads/{job.id}/review-error-reports/{receipt['report_id']}/evidence.zip"
    response = client.get(url)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    assert hashlib.sha256(response.content).hexdigest() == response.headers["x-evidence-sha256"]
    assert client.get(f"/build-concepts/uploads/{job.id}/review-error-reports").json()["reports"][0]["report_id"] == receipt["report_id"]
    principal = auth.Principal(sub="unrelated-user", email="other@example.test", name="Other")
    assert client.get(url).status_code == 404
    with pytest.raises(FileNotFoundError):
        reports.evidence_for_job(job.id + 1, receipt["report_id"])
    with pytest.raises(FileNotFoundError):
        reports.evidence_for_job(job.id, "../secret")


def test_concept_upload_captures_before_apply_and_keeps_run_identity(db, tmp_path, monkeypatch):
    from tests.test_concept_review_workflow import _review_job
    from app.services import build_concepts_release_api_contract as endpoint
    from app.services import reviewed_file_input

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "data/uploads")
    job = _review_job(db)
    run_id = job.run_id
    before_clock = copy.deepcopy(job.run_state)

    def apply(worker_db, worker_job, **kwargs):
        captured = reports.list_for_job(job.id)
        assert len(captured) == 1
        _, path = reports.evidence_for_job(job.id, captured[0]["report_id"])
        with zipfile.ZipFile(path) as archive:
            saved = json.loads(archive.read("context/job.json"))
            assert saved["run_state"] == before_clock
            assert archive.read("review/corrected-upload.bin") == b"reviewer file unchanged"
        return {"round_recorded": True, "input_sha256": hashlib.sha256(b"reviewer file unchanged").hexdigest()}

    monkeypatch.setattr(reviewed_file_input, "queue", apply)
    result = asyncio.run(endpoint._concept_review_upload_endpoint(
        job.id, lane="post", file=UploadFile(io.BytesIO(b"reviewer file unchanged"), filename="corrected.xlsx"),
        db=db, user=auth.LOCAL_PRINCIPAL, review_error_notes=""))
    assert result["review_error_report"]["status"] == "queued"
    assert result["concept_review"]["review_error_reports"] == [result["review_error_report"]]
    db.refresh(job)
    assert job.run_id == run_id
    assert not job.openai_usage.get("request_count")
    assert reports.list_for_job(job.id)[0]["upload_accepted"] is True


def test_concept_capture_failure_returns_503_before_upload_or_clock_change(db, tmp_path, monkeypatch):
    from tests.test_concept_review_workflow import _review_job
    from app.services import build_concepts_release_api_contract as endpoint
    from app.services import reviewed_file_input
    from fastapi import HTTPException

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "data/uploads")
    job = _review_job(db)
    before = copy.deepcopy(reports._record(job))
    monkeypatch.setattr(reports, "_sha", lambda *args: (_ for _ in ()).throw(OSError("storage full")))
    monkeypatch.setattr(reviewed_file_input, "queue", lambda *args, **kwargs: pytest.fail("upload applied before capture"))
    with pytest.raises(HTTPException) as caught:
        asyncio.run(endpoint._concept_review_upload_endpoint(
            job.id, lane="post", file=UploadFile(io.BytesIO(b"correction"), filename="corrected.xlsx"),
            db=db, user=auth.LOCAL_PRINCIPAL, review_error_notes="reported issue"))
    assert caught.value.status_code == 503
    db.refresh(job)
    assert reports._record(job) == before


def test_master_upload_logs_exact_original_and_corrected_without_new_generation(db, tmp_path, monkeypatch):
    from tests.test_master_review import _master_ready_job, _master_bytes, OWNER
    from app.services import master_review

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "data/uploads")
    config.DATA_DIR.mkdir(parents=True)
    job, original = _master_ready_job(db)
    corrected = _master_bytes(original)
    before = copy.deepcopy((job.run_id, job.openai_usage, job.generation_checkpoint))
    result = master_review.submit_reviewed_master(db, job, lane="post", workbook_bytes=corrected,
        filename="corrected master.xlsx", owner_sub=OWNER, review_error_notes="Need a clearer explanation.")
    assert result["round_recorded"] is False
    assert result["version"] == original.version
    receipt = result["review_error_report"]
    _, path = reports.evidence_for_job(job.id, receipt["report_id"])
    with zipfile.ZipFile(path) as archive:
        assert archive.read("review/corrected-upload.bin") == corrected
        assert any(archive.read(name) == corrected for name in archive.namelist()
                   if name.startswith(f"outputs/master/{original.id}/") and name.endswith(".xlsx"))
    assert (job.run_id, job.openai_usage, job.generation_checkpoint) == before
    assert job.concept_review["review_error_reports"] == [receipt]

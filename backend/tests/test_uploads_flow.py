"""Upload is staged only; conversion to MMD is a separate, replaceable step."""
import io

import pytest

from app import models
from app.services import generation_recovery, uploads
from tests.conftest import (
    convert_assessment_upload,
    convert_concept_upload,
    stream_result,
)


def test_upload_stages_without_processing(client, db):
    files = {"file": ("mistake.txt", io.BytesIO(b"# Title\n\nfirst upload body"), "text/plain")}
    job = client.post("/build-assessments/uploads?upload_type=questions", files=files).json()
    assert job["status"] == "uploaded"
    assert job["mmd_text"] == ""

    # The DB row reflects 'uploaded' with no MMD computed yet.
    row = db.get(models.UploadJob, job["id"])
    db.refresh(row)
    assert row.status == "uploaded"
    assert row.mmd_text == ""


def test_replace_file_before_convert(client, db):
    files = {"file": ("wrong.txt", io.BytesIO(b"# Wrong\n\nwrong content"), "text/plain")}
    job = client.post("/build-assessments/uploads?upload_type=questions", files=files).json()
    row = db.get(models.UploadJob, job["id"])
    row.generation_checkpoint = {"stage": "pre_type_assignment"}
    row.question_inventory = {"items": [{"qid": "old"}]}
    db.commit()

    # Swap in the correct file before converting.
    newfiles = {"file": ("right.txt", io.BytesIO(b"# Right\n\nright content here"), "text/plain")}
    replaced = client.put(
        f"/build-assessments/uploads/{job['id']}/file", files=newfiles).json()
    assert replaced["filename"] == "right.txt"
    assert replaced["status"] == "uploaded"
    db.expire_all()
    row = db.get(models.UploadJob, job["id"])
    assert row.generation_checkpoint == {}
    assert row.question_inventory == {}

    row.generation_checkpoint = {"stage": "pre_type_assignment"}
    row.question_inventory = {"items": [{"qid": "stale"}]}
    db.commit()
    converted = convert_assessment_upload(client, job["id"])
    assert converted["status"] == "converted"
    assert "right content here" in converted["mmd_text"]
    db.expire_all()
    row = db.get(models.UploadJob, job["id"])
    assert row.generation_checkpoint == {}
    assert row.question_inventory == {}


def test_convert_then_get_job(client):
    files = {"file": ("doc.txt", io.BytesIO(b"# Doc\n\nbody text"), "text/plain")}
    job = client.post("/build-concepts/post-learning/uploads", files=files).json()
    convert_concept_upload(client, job["id"])

    fetched = client.get(f"/build-concepts/uploads/{job['id']}").json()
    assert fetched["status"] == "converted"
    assert fetched["mmd_text"].startswith("#")


def test_convert_text_upload_preserves_utf8_source(client):
    text = "Frédéric Sorrieu’s vision — ₹500"
    files = {
        "file": (
            "nationalism.mmd",
            io.BytesIO(text.encode("utf-8")),
            "text/plain",
        )
    }
    job = client.post(
        "/build-concepts/post-learning/uploads",
        files=files,
    ).json()

    converted = convert_concept_upload(client, job["id"])

    assert text in converted["mmd_text"]
    assert "FrÃ" not in converted["mmd_text"]
    assert "â€" not in converted["mmd_text"]


def test_generate_requires_conversion(client, first_chapter):
    files = {"file": ("doc.txt", io.BytesIO(b"# Doc\n\nbody"), "text/plain")}
    job = client.post("/build-concepts/post-learning/uploads", files=files).json()
    # Generating before conversion should surface an error in the stream.
    from tests.conftest import stream_error_message
    msg = stream_error_message(client.post(
        f"/build-concepts/post-learning/uploads/{job['id']}/generate",
        json={"target_chapter_id": first_chapter["id"]}))
    assert msg and "convert" in msg.lower()


def test_convert_stream_emits_progress_events(client):
    files = {"file": ("doc.txt", io.BytesIO(b"# Doc\n\nbody"), "text/plain")}
    job = client.post("/build-concepts/post-learning/uploads", files=files).json()
    from tests.conftest import stream_events
    events = stream_events(client.post(f"/build-concepts/uploads/{job['id']}/convert"))
    types = {e["type"] for e in events}
    assert "log" in types
    assert "progress" in types
    assert "result" in types


def _mark_non_resumable(db, job):
    inventory = dict(job.question_inventory or {})
    inventory[models.GENERATION_RECOVERY_INVENTORY_KEY] = {
        "resume_allowed": False,
        "recovery_action": "reconvert_new_upload",
        "recovery": "Start a new upload and conversion.",
    }
    job.question_inventory = inventory
    job.generation_checkpoint = {"stage": "source_graph_review"}
    db.commit()
    db.refresh(job)


def test_non_resumable_concept_upload_cannot_replace_or_convert(
    client, db, monkeypatch,
):
    staged = client.post(
        "/build-concepts/post-learning/uploads",
        files={"file": ("blocked.txt", io.BytesIO(b"blocked"), "text/plain")},
    ).json()
    job = db.get(models.UploadJob, staged["id"])
    _mark_non_resumable(db, job)

    async def forbidden_read(*_args, **_kwargs):
        raise AssertionError("blocked file replacement read the request body")

    monkeypatch.setattr(
        "app.api.build_concepts.read_limited_upload", forbidden_read
    )
    replaced = client.put(
        f"/build-concepts/uploads/{job.id}/file",
        files={"file": ("new.txt", io.BytesIO(b"new"), "text/plain")},
    )
    assert replaced.status_code == 409
    assert "Start a new upload and conversion" in replaced.json()["detail"]

    def forbidden_stream(*_args, **_kwargs):
        raise AssertionError("blocked conversion opened a progress stream")

    monkeypatch.setattr(
        "app.api.build_concepts.progress.stream", forbidden_stream
    )
    converted = client.post(f"/build-concepts/uploads/{job.id}/convert")
    assert converted.status_code == 409

    def forbidden_save(*_args, **_kwargs):
        raise AssertionError("blocked replacement wrote a source file")

    monkeypatch.setattr(uploads, "save_upload_file", forbidden_save)
    with pytest.raises(generation_recovery.NonResumableRunError):
        uploads.replace_file(
            db,
            job.id,
            filename="new.txt",
            raw_bytes=b"new",
            owner_sub="local:default",
            module="build_concepts",
        )

    def forbidden_provider(*_args, **_kwargs):
        raise AssertionError("blocked conversion entered the document provider")

    monkeypatch.setattr(uploads.mmd, "to_mmd", forbidden_provider)
    with pytest.raises(generation_recovery.NonResumableRunError):
        uploads.convert_job(
            db,
            job.id,
            owner_sub="local:default",
            module="build_concepts",
        )

    db.refresh(job)
    assert job.filename == "blocked.txt"
    assert job.status == "uploaded"
    assert job.generation_checkpoint == {"stage": "source_graph_review"}
    assert job.generation_recovery["resume_allowed"] is False


def test_non_resumable_pdf_cannot_bypass_through_installed_reader(
    client, db, monkeypatch,
):
    staged = client.post(
        "/build-concepts/post-learning/uploads",
        files={"file": ("blocked.pdf", io.BytesIO(b"%PDF-fake"), "application/pdf")},
    ).json()
    job = db.get(models.UploadJob, staged["id"])
    _mark_non_resumable(db, job)

    from app.services import canonical_source_phase221_fallback as fallback

    monkeypatch.setattr(fallback, "_enabled", lambda: True)

    def forbidden_reader(*_args, **_kwargs):
        raise AssertionError("blocked PDF conversion entered GPT PDF-to-ACSD")

    monkeypatch.setattr(fallback, "reconstruct_pdf_to_acsd", forbidden_reader)
    with pytest.raises(generation_recovery.NonResumableRunError):
        uploads.convert_job(
            db,
            job.id,
            owner_sub="local:default",
            module="build_concepts",
        )

    db.refresh(job)
    assert job.status == "uploaded"
    assert job.openai_usage == {}


def test_ordinary_concept_upload_can_still_replace_and_convert(client):
    staged = client.post(
        "/build-concepts/post-learning/uploads",
        files={"file": ("first.txt", io.BytesIO(b"first"), "text/plain")},
    ).json()
    replaced = client.put(
        f"/build-concepts/uploads/{staged['id']}/file",
        files={
            "file": (
                "ordinary.txt",
                io.BytesIO(b"# Ordinary\n\nallowed"),
                "text/plain",
            )
        },
    )
    assert replaced.status_code == 200, replaced.text
    result = convert_concept_upload(client, staged["id"])
    assert result["status"] == "converted"
    assert "allowed" in result["mmd_text"]

"""Upload is staged only; conversion to MMD is a separate, replaceable step."""
import io

from app import models
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

import json
import os

# Tests are always deterministic dry-mode, even when live API keys are present
# in the environment (live is default-on when keys exist).
os.environ["AEGIS_ALLOW_DRY"] = "1"
os.environ["AEGIS_USE_LIVE"] = "0"

import pytest
from fastapi.testclient import TestClient

from app import config
from app.bulk_import import reader
from app.db import Base, SessionLocal, engine, init_db
from app.main import app, bootstrap


def _load_test_fixtures() -> None:
    if not config.BULK_IMPORT_DB.exists():
        import subprocess
        import sys

        subprocess.run(
            [sys.executable, "scripts/generate_dummy_data.py"],
            check=True,
            cwd=str(config.ROOT),
        )
    db = SessionLocal()
    try:
        reader.import_workbook(db, config.BULK_IMPORT_DB)
    finally:
        db.close()


@pytest.fixture(scope="session", autouse=True)
def _prepare():
    Base.metadata.drop_all(bind=engine)
    init_db()
    # Fresh output workbook per test session.
    config.BULK_IMPORT_OUTPUT.unlink(missing_ok=True)
    _load_test_fixtures()
    yield
    Base.metadata.drop_all(bind=engine)
    config.BULK_IMPORT_OUTPUT.unlink(missing_ok=True)


@pytest.fixture()
def client():
    return TestClient(app)


# --------------------------------------------------------------------------- #
# Helpers for the NDJSON progress streams returned by convert/generate routes.
# --------------------------------------------------------------------------- #

def stream_events(resp) -> list[dict]:
    assert resp.status_code == 200, getattr(resp, "text", resp)
    return [json.loads(line) for line in resp.text.splitlines() if line.strip()]


def stream_result(resp) -> dict:
    """Return the final ``result`` payload of a progress stream (raises on error)."""
    events = stream_events(resp)
    for e in events:
        if e.get("type") == "error":
            raise AssertionError("stream error: " + str(e.get("message")))
    data = [e["data"] for e in events if e.get("type") == "result"]
    assert data, f"no result event in stream: {events}"
    return data[-1]


def stream_error_message(resp) -> str | None:
    for e in stream_events(resp):
        if e.get("type") == "error":
            return e.get("message", "")
    return None


def convert_assessment_upload(client, job_id: int) -> dict:
    return stream_result(client.post(f"/build-assessments/uploads/{job_id}/convert"))


def convert_concept_upload(client, job_id: int) -> dict:
    return stream_result(client.post(f"/build-concepts/uploads/{job_id}/convert"))


@pytest.fixture()
def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture()
def first_chapter(client):
    tree = client.get("/directory/tree").json()
    for board in tree:
        for grade in board["grades"]:
            for subject in grade["subjects"]:
                for unit in subject["units"]:
                    for ch in unit["chapters"]:
                        if ch.get("concept_count", 0) > 0:
                            return ch
    pytest.fail("no chapter with concepts in test fixture tree")


@pytest.fixture()
def first_concept(client, first_chapter):
    detail = client.get(f"/directory/chapters/{first_chapter['id']}").json()
    return detail["topics"][0]["concepts"][0]

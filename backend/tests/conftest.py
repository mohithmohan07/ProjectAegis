import json
import os
import re

# Tests are always deterministic dry-mode, even when live API keys are present
# in the environment (live is default-on when keys exist).
os.environ["AEGIS_ALLOW_DRY"] = "1"
os.environ["AEGIS_USE_LIVE"] = "0"

import pytest
from fastapi.testclient import TestClient

from app import config
from app.bulk_import import reader
from app.db import Base, SessionLocal, engine, init_db
from app.main import app


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


@pytest.fixture(autouse=True)
def _forget_negotiated_reasoning_ceilings():
    """Keep discovered effort ceilings from leaking between tests.

    Capability negotiation caches a per-model ceiling process-wide so one probe
    teaches every later request. That is exactly wrong for tests: a rejection
    staged by one test would silently lower the effort every later test sees.
    """
    from aegis_pipeline import openai_policy

    openai_policy.reset_reasoning_ceilings()
    yield
    openai_policy.reset_reasoning_ceilings()


@pytest.fixture(autouse=True)
def _recorded_legacy_assessment_level_authority(monkeypatch):
    """Give dry legacy-generation tests an explicit recorded tier authority.

    Production deliberately has no offline semantic fallback.  The historical
    HTTP/session tests still need to exercise persistence without a live model,
    so their model verdict is injected here, in test code, at the documented
    authority seam.
    """
    from app import bulk_import as bi
    from app.services import build_assessments, generation

    def provider(payload: dict) -> dict:
        return {
            "candidate_id": payload["candidate"]["candidate_id"],
            "tier": "Advanced",
            "rationale": "Explicit recorded fixture verdict for a dry test.",
        }

    def critic(_payload: dict) -> dict:
        return {"verdict": "verified", "confidence": 1.0, "issues": []}

    def cell_provider(payload: dict) -> dict:
        cell = payload["blueprint_cell"]
        kind = cell["sheet_kind"]
        return {
            "cell_id": cell["cell_id"],
            "marks": {
                "objective": 1.0,
                "subjective": 3.0,
                "descriptive": 5.0,
            }[kind],
            "question_duration": {
                "objective": 2.0,
                "subjective": 5.0,
                "descriptive": 10.0,
            }[kind],
            "math_keyboard": "" if kind == "objective" else "No",
            "rationale": "Explicit recorded fixture cell contract.",
        }

    def route_provider(payload: dict) -> dict:
        return {
            "candidate_id": payload["candidate"]["candidate_id"],
            "concept_key": payload["candidate_concepts"][0]["concept_key"],
            "evidence": "Explicit recorded fixture route evidence.",
            "rationale": "Explicit recorded fixture home-concept verdict.",
        }

    def identify_provider(payload: dict) -> dict:
        requested = payload["question_type"]
        kind = "objective" if requested == "auto" else requested
        chunks = [
            chunk.strip()
            for chunk in re.split(r"\n\s*\n+", payload["mmd_text"])
            if chunk.strip() and not chunk.lstrip().startswith("#")
        ]
        context_trigger = re.compile(
            r"based on the (above|following)|from the "
            r"(conversation|passage|dialogue)|refer(ring)? to the "
            r"(diagram|table|figure|graph)|using the table|according to the "
            r"(case study|passage)|answer the following",
            re.IGNORECASE,
        )
        rows = []
        previous = ""
        for chunk in chunks:
            question_text = bi.to_plain_text(chunk[:400])
            if previous and context_trigger.search(chunk):
                question_text = (
                    f"Context: {bi.to_plain_text(previous[:600])}\n\n"
                    f"{question_text}"
                )
            rows.append({
                "sheet_kind": kind,
                "question_category": {
                    "objective": "Multiple Choice Question",
                    "subjective": "Short Answer",
                    "descriptive": "Long Answer",
                }[kind],
                "cognitive_skills": "Understand",
                "level_of_difficulty": "Moderate",
                "marks": {
                    "objective": 1.0,
                    "subjective": 3.0,
                    "descriptive": 5.0,
                }[kind],
                "question_duration": {
                    "objective": 2.0,
                    "subjective": 5.0,
                    "descriptive": 10.0,
                }[kind],
                "math_keyboard": "" if kind == "objective" else "No",
                "question": chunk[:400],
                "question_text": question_text,
                "answers": [],
                "sub_questions": [],
            })
            previous = chunk
        return {"questions": rows}

    monkeypatch.setattr(
        build_assessments,
        "_legacy_level_authorities",
        lambda: (provider, critic, None),
    )
    monkeypatch.setattr(
        build_assessments,
        "_legacy_cell_mark_authorities",
        lambda: (cell_provider, critic, None),
    )
    monkeypatch.setattr(
        build_assessments,
        "_legacy_route_authorities",
        lambda: (route_provider, critic, None),
    )
    monkeypatch.setattr(
        generation,
        "_offline_assessment_identification_authority",
        lambda: identify_provider,
    )


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

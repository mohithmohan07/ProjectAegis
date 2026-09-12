"""The batch console's HTTP surface, its team boundary, and the safety guards.

The console is a shared board: one person stages the PDF, another uploads the
reviewed file days later. These pin that a teammate can do their part, that
nobody gains access to a job which was never put on the board, and that a
redeploy cannot delete a chapter with work in flight.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import config, models
from app.db import SessionLocal
from app.main import app
from app.services import auth, chapter_batches, syllabus_import, uploads
from app.services import build_concepts_release as release_svc


def _google_mode(monkeypatch) -> None:
    monkeypatch.setattr(config, "AUTH_MODE", "google")
    monkeypatch.setattr(
        config, "GOOGLE_CLIENT_ID", "test-client.apps.googleusercontent.com")
    monkeypatch.setattr(config, "ALLOWED_GOOGLE_DOMAIN", "up.school")
    monkeypatch.setattr(config, "LEGACY_OWNER_EMAIL", "")
    monkeypatch.setattr(
        config, "ADMIN_PASSWORD", "strong-google-test-admin-password")
    monkeypatch.setattr(
        config, "SESSION_SECRET", "test-session-secret-" + ("x" * 48))
    monkeypatch.setattr(config, "SECURE_COOKIES", True)


def _client(sub: str) -> TestClient:
    principal = auth.Principal(
        sub=sub, email=f"{sub}@up.school", name=sub.title(), hd="up.school")
    client = TestClient(app, base_url="https://testserver")
    client.cookies.set(
        config.SESSION_COOKIE_NAME, auth.encode_session(principal))
    return client


@pytest.fixture()
def session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.rollback()
        db.close()


def _chapter(db, code, **kwargs):
    chapter = models.Chapter(
        chapter_code=code,
        board=kwargs.get("board", "CBSE"),
        grade=kwargs.get("grade", "10"),
        subject=kwargs.get("subject", "Maths"),
        unit=kwargs.get("unit", "Algebra"),
        chapter_title=kwargs.get("title", "Polynomials"),
        chapter_display_name=kwargs.get("title", "Polynomials"),
    )
    db.add(chapter)
    db.commit()
    return chapter


def _job(db, owner_sub, *, status="converted", inventory=None):
    job = models.UploadJob(
        owner_sub=owner_sub, module="build_concepts", learning_kind="post",
        filename="chapter.pdf", status=status,
        question_inventory=inventory or {},
    )
    db.add(job)
    db.commit()
    return job


def _board_row(db, chapter, job):
    row = models.ChapterBatchRow(
        chapter_id=chapter.id, job_id=job.id, previous_job_ids=[],
        created_by_sub=job.owner_sub, created_by_email="owner@up.school",
    )
    db.add(row)
    db.commit()
    return row


def test_the_list_page_carries_its_own_vocabulary_and_facets(
    session, monkeypatch,
):
    _google_mode(monkeypatch)
    _chapter(session, "10CBBA_L01", board="CBSE", grade="10", subject="Biology")
    client = _client("google:reader")

    response = client.get("/chapter-batches", params={"page_size": 5})
    assert response.status_code == 200
    payload = response.json()
    assert payload["page"] == 1
    assert {"boards", "grades", "subjects", "triples"} <= set(payload["facets"])
    # The client never hardcodes a state name or a label.
    values = {item["value"] for item in payload["states"]}
    assert "published" in values and "recovering" in values
    assert {"running", "queued", "blocked", "capacity", "worker_alive"} <= set(
        payload["queue"]
    )
    for row in payload["items"]:
        assert row["state"] in values
        assert row["state_label"]
        # The big payloads never ride the list.
        assert "question_inventory" not in row
        assert "generation_running" not in row


def test_the_grade_facet_comes_from_the_catalogue_not_the_frozen_list(
    session, monkeypatch,
):
    """Grades 04 and 05 exist as chapters but not in ``bulk_import.GRADES``."""
    _google_mode(monkeypatch)
    _chapter(session, "04KAMA_L01", board="KARNATAKA", grade="04",
             subject="Maths")
    client = _client("google:reader")

    payload = client.get("/chapter-batches", params={"page_size": 1}).json()
    assert "04" in payload["facets"]["grades"]


def test_a_teammate_may_read_a_job_on_the_board_but_not_one_off_it(
    session, monkeypatch,
):
    _google_mode(monkeypatch)
    chapter = _chapter(session, "10CBMA_L02")
    on_board = _job(session, "google:owner")
    _board_row(session, chapter, on_board)
    private = _job(session, "google:owner")

    teammate = _client("google:teammate")
    assert teammate.get(
        f"/build-concepts/uploads/{on_board.id}").status_code == 200
    refused = teammate.get(f"/build-concepts/uploads/{private.id}")
    assert refused.status_code == 404
    # The refusal is byte-identical to an owner's miss, so membership of the
    # board cannot be probed through it.
    assert refused.json()["detail"] == "upload job not found"


def test_the_shared_lookup_never_widens_the_owner_lookup(session):
    chapter = _chapter(session, "10CBMA_L03")
    job = _job(session, "google:owner")
    _board_row(session, chapter, job)

    # Unchanged: the owner filter still refuses a stranger.
    with pytest.raises(uploads.UploadJobNotFound):
        uploads.get_job(session, job.id, owner_sub="google:teammate")
    # Widened: only for a job the console board references.
    assert uploads.get_shared_job(session, job.id).id == job.id


def test_push_answers_every_row_and_never_fails_the_batch(
    session, monkeypatch,
):
    _google_mode(monkeypatch)
    ready = _chapter(session, "10CBMA_L04")
    job = _job(session, "google:owner")
    _board_row(session, ready, job)
    bare = _chapter(session, "10CBMA_L05")

    client = _client("google:owner")
    response = client.post("/chapter-batches/push", json={
        "step": "step01",
        "rows": [{"chapter_id": ready.id}, {"chapter_id": bare.id}],
    })
    assert response.status_code == 200
    results = {item["chapter_id"]: item for item in response.json()["results"]}
    assert results[ready.id]["verdict"] == "queued"
    assert results[bare.id]["verdict"] == "refused"
    assert results[bare.id]["reason_code"] == "no_source"
    # Each outcome carries the freshly derived row for an optimistic patch.
    assert results[ready.id]["row"]["state"] == "step01_queued"


def test_push_refuses_an_unknown_step(session, monkeypatch):
    _google_mode(monkeypatch)
    client = _client("google:owner")
    response = client.post("/chapter-batches/push", json={
        "step": "force_release", "rows": [],
    })
    assert response.status_code == 400


def test_a_queued_chapter_can_be_cancelled_and_the_row_says_so(
    session, monkeypatch,
):
    _google_mode(monkeypatch)
    chapter = _chapter(session, "10CBMA_L06")
    job = _job(session, "google:owner")
    _board_row(session, chapter, job)
    client = _client("google:owner")
    client.post("/chapter-batches/push", json={
        "step": "step01", "rows": [{"chapter_id": chapter.id}],
    })

    response = client.post(
        "/chapter-batches/cancel", json={"chapter_ids": [chapter.id]})
    assert response.status_code == 200
    row = response.json()["results"][0]["row"]
    assert row["state"] == "cancelled"


def test_the_worker_never_names_a_legacy_route():
    """Q52 closed force-release, revisions and release-review for these jobs."""
    from pathlib import Path

    source = Path(
        "app/services/chapter_queue_worker.py"
    ).read_text(encoding="utf-8")
    for forbidden in (
        "force_release", "apply_workbook_and_publish", "record_instruction",
        "apply_manual_edits", "apply_instruction_round", "release_review",
    ):
        assert forbidden not in source, forbidden


def test_a_chapter_with_console_work_survives_a_syllabus_reconcile(session):
    """A redeploy must not delete a chapter whose PDF is staged and queued."""
    chapter = _chapter(session, "10CBMA_L07")
    job = _job(session, "google:owner")
    _board_row(session, chapter, job)

    # It has no topics — the run is what creates them.
    assert not chapter.topics
    assert syllabus_import._chapter_has_content(chapter) is False
    assert syllabus_import._chapter_has_content(chapter, session) is True


def test_a_queue_run_writes_a_readable_journal(tmp_path, session):
    """A run with no HTTP client still leaves a log a person can read."""
    from app.services import progress, run_journal

    job = _job(session, "google:owner")
    with progress.capture_to_journal(job.id, title="Step 01 — starting") as run:
        progress.log("did a thing")
        run.set_result({"job_id": job.id, "ok": True})

    events = run_journal.read_after(job.id, 0)["events"]
    kinds = [event.get("type") for event in events]
    assert "result" in kinds
    assert any("did a thing" in str(event.get("message") or "")
               for event in events)

    # A second step continues the same run rather than truncating it.
    with progress.capture_to_journal(job.id, continue_existing=True) as run:
        progress.log("step two")
        run.set_result({"job_id": job.id})
    after = run_journal.read_after(job.id, 0)["events"]
    assert len(after) > len(events)
    assert [event["seq"] for event in after] == sorted(
        event["seq"] for event in after)


def test_a_failing_step_records_its_error_in_the_journal(session):
    from app.services import progress, run_journal

    job = _job(session, "google:owner")
    with pytest.raises(ValueError):
        with progress.capture_to_journal(job.id):
            raise ValueError("the provider refused")

    events = run_journal.read_after(job.id, 0)["events"]
    errors = [event for event in events if event.get("type") == "error"]
    assert errors and "provider refused" in errors[-1]["message"]


def test_staging_a_source_binds_the_chapter_and_spends_nothing(
    session, monkeypatch,
):
    _google_mode(monkeypatch)
    chapter = _chapter(session, "10CBMA_L08")
    client = _client("google:owner")

    response = client.post(
        f"/chapter-batches/{chapter.id}/source",
        params={"source_book": "NCERT Maths", "chapter_duration_minutes": 45},
        files={"file": ("chapter.pdf", b"%PDF-1.4 test", "application/pdf")},
    )
    assert response.status_code == 200, response.text
    row = response.json()
    # Staged, not started: the push is the only act that spends money.
    assert row["state"] == "source_staged"
    assert row["can"]["step01"] is True
    assert row["can"]["step02"] is False
    assert row["source_book"] == "NCERT Maths"
    assert row["source_filename"] == "chapter.pdf"
    assert row["job_id"]

    bound = session.query(models.ChapterBatchRow).filter(
        models.ChapterBatchRow.chapter_id == chapter.id).one()
    assert bound.job_id == row["job_id"]
    job = session.get(models.UploadJob, bound.job_id)
    assert job.status == "uploaded"
    # The publication provenance is the explicit field, never the chapter code.
    assert job.source_book == "NCERT Maths"
    assert job.chapter_duration_minutes == 45


def test_restaging_a_source_keeps_the_earlier_run_readable(
    session, monkeypatch,
):
    _google_mode(monkeypatch)
    chapter = _chapter(session, "10CBMA_L09")
    client = _client("google:owner")

    first = client.post(
        f"/chapter-batches/{chapter.id}/source",
        files={"file": ("one.pdf", b"%PDF-1.4 one", "application/pdf")},
    ).json()
    second = client.post(
        f"/chapter-batches/{chapter.id}/source",
        files={"file": ("two.pdf", b"%PDF-1.4 two", "application/pdf")},
    ).json()

    assert second["job_id"] != first["job_id"]
    bound = session.query(models.ChapterBatchRow).filter(
        models.ChapterBatchRow.chapter_id == chapter.id).one()
    session.refresh(bound)
    assert first["job_id"] in list(bound.previous_job_ids or [])


def test_a_running_chapter_refuses_a_new_source(session, monkeypatch):
    _google_mode(monkeypatch)
    chapter = _chapter(session, "10CBMA_L10")
    job = _job(session, "google:owner")
    board = _board_row(session, chapter, job)
    session.add(models.ChapterBatchTask(
        batch_row_id=board.id, kind="step01", state="leased",
        lease_owner="worker:1:abc",
        lease_expires_at=__import__("datetime").datetime.utcnow()
        + __import__("datetime").timedelta(minutes=5),
    ))
    session.commit()

    client = _client("google:owner")
    response = client.post(
        f"/chapter-batches/{chapter.id}/source",
        files={"file": ("new.pdf", b"%PDF-1.4 new", "application/pdf")},
    )
    assert response.status_code == 409


def test_the_events_route_reports_running_from_the_lease(session, monkeypatch):
    """Not from the process-local job lock, which lies after a restart."""
    _google_mode(monkeypatch)
    chapter = _chapter(session, "10CBMA_L11")
    job = _job(session, "google:owner",
               inventory=_marker_pending())
    board = _board_row(session, chapter, job)
    session.add(models.ChapterBatchTask(
        batch_row_id=board.id, kind="step02", state="leased",
        lease_owner="worker:1:abc",
        lease_expires_at=__import__("datetime").datetime.utcnow()
        + __import__("datetime").timedelta(minutes=5),
    ))
    session.commit()

    client = _client("google:teammate")
    payload = client.get(f"/chapter-batches/{chapter.id}/events").json()
    assert payload["running"] is True
    assert "events" in payload and "next" in payload


def _marker_pending():
    return {release_svc.CONCEPT_REVIEW_KEY: {
        "version": release_svc.CONCEPT_REVIEW_VERSION,
        "status": release_svc.CONCEPT_REVIEW_REVIEWED,
        "available_lanes": ["post"], "required_lanes": ["post"],
        "reviewed_lanes": ["post"], "master_review": {},
        "corrected_inputs": {},
    }}

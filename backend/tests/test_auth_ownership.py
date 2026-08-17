"""Authentication, upload ownership, and duplicate-resume boundaries."""
from __future__ import annotations

import threading
import time

import pytest
from fastapi.testclient import TestClient

from app import config, models
from app.db import SessionLocal
from app.main import app
from app.services import (
    auth,
    build_concepts,
    checkpoints,
    drive_checkpoints,
    generation,
    uploads,
)


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


def _principal(sub: str) -> auth.Principal:
    return auth.Principal(
        sub=sub,
        email=f"{sub}@up.school",
        name=sub.title(),
        hd="up.school",
    )


def _signed_client(principal: auth.Principal) -> TestClient:
    client = TestClient(app, base_url="https://testserver")
    client.cookies.set(
        config.SESSION_COOKIE_NAME,
        auth.encode_session(principal),
    )
    return client


def test_local_mode_is_explicit_and_usable_offline(client, monkeypatch):
    monkeypatch.setattr(config, "AUTH_MODE", "local")

    configured = client.get("/auth/config")
    assert configured.status_code == 200
    assert configured.headers["cache-control"] == "no-store"
    assert configured.headers["vary"] == "Cookie"
    assert configured.json()["mode"] == "local"
    assert configured.json()["google_client_id"] == ""
    assert configured.json()["csrf_token"]
    backup = configured.json()["drive_checkpoint_backup"]
    assert set(backup) == {
        "enabled",
        "configured",
        "state",
        "verified",
        "auth_mode",
        "notice",
    }
    assert isinstance(backup["enabled"], bool)
    assert isinstance(backup["configured"], bool)
    assert isinstance(backup["verified"], bool)
    assert isinstance(backup["notice"], str)

    me = client.get("/auth/me")
    assert me.headers["cache-control"] == "no-store"
    assert me.headers["vary"] == "Cookie"
    assert me.json() == {
        "authenticated": True,
        "user": {
            "sub": auth.LOCAL_OWNER_SUB,
            "email": "local@localhost",
            "name": "Local User",
            "picture": "",
            "hd": "local",
        },
    }
    assert client.get("/directory/tree").status_code == 200
    assert client.post(
        "/auth/google",
        json={"credential": "unused", "csrf_token": "unused"},
    ).status_code == 400


def test_google_mode_rejects_default_admin_password(monkeypatch):
    _google_mode(monkeypatch)
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "admin")

    with pytest.raises(RuntimeError, match="AEGIS_ADMIN_PASSWORD"):
        auth.validate_configuration()


def test_google_login_rejects_wrong_verified_domain(monkeypatch):
    _google_mode(monkeypatch)
    monkeypatch.setattr(
        auth,
        "_decode_google_token",
        lambda _credential: {
            "sub": "outsider",
            "email": "outsider@example.org",
            "email_verified": True,
            "hd": "example.org",
        },
    )
    with TestClient(app, base_url="https://testserver") as client:
        csrf = client.get("/auth/config").json()["csrf_token"]
        response = client.post(
            "/auth/google",
            json={"credential": "verified-google-token", "csrf_token": csrf},
        )

    assert response.status_code == 403
    assert "up.school" in response.json()["detail"]


def test_google_login_sets_secure_session_and_logout_clears_it(monkeypatch):
    _google_mode(monkeypatch)
    monkeypatch.setattr(
        auth,
        "_decode_google_token",
        lambda _credential: {
            "sub": "teacher-1",
            "email": "teacher-1@up.school",
            "email_verified": True,
            "name": "Teacher One",
            "picture": "https://example.test/avatar.png",
            "hd": "up.school",
        },
    )
    with TestClient(app, base_url="https://testserver") as client:
        csrf = client.get("/auth/config").json()["csrf_token"]
        response = client.post(
            "/auth/google",
            json={"credential": "verified-google-token", "csrf_token": csrf},
        )
        assert response.status_code == 200
        assert response.json()["user"]["sub"] == "teacher-1"
        cookie_headers = response.headers.get_list("set-cookie")
        session_cookie = next(
            value for value in cookie_headers
            if value.startswith(f"{config.SESSION_COOKIE_NAME}=")
        )
        assert "HttpOnly" in session_cookie
        assert "Secure" in session_cookie
        assert "SameSite=lax" in session_cookie
        assert client.get("/directory/tree").status_code == 200

        assert client.post("/auth/logout").json() == {"ok": True}
        assert client.get("/auth/me").json()["authenticated"] is False
        assert client.get("/directory/tree").status_code == 401


@pytest.mark.parametrize(
    ("login_email", "expected_owner"),
    [
        ("legacy-owner@up.school", "new-google-sub"),
        ("different-user@up.school", auth.LOCAL_OWNER_SUB),
    ],
)
def test_only_configured_google_user_adopts_legacy_uploads(
    db, monkeypatch, login_email, expected_owner,
):
    _google_mode(monkeypatch)
    monkeypatch.setattr(
        config, "LEGACY_OWNER_EMAIL", "legacy-owner@up.school")
    monkeypatch.setattr(
        auth,
        "_decode_google_token",
        lambda _credential: {
            "sub": "new-google-sub",
            "email": login_email,
            "email_verified": True,
            "hd": "up.school",
        },
    )
    legacy = models.UploadJob(
        owner_sub=auth.LOCAL_OWNER_SUB,
        module="build_concepts",
        upload_type="document",
        learning_kind="post",
        filename="legacy.mmd",
        mmd_text="legacy",
        status="converted",
    )
    db.add(legacy)
    db.commit()
    db.refresh(legacy)
    legacy_ids = [
        row.id
        for row in db.query(models.UploadJob.id).filter(
            models.UploadJob.owner_sub == auth.LOCAL_OWNER_SUB,
        )
    ]

    try:
        with TestClient(app, base_url="https://testserver") as client:
            csrf = client.get("/auth/config").json()["csrf_token"]
            response = client.post(
                "/auth/google",
                json={
                    "credential": "verified-google-token",
                    "csrf_token": csrf,
                },
            )

        assert response.status_code == 200
        db.expire_all()
        assert db.get(models.UploadJob, legacy.id).owner_sub == expected_owner
    finally:
        # This session-scoped test database is shared with the compatibility
        # suite; restore only rows this explicit adoption could have changed.
        db.query(models.UploadJob).filter(
            models.UploadJob.id.in_(legacy_ids),
            models.UploadJob.owner_sub == "new-google-sub",
        ).update(
            {models.UploadJob.owner_sub: auth.LOCAL_OWNER_SUB},
            synchronize_session=False,
        )
        db.commit()


@pytest.mark.parametrize("session_value", ["forged.payload", ""])
def test_forged_or_missing_session_cannot_access_application_routes(
    monkeypatch, session_value,
):
    _google_mode(monkeypatch)
    with TestClient(app, base_url="https://testserver") as client:
        if session_value:
            client.cookies.set(config.SESSION_COOKIE_NAME, session_value)
        assert client.get("/directory/tree").status_code == 401
        assert client.get("/health").status_code == 200


def test_expired_session_is_rejected(monkeypatch):
    _google_mode(monkeypatch)
    now = int(time.time())
    expired = auth.encode_session(
        _principal("expired-user"),
        now=now - 100,
        expires_at=now - 1,
    )
    with TestClient(app, base_url="https://testserver") as client:
        client.cookies.set(config.SESSION_COOKIE_NAME, expired)
        assert client.get("/auth/me").json()["authenticated"] is False
        assert client.get("/directory/tree").status_code == 401


def test_two_users_cannot_read_mutate_export_or_resume_each_others_job(
    db, first_chapter, monkeypatch,
):
    _google_mode(monkeypatch)
    job = models.UploadJob(
        owner_sub="teacher-a",
        module="build_concepts",
        upload_type="document",
        learning_kind="post",
        filename="private.mmd",
        mmd_text="## Private source\nBody",
        status="converted",
        generation_checkpoint={"stage": "private-checkpoint"},
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    owner = _signed_client(_principal("teacher-a"))
    outsider = _signed_client(_principal("teacher-b"))
    try:
        own_response = owner.get(f"/build-concepts/uploads/{job.id}")
        assert own_response.status_code == 200
        assert own_response.json()["mmd_text"].startswith("## Private")

        assert outsider.get(
            f"/build-concepts/uploads/{job.id}").status_code == 404
        assert outsider.get(
            f"/build-concepts/uploads/{job.id}/checkpoint").status_code == 404
        assert outsider.delete(
            f"/build-concepts/uploads/{job.id}/checkpoint").status_code == 404
        assert outsider.put(
            f"/build-concepts/uploads/{job.id}/file",
            files={"file": ("replacement.txt", b"stolen", "text/plain")},
        ).status_code == 404
        assert outsider.post(
            f"/build-concepts/uploads/{job.id}/convert").status_code == 404
        assert outsider.post(
            f"/build-concepts/post-learning/uploads/{job.id}/generate",
            json={"target_chapter_id": first_chapter["id"]},
        ).status_code == 404
    finally:
        owner.close()
        outsider.close()

    db.expire_all()
    preserved = db.get(models.UploadJob, job.id)
    assert preserved.owner_sub == "teacher-a"
    assert preserved.generation_checkpoint["stage"] == "private-checkpoint"


def test_resumable_discovery_is_owner_and_learning_kind_scoped(
    db, monkeypatch,
):
    _google_mode(monkeypatch)
    rows = [
        models.UploadJob(
            owner_sub="discovery-a",
            module="build_concepts",
            upload_type="document",
            learning_kind="post",
            filename="a-post.mmd",
            mmd_text="a",
            status="converted",
            generation_checkpoint={"stage": "post_type_assignment"},
        ),
        models.UploadJob(
            owner_sub="discovery-a",
            module="build_concepts",
            upload_type="document",
            learning_kind="post",
            filename="finished.mmd",
            mmd_text="b",
            status="generated",
            generation_checkpoint={},
        ),
        models.UploadJob(
            owner_sub="discovery-a",
            module="build_concepts",
            upload_type="document",
            learning_kind="pre",
            filename="a-pre.mmd",
            mmd_text="c",
            status="converted",
            # The ``pre`` learning_kind is deliberately still a legal value
            # (step 7 retired the pre generation lane, not the column or the
            # route contract), so a pre-kind job with a real checkpoint stage
            # must still be excluded from the post lane's discovery.
            generation_checkpoint={"stage": "canonical_skeleton"},
        ),
        models.UploadJob(
            owner_sub="discovery-b",
            module="build_concepts",
            upload_type="document",
            learning_kind="post",
            filename="b-post.mmd",
            mmd_text="d",
            status="converted",
            generation_checkpoint={"stage": "final_content_ready"},
        ),
    ]
    db.add_all(rows)
    db.commit()

    with _signed_client(_principal("discovery-a")) as client:
        response = client.get(
            "/build-concepts/checkpoints/resumable",
            params={"learning_kind": "post"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert [item["filename"] for item in payload["items"]] == ["a-post.mmd"]
    assert payload["items"][0]["generation_running"] is False
    assert {
        "mmd_text",
        "generation_log",
        "openai_usage",
        "detail",
    }.isdisjoint(payload["items"][0])


def test_resumable_discovery_is_limited_and_orders_by_checkpoint_time(
    db, monkeypatch,
):
    _google_mode(monkeypatch)
    rows = [
        models.UploadJob(
            owner_sub="limit-owner",
            module="build_concepts",
            upload_type="document",
            learning_kind="post",
            filename=f"checkpoint-{index:02d}.mmd",
            mmd_text="large source omitted from summary",
            status="converted",
            generation_checkpoint={
                "stage": "post_type_assignment",
                "saved_at": f"2026-07-{index + 1:02d}T12:00:00+00:00",
                "progress": index / 22,
            },
            generation_log=[{"message": "must stay out of discovery"}],
            openai_usage={"total_tokens": index},
        )
        for index in range(22)
    ]
    db.add_all(rows)
    db.commit()

    with _signed_client(_principal("limit-owner")) as client:
        response = client.get(
            "/build-concepts/checkpoints/resumable",
            params={"learning_kind": "post"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 22
    assert len(payload["items"]) == checkpoints.MAX_RESUMABLE_JOBS == 20
    assert payload["items"][0]["filename"] == "checkpoint-21.mmd"
    assert payload["items"][-1]["filename"] == "checkpoint-02.mmd"


def test_same_filename_uploads_use_distinct_private_storage(
    db, tmp_path, monkeypatch,
):
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads")

    first = build_concepts.create_post_learning_job(
        db,
        filename="chapter.pdf",
        raw_bytes=b"teacher-a",
        owner_sub="teacher-a",
    )
    second = build_concepts.create_post_learning_job(
        db,
        filename="chapter.pdf",
        raw_bytes=b"teacher-b",
        owner_sub="teacher-b",
    )

    assert first.upload_storage_key != second.upload_storage_key
    assert uploads.upload_file_path(first).read_bytes() == b"teacher-a"
    assert uploads.upload_file_path(second).read_bytes() == b"teacher-b"
    assert uploads.upload_file_path(first).parent.name == str(first.id)
    assert uploads.upload_file_path(second).parent.name == str(second.id)


def test_drive_worker_can_export_owned_checkpoint_without_public_bypass(
    db, first_chapter,
):
    chapter = db.get(models.Chapter, first_chapter["id"])
    assert chapter is not None
    job = models.UploadJob(
        owner_sub="drive-owner",
        module="build_concepts",
        upload_type="document",
        learning_kind="post",
        filename="owned-checkpoint.mmd",
        mmd_text="## Source\nPrivate teacher material.",
        status="converted",
        question_inventory={"items": [], "stats": {}, "mined_types": []},
    )
    stage = generation._make_concept_checkpoint(
        "pre_type_assignment",
        records=[],
        question_task_inventory={"items": [], "stats": {}},
        mined_types={"types": []},
        method_row_snapshot=[],
    )
    job.generation_checkpoint = (
        build_concepts._merge_generation_checkpoint_history(
            None,
            stage,
            fingerprint=build_concepts._generation_checkpoint_fingerprint(
                job, chapter,
            ),
            target_identity=build_concepts._generation_target_identity(
                chapter,
            ),
            target_chapter_id=chapter.id,
        )
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    with pytest.raises(uploads.UploadJobNotFound):
        checkpoints.export_bundle(db, job.id)

    filename, content = drive_checkpoints.make_database_exporter(
        SessionLocal,
    )(job.id)

    assert filename == "owned-checkpoint.aegis-checkpoint.json"
    assert b'"format": "aegis-concept-checkpoint"' in content


@pytest.mark.parametrize("should_fail", [False, True])
def test_concept_routes_queue_post_accounting_drive_state_on_success_and_error(
    db,
    client,
    first_chapter,
    monkeypatch,
    should_fail,
):
    # Step 7 retired the pre-learning generate route, so both arms run the
    # post lane; the property under test is the accounting/backup queueing on
    # the success and the error path, not the lane.
    learning_kind = "post"
    route_prefix = "post-learning"
    job = models.UploadJob(
        owner_sub=auth.LOCAL_OWNER_SUB,
        module="build_concepts",
        upload_type="document",
        learning_kind=learning_kind,
        filename=f"{learning_kind}-{'error' if should_fail else 'final'}.mmd",
        mmd_text="source",
        status="converted",
        generation_checkpoint={"stage": "stale-remote-stage"},
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    observed: list[dict] = []

    def fake_run(worker_db, job_id, _fn, *, owner_sub=None):
        row = uploads.get_job(worker_db, job_id, owner_sub=owner_sub)
        row.generation_checkpoint = {}
        row.openai_usage = {"total_tokens": 321}
        row.generation_log = [{"level": "error", "message": "final log"}]
        worker_db.commit()
        if should_fail:
            raise RuntimeError("generation failed after accounting")
        return {"job_id": job_id}

    def capture_backup(job_id):
        verify_db = SessionLocal()
        try:
            row = verify_db.get(models.UploadJob, job_id)
            observed.append({
                "checkpoint": row.generation_checkpoint,
                "tokens": row.openai_usage.get("total_tokens"),
                "log": row.generation_log[-1]["message"],
            })
        finally:
            verify_db.close()
        return drive_checkpoints.BackupScheduleResult(
            accepted=True,
            state="queued",
            job_id=job_id,
        )

    monkeypatch.setattr(uploads, "run_with_openai_usage", fake_run)
    monkeypatch.setattr(
        drive_checkpoints, "schedule_checkpoint_backup", capture_backup)

    response = client.post(
        f"/build-concepts/{route_prefix}/uploads/{job.id}/generate",
        json={"target_chapter_id": first_chapter["id"]},
    )

    assert response.status_code == 200
    assert observed == [{
        "checkpoint": {},
        "tokens": 321,
        "log": "final log",
    }]
    assert (
        '"type": "error"' in response.text
        if should_fail
        else '"type": "result"' in response.text
    )


def test_discard_checkpoint_queues_non_resumable_drive_bundle(
    db, client, monkeypatch,
):
    job = models.UploadJob(
        owner_sub=auth.LOCAL_OWNER_SUB,
        module="build_concepts",
        upload_type="document",
        learning_kind="post",
        filename="discard.mmd",
        mmd_text="source",
        status="converted",
        generation_checkpoint={"stage": "saved"},
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    queued: list[int] = []
    monkeypatch.setattr(
        drive_checkpoints,
        "schedule_checkpoint_backup",
        lambda job_id: queued.append(job_id),
    )

    response = client.delete(
        f"/build-concepts/uploads/{job.id}/checkpoint")

    assert response.status_code == 200
    assert response.json()["checkpoint_available"] is False
    assert queued == [job.id]


@pytest.mark.parametrize("storage_key", ["", ".", "..", "../escape.pdf"])
def test_upload_storage_key_rejects_root_and_traversal(
    tmp_path, monkeypatch, storage_key,
):
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads")
    config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    with pytest.raises(ValueError, match="invalid upload storage key"):
        uploads._storage_path(storage_key)


def test_duplicate_generation_fails_fast_and_exposes_running_state(db, client):
    job = models.UploadJob(
        owner_sub=auth.LOCAL_OWNER_SUB,
        module="build_concepts",
        upload_type="document",
        learning_kind="post",
        filename="running.mmd",
        mmd_text="## Running\nBody",
        status="converted",
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    started = threading.Event()
    release = threading.Event()
    first_errors: list[Exception] = []

    def first_run() -> None:
        worker_db = SessionLocal()
        try:
            uploads.run_with_openai_usage(
                worker_db,
                job.id,
                lambda: (
                    started.set(),
                    release.wait(timeout=5),
                    {"ok": True},
                )[-1],
                owner_sub=auth.LOCAL_OWNER_SUB,
            )
        except Exception as exc:  # pragma: no cover - asserted below
            first_errors.append(exc)
        finally:
            worker_db.close()

    thread = threading.Thread(target=first_run)
    thread.start()
    assert started.wait(timeout=2)
    assert uploads.is_job_running(job.id)
    assert job.generation_running is True
    assert client.put(
        f"/build-concepts/uploads/{job.id}/file",
        files={"file": ("replacement.txt", b"blocked", "text/plain")},
    ).status_code == 409
    assert client.delete(
        f"/build-concepts/uploads/{job.id}/checkpoint",
    ).status_code == 409
    assert client.post(
        f"/build-concepts/uploads/{job.id}/convert",
    ).status_code == 409

    second_db = SessionLocal()
    started_at = time.perf_counter()
    try:
        with pytest.raises(
            uploads.JobAlreadyRunningError,
            match="already running",
        ):
            uploads.run_with_openai_usage(
                second_db,
                job.id,
                lambda: {"must_not": "run"},
                owner_sub=auth.LOCAL_OWNER_SUB,
            )
    finally:
        second_db.close()
        release.set()
        thread.join(timeout=5)

    assert time.perf_counter() - started_at < 1.0
    assert not first_errors
    assert not uploads.is_job_running(job.id)


def test_stale_session_cannot_rerun_a_completed_upload(db):
    job = models.UploadJob(
        owner_sub=auth.LOCAL_OWNER_SUB,
        module="build_concepts",
        upload_type="document",
        learning_kind="post",
        filename="stale.mmd",
        mmd_text="source",
        status="converted",
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    stale_db = SessionLocal()
    winner_db = SessionLocal()
    called = False
    try:
        stale_job = uploads.get_job(stale_db, job.id)
        assert stale_job.status == "converted"
        winner = winner_db.get(models.UploadJob, job.id)
        winner.status = "generated"
        winner_db.commit()

        def must_not_run():
            nonlocal called
            called = True
            return {}

        with pytest.raises(ValueError, match="already been generated"):
            uploads.run_with_openai_usage(
                stale_db,
                job.id,
                must_not_run,
                owner_sub=auth.LOCAL_OWNER_SUB,
            )
    finally:
        stale_db.close()
        winner_db.close()

    assert called is False

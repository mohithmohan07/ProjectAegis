"""Focused regressions for assessment ownership and legacy owner migration."""
from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.engine import URL

from app import config, models
from app import db as db_module
from app.main import app
from app.services import auth


def _enable_google_mode(monkeypatch) -> None:
    monkeypatch.setattr(config, "AUTH_MODE", "google")
    monkeypatch.setattr(
        config, "ADMIN_PASSWORD", "strong-google-test-admin-password")
    monkeypatch.setattr(
        config,
        "GOOGLE_CLIENT_ID",
        "test-client.apps.googleusercontent.com",
    )
    monkeypatch.setattr(config, "ALLOWED_GOOGLE_DOMAIN", "up.school")
    monkeypatch.setattr(
        config,
        "SESSION_SECRET",
        "assessment-idor-session-secret-" + ("x" * 48),
    )
    monkeypatch.setattr(config, "SECURE_COOKIES", True)


def _principal(sub: str) -> auth.Principal:
    return auth.Principal(
        sub=sub,
        email=f"{sub}@up.school",
        name=sub,
        hd="up.school",
    )


def _signed_client(sub: str) -> TestClient:
    client = TestClient(app, base_url="https://testserver")
    client.cookies.set(
        config.SESSION_COOKIE_NAME,
        auth.encode_session(_principal(sub)),
    )
    return client


def test_assessment_upload_read_and_mutation_are_owner_scoped(
    db, monkeypatch,
):
    _enable_google_mode(monkeypatch)
    job = models.UploadJob(
        owner_sub="assessment-owner",
        module="build_assessments",
        upload_type="questions",
        filename="private-questions.txt",
        mmd_text="# Private questions",
        status="converted",
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    owner = _signed_client("assessment-owner")
    outsider = _signed_client("assessment-outsider")
    try:
        owned = owner.get(f"/build-assessments/uploads/{job.id}")
        assert owned.status_code == 200
        assert owned.json()["filename"] == "private-questions.txt"

        assert outsider.get(
            f"/build-assessments/uploads/{job.id}"
        ).status_code == 404
        assert outsider.put(
            f"/build-assessments/uploads/{job.id}/file",
            files={
                "file": (
                    "replacement.txt",
                    b"attempted replacement",
                    "text/plain",
                )
            },
        ).status_code == 404
    finally:
        owner.close()
        outsider.close()

    db.expire_all()
    preserved = db.get(models.UploadJob, job.id)
    assert preserved.owner_sub == "assessment-owner"
    assert preserved.filename == "private-questions.txt"
    assert preserved.mmd_text == "# Private questions"
    assert preserved.status == "converted"


def test_legacy_sqlite_upload_jobs_gain_owner_backfill_and_index(
    tmp_path, monkeypatch,
):
    database_path = tmp_path / "legacy-aegis.sqlite3"
    legacy_engine = create_engine(
        URL.create("sqlite", database=str(database_path))
    )
    try:
        with legacy_engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE upload_jobs ("
                "id INTEGER PRIMARY KEY, "
                "module VARCHAR(32) NOT NULL, "
                "filename VARCHAR(255) DEFAULT ''"
                ")"
            )
            connection.exec_driver_sql(
                "INSERT INTO upload_jobs (id, module, filename) "
                "VALUES (1, 'build_assessments', 'legacy.txt')"
            )

        monkeypatch.setattr(db_module, "DB_URL", "sqlite:///legacy-aegis.sqlite3")
        monkeypatch.setattr(db_module, "engine", legacy_engine)
        db_module._ensure_columns()

        with legacy_engine.connect() as connection:
            columns = {
                row[1]
                for row in connection.exec_driver_sql(
                    "PRAGMA table_info(upload_jobs)"
                )
            }
            owner_sub = connection.exec_driver_sql(
                "SELECT owner_sub FROM upload_jobs WHERE id = 1"
            ).scalar_one()
            indexes = {
                row[1]
                for row in connection.exec_driver_sql(
                    "PRAGMA index_list(upload_jobs)"
                )
            }
            owner_index_columns = [
                row[2]
                for row in connection.exec_driver_sql(
                    "PRAGMA index_info(ix_upload_jobs_owner_sub)"
                )
            ]

        assert "owner_sub" in columns
        assert owner_sub == auth.LOCAL_OWNER_SUB
        assert "ix_upload_jobs_owner_sub" in indexes
        assert owner_index_columns == ["owner_sub"]
    finally:
        legacy_engine.dispose()

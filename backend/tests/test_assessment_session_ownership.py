"""Multi-user isolation regressions for concept-mapping assessment sessions."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.engine import URL

from app import config, models
from app import db as db_module
from app.main import app
from app.services import auth
from app.services import build_assessments as assessments


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
        "assessment-session-owner-test-secret-" + ("x" * 40),
    )
    monkeypatch.setattr(config, "SECURE_COOKIES", True)
    monkeypatch.setattr(config, "LEGACY_OWNER_EMAIL", "")


def _principal(sub: str, *, email: str | None = None) -> auth.Principal:
    return auth.Principal(
        sub=sub,
        email=email or f"{sub}@up.school",
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


def test_concept_mapping_session_routes_are_owner_scoped(db, monkeypatch):
    _enable_google_mode(monkeypatch)
    concept_id = db.query(models.Concept.id).order_by(
        models.Concept.id,
    ).limit(1).scalar()
    assert concept_id is not None

    owner = _signed_client("assessment-session-owner")
    outsider = _signed_client("assessment-session-outsider")
    session_id: int | None = None
    try:
        created = owner.post(
            "/build-assessments/sessions",
            json={"scope_type": "concept", "scope_ids": [concept_id]},
        )
        assert created.status_code == 200
        session_id = created.json()["id"]

        db.expire_all()
        stored = db.get(models.AssessmentSession, session_id)
        assert stored is not None
        assert stored.owner_sub == "assessment-session-owner"

        assert owner.get(
            f"/build-assessments/sessions/{session_id}",
        ).status_code == 200
        assert outsider.get(
            f"/build-assessments/sessions/{session_id}",
        ).status_code == 404

        batch_payload = {
            "cognitive_skills": ["Understand"],
            "difficulty_levels": ["Moderate"],
            "categories": ["Multiple Choice Question"],
            "question_type": "objective",
            "num_questions": 1,
            "appears_in": ["Worksheet"],
        }
        assert outsider.post(
            f"/build-assessments/sessions/{session_id}/batches",
            json=batch_payload,
        ).status_code == 404
        db.expire_all()
        assert len(db.get(models.AssessmentSession, session_id).batches) == 0

        assert owner.post(
            f"/build-assessments/sessions/{session_id}/batches",
            json=batch_payload,
        ).status_code == 200
        assert outsider.post(
            f"/build-assessments/sessions/{session_id}/generate",
        ).status_code == 404

        # The worker opens a fresh database session, so the service itself must
        # repeat the ownership check rather than trusting the route preflight.
        with pytest.raises(assessments.AssessmentSessionNotFound):
            assessments.generate(
                db,
                session_id,
                owner_sub="assessment-session-outsider",
            )
    finally:
        owner.close()
        outsider.close()
        if session_id is not None:
            db.expire_all()
            stored = db.get(models.AssessmentSession, session_id)
            if stored is not None:
                db.delete(stored)
                db.commit()


def test_configured_legacy_adoption_moves_both_private_work_tables(
    db, monkeypatch,
):
    _enable_google_mode(monkeypatch)
    monkeypatch.setattr(
        config,
        "LEGACY_OWNER_EMAIL",
        "legacy-owner@up.school",
    )

    legacy_upload = models.UploadJob(
        owner_sub=auth.LOCAL_OWNER_SUB,
        module="build_assessments",
        upload_type="questions",
        filename="legacy.txt",
    )
    legacy_session = models.AssessmentSession(
        owner_sub=auth.LOCAL_OWNER_SUB,
        source="concept_mapping",
        scope_type="concept",
        scope_ids=[],
    )
    foreign_session = models.AssessmentSession(
        owner_sub="existing-google-owner",
        source="concept_mapping",
        scope_type="concept",
        scope_ids=[],
    )
    db.add_all([legacy_upload, legacy_session, foreign_session])
    db.commit()

    local_upload_ids = [
        row.id
        for row in db.query(models.UploadJob.id).filter(
            models.UploadJob.owner_sub == auth.LOCAL_OWNER_SUB,
        )
    ]
    local_session_ids = [
        row.id
        for row in db.query(models.AssessmentSession.id).filter(
            models.AssessmentSession.owner_sub == auth.LOCAL_OWNER_SUB,
        )
    ]
    try:
        adopted = auth.adopt_legacy_upload_jobs(
            db,
            _principal(
                "legacy-google-sub",
                email="legacy-owner@up.school",
            ),
        )
        assert adopted == len(local_upload_ids) + len(local_session_ids)
        db.expire_all()
        assert db.get(
            models.UploadJob,
            legacy_upload.id,
        ).owner_sub == "legacy-google-sub"
        assert db.get(
            models.AssessmentSession,
            legacy_session.id,
        ).owner_sub == "legacy-google-sub"
        assert db.get(
            models.AssessmentSession,
            foreign_session.id,
        ).owner_sub == "existing-google-owner"
    finally:
        db.query(models.UploadJob).filter(
            models.UploadJob.id.in_(local_upload_ids),
            models.UploadJob.owner_sub == "legacy-google-sub",
        ).update(
            {models.UploadJob.owner_sub: auth.LOCAL_OWNER_SUB},
            synchronize_session=False,
        )
        db.query(models.AssessmentSession).filter(
            models.AssessmentSession.id.in_(local_session_ids),
            models.AssessmentSession.owner_sub == "legacy-google-sub",
        ).update(
            {models.AssessmentSession.owner_sub: auth.LOCAL_OWNER_SUB},
            synchronize_session=False,
        )
        db.delete(foreign_session)
        db.delete(legacy_session)
        db.delete(legacy_upload)
        db.commit()


def test_legacy_sqlite_assessment_sessions_gain_owner_backfill_and_index(
    tmp_path, monkeypatch,
):
    database_path = tmp_path / "legacy-assessment-session.sqlite3"
    legacy_engine = create_engine(
        URL.create("sqlite", database=str(database_path)),
    )
    try:
        with legacy_engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE assessment_sessions ("
                "id INTEGER PRIMARY KEY, "
                "source VARCHAR(32) DEFAULT 'concept_mapping'"
                ")"
            )
            connection.exec_driver_sql(
                "INSERT INTO assessment_sessions (id) VALUES (1)"
            )

        monkeypatch.setattr(
            db_module,
            "DB_URL",
            "sqlite:///legacy-assessment-session.sqlite3",
        )
        monkeypatch.setattr(db_module, "engine", legacy_engine)
        db_module._ensure_columns()

        with legacy_engine.connect() as connection:
            columns = {
                row[1]
                for row in connection.exec_driver_sql(
                    "PRAGMA table_info(assessment_sessions)"
                )
            }
            owner_sub = connection.exec_driver_sql(
                "SELECT owner_sub FROM assessment_sessions WHERE id = 1"
            ).scalar_one()
            indexes = {
                row[1]
                for row in connection.exec_driver_sql(
                    "PRAGMA index_list(assessment_sessions)"
                )
            }
            owner_index_columns = [
                row[2]
                for row in connection.exec_driver_sql(
                    "PRAGMA index_info(ix_assessment_sessions_owner_sub)"
                )
            ]

        assert "owner_sub" in columns
        assert owner_sub == auth.LOCAL_OWNER_SUB
        assert "ix_assessment_sessions_owner_sub" in indexes
        assert owner_index_columns == ["owner_sub"]
    finally:
        legacy_engine.dispose()

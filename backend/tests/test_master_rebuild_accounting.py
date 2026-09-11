"""Explicit per-lane Master rebuilds persist their usage and run log (Q41/Q43)."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import config
from app.api import build_assessments as api
from app.services import assessment_release_run as master_run
from app.services import build_concepts_release_contract as release_contract
from app.services import auth, model_provider, openai_usage, progress, uploads
from tests.test_build_concepts_release import _job


def _usage_response():
    usage = SimpleNamespace(
        prompt_tokens=100, completion_tokens=20, total_tokens=120,
        prompt_tokens_details=SimpleNamespace(cached_tokens=0),
        completion_tokens_details=SimpleNamespace(reasoning_tokens=0),
    )
    return SimpleNamespace(model="gpt-5.6-luna", usage=usage,
                           choices=[SimpleNamespace(message=SimpleNamespace(content="{}"), finish_reason="stop")])


def _release(lane):
    return SimpleNamespace(
        id=7, release_uid="REL-7", version=1, state="published", diagnostics={}, payload={},
        lane=lane, concept_snapshot_sha256="abc", workbook_hashes={}, publication={},
        created_at=datetime.now(timezone.utc),
    )


@pytest.fixture
def generated_job(db, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads")
    job, _chapter = _job(db)
    job.status = "generated"
    db.commit()
    db.refresh(job)
    return job


@pytest.mark.parametrize("lane", ["post", "pre"])
def test_from_job_rebuild_route_persists_usage_and_run_log_on_success(db, generated_job, monkeypatch, lane):
    job = generated_job
    seen = {}

    def rebuild(db_, job_id, lane_, *, owner_sub=None, claim_job_lock=False, **_kwargs):
        assert (job_id, lane_, claim_job_lock) == (job.id, lane, True)
        assert uploads.is_job_running(job_id) is False, "the rebuild claims the lock itself"
        seen["tracking"] = openai_usage.is_tracking()
        seen["profile"] = model_provider.bound_profile()
        progress.log(f"Master rebuild authored one {lane_} decision.")
        openai_usage.record_response(_usage_response())
        return _release(lane_)

    monkeypatch.setattr(release_contract, "rebuild_lane_master", rebuild)
    route = api.run_pre_release_from_job if lane == "pre" else api.run_release_from_job
    summary = route(job.id, db=db, user=auth.LOCAL_PRINCIPAL)
    assert summary["id"] == 7 and summary["lane"] == lane
    assert seen["tracking"] is True
    # A job with recorded history binds its saved (historical) routing, never a fresh one.
    assert seen["profile"] is None
    db.expire_all()
    saved = uploads.get_job(db, job.id)
    assert saved.openai_usage["request_count"] == 1
    assert saved.openai_usage["total_tokens"] == 120
    messages = [event.get("message", "") for event in saved.generation_log if event.get("type") == "log"]
    assert any(f"Master rebuild authored one {lane} decision." in message for message in messages)

    # A second explicit rebuild accumulates onto the persisted ledger.
    route(job.id, db=db, user=auth.LOCAL_PRINCIPAL)
    db.expire_all()
    again = uploads.get_job(db, job.id)
    assert again.openai_usage["request_count"] == 2
    assert again.openai_usage["total_tokens"] == 240


def test_from_job_rebuild_route_persists_usage_and_diagnostic_on_failure(db, generated_job, monkeypatch):
    job = generated_job

    def rebuild(db_, job_id, lane_, *, owner_sub=None, claim_job_lock=False, **_kwargs):
        openai_usage.record_response(_usage_response())
        progress.log("Master rebuild spent one decision before refusing.")
        raise master_run.ReleaseRunError("the post Master file cannot be built: staged rows missing")

    monkeypatch.setattr(release_contract, "rebuild_lane_master", rebuild)
    with pytest.raises(HTTPException) as raised:
        api.run_release_from_job(job.id, db=db, user=auth.LOCAL_PRINCIPAL)
    assert raised.value.status_code == 400
    db.expire_all()
    saved = uploads.get_job(db, job.id)
    assert saved.openai_usage["request_count"] == 1
    errors = [event for event in saved.generation_log
              if event.get("type") == "log" and event.get("level") == "error"]
    assert any("staged rows missing" in event.get("message", "") for event in errors)
    assert any("spent one decision" in event.get("message", "") for event in saved.generation_log)


def test_unknown_job_still_reaches_the_rebuild_refusal_without_accounting(db, monkeypatch):
    claims = []

    def rebuild(*_args, **kwargs):
        claims.append(bool(kwargs.get("claim_job_lock")))
        raise uploads.UploadJobNotFound("upload job not found")

    monkeypatch.setattr(release_contract, "rebuild_lane_master", rebuild)
    with pytest.raises(HTTPException) as raised:
        api.run_release_from_job(880404, db=db, user=auth.LOCAL_PRINCIPAL)
    assert raised.value.status_code == 404
    assert claims == [True]

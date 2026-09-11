"""A saved Concept-review boundary does not preflight a retired Pre author."""
from __future__ import annotations

import copy
import inspect

import pytest

from app import config, models
from app.services import build_concepts_release as release
from app.services import model_provider, model_routing_run, openai_usage, uploads


def _legacy_job(db, monkeypatch, tmp_path, marker):
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setenv("AEGIS_ALLOW_DRY", "0")
    monkeypatch.delenv("AEGIS_USE_LIVE", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-no-provider-call")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    job = models.UploadJob(
        module="build_concepts", filename="legacy-source.txt", status="concept_review",
        mmd_text="Source work already recorded.",
        question_inventory={release.CONCEPT_REVIEW_KEY: copy.deepcopy(marker)} if marker is not None else {},
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    model_routing_run.save_profile_for_job(job, model_provider.legacy_profile())
    path = model_routing_run._record_path(job)
    return job, path, path.read_bytes()


@pytest.mark.parametrize("status", release.CONCEPT_REVIEW_STATUSES)
def test_review_and_continue_enter_with_openai_without_old_gemini_credential(db, monkeypatch, tmp_path, status):
    job, path, saved = _legacy_job(db, monkeypatch, tmp_path, {
        "version": release.CONCEPT_REVIEW_VERSION, "status": status,
        "concept_release_uids": {"post": "post-source-release", "pre": "reviewed-pre-release"},
    })
    reached = []

    def work():
        # Outer historical work retains its recorded profile. A corrected
        # Pre workflow can now enter and bind its separate new mini revision.
        assert model_provider.bound_profile() == model_provider.legacy_profile()
        with model_provider.bind_profile(model_provider.new_profile()):
            assert model_provider.bound_profile() == model_provider.new_profile()
            reached.append(True)
        return {"entered": True}

    # Exercise the real ownership/lock/usage/preflight boundary. The public
    # decorators only prepare canonical source sessions, outside this repair.
    usage_boundary = inspect.unwrap(uploads.run_with_openai_usage)
    with openai_usage.track():
        result = usage_boundary(db, job.id, work)
    assert result["entered"] and reached == [True]
    assert result["openai_usage"]["request_count"] == 0
    assert path.read_bytes() == saved


@pytest.mark.parametrize("marker", [
    None,
    {},
    {"version": release.CONCEPT_REVIEW_VERSION, "status": "pending_review"},
    {"version": "unknown-review-policy", "status": "pending_review", "concept_release_uids": {"post": "uid"}},
    {"version": release.CONCEPT_REVIEW_VERSION, "status": "source_building", "concept_release_uids": {"post": "uid"}},
])
def test_original_source_phase_still_requires_its_recorded_pre_author_credential(db, monkeypatch, tmp_path, marker):
    job, path, saved = _legacy_job(db, monkeypatch, tmp_path, marker)
    reached = []
    usage_boundary = inspect.unwrap(uploads.run_with_openai_usage)
    with openai_usage.track(), pytest.raises(config.LiveRequiredError, match="GEMINI_API_KEY"):
        usage_boundary(db, job.id, lambda: reached.append(True))
    assert reached == []
    assert path.read_bytes() == saved

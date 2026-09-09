from types import SimpleNamespace
import json

import pytest

from app import config
from app.services import model_provider, model_routing_run


def _job(**changes):
    return SimpleNamespace(**{
        "id": 42, "upload_storage_key": "42/source.pdf", "filename": "source.pdf",
        "mmd_text": "", "generation_checkpoint": {}, "question_inventory": {},
        "openai_usage": {}, "module": "build_concepts", **changes,
    })


@pytest.fixture(autouse=True)
def isolated_artifacts(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads")


def test_profile_is_frozen_before_conversion_and_reused_after_checkpoint():
    job = _job()
    with model_routing_run.bind_job(job):
        assert model_provider.bound_profile() == model_provider.new_profile()
    saved = model_routing_run._record_path(job).read_bytes()
    job.mmd_text = "converted content"
    job.generation_checkpoint = {"stage": "pre_type_assignment"}
    assert model_routing_run.profile_for_job(job) == model_provider.new_profile()
    assert model_routing_run._record_path(job).read_bytes() == saved


def test_historical_converted_upload_does_not_acquire_new_routing():
    with model_routing_run.bind_job(_job(mmd_text="existing paid source")):
        assert model_provider.bound_profile() is None
    assert model_provider.bound_profile() == model_provider.new_profile()


def test_replacement_has_a_distinct_profile_record():
    old = _job(mmd_text="existing source")
    assert model_routing_run.profile_for_job(old) is None
    replacement = _job(upload_storage_key="42/replacement.pdf")
    assert model_routing_run.profile_for_job(replacement) == model_provider.new_profile()
    assert model_routing_run._record_path(old) != model_routing_run._record_path(replacement)


def test_full_run_checks_gemini_before_work_but_conversion_only_needs_openai(monkeypatch):
    monkeypatch.setenv("AEGIS_ALLOW_DRY", "0")
    monkeypatch.delenv("AEGIS_USE_LIVE", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    job = _job()
    with model_routing_run.bind_job(job):
        pass
    reached_work = []
    with pytest.raises(config.LiveRequiredError, match="GEMINI_API_KEY"):
        with model_routing_run.bind_job(job, require_pre=True):
            reached_work.append(True)
    assert not reached_work
    monkeypatch.setenv("GEMINI_API_KEY", "test-only")
    with model_routing_run.bind_job(job, require_pre=True):
        assert model_provider.bound_profile() == model_provider.new_profile()


def test_altered_record_is_rejected_before_any_request():
    job = _job()
    model_routing_run.profile_for_job(job)
    path = model_routing_run._record_path(job)
    record = json.loads(path.read_text())
    record["profile"]["routes"]["default"]["model"] = "gemini-3.8-flash"
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="routing profile"):
        with model_routing_run.bind_job(job):
            pytest.fail("invalid profile reached work")


def test_profile_changes_source_identity_but_historical_hash_stays_stable():
    from app.services import canonical_source_phase3, generation

    with model_provider.bind_profile(None):
        legacy = generation._metadata(subject="English", chapter_title="The Mother Bird")
    assert model_provider.PROFILE_KEY not in legacy
    fresh = generation._metadata(subject="English", chapter_title="The Mother Bird")
    assert fresh[model_provider.PROFILE_KEY] == model_provider.new_profile()
    old_hash = canonical_source_phase3.semantic_context_hash(legacy)
    assert canonical_source_phase3.semantic_context_hash(fresh) != old_hash
    fresh.pop(model_provider.PROFILE_KEY)
    assert canonical_source_phase3.semantic_context_hash(fresh) == old_hash

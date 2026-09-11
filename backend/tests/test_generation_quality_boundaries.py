"""New upload policy reaches semantic stages; saved uploads keep their contract."""
import json
from types import SimpleNamespace

import pytest

from app import config
from app.services import assessment_profile, column_spec, generation
from app.services import generation_quality_policy as quality
from app.services import model_provider, model_routing_run
from app.services import canonical_source_phase3, prelearning_capture_policy
from app.services.phase3 import prompts


def _job(**changes):
    return SimpleNamespace(**{
        "id": 840, "upload_storage_key": "840/source.pdf", "filename": "source.pdf",
        "mmd_text": "", "generation_checkpoint": {}, "question_inventory": {},
        "openai_usage": {}, "module": "build_concepts", **changes,
    })


def test_new_upload_freezes_quality_before_source_and_preserves_on_resume(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads")
    job = _job()
    with model_routing_run.bind_job(job):
        metadata = generation._metadata(subject="Mathematics", grade="10")
    assert quality.active(metadata)
    saved = model_routing_run._record_path(job).read_bytes()
    assert json.loads(saved)[quality.KEY] == quality.VERSION
    job.mmd_text = "already paid for source"
    job.generation_checkpoint = {"stage": "pre_type_assignment"}
    with model_routing_run.bind_job(job):
        assert generation._metadata(subject="Mathematics", grade="10") == metadata
    assert model_routing_run._record_path(job).read_bytes() == saved


@pytest.mark.parametrize("profile", [None, model_provider.legacy_profile(), model_provider.new_profile()])
def test_saved_upload_without_quality_is_not_upgraded(monkeypatch, tmp_path, profile):
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads")
    job = _job(mmd_text="saved source")
    model_routing_run.save_profile_for_job(job, profile)
    saved = model_routing_run._record_path(job).read_bytes()
    with model_routing_run.bind_job(job):
        metadata = generation._metadata(subject="English")
        assert not quality.active(metadata)
        assert quality.POST_DESCRIPTION_INSTRUCTION not in generation._metadata_block(metadata)
    assert model_routing_run._record_path(job).read_bytes() == saved


def test_explicit_quality_survives_profile_resolution_and_master_binding():
    metadata = {"subject": "Mathematics", "grade": "10", quality.KEY: quality.VERSION}
    profile = assessment_profile.resolve_for_metadata(None, metadata)
    assert quality.active(profile)
    assert quality.KEY not in profile["_resolved_metadata"]  # not a curriculum selector
    reentered = assessment_profile.resolve_for_metadata(profile, {})
    assert quality.active(column_spec.bind_metadata({}, reentered))
    historical = assessment_profile.resolve_for_metadata(None, {"subject": "Mathematics"})
    assert not quality.active(column_spec.bind_metadata({}, historical))


def test_pre_and_post_prompts_receive_only_their_quality_scope():
    metadata = {quality.KEY: quality.VERSION}
    env = {"metadata": metadata}
    post = prompts.instruction_rules_suffix(env)
    assert quality.POST_DESCRIPTION_INSTRUCTION in post
    assert prelearning_capture_policy.QUALITY_INSTRUCTION not in post
    for slots in (prompts.PRE_LEARNING_SLOTS, prompts.PRE_QUESTION_SLOTS):
        pre = prompts.instruction_rules_suffix(env, slots=slots)
        assert prelearning_capture_policy.QUALITY_INSTRUCTION in pre
        assert quality.POST_DESCRIPTION_INSTRUCTION not in pre
    assert prompts.instruction_rules_suffix({"metadata": {}}) == ""


def test_quality_changes_source_semantic_identity_without_changing_historical_hash():
    metadata = {"subject": "Mathematics", "grade": "10"}
    original = canonical_source_phase3.semantic_context_hash(metadata)
    current = {**metadata, quality.KEY: quality.VERSION}
    assert canonical_source_phase3.semantic_context_hash(current) != original
    current.pop(quality.KEY)
    assert canonical_source_phase3.semantic_context_hash(current) == original


def test_post_policy_survives_a_refused_pre_map_in_its_shared_checkpoint_bundle():
    from app.services import build_concepts_release

    pre_map = {"records": [], "refused": "recorded prerequisite review finding"}
    bundle = generation.phase3_pre_release_bundle(
        pre_map, {"questions": {}}, generation_policy={quality.KEY: quality.VERSION},
    )
    assert generation.valid_phase3_pre_release_bundle(bundle)
    assert quality.KEY not in pre_map
    assert build_concepts_release.generation_quality_fields({
        generation.PHASE3_PRE_RELEASE_FIELD: bundle,
    }) == {quality.KEY: quality.VERSION}
    historical = generation.phase3_pre_release_bundle(pre_map, {"questions": {}})
    assert not quality.active(historical)


@pytest.mark.parametrize("lane", ["Pre", "Post"])
def test_terminal_concept_refiner_reviews_quality_without_changing_identity(lane):
    from app.services import release_refiner
    from app.services.phase3 import kernel
    from tests.test_release_refiner import _METADATA, _rows, _Provider

    metadata = {**_METADATA, "pre_post": lane, quality.KEY: quality.VERSION}
    seen = []
    original = _rows()

    def author(payload):
        seen.append(payload)
        return _Provider()(payload)

    rows, _, _ = release_refiner.refine_release(
        original, metadata=metadata, provider=author,
        output_kind="pre_concepts_release" if lane == "Pre" else "concepts_release",
        critic=lambda payload: {"verdict": "verified", "confidence": 1.0, "issues": []},
        store=kernel.DecisionStore(),
    )
    assert rows == original
    assert seen
    for payload in seen:
        assert quality.active(payload)
        expected = (prelearning_capture_policy.QUALITY_INSTRUCTION
                    if lane == "Pre" else quality.POST_DESCRIPTION_INSTRUCTION)
        assert expected in payload["rules"]
        assert "existing prose whitelist" in payload["rules"]

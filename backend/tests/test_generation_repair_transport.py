"""Fresh Q48 work adopts repairs without changing already recorded runs."""
from __future__ import annotations

import json

import pytest

from app import config, models
from app.services import (
    assessment_profile,
    checkpoints,
    column_spec,
    generation,
    generation_quality_policy as quality,
    generation_repair_policy as repair,
    model_provider,
    model_routing_run,
)
from tests.test_concept_checkpoint_bundles import _job as _checkpoint_job, _resign
from tests.test_model_routing_run import _job as _source_job


def test_fresh_upload_freezes_repair_and_replacement_adopts_it(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads")
    old = _source_job(mmd_text="previously converted source")
    with model_routing_run.bind_job(old):
        assert repair.run_fields() == {}
    old_bytes = model_routing_run._record_path(old).read_bytes()

    fresh = _source_job(upload_storage_key="42/replacement.pdf")
    with model_routing_run.bind_job(fresh):
        assert repair.run_fields() == {repair.KEY: repair.VERSION}
        assert generation._metadata()[repair.KEY] == repair.VERSION
    path = model_routing_run._record_path(fresh)
    saved_bytes = path.read_bytes()
    record = json.loads(saved_bytes)
    assert record[repair.KEY] == repair.VERSION
    assert record[quality.KEY] == quality.VERSION
    assert record["profile"] == model_provider.new_profile()

    fresh.mmd_text = "new converted source"
    fresh.generation_checkpoint = {"stage": "pre_type_assignment"}
    with model_routing_run.bind_job(fresh):
        assert repair.run_fields() == {repair.KEY: repair.VERSION}
    assert path.read_bytes() == saved_bytes
    assert model_routing_run._record_path(old).read_bytes() == old_bytes


@pytest.mark.parametrize("quality_version", [None, quality.VERSION])
def test_recorded_mini_run_without_repair_stays_historical(monkeypatch, tmp_path, quality_version):
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads")
    job = _source_job()
    model_routing_run.save_profile_for_job(
        job, model_provider.new_profile(), quality_version=quality_version,
    )
    path = model_routing_run._record_path(job)
    saved = path.read_bytes()
    with repair.bind_run(repair.VERSION), model_routing_run.bind_job(job):
        assert model_provider.bound_profile() == model_provider.new_profile()
        assert repair.run_fields() == {}
        assert repair.KEY not in generation._metadata()
    assert path.read_bytes() == saved


@pytest.mark.parametrize("internal_backup", [False, True])
def test_repair_stamp_roundtrips_before_envelope_and_binds_restored_metadata(db, internal_backup):
    original = _checkpoint_job(db)
    profile = model_provider.new_profile()
    model_routing_run.save_profile_for_job(
        original, profile, quality_version=quality.VERSION,
        repair_version=repair.VERSION,
    )
    with model_routing_run.bind_job(original):
        original_metadata = generation._metadata()
    exporter = checkpoints.export_bundle_for_internal_backup if internal_backup else checkpoints.export_bundle
    # An export worker can be bound to an unrelated historical upload.
    with model_provider.bind_profile(None), quality.bind_run(None), repair.bind_run(None):
        _, raw = exporter(db, original.id)
    payload = json.loads(raw)["payload"]
    assert payload[repair.KEY] == repair.VERSION
    assert "envelope" not in payload["generation_checkpoint"]

    restored = checkpoints.import_bundle(db, raw)
    assert json.loads(model_routing_run._record_path(restored).read_text()) == {
        "version": 1, "profile": profile, quality.KEY: quality.VERSION,
        repair.KEY: repair.VERSION,
    }
    with model_routing_run.bind_job(restored):
        assert repair.run_fields() == {repair.KEY: repair.VERSION}
        assert generation._metadata() == original_metadata
    _, reexported = checkpoints.export_bundle(db, restored.id)
    assert json.loads(reexported)["payload"][repair.KEY] == repair.VERSION


@pytest.mark.parametrize("invalid", [None, "unknown", {}, True, 1, []])
def test_invalid_portable_repair_stamp_is_rejected_before_any_write(db, monkeypatch, invalid):
    original = _checkpoint_job(db)
    _, raw = checkpoints.export_bundle(db, original.id)
    bundle = json.loads(raw)
    bundle["payload"][repair.KEY] = invalid
    _resign(bundle)
    count = db.query(models.UploadJob).count()

    def unexpected_write(*args, **kwargs):
        pytest.fail("invalid repair policy reached a routing-record write")

    monkeypatch.setattr(model_routing_run, "save_profile_for_job", unexpected_write)
    with pytest.raises(ValueError, match="generation repair policy"):
        checkpoints.import_bundle(db, checkpoints._json_bytes(bundle))
    assert db.query(models.UploadJob).count() == count


def test_portable_repair_stamp_is_checksum_covered(db):
    original = _checkpoint_job(db)
    _, raw = checkpoints.export_bundle(db, original.id)
    bundle = json.loads(raw)
    bundle["payload"][repair.KEY] = repair.VERSION
    with pytest.raises(ValueError, match="checksum"):
        checkpoints.import_bundle(db, checkpoints._json_bytes(bundle))


@pytest.mark.parametrize("invalid", [None, "unknown", {}, True, 1, []])
def test_invalid_saved_repair_stamp_cannot_reach_work(monkeypatch, tmp_path, invalid):
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads")
    job = _source_job()
    model_routing_run.profile_for_job(job)
    path = model_routing_run._record_path(job)
    record = json.loads(path.read_text())
    record[repair.KEY] = invalid
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="generation repair policy"):
        with model_routing_run.bind_job(job):
            pytest.fail("invalid repair policy reached generation")


def test_assessment_profile_and_column_binding_carry_only_explicit_repair_stamp():
    metadata = {"board": "CBSE", "grade": "Grade 06", "subject": "Mathematics"}
    historical = assessment_profile.resolve_for_metadata(None, metadata)
    with repair.bind_run(repair.VERSION):
        replay = assessment_profile.resolve_for_metadata(historical, metadata)
        assert replay == historical
        assert repair.KEY not in column_spec.bind_metadata(metadata, replay)

    current = assessment_profile.resolve_for_metadata(
        None, {**metadata, repair.KEY: repair.VERSION},
    )
    assert current[repair.KEY] == repair.VERSION
    assert repair.KEY not in current["_resolved_metadata"]
    replay = assessment_profile.resolve_for_metadata(current, {})
    assert replay == current
    assert column_spec.bind_metadata(metadata, replay)[repair.KEY] == repair.VERSION
    assert repair.KEY not in metadata

"""Portable checkpoints retain routing before any restored run can spend."""
from __future__ import annotations

import json
import io
import zipfile

import pytest

from app import models
from app.services import checkpoints, generation, model_provider, model_routing_run
from app.services import build_concepts_release_files as release_files
from tests.test_concept_checkpoint_bundles import _job, _resign


@pytest.mark.parametrize("internal_backup", [False, True])
@pytest.mark.parametrize("profile_name,expected_model", [
    ("new_profile", "gpt-5.4-mini"),
    ("legacy_profile", "gemini-3.8-flash"),
])
def test_versioned_profile_roundtrips_before_phase3_and_drives_restored_metadata(db, internal_backup, profile_name, expected_model):
    original = _job(db)
    profile = getattr(model_provider, profile_name)()
    model_routing_run.save_profile_for_job(original, profile)
    exporter = checkpoints.export_bundle_for_internal_backup if internal_backup else checkpoints.export_bundle
    # Drive exports run in an independent worker; the saved run record wins
    # over whichever profile the exporting thread happens to have bound.
    with model_provider.bind_profile(None):
        _, raw = exporter(db, original.id)
    payload = json.loads(raw)["payload"]
    assert payload[model_provider.PROFILE_KEY] == profile
    assert "envelope" not in payload["generation_checkpoint"]
    restored = checkpoints.import_bundle(db, raw)
    assert restored.id != original.id
    assert model_routing_run.recorded_profile_for_job(restored) == profile
    with model_routing_run.bind_job(restored):
        assert generation._metadata()[model_provider.PROFILE_KEY] == profile
        assert model_provider.resolve_route("pre_learning", stage="prequestions.author").model == expected_model
    _, reexported = checkpoints.export_bundle(db, restored.id)
    assert json.loads(reexported)["payload"][model_provider.PROFILE_KEY] == profile


def test_legacy_bundle_absence_remains_legacy_on_restore_and_export_never_mints(db):
    original = _job(db)
    assert not model_routing_run._record_path(original).exists()
    _, raw = checkpoints.export_bundle(db, original.id)
    assert not model_routing_run._record_path(original).exists()
    assert model_provider.PROFILE_KEY not in json.loads(raw)["payload"]
    restored = checkpoints.import_bundle(db, raw)
    assert json.loads(model_routing_run._record_path(restored).read_text())["profile"] is None
    with model_routing_run.bind_job(restored):
        assert model_provider.bound_profile() is None
        assert model_provider.PROFILE_KEY not in generation._metadata()
    _, reexported = checkpoints.export_bundle(db, restored.id)
    assert model_provider.PROFILE_KEY not in json.loads(reexported)["payload"]


@pytest.mark.parametrize("profile", [{}, {"version": "unknown"}, "gemini", 1])
def test_malformed_portable_profile_is_rejected_before_import(db, profile):
    original = _job(db)
    _, raw = checkpoints.export_bundle(db, original.id)
    bundle = json.loads(raw)
    bundle["payload"][model_provider.PROFILE_KEY] = profile
    _resign(bundle)
    count = db.query(models.UploadJob).count()
    with pytest.raises(ValueError, match="model routing profile"):
        checkpoints.import_bundle(db, checkpoints._json_bytes(bundle))
    assert db.query(models.UploadJob).count() == count


def test_failed_import_commit_does_not_leave_a_profile_for_reused_job_id(db, monkeypatch):
    original = _job(db)
    model_routing_run.save_profile_for_job(original, model_provider.new_profile())
    _, raw = checkpoints.export_bundle(db, original.id)
    saved_paths = []
    save = model_routing_run.save_profile_for_job

    def record_save(job, profile):
        result = save(job, profile)
        saved_paths.append(model_routing_run._record_path(job))
        return result

    def fail_commit():
        raise RuntimeError("commit failed")

    monkeypatch.setattr(model_routing_run, "save_profile_for_job", record_save)
    monkeypatch.setattr(db, "commit", fail_commit)
    with pytest.raises(RuntimeError, match="commit failed"):
        checkpoints.import_bundle(db, raw)
    assert len(saved_paths) == 1 and not saved_paths[0].exists()


def test_diagnostics_contains_only_active_routing_record_among_upload_siblings(db):
    job = _job(db)
    profile = model_provider.new_profile()
    model_routing_run.save_profile_for_job(job, profile)
    path = model_routing_run._record_path(job)
    # The source replacement record and unrelated upload siblings are outside
    # canonical artifacts and must not be swept into this run's diagnostics.
    (path.parent / "source.model-routing.obsolete.json").write_text('{"profile":"obsolete"}')
    (path.parent / "unrelated-sibling.txt").write_text("unrelated")
    with zipfile.ZipFile(io.BytesIO(release_files.build_diagnostics_zip(job))) as archive:
        assert json.loads(archive.read("source/model-routing.json")) == {
            "version": 1, "profile": profile,
        }
        assert not any("obsolete" in name or "unrelated-sibling" in name for name in archive.namelist())

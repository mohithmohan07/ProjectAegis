"""Frozen source decisions survive deployment model changes without relabeling."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import config
from app.services import canonical_source_phase22 as phase22
from app.services import canonical_source_phase34_structured_output_contract as phase34
from app.services import model_provider


def test_recorded_v1_source_keys_keep_the_original_luna_identity(monkeypatch):
    """Compare against the pre-migration cache material, not mutable config."""
    profile = model_provider.legacy_profile()
    monkeypatch.setattr(config, "OPENAI_MODEL", "gpt-5.4-mini")
    monkeypatch.setattr(phase22, "_pdf_sha256", lambda _: "pdf-sha")
    # This is the exact v1 serializer used when Fly's model was Luna.
    old22_material = "\u241f".join([
        phase22.ADJUDICATION_VERSION, "gpt-5.6-luna", "source-sha", "pdf-sha", "packet",
    ]) + "\u241f" + json.dumps(profile, sort_keys=True, separators=(",", ":"))
    old34_material = {
        model_provider.PROFILE_KEY: profile,
        "version": phase34._TURNOVER_VERSION,
        "compiler": phase34.phase3.COMPILER_VERSION,
        "model": "gpt-5.6-luna",
        "kind": "hierarchy",
        "payload_sha256": phase34.phase3._sha256_json({"sections": []}),
        "target_ids": ["section-1"],
    }
    with model_provider.bind_profile(profile):
        assert phase22._cache_key(
            canonical={"source_contract": {"source_sha256": "source-sha"}},
            source_path=Path("unused"), packet={"fingerprint": "packet"},
        ) == hashlib.sha256(old22_material.encode("utf-8")).hexdigest()
        assert phase34._cache_key(
            kind="hierarchy", payload={"sections": []}, target_ids=["section-1"],
        ) == phase34.phase3._sha256_json(old34_material)


@pytest.mark.parametrize("profile", [model_provider.legacy_profile(), model_provider.new_profile()], ids=["v1", "v2"])
def test_source_adjudication_replays_frozen_cache_after_config_change(tmp_path, monkeypatch, profile):
    from app.services import uploads
    from tests.test_canonical_source_phase22 import _compile, _corrupted_rne, _decision, _make_pdf

    pdf = tmp_path / "RNE.pdf"
    _make_pdf(pdf)
    monkeypatch.setattr(uploads, "upload_file_path", lambda _: pdf)
    monkeypatch.setattr(uploads, "source_artifact_directory", lambda _: tmp_path / "artifacts")
    monkeypatch.setattr(phase22, "_CACHE_DIR", tmp_path / "cache")
    expected_model = profile["routes"]["default"]["model"]
    source = _corrupted_rne()
    calls = []

    def provider(packet, _pages):
        calls.append(packet["fingerprint"])
        return _decision(packet)

    with model_provider.bind_profile(profile):
        for configured_model in ("gpt-5.6-luna", "gpt-5.4-mini"):
            monkeypatch.setattr(config, "OPENAI_MODEL", configured_model)
            compiled = _compile(source)
            job = SimpleNamespace(
                id=72, filename="RNE.pdf", mmd_text=source,
                generation_checkpoint={}, question_inventory={}, detail="",
            )
            canonical, _report, ready = phase22.adjudicate_job_source(
                SimpleNamespace(commit=lambda: None), job,
                compiled.canonical, compiled.report, decision_provider=provider,
            )
            assert ready
            repairs = canonical["source_adjudication"]["decisions"]
            assert len(repairs) == 2
            assert {entry["provenance"]["model"] for entry in repairs} == {expected_model}
        assert len(calls) == 2  # No new provider decisions after configuration changes.
    cache_files = list((tmp_path / "cache").glob("*.json"))
    assert len(cache_files) == 2
    assert {json.loads(path.read_text())["model"] for path in cache_files} == {expected_model}


@pytest.mark.parametrize("profile", [model_provider.legacy_profile(), model_provider.new_profile()], ids=["v1", "v2"])
def test_hierarchy_cache_and_receipts_follow_frozen_model(tmp_path, monkeypatch, profile):
    monkeypatch.setattr(phase34, "_artifact_dir", lambda: tmp_path)
    kwargs = {"kind": "hierarchy", "payload": {"sections": []}, "target_ids": ["section-1"]}
    result = {"sections": [{"section_id": "section-1", "role": "main_topic"}]}
    with model_provider.bind_profile(profile):
        monkeypatch.setattr(config, "OPENAI_MODEL", "gpt-5.6-luna")
        first_key = phase34._cache_key(**kwargs)
        phase34._write_cache_entry(first_key, result)
        monkeypatch.setattr(config, "OPENAI_MODEL", "gpt-5.4-mini")
        assert phase34._cache_key(**kwargs) == first_key
        assert phase34._cached_result(first_key) == result
    entry = phase34._read_cache()["entries"][first_key]
    assert entry["model"] == profile["routes"]["default"]["model"]
    assert entry[model_provider.PROFILE_KEY] == profile


def test_explicit_unprofiled_source_identity_keeps_configuration_path(monkeypatch):
    with model_provider.bind_profile(None):
        for configured_model in ("gpt-5.6-luna", "gpt-5.4-mini", "legacy-deployment-model"):
            monkeypatch.setattr(config, "OPENAI_MODEL", configured_model)
            assert model_provider.source_model_identity() == configured_model

"""Model routing joins paid source/Architect reuse; sealed legacy keys stay exact."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from app import config
from app.services import canonical_source_phase221_fallback as fallback
from app.services import instruction_architect as architect
from app.services import model_provider
from tests.test_chapter_outline import _page_acsd
from tests.test_chapter_outline_independent_review import _provider
from tests.test_instruction_architect import SAMPLE_METADATA, _authored_slots
from tests.test_pdf_acsd_lane_reuse import _fake_pages, _identified_verified_batch_result


@pytest.mark.parametrize("contract", [None, fallback.INGESTION_CONTRACT_VERSION])
def test_legacy_source_cache_keys_are_byte_exact_and_new_profile_separates(contract):
    version = "2.4.0" if contract is None else fallback.FALLBACK_VERSION
    sha = "pdf-sha"
    pages = _fake_pages(1, 2)
    prefix = [version, fallback.FALLBACK_COMPILER]
    if contract:
        prefix.append(contract)
    prefix += [config.OPENAI_MODEL, sha]
    outline = [version] + ([contract] if contract else []) + [
        fallback.OUTLINE_VERSION, fallback._outline_prompt_sha256(),
        config.OPENAI_MODEL, sha, "chapter-outline",
    ]
    expected = [
        hashlib.sha256("␟".join(parts).encode()).hexdigest()
        for parts in (
            prefix + ["PDF-PAGE-0001,PDF-PAGE-0002"],
            prefix + ["full-verified-bundle"], outline,
        )
    ]

    def keys():
        kwargs = {"fallback_version": version, "ingestion_contract": contract}
        return [
            fallback._batch_cache_key_for_contract(sha, pages, **kwargs),
            fallback._bundle_cache_key_for_contract(sha, **kwargs),
            fallback._outline_cache_key_for_contract(sha, **kwargs),
        ]

    with model_provider.bind_profile(None):
        assert keys() == expected
    with model_provider.bind_profile(model_provider.new_profile()):
        fresh = keys()
        assert all(new != old for new, old in zip(fresh, expected))
        assert keys() == fresh
    with model_provider.bind_profile(None):
        assert keys() == expected


def test_cache_envelope_rejects_profile_mismatch_even_at_expected_filename(tmp_path, monkeypatch):
    monkeypatch.setattr(fallback, "_CACHE_DIR", tmp_path)
    row = {"status": "verified", "result": {"pages": []}}
    with model_provider.bind_profile(None):
        fallback._write_verified_batch_cache("copied", row)
        assert json.loads(fallback._batch_cache_path("copied").read_text()) == row
        assert fallback._read_verified_batch_cache("copied") == row
    with model_provider.bind_profile(model_provider.new_profile()):
        assert fallback._read_verified_batch_cache("copied") is None
        fallback._write_verified_batch_cache("copied", row)
        current = fallback._read_verified_batch_cache("copied")
        assert current[model_provider.PROFILE_KEY] == model_provider.new_profile()
    with model_provider.bind_profile(None):
        assert fallback._read_verified_batch_cache("copied") is None
    assert model_provider.PROFILE_KEY not in row


def test_source_conversion_reuses_only_within_its_frozen_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(fallback, "_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(fallback, "_pdf_page_count", lambda path: 2)
    monkeypatch.setattr(fallback, "_pdf_sha256", lambda path: "same-source")
    monkeypatch.setattr(fallback, "_batch_size", lambda: 2)
    monkeypatch.setattr(fallback, "collect_pdf_pages", lambda *args, **kwargs: _fake_pages(1, 2))
    monkeypatch.setattr(fallback, "derive_chapter_outline", lambda pages: None)
    calls = []

    def provider(pages):
        calls.append(model_provider.bound_profile())
        return _identified_verified_batch_result()

    def run():
        return fallback.extract_pdf_to_page_acsd(Path("unused.pdf"), provider=provider)

    with model_provider.bind_profile(None):
        legacy = run()
        assert run() == legacy
    with model_provider.bind_profile(model_provider.new_profile()):
        current = run()
        assert run() == current
    with model_provider.bind_profile(None):
        assert run() == legacy
    assert calls == [None, model_provider.new_profile()]
    assert current["pages"] == legacy["pages"]


def test_outline_author_and_independent_review_replay_per_profile(tmp_path, monkeypatch):
    calls = _provider(monkeypatch, tmp_path)
    source = _page_acsd()
    with model_provider.bind_profile(None):
        legacy = fallback.derive_chapter_outline(source)
        assert model_provider.PROFILE_KEY not in legacy["review_provenance"]
        assert fallback.derive_chapter_outline(source) == legacy
    with model_provider.bind_profile(model_provider.new_profile()):
        assert not fallback._outline_review_is_current(source, legacy)
        current = fallback.derive_chapter_outline(source)
        assert current["review_provenance"][model_provider.PROFILE_KEY] == model_provider.new_profile()
        assert fallback.derive_chapter_outline(source) == current
    with model_provider.bind_profile(None):
        assert not fallback._outline_review_is_current(source, current)
        assert fallback.derive_chapter_outline(source) == legacy
    assert len(calls) == 4  # one author and one independent critic per profile


def test_architect_artifact_reuse_requires_same_profile_without_changing_slot_hash(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "use_live_generation", lambda: True)
    calls = []

    def author(*args, **kwargs):
        calls.append(model_provider.bound_profile())
        return _authored_slots()

    def ensure():
        return architect.ensure_instruction_set(
            metadata=SAMPLE_METADATA, source_text="source", artifact_dir=tmp_path,
            api_call=author, critic=lambda payload: {"verdict": "verified", "confidence": 1.0},
        )

    with model_provider.bind_profile(None):
        legacy = ensure()
        assert model_provider.PROFILE_KEY not in legacy
        assert ensure() == legacy
    with model_provider.bind_profile(model_provider.new_profile()):
        assert not architect._stored_set_reusable(legacy, architect._frozen_core_entries())
        current = ensure()
        assert ensure() == current
        assert current[model_provider.PROFILE_KEY] == model_provider.new_profile()
    with model_provider.bind_profile(None):
        assert not architect._stored_set_reusable(current, architect._frozen_core_entries())
        # Historical artifact remains independently replayable without spend.
        architect.write_instruction_set(copy.deepcopy(legacy), tmp_path)
        assert ensure() == legacy
    assert calls == [None, model_provider.new_profile()]
    assert legacy["instruction_set_sha256"] == current["instruction_set_sha256"]
    assert {k: v for k, v in current.items() if k != model_provider.PROFILE_KEY} == legacy

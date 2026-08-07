"""Pass 1 — Chapter Reading (docs/build-concepts-manual-process.md, Step 1).

The pass hands the raw MMD to the model before any compiler touches it, and
its contract is coverage-shaped: a chunk the model cannot read usably keeps
its original text and is flagged — the run continues. Only a definitive
quota stop propagates. The cached reading is what makes resumes coherent:
every resume of one run must see byte-identical normalized text.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import ModuleType

import pytest

from app import config
from app.services import canonical_source_phase22 as phase22
from app.services import chapter_reading, chapter_reading_contract, generation

PROCESS_DOC = (
    Path(__file__).resolve().parents[2] / "docs" /
    "build-concepts-manual-process.md"
)

RAW = (
    "# The Rise of Nationalism\n\n"
    "Napoleon introduced the Civil Code of 1804.\n\n"
    "![Fig. 1 Marianne](img/marianne.png)\n\n"
    "Reprint 2024-25\n"
)


@pytest.fixture(autouse=True)
def _isolated_reading_state(tmp_path, monkeypatch):
    """Fresh caches per test: reads must never leak across tests."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    with chapter_reading._memory_lock:
        chapter_reading._memory_cache.clear()
    yield
    with chapter_reading._memory_lock:
        chapter_reading._memory_cache.clear()


def _live(monkeypatch):
    monkeypatch.setattr(config, "use_live_generation", lambda: True)


def _api_ok(system, user, **kwargs):
    payload = json.loads(user)
    return {
        "normalized_mmd": payload["chunk_mmd"].replace("Reprint 2024-25\n", ""),
        "blocks": [
            {"kind": "heading", "label": "The Rise of Nationalism", "page": 1},
            {"kind": "prose", "label": "Civil Code"},
            {"kind": "figure", "label": "Fig. 1 Marianne", "page": 1},
            {"kind": "furniture", "label": "Reprint 2024-25"},
        ],
        "dropped_furniture": ["Reprint 2024-25"],
    }


# --------------------------------------------------------------------------- #
# The reading itself
# --------------------------------------------------------------------------- #

def test_dry_mode_passes_the_source_through_unchanged():
    reading = chapter_reading.read_chapter(RAW)

    assert reading["mode"] == "dry"
    assert reading["normalized_mmd"] == RAW
    assert reading["census"] == []


def test_chunking_is_lossless():
    """Fallback chunks are the original text, so reassembly must be exact."""
    text = "\n\n".join(f"Paragraph {i} " + "x" * 40 for i in range(30)) + "\n"

    chunks = chapter_reading._chunks(text, max_chars=200)

    assert len(chunks) > 1
    assert "".join(chunks) == text


def test_live_reading_normalizes_and_takes_census(monkeypatch):
    _live(monkeypatch)

    reading = chapter_reading.read_chapter(RAW, api_call=_api_ok)

    assert reading["mode"] == "live"
    assert "Reprint 2024-25" not in reading["normalized_mmd"]
    assert "![Fig. 1 Marianne](img/marianne.png)" in reading["normalized_mmd"]
    kinds = {row["kind"] for row in reading["census"]}
    assert {"heading", "prose", "figure", "furniture"} <= kinds
    assert reading["dropped_furniture"] == ["Reprint 2024-25"]
    assert reading["provenance"]["fallback_chunks"] == 0


def test_second_read_is_served_from_cache_and_never_rebilled(monkeypatch):
    _live(monkeypatch)
    calls = {"n": 0}

    def counting(system, user, **kwargs):
        calls["n"] += 1
        return _api_ok(system, user, **kwargs)

    first = chapter_reading.read_chapter(RAW, api_call=counting)
    made = calls["n"]

    def forbidden(system, user, **kwargs):
        raise AssertionError("cached reading must not call the model")

    second = chapter_reading.read_chapter(RAW, api_call=forbidden)

    assert made >= 1
    assert second["normalized_mmd"] == first["normalized_mmd"]


def test_cache_survives_a_process_restart(monkeypatch):
    """Disk, not memory, is what keeps a resumed run's source identical."""
    _live(monkeypatch)
    chapter_reading.read_chapter(RAW, api_call=_api_ok)
    with chapter_reading._memory_lock:
        chapter_reading._memory_cache.clear()

    def forbidden(system, user, **kwargs):
        raise AssertionError("disk-cached reading must not call the model")

    reading = chapter_reading.read_chapter(RAW, api_call=forbidden)

    assert reading["mode"] == "live"
    assert "Reprint 2024-25" not in reading["normalized_mmd"]


def test_a_dropped_image_keeps_the_original_chunk_and_flags_it(monkeypatch):
    """A lost image is lost coverage; verbatim fallback preserves it."""
    _live(monkeypatch)

    def drops_image(system, user, **kwargs):
        return {
            "normalized_mmd": "Napoleon introduced the Civil Code of 1804.\n",
            "blocks": [{"kind": "prose", "label": ""}],
            "dropped_furniture": [],
        }

    reading = chapter_reading.read_chapter(RAW, api_call=drops_image)

    assert reading["normalized_mmd"].strip("\n") == RAW.strip("\n")
    flags = [
        row for row in reading["census"]
        if row["kind"] == chapter_reading.UNREAD_KIND
    ]
    assert flags and "image tag dropped" in flags[0]["note"]
    assert reading["provenance"]["fallback_chunks"] == 1


def test_provider_failure_flags_the_chunk_and_continues(monkeypatch):
    _live(monkeypatch)

    def unavailable(system, user, **kwargs):
        raise RuntimeError("OpenAI unavailable after 12 transient retries")

    reading = chapter_reading.read_chapter(RAW, api_call=unavailable)

    assert reading["normalized_mmd"].strip("\n") == RAW.strip("\n")
    assert reading["provenance"]["fallback_chunks"] == 1


def test_quota_exhaustion_still_stops_the_run(monkeypatch):
    """The one legitimate stop: a definitive billing denial, not a flag."""
    _live(monkeypatch)

    def quota(system, user, **kwargs):
        raise RuntimeError("OpenAI quota exhausted (insufficient_quota)")

    with pytest.raises(RuntimeError, match="insufficient_quota"):
        chapter_reading.read_chapter(RAW, api_call=quota)


def test_normalized_for_resolves_only_a_cached_reading(monkeypatch):
    assert chapter_reading.normalized_for(RAW) == RAW

    _live(monkeypatch)
    reading = chapter_reading.read_chapter(RAW, api_call=_api_ok)

    assert chapter_reading.normalized_for(RAW) == reading["normalized_mmd"]


# --------------------------------------------------------------------------- #
# The contract wiring
# --------------------------------------------------------------------------- #

def _stub_generation(prior_checkpoint=None):
    stub = ModuleType("stub_generation")
    captured = {}

    def concepts_from_mmd(mmd_text, *args, **kwargs):
        captured["mmd_text"] = mmd_text
        return []

    stub.concepts_from_mmd = concepts_from_mmd
    stub._newest_compatible_concept_checkpoint = (
        lambda envelope, **kwargs: prior_checkpoint
    )
    return stub, captured


def test_contract_generates_from_the_normalized_source(monkeypatch):
    _live(monkeypatch)
    chapter_reading.read_chapter(RAW, api_call=_api_ok)
    stub, captured = _stub_generation()
    chapter_reading_contract.install(stub)

    artifacts: dict = {}
    stub.concepts_from_mmd(
        RAW, live=True, artifacts=artifacts, resume_checkpoint=None,
        chapter_title="Nationalism",
    )

    assert "Reprint 2024-25" not in captured["mmd_text"]
    assert "![Fig. 1 Marianne](img/marianne.png)" in captured["mmd_text"]
    assert artifacts["chapter_reading"]["provenance"]["version"] == (
        chapter_reading.READING_VERSION
    )
    assert artifacts["chapter_reading"]["census"]


def test_contract_keeps_raw_source_for_a_pre_reading_resume(monkeypatch):
    """Stages compiled from raw text must never see a switched source."""
    _live(monkeypatch)
    stub, captured = _stub_generation(
        prior_checkpoint={"stage": "pre_type_assignment"}
    )
    chapter_reading_contract.install(stub)

    def forbidden(*args, **kwargs):
        raise AssertionError("a legacy resume must not trigger a reading")

    monkeypatch.setattr(chapter_reading, "read_chapter", forbidden)
    stub.concepts_from_mmd(RAW, live=True, resume_checkpoint={"stages": []})

    assert captured["mmd_text"] == RAW


def test_contract_skips_reading_in_dry_runs(monkeypatch):
    stub, captured = _stub_generation()
    chapter_reading_contract.install(stub)

    def forbidden(*args, **kwargs):
        raise AssertionError("dry runs must not read")

    monkeypatch.setattr(chapter_reading, "read_chapter", forbidden)
    stub.concepts_from_mmd(RAW, live=False)

    assert captured["mmd_text"] == RAW


def test_install_is_idempotent():
    stub, _captured = _stub_generation()
    chapter_reading_contract.install(stub)
    wrapped_once = stub.concepts_from_mmd
    chapter_reading_contract.install(stub)

    assert stub.concepts_from_mmd is wrapped_once
    assert generation.concepts_from_mmd._chapter_reading_installed
    assert phase22.active_semantic_source._chapter_reading_installed


def test_deposit_and_recovery_see_the_same_derived_source(monkeypatch):
    """``active_semantic_source(raw audit copy)`` resolves to the reading."""
    _live(monkeypatch)
    reading = chapter_reading.read_chapter(RAW, api_call=_api_ok)

    assert phase22.active_semantic_source(RAW) == reading["normalized_mmd"]


# --------------------------------------------------------------------------- #
# The process document and the prompt
# --------------------------------------------------------------------------- #

def test_the_process_document_states_the_five_steps():
    assert PROCESS_DOC.is_file()
    text = PROCESS_DOC.read_text(encoding="utf-8")

    for phrase in (
        "Step 1", "Step 2", "Step 3", "Step 4", "Step 5",
        "QID009#a",
        "coverage, not certainty",
        "Nothing here is deterministic parsing",
    ):
        assert phrase in text, phrase


def test_the_reading_prompt_carries_the_hard_requirements():
    from app.services import prompts

    prompt = prompts.get_text("chapter_reading.normalize.system")

    for kind in chapter_reading.BLOCK_KINDS:
        assert kind in prompt, kind
    assert "NEVER paraphrase" in prompt
    assert "![...](...)" in prompt and "exactly as it" in prompt
    assert "Page is provenance only" in prompt
    assert "dropped_furniture" in prompt

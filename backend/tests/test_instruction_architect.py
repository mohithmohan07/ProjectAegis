"""The Architect (docs/aegis-restructure.md §8.1): instruction-set governance.

Pinned here:

* the offline path emits a deterministic, EMPTY-slotted set (no invented
  guidance) whose hash is stable across calls;
* the governance hash moves with any slot text and with any frozen-core
  prompt override;
* the critic is advisory — dissent lands in review_flags and never gates;
* the live path fails closed on an invalid slot response (pre-spend; slots
  are never invented locally);
* the hash joins every decision identity: envelope seal (and therefore the
  kernel decision keys), envelope reuse, checkpoint fingerprints and their
  bundle-validator mirror, semantic_context_hash, and both pre-spend
  human-gate identities.
"""
from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

from app import config
from app.services import build_concepts
from app.services import canonical_source_phase3 as phase3_core
from app.services import checkpoints
from app.services import concept_topology_contract as topology_contract
from app.services import generation
from app.services import instruction_architect as architect
from app.services import prompts
from app.services import source_topic_decision
from app.services import type_granularity_decision
from app.services.phase3 import envelope as p3_envelope
from app.services.phase3 import kernel as p3_kernel
from app.services.phase3 import prompts as p3_prompts
from app.services.phase3 import runner as p3_runner


SAMPLE_METADATA = {
    "board": "CBSE",
    "grade": "6",
    "subject": "Mathematics",
    "unit": "Numbers",
    "chapter_title": "Knowing Our Numbers",
    "chapter_id": 11,
    "chapter_code": "6M01",
    "learning_kind": "Post",
    "source_book": "NCERT Class 6 Mathematics",
}


def _authored_slots() -> dict:
    return {
        "subject_topology_guidance": "Method concepts anchor each topic.",
        "grade_band_vocabulary": "Short sentences; define every new term.",
        "language_mode": {
            "mode": "expository",
            "rationale": "Non-literature mathematics chapter.",
        },
        "board_publication_conventions": "NCERT exercise numbering.",
        "chapter_cautions": ["Large-number tables continue across pages."],
    }


def _live(monkeypatch):
    monkeypatch.setattr(config, "use_live_generation", lambda: True)


# ---------------------------------------------------------------------------
# assembly


def test_offline_defaults_are_deterministic_and_empty_slotted():
    first = architect.assemble_instruction_set(
        metadata=SAMPLE_METADATA, source_text="# Chapter\nBody.")
    second = architect.assemble_instruction_set(
        metadata=SAMPLE_METADATA, source_text="# Chapter\nBody.")
    assert first["slots_source"] == "offline-defaults"
    assert first["instruction_set_sha256"] == second["instruction_set_sha256"]
    assert first["slots"] == {
        "subject_topology_guidance": "",
        "grade_band_vocabulary": "",
        "language_mode": {"mode": "", "rationale": ""},
        "board_publication_conventions": "",
        "chapter_cautions": [],
    }
    assert first["review_flags"] == []
    # The null assembly is the same identity every non-assembling caller
    # (legacy checkpoints, standalone transformations) resolves to.
    assert first["instruction_set_sha256"] == architect.empty_set_sha256()
    # The frozen core is enumerated and hashed.
    assert "concepts.skeleton.system" in first["frozen_core_registry_keys"]
    assert "phase3:HOST_SYSTEM" in first["frozen_core_registry_keys"]


def test_hash_moves_with_any_slot_text():
    frozen = architect._frozen_core_entries()
    base = architect.instruction_set_sha256(_authored_slots(), frozen)
    changed = _authored_slots()
    changed["grade_band_vocabulary"] += " Prefer concrete examples."
    assert architect.instruction_set_sha256(changed, frozen) != base


def test_hash_moves_with_a_frozen_core_prompt_override():
    base = architect.empty_set_sha256()
    prompts.set_override(
        "concepts.skeleton.system",
        prompts.get_text("concepts.skeleton.system") + "\nOverride.",
    )
    try:
        assert architect.empty_set_sha256() != base
    finally:
        prompts.reset("concepts.skeleton.system")
    assert architect.empty_set_sha256() == base


def test_architect_prompt_is_registered_under_architect_category():
    spec = prompts.describe("architect.assemble.system")
    assert spec["category"] == "Architect"
    assert "Architect" in prompts.categories()


def test_live_assembly_fails_closed_on_invalid_slot_response(monkeypatch):
    _live(monkeypatch)

    def bad_api(system, user, **kwargs):
        return {"subject_topology_guidance": 7}

    with pytest.raises(architect.InstructionAssemblyError) as excinfo:
        architect.assemble_instruction_set(
            metadata=SAMPLE_METADATA,
            source_text="body",
            api_call=bad_api,
            critic=lambda payload: {"verdict": "verified", "confidence": 1.0},
        )
    assert "refusing to invent" in str(excinfo.value)


def test_critic_dissent_flags_never_gate(monkeypatch):
    _live(monkeypatch)
    calls = {"assemble": 0}

    def api(system, user, **kwargs):
        calls["assemble"] += 1
        return _authored_slots()

    def dissenting_critic(payload):
        assert payload["proposed_decision"]["language_mode"]["mode"] == (
            "expository"
        )
        return {
            "verdict": "rejected",
            "confidence": 0.42,
            "issues": ["vocabulary guidance is too generic"],
        }

    result = architect.assemble_instruction_set(
        metadata=SAMPLE_METADATA,
        source_text="body",
        api_call=api,
        critic=dissenting_critic,
    )
    assert calls["assemble"] == 1  # dissent triggers no retry and no gate
    assert result["slots_source"] == "model"
    assert result["slots"]["grade_band_vocabulary"] == (
        "Short sentences; define every new term."
    )
    assert any("rejected" in flag for flag in result["review_flags"])
    assert any(
        "vocabulary guidance is too generic" in flag
        for flag in result["review_flags"]
    )


def test_critic_failure_is_flagged_not_fatal(monkeypatch):
    _live(monkeypatch)

    def api(system, user, **kwargs):
        return _authored_slots()

    def broken_critic(payload):
        raise RuntimeError("critic transport down")

    result = architect.assemble_instruction_set(
        metadata=SAMPLE_METADATA,
        source_text="body",
        api_call=api,
        critic=broken_critic,
    )
    assert any("unaudited" in flag for flag in result["review_flags"])


def test_ensure_replays_persisted_assembly_while_frozen_core_matches(
    tmp_path, monkeypatch
):
    _live(monkeypatch)
    stored = architect._finalize(
        architect._identity_metadata(SAMPLE_METADATA),
        architect._normalized_slots(_authored_slots()),
        architect._frozen_core_entries(),
        slots_source="model",
        review_flags=[],
    )
    architect.write_instruction_set(stored, tmp_path)

    def exploding_api(system, user, **kwargs):  # pragma: no cover - guard
        raise AssertionError("decide-once: a stored set must not re-assemble")

    replayed = architect.ensure_instruction_set(
        metadata=SAMPLE_METADATA,
        source_text="body",
        artifact_dir=tmp_path,
        api_call=exploding_api,
    )
    assert replayed["instruction_set_sha256"] == (
        stored["instruction_set_sha256"]
    )


def test_ensure_reassembles_when_frozen_core_changed(tmp_path, monkeypatch):
    _live(monkeypatch)
    stored = architect._finalize(
        architect._identity_metadata(SAMPLE_METADATA),
        architect._normalized_slots(_authored_slots()),
        architect._frozen_core_entries(),
        slots_source="model",
        review_flags=[],
    )
    architect.write_instruction_set(stored, tmp_path)
    prompts.set_override(
        "concepts.skeleton.system",
        prompts.get_text("concepts.skeleton.system") + "\nOverride.",
    )
    try:
        rebuilt = architect.ensure_instruction_set(
            metadata=SAMPLE_METADATA,
            source_text="body",
            artifact_dir=tmp_path,
            api_call=lambda system, user, **kwargs: _authored_slots(),
            critic=lambda payload: {"verdict": "verified", "confidence": 1.0},
        )
    finally:
        prompts.reset("concepts.skeleton.system")
    assert rebuilt["instruction_set_sha256"] != (
        stored["instruction_set_sha256"]
    )
    on_disk = architect.load_instruction_set(tmp_path)
    assert on_disk["instruction_set_sha256"] == (
        rebuilt["instruction_set_sha256"]
    )


# ---------------------------------------------------------------------------
# the hash joins every decision identity


def _sample_envelope(instruction_hash: str) -> dict:
    graph = {
        "source_contract_hash": "c" * 64,
        "topics": [{"topic_id": "TOPIC-0001", "title": "Numbers"}],
        "subtopics": [],
        "blocks": [
            {"block_id": "BLK-1", "topic_id": "TOPIC-0001", "kind": "para"},
        ],
    }
    return p3_envelope.build(
        graph=graph,
        canonical={"blocks": [{"block_id": "BLK-1", "display_text": "t"}]},
        skeleton_rows=[{"concept_title": "Place Value", "topic": "Numbers"}],
        inventory={},
        mined_types={},
        metadata={
            "board": "CBSE",
            "instruction_set_sha256": instruction_hash,
        },
    )


def test_envelope_seal_covers_the_instruction_hash():
    env_a = _sample_envelope("a" * 64)
    env_b = _sample_envelope("b" * 64)
    assert env_a["envelope_sha256"] != env_b["envelope_sha256"]
    # The kernel joins the instruction identity through the sealed envelope:
    # same kind/unit/payload, different instruction set, different key. No
    # separate identity field is added (no double-join).
    key_a = p3_kernel.decision_key(
        kind="settle.topology", unit_id="TOPIC-0001#batch1",
        envelope_sha256=env_a["envelope_sha256"], payload={"stage": "x"},
        policy_version="p",
    )
    key_b = p3_kernel.decision_key(
        kind="settle.topology", unit_id="TOPIC-0001#batch1",
        envelope_sha256=env_b["envelope_sha256"], payload={"stage": "x"},
        policy_version="p",
    )
    assert key_a != key_b


def test_envelope_version_1_no_longer_validates():
    env = _sample_envelope("a" * 64)
    stale = copy.deepcopy(env)
    stale["envelope_version"] = 1
    with pytest.raises(p3_envelope.EnvelopeError):
        p3_envelope.validate(stale)


def _run_seam_with(monkeypatch, tmp_path, *, stored_env, meta):
    """Drive _run_rewritten_phase3 against a persisted envelope wrapper."""
    session = {
        "artifact_dir": str(tmp_path),
        "canonical": {"blocks": [{"block_id": "BLK-1", "display_text": "t"}]},
    }
    graph = copy.deepcopy(stored_env["graph"])
    monkeypatch.setattr(phase3_core, "active_session", lambda: session)
    monkeypatch.setattr(phase3_core, "active_graph", lambda: graph)
    out = [{"concept_title": "Place Value", "topic": "Numbers"}]
    wrapper = {
        "boundary_skeleton_sha256": phase3_core._sha256_json(list(out)),
        "envelope": stored_env,
    }
    (tmp_path / "source.phase3-envelope.json").write_text(
        json.dumps(wrapper, ensure_ascii=False), encoding="utf-8")
    captured = {}

    def fake_run(env, store_dir=None):
        captured["envelope_sha256"] = env["envelope_sha256"]
        return {
            "summary": {
                "row_count": 0, "routed_qids": 0,
                "unrouted_items": 0, "flagged_row_count": 0,
            },
            "records": [],
        }

    monkeypatch.setattr(p3_runner, "run", fake_run)
    kwargs = {
        "meta": meta,
        "question_task_inventory": {},
        "mined_types": {},
    }
    topology_contract._run_rewritten_phase3(generation, out, kwargs)
    return captured["envelope_sha256"]


def test_envelope_reuse_requires_matching_instruction_hash(
    tmp_path, monkeypatch
):
    stored = _sample_envelope("a" * 64)
    # Same instruction set: the sealed envelope replays.
    reused = _run_seam_with(
        monkeypatch, tmp_path,
        stored_env=stored,
        meta={"board": "CBSE", "instruction_set_sha256": "a" * 64},
    )
    assert reused == stored["envelope_sha256"]


def test_envelope_reuse_refuses_mismatched_instruction_hash(
    tmp_path, monkeypatch
):
    stored = _sample_envelope("a" * 64)
    rebuilt = _run_seam_with(
        monkeypatch, tmp_path,
        stored_env=stored,
        meta={"board": "CBSE", "instruction_set_sha256": "b" * 64},
    )
    # Rebuilt, not crashed: a fresh envelope sealed under the new
    # instruction identity, so every decision key changes.
    assert rebuilt != stored["envelope_sha256"]


def _job_and_chapter():
    job = SimpleNamespace(learning_kind="post", mmd_text="# Chapter body")
    chapter = SimpleNamespace(
        board="CBSE", grade="6", subject="Mathematics", unit="Numbers",
        chapter_title="Knowing Our Numbers", chapter_code="6M01", id=11,
    )
    return job, chapter


def test_checkpoint_fingerprint_binds_the_instruction_hash():
    job, chapter = _job_and_chapter()
    default = build_concepts._generation_checkpoint_fingerprint(job, chapter)
    assembled = build_concepts._generation_checkpoint_fingerprint(
        job, chapter, instruction_set_sha256="a" * 64)
    assert default != assembled
    # None resolves to the empty-set identity over the current frozen core.
    assert default == build_concepts._generation_checkpoint_fingerprint(
        job, chapter,
        instruction_set_sha256=architect.empty_set_sha256(),
    )


def test_checkpoint_restore_refuses_a_mismatched_instruction_hash():
    job, chapter = _job_and_chapter()
    checkpoint = {
        "checkpoint_format": generation._CONCEPT_CHECKPOINT_FORMAT,
        "schema_version": generation._CONCEPT_CHECKPOINT_SCHEMA,
        "fingerprint": build_concepts._generation_checkpoint_fingerprint(
            job, chapter, instruction_set_sha256="a" * 64),
        "target_identity": build_concepts._generation_target_identity(
            chapter),
    }
    assert build_concepts._checkpoint_identity_matches_generation(
        checkpoint, job=job, chapter=chapter,
        instruction_set_sha256="a" * 64,
    )
    # A changed instruction set rewinds (no resume), it does not crash.
    assert not build_concepts._checkpoint_identity_matches_generation(
        checkpoint, job=job, chapter=chapter,
        instruction_set_sha256="b" * 64,
    )


def test_bundle_validator_mirrors_the_v3_fingerprint():
    job, chapter = _job_and_chapter()
    target = build_concepts._generation_target_identity(chapter)
    stamped = "a" * 64
    assert checkpoints._expected_fingerprint(
        "post", target, job.mmd_text, stamped,
    ) == build_concepts._generation_checkpoint_fingerprint(
        job, chapter, instruction_set_sha256=stamped)
    # An envelope without the stored field resolves to the empty-set
    # identity — exactly what the writer-side default produces.
    assert checkpoints._checkpoint_instruction_set_sha256({}, "$") == (
        architect.empty_set_sha256()
    )
    with pytest.raises(ValueError):
        checkpoints._checkpoint_instruction_set_sha256(
            {"instruction_set_sha256": "not-a-hash"}, "$")


def test_merged_checkpoint_envelope_stores_the_instruction_hash():
    job, chapter = _job_and_chapter()
    fingerprint = build_concepts._generation_checkpoint_fingerprint(
        job, chapter, instruction_set_sha256="a" * 64)
    merged = build_concepts._merge_generation_checkpoint_history(
        None,
        {"stage": "canonical_skeleton", "skeleton_method_row_snapshot": []},
        fingerprint=fingerprint,
        target_identity=build_concepts._generation_target_identity(chapter),
        target_chapter_id=11,
        instruction_set_sha256="a" * 64,
    )
    assert merged["instruction_set_sha256"] == "a" * 64
    # Default resolves to the empty-set identity, matching the fingerprint
    # default, so bundles stay self-consistent.
    defaulted = build_concepts._merge_generation_checkpoint_history(
        None,
        {"stage": "canonical_skeleton", "skeleton_method_row_snapshot": []},
        fingerprint=build_concepts._generation_checkpoint_fingerprint(
            job, chapter),
        target_identity=build_concepts._generation_target_identity(chapter),
        target_chapter_id=11,
    )
    assert defaulted["instruction_set_sha256"] == (
        architect.empty_set_sha256()
    )


def test_semantic_context_hash_joins_the_instruction_hash():
    base = {"board": "CBSE", "grade": "6", "subject": "Mathematics"}
    without = phase3_core.semantic_context_hash(base)
    with_hash = phase3_core.semantic_context_hash(
        {**base, "instruction_set_sha256": "a" * 64})
    other_hash = phase3_core.semantic_context_hash(
        {**base, "instruction_set_sha256": "b" * 64})
    assert without != with_hash
    assert with_hash != other_hash
    # Absent and empty are the same identity (legacy graphs stay valid).
    assert without == phase3_core.semantic_context_hash(
        {**base, "instruction_set_sha256": ""})


def test_pre_spend_gate_identities_join_the_instruction_hash():
    meta = {
        "board": "CBSE", "grade": "6", "subject": "Mathematics",
        "unit": "Numbers", "chapter_title": "Knowing Our Numbers",
        "chapter_code": "6M01", "learning_kind": "Post",
    }
    review = {"topic_count": 2}
    without = type_granularity_decision._identity(
        review=review, inventory={}, mined_types={}, meta=meta)
    with_hash = type_granularity_decision._identity(
        review=review, inventory={}, mined_types={},
        meta={**meta, "instruction_set_sha256": "a" * 64})
    assert without["context_hash"] != with_hash["context_hash"]
    assert type_granularity_decision._GATE_VERSION == (
        "type-granularity-human-gate-6"
    )

    st_without = source_topic_decision._identity(
        records=[], source_topics=[], missing_topics=[], meta=meta)
    st_with = source_topic_decision._identity(
        records=[], source_topics=[], missing_topics=[],
        meta={**meta, "instruction_set_sha256": "a" * 64})
    assert st_without["context_hash"] != st_with["context_hash"]
    assert source_topic_decision._GATE_VERSION == (
        "source-topic-coverage-human-gate-2"
    )


# ---------------------------------------------------------------------------
# consumption: prompts and payload rules


def test_metadata_block_renders_authored_slots_and_empty_slots_nothing():
    meta = generation._metadata(
        subject="Mathematics", board="CBSE", grade="6",
        instruction_set_sha256="a" * 64,
        instruction_slots=_authored_slots(),
    )
    block = generation._metadata_block(meta)
    assert "RUN INSTRUCTIONS (Architect):" in block
    assert "Method concepts anchor each topic." in block
    assert "Language mode: expository" in block
    assert "Large-number tables continue across pages." in block

    empty_meta = generation._metadata(
        subject="Mathematics", board="CBSE", grade="6",
        instruction_set_sha256=architect.empty_set_sha256(),
        instruction_slots={
            "subject_topology_guidance": "",
            "grade_band_vocabulary": "",
            "language_mode": {"mode": "", "rationale": ""},
            "board_publication_conventions": "",
            "chapter_cautions": [],
        },
    )
    legacy_meta = generation._metadata(
        subject="Mathematics", board="CBSE", grade="6")
    assert generation._metadata_block(empty_meta) == (
        generation._metadata_block(legacy_meta)
    )
    assert "RUN INSTRUCTIONS" not in generation._metadata_block(empty_meta)


def test_settle_and_host_rules_suffix_comes_from_envelope_metadata():
    env_with = {"metadata": {"instruction_slots": _authored_slots()}}
    suffix = p3_prompts.instruction_rules_suffix(env_with)
    assert "Subject topology guidance: Method concepts anchor each topic." in (
        suffix
    )
    assert "Grade-band vocabulary: Short sentences; define every new term." in (
        suffix
    )
    # Only the two teaching-guidance slots ride the Phase 3 rules.
    assert "NCERT" not in suffix
    # Empty slots append nothing, keeping payloads (and decision keys)
    # byte-identical.
    assert p3_prompts.instruction_rules_suffix({"metadata": {}}) == ""
    assert p3_prompts.instruction_rules_suffix(
        {"metadata": {"instruction_slots": {
            "subject_topology_guidance": "",
            "grade_band_vocabulary": "",
        }}}
    ) == ""

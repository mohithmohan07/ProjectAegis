"""Phase 2.1.2 contract — compile-seam cutover regressions (lean build mode)."""
from __future__ import annotations

import json

import pytest

from app.services import canonical_source_phase2 as phase2
from app.services import canonical_source_phase212 as qx
from app.services import canonical_source_phase212_contract as qx_contract


SOURCE = """# Chapter 2 Sound

Sound travels through air.

Discuss
Why do we hear echoes?
"""


def test_active_compile_calls_the_author_and_marks_authority(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(qx, "_CACHE_DIR", tmp_path / "cache")
    calls = {"author": 0}
    real_call = qx._call_provider  # the suite echo author from conftest

    def counting(**kwargs):
        if kwargs.get("system") == qx._AUTHOR_SYSTEM:
            calls["author"] += 1
        return real_call(**kwargs)

    monkeypatch.setattr(qx, "_call_provider", counting)
    compiled = phase2.compile_phase2_source(
        SOURCE, source_filename="sound.mmd", consumer_module="build_concepts"
    )
    assert calls["author"] >= 1
    canonical = compiled.canonical
    assert canonical["task_membership"]["status"] == "adjudicated"
    assert canonical["task_membership"]["authority"] == "model_verdict"
    assert canonical[qx.LEDGER_KEY]["membership_authority"] == "model_verdict"
    assert canonical["phase2_inventory_ready"] is True
    assert all(
        t.get("membership_authority") == "model_verdict"
        for t in canonical["tasks"]
    )


def test_second_compile_replays_the_sealed_ledger(monkeypatch, tmp_path):
    monkeypatch.setattr(qx, "_CACHE_DIR", tmp_path / "cache")
    first = phase2.compile_phase2_source(
        SOURCE, source_filename="sound.mmd", consumer_module="build_concepts"
    ).canonical

    def explode(**kwargs):  # pragma: no cover
        raise AssertionError("replay must not call the provider")

    monkeypatch.setattr(qx, "_call_provider", explode)
    second = phase2.compile_phase2_source(
        SOURCE, source_filename="sound.mmd", consumer_module="build_concepts"
    ).canonical
    assert second["task_membership"] == {
        "status": "adjudicated",
        "authority": "model_verdict",
    }
    assert json.dumps(second["tasks"], sort_keys=True) == json.dumps(
        first["tasks"], sort_keys=True
    )


def test_provider_down_blocks_pre_spend_never_falls_back(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(qx, "_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(qx, "_provider_ready", lambda: False)
    compiled = phase2.compile_phase2_source(
        SOURCE, source_filename="sound.mmd", consumer_module="build_concepts"
    )
    canonical = compiled.canonical
    assert canonical["task_membership"]["status"] == "unadjudicated"
    assert canonical["task_membership"]["reason_code"] == (
        "provider_unavailable"
    )
    # The parser candidates stay VISIBLE as candidates …
    assert canonical["tasks"], "candidates must not be silently dropped"
    # … but the bundle is not generation-ready and the issue is named.
    assert canonical["phase2_inventory_ready"] is False
    codes = {
        issue["code"] for issue in phase2.phase2_inventory_issues(canonical)
    }
    assert "task_membership_unadjudicated" in codes


def test_shadow_compile_never_adjudicates(monkeypatch, tmp_path):
    monkeypatch.setattr(qx, "_CACHE_DIR", tmp_path / "cache")

    def explode(**kwargs):  # pragma: no cover
        raise AssertionError("shadow compiles must not spend")

    monkeypatch.setattr(qx, "_call_provider", explode)
    compiled = phase2.compile_phase2_source(
        SOURCE, source_filename="sound.mmd", consumer_module="revision"
    )
    assert "task_membership" not in compiled.canonical
    assert qx.LEDGER_KEY not in compiled.canonical


def test_outline_lane_is_exempt():
    """The PDF page-ledger/outline authority is never re-judged by QX."""
    bundle = {
        "chapter_outline": {"version": "test-1"},
        "used_for_generation": True,
    }
    assert qx_contract.adjudication_exempt(bundle)
    assert qx_contract.qx_artifact_valid(bundle, {})


def test_parser_era_artifact_is_stale_for_generation():
    """The checkpoint re-key: no ledger + active text lane = recompile."""
    parser_era = {"used_for_generation": True, "tasks": [{"qid": "QINV-0001"}]}
    assert not qx_contract.qx_artifact_valid(parser_era, {})
    shadow = {"used_for_generation": False}
    assert qx_contract.qx_artifact_valid(shadow, {})


def test_count_changing_adjudication_keeps_the_phase21_seals_valid(
    monkeypatch, tmp_path
):
    """Audit F1: a created (and a rejected) membership verdict must leave
    canonical AND report Phase-2.1 seals consistent, or the persisted
    artifact deterministically fails its next load."""
    from app.services import canonical_source_phase21 as phase21

    monkeypatch.setattr(qx, "_CACHE_DIR", tmp_path / "cache")

    def creating(**kwargs):
        real = qx._call_provider  # conftest echo (rebound below per call)
        raise AssertionError("replaced below")

    def make_author(mode):
        def author(*, system, prompt, schema, purpose="source_extraction",
                   max_tokens=None):
            payload = json.loads(prompt)
            if system == qx._CRITIC_SYSTEM:
                return {"verdict": "concur", "dissents": []}
            if system == qx._FIXER_SYSTEM:
                block = payload["block"]
                return {
                    "block_id": block["block_id"], "verdict": "not_task",
                    "confirmed_candidate_ids": [],
                    "rejected_candidate_ids": [
                        c["candidate_id"] for c in
                        payload["candidates_overlapping_this_block"]
                    ],
                    "missed_asks": [], "continues_task_ref": "",
                    "context_for_task_refs": [], "rationale": "test",
                }
            out = []
            for blk in payload.get("blocks") or []:
                entry = {
                    "block_id": blk["block_id"], "verdict": "not_task",
                    "confirmed_candidate_ids": [],
                    "rejected_candidate_ids": [],
                    "missed_asks": [], "continues_task_ref": "",
                    "context_for_task_refs": [],
                }
                if mode == "create" and "Sound travels" in blk["text"]:
                    entry["verdict"] = "contains_tasks"
                    entry["missed_asks"] = [{
                        "evidence_text": "Sound travels through air.",
                        "task_ref": "NEW-1",
                    }]
                out.append(entry)
            # every candidate must still be ruled: reject them all
            candidates = payload.get("candidates") or []
            if out and candidates:
                out[0]["rejected_candidate_ids"] = [
                    c["candidate_id"] for c in candidates
                ]
            return {"blocks": out}
        return author

    for mode in ("create", "reject"):
        monkeypatch.setattr(qx, "_call_provider", make_author(mode))
        monkeypatch.setattr(qx, "_provider_ready", lambda: True)
        compiled = phase2.compile_phase2_source(
            SOURCE, source_filename=f"seal-{mode}.mmd",
            consumer_module="build_concepts",
        )
        assert phase21.hardening_artifact_valid(
            compiled.canonical, compiled.report
        ), f"{mode}: the Phase-2.1 seals must survive a count change"

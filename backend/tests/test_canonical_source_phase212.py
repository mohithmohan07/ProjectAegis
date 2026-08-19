"""QX (Phase 2.1.2) — semantic task-membership adjudication regressions.

Lean build-mode coverage: the core contract paths with scripted providers.
Live semantic quality is QX4 (owner-gated, needs a real provider).
"""
from __future__ import annotations

import json

import pytest

from app.services import canonical_source_phase2 as phase2
from app.services import canonical_source_phase212 as qx


SOURCE = """# Chapter 1 Water

Water is essential for life. Plants absorb it through roots.

# विचार करा

पाण्याचे महत्त्व स्पष्ट करा.

Rivers carry water to the sea. Compare the two accounts.

Discuss
How does rain form?
"""


def _compiled():
    # Shadow-mode compile: the Phase 2.1.2 contract only adjudicates the
    # active lane, so these tests drive adjudication themselves with
    # scripted providers instead of racing the suite-wide echo author.
    return phase2.compile_phase2_source(
        SOURCE, source_filename="water.mmd", consumer_module="qx_tests"
    ).canonical


def _entry(block_id, **over):
    entry = {
        "block_id": block_id,
        "verdict": "not_task",
        "confirmed_candidate_ids": [],
        "rejected_candidate_ids": [],
        "missed_asks": [],
        "continues_task_ref": "",
        "context_for_task_refs": [],
    }
    entry.update(over)
    return entry


def _script_author(rules):
    """A scripted author: rules maps a text marker -> entry overrides."""
    calls = {"author": 0, "critic": 0, "fixer": 0, "correction": 0}

    def call(*, system, prompt, schema, purpose="source_extraction",
             max_tokens=None):
        payload = json.loads(prompt)
        if system == qx._CRITIC_SYSTEM:
            calls["critic"] += 1
            return {"verdict": "concur", "dissents": []}
        if system == qx._FIXER_SYSTEM:
            calls["fixer"] += 1
            block = payload["block"]
            entry = _entry(block["block_id"])
            entry["rejected_candidate_ids"] = [
                c["candidate_id"]
                for c in payload["candidates_overlapping_this_block"]
            ]
            return entry | {"rationale": "fixer ruled"}

        def entries(blocks, candidates):
            out = []
            ruled = set()
            for over in rules.values():
                ruled.update(over.get("confirmed_candidate_ids") or [])
                ruled.update(over.get("rejected_candidate_ids") or [])
            owner = {}
            for c in candidates:
                anchor = (c.get("block_ids") or [None])[0] or c.get(
                    "nearest_block_id"
                )
                owner.setdefault(anchor, []).append(c["candidate_id"])
            for blk in blocks:
                entry = _entry(blk["block_id"])
                for marker, over in rules.items():
                    if marker in blk["text"]:
                        entry.update(over)
                # Unruled candidates anchored here default to rejected so
                # the scripted closed world stays complete.
                extra = [
                    cid for cid in owner.get(blk["block_id"], [])
                    if cid not in ruled
                    and cid not in entry["confirmed_candidate_ids"]
                    and cid not in entry["rejected_candidate_ids"]
                ]
                entry["rejected_candidate_ids"] = (
                    list(entry["rejected_candidate_ids"]) + extra
                )
                out.append(entry)
            return out

        if system == qx._CORRECTION_SYSTEM:
            calls["correction"] += 1
            return {"blocks": entries(
                payload["blocks"], payload.get("candidates") or []
            )}
        calls["author"] += 1
        return {"blocks": entries(
            payload["blocks"], payload.get("candidates") or []
        )}

    return call, calls


MARATHI_RULES = {
    "स्पष्ट करा": {
        "verdict": "contains_tasks",
        "missed_asks": [{
            "evidence_text": "पाण्याचे महत्त्व स्पष्ट करा.",
            "task_ref": "NEW-1",
        }],
    },
    "Compare the two accounts.": {
        "verdict": "contains_tasks",
        "missed_asks": [{
            "evidence_text": "Compare the two accounts.",
            "task_ref": "NEW-2",
        }],
    },
}


def _adjudicate(monkeypatch, canonical, rules):
    call, calls = _script_author(rules)
    monkeypatch.setattr(qx, "_call_provider", call)
    monkeypatch.setattr(qx, "_provider_ready", lambda: True)
    result = qx.adjudicate_task_membership(canonical)
    return result, calls


def test_missed_asks_enter_the_inventory(monkeypatch, tmp_path):
    """The 24→0 class is dead: asks outside every cue vocabulary ship."""
    monkeypatch.setattr(qx, "_CACHE_DIR", tmp_path / "cache")
    canonical = _compiled()
    result, calls = _adjudicate(monkeypatch, canonical, MARATHI_RULES)
    assert result["status"] == "adjudicated"
    prompts = [t["raw_prompt"] for t in canonical["tasks"]]
    assert "पाण्याचे महत्त्व स्पष्ट करा." in prompts
    assert "Compare the two accounts." in prompts
    assert all(
        t["membership_authority"] == "model_verdict"
        for t in canonical["tasks"]
    )
    assert canonical["task_verdict_ledger"]["accounting"][
        "unaccounted_block_ids"] == []
    assert calls["author"] >= 1


def test_rejected_candidate_is_recorded_not_silent(monkeypatch, tmp_path):
    monkeypatch.setattr(qx, "_CACHE_DIR", tmp_path / "cache")
    canonical = _compiled()
    # The Discuss cue gives phase21 one recovered candidate.
    candidates = qx._candidate_payloads(
        canonical, qx._content_blocks(canonical)[0]
    )
    assert candidates, "fixture must produce at least one parser candidate"
    cid = candidates[0]["candidate_id"]
    rules = dict(MARATHI_RULES)
    rules["How does rain form?"] = {
        "verdict": "not_task",
        "rejected_candidate_ids": [cid],
    }
    result, _ = _adjudicate(monkeypatch, canonical, rules)
    assert result["status"] == "adjudicated"
    accounting = result["ledger"]["accounting"]
    assert accounting["candidates_rejected"] == 1
    rec = accounting["rejected_candidates"][0]
    assert rec["candidate_id"] == cid
    assert rec["ruling"] == "candidate_ruled_not_task"
    prompts = [t["raw_prompt"] for t in canonical["tasks"]]
    assert "How does rain form?" not in prompts


def test_confirmed_candidate_keeps_granularity(monkeypatch, tmp_path):
    monkeypatch.setattr(qx, "_CACHE_DIR", tmp_path / "cache")
    canonical = _compiled()
    candidates = qx._candidate_payloads(
        canonical, qx._content_blocks(canonical)[0]
    )
    cid = candidates[0]["candidate_id"]
    rules = dict(MARATHI_RULES)
    rules["How does rain form?"] = {
        "verdict": "contains_tasks",
        "confirmed_candidate_ids": [cid],
    }
    result, _ = _adjudicate(monkeypatch, canonical, rules)
    assert result["status"] == "adjudicated"
    confirmed = [
        t for t in canonical["tasks"]
        if (t.get("qx_ruling") or {}).get("candidate_id") == cid
    ]
    assert len(confirmed) == 1
    assert confirmed[0]["membership_authority"] == "model_verdict"


def test_uncertain_routes_to_fixer_and_completes(monkeypatch, tmp_path):
    monkeypatch.setattr(qx, "_CACHE_DIR", tmp_path / "cache")
    canonical = _compiled()
    candidates = qx._candidate_payloads(
        canonical, qx._content_blocks(canonical)[0]
    )
    cid = candidates[0]["candidate_id"]
    rules = dict(MARATHI_RULES)
    rules["How does rain form?"] = {
        "verdict": "uncertain",
        "rejected_candidate_ids": [cid],
    }
    result, calls = _adjudicate(monkeypatch, canonical, rules)
    assert result["status"] == "adjudicated"
    assert calls["fixer"] == 1
    assert result["ledger"]["fixer_decisions"]
    decision = result["ledger"]["fixer_decisions"][0]
    assert decision["decision_sha256"]


def test_provider_down_is_named_never_parser_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(qx, "_CACHE_DIR", tmp_path / "cache")
    canonical = _compiled()
    before = [t["raw_prompt"] for t in canonical.get("tasks") or []]
    monkeypatch.setattr(qx, "_provider_ready", lambda: False)
    result = qx.adjudicate_task_membership(canonical)
    assert result["status"] == "unadjudicated"
    assert result["reason_code"] == "provider_unavailable"
    assert [t["raw_prompt"] for t in canonical.get("tasks") or []] == before
    assert "task_verdict_ledger" not in canonical


def test_replay_uses_sealed_ledger_without_a_call(monkeypatch, tmp_path):
    monkeypatch.setattr(qx, "_CACHE_DIR", tmp_path / "cache")
    canonical = _compiled()
    result, calls = _adjudicate(monkeypatch, canonical, MARATHI_RULES)
    assert result["status"] == "adjudicated" and not result["replayed"]
    first_tasks = json.dumps(canonical["tasks"], sort_keys=True)

    fresh = _compiled()

    def explode(**kwargs):  # pragma: no cover - proves no call happens
        raise AssertionError("replay must not call the provider")

    monkeypatch.setattr(qx, "_call_provider", explode)
    monkeypatch.setattr(qx, "_provider_ready", lambda: True)
    replayed = qx.adjudicate_task_membership(fresh)
    assert replayed["status"] == "adjudicated" and replayed["replayed"]
    assert json.dumps(fresh["tasks"], sort_keys=True) == first_tasks


def test_critic_dissent_is_one_flag_never_a_gate(monkeypatch, tmp_path):
    monkeypatch.setattr(qx, "_CACHE_DIR", tmp_path / "cache")
    canonical = _compiled()
    call, calls = _script_author(MARATHI_RULES)

    def with_dissent(*, system, prompt, schema, purpose="source_extraction",
                     max_tokens=None):
        if system == qx._CRITIC_SYSTEM:
            return {
                "verdict": "dissent",
                "dissents": [{"target": "NEW-1", "reason": "check wording"}],
            }
        return call(system=system, prompt=prompt, schema=schema,
                    purpose=purpose, max_tokens=max_tokens)

    monkeypatch.setattr(qx, "_call_provider", with_dissent)
    monkeypatch.setattr(qx, "_provider_ready", lambda: True)
    result = qx.adjudicate_task_membership(canonical)
    assert result["status"] == "adjudicated"
    flagged = [
        t for t in canonical["tasks"]
        if any("critic dissent" in f for f in t.get("review_flags") or [])
    ]
    assert len(flagged) == 1
    assert flagged[0]["raw_prompt"] == "पाण्याचे महत्त्व स्पष्ट करा."


def test_correction_round_repairs_missing_coverage(monkeypatch, tmp_path):
    monkeypatch.setattr(qx, "_CACHE_DIR", tmp_path / "cache")
    canonical = _compiled()
    call, calls = _script_author(MARATHI_RULES)
    state = {"first": True}

    def drop_one_then_correct(*, system, prompt,
                              schema, purpose="source_extraction",
                              max_tokens=None):
        response = call(system=system, prompt=prompt, schema=schema,
                        purpose=purpose, max_tokens=max_tokens)
        if system == qx._AUTHOR_SYSTEM and state["first"]:
            state["first"] = False
            response["blocks"] = response["blocks"][1:]
        return response

    monkeypatch.setattr(qx, "_call_provider", drop_one_then_correct)
    monkeypatch.setattr(qx, "_provider_ready", lambda: True)
    result = qx.adjudicate_task_membership(canonical)
    assert result["status"] == "adjudicated"
    assert calls["correction"] == 1
    assert result["ledger"]["correction_history"]
    assert result["ledger"]["accounting"]["unaccounted_block_ids"] == []


def test_empty_source_adjudicates_trivially(monkeypatch, tmp_path):
    monkeypatch.setattr(qx, "_CACHE_DIR", tmp_path / "cache")
    canonical = phase2.compile_phase2_source(
        "", source_filename="empty.mmd", consumer_module="qx_tests"
    ).canonical

    def explode(**kwargs):  # pragma: no cover
        raise AssertionError("empty source must not call the provider")

    monkeypatch.setattr(qx, "_call_provider", explode)
    result = qx.adjudicate_task_membership(canonical)
    assert result["status"] == "adjudicated"
    assert canonical["tasks"] == []
    assert result["ledger"]["empty_source"] is True

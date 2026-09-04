"""The decision kernel: one loop, bounded corrections, dissent as flags."""
from __future__ import annotations

import pytest

from app.services.phase3 import envelope as envelope_mod
from app.services.phase3 import kernel


def _payload() -> dict:
    return {"stage": "test", "concepts": [{"concept_id": "C-1"}]}


def _ok_checker(_response) -> list[str]:
    return []


def test_mechanical_defects_get_bounded_corrections_then_succeed():
    calls: list[dict] = []

    def provider(request: dict) -> dict:
        calls.append(request)
        return {"try": len(calls)}

    def checker(response: dict) -> list[str]:
        return [] if response["try"] >= 3 else ["still broken"]

    store = kernel.DecisionStore()
    decision = kernel.decide(
        kind="test",
        unit_id="U-1",
        envelope_sha256="e" * 64,
        payload=_payload(),
        provider=provider,
        checker=checker,
        store=store,
    )

    assert len(calls) == 3
    assert calls[1]["response_contract_feedback"] == ["still broken"]
    assert decision["response"]["try"] == 3


def test_defects_after_bounded_attempts_fail_closed_with_the_defect_list():
    calls = 0

    def provider(_request: dict) -> dict:
        nonlocal calls
        calls += 1
        return {}

    with pytest.raises(kernel.ContractError) as failed:
        kernel.decide(
            kind="test",
            unit_id="U-1",
            envelope_sha256="e" * 64,
            payload=_payload(),
            provider=provider,
            checker=lambda _response: ["C-1 confidence 0.500 is below 0.920"],
            store=kernel.DecisionStore(),
        )

    assert calls == 3
    assert "C-1 confidence 0.500 is below 0.920" in str(failed.value)
    assert failed.value.defects


def test_critic_dissent_becomes_flags_and_never_blocks():
    def critic(_request: dict) -> dict:
        return {
            "verdict": "rejected",
            "confidence": 0.91,
            "issues": ["the block does not support the causal claim"],
        }

    decision = kernel.decide(
        kind="test",
        unit_id="U-1",
        envelope_sha256="e" * 64,
        payload=_payload(),
        provider=lambda _request: {"fine": True},
        checker=_ok_checker,
        critic=critic,
        store=kernel.DecisionStore(),
    )

    flags = decision["review_flags"]
    assert any("rejected" in flag and "0.910" in flag for flag in flags)
    assert any("causal claim" in flag for flag in flags)
    assert decision["response"] == {"fine": True}


def test_a_crashing_critic_cannot_take_the_run_down():
    def critic(_request: dict) -> dict:
        raise RuntimeError("critic transport exploded")

    decision = kernel.decide(
        kind="test",
        unit_id="U-1",
        envelope_sha256="e" * 64,
        payload=_payload(),
        provider=lambda _request: {"fine": True},
        checker=_ok_checker,
        critic=critic,
        store=kernel.DecisionStore(),
    )

    assert any(
        "decision stands unaudited" in flag
        for flag in decision["review_flags"]
    )


def test_a_settled_decision_is_never_re_asked(tmp_path):
    calls = 0

    def provider(_request: dict) -> dict:
        nonlocal calls
        calls += 1
        return {"answer": 42}

    store = kernel.DecisionStore(tmp_path)
    kwargs = dict(
        kind="test",
        unit_id="U-1",
        envelope_sha256="e" * 64,
        payload=_payload(),
        provider=provider,
        checker=_ok_checker,
        store=store,
    )
    first = kernel.decide(**kwargs)
    second = kernel.decide(**kwargs)

    assert calls == 1
    assert first["key"] == second["key"]
    assert second["response"] == {"answer": 42}
    # A second store over the same directory replays too: resume is free.
    resumed = kernel.decide(**{**kwargs, "store": kernel.DecisionStore(tmp_path)})
    assert calls == 1
    assert resumed["response"] == {"answer": 42}


def test_store_entries_are_immutable():
    store = kernel.DecisionStore()
    first = store.put("k", {"value": 1})
    second = store.put("k", {"value": 2})
    assert first == second == {"value": 1}


def test_a_changed_payload_or_envelope_yields_a_new_key():
    base = dict(
        kind="test",
        unit_id="U-1",
        envelope_sha256="e" * 64,
        payload=_payload(),
    )
    key = kernel.decision_key(**base)
    assert kernel.decision_key(**{**base, "envelope_sha256": "f" * 64}) != key
    assert kernel.decision_key(
        **{**base, "payload": {**_payload(), "extra": 1}}
    ) != key
    assert kernel.decision_key(**base) == key


def test_envelope_requires_live_api_or_fails_closed(monkeypatch):
    from app.services import canonical_source_phase3 as phase3_core

    monkeypatch.setattr(phase3_core, "semantic_api_enabled", lambda: False)
    with pytest.raises(envelope_mod.LiveApiUnavailable):
        envelope_mod.require_live_api()


def test_envelope_rejects_missing_fields_and_broken_seals():
    with pytest.raises(envelope_mod.EnvelopeError):
        envelope_mod.validate({"envelope_version": 1})

    built = envelope_mod.build(
        graph={
            "source_contract_hash": "S-1",
            "topics": [{"topic_id": "T-1", "title": "Topic"}],
            "subtopics": [],
            "blocks": [{"block_id": "B-1", "topic_id": "T-1"}],
        },
        canonical={"blocks": [{"block_id": "B-1", "display_text": "text"}]},
        skeleton_rows=[{"concept_title": "A concept"}],
        inventory={"items": []},
        mined_types={"types": []},
    )
    assert envelope_mod.validate(built)

    tampered = dict(built)
    tampered["source_contract_hash"] = "S-2"
    with pytest.raises(envelope_mod.EnvelopeError, match="seal"):
        envelope_mod.validate(tampered)


def test_pure_confidence_shortfall_ships_flagged_after_one_attempt():
    """An honest sub-floor confidence must not kill a run (staging: one
    0.880 grounding failed a whole chapter). Structural defects still
    fail closed; confidence-only shortfalls ship with review flags — and
    after ONE attempt (register Q26): the prompts forbid inflating a score
    to pass a threshold, so a re-ask on the same evidence could only buy
    an inflated number for a full re-spend."""
    calls = 0

    def provider(_request: dict) -> dict:
        nonlocal calls
        calls += 1
        return {"confidence": 0.88}

    decision = kernel.decide(
        kind="test",
        unit_id="U-1",
        envelope_sha256="e" * 64,
        payload=_payload(),
        provider=provider,
        checker=lambda _r: [
            "[confidence] C-1 confidence 0.880 is below 0.920"
        ],
        store=kernel.DecisionStore(),
    )

    assert calls == 1
    assert decision["response"] == {"confidence": 0.88}
    assert any(
        "0.880 is below 0.920" in flag and "shipped for review" in flag
        for flag in decision["review_flags"]
    )


def test_a_structural_defect_beside_a_confidence_shortfall_still_re_asks():
    """Only a PURE confidence shortfall skips the bounded corrections."""
    calls = 0

    def provider(_request: dict) -> dict:
        nonlocal calls
        calls += 1
        if calls < 2:
            return {"confidence": 0.88}
        return {"confidence": 0.99, "rows": [1]}

    def checker(response: dict) -> list[str]:
        defects = []
        if "rows" not in response:
            defects.append("rows missing")
        if float(response.get("confidence") or 0) < 0.92:
            defects.append("[confidence] U-1 confidence is below 0.920")
        return defects

    decision = kernel.decide(
        kind="test",
        unit_id="U-1",
        envelope_sha256="e" * 64,
        payload=_payload(),
        provider=provider,
        checker=checker,
        store=kernel.DecisionStore(),
    )

    assert calls == 2
    assert decision["response"] == {"confidence": 0.99, "rows": [1]}
    assert decision["review_flags"] == []


def test_a_clean_pass_emits_nothing_and_the_band_is_gone():
    """Owner steer 2026-08-20: no human-review band.

    A verified audit at 0.91 — squarely in what used to be the
    0.900-0.919 band — with no issues emits NOTHING. An issue the critic
    names still records whatever the verdict label says (Q10:
    dissent-content is dissent); a "verified" at sub-floor confidence is
    a broken audit and never looks clean. The acceptance floor's
    [confidence] mechanics are a separate, untouched path.
    """
    from app.services.phase3 import kernel

    assert kernel.advisory_flags({
        "verdict": "verified", "confidence": 0.91, "issues": [],
    }) == []
    noted = kernel.advisory_flags({
        "verdict": "verified", "confidence": 0.99,
        "issues": ["a concern the audit still names"],
    })
    assert noted == ["critic: a concern the audit still names"]
    subfloor = kernel.advisory_flags({
        "verdict": "verified", "confidence": 0.5, "issues": [],
    })
    assert any("sub-floor" in flag for flag in subfloor)
    dissent = kernel.advisory_flags({
        "verdict": "dissent", "confidence": 0.91,
        "issues": ["the named defect"],
    })
    assert any("dissent" in flag for flag in dissent)
    assert any(flag == "critic: the named defect" for flag in dissent)

"""Model-certified terminal evidence narrowing.

Semantic support belongs to the provider/critic pair. Deterministic code owns
exact IDs, source addressing, persistence, count preservation and projection.
"""
from __future__ import annotations

import copy

import pytest

from app.services import canonical_source_phase22 as phase22
from app.services import (
    canonical_source_phase38_boundary_grounding_turnover_contract as phase38,
)
from app.services import evidence_narrowing as en


_SOURCE = (
    "The common difference of an arithmetic progression is the fixed number "
    "added to each term to obtain the next term. It can be positive, negative "
    "or zero."
)


def _graph(text_suffix: str = "") -> dict:
    return {
        "source_contract_hash": "source-contract" + text_suffix,
        "topics": [{"topic_id": "T-1", "title": "Arithmetic Progressions"}],
        "blocks": [
            {"block_id": "B-1", "topic_id": "T-1", "kind": "paragraph", "order": 2},
            {"block_id": "H-1", "topic_id": "T-1", "kind": "heading", "order": 1},
        ],
    }


def _canonical(text: str = _SOURCE) -> dict:
    return {
        "blocks": [
            {"block_id": "B-1", "display_text": text},
            {"block_id": "H-1", "display_text": "Arithmetic Progressions"},
        ]
    }


def _row(description: str, *, title: str = "Common difference") -> dict:
    return {
        "concept_title": title,
        "topic": "Arithmetic Progressions",
        "_semantic_topic_id": "T-1",
        "parent_concept": "Sequences",
        "concept_details": (
            f"Description: {description} // "
            "Achieving Mastery: The learner identifies the common difference."
        ),
        "_phase32_origin_concept_id": f"ORIGIN-{title.replace(' ', '-').upper()}",
    }


def _decision(payload: dict, *, description: str, kind: str = "narrowed") -> dict:
    concept = payload["concepts"][0]
    return {
        "decisions": [{
            "concept_id": concept["concept_id"],
            "action": "keep" if kind == "unchanged" else "rewrite",
            "rewrite_kind": kind,
            "description": description,
            "evidence_block_ids": ["B-1"],
            "kept_claims": ["The common difference is fixed."],
            "dropped_claims": [] if kind == "unchanged" else ["The matrix claim."],
            "reason": "Only the source-supported arithmetic-progression claim remains.",
        }]
    }


def _verified(payload: dict, *, verdict: str = "verified") -> dict:
    concept = payload["concepts"][0]
    return {
        "verdict": verdict,
        "confidence": 0.99,
        "issues": [] if verdict == "verified" else ["One causal claim is unsupported."],
        "decisions": [{
            "concept_id": concept["concept_id"],
            "verdict": verdict,
            "evidence_block_ids": ["B-1"],
            "unsupported_claims": [] if verdict == "verified" else ["Causal claim"],
            "reason": "The proposed Description is fully entailed." if verdict == "verified"
                      else "The proposed Description still overclaims.",
        }],
    }


def _transported(value):
    """Return a fake model callable that crosses the real exactly-once hook."""

    def call(payload):
        phase22._notify_openai_transport_started()
        return value(payload) if callable(value) else copy.deepcopy(value)

    return call


def test_evidence_index_excludes_heading_blocks():
    evidence = en.build_evidence(_graph(), _canonical())
    assert evidence["T-1"].block_ids == ("B-1",)


def test_provider_rewrite_requires_independent_critic_and_preserves_other_sections(tmp_path):
    original = _row(
        "The common difference is fixed. It also determines matrix eigenvalues."
    )
    rewritten = "The common difference is the fixed number added to obtain the next term."

    out, audit = en.narrow_records(
        [original],
        graph=_graph(),
        canonical=_canonical(),
        provider=_transported(lambda payload: _decision(payload, description=rewritten)),
        critic=_transported(_verified),
        cache_dir=tmp_path,
    )

    assert rewritten in out[0]["concept_details"]
    assert "eigenvalues" not in out[0]["concept_details"]
    assert "Achieving Mastery: The learner identifies" in out[0]["concept_details"]
    assert out[0]["topic"] == original["topic"]
    assert out[0]["concept_title"] == original["concept_title"]
    assert out[0][en.NARROWED_CONTRACT_FIELD]["basis"] == en.NARROWED_CONTRACT
    assert audit.rows[0].verified is True
    assert audit.rows[0].kind == "narrowed"


def test_verified_keep_is_byte_identical(tmp_path):
    row = _row("The common difference is fixed.")

    out, audit = en.narrow_records(
        [row],
        graph=_graph(),
        canonical=_canonical(),
        provider=_transported(
            lambda payload: _decision(
                payload,
                description="The common difference is fixed.",
                kind="unchanged",
            )
        ),
        critic=_transported(_verified),
        cache_dir=tmp_path,
    )

    assert out == [row]
    assert audit.rows[0].kind == "unchanged"
    assert audit.rows[0].verified is True


def test_accepted_decision_replays_without_another_paid_call(tmp_path):
    row = _row("The common difference is fixed. It controls eigenvalues.")
    rewritten = "The common difference is fixed."
    calls = {"provider": 0, "critic": 0}

    def provider(payload):
        calls["provider"] += 1
        phase22._notify_openai_transport_started()
        return _decision(payload, description=rewritten)

    def critic(payload):
        calls["critic"] += 1
        phase22._notify_openai_transport_started()
        return _verified(payload)

    first, first_audit = en.narrow_records(
        [row], graph=_graph(), canonical=_canonical(),
        provider=provider, critic=critic, cache_dir=tmp_path,
    )

    def must_not_run(_payload):
        raise AssertionError("accepted disposition was repurchased")

    second, second_audit = en.narrow_records(
        [row], graph=_graph(), canonical=_canonical(),
        provider=must_not_run, critic=must_not_run, cache_dir=tmp_path,
    )

    assert second == first
    assert calls == {"provider": 1, "critic": 1}
    assert first_audit.cache_status == "accepted_fresh"
    assert second_audit.cache_status == "accepted_replay"


def test_critic_rejection_gets_one_bounded_semantic_correction(tmp_path):
    row = _row("The common difference is fixed. It controls eigenvalues.")
    provider_payloads = []
    critic_calls = 0

    def provider(payload):
        provider_payloads.append(copy.deepcopy(payload))
        phase22._notify_openai_transport_started()
        description = (
            "The common difference is fixed and therefore controls eigenvalues."
            if payload["attempt"] == 1
            else "The common difference is fixed."
        )
        return _decision(payload, description=description)

    def critic(payload):
        nonlocal critic_calls
        critic_calls += 1
        phase22._notify_openai_transport_started()
        return _verified(payload, verdict="rejected" if critic_calls == 1 else "verified")

    out, audit = en.narrow_records(
        [row], graph=_graph(), canonical=_canonical(),
        provider=provider, critic=critic, cache_dir=tmp_path,
    )

    assert len(provider_payloads) == 2
    assert critic_calls == 2
    assert provider_payloads[1]["previous_critic_feedback"]["issues"]
    assert "controls eigenvalues" not in out[0]["concept_details"]
    assert audit.rows[0].verified is True


def test_two_critic_rejections_release_original_unchanged_not_lexically_trimmed(tmp_path):
    row = _row("The common difference is fixed. It controls eigenvalues.")

    out, audit = en.narrow_records(
        [row],
        graph=_graph(),
        canonical=_canonical(),
        provider=_transported(
            lambda payload: _decision(payload, description="The common difference is fixed.")
        ),
        critic=_transported(lambda payload: _verified(payload, verdict="rejected")),
        cache_dir=tmp_path,
    )

    assert out == [row]
    assert audit.rows[0].kind == "unmodified_unverified"
    assert "no lexical" in audit.rows[0].note


def test_ambiguous_started_request_is_never_replayed(tmp_path):
    row = _row("The common difference is fixed. It controls eigenvalues.")

    def crashes_after_transport(_payload):
        phase22._notify_openai_transport_started()
        raise SystemExit("worker died after request dispatch")

    with pytest.raises(SystemExit):
        en.narrow_records(
            [row], graph=_graph(), canonical=_canonical(),
            provider=crashes_after_transport, critic=_transported(_verified),
            cache_dir=tmp_path,
        )

    calls = 0

    def must_not_replay(_payload):
        nonlocal calls
        calls += 1
        raise AssertionError("ambiguous paid request replayed")

    out, audit = en.narrow_records(
        [row], graph=_graph(), canonical=_canonical(),
        provider=must_not_replay, critic=must_not_replay, cache_dir=tmp_path,
    )

    assert calls == 0
    assert out == [row]
    assert audit.rows[0].kind == "unmodified_unverified"
    assert "not replayed" in audit.rows[0].note


def test_crash_before_critic_transport_resumes_from_saved_provider_only(tmp_path):
    row = _row("The common difference is fixed. It controls eigenvalues.")
    provider_calls = 0

    def provider(payload):
        nonlocal provider_calls
        provider_calls += 1
        phase22._notify_openai_transport_started()
        return _decision(payload, description="The common difference is fixed.")

    def crash_before_transport(_payload):
        raise SystemExit("worker died before critic transport")

    with pytest.raises(SystemExit):
        en.narrow_records(
            [row], graph=_graph(), canonical=_canonical(),
            provider=provider, critic=crash_before_transport, cache_dir=tmp_path,
        )

    critic_calls = 0

    def critic(payload):
        nonlocal critic_calls
        critic_calls += 1
        phase22._notify_openai_transport_started()
        return _verified(payload)

    out, audit = en.narrow_records(
        [row], graph=_graph(), canonical=_canonical(),
        provider=lambda _payload: pytest.fail("provider response was not reused"),
        critic=critic, cache_dir=tmp_path,
    )

    assert provider_calls == 1
    assert critic_calls == 1
    assert "eigenvalues" not in out[0]["concept_details"]
    assert audit.rows[0].verified is True


def test_api_failure_releases_unchanged_and_caches_the_unverified_fallback(tmp_path):
    row = _row("The common difference is fixed. It controls eigenvalues.")
    calls = 0

    def unavailable(_payload):
        nonlocal calls
        calls += 1
        phase22._notify_openai_transport_started()
        raise RuntimeError("API unavailable")

    first, first_audit = en.narrow_records(
        [row], graph=_graph(), canonical=_canonical(),
        provider=unavailable, critic=_transported(_verified), cache_dir=tmp_path,
    )
    second, second_audit = en.narrow_records(
        [row], graph=_graph(), canonical=_canonical(),
        provider=lambda _payload: pytest.fail("fallback replay called provider"),
        critic=lambda _payload: pytest.fail("fallback replay called critic"),
        cache_dir=tmp_path,
    )

    assert calls == 1
    assert first == second == [row]
    assert first_audit.rows[0].kind == "unmodified_unverified"
    assert second_audit.cache_status == "unmodified_replay"


def test_unknown_evidence_id_is_a_contract_failure_not_a_semantic_guess(tmp_path):
    row = _row("The common difference is fixed. It controls eigenvalues.")

    def bad_provider(payload):
        response = _decision(payload, description="The common difference is fixed.")
        response["decisions"][0]["evidence_block_ids"] = ["NOT-A-BLOCK"]
        return response

    out, audit = en.narrow_records(
        [row], graph=_graph(), canonical=_canonical(),
        provider=_transported(bad_provider), critic=_transported(_verified),
        cache_dir=tmp_path,
    )

    assert out == [row]
    assert audit.rows[0].kind == "unmodified_unverified"
    assert "unknown evidence" in audit.rows[0].note


def test_no_canonical_evidence_makes_no_model_call(tmp_path):
    row = _row("Any claim.")

    out, audit = en.narrow_records(
        [row], graph=None, canonical=None,
        provider=lambda _payload: pytest.fail("provider should not run"),
        critic=lambda _payload: pytest.fail("critic should not run"),
        cache_dir=tmp_path,
    )

    assert out == [row]
    assert audit.rows[0].kind == "unchanged"
    assert "no usable canonical source blocks" in audit.rows[0].note


def test_only_named_concepts_are_reconsidered(tmp_path):
    alpha = _row("Alpha overclaim.", title="Alpha")
    beta = _row("Beta overclaim.", title="Beta")

    def provider(payload):
        assert [row["concept_title"] for row in payload["concepts"]] == ["Alpha"]
        phase22._notify_openai_transport_started()
        return _decision(payload, description="The common difference is fixed.")

    out, audit = en.narrow_records(
        [alpha, beta], graph=_graph(), canonical=_canonical(),
        concept_titles=["Alpha"], provider=provider, critic=_transported(_verified),
        cache_dir=tmp_path,
    )

    assert out[1] == beta
    assert [row.concept_title for row in audit.rows] == ["Alpha"]


def test_source_change_invalidates_the_cached_semantic_decision(tmp_path):
    row = _row("The common difference is fixed. It controls eigenvalues.")
    calls = 0

    def provider(payload):
        nonlocal calls
        calls += 1
        phase22._notify_openai_transport_started()
        return _decision(payload, description="The common difference is fixed.")

    en.narrow_records(
        [row], graph=_graph("-one"), canonical=_canonical(),
        provider=provider, critic=_transported(_verified), cache_dir=tmp_path,
    )
    en.narrow_records(
        [row], graph=_graph("-two"), canonical=_canonical(_SOURCE + " Extra evidence."),
        provider=provider, critic=_transported(_verified), cache_dir=tmp_path,
    )

    assert calls == 2


def test_accepted_cache_survives_phase38_final_state_write_and_bundle_replay(tmp_path):
    """The terminal caller's stale local state must not erase the accepted result."""

    row = _row("The common difference is fixed. It controls eigenvalues.")
    issue_key = "a" * 64
    original_state = {
        "scope": "upload-job:terminal-cache",
        "feedback": {},
        "active_issue_key": issue_key,
        "issue_buckets": {issue_key: {"feedback": {}}},
    }
    persisted = []
    store = {
        "scope": original_state["scope"],
        "state": original_state,
        "persisted_state": copy.deepcopy(original_state),
        "persist": lambda _expected, value: persisted.append(copy.deepcopy(value)),
    }
    token = phase38._ACTIVE_CONVERGENCE_STORE.set(store)
    try:
        first, _audit = en.narrow_records(
            [row], graph=_graph(), canonical=_canonical(),
            provider=_transported(
                lambda payload: _decision(
                    payload, description="The common difference is fixed."
                )
            ),
            critic=_transported(_verified),
            cache_dir=tmp_path / "first-worker",
        )
        # This is what _terminal_disposition does after narrow_records returns:
        # it continues with the state object it received before the cache's
        # intermediate CAS writes. The mirror must have kept that object current.
        original_state["status"] = "disposed"
        phase38._persist_convergence_state(original_state)
        portable = copy.deepcopy(persisted[-1])
    finally:
        phase38._ACTIVE_CONVERGENCE_STORE.reset(token)

    assert any(key.startswith(en._CACHE_PREFIX) for key in portable["feedback"])

    second_store = {
        "scope": portable["scope"],
        "state": portable,
        "persisted_state": copy.deepcopy(portable),
        "persist": lambda _expected, _value: None,
    }
    token = phase38._ACTIVE_CONVERGENCE_STORE.set(second_store)
    try:
        second, audit = en.narrow_records(
            [row], graph=_graph(), canonical=_canonical(),
            provider=lambda _payload: pytest.fail("portable cache replayed provider"),
            critic=lambda _payload: pytest.fail("portable cache replayed critic"),
            cache_dir=tmp_path / "restored-worker-with-empty-disk-cache",
        )
    finally:
        phase38._ACTIVE_CONVERGENCE_STORE.reset(token)

    assert second == first
    assert audit.cache_status == "accepted_replay"

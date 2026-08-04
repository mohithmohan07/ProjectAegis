"""Integration guards for terminal semantic narrowing and portable replay."""
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
        ],
    }


def _canonical(text: str = _SOURCE) -> dict:
    return {"blocks": [{"block_id": "B-1", "display_text": text}]}


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


def _decision(payload: dict, *, description: str) -> dict:
    concept = payload["concepts"][0]
    return {
        "decisions": [{
            "concept_id": concept["concept_id"],
            "action": "rewrite",
            "rewrite_kind": "narrowed",
            "description": description,
            "evidence_block_ids": ["B-1"],
            "kept_claims": ["The common difference is fixed."],
            "dropped_claims": ["The matrix claim."],
            "reason": "Only the source-supported claim remains.",
        }]
    }


def _verified(payload: dict) -> dict:
    concept = payload["concepts"][0]
    return {
        "verdict": "verified",
        "confidence": 0.99,
        "issues": [],
        "decisions": [{
            "concept_id": concept["concept_id"],
            "verdict": "verified",
            "evidence_block_ids": ["B-1"],
            "unsupported_claims": [],
            "reason": "The proposed Description is fully entailed.",
        }],
    }


def _transported(value):
    def call(payload):
        phase22._notify_openai_transport_started()
        return value(payload) if callable(value) else copy.deepcopy(value)

    return call


def test_certified_cross_topic_evidence_is_offered_to_the_semantic_pair(tmp_path):
    graph = {
        "source_contract_hash": "cross-topic-source-contract",
        "topics": [
            {"topic_id": "T-1", "title": "Arithmetic Progressions"},
            {"topic_id": "T-2", "title": "Finite Sums"},
        ],
        "blocks": [
            {"block_id": "B-1", "topic_id": "T-1", "kind": "paragraph", "order": 1},
            {"block_id": "B-2", "topic_id": "T-2", "kind": "paragraph", "order": 2},
        ],
    }
    canonical = {
        "blocks": [
            {"block_id": "B-1", "display_text": _SOURCE},
            {
                "block_id": "B-2",
                "display_text": "The sum of the first n terms is written as S_n.",
            },
        ]
    }
    row = _row(
        "The common difference affects the finite sum.",
        title="Common difference in finite sums",
    )
    row["topic"] = "Finite Sums"
    row["_semantic_topic_id"] = "T-2"
    row["_source_block_ids"] = ["B-1"]

    def provider(payload):
        blocks = payload["concepts"][0]["allowed_evidence_blocks"]
        assert {block["block_id"] for block in blocks} == {"B-1", "B-2"}
        phase22._notify_openai_transport_started()
        return _decision(
            payload,
            description=(
                "The common difference is the fixed number added to obtain "
                "the next term."
            ),
        )

    out, audit = en.narrow_records(
        [row],
        graph=graph,
        canonical=canonical,
        provider=provider,
        critic=_transported(_verified),
        cache_dir=tmp_path,
    )

    assert out[0][en.NARROWED_EVIDENCE_FIELD] == ["B-1"]
    assert audit.rows[0].verified is True


def test_active_checkpoint_must_persist_before_any_model_transport(tmp_path):
    row = _row("The common difference is fixed. It controls eigenvalues.")
    calls = 0
    issue_key = "b" * 64
    state = {
        "scope": "upload-job:checkpoint-write-failure",
        "feedback": {},
        "active_issue_key": issue_key,
        "issue_buckets": {issue_key: {"feedback": {}}},
    }

    def reject_checkpoint_write(_expected, _replacement):
        raise RuntimeError("checkpoint compare-and-swap failed")

    store = {
        "scope": state["scope"],
        "state": state,
        "persisted_state": copy.deepcopy(state),
        "persist": reject_checkpoint_write,
    }

    def provider(_payload):
        nonlocal calls
        calls += 1
        phase22._notify_openai_transport_started()
        raise AssertionError("transport crossed without a portable claim")

    token = phase38._ACTIVE_CONVERGENCE_STORE.set(store)
    try:
        out, audit = en.narrow_records(
            [row],
            graph=_graph(),
            canonical=_canonical(),
            provider=provider,
            critic=_transported(_verified),
            cache_dir=tmp_path,
        )
    finally:
        phase38._ACTIVE_CONVERGENCE_STORE.reset(token)

    assert calls == 0
    assert out == [row]
    assert audit.rows[0].kind == "unmodified_unverified"
    assert "portable Phase 3.8" in audit.rows[0].note


def test_failed_reverification_restores_the_pre_narrowing_description(tmp_path):
    original = _row(
        "The common difference is fixed. It also determines matrix eigenvalues."
    )
    accepted, _audit = en.narrow_records(
        [original],
        graph=_graph("-first"),
        canonical=_canonical(),
        provider=_transported(
            lambda payload: _decision(
                payload,
                description="The common difference is fixed.",
            )
        ),
        critic=_transported(_verified),
        cache_dir=tmp_path,
    )
    assert accepted != [original]

    def unavailable(_payload):
        phase22._notify_openai_transport_started()
        raise RuntimeError("provider unavailable for changed source")

    released, audit = en.narrow_records(
        accepted,
        graph=_graph("-changed"),
        canonical=_canonical(_SOURCE + " The source contract has changed."),
        provider=unavailable,
        critic=_transported(_verified),
        cache_dir=tmp_path,
    )

    assert released == [original]
    assert audit.rows[0].kind == "unmodified_unverified"


def test_terminal_model_output_budget_inherits_provider_ceiling(monkeypatch):
    from app.services import evidence_narrowing_models as models

    monkeypatch.setattr(phase22, "_MAX_OUTPUT_TOKENS", 128_000, raising=False)
    monkeypatch.delenv("AEGIS_EVIDENCE_NARROWING_MAX_OUTPUT_TOKENS", raising=False)
    assert models._model_output_tokens() == 128_000

    monkeypatch.setenv("AEGIS_EVIDENCE_NARROWING_MAX_OUTPUT_TOKENS", "64000")
    assert models._model_output_tokens() == 64_000


def test_reverification_keep_restores_the_original_description(tmp_path):
    original = _row(
        "The common difference is fixed. It also determines matrix eigenvalues."
    )
    accepted, _audit = en.narrow_records(
        [original],
        graph=_graph("-first-keep"),
        canonical=_canonical(),
        provider=_transported(
            lambda payload: _decision(
                payload,
                description="The common difference is fixed.",
            )
        ),
        critic=_transported(_verified),
        cache_dir=tmp_path,
    )

    def keep_original(payload):
        phase22._notify_openai_transport_started()
        concept = payload["concepts"][0]
        assert concept["description"] == (
            "The common difference is fixed. It also determines matrix eigenvalues."
        )
        return {
            "decisions": [{
                "concept_id": concept["concept_id"],
                "action": "keep",
                "rewrite_kind": "unchanged",
                "description": concept["description"],
                "evidence_block_ids": ["B-1"],
                "kept_claims": [concept["description"]],
                "dropped_claims": [],
                "reason": "The changed source now supports the complete Description.",
            }]
        }

    released, audit = en.narrow_records(
        accepted,
        graph=_graph("-changed-keep"),
        canonical=_canonical(
            _SOURCE + " The complete Description is now explicitly supported."
        ),
        provider=keep_original,
        critic=_transported(_verified),
        cache_dir=tmp_path,
    )

    assert released == [original]
    assert audit.rows[0].kind == "unchanged"
    assert audit.rows[0].verified is True

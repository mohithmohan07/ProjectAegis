"""Focused coverage for centralized Phase 3 semantic confidence gates."""
from __future__ import annotations

import pytest

from app.services import canonical_source_phase3 as phase3
from app.services import generation
from app.services import semantic_confidence_policy as policy


def _clear_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(policy.SEMANTIC_ACCEPTANCE_ENV, raising=False)
    monkeypatch.delenv(policy.DESTRUCTIVE_SEMANTIC_ENV, raising=False)


def test_default_policy_relaxes_only_non_destructive_semantics(monkeypatch):
    _clear_overrides(monkeypatch)

    assert policy.minimum(policy.ConfidenceGate.SEMANTIC) == 0.92
    assert policy.minimum(policy.ConfidenceGate.DESTRUCTIVE) == 0.96
    assert policy.minimum(policy.ConfidenceGate.SOURCE_CRITICAL) == 0.96
    assert policy.accepts(0.92)
    assert not policy.accepts(
        0.92,
        policy.ConfidenceGate.DESTRUCTIVE,
    )
    assert not policy.accepts(
        0.92,
        policy.ConfidenceGate.SOURCE_CRITICAL,
    )


def test_policy_overrides_are_validated_and_cache_sensitive(monkeypatch):
    _clear_overrides(monkeypatch)
    baseline = policy.cache_identity()

    monkeypatch.setenv(policy.SEMANTIC_ACCEPTANCE_ENV, "0.94")
    monkeypatch.setenv(policy.DESTRUCTIVE_SEMANTIC_ENV, "0.98")

    assert policy.minimum() == 0.94
    assert policy.minimum(policy.ConfidenceGate.DESTRUCTIVE) == 0.98
    assert policy.cache_identity() != baseline

    monkeypatch.setenv(policy.SEMANTIC_ACCEPTANCE_ENV, "0.80")
    with pytest.raises(ValueError, match="must be between 0.85 and 0.96"):
        policy.minimum()

    monkeypatch.setenv(policy.SEMANTIC_ACCEPTANCE_ENV, "0.92")
    monkeypatch.setenv(policy.DESTRUCTIVE_SEMANTIC_ENV, "0.95")
    with pytest.raises(ValueError, match="must be between 0.96 and 1.00"):
        policy.minimum(policy.ConfidenceGate.DESTRUCTIVE)


def test_lower_override_cannot_weaken_fixed_semantic_bands(monkeypatch):
    _clear_overrides(monkeypatch)
    baseline = policy.cache_identity()
    monkeypatch.setenv(policy.SEMANTIC_ACCEPTANCE_ENV, "0.85")

    assert policy.minimum() == 0.92
    assert policy.cache_identity() == baseline
    assert policy.semantic_band(0.92) == "accepted"
    assert policy.semantic_band(0.91) == "human_review"
    assert policy.semantic_band(0.899) == "rejected"
    assert not policy.accepts(0.919)


def test_stricter_override_only_raises_auto_accept_boundary(monkeypatch):
    _clear_overrides(monkeypatch)
    monkeypatch.setenv(policy.SEMANTIC_ACCEPTANCE_ENV, "0.94")

    assert policy.semantic_band(0.94) == "accepted"
    assert policy.semantic_band(0.93) == "rejected"
    assert policy.semantic_band(0.91) == "human_review"
    assert policy.minimum(policy.ConfidenceGate.DESTRUCTIVE) == 0.96
    assert policy.minimum(policy.ConfidenceGate.SOURCE_CRITICAL) == 0.96


def test_final_checkpoint_replays_semantics_when_policy_changes(monkeypatch):
    _clear_overrides(monkeypatch)
    checkpoint = generation._make_concept_checkpoint(
        "final_content_ready",
        records=[],
        question_task_inventory={"items": [], "stats": {}},
        mined_types={"types": []},
        method_row_snapshot=[],
    )

    assert "semantic confidence policy changed" not in (
        generation._final_checkpoint_refresh_reasons(
            checkpoint,
            sections=[],
            source_topic_excerpts=[],
        )
    )

    monkeypatch.setenv(policy.SEMANTIC_ACCEPTANCE_ENV, "0.96")
    assert "semantic confidence policy changed" in (
        generation._final_checkpoint_refresh_reasons(
            checkpoint,
            sections=[],
            source_topic_excerpts=[],
        )
    )

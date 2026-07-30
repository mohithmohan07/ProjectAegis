"""Focused coverage for centralized Phase 3 semantic confidence gates."""
from __future__ import annotations

import pytest

from app.services import canonical_source_phase3 as phase3
from app.services import canonical_source_phase31_grounding_contract as phase31
from app.services import canonical_source_phase32_topology_adjudication_contract as phase32
from app.services import canonical_source_phase33_preflight_contract as phase33
from app.services import (
    canonical_source_phase37_visual_topology_convergence_contract as phase37,
)
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


def test_hierarchy_and_grounding_use_semantic_acceptance_gate(monkeypatch):
    _clear_overrides(monkeypatch)
    classifications = {
        "SEC-0001": {
            "section_id": "SEC-0001",
            "role": "main_topic",
            "parent_section_id": "",
            "confidence": 0.92,
            "evidence": ["source heading"],
        }
    }

    accepted = phase3._apply_critic_repairs(
        classifications,
        {
            "verdict": "verified",
            "confidence": 0.92,
            "repairs": [],
            "issues": [],
        },
    )
    assert accepted == classifications

    proposals, errors = phase31._parse_proposals(
        {
            "concepts": [
                {
                    "concept_id": "CONCEPT-0001",
                    "source_block_ids": ["BLK-0001"],
                    "confidence": 0.92,
                    "reason": "Visible support.",
                }
            ]
        },
        expected_ids={"CONCEPT-0001"},
        allowed_blocks={"BLK-0001"},
    )
    assert not errors
    assert proposals["CONCEPT-0001"]["confidence"] == 0.92

    with pytest.raises(ValueError, match="0.919 is below 0.920"):
        phase3._apply_critic_repairs(
            classifications,
            {
                "verdict": "verified",
                "confidence": 0.919,
                "repairs": [],
                "issues": [],
            },
        )


def test_topology_and_type_hosts_accept_semantic_boundary(monkeypatch):
    _clear_overrides(monkeypatch)
    concept_id = "TOPOLOGY-CONCEPT-0001"
    concepts = {
        concept_id: {
            "concept_id": concept_id,
            "current_topic_id": "TOPIC-0001",
            "concept_title": "National Identity",
            "parent_concept": "Nation and State",
            "source_claim": "Citizens developed a shared national identity.",
        }
    }
    response = {
        "concepts": [
            {
                "concept_id": concept_id,
                "decision": "keep",
                "segments": [
                    {
                        "topic_id": "TOPIC-0001",
                        "concept_title": "National Identity",
                        "parent_concept": "Nation and State",
                        "description": (
                            "Citizens developed a shared national identity."
                        ),
                        "achieving_mastery": (
                            "Explain how shared identity supports nationhood."
                        ),
                        "keywords": ["identity"],
                        "confidence": 0.92,
                        "reason": "The current topic directly supports the claim.",
                    }
                ],
                "confidence": 0.92,
                "reason": "No topology change is required.",
            }
        ]
    }

    decisions, decision_errors = phase32._parse_decisions(
        response,
        concepts=concepts,
        topic_ids={"TOPIC-0001"},
    )
    assert not decision_errors
    assert decisions[concept_id]["confidence"] == 0.92

    host_plan, host_errors = phase33._parse_host_plan(
        {
            "assignments": [
                {
                    "assignment_unit_id": "TYPE-0001",
                    "decision": "existing",
                    "existing_concept_id": "EXISTING-0001",
                    "new_concept_key": "NONE",
                    "confidence": 0.92,
                    "reason": "The concept entails the method.",
                }
            ],
            "new_concepts": [],
        },
        unit_payloads=[
            {
                "assignment_unit_id": "TYPE-0001",
                "topic_id": "TOPIC-0001",
            }
        ],
        existing_concepts=[
            {
                "concept_id": "EXISTING-0001",
                "topic_id": "TOPIC-0001",
            }
        ],
        source_blocks=[],
    )
    assert not host_errors
    assert host_plan is not None
    assert host_plan["assignments"][0]["confidence"] == 0.92


def test_retirement_requires_destructive_proposal_and_review_gate(monkeypatch):
    _clear_overrides(monkeypatch)
    concept_id = "TOPOLOGY-CONCEPT-0001"
    concepts = {
        concept_id: {
            "concept_id": concept_id,
            "current_topic_id": "TOPIC-0001",
            "concept_title": "Unsupported inference",
            "parent_concept": "Source",
            "source_claim": "Unsupported inference",
        }
    }
    response = {
        "concepts": [
            {
                "concept_id": concept_id,
                "decision": "retire",
                "segments": [],
                "retire_into_concept_id": "NONE",
                "confidence": 0.95,
                "reason": "No source-supported teaching objective remains.",
            }
        ]
    }

    _parsed, errors = phase37._parse_decisions(
        response,
        concepts=concepts,
        topic_ids={"TOPIC-0001"},
    )
    assert any(
        "retirement confidence 0.950 is below 0.960" in error
        for error in errors
    )

    review = {
        "verdict": "verified",
        "confidence": 0.92,
        "accepted_concept_ids": [concept_id],
        "rejected_concept_ids": [],
        "issues": [],
    }
    assert phase31._review_state(
        review,
        concept_ids={concept_id},
    )["verified"]
    assert not phase31._review_state(
        review,
        concept_ids={concept_id},
        confidence_gate=policy.ConfidenceGate.DESTRUCTIVE,
    )["verified"]

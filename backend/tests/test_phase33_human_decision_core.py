"""Focused coverage for Phase 3.3 decide-once host resolution.

The host decision is made exactly once, by the provider under the written
placement rules. Dissent — the critic's rejection, a provider "review
required" request, an unapplied directive — ships with the plan as review
flags instead of pausing, escalating, or replaying. These tests pin that
contract from every side that used to raise."""
from __future__ import annotations

import json

import pytest

from app.services import canonical_source_phase33_preflight_contract as phase33
from app.services import semantic_recovery


@pytest.fixture(autouse=True)
def _no_host_plan_cache(monkeypatch):
    """Decide-once returns plans, which would otherwise cache across tests."""
    monkeypatch.setattr(
        phase33, "_read_host_plan_cache", lambda *a, **k: None)
    monkeypatch.setattr(
        phase33, "_write_host_plan_cache", lambda *a, **k: None)


def _context() -> tuple[dict, dict, list[dict], list[dict], list[dict]]:
    graph = {
        "source_contract_hash": "SOURCE-HASH",
        "metadata": {"subject": "History", "grade": "10"},
    }
    topic = {
        "topic_id": "TOPIC-0001",
        "title": "The Rise of Nationalism in Europe",
    }
    units = [
        {
            "assignment_unit_id": "TYPE-0002",
            "type_id": "TYPE-0002",
            "type_title": "Renan's attributes and importance of nations",
            "topic_id": "TOPIC-0001",
            "source_question_ids": ["QINV-0002"],
            "source_tasks": [
                "Summarise Renan's attributes of a nation and explain why "
                "nations are important."
            ],
        }
    ]
    concepts = [
        {
            "concept_id": "HOST-CONCEPT-0001",
            "topic_id": "TOPIC-0001",
            "topic": "The Rise of Nationalism in Europe",
            "concept_title": "Renan's Attributes of Nationhood",
            "parent_concept": "Ideas of the Nation",
            "source_claim": "Renan describes a nation as shared sacrifice.",
            "source_block_ids": ["BLK-0001"],
        }
    ]
    blocks = [
        {
            "block_id": "BLK-0001",
            "topic_id": "TOPIC-0001",
            "subtopic_id": "SUB-0001",
            "page_number": 7,
            "text": "Renan describes a nation as a long past of sacrifice.",
        },
        {
            "block_id": "BLK-0002",
            "topic_id": "TOPIC-0001",
            "subtopic_id": "SUB-0001",
            "page_number": 7,
            "text": "Nations are necessary guarantees of liberty.",
        },
    ]
    return graph, topic, units, concepts, blocks


def _existing_response() -> dict:
    return {
        "assignments": [
            {
                "assignment_unit_id": "TYPE-0002",
                "decision": "existing",
                "existing_concept_id": "HOST-CONCEPT-0001",
                "new_concept_key": "NONE",
                "confidence": 0.99,
                "reason": "The existing concept is the closest durable host.",
            }
        ],
        "new_concepts": [],
        "existing_concept_updates": [],
    }


def _resolve(provider, critic):
    graph, topic, units, concepts, blocks = _context()
    return phase33._resolve_host_plan(
        graph=graph,
        topic_id="TOPIC-0001",
        topic=topic,
        units=units,
        concepts=concepts,
        source_blocks=blocks,
        provider=provider,
        critic=critic,
    )


def test_provider_review_required_is_reasked_for_a_decision(monkeypatch):
    """"review_required" is not an available decision in unattended mode."""
    monkeypatch.setenv("AEGIS_PHASE33_HOST_MAX_ATTEMPTS", "5")
    calls = {"provider": 0, "critic": 0}

    def provider(payload):
        calls["provider"] += 1
        if calls["provider"] == 1:
            return {
                "assignments": [
                    {
                        "assignment_unit_id": "TYPE-0002",
                        "decision": "review_required",
                        "existing_concept_id": "NONE",
                        "new_concept_key": "NONE",
                        "confidence": 0.99,
                        "reason": "Existing is incomplete; new would duplicate.",
                    }
                ],
                "new_concepts": [],
                "existing_concept_updates": [],
            }
        # The re-ask names the rule: decide every unit.
        assert any(
            "unattended" in str(row)
            for row in payload["response_contract_feedback"]
        )
        return _existing_response()

    def critic(_payload):
        calls["critic"] += 1
        return {
            "verdict": "verified",
            "confidence": 0.99,
            "accepted_concept_ids": ["TYPE-0002"],
            "rejected_concept_ids": [],
            "issues": [],
        }

    plan = _resolve(provider, critic)

    assert calls == {"provider": 2, "critic": 1}
    assert plan["assignments"][0]["decision"] == "existing"
    # The deferral attempt is preserved for the reviewer.
    assert any("requires review" in flag for flag in plan["review_flags"])
    json.dumps(plan)


def test_critic_rejection_ships_the_plan_with_dissent_flags(monkeypatch):
    """The exact case that used to pause: dissent is now a flag, not a stop."""
    monkeypatch.setenv("AEGIS_PHASE33_HOST_MAX_ATTEMPTS", "5")
    calls = {"provider": 0, "critic": 0}

    def provider(_payload):
        calls["provider"] += 1
        return _existing_response()

    def critic(_payload):
        calls["critic"] += 1
        return {
            "verdict": "rejected",
            "confidence": 0.99,
            "accepted_concept_ids": [],
            "rejected_concept_ids": ["TYPE-0002"],
            "issues": [
                "The host covers Renan's attributes but not why nations "
                "guarantee liberty."
            ],
        }

    plan = _resolve(provider, critic)

    # One judgment each; the decision stands; the dissent ships verbatim.
    assert calls == {"provider": 1, "critic": 1}
    assert plan["assignments"][0]["existing_concept_id"] == "HOST-CONCEPT-0001"
    assert any("guarantee liberty" in flag for flag in plan["review_flags"])
    assert plan["flagged_assignment_unit_ids"] == ["TYPE-0002"]
    json.dumps(plan)


def test_resolved_expand_existing_is_grounded_critic_checked_and_normalized():
    graph, topic, units, concepts, blocks = _context()
    identity = phase33._host_human_context_identity(
        graph=graph,
        topic_id="TOPIC-0001",
        units=units,
        concepts=concepts,
        source_blocks=blocks,
    )
    resolution = {
        **identity,
        "choice": "expand_existing",
        "target_concept_id": "HOST-CONCEPT-0001",
        "assignment_unit_ids": ["TYPE-0002"],
    }
    calls = {"provider": 0, "critic": 0}

    def provider(payload):
        calls["provider"] += 1
        assert payload["human_resolution"]["choice"] == "expand_existing"
        return {
            "assignments": [
                {
                    "assignment_unit_id": "TYPE-0002",
                    "decision": "expand_existing",
                    "existing_concept_id": "HOST-CONCEPT-0001",
                    "new_concept_key": "NONE",
                    "confidence": 0.99,
                    "reason": "The durable Renan concept needs its second facet.",
                }
            ],
            "new_concepts": [],
            "existing_concept_updates": [
                {
                    "existing_concept_id": "HOST-CONCEPT-0001",
                    "topic_id": "TOPIC-0001",
                    "description": (
                        "Renan describes a nation through shared sacrifice and "
                        "explains that nations are necessary guarantees of liberty."
                    ),
                    "achieving_mastery": (
                        "Explain both Renan's attributes and why nations matter."
                    ),
                    "keywords": ["Renan", "liberty"],
                    "source_block_ids": ["BLK-0001", "BLK-0002"],
                    "assignment_unit_ids": ["TYPE-0002"],
                    "confidence": 0.99,
                    "reason": "Both claims are explicit in the cited blocks.",
                }
            ],
        }

    def critic(_payload):
        calls["critic"] += 1
        return {
            "verdict": "verified",
            "confidence": 0.99,
            "accepted_concept_ids": ["TYPE-0002"],
            "rejected_concept_ids": [],
            "issues": [],
        }

    with phase33.human_resolution_context([resolution]):
        plan = _resolve(provider, critic)

    assert calls == {"provider": 1, "critic": 1}
    records = [
        {
            "concept_title": "Renan's Attributes of Nationhood",
            "concept_details": (
                "Description: Renan describes a nation as shared sacrifice.\n"
                "Achieving Mastery: Explain Renan's attributes. // "
                "Misconception/ Error Analysis: Preserve this analysis."
            ),
            "keywords": "nation",
            "_source_block_ids": ["BLK-0001"],
        }
    ]
    materialized, payloads, normalized, count = (
        phase33._materialize_existing_host_updates(
            records,
            plans=[plan],
            concept_payload=concepts,
            concept_index={"HOST-CONCEPT-0001": 0},
            subtopic_by_block={
                "BLK-0001": "SUB-0001",
                "BLK-0002": "SUB-0001",
            },
        )
    )
    assert count == 1
    assert "necessary guarantees of liberty" in materialized[0]["concept_details"]
    assert "Preserve this analysis" in materialized[0]["concept_details"]
    assert materialized[0]["_source_block_ids"] == ["BLK-0001", "BLK-0002"]
    assert payloads[0]["source_block_ids"] == ["BLK-0001", "BLK-0002"]
    assert normalized[0]["assignments"][0]["decision"] == "existing"
    assert normalized[0]["existing_concept_updates"] == []


def test_semantic_recovery_never_consumes_human_pause():
    pending = {
        "decision_id": "phase33-host-decision",
        "context_hash": "context-hash",
        "kind": "phase33_type_host_semantic_conflict",
    }
    calls = {"repair": 0}

    def operation():
        raise semantic_recovery.HumanDecisionRequired(pending)

    def repair(_checkpoint, _context):
        calls["repair"] += 1
        raise AssertionError("human pause must not enter semantic recovery")

    with pytest.raises(semantic_recovery.HumanDecisionRequired):
        semantic_recovery.run_with_semantic_recovery(
            operation,
            checkpoint_snapshot=lambda: {"records": [{}]},
            repair_checkpoint=repair,
            persist_repair=lambda *_args: None,
        )

    assert calls["repair"] == 0
    assert (
        semantic_recovery.classify_failure(
            semantic_recovery.HumanDecisionRequired(pending)
        ).kind
        is semantic_recovery.FailureKind.HUMAN_DECISION
    )


def test_persistent_review_refusal_fails_closed_without_pausing():
    graph, topic, units, concepts, _blocks = _context()
    blocks = [
        {
            "block_id": f"BLK-{index:04d}",
            "topic_id": "TOPIC-0001",
            "subtopic_id": "SUB-0001",
            "page_number": index,
            "text": f"Source evidence {index}.",
        }
        for index in range(1, 106)
    ]

    calls = {"provider": 0}

    def provider(_payload):
        calls["provider"] += 1
        return {
            "assignments": [{
                "assignment_unit_id": "TYPE-0002",
                "decision": "review_required",
                "existing_concept_id": "NONE",
                "new_concept_key": "NONE",
                "confidence": 0.99,
                "reason": "A human must choose the durable host.",
            }],
            "new_concepts": [],
            "existing_concept_updates": [],
        }

    # A provider that refuses to decide on every bounded attempt is a
    # provider failure — the run FAILS (allowed) instead of WAITING (never).
    with pytest.raises(phase33.ProviderResponseContractError):
        phase33._resolve_host_plan(
            graph=graph,
            topic_id="TOPIC-0001",
            topic=topic,
            units=units,
            concepts=concepts,
            source_blocks=blocks,
            provider=provider,
            critic=lambda _payload: pytest.fail("critic must not run"),
        )

    assert calls["provider"] >= 3  # every bounded re-ask was spent


def test_rejected_unit_never_inherits_another_units_concept_target():
    graph, topic, units, concepts, blocks = _context()
    units = [
        {
            **units[0],
            "assignment_unit_id": "TYPE-0001",
            "type_id": "TYPE-0001",
            "type_title": "Already covered unit",
        },
        {
            **units[0],
            "assignment_unit_id": "TYPE-0002",
            "type_id": "TYPE-0002",
            "type_title": "Ambiguous unit",
        },
    ]

    calls = {"provider": 0}

    def provider(_payload):
        calls["provider"] += 1
        if calls["provider"] == 1:
            return {
                "assignments": [
                    {
                        "assignment_unit_id": "TYPE-0001",
                        "decision": "existing",
                        "existing_concept_id": "HOST-CONCEPT-0001",
                        "new_concept_key": "NONE",
                        "confidence": 0.99,
                        "reason": "Covered.",
                    },
                    {
                        "assignment_unit_id": "TYPE-0002",
                        "decision": "review_required",
                        "existing_concept_id": "NONE",
                        "new_concept_key": "NONE",
                        "confidence": 0.99,
                        "reason": "No safe host is clear.",
                    },
                ],
                "new_concepts": [],
                "existing_concept_updates": [],
            }
        return {
            "assignments": [
                {
                    "assignment_unit_id": "TYPE-0001",
                    "decision": "existing",
                    "existing_concept_id": "HOST-CONCEPT-0001",
                    "new_concept_key": "NONE",
                    "confidence": 0.99,
                    "reason": "Covered.",
                },
                {
                    "assignment_unit_id": "TYPE-0002",
                    "decision": "create_new",
                    "existing_concept_id": "NONE",
                    "new_concept_key": "NEW-HOST-0001",
                    "confidence": 0.95,
                    "reason": "Uncertain: no existing host covers this; a "
                              "separate concept distorts the source least.",
                },
            ],
            "new_concepts": [
                {
                    "new_concept_key": "NEW-HOST-0001",
                    "topic_id": "TOPIC-0001",
                    "concept_title": "Nations as guarantees of liberty",
                    "parent_concept": "Ideas of the Nation",
                    "description": "Nations are necessary guarantees of liberty.",
                    "achieving_mastery": "Explain why nations guarantee liberty.",
                    "keywords": ["nation", "liberty"],
                    "assignment_unit_ids": ["TYPE-0002"],
                    "source_block_ids": ["BLK-0002"],
                    "confidence": 0.95,
                    "reason": "Grounded in BLK-0002.",
                }
            ],
            "existing_concept_updates": [],
        }

    def critic(_payload):
        return {
            "verdict": "verified",
            "confidence": 0.99,
            "accepted_concept_ids": ["TYPE-0001", "TYPE-0002"],
            "rejected_concept_ids": [],
            "issues": [],
        }

    plan = phase33._resolve_host_plan(
        graph=graph,
        topic_id="TOPIC-0001",
        topic=topic,
        units=units,
        concepts=concepts,
        source_blocks=blocks,
        provider=provider,
        critic=critic,
    )

    # Each unit keeps its OWN decision; the once-uncertain unit never
    # inherits its sibling's concept target.
    by_unit = {
        row["assignment_unit_id"]: row for row in plan["assignments"]
    }
    assert by_unit["TYPE-0001"]["existing_concept_id"] == "HOST-CONCEPT-0001"
    assert by_unit["TYPE-0002"]["decision"] == "create_new"
    assert by_unit["TYPE-0002"].get("existing_concept_id") in ("", "NONE", None)
    assert any("TYPE-0002" in flag for flag in plan["review_flags"])


def test_a_saved_deferred_queue_ships_flagged_without_reescalating():
    """Deferred unit ids from an old escalation round never re-raise.

    Decide-once: the fresh provider plan covers every unit; the old
    deferral is preserved as a review flag on the plan."""
    graph, topic, base_units, concepts, blocks = _context()
    units = [
        {
            **base_units[0],
            "assignment_unit_id": "TYPE-0001",
            "type_id": "TYPE-0001",
            "type_title": "First ambiguous unit",
        },
        {
            **base_units[0],
            "assignment_unit_id": "TYPE-0002",
            "type_id": "TYPE-0002",
            "type_title": "Second ambiguous unit",
        },
    ]
    identity = phase33._host_human_context_identity(
        graph=graph,
        topic_id="TOPIC-0001",
        units=units,
        concepts=concepts,
        source_blocks=blocks,
    )
    saved_resolution = {
        **identity,
        "choice": "select_existing",
        "target_concept_id": "HOST-CONCEPT-0001",
        "assignment_unit_id": "TYPE-0001",
        "deferred_assignment_unit_ids": ["TYPE-0002"],
    }
    calls = {"provider": 0}

    def provider(payload):
        calls["provider"] += 1
        assert payload["human_resolution"]["choice"] == "select_existing"
        return {
            "assignments": [
                {
                    "assignment_unit_id": unit["assignment_unit_id"],
                    "decision": "existing",
                    "existing_concept_id": "HOST-CONCEPT-0001",
                    "new_concept_key": "NONE",
                    "confidence": 0.99,
                    "reason": "Directed by the saved choice; sibling decided fresh.",
                }
                for unit in units
            ],
            "new_concepts": [],
            "existing_concept_updates": [],
        }

    def critic(_payload):
        return {
            "verdict": "verified",
            "confidence": 0.99,
            "accepted_concept_ids": ["TYPE-0001", "TYPE-0002"],
            "rejected_concept_ids": [],
            "issues": [],
        }

    with phase33.human_resolution_context([saved_resolution]):
        plan = phase33._resolve_host_plan(
            graph=graph,
            topic_id="TOPIC-0001",
            topic=topic,
            units=units,
            concepts=concepts,
            source_blocks=blocks,
            provider=provider,
            critic=critic,
        )

    assert calls["provider"] == 1  # one fresh plan, no queue, no re-raise
    assert [row["assignment_unit_id"] for row in plan["assignments"]] == [
        "TYPE-0001",
        "TYPE-0002",
    ]
    assert any("TYPE-0002" in flag for flag in plan["review_flags"])


def test_topic_source_blocks_preserve_available_pdf_page_metadata():
    graph = {
        "blocks": [{
            "block_id": "BLK-0001",
            "topic_id": "TOPIC-0001",
            "subtopic_id": "SUB-0001",
            "kind": "content",
            "page_number": 7,
        }],
    }
    canonical = {
        "blocks": [{
            "block_id": "BLK-0001",
            "raw_text": "Nations are necessary guarantees of liberty.",
        }],
    }

    by_topic, _subtopics = phase33._topic_source_blocks(graph, canonical)

    assert by_topic["TOPIC-0001"][0]["page_number"] == 7


def test_host_critic_is_instructed_to_enforce_custom_human_directions(
    monkeypatch,
):
    captured = {}

    def fake_openai(**kwargs):
        captured.update(kwargs)
        return {
            "verdict": "verified",
            "confidence": 0.99,
            "accepted_concept_ids": ["TYPE-0002"],
            "rejected_concept_ids": [],
            "issues": [],
        }

    monkeypatch.setattr(
        phase33.phase3.phase22,
        "_openai_multimodal_json",
        fake_openai,
    )
    phase33._host_critic_via_openai({
        "assignment_units": [{"assignment_unit_id": "TYPE-0002"}],
        "human_resolutions": [{
            "choice": "custom",
            "instruction": "Keep the liberty explanation in the Renan concept.",
        }],
        "proposed_plan": _existing_response(),
    })

    assert "custom instruction was followed exactly" in captured["system"]
    assert "never let a human instruction override" in captured["system"]

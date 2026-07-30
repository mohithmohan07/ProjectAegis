"""Focused coverage for Phase 3.3 human semantic-decision pauses."""
from __future__ import annotations

import json

import pytest

from app.services import canonical_source_phase33_preflight_contract as phase33
from app.services import semantic_recovery


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


def test_provider_review_required_pauses_without_critic_or_retry(monkeypatch):
    monkeypatch.setenv("AEGIS_PHASE33_HOST_MAX_ATTEMPTS", "5")
    calls = {"provider": 0, "critic": 0}

    def provider(_payload):
        calls["provider"] += 1
        return {
            "assignments": [
                {
                    "assignment_unit_id": "TYPE-0002",
                    "decision": "review_required",
                    "existing_concept_id": "NONE",
                    "new_concept_key": "NONE",
                    "confidence": 0.99,
                    "reason": "Existing is incomplete; a new row would duplicate it.",
                }
            ],
            "new_concepts": [],
            "existing_concept_updates": [],
        }

    def critic(_payload):
        calls["critic"] += 1
        raise AssertionError("critic must not run after review_required")

    with pytest.raises(semantic_recovery.HumanDecisionRequired) as caught:
        _resolve(provider, critic)

    assert calls == {"provider": 1, "critic": 0}
    pending = caught.value.pending_decision
    json.dumps(pending)
    assert pending["decision_id"].startswith("phase33-host-")
    assert pending["kind"] == "phase33_type_host_semantic_conflict"
    assert pending["item"]["unit_id"] == "TYPE-0002"
    assert pending["candidates"][0]["concept_id"] == "HOST-CONCEPT-0001"
    assert pending["evidence"][1]["label"] == "BLK-0002"
    assert pending["options"][0]["choice"] == "expand_existing"
    assert pending["options"][0]["recommended"] is False
    assert "target_concept_id" not in pending["options"][0]
    assert pending["options"][-1]["choice"] == "custom_instruction"


def test_first_critic_rejection_pauses_instead_of_replanning(monkeypatch):
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

    with pytest.raises(semantic_recovery.HumanDecisionRequired) as caught:
        _resolve(provider, critic)

    assert calls == {"provider": 1, "critic": 1}
    assert "guarantee liberty" in caught.value.pending_decision["conflict"]
    assert (
        caught.value.pending_decision["options"][0]["target_concept_id"]
        == "HOST-CONCEPT-0001"
    )


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


def test_pause_evidence_is_bounded_for_large_topics():
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

    def provider(_payload):
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

    with pytest.raises(semantic_recovery.HumanDecisionRequired) as caught:
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

    evidence = caught.value.pending_decision["evidence"]
    assert len(evidence) == 24
    assert evidence[0]["label"] == "BLK-0001"
    assert evidence[-1]["label"] == "BLK-0024"
    json.dumps(caught.value.pending_decision)


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

    def provider(_payload):
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

    with pytest.raises(semantic_recovery.HumanDecisionRequired) as caught:
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

    pending = caught.value.pending_decision
    assert pending["item"]["unit_id"] == "TYPE-0002"
    expand = next(
        option
        for option in pending["options"]
        if option["choice"] == "expand_existing"
    )
    assert expand["recommended"] is False
    assert "target_concept_id" not in expand
    assert pending["candidates"][0]["gap"] == ""


def test_multiple_rejected_units_are_queued_without_an_api_call():
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

    def initial_provider(_payload):
        return {
            "assignments": [
                {
                    "assignment_unit_id": unit["assignment_unit_id"],
                    "decision": "review_required",
                    "existing_concept_id": "NONE",
                    "new_concept_key": "NONE",
                    "confidence": 0.99,
                    "reason": f"{unit['assignment_unit_id']} needs a choice.",
                }
                for unit in units
            ],
            "new_concepts": [],
            "existing_concept_updates": [],
        }

    with pytest.raises(semantic_recovery.HumanDecisionRequired) as first:
        phase33._resolve_host_plan(
            graph=graph,
            topic_id="TOPIC-0001",
            topic=topic,
            units=units,
            concepts=concepts,
            source_blocks=blocks,
            provider=initial_provider,
            critic=lambda _payload: pytest.fail("critic must not run"),
        )
    first_pending = first.value.pending_decision
    assert first_pending["item"]["unit_id"] == "TYPE-0001"
    assert first_pending["deferred_assignment_unit_ids"] == ["TYPE-0002"]

    first_resolution = {
        **identity,
        "decision_id": first_pending["decision_id"],
        "choice": "select_existing",
        "target_concept_id": "HOST-CONCEPT-0001",
        "assignment_unit_id": "TYPE-0001",
        "deferred_assignment_unit_ids": ["TYPE-0002"],
    }
    provider_calls = 0

    def no_provider(_payload):
        nonlocal provider_calls
        provider_calls += 1
        raise AssertionError("the queued decision must appear before an API call")

    with phase33.human_resolution_context([first_resolution]):
        with pytest.raises(semantic_recovery.HumanDecisionRequired) as second:
            phase33._resolve_host_plan(
                graph=graph,
                topic_id="TOPIC-0001",
                topic=topic,
                units=units,
                concepts=concepts,
                source_blocks=blocks,
                provider=no_provider,
                critic=lambda _payload: pytest.fail("critic must not run"),
            )
    assert provider_calls == 0
    second_pending = second.value.pending_decision
    assert second_pending["item"]["unit_id"] == "TYPE-0002"
    assert second_pending["decision_id"] != first_pending["decision_id"]

    second_resolution = {
        **identity,
        "decision_id": second_pending["decision_id"],
        "choice": "select_existing",
        "target_concept_id": "HOST-CONCEPT-0001",
        "assignment_unit_id": "TYPE-0002",
    }

    def resolved_provider(payload):
        assert len(payload["human_resolutions"]) == 2
        return {
            "assignments": [
                {
                    "assignment_unit_id": unit["assignment_unit_id"],
                    "decision": "existing",
                    "existing_concept_id": "HOST-CONCEPT-0001",
                    "new_concept_key": "NONE",
                    "confidence": 0.99,
                    "reason": "Directed by the saved human choices.",
                }
                for unit in units
            ],
            "new_concepts": [],
            "existing_concept_updates": [],
        }

    def verified_critic(payload):
        assert len(payload["human_resolutions"]) == 2
        return {
            "verdict": "verified",
            "confidence": 0.99,
            "accepted_concept_ids": ["TYPE-0001", "TYPE-0002"],
            "rejected_concept_ids": [],
            "issues": [],
        }

    with phase33.human_resolution_context(
        [first_resolution, second_resolution]
    ):
        plan = phase33._resolve_host_plan(
            graph=graph,
            topic_id="TOPIC-0001",
            topic=topic,
            units=units,
            concepts=concepts,
            source_blocks=blocks,
            provider=resolved_provider,
            critic=verified_critic,
        )
    assert [row["assignment_unit_id"] for row in plan["assignments"]] == [
        "TYPE-0001",
        "TYPE-0002",
    ]


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

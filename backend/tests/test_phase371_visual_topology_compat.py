"""Compatibility regressions for Phase 3.7.1 visual topology guards."""
from __future__ import annotations

from app.services import canonical_source_phase32_topology_adjudication_contract as phase32
from app.services import canonical_source_phase37_visual_topology_convergence_contract as phase37


def _concept(concept_id: str, title: str) -> dict:
    return {
        "concept_id": concept_id,
        "source_order": int(concept_id.rsplit("-", 1)[-1]),
        "current_topic_id": "TOPIC-0001",
        "current_topic_title": "Topic",
        "concept_title": title,
        "parent_concept": "Parent",
        "source_claim": f"Source claim for {title}.",
        "existing_mastery": f"The learner explains {title}.",
    }


def _keep_row(concept: dict) -> dict:
    return {
        "concept_id": concept["concept_id"],
        "decision": "keep",
        "segments": [
            {
                "topic_id": concept["current_topic_id"],
                "concept_title": concept["concept_title"],
                "parent_concept": concept["parent_concept"],
                "description": concept["source_claim"],
                "achieving_mastery": concept["existing_mastery"],
                "keywords": [],
                "confidence": 0.99,
                "reason": "Fully supported.",
            }
        ],
        "retire_into_concept_id": "NONE",
        "confidence": 0.99,
        "reason": "Fully supported.",
    }


def test_plain_source_block_retains_exact_legacy_payload_shape():
    graph_block = {
        "block_id": "BLK-00001",
        "kind": "paragraph",
        "topic_id": "TOPIC-0001",
    }
    canonical_block = {
        "block_id": "BLK-00001",
        "display_text": "Exact source evidence without a synthetic label.",
    }

    value = phase37._evidence_text(
        graph_block,
        canonical_block,
        {"figures": [], "images": []},
    )

    assert value == "Exact source evidence without a synthetic label."


def test_retirement_target_must_be_decided_in_the_same_batch():
    retiring = _concept("TOPOLOGY-CONCEPT-0001", "Unsupported visual inference")
    outside = _concept("TOPOLOGY-CONCEPT-0002", "Existing censorship concept")
    directory = {
        retiring["concept_id"]: retiring,
        outside["concept_id"]: outside,
    }
    token = phase37._ACTIVE_CONCEPT_DIRECTORY.set(directory)
    try:
        parsed, errors = phase32._parse_decisions(
            {
                "concepts": [
                    {
                        "concept_id": retiring["concept_id"],
                        "decision": "retire",
                        "segments": [],
                        "retire_into_concept_id": outside["concept_id"],
                        "confidence": 0.99,
                        "reason": "Subsumed by another concept.",
                    }
                ]
            },
            concepts={retiring["concept_id"]: retiring},
            topic_ids={"TOPIC-0001"},
        )
    finally:
        phase37._ACTIVE_CONCEPT_DIRECTORY.reset(token)

    assert retiring["concept_id"] not in parsed
    assert any("same topology batch" in error for error in errors)


def test_retirement_can_target_a_non_retiring_concept_in_the_same_batch():
    retiring = _concept("TOPOLOGY-CONCEPT-0001", "Unsupported visual inference")
    survivor = _concept("TOPOLOGY-CONCEPT-0002", "Existing censorship concept")
    directory = {
        retiring["concept_id"]: retiring,
        survivor["concept_id"]: survivor,
    }
    token = phase37._ACTIVE_CONCEPT_DIRECTORY.set(directory)
    try:
        parsed, errors = phase32._parse_decisions(
            {
                "concepts": [
                    {
                        "concept_id": retiring["concept_id"],
                        "decision": "retire",
                        "segments": [],
                        "retire_into_concept_id": survivor["concept_id"],
                        "confidence": 0.99,
                        "reason": "Every supported part is fully subsumed.",
                    },
                    _keep_row(survivor),
                ]
            },
            concepts=directory,
            topic_ids={"TOPIC-0001"},
        )
    finally:
        phase37._ACTIVE_CONCEPT_DIRECTORY.reset(token)

    assert errors == []
    assert parsed[retiring["concept_id"]]["decision"] == "retire"
    assert parsed[survivor["concept_id"]]["decision"] == "keep"


def test_apply_pass_clears_stale_provisional_retirement_audit():
    phase37._PENDING_RETIREMENT_AUDIT.set({"retired_count": 1})
    concept = _concept("TOPOLOGY-CONCEPT-0001", "Supported concept")
    record = {
        "topic": "Topic",
        "_semantic_topic_id": "TOPIC-0001",
        "parent_concept": concept["parent_concept"],
        "concept_title": concept["concept_title"],
        "concept_details": (
            "Description: Source claim for Supported concept.\n"
            "Achieving Mastery: The learner explains Supported concept."
        ),
        "keywords": "",
    }
    phase32._apply_decisions(
        [record],
        concepts=[concept],
        index_by_id={concept["concept_id"]: 0},
        decisions={
            concept["concept_id"]: {
                "concept_id": concept["concept_id"],
                "decision": "keep",
                "segments": [],
                "confidence": 0.99,
                "reason": "Supported.",
            }
        },
        graph={
            "source_contract_hash": "hash",
            "topics": [
                {
                    "topic_id": "TOPIC-0001",
                    "title": "Topic",
                    "order": 1,
                }
            ],
        },
    )

    assert phase37._PENDING_RETIREMENT_AUDIT.get() is None

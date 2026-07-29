"""Regression coverage for topic-scoped Phase 3.3 new-host keys."""
from __future__ import annotations

from app.services import canonical_source_phase333_multitopic_host_contract as phase333


def test_identical_local_new_host_keys_do_not_collide_across_topics():
    records = [
        {
            "topic": "Topic One",
            "parent_concept": "P1",
            "concept_title": "Existing One",
            "concept_details": "Description: Existing one.",
            "_semantic_topic_id": "TOPIC-0001",
        },
        {
            "topic": "Topic Two",
            "parent_concept": "P2",
            "concept_title": "Existing Two",
            "concept_details": "Description: Existing two.",
            "_semantic_topic_id": "TOPIC-0002",
        },
    ]
    concept_payload = [
        {
            "concept_id": "HOST-CONCEPT-0001",
            "topic_id": "TOPIC-0001",
            "topic": "Topic One",
            "parent_concept": "P1",
            "concept_title": "Existing One",
        },
        {
            "concept_id": "HOST-CONCEPT-0002",
            "topic_id": "TOPIC-0002",
            "topic": "Topic Two",
            "parent_concept": "P2",
            "concept_title": "Existing Two",
        },
    ]
    units = [
        {
            "_phase33_assignment_unit_id": "UNIT-0001",
            "_semantic_topic_id": "TOPIC-0001",
            "source_question_ids": ["QINV-0001"],
        },
        {
            "_phase33_assignment_unit_id": "UNIT-0002",
            "_semantic_topic_id": "TOPIC-0002",
            "source_question_ids": ["QINV-0002"],
        },
    ]
    plans = [
        {
            "assignments": [
                {
                    "assignment_unit_id": "UNIT-0001",
                    "decision": "create_new",
                    "existing_concept_id": "NONE",
                    "new_concept_key": "NEW-HOST-0001",
                    "confidence": 0.999,
                }
            ],
            "new_concepts": [
                {
                    "new_concept_key": "NEW-HOST-0001",
                    "topic_id": "TOPIC-0001",
                    "concept_title": "New Host One",
                    "parent_concept": "P1",
                    "description": "Source-supported concept one.",
                    "achieving_mastery": "Explain concept one.",
                    "keywords": ["one"],
                    "source_block_ids": ["BLK-0001"],
                    "assignment_unit_ids": ["UNIT-0001"],
                    "confidence": 0.999,
                }
            ],
        },
        {
            "assignments": [
                {
                    "assignment_unit_id": "UNIT-0002",
                    "decision": "create_new",
                    "existing_concept_id": "NONE",
                    "new_concept_key": "NEW-HOST-0001",
                    "confidence": 0.998,
                }
            ],
            "new_concepts": [
                {
                    "new_concept_key": "NEW-HOST-0001",
                    "topic_id": "TOPIC-0002",
                    "concept_title": "New Host Two",
                    "parent_concept": "P2",
                    "description": "Source-supported concept two.",
                    "achieving_mastery": "Explain concept two.",
                    "keywords": ["two"],
                    "source_block_ids": ["BLK-0002"],
                    "assignment_unit_ids": ["UNIT-0002"],
                    "confidence": 0.998,
                }
            ],
        },
    ]

    out, host_map, qid_map, created = phase333._apply_host_plan(
        records,
        plans=plans,
        concept_payload=concept_payload,
        concept_index={"HOST-CONCEPT-0001": 0, "HOST-CONCEPT-0002": 1},
        topic_by_id={
            "TOPIC-0001": {"topic_id": "TOPIC-0001", "title": "Topic One"},
            "TOPIC-0002": {"topic_id": "TOPIC-0002", "title": "Topic Two"},
        },
        graph={"source_contract_hash": "SOURCE-1"},
        subtopic_by_block={"BLK-0001": "SUB-1", "BLK-0002": "SUB-2"},
        units=units,
    )

    assert created == 2
    assert {row["concept_title"] for row in out} == {
        "Existing One",
        "Existing Two",
        "New Host One",
        "New Host Two",
    }
    assert host_map["UNIT-0001"]["concept_title"] == "New Host One"
    assert host_map["UNIT-0002"]["concept_title"] == "New Host Two"
    assert qid_map["QINV-0001"]["topic_id"] == "TOPIC-0001"
    assert qid_map["QINV-0002"]["topic_id"] == "TOPIC-0002"

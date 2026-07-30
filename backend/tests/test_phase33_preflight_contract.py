"""Regression coverage for Phase 3.3 convergence and Type-host preflight."""
from __future__ import annotations

import copy

from app.services import canonical_source_phase3 as phase3
from app.services import canonical_source_phase33_preflight_contract as phase33
from app.services import generation as g


def _verified_review(ids: list[str]) -> dict:
    return {
        "verdict": "verified",
        "confidence": 0.999,
        "accepted_concept_ids": ids,
        "rejected_concept_ids": [],
        "issues": [],
    }


def test_production_keep_and_move_projection_preserves_source_claim():
    concepts = [
        {
            "concept_id": "TOPOLOGY-CONCEPT-0001",
            "current_topic_id": "TOPIC-0001",
            "concept_title": "Nation-State as Shared Political Identity",
            "parent_concept": "Idea of the Nation",
            "source_claim": "Citizens develop a shared political identity.",
            "existing_mastery": "Explain how shared identity distinguishes a nation-state.",
        },
        {
            "concept_id": "TOPOLOGY-CONCEPT-0002",
            "current_topic_id": "TOPIC-0001",
            "concept_title": "Political Fragmentation in Dynastic Europe",
            "parent_concept": "Dynastic Europe",
            "source_claim": "Germany, Italy and Switzerland were politically fragmented.",
            "existing_mastery": "Explain why fragmented territories were not nation-states.",
        },
    ]
    response = {
        "concepts": [
            {
                "concept_id": "TOPOLOGY-CONCEPT-0001",
                "decision": "keep",
                "segments": [
                    {
                        "topic_id": "TOPIC-0002",
                        "concept_title": "Paraphrased title",
                        "parent_concept": "Changed parent",
                        "description": "Paraphrased claim",
                        "achieving_mastery": "Changed mastery",
                        "keywords": [],
                        "confidence": 0.999,
                        "reason": "Keep",
                    }
                ],
                "confidence": 0.999,
                "reason": "Keep",
            },
            {
                "concept_id": "TOPOLOGY-CONCEPT-0002",
                "decision": "move",
                "segments": [
                    {
                        "topic_id": "TOPIC-0002",
                        "concept_title": "Rewritten title",
                        "parent_concept": "Europe Before Nation-States",
                        "description": "Rewritten claim",
                        "achieving_mastery": "Rewritten mastery",
                        "keywords": [],
                        "confidence": 0.999,
                        "reason": "Move",
                    }
                ],
                "confidence": 0.999,
                "reason": "Move",
            },
        ]
    }

    projected = phase33._project_stable_topology_decisions(
        response,
        concepts=concepts,
    )

    kept = projected["concepts"][0]["segments"][0]
    assert kept["topic_id"] == "TOPIC-0001"
    assert kept["concept_title"] == concepts[0]["concept_title"]
    assert kept["parent_concept"] == concepts[0]["parent_concept"]
    assert kept["description"] == concepts[0]["source_claim"]
    assert kept["achieving_mastery"] == concepts[0]["existing_mastery"]

    moved = projected["concepts"][1]["segments"][0]
    assert moved["topic_id"] == "TOPIC-0002"
    assert moved["concept_title"] == concepts[1]["concept_title"]
    assert moved["description"] == concepts[1]["source_claim"]
    assert moved["achieving_mastery"] == concepts[1]["existing_mastery"]
    assert moved["parent_concept"] == "Europe Before Nation-States"


def test_verified_phase32_decision_is_reused_per_concept(tmp_path):
    graph = {"source_contract_hash": "SOURCE-1"}
    session = {"artifact_dir": tmp_path}
    concept = {
        "concept_id": "TOPOLOGY-CONCEPT-0001",
        "current_topic_id": "TOPIC-0001",
        "concept_title": "Nation-State",
        "parent_concept": "Idea of the Nation",
        "source_claim": "Citizens develop a common identity.",
        "existing_mastery": "Explain common identity.",
    }
    provider_payload = {
        "topics": [
            {
                "topic_id": "TOPIC-0001",
                "title": "The French Revolution and the Idea of the Nation",
                "evidence": "Canonical source evidence",
            }
        ],
        "concepts": [concept],
    }
    calls = {"provider": 0, "critic": 0}

    def provider(_payload):
        calls["provider"] += 1
        return {
            "concepts": [
                {
                    "concept_id": concept["concept_id"],
                    "decision": "keep",
                    "segments": [
                        {
                            "topic_id": "TOPIC-0001",
                            "concept_title": "A paraphrase",
                            "parent_concept": "Another parent",
                            "description": "A paraphrase",
                            "achieving_mastery": "Another mastery",
                            "keywords": [],
                            "confidence": 0.999,
                            "reason": "Keep",
                        }
                    ],
                    "confidence": 0.999,
                    "reason": "Keep",
                }
            ]
        }

    def critic(payload):
        calls["critic"] += 1
        return _verified_review([row["concept_id"] for row in payload["concepts"]])

    with phase3.activate_session(session), phase3.activate(graph):
        first = phase33._phase32_provider_with_cache(provider, provider_payload)
        review_payload = {
            "topics": copy.deepcopy(provider_payload["topics"]),
            "concepts": [concept],
            "proposed_decisions": copy.deepcopy(first["concepts"]),
        }
        phase33._phase32_critic_with_cache(critic, review_payload)
        second = phase33._phase32_provider_with_cache(
            lambda _payload: (_ for _ in ()).throw(
                AssertionError("provider must not run on verified cache hit")
            ),
            provider_payload,
        )
        second_review = phase33._phase32_critic_with_cache(
            lambda _payload: (_ for _ in ()).throw(
                AssertionError("critic must not run on verified cache hit")
            ),
            {
                **review_payload,
                "proposed_decisions": copy.deepcopy(second["concepts"]),
            },
        )

    assert calls == {"provider": 1, "critic": 1}
    assert first == second
    assert second_review["verdict"] == "verified"
    assert second_review["confidence"] == 1.0
    assert (
        first["concepts"][0]["segments"][0]["description"]
        == concept["source_claim"]
    )
    assert (tmp_path / phase33._DECISION_CACHE_FILENAME).exists()


def _host_graph() -> tuple[dict, dict]:
    graph = {
        "source_contract_hash": "HOST-SOURCE-1",
        "metadata": {"subject": "History", "grade": "10"},
        "topics": [
            {
                "topic_id": "TOPIC-0001",
                "order": 1,
                "title": "The Making of Nationalism in Europe",
            }
        ],
        "blocks": [
            {
                "block_id": "BLK-0001",
                "topic_id": "TOPIC-0001",
                "subtopic_id": "SUB-0001",
                "kind": "paragraph",
            },
            {
                "block_id": "BLK-0002",
                "topic_id": "TOPIC-0001",
                "subtopic_id": "SUB-0002",
                "kind": "paragraph",
            },
        ],
        "tasks": [
            {
                "task_id": "TASK-0001",
                "qid": "QINV-0001",
                "topic_id": "TOPIC-0001",
                "subtopic_id": "SUB-0002",
            }
        ],
    }
    canonical = {
        "blocks": [
            {
                "block_id": "BLK-0001",
                "order": 1,
                "kind": "paragraph",
                "display_text": "Citizens developed a common national identity.",
            },
            {
                "block_id": "BLK-0002",
                "order": 2,
                "kind": "paragraph",
                "display_text": (
                    "The Zollverein removed tariff barriers and supported economic "
                    "integration among German states."
                ),
            },
        ],
        "tasks": [
            {
                "task_id": "TASK-0001",
                "qid": "QINV-0001",
                "order": 1,
            }
        ],
    }
    return graph, canonical


def _host_records() -> list[dict]:
    return [
        {
            "topic": "The Making of Nationalism in Europe",
            "parent_concept": "National Identity",
            "concept_title": "Shared National Identity",
            "concept_details": (
                "Description: Citizens developed a common national identity.\n"
                "Achieving Mastery: Explain how common identity supported nationalism. "
                "// Misconception/ Error Analysis: Misconceptions: Students may "
                "believe identity depended only on a ruler.; Error Analysis: "
                "Students may omit the role of citizens when explaining identity."
            ),
            "keywords": "identity",
            "_semantic_topic_id": "TOPIC-0001",
            "_semantic_graph_contract": "HOST-SOURCE-1",
            "_source_block_ids": ["BLK-0001"],
            "_source_grounding_contract": "api-verified-source-block-ids",
        },
        {
            "topic": "The Making of Nationalism in Europe",
            "parent_concept": "Culmination",
            "concept_title": "Culmination: Making Nationalism in Europe",
            "concept_details": "Description: Recap of Shared National Identity.",
            "keywords": "",
            "_semantic_topic_id": "TOPIC-0001",
            "_semantic_graph_contract": "HOST-SOURCE-1",
        },
    ]


def _mined_type() -> dict:
    return {
        "types": [
            {
                "type_id": "TYPE-0001",
                "type_title": "Explaining economic integration through tariff removal",
                "type_description": "Connect tariff removal with German economic integration.",
                "task_pattern": "Explain how tariff policy supported integration.",
                "topic_match_hint": "The Making of Nationalism in Europe",
                "placement_scope": "normal",
                "source_question_ids": ["QINV-0001"],
                "case_prompts": [
                    {
                        "case_id": "CASE-0001",
                        "case_title": "Tariff barriers and integration",
                        "placement_scope": "normal",
                        "examples": [
                            {
                                "source_question_id": "QINV-0001",
                                "example_prompt": (
                                    "Explain how removal of tariff barriers supported "
                                    "economic integration among German states."
                                ),
                            }
                        ],
                    }
                ],
            }
        ]
    }


def test_populated_topic_can_gain_one_necessary_type_host(monkeypatch):
    graph, canonical = _host_graph()
    records = _host_records()
    mined = _mined_type()
    inventory = {
        "items": [
            {
                "qid": "QINV-0001",
                "topic_hint": "The Making of Nationalism in Europe",
                "source_kind": "short_answer",
                "raw_task": (
                    "Explain how removal of tariff barriers supported economic "
                    "integration among German states."
                ),
            }
        ]
    }

    def provider(payload):
        unit_id = payload["assignment_units"][0]["assignment_unit_id"]
        return {
            "assignments": [
                {
                    "assignment_unit_id": unit_id,
                    "decision": "create_new",
                    "existing_concept_id": "NONE",
                    "new_concept_key": "NEW-HOST-0001",
                    "confidence": 0.999,
                    "reason": "No existing concept teaches tariff-led integration.",
                }
            ],
            "new_concepts": [
                {
                    "new_concept_key": "NEW-HOST-0001",
                    "topic_id": "TOPIC-0001",
                    "concept_title": "Economic Integration through the Zollverein",
                    "parent_concept": "Economic Foundations of Nationalism",
                    "description": (
                        "The Zollverein removed tariff barriers and promoted "
                        "economic integration among German states."
                    ),
                    "achieving_mastery": (
                        "Explain the link between tariff removal and economic "
                        "integration among German states."
                    ),
                    "keywords": ["Zollverein", "tariff barriers"],
                    "source_block_ids": ["BLK-0002"],
                    "assignment_unit_ids": [unit_id],
                    "confidence": 0.999,
                    "reason": "BLK-0002 supports a distinct durable concept.",
                }
            ],
        }

    def critic(payload):
        ids = [
            row["assignment_unit_id"]
            for row in payload["assignment_units"]
        ]
        return _verified_review(ids)

    def learner_analysis(rows, **_kwargs):
        for row in rows:
            if row.get("_phase33_new_type_host"):
                row["concept_details"] += (
                    " // Misconception/ Error Analysis: Misconceptions: Students "
                    "may believe tariff removal had no political significance.; "
                    "Error Analysis: Students may omit the connection between "
                    "lower trade barriers and integration."
                )
        return rows

    monkeypatch.setattr(g, "_ensure_misconceptions_via_api", learner_analysis)
    monkeypatch.setattr(g, "_validate_final_or_raise", lambda *_args, **_kwargs: {})

    with phase3.activate_session({"canonical": canonical}), phase3.activate(graph):
        reconciled = phase33._reconcile_type_hosts(
            g,
            records,
            mined_types=mined,
            question_task_inventory=inventory,
            meta={"subject": "History", "grade": "10"},
            mmd_text="canonical source",
            method_anchors=[],
            graph=graph,
            canonical=canonical,
            provider=provider,
            critic=critic,
        )

    titles = [row["concept_title"] for row in reconciled]
    assert "Economic Integration through the Zollverein" in titles
    assert titles.index("Economic Integration through the Zollverein") < titles.index(
        "Culmination: Making Nationalism in Europe"
    )
    host_map = mined["_phase33_verified_host_map"]
    assert len(host_map) == 1
    destination = next(iter(host_map.values()))
    assert destination["concept_title"] == "Economic Integration through the Zollverein"
    assert mined["_phase33_type_host_contract"]["state"] == (
        "verified_before_topology_freeze"
    )

    expanded = g._expand_mined_types_to_assignment_units(mined["types"])
    assert expanded[0]["concept_match_hint"] == (
        "Economic Integration through the Zollverein"
    )
    assert expanded[0]["_phase33_host_verified"] is True


def test_low_confidence_cannot_create_type_host():
    units = [
        {
            "assignment_unit_id": "TYPE-0001",
            "topic_id": "TOPIC-0001",
        }
    ]
    source_blocks = [
        {
            "block_id": "BLK-0001",
            "topic_id": "TOPIC-0001",
            "text": "Source evidence",
        }
    ]
    response = {
        "assignments": [
            {
                "assignment_unit_id": "TYPE-0001",
                "decision": "create_new",
                "existing_concept_id": "NONE",
                "new_concept_key": "NEW-HOST-0001",
                "confidence": 0.72,
                "reason": "Low confidence",
            }
        ],
        "new_concepts": [],
    }

    plan, errors = phase33._parse_host_plan(
        response,
        unit_payloads=units,
        existing_concepts=[],
        source_blocks=source_blocks,
    )

    assert plan is None
    assert any("host confidence 0.720 is below 0.920" in error for error in errors)

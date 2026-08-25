"""Regressions for keeping Host inside the authoritative literary map."""
from __future__ import annotations

import json

from app.services import postlearning_formation_contract as post
from app.services import postlearning_host_contract as post_host
from app.services.phase3 import envelope, host, kernel


def _concept(concept_id: str, title: str, role: str, block_id: str, qids=None):
    return {
        "plan_concept_id": concept_id,
        "display_name": title,
        "semantic_role": role,
        "facets": [title.casefold()],
        "source_block_ids": [block_id],
        "task_qids": list(qids or []),
        "achieving_mastery": f"Explain {title.casefold()} with evidence.",
        "rationale": f"{title} is one planned literary teaching unit.",
    }


def _env():
    plan = {
        "topics": [
            {
                "plan_topic_id": "PT-1",
                "display_name": "Stanza 1 - A new beginning",
                "evidence_block_ids": ["BLK-1"],
                "concepts": [
                    _concept(
                        "PC-1", "A shared new-school beginning", "ordinary",
                        "BLK-1", ["Q-1"],
                    ),
                    _concept(
                        "PC-2", "The stanza as one invitation",
                        "stanza_culmination", "BLK-1",
                    ),
                ],
            },
            {
                "plan_topic_id": "PT-2",
                "display_name": "Detailed Analysis of 'A Small New Start'",
                "evidence_block_ids": ["BLK-2"],
                "concepts": [
                    _concept(
                        "PC-3", "Language & Literary Devices",
                        "detailed_analysis", "BLK-2", ["Q-2"],
                    ),
                    _concept(
                        "PC-4", "The poem as a whole",
                        "chapter_culmination", "BLK-2",
                    ),
                ],
            },
        ],
        "threaded_components": [],
        "non_teaching_block_ids": [],
        "notes": "",
    }
    graph = {
        "source_contract_hash": "host-source-contract",
        "metadata": {"language_topology_plan": json.dumps(plan)},
        "topics": [
            {
                "topic_id": "TOPIC-0001", "plan_topic_id": "PT-1",
                "title": "Stanza 1 - A new beginning",
            },
            {
                "topic_id": "TOPIC-0002", "plan_topic_id": "PT-2",
                "title": "Detailed Analysis of 'A Small New Start'",
            },
        ],
        "subtopics": [],
        "blocks": [
            {"block_id": "BLK-1", "topic_id": "TOPIC-0001", "kind": "paragraph"},
            {"block_id": "BLK-2", "topic_id": "TOPIC-0002", "kind": "paragraph"},
        ],
    }
    raw_build = getattr(
        envelope.build,
        "_aegis_postlearning_support_original",
        envelope.build,
    )
    return post.materialize_envelope(raw_build(
        graph=graph,
        canonical={"blocks": [
            {"block_id": "BLK-1", "display_text": "A new beginning."},
            {"block_id": "BLK-2", "display_text": "A sound device."},
        ]},
        skeleton_rows=[{
            "topic": "Stanza 1 - A new beginning",
            "parent_concept": "Warm-up",
            "concept_title": "A generic activity concept",
            "concept_details": "Description: Generic.",
            "keywords": "generic",
            "_semantic_topic_id": "TOPIC-0001",
        }],
        inventory={"items": [
            {"qid": "Q-1", "normalized_task": "Write a school goal."},
            {"qid": "Q-2", "normalized_task": "Find alliteration."},
        ]},
        mined_types={"types": [{
            "type_id": "TYPE-1",
            "type_title": "Writing a personal school response",
            "type_description": "Write a short personal response.",
            "case_prompts": [{
                "case_id": "CASE-1",
                "case_title": "Write one school goal",
                "case_signature": "personal response",
                "_semantic_topic_id": "TOPIC-0001",
                "examples": [{
                    "source_question_id": "Q-1",
                    "example_prompt": "Write a school goal.",
                }],
            }],
        }]},
        metadata={
            "grade": "06", "subject": "English", "unit": "POEM",
            "language_topology_plan": json.dumps(plan),
        },
    ))


def _settled(env):
    rows = []
    normal = 0
    for entry in post.plan_entries(env):
        row = {
            "topic": entry["topic_title"],
            "parent_concept": (
                "Culmination" if entry["culmination"] else entry["topic_title"]
            ),
            "concept_title": entry["wire_title"],
            "concept_details": (
                "Description: A complete teaching paragraph.\n"
                f"Achieving Mastery: {entry['achieving_mastery']}"
            ),
            "keywords": ", ".join(entry["facets"]),
            "_semantic_topic_id": entry["topic_id"],
            "_source_block_ids": list(entry["source_block_ids"]),
            "_source_grounding_contract": (
                "derived-from-verified-topic-concepts"
                if entry["culmination"]
                else "api-verified-source-block-ids"
            ),
        }
        if not entry["culmination"]:
            normal += 1
            row["_phase32_origin_concept_id"] = (
                f"TOPOLOGY-CONCEPT-{normal:04d}"
            )
        rows.append(post._stamp_row(row, entry))
    return rows


def _host_result():
    created = {
        "topic": "Stanza 1 - A new beginning",
        "parent_concept": "Personal response writing",
        "concept_title": "Writing School Goals in a Short Personal Response",
        "concept_details": "Description: A response method, not poem teaching.",
        "keywords": "personal response",
        "_semantic_topic_id": "TOPIC-0001",
        "_source_block_ids": ["BLK-1"],
        "_source_grounding_contract": "api-created-missing-type-host",
    }
    destination = {
        "decision": "create_new",
        "concept_title": created["concept_title"],
        "parent_concept": created["parent_concept"],
        "topic": created["topic"],
        "topic_id": created["_semantic_topic_id"],
        "confidence": 0.97,
    }
    return {
        "host_map": {"TYPE-1::CASE-1::0001": dict(destination)},
        "qid_map": {"Q-1": {**destination, "decision": "api_placement"}},
        "new_concepts": [created],
    }


def test_no_created_concept_costs_no_reconciliation_call():
    env = _env()
    result = {"host_map": {}, "qid_map": {}, "new_concepts": []}
    calls = {"n": 0}

    def forbidden(_request):
        calls["n"] += 1
        raise AssertionError("no temporary host means no decision")

    assert post_host.reconcile(
        env, _settled(env), result,
        provider=forbidden, store=kernel.DecisionStore(), allow_live=False,
    ) == result
    assert calls["n"] == 0


def test_created_host_is_reassigned_once_and_replays_for_free():
    env = _env()
    calls = {"n": 0}

    def provider(request):
        calls["n"] += 1
        assert request["created_concepts"][0]["units"][0]["qids"] == ["Q-1"]
        return {"reassignments": [{
            "created_concept_id": "HOST-CREATED-0001",
            "destination_plan_concept_id": "PC-1",
            "rationale": (
                "The personal response activates the stanza's new-beginning "
                "idea; the response format is not a new literary concept."
            ),
        }]}

    def critic(_request):
        return {"verdict": "verified", "confidence": 0.999, "issues": []}

    store = kernel.DecisionStore()
    first = post_host.reconcile(
        env, _settled(env), _host_result(),
        provider=provider, critic=critic, store=store, allow_live=False,
    )
    second = post_host.reconcile(
        env, _settled(env), _host_result(),
        provider=provider, critic=critic, store=store, allow_live=False,
    )

    assert calls["n"] == 1
    assert second == first
    assert first["new_concepts"] == []
    host_entry = first["host_map"]["TYPE-1::CASE-1::0001"]
    qid_entry = first["qid_map"]["Q-1"]
    assert host_entry["concept_title"] == "A shared new-school beginning"
    assert qid_entry["concept_title"] == "A shared new-school beginning"
    assert host_entry["decision"] == (
        "existing_after_language_plan_reconciliation"
    )
    assert any(
        "response format is not a new literary concept" in flag
        for flag in host_entry["review_flags"]
    )


def test_install_wraps_host_once():
    post_host.install()
    first = host.host
    post_host.install()
    assert host.host is first
    assert getattr(host.host, "_aegis_postlearning_host", False)

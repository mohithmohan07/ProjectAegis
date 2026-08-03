"""Installed Type/Case ownership-to-deposit certificate regression."""
from __future__ import annotations

import copy
import json

import pytest

from app import models
from app.services import build_concepts
from app.services import canonical_source_phase3 as phase3
from app.services import canonical_source_phase31_grounding_contract as phase31
from app.services import canonical_source_phase33_preflight_contract as phase33
from app.services import concept_topology_contract as topology
from app.services import generation as g
from app.services import grounding_certificate
from app.services import placement_policy


QID = "QINV-0001"
SOURCE_CONTRACT = "c" * 64


def _source() -> tuple[dict, dict]:
    graph = {
        "source_contract_hash": SOURCE_CONTRACT,
        "metadata": {"subject": "Mathematics"},
        "topics": [
            {"topic_id": "TOPIC-A", "order": 1, "title": "Foundations"},
            {"topic_id": "TOPIC-B", "order": 2, "title": "Later Method"},
        ],
        "subtopics": [],
        "blocks": [
            {
                "block_id": "BLK-A",
                "topic_id": "TOPIC-A",
                "subtopic_id": "",
                "kind": "paragraph",
                "order": 1,
                "text": "The source introduces the foundational operation.",
            },
            {
                "block_id": "BLK-B",
                "topic_id": "TOPIC-B",
                "subtopic_id": "",
                "kind": "paragraph",
                "order": 2,
                "text": (
                    "The later method is required to perform the cumulative "
                    "operation in the source task."
                ),
            },
        ],
        "tasks": [{
            "task_id": "TASK-0001",
            "qid": QID,
            "topic_id": "TOPIC-A",
            "subtopic_id": "",
        }],
    }
    canonical = {"blocks": [
        {
            "block_id": "BLK-A",
            "order": 1,
            "kind": "paragraph",
            "display_text": "The source introduces the foundational operation.",
        },
        {
            "block_id": "BLK-B",
            "order": 2,
            "kind": "paragraph",
            "display_text": (
                "The later method is required to perform the cumulative "
                "operation in the source task."
            ),
        },
    ]}
    return graph, canonical


def _row_placement(record: dict, graph: dict) -> dict:
    order = placement_policy.seal_teaching_order(
        graph, source_contract_hash=SOURCE_CONTRACT
    )
    claim_id = "TOPOLOGY-CONCEPT-B#1"
    relation = placement_policy.TopicRelationship(
        claim_id=claim_id,
        topic_id="TOPIC-B",
        relationship_type=placement_policy.RelationshipType.CORE_TEACHING,
        necessity=True,
        evidence_block_ids=("BLK-B",),
        provider_reason="The later block directly teaches this concept.",
        critic_verdict="accepted",
    )
    value = {
        "certified": True,
        "policy_version": placement_policy.POLICY_VERSION,
        "teaching_order_sha256": order.sha256,
        "claim_id": claim_id,
        "normalized_claim": grounding_certificate.source_claim(record),
        "origin_claim_sha256": placement_policy.claim_text_sha256(
            grounding_certificate.source_claim(record)
        ),
        "source_location_topic_id": "TOPIC-B",
        "owner_topic_id": "TOPIC-B",
        "required_topic_ids": ["TOPIC-B"],
        "prerequisite_topic_ids": [],
        "reference_edges": [],
        "illustration_topic_ids": [],
        "topic_relationships": [
            placement_policy.relationship_audit_row(relation)
        ],
        "origin_claim_ids": ["TOPOLOGY-CONCEPT-B"],
        "split_group_id": "",
        "protected_source_items": [],
    }
    value["placement_certificate_sha256"] = (
        placement_policy.placement_contract_sha256(value)
    )
    return value


def _placement_provider(payload: dict) -> dict:
    claim_id = payload["claims"][0]["claim_id"]
    return {"relationships": [
        {
            "claim_id": claim_id,
            "topic_id": "TOPIC-A",
            "relationship_type": "CORE_TEACHING",
            "necessity": True,
            "evidence_block_ids": ["BLK-A"],
            "reason": "The physical source introduces the operation.",
        },
        {
            "claim_id": claim_id,
            "topic_id": "TOPIC-B",
            "relationship_type": "REQUIRED_LATER_METHOD",
            "necessity": True,
            "evidence_block_ids": ["BLK-B"],
            "reason": "The later method is necessary to perform the task.",
        },
    ]}


def _relationship_critic(payload: dict) -> dict:
    return {
        "verdict": "verified",
        "confidence": 0.999,
        "issues": [],
        "relationship_reviews": [{
            "relationship_id": relation["relationship_id"],
            "verdict": "accepted",
            "evidence_supported": True,
            "necessity_supported": True,
            "direction_supported": True,
            "reason": "The exact block supports the stated relationship.",
        } for relation in payload["proposed_relationships"]],
    }


def _grounding_provider(payload: dict) -> dict:
    assert payload["topic"]["topic_id"] == "TOPIC-B"
    return {"concepts": [{
        "concept_id": concept["concept_id"],
        "source_block_ids": ["BLK-B"],
        "confidence": 0.999,
        "reason": "BLK-B exactly supports the concept claim.",
    } for concept in payload["concepts"]]}


def _grounding_critic(payload: dict) -> dict:
    return {
        "verdict": "verified",
        "confidence": 0.999,
        "accepted_concept_ids": [
            concept["concept_id"] for concept in payload["concepts"]
        ],
        "rejected_concept_ids": [],
        "issues": [],
    }


def _host_provider(payload: dict) -> dict:
    unit_id = payload["assignment_units"][0]["assignment_unit_id"]
    assert payload["topic"]["topic_id"] == "TOPIC-B"
    host_id = payload["existing_concepts"][0]["concept_id"]
    assert host_id == "HOST-CONCEPT-0001"
    return {
        "assignments": [{
            "assignment_unit_id": unit_id,
            "decision": "existing",
            "existing_concept_id": host_id,
            "new_concept_key": "NONE",
            "confidence": 0.999,
            "reason": "The existing later-method concept entails the task.",
        }],
        "new_concepts": [],
        "existing_concept_updates": [],
    }


def _host_critic(payload: dict) -> dict:
    return {
        "verdict": "verified",
        "confidence": 0.999,
        "accepted_concept_ids": [
            row["assignment_unit_id"] for row in payload["assignment_units"]
        ],
        "rejected_concept_ids": [],
        "issues": [],
        "relationship_reviews": [{
            "relationship_id": relation["relationship_id"],
            "verdict": "accepted",
            "evidence_supported": True,
            "necessity_supported": True,
            "direction_supported": True,
            "reason": "The host mutation remains exactly source-supported.",
        } for relation in payload.get("proposed_relationships") or []],
    }


def _earlier_owner_relationships(claim_id: str) -> list[dict]:
    relationships = (
        placement_policy.TopicRelationship(
            claim_id=claim_id,
            topic_id="TOPIC-A",
            relationship_type=placement_policy.RelationshipType.CORE_TEACHING,
            necessity=True,
            evidence_block_ids=("BLK-A",),
            provider_reason="The earlier topic directly introduces the operation.",
            critic_verdict="accepted",
        ),
        placement_policy.TopicRelationship(
            claim_id=claim_id,
            topic_id="TOPIC-B",
            relationship_type=(
                placement_policy.RelationshipType.REQUIRED_LATER_METHOD
            ),
            necessity=True,
            evidence_block_ids=("BLK-B",),
            provider_reason="The later method remains necessary for the task.",
            critic_verdict="accepted",
        ),
    )
    return [
        placement_policy.relationship_audit_row(relation)
        for relation in relationships
    ]


def _rehash_earlier_owner_contract(contract: dict, *, type_case: bool) -> dict:
    value = copy.deepcopy(contract)
    value["source_location_topic_id"] = "TOPIC-A"
    value["source_location_topic_ids"] = ["TOPIC-A"]
    value["owner_topic_id"] = "TOPIC-A"
    if type_case:
        value["owner_topic_title"] = "Foundations"
    value["required_topic_ids"] = ["TOPIC-A", "TOPIC-B"]
    value["prerequisite_topic_ids"] = []
    value["reference_edges"] = []
    value["illustration_topic_ids"] = []
    value["topic_relationships"] = _earlier_owner_relationships(
        str(value["claim_id"])
    )
    value["placement_certificate_sha256"] = (
        g._type_case_placement_digest(value)
        if type_case
        else placement_policy.placement_contract_sha256(value)
    )
    return value


def test_installed_allocate_chain_certifies_later_owner_through_final_deposit(
    monkeypatch,
    tmp_path,
    db,
):
    graph, canonical = _source()
    record = {
        "topic": "Later Method",
        "parent_concept": "Cumulative Operations",
        "concept_title": "Apply the Later Method",
        "concept_details": (
            "Description: The later method performs the cumulative operation.\n"
            "Achieving Mastery: Apply the later method and justify each step. "
            "// Misconception/ Error Analysis: Misconceptions: Students may "
            "believe the foundational operation alone completes the task.; "
            "Error Analysis: Students may omit the later-method step and "
            "therefore produce an incomplete operation."
        ),
        "keywords": "later method, cumulative operation",
        "_semantic_topic_id": "TOPIC-B",
        "_semantic_graph_contract": SOURCE_CONTRACT,
    }
    record["_placement_contract"] = _row_placement(record, graph)
    culmination = {
        "topic": "Later Method",
        "parent_concept": "Culmination",
        "concept_title": "Culmination - Later Method",
        "concept_details": (
            "Description: Recap of Apply the Later Method and its cumulative "
            "operation."
        ),
        "keywords": "culmination, later method",
        "_semantic_topic_id": "TOPIC-B",
        "_semantic_graph_contract": SOURCE_CONTRACT,
    }
    inventory = {"items": [{
        "qid": QID,
        "source_kind": "short_answer",
        "raw_task": "Use the later method to complete the cumulative operation.",
        "topic_hint": "Foundations",
        "source_location_topic_id": "TOPIC-A",
    }]}
    mined_types = {"types": [{
        "type_id": "TYPE-0001",
        "type_title": "Complete a cumulative operation",
        "type_description": "Apply the necessary later method.",
        "task_pattern": "Use the later method to complete the operation.",
        "topic_match_hint": "Foundations",
        "placement_scope": "normal",
        "source_question_ids": [QID],
        "case_prompts": [{
            "case_id": "CASE-0001",
            "case_title": "Later-method application",
            "case_definition": "The later method is necessary.",
            "placement_scope": "normal",
            "examples": [{
                "source_question_id": QID,
                "example_prompt": inventory["items"][0]["raw_task"],
            }],
        }],
    }]}

    monkeypatch.setattr(phase3, "semantic_api_enabled", lambda: True)
    monkeypatch.setattr(phase31, "_ground_via_openai", _grounding_provider)
    monkeypatch.setattr(phase31, "_critic_via_openai", _grounding_critic)
    monkeypatch.setattr(
        phase33, "_type_case_placement_via_openai", _placement_provider
    )
    monkeypatch.setattr(
        phase33, "_type_case_placement_critic_via_openai",
        _relationship_critic,
    )
    monkeypatch.setattr(phase33, "_host_plan_via_openai", _host_provider)
    monkeypatch.setattr(phase33, "_host_critic_via_openai", _host_critic)

    def fake_generation_transport(system: str, user: str, *, purpose: str, **_kw):
        assert purpose == "concept_mapping"
        payload = json.loads(user.split(
            "MINED TYPE ASSIGNMENT UNITS (every type_id MUST be assigned):\n",
            1,
        )[1])
        type_id = payload["types"][0]["type_id"]
        return {"assignments": [{
            "concept_id": "CONCEPT-0001",
            "type_ids": [type_id],
        }]}

    monkeypatch.setattr(g, "_openai_json", fake_generation_transport)
    monkeypatch.setattr(
        g,
        "_repair_records_via_api",
        lambda rows, **_kwargs: copy.deepcopy(rows),
    )
    monkeypatch.setattr(
        g,
        "_repair_final_rich_text_via_api",
        lambda rows, **_kwargs: (copy.deepcopy(rows), False),
    )

    original_mined_types = copy.deepcopy(mined_types)
    session = {"artifact_dir": tmp_path, "canonical": canonical}
    with phase3.activate_session(session), phase3.activate(graph):
        allocated = topology._allocate_after_freeze(
            g,
            [record, culmination],
            subject="Mathematics",
            mmd_text="canonical source",
            meta={"subject": "Mathematics", "grade": "10"},
            source_sections=[],
            question_task_inventory=inventory,
            mined_types=mined_types,
            method_anchors=[],
        )
        finalized, final_ledger = g._final_type_case_qid_host_manifests(
            allocated, inventory, mined_types
        )
        certificate = grounding_certificate.build_final_certificate(
            finalized,
            type_case_qid_placement_ledger=final_ledger,
            require_placement_contracts=True,
        )
        verified = grounding_certificate.verify_final_certificate(
            finalized,
            certificate,
            semantic_graph=graph,
            require_semantic_graph=True,
            require_placement_contracts=True,
            type_case_qid_placement_ledger=final_ledger,
        )

        chapter = models.Chapter(
            chapter_code="10CBSE_InstalledTypeCaseFinalChain",
            board="CBSE",
            grade="10",
            subject="Mathematics",
            unit="Algebra",
            chapter_title="Installed Type Case Final Chain",
            chapter_display_name="Installed Type Case Final Chain",
        )
        db.add(chapter)
        db.commit()
        db.refresh(chapter)
        deposited_certificate: dict = {}
        created, merged = build_concepts._deposit_concepts(
            db,
            chapter,
            finalized,
            "Post",
            "Installed Chain Source",
            inventory=inventory,
            mined_types=mined_types,
            source_text="",
            final_grounding_certificate=certificate,
            grounding_certificate_sink=deposited_certificate,
        )

        assert len(created) == 2
        assert merged == []
        deposited_normal = db.get(models.Concept, created[0])
        assert deposited_normal is not None
        assert deposited_normal.topic.topic_title == "Later Method"
        assert deposited_certificate["certificate"] == certificate

        # Rehash every local layer after declaring the earlier topic as owner,
        # while deliberately retaining the accepted REQUIRED_LATER_METHOD edge.
        # The envelope is self-consistent, but the active teaching order still
        # recomputes TOPIC-B and must stop this payload before any DB write.
        tampered_records = copy.deepcopy(finalized)
        tampered_normal = tampered_records[0]
        tampered_normal["topic"] = "Foundations"
        tampered_normal["_semantic_topic_id"] = "TOPIC-A"
        tampered_normal["_placement_contract"] = (
            _rehash_earlier_owner_contract(
                tampered_normal["_placement_contract"],
                type_case=False,
            )
        )
        child = _rehash_earlier_owner_contract(
            final_ledger["placements"][QID],
            type_case=True,
        )
        tampered_ledger = copy.deepcopy(final_ledger)
        tampered_ledger["placements"][QID] = copy.deepcopy(child)
        tampered_ledger.pop("certificate_sha256", None)
        tampered_ledger["certificate_sha256"] = phase3._sha256_json(
            tampered_ledger
        )
        tampered_inventory = copy.deepcopy(inventory)
        tampered_inventory["items"][0][
            "_type_case_placement_contract"
        ] = copy.deepcopy(child)
        tampered_inventory["items"][0]["owner_topic_id"] = "TOPIC-A"
        tampered_inventory["items"][0]["owner_topic_title"] = "Foundations"
        tampered_inventory[g._TYPE_CASE_QID_PLACEMENT_LEDGER_KEY] = (
            copy.deepcopy(tampered_ledger)
        )
        tampered_mined = {
            "types": g._annotate_mined_type_case_routes(
                copy.deepcopy(original_mined_types["types"]),
                tampered_inventory,
            ),
            g._TYPE_CASE_QID_PLACEMENT_LEDGER_KEY: copy.deepcopy(
                tampered_ledger
            ),
        }
        g._reset_placement_certifications(tampered_mined)
        g._certify_inventory_host(
            tampered_mined,
            QID,
            tampered_normal,
            basis="rehashed_tamper_fixture",
        )
        tampered_records, rebuilt_tampered_ledger = (
            g._final_type_case_qid_host_manifests(
                tampered_records,
                tampered_inventory,
                tampered_mined,
            )
        )
        grounding_certificate.seal_records(
            tampered_records,
            source_contract_hash=SOURCE_CONTRACT,
            semantic_topology_sha256=(
                grounding_certificate.semantic_topology_sha256(graph)
            ),
            allowed_block_ids={"BLK-A", "BLK-B"},
        )
        tampered_certificate = grounding_certificate.build_final_certificate(
            tampered_records,
            type_case_qid_placement_ledger=rebuilt_tampered_ledger,
            require_placement_contracts=True,
        )
        assert grounding_certificate.verify_certificate_envelope(
            tampered_certificate,
            type_case_qid_placement_ledger=rebuilt_tampered_ledger,
        ) == tampered_certificate
        with pytest.raises(
            build_concepts.DepositValidationError,
            match="active teaching order",
        ):
            build_concepts._deposit_concepts(
                db,
                chapter,
                tampered_records,
                "Post",
                "Tampered Source",
                inventory=tampered_inventory,
                mined_types=tampered_mined,
                source_text="",
                final_grounding_certificate=tampered_certificate,
                grounding_certificate_sink={},
            )

    placement = final_ledger["placements"][QID]
    assert placement["source_location_topic_id"] == "TOPIC-A"
    assert placement["owner_topic_id"] == "TOPIC-B"
    assert inventory["items"][0]["topic_hint"] == "Foundations"
    assert inventory["items"][0]["source_location_topic_id"] == "TOPIC-A"

    task_text = g._inventory_task_text(inventory["items"][0])
    exact_locations = g._rendered_inventory_example_locations(
        finalized, inventory["items"][0]
    )
    assert exact_locations == [0]
    assert finalized[0]["_semantic_topic_id"] == "TOPIC-B"
    assert task_text in finalized[0]["concept_details"]

    row_manifest = finalized[0][
        grounding_certificate.TYPE_CASE_HOST_MANIFEST_FIELD
    ]
    entry = row_manifest["placements"][QID]
    assert entry["owner_topic_id"] == entry["host_topic_id"] == "TOPIC-B"
    assert entry["placement_contract"] == placement
    assert entry["placement_certificate_sha256"] == placement[
        "placement_certificate_sha256"
    ]
    assert inventory[g._TYPE_CASE_QID_PLACEMENT_LEDGER_KEY] == final_ledger
    assert mined_types[g._TYPE_CASE_QID_PLACEMENT_LEDGER_KEY] == final_ledger
    assert verified == certificate
    assert grounding_certificate.verify_semantic_topology(
        certificate, graph
    ) == grounding_certificate.semantic_topology_sha256(graph)

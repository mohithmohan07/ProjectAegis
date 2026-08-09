"""P0 regressions for certified Phase 3.3 host mutations."""
from __future__ import annotations

import copy

import pytest

from app.services import canonical_source_phase3 as phase3
from app.services import canonical_source_phase31_grounding_contract as phase31
from app.services import canonical_source_phase32_topology_adjudication_contract as phase32
from app.services import canonical_source_phase33_preflight_contract as phase33
from app.services import generation as g
from app.services import grounding_certificate
from app.services import placement_policy


def _fixture() -> tuple[dict, dict, placement_policy.TeachingOrder, dict]:
    graph = {
        "source_contract_hash": "SOURCE-HOST-MUTATION-1",
        "metadata": {"subject": "Mathematics", "grade": "10"},
        "topics": [
            {"topic_id": "TOPIC-A", "title": "Foundations"},
            {"topic_id": "TOPIC-B", "title": "Cumulative Method"},
        ],
        "blocks": [
            {
                "block_id": "BLK-A",
                "topic_id": "TOPIC-A",
                "subtopic_id": "",
                "kind": "paragraph",
                "order": 1,
                "text": "The foundational quantity is introduced here.",
            },
            {
                "block_id": "BLK-B1",
                "topic_id": "TOPIC-B",
                "subtopic_id": "",
                "kind": "paragraph",
                "order": 2,
                "text": "The cumulative method uses the foundational quantity.",
            },
            {
                "block_id": "BLK-B2",
                "topic_id": "TOPIC-B",
                "subtopic_id": "",
                "kind": "paragraph",
                "order": 3,
                "text": "Apply the cumulative method to determine the result.",
            },
        ],
        "tasks": [
            {
                "task_id": "TASK-1",
                "qid": "QINV-0001",
                "topic_id": "TOPIC-A",
                "subtopic_id": "",
            },
            {
                "task_id": "TASK-2",
                "qid": "QINV-0002",
                "topic_id": "TOPIC-A",
                "subtopic_id": "",
            },
        ],
    }
    canonical = {
        "blocks": [
            {
                "block_id": "BLK-A",
                "display_text": "The foundational quantity is introduced here.",
            },
            {
                "block_id": "BLK-B1",
                "display_text": (
                    "The cumulative method uses the foundational quantity."
                ),
            },
            {
                "block_id": "BLK-B2",
                "display_text": (
                    "Apply the cumulative method to determine the result."
                ),
            },
        ]
    }
    order = placement_policy.seal_teaching_order(
        graph, source_contract_hash=graph["source_contract_hash"]
    )
    directory = phase32._exact_block_directory(graph, canonical)
    return graph, canonical, order, directory


def _type_case_contract(
    qid: str,
    *,
    order: placement_policy.TeachingOrder,
    owner: str = "TOPIC-B",
) -> dict:
    relationship = placement_policy.TopicRelationship(
        claim_id=f"TASK-{qid}",
        topic_id=owner,
        relationship_type=placement_policy.RelationshipType.CORE_TEACHING,
        necessity=True,
        evidence_block_ids=("BLK-B2",),
        provider_reason="The cumulative topic directly teaches the task.",
        critic_verdict="accepted",
    )
    value = {
        "version": 2,
        "certified": True,
        "policy_version": placement_policy.POLICY_VERSION,
        "teaching_order_sha256": order.sha256,
        "source_contract_hash": "SOURCE-HOST-MUTATION-1",
        "claim_id": f"TASK-{qid}",
        "qid": qid,
        "normalized_claim": f"Perform task {qid}.",
        "source_location_topic_id": "TOPIC-A",
        "source_location_topic_ids": ["TOPIC-A"],
        "owner_topic_id": owner,
        "owner_topic_title": "Cumulative Method",
        "required_topic_ids": [owner],
        "prerequisite_topic_ids": [],
        "reference_edges": [],
        "illustration_topic_ids": [],
        "topic_relationships": [
            placement_policy.relationship_audit_row(relationship)
        ],
    }
    value["placement_certificate_sha256"] = g._type_case_placement_digest(
        value
    )
    return value


def _placement_authority(
    qids: list[str],
) -> tuple[dict, dict, dict, placement_policy.TeachingOrder, dict]:
    graph, canonical, order, directory = _fixture()
    contracts = {
        qid: _type_case_contract(qid, order=order) for qid in qids
    }
    ledger = {
        "version": 2,
        "certified": True,
        "policy_version": placement_policy.POLICY_VERSION,
        "teaching_order_sha256": order.sha256,
        "source_contract_hash": graph["source_contract_hash"],
        "placements": copy.deepcopy(contracts),
    }
    ledger["certificate_sha256"] = phase3._sha256_json(ledger)
    inventory = {phase33._TYPE_CASE_QID_PLACEMENT_LEDGER_KEY: ledger}
    authority, validated = phase33._host_mutation_authority(
        g, graph=graph, order=order, inventory=inventory
    )
    return authority, validated, graph, order, directory


def _old_row(order: placement_policy.TeachingOrder) -> dict:
    claim = "The cumulative method uses the foundational quantity."
    relationship = placement_policy.TopicRelationship(
        claim_id="CONCEPT-CLAIM-OLD",
        topic_id="TOPIC-B",
        relationship_type=placement_policy.RelationshipType.CORE_TEACHING,
        necessity=True,
        evidence_block_ids=("BLK-B1",),
        provider_reason="The topic directly teaches the concept.",
        critic_verdict="accepted",
    )
    contract = {
        "certified": True,
        "policy_version": placement_policy.POLICY_VERSION,
        "teaching_order_sha256": order.sha256,
        "claim_id": "CONCEPT-CLAIM-OLD",
        "normalized_claim": claim,
        "origin_claim_sha256": placement_policy.claim_text_sha256(claim),
        "source_location_topic_id": "TOPIC-B",
        "owner_topic_id": "TOPIC-B",
        "required_topic_ids": ["TOPIC-B"],
        "prerequisite_topic_ids": [],
        "reference_edges": [],
        "illustration_topic_ids": [],
        "topic_relationships": [
            placement_policy.relationship_audit_row(relationship)
        ],
        "origin_claim_ids": ["ORIGIN-CONCEPT-1"],
        "split_group_id": "SPLIT-GROUP-1",
        "protected_source_items": ["QINV-OLD"],
    }
    contract["placement_certificate_sha256"] = (
        placement_policy.placement_contract_sha256(contract)
    )
    return {
        "topic": "Cumulative Method",
        "parent_concept": "Methods",
        "concept_title": "Cumulative Method",
        "concept_details": (
            "Description: " + claim
            + "\nAchieving Mastery: Explain the cumulative method."
        ),
        "keywords": "cumulative",
        "_semantic_topic_id": "TOPIC-B",
        "_semantic_graph_contract": "SOURCE-HOST-MUTATION-1",
        "_source_block_ids": ["BLK-B1"],
        "_source_grounding_contract": "api-verified-source-block-ids",
        "_source_grounding_version": "test-grounding-1",
        "_placement_contract": contract,
    }


def _source_blocks(directory: dict) -> list[dict]:
    return [copy.deepcopy(directory[key]) for key in sorted(directory)]


def _unit(qids: list[str]) -> dict:
    return {
        "assignment_unit_id": "UNIT-1",
        "topic_id": "TOPIC-B",
        "type_id": "TYPE-1",
        "type_title": "Use the cumulative method",
        "source_question_ids": qids,
        "source_tasks": [f"Perform task {qid}." for qid in qids],
    }


def _accepted_review(payload: dict) -> dict:
    return {
        "verdict": "verified",
        "confidence": 0.999,
        "accepted_concept_ids": ["UNIT-1"],
        "rejected_concept_ids": [],
        "issues": [],
        "relationship_reviews": [
            {
                "relationship_id": row["relationship_id"],
                "verdict": "accepted",
                "evidence_supported": True,
                "necessity_supported": True,
                "direction_supported": True,
                "reason": "Exact evidence and dependency direction are valid.",
            }
            for row in payload["proposed_relationships"]
        ],
    }


def _relationships(*, reverse: bool = False) -> list[dict]:
    rows = [
        {
            "topic_id": "TOPIC-A",
            "relationship_type": "REQUIRED_PREREQUISITE",
            "necessity": True,
            "evidence_block_ids": ["BLK-A"],
            "reason": "The foundational quantity is required.",
        },
        {
            "topic_id": "TOPIC-B",
            "relationship_type": "CORE_TEACHING",
            "necessity": True,
            "evidence_block_ids": ["BLK-B1", "BLK-B2"],
            "reason": "The cumulative topic teaches the complete method.",
        },
    ]
    return list(reversed(rows)) if reverse else rows


def _resolve_create(*, reverse: bool = False, critic=_accepted_review):
    authority, qid_contracts, graph, order, directory = _placement_authority(
        ["QINV-0001", "QINV-0002"]
    )
    units = [_unit(["QINV-0002", "QINV-0001"])]

    def provider(_payload: dict) -> dict:
        return {
            "assignments": [
                {
                    "assignment_unit_id": "UNIT-1",
                    "decision": "create_new",
                    "existing_concept_id": "NONE",
                    "new_concept_key": "NEW-HOST-0001",
                    "confidence": 0.999,
                    "reason": "No current host teaches the complete method.",
                }
            ],
            "new_concepts": [
                {
                    "new_concept_key": "NEW-HOST-0001",
                    "topic_id": "TOPIC-B",
                    "concept_title": "Applying the Cumulative Method",
                    "parent_concept": "Methods",
                    "description": (
                        "The cumulative method uses the foundational quantity "
                        "to determine the result."
                    ),
                    "achieving_mastery": (
                        "Apply the cumulative method to determine a result."
                    ),
                    "keywords": ["cumulative", "result"],
                    "source_block_ids": ["BLK-B1", "BLK-B2"],
                    "topic_relationships": _relationships(reverse=reverse),
                    "assignment_unit_ids": ["UNIT-1"],
                    "confidence": 0.999,
                    "reason": "The source supports a distinct durable method.",
                }
            ],
            "existing_concept_updates": [],
        }

    plan = phase33._resolve_host_plan(
        graph=graph,
        topic_id="TOPIC-B",
        topic=graph["topics"][1],
        units=units,
        concepts=[],
        source_blocks=_source_blocks(directory),
        provider=provider,
        critic=critic,
        placement_authority=authority,
        placement_order=order,
        block_directory=directory,
        qid_contracts=qid_contracts,
    )
    return plan, authority, qid_contracts, graph, order, directory, units


def test_create_host_provider_critic_apply_and_phase31_certificate_chain():
    (
        plan,
        authority,
        qid_contracts,
        graph,
        order,
        directory,
        _unit_payloads,
    ) = _resolve_create()
    definition = plan["new_concepts"][0]
    contract = definition["_placement_contract"]
    assert contract["owner_topic_id"] == "TOPIC-B"
    assert contract["claim_id"] == phase33._stable_new_host_claim_id(
        graph["source_contract_hash"], ["QINV-0001", "QINV-0002"]
    )
    assert contract["protected_source_items"] == [
        "QINV-0001",
        "QINV-0002",
    ]

    units = [{
        "_phase33_assignment_unit_id": "UNIT-1",
        "_semantic_topic_id": "TOPIC-B",
        "source_question_ids": ["QINV-0001", "QINV-0002"],
    }]
    records, host_map, qid_map, created = phase33._apply_host_plan(
        [],
        plans=[plan],
        concept_payload=[],
        concept_index={},
        topic_by_id={row["topic_id"]: row for row in graph["topics"]},
        graph=graph,
        subtopic_by_block={},
        units=units,
    )
    assert created == 1
    records = phase33._bind_and_verify_applied_host_mutations(
        records,
        plans=[plan],
        host_map=host_map,
        qid_map=qid_map,
        units=units,
        placement_authority=authority,
        qid_contracts=qid_contracts,
        graph=graph,
    )
    phase31._verify_preserved_placement_contracts(records)
    grounding_certificate.seal_records(
        records,
        source_contract_hash=graph["source_contract_hash"],
        semantic_topology_sha256=(
            grounding_certificate.semantic_topology_sha256(graph)
        ),
        allowed_block_ids=set(directory),
    )
    assert records[0][grounding_certificate.ROW_CERTIFICATE_FIELD]
    assert grounding_certificate.verify_placement_contract(records[0])[
        "owner_topic_id"
    ] == "TOPIC-B"
    assert qid_map["QINV-0001"]["topic_id"] == "TOPIC-B"
    assert qid_map["QINV-0002"]["placement_certificate_sha256"] == (
        contract["placement_certificate_sha256"]
    )


def test_expand_preserves_old_lineage_source_location_and_requires_old_contract():
    authority, qid_contracts, graph, order, directory = _placement_authority(
        ["QINV-0001"]
    )
    row = _old_row(order)
    old_contract = copy.deepcopy(row["_placement_contract"])
    concepts, concept_index, _by_topic = phase33._normal_concept_payloads([row])
    unit = _unit(["QINV-0001"])

    def provider(_payload: dict) -> dict:
        return {
            "assignments": [{
                "assignment_unit_id": "UNIT-1",
                "decision": "expand_existing",
                "existing_concept_id": "HOST-CONCEPT-0001",
                "new_concept_key": "NONE",
                "confidence": 0.999,
                "reason": "The durable concept identity remains correct.",
            }],
            "new_concepts": [],
            "existing_concept_updates": [{
                "existing_concept_id": "HOST-CONCEPT-0001",
                "topic_id": "TOPIC-B",
                "description": (
                    "The cumulative method uses the foundational quantity "
                    "to determine the result."
                ),
                "achieving_mastery": "Apply the cumulative method.",
                "keywords": ["result"],
                "source_block_ids": ["BLK-B1", "BLK-B2"],
                "topic_relationships": _relationships(),
                "assignment_unit_ids": ["UNIT-1"],
                "confidence": 0.999,
                "reason": "The expansion is supported by exact source blocks.",
            }],
        }

    plan = phase33._resolve_host_plan(
        graph=graph,
        topic_id="TOPIC-B",
        topic=graph["topics"][1],
        units=[unit],
        concepts=concepts,
        source_blocks=_source_blocks(directory),
        provider=provider,
        critic=_accepted_review,
        placement_authority=authority,
        placement_order=order,
        block_directory=directory,
        qid_contracts=qid_contracts,
    )
    updated = plan["existing_concept_updates"][0]["_placement_contract"]
    for field in (
        "claim_id",
        "origin_claim_sha256",
        "source_location_topic_id",
        "origin_claim_ids",
        "split_group_id",
    ):
        assert updated[field] == old_contract[field]

    materialized, payloads, plans, count = (
        phase33._materialize_existing_host_updates(
            [row],
            plans=[plan],
            concept_payload=concepts,
            concept_index=concept_index,
            subtopic_by_block={},
        )
    )
    assert count == 1
    assert materialized[0]["_placement_contract"] == updated
    units = [{
        "_phase33_assignment_unit_id": "UNIT-1",
        "_semantic_topic_id": "TOPIC-B",
        "source_question_ids": ["QINV-0001"],
    }]
    applied, host_map, qid_map, _created = phase33._apply_host_plan(
        materialized,
        plans=plans,
        concept_payload=payloads,
        concept_index=concept_index,
        topic_by_id={row["topic_id"]: row for row in graph["topics"]},
        graph=graph,
        subtopic_by_block={},
        units=units,
    )
    applied = phase33._bind_and_verify_applied_host_mutations(
        applied,
        plans=plans,
        host_map=host_map,
        qid_map=qid_map,
        units=units,
        placement_authority=authority,
        qid_contracts=qid_contracts,
        graph=graph,
    )
    phase31._verify_preserved_placement_contracts(applied)
    grounding_certificate.verify_placement_contract(
        applied[0], allowed_block_ids=set(directory)
    )

    broken = copy.deepcopy(concepts)
    broken[0]["_placement_contract"]["owner_topic_id"] = "TOPIC-A"
    with pytest.raises(
        phase33.ProviderResponseContractError,
        match="no current certified old placement contract",
    ):
        phase33._resolve_host_plan(
            graph=graph,
            topic_id="TOPIC-B",
            topic=graph["topics"][1],
            units=[unit | {"assignment_unit_id": "UNIT-1"}],
            concepts=broken,
            source_blocks=_source_blocks(directory),
            provider=provider,
            critic=_accepted_review,
            placement_authority=authority,
            placement_order=order,
            block_directory=directory,
            qid_contracts=qid_contracts,
        )


def test_create_owner_and_digest_are_relationship_order_independent():
    first = _resolve_create(reverse=False)[0]["new_concepts"][0]
    second = _resolve_create(reverse=True)[0]["new_concepts"][0]

    assert first["_placement_contract"] == second["_placement_contract"]
    assert first["_placement_contract"]["owner_topic_id"] == "TOPIC-B"


@pytest.mark.parametrize("mode", ["missing", "rejected", "fabricated"])
def test_missing_rejected_or_fabricated_relationship_review_fails_closed(mode):
    def critic(payload: dict) -> dict:
        review = _accepted_review(payload)
        if mode == "missing":
            review["relationship_reviews"].pop()
        elif mode == "rejected":
            review["relationship_reviews"][0]["verdict"] = "rejected"
            review["relationship_reviews"][0]["reason"] = "Not supported."
        else:
            review["relationship_reviews"][0]["relationship_id"] = (
                "REL-FABRICATED"
            )
        return review

    with pytest.raises(RuntimeError, match="type_host_placement_uncertified"):
        _resolve_create(critic=critic)


@pytest.mark.parametrize("mode", ["fabricated_block", "wrong_topic_block"])
def test_fabricated_or_wrong_topic_provider_evidence_fails_before_critic(mode):
    authority, qid_contracts, graph, order, directory = _placement_authority(
        ["QINV-0001"]
    )
    critic_calls = 0
    provider_calls = 0

    def provider(_payload: dict) -> dict:
        nonlocal provider_calls
        provider_calls += 1
        block_id = "BLK-NOT-REAL" if mode == "fabricated_block" else "BLK-A"
        return {
            "assignments": [{
                "assignment_unit_id": "UNIT-1",
                "decision": "create_new",
                "existing_concept_id": "NONE",
                "new_concept_key": "NEW-HOST-0001",
                "confidence": 0.999,
                "reason": "Create.",
            }],
            "new_concepts": [{
                "new_concept_key": "NEW-HOST-0001",
                "topic_id": "TOPIC-B",
                "concept_title": "Cumulative Application",
                "parent_concept": "Methods",
                "description": "Apply the cumulative method.",
                "achieving_mastery": "Apply it.",
                "keywords": [],
                "source_block_ids": ["BLK-B2"],
                "topic_relationships": [{
                    "topic_id": "TOPIC-B",
                    "relationship_type": "CORE_TEACHING",
                    "necessity": True,
                    "evidence_block_ids": [block_id],
                    "reason": "Claimed evidence.",
                }],
                "assignment_unit_ids": ["UNIT-1"],
                "confidence": 0.999,
                "reason": "Create.",
            }],
            "existing_concept_updates": [],
        }

    def critic(_payload: dict) -> dict:
        nonlocal critic_calls
        critic_calls += 1
        raise AssertionError("critic must not receive fabricated evidence")

    with pytest.raises(
        phase33.ProviderResponseContractError,
        match="type_host_placement_uncertified",
    ):
        phase33._resolve_host_plan(
            graph=graph,
            topic_id="TOPIC-B",
            topic=graph["topics"][1],
            units=[_unit(["QINV-0001"])],
            concepts=[],
            source_blocks=_source_blocks(directory),
            provider=provider,
            critic=critic,
            placement_authority=authority,
            placement_order=order,
            block_directory=directory,
            qid_contracts=qid_contracts,
        )
    # Bad evidence gets the same bounded mechanical corrections as any other
    # contract defect (job 23 died on attempt 1 of this exact case), and only
    # then fails closed — without the critic ever seeing the bad citation.
    assert provider_calls == 3
    assert critic_calls == 0


def test_wrong_topic_evidence_is_recovered_by_one_bounded_correction():
    """Job 23 died on one cross-topic citation with zero re-asks.

    The same defect now costs one corrected provider round: the model is told
    exactly which relationship cited a block from the wrong topic, returns the
    fixed response, and the plan certifies normally.
    """
    authority, qid_contracts, graph, order, directory = _placement_authority(
        ["QINV-0001", "QINV-0002"]
    )
    units = [_unit(["QINV-0002", "QINV-0001"])]
    provider_calls = 0

    def provider(payload: dict) -> dict:
        nonlocal provider_calls
        provider_calls += 1
        relationships = _relationships()
        if provider_calls == 1:
            # TOPIC-B's core-teaching relationship cites TOPIC-A's block —
            # the exact defect that killed job 23.
            relationships[1]["evidence_block_ids"] = ["BLK-A", "BLK-B2"]
        else:
            feedback = payload.get("response_contract_feedback") or []
            assert any("BLK-A" in str(row) for row in feedback)
        return {
            "assignments": [{
                "assignment_unit_id": "UNIT-1",
                "decision": "create_new",
                "existing_concept_id": "NONE",
                "new_concept_key": "NEW-HOST-0001",
                "confidence": 0.999,
                "reason": "No current host teaches the complete method.",
            }],
            "new_concepts": [{
                "new_concept_key": "NEW-HOST-0001",
                "topic_id": "TOPIC-B",
                "concept_title": "Applying the Cumulative Method",
                "parent_concept": "Methods",
                "description": (
                    "The cumulative method uses the foundational quantity "
                    "to determine the result."
                ),
                "achieving_mastery": (
                    "Apply the cumulative method to determine a result."
                ),
                "keywords": ["cumulative", "result"],
                "source_block_ids": ["BLK-B1", "BLK-B2"],
                "topic_relationships": relationships,
                "assignment_unit_ids": ["UNIT-1"],
                "confidence": 0.999,
                "reason": "The source supports a distinct durable method.",
            }],
            "existing_concept_updates": [],
        }

    plan = phase33._resolve_host_plan(
        graph=graph,
        topic_id="TOPIC-B",
        topic=graph["topics"][1],
        units=units,
        concepts=[],
        source_blocks=_source_blocks(directory),
        provider=provider,
        critic=_accepted_review,
        placement_authority=authority,
        placement_order=order,
        block_directory=directory,
        qid_contracts=qid_contracts,
    )

    assert provider_calls == 2
    contract = plan["new_concepts"][0]["_placement_contract"]
    assert contract["owner_topic_id"] == "TOPIC-B"


def test_conflicting_qid_owners_fail_before_host_critic():
    authority, qid_contracts, graph, order, directory = _placement_authority(
        ["QINV-0001", "QINV-0002"]
    )
    qid_contracts["QINV-0002"]["owner_topic_id"] = "TOPIC-A"
    critic_calls = 0

    def provider(_payload: dict) -> dict:
        return {
            "assignments": [{
                "assignment_unit_id": "UNIT-1",
                "decision": "create_new",
                "existing_concept_id": "NONE",
                "new_concept_key": "NEW-HOST-0001",
                "confidence": 0.999,
                "reason": "Create.",
            }],
            "new_concepts": [{
                "new_concept_key": "NEW-HOST-0001",
                "topic_id": "TOPIC-B",
                "concept_title": "Cumulative Application",
                "parent_concept": "Methods",
                "description": "Apply the cumulative method.",
                "achieving_mastery": "Apply it.",
                "keywords": [],
                "source_block_ids": ["BLK-B2"],
                "topic_relationships": [{
                    "topic_id": "TOPIC-B",
                    "relationship_type": "CORE_TEACHING",
                    "necessity": True,
                    "evidence_block_ids": ["BLK-B2"],
                    "reason": "The topic teaches the task.",
                }],
                "assignment_unit_ids": ["UNIT-1"],
                "confidence": 0.999,
                "reason": "Create.",
            }],
            "existing_concept_updates": [],
        }

    def critic(_payload: dict) -> dict:
        nonlocal critic_calls
        critic_calls += 1
        return {}

    with pytest.raises(
        phase33.ProviderResponseContractError,
        match="owner conflict",
    ):
        phase33._resolve_host_plan(
            graph=graph,
            topic_id="TOPIC-B",
            topic=graph["topics"][1],
            units=[_unit(["QINV-0001", "QINV-0002"])],
            concepts=[],
            source_blocks=_source_blocks(directory),
            provider=provider,
            critic=critic,
            placement_authority=authority,
            placement_order=order,
            block_directory=directory,
            qid_contracts=qid_contracts,
        )
    assert critic_calls == 0


def test_unchanged_existing_host_contract_is_byte_for_byte_preserved():
    _authority, _contracts, graph, order, _directory = _placement_authority(
        ["QINV-0001"]
    )
    row = _old_row(order)
    before = copy.deepcopy(row["_placement_contract"])
    payload, index, _by_topic = phase33._normal_concept_payloads([row])
    plan = {
        "assignments": [{
            "assignment_unit_id": "UNIT-1",
            "decision": "existing",
            "existing_concept_id": "HOST-CONCEPT-0001",
            "new_concept_key": "NONE",
            "confidence": 0.999,
        }],
        "new_concepts": [],
        "existing_concept_updates": [],
    }
    units = [{
        "_phase33_assignment_unit_id": "UNIT-1",
        "_semantic_topic_id": "TOPIC-B",
        "source_question_ids": ["QINV-0001"],
    }]
    out, _host_map, _qid_map, _created = phase33._apply_host_plan(
        [row],
        plans=[plan],
        concept_payload=payload,
        concept_index=index,
        topic_by_id={value["topic_id"]: value for value in graph["topics"]},
        graph=graph,
        subtopic_by_block={},
        units=units,
    )
    phase33._assert_unchanged_host_contracts_preserved([row], out)
    assert out[0]["_placement_contract"] == before


def test_host_plan_cache_revalidates_reviews_authority_and_plan_digest(tmp_path):
    plan, authority, _qids, graph, _order, _directory, _units = (
        _resolve_create()
    )
    key = "PLAN-CACHE-KEY"
    with phase3.activate_session({"artifact_dir": tmp_path}):
        phase33._write_host_plan_cache(
            key,
            graph=graph,
            topic_id="TOPIC-B",
            plan=plan,
            confidence=0.999,
            placement_authority=authority,
        )
        assert phase33._read_host_plan_cache(
            key,
            placement_authority=authority,
            topic_id="TOPIC-B",
        ) == plan

        path = tmp_path / phase33._HOST_PLAN_CACHE_FILENAME
        cached = phase33._read_json(path)
        cached["entries"][key]["plan"][
            phase33._HOST_MUTATION_ENVELOPE_KEY
        ]["relationship_reviews"][0]["evidence_supported"] = False
        phase33._write_json(path, cached)
        assert phase33._read_host_plan_cache(
            key,
            placement_authority=authority,
            topic_id="TOPIC-B",
        ) is None

        changed_authority = copy.deepcopy(authority)
        changed_authority["teaching_order_sha256"] = "0" * 64
        assert phase33._read_host_plan_cache(
            key,
            placement_authority=changed_authority,
            topic_id="TOPIC-B",
        ) is None


def test_host_result_cache_revalidates_full_output_placement_envelope(tmp_path):
    authority, _qids, graph, order, _directory = _placement_authority(
        ["QINV-0001"]
    )
    records = [_old_row(order)]
    host_map = {"UNIT-1": {"topic_id": "TOPIC-B"}}
    qid_map = {"QINV-0001": {"topic_id": "TOPIC-B"}}
    key = "RESULT-CACHE-KEY"
    with phase3.activate_session({"artifact_dir": tmp_path}):
        phase33._write_host_result_cache(
            key,
            graph=graph,
            records=records,
            host_map=host_map,
            qid_map=qid_map,
            summary={"output_rows": 1},
            placement_authority=authority,
        )
        assert phase33._read_host_result_cache(
            key, placement_authority=authority
        ) is not None

        path = tmp_path / phase33._HOST_RESULT_CACHE_FILENAME
        cached = phase33._read_json(path)
        cached["records"][0]["_placement_contract"][
            "owner_topic_id"
        ] = "TOPIC-A"
        cached["records_sha256"] = phase3._sha256_json(cached["records"])
        phase33._write_json(path, cached)
        assert phase33._read_host_result_cache(
            key, placement_authority=authority
        ) is None

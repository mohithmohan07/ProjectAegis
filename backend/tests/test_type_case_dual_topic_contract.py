"""Behavioral regressions for certified Type/Case dual-topic routing."""
from __future__ import annotations

import copy

import pytest

from app.services import canonical_source_phase3 as phase3
from app.services import canonical_source_phase33_preflight_contract as phase33
from app.services import canonical_source_phase333_multitopic_host_contract as phase333
from app.services import generation as g
from app.services import placement_policy


def _contract(
    qid: str,
    *,
    source: str,
    owner: str,
    owner_title: str,
) -> dict:
    relationship = placement_policy.TopicRelationship(
        claim_id=f"TASK:{qid}",
        topic_id=owner,
        relationship_type=placement_policy.RelationshipType.CORE_TEACHING,
        necessity=True,
        evidence_block_ids=(f"BLK-{owner}",),
    )
    value = {
        "version": 2,
        "policy_version": placement_policy.POLICY_VERSION,
        "teaching_order_sha256": "teaching-order-sha",
        "source_contract_hash": "SOURCE-1",
        "claim_id": f"TASK:{qid}",
        "source_location_topic_id": source,
        "source_location_topic_ids": [source],
        "owner_topic_id": owner,
        "owner_topic_title": owner_title,
        "required_topic_ids": [owner],
        "prerequisite_topic_ids": [],
        "reference_edges": [],
        "illustration_topic_ids": [],
        "topic_relationships": [{
            "relationship_id": relationship.relationship_id,
            "claim_id": f"TASK:{qid}",
            "topic_id": owner,
            "relationship_type": "CORE_TEACHING",
            "necessity": True,
            "evidence_block_ids": [f"BLK-{owner}"],
            "provider_reason": "The topic directly teaches the task.",
            "critic_verdict": "accepted",
        }],
        "certified": True,
    }
    value["placement_certificate_sha256"] = (
        g._type_case_placement_digest(value))
    return value


def _inventory_item(qid: str, contract: dict) -> dict:
    return {
        "qid": qid,
        "topic_hint": "Physical source title",
        "source_location_topic_id": contract["source_location_topic_id"],
        "_type_case_placement_contract": copy.deepcopy(contract),
    }


def _ledger(placements: dict[str, dict]) -> dict:
    value = {
        "version": g._TYPE_CASE_PLACEMENT_CONTRACT_VERSION,
        "certified": True,
        "policy_version": placement_policy.POLICY_VERSION,
        "source_contract_hash": "SOURCE-1",
        "teaching_order_sha256": "teaching-order-sha",
        "placements": copy.deepcopy(placements),
    }
    value["certificate_sha256"] = phase3._sha256_json(value)
    return value


def _example(qid: str) -> dict:
    return {
        "source_question_id": qid,
        "example_prompt": f"Perform task {qid}.",
    }


def _checkpoint_placement_payload() -> tuple[list[dict], dict, dict]:
    qid = "QINV-0001"
    contract = _contract(
        qid,
        source="TOPIC-A",
        owner="TOPIC-B",
        owner_title="Later Method",
    )
    inventory = {
        "items": [{
            **_inventory_item(qid, contract),
            "source_kind": "exercise",
            "raw_task": "Apply the later method to solve the source task.",
        }],
        "stats": {"total_inventory_items": 1},
    }
    raw_types = [{
        "type_id": "TYPE-0001",
        "type_title": "Apply the later method",
        "source_question_ids": [qid],
        "case_prompts": [{
            "case_id": "CASE-0001",
            "examples": [_example(qid)],
        }],
    }]
    ledger = _ledger({qid: contract})
    inventory[g._TYPE_CASE_QID_PLACEMENT_LEDGER_KEY] = copy.deepcopy(ledger)
    mined_types = {
        "types": g._annotate_mined_type_case_routes(raw_types, inventory),
        g._TYPE_CASE_QID_PLACEMENT_LEDGER_KEY: copy.deepcopy(ledger),
    }
    records = [{
        "topic": "Later Method",
        "parent_concept": "Methods",
        "concept_title": "Certified host",
        "concept_details": "Description: Apply the later method.",
        "keywords": "method",
    }]
    g._reset_placement_certifications(mined_types)
    g._certify_inventory_host(
        mined_types, qid, records[0], basis="type_host_review"
    )
    return records, inventory, mined_types


def test_legacy_post_type_checkpoint_without_v2_ledger_rewinds_to_pre_type():
    records, inventory, mined_types = _checkpoint_placement_payload()
    pre_type = g._make_concept_checkpoint(
        g._CONCEPT_CHECKPOINT_STAGE,
        records=records,
        question_task_inventory={
            "items": [{
                key: copy.deepcopy(value)
                for key, value in inventory["items"][0].items()
                if key != "_type_case_placement_contract"
            }],
            "stats": copy.deepcopy(inventory["stats"]),
        },
        mined_types={
            "types": [{
                "type_id": "TYPE-0001",
                "type_title": "Apply the later method",
                "source_question_ids": ["QINV-0001"],
                "case_prompts": [{
                    "case_id": "CASE-0001",
                    "examples": [_example("QINV-0001")],
                }],
            }],
        },
        method_row_snapshot=[],
    )
    legacy_post = g._make_concept_checkpoint(
        "post_type_assignment",
        records=records,
        question_task_inventory={
            key: copy.deepcopy(value)
            for key, value in inventory.items()
            if key != g._TYPE_CASE_QID_PLACEMENT_LEDGER_KEY
        },
        mined_types={
            key: copy.deepcopy(value)
            for key, value in mined_types.items()
            if key != g._TYPE_CASE_QID_PLACEMENT_LEDGER_KEY
        },
        method_row_snapshot=[],
    )
    legacy_post["stage_schema_version"] = 5
    history = {
        "checkpoint_format": g._CONCEPT_CHECKPOINT_FORMAT,
        "schema_version": g._CONCEPT_CHECKPOINT_SCHEMA,
        "checkpoints": [pre_type, legacy_post],
    }

    restored = g._newest_compatible_concept_checkpoint(history)

    assert not g._compatible_concept_checkpoint_entry(legacy_post)
    assert restored is not None
    assert restored["stage"] == g._CONCEPT_CHECKPOINT_STAGE


def test_current_post_type_checkpoint_with_matching_v2_ledger_resumes():
    records, inventory, mined_types = _checkpoint_placement_payload()
    checkpoint = g._make_concept_checkpoint(
        "post_type_assignment",
        records=records,
        question_task_inventory=inventory,
        mined_types=mined_types,
        method_row_snapshot=[],
    )

    assert checkpoint["stage_schema_version"] == 6
    assert g._compatible_concept_checkpoint_entry(checkpoint)
    assert g._newest_compatible_concept_checkpoint(checkpoint) == checkpoint

    missing_mined_ledger = copy.deepcopy(checkpoint)
    missing_mined_ledger["mined_types"].pop(
        g._TYPE_CASE_QID_PLACEMENT_LEDGER_KEY
    )
    assert not g._compatible_concept_checkpoint_entry(missing_mined_ledger)

    missing_ordinary_unit = copy.deepcopy(checkpoint)
    missing_ordinary_unit["mined_types"]["types"] = []
    assert not g._compatible_concept_checkpoint_entry(missing_ordinary_unit)


def test_current_post_type_checkpoint_allows_certified_pure_activity_qid():
    qid = "QINV-ACTIVITY"
    contract = _contract(
        qid,
        source="TOPIC-A",
        owner="TOPIC-B",
        owner_title="Investigations",
    )
    ledger = _ledger({qid: contract})
    inventory = {
        "items": [{
            **_inventory_item(qid, contract),
            "source_kind": "activity",
            "raw_task": (
                "Observe the investigation and record the supported result."
            ),
        }],
        "stats": {"total_inventory_items": 1},
        g._TYPE_CASE_QID_PLACEMENT_LEDGER_KEY: copy.deepcopy(ledger),
    }
    mined_types = {
        "types": [],
        g._TYPE_CASE_QID_PLACEMENT_LEDGER_KEY: copy.deepcopy(ledger),
    }
    records = [{
        "topic": "Investigations",
        "parent_concept": "Activities",
        "concept_title": "Investigation Hub",
        "concept_details": (
            "Description: Perform and document the investigation."
        ),
        "keywords": "investigation",
    }]
    g._reset_placement_certifications(mined_types)
    g._certify_inventory_host(
        mined_types, qid, records[0], basis="activity_host_review"
    )
    checkpoint = g._make_concept_checkpoint(
        "post_type_assignment",
        records=records,
        question_task_inventory=inventory,
        mined_types=mined_types,
        method_row_snapshot=[],
    )

    assert g._compatible_concept_checkpoint_entry(checkpoint)


def test_empty_inventory_post_type_checkpoint_remains_portable_without_ledger():
    checkpoint = g._make_concept_checkpoint(
        "post_type_assignment",
        records=[{"topic": "T", "concept_title": "Portable concept"}],
        question_task_inventory={"items": [], "stats": {}},
        mined_types={"types": []},
        method_row_snapshot=[],
    )

    assert g._compatible_concept_checkpoint_entry(checkpoint)


def test_source_location_and_certified_owner_remain_distinct_end_to_end():
    contract = _contract(
        "QINV-0001",
        source="TOPIC-A",
        owner="TOPIC-B",
        owner_title="Later Method",
    )
    inventory = {"items": [_inventory_item("QINV-0001", contract)]}
    types = [{
        "type_id": "TYPE-0001",
        "type_title": "Reusable operator",
        "source_question_ids": ["QINV-0001"],
        "case_prompts": [{
            "case_id": "CASE-0001",
            "examples": [_example("QINV-0001")],
        }],
    }]
    graph = {
        "source_contract_hash": "SOURCE-1",
        "topics": [
            {"topic_id": "TOPIC-A", "title": "Physical Source"},
            {"topic_id": "TOPIC-B", "title": "Later Method"},
        ],
        "subtopics": [],
        "tasks": [{
            "task_id": "TASK-1",
            "qid": "QINV-0001",
            "topic_id": "TOPIC-A",
            "subtopic_id": "",
        }],
    }

    routed = g._annotate_mined_type_case_routes(types, inventory)
    case = routed[0]["case_prompts"][0]
    assert case["source_location_topic_ids"] == ["TOPIC-A"]
    assert case["owner_topic_id"] == "TOPIC-B"
    assert case["topic_match_hint"] == "Later Method"

    annotated_inventory = phase3.annotate_inventory(inventory, graph)
    item = annotated_inventory["items"][0]
    assert item["source_location_topic_id"] == "TOPIC-A"
    assert item["owner_topic_id"] == "TOPIC-B"
    assert item["_semantic_topic_id"] == "TOPIC-B"

    annotated = phase3.annotate_mined_types({"types": routed}, graph)
    case = annotated["types"][0]["case_prompts"][0]
    assert case["_semantic_topic_id"] == "TOPIC-B"
    assert case["topic_match_hint"] == "Later Method"


def test_reusable_type_keeps_cases_with_distinct_certified_owners():
    first = _contract(
        "QINV-0001", source="TOPIC-A", owner="TOPIC-A",
        owner_title="Foundations",
    )
    second = _contract(
        "QINV-0002", source="TOPIC-A", owner="TOPIC-B",
        owner_title="Applications",
    )
    inventory = {"items": [
        _inventory_item("QINV-0001", first),
        _inventory_item("QINV-0002", second),
    ]}
    types = [{
        "type_id": "TYPE-REUSE",
        "source_question_ids": ["QINV-0001", "QINV-0002"],
        "case_prompts": [
            {"case_id": "CASE-A", "examples": [_example("QINV-0001")]},
            {"case_id": "CASE-B", "examples": [_example("QINV-0002")]},
        ],
    }]

    routed = g._annotate_mined_type_case_routes(types, inventory)

    assert len(routed) == 1
    assert routed[0]["owner_topic_ids"] == ["TOPIC-A", "TOPIC-B"]
    assert routed[0]["owner_topic_id"] == ""
    assert [
        case["owner_topic_id"] for case in routed[0]["case_prompts"]
    ] == ["TOPIC-A", "TOPIC-B"]
    units = g._expand_mined_types_to_assignment_units(routed)
    assert len(units) == 2
    assert {unit["owner_topic_id"] for unit in units} == {
        "TOPIC-A", "TOPIC-B",
    }


def test_mixed_owner_case_splits_exactly_once_but_same_owner_does_not():
    owner_a = _contract(
        "QINV-0001", source="TOPIC-A", owner="TOPIC-A",
        owner_title="Foundations",
    )
    owner_b = _contract(
        "QINV-0002", source="TOPIC-A", owner="TOPIC-B",
        owner_title="Applications",
    )
    inventory = {"items": [
        _inventory_item("QINV-0001", owner_a),
        _inventory_item("QINV-0002", owner_b),
    ]}
    mixed = [{
        "type_id": "TYPE-MIXED",
        "source_question_ids": ["QINV-0001", "QINV-0002"],
        "case_prompts": [{
            "case_id": "CASE-MIXED",
            "examples": [_example("QINV-0001"), _example("QINV-0002")],
        }],
    }]

    split = g._annotate_mined_type_case_routes(mixed, inventory)
    assert len(split[0]["case_prompts"]) == 2
    assert sorted(
        qid
        for case in split[0]["case_prompts"]
        for qid in g._assignment_case_qids(case)
    ) == ["QINV-0001", "QINV-0002"]

    same_owner_second = _contract(
        "QINV-0002", source="TOPIC-C", owner="TOPIC-B",
        owner_title="Applications",
    )
    same_owner_first = _contract(
        "QINV-0001", source="TOPIC-A", owner="TOPIC-B",
        owner_title="Applications",
    )
    same_inventory = {"items": [
        _inventory_item("QINV-0001", same_owner_first),
        _inventory_item("QINV-0002", same_owner_second),
    ]}
    kept = g._annotate_mined_type_case_routes(mixed, same_inventory)
    assert len(kept[0]["case_prompts"]) == 1
    assert kept[0]["case_prompts"][0]["source_location_topic_ids"] == [
        "TOPIC-A", "TOPIC-C",
    ]
    assert kept[0]["case_prompts"][0]["owner_topic_id"] == "TOPIC-B"


def test_invalid_v2_digest_fails_closed_instead_of_using_source_topic():
    contract = _contract(
        "QINV-0001", source="TOPIC-A", owner="TOPIC-B",
        owner_title="Later Method",
    )
    contract["owner_topic_id"] = "TOPIC-C"
    inventory = {"items": [_inventory_item("QINV-0001", contract)]}
    types = [{
        "type_id": "TYPE-0001",
        "source_question_ids": ["QINV-0001"],
        "case_prompts": [{"examples": [_example("QINV-0001")]}],
    }]

    with pytest.raises(RuntimeError, match="type_case_owner_uncertified"):
        g._annotate_mined_type_case_routes(types, inventory)


def test_multitopic_host_apply_binds_qid_manifest_and_rejects_owner_drift():
    child = _contract(
        "QINV-0001", source="TOPIC-A", owner="TOPIC-B",
        owner_title="Applications",
    )
    aggregate = g._aggregate_type_case_placement_contract(
        {"QINV-0001": child})
    unit = {
        "_phase33_assignment_unit_id": "UNIT-0001",
        "_semantic_topic_id": "TOPIC-B",
        "owner_topic_id": "TOPIC-B",
        "source_question_ids": ["QINV-0001"],
        "_type_case_placement_contract": aggregate,
        "case_prompts": [{
            "examples": [{
                **_example("QINV-0001"),
                "_type_case_placement_contract": child,
            }],
        }],
    }
    records = [{
        "topic": "Applications",
        "parent_concept": "Methods",
        "concept_title": "Specific application",
        "_semantic_topic_id": "TOPIC-B",
    }]
    concepts = [{
        "concept_id": "HOST-CONCEPT-0001",
        "topic_id": "TOPIC-B",
        "topic": "Applications",
        "parent_concept": "Methods",
        "concept_title": "Specific application",
    }]
    plan = [{
        "assignments": [{
            "assignment_unit_id": "UNIT-0001",
            "decision": "existing",
            "existing_concept_id": "HOST-CONCEPT-0001",
            "new_concept_key": "NONE",
            "confidence": 0.999,
        }],
        "new_concepts": [],
    }]

    out, host_map, qid_map, _created = phase333._apply_host_plan(
        records,
        plans=plan,
        concept_payload=concepts,
        concept_index={"HOST-CONCEPT-0001": 0},
        topic_by_id={
            "TOPIC-B": {"topic_id": "TOPIC-B", "title": "Applications"},
        },
        graph={"source_contract_hash": "SOURCE-1"},
        subtopic_by_block={},
        units=[unit],
    )
    manifest = out[0]["_type_case_qid_host_placement_manifest"]
    manifest_entry = manifest["placements"]["QINV-0001"]
    assert manifest_entry["owner_topic_id"] == "TOPIC-B"
    assert manifest_entry["host_topic_id"] == "TOPIC-B"
    assert manifest_entry["placement_contract"] == child
    assert g._type_case_placement_digest(
        manifest_entry["placement_contract"]
    ) == manifest_entry["placement_certificate_sha256"]
    assert host_map["UNIT-0001"]["topic_id"] == "TOPIC-B"
    assert qid_map["QINV-0001"]["topic_id"] == "TOPIC-B"

    wrong = copy.deepcopy(concepts)
    wrong[0]["topic_id"] = "TOPIC-A"
    with pytest.raises(ValueError, match="certified assignment-unit owner"):
        phase333._apply_host_plan(
            records,
            plans=plan,
            concept_payload=wrong,
            concept_index={"HOST-CONCEPT-0001": 0},
            topic_by_id={
                "TOPIC-A": {"topic_id": "TOPIC-A", "title": "Foundations"},
                "TOPIC-B": {"topic_id": "TOPIC-B", "title": "Applications"},
            },
            graph={"source_contract_hash": "SOURCE-1"},
            subtopic_by_block={},
            units=[unit],
        )


def test_verified_host_selection_uses_owner_id_and_ignores_candidate_order():
    child = _contract(
        "QINV-0001", source="TOPIC-A", owner="TOPIC-B",
        owner_title="Germania and her attributes",
    )
    unit = {
        "_phase33_host_verified": True,
        "_type_case_placement_contract": (
            g._aggregate_type_case_placement_contract({"QINV-0001": child})
        ),
        "topic_match_hint": "Germania and her attributes",
        "parent_concept_match_hint": "National allegories",
        "concept_match_hint": "Germania and her attributes",
    }
    concepts = {
        "CONCEPT-GENERIC": {
            "concept_id": "CONCEPT-GENERIC",
            "topic_id": "TOPIC-A",
            "topic": "Allegories of the nation",
            "parent_concept": "National allegories",
            "concept": "Germania and her attributes",
            "is_culmination": False,
        },
        "CONCEPT-SPECIFIC": {
            "concept_id": "CONCEPT-SPECIFIC",
            "topic_id": "TOPIC-B",
            "topic": "Germania and her attributes",
            "parent_concept": "National allegories",
            "concept": "Germania and her attributes",
            "is_culmination": False,
        },
    }

    assert g._high_confidence_assignment_override(
        unit,
        ("CONCEPT-GENERIC", "CONCEPT-SPECIFIC"),
        concepts,
    ) == "CONCEPT-SPECIFIC"
    assert g._high_confidence_assignment_override(
        unit,
        ("CONCEPT-SPECIFIC", "CONCEPT-GENERIC"),
        concepts,
    ) == "CONCEPT-SPECIFIC"

    with pytest.raises(RuntimeError, match="verified Type host is absent"):
        g._high_confidence_assignment_override(
            unit,
            ("CONCEPT-GENERIC",),
            concepts,
        )


def test_resume_preserves_valid_v2_ownership_and_invalidates_old_host_map():
    child = _contract(
        "QINV-0001", source="TOPIC-A", owner="TOPIC-B",
        owner_title="Applications",
    )
    ledger = {
        "version": 2,
        "certified": True,
        "policy_version": placement_policy.POLICY_VERSION,
        "source_contract_hash": "SOURCE-1",
        "teaching_order_sha256": "teaching-order-sha",
        "placements": {"QINV-0001": copy.deepcopy(child)},
    }
    ledger["certificate_sha256"] = phase3._sha256_json(ledger)
    inventory = {"items": [{
        **_inventory_item("QINV-0001", child),
        "source_kind": "short_answer",
        "raw_task": "Perform the application.",
    }]}
    persisted = {
        g._TYPE_CASE_QID_PLACEMENT_LEDGER_KEY: ledger,
        "_phase33_verified_host_map": {"OLD": {"topic_id": "TOPIC-A"}},
        "types": [{
            "type_id": "TYPE-0001",
            "type_title": "Apply the method",
            "source_question_ids": ["QINV-0001"],
            "_phase33_verified_host_map": {
                "OLD": {"topic_id": "TOPIC-A"},
            },
            "case_prompts": [{
                "case_id": "CASE-0001",
                "examples": [{
                    **_example("QINV-0001"),
                    "_type_case_placement_contract": copy.deepcopy(child),
                }],
                "_type_case_placement_contract": copy.deepcopy(child),
            }],
        }],
    }

    resumed = g._reconcile_resumed_mined_types(
        persisted,
        inventory=inventory,
        meta={},
        use_api=False,
    )

    assert resumed[g._TYPE_CASE_QID_PLACEMENT_LEDGER_KEY] == ledger
    assert "_phase33_verified_host_map" not in resumed
    assert "_phase33_verified_host_map" not in resumed["types"][0]
    case = resumed["types"][0]["case_prompts"][0]
    assert case["owner_topic_id"] == "TOPIC-B"
    assert case["source_location_topic_ids"] == ["TOPIC-A"]


def _ownership_producer_fixture():
    graph = {
        "source_contract_hash": "SOURCE-PRODUCER-1",
        "metadata": {"chapter": "Cumulative methods"},
        "topics": [
            {"topic_id": "TOPIC-A", "title": "Foundations", "order": 1},
            {"topic_id": "TOPIC-B", "title": "Later Method", "order": 2},
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
                "text": "The later method is required to perform this task.",
            },
        ],
        "tasks": [{
            "task_id": "TASK-0001",
            "qid": "QINV-0001",
            "topic_id": "TOPIC-A",
            "subtopic_id": "",
        }],
    }
    canonical = {"blocks": [
        {
            "block_id": "BLK-A",
            "text": "The source introduces the foundational operation.",
        },
        {
            "block_id": "BLK-B",
            "text": "The later method is required to perform this task.",
        },
    ]}
    inventory = {"items": [{
        "qid": "QINV-0001",
        "source_kind": "short_answer",
        "raw_task": "Use the later method to complete the operation.",
        "topic_hint": "Foundations",
    }]}
    mined_types = {"types": [{
        "type_id": "TYPE-0001",
        "type_title": "Complete the cumulative operation",
        "task_pattern": "Use the required method.",
        "source_question_ids": ["QINV-0001"],
        "case_prompts": [{
            "case_id": "CASE-0001",
            "case_title": "Later-method application",
            "case_definition": "The later method is necessary.",
            "examples": [_example("QINV-0001")],
        }],
    }]}
    units = [{
        "_phase33_assignment_unit_id": "TYPE-0001",
        "type_id": "TYPE-0001",
        "type_title": "Complete the cumulative operation",
        "task_pattern": "Use the required method.",
        "source_question_ids": ["QINV-0001"],
        "case_prompts": copy.deepcopy(
            mined_types["types"][0]["case_prompts"]),
    }]
    return graph, canonical, inventory, mined_types, units


@pytest.mark.parametrize(
    "relationship_topic_order",
    [("TOPIC-A", "TOPIC-B"), ("TOPIC-B", "TOPIC-A")],
)
def test_type_case_ownership_producer_is_order_independent_and_replays_ledger(
    monkeypatch, relationship_topic_order,
):
    graph, canonical, inventory, mined_types, units = (
        _ownership_producer_fixture())
    monkeypatch.setattr(
        phase33, "_read_type_case_placement_cache",
        lambda **_kwargs: {"entries": {}},
    )
    monkeypatch.setattr(
        phase33, "_write_type_case_placement_cache",
        lambda *_args, **_kwargs: None,
    )
    provider_calls = []
    critic_calls = []

    def provider(payload):
        provider_calls.append(copy.deepcopy(payload))
        claim_id = payload["claims"][0]["claim_id"]
        rows = {
            "TOPIC-A": {
                "claim_id": claim_id,
                "topic_id": "TOPIC-A",
                "relationship_type": "CORE_TEACHING",
                "necessity": True,
                "evidence_block_ids": ["BLK-A"],
                "reason": "The foundational operation is directly taught.",
            },
            "TOPIC-B": {
                "claim_id": claim_id,
                "topic_id": "TOPIC-B",
                "relationship_type": "REQUIRED_LATER_METHOD",
                "necessity": True,
                "evidence_block_ids": ["BLK-B"],
                "reason": "The later method is required to perform the task.",
            },
        }
        return {
            "relationships": [
                copy.deepcopy(rows[topic_id])
                for topic_id in relationship_topic_order
            ],
        }

    def critic(payload):
        critic_calls.append(copy.deepcopy(payload))
        return {
            "verdict": "verified",
            "confidence": 0.999,
            "issues": [],
            "relationship_reviews": [{
                "relationship_id": row["relationship_id"],
                "verdict": "accepted",
                "evidence_supported": True,
                "necessity_supported": True,
                "direction_supported": True,
                "reason": "Exact evidence supports this dependency.",
            } for row in payload["proposed_relationships"]],
        }

    assert phase33._certify_type_case_ownership(
        g,
        mined_types=mined_types,
        inventory=inventory,
        units=units,
        graph=graph,
        canonical=canonical,
        provider=provider,
        critic=critic,
    ) is True
    assert len(provider_calls) == 1
    assert len(critic_calls) == 1

    inventory_ledger = inventory[g._TYPE_CASE_QID_PLACEMENT_LEDGER_KEY]
    mined_ledger = mined_types[g._TYPE_CASE_QID_PLACEMENT_LEDGER_KEY]
    assert inventory_ledger == mined_ledger
    assert inventory_ledger["certified"] is True
    material = copy.deepcopy(inventory_ledger)
    supplied_digest = material.pop("certificate_sha256")
    assert supplied_digest == phase3._sha256_json(material)
    assert g._valid_type_case_qid_placement_ledger(
        inventory_ledger) is inventory_ledger

    placement = inventory_ledger["placements"]["QINV-0001"]
    assert placement["source_location_topic_id"] == "TOPIC-A"
    assert placement["owner_topic_id"] == "TOPIC-B"
    assert placement["required_topic_ids"] == ["TOPIC-A", "TOPIC-B"]
    assert g._type_case_placement_digest(
        placement) == placement["placement_certificate_sha256"]

    def must_not_run(_payload):
        raise AssertionError("certified same-job replay called a model")

    assert phase33._certify_type_case_ownership(
        g,
        mined_types=mined_types,
        inventory=inventory,
        units=units,
        graph=graph,
        canonical=canonical,
        provider=must_not_run,
        critic=must_not_run,
    ) is True
    assert len(provider_calls) == 1
    assert len(critic_calls) == 1


def test_reconcile_certifies_before_grouping_and_rebuilds_split_units(
    monkeypatch,
):
    graph = {"source_contract_hash": "SOURCE-ORDER", "topics": []}
    records = [{"concept_title": "Existing concept"}]
    mined_types = {"types": [{"type_id": "TYPE-MIXED"}]}
    inventory = {"items": [{"qid": "QINV-0001"}]}
    events = []
    before = [{"_phase33_assignment_unit_id": "BEFORE-SPLIT"}]

    monkeypatch.setattr(
        phase33.phase3, "canonicalize_record_topics",
        lambda rows, _graph: copy.deepcopy(rows),
    )
    monkeypatch.setattr(
        phase33.phase31, "ground_concepts",
        lambda rows, **_kwargs: copy.deepcopy(rows),
    )
    monkeypatch.setattr(
        phase33.phase3, "annotate_mined_types",
        lambda value, _graph: copy.deepcopy(value),
    )

    calls = 0

    def stable_units(_generation, _mined_types, _graph):
        nonlocal calls
        calls += 1
        events.append(f"units-{calls}")
        return copy.deepcopy(before) if calls == 1 else []

    def certify(_generation, *, units, **_kwargs):
        events.append("certify")
        assert units == before
        return True

    monkeypatch.setattr(phase33, "_stable_assignment_units", stable_units)
    monkeypatch.setattr(phase33, "_certify_type_case_ownership", certify)

    assert phase33._reconcile_type_hosts(
        g,
        records,
        mined_types=mined_types,
        question_task_inventory=inventory,
        meta={},
        mmd_text="",
        method_anchors=[],
        graph=graph,
        canonical={},
    ) == records
    assert events == ["units-1", "certify", "units-2"]


def test_final_manifest_covers_normal_and_activity_qids_exactly_once():
    normal = _contract(
        "QINV-0001",
        source="TOPIC-A",
        owner="TOPIC-B",
        owner_title="Applications",
    )
    activity = _contract(
        "QINV-0002",
        source="TOPIC-A",
        owner="TOPIC-B",
        owner_title="Applications",
    )
    inventory = {"items": [
        {
            **_inventory_item("QINV-0001", normal),
            "source_kind": "short_answer",
            "raw_task": "Perform the certified application task.",
        },
        {
            **_inventory_item("QINV-0002", activity),
            "source_kind": "activity",
            "raw_task": "Carry out the certified classroom activity.",
        },
    ]}
    ledger = {
        "version": 2,
        "certified": True,
        "policy_version": placement_policy.POLICY_VERSION,
        "source_contract_hash": "SOURCE-1",
        "teaching_order_sha256": "teaching-order-sha",
        "placements": {
            "QINV-0001": copy.deepcopy(normal),
            "QINV-0002": copy.deepcopy(activity),
        },
    }
    ledger["certificate_sha256"] = phase3._sha256_json(ledger)
    inventory[g._TYPE_CASE_QID_PLACEMENT_LEDGER_KEY] = copy.deepcopy(ledger)
    raw_types = [{
        "type_id": "TYPE-0001",
        "type_title": "Apply the certified method",
        "source_question_ids": ["QINV-0001"],
        "case_prompts": [{
            "case_id": "CASE-0001",
            "case_title": "Ordinary application",
            "examples": [_example("QINV-0001")],
            "is_activity": False,
        }],
    }, {
        "type_id": "TYPE-0002",
        "type_title": "Perform the certified activity",
        "source_question_ids": ["QINV-0002"],
        "case_prompts": [{
            "case_id": "CASE-0002",
            "case_title": "Classroom activity",
            "examples": [_example("QINV-0002")],
            "is_activity": True,
        }],
    }]
    mined_types = {
        "types": g._annotate_mined_type_case_routes(raw_types, inventory),
        g._TYPE_CASE_QID_PLACEMENT_LEDGER_KEY: copy.deepcopy(ledger),
    }
    normal_record = {
        "topic": "Applications",
        "parent_concept": "Methods",
        "concept_title": "Certified application",
        "concept_details": "Description: Apply the method.",
        "_semantic_topic_id": "TOPIC-B",
    }
    normal_record = g._append_inventory_example_to_record(
        normal_record,
        g._inventory_task_text(inventory["items"][0]),
        inventory["items"][0],
    )
    activity_record = {
        "topic": "Applications",
        "parent_concept": "Methods",
        "concept_title": "Certified activity",
        "concept_details": "Description: Perform the classroom activity.",
        "_semantic_topic_id": "TOPIC-B",
        "_activity_hub_qids": ["QINV-0002"],
    }

    final, resolved = g._final_type_case_qid_host_manifests(
        [normal_record, activity_record],
        inventory,
        mined_types,
    )

    assert resolved == ledger
    manifested = {
        qid: entry
        for record in final
        for qid, entry in (
            record.get("_type_case_qid_host_placement_manifest", {})
            .get("placements", {})
            .items()
        )
    }
    assert set(manifested) == {"QINV-0001", "QINV-0002"}
    assert manifested["QINV-0001"]["host_disposition"] == (
        "type_case_example"
    )
    assert manifested["QINV-0002"]["host_disposition"] == (
        "activity_info_hub"
    )
    assert all(
        entry["host_topic_id"] == entry["owner_topic_id"] == "TOPIC-B"
        for entry in manifested.values()
    )
    assert manifested["QINV-0001"]["placement_contract"] == normal
    assert manifested["QINV-0002"]["placement_contract"] == activity

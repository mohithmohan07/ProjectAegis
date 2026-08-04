"""Behavioral coverage for certified Activity/Info Hub topic ownership."""
from __future__ import annotations

import copy

import pytest

from app.services import canonical_source_phase3 as phase3
from app.services import generation as g
from app.services import placement_policy


def _placement(qid: str, owner: str, owner_title: str) -> dict:
    claim_id = f"TASK-{qid}"
    relationship = placement_policy.TopicRelationship(
        claim_id=claim_id,
        topic_id=owner,
        relationship_type=placement_policy.RelationshipType.CORE_TEACHING,
        necessity=True,
        evidence_block_ids=(f"BLK-{owner}",),
    )
    value = {
        "version": 2,
        "certified": True,
        "policy_version": placement_policy.POLICY_VERSION,
        "teaching_order_sha256": "a" * 64,
        "source_contract_hash": "b" * 64,
        "claim_id": claim_id,
        "qid": qid,
        "normalized_claim": f"Complete the classroom task for {qid}.",
        "source_location_topic_id": "TOPIC-SOURCE",
        "source_location_topic_ids": ["TOPIC-SOURCE"],
        "owner_topic_id": owner,
        "owner_topic_title": owner_title,
        "required_topic_ids": [owner],
        "prerequisite_topic_ids": [],
        "reference_edges": [],
        "illustration_topic_ids": [],
        "topic_relationships": [{
            "relationship_id": relationship.relationship_id,
            "claim_id": claim_id,
            "topic_id": owner,
            "relationship_type": "CORE_TEACHING",
            "necessity": True,
            "evidence_block_ids": [f"BLK-{owner}"],
            "provider_reason": "The owner topic directly teaches the task.",
            "critic_verdict": "accepted",
        }],
    }
    value["placement_certificate_sha256"] = (
        g._type_case_placement_digest(value)
    )
    return value


def _inventory_item(
    qid: str,
    placement: dict,
    *,
    source_kind: str = "activity",
    activity_origin: bool = False,
) -> dict:
    return {
        "qid": qid,
        "source_kind": source_kind,
        "raw_task": f"Complete the classroom task for {qid}.",
        "topic_hint": "Physical Source Topic",
        "source_location_topic_id": "TOPIC-SOURCE",
        "owner_topic_id": placement["owner_topic_id"],
        "owner_topic_title": placement["owner_topic_title"],
        "_activity_origin": activity_origin,
        "_type_case_placement_contract": copy.deepcopy(placement),
    }


def _ledger(placements: dict[str, dict]) -> dict:
    value = {
        "version": 2,
        "certified": True,
        "policy_version": placement_policy.POLICY_VERSION,
        "teaching_order_sha256": "a" * 64,
        "source_contract_hash": "b" * 64,
        "placements": copy.deepcopy(placements),
    }
    value["certificate_sha256"] = phase3._sha256_json(value)
    return value


def _mined_types(inventory: dict, placements: dict[str, dict]) -> dict:
    raw_types = []
    for index, item in enumerate(inventory["items"], start=1):
        qid = item["qid"]
        raw_types.append({
            "type_id": f"TYPE-{index:04d}",
            "type_title": f"Classroom task {index}",
            "source_question_ids": [qid],
            "case_prompts": [{
                "case_id": f"CASE-{index:04d}",
                "case_title": f"Task case {index}",
                "examples": [{
                    "source_question_id": qid,
                    "example_prompt": g._inventory_task_text(item),
                }],
            }],
        })
    ledger = _ledger(placements)
    inventory[g._TYPE_CASE_QID_PLACEMENT_LEDGER_KEY] = copy.deepcopy(ledger)
    return {
        "types": g._annotate_mined_type_case_routes(raw_types, inventory),
        g._TYPE_CASE_QID_PLACEMENT_LEDGER_KEY: copy.deepcopy(ledger),
    }


def _record(topic_id: str, topic: str, concept: str) -> dict:
    return {
        "topic": topic,
        "parent_concept": "Classroom work",
        "concept_title": concept,
        "concept_details": "Description: Source-grounded classroom work.",
        "_semantic_topic_id": topic_id,
    }


def _ambiguous_owner_topic_setup() -> tuple[dict, dict, list[dict]]:
    placement = _placement(
        "QINV-0001", "TOPIC-OWNER", "Later Applications"
    )
    item = _inventory_item("QINV-0001", placement)
    item["raw_task"] = (
        "Compare the measured voltage and current, then explain whether "
        "their ratio remains constant."
    )
    task = item["raw_task"]
    inventory = {"items": [item]}
    mined = _mined_types(inventory, {"QINV-0001": placement})
    records = [
        {
            **_record(
                "TOPIC-OWNER", "Later Applications", "Electric current"
            ),
            "concept_details": (
                "Description: Electric current is the rate of charge flow. "
                "// Types: Type 01: Investigate measurements Case 01: "
                f"Copied placement Example 01: {task}"
            ),
        },
        {
            **_record(
                "TOPIC-OWNER",
                "Later Applications",
                "Testing the voltage-current relationship",
            ),
            "concept_details": (
                "Description: Compare measured voltage and current and test "
                "whether their ratio remains constant."
            ),
        },
    ]
    return inventory, mined, records


def test_pure_activity_uses_certified_owner_not_physical_topic():
    placement = _placement(
        "QINV-0001", "TOPIC-OWNER", "Later Applications"
    )
    inventory = {"items": [_inventory_item("QINV-0001", placement)]}
    mined = _mined_types(
        inventory, {"QINV-0001": placement}
    )
    records = [
        _record(
            "TOPIC-SOURCE", "Physical Source Topic", "Physical host"
        ),
        _record("TOPIC-OWNER", "Later Applications", "Owner host"),
    ]

    out = g._populate_activity_hubs_via_api(
        records, inventory, meta={}, mined_types=mined
    )

    assert g._activity_hub_locations(
        out, inventory["items"][0]
    ) == [1]
    assert not out[0].get("_activity_hub_qids")
    assert g._hub_inventory_contract_violations(out, inventory) == []
    certified = mined[g._PLACEMENT_CERTIFICATIONS_KEY]["hosts"]
    assert certified["QINV-0001"]["topic"] == "Later Applications"


def test_mixed_activity_owners_remain_isolated_with_same_physical_topic():
    first = _placement("QINV-0001", "TOPIC-OWNER-A", "Applications A")
    second = _placement("QINV-0002", "TOPIC-OWNER-B", "Applications B")
    inventory = {"items": [
        _inventory_item("QINV-0001", first),
        _inventory_item("QINV-0002", second),
    ]}
    mined = _mined_types(
        inventory,
        {"QINV-0001": first, "QINV-0002": second},
    )
    records = [
        _record(
            "TOPIC-SOURCE", "Physical Source Topic", "Physical host"
        ),
        _record("TOPIC-OWNER-A", "Applications A", "Owner A host"),
        _record("TOPIC-OWNER-B", "Applications B", "Owner B host"),
    ]

    out = g._populate_activity_hubs_via_api(
        records, inventory, meta={}, mined_types=mined
    )

    assert g._activity_hub_locations(out, inventory["items"][0]) == [1]
    assert g._activity_hub_locations(out, inventory["items"][1]) == [2]
    assert out[1]["_activity_hub_qids"] == ["QINV-0001"]
    assert out[2]["_activity_hub_qids"] == ["QINV-0002"]
    assert g._hub_inventory_contract_violations(out, inventory) == []


def test_assessable_activity_example_and_hub_share_certified_owner_row():
    placement = _placement(
        "QINV-0001", "TOPIC-OWNER", "Later Applications"
    )
    item = _inventory_item(
        "QINV-0001",
        placement,
        source_kind="short_answer",
        activity_origin=True,
    )
    inventory = {"items": [item]}
    mined = _mined_types(inventory, {"QINV-0001": placement})
    task = g._inventory_task_text(item)
    records = [
        {
            **_record(
                "TOPIC-SOURCE", "Physical Source Topic", "Physical host"
            ),
            "concept_details": (
                "Description: Physical source context. // Types: "
                "Type 01: Classroom task Case 01: Activity question "
                f"Example 01: {task}"
            ),
        },
        _record("TOPIC-OWNER", "Later Applications", "Owner host"),
    ]

    out = g._populate_activity_hubs_via_api(
        records, inventory, meta={}, mined_types=mined
    )

    assert g._rendered_inventory_example_locations(out, item) == [1]
    assert g._activity_hub_locations(out, item) == [1]
    assert g._activity_example_hub_alignment_violations(out, inventory) == []
    assert g._hub_inventory_contract_violations(out, inventory) == []

    finalized, resolved = g._final_type_case_qid_host_manifests(
        out, inventory, mined
    )
    manifest = finalized[1][
        "_type_case_qid_host_placement_manifest"
    ]["placements"]["QINV-0001"]
    assert resolved == inventory[g._TYPE_CASE_QID_PLACEMENT_LEDGER_KEY]
    assert manifest["owner_topic_id"] == "TOPIC-OWNER"
    assert manifest["host_topic_id"] == "TOPIC-OWNER"
    assert manifest["host_disposition"] == "activity_info_hub"


def test_invalid_declared_v2_activity_never_falls_back_to_physical_topic():
    placement = _placement(
        "QINV-0001", "TOPIC-OWNER", "Later Applications"
    )
    placement["owner_topic_id"] = "TOPIC-TAMPERED"
    item = _inventory_item("QINV-0001", placement)

    with pytest.raises(RuntimeError, match="type_case_owner_uncertified"):
        g._inventory_item_owner_topic(item)


def test_live_activity_without_v2_owner_never_uses_physical_topic(
    monkeypatch,
):
    item = {
        "qid": "QINV-0001",
        "source_kind": "activity",
        "topic_hint": "Physical Source Topic",
    }
    monkeypatch.setattr(g.config, "use_live_generation", lambda: True)

    with phase3.activate({"source_contract_hash": "SOURCE-LIVE"}):
        with pytest.raises(
            RuntimeError,
            match="no usable v2 placement contract",
        ):
            g._inventory_item_owner_topic(item)


def test_offline_legacy_activity_retains_physical_topic_fallback(
    monkeypatch,
):
    item = {
        "qid": "QINV-0001",
        "source_kind": "activity",
        "topic_hint": "Physical Source Topic",
    }
    monkeypatch.setattr(g.config, "use_live_generation", lambda: False)

    assert g._inventory_item_owner_topic(item) == (
        "",
        "Physical Source Topic",
    )


def test_topic_type_coverage_counts_certified_owner_not_physical_source():
    placement = _placement(
        "QINV-0001", "TOPIC-OWNER", "Later Applications"
    )
    item = _inventory_item(
        "QINV-0001",
        placement,
        source_kind="short_answer",
    )
    records = [
        _record(
            "TOPIC-SOURCE", "Physical Source Topic", "Physical concept"
        ),
        {
            **_record(
                "TOPIC-OWNER", "Later Applications", "Owner concept"
            ),
            "concept_details": (
                "Description: Apply the later method. // Types: "
                "Type 01: Later-method application Case 01: Required method "
                "Example 01: Complete the classroom task for QINV-0001."
            ),
        },
    ]

    assert g._inventory_topic_type_coverage_violations(
        records, {"items": [item]}
    ) == []

    records[1]["concept_details"] = "Description: No rendered Type."
    assert g._inventory_topic_type_coverage_violations(
        records, {"items": [item]}
    ) == [{
        "topic": "Later Applications",
        "inventory_items": 1,
        "topic_id": "TOPIC-OWNER",
    }]


def test_ambiguous_activity_host_rejected_by_independent_critic_never_certifies(
    monkeypatch,
):
    inventory, mined, records = _ambiguous_owner_topic_setup()
    calls: list[dict] = []
    responses = iter([
        {"placements": [{
            "qid": "QINV-0001",
            # Structurally allowed and plausible, but less specific.
            "concept_id": "CONCEPT-0001",
        }]},
        {"reviews": [{
            "qid": "QINV-0001",
            "concept_id": "CONCEPT-0001",
            "verdict": "reject",
            "confidence": 0.99,
            "reason": (
                "The second candidate directly teaches the measurement and "
                "constant-ratio investigation."
            ),
        }]},
    ])

    def fake_openai(system, user, **kwargs):
        calls.append({"system": system, "user": user, **kwargs})
        return next(responses)

    monkeypatch.setattr(g, "_openai_json", fake_openai)

    with pytest.raises(
        RuntimeError,
        match="did not certify every inventory item",
    ):
        g._populate_activity_hubs_via_api(
            records,
            inventory,
            meta=g._metadata(subject="Physics"),
            mined_types=mined,
            max_attempts=1,
        )

    assert [call["purpose"] for call in calls] == [
        "concept_detailing",
        "concept_validation",
    ]
    assert calls[0]["system"] != calls[1]["system"]
    assert "first-pass semantic proposal" in calls[0]["system"]
    assert "Independently review" in calls[1]["system"]
    assert inventory["items"][0]["raw_task"] in calls[1]["user"]
    assert "Electric current" in calls[1]["user"]
    assert "Testing the voltage-current relationship" in calls[1]["user"]
    assert '"concept_id": "CONCEPT-0001"' in calls[1]["user"]
    # The exact task is present once as source authority, never as circular
    # candidate teaching evidence copied from the wrong host's Type Example.
    assert calls[1]["user"].count(
        inventory["items"][0]["raw_task"]
    ) == 1
    assert '"teaching_description": "Electric current is the rate' in (
        calls[1]["user"]
    )
    assert '"concept_details"' not in calls[1]["user"]
    assert '"existing_activity_hub"' not in calls[1]["user"]
    assert mined[g._PLACEMENT_CERTIFICATIONS_KEY]["hosts"] == {}
    assert not any(
        record.get("_activity_hub_qids") for record in records
    )


def test_wrong_preexisting_live_v2_hub_cannot_certify_itself(monkeypatch):
    inventory, mined, records = _ambiguous_owner_topic_setup()
    item = inventory["items"][0]
    records[0]["concept_details"] += (
        " // Activity/Info Hub: "
        + g._compact_activity_hub_note(item)
    )
    g._mark_activity_hub_placement(records[0], item)
    responses = iter([
        {"placements": [{
            "qid": "QINV-0001",
            # The provider follows the stale, pre-existing location.
            "concept_id": "CONCEPT-0001",
        }]},
        {"reviews": [{
            "qid": "QINV-0001",
            "concept_id": "CONCEPT-0001",
            "verdict": "reject",
            "confidence": 0.99,
            "reason": (
                "The second candidate directly teaches the measurement and "
                "constant-ratio investigation; the pre-existing first host "
                "is less specific."
            ),
        }]},
    ])
    calls: list[str] = []

    def fake_openai(_system, _user, **kwargs):
        calls.append(kwargs["purpose"])
        return next(responses)

    monkeypatch.setattr(g, "_openai_json", fake_openai)

    with pytest.raises(
        RuntimeError,
        match="did not certify every inventory item",
    ):
        g._populate_activity_hubs_via_api(
            records,
            inventory,
            meta=g._metadata(subject="Physics"),
            mined_types=mined,
            max_attempts=1,
        )

    assert calls == ["concept_detailing", "concept_validation"]
    assert mined[g._PLACEMENT_CERTIFICATIONS_KEY]["hosts"] == {}


def test_ambiguous_activity_host_requires_provider_and_critic_acceptance(
    monkeypatch,
):
    inventory, mined, records = _ambiguous_owner_topic_setup()
    calls: list[str] = []
    responses = iter([
        {"placements": [{
            "qid": "QINV-0001",
            "concept_id": "CONCEPT-0002",
        }]},
        {"reviews": [{
            "qid": "QINV-0001",
            "concept_id": "CONCEPT-0002",
            "verdict": "accept",
            "confidence": 0.97,
            "reason": (
                "This candidate directly teaches comparing the measurements "
                "and testing the constant ratio."
            ),
        }]},
    ])

    def fake_openai(_system, _user, **kwargs):
        calls.append(kwargs["purpose"])
        return next(responses)

    monkeypatch.setattr(g, "_openai_json", fake_openai)

    out = g._populate_activity_hubs_via_api(
        records,
        inventory,
        meta=g._metadata(subject="Physics"),
        mined_types=mined,
        max_attempts=1,
    )

    assert calls == ["concept_detailing", "concept_validation"]
    assert g._activity_hub_locations(out, inventory["items"][0]) == [1]
    certification = mined[g._PLACEMENT_CERTIFICATIONS_KEY]["hosts"][
        "QINV-0001"
    ]
    assert certification["concept"] == (
        "Testing the voltage-current relationship"
    )
    assert certification["basis"] == "activity_host_provider_critic"


@pytest.mark.parametrize(
    "critic_response",
    [
        {"reviews": []},
        {"reviews": [{
            "qid": "QINV-0001",
            "concept_id": "CONCEPT-0001",
            "verdict": "accept",
            "confidence": 0.99,
            "reason": "Mismatched proposal identity.",
        }]},
        {"reviews": [{
            "qid": "QINV-UNKNOWN",
            "concept_id": "CONCEPT-0002",
            "verdict": "accept",
            "confidence": 0.99,
            "reason": "Invented QID.",
        }]},
        {"reviews": [{
            "qid": "QINV-0001",
            "concept_id": "CONCEPT-0002",
            "verdict": "accept",
            "confidence": 0.99,
            "reason": "First duplicate verdict.",
        }, {
            "qid": "QINV-0001",
            "concept_id": "CONCEPT-0002",
            "verdict": "accept",
            "confidence": 0.99,
            "reason": "Second duplicate verdict.",
        }]},
    ],
    ids=["missing", "mismatched-concept", "unknown-qid", "duplicate-qid"],
)
def test_malformed_activity_host_critic_fails_closed(
    monkeypatch, critic_response,
):
    inventory, mined, records = _ambiguous_owner_topic_setup()
    responses = iter([
        {"placements": [{
            "qid": "QINV-0001",
            "concept_id": "CONCEPT-0002",
        }]},
        critic_response,
    ])
    monkeypatch.setattr(
        g,
        "_openai_json",
        lambda *_args, **_kwargs: next(responses),
    )

    with pytest.raises(
        RuntimeError,
        match="did not certify every inventory item",
    ):
        g._populate_activity_hubs_via_api(
            records,
            inventory,
            meta=g._metadata(subject="Physics"),
            mined_types=mined,
            max_attempts=1,
        )

    assert mined[g._PLACEMENT_CERTIFICATIONS_KEY]["hosts"] == {}

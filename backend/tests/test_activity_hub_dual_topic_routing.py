"""Behavioral coverage for Activity/Info Hub owner-topic resolution.

The legacy post-81% hub-population chain (``_populate_activity_hubs_via_api``,
the placement-certification ledger, and the topic/type coverage defects) was
deleted by the phase-3 rewrite; hub binding is now the Host pass's job.  What
remains here is the surviving owner-scope resolver
``generation._inventory_item_owner_topic``: a certified v2 contract still
names the semantic owner, a tampered v2 declaration still fails closed, and an
item without any v2 contract yields provenance only (the printed topic title),
live or offline alike.
"""
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


def test_certified_v2_activity_uses_contract_owner_not_physical_topic():
    placement = _placement(
        "QINV-0001", "TOPIC-OWNER", "Later Applications"
    )
    item = _inventory_item("QINV-0001", placement)

    assert g._inventory_item_owner_topic(item) == (
        "TOPIC-OWNER",
        "Later Applications",
    )


def test_invalid_declared_v2_activity_never_falls_back_to_physical_topic():
    placement = _placement(
        "QINV-0001", "TOPIC-OWNER", "Later Applications"
    )
    placement["owner_topic_id"] = "TOPIC-TAMPERED"
    item = _inventory_item("QINV-0001", placement)

    with pytest.raises(RuntimeError, match="type_case_owner_uncertified"):
        g._inventory_item_owner_topic(item)


def test_live_activity_without_v2_contract_returns_provenance_only(
    monkeypatch,
):
    """Under the rewritten Phase 3 the Host pass owns placement.

    A live item that carries no v2 placement contract is no longer an
    integrity fault: its printed topic ships as provenance only (empty owner
    ID), exactly as in the offline case, and nothing raises.
    """
    item = {
        "qid": "QINV-0001",
        "source_kind": "activity",
        "topic_hint": "Physical Source Topic",
    }
    monkeypatch.setattr(g.config, "use_live_generation", lambda: True)

    with phase3.activate({"source_contract_hash": "SOURCE-LIVE"}):
        assert g._inventory_item_owner_topic(item) == (
            "",
            "Physical Source Topic",
        )


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

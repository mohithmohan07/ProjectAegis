"""Phase 3.2 must not decide ownership itself.

The stage that actually moves concepts previously chose ``topic_id`` freely.
These tests prove the replacement: the provider classifies typed
relationships, and deterministic code recomputes the owner and overrides the
proposal inside the same call — before parsing, caching or grounding ever
sees it.
"""
from __future__ import annotations

import pytest

from app.services import (
    canonical_source_phase32_topology_adjudication_contract as phase32,
)
from app.services import placement_policy as pp


def _payload():
    """Three cumulative topics; the graph is unavailable, so payload order."""
    return {
        "topics": [
            {"topic_id": "T-52", "title": "Arithmetic Progressions"},
            {"topic_id": "T-53", "title": "nth Term of an AP"},
            {"topic_id": "T-54", "title": "Sum of First n Terms"},
        ],
        "concepts": [{"concept_id": "TOPOLOGY-CONCEPT-0001"}],
    }


def _response(topic_id, relationships):
    return {
        "concepts": [{
            "concept_id": "TOPOLOGY-CONCEPT-0001",
            "decision": "keep",
            "confidence": 0.9,
            "reason": "r",
            "segments": [{
                "topic_id": topic_id,
                "concept_title": "t",
                "parent_concept": "p",
                "description": "Applying the sum formula needs a and d.",
                "achieving_mastery": "m",
                "keywords": [],
                "confidence": 0.9,
                "reason": "r",
                "topic_relationships": relationships,
            }],
        }]
    }


@pytest.fixture(autouse=True)
def _no_graph(monkeypatch):
    # Force the payload-order fallback so the test is independent of any
    # ambient Phase 3 session.
    monkeypatch.setattr(phase32.phase3, "active_graph", lambda: None)
    monkeypatch.setattr(
        phase32.progress, "log", lambda *_a, **_k: None)


def _segment(data):
    return data["concepts"][0]["segments"][0]


def test_provider_proposal_is_overridden_by_the_computed_owner():
    """Model proposes the earlier topic; policy moves it to the later one."""
    data = _response(
        "T-52",
        [
            {"topic_id": "T-54", "relationship_type": "CORE_TEACHING",
             "necessity": True, "evidence_block_ids": ["B54"], "reason": "r"},
            {"topic_id": "T-52", "relationship_type": "REQUIRED_PREREQUISITE",
             "necessity": True, "evidence_block_ids": ["B52"], "reason": "r"},
        ],
    )
    out = phase32._enforce_placement_policy(_payload(), data)
    segment = _segment(out)

    assert segment["topic_id"] == "T-54"
    assert segment["_placement_proposed_topic_id"] == "T-52"
    assert segment["_placement_certified"] is True
    assert segment["_placement_prerequisites"] == ["T-52"]


def test_retrospective_reference_does_not_drag_the_concept_forward():
    """A later topic that merely refers back must not take ownership."""
    data = _response(
        "T-52",
        [
            {"topic_id": "T-52", "relationship_type": "CORE_TEACHING",
             "necessity": True, "evidence_block_ids": ["B52"], "reason": "r"},
            {"topic_id": "T-54",
             "relationship_type": "RETROSPECTIVE_REFERENCE",
             "necessity": False, "evidence_block_ids": [], "reason": "r"},
        ],
    )
    segment = _segment(phase32._enforce_placement_policy(_payload(), data))

    assert segment["topic_id"] == "T-52"
    assert "_placement_proposed_topic_id" not in segment
    assert segment["_placement_reference_edges"] == [
        ["T-54", "RETROSPECTIVE_REFERENCE"]
    ]


def test_ownership_is_independent_of_relationship_order():
    forward = [
        {"topic_id": "T-53", "relationship_type": "REQUIRED_LATER_METHOD",
         "necessity": True, "evidence_block_ids": ["B53"], "reason": "r"},
        {"topic_id": "T-54", "relationship_type": "REQUIRED_LATER_METHOD",
         "necessity": True, "evidence_block_ids": ["B54"], "reason": "r"},
    ]
    a = _segment(phase32._enforce_placement_policy(
        _payload(), _response("T-53", forward)))
    b = _segment(phase32._enforce_placement_policy(
        _payload(), _response("T-53", list(reversed(forward)))))

    assert a["topic_id"] == b["topic_id"] == "T-54"


def test_unevidenced_necessity_cannot_move_the_concept():
    data = _response(
        "T-52",
        [
            {"topic_id": "T-52", "relationship_type": "CORE_TEACHING",
             "necessity": True, "evidence_block_ids": ["B52"], "reason": "r"},
            # Asserts a later method is required but cites nothing.
            {"topic_id": "T-54", "relationship_type": "REQUIRED_LATER_METHOD",
             "necessity": True, "evidence_block_ids": [], "reason": "r"},
        ],
    )
    assert _segment(
        phase32._enforce_placement_policy(_payload(), data)
    )["topic_id"] == "T-52"


def test_segment_without_certifiable_ownership_is_marked_not_invented():
    """No owning relationship: keep the proposal, flag it uncertified."""
    data = _response(
        "T-53",
        [
            {"topic_id": "T-52", "relationship_type": "REQUIRED_PREREQUISITE",
             "necessity": True, "evidence_block_ids": ["B52"], "reason": "r"},
        ],
    )
    segment = _segment(phase32._enforce_placement_policy(_payload(), data))

    assert segment["topic_id"] == "T-53"          # unchanged, not invented
    assert segment["_placement_certified"] is False
    assert "no certified owning" in segment["_placement_note"]


def test_unknown_topic_ids_are_discarded_rather_than_trusted():
    data = _response(
        "T-52",
        [
            {"topic_id": "T-52", "relationship_type": "CORE_TEACHING",
             "necessity": True, "evidence_block_ids": ["B52"], "reason": "r"},
            {"topic_id": "T-NOT-IN-CHAPTER",
             "relationship_type": "CORE_TEACHING",
             "necessity": True, "evidence_block_ids": ["BX"], "reason": "r"},
        ],
    )
    assert _segment(
        phase32._enforce_placement_policy(_payload(), data)
    )["topic_id"] == "T-52"


def test_schema_requires_typed_relationships_from_the_provider():
    schema = phase32._adjudication_schema(
        ["TOPOLOGY-CONCEPT-0001"], ["T-52", "T-54"])
    segment = (
        schema["schema"]["properties"]["concepts"]["items"]
        ["properties"]["segments"]["items"]
    )

    assert "topic_relationships" in segment["required"]
    rel = segment["properties"]["topic_relationships"]["items"]["properties"]
    assert rel["topic_id"]["enum"] == ["T-52", "T-54"]
    assert set(rel["relationship_type"]["enum"]) == {
        kind.value for kind in pp.RelationshipType
    }


def test_graph_block_order_is_preferred_over_payload_order(monkeypatch):
    """Teaching order comes from the sealed contract when available."""
    monkeypatch.setattr(
        phase32.phase3, "active_graph",
        lambda: {
            "source_contract_hash": "sc",
            # Declared reversed; block order is authoritative.
            "topics": [{"topic_id": "T-54", "title": "Sum"},
                       {"topic_id": "T-52", "title": "AP"}],
            "blocks": [{"block_id": "B52", "topic_id": "T-52", "order": 10},
                       {"block_id": "B54", "topic_id": "T-54", "order": 90}],
        },
    )
    order = phase32._teaching_order_for(_payload())

    assert order.rank("T-52") == 0
    assert order.rank("T-54") == 1
    assert order.latest(["T-52", "T-54"]) == "T-54"

"""Type/Case ownership must accept a split inventory leaf.

Phase 2.1 keeps the original canonical task objects and materializes
independently answerable children only in the production inventory, so
``QINV-0011`` stays in the semantic graph while ``QINV-0011.1`` and
``QINV-0011.2`` exist only as inventory rows.  Phase 3.3 looked each QID up in
the graph directly, so the first leaf ended the run with
``type_case_owner_uncertified: QID QINV-0011.1 has no sealed semantic
task/text`` -- a live stop after the Phase 3.1 placement boundary was cleared.
"""
from __future__ import annotations

import copy

import pytest

from app.services import generation
from app.services import canonical_source_phase33_preflight_contract as phase33


PARENT_IDENTITY = "2632021dc6708e609e36d2dc372df4449a18e84bb596b0204fdb610e094"


def _graph() -> dict:
    return {
        "topics": [{"topic_id": "TOPIC-0004", "title": "The Making of Italy"}],
        "tasks": [{
            "qid": "QINV-0011",
            "task_id": "TASK-00011",
            "topic_id": "TOPIC-0004",
            "identity_key": PARENT_IDENTITY,
            "display_prompt": "Look at Fig. 14(a). ... Examine Fig. 14(b). ...",
        }],
    }


def _leaf(number: int, **overrides) -> dict:
    item = {
        "qid": f"QINV-0011.{number}",
        "parent_qid": "QINV-0011",
        "_acsd_parent_qid": "QINV-0011",
        "_acsd_parent_identity_key": PARENT_IDENTITY,
        "raw_task": f"Sub-question {number} about Italian unification.",
        "topic_hint": "The Making of Germany and Italy",
    }
    item.update(overrides)
    return item


def _units() -> list[dict]:
    return [{
        "_phase33_assignment_unit_id": "UNIT-0001",
        "type_id": "TYPE-01",
        "type_title": "Map interpretation",
        "case_prompts": [{"case_id": "CASE-01"}],
        "source_question_ids": ["QINV-0011.1", "QINV-0011.2"],
    }]


def test_split_leaves_inherit_their_sealed_parent_task():
    inventory = {"items": [_leaf(1), _leaf(2)]}

    claims, rows = phase33._type_case_claim_rows(
        generation,
        units=_units(),
        inventory=inventory,
        graph=_graph(),
    )

    assert [claim["qid"] for claim in claims] == ["QINV-0011.1", "QINV-0011.2"]
    # Provenance comes from the parent task; the claim text stays the leaf's own.
    assert {claim["source_location_topic_id"] for claim in claims} == {
        "TOPIC-0004"
    }
    assert claims[0]["normalized_claim"] == (
        "Sub-question 1 about Italian unification."
    )
    assert rows["QINV-0011.1"]["source_location_topic_id"] == "TOPIC-0004"


def test_a_leaf_with_no_parent_task_still_fails_closed():
    inventory = {"items": [_leaf(1, parent_qid="", _acsd_parent_qid="QINV-9999")]}

    with pytest.raises(
        RuntimeError, match="QINV-0011.1 has no sealed semantic task/text"
    ):
        phase33._type_case_claim_rows(
            generation,
            units=_units(),
            inventory=inventory,
            graph={"topics": [], "tasks": []},
        )


def test_a_leaf_whose_sealed_parent_identity_disagrees_is_rejected():
    # Inheriting a parent task is provenance, not a convenience lookup: a leaf
    # that names a different sealed parent must not borrow this one's topic.
    inventory = {"items": [_leaf(1, _acsd_parent_identity_key="f" * 59)]}

    with pytest.raises(
        RuntimeError, match="does not match its sealed parent task identity"
    ):
        phase33._type_case_claim_rows(
            generation,
            units=_units(),
            inventory=inventory,
            graph=_graph(),
        )


def test_an_ordinary_unsplit_qid_is_unaffected():
    graph = _graph()
    inventory = {"items": [{
        "qid": "QINV-0011",
        "raw_task": "Look at Fig. 14(a) and Fig. 14(b).",
    }]}
    units = copy.deepcopy(_units())
    units[0]["source_question_ids"] = ["QINV-0011"]

    claims, _rows = phase33._type_case_claim_rows(
        generation, units=units, inventory=inventory, graph=graph
    )

    assert [claim["qid"] for claim in claims] == ["QINV-0011"]
    assert claims[0]["source_location_topic_id"] == "TOPIC-0004"

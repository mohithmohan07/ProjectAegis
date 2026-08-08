"""An explicitly unplaced task ships flagged instead of stopping the run.

Job 15 reached 100%-shipped only through the failure lane: the ownership
stage recorded compound task QINV-0016.5 as unplaced ("keeps its printed
topic … no owner was invented"), and then two downstream validators treated
that deliberate record as fatal — the Case renderer raised "declares
placement v2 without a complete certified owner", and the qid ledger
validator would have rejected any ledger carrying the unplaced row. These
tests pin the repaired seam: unplaced is a legal, flagged terminal state
end to end.
"""
from __future__ import annotations

from app.services import generation, placement_policy


def _unplaced_contract(qid: str) -> dict:
    return {
        "version": generation._TYPE_CASE_PLACEMENT_CONTRACT_VERSION,
        "certified": False,
        "basis": placement_policy.UNPLACED_BASIS,
        "qid": qid,
        "source_location_topic_id": "TOPIC-0005",
        "owner_topic_id": "TOPIC-0005",
        "rationale": "no certifiable ownership after per-claim re-asks",
    }


def test_declared_unplaced_contract_is_found_on_the_item():
    item = {"qid": "QINV-0016.5",
            "_type_case_placement_contract": _unplaced_contract("QINV-0016.5")}

    found = generation._declared_unplaced_type_case_contract(
        item, {}, {}, "QINV-0016.5")

    assert found is not None
    assert found["basis"] == placement_policy.UNPLACED_BASIS


def test_declared_unplaced_contract_matches_by_claim_map_and_ignores_others():
    contract = _unplaced_contract("")
    contract["qid_claim_ids"] = {"QINV-0016.5": "CLAIM-1"}
    case = {"_type_case_placement_contract": contract}

    assert generation._declared_unplaced_type_case_contract(
        {}, case, {}, "QINV-0016.5") is not None
    # A different qid is not covered by this record.
    assert generation._declared_unplaced_type_case_contract(
        {}, case, {}, "QINV-0001") is None
    # A certified-basis contract is never treated as an unplaced record.
    certified = dict(contract, basis=placement_policy.CERTIFIED_BASIS)
    assert generation._declared_unplaced_type_case_contract(
        {}, {"_type_case_placement_contract": certified}, {}, "QINV-0016.5",
    ) is None


def test_ledger_accepts_an_unplaced_row_and_rejects_garbage():
    assert generation._usable_type_case_ledger_placement(
        _unplaced_contract("QINV-0016.5"))
    assert not generation._usable_type_case_ledger_placement(
        {"basis": placement_policy.UNPLACED_BASIS})  # no qid
    assert not generation._usable_type_case_ledger_placement(
        {"qid": "QINV-0001"})  # neither certified nor explicitly unplaced
    assert not generation._usable_type_case_ledger_placement("junk")


def test_case_rendering_ships_an_unplaced_qid_by_its_printed_topic():
    """The exact job 15 stop: declared v2 + no certified owner + unplaced."""
    inventory = {"items": [{
        "qid": "QINV-0016.5",
        "source_kind": "exercise",
        "raw_task": "A compound question spanning five concepts.",
        "topic_hint": "Visualising the Nation",
        "_type_case_placement_contract": _unplaced_contract("QINV-0016.5"),
    }]}
    types = [{
        "type_id": "TYPE-0008",
        "type_title": "Compound review",
        "case_prompts": [{
            "case_id": "CASE-0001",
            "case_title": "Compound case",
            "examples": [{
                "source_question_id": "QINV-0016.5",
                "example_prompt": "A compound question spanning five concepts.",
            }],
        }],
    }]

    routed = generation._annotate_mined_type_case_routes(types, inventory)

    # No RuntimeError — and the case survived, routed by provenance.
    assert routed and routed[0].get("case_prompts")

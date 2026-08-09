"""Golden gate 3: Assemble projects the settled+hosted state, house format.

Runs the full replayed chain — Settle from job 23's envelope, Host from
its certified maps, then the deterministic Assemble — and checks the
projection: Types embedded in concept_details in the house ``// Types:``
shape, every certified QID routed to its host row, every inventory item
accounted for, and every row still consumable by the publication chain.
"""
from __future__ import annotations

import re

import pytest

from tests import test_phase3_host_golden as host_golden
from tests import test_phase3_settle_golden as settle_golden
from app.services.phase3 import assemble as assemble_mod
from app.services.phase3 import host as host_mod
from app.services.phase3 import kernel, settle


def _normal(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


@pytest.fixture(scope="module")
def golden_envelope():
    return settle_golden.envelope_mod.load(
        settle_golden.GOLDEN / "rne_envelope.json"
    )


@pytest.fixture(scope="module")
def assembled(golden_envelope):
    import json

    golden_rows = json.loads(
        (settle_golden.GOLDEN / "rne_settled_rows.json").read_text(
            encoding="utf-8"
        )
    )["records"]
    golden_hosts = json.loads(
        (settle_golden.GOLDEN / "rne_host_maps.json").read_text(
            encoding="utf-8"
        )
    )
    mapping = settle_golden._replay_map(golden_envelope, golden_rows)
    topology, grounding, analysis, critic = settle_golden._providers(mapping)
    settled = settle.settle(
        golden_envelope,
        topology_provider=topology,
        grounding_provider=grounding,
        analysis_provider=analysis,
        critic=critic,
        store=kernel.DecisionStore(),
    )
    hosts = host_mod.host(
        golden_envelope,
        settled,
        provider=host_golden._replay_provider(golden_hosts, settled),
        critic=host_golden._verified_critic,
        store=kernel.DecisionStore(),
    )
    return (
        assemble_mod.assemble(golden_envelope, settled, hosts),
        golden_hosts,
        settled,
    )


def test_types_are_embedded_in_the_house_format(assembled, golden_envelope):
    result, golden_hosts, _settled = assembled
    types, cases = assemble_mod._type_catalog(golden_envelope)

    # QINV-0005's certified host carries TYPE-0001::CASE-0002.
    golden = golden_hosts["qid_map"]["QINV-0005"]
    row = next(
        row for row in result["rows"]
        if _normal(row["concept_title"]) == _normal(golden["concept_title"])
    )
    details = row["concept_details"]
    assert " // Types: " in details
    assert types["TYPE-0001"]["title"] in details
    assert cases[("TYPE-0001", "CASE-0002")]["title"] in details
    assert "Example:" in details
    # Order: Description, Mastery, Types, then learner analysis.
    assert details.index("// Types:") < details.index("Misconception/")
    assert re.search(r"Type 01: ", details)
    assert "TYPE-0001::CASE-0002::0002" in row[
        "_aegis_release_type_case_routes"
    ]


def test_every_certified_qid_routes_to_its_host_row(assembled):
    result, golden_hosts, _settled = assembled
    rows_by_qid = {}
    for row in result["rows"]:
        for qid in row.get("_aegis_release_qids") or []:
            rows_by_qid[qid] = row
    for qid, golden in golden_hosts["qid_map"].items():
        row = rows_by_qid.get(qid)
        assert row is not None, f"{qid} reached no row"
        assert _normal(row["concept_title"]) == _normal(
            golden["concept_title"]
        ), qid


def test_every_inventory_item_is_accounted_for(assembled, golden_envelope):
    result, _golden_hosts, _settled = assembled
    coverage = result["coverage"]
    inventory_qids = {
        str(item.get("qid"))
        for item in golden_envelope["inventory"]["items"]
        if item.get("qid")
    }
    accounted = set(coverage["routed_qids"]) | {
        row["qid"] for row in coverage["unrouted"]
    }
    assert accounted == inventory_qids
    assert coverage["items"] == len(inventory_qids)
    # Nothing vanishes silently: unrouted items say what they are.
    assert all(row["qid"] for row in coverage["unrouted"])


def test_rows_stay_consumable_by_the_publication_chain(assembled):
    result, _golden_hosts, settled = assembled
    assert len(result["rows"]) == len(settled)  # replay creates no hosts
    for row in result["rows"]:
        assert _normal(row["topic"]), row["concept_title"]
        assert _normal(row["concept_title"])
        assert str(row["concept_details"]).startswith("Description:")


def test_unhosted_rows_carry_no_types_section(assembled):
    result, _golden_hosts, _settled = assembled
    unhosted = [
        row for row in result["rows"]
        if not row.get("_aegis_release_type_case_routes")
    ]
    assert unhosted
    assert all(
        "// Types:" not in row["concept_details"] for row in unhosted
    )


def test_assemble_is_deterministic(assembled, golden_envelope):
    result, golden_hosts, settled = assembled
    hosts = host_mod.host(
        golden_envelope,
        settled,
        provider=host_golden._replay_provider(golden_hosts, settled),
        critic=host_golden._verified_critic,
        store=kernel.DecisionStore(),
    )
    again = assemble_mod.assemble(golden_envelope, settled, hosts)
    assert again == result


def test_a_host_entry_for_a_missing_row_is_a_hard_error(
    assembled, golden_envelope,
):
    _result, _golden_hosts, settled = assembled
    broken = {
        "host_map": {
            "TYPE-9999::CASE-9999::0001": {
                "concept_title": "A Row That Does Not Exist",
                "parent_concept": "Nowhere",
                "topic_id": "TOPIC-0001",
                "decision": "existing",
                "confidence": 0.99,
            }
        },
        "qid_map": {},
        "new_concepts": [],
    }
    with pytest.raises(assemble_mod.AssemblyError, match="does not exist"):
        assemble_mod.assemble(golden_envelope, settled, broken)

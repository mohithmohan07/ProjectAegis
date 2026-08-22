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

from tests import test_phase3_analyse as analyse_golden
from tests import test_phase3_host_golden as host_golden
from tests import test_phase3_settle_golden as settle_golden
from app.services.phase3 import analyse as analyse_mod
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
    golden_analysis = json.loads(
        (settle_golden.GOLDEN / "rne_analysis.json").read_text(
            encoding="utf-8"
        )
    )
    analysis = analyse_mod.analyse(
        golden_envelope,
        [*settled, *(hosts.get("new_concepts") or [])],
        provider=analyse_golden.analyse_replay_provider(golden_analysis),
        critic=analyse_golden._verified_critic,
        store=kernel.DecisionStore(),
    )
    return (
        assemble_mod.assemble(
            golden_envelope, settled, hosts, None, analysis
        ),
        golden_hosts,
        settled,
        analysis,
    )


def test_types_are_embedded_in_the_house_format(assembled, golden_envelope):
    result, golden_hosts, _settled, _analysis = assembled
    types, cases = assemble_mod._type_catalog(golden_envelope)

    # Per-question rendering: every Example appears on the row its own
    # question was placed on, so the visible Types text always agrees
    # with qid routing (reviewers found whole-Case pooling contradicted
    # correct placements). CASE-0002's title renders wherever one of its
    # examples' questions landed.
    case_qids = [
        example["qid"]
        for example in cases[("TYPE-0001", "CASE-0002")]["examples"]
        if example["qid"]
    ]
    assert case_qids
    destination = _normal(
        golden_hosts["qid_map"][case_qids[0]]["concept_title"]
    )
    row = next(
        row for row in result["rows"]
        if _normal(row["concept_title"]) == destination
    )
    details = row["concept_details"]
    assert " // Types: " in details
    assert types["TYPE-0001"]["title"] in details
    assert cases[("TYPE-0001", "CASE-0002")]["title"] in details
    # Order: Description, Mastery, Types, then learner analysis.
    assert details.index("// Types:") < details.index("Misconception/")
    # Chapter-wide continuous numbering is applied by the deposit-parity
    # pipeline in ROW order, so this host carries a numbered Type whose
    # ordinal depends on its position, not on the taxonomy id.
    assert re.search(r"Type \d{2}: ", details)
    assert any(
        unit.startswith("TYPE-0001::CASE-0002")
        for unit in row["_aegis_release_type_case_routes"]
    )
    # The rendered example text lives ONLY on its question's destination.
    example_prompt = cases[("TYPE-0001", "CASE-0002")]["examples"][0][
        "prompt"
    ]
    carriers = [
        _normal(out_row["concept_title"])
        for out_row in result["rows"]
        if example_prompt[:80] in str(out_row["concept_details"])
    ]
    assert carriers == [destination]


def test_every_certified_qid_routes_to_its_host_row(assembled):
    result, golden_hosts, _settled, _analysis = assembled
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
    result, _golden_hosts, _settled, _analysis = assembled
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
    result, _golden_hosts, settled, _analysis = assembled
    assert len(result["rows"]) == len(settled)  # replay creates no hosts
    for row in result["rows"]:
        assert _normal(row["topic"]), row["concept_title"]
        assert _normal(row["concept_title"])
        assert str(row["concept_details"]).startswith("Description:")


def test_types_sections_do_not_sprawl_beyond_hosts(assembled):
    """Types stay on their host rows. The deposit-parity pipeline may
    relocate a small amount of inventory-owned content onto a non-host
    row (its coverage repair), but Types never sprawl broadly."""
    result, _golden_hosts, _settled, _analysis = assembled
    unhosted = [
        row for row in result["rows"]
        if not row.get("_aegis_release_type_case_routes")
    ]
    assert unhosted
    unhosted_with_types = [
        row for row in unhosted
        if "// Types:" in row["concept_details"]
    ]
    assert len(unhosted_with_types) <= 2, [
        row["concept_title"] for row in unhosted_with_types
    ]


def test_assemble_is_deterministic(assembled, golden_envelope):
    result, golden_hosts, settled, analysis = assembled
    hosts = host_mod.host(
        golden_envelope,
        settled,
        provider=host_golden._replay_provider(golden_hosts, settled),
        critic=host_golden._verified_critic,
        store=kernel.DecisionStore(),
    )
    again = assemble_mod.assemble(
        golden_envelope, settled, hosts, None, analysis
    )
    assert again == result


def test_assemble_records_zero_model_usage(
    assembled, golden_envelope, monkeypatch,
):
    """The stage labelled deterministic must stay a zero-provider boundary."""
    from app.services import generation, openai_usage, progress

    result, golden_hosts, settled, analysis = assembled
    hosts = host_mod.host(
        golden_envelope,
        settled,
        provider=host_golden._replay_provider(golden_hosts, settled),
        critic=host_golden._verified_critic,
        store=kernel.DecisionStore(),
    )
    original_mastery = generation._ensure_mastery_lines_via_api
    original_coverage = generation._enforce_rendered_inventory_coverage

    def deterministic_mastery(records, *, meta, use_api=True):
        assert use_api is False
        return original_mastery(records, meta=meta, use_api=use_api)

    def deterministic_coverage(
        records, inventory, mined_types=None, *, fixer=None, fixer_store=None,
    ):
        assert fixer is None
        assert fixer_store is None
        return original_coverage(
            records,
            inventory,
            mined_types,
            fixer=fixer,
            fixer_store=fixer_store,
        )

    monkeypatch.setattr(
        generation, "_ensure_mastery_lines_via_api", deterministic_mastery,
    )
    monkeypatch.setattr(
        generation,
        "_enforce_rendered_inventory_coverage",
        deterministic_coverage,
    )

    with openai_usage.track():
        progress.step(
            "Phase 3 — Assemble: embedding Types and routing QIDs "
            "(deterministic)"
        )
        again = assemble_mod.assemble(
            golden_envelope, settled, hosts, None, analysis
        )
        usage = openai_usage.console_summary()

    assert again == result
    assert usage["request_count"] == 0
    assert usage["total_tokens"] == 0
    assert usage["stages"] == []


def test_a_host_entry_for_a_missing_row_is_a_hard_error(
    assembled, golden_envelope,
):
    _result, _golden_hosts, settled, _analysis = assembled
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

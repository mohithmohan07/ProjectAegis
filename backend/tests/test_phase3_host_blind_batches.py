"""Q68: Host re-decides its blind ``create_new`` units with every batch visible.

``phase3.host.host`` certifies units in parallel batches over ONE concept
payload built before any batch returns, so a batch never sees another
batch's creation and three same-meaning concepts were minted for Triangles
(the reviewers' corrections catalogue). Under generation-quality v3 every
unit whose first-pass verdict was create_new is re-decided once more,
sequentially, with every first-pass creation in ``settled_concepts``; the
first pass — payloads, keys, rows — is untouched, so every run stamped v2 or
earlier replays its single blind pass byte for byte.
"""
from __future__ import annotations

import copy
import json

import pytest

from app.services import generation_quality_policy as quality
from app.services.phase3 import envelope as envelope_mod
from app.services.phase3 import host as host_mod
from app.services.phase3 import kernel
from tests import test_phase3_host_golden as golden

U1 = "TYPE-0001::CASE-0001::0001"
U2 = "TYPE-0002::CASE-0009::0003"
T1 = "Similarity as a Basis for Indirect Measurement"
T2 = "Similarity-based Indirect Measurement"


@pytest.fixture(scope="module")
def env() -> dict:
    return envelope_mod.load(golden.GOLDEN / "rne_envelope.json")


@pytest.fixture(scope="module")
def settled_rows() -> list[dict]:
    return json.loads(
        (golden.GOLDEN / "rne_settled_rows.json").read_text(encoding="utf-8")
    )["records"]


@pytest.fixture(scope="module")
def golden_hosts() -> dict:
    return json.loads(
        (golden.GOLDEN / "rne_host_maps.json").read_text(encoding="utf-8")
    )


def _stamped(env: dict, version: str) -> dict:
    stamped = copy.deepcopy(env)
    stamped["metadata"][quality.KEY] = version
    stamped["envelope_sha256"] = envelope_mod.seal_sha256(stamped)
    return stamped


def _creation(title: str) -> dict:
    return {
        "concept_title": title,
        "parent_concept": "Similar Triangles",
        "_semantic_topic_id": "TOPIC-0001",
        "concept_details": (
            "Description: Two similar triangles have proportional sides, so an "
            "unreachable length is found from a measurable one.\n"
            "Achieving Mastery: Find an unreachable height from a similar "
            "triangle's measured sides."
        ),
        "keywords": "similarity, indirect measurement",
        "source_block_ids": ["BLK-00001"],
    }


def _creating_provider(golden_hosts, settled_rows, *, resolve_to=T1, calls=None):
    """The golden replay, except two units in different batches each create a
    same-meaning concept blind; on the resolution request both name one."""
    base = golden._replay_provider(golden_hosts, settled_rows)
    titles = {U1: T1, U2: T2}

    def provider(request: dict) -> dict:
        if calls is not None:
            calls.append(copy.deepcopy(request))
        response = base(request)
        if request.get("stage") != "host":
            return response
        resolving = "resolved_units" in request
        for assignment in response["assignments"]:
            unit_id = assignment["unit_id"]
            if unit_id not in titles:
                continue
            qids = list(assignment.get("qid_placements") or {})
            if resolving:
                host = resolve_to
                assignment.update({
                    "decision": "existing",
                    "host_concept_title": host,
                    "reason": "one created row teaches both units' idea",
                })
                assignment.pop("new_concept", None)
            else:
                host = titles[unit_id]
                assignment.update({
                    "decision": "create_new",
                    "new_concept": _creation(host),
                    "reason": "no settled row hosts indirect measurement",
                })
                assignment.pop("host_concept_title", None)
            assignment["qid_placements"] = {
                qid: {
                    "falls_under": [host],
                    "destination_concept_title": host,
                    "reason": "single-concept question",
                }
                for qid in qids
            }
        return response

    return provider


def _unit_qids(env: dict) -> dict[str, list[str]]:
    return {
        str(unit["unit_id"]): list(unit["qids"])
        for unit in host_mod.derive_units(env)
    }


def test_a_v3_run_re_decides_creating_units_with_every_batches_creation_visible(
    env, settled_rows, golden_hosts,
):
    calls: list[dict] = []
    v3 = _stamped(env, quality.V3)
    result = host_mod.host(
        v3, settled_rows,
        provider=_creating_provider(golden_hosts, settled_rows, calls=calls),
        critic=golden._verified_critic, store=kernel.DecisionStore(),
    )
    first_pass = [c for c in calls if c.get("stage") == "host" and "resolved_units" not in c]
    resolution = [c for c in calls if c.get("stage") == "host" and "resolved_units" in c]
    assert len(resolution) == 1
    request = resolution[0]
    assert [u["unit_id"] for u in request["units"]] == [U1, U2]
    assert request["units"][0]["first_pass_created"]["concept_title"] == T1
    assert request["units"][0]["first_pass_created"]["created_by_host_batch"] == "units#0"
    assert request["units"][1]["first_pass_created"]["concept_title"] == T2
    assert request["units"][1]["first_pass_created"]["created_by_host_batch"] == "units#8"
    created = [
        row for row in request["settled_concepts"]
        if row.get("_source_grounding_contract") == "api-created-missing-type-host"
    ]
    assert {row["concept_title"] for row in created} == {T1, T2}
    assert {row["created_for_unit"] for row in created} == {U1, U2}
    assert "complete source-grounded concept. RESOLUTION PASS:" in request["rules"]
    assert "Surface similarity is NOT ownership" in request["rules"]
    assert request["resolved_units"] == []
    for call in first_pass:
        assert "resolved_units" not in call
        assert all("first_pass_created" not in u for u in call["units"])
        assert "RESOLUTION PASS" not in call["rules"]

    assert [row["concept_title"] for row in result["new_concepts"]] == [T1]
    kept = result["new_concepts"][0]
    assert kept["_source_grounding_contract"] == "api-created-missing-type-host"
    qids = _unit_qids(env)
    for unit_id in (U1, U2):
        entry = result["host_map"][unit_id]
        assert entry["concept_title"] == T1 and entry["topic_id"] == "TOPIC-0001"
        assert entry["decision"] == "existing"
        for qid in qids[unit_id]:
            assert result["qid_map"][qid]["concept_title"] == T1
    audit_1 = result["host_map"][U1]["host_resolution"]
    assert audit_1["policy_version"] == host_mod.RESOLUTION_POLICY_VERSION
    assert audit_1["first_pass_batch"] == "units#0"
    assert audit_1["first_pass_retained"] is True
    assert audit_1["decision"] == "existing"
    assert audit_1["first_pass_created"]["concept_title"] == T1
    audit_2 = result["host_map"][U2]["host_resolution"]
    assert audit_2["first_pass_batch"] == "units#8"
    assert audit_2["first_pass_retained"] is False
    assert audit_2["first_pass_created"]["concept_title"] == T2
    flag_2 = [f for f in result["host_map"][U2]["review_flags"] if "every batch's creation visible" in f]
    assert flag_2 and "retired into this unit's audit" in flag_2[0]
    assert any(
        "retired into this unit's audit" in f
        for f in result["qid_map"][qids[U2][0]]["review_flags"]
    )
    assert sum(1 for e in result["host_map"].values() if "host_resolution" in e) == 2
    assert len(result["host_map"]) == len(host_mod.derive_units(env))


@pytest.mark.parametrize("version", [None, "v2"])
def test_a_pre_v3_run_keeps_its_single_blind_pass_byte_for_byte(
    env, settled_rows, golden_hosts, version,
):
    target = env if version is None else _stamped(env, quality.V2)
    calls: list[dict] = []
    result = host_mod.host(
        target, settled_rows,
        provider=_creating_provider(golden_hosts, settled_rows, calls=calls),
        critic=golden._verified_critic, store=kernel.DecisionStore(),
    )
    host_calls = [c for c in calls if c.get("stage") == "host"]
    assert all("resolved_units" not in c for c in host_calls)
    assert [row["concept_title"] for row in result["new_concepts"]] == [T1, T2]
    assert not any("host_resolution" in e for e in result["host_map"].values())
    assert not any("host_resolution" in e for e in result["qid_map"].values())

    v3_calls: list[dict] = []
    host_mod.host(
        _stamped(env, quality.V3), settled_rows,
        provider=_creating_provider(golden_hosts, settled_rows, calls=v3_calls),
        critic=golden._verified_critic, store=kernel.DecisionStore(),
    )
    v3_first = [c for c in v3_calls if c.get("stage") == "host" and "resolved_units" not in c]
    assert len(host_calls) == len(v3_first)
    assert all("RESOLUTION PASS" not in c["rules"] for c in host_calls)
    if version is not None:
        # A stamped run's first-pass rules are byte-identical across v2 and
        # v3 (the unstamped golden envelope carries no quality instruction
        # suffix at all, so only the stamped pair is comparable).
        assert {c["rules"] for c in host_calls} == {c["rules"] for c in v3_first}


def test_the_resolution_pass_replays_for_free_and_identically(
    env, settled_rows, golden_hosts,
):
    v3 = _stamped(env, quality.V3)
    provider = _creating_provider(golden_hosts, settled_rows)
    calls = {"n": 0}

    def counted(request: dict) -> dict:
        calls["n"] += 1
        return provider(request)

    store = kernel.DecisionStore()
    first = host_mod.host(
        v3, settled_rows, provider=counted, critic=golden._verified_critic, store=store,
    )
    after_first = calls["n"]
    second = host_mod.host(
        v3, settled_rows, provider=counted, critic=golden._verified_critic, store=store,
    )
    assert calls["n"] == after_first
    assert second == first


def test_re_minting_a_first_pass_creation_under_the_same_title_is_refused(
    env, settled_rows, golden_hosts, monkeypatch,
):
    monkeypatch.setenv("AEGIS_PHASE3_DECISION_WORKERS", "1")
    base = _creating_provider(golden_hosts, settled_rows)

    def re_minting(request: dict) -> dict:
        response = base(request)
        if request.get("stage") == "host" and "resolved_units" in request:
            for assignment in response["assignments"]:
                if assignment["unit_id"] == U2:
                    assignment.update({
                        "decision": "create_new",
                        "new_concept": _creation(T1),
                    })
                    assignment.pop("host_concept_title", None)
        return response

    with pytest.raises(kernel.ContractError) as failed:
        host_mod.host(
            _stamped(env, quality.V3), settled_rows,
            provider=re_minting, critic=golden._verified_critic,
            store=kernel.DecisionStore(),
        )
    assert "duplicates an existing settled concept title" in str(failed.value)
    assert "units#resolve#0" in str(failed.value)


def test_a_resolution_that_keeps_both_creations_retires_nothing(
    env, settled_rows, golden_hosts,
):
    """The model may decide the two created rows are distinct: each unit
    stays on its own creation, both rows ship, both audits say retained."""
    base = _creating_provider(golden_hosts, settled_rows)
    own = {U1: T1, U2: T2}

    def keeping(request: dict) -> dict:
        response = base(request)
        if request.get("stage") == "host" and "resolved_units" in request:
            for assignment in response["assignments"]:
                unit_id = assignment["unit_id"]
                if unit_id in own:
                    assignment["host_concept_title"] = own[unit_id]
                    for placement in assignment["qid_placements"].values():
                        placement["falls_under"] = [own[unit_id]]
                        placement["destination_concept_title"] = own[unit_id]
        return response

    result = host_mod.host(
        _stamped(env, quality.V3), settled_rows,
        provider=keeping, critic=golden._verified_critic,
        store=kernel.DecisionStore(),
    )
    assert [row["concept_title"] for row in result["new_concepts"]] == [T1, T2]
    assert result["host_map"][U1]["host_resolution"]["first_pass_retained"] is True
    assert result["host_map"][U2]["host_resolution"]["first_pass_retained"] is True
    assert result["host_map"][U2]["concept_title"] == T2

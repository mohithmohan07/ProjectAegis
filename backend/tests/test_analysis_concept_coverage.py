"""Corrections 2.0: model-authored pairs on every new ordinary concept.

All providers here are offline recordings. These regressions exercise the
real inventory/allot/coverage/stamping seams, durable replay, and Fixer path.
"""
from __future__ import annotations

import copy
import hashlib
import json

import pytest

from app.services import analysis_correction_policy as policy
from app.services import generation
from app.services.phase3 import analyse, kernel, preanalyse
from tests import test_phase3_analyse as post_fixtures
from tests import test_phase3_preanalyse as pre_fixtures
from tests import test_phase3_premap as premap_fixtures
from tests import test_phase3_settle_golden as fixtures


@pytest.fixture
def env():
    envelope = fixtures.envelope_mod.load(fixtures.GOLDEN / "rne_envelope.json")
    envelope["metadata"][policy.KEY] = policy.VERSION
    envelope["envelope_sha256"] = fixtures.envelope_mod.seal_sha256(envelope)
    return envelope


PAIRS = {
    "Rectilinear Propagation Of Light": {
        "kind": "misconception",
        "text": "Light bends around corners on its own in air.",
        "correction": "Light travels in straight lines in air.",
        "evidence": "The target concept's Description states straight-line travel.",
        "rationale": "This belief misapplies the straight-line model.",
    },
    "Reflection From Plane Mirrors": {
        "kind": "misconception",
        "text": "The image in a plane mirror can be caught on a screen.",
        "correction": "A plane mirror forms a virtual image that cannot be caught on a screen.",
        "evidence": "The target concept's Description identifies the image as virtual.",
        "rationale": "This distinguishes a virtual image from a real image.",
    },
    "Reading a Map": {
        "kind": "misconception",
        "text": "A colour always means the same thing on every historical map.",
        "correction": "Read the key of the particular map to find what its colours mean.",
        "evidence": "PR-0002 and PRC-0002 describe reading a map against its own key.",
        "rationale": "A familiar colour convention can be wrongly transferred to another map.",
    },
}


def _coverage(request):
    return {"items": [copy.deepcopy(PAIRS[request["target_concept"]["concept_title"]])]}


def assert_post_analysis_policy_replay(monkeypatch, *, historical_stamp):
    """Shared legacy pass guard for the original capture/map slice tests.

    Keys and system digests were reproduced against b0092d1's Analyse module,
    using this same golden envelope and empty recorded chapter inventory.
    Versioned coverage may import Pre helpers; it must not change paid legacy
    identities, replay, or live author/critic instructions.
    """
    frozen = {
        None: (
            "5054674b64d28cb94dabc3936ab124675ea284023fe7a9517a6bf41a86d4e91c",
            "d46f57e45db9e7b2390a90c28936d5156ac55f4ecd2f1f5896f4e1b5a2aae2eb",
        ),
        policy.VERSION_V1: (
            "f85af80fecce1eaf97563dfaa3a5aa7a7d20d8feb32afcb5fc0f496583f22cce",
            "21d71404b0714c906567e4a72fa516d4e8218d5d9c2cfbe2559557c5db15a3c5",
        ),
    }
    decision_key, systems_digest = frozen[historical_stamp]
    old_env = fixtures.envelope_mod.load(fixtures.GOLDEN / "rne_envelope.json")
    old_env["metadata"].pop(policy.KEY, None)
    if historical_stamp:
        old_env["metadata"][policy.KEY] = historical_stamp
    old_env["envelope_sha256"] = fixtures.envelope_mod.seal_sha256(old_env)
    requests = []

    def author(request):
        requests.append(copy.deepcopy(request))
        return {"items": []} if request["stage"] == "analyse.inventory" else _coverage(request)

    rows = post_fixtures._stamp_rows()
    store = kernel.DecisionStore()
    old_result = analyse.analyse(old_env, rows, provider=author,
                                 critic=post_fixtures._verified_critic, store=store)
    assert [request["stage"] for request in requests] == ["analyse.inventory"]
    assert old_result["inventory"] == []
    assert store.get(decision_key) is not None

    def refuse_spend(_request):
        raise AssertionError("A frozen old-policy decision must replay without provider work")

    assert analyse.analyse(old_env, rows, provider=refuse_spend,
                           critic=refuse_spend, store=store) == old_result
    assert analyse._policy_version("ANALYSE_ALLOT_SYSTEM") == (
        "analysis-1;prompts:d5a8032990f53c53d3f624ec3c5afbbd1c48a34453811668ae8c1f0a87596710"
    )
    systems = []
    monkeypatch.setattr(generation, "_openai_json",
                        lambda system, *_args, **_kwargs: systems.append(system) or {})
    old_payload = {"stage": "analyse.inventory", **policy.fields(old_env)}
    analyse._live_build(old_payload)
    analyse._live_critic(old_payload)
    assert hashlib.sha256("\n".join(systems).encode()).hexdigest() == systems_digest
    old_systems = list(systems)

    new_env = copy.deepcopy(old_env)
    new_env["metadata"][policy.KEY] = policy.VERSION
    new_env["envelope_sha256"] = fixtures.envelope_mod.seal_sha256(new_env)
    requests.clear()
    new_result = analyse.analyse(new_env, rows, provider=author,
                                 critic=post_fixtures._verified_critic, store=store)
    coverage = [request for request in requests if request["stage"] == "analyse.coverage"]
    assert len(coverage) == 2
    assert all(request[policy.KEY] == policy.VERSION for request in coverage)
    assert set(new_result["allotments"].values()) == {"CONCEPT-0001", "CONCEPT-0002"}
    assert all(item["correction"] for item in new_result["inventory"])
    systems.clear()
    analyse._live_build(coverage[0])
    analyse._live_critic(coverage[0])
    assert systems == [policy.COVERAGE_AUTHOR_INSTRUCTION, policy.COVERAGE_CRITIC_INSTRUCTION]
    systems.clear()
    analyse._live_build(old_payload)
    analyse._live_critic(old_payload)
    assert systems == old_systems


@pytest.mark.parametrize("stamp", [None, policy.VERSION_V1])
def test_legacy_inventory_stays_sparse_without_a_coverage_decision(env, stamp):
    env["metadata"].pop(policy.KEY)
    if stamp:
        env["metadata"][policy.KEY] = stamp
    env["envelope_sha256"] = fixtures.envelope_mod.seal_sha256(env)
    requests = []

    def provider(request):
        requests.append(copy.deepcopy(request))
        assert request["stage"] == "analyse.inventory"
        return {"items": []}

    result = analyse.analyse(
        env, post_fixtures._stamp_rows(), provider=provider,
        critic=post_fixtures._verified_critic,
    )
    assert {key: result[key] for key in (
        "inventory", "allotments", "rationales", "review_flags",
    )} == {"inventory": [], "allotments": {}, "rationales": {}, "review_flags": {}}
    assert len(requests) == 1
    assert policy.fields(env) == ({policy.KEY: stamp} if stamp else {})
    assert policy.suffix(env) == (";" + stamp if stamp else "")
    assert not policy.covers_every_concept(env)


def test_post_empty_inventory_gets_pairs_for_both_rows_but_not_culmination_and_replays(env):
    author_requests = []
    critic_requests = []

    def author(request):
        author_requests.append(copy.deepcopy(request))
        if request["stage"] == "analyse.inventory":
            return {"items": []}
        assert request["stage"] == "analyse.coverage"
        return _coverage(request)

    def critic(request):
        critic_requests.append(copy.deepcopy(request))
        if request["stage"] == "analyse.coverage":
            return {"verdict": "revise", "confidence": 0.9, "issues": ["Review the wording against the source."]}
        return post_fixtures._verified_critic(request)

    rows = post_fixtures._stamp_rows()
    store = kernel.DecisionStore()
    result = analyse.analyse(env, rows, provider=author, critic=critic, store=store)
    assert result["allotments"] == {"LA-0001": "CONCEPT-0001", "LA-0002": "CONCEPT-0002"}
    calls = [r for r in author_requests if r["stage"] == "analyse.coverage"]
    assert len(calls) == 2
    expected_evidence = analyse.build_evidence(env)
    for request in calls:
        assert request["evidence"] == expected_evidence
        assert request["target_concept"]["description"]
        assert request["chapter"]["grade"] == env["metadata"]["grade"]
        assert all(not c["concept_title"].startswith("Culmination - ") for c in request["settled_concepts"])
        assert request["existing_analysis"]["inventory"] == []
    assert len([r for r in critic_requests if r["stage"] == "analyse.coverage"]) == 2
    assert set(result["review_flags"]) == {"LA-0001", "LA-0002"}
    replay = analyse.analyse(env, rows, provider=author, critic=critic, store=store)
    assert replay == result
    assert len(author_requests) == len(critic_requests) == 3

    post_fixtures._stamp(rows, result)
    for row in rows[:2]:
        assert "Misconceptions: (1) " in row["concept_details"]
        assert "Correction: " in row["concept_details"]
        assert "Achieving Mastery: " in row["concept_details"]
    assert "Misconceptions:" not in rows[2]["concept_details"]


def test_existing_items_are_never_moved_or_copied_to_fill_a_different_concept(env):
    existing = {
        "item_id": "LA-0001", **PAIRS["Rectilinear Propagation Of Light"],
    }
    calls = []

    def provider(request):
        calls.append(copy.deepcopy(request))
        if request["stage"] == "analyse.inventory":
            return {"items": [existing]}
        if request["stage"] == "analyse.allot":
            return {"allotments": [{"item_id": "LA-0001", "concept_id": "CONCEPT-0001", "rationale": "Source-specific ownership."}]}
        assert request["target_concept"]["concept_id"] == "CONCEPT-0002"
        assert request["existing_analysis"]["inventory"] == [existing]
        return _coverage(request)

    result = analyse.analyse(
        env, post_fixtures._stamp_rows(), provider=provider,
        critic=post_fixtures._verified_critic,
    )
    assert [r["stage"] for r in calls] == ["analyse.inventory", "analyse.allot", "analyse.coverage"]
    assert result["inventory"][0] == existing
    assert result["allotments"] == {"LA-0001": "CONCEPT-0001", "LA-0002": "CONCEPT-0002"}
    assert result["inventory"][1]["text"] != existing["text"]


def test_missing_pair_goes_to_fixer_with_full_evidence_then_is_advisory_reviewed(env):
    calls = []
    fixes = []
    reviews = []

    def provider(request):
        calls.append(request["stage"])
        return {"items": []}

    def fixer(request):
        fixes.append(copy.deepcopy(request))
        assert "has no misconception" in " ".join(request["blocked_check"])
        original = request["original_payload"]
        assert original["evidence"] == analyse.build_evidence(env)
        return {**_coverage(original), "rationale": "Author the omitted source-grounded pair for the specified concept."}

    def critic(request):
        reviews.append(request["stage"])
        return post_fixtures._verified_critic(request)

    result = analyse.analyse(
        env, post_fixtures._stamp_rows()[:1], provider=provider,
        critic=critic, fixer=fixer,
    )
    assert len(fixes) == 1
    assert reviews == ["analyse.inventory", "analyse.coverage"]
    assert result["allotments"] == {"LA-0001": "CONCEPT-0001"}
    assert any("fixer: blocked=" in flag for flag in result["review_flags"]["LA-0001"])


def test_error_analysis_alone_does_not_satisfy_the_declared_misconception_field(env):
    incomplete = {"items": [{**PAIRS["Reading a Map"], "kind": "error_analysis"}]}
    assert "has no misconception" in " ".join(analyse._coverage_checker(incomplete))
    empty_correction = {"items": [{**PAIRS["Reading a Map"], "correction": ""}]}
    assert "has empty correction" in " ".join(analyse._coverage_checker(empty_correction))
    empty_evidence = {"items": [{**PAIRS["Reading a Map"], "evidence": ""}]}
    assert "has empty evidence" in " ".join(analyse._coverage_checker(empty_evidence))


def test_pre_coverage_uses_only_prerequisite_evidence_and_preserves_qid_advisory_flags(env):
    inventory = [
        {**item, "correction": "Sovereignty concerns the highest law-making authority in a state."}
        for item in pre_fixtures.INVENTORY
    ]
    base = premap_fixtures._provider(inventory=inventory, allotments=pre_fixtures.ALLOTMENTS)
    prerequisites = copy.deepcopy(premap_fixtures.PREREQUISITES)
    prerequisites["prerequisites"][1]["retained_atoms"] = [{
        "text": "Use the particular map's key before interpreting colours.",
        "rationale": "QINV-0004 assumes this independent map-reading capability.",
    }]
    coverage_requests = []

    def provider(request):
        if request["stage"] == "prelearn.analyse.coverage":
            coverage_requests.append(copy.deepcopy(request))
            assert request["target_concept"]["pre_concept_id"] == "PRC-0002"
            return _coverage(request)
        return base(request)

    def critic(request):
        if request["stage"].startswith("prelearn.analyse"):
            return {"verdict": "revise", "confidence": 0.8, "issues": ["Review context from QINV-0004."]}
        return pre_fixtures._verified_critic(request)

    result = pre_fixtures._build(
        env, provider=provider, critic=critic, prerequisites=prerequisites,
    )
    assert len(coverage_requests) == 1
    payload = coverage_requests[0]
    assert set(payload["evidence"]) == {"prerequisites", "pre_concepts"}
    atoms = payload["evidence"]["prerequisites"][1]["retained_atoms"]
    assert atoms[0]["text"] == "Use the particular map's key before interpreting colours."
    assert "a source question of this chapter" in atoms[0]["rationale"]
    flat = json.dumps(payload)
    for item in env["inventory"]["items"]:
        assert item["qid"] not in flat
    assert "source_blocks" not in flat
    for row in result["rows"]:
        assert "Misconceptions: (1) " in row["concept_details"]
        assert "Correction: " in row["concept_details"]
        assert "QINV-0004" not in row["concept_details"]
    assert result["analysis"]["allotments"]["PLA-0003"] == "PRC-0002"
    assert any("QINV-0004" in flag for flags in result["analysis"]["review_flags"].values() for flag in flags)


@pytest.mark.parametrize("call, critic", [
    (analyse._live_coverage, False),
    (analyse._live_build, False),
    (preanalyse._live_build, False),
    (analyse._live_critic, True),
    (preanalyse._live_critic, True),
])
def test_live_coverage_adapters_use_the_new_contract_and_full_payload(monkeypatch, call, critic):
    seen = []
    monkeypatch.setattr(generation, "_openai_json", lambda *args, **kwargs: seen.append((args, kwargs)) or {})
    payload = {"stage": "analyse.coverage", policy.KEY: policy.VERSION, "evidence": {"source_blocks": [{"text": "Complete source evidence"}]}}
    call(payload)
    args, kwargs = seen[0]
    assert args[0] == (policy.COVERAGE_CRITIC_INSTRUCTION if critic else policy.COVERAGE_AUTHOR_INSTRUCTION)
    assert "Complete source evidence" in args[1]
    assert kwargs["purpose"] == ("advisory_critic" if critic else "concept_mapping")

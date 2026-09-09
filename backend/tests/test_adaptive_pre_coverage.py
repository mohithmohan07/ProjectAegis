"""Fresh Pre plans choose sufficient coverage; sealed quota runs still replay.

Scripted plans test the numeric/identity contract and policy transport. Actual
diagnostic sufficiency and prior-grade scope remain author/critic judgments.
"""
from __future__ import annotations

import copy
import json

import pytest

from app.services import release_qc
from app.services.phase3 import envelope as envelope_mod
from app.services.phase3 import kernel, pre_coverage, premap, prequestions, prompts
from tests.test_phase3_prequestions import golden_envelope, pre_map  # noqa: F401
from tests.test_pre_coverage_rule import _qc_payload, _tiered_questions


RULE = pre_coverage.owner_rule()


def _adaptive(env):
    result = copy.deepcopy(env)
    result["metadata"] = pre_coverage.stamp(result.get("metadata"))
    result["envelope_sha256"] = envelope_mod.seal_sha256(result)
    return envelope_mod.validate(result)


def _plan(split):
    return {
        "total": sum(split.values()),
        "split": [{"tier": tier, "count": count} for tier, count in split.items()],
        "rationale": "Each distinct prerequisite mastery demand has sufficient diagnostic coverage.",
    }


def _provider(split_by_concept, seen=None):
    def provider(payload):
        if seen is not None:
            seen.append(copy.deepcopy(payload))
        assert payload["coverage_rule"] == RULE
        if payload["stage"] == "prequestions.plan":
            return {"plans": [
                {"pre_concept_id": row["pre_concept_id"], **_plan(split_by_concept[row["pre_concept_id"]])}
                for row in payload["pre_concepts"]
            ]}
        cid = payload["pre_concept"]["pre_concept_id"]
        return {"questions": _tiered_questions(cid, split_by_concept[cid])}
    return provider


def test_fresh_mint_explicitly_adopts_adaptive_even_from_copied_fixed_metadata(golden_envelope):
    old = {pre_coverage.RULE_FIELD: pre_coverage.legacy_owner_rule(), "grade": "2"}
    original = copy.deepcopy(old)
    fresh = pre_coverage.stamp(old)
    assert fresh == {"grade": "2", pre_coverage.RULE_FIELD: RULE}
    assert old == original
    assert RULE == {"version": "pre-coverage-adaptive-2026-09-09-v1", "mode": "adaptive"}
    assert pre_coverage.rule_for(_adaptive(golden_envelope)) == RULE
    assert pre_coverage.rule_for(golden_envelope) is None
    with pytest.raises(pre_coverage.CoverageRuleError, match="no policy total"):
        pre_coverage.total(RULE)


@pytest.mark.parametrize("broken", [
    {**RULE, "per_tier": {"Basic": 5}},
    {**RULE, "total": 10},
    {**RULE, "mode": "fixed"},
    {"version": "unknown-adaptive", "mode": "adaptive"},
])
def test_adaptive_policy_cannot_smuggle_a_fixed_count(broken):
    with pytest.raises(pre_coverage.CoverageRuleError, match="adaptive coverage"):
        pre_coverage.validate(broken)


@pytest.mark.parametrize("split", [
    {"Basic": 1}, {"Intermediate": 2}, {"Basic": 2, "Intermediate": 1},
    {"Advanced": 3}, {"Basic": 0, "Intermediate": 1},
])
def test_adaptive_checkers_follow_only_the_model_authored_plan(split):
    plan = _plan(split)
    assert prequestions._plan_checker(["PRC-1"], RULE)({
        "plans": [{"pre_concept_id": "PRC-1", **plan}],
    }) == []
    checker = prequestions._author_checker("PRC-1", plan["total"], rule=RULE, split=split)
    questions = _tiered_questions("PRC-1", split)
    assert checker({"questions": questions}) == []
    assert checker({"questions": questions + [copy.deepcopy(questions[0])]})
    assert checker({"questions": questions[:-1]})


def test_adaptive_plan_still_requires_rationale_and_self_consistent_arithmetic():
    checker = prequestions._plan_checker(["PRC-1"], RULE)
    good = {"pre_concept_id": "PRC-1", **_plan({"Basic": 1})}
    assert any("rationale" in issue for issue in checker({"plans": [{**good, "rationale": ""}]}))
    assert any("split sums" in issue for issue in checker({"plans": [{**good, "total": 2}]}))
    zero = {"pre_concept_id": "PRC-1", **_plan({})}
    assert checker({"plans": [zero]}) == []


def test_fresh_build_keeps_small_different_plans_tiers_review_and_replay(golden_envelope, pre_map):
    env = _adaptive(golden_envelope)
    cids = [row["_pre_concept_id"] for row in pre_map["rows"]]
    splits = {cid: ({"Basic": 1} if index == 0 else {"Intermediate": 2, "Advanced": 1})
              for index, cid in enumerate(cids)}
    seen, reviews = [], []
    def critic(payload):
        reviews.append(copy.deepcopy(payload))
        return {"verdict": "rejected", "confidence": 0.9,
                "issues": ["Check diagnostic sufficiency against the retained mastery."]}
    store = kernel.DecisionStore()
    result = prequestions.build(env, pre_map, provider=_provider(splits, seen), critic=critic, store=store)
    assert result["blocked"] == {}
    assert result["coverage_rule"] == RULE
    assert len(seen) == len(reviews) == len(cids) + 1
    for cid in cids:
        assert result["plans"][cid] == _plan(splits[cid])
        assert len(result["questions"][cid]) == sum(splits[cid].values())
        assert {q["tier"] for q in result["questions"][cid]} == set(splits[cid])
        assert result["review_flags"][cid]
    assert all(store.get(key)["policy_version"] == prequestions.ADAPTIVE_POLICY_VERSION for key in store.keys())
    def forbidden(*args, **kwargs):
        raise AssertionError("accepted adaptive plans and questions must replay without spending")
    assert prequestions.build(env, pre_map, provider=forbidden, critic=forbidden, store=store) == result
    for payload in seen:
        serialized = json.dumps(payload)
        assert not any(qid in serialized for qid in premap.inventory_qids(env))


def test_adaptive_empty_map_keeps_policy_and_spends_nothing(golden_envelope):
    def forbidden(*args, **kwargs):
        raise AssertionError("an empty map has no question authoring work")
    result = prequestions.build(_adaptive(golden_envelope), {"rows": []}, provider=forbidden)
    assert result["coverage_rule"] == RULE and result["questions"] == {}


def test_adaptive_systems_remove_fixed_quota_claims_but_keep_prior_scope_and_review():
    payload = {"coverage_rule": RULE}
    for system in (prequestions._plan_system(payload), prequestions._author_system(payload),
                   prequestions._critic_system(payload), prequestions._plan_rules("", RULE),
                   prequestions._author_rules("", RULE)):
        assert "fixed rule" not in system
        assert "five" not in system and "5 Basic" not in system
        assert "prior" in system or "earlier" in system
        assert "pad" in system
    assert "no fixed total or per-tier quota" in prequestions._plan_system(payload)
    assert "a question in every tier" in prequestions._critic_system(payload)
    assert "Dissent is recorded and advisory" in prequestions._critic_system(payload)
    assert prequestions._plan_system({}) == prompts.PREQUESTIONS_PLAN_SYSTEM
    fixed = {"coverage_rule": pre_coverage.legacy_owner_rule()}
    assert prequestions._author_system(fixed) == prompts.PREQUESTIONS_AUTHOR_SYSTEM
    assert prequestions._critic_system(fixed) == prompts.PREQUESTIONS_CRITIC_SYSTEM


def test_adaptive_release_qc_holds_to_each_accepted_plan_not_withdrawn_quota():
    questions = _tiered_questions("PRC-0001", {"Intermediate": 2})
    payload = _qc_payload(questions, rule=RULE)
    payload["pre_question_plans"] = {"PRC-0001": _plan({"Intermediate": 2})}
    assert release_qc._pre_coverage_rule_findings(payload) == ([], [])
    payload["generated_questions"] = questions[:1]
    issues, blocking = release_qc._pre_coverage_rule_findings(payload)
    assert len(issues) == len(blocking) == 1
    assert "recorded plan asks for 2" in issues[0]["message"]
    assert "5 Basic" not in issues[0]["message"]
    payload["generated_questions"] = questions
    payload["generated_questions"][0]["tier"] = "Basic"
    assert release_qc._pre_coverage_rule_findings(payload)[1]
    payload["pre_question_plans"] = {}
    assert "no model-authored coverage plan" in release_qc._pre_coverage_rule_findings(payload)[1][0]


def test_adaptive_source_question_identity_guard_still_refuses(golden_envelope, pre_map):
    env = _adaptive(golden_envelope)
    splits = {row["_pre_concept_id"]: {"Basic": 1} for row in pre_map["rows"]}
    base = _provider(splits)
    qid = premap.inventory_qids(env)[0]
    def leaking(payload):
        result = base(payload)
        if payload["stage"] == "prequestions.author":
            result["questions"][0]["question_text"] += " " + qid
        return result
    with pytest.raises(premap.PreExtractionError):
        prequestions.build(env, pre_map, provider=leaking, store=kernel.DecisionStore())

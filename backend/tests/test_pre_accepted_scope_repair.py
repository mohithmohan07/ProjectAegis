"""Accepted Pre scope cannot silently disappear through a zero coverage plan."""
from __future__ import annotations

import copy
import json

import pytest

from app.services import generation, generation_repair_policy as repair
from app.services.phase3 import envelope, kernel, pre_coverage, prequestions
from tests.test_phase3_prequestions import (
    _plan_response,
    _questions_for,
    _verified_critic,
    golden_envelope,  # noqa: F401 - shared fixture
    pre_map,  # noqa: F401 - shared fixture
)


def _accepted_env(original, *, current=True):
    env = copy.deepcopy(original)
    env["metadata"][pre_coverage.RULE_FIELD] = pre_coverage.owner_rule()
    if current:
        env["metadata"][repair.KEY] = repair.VERSION
    else:
        env["metadata"].pop(repair.KEY, None)
    env["envelope_sha256"] = envelope.seal_sha256(env)
    return envelope.validate(env)


def _plans(total_by_id):
    return {
        concept_id: {
            "total": total,
            "split": [{"tier": "Basic", "count": total}] if total else [],
            "rationale": (
                "These questions check the accepted definition and its application."
                if total else
                "The planner disputes eligibility because the source does not document when it was taught."
            ),
        }
        for concept_id, total in total_by_id.items()
    }


ZERO = _plans({"PRC-0001": 0, "PRC-0002": 0})
POSITIVE = _plans({"PRC-0001": 1, "PRC-0002": 2})


def _author(request):
    concept_id = request["pre_concept"]["pre_concept_id"]
    rows = _questions_for(concept_id, request["coverage_plan"]["total"])
    for row in rows:
        row["tier"] = "Basic"
    return {"questions": rows}


@pytest.mark.parametrize("recover_in_fixer", [False, True])
def test_zero_plan_reaches_bounded_repair_or_fixer_and_authors_accepted_scope(
    golden_envelope, pre_map, recover_in_fixer,
):
    env = _accepted_env(golden_envelope)
    before_map = copy.deepcopy(pre_map)
    plans_seen, authors_seen, fixers_seen = [], [], []

    def provider(request):
        plans_seen.append(copy.deepcopy(request))
        plans = ZERO if recover_in_fixer or len(plans_seen) == 1 else POSITIVE
        return _plan_response(plans, request)

    def author(request):
        authors_seen.append(copy.deepcopy(request))
        return _author(request)

    def fixer(request):
        fixers_seen.append(copy.deepcopy(request))
        return _plan_response(POSITIVE, request["original_payload"])

    store = kernel.DecisionStore()
    result = prequestions.build(
        env, pre_map, provider=provider, author_provider=author,
        critic=_verified_critic, fixer=fixer, store=store,
    )

    assert result["blocked"] == {}
    assert {cid: len(rows) for cid, rows in result["questions"].items()} == {
        "PRC-0001": 1, "PRC-0002": 2,
    }
    assert len(plans_seen) == (kernel.MAX_ATTEMPTS if recover_in_fixer else 2)
    assert len(fixers_seen) == int(recover_in_fixer)
    assert len(authors_seen) == 2
    assert any("zero cannot drop its accepted scope" in defect
               for defect in plans_seen[1]["response_contract_feedback"])
    for payload in plans_seen + authors_seen:
        assert payload[repair.KEY] == repair.VERSION
        assert repair.PRE_ASSESSMENT_INSTRUCTION in payload["rules"]
        assert "QINV-" not in json.dumps(payload)
    if recover_in_fixer:
        assert repair.VERSION in fixers_seen[0]["contract"]["policy_version"]
        assert repair.PRE_ASSESSMENT_INSTRUCTION in fixers_seen[0]["original_payload"]["rules"]
        assert any("fixer:" in flag for flag in result["decision_flags"]["plan"])
    assert pre_map == before_map

    # A corrected and reviewed plan is a durable decision, not another chance
    # to reopen prerequisite eligibility on resume.
    counts = (len(plans_seen), len(authors_seen), len(fixers_seen))
    assert prequestions.build(
        env, pre_map, provider=provider, author_provider=author,
        critic=_verified_critic, fixer=fixer, store=store,
    ) == result
    assert (len(plans_seen), len(authors_seen), len(fixers_seen)) == counts


def test_persistent_zero_blocks_named_concepts_without_spending_on_authors(
    golden_envelope, pre_map,
):
    plans_seen, fixers_seen = [], []

    def provider(request):
        plans_seen.append(copy.deepcopy(request))
        return _plan_response(ZERO, request)

    def fixer(request):
        fixers_seen.append(copy.deepcopy(request))
        return _plan_response(ZERO, request["original_payload"])

    def forbidden(_request):
        pytest.fail("An invalid zero plan must not reach authoring or semantic review")

    result = prequestions.build(
        _accepted_env(golden_envelope), pre_map, provider=provider,
        author_provider=forbidden, critic=forbidden, fixer=fixer,
        store=kernel.DecisionStore(),
    )

    assert result["plans"] == {} and result["questions"] == {}
    assert set(result["blocked"]) == {"PRC-0001", "PRC-0002"}
    assert len(plans_seen) == len(fixers_seen) == kernel.MAX_ATTEMPTS
    for concept_id, reason in result["blocked"].items():
        assert concept_id in reason
        assert "zero cannot drop its accepted scope" in reason
        assert result["review_flags"][concept_id]


def test_historical_adaptive_zero_plan_remains_accepted_without_new_policy(
    golden_envelope, pre_map,
):
    seen = []

    def provider(request):
        seen.append(copy.deepcopy(request))
        return _plan_response(ZERO, request)

    def forbidden(_request):
        pytest.fail("Historical zero plans must not acquire new repair or author calls")

    result = prequestions.build(
        _accepted_env(golden_envelope, current=False), pre_map,
        provider=provider, author_provider=forbidden,
        critic=_verified_critic, fixer=forbidden, store=kernel.DecisionStore(),
    )

    assert len(seen) == 1
    assert repair.KEY not in seen[0]
    assert {cid: plan["total"] for cid, plan in result["plans"].items()} == {
        "PRC-0001": 0, "PRC-0002": 0,
    }
    assert result["questions"] == {} and result["blocked"] == {}


@pytest.mark.parametrize("adapter", [
    prequestions._live_plan, prequestions._live_author, prequestions._live_critic,
])
def test_effective_live_prompts_assess_accepted_scope_without_fixed_count_anchors(
    monkeypatch, adapter,
):
    requests = []

    def offline_openai(system, user, **kwargs):
        requests.append((system, user, kwargs))
        return {}

    monkeypatch.setattr(generation, "_openai_json", offline_openai)
    rule = pre_coverage.owner_rule()
    payload = {
        repair.KEY: repair.VERSION,
        "coverage_rule": rule,
        "rules": prequestions._plan_rules("", rule, accepted_scope=True),
        "pre_concepts": [{
            "pre_concept_id": "PRC-0001",
            "concept_title": "Reading a table",
            "concept_details": "Read a value at a row and column intersection.",
        }],
    }
    adapter(payload)

    assert len(requests) == 1
    system, user, _kwargs = requests[0]
    assert repair.PRE_ASSESSMENT_INSTRUCTION in system
    assert repair.PRE_ASSESSMENT_INSTRUCTION in json.loads(user)["rules"]
    assert "this coverage/authoring pass does not reopen that decision" in system
    full_prompt = " ".join((system + user).split()).lower()
    for old_anchor in (
        "five basic", "five intermediate", "5 basic", "5 intermediate",
        "planned at six", "six questions; that one", '"total": 10',
        "identical fixed totals and splits are correct",
        "total and the split are the owner's fixed rule",
    ):
        assert old_anchor not in full_prompt
    assert "adaptive" in full_prompt and "tier" in full_prompt

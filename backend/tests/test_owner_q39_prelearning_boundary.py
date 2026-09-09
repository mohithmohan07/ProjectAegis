"""New prior-only rules reach existing decisions; historical keys still replay."""
from __future__ import annotations

import copy

import pytest

from app.services import generation, prelearning_authority_v2 as authority
from app.services import prelearning_capture_policy as policy
from app.services import source_topic_policy
from app.services.phase3 import kernel, preanalyse, prelearn, premap, prequestions, prompts
from tests import test_prelearning_atomic_authority as fixtures


@pytest.mark.parametrize("call", [
    prelearn._live_capture, prelearn._live_merge, prelearn._live_critic,
    authority._live_author, authority._live_critic,
    premap._live_map, premap._live_links, premap._live_critic,
    preanalyse._live_build, preanalyse._live_allot, preanalyse._live_critic,
    prequestions._live_plan, prequestions._live_author, prequestions._live_critic,
])
def test_existing_author_and_critic_adapters_only_adopt_stamped_boundary(monkeypatch, call):
    seen = []

    def record(system, user, **kwargs):
        seen.append((system, user))
        return {}

    monkeypatch.setattr(generation, "_openai_json", record)
    historical = {"capture_policy": policy.VERSION}
    call(historical)
    call({**historical, policy.BOUNDARY_KEY: policy.BOUNDARY_VERSION})
    call(historical)
    assert seen[0] == seen[2]
    assert policy.BOUNDARY_INSTRUCTION not in seen[0][0]
    assert seen[1][0] == seen[0][0] + "\n" + policy.BOUNDARY_INSTRUCTION


def test_adjudication_keeps_disposed_teaching_and_replays_each_policy(tmp_path):
    env = fixtures._env()
    merged = fixtures._merged()
    response = fixtures._split_response()
    # One candidate's prior-grade provenance is unsupported. The API keeps
    # its complete atom as an explicit disposition, not a released Pre row.
    response["prerequisites"].pop()
    response["dispositions"] = [{
        "disposition_id": "PD-0001", "classification": "insufficient_evidence",
        "atoms": ["PA-0002"], "rationale": "No supplied prior-grade evidence.",
    }]
    for demand in response["demand_coverage"]:
        demand["prerequisite_ids"] = [p for p in demand["prerequisite_ids"] if p != "PR-0002"]
    calls = []

    def author(payload):
        calls.append(copy.deepcopy(payload))
        return copy.deepcopy(response)

    def run(current):
        return authority.adjudicate(current, merged, provider=author,
                                    store=kernel.DecisionStore(tmp_path / "decisions"))

    original = copy.deepcopy(merged)
    old = run(env)
    assert run(env) == old
    assert len(calls) == 1
    new_env = copy.deepcopy(env)
    new_env["metadata"][policy.BOUNDARY_KEY] = policy.BOUNDARY_VERSION
    new = run(new_env)
    assert run(new_env) == new
    assert run(env) == old
    assert len(calls) == 2
    assert policy.BOUNDARY_KEY not in calls[0]
    assert calls[1][policy.BOUNDARY_KEY] == policy.BOUNDARY_VERSION
    assert calls[0]["rules"] == authority.SYSTEM
    assert policy.BOUNDARY_INSTRUCTION in calls[1]["rules"]
    assert old["adjudication"]["decision_key"] != new["adjudication"]["decision_key"]
    assert new["dispositions"][0]["atoms"] == ["PA-0002"]
    assert new["dispositions"][0]["captures"] == ["settle:PR-0001"]
    assert len(new["atoms"]) == 2
    assert len(new["prerequisites"]) == 1
    assert merged == original


def test_frozen_topic_and_prior_rules_reach_phase3_without_architect_slots():
    historical = {"metadata": {"instruction_slots": {"subject_topology_guidance": "Recorded source guidance"}}}
    before = prompts.instruction_rules_suffix(historical)
    assert "OWNER SOURCE POLICY" not in before
    assert policy.BOUNDARY_INSTRUCTION not in before
    metadata = {
        "source_topic_policy_version": source_topic_policy.SOURCE_TOPIC_POLICY_VERSION,
        policy.BOUNDARY_KEY: policy.BOUNDARY_VERSION,
    }
    for slots in (prompts.DEFAULT_SLOTS, prompts.PRE_LEARNING_SLOTS, prompts.PRE_QUESTION_SLOTS):
        suffix = prompts.instruction_rules_suffix({"metadata": metadata}, slots=slots)
        if slots == prompts.DEFAULT_SLOTS:
            assert source_topic_policy.SOURCE_TOPIC_POLICY in suffix
        else:
            assert source_topic_policy.PRE_SOURCE_POLICY in suffix
            assert source_topic_policy.SOURCE_TOPIC_POLICY not in suffix
            assert "final topic is Detailed Analysis" not in suffix
        assert policy.BOUNDARY_INSTRUCTION in suffix
    assert prompts.instruction_rules_suffix(historical) == before
    assert prompts.instruction_rules_suffix({"metadata": {}}) == ""

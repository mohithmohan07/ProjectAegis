"""Saved decisions replay only under the same relevant author/critic text.

These calls reuse one sealed envelope and reopen the on-disk decision store,
covering direct resumes that do not reassemble the Instruction Architect.
"""
from __future__ import annotations

from collections import Counter
import json

import pytest

from app.services.phase3 import analyse, kernel, prompts, settle
from tests import test_phase3_analyse as analyse_golden
from tests import test_phase3_premap as premap_golden
from tests import test_phase3_preanalyse as preanalyse_golden
from tests import test_phase3_settle_golden as settle_golden


@pytest.mark.parametrize("scenario,prompt_name,affected", [
    ("analyse", "ANALYSE_CRITIC_SYSTEM", {"analyse.inventory", "analyse.allot"}),
    ("analyse", "ANALYSE_INVENTORY_SYSTEM", {"analyse.inventory"}),
    ("analyse", "ANALYSE_ALLOT_SYSTEM", {"analyse.allot"}),
    ("preanalyse", "PREANALYSE_CRITIC_SYSTEM", {"prelearn.analyse.inventory", "prelearn.analyse.allot"}),
    ("preanalyse", "PREANALYSE_INVENTORY_SYSTEM", {"prelearn.analyse.inventory"}),
    ("preanalyse", "PREANALYSE_ALLOT_SYSTEM", {"prelearn.analyse.allot"}),
    ("premap", "PREMAP_CRITIC_SYSTEM", {"premap.map", "premap.needed_for"}),
    ("premap", "PREMAP_SYSTEM", {"premap.map"}),
    ("premap", "PREMAP_NEEDED_FOR_SYSTEM", {"premap.needed_for"}),
    ("settle", "CRITIC_SYSTEM", {"topology", "grounding", "content_authoring"}),
    ("settle", "TOPOLOGY_SYSTEM", {"topology"}),
    ("settle", "GROUNDING_SYSTEM", {"grounding"}),
    ("settle", "ANALYSIS_SYSTEM", {"content_authoring"}),
])
def test_saved_decisions_rekey_only_for_relevant_prompt_changes(
    tmp_path, monkeypatch, scenario, prompt_name, affected,
):
    env = settle_golden.envelope_mod.load(settle_golden.GOLDEN / "rne_envelope.json")
    rows = json.loads((settle_golden.GOLDEN / "rne_settled_rows.json").read_text())["records"]
    author_calls, critic_calls = Counter(), Counter()

    def count_author(provider):
        def counted(request):
            author_calls[request["stage"]] += 1
            return provider(request)
        return counted

    def critic(request):
        critic_calls[request["stage"]] += 1
        return {"verdict": "verified", "confidence": 0.999, "issues": []}

    if scenario == "analyse":
        settled = analyse_golden._settled(env, rows)
        inventory = json.loads((settle_golden.GOLDEN / "rne_analysis.json").read_text())
        author = count_author(analyse_golden.analyse_replay_provider(inventory))

        def run(store):
            return analyse.analyse(env, settled, provider=author, critic=critic, store=store)
    elif scenario in {"preanalyse", "premap"}:
        author = count_author(premap_golden._provider(
            inventory=preanalyse_golden.INVENTORY,
            allotments=preanalyse_golden.ALLOTMENTS,
        ))

        def run(store):
            return premap_golden._build(env, provider=author, critic=critic, store=store)
    else:
        topology, grounding, author, _ = settle_golden._providers(
            settle_golden._replay_map(env, rows)
        )
        topology, grounding, author = map(count_author, (topology, grounding, author))

        def run(store):
            return settle.settle(
                env, topology_provider=topology, grounding_provider=grounding,
                analysis_provider=author, critic=critic, store=store,
            )

    saved = tmp_path / "decisions"
    first = run(kernel.DecisionStore(saved))
    initial_authors, initial_critics = author_calls.copy(), critic_calls.copy()
    assert affected <= set(initial_authors)
    assert run(kernel.DecisionStore(saved)) == first
    assert author_calls == initial_authors
    assert critic_calls == initial_critics

    # An unrelated prompt cannot invalidate these per-pass saved judgments.
    monkeypatch.setattr(prompts, "HOST_SYSTEM", prompts.HOST_SYSTEM + "\nUnrelated host revision.")
    assert run(kernel.DecisionStore(saved)) == first
    assert author_calls == initial_authors
    assert critic_calls == initial_critics

    monkeypatch.setattr(prompts, prompt_name, getattr(prompts, prompt_name) + "\nReview revision.")
    run(kernel.DecisionStore(saved))
    for stage in initial_authors:
        multiplier = 2 if stage in affected else 1
        assert author_calls[stage] == multiplier * initial_authors[stage], stage
        assert critic_calls[stage] == multiplier * initial_critics[stage], stage
    after_authors, after_critics = author_calls.copy(), critic_calls.copy()
    run(kernel.DecisionStore(saved))
    assert author_calls == after_authors
    assert critic_calls == after_critics

"""Recorded assessment-group verdicts replay without another decision spend."""
from __future__ import annotations

import copy
import json
from collections import Counter
from pathlib import Path
from typing import Callable

from app.services import assessment_grouping as grouping
from app.services import assessment_quality as quality
from app.services.phase3 import envelope as envelope_mod
from app.services.phase3 import kernel


GOLDEN = Path(__file__).parent / "golden"


def _load_recorded() -> tuple[dict, dict]:
    recorded = json.loads(
        (GOLDEN / "rne_assessment_groups.json").read_text(encoding="utf-8")
    )
    assert recorded["envelope_fixture"] == "rne_envelope.json"
    return recorded, envelope_mod.load(GOLDEN / recorded["envelope_fixture"])


def _run_recorded_passes(
    recorded: dict,
    envelope: dict,
    *,
    store: kernel.DecisionStore,
    provider: kernel.Provider,
    critic: kernel.Critic,
    fixer: kernel.Provider,
) -> dict:
    concept = recorded["concept"]
    candidates = recorded["candidates"]
    metadata = recorded["metadata"]
    envelope_sha256 = envelope["envelope_sha256"]

    levels = grouping.decide_levels(
        [
            {"candidate": candidate, "concept": concept}
            for candidate in candidates
        ],
        meta=metadata,
        envelope_sha256=envelope_sha256,
        provider=provider,
        critic=critic,
        store=store,
        fixer=fixer,
    )
    levels_by_id = {row["candidate_id"]: row for row in levels}
    cluster_tier = recorded["cluster_response"]["tier"]
    tier_candidates = [
        candidate
        for candidate in candidates
        if levels_by_id[candidate["candidate_id"]]["tier"] == cluster_tier
    ]
    clustered = grouping.cluster_tier(
        tier_candidates,
        concept=concept,
        tier=cluster_tier,
        meta=metadata,
        envelope_sha256=envelope_sha256,
        provider=provider,
        critic=critic,
        store=store,
        fixer=fixer,
    )

    members_by_id = {
        candidate["candidate_id"]: candidate for candidate in candidates
    }
    groups = []
    for sequence, family in enumerate(clustered["families"], start=1):
        groups.append(
            grouping.group_record(
                concept_id=concept["concept_key"],
                concept_machine_id=concept["concept_machine_id"],
                concept_name=concept["concept_display_name"],
                tier=cluster_tier,
                sequence=sequence,
                member_candidate_ids=family["member_candidate_ids"],
                family=family["family"],
            )
        )

    descriptions = {}
    described_groups = []
    for group in groups:
        members = [
            members_by_id[candidate_id]
            for candidate_id in group["member_candidate_ids"]
        ]
        result = grouping.describe_group(
            group,
            members,
            concept=concept,
            meta=metadata,
            envelope_sha256=envelope_sha256,
            provider=provider,
            critic=critic,
            store=store,
            fixer=fixer,
        )
        descriptions[group["group_key"]] = result
        described_groups.append(
            {**group, "semantic_description": result["description"]}
        )

    reviews = {}
    for group in described_groups:
        members = [
            members_by_id[candidate_id]
            for candidate_id in group["member_candidate_ids"]
        ]
        siblings = [
            {
                "group": sibling,
                "members": [
                    members_by_id[candidate_id]
                    for candidate_id in sibling["member_candidate_ids"]
                ],
            }
            for sibling in described_groups
            if sibling["group_key"] != group["group_key"]
        ]
        reviews[group["group_key"]] = quality.review_group(
            group,
            members,
            siblings=siblings,
            concept=concept,
            meta=metadata,
            envelope_sha256=envelope_sha256,
            provider=provider,
            critic=critic,
            store=store,
            fixer=fixer,
        )

    return {
        "levels": levels,
        "cluster": clustered,
        "groups": described_groups,
        "descriptions": descriptions,
        "reviews": reviews,
    }


def _recorded_provider(
    recorded: dict, calls: list[str]
) -> kernel.Provider:
    levels = {
        response["candidate_id"]: response
        for response in recorded["level_responses"]
    }

    def provide(request: dict) -> dict:
        stage = request["stage"]
        calls.append(stage)
        if stage == "assessment.level":
            response = levels[request["candidate"]["candidate_id"]]
        elif stage == "assessment.variant_cluster":
            response = recorded["cluster_response"]
        elif stage == "assessment.group_description":
            response = recorded["description_responses"][
                request["group"]["group_key"]
            ]
        elif stage == "assessment.group_quality":
            response = recorded["quality_responses"][
                request["group"]["group_key"]
            ]
        else:  # pragma: no cover - an unexpected pass is itself the failure
            raise AssertionError(f"unexpected assessment stage {stage!r}")
        return copy.deepcopy(response)

    return provide


def _recorded_critic(recorded: dict, calls: list[str]) -> kernel.Critic:
    def review(request: dict) -> dict:
        calls.append(request["stage"])
        return copy.deepcopy(recorded["critic_review"])

    return review


def _forbidden(label: str) -> Callable[[dict], dict]:
    def call(_request: dict) -> dict:
        raise AssertionError(f"cached replay called {label}")

    return call


def test_recorded_grouping_verdicts_replay_without_provider_calls() -> None:
    recorded, envelope = _load_recorded()
    store = kernel.DecisionStore()
    provider_calls: list[str] = []
    critic_calls: list[str] = []

    first = _run_recorded_passes(
        recorded,
        envelope,
        store=store,
        provider=_recorded_provider(recorded, provider_calls),
        critic=_recorded_critic(recorded, critic_calls),
        fixer=_forbidden("the Fixer"),
    )

    assert [row["decision"] for row in first["levels"]] == recorded[
        "level_responses"
    ]
    assert first["cluster"]["decision"] == recorded["cluster_response"]
    assert {
        key: result["decision"]
        for key, result in first["descriptions"].items()
    } == recorded["description_responses"]
    assert [
        (group["group_key"], group["family"], group["member_candidate_ids"])
        for group in first["groups"]
    ] == [
        (
            key,
            family["family"],
            family["member_candidate_ids"],
        )
        for key, family in zip(
            recorded["description_responses"],
            recorded["cluster_response"]["families"],
        )
    ]

    for group_key, expected in recorded["quality_responses"].items():
        review = first["reviews"][group_key]
        assert review["flags"] == expected["flags"]
        assert review["quality_review"] == "verified"
        stored = store.get(review["authority"]["decision_key"])
        assert stored is not None
        assert stored["response"] == expected

    expected_calls = Counter(
        {
            "assessment.level": 2,
            "assessment.variant_cluster": 1,
            "assessment.group_description": 2,
            "assessment.group_quality": 2,
        }
    )
    assert Counter(provider_calls) == expected_calls
    assert Counter(critic_calls) == expected_calls
    assert len(store.keys()) == 7

    second = _run_recorded_passes(
        recorded,
        envelope,
        store=store,
        provider=_forbidden("a provider"),
        critic=_forbidden("a critic"),
        fixer=_forbidden("the Fixer"),
    )

    assert second == first
    assert Counter(provider_calls) == expected_calls
    assert Counter(critic_calls) == expected_calls
    assert len(store.keys()) == 7

"""Regression coverage for Phase 3.2 source-verified topology adjudication."""
from __future__ import annotations

import copy

import pytest

from app.services import canonical_source_phase3 as phase3
from app.services import canonical_source_phase32_topology_adjudication_contract as phase32
from app.services import canonical_source_phase33_preflight_contract as phase33
from app.services import semantic_recovery


def _graph_and_canonical() -> tuple[dict, dict]:
    graph = {
        "source_contract_hash": "RNE-SOURCE-CONTRACT",
        "metadata": {
            "subject": "History",
            "board": "CBSE",
            "grade": "10",
            "chapter_title": "The Rise of Nationalism in Europe",
        },
        "topics": [
            {
                "topic_id": "TOPIC-0001",
                "order": 1,
                "title": "The French Revolution and the Idea of the Nation",
                "structural_number": "1",
            },
            {
                "topic_id": "TOPIC-0002",
                "order": 2,
                "title": "The Making of Nationalism in Europe",
                "structural_number": "2",
            },
        ],
        "blocks": [
            {
                "block_id": "BLK-00015",
                "topic_id": "TOPIC-0001",
                "subtopic_id": "",
                "kind": "paragraph",
            },
            {
                "block_id": "BLK-00031",
                "topic_id": "TOPIC-0002",
                "subtopic_id": "",
                "kind": "paragraph",
            },
        ],
    }
    canonical = {
        "blocks": [
            {
                "block_id": "BLK-00015",
                "order": 15,
                "kind": "paragraph",
                "display_text": (
                    "A nation-state was one in which the majority of citizens "
                    "developed a sense of common identity and shared history or "
                    "descent, forged through struggles and the actions of leaders "
                    "and common people."
                ),
            },
            {
                "block_id": "BLK-00031",
                "order": 31,
                "kind": "paragraph",
                "display_text": (
                    "Mid-eighteenth-century Germany, Italy and Switzerland were "
                    "divided into kingdoms, duchies and cantons. The Habsburg "
                    "Empire contained diverse peoples whose common tie was "
                    "allegiance to the emperor."
                ),
            },
        ],
    }
    return graph, canonical


@pytest.mark.parametrize(
    "function_name",
    ["_adjudicate_via_openai", "_critic_via_openai"],
)
def test_human_directed_phase32_openai_pair_disables_transport_retries(
    monkeypatch,
    function_name,
):
    calls: list[dict] = []

    def fake_openai(**kwargs):
        calls.append(kwargs)
        return {}

    monkeypatch.setattr(
        phase3.phase22,
        "_openai_multimodal_json",
        fake_openai,
    )
    payload = {
        "concepts": [{"concept_id": "CONCEPT-0001"}],
        "topics": [{"topic_id": "TOPIC-0001"}],
        "human_resolutions": [{"choice": "select_candidate"}],
    }

    getattr(phase32, function_name)(payload)

    assert len(calls) == 1
    assert calls[0]["single_attempt"] is True


def _combined_record() -> dict:
    return {
        "topic": "The French Revolution and the Idea of the Nation",
        "parent_concept": "Nation and State",
        "concept_title": "Nation-States and Dynastic Europe",
        "concept_details": (
            "Description: A nation-state develops common identity and shared "
            "history through political struggle, while mid-eighteenth-century "
            "Germany, Italy and Switzerland remained divided and the Habsburg "
            "Empire held diverse peoples through allegiance to an emperor.\n"
            "Achieving Mastery: Compare a nation-state with a dynastic empire."
        ),
        "keywords": "nation-state, Habsburg Empire",
        "_semantic_topic_id": "TOPIC-0001",
        "_semantic_graph_contract": "RNE-SOURCE-CONTRACT",
    }


def _split_response(payload: dict) -> dict:
    concept_id = payload["concepts"][0]["concept_id"]
    return {
        "concepts": [
            {
                "concept_id": concept_id,
                "decision": "split",
                "segments": [
                    {
                        "topic_id": "TOPIC-0001",
                        "concept_title": "Nation-State as Shared Political Identity",
                        "parent_concept": "Idea of the Nation",
                        "description": (
                            "A nation-state is a modern state in which citizens "
                            "develop a common identity and shared history or descent "
                            "through political struggle."
                        ),
                        "achieving_mastery": (
                            "Explain how shared identity distinguishes a nation-state "
                            "from rule based only on a monarch."
                        ),
                        "keywords": ["nation-state", "shared identity"],
                        "confidence": 0.999,
                        "reason": "The first source block supports this durable idea.",
                    },
                    {
                        "topic_id": "TOPIC-0002",
                        "concept_title": (
                            "Political Fragmentation and the Habsburg Empire"
                        ),
                        "parent_concept": "Europe Before Nation-States",
                        "description": (
                            "Mid-eighteenth-century Germany, Italy and Switzerland "
                            "were divided into kingdoms, duchies and cantons, while "
                            "the Habsburg Empire bound diverse peoples through "
                            "allegiance to the emperor."
                        ),
                        "achieving_mastery": (
                            "Use the political map and Habsburg example to explain "
                            "why mid-eighteenth-century Europe lacked nation-states."
                        ),
                        "keywords": [
                            "kingdoms",
                            "duchies",
                            "cantons",
                            "Habsburg Empire",
                        ],
                        "confidence": 0.999,
                        "reason": "The second source block supports this separate idea.",
                    },
                ],
                "confidence": 0.999,
                "reason": (
                    "The original row over-merged source claims from two canonical "
                    "topics."
                ),
            }
        ]
    }


def _verified_topology_review(payload: dict) -> dict:
    ids = [row["concept_id"] for row in payload["concepts"]]
    return {
        "verdict": "verified",
        "confidence": 0.999,
        "accepted_concept_ids": ids,
        "rejected_concept_ids": [],
        "issues": [],
    }


def _grounding_provider(payload: dict) -> dict:
    topic_id = payload["topic"]["topic_id"]
    block_id = "BLK-00015" if topic_id == "TOPIC-0001" else "BLK-00031"
    return {
        "concepts": [
            {
                "concept_id": concept["concept_id"],
                "source_block_ids": [block_id],
                "confidence": 0.999,
                "reason": "The canonical source block fully supports the claim.",
            }
            for concept in payload["concepts"]
        ]
    }


def _verified_grounding_review(payload: dict) -> dict:
    ids = [row["concept_id"] for row in payload["concepts"]]
    return {
        "verdict": "verified",
        "confidence": 0.999,
        "accepted_concept_ids": ids,
        "rejected_concept_ids": [],
        "issues": [],
    }


def test_rne_cross_topic_overmerge_is_split_before_topology_freeze():
    graph, canonical = _graph_and_canonical()

    repaired = phase32.adjudicate_topology(
        [_combined_record()],
        graph=graph,
        canonical=canonical,
        provider=_split_response,
        critic=_verified_topology_review,
        grounding_provider=_grounding_provider,
        grounding_critic=_verified_grounding_review,
    )

    assert [row["concept_title"] for row in repaired] == [
        "Nation-State as Shared Political Identity",
        "Political Fragmentation and the Habsburg Empire",
    ]
    assert [row["topic"] for row in repaired] == [
        "The French Revolution and the Idea of the Nation",
        "The Making of Nationalism in Europe",
    ]
    assert repaired[0]["_source_block_ids"] == ["BLK-00015"]
    assert repaired[1]["_source_block_ids"] == ["BLK-00031"]
    assert all(
        row["_source_grounding_contract"] == "api-verified-source-block-ids"
        for row in repaired
    )
    assert all(
        "Misconception/ Error Analysis" not in row["concept_details"]
        for row in repaired
    )
    assert "Nation-States and Dynastic Europe" not in {
        row["concept_title"] for row in repaired
    }


def test_whole_claim_is_moved_instead_of_unnecessarily_split():
    graph, canonical = _graph_and_canonical()
    record = _combined_record()
    record["concept_title"] = "Political Fragmentation in Dynastic Europe"
    record["concept_details"] = (
        "Description: Mid-eighteenth-century Germany, Italy and Switzerland "
        "were divided into kingdoms, duchies and cantons, while the Habsburg "
        "Empire held diverse peoples through allegiance to an emperor.\n"
        "Achieving Mastery: Explain why this political structure was not a "
        "nation-state."
    )

    def provider(payload: dict) -> dict:
        concept_id = payload["concepts"][0]["concept_id"]
        return {
            "concepts": [{
                "concept_id": concept_id,
                "decision": "move",
                "segments": [{
                    "topic_id": "TOPIC-0002",
                    "concept_title": "Political Fragmentation in Dynastic Europe",
                    "parent_concept": "Europe Before Nation-States",
                    "description": (
                        "Mid-eighteenth-century Germany, Italy and Switzerland "
                        "were divided into kingdoms, duchies and cantons, while "
                        "the Habsburg Empire bound diverse peoples through "
                        "allegiance to the emperor."
                    ),
                    "achieving_mastery": (
                        "Explain why fragmented dynastic territories were not "
                        "nation-states."
                    ),
                    "keywords": ["dynastic Europe", "Habsburg Empire"],
                    "confidence": 0.999,
                    "reason": "The complete claim belongs to Topic 2.",
                }],
                "confidence": 0.999,
                "reason": "Move is sufficient; no split is needed.",
            }]
        }

    repaired = phase32.adjudicate_topology(
        [record],
        graph=graph,
        canonical=canonical,
        provider=provider,
        critic=_verified_topology_review,
        grounding_provider=_grounding_provider,
        grounding_critic=_verified_grounding_review,
    )

    assert len(repaired) == 1
    assert repaired[0]["topic"] == "The Making of Nationalism in Europe"
    assert repaired[0]["_phase32_topology_decision"] == "move"
    assert repaired[0]["_source_block_ids"] == ["BLK-00031"]


def test_critic_disagreement_pauses_then_resume_uses_one_directed_pair(
    monkeypatch,
):
    graph, canonical = _graph_and_canonical()
    monkeypatch.setenv("AEGIS_SEMANTIC_ACCEPTANCE_MIN_CONFIDENCE", "0.90")
    provider_calls = 0
    critic_calls = 0

    def provider(payload: dict) -> dict:
        nonlocal provider_calls
        provider_calls += 1
        if provider_calls == 2:
            assert payload["human_resolutions"][0]["choice"] == (
                "accept_recommended"
            )
            assert "durable boundary" in (
                payload["human_resolutions"][0]["critic_feedback"]["conflict"]
            )
        return _split_response(payload)

    def critic(payload: dict) -> dict:
        nonlocal critic_calls
        critic_calls += 1
        if critic_calls == 1:
            concept_id = payload["concepts"][0]["concept_id"]
            return {
                "verdict": "rejected",
                "confidence": 0.91,
                "accepted_concept_ids": [],
                "rejected_concept_ids": [concept_id],
                "issues": [
                    "The split needs a human decision on the durable boundary."
                ],
            }
        return _verified_topology_review(payload)

    with pytest.raises(semantic_recovery.HumanDecisionRequired) as paused:
        phase32.adjudicate_topology(
            [_combined_record()],
            graph=graph,
            canonical=canonical,
            provider=provider,
            critic=critic,
            grounding_provider=_grounding_provider,
            grounding_critic=_verified_grounding_review,
        )

    assert provider_calls == 1
    assert critic_calls == 1
    pending = paused.value.pending_decision
    recommended = next(
        option for option in pending["options"]
        if option["choice"] == "accept_recommended"
    )
    resolution = {
        **copy.deepcopy(pending),
        "choice": "accept_recommended",
        "target_id": recommended["target_id"],
        "instruction": "",
    }
    with phase33.human_resolution_context([resolution]):
        repaired = phase32.adjudicate_topology(
            [_combined_record()],
            graph=graph,
            canonical=canonical,
            provider=provider,
            critic=critic,
            grounding_provider=_grounding_provider,
            grounding_critic=_verified_grounding_review,
        )

    assert provider_calls == 2
    assert critic_calls == 2
    assert [row["_phase32_topology_decision"] for row in repaired] == [
        "split",
        "split",
    ]


def test_low_confidence_does_not_authorize_creation_of_a_new_concept(monkeypatch):
    graph, canonical = _graph_and_canonical()
    calls = 0

    def provider(payload: dict) -> dict:
        nonlocal calls
        calls += 1
        response = _split_response(payload)
        response["concepts"][0]["confidence"] = 0.72
        return response

    monkeypatch.setenv("AEGIS_PHASE32_TOPOLOGY_MAX_ATTEMPTS", "2")
    with pytest.raises(semantic_recovery.HumanDecisionRequired) as paused:
        phase32.adjudicate_topology(
            [_combined_record()],
            graph=graph,
            canonical=canonical,
            provider=provider,
            critic=lambda _payload: (_ for _ in ()).throw(
                AssertionError("critic must not review a low-confidence proposal")
            ),
            grounding_provider=_grounding_provider,
            grounding_critic=_verified_grounding_review,
        )

    assert calls == 1
    pending = paused.value.pending_decision
    assert pending["kind"] == "phase32_concept_blueprint_semantic_conflict"
    assert "topology confidence 0.720" in pending["conflict"]
    assert "provider" in pending["diagnosis"].casefold()
    assert "before independent review" in pending["diagnosis"].casefold()
    assert {row["choice"] for row in pending["options"]} >= {
        "select_candidate",
        "replace_source",
        "custom_instruction",
    }


def test_low_segment_confidence_pauses_without_a_retry_or_critic(monkeypatch):
    graph, canonical = _graph_and_canonical()
    provider_calls = 0
    critic_calls = 0

    def provider(payload: dict) -> dict:
        nonlocal provider_calls
        provider_calls += 1
        response = _split_response(payload)
        response["concepts"][0]["segments"][0]["confidence"] = 0.72
        return response

    def critic(_payload: dict) -> dict:
        nonlocal critic_calls
        critic_calls += 1
        raise AssertionError("critic must not review a low-confidence segment")

    monkeypatch.setenv("AEGIS_PHASE32_TOPOLOGY_MAX_ATTEMPTS", "5")
    with pytest.raises(semantic_recovery.HumanDecisionRequired) as paused:
        phase32.adjudicate_topology(
            [_combined_record()],
            graph=graph,
            canonical=canonical,
            provider=provider,
            critic=critic,
            grounding_provider=_grounding_provider,
            grounding_critic=_verified_grounding_review,
        )

    assert provider_calls == 1
    assert critic_calls == 0
    assert "segment 1 confidence 0.720" in paused.value.pending_decision["conflict"]
    assert "provider" in paused.value.pending_decision["diagnosis"].casefold()
    assert phase32._topology_parse_errors_are_mechanical([
        "TOPOLOGY-CONCEPT-0001 retirement confidence 0.950 is below 0.960"
    ]) is False


def test_keep_decision_cannot_silently_rewrite_the_existing_claim(monkeypatch):
    graph, canonical = _graph_and_canonical()
    record = {
        "topic": "The French Revolution and the Idea of the Nation",
        "parent_concept": "Idea of the Nation",
        "concept_title": "Nation-State as Shared Political Identity",
        "concept_details": (
            "Description: A nation-state develops common identity and shared "
            "history through political struggle.\n"
            "Achieving Mastery: Explain the role of shared identity."
        ),
        "_semantic_topic_id": "TOPIC-0001",
        "_semantic_graph_contract": "RNE-SOURCE-CONTRACT",
    }

    def provider(payload: dict) -> dict:
        concept_id = payload["concepts"][0]["concept_id"]
        return {
            "concepts": [{
                "concept_id": concept_id,
                "decision": "keep",
                "segments": [{
                    "topic_id": "TOPIC-0001",
                    "concept_title": "Nation-State as Shared Political Identity",
                    "parent_concept": "Idea of the Nation",
                    "description": "A different rewritten claim.",
                    "achieving_mastery": "Explain the role of shared identity.",
                    "keywords": ["nation-state"],
                    "confidence": 0.999,
                    "reason": "Invalid rewrite disguised as keep.",
                }],
                "confidence": 0.999,
                "reason": "Keep.",
            }]
        }

    monkeypatch.setenv("AEGIS_PHASE32_TOPOLOGY_MAX_ATTEMPTS", "1")
    with pytest.raises(semantic_recovery.HumanDecisionRequired) as paused:
        phase32.adjudicate_topology(
            [record],
            graph=graph,
            canonical=canonical,
            provider=provider,
            critic=_verified_topology_review,
            grounding_provider=_grounding_provider,
            grounding_critic=_verified_grounding_review,
        )
    assert "keep decision rewrote" in paused.value.pending_decision["conflict"]


def test_verified_topology_and_grounding_are_reused_from_cache(tmp_path):
    graph, canonical = _graph_and_canonical()
    provider_calls = 0
    critic_calls = 0
    grounding_calls = 0
    grounding_critic_calls = 0

    def provider(payload: dict) -> dict:
        nonlocal provider_calls
        provider_calls += 1
        return _split_response(payload)

    def critic(payload: dict) -> dict:
        nonlocal critic_calls
        critic_calls += 1
        return _verified_topology_review(payload)

    def ground(payload: dict) -> dict:
        nonlocal grounding_calls
        grounding_calls += 1
        return _grounding_provider(payload)

    def ground_critic(payload: dict) -> dict:
        nonlocal grounding_critic_calls
        grounding_critic_calls += 1
        return _verified_grounding_review(payload)

    session = {"artifact_dir": tmp_path, "canonical": canonical}
    with phase3.activate_session(session), phase3.activate(graph):
        first = phase32.adjudicate_topology(
            [_combined_record()],
            graph=graph,
            canonical=canonical,
            provider=provider,
            critic=critic,
            grounding_provider=ground,
            grounding_critic=ground_critic,
        )
        second = phase32.adjudicate_topology(
            [_combined_record()],
            graph=graph,
            canonical=canonical,
            provider=lambda _payload: (_ for _ in ()).throw(
                AssertionError("topology provider must not run on cache hit")
            ),
            critic=lambda _payload: (_ for _ in ()).throw(
                AssertionError("topology critic must not run on cache hit")
            ),
            grounding_provider=lambda _payload: (_ for _ in ()).throw(
                AssertionError("grounding provider must not run on cache hit")
            ),
            grounding_critic=lambda _payload: (_ for _ in ()).throw(
                AssertionError("grounding critic must not run on cache hit")
            ),
        )

    assert first == second
    assert provider_calls == 1
    assert critic_calls == 1
    assert grounding_calls == 2
    assert grounding_critic_calls == 2
    assert (tmp_path / phase32._CACHE_FILENAME).exists()


def test_exact_grounding_must_pass_before_repaired_topology_can_freeze():
    graph, canonical = _graph_and_canonical()

    def rejected_grounding(payload: dict) -> dict:
        ids = [row["concept_id"] for row in payload["concepts"]]
        return {
            "verdict": "rejected",
            "confidence": 0.999,
            "accepted_concept_ids": [],
            "rejected_concept_ids": ids,
            "issues": ["The repaired claim still lacks exact block support."],
        }

    with pytest.raises(semantic_recovery.HumanDecisionRequired) as paused:
        phase32.adjudicate_topology(
            [_combined_record()],
            graph=graph,
            canonical=canonical,
            provider=_split_response,
            critic=_verified_topology_review,
            grounding_provider=_grounding_provider,
            grounding_critic=rejected_grounding,
        )
    assert paused.value.pending_decision["kind"] == (
        "phase31_source_grounding_semantic_conflict"
    )
    assert "lacks exact block support" in paused.value.pending_decision["conflict"]

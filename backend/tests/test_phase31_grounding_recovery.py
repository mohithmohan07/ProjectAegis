"""Regression coverage for Phase 3.1 concept grounding and resume recovery."""
from __future__ import annotations

import copy

import pytest

from app.services import canonical_source_phase3 as phase3
from app.services import canonical_source_phase31_grounding_contract as phase31


def _source_graph() -> tuple[dict, dict]:
    graph = {
        "source_contract_hash": "SOURCE-CONTRACT-1",
        "metadata": {
            "subject": "History",
            "chapter_title": "The Rise of Nationalism in Europe",
        },
        "topics": [{
            "topic_id": "TOPIC-0001",
            "order": 1,
            "title": "The Making of Nationalism in Europe",
        }],
        "blocks": [
            {
                "block_id": "BLK-0001",
                "topic_id": "TOPIC-0001",
                "subtopic_id": "TOPIC-0001-SUB-001",
                "kind": "paragraph",
            },
            {
                "block_id": "BLK-0002",
                "topic_id": "TOPIC-0001",
                "subtopic_id": "TOPIC-0001-SUB-002",
                "kind": "paragraph",
            },
        ],
    }
    canonical = {
        "blocks": [
            {
                "block_id": "BLK-0001",
                "kind": "paragraph",
                "display_text": (
                    "A landed aristocracy dominated social and political life "
                    "while most people belonged to the peasantry."
                ),
            },
            {
                "block_id": "BLK-0002",
                "kind": "paragraph",
                "display_text": (
                    "The Zollverein abolished tariff barriers and supported "
                    "economic integration among the German states."
                ),
            },
        ],
    }
    return graph, canonical


def _record(
    *,
    title: str = "Landed Aristocracy and Peasant Society",
    details: str | None = None,
) -> dict:
    return {
        "topic": "The Making of Nationalism in Europe",
        "parent_concept": "Social Structure",
        "concept_title": title,
        "concept_details": details or (
            "Description: A landed aristocracy dominated social and political "
            "life while most people belonged to the peasantry."
        ),
        "keywords": "aristocracy, peasantry",
        "_semantic_topic_id": "TOPIC-0001",
        "_semantic_graph_contract": "SOURCE-CONTRACT-1",
    }


def _verified_review(payload: dict) -> dict:
    ids = [row["concept_id"] for row in payload["concepts"]]
    return {
        "verdict": "verified",
        "confidence": 0.999,
        "accepted_concept_ids": ids,
        "rejected_concept_ids": [],
        "issues": [],
    }


def test_grounding_projects_only_source_facing_description():
    graph, canonical = _source_graph()
    records = [_record(details=(
        "Description: A landed aristocracy dominated social and political life "
        "while most people belonged to the peasantry.\n"
        "Achieving Mastery: Explain how class structure shaped political power. "
        "// Misconception/ Error Analysis: Misconceptions: A learner believes "
        "all Europeans had equal political influence.; Error Analysis: The "
        "learner treats peasant and aristocratic roles as interchangeable."
    ))]

    def provider(payload: dict) -> dict:
        concept = payload["concepts"][0]
        assert concept["source_claim"].endswith("the peasantry.")
        assert "Achieving Mastery" not in concept["source_claim"]
        assert "Misconception" not in concept["source_claim"]
        assert "learner" not in concept["source_claim"].casefold()
        return {"concepts": [{
            "concept_id": concept["concept_id"],
            "source_block_ids": ["BLK-0001"],
            "confidence": 0.999,
            "reason": "The paragraph explicitly supports the source claim.",
        }]}

    grounded = phase31.ground_concepts(
        records,
        graph=graph,
        canonical=canonical,
        provider=provider,
        critic=_verified_review,
    )

    assert grounded[0]["_source_block_ids"] == ["BLK-0001"]
    assert grounded[0]["_source_grounding_contract"] == (
        "api-verified-source-block-ids"
    )


def test_culmination_grounding_is_derived_from_verified_normal_concepts():
    graph, canonical = _source_graph()
    records = [
        _record(),
        _record(
            title="Culmination: Social and Economic Nationalism",
            details=(
                "Description: Recap of Landed Aristocracy and Peasant Society "
                "and Economic Integration."
            ),
        ),
    ]

    def provider(payload: dict) -> dict:
        # Culmination rows are generated recaps, not independent textbook claims.
        assert len(payload["concepts"]) == 1
        return {"concepts": [{
            "concept_id": payload["concepts"][0]["concept_id"],
            "source_block_ids": ["BLK-0001"],
            "confidence": 0.999,
            "reason": "The paragraph directly supports the normal concept.",
        }]}

    grounded = phase31.ground_concepts(
        records,
        graph=graph,
        canonical=canonical,
        provider=provider,
        critic=_verified_review,
    )

    assert grounded[1]["_source_block_ids"] == ["BLK-0001"]
    assert grounded[1]["_source_grounding_contract"] == (
        "derived-from-verified-topic-concepts"
    )


def test_critic_rejection_repairs_only_unresolved_concepts(monkeypatch):
    graph, canonical = _source_graph()
    records = [_record(
        title="Economic Integration through the Zollverein",
        details=(
            "Description: The Zollverein reduced tariff barriers and promoted "
            "economic integration among German states."
        ),
    )]
    provider_calls = 0
    critic_calls = 0

    def provider(payload: dict) -> dict:
        nonlocal provider_calls
        provider_calls += 1
        concept_id = payload["concepts"][0]["concept_id"]
        block = "BLK-0001" if provider_calls == 1 else "BLK-0002"
        if provider_calls == 2:
            assert payload["critic_feedback"]["rejected_concept_ids"] == [
                concept_id
            ]
            assert payload["previous_grounding"][0][
                "source_block_ids"
            ] == ["BLK-0001"]
        return {"concepts": [{
            "concept_id": concept_id,
            "source_block_ids": [block],
            "confidence": 0.999,
            "reason": "Bounded source selection.",
        }]}

    def critic(payload: dict) -> dict:
        nonlocal critic_calls
        critic_calls += 1
        concept_id = payload["concepts"][0]["concept_id"]
        selected = payload["proposed_grounding"][0]["source_block_ids"]
        if critic_calls == 1:
            assert selected == ["BLK-0001"]
            return {
                "verdict": "rejected",
                "confidence": 0.92,
                "accepted_concept_ids": [],
                "rejected_concept_ids": [concept_id],
                "issues": [
                    "The selected block discusses class structure, not the Zollverein."
                ],
            }
        assert selected == ["BLK-0002"]
        return {
            "verdict": "verified",
            "confidence": 0.999,
            "accepted_concept_ids": [concept_id],
            "rejected_concept_ids": [],
            "issues": [],
        }

    monkeypatch.setenv("AEGIS_PHASE3_GROUNDING_MAX_ATTEMPTS", "3")
    grounded = phase31.ground_concepts(
        records,
        graph=graph,
        canonical=canonical,
        provider=provider,
        critic=critic,
    )

    assert provider_calls == 2
    assert critic_calls == 2
    assert grounded[0]["_source_block_ids"] == ["BLK-0002"]


def test_exhausted_grounding_reports_topic_and_critic_reason(monkeypatch):
    graph, canonical = _source_graph()
    records = [_record()]

    def provider(payload: dict) -> dict:
        return {"concepts": [{
            "concept_id": payload["concepts"][0]["concept_id"],
            "source_block_ids": ["BLK-0001"],
            "confidence": 0.999,
            "reason": "Candidate grounding.",
        }]}

    monkeypatch.setenv("AEGIS_PHASE3_GROUNDING_MAX_ATTEMPTS", "2")
    with pytest.raises(
        ValueError,
        match=(
            "The Making of Nationalism in Europe.*"
            "block does not support the causal claim.*invalid or uncertain"
        ),
    ):
        phase31.ground_concepts(
            records,
            graph=graph,
            canonical=canonical,
            provider=provider,
            critic=lambda _payload: {
                "verdict": "rejected",
                "confidence": 0.91,
                "issues": ["block does not support the causal claim"],
            },
        )


def test_verified_topic_grounding_is_reused_from_artifact_cache(tmp_path):
    graph, canonical = _source_graph()
    records = [_record()]
    provider_calls = 0
    critic_calls = 0

    def provider(payload: dict) -> dict:
        nonlocal provider_calls
        provider_calls += 1
        return {"concepts": [{
            "concept_id": payload["concepts"][0]["concept_id"],
            "source_block_ids": ["BLK-0001"],
            "confidence": 0.999,
            "reason": "The source explicitly supports the claim.",
        }]}

    def critic(payload: dict) -> dict:
        nonlocal critic_calls
        critic_calls += 1
        return _verified_review(payload)

    session = {
        "artifact_dir": tmp_path,
        "canonical": canonical,
    }
    with phase3.activate_session(session), phase3.activate(graph):
        first = phase31.ground_concepts(
            copy.deepcopy(records),
            graph=graph,
            canonical=canonical,
            provider=provider,
            critic=critic,
        )
        second = phase31.ground_concepts(
            copy.deepcopy(records),
            graph=graph,
            canonical=canonical,
            provider=lambda _payload: (_ for _ in ()).throw(
                AssertionError("provider should not run on cache hit")
            ),
            critic=lambda _payload: (_ for _ in ()).throw(
                AssertionError("critic should not run on cache hit")
            ),
        )

    assert provider_calls == 1
    assert critic_calls == 1
    assert first[0]["_source_block_ids"] == second[0]["_source_block_ids"]
    assert (tmp_path / phase31._GROUNDING_CACHE_FILENAME).exists()


def test_partial_acceptance_survives_failure_and_only_rejected_ids_resume(
    tmp_path,
    monkeypatch,
):
    graph, canonical = _source_graph()
    records = [
        _record(
            title=f"Independent concept {number}",
            details=f"Description: Independently grounded source claim {number}.",
        )
        for number in range(1, 8)
    ]
    provider_batches: list[list[str]] = []
    critic_calls = 0

    def provider(payload: dict) -> dict:
        ids = [row["concept_id"] for row in payload["concepts"]]
        provider_batches.append(ids)
        return {"concepts": [
            {
                "concept_id": concept_id,
                "source_block_ids": [
                    "BLK-0001" if concept_id.endswith(tuple("12345"))
                    else "BLK-0002"
                ],
                "confidence": 0.999,
                "reason": "The selected block supports this source claim.",
            }
            for concept_id in ids
        ]}

    def critic(payload: dict) -> dict:
        nonlocal critic_calls
        critic_calls += 1
        ids = [row["concept_id"] for row in payload["concepts"]]
        if critic_calls == 1:
            assert len(ids) == 7
            return {
                "verdict": "rejected",
                "confidence": 0.999,
                "accepted_concept_ids": ids[:5],
                "rejected_concept_ids": ids[5:],
                "issues": ["The final two concepts need different evidence."],
            }
        assert ids == provider_batches[0][5:]
        return _verified_review(payload)

    monkeypatch.setenv("AEGIS_PHASE3_GROUNDING_MAX_ATTEMPTS", "1")
    session = {
        "artifact_dir": tmp_path,
        "canonical": canonical,
    }
    with phase3.activate_session(session), phase3.activate(graph):
        with pytest.raises(ValueError, match="failed independent verification"):
            phase31.ground_concepts(
                copy.deepcopy(records),
                graph=graph,
                canonical=canonical,
                provider=provider,
                critic=critic,
            )
        resumed = phase31.ground_concepts(
            copy.deepcopy(records),
            graph=graph,
            canonical=canonical,
            provider=provider,
            critic=critic,
        )

    assert len(provider_batches) == 2
    assert len(provider_batches[0]) == 7
    assert provider_batches[1] == provider_batches[0][5:]
    assert critic_calls == 2
    assert [row["_source_block_ids"] for row in resumed[:5]] == [
        ["BLK-0001"]
    ] * 5
    assert [row["_source_block_ids"] for row in resumed[5:]] == [
        ["BLK-0002"]
    ] * 2


def test_later_learner_analysis_does_not_invalidate_source_claim_cache(
    tmp_path,
):
    graph, canonical = _source_graph()
    original = _record(details=(
        "Description: A landed aristocracy dominated social and political life "
        "while most people belonged to the peasantry.\n"
        "Achieving Mastery: Explain the class relationship."
    ))
    enriched = copy.deepcopy(original)
    enriched["concept_details"] = (
        "Description: A landed aristocracy dominated social and political life "
        "while most people belonged to the peasantry.\n"
        "Achieving Mastery: Compare political power across both groups. // "
        "Misconception/ Error Analysis: A learner confuses their roles."
    )
    calls = 0

    def provider(payload: dict) -> dict:
        nonlocal calls
        calls += 1
        return {"concepts": [{
            "concept_id": payload["concepts"][0]["concept_id"],
            "source_block_ids": ["BLK-0001"],
            "confidence": 0.999,
            "reason": "The source explicitly supports the Description claim.",
        }]}

    session = {
        "artifact_dir": tmp_path,
        "canonical": canonical,
    }
    with phase3.activate_session(session), phase3.activate(graph):
        first = phase31.ground_concepts(
            [original],
            graph=graph,
            canonical=canonical,
            provider=provider,
            critic=_verified_review,
        )
        second = phase31.ground_concepts(
            [enriched],
            graph=graph,
            canonical=canonical,
            provider=lambda _payload: (_ for _ in ()).throw(
                AssertionError("generated pedagogy must not invalidate grounding")
            ),
            critic=lambda _payload: (_ for _ in ()).throw(
                AssertionError("cached source-claim review should be reused")
            ),
        )

    assert calls == 1
    assert first[0]["_source_block_ids"] == second[0]["_source_block_ids"]


@pytest.mark.parametrize(
    "drift",
    [
        "source_claim",
        "source_contract",
        "semantic_context",
        "pdf",
        "source_block_text",
        "topic",
    ],
)
def test_grounding_cache_invalidates_on_source_or_context_drift(
    tmp_path,
    drift,
):
    graph, canonical = _source_graph()
    graph.update({
        "semantic_context_hash": "SEMANTIC-CONTEXT-1",
        "source_sha256": "SOURCE-SHA-1",
        "vision_evidence": {"pdf_sha256": "PDF-SHA-1"},
    })
    records = [_record()]
    calls = 0

    def provider(payload: dict) -> dict:
        nonlocal calls
        calls += 1
        return {"concepts": [{
            "concept_id": payload["concepts"][0]["concept_id"],
            "source_block_ids": ["BLK-0001"],
            "confidence": 0.999,
            "reason": "Freshly reviewed against the current source identity.",
        }]}

    session = {
        "artifact_dir": tmp_path,
        "canonical": canonical,
    }
    with phase3.activate_session(session), phase3.activate(graph):
        phase31.ground_concepts(
            copy.deepcopy(records),
            graph=graph,
            canonical=canonical,
            provider=provider,
            critic=_verified_review,
        )

        changed_graph = copy.deepcopy(graph)
        changed_canonical = copy.deepcopy(canonical)
        changed_records = copy.deepcopy(records)
        if drift == "source_claim":
            changed_records[0]["concept_details"] = (
                "Description: A materially changed source claim."
            )
        elif drift == "source_contract":
            changed_graph["source_contract_hash"] = "SOURCE-CONTRACT-2"
        elif drift == "semantic_context":
            changed_graph["semantic_context_hash"] = "SEMANTIC-CONTEXT-2"
        elif drift == "pdf":
            changed_graph["vision_evidence"]["pdf_sha256"] = "PDF-SHA-2"
        elif drift == "source_block_text":
            changed_canonical["blocks"][0]["display_text"] += " Changed."
        elif drift == "topic":
            changed_graph["topics"][0]["order"] = 2

        phase31.ground_concepts(
            changed_records,
            graph=changed_graph,
            canonical=changed_canonical,
            provider=provider,
            critic=_verified_review,
        )

    assert calls == 2


def test_validated_final_topology_cache_skips_repeated_learner_analysis(
    tmp_path,
):
    graph, canonical = _source_graph()
    records = [_record()]
    calls = 0

    def original_prepare(rows, **_kwargs):
        nonlocal calls
        calls += 1
        prepared = copy.deepcopy(rows)
        prepared[0]["concept_details"] += (
            " // Misconception/ Error Analysis: Misconceptions: A learner "
            "believes social rank had no political effect.; Error Analysis: "
            "The learner merges aristocratic and peasant roles."
        )
        return prepared

    kwargs = {
        "subject": "History",
        "mmd_text": "canonical semantic source",
        "meta": {"chapter_title": "The Rise of Nationalism in Europe"},
        "source_sections": [{"section_id": "SEC-0001"}],
        "source_topic_excerpts": [{"topic": "The Making of Nationalism in Europe"}],
        "method_anchors": [],
    }
    session = {
        "artifact_dir": tmp_path,
        "canonical": canonical,
    }
    with phase3.activate_session(session), phase3.activate(graph):
        first = phase31._prepare_topology_with_cache(
            original_prepare,
            copy.deepcopy(records),
            (),
            copy.deepcopy(kwargs),
        )
        second = phase31._prepare_topology_with_cache(
            original_prepare,
            copy.deepcopy(records),
            (),
            copy.deepcopy(kwargs),
        )

    assert calls == 1
    assert first == second
    assert "Misconception/ Error Analysis" in second[0]["concept_details"]
    assert (tmp_path / phase31._TOPOLOGY_CACHE_FILENAME).exists()

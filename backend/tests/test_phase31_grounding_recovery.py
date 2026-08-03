"""Regression coverage for Phase 3.1 concept grounding and resume recovery."""
from __future__ import annotations

import copy

import pytest

from app import schemas
from app.services import canonical_source_phase3 as phase3
from app.services import canonical_source_phase31_grounding_contract as phase31
from app.services import canonical_source_phase33_preflight_contract as phase33
from app.services import grounding_certificate
from app.services import semantic_recovery


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
                "source_start": 100,
                "source_end": 192,
            },
            {
                "block_id": "BLK-0002",
                "topic_id": "TOPIC-0001",
                "subtopic_id": "TOPIC-0001-SUB-002",
                "kind": "paragraph",
                "source_start": 240,
                "source_end": 330,
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
                "source_start": 100,
                "source_end": 192,
            },
            {
                "block_id": "BLK-0002",
                "kind": "paragraph",
                "display_text": (
                    "The Zollverein abolished tariff barriers and supported "
                    "economic integration among the German states."
                ),
                "source_start": 240,
                "source_end": 330,
            },
        ],
    }
    return graph, canonical


@pytest.mark.parametrize("function_name", ["_ground_via_openai", "_critic_via_openai"])
def test_human_directed_phase31_openai_pair_disables_transport_retries(
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
        "concepts": [{"concept_id": "CONCEPT-GROUND-0001"}],
        "source_blocks": [{"block_id": "BLK-0001"}],
        "human_resolutions": [{"choice": "select_candidate"}],
    }

    getattr(phase31, function_name)(payload)

    assert len(calls) == 1
    assert calls[0]["single_attempt"] is True


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


def test_critic_rejection_pauses_then_one_directed_pair_resumes(monkeypatch):
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
            assert payload["human_resolutions"][0]["choice"] == (
                "select_candidate"
            )
            selected_candidate = payload["human_resolutions"][0][
                "selected_candidate"
            ]
            assert selected_candidate["action"] == "use_verified_evidence"
            assert selected_candidate["source_block_ids"] == ["BLK-0002"]
            assert selected_candidate["source_topic_id"] == "TOPIC-0001"
            assert phase31.early_gate.candidate_binding_is_valid(
                selected_candidate
            )
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
        assert payload["human_resolutions"][0]["selected_candidate"][
            "source_block_ids"
        ] == ["BLK-0002"]
        return {
            "verdict": "verified",
            "confidence": 0.999,
            "accepted_concept_ids": [concept_id],
            "rejected_concept_ids": [],
            "issues": [],
        }

    monkeypatch.setenv("AEGIS_PHASE3_GROUNDING_MAX_ATTEMPTS", "3")
    with pytest.raises(semantic_recovery.HumanDecisionRequired) as paused:
        phase31.ground_concepts(
            records,
            graph=graph,
            canonical=canonical,
            provider=provider,
            critic=critic,
        )

    pending = paused.value.pending_decision
    assert provider_calls == 1
    assert critic_calls == 1
    assert pending["kind"] == "phase31_source_grounding_semantic_conflict"
    assert {row["choice"] for row in pending["options"]} >= {
        "select_candidate",
        "replace_source",
        "custom_instruction",
    }
    selected = next(
        row for row in pending["candidates"]
        if "Zollverein" in row["coverage"]
    )
    resolution = {
        **copy.deepcopy(pending),
        "choice": "select_candidate",
        "target_id": selected["target_id"],
        "instruction": "",
    }
    with phase33.human_resolution_context([resolution]):
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


def test_review_band_grounding_pauses_after_one_pair(monkeypatch):
    graph, canonical = _source_graph()
    records = [_record()]

    def provider(payload: dict) -> dict:
        return {"concepts": [{
            "concept_id": payload["concepts"][0]["concept_id"],
            "source_block_ids": ["BLK-0001"],
            "confidence": 0.999,
            "reason": "Candidate grounding.",
        }]}

    provider_calls = 0
    critic_calls = 0

    def counted_provider(payload: dict) -> dict:
        nonlocal provider_calls
        provider_calls += 1
        return provider(payload)

    def review_band_critic(_payload: dict) -> dict:
        nonlocal critic_calls
        critic_calls += 1
        return {
            "verdict": "rejected",
            "confidence": 0.91,
            "issues": ["block does not support the causal claim"],
        }

    monkeypatch.setenv("AEGIS_PHASE3_GROUNDING_MAX_ATTEMPTS", "5")
    monkeypatch.setenv("AEGIS_SEMANTIC_ACCEPTANCE_MIN_CONFIDENCE", "0.90")
    with pytest.raises(semantic_recovery.HumanDecisionRequired) as paused:
        phase31.ground_concepts(
            records,
            graph=graph,
            canonical=canonical,
            provider=counted_provider,
            critic=review_band_critic,
        )
    assert provider_calls == 1
    assert critic_calls == 1
    assert "0.900–0.919" in paused.value.pending_decision["diagnosis"]
    assert "causal claim" in paused.value.pending_decision["conflict"]


def test_lowered_env_cannot_send_rejected_grounding_to_critic(monkeypatch):
    graph, canonical = _source_graph()
    provider_calls = 0
    critic_calls = 0

    def rejected_provider(payload: dict) -> dict:
        nonlocal provider_calls
        provider_calls += 1
        return {"concepts": [{
            "concept_id": payload["concepts"][0]["concept_id"],
            "source_block_ids": ["BLK-0001"],
            "confidence": 0.88,
            "reason": "Uncertain source match.",
        }]}

    def critic_must_not_run(_payload: dict) -> dict:
        nonlocal critic_calls
        critic_calls += 1
        raise AssertionError("critic must not rescue a rejected proposal")

    monkeypatch.setenv("AEGIS_SEMANTIC_ACCEPTANCE_MIN_CONFIDENCE", "0.85")
    with pytest.raises(semantic_recovery.HumanDecisionRequired) as paused:
        phase31.ground_concepts(
            [_record()],
            graph=graph,
            canonical=canonical,
            provider=rejected_provider,
            critic=critic_must_not_run,
        )

    assert provider_calls == 1
    assert critic_calls == 0
    assert "confidence 0.880 is below 0.920" in (
        paused.value.pending_decision["conflict"]
    )


def test_critic_partition_contradiction_is_never_cached_as_accepted():
    concept_id = "CONCEPT-GROUND-0001"
    state = phase31._review_state(
        {
            "verdict": "rejected",
            "confidence": 0.999,
            "accepted_concept_ids": [concept_id],
            "rejected_concept_ids": [concept_id],
            "issues": [f"{concept_id} is not source-supported."],
        },
        concept_ids={concept_id},
    )

    assert state["accepted"] == set()
    assert state["rejected"] == {concept_id}


def test_negative_block_mention_is_not_treated_as_a_recommendation():
    graph, canonical = _source_graph()
    records = [_record()]
    topic = graph["topics"][0]
    source_blocks, payload_blocks = phase31._candidate_blocks(
        graph, canonical, "TOPIC-0001"
    )
    concepts, _ = phase31._concept_payload(records, [0])
    candidates = phase31._grounding_candidates(
        graph=graph,
        concept=concepts[0],
        source_blocks=payload_blocks,
    )
    identity = phase31._grounding_identity(
        graph=graph,
        topic=topic,
        concept=concepts[0],
        candidates=candidates,
        source_blocks=payload_blocks,
    )

    pending = phase31._grounding_pending_decision(
        identity=identity,
        graph=graph,
        topic=topic,
        concept=concepts[0],
        candidates=candidates,
        source_blocks=payload_blocks,
        issues=[
            "BLK-0001 does not support the claim; BLK-0002 is the relevant evidence."
        ],
        rejected_ids=[concepts[0]["concept_id"]],
    )

    assert source_blocks
    assert not any(
        row["choice"] == "accept_recommended" for row in pending["options"]
    )
    assert next(
        row for row in pending["options"] if row["choice"] == "select_candidate"
    )["recommended"] is True


def test_many_evidence_candidates_keep_repairs_and_exact_block_bindings():
    graph, canonical = _source_graph()
    graph["topics"].extend([
        {
            "topic_id": "TOPIC-0002",
            "order": 2,
            "title": "The Age of Revolutions",
        },
        {
            "topic_id": "TOPIC-0003",
            "order": 3,
            "title": "Visualising the Nation",
        },
    ])
    for number in range(3, 105):
        block_id = f"BLK-{number:04d}"
        graph["blocks"].append({
            "block_id": block_id,
            "topic_id": "TOPIC-0001",
            "subtopic_id": f"TOPIC-0001-SUB-{number:03d}",
            "kind": "paragraph",
            "page_number": number,
        })
        canonical["blocks"].append({
            "block_id": block_id,
            "kind": "paragraph",
            "display_text": f"Verified source paragraph number {number}.",
            "page_number": number,
        })

    _usable, source_blocks = phase31._candidate_blocks(
        graph, canonical, "TOPIC-0001"
    )
    concepts, _ = phase31._concept_payload([_record()], [0])
    concept = concepts[0]
    candidates = phase31._grounding_candidates(
        graph=graph,
        concept=concept,
        source_blocks=source_blocks,
    )

    assert sum(
        row["action"] == "use_verified_evidence" for row in candidates
    ) > 8
    first_topology = next(
        index
        for index, row in enumerate(candidates)
        if row["action"] != "use_verified_evidence"
    )
    assert all(
        row["action"] == "use_verified_evidence"
        for row in candidates[:first_topology]
    )
    assert {row["action"] for row in candidates} >= {
        "use_verified_evidence",
        "refine",
        "split",
        "move",
    }
    assert {
        row["target_topic_id"]
        for row in candidates
        if row["action"] == "move"
    } == {"TOPIC-0002", "TOPIC-0003"}
    assert len(candidates) <= phase31._PUBLIC_GROUNDING_CANDIDATE_LIMIT
    assert all(
        concept["source_claim"] not in row["coverage"]
        for row in candidates
        if row["action"] != "use_verified_evidence"
    )

    evidence = next(
        row for row in candidates
        if row["source_block_ids"] == ["BLK-0017"]
    )
    assert evidence["source_topic_id"] == "TOPIC-0001"
    assert evidence["source_page"] == "17"
    assert evidence["text_sha256"] == phase3._sha256_text(
        evidence["coverage"]
    )
    assert phase31.early_gate.candidate_binding_is_valid(evidence)

    identity = phase31._grounding_identity(
        graph=graph,
        topic=graph["topics"][0],
        concept=concept,
        candidates=candidates,
        source_blocks=source_blocks,
    )
    pending = phase31._grounding_pending_decision(
        identity=identity,
        graph=graph,
        topic=graph["topics"][0],
        concept=concept,
        candidates=candidates,
        source_blocks=source_blocks,
        issues=["The first mapping was not minimally sufficient."],
        rejected_ids=[concept["concept_id"]],
    )
    public_evidence = next(
        row for row in pending["candidates"]
        if row["source_block_ids"] == ["BLK-0017"]
    )
    assert public_evidence["action"] == "use_verified_evidence"
    assert public_evidence["binding_hash"] == evidence["binding_hash"]
    assert public_evidence["boundary_relation"] == evidence[
        "boundary_relation"
    ]


def test_rejected_compound_claim_exposes_late_blocks_and_exact_offsets():
    graph, canonical = _source_graph()
    for number in range(3, 33):
        block_id = f"BLK-{number:04d}"
        source_start = number * 1_000
        text = (
            "The Zollverein removed tariff barriers and joined the German "
            "states through economic integration."
            if number == 30
            else f"Verified contextual source paragraph {number}."
        )
        graph["blocks"].append({
            "block_id": block_id,
            "topic_id": "TOPIC-0001",
            "subtopic_id": f"TOPIC-0001-SUB-{number:03d}",
            "kind": "paragraph",
            "page_number": number,
            "source_start": source_start,
            "source_end": source_start + len(text),
        })
        canonical["blocks"].append({
            "block_id": block_id,
            "kind": "paragraph",
            "display_text": text,
            "page_number": number,
            "source_start": source_start,
            "source_end": source_start + len(text),
        })

    records = [_record(details=(
        "Description: A landed aristocracy dominated political life while the "
        "Zollverein removed tariffs and promoted German economic integration."
    ))]
    _usable, source_blocks = phase31._candidate_blocks(
        graph, canonical, "TOPIC-0001"
    )
    concepts, _ = phase31._concept_payload(records, [0])
    concept = concepts[0]
    candidates = phase31._grounding_candidates(
        graph=graph,
        concept=concept,
        source_blocks=source_blocks,
    )
    identity = phase31._grounding_identity(
        graph=graph,
        topic=graph["topics"][0],
        concept=concept,
        candidates=candidates,
        source_blocks=source_blocks,
    )
    proposal = {
        concept["concept_id"]: {
            "concept_id": concept["concept_id"],
            "source_block_ids": ["BLK-0001", "BLK-0030"],
            "confidence": 0.999,
            "reason": "Both canonical passages are needed.",
        }
    }
    pending = phase31._grounding_pending_decision(
        identity=identity,
        graph=graph,
        topic=graph["topics"][0],
        concept=concept,
        candidates=candidates,
        source_blocks=source_blocks,
        issues=[
            "BLK-0001 supports the social clause and BLK-0030 supports the "
            "economic clause; review the complete set."
        ],
        rejected_ids=[concept["concept_id"]],
        proposal=proposal,
    )

    # The old source_blocks[:24] packet dropped BLK-0030 entirely. The exact
    # issue set now leads the evidence packet and all 32 bounded blocks remain.
    assert [row["label"] for row in pending["evidence"][:2]] == [
        "BLK-0001",
        "BLK-0030",
    ]
    assert len(pending["evidence"]) == 32
    late = next(
        row for row in pending["evidence"] if row["label"] == "BLK-0030"
    )
    exact_text = next(
        row["text"] for row in source_blocks if row["block_id"] == "BLK-0030"
    )
    assert late["text"] == exact_text
    assert late["page"].endswith(
        f"source_offsets=30000:{30_000 + len(exact_text)}"
    )
    assert late["evidence_id"] == (
        f"BLK-0030@30000:{30_000 + len(exact_text)}#"
        f"{phase3._sha256_text(exact_text)}"
    )
    # The public decision remains compatible with existing API/UI clients.
    schemas.PendingSemanticDecision.model_validate(pending)


def test_verified_multi_block_candidate_can_direct_one_safe_resume():
    graph, canonical = _source_graph()
    records = [_record(details=(
        "Description: A landed aristocracy dominated social and political "
        "life, while the Zollverein removed tariff barriers and supported "
        "economic integration among the German states."
    ))]
    _usable, source_blocks = phase31._candidate_blocks(
        graph, canonical, "TOPIC-0001"
    )
    concepts, _ = phase31._concept_payload(records, [0])
    candidates = phase31._grounding_candidates(
        graph=graph,
        concept=concepts[0],
        source_blocks=source_blocks,
    )
    evidence_set = next(
        row
        for row in candidates
        if row["source_block_ids"] == ["BLK-0001", "BLK-0002"]
    )
    assert evidence_set["action"] == "use_verified_evidence"
    assert evidence_set["source_kind"] == "verified_evidence_set"
    assert "text_sha256=" in evidence_set["coverage"]
    assert "source_offsets=100:192" in evidence_set["coverage"]
    assert "source_offsets=240:330" in evidence_set["coverage"]
    assert evidence_set["text_sha256"] == phase3._sha256_text(
        evidence_set["coverage"]
    )
    assert phase31.early_gate.candidate_binding_is_valid(evidence_set)

    reordered = copy.deepcopy(evidence_set)
    reordered["source_block_ids"] = ["BLK-0002", "BLK-0001"]
    assert not phase31.early_gate.candidate_binding_is_valid(reordered)

    provider_calls = 0
    critic_calls = 0

    def provider(payload: dict) -> dict:
        nonlocal provider_calls
        provider_calls += 1
        concept_id = payload["concepts"][0]["concept_id"]
        selected_ids = ["BLK-0001"]
        if provider_calls == 2:
            selected = payload["human_resolutions"][0]["selected_candidate"]
            assert selected["source_block_ids"] == ["BLK-0001", "BLK-0002"]
            assert phase31.early_gate.candidate_binding_is_valid(selected)
            selected_ids = list(selected["source_block_ids"])
        return {"concepts": [{
            "concept_id": concept_id,
            "source_block_ids": selected_ids,
            "confidence": 0.999,
            "reason": "Use the complete independently checked source set.",
        }]}

    def critic(payload: dict) -> dict:
        nonlocal critic_calls
        critic_calls += 1
        concept_id = payload["concepts"][0]["concept_id"]
        if critic_calls == 1:
            return {
                "verdict": "rejected",
                "confidence": 0.999,
                "accepted_concept_ids": [],
                "rejected_concept_ids": [concept_id],
                "issues": [
                    "BLK-0001 supports only the first clause; BLK-0002 is "
                    "also required for the Zollverein clause."
                ],
            }
        assert payload["proposed_grounding"][0]["source_block_ids"] == [
            "BLK-0001",
            "BLK-0002",
        ]
        return _verified_review(payload)

    with pytest.raises(semantic_recovery.HumanDecisionRequired) as paused:
        phase31.ground_concepts(
            records,
            graph=graph,
            canonical=canonical,
            provider=provider,
            critic=critic,
        )
    pending = paused.value.pending_decision
    selected_set = next(
        row
        for row in pending["candidates"]
        if row["source_block_ids"] == ["BLK-0001", "BLK-0002"]
    )
    resolution = {
        **copy.deepcopy(pending),
        "choice": "select_candidate",
        "target_id": selected_set["target_id"],
        "instruction": "",
    }
    with phase33.human_resolution_context([resolution]):
        grounded = phase31.ground_concepts(
            records,
            graph=graph,
            canonical=canonical,
            provider=provider,
            critic=critic,
        )

    assert provider_calls == 2
    assert critic_calls == 2
    assert grounded[0]["_source_block_ids"] == ["BLK-0001", "BLK-0002"]


def test_wrong_block_prose_cannot_rebind_a_sealed_evidence_candidate():
    graph, canonical = _source_graph()
    _usable, source_blocks = phase31._candidate_blocks(
        graph, canonical, "TOPIC-0001"
    )
    concepts, _ = phase31._concept_payload([_record()], [0])
    candidates = phase31._grounding_candidates(
        graph=graph,
        concept=concepts[0],
        source_blocks=source_blocks,
    )
    zollverein = next(
        row for row in candidates
        if row["source_block_ids"] == ["BLK-0002"]
    )
    assert "Zollverein" in zollverein["coverage"]
    assert phase31.early_gate.candidate_binding_is_valid(zollverein)

    tampered = copy.deepcopy(zollverein)
    tampered["coverage"] = next(
        row["text"] for row in source_blocks
        if row["block_id"] == "BLK-0001"
    )
    assert not phase31.early_gate.candidate_binding_is_valid(tampered)

    identity = phase31._grounding_identity(
        graph=graph,
        topic=graph["topics"][0],
        concept=concepts[0],
        candidates=candidates,
        source_blocks=source_blocks,
    )
    pending = phase31._grounding_pending_decision(
        identity=identity,
        graph=graph,
        topic=graph["topics"][0],
        concept=concepts[0],
        candidates=candidates,
        source_blocks=source_blocks,
        issues=[
            "BLK-0001 is not sufficient; perhaps BLK-0002 supports the claim."
        ],
        rejected_ids=[concepts[0]["concept_id"]],
    )
    assert not any(
        row["choice"] == "accept_recommended" for row in pending["options"]
    )


def test_stale_grounding_resolution_requires_exact_context():
    graph, canonical = _source_graph()
    records = [_record()]
    source_blocks, payload_blocks = phase31._candidate_blocks(
        graph, canonical, "TOPIC-0001"
    )
    concepts, _ = phase31._concept_payload(records, [0])
    candidates = phase31._grounding_candidates(
        graph=graph,
        concept=concepts[0],
        source_blocks=payload_blocks,
    )
    identity = phase31._grounding_identity(
        graph=graph,
        topic=graph["topics"][0],
        concept=concepts[0],
        candidates=candidates,
        source_blocks=payload_blocks,
    )
    stale = {
        **identity,
        "context_hash": "0" * 64,
        "kind": "phase31_source_grounding_semantic_conflict",
        "phase": "3.1",
        "item": {"unit_id": concepts[0]["concept_id"]},
        "choice": "select_candidate",
        "target_id": candidates[0]["target_id"],
    }

    with phase33.human_resolution_context([stale]):
        assert phase31._grounding_resolution_for(
            identity=identity,
            concept=concepts[0],
            candidates=candidates,
        ) is None
    exact = {
        **stale,
        **identity,
    }
    with phase33.human_resolution_context([exact]):
        assert phase31._grounding_resolution_for(
            identity=identity,
            concept=concepts[0],
            candidates=candidates,
        ) is not None
        with phase31.early_gate.suppress_resolution_ids(
            {identity["decision_id"]}
        ):
            assert phase31._grounding_resolution_for(
                identity=identity,
                concept=concepts[0],
                candidates=candidates,
            ) is None
    assert source_blocks


@pytest.mark.parametrize(
    "resolver_version",
    [
        "semantic-resolution-agent-2",
        "semantic-resolution-agent-3",
        "semantic-resolution-agent-4",
        "semantic-resolution-agent-5",
    ],
)
def test_complete_workspace_agent_maps_legacy_blk_to_current_binding(
    resolver_version,
):
    graph, canonical = _source_graph()
    records = [_record()]
    _source_blocks, payload_blocks = phase31._candidate_blocks(
        graph, canonical, "TOPIC-0001"
    )
    concepts, _ = phase31._concept_payload(records, [0])
    candidates = phase31._grounding_candidates(
        graph=graph,
        concept=concepts[0],
        source_blocks=payload_blocks,
    )
    identity = phase31._grounding_identity(
        graph=graph,
        topic=graph["topics"][0],
        concept=concepts[0],
        candidates=candidates,
        source_blocks=payload_blocks,
    )
    current = next(
        row for row in candidates
        if row.get("source_block_ids") == ["BLK-0001"]
    )
    legacy_target = "3.1:evidence:" + ("1" * 64)
    legacy_candidate = {
        "target_id": legacy_target,
        "concept_id": "",
        "title": "Use verified evidence BLK-0001",
        "topic": current["topic"],
        "coverage": current["coverage"],
        "gap": "",
    }
    upgraded = {
        "decision_id": "phase31-ground-" + ("0" * 24),
        "context_hash": "0" * 64,
        "kind": "phase31_source_grounding_semantic_conflict",
        "phase": "3.1",
        "item": {"unit_id": concepts[0]["concept_id"]},
        "candidates": [legacy_candidate],
        "choice": "select_candidate",
        "target_id": legacy_target,
        "resolved_by": "agent",
        "agent_review": {
            "status": "resolved",
            "resolver_version": resolver_version,
            "capability_key": "c" * 64,
        },
    }

    with phase33.human_resolution_context([upgraded]):
        mapped = phase31._grounding_resolution_for(
            identity=identity,
            concept=concepts[0],
            candidates=candidates,
        )
    assert mapped is not None
    assert mapped["target_id"] == current["target_id"]
    assert mapped["selected_candidate"]["binding_hash"] == current[
        "binding_hash"
    ]

    tampered = copy.deepcopy(upgraded)
    tampered["candidates"][0]["coverage"] = (
        "This text belongs to a different BLK."
    )
    with phase33.human_resolution_context([tampered]):
        assert phase31._grounding_resolution_for(
            identity=identity,
            concept=concepts[0],
            candidates=candidates,
        ) is None


def test_unsupported_claim_can_be_sent_back_to_topology_without_grounding_retry():
    graph, canonical = _source_graph()
    records = [_record(details=(
        "Description: The landed aristocracy controlled society and invented "
        "the Zollverein as a peasant parliament."
    ))]
    provider_calls = 0

    def provider(payload: dict) -> dict:
        nonlocal provider_calls
        provider_calls += 1
        return {"concepts": [{
            "concept_id": payload["concepts"][0]["concept_id"],
            "source_block_ids": ["BLK-0001"],
            "confidence": 0.999,
            "reason": "Candidate evidence.",
        }]}

    def critic(payload: dict) -> dict:
        concept_id = payload["concepts"][0]["concept_id"]
        return {
            "verdict": "rejected",
            "confidence": 0.999,
            "accepted_concept_ids": [],
            "rejected_concept_ids": [concept_id],
            "issues": [
                f"{concept_id}: no source block supports the invented clause."
            ],
        }

    with pytest.raises(semantic_recovery.HumanDecisionRequired) as paused:
        phase31.ground_concepts(
            records,
            graph=graph,
            canonical=canonical,
            provider=provider,
            critic=critic,
        )
    pending = paused.value.pending_decision
    refine = next(
        row for row in pending["candidates"] if row["title"].startswith("Refine")
    )
    resolution = {
        **copy.deepcopy(pending),
        "choice": "select_candidate",
        "target_id": refine["target_id"],
        "instruction": "",
    }

    with phase33.human_resolution_context([resolution]):
        with pytest.raises(phase31.early_gate.TopologyRepairRequired) as handoff:
            phase31.ground_concepts(
                records,
                graph=graph,
                canonical=canonical,
                provider=provider,
                critic=critic,
            )

    assert provider_calls == 1
    assert "HUMAN DIRECTION requires topology action refine" in str(handoff.value)
    assert "no source block supports" in str(handoff.value)


def test_partial_accepts_are_cached_and_resume_sends_only_rejected_id(tmp_path):
    graph, canonical = _source_graph()
    records = [
        _record(),
        _record(
            title="Economic Integration through the Zollverein",
            details=(
                "Description: The Zollverein abolished tariff barriers and "
                "supported economic integration among the German states."
            ),
        ),
    ]
    provider_calls: list[list[str]] = []
    critic_calls: list[list[str]] = []

    def provider(payload: dict) -> dict:
        ids = [row["concept_id"] for row in payload["concepts"]]
        provider_calls.append(ids)
        return {
            "concepts": [
                {
                    "concept_id": concept_id,
                    "source_block_ids": [
                        "BLK-0002"
                        if len(provider_calls) == 2
                        else "BLK-0001"
                    ],
                    "confidence": 0.999,
                    "reason": "Bounded source selection.",
                }
                for concept_id in ids
            ]
        }

    def critic(payload: dict) -> dict:
        ids = [row["concept_id"] for row in payload["concepts"]]
        critic_calls.append(ids)
        if len(critic_calls) == 1:
            return {
                "verdict": "rejected",
                "confidence": 0.999,
                "accepted_concept_ids": [ids[0]],
                "rejected_concept_ids": [ids[1]],
                "issues": [
                    f"{ids[1]} needs the missing verified evidence BLK-0002."
                ],
            }
        return {
            "verdict": "verified",
            "confidence": 0.999,
            "accepted_concept_ids": ids,
            "rejected_concept_ids": [],
            "issues": [],
        }

    session = {"artifact_dir": tmp_path, "canonical": canonical}
    with phase3.activate_session(session), phase3.activate(graph):
        with pytest.raises(semantic_recovery.HumanDecisionRequired) as paused:
            phase31.ground_concepts(
                copy.deepcopy(records),
                graph=graph,
                canonical=canonical,
                provider=provider,
                critic=critic,
            )
        pending = paused.value.pending_decision
        selected = next(
            row for row in pending["candidates"]
            if "BLK-0002" in row["title"]
        )
        resolution = {
            **copy.deepcopy(pending),
            "choice": "select_candidate",
            "target_id": selected["target_id"],
            "instruction": "",
        }
        with phase33.human_resolution_context([resolution]):
            grounded = phase31.ground_concepts(
                copy.deepcopy(records),
                graph=graph,
                canonical=canonical,
                provider=provider,
                critic=critic,
            )

    assert provider_calls == [
        ["CONCEPT-GROUND-0001", "CONCEPT-GROUND-0002"],
        ["CONCEPT-GROUND-0002"],
    ]
    assert critic_calls == provider_calls
    assert [row["_source_block_ids"] for row in grounded] == [
        ["BLK-0001"],
        ["BLK-0002"],
    ]


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
        with pytest.raises(
            semantic_recovery.HumanDecisionRequired,
            match="paused for a human semantic decision",
        ):
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


def test_final_topology_cache_rejects_active_semantic_block_drift(tmp_path):
    graph, canonical = _source_graph()
    records = [_record()]
    calls = 0

    def original_prepare(rows, **_kwargs):
        nonlocal calls
        calls += 1
        return copy.deepcopy(rows)

    kwargs = {
        "subject": "History",
        "mmd_text": "canonical semantic source",
        "meta": {},
        "source_sections": [],
        "source_topic_excerpts": [],
        "method_anchors": [],
    }
    session = {"artifact_dir": tmp_path, "canonical": canonical}
    with phase3.activate_session(session), phase3.activate(graph):
        phase31._prepare_topology_with_cache(
            original_prepare,
            copy.deepcopy(records),
            (),
            copy.deepcopy(kwargs),
        )

    drifted = copy.deepcopy(graph)
    drifted["blocks"][0]["kind"] = "figure"
    with phase3.activate_session(session), phase3.activate(drifted):
        phase31._prepare_topology_with_cache(
            original_prepare,
            copy.deepcopy(records),
            (),
            copy.deepcopy(kwargs),
        )

    assert calls == 2


def test_production_grounding_rejects_a_normal_row_without_placement_authority():
    with pytest.raises(
        grounding_certificate.GroundingCertificateError,
        match="placement contract row 0 is missing",
    ):
        phase31._verify_preserved_placement_contracts(
            [{"concept_title": "Normal source concept"}],
            require_all=True,
        )

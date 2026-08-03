"""Installed Phase 3.7 -> Phase 3.2 placement-authority regressions."""
from __future__ import annotations

import json

import pytest

from app.services import canonical_source_phase3 as phase3
from app.services import canonical_source_phase32_topology_adjudication_contract as phase32
from app.services import canonical_source_phase37_visual_topology_convergence_contract as phase37
from app.services import placement_policy
from app.services import semantic_recovery


def _source() -> tuple[dict, dict]:
    graph = {
        "source_contract_hash": "PLACEMENT-INSTALLED-CHAIN",
        "metadata": {"subject": "History"},
        "topics": [
            {"topic_id": "TOPIC-A", "order": 1, "title": "Earlier topic"},
            {"topic_id": "TOPIC-B", "order": 2, "title": "Later topic"},
        ],
        "blocks": [
            {
                "block_id": "BLK-A",
                "order": 1,
                "kind": "paragraph",
                "topic_id": "TOPIC-A",
                "subtopic_id": "",
            },
            {
                "block_id": "BLK-B",
                "order": 2,
                "kind": "paragraph",
                "topic_id": "TOPIC-B",
                "subtopic_id": "",
            },
        ],
    }
    canonical = {
        "blocks": [
            {
                "block_id": "BLK-A",
                "order": 1,
                "kind": "paragraph",
                "display_text": "The earlier topic introduces shared identity.",
            },
            {
                "block_id": "BLK-B",
                "order": 2,
                "kind": "paragraph",
                "display_text": (
                    "The later topic teaches how shared identity becomes a "
                    "political programme."
                ),
            },
        ]
    }
    return graph, canonical


def _record(*, protected: bool = False) -> dict:
    value = {
        "topic": "Earlier topic",
        "_semantic_topic_id": "TOPIC-A",
        "parent_concept": "Identity",
        "concept_title": "Shared identity as a political programme",
        "concept_details": (
            "Description: Shared identity becomes a political programme.\n"
            "Achieving Mastery: Explain the political programme."
        ),
        "keywords": "identity",
    }
    if protected:
        value["protected_source_items"] = ["QINV-0001"]
    return value


def _keep_in_a_owned_by_b(payload: dict, *, block_id: str = "BLK-B") -> dict:
    concept = payload["concepts"][0]
    return {
        "concepts": [{
            "concept_id": concept["concept_id"],
            # Phase 3.3's stable projection deliberately preserves this keep/A
            # proposal. The central placement seam must still derive move/B.
            "decision": "keep",
            "segments": [{
                "topic_id": "TOPIC-A",
                "concept_title": concept["concept_title"],
                "parent_concept": concept["parent_concept"],
                "description": concept["source_claim"],
                "achieving_mastery": concept["existing_mastery"],
                "keywords": ["identity"],
                "protected_source_items": list(
                    concept.get("protected_source_items") or []
                ),
                "topic_relationships": [{
                    "topic_id": "TOPIC-B",
                    "relationship_type": "CORE_TEACHING",
                    "necessity": True,
                    "evidence_block_ids": [block_id],
                    "reason": "The later block directly teaches this claim.",
                }],
                "confidence": 0.999,
                "reason": "Provider proposal; deterministic placement owns it.",
            }],
            "confidence": 0.999,
            "reason": "Provider proposal.",
        }]
    }


def _accepted_review(payload: dict) -> dict:
    split_review_ids = list(dict.fromkeys(
        str(attestation.get("split_review_id") or "")
        for decision in payload.get("proposed_decisions") or []
        for segment in decision.get("segments") or []
        for contract in [segment.get("_placement_contract") or {}]
        for attestation in [contract.get("split_attestation") or {}]
        if str(attestation.get("split_review_id") or "")
    ))
    return {
        "verdict": "verified",
        "confidence": 0.999,
        "accepted_concept_ids": [
            concept["concept_id"] for concept in payload["concepts"]
        ],
        "rejected_concept_ids": [],
        "issues": [],
        "relationship_reviews": [
            {
                "relationship_id": relation["relationship_id"],
                "verdict": "accepted",
                "evidence_supported": True,
                "necessity_supported": True,
                "direction_supported": True,
                "reason": "The exact block supports the relationship.",
            }
            for decision in payload.get("proposed_decisions") or []
            for segment in decision.get("segments") or []
            for relation in segment.get("topic_relationships") or []
        ],
        "split_reviews": [
            {
                "split_review_id": review_id,
                "verdict": "accepted",
                "complete_parent_claim_preserved": True,
                "protected_items_preserved": True,
                "irreducible_relationships_preserved": True,
                "reason": "Both children preserve the exact parent split.",
            }
            for review_id in split_review_ids
        ],
    }


def _grounding_provider(payload: dict) -> dict:
    assert payload["topic"]["topic_id"] == "TOPIC-B"
    return {
        "concepts": [{
            "concept_id": concept["concept_id"],
            "source_block_ids": ["BLK-B"],
            "confidence": 0.999,
            "reason": "The exact later block supports the claim.",
        } for concept in payload["concepts"]]
    }


def _grounding_critic(payload: dict) -> dict:
    return {
        "verdict": "verified",
        "confidence": 0.999,
        "accepted_concept_ids": [
            concept["concept_id"] for concept in payload["concepts"]
        ],
        "rejected_concept_ids": [],
        "issues": [],
    }


def _install_fake_phase37_transport(
    monkeypatch,
    *,
    provider,
    critic=_accepted_review,
) -> list[str]:
    calls: list[str] = []

    def fake_openai(**kwargs):
        payload = json.loads(kwargs["prompt"])
        if "independent Aegis Phase 3.7" in kwargs["system"]:
            calls.append("critic")
            return critic(payload)
        calls.append("provider")
        return provider(payload)

    monkeypatch.setattr(phase3, "semantic_api_enabled", lambda: True)
    monkeypatch.setattr(phase37, "_visual_evidence_pages", lambda _payload: ([], []))
    monkeypatch.setattr(phase37.phase22, "_openai_multimodal_json", fake_openai)
    return calls


def test_installed_phase37_keep_in_a_with_certified_owner_b_becomes_move(
    monkeypatch,
):
    graph, canonical = _source()
    calls = _install_fake_phase37_transport(
        monkeypatch, provider=_keep_in_a_owned_by_b
    )

    result = phase32.adjudicate_topology(
        [_record()],
        graph=graph,
        canonical=canonical,
        grounding_provider=_grounding_provider,
        grounding_critic=_grounding_critic,
    )

    assert calls == ["provider", "critic"]
    assert result[0]["_phase32_topology_decision"] == "move"
    assert result[0]["_semantic_topic_id"] == "TOPIC-B"
    contract = result[0]["_placement_contract"]
    assert contract["certified"] is True
    assert contract["source_location_topic_id"] == "TOPIC-A"
    assert contract["owner_topic_id"] == "TOPIC-B"
    assert contract["placement_certificate_sha256"] == (
        placement_policy.placement_contract_sha256(contract)
    )


def test_installed_phase37_missing_relationship_review_fails_closed(monkeypatch):
    graph, canonical = _source()

    def incomplete_review(payload: dict) -> dict:
        value = _accepted_review(payload)
        value.pop("relationship_reviews")
        return value

    calls = _install_fake_phase37_transport(
        monkeypatch,
        provider=_keep_in_a_owned_by_b,
        critic=incomplete_review,
    )

    with pytest.raises(semantic_recovery.HumanDecisionRequired, match=""):
        phase32.adjudicate_topology(
            [_record()],
            graph=graph,
            canonical=canonical,
            grounding_provider=lambda _payload: (_ for _ in ()).throw(
                AssertionError("uncertified placement must not be grounded")
            ),
            grounding_critic=_grounding_critic,
        )

    assert calls == ["provider", "critic"]


@pytest.mark.parametrize("block_id", ["BLK-NOT-REAL", "BLK-A"])
def test_installed_phase37_fabricated_or_wrong_topic_block_fails_before_critic(
    monkeypatch,
    block_id,
):
    graph, canonical = _source()
    calls = _install_fake_phase37_transport(
        monkeypatch,
        provider=lambda payload: _keep_in_a_owned_by_b(
            payload, block_id=block_id
        ),
    )

    with pytest.raises(
        (
            semantic_recovery.HumanDecisionRequired,
            semantic_recovery.ProviderResponseContractError,
        )
    ):
        phase32.adjudicate_topology(
            [_record()],
            graph=graph,
            canonical=canonical,
            grounding_provider=lambda _payload: (_ for _ in ()).throw(
                AssertionError("invalid relationship must not be grounded")
            ),
            grounding_critic=_grounding_critic,
        )

    assert "critic" not in calls


def test_installed_phase37_split_cannot_drop_protected_source_inventory(
    monkeypatch,
):
    graph, canonical = _source()

    def lossy_split(payload: dict) -> dict:
        concept = payload["concepts"][0]
        rows = []
        for topic_id, block_id, text in (
            ("TOPIC-A", "BLK-A", "Shared identity is introduced."),
            ("TOPIC-B", "BLK-B", "It becomes a political programme."),
        ):
            rows.append({
                "topic_id": topic_id,
                "concept_title": text,
                "parent_concept": concept["parent_concept"],
                "description": text,
                "achieving_mastery": f"Explain: {text}",
                "keywords": [],
                # QINV-0001 is deliberately lost by both children.
                "protected_source_items": [],
                "topic_relationships": [{
                    "topic_id": topic_id,
                    "relationship_type": "CORE_TEACHING",
                    "necessity": True,
                    "evidence_block_ids": [block_id],
                    "reason": "The exact block teaches this child.",
                }],
                "confidence": 0.999,
                "reason": "Split child.",
            })
        return {
            "concepts": [{
                "concept_id": concept["concept_id"],
                "decision": "split",
                "segments": rows,
                "confidence": 0.999,
                "reason": "Lossy split fixture.",
            }]
        }

    calls = _install_fake_phase37_transport(monkeypatch, provider=lossy_split)
    with pytest.raises(semantic_recovery.HumanDecisionRequired):
        phase32.adjudicate_topology(
            [_record(protected=True)],
            graph=graph,
            canonical=canonical,
            grounding_provider=lambda _payload: (_ for _ in ()).throw(
                AssertionError("lossy split must not be grounded")
            ),
            grounding_critic=_grounding_critic,
        )

    # Source-survival loss is a semantic rejection, so it receives no
    # mechanical provider-contract retry and never reaches the critic.
    assert calls == ["provider"]


def test_installed_split_partitions_qid_figure_image_and_katex_exactly_once(
    monkeypatch,
):
    graph, canonical = _source()
    image_url = "https://assets.example.test/identity.png"
    katex = "a+b"
    katex_item = "KATEX:" + phase3._sha256_text(katex)
    image_item = "IMAGE:" + image_url
    record = _record()
    record.update({
        "protected_source_items": ["QINV-0001", "FIG-0001"],
        "source_question_ids": ["QINV-0001"],
        "figure_ids": ["FIG-0001"],
        "image_urls": [image_url],
        "concept_details": (
            "Description: Shared identity becomes a political programme "
            f"using {image_url} and [katex]{katex}[/katex].\n"
            "Achieving Mastery: Explain the complete relationship."
        ),
    })

    def valid_split(payload: dict) -> dict:
        concept = payload["concepts"][0]
        return {"concepts": [{
            "concept_id": concept["concept_id"],
            "decision": "split",
            "segments": [
                {
                    "topic_id": "TOPIC-A",
                    "concept_title": "Shared identity and its source image",
                    "parent_concept": concept["parent_concept"],
                    "description": (
                        "Shared identity is introduced through source task "
                        f"QINV-0001 and {image_url}."
                    ),
                    "achieving_mastery": "Explain the source representation.",
                    "keywords": ["identity"],
                    "protected_source_items": [
                        "QINV-0001", image_url, image_item,
                    ],
                    "topic_relationships": [{
                        "topic_id": "TOPIC-A",
                        "relationship_type": "CORE_TEACHING",
                        "necessity": True,
                        "evidence_block_ids": ["BLK-A"],
                        "reason": "The earlier block teaches this child.",
                    }],
                    "confidence": 0.999,
                    "reason": "Earlier child.",
                },
                {
                    "topic_id": "TOPIC-B",
                    "concept_title": "Political programme and symbolic figure",
                    "parent_concept": concept["parent_concept"],
                    "description": (
                        "The political programme uses FIG-0001 and "
                        f"[katex]{katex}[/katex]."
                    ),
                    "achieving_mastery": "Explain the later programme.",
                    "keywords": ["programme"],
                    "protected_source_items": ["FIG-0001", katex_item],
                    "topic_relationships": [{
                        "topic_id": "TOPIC-B",
                        "relationship_type": "CORE_TEACHING",
                        "necessity": True,
                        "evidence_block_ids": ["BLK-B"],
                        "reason": "The later block teaches this child.",
                    }],
                    "confidence": 0.999,
                    "reason": "Later child.",
                },
            ],
            "confidence": 0.999,
            "reason": "Every protected identity has one child.",
        }]}

    calls = _install_fake_phase37_transport(
        monkeypatch,
        provider=valid_split,
    )

    def grounding_provider(payload: dict) -> dict:
        block_id = "BLK-A" if payload["topic"]["topic_id"] == "TOPIC-A" else "BLK-B"
        return {"concepts": [{
            "concept_id": concept["concept_id"],
            "source_block_ids": [block_id],
            "confidence": 0.999,
            "reason": "Exact child evidence.",
        } for concept in payload["concepts"]]}

    result = phase32.adjudicate_topology(
        [record],
        graph=graph,
        canonical=canonical,
        grounding_provider=grounding_provider,
        grounding_critic=_grounding_critic,
    )

    assert calls == ["provider", "critic"]
    assert len(result) == 2
    assert [row.get("source_question_ids") for row in result] == [
        ["QINV-0001"], None,
    ]
    assert [row.get("figure_ids") for row in result] == [
        None, ["FIG-0001"],
    ]
    assert [row.get("image_urls") for row in result] == [[image_url], None]
    assert [
        set(row["_placement_contract"]["protected_source_items"])
        for row in result
    ] == [
        {"QINV-0001", image_url, image_item},
        {"FIG-0001", katex_item},
    ]
    split_attestations = [
        row["_placement_contract"]["split_attestation"] for row in result
    ]
    assert split_attestations[0] == split_attestations[1]
    assert split_attestations[0]["critic_verdict"] == "accepted"


def test_phase32_cache_row_without_current_placement_contract_is_ignored(
    tmp_path,
):
    records = [_record()]
    phase32._write_json(
        tmp_path / phase32._CACHE_FILENAME,
        {
            "version": phase32._TOPOLOGY_VERSION,
            "cache_key": "cache-key",
            "records_sha256": phase3._sha256_json(records),
            "records": records,
        },
    )

    with phase3.activate_session({"artifact_dir": tmp_path}):
        assert phase32._read_cached_records("cache-key") is None


def test_phase32_cache_key_binds_active_semantic_block_topology():
    graph, _canonical = _source()
    records = phase3.canonicalize_record_topics([_record()], graph)
    topics, _order = phase32._topic_evidence(graph, _canonical)
    concepts, _index = phase32._concept_rows(records)

    first = phase32._cache_key(
        records,
        graph=graph,
        topics=topics,
        concepts=concepts,
    )
    drifted = json.loads(json.dumps(graph))
    drifted["blocks"][0]["topic_id"] = "TOPIC-B"
    second = phase32._cache_key(
        records,
        graph=drifted,
        topics=topics,
        concepts=concepts,
    )

    assert first != second


def test_installed_phase33_decision_cache_replays_exhaustive_reviews(
    monkeypatch,
    tmp_path,
):
    graph, canonical = _source()
    calls = _install_fake_phase37_transport(
        monkeypatch, provider=_keep_in_a_owned_by_b
    )
    session = {"artifact_dir": tmp_path, "canonical": canonical}

    with phase3.activate_session(session), phase3.activate(graph):
        first = phase32.adjudicate_topology(
            [_record()],
            graph=graph,
            canonical=canonical,
            grounding_provider=_grounding_provider,
            grounding_critic=_grounding_critic,
        )
        # Force the installed chain through Phase 3.3's per-decision replay
        # instead of the later whole-topology cache.
        (tmp_path / phase32._CACHE_FILENAME).unlink()
        second = phase32.adjudicate_topology(
            [_record()],
            graph=graph,
            canonical=canonical,
            grounding_provider=_grounding_provider,
            grounding_critic=_grounding_critic,
        )

    assert calls == ["provider", "critic"]
    assert first[0]["_placement_contract"] == second[0]["_placement_contract"]
    assert second[0]["_placement_contract"]["certified"] is True

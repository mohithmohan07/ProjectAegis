"""Regression coverage for Phase 3.8 boundary-aware grounding turnover."""
from __future__ import annotations

import copy

from app.services import canonical_source_phase31_grounding_contract as phase31
from app.services import canonical_source_phase32_topology_adjudication_contract as phase32
from app.services import canonical_source_phase33_preflight_contract as phase33
from app.services import canonical_source_phase38_boundary_grounding_turnover_contract as phase38
from app.services import early_semantic_gate as early_gate


def _boundary_source() -> tuple[dict, dict]:
    graph = {
        "source_contract_hash": "source-hash",
        "metadata": {"subject": "History"},
        "topics": [
            {
                "topic_id": "TOPIC-0001",
                "order": 1,
                "title": "The French Revolution and the Idea of the Nation",
            },
            {
                "topic_id": "TOPIC-0002",
                "order": 2,
                "title": "The Making of Nationalism in Europe",
            },
            {
                "topic_id": "TOPIC-0003",
                "order": 3,
                "title": "The Age of Revolutions",
            },
        ],
        "subtopics": [],
        "blocks": [
            {
                "block_id": "BLK-00046",
                "order": 46,
                "source_start": 4600,
                "kind": "paragraph",
                "topic_id": "TOPIC-0001",
                "subtopic_id": "",
            },
            {
                "block_id": "BLK-00048",
                "order": 48,
                "source_start": 4800,
                "kind": "figure",
                "topic_id": "TOPIC-0001",
                "subtopic_id": "",
                "figure_id": "FIG-00005",
            },
            {
                "block_id": "BLK-00049",
                "order": 49,
                "source_start": 4900,
                "kind": "paragraph",
                "topic_id": "TOPIC-0001",
                "subtopic_id": "",
            },
            {
                "block_id": "BLK-00051",
                "order": 51,
                "source_start": 5100,
                "kind": "paragraph",
                "topic_id": "TOPIC-0002",
                "subtopic_id": "",
            },
            {
                "block_id": "BLK-00053",
                "order": 53,
                "source_start": 5300,
                "kind": "figure",
                "topic_id": "TOPIC-0002",
                "subtopic_id": "",
                "figure_id": "FIG-00006",
            },
            {
                "block_id": "BLK-00060",
                "order": 60,
                "source_start": 6000,
                "kind": "paragraph",
                "topic_id": "TOPIC-0003",
                "subtopic_id": "",
            },
        ],
    }
    canonical = {
        "blocks": [
            {
                "block_id": "BLK-00046",
                "kind": "paragraph",
                "display_text": (
                    "The Civil Code abolished privileges based on birth, "
                    "established equality before law and secured property rights."
                ),
            },
            {
                "block_id": "BLK-00048",
                "kind": "figure",
                "figure_id": "FIG-00005",
                "raw_text": (
                    "\\begin{figure}\\caption{French soldiers are shown as "
                    "oppressors while claiming to bring liberty.}\\end{figure}"
                ),
            },
            {
                "block_id": "BLK-00049",
                "kind": "paragraph",
                "display_text": (
                    "Uniform laws, measures and currency aided trade and movement."
                ),
            },
            {
                "block_id": "BLK-00051",
                "kind": "paragraph",
                "display_text": (
                    "Initial welcomes turned to hostility as taxation, censorship "
                    "and forced conscription outweighed administrative advantages."
                ),
            },
            {
                "block_id": "BLK-00053",
                "kind": "figure",
                "figure_id": "FIG-00006",
                "raw_text": (
                    "\\begin{figure}\\caption{Napoleon loses territories after "
                    "the battle of Leipzig.}\\end{figure}"
                ),
            },
            {
                "block_id": "BLK-00060",
                "kind": "paragraph",
                "display_text": "Revolutionary movements spread after 1830.",
            },
        ],
        "figures": [
            {
                "figure_id": "FIG-00005",
                "caption_raw": (
                    "French occupation is depicted as oppressive despite claims "
                    "of liberty and equality."
                ),
                "reference_ids": ["4"],
                "image_ids": [],
            },
            {
                "figure_id": "FIG-00006",
                "caption_raw": "Napoleon loses territories after Leipzig.",
                "reference_ids": ["5"],
                "image_ids": [],
            },
        ],
        "images": [],
    }
    return graph, canonical


def test_candidate_blocks_include_only_the_immediately_adjacent_topic_window():
    graph, canonical = _boundary_source()

    _usable, payload = phase31._candidate_blocks(
        graph,
        canonical,
        "TOPIC-0001",
    )

    by_id = {row["block_id"]: row for row in payload}
    assert set(by_id) == {
        "BLK-00046",
        "BLK-00048",
        "BLK-00049",
        "BLK-00051",
        "BLK-00053",
    }
    assert "BLK-00060" not in by_id
    assert by_id["BLK-00049"]["boundary_relation"] == "native_topic"
    assert by_id["BLK-00051"]["boundary_relation"] == "next_topic_boundary"
    assert by_id["BLK-00051"]["source_topic_id"] == "TOPIC-0002"
    assert (
        by_id["BLK-00051"]["source_topic_title"]
        == "The Making of Nationalism in Europe"
    )


def test_exact_grounding_can_use_a_boundary_continuation_without_the_wrong_figure():
    graph, canonical = _boundary_source()
    records = [
        {
            "topic": "The French Revolution and the Idea of the Nation",
            "_semantic_topic_id": "TOPIC-0001",
            "parent_concept": "Napoleonic rule",
            "concept_title": "Napoleonic administrative reform and coercive rule",
            "concept_details": (
                "Description: Napoleon combined legal and administrative reform "
                "with coercive occupation; initial welcomes turned to hostility "
                "under taxation, censorship and forced conscription.\n"
                "Achieving Mastery: The learner contrasts reform with coercion."
            ),
        }
    ]

    def provider(payload):
        source = {row["block_id"]: row for row in payload["source_blocks"]}
        assert source["BLK-00051"]["boundary_relation"] == "next_topic_boundary"
        assert source["BLK-00053"]["figure_id"] == "FIG-00006"
        return {
            "concepts": [
                {
                    "concept_id": "CONCEPT-GROUND-0001",
                    "source_block_ids": [
                        "BLK-00046",
                        "BLK-00048",
                        "BLK-00049",
                        "BLK-00051",
                    ],
                    "confidence": 0.99,
                    "reason": (
                        "The adjacent paragraph visibly continues the Napoleonic "
                        "rule discussion; FIG-00006 is not selected."
                    ),
                }
            ]
        }

    def critic(payload):
        proposal = payload["proposed_grounding"][0]
        assert "BLK-00051" in proposal["source_block_ids"]
        assert "BLK-00053" not in proposal["source_block_ids"]
        return {
            "verdict": "verified",
            "confidence": 0.99,
            "accepted_concept_ids": ["CONCEPT-GROUND-0001"],
            "rejected_concept_ids": [],
            "issues": [],
        }

    grounded = phase31.ground_concepts(
        records,
        graph=graph,
        canonical=canonical,
        provider=provider,
        critic=critic,
    )

    assert grounded[0]["_source_block_ids"] == [
        "BLK-00046",
        "BLK-00048",
        "BLK-00049",
        "BLK-00051",
    ]
    assert grounded[0]["_source_grounding_version"] == (
        "phase3.8-boundary-aware-source-grounding-2"
    )
    assert grounded[0]["_source_grounding_contract"] == (
        "api-verified-boundary-aware-source-block-ids"
    )
    assert grounded[0]["_source_grounding_boundary_blocks"] == [
        {
            "block_id": "BLK-00051",
            "source_topic_id": "TOPIC-0002",
            "target_topic_id": "TOPIC-0001",
        }
    ]


def test_targeted_convergence_retries_the_failed_origin_beyond_two_passes():
    records = [
        {
            "topic": "Topic A",
            "_semantic_topic_id": "TOPIC-0001",
            "parent_concept": "Parent",
            "concept_title": "Concept A",
            "concept_details": "Description: Claim A",
        },
        {
            "topic": "Topic A",
            "_semantic_topic_id": "TOPIC-0001",
            "parent_concept": "Parent",
            "concept_title": "Concept B",
            "concept_details": "Description: Claim B",
        },
    ]
    repaired = copy.deepcopy(records)
    repaired[0]["_phase32_origin_concept_id"] = "TOPOLOGY-CONCEPT-0001"
    repaired[1]["_phase32_origin_concept_id"] = "TOPOLOGY-CONCEPT-0002"
    calls = 0
    seen_feedback: list[dict[str, str]] = []

    def original(_records, *_args, **_kwargs):
        nonlocal calls
        calls += 1
        feedback = copy.deepcopy(
            phase33._EXTERNAL_GROUNDING_FEEDBACK.get() or {}
        )
        seen_feedback.append(feedback)
        if calls <= 3:
            phase38._LAST_REPAIRED_TOPOLOGY.set(copy.deepcopy(repaired))
            raise ValueError(
                "Phase 3.2 topology decisions passed independent topic review, "
                "but the repaired rows still failed exact source-block grounding "
                "before freeze: Phase 3 concept grounding failed independent "
                "verification for topic 'Topic A': CONCEPT-GROUND-0002 is not "
                "fully supported."
            )
        return copy.deepcopy(repaired)

    result = phase38._phase32_adjudicate_with_targeted_convergence(
        original,
        records,
    )

    assert result == repaired
    assert calls == 4
    assert seen_feedback[0] == {}
    assert set(seen_feedback[1]) == {"TOPOLOGY-CONCEPT-0002"}
    assert set(seen_feedback[2]) == {"TOPOLOGY-CONCEPT-0002"}
    assert "Returning the same effective" in seen_feedback[3][
        "TOPOLOGY-CONCEPT-0002"
    ]


def test_human_topology_handoff_is_consumed_once_before_directed_pass():
    records = [{
        "topic": "Topic A",
        "_semantic_topic_id": "TOPIC-0001",
        "parent_concept": "Parent",
        "concept_title": "Concept A",
        "concept_details": "Description: Unsupported compound claim",
    }]
    adjudicated = copy.deepcopy(records)
    adjudicated[0]["_phase32_origin_concept_id"] = (
        "TOPOLOGY-CONCEPT-0001"
    )
    decision_id = "phase31-ground-1234567890abcdef12345678"
    calls = 0

    def original(_records, *_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise early_gate.TopologyRepairRequired(
                "failed exact source-block grounding before freeze: "
                "CONCEPT-GROUND-0001 HUMAN DIRECTION requires topology action refine",
                decision_id=decision_id,
            )
        assert decision_id in early_gate._SUPPRESSED_RESOLUTION_IDS.get()
        assert phase33._EXTERNAL_GROUNDING_FEEDBACK.get()
        return copy.deepcopy(adjudicated)

    assert phase38._phase32_adjudicate_with_targeted_convergence(
        original,
        records,
    ) == adjudicated
    assert calls == 2


def test_old_final_topology_cache_is_rejected_but_current_grounding_is_reused(
    monkeypatch,
):
    old = [
        {
            "topic": "Topic A",
            "concept_title": "Concept A",
            "_source_grounding_version": "phase3.1-source-claim-grounding-1",
        }
    ]
    current = [
        {
            "topic": "Topic A",
            "concept_title": "Concept A",
            "_source_grounding_version": (
                "phase3.8-boundary-aware-source-grounding-2"
            ),
        }
    ]

    monkeypatch.setattr(
        phase32,
        "_PHASE38_ORIGINAL_READ_CACHED_RECORDS",
        lambda _key: copy.deepcopy(old),
    )
    assert phase38._read_cached_records("cache-key") is None

    monkeypatch.setattr(
        phase32,
        "_PHASE38_ORIGINAL_READ_CACHED_RECORDS",
        lambda _key: copy.deepcopy(current),
    )
    assert phase38._read_cached_records("cache-key") == current

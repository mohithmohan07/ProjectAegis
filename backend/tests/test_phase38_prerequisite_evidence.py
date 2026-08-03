"""Prerequisite evidence and advanced placement for cumulative chapters.

Phase 3.1 grounded a concept only against its own topic's blocks plus the
immediately adjacent ones. Textbook reasoning is cumulative, so a later
section legitimately applies a definition established several sections
earlier — non-adjacent, therefore outside the window. Both gates were then
correct and irreconcilable: grounding said "unsupported", topology said
"correctly placed", and the run ping-ponged.

Modelled on Arithmetic Progressions: a concept under *5.4 Sum of First n
Terms* that applies the real-world AP situations introduced in *5.2*, with
*5.3 nth Term* sitting between them so the adjacent window cannot reach.

The corrected contract offers a bounded, relevance-ranked window of earlier
"prerequisite" blocks, while keeping the guard that a concept's principal
claim must still be native — so the window cannot excuse a genuinely
misplaced concept.
"""
from __future__ import annotations

import json

import pytest

from app.services import (
    canonical_source_phase38_boundary_grounding_turnover_contract as phase38,
)


def _block(block_id: str, topic_id: str, order: int, text: str) -> dict:
    return {
        "block_id": block_id,
        "topic_id": topic_id,
        "kind": "paragraph",
        "order": order,
        "source_start": order * 100,
        "text": text,
    }


def _ap_graph() -> dict:
    """5.2 definitions, 5.3 nth term, 5.4 sums — 5.2 is not adjacent to 5.4."""

    return {
        "topics": [
            {"topic_id": "TOPIC-52", "title": "Arithmetic Progressions"},
            {"topic_id": "TOPIC-53", "title": "nth Term of an AP"},
            {"topic_id": "TOPIC-54", "title": "Sum of First n Terms of an AP"},
        ],
        "blocks": [
            _block(
                "BLK-0001", "TOPIC-52", 1,
                "A taxi fare situation forms an arithmetic progression where "
                "the common difference stays constant between consecutive "
                "terms.",
            ),
            _block(
                "BLK-0002", "TOPIC-52", 2,
                "The ladder rungs decrease uniformly, so the lengths form an "
                "arithmetic progression with a fixed common difference.",
            ),
            _block(
                "BLK-0003", "TOPIC-53", 3,
                "The nth term of an arithmetic progression is a plus n minus "
                "one times the common difference.",
            ),
            _block(
                "BLK-0004", "TOPIC-54", 4,
                "The sum of the first n terms of an arithmetic progression "
                "pairs the first and last terms.",
            ),
            _block(
                "BLK-0005", "TOPIC-54", 5,
                "Applying the sum formula to a progression requires the first "
                "term and the common difference.",
            ),
        ],
    }


def _canonical(graph: dict) -> dict:
    return {
        "blocks": [
            {"block_id": row["block_id"], "text": row["text"], "page_number": 1}
            for row in graph["blocks"]
        ]
    }


@pytest.fixture()
def wired(monkeypatch):
    """Route the wrapper's native lookup to the target topic's own blocks."""

    graph = _ap_graph()

    def native(_graph, _canonical, topic_id):
        rows = [
            row for row in graph["blocks"]
            if row["topic_id"] == topic_id
        ]
        payload = [
            {"block_id": row["block_id"], "text": row["text"]}
            for row in rows
        ]
        return rows, payload

    monkeypatch.setattr(
        phase38.phase31, "_PHASE38_ORIGINAL_CANDIDATE_BLOCKS", native,
        raising=False,
    )
    monkeypatch.setattr(
        phase38.phase37, "_evidence_text",
        lambda block, _source, _canonical: str(block.get("text") or ""),
    )
    monkeypatch.setattr(
        phase38.phase37, "_bounded_visual_evidence",
        lambda text, limit=3000: str(text)[:limit],
    )
    return graph


def _payload_for(graph, topic_id):
    _usable, payload = phase38._candidate_blocks(
        graph, _canonical(graph), topic_id)
    return {str(row["block_id"]): row for row in payload}


# --------------------------------------------------------------------------- #
# The window reaches a non-adjacent prerequisite topic
# --------------------------------------------------------------------------- #

def test_sum_topic_sees_non_adjacent_definition_blocks(wired):
    rows = _payload_for(wired, "TOPIC-54")

    # Native 5.4 blocks are present and still labelled native.
    assert rows["BLK-0004"]["boundary_relation"] == "native_topic"
    assert rows["BLK-0005"]["boundary_relation"] == "native_topic"
    # 5.3 is immediately adjacent, so it arrives through the existing window.
    assert rows["BLK-0003"]["boundary_relation"] == "previous_topic_boundary"
    # 5.2 is two sections back: previously unreachable, now offered as
    # explicitly labelled prerequisite context.
    assert rows["BLK-0001"]["boundary_relation"] == "prerequisite_topic_evidence"
    assert rows["BLK-0002"]["boundary_relation"] == "prerequisite_topic_evidence"
    assert rows["BLK-0001"]["source_topic_title"] == "Arithmetic Progressions"


def test_prerequisite_window_is_relevance_ranked_not_everything(wired):
    graph = wired
    graph["blocks"].append(
        _block(
            "BLK-0009", "TOPIC-52", 0,
            "Chapter opening photograph of a spiral staircase in a museum.",
        )
    )
    rows = _payload_for(graph, "TOPIC-54")
    # An unrelated earlier block shares no meaningful vocabulary with the sum
    # topic, so it is not offered as prerequisite evidence.
    assert "BLK-0009" not in rows


def test_prerequisite_window_is_bounded(wired, monkeypatch):
    monkeypatch.setenv("AEGIS_PHASE38_PREREQUISITE_BLOCKS", "1")
    rows = _payload_for(wired, "TOPIC-54")
    prerequisite = [
        block_id for block_id, row in rows.items()
        if row["boundary_relation"] == "prerequisite_topic_evidence"
    ]
    assert len(prerequisite) == 1


def test_prerequisite_window_can_be_disabled(wired, monkeypatch):
    monkeypatch.setenv("AEGIS_PHASE38_PREREQUISITE_BLOCKS", "0")
    rows = _payload_for(wired, "TOPIC-54")
    assert not [
        row for row in rows.values()
        if row["boundary_relation"] == "prerequisite_topic_evidence"
    ]


def test_earliest_topic_gets_no_prerequisite_evidence(wired):
    rows = _payload_for(wired, "TOPIC-52")
    assert not [
        row for row in rows.values()
        if row["boundary_relation"] == "prerequisite_topic_evidence"
    ]


# --------------------------------------------------------------------------- #
# The contract keeps the misplacement detector intact
# --------------------------------------------------------------------------- #

def test_contract_requires_the_principal_claim_to_stay_native():
    contract = phase38._augment_grounding_payload(
        {"concepts": [], "source_blocks": []},
        page_numbers=[],
    )["boundary_grounding_contract"]

    assert contract["allowed_context_relations"] == [
        "prerequisite_topic_evidence"
    ]
    prerequisite_rule = contract["prerequisite_rule"]
    assert "PRINCIPAL claim" in prerequisite_rule
    assert "native_topic blocks" in prerequisite_rule
    # The escape hatch must stay closed: needing prerequisite blocks for the
    # principal claim is still a misplacement.
    assert "misplaced" in prerequisite_rule


def test_contract_prefers_split_so_the_earlier_topic_keeps_a_concept():
    contract = phase38._augment_grounding_payload(
        {"concepts": [], "source_blocks": []},
        page_numbers=[],
    )["boundary_grounding_contract"]

    route = contract["repair_route"]
    assert "SPLIT it and keep BOTH parts" in route
    # Neither topic may be left without its concept: the earlier topic keeps
    # the foundational idea, the later topic gains how it behaves there.
    assert "earlier topic keeps a concept" in route
    assert "later topic gains a concept" in route
    assert "never leave either topic without its concept" in route
    # Advanced placement is legitimate, not an error to be repaired away.
    advanced = contract["advanced_placement_rule"]
    assert "advanced material" in advanced
    assert "belongs to the LATER topic" in advanced


def test_both_provider_prompts_carry_the_prerequisite_rules(monkeypatch):
    captured: list[dict] = []

    def fake_call(**kwargs):
        captured.append(kwargs)
        return {}

    monkeypatch.setattr(phase38.phase22, "_openai_multimodal_json", fake_call)
    monkeypatch.setattr(
        phase38.phase37, "_visual_evidence_pages", lambda _payload: ([], []))
    monkeypatch.setattr(
        phase38.phase31, "_grounding_schema", lambda *_a, **_k: {"x": 1})
    monkeypatch.setattr(
        phase38.phase31, "_critic_schema", lambda *_a, **_k: {"x": 1})

    payload = {"concepts": [], "source_blocks": []}
    phase38._ground_via_openai(payload)
    phase38._critic_via_openai(payload)

    mapper, critic = captured[0]["system"], captured[1]["system"]
    for system in (mapper, critic):
        assert "prerequisite_topic_evidence" in system
        assert "principal claim" in system
    assert "advanced material" in mapper
    assert "advanced material" in critic
    # The critic still owns the rejection rule for a misplaced principal claim.
    assert "principal claim itself rests on prerequisite blocks" in critic
    # The contract travels with the prompt so the rules are auditable.
    sent = json.loads(captured[0]["prompt"])
    assert "prerequisite_rule" in sent["boundary_grounding_contract"]


# --------------------------------------------------------------------------- #
# Advanced placement is universal, not chapter-specific
# --------------------------------------------------------------------------- #

def test_split_keeps_a_concept_in_both_topics():
    """The d / Sn case: 5.2 keeps "d", 5.4 gains "how d affects Sn".

    Promoting the advanced behaviour must not delete the foundational
    concept, and keeping the foundational concept must not leave the
    advanced behaviour untaught.
    """

    route = phase38._augment_grounding_payload(
        {"concepts": [], "source_blocks": []},
        page_numbers=[],
    )["boundary_grounding_contract"]["repair_route"]

    assert "earlier topic keeps a concept" in route
    assert "later topic gains a concept" in route
    assert "do not delete the foundational concept" in route
    assert "do not leave the advanced behaviour untaught" in route


def test_advanced_placement_rule_is_subject_agnostic():
    from app.services import generation

    # Both the prompt that authors a Type's topic and the one that reviews
    # that placement carry the same universal rule.
    for key in (
        "concepts.type_mining.system",
        "concepts.type_alignment_review.system",
    ):
        rule = generation.prompts.get_text(key)
        assert "LATEST of those topics" in rule, key
        # Stated for every subject, not only numerically ordered sections.
        assert "every subject and chapter" in rule, key
        assert "prerequisite, not the owner" in rule, key


@pytest.mark.parametrize(
    ("qids", "expected"),
    [
        # The later topic owns an unattributed qid regardless of how many
        # questions each topic contributes.
        (["QINV-0001", "QINV-0002", "QINV-0003"], "Later Topic"),
        (["QINV-0003", "QINV-0001", "QINV-0002"], "Later Topic"),
    ],
)
def test_unattributed_qid_follows_the_latest_topic_not_the_largest(
    qids, expected,
):
    from app.services import generation

    inventory = {"items": [
        {"qid": "QINV-0001", "topic_hint": "Earlier Topic",
         "raw_task": "Foundational task."},
        {"qid": "QINV-0002", "topic_hint": "Earlier Topic",
         "raw_task": "Another foundational task."},
        {"qid": "QINV-0003", "topic_hint": "Later Topic",
         "raw_task": "Advanced task."},
        {"qid": "QINV-0004", "topic_hint": "",
         "raw_task": "Combined task using both methods."},
    ]}
    types = [{
        "type_id": "TYPE-0001",
        "type_title": "Applying the rules together",
        "source_question_ids": [*qids, "QINV-0004"],
        "case_prompts": [{
            "case_title": "Use the relevant rule",
            "examples": [
                {"source_question_id": qid, "example_prompt": "task"}
                for qid in [*qids, "QINV-0004"]
            ],
        }],
    }]

    out = generation._split_mined_types_by_source_topic(types, inventory)
    owner = {
        item["topic_match_hint"]: item["source_question_ids"]
        for item in out
    }
    # The earlier topic supplies two questions and the later only one, so a
    # majority rule would have filed the combined task under the earlier one.
    assert "QINV-0004" in owner[expected]
    assert "QINV-0004" not in owner["Earlier Topic"]

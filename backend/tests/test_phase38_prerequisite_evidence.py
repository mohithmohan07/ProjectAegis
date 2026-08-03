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

from contextlib import contextmanager
import json

import pytest

from app.services import (
    canonical_source_phase38_boundary_grounding_turnover_contract as phase38,
    grounding_certificate,
    placement_policy,
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


def _relationship(
    *,
    claim_id: str,
    topic_id: str,
    relationship_type: str,
    evidence_block_ids: list[str],
) -> dict:
    kind = placement_policy.RelationshipType(relationship_type)
    relation = placement_policy.TopicRelationship(
        claim_id=claim_id,
        topic_id=topic_id,
        relationship_type=kind,
        necessity=True,
        evidence_block_ids=tuple(evidence_block_ids),
    )
    return {
        "relationship_id": relation.relationship_id,
        "claim_id": claim_id,
        "topic_id": topic_id,
        "relationship_type": relationship_type,
        "necessity": True,
        "evidence_block_ids": evidence_block_ids,
        "provider_reason": "Exact relationship evidence.",
        "critic_verdict": "accepted",
    }


def _certified_row(
    *,
    prerequisite_topic_id: str = "TOPIC-52",
    prerequisite_block_id: str = "BLK-0002",
) -> dict:
    claim_id = "TOPOLOGY-CONCEPT-0001#1"
    claim = "Apply the sum formula using the established AP definition."
    relationships = [
        _relationship(
            claim_id=claim_id,
            topic_id="TOPIC-54",
            relationship_type="CORE_TEACHING",
            evidence_block_ids=["BLK-0004", "BLK-0005"],
        )
    ]
    prerequisite_topics: list[str] = []
    required_topics = ["TOPIC-54"]
    if prerequisite_topic_id:
        prerequisite_topics.append(prerequisite_topic_id)
        required_topics.append(prerequisite_topic_id)
        relationships.append(_relationship(
            claim_id=claim_id,
            topic_id=prerequisite_topic_id,
            relationship_type="REQUIRED_PREREQUISITE",
            evidence_block_ids=[prerequisite_block_id],
        ))
    contract = {
        "certified": True,
        "policy_version": placement_policy.POLICY_VERSION,
        "teaching_order_sha256": "a" * 64,
        "claim_id": claim_id,
        "normalized_claim": claim,
        "origin_claim_sha256": placement_policy.claim_text_sha256(claim),
        "source_location_topic_id": "TOPIC-54",
        "owner_topic_id": "TOPIC-54",
        "required_topic_ids": required_topics,
        "prerequisite_topic_ids": prerequisite_topics,
        "reference_edges": [],
        "illustration_topic_ids": [],
        "topic_relationships": relationships,
        "origin_claim_ids": ["TOPOLOGY-CONCEPT-0001"],
        "split_group_id": "",
        "protected_source_items": [],
    }
    contract["placement_certificate_sha256"] = (
        placement_policy.placement_contract_sha256(contract)
    )
    return {
        "topic": "Sum of First n Terms of an AP",
        "parent_concept": "Arithmetic Progressions",
        "concept_title": "Applying the AP Sum Formula",
        "concept_details": f"Description: {claim}",
        "_semantic_topic_id": "TOPIC-54",
        grounding_certificate.PLACEMENT_CONTRACT_FIELD: contract,
    }


def _certified_later_method_row() -> dict:
    """An irreducible claim with earlier teaching and a later required method."""

    row = _certified_row(
        prerequisite_topic_id="",
        prerequisite_block_id="",
    )
    contract = row[grounding_certificate.PLACEMENT_CONTRACT_FIELD]
    claim_id = contract["claim_id"]
    contract["source_location_topic_id"] = "TOPIC-52"
    contract["required_topic_ids"] = ["TOPIC-52", "TOPIC-54"]
    contract["prerequisite_topic_ids"] = []
    contract["topic_relationships"] = [
        _relationship(
            claim_id=claim_id,
            topic_id="TOPIC-52",
            relationship_type="CORE_TEACHING",
            evidence_block_ids=["BLK-0002"],
        ),
        _relationship(
            claim_id=claim_id,
            topic_id="TOPIC-54",
            relationship_type="REQUIRED_LATER_METHOD",
            evidence_block_ids=["BLK-0004"],
        ),
    ]
    contract["placement_certificate_sha256"] = (
        placement_policy.placement_contract_sha256(contract)
    )
    return row


@contextmanager
def _placement_rows(*rows):
    token = phase38._ACTIVE_GROUNDING_RECORDS.set(list(rows))
    try:
        yield
    finally:
        phase38._ACTIVE_GROUNDING_RECORDS.reset(token)


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


def test_certified_exact_prerequisite_blocks_replace_lexical_candidates(wired):
    row = _certified_row(prerequisite_block_id="BLK-0002")

    with _placement_rows(row):
        rows = _payload_for(wired, "TOPIC-54")

    assert rows["BLK-0002"]["boundary_relation"] == (
        "certified_required_topic_evidence"
    )
    # BLK-0001 is lexically relevant, but strict certified rows cannot receive
    # it merely because it shares long words with the target topic.
    assert "BLK-0001" not in rows


def test_certified_symbol_only_prerequisite_needs_no_lexical_overlap(wired):
    graph = wired
    graph["blocks"].append(
        _block("BLK-0010", "TOPIC-52", 2, "d, aₙ, Sₙ")
    )
    row = _certified_row(prerequisite_block_id="BLK-0010")

    assert not phase38._significant_tokens("d, aₙ, Sₙ")
    with _placement_rows(row):
        rows = _payload_for(graph, "TOPIC-54")

    assert rows["BLK-0010"]["boundary_relation"] == (
        "certified_required_topic_evidence"
    )


def test_certified_earlier_core_plus_later_method_gets_both_exact_sides(wired):
    row = _certified_later_method_row()

    with _placement_rows(row):
        rows = _payload_for(wired, "TOPIC-54")

    assert rows["BLK-0002"]["boundary_relation"] == (
        "certified_required_topic_evidence"
    )
    assert rows["BLK-0004"]["boundary_relation"] == "native_topic"
    assert "BLK-0001" not in rows


def test_certified_row_without_prerequisite_declines_lexical_fallback(wired):
    row = _certified_row(
        prerequisite_topic_id="",
        prerequisite_block_id="",
    )

    with _placement_rows(row):
        rows = _payload_for(wired, "TOPIC-54")

    assert not [
        value for value in rows.values()
        if value["boundary_relation"] == "prerequisite_topic_evidence"
    ]
    assert "BLK-0001" not in rows
    assert "BLK-0002" not in rows


def test_certified_prerequisite_block_must_match_asserted_topic(wired):
    row = _certified_row(
        prerequisite_topic_id="TOPIC-52",
        prerequisite_block_id="BLK-0003",
    )

    with _placement_rows(row), pytest.raises(
        ValueError,
        match=(
            r"BLK-0003 asserts topic TOPIC-52, but the graph assigns TOPIC-53"
        ),
    ):
        _payload_for(wired, "TOPIC-54")


def test_certified_unknown_prerequisite_block_fails_closed(wired):
    row = _certified_row(prerequisite_block_id="BLK-9999")

    with _placement_rows(row), pytest.raises(
        ValueError,
        match="references unknown source block.*BLK-9999",
    ):
        _payload_for(wired, "TOPIC-54")


def test_stale_certified_placement_never_falls_back_to_lexical_evidence(wired):
    row = _certified_row()
    row[grounding_certificate.PLACEMENT_CONTRACT_FIELD][
        "prerequisite_topic_ids"
    ] = []

    with _placement_rows(row), pytest.raises(
        ValueError,
        match="stale or mismatched placement certificate",
    ):
        _payload_for(wired, "TOPIC-54")


def test_live_grounding_wrapper_scopes_and_clears_placement_rows(monkeypatch):
    row = _certified_row()
    observed: list[list[dict] | None] = []

    def original(records, *_args, **_kwargs):
        observed.append(phase38._ACTIVE_GROUNDING_RECORDS.get())
        return records

    monkeypatch.setattr(
        phase38.phase31,
        "_PHASE38_ORIGINAL_GROUND_CONCEPTS",
        original,
    )

    result = phase38._ground_concepts_with_placement_context([row])

    assert result == [row]
    assert observed == [[row]]
    assert phase38._ACTIVE_GROUNDING_RECORDS.get() is None


def test_proposal_cannot_borrow_another_rows_prerequisite_block(
    wired,
    monkeypatch,
):
    row = _certified_row(prerequisite_block_id="BLK-0002")
    applied: list[bool] = []
    monkeypatch.setattr(
        phase38.phase31,
        "_PHASE38_ORIGINAL_APPLY_PROPOSALS",
        lambda *_args, **_kwargs: applied.append(True),
    )

    with pytest.raises(
        ValueError,
        match="outside its certified placement contract: BLK-0001",
    ):
        phase38._apply_proposals(
            [row],
            proposals={"CONCEPT-GROUND-0001": {
                "source_block_ids": ["BLK-0001", "BLK-0004"],
            }},
            index_by_id={"CONCEPT-GROUND-0001": 0},
            candidates=wired["blocks"],
        )

    assert applied == []


def test_proposal_accepts_certified_earlier_core_with_later_native_method(
    wired,
    monkeypatch,
):
    row = _certified_later_method_row()
    applied: list[bool] = []
    monkeypatch.setattr(
        phase38.phase31,
        "_PHASE38_ORIGINAL_APPLY_PROPOSALS",
        lambda *_args, **_kwargs: applied.append(True),
    )

    phase38._apply_proposals(
        [row],
        proposals={"CONCEPT-GROUND-0001": {
            "source_block_ids": ["BLK-0002", "BLK-0004"],
        }},
        index_by_id={"CONCEPT-GROUND-0001": 0},
        candidates=wired["blocks"],
    )

    assert applied == [True]


def test_proposal_requires_a_native_block_from_its_certified_owner(
    wired,
    monkeypatch,
):
    row = _certified_row(prerequisite_block_id="BLK-0002")
    applied: list[bool] = []
    monkeypatch.setattr(
        phase38.phase31,
        "_PHASE38_ORIGINAL_APPLY_PROPOSALS",
        lambda *_args, **_kwargs: applied.append(True),
    )

    with pytest.raises(
        ValueError,
        match="selected no native block from its certified owner topic TOPIC-54",
    ):
        phase38._apply_proposals(
            [row],
            proposals={"CONCEPT-GROUND-0001": {
                "source_block_ids": ["BLK-0002"],
            }},
            index_by_id={"CONCEPT-GROUND-0001": 0},
            candidates=wired["blocks"],
        )

    assert applied == []


# --------------------------------------------------------------------------- #
# The contract keeps the misplacement detector intact
# --------------------------------------------------------------------------- #

def test_contract_requires_the_later_topic_to_be_necessary_not_merely_touched():
    contract = phase38._augment_grounding_payload(
        {"concepts": [], "source_blocks": []},
        page_numbers=[],
    )["boundary_grounding_contract"]

    assert contract["allowed_context_relations"] == [
        "prerequisite_topic_evidence",
        "certified_required_topic_evidence",
    ]
    prerequisite_rule = contract["prerequisite_rule"]
    assert "never establish ownership by themselves" in prerequisite_rule
    assert "at least one native_topic block" in prerequisite_rule
    assert "necessary to understand or perform" in prerequisite_rule
    assert "Mere mention, chronology" in prerequisite_rule
    assert "supported only by prerequisite blocks" in prerequisite_rule
    assert "misplaced" in prerequisite_rule
    assert "CORE_TEACHING" in contract["certified_required_evidence_rule"]


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
    # Advanced placement is legitimate only when the later topic is required.
    advanced = contract["advanced_placement_rule"]
    assert "latest topic" in advanced
    assert "necessary to understand or perform" in advanced
    assert "touching, mentioning" in advanced
    assert "Do not push a concept back" in advanced


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
        assert "at least one native_topic block" in system
        assert "necessary to understand or perform" in system
        assert "Mere mention, chronology" in system
    assert "supported only by prerequisite" in mapper
    assert "cannot establish this topic's ownership" in critic
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


# --------------------------------------------------------------------------- #
# Retrospective reference: the exception for chronological/thematic chapters
# --------------------------------------------------------------------------- #

def test_retrospective_reference_keeps_the_concept_where_it_is_taught():
    """History refers backwards; that must not drag a concept forward.

    The Germania banner (Visualising the Nation) names the Frankfurt
    Parliament (1848 revolutions). The later topic only illustrates the
    earlier one, so the Frankfurt Parliament concept stays where it is
    taught and the allegory topic gains its own concept.
    """

    advanced = phase38._augment_grounding_payload(
        {"concepts": [], "source_blocks": []},
        page_numbers=[],
    )["boundary_grounding_contract"]["advanced_placement_rule"]

    assert "EXCEPTION - retrospective reference" in advanced
    assert "only " in advanced and "illustrates the earlier one" in advanced
    assert "leave it where it is taught" in advanced
    # The decisive test is stated, not left implicit.
    assert "direction of dependence" in advanced
    # And the reason book order is not teaching order in such chapters.
    assert "later in the book does not by itself mean later in teaching" in (
        advanced
    )


def test_both_provider_prompts_carry_the_exception(monkeypatch):
    captured: list[dict] = []
    monkeypatch.setattr(
        phase38.phase22, "_openai_multimodal_json",
        lambda **kwargs: captured.append(kwargs) or {},
    )
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

    assert "mentions or illustrates the earlier material" in mapper
    assert "retrospective-reference" in critic
    # The critic must actively reject a forward-dragged concept.
    assert "returns to the topic that teaches it" in critic


def test_type_prompts_carry_the_exception():
    from app.services import generation

    for key in (
        "concepts.type_mining.system",
        "concepts.type_alignment_review.system",
    ):
        rule = generation.prompts.get_text(key)
        assert "Retrospective reference is the exception" in rule, key
        assert "stays with the topic that teaches it" in rule, key
        assert "Ask which direction the" in rule, key

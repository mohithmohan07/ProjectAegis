"""Behavioural tests for typed topic ownership.

These assert *placement outcomes*, not that instruction strings exist. Every
fixture below is a golden case from docs/concept-placement-rules.md, drawn
from four different subject shapes so nothing is tuned to one textbook:

* Mathematics  - cumulative numbered sections (Arithmetic Progressions)
* History      - chronological/thematic sections that refer backwards (RNE)
* Physics      - cumulative method dependency
* Language     - later grammar that merely appears, and is not tested

No subject keyword appears in the production algorithm; these are fixtures.
"""
from __future__ import annotations

import pytest

from app.services import placement_policy as pp
from app.services.placement_policy import (
    AtomicClaim,
    PlacementPolicyError,
    RelationshipType as RT,
    TopicRelationship,
)


def _graph(topics: list[tuple[str, str]], blocks: list[tuple[str, str, int]]):
    return {
        "topics": [
            {"topic_id": tid, "title": title} for tid, title in topics
        ],
        "blocks": [
            {"block_id": bid, "topic_id": tid, "order": order}
            for bid, tid, order in blocks
        ],
    }


# --------------------------------------------------------------------------- #
# Teaching order is sealed from source position, not section numbers
# --------------------------------------------------------------------------- #

def test_teaching_order_comes_from_source_position_not_numbering():
    # Deliberately unnumbered, non-sequential titles in a shuffled declaration.
    graph = _graph(
        topics=[("T-C", "Nationalism and Imperialism"),
                ("T-A", "The French Revolution"),
                ("T-B", "Visualising the Nation")],
        blocks=[("B3", "T-C", 300), ("B1", "T-A", 100), ("B2", "T-B", 200)],
    )
    order = pp.seal_teaching_order(graph, source_contract_hash="sc")

    assert order.rank("T-A") == 0
    assert order.rank("T-B") == 1
    assert order.rank("T-C") == 2
    assert order.latest(["T-B", "T-A"]) == "T-B"


def test_teaching_order_hash_changes_only_with_order():
    graph = _graph(
        topics=[("T-A", "One"), ("T-B", "Two")],
        blocks=[("B1", "T-A", 10), ("B2", "T-B", 20)],
    )
    same_titles_different_order = _graph(
        topics=[("T-A", "One"), ("T-B", "Two")],
        blocks=[("B1", "T-A", 90), ("B2", "T-B", 20)],
    )
    a = pp.seal_teaching_order(graph, source_contract_hash="sc")
    b = pp.seal_teaching_order(graph, source_contract_hash="sc")
    c = pp.seal_teaching_order(
        same_titles_different_order, source_contract_hash="sc")

    assert a.sha256 == b.sha256
    assert a.sha256 != c.sha256


# --------------------------------------------------------------------------- #
# Mathematics: Arithmetic Progressions (M1-M3)
# --------------------------------------------------------------------------- #

AP = _graph(
    topics=[("AP-52", "Arithmetic Progressions"),
            ("AP-53", "nth Term of an AP"),
            ("AP-54", "Sum of First n Terms of an AP")],
    blocks=[("B52", "AP-52", 100), ("B53", "AP-53", 200),
            ("B54", "AP-54", 300)],
)
AP_ORDER = pp.seal_teaching_order(AP, source_contract_hash="ap")


def test_M1_sign_of_d_stays_in_the_definitions_topic():
    """Sign of d controls increasing/decreasing/constant terms -> 5.2."""
    claim = AtomicClaim(
        claim_id="M1a",
        normalized_claim=(
            "The sign of the common difference d determines whether the terms "
            "of an AP increase, decrease, or stay constant."
        ),
        evidence_block_ids=("B52",),
    )
    rels = [
        TopicRelationship("M1a", "AP-52", RT.CORE_TEACHING, True, ("B52",)),
    ]
    decision = pp.compute_placement(claim, rels, AP_ORDER)
    assert decision.owner_topic_id == "AP-52"


def test_M1_effect_of_d_on_the_finite_sum_is_owned_by_the_sum_topic():
    """d's effect within Sn = n/2[2a+(n-1)d] -> 5.4, with 5.2 prerequisite."""
    claim = AtomicClaim(
        claim_id="M1b",
        normalized_claim=(
            "In Sn = n/2[2a + (n-1)d], the common difference d affects the "
            "finite sum for a fixed first term and fixed n."
        ),
        evidence_block_ids=("B54",),
    )
    rels = [
        TopicRelationship("M1b", "AP-54", RT.CORE_TEACHING, True, ("B54",)),
        TopicRelationship(
            "M1b", "AP-52", RT.REQUIRED_PREREQUISITE, True, ("B52",)),
    ]
    decision = pp.compute_placement(claim, rels, AP_ORDER)

    assert decision.owner_topic_id == "AP-54"
    assert decision.prerequisite_topic_ids == ("AP-52",)
    # The prerequisite is required, but never the owner.
    assert "AP-52" in decision.required_topic_ids


def test_M2_recognising_an_AP_situation_before_summing_is_owned_by_the_sum_topic():
    """Non-adjacent prerequisite: 5.2 evidence, owned by 5.4."""
    claim = AtomicClaim(
        claim_id="M2",
        normalized_claim=(
            "Recognising that a real-world situation forms an AP is required "
            "before the sum formula can be applied to it."
        ),
        evidence_block_ids=("B54", "B52"),
    )
    rels = [
        TopicRelationship("M2", "AP-54", RT.CORE_TEACHING, True, ("B54",)),
        TopicRelationship(
            "M2", "AP-52", RT.REQUIRED_PREREQUISITE, True, ("B52",)),
    ]
    decision = pp.compute_placement(claim, rels, AP_ORDER)
    assert decision.owner_topic_id == "AP-54"


def test_M3_task_needing_nth_term_and_sum_is_owned_by_the_later_topic():
    """A combined numerical belongs to 5.4 even though it tests both."""
    task = AtomicClaim(
        claim_id="M3",
        normalized_claim=(
            "Given a and d, find the 10th term and the sum of the first 10 "
            "terms."
        ),
        evidence_block_ids=("B53", "B54"),
        protected_source_items=("QINV-0016.1",),
    )
    rels = [
        TopicRelationship(
            "M3", "AP-53", RT.REQUIRED_LATER_METHOD, True, ("B53",)),
        TopicRelationship(
            "M3", "AP-54", RT.REQUIRED_LATER_METHOD, True, ("B54",)),
    ]
    decision = pp.compute_placement(task, rels, AP_ORDER)

    # Latest genuinely-required topic wins; not the first, not the majority.
    assert decision.owner_topic_id == "AP-54"
    assert set(decision.required_topic_ids) == {"AP-53", "AP-54"}


# --------------------------------------------------------------------------- #
# History: The Rise of Nationalism in Europe (H1-H3)
# --------------------------------------------------------------------------- #

RNE = _graph(
    topics=[("R1", "The French Revolution and the Idea of the Nation"),
            ("R2", "The Making of Nationalism in Europe"),
            ("R3", "The Age of Revolutions 1830-1848"),
            ("R4", "The Making of Germany and Italy"),
            ("R5", "Visualising the Nation"),
            ("R6", "Nationalism and Imperialism")],
    blocks=[("B1", "R1", 100), ("B2", "R2", 200), ("B3", "R3", 300),
            ("B4", "R4", 400), ("B5", "R5", 500), ("B6", "R6", 600)],
)
RNE_ORDER = pp.seal_teaching_order(RNE, source_contract_hash="rne")


def test_H1_zollverein_to_unification_is_owned_by_the_unification_topic():
    """Non-adjacent required relationship: evidence in R2, owned by R4."""
    claim = AtomicClaim(
        claim_id="H1",
        normalized_claim=(
            "Economic union through the zollverein contributed to German "
            "political unification."
        ),
        evidence_block_ids=("B2", "B4"),
    )
    rels = [
        TopicRelationship("H1", "R4", RT.CORE_TEACHING, True, ("B4",)),
        TopicRelationship(
            "H1", "R2", RT.REQUIRED_PREREQUISITE, True, ("B2",)),
    ]
    decision = pp.compute_placement(claim, rels, RNE_ORDER)

    assert decision.owner_topic_id == "R4"
    assert decision.prerequisite_topic_ids == ("R2",)


def test_H1_irreducible_relationship_is_not_split_into_unrelated_parts():
    """Splitting the relationship into standalone halves must be refused."""
    parent = AtomicClaim(
        claim_id="H1",
        normalized_claim=(
            "Economic union through the zollverein contributed to German "
            "political unification."
        ),
        protected_source_items=("BLK-Z", "BLK-U"),
    )
    # A "split" that drops the relationship and keeps two isolated topics.
    bad_children = [
        AtomicClaim(
            claim_id="H1a",
            normalized_claim="The zollverein was a customs union.",
            origin_claim_ids=(),
            protected_source_items=("BLK-Z",),
        ),
        AtomicClaim(
            claim_id="H1b",
            normalized_claim="Germany was unified in 1871.",
            origin_claim_ids=(),
            protected_source_items=("BLK-U",),
        ),
    ]
    audit = pp.audit_split([parent], bad_children)

    assert audit.committed is False
    assert "H1" in audit.missing_claims


def test_H2_retrospective_reference_creates_an_edge_not_a_move():
    """Germania (R5) names the Frankfurt Parliament (R3); R3 keeps it."""
    claim = AtomicClaim(
        claim_id="H2",
        normalized_claim=(
            "The Frankfurt Parliament drafted a constitution for a German "
            "nation in 1848."
        ),
        evidence_block_ids=("B3",),
    )
    rels = [
        TopicRelationship("H2", "R3", RT.CORE_TEACHING, True, ("B3",)),
        # R5 merely points back at it.
        TopicRelationship(
            "H2", "R5", RT.RETROSPECTIVE_REFERENCE, False, ("B5",)),
    ]
    decision = pp.compute_placement(claim, rels, RNE_ORDER)

    # The later topic does NOT take ownership...
    assert decision.owner_topic_id == "R3"
    # ...it gets a typed edge instead, and no duplicate concept.
    assert ("R5", "RETROSPECTIVE_REFERENCE") in decision.reference_edges
    assert "R5" not in decision.required_topic_ids


def test_H3_vienna_stays_but_its_later_unravelling_is_later_owned():
    """Two claims: the settlement (R2) and its Balkan unravelling (R6)."""
    vienna = AtomicClaim(
        claim_id="H3a",
        normalized_claim=(
            "The Treaty of Vienna of 1815 restored conservative monarchies."
        ),
        evidence_block_ids=("B2",),
    )
    unravelling = AtomicClaim(
        claim_id="H3b",
        normalized_claim=(
            "The Vienna settlement was progressively challenged as Balkan "
            "nationalities broke away from Ottoman control."
        ),
        evidence_block_ids=("B6", "B2"),
    )
    rels = [
        TopicRelationship("H3a", "R2", RT.CORE_TEACHING, True, ("B2",)),
        TopicRelationship(
            "H3a", "R6", RT.RETROSPECTIVE_REFERENCE, False, ("B6",)),
        TopicRelationship("H3b", "R6", RT.CORE_TEACHING, True, ("B6",)),
        TopicRelationship(
            "H3b", "R2", RT.REQUIRED_PREREQUISITE, True, ("B2",)),
    ]
    a = pp.compute_placement(vienna, rels, RNE_ORDER)
    b = pp.compute_placement(unravelling, rels, RNE_ORDER)

    assert a.owner_topic_id == "R2"          # settlement stays where taught
    assert b.owner_topic_id == "R6"          # the relationship is later-owned
    assert b.prerequisite_topic_ids == ("R2",)


# --------------------------------------------------------------------------- #
# Physics and Language: universality beyond the two debugged chapters
# --------------------------------------------------------------------------- #

def test_physics_cumulative_method_takes_the_latest_required_topic():
    graph = _graph(
        topics=[("P1", "Electric Current"), ("P2", "Ohm's Law"),
                ("P3", "Resistors in Series and Parallel"),
                ("P4", "Heating Effect of Electric Current")],
        blocks=[("Q1", "P1", 10), ("Q2", "P2", 20), ("Q3", "P3", 30),
                ("Q4", "P4", 40)],
    )
    order = pp.seal_teaching_order(graph, source_contract_hash="phy")
    task = AtomicClaim(
        claim_id="P",
        normalized_claim=(
            "Find the heat produced in a parallel combination of resistors "
            "connected across a supply."
        ),
        evidence_block_ids=("Q3", "Q4"),
    )
    rels = [
        TopicRelationship("P", "P2", RT.REQUIRED_PREREQUISITE, True, ("Q2",)),
        TopicRelationship("P", "P3", RT.REQUIRED_LATER_METHOD, True, ("Q3",)),
        TopicRelationship("P", "P4", RT.CORE_TEACHING, True, ("Q4",)),
    ]
    assert pp.compute_placement(task, rels, order).owner_topic_id == "P4"


def test_language_task_is_not_moved_by_incidental_later_grammar():
    graph = _graph(
        topics=[("L1", "Reading Comprehension"), ("L2", "Reported Speech")],
        blocks=[("W1", "L1", 10), ("W2", "L2", 20)],
    )
    order = pp.seal_teaching_order(graph, source_contract_hash="lang")
    task = AtomicClaim(
        claim_id="L",
        normalized_claim=(
            "Answer questions on the passage. The passage happens to contain "
            "a reported-speech sentence, which the task does not test."
        ),
        evidence_block_ids=("W1",),
    )
    rels = [
        TopicRelationship("L", "L1", RT.CORE_TEACHING, True, ("W1",)),
        TopicRelationship("L", "L2", RT.INCIDENTAL_MENTION, False, ()),
    ]
    decision = pp.compute_placement(task, rels, order)

    assert decision.owner_topic_id == "L1"
    assert ("L2", "INCIDENTAL_MENTION") in decision.reference_edges


# --------------------------------------------------------------------------- #
# The things that must never establish ownership
# --------------------------------------------------------------------------- #

def test_ownership_is_invariant_to_relationship_order():
    """Reversing the input order cannot change the computed owner."""
    claim = AtomicClaim(claim_id="X", normalized_claim="c",
                        evidence_block_ids=("B53", "B54"))
    rels = [
        TopicRelationship("X", "AP-53", RT.CORE_TEACHING, True, ("B53",)),
        TopicRelationship(
            "X", "AP-54", RT.REQUIRED_LATER_METHOD, True, ("B54",)),
    ]
    forward = pp.compute_placement(claim, rels, AP_ORDER)
    reversed_ = pp.compute_placement(claim, list(reversed(rels)), AP_ORDER)

    assert forward.owner_topic_id == reversed_.owner_topic_id == "AP-54"
    assert forward.required_topic_ids == reversed_.required_topic_ids


def test_physical_location_alone_never_establishes_ownership():
    """A claim printed under 5.2 but requiring 5.4 is owned by 5.4."""
    claim = AtomicClaim(
        claim_id="LOC",
        normalized_claim="c",
        source_location_topic_id="AP-52",   # printed here...
        evidence_block_ids=("B54",),
    )
    rels = [
        TopicRelationship("LOC", "AP-54", RT.CORE_TEACHING, True, ("B54",)),
    ]
    assert pp.compute_placement(claim, rels, AP_ORDER).owner_topic_id == "AP-54"


@pytest.mark.parametrize(
    "kind",
    [RT.RETROSPECTIVE_REFERENCE, RT.INCIDENTAL_MENTION],
)
def test_edge_only_relationships_can_never_own(kind):
    claim = AtomicClaim(claim_id="E", normalized_claim="c",
                        evidence_block_ids=("B52",))
    rels = [
        TopicRelationship("E", "AP-52", RT.CORE_TEACHING, True, ("B52",)),
        TopicRelationship("E", "AP-54", kind, True, ("B54",)),
    ]
    assert pp.compute_placement(claim, rels, AP_ORDER).owner_topic_id == "AP-52"


def test_necessity_without_evidence_is_not_certified():
    """An unevidenced necessity claim cannot pull ownership forward."""
    claim = AtomicClaim(claim_id="N", normalized_claim="c",
                        evidence_block_ids=("B52",))
    rels = [
        TopicRelationship("N", "AP-52", RT.CORE_TEACHING, True, ("B52",)),
        # Asserts necessity but cites nothing.
        TopicRelationship("N", "AP-54", RT.REQUIRED_LATER_METHOD, True, ()),
    ]
    assert pp.compute_placement(claim, rels, AP_ORDER).owner_topic_id == "AP-52"


def test_critic_rejection_removes_ownership_authority():
    claim = AtomicClaim(claim_id="C", normalized_claim="c",
                        evidence_block_ids=("B52",))
    rels = [
        TopicRelationship("C", "AP-52", RT.CORE_TEACHING, True, ("B52",)),
        TopicRelationship(
            "C", "AP-54", RT.REQUIRED_LATER_METHOD, True, ("B54",),
            critic_verdict="rejected"),
    ]
    assert pp.compute_placement(claim, rels, AP_ORDER).owner_topic_id == "AP-52"


def test_claim_with_no_owning_relationship_fails_closed():
    claim = AtomicClaim(claim_id="Z", normalized_claim="c")
    rels = [
        TopicRelationship(
            "Z", "AP-52", RT.REQUIRED_PREREQUISITE, True, ("B52",)),
    ]
    with pytest.raises(PlacementPolicyError, match="no certified owning"):
        pp.compute_placement(claim, rels, AP_ORDER)


def test_prerequisite_later_than_owner_is_a_contradiction():
    claim = AtomicClaim(claim_id="P", normalized_claim="c",
                        evidence_block_ids=("B52",))
    rels = [
        TopicRelationship("P", "AP-52", RT.CORE_TEACHING, True, ("B52",)),
        TopicRelationship(
            "P", "AP-54", RT.REQUIRED_PREREQUISITE, True, ("B54",)),
    ]
    with pytest.raises(PlacementPolicyError, match="taught later"):
        pp.compute_placement(claim, rels, AP_ORDER)


# --------------------------------------------------------------------------- #
# Split survival and protected items
# --------------------------------------------------------------------------- #

def test_split_survives_when_both_sides_and_all_protected_items_are_kept():
    parent = AtomicClaim(
        claim_id="S", normalized_claim="d affects terms and the finite sum",
        protected_source_items=("QINV-0007", "FIG-0003"),
    )
    children = [
        AtomicClaim(claim_id="S1", normalized_claim="sign of d and terms",
                    origin_claim_ids=("S",),
                    protected_source_items=("QINV-0007",)),
        AtomicClaim(claim_id="S2", normalized_claim="d in the finite sum",
                    origin_claim_ids=("S",),
                    protected_source_items=("FIG-0003",)),
    ]
    assert pp.audit_split([parent], children).committed is True


def test_split_that_drops_a_protected_item_is_refused():
    parent = AtomicClaim(
        claim_id="S", normalized_claim="c",
        protected_source_items=("QINV-0007", "FIG-0003"),
    )
    children = [
        AtomicClaim(claim_id="S1", normalized_claim="c1",
                    origin_claim_ids=("S",),
                    protected_source_items=("QINV-0007",)),
    ]
    audit = pp.audit_split([parent], children)

    assert audit.committed is False
    assert audit.missing_protected_items == ("FIG-0003",)


def test_split_with_no_children_is_refused_rather_than_deleting_the_parent():
    parent = AtomicClaim(claim_id="S", normalized_claim="c")
    audit = pp.audit_split([parent], [])

    assert audit.committed is False
    assert audit.missing_claims == ("S",)


# --------------------------------------------------------------------------- #
# Provider contract and certificate
# --------------------------------------------------------------------------- #

def test_provider_schema_enumerates_ids_so_they_cannot_be_reformatted():
    """Sub-part QIDs were lost because nothing constrained the id string."""
    schema = pp.relationship_schema(
        ["QINV-0016.1", "QINV-0016.2"], ["AP-52", "AP-54"])
    props = schema["schema"]["properties"]["relationships"]["items"]["properties"]

    assert props["claim_id"]["enum"] == ["QINV-0016.1", "QINV-0016.2"]
    assert props["topic_id"]["enum"] == ["AP-52", "AP-54"]
    assert schema["strict"] is True


def test_parser_discards_unknown_ids_and_types_rather_than_guessing():
    parsed = pp.parse_relationships(
        {
            "relationships": [
                {"claim_id": "A", "topic_id": "AP-52",
                 "relationship_type": "CORE_TEACHING", "necessity": True,
                 "evidence_block_ids": ["B52"], "reason": "r"},
                {"claim_id": "GHOST", "topic_id": "AP-52",
                 "relationship_type": "CORE_TEACHING", "necessity": True,
                 "evidence_block_ids": [], "reason": "r"},
                {"claim_id": "A", "topic_id": "AP-52",
                 "relationship_type": "MADE_UP", "necessity": True,
                 "evidence_block_ids": [], "reason": "r"},
            ]
        },
        known_claim_ids=["A"],
        known_topic_ids=["AP-52"],
    )
    assert len(parsed) == 1
    assert parsed[0].relationship_type is RT.CORE_TEACHING


def test_certificate_changes_when_any_placement_changes():
    claim = AtomicClaim(claim_id="X", normalized_claim="c",
                        evidence_block_ids=("B54",))
    base = [TopicRelationship("X", "AP-54", RT.CORE_TEACHING, True, ("B54",))]
    moved = [TopicRelationship("X", "AP-52", RT.CORE_TEACHING, True, ("B52",))]

    a = pp.placement_certificate_sha256(
        {"X": pp.compute_placement(claim, base, AP_ORDER)}, AP_ORDER)
    b = pp.placement_certificate_sha256(
        {"X": pp.compute_placement(claim, base, AP_ORDER)}, AP_ORDER)
    c = pp.placement_certificate_sha256(
        {"X": pp.compute_placement(claim, moved, AP_ORDER)}, AP_ORDER)

    assert a == b
    assert a != c


def test_audit_row_shows_relationship_to_required_topics_to_owner():
    claim = AtomicClaim(claim_id="M2", normalized_claim="c",
                        evidence_block_ids=("B54",))
    rels = [
        TopicRelationship("M2", "AP-54", RT.CORE_TEACHING, True, ("B54",)),
        TopicRelationship(
            "M2", "AP-52", RT.REQUIRED_PREREQUISITE, True, ("B52",)),
    ]
    row = pp.compute_placement(claim, rels, AP_ORDER).as_audit_row(AP_ORDER)

    assert row["owner"]["title"] == "Sum of First n Terms of an AP"
    assert [p["title"] for p in row["prerequisites"]] == [
        "Arithmetic Progressions"]
    assert row["rationale"].startswith("owner=AP-54")

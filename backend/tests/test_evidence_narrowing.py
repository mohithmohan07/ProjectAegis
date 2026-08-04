"""The terminal disposition: a run always produces output.

These tests pin the product rule that replaced retirement and structured
failure. When every bounded repair has failed, an ungroundable concept is
*modified to match its evidence* -- narrowed to the clauses the canonical
source supports, or restated verbatim from the source when no written clause
survives. Nothing is deleted, nothing is invented, and nothing waits for a
person.

The examples are the ones from ``docs/concept-placement-rules.md`` so a
failure here reads as a policy violation rather than an assertion mismatch.
"""
from __future__ import annotations

import pytest

from app.services import evidence_narrowing as en


# --------------------------------------------------------------------------- #
# Fixtures modelled on the two worked chapters in the spec
# --------------------------------------------------------------------------- #

_AP_52 = (
    "An arithmetic progression is a list of numbers in which each term is "
    "obtained by adding a fixed number to the preceding term. The fixed "
    "number is called the common difference of the AP and is denoted by d. "
    "The common difference can be positive, negative or zero, so an AP may "
    "increase or decrease."
)
_AP_54 = (
    "The sum of the first n terms of an AP is given by Sn = n/2 [2a + "
    "(n-1)d]. Substituting the common difference into this formula shows "
    "how the sum grows as more terms are added."
)


def _graph():
    return {
        "source_contract_hash": "sc",
        "topics": [
            {"topic_id": "T-52", "title": "Arithmetic Progressions"},
            {"topic_id": "T-54", "title": "Sum of First n Terms"},
        ],
        "blocks": [
            {"block_id": "B52", "topic_id": "T-52", "order": 10,
             "kind": "paragraph"},
            {"block_id": "B54", "topic_id": "T-54", "order": 90,
             "kind": "paragraph"},
            # A heading carries a topic's words but teaches nothing; it must
            # never be usable as the evidence that supports a claim.
            {"block_id": "BH", "topic_id": "T-54", "order": 89,
             "kind": "heading"},
        ],
    }


def _canonical():
    return {
        "blocks": [
            {"block_id": "B52", "display_text": _AP_52},
            {"block_id": "B54", "display_text": _AP_54},
            {"block_id": "BH", "display_text": "Sum of First n Terms"},
        ]
    }


def _row(description, *, topic_id="T-52", title="Common difference"):
    return {
        "concept_title": title,
        "topic": "Arithmetic Progressions" if topic_id == "T-52"
                 else "Sum of First n Terms",
        "_semantic_topic_id": topic_id,
        "parent_concept": "Arithmetic Progressions",
        "concept_details": (
            f"Description: {description} // "
            "Achieving Mastery: The learner identifies d in a given list."
        ),
    }


@pytest.fixture
def evidence():
    return en.build_evidence(_graph(), _canonical())


# --------------------------------------------------------------------------- #
# Nothing is ever deleted
# --------------------------------------------------------------------------- #

def test_every_input_row_survives_the_disposition(evidence):
    rows = [
        _row("The common difference d can be positive, negative or zero."),
        _row("Aegis invented this claim about matrices and eigenvalues."),
        _row("Sn grows as more terms are added.", topic_id="T-54"),
    ]
    out, audit = en.narrow_records(rows, evidence=evidence)

    assert len(out) == len(rows)
    assert [row["concept_title"] for row in out] == [
        row["concept_title"] for row in rows
    ]
    assert audit.version == en.DISPOSITION_VERSION


def test_a_concept_with_no_evidence_at_all_is_still_returned():
    """A missing graph is a source defect, not a reason to lose a concept."""
    rows = [_row("Any claim at all.")]
    out, audit = en.narrow_records(rows, graph=None, canonical=None)

    assert len(out) == 1
    assert out[0] == rows[0]  # byte-identical: no bookkeeping field written
    assert audit.changed_rows == []
    assert "no canonical source blocks" in audit.rows[0].note


# --------------------------------------------------------------------------- #
# Supported material is left alone
# --------------------------------------------------------------------------- #

def test_a_fully_supported_claim_is_untouched(evidence):
    rows = [_row(
        "The common difference can be positive, negative or zero, so an AP "
        "may increase or decrease."
    )]
    out, audit = en.narrow_records(rows, evidence=evidence)

    assert out[0] == rows[0]
    assert audit.changed_rows == []
    assert en.NARROWED_FLAG_FIELD not in out[0]


def test_inflected_wording_still_counts_as_supported(evidence):
    """"increases"/"increase" is a phrasing difference, not a new claim."""
    rows = [_row(
        "The common differences can be positive, negative or zero, so an AP "
        "increases or decreases."
    )]
    _out, audit = en.narrow_records(rows, evidence=evidence)

    assert audit.changed_rows == []


# --------------------------------------------------------------------------- #
# Rule M1: the unsupported half goes, the supported half stays
# --------------------------------------------------------------------------- #

def test_unsupported_clause_is_removed_and_the_supported_one_kept(evidence):
    """The spec's own example: d in 5.2, Sn behaviour not taught there."""
    rows = [_row(
        "The common difference d can be positive, negative or zero. "
        "Therefore the eigenvalues of its companion matrix are distinct."
    )]
    out, audit = en.narrow_records(rows, evidence=evidence)
    claim = out[0]["concept_details"]

    assert "common difference" in claim
    assert "eigenvalues" not in claim
    assert out[0][en.NARROWED_KIND_FIELD] == "narrowed"
    assert audit.rows[0].dropped_clauses == (
        "Therefore the eigenvalues of its companion matrix are distinct.",
    )
    # Pedagogical sections are not part of the source-facing claim and are
    # never rewritten by a grounding disposition.
    assert "Achieving Mastery: The learner identifies d" in claim


def test_narrowing_records_what_it_removed(evidence):
    rows = [_row(
        "The common difference d can be positive, negative or zero. "
        "It also proves the Riemann hypothesis."
    )]
    out, _audit = en.narrow_records(rows, evidence=evidence)

    assert out[0][en.NARROWED_ORIGINAL_FIELD].startswith("The common")
    assert out[0][en.NARROWED_DROPPED_FIELD] == [
        "It also proves the Riemann hypothesis."
    ]
    assert out[0][en.NARROWED_EVIDENCE_FIELD] == ["B52"]
    assert out[0][en.NARROWED_NOTE_FIELD]


def test_partial_token_overlap_is_not_support(evidence):
    """Half a match is exactly the over-inference grounding rejected."""
    assert not en.clause_supported(
        "The common difference d determines the orbital period of Mars.",
        en.build_evidence(_graph(), _canonical())["T-52"].tokens,
    )


def test_a_bare_fragment_cannot_pass_by_being_short(evidence):
    tokens = evidence["T-52"].tokens
    assert not en.clause_supported("The AP.", tokens)
    assert not en.clause_supported("Numbers.", tokens)


# --------------------------------------------------------------------------- #
# When nothing survives, the source speaks for itself
# --------------------------------------------------------------------------- #

def test_a_wholly_unsupported_concept_is_restated_from_source(evidence):
    rows = [_row(
        "Arithmetic progressions were first catalogued in Babylonian "
        "astronomical diaries recovered from Uruk."
    )]
    out, audit = en.narrow_records(rows, evidence=evidence)
    claim = out[0]["concept_details"]

    assert "Babylonian" not in claim
    assert out[0][en.NARROWED_KIND_FIELD] == "restated"
    # What ships is the source's own sentence, so it is supported by
    # construction rather than by a provider's assertion.
    restated = audit.rows[0].kept_clauses[0]
    assert restated in _AP_52
    assert out[0][en.NARROWED_EVIDENCE_FIELD] == ["B52"]


def test_a_restatement_is_never_taken_from_a_heading_block():
    """Headings repeat the topic title; they teach nothing."""
    evidence = en.build_evidence(_graph(), _canonical())

    assert evidence["T-54"].block_ids == ("B54",)


def test_the_restatement_choice_is_deterministic(evidence):
    rows = [_row(
        "An entirely unrelated assertion about thermodynamic equilibrium."
    )]
    first, _ = en.narrow_records(rows, evidence=evidence)
    second, _ = en.narrow_records(rows, evidence=evidence)

    assert first == second


def test_the_restatement_prefers_the_sentence_the_concept_is_about(evidence):
    """Selection is by overlap with the concept, not by block order."""
    rows = [_row(
        "The sum formula is applied to the first hundred natural numbers "
        "using Gauss's schoolroom trick.",
        topic_id="T-54",
        title="Sum of an AP",
    )]
    out, _audit = en.narrow_records(rows, evidence=evidence)

    assert "Sn = n/2" in out[0]["concept_details"]


# --------------------------------------------------------------------------- #
# Placement is not this module's business
# --------------------------------------------------------------------------- #

def test_narrowing_never_moves_a_concept_between_topics(evidence):
    """Ownership belongs to placement_policy; narrowing only edits claims."""
    rows = [_row(
        "The common difference d can be positive, negative or zero. "
        "Substituting it into Sn shows how the sum grows.",
    )]
    out, _audit = en.narrow_records(rows, evidence=evidence)

    # The Sn clause is unsupported *here* and is dropped. It is not silently
    # relocated to T-54: only Phase 3.2 may move a concept.
    assert out[0]["topic"] == "Arithmetic Progressions"
    assert out[0]["_semantic_topic_id"] == "T-52"
    assert "Sn" not in out[0]["concept_details"].split("Achieving Mastery")[0]


def test_only_the_named_concepts_are_reconsidered(evidence):
    rows = [
        _row("Something entirely unsupported about Uruk.", title="Alpha"),
        _row("Another unsupported claim about Uruk.", title="Beta"),
    ]
    out, audit = en.narrow_records(
        rows, evidence=evidence, concept_titles=["Alpha"]
    )

    assert out[1] == rows[1]
    assert [row.concept_title for row in audit.rows] == ["Alpha"]


# --------------------------------------------------------------------------- #
# Clause splitting must not damage the material it is protecting
# --------------------------------------------------------------------------- #

def test_a_decimal_does_not_split_a_clause():
    assert en.split_clauses("The ladder rung is 2.5 metres long.") == [
        "The ladder rung is 2.5 metres long."
    ]


def test_display_maths_is_not_split_on_its_punctuation():
    """A "." inside \\[ ... \\] is notation, not a sentence boundary."""
    clauses = en.split_clauses(
        r"The sum is \[ S_n = \frac{n}{2}[2a + (n-1)d]. \] It follows that "
        r"the total is finite. A separate sentence."
    )
    # Two sentences, not three: the formula stays whole and attached.
    assert len(clauses) == 2
    assert r"\frac{n}{2}[2a + (n-1)d]." in clauses[0]
    assert clauses[1] == "A separate sentence."


def test_semicolons_separate_independently_checkable_claims():
    assert en.split_clauses("d is fixed; the AP is therefore linear.") == [
        "d is fixed;", "the AP is therefore linear."
    ]


def test_the_audit_summary_names_what_happened(evidence):
    rows = [
        _row("The common difference d is zero. And also Uruk tablets."),
        _row("Wholly unrelated Babylonian astronomy.", title="Beta"),
    ]
    _out, audit = en.narrow_records(rows, evidence=evidence)
    summary = audit.summary()

    assert "narrowed" in summary
    assert "restated" in summary

"""Chapter-level refinement: continuous Type numbering + type reduction."""
from app.services import concept_refiner as cr


def _rec(title, details, topic="Topic 01"):
    return {"topic": topic, "concept_title": title, "concept_details": details, "keywords": ""}


def test_continuous_type_numbering_across_concepts():
    records = [
        _rec("A", "Description: a // Types: Type 01: X Case 01: q1 Case 02: q2 Type 02: Y Case 01: q3 // Misconception: m"),
        _rec("B", "Description: b // Types: Type 01: Z Case 01: q4 // Misconception: m"),
    ]
    out = cr.renumber_types_continuously(records)
    # A keeps Type 01, Type 02; B's single type continues to Type 03.
    assert "Type 01: X" in out[0]["concept_details"]
    assert "Type 02: Y" in out[0]["concept_details"]
    assert "Type 03: Z" in out[1]["concept_details"]
    # Case numbering restarts within each Type.
    assert "Type 01: X Case 01: q1 Case 02: q2" in out[0]["concept_details"]
    assert "Type 02: Y Case 01: q3" in out[0]["concept_details"]
    assert "Type 03: Z Case 01: q4" in out[1]["concept_details"]


def test_culmination_excluded_from_continuous_sequence():
    records = [
        _rec("A", "Description: a // Types: Type 01: X Case 01: q1 // Misconception: m"),
        _rec("Culmination - Topic 01", "Description: c // Types: Type 01: Mix Case 01: q // Misconception: m"),
        _rec("B", "Description: b // Types: Type 01: Y Case 01: q2 // Misconception: m"),
    ]
    out = cr.renumber_types_continuously(records)
    assert "Type 01: X" in out[0]["concept_details"]
    # Culmination restarts at Type 01 and does NOT advance the chapter counter.
    assert "Type 01: Mix" in out[1]["concept_details"]
    # B continues from A (Type 02), not from the culmination.
    assert "Type 02: Y" in out[2]["concept_details"]


def test_reduce_types_drops_caseless_theory_block():
    # A theory concept whose Types block has no concrete Case is dropped.
    details = "Description: theory only // Types: Type 01: Definition // Misconception: m"
    out = cr.reduce_type_sections(details)
    assert "Types:" not in out
    assert "Description: theory only" in out
    assert "Misconception: m" in out


def test_reduce_types_keeps_real_types():
    details = "Description: d // Types: Type 01: Solve Case 01: compute // Misconception: m"
    assert cr.reduce_type_sections(details) == details


def test_refine_chapter_reduces_then_numbers_continuously():
    records = [
        _rec("Theory", "Description: t // Types: Type 01: Definition // Misconception: m"),
        _rec("Solve A", "Description: a // Types: Type 01: P Case 01: c1 // Misconception: m"),
        _rec("Solve B", "Description: b // Types: Type 01: Q Case 01: c2 Type 02: R Case 01: c3 // Misconception: m"),
    ]
    out = cr.refine_chapter(records)
    # Theory lost its Types block.
    assert "Types:" not in out[0]["concept_details"]
    # Numbering is continuous across the concepts that DO have types.
    assert "Type 01: P" in out[1]["concept_details"]
    assert "Type 02: Q" in out[2]["concept_details"]
    assert "Type 03: R" in out[2]["concept_details"]


def test_records_without_types_are_untouched():
    records = [_rec("X", "Description: only // Misconception: none")]
    out = cr.refine_chapter(records)
    assert out[0]["concept_details"] == "Description: only // Misconception: none"

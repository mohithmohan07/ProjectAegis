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


def test_culmination_uses_separate_miscellaneous_sequence():
    records = [
        _rec("A", "Description: a // Types: Type 01: X Case 01: q1 // Misconception: m"),
        _rec("Culmination - Topic 01", "Description: c // Types: Type 01: Mix Case 01: q // Misconception: m"),
        _rec("B", "Description: b // Types: Type 01: Y Case 01: q2 // Misconception: m"),
        _rec("Culmination - Topic 02", "Description: c // Types: Type 01: Mix2 Case 01: q // Misconception: m"),
    ]
    out = cr.renumber_types_continuously(records)
    assert "Type 01: X" in out[0]["concept_details"]
    # Culmination uses the Miscellaneous sequence (does not touch the chapter counter).
    assert "Miscellaneous Type 01: Mix" in out[1]["concept_details"]
    # B continues the regular sequence from A (Type 02), ignoring the culmination.
    assert "Type 02: Y" in out[2]["concept_details"]
    # The 2nd culmination continues the Miscellaneous sequence (02), chapter-wide.
    assert "Miscellaneous Type 02: Mix2" in out[3]["concept_details"]


def test_miscellaneous_numbering_is_idempotent():
    records = [
        _rec("Culmination - T", "Description: c // Types: Type 01: M Case 01: q // Misconception: m"),
    ]
    once = cr.renumber_types_continuously(records)
    twice = cr.renumber_types_continuously(once)
    # Re-running must not stack the prefix.
    assert "Miscellaneous Type 01: M" in twice[0]["concept_details"]
    assert "Miscellaneous Miscellaneous" not in twice[0]["concept_details"]


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
    assert out[0]["concept_details"] == "Description: only // Misconceptions: none"


def test_refine_chapter_adds_missing_misconceptions_to_normal_concepts():
    records = [
        _rec("Basic Proportionality Theorem", "Description: relates side ratios."),
        _rec("Culmination - Topic 01", "Description: Recap"),
    ]
    out = cr.refine_chapter(records)
    assert "Misconceptions:" in out[0]["concept_details"]
    assert "Basic Proportionality Theorem" in out[0]["concept_details"]
    assert "Misconceptions:" not in out[1]["concept_details"]


def test_culmination_description_becomes_recap():
    records = [
        _rec("Solve A", "Description: a // Types: Type 01: P Case 01: c1 // Misconception: m"),
        _rec("Culmination - Topic 01",
             "Description: long synthesis of everything // "
             "Types: Type 01: Mixed Case 01: combine // Misconception: keep me"),
    ]
    out = cr.refine_chapter(records)
    culm = out[1]["concept_details"]
    # Description collapses to exactly "Recap".
    assert "Description: Recap" in culm
    assert "long synthesis" not in culm
    # Types (Miscellaneous sequence) and Misconception are preserved.
    assert "Miscellaneous Type 01: Mixed" in culm
    assert "Misconceptions: keep me" in culm
    # Regular concept keeps its continuous Type numbering.
    assert "Type 01: P" in out[0]["concept_details"]


def test_culmination_recap_when_no_description_section():
    records = [_rec("Culmination - T",
                    "Types: Type 01: Mix Case 01: q // Misconception: m")]
    out = cr.set_culmination_recap([dict(r) for r in records])
    assert out[0]["concept_details"].startswith("Description: Recap")


def test_activity_info_hub_section_order_and_append():
    details = (
        "Description: Ohm's law relates V, I and R.\n"
        "Achieving Mastery: Applying V = IR.\n"
        " // Types: Type 01: Ohm's law Case 01: Direct V/I questions "
        "Example: Find R when V is 220 V and I is 0.5 A. "
        "// Misconceptions: Students confuse R and resistivity."
    )
    with_hub = cr.append_activity_hub(
        details,
        "Activity: Activity 11.1. Set up the circuit and vary cells.",
    )
    sections = cr.split_sections(with_hub)
    labels = [label for label, _ in sections]
    assert labels == [
        "Description", "Activity/Info Hub", "Types", "Misconceptions",
    ]
    normalized = cr.normalize_misconception_sections(with_hub)
    labels2 = [label for label, _ in cr.split_sections(normalized)]
    assert labels2 == [
        "Description", "Activity/Info Hub", "Types", "Misconceptions",
    ]
    assert "Activity 11.1" in cr.activity_hub_body(normalized)

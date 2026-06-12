"""Concept-output cleaners, exercised with the team's real reported strings."""
from app.services import build_concepts, concept_cleanup, directory
from app.services.concept_cleanup import (
    clean_concept_name,
    description_length_report,
    detect_repeated_leading_phrase,
    strip_dangling_references,
)


# --------------------------- & collapse (Input 02a) --------------------------- #

def test_multiple_ampersands_collapse_to_comma_and_and():
    name = "Culmination – Definition & Importance & Monsoon Dependence"
    assert clean_concept_name(name) == (
        "Culmination – Definition, Importance and Monsoon Dependence"
    )


def test_single_ampersand_is_preserved():
    assert clean_concept_name("History & Civics") == "History & Civics"


def test_no_ampersand_unchanged():
    assert clean_concept_name("Structure and Function of Cells") == (
        "Structure and Function of Cells"
    )


# ----------------------- reference stripping (Input 01/02b) ------------------- #

def test_parenthetical_example_reference_removed_content_kept():
    text = (
        "give computational steps: compute four side lengths and two diagonal "
        "lengths, compare; worked example: show points form but not square (Example 19)."
    )
    out = strip_dangling_references(text)
    assert "Example 19" not in out
    assert "worked example:" in out  # real content preserved
    assert out.endswith("not square.")


def test_examples_type_roman_reference_removed():
    text = (
        "worked example: show four points form a parallelogram by equating "
        "midpoints (Examples Type III)."
    )
    out = strip_dangling_references(text)
    assert "Examples Type III" not in out
    assert "equating midpoints." in out


def test_inline_figure_table_example_references_removed():
    text = "Refer table no. 1 and Figure 1,2 and Example 2 or ex 1 for details"
    out = strip_dangling_references(text)
    for token in ("table no. 1", "Figure 1,2", "Example 2", "ex 1"):
        assert token.lower() not in out.lower()
    assert "  " not in out  # tidied whitespace
    # Stranded connectors are cleaned too — only real content remains.
    assert out == "for details"


def test_worded_example_without_number_is_not_stripped():
    text = "Description: a worked example illustrates the parallelogram property."
    assert strip_dangling_references(text) == text


# ------------------------ MMD references (Input 02b) -------------------------- #

def test_mmd_references_replaced_with_chapter_language():
    from app.services.concept_cleanup import replace_mmd_references

    assert replace_mmd_references("Solve the MMD problem on rates.") == (
        "Solve the problem on rates.")
    assert replace_mmd_references("As shown in the MMD, compare values.") == (
        "As shown in the chapter, compare values.")
    assert replace_mmd_references("Reference MMD for the diagram.") == (
        "Reference chapter for the diagram.")


def test_clean_concept_record_applies_mmd_replacement():
    rec = {
        "concept_title": "Reading the MMD table",
        "concept_details": "Description: derived from the MMD problems in this chapter.",
    }
    out = concept_cleanup.clean_concept_record(rec)
    assert "MMD" not in out["concept_title"]
    assert "MMD" not in out["concept_details"]


# --------------------- repetition detector (Input 02c) ------------------------ #

def test_detects_repeated_leading_phrase():
    names = [
        "Structure and Function of Subsistence Farming",
        "Structure and Function of Commercial Farming",
        "Soil Types",
    ]
    rep = detect_repeated_leading_phrase(names)
    assert rep is not None
    assert rep["phrase"].startswith("structure and function")
    assert rep["count"] == 2


def test_no_false_positive_when_unique():
    assert detect_repeated_leading_phrase(["Photosynthesis", "Respiration"]) is None


# ----------------------- length detector (Input 02e) -------------------------- #

def test_length_report_flags_long_section():
    long_desc = "Description: " + " ".join(["word"] * 120) + " // Types: Type 01 short"
    rep = description_length_report(long_desc, max_words_per_section=90)
    assert any(s["section"].startswith("Description") for s in rep["over_budget"])


# ------------------ culmination post-pass (vendored parity) ------------------- #

def test_ensure_culmination_per_topic():
    from app.services.generation import _ensure_culmination_per_topic

    records = [
        {"topic": "T1", "concept_title": "A", "concept_details": "", "keywords": ""},
        {"topic": "T1", "concept_title": "B", "concept_details": "", "keywords": ""},
        # T1 has no culmination -> synthesized; T2's culmination not last -> moved.
        {"topic": "T2", "concept_title": "Culmination - T2 wrap", "concept_details": "", "keywords": ""},
        {"topic": "T2", "concept_title": "C", "concept_details": "", "keywords": ""},
    ]
    out = _ensure_culmination_per_topic(records)
    t1 = [r for r in out if r["topic"] == "T1"]
    t2 = [r for r in out if r["topic"] == "T2"]
    assert t1[-1]["concept_title"].startswith("Culmination")
    assert len(t1) == 3  # A, B + synthesized culmination
    assert t2[-1]["concept_title"] == "Culmination - T2 wrap"
    assert [r["concept_title"] for r in t2] == ["C", "Culmination - T2 wrap"]


# ----------------------- end-to-end wiring (build_concepts) ------------------- #

def test_add_concept_cleans_name_and_description(db, first_chapter):
    """A messy record persisted via build_concepts must be stored cleaned."""
    detail = directory.chapter_detail(db, first_chapter["id"])
    topic_id = detail["topics"][0]["id"]
    import app.models as models
    topic = db.get(models.Topic, topic_id)

    messy = {
        "concept_title": "Culmination – Definition & Importance & Monsoon Dependence",
        "concept_details": "Description: compute and compare (Example 19).",
        "keywords": "x",
    }
    concept = build_concepts._add_concept(db, topic, messy)
    db.flush()
    assert "&" not in concept.concept_title
    assert "and Monsoon Dependence" in concept.concept_title
    assert "Example 19" not in concept.concept_details

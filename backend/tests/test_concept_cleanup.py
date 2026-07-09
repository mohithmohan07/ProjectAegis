"""Concept-output cleaners, exercised with the team's real reported strings."""
from app.services import build_concepts, concept_cleanup, directory
from app.services.concept_cleanup import (
    clean_concept_name,
    clean_concept_record,
    description_length_report,
    detect_repeated_leading_phrase,
    strip_dangling_references,
    to_title_case,
)


# ------------------------------ Title Case ------------------------------------ #

def test_to_title_case_basics():
    assert to_title_case("the structure of human heart") == "The Structure of Human Heart"
    assert to_title_case("causes and effects of pollution") == "Causes and Effects of Pollution"


def test_to_title_case_preserves_acronyms_units_and_numbers():
    # Internal capitals / digits are left untouched.
    assert to_title_case("pH of common solutions") == "pH of Common Solutions"
    assert to_title_case("NaCl and H2O reactions") == "NaCl and H2O Reactions"
    assert to_title_case("solving 2x equations") == "Solving 2x Equations"


def test_clean_record_titlecases_topic_and_concept():
    rec = clean_concept_record({
        "topic": "operations on rational numbers",
        "concept_title": "addition of rational numbers",
        "concept_details": "Description: d // Misconception: m",
    })
    assert rec["topic"] == "Operations on Rational Numbers"
    assert rec["concept_title"] == "Addition of Rational Numbers"


def test_clean_record_preserves_numeric_types_section():
    rec = clean_concept_record({
        "concept_title": "evaluating exponents",
        "concept_details": (
            "Description: see Example 19 // Types: Type 01: Eval Case 01: 2^3 "
            "// Misconception: m"
        ),
    })
    # Dangling ref removed from Description, but Types/Case labels preserved.
    assert "Example 19" not in rec["concept_details"]
    assert "Type 01: Eval Case 01: 2^3" in rec["concept_details"]


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


def test_compact_fig_refs_without_space_are_stripped_and_neutralized():
    from app.services.concept_cleanup import neutralize_source_artifacts

    assert "fig.11" not in strip_dangling_references("Use fig.11.1 to find R.").lower()
    assert "fig.11" not in neutralize_source_artifacts("Use fig.11.1 to find R.").lower()
    assert neutralize_source_artifacts("Use fig.11.1 to find R.") == (
        "Use the figure to find R."
    )


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

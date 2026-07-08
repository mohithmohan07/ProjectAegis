"""Regression tests for QA review feedback (Reviews 01–06)."""
from app import models
from app import bulk_import as bi
from app.services import (
    build_concepts,
    concept_cleanup,
    concept_refiner as cr,
    concept_validator,
)
from app.services import directory, generation as g


def test_misconception_dedup_keeps_one_section():
    details = (
        "Description: Layers are studied indirectly.\n"
        "Achieving Mastery: Explaining indirect evidence. // "
        "Misconception: Students confuse crust and mantle. // "
        "Misconceptions: Students confuse crust and mantle."
    )
    out = cr.normalize_misconception_sections(details)
    assert out.count("Misconception") == 1
    assert "Achieving Mastery:" in out
    assert "Students confuse crust and mantle." in out


def test_misconception_strips_inline_after_mastery():
    details = (
        "Description: A concept body.\n"
        "Achieving Mastery: Doing it well. // Misconception: A common error."
    )
    out = cr.normalize_misconception_sections(details)
    assert "// Misconception:" not in out.split("Misconceptions:")[0]
    assert "Misconceptions: A common error." in out


def test_misconception_prefers_specific_over_generic_duplicate():
    details = (
        "Description: BPT applies only under a parallel-line condition.\n"
        "Achieving Mastery: Checking the parallel condition before using BPT. // "
        "Misconceptions: Students may ignore the parallel-line condition. // "
        "Misconception: Students may apply Basic Proportionality Theorem as a "
        "memorized rule without checking the conditions, context, or "
        "representation given in the problem."
    )
    out = cr.normalize_misconception_sections(details)
    assert out.count("Misconception") == 1
    assert "ignore the parallel-line condition" in out
    assert "memorized rule" not in out


def test_split_merged_description_blocks():
    merged = (
        "Description: First concept body. // Types: Type 01: Direct Case 01: q1. "
        "Description: Second concept wrongly merged. // Misconceptions: oops."
    )
    out = cr.split_merged_description_blocks(merged)
    assert "Second concept" not in out


def test_dedupe_similar_titles_drops_bpt_echo():
    records = [
        {"topic": "Similarity", "concept_title": "Basic Proportionality Theorem",
         "concept_details": "Description: a", "keywords": ""},
        {"topic": "Criteria", "concept_title": "The Basic Proportionality Theorem",
         "concept_details": "Description: b", "keywords": ""},
    ]
    out = concept_cleanup.dedupe_similar_titles_chapter_wide(records)
    assert len(out) == 1


def test_dedupe_similar_titles_handles_bpt_abbreviation():
    records = [
        {"topic": "Similarity", "concept_title": "Basic Proportionality Theorem",
         "concept_details": "Description: a", "keywords": ""},
        {"topic": "Criteria", "concept_title": "BPT",
         "concept_details": "Description: b", "keywords": ""},
        {"topic": "Criteria", "concept_title": "Converse Basic Proportionality Theorem",
         "concept_details": "Description: c", "keywords": ""},
        {"topic": "Practice", "concept_title": "CBPT",
         "concept_details": "Description: d", "keywords": ""},
    ]
    out = concept_cleanup.dedupe_similar_titles_chapter_wide(records)
    assert [r["concept_title"] for r in out] == [
        "Basic Proportionality Theorem",
        "Converse Basic Proportionality Theorem",
    ]


def test_filter_drops_english_pedagogy_concepts():
    records = [
        {"topic": "A Letter to God", "concept_title": "Lencho's Faith",
         "concept_details": "Description: a", "keywords": ""},
        {"topic": "A Letter to God",
         "concept_title": "Pre-reading Prediction and Discussion",
         "concept_details": "Description: b", "keywords": ""},
    ]
    out = concept_cleanup.filter_review_violations(
        records, subject="English", board="CBSE")
    assert len(out) == 1
    assert out[0]["concept_title"] == "Lencho's Faith"


def test_english_activity_topic_uses_chapter_title():
    records = [
        {"topic": "January 2006", "concept_title": "Lencho's Faith",
         "concept_details": "Description: a", "keywords": ""},
    ]
    out = concept_cleanup.filter_review_violations(
        records, subject="English", board="CBSE", chapter_title="A Letter to God")
    assert out[0]["topic"] == "A Letter to God"


def test_overview_topic_reassigned():
    records = [
        {"topic": "Real Section", "concept_title": "A",
         "concept_details": "Description: a", "keywords": ""},
        {"topic": "Overview", "concept_title": "B",
         "concept_details": "Description: b", "keywords": ""},
    ]
    out = concept_cleanup.filter_review_violations(records, subject="Civics", board="CBSE")
    assert out[1]["topic"] == "Real Section"


def test_power_sharing_forms_rows_get_their_own_topic():
    records = [
        {"topic": "Outcomes of Democracy", "concept_title": "Horizontal Distribution of Power",
         "concept_details": "Description: Power is shared among legislature, executive and judiciary.",
         "keywords": ""},
        {"topic": "Outcomes of Democracy", "concept_title": "Respect for Diversity",
         "concept_details": "Description: Democratic outcomes include accommodation.",
         "keywords": ""},
    ]
    out = concept_cleanup.filter_review_violations(
        records, subject="Civics", board="CBSE", chapter_title="Power Sharing")
    assert out[0]["topic"] == "Forms of Power-sharing"
    assert out[1]["topic"] == "Outcomes of Democracy"


def test_fullmarks_book_tag():
    assert directory.book_tag("Fullmarks") == "Fullmarks"
    tag = directory.chapter_tag("CBSE", "09", "Geography", book="Fullmarks")
    assert tag == "09_Social_Science_CBSE_Fullmarks"


def test_cbse_english_uses_el_code():
    assert directory.code_prefix("CBSE", "10", "English") == "10CBEL"
    assert directory.make_chapter_code(
        "CBSE", "10", "English", "A Letter to God").startswith("10CBEL_")


def test_chapter_meta_respects_finalized_duration():
    meta = g._metadata(subject="History", finalized_duration_minutes=270)
    out = g.chapter_meta_via_api(
        meta=meta,
        topics=[{"topic": "Intro", "concepts": ["A"]}],
        live=False,
    )
    # dry path returns {} — verify finalized is carried in meta for live callers
    assert meta["finalized_duration_minutes"] == 270


def test_parse_duration_minutes():
    assert build_concepts._parse_duration_minutes("270 minutes") == 270
    assert build_concepts._parse_duration_minutes("160 minutes") == 160


def test_topic_display_name_is_clean_when_topic_is_created_or_reused(db):
    chapter = models.Chapter(
        chapter_code="10CBMA_Triangles",
        board="CBSE",
        grade="10",
        subject="Mathematics",
        chapter_title="Triangles",
    )
    db.add(chapter)
    db.flush()
    topic = build_concepts._find_or_create_topic(
        db,
        chapter,
        "Topic 03: Similarity Criteria (10CBMA_Triangles_PL)",
        "Post",
    )
    assert topic.topic_display_name == "Similarity Criteria"
    topic.topic_display_name = "Topic 03: Similarity Criteria (10CBMA_Triangles_PL)"
    reused = build_concepts._find_or_create_topic(
        db,
        chapter,
        "Topic 03: Similarity Criteria (10CBMA_Triangles_PL)",
        "Post",
    )
    assert reused is topic
    assert reused.topic_display_name == "Similarity Criteria"


def test_strip_title_tag_in_labels():
    assert bi.strip_title_tag("What is Science (09CBSS_Ch_PL_T)") == "What is Science"


def test_short_case_examples_fail_for_full_source_detail():
    rows = [{
        "topic": "Triangles",
        "parent_concept": "Similarity",
        "concept_title": "Basic Proportionality Theorem",
        "concept_details": (
            "Description: Relates parallel lines and proportional segments. // "
            "Types: Type 01: Direct Case 01: q // "
            "Misconceptions: Students may ignore the parallel-line condition."
        ),
        "keywords": "",
    }]
    report = concept_validator.validate_concept_rows(rows, allow_types=True)
    short_case = [e for e in report["errors"] if e["code"] == "short_case_example"]
    assert short_case
    assert short_case[0]["severity"] == "error"


def test_concise_math_case_with_source_expression_is_allowed():
    rows = [{
        "topic": "Laws of Exponents",
        "parent_concept": "Operations on Powers",
        "concept_title": "Dividing Powers with the Same Base",
        "concept_details": (
            "Description: Dividing powers subtracts exponents for the same non-zero base. // "
            "Types: Type 01: Same-base division Case 01: Simplify p^9 ÷ p^3. // "
            "Misconceptions: Students may subtract bases instead of exponents."
        ),
        "keywords": "",
    }]
    report = concept_validator.validate_concept_rows(rows, allow_types=True)
    assert not any(e["code"] == "short_case_example" for e in report["errors"])


def test_generic_only_misconception_warns_for_review_quality():
    rows = [{
        "topic": "Triangles",
        "parent_concept": "Similarity",
        "concept_title": "Basic Proportionality Theorem",
        "concept_details": (
            "Description: Relates parallel lines and proportional segments. // "
            "Misconceptions: Students may apply Basic Proportionality Theorem "
            "as a memorized rule without checking the conditions, context, or "
            "representation given in the problem."
        ),
        "keywords": "",
    }]
    report = concept_validator.validate_concept_rows(rows, allow_types=True)
    assert any(e["code"] == "generic_misconception" for e in report["errors"])


def test_power_sharing_metadata_names_required_forms_topic():
    meta = g._metadata(subject="Civics", board="CBSE", chapter_title="Power Sharing")
    block = g._metadata_block(meta)
    assert "Forms of Power-sharing" in block
    assert "Do not merge horizontal" in block


# --------------------------------------------------------------------------- #
# V3 review: Type -> Case (defined sub-type) -> Example (full question)
# --------------------------------------------------------------------------- #

def test_mined_type_renders_case_subtypes_with_example_lines():
    body, n = g._mined_type_to_body({
        "type_title": "Questions based on computing resistance",
        "type_description": "Given electrical readings, compute resistance "
                            "using Ohm's law.",
        "case_prompts": [
            {
                "case_title": "Ohm's law formula-based question when V and I "
                              "are given (without circuit)",
                "examples": [
                    {"example_prompt": "Calculate the resistance of the circuit "
                                       "if V is 220 V and I is 0.5 mA."},
                ],
            },
            {
                "case_title": "Ohm's law formula-based question when the "
                              "circuit diagram is given",
                "examples": [
                    {"example_prompt": "Calculate the resistance for the given "
                                       "circuit. (Refer fig. 11.1) "
                                       "![](https://cdn.mathpix.com/f11.jpg)"},
                ],
            },
        ],
    }, 0)
    assert n == 1
    assert "Case 01: Ohm's law formula-based question when V and I are given" in body
    assert "Example: Calculate the resistance of the circuit if V is 220 V" in body
    assert "Case 02: Ohm's law formula-based question when the circuit diagram" in body
    assert "(Refer fig. 11.1) ![](https://cdn.mathpix.com/f11.jpg)" in body


def test_inventory_task_text_prefers_raw_task_and_ships_images():
    item = {
        "raw_task": "Calculate the resistance for the given circuit. (Refer fig. 11.1)",
        "normalized_task": "Compute resistance.",
        "image_urls": ["https://cdn.mathpix.com/f11.jpg"],
    }
    text = g._inventory_task_text(item)
    assert text.startswith("Calculate the resistance for the given circuit.")
    assert "![](https://cdn.mathpix.com/f11.jpg)" in text


def test_validator_allows_figure_reference_with_embedded_image():
    details = (
        "Description: Ohm's law relates V, I and R. // "
        "Types: Type 01: Computing resistance Case 01: Circuit diagram given "
        "Example: Calculate the resistance for the given circuit. "
        "(Refer fig. 11.1) ![](https://cdn.mathpix.com/f11.jpg) // "
        "Misconceptions: Students may invert the V/I ratio."
    )
    rows = [{"topic": "Electricity", "parent_concept": "Ohm's Law",
             "concept_title": "Resistance", "concept_details": details,
             "keywords": ""}]
    report = concept_validator.validate_concept_rows(rows, allow_types=True)
    assert not any(e["code"] == "source_artifact" for e in report["errors"])
    assert not any(e["code"] == "short_case_example" for e in report["errors"])

    # Without the image URL the bare figure pointer is still an artifact.
    no_image = [{"topic": "Electricity", "parent_concept": "Ohm's Law",
                 "concept_title": "Resistance",
                 "concept_details": details.replace(
                     " ![](https://cdn.mathpix.com/f11.jpg)", ""),
                 "keywords": ""}]
    report2 = concept_validator.validate_concept_rows(no_image, allow_types=True)
    assert any(e["code"] == "source_artifact" for e in report2["errors"])


def test_validator_flags_truncated_example_lines():
    rows = [{
        "topic": "Electricity",
        "parent_concept": "Ohm's Law",
        "concept_title": "Resistance",
        "concept_details": (
            "Description: Ohm's law relates V, I and R. // "
            "Types: Type 01: Computing resistance Case 01: V and I given "
            "Example: q // "
            "Misconceptions: Students may invert the V/I ratio."
        ),
        "keywords": "",
    }]
    report = concept_validator.validate_concept_rows(rows, allow_types=True)
    assert any(e["code"] == "short_case_example" for e in report["errors"])


def test_cleanup_keeps_figure_reference_next_to_embedded_image():
    text = ("Calculate the resistance for the given circuit. (Refer fig. 11.1) "
            "![](https://cdn.mathpix.com/f11.jpg)")
    assert concept_cleanup.strip_dangling_references(text) == text
    assert concept_cleanup.neutralize_source_artifacts(text) == text
    # Without the image, the bare reference is still neutralized.
    bare = "Calculate the resistance for the given circuit shown in fig. 11.1."
    assert "fig. 11.1" not in concept_cleanup.neutralize_source_artifacts(bare)


def test_multiple_specific_misconceptions_are_kept():
    details = (
        "Description: Resistance depends on material and geometry. // "
        "Misconceptions: Students may think doubling length halves resistance. // "
        "Misconception: Students may confuse resistance with resistivity."
    )
    out = cr.normalize_misconception_sections(details)
    assert out.count("Misconceptions:") == 1
    assert "doubling length halves resistance" in out
    assert "confuse resistance with resistivity" in out


def test_duplicate_mastery_statements_keep_the_second():
    details = (
        "Description: A concept body.\n"
        "Achieving Mastery: Applying Resistance correctly in new problems. "
        "More explanation. Achieving Mastery: Selecting and rearranging R = V/I "
        "for the given circuit values."
    )
    out = cr.format_mastery_statement(details)
    assert out.count("Achieving Mastery:") == 1
    assert "Selecting and rearranging R = V/I" in out
    assert "Applying Resistance correctly in new problems" not in out


def test_mastery_after_misconceptions_replaces_the_earlier_statement():
    details = (
        "Description: A concept body.\n"
        "Achieving Mastery: Applying the concept to problems. // "
        "Misconceptions: A real learner error. "
        "Achieving Mastery: Explaining resistance from V-I data."
    )
    out = cr.normalize_misconception_sections(details)
    assert out.count("Achieving Mastery:") == 1
    assert "Explaining resistance from V-I data" in out
    assert "Misconceptions: A real learner error." in out


def test_topic_headings_prefer_main_sections_over_subtopics():
    def sec(heading, prefix, level=1):
        return {
            "heading": heading,
            "heading_level": level,
            "heading_numbered": True,
            "heading_number_prefix": prefix,
            "heading_chapter": False,
        }

    sections = [
        sec("1 The French Revolution and the Idea of the Nation", "1"),
        sec("2 The Making of Nationalism in Europe", "2"),
        sec("2.1 The Aristocracy and the New Middle Class", "2.1", level=2),
        sec("2.2 What Did Liberal Nationalism Stand For?", "2.2", level=2),
        sec("3 The Age of Revolutions: 1830-1848", "3"),
        sec("4 The Making of Germany and Italy", "4"),
        sec("4.1 Germany - Can the Army Be the Architect of a Nation?", "4.1", level=2),
        sec("4.2 Italy Unified", "4.2", level=2),
    ]
    headings = g._topic_headings(sections)
    assert "2 The Making of Nationalism in Europe" in headings
    assert "4 The Making of Germany and Italy" in headings
    assert not any("Aristocracy" in h for h in headings)
    assert not any("Italy Unified" in h for h in headings)


def test_inventory_prompt_requires_checkpoints_activities_and_images():
    inventory = g.prompts.get_text("concepts.question_task_inventory.system")
    assert "checkpoint_question" in inventory
    assert "activity" in inventory
    assert "image_urls" in inventory
    assert "cdn.mathpix.com" in inventory
    assert "never truncate" in inventory
    embedding = g.prompts.get_text("concepts.type_embedding.system")
    assert "is_activity" in embedding
    assert "Respect chapter position" in embedding
    assert "heating-effect" in embedding

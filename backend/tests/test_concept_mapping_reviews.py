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


# --------------------------------------------------------------------------- #
# Full-GPT passes: misconceptions, duplicate merge, merged cells, no-loss types
# --------------------------------------------------------------------------- #

def test_misconceptions_via_api_replaces_generic_text(monkeypatch):
    def fake_openai(system, user, **kw):
        assert "Misconceptions" in system
        return {"rows": [{
            "topic": "Triangles", "parent_concept": "Similarity",
            "concept": "Basic Proportionality Theorem",
            "concept_description": (
                "Description: unchanged // Misconceptions: Students may apply "
                "the ratio to non-parallel cutting lines, and may also assume "
                "AD/DB equals AE/EC without checking DE parallel to BC."
            ),
            "keywords": "",
        }]}

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    records = [{
        "topic": "Triangles", "parent_concept": "Similarity",
        "concept_title": "Basic Proportionality Theorem",
        "concept_details": (
            "Description: Relates parallel lines and proportional segments. // "
            "Misconceptions: Students may apply Basic Proportionality Theorem "
            "as a memorized rule without checking the conditions, context, or "
            "representation given in the problem."
        ),
        "keywords": "",
    }]
    out = g._ensure_misconceptions_via_api(records, meta=g._metadata(subject="Math"))
    details = out[0]["concept_details"]
    assert "memorized rule" not in details
    assert "non-parallel cutting lines" in details
    assert "Relates parallel lines and proportional segments." in details


def test_validator_flags_merged_description_blocks():
    rows = [{
        "topic": "T", "parent_concept": "P", "concept_title": "C",
        "concept_details": (
            "Description: First concept body. // Types: Type 01: X Case 01: "
            "Solve the given task with all values shown. "
            "Description: Second concept wrongly merged here."
        ),
        "keywords": "",
    }]
    report = concept_validator.validate_concept_rows(rows, allow_types=True)
    assert any(e["code"] == "merged_description" for e in report["errors"])


def test_find_similar_title_groups_detects_without_dropping():
    records = [
        {"topic": "Similarity", "concept_title": "Basic Proportionality Theorem",
         "concept_details": "Description: a", "keywords": ""},
        {"topic": "Criteria", "concept_title": "BPT",
         "concept_details": "Description: b", "keywords": ""},
        {"topic": "Criteria", "concept_title": "Unrelated Concept",
         "concept_details": "Description: c", "keywords": ""},
    ]
    groups = concept_cleanup.find_similar_title_groups(records)
    assert groups == [[0, 1]]
    assert len(records) == 3  # detector never mutates


def test_merge_similar_concepts_via_api_merges_content(monkeypatch):
    def fake_openai(system, user, **kw):
        assert "merge into ONE row" in user
        return {"rows": [{
            "topic": "Similarity", "parent_concept": "Similarity",
            "concept": "Basic Proportionality Theorem",
            "concept_description": "Description: merged body from both rows.",
            "keywords": "bpt",
        }]}

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    records = [
        {"topic": "Similarity", "parent_concept": "Similarity",
         "concept_title": "Basic Proportionality Theorem",
         "concept_details": "Description: a", "keywords": ""},
        {"topic": "Criteria", "parent_concept": "Criteria",
         "concept_title": "BPT", "concept_details": "Description: b",
         "keywords": ""},
        {"topic": "Criteria", "parent_concept": "Criteria",
         "concept_title": "Unrelated Concept",
         "concept_details": "Description: c", "keywords": ""},
    ]
    out = g._merge_similar_concepts_via_api(records, meta=g._metadata(subject="Math"))
    assert len(out) == 2
    assert out[0]["concept_details"] == "Description: merged body from both rows."
    assert out[1]["concept_title"] == "Unrelated Concept"


def test_unassigned_mined_types_fail_instead_of_guessing(monkeypatch):
    import pytest

    monkeypatch.setattr(g, "_openai_json", lambda *a, **kw: {"assignments": []})
    records = [
        {"topic": "Electricity", "parent_concept": "P",
         "concept_title": "Resistance", "concept_details": "Description: d",
         "keywords": ""},
        {"topic": "Electricity", "parent_concept": "Culmination",
         "concept_title": "Culmination - Electricity",
         "concept_details": "Description: Recap", "keywords": ""},
    ]
    mined = {"types": [{
        "type_id": "TYPE-0001", "type_title": "Activity-based observation",
        "topic_match_hint": "Electricity",
        "case_prompts": [{
            "case_title": "Observe current variation in a test circuit",
            "examples": [{"example_prompt": (
                "Set up the circuit with a nichrome wire and record the "
                "ammeter reading for each cell added.")}],
        }],
    }]}
    with pytest.raises(RuntimeError, match="unassigned mined Types"):
        g._assign_mined_types_via_api(
            records, meta=g._metadata(subject="Physics"), mined_types=mined,
            max_attempts=1)


def test_duplicate_inventory_assignments_are_reported():
    inventory = {"items": [{"qid": "QINV-0001", "raw_task": "Why did tensions emerge?"}]}
    types = {"types": [
        {"type_id": "TYPE-0001", "source_question_ids": ["QINV-0001"],
         "case_prompts": [{"case_title": "Cause question",
                           "examples": [{"source_question_id": "QINV-0001",
                                         "example_prompt": "Why did tensions emerge?"}]}]},
        {"type_id": "TYPE-0002", "source_question_ids": ["QINV-0001"],
         "case_prompts": [{"case_title": "Another cause question",
                           "examples": [{"source_question_id": "QINV-0001",
                                         "example_prompt": "Why did tensions emerge?"}]}]},
    ]}
    dupes = g._duplicate_inventory_assignments(inventory, types["types"])
    assert dupes and dupes[0]["qid"] == "QINV-0001"
    assert dupes[0]["assignment_count"] == 2


def test_type_mining_retries_duplicate_assignments_with_complete_list(monkeypatch):
    calls = {"n": 0}

    def fake_openai(system, user, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"types": [
                {"type_id": "TYPE-0001", "type_title": "Pattern One",
                 "source_question_ids": ["QINV-0001"],
                 "case_prompts": [{"case_title": "Defined case",
                                   "examples": [{"source_question_id": "QINV-0001",
                                                 "example_prompt": "Question one"}]}]},
                {"type_id": "TYPE-0002", "type_title": "Pattern Two",
                 "source_question_ids": ["QINV-0001"],
                 "case_prompts": [{"case_title": "Duplicate case",
                                   "examples": [{"source_question_id": "QINV-0001",
                                                 "example_prompt": "Question one"}]}]},
            ]}
        assert "duplicate_assignments" in user
        assert "COMPLETE corrected" in user
        return {"types": [
            {"type_id": "TYPE-0001", "type_title": "Pattern One",
             "source_question_ids": ["QINV-0001"],
             "case_prompts": [{"case_title": "Defined case",
                               "examples": [{"source_question_id": "QINV-0001",
                                             "example_prompt": "Question one"}]}]},
        ]}

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    inventory = {"items": [{"qid": "QINV-0001", "raw_task": "Question one"}], "stats": {}}
    mined = g._mine_types_from_inventory_via_api(
        meta=g._metadata(subject="History"), inventory=inventory, max_coverage_attempts=2)
    assert calls["n"] == 2
    assert not g._duplicate_inventory_assignments(inventory, mined["types"])


def test_type_alignment_review_preserves_non_type_sections(monkeypatch):
    def fake_openai(system, user, **kw):
        assert "exactly once" in system
        return {"rows": [{
            "topic": "Wrong",
            "parent_concept": "Wrong",
            "concept": "Wrong",
            "concept_description": (
                "Description: changed // Types: Type 01: Correct Pattern "
                "Case 01: Defined sub-type Example: Why did nationalist "
                "tensions emerge in the Balkans? // Misconceptions: changed"
            ),
            "keywords": "wrong",
        }]}

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    rows = [{
        "topic": "Nationalism and Imperialism",
        "parent_concept": "Balkan Nationalism",
        "concept_title": "Late Nineteenth-century Nationalism Became More Aggressive",
        "concept_details": (
            "Description: Original description.\n"
            "Achieving Mastery: Original mastery. // "
            "Misconceptions: Original misconception."
        ),
        "keywords": "nationalism",
    }]
    out = g._review_type_concept_alignment_via_api(
        rows,
        meta=g._metadata(subject="History"),
        question_task_inventory={"items": [{"qid": "QINV-0001", "raw_task": "Why did nationalist tensions emerge in the Balkans?"}]},
        mined_types={"types": []},
        source_context="",
    )
    assert out[0]["topic"] == rows[0]["topic"]
    assert "Original description" in out[0]["concept_details"]
    assert "Correct Pattern" in out[0]["concept_details"]
    assert "changed" not in out[0]["concept_details"].split("Types:", 1)[0]


def test_uploaded_duration_lookup_for_reviewed_chapters():
    assert build_concepts.chapter_durations.lookup_duration_minutes(
        board="CBSE",
        grade="10",
        subject="History",
        chapter_title="The Rise of Nationalism in Europe",
    ) == 343
    assert build_concepts.chapter_durations.lookup_duration_minutes(
        board="CBSE",
        grade="10",
        subject="Physics",
        chapter_title="Electricity",
    ) == 561


def test_neutralize_unrepaired_rows_keeps_clean_rows_verbatim():
    clean = {
        "topic": "Electricity", "parent_concept": "Ohm's Law",
        "concept_title": "Resistance",
        "concept_details": (
            "Description: Ohm's law relates V, I and R. // "
            "Misconceptions: Students may invert the V/I ratio."
        ),
        "keywords": "",
    }
    failing = {
        "topic": "Electricity", "parent_concept": "Ohm's Law",
        "concept_title": "Heating Effect",
        "concept_details": (
            "Description: See Example 11 for the heating computation. // "
            "Misconceptions: Students may confuse power with energy."
        ),
        "keywords": "",
    }
    out = g._neutralize_unrepaired_rows([dict(clean), dict(failing)])
    assert out[0]["concept_details"] == clean["concept_details"]
    assert "Example 11" not in out[1]["concept_details"]


def test_chapter_meta_summary_retries_before_deterministic_fallback(monkeypatch, db):
    calls = {"n": 0}

    def flaky_meta(**kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient API failure")
        return {"chapter_duration_minutes": 270}

    monkeypatch.setattr(build_concepts.generation, "chapter_meta_via_api", flaky_meta)
    chapter = models.Chapter(
        chapter_code="10CBSS_RiseNat", board="CBSE", grade="10",
        subject="History", chapter_title="The Rise of Nationalism in Europe",
    )
    db.add(chapter)
    db.flush()
    meta = build_concepts._chapter_meta_summary(chapter)
    assert calls["n"] == 2
    assert meta["chapter_duration_minutes"] == 270


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


def test_neutralize_compact_fig_refs_without_space():
    """OCR often emits fig.11.1; neutralize must clear it or final validation fails."""
    rec = {
        "topic": "Electric Current And Circuit",
        "parent_concept": "Resistance",
        "concept_title": "What Determines Resistance in a Conductor",
        "concept_details": (
            "Description: Resistance is the opposition a conductor offers to "
            "the flow of electric current. It depends mainly on three factors. "
            "Achieving Mastery: Predicting how R changes with geometry.\n"
            " // Types: Type 01: Relating resistance to geometry "
            "Case 01: Length dependence "
            "Example: Find the new resistance when the wire in fig.11.5 is "
            "doubled in length. "
            "// Misconceptions: Students may think thicker wires have higher "
            "resistance."
        ),
        "keywords": "",
    }
    out = concept_cleanup.clean_concept_record(dict(rec), neutralize_artifacts=True)
    report = concept_validator.validate_concept_rows(
        [out], allow_types=True, require_culmination=False)
    assert not any(e["code"] == "source_artifact" for e in report["errors"])
    assert "fig.11" not in out["concept_details"].lower()


def test_chapter_opening_labelled_in_section_chunks():
    sections = g.parse_mmd_sections(
        "Before any numbered section, Frédéric Sorrieu painted a series of "
        "prints.\n\n"
        "## 1 The French Revolution and the Idea of the Nation\n\n"
        "The first clear expression of nationalism came with the French "
        "Revolution.\n"
    )
    text = g._format_section_chunk(sections)
    assert "HEADING PATH: [Chapter opening]" in text
    assert "Frédéric Sorrieu" in text


def test_v5_prompts_require_opening_granularity_and_mathpix_policy():
    skeleton = g.prompts.get_text("concepts.skeleton.system")
    assert "[Chapter opening]" in skeleton
    assert "German unification and Italian unification" in skeleton
    assert "Frédéric Sorrieu" in skeleton
    canonicalize = g.prompts.get_text("concepts.canonicalize.system")
    assert "Germany vs Italy" in canonicalize
    refine = g.prompts.get_text("concepts.description_refine.system")
    assert "Do NOT embed Mathpix" in refine
    assert "truncated mid-sentence" in refine
    inventory = g.prompts.get_text("concepts.question_task_inventory.system")
    assert "stay ONE inventory item" in inventory
    assert "Missing even one" in inventory and "checkpoint is a defect" in inventory
    assert "Frédéric Sorrieu" in inventory
    embedding = g.prompts.get_text("concepts.type_embedding.system")
    assert "Picture-/source-/map-based" in embedding
    repair = g.prompts.get_text("concepts.repair.system")
    assert "fig.11.1" in repair
    assert "Do not put image URLs in the Description" in repair


def test_cleanup_strips_mathpix_from_description_keeps_types():
    rec = {
        "topic": "The French Revolution and the Idea of the Nation",
        "parent_concept": "The Idea of the Nation",
        "concept_title": "Frédéric Sorrieu's Vision of Democratic Republics",
        "concept_details": (
            "Description: Sorrieu's utopian print series. "
            "![](https://cdn.mathpix.com/cropped/sorrieu.jpg) "
            "Achieving Mastery: Interpreting nationalist allegory.\n"
            " // Types: Type 01: Source interpretation "
            "Case 01: Print analysis "
            "Example: Describe the painting of the peoples of Europe. "
            "(Refer fig. 1) ![](https://cdn.mathpix.com/cropped/sorrieu.jpg) "
            "// Misconceptions: Students may treat the print as literal history."
        ),
        "keywords": "",
    }
    out = concept_cleanup.clean_concept_record(dict(rec), neutralize_artifacts=True)
    desc, rest = out["concept_details"].split("Types:", 1)
    assert "cdn.mathpix.com" not in desc
    assert "cdn.mathpix.com" in rest
    report = concept_validator.validate_concept_rows(
        [out], allow_types=True, require_culmination=False)
    assert not any(e["code"] == "description_image_url" for e in report["errors"])


def test_validator_warns_on_mathpix_in_description():
    rows = [{
        "topic": "T", "parent_concept": "P", "concept_title": "C",
        "concept_details": (
            "Description: A visual concept "
            "![](https://cdn.mathpix.com/cropped/x.jpg) "
            "// Misconceptions: Students may ignore the figure."
        ),
        "keywords": "",
    }]
    report = concept_validator.validate_concept_rows(rows, allow_types=True)
    assert any(e["code"] == "description_image_url" for e in report["errors"])


def test_expected_min_skeleton_rows_is_denser_for_history_scale():
    # ~10k chars should expect more than the old content//3000 floor.
    text = "x" * 10_000
    assert g._expected_min_skeleton_rows(text) >= 4

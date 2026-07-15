"""Regression tests for QA review feedback (Reviews 01–06)."""
import re
from pathlib import Path

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


def test_filter_drops_pedagogy_concepts_without_subject_branch():
    records = [
        {"topic": "A Letter to God", "concept_title": "Lencho's Faith",
         "concept_details": "Description: a", "keywords": ""},
        {"topic": "A Letter to God",
         "concept_title": "Pre-reading Prediction and Discussion",
         "concept_details": "Description: b", "keywords": ""},
    ]
    out = concept_cleanup.filter_review_violations(
        records, subject="Unclassified Upload", board="CBSE")
    assert len(out) == 1
    assert out[0]["concept_title"] == "Lencho's Faith"


def test_pedagogy_topic_uses_chapter_title_without_subject_branch():
    records = [
        {"topic": "Classroom Activity", "concept_title": "Lencho's Faith",
         "concept_details": "Description: a", "keywords": ""},
    ]
    out = concept_cleanup.filter_review_violations(
        records, subject="", board="CBSE", chapter_title="A Letter to God")
    assert out[0]["topic"] == "A Letter to God"


def test_overview_topic_is_dropped_not_reassigned():
    """Overview/Summary rows are omitted entirely — never pushed next door."""
    records = [
        {"topic": "Real Section", "concept_title": "A",
         "concept_details": "Description: a", "keywords": ""},
        {"topic": "Overview", "concept_title": "B",
         "concept_details": "Description: b", "keywords": ""},
        {"topic": "Summary", "concept_title": "C",
         "concept_details": "Description: c", "keywords": ""},
    ]
    out = concept_cleanup.filter_review_violations(records, subject="Civics", board="CBSE")
    assert [r["concept_title"] for r in out] == ["A"]


def test_overview_and_summary_content_is_omitted_not_merged():
    """Filler Overview/Summary bodies must not be attached to neighboring topics."""
    assert g._is_non_topic_heading("Overview")
    assert g._is_filler_source_topic("Overview")
    assert g._is_filler_source_topic("Summary")
    sections = [
        {
            "heading": "Overview",
            "heading_level": 2,
            "heading_numbered": False,
            "heading_number_prefix": "",
            "heading_chapter": False,
            "body": "UNIQUE_OVERVIEW_PREVIEW about power sharing.",
            "exercise_blocks": [],
        },
        {
            "heading": "1 Belgium and Sri Lanka",
            "heading_level": 2,
            "heading_numbered": True,
            "heading_number_prefix": "1",
            "heading_chapter": False,
            "body": "Belgium and Sri Lanka illustrate power sharing.",
            "exercise_blocks": [],
        },
        {
            "heading": "2 Why Power Sharing is Desirable",
            "heading_level": 2,
            "heading_numbered": True,
            "heading_number_prefix": "2",
            "heading_chapter": False,
            "body": "Power sharing reduces conflict.",
            "exercise_blocks": [],
        },
        {
            "heading": "3 Forms of Power Sharing",
            "heading_level": 2,
            "heading_numbered": True,
            "heading_number_prefix": "3",
            "heading_chapter": False,
            "body": "Power is shared horizontally and vertically.",
            "exercise_blocks": [],
        },
        {
            "heading": "Summary",
            "heading_level": 2,
            "heading_numbered": False,
            "heading_number_prefix": "",
            "heading_chapter": False,
            "body": "UNIQUE_SUMMARY_RECAP of the chapter.",
            "exercise_blocks": [],
        },
    ]
    headings = g._topic_headings(sections)
    assert "Overview" not in headings
    assert "Summary" not in headings
    paired = g._sections_with_source_topics(sections)
    assert not any(
        g._is_filler_source_topic(section.get("heading") or "")
        for _, section in paired
    )
    excerpts = g._group_source_topic_excerpts(sections)
    joined = " ".join(group["excerpt"] for group in excerpts)
    assert "UNIQUE_OVERVIEW_PREVIEW" not in joined
    assert "UNIQUE_SUMMARY_RECAP" not in joined
    assert not any(
        g._topic_comparison_key(group["topic"]) in {"overview", "summary"}
        for group in excerpts
    )
    records = [
        {
            "topic": "Belgium and Sri Lanka",
            "parent_concept": "Cases",
            "concept_title": "Belgian Accommodation",
            "concept_details": "Description: Belgium shares power.",
            "keywords": "",
        },
        {
            "topic": "Why Power Sharing is Desirable",
            "parent_concept": "Rationale",
            "concept_title": "Prudential Reasons for Power Sharing",
            "concept_details": "Description: Power sharing reduces conflict.",
            "keywords": "",
        },
        {
            "topic": "Forms of Power Sharing",
            "parent_concept": "Forms",
            "concept_title": "Horizontal Power Sharing",
            "concept_details": "Description: Organs of government share power.",
            "keywords": "",
        },
        {
            "topic": "Overview",
            "parent_concept": "Preview",
            "concept_title": "Should Be Dropped",
            "concept_details": "Description: preview only.",
            "keywords": "",
        },
    ]
    assert g._missing_source_topic_excerpts(records, excerpts) == []
    scrubbed = g._scrub_section_numbers([dict(r) for r in records])
    assert [r["concept_title"] for r in scrubbed] == [
        "Belgian Accommodation",
        "Prudential Reasons for Power Sharing",
        "Horizontal Power Sharing",
    ]


def test_cleanup_does_not_invent_subject_specific_topics():
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
    assert out[0]["topic"] == "Outcomes of Democracy"
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


def test_correction_shaped_misconception_is_rejected_as_review_input():
    correction = (
        "A nation is not simply a territory, dynasty, ethnic group, or people "
        "sharing a common language."
    )
    assert cr._is_correction_shaped_misconception(correction)
    assert not cr._is_correction_shaped_misconception(
        "Students may believe that a nation has always existed with a fixed identity."
    )
    rows = [{
        "topic": "Nation States",
        "parent_concept": "National Identity",
        "concept_title": "Historically Constructed National Identity",
        "concept_details": (
            "Description: National identity changes through historical processes. // "
            f"Misconceptions: {correction}"
        ),
        "keywords": "",
    }]
    report = concept_validator.validate_concept_rows(rows, allow_types=True)
    assert any(
        e["code"] == "misconception_framing" for e in report["errors"])


def test_metadata_has_no_subject_specific_prompt_supplements():
    meta = g._metadata(subject="Civics", board="CBSE", chapter_title="Power Sharing")
    block = g._metadata_block(meta)
    assert "Forms of Power-sharing" not in block
    assert "Do not merge horizontal" not in block
    english = g._metadata_block(g._metadata(
        subject="English", chapter_title="A Letter to God"))
    assert "ENGLISH LITERATURE RULES" not in english


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
    assert "![Fig. 11.1](https://cdn.mathpix.com/f11.jpg)" in text


def test_inventory_visual_without_pdf_figure_number_gets_descriptive_alt():
    text = g._inventory_task_text({
        "source_kind": "diagram_task",
        "source_label": "Circuit comparison",
        "raw_task": "Compare the two circuits.",
        "image_urls": ["https://cdn.mathpix.com/circuits.jpg"],
    })
    assert "![Circuit comparison](https://cdn.mathpix.com/circuits.jpg)" in text


def test_latex_figure_uses_adjacent_source_caption_in_public_markdown():
    url = "https://cdn.mathpix.com/cropped/source.jpg?height=800&width=600"
    raw = (
        "Interpret the print. "
        "\\begin{figure}\n"
        f"\\includegraphics[alt={{}},max width=\\textwidth]{{{url}}}\n"
        "\\captionsetup{labelformat=empty}\n"
        "\\caption{Fig． 1 - A democratic republic print prepared in 1848.}\n"
        "\\end{figure}"
    )
    text = g._inventory_task_text({
        "source_kind": "source_task",
        "source_label": "Opening print",
        "raw_task": raw,
        "image_urls": [],
    })
    assert "\\includegraphics" not in text
    assert (
        f"![Fig． 1 - A democratic republic print prepared in 1848]({url})"
        in text
    )


def test_public_inventory_examples_remove_textbook_section_numbers():
    text = g._inventory_task_text({
        "source_kind": "exercise",
        "raw_task": (
            "Use the sequence introduced in Section 5.1 to find its twentieth term."
        ),
        "image_urls": [],
    })
    assert "Section 5.1" not in text
    assert "earlier chapter discussion" in text


def test_uploaded_nationalism_fixture_recovers_all_checkpoint_containers():
    source = (
        Path(__file__).parents[1] / "data" / "RNE.mmd"
    ).read_text(encoding="utf-8")
    sections = [
        section
        for chunk in g._section_aware_chunks(source)
        for section in chunk["sections"]
    ]
    anchors = g._source_task_anchors(sections)
    checkpoints = [
        item for item in anchors
        if item["source_kind"] == "checkpoint_question"
    ]
    assert len(checkpoints) == 14
    assert sum(bool(item.get("_activity_origin")) for item in checkpoints) == 8
    assert len(g._hub_inventory_items({"items": anchors})) == 10
    assert not any(
        "Do we require any further proof" in item["raw_task"]
        or "Is it not a disgrace" in item["raw_task"]
        for item in checkpoints
    )
    italy_map_activity = next(
        item for item in checkpoints
        if "Look at Fig. 14(a)" in item["raw_task"]
    )
    assert "was not the result of a sudden upheaval" not in (
        italy_map_activity["raw_task"])


def test_repeated_generic_checkpoint_labels_preserve_distinct_tasks():
    items = [{
        "source_kind": "checkpoint_question",
        "source_label": "Discuss",
        "raw_task": "Explain how language contributed to national identity.",
    }]
    anchors = [
        {
            "source_kind": "checkpoint_question",
            "source_label": "Discuss",
            "raw_task": "Explain how language contributed to national identity.",
        },
        {
            "source_kind": "checkpoint_question",
            "source_label": "Discuss",
            "raw_task": "Compare the political meanings of two allegories.",
        },
    ]
    merged = g._merge_source_task_anchors(items, anchors)
    assert [item["raw_task"] for item in merged] == [
        "Explain how language contributed to national identity.",
        "Compare the political meanings of two allegories.",
    ]


def test_uploaded_nationalism_fixture_exposes_sorrieu_opening_for_recovery():
    source = (
        Path(__file__).parents[1] / "data" / "RNE.mmd"
    ).read_text(encoding="utf-8")
    sections = [
        section
        for chunk in g._section_aware_chunks(source)
        for section in chunk["sections"]
    ]
    opening = g._chapter_opening_excerpt(sections, g._topic_headings(sections))
    assert opening is not None
    assert opening["topic"] == "The French Revolution and the Idea of the Nation"
    assert "Frédéric Sorrieu" in opening["excerpt"]


def test_uploaded_ap_fixture_keeps_parent_questions_and_own_mcq_options():
    source = (
        Path(__file__).parents[1] / "data" / "jemh105 (1).mmd"
    ).read_text(encoding="utf-8")
    sections = [
        section
        for chunk in g._section_aware_chunks(source)
        for section in chunk["sections"]
    ]
    anchors = g._source_task_anchors(sections)
    exercise_anchors = [
        item for item in anchors if item["source_kind"] == "exercise"
    ]
    assert len(exercise_anchors) == 49
    assert len({
        item["source_label"] for item in exercise_anchors
    }) == len(exercise_anchors)
    mcq = next(
        item for item in exercise_anchors
        if item["source_label"] == "EXERCISE 5.2 Q2"
    )
    assert "30 th term" in mcq["raw_task"]
    assert "(A) 97 (B) 77 (C) -77 (D) -87" in mcq["raw_task"]
    assert "11th term" in mcq["raw_task"]
    assert "(A) 28 (B) 22 (C) -38" in mcq["raw_task"]


def test_authoritative_parent_question_replaces_gpt_split_subparts():
    full = (
        "Choose the correct choice and justify: "
        "(i) Find the 30th term. (A) 97 (B) 77 "
        "(ii) Find the 11th term. (A) 28 (B) 22"
    )
    anchors = [{
        "source_kind": "exercise",
        "source_label": "EXERCISE 5.2 Q2",
        "parent_source_label": "EXERCISE 5.2",
        "raw_task": full,
        "normalized_task": full,
    }]
    items = [
        {
            "source_kind": "mcq",
            "source_label": "Exercise 5.2 Question 2(i)",
            "parent_source_label": "Exercise 5.2 Question 2",
            "subpart_label": "(i)",
            "raw_task": "Find the 30th term. (A) 97 (B) 77",
        },
        {
            "source_kind": "mcq",
            "source_label": "Exercise 5.2 Question 2(ii)",
            "parent_source_label": "Exercise 5.2 Question 2",
            "subpart_label": "(ii)",
            "raw_task": "Find the 11th term. (A) 28 (B) 22",
        },
    ]
    assert g._merge_source_task_anchors(items, anchors) == anchors


def test_unique_question_label_root_merges_question_and_q_notation():
    anchor = {
        "source_kind": "exercise",
        "source_label": "EXERCISE 5.3 Q4",
        "parent_source_label": "EXERCISE 5.3",
        "raw_task": "How many terms of the AP 9, 17, 25, ... give a sum of 636?",
    }
    gpt_item = {
        "source_kind": "exercise",
        "source_label": "Exercise 5.3 Question 4",
        "raw_task": "How many terms give a sum of 636?",
    }
    merged = g._merge_source_task_anchors([gpt_item], [anchor])
    assert len(merged) == 1
    assert merged[0]["source_label"] == anchor["source_label"]
    assert merged[0]["raw_task"] == anchor["raw_task"]
    assert g._inventory_question_label_root(
        "EXERCISE 5.4 (Optional)* Q2"
    ) == g._inventory_question_label_root("Exercise 5.4 Question 2")


def test_uploaded_electricity_activities_feed_types_and_hubs_with_visuals():
    source = (
        Path(__file__).parents[1] / "data" / "Class 10 Chapter 5 Electricity.mmd"
    ).read_text(encoding="utf-8")
    sections = [
        section
        for chunk in g._section_aware_chunks(source)
        for section in chunk["sections"]
    ]
    anchors = g._source_task_anchors(sections)
    activities = [
        item for item in anchors if item.get("_activity_origin")
    ]
    assert len(activities) == 6
    assert all(
        item["source_kind"] == "checkpoint_question" for item in activities)
    assert len(g._hub_inventory_items({"items": anchors})) == 6
    rendered_visuals = [
        g._inventory_task_text(item)
        for item in activities if item.get("image_urls")
    ]
    assert rendered_visuals
    assert all("\\includegraphics" not in text for text in rendered_visuals)
    assert all(re.search(r"!\[[^\]]+\]\(https://", text)
               for text in rendered_visuals)


def test_assessable_activity_can_appear_once_in_types_and_in_hub():
    prompt = "Describe the observed current and explain why it changes."
    inventory = {"items": [{
        "qid": "QINV-0001",
        "source_kind": "checkpoint_question",
        "_activity_origin": True,
        "raw_task": prompt,
    }]}
    rows = [{
        "topic": "Current",
        "concept_title": "Current in Conductors",
        "concept_details": (
            "Description: Current is measured in a closed circuit. // "
            "Activity/Info Hub: Observe current while changing the conductor. // "
            "Types: Type 01: Interpreting observations "
            f"Case 01: Current changes Example: {prompt}"
        ),
    }]
    assert g._rendered_inventory_coverage_defects(rows, inventory) == {
        "missing": [],
        "duplicate": [],
    }
    assert g._hub_inventory_examples_in_types(rows, inventory) == set()


def test_assessable_activity_coverage_repair_reuses_its_gpt_hub_concept():
    prompt = "Measure current for each conductor and explain the differences."
    item = {
        "source_kind": "checkpoint_question",
        "_activity_origin": True,
        "topic_hint": "Resistance",
        "raw_task": prompt,
    }
    rows = [
        {
            "topic": "Resistance",
            "concept_title": "Material Resistivity",
            "concept_details": "Description: Material affects resistance.",
        },
        {
            "topic": "Resistance",
            "concept_title": "Comparing Component Resistance",
            "concept_details": (
                "Description: Components oppose current differently. // "
                f"Activity/Info Hub: Activity: {prompt}"
            ),
        },
    ]
    assert g._best_record_index_for_inventory_item(rows, item) == 1


def test_opening_recovery_adds_only_model_identified_missing_rows(monkeypatch):
    sections = [
        {
            "heading": "",
            "body": (
                "A distinctive artist prepared a series of prints showing a "
                "democratic world of nation-states. The visual teaches liberty, "
                "fraternity, and national identity through a long procession. "
                "This substantive opening framing precedes the first main topic."
            ),
        },
        {
            "heading": "1 First Main Topic",
            "body": "The first numbered topic begins here.",
        },
    ]
    rows = [{
        "topic": "First Main Topic",
        "parent_concept": "Existing",
        "concept_title": "Existing Main Idea",
        "concept_details": "Description: Existing teaching content.",
        "keywords": "existing",
    }]

    def fake_openai(system, user):
        assert "chapter-opening material" in system
        assert "distinctive artist" in user
        return {"missing_rows": [{
            "parent_concept": "Opening Visual",
            "concept": "Democratic World in the Opening Print",
            "concept_description": (
                "Description: The opening print presents national liberty and "
                "fraternity through a procession of peoples."
            ),
            "keywords": ["liberty", "fraternity", "nation"],
        }]}

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    out = g._recover_chapter_opening_concepts_via_api(
        rows,
        meta={},
        sections=sections,
        headings=["1 First Main Topic"],
    )
    assert [row["concept_title"] for row in out] == [
        "Democratic World in the Opening Print",
        "Existing Main Idea",
    ]
    assert all(row["topic"] == "First Main Topic" for row in out)


def test_description_section_references_are_removed_without_touching_math():
    row = {
        "topic": "Sequences",
        "parent_concept": "Terms",
        "concept_title": "Nth Term",
        "concept_details": (
            "Description: Section 5.3 introduces a_n=a+(n-1)d and 1.25 as a "
            "decimal value. // Misconceptions: Students may use n instead of n-1."
        ),
        "keywords": "",
    }
    cleaned = concept_cleanup.clean_concept_record(dict(row))
    assert "Section 5.3" not in cleaned["concept_details"]
    assert "1.25" in cleaned["concept_details"]


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


def test_activity_types_go_to_info_hub_not_culmination_types(monkeypatch):
    """is_activity mined Types land in Activity/Info Hub on a normal concept."""
    monkeypatch.setattr(
        g, "_openai_json",
        lambda *a, **kw: {
            "assignments": [{
                "concept_id": "CONCEPT-0001",
                "type_ids": ["TYPE-0001"],
            }],
        },
    )
    records = [
        {
            "topic": "Electricity",
            "parent_concept": "Current",
            "concept_title": "Ohm's Law",
            "concept_details": (
                "Description: V = IR.\nAchieving Mastery: Applying Ohm's law. "
                "// Misconceptions: Students confuse R and resistivity."
            ),
            "keywords": "",
        },
        {
            "topic": "Electricity",
            "parent_concept": "Culmination",
            "concept_title": "Culmination - Electricity",
            "concept_details": "Description: Recap",
            "keywords": "",
        },
    ]
    mined = {"types": [{
        "type_id": "TYPE-0001",
        "type_title": "Ohm's law experiment",
        "topic_match_hint": "Electricity",
        "is_activity": True,
        "case_prompts": [{
            "case_title": "Activity 11.1",
            "examples": [{"example_prompt": (
                "Set up the circuit with a nichrome wire and record the "
                "ammeter reading for each cell added.")}],
        }],
    }]}
    out = g._assign_mined_types_via_api(
        records, meta=g._metadata(subject="Physics"), mined_types=mined,
        max_attempts=1)
    ohms = next(r for r in out if r["concept_title"] == "Ohm's Law")
    culm = next(r for r in out if cr.is_culmination(r["concept_title"]))
    hub = cr.activity_hub_body(ohms["concept_details"])
    assert "Activity 11.1" in hub or "nichrome" in hub
    assert "Type 01:" not in ohms["concept_details"]
    assert "Type 01:" not in culm["concept_details"]
    assert "Miscellaneous Type" not in culm["concept_details"]
    assert not g._mined_type_topic_violations(out, mined)


def test_activity_types_use_normal_evidence_with_multiple_topic_concepts(
    monkeypatch,
):
    """Activity assignment corrects Culmination guesses and defers ambiguity."""
    def fake_openai(system, user, **kw):
        return {"assignments": [{
            "concept_id": "CONCEPT-0003",
            "type_ids": ["TYPE-CURRENT", "TYPE-AMBIGUOUS"],
        }]}

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    records = [
        {
            "topic": "Electricity",
            "parent_concept": "Current",
            "concept_title": "Measuring Current in a Circuit",
            "concept_details": "Description: Current is measured in series.",
            "keywords": "",
        },
        {
            "topic": "Electricity",
            "parent_concept": "Resistance",
            "concept_title": "Testing Wire Resistance",
            "concept_details": "Description: Resistance depends on the wire.",
            "keywords": "",
        },
        {
            "topic": "Electricity",
            "parent_concept": "Culmination",
            "concept_title": "Culmination - Electricity",
            "concept_details": "Description: Recap",
            "keywords": "",
        },
    ]
    mined = {"types": [
        {
            "type_id": "TYPE-CURRENT",
            "type_title": "Measuring Current in a Circuit Activity",
            "topic_match_hint": "Electricity",
            "is_activity": True,
            "case_prompts": [{
                "case_title": "Observe current",
                "examples": [{
                    "example_prompt": "Measure current as cells are added.",
                }],
            }],
        },
        {
            "type_id": "TYPE-AMBIGUOUS",
            "type_title": "Classroom Investigation",
            "topic_match_hint": "Electricity",
            "is_activity": True,
            "case_prompts": [{
                "case_title": "Record observations",
                "examples": [{
                    "example_prompt": "Complete the classroom investigation.",
                }],
            }],
        },
    ]}

    out = g._assign_mined_types_via_api(
        records, meta=g._metadata(subject="Physics"), mined_types=mined,
        max_attempts=1)

    assert "Measure current as cells are added." in cr.activity_hub_body(
        out[0]["concept_details"])
    assert not cr.activity_hub_body(out[1]["concept_details"])
    assert not cr.activity_hub_body(out[2]["concept_details"])
    assert "Complete the classroom investigation." not in str(out)
    assert not g._mined_type_topic_violations(out, mined)


def test_activity_inventory_excluded_from_types_coverage_and_placed_in_hub():
    activity = (
        "Set up the circuit with a nichrome wire and record the ammeter "
        "reading for each cell added."
    )
    exercise = (
        "Calculate the resistance of a conductor when potential difference "
        "is 12 V and current is 2 A."
    )
    inventory = {"items": [
        {
            "qid": "QINV-0001",
            "source_kind": "activity",
            "source_label": "Activity 11.1",
            "raw_task": activity,
            "topic_hint": "Electricity",
        },
        {
            "qid": "QINV-0002",
            "source_kind": "exercise",
            "raw_task": exercise,
            "topic_hint": "Electricity",
        },
    ]}
    records = [{
        "topic": "Electricity",
        "parent_concept": "Current",
        "concept_title": "Ohm's Law",
        "concept_details": (
            "Description: V = IR.\nAchieving Mastery: Applying Ohm's law. "
            f"// Types: Type 01: Ohm's law Case 01: Direct V/I questions "
            f"Example: {exercise} "
            "// Misconceptions: Students confuse R and resistivity."
        ),
        "keywords": "",
    }]
    # Activity items are not part of the Types exact-coverage contract.
    assert g._rendered_inventory_coverage_defects(records, inventory) == {
        "missing": [],
        "duplicate": [],
    }
    out = g._place_activity_inventory_into_hubs(records, inventory)
    assert "Activity 11.1" in cr.activity_hub_body(out[0]["concept_details"])
    assert "nichrome" in cr.activity_hub_body(out[0]["concept_details"])


def test_gpt_selected_activity_hub_relocates_exact_assessable_example(monkeypatch):
    prompt = "Record the current while increasing the number of cells."
    inventory = {"items": [{
        "qid": "QINV-0001",
        "source_kind": "checkpoint_question",
        "source_label": "Activity 11.1 question",
        "raw_task": prompt,
        "topic_hint": "Ohm's Law",
        "_activity_origin": True,
    }]}
    records = [
        {
            "topic": "Ohm's Law",
            "parent_concept": "Resistance",
            "concept_title": "General Resistance",
            "concept_details": (
                "Description: Resistance opposes current. // Types: "
                "Type 01: Experimental questions Case 01: Observe current "
                f"Example: {prompt}"
            ),
            "keywords": "",
        },
        {
            "topic": "Ohm's Law",
            "parent_concept": "Experiments",
            "concept_title": "Testing the Voltage-current Relationship",
            "concept_details": (
                "Description: Compare measured V and I. // Types: "
                "Type 01: Conceptual checks Case 01: Proportionality "
                "Example: Explain why voltage and current are proportional. "
                "Type 02: Reading graphs Case 01: Slope "
                "Example: Interpret the slope of a voltage-current graph."
            ),
            "keywords": "",
        },
    ]
    monkeypatch.setattr(
        g, "_openai_json",
        lambda *args, **kwargs: {"placements": [{
            "qid": "QINV-0001",
            "concept_id": "CONCEPT-0002",
            "hub_note": f"Activity: Measure V and I. {prompt}",
        }]},
    )

    out = g._populate_activity_hubs_via_api(
        records, inventory, meta=g._metadata(subject="Physics"))

    assert prompt not in g._types_body(out[0]["concept_details"])
    assert prompt in g._types_body(out[1]["concept_details"])
    assert prompt in cr.activity_hub_body(out[1]["concept_details"])
    assert re.findall(
        r"\bType\s+(\d{2}):", g._types_body(out[1]["concept_details"])
    ) == ["01", "02", "03"]
    assert g._rendered_inventory_example_locations(
        out, inventory["items"][0]) == [1]
    assert g._rendered_inventory_coverage_defects(out, inventory) == {
        "missing": [],
        "duplicate": [],
    }
    assert not g._rendered_inventory_topic_violations(out, inventory)
    assert not g._activity_example_hub_alignment_violations(out, inventory)


def test_activity_alignment_keeps_hub_copy_when_exact_example_is_duplicated():
    prompt = "Record the current while increasing the number of cells."
    item = {
        "qid": "QINV-0001",
        "source_kind": "checkpoint_question",
        "source_label": "Activity 11.1 question",
        "raw_task": prompt,
        "topic_hint": "Ohm's Law",
        "_activity_origin": True,
    }
    inventory = {"items": [item]}
    records = [
        {
            "topic": "Ohm's Law",
            "parent_concept": "Resistance",
            "concept_title": "General Resistance",
            "concept_details": (
                "Description: Resistance opposes current. // Types: "
                "Type 01: Experimental questions Case 01: Observe current "
                f"Example: {prompt} "
                "Type 02: Direct calculations Case 01: Find resistance "
                "Example: Calculate resistance from 12 V and 2 A."
            ),
            "keywords": "",
        },
        {
            "topic": "Ohm's Law",
            "parent_concept": "Experiments",
            "concept_title": "Testing the Voltage-current Relationship",
            "concept_details": (
                f"Description: Compare measured V and I. // Activity/Info Hub: "
                f"Activity: Measure V and I. {prompt} // Types: "
                "Type 01: Experimental questions Case 01: Compare readings "
                f"Example: {prompt}"
            ),
            "keywords": "",
        },
    ]

    out = g._align_activity_examples_with_hubs(records, inventory)

    assert g._rendered_inventory_example_locations(out, item) == [1]
    assert g._rendered_inventory_coverage_defects(out, inventory) == {
        "missing": [],
        "duplicate": [],
    }
    assert not g._activity_example_hub_alignment_violations(out, inventory)
    assert re.findall(
        r"\bType\s+(\d{2}):", g._types_body(out[0]["concept_details"])
    ) == ["01"]
    assert re.findall(
        r"\bType\s+(\d{2}):", g._types_body(out[1]["concept_details"])
    ) == ["02"]


def test_preexisting_activity_hub_is_aligned_without_an_api_call(monkeypatch):
    prompt = "Record the current while increasing the number of cells."
    inventory = {"items": [{
        "qid": "QINV-0001",
        "source_kind": "checkpoint_question",
        "source_label": "Activity 11.1 question",
        "raw_task": prompt,
        "topic_hint": "Ohm's Law",
        "_activity_origin": True,
    }]}
    records = [
        {
            "topic": "Ohm's Law",
            "parent_concept": "Resistance",
            "concept_title": "General Resistance",
            "concept_details": (
                "Description: Resistance opposes current. // Types: "
                "Type 01: Experimental questions Case 01: Observe current "
                f"Example: {prompt}"
            ),
            "keywords": "",
        },
        {
            "topic": "Ohm's Law",
            "parent_concept": "Experiments",
            "concept_title": "Testing the Voltage-current Relationship",
            "concept_details": (
                "Description: Compare measured V and I. // Activity/Info Hub: "
                f"Activity: Measure V and I. {prompt}"
            ),
            "keywords": "",
        },
    ]

    monkeypatch.setattr(
        g, "_openai_json",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("an existing Hub must not trigger an API call")),
    )
    out = g._populate_activity_hubs_via_api(
        records, inventory, meta=g._metadata(subject="Physics"))

    assert g._rendered_inventory_example_locations(
        out, inventory["items"][0]) == [1]
    assert g._rendered_inventory_coverage_defects(out, inventory) == {
        "missing": [],
        "duplicate": [],
    }
    assert not g._activity_example_hub_alignment_violations(out, inventory)


def test_terminal_coverage_repair_realigns_an_exact_activity_example():
    prompt = "Record the current while increasing the number of cells."
    item = {
        "qid": "QINV-0001",
        "source_kind": "checkpoint_question",
        "source_label": "Activity 11.1 question",
        "raw_task": prompt,
        "topic_hint": "Ohm's Law",
        "_activity_origin": True,
    }
    inventory = {"items": [item]}
    records = [
        {
            "topic": "Ohm's Law",
            "parent_concept": "Resistance",
            "concept_title": "General Resistance",
            "concept_details": (
                "Description: Resistance opposes current. // Types: "
                "Type 01: Experimental questions Case 01: Observe current "
                f"Example: {prompt}"
            ),
            "keywords": "",
        },
        {
            "topic": "Ohm's Law",
            "parent_concept": "Experiments",
            "concept_title": "Testing the Voltage-current Relationship",
            "concept_details": (
                "Description: Compare measured V and I. // Activity/Info Hub: "
                f"Activity: Measure V and I. {prompt}"
            ),
            "keywords": "",
        },
    ]
    assert g._rendered_inventory_coverage_defects(records, inventory) == {
        "missing": [],
        "duplicate": [],
    }

    out = g._enforce_rendered_inventory_coverage(records, inventory)

    assert g._rendered_inventory_example_locations(out, item) == [1]
    assert not g._activity_example_hub_alignment_violations(out, inventory)


def test_final_placement_rebuild_reuses_gpt_assignment_on_final_rows(monkeypatch):
    prompt = "Calculate the heat produced by a resistor carrying current."
    inventory = {"items": [{
        "qid": "QINV-0001",
        "source_kind": "exercise",
        "raw_task": prompt,
        "topic_hint": "Heating Effect",
    }]}
    mined = {"types": [{
        "type_id": "TYPE-0001",
        "type_title": "Calculating Electrical Heating",
        "topic_match_hint": "Heating Effect",
        "source_question_ids": ["QINV-0001"],
    }]}
    records = [
        {
            "topic": "Electric Current",
            "parent_concept": "Current",
            "concept_title": "Current in a Conductor",
            "concept_details": (
                "Description: Current is charge flow. // Types: "
                "Type 01: Calculating Electrical Heating "
                f"Case 01: Find heat Example: {prompt}"
            ),
            "keywords": "",
        },
        {
            "topic": "Heating Effect",
            "parent_concept": "Heating",
            "concept_title": "Joule Heating",
            "concept_details": "Description: Electrical energy becomes heat.",
            "keywords": "",
        },
    ]
    calls = []
    cleanup_calls = []

    def fake_assign(candidate, *, meta, mined_types):
        calls.append((meta, mined_types))
        assert all(not g._types_body(row["concept_details"]) for row in candidate)
        candidate[1] = dict(candidate[1])
        candidate[1]["concept_details"] = g._inject_types(
            candidate[1]["concept_details"],
            "Type 01: Calculating Electrical Heating "
            f"Case 01: Find heat Example: {prompt}",
        )
        return candidate

    monkeypatch.setattr(g, "_assign_mined_types_via_api", fake_assign)
    monkeypatch.setattr(
        g, "_populate_activity_hubs_via_api",
        lambda candidate, inventory, *, meta: candidate,
    )
    original_salvage = g._salvage_short_case_examples
    original_neutralize = g._neutralize_unrepaired_rows

    def track_salvage(candidate, *, inventory):
        cleanup_calls.append("salvage")
        return original_salvage(candidate, inventory=inventory)

    def track_neutralize(candidate, *, inventory):
        cleanup_calls.append("neutralize")
        return original_neutralize(candidate, inventory=inventory)

    monkeypatch.setattr(g, "_salvage_short_case_examples", track_salvage)
    monkeypatch.setattr(g, "_neutralize_unrepaired_rows", track_neutralize)

    out = g._rebuild_types_after_final_placement_drift(
        records, inventory, mined, meta=g._metadata(subject="Physics"))

    assert len(calls) == 1
    assert cleanup_calls == ["salvage", "neutralize"]
    assert g._rendered_inventory_example_locations(
        out, inventory["items"][0]) == [1]
    assert not g._rendered_inventory_topic_violations(out, inventory, mined)


def test_cross_topic_gpt_hub_choice_falls_back_to_exact_example_row(monkeypatch):
    prompt = "Compare the current through the wire for each applied voltage."
    item = {
        "qid": "QINV-0001",
        "source_kind": "checkpoint_question",
        "source_label": "Activity 11.2 question",
        "raw_task": prompt,
        "topic_hint": "Ohm's Law",
        "_activity_origin": True,
    }
    inventory = {"items": [item]}
    records = [
        {
            "topic": "Ohm's Law",
            "parent_concept": "Current",
            "concept_title": "Ohm's Law Measurements",
            "concept_details": (
                "Description: Voltage and current are proportional. // Types: "
                "Type 01: Experimental questions Case 01: Compare readings "
                f"Example: {prompt}"
            ),
            "keywords": "",
        },
        {
            "topic": "Resistance Factors",
            "parent_concept": "Materials",
            "concept_title": "Conductor Material",
            "concept_details": "Description: Materials have different resistivity.",
            "keywords": "",
        },
    ]
    monkeypatch.setattr(
        g, "_openai_json",
        lambda *args, **kwargs: {"placements": [{
            "qid": "QINV-0001",
            "concept_id": "CONCEPT-0002",
            "hub_note": f"Activity: Compare conductor materials. {prompt}",
        }]},
    )

    out = g._populate_activity_hubs_via_api(
        records, inventory, meta=g._metadata(subject="Physics"))

    assert prompt in cr.activity_hub_body(out[0]["concept_details"])
    assert not cr.activity_hub_body(out[1]["concept_details"])
    assert g._rendered_inventory_example_locations(out, item) == [0]
    assert not g._rendered_inventory_topic_violations(out, inventory)
    assert not g._activity_example_hub_alignment_violations(out, inventory)


def test_activity_hub_fallback_never_uses_culmination_without_topic_normal():
    activity = "Observe how current changes when another cell is added."
    inventory = {"items": [{
        "qid": "QINV-0001",
        "source_kind": "activity",
        "source_label": "Lab activity",
        "raw_task": activity,
        "topic_hint": "Electricity",
    }]}
    records = [
        {
            "topic": "Electricity",
            "parent_concept": "Culmination",
            "concept_title": "Culmination - Electricity",
            "concept_details": "Description: Recap",
            "keywords": "",
        },
        {
            "topic": "Magnetism",
            "parent_concept": "Fields",
            "concept_title": "Observing Magnetic Fields",
            "concept_details": "Description: Field lines show magnetic effects.",
            "keywords": "",
        },
    ]

    out = g._place_activity_inventory_into_hubs(records, inventory)

    assert not cr.activity_hub_body(out[0]["concept_details"])
    assert activity in cr.activity_hub_body(out[1]["concept_details"])


def test_empty_activity_task_uses_source_label_for_hub_fallback():
    item = {
        "qid": "QINV-0001",
        "source_kind": "activity",
        "source_label": "Activity 11.1",
        "raw_task": "   ",
        "normalized_task": "",
        "topic_hint": "Electricity",
    }
    records = [{
        "topic": "Electricity",
        "parent_concept": "Current",
        "concept_title": "Electric Current",
        "concept_details": "Description: Current is moving charge.",
        "keywords": "",
    }]

    assert not g._inventory_item_already_in_hubs(records, item)
    out = g._place_activity_inventory_into_hubs(
        records, {"items": [item]})

    assert "Activity 11.1" in cr.activity_hub_body(
        out[0]["concept_details"])
    assert g._inventory_item_already_in_hubs(out, item)


def test_type_review_rejects_activity_inventory_in_types_examples():
    activity = "Observe how current changes when another cell is added."
    item = {
        "qid": "QINV-0001",
        "source_kind": "activity",
        "source_label": "Lab activity",
        "raw_task": activity,
        "topic_hint": "Electricity",
    }
    inventory = {"items": [item]}
    original = [{
        "topic": "Electricity",
        "parent_concept": "Current",
        "concept_title": "Electric Current",
        "concept_details": (
            "Description: Current is moving charge. // Activity/Info Hub: "
            f"Activity: Lab activity. {activity} // "
            "Misconceptions: Students may confuse current and charge."
        ),
        "keywords": "",
    }]
    candidate = [dict(original[0])]
    candidate[0]["concept_details"] = g._inject_types(
        candidate[0]["concept_details"],
        "Type 01: Classroom investigation Case 01: Observe current "
        f"Example: {activity}",
    )

    assert g._accept_exact_inventory_type_review(
        original, candidate, inventory) is original

    types_only = [dict(candidate[0])]
    types_only[0]["concept_details"] = cr.join_sections([
        (label, body)
        for label, body in cr.split_sections(types_only[0]["concept_details"])
        if not cr.is_activity_hub_label(label)
    ])
    assert g._place_activity_inventory_into_hubs(
        types_only, inventory) == types_only


def test_activity_hub_populated_via_api_not_chapter_filters(monkeypatch):
    """GPT places activity inventory; chapter-named dilemma headings are not
    hard-coded as filler topics."""
    activity = "Observe how current changes when another cell is added."
    inventory = {"items": [{
        "qid": "QINV-0001",
        "source_kind": "activity",
        "source_label": "Lab activity",
        "raw_task": activity,
        "topic_hint": "Electric Current",
    }]}
    records = [
        {
            "topic": "Electric Current",
            "parent_concept": "Current",
            "concept_title": "Relationship Between V And I",
            "concept_details": (
                "Description: V and I are proportional for ohmic conductors.\n"
                "Achieving Mastery: Relating V and I from readings. "
                "// Misconceptions: Students treat all conductors as ohmic."
            ),
            "keywords": "",
        },
        {
            "topic": "Electric Current",
            "parent_concept": "Culmination",
            "concept_title": "Culmination - Electric Current",
            "concept_details": "Description: Recap",
            "keywords": "",
        },
    ]
    monkeypatch.setattr(
        g, "_openai_json",
        lambda *a, **kw: {
            "placements": [{
                "concept_id": "CONCEPT-0001",
                "qid": "QINV-0001",
                "hub_note": f"Activity: Lab activity. {activity}",
            }],
        },
    )
    out = g._populate_activity_hubs_via_api(
        records, inventory, meta=g._metadata(subject="Physics"))
    hub = cr.activity_hub_body(out[0]["concept_details"])
    assert "Lab activity" in hub
    assert "current changes" in hub
    assert not cr.activity_hub_body(out[1]["concept_details"])
    # Discussion-case chapter titles are not deterministic filler keys.
    assert not g._is_filler_source_topic("Khalil's Dilemma")
    assert not g._is_filler_source_topic(
        "Can You Help Poor Vikram in Answering Vetal?")
    assert g._is_filler_source_topic("Overview")
    assert g._is_filler_source_topic("Summary")


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


def test_neutralize_preserves_exact_inventory_owned_figure_prompt():
    task = (
        "Look at Fig. 14(a). Do you think that the people living in any of "
        "these regions thought of themselves as Italians? Examine Fig. 14(b). "
        "Which was the first region to become a part of unified Italy? Which "
        "was the last region to join? In which year did the largest number "
        "of states join?"
    )
    inventory = {"items": [{"qid": "QINV-0031", "raw_task": task}]}
    rows = [{
        "topic": "Italian Unification",
        "parent_concept": "National Unification",
        "concept_title": "Regions of Unified Italy",
        "concept_details": (
            "Description: See Example 11 for the regional sequence. // "
            "Types: Type 01: Interpreting territorial change over time "
            f"Case 01: Compare mapped stages Example: {task} // "
            "Misconceptions: Students may treat unification as simultaneous."
        ),
        "keywords": "",
    }]

    out = g._neutralize_unrepaired_rows(
        rows, inventory=inventory)

    assert "Example 11" not in out[0]["concept_details"]
    assert task in out[0]["concept_details"]
    assert g._rendered_inventory_coverage_defects(out, inventory) == {
        "missing": [],
        "duplicate": [],
    }
    report = concept_validator.validate_concept_rows(
        out,
        allow_types=True,
        allowed_source_examples=g._inventory_source_examples(inventory),
    )
    assert not any(
        error["code"] == "source_artifact"
        for error in report["errors"]
    )

    unowned = [dict(out[0])]
    unowned[0]["concept_details"] = unowned[0]["concept_details"].replace(
        "Fig. 14(a)", "Fig. 15(a)")
    hard_gate = concept_validator.validate_concept_rows(
        unowned,
        allow_types=True,
        allowed_source_examples=g._inventory_source_examples(inventory),
    )
    assert any(
        error["code"] == "source_artifact"
        for error in hard_gate["errors"]
    )


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
    assert "lesson-plan" in skeleton and "apart" in skeleton
    assert "Activity/Info" in skeleton
    assert "Frédéric Sorrieu" not in skeleton
    assert "Nationalism in Europe" not in skeleton
    canonicalize = g.prompts.get_text("concepts.canonicalize.system")
    assert "Belgium vs Sri Lanka" not in canonicalize
    assert "lesson-plan them apart" in canonicalize
    refine = g.prompts.get_text("concepts.description_refine.system")
    assert "Do NOT embed Mathpix" in refine
    assert "truncated mid-sentence" in refine
    assert "Preserve any existing Activity/Info Hub" in refine
    inventory = g.prompts.get_text("concepts.question_task_inventory.system")
    assert "dependent subquestions" in inventory
    assert "independently assessable" in inventory
    assert "Missing even one" in inventory and "checkpoint is a defect" in inventory
    assert "Activity/Info Hub" in inventory
    assert "feed culmination" not in inventory.lower()
    assert "Frédéric Sorrieu" not in inventory
    embedding = g.prompts.get_text("concepts.type_embedding.system")
    assert "Picture-/source-/map-based" in embedding
    repair = g.prompts.get_text("concepts.repair.system")
    assert "fig.11.1" in repair
    hub = g.prompts.get_text("concepts.activity_hub.system")
    assert "UNIVERSAL" in hub
    assert "is_culmination" in hub
    assert "pending" in hub.lower()
    types_example = g.prompts.get_text("concepts.types_example")
    assert "Ohm's law" not in types_example
    assert "reusable assessable pattern" in types_example
    math_types = g.prompts.get_text("concepts.types_guidance.math")
    assert "Ohm's Law" not in math_types
    descriptive_types = g.prompts.get_text("concepts.types_guidance.descriptive")
    assert "Belgium" not in descriptive_types
    assert "Do not put image URLs in the Description" in repair


def test_cleanup_strips_mathpix_from_description_keeps_types_and_hub():
    image_url = "https://cdn.mathpix.com/cropped/sorrieu.jpg"
    rec = {
        "topic": "The French Revolution and the Idea of the Nation",
        "parent_concept": "The Idea of the Nation",
        "concept_title": "Frédéric Sorrieu's Vision of Democratic Republics",
        "concept_details": (
            "Description: Sorrieu's utopian print series. "
            f"![]({image_url}) "
            "Achieving Mastery: Interpreting nationalist allegory.\n"
            " // Activity/Info Hub: Activity: Interpret the accompanying print. "
            f"(Refer fig. 1) ![]({image_url}) "
            " // Types: Type 01: Source interpretation "
            "Case 01: Print analysis "
            "Example: Describe the painting of the peoples of Europe. "
            f"(Refer fig. 1) ![]({image_url}) "
            "// Misconceptions: Students may treat the print as literal history."
        ),
        "keywords": "",
    }
    out = concept_cleanup.clean_concept_record(dict(rec), neutralize_artifacts=True)
    sections = dict(cr.split_sections(out["concept_details"]))
    assert image_url not in sections["Description"]
    assert image_url in sections["Activity/Info Hub"]
    assert image_url in sections["Types"]
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


def test_history_descriptive_examples_are_not_short_case_errors():
    rows = [{
        "topic": "The Making of Germany and Italy",
        "parent_concept": "German Unification",
        "concept_title": "German Unification Under Prussian Leadership",
        "concept_details": (
            "Description: Prussia led German unification. // "
            "Types: Type 01: Cause-effect Case 01: Leadership "
            "Example: Explain German unification under Prussia. // "
            "Misconceptions: Students may credit liberalism alone."
        ),
        "keywords": "",
    }]
    report = concept_validator.validate_concept_rows(rows, allow_types=True)
    assert not any(e["code"] == "short_case_example" for e in report["errors"])


def test_salvage_short_case_examples_expands_from_inventory():
    records = [
        {
            "topic": "The Making of Germany and Italy",
            "parent_concept": "German Unification",
            "concept_title": "German Unification Under Prussian Leadership",
            "concept_details": (
                "Description: Prussia led German unification. "
                "Achieving Mastery: Explaining Bismarck's role.\n"
                " // Types: Type 01: Cause-effect "
                "Case 01: Prussian leadership Example: q "
                "Case 02: Wars "
                "Example: Explain how the three wars of unification "
                "strengthened Prussia. "
                "// Misconceptions: Students may credit liberalism alone."
            ),
            "keywords": "",
        },
        {
            "topic": "Visualising the Nation",
            "parent_concept": "Allegory",
            "concept_title": "National Allegory and the Visual Language of Nationalism",
            "concept_details": (
                "Description: Nations were personified as female figures. "
                "Achieving Mastery: Reading nationalist allegory.\n"
                " // Types: Type 01: Source interpretation "
                "Case 01: Germania symbols Example: Describe the print. "
                "// Misconceptions: Students treat allegory as literal history."
            ),
            "keywords": "",
        },
    ]
    inventory = {"items": [
        {
            "qid": "QINV-0001",
            "raw_task": (
                "Explain how Prussian leadership under Bismarck used wars and "
                "diplomacy to unify Germany."
            ),
        },
        {
            "qid": "QINV-0002",
            "raw_task": (
                "Describe the painting of Germania and identify the symbols "
                "used to represent the German nation."
            ),
            "image_urls": ["https://cdn.mathpix.com/cropped/germania.jpg"],
        },
    ]}
    out = g._salvage_short_case_examples(records, inventory=inventory)
    out = g._neutralize_unrepaired_rows(out)
    assert "Prussian leadership under Bismarck" in out[0]["concept_details"]
    assert "Example: q" not in out[0]["concept_details"]
    assert "Germania" in out[1]["concept_details"]
    assert "cdn.mathpix.com" in out[1]["concept_details"]
    for row in out:
        report = concept_validator.validate_concept_rows(
            [row], allow_types=True, require_culmination=False)
        assert not any(
            e["code"] in {"short_case_example", "source_artifact"}
            and e["severity"] == "error"
            for e in report["errors"]
        )


def test_short_example_salvage_never_borrows_from_another_source_topic():
    own_topic_task = (
        "Describe the symbols used in the national allegory print and explain "
        "what each symbol represents."
    )
    other_topic_task = (
        "Describe the print and explain how it portrays imperial rivalry."
    )
    records = [{
        "topic": "Visualising the Nation",
        "parent_concept": "Allegory",
        "concept_title": "National Allegory",
        "concept_details": (
            "Description: Nations were represented as female figures. // "
            "Types: Type 01: Visual source interpretation "
            "Case 01: Allegorical symbols Example: Describe the print. // "
            "Misconceptions: Students may treat allegory as literal history."
        ),
        "keywords": "",
    }]
    inventory = {"items": [
        {
            "qid": "QINV-0001",
            "raw_task": own_topic_task,
            "topic_hint": "Visualising the Nation",
        },
        {
            "qid": "QINV-0002",
            "raw_task": other_topic_task,
            "topic_hint": "Nationalism and Imperialism",
        },
    ]}

    out = g._salvage_short_case_examples(records, inventory=inventory)

    assert own_topic_task in out[0]["concept_details"]
    assert other_topic_task not in out[0]["concept_details"]


def test_final_scrub_clears_page14_and_reintroduced_artifacts():
    """Electricity deposit must not die on OCR page14 / post-mastery Example N."""
    rows = [
        {
            "topic": "Electric Current And Circuit",
            "parent_concept": "Resistance",
            "concept_title": "What Determines Resistance in a Conductor",
            "concept_details": (
                "Description: Resistance depends on three factors as shown in "
                "Fig.12.1 and Table 12.2 on page14. "
                "Achieving Mastery: Predicting how R changes.\n"
                " // Types: Type 01: Geometry Case 01: Length "
                "Example: Find R when the wire in fig.12.5 is doubled. "
                "// Misconceptions: Students confuse R and resistivity."
            ),
            "keywords": "",
        },
        {
            "topic": "Electric Current And Circuit",
            "parent_concept": "Resistivity",
            "concept_title": "Resistivity and Material Comparison",
            "concept_details": (
                "Description: Resistivity is intrinsic; see Example11 and p14. "
                "Achieving Mastery: Comparing materials by resistivity.\n"
                " // Types: Type 01: Material comparison Case 01: Table "
                "Example: Compare the resistivities listed in Table12.2. "
                "// Misconceptions: Students confuse resistance with resistivity."
            ),
            "keywords": "",
        },
    ]
    out = g._neutralize_unrepaired_rows([dict(r) for r in rows])
    # Simulate a later mastery GPT pass reintroducing an artifact.
    out[0]["concept_details"] = out[0]["concept_details"].replace(
        "Predicting how R changes.",
        "Predicting how R changes as in Example 12 on page 20.",
    )
    out = g._neutralize_unrepaired_rows(out)
    report = concept_validator.validate_concept_rows(
        out, allow_types=True, require_culmination=False)
    assert not any(
        e["code"] == "source_artifact" and e["severity"] == "error"
        for e in report["errors"]
    )
    combined = " ".join(r["concept_details"] for r in out)
    assert "page14" not in combined.lower()
    assert "example 12" not in combined.lower()
    assert "fig.12" not in combined.lower()


def test_salvage_replaces_artifact_examples_from_inventory():
    records = [{
        "topic": "The Age of Revolutions",
        "parent_concept": "Liberal Nationalism",
        "concept_title": "From Liberal Nationalism to Imperial Power Politics",
        "concept_details": (
            "Description: Liberal nationalism shifted toward imperial politics. "
            "Achieving Mastery: Tracing the shift.\n"
            " // Types: Type 01: Chronology Case 01: Balkan tension "
            "Example: See Example 3 on page 14 for the Balkan conflict. "
            "// Misconceptions: Students may treat 1848 as the end."
        ),
        "keywords": "",
    }]
    inventory = {"items": [{
        "qid": "QINV-0003",
        "raw_task": (
            "Trace how the Balkans became a source of nationalist tension "
            "in Europe after 1871."
        ),
    }]}
    out = g._salvage_short_case_examples(records, inventory=inventory)
    out = g._neutralize_unrepaired_rows(out)
    assert "Balkans became a source" in out[0]["concept_details"]
    assert "Example 3" not in out[0]["concept_details"]
    report = concept_validator.validate_concept_rows(
        out, allow_types=True, require_culmination=False)
    assert not any(e["severity"] == "error" for e in report["errors"])


def test_salvage_short_case_examples_is_idempotent_and_preserves_valid_types():
    full_case_based = (
        "Compare two source situations and justify which one forms an "
        "arithmetic progression."
    )
    full_formula = (
        "Find the twentieth term when the first term and common difference "
        "are given."
    )
    records = [{
        "topic": "Arithmetic Progressions",
        "parent_concept": "Recognising Progressions",
        "concept_title": "Recognise Arithmetic Progression Patterns",
        "concept_details": (
            "Description: Arithmetic progressions model patterns with a fixed "
            "change between consecutive terms. "
            "Achieving Mastery: Distinguishing constant-change patterns. // "
            "Types: Type 07: Case-based source classification "
            "Case 01: Truncated source task Example: q "
            f"Case 02: Compare source situations Example: {full_case_based} "
            "Type 08: Formula application "
            f"Case 01: Requested term Example: {full_formula} "
            "Type 09: Empty stub "
            "Case 01: Missing source task Example: x // "
            "Misconceptions: Students may compare terms instead of differences."
        ),
        "keywords": "arithmetic progression, common difference",
    }]

    short_only_inventory = {"items": [{"raw_task": "q"}]}
    once = g._salvage_short_case_examples(
        records, inventory=short_only_inventory)
    twice = g._salvage_short_case_examples(
        once, inventory=short_only_inventory)

    assert twice == once
    details = once[0]["concept_details"]
    assert "Type 07: Case-based source classification" in details
    assert "Type 08: Formula application" in details
    assert "Type 09: Empty stub" not in details
    assert full_case_based in details
    assert full_formula in details
    assert "Example: q" not in details
    assert "Example: x" not in details
    report = concept_validator.validate_concept_rows(
        once, allow_types=True, require_culmination=False)
    assert not any(
        e["code"] == "short_case_example" and e["severity"] == "error"
        for e in report["errors"]
    )


def _reviewed_history_structure_mmd() -> str:
    checkpoints = [
        "What did the French revolutionaries do to create a collective identity?",
        "How did language help to build the idea of the nation?",
        "Why did conservative regimes impose censorship?",
        "What did liberal nationalism stand for?",
        "How did the Greek struggle mobilise European support?",
        "Why was the Frankfurt Parliament unable to unite Germany?",
        "How did Bismarck use war to unify Germany?",
        "What role did Mazzini play in Italian unification?",
        "How was Britain formed as a nation-state?",
        "Why were female allegories used to represent nations?",
        "What attributes were associated with Marianne?",
        "How did Germania communicate the German national idea?",
        "Why did nationalism become linked with imperialism?",
        "How did Balkan rivalries create conflict in Europe?",
    ]
    topic_blocks = [
        ("1 The French Revolution and the Idea of the Nation", checkpoints[:2]),
        ("2 The Making of Nationalism in Europe", checkpoints[2:5]),
        ("3 The Age of Revolutions", checkpoints[5:8]),
        ("4 The Making of Germany and Italy", checkpoints[8:10]),
        ("5 Visualising the Nation", checkpoints[10:12]),
        ("6 Nationalism and Imperialism", checkpoints[12:]),
    ]
    source = []
    for heading, asks in topic_blocks:
        source.append(f"\\section*{{{heading}}}\n")
        source.append("This section develops its own source-grounded ideas.\n")
        source.extend(f"{ask}\n\n" for ask in asks)
    source.append("\\section*{Write in brief}\n")
    source.extend(
        f"{number}. Explain the concise historical task numbered {number}.\n"
        for number in range(1, 6)
    )
    source.append("\\section*{Discuss}\n")
    source.extend(
        f"{number}. Discuss the analytical historical task numbered {number}.\n"
        for number in range(1, 7)
    )
    return "".join(source)


def test_history_structure_audit_captures_all_checkpoints_and_exercises():
    sections = g.parse_mmd_sections(_reviewed_history_structure_mmd())
    anchors = g._source_task_anchors(sections)
    checkpoints = [
        item for item in anchors
        if item["source_kind"] == "checkpoint_question"
    ]
    exercises = [
        item for item in anchors if item["source_kind"] == "exercise"
    ]
    assert len(checkpoints) == 14
    assert len(exercises) == 11
    assert all(item["topic_hint"] for item in checkpoints)
    assert all(not item["topic_hint"] for item in exercises)


def test_inventory_keeps_distinct_questions_with_shared_section_label():
    items = [
        {
            "source_kind": "exercise",
            "source_label": "Exercises",
            "raw_task": f"Explain historical development {number}.",
            "normalized_task": f"Explain historical development {number}.",
        }
        for number in range(1, 12)
    ]
    assert len(g._merge_source_task_anchors(items, [])) == 11


def test_anchor_merge_preserves_full_mcq_stem_and_its_own_options():
    model_item = {
        "source_kind": "mcq",
        "source_label": "Exercise 5.2 Q2(i)",
        "raw_task": (
            "Which term of the AP 3, 8, 13, ... is 78? "
            "(A) 14 (B) 15 (C) 16 (D) 17"
        ),
        "normalized_task": "Which term of the AP 3, 8, 13, ... is 78?",
    }
    shorter_anchor = {
        "source_kind": "exercise",
        "source_label": "Exercise 5.2 Q2(i)",
        "raw_task": "Which term of the AP 3, 8, 13, ... is 78?",
        "normalized_task": "Which term of the AP 3, 8, 13, ... is 78?",
    }
    merged = g._merge_source_task_anchors([model_item], [shorter_anchor])
    assert len(merged) == 1
    assert "(A) 14 (B) 15 (C) 16 (D) 17" in merged[0]["raw_task"]


def test_structured_mcq_options_rebuild_the_same_question_only():
    item = g._sanitize_inventory_item({
        "source_kind": "mcq",
        "raw_task": "Which of 14, 15, 16, and 17 is prime?",
        "options": [
            {"label": "A", "text": "14"},
            {"label": "B", "text": "15"},
            {"label": "C", "text": "16"},
            {"label": "D", "text": "17"},
        ],
    })
    assert item["raw_task"] == (
        "Which of 14, 15, 16, and 17 is prime? (A) 14 (B) 15 (C) 16 (D) 17"
    )
    assert item["normalized_task"] == item["raw_task"]


def test_independent_lettered_exercise_subparts_get_separate_anchors():
    source = r"""
\section*{1 Revolutions}
Historical movements developed across Europe.

\section*{Write in brief}
1. Write a note on:
a) Giuseppe Mazzini
b) Count Camillo de Cavour
c) The Greek war of independence
d) Frankfurt parliament
e) The role of women in nationalist struggles
2. Explain the main revolutionary change.
"""
    anchors = g._source_task_anchors(g.parse_mmd_sections(source))
    exercises = [
        item for item in anchors if item["source_kind"] == "exercise"
    ]
    assert len(exercises) == 6
    assert [item["source_label"] for item in exercises[:5]] == [
        f"Write in brief Q1({letter})" for letter in "abcde"
    ]
    assert all(
        item["raw_task"].startswith("Write a note on:")
        for item in exercises[:5]
    )
    assert all(not item["topic_hint"] for item in exercises)


def test_dependent_lettered_subquestions_remain_one_inventory_anchor():
    source = r"""
\section*{1 Source Analysis}
Read the passage and use it for all parts.
\section*{Questions}
1. Using the passage above: (a) identify the speaker (b) explain the argument
(c) infer why the audience responded.
"""
    anchors = g._source_task_anchors(g.parse_mmd_sections(source))
    exercises = [
        item for item in anchors if item["source_kind"] == "exercise"
    ]
    assert len(exercises) == 1
    assert "(a)" in exercises[0]["raw_task"]
    assert "(b)" in exercises[0]["raw_task"]
    assert "(c)" in exercises[0]["raw_task"]


def test_split_subpart_anchors_replace_compound_model_inventory_row():
    parent = {
        "source_kind": "exercise",
        "source_label": "Write in brief Q1",
        "raw_task": "Write a note on: a) Mazzini b) Cavour",
        "normalized_task": "Write a note on: a) Mazzini b) Cavour",
    }
    anchors = [
        {
            "source_kind": "exercise",
            "source_label": f"Write in brief Q1({letter})",
            "parent_source_label": "Write in brief Q1",
            "raw_task": f"Write a note on: {name}",
            "normalized_task": f"Write a note on: {name}",
        }
        for letter, name in [("a", "Mazzini"), ("b", "Cavour")]
    ]
    merged = g._merge_source_task_anchors([parent], anchors)
    assert len(merged) == 2
    assert all(item["source_label"] != "Write in brief Q1" for item in merged)


def test_topic_headings_never_truncate_valid_tail_topics():
    source = "\n".join(
        f"\\section*{{{number} Source Topic {number}}}\nBody {number}."
        for number in range(1, 15)
    )
    headings = g._topic_headings(g.parse_mmd_sections(source))
    assert len(headings) == 14
    assert headings[-1] == "Source Topic 14"


def test_missing_source_topic_recovery_adds_visualising_the_nation(monkeypatch):
    records = [{
        "topic": "The Making of Germany and Italy",
        "parent_concept": "Unification",
        "concept_title": "German Unification",
        "concept_details": "Description: Germany was unified.",
        "keywords": "Germany",
        "source_evidence": "German unification",
    }]
    excerpts = [
        {"topic": "The Making of Germany and Italy", "excerpt": "Germany."},
        {
            "topic": "Visualising the Nation",
            "excerpt": "Marianne and Germania personified nations.",
        },
    ]

    def fake_api(system, user):
        assert "Visualising the Nation" in user
        return {"rows": [{
            "topic": "Visualising the Nation",
            "parent_concept": "National Allegory",
            "concept": "Marianne and Germania as National Allegories",
            "concept_description": (
                "Description: Marianne and Germania gave visual form to "
                "otherwise abstract national identities."
            ),
            "keywords": "Marianne, Germania, allegory",
            "source_evidence": "Marianne and Germania personified nations",
        }]}

    monkeypatch.setattr(g, "_openai_json", fake_api)
    out = g._recover_missing_topic_concepts_via_api(
        records,
        meta=g._metadata(subject="Any"),
        source_topic_excerpts=excerpts,
    )
    assert {record["topic"] for record in out} == {
        "The Making of Germany and Italy",
        "Visualising the Nation",
    }


def test_source_topic_order_is_restored_after_recovery_append():
    records = [
        {
            "topic": "Nationalism and Imperialism",
            "concept_title": "Imperialist Rivalries",
            "concept_details": "Description: Rivalries intensified.",
        },
        {
            "topic": "Visualising the Nation",
            "concept_title": "National Allegories",
            "concept_details": "Description: Nations were personified.",
        },
        {
            "topic": "The French Revolution and the Idea of the Nation",
            "concept_title": "Revolutionary Nation",
            "concept_details": "Description: Sovereignty shifted to citizens.",
        },
    ]
    headings = [
        "The French Revolution and the Idea of the Nation",
        "Visualising the Nation",
        "Nationalism and Imperialism",
    ]
    out = g._reorder_records_by_source_topics(records, headings)
    assert [row["topic"] for row in out] == headings


def test_chapter_wide_tasks_are_semantically_distributed(monkeypatch):
    records = [
        {
            "topic": topic,
            "parent_concept": topic,
            "concept_title": concept,
            "concept_details": f"Description: {concept} is taught here.",
            "keywords": "",
        }
        for topic, concept in [
            ("Revolutions", "Liberal Revolution"),
            ("Visualising the Nation", "National Allegory"),
            ("Nationalism and Imperialism", "Balkan Rivalries"),
        ]
    ]
    inventory = {"items": [
        {
            "qid": "QINV-0001",
            "raw_task": "Explain how Marianne represented the French nation.",
            "source_kind": "exercise",
            "_topic_scope": "chapter",
        },
        {
            "qid": "QINV-0002",
            "raw_task": "Why did Balkan rivalries intensify imperial conflict?",
            "source_kind": "exercise",
            "_topic_scope": "chapter",
        },
    ]}

    def fake_api(system, user):
        assert "physical location" in system
        return {"assignments": [
            {"qid": "QINV-0001", "topic": "Visualising the Nation"},
            {"qid": "QINV-0002", "topic": "Nationalism and Imperialism"},
        ]}

    monkeypatch.setattr(g, "_openai_json", fake_api)
    out = g._assign_chapter_wide_inventory_topics_via_api(
        meta=g._metadata(subject="Any"),
        inventory=inventory,
        records=records,
        source_topic_excerpts=[
            {"topic": record["topic"], "excerpt": record["concept_details"]}
            for record in records
        ],
    )
    assert [item["topic_hint"] for item in out["items"]] == [
        "Visualising the Nation",
        "Nationalism and Imperialism",
    ]


def test_chapter_wide_task_placement_retries_invalid_topic(monkeypatch):
    calls = {"count": 0}
    records = [{
        "topic": "Visualising the Nation",
        "parent_concept": "Allegory",
        "concept_title": "National Allegory",
        "concept_details": "Description: Nations were personified.",
        "keywords": "",
    }]
    inventory = {"items": [{
        "qid": "QINV-0001",
        "raw_task": "Interpret the symbols carried by Germania.",
        "source_kind": "exercise",
        "_topic_scope": "chapter",
    }]}

    def fake_api(system, user):
        calls["count"] += 1
        topic = "Invented Review Topic" if calls["count"] == 1 else (
            "Visualising the Nation")
        return {"assignments": [{"qid": "QINV-0001", "topic": topic}]}

    monkeypatch.setattr(g, "_openai_json", fake_api)
    out = g._assign_chapter_wide_inventory_topics_via_api(
        meta=g._metadata(subject="Any"),
        inventory=inventory,
        records=records,
        source_topic_excerpts=[{
            "topic": "Visualising the Nation",
            "excerpt": "Germania carries symbolic attributes.",
        }],
    )
    assert calls["count"] == 2
    assert out["items"][0]["topic_hint"] == "Visualising the Nation"


def test_repeated_type_definitions_merge_into_cases():
    types = [
        {
            "type_id": "TYPE-0001",
            "type_title": "Interpreting National Allegory",
            "type_description": "Interpret symbols used to embody a nation.",
            "task_pattern": "Given an allegory, explain its national symbols.",
            "concept_match_hint": "National Allegory",
            "topic_match_hint": "Visualising the Nation",
            "source_question_ids": ["QINV-0001"],
            "case_prompts": [{
                "case_id": "CASE-0001",
                "case_title": "Marianne with republican symbols",
                "examples": [{
                    "source_question_id": "QINV-0001",
                    "example_prompt": "Explain the symbols associated with Marianne.",
                }],
            }],
        },
        {
            "type_id": "TYPE-0002",
            "type_title": "Interpreting National Allegory",
            "type_description": "Interpret symbols used to embody a nation.",
            "task_pattern": "Given an allegory, explain its national symbols.",
            "concept_match_hint": "National Allegory",
            "topic_match_hint": "Visualising the Nation",
            "source_question_ids": ["QINV-0002"],
            "case_prompts": [{
                "case_id": "CASE-0002",
                "case_title": "Germania with imperial symbols",
                "examples": [{
                    "source_question_id": "QINV-0002",
                    "example_prompt": "Explain the symbols associated with Germania.",
                }],
            }],
        },
    ]
    merged = g._merge_equivalent_mined_types(types)
    assert len(merged) == 1
    assert merged[0]["source_question_ids"] == ["QINV-0001", "QINV-0002"]
    assert len(merged[0]["case_prompts"]) == 2


def test_case_assignment_units_rejoin_when_assigned_to_same_concept():
    units = [
        {
            "type_id": f"TYPE-0001::CASE-{number:04d}::{number:04d}",
            "_origin_type_id": "TYPE-0001",
            "type_title": "Applying a Reusable Rule",
            "source_question_ids": [f"QINV-{number:04d}"],
            "case_prompts": [{
                "case_id": f"CASE-{number:04d}",
                "case_title": f"Condition {number}",
                "examples": [{
                    "source_question_id": f"QINV-{number:04d}",
                    "example_prompt": f"Apply the rule under condition {number}.",
                }],
            }],
        }
        for number in (1, 2)
    ]
    collapsed = g._collapse_assignment_units_for_render(units)
    assert len(collapsed) == 1
    body, _ = g._mined_type_to_body(collapsed[0], 0)
    assert body.count("Type 01:") == 1
    assert "Case 01: Condition 1" in body
    assert "Case 02: Condition 2" in body


def test_public_examples_strip_textbook_example_labels():
    body, _ = g._mined_type_to_body({
        "type_title": "Applying a Formula",
        "case_prompts": [{
            "case_title": "Example 11: Sum when the first and last terms are given",
            "examples": [{
                "example_prompt": "Example 11: Find the sum of the first ten terms.",
            }],
        }],
    }, 0)
    assert "Example 11" not in body
    assert body.count("Example:") == 1
    assert "Example: Find the sum of the first ten terms." in body


def test_inventory_topic_with_tasks_requires_rendered_types():
    records = [{
        "topic": "Sum of First n Terms",
        "parent_concept": "Finite Sums",
        "concept_title": "Applying the Sum Formula",
        "concept_details": "Description: Apply the finite-sum rule.",
        "keywords": "",
    }]
    inventory = {"items": [{
        "qid": "QINV-0001",
        "topic_hint": "Sum of First n Terms",
        "raw_task": "Find the sum of the first ten terms.",
    }]}
    assert g._inventory_topic_type_coverage_violations(records, inventory) == [{
        "topic": "Sum of First n Terms",
        "inventory_items": 1,
    }]


def test_type_review_cannot_drop_or_duplicate_inventory_examples():
    first = "Explain how a shared identity was created by revolutionaries."
    second = "Interpret the symbols used in a national allegory."
    inventory = {"items": [
        {"qid": "QINV-0001", "raw_task": first},
        {"qid": "QINV-0002", "raw_task": second},
    ]}
    original = [{
        "topic": "Nation",
        "parent_concept": "Identity",
        "concept_title": "National Identity",
        "concept_details": (
            "Description: Identity is constructed. // Types: "
            f"Type 01: Source interpretation Case 01: Political identity "
            f"Example: {first} "
            f"Case 02: Visual identity Example: {second} // "
            "Misconceptions: Identity is not timeless."
        ),
        "keywords": "",
    }]
    missing = [dict(original[0])]
    missing[0]["concept_details"] = missing[0]["concept_details"].replace(
        f"Case 02: Visual identity Example: {second} ", "")
    duplicate = [dict(original[0])]
    duplicate[0]["concept_details"] = duplicate[0]["concept_details"].replace(
        "// Misconceptions:",
        f"Case 03: Repeated visual identity Example: {second} // Misconceptions:",
    )

    assert g._accept_exact_inventory_type_review(
        original, missing, inventory) == original
    assert g._accept_exact_inventory_type_review(
        original, duplicate, inventory) == original
    assert g._rendered_inventory_coverage_defects(original, inventory) == {
        "missing": [],
        "duplicate": [],
    }


def test_type_review_cannot_move_activity_example_away_from_its_hub():
    prompt = "Interpret how the caricature represents parliamentary power."
    inventory = {"items": [{
        "qid": "QINV-0001",
        "source_kind": "checkpoint_question",
        "topic_hint": "German Unification",
        "_activity_origin": True,
        "raw_task": prompt,
    }]}
    original = [
        {
            "topic": "German Unification",
            "concept_title": "Bismarck and Parliament",
            "concept_details": (
                "Description: The caricature contrasts executive and elected "
                "power. // Activity/Info Hub: Activity: "
                f"{prompt} // Types: Type 01: Interpreting political cartoons "
                f"Case 01: Explain a power relationship Example: {prompt}"
            ),
        },
        {
            "topic": "Italian Unification",
            "concept_title": "Garibaldi and Italy",
            "concept_details": "Description: Garibaldi led a military campaign.",
        },
    ]
    candidate = [dict(row) for row in original]
    candidate[0]["concept_details"] = candidate[0]["concept_details"].replace(
        " // Types: Type 01: Interpreting political cartoons "
        f"Case 01: Explain a power relationship Example: {prompt}",
        "",
    )
    candidate[1]["concept_details"] += (
        " // Types: Type 01: Interpreting political cartoons "
        f"Case 01: Explain a power relationship Example: {prompt}"
    )

    assert g._rendered_inventory_topic_violations(candidate, inventory)
    assert g._activity_example_hub_alignment_violations(candidate, inventory)
    assert g._accept_exact_inventory_type_review(
        original, candidate, inventory) is original


def test_rendered_inventory_coverage_handles_embedded_structure_tokens_exactly():
    prompt = (
        r"Compare Type 12: direct use with Case 03: boundary reasoning. "
        r"For Example: preserve \begin{figure} and max width=\textwidth exactly."
    )
    inventory = {"items": [{
        "qid": "QINV-0001",
        "raw_task": prompt,
    }]}
    records = [{
        "topic": "Reusable Tasks",
        "parent_concept": "Exact Source Questions",
        "concept_title": "Structural Words Inside a Question",
        "concept_details": (
            "Description: Structural words may be source content. // Types: "
            "Type 01: Interpret a source Case 01: Keep literal wording "
            f"Example: {prompt} // Misconceptions: Do not rewrite the source."
        ),
        "keywords": "",
    }]

    # The generic flat-string parser cannot disambiguate source-owned markers;
    # exact coverage must therefore use inventory framing rather than its parts.
    assert prompt not in g._rendered_type_examples(records)
    assert g._rendered_inventory_coverage_defects(records, inventory) == {
        "missing": [],
        "duplicate": [],
    }

    mutated = [dict(records[0])]
    mutated[0]["concept_details"] = mutated[0]["concept_details"].replace(
        r"max width=\textwidth", "max width=textwidth")
    assert g._rendered_inventory_coverage_defects(mutated, inventory) == {
        "missing": ["QINV-0001"],
        "duplicate": [],
    }


def test_salvage_does_not_duplicate_already_rendered_inventory_examples():
    """Short stubs must not steal inventory prompts already placed elsewhere."""
    germania = (
        "Describe the painting of Germania and identify the symbols used to "
        "represent the German nation."
    )
    prussia = (
        "Explain how Prussian leadership under Bismarck used wars and "
        "diplomacy to unify Germany."
    )
    liberal = (
        "Explain what the ideas of liberal nationalists meant in the early "
        "nineteenth century."
    )
    records = [
        {
            "topic": "Visualising the Nation",
            "parent_concept": "Allegory",
            "concept_title": "Culmination - Romantic Culture, Women's Exclusion",
            "concept_details": (
                "Description: Culmination of visual nationalism. "
                "Achieving Mastery: Reading allegory. // Types: "
                "Type 01: Source Case 01: Germania print "
                "Example: Describe the print. "
                "Case 02: Another Example: q // "
                "Misconceptions: Allegory is not literal history."
            ),
            "keywords": "",
        },
        {
            "topic": "Visualising the Nation",
            "parent_concept": "Allegory",
            "concept_title": "National Allegory Germania",
            "concept_details": (
                "Description: Nations were personified. "
                "Achieving Mastery: Reading symbols. // Types: "
                f"Type 01: Source Case 01: Germania Example: {germania} // "
                "Misconceptions: Symbols are not decorative extras."
            ),
            "keywords": "",
        },
        {
            "topic": "The French Revolution and the Idea of the Nation",
            "parent_concept": "Liberalism",
            "concept_title": "Liberal Nationalism",
            "concept_details": (
                "Description: Liberal ideas shaped early nationalism. "
                "Achieving Mastery: Explaining liberal nationalism. // Types: "
                f"Type 01: Ideas Case 01: Meaning Example: {prussia} // "
                "Misconceptions: Liberalism is not only economic freedom."
            ),
            "keywords": "",
        },
    ]
    inventory = {"items": [
        {"qid": "QINV-0001", "raw_task": germania,
         "topic_hint": "Visualising the Nation"},
        {"qid": "QINV-0002", "raw_task": prussia,
         "topic_hint": "The Making of Germany and Italy"},
        {"qid": "QINV-0003", "raw_task": liberal,
         "topic_hint": "The French Revolution and the Idea of the Nation"},
    ]}

    salvaged = g._salvage_short_case_examples(
        [dict(row) for row in records], inventory=inventory)
    # Germania must remain exactly once; stub must not duplicate it.
    assert g._rendered_inventory_coverage_defects(salvaged, inventory)[
        "duplicate"
    ] == []
    assert "Describe the print." not in salvaged[0]["concept_details"]
    assert salvaged[0]["concept_details"].count(germania) == 0

    repaired = g._repair_rendered_inventory_coverage(salvaged, inventory)
    assert g._rendered_inventory_coverage_defects(repaired, inventory) == {
        "missing": [],
        "duplicate": [],
    }
    assert liberal in repaired[2]["concept_details"]


def test_repair_rendered_inventory_coverage_removes_duplicates_and_fills_gaps():
    first = "Explain how a shared identity was created by revolutionaries."
    second = "Interpret the symbols used in a national allegory."
    inventory = {"items": [
        {"qid": "QINV-0001", "raw_task": first, "topic_hint": "Nation"},
        {"qid": "QINV-0002", "raw_task": second, "topic_hint": "Nation"},
    ]}
    broken = [{
        "topic": "Nation",
        "parent_concept": "Identity",
        "concept_title": "National Identity",
        "concept_details": (
            "Description: Identity is constructed. Achieving Mastery: x. // "
            "Types: Type 01: Source interpretation "
            f"Case 01: Political identity Example: {first} "
            f"Case 02: Repeated Example: {first} // "
            "Misconceptions: Identity is not timeless."
        ),
        "keywords": "",
    }]

    repaired = g._repair_rendered_inventory_coverage(broken, inventory)
    assert g._rendered_inventory_coverage_defects(repaired, inventory) == {
        "missing": [],
        "duplicate": [],
    }
    assert repaired[0]["concept_details"].count(first) == 1
    assert second in repaired[0]["concept_details"]


def test_coverage_repair_groups_semantic_fallback_cases_on_normal_concept():
    first = "Explain how a shared identity was created by revolutionaries."
    second = "Interpret the symbols used in a national allegory."
    inventory = {"items": [
        {
            "qid": "QINV-0001",
            "source_kind": "checkpoint_question",
            "raw_task": first,
            "topic_hint": "Nation",
        },
        {
            "qid": "QINV-0002",
            "source_kind": "checkpoint_question",
            "raw_task": second,
            "topic_hint": "Nation",
        },
    ]}
    records = [
        {
            "topic": "Nation",
            "parent_concept": "Identity",
            "concept_title": "National Identity",
            "concept_details": (
                "Description: Identity is constructed. Achieving Mastery: x. // "
                "Misconceptions: Students may treat identity as timeless."
            ),
            "keywords": "",
        },
        {
            "topic": "Nation",
            "parent_concept": "Synthesis",
            "concept_title": "Culmination - Identity and Allegory",
            "concept_details": "Description: Recap",
            "keywords": "",
        },
    ]
    repaired = g._repair_rendered_inventory_coverage(records, inventory)
    normal_details = repaired[0]["concept_details"]
    assert "Source inventory task" not in normal_details
    assert normal_details.count("Answering a Checkpoint Question") == 1
    assert normal_details.count("Case 01:") == 1
    assert normal_details.count("Case 02:") == 1
    assert first in normal_details and second in normal_details
    assert first not in repaired[1]["concept_details"]
    assert second not in repaired[1]["concept_details"]


def test_repair_does_not_double_append_shared_normalized_inventory_prompts():
    """Sibling qids with the same normalized text must place the prompt once."""
    shared = (
        "Explain how a shared identity was created by revolutionaries "
        "across Europe."
    )
    inventory = {"items": [
        {"qid": "QINV-0001", "raw_task": shared, "topic_hint": "Nation"},
        # Same wording / normalization as QINV-0001 — both report missing when
        # count is 0, but only one Example slot should be created.
        {"qid": "QINV-0002", "raw_task": f"  {shared}  ", "topic_hint": "Nation"},
    ]}
    empty = [{
        "topic": "Nation",
        "parent_concept": "Identity",
        "concept_title": "National Identity",
        "concept_details": (
            "Description: Identity is constructed. Achieving Mastery: x. // "
            "Misconceptions: Identity is not timeless."
        ),
        "keywords": "",
    }]

    repaired = g._repair_rendered_inventory_coverage(empty, inventory)
    assert g._rendered_inventory_coverage_defects(repaired, inventory) == {
        "missing": [],
        "duplicate": [],
    }
    assert repaired[0]["concept_details"].count(shared) == 1


def test_default_openai_model_is_gpt_56_luna():
    from pathlib import Path

    from app import config

    source = Path(config.__file__).read_text()
    assert 'os.environ.get("AEGIS_OPENAI_MODEL", "gpt-5.6-luna")' in source


def test_coverage_defects_ignore_empty_or_stub_inventory_prompts():
    inventory = {"items": [
        {"qid": "QINV-0001", "raw_task": ""},
        {"qid": "QINV-0002", "raw_task": "q"},
        {
            "qid": "QINV-0003",
            "raw_task": (
                "Explain how current flows through a closed electric circuit."
            ),
        },
    ]}
    records = [{
        "topic": "Electric Current And Circuit",
        "parent_concept": "Current",
        "concept_title": "Closed Circuit Current",
        "concept_details": (
            "Description: Current needs a closed path. Achieving Mastery: x. // "
            "Misconceptions: Open circuits still carry current."
        ),
        "keywords": "",
    }]
    # Empty/stub inventory rows are not part of the exact-coverage contract.
    assert g._rendered_inventory_coverage_defects(records, inventory) == {
        "missing": ["QINV-0003"],
        "duplicate": [],
    }
    enforced = g._enforce_rendered_inventory_coverage(records, inventory)
    assert g._rendered_inventory_coverage_defects(enforced, inventory) == {
        "missing": [],
        "duplicate": [],
    }
    assert "closed electric circuit" in enforced[0]["concept_details"]


def test_enforce_coverage_does_not_abort_on_residual_missing(monkeypatch):
    """After repair attempts, residual missing warns instead of hard-failing."""
    prompt = (
        "Calculate the resistance of a conductor when potential difference "
        "and current are given."
    )
    inventory = {"items": [
        {"qid": "QINV-0001", "raw_task": prompt, "topic_hint": "Ohm"},
    ]}
    records = [{
        "topic": "Ohm",
        "parent_concept": "Resistance",
        "concept_title": "Ohm's Law",
        "concept_details": (
            "Description: V = IR. Achieving Mastery: x. // "
            "Misconceptions: Students confuse R and resistivity."
        ),
        "keywords": "",
    }]

    # Force the repair placer to no-op so residual missing remains.
    monkeypatch.setattr(
        g, "_append_inventory_example_to_record",
        lambda record, text, item=None: record,
    )
    out = g._enforce_rendered_inventory_coverage(records, inventory)
    assert out is not None
    assert g._rendered_inventory_coverage_defects(out, inventory)["missing"]


def test_unambiguous_case_evidence_overrides_wrong_concept_guess():
    concepts = {
        "CONCEPT-0001": {
            "concept_id": "CONCEPT-0001",
            "topic": "Nation Formation",
            "concept": "Italian Fragmentation and Unification Efforts",
            "is_culmination": False,
        },
        "CONCEPT-0002": {
            "concept_id": "CONCEPT-0002",
            "topic": "Nation Formation",
            "concept": "British Nation-state Formation Through English Dominance",
            "is_culmination": False,
        },
        "CONCEPT-0003": {
            "concept_id": "CONCEPT-0003",
            "topic": "Nation Formation",
            "concept": "Culmination - Nation Formation",
            "is_culmination": True,
        },
    }
    britain = {
        "type_title": "Explaining Nation Formation",
        "case_prompts": [{
            "case_title": "Explain why British nationalism differed",
            "examples": [{
                "example_prompt": (
                    "How was the history of nationalism in Britain unlike "
                    "the rest of Europe?"
                ),
            }],
        }],
    }
    mixed = {
        "type_title": "Comparing Nation Formation",
        "case_prompts": [{
            "case_title": "Compare any two countries",
            "examples": [{
                "example_prompt": (
                    "Through a focus on any two countries, explain how "
                    "nations developed."
                ),
            }],
        }],
    }
    candidates = tuple(concepts)
    assert g._high_confidence_assignment_override(
        britain, candidates, concepts) == "CONCEPT-0002"
    assert g._high_confidence_assignment_override(
        mixed, candidates, concepts) == "CONCEPT-0003"

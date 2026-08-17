"""Regression tests for QA review feedback (Reviews 01–06)."""
import json
import re
from pathlib import Path

import pytest

from app import models
from app import bulk_import as bi
from app.services import (
    build_concepts,
    concept_cleanup,
    concept_refiner as cr,
    concept_validator,
)
from app.services import directory, generation as g


def _tracked_source_sections(filename):
    source = (Path(__file__).parents[1] / "data" / "Testing" / filename).read_text(
        encoding="utf-8")
    return g.parse_mmd_sections(source)


def _attached_source_anchors(filename):
    sections = _tracked_source_sections(filename)
    return sections, g._attach_explicit_figure_images(
        g._source_task_anchors(sections), sections)


def _assert_exact_figure_attachment(item, registry, figure_ids):
    """Assert source Figure IDs become same-question canonical image tags."""
    expected = [registry[figure_id][0] for figure_id in figure_ids]
    assert item["image_urls"] == [entry["url"] for entry in expected]
    assert list((item.get("_image_captions") or {}).values()) == [
        entry["caption"] for entry in expected
    ]
    rendered = g._inventory_task_text(item)
    assert rendered.count("[img ") == len(expected)
    for entry in expected:
        assert f'[img src="{entry["url"]}" ' in rendered
        assert " alt=\"" in rendered


def test_misconception_dedup_keeps_one_section():
    details = (
        "Description: Layers are studied indirectly.\n"
        "Achieving Mastery: Explaining indirect evidence. // "
        "Misconception: Students confuse crust and mantle. // "
        "Misconceptions: Students confuse crust and mantle."
    )
    out = cr.normalize_misconception_sections(details)
    assert [
        label for label, _ in cr.split_sections(out)
        if cr.is_learner_analysis_label(label)
    ] == ["Misconception/ Error Analysis"]
    assert "Achieving Mastery:" in out
    assert cr.analysis_components(out)[0] == "Students confuse crust and mantle."


def test_misconception_strips_inline_after_mastery():
    details = (
        "Description: A concept body.\n"
        "Achieving Mastery: Doing it well. // Misconception: A common error."
    )
    out = cr.normalize_misconception_sections(details)
    assert "// Misconception:" not in out.split("Misconceptions:")[0]
    assert "Error Analysis: A common error." in out


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
    assert [
        label for label, _ in cr.split_sections(out)
        if cr.is_learner_analysis_label(label)
    ] == ["Misconception/ Error Analysis"]
    assert "ignore the parallel-line condition" in out
    assert "memorized rule" not in out


def test_split_merged_description_blocks():
    merged = (
        "Description: First concept body. // Types: Type 01: Direct Case 01: q1. "
        "Description: Second concept wrongly merged. // Misconceptions: oops."
    )
    out = cr.split_merged_description_blocks(merged)
    assert "Second concept" not in out


def test_alias_related_titles_both_survive_the_cleanup_chain():
    """No deterministic similarity judgment: BPT and Basic Proportionality
    Theorem may genuinely be two concepts or one — that call belongs to the
    model (Settle topology) and the reviewer, never to an alias table or
    token-similarity keys. The deterministic cleanup chain keeps BOTH rows."""
    records = [
        {"topic": "Similarity", "parent_concept": "Similarity",
         "concept_title": "Basic Proportionality Theorem",
         "concept_details": (
             "Description: A line parallel to one side of a triangle divides "
             "the other two sides in the same ratio."
         ),
         "keywords": "bpt"},
        {"topic": "Criteria", "parent_concept": "Criteria",
         "concept_title": "BPT",
         "concept_details": (
             "Description: Applying the proportionality result while proving "
             "similarity criteria."
         ),
         "keywords": "bpt"},
        {"topic": "Criteria", "parent_concept": "Criteria",
         "concept_title": "Converse Basic Proportionality Theorem",
         "concept_details": (
             "Description: A line dividing two sides of a triangle in the "
             "same ratio is parallel to the third side."
         ),
         "keywords": "converse"},
    ]

    out = [concept_cleanup.clean_concept_record(dict(r)) for r in records]
    out = concept_cleanup.filter_review_violations(
        out, subject="Mathematics", board="CBSE", chapter_title="Triangles",
    )
    out = cr.refine_chapter(out)

    assert [r["concept_title"] for r in out] == [
        "Basic Proportionality Theorem",
        "BPT",
        "Converse Basic Proportionality Theorem",
    ]
    # The deterministic similarity machinery is gone from the codebase.
    assert not hasattr(concept_cleanup, "dedupe_similar_titles_chapter_wide")
    assert not hasattr(concept_cleanup, "titles_look_similar")
    assert not hasattr(concept_cleanup, "find_similar_title_groups")
    assert not hasattr(concept_cleanup, "_KNOWN_CONCEPT_ALIASES")

    # An undecided EXACT duplicate is flagged by the validator (a blocking
    # duplicate-label report at deposit), never silently dropped.
    duplicated = out + [dict(out[0])]
    report = concept_validator.validate_concept_rows(duplicated)
    assert any(
        e["code"] == "duplicate_title" for e in report["errors"]
    )
    assert len(duplicated) == 4  # validation never mutates or drops rows


def test_no_concept_row_is_deleted_for_what_its_title_seems_to_mean():
    """The pedagogy-title vocabulary is purged; the rows survive (Rule 1, R4).

    ``_PEDAGOGY_CONCEPT_RE`` deleted any row whose ``concept_title`` matched
    a twelve-phrase classroom-instruction vocabulary. That is CLAUDE.md's
    first forbidden bullet ("is this filler?") answered by a regex, and the
    loss was silent — an aggregate count, no title, no id, no review flag.
    Whether a row is a task container rather than a durable teaching concept
    is a judgment the pipeline already holds a verdict on (the Activity/Info
    Hub host proposal + its independent critic upstream; the validator's
    row-addressable ``forbidden_name``/``forbidden_topic`` review warnings
    downstream), so no new model pass is introduced here — the drop is
    simply gone.
    """
    assert not hasattr(concept_cleanup, "_PEDAGOGY_CONCEPT_RE")

    records = [
        {"topic": "A Letter to God", "concept_title": "Lencho's Faith",
         "concept_details": "Description: a", "keywords": ""},
        # The row the old vocabulary was written for.
        {"topic": "A Letter to God",
         "concept_title": "Pre-reading Prediction and Discussion",
         "concept_details": "Description: b", "keywords": ""},
        # A legitimately-named teaching concept the same pattern swallowed.
        {"topic": "A Letter to God",
         "concept_title": "Pre-Reading Vocabulary",
         "concept_details": "Description: c", "keywords": ""},
        {"topic": "Writing Skills", "concept_title": "Informal Letter Format",
         "concept_details": "Description: d", "keywords": ""},
    ]
    out = concept_cleanup.filter_review_violations(
        records, subject="Unclassified Upload", board="CBSE")
    assert [r["concept_title"] for r in out] == [
        "Lencho's Faith",
        "Pre-reading Prediction and Discussion",
        "Pre-Reading Vocabulary",
        "Informal Letter Format",
    ]
    # Surviving untouched means no flag either: nothing happened to them.
    assert not any(r.get("review_flags") for r in out)

    # The second run at the release boundary cannot take a second bite:
    # the function is now a fixpoint over the rows it keeps.
    assert concept_cleanup.filter_review_violations(
        out, subject="Unclassified Upload", board="CBSE") == out


def test_pedagogy_topic_reassignment_rides_the_row_as_a_review_flag():
    """A rewritten learner-visible topic is recorded on the row (R4).

    The topic reassignment survives this slice, but it may not be silent:
    the flag names the exact before/after so a reviewer can address the row,
    never an aggregate the run cannot trace back to anything.
    """
    records = [
        {"topic": "Classroom Activity", "concept_title": "Lencho's Faith",
         "concept_details": "Description: a", "keywords": ""},
    ]
    out = concept_cleanup.filter_review_violations(
        records, subject="", board="CBSE", chapter_title="A Letter to God")
    assert out[0]["topic"] == "A Letter to God"
    flags = out[0]["review_flags"]
    assert len(flags) == 1
    assert "R4" in flags[0]
    assert "'Classroom Activity'" in flags[0]
    assert "'A Letter to God'" in flags[0]

    # Idempotent: replaying the deterministic chain must not stack flags.
    again = concept_cleanup.filter_review_violations(
        out, subject="", board="CBSE", chapter_title="A Letter to God")
    assert again == out


def test_omitted_umbrella_topic_rows_are_named_never_bare_counted(monkeypatch):
    """An omitted row cannot carry a flag, so it is named instead (R4)."""
    from app.services import progress as _progress

    logged: list[str] = []
    monkeypatch.setattr(
        _progress, "log",
        lambda message, **_kw: logged.append(str(message)),
    )
    records = [
        {"topic": "Real Section", "concept_title": "A",
         "concept_details": "Description: a", "keywords": ""},
        {"topic": "Overview", "concept_title": "Preview of the Chapter",
         "concept_details": "Description: b", "keywords": ""},
    ]
    out = concept_cleanup.filter_review_violations(
        records, subject="Civics", board="CBSE")
    assert [r["concept_title"] for r in out] == ["A"]
    assert len(logged) == 1
    assert "'Preview of the Chapter'" in logged[0]
    assert "'Overview'" in logged[0]
    assert "pedagogy / filler concept row(s)" not in logged[0]


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


def test_overview_and_summary_content_reaches_excerpts_and_rows_survive():
    """Overview/Summary bodies reach the model with everything else.

    The old ``_is_filler_source_topic``/``_is_non_topic_heading`` vocabulary
    is purged (§3): no heading keyword decides that a section teaches
    nothing, and no concept row is deterministically deleted for its topic
    name. What a preview or recap means is the model's judgment.
    """
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
    # The numbered sections remain the structural main topics; the unnumbered
    # Overview/Summary umbrellas attach to them instead of standing alone.
    assert "Overview" not in headings
    assert "Summary" not in headings
    paired = g._sections_with_source_topics(sections)
    paired_headings = {
        (section.get("heading") or "") for _, section in paired
    }
    assert {"Overview", "Summary"} <= paired_headings
    excerpts = g._group_source_topic_excerpts(sections)
    joined = " ".join(group["excerpt"] for group in excerpts)
    # Overview/Summary prose REACHES the model inside its owning topic's
    # excerpt — nothing is withheld by a heading vocabulary.
    assert "UNIQUE_OVERVIEW_PREVIEW" in joined
    assert "UNIQUE_SUMMARY_RECAP" in joined
    # Chunking likewise carries every section to the model.
    chunk_sections = [
        section
        for chunk in g._pack_section_chunks(sections)
        for section in chunk["sections"]
    ]
    assert {
        (section.get("heading") or "") for section in chunk_sections
    } >= {"Overview", "Summary"}
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
            "concept_title": "Survives The Scrub",
            "concept_details": "Description: preview only.",
            "keywords": "",
        },
    ]
    assert g._missing_source_topic_excerpts(records, excerpts) == []
    # The model's rows stand: no concept row is deterministically deleted
    # for carrying an Overview/Summary topic ("Dropped ... filler" is gone).
    scrubbed = g._scrub_section_numbers([dict(r) for r in records])
    assert [r["concept_title"] for r in scrubbed] == [
        "Belgian Accommodation",
        "Prudential Reasons for Power Sharing",
        "Horizontal Power Sharing",
        "Survives The Scrub",
    ]
    assert scrubbed[3]["topic"] == "Overview"


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


def test_case_example_length_is_never_a_validation_error():
    """Replaces the retired ``short_case_example`` gate.

    A one-token Case body used to be a fatal error decided by a word
    count. Whether a rendered Example carries the book's real question is
    now settled where the source is in hand — the exact-once inventory
    coverage contract, keyed by QID — so the validator itself makes no
    length judgment at all, at any strictness.
    """
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
    for strict in (False, True):
        report = concept_validator.validate_concept_rows(
            rows, allow_types=True, strict_type_hierarchy=strict)
        codes = {e["code"] for e in report["errors"]}
        assert not any("short" in code for code in codes), sorted(codes)


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
    assert "Example 01: Calculate the resistance of the circuit if V is 220 V" in body
    assert "Case 02: Ohm's law formula-based question when the circuit diagram" in body
    assert (
        '(Refer fig. 11.1) '
        '[img src="https://cdn.mathpix.com/f11.jpg" alt="Source visual"]'
        in body
    )


def test_inventory_task_text_prefers_raw_task_and_ships_images():
    item = {
        "raw_task": "Calculate the resistance for the given circuit. (Refer fig. 11.1)",
        "normalized_task": "Compute resistance.",
        "image_urls": ["https://cdn.mathpix.com/f11.jpg"],
    }
    text = g._inventory_task_text(item)
    assert text.startswith("Calculate the resistance for the given circuit.")
    assert (
        '[img src="https://cdn.mathpix.com/f11.jpg" alt="Fig. 11.1"]'
        in text
    )


def test_inventory_visual_without_pdf_figure_number_gets_descriptive_alt():
    text = g._inventory_task_text({
        "source_kind": "diagram_task",
        "source_label": "Circuit comparison",
        "raw_task": "Compare the two circuits.",
        "image_urls": ["https://cdn.mathpix.com/circuits.jpg"],
    })
    assert (
        '[img src="https://cdn.mathpix.com/circuits.jpg" '
        'alt="Circuit comparison"]' in text
    )


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
        f'[img src="{url}" '
        'alt="Fig. 1 - A democratic republic print prepared in 1848"]'
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
        Path(__file__).parents[1] / "data" / "Testing" / "RNE.mmd"
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
    list_discuss = next(
        item for item in checkpoints
        if "political ends that List" in item["raw_task"]
    )
    assert "drew up the Treaty of Vienna" not in list_discuss["raw_task"]
    culture_discuss = next(
        item for item in checkpoints
        if "language and popular traditions" in item["raw_task"]
    )
    assert "Peasants' uprising" not in culture_discuss["raw_task"]
    assert "National Assembly proclaimed" not in culture_discuss["raw_task"]


def test_uploaded_nationalism_fixture_exposes_all_six_main_topics():
    source = (
        Path(__file__).parents[1] / "data" / "Testing" / "RNE.mmd"
    ).read_text(encoding="utf-8")

    assert g._topic_headings(g.parse_mmd_sections(source)) == [
        "The French Revolution and the Idea of the Nation",
        "The Making of Nationalism in Europe",
        "The Age of Revolutions: 1830-1848",
        "The Making of Germany and Italy",
        "Visualising the Nation",
        "Nationalism and Imperialism",
    ]


def test_deposit_topology_guard_rejects_five_of_six_nationalism_topics(
    db, first_chapter,
):
    source = (
        Path(__file__).parents[1] / "data" / "Testing" / "RNE.mmd"
    ).read_text(encoding="utf-8")
    topics = g._topic_headings(g.parse_mmd_sections(source))
    missing_topic = "The Making of Nationalism in Europe"
    records = [
        {
            "topic": topic,
            "parent_concept": topic,
            "concept_title": f"{topic} concept",
            "concept_details": "Description: Source-grounded concept.",
            "keywords": "nationalism",
        }
        for topic in topics
        if topic != missing_topic
    ]

    missing = build_concepts._missing_deposit_source_topics(records, source)

    assert [group["topic"] for group in missing] == [missing_topic]
    chapter = db.get(models.Chapter, first_chapter["id"])
    with pytest.raises(
        build_concepts.DepositValidationError,
        match="source-topic topology failed before deposit",
    ):
        build_concepts._deposit_concepts(
            db,
            chapter,
            records,
            "Post",
            "RNE",
            source_text=source,
        )


def test_deposit_restores_validated_snapshot_when_cleanup_drops_only_topic_row(
    db, first_chapter, monkeypatch,
):
    source = (
        Path(__file__).parents[1] / "data" / "Testing" / "RNE.mmd"
    ).read_text(encoding="utf-8")
    topics = g._topic_headings(g.parse_mmd_sections(source))
    removed_topic = "The Making of Nationalism in Europe"
    records = [
        {
            "topic": topic,
            "parent_concept": topic,
            "concept_title": f"{topic} concept",
            "concept_details": (
                "Description: The source explains this historical idea. // "
                "Misconception/ Error Analysis: Misconceptions: Learners may "
                "confuse its context.; Error Analysis: Learners may assign it "
                "to the wrong historical period."
            ),
            "keywords": "nationalism, Europe",
        }
        for topic in topics
    ]
    cleanup_called = False

    def drop_only_row(rows, **_kwargs):
        nonlocal cleanup_called
        cleanup_called = True
        return [
            row for row in rows
            if g._topic_comparison_key(row.get("topic", ""))
            != g._topic_comparison_key(removed_topic)
        ]

    monkeypatch.setattr(
        build_concepts.concept_cleanup,
        "filter_review_violations",
        drop_only_row,
    )
    class InventoryValidationReached(Exception):
        pass

    def inspect_inventory_validation(rows, *_args, **_kwargs):
        assert removed_topic in {row.get("topic") for row in rows}
        raise InventoryValidationReached

    monkeypatch.setattr(
        build_concepts.generation,
        "_normalize_activity_hubs_from_inventory",
        inspect_inventory_validation,
    )
    chapter = db.get(models.Chapter, first_chapter["id"])
    inventory = {
        "items": [{
            "qid": "QINV-MISSING-TOPIC",
            "source_kind": "exercise",
            "topic_hint": removed_topic,
            "raw_task": "Explain the making of nationalism in Europe.",
            "task_kind": "question",
        }],
        "stats": {"total_inventory_items": 1},
    }

    with pytest.raises(InventoryValidationReached):
        build_concepts._deposit_concepts(
            db,
            chapter,
            records,
            "Post",
            "RNE",
            inventory=inventory,
            mined_types={"types": []},
            source_text=source,
        )

    assert cleanup_called


def test_uploaded_nationalism_fixture_inventories_all_chapter_final_tasks():
    source = (
        Path(__file__).parents[1] / "data" / "Testing" / "RNE.mmd"
    ).read_text(encoding="utf-8")
    anchors = g._source_task_anchors(g.parse_mmd_sections(source))
    chapter_final = [
        item for item in anchors
        if (
            item.get("_topic_scope") == "chapter"
            and item.get("parent_source_label") in {
                "Write in brief", "Discuss", "Project",
            }
        )
    ]

    assert len(chapter_final) == 11
    # List-item kinds carry the neutral parse-time default; the real
    # exercise-vs-intext verdict is owned by the outline/page-verification
    # model passes (§3 purge).
    assert sum(
        item["source_kind"] == "intext_question" for item in chapter_final
    ) == 10
    stats = g._inventory_stats(anchors)
    assert stats["chapter_final_tasks"] == 11
    project = next(
        item for item in chapter_final
        if item["parent_source_label"] == "Project"
    )
    assert project["source_kind"] == "activity"
    assert "nationalist symbols in countries outside Europe" in project["raw_task"]
    project_note = g._compact_activity_hub_note(project)
    assert "Project Project" not in project_note
    assert "Find out more about nationalist symbols" in project_note
    assert "collect examples of pictures, posters or music" in project_note
    assert "How are these different from European examples?" in project_note
    assert "…" not in project_note


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


def test_repeated_figure_it_out_labels_match_only_within_their_topic():
    square = {
        "source_kind": "exercise",
        "source_label": "Figure it Out Q1",
        "parent_source_label": "Figure it Out",
        "topic_hint": "Square Numbers",
        "raw_task": "Which numbers are not perfect squares?",
    }
    cube = {
        "source_kind": "exercise",
        "source_label": "Figure it Out Q1",
        "parent_source_label": "Figure it Out",
        "topic_hint": "Cubic Numbers",
        "raw_task": "Find the cube roots of 27000 and 10648.",
    }

    assert g._inventory_items_match(square, square)
    assert g._inventory_items_match(cube, cube)
    assert not g._inventory_items_match(square, cube)
    assert not g._inventory_items_match(cube, square)
    stale_square = {
        **square,
        "topic_hint": "Cubic Numbers",
    }
    assert not g._inventory_items_match(stale_square, square)
    assert not g._inventory_items_match(stale_square, cube)
    merged = g._merge_source_task_anchors([dict(square), dict(cube)], [
        dict(square),
        dict(cube),
    ])
    assert [
        (item["topic_hint"], item["raw_task"]) for item in merged
    ] == [
        ("Square Numbers", "Which numbers are not perfect squares?"),
        ("Cubic Numbers", "Find the cube roots of 27000 and 10648."),
    ]


def test_math_notation_equivalence_merges_model_and_source_occurrences():
    model = {
        "source_kind": "worked_example",
        "source_label": "Consecutive squares",
        "topic_hint": "Square Numbers",
        "raw_task": (
            "Using the pattern above, find 36², given that 35²=1225."
        ),
        "shared_context": "The sum of the first n odd numbers is n².",
        "requires_context": True,
    }
    anchor = {
        "source_kind": "checkpoint_question",
        "source_label": "Checkpoint 5.1",
        "parent_source_label": "Perfect Squares and Odd Numbers",
        "topic_hint": "Square Numbers",
        "raw_task": (
            "Using the pattern above, find $36^{2}$, "
            "given that $35^{2}=1225$."
        ),
        "normalized_task": (
            "Using the pattern above, find $36^{2}$, "
            "given that $35^{2}=1225$."
        ),
    }

    merged = g._merge_source_task_anchors([model], [anchor])

    assert len(merged) == 1
    assert merged[0]["source_label"] == "Checkpoint 5.1"
    assert merged[0]["shared_context"] == model["shared_context"]


def test_matching_source_anchor_collapses_duplicate_inventory_variants():
    complete = {
        "source_kind": "source_task",
        "source_label": "Taxicab Numbers",
        "topic_hint": "Cubic Numbers",
        "raw_task": (
            "Express 4104 and 13832 in two ways as sums of two positive cubes."
        ),
        "shared_context": "A taxicab number has two cube-sum representations.",
        "requires_context": True,
    }
    truncated = {
        "source_kind": "checkpoint_question",
        "source_label": "Checkpoint 10.1",
        "parent_source_label": "Taxicab Numbers",
        "topic_hint": "Cubic Numbers",
        "raw_task": "Express 4104 and 13832 in two ways as sums of",
    }
    anchor = {
        **complete,
        "source_kind": "checkpoint_question",
        "source_label": "Checkpoint 10.1",
        "parent_source_label": "Taxicab Numbers",
    }

    merged = g._merge_source_task_anchors(
        [complete, truncated], [anchor])

    assert len(merged) == 1
    assert merged[0]["source_label"] == "Checkpoint 10.1"
    assert merged[0]["shared_context"] == complete["shared_context"]


def test_compound_figure_it_out_umbrella_is_replaced_by_atomic_anchors():
    umbrella = {
        "source_kind": "exercise",
        "source_label": "Figure it Out",
        "raw_task": (
            "1. Find the cube roots of 27000 and 10648. "
            "2. What number will you multiply by 1323 to make it a cube "
            "number?"
        ),
    }
    anchors = [
        {
            "source_kind": "exercise",
            "source_label": "? Figure it Out Q1",
            "raw_task": "Find the cube roots of 27000 and 10648.",
        },
        {
            "source_kind": "exercise",
            "source_label": "? Figure it Out Q2",
            "raw_task": (
                "What number will you multiply by 1323 to make it a cube "
                "number?"
            ),
        },
    ]

    merged = g._merge_source_task_anchors([umbrella], anchors)

    assert [item["raw_task"] for item in merged] == [
        anchor["raw_task"] for anchor in anchors
    ]


def test_repeated_numbered_question_labels_are_local_not_chapter_wide():
    assert g._source_label_is_generic("Q U E S T I O N S Q1")
    assert g._source_label_is_generic("Questions Q2")
    assert not g._source_label_is_generic("Question 3")
    assert not g._source_label_is_generic("EXERCISE 5.2 Q2")
    assert not g._source_label_is_generic("Activity 11.1")

    sections = _tracked_source_sections(
        "Class 10 Chapter 5 Electricity.mmd")
    anchors = g._source_task_anchors(sections)
    refreshed = g._refresh_inventory_from_source_anchors(
        g._empty_inventory(), sections)

    assert len(anchors) == 60
    assert len(refreshed["items"]) == 60
    assert {
        g._inventory_task_match_key(item) for item in refreshed["items"]
    } == {
        g._inventory_task_match_key(item) for item in anchors
    }
    repeated_q1 = [
        item for item in refreshed["items"]
        if item["source_label"] == "Q U E S T I O N S Q1"
    ]
    assert len(repeated_q1) == 6
    assert len({
        g._inventory_task_match_key(item) for item in repeated_q1
    }) == 6


def test_checkpoint_refresh_matches_mojibake_activity_rows_without_new_qids():
    """Old RNE checkpoints use generic Activity labels and damaged UTF-8."""
    opening = "In what way does this print（Fig．1） depict a utopian vision?"
    hubner = "Describe what you see in Fig. 17. What could Hübner mean?"
    saved = [
        {
            "qid": "QINV-0017",
            "source_kind": "activity",
            "source_label": "Activity",
            "raw_task": (
                "In what way does this printï¼ˆFigï¼Ž1ï¼‰ depict a "
                "utopian vision?"
            ),
        },
        {
            "qid": "QINV-0004",
            "source_kind": "checkpoint_question",
            "source_label": "Activity",
            "raw_task": (
                "Describe what you see in Fig. 17. What could HÃ¼bner mean?"
            ),
        },
    ]
    anchors = [
        {
            "source_kind": "checkpoint_question",
            "source_label": "Activity",
            "parent_source_label": "Activity",
            "raw_task": opening,
            "normalized_task": opening,
            "_activity_origin": True,
        },
        {
            "source_kind": "checkpoint_question",
            "source_label": "Activity",
            "parent_source_label": "Activity",
            "raw_task": hubner,
            "normalized_task": hubner,
            "_activity_origin": True,
        },
    ]

    merged = g._merge_source_task_anchors(saved, anchors)

    assert [item["qid"] for item in merged] == ["QINV-0017", "QINV-0004"]
    assert [item["raw_task"] for item in merged] == [opening, hubner]
    assert g._inventory_coverage_key(saved[0]["raw_task"]) == (
        g._inventory_coverage_key(opening))
    assert g._inventory_coverage_key(saved[1]["raw_task"]) == (
        g._inventory_coverage_key(hubner))


def test_mojibake_activity_question_becomes_a_trimmed_checkpoint_with_figure():
    clean_prompt = (
        "In what way does this print (Fig. 1) depict a utopian vision?"
    )
    damaged_prompt = clean_prompt.encode("utf-8").decode("latin-1")
    source = (
        "# The French Revolution\n"
        "\\begin{figure}\n"
        "\\includegraphics{https://example.test/fig-1.png}\n"
        "\\caption{Fig. 1 - Test print}\n"
        "\\end{figure}\n"
        "## Activity\n"
        f"{damaged_prompt}\n"
        "identifiable by a flag.\n"
    )

    sections = g.parse_mmd_sections(source)
    anchors = g._attach_explicit_figure_images(
        g._source_task_anchors(sections), sections)

    assert len(anchors) == 1
    anchor = anchors[0]
    assert anchor["source_kind"] == "checkpoint_question"
    assert anchor["_activity_origin"] is True
    assert "identifiable by a flag" not in anchor["raw_task"]
    assert anchor["image_urls"] == ["https://example.test/fig-1.png"]
    rendered = g._inventory_task_text(anchor)
    assert clean_prompt in rendered
    assert "HÃ" not in rendered
    assert '[img src="https://example.test/fig-1.png" ' in rendered


def test_canonical_rich_text_repairs_mojibake_in_existing_inventory_examples():
    clean_prompt = "What could Hübner mean?"
    damaged_prompt = clean_prompt.encode("utf-8").decode("latin-1")
    records = [{
        "concept_details": (
            "Description: d // Types: Type 01: Visual interpretation "
            "Case 01: Given a source visual, interpret its reference. "
            f"Example 01: {damaged_prompt}"
        ),
    }]

    out = g._canonicalize_concept_rich_text(records)

    assert clean_prompt in out[0]["concept_details"]
    assert "HÃ" not in out[0]["concept_details"]


def test_trimmed_checkpoint_anchor_replaces_a_longer_ocr_bleed_variant():
    prompt = "Discuss the importance of language in national identity."
    anchor = {
        "source_kind": "checkpoint_question",
        "source_label": "Discuss",
        "parent_source_label": "Discuss",
        "raw_task": prompt,
        "normalized_task": prompt,
        "_source_task_boundary": "direct_prompt",
    }
    merged = g._merge_source_task_anchors([
        {
            "qid": "QINV-0022",
            "source_kind": "checkpoint_question",
            "source_label": "Discuss",
            "raw_task": prompt + " National Assembly proclaimed a Republic.",
            "image_urls": ["https://example.test/unrelated-figure.png"],
            "requires_visual": True,
        }
    ], [anchor])

    assert len(merged) == 1
    assert merged[0]["qid"] == "QINV-0022"
    assert merged[0]["raw_task"] == prompt
    assert merged[0]["_source_task_boundary"] == "direct_prompt"
    assert merged[0]["image_urls"] == []
    assert not merged[0]["requires_visual"]


def test_uploaded_nationalism_fixture_exposes_sorrieu_opening_for_recovery():
    source = (
        Path(__file__).parents[1] / "data" / "Testing" / "RNE.mmd"
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
        Path(__file__).parents[1] / "data" / "Testing" / "jemh105 (1).mmd"
    ).read_text(encoding="utf-8")
    sections = [
        section
        for chunk in g._section_aware_chunks(source)
        for section in chunk["sections"]
    ]
    anchors = g._source_task_anchors(sections)
    # Parse-time list items carry the neutral kind; the outline/page model
    # verdict owns the real exercise-vs-intext classification (§3 purge).
    exercise_anchors = [
        item for item in anchors if item["source_kind"] == "intext_question"
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
        Path(__file__).parents[1] / "data" / "Testing" / "Class 10 Chapter 5 Electricity.mmd"
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
    assert all(re.search(
        r'\[img\s+src="https://[^"]+"\s+alt="[^"]+"\]', text)
               for text in rendered_visuals)
    assert all(
        not g.kr.rich_text_issues(g._compact_activity_hub_note(item))
        for item in activities
    )


def test_tracked_rne_anchors_keep_question_boundaries_and_exact_figures():
    sections, anchors = _attached_source_anchors("RNE.mmd")
    registry = g._source_figure_registry(sections)

    opening = next(
        item for item in anchors if "utopian vision" in item["raw_task"])
    assert "identifiable by the revolutionary tricolour" not in opening["raw_task"]
    _assert_exact_figure_attachment(opening, registry, ["1"])

    club_caricature = next(
        item for item in anchors
        if "What is the caricaturist trying to depict" in item["raw_task"])
    _assert_exact_figure_attachment(club_caricature, registry, ["6"])

    bismarck = next(
        item for item in anchors
        if "Bismarck and the elected deputies" in item["raw_task"])
    assert "Chief Minister Cavour" not in bismarck["raw_task"]
    _assert_exact_figure_attachment(bismarck, registry, ["13"])

    italy_map = next(
        item for item in anchors if "Look at Fig. 14(a)" in item["raw_task"])
    _assert_exact_figure_attachment(italy_map, registry, ["14(a)", "14(b)"])

    garibaldi = next(
        item for item in anchors
        if "artist has portrayed Garibaldi" in item["raw_task"])
    _assert_exact_figure_attachment(garibaldi, registry, ["15"])

    veit = next(
        item for item in anchors if "Veit's Germania" in item["raw_task"])
    _assert_exact_figure_attachment(veit, registry, ["17"])

    hubner = next(
        item for item in anchors if "Describe what you see in Fig. 17" in item["raw_task"])
    _assert_exact_figure_attachment(hubner, registry, ["17"])
    assert "19" not in g._figure_reference_ids(
        " ".join((hubner.get("_image_captions") or {}).values()))

    frankfurt = next(
        item for item in anchors if "citizen of Frankfurt" in item["raw_task"])
    _assert_exact_figure_attachment(frankfurt, registry, ["10"])


def test_tracked_math_ap_inventory_has_all_examples_and_exact_figure_questions():
    sections, anchors = _attached_source_anchors("jemh105 (1).mmd")
    registry = g._source_figure_registry(sections)
    worked_examples = [
        item for item in anchors if item["source_kind"] == "worked_example"
    ]
    assert [item["source_label"] for item in worked_examples] == [
        f"Example {number}" for number in range(1, 17)
    ]
    expected = {
        "EXERCISE 5.3 Q18": "5.4",
        "EXERCISE 5.3 Q19": "5.5",
        "EXERCISE 5.3 Q20": "5.6",
        "EXERCISE 5.4 (Optional)* Q3": "5.7",
        "EXERCISE 5.4 (Optional)* Q5": "5.8",
    }
    for source_label, figure_id in expected.items():
        item = next(item for item in anchors if item["source_label"] == source_label)
        _assert_exact_figure_attachment(item, registry, [figure_id])


def test_tracked_electricity_inventory_counts_and_activity_figure_sets():
    sections, anchors = _attached_source_anchors(
        "Class 10 Chapter 5 Electricity.mmd")
    registry = g._source_figure_registry(sections)
    assert len(g._topic_headings(sections)) == 8
    counts = {
        kind: sum(item["source_kind"] == kind for item in anchors)
        for kind in {item["source_kind"] for item in anchors}
    }
    assert counts == {
        "worked_example": 13,
        # In-text Questions blocks and final Exercises both carry the neutral
        # parse-time kind; the outline/page model verdict owns the split.
        "intext_question": 41,
        "checkpoint_question": 6,
    }
    assert [
        item["source_label"] for item in anchors
        if item["source_kind"] == "worked_example"
    ] == [f"Example 11.{number}" for number in range(1, 14)]

    expected = {
        # Fig. 11.3 is the worked result of the graphing activity, not an
        # input visual required by the learner task.
        "Activity 11.1": ["11.2"],
        "Activity 11.2": ["11.4"],
        "Activity 11.3": ["11.5"],
        "Activity 11.4": ["11.6"],
        "Activity 11.5": ["11.6", "11.8"],
        "Activity 11.6": ["11.10", "11.11"],
    }
    for source_label, figure_ids in expected.items():
        item = next(item for item in anchors if item["source_label"] == source_label)
        _assert_exact_figure_attachment(item, registry, figure_ids)

    example_11_7 = next(
        item for item in anchors if item["source_label"] == "Example 11.7"
    )
    _assert_exact_figure_attachment(example_11_7, registry, ["11.9"])


def test_tracked_electricity_activities_exclude_result_and_derivation_prose():
    _sections, anchors = _attached_source_anchors(
        "Class 10 Chapter 5 Electricity.mmd")
    activities = {
        item["source_label"]: item
        for item in anchors
        if item.get("_activity_origin")
    }

    assert "Plot a graph between" in activities["Activity 11.1"]["raw_task"]
    assert "In this Activity, you will find" not in (
        activities["Activity 11.1"]["raw_task"])
    activity_11_1 = g._inventory_task_text(activities["Activity 11.1"])
    assert "Fig. 11.3" not in activity_11_1
    assert "straight line" not in activity_11_1.lower()
    assert "this is Ohm's law" not in activity_11_1.lower()
    assert "In this Activity we observe" not in (
        activities["Activity 11.2"]["raw_task"])
    assert "It is observed that" not in (
        activities["Activity 11.3"]["raw_task"])
    assert r"\begin{table}" not in activities["Activity 11.3"]["raw_task"]
    assert "You will observe that" not in (
        activities["Activity 11.5"]["raw_task"])
    assert "It is observed that" not in (
        activities["Activity 11.6"]["raw_task"])
    assert all(
        not g.kr.rich_text_issues(g._inventory_task_text(item))
        for item in activities.values()
    )


def test_activity_prompt_stops_before_inline_observed_result():
    body = (
        "\\begin{itemize}\n"
        "\\item[-] Mix yeast with flour and warm water.\n"
        "\\item[-] Observe the dough after four hours.\n"
        "\\end{itemize}\n\n"
        "Did its volume or texture change? If not, wait a little longer. "
        "After some time, you may notice that the dough has risen and become "
        "fluffy. This happens because carbon dioxide is released."
    )

    prompt = g._trim_activity_ocr_bleed(body)

    assert "Did its volume or texture change?" in prompt
    assert "the dough has risen" not in prompt
    assert "carbon dioxide is released" not in prompt


def test_figure_panel_inheritance_does_not_capture_question_subparts():
    prompt = (
        "Use Fig. 11.9. Calculate (a) the total resistance, "
        "(b) the current, and (c) the potential difference."
    )

    assert g._figure_reference_ids(prompt) == ["11.9"]
    assert g._figure_reference_ids(
        "Compare Fig. 14(a) and (b), then explain the change."
    ) == ["14(a)", "14(b)"]


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


def test_inventory_coverage_equates_roman_subparts_with_rendered_bullets():
    inventory = {"items": [
        {
            "qid": "QINV-0001",
            "source_kind": "exercise",
            "raw_task": (
                "State true or false. Explain your reasoning. "
                "\\item[(i)] The cube of any odd number is even. "
                "\\item[(ii)] No perfect cube ends with 8."
            ),
        },
        {
            "qid": "QINV-0002",
            "source_kind": "exercise",
            "raw_task": (
                "Fill the pattern: \\item[] "
                "$1^{2}+2^{2}+2^{2}=3^{2}$."
            ),
        },
    ]}
    records = [{
        "topic": "Cubic Numbers",
        "concept_title": "Cube Patterns",
        "concept_details": (
            "Description: Cubes have reusable patterns. // Types: "
            "Type 01: Testing cube claims Case 01: Multiple claims "
            "Example 01: State true or false. Explain your reasoning. "
            "â€¢ The cube of any odd number is even. "
            "â€¢ No perfect cube ends with 8. "
            "Type 02: Completing patterns Case 01: Square-sum pattern "
            "Example 01: Fill the pattern: â€¢ "
            "[Katex] 1^{2}+2^{2}+2^{2}=3^{2} [/Katex]."
        ),
    }]

    assert g._rendered_inventory_coverage_defects(records, inventory) == {
        "missing": [],
        "duplicate": [],
    }


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

    def fake_openai(system, user, **_kwargs):
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

    # Without the image URL, strict Types validation sees the Figure and
    # rejects the missing canonical image rather than hiding the reference.
    no_image = [{"topic": "Electricity", "parent_concept": "Ohm's Law",
                 "concept_title": "Resistance",
                 "concept_details": details.replace(
                     " ![](https://cdn.mathpix.com/f11.jpg)", ""),
                 "keywords": ""}]
    report2 = concept_validator.validate_concept_rows(
        no_image, allow_types=True, strict_type_hierarchy=True)
    assert any(
        e["code"] == "figure_reference_without_image"
        for e in report2["errors"])


def test_truncated_example_lines_are_caught_by_coverage_not_by_length():
    """The truncation signal is the missing QID, not the short string.

    ``Example: q`` used to raise a word-count error. It now raises
    nothing in the validator; the source question it truncated is
    reported missing by the exact-once coverage contract, which knows
    what the book actually asked because it holds the inventory.
    """
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
    codes = {e["code"] for e in report["errors"]}
    assert not any("short" in code for code in codes), sorted(codes)

    inventory = {"items": [{
        "qid": "QINV-0001",
        "source_kind": "exercise",
        "raw_task": (
            "Calculate the resistance of a conductor carrying 0.5 A across "
            "a potential difference of 6 V."
        ),
    }]}
    assert g._rendered_inventory_coverage_defects(rows, inventory) == {
        "missing": ["QINV-0001"],
        "duplicate": [],
    }


def test_cleanup_preserves_figure_reference_for_same_example_image_validation():
    text = ("Calculate the resistance for the given circuit. (Refer fig. 11.1) "
            "![](https://cdn.mathpix.com/f11.jpg)")
    assert concept_cleanup.strip_dangling_references(text) == text
    assert concept_cleanup.neutralize_source_artifacts(text) == text
    # Without the image, keep the Figure ID so strict validation can reject
    # the missing tag instead of silently changing the question.
    bare = "Calculate the resistance for the given circuit shown in fig. 11.1."
    assert "fig. 11.1" in concept_cleanup.neutralize_source_artifacts(bare).lower()


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
    assert "Error Analysis: A real learner error." in out


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

def test_learner_analysis_via_api_replaces_generic_text(monkeypatch):
    def fake_openai(system, user, **kw):
        assert "Misconceptions" in system and "Error Analysis" in system
        return {"rows": [{
            "topic": "Triangles", "parent_concept": "Similarity",
            "concept": "Basic Proportionality Theorem",
            "concept_description": (
                "Description: unchanged\n"
                "Misconception/ Error Analysis: Misconceptions: Students may "
                "believe any line through two sides of a triangle creates "
                "proportional segments; Error Analysis: Students may apply "
                "the ratio to non-parallel cutting lines or form AD/DB and "
                "AE/EC without first checking that DE is parallel to BC."
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
    assert "any line through two sides" in details
    assert "non-parallel cutting lines" in details
    assert "Misconceptions:" in details
    assert "Error Analysis:" in details
    assert "Relates parallel lines and proportional segments." in details


def test_learner_analysis_via_api_fails_closed_when_no_row_is_usable(
    monkeypatch,
):
    monkeypatch.setattr(
        g,
        "_openai_json",
        lambda *args, **kwargs: {
            "rows": [{
                "topic": "Triangles",
                "parent_concept": "Similarity",
                "concept": "Basic Proportionality Theorem",
                "concept_description": "Description: unchanged",
                "keywords": "",
            }],
        },
    )
    records = [{
        "topic": "Triangles",
        "parent_concept": "Similarity",
        "concept_title": "Basic Proportionality Theorem",
        "concept_details": (
            "Description: Relates parallel lines and proportional segments."
        ),
        "keywords": "",
    }]

    with pytest.raises(
        RuntimeError,
        match="specific learner-analysis generation returned unusable rows",
    ):
        g._ensure_misconceptions_via_api(
            records, meta=g._metadata(subject="Math"))


def test_learner_analysis_via_api_retries_only_unresolved_rows(monkeypatch):
    titles = ["Fair Test", "Systematic Observation"]
    calls: list[dict] = []

    def analysis_row(title: str, *, valid: bool) -> dict:
        return {
            "concept": title,
            "misconception": (
                "Students may believe that one observation proves a "
                "universal pattern."
                if valid else "Students may find this difficult."
            ),
            "error_analysis": (
                "Students may omit the time and conditions when recording "
                "each trial."
                if valid else "Students may answer incorrectly."
            ),
        }

    def fake_openai(_system, user, **_kwargs):
        payload = json.loads(
            user.split(
                "Rows missing usable Misconceptions and/or Error Analysis "
                "sections:\n",
                1,
            )[1].split("\n\nVALIDATION FEEDBACK", 1)[0]
        )
        requested = [row["concept"] for row in payload["rows"]]
        calls.append({"requested": requested})
        if len(calls) == 1:
            return {"rows": [
                analysis_row(titles[0], valid=True),
                analysis_row(titles[1], valid=False),
            ]}
        return {"rows": [analysis_row(titles[1], valid=True)]}

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    records = [{
        "topic": "Investigation",
        "parent_concept": "Scientific Method",
        "concept_title": title,
        "concept_details": f"Description: {title}.",
        "keywords": "",
    } for title in titles]

    repaired = g._ensure_misconceptions_via_api(
        records, meta=g._metadata(subject="Science"), max_attempts=3)

    assert calls == [
        {"requested": titles},
        {"requested": [titles[1]]},
    ]
    assert all(
        not g._learner_analysis_needs_rewrite(row["concept_details"])
        for row in repaired
    )


def test_learner_analysis_action_only_retry_recovers_stubborn_error(
    monkeypatch,
):
    calls: list[str] = []

    def fake_openai(_system, user, **_kwargs):
        calls.append(user)
        if len(calls) < 4:
            error = (
                "Students may believe that consecutive odd numbers can be "
                "added in any order."
            )
        else:
            error = (
                "Students may miscalculate the running total by adding one "
                "odd term twice, producing a sum that no longer equals the "
                "cube."
            )
        return {"rows": [{
            "concept": "Cubes as Sums of Consecutive Odd Numbers",
            "misconception": (
                "Students may believe every odd number is itself a perfect "
                "cube."
            ),
            "error_analysis": error,
        }]}

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    records = [{
        "topic": "Cubic Numbers",
        "parent_concept": "Odd-number Patterns",
        "concept_title": "Cubes as Sums of Consecutive Odd Numbers",
        "concept_details": (
            "Description: A cube can be represented by an appropriate block "
            "of consecutive odd numbers."
        ),
        "keywords": "",
    }]

    repaired = g._ensure_misconceptions_via_api(
        records, meta=g._metadata(subject="Mathematics"))

    assert len(calls) == 4
    assert all(
        "ACTION-ONLY ERROR_ANALYSIS CONTRACT" in call
        for call in calls
    )
    assert not g._learner_analysis_needs_rewrite(
        repaired[0]["concept_details"])
    assert "adding one odd term twice" in repaired[0]["concept_details"]


def test_terminal_validation_rejects_both_title_substitution_fallbacks():
    """The refine pass never authors the title-substitution filler anymore
    (a missing analysis stays missing), and the terminal gate still rejects
    both filler shapes wherever they appear."""
    records = cr.ensure_analysis_sections([{
        "topic": "Inquiry",
        "parent_concept": "Scientific Method",
        "concept_title": "Science as Evolving Inquiry",
        "concept_details": (
            "Description: Scientific explanations change when evidence changes."
        ),
        "keywords": "",
    }])
    assert cr.analysis_components(records[0]["concept_details"]) == ("", "")
    assert "Misconception" not in records[0]["concept_details"]

    title = "Science as Evolving Inquiry"
    # The deterministic filler generators are deleted from the codebase;
    # the terminal gate still rejects their historical output shapes when a
    # legacy row carries them.
    assert not hasattr(cr, "_fallback_misconception")
    assert not hasattr(cr, "_fallback_error_analysis")
    misconception = (
        f"Students may assume {title} is a rule that always applies "
        "without checking its conditions, context, or representation."
    )
    error_analysis = (
        f"Students may apply {title} as a memorized rule without checking "
        "the conditions, context, or representation given in the problem."
    )
    assert concept_validator.is_terminal_generic_analysis_filler(
        misconception)
    assert concept_validator.is_terminal_generic_analysis_filler(
        error_analysis)
    report = concept_validator.validate_concept_rows(
        [{
            "topic": "Inquiry",
            "parent_concept": "Scientific Method",
            "concept_title": title,
            "concept_details": (
                "Description: Scientific explanations change when evidence "
                "changes. // Misconception/ Error Analysis: "
                f"Misconceptions: {misconception}; "
                f"Error Analysis: {error_analysis}"
            ),
            "keywords": "",
        }],
        strict_analysis_section=True,
    )
    assert {
        error["code"] for error in report["errors"]
    }.issuperset({
        "generic_misconception",
        "generic_error_analysis",
    })


def test_authored_one_sided_analysis_gets_no_deterministic_filler():
    """Under the rewrite, either authored section alone is complete: the
    refine pass must never pad the other side with templated filler (the
    exact text the terminal gate rejects)."""
    ea_only = {
        "topic": "Electric Current",
        "parent_concept": "Circuits",
        "concept_title": "Using an Ammeter to Measure Current",
        "concept_details": (
            "Description: An ammeter measures the current through a circuit "
            "component and must be inserted in series. // "
            "Misconception/ Error Analysis: Error Analysis: The learner "
            "connects the ammeter in parallel across the bulb instead of "
            "placing it in series with the component."
        ),
        "keywords": "",
    }
    missing = {
        "topic": "Electric Current",
        "parent_concept": "Circuits",
        "concept_title": "Electric Circuits Carry Energy",
        "concept_details": (
            "Description: A circuit is a closed conducting path that carries "
            "electrical energy to components."
        ),
        "keywords": "",
    }

    refined = cr.refine_chapter([dict(ea_only), dict(missing)])

    assert "Students may apply" not in refined[0]["concept_details"]
    assert "Students may assume" not in refined[0]["concept_details"]
    assert "connects the ammeter in parallel" in refined[0]["concept_details"]
    report = concept_validator.validate_concept_rows(
        [refined[0]], strict_analysis_section=True)
    assert not {
        "generic_misconception", "generic_error_analysis",
        "analysis_section_format", "missing_learner_analysis",
    } & {error["code"] for error in report["errors"]}
    # A wholly missing analysis stays missing — authoring it is model work.
    assert "Misconception" not in refined[1]["concept_details"]


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


def test_mined_activity_role_is_case_scoped_by_authoritative_inventory_qids():
    inventory = {"items": [
        {
            "qid": "QINV-0001",
            "source_kind": "short_answer",
            "topic_hint": "Scientific Investigation",
            "raw_task": "Why is one side of the puri thinner?",
        },
        {
            "qid": "QINV-0002",
            "source_kind": "experiment_task",
            "topic_hint": "Scientific Investigation",
            "raw_task": "Measure the time taken for the puri to puff.",
        },
    ]}
    raw_types = [{
        "type_id": "TYPE-0001",
        "type_title": "Investigating puri puffing",
        "type_description": "Use observations to investigate puffing.",
        "task_pattern": "Explain or test one aspect of puri puffing.",
        "concept_match_hint": "Controlled Investigation",
        "topic_match_hint": "Scientific Investigation",
        "is_activity": True,
        "source_question_ids": ["QINV-0001", "QINV-0002"],
        "case_prompts": [
            {
                "case_id": "CASE-0001",
                "case_title": "Explain unequal thickness",
                "examples": [{
                    "source_question_id": "QINV-0001",
                    "example_prompt": "Why is one side of the puri thinner?",
                }],
            },
            {
                "case_id": "CASE-0002",
                "case_title": "Measure puffing time",
                "examples": [{
                    "source_question_id": "QINV-0002",
                    "example_prompt": (
                        "Measure the time taken for the puri to puff."),
                }],
            },
        ],
    }]

    normalized = g._normalize_mined_type_candidate(raw_types, inventory)

    assert len(normalized) == 1
    assert normalized[0]["source_question_ids"] == [
        "QINV-0001", "QINV-0002",
    ]
    assert [
        (
            case["is_activity"],
            g._assignment_case_qids(case),
        )
        for case in normalized[0]["case_prompts"]
    ] == [
        (False, ["QINV-0001"]),
        (True, ["QINV-0002"]),
    ]
    assert not g._uncovered_inventory_items(inventory, normalized)
    assert not g._duplicate_inventory_assignments(inventory, normalized)


def test_diagram_interpretation_hint_requires_an_owned_visual_item():
    puri_prompt = (
        "Have you noticed how a puri or a batura puffs up when placed in hot "
        "oil? Or how a phulka swells when put directly on the flame."
    )
    diagram_prompt = "Interpret the labelled diagram of the experimental setup."
    inventory = {"items": [
        {
            "qid": "QINV-0001",
            "source_kind": "intext_question",
            "topic_hint": "Scientific Investigation",
            "raw_task": puri_prompt,
            "requires_visual": False,
        },
        {
            "qid": "QINV-0002",
            "source_kind": "diagram_task",
            "topic_hint": "Scientific Investigation",
            "raw_task": diagram_prompt,
            "requires_visual": True,
        },
    ]}

    def mined_type(qid: str, prompt: str) -> dict:
        return {
            "type_id": f"TYPE-{qid}",
            "type_title": f"Interpreting {qid}",
            "type_description": "Interpret the supplied source evidence.",
            "task_pattern": "Interpret the supplied evidence.",
            "concept_match_hint": f"Concept for {qid}",
            "topic_match_hint": "Scientific Investigation",
            "subject_skill_hint": "Diagram Interpretation",
            "source_question_ids": [qid],
            "case_prompts": [{
                "case_id": f"CASE-{qid}",
                "case_title": f"Case for {qid}",
                "examples": [{
                    "source_question_id": qid,
                    "example_prompt": prompt,
                }],
            }],
        }

    normalized = g._normalize_mined_type_candidate(
        [
            mined_type("QINV-0001", puri_prompt),
            mined_type("QINV-0002", diagram_prompt),
        ],
        inventory,
    )

    by_qid = {
        item["source_question_ids"][0]: item
        for item in normalized
    }
    assert by_qid["QINV-0001"]["subject_skill_hint"] == ""
    assert (
        by_qid["QINV-0002"]["subject_skill_hint"]
        == "Diagram Interpretation"
    )
    assert (
        by_qid["QINV-0001"]["case_prompts"][0]["examples"][0][
            "example_prompt"
        ]
        == puri_prompt
    )


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


def test_certified_split_type_cases_are_qualified_without_moving_examples():
    assert g._safe_type_case_qualifier(
        "Social Case 01: Type 02: comparison // Worked Example: powers, "
        "Example 100: reading and Examples: recap"
    ) == (
        "Social Case 01 Type 02 comparison / Worked Example powers, "
        "Example 100 reading and Examples recap"
    )

    type_title = "Converting Geological Ages from Years to Seconds"
    case_title = (
        "Given the complete source context, completing time-scale table "
        "relates geological ages to powers under its stated conditions"
    )
    fossil_prompt = (
        "A fossil is 15 million years old. Express this age in seconds."
    )
    plant_prompt = (
        "Plants appeared 470 million years ago. Express this age in seconds."
    )
    records = [
        {
            "topic": "Did You Ever Wonder?",
            "parent_concept": "Scientific Notation",
            "concept_title": (
                "Calculating real-world quantities with scientific notation"
            ),
            "concept_details": (
                "Description: Scientific notation supports large time-scale "
                "calculations. // Types: "
                f"Type 51: {type_title} Case 01: {case_title} "
                f"Example 01: {plant_prompt}"
            ),
            "keywords": "scientific notation, time",
        },
        {
            "topic": "Did You Ever Wonder?",
            "parent_concept": "Powers of Ten",
            "concept_title": "Interpreting powers of ten as time scales",
            "concept_details": (
                "Description: Powers of ten express geological ages "
                "compactly. // Types: "
                f"Type 51: {type_title} Case 01: {case_title} "
                f"Example 01: {fossil_prompt}"
            ),
            "keywords": "powers of ten, geological age",
        },
    ]
    inventory = {"items": [
        {
            "qid": "QINV-0085",
            "source_kind": "exercise",
            "topic_hint": "Did You Ever Wonder?",
            "raw_task": fossil_prompt,
        },
        {
            "qid": "QINV-0086",
            "source_kind": "exercise",
            "topic_hint": "Did You Ever Wonder?",
            "raw_task": plant_prompt,
        },
    ]}
    mined_types = {"types": [{
        "type_id": "TYPE-0051",
        "type_title": type_title,
        "topic_match_hint": "Did You Ever Wonder?",
        "source_question_ids": ["QINV-0085", "QINV-0086"],
        "case_prompts": [
            {
                "case_id": "CASE-0078",
                "case_title": case_title,
                "case_signature": (
                    "Fossil age of 15 million years converted to seconds"
                ),
                "source_question_ids": ["QINV-0085"],
                "examples": [{
                    "source_question_id": "QINV-0085",
                    "example_prompt": fossil_prompt,
                }],
            },
            {
                "case_id": "CASE-0079",
                "case_title": case_title,
                "case_signature": (
                    "Plant age of 470 million years converted to seconds"
                ),
                "source_question_ids": ["QINV-0086"],
                "examples": [{
                    "source_question_id": "QINV-0086",
                    "example_prompt": plant_prompt,
                }],
            },
        ],
    }]}
    g._reset_placement_certifications(mined_types)
    g._certify_inventory_host(
        mined_types,
        "QINV-0085",
        records[1],
        basis="type_host_review",
    )
    g._certify_inventory_host(
        mined_types,
        "QINV-0086",
        records[0],
        basis="type_host_review",
    )
    mined_before = json.loads(json.dumps(mined_types))
    body_before = [
        g._types_body(record["concept_details"]) for record in records
    ]
    example_suffix_before = [
        re.search(r"\bExample\s+\d{1,2}:.*", body, re.DOTALL).group(0)
        for body in body_before
    ]
    validation_args = {
        "allow_types": True,
        "allowed_source_examples": [fossil_prompt, plant_prompt],
        "strict_type_hierarchy": True,
    }
    before_report = concept_validator.validate_concept_rows(
        records, **validation_args)
    assert [
        error for error in before_report["errors"]
        if error["code"] == "duplicate_type_definition"
    ]
    assert g._rendered_inventory_coverage_defects(records, inventory) == {
        "missing": [],
        "duplicate": [],
    }
    assert not g._placement_certification_violations(
        records, inventory, mined_types)

    unproven_mined_types = json.loads(json.dumps(mined_types))
    unproven_cases = unproven_mined_types["types"][0]["case_prompts"]
    unproven_cases[1]["case_id"] = unproven_cases[0]["case_id"]
    unproven_cases[1]["case_signature"] = unproven_cases[0]["case_signature"]
    assert g._disambiguate_certified_split_type_cases(
        records, inventory, unproven_mined_types) == records

    out = g._disambiguate_certified_split_type_cases(
        records, inventory, mined_types)

    after_report = concept_validator.validate_concept_rows(
        out, **validation_args)
    assert not {
        "missing_type_definition",
        "generic_type_definition",
        "duplicate_type_definition",
    } & {
        error["code"] for error in after_report["errors"]
        if error["severity"] == "error"
    }
    assert g._types_body(out[0]["concept_details"]) == body_before[0]
    assert (
        f"Case 01: {case_title} — "
        "Interpreting powers of ten as time scales"
        in g._types_body(out[1]["concept_details"])
    )
    assert (
        f"Type 51: {type_title} Case 01:"
        in g._types_body(out[1]["concept_details"])
    )
    example_suffix_after = [
        re.search(
            r"\bExample\s+\d{1,2}:.*",
            g._types_body(record["concept_details"]),
            re.DOTALL,
        ).group(0)
        for record in out
    ]
    assert example_suffix_after == example_suffix_before
    assert g._rendered_inventory_coverage_defects(out, inventory) == {
        "missing": [],
        "duplicate": [],
    }
    assert not g._placement_certification_violations(
        out, inventory, mined_types)
    assert mined_types == mined_before
    assert records[1]["concept_details"] != out[1]["concept_details"]
    assert g._disambiguate_certified_split_type_cases(
        out, inventory, mined_types) == out

    third_prompt = (
        "A meteorite is 65 million years old. Express this age in seconds."
    )
    three_records = [dict(record) for record in records]
    three_records.append({
        **records[1],
        "concept_title": "Comparing powers of ten across time scales",
        "concept_details": records[1]["concept_details"].replace(
            fossil_prompt, third_prompt),
    })
    three_inventory = json.loads(json.dumps(inventory))
    three_inventory["items"].append({
        "qid": "QINV-0087",
        "source_kind": "exercise",
        "topic_hint": "Did You Ever Wonder?",
        "raw_task": third_prompt,
    })
    three_mined_types = json.loads(json.dumps(mined_types))
    three_type = three_mined_types["types"][0]
    three_type["source_question_ids"].append("QINV-0087")
    repeated_case = json.loads(json.dumps(three_type["case_prompts"][0]))
    repeated_case["source_question_ids"] = ["QINV-0087"]
    repeated_case["examples"] = [{
        "source_question_id": "QINV-0087",
        "example_prompt": third_prompt,
    }]
    three_type["case_prompts"].append(repeated_case)
    g._reset_placement_certifications(three_mined_types)
    for qid, host in (
        ("QINV-0085", three_records[1]),
        ("QINV-0086", three_records[0]),
        ("QINV-0087", three_records[2]),
    ):
        g._certify_inventory_host(
            three_mined_types,
            qid,
            host,
            basis="type_host_review",
        )

    three_out = g._disambiguate_certified_split_type_cases(
        three_records, three_inventory, three_mined_types)

    assert three_out[1]["concept_details"] != three_records[1][
        "concept_details"
    ]
    assert three_out[2]["concept_details"] == three_records[2][
        "concept_details"
    ]
    three_report = concept_validator.validate_concept_rows(
        three_out,
        allow_types=True,
        allowed_source_examples=[
            fossil_prompt, plant_prompt, third_prompt],
        strict_type_hierarchy=True,
    )
    assert any(
        error["code"] == "duplicate_type_definition"
        for error in three_report["errors"]
    )

    renumbered = cr.renumber_types_continuously(out)

    first_body = g._types_body(renumbered[0]["concept_details"])
    second_body = g._types_body(renumbered[1]["concept_details"])
    assert f"Type 01: {type_title} Case 01:" in first_body
    assert (
        f"Type 01: {type_title} Case 02: {case_title} — "
        "Interpreting powers of ten as time scales"
        in second_body
    )


def test_activity_hub_fallback_never_crosses_topics_without_normal_host():
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
    assert not cr.activity_hub_body(out[1]["concept_details"])


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
    assert "image URL(s)" in inventory
    assert "never truncate" in inventory
    embedding = g.prompts.get_text("concepts.type_embedding.system")
    assert "is_activity" in embedding
    assert "Respect chapter position" in embedding
    assert "heating-effect" in embedding


def test_neutralize_preserves_compact_fig_refs_for_strict_image_validation():
    """OCR compact Figure IDs must remain visible to the strict image check."""
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
    assert "fig.11.5" in out["concept_details"].lower()
    strict_report = concept_validator.validate_concept_rows(
        [out], allow_types=True, strict_type_hierarchy=True)
    assert any(
        error["code"] == "figure_reference_without_image"
        for error in strict_report["errors"]
    )


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


def test_prompts_require_opening_granularity_and_canonical_media_policy():
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
    assert "Do NOT embed image URLs in Description" in refine
    assert "truncated mid-sentence" in refine
    assert "Preserve any existing Activity/Info Hub" in refine
    inventory = g.prompts.get_text("concepts.question_task_inventory.system")
    assert "numbered parent question" in inventory
    assert "independently answerable" in inventory
    assert "dependent subparts" in inventory
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
        item for item in anchors
        if item["source_kind"] == "intext_question"
    ]
    assert len(checkpoints) == 14
    assert len(exercises) == 11
    assert all(item["topic_hint"] for item in checkpoints)
    assert all(not item["topic_hint"] for item in exercises)


def test_headingless_intro_chapter_uses_selected_chapter_as_its_topic():
    chapter_title = "Exploring the Investigative World of Science"
    sections = g.parse_mmd_sections(
        "Science begins with careful questions and systematic investigation.\n"
        "Continue observing, measuring, and revising explanations.\n"
    )

    assert g._topic_headings(sections) == []
    assert g._apply_headingless_chapter_topic_fallback(
        sections, chapter_title)
    assert g._topic_headings(sections) == [chapter_title]
    assert {
        topic for topic, _section in g._sections_with_source_topics(sections)
    } == {chapter_title}


def test_closing_slogan_heading_is_structure_not_vocabulary_filtered():
    """A chapter whose only heading is a closing slogan keeps that structural
    heading: no keyword vocabulary judges it a non-topic, so the headingless
    fallback does not fire and any renaming is a model/outline decision."""
    chapter_title = "Exploring the Investigative World of Science"
    sections = g.parse_mmd_sections(
        "Science begins with careful questions and systematic investigation.\n"
        "\\section*{Happy investigating!}\n"
        "Continue observing, measuring, and revising explanations.\n"
    )

    assert g._topic_headings(sections) == ["Happy investigating!"]
    assert not g._apply_headingless_chapter_topic_fallback(
        sections, chapter_title)


def test_headingless_scientific_investigation_prose_has_exact_method_anchors():
    source = (
        "We can do simple experiments to answer focused questions.\n\n"
        "What are the different things that may change the way a puri puffs "
        "up when fried? For that, we try and find out what all can we change "
        "or control when we do the experiment, and what all can we observe to "
        "see if these changes made any difference.\n\n"
        "However, to make sense of the changes, we also need to think of what "
        "we can observe or measure. Maybe we can start by checking whether the "
        "puri puffs up (yes/no), or we can measure the time it takes to puff "
        "up (seconds). We can check whether a very thick layer of dough still "
        "gives a thin side to the puri.\n\n"
        "Further, while doing such experiments, it is better to change only "
        "one thing at a time while keeping the other conditions same. For "
        "example, if we wanted to see the effect of boiling hot, hot, and not "
        "very hot oil, we would\nuse circles of dough of the same thickness, "
        "and drop them in the same way. It is also a good idea to keep notes "
        "of everything that you see and sense when doing an experiment. Did "
        "the oil splatter, smell, or smoke? Do puris puff better when made "
        "fresh or from stored dough? What happens if I prick a hole in the "
        "puri before frying?\n\n"
        "This is the idea of systematic investigation.\n"
    )

    anchors = g._source_task_anchors(g.parse_mmd_sections(source))

    assert len(anchors) == 9
    assert all(item["source_kind"] == "experiment_task" for item in anchors)
    assert [item["source_label"] for item in anchors] == [
        "Scientific investigation: variable question",
        "Scientific investigation: controls and observations",
        "Scientific investigation: observation and measurement",
        "Scientific investigation: measuring an outcome",
        "Scientific investigation: testing a changed condition",
        "Scientific investigation: controlled variable comparison",
        "Scientific investigation: recording observations",
        "Scientific investigation: fresh and stored samples",
        "Scientific investigation: changing the sample",
    ]
    assert anchors[2]["raw_task"] == (
        "However, to make sense of the changes, we also need to think of what "
        "we can observe or measure."
    )
    assert anchors[5]["raw_task"] == (
        "For example, if we wanted to see the effect of boiling hot, hot, and "
        "not very hot oil, we would use circles of dough of the same thickness, "
        "and drop them in the same way."
    )
    assert anchors[6]["raw_task"] == (
        "It is also a good idea to keep notes of everything that you see and "
        "sense when doing an experiment. Did the oil splatter, smell, or smoke?"
    )


def _headingless_overview_and_method_mmd():
    return (
        "We explore everyday life-like why does dough rise? - and bigger "
        "mysteries like is the world getting warmer?\n\n"
        "But what does our body need to stay healthy? How do we fight these "
        "infections? We will investigate these ideas.\n\n"
        "Isn't it fascinating that calendars which determine our routines are "
        "linked to objects beyond our planet?\n\n"
        "Let us return to a question asked earlier: Why is one side of a puri "
        "thinner than the other?\n\n"
        "A kitchen is a place to observe and ask what happens if...? Have you "
        "noticed how a puri or a batura puffs up when placed in hot oil? Or "
        "how a phulka swells when put directly on the flame. Why does it puff "
        "up like a balloon? And why is one side thinner than the other?\n\n"
        "We can do simple experiments to answer focused questions. What are "
        "the different things that may change the way a puri puffs up when "
        "fried? For that, we find out what all can we change or control when "
        "we do the experiment, and what all can we observe to see if these "
        "changes made any difference.\n\n"
        "However, to make sense of the changes, we also need to think of what "
        "we can observe or measure. Maybe we can start by checking whether the "
        "puri puffs up (yes/no), or we can measure the time it takes to puff "
        "up (seconds). We can check whether a very thick layer of dough still "
        "gives a thin side to the puri.\n\n"
        "It is better to change only one thing at a time. For example, if we "
        "wanted to see the effect of boiling hot, hot, and not very hot oil, "
        "we would use circles of dough of the same thickness, and drop them in "
        "the same way. It is also a good idea to keep notes of everything that "
        "you see and sense when doing an experiment. Did the oil splatter, "
        "smell, or smoke? Do puris puff better when made fresh or from stored "
        "dough? What happens if I prick a hole in the puri before frying?\n\n"
        "This is the idea of systematic investigation.\n"
    )


def test_headingless_science_overview_recovers_only_exact_opening_prompts():
    prompts = g._headingless_overview_prose_prompts(
        _headingless_overview_and_method_mmd())

    assert [item["task"] for item in prompts] == [
        "why does dough rise?",
        "is the world getting warmer?",
        "what does our body need to stay healthy?",
        "How do we fight these infections?",
        "Why is one side of a puri thinner than the other?",
        (
            "Have you noticed how a puri or a batura puffs up when placed in "
            "hot oil? Or how a phulka swells when put directly on the flame."
        ),
        "Why does it puff up like a balloon?",
    ]
    assert len({item["label"] for item in prompts}) == 7
    assert all("fascinating" not in item["task"] for item in prompts)
    assert all("what happens if" not in item["task"] for item in prompts)


def test_headingless_science_source_has_seven_overview_and_nine_method_anchors():
    anchors = g._source_task_anchors(
        g.parse_mmd_sections(_headingless_overview_and_method_mmd()))

    assert len(anchors) == 16
    assert [
        item["source_kind"] for item in anchors[:7]
    ] == ["intext_question"] * 7
    assert [
        item["source_kind"] for item in anchors[7:]
    ] == ["experiment_task"] * 9
    assert all(
        item["_source_task_boundary"] == "direct_prompt"
        for item in anchors
    )


def test_headingless_overview_merge_restores_seven_without_split_or_callback():
    topic = "Exploring the Investigative World of Science"
    model_items = [
        {
            "qid": "QINV-0000",
            "source_kind": "short_answer",
            "source_label": "",
            "topic_hint": topic,
            "raw_task": (
                "These may range from everyday life-like why does dough rise? "
                "- to the bigger mysteries of Earth and beyond like is the "
                "world getting warmer?"
            ),
            "normalized_task": (
                "Identify scientific questions arising from everyday "
                "phenomena and global changes."
            ),
        },
        {
            "qid": "QINV-0001",
            "source_kind": "intext_question",
            "source_label": "Chapter opening prompt",
            "topic_hint": topic,
            "raw_task": "Why is one side of a puri thinner than the other?",
            "normalized_task": (
                "Explain why one side of a puri may be thinner than the other."
            ),
        },
        {
            "qid": "QINV-0002",
            "source_kind": "intext_question",
            "source_label": "Investigation prompt",
            "topic_hint": topic,
            "raw_task": (
                "Have you noticed how a puri or a batura puffs up when placed "
                "in hot oil?"
            ),
            "normalized_task": (
                "Observe how a puri or batura puffs up in hot oil."
            ),
        },
        {
            "qid": "QINV-0003",
            "source_kind": "intext_question",
            "source_label": "Investigation prompt",
            "topic_hint": topic,
            "raw_task": (
                "Or how a phulka swells when put directly on the flame."
            ),
            "normalized_task": (
                "Observe how a phulka swells when placed on a flame."
            ),
        },
        {
            "qid": "QINV-0004",
            "source_kind": "intext_question",
            "source_label": "Investigation prompt",
            "topic_hint": topic,
            "raw_task": "Why does it puff up like a balloon?",
            "normalized_task": (
                "Explain why the puri puffs up like a balloon."
            ),
        },
        {
            "qid": "QINV-0005",
            "source_kind": "intext_question",
            "source_label": "Investigation prompt",
            "topic_hint": topic,
            "raw_task": "And why is one side thinner than the other?",
            "normalized_task": (
                "Explain why the two sides of the puri have unequal thickness."
            ),
            "shared_context": (
                "The prompt concerns the uneven thickness observed in a puri."
            ),
        },
    ]
    sections = g.parse_mmd_sections(_headingless_overview_and_method_mmd())
    assert g._apply_headingless_chapter_topic_fallback(sections, topic)
    overview_anchors = [
        item for item in g._source_task_anchors(sections)
        if item["parent_source_label"] == "Chapter opening"
    ]

    merged = g._merge_source_task_anchors(model_items, overview_anchors)

    assert len(merged) == 7
    assert sum(
        "one side of a puri thinner" in item["raw_task"].lower()
        for item in merged
    ) == 1
    assert all(
        item["raw_task"] != "And why is one side thinner than the other?"
        for item in merged
    )
    assert "QINV-0000" not in {item.get("qid") for item in merged}
    assert sum(
        item["raw_task"].startswith("Have you noticed")
        and "Or how a phulka" in item["raw_task"]
        for item in merged
    ) == 1
    assert "QINV-0001" in {item.get("qid") for item in merged}


def test_direct_prompt_callback_cleanup_requires_context_and_matching_scope():
    full = "Why is one side of a puri thinner than the other?"
    short = "And why is one side thinner than the other?"
    topic = "Exploring the Investigative World of Science"
    items = [
        {
            "qid": "QINV-0001",
            "topic_hint": topic,
            "raw_task": full,
            "normalized_task": full,
        },
        {
            "qid": "QINV-0002",
            "topic_hint": topic,
            "raw_task": short,
            "normalized_task": short,
        },
        {
            "qid": "QINV-0003",
            "topic_hint": "Geometry",
            "raw_task": short,
            "normalized_task": (
                "Explain why the two sides of the puri have unequal thickness."
            ),
        },
    ]
    anchor = {
        "source_kind": "intext_question",
        "source_label": "Chapter opening: puri side thickness",
        "topic_hint": topic,
        "raw_task": full,
        "normalized_task": full,
        "_source_task_boundary": "direct_prompt",
    }

    merged = g._merge_source_task_anchors(items, [anchor])

    assert {item.get("qid") for item in merged} == {
        "QINV-0001", "QINV-0002", "QINV-0003",
    }


def test_scientific_investigation_backstop_preserves_finer_model_splits():
    source = (
        "We can do simple experiments to answer focused questions.\n\n"
        "However, to make sense of the changes, we also need to think of what "
        "we can observe or measure.\n\n"
        "It is better to change only one thing at a time. For example, if we "
        "wanted to see the effect of boiling hot, hot, and not very hot oil, "
        "we would use circles of dough of the same thickness, and drop them in "
        "the same way. It is also a good idea to keep notes of everything that "
        "you see and sense when doing an experiment. Did the oil splatter, "
        "smell, or smoke?\n\n"
        "This is the idea of systematic investigation.\n"
    )
    model_items = [
        {
            "source_kind": "observation_task",
            "source_label": "Chapter opening investigation",
            "raw_task": (
                "Have you noticed how a puri puffs up when placed in hot oil?"
            ),
            "normalized_task": (
                "Have you noticed how a puri puffs up when placed in hot oil?"
            ),
        },
        {
            "source_kind": "short_answer",
            "source_label": "Chapter opening investigation",
            "raw_task": "Why does it puff up like a balloon?",
            "normalized_task": "Why does it puff up like a balloon?",
        },
        {
            "source_kind": "short_answer",
            "source_label": "Chapter opening investigation",
            "raw_task": "And why is one side thinner than the other?",
            "normalized_task": "And why is one side thinner than the other?",
        },
        {
            "source_kind": "experiment_task",
            "source_label": "Chapter opening investigation",
            "raw_task": "Did the oil splatter, smell, or smoke?",
            "normalized_task": "Did the oil splatter, smell, or smoke?",
        },
    ]
    for index, item in enumerate(model_items, start=1):
        item["qid"] = f"QINV-{index:04d}"
        item["order_index"] = index

    sections = g.parse_mmd_sections(source)
    anchors = g._source_task_anchors(sections)
    merged = g._merge_source_task_anchors(model_items, anchors)

    assert len(merged) == 6
    assert all(
        any(
            item["raw_task"] == task
            for item in merged
        )
        for task in (
            "Have you noticed how a puri puffs up when placed in hot oil?",
            "Why does it puff up like a balloon?",
            "And why is one side thinner than the other?",
        )
    )
    assert sum(
        item["raw_task"].endswith("Did the oil splatter, smell, or smoke?")
        for item in merged
    ) == 1
    assert any(
        item["raw_task"].startswith(
            "It is also a good idea to keep notes of everything")
        for item in merged
    )
    assert [
        item["source_label"] for item in merged[-3:]
    ] == [
        "Scientific investigation: observation and measurement",
        "Scientific investigation: controlled variable comparison",
        "Scientific investigation: recording observations",
    ]

    refreshed = g._refresh_inventory_from_source_anchors(
        {"items": model_items, "stats": {}},
        sections,
    )
    assert len(refreshed["items"]) == 6
    assert len({
        item["qid"] for item in refreshed["items"]
    }) == 6
    assert {
        f"QINV-{index:04d}" for index in range(1, 5)
    }.issubset({
        item["qid"] for item in refreshed["items"]
    })


def test_direct_prompt_anchor_removes_its_contained_model_fragment():
    full = (
        "Maybe we can start by checking whether the puri puffs up (yes/no), "
        "or we can measure the time it takes to puff up (seconds)."
    )
    fragment = "or we can measure the time it takes to puff up (seconds)."
    model_items = [
        {
            "source_kind": "experiment_task",
            "source_label": "Scientific investigation: measuring an outcome",
            "raw_task": full,
            "normalized_task": full,
            "topic_hint": "Exploring the Investigative World of Science",
        },
        {
            "source_kind": "experiment_task",
            "source_label": "Chapter opening",
            "raw_task": fragment,
            "normalized_task": (
                "Measure the time taken by the puri to puff up in seconds."
            ),
            "topic_hint": "Exploring the Investigative World of Science",
        },
    ]
    anchor = {
        "source_kind": "experiment_task",
        "source_label": "Scientific investigation: measuring an outcome",
        "raw_task": full,
        "normalized_task": full,
        "topic_hint": "Exploring the Investigative World of Science",
        "_source_task_boundary": "direct_prompt",
    }

    merged = g._merge_source_task_anchors(model_items, [anchor])

    assert len(merged) == 1
    assert merged[0]["raw_task"] == full


def test_activity_hub_note_keeps_task_sentences_before_optional_context():
    question = "Did the oil splatter, smell, or smoke?"
    item = {
        "qid": "QINV-0015",
        "source_kind": "experiment_task",
        "source_label": "Scientific investigation: recording observations",
        "shared_context": (
            "The task refers to recording observations during the "
            "puri-frying experiment."
        ),
        "raw_task": (
            "It is also a good idea to keep notes of everything that you see "
            f"and sense when doing an experiment. {question}"
        ),
    }

    note = g._compact_activity_hub_note(item)

    assert question in note
    assert "recording observations during the puri-frying experiment" in note
    assert not note.endswith("?.")
    assert "…" not in note


def test_activity_hub_note_does_not_split_at_fig_or_clip_project_steps():
    task = (
        "Look at Fig. 14(a). Compare it with Fig. 14(b). "
        "Collect nationalist symbols from one country outside Europe. "
        "Explain their historical setting and present your comparison."
    )
    item = {
        "qid": "QINV-PROJECT-1",
        "source_kind": "activity",
        "source_label": "Project",
        "raw_task": task,
        "normalized_task": task,
    }

    note = g._compact_activity_hub_note(item)

    assert task in note
    assert note.count(task) == 1
    assert "…" not in note


def test_grade8_math_callouts_exercises_and_answer_key_boundary():
    source = (
        "\\section*{A Square and a Cube}\n"
        "A minister describes the one-hundred-locker puzzle.\n"
        "\\begin{itemize}\n"
        "\\item[] ? Before the process begins, how can Khoisnam know which "
        "lockers remain open?\n"
        "Hint: Find how many times each locker is toggled.\n"
        "\\end{itemize}\n"
        "If a locker is toggled an odd number of times it remains open. This "
        "paragraph explains the answer and must not enter the prompt.\n"
        "\\subsection*{1.1 Square Numbers}\n"
        "\\begin{itemize}\n"
        "\\item[] ? What is the square root of 64?\n"
        "We know that 8 multiplied by 8 is 64.\n"
        "\\end{itemize}\n"
        "\\section*{? Figure it Out}\n"
        "\\begin{itemize}\n"
        "\\item[1.] Which of 2032 and 1089 is a perfect square?\n"
        "\\item[2.] Find the side of a square with area 441 square metres.\n"
        "\\end{itemize}\n"
        "\\section*{1 A SQUARE AND A CUBE}\n"
        "Page No. 2\n"
        "Question one.\nAns. First answer.\n"
        "Page No. 3\n"
        "Question two.\nAns. Second answer.\n"
        "Page No. 4\n"
        "Question three.\nAns. Third answer.\n"
    )
    chunks = g._section_aware_chunks(source)
    sections = [section for chunk in chunks for section in chunk["sections"]]
    anchors = g._source_task_anchors(sections)

    assert all(
        not g._is_answer_key_source_section(section) for section in sections)
    # Neutral parse-time kind for Figure-it-Out list items (§3 purge): the
    # outline/page verdict owns the real classification.
    assert [
        item["source_kind"] for item in anchors
    ].count("intext_question") == 2
    locker = next(
        item for item in anchors if "Khoisnam" in item["raw_task"])
    square_root = next(
        item for item in anchors if "square root of 64" in item["raw_task"])
    assert "Hint: Find how many times" in locker["raw_task"]
    assert "This paragraph explains" not in locker["raw_task"]
    assert "We know that" not in square_root["raw_task"]
    assert all("Ans." not in item["raw_task"] for item in anchors)


def test_grade8_math_mid_sentence_image_keeps_lowercase_prompt_tail():
    url = "https://cdn.mathpix.com/cropped/operator.jpg"
    source = (
        "\\subsection*{1.2 Cubic Numbers}\n"
        "\\section*{Taxicab Numbers}\n"
        "\\begin{itemize}\n"
        "\\item[] ? Express 4104 as the sum of\n"
        f"![]({url})\n"
        "two positive cubes.\n"
        "\\end{itemize}\n"
    )
    sections = [
        section
        for chunk in g._section_aware_chunks(source)
        for section in chunk["sections"]
    ]

    anchors = g._source_task_anchors(sections)

    assert len(anchors) == 1
    assert anchors[0]["raw_task"].endswith("two positive cubes.")
    assert anchors[0]["image_urls"] == [url]


def test_grade8_math_final_figure_it_out_and_square_pairs_task_scopes():
    source = (
        "\\subsection*{1.1 Square Numbers}\n"
        "Square teaching text.\n"
        "\\subsection*{1.2 Cubic Numbers}\n"
        "Cube teaching text.\n"
        "\\subsection*{1.3 A Pinch of History}\n"
        "History teaching text.\n"
        "\\section*{? Figure it Out}\n"
        "\\begin{itemize}\n"
        "\\item[1.] Find the cube root of 27000.\n"
        "\\end{itemize}\n"
        "\\section*{Square Pairs!}\n"
        "Try arranging the numbers without repetition so that every adjacent "
        "pair adds to a square.\n\n"
        "Can you arrange the numbers in more than one way? "
        "If not, can you explain why?\n"
        "Can you arrange them in a circle?\n"
    )
    sections = [
        section
        for chunk in g._section_aware_chunks(source)
        for section in chunk["sections"]
    ]

    anchors = g._source_task_anchors(sections)
    final_exercise = next(
        item for item in anchors if "cube root of 27000" in item["raw_task"])
    square_pair = next(
        item for item in anchors if "more than one way" in item["raw_task"])

    assert final_exercise["topic_hint"] == ""
    assert final_exercise["_topic_scope"] == "chapter"
    assert square_pair["topic_hint"] == "Square Numbers"
    assert "every adjacent pair" in square_pair["shared_context"]
    assert square_pair["requires_context"] is True
    assert square_pair["raw_task"].endswith(
        "If not, can you explain why?")


def test_referential_sum_and_table_prompts_receive_source_context():
    url = "https://cdn.mathpix.com/cropped/cube-table.jpg"
    source = (
        "\\subsection*{1.1 Cubic Numbers}\n"
        "? Complete the table below.\n"
        f"![]({url})\n\n"
        "\\begin{tabular}{|l|l|}\n"
        "\\hline $1^3=1$ & $2^3=8$ \\\\\n"
        "\\hline\n"
        "\\end{tabular}\n"
        "? What patterns do you notice in the table above?\n"
        "\\section*{Perfect Cubes and Consecutive Odd Numbers}\n"
        "$$91+93+95+97+99+101+103+105+107+109.$$\n"
        "? Can you tell what this sum is without doing the calculation?\n"
    )
    sections = [
        section
        for chunk in g._section_aware_chunks(source)
        for section in chunk["sections"]
    ]
    anchors = g._attach_explicit_figure_images(
        g._source_task_anchors(sections), sections)
    table_completion = next(
        item for item in anchors
        if item["raw_task"].startswith("Complete the table below."))
    table_pattern = next(
        item for item in anchors if "table above" in item["raw_task"])
    sum_prompt = next(
        item for item in anchors if "this sum" in item["raw_task"])

    assert table_completion["image_urls"] == [url]
    assert table_completion["requires_context"] is True
    assert "1^3=1" in table_completion["shared_context"]
    assert "2^3=8" in table_completion["shared_context"]
    assert table_pattern["image_urls"] == [url]
    assert f'[img src="{url}" ' in g._inventory_task_text(table_pattern)
    assert "91+93+95+97+99+101+103+105+107+109" in (
        sum_prompt["shared_context"])
    assert sum_prompt["requires_context"] is True
    assert "The referenced sum is [Katex]" in g._inventory_task_text(sum_prompt)


def test_fill_table_below_callout_owns_adjacent_source_tables():
    source = (
        "\\subsection*{2.1 The Power of Doubling}\n"
        "\\begin{itemize}\n"
        "\\item[] (?) Fill the table below.\n\n"
        "\\begin{tabular}{|l|l|l|l|l|l|}\n"
        "\\hline Fold & Thickness & Fold & Thickness & Fold & Thickness \\\\\n"
        "\\hline 18 & $\\approx 262 \\mathrm{~cm}$ & 21 & & 24 & \\\\\n"
        "\\hline 19 & $\\approx 524 \\mathrm{~cm}$ & 22 & & 25 & \\\\\n"
        "\\hline 20 & $\\approx 10.4 \\mathrm{~m}$ & 23 & & 26 & \\\\\n"
        "\\hline\n"
        "\\end{tabular}\n"
        "\\item[] After 26 folds, the thickness is approximately 670 m.\n"
        "\\begin{tabular}{|l|l|}\n"
        "\\hline Fold & Thickness \\\\\n"
        "\\hline 27 & $\\approx 1.3 \\mathrm{~km}$ \\\\\n"
        "\\hline 28 & \\\\\n"
        "\\hline\n"
        "\\end{tabular}\n"
        "\\item[] Continue the same doubling pattern through fold 45.\n"
        "\\begin{tabular}{|l|l|l|}\n"
        "\\hline Fold & Fold & Fold \\\\\n"
        "\\hline 31 & 36 & 41 \\\\\n"
        "\\hline 32 & 37 & 42 \\\\\n"
        "\\hline 33 & 38 & 43 \\\\\n"
        "\\hline 34 & 39 & 44 \\\\\n"
        "\\hline 35 & 40 & 45 \\\\\n"
        "\\hline\n"
        "\\end{tabular}\n"
        "\\end{itemize}\n"
        "\\begin{tabular}{|l|l|}\n"
        "\\hline Fold 4 & 0.016 cm \\\\\n"
        "\\hline Fold 5 & 0.032 cm \\\\\n"
        "\\hline\n"
        "\\end{tabular}\n"
        "\\begin{tabular}{|l|l|}\n"
        "\\hline Fold 9 & 0.512 cm \\\\\n"
        "\\hline Fold 10 & 1.024 cm \\\\\n"
        "\\hline\n"
        "\\end{tabular}\n"
        "? What happens after 30 folds?\n"
    )
    sections = [
        section
        for chunk in g._section_aware_chunks(source)
        for section in chunk["sections"]
    ]

    anchors = g._source_task_anchors(sections)
    assert [
        item["raw_task"] for item in anchors
    ].count("Fill the table below.") == 1
    assert len(anchors) == 2
    table_prompt = next(
        item for item in anchors if item["raw_task"] == "Fill the table below.")
    public_task = g._inventory_task_text(table_prompt)

    assert table_prompt["requires_context"] is True
    assert "Fold | Thickness" in table_prompt["shared_context"]
    assert "18" in table_prompt["shared_context"]
    assert "21" in table_prompt["shared_context"]
    assert "24" in table_prompt["shared_context"]
    assert "26" in table_prompt["shared_context"]
    assert "27" in table_prompt["shared_context"]
    assert "28" in table_prompt["shared_context"]
    assert "31" in table_prompt["shared_context"]
    assert "36" in table_prompt["shared_context"]
    assert "41" in table_prompt["shared_context"]
    assert "45" in table_prompt["shared_context"]
    assert "Fold 4" not in table_prompt["shared_context"]
    assert "0.016 cm" not in table_prompt["shared_context"]
    assert "Fold 9" not in table_prompt["shared_context"]
    assert "1.024 cm" not in table_prompt["shared_context"]
    assert "What happens after 30 folds?" not in table_prompt["shared_context"]
    assert public_task.endswith("Fill the table below.")

    # A short imperative is a source task like any other. The word-count
    # cascade that used to call this one a ``stub_task`` — and so route it
    # for dropping when its table had not yet been attached — is gone: the
    # row is in the inventory by the outline judge's verdict, so it is
    # accounted, never re-litigated by length.
    contextless = {
        "items": [{
            "qid": "QINV-0013",
            "source_kind": "checkpoint_question",
            "raw_task": "Fill the table below.",
            "normalized_task": "Fill the table below.",
        }],
    }
    assert g._invalid_inventory_items(contextless) == []

    # The same short imperative WITH its table riding along as shared context
    # is a complete source task (job 12: "Complete the table below." killed a
    # 3D Shapes run at the extraction gate despite carrying the full table).
    context_backed = {
        "items": [{
            "qid": "QINV-0013",
            "source_kind": "checkpoint_question",
            "raw_task": "Fill the table below.",
            "normalized_task": "Fill the table below.",
            "requires_context": True,
            "shared_context": (
                "Name | Figure | Number of Vertices | Number of Sides\n"
                "Triangle |  | 3 | 3\nQuadrilateral |  |  |"
            ),
            # The ACSD source contract pins the public prompt to the bare
            # display wording, so the stub judgment must consult the shared
            # context itself rather than rely on context embedding.
            "_acsd_source_contract": "acsd-phase2-source-critical",
            "_acsd_display_prompt": "Fill the table below.",
        }],
    }
    assert g._invalid_inventory_items(context_backed) == []

    merged = g._merge_source_task_anchors(
        [{
            "qid": "QINV-0013",
            "source_kind": "checkpoint_question",
            "source_label": table_prompt["source_label"],
            "raw_task": "Fill the table below.",
            "normalized_task": "Fill the table below.",
        }],
        anchors,
    )
    recovered = [
        item for item in merged
        if item["raw_task"] == "Fill the table below."
    ]
    assert len(recovered) == 1
    assert recovered[0]["qid"] == "QINV-0013"
    next_qid = 14
    for item in merged:
        if item.get("qid"):
            continue
        item["qid"] = f"QINV-{next_qid:04d}"
        next_qid += 1
    assert g._invalid_inventory_items({"items": merged}) == []


def test_fill_table_below_without_owner_boundary_takes_first_table_only():
    source = (
        "\\subsection*{1.1 Cubic Numbers}\n"
        "? Complete the table below.\n\n"
        "\\begin{tabular}{|l|l|}\n"
        "\\hline A & B \\\\\n"
        "\\hline 1 & 8 \\\\\n"
        "\\hline\n"
        "\\end{tabular}\n"
        "This later reference table is not part of the checkpoint.\n"
        "\\begin{tabular}{|l|l|}\n"
        "\\hline X & Y \\\\\n"
        "\\hline 99 & 100 \\\\\n"
        "\\hline\n"
        "\\end{tabular}\n"
    )
    sections = [
        section
        for chunk in g._section_aware_chunks(source)
        for section in chunk["sections"]
    ]

    anchors = g._source_task_anchors(sections)
    prompt = next(
        item for item in anchors
        if item["raw_task"] == "Complete the table below.")

    assert "A | B" in prompt["shared_context"]
    assert "1 | 8" in prompt["shared_context"]
    assert "X | Y" not in prompt["shared_context"]
    assert "99 | 100" not in prompt["shared_context"]


def test_fill_table_below_does_not_claim_table_after_intervening_prose():
    source = (
        "\\subsection*{1.1 Cubic Numbers}\n"
        "? Complete the table below.\n\n"
        "This paragraph starts a separate worked explanation.\n"
        "\\begin{tabular}{|l|l|}\n"
        "\\hline X & Y \\\\\n"
        "\\hline 99 & 100 \\\\\n"
        "\\hline\n"
        "\\end{tabular}\n"
    )
    sections = [
        section
        for chunk in g._section_aware_chunks(source)
        for section in chunk["sections"]
    ]

    prompt = next(
        item for item in g._source_task_anchors(sections)
        if item["raw_task"] == "Complete the table below.")
    prompt["qid"] = "QINV-0013"

    assert prompt["shared_context"] == ""
    assert prompt["requires_context"] is False
    # Failing to claim the later table is a context-attachment outcome, not
    # a licence to reject the row: a context-less short imperative is still
    # a source task and still enters the coverage contract.
    assert g._invalid_inventory_items({"items": [prompt]}) == []


def test_brevity_never_nominates_an_inventory_row_for_adjudication():
    """Only emptiness nominates. A short row is a task, not a suspect.

    The old nomination rule called a row an ``empty_or_stub_task`` by word
    count, which meant a genuinely short source question could be sent to
    the adjudicator to be dropped. Emptiness — no coverage key at all — is
    mechanics and still nominates; length never does.
    """
    source_task = "Is 9 a cube?"
    anchors = [{
        "source_kind": "checkpoint_question",
        "source_label": "Checkpoint 9.2",
        "raw_task": source_task,
        "normalized_task": source_task,
    }]
    short_but_unowned = {
        "source_kind": "other",
        "source_label": "Unowned short ask",
        "raw_task": "q",
        "normalized_task": "q",
    }
    empty_row = {
        "source_kind": "other",
        "source_label": "Unowned fragment",
        "raw_task": "",
        "normalized_task": "",
    }
    model_items = [dict(anchors[0]), short_but_unowned, empty_row]

    candidates = g._unowned_stub_inventory_candidates(model_items, anchors)

    assert [c["index"] for c in candidates] == [2]
    assert candidates[0]["reason"] == "empty_task"
    # Detection never removes: every row is still present for the
    # inventory-row adjudicator and its critic to rule on.
    assert len(model_items) == 3


def test_a_banner_row_still_reaches_the_source_reading_adjudicator():
    """Purging the word count must not purge the judge with it.

    ``## Practice Set 1.1`` (Balbharati Std 6, "Three Dimensional Shapes")
    reached ``_adjudicate_invalid_inventory_rows`` only via this
    nomination. With no nomination at all it would instead be OWED a
    rendered public Example by the coverage contract and force-placed in
    front of a learner. The nomination is mechanics — the row's task text
    is nothing but the label it was filed under — and it decides nothing;
    the model rules against the source with an independent critic.
    """
    banner = {
        "qid": "QINV-0026",
        "source_kind": "checkpoint_question",
        "source_label": "Practice Set 1.1",
        "raw_task": "## Practice Set 1.1",
    }
    real_question = {
        "qid": "QINV-0100",
        "source_kind": "checkpoint_question",
        "source_label": "Discuss",
        "raw_task": "Find the volume of the cuboid.",
    }
    short_question = {
        "qid": "QINV-0101",
        "source_kind": "checkpoint_question",
        "source_label": "Checkpoint 9.2",
        "raw_task": "Is 9 a cube?",
    }

    # A descriptive caption the extractor mistook for a task used to be
    # nominated by a verb allow-list plus a "?" test. That vocabulary is
    # purged, and the deliberate consequence is stated here: the row is no
    # longer a drop candidate at all. If no authored Type renders it, the
    # coverage repair force-places it WITH a review flag naming its QID —
    # a flagged surplus Example a reviewer can delete, rather than a
    # deterministic drop of something that might have been a question.
    # CLAUDE.md ranks these outcomes: "stopping the run is recoverable,
    # dropping a question is not".
    described_caption = {
        "qid": "QINV-0102",
        "source_kind": "source_task",
        "source_label": "Fig. 3",
        "raw_task": (
            "The picture shows farmers working in the field during the "
            "monsoon season in Maharashtra."
        ),
    }

    candidates = g._unowned_stub_inventory_candidates(
        [banner, real_question, short_question, described_caption], [])

    assert [c["qid"] for c in candidates] == ["QINV-0026"]
    assert candidates[0]["reason"] == "task_is_only_its_own_label"
    # No keyword vocabulary survives to classify these rows.
    assert not hasattr(g, "_unowned_inventory_row_is_non_task")


def test_a_force_placed_example_is_flagged_on_the_row_it_lands_on():
    """R4 / Q13: the coverage repair may guess, but never silently.

    When no authored Type/Case rendered an inventory prompt, the repair
    ladder synthesises a Type/Case shell around the raw inventory wording
    and publishes it. Nothing is dropped — and nothing is guessed
    silently either, so the placement rides the row as a review flag
    naming the QID.
    """
    inventory = {"items": [{
        "qid": "QINV-0001",
        "source_kind": "checkpoint_question",
        "source_label": "Practice Set 1.1",
        "raw_task": "## Practice Set 1.1",
    }]}
    rows = [{
        "title": "A",
        "topic": "T",
        "concept_details": "Description: d. Achieving Mastery: m.",
        "misconceptions": "x",
    }]

    assert g._rendered_inventory_coverage_defects(rows, inventory) == {
        "missing": ["QINV-0001"], "duplicate": [],
    }

    enforced = g._enforce_rendered_inventory_coverage(
        [dict(row) for row in rows], inventory)

    flags = enforced[0].get("review_flags") or []
    assert any("QINV-0001" in flag and "force-placed" in flag
               for flag in flags), flags
    # Replaying the deterministic pipeline must not add a second flag —
    # the assemble deposit fixpoint compares the row sets for drift.
    replayed = g._enforce_rendered_inventory_coverage(
        [dict(row) for row in enforced], inventory)
    assert replayed == enforced


def test_visual_anchor_does_not_protect_duplicate_that_lost_its_image():
    # Both rows are nominatable on mechanics — their task text is nothing
    # but the label they were filed under — so what this test isolates is
    # the visual contract: the deterministic anchor protects only the row
    # that still carries the anchor's image.
    #
    # ``source_kind`` is deliberately ``checkpoint_question``: that is the
    # kind the production scenario used, and the nomination must not
    # depend on a per-kind keyword rule (the verb allow-list that briefly
    # stood here is purged — Rule 1, first bullet).
    prompt = "Practice Set 9.4"
    url = "https://cdn.mathpix.com/table.jpg"
    anchors = [{
        "source_kind": "checkpoint_question",
        "source_label": prompt,
        "raw_task": prompt,
        "normalized_task": prompt,
        "requires_visual": True,
        "image_urls": [url],
    }]
    complete = dict(anchors[0])
    incomplete = {
        "source_kind": "checkpoint_question",
        "source_label": prompt,
        "raw_task": prompt,
        "normalized_task": prompt,
        "requires_visual": False,
        "image_urls": [],
    }

    assert g._inventory_row_task_is_only_its_own_label(complete)
    assert g._inventory_row_task_is_only_its_own_label(incomplete)

    candidates = g._unowned_stub_inventory_candidates(
        [complete, incomplete], anchors)

    # The anchor's visual contract protects only the row that kept its
    # image; the duplicate that lost it is nominated for adjudication.
    assert [c["index"] for c in candidates] == [1]
    assert candidates[0]["reason"] == "task_is_only_its_own_label"


def test_source_owned_markdown_visual_survives_attachment_resolution():
    prompt = "Complete the table below."
    url = "https://cdn.mathpix.com/table.jpg"
    raw_task = f"{prompt} ![]({url})"
    item = {
        "source_kind": "checkpoint_question",
        "source_label": "Checkpoint 9.4",
        "raw_task": raw_task,
        "normalized_task": raw_task,
        "requires_visual": True,
        "image_urls": [url],
    }

    attached = g._attach_explicit_figure_images([item], [])[0]

    assert attached["image_urls"] == [url]
    assert attached["requires_visual"] is True
    assert attached["_figure_images_resolved"] is True
    assert f'[img src="{url}" ' in g._inventory_task_text(attached)


def test_conditional_followup_remains_in_the_same_checkpoint_prompt():
    source = (
        "\\section*{Square Pairs!}\n"
        "Can you arrange them in more than one way? "
        "If not, can you explain why?\n"
        "Can you do the same with numbers from 1 to 32, but in a circle?\n"
    )
    sections = [
        section
        for chunk in g._section_aware_chunks(source)
        for section in chunk["sections"]
    ]

    anchors = g._source_task_anchors(sections)

    assert [anchor["raw_task"] for anchor in anchors] == [
        (
            "Can you arrange them in more than one way? "
            "If not, can you explain why?"
        ),
        "Can you do the same with numbers from 1 to 32, but in a circle?",
    ]
    resumed = {
        **anchors[0],
        "qid": "QINV-0001",
        "raw_task": "Can you arrange them in more than one way?",
        "normalized_task": "Can you arrange them in more than one way?",
    }
    merged = g._merge_source_task_anchors([resumed], anchors)
    first = next(item for item in merged if item.get("qid") == "QINV-0001")
    assert first["raw_task"] == anchors[0]["raw_task"]


def test_grade8_math_body_figure_it_out_and_callout_boundaries():
    local_exercises = "".join(
        f"\\item[{number}.] Local exercise {number}.\n"
        for number in range(1, 4)
    )
    final_exercises = "".join(
        f"\\item[{number}.] Final exercise {number}.\n"
        for number in range(1, 14)
    ) + "14. Final exercise 14.\n"
    source = (
        "\\subsection*{2.2 Exponential Notation and Operations}\n"
        "\\begin{itemize}\n"
        "\\item[] ? Make reasonable assumptions and find the answers. "
        "Remember to estimate first.\n"
        "\\item[] ? Is the first power larger? Yes, since its exponent is "
        "greater.\n"
        "\\item[] ? Figure it Out\n"
        f"{local_exercises}"
        "\\end{itemize}\n"
        "\\subsection*{2.5 A Pinch of History}\n"
        "\\begin{itemize}\n"
        "\\item[] ? Calculate and write the answer using scientific notation:\n"
        "\\begin{itemize}\n"
        "\\item[(i)] First nested subpart.\n"
        "\\item[(ii)] Second nested subpart.\n"
        "\\end{itemize}\n"
        "\\end{itemize}\n"
        "\\begin{itemize}\n"
        "\\item[(iii)] Third nested subpart.\n"
        "\\item[(iv)] Fourth nested subpart.\n"
        "\\end{itemize}\n"
        "\\item[] ? Figure it Out\n"
        f"{final_exercises}"
    )

    sections = [
        section
        for chunk in g._section_aware_chunks(source)
        for section in chunk["sections"]
    ]
    anchors = g._source_task_anchors(sections)
    exercises = [
        item for item in anchors if item["source_kind"] == "exercise"
    ]

    assert len(exercises) == 17
    assert any(
        item["raw_task"] == "Local exercise 1."
        and item["topic_hint"] == "Exponential Notation and Operations"
        for item in exercises
    )
    assert any(
        item["raw_task"] == "Final exercise 14."
        and item["topic_hint"] == ""
        for item in exercises
    )
    assert all(
        item["raw_task"].lower() != "figure it out" for item in anchors
    )

    nested = next(
        item for item in anchors
        if item["raw_task"].startswith(
            "Calculate and write the answer using scientific notation")
    )
    assert "Third nested subpart" in nested["raw_task"]
    assert "Fourth nested subpart" in nested["raw_task"]

    ordinary_answers = next(
        item for item in anchors if "reasonable assumptions" in item["raw_task"]
    )
    assert ordinary_answers["raw_task"] == (
        "Make reasonable assumptions and find the answers. "
        "Remember to estimate first."
    )
    inline_answer = next(
        item for item in anchors if "first power larger" in item["raw_task"]
    )
    assert inline_answer["raw_task"] == "Is the first power larger?"


def test_grade8_science_final_blocks_inventory_nine_questions_and_four_projects():
    source = (
        "## 2.4 Microorganisms and Us\n"
        "Microorganisms interact with food, soil, and health.\n"
        "\\section*{Keep the curiosity alive}\n"
        "\\begin{itemize}\n"
        "\\item[1.] Label the cell diagram.\n"
        "\\item[2.] Study the yeast set-up in Fig. 2.14 and answer:\n"
        "\\begin{itemize}\n"
        "\\item[(i)] Predict what happens after four hours and choose a reason.\n"
        "\\item[(ii)] Explain the purpose of passing the gas into lime water.\n"
        "\\end{itemize}\n"
        "\\begin{figure}\n"
        "\\includegraphics{https://example.test/fig-2-14.png}\n"
        "\\caption{Fig. 2.14: Experimental set-up}\n"
        "\\end{figure}\n"
        "\\item[3.] Explain why a bean farmer may not add nitrogen fertiliser.\n"
        "\\item[4.] Compare two compost pits with and without dry leaves.\n"
        "\\item[5.] Identify the three described microorganisms.\n"
        "\\item[6.] Design an experiment for microbial growth conditions.\n"
        "\\item[7.] Compare bread kept near a sink and in a refrigerator.\n"
        "\\item[8.] Give two explanations for curd becoming more sour.\n"
        "\\item[9.] Observe Fig. 2.15 and answer all three subparts.\n"
        "\\begin{figure}\n"
        "\\includegraphics{https://example.test/fig-2-15.png}\n"
        "\\caption{Fig. 2.15: Experimental set-up}\n"
        "\\end{figure}\n"
        "\\end{itemize}\n"
        "\\section*{Discover, design, and debate}\n"
        "\\begin{itemize}\n"
        "\\item[-] Investigate India's biogas programme.\n"
        "\\item[-] Document a traditional fermented food from your area.\n"
        "\\item[-] Study the parts of a mushroom under magnification.\n"
        "\\item[-] Interview an entrepreneur about mushroom cultivation.\n"
        "\\end{itemize}\n"
    )
    sections = g.parse_mmd_sections(source)
    anchors = g._attach_explicit_figure_images(
        g._source_task_anchors(sections), sections)
    questions = [
        item for item in anchors
        if item["source_kind"] == "intext_question"
    ]
    projects = [
        item for item in anchors if item["source_kind"] == "activity"
    ]

    assert len(questions) == 9
    assert len(projects) == 4
    assert "(i)" in questions[1]["raw_task"]
    assert "(ii)" in questions[1]["raw_task"]
    assert questions[1]["image_urls"] == [
        "https://example.test/fig-2-14.png"]
    assert questions[8]["image_urls"] == [
        "https://example.test/fig-2-15.png"]
    assert all(item["_topic_scope"] == "chapter" for item in anchors)


def test_grade8_science_activity_figures_use_exact_source_boundaries():
    disease_figures = "".join(
        (
            "\\begin{figure}\n"
            f"\\includegraphics{{https://example.test/disease-{index}.png}}\n"
            f"\\caption{{{caption}}}\n"
            "\\end{figure}\n"
        )
        for index, caption in enumerate(
            ("Cold and flu", "Typhoid", "Diabetes", "Asthma", "Chickenpox"),
            start=1,
        )
    )
    source = (
        "\\subsection*{3.2 How Can We Stay Healthy?}\n"
        "\\section*{Activity 3.3: Let us compare}\n"
        "\\begin{itemize}\n"
        "\\item[-] Look at Fig. 3.3a and Fig. 3.3b. Which playground "
        "would you like to play in, and why?\n"
        "\\item[-] Most of us would choose the clean playground; this is "
        "the supplied answer.\n"
        "\\end{itemize}\n"
        "\\begin{figure}\n"
        "\\includegraphics{https://example.test/fig-3-3.png}\n"
        "\\caption{Fig. 3.3: Two different playgrounds}\n"
        "\\end{figure}\n"
        "\\subsection*{3.5 How to Prevent and Control Diseases?}\n"
        "\\begin{figure}\n"
        "\\includegraphics{https://example.test/fig-3-5-a.png}\n"
        "\\caption{Fig. 3.5 (a): Spread in the community}\n"
        "\\end{figure}\n"
        "\\section*{Activity 3.7: Let us infer}\n"
        "\\begin{itemize}\n"
        "\\item[-] Study the infographic in Fig. 3.5b. How did resistance "
        "develop, and what precautions should be taken?\n"
        "\\item[-] To tackle the problem, use antibiotics only as "
        "prescribed; this is the supplied answer.\n"
        "\\end{itemize}\n"
        "\\begin{figure}\n"
        "\\includegraphics{https://example.test/fig-3-5-b.png}\n"
        "\\caption{Fig. 3.5 (b): Development of resistance}\n"
        "\\end{figure}\n"
        "\\section*{Keep the curiosity alive}\n"
        "\\begin{itemize}\n"
        "\\item[1.] Group the diseases shown in the images as communicable "
        "or non-communicable.\n"
        f"{disease_figures}"
        "\\end{itemize}\n"
    )
    sections = g.parse_mmd_sections(source)
    anchors = g._attach_explicit_figure_images(
        g._source_task_anchors(sections), sections)

    compare = next(
        item for item in anchors if item["source_label"].startswith(
            "Activity 3.3"))
    resistance = next(
        item for item in anchors if item["source_label"].startswith(
            "Activity 3.7"))
    disease_grouping = next(
        item for item in anchors if item["source_label"].endswith("Q1"))

    assert "supplied answer" not in compare["raw_task"]
    assert compare["image_urls"] == ["https://example.test/fig-3-3.png"]
    assert "supplied answer" not in resistance["raw_task"]
    assert resistance["image_urls"] == [
        "https://example.test/fig-3-5-b.png"]
    assert len(disease_grouping["image_urls"]) == 5
    assert list(disease_grouping["_image_captions"].values()) == [
        "Cold and flu", "Typhoid", "Diabetes", "Asthma", "Chickenpox",
    ]
    assert g._figure_reference_ids(
        "Compare Fig. 3.5a, Fig. 3.5b, and Fig. 3.3a."
    ) == ["3.5(a)", "3.5(b)", "3.3(a)"]


def test_grade8_science_activity_result_prose_and_infographic_boundaries():
    source = (
        "\\subsection*{3.4 Diseases: What Are the Causes and Types?}\n"
        "\\section*{Activity 3.4: Let us find out}\n"
        "\\begin{itemize}\n"
        "\\item[-] Check the information in Table 3.1 and add missing "
        "details.\n"
        "\\item[-] Study the table and propose preventive steps.\n"
        "\\end{itemize}\n"
        "\\begin{table}\n"
        "\\begin{tabular}{|l|l|}\n"
        "Disease ![](https://example.test/decorative.png) & Prevention \\\\\n"
        "\\end{tabular}\n"
        "\\end{table}\n"
        "By studying the Table 3.1, we can understand the completed answer.\n"
        "Parasites are unrelated following exposition.\n"
        "\\subsection*{3.5 How to Prevent and Control Diseases?}\n"
        "\\section*{Activity 3.6: Let us read}\n"
        "\\section*{Odisha - community-led sanitation campaign}\n"
        "A sanitation campaign helped families build and use toilets.\n"
        "What do you infer from this case study? Simple steps like good "
        "sanitation can greatly reduce the spread of communicable diseases. "
        "Find similar campaigns in your location. Share and discuss their "
        "impact with your peers.\n"
        "\\section*{Ability of the body to fight diseases}\n"
        "Immunity protects the body.\n"
        "\\section*{Think like a scientist}\n"
        "Observations\nJenner observed a pattern.\n\n"
        "Hypothesis\n![](https://example.test/hypothesis.png)\n"
        "Cowpox exposure might protect people.\n\n"
        "Experimentation\n![](https://example.test/experiment.png)\n"
        "He tested the hypothesis.\n\n"
        "Results\nThe test supported it.\n\n"
        "Application\nMass vaccination helped eradicate smallpox.\n"
        "![](https://example.test/application.png)\n\n"
        "Vaccines are discussed in unrelated following prose.\n"
        "![](https://example.test/unrelated.png)\n"
        "\\subsection*{3.5.1 Treatment of diseases}\n"
        "Treatment follows diagnosis.\n"
    )
    sections = g.parse_mmd_sections(source)
    anchors = g._attach_explicit_figure_images(
        g._source_task_anchors(sections), sections)

    table_activity = next(
        item for item in anchors if item["source_label"].startswith(
            "Activity 3.4"))
    sanitation = next(
        item for item in anchors if item["source_label"].startswith(
            "Activity 3.6"))
    scientific_method = next(
        item for item in anchors if item["source_label"] == (
            "Think like a scientist"))

    assert "Table 3.1" in table_activity["raw_task"]
    assert "By studying" not in table_activity["raw_task"]
    assert "Parasites" not in table_activity["raw_task"]
    assert table_activity["image_urls"] == []
    assert "sanitation campaign helped" in sanitation["raw_task"]
    assert "What do you infer" in sanitation["raw_task"]
    assert "Find similar campaigns" in sanitation["raw_task"]
    assert "Simple steps like" not in sanitation["raw_task"]
    assert scientific_method["source_kind"] == "activity"
    assert scientific_method["_activity_origin"] is False
    assert "Application" in scientific_method["raw_task"]
    assert "Vaccines are discussed" not in scientific_method["raw_task"]
    assert scientific_method["image_urls"] == [
        "https://example.test/hypothesis.png",
        "https://example.test/experiment.png",
        "https://example.test/application.png",
    ]


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


def test_inventory_keeps_repeated_wording_when_source_context_differs():
    prompt = "What do you observe?"
    items = [
        {
            "source_kind": "checkpoint_question",
            "source_label": "Reflect",
            "parent_source_label": "Activity 2.1",
            "topic_hint": "Microorganisms",
            "raw_task": prompt,
        },
        {
            "source_kind": "checkpoint_question",
            "source_label": "Reflect",
            "parent_source_label": "Activity 2.2",
            "topic_hint": "Food Preservation",
            "raw_task": prompt,
        },
    ]

    assert g._merge_source_task_anchors(items, []) == items


def test_inventory_still_dedupes_trace_equivalent_representations():
    item = {
        "source_kind": "exercise",
        "source_label": "Question 1",
        "parent_source_label": "Exercise",
        "topic_hint": "Powers",
        "raw_task": "Write the number as a power.",
    }

    assert g._merge_source_task_anchors([item, dict(item)], []) == [item]


def test_implicit_visual_does_not_attach_decorative_nearest_image():
    source = (
        "## Cells\n"
        "\\begin{figure}\n"
        "\\includegraphics{https://example.test/school-logo.png}\n"
        "\\caption{Decorative school logo}\n"
        "\\end{figure}\n"
    )
    sections = g.parse_mmd_sections(source)
    item = {
        "source_kind": "diagram_task",
        "source_label": "Observe",
        "topic_hint": "Cells",
        "raw_task": "Look at the image and explain the process.",
        "_source_section_index": 0,
        "_source_position": 0,
    }

    attached = g._attach_explicit_figure_images([item], sections)[0]

    assert attached["image_urls"] == []
    assert attached["_figure_images_resolved"] is True
    assert attached["requires_visual"] is False


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


def test_structured_mcq_options_replace_a_conflicting_existing_tail():
    item = g._sanitize_inventory_item({
        "source_kind": "mcq",
        "raw_task": (
            "Which term is prime? (A) 13 (B) 14 (C) 15 (D) 16"
        ),
        "options": [
            {"label": "A", "text": "14"},
            {"label": "B", "text": "15"},
            {"label": "C", "text": "16"},
            {"label": "D", "text": "17"},
        ],
    })
    assert item["raw_task"] == (
        "Which term is prime? (A) 14 (B) 15 (C) 16 (D) 17"
    )
    assert item["raw_task"].count("(A)") == 1
    assert "(A) 13" not in item["raw_task"]


def test_structured_options_never_truncate_lowercase_multipart_exercise():
    prompt = (
        "Answer both parts: (a) calculate the current; "
        "(b) explain why it changes."
    )
    item = g._sanitize_inventory_item({
        "source_kind": "exercise",
        "raw_task": prompt,
        "options": [
            {"label": "A", "text": "Current doubles"},
            {"label": "B", "text": "Current halves"},
        ],
    })

    assert item["raw_task"] == prompt
    assert item["options"] == []
    assert g._mcq_option_tail("Choose one: (A) first (C) third") is None


def test_anchor_merge_prefers_source_mcq_options_when_model_options_conflict():
    model_item = {
        "source_kind": "mcq",
        "source_label": "Exercise 5.2 Q2(i)",
        "raw_task": (
            "Which term of the AP is 78? "
            "(A) 13 (B) 14 (C) 15 (D) 16"
        ),
        "normalized_task": "Which term of the AP is 78?",
    }
    source_anchor = {
        "source_kind": "mcq",
        "source_label": "Exercise 5.2 Q2(i)",
        "raw_task": (
            "Which term of the AP is 78? "
            "(A) 14 (B) 15 (C) 16 (D) 17"
        ),
        "normalized_task": "Which term of the AP is 78?",
    }
    merged = g._merge_source_task_anchors([model_item], [source_anchor])
    assert len(merged) == 1
    assert merged[0]["raw_task"] == source_anchor["raw_task"]
    assert "(A) 13" not in merged[0]["raw_task"]


def test_plain_container_headings_are_removed_but_real_imperatives_survive():
    assert g._strip_public_source_heading(
        "Discuss\nExplain why the current changes."
    ) == "Explain why the current changes."
    assert g._strip_public_source_heading(
        "Activity: Explain why the current changes."
    ) == "Explain why the current changes."
    assert g._strip_public_source_heading(
        "Discuss why the current changes with resistance."
    ) == "Discuss why the current changes with resistance."


def test_lettered_exercise_subparts_keep_one_parent_anchor():
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
        item for item in anchors
        if item["source_kind"] == "intext_question"
    ]
    assert len(exercises) == 2
    assert exercises[0]["source_label"] == "Write in brief Q1"
    assert exercises[0]["raw_task"].startswith("Write a note on:")
    assert all(f"{letter})" in exercises[0]["raw_task"] for letter in "abcde")
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
    questions = [
        item for item in anchors
        if item["source_kind"] in {"exercise", "intext_question"}
    ]
    assert len(questions) == 1
    assert questions[0]["source_kind"] == "intext_question"
    assert "(a)" in questions[0]["raw_task"]
    assert "(b)" in questions[0]["raw_task"]
    assert "(c)" in questions[0]["raw_task"]


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

    def fake_api(system, user, **_kwargs):
        assert "Visualising the Nation" in user
        assert _kwargs["single_attempt"] is True
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
        max_attempts=1,
        single_attempt=True,
    )
    assert {record["topic"] for record in out} == {
        "The Making of Germany and Italy",
        "Visualising the Nation",
    }


def test_final_checkpoint_missing_source_topic_resumes_from_prior_stage(
    monkeypatch,
):
    source = (
        Path(__file__).parents[1] / "data" / "Testing" / "RNE.mmd"
    ).read_text(encoding="utf-8")
    topics = g._topic_headings(g.parse_mmd_sections(source))
    missing_topic = "The Making of Nationalism in Europe"
    assert missing_topic in topics

    def record(topic):
        return {
            "topic": topic,
            "parent_concept": topic,
            "concept_title": f"{topic} concept",
            "concept_details": "Description: A source-grounded concept.",
            "keywords": "nationalism",
    }

    all_records = [record(topic) for topic in topics]
    inventory = {"items": [], "stats": {}}
    mined_types = {"types": []}
    prior_stage = g._make_concept_checkpoint(
        "post_type_assignment",
        records=all_records,
        question_task_inventory=inventory,
        mined_types=mined_types,
        method_row_snapshot=[],
    )
    incomplete_final = g._make_concept_checkpoint(
        "final_content_ready",
        records=[row for row in all_records if row["topic"] != missing_topic],
        question_task_inventory=inventory,
        mined_types=mined_types,
        method_row_snapshot=[],
    )
    checkpoint_history = {
        "checkpoint_format": g._CONCEPT_CHECKPOINT_FORMAT,
        "schema_version": g._CONCEPT_CHECKPOINT_SCHEMA,
        "checkpoints": [prior_stage, incomplete_final],
    }
    finalizer_calls = []

    def finalize(records, **kwargs):
        finalizer_calls.append((records, kwargs["source_topic_excerpts"]))
        return records

    monkeypatch.setattr(g, "_prepare_final_concept_content", finalize)
    monkeypatch.setattr(g, "_canonicalize_concept_rich_text", lambda rows: rows)
    # This test isolates source-topic recovery. Resuming the older empty
    # inventory now also refreshes deterministic task anchors and would
    # legitimately run the focused Type-delta API pass; keep that independent
    # contract out of this fixture.
    monkeypatch.setattr(
        g,
        "_reconcile_resumed_mined_types",
        lambda *args, **kwargs: {"types": []},
    )
    # This fixture isolates source-topic recovery. RNE also contains real
    # deterministic task anchors; allowing those into this deliberately empty
    # inventory would correctly rewind the 91% checkpoint for a fresh
    # certified Type-host review. That independent contract is covered by the
    # checkpoint inventory-refresh tests.
    monkeypatch.setattr(g, "_source_task_anchors", lambda _sections: [])
    monkeypatch.setattr(
        g,
        "_validate_final_or_raise",
        lambda *args, **kwargs: {"ok": True, "errors": [], "summary": {}},
    )

    out = g.concepts_from_mmd(
        source,
        subject="Social Science",
        live=True,
        resume_checkpoint=checkpoint_history,
    )

    assert len(finalizer_calls) == 1
    restored_records, source_topic_excerpts = finalizer_calls[0]
    assert {row["topic"] for row in restored_records} == set(topics)
    assert [group["topic"] for group in source_topic_excerpts] == topics
    assert {row["topic"] for row in out} == set(topics)


def test_final_checkpoint_with_orphan_analysis_prefix_forces_final_repair():
    source = "## T\nA short source section."
    sections = g.parse_mmd_sections(source)
    checkpoint = g._make_concept_checkpoint(
        "final_content_ready",
        records=[{
            "topic": "T",
            "parent_concept": "P",
            "concept_title": "C",
            "concept_details": (
                "Description: d\nMisconception/ // "
                "Misconception/ Error Analysis: Misconceptions: Students may "
                "believe d is always true.; Error Analysis: Students may omit "
                "a condition."
            ),
            "keywords": "",
        }],
        question_task_inventory={"items": [], "stats": {}},
        mined_types={"types": []},
        method_row_snapshot=[],
    )

    reasons = g._final_checkpoint_refresh_reasons(
        checkpoint,
        sections=sections,
        source_topic_excerpts=g._group_source_topic_excerpts(sections),
    )

    assert any("learner-analysis" in reason for reason in reasons)


def test_final_checkpoint_same_label_with_truncated_task_forces_refresh():
    source = (
        "## Number Patterns\n"
        "Example 1: Calculate the twentieth term of the arithmetic "
        "progression 4, 9, 14, 19 and explain every substituted value.\n"
    )
    sections = g.parse_mmd_sections(source)
    anchors = g._source_task_anchors(sections)
    assert anchors
    anchor = anchors[0]
    checkpoint = g._make_concept_checkpoint(
        "final_content_ready",
        records=[],
        question_task_inventory={"items": [{
            "qid": "QINV-0001",
            "source_kind": anchor.get("source_kind") or "worked_example",
            "source_label": anchor["source_label"],
            "topic_hint": anchor.get("topic_hint") or "Number Patterns",
            "raw_task": "Calculate the twentieth term.",
        }], "stats": {}},
        mined_types={"types": []},
        method_row_snapshot=[],
    )

    reasons = g._final_checkpoint_refresh_reasons(
        checkpoint,
        sections=sections,
        source_topic_excerpts=g._group_source_topic_excerpts(sections),
    )

    assert any("truncated or stale" in reason for reason in reasons)


def test_final_checkpoint_same_qid_with_changed_semantics_forces_refresh():
    source = (
        "## Microorganisms\n"
        "### Activity\n"
        "What changes do you observe?\n"
    )
    sections = g.parse_mmd_sections(source)
    anchors = g._source_task_anchors(sections)
    assert len(anchors) == 1
    anchor = anchors[0]
    checkpoint = g._make_concept_checkpoint(
        "final_content_ready",
        records=[],
        question_task_inventory={"items": [{
            "qid": "QINV-0001",
            "source_kind": "exercise",
            "source_label": anchor["source_label"],
            "parent_source_label": anchor.get("parent_source_label") or "",
            "topic_hint": "A stale topic",
            "raw_task": anchor["raw_task"],
            "normalized_task": anchor["raw_task"],
            "_activity_origin": False,
        }], "stats": {}},
        mined_types={"types": []},
        method_row_snapshot=[],
    )

    reasons = g._final_checkpoint_refresh_reasons(
        checkpoint,
        sections=sections,
        source_topic_excerpts=g._group_source_topic_excerpts(sections),
    )

    assert "source inventory semantics changed" in reasons


def test_saved_final_checkpoint_reconciles_wrong_figure_tag_without_api(
    monkeypatch,
):
    source = (
        "# Visual Symbols\n"
        "\\begin{figure}\n"
        "\\includegraphics{https://example.test/fig-17.png}\n"
        "\\caption{Fig. 17 - Germania}\n"
        "\\end{figure}\n"
        "\\begin{figure}\n"
        "\\includegraphics{https://example.test/fig-18.png}\n"
        "\\caption{Fig. 18 - Marianne}\n"
        "\\end{figure}\n"
    )
    analysis = (
        "Misconception/ Error Analysis: Misconceptions: Students may treat "
        "every national allegory as the same person.; Error Analysis: Students "
        "may identify the symbol without linking it to the named nation."
    )
    records = [
        {
            "topic": "Visual Symbols",
            "parent_concept": "National Allegory",
            "concept_title": "Marianne and Germania",
            "concept_details": (
                "Description: National allegories make an abstract nation "
                "visible through a named symbolic figure.\n"
                "Achieving Mastery: Reading a national allegory and naming "
                "the nation it personifies. // "
                "Types: Type 01: Interpret a named national allegory. "
                "Case 01: Read a symbol in its stated historical setting. "
                "Example 01: Refer to Fig. 18 and identify the national "
                "symbol. [img src=\"https://example.test/fig-17.png\" "
                "alt=\"Fig. 17 - Germania\"] // "
                + analysis
            ),
            "keywords": "Marianne, Germania, allegory",
            # Q1: the row carries the analysis section, so it models an
            # allotted row (assemble-stamped marker).
            "_aegis_analysis_allotments": ["LA-0001", "LA-0002"],
        },
        {
            "topic": "Visual Symbols",
            "parent_concept": "Culmination",
            "concept_title": "Culmination - Marianne and Germania",
            "concept_details": (
                "Description: Recap of the visual language of nationalism. // "
                "Types: Miscellaneous Type 01: Compare national symbols. "
                "Case 01: Connect two visual representations. "
                "Example 01: Compare the national symbols shown in the chapter."
            ),
            "keywords": "culmination, allegory",
        },
    ]
    inventory = {"items": [
        {
            "qid": "QINV-0001",
            "source_kind": "diagram_task",
            "topic_hint": "Visual Symbols",
            "raw_task": (
                "Refer to Fig. 18 and identify the national symbol."
            ),
        },
            {
                "qid": "QINV-0002",
                "source_kind": "exercise",
                "topic_hint": "Visual Symbols",
                "_chapter_wide_task": True,
                "raw_task": (
                    "Compare the national symbols shown in the chapter."
                ),
            },
        ], "stats": {}}
    mined_types = {"types": []}
    g._reset_placement_certifications(mined_types)
    g._certify_inventory_host(
        mined_types,
        "QINV-0001",
        records[0],
        basis="type_host_review",
    )
    # The chapter-wide task is rendered on (and certified to) the authored
    # culmination row. Under the purge the culmination survives verbatim —
    # it is no longer deleted by the recap re-stamp — so its certification
    # names the row it actually lives on.
    g._certify_inventory_host(
        mined_types,
        "QINV-0002",
        records[1],
        basis="type_host_review",
    )
    checkpoint = g._make_concept_checkpoint(
        "final_content_ready",
        records=records,
        question_task_inventory=inventory,
        mined_types=mined_types,
        method_row_snapshot=[],
    )
    emitted = []

    def no_api(*_args, **_kwargs):
        raise AssertionError("a saved final checkpoint should not call the API")

    monkeypatch.setattr(g, "_openai_json", no_api)
    out = g.concepts_from_mmd(
        source,
        subject="Social Science",
        chapter_title="Visual Symbols",
        live=True,
        resume_checkpoint=checkpoint,
        checkpoint_callback=emitted.append,
    )

    repaired = out[0]["concept_details"]
    assert '[img src="https://example.test/fig-18.png" ' in repaired
    assert 'alt="Fig. 18 - Marianne"]' in repaired
    assert "fig-17.png" not in repaired
    report = concept_validator.validate_concept_rows(
        out,
        allow_types=True,
        require_culmination=True,
        allow_culmination=True,
        strict_type_hierarchy=True,
        strict_analysis_section=True,
    )
    assert not {
        "figure_reference_without_image", "figure_reference_image_mismatch",
    } & {error["code"] for error in report["errors"]}
    assert emitted
    persisted = emitted[-1]
    assert persisted["stage"] == "final_content_ready"
    assert "fig-18.png" in persisted["records"][0]["concept_details"]


def test_saved_final_checkpoint_restores_missing_inventory_example_without_api(
    monkeypatch,
):
    source = "# Nationalism\nPolitical authority can be understood in civic terms."
    task = "Explain how popular sovereignty changed political authority."
    analysis = (
        "Misconception/ Error Analysis: Misconceptions: Students may believe "
        "sovereignty belongs only to a monarch.; Error Analysis: Students may "
        "treat political authority as hereditary rather than civic."
    )
    records = [
        {
            "topic": "Nationalism",
            "parent_concept": "Nation States",
            "concept_title": "Popular Sovereignty",
            "concept_details": (
                "Description: Popular sovereignty shifts political authority "
                "from rulers to citizens.\nAchieving Mastery: Explaining how "
                "civic authority replaces hereditary rule in a nation-state. "
                "// " + analysis
            ),
            "keywords": "sovereignty, citizens",
            # Q1: the row carries the analysis section, so it models an
            # allotted row (assemble-stamped marker).
            "_aegis_analysis_allotments": ["LA-0001", "LA-0002"],
        },
        {
            "topic": "Nationalism",
            "parent_concept": "Culmination",
            "concept_title": "Culmination - Nationalism",
            "concept_details": "Description: Recap",
            "keywords": "nationalism",
        },
    ]
    inventory = {
        "items": [{
            "qid": "QINV-0007",
            "source_kind": "checkpoint_question",
            "topic_hint": "Nationalism",
            "raw_task": task,
        }],
        "stats": {"total_inventory_items": 1},
    }
    mined_types = {"types": []}
    g._reset_placement_certifications(mined_types)
    g._certify_inventory_host(
        mined_types,
        "QINV-0007",
        records[0],
        basis="type_host_review",
    )
    checkpoint = g._make_concept_checkpoint(
        "final_content_ready",
        records=records,
        question_task_inventory=inventory,
        mined_types=mined_types,
        method_row_snapshot=[],
    )
    emitted = []

    def no_api(*_args, **_kwargs):
        raise AssertionError("a saved final checkpoint should not call the API")

    monkeypatch.setattr(g, "_openai_json", no_api)
    out = g.concepts_from_mmd(
        source,
        subject="Social Science",
        chapter_title="Nationalism",
        live=True,
        resume_checkpoint=checkpoint,
        checkpoint_callback=emitted.append,
    )

    assert g._rendered_inventory_coverage_defects(out, inventory) == {
        "missing": [], "duplicate": []}
    details = out[0]["concept_details"]
    assert "Type 01:" in details
    assert "Case 01:" in details
    assert f"Example 01: {task}" in details
    assert emitted
    persisted = emitted[-1]
    assert persisted["stage"] == "final_content_ready"
    assert task in persisted["records"][0]["concept_details"]


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

    def fake_api(system, user, **_kwargs):
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

    def fake_api(system, user, **_kwargs):
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
    assert body.count("Example 01:") == 1
    assert "Example 01: Find the sum of the first ten terms." in body


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


def test_inventory_coverage_survives_cosmetic_cleanup_punctuation():
    prompt = "Explain why 21 , values differ . . . Show the reasoning."
    inventory = {"items": [{"qid": "QINV-0001", "raw_task": prompt}]}
    record = {
        "topic": "Data Interpretation",
        "parent_concept": "Comparing Values",
        "concept_title": "Comparing Measurements",
        "concept_details": (
            "Description: Compare measured values. // Types: "
            "Type 01: Explain a comparison Case 01: Compare values "
            f"Example 01: {prompt}"
        ),
        "keywords": "",
    }
    cleaned = concept_cleanup.clean_concept_record(dict(record))

    assert "21, values differ..." in cleaned["concept_details"]
    assert g._rendered_inventory_coverage_defects([cleaned], inventory) == {
        "missing": [],
        "duplicate": [],
    }


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


def test_coverage_repair_preserves_synthesis_culmination_placement():
    mixed_prompt = (
        "Combine voltage, current, and resistance relationships to explain "
        "the circuit behavior."
    )
    cross_prompt = (
        "Compare electrical resistance with the heating effect produced by "
        "the same conductor."
    )
    inventory = {"items": [
        {
            "qid": "Q-MIXED",
            "raw_task": mixed_prompt,
            "topic_hint": "Electric Current",
        },
        {
            "qid": "Q-CROSS",
            "raw_task": cross_prompt,
            "topic_hint": "Electric Current",
        },
    ]}
    records = [
        {
            "topic": "Electric Current",
            "parent_concept": "Current",
            "concept_title": "Voltage-current Relationship",
            "concept_details": "Description: Current depends on voltage.",
            "keywords": "",
        },
        {
            "topic": "Electric Current",
            "parent_concept": "Culmination",
            "concept_title": "Culmination - Electric Current",
            "concept_details": (
                "Description: Recap current relationships. // Types: "
                "Type 01: Combining Current Relationships "
                "Case 01: Integrated relationships "
                "Example: Combine the supplied circuit relationships."
            ),
            "keywords": "",
        },
        {
            "topic": "Heating Effect",
            "parent_concept": "Culmination",
            "concept_title": "Culmination - Heating Effect",
            "concept_details": (
                "Description: Recap heating effects. // Types: "
                "Type 01: Comparing Electrical and Heating Effects "
                "Case 01: Integrated comparison "
                "Example: Compare the two effects using supplied values."
            ),
            "keywords": "",
        },
    ]

    def mined_type(type_id, title, scope, qid, prompt):
        return {
            "type_id": type_id,
            "type_title": title,
            "topic_match_hint": "Electric Current",
            "placement_scope": scope,
            "source_question_ids": [qid],
            "case_prompts": [{
                "case_id": f"CASE-{qid}",
                "case_title": title,
                "placement_scope": scope,
                "examples": [{
                    "source_question_id": qid,
                    "example_prompt": prompt,
                }],
            }],
        }

    mined = {"types": [
        mined_type(
            "TYPE-MIXED",
            "Combining Current Relationships",
            "mixed_synthesis",
            "Q-MIXED",
            mixed_prompt,
        ),
        mined_type(
            "TYPE-CROSS",
            "Comparing Electrical and Heating Effects",
            "cross_topic_synthesis",
            "Q-CROSS",
            cross_prompt,
        ),
    ]}

    repaired = g._repair_rendered_inventory_coverage(
        records, inventory, mined)

    assert mixed_prompt not in repaired[0]["concept_details"]
    assert mixed_prompt in repaired[1]["concept_details"]
    assert cross_prompt not in repaired[0]["concept_details"]
    assert cross_prompt not in repaired[1]["concept_details"]
    assert cross_prompt in repaired[2]["concept_details"]


def test_coverage_repair_uses_semantic_fallback_types_on_normal_concept():
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
    assert "Checkpoint Question" not in normal_details
    assert "Explaining How a Shared Identity" in normal_details
    assert "Interpreting Symbols Used" in normal_details
    assert normal_details.count("Case 01:") == 2
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
    from aegis_pipeline.openai_policy import DEFAULT_OPENAI_MODEL

    assert DEFAULT_OPENAI_MODEL == "gpt-5.6-luna"


def test_every_inventory_prompt_participates_in_coverage_however_short():
    """Coverage trusts the inventory; only emptiness has nothing to render.

    A short source question used to be filtered OUT of the expected-coverage
    map by a word count, so it could disappear from the ledger without any
    record — a Rule 1 violation and an R4 silent loss at once. Now every
    inventory item with text is expected, and the repair ladder places it
    verbatim rather than refusing it for being brief.
    """
    inventory = {"items": [
        {"qid": "QINV-0001", "raw_task": ""},
        {"qid": "QINV-0002", "raw_task": "Is 9 a cube?"},
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
    # The three-word question is OWED, exactly like its long neighbour; only
    # the text-less row has nothing to place.
    assert g._rendered_inventory_coverage_defects(records, inventory) == {
        "missing": ["QINV-0002", "QINV-0003"],
        "duplicate": [],
    }
    enforced = g._enforce_rendered_inventory_coverage(records, inventory)
    assert g._rendered_inventory_coverage_defects(enforced, inventory) == {
        "missing": [],
        "duplicate": [],
    }
    assert "closed electric circuit" in enforced[0]["concept_details"]
    # R4: the short question is on the page, not merely "not missing".
    assert "Is 9 a cube?" in enforced[0]["concept_details"]


def test_wording_collapse_names_every_qid_that_shares_the_prompt():
    """Two source questions, one wording, one rendered slot — say so.

    The exact-once coverage contract is keyed by normalized wording, so
    distinct QIDs printing the same short imperative ("Fill the table
    below.") share a single rendered Example. Widening participation to
    every inventory prompt makes that far more reachable, and the flag
    that records it must name the questions involved rather than claim
    the removed Example "renders elsewhere" (R4).
    """
    prompt = "Fill the table below."
    inventory = {"items": [
        {"qid": "QINV-0001", "source_kind": "checkpoint_question",
         "source_label": "L1", "raw_task": prompt},
        {"qid": "QINV-0002", "source_kind": "checkpoint_question",
         "source_label": "L2", "raw_task": prompt},
    ]}
    records = [
        {"title": "A", "topic": "T", "misconceptions": "x",
         "_aegis_release_qids": ["QINV-0001"],
         "concept_details": (
             "Description: d. Achieving Mastery: m. // Types: Type 01: A "
             f"Case 01: C Example 01: {prompt}")},
        {"title": "B", "topic": "T", "misconceptions": "x",
         "_aegis_release_qids": ["QINV-0002"],
         "concept_details": (
             "Description: d. Achieving Mastery: m. // Types: Type 01: B "
             f"Case 01: C Example 01: {prompt}")},
    ]

    enforced = g._enforce_rendered_inventory_coverage(records, inventory)

    flags = " ".join(
        flag for row in enforced for flag in (row.get("review_flags") or []))
    assert "QINV-0001" in flags and "QINV-0002" in flags, flags
    assert "share that one rendered Example" in flags
    # The case-uniqueness audit is the other half of the record: it keys
    # on QID, so it reports the collapse the coverage contract cannot see.
    from app.services.phase3 import assemble as assemble_mod

    findings = assemble_mod.audit_case_uniqueness(
        enforced,
        expected_examples=[
            {"qid": item["qid"], "prompt": prompt}
            for item in inventory["items"]
        ],
    )
    assert [f["code"] for f in findings] == ["qid_render_count_mismatch"]
    assert findings[0]["qids"] == ["QINV-0001", "QINV-0002"]


def test_enforce_coverage_hard_fails_on_residual_missing(monkeypatch):
    """A placeable inventory omission may never pass the final boundary."""
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
    with pytest.raises(
        RuntimeError,
        match=r"failed exact inventory coverage.*still-missing",
    ):
        g._enforce_rendered_inventory_coverage(records, inventory)


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


@pytest.mark.parametrize(
    "evidence",
    [
        (
            "The task introduces a systematic investigation of puri puffing. "
            "What are the different things that may change the way a puri "
            "puffs up when fried?"
        ),
        (
            "The task refers to recording observations during the puri-frying "
            "experiment. It is also a good idea to keep notes of everything "
            "that you see and sense when doing an experiment. Did the oil "
            "splatter, smell, or smoke?"
        ),
    ],
)
def test_activity_host_override_rejects_single_prefix_collision(evidence):
    concepts = {
        "CONCEPT-0001": {
            "concept_id": "CONCEPT-0001",
            "topic": "Exploring the Investigative World of Science",
            "concept": "Particle Motion in Different States of Matter",
            "is_culmination": False,
        },
        "CONCEPT-0002": {
            "concept_id": "CONCEPT-0002",
            "topic": "Exploring the Investigative World of Science",
            "concept": "Reflection and Refraction in Everyday Optics",
            "is_culmination": False,
        },
        "CONCEPT-0003": {
            "concept_id": "CONCEPT-0003",
            "topic": "Exploring the Investigative World of Science",
            "concept": (
                "Designing Fair Tests with Variables and Measurable Evidence"
            ),
            "is_culmination": False,
        },
    }
    activity = {
        "is_activity": True,
        "_source_task_evidence": evidence,
        "placement_scope": "normal",
    }

    assert g._high_confidence_assignment_override(
        activity, tuple(concepts), concepts) == ""


def test_activity_host_override_accepts_two_independent_title_signals():
    concepts = {
        "CONCEPT-0001": {
            "concept_id": "CONCEPT-0001",
            "topic": "Scientific Inquiry",
            "concept": "Particle Motion in Different States of Matter",
            "is_culmination": False,
        },
        "CONCEPT-0002": {
            "concept_id": "CONCEPT-0002",
            "topic": "Scientific Inquiry",
            "concept": (
                "Designing Fair Tests with Variables and Measurable Evidence"
            ),
            "is_culmination": False,
        },
    }
    activity = {
        "is_activity": True,
        "_source_task_evidence": (
            "Design a fair test with variables and record measurable evidence."
        ),
        "placement_scope": "normal",
    }

    assert g._high_confidence_assignment_override(
        activity, tuple(concepts), concepts) == "CONCEPT-0002"

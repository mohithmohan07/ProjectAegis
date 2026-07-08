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

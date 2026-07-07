"""Tests for Concept Map V2 — validation, rendering, and tag generation."""
from __future__ import annotations

import pytest

from app.services import concept_map_v2 as v2


def _cfg(**kw) -> v2.RunConfig:
    defaults = dict(
        board="CBSE",
        class_num=10,
        subject="Mathematics",
        chapter="Triangles",
        publication="NCERT",
        chapter_duration_minutes=360,
        expected_topics=["Similar Figures", "Pythagoras Theorem"],
        allow_introduction_topic=False,
    )
    defaults.update(kw)
    return v2.RunConfig(**defaults)


def _concept(**kw) -> v2.ConceptWithTypes:
    base = dict(
        concept_id="CONCEPT-0001",
        topic="Similar Figures",
        parent_concept="Similarity Basics",
        concept_title="All Circles Are Similar",
        description_body="Every circle has the same shape regardless of radius.",
        mastery="Classifying any two circles as similar.",
        misconception="Students may think different-sized circles are different shapes.",
        keywords=["similarity", "circles"],
        source_evidence=["section intro"],
    )
    base.update(kw)
    types = base.pop("types", [])
    return v2.ConceptWithTypes(types=types, **base)


def test_chapter_tag_is_code_generated():
    cfg = _cfg(publication="NCERT", subject="Geography", class_num=9)
    assert v2.chapter_tag(cfg) == "09_Social_Science_CBSE_NCERT"


def test_topic_display_name_strips_numbering():
    assert v2.topic_display_name("1.2 Similar Figures") == "Similar Figures"


def test_render_concept_description_format():
    c = _concept(types=[
        v2.MinedType(
            type_id="TYPE-0001",
            type_title="Identifying Similarity Facts",
            source_question_ids=["QINV-0001"],
            cases=[
                v2.MinedCase(
                    case_id="CASE-0001",
                    source_question_id="QINV-0001",
                    case_prompt=(
                        "State whether all squares are similar to each other "
                        "and justify using the definition of similarity."
                    ),
                ),
            ],
        ),
    ])
    out = v2.render_concept_description(c)
    assert out.startswith("Description: Every circle")
    assert "\nAchieving Mastery: Classifying any two circles as similar." in out
    assert "// Misconception:" in out
    assert "// Types: Type 01: Identifying Similarity Facts Case 01:" in out
    assert out.count("Misconception:") == 1


def test_render_skips_types_without_valid_cases():
    c = _concept(types=[
        v2.MinedType(
            type_id="TYPE-0001",
            type_title="Bad Type",
            cases=[v2.MinedCase("C1", "Q1", "short")],
        ),
    ])
    out = v2.render_concept_description(c)
    assert "Types:" not in out


def test_validate_rejects_invalid_topic():
    cfg = _cfg()
    c = _concept(topic="Invented Topic")
    errors = v2.validate_concept_map(cfg, [c])
    assert any("Invalid topic" in e for e in errors)


def test_validate_rejects_duplicate_concept():
    cfg = _cfg()
    a = _concept(concept_title="Same Title", topic="Similar Figures")
    b = _concept(concept_id="CONCEPT-0002", concept_title="Same Title",
                 topic="Pythagoras Theorem")
    errors = v2.validate_concept_map(cfg, [a, b])
    assert any("Duplicate concept" in e for e in errors)


def test_validate_rejects_english_lit_pedagogy():
    cfg = _cfg(subject="English Literature", chapter="A Letter to God",
               expected_topics=["A Letter to God"])
    c = _concept(
        topic="A Letter to God",
        concept_title="Pre-reading Prediction Activity",
        types=[],
    )
    errors = v2.validate_concept_map(cfg, [c])
    assert any("pedagogy" in e.lower() for e in errors)


def test_english_literature_locks_topic_to_chapter_name():
    cfg = _cfg(subject="English Literature", chapter="A Letter to God",
               expected_topics=["Introduction", "A Letter to God"])
    topics = v2.canonical_topics(cfg)
    assert topics == ["A Letter to God"]


def test_topic_concept_labels_use_titles():
    concepts = [
        _concept(concept_title="Concept A"),
        _concept(concept_id="C2", concept_title="Concept B"),
    ]
    assert v2.topic_concept_labels(concepts) == "Concept A, Concept B"


def test_build_output_rows_topic_display_equals_name():
    cfg = _cfg()
    c = _concept(types=[])
    meta, rows = v2.build_output_rows(cfg, [c])
    assert rows[0]["topic_display_name"] == "Similar Figures"
    assert rows[0]["topic_name"] == "Similar Figures"
    assert meta["chapter_duration_minutes"] == 360


def test_to_legacy_records_shape():
    cfg = _cfg()
    _, rows = v2.build_output_rows(cfg, [_concept(types=[])])
    legacy = v2.to_legacy_records(rows)
    assert legacy[0]["concept_title"] == "All Circles Are Similar"
    assert "Achieving Mastery:" in legacy[0]["concept_details"]


def test_run_config_parses_duration_from_chapter_field():
    cfg = v2.run_config_from_meta(
        board="CBSE", grade="10", subject="History",
        chapter_title="Nationalism", publication="NCERT",
        chapter_duration="540 minutes",
        expected_topics=["Introduction", "The French Revolution"],
    )
    assert cfg.chapter_duration_minutes == 540
    assert cfg.allow_introduction_topic is True


def test_assert_finalized_config_requires_topics():
    cfg = _cfg(expected_topics=[])
    with pytest.raises(ValueError, match="expected_topics"):
        v2.assert_finalized_config(cfg)

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


def test_is_culmination_row_detects_flags_and_titles():
    assert v2.is_culmination_row(_concept(is_culmination=True))
    assert v2.is_culmination_row(_concept(
        parent_concept="Culmination", concept_title="Topic Recap"))
    assert v2.is_culmination_row(_concept(
        concept_title="Culmination - Ohm's Law"))
    assert not v2.is_culmination_row(_concept(concept_title="Ohm's Law"))


def test_validate_locked_topic_coverage_requires_one_normal_per_topic():
    locked = ["Electric Current", "Ohm's Law"]
    rows = [_concept(topic="Electric Current")]
    errors = v2.validate_locked_topic_coverage(
        locked_topics=locked, rows=rows)
    assert any("Ohm's Law" in e for e in errors)
    assert not any("Electric Current" in e for e in errors)


def test_validate_locked_topic_coverage_rejects_illegal_topic():
    locked = ["Electric Current"]
    rows = [_concept(topic="Invented Topic")]
    errors = v2.validate_locked_topic_coverage(
        locked_topics=locked, rows=rows)
    assert any("illegal_topic" in e for e in errors)


def test_ensure_exactly_one_culmination_per_locked_topic():
    locked = ["Topic A", "Topic B"]
    rows = [
        _concept(topic="Topic A", concept_title="Concept A1"),
        _concept(concept_id="C2", topic="Topic B", concept_title="Concept B1"),
    ]
    out = v2.ensure_exactly_one_culmination_per_locked_topic(
        locked_topics=locked, rows=rows)
    assert len(out) == 4
    assert sum(v2.is_culmination_row(r) for r in out) == 2
    assert out[1].concept_title.startswith("Culmination -")
    assert out[-1].concept_title.startswith("Culmination -")


def test_validate_pre_deposit_requires_one_culmination_per_topic():
    locked = ["Topic A"]
    rows = v2.ensure_exactly_one_culmination_per_locked_topic(
        locked_topics=locked,
        rows=[_concept(topic="Topic A")],
    )
    assert v2.validate_pre_deposit(locked_topics=locked, rows=rows) == []


def test_build_culmination_title_joins_concept_names():
    concepts = [
        _concept(concept_title="Alpha"),
        _concept(concept_id="C2", concept_title="Beta"),
        _concept(concept_id="C3", concept_title="Gamma"),
    ]
    title = v2.build_culmination_title(concepts)
    assert title == "Culmination - Alpha, Beta and Gamma"


def test_strip_model_culmination_rows():
    rows = [
        _concept(concept_title="Normal"),
        _concept(concept_id="C2", concept_title="Culmination - Normal",
                 parent_concept="Culmination"),
    ]
    assert len(v2.strip_model_culmination_rows(rows)) == 1


def test_generate_post_learning_concepts_safe_rebuilds_culminations():
    locked = ["Topic A", "Topic B"]
    calls: list[str] = []

    def fake_llm(system, prompt):
        calls.append(prompt)
        if len(calls) == 1:
            return {"concepts": [
                {"concept_id": "C1", "topic": "Topic A",
                 "parent_concept": "P", "concept_title": "Concept A",
                 "description_body": "Body A", "mastery": "Mastery A.",
                 "keywords": ["a"], "types": []},
                {"concept_id": "C2", "topic": "Topic B",
                 "parent_concept": "P", "concept_title": "Concept B",
                 "description_body": "Body B", "mastery": "Mastery B.",
                 "keywords": ["b"], "types": []},
                {"concept_id": "CUL", "topic": "Topic A",
                 "parent_concept": "Culmination",
                 "concept_title": "Culmination - LLM row",
                 "description_body": "Recap", "mastery": "M.", "keywords": [],
                 "types": [], "is_culmination": True},
            ]}
        raise AssertionError("repair should not run")

    rows = v2.generate_post_learning_concepts_safe(
        locked_topics=locked,
        chapter_text="chapter",
        question_inventory=[],
        call_llm_json=fake_llm,
        build_master_prompt=lambda: "master",
        validate_structural=lambda _: [],
    )
    assert len(calls) == 1
    assert len(rows) == 4
    assert sum(v2.is_culmination_row(r) for r in rows) == 2
    assert not any(
        r.concept_title == "Culmination - LLM row" for r in rows
    )


def test_generate_post_learning_concepts_safe_runs_strict_repair():
    locked = ["Topic A", "Topic B"]
    calls = 0
    repair_prompts: list[str] = []

    def fake_llm(system, prompt):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"concepts": [
                {"concept_id": "C1", "topic": "Topic A",
                 "parent_concept": "P", "concept_title": "Only A",
                 "description_body": "Body", "mastery": "Mastery.",
                 "keywords": ["a"], "types": []},
            ]}
        repair_prompts.append(prompt)
        return {"concepts": [
            {"concept_id": "C1", "topic": "Topic A",
             "parent_concept": "P", "concept_title": "Only A",
             "description_body": "Body", "mastery": "Mastery.",
             "keywords": ["a"], "types": []},
            {"concept_id": "C2", "topic": "Topic B",
             "parent_concept": "P", "concept_title": "Concept B",
             "description_body": "Body B", "mastery": "Mastery B.",
             "keywords": ["b"], "types": []},
        ]}

    rows = v2.generate_post_learning_concepts_safe(
        locked_topics=locked,
        chapter_text="chapter",
        question_inventory=[],
        call_llm_json=fake_llm,
        build_master_prompt=lambda: "master",
        validate_structural=lambda _: [],
    )
    assert calls == 2
    assert len(rows) == 4
    assert repair_prompts
    assert "Do not shrink the map" in repair_prompts[0]


def test_master_prompt_includes_topic_coverage_rules():
    cfg = _cfg()
    prompt = v2.build_concept_map_prompt(cfg, "chapter text", [])
    assert "TOPIC COVERAGE:" in prompt
    assert "Do not create culmination rows" in prompt
    assert "Do not target a fixed number of concepts" in prompt


def test_topic_match_score_fuzzy_series_parallel():
    assert v2.topic_match_score("Resistors in Series", "Series Resistors") >= 0.5
    assert v2.topic_match_score(
        "Equivalent Resistance in Series", "Resistors in Series") >= 0.5


def test_inject_fallback_concepts_for_missing_topics():
    locked = ["Topic A", "Resistors in Series", "Resistors in Parallel"]
    rows = [_concept(topic="Topic A", concept_title="Concept A")]
    chapter = (
        "## Resistors in Series\n"
        "When resistors are connected in series, the same current flows through each.\n\n"
        "## Resistors in Parallel\n"
        "In a parallel combination, the potential difference is the same across branches."
    )
    out, injected = v2.inject_fallback_concepts_for_missing_topics(
        rows,
        locked_topics=locked,
        chapter_text=chapter,
        question_inventory=[],
    )
    assert injected == 2
    assert not v2.topics_missing_coverage(locked, out)


def test_generate_post_learning_falls_back_when_repair_leaves_gaps():
    locked = ["Topic A", "Resistors in Series", "Resistors in Parallel"]
    calls = 0

    def fake_llm(system, prompt):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"concepts": [
                {"concept_id": "C1", "topic": "Topic A",
                 "parent_concept": "P", "concept_title": "Concept A",
                 "description_body": "Body A", "mastery": "Mastery A.",
                 "keywords": ["a"], "types": []},
            ]}
        if "MISSING LOCKED TOPICS" in prompt:
            return {"concepts": []}
        return {"concepts": [
            {"concept_id": "C1", "topic": "Topic A",
             "parent_concept": "P", "concept_title": "Concept A",
             "description_body": "Body A", "mastery": "Mastery A.",
             "keywords": ["a"], "types": []},
        ]}

    chapter = (
        "## Resistors in Series\n"
        "When resistors are connected in series, the same current flows through each.\n\n"
        "## Resistors in Parallel\n"
        "In a parallel combination, the potential difference is the same across branches."
    )
    rows = v2.generate_post_learning_concepts_safe(
        locked_topics=locked,
        chapter_text=chapter,
        question_inventory=[],
        call_llm_json=fake_llm,
        build_master_prompt=lambda: "master",
        validate_structural=lambda _: [],
    )
    normal = [r for r in rows if not v2.is_culmination_row(r)]
    culm = [r for r in rows if v2.is_culmination_row(r)]
    assert len(normal) == 3
    assert len(culm) == 3
    assert not v2.topics_missing_coverage(locked, rows)

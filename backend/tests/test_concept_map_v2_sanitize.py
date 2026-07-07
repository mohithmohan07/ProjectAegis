"""Tests for Concept Map V2 case prompt sanitization (Figure/Table/Activity refs)."""
from __future__ import annotations

from app.services import concept_map_v2 as v2

_CDN = "https://cdn.mathpix.com/cropped/fig-11-6.jpg"


def _physics_case() -> v2.MinedCase:
    return v2.MinedCase(
        case_id="CASE-0001",
        source_question_id="QINV-0001",
        case_prompt=(
            "In Activity 11.4, insert a voltmeter across the ends X and Y of the "
            "series combination of three resistors, as shown in Figure 11.6."
        ),
    )


def test_sanitize_case_prompt_neutralizes_figure_table_activity():
    out = v2.sanitize_case_prompt(
        'Use Table 11.2 to compare iron and mercury. See Figure 11.5 for setup.',
    )
    assert "Table 11.2" not in out
    assert "Figure 11.5" not in out
    assert "the given table" in out
    assert "the figure" in out


def test_sanitize_case_prompt_inlines_figure_from_chapter_mmd():
    chapter = (
        "Activity text with ![Fig. 11.6 Series circuit](%s) embedded." % _CDN
    )
    out = v2.sanitize_case_prompt(_physics_case().case_prompt, chapter_text=chapter)
    assert "Figure 11.6" not in out
    assert "Activity 11.4" not in out
    assert _CDN in out
    assert "![Fig. 11.6 Series circuit]" in out


def test_sanitize_concept_cases_passes_validation():
    cfg = v2.run_config_from_meta(
        board="CBSE", grade="10", subject="Physics",
        chapter_title="Electricity", publication="NCERT",
        chapter_duration="360 minutes",
        expected_topics=["Ohm's law", "Resistance", "Series combination"],
    )
    concept = v2.ConceptWithTypes(
        concept_id="C1",
        topic="Series combination",
        parent_concept="Circuits",
        concept_title="Series combination of resistors",
        description_body="When resistors are connected in series, the same current flows through each.",
        mastery="Calculating equivalent resistance for resistors in series.",
        misconception=None,
        keywords=["series"],
        source_evidence=["section"],
        types=[
            v2.MinedType(
                type_id="T1",
                type_title="Series circuit observation and relation",
                source_question_ids=["Q1"],
                cases=[_physics_case()],
            ),
        ],
    )
    chapter = f"Source ![Fig. 11.6 diagram]({_CDN})"
    v2.sanitize_concept_cases([concept], chapter_text=chapter)
    errors = v2.validate_concept_map(cfg, [concept])
    assert not [e for e in errors if "source-artifact" in e.lower()]

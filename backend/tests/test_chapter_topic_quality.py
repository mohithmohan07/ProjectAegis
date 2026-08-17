"""Chapter/topic metadata quality, topic segregation, mastery statement
formatting, detailed culmination recaps, and the dropped workbook columns."""
import pytest

from app import models
from app.services import build_concepts
from app.services import concept_refiner as cr
from app.services import concept_validator as cv
from app.services import generation as g
from app.services import prompts


# --------------------------------------------------------------------------- #
# Detailed culmination Recap
# --------------------------------------------------------------------------- #

def _rec(title, details, topic="Topic A", parent="P"):
    return {"topic": topic, "parent_concept": parent,
            "concept_title": title, "concept_details": details, "keywords": ""}


def test_recap_machinery_is_gone_and_authored_culminations_survive():
    """The 'Recap of <titles>' prefix was code-composed, not decided by the
    model. It is deleted: the culmination Description is the model-authored
    consolidation, and no deterministic pass rewrites it."""
    assert not hasattr(cr, "recap_text")
    assert not hasattr(cr, "set_culmination_recap")

    authored = (
        "Description: Together these ideas let the learner move from single "
        "criteria to choosing the right similarity argument in mixed "
        "problems. // Types: Type 01: Mix Case 01: q"
    )
    records = [
        _rec("Similarity of Figures", "Description: a"),
        _rec("Similar Triangles", "Description: b"),
        _rec("Culmination - Similarity", authored, parent="Culmination"),
    ]
    out = cr.refine_chapter(records)
    assert out[-1]["concept_details"] == authored
    assert "Recap of" not in out[-1]["concept_details"]


def test_validator_accepts_authored_consolidation_description():
    rows = [
        {"topic": "T", "parent_concept": "P", "concept_title": "A",
         "concept_details": "Description: teaches a thing clearly and fully",
         "keywords": "k"},
        {"topic": "T", "parent_concept": "P", "concept_title": "B",
         "concept_details": "Description: teaches another thing fully",
         "keywords": "k"},
        {"topic": "T", "parent_concept": "Culmination",
         "concept_title": "Culmination - T",
         "concept_details": "Description: a long authored synthesis of A "
                            "and B // Types: Type 01: Mix Case 01: combine",
         "keywords": "k"},
    ]
    # An authored consolidation with NO "Recap of" prefix passes.
    report = cv.validate_concept_rows(rows, require_culmination=True)
    assert not [e for e in report["errors"]
                if e["code"] == "culmination_description"]

    # Only a culmination without any authored Description content fails
    # (mechanics: the labeled section must exist and be non-empty).
    rows[2]["concept_details"] = "Description:  // Types: Type 01: M Case 01: q"
    report = cv.validate_concept_rows(rows, require_culmination=True)
    assert [e for e in report["errors"] if e["code"] == "culmination_description"]


def test_topic_without_authored_culmination_ships_flagged_not_invented():
    rows = [
        {"topic": "T", "parent_concept": "P", "concept_title": "A",
         "concept_details": "Description: teaches a thing clearly and fully",
         "keywords": "k"},
        {"topic": "T", "parent_concept": "P", "concept_title": "B",
         "concept_details": "Description: teaches another thing fully",
         "keywords": "k"},
    ]
    report = cv.validate_concept_rows(rows, require_culmination=True)
    flagged = [
        e for e in report["errors"] if e["code"] == "culmination_count"
    ]
    assert flagged and flagged[0]["severity"] == "warning"
    assert "flagged for review" in flagged[0]["message"]
    # A warning never blocks the run.
    assert report["ok"]


# --------------------------------------------------------------------------- #
# Achieving Mastery statement on its own line
# --------------------------------------------------------------------------- #

def test_mastery_statement_gets_line_break():
    details = ("Description: The midpoint theorem relates midpoints to parallel "
               "sides. Achieving Mastery: Using the midpoint property to set up "
               "the smaller triangles correctly. // Misconception: m")
    out = cr.format_mastery_statement(details)
    assert ("\nAchieving Mastery: Using the midpoint property to set up the "
            "smaller triangles correctly.") in out
    assert "Misconception: m" in out


def test_mastery_label_variants_are_normalized():
    out = cr.format_mastery_statement(
        "Description: body text. Mastery: solving mixed problems unaided.")
    assert out.endswith("\nAchieving Mastery: solving mixed problems unaided.")
    # Idempotent: re-running does not stack labels or newlines.
    assert cr.format_mastery_statement(out) == out


def test_mastery_formatting_leaves_plain_descriptions_alone():
    details = "Description: no mastery statement here. // Misconception: m"
    assert cr.format_mastery_statement(details) == details


def test_refine_chapter_formats_mastery_but_not_culminations():
    records = [
        _rec("A", "Description: body. Achieving Mastery: applying the rule. // "
                  "Types: Type 01: X Case 01: q"),
        _rec("Culmination - Topic A", "Description: An authored synthesis.",
             parent="Culmination"),
    ]
    out = cr.refine_chapter(records)
    assert "\nAchieving Mastery: applying the rule." in out[0]["concept_details"]
    # The authored culmination Description is left exactly as authored.
    assert out[1]["concept_details"] == "Description: An authored synthesis."


def test_description_refine_prompt_requires_mastery_line():
    text = prompts.get_text("concepts.description_refine.system")
    assert "Achieving Mastery:" in text
    assert "line" in text.lower()


# --------------------------------------------------------------------------- #
# Topic segregation (one-topic-for-all collapse)
# --------------------------------------------------------------------------- #

def test_skeleton_prompt_forbids_umbrella_topics():
    text = prompts.get_text("concepts.skeleton.system")
    assert "TOPIC SEGREGATION IS MANDATORY" in text
    assert "NEVER a topic" in text


def test_topic_segregation_verdict_carries_the_evidence(monkeypatch):
    """The model, not arithmetic, judges segregation — from source evidence."""
    records = [
        _rec("Meaning of Similarity", "Description: a", topic="Triangles"),
        _rec("AAA Criterion", "Description: b", topic="Triangles"),
    ]
    captured = {}

    def fake_openai(system, user, **kw):
        captured["system"] = system
        captured["user"] = user
        return {"verdict": "restructure", "reason": "rows collapsed"}

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    verdict = g._topic_segregation_verdict_via_api(
        records, meta=g._metadata(subject="Math"),
        source_topic_excerpts=[
            {"topic": "Similarity of Figures",
             "excerpt": "Ratios teach similarity of figures."},
            {"topic": "Criteria for Similarity",
             "excerpt": "Angle criteria teach similar triangles."},
        ],
        headings=["Similarity of Figures", "Criteria for Similarity"],
    )
    assert verdict == {"restructure": True, "reason": "rows collapsed"}
    assert "SECTION HEADINGS" in captured["user"]
    assert "Ratios teach similarity of figures." in captured["user"]
    assert "Meaning of Similarity" in captured["user"]
    assert "never" in captured["system"]  # arithmetic never decides


def test_topic_segregation_verdict_accepts_faithful(monkeypatch):
    monkeypatch.setattr(
        g, "_openai_json",
        lambda *a, **kw: {"verdict": "Faithful", "reason": "rows match"})
    verdict = g._topic_segregation_verdict_via_api(
        [_rec("C", "Description: d", topic="T")],
        meta=g._metadata(subject="Math"),
        source_topic_excerpts=[{"topic": "T", "excerpt": "teaches C"}],
        headings=["T", "U"],
    )
    assert verdict == {"restructure": False, "reason": "rows match"}


def test_topic_segregation_verdict_fails_closed_on_no_decision(monkeypatch):
    """No positive verdict → the run stops; nothing is guessed."""
    monkeypatch.setattr(
        g, "_openai_json", lambda *a, **kw: {"verdict": "maybe"})
    with pytest.raises(RuntimeError, match="did not positively decide"):
        g._topic_segregation_verdict_via_api(
            [_rec("C", "Description: d", topic="T")],
            meta=g._metadata(subject="Math"),
            source_topic_excerpts=[{"topic": "T", "excerpt": "teaches C"}],
            headings=["T", "U"],
        )


# ``_topic_headings`` is the structural FALLBACK topic list: the contract
# wrapper prefers the semantic graph's model-ruled outline, and this fallback
# no longer censors structure by heading wording (§3 purge). Only mechanics
# remain: part-suffix merging, the parser's own "General" placeholder, OCR
# math fragments, and numbered-outline selection.
def test_topic_headings_merge_parts_and_keep_structural_blocks():
    sections = [
        {"heading": "Similar Triangles"},
        {"heading": "Similar Triangles (part 2/3)"},
        {"heading": "EXERCISE 6.1"},
        {"heading": "General"},
        {"heading": "Areas of Similar Triangles"},
    ]
    assert g._topic_headings(sections) == [
        "Similar Triangles", "EXERCISE 6.1", "Areas of Similar Triangles"]


def test_topic_headings_keep_structural_ocr_headings_unfiltered():
    """No heading vocabulary decides what is "not a topic" — whether a
    Solution/Summary block is a topic is the semantic outline's call."""
    sections = [
        {"heading": "Similarity of Triangles"},
        {"heading": "Solution"},
        {"heading": "Example 3"},
        {"heading": "Summary"},
        {"heading": "Tick the Correct Answer and Justify"},
        {"heading": "Note to the Reader"},
        {"heading": "Pythagoras Theorem"},
    ]
    assert g._topic_headings(sections) == [
        "Similarity of Triangles",
        "Solution",
        "Example 3",
        "Summary",
        "Tick the Correct Answer and Justify",
        "Note to the Reader",
        "Pythagoras Theorem",
    ]


def test_topic_headings_skip_only_math_fragments():
    """OCR math fragments stay filtered (mechanics); exercise headings do
    not — they are structure the semantic outline may still discard."""
    sections = [
        {"heading": "Criteria for Similarity of Triangles"},
        {"heading": "$ AMC PNR $"},
        {"heading": "EXERCISE 6.6 (Optional)*"},
        {"heading": "Pythagoras Theorem"},
    ]
    assert g._topic_headings(sections) == [
        "Criteria for Similarity of Triangles",
        "EXERCISE 6.6 (Optional)*",
        "Pythagoras Theorem",
    ]


def test_topic_headings_keep_question_type_headings_unfiltered():
    sections = [
        {"heading": "Shaping of the Earth's Surface"},
        {"heading": "Short Answer Questions"},
        {"heading": "Long Answer Questions"},
        {"heading": "Multiple Choice Questions"},
        {"heading": "Fill in the Blanks"},
        {"heading": "Forces of Gradation"},
    ]
    assert g._topic_headings(sections) == [
        "Shaping of the Earth's Surface",
        "Short Answer Questions",
        "Long Answer Questions",
        "Multiple Choice Questions",
        "Fill in the Blanks",
        "Forces of Gradation",
    ]


def test_topic_headings_prefer_main_heading_level_over_micro_subheadings():
    sections = [{"heading": "Landforms: Earth's Living Canvas", "heading_level": 1}]
    sections.extend([
        {"heading": "Shaping of the Earth's Surface", "heading_level": 2},
        {"heading": "Forces of Gradation", "heading_level": 2},
        {"heading": "Weathering and Erosion", "heading_level": 2},
        {"heading": "Depositional Landforms", "heading_level": 2},
    ])
    sections.extend(
        {"heading": f"Micro Heading {i}", "heading_level": 3}
        for i in range(1, 18)
    )
    sections.extend([
        {"heading": "Short Answer Questions", "heading_level": 2},
        {"heading": "Multiple Choice Questions", "heading_level": 2},
    ])
    assert g._topic_headings(sections) == [
        "Shaping of the Earth's Surface",
        "Forces of Gradation",
        "Weathering and Erosion",
        "Depositional Landforms",
        "Short Answer Questions",
        "Multiple Choice Questions",
    ]


def test_topic_headings_keep_two_numbered_mains_above_three_subtopics():
    sections = g.parse_mmd_sections(
        "## 1 Main A\nParent A.\n\n"
        "### 1.1 A One\nDetail.\n\n"
        "### 1.2 A Two\nDetail.\n\n"
        "## 2 Main B\nParent B.\n\n"
        "### 2.1 B One\nDetail.\n"
    )

    assert g._topic_headings(sections) == ["Main A", "Main B"]


def test_topic_headings_do_not_promote_one_subtopic_to_a_peer_main():
    sections = g.parse_mmd_sections(
        "## 1 Main Topic\nParent material.\n\n"
        "### 1.1 Only Subtopic\nChild material.\n"
    )

    assert g._topic_headings(sections) == ["Main Topic"]


def test_topic_headings_keep_one_numbered_parent_above_two_subtopics():
    sections = g.parse_mmd_sections(
        "## 1 Main Topic\nParent material.\n\n"
        "### 1.1 First Subtopic\nChild A.\n\n"
        "### 1.2 Second Subtopic\nChild B.\n"
    )

    assert g._topic_headings(sections) == ["Main Topic"]


def test_topic_headings_do_not_return_single_chapter_title_as_topic():
    sections = [
        {"heading": "Landforms: Earth's Living Canvas", "heading_level": 1},
        {"heading": "Shaping of the Earth's Surface", "heading_level": 2},
        {"heading": "Forces of Gradation", "heading_level": 2},
        {"heading": "Weathering and Erosion", "heading_level": 2},
        {"heading": "Depositional Landforms", "heading_level": 2},
    ]
    assert g._topic_headings(sections) == [
        "Shaping of the Earth's Surface",
        "Forces of Gradation",
        "Weathering and Erosion",
        "Depositional Landforms",
    ]


def test_topic_headings_prefer_numbered_sections_over_ocr_quote_noise():
    sections = g.parse_mmd_sections(
        "\\section*{Introduction to Trigonometry}\n"
        "\\section*{There is perhaps nothing which so occupies the middle position.}\n"
        "\\section*{- J.F. Herbart (1890)}\n"
        "\\subsection*{8.1 Introduction}\n"
        "\\subsection*{8.2 Trigonometric Ratios}\n"
        "\\section*{EXERCISE 8.1}\n"
        "\\subsection*{8.3 Trigonometric Ratios of Some Specific Angles}\n"
        "\\subsection*{8.4 Trigonometric Identities}\n"
        "\\subsection*{8.5 Summary}\n"
    )
    assert g._topic_headings(sections) == [
        "Introduction",
        "Trigonometric Ratios",
        "Trigonometric Ratios of Some Specific Angles",
        "Trigonometric Identities",
        "Summary",
    ]


def test_topic_headings_keep_numbered_mixed_math_heading():
    sections = g.parse_mmd_sections(
        "\\section*{Arithmetic Progressions}\n"
        "\\section*{5.1 Introduction}\n"
        "\\subsection*{5.2 Arithmetic Progressions}\n"
        "\\section*{5.3 nth Term of an AP}\n"
        "\\subsection*{5.4 Sum of First \\(\\boldsymbol{n}\\) Terms of an AP}\n"
        "\\subsection*{5.5 Summary}\n"
    )
    assert g._topic_headings(sections) == [
        "Introduction",
        "Arithmetic Progressions",
        "nth Term of an AP",
        "Sum of First n Terms of an AP",
        # The numbered 5.5 Summary section is real structure; whether it is
        # a teaching topic is the semantic outline's decision.
        "Summary",
    ]


def test_topic_headings_ignore_lettered_exercise_items_when_decimal_sections_exist():
    sections = g.parse_mmd_sections(
        "\\section*{Shaping of the Earth's Surface}\n"
        "\\subsection*{2.1 Interior of the Earth}\n"
        "\\subsection*{2.2 Theory of Plate Tectonics}\n"
        "\\subsection*{2.3 Weathering and Erosion}\n"
        "\\section*{V. Very short answer type questions}\n"
        "\\section*{I. Source-based Questions}\n"
        "\\section*{1. Layers of the Earth}\n"
        "\\section*{2. Types of Plate Movements}\n"
        "\\section*{1. Real Life Connect Activity + Life Skills and Values}\n"
    )
    assert g._topic_headings(sections) == [
        "Interior of the Earth",
        "Theory of Plate Tectonics",
        "Weathering and Erosion",
    ]


def test_snap_topics_to_headings_merges_micro_topics():
    headings = ["Triangles", "Introduction", "Similar Figures",
                "Similarity of Triangles", "Pythagoras Theorem"]
    records = [
        _rec("Chapter Scope", "Description: a", topic="Triangles"),  # = chapter title
        _rec("Meaning of Similarity", "Description: b", topic="Similar Figures"),
        _rec("Scale Factor", "Description: c", topic="Similarity From Side Ratios"),
        _rec("BPT", "Description: d", topic="Similarity of Triangles"),
        _rec("Shadow Problems", "Description: e", topic="Shadow Problems Using Triangles"),
    ]
    out = g._snap_topics_to_headings(records, headings, chapter_title="Triangles")
    assert [r["topic"] for r in out] == [
        "Introduction",           # chapter title is not a topic -> first section
        "Similar Figures",
        "Similar Figures",        # invented micro-topic -> preceding real section
        "Similarity of Triangles",
        "Similarity of Triangles",
    ]


def test_snap_topics_skipped_with_too_few_headings():
    records = [_rec("C", "Description: d", topic="Invented Topic")]
    out = g._snap_topics_to_headings(records, ["Only", "Two"], chapter_title="X")
    assert out[0]["topic"] == "Invented Topic"


def test_scrub_keeps_model_topics_and_strips_only_numbering():
    """The scrub is mechanics only (§3 purge): numbering artifacts go, but
    no wording vocabulary reassigns or deletes a model-authored row."""
    records = [
        _rec("Meaning of Similarity", "Description: a", topic="Similar Figures"),
        _rec("Worked ratio problem", "Description: b", topic="Solution"),
        _rec("Chapter recap idea", "Description: c", topic="Summary"),
        _rec("Pythagoras statement", "Description: d", topic="Pythagoras Theorem"),
        _rec("Numbering only", "Description: e", topic="EXERCISE 6.1"),
    ]
    out = g._scrub_section_numbers(records)
    assert [r["topic"] for r in out] == [
        "Similar Figures",
        "Solution",
        "Summary",
        "Pythagoras Theorem",
        # "EXERCISE 6.1" scrubs to nothing, so the row inherits the previous
        # topic instead of being orphaned or dropped.
        "Pythagoras Theorem",
    ]
    assert [r["concept_title"] for r in out] == [
        "Meaning of Similarity",
        "Worked ratio problem",
        "Chapter recap idea",
        "Pythagoras statement",
        "Numbering only",
    ]


def test_enforce_culminations_never_invents_rows_types_or_recaps():
    authored = "Description: An authored consolidation of both criteria."
    records = [
        _rec("AA Criterion", "Description: a", topic="T1"),
        _rec("SSS Criterion", "Description: b", topic="T1"),
        _rec("Culmination - T1", authored, topic="T1",
             parent="Culmination"),
    ]
    out = g._enforce_culminations(records)
    culm = out[-1]
    # Authored content kept verbatim: no synthesized Types, no code-composed
    # recap, and the authored title survives.
    assert culm["concept_details"] == authored
    assert culm["concept_title"] == "Culmination - T1"
    assert "Types:" not in culm["concept_details"]
    # A culmination with an inventory-backed Type is preserved.
    records[2]["concept_details"] = (
        "Description: Recap // Types: Type 01: Real mined mix Case 01: combine x and y")
    out = g._enforce_culminations(records)
    assert "Real mined mix" in out[-1]["concept_details"]
    assert "Mixed application combining" not in out[-1]["concept_details"]

    # A topic the model left without a culmination ships WITHOUT one — no
    # invented row — and duplicate authored rows are normalized to one.
    out = g._enforce_culminations([
        _rec("AA Criterion", "Description: a", topic="T1"),
        _rec("SSS Criterion", "Description: b", topic="T1"),
    ])
    assert [r["concept_title"] for r in out] == [
        "AA Criterion", "SSS Criterion",
    ]


def test_restructure_topics_reassigns_only_topics(monkeypatch):
    records = [
        _rec("Meaning of Similarity", "Description: a", topic="Triangles"),
        _rec("AAA Criterion", "Description: b", topic="Triangles"),
    ]

    def fake_openai(system, user, **kw):
        assert "SECTION HEADINGS" in user
        assert "SOURCE TOPIC EXCERPTS" in user
        assert "Ratios teach similarity of figures." in user
        assert "Angle criteria teach similar triangles." in user
        return {"rows": [
            {"topic": "Similarity of Figures", "parent_concept": "P",
             "concept": "Meaning of Similarity",
             "concept_description": "Description: REWRITTEN", "keywords": "x"},
            {"topic": "Criteria for Similarity", "parent_concept": "P",
             "concept": "AAA Criterion",
             "concept_description": "Description: REWRITTEN", "keywords": "x"},
        ]}

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    out = g._restructure_topics_via_api(
        records, meta=g._metadata(subject="Math"),
        source_topic_excerpts=[
            {
                "topic": "Similarity of Figures",
                "excerpt": "Ratios teach similarity of figures.",
            },
            {
                "topic": "Criteria for Similarity",
                "excerpt": "Angle criteria teach similar triangles.",
            },
        ],
    )
    assert [r["topic"] for r in out] == [
        "Similarity of Figures", "Criteria for Similarity"]
    # ONLY the topic moved; descriptions/keywords stay as authored upstream.
    assert out[0]["concept_details"] == "Description: a"
    assert out[1]["concept_details"] == "Description: b"


def test_skeleton_user_message_lists_section_headings(monkeypatch):
    captured = {}

    def fake_openai(system, user, **kw):
        captured.setdefault("user", user)
        return {"rows": [
            {"topic": "Real Heading", "parent_concept": "P", "concept": "C",
             "concept_description": "Description: teaches one clear idea here",
             "keywords": "k"},
        ]}

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    g._extract_skeleton_via_api(
        [{"text": "HEADING PATH: Real Heading\nSECTION TEXT:\nBody",
          "sections": [{"heading": "Real Heading"}]}],
        meta=g._metadata(subject="Math"),
    )
    assert "SECTION HEADINGS IN THIS CHUNK" in captured["user"]
    assert "- Real Heading" in captured["user"]


# --------------------------------------------------------------------------- #
# Chapter/topic metadata via API
# --------------------------------------------------------------------------- #

def test_chapter_meta_via_api_parses_fields(monkeypatch):
    def fake_openai(system, user, **kw):
        assert "Topics and their concepts" in user
        return {
            "chapter_description": "Builds similarity from figures to proofs.",
            "chapter_duration_minutes": "270",
            "topics": [
                {"topic": "Similar Triangles",
                 "topic_description": "Defines similarity and its criteria."},
                {"topic": "", "topic_description": "dropped"},
            ],
        }

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    out = g.chapter_meta_via_api(
        meta=g._metadata(subject="Math"),
        topics=[{"topic": "Similar Triangles", "concepts": ["A", "B"]}],
        live=True,
    )
    assert out["chapter_description"] == "Builds similarity from figures to proofs."
    assert out["chapter_duration_minutes"] == 270
    assert out["topic_descriptions"] == {
        "similar triangles": "Defines similarity and its criteria."}


def test_chapter_meta_via_api_is_empty_in_dry_mode():
    assert g.chapter_meta_via_api(
        meta=g._metadata(subject="Math"),
        topics=[{"topic": "T", "concepts": ["A"]}],
        live=False,
    ) == {}


def test_sync_chapter_topic_summary_uses_api_meta(db):
    chapter = models.Chapter(
        chapter_code="10CBMA_MetaQ", board="CBSE", grade="10",
        subject="Mathematics", unit="Mathematics Unit",
        chapter_title="Meta Quality Chapter",
        chapter_display_name="Meta Quality Chapter",
        chapter_description="This chapter develops 5 concept(s) across 1 topic(s): T.",
        chapter_duration="",
    )
    db.add(chapter)
    db.flush()
    topic = models.Topic(
        chapter_id=chapter.id, topic_title="Similar Triangles",
        topic_display_name="Similar Triangles", pre_post_learning="Post",
        topic_description="Covers A, B.",
    )
    db.add(topic)
    db.commit()

    build_concepts._sync_chapter_topic_summary(chapter, {
        "chapter_description": "A strong teacher-facing chapter description.",
        "chapter_duration_minutes": 315,
        "topic_descriptions": {
            "similar triangles": "Develops the criteria for triangle similarity.",
        },
    })
    assert chapter.chapter_description == "A strong teacher-facing chapter description."
    assert chapter.chapter_duration == "315 minutes"
    assert topic.topic_description == "Develops the criteria for triangle similarity."


def test_sync_chapter_topic_summary_preserves_finalized_duration(db):
    chapter = models.Chapter(
        chapter_code="10CBMA_MetaDuration", board="CBSE", grade="10",
        subject="Mathematics", unit="Mathematics Unit",
        chapter_title="Final Duration Chapter",
        chapter_display_name="Final Duration Chapter",
        chapter_duration="160 minutes",
    )
    db.add(chapter)
    db.flush()
    topic = models.Topic(
        chapter_id=chapter.id, topic_title="Similar Triangles",
        topic_display_name="Similar Triangles", pre_post_learning="Post",
    )
    db.add(topic)
    db.commit()

    build_concepts._sync_chapter_topic_summary(chapter, {
        "chapter_duration_minutes": 315,
        "topic_descriptions": {"similar triangles": "Topic description."},
    })
    assert chapter.chapter_duration == "160 minutes"


def test_sync_chapter_topic_summary_falls_back_without_meta(db):
    chapter = models.Chapter(
        chapter_code="10CBMA_MetaF", board="CBSE", grade="10",
        subject="Mathematics", unit="Mathematics Unit",
        chapter_title="Meta Fallback Chapter",
        chapter_display_name="Meta Fallback Chapter",
    )
    db.add(chapter)
    db.flush()
    topic = models.Topic(
        chapter_id=chapter.id, topic_title="T",
        topic_display_name="T", pre_post_learning="Post",
    )
    db.add(topic)
    db.flush()
    db.add(models.Concept(
        topic_id=topic.id, concept_title="A", concept_display_name="A",
        concept_details="Description: d"))
    db.commit()

    build_concepts._sync_chapter_topic_summary(chapter)
    assert chapter.chapter_description
    assert chapter.chapter_duration.endswith("minutes")
    assert topic.topic_description == "Covers A."


def test_mastery_line_pass_normalizes_format_and_never_backfills(monkeypatch):
    """Settle authors mastery under the rewritten Phase 3: this pass never
    calls the API and never invents a template line — it only normalizes the
    line-broken format. A row still missing mastery is the Polish pass's to
    repair with a real model call."""
    records = [
        _rec(
            "Has One",
            "Description: body. Achieving Mastery: Applying the relationship "
            "accurately to unfamiliar problems.",
        ),
        _rec("Missing One", "Description: body only. // Types: Type 01: X Case 01: q"),
        _rec("Culmination - T", "Description: An authored synthesis.",
             parent="Culmination"),
    ]

    def forbidden_openai(*_args, **_kwargs):
        pytest.fail("the mastery-line pass must not call the API")

    monkeypatch.setattr(g, "_openai_json", forbidden_openai)
    out = g._ensure_mastery_lines_via_api(records, meta=g._metadata(subject="Math"))
    # The authored inline mastery is normalized onto its own line.
    assert ("\nAchieving Mastery: Applying the relationship accurately to "
            "unfamiliar problems." in out[0]["concept_details"])
    # A row without mastery stays without one — no template backfill.
    assert "Achieving Mastery" not in out[1]["concept_details"]
    assert "correctly in new problems" not in out[1]["concept_details"]
    assert "Types: Type 01: X Case 01: q" in out[1]["concept_details"]
    # Culminations are never targeted.
    assert out[2]["concept_details"] == "Description: An authored synthesis."


def test_mastery_template_is_gone_and_polish_repairs_missing_mastery():
    """The deterministic 'Applying {title} correctly in new problems.'
    template is purged from app/; a missing mastery line reaches the Polish
    pass as model-repairable content."""
    import pathlib
    import subprocess

    app_dir = pathlib.Path(g.__file__).resolve().parents[1]
    hits = subprocess.run(
        ["grep", "-rn", "correctly in new problems", str(app_dir)],
        capture_output=True, text=True,
    )
    real_hits = [
        line for line in hits.stdout.splitlines()
        if "__pycache__" not in line
    ]
    assert real_hits == []

    from app.services.phase3 import polish

    assert "missing_mastery_statement" in polish.CONTENT_CODES
    failures = polish._failures(
        [{
            "topic": "T", "parent_concept": "P",
            "concept_title": "AA Similarity Criterion",
            "concept_details": (
                "Description: body only, teaching the idea fully. // "
                "Misconception/ Error Analysis: Misconceptions: Learners "
                "may believe similarity requires equal sides."
            ),
            "keywords": "",
        }],
        source_text="",
    )
    assert any(
        entry["code"] == "missing_mastery_statement"
        for entry in failures.get(0, [])
    )


def test_duplicate_titles_are_dropped_chapter_wide():
    records = [
        _rec("Similarity vs Congruence", "Description: a", topic="T1"),
        _rec("AAA Criterion", "Description: b", topic="T1"),
        # Same concept restated under another topic (chunked extraction echo).
        _rec("Similarity vs Congruence", "Description: a again", topic="T2"),
    ]
    out = g._dedupe_titles_chapter_wide(records)
    assert [r["concept_title"] for r in out] == [
        "Similarity vs Congruence", "AAA Criterion"]
    # First statement (the teaching home) is the one kept.
    assert out[0]["topic"] == "T1"


def test_control_chars_are_stripped_from_records_and_cells():
    from app.services import concept_cleanup
    from app.bulk_import import writer as bw

    rec = {"topic": "T", "parent_concept": "P",
           "concept_title": "Angles Sum to 180\x04",
           "concept_details": "Description: angles sum to 180\x04 in a triangle.",
           "keywords": ""}
    out = concept_cleanup.clean_concept_record(dict(rec))
    assert "\x04" not in out["concept_title"]
    assert "\x04" not in out["concept_details"]
    # Writer-level guard for values that bypass record cleanup.
    assert bw._safe_cell("sum is 180\x04.") == "sum is 180."
    assert bw._safe_cell("line1\nline2\tok") == "line1\nline2\tok"  # kept
    assert bw._safe_cell(42) == 42


def test_chapter_meta_prompt_contract():
    text = prompts.get_text("concepts.chapter_meta.system")
    assert "chapter_duration_minutes" in text
    assert "topic_description" in text
    assert "never generic filler" in text.lower()

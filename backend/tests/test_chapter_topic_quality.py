"""Chapter/topic metadata quality, topic segregation, mastery statement
formatting, detailed culmination recaps, and the dropped workbook columns."""
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


def test_recap_lists_merged_concepts():
    records = [
        _rec("Similarity of Figures", "Description: a"),
        _rec("Similar Triangles", "Description: b"),
        _rec("Basic Proportionality Theorem", "Description: c"),
        _rec("Culmination - Similarity",
             "Description: Recap // Types: Type 01: Mix Case 01: q",
             parent="Culmination"),
    ]
    out = cr.set_culmination_recap(records)
    culm = out[-1]["concept_details"]
    assert culm.startswith(
        "Description: Recap of Similarity of Figures, Similar Triangles "
        "and Basic Proportionality Theorem.")
    # Types are untouched.
    assert "Types: Type 01: Mix" in culm


def test_recap_scopes_to_own_topic():
    records = [
        _rec("A", "Description: a", topic="T1"),
        _rec("Culmination - T1", "Description: Recap", topic="T1", parent="Culmination"),
        _rec("B", "Description: b", topic="T2"),
        _rec("Culmination - T2", "Description: Recap", topic="T2", parent="Culmination"),
    ]
    out = cr.set_culmination_recap(records)
    assert out[1]["concept_details"] == "Description: Recap of A."
    assert out[3]["concept_details"] == "Description: Recap of B."


def test_recap_without_normal_concepts_falls_back():
    records = [_rec("Culmination - Empty", "Description: Recap", parent="Culmination")]
    out = cr.set_culmination_recap(records)
    assert out[0]["concept_details"] == "Description: Recap"


def test_validator_accepts_detailed_recap():
    rows = [
        {"topic": "T", "parent_concept": "P", "concept_title": "A",
         "concept_details": "Description: teaches a thing clearly and fully",
         "keywords": "k"},
        {"topic": "T", "parent_concept": "Culmination",
         "concept_title": "Culmination - T",
         "concept_details": "Description: Recap of A. // Types: "
                            "Type 01: Mix Case 01: combine",
         "keywords": "k"},
    ]
    report = cv.validate_concept_rows(rows, require_culmination=True)
    assert not [e for e in report["errors"]
                if e["code"] == "culmination_description"]

    rows[1]["concept_details"] = "Description: a long synthesis // Types: Type 01: M Case 01: q"
    report = cv.validate_concept_rows(rows, require_culmination=True)
    assert [e for e in report["errors"] if e["code"] == "culmination_description"]


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
        _rec("Culmination - Topic A", "Description: Recap", parent="Culmination"),
    ]
    out = cr.refine_chapter(records)
    assert "\nAchieving Mastery: applying the rule." in out[0]["concept_details"]
    assert out[1]["concept_details"].startswith("Description: Recap of A.")


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


def test_topics_look_collapsed_detection():
    headings = ["Introduction to Similarity", "Similar Triangles",
                "Criteria for Similarity", "Pythagoras Theorem"]
    one_topic = [_rec(f"C{i}", "Description: d", topic="Triangles") for i in range(8)]
    assert g._topics_look_collapsed(one_topic, headings)
    two_topics = [
        _rec(f"C{i}", "Description: d", topic="T1" if i < 6 else "T2")
        for i in range(14)
    ]
    assert g._topics_look_collapsed(two_topics, headings)
    healthy = [
        _rec(f"C{i}", "Description: d", topic=headings[i % 4]) for i in range(12)
    ]
    assert not g._topics_look_collapsed(healthy, headings)
    assert not g._topics_look_collapsed(one_topic, ["Only Heading"])


def test_topic_headings_skip_exercises_parts_and_general():
    sections = [
        {"heading": "Similar Triangles"},
        {"heading": "Similar Triangles (part 2/3)"},
        {"heading": "EXERCISE 6.1"},
        {"heading": "General"},
        {"heading": "Areas of Similar Triangles"},
    ]
    assert g._topic_headings(sections) == [
        "Similar Triangles", "Areas of Similar Triangles"]


def test_topic_headings_skip_structural_ocr_headings():
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
        "Similarity of Triangles", "Pythagoras Theorem"]


def test_topic_headings_skip_optional_exercises_and_math_fragments():
    sections = [
        {"heading": "Criteria for Similarity of Triangles"},
        {"heading": "$ AMC PNR $"},
        {"heading": "EXERCISE 6.6 (Optional)*"},
        {"heading": "Pythagoras Theorem"},
    ]
    assert g._topic_headings(sections) == [
        "Criteria for Similarity of Triangles", "Pythagoras Theorem"]


def test_topic_headings_skip_exercise_question_type_headings():
    sections = [
        {"heading": "Shaping of the Earth's Surface"},
        {"heading": "Short Answer Questions"},
        {"heading": "Long Answer Questions"},
        {"heading": "Multiple Choice Questions"},
        {"heading": "Fill in the Blanks"},
        {"heading": "Forces of Gradation"},
    ]
    assert g._topic_headings(sections) == [
        "Shaping of the Earth's Surface", "Forces of Gradation"]


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
    ]


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


def test_scrub_merges_structural_topics_into_previous():
    records = [
        _rec("Meaning of Similarity", "Description: a", topic="Similar Figures"),
        _rec("Worked ratio problem", "Description: b", topic="Solution"),
        _rec("Chapter recap idea", "Description: c", topic="Summary"),
        _rec("Pythagoras statement", "Description: d", topic="Pythagoras Theorem"),
    ]
    out = g._scrub_section_numbers(records)
    # Solution inherits the previous teaching topic; Summary filler is dropped.
    assert [r["topic"] for r in out] == [
        "Similar Figures", "Similar Figures", "Pythagoras Theorem"]
    assert [r["concept_title"] for r in out] == [
        "Meaning of Similarity", "Worked ratio problem", "Pythagoras statement"]


def test_enforce_culminations_injects_starter_types():
    records = [
        _rec("AA Criterion", "Description: a", topic="T1"),
        _rec("SSS Criterion", "Description: b", topic="T1"),
        _rec("Culmination - T1", "Description: Recap", topic="T1",
             parent="Culmination"),
    ]
    out = g._enforce_culminations(records)
    culm = out[-1]["concept_details"]
    assert "Types: Type 01: Mixed application combining the topic's concepts" in culm
    assert "AA Criterion, SSS Criterion" in culm
    # A culmination that already has meaningful Types is left alone.
    records[2]["concept_details"] = (
        "Description: Recap // Types: Type 01: Real mined mix Case 01: combine x and y")
    out = g._enforce_culminations(records)
    assert "Real mined mix" in out[-1]["concept_details"]
    assert "Mixed application combining" not in out[-1]["concept_details"]


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


def test_mastery_line_pass_completes_missing_rows(monkeypatch):
    records = [
        _rec("Has One", "Description: body.\nAchieving Mastery: doing it right."),
        _rec("Missing One", "Description: body only. // Types: Type 01: X Case 01: q"),
        _rec("Culmination - T", "Description: Recap", parent="Culmination"),
    ]

    def fake_openai(system, user, **kw):
        assert "Missing One" in user and "Has One" not in user
        return {"rows": [{
            "topic": "Topic A", "parent_concept": "P", "concept": "Missing One",
            "concept_description": ("Description: body only.\nAchieving Mastery: "
                                    "applying the rule to fresh problems."),
            "keywords": "",
        }]}

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    out = g._ensure_mastery_lines_via_api(records, meta=g._metadata(subject="Math"))
    assert ("\nAchieving Mastery: applying the rule to fresh problems."
            in out[1]["concept_details"])
    # Types are untouched; culminations are never targeted.
    assert "Types: Type 01: X Case 01: q" in out[1]["concept_details"]
    assert out[2]["concept_details"] == "Description: Recap"


def test_mastery_line_deterministic_fallback():
    records = [_rec("AA Similarity Criterion", "Description: body only.")]
    out = g._ensure_mastery_lines_via_api(
        records, meta=g._metadata(subject="Math"), use_api=False)
    assert ("\nAchieving Mastery: Applying AA Similarity Criterion correctly "
            "in new problems.") in out[0]["concept_details"]


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

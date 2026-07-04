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


def test_restructure_topics_reassigns_only_topics(monkeypatch):
    records = [
        _rec("Meaning of Similarity", "Description: a", topic="Triangles"),
        _rec("AAA Criterion", "Description: b", topic="Triangles"),
    ]

    def fake_openai(system, user, **kw):
        assert "SECTION HEADINGS" in user
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
        headings=["Similarity of Figures", "Criteria for Similarity"])
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
        chapter_duration="60 minutes",
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


def test_chapter_meta_prompt_contract():
    text = prompts.get_text("concepts.chapter_meta.system")
    assert "chapter_duration_minutes" in text
    assert "topic_description" in text
    assert "never generic filler" in text.lower()

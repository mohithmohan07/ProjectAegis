"""Concept-mapping output format: tags in title columns, label columns,
required fields, and tag-stripping round-trip."""
import io

from app import bulk_import as bi
from app import models
from app.bulk_import import writer
from tests.conftest import convert_concept_upload, stream_result


def test_strip_helpers():
    assert bi.strip_title_tag("Understanding Social Science (09_SocialScience_CBSE)") \
        == "Understanding Social Science"
    assert bi.strip_title_tag("What is Social Science (09CBSS_Ch_PL_Topic)") \
        == "What is Social Science"
    # A real parenthetical without underscores is preserved.
    assert bi.strip_title_tag("Photosynthesis (C3)") == "Photosynthesis (C3)"
    assert bi.strip_topic_title("Topic 01: Meaning of Social Science (09CBSS_Ch_PL)") \
        == "Meaning of Social Science"


def test_writer_composes_tags_labels_and_clean_display(db):
    concept = (
        db.query(models.Concept)
        .join(models.Topic).join(models.Chapter)
        .filter(models.Chapter.board != "").first()
    )
    assert concept is not None
    chapter = concept.topic.chapter
    row = writer._concept_to_row(concept, "objective")

    # Chapter: tag in title (col 0), clean display (col 1).
    assert bi.strip_title_tag(row[0]) == chapter.chapter_title
    assert "_" in row[0] and row[0].endswith(")")
    assert row[1] == chapter.chapter_title

    # Topic title col (6): "Topic NN: <title> (<tag>)"; display col (7):
    # "Topic NN: <title>" with NO code/tag.
    clean_topic = bi.strip_topic_title(concept.topic.topic_title)
    assert row[6].startswith("Topic ") and row[6].endswith(")")
    assert bi.strip_topic_title(row[6]) == clean_topic
    assert row[7].startswith("Topic ")
    assert bi.strip_topic_title(row[7]) == clean_topic
    assert "_" not in row[7]  # display name carries no code/tag

    # topic_concept_labels (col 9) lists the topic's concept titles.
    assert bi.OBJECTIVE_FIELDS[9] == "topic_concept_labels"
    assert concept.concept_title in row[9]

    # Concept: tag in title (col 12), clean display (col 13).
    assert bi.strip_title_tag(row[12]) == concept.concept_title
    assert row[13] == concept.concept_title


def test_deposit_applies_numbering_recap_titlecase_and_topic_columns(db):
    """End-to-end deposit: continuous Type NN, Miscellaneous for culmination,
    Recap description, Title Case, and tagged pre/post topic columns."""
    from app.services import build_concepts

    chapter = models.Chapter(
        chapter_code="07CBMA_FmtRules", board="CBSE", grade="07",
        subject="Mathematics", unit="Mathematics Unit",
        chapter_title="Format Rules Chapter",
        chapter_display_name="Format Rules Chapter",
    )
    db.add(chapter)
    db.commit()

    records = [
        {"topic": "operations on numbers", "concept_title": "addition of integers",
         "concept_details": ("Description: a // Types: Type 01: Direct Case 01: 2+3 "
                             "Case 02: 5+9 // Misconception: m"), "keywords": ""},
        {"topic": "operations on numbers", "concept_title": "Culmination - Operations On Numbers",
         "concept_details": ("Description: a long synthesis paragraph // "
                             "Types: Type 01: Mixed Case 01: combine ops // "
                             "Misconception: keep me"), "keywords": ""},
        {"topic": "powers and roots", "concept_title": "squares of numbers",
         "concept_details": ("Description: b // Types: Type 01: Compute Case 01: 4^2 "
                             "// Misconception: m"), "keywords": ""},
    ]
    build_concepts._deposit_concepts(db, chapter, records, "Post", "")
    build_concepts._sync_chapter_topic_summary(chapter)
    db.commit()

    by_title = {c.concept_title: c for t in chapter.topics for c in t.concepts}

    # Title Case on topics and concepts.
    topic_titles = {t.topic_title for t in chapter.topics}
    assert {"Operations on Numbers", "Powers and Roots"} <= topic_titles
    assert "Addition of Integers" in by_title

    # Continuous Type numbering across the chapter (culmination excluded).
    assert "Type 01: Direct" in by_title["Addition of Integers"].concept_details
    assert "Type 02: Compute" in by_title["Squares of Numbers"].concept_details

    # Culmination: Miscellaneous Type sequence + Description collapses to "Recap".
    culm = by_title["Culmination - Operations on Numbers"].concept_details
    assert "Miscellaneous Type 01: Mixed" in culm
    assert "Description: Recap" in culm
    assert "long synthesis" not in culm
    assert "Misconception: keep me" in culm

    # Column E (post_topics) lists tagged Topic Titles (with the code).
    assert "Topic 01:" in chapter.post_topics
    assert "07CBMA" in chapter.post_topics  # the code is present

    # Topic display column shows "Topic NN: <Title>" without the code.
    row = writer._concept_to_row(by_title["Addition of Integers"], "objective")
    assert row[4] == chapter.post_topics  # chapter band col E
    assert row[7].startswith("Topic 01: Operations on Numbers")
    assert "_" not in row[7]


def test_group_label_columns_present_and_ordered():
    f = bi.OBJECTIVE_FIELDS
    assert f[22] == "concept_question_labels"
    gi = f.index("group_question_labels")
    assert f[gi + 1] == "related_digicards"


def test_writer_leaves_group_columns_empty_for_concept_rows(db):
    """Concept-catalog rows must not pre-fill group columns at generation time."""
    concept = (
        db.query(models.Concept)
        .join(models.Topic).join(models.Chapter)
        .filter(models.Chapter.board != "").first()
    )
    assert concept is not None
    row = writer._concept_to_row(concept, "objective")
    basic_idx = bi.OBJECTIVE_FIELDS.index("basic_groups")
    assert row[basic_idx] == ""
    assert row[basic_idx + 1] == ""
    assert row[basic_idx + 2] == ""


def test_writer_falls_back_parent_concept_without_optional_column(db):
    concept = db.query(models.Concept).join(models.Topic).join(models.Chapter).first()
    concept.parent_concept = "Cell Organisation"
    row = writer._concept_to_row(concept, "objective")
    related_idx = bi.OBJECTIVE_FIELDS.index("related_concepts")
    assert "parent: Cell Organisation" in row[related_idx]


def test_append_concepts_uses_optional_parent_concept_column(db, tmp_path):
    import openpyxl

    concept = db.query(models.Concept).join(models.Topic).join(models.Chapter).first()
    concept.parent_concept = "Cell Organisation"
    db.flush()

    path = tmp_path / "parent_template.xlsx"
    wb = writer._new_workbook()
    ws = wb[bi.SHEET_OBJECTIVE]
    insert_at = bi.OBJECTIVE_FIELDS.index("concept_display_name") + 2
    ws.insert_cols(insert_at)
    ws.cell(row=2, column=insert_at, value="parent_concept")
    wb.save(path)

    writer.append_concepts(db, path, [concept.id])
    out = openpyxl.load_workbook(path)[bi.SHEET_OBJECTIVE]
    headers = [c.value for c in out[2]]
    parent_idx = headers.index("parent_concept") + 1
    assert out.cell(row=3, column=parent_idx).value == "Cell Organisation"


def test_roundtrip_recovers_clean_titles(db):
    """Export concepts (tagged cells) then re-import: clean titles are recovered."""
    concept = db.query(models.Concept).join(models.Topic).join(models.Chapter).first()
    data = writer.write_concepts_workbook(db, [concept.id])
    import openpyxl
    ws = openpyxl.load_workbook(io.BytesIO(data))[bi.SHEET_OBJECTIVE]
    rows = [r for r in ws.iter_rows(min_row=3, values_only=True) if r and r[0]]
    assert rows
    # The exported title cell carries a tag; stripping recovers the clean title.
    assert bi.strip_title_tag(str(rows[0][12])) == concept.concept_title
    assert "_" in str(rows[0][12])  # a tag is present


def test_deposit_fills_required_fields(client, db):
    # A fresh chapter with blank (NA-equivalent) required fields.
    chapter = models.Chapter(
        chapter_code="09CBSS_ReqTest", board="CBSE", grade="09",
        subject="Social Science", unit="Social Science Unit",
        chapter_title="Req Test Chapter", chapter_display_name="Req Test Chapter",
        chapter_duration="", chapter_description="", pre_topics="", post_topics="",
    )
    db.add(chapter)
    db.commit()
    chapter_id = chapter.id

    files = {"file": ("req.txt", io.BytesIO(
        b"## Meaning of Social Science\nWhat is social science studies.\n"
        b"Scope of social science explained.\n"
        b"## Importance of Social Science\nWhy social science matters today."
    ), "text/plain")}
    job = client.post("/build-concepts/post-learning/uploads", files=files).json()
    convert_concept_upload(client, job["id"])
    stream_result(client.post(
        f"/build-concepts/post-learning/uploads/{job['id']}/generate",
        json={"target_chapter_id": chapter_id}))

    db.expire_all()
    chapter = db.get(models.Chapter, chapter_id)
    # Required fields are filled (no "NA").
    assert chapter.chapter_duration.endswith("minutes")
    assert chapter.chapter_description and chapter.chapter_description.lower() != "na"
    # pre/post topic lists are comma-separated (never semicolons).
    assert ";" not in (chapter.post_topics or "")
    # Newly created post topics carry a synthesized summary.
    new = [t for t in chapter.topics if t.topic_title in
           ("Meaning of Social Science", "Importance of Social Science")]
    assert new and all(t.topic_description for t in new)

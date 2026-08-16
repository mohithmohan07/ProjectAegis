"""Regression tests for the final Build Concepts workbook boundary."""

import io
import re

import openpyxl

from app import bulk_import as bi
from app import models
from app.bulk_import import writer
from app.services import build_concepts
from app.services import generation as g


def _concept(topic, title, *, order, details="Description: Current content."):
    return models.Concept(
        topic=topic,
        concept_title=title,
        concept_display_name=title,
        concept_details=details,
        source_order=order,
    )


def test_scoped_export_excludes_stale_labels_and_uses_source_topic_order(db):
    chapter = models.Chapter(
        chapter_code="10CBSS_Delivery",
        board="CBSE",
        grade="10",
        subject="Social Science",
        unit="History",
        chapter_title="Delivery Integrity",
        chapter_display_name="Delivery Integrity",
    )
    # Deliberately persist topic 6 before topics 2-5, reproducing the
    # historical-id order 1,6,2,3,4,5 from the failed workbook.
    titles = ["Topic One", "Topic Six", "Topic Two", "Topic Three", "Topic Four", "Topic Five"]
    persisted = [
        models.Topic(
            chapter=chapter,
            topic_title=title,
            topic_display_name=title,
            pre_post_learning="Post",
        )
        for title in titles
    ]
    canonical = [
        persisted[0], persisted[2], persisted[3],
        persisted[4], persisted[5], persisted[1],
    ]
    selected = []
    for order, topic in enumerate(canonical, start=1):
        topic.source_order = order
        topic.topic_description = "Stale description from the prior topology."
        # Historical concepts remain in the database, but are not part of this
        # accepted 6-row topology.
        topic.concepts.append(_concept(
            topic, f"Stale Concept {order}", order=90))
        current = _concept(topic, f"Current Concept {order}", order=1)
        topic.concepts.append(current)
        selected.append(current)
    db.add(chapter)
    db.commit()

    active_ids = {concept.id for concept in selected}
    build_concepts._sync_chapter_topic_summary(
        chapter,
        {},
        active_concept_ids=active_ids,
        pre_post="Post",
    )
    db.commit()

    data = writer.write_concepts_workbook(
        db, [concept.id for concept in selected])
    ws = openpyxl.load_workbook(
        io.BytesIO(data), data_only=True)[bi.SHEET_OBJECTIVE]
    rows = list(ws.iter_rows(min_row=3, values_only=True))

    assert len(rows) == len(selected)
    assert [
        str(row[writer._IDX_TOPIC_TITLE]).split(":", 1)[0]
        for row in rows
    ] == [f"Topic {number:02d}" for number in range(1, 7)]
    labels_index = len(bi.CHAPTER_FIELDS) + bi.TOPIC_FIELDS.index(
        "topic_concept_labels")
    description_index = len(bi.CHAPTER_FIELDS) + bi.TOPIC_FIELDS.index(
        "topic_description")
    for number, row in enumerate(rows, start=1):
        labels = str(row[labels_index])
        assert f"Current Concept {number}" in labels
        assert "Stale Concept" not in labels
        assert str(row[description_index]).startswith(
            f"Covers Current Concept {number}")


def test_multi_chapter_type_numbers_restart_without_false_split_failure(db):
    selected = []
    for number in (1, 2):
        chapter = models.Chapter(
            chapter_code=f"10CBMA_TypeScope{number}",
            board="CBSE",
            grade="10",
            subject="Mathematics",
            unit="Algebra",
            chapter_title=f"Type Scope {number}",
            chapter_display_name=f"Type Scope {number}",
        )
        topic = models.Topic(
            chapter=chapter,
            topic_title="Methods",
            topic_display_name="Methods",
            pre_post_learning="Post",
            source_order=1,
        )
        selected.append(_concept(
            topic,
            f"Method {number}",
            order=1,
            details=(
                "Description: Apply the method. // "
                "Types: Type 01: Direct method "
                "Case 01: Use the stated method. "
                f"Example 01: Complete chapter {number} method task."
            ),
        ))
        db.add(chapter)
    db.commit()

    # Read-back validation scopes Type identity by chapter, so Type 01 may
    # correctly restart in another chapter.
    data = writer.write_concepts_workbook(
        db, [concept.id for concept in selected])
    assert data.startswith(b"PK")


def test_tagged_copy_is_not_counted_as_a_second_type_host(db):
    chapter = models.Chapter(
        chapter_code="10CBSS_TaggedType",
        board="CBSE",
        grade="10",
        subject="Social Science",
        unit="History",
        chapter_title="Tagged Type",
        chapter_display_name="Tagged Type",
    )
    home = models.Topic(
        chapter=chapter,
        topic_title="Home",
        topic_display_name="Home",
        pre_post_learning="Post",
        source_order=1,
    )
    secondary = models.Topic(
        chapter=chapter,
        topic_title="Secondary",
        topic_display_name="Secondary",
        pre_post_learning="Post",
        source_order=2,
    )
    concept = _concept(
        home,
        "Reusable Interpretation",
        order=1,
        details=(
            "Description: Interpret the source. // "
            "Types: Type 01: Source interpretation "
            "Case 01: Explain one source feature. "
            "Example 01: Explain the supplied source feature."
        ),
    )
    db.add(chapter)
    db.flush()
    db.add(models.ConceptTag(concept=concept, topic=secondary))
    db.commit()

    # The same concept is serialized twice as an intentional tag, but remains
    # one semantic Type host.
    data = writer.write_concepts_workbook(db, [concept.id])
    assert data.startswith(b"PK")


def test_tagged_secondary_concept_is_not_required_in_that_topics_recap(db):
    chapter = models.Chapter(
        chapter_code="10CBSS_TaggedRecap",
        board="CBSE",
        grade="10",
        subject="Social Science",
        unit="History",
        chapter_title="Tagged Recap",
        chapter_display_name="Tagged Recap",
    )
    home = models.Topic(
        chapter=chapter,
        topic_title="Home",
        topic_display_name="Home",
        pre_post_learning="Post",
        source_order=1,
    )
    secondary = models.Topic(
        chapter=chapter,
        topic_title="Secondary",
        topic_display_name="Secondary",
        pre_post_learning="Post",
        source_order=2,
    )
    tagged = _concept(home, "Tagged Supporting Idea", order=1)
    primary = _concept(secondary, "Secondary Foundation", order=1)
    culmination = _concept(
        secondary,
        "Culmination - Secondary",
        order=2,
        details="Description: Recap of Secondary Foundation.",
    )
    db.add(chapter)
    db.flush()
    db.add(models.ConceptTag(concept=tagged, topic=secondary))
    db.commit()

    data = writer.write_concepts_workbook(
        db, [tagged.id, primary.id, culmination.id])
    assert data.startswith(b"PK")


def test_readback_accepts_one_type_number_on_two_real_hosts(db):
    """Under the rewritten Phase 3 the Host pass routes each question to the
    concept it belongs to, so two Cases of one chapter-wide Type may
    legitimately live on different concept hosts; the read-back gate must not
    reject that as a split Type (the single-host expectation belonged to the
    legacy allocator)."""
    chapter = models.Chapter(
        chapter_code="10CBSS_SplitType",
        board="CBSE",
        grade="10",
        subject="Social Science",
        unit="History",
        chapter_title="Split Type",
        chapter_display_name="Split Type",
    )
    topic = models.Topic(
        chapter=chapter,
        topic_title="National Symbols",
        topic_display_name="National Symbols",
        pre_post_learning="Post",
        source_order=1,
    )
    first = _concept(
        topic,
        "Marianne",
        order=1,
        details=(
            "Description: Interpret Marianne. // "
            "Types: Type 17: Interpreting national allegories "
            "Case 01: Interpret Marianne. "
            "Example 01: Explain one attribute of Marianne."
        ),
    )
    second = _concept(
        topic,
        "Germania",
        order=2,
        details=(
            "Description: Interpret Germania. // "
            "Types: Type 17: Interpreting national allegories "
            "Case 02: Interpret Germania. "
            "Example 01: Explain one attribute of Germania."
        ),
    )
    db.add(chapter)
    db.commit()

    data = writer.write_concepts_workbook(db, [first.id, second.id])
    assert data.startswith(b"PK")


def test_deposit_removes_stale_same_chapter_secondary_topic(db):
    chapter = models.Chapter(
        chapter_code="08CBSS_StaleTag",
        board="CBSE",
        grade="08",
        subject="Social Science",
        unit="History",
        chapter_title="Stale Tag",
        chapter_display_name="Stale Tag",
    )
    old = models.Topic(
        chapter=chapter,
        topic_title="Old Topic",
        topic_display_name="Old Topic",
        pre_post_learning="Pre",
    )
    current = models.Topic(
        chapter=chapter,
        topic_title="Current Topic",
        topic_display_name="Current Topic",
        pre_post_learning="Pre",
    )
    stale_secondary = models.Topic(
        chapter=chapter,
        topic_title="Stale Secondary",
        topic_display_name="Stale Secondary",
        pre_post_learning="Pre",
    )
    concept = _concept(old, "Shared Foundation", order=1)
    db.add(chapter)
    db.flush()
    db.add(models.ConceptTag(concept=concept, topic=stale_secondary))
    db.commit()

    created, merged = build_concepts._deposit_concepts(
        db,
        chapter,
        [{
            "topic": current.topic_title,
            "concept_title": concept.concept_title,
            "parent_concept": "Foundations",
            "concept_details": (
                "Description: The current prerequisite foundation supports "
                "the chapter's later reasoning."
            ),
            "keywords": "",
        }],
        "Pre",
        "Current Source",
    )
    db.flush()
    db.expire_all()
    refreshed = db.get(models.Concept, concept.id)

    assert not created
    assert merged == [concept.id]
    assert refreshed.topic.topic_title == "Current Topic"
    assert all(
        tag.topic.topic_title != "Stale Secondary"
        for tag in refreshed.tags
    )


def test_generic_activity_marker_is_not_repeated_in_visible_hub_note():
    item = {
        "qid": "QINV-HUB-1",
        "source_kind": "activity",
        "source_label": "Activity",
        "raw_task": (
            "Look at Fig. 14(a). Do you notice how the symbols change?"
        ),
    }

    note = g._compact_activity_hub_note(item)

    assert "Activity — Activity —" not in note
    task = "Look at Fig. 14(a). Do you notice how the symbols change?"
    assert task in note
    assert note.count(task) == 1


def test_culmination_title_is_topic_based_while_recap_remains_complete():
    topic = "A Compact Topic"
    records = [
        {
            "topic": topic,
            "concept_title": (
                "A Very Long First Concept Name That Previously Overflowed "
                "the Culmination Title"),
            "concept_details": "Description: First.",
        },
        {
            "topic": topic,
            "concept_title": (
                "A Very Long Second Concept Name That Previously Made the "
                "Title Even Longer"),
            "concept_details": "Description: Second.",
        },
        {
            "topic": topic,
            "concept_title": "A Third Concept Previously Omitted from the Title",
            "concept_details": "Description: Third.",
        },
        {
            "topic": topic,
            "concept_title": "A Fourth Concept Also Omitted from the Title",
            "concept_details": "Description: Fourth.",
        },
    ]

    result = g._enforce_culminations(records)
    culmination = result[-1]

    assert culmination["concept_title"] == f"Culmination - {topic}"
    assert len(culmination["concept_title"]) < 120
    for record in records:
        assert record["concept_title"] in culmination["concept_details"]

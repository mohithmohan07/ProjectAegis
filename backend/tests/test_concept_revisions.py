"""Reviewer instructions, the edits made from them, and where they are stored.

The properties under test are product requirements, not implementation detail:
rounds are unbounded, the delivered workbook is never annotated with review
history, and an instruction survives a failed attempt to apply it.
"""
from __future__ import annotations

import pytest

from app import models
from app.db import SessionLocal
from app.services import concept_revisions


@pytest.fixture()
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture()
def job(db):
    chapter = models.Chapter(
        chapter_code="10CBMA_REV01",
        chapter_title="Arithmetic Progressions",
    )
    db.add(chapter)
    db.flush()

    topics = {}
    for order, title in enumerate(
        ["Arithmetic Progressions", "nth Term", "Sum of First n Terms"]
    ):
        topic = models.Topic(
            chapter_id=chapter.id, topic_title=title, source_order=order
        )
        db.add(topic)
        db.flush()
        topics[title] = topic

    concept = models.Concept(
        topic_id=topics["Arithmetic Progressions"].id,
        concept_title="How d governs the behaviour of Sn",
        concept_details="Description: the common difference drives the sum.",
        keywords="d, sum",
        source_order=0,
    )
    db.add(concept)

    upload = models.UploadJob(
        module="build_concepts",
        learning_kind="post",
        filename="ap.mmd",
        status="generated",
        deposit_scope_type="chapter",
        deposit_scope_ids=[chapter.id],
    )
    db.add(upload)
    db.commit()
    db.refresh(upload)
    db.refresh(concept)
    return upload, concept, topics


def _provider(changes, summary="Moved the concept to the later topic."):
    def call(**_kwargs):
        return {"change_summary": summary, "changes": changes}

    return call


def test_rounds_are_unbounded(db, job):
    """A reviewer may keep correcting an output for as long as it takes."""

    upload, _concept, _topics = job

    for expected in range(1, 26):
        revision = concept_revisions.record_instruction(
            db, upload, f"Round {expected}: move it later."
        )
        assert revision.round_number == expected

    history = concept_revisions.list_revisions(db, upload.id)
    assert [row.round_number for row in history] == list(range(1, 26))
    # Oldest first, so the instructions replay in the order they were given.
    assert history[0].instruction.startswith("Round 1:")
    assert history[-1].instruction.startswith("Round 25:")


def test_instruction_is_committed_before_any_edit_is_attempted(db, job):
    upload, _concept, _topics = job

    revision = concept_revisions.record_instruction(
        db, upload, "Put the Sn concept under Sum of First n Terms."
    )

    assert revision.id is not None
    assert revision.status == "pending"
    assert revision.completed_at is None


def test_a_failed_round_keeps_the_reviewer_words(db, job):
    """The instruction is the research artifact; it outlives the attempt."""

    upload, _concept, _topics = job
    revision = concept_revisions.record_instruction(
        db, upload, "Move it to Sum of First n Terms."
    )

    def exploding(**_kwargs):
        raise RuntimeError("provider unavailable")

    applied = concept_revisions.apply_instruction(
        db, upload, revision, provider=exploding
    )

    assert applied.status == "failed"
    assert "provider unavailable" in applied.error
    assert applied.instruction == "Move it to Sum of First n Terms."
    assert concept_revisions.list_revisions(db, upload.id)[-1].id == revision.id


def test_placement_correction_repoints_the_concept(db, job):
    upload, concept, topics = job
    revision = concept_revisions.record_instruction(
        db, upload, "This belongs under Sum of First n Terms."
    )

    applied = concept_revisions.apply_instruction(
        db,
        upload,
        revision,
        provider=_provider([{
            "concept_id": concept.id,
            "field": "topic",
            "after": "Sum of First n Terms",
            "reason": "Reviewer: this belongs under Sum of First n Terms.",
        }]),
    )

    db.refresh(concept)
    assert applied.status == "applied"
    assert concept.topic_id == topics["Sum of First n Terms"].id
    assert applied.changes == [{
        "concept_id": concept.id,
        "field": "topic",
        "before": "Arithmetic Progressions",
        "after": "Sum of First n Terms",
        "reason": "Reviewer: this belongs under Sum of First n Terms.",
    }]


def test_a_round_that_changes_nothing_is_recorded_as_no_change(db, job):
    """The workbook is rebuilt from concepts, so this leaves it identical."""

    upload, _concept, _topics = job
    revision = concept_revisions.record_instruction(db, upload, "Looks fine.")

    applied = concept_revisions.apply_instruction(
        db, upload, revision,
        provider=_provider([], summary="Nothing needed changing."),
    )

    assert applied.status == "no_change"
    assert applied.changes == []
    assert applied.change_summary == "Nothing needed changing."


def test_an_unknown_topic_is_declined_rather_than_created(db, job):
    """A reviewer redirects among taught topics; inventing one is generation's job."""

    upload, concept, _topics = job
    original_topic_id = concept.topic_id
    revision = concept_revisions.record_instruction(
        db, upload, "Move it to a brand new topic."
    )

    applied = concept_revisions.apply_instruction(
        db, upload, revision,
        provider=_provider([{
            "concept_id": concept.id,
            "field": "topic",
            "after": "A Topic This Chapter Never Teaches",
            "reason": "Reviewer asked for a new topic.",
        }]),
    )

    db.refresh(concept)
    assert concept.topic_id == original_topic_id
    assert applied.changes == []
    assert applied.status == "no_change"


def test_edits_outside_the_allowed_fields_are_ignored(db, job):
    upload, concept, _topics = job
    revision = concept_revisions.record_instruction(db, upload, "Change things.")

    applied = concept_revisions.apply_instruction(
        db, upload, revision,
        provider=_provider([
            {
                "concept_id": concept.id,
                "field": "source_order",
                "after": "99",
                "reason": "not an editable field",
            },
            {
                "concept_id": 10_000_000,
                "field": "concept_title",
                "after": "belongs to another job",
                "reason": "not an offered concept",
            },
        ]),
    )

    assert applied.changes == []


def test_review_history_is_not_written_into_the_delivered_workbook(
    db, job, tmp_path
):
    """Review history lives in the database; the artifact stays untouched.

    The reviewer's words and Aegis's reasons must never reach the deliverable.
    Only the corrected concept content does, so two rounds can be diffed
    without the audit trail moving underneath them.
    """

    from app.bulk_import import writer

    upload, concept, _topics = job
    revision = concept_revisions.record_instruction(
        db, upload, "Rename this to something shorter."
    )
    concept_revisions.apply_instruction(
        db, upload, revision,
        provider=_provider([{
            "concept_id": concept.id,
            "field": "concept_title",
            "after": "How d governs Sn",
            "reason": "Reviewer asked for a shorter title.",
        }]),
    )

    destination = tmp_path / "delivered.xlsx"
    writer.write_workbook(db, destination)

    # An xlsx is a zip, so the cells must be read rather than byte-scanned;
    # a raw search would pass whether or not the history leaked.
    from openpyxl import load_workbook

    book = load_workbook(destination, read_only=True, data_only=True)
    cells = {
        str(value)
        for sheet in book.worksheets
        for row in sheet.iter_rows(values_only=True)
        for value in row
        if value is not None
    }
    book.close()
    blob = " \n ".join(cells)

    for marker in (
        "Rename this to something shorter",
        "Reviewer asked for a shorter title",
        "round_number",
        "concept_revisions",
    ):
        assert marker not in blob, (
            f"review history leaked into the workbook: {marker!r}"
        )
    # The corrected content itself is delivered, proving the scan can see cells.
    assert "How d governs Sn" in blob


def test_empty_and_oversized_instructions_are_refused(db, job):
    upload, _concept, _topics = job

    with pytest.raises(concept_revisions.RevisionError):
        concept_revisions.record_instruction(db, upload, "   ")
    with pytest.raises(concept_revisions.RevisionError):
        concept_revisions.record_instruction(
            db, upload, "x" * (concept_revisions.MAX_INSTRUCTION_CHARS + 1)
        )


def test_revision_summary_exposes_the_round_for_review(db, job):
    upload, concept, _topics = job
    revision = concept_revisions.record_instruction(db, upload, "Move it later.")
    applied = concept_revisions.apply_instruction(
        db, upload, revision,
        provider=_provider([{
            "concept_id": concept.id,
            "field": "topic",
            "after": "Sum of First n Terms",
            "reason": "Reviewer: move it later.",
        }]),
    )

    summary = concept_revisions.revision_summary(applied)
    assert summary["round_number"] == 1
    assert summary["instruction"] == "Move it later."
    assert summary["status"] == "applied"
    assert summary["change_count"] == 1
    assert summary["created_at"]

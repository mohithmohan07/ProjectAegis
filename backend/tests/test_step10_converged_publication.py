"""S10 — the converged publication: identity in, judgment out (Rounds 6-7).

The publication act stops judging content on its way to the database.
Titles are no longer rewritten (the cleaner ran [measured] as a rewriter at
this boundary), resolution is carried identity first and a TOPIC-SCOPED
exactly-one title match second — never the chapter-wide match whose
re-parent [measured] destroyed a same-titled concept (T11.3), and never the
slot-position guess the Round-7 audits [measured] overwriting a removed
row's neighbour — the umbrella-topic vocabulary flags instead of deleting,
no row is dropped in silence on the DB-write path, and both lanes gain what
only the Master lane had: a tamper gate that compares recorded seals within
one recorded draft lineage and judges no content.

Every DB-writing test here builds its OWN chapter: the seeded fixture
chapter is shared suite-wide, and [measured] depositing odd content into it
turned five lifecycle tests red under targeted orderings.
"""
from __future__ import annotations

import copy
import io
from pathlib import Path

import openpyxl
import pytest

from app import models
from app.services import assessment_grouping as ag
from app.services import assessment_release_service as svc
from app.services import assessment_release_snapshot as snapshot_mod
from app.services import build_concepts_release as release
from app.services import build_concepts_release_files as release_files
from app.services import build_concepts_release_publication as publication
from app.services import concept_cleanup

from tests.test_assessment_release_run import OWNER
from tests.test_step9_rule_e_deraising import _INVENTORY, _SOUND_ROW
from tests.test_pre_release_lane_wiring import _both_lanes_job


def _chapter(db, code, *, title=None, grade="06"):
    chapter = models.Chapter(
        chapter_code=code, board="CBSE", grade=grade, subject="Science",
        unit="Science Unit", chapter_title=title or f"S10 {code}",
        chapter_display_name=title or f"S10 {code}",
        chapter_description="", chapter_duration="",
    )
    db.add(chapter)
    db.commit()
    db.refresh(chapter)
    return chapter


def _synthetic_route_inputs(records, inventory):
    """Add one mechanically complete route to publication-only fixtures.

    These tests exercise publication mechanics rather than concept generation,
    but their source inventories contain real synthetic QIDs. The production
    gate therefore correctly requires the fixture Concept File to expose a
    visible Type/Case/Example route. One deterministic carrier owns every
    fixture QID; no Type is imposed on the remaining concepts.
    """

    rows = [copy.deepcopy(dict(row)) for row in records]
    items = [
        copy.deepcopy(dict(item))
        for item in (inventory or {}).get("items") or []
        if isinstance(item, dict) and str(item.get("qid") or "").strip()
    ]
    carriers = [
        row for row in rows
        if str(row.get("topic") or "").strip()
        and str(
            row.get("concept_title") or row.get("concept") or ""
        ).strip()
    ]
    if not items or not carriers:
        return rows, {"types": []}

    # The first publishable row is the route carrier, matching the release's
    # source order. Tests that deliberately damage a later row therefore keep
    # exercising that row's own publication defect rather than accidentally
    # deleting the fixture's only valid route at the same time.
    carrier = carriers[0]
    type_id = "TYPE-SYNTHETIC-PUBLICATION"
    case_id = "CASE-SYNTHETIC-PUBLICATION"
    examples = []
    rendered_examples = []
    placements = {}
    for index, item in enumerate(items, start=1):
        qid = str(item["qid"]).strip()
        prompt = str(
            item.get("raw_task")
            or item.get("normalized_task")
            or f"Synthetic publication question {index}."
        ).strip()
        examples.append({
            "source_question_id": qid,
            "prompt": prompt,
        })
        rendered_examples.append(f"Example {index:02d}: {prompt}")
        placements[qid] = {
            "qid": qid,
            "type_id": type_id,
            "case_id": case_id,
            "host_disposition": "type_case_example",
        }

    base_details = str(carrier.get("concept_details") or "").rstrip()
    carrier["concept_details"] = (
        base_details
        + " // Types: Type 01: Synthetic publication route. "
        + "Case 01: Exercise the fixture's publication route. "
        + " ".join(rendered_examples)
    ).strip()
    carrier["_type_case_qid_host_placement_manifest"] = {
        "placements": placements,
    }
    mined_types = {
        "types": [{
            "type_id": type_id,
            "type_title": "Synthetic publication route",
            "type_definition": (
                "A deterministic route used only by publication fixtures."
            ),
            "owner_topic_ids": [],
            "case_prompts": [{
                "case_id": case_id,
                "case_definition": (
                    "Exercise the fixture's publication route."
                ),
                "owner_topic_ids": [],
                "source_question_ids": [
                    str(item["qid"]).strip() for item in items
                ],
                "examples": examples,
            }],
        }],
    }
    return rows, mined_types


def _stage_synthetic_release(
    db,
    job,
    *,
    target_chapter_id,
    records,
    inventory,
    **kwargs,
):
    routed, mined_types = _synthetic_route_inputs(records, inventory)
    return release.stage_release(
        db,
        job,
        target_chapter_id=target_chapter_id,
        records=routed,
        inventory=copy.deepcopy(dict(inventory or {})),
        mined_types=mined_types,
        **kwargs,
    )


def _job_with_records(db, chapter, records):
    """Publication fixture carrying real route evidence for its QIDs."""

    job = models.UploadJob(
        owner_sub=OWNER,
        module="build_concepts",
        upload_type="textbook",
        filename="ch.mmd",
        mmd_text="# Chapter\n\nExercise 1. Which of these is a solid?",
        status="generated",
        deposit_scope_type="chapter",
        deposit_scope_ids=[chapter.id],
        question_inventory=copy.deepcopy(_INVENTORY),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    _stage_synthetic_release(
        db,
        job,
        target_chapter_id=chapter.id,
        records=records,
        inventory=_INVENTORY,
        reason="S10 publication fixture",
    )
    db.refresh(job)
    return job


def _edit_staged_payload_in_place(db, job, lane, mutate):
    """Tamper: rewrite one lane's staged slot with no re-stage recorded."""
    resolved = release.normalize_lane(lane)
    key = release.release_key_for_lane(resolved)
    inventory = copy.deepcopy(dict(job.question_inventory or {}))
    slot = copy.deepcopy(dict(inventory[key]))
    mutate(slot)
    inventory[key] = slot
    job.question_inventory = inventory
    db.commit()
    db.refresh(job)


def _freeze_row(db, job, lane, *, uid_suffix=""):
    """A frozen Master row recording the lane's CURRENT draft and seal."""
    payload = release.release_payload(job, lane=lane)
    row = models.AssessmentRelease(
        release_uid=f"s10-freeze-{lane}-{job.id}{uid_suffix}",
        version=1,
        owner_sub=OWNER,
        job_id=job.id,
        lane=lane,
        provider_identity={
            "lane": lane,
            "job_id": job.id,
            "staged_release_version": release.staged_version(payload),
            "staged_release_uid": str(
                payload.get(release.STAGED_RELEASE_UID_FIELD) or ""),
        },
        concept_snapshot={
            "source_concept_release_sha256": (
                snapshot_mod.source_release_sha256(payload)
            ),
        },
    )
    db.add(row)
    db.commit()
    return row


def _workbook_text(data: bytes) -> str:
    book = openpyxl.load_workbook(io.BytesIO(data), read_only=True)
    text = "\n".join(
        str(cell)
        for sheet in book.worksheets
        for row_values in sheet.iter_rows(values_only=True)
        for cell in row_values
        if cell is not None
    )
    book.close()
    return text


def _master_payload(
    chapter, concept_key, machine_id, concept_name, topic_title, *,
    details, keywords, label, carry_machine_id=True,
):
    from tests.test_mes_release_lifecycle import _payload

    payload = _payload(concept_key, machine_id, concept_name, label=label)
    row = {
        "concept_key": concept_key,
        "concept_title": concept_name,
        "concept_display_name": concept_name,
        "parent_concept": "",
        "concept_details": details,
        "keywords": keywords,
        "related_concepts": "",
        "digicards": "",
        "concept_source": "fixture",
    }
    if carry_machine_id:
        row["concept_machine_id"] = machine_id
    payload["concept_snapshot"] = {
        "source_concept_release_sha256": "c" * 64,
        "target_chapter_id": chapter.id,
        "concept_provenance": [{
            "concept_key": concept_key,
            "release_row_identity": {"position": 1},
        }],
        "chapter": {"chapter_title": chapter.chapter_title},
        "topics": [{
            "topic_title": topic_title,
            "topic_display_name": topic_title,
            "pre_post_learning": "Post",
            "topic_concept_labels": "",
            "related_topics": "",
            "topic_description": "",
            "concepts": [row],
        }],
    }
    return payload


def _published_concept(
    db, chapter, topic_title, concept_name, *,
    details, keywords, machine_id="",
):
    topic = models.Topic(
        chapter_id=chapter.id,
        topic_title=topic_title,
        topic_display_name=topic_title,
        pre_post_learning="Post",
        topic_description="",
    )
    concept = models.Concept(
        topic=topic,
        concept_title=concept_name,
        concept_display_name=concept_name,
        parent_concept="",
        concept_details=details,
        keywords=keywords,
        sources="fixture",
        machine_id=machine_id,
    )
    db.add(concept)
    db.commit()
    db.refresh(concept)
    return concept


# --------------------------------------------------------------------------- #
# 1. The reviewer's words reach the database byte for byte
# --------------------------------------------------------------------------- #

def test_publication_never_rewrites_a_reviewer_edited_title(db):
    """[measured at d7d2e2f] the staged title ``pH and its meaning`` was
    persisted as ``pH and Its Meaning`` and the details lost ``See Fig.
    2.1`` plus the head of its own sentence — ``clean_concept_record``
    running at the publication boundary. Staging already cleaned what
    generation produced; an edit that survives review is a decision. Where
    the cleaner WOULD have rewritten, the divergence is recorded on the
    receipt (T7.2) rather than dying in silence.
    """
    chapter = _chapter(db, "06CBSC_S10Title")
    title = "pH and its meaning"
    details = "See Fig. 2.1 for the pH scale."
    job = _job_with_records(db, chapter, [{
        "topic": "Acids and Bases",
        "concept_title": title,
        "concept_details": details,
        "keywords": "pH, scale",
    }])
    staged_details = release.release_payload(job)["records"][0][
        "concept_details"
    ]
    assert staged_details.startswith(details)

    result = publication.upload_release_to_database(
        db, job.id, owner_sub=OWNER, lane="post")
    assert len(result["created_concept_ids"]) == 1

    row = db.get(models.Concept, result["created_concept_ids"][0])
    assert row.concept_title == title
    assert row.concept_display_name == title
    assert row.concept_details == staged_details

    payload = release.release_payload(job)
    flags = payload["summary"].get("identity_review_flags") or []
    assert any("house normalization" in flag for flag in flags), flags


# --------------------------------------------------------------------------- #
# 2. Same title, two topics, two rows — T11.3
# --------------------------------------------------------------------------- #

def test_two_same_titled_concepts_under_two_topics_publish_as_two_rows(db):
    """[measured at d7d2e2f] the cross-publication form is where the
    destruction was real: publishing ``Shared Name`` under a second topic
    re-parented the first concept, deleted its tags and overwrote its
    details. Resolution is topic-scoped now; the first concept is not
    touched.
    """
    chapter = _chapter(db, "06CBSC_S10Shared")
    job = _job_with_records(db, chapter, [{
        "topic": "Topic Alpha",
        "concept_title": "Shared Name",
        "concept_details": "Alpha details.",
        "keywords": "alpha",
    }])
    first_details = release.release_payload(job)["records"][0][
        "concept_details"
    ]
    first = publication.upload_release_to_database(
        db, job.id, owner_sub=OWNER, lane="post")
    [first_id] = first["created_concept_ids"]

    _stage_synthetic_release(
        db,
        job,
        target_chapter_id=chapter.id,
        records=[{
            "topic": "Topic Gamma",
            "concept_title": "Shared Name",
            "concept_details": "Gamma details.",
            "keywords": "gamma",
        }],
        inventory=copy.deepcopy(dict(job.question_inventory or {})),
        reason="S10 second publication",
    )
    db.refresh(job)
    second_details = release.release_payload(job)["records"][0][
        "concept_details"
    ]
    second = publication.upload_release_to_database(
        db, job.id, owner_sub=OWNER, lane="post")

    assert second["updated_concept_ids"] == []
    assert len(second["created_concept_ids"]) == 1
    [second_id] = second["created_concept_ids"]
    assert second_id != first_id

    first_row = db.get(models.Concept, first_id)
    second_row = db.get(models.Concept, second_id)
    assert first_row.topic.topic_title == "Topic Alpha"
    assert first_row.concept_details == first_details
    assert second_row.topic.topic_title == "Topic Gamma"
    assert second_row.concept_details == second_details
    assert first_row.machine_id and second_row.machine_id
    assert first_row.machine_id != second_row.machine_id


# --------------------------------------------------------------------------- #
# 3. A recased topic title no longer refuses the Master upload
# --------------------------------------------------------------------------- #

def test_a_topic_title_recased_between_publications_still_resolves(db):
    """[measured] the MC6 refusal class: the resolver matched
    ``Topic.topic_title`` byte-exactly, so a Master snapshot staged with a
    recased topic title was refused against rows nobody had edited. The
    persisted ``machine_id`` is the identity now; the topic read confirms
    it through the one LENIENT normaliser, so a recase still resolves.
    """
    chapter = _chapter(db, "06CBSC_S10Recase")
    machine_id = "06CBSC_S10Recase_PL_T01_C01"
    concept = _published_concept(
        db, chapter, "Growth and Reproduction", "Vegetative Propagation",
        details="Plants grow new plants from their own parts.",
        keywords="growth", machine_id=machine_id,
    )
    payload = _master_payload(
        chapter, "release:s10recase:0001", machine_id,
        "Vegetative Propagation", "growth and reproduction",
        details="Plants grow new plants from their own parts.",
        keywords="growth", label=f"{machine_id} Q01",
    )
    created = svc.create_release(
        db, chapter_id=chapter.id, payload=payload, owner_sub=OWNER)
    published = svc.publish_release(db, created)
    result = svc.upload_master_to_database(db, published, owner_sub=OWNER)
    assert result["questions_created"] == 1
    question = db.query(models.Question).filter_by(
        question_label=f"{machine_id} Q01").one()
    assert question.group.concept_id == concept.id


def test_master_resolution_survives_a_published_content_edit(db):
    """Round 6's recorded drop of the content-drift half, pinned: editing a
    published concept's details after the concept release uploaded no
    longer refuses the Master upload — drift protection is the seal's job,
    not a byte-compare against mutable rows.
    """
    chapter = _chapter(db, "06CBSC_S10Drift")
    machine_id = "06CBSC_S10Drift_PL_T01_C01"
    concept = _published_concept(
        db, chapter, "Matter", "States of Matter",
        details="Edited AFTER the concept release uploaded.",
        keywords="edited, later", machine_id=machine_id,
    )
    payload = _master_payload(
        chapter, "release:s10drift:0001", machine_id,
        "States of Matter", "Matter",
        details="The details the snapshot froze.",
        keywords="frozen", label=f"{machine_id} Q01",
    )
    created = svc.create_release(
        db, chapter_id=chapter.id, payload=payload, owner_sub=OWNER)
    published = svc.publish_release(db, created)
    result = svc.upload_master_to_database(db, published, owner_sub=OWNER)
    assert result["questions_created"] == 1
    question = db.query(models.Question).filter_by(
        question_label=f"{machine_id} Q01").one()
    assert question.group.concept_id == concept.id


def test_a_blank_id_published_row_refuses_an_id_carrying_snapshot(db):
    """Round 6's recorded migration path, pinned: a row published before
    the identity column refuses an id-carrying snapshot with "upload that
    concept release first" — republishing the concept release mints the
    ids, and nothing is ever resolved by similar text.
    """
    chapter = _chapter(db, "06CBSC_S10Legacy")
    machine_id = "06CBSC_S10Legacy_PL_T01_C01"
    _published_concept(
        db, chapter, "Old Topic", "Old Concept",
        details="Published before machine ids existed.",
        keywords="old", machine_id="",
    )
    payload = _master_payload(
        chapter, "release:s10legacy:0001", machine_id,
        "Old Concept", "Old Topic",
        details="Published before machine ids existed.",
        keywords="old", label=f"{machine_id} Q01",
    )
    created = svc.create_release(
        db, chapter_id=chapter.id, payload=payload, owner_sub=OWNER)
    published = svc.publish_release(db, created)
    with pytest.raises(
        svc.UploadRefused, match="upload that concept release first"
    ):
        svc.upload_master_to_database(db, published, owner_sub=OWNER)


# --------------------------------------------------------------------------- #
# 4. No row is dropped in silence on the DB-write path
# --------------------------------------------------------------------------- #

def test_a_staged_row_with_no_topic_is_a_named_defect_not_a_silent_drop(db):
    """The measured two-row payload, through the one gap that was left.

    ``structural_defects`` recomputes row defects fail-closed only when the
    payload carries no ``staged_row_defects`` key; a RECORDED empty list
    wins. [measured at d7d2e2f] a payload staged sound and then edited in
    place — row 2's topic blanked, the recorded list still empty — sailed
    past the gate and published one of two rows, silently. The publication's
    own filter now refuses, naming the row through the same
    ``staged_row_unusable`` vocabulary.
    """
    chapter = _chapter(db, "06CBSC_S10RowGate")
    job = _job_with_records(db, chapter, [
        dict(_SOUND_ROW),
        {
            "topic": "Second Section",
            "concept_title": "Second Concept",
            "concept_details": "A second, sound row.",
            "keywords": "second",
        },
    ])
    concepts_before = db.query(models.Concept).count()
    payload = release.release_payload(job)
    assert payload[release.STAGED_ROW_DEFECTS_FIELD] == []

    def blank_second_topic(slot):
        slot["records"][1]["topic"] = ""

    _edit_staged_payload_in_place(db, job, "post", blank_second_topic)
    tampered = release.release_payload(job)
    assert tampered["records"][1]["topic"] == ""
    assert tampered[release.STAGED_ROW_DEFECTS_FIELD] == []
    assert release.structural_defects(tampered) == []

    with pytest.raises(ValueError, match="row 2 has no topic"):
        publication.upload_release_to_database(
            db, job.id, owner_sub=OWNER, lane="post")

    # And nothing was half-written: the refusal precedes every DB row.
    assert db.query(models.Concept).count() == concepts_before


def test_an_entirely_unusable_tampered_payload_is_refused_not_published_as_empty(
    db,
):
    """[measured] with EVERY row unusable, ``nothing_to_publish`` read the
    tampered payload as empty and the zero-row branch published a latched
    success whose receipt said "nothing was removed". The row gate now runs
    before the zero-row branch, so the same tamper refuses loudly.
    """
    chapter = _chapter(db, "06CBSC_S10AllGone")
    job = _job_with_records(db, chapter, [dict(_SOUND_ROW)])

    def blank_the_only_topic(slot):
        slot["records"][0]["topic"] = ""

    _edit_staged_payload_in_place(db, job, "post", blank_the_only_topic)

    with pytest.raises(ValueError, match="row 1 has no topic"):
        publication.upload_release_to_database(
            db, job.id, owner_sub=OWNER, lane="post")
    payload = release.release_payload(job)
    assert not (payload.get("summary") or {}).get("database_uploaded")


# --------------------------------------------------------------------------- #
# 5. The umbrella-topic vocabulary flags; it no longer deletes
# --------------------------------------------------------------------------- #

def test_a_concept_under_a_topic_named_Summary_is_flagged_not_deleted():
    """The measured 5-in-2-out case (spec T7.3): both ``Summary`` rows and
    the ``General`` row were deleted with only a progress-log line as the
    record, while the Marathi ``सारांश`` survived an English-only name set —
    the vocabulary deciding what the rows MEAN. It now raises the coded
    ``review_topic_name`` flag on the row and decides nothing.
    """
    records = [
        {"topic": "Summary", "concept_title": "Key Takeaways",
         "concept_details": "Description: a", "keywords": ""},
        {"topic": "Summary", "concept_title": "Chapter Recap",
         "concept_details": "Description: b", "keywords": ""},
        {"topic": "General", "concept_title": "Odds and Ends",
         "concept_details": "Description: c", "keywords": ""},
        {"topic": "सारांश", "concept_title": "मुख्य मुद्दे",
         "concept_details": "Description: d", "keywords": ""},
        {"topic": "Photosynthesis", "concept_title": "Chlorophyll",
         "concept_details": "Description: e", "keywords": ""},
    ]
    out = concept_cleanup.filter_review_violations(
        copy.deepcopy(records), subject="Science", board="CBSE")

    assert [r["concept_title"] for r in out] == [
        r["concept_title"] for r in records
    ]
    assert [r["topic"] for r in out] == [r["topic"] for r in records]
    for row in out[:3]:
        assert len(row["review_flags"]) == 1
        assert "review_topic_name" in row["review_flags"][0]
        assert "R4" in row["review_flags"][0]
        assert repr(row["topic"]) in row["review_flags"][0]
    # The English-only reach of the name set is a recorded fact, not a fix:
    # the Marathi umbrella row is exactly as unflagged as the real one.
    assert "review_flags" not in out[3]
    assert "review_flags" not in out[4]

    # The fixpoint the assemble/deposit replay depends on.
    again = concept_cleanup.filter_review_violations(
        copy.deepcopy(out), subject="Science", board="CBSE")
    assert again == out


# --------------------------------------------------------------------------- #
# 6. The empty decided Pre release still exports and still publishes (pin)
# --------------------------------------------------------------------------- #

def test_an_empty_pre_release_still_exports_and_still_publishes_zero_rows(
    db, client,
):
    """PIN, not a regression: S10 rewrote the publication loop and OD7
    (Round 5) rules that the empty path changes NOTHING — D8.2's zero-row
    idempotent success and D8.4's always-a-workbook survive the rewrite.
    """
    chapter = _chapter(db, "06CBSC_S10Empty")
    job = _both_lanes_job(db, chapter, pre_rows=False)

    for name in (
        "release-bulk-import.xlsx", "release.xlsx",
        "diagnostics.zip", "release.json",
    ):
        response = client.get(
            f"/build-concepts/uploads/{job.id}/{name}?lane=pre")
        assert response.status_code == 200, name
        assert response.content, name

    result = publication.upload_release_to_database(
        db, job.id, owner_sub=OWNER, lane="pre")
    assert result["database_uploaded"] is True
    assert result["created_concept_ids"] == []
    assert result["updated_concept_ids"] == []
    again = publication.upload_release_to_database(
        db, job.id, owner_sub=OWNER, lane="pre")
    assert again["database_uploaded"] is True
    assert again["created_concept_ids"] == []


# --------------------------------------------------------------------------- #
# 7/8. What the reviewer approved is what the published file reads back
# --------------------------------------------------------------------------- #

def test_pre_related_concepts_stays_empty_through_publication(db, client):
    """T3.3b end to end under owner decision D4 (2026-08-29): the Pre
    lane's ``related_concepts`` ships EMPTY, and stays empty identically
    before and after the upload — including through the published-concept
    shortcut. The Post machine_id must not leak into the Pre workbook at
    any stage."""
    chapter = _chapter(db, "06CBSC_S10PreRel")
    job = _both_lanes_job(db, chapter)

    pre = release.release_payload(job, lane=release.LANE_PRE)
    assert pre["records"][0][release.PRE_ROW_RELATED_CONCEPTS_FIELD] == ""

    post = release.release_payload(job, lane=release.LANE_POST)
    _c, post_concepts, _r, _d = release_files.transient_release_hierarchy(
        db, job, payload=post)
    post_machine_id = post_concepts[0].machine_id
    assert post_machine_id

    before = _workbook_text(release_files.build_release_bulk_import_workbook(
        db, job, lane=release.LANE_PRE))
    assert post_machine_id not in before

    result = publication.upload_release_to_database(
        db, job.id, owner_sub=OWNER, lane="pre")
    assert result["created_concept_ids"]
    row = db.get(models.Concept, result["created_concept_ids"][0])
    assert row.related_concepts == ""

    # Through the published-concept shortcut this time.
    db.refresh(job)
    payload = release.release_payload(job, lane=release.LANE_PRE)
    assert payload["summary"]["database_uploaded"] is True
    after = _workbook_text(release_files.build_release_bulk_import_workbook(
        db, job, lane=release.LANE_PRE))
    assert post_machine_id not in after


def test_digicards_survive_publication(db):
    """The same omission class, the same fix, the other column."""
    chapter = _chapter(db, "06CBSC_S10Cards")
    digicards = "Card: what pH measures\nCard: the pH of pure water"
    job = _job_with_records(db, chapter, [{
        "topic": "Digital Card Notes",
        "concept_title": "The pH Scale",
        "concept_details": "The pH scale measures acidity.",
        "keywords": "pH",
        "digicards": digicards,
    }])
    result = publication.upload_release_to_database(
        db, job.id, owner_sub=OWNER, lane="post")
    row = db.get(models.Concept, result["created_concept_ids"][0])
    assert row.digicards == digicards

    db.refresh(job)
    after = _workbook_text(release_files.build_release_bulk_import_workbook(
        db, job, lane=release.LANE_POST))
    assert "Card: what pH measures" in after


# --------------------------------------------------------------------------- #
# 9. Republication resolves rows, never destroys or duplicates them
# --------------------------------------------------------------------------- #

def test_a_shrinking_republication_never_overwrites_the_removed_rows_neighbour(
    db,
):
    """[measured, Round 7 audit] the slot-position lookup this replaces
    overwrote Concept B's row with Concept C's content when a re-stage
    removed B — a silent learner-visible loss with a stale duplicate left
    beside it. Topic-scoped title resolution updates A and C in place and
    leaves B's row untouched.
    """
    chapter = _chapter(db, "06CBSC_S10Shrink")
    rows = [
        {"topic": "One Topic", "concept_title": f"Concept {letter}",
         "concept_details": f"{letter} details.", "keywords": letter.lower()}
        for letter in ("A", "B", "C")
    ]
    job = _job_with_records(db, chapter, rows)
    first = publication.upload_release_to_database(
        db, job.id, owner_sub=OWNER, lane="post")
    assert len(first["created_concept_ids"]) == 3
    by_title = {
        db.get(models.Concept, cid).concept_title: cid
        for cid in first["created_concept_ids"]
    }
    b_machine_id = db.get(models.Concept, by_title["Concept B"]).machine_id
    c_machine_id = db.get(models.Concept, by_title["Concept C"]).machine_id

    _stage_synthetic_release(
        db, job, target_chapter_id=chapter.id,
        records=[copy.deepcopy(rows[0]), copy.deepcopy(rows[2])],
        inventory=copy.deepcopy(dict(job.question_inventory or {})),
        reason="B removed",
    )
    db.refresh(job)
    second = publication.upload_release_to_database(
        db, job.id, owner_sub=OWNER, lane="post")

    assert second["created_concept_ids"] == []
    assert sorted(second["updated_concept_ids"]) == sorted(
        [by_title["Concept A"], by_title["Concept C"]])
    b_row = db.get(models.Concept, by_title["Concept B"])
    c_row = db.get(models.Concept, by_title["Concept C"])
    assert b_row.concept_title == "Concept B"
    assert b_row.concept_details == "B details."
    assert b_row.machine_id == b_machine_id
    assert c_row.concept_details == "C details."
    assert c_row.machine_id == c_machine_id


def test_a_reordered_republication_keeps_each_id_with_its_row(db):
    """[measured, Round 7 audit] under slot resolution a swapped re-stage
    traded the two rows' content across their ids, so every question
    attached per id rode along under the other concept's text. Identity
    follows the ROW now; only ``source_order`` moves.
    """
    chapter = _chapter(db, "06CBSC_S10Reorder")
    rows = [
        {"topic": "One Topic", "concept_title": "Concept X",
         "concept_details": "X details.", "keywords": "x"},
        {"topic": "One Topic", "concept_title": "Concept Y",
         "concept_details": "Y details.", "keywords": "y"},
    ]
    job = _job_with_records(db, chapter, rows)
    first = publication.upload_release_to_database(
        db, job.id, owner_sub=OWNER, lane="post")
    by_title = {
        db.get(models.Concept, cid).concept_title: db.get(
            models.Concept, cid)
        for cid in first["created_concept_ids"]
    }
    x_id = by_title["Concept X"].machine_id
    y_id = by_title["Concept Y"].machine_id

    _stage_synthetic_release(
        db, job, target_chapter_id=chapter.id,
        records=[copy.deepcopy(rows[1]), copy.deepcopy(rows[0])],
        inventory=copy.deepcopy(dict(job.question_inventory or {})),
        reason="reordered",
    )
    db.refresh(job)
    staged_by_title = {
        row["concept_title"]: row["concept_details"]
        for row in release.release_payload(job)["records"]
    }
    second = publication.upload_release_to_database(
        db, job.id, owner_sub=OWNER, lane="post")
    assert second["created_concept_ids"] == []
    db.expire_all()
    x_row = by_title["Concept X"]
    y_row = by_title["Concept Y"]
    assert (x_row.concept_details, x_row.machine_id) == (
        staged_by_title["Concept X"], x_id,
    )
    assert (y_row.concept_details, y_row.machine_id) == (
        staged_by_title["Concept Y"], y_id,
    )
    assert (y_row.source_order, x_row.source_order) == (1, 2)


def test_a_legacy_blank_id_row_is_adopted_not_duplicated(db):
    """[measured, Round 7 audit] a blank-``machine_id`` legacy row — the
    deposit lane's normal product — made the slot design create a fresh
    duplicate on EVERY republication, with the reviewer-seen id never
    persisted. Title resolution adopts the legacy row: it is updated in
    place, minted the id the export stamped, and republication converges.
    """
    chapter = _chapter(db, "06CBSC_S10Adopt")
    legacy = _published_concept(
        db, chapter, "Adoptive Topic", "Adopted Concept",
        details="The deposit-lane original.", keywords="legacy",
        machine_id="",
    )
    rows = [{
        "topic": "Adoptive Topic", "concept_title": "Adopted Concept",
        "concept_details": "The staged replacement.", "keywords": "staged",
    }]
    job = _job_with_records(db, chapter, rows)
    staged_details = release.release_payload(job)["records"][0][
        "concept_details"
    ]

    _c, staged, _r, _d = release_files.transient_release_hierarchy(
        db, job, payload=release.release_payload(job))
    [staged_id] = [c.machine_id for c in staged]
    assert staged_id

    first = publication.upload_release_to_database(
        db, job.id, owner_sub=OWNER, lane="post")
    assert first["created_concept_ids"] == []
    assert first["updated_concept_ids"] == [legacy.id]
    db.expire_all()
    adopted = db.get(models.Concept, legacy.id)
    assert adopted.machine_id == staged_id
    assert adopted.concept_details == staged_details

    _stage_synthetic_release(
        db, job, target_chapter_id=chapter.id,
        records=copy.deepcopy(rows),
        inventory=copy.deepcopy(dict(job.question_inventory or {})),
        reason="republished",
    )
    db.refresh(job)
    second = publication.upload_release_to_database(
        db, job.id, owner_sub=OWNER, lane="post")
    assert second["created_concept_ids"] == []
    assert second["updated_concept_ids"] == [legacy.id]
    assert db.query(models.Concept).join(models.Topic).filter(
        models.Topic.chapter_id == chapter.id).count() == 1


def test_a_prepended_topic_still_homes_master_questions(db):
    """[measured, Round 7 audit] end to end: the transient export used to
    stamp purely positional ids, so a re-stage that prepended a topic gave
    Concept X the id the already-published topic held, and the Master
    attached X's question to the unrelated Concept A, silently. The export
    now consults persisted identity, so the snapshot id, the reviewer-seen
    id and the persisted id are one string and the question homes on X.
    """
    chapter = _chapter(db, "06CBSC_S10Prepend")
    job = _job_with_records(db, chapter, [{
        "topic": "Matter", "concept_title": "Concept A",
        "concept_details": "A.", "keywords": "a",
    }])
    publication.upload_release_to_database(
        db, job.id, owner_sub=OWNER, lane="post")

    _stage_synthetic_release(
        db, job, target_chapter_id=chapter.id,
        records=[
            {"topic": "Brand New Topic", "concept_title": "Concept X",
             "concept_details": "X.", "keywords": "x"},
            {"topic": "Matter", "concept_title": "Concept A",
             "concept_details": "A.", "keywords": "a"},
        ],
        inventory=copy.deepcopy(dict(job.question_inventory or {})),
        reason="v2 prepends a topic",
    )
    db.refresh(job)
    staged_payload = release.release_payload(job)
    bridge = snapshot_mod.build(db, job, staged_payload)
    x_row = next(
        c
        for t in bridge["snapshot"]["topics"]
        for c in t.get("concepts") or []
        if c["concept_title"] == "Concept X"
    )
    x_mid = x_row["concept_machine_id"]

    publication.upload_release_to_database(
        db, job.id, owner_sub=OWNER, lane="post")
    db.expire_all()
    persisted = {
        c.machine_id: c
        for t in db.get(models.Chapter, chapter.id).topics
        for c in t.concepts
    }
    assert persisted[x_mid].concept_title == "Concept X"

    concept_key = x_row["concept_key"]
    label = f"{x_mid} Q01"
    groups = []
    for shell in ag.required_shells(concept_key, x_mid, "Concept X"):
        shell = dict(shell)
        shell["concept_key"] = concept_key
        groups.append(shell)
    payload = {
        "groups": groups,
        "candidates": [{
            "candidate_id": f"CAND-{label}",
            "question_label": label,
            "source_atom_ids": ["QINV-0001"],
            "blueprint_cell_id": "CELL-rel1",
            "sheet_kind": "objective",
            "question_category": "Multiple Choice Question",
            "cognitive_skill": "Remember",
            "difficulty": "Less",
            "marks": 1.0,
            "question_duration": 2.0,
            "math_keyboard": "",
            "question_appears_in": "Pre/Post-Worksheet/Test",
            "answer_restriction": "Specific",
            "restriction_reason": "The authored answer space is bounded.",
            "question": "A question authored for Concept X.",
            "question_text": "A question authored for Concept X.",
            "answers": [
                {"answer_type": "Phrases", "answer_content": "Cube",
                 "correct_answer": "1", "answer_weightage": "1"},
                {"answer_type": "Phrases", "answer_content": "Circle",
                 "correct_answer": "0", "answer_weightage": "0"},
            ],
            "sub_questions": [],
            "answer_explanation": "Authored against Concept X.",
            "concept_key": concept_key,
            "group_key": f"({x_mid}) BG01",
            "flags": [],
        }],
        "concept_snapshot": {
            "source_concept_release_sha256":
                snapshot_mod.source_release_sha256(staged_payload),
            "target_chapter_id": chapter.id,
            "concept_provenance": bridge.get("concept_provenance") or [],
            "chapter": {"chapter_title": chapter.chapter_title},
            "topics": bridge["snapshot"]["topics"],
        },
    }
    created = svc.create_release(
        db, chapter_id=chapter.id, payload=payload, owner_sub=OWNER)
    published = svc.publish_release(db, created)
    result = svc.upload_master_to_database(db, published, owner_sub=OWNER)
    assert result["questions_created"] == 1
    question = db.query(models.Question).filter_by(
        question_label=label).one()
    assert question.group.concept_id == persisted[x_mid].id


# --------------------------------------------------------------------------- #
# 10. Every one of the four outputs refuses a tampered artifact
# --------------------------------------------------------------------------- #

def test_publication_refuses_a_tampered_artifact_for_every_one_of_the_four_outputs(
    db,
):
    """Outputs 01 and 03: the concept lane had NO verification of any kind —
    [measured at d7d2e2f] a semantically edited but well-shaped payload
    published without complaint. The seal gate refuses it now, per lane,
    keyed on the draft's recorded LINEAGE: stripping the lineage field is
    itself refused, and a legitimate re-stage (new lineage) publishes.
    Outputs 02 and 04: the Master lane's disk re-hash already refused a
    tampered ``master.xlsx`` (pinned elsewhere); the ``concepts.xlsx`` half
    was untested and is pinned here.
    """
    from tests.test_mes_release_lifecycle import _fresh_release

    # -- Outputs 01/03: the staged payloads, sealed by their frozen rows --
    chapter = _chapter(db, "06CBSC_S10Tamper")
    job = _both_lanes_job(db, chapter)
    for lane in (release.LANE_PRE, release.LANE_POST):
        _freeze_row(db, job, lane)

    def edit_first_record(slot):
        slot["records"][0]["concept_details"] = (
            "Edited in place after the freeze. "
            + str(slot["records"][0].get("concept_details") or "")
        )

    _edit_staged_payload_in_place(db, job, release.LANE_POST, edit_first_record)
    with pytest.raises(ValueError, match="edited in place after the freeze"):
        publication.upload_release_to_database(
            db, job.id, owner_sub=OWNER, lane="post")

    _edit_staged_payload_in_place(db, job, release.LANE_PRE, edit_first_record)
    with pytest.raises(ValueError, match="edited in place after the freeze"):
        publication.upload_release_to_database(
            db, job.id, owner_sub=OWNER, lane="pre")

    # Stripping the lineage field IS the tamper it hides (Round 7): the
    # frozen row records a lineage this payload no longer carries.
    def strip_lineage(slot):
        slot.pop(release.STAGED_RELEASE_UID_FIELD, None)

    _edit_staged_payload_in_place(db, job, release.LANE_POST, strip_lineage)
    with pytest.raises(ValueError, match="carries no lineage"):
        publication.upload_release_to_database(
            db, job.id, owner_sub=OWNER, lane="post")

    # A legitimate re-stage mints a NEW lineage and publishes: the gate
    # accuses nobody who recorded their change.
    _stage_synthetic_release(
        db, job, target_chapter_id=chapter.id,
        records=[dict(_SOUND_ROW)],
        inventory=copy.deepcopy(dict(job.question_inventory or {})),
        reason="legitimate re-stage after the freeze",
    )
    db.refresh(job)
    restaged = publication.upload_release_to_database(
        db, job.id, owner_sub=OWNER, lane="post")
    assert restaged["database_uploaded"] is True

    # An untouched payload is never a false positive: same seal machinery,
    # separate job, both lanes publish.
    clean_job = _both_lanes_job(db, _chapter(db, "06CBSC_S10Clean"))
    for lane in (release.LANE_PRE, release.LANE_POST):
        _freeze_row(db, clean_job, lane)
    for lane in (release.LANE_PRE, release.LANE_POST):
        result = publication.upload_release_to_database(
            db, clean_job.id, owner_sub=OWNER, lane=lane)
        assert result["database_uploaded"] is True

    # -- Outputs 02/04: the Master lane's two served artifacts --
    for filename in (svc.CONCEPTS_FILENAME, svc.MASTER_FILENAME):
        master_release, _, _ = _fresh_release(db)
        published = svc.publish_release(db, master_release)
        artifact = Path(published.publication["directory"]) / filename
        artifact.write_bytes(artifact.read_bytes() + b"tampered")
        with pytest.raises(svc.UploadRefused, match="no longer matches"):
            svc.upload_master_to_database(db, published, owner_sub=OWNER)

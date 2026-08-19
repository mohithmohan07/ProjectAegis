"""Outputs 03/04 on the release lane — the sibling key, and what it pins.

docs/aegis-restructure.md §5: Output 03 is the Pre-Learning Concept
Review, Output 04 the Pre-Learning Master File. §7: publication stays a
separate, explicit, authenticated act (Rule G). Spec T3 decided the
shape: ONE slot per lane on one job, a sibling key rather than a
lane-keyed sub-map, with a lane parameter threaded through payload
construction and the four download routes.

The tests here are deliberately structural. Three of them exist because
the same defect is reachable three different ways:

* the leak barrier, bound to the SLOT rather than to ``learning_kind``
  (the Pre release rides a POST job, so a field-keyed barrier answers
  "post" on a payload that is unambiguously Pre);
* the download manifest, which has TWO implementations, one
  monkeypatching the other;
* the concept snapshot, which likewise has TWO writers.

Each of those pairs is pinned on both members, so editing one alone
fails loudly instead of silently doing nothing.
"""
from __future__ import annotations

import copy
import io
import json
import zipfile

import pytest

from app import models
from app.services import assessment_release_run as run
from app.services import assessment_release_service as release_service
from app.services import build_concepts_release as release
from app.services import build_concepts_release_files as release_files
from app.services import build_concepts_release_manifest as release_manifest
from app.services import build_concepts_release_publication as publication
from app.services import coverage_ledger

from tests.test_assessment_release_run import (
    OWNER,
    _authorities,
    _chapter_with_concepts,
    _decision_context,
)
from tests.test_assessment_pre_release_lane import (
    SOURCE_INVENTORY,
    _cells,
    _generated_authorities,
    _questions,
)


# --------------------------------------------------------------------------- #
# Fixtures: one POST job carrying BOTH staged releases, exactly as a real run
# leaves it (Q3 — one Build Concepts run produces all four outputs).
# --------------------------------------------------------------------------- #

def _post_records():
    return [
        {
            "topic": "Solids",
            "concept_title": "What makes a solid",
            "concept_details": (
                "Description: A solid keeps its own shape.\n"
                "Achieving Mastery: name three solids and say why."
            ),
            "keywords": "solid, shape",
        },
    ]


def _pre_map(*, rows=True, refused="", topic="Counting",
             concept="Counting to ten", verdict="assumes_nothing",
             verdict_review_flags=()):
    if not rows:
        # After spec-step8 S9 an empty capture is never bare: ``premap.build``
        # spends one verdict on it and stamps the result here. ``verdict=None``
        # models the pre-S9 shape — an empty map nobody decided — which is
        # exactly what the release lane must keep refusing.
        empty = {
            "rows": [],
            "topics": [],
            "needed_for": {},
            "analysis": {
                "inventory": [], "allotments": {}, "rationales": {},
                "review_flags": {},
            },
            "review_flags": {},
            "decision_flags": {},
            "validation": [],
            "refused": refused,
        }
        if verdict:
            empty[release.PRE_LANE_VERDICT_FIELD] = {
                "verdict": verdict,
                "rationale": "the chapter opens from first principles",
                "review_flags": list(verdict_review_flags),
            }
            if verdict_review_flags:
                empty["decision_flags"]["empty_capture"] = list(
                    verdict_review_flags
                )
        return empty
    return {
        "rows": [
            {
                "topic": topic,
                "parent_concept": "",
                "concept_title": concept,
                "concept_details": (
                    "Description: Counting to ten names each number in "
                    "order.\nAchieving Mastery: count aloud to ten."
                ),
                "keywords": "count, number",
                "_semantic_topic_id": "PRT-0001",
                "_source_block_ids": [],
                "_source_grounding_contract": (
                    "derived-from-prerequisite-capture"
                ),
                "_pre_concept_id": "PRC-0001",
                "_aegis_pre_prerequisites": [
                    {"prerequisite_id": "PRQ-0001", "text": "counts to ten"},
                ],
                "_aegis_needed_for": [
                    {
                        "post_concept_id": "CONCEPT-0001",
                        "post_concept_title": "What makes a solid",
                    },
                ],
            },
        ],
        "topics": [
            {
                "pre_topic_id": "PRT-0001",
                "title": topic,
                "pre_concept_ids": ["PRC-0001"],
            },
        ],
        "needed_for": {"PRC-0001": ["CONCEPT-0001"]},
        "analysis": {
            "inventory": [], "allotments": {}, "rationales": {},
            "review_flags": {},
        },
        "review_flags": {},
        "decision_flags": {},
        "validation": [],
        "refused": refused,
    }


def _pre_questions(count=2):
    authored = _questions(count, concept="PRC-0001")
    return {
        "plans": {
            "PRC-0001": {
                "total": count,
                "split": [{"tier": "recall", "count": count}],
                "rationale": "the capture supports this many",
            },
        },
        "questions": {"PRC-0001": authored},
        "blocked": {},
        "review_flags": {},
        "decision_flags": {},
    }


def _both_lanes_job(
    db, chapter, *, pre_rows=True, questions=2, refused="",
    topic="Counting", concept="Counting to ten",
):
    """A POST job whose sibling slot carries the Pre release.

    ``learning_kind`` is "post" — the value every live creation site
    hardwires (spec T2) — because that is the state slice E has to work
    in, and the state in which a field-keyed lane test fails.
    """

    job = models.UploadJob(
        owner_sub=OWNER,
        module="build_concepts",
        upload_type="textbook",
        filename="ch.mmd",
        mmd_text="# Chapter\n\nExercise 1. Which of these is a solid?",
        status="generated",
        learning_kind="post",
        deposit_scope_type="chapter",
        deposit_scope_ids=[chapter.id],
        question_inventory=copy.deepcopy(SOURCE_INVENTORY),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    release.stage_release(
        db,
        job,
        target_chapter_id=chapter.id,
        records=_post_records(),
        inventory=copy.deepcopy(SOURCE_INVENTORY),
        reason="recorded Output-01 fixture",
    )
    release.stage_pre_release(
        db,
        job,
        target_chapter_id=chapter.id,
        pre_map=_pre_map(
            rows=pre_rows, refused=refused, topic=topic, concept=concept,
        ),
        pre_questions=_pre_questions(questions) if pre_rows else {},
        inventory=copy.deepcopy(SOURCE_INVENTORY),
        reason="recorded Output-03 fixture",
    )
    db.refresh(job)
    return job


# --------------------------------------------------------------------------- #
# 1. The barrier is bound to the SIBLING KEY, not to learning_kind
# --------------------------------------------------------------------------- #

def test_the_barrier_is_bound_to_the_sibling_key_not_to_learning_kind(db):
    """The carry-forward from slice D2, closed.

    The staged Pre payload here says ``learning_kind: "post"`` and lives
    on a job whose column says "post" — every field-keyed test for "is
    this Pre" answers no. It is still the Pre payload, because it came out
    of the Pre SLOT, and routing it into the source lane would materialise
    the chapter's own questions into Output 04.

    THIS TEST FAILS THE MOMENT THE BARRIER IS REBOUND TO
    ``learning_kind`` in either place it appears.
    """

    chapter = _chapter_with_concepts(db)
    job = _both_lanes_job(db, chapter)

    # Scrub every field a lane could be read off, leaving only the SLOT.
    inventory = copy.deepcopy(job.question_inventory)
    inventory[release.PRE_RELEASE_KEY]["learning_kind"] = "post"
    inventory[release.PRE_RELEASE_KEY].pop(release.RELEASE_LANE_FIELD)
    job.question_inventory = inventory
    db.commit()
    db.refresh(job)

    staged = release.release_payload(job, lane="pre")
    assert staged["learning_kind"] == "post"
    assert job.learning_kind == "post"
    assert release.payload_lane(staged) == release.LANE_POST, (
        "the fixture must leave NO field saying 'pre' — the key is the "
        "only thing left that can hold the barrier"
    )
    # ...and the lane resolved from the KEY says otherwise, which is the
    # binding that holds.
    _, resolved = release.staged_release_for_lane(job, "pre")
    assert resolved == release.LANE_PRE
    # The leak's raw material must still be REACHABLE, or this test would
    # pass by having nothing to leak. It no longer rides the Pre payload
    # (the steer forbids a chapter QID anywhere in a Pre release payload);
    # it lives on the job column and in the POST sibling, which is exactly
    # where the source lane would find it.
    assert not staged["question_task_inventory"], (
        "the Pre payload carries no chapter question identity at all"
    )
    assert job.question_inventory["items"], (
        "the fixture must keep the leak's raw material on the job"
    )
    assert release.release_payload(job)["question_task_inventory"]["items"], (
        "the fixture must keep the leak's raw material in the Post sibling"
    )
    assert run._generated_lane_source_qids(job, []) == [
        "QINV-0001", "QINV-0002",
    ], "and the barrier must still be able to see it"

    authorities, _ = _authorities(db, chapter)
    before = db.query(models.AssessmentRelease).count()
    with pytest.raises(run.SourceQuestionLeak) as raised:
        run.run_release_for_job(
            db, job.id, owner_sub=OWNER, lane="pre",
            authorities=authorities, **_decision_context())
    assert "generated" in str(raised.value).lower()
    assert db.query(models.AssessmentRelease).count() == before


def test_the_post_slot_on_the_same_job_still_takes_the_source_lane(db):
    """The sibling must not poison the lane it sits beside.

    One job now holds both releases. Output 02 still reads the POST slot
    and still runs the SOURCE lane over the chapter's own questions —
    which is what Output 02 is.
    """

    chapter = _chapter_with_concepts(db)
    job = _both_lanes_job(db, chapter)
    authorities, _ = _authorities(db, chapter)

    released = run.run_release_for_job(
        db, job.id, owner_sub=OWNER, authorities=authorities,
        **_decision_context())

    assert released.payload["source_atoms"], (
        "Output 02 is built from the chapter's source questions"
    )
    assert "pre_post_learning" not in released.payload
    lanes = {
        topic["pre_post_learning"]
        for topic in released.concept_snapshot["topics"]
    }
    assert lanes == {"Post"}


def test_naming_the_pre_slot_without_its_questions_is_refused_not_served(db):
    """A lane name never silently selects a mode.

    ``lane="pre"`` says which slot to read. It does NOT turn on the
    generated lane — deliberately, so the forgotten-argument path is a
    refusal rather than a quiet fall-through into the source lane.
    """

    chapter = _chapter_with_concepts(db)
    job = _both_lanes_job(db, chapter)
    authorities, _ = _authorities(db, chapter)

    with pytest.raises(run.SourceQuestionLeak):
        run.run_release_for_job(
            db, job.id, owner_sub=OWNER, lane="pre",
            authorities=authorities, **_decision_context())


def test_the_pre_lane_entry_point_carries_the_staged_questions(db):
    """``run_pre_release_for_job`` is the supported Output-04 call."""

    chapter = _chapter_with_concepts(db)
    job = _both_lanes_job(db, chapter, questions=2)
    authorities, _ = _generated_authorities()

    concept_key = next(iter(
        run.release_snapshot.build(
            db, job, release.release_payload(job, lane="pre")
        )["concept_records_by_key"]
    ))
    released = run.run_pre_release_for_job(
        db,
        job.id,
        owner_sub=OWNER,
        blueprint_cells=_cells(2, concept_key),
        authorities=authorities,
        **_decision_context(),
    )

    assert released.payload["pre_post_learning"] == "Pre"
    assert released.payload["source_atoms"] == []
    assert all(
        candidate["source_atom_ids"] == []
        for candidate in released.payload["candidates"]
    )
    lanes = {
        topic["pre_post_learning"]
        for topic in released.concept_snapshot["topics"]
    }
    assert lanes == {"Pre"}


def test_output_04_refuses_a_job_with_no_staged_pre_release(db):
    chapter = _chapter_with_concepts(db)
    job = models.UploadJob(
        owner_sub=OWNER,
        module="build_concepts",
        upload_type="textbook",
        filename="ch.mmd",
        mmd_text="# Chapter",
        status="generated",
        learning_kind="post",
        deposit_scope_type="chapter",
        deposit_scope_ids=[chapter.id],
        question_inventory=copy.deepcopy(SOURCE_INVENTORY),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    release.stage_release(
        db, job, target_chapter_id=chapter.id, records=_post_records(),
        inventory=copy.deepcopy(SOURCE_INVENTORY), reason="post only",
    )

    with pytest.raises(run.ReleaseRunError) as raised:
        run.run_pre_release_for_job(db, job.id, owner_sub=OWNER)
    assert "Output-03" in str(raised.value)


# --------------------------------------------------------------------------- #
# 2. BOTH manifest implementations
# --------------------------------------------------------------------------- #

_PRE_ENTRY_KINDS = {
    "released_pre_concepts",
    "pre_release_diagnostics",
    "pre_release_payload",
    "pre_database_upload",
}


def test_both_manifest_implementations_carry_the_pre_entries(db):
    """``build_concepts_release_manifest`` MONKEYPATCHES the files module.

    Adding an entry to one of them alone is a silent no-op. Both are
    asserted here so a one-sided edit fails.

    The eager half is reached through ``eager_release_artifact_entries``,
    NOT through ``release_files.release_artifact_entries``. That matters:
    ``install()`` rebinds the module attribute for the rest of the
    process — ``app.main.bootstrap()`` does it in production and
    ``tests/test_build_concepts_release.py`` does it mid-session, and it
    is never restored — so reaching the "eager" implementation by
    attribute lookup hands back the LAZY one, and this pin degenerates
    into comparing a function with itself. It passed in isolation and was
    tautological in a full run.
    """

    chapter = _chapter_with_concepts(db)
    job = _both_lanes_job(db, chapter)

    eager = release_files.eager_release_artifact_entries(job)
    lazy = release_manifest.release_artifact_entries(job)
    assert (
        release_files.eager_release_artifact_entries
        is not release_manifest.release_artifact_entries
    ), "the pin must hold two different functions to be worth anything"

    assert _PRE_ENTRY_KINDS <= {row["kind"] for row in eager}
    assert _PRE_ENTRY_KINDS <= {row["kind"] for row in lazy}
    # Same rows, same order, same URLs — only the sizes differ, which is
    # the lazy manifest's entire reason to exist.
    by_kind_eager = {row["kind"]: row for row in eager}
    by_kind_lazy = {row["kind"]: row for row in lazy}
    for kind in _PRE_ENTRY_KINDS:
        for field in ("download_url", "filename", "media_type", "action"):
            assert by_kind_eager[kind][field] == by_kind_lazy[kind][field], (
                f"the two manifest implementations disagree on {kind}.{field}"
            )
        assert "lane=pre" in by_kind_eager[kind]["download_url"]
    assert [row["kind"] for row in eager] == [row["kind"] for row in lazy]


def test_neither_manifest_offers_the_pre_entries_without_a_pre_release(db):
    chapter = _chapter_with_concepts(db)
    job = models.UploadJob(
        owner_sub=OWNER,
        module="build_concepts",
        upload_type="textbook",
        filename="ch.mmd",
        mmd_text="# Chapter",
        status="generated",
        learning_kind="post",
        deposit_scope_type="chapter",
        deposit_scope_ids=[chapter.id],
        question_inventory={"items": []},
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    release.stage_release(
        db, job, target_chapter_id=chapter.id, records=_post_records(),
        reason="post only",
    )

    for entries in (
        release_files.eager_release_artifact_entries(job),
        release_manifest.release_artifact_entries(job),
    ):
        assert not _PRE_ENTRY_KINDS & {row["kind"] for row in entries}


# --------------------------------------------------------------------------- #
# 3. BOTH snapshot writers
# --------------------------------------------------------------------------- #

def test_both_snapshot_writers_are_lane_correct_for_output_02(db):
    """Two writers project the concept hierarchy; both must say Pre.

    ``assessment_release_snapshot.build`` reads the staged payload;
    ``release_service.snapshot_from_chapter`` reads live Topic ORM rows,
    where BOTH lanes' topics sit in one chapter. A Pre Master projected
    through the second without a lane would carry the chapter's Post
    topics.
    """

    chapter = _chapter_with_concepts(db)
    job = _both_lanes_job(db, chapter)

    # Writer 1 — from the staged payload.
    bridge = run.release_snapshot.build(
        db, job, release.release_payload(job, lane="pre")
    )
    assert {
        topic["pre_post_learning"] for topic in bridge["snapshot"]["topics"]
    } == {"Pre"}
    assert {
        topic["pre_post_learning"]
        for topic in run.release_snapshot.build(
            db, job, release.release_payload(job)
        )["snapshot"]["topics"]
    } == {"Post"}

    # Writer 2 — from live ORM rows. Give the chapter one topic in each
    # lane so an unscoped projection would visibly mix them.
    pre_topic = models.Topic(
        chapter_id=chapter.id,
        topic_title="Counting",
        topic_display_name="Counting",
        pre_post_learning="Pre",
        source_order=99,
    )
    db.add(pre_topic)
    db.commit()

    both = release_service.snapshot_from_chapter(db, chapter.id, {})
    scoped_pre = release_service.snapshot_from_chapter(
        db, chapter.id, {}, pre_post="Pre",
    )
    scoped_post = release_service.snapshot_from_chapter(
        db, chapter.id, {}, pre_post="Post",
    )
    assert {
        topic["pre_post_learning"] for topic in both["topics"]
    } == {"Pre", "Post"}, "the unscoped default is unchanged"
    assert {
        topic["pre_post_learning"] for topic in scoped_pre["topics"]
    } == {"Pre"}
    assert {
        topic["pre_post_learning"] for topic in scoped_post["topics"]
    } == {"Post"}


# --------------------------------------------------------------------------- #
# 4. The new audit markers: registered AND stripped
# --------------------------------------------------------------------------- #

def test_the_new_pre_audit_markers_are_registered_and_stripped(db):
    """Every ``_aegis_*`` marker rides the release and never the database."""

    for field in (
        release.RELEASE_ROW_LANE_FIELD,
        release.PRE_ROW_GENERATED_QUESTIONS_FIELD,
    ):
        assert field.startswith("_aegis_")
        assert field in release._RELEASE_AUDIT_FIELDS

    chapter = _chapter_with_concepts(db)
    job = _both_lanes_job(db, chapter)
    row = release.release_payload(job, lane="pre")["records"][0]
    # They ride the release for the reviewer's audit...
    assert row[release.RELEASE_ROW_LANE_FIELD] == "pre"
    assert row[release.PRE_ROW_GENERATED_QUESTIONS_FIELD] == [
        "PRC-0001-PRQ-0001", "PRC-0001-PRQ-0002",
    ]
    # ...and are stripped before the deterministic database upsert.
    stripped = release._strip_release_fields(row)
    assert release.RELEASE_ROW_LANE_FIELD not in stripped
    assert release.PRE_ROW_GENERATED_QUESTIONS_FIELD not in stripped
    assert not [key for key in stripped if key.startswith("_aegis_")]


def test_the_published_pre_concept_carries_no_audit_marker(db):
    """The strip is checked where it matters: on the committed row."""

    chapter = _chapter_with_concepts(db)
    job = _both_lanes_job(
        db, chapter,
        topic="Counting markers", concept="Counting to ten markers",
    )

    result = publication.upload_release_to_database(
        db, job.id, owner_sub=OWNER, lane="pre",
    )
    assert result["database_uploaded"] is True
    concept = db.get(models.Concept, result["created_concept_ids"][0])
    assert concept.topic.pre_post_learning == "Pre"
    assert "_aegis" not in (concept.concept_details or "")
    assert "_aegis" not in (concept.keywords or "")


# --------------------------------------------------------------------------- #
# 5. Outputs 01/02 are byte-unchanged
# --------------------------------------------------------------------------- #

_POST_PAYLOAD_KEYS = {
    "version", "staged_version", "released_at", "release_reason", "job_id",
    "learning_kind", "source_book", "filename", "source_document_hash",
    "target_chapter_id", "directory_metadata", "target_identity",
    "checkpoint_stage", "checkpoint_progress", "records", "issues",
    # spec-step8 S9 adds exactly one key: the row-level defect record the
    # state split needs somewhere to live. A defect discovered at staging
    # with no place in the payload is a producer with no consumer — the
    # hole S8 closed for ``unplaced``, one lane over.
    "staged_row_defects",
    "type_case_rows", "question_task_inventory", "extraction_provenance",
    "mined_types", "pending_decision_snapshot", "final_grounding_certificate",
    "chapter_meta", "instruction_set", "summary",
}


def test_the_post_release_payload_keeps_its_recorded_shape(db):
    """Spec T3's reason for a sibling key rather than a sub-map.

    A lane-keyed sub-map inside ``RELEASE_KEY`` would have changed this
    shape and every recorded release fixture with it. Pinned as an exact
    key set, and pinned on a job that HAS a Pre sibling, so the Pre lane
    cannot leak a key into the Post payload.

    ``staged_version`` is the ONE key spec-step8 S6 adds, and it is added
    deliberately rather than tolerated: staging used to OVERWRITE this
    slot, which is why §7:551's "a new immutable release version per
    applied round" was unexpressible on it and why a ``force_release``
    after a Master had been frozen could move the payload with nothing on
    either side recording that it had. The slot is still one slot — the
    staging DRAFT — and this is the draft's version. It is deliberately
    NOT a lane-keyed sub-map, which is the shape this test exists to
    refuse.
    """

    chapter = _chapter_with_concepts(db)
    job = _both_lanes_job(db, chapter)
    payload = release.release_payload(job)

    assert set(payload) == _POST_PAYLOAD_KEYS
    assert payload["staged_version"] == 1
    assert payload["version"] == release.RELEASE_VERSION
    assert payload["learning_kind"] == "post"
    assert release.RELEASE_LANE_FIELD not in payload
    assert set(payload["summary"]) == {
        "row_count", "affected_row_count", "issue_count", "error_count",
        "warning_count", "database_uploaded",
    }
    assert not [
        key for key in payload["records"][0]
        if key in (
            release.RELEASE_ROW_LANE_FIELD,
            release.PRE_ROW_GENERATED_QUESTIONS_FIELD,
        )
    ]


def test_the_post_downloads_are_untouched_by_the_lane_parameter(db, client):
    chapter = _chapter_with_concepts(db)
    job = _both_lanes_job(db, chapter)

    default = client.get(f"/build-concepts/uploads/{job.id}/release.json")
    explicit = client.get(
        f"/build-concepts/uploads/{job.id}/release.json?lane=post"
    )
    assert default.status_code == 200
    assert default.content == explicit.content
    assert default.headers["content-disposition"] == (
        f'attachment; filename="concept_release_job_{job.id}.json"'
    )
    assert json.loads(default.content)["learning_kind"] == "post"


# --------------------------------------------------------------------------- #
# 6. An EMPTY Pre map ships cleanly
# --------------------------------------------------------------------------- #

def test_an_empty_but_decided_pre_release_is_ready(db, client):
    """REPLACES ``test_a_chapter_that_assumes_nothing_ships_a_clean_empty_
    pre_release`` (spec-step8 S9), which pinned the behaviour this slice
    inverts.

    [measured at 76c84fb] that release read *Diagnostic release* with the
    defect "the release contains no concept rows to upload", and
    ``release-bulk-import.xlsx`` 404'd while the other three downloads
    returned 200. Two separate wrongs, and they are two because the
    questions were two: EMPTINESS is not CORRUPTION (D8.1), and a defect
    blocks the database write, never a download (Rule E).

    A chapter that genuinely assumes nothing is legal. What makes it legal
    is not that its list is empty — that is the inference Rule 1 forbids —
    but that the run POSITIVELY DECIDED it, and the verdict rides the
    payload.
    """

    chapter = _chapter_with_concepts(db)
    job = _both_lanes_job(db, chapter, pre_rows=False)

    payload = release.release_payload(job, lane="pre")
    assert payload is not None, "an empty Pre map still stages a release"
    assert payload["records"] == []
    assert payload["generated_questions"] == []
    assert payload["summary"]["row_count"] == 0

    # Emptiness is answered by its OWN question now.
    assert release.nothing_to_publish(payload) is True
    assert release.structural_defects(payload) == []
    assert release.release_state(payload) == release.READY

    # ALL FOUR downloads, including the one that 404'd.
    for name in (
        "release-bulk-import.xlsx", "release.xlsx",
        "diagnostics.zip", "release.json",
    ):
        response = client.get(
            f"/build-concepts/uploads/{job.id}/{name}?lane=pre")
        assert response.status_code == 200, name
        assert response.content, name

    # And publishing nothing is the idempotent zero-row success (D8.2).
    result = publication.upload_release_to_database(
        db, job.id, owner_sub=OWNER, lane="pre")
    assert result["database_uploaded"] is True
    assert result["created_concept_ids"] == []
    assert result["updated_concept_ids"] == []
    # Idempotent: the second act reports the same thing and writes nothing.
    again = publication.upload_release_to_database(
        db, job.id, owner_sub=OWNER, lane="pre")
    assert again["database_uploaded"] is True
    assert again["created_concept_ids"] == []

    # The run itself is unaffected: the Post release is untouched and
    # still publishes.
    assert release.release_state(
        release.release_payload(job)
    ) in (release.READY, release.READY_WITH_FLAGS)
    post = publication.upload_release_to_database(
        db, job.id, owner_sub=OWNER, lane="post")
    assert post["database_uploaded"] is True


def test_an_empty_capture_without_a_positive_verdict_still_blocks(db, client):
    """D8.3 — the whole reason premap spends the verdict at all.

    An OCR-degraded scan and a chapter that genuinely assumes nothing both
    produce an empty capture. Only a recorded ``assumes_nothing`` opens the
    zero-row publication; ``capture_incomplete`` and — the case that
    matters most — NO verdict at all keep the release Diagnostic, because
    what is missing may be missing from the CAPTURE rather than from the
    chapter (R4).
    """

    chapter = _chapter_with_concepts(db)

    for verdict, expected_open in (
        (None, False),
        ("capture_incomplete", False),
        ("assumes_nothing", True),
    ):
        pre_map = _pre_map(rows=False, verdict=verdict)
        job = _both_lanes_job(db, chapter, pre_rows=False)
        release.stage_pre_release(
            db, job, target_chapter_id=chapter.id, pre_map=pre_map,
            pre_questions={}, reason="verdict fixture",
        )
        db.refresh(job)
        payload = release.release_payload(job, lane="pre")
        defects = release.structural_defects(payload)
        if expected_open:
            assert defects == [], verdict
            assert release.release_state(payload) == release.READY
        else:
            assert defects, verdict
            assert release.release_state(payload) == (
                release.DIAGNOSTIC_RELEASE)
            with pytest.raises(ValueError):
                publication.upload_release_to_database(
                    db, job.id, owner_sub=OWNER, lane="pre")

        # EVERY download stays 200 in all three cases — that is Rule E,
        # and it is the half the state must never take away.
        for name in (
            "release-bulk-import.xlsx", "release.xlsx",
            "diagnostics.zip", "release.json",
        ):
            response = client.get(
                f"/build-concepts/uploads/{job.id}/{name}?lane=pre")
            assert response.status_code == 200, (verdict, name)


def test_the_empty_capture_critics_dissent_reaches_the_release(db, client):
    """Q10 at the release boundary, not only at the decision.

    [measured before this test existed] a critic that dissented on the
    empty-capture verdict — the ONE decision that decides whether an empty
    Pre release is *Ready* or *Diagnostic* — reached the release nowhere at
    all. Its words were in ``pre_map["decision_flags"]``, and
    ``grep -n decision_flags backend/app/services/build_concepts_release.py``
    returns nothing: ``stage_pre_release`` copies the verdict row and
    nothing else, so a search of the whole staged payload for "missing
    pages" returned False.

    Q10 says the dissent is a recorded advisory flag. Recorded means where
    the reviewer of THIS artefact reads it, and advisory means it flags
    without blocking — so the assertions below are both halves: the words
    are in the release, AND the publication still opens.
    """

    chapter = _chapter_with_concepts(db)
    job = _both_lanes_job(db, chapter, pre_rows=False)
    release.stage_pre_release(
        db, job, target_chapter_id=chapter.id,
        pre_map=_pre_map(
            rows=False,
            verdict_review_flags=[
                "critic: rejected (confidence 0.42)",
                "critic: the source shows two missing pages",
            ],
        ),
        pre_questions={}, reason="critic dissent fixture",
    )
    db.refresh(job)
    payload = release.release_payload(job, lane="pre")

    # It rides the payload's verdict row, which is what every reader copies.
    assert payload[release.PRE_LANE_VERDICT_FIELD]["review_flags"] == [
        "critic: rejected (confidence 0.42)",
        "critic: the source shows two missing pages",
    ]
    # It is a NAMED release issue a reviewer reads without the payload.
    flagged = [
        issue for issue in payload["issues"]
        if issue["code"] == "pre_learning_empty_capture_review_flag"
    ]
    assert len(flagged) == 2
    assert any("missing pages" in issue["message"] for issue in flagged)

    # ADVISORY, never a gate: the verdict stands, nothing is corrupt, and
    # the zero-row publication is still open. The release only wears the
    # flag — which is the whole difference between advising and vetoing.
    assert release.structural_defects(payload) == []
    assert release.release_state(payload) == release.READY_WITH_FLAGS
    for name in (
        "release-bulk-import.xlsx", "release.xlsx",
        "diagnostics.zip", "release.json",
    ):
        response = client.get(
            f"/build-concepts/uploads/{job.id}/{name}?lane=pre")
        assert response.status_code == 200, name
    assert "missing pages" in response.text

    result = publication.upload_release_to_database(
        db, job.id, owner_sub=OWNER, lane="pre")
    assert result["database_uploaded"] is True


def test_no_pre_lane_at_all_stages_no_sibling(db):
    """"This run built no Pre map" stays distinguishable from "empty"."""

    chapter = _chapter_with_concepts(db)
    job = models.UploadJob(
        owner_sub=OWNER,
        module="build_concepts",
        upload_type="textbook",
        filename="ch.mmd",
        mmd_text="# Chapter",
        status="generated",
        learning_kind="post",
        deposit_scope_type="chapter",
        deposit_scope_ids=[chapter.id],
        question_inventory={"items": []},
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    release.stage_release(
        db, job, target_chapter_id=chapter.id, records=_post_records(),
        reason="post only",
    )

    assert release.stage_pre_release(db, job, pre_map=None) is None
    assert release.pre_release_available(job) is False
    assert release.staged_generated_questions(job) is None
    assert release.release_payload(job, lane="pre") is None


# --------------------------------------------------------------------------- #
# 7. The download routes serve the right lane, and only it
# --------------------------------------------------------------------------- #

def test_the_four_download_routes_serve_the_pre_outputs_under_the_lane(
    db, client,
):
    chapter = _chapter_with_concepts(db)
    job = _both_lanes_job(db, chapter)

    payload = client.get(
        f"/build-concepts/uploads/{job.id}/release.json?lane=pre"
    )
    assert payload.status_code == 200
    body = json.loads(payload.content)
    assert body["learning_kind"] == "pre"
    assert body[release.RELEASE_LANE_FIELD] == "pre"
    assert [row["concept_title"] for row in body["records"]] == [
        "Counting to ten"
    ]
    assert "pre_" in payload.headers["content-disposition"]

    for route in ("release.xlsx", "release-bulk-import.xlsx"):
        response = client.get(
            f"/build-concepts/uploads/{job.id}/{route}?lane=pre"
        )
        assert response.status_code == 200, route
        assert response.content[:2] == b"PK", route
        assert "pre_" in response.headers["content-disposition"], route

    diagnostics = client.get(
        f"/build-concepts/uploads/{job.id}/diagnostics.zip?lane=pre"
    )
    assert diagnostics.status_code == 200
    with zipfile.ZipFile(io.BytesIO(diagnostics.content)) as archive:
        staged = json.loads(archive.read("release/release_payload.json"))
        assert staged["learning_kind"] == "pre"
        report = archive.read("RUN_REPORT.txt").decode("utf-8")
    assert "Pre-Learning outputs" in report


def test_the_post_lane_never_serves_the_pre_outputs(db, client):
    """Asking for Post gets Post. The lane is not a hint."""

    chapter = _chapter_with_concepts(db)
    job = _both_lanes_job(db, chapter)

    body = json.loads(client.get(
        f"/build-concepts/uploads/{job.id}/release.json?lane=post"
    ).content)
    titles = [row["concept_title"] for row in body["records"]]
    assert titles == ["What makes a solid"]
    assert "Counting to ten" not in titles
    assert body["learning_kind"] == "post"


def test_an_unknown_lane_is_refused_rather_than_defaulted(db, client):
    chapter = _chapter_with_concepts(db)
    job = _both_lanes_job(db, chapter)

    response = client.get(
        f"/build-concepts/uploads/{job.id}/release.json?lane=sideways"
    )
    assert response.status_code == 400
    assert "sideways" in response.json()["detail"]


def test_the_pre_downloads_404_when_the_run_built_no_pre_lane(db, client):
    chapter = _chapter_with_concepts(db)
    job = models.UploadJob(
        owner_sub=OWNER,
        module="build_concepts",
        upload_type="textbook",
        filename="ch.mmd",
        mmd_text="# Chapter",
        status="generated",
        learning_kind="post",
        deposit_scope_type="chapter",
        deposit_scope_ids=[chapter.id],
        question_inventory={"items": []},
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    release.stage_release(
        db, job, target_chapter_id=chapter.id, records=_post_records(),
        reason="post only",
    )

    assert client.get(
        f"/build-concepts/uploads/{job.id}/release.json?lane=pre"
    ).status_code == 404
    assert client.get(
        f"/build-concepts/uploads/{job.id}/release.json"
    ).status_code == 200


# --------------------------------------------------------------------------- #
# 8. Publication stays a separate, explicit, authenticated act (Rule G)
# --------------------------------------------------------------------------- #

def test_publishing_one_lane_never_publishes_the_other(db):
    chapter = _chapter_with_concepts(db)
    job = _both_lanes_job(
        db, chapter,
        topic="Counting two lanes", concept="Counting to ten two lanes",
    )

    publication.upload_release_to_database(db, job.id, owner_sub=OWNER)
    db.refresh(job)
    assert release.release_payload(job)["summary"]["database_uploaded"]
    assert not release.release_payload(
        job, lane="pre"
    )["summary"].get("database_uploaded")

    publication.upload_release_to_database(
        db, job.id, owner_sub=OWNER, lane="pre",
    )
    db.refresh(job)
    pre = release.release_payload(job, lane="pre")
    assert pre["summary"]["database_uploaded"] is True
    # The Pre publication records its OWN ids and never redefines the
    # Post lane's ``job.result_ids``.
    assert pre["summary"]["concept_ids"]
    assert not set(pre["summary"]["concept_ids"]) & set(job.result_ids or [])

    # Publication title-cases the labels it writes, so compare on the
    # normalised form rather than the authored one.
    lanes = {
        str(topic.topic_title).casefold(): topic.pre_post_learning
        for topic in db.query(models.Topic).filter(
            models.Topic.chapter_id == chapter.id,
        )
        if str(topic.topic_title).casefold()
        in {"counting two lanes", "solids"}
    }
    assert lanes == {"counting two lanes": "Pre", "solids": "Post"}


def test_publication_is_idempotent_per_lane(db):
    chapter = _chapter_with_concepts(db)
    job = _both_lanes_job(
        db, chapter,
        topic="Counting idempotence",
        concept="Counting to ten idempotence",
    )

    first = publication.upload_release_to_database(
        db, job.id, owner_sub=OWNER, lane="pre")
    assert first["created_concept_ids"]
    before = db.query(models.Concept).count()

    second = publication.upload_release_to_database(
        db, job.id, owner_sub=OWNER, lane="pre")
    assert second["database_uploaded"] is True
    # The second act creates nothing and reports the ids this LANE
    # recorded — never the Post lane's ``job.result_ids``.
    assert db.query(models.Concept).count() == before
    assert second["created_concept_ids"] == release.release_payload(
        job, lane="pre",
    )["summary"]["concept_ids"]


def test_generation_never_publishes_the_pre_lane(db):
    """Rule G: staging is not publication, for either lane."""

    chapter = _chapter_with_concepts(db)
    job = _both_lanes_job(
        db, chapter,
        topic="Counting unpublished",
        concept="Counting to ten unpublished",
    )
    payload = release.release_payload(job, lane="pre")

    assert payload["summary"]["database_uploaded"] is False
    assert not db.query(models.Topic).filter(
        models.Topic.chapter_id == chapter.id,
        models.Topic.topic_title == "Counting unpublished",
    ).all()


# --------------------------------------------------------------------------- #
# 9. The three release states, for the Pre outputs
# --------------------------------------------------------------------------- #

def test_semantic_doubt_flags_and_never_blocks_the_pre_release(db):
    """Ready with flags: downloads AND explicit publication both open."""

    chapter = _chapter_with_concepts(db)
    flagged = _pre_map(
        topic="Counting flagged", concept="Counting to ten flagged",
    )
    flagged["rows"][0]["review_flags"] = [
        "critic dissented: this prerequisite may not be needed",
    ]
    job = models.UploadJob(
        owner_sub=OWNER, module="build_concepts", upload_type="textbook",
        filename="ch.mmd", mmd_text="# Chapter", status="generated",
        learning_kind="post", deposit_scope_type="chapter",
        deposit_scope_ids=[chapter.id], question_inventory={"items": []},
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    release.stage_release(
        db, job, target_chapter_id=chapter.id, records=_post_records(),
        reason="post",
    )
    release.stage_pre_release(
        db, job, target_chapter_id=chapter.id, pre_map=flagged,
        pre_questions=_pre_questions(1), reason="flagged",
    )
    db.refresh(job)

    payload = release.release_payload(job, lane="pre")
    assert release.release_state(payload) == release.READY_WITH_FLAGS
    assert release.structural_defects(payload) == []
    assert any(
        issue["code"] == "pre_learning_review_flag"
        for issue in payload["issues"]
    )
    assert all(
        issue["severity"] == "warning" for issue in payload["issues"]
    )
    # Flags never block (Rule E).
    assert publication.upload_release_to_database(
        db, job.id, owner_sub=OWNER, lane="pre",
    )["database_uploaded"] is True


def test_structural_corruption_blocks_the_pre_database_upload(db):
    """Diagnostic release: evidence ships, the database write does not."""

    chapter = _chapter_with_concepts(db)
    job = _both_lanes_job(
        db, chapter, pre_rows=False,
        refused="a Pre row carried source question QINV-0001",
    )

    payload = release.release_payload(job, lane="pre")
    assert release.release_state(payload) == release.DIAGNOSTIC_RELEASE
    assert any(
        issue["code"] == "pre_learning_map_refused"
        for issue in payload["issues"]
    )
    with pytest.raises(ValueError) as raised:
        publication.upload_release_to_database(
            db, job.id, owner_sub=OWNER, lane="pre")
    assert "refused its own artefact" in str(raised.value)
    # Evidence still ships (Rule E).
    assert release_files.release_payload_bytes(job, lane="pre")


# --------------------------------------------------------------------------- #
# 10. The ledger and the run report say what shipped
# --------------------------------------------------------------------------- #

def test_the_coverage_ledger_reports_what_the_pre_lane_shipped(db):
    chapter = _chapter_with_concepts(db)
    job = _both_lanes_job(db, chapter, questions=2)
    pre_map = _pre_map()

    ledger = coverage_ledger.build_coverage_ledger(
        question_inventory=copy.deepcopy(SOURCE_INVENTORY),
        records=release.release_payload(job)["records"] + pre_map["rows"],
        pre_map_snapshot=pre_map,
        pre_questions_snapshot=_pre_questions(2),
        pre_release_payload=release.release_payload(job, lane="pre"),
    )
    pre = ledger["summary"]["pre_learning"]
    assert pre["generated_questions"]["total"] == 2
    assert pre["generated_questions"]["pre_concepts_with_questions"] == 1
    assert pre["released"]["staged"] is True
    assert pre["released"]["generated_questions"] == 2
    assert pre["released"]["release_state"] in {
        release.READY, release.READY_WITH_FLAGS,
    }

    rendered = coverage_ledger.render_coverage(ledger)
    assert "generated questions: 2 authored" in rendered
    assert "staged Pre release:" in rendered


def test_the_ledger_reports_an_under_target_concept_without_charging_it(db):
    """Q4: an adaptive target is never a quota, so variance is not a debt."""

    pre_map = _pre_map()
    plan = _pre_questions(1)
    plan["plans"]["PRC-0001"]["total"] = 5

    ledger = coverage_ledger.build_coverage_ledger(
        question_inventory={"items": []},
        records=pre_map["rows"],
        pre_map_snapshot=pre_map,
        prelearn_snapshot={
            "prerequisites": [
                {"prerequisite_id": "PRQ-0001", "text": "counts to ten"},
            ],
        },
        pre_questions_snapshot=plan,
    )
    rendered = coverage_ledger.render_coverage(ledger)
    assert "authored 1 of the 5" in rendered
    assert "not a quota" in rendered
    assert ledger["complete"] is True, (
        "a target missed is reported, never charged as incompleteness"
    )


# --------------------------------------------------------------------------- #
# 11. The run stages the sibling, and the Refiner's seam accepts it
# --------------------------------------------------------------------------- #

def test_the_release_contract_stages_the_sibling_from_the_run_snapshots(
    db, tmp_path, monkeypatch,
):
    """One run, four outputs (Q3) — through the installed release wrapper.

    The Pre map and its generated questions leave the sealed phase-3
    boundary as snapshots beside the decision store, which is also how
    they survive a resume that skips phase 3 entirely. The contract reads
    them back rather than re-entering generation.
    """

    from app.services import build_concepts_release_contract as contract
    from app.services import uploads

    chapter = _chapter_with_concepts(db)
    job = models.UploadJob(
        owner_sub=None,
        module="build_concepts",
        upload_type="textbook",
        filename="ch.mmd",
        mmd_text="# Chapter",
        status="converted",
        learning_kind="post",
        deposit_scope_type="chapter",
        deposit_scope_ids=[chapter.id],
        question_inventory={"items": []},
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    (tmp_path / release.PRE_MAP_SNAPSHOT).write_text(
        json.dumps(_pre_map(topic="Counting staged", concept="Counting staged"),
                   ensure_ascii=False),
        encoding="utf-8",
    )
    (tmp_path / release.PRE_QUESTIONS_SNAPSHOT).write_text(
        json.dumps(_pre_questions(2), ensure_ascii=False), encoding="utf-8",
    )
    monkeypatch.setattr(
        uploads, "source_artifact_directory", lambda _job_id: tmp_path,
        raising=False,
    )
    contract.install()

    def _original(_db, _job_id, _chapter_id, **_kwargs):
        contract._capture_deposit(
            lambda records=None, chapter_id=None, **_k: None,
            (),
            {"records": _post_records(), "chapter_id": chapter.id},
        )
        return {}

    contract._run_generation_release(
        _original, db, job.id, chapter.id, owner_sub=None,
    )
    db.refresh(job)

    assert release.release_available(job)
    assert release.pre_release_available(job)
    pre = release.release_payload(job, lane="pre")
    assert [row["concept_title"] for row in pre["records"]] == [
        "Counting staged"
    ]
    assert len(pre["generated_questions"]) == 2
    # The job's headline state stays the POST release's; the sibling does
    # not move status or result_ids.
    assert job.status == release.RELEASE_STATUS
    assert job.result_ids == []


def test_the_pre_lane_joined_the_refiner_seam(db, tmp_path, monkeypatch):
    """§8.3's designed hook, used rather than left as a docstring promise.

    The Refiner never blocks, so what is pinned here is that the Pre lane
    reaches the seam under its own ``output_kind`` and that its outcome —
    refined or unavailable — is RECORDED on the release either way.
    """

    from app.services import release_refiner
    from app.services import uploads

    assert "pre_concepts_release" in release_refiner._CONCEPT_OUTPUT_KINDS
    seen: list[str] = []
    original = release_refiner.refine_release

    def _spy(records, **kwargs):
        seen.append(str(kwargs.get("output_kind")))
        return original(records, **kwargs)

    monkeypatch.setattr(release_refiner, "refine_release", _spy)

    chapter = _chapter_with_concepts(db)
    job = models.UploadJob(
        owner_sub=OWNER, module="build_concepts", upload_type="textbook",
        filename="ch.mmd", mmd_text="# Chapter", status="generated",
        learning_kind="post", deposit_scope_type="chapter",
        deposit_scope_ids=[chapter.id], question_inventory={"items": []},
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    release.stage_release(
        db, job, target_chapter_id=chapter.id, records=_post_records(),
        reason="post",
    )
    (tmp_path / release.PRE_MAP_SNAPSHOT).write_text(
        json.dumps(_pre_map(topic="Counting refined", concept="Counting refined")),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        uploads, "source_artifact_directory", lambda _job_id: tmp_path,
        raising=False,
    )

    release.stage_pre_release_from_run(
        db, job, target_chapter_id=chapter.id, reason="refiner seam",
    )
    db.refresh(job)

    assert seen == [release.PRE_REFINER_OUTPUT_KIND]
    refinements = release.release_payload(job, lane="pre")["refinements"]
    assert refinements["output_kind"] == release.PRE_REFINER_OUTPUT_KIND
    assert "summary" in refinements


def test_a_broken_pre_lane_never_costs_the_finished_post_release(
    db, tmp_path, monkeypatch,
):
    """"Finished work always ships" — the Pre lane cannot take Post down."""

    from app.services import uploads

    chapter = _chapter_with_concepts(db)
    job = models.UploadJob(
        owner_sub=OWNER, module="build_concepts", upload_type="textbook",
        filename="ch.mmd", mmd_text="# Chapter", status="generated",
        learning_kind="post", deposit_scope_type="chapter",
        deposit_scope_ids=[chapter.id], question_inventory={"items": []},
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    release.stage_release(
        db, job, target_chapter_id=chapter.id, records=_post_records(),
        reason="post",
    )
    post_before = copy.deepcopy(release.release_payload(job))

    (tmp_path / release.PRE_MAP_SNAPSHOT).write_text(
        json.dumps(_pre_map()), encoding="utf-8",
    )
    monkeypatch.setattr(
        uploads, "source_artifact_directory", lambda _job_id: tmp_path,
        raising=False,
    )
    monkeypatch.setattr(
        release, "stage_pre_release",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("pre lane broke")),
    )

    assert release.stage_pre_release_from_run(db, job) is None
    db.refresh(job)
    assert release.release_payload(job) == post_before
    assert release.release_payload(job, lane="pre") is None


def test_the_pre_release_payload_carries_no_chapter_question_identity(db):
    """The steer's mechanical rule, over the WHOLE payload.

    "No QID from the chapter's question/task inventory may appear
    anywhere in a Pre row **or the Pre release payload**. That is
    identity accounting, not judgment" (owner steer, 17 Aug 2026). Both
    halves are asserted, and the payload half is asserted over the bytes
    the reviewer actually downloads — that is where a leak would be
    visible, and where the chapter's ``question_task_inventory`` used to
    sit with every ``raw_task`` in it.
    """

    chapter = _chapter_with_concepts(db)
    job = _both_lanes_job(
        db, chapter, topic="Counting identity", concept="Counting identity",
    )
    payload = release.release_payload(job, lane="pre")

    rows_text = json.dumps(payload["records"], ensure_ascii=False)
    for qid in ("QINV-0001", "QINV-0002"):
        assert qid not in rows_text
    assert all(
        row[release.RELEASE_ROW_QIDS_FIELD] == []
        for row in payload["records"]
    )
    # The whole payload, and the Output-03 download built from it.
    whole = json.dumps(payload, ensure_ascii=False)
    downloaded = release_files.release_payload_bytes(
        job, lane="pre"
    ).decode("utf-8")
    for qid in ("QINV-0001", "QINV-0002"):
        assert qid not in whole
        assert qid not in downloaded
    assert "Which of these is a solid?" not in downloaded, (
        "nor the source question's own wording, which rode the inventory"
    )
    assert payload["question_task_inventory"] == {}
    # The Post sibling on the SAME job is untouched by that rule: Output 02
    # is built from the chapter's questions, so its payload keeps them.
    post = release.release_payload(job)
    assert {
        item["qid"] for item in post["question_task_inventory"]["items"]
    } == {"QINV-0001", "QINV-0002"}
    # ...and the sibling slots never nest inside one another.
    assert release.RELEASE_KEY not in post["question_task_inventory"]
    assert release.PRE_RELEASE_KEY not in post["question_task_inventory"]


def test_the_job_manifest_offers_all_four_outputs_to_the_reviewer(db, client):
    """The reviewer sees Outputs 03/04 beside 01/02, in the same list.

    ``_install_manifest_extension`` folds ``release_artifact_entries`` into
    the job's ``source_artifacts.files``, which the review surface renders
    generically — so the Pre downloads appear the moment the entries do,
    with no per-kind frontend change (step 9 owns the rendered pages).
    """

    from app.services import build_concepts_release_contract as contract

    contract.install()
    chapter = _chapter_with_concepts(db)
    job = _both_lanes_job(
        db, chapter, topic="Counting manifest", concept="Counting manifest",
    )

    files = client.get(
        f"/build-concepts/uploads/{job.id}"
    ).json()["source_artifacts"]["files"]
    by_kind = {row["kind"]: row for row in files}

    assert _PRE_ENTRY_KINDS <= set(by_kind)
    assert {
        "released_concepts", "release_diagnostics", "release_payload",
        "database_upload",
    } <= set(by_kind)
    for kind in _PRE_ENTRY_KINDS:
        assert by_kind[kind]["download_url"].endswith("?lane=pre")
    # The Post entries keep their exact, laneless URLs.
    assert by_kind["release_payload"]["download_url"] == (
        f"/build-concepts/uploads/{job.id}/release.json"
    )


# --------------------------------------------------------------------------- #
# 12. What the audits found, and what now holds it
#
# Every test below pins a defect three independent audits reached by
# execution against the slice as first written. They are grouped here
# rather than merged into the sections above because each one is a
# distinct failure mode with its own doctrine reference, and a later
# reader deleting one should have to say which finding they are
# reopening.
# --------------------------------------------------------------------------- #

def _snapshot_job(db, chapter, *, filename="ch.mmd"):
    job = models.UploadJob(
        owner_sub=OWNER, module="build_concepts", upload_type="textbook",
        filename=filename,
        mmd_text="# Chapter\n\nExercise 1. Which of these is a solid?",
        status="generated", learning_kind="post", deposit_scope_type="chapter",
        deposit_scope_ids=[chapter.id],
        question_inventory=copy.deepcopy(SOURCE_INVENTORY),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    release.stage_release(
        db, job, target_chapter_id=chapter.id, records=_post_records(),
        inventory=copy.deepcopy(SOURCE_INVENTORY), reason="post",
    )
    db.refresh(job)
    return job


def _point_at(monkeypatch, tmp_path):
    from app.services import uploads

    monkeypatch.setattr(
        uploads, "source_artifact_directory", lambda _job_id: tmp_path,
        raising=False,
    )


def test_an_unreadable_questions_snapshot_is_never_an_empty_pre_lane(
    db, tmp_path, monkeypatch,
):
    """R4: a chapter's generated questions are never lost without a word.

    The questions snapshot is on disk and truncated. Read as "absent",
    that is indistinguishable from a lane that authored nothing, and
    Output 04 would ship empty, flagless and ``ready`` with every
    generated question gone — "silently losing a learner's question is
    never recoverable" (CLAUDE.md). Structural corruption blocks the
    database write and every download stays open (§4, Rule E).
    """

    chapter = _chapter_with_concepts(db)
    job = _snapshot_job(db, chapter)
    (tmp_path / release.PRE_MAP_SNAPSHOT).write_text(
        json.dumps(_pre_map()), encoding="utf-8",
    )
    (tmp_path / release.PRE_QUESTIONS_SNAPSHOT).write_text(
        json.dumps(_pre_questions(2))[:40], encoding="utf-8",
    )
    _point_at(monkeypatch, tmp_path)

    assert release.stage_pre_release_from_run(
        db, job, target_chapter_id=chapter.id, reason="truncated questions",
    ) is not None
    db.refresh(job)
    payload = release.release_payload(job, lane="pre")

    assert payload["records"], "the map read fine, so its rows still ship"
    assert payload["generated_questions"] == []
    # ...and the release says WHY they are missing, rather than implying
    # the chapter needed none.
    assert payload["snapshot_defects"], (
        "the unreadable snapshot must be recorded on the payload"
    )
    assert release.PRE_QUESTIONS_SNAPSHOT in payload["snapshot_defects"][0]
    assert any(
        row["code"] == "pre_learning_snapshot_unreadable"
        and row["severity"] == "error"
        for row in payload["issues"]
    )
    assert release.release_state(payload) == release.DIAGNOSTIC_RELEASE
    assert any(
        "could not be read" in defect
        for defect in release.structural_defects(payload)
    )
    # Evidence still ships (Rule E) — the block is on the database write.
    assert release_files.release_payload_bytes(job, lane="pre")
    assert release_files.build_release_workbook(job, lane="pre")
    with pytest.raises(ValueError):
        publication.upload_release_to_database(
            db, job.id, owner_sub=OWNER, lane="pre",
        )


def test_a_lane_that_authored_no_question_stays_distinguishable_from_that(
    db, tmp_path, monkeypatch,
):
    """The other half of the pair above — and it must NOT be a defect.

    A real Phase 03 run whose questions snapshot says "I authored none"
    is a legitimate, flag-free Pre release. Only an UNREADABLE snapshot
    is structural corruption. Collapsing the two in either direction is
    the R4 failure.
    """

    chapter = _chapter_with_concepts(db)
    job = _snapshot_job(db, chapter)
    (tmp_path / release.PRE_MAP_SNAPSHOT).write_text(
        json.dumps(_pre_map()), encoding="utf-8",
    )
    (tmp_path / release.PRE_QUESTIONS_SNAPSHOT).write_text(
        json.dumps({
            "plans": {}, "questions": {}, "blocked": {},
            "review_flags": {}, "decision_flags": {},
        }),
        encoding="utf-8",
    )
    _point_at(monkeypatch, tmp_path)

    release.stage_pre_release_from_run(
        db, job, target_chapter_id=chapter.id, reason="authored none",
    )
    db.refresh(job)
    payload = release.release_payload(job, lane="pre")

    assert payload["generated_questions"] == []
    assert payload["snapshot_defects"] == []
    assert release.structural_defects(payload) == []
    assert release.release_state(payload) == release.READY


def test_an_unreadable_map_snapshot_is_recorded_not_silently_absent(
    db, tmp_path, monkeypatch, capsys,
):
    """"No Pre lane at all" and "the Pre map is corrupt" are different.

    Returning ``None`` for both made a corrupt artefact render as the
    ledger line a chapter with no Pre lane gets, with nothing logged at
    all — the reviewer could not tell the two apart.
    """

    from app.services import progress

    logged: list[str] = []
    monkeypatch.setattr(
        progress, "log",
        lambda message, **kw: logged.append(str(message)),
    )

    chapter = _chapter_with_concepts(db)
    job = _snapshot_job(db, chapter)
    (tmp_path / release.PRE_MAP_SNAPSHOT).write_text(
        "{not json at all", encoding="utf-8",
    )
    _point_at(monkeypatch, tmp_path)

    assert release.stage_pre_release_from_run(
        db, job, target_chapter_id=chapter.id, reason="corrupt map",
    ) is not None
    db.refresh(job)
    payload = release.release_payload(job, lane="pre")

    assert payload["snapshot_defects"], "the defect rides the payload"
    assert release.PRE_MAP_SNAPSHOT in payload["snapshot_defects"][0]
    assert release.release_state(payload) == release.DIAGNOSTIC_RELEASE
    assert any("could not be read" in line for line in logged), logged
    # The Post release is untouched, as always.
    assert release.release_payload(job)["records"]


def test_no_pre_lane_at_all_still_stages_no_sibling(
    db, tmp_path, monkeypatch,
):
    """The absent case keeps its own answer: stage nothing."""

    chapter = _chapter_with_concepts(db)
    job = _snapshot_job(db, chapter)
    _point_at(monkeypatch, tmp_path)

    assert release.stage_pre_release_from_run(
        db, job, target_chapter_id=chapter.id, reason="no phase 3",
    ) is None
    db.refresh(job)
    assert release.release_payload(job, lane="pre") is None


# --------------------------------------------------------------------------- #
# Identity accounting at OUTPUT 03's boundary (the steer's own rule)
# --------------------------------------------------------------------------- #

def test_a_critic_flag_naming_a_source_question_is_redacted_not_refused(db):
    """The ordering rule, on Output 03 — the same one Output 04 uses.

    ``premap``'s doctrine puts review flags OUTSIDE the guarded surface
    by ordering, precisely so the advisory critic may name the source
    question it compared a prerequisite against; Q10 forbids that dissent
    from gating anything. But the flags are transcribed verbatim into the
    release ``issues`` and ride the records, so redaction must run over
    the audit channels — and it must run FIRST, and never raise, so a
    critic can flag freely and can never brick the run.
    """

    chapter = _chapter_with_concepts(db)
    pre_map = _pre_map()
    pre_map["rows"][0]["review_flags"] = [
        "critic: this prerequisite may just be source question "
        "QINV-0001 reworded; cf QINV-0002",
    ]
    job = _snapshot_job(db, chapter)
    release.stage_pre_release(
        db, job, target_chapter_id=chapter.id, pre_map=pre_map,
        pre_questions=_pre_questions(1),
        inventory=copy.deepcopy(SOURCE_INVENTORY),
        reason="critic dissent naming a QID",
    )
    db.refresh(job)
    payload = release.release_payload(job, lane="pre")

    # The run COMPLETED and the dissent SURVIVED — redacted, not dropped.
    assert release.release_state(payload) == release.READY_WITH_FLAGS
    assert payload["records"], "a flag never costs the rows"
    flagged = [
        row for row in payload["issues"]
        if row["code"] == "pre_learning_review_flag"
    ]
    assert flagged and "reworded" in flagged[0]["message"]
    # ...and no chapter identity survives anywhere in the artefact.
    downloaded = release_files.release_payload_bytes(
        job, lane="pre"
    ).decode("utf-8")
    for qid in ("QINV-0001", "QINV-0002"):
        assert qid not in json.dumps(payload, ensure_ascii=False)
        assert qid not in downloaded
    # ...including inside the diagnostics archive, in the members that ARE
    # the Pre release. The rest of that archive is deliberately not in
    # scope: it is the run's evidence bundle and carries the chapter's own
    # material by design — the converted source MMD with its exercises,
    # the question inventory, the generation checkpoint. The steer's
    # mechanical rule is about a Pre ROW and the Pre RELEASE PAYLOAD, and
    # an auditor's account of the run is neither.
    diagnostics = release_files.build_diagnostics_zip(job, lane="pre")
    with zipfile.ZipFile(io.BytesIO(diagnostics)) as archive:
        members = [
            name for name in archive.namelist() if name.startswith("release/")
        ]
        assert "release/pre_release_payload.json" in members
        blob = b"".join(archive.read(name) for name in members)
    for qid in (b"QINV-0001", b"QINV-0002"):
        assert qid not in blob


def test_a_source_qid_in_an_authored_pre_row_is_refused_at_the_boundary(db):
    """The refusal half, over the AUTHORED surface, fail-closed.

    ``premap`` guards the rows upstream, before any flag is stamped — but
    the Refiner rewrites them afterwards, so the guard runs again here.
    It fails closed: the rows do not ship. It does not stop the run and
    does not delete the lane, because losing Outputs 03/04 without a word
    would be the R4 failure and halting would spend a finished Post
    release on a Pre-lane defect.
    """

    chapter = _chapter_with_concepts(db)
    pre_map = _pre_map()
    pre_map["rows"][0]["concept_details"] = (
        "Description: this restates QINV-0001.\nAchieving Mastery: count."
    )
    job = _snapshot_job(db, chapter)
    release.stage_pre_release(
        db, job, target_chapter_id=chapter.id, pre_map=pre_map,
        pre_questions=_pre_questions(1),
        inventory=copy.deepcopy(SOURCE_INVENTORY),
        reason="authored row naming a QID",
    )
    db.refresh(job)
    payload = release.release_payload(job, lane="pre")

    assert payload["records"] == []
    assert payload["generated_questions"] == []
    assert "QINV-0001" in payload["refused"]
    assert any(
        row["code"] == "pre_learning_source_identity_refused"
        for row in payload["issues"]
    )
    assert release.release_state(payload) == release.DIAGNOSTIC_RELEASE
    with pytest.raises(ValueError):
        publication.upload_release_to_database(
            db, job.id, owner_sub=OWNER, lane="pre",
        )
    # The Post release still ships and still publishes — the whole point.
    assert release.release_payload(job)["records"]
    publication.upload_release_to_database(db, job.id, owner_sub=OWNER)


def test_the_generated_questions_are_accounted_for_before_any_spend(db):
    """Both of the generated lane's inputs are checked pre-spend, not one.

    A chapter QID inside a staged generated question is certain to be
    refused by the final barrier — but only after materialization and the
    critic have spent, and then it replays that refusal at zero spend on
    every retry, because the decisions are content-addressed and the
    staged payload is immutable. It is fully knowable here, which is what
    CLAUDE.md reserves a pre-spend stop for.
    """

    chapter = _chapter_with_concepts(db)
    job = _both_lanes_job(db, chapter)
    inventory = copy.deepcopy(job.question_inventory)
    inventory[release.PRE_RELEASE_KEY]["generated_questions"][0][
        "question_text"
    ] = "See QINV-0001: which of these is a solid?"
    job.question_inventory = inventory
    db.commit()
    db.refresh(job)

    from app.services.phase3 import premap

    calls: dict[str, list] = {}
    authorities, _ = _generated_authorities(calls=calls)
    before = db.query(models.AssessmentRelease).count()
    with pytest.raises(premap.PreExtractionError) as raised:
        run.run_pre_release_for_job(
            db, job.id, owner_sub=OWNER, authorities=authorities,
            **_decision_context())

    assert "QINV-0001" in str(raised.value)
    assert calls == {}, (
        "the refusal must cost nothing: both inputs are in hand here"
    )
    assert db.query(models.AssessmentRelease).count() == before


def test_the_source_question_barrier_answers_409_rather_than_500(db, client):
    """The barrier's two exception types are unrelated classes.

    ``SourceQuestionLeak`` is a ``ReleaseRunError``; the QID half raises
    ``premap.PreExtractionError``, a bare ``RuntimeError``, because it
    reuses the Pre lane's one guard rather than wrapping it. The QID half
    is the REACHABLE one, so without an arm for it the barrier's
    reachable half surfaced as an unhandled 500 while its
    coding-fault-only half got the clean 409.
    """

    chapter = _chapter_with_concepts(db)
    job = _both_lanes_job(db, chapter)
    inventory = copy.deepcopy(job.question_inventory)
    inventory[release.PRE_RELEASE_KEY]["generated_questions"][0][
        "question_text"
    ] = "See QINV-0001: which of these is a solid?"
    job.question_inventory = inventory
    db.commit()

    response = client.post(f"/build-assessments/releases/from-job/{job.id}/pre")

    assert response.status_code == 409, response.text
    assert "QINV-0001" in response.json()["detail"]


# --------------------------------------------------------------------------- #
# The paired writers, and the exits that stage the sibling
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("filename", [
    "ch 1 (draft).mmd", "Grade 6: Science.mmd", "ch#1.mmd",
])
def test_both_manifests_agree_on_an_awkward_source_filename(db, filename):
    """The twin pin is only as good as the pieces the twins share.

    ``_safe_filename`` was itself a divergent twin — one collapsed
    whitespace, the other sanitised to a safe character set — so the
    equality assertion in the pin above held for the benign fixture name
    and for nothing else. The advertised stem is what the browser saves
    the file as, so the divergence was user-visible.
    """

    chapter = _chapter_with_concepts(db)
    job = _both_lanes_job(db, chapter)
    job.filename = filename
    db.commit()
    db.refresh(job)

    eager = {
        row["kind"]: row["filename"]
        for row in release_files.eager_release_artifact_entries(job)
    }
    lazy = {
        row["kind"]: row["filename"]
        for row in release_manifest.release_artifact_entries(job)
    }
    assert eager == lazy
    for name in eager.values():
        assert not (set(name) & set(" :#()")), name


def test_every_release_exit_stages_the_pre_sibling(db, tmp_path, monkeypatch):
    """Outputs 03/04 must not depend on WHICH exit released the Post lane.

    The Pre lane's inputs are the Phase 03 snapshots on disk, not the
    captured rows, so a run that completed Phase 03 and then failed after
    the deposit boundary — or hit an unresolved semantic boundary and
    released a checkpoint instead — has a Pre map available. Staged from
    only one of the four exits, its absence on the others reads exactly
    like a chapter with no Pre lane at all.
    """

    from app.services import build_concepts_release_contract as contract

    chapter = _chapter_with_concepts(db)
    (tmp_path / release.PRE_MAP_SNAPSHOT).write_text(
        json.dumps(_pre_map()), encoding="utf-8",
    )
    (tmp_path / release.PRE_QUESTIONS_SNAPSHOT).write_text(
        json.dumps(_pre_questions(1)), encoding="utf-8",
    )
    _point_at(monkeypatch, tmp_path)

    # The unresolved-boundary exit: no captured rows at all.
    job = _snapshot_job(db, chapter)
    contract._release_after_result(
        db, job.id, chapter.id, owner_sub=OWNER,
        result={"pending_decision": {"kind": "semantic"}}, captured=None,
    )
    db.refresh(job)
    staged = release.release_payload(job, lane="pre")
    assert staged is not None, (
        "the checkpoint-fallback exit must still stage Outputs 03/04"
    )
    assert staged["records"]

    # The post-deposit failure exits, both of them.
    for captured in (
        {"records": _post_records(), "inventory": copy.deepcopy(SOURCE_INVENTORY)},
        None,
    ):
        other = _snapshot_job(db, chapter)
        contract._run_generation_release(
            _raise_after_capture(captured), db, other.id, chapter.id,
            owner_sub=OWNER,
        )
        db.refresh(other)
        assert release.release_payload(other, lane="pre") is not None, (
            f"the failure exit with captured={bool(captured)} must stage too"
        )


def _raise_after_capture(captured):
    from app.services import build_concepts_release_contract as contract

    def _original(db, job_id, target_chapter_id, *args, **kwargs):
        if captured is not None:
            contract._RELEASE_CAPTURE.set(copy.deepcopy(captured))
        raise RuntimeError("generation failed after the deposit boundary")

    return _original

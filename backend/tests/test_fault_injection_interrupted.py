"""Step 12 residues — the two DEFERRED fault injections (spec-step12
"Residues"): an interrupted RELEASE (staging dies mid-write) and an
interrupted PUBLICATION (the DB write dies partway), buildable only now
that S9/S10 have landed.

No product code is edited; frozen files are exercised, never modified.
Each test injects at a documented seam and pins the CURRENT contract:

* CASE E — staging is atomic-at-the-end. ``stage_release`` computes every
  piece of the payload in memory (issues, QC audit, row defects, summary,
  chapter meta, the minted version and lineage uid) and only THEN writes
  the slot in one assignment plus one commit
  (build_concepts_release.py:2402-2415). A crash inside the LAST helper
  the payload assembly calls (``_instruction_set_summary``, evaluated at
  build_concepts_release.py:2394, after the version and uid were already
  minted) therefore leaves the job's release slot either byte-identical
  to the COMPLETE previous payload or absent — never half-written.

* CASE F — publication is one transaction. ``upload_release_to_database``
  flushes per row but the only ``db.commit()`` on the row path runs inside
  ``build_concepts._commit_and_publish_concept_workbook`` AFTER every row,
  and the enclosing ``except Exception: db.rollback(); raise``
  (build_concepts_release_publication.py:670-672) discards every flushed
  row on a crash partway — full rollback, no partial rows, no
  ``database_uploaded`` latch, and a retry is a clean first publication
  that can never duplicate what the crash left behind, because the crash
  leaves nothing behind.

Nothing learner-visible vanishes anywhere below: Case E's crashed
re-stage keeps the complete previous payload (every staged row still
present and downloadable), and Case F's crashed publication persists
zero rows precisely so that no row can be half-shipped — the staged
payload itself is untouched and republishes in full on the retry.
"""
from __future__ import annotations

import copy

import pytest

from app import models
from app.services import build_concepts
from app.services import build_concepts_release as release
from app.services import build_concepts_release_files as release_files
from app.services import build_concepts_release_publication as publication

from tests.test_assessment_release_run import OWNER
from tests.test_step9_rule_e_deraising import (
    _INVENTORY,
    _SOUND_ROW,
    _SOUND_SECOND_ROW,
)
from tests.test_step10_converged_publication import (
    _chapter,
    _job_with_records,
    _stage_synthetic_release,
)


_STAGING_CRASH = (
    "injected staging crash: the process died inside payload assembly "
    "(test_fault_injection_interrupted, Case E)"
)
_PUBLICATION_CRASH = (
    "injected publication crash: the process died after the first row "
    "was written (test_fault_injection_interrupted, Case F)"
)


def _fresh_converted_job(db, chapter) -> models.UploadJob:
    """A converted-but-never-staged job: ``_job_with_records`` minus the
    ``stage_release`` call, so Case E can crash the FIRST staging."""
    job = models.UploadJob(
        owner_sub=OWNER,
        module="build_concepts",
        upload_type="textbook",
        filename="ch.mmd",
        mmd_text="# Chapter\n\nExercise 1. Which of these is a solid?",
        status="converted",
        deposit_scope_type="chapter",
        deposit_scope_ids=[chapter.id],
        question_inventory=copy.deepcopy(_INVENTORY),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def _arm_staging_crash(monkeypatch):
    """Inject at the LAST helper stage_release's payload assembly calls.

    ``_instruction_set_summary`` is evaluated inside the payload dict
    literal (build_concepts_release.py:2394) AFTER ``next_staged_version``
    and the ``uuid4().hex`` lineage mint, and BEFORE the one slot
    assignment + commit — so the crash lands with maximal partial state
    computed and nothing yet written.
    """
    real = release._instruction_set_summary
    state = {"armed": True}

    def dying(job):
        if state["armed"]:
            raise RuntimeError(_STAGING_CRASH)
        return real(job)

    monkeypatch.setattr(release, "_instruction_set_summary", dying)
    return state


def _chapter_concepts(db, chapter_id):
    return (
        db.query(models.Concept)
        .join(models.Topic)
        .filter(models.Topic.chapter_id == chapter_id)
        .all()
    )


# --------------------------------------------------------------------------- #
# CASE E — interrupted release (staging dies mid-write)
# --------------------------------------------------------------------------- #

def test_a_crash_during_the_first_staging_leaves_the_slot_absent_and_retry_completes(
    db, monkeypatch,
):
    """Atomic-at-the-end staging, fresh-job half: the payload is assembled
    entirely in memory and written to ``question_inventory[RELEASE_KEY]``
    in one assignment followed by one commit
    (build_concepts_release.py:2402-2415). A crash inside the last helper
    of the payload assembly — after the QC audit, the summary, the minted
    version and the lineage uid were all computed — leaves NO release slot
    at all: a subsequent fresh read sees no payload and no partial key,
    never a half-written payload. The retry then completes normally with
    the seal fields present.
    """
    chapter = _chapter(db, "06CBSC_S12IntE1")
    job = _fresh_converted_job(db, chapter)
    state = _arm_staging_crash(monkeypatch)

    with pytest.raises(RuntimeError, match="injected staging crash"):
        _stage_synthetic_release(
            db,
            job,
            target_chapter_id=chapter.id,
            records=[dict(_SOUND_ROW)],
            inventory=copy.deepcopy(_INVENTORY),
            reason="Case E first staging (will crash)",
        )

    # A subsequent read — expired, so it comes from the database, as a new
    # process would read it — must not see partial data.
    db.expire_all()
    crashed = db.get(models.UploadJob, job.id)
    assert release.release_payload(crashed) is None
    assert release.RELEASE_KEY not in dict(crashed.question_inventory or {})
    assert crashed.question_inventory == _INVENTORY
    assert crashed.status == "converted", (
        "the crashed staging must not have advanced the job lifecycle"
    )

    # Retry after the crash: completes normally, payload complete, both
    # seal fields present (the minted draft version and the lineage uid
    # ``_refuse_a_moved_seal`` keys on).
    state["armed"] = False
    _stage_synthetic_release(
        db,
        crashed,
        target_chapter_id=chapter.id,
        records=[dict(_SOUND_ROW)],
        inventory=copy.deepcopy(_INVENTORY),
        reason="Case E retry",
    )
    db.refresh(crashed)
    payload = release.release_payload(crashed)
    assert payload is not None
    assert payload["version"] == release.RELEASE_VERSION
    assert release.staged_version(payload) == 1, (
        "the crashed attempt minted no version: the retry is the first "
        "staging the slot ever recorded"
    )
    assert str(payload[release.STAGED_RELEASE_UID_FIELD]).strip()
    assert [r["concept_title"] for r in payload["records"]] == [
        _SOUND_ROW["concept_title"]
    ]
    assert release.structural_defects(payload) == []
    assert crashed.status == "released"


def test_a_crash_during_a_restage_keeps_the_previous_payload_byte_identical(
    db, monkeypatch,
):
    """Atomic-at-the-end staging, re-stage half: when a staged v1 already
    occupies the slot and the v2 staging crashes inside payload assembly,
    the slot still holds the COMPLETE v1 payload byte for byte — same
    version, same lineage uid, same records, same summary — never a
    half-written v2 and never a torn mix of the two. Nothing
    learner-visible is lost: every v1 row is still staged and
    publishable. The retry then mints v2 with a NEW lineage uid.
    """
    chapter = _chapter(db, "06CBSC_S12IntE2")
    job = _job_with_records(db, chapter, [dict(_SOUND_ROW)])
    before = release.release_payload(job)
    assert release.staged_version(before) == 1
    v1_uid = str(before[release.STAGED_RELEASE_UID_FIELD])

    state = _arm_staging_crash(monkeypatch)
    with pytest.raises(RuntimeError, match="injected staging crash"):
        _stage_synthetic_release(
            db,
            job,
            target_chapter_id=chapter.id,
            records=[dict(_SOUND_ROW), dict(_SOUND_SECOND_ROW)],
            inventory=copy.deepcopy(dict(job.question_inventory or {})),
            reason="Case E re-stage (will crash)",
        )

    db.expire_all()
    crashed = db.get(models.UploadJob, job.id)
    after = release.release_payload(crashed)
    assert after == before, (
        "the crashed re-stage must leave the COMPLETE previous payload — "
        "not a half-written v2, not a torn mix"
    )
    assert release.staged_version(after) == 1
    assert str(after[release.STAGED_RELEASE_UID_FIELD]) == v1_uid
    assert [r["concept_title"] for r in after["records"]] == [
        _SOUND_ROW["concept_title"]
    ]

    # Retry: the re-stage completes, v2 replaces v1 whole, with a freshly
    # minted lineage uid (a legitimate re-stage, never a tamper).
    state["armed"] = False
    _stage_synthetic_release(
        db,
        crashed,
        target_chapter_id=chapter.id,
        records=[dict(_SOUND_ROW), dict(_SOUND_SECOND_ROW)],
        inventory=copy.deepcopy(dict(crashed.question_inventory or {})),
        reason="Case E re-stage retry",
    )
    db.refresh(crashed)
    payload = release.release_payload(crashed)
    assert release.staged_version(payload) == 2
    retry_uid = str(payload[release.STAGED_RELEASE_UID_FIELD]).strip()
    assert retry_uid and retry_uid != v1_uid
    assert [r["concept_title"] for r in payload["records"]] == [
        _SOUND_ROW["concept_title"], _SOUND_SECOND_ROW["concept_title"],
    ]
    assert release.structural_defects(payload) == []


# --------------------------------------------------------------------------- #
# CASE F — interrupted publication (the DB write dies partway)
# --------------------------------------------------------------------------- #

def test_a_crash_after_the_first_written_row_rolls_back_and_retry_publishes_without_duplicates(
    db, monkeypatch,
):
    """One transaction, full rollback — the pinned CURRENT contract.

    The injection seam is ``build_concepts._add_concept``, the per-record
    creation helper the publication loop calls
    (build_concepts_release_publication.py:487): the wrapper lets row 1
    write and flush, then raises on row 2 — and proves, from inside the
    dying transaction, that row 1 WAS already visible there. The
    publication's ``except Exception: db.rollback(); raise``
    (build_concepts_release_publication.py:670-672) then discards it: the
    only ``db.commit()`` on this path runs inside
    ``build_concepts._commit_and_publish_concept_workbook`` after every
    row, so the crash persists NOTHING — no partial rows, no orphan topic,
    no ``database_uploaded`` latch. No learner-visible row vanishes: the
    staged payload still holds both rows and the retry publishes both.

    The retry is therefore a clean first publication (both rows CREATED,
    none merged — S10's carried-identity/title merge machinery has nothing
    to merge with because nothing survived), and the non-negotiable
    property holds: crash + retry yields exactly the staged rows, each
    with a unique machine identity — the ids the transient export
    predicted — and NO duplicate concepts.
    """
    chapter = _chapter(db, "06CBSC_S12IntF")
    staged_rows = [
        {"topic": "Interruption", "concept_title": "First Written Row",
         "concept_details": "Row one, written before the crash.",
         "keywords": "first"},
        {"topic": "Interruption", "concept_title": "Second Never-Written Row",
         "concept_details": "Row two, where the crash lands.",
         "keywords": "second"},
    ]
    job = _job_with_records(db, chapter, staged_rows)

    real_add = build_concepts._add_concept
    state = {"created": 0, "armed": True, "rows_visible_at_crash": None}

    def dying_add(db_, topic, rec, source_book="", *, clean=True):
        if state["armed"] and state["created"] >= 1:
            # Prove the partial state: the first row is already written
            # (flushed) inside the still-open transaction when we die.
            state["rows_visible_at_crash"] = len(
                _chapter_concepts(db_, chapter.id))
            raise RuntimeError(_PUBLICATION_CRASH)
        concept = real_add(db_, topic, rec, source_book, clean=clean)
        state["created"] += 1
        return concept

    monkeypatch.setattr(build_concepts, "_add_concept", dying_add)

    with pytest.raises(RuntimeError, match="injected publication crash"):
        publication.upload_release_to_database(
            db, job.id, owner_sub=OWNER, lane="post")
    assert state["rows_visible_at_crash"] == 1, (
        "the crash must land AFTER the first row was written"
    )

    # Full rollback: the transaction discarded the flushed row, so NO
    # partial rows persist and the release is NOT marked uploaded.
    db.expire_all()
    assert _chapter_concepts(db, chapter.id) == []
    crashed_job = db.get(models.UploadJob, job.id)
    crashed_payload = release.release_payload(crashed_job)
    assert not (crashed_payload.get("summary") or {}).get(
        "database_uploaded"), (
        "a crashed publication must not latch database_uploaded"
    )
    assert crashed_job.result_ids == []
    # The staged payload itself is untouched: both rows still staged.
    assert [r["concept_title"] for r in crashed_payload["records"]] == [
        row["concept_title"] for row in staged_rows
    ]

    # What the reviewer will see published is what the export predicts for
    # this staged payload — captured after the crash, before the retry.
    _c, predicted, _r, _d = release_files.transient_release_hierarchy(
        db, crashed_job, payload=crashed_payload)
    expected = {(c.concept_title, c.machine_id) for c in predicted}
    assert len(expected) == 2 and all(mid for _t, mid in expected)

    # Retry after the crash: completes as a clean first publication.
    state["armed"] = False
    result = publication.upload_release_to_database(
        db, job.id, owner_sub=OWNER, lane="post")
    assert result["database_uploaded"] is True
    assert len(result["created_concept_ids"]) == 2
    assert result["updated_concept_ids"] == [], (
        "nothing survived the rollback, so the retry creates — there is "
        "no partial row for the S10 merge machinery to adopt"
    )

    # NO DUPLICATE concepts after crash + retry (non-negotiable), and the
    # published rows match the staged payload rows exactly: same titles,
    # the machine ids the export stamped for the reviewer.
    db.expire_all()
    persisted = _chapter_concepts(db, chapter.id)
    assert len(persisted) == 2
    assert {(c.concept_title, c.machine_id) for c in persisted} == expected
    assert len({c.machine_id for c in persisted}) == 2
    assert sorted(c.id for c in persisted) == sorted(
        result["created_concept_ids"])

    # The latch closed honestly and the receipt repeats idempotently.
    db.refresh(job)
    payload = release.release_payload(job)
    assert payload["summary"]["database_uploaded"] is True
    again = publication.upload_release_to_database(
        db, job.id, owner_sub=OWNER, lane="post")
    assert again["database_uploaded"] is True
    assert sorted(again["created_concept_ids"]) == sorted(
        result["created_concept_ids"])
    assert len(_chapter_concepts(db, chapter.id)) == 2, (
        "the latched repeat must not duplicate either"
    )

"""Step 9 — the review & edit surface over the STAGED release (§7).

The properties under test are product requirements: the view is a pure
projection of the release SLOT (the one authority); a manual edit is
applied verbatim as one recorded round; every applied round mints a new
``staged_release_uid`` + ``staged_version`` plus an immutable
ConceptReleaseVersion row; a stale uid or before-value is refused with a
conflict, never silently merged; a failed instruction round is itself a
recorded round (the reviewer's words are never lost with the outage) and
leaves the slot byte-untouched; and the edit trail is stripped before DB
publication like every other release-audit field.
"""
from __future__ import annotations

import copy
import io
from pathlib import Path

import pytest

from app import models
from app.services import build_concepts_release as release
from app.services import generation_recovery
from app.services import release_review as review
from app.services import release_workbook_edits


def _chapter(db):
    value = db.query(models.Chapter).order_by(models.Chapter.id).first()
    assert value is not None
    return value


def _records():
    return [
        {
            "topic": "Topic A",
            "parent_concept": "Parent A",
            "concept_title": "Released Concept Alpha",
            "concept_details": "Description: the first supported concept.",
            "keywords": "alpha",
        },
        {
            "topic": "Topic A",
            "parent_concept": "Parent A",
            "concept_title": "Released Concept Beta",
            "concept_details": "Description: the second supported concept.",
            "keywords": "beta",
        },
        {
            "topic": "Topic B",
            "parent_concept": "Parent B",
            "concept_title": "Released Concept Gamma",
            "concept_details": "Description: the later topic's concept.",
            "keywords": "gamma",
        },
    ]


def _staged_job(db):
    chapter = _chapter(db)
    job = models.UploadJob(
        module="build_concepts",
        upload_type="document",
        learning_kind="post",
        source_book="NCERT",
        filename="review-source.mmd",
        mmd_text="## Topic A\nA source paragraph.",
        status="converted",
        deposit_scope_type="chapter",
        deposit_scope_ids=[chapter.id],
        question_inventory={"items": [], "stats": {}, "mined_types": []},
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    release.stage_release(
        db, job, target_chapter_id=chapter.id, records=_records(),
    )
    db.refresh(job)
    return job


def _slot(job):
    return release.release_payload(job, lane=release.LANE_POST)


def _uid(job) -> str:
    return str(_slot(job).get(release.STAGED_RELEASE_UID_FIELD) or "")


def _version_rows(db, job):
    return list(
        db.query(models.ConceptReleaseVersion)
        .filter(models.ConceptReleaseVersion.job_id == job.id)
        .order_by(models.ConceptReleaseVersion.id.asc())
        .all()
    )


def _mark_non_resumable(db, job):
    inventory = dict(job.question_inventory or {})
    inventory[models.GENERATION_RECOVERY_INVENTORY_KEY] = {
        "resume_allowed": False,
        "recovery_action": "reconvert_new_upload",
        "recovery": "Start a new upload and conversion.",
    }
    job.question_inventory = inventory
    job.generation_checkpoint = {"stage": "source_graph_review"}
    db.commit()
    db.refresh(job)


# --------------------------------------------------------------------------- #
# The read-only projection
# --------------------------------------------------------------------------- #

def test_the_view_projects_the_staged_slot(db):
    job = _staged_job(db)
    view = review.review_view(db, job, "post")

    assert view["job_id"] == job.id
    assert view["lane"] == "post"
    assert view["staged_version"] == release.staged_version(_slot(job))
    assert view["staged_release_uid"] == _uid(job) != ""
    assert view["summary"]["row_count"] == 3
    assert view["summary"]["database_uploaded"] is False
    # Grouped by topic in record order, record_index global across topics.
    assert [t["topic"] for t in view["topics"]] == ["Topic A", "Topic B"]
    indices = [
        concept["record_index"]
        for topic in view["topics"]
        for concept in topic["concepts"]
    ]
    assert indices == [0, 1, 2]
    titles = [
        concept["concept_title"] for concept in view["topics"][0]["concepts"]
    ]
    assert titles == ["Released Concept Alpha", "Released Concept Beta"]
    # No round has run: the history is empty (staging writes no history).
    assert view["versions"] == []


def test_a_job_without_a_staged_release_is_unavailable(db):
    chapter = _chapter(db)
    job = models.UploadJob(
        module="build_concepts", learning_kind="post",
        filename="bare.mmd", status="converted",
        deposit_scope_type="chapter", deposit_scope_ids=[chapter.id],
    )
    db.add(job)
    db.commit()
    with pytest.raises(review.ReviewUnavailable):
        review.review_view(db, job, "post")


# --------------------------------------------------------------------------- #
# Manual edits — verbatim, recorded, conflict-checked
# --------------------------------------------------------------------------- #

def test_a_manual_edit_is_one_recorded_round(db):
    job = _staged_job(db)
    uid_before = _uid(job)
    version_before = release.staged_version(_slot(job))

    view = review.apply_manual_edits(
        db, job,
        lane="post",
        staged_release_uid=uid_before,
        edits=[{
            "record_index": 0,
            "field": "concept_title",
            "before": "Released Concept Alpha",
            "after": "Concept Alpha, corrected by the reviewer",
        }],
        owner_sub="local:default",
    )

    # The slot moved: new uid, next version, reviewer text verbatim.
    payload = _slot(job)
    assert _uid(job) != uid_before
    assert release.staged_version(payload) == version_before + 1
    row = payload["records"][0]
    assert row["concept_title"] == "Concept Alpha, corrected by the reviewer"
    assert "manual reviewer edit: concept_title replaced in place" in (
        row["review_flags"]
    )
    trail = row[review.MANUAL_EDIT_TRAIL_FIELD]
    assert trail and trail[0]["field"] == "concept_title"
    # The edited content is no longer what any earlier upload published.
    assert payload["summary"]["database_uploaded"] is False

    # The §7 record: the pre-edit staging is seeded, then the round.
    rows = _version_rows(db, job)
    assert [r.origin for r in rows] == ["staged", "manual_edit"]
    seed, round_row = rows
    assert seed.staged_release_uid == uid_before
    assert seed.payload["records"][0]["concept_title"] == (
        "Released Concept Alpha"
    ), "the seed snapshots the PRE-edit staging, never the working copy"
    assert round_row.staged_release_uid == _uid(job)
    assert round_row.parent_uid == uid_before
    assert round_row.operations == [{
        "record_index": 0,
        "field": "concept_title",
        "before": "Released Concept Alpha",
        "after": "Concept Alpha, corrected by the reviewer",
    }]
    # The returned view is the post-round projection.
    assert view["staged_release_uid"] == _uid(job)
    assert view["versions"][-1]["origin"] == "manual_edit"


def test_a_stale_uid_is_a_conflict_and_changes_nothing(db):
    job = _staged_job(db)
    before = _slot(job)
    with pytest.raises(review.ReviewConflict):
        review.apply_manual_edits(
            db, job,
            lane="post",
            staged_release_uid="not-the-current-uid",
            edits=[{
                "record_index": 0, "field": "keywords",
                "before": "alpha", "after": "alpha, edited",
            }],
        )
    assert _slot(job) == before
    assert _version_rows(db, job) == []


def test_a_stale_before_value_is_a_conflict_and_changes_nothing(db):
    job = _staged_job(db)
    before = _slot(job)
    with pytest.raises(review.ReviewConflict):
        review.apply_manual_edits(
            db, job,
            lane="post",
            staged_release_uid=_uid(job),
            edits=[{
                "record_index": 0, "field": "concept_title",
                "before": "A title the page never showed",
                "after": "Anything",
            }],
        )
    assert _slot(job) == before


def test_mechanically_invalid_edits_are_refused(db):
    job = _staged_job(db)
    uid = _uid(job)

    with pytest.raises(review.ReviewEditError):
        review.apply_manual_edits(
            db, job, lane="post", staged_release_uid=uid,
            edits=[{"record_index": 0, "field": "machine_id",
                    "before": "", "after": "MID-9"}],
        )
    with pytest.raises(review.ReviewEditError):
        review.apply_manual_edits(
            db, job, lane="post", staged_release_uid=uid,
            edits=[{"record_index": 99, "field": "keywords",
                    "before": "", "after": "x"}],
        )
    with pytest.raises(review.ReviewEditError):
        review.apply_manual_edits(
            db, job, lane="post", staged_release_uid=uid,
            edits=[{"record_index": 0, "field": "concept_title",
                    "before": "Released Concept Alpha", "after": "   "}],
        )
    assert _version_rows(db, job) == []


# --------------------------------------------------------------------------- #
# Instruction rounds — one bounded model pass, failures recorded
# --------------------------------------------------------------------------- #

def _scripted(response):
    calls = []

    def provider(**kwargs):
        calls.append(kwargs)
        if isinstance(response, Exception):
            raise response
        return response

    provider.calls = calls
    return provider


def test_an_instruction_round_applies_the_change_list(db):
    job = _staged_job(db)
    uid_before = _uid(job)
    provider = _scripted({
        "change_summary": "Clarified Beta; added the missed concept.",
        "changes": [{
            "record_id": "REC-0002",
            "field": "concept_details",
            "after": "Description: the second concept, clarified.",
            "reason": "the reviewer asked for a clearer description",
        }],
        "additions": [{
            "topic": "Topic B",
            "parent_concept": "Parent B",
            "concept_title": "The Missed Concept",
            "concept_details": "Description: generation missed this one.",
            "keywords": "missed",
            "reason": "the chapter teaches it explicitly",
        }],
    })

    view = review.apply_instruction_round(
        db, job,
        lane="post",
        staged_release_uid=uid_before,
        instruction="Clarify Beta's description and add the missed concept.",
        provider=provider,
    )

    payload = _slot(job)
    assert _uid(job) != uid_before
    assert payload["records"][1]["concept_details"] == (
        "Description: the second concept, clarified."
    )
    added = payload["records"][3]
    assert added["concept_title"] == "The Missed Concept"
    assert added[release.RELEASE_ROW_STATUS_FIELD] == "ready"
    assert any(
        flag.startswith("added by reviewer instruction")
        for flag in added["review_flags"]
    )
    # Row count moved with the addition; the summary is recomputed.
    assert payload["summary"]["row_count"] == 4
    rows = _version_rows(db, job)
    assert [r.origin for r in rows] == ["staged", "instruction"]
    assert rows[1].instruction.startswith("Clarify Beta's description")
    assert rows[1].diff["change_summary"] == (
        "Clarified Beta; added the missed concept."
    )
    # The bounded pass ran exactly once, against the REC-#### packet.
    assert len(provider.calls) == 1
    assert "REC-0003" in provider.calls[0]["prompt"]
    assert view["summary"]["row_count"] == 4


def test_a_failed_instruction_round_is_recorded_not_lost(db):
    job = _staged_job(db)
    before = _slot(job)

    with pytest.raises(review.InstructionRoundFailed):
        review.apply_instruction_round(
            db, job,
            lane="post",
            staged_release_uid=_uid(job),
            instruction="Rename every concept to its chapter wording.",
            provider=_scripted(RuntimeError("provider down")),
        )

    # The slot is byte-untouched; the reviewer's words are on the record.
    assert _slot(job) == before
    rows = _version_rows(db, job)
    assert [r.origin for r in rows] == ["staged", "instruction_failed"]
    failed = rows[1]
    assert failed.instruction == (
        "Rename every concept to its chapter wording."
    )
    assert "provider down" in failed.diff["error"]
    assert failed.payload is None
    assert failed.staged_release_uid == _uid(job)


def test_a_model_naming_an_unknown_target_applies_nothing(db):
    job = _staged_job(db)
    before = _slot(job)
    with pytest.raises(review.InstructionRoundFailed):
        review.apply_instruction_round(
            db, job,
            lane="post",
            staged_release_uid=_uid(job),
            instruction="Fix the ninth concept.",
            provider=_scripted({
                "change_summary": "…",
                "changes": [{
                    "record_id": "REC-9999", "field": "keywords",
                    "after": "x", "reason": "",
                }],
                "additions": [],
            }),
        )
    assert _slot(job) == before
    assert [r.origin for r in _version_rows(db, job)] == [
        "staged", "instruction_failed",
    ]


def test_an_empty_change_list_is_a_failed_round(db):
    job = _staged_job(db)
    with pytest.raises(review.InstructionRoundFailed):
        review.apply_instruction_round(
            db, job,
            lane="post",
            staged_release_uid=_uid(job),
            instruction="Do something unstated.",
            provider=_scripted({
                "change_summary": "", "changes": [], "additions": [],
            }),
        )
    assert [r.origin for r in _version_rows(db, job)] == [
        "staged", "instruction_failed",
    ]


# --------------------------------------------------------------------------- #
# Publication contract: the trail is a release-audit field
# --------------------------------------------------------------------------- #

def test_the_edit_trail_is_stripped_before_db_publication():
    assert review.MANUAL_EDIT_TRAIL_FIELD in release._RELEASE_AUDIT_FIELDS
    stripped = release._strip_release_fields({
        "concept_title": "Kept",
        review.MANUAL_EDIT_TRAIL_FIELD: [{"field": "keywords"}],
    })
    assert stripped == {"concept_title": "Kept"}


def test_an_edited_release_still_renders_and_publishes_shapes(db):
    """The round's output stays a valid staged release: the projection,
    the strip, and the summary all keep working on the edited payload."""
    job = _staged_job(db)
    review.apply_manual_edits(
        db, job, lane="post", staged_release_uid=_uid(job),
        edits=[{
            "record_index": 2, "field": "keywords",
            "before": "gamma", "after": "gamma, revised",
        }],
    )
    payload = _slot(job)
    row = payload["records"][2]
    assert row["keywords"] == "gamma, revised"
    published_shape = release._strip_release_fields(row)
    assert review.MANUAL_EDIT_TRAIL_FIELD not in published_shape
    assert published_shape["keywords"] == "gamma, revised"
    # A second round chains on the first (uid lineage, version + 1).
    uid_two = _uid(job)
    review.apply_manual_edits(
        db, job, lane="post", staged_release_uid=uid_two,
        edits=[{
            "record_index": 2, "field": "keywords",
            "before": "gamma, revised", "after": "gamma, final",
        }],
    )
    rows = _version_rows(db, job)
    assert [r.origin for r in rows] == [
        "staged", "manual_edit", "manual_edit",
    ]
    assert rows[2].parent_uid == uid_two
    assert rows[2].version == rows[1].version + 1


# --------------------------------------------------------------------------- #
# The HTTP surface (the contract the review page is built against)
# --------------------------------------------------------------------------- #

def test_the_review_routes_serve_the_contract(client, db, monkeypatch):
    job = _staged_job(db)

    view = client.get(
        f"/build-concepts/uploads/{job.id}/release-review", params={"lane": "post"}
    )
    assert view.status_code == 200, view.text
    body = view.json()
    assert body["job_id"] == job.id
    uid = body["staged_release_uid"]
    assert uid

    # A stale uid is a 409, not a silent overwrite.
    stale = client.post(
        f"/build-concepts/uploads/{job.id}/release-review/manual-edit",
        json={
            "lane": "post", "staged_release_uid": "stale",
            "edits": [{"record_index": 0, "field": "keywords",
                       "before": "alpha", "after": "alpha, edited"}],
        },
    )
    assert stale.status_code == 409

    edited = client.post(
        f"/build-concepts/uploads/{job.id}/release-review/manual-edit",
        json={
            "lane": "post", "staged_release_uid": uid,
            "edits": [{"record_index": 0, "field": "keywords",
                       "before": "alpha", "after": "alpha, edited"}],
        },
    )
    assert edited.status_code == 200, edited.text
    edited_body = edited.json()
    assert edited_body["staged_release_uid"] != uid
    assert edited_body["topics"][0]["concepts"][0]["keywords"] == (
        "alpha, edited"
    )
    assert [v["origin"] for v in edited_body["versions"]] == [
        "staged", "manual_edit",
    ]

    # An invalid field is a 422 (mechanically invalid, not a conflict).
    bad_field = client.post(
        f"/build-concepts/uploads/{job.id}/release-review/manual-edit",
        json={
            "lane": "post",
            "staged_release_uid": edited_body["staged_release_uid"],
            "edits": [{"record_index": 0, "field": "machine_id",
                       "before": "", "after": "MID-9"}],
        },
    )
    assert bad_field.status_code == 422

    # A failed instruction pass is a 502 whose round is already recorded.
    monkeypatch.setattr(
        "app.services.release_review._default_provider",
        _scripted(RuntimeError("provider down")),
    )
    failed = client.post(
        f"/build-concepts/uploads/{job.id}/release-review/apply-instruction",
        json={
            "lane": "post",
            "staged_release_uid": edited_body["staged_release_uid"],
            "instruction": "Clarify the second concept.",
        },
    )
    assert failed.status_code == 502
    assert "instruction pass failed" in failed.json()["detail"]

    # A successful pass returns the post-round view.
    monkeypatch.setattr(
        "app.services.release_review._default_provider",
        _scripted({
            "change_summary": "Clarified.",
            "changes": [{
                "record_id": "REC-0002", "field": "concept_details",
                "after": "Description: clarified over HTTP.",
                "reason": "requested",
            }],
            "additions": [],
        }),
    )
    applied = client.post(
        f"/build-concepts/uploads/{job.id}/release-review/apply-instruction",
        json={
            "lane": "post",
            "staged_release_uid": edited_body["staged_release_uid"],
            "instruction": "Clarify the second concept.",
        },
    )
    assert applied.status_code == 200, applied.text
    applied_body = applied.json()
    assert applied_body["topics"][0]["concepts"][1]["concept_details"] == (
        "Description: clarified over HTTP."
    )
    assert [v["origin"] for v in applied_body["versions"]] == [
        "staged", "manual_edit", "instruction_failed", "instruction",
    ]


def test_the_review_routes_answer_404_when_nothing_is_staged(client, db):
    chapter = _chapter(db)
    job = models.UploadJob(
        module="build_concepts", learning_kind="post",
        filename="bare.mmd", status="converted",
        deposit_scope_type="chapter", deposit_scope_ids=[chapter.id],
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    resp = client.get(f"/build-concepts/uploads/{job.id}/release-review")
    assert resp.status_code == 404
    missing = client.get("/build-concepts/uploads/99999/release-review")
    assert missing.status_code == 404


def test_non_resumable_release_stays_readable_but_cannot_be_edited_or_uploaded(
    client, db, monkeypatch,
):
    job = _staged_job(db)
    _mark_non_resumable(db, job)
    original_payload = copy.deepcopy(_slot(job))
    original_versions = len(_version_rows(db, job))
    uid = _uid(job)

    # Diagnostic review remains a read-only surface.
    viewed = client.get(
        f"/build-concepts/uploads/{job.id}/release-review",
        params={"lane": "post"},
    )
    assert viewed.status_code == 200, viewed.text
    assert viewed.json()["staged_release_uid"] == uid

    def forbidden_provider(*_args, **_kwargs):
        raise AssertionError("blocked instruction reached the provider")

    with pytest.raises(generation_recovery.NonResumableRunError):
        review.apply_instruction_round(
            db,
            job,
            lane="post",
            staged_release_uid=uid,
            instruction="Change the first row.",
            provider=forbidden_provider,
        )
    with pytest.raises(generation_recovery.NonResumableRunError):
        review.apply_manual_edits(
            db,
            job,
            lane="post",
            staged_release_uid=uid,
            edits=[{
                "record_index": 0,
                "field": "keywords",
                "before": "alpha",
                "after": "changed",
            }],
        )
    with pytest.raises(generation_recovery.NonResumableRunError):
        release_workbook_edits.apply_workbook_and_publish(
            db,
            job,
            lane="post",
            workbook_path=Path("file-must-not-be-read.xlsx"),
        )

    def forbidden_manual(*_args, **_kwargs):
        raise AssertionError("blocked manual edit called the service")

    def forbidden_instruction(*_args, **_kwargs):
        raise AssertionError("blocked instruction recorded a review round")

    monkeypatch.setattr(review, "apply_manual_edits", forbidden_manual)
    monkeypatch.setattr(review, "apply_instruction_round", forbidden_instruction)
    manual = client.post(
        f"/build-concepts/uploads/{job.id}/release-review/manual-edit",
        json={
            "lane": "post",
            "staged_release_uid": uid,
            "edits": [{
                "record_index": 0,
                "field": "keywords",
                "before": "alpha",
                "after": "changed",
            }],
        },
    )
    assert manual.status_code == 409
    instruction = client.post(
        f"/build-concepts/uploads/{job.id}/release-review/apply-instruction",
        json={
            "lane": "post",
            "staged_release_uid": uid,
            "instruction": "Change the first row.",
        },
    )
    assert instruction.status_code == 409

    async def forbidden_read(*_args, **_kwargs):
        raise AssertionError("blocked workbook upload read the request body")

    monkeypatch.setattr(
        "app.api.build_concepts.read_limited_upload", forbidden_read
    )
    workbook = client.post(
        f"/build-concepts/uploads/{job.id}/upload-edited-workbook",
        params={"lane": "post"},
        files={"file": ("edited.xlsx", io.BytesIO(b"not read"), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert workbook.status_code == 409

    db.refresh(job)
    assert _slot(job) == original_payload
    assert len(_version_rows(db, job)) == original_versions
    assert job.generation_recovery["resume_allowed"] is False


def test_ordinary_edited_workbook_route_still_reads_and_dispatches(
    client, db, monkeypatch,
):
    job = _staged_job(db)
    dispatched: list[tuple[int, str]] = []

    def capture(_db, received_job, *, lane, workbook_path, owner_sub):
        assert workbook_path.read_bytes() == b"ordinary workbook"
        dispatched.append((received_job.id, lane))
        return {"job_id": received_job.id, "publication": "staged"}

    monkeypatch.setattr(
        release_workbook_edits, "apply_workbook_and_publish", capture
    )
    response = client.post(
        f"/build-concepts/uploads/{job.id}/upload-edited-workbook",
        params={"lane": "post"},
        files={"file": ("edited.xlsx", io.BytesIO(b"ordinary workbook"), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert response.status_code == 200, response.text
    assert dispatched == [(job.id, "post")]

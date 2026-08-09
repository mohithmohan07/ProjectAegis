from __future__ import annotations

import copy
import io
import json
import zipfile

from openpyxl import load_workbook

from app import models
from app.services import build_concepts
from app.services import build_concepts_release as release
from app.services import build_concepts_release_contract as release_contract
from app.services import build_concepts_release_files as release_files
from app.services import build_concepts_release_manifest
from app.services import build_concepts_release_publication as publication


def _chapter(db):
    value = db.query(models.Chapter).order_by(models.Chapter.id).first()
    assert value is not None
    return value


def _job(db, *, checkpoint=None):
    chapter = _chapter(db)
    job = models.UploadJob(
        module="build_concepts",
        upload_type="document",
        learning_kind="post",
        source_book="NCERT",
        filename="release-source.mmd",
        mmd_text="## Topic A\nA source paragraph.",
        status="converted",
        deposit_scope_type="chapter",
        deposit_scope_ids=[chapter.id],
        generation_checkpoint=copy.deepcopy(checkpoint or {}),
        question_inventory={"items": [], "stats": {}, "mined_types": []},
        generation_log=[{
            "type": "log",
            "level": "error",
            "message": "TOPOLOGY-CONCEPT-0001 disagreed at BLK-0007",
            "ts": 1.0,
        }],
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job, chapter


def _records():
    return [{
        "topic": "Topic A",
        "parent_concept": "Parent A",
        "concept_title": "Released Concept Alpha",
        "concept_details": "Description: Supported core with one disputed clause.",
        "keywords": "alpha",
        "_semantic_topic_id": "TOPIC-A",
        "_phase32_origin_concept_id": "TOPOLOGY-CONCEPT-0001",
        "_source_block_ids": ["BLK-0007"],
    }]


def _inventory():
    return {
        "items": [
            {"qid": "QINV-0001", "raw_task": "Explain the first method."},
            {"qid": "QINV-0002", "raw_task": "Apply the later method."},
        ],
        "stats": {"items": 2},
    }


def _mined_types(*, duplicate=False):
    second_qid = "QINV-0001" if duplicate else "QINV-0002"
    return {
        "types": [{
            "type_id": "TYPE-0001",
            "type_title": "Evidence-based explanation",
            "type_definition": (
                "Explain a source relationship by connecting evidence to the "
                "required historical framework."
            ),
            "owner_topic_ids": ["TOPIC-A", "TOPIC-B"],
            "case_prompts": [
                {
                    "case_id": "CASE-0001",
                    "case_definition": "Explain the foundational relationship.",
                    "owner_topic_ids": ["TOPIC-A"],
                    "source_question_ids": ["QINV-0001"],
                    "examples": [{
                        "source_question_id": "QINV-0001",
                        "prompt": "Explain the first method.",
                    }],
                },
                {
                    "case_id": "CASE-0002",
                    "case_definition": (
                        "Apply the same reusable method after the later topic "
                        "becomes necessary."
                    ),
                    "owner_topic_ids": ["TOPIC-B"],
                    "source_question_ids": [second_qid],
                    "examples": [{
                        "source_question_id": second_qid,
                        "prompt": "Apply the later method.",
                    }],
                },
            ],
        }],
    }


def _pending():
    return {
        "decision_id": "phase32-release-test",
        "context_hash": "a" * 64,
        "kind": "phase32_concept_blueprint_semantic_conflict",
        "phase": "3.2",
        "conflict": "The concept may require the later topic framework.",
        "diagnosis": "Autonomous review could not certify one topology action.",
        "decision_question": "Move, refine or split?",
        "item": {
            "unit_id": "TOPOLOGY-CONCEPT-0001",
            "topic": "Topic A",
            "qids": ["QINV-0001"],
        },
        "candidates": [{
            "target_id": "TOPOLOGY-CONCEPT-0001",
            "title": "Released Concept Alpha",
            "topic": "Topic A",
            "source_block_ids": ["BLK-0007"],
        }],
        "evidence": [{
            "evidence_id": "BLK-0007",
            "label": "Source block",
            "text": "The relevant source relationship.",
            "page": "9",
        }],
        "options": [],
    }


def test_one_type_can_route_cases_to_different_topics_without_qid_duplication():
    rows, issues, routes = release.audit_type_cases(
        _mined_types(), _inventory()
    )

    assert [row["row_kind"] for row in rows] == [
        "type", "case", "example", "case", "example"
    ]
    assert rows[1]["owner_topic_ids"] == ["TOPIC-A"]
    assert rows[3]["owner_topic_ids"] == ["TOPIC-B"]
    assert rows[2]["example_qid"] == "QINV-0001"
    assert rows[4]["example_qid"] == "QINV-0002"
    assert routes["QINV-0001"][0]["case_id"] == "CASE-0001"
    assert routes["QINV-0002"][0]["case_id"] == "CASE-0002"
    assert not [issue for issue in issues if issue["severity"] == "error"]


def test_duplicate_qid_is_released_as_an_audit_error():
    _rows, issues, _routes = release.audit_type_cases(
        _mined_types(duplicate=True), _inventory()
    )
    codes = [issue["code"] for issue in issues]
    assert "duplicate_qid_assignment" in codes
    assert "unassigned_inventory_qid" in codes


def test_stage_release_clears_manual_pause_and_highlights_the_affected_row(db):
    job, chapter = _job(db)

    result = release.stage_release(
        db,
        job,
        target_chapter_id=chapter.id,
        records=_records(),
        inventory=_inventory(),
        mined_types=_mined_types(),
        pending_decision=_pending(),
        reason="Release the newest durable rows instead of asking the user.",
    )

    db.refresh(job)
    payload = release.release_payload(job)
    assert payload is not None
    assert result["status"] == "released"
    assert result["database_uploaded"] is False
    assert job.status == "released"
    assert job.result_ids == []
    assert job.pending_decision is None
    assert payload["records"][0][release.RELEASE_ROW_STATUS_FIELD] == (
        "released_with_errors"
    )
    assert payload["records"][0][release.RELEASE_ROW_ERRORS_FIELD]
    assert payload["issues"][0]["unit_id"] == "TOPOLOGY-CONCEPT-0001"
    assert payload["issues"][0]["block_ids"] == ["BLK-0007"]


def test_release_workbook_orders_type_case_example_and_marks_errors(db):
    job, chapter = _job(db)
    release.stage_release(
        db,
        job,
        target_chapter_id=chapter.id,
        records=_records(),
        inventory=_inventory(),
        mined_types=_mined_types(),
        pending_decision=_pending(),
    )

    workbook = load_workbook(io.BytesIO(release_files.build_release_workbook(job)))
    assert workbook.sheetnames == [
        "Released Concepts",
        "Type Case Routing",
        "Release Issues",
        "Release Manifest",
    ]
    concepts = workbook["Released Concepts"]
    assert concepts.cell(1, 2).value == "Release Status"
    assert concepts.cell(1, 3).value == "Errors / Warnings"
    assert concepts.cell(2, 2).value == "released_with_errors"
    assert concepts.cell(2, 1).fill.fgColor.rgb[-6:] == "F4CCCC"

    routes = workbook["Type Case Routing"]
    assert [routes.cell(row, 1).value for row in range(2, 7)] == [
        "type", "case", "example", "case", "example"
    ]
    assert routes.cell(3, 6).value == "Explain the foundational relationship."
    assert routes.cell(4, 9).value == "QINV-0001"


def test_diagnostics_archive_keeps_log_checkpoint_source_evidence_and_blks(db):
    checkpoint = {
        "checkpoints": [{
            "stage": "pre_type_assignment",
            "progress": 0.81,
            "records": _records(),
        }]
    }
    job, chapter = _job(db, checkpoint=checkpoint)
    release.stage_release(
        db,
        job,
        target_chapter_id=chapter.id,
        records=_records(),
        inventory=_inventory(),
        mined_types=_mined_types(),
        pending_decision=_pending(),
        checkpoint=checkpoint,
    )

    with zipfile.ZipFile(io.BytesIO(release_files.build_diagnostics_zip(job))) as archive:
        names = set(archive.namelist())
        assert "release/released_concepts.xlsx" in names
        assert "release/release_payload.json" in names
        assert "context/generation_log.json" in names
        assert "context/generation_checkpoint.json" in names
        assert "context/question_inventory.json" in names
        assert "context/source_evidence.json" in names
        assert "context/blks.json" in names
        assert "source/converted_source.mmd" in names
        blocks = json.loads(archive.read("context/blks.json"))
        assert any(
            row.get("block_id") == "BLK-0007"
            for row in blocks["blocks"]
        )


def test_post_capture_failure_releases_captured_rows_not_an_empty_checkpoint(db):
    job, chapter = _job(db)

    def fails_after_capture(_db, _job_id, _chapter_id, **_kwargs):
        release_contract._RELEASE_CAPTURE.set({
            "records": _records(),
            "inventory": _inventory(),
            "mined_types": _mined_types(),
            "final_grounding_certificate": {"version": "test"},
            "checkpoint": {},
        })
        raise RuntimeError("late publication preparation failure")

    wrapped = release_contract._wrap_generation(fails_after_capture)
    result = wrapped(db, job.id, chapter.id)

    payload = release.release_payload(job)
    assert result["row_count"] == 1
    assert payload is not None
    assert payload["records"][0]["concept_title"] == "Released Concept Alpha"
    assert any(
        issue["code"] == "RuntimeError" for issue in payload["issues"]
    )


def _validated_cache_rows():
    return [
        {
            "topic": "Topic A",
            "parent_concept": "Parent A",
            "concept_title": "Validated Cached Concept",
            "concept_details": (
                "Description: The complete validated claim.\n"
                "Achieving Mastery: Explain it. // Misconception/ Error "
                "Analysis: Misconceptions: A learner merges two ideas.; "
                "Error Analysis: The learner treats them as one step."
            ),
            "keywords": "validated",
            "_semantic_topic_id": "TOPIC-A",
            "_semantic_graph_contract": "CONTRACT-1",
            "_source_block_ids": ["BLK-0007"],
        },
        {
            "topic": "Topic A",
            "parent_concept": "Parent A",
            "concept_title": "Culmination - Topic A",
            "concept_details": "Description: Recap of the validated claim.",
            "keywords": "",
            "_semantic_topic_id": "TOPIC-A",
            "_semantic_graph_contract": "CONTRACT-1",
            "_source_block_ids": ["BLK-0007"],
        },
    ]


def _write_validated_cache(tmp_path, rows):
    from app.services import canonical_source_phase3 as phase3

    (tmp_path / release._FINAL_TOPOLOGY_ARTIFACT).write_text(
        json.dumps({
            "records": rows,
            "records_sha256": phase3._sha256_json(rows),
            "source_contract_hash": "CONTRACT-1",
        }),
        encoding="utf-8",
    )


def test_failure_release_prefers_validated_topology_cache(
    db, tmp_path, monkeypatch,
):
    """Job 23 released 51 stale checkpoint rows without learner analysis
    while a validated topology with complete learner analysis sat cached."""
    job, chapter = _job(db)
    stale = [{
        "topic": "Topic A",
        "parent_concept": "Parent A",
        "concept_title": "Stale Checkpoint Concept",
        "concept_details": "Description: No learner analysis yet.",
        "keywords": "stale",
        "_semantic_topic_id": "TOPIC-A",
        "_semantic_graph_contract": "CONTRACT-1",
        "_source_block_ids": ["BLK-0007"],
    }]
    monkeypatch.setattr(
        release.generation,
        "_newest_compatible_concept_checkpoint",
        lambda _envelope: {"records": copy.deepcopy(stale)},
    )
    _write_validated_cache(tmp_path, _validated_cache_rows())
    monkeypatch.setattr(
        release.uploads,
        "source_artifact_directory",
        lambda _job_id: str(tmp_path),
        raising=False,
    )

    result = release.stage_release(
        db,
        job,
        target_chapter_id=chapter.id,
        error=RuntimeError("host mutation contract failed closed"),
        reason="Generation failed after creating a durable checkpoint.",
    )

    payload = release.release_payload(job)
    assert result["status"] == release.RELEASE_STATUS
    titles = [row["concept_title"] for row in payload["records"]]
    assert "Validated Cached Concept" in titles
    assert "Stale Checkpoint Concept" not in titles
    assert any(
        issue["code"] == "release_rows_upgraded_from_validated_cache"
        for issue in payload["issues"]
    )


def test_captured_final_rows_are_never_overridden_by_cache(
    db, tmp_path, monkeypatch,
):
    job, chapter = _job(db)
    _write_validated_cache(tmp_path, _validated_cache_rows())
    monkeypatch.setattr(
        release.uploads,
        "source_artifact_directory",
        lambda _job_id: str(tmp_path),
        raising=False,
    )

    release.stage_release(
        db,
        job,
        target_chapter_id=chapter.id,
        records=_records(),
        inventory=_inventory(),
        mined_types=_mined_types(),
        reason="Generation completed.",
    )

    payload = release.release_payload(job)
    titles = [row["concept_title"] for row in payload["records"]]
    assert titles == ["Released Concept Alpha"]


def test_cache_from_a_different_source_contract_is_ignored(
    db, tmp_path, monkeypatch,
):
    job, chapter = _job(db)
    stale = [{
        "topic": "Topic A",
        "parent_concept": "Parent A",
        "concept_title": "Stale Checkpoint Concept",
        "concept_details": "Description: No learner analysis yet.",
        "keywords": "stale",
        "_semantic_topic_id": "TOPIC-A",
        "_semantic_graph_contract": "CONTRACT-2",
        "_source_block_ids": ["BLK-0007"],
    }]
    monkeypatch.setattr(
        release.generation,
        "_newest_compatible_concept_checkpoint",
        lambda _envelope: {"records": copy.deepcopy(stale)},
    )
    _write_validated_cache(tmp_path, _validated_cache_rows())
    monkeypatch.setattr(
        release.uploads,
        "source_artifact_directory",
        lambda _job_id: str(tmp_path),
        raising=False,
    )

    release.stage_release(
        db,
        job,
        target_chapter_id=chapter.id,
        error=RuntimeError("host mutation contract failed closed"),
        reason="Generation failed after creating a durable checkpoint.",
    )

    payload = release.release_payload(job)
    titles = [row["concept_title"] for row in payload["records"]]
    assert titles == ["Stale Checkpoint Concept"]


def test_release_routes_and_manual_decision_endpoint_are_unattended(client, db):
    build_concepts_release_manifest.install()
    release_contract.install()
    job, chapter = _job(db)
    release.stage_release(
        db,
        job,
        target_chapter_id=chapter.id,
        records=_records(),
        inventory=_inventory(),
        mined_types=_mined_types(),
        pending_decision=_pending(),
    )

    response = client.get(f"/build-concepts/uploads/{job.id}")
    assert response.status_code == 200
    body = response.json()
    assert body["awaiting_decision"] is False
    assert body["pending_decision"] is None
    kinds = {row["kind"] for row in body["source_artifacts"]["files"]}
    assert {
        "released_concepts",
        "release_diagnostics",
        "release_payload",
        "database_upload",
    } <= kinds

    assert client.get(
        f"/build-concepts/uploads/{job.id}/release.xlsx"
    ).status_code == 200
    assert client.get(
        f"/build-concepts/uploads/{job.id}/diagnostics.zip"
    ).status_code == 200
    assert client.get(
        f"/build-concepts/uploads/{job.id}/release.json"
    ).status_code == 200

    manual = client.post(
        f"/build-concepts/uploads/{job.id}/decisions/phase32-release-test",
        json={"choice": "accept_recommended"},
    )
    assert manual.status_code == 409
    assert "unattended" in manual.json()["detail"].lower()


def test_explicit_upload_publishes_flagged_rows_and_is_idempotent(db, monkeypatch):
    job, chapter = _job(db)
    release.stage_release(
        db,
        job,
        target_chapter_id=chapter.id,
        records=_records(),
        inventory=_inventory(),
        mined_types=_mined_types(),
        pending_decision=_pending(),
    )
    assert db.query(models.Concept).filter(
        models.Concept.concept_title == "Released Concept Alpha"
    ).count() == 0

    def commit_only(database, _path, ids):
        database.commit()
        return {
            "written": len(ids),
            "sources_updated": 0,
            "publication_status": "published",
        }

    monkeypatch.setattr(
        build_concepts,
        "_commit_and_publish_concept_workbook",
        commit_only,
    )
    first = publication.upload_release_to_database(db, job.id)
    second = publication.upload_release_to_database(db, job.id)

    assert first["database_uploaded"] is True
    assert first["created_concept_ids"]
    assert second["database_uploaded"] is True
    assert db.query(models.Concept).filter(
        models.Concept.concept_title == "Released Concept Alpha"
    ).count() == 1
    db.refresh(job)
    assert job.status == "generated"
    assert release.release_payload(job)["summary"]["database_uploaded"] is True


# --------------------------------------------------------------------------- #
# No generation outcome reaches the user as a decision
# --------------------------------------------------------------------------- #
#
# The 81%/89% selection screen was the last place a run could stop and wait.
# These pin every way generation can end badly, and prove each one produces a
# release instead of a question.

def _run_wrapped(db, job, chapter, outcome):
    """Drive the installed release wrapper around one generation outcome."""

    release_contract.install()

    def original(_db, _job_id, _target_chapter_id, **_kwargs):
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    return release_contract._run_generation_release(
        original,
        db,
        job.id,
        chapter.id,
        owner_sub=None,
    )


def test_an_unattended_decision_stop_becomes_a_release(db):
    """The stop that fired when only human-only actions remained."""
    job, chapter = _job(db)

    result = _run_wrapped(
        db, job, chapter,
        build_concepts.UnattendedDecisionUnavailable(
            "Unattended generation stopped: the saved decision offers only "
            "actions that a person must take (replace_source)."
        ),
    )

    assert result["status"] == release.RELEASE_STATUS
    db.refresh(job)
    assert job.status == release.RELEASE_STATUS
    payload = release.release_payload(job)
    # The issue is coded by the exception class, so the export names the
    # exact stop that was converted rather than a generic "it failed".
    codes = {issue["code"] for issue in payload["issues"]}
    assert "UnattendedDecisionUnavailable" in codes
    # The reason survives so the export explains why the run ended where it did.
    assert any(
        "replace_source" in str(issue.get("message", ""))
        + str(issue.get("details", ""))
        for issue in payload["issues"]
    )


def test_a_returned_pending_decision_becomes_a_release(db):
    """Generation answering with awaiting_decision must not reach the UI."""
    job, chapter = _job(db)

    result = _run_wrapped(db, job, chapter, {
        "job_id": job.id,
        "status": "awaiting_decision",
        "pending_decision": _pending(),
        "resume_required": False,
    })

    assert result["status"] == release.RELEASE_STATUS
    assert "pending_decision" not in result
    db.refresh(job)
    assert build_concepts._pending_human_decision(job.generation_checkpoint) is None
    payload = release.release_payload(job)
    # The issue is not discarded -- it travels with the release.
    assert payload["pending_decision_snapshot"]
    # The decision's own kind is the issue code, so the export says which
    # semantic question went unanswered.
    assert any(
        issue["code"] == _pending()["kind"] for issue in payload["issues"]
    )


def test_an_ordinary_generation_failure_becomes_a_release(db):
    job, chapter = _job(db)

    result = _run_wrapped(
        db, job, chapter, RuntimeError("phase 3.3 contract is unavailable"),
    )

    assert result["status"] == release.RELEASE_STATUS
    payload = release.release_payload(job)
    assert any(
        "phase 3.3 contract is unavailable" in str(issue.get("message", ""))
        for issue in payload["issues"]
    )

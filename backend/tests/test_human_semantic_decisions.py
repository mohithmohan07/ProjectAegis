import copy
import json
import threading

import pytest

from app import models
from app.db import SessionLocal
from app.services import (
    auth,
    build_concepts,
    canonical_source_phase33_preflight_contract as phase33,
    checkpoints,
    generation,
    semantic_recovery,
    uploads,
)


def _pending_packet() -> dict:
    context_hash = "a" * 64
    return {
        "decision_id": f"phase33-host-{context_hash[:24]}",
        "context_hash": context_hash,
        "kind": "phase33_type_host_semantic_conflict",
        "phase": "3.3",
        "conflict": (
            "The existing Renan concept covers attributes but not why "
            "nations safeguard liberty."
        ),
        "item": {
            "unit_id": "TYPE-0002",
            "type_id": "TYPE-0002",
            "type_title": "Explain Renan's View of Nations",
            "qids": ["QINV-0002"],
            "questions": [
                "Why, in Renan's view, are nations important?",
            ],
            "topic": {
                "topic_id": "TOPIC-0001",
                "title": "The French Revolution and the Idea of the Nation",
            },
        },
        "candidates": [{
            "concept_id": "HOST-CONCEPT-0001",
            "title": "Ernest Renan's Idea of a Nation",
            "topic": "The French Revolution and the Idea of the Nation",
            "coverage": "Attributes of nationhood.",
            "gap": "Nations as guarantees of liberty.",
        }],
        "evidence": [{
            "page": 8,
            "label": "EVIDENCE-PAGE-C",
            "text": "Their existence is a guarantee of liberty.",
        }],
        "deferred_assignment_unit_ids": ["TYPE-0003"],
        "options": [
            {
                "choice": "expand_existing",
                "label": "Expand the existing concept",
                "recommended": True,
                "target_concept_id": "HOST-CONCEPT-0001",
                "directed_update": {"preserve_concept_identity": True},
            },
            {
                "choice": "create_new",
                "label": "Create a separate concept",
                "recommended": False,
            },
            {
                "choice": "select_existing",
                "label": "Select another existing concept",
                "recommended": False,
            },
            {
                "choice": "custom",
                "label": "Give a custom instruction",
                "recommended": False,
            },
        ],
    }


def _job_at_81_percent(db, first_chapter, *, learning_kind="post"):
    chapter = db.get(models.Chapter, first_chapter["id"])
    job = models.UploadJob(
        owner_sub=auth.LOCAL_OWNER_SUB,
        module="build_concepts",
        upload_type="document",
        learning_kind=learning_kind,
        filename="rne.mmd",
        mmd_text="# Renan\nNations are a guarantee of liberty.",
        status="converted",
        question_inventory={"items": [], "stats": {}, "mined_types": []},
    )
    db.add(job)
    db.flush()
    stage = generation._make_concept_checkpoint(
        "pre_type_assignment",
        records=[{
            "topic": "The French Revolution",
            "parent_concept": "Nationalism",
            "concept_title": "Ernest Renan's Idea of a Nation",
            "concept_details": "Description: Attributes of a nation.",
            "keywords": "Renan, nation",
        }],
        question_task_inventory={"items": [], "stats": {}},
        mined_types={"types": []},
        method_row_snapshot=[],
    )
    job.generation_checkpoint = (
        build_concepts._merge_generation_checkpoint_history(
            {},
            stage,
            fingerprint=build_concepts._generation_checkpoint_fingerprint(
                job, chapter),
            target_identity=build_concepts._generation_target_identity(chapter),
            target_chapter_id=chapter.id,
        )
    )
    db.commit()
    db.refresh(job)
    return job, chapter


def _attach_pending(db, job, chapter):
    fingerprint = job.generation_checkpoint["fingerprint"]
    return build_concepts._persist_pending_human_decision(
        db,
        job,
        _pending_packet(),
        fingerprint=fingerprint,
        target_chapter_id=chapter.id,
        owner_sub=auth.LOCAL_OWNER_SUB,
    )


def test_generation_pause_preserves_81_percent_checkpoint_and_skips_reentry(
    db,
    first_chapter,
    monkeypatch,
):
    job, chapter = _job_at_81_percent(db, first_chapter)
    calls = 0

    def require_human(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise semantic_recovery.HumanDecisionRequired(_pending_packet())

    monkeypatch.setattr(
        build_concepts.config, "use_live_generation", lambda: True)
    monkeypatch.setattr(
        build_concepts.generation, "concepts_from_mmd", require_human)
    monkeypatch.setattr(
        build_concepts.drive_checkpoints,
        "schedule_checkpoint_backup",
        lambda *_args, **_kwargs: None,
    )

    result = build_concepts.generate_post_learning(
        db, job.id, chapter.id, owner_sub=auth.LOCAL_OWNER_SUB)

    assert result["status"] == "awaiting_decision"
    assert result["pending_decision"]["decision_id"].startswith("phase33-host-")
    assert result["pending_decision"]["options"][-1]["choice"] == (
        "custom_instruction")
    assert calls == 1
    db.refresh(job)
    assert job.status == "converted"
    assert job.generation_checkpoint["stage"] == "pre_type_assignment"
    assert job.checkpoint_progress == pytest.approx(0.81)
    assert job.awaiting_decision is True

    repeated = build_concepts.generate_post_learning(
        db, job.id, chapter.id, owner_sub=auth.LOCAL_OWNER_SUB)
    assert repeated["status"] == "awaiting_decision"
    assert calls == 1


def test_decision_submission_is_owner_scoped_one_time_and_api_free(
    client,
    db,
    first_chapter,
    monkeypatch,
):
    job, chapter = _job_at_81_percent(db, first_chapter)
    monkeypatch.setattr(
        build_concepts.drive_checkpoints,
        "schedule_checkpoint_backup",
        lambda *_args, **_kwargs: None,
    )
    pending = _attach_pending(db, job, chapter)
    monkeypatch.setattr(
        generation,
        "_openai_json",
        lambda *_args, **_kwargs: pytest.fail(
            "decision submission must not call OpenAI"),
    )

    response = client.post(
        f"/build-concepts/uploads/{job.id}/decisions/"
        f"{pending['decision_id']}",
        json={"choice": "expand_existing"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "decision_recorded"
    assert body["resume_required"] is True
    assert body["resolved_decision"]["target_concept_id"] == (
        "HOST-CONCEPT-0001")
    db.expire_all()
    saved = db.get(models.UploadJob, job.id)
    assert saved.pending_decision is None
    assert saved.generation_checkpoint["stage"] == "pre_type_assignment"

    duplicate = client.post(
        f"/build-concepts/uploads/{job.id}/decisions/"
        f"{pending['decision_id']}",
        json={"choice": "expand_existing"},
    )
    assert duplicate.status_code == 409
    with pytest.raises(uploads.UploadJobNotFound):
        build_concepts.record_human_semantic_decision(
            db,
            job.id,
            pending["decision_id"],
            choice="expand_existing",
            owner_sub="google:another-user",
        )


def test_resolution_context_and_checkpoint_bundle_round_trip(
    db,
    first_chapter,
    monkeypatch,
):
    job, chapter = _job_at_81_percent(db, first_chapter)
    monkeypatch.setattr(
        build_concepts.drive_checkpoints,
        "schedule_checkpoint_backup",
        lambda *_args, **_kwargs: None,
    )
    pending = _attach_pending(db, job, chapter)
    build_concepts.record_human_semantic_decision(
        db,
        job.id,
        pending["decision_id"],
        choice="expand_existing",
        owner_sub=auth.LOCAL_OWNER_SUB,
    )
    db.refresh(job)

    identity = {
        "decision_id": pending["decision_id"],
        "context_hash": pending["context_hash"],
    }
    assert phase33._human_resolution_for(identity) is None
    with build_concepts._human_decision_resolution_context(
        job.generation_checkpoint
    ):
        resolution = phase33._human_resolution_for(identity)
        assert resolution["choice"] == "expand_existing"
        assert resolution["target_concept_id"] == "HOST-CONCEPT-0001"
        assert resolution["deferred_assignment_unit_ids"] == ["TYPE-0003"]
    ledger = job.generation_checkpoint["human_decisions"]
    assert ledger["deferred_assignment_unit_ids"] == ["TYPE-0003"]
    assert (
        ledger["resolutions"][0]["pending_decision"][
            "deferred_assignment_unit_ids"
        ]
        == []
    )
    assert phase33._human_resolution_for(identity) is None

    _, exported = checkpoints.export_bundle(
        db, job.id, owner_sub=auth.LOCAL_OWNER_SUB)
    restored = checkpoints.import_bundle(
        db, exported, owner_sub=auth.LOCAL_OWNER_SUB)
    assert restored.generation_checkpoint["human_decisions"] == (
        job.generation_checkpoint["human_decisions"])

    tampered = json.loads(exported)
    tampered["payload"]["generation_checkpoint"]["human_decisions"][
        "context"
    ]["fingerprint"] = "0" * 64
    tampered["payload_sha256"] = checkpoints.hashlib.sha256(
        checkpoints._json_bytes(tampered["payload"])).hexdigest()
    with pytest.raises(ValueError, match="does not match the checkpoint"):
        checkpoints.import_bundle(
            db,
            json.dumps(tampered).encode(),
            owner_sub=auth.LOCAL_OWNER_SUB,
        )


def test_large_ambiguity_queue_persists_and_compacts_losslessly(
    db,
    first_chapter,
    monkeypatch,
):
    job, chapter = _job_at_81_percent(db, first_chapter)
    monkeypatch.setattr(
        build_concepts.drive_checkpoints,
        "schedule_checkpoint_backup",
        lambda *_args, **_kwargs: None,
    )
    packet = _pending_packet()
    deferred = [f"TYPE-{index:04d}" for index in range(3, 104)]
    packet["deferred_assignment_unit_ids"] = deferred
    pending = build_concepts._persist_pending_human_decision(
        db,
        job,
        packet,
        fingerprint=job.generation_checkpoint["fingerprint"],
        target_chapter_id=chapter.id,
        owner_sub=auth.LOCAL_OWNER_SUB,
    )
    assert pending["deferred_assignment_unit_ids"] == deferred

    build_concepts.record_human_semantic_decision(
        db,
        job.id,
        pending["decision_id"],
        choice="expand_existing",
        owner_sub=auth.LOCAL_OWNER_SUB,
    )
    db.refresh(job)
    ledger = job.generation_checkpoint["human_decisions"]
    assert ledger["deferred_assignment_unit_ids"] == deferred
    assert (
        ledger["resolutions"][0]["pending_decision"][
            "deferred_assignment_unit_ids"
        ]
        == []
    )
    identity = {
        "decision_id": pending["decision_id"],
        "context_hash": pending["context_hash"],
    }
    with build_concepts._human_decision_resolution_context(
        job.generation_checkpoint
    ):
        resolution = phase33._human_resolution_for(identity)
        assert resolution["deferred_assignment_unit_ids"] == deferred


def test_select_existing_requires_a_current_candidate(
    db,
    first_chapter,
    monkeypatch,
):
    job, chapter = _job_at_81_percent(db, first_chapter)
    monkeypatch.setattr(
        build_concepts.drive_checkpoints,
        "schedule_checkpoint_backup",
        lambda *_args, **_kwargs: None,
    )
    pending = _attach_pending(db, job, chapter)

    with pytest.raises(ValueError, match="pending decision candidates"):
        build_concepts.record_human_semantic_decision(
            db,
            job.id,
            pending["decision_id"],
            choice="select_existing",
            target_concept_id="HOST-CONCEPT-DOES-NOT-EXIST",
            owner_sub=auth.LOCAL_OWNER_SUB,
        )


def test_expand_existing_never_selects_the_only_candidate_implicitly(
    db,
    first_chapter,
    monkeypatch,
):
    job, chapter = _job_at_81_percent(db, first_chapter)
    monkeypatch.setattr(
        build_concepts.drive_checkpoints,
        "schedule_checkpoint_backup",
        lambda *_args, **_kwargs: None,
    )
    packet = _pending_packet()
    packet["options"][0].pop("target_concept_id")
    pending = build_concepts._persist_pending_human_decision(
        db,
        job,
        packet,
        fingerprint=job.generation_checkpoint["fingerprint"],
        target_chapter_id=chapter.id,
        owner_sub=auth.LOCAL_OWNER_SUB,
    )

    with pytest.raises(
        ValueError,
        match="target_concept_id is required",
    ):
        build_concepts.record_human_semantic_decision(
            db,
            job.id,
            pending["decision_id"],
            choice="expand_existing",
            owner_sub=auth.LOCAL_OWNER_SUB,
        )


def test_pre_learning_pause_records_decision_then_resumes_explicitly(
    db,
    first_chapter,
    monkeypatch,
):
    job, chapter = _job_at_81_percent(
        db, first_chapter, learning_kind="pre")
    generation_calls = 0
    resolution_seen = False
    identity = {
        "decision_id": _pending_packet()["decision_id"],
        "context_hash": _pending_packet()["context_hash"],
    }

    def post_map(*_args, **_kwargs):
        nonlocal generation_calls, resolution_seen
        generation_calls += 1
        resolution = phase33._human_resolution_for(identity)
        if resolution is None:
            raise semantic_recovery.HumanDecisionRequired(_pending_packet())
        resolution_seen = True
        assert resolution["choice"] == "expand_existing"
        return [{
            "topic": "Nationalism",
            "parent_concept": "Nationalism",
            "concept_title": "Renan's Idea of a Nation",
            "concept_details": "Description: A nation safeguards liberty.",
            "keywords": "nation, liberty",
        }]

    monkeypatch.setattr(
        build_concepts.config, "use_live_generation", lambda: True)
    monkeypatch.setattr(generation, "concepts_from_mmd", post_map)
    monkeypatch.setattr(
        generation,
        "pre_learning_from_rows",
        lambda *_args, **_kwargs: [{
            "topic": "Prerequisites",
            "parent_concept": "Civic Ideas",
            "concept_title": "Meaning of Liberty",
            "concept_details": "Description: Understand liberty.",
            "keywords": "liberty",
        }],
    )
    monkeypatch.setattr(
        build_concepts,
        "_deposit_and_publish_concepts",
        lambda *_args, **_kwargs: (
            [901],
            [],
            {"written": 1, "sources_updated": 0, "parent_column": True},
        ),
    )
    monkeypatch.setattr(
        build_concepts.drive_checkpoints,
        "schedule_checkpoint_backup",
        lambda *_args, **_kwargs: None,
    )

    paused = build_concepts.generate_pre_learning_from_upload(
        db, job.id, chapter.id, owner_sub=auth.LOCAL_OWNER_SUB)
    assert paused["status"] == "awaiting_decision"
    assert generation_calls == 1
    db.refresh(job)
    assert job.learning_kind == "pre"
    assert job.status == "converted"
    assert job.generation_checkpoint["stage"] == "pre_type_assignment"

    recorded = build_concepts.record_human_semantic_decision(
        db,
        job.id,
        paused["pending_decision"]["decision_id"],
        choice="expand_existing",
        owner_sub=auth.LOCAL_OWNER_SUB,
    )
    assert recorded["resume_required"] is True
    assert generation_calls == 1  # submission never starts generation

    result = build_concepts.generate_pre_learning_from_upload(
        db, job.id, chapter.id, owner_sub=auth.LOCAL_OWNER_SUB)
    assert result["concept_ids"] == [901]
    assert generation_calls == 2
    assert resolution_seen is True
    db.refresh(job)
    assert job.status == "generated"
    assert job.generation_checkpoint == {}


def test_concurrent_decision_submission_returns_409(
    client,
    db,
    first_chapter,
    monkeypatch,
):
    job, chapter = _job_at_81_percent(db, first_chapter)
    monkeypatch.setattr(
        build_concepts.drive_checkpoints,
        "schedule_checkpoint_backup",
        lambda *_args, **_kwargs: None,
    )
    pending = _attach_pending(db, job, chapter)
    entered = threading.Event()
    release = threading.Event()
    failures: list[Exception] = []
    original = build_concepts._record_human_semantic_decision_locked

    def slow_record(*args, **kwargs):
        entered.set()
        if not release.wait(timeout=5):
            raise RuntimeError("test did not release the decision writer")
        return original(*args, **kwargs)

    monkeypatch.setattr(
        build_concepts,
        "_record_human_semantic_decision_locked",
        slow_record,
    )

    def first_writer():
        worker_db = SessionLocal()
        try:
            build_concepts.record_human_semantic_decision(
                worker_db,
                job.id,
                pending["decision_id"],
                choice="expand_existing",
                owner_sub=auth.LOCAL_OWNER_SUB,
            )
        except Exception as exc:  # pragma: no cover - asserted below
            failures.append(exc)
        finally:
            worker_db.close()

    writer = threading.Thread(target=first_writer)
    writer.start()
    assert entered.wait(timeout=5)
    contender = client.post(
        f"/build-concepts/uploads/{job.id}/decisions/"
        f"{pending['decision_id']}",
        json={"choice": "expand_existing"},
    )
    assert contender.status_code == 409
    assert "already running" in contender.json()["detail"]
    release.set()
    writer.join(timeout=5)
    assert not writer.is_alive()
    assert failures == []

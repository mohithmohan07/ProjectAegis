import copy
import hashlib
import io
import json

import pytest

from app import models
from app.db import SessionLocal
from app.services import (
    build_concepts,
    canonical_source_phase38_boundary_grounding_turnover_contract as phase38,
    checkpoints,
    generation,
    openai_usage,
    uploads,
)


def _checkpoint_stage():
    return generation._make_concept_checkpoint(
        "pre_type_assignment",
        records=[{
            "topic": "Electricity",
            "parent_concept": "Electric Power",
            "concept_title": "Calculating Electric Power",
            "concept_details": "Description: Power relates energy and time.",
            "keywords": "",
        }],
        question_task_inventory={"items": [], "stats": {}},
        mined_types={"types": []},
        method_row_snapshot=[],
    )


def _pre_checkpoint_stage():
    return generation._make_concept_checkpoint(
        "pre_derivation_draft",
        records=[],
        pre_draft={"topics": []},
    )


def _job(db, *, learning_kind="post"):
    chapter = db.query(models.Chapter).order_by(models.Chapter.id).first()
    assert chapter is not None
    job = models.UploadJob(
        module="build_concepts",
        upload_type="document",
        learning_kind=learning_kind,
        source_book="NCERT",
        filename="electricity.mmd",
        mmd_text="## Electricity\nPower P is given by VI.",
        status="converted",
        generation_checkpoint={},
        question_inventory={"items": [], "stats": {}, "mined_types": []},
        generation_log=[{
            "type": "log",
            "level": "error",
            "message": "row_index=3; code=rich_text_format",
            "ts": 1.0,
        }],
        openai_usage={"request_count": 2, "total_tokens": 100},
    )
    job.generation_checkpoint = (
        build_concepts._merge_generation_checkpoint_history(
            None,
            (
                _pre_checkpoint_stage()
                if learning_kind == "pre"
                else _checkpoint_stage()
            ),
            fingerprint=build_concepts._generation_checkpoint_fingerprint(
                job, chapter),
            target_identity=build_concepts._generation_target_identity(chapter),
            target_chapter_id=chapter.id,
        )
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def _resign(bundle):
    bundle["payload_sha256"] = hashlib.sha256(
        checkpoints._json_bytes(bundle["payload"])
    ).hexdigest()
    return bundle


def _phase38_ledger(job):
    fingerprint = str(job.generation_checkpoint.get("fingerprint") or "")
    return {
        "version": 1,
        "contract": "phase3.8-boundary-aware-source-grounding-2",
        "scope": f"upload-job:{job.id}:{fingerprint}",
        "source_contract_hash": "a" * 64,
        "base_candidate_sha256": "b" * 64,
        "candidate_sha256": "c" * 64,
        "candidate_history": ["c" * 64],
        "attempts": 2,
        "signatures": {"d" * 64: 1},
        "suppressed_resolution_ids": ["phase38-decision-1"],
        "feedback": {
            "TOPOLOGY-CONCEPT-0001": "Atomise every supported clause."
        },
        "final_verification_pending": False,
        "status": "active",
        "terminal_reason": "",
    }


def _phase38_extended_ledger(job):
    fingerprint = str(job.generation_checkpoint.get("fingerprint") or "")
    state = phase38._fresh_convergence_state(
        scope=f"upload-job:{job.id}:{fingerprint}",
        source_contract_hash="a" * 64,
    )
    state["base_candidate_sha256"] = "b" * 64
    state["candidate_sha256"] = "c" * 64
    state["suppressed_resolution_ids"] = ["phase38-decision-1"]
    bucket = phase38._fresh_issue_bucket()
    bucket.update({
        "candidate_history": ["c" * 64],
        "attempts": 2,
        "signatures": {"d" * 64: 1},
        "feedback": {
            "TOPOLOGY-CONCEPT-0001": "Atomise every supported clause."
        },
    })
    phase38._mirror_active_issue(state, "e" * 64, bucket)
    return state


def _placement_certification_ledger():
    return {
        "version": generation._PLACEMENT_CERTIFICATION_VERSION,
        "hosts": {
            "QINV-0001": {
                "topic": "Methods",
                "topic_key": "methods",
                "concept": "Method Beta",
                "concept_key": "method beta",
                "is_culmination": False,
                "basis": "type_host_review",
            },
        },
    }


def _certified_inventory_item():
    return {
        "qid": "QINV-0001",
        "source_kind": "exercise",
        "raw_task": (
            "Explain how Method Beta establishes the requested result."
        ),
    }


def _post_bundle(client, bundle, *, learning_kind=""):
    suffix = f"?learning_kind={learning_kind}" if learning_kind else ""
    return client.post(
        f"/build-concepts/checkpoints/import{suffix}",
        files={
            "file": (
                "checkpoint.json",
                io.BytesIO(json.dumps(bundle).encode()),
                "application/json",
            )
        },
    )


def test_checkpoint_bundle_round_trips_as_new_converted_job(client, db):
    original = _job(db)

    response = client.get(
        f"/build-concepts/uploads/{original.id}/checkpoint"
    )

    assert response.status_code == 200
    assert "electricity.aegis-checkpoint.json" in response.headers[
        "content-disposition"
    ]
    bundle = response.json()
    assert bundle["format"] == "aegis-concept-checkpoint"
    assert bundle["payload"]["generation_checkpoint"]["stage"] == (
        "pre_type_assignment"
    )

    restored = client.post(
        "/build-concepts/checkpoints/import",
        files={
            "file": (
                "electricity.aegis-checkpoint.json",
                io.BytesIO(response.content),
                "application/json",
            )
        },
    )

    assert restored.status_code == 200
    data = restored.json()
    assert data["id"] != original.id
    assert data["status"] == "converted"
    assert data["checkpoint_available"] is True
    assert data["checkpoint_stage"] == "pre_type_assignment"
    assert data["checkpoint_progress"] == 0.81
    assert data["generation_log"][0]["message"].startswith("row_index=3")
    imported = db.get(models.UploadJob, data["id"])
    assert imported.mmd_text == original.mmd_text
    assert imported.openai_usage["total_tokens"] == 100
    chapter = db.query(models.Chapter).order_by(models.Chapter.id).first()
    assert build_concepts._checkpoint_matches_generation(
        imported.generation_checkpoint,
        job=imported,
        chapter=chapter,
    )


def test_phase38_convergence_ledger_round_trips_with_checkpoint(client, db):
    original = _job(db)
    ledger = _phase38_extended_ledger(original)
    checkpoint = copy.deepcopy(original.generation_checkpoint)
    checkpoint[build_concepts._PHASE38_CONVERGENCE_KEY] = copy.deepcopy(ledger)
    original.generation_checkpoint = checkpoint
    db.commit()

    exported = client.get(
        f"/build-concepts/uploads/{original.id}/checkpoint"
    )
    assert exported.status_code == 200
    restored = _post_bundle(client, exported.json())

    assert restored.status_code == 200
    imported = db.get(models.UploadJob, restored.json()["id"])
    imported_ledger = imported.generation_checkpoint[
        build_concepts._PHASE38_CONVERGENCE_KEY
    ]
    expected_scope = (
        f"upload-job:{imported.id}:"
        f"{imported.generation_checkpoint['fingerprint']}"
    )
    assert imported_ledger["scope"] == expected_scope
    expected_ledger = copy.deepcopy(ledger)
    expected_ledger["scope"] = expected_scope
    assert imported_ledger == expected_ledger
    # The first Phase 3.8 call in the restored job must load, rather than
    # silently reset, the portable attempt budget under its new namespace.
    normalized = phase38._normalized_convergence_state(
        imported_ledger,
        scope=expected_scope,
    )
    assert normalized["attempts"] == ledger["attempts"]
    assert normalized["candidate_history"] == ledger["candidate_history"]


def test_disposed_phase38_ledger_survives_export_and_import(client, db):
    """A terminally disposed budget must remain portable.

    The disposition is what lets a run finish; a bundle that refused to carry
    it would hand the restored job a fresh budget and re-spend the money the
    original run already spent reaching the same answer.
    """
    original = _job(db)
    ledger = _phase38_extended_ledger(original)
    for bucket in ledger["issue_buckets"].values():
        bucket["status"] = "disposed"
        bucket["final_verification_pending"] = False
        bucket["terminal_reason"] = "narrowed 2 concepts to their evidence"
    phase38._mirror_active_issue(
        ledger,
        ledger["active_issue_key"],
        ledger["issue_buckets"][ledger["active_issue_key"]],
    )
    ledger["disposition"] = "aegis-evidence-narrowing-1"
    checkpoint = copy.deepcopy(original.generation_checkpoint)
    checkpoint[build_concepts._PHASE38_CONVERGENCE_KEY] = copy.deepcopy(ledger)
    original.generation_checkpoint = checkpoint
    db.commit()

    exported = client.get(f"/build-concepts/uploads/{original.id}/checkpoint")
    assert exported.status_code == 200
    restored = _post_bundle(client, exported.json())
    assert restored.status_code == 200

    imported = db.get(models.UploadJob, restored.json()["id"])
    imported_ledger = imported.generation_checkpoint[
        build_concepts._PHASE38_CONVERGENCE_KEY
    ]
    assert imported_ledger["status"] == "disposed"
    assert imported_ledger["disposition"] == "aegis-evidence-narrowing-1"
    normalized = phase38._normalized_convergence_state(
        imported_ledger,
        scope=imported_ledger["scope"],
    )
    assert normalized["status"] == "disposed"
    assert normalized["disposition"] == "aegis-evidence-narrowing-1"


def test_phase38_cas_rejects_a_stale_identical_worker(
    db,
    monkeypatch,
):
    original = _job(db)
    replacement = _phase38_ledger(original)
    monkeypatch.setattr(
        build_concepts.drive_checkpoints,
        "schedule_checkpoint_backup",
        lambda _job_id: None,
    )
    worker_a = SessionLocal()
    worker_b = SessionLocal()
    try:
        job_a = worker_a.get(models.UploadJob, original.id)
        job_b = worker_b.get(models.UploadJob, original.id)
        assert job_a is not None and job_b is not None

        build_concepts._persist_phase38_convergence_state(
            worker_a,
            job_a,
            {},
            replacement,
        )
        # Worker B observed the same empty ledger before A committed. Even an
        # identical replacement is a stale dispatch claim and must not be
        # treated as an idempotent success that permits a second model call.
        with pytest.raises(
            phase38.Phase38ConvergenceExhausted,
            match="another worker advanced",
        ):
            build_concepts._persist_phase38_convergence_state(
                worker_b,
                job_b,
                {},
                replacement,
            )

        db.expire_all()
        stored = db.get(models.UploadJob, original.id)
        assert stored is not None
        assert stored.generation_checkpoint[
            build_concepts._PHASE38_CONVERGENCE_KEY
        ] == replacement
    finally:
        worker_a.close()
        worker_b.close()


def test_discarding_last_stage_preserves_phase38_control_ledger(
    client,
    db,
):
    original = _job(db)
    stored = copy.deepcopy(original.generation_checkpoint)
    ledger = _phase38_ledger(original)
    stored[build_concepts._PHASE38_CONVERGENCE_KEY] = copy.deepcopy(ledger)
    durable = build_concepts._merge_generation_checkpoint_history(
        stored,
        {
            "checkpoint_action": "discard_stage",
            "stage": "pre_type_assignment",
        },
        fingerprint=stored["fingerprint"],
        target_identity=stored["target_identity"],
        target_chapter_id=stored["target_chapter_id"],
    )

    assert durable[build_concepts._PHASE38_CONTROL_ONLY_KEY] is True
    assert durable["checkpoints"] == []
    assert durable[build_concepts._PHASE38_CONVERGENCE_KEY] == ledger
    assert generation._newest_compatible_concept_checkpoint(durable) is None

    original.generation_checkpoint = durable
    db.commit()
    exported = client.get(
        f"/build-concepts/uploads/{original.id}/checkpoint"
    )
    assert exported.status_code == 200
    restored = _post_bundle(client, exported.json())
    assert restored.status_code == 200
    imported = db.get(models.UploadJob, restored.json()["id"])
    imported_ledger = imported.generation_checkpoint[
        build_concepts._PHASE38_CONVERGENCE_KEY
    ]
    assert imported_ledger["attempts"] == ledger["attempts"]
    assert imported_ledger["candidate_history"] == ledger[
        "candidate_history"
    ]
    assert imported_ledger["scope"].startswith(
        f"upload-job:{imported.id}:"
    )


def test_compatibility_fallback_keeps_phase38_when_every_stage_is_invalid(db):
    original = _job(db)
    stored = copy.deepcopy(original.generation_checkpoint)
    ledger = _phase38_ledger(original)
    stored[build_concepts._PHASE38_CONVERGENCE_KEY] = copy.deepcopy(ledger)
    for entry in stored["checkpoints"]:
        entry["stage_schema_version"] = 999

    normalized = build_concepts._compatible_generation_checkpoint_envelope(
        stored,
        fingerprint=stored["fingerprint"],
        target_identity=stored["target_identity"],
        target_chapter_id=stored["target_chapter_id"],
    )

    assert normalized[build_concepts._PHASE38_CONTROL_ONLY_KEY] is True
    assert normalized["checkpoints"] == []
    assert normalized[build_concepts._PHASE38_CONVERGENCE_KEY] == ledger


def test_malformed_phase38_candidate_hash_is_rejected(client, db):
    original = _job(db)
    bundle = client.get(
        f"/build-concepts/uploads/{original.id}/checkpoint"
    ).json()
    bundle["payload"]["generation_checkpoint"][
        build_concepts._PHASE38_CONVERGENCE_KEY
    ] = _phase38_ledger(original)
    bundle["payload"]["generation_checkpoint"][
        build_concepts._PHASE38_CONVERGENCE_KEY
    ]["candidate_sha256"] = "not-a-hash"
    _resign(bundle)

    response = _post_bundle(client, bundle)

    assert response.status_code == 400
    assert "candidate_sha256" in response.json()["detail"]


def test_placement_certification_ledger_round_trips_in_both_payload_copies(
    db,
):
    original = _job(db)
    ledger = _placement_certification_ledger()
    inventory = {
        "items": [_certified_inventory_item()],
        "stats": {"total_inventory_items": 1},
        "mined_types": [{"type_id": "TYPE-0001"}],
        generation._PLACEMENT_CERTIFICATIONS_KEY: copy.deepcopy(ledger),
    }
    original.question_inventory = copy.deepcopy(inventory)
    generation_checkpoint = copy.deepcopy(original.generation_checkpoint)
    checkpoint = generation_checkpoint["checkpoints"][-1]
    checkpoint["question_task_inventory"] = {
        "items": [_certified_inventory_item()],
        "stats": {"total_inventory_items": 1},
    }
    checkpoint["mined_types"] = {
        "types": [{"type_id": "TYPE-0001"}],
        generation._PLACEMENT_CERTIFICATIONS_KEY: copy.deepcopy(ledger),
    }
    original.generation_checkpoint = generation_checkpoint
    db.commit()

    _, raw_bytes = checkpoints.export_bundle(db, original.id)
    bundle = json.loads(raw_bytes)
    restored = checkpoints.import_bundle(db, raw_bytes)

    assert bundle["payload"]["question_inventory"][
        generation._PLACEMENT_CERTIFICATIONS_KEY
    ] == ledger
    assert bundle["payload"]["generation_checkpoint"]["checkpoints"][-1][
        "mined_types"
    ][generation._PLACEMENT_CERTIFICATIONS_KEY] == ledger
    assert restored.question_inventory[
        generation._PLACEMENT_CERTIFICATIONS_KEY
    ] == ledger
    assert restored.generation_checkpoint["checkpoints"][-1]["mined_types"][
        generation._PLACEMENT_CERTIFICATIONS_KEY
    ] == ledger


def test_checkpoint_import_rejects_tampered_payload(client, db):
    original = _job(db)
    exported = client.get(
        f"/build-concepts/uploads/{original.id}/checkpoint"
    ).json()
    exported["payload"]["job"]["mmd_text"] += "\ntampered"

    response = client.post(
        "/build-concepts/checkpoints/import",
        files={
            "file": (
                "tampered.json",
                io.BytesIO(json.dumps(exported).encode()),
                "application/json",
            )
        },
    )

    assert response.status_code == 400
    assert "checksum" in response.json()["detail"]


@pytest.mark.parametrize(
    ("mutate", "detail_fragment"),
    [
        (
            lambda ledger: ledger.update(version=2),
            "version is not supported",
        ),
        (
            lambda ledger: ledger["hosts"]["QINV-0001"].update(
                concept_key="different concept"
            ),
            "normalized host identity",
        ),
        (
            lambda ledger: ledger["hosts"].update({
                "QINV-UNKNOWN": ledger["hosts"].pop("QINV-0001"),
            }),
            "must exactly cover the inventory qids",
        ),
    ],
)
def test_checksum_valid_malformed_inventory_certifications_are_rejected(
    client, db, mutate, detail_fragment,
):
    original = _job(db)
    bundle = client.get(
        f"/build-concepts/uploads/{original.id}/checkpoint"
    ).json()
    bundle["payload"]["question_inventory"] = {
        "items": [{"qid": "QINV-0001"}],
        "stats": {"total_inventory_items": 1},
        "mined_types": [{"type_id": "TYPE-0001"}],
        generation._PLACEMENT_CERTIFICATIONS_KEY: (
            _placement_certification_ledger()
        ),
    }
    mutate(bundle["payload"]["question_inventory"][
        generation._PLACEMENT_CERTIFICATIONS_KEY
    ])
    _resign(bundle)
    before = db.query(models.UploadJob).count()

    response = _post_bundle(client, bundle)

    assert response.status_code == 400
    assert detail_fragment in response.json()["detail"]
    assert db.query(models.UploadJob).count() == before


def test_checksum_valid_malformed_checkpoint_certification_is_rejected(
    client, db,
):
    original = _job(db)
    bundle = client.get(
        f"/build-concepts/uploads/{original.id}/checkpoint"
    ).json()
    checkpoint = bundle["payload"]["generation_checkpoint"]["checkpoints"][-1]
    checkpoint["question_task_inventory"] = {
        "items": [_certified_inventory_item()],
        "stats": {"total_inventory_items": 1},
    }
    checkpoint["mined_types"] = {
        "types": [{"type_id": "TYPE-0001"}],
        generation._PLACEMENT_CERTIFICATIONS_KEY: (
            _placement_certification_ledger()
        ),
    }
    checkpoint["mined_types"][
        generation._PLACEMENT_CERTIFICATIONS_KEY
    ]["hosts"]["QINV-0001"]["unexpected"] = "not portable"
    _resign(bundle)
    before = db.query(models.UploadJob).count()

    response = _post_bundle(client, bundle)

    assert response.status_code == 400
    assert "contains unsupported field(s): unexpected" in (
        response.json()["detail"]
    )
    assert db.query(models.UploadJob).count() == before


@pytest.mark.parametrize(
    ("mutate", "detail_fragment"),
    [
        (
            lambda bundle: bundle["payload"].update(
                generation_log="not-an-array"),
            "generation_log must be an array",
        ),
        (
            lambda bundle: bundle["payload"].update(
                openai_usage={"request_count": "2"}),
            "request_count must be an integer",
        ),
        (
            lambda bundle: bundle["payload"].update(
                generation_checkpoint=_checkpoint_stage()),
            "fingerprint must be a string",
        ),
        (
            lambda bundle: bundle["payload"]["job"].update(
                deposit_scope_ids=["1"]),
            "deposit_scope_ids[0] must be an integer",
        ),
        (
            lambda bundle: bundle["payload"]["generation_checkpoint"]
            ["checkpoints"][-1].update(
                source_review_resolution_applied="yes"),
            "source_review_resolution_applied must be a boolean",
        ),
        (
            lambda bundle: bundle["payload"]["generation_checkpoint"]
            ["checkpoints"][-1].update(
                source_review_metadata_sanitization_applied="yes"),
            (
                "source_review_metadata_sanitization_applied must be a "
                "boolean"
            ),
        ),
    ],
)
def test_checksum_valid_malformed_shapes_are_rejected_before_db_write(
    client, db, mutate, detail_fragment,
):
    original = _job(db)
    bundle = client.get(
        f"/build-concepts/uploads/{original.id}/checkpoint"
    ).json()
    mutate(bundle)
    _resign(bundle)
    before = db.query(models.UploadJob).count()

    response = _post_bundle(client, bundle)

    assert response.status_code == 400
    assert detail_fragment in response.json()["detail"]
    assert db.query(models.UploadJob).count() == before


def test_checksum_valid_overlong_log_message_is_rejected(client, db):
    original = _job(db)
    bundle = client.get(
        f"/build-concepts/uploads/{original.id}/checkpoint"
    ).json()
    bundle["payload"]["generation_log"][0]["message"] = (
        "x" * (checkpoints.MAX_LOG_MESSAGE_CHARS + 1)
    )
    _resign(bundle)

    response = _post_bundle(client, bundle)

    assert response.status_code == 400
    assert "message exceeds" in response.json()["detail"]


def test_checksum_valid_source_change_must_also_match_checkpoint_fingerprint(
    client, db,
):
    original = _job(db)
    bundle = client.get(
        f"/build-concepts/uploads/{original.id}/checkpoint"
    ).json()
    bundle["payload"]["job"]["mmd_text"] += "\nChanged after checkpoint."
    _resign(bundle)

    response = _post_bundle(client, bundle)

    assert response.status_code == 400
    assert "fingerprint does not match" in response.json()["detail"]


def test_metadata_complete_direct_stage_is_portable(client, db):
    original = _job(db)
    chapter = db.query(models.Chapter).order_by(models.Chapter.id).first()
    bundle = client.get(
        f"/build-concepts/uploads/{original.id}/checkpoint"
    ).json()
    envelope = bundle["payload"]["generation_checkpoint"]
    direct = copy.deepcopy(envelope["checkpoints"][-1])
    for field in ("fingerprint", "target_identity", "target_chapter_id"):
        direct[field] = copy.deepcopy(envelope[field])
    bundle["payload"]["generation_checkpoint"] = direct
    _resign(bundle)

    response = _post_bundle(client, bundle)

    assert response.status_code == 200
    imported = db.get(models.UploadJob, response.json()["id"])
    assert build_concepts._checkpoint_matches_generation(
        imported.generation_checkpoint,
        job=imported,
        chapter=chapter,
    )


@pytest.mark.parametrize("learning_kind", ["post", "pre"])
def test_real_envelope_import_can_match_its_target_after_restore(
    client, db, learning_kind,
):
    original = _job(db, learning_kind=learning_kind)
    chapter = db.query(models.Chapter).order_by(models.Chapter.id).first()
    exported = client.get(
        f"/build-concepts/uploads/{original.id}/checkpoint"
    )

    response = client.post(
        f"/build-concepts/checkpoints/import?learning_kind={learning_kind}",
        files={
            "file": (
                "restored.json",
                io.BytesIO(exported.content),
                "application/json",
            )
        },
    )

    assert response.status_code == 200
    imported = db.get(models.UploadJob, response.json()["id"])
    assert build_concepts._checkpoint_matches_generation(
        imported.generation_checkpoint,
        job=imported,
        chapter=chapter,
    )
    assert imported.learning_kind == learning_kind
    assert imported.openai_usage == original.openai_usage


def test_complete_usage_and_historical_cost_round_trip_unchanged(client, db):
    original = _job(db)
    accumulator = openai_usage.UsageAccumulator()
    accumulator.add(
        model="gpt-5.4-mini-2026-03-17",
        request_count=3,
        input_tokens=12_345,
        cached_input_tokens=2_345,
        output_tokens=6_789,
        reasoning_tokens=1_234,
        total_tokens=19_134,
    )
    historical = accumulator.summary()
    original.openai_usage = historical
    db.commit()
    _, raw_bytes = checkpoints.export_bundle(db, original.id)

    restored = checkpoints.import_bundle(db, raw_bytes)

    assert restored.openai_usage == historical
    assert restored.openai_usage["estimated_cost_usd"] == (
        historical["estimated_cost_usd"]
    )
    assert restored.openai_usage["models"][0]["cached_input_tokens"] == 2_345


def test_legacy_v1_usage_without_cache_write_tokens_still_imports(db):
    original = _job(db)
    accumulator = openai_usage.UsageAccumulator()
    accumulator.add(
        model="gpt-5.4-mini-2026-03-17",
        request_count=3,
        input_tokens=12_345,
        cached_input_tokens=2_345,
        output_tokens=6_789,
        reasoning_tokens=1_234,
        total_tokens=19_134,
    )
    original.openai_usage = accumulator.summary()
    db.commit()
    _, raw_bytes = checkpoints.export_bundle(db, original.id)
    legacy = json.loads(raw_bytes)
    legacy["payload"]["openai_usage"].pop("cache_write_tokens")
    for row in legacy["payload"]["openai_usage"]["models"]:
        row.pop("cache_write_tokens")
    _resign(legacy)

    restored = checkpoints.import_bundle(
        db, checkpoints._json_bytes(legacy, pretty=True)
    )

    assert restored.openai_usage["request_count"] == 3
    assert "cache_write_tokens" not in restored.openai_usage
    assert "cache_write_tokens" not in restored.openai_usage["models"][0]


def test_imported_usage_is_the_baseline_for_resumed_checkpoint_runs(db):
    original = _job(db)
    historical_accumulator = openai_usage.UsageAccumulator()
    historical_accumulator.add(
        model="gpt-5.4-mini-2026-03-17",
        request_count=3,
        input_tokens=12_345,
        cached_input_tokens=2_345,
        output_tokens=6_789,
        reasoning_tokens=1_234,
        total_tokens=19_134,
    )
    original.openai_usage = historical_accumulator.summary()
    db.commit()
    _, raw_bytes = checkpoints.export_bundle(db, original.id)
    restored = checkpoints.import_bundle(db, raw_bytes)

    with openai_usage.track() as resumed_usage:
        def resume():
            resumed_usage.add(
                model="gpt-5.4-mini-2026-03-17",
                input_tokens=100,
                cached_input_tokens=40,
                output_tokens=20,
                total_tokens=120,
            )
            # Multiple automatic saves and terminal persistence must all
            # rewrite baseline + current run, not add current repeatedly.
            uploads.persist_current_openai_usage(db, restored.id)
            uploads.persist_current_openai_usage(db, restored.id)
            return {"resumed": True}

        result = uploads.run_with_openai_usage(db, restored.id, resume)

    assert result["openai_usage"]["request_count"] == 4
    assert result["openai_usage"]["total_tokens"] == 19_254
    db.refresh(restored)
    assert restored.openai_usage["request_count"] == 4
    assert restored.openai_usage["total_tokens"] == 19_254


def test_structured_terminal_error_round_trips_with_bounded_frames(db):
    original = _job(db)
    terminal = {
        "type": "log",
        "level": "error",
        "message": "RuntimeError: validation failed at generation.py:123",
        "error": {
            "exception_type": "RuntimeError",
            "reason": "validation failed",
            "frames": [{
                "file": "app/services/generation.py",
                "line": 123,
                "function": "_validate_final_or_raise",
            }],
        },
    }
    original.generation_log = [terminal]
    db.commit()
    _, raw_bytes = checkpoints.export_bundle(db, original.id)

    restored = checkpoints.import_bundle(db, raw_bytes)

    assert restored.generation_log == [terminal]


def test_import_rolls_back_when_database_commit_fails(db, monkeypatch):
    original = _job(db)
    _, raw_bytes = checkpoints.export_bundle(db, original.id)
    rollback_called = False
    real_rollback = db.rollback

    def fail_commit():
        raise RuntimeError("simulated commit failure")

    def track_rollback():
        nonlocal rollback_called
        rollback_called = True
        real_rollback()

    monkeypatch.setattr(db, "commit", fail_commit)
    monkeypatch.setattr(db, "rollback", track_rollback)

    with pytest.raises(RuntimeError, match="simulated commit failure"):
        checkpoints.import_bundle(db, raw_bytes)

    assert rollback_called is True
    assert not db.new


def test_converted_source_can_be_exported_without_generation_checkpoint(
    client, db,
):
    job = models.UploadJob(
        module="build_concepts",
        upload_type="document",
        learning_kind="post",
        filename="source.mmd",
        mmd_text="## Topic\nConverted source.",
        status="converted",
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    response = client.get(f"/build-concepts/uploads/{job.id}/checkpoint")

    assert response.status_code == 200
    assert response.json()["payload"]["generation_checkpoint"] == {}


def test_clear_checkpoint_keeps_converted_source(client, db):
    job = _job(db)

    response = client.delete(
        f"/build-concepts/uploads/{job.id}/checkpoint"
    )

    assert response.status_code == 200
    assert response.json()["checkpoint_available"] is False
    db.refresh(job)
    assert job.generation_checkpoint == {}
    assert job.mmd_text

import copy
import hashlib
import io
import json

import pytest

from app import models
from app.services import build_concepts, checkpoints, generation, openai_usage


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

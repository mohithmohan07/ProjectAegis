import copy
import io

import pytest

from app import models
from app.services import build_concepts
from tests.conftest import convert_concept_upload, stream_result


def test_post_learning_creates_concepts(client, first_chapter):
    files = {"file": ("notes.txt", io.BytesIO(
        b"## Trigonometry Basics\nSine ratio: opposite over hypotenuse\n"
        b"Cosine ratio: adjacent over hypotenuse"
    ), "text/plain")}
    job = client.post("/build-concepts/post-learning/uploads", files=files).json()
    assert job["learning_kind"] == "post"
    assert job["status"] == "uploaded"  # upload stages only

    convert_concept_upload(client, job["id"])
    result = stream_result(client.post(
        f"/build-concepts/post-learning/uploads/{job['id']}/generate",
        json={"target_chapter_id": first_chapter["id"]}))
    assert result["concepts_created"] >= 2
    assert result["rows_appended"] >= 2


def test_post_learning_groups_concepts_under_one_topic(client, db, first_chapter):
    """Concepts sharing a topic name must share ONE Topic row (no duplicates)."""
    files = {"file": ("grouping.txt", io.BytesIO(
        b"## Grouping Topic 9912\nGrouping concept alpha 9912\n"
        b"Grouping concept beta 9912\nGrouping concept gamma 9912"
    ), "text/plain")}
    job = client.post("/build-concepts/post-learning/uploads", files=files).json()
    convert_concept_upload(client, job["id"])
    result = stream_result(client.post(
        f"/build-concepts/post-learning/uploads/{job['id']}/generate",
        json={"target_chapter_id": first_chapter["id"]}))
    assert result["concepts_created"] == 4

    import app.models as models
    topics = (
        db.query(models.Topic)
        .filter_by(chapter_id=first_chapter["id"], topic_title="Grouping Topic 9912")
        .all()
    )
    assert len(topics) == 1
    assert len(topics[0].concepts) == 4
    assert sum(c.concept_title.startswith("Culmination -") for c in topics[0].concepts) == 1


def test_post_learning_failure_persists_and_resumes_type_checkpoint(
    db, first_chapter, monkeypatch,
):
    job = models.UploadJob(
        module="build_concepts",
        upload_type="document",
        learning_kind="post",
        filename="checkpoint.mmd",
        mmd_text="## Topic\nSource body",
        status="converted",
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    checkpoint = {
            "schema_version": (
                build_concepts.generation._CONCEPT_CHECKPOINT_SCHEMA),
        "stage": "pre_type_assignment",
        "records": [{"topic": "T", "concept_title": "C"}],
        "question_task_inventory": {
            "items": [{"qid": "QINV-0001", "raw_task": "Explain the source."}],
            "stats": {"total_inventory_items": 1},
        },
        "mined_types": {"types": [{"type_id": "TYPE-0001"}]},
        "method_row_snapshot": [],
    }

    def fail_after_checkpoint(*args, checkpoint_callback=None, **kwargs):
        assert checkpoint_callback is not None
        checkpoint_callback(checkpoint)
        raise RuntimeError("type embedding failed: unassigned TYPE-0001")

    monkeypatch.setattr(
        build_concepts.generation, "concepts_from_mmd", fail_after_checkpoint)
    with pytest.raises(RuntimeError, match="unassigned TYPE-0001"):
        build_concepts.generate_post_learning(
            db, job.id, first_chapter["id"])

    db.expire_all()
    saved = db.get(models.UploadJob, job.id)
    assert saved.generation_checkpoint["stage"] == "pre_type_assignment"
    assert saved.question_inventory["items"][0]["qid"] == "QINV-0001"

    def resume_from_checkpoint(*args, resume_checkpoint=None, **kwargs):
        assert resume_checkpoint["stage"] == "pre_type_assignment"
        return [{
            "topic": "T",
            "parent_concept": "P",
            "concept_title": "C",
            "concept_details": "Description: complete",
            "keywords": "",
        }]

    monkeypatch.setattr(
        build_concepts.generation, "concepts_from_mmd",
        resume_from_checkpoint,
    )
    monkeypatch.setattr(
        build_concepts, "_deposit_concepts", lambda *a, **kw: ([], []))
    monkeypatch.setattr(
        build_concepts.writer,
        "append_concepts",
        lambda *a, **kw: {
            "written": 0, "sources_updated": 0, "parent_column": True,
        },
    )
    monkeypatch.setattr(
        build_concepts, "_publish_staged_workbook", lambda *a, **kw: None)
    result = build_concepts.generate_post_learning(
        db, job.id, first_chapter["id"])
    assert result["concepts_created"] == 0
    db.expire_all()
    assert db.get(models.UploadJob, job.id).generation_checkpoint == {}


def test_checkpoint_without_inventory_does_not_erase_saved_inventory(
    db, first_chapter, monkeypatch,
):
    job = models.UploadJob(
        module="build_concepts",
        upload_type="document",
        learning_kind="post",
        filename="inventory-preservation.mmd",
        mmd_text="## Topic\nSource body",
        status="converted",
        question_inventory={
            "items": [{"qid": "QINV-KEEP"}],
            "stats": {"total_inventory_items": 1},
            "mined_types": [{"type_id": "TYPE-KEEP"}],
        },
    )
    db.add(job)
    db.commit()

    def fail_after_skeleton(*args, checkpoint_callback=None, **kwargs):
        checkpoint_callback(
            build_concepts.generation._make_concept_checkpoint(
                "skeleton_complete",
                records=[{"topic": "T", "concept_title": "C"}],
            )
        )
        raise RuntimeError("later stage failed")

    monkeypatch.setattr(
        build_concepts.generation,
        "concepts_from_mmd",
        fail_after_skeleton,
    )

    with pytest.raises(RuntimeError, match="later stage failed"):
        build_concepts.generate_post_learning(
            db, job.id, first_chapter["id"])

    db.expire_all()
    saved = db.get(models.UploadJob, job.id)
    assert saved.question_inventory["items"][0]["qid"] == "QINV-KEEP"
    assert saved.question_inventory["mined_types"][0]["type_id"] == "TYPE-KEEP"


def test_post_learning_preserves_invalid_checkpoint_and_requires_start_over(
    db, first_chapter, monkeypatch,
):
    job = models.UploadJob(
        module="build_concepts",
        upload_type="document",
        learning_kind="post",
        filename="invalid-checkpoint.mmd",
        mmd_text="## Topic\nSource body",
        status="converted",
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    chapter = db.get(models.Chapter, first_chapter["id"])
    job.generation_checkpoint = {
        "fingerprint": build_concepts._generation_checkpoint_fingerprint(
            job, chapter),
        "target_chapter_id": chapter.id,
        "schema_version": 999,
        "stage": "pre_type_assignment",
    }
    db.commit()

    original = dict(job.generation_checkpoint)
    monkeypatch.setattr(
        build_concepts.generation,
        "concepts_from_mmd",
        lambda *a, **kw: (_ for _ in ()).throw(
            AssertionError("mismatched checkpoint must stop generation")),
    )

    with pytest.raises(ValueError, match="has been preserved"):
        build_concepts.generate_post_learning(db, job.id, chapter.id)

    db.expire_all()
    assert db.get(models.UploadJob, job.id).generation_checkpoint == original


def test_checkpoint_target_identity_survives_chapter_id_changes(
    db, first_chapter,
):
    original = db.get(models.Chapter, first_chapter["id"])
    rebuilt = models.Chapter(
        chapter_code=original.chapter_code,
        board=original.board,
        grade=original.grade,
        subject=original.subject,
        unit=original.unit,
        chapter_title=original.chapter_title,
        chapter_display_name=original.chapter_display_name,
    )
    job = models.UploadJob(
        module="build_concepts",
        upload_type="document",
        learning_kind="post",
        filename="portable.mmd",
        mmd_text="## Stable source\nBody",
        status="converted",
    )
    db.add_all([rebuilt, job])
    db.commit()
    db.refresh(rebuilt)
    db.refresh(job)
    checkpoint = build_concepts.generation._make_concept_checkpoint(
        "pre_type_assignment",
        records=[{"concept_title": "C"}],
        question_task_inventory={"items": [], "stats": {}},
        mined_types={"types": []},
        method_row_snapshot=[],
    )
    envelope = build_concepts._merge_generation_checkpoint_history(
        {},
        checkpoint,
        fingerprint=build_concepts._generation_checkpoint_fingerprint(
            job, original),
        target_identity=build_concepts._generation_target_identity(original),
        target_chapter_id=original.id,
    )

    assert envelope["checkpoints"] == [checkpoint]
    assert rebuilt.id != original.id
    assert build_concepts._checkpoint_matches_generation(
        envelope,
        job=job,
        chapter=rebuilt,
    )


def test_post_learning_wrong_chapter_preserves_checkpoint(
    db, first_chapter, monkeypatch,
):
    original = db.get(models.Chapter, first_chapter["id"])
    wrong_target = models.Chapter(
        chapter_code=f"{original.chapter_code}-WRONG",
        board=original.board,
        grade=original.grade,
        subject=original.subject,
        unit=original.unit,
        chapter_title=f"{original.chapter_title} Wrong Target",
        chapter_display_name=f"{original.chapter_title} Wrong Target",
    )
    job = models.UploadJob(
        module="build_concepts",
        upload_type="document",
        learning_kind="post",
        filename="wrong-target.mmd",
        mmd_text="## Stable source\nBody",
        status="converted",
    )
    db.add_all([wrong_target, job])
    db.commit()
    checkpoint = build_concepts.generation._make_concept_checkpoint(
        "pre_type_assignment",
        records=[{"concept_title": "C"}],
        question_task_inventory={"items": [], "stats": {}},
        mined_types={"types": []},
        method_row_snapshot=[],
    )
    job.generation_checkpoint = build_concepts._merge_generation_checkpoint_history(
        {},
        checkpoint,
        fingerprint=build_concepts._generation_checkpoint_fingerprint(
            job, original),
        target_identity=build_concepts._generation_target_identity(original),
        target_chapter_id=original.id,
    )
    db.commit()
    expected = copy.deepcopy(job.generation_checkpoint)
    monkeypatch.setattr(
        build_concepts.generation,
        "concepts_from_mmd",
        lambda *a, **kw: (_ for _ in ()).throw(
            AssertionError("wrong target must stop before generation")),
    )

    with pytest.raises(ValueError, match="selected chapter or converted source"):
        build_concepts.generate_post_learning(db, job.id, wrong_target.id)

    db.expire_all()
    assert db.get(models.UploadJob, job.id).generation_checkpoint == expected


@pytest.mark.parametrize("learning_kind", ["post", "pre"])
def test_upload_workbook_failure_rolls_back_new_concepts(
    db, first_chapter, monkeypatch, tmp_path, learning_kind,
):
    marker = f"Atomic Workbook Failure {learning_kind.title()} 73419"
    job = models.UploadJob(
        module="build_concepts",
        upload_type="document",
        learning_kind=learning_kind,
        filename=f"{learning_kind}-atomic.mmd",
        mmd_text="## Atomic source\nBody",
        status="converted",
    )
    db.add(job)
    db.commit()
    chapter = db.get(models.Chapter, first_chapter["id"])
    normal = {
        "topic": f"{marker} Topic",
        "parent_concept": marker,
        "concept_title": marker,
        "concept_details": (
            "Description: Learners apply a complete, source-grounded "
            "procedure accurately. // Error Analysis: Students may omit a "
            "required step while applying the procedure."
        ),
        "keywords": "",
    }
    post_records = [
        normal,
        {
            "topic": f"{marker} Topic",
            "parent_concept": "Culmination",
            "concept_title": f"Culmination - {marker}",
            "concept_details": f"Description: Recap of {marker}.",
            "keywords": "",
        },
    ]
    monkeypatch.setattr(
        build_concepts.config,
        "BULK_IMPORT_OUTPUT",
        tmp_path / "bulk_import_output.xlsx",
    )
    monkeypatch.setattr(
        build_concepts, "_chapter_meta_summary", lambda _chapter: {})
    monkeypatch.setattr(
        build_concepts.generation,
        "concepts_from_mmd",
        lambda *a, **kw: post_records,
    )
    monkeypatch.setattr(
        build_concepts.generation,
        "pre_learning_from_rows",
        lambda *a, **kw: [normal],
    )
    monkeypatch.setattr(
        build_concepts.writer,
        "append_concepts",
        lambda *a, **kw: (_ for _ in ()).throw(
            RuntimeError("workbook write failed")),
    )

    generate = (
        build_concepts.generate_post_learning
        if learning_kind == "post"
        else build_concepts.generate_pre_learning_from_upload
    )
    with pytest.raises(RuntimeError, match="workbook write failed"):
        generate(db, job.id, chapter.id)

    db.expire_all()
    assert (
        db.query(models.Concept)
        .filter(models.Concept.concept_title == marker)
        .count()
        == 0
    )


def test_checkpoint_history_falls_back_from_unknown_newer_stage():
    compatible = build_concepts.generation._make_concept_checkpoint(
        "pre_type_assignment",
        records=[{"concept_title": "C"}],
        question_task_inventory={"items": [], "stats": {}},
        mined_types={"types": []},
        method_row_snapshot=[],
    )
    envelope = {
        "schema_version": build_concepts.generation._CONCEPT_CHECKPOINT_SCHEMA,
        "checkpoint_format": (
            build_concepts.generation._CONCEPT_CHECKPOINT_FORMAT),
        "checkpoints": [
            compatible,
            {
                "schema_version": (
                    build_concepts.generation._CONCEPT_CHECKPOINT_SCHEMA),
                "stage": "future_incompatible_stage",
                "stage_schema_version": 99,
                "records": [{"concept_title": "future"}],
            },
        ],
    }

    restored = (
        build_concepts.generation._newest_compatible_concept_checkpoint(
            envelope)
    )

    assert restored["stage"] == "pre_type_assignment"


def test_inventory_csv_download(client, db, first_chapter):
    """The stored Question / Task Inventory downloads as an audit CSV."""
    files = {"file": ("inv.txt", io.BytesIO(
        b"## Inventory Topic 7731\nInventory concept alpha 7731"
    ), "text/plain")}
    job = client.post("/build-concepts/post-learning/uploads", files=files).json()
    convert_concept_upload(client, job["id"])
    stream_result(client.post(
        f"/build-concepts/post-learning/uploads/{job['id']}/generate",
        json={"target_chapter_id": first_chapter["id"]}))

    # Dry mode produces no inventory, so simulate what a live run stores.
    import app.models as models
    job_row = db.get(models.UploadJob, job["id"])
    job_row.question_inventory = {
        "items": [
            {"qid": "QINV-0001", "order_index": 1, "source_kind": "exercise",
             "source_label": "Exercise 1.1 Q1", "topic_hint": "T",
             "raw_task": "Simplify, p^9 ÷ p^3.", "normalized_task": "Divide powers.",
             "requires_visual": False, "requires_context": False,
             "content_objects": {"variables": ["p"]}},
            {"qid": "QINV-0002", "order_index": 2, "source_kind": "mcq",
             "source_label": "Q2", "topic_hint": "T",
             "raw_task": "Pick the value of 2^3.", "normalized_task": "Evaluate a power.",
             "requires_visual": False, "requires_context": False,
             "content_objects": {}},
        ],
        "stats": {"total_inventory_items": 2},
        "mined_types": [
            {"type_id": "TYPE-0001", "type_title": "Dividing Powers with the Same Base",
             "source_question_ids": ["QINV-0001"], "case_prompts": []},
        ],
    }
    db.commit()

    resp = client.get(f"/build-concepts/uploads/{job['id']}/inventory.csv")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "attachment" in resp.headers["content-disposition"]

    import csv
    import io as _io
    rows = list(csv.DictReader(_io.StringIO(resp.text)))
    assert len(rows) == 2
    by_qid = {r["qid"]: r for r in rows}
    assert by_qid["QINV-0001"]["raw_task"] == "Simplify, p^9 ÷ p^3."
    assert by_qid["QINV-0001"]["classified"] == "yes"
    assert by_qid["QINV-0001"]["mined_type_ids"] == "TYPE-0001"
    assert "Dividing Powers" in by_qid["QINV-0001"]["mined_type_titles"]
    # Unclassified items are visible at a glance in the audit CSV.
    assert by_qid["QINV-0002"]["classified"] == "no"
    assert by_qid["QINV-0002"]["mined_type_ids"] == ""


def test_inventory_csv_missing_returns_404(client):
    files = {"file": ("noinv.txt", io.BytesIO(b"## X\nY"), "text/plain")}
    job = client.post("/build-concepts/post-learning/uploads", files=files).json()
    resp = client.get(f"/build-concepts/uploads/{job['id']}/inventory.csv")
    assert resp.status_code == 404


def test_pre_learning_from_upload(client, first_chapter):
    files = {"file": ("doc.txt", io.BytesIO(
        b"## Foundations\nNumber line basics\nInteger operations"
    ), "text/plain")}
    job = client.post("/build-concepts/pre-learning/uploads", files=files).json()
    assert job["learning_kind"] == "pre"
    convert_concept_upload(client, job["id"])
    result = stream_result(client.post(
        f"/build-concepts/pre-learning/uploads/{job['id']}/generate",
        json={"target_chapter_id": first_chapter["id"]}))
    assert result["concepts_created"] >= 2


def test_pre_learning_from_existing_post_learning(client, first_chapter):
    result = stream_result(client.post("/build-concepts/pre-learning/from-existing", json={
        "chapter_ids": [first_chapter["id"]],
    }))
    assert result["chapters"] == 1
    assert result["concepts_created"] >= 1
    assert str(first_chapter["id"]) in {str(k) for k in result["per_chapter"]}

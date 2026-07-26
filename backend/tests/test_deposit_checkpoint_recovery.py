import copy

import pytest

from app import models
from app.services import build_concepts, openai_usage


def _concept_records(title: str) -> list[dict]:
    return [{
        "topic": "Checkpoint Topic",
        "parent_concept": "Checkpoint Parent",
        "concept_title": title,
        "concept_details": "Description: Saved checkpoint content.",
        "keywords": "",
    }]


def _usage_summary() -> dict:
    usage = openai_usage.UsageAccumulator()
    usage.add(
        model="gpt-5.6-luna",
        input_tokens=120,
        cached_input_tokens=20,
        output_tokens=30,
        reasoning_tokens=10,
    )
    return usage.summary()


def _job_with_terminal_history(
    db,
    first_chapter: dict,
    *,
    filename: str,
) -> tuple[int, int, dict]:
    expected_usage = _usage_summary()
    job = models.UploadJob(
        module="build_concepts",
        upload_type="document",
        learning_kind="post",
        filename=filename,
        mmd_text="# Checkpoint Topic\nSource body.",
        status="converted",
        openai_usage=copy.deepcopy(expected_usage),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    chapter = db.get(models.Chapter, first_chapter["id"])

    common = {
        "question_task_inventory": {"items": [], "stats": {}},
        "mined_types": {"types": []},
        "method_row_snapshot": [],
    }
    prior = build_concepts.generation._make_concept_checkpoint(
        "post_type_assignment",
        records=_concept_records("Retained 91 Percent Concept"),
        **common,
    )
    final = build_concepts.generation._make_concept_checkpoint(
        "final_content_ready",
        records=_concept_records("Rejected 98 Percent Concept"),
        **common,
    )
    checkpoint_args = {
        "fingerprint": build_concepts._generation_checkpoint_fingerprint(
            job, chapter),
        "target_identity": build_concepts._generation_target_identity(chapter),
        "target_chapter_id": chapter.id,
    }
    history = build_concepts._merge_generation_checkpoint_history(
        {}, prior, **checkpoint_args)
    job.generation_checkpoint = (
        build_concepts._merge_generation_checkpoint_history(
            history, final, **checkpoint_args)
    )
    db.commit()
    return job.id, chapter.id, expected_usage


def _fake_generation(resume_stages: list[str]):
    def generate(
        *_args,
        artifacts=None,
        resume_checkpoint=None,
        **_kwargs,
    ):
        saved = (
            build_concepts.generation
            ._newest_compatible_concept_checkpoint(resume_checkpoint)
        )
        assert saved is not None
        resume_stages.append(saved["stage"])
        if artifacts is not None:
            artifacts["question_task_inventory"] = {
                "items": [],
                "stats": {},
            }
            artifacts["mined_types"] = {"types": []}
        return copy.deepcopy(saved["records"])

    return generate


def _successful_deposit(*_args, **_kwargs):
    return [], [], {
        "written": 0,
        "sources_updated": 0,
        "parent_column": True,
    }


def _forbid_openai(*_args, **_kwargs):
    raise AssertionError("deposit checkpoint recovery must not call OpenAI")


def test_typed_deposit_failure_rewinds_final_then_retry_resumes_prior(
    db,
    first_chapter,
    monkeypatch,
):
    job_id, chapter_id, expected_usage = _job_with_terminal_history(
        db,
        first_chapter,
        filename="deposit-validation-rewind.mmd",
    )
    resume_stages: list[str] = []
    deposit_attempts = 0

    def deposit(*_args, **_kwargs):
        nonlocal deposit_attempts
        deposit_attempts += 1
        if deposit_attempts == 1:
            raise build_concepts.DepositValidationError(
                "deposit validation failed: rich_text_format")
        return _successful_deposit()

    monkeypatch.setattr(
        build_concepts.generation,
        "concepts_from_mmd",
        _fake_generation(resume_stages),
    )
    monkeypatch.setattr(
        build_concepts.generation, "_openai_json", _forbid_openai)
    monkeypatch.setattr(
        build_concepts, "_deposit_and_publish_concepts", deposit)
    monkeypatch.setattr(
        build_concepts.drive_checkpoints,
        "schedule_checkpoint_backup",
        lambda *_args, **_kwargs: None,
    )

    with pytest.raises(
        build_concepts.DepositValidationError,
        match="rich_text_format",
    ):
        build_concepts.generate_post_learning(db, job_id, chapter_id)

    db.expire_all()
    rewound = db.get(models.UploadJob, job_id)
    assert rewound.generation_checkpoint["stage"] == "post_type_assignment"
    assert [
        entry["stage"]
        for entry in rewound.generation_checkpoint["checkpoints"]
    ] == ["post_type_assignment"]
    assert rewound.openai_usage["request_count"] == (
        expected_usage["request_count"])
    assert rewound.openai_usage["total_tokens"] == expected_usage["total_tokens"]
    assert rewound.openai_usage["estimated_cost_usd"] == (
        expected_usage["estimated_cost_usd"])

    result = build_concepts.generate_post_learning(db, job_id, chapter_id)

    assert result["job_id"] == job_id
    assert resume_stages == [
        "final_content_ready",
        "post_type_assignment",
    ]
    assert deposit_attempts == 2
    db.expire_all()
    completed = db.get(models.UploadJob, job_id)
    assert completed.status == "generated"
    assert completed.generation_checkpoint == {}
    assert completed.openai_usage["request_count"] == (
        expected_usage["request_count"])
    assert completed.openai_usage["total_tokens"] == (
        expected_usage["total_tokens"])
    assert completed.openai_usage["estimated_cost_usd"] == (
        expected_usage["estimated_cost_usd"])


def test_generic_deposit_io_failure_preserves_final_checkpoint(
    db,
    first_chapter,
    monkeypatch,
):
    job_id, chapter_id, expected_usage = _job_with_terminal_history(
        db,
        first_chapter,
        filename="deposit-io-preserves-final.mmd",
    )
    resume_stages: list[str] = []

    def fail_publish(*_args, **_kwargs):
        raise OSError("workbook publish failed")

    monkeypatch.setattr(
        build_concepts.generation,
        "concepts_from_mmd",
        _fake_generation(resume_stages),
    )
    monkeypatch.setattr(
        build_concepts.generation, "_openai_json", _forbid_openai)
    monkeypatch.setattr(
        build_concepts, "_deposit_and_publish_concepts", fail_publish)
    monkeypatch.setattr(
        build_concepts.drive_checkpoints,
        "schedule_checkpoint_backup",
        lambda *_args, **_kwargs: None,
    )

    with pytest.raises(OSError, match="workbook publish failed"):
        build_concepts.generate_post_learning(db, job_id, chapter_id)

    db.expire_all()
    preserved = db.get(models.UploadJob, job_id)
    assert resume_stages == ["final_content_ready"]
    assert preserved.generation_checkpoint["stage"] == "final_content_ready"
    assert [
        entry["stage"]
        for entry in preserved.generation_checkpoint["checkpoints"]
    ] == ["post_type_assignment", "final_content_ready"]
    assert preserved.openai_usage["request_count"] == (
        expected_usage["request_count"])
    assert preserved.openai_usage["total_tokens"] == (
        expected_usage["total_tokens"])
    assert preserved.openai_usage["estimated_cost_usd"] == (
        expected_usage["estimated_cost_usd"])


def test_deposit_fatal_validator_errors_use_typed_exception(
    db,
    first_chapter,
    monkeypatch,
):
    chapter = db.get(models.Chapter, first_chapter["id"])
    monkeypatch.setattr(
        build_concepts.concept_validator,
        "validate_concept_rows",
        lambda *_args, **_kwargs: {
            "ok": False,
            "errors": [{
                "code": "rich_text_format",
                "severity": "error",
            }],
            "summary": {"warnings": 0},
        },
    )

    with pytest.raises(
        build_concepts.DepositValidationError,
        match="rich_text_format",
    ):
        build_concepts._deposit_concepts(
            db,
            chapter,
            _concept_records("Typed Deposit Failure"),
            "Post",
            "",
        )


def test_deposit_certified_hub_normalization_failure_is_typed(
    db,
    first_chapter,
    monkeypatch,
):
    chapter = db.get(models.Chapter, first_chapter["id"])
    monkeypatch.setattr(
        build_concepts.generation,
        "_normalize_activity_hubs_from_inventory",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("certified activity host drift")
        ),
    )

    with pytest.raises(
        build_concepts.DepositValidationError,
        match="certified activity host drift",
    ):
        build_concepts._deposit_concepts(
            db,
            chapter,
            _concept_records("Typed Hub Failure"),
            "Post",
            "",
            inventory={"items": [], "stats": {}},
            mined_types={"types": []},
        )

"""OpenAI usage accounting, pricing, persistence, and retry coverage."""
from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace

import pytest

from app import config, models
from app.db import SessionLocal
from app.services import generation, openai_usage, progress, uploads, workbooks


def _response(
    *,
    model: str = "gpt-5.4-mini-2026-03-17",
    input_tokens: int = 100,
    cached_tokens: int = 40,
    output_tokens: int = 20,
    reasoning_tokens: int = 8,
    content: str = "{}",
    finish_reason: str = "stop",
):
    usage = SimpleNamespace(
        prompt_tokens=input_tokens,
        completion_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        prompt_tokens_details=SimpleNamespace(cached_tokens=cached_tokens),
        completion_tokens_details=SimpleNamespace(
            reasoning_tokens=reasoning_tokens
        ),
    )
    choice = SimpleNamespace(
        message=SimpleNamespace(content=content), finish_reason=finish_reason
    )
    return SimpleNamespace(model=model, usage=usage, choices=[choice])


def test_cached_tokens_and_reasoning_are_not_double_charged():
    with openai_usage.track():
        openai_usage.record_response(
            _response(
                input_tokens=1_000,
                cached_tokens=400,
                output_tokens=200,
                reasoning_tokens=80,
            )
        )
        summary = openai_usage.current_summary()

    assert summary["request_count"] == 1
    assert summary["input_tokens"] == 1_000
    assert summary["cached_input_tokens"] == 400
    assert summary["uncached_input_tokens"] == 600
    assert summary["output_tokens"] == 200
    assert summary["reasoning_tokens"] == 80
    assert summary["total_tokens"] == 1_200
    # 600*.75/M + 400*.075/M + 200*4.50/M = $0.00138.
    assert summary["estimated_cost_usd"] == pytest.approx(0.00138)


def test_multiple_responses_aggregate_and_unknown_pricing_is_not_zero():
    with openai_usage.track():
        openai_usage.record_response(_response())
        openai_usage.record_response(
            _response(
                model="future-model",
                input_tokens=9,
                cached_tokens=99,  # clamped to the input total
                output_tokens=3,
                reasoning_tokens=99,  # clamped to the output total
            )
        )
        summary = openai_usage.current_summary()

    assert summary["request_count"] == 2
    assert summary["input_tokens"] == 109
    assert summary["cached_input_tokens"] == 49
    assert summary["reasoning_tokens"] == 11
    assert summary["model"] == "multiple"
    assert summary["pricing_complete"] is False
    assert summary["estimated_cost_usd"] is None


def test_persisted_cost_is_merged_without_historical_repricing():
    prior = {
        **openai_usage.UsageAccumulator().summary(),
        "model": "gpt-5.4-mini-2026-03-17",
        "models": [{
            "model": "gpt-5.4-mini-2026-03-17",
            "request_count": 1,
            "input_tokens": 100,
            "cached_input_tokens": 0,
            "output_tokens": 20,
            "reasoning_tokens": 0,
            "total_tokens": 120,
        }],
        "request_count": 1,
        "input_tokens": 100,
        "output_tokens": 20,
        "total_tokens": 120,
        "estimated_cost_usd": 1.25,
        "pricing_complete": True,
        "pricing_as_of": "historical-snapshot",
    }
    current = openai_usage.UsageAccumulator()
    current.add(
        model="gpt-5.4-mini-2026-03-17",
        input_tokens=100,
        cached_input_tokens=40,
        output_tokens=20,
    )

    merged = openai_usage.merge_summaries(prior, current.summary())

    assert merged["request_count"] == 2
    assert merged["estimated_cost_usd"] == pytest.approx(1.250138)
    assert merged["pricing_as_of"] == "multiple"


def test_missing_provider_usage_is_not_invented():
    with openai_usage.track():
        openai_usage.record_response(SimpleNamespace(model="gpt-5.4-mini"))
        summary = openai_usage.current_summary()
    assert summary["request_count"] == 0
    assert summary["total_tokens"] == 0
    assert summary["estimated_cost_usd"] == 0.0


def test_context_isolation_between_concurrent_jobs():
    barrier = threading.Barrier(2)
    results: dict[int, dict] = {}

    def worker(index: int) -> None:
        with openai_usage.track():
            barrier.wait()
            openai_usage.record_response(
                _response(input_tokens=index * 100, cached_tokens=0)
            )
            results[index] = openai_usage.current_summary()

    threads = [threading.Thread(target=worker, args=(i,)) for i in (1, 2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results[1]["input_tokens"] == 100
    assert results[2]["input_tokens"] == 200
    assert openai_usage.current_summary()["request_count"] == 0


def test_progress_stream_emits_usage_and_attaches_it_to_result():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()

    @app.post("/run")
    def run():
        def work():
            openai_usage.record_response(_response())
            return {"artifact": "generated.xlsx"}

        return progress.stream(work)

    response = TestClient(app).post("/run")
    events = [json.loads(line) for line in response.text.splitlines()]
    usage_events = [event for event in events if event["type"] == "usage"]
    result = next(event["data"] for event in events if event["type"] == "result")

    assert usage_events[-1]["data"]["total_tokens"] == 120
    assert result["openai_usage"]["estimated_cost_usd"] == pytest.approx(
        0.000138
    )


def test_invalid_json_retry_counts_both_billable_responses(monkeypatch):
    responses = [
        _response(content="not-json"),
        _response(content='{"rows": []}'),
    ]

    class FakeClient:
        def __init__(self):
            create = lambda **_kwargs: responses.pop(0)  # noqa: E731
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=create)
            )

    import openai
    import time

    monkeypatch.setattr(openai, "OpenAI", FakeClient)
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    generation._openai_gate = None
    try:
        with openai_usage.track():
            assert generation._openai_json("system", "user") == {"rows": []}
            summary = openai_usage.current_summary()
    finally:
        generation._openai_gate = None

    assert summary["request_count"] == 2
    assert summary["total_tokens"] == 240


def test_upload_job_usage_persists_and_resets_when_file_is_replaced(db):
    job = models.UploadJob(
        module="build_concepts", filename="old.txt", status="converted"
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    with openai_usage.track():
        openai_usage.record_response(_response())
        saved = uploads.persist_current_openai_usage(db, job.id)
    assert saved["request_count"] == 1
    assert uploads.get_job(db, job.id).openai_usage["total_tokens"] == 120

    replaced = uploads.replace_file(
        db, job.id, filename="new.txt", raw_bytes=b"replacement"
    )
    assert replaced.openai_usage == {}


def test_failed_uploaded_run_still_persists_billable_usage(db):
    job = models.UploadJob(
        module="build_assessments", filename="questions.txt", status="deposited"
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    def fail():
        openai_usage.record_response(_response())
        raise RuntimeError("generation failed")

    with openai_usage.track():
        with pytest.raises(RuntimeError, match="generation failed"):
            uploads.run_with_openai_usage(db, job.id, fail)

    db.expire_all()
    assert uploads.get_job(db, job.id).openai_usage["request_count"] == 1


def test_failed_streamed_upload_persists_exact_generation_log(db):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    job = models.UploadJob(
        module="build_concepts",
        upload_type="document",
        filename="diagnostics.txt",
        status="converted",
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    app = FastAPI()

    @app.post("/run")
    def run():
        def work():
            local = SessionLocal()
            try:
                def fail():
                    progress.log(
                        "row_index=7; concept='Electric Power'; "
                        "code='rich_text_format'",
                        level="error",
                    )
                    raise RuntimeError(
                        "final validation failed at row_index=7"
                    )

                return uploads.run_with_openai_usage(local, job.id, fail)
            finally:
                local.close()

        return progress.stream(work)

    response = TestClient(app).post("/run")

    assert "row_index=7" in response.text
    db.expire_all()
    saved = uploads.get_job(db, job.id)
    assert saved.generation_log[-1]["level"] == "error"
    assert saved.generation_log[-1]["error"]["exception_type"] == "RuntimeError"
    assert saved.generation_log[-1]["error"]["frames"][-1]["function"] == "fail"
    assert any(
        "Electric Power" in event.get("message", "")
        for event in saved.generation_log
    )
    assert "row_index=7" in saved.detail
    assert "test_openai_usage.py:" in saved.detail


def test_progress_event_limit_zero_returns_no_events():
    token = progress._history.set([{"type": "log", "message": "secret"}])
    try:
        assert progress.current_events(limit=0) == []
    finally:
        progress._history.reset(token)


def test_concurrent_runs_for_one_upload_do_not_lose_usage(db):
    job = models.UploadJob(
        module="build_concepts", filename="same-file.txt", status="converted"
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    errors: list[Exception] = []

    def worker():
        local = SessionLocal()
        try:
            with openai_usage.track():
                def work():
                    uploads.get_job(local, job.id)
                    openai_usage.record_response(_response())
                    time.sleep(0.03)
                    return {}

                uploads.run_with_openai_usage(local, job.id, work)
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)
        finally:
            local.close()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    db.expire_all()
    saved = uploads.get_job(db, job.id).openai_usage
    assert saved["request_count"] == 2
    assert saved["total_tokens"] == 240


def test_workbook_library_recovers_usage_from_sidecar(tmp_path, monkeypatch):
    monkeypatch.setattr(workbooks, "WORKBOOK_ROOT", tmp_path)
    pdf = tmp_path / "Class 08" / "Mathematics" / "chapter.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"%PDF-1.4")

    with openai_usage.track():
        openai_usage.record_response(_response())
        workbooks._persist_usage(pdf)

    entry = workbooks.library()[0]
    assert entry["openai_usage"]["total_tokens"] == 120
    assert entry["openai_usage"]["estimated_cost_usd"] == pytest.approx(
        0.000138
    )


def test_requested_model_is_the_default():
    assert config.OPENAI_MODEL == "gpt-5.4-mini-2026-03-17"

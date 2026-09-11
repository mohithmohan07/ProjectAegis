"""OpenAI usage accounting, pricing, persistence, and retry coverage."""
from __future__ import annotations

import json
import threading
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
    cache_write_tokens: int = 0,
    output_tokens: int = 20,
    reasoning_tokens: int = 8,
    content: str = "{}",
    finish_reason: str = "stop",
):
    usage = SimpleNamespace(
        prompt_tokens=input_tokens,
        completion_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        prompt_tokens_details=SimpleNamespace(
            cached_tokens=cached_tokens,
            cache_write_tokens=cache_write_tokens,
        ),
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


def test_luna_pricing_matches_standard_text_token_rates():
    with openai_usage.track():
        openai_usage.record_response(
            _response(
                model="gpt-5.6-luna",
                input_tokens=1_000,
                cached_tokens=400,
                cache_write_tokens=200,
                output_tokens=200,
                reasoning_tokens=80,
            )
        )
        summary = openai_usage.current_summary()

    # 400*$0.20/M + 400*$0.02/M + 200*$0.25/M + 200*$1.20/M
    # = $0.000378. Cache-write tokens are a subset of input tokens.
    assert summary["model"] == "gpt-5.6-luna"
    assert summary["cache_write_tokens"] == 200
    assert summary["reasoning_tokens"] == 80
    assert summary["estimated_cost_usd"] == pytest.approx(0.000378)


def test_unpriced_model_reports_incomplete_pricing_not_zero_cost():
    """An unrecognized model must be visible, never silently free."""

    with openai_usage.track():
        openai_usage.record_response(
            _response(
                model="gpt-7.0-imaginary",
                input_tokens=1_000,
                cached_tokens=400,
                cache_write_tokens=200,
                output_tokens=200,
                reasoning_tokens=80,
            )
        )
        summary = openai_usage.current_summary()

    assert summary["model"] == "gpt-7.0-imaginary"
    assert summary["request_count"] == 1
    assert summary["total_tokens"] == 1_200
    assert summary["pricing_complete"] is False
    assert summary["estimated_cost_usd"] is None


def test_luna_long_context_multiplier_is_applied_per_request():
    with openai_usage.track():
        openai_usage.record_response(
            _response(
                model="gpt-5.6-luna",
                input_tokens=273_000,
                cached_tokens=0,
                output_tokens=1_000,
                reasoning_tokens=500,
            )
        )
        summary = openai_usage.current_summary()

    # Inputs above 272K are 2x; output is 1.5x for the full request.
    assert summary["estimated_cost_usd"] == pytest.approx(0.111)


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
    assert summary["estimated_cost_usd"] is None
    assert summary["usage_complete"] is False
    assert summary["missing_usage_response_count"] == 1


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
        def __init__(self, *a, **kw):
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


def test_repeated_checkpoint_persistence_adds_the_active_run_only_once(db):
    prior = openai_usage.UsageAccumulator()
    prior.add(
        model="gpt-5.4-mini-2026-03-17",
        input_tokens=200,
        cached_input_tokens=80,
        output_tokens=40,
    )
    job = models.UploadJob(
        module="build_concepts",
        filename="checkpointed.txt",
        status="converted",
        openai_usage=prior.summary(),
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    with openai_usage.track():
        openai_usage.record_response(_response())
        first = uploads.persist_current_openai_usage(db, job.id)
        repeated = uploads.persist_current_openai_usage(db, job.id)
        openai_usage.record_response(_response())
        advanced = uploads.persist_current_openai_usage(db, job.id)

    assert first["request_count"] == 2
    assert repeated["request_count"] == 2
    assert repeated["total_tokens"] == first["total_tokens"]
    assert advanced["request_count"] == 3
    assert advanced["total_tokens"] == 480


def test_checkpointed_failure_and_refreshed_resume_add_only_new_usage(db):
    job = models.UploadJob(
        module="build_concepts",
        filename="resume-usage.txt",
        status="converted",
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    first_db = SessionLocal()
    try:
        with openai_usage.track():
            def fail_after_checkpoints():
                openai_usage.record_response(_response())
                uploads.persist_current_openai_usage(first_db, job.id)
                uploads.persist_current_openai_usage(first_db, job.id)
                raise RuntimeError("resume after refresh")

            with pytest.raises(RuntimeError, match="resume after refresh"):
                uploads.run_with_openai_usage(
                    first_db, job.id, fail_after_checkpoints
                )
    finally:
        first_db.close()

    db.expire_all()
    after_failure = uploads.get_job(db, job.id).openai_usage
    assert after_failure["request_count"] == 1
    assert after_failure["total_tokens"] == 120

    resumed_db = SessionLocal()
    try:
        with openai_usage.track():
            def resume_after_refresh():
                # ``run_with_openai_usage`` binds the durable first attempt
                # before this new response is recorded.
                assert openai_usage.visible_summary()["request_count"] == 1
                openai_usage.record_response(_response())
                uploads.persist_current_openai_usage(resumed_db, job.id)
                return {"resumed": True}

            result = uploads.run_with_openai_usage(
                resumed_db, job.id, resume_after_refresh
            )
    finally:
        resumed_db.close()

    assert result["openai_usage"]["request_count"] == 2
    assert result["openai_usage"]["total_tokens"] == 240
    db.expire_all()
    durable = uploads.get_job(db, job.id).openai_usage
    assert durable["request_count"] == 2
    assert durable["total_tokens"] == 240


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


def test_concurrent_runs_for_one_upload_fail_fast_without_double_usage(db):
    job = models.UploadJob(
        module="build_concepts", filename="same-file.txt", status="converted"
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    errors: list[Exception] = []
    first_run_entered = threading.Event()
    release_first_run = threading.Event()

    def first_worker():
        local = SessionLocal()
        try:
            with openai_usage.track():
                def work():
                    uploads.get_job(local, job.id)
                    openai_usage.record_response(_response())
                    first_run_entered.set()
                    assert release_first_run.wait(timeout=5)
                    return {}

                uploads.run_with_openai_usage(local, job.id, work)
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)
        finally:
            local.close()

    thread = threading.Thread(target=first_worker)
    thread.start()
    assert first_run_entered.wait(timeout=5)
    second = SessionLocal()
    try:
        with openai_usage.track():
            with pytest.raises(uploads.JobAlreadyRunningError):
                uploads.run_with_openai_usage(second, job.id, lambda: {})
    finally:
        second.close()
        release_first_run.set()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert not errors
    db.expire_all()
    saved = uploads.get_job(db, job.id).openai_usage
    assert saved["request_count"] == 1
    assert saved["total_tokens"] == 120


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
    assert config.OPENAI_MODEL == "gpt-5.6-luna"


def _provider_receipt(provider: str, model: str):
    """Record one deterministic receipt through the real attempt ledger."""
    with openai_usage.request_attempt(
        requested_model=model, provider=provider,
    ):
        openai_usage.record_service_started()
        openai_usage.record_response(_response(model=model))


def test_mixed_provider_breakdown_is_compact_and_cumulative_across_resume(
    monkeypatch,
):
    monkeypatch.setenv("AEGIS_USD_TO_INR_RATE", "90")
    monkeypatch.setenv("AEGIS_USD_TO_INR_AS_OF", "2026-09-08")
    with openai_usage.track():
        _provider_receipt("openai", "gpt-5.6-luna")
        _provider_receipt("gemini", "gemini-3.8-flash")
        historical = openai_usage.current_summary()

    monkeypatch.setenv("AEGIS_USD_TO_INR_RATE", "100")
    monkeypatch.setenv("AEGIS_USD_TO_INR_AS_OF", "2026-09-09")
    with openai_usage.track():
        openai_usage.bind_persisted_summary("mixed-resume", historical)
        _provider_receipt("openai", "gpt-5.6-luna")
        _provider_receipt("gemini", "gemini-3.8-flash")
        compact = openai_usage.console_summary()
        full = openai_usage.visible_summary()

    assert compact["request_attempts"] == []
    assert compact["estimated_cost_usd"] == pytest.approx(0.0003196)
    assert compact["estimated_cost_inr"] == pytest.approx(0.030362)
    assert compact["usd_to_inr_rate"] is None
    assert {row["provider"] for row in compact["providers"]} == {
        "openai", "gemini",
    }
    by_provider = {row["provider"]: row for row in compact["providers"]}
    assert by_provider["openai"]["request_count"] == 2
    assert by_provider["gemini"]["request_count"] == 2
    assert by_provider["openai"]["estimated_cost_inr"] == pytest.approx(0.006992)
    assert by_provider["gemini"]["estimated_cost_inr"] == pytest.approx(0.02337)
    assert sum(row["estimated_cost_usd"] for row in compact["providers"]) == pytest.approx(
        compact["estimated_cost_usd"]
    )
    assert full["providers"] == compact["providers"]


def test_provider_breakdown_preserves_pending_and_missing_cost_states(monkeypatch):
    monkeypatch.setenv("AEGIS_USD_TO_INR_RATE", "90")
    with openai_usage.track():
        _provider_receipt("openai", "gpt-5.6-luna")
        historical = openai_usage.current_summary()

    monkeypatch.setenv("AEGIS_USD_TO_INR_RATE", "100")
    with openai_usage.track():
        openai_usage.bind_persisted_summary("missing-resume", historical)
        with openai_usage.request_attempt(
            requested_model="gemini-3.8-flash", provider="gemini",
        ):
            openai_usage.record_service_started()
            pending = openai_usage.console_summary()
            pending_gemini = next(
                row for row in pending["providers"] if row["provider"] == "gemini"
            )
            assert pending_gemini["pending_request_count"] == 1
            assert pending_gemini["unresolved_usage_request_count"] == 0
            assert pending["usage_complete"] is True
            openai_usage.record_response(
                SimpleNamespace(model="gemini-3.8-flash", usage=None)
            )
        missing = openai_usage.console_summary()

    gemini = next(row for row in missing["providers"] if row["provider"] == "gemini")
    assert missing["estimated_cost_usd"] is None
    assert missing["known_usage_estimated_cost_usd"] == pytest.approx(
        historical["estimated_cost_usd"]
    )
    assert gemini["missing_usage_response_count"] == 1
    assert gemini["unresolved_usage_request_count"] == 1
    assert gemini["estimated_cost_usd"] is None
    assert gemini["estimated_cost_inr"] is None
    openai = next(row for row in missing["providers"] if row["provider"] == "openai")
    assert openai["estimated_cost_inr"] == historical["estimated_cost_inr"]


def test_explicit_custom_provider_stays_unknown_with_recorded_model_receipt():
    with openai_usage.track():
        _provider_receipt("custom-gateway", "gpt-5.6-luna")
        summary = openai_usage.current_summary()

    assert summary["request_attempts"][0]["provider"] == "custom-gateway"
    assert [row["provider"] for row in summary["providers"]] == ["unknown"]
    assert summary["providers"][0]["estimated_cost_usd"] == pytest.approx(
        summary["estimated_cost_usd"]
    )
    assert summary["providers"][0]["estimated_cost_inr"] == pytest.approx(
        summary["estimated_cost_inr"]
    )


def test_legacy_aggregate_pending_state_gets_unknown_provider_row():
    merged = openai_usage.merge_summaries({
        "model": "unknown",
        "request_count": 0,
        "estimated_cost_usd": 0.0,
        "known_usage_estimated_cost_usd": 0.0,
        "pending_request_count": 1,
        "unresolved_usage_request_count": 0,
        "usage_complete": True,
        "pricing_complete": True,
    })

    unknown = next(row for row in merged["providers"] if row["provider"] == "unknown")
    assert unknown["pending_request_count"] == 1
    assert unknown["unresolved_usage_request_count"] == 0
    assert unknown["known_usage_estimated_cost_usd"] == 0


def test_legacy_attempt_provider_overrides_gpt_model_prefix():
    legacy = {
        "model": "gpt-5.6-luna",
        "request_count": 1,
        "input_tokens": 100,
        "cached_input_tokens": 0,
        "output_tokens": 20,
        "reasoning_tokens": 0,
        "total_tokens": 120,
        "estimated_cost_usd": 0.25,
        "pricing_complete": True,
        "usage_complete": True,
        "request_attempts": [{
            "attempt_id": "legacy-proxy-attempt",
            "provider": "custom-gateway",
            "requested_model": "gpt-5.6-luna",
            "actual_model": "gpt-5.6-luna",
            "usage_reported": True,
            "usage_status": "reported",
            "input_tokens": 100,
            "cached_input_tokens": 0,
            "output_tokens": 20,
            "reasoning_tokens": 0,
            "total_tokens": 120,
            "estimated_cost_usd": 0.25,
            "service_started_at": 1.0,
        }],
        "attempt_details_included": True,
    }

    merged = openai_usage.merge_summaries(legacy)
    assert [row["provider"] for row in merged["providers"]] == ["unknown"]
    assert merged["providers"][0]["request_count"] == 1


def test_provider_rows_are_validated_even_on_v2_marker():
    from app.services import checkpoints

    with openai_usage.track():
        _provider_receipt("openai", "gpt-5.6-luna")
        snapshot = openai_usage.current_summary()
    snapshot["usage_schema_version"] = 2
    snapshot["providers"][0]["request_count"] = -1

    with pytest.raises(ValueError, match="runtime telemetry schema"):
        checkpoints._validate_usage(snapshot, "usage")


def test_legacy_provider_identity_survives_compact_resume_without_rewriting_history():
    import copy
    from app.services import checkpoints

    with openai_usage.track():
        _provider_receipt("custom-gateway", "gpt-5.6-luna")
        legacy = openai_usage.current_summary()
    legacy.pop("providers")
    original = copy.deepcopy(legacy)
    with openai_usage.track():
        openai_usage.bind_persisted_summary("legacy-proxy-resume", legacy)
        _provider_receipt("openai", "gpt-5.6-luna")
        compact = openai_usage.console_summary()
        full = openai_usage.visible_summary()

    assert legacy == original
    assert compact["request_attempts"] == []
    assert compact["providers"] == full["providers"]
    providers = {row["provider"]: row for row in compact["providers"]}
    assert set(providers) == {"unknown", "openai"}
    assert providers["unknown"]["estimated_cost_inr"] == legacy["estimated_cost_inr"]
    assert sum(row["estimated_cost_inr"] for row in providers.values()) == pytest.approx(
        compact["estimated_cost_inr"]
    )
    checkpoints._validate_usage(compact, "usage")


@pytest.mark.parametrize("model_currency_available", [True, False])
def test_legacy_provider_split_preserves_top_currency_when_model_detail_is_missing(
    model_currency_available,
):
    import copy

    with openai_usage.track():
        _provider_receipt("openai", "gpt-5.6-luna")
        _provider_receipt("gemini", "gemini-3.8-flash")
        legacy = openai_usage.current_summary()
    legacy.pop("providers")
    legacy["request_attempts"] = []
    legacy["attempt_details_included"] = False
    legacy["estimated_cost_usd"] = 1.0
    legacy["known_usage_estimated_cost_usd"] = 1.0
    legacy["estimated_cost_inr"] = 90.0
    legacy["known_usage_estimated_cost_inr"] = 90.0
    for model, dollars, rupees in zip(legacy["models"], [0.25, 0.75], [22.5, 67.5]):
        model["estimated_cost_usd"] = model["known_usage_estimated_cost_usd"] = dollars
        model["estimated_cost_inr"] = model["known_usage_estimated_cost_inr"] = rupees
        if not model_currency_available:
            model.pop("estimated_cost_inr")
            model.pop("known_usage_estimated_cost_inr")
    original = copy.deepcopy(legacy)
    merged = openai_usage.merge_summaries(legacy)
    assert legacy == original
    providers = merged["providers"]
    assert {row["provider"] for row in providers} == (
        {"openai", "gemini"} if model_currency_available else {"unknown"}
    )
    assert sum(row["estimated_cost_usd"] for row in providers) == 1.0
    assert sum(row["estimated_cost_inr"] for row in providers) == 90.0
    assert merged["estimated_cost_inr"] == 90.0

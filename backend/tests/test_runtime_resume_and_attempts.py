"""Paid-work preservation, bounded fanout and honest transport accounting."""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from app.services import generation, openai_usage, progress
from app.services.phase3 import kernel


def test_interrupted_critic_resumes_paid_valid_author_from_disk(tmp_path):
    state = {"interrupt": True, "authors": 0, "critics": 0}

    def author(_payload):
        state["authors"] += 1
        return {"answer": 42}

    def critic(_payload):
        state["critics"] += 1
        if state["interrupt"]:
            raise KeyboardInterrupt("process interrupted after paid author")
        return {"verdict": "dissent", "confidence": 0.99, "issues": ["check the source"]}

    kwargs = dict(
        kind="demo", unit_id="C1", envelope_sha256="source-one",
        payload={"source": ["complete source"], "prompt": "version one", "schema": "answer:int"},
        provider=author, checker=lambda response: [] if isinstance(response.get("answer"), int) else ["answer invalid"],
        critic=critic, policy_version="v1",
    )
    store = kernel.DecisionStore(tmp_path)
    with pytest.raises(KeyboardInterrupt):
        kernel.decide(**kwargs, store=store)
    assert store.keys() == []
    receipt = store.pending_authors().get(store.pending_authors().keys()[0])
    assert receipt["state"] == "pending_review"
    assert kernel.peek(**{key: value for key, value in kwargs.items() if key in {"kind", "unit_id", "envelope_sha256", "payload", "policy_version"}}, store=store) is None
    state["interrupt"] = False
    decision = kernel.decide(**kwargs, store=kernel.DecisionStore(tmp_path))
    assert state == {"interrupt": False, "authors": 1, "critics": 2}
    assert decision["state"] == "reviewed"
    assert any("check the source" in flag for flag in decision["review_flags"])
    kernel.decide(**kwargs, store=kernel.DecisionStore(tmp_path))
    assert state["authors"] == 1 and state["critics"] == 2


@pytest.mark.parametrize("changed_field", ["payload", "envelope_sha256", "policy_version"])
def test_pending_receipt_does_not_cross_source_prompt_schema_identity(tmp_path, changed_field):
    calls = []
    interrupted = [True]

    def author(payload):
        calls.append(payload)
        return {"answer": len(calls)}

    def critic(_payload):
        if interrupted[0]:
            raise KeyboardInterrupt()
        return {"verdict": "verified", "confidence": 1}

    kwargs = dict(kind="demo", unit_id="C1", envelope_sha256="s1", payload={"prompt": "p1", "schema": "schema1", "visual_identity": "digest1"}, policy_version="v1", provider=author, checker=lambda _: [], critic=critic)
    with pytest.raises(KeyboardInterrupt):
        kernel.decide(**kwargs, store=kernel.DecisionStore(tmp_path))
    interrupted[0] = False
    kwargs[changed_field] = {"prompt": "p2", "schema": "schema2", "visual_identity": "digest2"} if changed_field == "payload" else "new"
    kernel.decide(**kwargs, store=kernel.DecisionStore(tmp_path))
    assert len(calls) == 2


def test_nested_pools_do_not_multiply_workers():
    active = peak = 0
    lock = threading.Lock()

    def leaf(value):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.005)
        with lock:
            active -= 1
        return value

    result = kernel.parallel_map_in_order(
        range(8), lambda n: kernel.parallel_map_in_order(range(n * 8, n * 8 + 8), leaf, max_workers=8),
        max_workers=4,
    )
    assert result == [list(range(n * 8, n * 8 + 8)) for n in range(8)]
    assert 1 < peak <= 4


def test_later_failure_is_observed_while_first_input_is_still_running():
    started = []

    def worker(n):
        started.append(n)
        if n == 0:
            assert kernel._pool_cancel.get().wait(1), "failure must be observed before first result finishes"
            return n
        raise RuntimeError("second failed")

    with pytest.raises(RuntimeError, match="second failed"):
        kernel.parallel_map_in_order(range(20), worker, max_workers=2)
    assert set(started) == {0, 1}


def _response(content='{"ok": true}', *, usage=True):
    return SimpleNamespace(
        id="chat-response-1", _request_id="provider-request-1", model="gpt-5.6-luna", service_tier="default",
        choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason="stop")],
        usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20, total_tokens=120) if usage else None,
    )


def test_adapter_records_invalid_draft_and_success_as_separate_attempts(monkeypatch):
    responses = [_response("broken JSON"), _response()]
    monkeypatch.setattr("openai.OpenAI", lambda **kwargs: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs: responses.pop(0)))))
    monkeypatch.setattr(openai_usage.time, "sleep", lambda _: None)
    with openai_usage.track():
        progress.step("Author")
        assert generation._openai_json("json", "json") == {"ok": True}
        summary = openai_usage.current_summary()
    assert summary["request_count"] == summary["provider_request_count"] == summary["attempt_count"] == 2
    first, second = summary["request_attempts"]
    assert [first["outcome"], second["outcome"]] == ["invalid_json", "success"]
    assert first["response_id"] == "chat-response-1"
    assert first["request_id"] == "provider-request-1"
    assert first["requested_reasoning_effort"]
    assert first["actual_reasoning_effort"] is None  # provider did not report it
    assert first["actual_service_tier"] == "default"
    assert first["queued_at"] <= first["service_started_at"] <= first["service_ended_at"] <= first["ended_at"]
    assert first["backoff_intervals"][0]["requested_seconds"] == 2
    assert summary["attempt_coverage_complete"]
    assert summary["cost_by_stage_lane_model"][0]["total_tokens"] == 240


def test_missing_usage_and_failed_sent_requests_remain_unknown_after_persistence_merge():
    with openai_usage.track():
        with openai_usage.request_attempt(requested_model="gpt-5.6-luna"):
            openai_usage.record_service_started()
            openai_usage.record_response(_response(usage=False))
            openai_usage.record_service_ended()
        summary = openai_usage.current_summary()
    merged = openai_usage.merge_summaries(summary, {})
    assert merged["request_count"] == 0 and merged["provider_request_count"] == 1
    assert merged["estimated_cost_usd"] is None
    assert merged["usage_complete"] is False
    assert merged["missing_usage_response_count"] == 1
    assert merged["request_attempts"][0]["usage_reported"] is False


def test_replay_mechanical_work_has_time_but_no_api_cost(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(openai_usage.time, "monotonic", lambda: clock[0])
    with openai_usage.track() as accumulator:
        accumulator.started_monotonic = 100.0
        openai_usage.mark_stage("Workbook replay")
        clock[0] = 105.0
        summary = openai_usage.current_summary()
    assert summary["elapsed_seconds"] == 5
    assert summary["estimated_cost_usd"] == 0
    assert summary["request_count"] == summary["attempt_count"] == 0
    assert summary["stage_timings"] == [{"stage": "Workbook replay", "elapsed_seconds": 5}]


def test_parallel_stage_duration_is_reported_once_and_cost_matrix_preserves_saved_prices():
    with openai_usage.track():
        progress.step("Review")
        def work(lane):
            with progress.label_scope(lane):
                with openai_usage.request_attempt(requested_model="gpt-5.6-luna"):
                    openai_usage.record_service_started()
                    openai_usage.record_response(_response())
                    openai_usage.record_service_ended()
        kernel.parallel_map_in_order(["Pre", "Post"], work, max_workers=2)
        summary = openai_usage.current_summary()
    assert len(summary["stage_timings"]) == 1
    assert len(summary["cost_by_stage_lane_model"]) == 2
    assert {row["elapsed_scope"] for row in summary["stages"]} == {"shared_stage_window_do_not_sum_lanes"}
    summary["cost_by_stage_lane_model"][0]["estimated_cost_usd"] = 1.234
    merged = openai_usage.merge_summaries(summary, {})
    assert merged["cost_by_stage_lane_model"][0]["estimated_cost_usd"] == 1.234
    assert merged["provider_request_count"] == 2


def test_provider_error_without_usage_does_not_become_a_free_run(monkeypatch):
    import httpx
    import openai

    def fail(**kwargs):
        raise openai.APIConnectionError(request=httpx.Request("POST", "https://example.invalid"))

    monkeypatch.setattr(openai, "OpenAI", lambda **kwargs: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=fail))))
    with openai_usage.track():
        with pytest.raises(RuntimeError, match="unavailable"):
            generation._openai_json("json", "json", single_attempt=True)
        summary = openai_usage.current_summary()
    assert summary["provider_request_count"] == 1
    assert summary["request_count"] == 0
    assert summary["estimated_cost_usd"] is None
    assert summary["request_attempts"][0]["outcome"] == "provider_error"
    assert summary["request_attempts"][0]["error_type"] == "APIConnectionError"


def test_compact_console_retains_totals_without_restreaming_all_attempts():
    with openai_usage.track():
        with openai_usage.request_attempt(requested_model="gpt-5.6-luna"):
            openai_usage.record_service_started()
            openai_usage.record_response(_response())
            openai_usage.record_service_ended()
        baseline = openai_usage.current_summary()
    with openai_usage.track():
        openai_usage.bind_persisted_summary("job-test", baseline)
        with openai_usage.request_attempt(requested_model="gpt-5.6-luna"):
            openai_usage.record_service_started()
            openai_usage.record_response(_response())
            openai_usage.record_service_ended()
        compact = openai_usage.console_summary()
        complete = openai_usage.visible_summary()
    assert compact["provider_request_count"] == complete["provider_request_count"] == 2
    assert compact["attempt_count"] == complete["attempt_count"] == 2
    assert compact["request_attempts"] == [] and len(complete["request_attempts"]) == 2
    assert compact["estimated_cost_usd"] == complete["estimated_cost_usd"]


def test_unknown_tier_retains_known_cost_without_pricing_it_at_standard_rates():
    with openai_usage.track():
        openai_usage.record_response(_response())
        unpriced = _response()
        unpriced.service_tier = "priority"
        openai_usage.record_response(unpriced)
        summary = openai_usage.current_summary()
    merged = openai_usage.merge_summaries(summary, {})
    assert merged["estimated_cost_usd"] is None
    assert merged["known_usage_estimated_cost_usd"] == pytest.approx(0.000044)
    assert merged["cost_by_stage_lane_model"][0]["known_usage_estimated_cost_usd"] == pytest.approx(0.000044)


@pytest.mark.parametrize("adapter", ["strict_source", "legacy_source"])
def test_source_transports_record_each_physical_attempt(monkeypatch, adapter):
    from app.services import canonical_source_phase22 as phase22
    from app.services import canonical_source_phase34_structured_output_contract as phase34

    responses = [_response("broken JSON"), _response()]
    monkeypatch.setattr("openai.OpenAI", lambda **kwargs: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs: responses.pop(0)))))
    monkeypatch.setattr(openai_usage.time, "sleep", lambda _: None)
    caller = (
        phase34._resilient_openai_multimodal_json if adapter == "strict_source"
        else phase22._PHASE34_ORIGINAL_OPENAI_MULTIMODAL_JSON
    )
    with openai_usage.track():
        progress.step("Source audit")
        result = caller(
            system="Return JSON", prompt="Audit the complete source", pages=[],
            response_schema={"name": "source_test", "strict": True, "schema": {
                "type": "object", "properties": {"ok": {"type": "boolean"}},
                "required": ["ok"], "additionalProperties": False,
            }},
        )
        summary = openai_usage.current_summary()
    assert result == {"ok": True}
    assert summary["request_count"] == summary["provider_request_count"] == 2
    assert [row["outcome"] for row in summary["request_attempts"]] == ["invalid_json", "success"]
    assert summary["untracked_response_count"] == 0
    assert summary["cost_by_stage_lane_model"][0]["stage"] == "Source audit"


def test_concurrent_same_identity_reviews_the_immutable_pending_winner():
    barrier = threading.Barrier(2)
    calls = []
    reviewed = []
    lock = threading.Lock()
    store = kernel.DecisionStore()

    def author(_payload):
        with lock:
            value = len(calls)
            calls.append(value)
        barrier.wait(timeout=2)
        return {"value": value}

    def critic(payload):
        reviewed.append(payload["proposed_decision"])
        return {"verdict": "verified", "confidence": 1}

    def checker(_response):
        return []

    def work(_index):
        return kernel.decide(
            kind="same", unit_id="same", envelope_sha256="same", payload={"schema": "v1"},
            provider=author, checker=checker, critic=critic, store=store,
        )

    decisions = kernel.parallel_map_in_order([0, 1], work, max_workers=2)
    assert len(reviewed) == 2
    assert reviewed[0] == reviewed[1] == decisions[0]["response"] == decisions[1]["response"]


def test_nested_mechanical_spans_do_not_double_count_wall_or_thread_cpu(monkeypatch):
    clock = {"wall": 100.0, "cpu": 0.0}
    monkeypatch.setattr(openai_usage.time, "time", lambda: clock["wall"])
    monkeypatch.setattr(openai_usage.time, "monotonic", lambda: clock["wall"])
    monkeypatch.setattr(openai_usage.time, "thread_time", lambda: clock["cpu"])
    with openai_usage.track():
        with openai_usage.mechanical_span("validation"):
            clock.update(wall=105.0, cpu=2.0)
            with openai_usage.mechanical_span("serialization"):
                clock.update(wall=108.0, cpu=3.0)
            clock.update(wall=110.0, cpu=4.0)
        summary = openai_usage.current_summary()
    assert summary["mechanical_wall_seconds"] == 10
    assert summary["mechanical_thread_cpu_seconds"] == 4
    assert summary["mechanical_cpu_complete"] is True
    outer, inner = summary["mechanical_spans"]
    assert inner["parent_span_id"] == outer["span_id"]
    assert inner["thread_cpu_seconds"] == 1
    merged = openai_usage.merge_summaries(summary, {})
    assert merged["mechanical_span_count"] == 2
    assert merged["mechanical_thread_cpu_seconds"] == 4
    assert merged["mechanical_wall_seconds"] == 10


def test_actual_workbook_serialization_and_readback_record_cpu_without_api_calls():
    import openpyxl
    from app.bulk_import import assessment_workbook

    workbook = openpyxl.Workbook()
    workbook.active.append(["Header", "Value"])
    workbook.active.append(["item", 3])
    with openai_usage.track():
        data = assessment_workbook._workbook_bytes(workbook)
        parsed = assessment_workbook.parse_workbook(data)
        summary = openai_usage.current_summary()
    assert data.startswith(b"PK") and parsed["sheets"]
    assert {row["operation"] for row in summary["mechanical_spans"]} == {"workbook.serialize", "workbook.parse"}
    assert all(row["outcome"] == "success" and row["thread_cpu_seconds"] >= 0 for row in summary["mechanical_spans"])
    assert summary["mechanical_wall_seconds"] > 0
    assert summary["request_count"] == summary["provider_request_count"] == 0
    assert summary["estimated_cost_usd"] == 0


def _versioned_telemetry_sample():
    with openai_usage.track():
        with openai_usage.mechanical_span("workbook.serialize"):
            pass
        with openai_usage.request_attempt(requested_model="gpt-5.6-luna"):
            openai_usage.record_service_started()
            openai_usage.record_response(_response())
            openai_usage.record_service_ended()
        return openai_usage.current_summary()


@pytest.mark.parametrize("path,value", [
    (("usage_schema_version",), 4),
    (("usage_schema_version",), 2.0),
    (("request_attempts", 0, "queue_seconds"), -1),
    (("request_attempts", 0, "usage_reported"), "yes"),
    (("request_attempts", 0, "outcome"), "invented-state"),
    (("request_attempts", 0, "unknown_field"), {"untrusted": "data"}),
    (("mechanical_spans", 0, "thread_cpu_seconds"), float("nan")),
    (("mechanical_spans", 0, "cpu_scope"), "includes-all-Fly-machines"),
    (("cost_by_stage_lane_model", 0, "cached_input_tokens"), 999),
    (("stages", 0, "provider_request_count"), True),
])
def test_checkpoint_telemetry_rejects_untyped_unknown_or_impossible_fields(path, value):
    from app.services import checkpoints

    summary = _versioned_telemetry_sample()
    checkpoints._validate_usage(summary, "usage")
    cursor = summary
    for key in path[:-1]:
        cursor = cursor[key]
    cursor[path[-1]] = value
    with pytest.raises(ValueError):
        checkpoints._validate_usage(summary, "usage")

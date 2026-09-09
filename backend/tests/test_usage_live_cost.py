"""Recorded costs survive live calls, unknown receipts and resumed segments."""
from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from app.services import checkpoints, openai_usage, progress


def response(*, tier="default", usage=True):
    return SimpleNamespace(
        model="gpt-5.6-luna", service_tier=tier,
        usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20, total_tokens=120)
        if usage else None,
    )


def paid_response():
    with openai_usage.request_attempt(requested_model="gpt-5.6-luna"):
        openai_usage.record_service_started()
        openai_usage.record_service_ended()
        openai_usage.record_response(response())


def test_pending_call_keeps_recorded_cost_then_timeout_and_retry_preserve_subtotal():
    with openai_usage.track():
        progress.step("Question review")
        paid_response()
        with pytest.raises(TimeoutError):
            with openai_usage.request_attempt(requested_model="gpt-5.6-luna"):
                queued = openai_usage.current_summary()
                assert queued["pending_request_count"] == 0
                openai_usage.record_service_started()
                for snapshot in (openai_usage.current_summary(), openai_usage.console_summary()):
                    assert snapshot["pending_request_count"] == 1
                    assert snapshot["unresolved_usage_request_count"] == 0
                    assert snapshot["usage_complete"] is True
                    assert snapshot["estimated_cost_usd"] == pytest.approx(0.000044)
                    assert snapshot["stages"][0]["estimated_cost_usd"] == pytest.approx(0.000044)
                raise TimeoutError("provider has no receipt")
        paid_response()
        snapshot = openai_usage.current_summary()
        checkpoints._validate_usage(snapshot, "usage")
    for ledger in (snapshot, openai_usage.merge_summaries(snapshot, {})):
        assert ledger["pending_request_count"] == 0
        assert ledger["unresolved_usage_request_count"] == 1
        assert ledger["usage_complete"] is False
        assert ledger["pricing_complete"] is True
        assert ledger["estimated_cost_usd"] is None
        assert ledger["known_usage_estimated_cost_usd"] == pytest.approx(0.000088)
        assert ledger["stages"][0]["known_usage_estimated_cost_usd"] == pytest.approx(0.000088)
        assert ledger["stages"][0]["unresolved_usage_request_count"] == 1
        assert ledger["stages"][0]["pricing_complete"] is True


def test_usage_is_pending_between_transport_end_and_response_accounting():
    with openai_usage.track():
        with openai_usage.request_attempt(requested_model="gpt-5.6-luna"):
            openai_usage.record_service_started()
            openai_usage.record_service_ended()
            assert openai_usage.current_summary()["pending_request_count"] == 1
            openai_usage.record_response(response())
            current = openai_usage.current_summary()
            assert current["pending_request_count"] == current["unresolved_usage_request_count"] == 0
            assert current["usage_complete"] is True


def test_queue_timeout_has_no_unknown_provider_charge():
    with openai_usage.track():
        with pytest.raises(TimeoutError):
            with openai_usage.request_attempt(requested_model="gpt-5.6-luna"):
                raise TimeoutError("never sent")
        current = openai_usage.current_summary()
    assert current["provider_request_count"] == 0
    assert current["unresolved_usage_request_count"] == 0
    assert current["estimated_cost_usd"] == 0


@pytest.mark.parametrize("tracked", [True, False])
def test_missing_receipt_streams_immediately_and_attributes_unknown_stage(tracked, monkeypatch):
    events = []
    monkeypatch.setattr(progress, "usage", lambda data: events.append(data))
    with openai_usage.track():
        progress.step("Rubric review")
        paid_response()
        baseline = openai_usage.current_summary()
    with openai_usage.track():
        openai_usage.bind_persisted_summary("job", baseline)
        progress.step("Rubric review")
        if tracked:
            with openai_usage.request_attempt(requested_model="gpt-5.6-luna"):
                openai_usage.record_service_started()
                assert events[-1]["pending_request_count"] == 1
                openai_usage.record_response(response(usage=False))
                # Already visible before the attempt context is closed.
                assert events[-1]["unresolved_usage_request_count"] == 1
        else:
            openai_usage.record_response(response(usage=False))
        current = events[-1]
    assert current["pending_request_count"] == 0
    assert current["missing_usage_response_count"] == 1
    assert current["unresolved_usage_request_count"] == 1
    assert current["known_usage_estimated_cost_usd"] == pytest.approx(0.000044)
    assert current["stages"][0]["unresolved_usage_request_count"] == 1
    assert current["stages"][0]["usage_complete"] is False
    assert current["stages"][0]["estimated_cost_usd"] is None
    assert current["request_attempts"] == []


def test_stage_subtotal_continues_after_unpriced_response_and_survives_resume():
    with openai_usage.track():
        progress.step("Master")
        paid_response()
        openai_usage.record_response(response(tier="priority"))
        paid_response()
        saved = openai_usage.current_summary()
    with openai_usage.track():
        openai_usage.bind_persisted_summary("job", saved)
        progress.step("Master")
        paid_response()
        compact = openai_usage.console_summary()
        full = openai_usage.visible_summary()
    for current in (compact, full):
        assert current["estimated_cost_usd"] is None
        assert current["unresolved_usage_request_count"] == 0
        assert current["usage_complete"] is True
        assert current["known_usage_estimated_cost_usd"] == pytest.approx(0.000132)
        assert current["stages"][0]["estimated_cost_usd"] is None
        assert current["stages"][0]["known_usage_estimated_cost_usd"] == pytest.approx(0.000132)
        checkpoints._validate_usage(current, "usage")


def test_interrupted_pending_snapshot_becomes_unresolved_baseline_without_mutating_history():
    with openai_usage.track():
        progress.step("Master")
        paid_response()
        with openai_usage.request_attempt(requested_model="gpt-5.6-luna"):
            openai_usage.record_service_started()
            interrupted = openai_usage.current_summary()
    original = copy.deepcopy(interrupted)
    with openai_usage.track():
        first = openai_usage.bind_persisted_summary("job", interrupted)
        paid_response()
        current = openai_usage.bind_persisted_summary("job", first)
    assert interrupted == original
    assert current["pending_request_count"] == 0
    assert current["unresolved_usage_request_count"] == 1
    assert current["estimated_cost_usd"] is None
    assert current["known_usage_estimated_cost_usd"] == pytest.approx(0.000088)
    assert current["stages"][0]["pending_request_count"] == 0
    assert current["stages"][0]["unresolved_usage_request_count"] == 1
    assert current["request_attempts"][1]["ended_at"] is None
    checkpoints._validate_usage(current, "usage")


def test_v2_and_unversioned_checkpoints_stay_readable_without_repricing_or_mutation():
    with openai_usage.track():
        paid_response()
        saved = openai_usage.current_summary()
    saved["usage_schema_version"] = 2
    for key in ("pending_request_count", "unresolved_usage_request_count"):
        saved.pop(key)
    for row in saved["stages"]:
        for key in ("pending_request_count", "unresolved_usage_request_count",
                    "known_usage_estimated_cost_usd", "missing_usage_response_count"):
            row.pop(key)
    original = copy.deepcopy(saved)
    checkpoints._validate_usage(saved, "usage")
    assert saved == original
    merged = openai_usage.merge_summaries(saved)
    assert merged["estimated_cost_usd"] == saved["estimated_cost_usd"]
    assert merged["known_usage_estimated_cost_usd"] == saved["estimated_cost_usd"]
    checkpoints._validate_usage(merged, "usage")
    legacy = {"request_count": 1, "model": "historical-model", "estimated_cost_usd": 1.23}
    checkpoints._validate_usage(legacy, "usage")
    assert openai_usage.merge_summaries(legacy)["known_usage_estimated_cost_usd"] == 1.23


def test_v2_stage_known_subtotal_is_recovered_from_saved_matrix():
    with openai_usage.track():
        paid_response()
        openai_usage.record_response(response(tier="priority"))
        saved = openai_usage.current_summary()
    saved["stages"][0].pop("known_usage_estimated_cost_usd")
    saved["cost_by_stage_lane_model"][0]["known_usage_estimated_cost_usd"] = 9.876
    merged = openai_usage.merge_summaries(saved)
    assert merged["stages"][0]["estimated_cost_usd"] is None
    assert merged["stages"][0]["known_usage_estimated_cost_usd"] == 9.876


@pytest.mark.parametrize("field", ["pending_request_count", "unresolved_usage_request_count"])
def test_v3_requires_explicit_request_states(field):
    current = openai_usage.UsageAccumulator().summary()
    current.pop(field)
    with pytest.raises(ValueError, match="runtime telemetry schema"):
        checkpoints._validate_usage(current, "usage")

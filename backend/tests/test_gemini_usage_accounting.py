"""Provider receipts, dated rates and frozen INR survive stream/retry/resume."""
from __future__ import annotations

import copy
from datetime import datetime, timezone
from decimal import Decimal
import json

import pytest

from app.services import checkpoints, openai_usage, progress, uploads, usage_currency


def _stamp(value):
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp()


@pytest.fixture(autouse=True)
def fixed_quotes(monkeypatch):
    monkeypatch.setenv("AEGIS_USD_TO_INR_RATE", "90")
    monkeypatch.setenv("AEGIS_USD_TO_INR_AS_OF", "2026-09-08")
    for kind in ("INPUT", "CACHED_INPUT", "OUTPUT"):
        monkeypatch.delenv(f"AEGIS_GEMINI_{kind}_PRICE_PER_M", raising=False)
    # Model price dates are controlled independently from FX observations.
    original = openai_usage._pricing_date
    monkeypatch.setattr(openai_usage, "_pricing_date", lambda priced_at=None: original(
        _stamp("2026-09-09") if priced_at is None else priced_at))


def _response(*, completion=200, reasoning=80, total=1280, model="gemini-3.8-flash"):
    return {
        "id": "gemini-response", "model": model,
        "usage": {
            "prompt_tokens": 1000, "completion_tokens": completion,
            "total_tokens": total,
            "prompt_tokens_details": {"cached_tokens": 400},
            "completion_tokens_details": {"reasoning_tokens": reasoning},
        },
    }


def _paid(response=None, *, service_date="2026-09-09", twice=False):
    with openai_usage.request_attempt(requested_model="gemini-3.8-flash", provider="gemini",
                                      purpose="question_review", reasoning_effort="high"):
        openai_usage.record_service_started()
        openai_usage._active_attempt.get()["service_started_at"] = _stamp(service_date)
        openai_usage.record_response(response or _response())
        if twice:
            openai_usage.record_response(response or _response())
        openai_usage.record_service_ended()
        openai_usage.record_attempt_outcome("success")


@pytest.mark.parametrize("service_date,cost,policy", [
    ("2026-12-31T23:59:59", 0.00153, "gemini-flash-standard-through-2026-12-31"),
    ("2027-01-01T00:00:00", 0.00306, "gemini-flash-standard-from-2027-01-01"),
])
@pytest.mark.parametrize("completion,reasoning", [(200, 80), (280, 80), (200, None)])
def test_gemini_temporal_pricing_reconciles_reported_total_without_double_thinking(
    service_date, cost, policy, completion, reasoning,
):
    with openai_usage.track():
        _paid(_response(completion=completion, reasoning=reasoning), service_date=service_date)
        ledger = openai_usage.current_summary()
    row = ledger["request_attempts"][0]
    assert ledger["estimated_cost_usd"] == pytest.approx(cost)
    assert ledger["estimated_cost_inr"] == pytest.approx(cost * 90)
    assert ledger["input_tokens"] == 1000
    assert ledger["output_tokens"] == 280
    assert ledger["reasoning_tokens"] == (reasoning or 0)
    assert ledger["total_tokens"] == 1280
    assert row["reported_output_tokens"] == completion
    assert row["reported_reasoning_tokens"] == reasoning
    assert row["reported_total_tokens"] == row["total_tokens"] == 1280
    assert row["provider"] == "gemini"
    assert row["actual_model"] == "gemini-3.8-flash"
    assert row["pricing_policy"] == policy
    assert row["pricing_effective_date"] == service_date[:10]
    assert row["usage_accounting_basis"] == "gemini_reported_total_minus_prompt"
    assert row["pricing_source"].endswith("#gemini-3.8-flash")
    checkpoints._validate_usage(json.loads(json.dumps(ledger)), "usage")


@pytest.mark.parametrize("completion,total", [(200, None), (200, 999), (None, 1280)])
def test_incomplete_receipt_keeps_reported_counts_and_unknown_cost(completion, total):
    with openai_usage.track():
        _paid(_response(completion=completion, total=total))
        ledger = openai_usage.current_summary()
    row = ledger["request_attempts"][0]
    assert row["reported_input_tokens"] == ledger["input_tokens"] == 1000
    assert row["reported_output_tokens"] == completion
    assert row["reported_total_tokens"] == total
    assert row["usage_status"] == "incomplete"
    assert ledger["usage_complete"] is False
    assert ledger["estimated_cost_usd"] is None
    assert ledger["estimated_cost_inr"] is None
    assert ledger["known_usage_estimated_cost_inr"] == 0
    checkpoints._validate_usage(ledger, "usage")


def test_unknown_gemini_is_unpriced_until_all_explicit_overrides(monkeypatch):
    assert openai_usage._pricing_for("gemini-unverified-model") is None
    monkeypatch.setenv("AEGIS_GEMINI_INPUT_PRICE_PER_M", "1")
    assert openai_usage._pricing_for("gemini-unverified-model") is None
    monkeypatch.setenv("AEGIS_GEMINI_CACHED_INPUT_PRICE_PER_M", "0.1")
    monkeypatch.setenv("AEGIS_GEMINI_OUTPUT_PRICE_PER_M", "5")
    assert openai_usage._pricing_for("gemini-unverified-model").output_per_million == Decimal("5")
    assert openai_usage._pricing_for("gemini-3.8-flash").input_per_million == Decimal("1")
    monkeypatch.setenv("AEGIS_GEMINI_INPUT_PRICE_PER_M", "NaN")
    assert openai_usage._pricing_for("gemini-unverified-model") is None
    assert openai_usage._pricing_for("gemini-3.8-flash").input_per_million == Decimal("0.75")
    assert openai_usage._pricing_for("gemini-3.8-flash-lite") is None


def test_request_and_run_inr_stream_freezes_quote_and_deduplicates_accounting(monkeypatch, caplog):
    events = []
    logs = []
    monkeypatch.setattr(progress, "usage", lambda row: events.append(copy.deepcopy(row)))
    monkeypatch.setattr(progress, "log", lambda message, **kwargs: logs.append(message))
    with caplog.at_level("INFO", logger=openai_usage.__name__):
        with openai_usage.track():
            progress.step("Source questions")
            _paid(twice=True)
            monkeypatch.setenv("AEGIS_USD_TO_INR_RATE", "100")
            _paid()
            ledger = openai_usage.current_summary()
    assert ledger["request_count"] == ledger["attempt_count"] == 2
    assert ledger["estimated_cost_inr"] == pytest.approx(0.2754)
    assert ledger["usd_to_inr_rate"] == "90"
    assert all(row["estimated_cost_inr"] == pytest.approx(0.1377) for row in ledger["request_attempts"])
    receipt_events = [row for row in events if (row.get("latest_request") or {}).get("usage_status") == "reported"]
    assert len(receipt_events) == 2
    assert receipt_events[-1]["request_attempts"] == []
    assert receipt_events[-1]["latest_request"]["estimated_cost_inr"] == pytest.approx(0.1377)
    assert receipt_events[-1]["estimated_cost_inr"] == ledger["estimated_cost_inr"]
    assert "request_estimate_inr=0.1377" in caplog.text
    assert "cumulative_estimate_inr=0.2754" in caplog.text
    charge_logs = [message for message in logs if message.startswith("API estimate")]
    assert len(charge_logs) == 2
    assert "gemini/gemini-3.8-flash | request ₹0.1377 | cumulative ₹0.1377" in charge_logs[0]
    assert "request ₹0.1377 | cumulative ₹0.2754" in charge_logs[1]
    for event in events:
        checkpoints._validate_usage(json.loads(json.dumps(event)), "live_usage")
    for field in ("models", "stages", "cost_by_stage_lane_model"):
        assert ledger[field][0]["estimated_cost_inr"] == ledger["estimated_cost_inr"]


def test_import_resume_preserves_frozen_prices_fx_and_repeated_saves(db, monkeypatch):
    from tests.test_concept_checkpoint_bundles import _job

    job = _job(db)
    with openai_usage.track():
        _paid()
        historical = openai_usage.current_summary()
    job.openai_usage = historical
    db.commit()
    _, raw = checkpoints.export_bundle(db, job.id)
    restored = checkpoints.import_bundle(db, raw)
    assert restored.openai_usage == historical
    monkeypatch.setenv("AEGIS_USD_TO_INR_RATE", "100")
    logs = []
    monkeypatch.setattr(progress, "log", lambda message, **kwargs: logs.append(message))
    with openai_usage.track():
        openai_usage.bind_persisted_summary(str(restored.id), restored.openai_usage)
        _paid(service_date="2027-01-01")
        saved = uploads.persist_current_openai_usage(db, restored.id)
        repeated = uploads.persist_current_openai_usage(db, restored.id)
    assert saved["request_count"] == repeated["request_count"] == 2
    assert saved["estimated_cost_usd"] == pytest.approx(0.00459)
    assert saved["estimated_cost_inr"] == repeated["estimated_cost_inr"] == pytest.approx(0.4437)
    assert saved["usd_to_inr_rate"] is None
    assert saved["inr_conversion_complete"] is True
    assert saved["request_attempts"][0] == historical["request_attempts"][0]
    assert saved["request_attempts"][1]["usd_to_inr_rate"] == "100"
    assert len([message for message in logs if message.startswith("API estimate")]) == 1
    assert any("request ₹0.3060 | cumulative ₹0.4437" in message for message in logs)
    for field in ("models", "stages", "cost_by_stage_lane_model"):
        assert saved[field][0]["estimated_cost_inr"] == saved["estimated_cost_inr"]
    checkpoints._validate_usage(saved, "resumed_usage")


def test_legacy_usd_only_is_not_reconverted_and_errors_keep_known_inr(monkeypatch):
    with openai_usage.track():
        _paid()
        current = openai_usage.current_summary()
    legacy = {"request_count": 1, "model": "old-model", "estimated_cost_usd": 1.25}
    merged = openai_usage.merge_summaries(legacy, current)
    assert merged["estimated_cost_usd"] == pytest.approx(1.25153)
    assert merged["estimated_cost_inr"] is None
    assert merged["known_usage_estimated_cost_inr"] == pytest.approx(0.1377)
    assert merged["inr_conversion_complete"] is False
    assert legacy == {"request_count": 1, "model": "old-model", "estimated_cost_usd": 1.25}
    with openai_usage.track():
        openai_usage.bind_persisted_summary("resume", current)
        with pytest.raises(TimeoutError):
            with openai_usage.request_attempt(requested_model="gemini-3.8-flash", provider="gemini"):
                openai_usage.record_service_started()
                raise TimeoutError("no provider receipt")
        failed = openai_usage.visible_summary()
    assert failed["estimated_cost_inr"] is None
    assert failed["known_usage_estimated_cost_inr"] == pytest.approx(0.1377)
    assert failed["unresolved_usage_request_count"] == 1
    assert failed["latest_request"]["provider"] == "gemini"
    assert failed["latest_request"].get("estimated_cost_inr") is None


def test_invalid_fx_preserves_billable_usd_receipt_without_retry(monkeypatch):
    monkeypatch.setenv("AEGIS_USD_TO_INR_RATE", "NaN")
    logs = []
    monkeypatch.setattr(progress, "log", lambda message, **kwargs: logs.append(message))
    with openai_usage.track():
        _paid()
        ledger = openai_usage.current_summary()
    assert ledger["request_count"] == 1
    assert ledger["estimated_cost_usd"] == pytest.approx(0.00153)
    assert ledger["usage_complete"] is True
    assert ledger["estimated_cost_inr"] is None
    assert ledger["inr_conversion_complete"] is False
    assert ledger["request_attempts"][0]["usd_to_inr_rate"] is None
    assert any("request unavailable | cumulative known ₹0.000000 (total unavailable)" in message for message in logs)

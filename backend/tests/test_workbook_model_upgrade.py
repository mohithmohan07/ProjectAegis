"""Workbook-only Luna upgrade: actual transport, defaults, and isolation."""
from types import SimpleNamespace
from decimal import Decimal

import pytest
from aegis_pipeline import openai_policy
from app.services import workbooks, openai_usage, model_provider

workbooks._vendor()
import gpt_writer
from config import DEFAULT_CONFIG


def test_workbook_default_is_independent_of_global_model(monkeypatch):
    monkeypatch.setenv("AEGIS_OPENAI_MODEL", "gpt-5.6-luna")
    monkeypatch.delenv("AEGIS_WORKBOOK_OPENAI_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert DEFAULT_CONFIG["openai_model"] == "gpt-6-luna"
    assert gpt_writer.GPTWriter({}).model == "gpt-6-luna"
    assert openai_policy.configured_openai_model() == "gpt-5.6-luna"
    assert model_provider.active_model() == "gpt-5.6-luna"
    monkeypatch.setenv("AEGIS_WORKBOOK_OPENAI_MODEL", "gpt-5.6-luna")
    assert gpt_writer.GPTWriter({}).model == "gpt-5.6-luna"
    assert gpt_writer.GPTWriter({"openai_model": "explicit-model"}).model == "explicit-model"


@pytest.mark.parametrize("purpose", ["workbook_planning", "workbook_authoring"])
def test_workbook_request_uses_luna_6_with_existing_effort(monkeypatch, purpose):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AEGIS_WORKBOOK_OPENAI_MODEL", raising=False)
    calls = []
    response = SimpleNamespace(choices=[SimpleNamespace(
        finish_reason="stop", message=SimpleNamespace(content='{"ok":true}')
    )])
    writer = gpt_writer.GPTWriter({})
    writer._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **kwargs: calls.append(kwargs) or response
    )))
    assert writer._chat("Return JSON", "Test JSON", max_tokens=128000, purpose=purpose) == '{"ok":true}'
    assert calls[0]["model"] == "gpt-6-luna"
    assert calls[0]["reasoning_effort"] == "xhigh"
    assert calls[0]["max_completion_tokens"] == 128000
    assert calls[0]["response_format"] == {"type": "json_object"}
    assert "tools" not in calls[0]


def test_luna_6_capacity_and_cost_are_known():
    capacity = openai_policy.provider_token_capacity("gpt-6-luna")
    assert (capacity.context_window, capacity.max_output_tokens) == (1050000, 128000)
    rates = dict(openai_usage._PRICING)["gpt-6-luna"]
    assert (rates.input_per_million, rates.cached_input_per_million, rates.output_per_million) == (
        Decimal("0.10"), Decimal("0.01"), Decimal("0.50")
    )

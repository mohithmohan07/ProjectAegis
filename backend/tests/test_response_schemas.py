from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app import config
from app.services import generation, model_provider, openai_usage
from app.services.response_schemas import (
    advisory_critic_schema, item_review_schema, provider_response_format,
)


def test_closed_review_schema_preserves_both_verdicts_and_unbounded_evidence():
    contract = advisory_critic_schema()
    wire = contract.json_schema()["schema"]
    assert wire["additionalProperties"] is False
    assert set(wire["required"]) == {"verdict", "confidence", "issues"}
    assert wire["properties"]["verdict"]["enum"] == ["verified", "dissent"]
    long_issue = "Keep all source detail, including [Katex]x^2[/Katex]. " * 2000
    for verdict, issues in (("verified", []), ("dissent", [long_issue])):
        response = {"verdict": verdict, "confidence": 0.8, "issues": issues}
        original = copy.deepcopy(response)
        contract.validate_response(response)
        assert response == original
    assert contract.identity() == advisory_critic_schema().identity()
    assert contract.identity() != item_review_schema().identity()


@pytest.mark.parametrize("changes", [
    {"confidence": True},
    {"confidence": "0.8"},
    {"confidence": 1.2},
    {"confidence": float("nan")},
    {"issues": [{"text": "a concern"}]},
    {"verdict": "rewrite"},
    {"new_decision": "change the author"},
])
def test_review_schema_rejects_malformed_envelopes_without_coercion(changes):
    response = {"verdict": "verified", "confidence": 0.8, "issues": []}
    response.update(changes)
    with pytest.raises(ValidationError):
        advisory_critic_schema().validate_response(response)


def test_item_review_requires_candidate_identity_without_guessing_it():
    response = {"verdict": "verified", "confidence": 1, "issues": []}
    with pytest.raises(ValidationError):
        item_review_schema().validate_response(response)
    response["candidate_id"] = "CAND-07"
    item_review_schema().validate_response(response)
    # Echo/uniqueness remain at the caller's existing identity checker.
    assert response["candidate_id"] == "CAND-07"


@pytest.mark.parametrize("provider,model,wire_type", [
    ("openai", "gpt-5.6-luna", "json_schema"),
    ("gemini", "gemini-2.5-pro", "json_object"),
    ("openai", "custom-model", "json_object"),
])
def test_provider_format_is_additive_and_conservative(provider, model, wire_type):
    wire = provider_response_format(
        advisory_critic_schema(), provider=provider, model=model,
    )
    assert wire["type"] == wire_type
    if wire_type == "json_schema":
        assert wire["json_schema"]["strict"] is True
    assert provider_response_format(None, provider=provider, model=model) == {
        "type": "json_object",
    }


def _stub_transport(monkeypatch, responses, *, provider="openai"):
    import openai

    calls = []
    receipts = []
    responses = iter(responses)

    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(
                message=SimpleNamespace(content=json.dumps(next(responses))),
                finish_reason="stop",
            )],
        )

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=create,
    )))
    monkeypatch.setattr(openai, "OpenAI", lambda **kwargs: client)
    monkeypatch.setattr(config, "OPENAI_MODEL", "gpt-5.6-luna")
    monkeypatch.setattr(model_provider, "active_provider", lambda: provider)
    monkeypatch.setattr(model_provider, "client_kwargs", lambda: {})
    monkeypatch.setattr(generation, "_openai_gate", None)
    monkeypatch.setattr(openai_usage, "record_response", lambda *a, **kw: receipts.append(a))
    monkeypatch.setattr("time.sleep", lambda _: None)
    return calls, receipts


@pytest.mark.parametrize("provider,wire_type", [
    ("openai", "json_schema"), ("gemini", "json_object"),
])
def test_installed_transport_checks_schema_and_accounts_rejected_draft(
    monkeypatch, provider, wire_type,
):
    complete = {"verdict": "dissent", "confidence": 0.5, "issues": ["Missing unit"]}
    calls, receipts = _stub_transport(
        monkeypatch, [{"verdict": "verified"}, complete], provider=provider,
    )
    result = generation._openai_json(
        "Return JSON review.", "Complete immutable evidence.", retries=2,
        purpose="advisory_critic", response_schema=advisory_critic_schema(),
        image_urls=["https://assets.example/figure.png"],
    )
    assert result == complete
    assert len(calls) == len(receipts) == 2
    assert all(call["response_format"]["type"] == wire_type for call in calls)
    assert calls[0]["messages"][-1]["content"][-1]["image_url"]["url"] == (
        "https://assets.example/figure.png"
    )
    assert calls[0]["reasoning_effort"] == calls[1]["reasoning_effort"] == "xhigh"


def test_single_attempt_schema_failure_never_spends_again(monkeypatch):
    calls, receipts = _stub_transport(monkeypatch, [{"verdict": "verified"}])
    with pytest.raises(RuntimeError, match="1 physical request"):
        generation._openai_json(
            "Return JSON review.", "Source evidence.", single_attempt=True,
            purpose="advisory_critic", response_schema=advisory_critic_schema(),
        )
    assert len(calls) == len(receipts) == 1

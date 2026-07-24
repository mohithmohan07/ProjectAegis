from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from aegis_pipeline import openai_policy
from app import config
from app.services import generation, workbooks


EXPECTED_REASONING_POLICY = {
    "assessment_generation": "medium",
    "source_extraction": "medium",
    "concept_mapping": "high",
    "concept_detailing": "medium",
    "concept_validation": "xhigh",
    "pre_learning": "high",
    "workbook_planning": "high",
    "workbook_authoring": "high",
    "metadata": "low",
}


def _json_response() -> SimpleNamespace:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content='{"ok": true}'),
                finish_reason="stop",
            )
        ]
    )


class _CapturingCompletions:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _json_response()


class _CapturingClient:
    completions = _CapturingCompletions()

    def __init__(self, *args, **kwargs) -> None:
        self.chat = SimpleNamespace(completions=self.completions)


def test_default_model_and_complete_reasoning_policy(monkeypatch):
    monkeypatch.delenv(openai_policy.OPENAI_MODEL_ENV, raising=False)

    assert openai_policy.configured_openai_model() == "gpt-5.6-luna"
    assert openai_policy.REASONING_EFFORT_BY_PURPOSE == EXPECTED_REASONING_POLICY
    assert "max" not in openai_policy.REASONING_EFFORT_BY_PURPOSE.values()


def test_model_override_keeps_purpose_policy(monkeypatch):
    monkeypatch.setenv(openai_policy.OPENAI_MODEL_ENV, "custom-model")

    assert openai_policy.chat_request_policy("metadata") == {
        "model": "custom-model",
    }
    assert openai_policy.chat_request_policy(
        "metadata", model="gpt-5.6-terra"
    )["reasoning_effort"] == "low"
    with pytest.raises(ValueError, match="Unknown OpenAI request purpose"):
        openai_policy.reasoning_effort_for("unregistered")  # type: ignore[arg-type]


def test_generation_call_sends_model_reasoning_and_json_mode(monkeypatch):
    import openai

    _CapturingClient.completions = _CapturingCompletions()
    monkeypatch.setattr(openai, "OpenAI", _CapturingClient)
    monkeypatch.setattr(config, "OPENAI_MODEL", "gpt-5.6-luna")
    generation._openai_gate = None

    result = generation._openai_json(
        "system",
        "user",
        max_tokens=321,
        purpose="concept_validation",
    )

    assert result == {"ok": True}
    call = _CapturingClient.completions.calls[-1]
    assert call["model"] == "gpt-5.6-luna"
    assert call["reasoning_effort"] == "xhigh"
    assert call["response_format"] == {"type": "json_object"}
    assert call["max_completion_tokens"] == 321
    generation._openai_gate = None


def test_workbook_call_uses_same_policy_and_preserves_json_mode():
    workbooks._vendor()
    from gpt_writer import GPTWriter

    completions = _CapturingCompletions()
    writer = GPTWriter.__new__(GPTWriter)
    writer.model = "gpt-5.6-luna"
    writer._client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions)
    )

    result = writer._chat(
        "system",
        "user",
        max_tokens=654,
        purpose="workbook_planning",
    )

    assert result == '{"ok": true}'
    call = completions.calls[-1]
    assert call["model"] == "gpt-5.6-luna"
    assert call["reasoning_effort"] == "high"
    assert call["response_format"] == {"type": "json_object"}
    assert call["max_completion_tokens"] == 654


def test_workbook_does_not_retry_unrelated_provider_failures():
    workbooks._vendor()
    from gpt_writer import GPTWriter

    class FailingCompletions:
        def __init__(self) -> None:
            self.calls = 0

        def create(self, **_kwargs):
            self.calls += 1
            raise RuntimeError("quota exhausted")

    completions = FailingCompletions()
    writer = GPTWriter.__new__(GPTWriter)
    writer.model = "gpt-5.6-luna"
    writer._client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions)
    )

    with pytest.raises(RuntimeError, match="quota exhausted"):
        writer._chat(
            "system",
            "user",
            max_tokens=654,
            purpose="workbook_planning",
        )
    assert completions.calls == 1


def test_all_active_runtime_calls_declare_a_known_purpose():
    generation_tree = ast.parse(
        Path(generation.__file__).read_text(encoding="utf-8")
    )
    generation_calls = [
        node
        for node in ast.walk(generation_tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_openai_json"
    ]
    assert len(generation_calls) == 39
    generation_purposes = [
        ast.literal_eval(keyword.value)
        for node in generation_calls
        for keyword in node.keywords
        if keyword.arg == "purpose"
    ]
    assert len(generation_purposes) == len(generation_calls)
    assert set(generation_purposes) <= set(EXPECTED_REASONING_POLICY)

    workbooks._vendor()
    from gpt_writer import GPTWriter

    workbook_tree = ast.parse(
        Path(__import__("gpt_writer").__file__).read_text(encoding="utf-8")
    )
    workbook_calls = [
        node
        for node in ast.walk(workbook_tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_chat"
    ]
    workbook_purposes = [
        ast.literal_eval(keyword.value)
        for node in workbook_calls
        for keyword in node.keywords
        if keyword.arg == "purpose"
    ]
    assert len(workbook_calls) == 5
    assert len(workbook_purposes) == len(workbook_calls)
    assert set(workbook_purposes) <= set(EXPECTED_REASONING_POLICY)

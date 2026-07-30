"""Regression coverage for Phase 3.4.1 schema-completeness turnover."""
from __future__ import annotations

import json
from types import SimpleNamespace

from app.services import canonical_source_phase22 as phase22
from app.services import generation


def _schema() -> dict:
    return {
        "name": "phase341_required_rows",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "rows": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 2,
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                        },
                        "required": ["id"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["rows"],
            "additionalProperties": False,
        },
    }


def test_valid_but_incomplete_object_gets_larger_budget_retry(monkeypatch):
    import openai

    calls: list[dict] = []

    class Client:
        def __init__(self, *args, **kwargs):
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=self.create)
            )

        def create(self, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                # A reasoning model can spend its budget before emitting the
                # required array and still expose a syntactically valid object.
                content = "{}"
                finish_reason = "length"
            else:
                content = json.dumps({
                    "rows": [{"id": "A"}, {"id": "B"}],
                })
                finish_reason = "stop"
            return SimpleNamespace(
                choices=[SimpleNamespace(
                    message=SimpleNamespace(content=content, refusal=None),
                    finish_reason=finish_reason,
                )]
            )

    monkeypatch.setattr(openai, "OpenAI", Client)
    monkeypatch.setenv("AEGIS_STRUCTURED_JSON_MAX_COMPLETION_TOKENS", "8000")
    generation._openai_gate = None

    result = phase22._openai_multimodal_json(
        system="Return every required row.",
        prompt="payload",
        pages=[],
        response_schema=_schema(),
        purpose="concept_mapping",
        max_tokens=1200,
    )

    assert result == {"rows": [{"id": "A"}, {"id": "B"}]}
    assert [call["max_completion_tokens"] for call in calls] == [1200, 5200]
    assert "STRICT-SCHEMA COMPLETENESS RECOVERY" in (
        calls[1]["messages"][0]["content"]
    )
    generation._openai_gate = None

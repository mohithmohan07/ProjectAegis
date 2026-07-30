"""Regression coverage for Phase 3.4 structured-output turnover."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app import config
from app.services import canonical_source_phase22 as phase22
from app.services import canonical_source_phase3 as phase3
from app.services import canonical_source_phase34_structured_output_contract as phase34
from app.services import generation


def _simple_schema() -> dict:
    return {
        "name": "phase34_test_object",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
            "additionalProperties": False,
        },
    }


def test_completion_limit_escalates_budget_and_finishes(monkeypatch):
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
                return SimpleNamespace(
                    choices=[SimpleNamespace(
                        message=SimpleNamespace(content='{"ok":', refusal=None),
                        finish_reason="length",
                    )]
                )
            return SimpleNamespace(
                choices=[SimpleNamespace(
                    message=SimpleNamespace(
                        content=json.dumps({"ok": True}), refusal=None
                    ),
                    finish_reason="stop",
                )]
            )

    monkeypatch.setattr(openai, "OpenAI", Client)
    monkeypatch.setattr(config, "OPENAI_MODEL", "gpt-5.6-luna")
    monkeypatch.setenv("AEGIS_STRUCTURED_JSON_MAX_COMPLETION_TOKENS", "8000")
    generation._openai_gate = None

    result = phase22._openai_multimodal_json(
        system="Return strict JSON.",
        prompt="payload",
        pages=[],
        response_schema=_simple_schema(),
        purpose="concept_mapping",
        max_tokens=1200,
    )

    assert result == {"ok": True}
    assert len(calls) == 2
    assert calls[0]["max_completion_tokens"] == 1200
    assert calls[1]["max_completion_tokens"] == 5200
    assert calls[0]["reasoning_effort"] == "high"
    assert calls[1]["reasoning_effort"] == "medium"
    assert "STRUCTURED OUTPUT RECOVERY" in calls[1]["messages"][0]["content"]
    generation._openai_gate = None


def test_complete_json_is_accepted_despite_length_marker(monkeypatch):
    import openai

    calls = 0

    class Client:
        def __init__(self, *args, **kwargs):
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=self.create)
            )

        def create(self, **_kwargs):
            nonlocal calls
            calls += 1
            return SimpleNamespace(
                choices=[SimpleNamespace(
                    message=SimpleNamespace(
                        content=json.dumps({"ok": True}), refusal=None
                    ),
                    finish_reason="length",
                )]
            )

    monkeypatch.setattr(openai, "OpenAI", Client)
    generation._openai_gate = None

    result = phase22._openai_multimodal_json(
        system="Return strict JSON.",
        prompt="payload",
        pages=[],
        response_schema=_simple_schema(),
        max_tokens=1200,
    )

    assert result == {"ok": True}
    assert calls == 1
    generation._openai_gate = None


def _hierarchy_payload(count: int) -> dict:
    sections = []
    for index in range(1, count + 1):
        sections.append({
            "section_id": f"SEC-{index:04d}",
            "title": f"Section {index}",
            "level": 1 if index in {1, 11, 21} else 2,
            "source_order": index,
            "source_start": index * 100,
            "baseline_role": "main_topic" if index in {1, 11, 21} else "subtopic",
            "excerpt": f"Visible source evidence for section {index}.",
        })
    return {
        "metadata": {
            "subject": "History",
            "chapter": "The Rise of Nationalism in Europe",
        },
        "sections": sections,
        "verified_pdf_headings": [],
        "allowed_roles": list(phase34._ALLOWED_HIERARCHY_ROLES),
    }


def _classification_response(kwargs: dict) -> dict:
    payload = json.loads(kwargs["prompt"])
    target_ids = payload["target_section_ids"]
    rows = []
    for section_id in target_ids:
        number = int(section_id.rsplit("-", 1)[1])
        parent = "" if number in {1, 11, 21} else (
            "SEC-0001" if number < 11 else (
                "SEC-0011" if number < 21 else "SEC-0021"
            )
        )
        rows.append({
            "section_id": section_id,
            "role": "main_topic" if not parent else "subtopic",
            "parent_section_id": parent,
            "confidence": 0.999,
            "evidence": ["bounded canonical section evidence"],
        })
    return {"sections": rows}


def test_hierarchy_batches_are_cached_and_allow_cross_batch_parent_ids(
    tmp_path,
    monkeypatch,
):
    payload = _hierarchy_payload(23)
    calls: list[list[str]] = []

    def fake_call(**kwargs):
        request = json.loads(kwargs["prompt"])
        calls.append(list(request["target_section_ids"]))
        return _classification_response(kwargs)

    monkeypatch.setenv("AEGIS_PHASE3_HIERARCHY_BATCH_SECTIONS", "10")
    monkeypatch.setattr(phase22, "_openai_multimodal_json", fake_call)
    session = {"artifact_dir": tmp_path}

    with phase3.activate_session(session):
        first = phase34._classify_hierarchy_batched(payload)
        monkeypatch.setattr(
            phase22,
            "_openai_multimodal_json",
            lambda **_kwargs: (_ for _ in ()).throw(
                AssertionError("verified hierarchy batches should be cached")
            ),
        )
        second = phase34._classify_hierarchy_batched(payload)

    assert len(calls) == 3
    assert [len(batch) for batch in calls] == [10, 10, 3]
    assert first == second
    assert [row["section_id"] for row in first["sections"]] == [
        f"SEC-{index:04d}" for index in range(1, 24)
    ]
    # SEC-0012 is in a different response batch from its canonical parent.
    section_12 = next(
        row for row in first["sections"] if row["section_id"] == "SEC-0012"
    )
    assert section_12["parent_section_id"] == "SEC-0011"
    assert (tmp_path / phase34._HIERARCHY_CACHE_FILENAME).exists()


def test_resume_reuses_successful_hierarchy_batches_after_late_failure(
    tmp_path,
    monkeypatch,
):
    payload = _hierarchy_payload(23)
    first_calls = 0

    def fail_on_second_batch(**kwargs):
        nonlocal first_calls
        first_calls += 1
        if first_calls == 2:
            raise RuntimeError("simulated provider interruption")
        return _classification_response(kwargs)

    monkeypatch.setenv("AEGIS_PHASE3_HIERARCHY_BATCH_SECTIONS", "10")
    monkeypatch.setattr(
        phase22, "_openai_multimodal_json", fail_on_second_batch
    )
    session = {"artifact_dir": tmp_path}

    with phase3.activate_session(session):
        with pytest.raises(RuntimeError, match="simulated provider interruption"):
            phase34._classify_hierarchy_batched(payload)

        resumed_calls: list[list[str]] = []

        def resumed(**kwargs):
            request = json.loads(kwargs["prompt"])
            resumed_calls.append(list(request["target_section_ids"]))
            return _classification_response(kwargs)

        monkeypatch.setattr(phase22, "_openai_multimodal_json", resumed)
        result = phase34._classify_hierarchy_batched(payload)

    assert first_calls == 2
    # The first ten verified IDs are read from cache; only batches 2 and 3 run.
    assert resumed_calls == [
        [f"SEC-{index:04d}" for index in range(11, 21)],
        [f"SEC-{index:04d}" for index in range(21, 24)],
    ]
    assert len(result["sections"]) == 23


def test_hierarchy_critic_is_batched_and_cached(tmp_path, monkeypatch):
    payload = _hierarchy_payload(21)
    payload["proposed_hierarchy"] = _classification_response({
        "prompt": json.dumps({
            "target_section_ids": [
                f"SEC-{index:04d}" for index in range(1, 22)
            ]
        })
    })["sections"]
    calls = 0

    def fake_critic(**kwargs):
        nonlocal calls
        calls += 1
        request = json.loads(kwargs["prompt"])
        assert request["target_section_ids"]
        return {
            "verdict": "verified",
            "confidence": 0.999,
            "repairs": [],
            "issues": [],
        }

    monkeypatch.setenv("AEGIS_PHASE3_HIERARCHY_BATCH_SECTIONS", "10")
    monkeypatch.setattr(phase22, "_openai_multimodal_json", fake_critic)
    session = {"artifact_dir": tmp_path}

    with phase3.activate_session(session):
        first = phase34._critic_hierarchy_batched(payload)
        monkeypatch.setattr(
            phase22,
            "_openai_multimodal_json",
            lambda **_kwargs: (_ for _ in ()).throw(
                AssertionError("verified critic batches should be cached")
            ),
        )
        second = phase34._critic_hierarchy_batched(payload)

    assert calls == 3
    assert first == second == {
        "verdict": "verified",
        "confidence": 0.999,
        "repairs": [],
        "issues": [],
    }

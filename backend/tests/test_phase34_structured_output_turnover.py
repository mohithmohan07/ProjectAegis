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


def test_max_reasoning_compacts_on_structured_output_turnover():
    assert phase34._downgraded_reasoning("max", 1) == "xhigh"
    assert phase34._downgraded_reasoning("max", 2) == "high"


def test_unsupported_reasoning_effort_downgrades_immediately(monkeypatch):
    import openai
    from aegis_pipeline import openai_policy

    calls: list[dict] = []

    class UnsupportedEffortError(RuntimeError):
        body = {
            "error": {
                "message": (
                    "Unsupported value: reasoning_effort does not support max"
                ),
                "param": "reasoning_effort",
                "code": "unsupported_value",
            }
        }

    class Client:
        def __init__(self, *args, **kwargs):
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=self.create)
            )

        def create(self, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise UnsupportedEffortError("unsupported max effort")
            return SimpleNamespace(
                choices=[SimpleNamespace(
                    message=SimpleNamespace(
                        content=json.dumps({"ok": True}), refusal=None
                    ),
                    finish_reason="stop",
                )]
            )

    monkeypatch.setattr(openai, "OpenAI", Client)
    monkeypatch.setattr(
        openai_policy,
        "chat_request_policy",
        lambda purpose, model=None: {
            "model": model or "gpt-5.6-terra",
            "reasoning_effort": "max",
        },
    )
    monkeypatch.setattr(phase34.time, "sleep", lambda _seconds: None)
    generation._openai_gate = None

    result = phase34._resilient_openai_multimodal_json(
        system="Return strict JSON.",
        prompt="payload",
        pages=[],
        response_schema=_simple_schema(),
        purpose="semantic_resolution",
        max_tokens=128_000,
        model="gpt-5.6-terra",
    )

    assert result == {"ok": True}
    assert [call["reasoning_effort"] for call in calls] == ["max", "xhigh"]
    assert len(calls) == 2
    generation._openai_gate = None


def test_each_unsupported_reasoning_effort_is_tried_once(monkeypatch):
    import openai
    from aegis_pipeline import openai_policy

    calls: list[dict] = []

    class UnsupportedEffortError(RuntimeError):
        status_code = 400
        body = {
            "error": {
                "message": "Unsupported reasoning_effort for this model",
                "param": "reasoning_effort",
                "code": "unsupported_value",
            }
        }

    class Client:
        def __init__(self, *args, **kwargs):
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=self.create)
            )

        def create(self, **kwargs):
            calls.append(kwargs)
            if kwargs["reasoning_effort"] in {"max", "xhigh"}:
                raise UnsupportedEffortError("unsupported effort")
            return SimpleNamespace(
                choices=[SimpleNamespace(
                    message=SimpleNamespace(
                        content=json.dumps({"ok": True}), refusal=None
                    ),
                    finish_reason="stop",
                )]
            )

    monkeypatch.setattr(openai, "OpenAI", Client)
    monkeypatch.setattr(
        openai_policy,
        "chat_request_policy",
        lambda purpose, model=None: {
            "model": model or "gpt-5.6-terra",
            "reasoning_effort": "max",
        },
    )
    monkeypatch.setattr(phase34.time, "sleep", lambda _seconds: None)
    generation._openai_gate = None

    result = phase34._resilient_openai_multimodal_json(
        system="Return strict JSON.",
        prompt="payload",
        pages=[],
        response_schema=_simple_schema(),
        purpose="semantic_resolution",
        max_tokens=128_000,
        model="gpt-5.6-terra",
    )

    assert result == {"ok": True}
    assert [call["reasoning_effort"] for call in calls] == [
        "max",
        "xhigh",
        "high",
    ]
    generation._openai_gate = None


def test_capability_downgrade_then_truncation_uses_lower_effort(monkeypatch):
    import openai
    from aegis_pipeline import openai_policy

    calls: list[dict] = []

    class UnsupportedEffortError(RuntimeError):
        status_code = 400
        body = {
            "error": {
                "message": "Unsupported reasoning_effort for this model",
                "param": "reasoning_effort",
                "code": "unsupported_value",
            }
        }

    class Client:
        def __init__(self, *args, **kwargs):
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=self.create)
            )

        def create(self, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise UnsupportedEffortError("unsupported xhigh effort")
            if len(calls) == 2:
                return SimpleNamespace(
                    choices=[SimpleNamespace(
                        message=SimpleNamespace(content="{}", refusal=None),
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
    monkeypatch.setattr(
        openai_policy,
        "chat_request_policy",
        lambda purpose, model=None: {
            "model": model or "gpt-5.6-terra",
            "reasoning_effort": "xhigh",
        },
    )
    generation._openai_gate = None

    result = phase34._resilient_openai_multimodal_json(
        system="Return strict JSON.",
        prompt="payload",
        pages=[],
        response_schema=_simple_schema(),
        purpose="semantic_resolution",
        max_tokens=128_000,
        model="gpt-5.6-terra",
    )

    assert result == {"ok": True}
    assert [call["reasoning_effort"] for call in calls] == [
        "xhigh",
        "high",
        "medium",
    ]
    generation._openai_gate = None


def test_unrelated_definitive_400_is_not_replayed(monkeypatch):
    import openai

    calls = 0

    class InvalidRequestError(RuntimeError):
        status_code = 400
        body = {
            "error": {
                "message": "Invalid schema declaration",
                "param": "response_format",
                "code": "invalid_request_error",
            }
        }

    class Client:
        def __init__(self, *args, **kwargs):
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=self.create)
            )

        def create(self, **_kwargs):
            nonlocal calls
            calls += 1
            raise InvalidRequestError("invalid response schema")

    monkeypatch.setattr(openai, "OpenAI", Client)
    monkeypatch.setattr(phase34.time, "sleep", lambda _seconds: None)
    generation._openai_gate = None

    with pytest.raises(RuntimeError, match="definitively rejected"):
        phase34._resilient_openai_multimodal_json(
            system="Return strict JSON.",
            prompt="payload",
            pages=[],
            response_schema=_simple_schema(),
            purpose="semantic_resolution",
            max_tokens=128_000,
            model="gpt-5.6-terra",
        )

    assert calls == 1
    generation._openai_gate = None


def test_structured_non_reasoning_param_wins_over_message_text():
    class ResponseFormatError(RuntimeError):
        body = {
            "error": {
                "message": (
                    "Unsupported response_format while reasoning_effort is set"
                ),
                "param": "response_format",
                "code": "unsupported_value",
            }
        }

    assert not phase34._unsupported_reasoning_effort(
        ResponseFormatError("unsupported response format")
    )


def test_single_attempt_does_not_negotiate_reasoning_effort(monkeypatch):
    import openai

    calls = 0

    class UnsupportedEffortError(RuntimeError):
        status_code = 400
        body = {
            "error": {
                "message": "Unsupported reasoning_effort for this model",
                "param": "reasoning_effort",
                "code": "unsupported_value",
            }
        }

    class Client:
        def __init__(self, *args, **kwargs):
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=self.create)
            )

        def create(self, **_kwargs):
            nonlocal calls
            calls += 1
            raise UnsupportedEffortError("unsupported effort")

    monkeypatch.setattr(openai, "OpenAI", Client)
    generation._openai_gate = None

    with pytest.raises(RuntimeError, match="single attempt failed"):
        phase34._resilient_openai_multimodal_json(
            system="Return strict JSON.",
            prompt="payload",
            pages=[],
            response_schema=_simple_schema(),
            purpose="semantic_resolution",
            max_tokens=128_000,
            model="gpt-5.6-terra",
            single_attempt=True,
        )

    assert calls == 1
    generation._openai_gate = None


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


def test_incomplete_object_at_provider_max_retries_same_allowance(monkeypatch):
    import openai

    calls: list[dict] = []

    class Client:
        def __init__(self, *args, **kwargs):
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=self.create)
            )

        def create(self, **kwargs):
            calls.append(kwargs)
            content = (
                "{}" if len(calls) == 1 else json.dumps({"ok": True})
            )
            return SimpleNamespace(
                choices=[SimpleNamespace(
                    message=SimpleNamespace(content=content, refusal=None),
                    finish_reason="length" if len(calls) == 1 else "stop",
                )]
            )

    monkeypatch.setattr(openai, "OpenAI", Client)
    monkeypatch.setattr(config, "OPENAI_MAX_OUTPUT_TOKENS", 128_000)
    generation._openai_gate = None

    result = phase34._resilient_openai_multimodal_json(
        system="Return strict JSON.",
        prompt="payload",
        pages=[],
        response_schema=_simple_schema(),
        purpose="concept_mapping",
        max_tokens=128_000,
        model="gpt-5.6-terra",
    )

    assert result == {"ok": True}
    assert [call["max_completion_tokens"] for call in calls] == [
        128_000,
        128_000,
    ]
    assert all(call["model"] == "gpt-5.6-terra" for call in calls)
    assert "STRUCTURED OUTPUT RECOVERY" in (
        calls[1]["messages"][0]["content"]
    )
    generation._openai_gate = None


def test_incomplete_object_at_provider_max_has_bounded_terminal(monkeypatch):
    import openai

    calls: list[dict] = []

    class Client:
        def __init__(self, *args, **kwargs):
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=self.create)
            )

        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(
                    message=SimpleNamespace(content="{}", refusal=None),
                    finish_reason="length",
                )]
            )

    monkeypatch.setattr(openai, "OpenAI", Client)
    monkeypatch.setattr(config, "OPENAI_MAX_OUTPUT_TOKENS", 128_000)
    monkeypatch.setenv("AEGIS_STRUCTURED_JSON_MAX_TRUNCATION_RETRIES", "2")
    generation._openai_gate = None

    with pytest.raises(
        phase34._TerminalStructuredOutputError,
        match="after 3 bounded response",
    ):
        phase34._resilient_openai_multimodal_json(
            system="Return strict JSON.",
            prompt="payload",
            pages=[],
            response_schema=_simple_schema(),
            purpose="concept_mapping",
            max_tokens=128_000,
        )

    assert len(calls) == 3
    assert all(call["max_completion_tokens"] == 128_000 for call in calls)
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


def _long_hierarchy_payload(count: int) -> dict:
    payload = _hierarchy_payload(count)
    for index, section in enumerate(payload["sections"], start=1):
        sentinel = f"BODY_ONLY_{index:04d}"
        section["excerpt"] = (f"{sentinel} source detail. " * 180).strip()
    return payload


def test_classifier_packet_keeps_full_directory_but_only_local_bodies(
    monkeypatch,
):
    payload = _long_hierarchy_payload(8)
    monkeypatch.setenv("AEGIS_PHASE34_TARGET_SECTION_EVIDENCE_CHARS", "900")
    monkeypatch.setenv("AEGIS_PHASE34_CONTEXT_SECTION_EVIDENCE_CHARS", "300")

    packet = phase34._classification_payload_for_batch(
        payload,
        ["SEC-0004"],
    )

    assert [row["section_id"] for row in packet["section_directory"]] == [
        f"SEC-{index:04d}" for index in range(1, 9)
    ]
    assert all(
        "excerpt" not in row and "body_evidence" not in row
        for row in packet["section_directory"]
    )
    evidence = {
        row["section_id"]: row for row in packet["section_evidence"]
    }
    assert set(evidence) == {"SEC-0001", "SEC-0003", "SEC-0004", "SEC-0005"}
    assert evidence["SEC-0004"]["relationship"] == "target"
    assert len(evidence["SEC-0004"]["body_evidence"]) <= 900
    assert all(
        len(row["body_evidence"]) <= 300
        for section_id, row in evidence.items()
        if section_id != "SEC-0004"
    )
    serialized = json.dumps(packet, ensure_ascii=False)
    for index in (2, 6, 7, 8):
        assert f"BODY_ONLY_{index:04d}" not in serialized


def test_critic_packet_adds_distant_proposed_parent_without_other_bodies(
    monkeypatch,
):
    payload = _long_hierarchy_payload(8)
    payload["proposed_hierarchy"] = [
        {
            "section_id": f"SEC-{index:04d}",
            "role": "subtopic",
            "parent_section_id": "SEC-0007" if index == 4 else "SEC-0001",
            "confidence": 0.999,
            "evidence": [f"PROPOSAL_EVIDENCE_{index:04d}"],
        }
        for index in range(1, 9)
    ]
    monkeypatch.setenv("AEGIS_PHASE34_TARGET_SECTION_EVIDENCE_CHARS", "900")
    monkeypatch.setenv("AEGIS_PHASE34_CONTEXT_SECTION_EVIDENCE_CHARS", "300")

    packet = phase34._critic_payload_for_batch(payload, ["SEC-0004"])

    evidence_ids = {
        row["section_id"] for row in packet["section_evidence"]
    }
    assert evidence_ids == {
        "SEC-0001",
        "SEC-0003",
        "SEC-0004",
        "SEC-0005",
        "SEC-0007",
    }
    assert [
        row["section_id"] for row in packet["proposed_hierarchy"]
    ] == [f"SEC-{index:04d}" for index in range(1, 9)]
    assert all(
        "evidence" not in row
        for row in packet["proposed_hierarchy"]
        if row["section_id"] != "SEC-0004"
    )
    serialized = json.dumps(packet, ensure_ascii=False)
    assert "PROPOSAL_EVIDENCE_0004" in serialized
    assert "PROPOSAL_EVIDENCE_0002" not in serialized
    for index in (2, 6, 8):
        assert f"BODY_ONLY_{index:04d}" not in serialized


def test_bounded_batches_keep_exact_identity_and_parent_integrity():
    all_ids = {"SEC-0001", "SEC-0002"}
    with pytest.raises(ValueError, match="omitted section IDs"):
        phase34._validate_hierarchy_batch(
            {"sections": []},
            target_ids=["SEC-0002"],
            all_ids=all_ids,
        )

    with pytest.raises(ValueError, match="invented a parent section"):
        phase34._validate_hierarchy_batch(
            {
                "sections": [
                    {
                        "section_id": "SEC-0002",
                        "role": "subtopic",
                        "parent_section_id": "SEC-9999",
                        "confidence": 0.999,
                        "evidence": [],
                    }
                ]
            },
            target_ids=["SEC-0002"],
            all_ids=all_ids,
        )


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

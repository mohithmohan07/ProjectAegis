"""Regression coverage for Phase 3.5 provider-maximum token capacity."""
from __future__ import annotations

from app import config
from app.services import canonical_source_phase22 as phase22
from app.services import canonical_source_phase3 as phase3
from app.services import canonical_source_phase31_grounding_contract as phase31
from app.services import canonical_source_phase32_topology_adjudication_contract as phase32
from app.services import canonical_source_phase33_preflight_contract as phase33
from app.services import canonical_source_phase34_structured_output_contract as phase34
from app.services import generation, workbooks
from aegis_pipeline import openai_policy


def _activate(monkeypatch) -> None:
    monkeypatch.setattr(config, "OPENAI_PROVIDER_MAX_TOKENS", True)
    monkeypatch.setattr(config, "OPENAI_CONTEXT_WINDOW_TOKENS", 1_050_000)
    monkeypatch.setattr(config, "OPENAI_MAX_OUTPUT_TOKENS", 128_000)
    monkeypatch.setattr(config, "OPENAI_MAX_INPUT_TOKENS", 922_000)
    monkeypatch.setattr(config, "OPENAI_MAX_INPUT_CHARS", 922_000)
    monkeypatch.setattr(config, "use_live_generation", lambda: True)


def test_gpt56_provider_capacity_defaults(monkeypatch):
    monkeypatch.delenv(openai_policy.OPENAI_PROVIDER_MAX_TOKENS_ENV, raising=False)
    monkeypatch.delenv(openai_policy.OPENAI_CONTEXT_WINDOW_ENV, raising=False)
    monkeypatch.delenv(openai_policy.OPENAI_MAX_OUTPUT_TOKENS_ENV, raising=False)

    capacity = openai_policy.provider_token_capacity("gpt-5.6-luna")

    assert capacity.context_window == 1_050_000
    assert capacity.max_output_tokens == 128_000
    assert capacity.max_input_tokens == 922_000
    assert openai_policy.configured_max_output_tokens("gpt-5.6-luna") == 128_000
    assert openai_policy.configured_max_input_tokens("gpt-5.6-luna") == 922_000
    assert openai_policy.effective_completion_tokens(
        4_000,
        model="gpt-5.6-luna",
    ) == 128_000


def test_live_json_calls_ignore_smaller_local_completion_budget(monkeypatch):
    _activate(monkeypatch)
    captured: dict = {}

    def original(system, user, max_tokens=None, retries=3, *, purpose="source_extraction"):
        captured.update(
            system=system,
            user=user,
            max_tokens=max_tokens,
            retries=retries,
            purpose=purpose,
        )
        return {"ok": True}

    monkeypatch.setattr(generation, "_PHASE35_ORIGINAL_OPENAI_JSON", original)

    result = generation._openai_json(
        "system",
        "user",
        max_tokens=321,
        purpose="concept_validation",
    )

    assert result == {"ok": True}
    assert captured["max_tokens"] == 128_000
    assert captured["purpose"] == "concept_validation"


def test_live_strict_schema_calls_use_provider_maximum(monkeypatch):
    _activate(monkeypatch)
    captured: dict = {}

    def original(**kwargs):
        captured.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(
        phase22,
        "_PHASE35_ORIGINAL_OPENAI_MULTIMODAL_JSON",
        original,
    )

    result = phase22._openai_multimodal_json(
        system="system",
        prompt="prompt",
        pages=[],
        response_schema={"name": "test", "strict": True, "schema": {}},
        purpose="concept_mapping",
        max_tokens=4_000,
    )

    assert result == {"ok": True}
    assert captured["max_tokens"] == 128_000
    assert phase34._completion_cap(4_000) == 128_000


def test_live_source_evidence_is_not_character_clipped(monkeypatch):
    _activate(monkeypatch)
    long_text = "Visible source sentence. " * 500

    assert phase22._compact(long_text, limit=20) == " ".join(long_text.split())
    assert generation._trim(long_text, max_chars=20) == long_text
    assert generation._split_mmd_into_chunks(long_text) == [long_text]

    excerpt = phase3._section_excerpt(
        {"block_ids": ["BLK-1"]},
        {
            "BLK-1": {
                "kind": "paragraph",
                "display_text": long_text,
            }
        },
        limit=20,
    )
    assert len(excerpt) > 9_000
    assert "TRIMMED" not in excerpt


def test_grounding_topology_and_host_payloads_keep_full_blocks(monkeypatch):
    _activate(monkeypatch)
    long_text = "Source evidence detail " * 400
    graph = {
        "topics": [{"topic_id": "TOPIC-1", "order": 1, "title": "Topic"}],
        "blocks": [
            {
                "block_id": "BLK-1",
                "topic_id": "TOPIC-1",
                "subtopic_id": "SUB-1",
                "kind": "paragraph",
            }
        ],
    }
    canonical = {
        "blocks": [
            {
                "block_id": "BLK-1",
                "display_text": long_text,
                "kind": "paragraph",
            }
        ]
    }

    _usable, grounding = phase31._candidate_blocks(graph, canonical, "TOPIC-1")
    topics, _order = phase32._topic_evidence(graph, canonical)
    hosts, _subtopics = phase33._topic_source_blocks(graph, canonical)

    assert grounding[0]["text"] == long_text.strip()
    assert long_text.strip() in topics[0]["evidence"]
    assert "AEGIS NOTE" not in topics[0]["evidence"]
    assert hosts["TOPIC-1"][0]["text"] == long_text.strip()


def test_workbook_writer_receives_same_provider_capacity(monkeypatch):
    _activate(monkeypatch)

    workbooks._vendor()
    from gpt_writer import GPTWriter

    assert GPTWriter.MAX_MMD_CHARS == 922_000
    assert GPTWriter.MAX_PLANNER_OUTPUT_TOKENS == 128_000
    assert GPTWriter.MAX_BUILDER_OUTPUT_TOKENS == 128_000
    assert GPTWriter.MAX_TOPIC_OUTPUT_TOKENS == 128_000
    assert GPTWriter.MAX_SHELL_OUTPUT_TOKENS == 128_000

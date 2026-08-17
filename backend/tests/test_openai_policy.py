from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from aegis_pipeline import openai_policy
from app import config
from app.services import generation, workbooks


EXPECTED_REASONING_POLICY = {
    "assessment_generation": "max",
    "source_extraction": "high",
    "source_adjudication": "max",
    "page_transcription": "medium",
    "chapter_outline": "max",
    "concept_mapping": "max",
    "concept_detailing": "max",
    "concept_validation": "high",
    "semantic_resolution": "max",
    "pre_learning": "max",
    "workbook_planning": "max",
    "workbook_authoring": "max",
    "revision_editing": "medium",
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
    # Judgment-heavy purposes ask for the deepest deliberation the model will
    # give; fidelity-heavy purposes are verified copy/structure work where
    # deep reasoning only adds latency.
    for judgment in (
        "source_adjudication", "concept_mapping", "concept_detailing",
        "semantic_resolution",
    ):
        assert openai_policy.REASONING_EFFORT_BY_PURPOSE[judgment] == "max"
    for fidelity in ("page_transcription", "revision_editing", "metadata"):
        assert openai_policy.REASONING_EFFORT_BY_PURPOSE[fidelity] in {
            "low", "medium",
        }


def test_model_override_keeps_purpose_policy(monkeypatch):
    monkeypatch.setenv(openai_policy.OPENAI_MODEL_ENV, "custom-model")

    assert openai_policy.chat_request_policy("metadata") == {
        "model": "custom-model",
    }
    assert openai_policy.chat_request_policy(
        "metadata", model="gpt-5.6-luna"
    )["reasoning_effort"] == "low"
    assert openai_policy.chat_request_policy(
        "concept_mapping", model="gpt-5.6-luna"
    )["reasoning_effort"] == "max"
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
    assert call["reasoning_effort"] == "high"
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
    assert call["reasoning_effort"] == "max"
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


class _UnsupportedEffortError(Exception):
    """The provider's structured 400 for an effort the model does not accept."""

    def __init__(self, effort: str = "max") -> None:
        super().__init__(
            f"Unsupported value: 'reasoning_effort' does not support {effort!r}"
        )
        self.status_code = 400
        self.param = "reasoning_effort"
        self.code = "unsupported_value"


def test_effort_ceiling_is_discovered_once_and_reused_process_wide(monkeypatch):
    """The whole reason `max` everywhere is affordable.

    A model that rejects `max` must cost one probe for the process, not one
    rejected request per call, and the ceiling must be visible to every call
    path rather than relearned by each.
    """

    monkeypatch.delenv(openai_policy.OPENAI_MODEL_ENV, raising=False)

    assert openai_policy.chat_request_policy(
        "concept_mapping", model="gpt-5.6-luna"
    )["reasoning_effort"] == "max"

    assert openai_policy.note_unsupported_reasoning_effort(
        "gpt-5.6-luna", "max"
    ) == "xhigh"

    # Every later request — any purpose, any call path — is now built at the
    # discovered ceiling without touching the provider again; purposes whose
    # policy already sits below the ceiling keep their own effort.
    for purpose, expected in (
        ("concept_mapping", "xhigh"),
        ("workbook_authoring", "xhigh"),
        ("concept_validation", "high"),
        ("metadata", "low"),
    ):
        assert openai_policy.chat_request_policy(
            purpose, model="gpt-5.6-luna"
        )["reasoning_effort"] == expected

    # A different model is unaffected by another model's ceiling.
    assert openai_policy.chat_request_policy(
        "concept_mapping", model="gpt-5.6-other"
    )["reasoning_effort"] == "max"


def test_effort_ceiling_only_ratchets_downward(monkeypatch):
    openai_policy.note_unsupported_reasoning_effort("gpt-5.6-luna", "high")
    assert openai_policy.reasoning_ceiling("gpt-5.6-luna") == "medium"

    # A slower caller reporting a weaker rejection must not widen the ceiling
    # a concurrent caller already discovered.
    openai_policy.note_unsupported_reasoning_effort("gpt-5.6-luna", "max")
    assert openai_policy.reasoning_ceiling("gpt-5.6-luna") == "medium"


def test_exhausted_effort_ladder_omits_the_parameter(monkeypatch):
    for effort in ("max", "xhigh", "high", "medium", "low", "none"):
        openai_policy.note_unsupported_reasoning_effort("gpt-5.6-luna", effort)

    assert openai_policy.reasoning_ceiling("gpt-5.6-luna") == ""
    assert openai_policy.chat_request_policy(
        "metadata", model="gpt-5.6-luna"
    ) == {"model": "gpt-5.6-luna"}


def test_generation_negotiates_effort_instead_of_replaying_the_same_request(
    monkeypatch,
):
    import openai

    class NegotiatingCompletions:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            if kwargs.get("reasoning_effort") == "max":
                raise _UnsupportedEffortError()
            return _json_response()

    completions = NegotiatingCompletions()
    _CapturingClient.completions = completions
    monkeypatch.setattr(openai, "OpenAI", _CapturingClient)
    monkeypatch.setattr(config, "OPENAI_MODEL", "gpt-5.6-luna")
    generation._openai_gate = None

    result = generation._openai_json(
        "system", "user", max_tokens=321, purpose="concept_mapping"
    )

    assert result == {"ok": True}
    # One probe, one immediate retry at the next rung — not three identical 400s.
    assert [call.get("reasoning_effort") for call in completions.calls] == [
        "max",
        "xhigh",
    ]
    assert openai_policy.reasoning_ceiling("gpt-5.6-luna") == "xhigh"
    generation._openai_gate = None


def test_single_attempt_records_the_ceiling_without_a_second_request(monkeypatch):
    import openai

    class RejectingCompletions:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            raise _UnsupportedEffortError()

    completions = RejectingCompletions()
    _CapturingClient.completions = completions
    monkeypatch.setattr(openai, "OpenAI", _CapturingClient)
    monkeypatch.setattr(config, "OPENAI_MODEL", "gpt-5.6-luna")
    generation._openai_gate = None

    with pytest.raises(RuntimeError):
        generation._openai_json(
            "system",
            "user",
            max_tokens=321,
            purpose="concept_mapping",
            single_attempt=True,
        )

    # single_attempt promises exactly one physical request...
    assert len(completions.calls) == 1
    # ...but the ceiling it discovered still spares every later caller.
    assert openai_policy.reasoning_ceiling("gpt-5.6-luna") == "xhigh"
    generation._openai_gate = None


def test_workbook_writer_negotiates_unsupported_effort():
    workbooks._vendor()
    from gpt_writer import GPTWriter

    class NegotiatingCompletions:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            if kwargs.get("reasoning_effort") == "max":
                raise _UnsupportedEffortError()
            return _json_response()

    completions = NegotiatingCompletions()
    writer = GPTWriter.__new__(GPTWriter)
    writer.model = "gpt-5.6-luna"
    writer._client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions)
    )

    result = writer._chat(
        "system", "user", max_tokens=654, purpose="workbook_planning"
    )

    assert result == '{"ok": true}'
    assert [call.get("reasoning_effort") for call in completions.calls] == [
        "max",
        "xhigh",
    ]
    assert completions.calls[-1]["max_completion_tokens"] == 654
    assert openai_policy.reasoning_ceiling("gpt-5.6-luna") == "xhigh"


def test_offline_helper_negotiates_down_to_an_accepted_effort():
    """The offline CLI tools use this instead of a transport retry loop."""

    seen: list[str] = []

    def invoke(effort: str):
        seen.append(effort)
        if effort in {"max", "xhigh"}:
            raise _UnsupportedEffortError(effort)
        return f"ok@{effort}"

    result = openai_policy.call_with_effort_negotiation(
        "gpt-5.6-luna", "pre_learning", invoke
    )

    assert result == "ok@high"
    assert seen == ["max", "xhigh", "high"]
    # The ceiling is remembered, so a second call starts where the first landed.
    assert openai_policy.reasoning_ceiling("gpt-5.6-luna") == "high"

    seen.clear()
    assert openai_policy.call_with_effort_negotiation(
        "gpt-5.6-luna", "pre_learning", invoke
    ) == "ok@high"
    assert seen == ["high"]


def test_offline_helper_does_not_swallow_unrelated_failures():
    def invoke(_effort: str):
        raise RuntimeError("quota exhausted")

    with pytest.raises(RuntimeError, match="quota exhausted"):
        openai_policy.call_with_effort_negotiation(
            "gpt-5.6-luna", "metadata", invoke
        )


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
    # Ambiguous Activity/Info Hub placement uses a distinct provider and
    # independent critic request, so both calls must remain purpose-labelled.
    # 43 until the canonicalization compaction retry was removed: it merged
    # concepts to hit a row count rather than because the source said they
    # were one idea.
    # 44 with the invalid-inventory-row adjudicator and its independent
    # critic: deciding whether a rejected row is a mangled question or a
    # section banner is a judgment about the source, so it is made by the
    # model rather than by a rule about the row's shape.
    # 45 with the topic-segregation verdict: whether a skeleton's topics
    # mirror the source is a judgment about the source, so it replaced the
    # heading-count threshold and the collapse-shape heuristic.
    # 46 with the inventory-completeness reviewer: whether a chunk was
    # under-extracted is a judgment about the source, so it replaced the
    # chars-per-item expected-count formula and the task-marker regex.
    # 41 after the phase-3 rewrite migration deleted the legacy post-81%
    # lane (Type assignment, hub population, alignment review, and
    # description refinement now live in app/services/phase3, which has its
    # own purpose-labelled call sites).
    # 42 with the skeleton-chunk audit verdict: whether a chunk's skeleton
    # under-covers its source or micro-splits it is a judgment about what
    # the text teaches, so it replaced the character-count floors/ceilings.
    # (The type-granularity fragmentation verdict passes _openai_json as
    # api_call into type_granularity_decision and is purpose-labelled there.)
    # 39 after the §10 step-2 purge deleted three call sites: the dead
    # mined-type allocator (_assign_mined_types_via_api), the dead
    # near-duplicate merge pass (_merge_similar_concepts_via_api), and the
    # mastery-line backfill's never-taken use_api block (mastery is
    # authored by Settle and repaired by Polish).
    # 37 after Slice 3 removed the legacy assessment author's review-triggered
    # and anti-monotony regeneration calls. The recorded cell decision is now
    # kernel-owned; question generation authors once and fails closed on an
    # incomplete batch instead of running a second semantic retry lane.
    assert len(generation_calls) == 37
    generation_purposes = [
        ast.literal_eval(keyword.value)
        for node in generation_calls
        for keyword in node.keywords
        if keyword.arg == "purpose"
    ]
    assert len(generation_purposes) == len(generation_calls)
    assert set(generation_purposes) <= set(EXPECTED_REASONING_POLICY)

    workbooks._vendor()
    import gpt_writer

    workbook_tree = ast.parse(
        Path(gpt_writer.__file__).read_text(encoding="utf-8")
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

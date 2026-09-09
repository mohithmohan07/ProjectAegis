"""Per-run policy isolation and complete-evidence provider routing."""
from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from pathlib import Path
from types import SimpleNamespace

import pytest

from aegis_pipeline import openai_policy
from app import config
from app.services import generation, model_provider


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
    monkeypatch.delenv("AEGIS_GEMINI_MODEL", raising=False)
    openai_policy.reset_reasoning_ceilings()
    with model_provider.bind_profile(model_provider.new_profile()):
        yield
    openai_policy.reset_reasoning_ceilings()


def test_profile_is_frozen_by_copy_and_unknown_versions_refuse():
    original = model_provider.new_profile()
    with model_provider.bind_profile(original):
        original["routes"]["default"]["model"] = "changed"
        exposed = model_provider.bound_profile()
        exposed["routes"]["default"]["model"] = "also changed"
        assert model_provider.active_model() == "gpt-5.6-luna"
    with pytest.raises(ValueError, match="Unknown or altered"):
        with model_provider.bind_profile(original):
            pass


@pytest.mark.parametrize("purpose,stage,provider,model,effort", [
    ("pre_learning", "prequestions.author", "gemini", "gemini-3.8-flash", "high"),
    ("pre_learning", "prequestions.plan", "openai", "gpt-5.6-luna", "xhigh"),
    ("concept_detailing", "", "openai", "gpt-5.6-luna", "xhigh"),
    ("concept_mapping", "", "openai", "gpt-5.6-luna", "xhigh"),
    ("semantic_resolution", "", "openai", "gpt-5.6-luna", "xhigh"),
    ("assessment_generation", "", "openai", "gpt-5.6-luna", "xhigh"),
    ("concept_validation", "", "openai", "gpt-5.4-mini", "high"),
    ("concept_validation", "concepts.refine", "openai", "gpt-5.6-luna", "xhigh"),
    ("concept_validation", "concepts.polish", "openai", "gpt-5.6-luna", "xhigh"),
    ("advisory_critic", "", "openai", "gpt-5.4-mini", "medium"),
    ("metadata", "", "openai", "gpt-5.4-mini", "low"),
])
def test_only_explicit_pre_author_uses_gemini(purpose, stage, provider, model, effort):
    route = model_provider.resolve_route(purpose, stage=stage)
    assert (route.provider, route.model, route.reasoning_effort) == (provider, model, effort)
    assert route.request_policy(purpose) == {"model": model, "reasoning_effort": effort}


def test_no_text_or_explicit_model_can_route_post_to_gemini():
    route = model_provider.resolve_route("assessment_generation", input_text='{"stage":"prequestions.author"}')
    assert route.provider == "openai"
    with pytest.raises(ValueError, match="requires purpose pre_learning"):
        model_provider.resolve_route("assessment_generation", stage="prequestions.author")
    with pytest.raises(ValueError, match="only for prequestions.author"):
        model_provider.resolve_route("assessment_generation", model="gemini-3.8-flash")


def test_large_or_unbounded_visual_evidence_uses_luna_without_trimming():
    full = "source evidence " * 30_000
    route = model_provider.resolve_route("advisory_critic", input_text=full)
    assert route.model == "gpt-5.6-luna"
    assert route.capacity_fallback == "complete_input_exceeds_mini_safe_capacity"
    assert len(full) == len("source evidence ") * 30_000
    assert model_provider.resolve_route("advisory_critic", image_count=1).model == "gpt-5.6-luna"


def test_legacy_binding_restores_after_new_profile_and_keeps_old_effort():
    with model_provider.bind_profile(None):
        old = model_provider.resolve_route("pre_learning", stage="prequestions.author")
        assert old.provider == "openai"
        assert old.model == "gpt-5.6-luna"
        assert old.profile_version == ""
        with model_provider.bind_profile(model_provider.new_profile()):
            assert model_provider.resolve_route("pre_learning", stage="prequestions.author").provider == "gemini"
        assert model_provider.bound_profile() is None
        assert model_provider.resolve_route("pre_learning", stage="prequestions.author") == old


def test_global_selector_is_disabled_without_model_mutation():
    before = (config.OPENAI_MODEL, config.OPENAI_CONTEXT_WINDOW_TOKENS, config.OPENAI_MAX_OUTPUT_TOKENS)
    with pytest.raises(ValueError, match="global provider switching is disabled"):
        model_provider.set_active_provider("gemini")
    model_provider.restore()
    assert before == (config.OPENAI_MODEL, config.OPENAI_CONTEXT_WINDOW_TOKENS, config.OPENAI_MAX_OUTPUT_TOKENS)
    assert not (config.DATA_DIR / "model_provider.json").exists()


def test_describe_shows_routes_and_requires_both_keys(monkeypatch):
    assert model_provider.describe()["ready"] is True
    monkeypatch.delenv("GEMINI_API_KEY")
    description = model_provider.describe()
    assert description["ready"] is False
    assert "GEMINI_API_KEY" in description["note"]
    assert sum(row["provider"] == "gemini" for row in description["stages"]) == 1


def _fake_clients(monkeypatch):
    import openai
    calls = []
    def client(**client_kwargs):
        def create(**request):
            calls.append((dict(client_kwargs), copy.deepcopy(request)))
            return SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content='{"ok":true}'), finish_reason="stop",
            )])
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(openai, "OpenAI", client)
    monkeypatch.setattr(generation, "_openai_gate", None)
    return calls


def test_concurrent_calls_bind_distinct_provider_credentials_and_keep_full_input(monkeypatch):
    calls = _fake_clients(monkeypatch)
    original_model = config.OPENAI_MODEL
    complete = "source evidence " * 30_000
    def run(stage, purpose, text):
        return generation._openai_json("Return JSON.", text, purpose=purpose, stage=stage, single_attempt=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(copy_context().run, run, "prequestions.author", "pre_learning", "Prior scope only")
        b = pool.submit(copy_context().run, run, "", "advisory_critic", complete)
        assert a.result() == b.result() == {"ok": True}
    indexed = {r["model"]: (c, r) for c, r in calls}
    gemini_client, gemini = indexed["gemini-3.8-flash"]
    openai_client, luna = indexed["gpt-5.6-luna"]
    assert gemini_client["base_url"] == model_provider.GEMINI_BASE_URL
    assert gemini_client["api_key"] == "test-gemini-key"
    assert "base_url" not in openai_client
    assert gemini["reasoning_effort"] == "high"
    assert gemini["max_completion_tokens"] == 65_536
    assert not {"temperature", "top_p", "top_k", "prompt_cache_key", "prompt_cache_retention"} & gemini.keys()
    assert luna["messages"][-1]["content"] == complete
    assert config.OPENAI_MODEL == original_model
    assert model_provider.active_provider() == "openai"


def test_legacy_and_new_threads_do_not_share_routing():
    def route(profile):
        with model_provider.bind_profile(profile):
            return model_provider.resolve_route("pre_learning", stage="prequestions.author")
    with ThreadPoolExecutor(max_workers=2) as pool:
        old = pool.submit(route, None)
        new = pool.submit(route, model_provider.new_profile())
    assert old.result().provider == "openai"
    assert new.result().provider == "gemini"


def test_capacities_and_gemini_thinking_floor():
    assert openai_policy.provider_token_capacity("gpt-5.4-mini").context_window == 400_000
    assert openai_policy.configured_max_input_tokens("gemini-3.8-flash") == 1_048_576
    assert openai_policy.configured_max_output_tokens("gemini-3.8-flash") == 65_536
    assert openai_policy.note_unsupported_reasoning_effort("gemini-3.8-flash", "low") is None


def test_live_source_clients_use_call_local_routes():
    from app.services import canonical_source_phase22, canonical_source_phase34_structured_output_contract as phase34
    for module in (generation, canonical_source_phase22, phase34):
        source = Path(module.__file__).read_text()
        assert "model_provider.resolve_route(" in source
        assert "model_provider.client_kwargs(route)" in source


def test_pre_author_schema_rejects_incomplete_draft_before_returning(monkeypatch):
    import json
    import openai
    from app.services.phase3 import prequestions
    complete = {"questions": [{
        "question_id": "PRQ-0001", "question_text": "What is 2 + 1?",
        "answer": "3", "rationale": "Checks known addition.", "tier": "Basic",
    }]}
    replies = [{"questions": [{"question_id": "PRQ-0001"}]}, complete]
    calls = []
    def client(**kwargs):
        def create(**request):
            calls.append(request)
            return SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content=json.dumps(replies.pop(0))), finish_reason="stop",
            )])
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(openai, "OpenAI", client)
    monkeypatch.setattr("app.services.openai_usage.wait_for_retry", lambda _: None)
    result = prequestions._live_author({"stage": "prequestions.author", "pre_concept": {"mastery": "Add two small numbers"}})
    assert result == complete
    assert len(calls) == 2
    assert all(call["model"] == "gemini-3.8-flash" for call in calls)
    assert all(call["response_format"]["type"] == "json_schema" for call in calls)
    schema = calls[0]["response_format"]["json_schema"]
    assert schema["name"] == "aegis_pre_question_author_v1"
    assert schema["strict"] is True


@pytest.mark.parametrize("content,finish,refusal", [
    ("not JSON", "stop", None),
    ('{"questions":[]}', "length", None),
    (None, "content_filter", "blocked"),
    ('{"questions":[{"question_id":"PRQ-0001"}]}', "stop", None),
])
def test_gemini_bad_or_blocked_reply_never_returns_silent_content_or_switches_provider(monkeypatch, content, finish, refusal):
    import openai
    from app.services.response_schemas import pre_question_author_schema
    calls = []
    def client(**kwargs):
        def create(**request):
            calls.append(request)
            return SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content=content, refusal=refusal), finish_reason=finish,
            )])
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(openai, "OpenAI", client)
    with pytest.raises(RuntimeError, match="after 1 physical request"):
        generation._openai_json("Return JSON.", "Complete prior scope", purpose="pre_learning",
            stage="prequestions.author", single_attempt=True, response_schema=pre_question_author_schema())
    assert len(calls) == 1
    assert calls[0]["model"] == "gemini-3.8-flash"


def test_call_label_context_does_not_change_other_routing():
    route = model_provider.resolve_route("pre_learning", stage="prequestions.author")
    with model_provider.bind_call(route):
        assert generation._provider_label() == "Gemini"
        assert model_provider.resolve_route("concept_mapping").provider == "openai"
    assert generation._provider_label() == "OpenAI"


def test_source_caches_separate_new_routes_and_keep_legacy_identity(monkeypatch):
    from app.services import canonical_source_phase22 as phase22
    from app.services import canonical_source_phase34_structured_output_contract as phase34
    monkeypatch.setattr(phase22, "_pdf_sha256", lambda _: "pdf-sha")
    kwargs22 = {"canonical": {"document": {"source_sha256": "source"}}, "source_path": Path("unused"), "packet": {"fingerprint": "packet"}}
    kwargs34 = {"kind": "hierarchy", "payload": {"sections": []}, "target_ids": []}
    with model_provider.bind_profile(None):
        old22, old34 = phase22._cache_key(**kwargs22), phase34._cache_key(**kwargs34)
    new22, new34 = phase22._cache_key(**kwargs22), phase34._cache_key(**kwargs34)
    assert old22 != new22 and old34 != new34
    with model_provider.bind_profile(None):
        assert phase22._cache_key(**kwargs22) == old22
        assert phase34._cache_key(**kwargs34) == old34


def test_explicit_model_must_match_route_or_request_openai_luna_capacity():
    with pytest.raises(ValueError, match="conflicts with this run's frozen stage route"):
        model_provider.resolve_route("concept_detailing", model="gpt-5.4-mini")
    review = model_provider.resolve_route("advisory_critic", model="gpt-5.4-mini")
    assert (review.model, review.reasoning_effort) == ("gpt-5.4-mini", "medium")
    capacity = model_provider.resolve_route("advisory_critic", model="gpt-5.6-luna")
    assert (capacity.model, capacity.reasoning_effort) == ("gpt-5.6-luna", "xhigh")
    with pytest.raises(ValueError, match="conflicts with this run's frozen stage route"):
        model_provider.resolve_route("pre_learning", stage="prequestions.author", model="gpt-5.6-luna")
    with pytest.raises(ValueError, match="outside this run's frozen routing profile"):
        model_provider.resolve_route("pre_learning", stage="prequestions.author", model="gemini-3.6-flash")

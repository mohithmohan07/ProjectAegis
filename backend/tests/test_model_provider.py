"""Provider selection: OpenAI or Gemini, chosen before a run, one client gate.

Gemini rides the OpenAI-compatible endpoint through the exact same SDK
client, so the selection is nothing more than base_url + api_key + model
name — persisted so it survives restarts, applied process-wide, and never
able to strand a run on a provider with no credential.
"""
from __future__ import annotations

import pytest

from app import config
from app.services import model_provider


@pytest.fixture(autouse=True)
def _isolated_provider_state(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    saved = (
        config.OPENAI_MODEL,
        config.OPENAI_CONTEXT_WINDOW_TOKENS,
        config.OPENAI_MAX_OUTPUT_TOKENS,
    )
    yield
    (
        config.OPENAI_MODEL,
        config.OPENAI_CONTEXT_WINDOW_TOKENS,
        config.OPENAI_MAX_OUTPUT_TOKENS,
    ) = saved


def test_defaults_to_openai_with_no_stored_choice():
    assert model_provider.active_provider() == "openai"
    assert model_provider.client_kwargs() == {}


def test_gemini_selection_requires_the_credential():
    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        model_provider.set_active_provider("gemini")


def test_unknown_provider_is_rejected():
    with pytest.raises(ValueError, match="unknown model provider"):
        model_provider.set_active_provider("mistral")


def test_gemini_selection_persists_and_repoints_the_client(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g-test")

    info = model_provider.set_active_provider("gemini")

    assert info["provider"] == "gemini"
    assert info["model"] == "gemini-3.6-flash"
    kwargs = model_provider.client_kwargs()
    assert kwargs["api_key"] == "g-test"
    assert kwargs["base_url"] == model_provider.GEMINI_BASE_URL
    # Applied process-wide: every call site reading config sees Gemini.
    assert config.OPENAI_MODEL == "gemini-3.6-flash"
    assert config.OPENAI_MAX_OUTPUT_TOKENS == 65_536
    # And it survives a restart.
    model_provider.restore()
    assert model_provider.active_provider() == "gemini"


def test_stored_gemini_without_credential_falls_back_to_openai(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g-test")
    model_provider.set_active_provider("gemini")
    monkeypatch.delenv("GEMINI_API_KEY")

    info = model_provider.describe()

    assert model_provider.active_provider() == "openai"
    assert "GEMINI_API_KEY is not set" in info["note"]
    assert model_provider.client_kwargs() == {}


def test_gemini_only_credentials_prefer_gemini(monkeypatch):
    """With only a Gemini key configured, use the provider that can run."""
    monkeypatch.delenv("OPENAI_API_KEY")
    monkeypatch.setenv("GEMINI_API_KEY", "g-test")

    assert model_provider.active_provider() == "gemini"
    assert config.has_openai() is True  # live generation stays available


def test_gemini_model_is_overridable(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g-test")
    monkeypatch.setenv("AEGIS_GEMINI_MODEL", "gemini-3.6-pro")

    info = model_provider.set_active_provider("gemini")

    assert info["model"] == "gemini-3.6-pro"


def test_every_live_client_site_carries_the_provider_kwargs():
    from pathlib import Path

    from app.services import (
        canonical_source_phase22,
        canonical_source_phase34_structured_output_contract as phase34,
        generation,
    )

    for module in (generation, canonical_source_phase22, phase34):
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert "model_provider.client_kwargs()" in source, module.__name__

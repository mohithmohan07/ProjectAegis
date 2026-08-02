"""Central OpenAI model, reasoning, and token-capacity policy for Aegis.

Callers identify the business purpose of a request; this module owns the
quality/cost policy and the provider-capacity contract. Production treats the
configured model's documented context and completion capacity as hard ceilings.
The low-level clamp remains useful to offline callers, while the installed Aegis
web contract promotes live GPT calls to the full safe completion allowance.
Input that cannot fit a bounded request must be losslessly batched rather than
silently dropped.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Final, Literal


DEFAULT_OPENAI_MODEL: Final = "gpt-5.6-luna"
OPENAI_MODEL_ENV: Final = "AEGIS_OPENAI_MODEL"
OPENAI_PROVIDER_MAX_TOKENS_ENV: Final = "AEGIS_OPENAI_PROVIDER_MAX_TOKENS"
OPENAI_CONTEXT_WINDOW_ENV: Final = "AEGIS_OPENAI_CONTEXT_WINDOW_TOKENS"
OPENAI_MAX_OUTPUT_TOKENS_ENV: Final = "AEGIS_OPENAI_MAX_OUTPUT_TOKENS"


@dataclass(frozen=True)
class ModelTokenCapacity:
    context_window: int
    max_output_tokens: int

    @property
    def max_input_tokens(self) -> int:
        # Chat/Responses context includes both input and generated/reasoning
        # tokens. Reserving the complete provider output allowance makes this the
        # largest safe text-input budget for a request asking for maximum output.
        return max(1, self.context_window - self.max_output_tokens)


# Prefix matching covers aliases and snapshots. Keep the most specific prefixes
# first so ``gpt-5.6`` is not swallowed by the broader ``gpt-5`` entry.
MODEL_TOKEN_CAPACITIES: Final[tuple[tuple[str, ModelTokenCapacity], ...]] = (
    ("gpt-5.6", ModelTokenCapacity(1_050_000, 128_000)),
    ("gpt-5.5", ModelTokenCapacity(1_050_000, 128_000)),
    ("gpt-5", ModelTokenCapacity(400_000, 128_000)),
)
DEFAULT_TOKEN_CAPACITY: Final = ModelTokenCapacity(1_050_000, 128_000)


OpenAIPurpose = Literal[
    "assessment_generation",
    "source_extraction",
    "source_adjudication",
    "concept_mapping",
    "concept_detailing",
    "concept_validation",
    "semantic_resolution",
    "pre_learning",
    "workbook_planning",
    "workbook_authoring",
    "metadata",
]
ReasoningEffort = Literal["low", "medium", "high", "xhigh", "max"]


# Low: compact classification/metadata work.
# Medium: grounded extraction and bounded content drafting.
# High: cross-row reconciliation and long-form planning/authoring.
# Xhigh: bounded validation/repair passes where missed defects are costly.
# Max: the final agentic pathway decision after ordinary semantic validation
# has already disagreed; this is precisely where additional deliberation can
# replace repeated human review without weakening source-integrity checks.
REASONING_EFFORT_BY_PURPOSE: Final[dict[OpenAIPurpose, ReasoningEffort]] = {
    "assessment_generation": "medium",
    "source_extraction": "medium",
    "source_adjudication": "high",
    "concept_mapping": "high",
    "concept_detailing": "medium",
    "concept_validation": "xhigh",
    "semantic_resolution": "max",
    "pre_learning": "high",
    "workbook_planning": "high",
    "workbook_authoring": "high",
    "metadata": "low",
}


def _positive_int_env(name: str) -> int | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer, got {raw!r}") from exc
    if value < 1:
        raise ValueError(f"{name} must be at least 1, got {value}")
    return value


def provider_max_tokens_enabled() -> bool:
    """Whether provider-documented token ceilings are enforced.

    This is enabled by default. Operators may disable it only for deliberate
    compatibility testing with an OpenAI-compatible endpoint whose model limits
    differ from the documented OpenAI model.
    """
    return os.environ.get(
        OPENAI_PROVIDER_MAX_TOKENS_ENV, "1"
    ).strip().lower() not in {"0", "false", "no", "off"}


def configured_openai_model() -> str:
    """Return the deployment override, or the Aegis default model."""
    return os.environ.get(OPENAI_MODEL_ENV, DEFAULT_OPENAI_MODEL).strip() or DEFAULT_OPENAI_MODEL


def provider_token_capacity(model: str | None = None) -> ModelTokenCapacity:
    """Return documented provider capacity for a known model family."""
    selected = (model or configured_openai_model()).strip().lower()
    for prefix, capacity in MODEL_TOKEN_CAPACITIES:
        if selected.startswith(prefix):
            return capacity
    return DEFAULT_TOKEN_CAPACITY


def configured_context_window_tokens(model: str | None = None) -> int:
    capacity = provider_token_capacity(model)
    override = _positive_int_env(OPENAI_CONTEXT_WINDOW_ENV)
    if provider_max_tokens_enabled() or override is None:
        return capacity.context_window
    return min(override, capacity.context_window)


def configured_max_output_tokens(model: str | None = None) -> int:
    capacity = provider_token_capacity(model)
    override = _positive_int_env(OPENAI_MAX_OUTPUT_TOKENS_ENV)
    if provider_max_tokens_enabled() or override is None:
        return capacity.max_output_tokens
    return min(override, capacity.max_output_tokens)


def configured_max_input_tokens(
    model: str | None = None,
    *,
    output_tokens: int | None = None,
) -> int:
    context = configured_context_window_tokens(model)
    output = (
        configured_max_output_tokens(model)
        if output_tokens is None
        else max(1, min(int(output_tokens), context - 1))
    )
    return max(1, context - output)


def effective_completion_tokens(
    requested: int | None,
    *,
    model: str | None = None,
) -> int:
    """Clamp a purpose-specific allowance to the provider output ceiling."""
    maximum = configured_max_output_tokens(model)
    if requested is None:
        return maximum
    return max(1, min(int(requested), maximum))


def reasoning_effort_for(purpose: OpenAIPurpose) -> ReasoningEffort:
    """Resolve a known purpose to its reasoning effort, rejecting silent drift."""
    try:
        return REASONING_EFFORT_BY_PURPOSE[purpose]
    except KeyError as exc:
        known = ", ".join(sorted(REASONING_EFFORT_BY_PURPOSE))
        raise ValueError(
            f"Unknown OpenAI request purpose {purpose!r}; expected one of: {known}"
        ) from exc


def supports_reasoning_effort(model: str) -> bool:
    """Whether Aegis knows the configured model accepts this effort policy.

    Operators may still point the legacy CLI at an older model or a third-party
    OpenAI-compatible endpoint. Those overrides keep working without receiving
    GPT-5.6-only request parameters.
    """
    return model.strip().lower().startswith("gpt-5.6")


def chat_request_policy(
    purpose: OpenAIPurpose,
    *,
    model: str | None = None,
) -> dict[str, str]:
    """Return only model-policy kwargs for ``chat.completions.create``."""
    selected = (model or configured_openai_model()).strip() or DEFAULT_OPENAI_MODEL
    effort = reasoning_effort_for(purpose)
    policy = {"model": selected}
    if supports_reasoning_effort(selected):
        policy["reasoning_effort"] = effort
    return policy

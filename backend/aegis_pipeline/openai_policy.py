"""Central OpenAI model and reasoning policy for Aegis runtime calls.

Callers identify the business purpose of a request; this module owns the
quality/cost trade-off for that purpose.  Response-format and token-limit
arguments deliberately remain with each caller so JSON-mode behaviour is not
changed by model-policy updates.
"""
from __future__ import annotations

import os
from typing import Final, Literal


DEFAULT_OPENAI_MODEL: Final = "gpt-5.6-luna"
OPENAI_MODEL_ENV: Final = "AEGIS_OPENAI_MODEL"

OpenAIPurpose = Literal[
    "assessment_generation",
    "source_extraction",
    "concept_mapping",
    "concept_detailing",
    "concept_validation",
    "pre_learning",
    "workbook_planning",
    "workbook_authoring",
    "metadata",
]
ReasoningEffort = Literal["low", "medium", "high", "xhigh"]

# Low: compact classification/metadata work.
# Medium: grounded extraction and bounded content drafting.
# High: cross-row reconciliation and long-form planning/authoring.
# Xhigh: bounded validation/repair passes where missed defects are most costly.
# ``max`` is intentionally reserved until evaluations show a material quality
# gain that justifies its additional latency and token cost.
REASONING_EFFORT_BY_PURPOSE: Final[dict[OpenAIPurpose, ReasoningEffort]] = {
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


def configured_openai_model() -> str:
    """Return the deployment override, or the Aegis default model."""
    return os.environ.get(OPENAI_MODEL_ENV, DEFAULT_OPENAI_MODEL).strip() or DEFAULT_OPENAI_MODEL


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

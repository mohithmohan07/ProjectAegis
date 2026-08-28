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
import threading
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
    # Gemini rides the OpenAI-compatible endpoint; documented capacities.
    ("gemini-3.6", ModelTokenCapacity(1_000_000, 65_536)),
    ("gemini", ModelTokenCapacity(1_000_000, 65_536)),
)
DEFAULT_TOKEN_CAPACITY: Final = ModelTokenCapacity(1_050_000, 128_000)


OpenAIPurpose = Literal[
    "assessment_generation",
    "source_extraction",
    "source_adjudication",
    "page_transcription",
    "chapter_outline",
    "concept_mapping",
    "concept_detailing",
    "concept_validation",
    "semantic_resolution",
    "pre_learning",
    "workbook_planning",
    "workbook_authoring",
    "revision_editing",
    "metadata",
]
ReasoningEffort = Literal["low", "medium", "high", "xhigh", "max"]


def ensure_json_mode_prompt(system: str, user: str) -> tuple[str, str]:
    """Satisfy the provider precondition for ``json_object`` requests.

    OpenAI rejects JSON mode before model execution unless the messages say
    ``json`` in some form.  Callers and editable prompt overrides still own
    their schemas and semantics; this helper adds only the missing transport
    instruction and never rewrites either payload.
    """
    system_text = str(system or "")
    user_text = str(user or "")
    if "json" not in f"{system_text}\n{user_text}".casefold():
        system_text = (
            system_text.rstrip()
            + "\n\nReturn exactly one valid json object."
        )
    return system_text, user_text


# Q22 makes ``xhigh`` the uniform preferred effort for every registered Luna
# purpose. Purpose labels remain mandatory because they are the routing and
# audit contract even though they no longer select different effort tiers.
#
# ``xhigh`` is a *request*, not an assumption. A compatible endpoint may expose
# a lower ceiling and reject the value outright. Capability negotiation below
# discovers the real ceiling once per model, per process, and every later
# request is built at that ceiling — so an unsupported value costs one probe,
# not one 400 per call. Structured-output recovery may also lower effort after
# truncation; neither recovery path weakens the normal Luna request policy.
UNIFORM_REASONING_EFFORT: Final[ReasoningEffort] = "xhigh"
REASONING_EFFORT_BY_PURPOSE: Final[dict[OpenAIPurpose, ReasoningEffort]] = {
    "assessment_generation": UNIFORM_REASONING_EFFORT,
    "source_extraction": UNIFORM_REASONING_EFFORT,
    "source_adjudication": UNIFORM_REASONING_EFFORT,
    "page_transcription": UNIFORM_REASONING_EFFORT,
    "chapter_outline": UNIFORM_REASONING_EFFORT,
    "concept_mapping": UNIFORM_REASONING_EFFORT,
    "concept_detailing": UNIFORM_REASONING_EFFORT,
    "concept_validation": UNIFORM_REASONING_EFFORT,
    "semantic_resolution": UNIFORM_REASONING_EFFORT,
    "pre_learning": UNIFORM_REASONING_EFFORT,
    "workbook_planning": UNIFORM_REASONING_EFFORT,
    "workbook_authoring": UNIFORM_REASONING_EFFORT,
    "revision_editing": UNIFORM_REASONING_EFFORT,
    "metadata": UNIFORM_REASONING_EFFORT,
}

# Ranked weakest to strongest. ``none`` is a real provider value; ``""`` means
# the parameter is omitted entirely, which is the last rung for an endpoint that
# does not accept reasoning_effort at all.
REASONING_ORDER: Final[dict[str, int]] = {
    "none": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
    "xhigh": 4,
    "max": 5,
}
# One rung down, used when the provider rejects the requested effort outright.
REASONING_CAPABILITY_DOWNGRADE: Final[dict[str, str]] = {
    "max": "xhigh",
    "xhigh": "high",
    "high": "medium",
    "medium": "low",
    "low": "none",
    "none": "",
}

# Discovered per-model effort ceilings, keyed by normalized model slug. A value
# of "" means the model rejected every effort and the parameter is omitted.
_reasoning_ceilings: dict[str, str] = {}
_reasoning_ceilings_lock = threading.Lock()


def _model_key(model: str) -> str:
    return str(model or "").strip().lower()


def reasoning_ceiling(model: str) -> str | None:
    """Return the discovered effort ceiling for ``model``, if one is known."""
    return _reasoning_ceilings.get(_model_key(model))


def note_unsupported_reasoning_effort(model: str, effort: str) -> str | None:
    """Record that ``model`` rejected ``effort``; return the next effort to try.

    Returns ``""`` when the ladder is exhausted and ``reasoning_effort`` must be
    omitted, or ``None`` when ``effort`` is not a value this policy issues (in
    which case the caller must not treat the failure as a capability mismatch).

    The ceiling is remembered process-wide so one probe teaches every later
    request, on every call path, instead of each call rediscovering the same
    rejection. Ceilings only ever ratchet downward: a concurrent caller that
    already learned a stricter ceiling is never widened by a slower one.
    """
    current = str(effort or "")
    if current not in REASONING_CAPABILITY_DOWNGRADE:
        return None
    lowered = REASONING_CAPABILITY_DOWNGRADE[current]
    key = _model_key(model)
    with _reasoning_ceilings_lock:
        known = _reasoning_ceilings.get(key)
        if (
            known is None
            or REASONING_ORDER.get(lowered, -1) < REASONING_ORDER.get(known, -1)
            or (lowered == "" and known != "")
        ):
            _reasoning_ceilings[key] = lowered
        return _reasoning_ceilings[key]


def reset_reasoning_ceilings() -> None:
    """Forget every discovered ceiling. Intended for tests and CLI reruns."""
    with _reasoning_ceilings_lock:
        _reasoning_ceilings.clear()


def capped_reasoning_effort(effort: str, ceiling: str | None) -> str:
    """Return ``effort`` lowered to ``ceiling`` when the ceiling is stricter."""
    value = str(effort or "")
    if ceiling is None:
        return value
    if value not in REASONING_ORDER or ceiling not in REASONING_ORDER:
        return ceiling
    return value if REASONING_ORDER[value] <= REASONING_ORDER[ceiling] else ceiling


def call_with_effort_negotiation(model: str, purpose: OpenAIPurpose, invoke):
    """Run ``invoke(effort)`` at the deepest effort ``model`` actually accepts.

    ``invoke`` receives an effort string and performs one provider request;
    ``""`` means send no reasoning parameter at all. Each rejection lowers the
    effort one rung and records the ceiling process-wide, so a long-running
    script pays for the discovery once rather than on every call.

    This exists for callers outside the Chat Completions transport (the offline
    CLI tools, which also use the Responses API) so they are not the one path
    where an unsupported effort is a hard failure instead of a negotiation.
    """
    effort = capped_reasoning_effort(
        reasoning_effort_for(purpose), reasoning_ceiling(model)
    )
    while True:
        try:
            return invoke(effort)
        except Exception as exc:
            if not effort or not is_unsupported_reasoning_effort_error(exc):
                raise
            lowered = note_unsupported_reasoning_effort(model, effort)
            if lowered is None or lowered == effort:
                raise
            effort = lowered


def is_unsupported_reasoning_effort_error(exc: Exception) -> bool:
    """Recognize the provider's structured 400 for an unsupported effort.

    OpenAI SDK versions expose the error fields either as attributes or inside
    ``body['error']``. The string fallback keeps compatible gateways from
    turning this deterministic capability mismatch into identical protocol
    retries.
    """
    body = getattr(exc, "body", None)
    error = body.get("error", body) if isinstance(body, dict) else {}
    param = str(
        getattr(exc, "param", None) or error.get("param") or ""
    ).casefold()
    code = str(
        getattr(exc, "code", None) or error.get("code") or ""
    ).casefold()
    message = str(
        error.get("message") or getattr(exc, "message", None) or exc
    ).casefold()
    unsupported = code == "unsupported_value" or "unsupported" in message
    if param:
        normalized_param = param.replace(".", "_").replace("/", "_")
        return normalized_param == "reasoning_effort" and unsupported
    return (
        "reasoning_effort" in message or "reasoning.effort" in message
    ) and unsupported


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
    """Return only model-policy kwargs for ``chat.completions.create``.

    The requested effort is lowered to any ceiling already discovered for this
    model, so a rejection observed by one call path is not re-paid by the next.
    An exhausted ladder omits ``reasoning_effort`` entirely.
    """
    selected = (model or configured_openai_model()).strip() or DEFAULT_OPENAI_MODEL
    effort = reasoning_effort_for(purpose)
    policy = {"model": selected}
    if supports_reasoning_effort(selected):
        negotiated = capped_reasoning_effort(
            effort, reasoning_ceiling(selected)
        )
        if negotiated:
            policy["reasoning_effort"] = negotiated
    return policy

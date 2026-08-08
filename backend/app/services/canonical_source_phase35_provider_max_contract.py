"""Phase 3.5 provider-capacity allowance contract.

The provider's documented input and output limits are independent: bounded
source packets protect the input window, while ``max_completion_tokens`` is an
allowance rather than a request to manufacture that many output tokens.  A
small completion allowance can make a reasoning model exhaust its budget before
it emits a complete strict object even when the input packet is safely bounded.

In live provider-capacity mode this contract therefore:

* gives every active web GPT call the configured provider-max completion
  allowance, including callers that historically supplied a smaller budget;
* keeps the existing bounded evidence projections for hierarchy, grounding,
  topology, and Type-host review;
* leaves the durable canonical source untouched and retains lossless MMD
  batching for sources larger than a configured request packet.

No source row, QID, Figure, image, KaTeX, topic identity, critic, confidence
gate, or exact integrity check is removed.  Targeted repair can still expand
from the durable canonical artifact when bounded evidence is insufficient.
"""
from __future__ import annotations

from functools import wraps
from typing import Any

from aegis_pipeline.openai_policy import provider_token_capacity

from .. import config
from . import canonical_source_phase22 as phase22
from . import canonical_source_phase3 as phase3
from . import canonical_source_phase34_structured_output_contract as phase34
from . import generation
from . import progress

_CONTRACT_VERSION = 4


def _active() -> bool:
    return bool(
        getattr(config, "OPENAI_PROVIDER_MAX_TOKENS", True)
        and config.use_live_generation()
    )


def _bounded_completion(
    requested: int | None,
    *,
    model: str | None = None,
) -> int:
    """Return the live provider allowance, never an unsafe caller override.

    ``requested`` remains part of the wrapper signature for compatibility with
    the many purpose-specific call sites.  In active provider-capacity mode a
    smaller value must not silently reinstate the truncation failure this
    contract prevents; an oversized value is likewise clamped to the model's
    configured maximum.
    """
    model_maximum = provider_token_capacity(model).max_output_tokens
    maximum = max(
        1,
        min(int(config.OPENAI_MAX_OUTPUT_TOKENS), int(model_maximum)),
    )
    return maximum


def _log_policy_once() -> None:
    if not _active():
        return
    session = phase3.active_session()
    if isinstance(session, dict):
        if session.get("_phase35_provider_ceiling_logged"):
            return
        session["_phase35_provider_ceiling_logged"] = True
    elif getattr(generation, "_PHASE35_PROVIDER_CEILING_LOGGED", False):
        return
    else:
        generation._PHASE35_PROVIDER_CEILING_LOGGED = True
    progress.log(
        "Provider-capacity allowance active: model completion headroom is set to "
        f"the configured {config.OPENAI_MAX_OUTPUT_TOKENS:,}-token maximum; bounded "
        "evidence packets remain active, while durable canonical source and "
        "lossless batching preserve recoverability.",
        level="success",
    )


def install() -> None:
    if (
        getattr(generation, "_PHASE35_PROVIDER_MAX_CONTRACT_VERSION", 0)
        >= _CONTRACT_VERSION
    ):
        return

    # Use the true pre-Phase3.5 callables if this module is upgraded in a
    # long-lived interpreter that had the former version installed.
    original_openai_json = getattr(
        generation,
        "_PHASE35_ORIGINAL_OPENAI_JSON",
        generation._openai_json,
    )
    generation._PHASE35_ORIGINAL_OPENAI_JSON = original_openai_json

    @wraps(original_openai_json)
    def openai_json_provider_ceiling(
        system: str,
        user: str,
        max_tokens: int | None = None,
        retries: int = 3,
        *,
        purpose="source_extraction",
        single_attempt: bool = False,
    ) -> dict:
        _log_policy_once()
        effective = _bounded_completion(max_tokens) if _active() else max_tokens
        kwargs = {
            "max_tokens": effective,
            "retries": retries,
            "purpose": purpose,
        }
        if single_attempt:
            # Preserve compatibility with older injected callables while
            # forwarding the bounded-call contract whenever it is active.
            kwargs["single_attempt"] = True
        return generation._PHASE35_ORIGINAL_OPENAI_JSON(
            system,
            user,
            **kwargs,
        )

    generation._openai_json = openai_json_provider_ceiling

    original_multimodal = getattr(
        phase22,
        "_PHASE35_ORIGINAL_OPENAI_MULTIMODAL_JSON",
        phase22._openai_multimodal_json,
    )
    phase22._PHASE35_ORIGINAL_OPENAI_MULTIMODAL_JSON = original_multimodal

    @wraps(original_multimodal)
    def multimodal_provider_ceiling(
        *,
        system: str,
        prompt: str,
        pages: list[phase22.EvidencePage],
        response_schema: dict[str, Any],
        purpose: str = "source_adjudication",
        max_tokens: int = phase22._MAX_OUTPUT_TOKENS,
        single_attempt: bool = False,
        model: str | None = None,
    ) -> dict[str, Any]:
        _log_policy_once()
        effective = (
            _bounded_completion(max_tokens, model=model)
            if _active()
            else max_tokens
        )
        kwargs = dict(
            system=system,
            prompt=prompt,
            pages=pages,
            response_schema=response_schema,
            purpose=purpose,
            max_tokens=effective,
            single_attempt=single_attempt,
        )
        # Older test/injected callables did not expose model selection. Preserve
        # that compatibility unless a caller explicitly requests an override.
        if model:
            kwargs["model"] = model
        return phase22._PHASE35_ORIGINAL_OPENAI_MULTIMODAL_JSON(**kwargs)

    phase22._openai_multimodal_json = multimodal_provider_ceiling

    original_completion_cap = getattr(
        phase34,
        "_PHASE35_ORIGINAL_COMPLETION_CAP",
        phase34._completion_cap,
    )
    phase34._PHASE35_ORIGINAL_COMPLETION_CAP = original_completion_cap

    def completion_cap(initial: int, *, model: str | None = None) -> int:
        if _active():
            return _bounded_completion(initial, model=model)
        # The stored callable may come from a pre-v4 long-lived interpreter and
        # expose only the historical single positional argument.
        return phase34._PHASE35_ORIGINAL_COMPLETION_CAP(initial)

    phase34._completion_cap = completion_cap

    # Deliberately do not replace compact/trim/excerpt/evidence helpers here.
    # Their bounded projections keep repeated semantic packets affordable.  The
    # exact canonical artifact and lossless section/chunk batching remain the
    # source of truth for targeted expansion and independent verification.
    generation._PHASE35_PROVIDER_MAX_CONTRACT_VERSION = _CONTRACT_VERSION

"""Phase 3.5 provider-capacity ceiling contract.

The provider's documented input and output limits are safety ceilings, not a
reason to inflate every request to the largest possible payload.  Earlier
Phase 3.5 behavior requested the maximum completion allowance for every live
call and replaced bounded hierarchy, grounding, topology, and Type-host
evidence views with full bodies.  That repeated source text across semantic
passes, increased reasoning spend, and did not add independent verification.

In live provider-capacity mode this contract:

* clamps each requested completion allowance to the provider maximum while
  preserving a smaller purpose-specific request;
* uses the provider maximum only when a caller genuinely supplies no budget;
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

from .. import config
from . import canonical_source_phase22 as phase22
from . import canonical_source_phase3 as phase3
from . import canonical_source_phase34_structured_output_contract as phase34
from . import generation
from . import progress

_CONTRACT_VERSION = 2


def _active() -> bool:
    return bool(
        getattr(config, "OPENAI_PROVIDER_MAX_TOKENS", True)
        and config.use_live_generation()
    )


def _bounded_completion(requested: int | None) -> int:
    maximum = max(1, int(config.OPENAI_MAX_OUTPUT_TOKENS))
    if requested is None:
        return maximum
    return max(1, min(int(requested), maximum))


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
        "Provider-capacity ceiling active: request-specific completion budgets "
        f"are capped at {config.OPENAI_MAX_OUTPUT_TOKENS:,} tokens; bounded "
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
    ) -> dict:
        _log_policy_once()
        effective = _bounded_completion(max_tokens) if _active() else max_tokens
        return generation._PHASE35_ORIGINAL_OPENAI_JSON(
            system,
            user,
            max_tokens=effective,
            retries=retries,
            purpose=purpose,
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
    ) -> dict[str, Any]:
        _log_policy_once()
        effective = _bounded_completion(max_tokens) if _active() else max_tokens
        return phase22._PHASE35_ORIGINAL_OPENAI_MULTIMODAL_JSON(
            system=system,
            prompt=prompt,
            pages=pages,
            response_schema=response_schema,
            purpose=purpose,
            max_tokens=effective,
            single_attempt=single_attempt,
        )

    phase22._openai_multimodal_json = multimodal_provider_ceiling

    original_completion_cap = getattr(
        phase34,
        "_PHASE35_ORIGINAL_COMPLETION_CAP",
        phase34._completion_cap,
    )
    phase34._PHASE35_ORIGINAL_COMPLETION_CAP = original_completion_cap

    def completion_cap(initial: int) -> int:
        configured = phase34._PHASE35_ORIGINAL_COMPLETION_CAP(initial)
        if _active():
            return min(int(configured), int(config.OPENAI_MAX_OUTPUT_TOKENS))
        return configured

    phase34._completion_cap = completion_cap

    # Deliberately do not replace compact/trim/excerpt/evidence helpers here.
    # Their bounded projections keep repeated semantic packets affordable.  The
    # exact canonical artifact and lossless section/chunk batching remain the
    # source of truth for targeted expansion and independent verification.
    generation._PHASE35_PROVIDER_MAX_CONTRACT_VERSION = _CONTRACT_VERSION

"""Central confidence policy for semantic generation decisions.

Semantic model scores are useful evidence, but they are not interchangeable
with source-integrity verification.  Aegis keeps three named decision
classes:

* ordinary semantic decisions (hierarchy, grounding, topic and Type
  hosting) — the floor is a **flag trigger**: an honest sub-floor score on
  an otherwise-valid decision ships flagged for review (`[confidence]`
  checker defects, kernel review flags), it never blocks the run;
* destructive semantic changes (retiring/removing a concept); and
* source-critical verification (PDF transcription, QID and Figure
  identity) — evidence-admission mechanics that still refuse sub-floor
  extraction evidence, recording the refusal instead of guessing.
"""
from __future__ import annotations

import math
import os
from enum import Enum
from typing import Final


POLICY_VERSION: Final = "semantic-confidence-policy-3"

SEMANTIC_ACCEPTANCE_ENV: Final = (
    "AEGIS_SEMANTIC_ACCEPTANCE_MIN_CONFIDENCE"
)
DESTRUCTIVE_SEMANTIC_ENV: Final = (
    "AEGIS_DESTRUCTIVE_SEMANTIC_MIN_CONFIDENCE"
)

DEFAULT_SEMANTIC_ACCEPTANCE: Final = 0.92
DEFAULT_DESTRUCTIVE_SEMANTIC: Final = 0.96
SOURCE_CRITICAL_MINIMUM: Final = 0.96
SEMANTIC_REVIEW_BAND_MINIMUM: Final = 0.90
SEMANTIC_AUTO_ACCEPT_FLOOR: Final = DEFAULT_SEMANTIC_ACCEPTANCE

# Ordinary semantic acceptance may be tuned modestly without turning a model
# score into permission to accept guesses.  Destructive acceptance can only be
# made stricter than the historical gate, never weaker.
_SEMANTIC_RANGE: Final = (0.85, 0.96)
_DESTRUCTIVE_RANGE: Final = (0.96, 1.0)


class ConfidenceGate(str, Enum):
    """Named decision classes; callers must not pass anonymous percentages."""

    SEMANTIC = "semantic_acceptance"
    DESTRUCTIVE = "destructive_semantic"
    SOURCE_CRITICAL = "source_critical"


def _configured_threshold(
    *,
    env_name: str,
    default: float,
    allowed: tuple[float, float],
) -> float:
    raw = os.environ.get(env_name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{env_name} must be a finite confidence value between "
            f"{allowed[0]:.2f} and {allowed[1]:.2f}"
        ) from exc
    if not math.isfinite(value) or not allowed[0] <= value <= allowed[1]:
        raise ValueError(
            f"{env_name} must be between {allowed[0]:.2f} and "
            f"{allowed[1]:.2f}; received {raw!r}"
        )
    return value


def minimum(gate: ConfidenceGate | str = ConfidenceGate.SEMANTIC) -> float:
    """Return the current validated minimum for one named decision class."""

    try:
        resolved = ConfidenceGate(gate)
    except ValueError as exc:
        raise ValueError(f"Unknown semantic confidence gate: {gate!r}") from exc
    if resolved is ConfidenceGate.SEMANTIC:
        # The environment may preserve a historical lower setting without
        # making it an acceptance bypass.  Ordinary semantic decisions never
        # auto-accept below 0.92; an override can only make that gate stricter.
        return max(
            SEMANTIC_AUTO_ACCEPT_FLOOR,
            _configured_threshold(
                env_name=SEMANTIC_ACCEPTANCE_ENV,
                default=DEFAULT_SEMANTIC_ACCEPTANCE,
                allowed=_SEMANTIC_RANGE,
            ),
        )
    if resolved is ConfidenceGate.DESTRUCTIVE:
        return _configured_threshold(
            env_name=DESTRUCTIVE_SEMANTIC_ENV,
            default=DEFAULT_DESTRUCTIVE_SEMANTIC,
            allowed=_DESTRUCTIVE_RANGE,
        )
    return SOURCE_CRITICAL_MINIMUM


def accepts(
    confidence: object,
    gate: ConfidenceGate | str = ConfidenceGate.SEMANTIC,
) -> bool:
    """Return whether a finite model score clears the named gate."""

    try:
        value = float(confidence)
    except (TypeError, ValueError):
        return False
    return math.isfinite(value) and value >= minimum(gate)


def semantic_band(confidence: object) -> str:
    """Classify an ordinary semantic score under the fixed decision bands.

    A configured threshold can raise the auto-accept boundary, but can never
    lower it.  The narrow 0.900--0.919 interval remains explicitly reserved
    for human review; scores below 0.900 are rejected, as are scores that fail
    an operator-configured stricter auto-accept threshold.
    """

    try:
        value = float(confidence)
    except (TypeError, ValueError):
        return "invalid"
    if not math.isfinite(value):
        return "invalid"
    if value >= minimum(ConfidenceGate.SEMANTIC):
        return "accepted"
    if (
        SEMANTIC_REVIEW_BAND_MINIMUM
        <= value
        < SEMANTIC_AUTO_ACCEPT_FLOOR
    ):
        return "human_review"
    return "rejected"


def threshold_text(
    gate: ConfidenceGate | str = ConfidenceGate.SEMANTIC,
    *,
    decimals: int = 3,
) -> str:
    """Render a gate consistently in prompts and operator diagnostics."""

    return f"{minimum(gate):.{decimals}f}"


def cache_identity() -> dict[str, object]:
    """Fingerprint policy-sensitive semantic caches.

    Effective environment overrides are deliberately included.  A cache
    approved under one gate must never be reused after an operator raises it;
    lowering the setting below the fixed 0.92 floor does not change the policy.
    """

    return {
        "version": POLICY_VERSION,
        ConfidenceGate.SEMANTIC.value: minimum(ConfidenceGate.SEMANTIC),
        ConfidenceGate.DESTRUCTIVE.value: minimum(ConfidenceGate.DESTRUCTIVE),
        ConfidenceGate.SOURCE_CRITICAL.value: minimum(
            ConfidenceGate.SOURCE_CRITICAL
        ),
    }

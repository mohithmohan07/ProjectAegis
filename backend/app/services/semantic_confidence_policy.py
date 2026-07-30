"""Central confidence policy for semantic generation decisions.

Semantic model scores are useful evidence, but they are not interchangeable
with source-integrity verification.  Aegis therefore keeps three explicitly
separate gates:

* ordinary semantic acceptance (hierarchy, grounding, topic and Type hosting);
* destructive semantic changes (retiring/removing a concept); and
* source-critical verification (PDF transcription, QID and Figure identity).

Only the ordinary semantic gate is intentionally relaxed.  Destructive and
source-critical decisions retain the historical 0.96 minimum.
"""
from __future__ import annotations

import math
import os
from enum import Enum
from typing import Final


POLICY_VERSION: Final = "semantic-confidence-policy-1"

SEMANTIC_ACCEPTANCE_ENV: Final = (
    "AEGIS_SEMANTIC_ACCEPTANCE_MIN_CONFIDENCE"
)
DESTRUCTIVE_SEMANTIC_ENV: Final = (
    "AEGIS_DESTRUCTIVE_SEMANTIC_MIN_CONFIDENCE"
)

DEFAULT_SEMANTIC_ACCEPTANCE: Final = 0.92
DEFAULT_DESTRUCTIVE_SEMANTIC: Final = 0.96
SOURCE_CRITICAL_MINIMUM: Final = 0.96

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
        return _configured_threshold(
            env_name=SEMANTIC_ACCEPTANCE_ENV,
            default=DEFAULT_SEMANTIC_ACCEPTANCE,
            allowed=_SEMANTIC_RANGE,
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


def threshold_text(
    gate: ConfidenceGate | str = ConfidenceGate.SEMANTIC,
    *,
    decimals: int = 3,
) -> str:
    """Render a gate consistently in prompts and operator diagnostics."""

    return f"{minimum(gate):.{decimals}f}"


def cache_identity() -> dict[str, object]:
    """Fingerprint policy-sensitive semantic caches.

    Environment overrides are deliberately included.  A cache approved under a
    lower threshold must never be reused after an operator raises the gate.
    """

    return {
        "version": POLICY_VERSION,
        ConfidenceGate.SEMANTIC.value: minimum(ConfidenceGate.SEMANTIC),
        ConfidenceGate.DESTRUCTIVE.value: minimum(ConfidenceGate.DESTRUCTIVE),
        ConfidenceGate.SOURCE_CRITICAL.value: minimum(
            ConfidenceGate.SOURCE_CRITICAL
        ),
    }

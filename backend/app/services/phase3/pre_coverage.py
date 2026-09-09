"""Versioned Pre-Learning coverage policy, frozen inside each run envelope.

The latest owner amendment restores adaptive coverage: the API authors the
context-sufficient total and tier split for each prerequisite, without padding
or a numeric quota. Fresh runs record that policy explicitly. Historical Q30
envelopes keep their five-Basic/five-Intermediate rule unchanged; envelopes
predating a carried rule keep their original no-rule behavior.

Historical Q30 policy:

Owner ruling, 7 Sep 2026: *"I would like 5 basic and 5 intermediate level
questions per concept of Pre Learning."* Every Pre-Learning concept
carries exactly five Basic and five Intermediate generated questions, and
no Advanced ones.

This amends Master Governing Contract v2.0 §8 ("No fixed count such as
five questions … is permitted"; register Q26) for the Pre lane, by the
contract's own author. It also reverses Q20 (21 Aug 2026: about five per
concept, split left to the model) and returns to the shape of the 20 Aug
2026 steer. Rule 0: the conflict is recorded, never blended — a run
executes under exactly one of the two postures, and which one is a
recorded fact of the run:

* the rule is a FROZEN RUN VARIABLE. ``stamp`` writes it into the Phase 3
  envelope's metadata where a production envelope is built
  (``concept_topology_contract._run_rewritten_phase3``), so it sits
  inside the envelope seal and inside every decision key. ``rule_for``
  reads it back. An envelope that records no rule — sealed before this
  ruling, or the golden fixtures — runs under Q26's no-quota posture, and
  ``prequestions.build`` logs which of the two is in force;
* the numbers live HERE and nowhere else. ``prequestions`` derives its
  prose and its checks from this object, so no checker in that module
  holds a literal count. What changed under Q30 is that the count is an
  owner's recorded rule the run is held to, rather than a norm the model
  is anchored on — the pins of Q4/Q26 against a hidden norm stand;
* the tier vocabulary is read from its one owner
  (``identity.GROUP_TIER_CODES``), never re-typed.

Nothing here decides content. Which capabilities the five Basic and five
Intermediate questions verify, and every question's text, remain the
model's; this module states counts the owner fixed.
"""
from __future__ import annotations

import copy
from typing import Any, Mapping

from .. import identity

# The envelope metadata key the rule rides under.
RULE_FIELD = "pre_coverage_rule"

LEGACY_OWNER_RULE: dict[str, Any] = {
    "version": "pre-coverage-owner-2026-09-07",
    "per_tier": {"Basic": 5, "Intermediate": 5},
}
ADAPTIVE_VERSION = "pre-coverage-adaptive-2026-09-09-v1"
OWNER_RULE: dict[str, Any] = {"version": ADAPTIVE_VERSION, "mode": "adaptive"}


class CoverageRuleError(ValueError):
    """A recorded coverage rule is malformed — a corrupt envelope."""


def owner_rule() -> dict[str, Any]:
    """The owner's rule of record, as a fresh copy."""

    return copy.deepcopy(OWNER_RULE)


def legacy_owner_rule() -> dict[str, Any]:
    """The historical Q30 rule; never the default for a fresh run."""
    return copy.deepcopy(LEGACY_OWNER_RULE)


def is_adaptive(rule: Mapping[str, Any] | None) -> bool:
    """Read the explicit policy discriminator, never infer it from a count."""
    return isinstance(rule, Mapping) and rule.get("version") == ADAPTIVE_VERSION and rule.get("mode") == "adaptive"


def validate(rule: object) -> dict[str, Any]:
    """The rule as a plain dict in tier order, or ``CoverageRuleError``."""

    if not isinstance(rule, Mapping):
        raise CoverageRuleError("coverage rule is not an object")
    version = str(rule.get("version") or "").strip()
    if not version:
        raise CoverageRuleError("coverage rule has no version")
    if version == ADAPTIVE_VERSION or rule.get("mode") == "adaptive":
        if not is_adaptive(rule) or set(rule) != {"version", "mode"}:
            raise CoverageRuleError(
                "adaptive coverage requires its recorded version and mode only; "
                "no fixed total, per-tier count or quota belongs in that policy"
            )
        return owner_rule()
    per_tier = rule.get("per_tier")
    if not isinstance(per_tier, Mapping) or not per_tier:
        raise CoverageRuleError("coverage rule names no tier")
    counts: dict[str, int] = {}
    for tier in identity.GROUP_TIER_CODES:
        if tier not in per_tier:
            continue
        count = per_tier[tier]
        if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
            raise CoverageRuleError(
                f"coverage rule count for {tier!r} is not a positive whole "
                "number"
            )
        counts[tier] = int(count)
    unknown = sorted(
        str(tier) for tier in per_tier if tier not in identity.GROUP_TIER_CODES
    )
    if unknown:
        raise CoverageRuleError(
            "coverage rule names unknown tier(s): " + ", ".join(unknown)
        )
    return {"version": version, "per_tier": counts}


def rule_for(env: Mapping[str, Any]) -> dict[str, Any] | None:
    """The rule the envelope records, or ``None`` when it records none."""

    metadata = env.get("metadata") if isinstance(env, Mapping) else None
    metadata = metadata if isinstance(metadata, Mapping) else {}
    recorded = metadata.get(RULE_FIELD)
    if recorded is None:
        return None
    return validate(recorded)


def stamp(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    """Record adaptive coverage at a fresh envelope mint.

    Sealed historical envelopes bypass this mint and retain their recorded
    rule. Copied/stale metadata does not reinstate the withdrawn fixed quota
    in a new run.
    """

    out = copy.deepcopy(dict(metadata or {}))
    out[RULE_FIELD] = owner_rule()
    return out


def total(rule: Mapping[str, Any]) -> int:
    if is_adaptive(rule):
        raise CoverageRuleError("adaptive coverage has no policy total; read the authored concept plan")
    return sum(int(count) for count in rule["per_tier"].values())


def tiers(rule: Mapping[str, Any]) -> tuple[str, ...]:
    if is_adaptive(rule):
        return tuple(identity.GROUP_TIER_CODES)
    return tuple(rule["per_tier"])


def describe(rule: Mapping[str, Any]) -> str:
    """``"5 Basic + 5 Intermediate (10 in all)"`` — for prose and messages."""

    if is_adaptive(rule):
        return "adaptive coverage: total and tier split authored from prerequisite mastery"
    parts = [f"{count} {tier}" for tier, count in rule["per_tier"].items()]
    return " + ".join(parts) + f" ({total(rule)} in all)"

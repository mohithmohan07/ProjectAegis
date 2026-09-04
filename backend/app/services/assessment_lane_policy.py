"""Master-lane cost policy (Master Governing Contract v2.0, register Q26).

The assessment lane makes one recorded author decision per stage and unit
(cell, materialization, answer restriction, marking, route, level, variant
cluster, group description).  Before Q26 every one of those decisions ALSO
paid for its own independent advisory critic, and two whole extra passes —
the Master Refiner and the touched-group QA — re-read every finished row.
A chapter's Master lane was therefore roughly twenty model calls per
question, and the Pre lane's generated questions rode the same pipeline.

Contract v2.0 §27 step 6 asks for ONE independent critic that jointly
verifies the question, answer space, model answer, criteria, accepted
equivalents and arithmetic.  This module is where that consolidation is
decided, in one place:

* the per-item author decisions keep their mechanical checkers and The
  Fixer, and their separate critics are OFF by default; the joint item
  review (``assessment_item_review``) audits the finished item once,
  after marking, with the whole item in view;
* the route keeps its critic (one unambiguous owning Concept is a
  semantic release gate, §43) and so do the chapter-level dedup and
  pre-learning claim verdicts (few calls, each removes a question);
* the Master Refiner and the touched-group QA are opt-in.

Nothing here changes who decides: every semantic verdict remains a model
verdict (CLAUDE.md Rule 1).  Only the number of second passes changes, and
the former behaviour is one environment variable away for A/B measurement.
"""
from __future__ import annotations

import os
from typing import Any, Callable

# Stages whose separate advisory critic stays on under the default policy.
DEFAULT_CRITIC_STAGES: frozenset[str] = frozenset({
    "route", "dedup", "pre_claim",
})
# Every stage that has a per-decision critic adapter at all.
ALL_CRITIC_STAGES: frozenset[str] = frozenset({
    "cells", "materialize", "answer_restriction", "marking", "route",
    "level", "cluster", "describe", "qa", "refiner", "dedup", "pre_claim",
})

CRITICS_ENV = "AEGIS_MASTER_CRITICS"
GROUP_QA_ENV = "AEGIS_MASTER_GROUP_QA"
REFINER_ENV = "AEGIS_MASTER_REFINER"
ITEM_REVIEW_ENV = "AEGIS_MASTER_ITEM_REVIEW"


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def critic_stages() -> frozenset[str]:
    """The stages whose separate critic runs.

    ``AEGIS_MASTER_CRITICS`` = ``all`` restores a critic on every decision
    (the pre-Q26 behaviour); ``none`` runs no per-decision critic; a
    comma-separated list names the stages explicitly.  Unset means the
    default set above.
    """
    raw = os.environ.get(CRITICS_ENV, "").strip().lower()
    if not raw:
        return DEFAULT_CRITIC_STAGES
    if raw == "all":
        return ALL_CRITIC_STAGES
    if raw == "none":
        return frozenset()
    stages = frozenset(
        token.strip() for token in raw.split(",") if token.strip()
    )
    unknown = sorted(stages - ALL_CRITIC_STAGES)
    if unknown:
        # An operator who typed a name expects that name, not a quiet
        # substitute (contract §2): refuse, never default.
        raise ValueError(
            f"{CRITICS_ENV} names unknown critic stage(s) "
            f"{', '.join(unknown)}; expected any of: "
            f"{', '.join(sorted(ALL_CRITIC_STAGES))}, all, or none"
        )
    return stages


def critic_for(
    stage: str, live_critic: Callable[[dict[str, Any]], Any] | None,
) -> Callable[[dict[str, Any]], Any] | None:
    """The live critic for ``stage`` under the policy, or ``None``.

    Called only when no critic was injected: an injected critic (a test
    seam, or a caller that deliberately wants one) is never removed.
    """
    return live_critic if stage in critic_stages() else None


def item_review_enabled() -> bool:
    """Whether the joint per-item review runs (default on)."""
    raw = os.environ.get(ITEM_REVIEW_ENV, "").strip()
    return True if not raw else _truthy(raw)


def group_qa_enabled() -> bool:
    """Whether the touched-group QA pass runs (default off, opt-in)."""
    return _truthy(os.environ.get(GROUP_QA_ENV, ""))


def master_refiner_enabled() -> bool:
    """Whether the Master Refiner prose pass runs (default off, opt-in)."""
    return _truthy(os.environ.get(REFINER_ENV, ""))


def describe() -> dict[str, Any]:
    """The effective lane policy, for run manifests and diagnostics."""
    return {
        "critic_stages": sorted(critic_stages()),
        "item_review": item_review_enabled(),
        "group_qa": group_qa_enabled(),
        "master_refiner": master_refiner_enabled(),
    }

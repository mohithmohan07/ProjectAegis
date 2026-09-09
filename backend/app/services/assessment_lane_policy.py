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
equivalents and arithmetic.  Q26 made that joint review the only Master
critic by default and switched the per-decision critics, the Master
Refiner and the touched-group QA off.

Register Q31 (owner ruling, 7 Sep 2026) reverses those defaults: the owner
measured the outputs written under the Q26 cuts against the earlier ones
and ruled that the writing quality comes first.  So, by default:

* every per-item author decision keeps its own independent advisory
  critic beside the joint item review — the critic's named dissent is what
  the Refiners read when they repair a row;
* the Master Refiner and the touched-group QA run on every Master;
* the joint item review stays on.

Each pass is still one environment variable away for the cost profile
(``AEGIS_MASTER_CRITICS=none``, ``AEGIS_MASTER_REFINER=0``,
``AEGIS_MASTER_GROUP_QA=0``), so the two policies can be measured against
each other on the same source.  Nothing here changes who decides: every
semantic verdict remains a model verdict (CLAUDE.md Rule 1).  Only the
number of second passes changes.
"""
from __future__ import annotations

import os
from typing import Any, Callable

# Every stage that has a per-decision critic adapter at all.
ALL_CRITIC_STAGES: frozenset[str] = frozenset({
    "cells", "materialize", "answer_restriction", "marking", "route",
    "level", "cluster", "describe", "qa", "refiner", "dedup", "pre_claim",
})
# Stages whose separate advisory critic runs under the default policy:
# all of them (register Q31). The Q26 cost set — route, dedup and the
# pre-learning claim only — is one variable away (``AEGIS_MASTER_CRITICS=
# route,dedup,pre_claim``).
DEFAULT_CRITIC_STAGES: frozenset[str] = ALL_CRITIC_STAGES
COST_CRITIC_STAGES: frozenset[str] = frozenset({
    "route", "dedup", "pre_claim",
})

CRITICS_ENV = "AEGIS_MASTER_CRITICS"
GROUP_QA_ENV = "AEGIS_MASTER_GROUP_QA"
REFINER_ENV = "AEGIS_MASTER_REFINER"
ITEM_REVIEW_ENV = "AEGIS_MASTER_ITEM_REVIEW"


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def critic_stages() -> frozenset[str]:
    """The stages whose separate critic runs.

    ``AEGIS_MASTER_CRITICS`` = ``all`` (the default since register Q31)
    runs a critic on every decision; ``none`` runs no per-decision critic;
    a comma-separated list names the stages explicitly (the Q26 cost set
    is ``route,dedup,pre_claim``).  Unset means the default set above.
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
    """Whether the touched-group QA pass runs (default on since Q31)."""
    raw = os.environ.get(GROUP_QA_ENV, "").strip()
    return True if not raw else _truthy(raw)


def master_refiner_enabled() -> bool:
    """Whether the Master Refiner prose pass runs (default on since Q31)."""
    raw = os.environ.get(REFINER_ENV, "").strip()
    return True if not raw else _truthy(raw)


def describe() -> dict[str, Any]:
    """The effective lane policy, for run manifests and diagnostics."""
    return {
        "critic_stages": sorted(critic_stages()),
        "item_review": item_review_enabled(),
        "group_qa": group_qa_enabled(),
        "master_refiner": master_refiner_enabled(),
    }

"""The Fixer — unblocks the run (docs/aegis-restructure.md §8.2, Q13).

Wherever the run hits a mid-run block — a failed gate, a structural
defect, a semantic non-decision — The Fixer is invoked with the full
context of the block (the failing check, the code path's contract, the
active prompts, the produced output, the source evidence), takes the
most suitable decision ONCE, records it as review flags on the affected
rows, and the run completes. All Fixer decisions run THROUGH
``kernel.decide`` (bounded attempts, content-addressed decide-once
store, advisory critic) — this module only provides the live provider
and the policy version; it never reimplements the kernel.

What still halts (by design, this round):

* the three pre-spend source-integrity pauses (source review,
  source-topic recovery, Type granularity) — map §1;
* genuine impossibility (source unreadable, provider down, quota
  exhausted, certificate/data corruption) — map §2;
* protocol impossibility: the Fixer itself cannot produce a
  mechanically applicable decision after bounded attempts;
* certificate-integrity mechanics — F12-F16 and their generation.py
  mirrors (``type_case_owner_uncertified`` family), F25/F26 (row-seal /
  grounding-certificate loss), F41/F42 (deposit certificate divergence):
  a corrupt certificate is data corruption, not a judgment call;
* F17 (``canonical_source_phase3.py:6728`` — source graph unsafe for
  generation): source integrity, only a human can replace the source;
* F43-F45 (bulk-import writer / workbook publication seams) — deferred
  to the Refiner step of the restructure; not mid-run generation blocks;
* remaining bounded-retry seams left as-is this round (rarely terminal,
  behind their own retries): F27 (learner-analysis unusable), F28
  (assignment-unit QID invariant), F29 (consolidation returned no
  rows), F30 (method-row snapshot anchors), F31 (type-consolidation
  guards, unreachable by design), F32 (missing-topic recovery
  verdicts), F33 (anchor recovery/canonicalization/skeleton
  reconciliation), F34 (run-level empty-input guards);
* ledger-safety mechanics (F49/F50) — they bound the ceiling-retirement
  loop and must stay.
"""
from __future__ import annotations

from typing import Any

# Stamped as the decision-store policy version on every Fixer decision so
# a future charter change mints new keys instead of replaying old verdicts.
FIXER_POLICY_VERSION = "fixer-1"


def live_fixer(payload: dict[str, Any]) -> dict[str, Any]:
    """The production Fixer provider: one JSON decision per blocked point."""

    from . import prompts
    from .. import generation

    return generation._openai_json(
        prompts.FIXER_SYSTEM,
        prompts.render(payload),
        purpose="concept_validation",
    )


def default_provider():
    """The Fixer for this deployment: live when the semantic API is live.

    Dry/test runs get ``None`` — every seam then keeps its fail-closed
    behavior unless a test injects a fake provider explicitly.
    """

    from .. import canonical_source_phase3 as phase3_core

    return live_fixer if phase3_core.semantic_api_enabled() else None

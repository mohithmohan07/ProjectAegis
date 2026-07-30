"""Phase 3.7.1 compatibility guards for visual topology convergence.

Phase 3.7 enriches source packets with labelled visual evidence. This small
follow-up preserves the legacy exact plain-text payload shape for non-visual
blocks, prevents a retirement target from depending on an undecided concept in
another batch, and clears any pending retirement audit before each convergence
pass.
"""
from __future__ import annotations

from functools import wraps
from typing import Any

from . import canonical_source_phase32_topology_adjudication_contract as phase32
from . import canonical_source_phase37_visual_topology_convergence_contract as phase37

_CONTRACT_VERSION = 1
_SOURCE_TEXT_PREFIX = "[Source text] "


def install() -> None:
    if getattr(phase37, "_PHASE371_COMPAT_VERSION", 0) >= _CONTRACT_VERSION:
        return

    original_evidence_text = phase37._evidence_text
    original_parse_decisions = phase32._parse_decisions
    original_apply_decisions = phase32._apply_decisions

    phase37._PHASE371_ORIGINAL_EVIDENCE_TEXT = original_evidence_text
    phase37._PHASE371_ORIGINAL_PARSE_DECISIONS = original_parse_decisions
    phase37._PHASE371_ORIGINAL_APPLY_DECISIONS = original_apply_decisions

    @wraps(original_evidence_text)
    def evidence_text(*args: Any, **kwargs: Any) -> str:
        value = str(original_evidence_text(*args, **kwargs) or "")
        # Existing plain-text evidence contracts compare source blocks exactly.
        # Remove only the first explanatory label; visual caption/alt labels on
        # subsequent lines remain explicit and auditable.
        if value.startswith(_SOURCE_TEXT_PREFIX):
            return value[len(_SOURCE_TEXT_PREFIX) :]
        return value

    @wraps(original_parse_decisions)
    def parse_decisions(
        response: dict[str, Any],
        *,
        concepts: dict[str, dict[str, Any]],
        topic_ids: set[str],
    ) -> tuple[dict[str, dict[str, Any]], list[str]]:
        parsed, errors = original_parse_decisions(
            response,
            concepts=concepts,
            topic_ids=topic_ids,
        )
        rows = [
            row
            for row in (response or {}).get("concepts") or []
            if isinstance(row, dict)
        ]
        decided_here = {
            str(row.get("concept_id") or ""): str(row.get("decision") or "")
            for row in rows
            if str(row.get("concept_id") or "")
        }
        for concept_id, decision in list(parsed.items()):
            if decision.get("decision") != "retire":
                continue
            target = str(decision.get("retire_into_concept_id") or "NONE")
            if target == "NONE":
                continue
            # A target in another unresolved batch might later be moved, split,
            # or retired. Require the survivor to be explicitly decided in this
            # same independently reviewed response.
            if target not in decided_here:
                errors.append(
                    f"{concept_id} retirement target {target} was not decided in "
                    "the same topology batch"
                )
                parsed.pop(concept_id, None)
                continue
            if decided_here[target] == "retire":
                errors.append(
                    f"{concept_id} cannot retire into another retiring concept "
                    f"{target}"
                )
                parsed.pop(concept_id, None)
        return parsed, list(dict.fromkeys(errors))

    @wraps(original_apply_decisions)
    def apply_decisions(*args: Any, **kwargs: Any):
        # Phase 3.3 may rerun the entire topology after exact-grounding feedback.
        # A later pass with no retirement must not inherit the prior pass's
        # provisional audit record.
        phase37._PENDING_RETIREMENT_AUDIT.set(None)
        return original_apply_decisions(*args, **kwargs)

    phase37._evidence_text = evidence_text
    phase37._parse_decisions = parse_decisions
    phase37._apply_decisions = apply_decisions
    phase32._parse_decisions = parse_decisions
    phase32._apply_decisions = apply_decisions
    phase37._PHASE371_COMPAT_VERSION = _CONTRACT_VERSION

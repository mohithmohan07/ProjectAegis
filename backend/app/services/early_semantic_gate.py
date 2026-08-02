"""Shared identity and resume helpers for early Phase 3 semantic pauses.

The public decision ledger deliberately exposes a small, stable schema.  These
helpers bind each selectable action to the complete source context without
adding another persistence format: the decision context and every generic
``target_id`` are SHA-256 identities over the source contract, original PDF,
semantic context, affected unit, evidence, and bounded candidate action.
"""
from __future__ import annotations

import copy
import json
import re
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator, Mapping

from . import canonical_source_phase3 as phase3
from . import semantic_confidence_policy as confidence_policy


REVIEW_BAND_MIN = confidence_policy.SEMANTIC_REVIEW_BAND_MINIMUM
AUTO_ACCEPT_MIN = confidence_policy.SEMANTIC_AUTO_ACCEPT_FLOOR
_SUPPRESSED_RESOLUTION_IDS: ContextVar[frozenset[str]] = ContextVar(
    "aegis_suppressed_early_resolution_ids", default=frozenset()
)

_CANDIDATE_BINDING_FIELDS = (
    "action",
    "source_block_ids",
    "source_topic_id",
    "target_topic_id",
    "boundary_relation",
    "source_kind",
    "source_page",
    "text_sha256",
)
_BLOCK_ID_RE = re.compile(r"\bBLK-[A-Za-z0-9_-]+\b")


class TopologyRepairRequired(ValueError):
    """Signal a human-directed grounding conflict back to topology."""

    def __init__(self, message: str, *, decision_id: str) -> None:
        super().__init__(message)
        self.decision_id = decision_id


@contextmanager
def suppress_resolution_ids(decision_ids: set[str]) -> Iterator[None]:
    token = _SUPPRESSED_RESOLUTION_IDS.set(frozenset(decision_ids))
    try:
        yield
    finally:
        _SUPPRESSED_RESOLUTION_IDS.reset(token)


def plain_json(value: Any) -> Any:
    return json.loads(
        json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    )


def _normal(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def source_identity(graph: Mapping[str, Any]) -> dict[str, str]:
    vision = graph.get("vision_evidence")
    if not isinstance(vision, Mapping):
        vision = {}
    return {
        "source_contract_hash": str(graph.get("source_contract_hash") or ""),
        "semantic_context_hash": str(
            graph.get("semantic_context_hash") or ""
        ),
        "semantic_source_sha256": str(
            graph.get("semantic_source_sha256") or ""
        ),
        "metadata_sha256": phase3._sha256_json(
            plain_json(graph.get("metadata") or {})
        ),
        "pdf_sha256": str(
            vision.get("pdf_sha256")
            or graph.get("pdf_sha256")
            or ""
        ),
    }


def candidate_binding_material(
    candidate: Mapping[str, Any],
) -> dict[str, Any]:
    """Return the stable semantic fields sealed into a candidate binding.

    A block ID is intentionally not meaningful on its own.  Evidence actions
    bind that ID to the exact provider-visible text digest and source boundary;
    topology actions bind their action and source/target topic relation.  The
    public candidate can therefore be compacted without asking a model to infer
    an opaque ``BLK`` identity from nearby prose.
    """

    material = {
        key: plain_json(candidate.get(key))
        for key in _CANDIDATE_BINDING_FIELDS
    }
    material["source_block_ids"] = [
        str(value)
        for value in material.get("source_block_ids") or []
        if str(value)
    ]
    return {
        "version": "early-human-semantic-binding-1",
        **material,
    }


def bind_candidate(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Copy a candidate and attach its deterministic exact-source seal."""

    row = plain_json(candidate)
    row.pop("binding_hash", None)
    row["binding_hash"] = phase3._sha256_json(
        candidate_binding_material(row)
    )
    return row


def candidate_binding_is_valid(candidate: Mapping[str, Any]) -> bool:
    """Validate a supplied binding while retaining legacy candidate support."""

    binding_hash = str(candidate.get("binding_hash") or "")
    if not binding_hash:
        return True
    binding_valid = bool(
        re.fullmatch(r"[0-9a-f]{64}", binding_hash)
        and binding_hash
        == phase3._sha256_json(candidate_binding_material(candidate))
    )
    if not binding_valid:
        return False
    if str(candidate.get("action") or "") != "use_verified_evidence":
        return True
    block_ids = [
        str(value)
        for value in candidate.get("source_block_ids") or []
        if str(value)
    ]
    text_sha256 = str(candidate.get("text_sha256") or "")
    coverage = str(candidate.get("coverage") or "")
    return bool(
        block_ids
        and str(candidate.get("source_topic_id") or "")
        and str(candidate.get("boundary_relation") or "")
        and re.fullmatch(r"[0-9a-f]{64}", text_sha256)
        and (
            not coverage
            or phase3._sha256_text(coverage) == text_sha256
        )
    )


def _candidate_action(candidate: Mapping[str, Any]) -> str:
    action = str(candidate.get("action") or "").strip().casefold()
    if action:
        return action
    title = _normal(candidate.get("title"))
    if title.startswith("use verified evidence"):
        return "use_verified_evidence"
    for value in ("refine", "split", "retire", "move", "keep"):
        if title.startswith(value):
            return value
    return ""


def _candidate_block_ids(candidate: Mapping[str, Any]) -> list[str]:
    raw = candidate.get("source_block_ids") or []
    if isinstance(raw, str) or not isinstance(raw, (list, tuple, set)):
        raw = [raw]
    explicit = [str(value) for value in raw if str(value)]
    if explicit:
        return list(dict.fromkeys(explicit))
    return list(dict.fromkeys(
        _BLOCK_ID_RE.findall(str(candidate.get("title") or ""))
    ))


def _legacy_candidate_matches_current(
    saved: Mapping[str, Any],
    current: Mapping[str, Any],
) -> bool:
    """Match one v1 candidate to v2 without trusting its regenerated ID."""

    action = _candidate_action(saved)
    if not action or action != _candidate_action(current):
        return False
    if action == "use_verified_evidence":
        saved_blocks = _candidate_block_ids(saved)
        current_blocks = _candidate_block_ids(current)
        if not saved_blocks or saved_blocks != current_blocks:
            return False
        saved_text = str(saved.get("coverage") or "")
        current_text = str(current.get("coverage") or "")
        current_text_hash = str(current.get("text_sha256") or "")
        return bool(
            saved_text
            and (
                phase3._sha256_text(saved_text) == current_text_hash
                or saved_text == current_text
            )
        )
    return all(
        _normal(saved.get(field)) == _normal(current.get(field))
        for field in ("title", "topic", "coverage", "gap")
    )


def _agent_upgrade_candidate(
    resolution: Mapping[str, Any],
    candidates: list[Mapping[str, Any]],
) -> dict[str, Any] | None:
    """Map a saved target only after a complete-workspace resolver sealed it."""

    review = resolution.get("agent_review")
    if not (
        str(resolution.get("resolved_by") or "") == "agent"
        and isinstance(review, Mapping)
        and str(review.get("status") or "") == "resolved"
        and str(review.get("resolver_version") or "") in {
            "semantic-resolution-agent-2",
            "semantic-resolution-agent-3",
        }
        and re.fullmatch(
            r"[0-9a-f]{64}", str(review.get("capability_key") or "")
        )
    ):
        return None
    saved_target_id = str(resolution.get("target_id") or "")
    saved_candidates = [
        row
        for row in resolution.get("candidates") or []
        if isinstance(row, Mapping)
        and str(row.get("target_id") or "") == saved_target_id
    ]
    if len(saved_candidates) != 1:
        return None
    matches = [
        plain_json(row)
        for row in candidates
        if candidate_binding_is_valid(row)
        and _legacy_candidate_matches_current(saved_candidates[0], row)
    ]
    return matches[0] if len(matches) == 1 else None


def candidate_target_id(
    *,
    phase: str,
    action: str,
    graph: Mapping[str, Any],
    unit: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> str:
    digest = phase3._sha256_json(
        {
            "version": "early-human-semantic-candidate-2",
            "phase": phase,
            "action": action,
            "source": source_identity(graph),
            "unit": plain_json(unit),
            "candidate": plain_json(candidate),
        }
    )
    return f"{phase}:{action}:{digest}"


def context_identity(
    *,
    phase: str,
    kind: str,
    graph: Mapping[str, Any],
    unit: Mapping[str, Any],
    evidence_fingerprint: Any,
    candidates: list[Mapping[str, Any]],
) -> dict[str, str]:
    context_hash = phase3._sha256_json(
        {
            "version": "early-human-semantic-context-1",
            "phase": phase,
            "kind": kind,
            "source": source_identity(graph),
            "unit": plain_json(unit),
            "evidence": plain_json(evidence_fingerprint),
            "candidate_hashes": [
                phase3._sha256_json(plain_json(row)) for row in candidates
            ],
        }
    )
    prefix = "phase31-ground" if phase == "3.1" else "phase32-blueprint"
    return {
        "context_hash": context_hash,
        "decision_id": f"{prefix}-{context_hash[:24]}",
    }


def followup_identity(
    identity: Mapping[str, str],
    *,
    resolution: Mapping[str, Any] | None,
    issues: list[str],
    proposal: Any,
) -> dict[str, str]:
    """Give a rejected human-directed attempt a fresh durable identity."""

    if not resolution:
        return dict(identity)
    context_hash = phase3._sha256_json(
        {
            "version": "early-human-semantic-followup-1",
            "base_context_hash": str(identity.get("context_hash") or ""),
            "resolution": {
                "decision_id": str(resolution.get("decision_id") or ""),
                "context_hash": str(resolution.get("context_hash") or ""),
                "choice": str(resolution.get("choice") or ""),
                "target_id": str(resolution.get("target_id") or ""),
                "instruction_sha256": phase3._sha256_text(
                    resolution.get("instruction") or ""
                ),
            },
            "issues": [str(value) for value in issues],
            "proposal_sha256": phase3._sha256_json(plain_json(proposal)),
        }
    )
    prefix = (
        "phase31-ground"
        if str(identity.get("decision_id") or "").startswith("phase31-")
        else "phase32-blueprint"
    )
    return {
        # The context hash remains the exact, reproducible source/evidence/
        # candidate identity.  A rejected directed attempt gets a fresh
        # decision ID, but does not weaken replay validation by replacing the
        # reproducible context with a model-output-dependent hash.
        "context_hash": str(identity.get("context_hash") or ""),
        "decision_id": f"{prefix}-{context_hash[:24]}",
    }


def _resolution_rows() -> list[dict[str, Any]]:
    # Local import avoids a module cycle: Phase 3.3 imports both Phase 3.1 and
    # Phase 3.2, while its ContextVar is the existing orchestration boundary.
    from . import canonical_source_phase33_preflight_contract as phase33

    return phase33._resolution_candidates(
        phase33._HUMAN_DECISION_RESOLUTIONS.get()
    )


def resolution_for(
    *,
    identity: Mapping[str, str],
    kind: str,
    phase: str,
    unit_id: str,
    candidates: list[Mapping[str, Any]],
) -> dict[str, Any] | None:
    """Return a source-bound saved direction for one early semantic unit."""

    by_target = {
        str(row.get("target_id") or ""): plain_json(row)
        for row in candidates
        if str(row.get("target_id") or "")
        and candidate_binding_is_valid(row)
    }
    expected_context = str(identity.get("context_hash") or "")
    expected_decision = str(identity.get("decision_id") or "")
    decision_prefix = (
        "phase31-ground-" if phase == "3.1" else "phase32-blueprint-"
    )
    for raw in reversed(_resolution_rows()):
        status = str(raw.get("status") or "").strip().casefold()
        if status in {"pending", "awaiting_decision", "cancelled", "rejected"}:
            continue
        if str(raw.get("kind") or "") != kind:
            continue
        if str(raw.get("phase") or "") != phase:
            continue
        item = raw.get("item")
        saved_unit_id = (
            str(item.get("unit_id") or "")
            if isinstance(item, Mapping)
            else ""
        )
        if saved_unit_id != unit_id:
            continue
        choice = str(raw.get("choice") or "").strip().casefold()
        if choice == "custom":
            choice = "custom_instruction"
        if choice not in {
            "accept_recommended",
            "select_candidate",
            "replace_source",
            "custom_instruction",
        }:
            continue
        target_id = str(raw.get("target_id") or "")
        exact_context = (
            str(raw.get("context_hash") or "")
            == expected_context
        )
        upgraded_candidate = None
        if (
            not exact_context
            and choice in {"accept_recommended", "select_candidate"}
        ):
            upgraded_candidate = _agent_upgrade_candidate(
                raw,
                candidates,
            )
            if upgraded_candidate is not None:
                target_id = str(upgraded_candidate.get("target_id") or "")
        saved_decision = str(raw.get("decision_id") or "")
        if saved_decision in _SUPPRESSED_RESOLUTION_IDS.get():
            continue
        exact_decision_family = (
            saved_decision == expected_decision
            or saved_decision.startswith(decision_prefix)
        )
        # Every choice, including replace_source, is bound to the exact
        # current source/evidence/candidate context.  A follow-up pause retains
        # that context while receiving a fresh decision ID in the same family.
        if (
            (not exact_context and upgraded_candidate is None)
            or not exact_decision_family
        ):
            continue
        if choice in {"accept_recommended", "select_candidate"}:
            if target_id not in by_target:
                continue
        elif choice == "custom_instruction":
            # Free text has no candidate hash. It is accepted only for the exact
            # first context; a rejected directed attempt re-pauses with bounded
            # candidates and cannot enter a free-text loop.
            if not str(raw.get("instruction") or "").strip():
                continue
        elif choice == "replace_source":
            # The persisted ledger fingerprint and exact stage/unit identity
            # already bind this safe terminal direction to the current upload.
            pass
        return plain_json(
            {
                "decision_id": str(raw.get("decision_id") or ""),
                "context_hash": str(raw.get("context_hash") or ""),
                "choice": choice,
                "target_id": target_id,
                "instruction": str(raw.get("instruction") or ""),
                "selected_candidate": copy.deepcopy(by_target.get(target_id)),
                "critic_feedback": {
                    "conflict": str(raw.get("conflict") or ""),
                    "diagnosis": str(raw.get("diagnosis") or ""),
                },
                "deferred_assignment_unit_ids": [
                    str(value)
                    for value in raw.get("deferred_assignment_unit_ids") or []
                    if str(value)
                ],
            }
        )
    return None


def confidence_band(confidence: object) -> str:
    return confidence_policy.semantic_band(confidence)


def block_ids_from_issues(issues: list[str]) -> set[str]:
    return {
        match.group(0)
        for issue in issues
        for match in re.finditer(r"\bBLK-[A-Za-z0-9_-]+\b", str(issue))
    }

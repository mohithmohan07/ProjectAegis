"""Phase 3.3 convergence preflight for topology, grounding, and Type hosts.

Phase 3.2 moves source verification ahead of learner analysis and topology freeze.
The remaining live-risk surface is narrower but important:

* a model can paraphrase a ``keep`` or ``move`` proposal even though those
  decisions should preserve the source-facing concept claim;
* independently verified Phase 3.2 decisions were cached only after the whole
  chapter passed exact grounding, so one late defect could replay earlier calls;
* a populated topic can still contain no semantically valid host for one mined
  Type/Case assignment unit.  The old missing-host repair ran only when a topic
  had zero normal concepts, and therefore could still force an unrelated unit
  into the nearest row after topology freeze.

This contract adds three fail-closed convergence layers without weakening source
identity or exact-inventory gates:

1. production ``keep``/``move`` proposals are deterministically projected back
   onto the original concept claim before the independent critic sees them;
2. independently verified Phase 3.2 decisions are cached per concept and exact
   grounding failures can trigger one bounded source-informed re-adjudication;
3. every normal mined assignment unit is independently certified to an existing
   source-grounded concept, or a genuinely missing durable concept is created,
   grounded, given learner analysis, and inserted *before* topology freeze.

Low confidence by itself never authorises a new concept.
"""
from __future__ import annotations

import copy
import json
import os
import re
import time
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from .. import config
from . import canonical_source_phase3 as phase3
from . import canonical_source_phase31_grounding_contract as phase31
from . import canonical_source_phase32_topology_adjudication_contract as phase32
from . import concept_refiner as cr
from . import concept_topology_contract as topology
from . import early_semantic_gate as early_gate
from . import grounding_certificate
from . import placement_policy
from . import progress
from . import semantic_confidence_policy as confidence_policy
from .semantic_recovery import (
    HumanDecisionRequired,
    ProviderResponseContractError,
)

_CONTRACT_VERSION = 2
_DECISION_CACHE_VERSION = "phase3.3-certified-placement-decision-cache-3"
_DECISION_CACHE_FILENAME = "source.phase33-topology-decision-cache.json"
_HOST_VERSION = "phase3.3-type-host-preflight-6-certified-mutation-v1"
_HOST_MUTATION_PLACEMENT_VERSION = 1
_HOST_MUTATION_ENVELOPE_KEY = "_host_mutation_placement_envelope"
_HOST_MUTATION_SOURCE_PLAN_KEY = "_phase33_certified_host_mutation_plan"
_TYPE_CASE_OWNERSHIP_VERSION = 2
_TYPE_CASE_QID_PLACEMENT_LEDGER_KEY = "_type_case_qid_placement_ledger"
_TYPE_CASE_PLACEMENT_CACHE_FILENAME = (
    "source.phase33-type-case-placement-cache.json"
)
_HOST_PLAN_CACHE_FILENAME = "source.phase33-type-host-plan-cache.json"
_HOST_RESULT_CACHE_FILENAME = "source.phase33-type-host-result-cache.json"

_EXTERNAL_GROUNDING_FEEDBACK: ContextVar[dict[str, str]] = ContextVar(
    "aegis_phase33_external_grounding_feedback", default={}
)
_HUMAN_DECISION_RESOLUTIONS: ContextVar[Any] = ContextVar(
    "aegis_phase33_human_decision_resolutions", default=None
)

TopologyProvider = Callable[[dict[str, Any]], dict[str, Any]]
TopologyCritic = Callable[[dict[str, Any]], dict[str, Any]]
HostProvider = Callable[[dict[str, Any]], dict[str, Any]]
HostCritic = Callable[[dict[str, Any]], dict[str, Any]]
PlacementProvider = Callable[[dict[str, Any]], dict[str, Any]]
PlacementCritic = Callable[[dict[str, Any]], dict[str, Any]]


@contextmanager
def human_resolution_context(resolutions: Any) -> Iterator[None]:
    """Expose persisted human choices to one generation attempt.

    Accepted inputs are a sequence of resolution objects, a mapping keyed by
    ``decision_id``/``context_hash``, or one resolution object. The surrounding
    orchestration layer remains responsible for persisting and marking a
    resolution consumed after a successful generation checkpoint.
    """

    token = _HUMAN_DECISION_RESOLUTIONS.set(copy.deepcopy(resolutions))
    try:
        yield
    finally:
        _HUMAN_DECISION_RESOLUTIONS.reset(token)


def _normal(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def _plain_json(value: Any) -> Any:
    """Return deterministic persistence-safe JSON data."""

    return json.loads(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
    )


def _host_human_context_identity(
    *,
    graph: dict[str, Any],
    topic_id: str,
    units: list[dict[str, Any]],
    concepts: list[dict[str, Any]],
    source_blocks: list[dict[str, Any]],
) -> dict[str, str]:
    context_hash = phase3._sha256_json(
        {
            "version": "phase3.3-human-type-host-decision-1",
            "source_contract_hash": str(graph.get("source_contract_hash") or ""),
            "topic_id": topic_id,
            "assignment_units": phase31._json_safe(units),
            "existing_concepts": phase31._json_safe(concepts),
            "source_blocks": [
                {
                    "block_id": str(row.get("block_id") or ""),
                    "text_sha256": phase3._sha256_text(row.get("text") or ""),
                }
                for row in source_blocks
            ],
        }
    )
    return {
        "context_hash": context_hash,
        "decision_id": f"phase33-host-{context_hash[:24]}",
    }


def _resolution_candidates(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, Mapping):
        row = dict(value)
        nested = row.get("resolution")
        if isinstance(nested, Mapping):
            row = {**row, **dict(nested)}
        if str(row.get("choice") or "").strip():
            return [row]
        out: list[dict[str, Any]] = []
        for key, raw in value.items():
            if isinstance(raw, str):
                out.append({"_lookup_key": str(key), "choice": raw})
            elif isinstance(raw, Mapping):
                candidate = dict(raw)
                candidate.setdefault("_lookup_key", str(key))
                nested = candidate.get("resolution")
                if isinstance(nested, Mapping):
                    candidate = {**candidate, **dict(nested)}
                out.append(candidate)
        return out
    if isinstance(value, (list, tuple)):
        out: list[dict[str, Any]] = []
        for raw in value:
            out.extend(_resolution_candidates(raw))
        return out
    return []


def _human_resolutions_for(
    identity: Mapping[str, str],
) -> list[dict[str, Any]]:
    """Return the newest saved direction for each affected assignment unit."""

    decision_id = str(identity.get("decision_id") or "")
    context_hash = str(identity.get("context_hash") or "")
    resolved: list[dict[str, Any]] = []
    key_indexes: dict[str, int] = {}
    for candidate in _resolution_candidates(
        _HUMAN_DECISION_RESOLUTIONS.get()
    ):
        status = _normal(candidate.get("status"))
        if status in {"pending", "awaiting_decision", "cancelled", "rejected"}:
            continue
        identifiers = {
            str(candidate.get("decision_id") or ""),
            str(candidate.get("context_hash") or ""),
            str(candidate.get("_lookup_key") or ""),
        }
        if decision_id not in identifiers and context_hash not in identifiers:
            continue
        choice = _normal(candidate.get("choice")).replace(" ", "_")
        aliases = {
            "custom_instruction": "custom",
        }
        choice = aliases.get(choice, choice)
        if choice not in {
            "expand_existing",
            "create_new",
            "select_existing",
            "custom",
        }:
            continue
        item = candidate.get("item")
        unit_id = (
            str(item.get("unit_id") or "")
            if isinstance(item, Mapping)
            else ""
        )
        assignment_unit_ids = [
            str(value)
            for value in candidate.get("assignment_unit_ids") or []
            if str(value)
        ]
        explicit_unit_id = str(
            candidate.get("assignment_unit_id")
            or candidate.get("unit_id")
            or unit_id
        )
        if explicit_unit_id and explicit_unit_id not in assignment_unit_ids:
            assignment_unit_ids.append(explicit_unit_id)
        row = {
            "decision_id": str(candidate.get("decision_id") or decision_id),
            "context_hash": context_hash,
            "choice": choice,
            "instruction": str(
                candidate.get("instruction")
                or candidate.get("custom_instruction")
                or ""
            ),
            "target_concept_id": str(
                candidate.get("target_concept_id")
                or candidate.get("existing_concept_id")
                or ""
            ),
            "assignment_unit_id": str(
                explicit_unit_id
            ),
            "assignment_unit_ids": assignment_unit_ids,
            "deferred_assignment_unit_ids": [
                str(value)
                for value in (
                    candidate.get("deferred_assignment_unit_ids") or []
                )
                if str(value)
            ],
        }
        # A later answer for the same unit supersedes an earlier rejected
        # direction. Choices without a unit remain independently addressable.
        resolution_key = (
            f"unit:{assignment_unit_ids[0]}"
            if len(assignment_unit_ids) == 1
            else f"decision:{row['decision_id']}"
        )
        if resolution_key in key_indexes:
            resolved[key_indexes[resolution_key]] = row
        else:
            key_indexes[resolution_key] = len(resolved)
            resolved.append(row)
    return _plain_json(resolved)


def _human_resolution_for(identity: Mapping[str, str]) -> dict[str, Any] | None:
    """Compatibility helper returning the latest effective human direction."""

    resolutions = _human_resolutions_for(identity)
    return copy.deepcopy(resolutions[-1]) if resolutions else None


def _resolution_unit_ids(resolution: Mapping[str, Any]) -> set[str]:
    unit_ids = {
        str(value)
        for value in resolution.get("assignment_unit_ids") or []
        if str(value)
    }
    explicit = str(
        resolution.get("assignment_unit_id")
        or resolution.get("unit_id")
        or ""
    )
    if explicit:
        unit_ids.add(explicit)
    return unit_ids


def _plan_source_block_ids_for_unit(
    plan: Mapping[str, Any],
    *,
    unit_id: str,
    concepts: list[dict[str, Any]],
    target_concept_id: str,
) -> set[str]:
    """Select only source blocks that explain the displayed unit conflict."""

    referenced: set[str] = set()
    assignment = next(
        (
            row
            for row in plan.get("assignments") or []
            if isinstance(row, Mapping)
            and str(row.get("assignment_unit_id") or "") == unit_id
        ),
        None,
    )
    existing_id = target_concept_id
    new_key = ""
    if isinstance(assignment, Mapping):
        existing_id = str(
            assignment.get("existing_concept_id") or existing_id
        )
        new_key = str(assignment.get("new_concept_key") or "")
    if existing_id and existing_id != "NONE":
        for concept in concepts:
            if str(concept.get("concept_id") or "") != existing_id:
                continue
            referenced.update(
                str(value)
                for value in concept.get("source_block_ids") or []
                if str(value)
            )
    for update in plan.get("existing_concept_updates") or []:
        if not isinstance(update, Mapping):
            continue
        update_units = {
            str(value)
            for value in update.get("assignment_unit_ids") or []
            if str(value)
        }
        if (
            unit_id not in update_units
            and str(update.get("existing_concept_id") or "") != existing_id
        ):
            continue
        referenced.update(
            str(value)
            for value in update.get("source_block_ids") or []
            if str(value)
        )
    for definition in plan.get("new_concepts") or []:
        if not isinstance(definition, Mapping):
            continue
        definition_units = {
            str(value)
            for value in definition.get("assignment_unit_ids") or []
            if str(value)
        }
        if (
            unit_id not in definition_units
            and str(definition.get("new_concept_key") or "") != new_key
        ):
            continue
        referenced.update(
            str(value)
            for value in definition.get("source_block_ids") or []
            if str(value)
        )
    return referenced


def _host_pending_decision(
    *,
    identity: Mapping[str, str],
    topic: dict[str, Any],
    units: list[dict[str, Any]],
    concepts: list[dict[str, Any]],
    source_blocks: list[dict[str, Any]],
    issues: list[str],
    proposed_plan: dict[str, Any] | None = None,
    resolved_choice: dict[str, Any] | None = None,
    resolved_choices: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    clean_issues = [
        re.sub(r"\s+", " ", str(issue or "")).strip()[:1200]
        for issue in issues
        if str(issue or "").strip()
    ] or ["No safe Type host was certified."]
    plan = proposed_plan if isinstance(proposed_plan, dict) else {}
    rejected_ids = {
        str(value)
        for value in (plan.get("rejected_assignment_unit_ids") or [])
        if str(value)
    }
    if not rejected_ids:
        rejected_ids = {
            str(row.get("assignment_unit_id") or "")
            for row in units
            if str(row.get("assignment_unit_id") or "")
            and any(
                str(row.get("assignment_unit_id") or "") in issue
                for issue in clean_issues
            )
        }
    if not rejected_ids:
        rejected_ids = {
            str(row.get("assignment_unit_id") or "")
            for row in units
            if str(row.get("assignment_unit_id") or "")
        }
    affected = [
        copy.deepcopy(row)
        for row in units
        if str(row.get("assignment_unit_id") or "") in rejected_ids
    ] or [copy.deepcopy(row) for row in units]
    primary = affected[0] if affected else {}
    primary_id = str(primary.get("assignment_unit_id") or "")
    matching_issues = [
        issue for issue in clean_issues if primary_id and primary_id in issue
    ]
    displayed_issues = (
        matching_issues
        if matching_issues
        else clean_issues if len(affected) == 1 else clean_issues[:1]
    )
    topic_value = {
        "topic_id": str(topic.get("topic_id") or primary.get("topic_id") or ""),
        "title": str(topic.get("title") or ""),
    }
    item = {
        "unit_id": str(primary.get("assignment_unit_id") or ""),
        "type_id": str(primary.get("type_id") or ""),
        "type_title": str(primary.get("type_title") or ""),
        "qids": [
            str(value)
            for value in (primary.get("source_question_ids") or [])[:100]
            if str(value)
        ],
        "questions": [
            str(value)
            for value in (primary.get("source_tasks") or [])[:100]
            if str(value)
        ],
        "topic": copy.deepcopy(topic_value),
    }
    selected_ids = {
        str(row.get("existing_concept_id") or "")
        for row in plan.get("assignments") or []
        if isinstance(row, dict)
        and str(row.get("assignment_unit_id") or "") == primary_id
        and str(row.get("decision") or "") in {"existing", "expand_existing"}
        and str(row.get("existing_concept_id") or "") not in {"", "NONE"}
    }
    all_resolutions = [
        copy.deepcopy(row)
        for row in (resolved_choices or [])
        if isinstance(row, dict)
    ]
    if (
        isinstance(resolved_choice, dict)
        and not any(
            str(row.get("decision_id") or "")
            == str(resolved_choice.get("decision_id") or "")
            for row in all_resolutions
        )
    ):
        all_resolutions.append(copy.deepcopy(resolved_choice))
    matching_resolution = next(
        (
            row
            for row in reversed(all_resolutions)
            if primary_id in _resolution_unit_ids(row)
        ),
        None,
    )
    target_id = str(
        (matching_resolution or {}).get("target_concept_id") or ""
    )
    if not target_id and len(selected_ids) == 1:
        target_id = next(iter(selected_ids))
    candidates = []
    gap = "; ".join(displayed_issues[:4])
    ordered_concepts = sorted(
        concepts,
        key=lambda concept: (
            str(concept.get("concept_id") or "") != target_id,
        ),
    )
    for concept in ordered_concepts[:100]:
        concept_id = str(concept.get("concept_id") or "")
        candidates.append(
            {
                "concept_id": concept_id,
                "title": str(concept.get("concept_title") or "")[:8000],
                "topic": str(
                    concept.get("topic") or topic_value["title"]
                )[:8000],
                "coverage": str(concept.get("source_claim") or "")[:8000],
                "gap": gap if concept_id in selected_ids or concept_id == target_id else "",
            }
        )
    referenced_block_ids = _plan_source_block_ids_for_unit(
        plan,
        unit_id=primary_id,
        concepts=concepts,
        target_concept_id=target_id,
    )
    usable_source_blocks = [
        row for row in source_blocks if isinstance(row, dict)
    ]
    evidence_rows = [
        *[
            row
            for row in usable_source_blocks
            if str(row.get("block_id") or "") in referenced_block_ids
        ],
        *[
            row
            for row in usable_source_blocks
            if str(row.get("block_id") or "") not in referenced_block_ids
        ],
    ][:24]
    evidence = [
        {
            "page": (
                row.get("page_number")
                or row.get("pdf_page")
                or row.get("page")
                or None
            ),
            "label": str(row.get("block_id") or ""),
            "text": str(row.get("text") or ""),
        }
        for row in evidence_rows
    ]
    expand_option: dict[str, Any] = {
        "choice": "expand_existing",
        "label": "Expand the existing concept",
        "recommended": bool(target_id),
    }
    if target_id:
        expand_option["target_concept_id"] = target_id
    decision_id = str(identity.get("decision_id") or "")
    if all_resolutions:
        rejection_hash = phase3._sha256_json(
            {
                "context_hash": str(identity.get("context_hash") or ""),
                "resolved_choices": phase31._json_safe(all_resolutions),
                "assignment_unit_id": primary_id,
                "issues": displayed_issues,
            }
        )
        decision_id = f"phase33-host-{rejection_hash[:24]}"
    options: list[dict[str, Any]] = []
    if concepts:
        options.append(expand_option)
    options.append(
        {
            "choice": "create_new",
            "label": "Create a separate source-grounded concept",
            "recommended": not concepts,
        }
    )
    if concepts:
        options.append(
            {
                "choice": "select_existing",
                "label": "Select another existing concept",
                "recommended": False,
            }
        )
    options.append(
        {
            "choice": "custom_instruction",
            "label": "Give a custom instruction",
            "recommended": False,
        }
    )
    pending = {
        "decision_id": decision_id,
        "context_hash": str(identity.get("context_hash") or ""),
        "kind": "phase33_type_host_semantic_conflict",
        "phase": "3.3",
        "conflict": "; ".join(displayed_issues[:6]),
        "topic": topic_value,
        "assignment_unit": copy.deepcopy(item),
        "item": item,
        "candidates": candidates,
        "evidence": evidence,
        "issues": displayed_issues,
        "deferred_assignment_unit_ids": [
            str(row.get("assignment_unit_id") or "")
            for row in affected[1:]
            if str(row.get("assignment_unit_id") or "")
        ],
        "options": options,
    }
    return _plain_json(pending)


def _artifact_dir() -> Path | None:
    session = phase3.active_session()
    if not isinstance(session, dict) or not session.get("artifact_dir"):
        return None
    return Path(session["artifact_dir"])


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_json(path: Path, value: dict[str, Any]) -> None:
    phase3._atomic_write(
        path,
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def _max_topology_convergence_passes() -> int:
    return max(
        1,
        min(
            3,
            int(os.environ.get("AEGIS_PHASE33_TOPOLOGY_CONVERGENCE_PASSES", "2")),
        ),
    )


def _max_host_attempts() -> int:
    return max(
        1,
        min(
            5,
            int(os.environ.get("AEGIS_PHASE33_HOST_MAX_ATTEMPTS", "3")),
        ),
    )


def _topic_evidence_fingerprint(topics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "topic_id": str(row.get("topic_id") or ""),
            "title": str(row.get("title") or ""),
            "order": int(row.get("order") or 0),
            "structural_number": str(row.get("structural_number") or ""),
            "evidence_sha256": phase3._sha256_text(row.get("evidence") or ""),
        }
        for row in topics
        if isinstance(row, dict)
    ]


def _phase32_decision_key(
    payload: dict[str, Any], concept: dict[str, Any]
) -> str:
    graph = phase3.active_graph() or {}
    order = phase32._teaching_order_for(payload)
    return phase3._sha256_json(
        {
            "version": _DECISION_CACHE_VERSION,
            "model": str(config.OPENAI_MODEL),
            "semantic_confidence_policy": confidence_policy.cache_identity(),
            "source": early_gate.source_identity(graph),
            "placement_policy_version": placement_policy.POLICY_VERSION,
            "teaching_order_sha256": order.sha256,
            "topics": _topic_evidence_fingerprint(
                [row for row in payload.get("topics") or [] if isinstance(row, dict)]
            ),
            "concept": phase31._json_safe(concept),
        }
    )


def _decision_relationship_ids(decision: object) -> set[str]:
    if not isinstance(decision, dict):
        return set()
    return {
        str(relation.get("relationship_id") or "")
        for segment in decision.get("segments") or []
        if isinstance(segment, dict)
        for relation in segment.get("topic_relationships") or []
        if isinstance(relation, dict)
        and str(relation.get("relationship_id") or "")
    }


def _verified_relationship_reviews(
    reviews: object,
    expected_ids: set[str],
) -> list[dict[str, Any]] | None:
    rows = [
        copy.deepcopy(row)
        for row in reviews or []
        if isinstance(row, dict)
        and str(row.get("relationship_id") or "") in expected_ids
    ] if isinstance(reviews, list) else []
    _verdicts, errors = phase32._relationship_review_verdicts(
        {"relationship_reviews": rows}, expected_ids
    )
    return None if errors else rows


def _decision_split_attestations(
    decision: object,
) -> dict[str, dict[str, Any]] | None:
    if not isinstance(decision, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for segment in decision.get("segments") or []:
        if not isinstance(segment, dict):
            continue
        contract = segment.get("_placement_contract")
        raw = contract.get("split_attestation") if isinstance(contract, dict) else None
        if not raw:
            continue
        try:
            pending = placement_policy.validate_split_attestation(
                raw, require_accepted=False
            )
        except placement_policy.PlacementPolicyError:
            return None
        review_id = str(pending.get("split_review_id") or "")
        if review_id in out and out[review_id] != pending:
            return None
        out[review_id] = pending
    return out


def _verified_split_reviews(
    reviews: object,
    expected: dict[str, dict[str, Any]],
) -> list[dict[str, Any]] | None:
    rows = [
        copy.deepcopy(row)
        for row in reviews or []
        if isinstance(row, dict)
        and str(row.get("split_review_id") or "") in expected
    ] if isinstance(reviews, list) else []
    _attestations, errors = phase32._split_review_verdicts(
        {"split_reviews": rows}, expected
    )
    return None if errors else rows


def _read_phase32_decision_cache() -> dict[str, Any]:
    directory = _artifact_dir()
    if directory is None:
        return {}
    cache = _read_json(directory / _DECISION_CACHE_FILENAME)
    if cache.get("version") != _DECISION_CACHE_VERSION:
        return {"version": _DECISION_CACHE_VERSION, "entries": {}}
    return cache


def _write_phase32_decision_cache(cache: dict[str, Any]) -> None:
    directory = _artifact_dir()
    if directory is None:
        return
    cache["version"] = _DECISION_CACHE_VERSION
    cache.setdefault("entries", {})
    _write_json(directory / _DECISION_CACHE_FILENAME, cache)


def _project_stable_topology_decisions(
    response: dict[str, Any],
    *,
    concepts: list[dict[str, Any]],
) -> dict[str, Any]:
    """Make keep/move semantics deterministic before Phase 3.2 parsing.

    ``keep`` means no concept rewrite. ``move`` means the complete source-facing
    concept changes topic, not meaning. The model still chooses the target topic
    and the independent critic still verifies the projected proposal.
    """
    value = copy.deepcopy(response)
    by_id = {
        str(row.get("concept_id") or ""): row
        for row in concepts
        if isinstance(row, dict) and str(row.get("concept_id") or "")
    }
    rows = value.get("concepts") if isinstance(value, dict) else None
    if not isinstance(rows, list):
        return value
    for row in rows:
        if not isinstance(row, dict):
            continue
        concept = by_id.get(str(row.get("concept_id") or ""))
        segments = row.get("segments")
        decision = str(row.get("decision") or "")
        if concept is None or not isinstance(segments, list) or len(segments) != 1:
            continue
        if decision not in {"keep", "move"}:
            continue
        segment = segments[0]
        if not isinstance(segment, dict):
            continue
        if decision == "keep":
            segment["topic_id"] = str(concept.get("current_topic_id") or "")
        segment["concept_title"] = str(concept.get("concept_title") or "")
        segment["description"] = str(concept.get("source_claim") or "")
        if decision == "keep":
            segment["parent_concept"] = str(concept.get("parent_concept") or "")
        elif not str(segment.get("parent_concept") or "").strip():
            segment["parent_concept"] = str(concept.get("parent_concept") or "")
        existing_mastery = str(concept.get("existing_mastery") or "").strip()
        if existing_mastery:
            segment["achieving_mastery"] = existing_mastery
        if not isinstance(segment.get("keywords"), list):
            segment["keywords"] = []
    return value


def _phase32_provider_with_cache(
    original: TopologyProvider,
    payload: dict[str, Any],
) -> dict[str, Any]:
    requested = [
        row for row in payload.get("concepts") or [] if isinstance(row, dict)
    ]
    feedback = dict(_EXTERNAL_GROUNDING_FEEDBACK.get() or {})
    cache = _read_phase32_decision_cache()
    entries = cache.setdefault("entries", {})
    cached_rows: dict[str, dict[str, Any]] = {}
    missing: list[dict[str, Any]] = []
    for concept in requested:
        concept_id = str(concept.get("concept_id") or "")
        key = _phase32_decision_key(payload, concept)
        entry = entries.get(key)
        if (
            concept_id not in feedback
            and isinstance(entry, dict)
            and entry.get("status") == "verified"
            and isinstance(entry.get("decision"), dict)
        ):
            cached_rows[concept_id] = copy.deepcopy(entry["decision"])
        else:
            missing.append(concept)

    fresh_rows: list[dict[str, Any]] = []
    if missing:
        fresh_payload = copy.deepcopy(payload)
        fresh_payload["concepts"] = copy.deepcopy(missing)
        relevant_feedback = {
            str(row.get("concept_id") or ""): feedback[str(row.get("concept_id") or "")]
            for row in missing
            if str(row.get("concept_id") or "") in feedback
        }
        if relevant_feedback:
            existing = fresh_payload.get("critic_feedback")
            if not isinstance(existing, dict):
                existing = {}
            existing = copy.deepcopy(existing)
            existing["exact_grounding_feedback"] = relevant_feedback
            existing["instruction"] = (
                "The prior independently reviewed topology still failed exact "
                "source-block grounding. Repair the affected original concept "
                "with keep/move/refine/split as source evidence requires."
            )
            fresh_payload["critic_feedback"] = existing
        fresh = original(fresh_payload)
        fresh = _project_stable_topology_decisions(
            fresh,
            concepts=missing,
        )
        fresh_rows = [
            row for row in (fresh or {}).get("concepts") or [] if isinstance(row, dict)
        ]

    fresh_by_id = {
        str(row.get("concept_id") or ""): row for row in fresh_rows
    }
    combined = []
    for concept in requested:
        concept_id = str(concept.get("concept_id") or "")
        row = cached_rows.get(concept_id) or fresh_by_id.get(concept_id)
        if row is not None:
            combined.append(copy.deepcopy(row))
    if cached_rows:
        progress.log(
            "Reused independently verified Phase 3.2 topology decisions for "
            f"{len(cached_rows)} concept(s); only uncached or grounding-rejected "
            "concepts were sent back to the adjudicator.",
            level="success",
        )
    return {"concepts": combined}


def _phase32_critic_with_cache(
    original: TopologyCritic,
    payload: dict[str, Any],
) -> dict[str, Any]:
    concepts = [
        row for row in payload.get("concepts") or [] if isinstance(row, dict)
    ]
    proposed = [
        row for row in payload.get("proposed_decisions") or [] if isinstance(row, dict)
    ]
    feedback = dict(_EXTERNAL_GROUNDING_FEEDBACK.get() or {})
    cache = _read_phase32_decision_cache()
    entries = cache.setdefault("entries", {})
    by_id = {str(row.get("concept_id") or ""): row for row in proposed}
    cached_ids: set[str] = set()
    cached_relationship_reviews: dict[str, list[dict[str, Any]]] = {}
    cached_split_reviews: dict[str, list[dict[str, Any]]] = {}
    for concept in concepts:
        concept_id = str(concept.get("concept_id") or "")
        key = _phase32_decision_key(payload, concept)
        entry = entries.get(key)
        expected_relationship_ids = _decision_relationship_ids(
            by_id.get(concept_id)
        )
        verified_reviews = (
            _verified_relationship_reviews(
                entry.get("relationship_reviews"),
                expected_relationship_ids,
            )
            if isinstance(entry, dict)
            else None
        )
        expected_split_attestations = _decision_split_attestations(
            by_id.get(concept_id)
        )
        verified_split_reviews = (
            _verified_split_reviews(
                entry.get("split_reviews"), expected_split_attestations
            )
            if isinstance(entry, dict)
            and expected_split_attestations is not None
            else None
        )
        if (
            concept_id not in feedback
            and isinstance(entry, dict)
            and entry.get("status") == "verified"
            and phase3._sha256_json(entry.get("decision"))
            == phase3._sha256_json(by_id.get(concept_id))
            and verified_reviews is not None
            and verified_split_reviews is not None
        ):
            cached_ids.add(concept_id)
            cached_relationship_reviews[concept_id] = verified_reviews
            cached_split_reviews[concept_id] = verified_split_reviews
    fresh_concepts = [
        row
        for row in concepts
        if str(row.get("concept_id") or "") not in cached_ids
    ]
    if not fresh_concepts:
        ids = [str(row.get("concept_id") or "") for row in concepts]
        return {
            "verdict": "verified",
            # This is a replay of the exact decision that already cleared the
            # configured gate, not a new probabilistic model score.  Use the
            # identity confidence so a deliberately strict 1.0 destructive
            # gate can still reuse its own previously verified cache entry.
            "confidence": 1.0,
            "accepted_concept_ids": ids,
            "rejected_concept_ids": [],
            "issues": [],
            "relationship_reviews": [
                copy.deepcopy(review)
                for concept_id in ids
                for review in cached_relationship_reviews.get(concept_id, [])
            ],
            "split_reviews": [
                copy.deepcopy(review)
                for concept_id in ids
                for review in cached_split_reviews.get(concept_id, [])
            ],
        }

    fresh_ids = {
        str(row.get("concept_id") or "") for row in fresh_concepts
    }
    fresh_payload = copy.deepcopy(payload)
    fresh_payload["concepts"] = copy.deepcopy(fresh_concepts)
    fresh_payload["proposed_decisions"] = [
        copy.deepcopy(row)
        for row in proposed
        if str(row.get("concept_id") or "") in fresh_ids
    ]
    fresh_relationship_ids = {
        relationship_id
        for row in fresh_payload["proposed_decisions"]
        for relationship_id in _decision_relationship_ids(row)
    }
    fresh_split_ids = {
        review_id
        for row in fresh_payload["proposed_decisions"]
        for review_id in (_decision_split_attestations(row) or {})
    }
    if isinstance(fresh_payload.get("placement_relationship_evidence"), list):
        fresh_payload["placement_relationship_evidence"] = [
            copy.deepcopy(row)
            for row in fresh_payload["placement_relationship_evidence"]
            if isinstance(row, dict)
            and str(row.get("relationship_id") or "")
            in fresh_relationship_ids
        ]
    if isinstance(fresh_payload.get("split_review_attestations"), list):
        fresh_payload["split_review_attestations"] = [
            copy.deepcopy(row)
            for row in fresh_payload["split_review_attestations"]
            if isinstance(row, dict)
            and str(row.get("split_review_id") or "") in fresh_split_ids
        ]
    review = original(fresh_payload)
    # Phase 3.2 has no destructive retirement action.  Every replayed
    # topology decision uses the ordinary semantic confidence gate.
    review_gate = confidence_policy.ConfidenceGate.SEMANTIC
    state = phase31._review_state(
        review,
        concept_ids=fresh_ids,
        confidence_gate=review_gate,
    )
    accepted_fresh = (
        set(state["accepted"])
        if confidence_policy.accepts(state["confidence"], review_gate)
        and early_gate.confidence_band(state["confidence"])
        == "accepted"
        else set()
    )
    fresh_review_rows = [
        copy.deepcopy(row)
        for row in (review or {}).get("relationship_reviews") or []
        if isinstance(row, dict)
    ]
    fresh_split_review_rows = [
        copy.deepcopy(row)
        for row in (review or {}).get("split_reviews") or []
        if isinstance(row, dict)
    ]
    if accepted_fresh:
        now = time.time()
        for concept in fresh_concepts:
            concept_id = str(concept.get("concept_id") or "")
            if concept_id not in accepted_fresh:
                continue
            decision = by_id.get(concept_id)
            if decision is None:
                continue
            expected_relationship_ids = _decision_relationship_ids(decision)
            verified_reviews = _verified_relationship_reviews(
                fresh_review_rows,
                expected_relationship_ids,
            )
            expected_split_attestations = _decision_split_attestations(decision)
            verified_split_reviews = (
                _verified_split_reviews(
                    fresh_split_review_rows, expected_split_attestations
                )
                if expected_split_attestations is not None
                else None
            )
            if verified_reviews is None or verified_split_reviews is None:
                # The ordinary live Phase 3.2 post-critic validator will reject
                # this response.  Never persist a concept whose typed
                # relationships did not independently clear the same gate.
                continue
            key = _phase32_decision_key(payload, concept)
            entries[key] = {
                "status": "verified",
                "created_at": now,
                "model": str(config.OPENAI_MODEL),
                "source_contract_hash": str(
                    (phase3.active_graph() or {}).get("source_contract_hash") or ""
                ),
                "review_confidence": float(state["confidence"]),
                "concept_id": concept_id,
                "decision": copy.deepcopy(decision),
                "relationship_reviews": verified_reviews,
                "split_reviews": verified_split_reviews,
            }
        _write_phase32_decision_cache(cache)
    # Already verified IDs are replayed as deterministic accepts. The live
    # critic sees only uncached IDs, and a partial rejection still persists its
    # explicitly accepted siblings before the typed human pause unwinds.
    accepted = cached_ids | accepted_fresh
    rejected = set(state["rejected"])
    return {
        "verdict": "verified" if state["verified"] else "rejected",
        "confidence": float(state["confidence"]),
        "accepted_concept_ids": sorted(accepted),
        "rejected_concept_ids": sorted(rejected),
        "issues": list(state["issues"]),
        "relationship_reviews": [
            copy.deepcopy(review)
            for concept_id in sorted(cached_ids)
            for review in cached_relationship_reviews.get(concept_id, [])
        ] + fresh_review_rows,
        "split_reviews": [
            copy.deepcopy(review)
            for concept_id in sorted(cached_ids)
            for review in cached_split_reviews.get(concept_id, [])
        ] + fresh_split_review_rows,
    }


_GROUNDING_ID_RE = re.compile(r"CONCEPT-GROUND-(?P<number>\d{4})")
_GROUNDING_TOPIC_RE = re.compile(r"for topic (?P<quote>['\"])(?P<topic>.+?)(?P=quote)")


def _grounding_feedback_origins(
    exc: Exception,
    *,
    records: list[dict[str, Any]],
) -> dict[str, str]:
    message = str(exc)
    origins: dict[str, str] = {}
    for match in _GROUNDING_ID_RE.finditer(message):
        index = int(match.group("number")) - 1
        if not 0 <= index < len(records):
            continue
        origin = str(records[index].get("_phase32_origin_concept_id") or "")
        if origin:
            origins[origin] = message[:6000]
    if origins:
        return origins
    topic_match = _GROUNDING_TOPIC_RE.search(message)
    if topic_match:
        topic_key = _normal(topic_match.group("topic"))
        for record in records:
            if _normal(record.get("topic")) != topic_key:
                continue
            origin = str(record.get("_phase32_origin_concept_id") or "")
            if origin:
                origins[origin] = message[:6000]
    return origins


def _phase32_adjudicate_with_convergence(
    original: Callable[..., list[dict[str, Any]]],
    records: list[dict[str, Any]],
    *args: Any,
    **kwargs: Any,
) -> list[dict[str, Any]]:
    # Injected providers are deterministic unit-test/extension boundaries. Do not
    # reinterpret their expected call counts or failures.
    if any(
        kwargs.get(name) is not None
        for name in ("provider", "critic", "grounding_provider", "grounding_critic")
    ):
        return original(records, *args, **kwargs)
    passes = _max_topology_convergence_passes()
    feedback: dict[str, str] = {}
    for convergence_pass in range(1, passes + 1):
        token = _EXTERNAL_GROUNDING_FEEDBACK.set(feedback)
        try:
            return original(records, *args, **kwargs)
        except early_gate.TopologyRepairRequired:
            # A saved human direction is not an automatic convergence failure.
            # Let Phase 3.8 suppress that exact one-shot resolution and carry
            # its bounded action into the next independently reviewed pass.
            raise
        except ValueError as exc:
            message = str(exc)
            if (
                convergence_pass >= passes
                or "failed exact source-block grounding before freeze" not in message
            ):
                raise
            # Phase 3.1 IDs are indices in the *repaired* row list. A split or
            # move can therefore change their relationship to the original input.
            # Reconsider every original normal concept once, carrying the exact
            # critic diagnostic. Per-concept caches still make the ordinary first
            # pass cheap, and a bounded whole-topology reconsideration is safer
            # than guessing which origin produced a new split segment.
            feedback = {
                f"TOPOLOGY-CONCEPT-{index + 1:04d}": message[:6000]
                for index, row in enumerate(records)
                if not cr.is_culmination(
                    str(row.get("concept_title") or row.get("concept") or "")
                )
            }
            progress.log(
                "Phase 3.3 is feeding exact source-grounding rejection back into "
                f"topology adjudication for {len(feedback)} original concept(s), "
                f"convergence pass {convergence_pass + 1}/{passes}.",
                level="warning",
            )
        finally:
            _EXTERNAL_GROUNDING_FEEDBACK.reset(token)
    raise RuntimeError("Phase 3.3 topology convergence ended unexpectedly")


def _stable_assignment_units(
    generation: Any,
    mined_types: dict[str, Any],
    graph: dict[str, Any],
) -> list[dict[str, Any]]:
    expanded = generation._expand_mined_types_to_assignment_units(
        list((mined_types or {}).get("types") or [])
    )
    units = phase3.annotate_assignment_units(expanded, graph)
    counts: dict[str, int] = {}
    out: list[dict[str, Any]] = []
    for position, raw in enumerate(units, start=1):
        if not isinstance(raw, dict):
            continue
        unit = copy.deepcopy(raw)
        base = str(unit.get("type_id") or f"UNIT-{position:04d}")
        counts[base] = counts.get(base, 0) + 1
        unit_id = base if counts[base] == 1 else f"{base}--{counts[base]:03d}"
        unit["_phase33_assignment_unit_id"] = unit_id
        out.append(unit)
    return out


def _inventory_task_evidence(
    generation: Any,
    inventory: dict[str, Any] | None,
) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    by_qid: dict[str, str] = {}
    rows: dict[str, dict[str, Any]] = {}
    for item in (inventory or {}).get("items") or []:
        if not isinstance(item, dict):
            continue
        qid = str(item.get("qid") or "").strip()
        if not qid:
            continue
        rows[qid] = item
        text = generation._inventory_task_text(item)
        if text:
            by_qid[qid] = text
    return by_qid, rows


def _unit_payload(
    unit: dict[str, Any],
    *,
    task_by_qid: dict[str, str],
) -> dict[str, Any]:
    qids = [str(value) for value in unit.get("source_question_ids") or [] if str(value)]
    tasks = [task_by_qid[qid] for qid in qids if qid in task_by_qid]
    cases = [row for row in unit.get("case_prompts") or [] if isinstance(row, dict)]
    case = cases[0] if cases else {}
    return {
        "assignment_unit_id": str(unit.get("_phase33_assignment_unit_id") or ""),
        "type_id": str(unit.get("type_id") or ""),
        "origin_type_id": str(unit.get("_origin_type_id") or unit.get("type_id") or ""),
        "topic_id": str(unit.get("_semantic_topic_id") or ""),
        "type_title": str(unit.get("type_title") or unit.get("title") or ""),
        "type_description": str(
            unit.get("type_description") or unit.get("description") or ""
        ),
        "task_pattern": str(unit.get("task_pattern") or ""),
        "case_title": str(case.get("case_title") or ""),
        "case_definition": str(case.get("case_definition") or ""),
        "source_question_ids": qids,
        "source_tasks": tasks,
    }


def _type_case_placement_batch_size() -> int:
    try:
        value = int(os.environ.get("AEGIS_PHASE33_PLACEMENT_BATCH_SIZE", "12"))
    except (TypeError, ValueError):
        value = 12
    return max(1, min(32, value))


def _type_case_placement_schema(
    claim_ids: list[str],
    topic_ids: list[str],
    block_ids: list[str],
) -> dict[str, Any]:
    return {
        "name": "phase33_type_case_topic_relationships",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "relationships": {
                    "type": "array",
                    "minItems": len(claim_ids),
                    "maxItems": max(len(claim_ids), len(claim_ids) * 12),
                    "items": {
                        "type": "object",
                        "properties": {
                            "claim_id": {"type": "string", "enum": claim_ids},
                            "topic_id": {"type": "string", "enum": topic_ids},
                            "relationship_type": {
                                "type": "string",
                                "enum": [
                                    kind.value
                                    for kind in placement_policy.RelationshipType
                                ],
                            },
                            "necessity": {"type": "boolean"},
                            "evidence_block_ids": {
                                "type": "array",
                                "items": {"type": "string", "enum": block_ids},
                            },
                            "reason": {"type": "string", "minLength": 1},
                        },
                        "required": [
                            "claim_id",
                            "topic_id",
                            "relationship_type",
                            "necessity",
                            "evidence_block_ids",
                            "reason",
                        ],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["relationships"],
            "additionalProperties": False,
        },
    }


def _type_case_placement_critic_schema(
    relationship_ids: list[str],
) -> dict[str, Any]:
    return {
        "name": "phase33_type_case_topic_relationship_critic",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "verdict": {
                    "type": "string",
                    "enum": ["verified", "rejected"],
                },
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "issues": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "relationship_reviews": {
                    "type": "array",
                    "minItems": len(relationship_ids),
                    "maxItems": len(relationship_ids),
                    "items": {
                        "type": "object",
                        "properties": {
                            "relationship_id": {
                                "type": "string",
                                "enum": relationship_ids,
                            },
                            "verdict": {
                                "type": "string",
                                "enum": ["accepted", "rejected"],
                            },
                            "evidence_supported": {"type": "boolean"},
                            "necessity_supported": {"type": "boolean"},
                            "direction_supported": {"type": "boolean"},
                            "reason": {"type": "string"},
                        },
                        "required": [
                            "relationship_id",
                            "verdict",
                            "evidence_supported",
                            "necessity_supported",
                            "direction_supported",
                            "reason",
                        ],
                        "additionalProperties": False,
                    },
                },
            },
            "required": [
                "verdict", "confidence", "issues", "relationship_reviews",
            ],
            "additionalProperties": False,
        },
    }


def _type_case_placement_via_openai(payload: dict[str, Any]) -> dict[str, Any]:
    claim_ids = [
        str(row.get("claim_id") or "")
        for row in payload.get("claims") or []
        if isinstance(row, dict) and str(row.get("claim_id") or "")
    ]
    topic_ids = [
        str(row.get("topic_id") or "")
        for row in payload.get("topics") or []
        if isinstance(row, dict) and str(row.get("topic_id") or "")
    ]
    block_ids = [
        str(value) for value in payload.get("allowed_block_ids") or []
        if str(value)
    ]
    system = (
        placement_policy.PROVIDER_SYSTEM
        + "\nEach claim here is one exact textbook QID/task. Classify the "
        "topics whose knowledge or method is necessary to perform that task. "
        "The task's physical source topic is provenance only. A reusable Type "
        "may contain Cases owned by different topics, so judge every claim "
        "independently. Return at least one CORE_TEACHING or "
        "REQUIRED_LATER_METHOD relationship for every claim. Every returned "
        "relationship, including references, illustrations, and incidental "
        "mentions, must cite at least one exact allowed source block."
    )
    return phase3.phase22._openai_multimodal_json(
        system=system,
        prompt=json.dumps(payload, ensure_ascii=False, indent=2),
        pages=[],
        response_schema=_type_case_placement_schema(
            claim_ids, topic_ids, block_ids
        ),
        purpose="concept_mapping",
        max_tokens=max(
            6000,
            min(config.OPENAI_MAX_OUTPUT_TOKENS, len(claim_ids) * 1400),
        ),
    )


def _type_case_placement_critic_via_openai(
    payload: dict[str, Any],
) -> dict[str, Any]:
    relationship_ids = [
        str(row.get("relationship_id") or "")
        for row in payload.get("proposed_relationships") or []
        if isinstance(row, dict) and str(row.get("relationship_id") or "")
    ]
    system = (
        placement_policy.CRITIC_SYSTEM
        + "\nAudit every QID independently. Confirm the exact cited block, "
        "necessity, and dependency direction. A later topic owns a task only "
        "when its method or framework is genuinely required to perform it; "
        "mere wording, physical location, chronology, or an optional method "
        "must not move it. Return every relationship_id exactly once."
    )
    return phase3.phase22._openai_multimodal_json(
        system=system,
        prompt=json.dumps(payload, ensure_ascii=False, indent=2),
        pages=[],
        response_schema=_type_case_placement_critic_schema(relationship_ids),
        purpose="concept_mapping",
        max_tokens=max(
            5000,
            min(config.OPENAI_MAX_OUTPUT_TOKENS, len(relationship_ids) * 350),
        ),
    )


def _type_case_claim_rows(
    generation: Any,
    *,
    units: list[dict[str, Any]],
    inventory: dict[str, Any],
    graph: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    task_by_qid = {
        str(row.get("qid") or ""): row
        for row in graph.get("tasks") or []
        if isinstance(row, dict) and str(row.get("qid") or "")
    }
    context_by_qid: dict[str, dict[str, Any]] = {}
    for unit in units:
        unit_id = str(unit.get("_phase33_assignment_unit_id") or "")
        cases = [
            row for row in unit.get("case_prompts") or []
            if isinstance(row, dict)
        ]
        case = cases[0] if cases else {}
        context = {
            "assignment_unit_id": unit_id,
            "type_id": str(unit.get("type_id") or ""),
            "type_title": str(unit.get("type_title") or unit.get("title") or ""),
            "task_pattern": str(unit.get("task_pattern") or ""),
            "case_id": str(case.get("case_id") or ""),
            "case_title": str(case.get("case_title") or ""),
            "case_definition": str(case.get("case_definition") or ""),
        }
        for raw_qid in unit.get("source_question_ids") or []:
            qid = str(raw_qid or "").strip()
            if not qid:
                continue
            if qid in context_by_qid:
                raise RuntimeError(
                    "type_case_owner_uncertified: QID "
                    f"{qid} appears in multiple assignment units"
                )
            context_by_qid[qid] = copy.deepcopy(context)

    rows_by_qid: dict[str, dict[str, Any]] = {}
    claims: list[dict[str, Any]] = []
    for item in (inventory or {}).get("items") or []:
        if not isinstance(item, dict):
            continue
        qid = str(item.get("qid") or "").strip()
        if not qid:
            continue
        if qid in rows_by_qid:
            raise RuntimeError(
                f"type_case_owner_uncertified: duplicate inventory QID {qid}"
            )
        # Phase 2.1 keeps the original canonical task objects and materializes
        # independently answerable leaves (QINV-0011 -> .1/.2) only in the
        # production inventory, so a leaf owns its own text and route while the
        # graph still owns the parent task.  A raw per-QID lookup therefore
        # fails closed here on a fully governed leaf; use the shared resolver.
        task = phase3._graph_task_for_qid(
            task_by_qid,
            qid,
            parent_qid=item.get("parent_qid") or item.get("_acsd_parent_qid"),
        )
        text = generation._inventory_task_text(item)
        if not isinstance(task, dict) or not text:
            raise RuntimeError(
                "type_case_owner_uncertified: QID "
                f"{qid} has no sealed semantic task/text"
            )
        # This is a certification boundary, so where a leaf carries its parent's
        # sealed identity the inherited task must actually match it.  Leaves
        # from older inventories carry no key and keep the resolver's behaviour.
        parent_identity = str(item.get("_acsd_parent_identity_key") or "").strip()
        if (
            parent_identity
            and str(task.get("qid") or "") != qid
            and str(task.get("identity_key") or "") != parent_identity
        ):
            raise RuntimeError(
                "type_case_owner_uncertified: QID "
                f"{qid} does not match its sealed parent task identity"
            )
        source_topic_id = str(task.get("topic_id") or "").strip()
        if not source_topic_id:
            raise RuntimeError(
                f"type_case_owner_uncertified: QID {qid} has no source topic"
            )
        existing_source = str(
            item.get("source_location_topic_id") or ""
        ).strip()
        if existing_source and existing_source != source_topic_id:
            raise RuntimeError(
                "type_case_owner_uncertified: QID "
                f"{qid} source provenance changed from {existing_source} "
                f"to {source_topic_id}"
            )
        item["source_location_topic_id"] = source_topic_id
        source_topic = next(
            (
                row for row in graph.get("topics") or []
                if isinstance(row, dict)
                and str(row.get("topic_id") or "") == source_topic_id
            ),
            {},
        )
        item["source_location_topic_title"] = str(
            source_topic.get("title") or ""
        )
        claim = {
            "claim_id": f"TASK-{qid}",
            "qid": qid,
            "normalized_claim": phase3._clean_public_text(text),
            "source_location_topic_id": source_topic_id,
            "type_case_context": copy.deepcopy(context_by_qid.get(qid) or {}),
        }
        claims.append(claim)
        rows_by_qid[qid] = item
    return claims, rows_by_qid


def _type_case_contract_is_current(
    generation: Any,
    contract: object,
    *,
    claim: dict[str, Any],
    graph: dict[str, Any],
    order: placement_policy.TeachingOrder,
    block_directory: dict[str, dict[str, Any]],
) -> bool:
    value = generation._certified_type_case_placement_contract(contract)
    if value is None:
        return False
    if any((
        str(value.get("policy_version") or "")
        != placement_policy.POLICY_VERSION,
        str(value.get("teaching_order_sha256") or "") != order.sha256,
        str(value.get("source_contract_hash") or "")
        != str(graph.get("source_contract_hash") or ""),
        str(value.get("claim_id") or "") != str(claim.get("claim_id") or ""),
        _normal(value.get("normalized_claim"))
        != _normal(claim.get("normalized_claim")),
        str(value.get("source_location_topic_id") or "")
        != str(claim.get("source_location_topic_id") or ""),
        not order.known(value.get("owner_topic_id")),
    )):
        return False
    relationships = placement_policy.parse_relationships(
        {"relationships": [
            {
                **row,
                "reason": str(
                    row.get("provider_reason") or row.get("reason") or ""
                ),
            }
            for row in value.get("topic_relationships") or []
            if isinstance(row, dict)
        ]},
        known_claim_ids=[str(claim.get("claim_id") or "")],
        known_topic_ids=order.ranks,
    )
    raw_relationships = [
        row for row in value.get("topic_relationships") or []
        if isinstance(row, dict)
    ]
    if not relationships or len(relationships) != len(raw_relationships):
        return False
    reviewed: list[placement_policy.TopicRelationship] = []
    for relation, raw in zip(relationships, raw_relationships):
        if not relation.evidence_block_ids:
            return False
        exact = [
            block_directory.get(block_id)
            for block_id in relation.evidence_block_ids
        ]
        if any(
            not isinstance(block, dict)
            or str(block.get("topic_id") or "") != relation.topic_id
            for block in exact
        ):
            return False
        if str(raw.get("relationship_id") or "") != relation.relationship_id:
            return False
        reviewed.append(placement_policy.TopicRelationship(
            claim_id=relation.claim_id,
            topic_id=relation.topic_id,
            relationship_type=relation.relationship_type,
            necessity=relation.necessity,
            evidence_block_ids=relation.evidence_block_ids,
            provider_reason=relation.provider_reason,
            critic_verdict=str(raw.get("critic_verdict") or ""),
        ))
    try:
        decision = placement_policy.compute_placement(
            placement_policy.AtomicClaim(
                claim_id=str(claim.get("claim_id") or ""),
                normalized_claim=str(claim.get("normalized_claim") or ""),
                source_location_topic_id=str(
                    claim.get("source_location_topic_id") or ""
                ),
                protected_source_items=(str(claim.get("qid") or ""),),
            ),
            reviewed,
            order,
        )
    except placement_policy.PlacementPolicyError:
        return False
    return (
        decision.owner_topic_id == str(value.get("owner_topic_id") or "")
        and list(decision.required_topic_ids)
        == list(value.get("required_topic_ids") or [])
        and list(decision.prerequisite_topic_ids)
        == list(value.get("prerequisite_topic_ids") or [])
        and [list(edge) for edge in decision.reference_edges]
        == list(value.get("reference_edges") or [])
        and list(decision.illustration_topic_ids)
        == list(value.get("illustration_topic_ids") or [])
    )


def _type_case_placement_cache_key(
    *,
    claim: dict[str, Any],
    graph: dict[str, Any],
    order: placement_policy.TeachingOrder,
) -> str:
    return phase3._sha256_json({
        "version": _TYPE_CASE_OWNERSHIP_VERSION,
        "model": str(config.OPENAI_MODEL),
        "semantic_confidence_policy": confidence_policy.cache_identity(),
        "source": early_gate.source_identity(graph),
        "policy_version": placement_policy.POLICY_VERSION,
        "teaching_order_sha256": order.sha256,
        "claim": phase31._json_safe(claim),
    })


def _read_type_case_placement_cache(
    *,
    graph: dict[str, Any],
    order: placement_policy.TeachingOrder,
) -> dict[str, Any]:
    directory = _artifact_dir()
    if directory is None:
        return {"entries": {}}
    value = _read_json(directory / _TYPE_CASE_PLACEMENT_CACHE_FILENAME)
    if any((
        value.get("version") != _TYPE_CASE_OWNERSHIP_VERSION,
        str(value.get("policy_version") or "")
        != placement_policy.POLICY_VERSION,
        str(value.get("teaching_order_sha256") or "") != order.sha256,
        str(value.get("source_contract_hash") or "")
        != str(graph.get("source_contract_hash") or ""),
        not isinstance(value.get("entries"), dict),
    )):
        return {"entries": {}}
    return value


def _write_type_case_placement_cache(
    cache: dict[str, Any],
    *,
    graph: dict[str, Any],
    order: placement_policy.TeachingOrder,
) -> None:
    directory = _artifact_dir()
    if directory is None:
        return
    value = {
        "version": _TYPE_CASE_OWNERSHIP_VERSION,
        "policy_version": placement_policy.POLICY_VERSION,
        "teaching_order_sha256": order.sha256,
        "source_contract_hash": str(graph.get("source_contract_hash") or ""),
        "entries": copy.deepcopy(cache.get("entries") or {}),
    }
    _write_json(directory / _TYPE_CASE_PLACEMENT_CACHE_FILENAME, value)


def _validated_type_case_relationships(
    response: dict[str, Any],
    *,
    claims: list[dict[str, Any]],
    order: placement_policy.TeachingOrder,
    block_directory: dict[str, dict[str, Any]],
) -> tuple[dict[str, list[placement_policy.TopicRelationship]], list[str]]:
    claim_ids = {str(row.get("claim_id") or "") for row in claims}
    raw = [
        row for row in (response or {}).get("relationships") or []
        if isinstance(row, dict)
    ]
    parsed = placement_policy.parse_relationships(
        {"relationships": raw},
        known_claim_ids=claim_ids,
        known_topic_ids=order.ranks,
    )
    errors: list[str] = []
    if len(parsed) != len(raw):
        errors.append("placement provider returned unknown claim/topic/type")
    grouped: dict[str, list[placement_policy.TopicRelationship]] = {
        claim_id: [] for claim_id in claim_ids
    }
    seen: set[tuple[str, str]] = set()
    for relation in parsed:
        key = (relation.claim_id, relation.topic_id)
        if key in seen:
            errors.append(
                f"{relation.claim_id} repeats/conflicts on topic {relation.topic_id}"
            )
            continue
        seen.add(key)
        necessary = relation.relationship_type in placement_policy.NECESSARY_TYPES
        if necessary != bool(relation.necessity):
            errors.append(
                f"{relation.relationship_id} has inconsistent necessity"
            )
        if not relation.provider_reason.strip():
            errors.append(f"{relation.relationship_id} omitted its reason")
        if not relation.evidence_block_ids:
            errors.append(
                f"{relation.relationship_id} has no exact source evidence"
            )
        if len(set(relation.evidence_block_ids)) != len(
            relation.evidence_block_ids
        ):
            errors.append(
                f"{relation.relationship_id} repeats exact evidence blocks"
            )
        for block_id in relation.evidence_block_ids:
            block = block_directory.get(block_id)
            if not isinstance(block, dict):
                errors.append(
                    f"{relation.relationship_id} cites unknown block {block_id}"
                )
            elif str(block.get("topic_id") or "") != relation.topic_id:
                errors.append(
                    f"{relation.relationship_id} cites {block_id} from topic "
                    f"{block.get('topic_id')} as evidence for {relation.topic_id}"
                )
        grouped.setdefault(relation.claim_id, []).append(relation)
    claim_by_id = {
        str(row.get("claim_id") or ""): row for row in claims
    }
    for claim_id, claim in claim_by_id.items():
        if not grouped.get(claim_id):
            errors.append(f"{claim_id} has no topic relationships")
            continue
        try:
            placement_policy.compute_provisional_placement(
                placement_policy.AtomicClaim(
                    claim_id=claim_id,
                    normalized_claim=str(claim.get("normalized_claim") or ""),
                    source_location_topic_id=str(
                        claim.get("source_location_topic_id") or ""
                    ),
                    protected_source_items=(str(claim.get("qid") or ""),),
                ),
                grouped[claim_id],
                order,
            )
        except placement_policy.PlacementPolicyError as exc:
            errors.append(f"{claim_id} placement is uncertifiable: {exc}")
    return grouped, list(dict.fromkeys(errors))


def _certify_type_case_batch(
    generation: Any,
    *,
    claims: list[dict[str, Any]],
    graph: dict[str, Any],
    canonical: dict[str, Any],
    order: placement_policy.TeachingOrder,
    block_directory: dict[str, dict[str, Any]],
    provider: PlacementProvider,
    critic: PlacementCritic,
) -> dict[str, dict[str, Any]]:
    topics, _block_order = phase32._topic_evidence(graph, canonical)
    payload = {
        "metadata": copy.deepcopy(graph.get("metadata") or {}),
        "policy_version": placement_policy.POLICY_VERSION,
        "teaching_order_sha256": order.sha256,
        "claims": copy.deepcopy(claims),
        "topics": copy.deepcopy(topics),
        "allowed_block_ids": sorted(block_directory),
    }
    grouped: dict[str, list[placement_policy.TopicRelationship]] = {}
    errors: list[str] = []
    for attempt in range(1, 3):
        request = copy.deepcopy(payload)
        request["attempt"] = attempt
        request["critic_feedback"] = {
            "verdict": "provider_contract_rejected",
            "issues": copy.deepcopy(errors),
        } if errors else {}
        response = provider(request)
        grouped, errors = _validated_type_case_relationships(
            response,
            claims=claims,
            order=order,
            block_directory=block_directory,
        )
        if not errors:
            break
    if errors:
        raise ProviderResponseContractError(
            "type_case_owner_uncertified: placement provider failed its "
            "bounded contract correction: " + "; ".join(errors[:8])
        )

    proposed = [
        placement_policy.relationship_audit_row(relation)
        for claim_id in sorted(grouped)
        for relation in grouped[claim_id]
    ]
    evidence = [
        {
            "relationship_id": relation.relationship_id,
            "claim_id": relation.claim_id,
            "topic_id": relation.topic_id,
            "relationship_type": relation.relationship_type.value,
            "necessity": relation.necessity,
            "provider_reason": relation.provider_reason,
            "exact_source_blocks": [
                copy.deepcopy(block_directory[block_id])
                for block_id in relation.evidence_block_ids
            ],
        }
        for claim_id in sorted(grouped)
        for relation in grouped[claim_id]
    ]
    review = critic({
        "metadata": copy.deepcopy(graph.get("metadata") or {}),
        "policy_version": placement_policy.POLICY_VERSION,
        "teaching_order_sha256": order.sha256,
        "claims": copy.deepcopy(claims),
        "proposed_relationships": proposed,
        "relationship_evidence": evidence,
    })
    confidence = float((review or {}).get("confidence") or 0.0)
    if (
        str((review or {}).get("verdict") or "") != "verified"
        or not confidence_policy.accepts(confidence)
        or list((review or {}).get("issues") or [])
    ):
        raise RuntimeError(
            "type_case_owner_uncertified: independent placement critic "
            "rejected the task relationship set"
        )
    relationship_ids = {
        relation.relationship_id
        for relationships in grouped.values()
        for relation in relationships
    }
    verdicts, review_errors = phase32._relationship_review_verdicts(
        review, relationship_ids
    )
    if review_errors:
        raise RuntimeError(
            "type_case_owner_uncertified: " + "; ".join(review_errors[:8])
        )

    claim_by_id = {
        str(row.get("claim_id") or ""): row for row in claims
    }
    contracts: dict[str, dict[str, Any]] = {}
    topic_title = {
        str(row.get("topic_id") or ""): str(row.get("title") or "")
        for row in graph.get("topics") or [] if isinstance(row, dict)
    }
    for claim_id, relationships in grouped.items():
        reviewed = placement_policy.apply_critic_verdicts(
            relationships, verdicts
        )
        claim_row = claim_by_id[claim_id]
        claim = placement_policy.AtomicClaim(
            claim_id=claim_id,
            normalized_claim=str(claim_row.get("normalized_claim") or ""),
            source_location_topic_id=str(
                claim_row.get("source_location_topic_id") or ""
            ),
            protected_source_items=(str(claim_row.get("qid") or ""),),
        )
        try:
            decision = placement_policy.compute_placement(
                claim, reviewed, order
            )
        except placement_policy.PlacementPolicyError as exc:
            raise RuntimeError(
                f"type_case_owner_uncertified: {claim_id}: {exc}"
            ) from exc
        contract = {
            "version": _TYPE_CASE_OWNERSHIP_VERSION,
            "certified": True,
            "policy_version": placement_policy.POLICY_VERSION,
            "teaching_order_sha256": order.sha256,
            "source_contract_hash": str(graph.get("source_contract_hash") or ""),
            "claim_id": claim_id,
            "qid": str(claim_row.get("qid") or ""),
            "normalized_claim": str(claim_row.get("normalized_claim") or ""),
            "source_location_topic_id": claim.source_location_topic_id,
            "source_location_topic_ids": [claim.source_location_topic_id],
            "owner_topic_id": decision.owner_topic_id,
            "owner_topic_title": topic_title.get(decision.owner_topic_id, ""),
            "required_topic_ids": list(decision.required_topic_ids),
            "prerequisite_topic_ids": list(decision.prerequisite_topic_ids),
            "reference_edges": [list(edge) for edge in decision.reference_edges],
            "illustration_topic_ids": list(
                decision.illustration_topic_ids
            ),
            "illustration_topic_ids": list(decision.illustration_topic_ids),
            "topic_relationships": [
                placement_policy.relationship_audit_row(relation)
                for relation in reviewed
            ],
        }
        contract["placement_certificate_sha256"] = (
            generation._type_case_placement_digest(contract)
        )
        contracts[str(claim_row.get("qid") or "")] = contract
    return contracts


def _type_case_ledger_material(
    *,
    graph: dict[str, Any],
    order: placement_policy.TeachingOrder,
    contracts: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    return {
        "version": _TYPE_CASE_OWNERSHIP_VERSION,
        "certified": True,
        "policy_version": placement_policy.POLICY_VERSION,
        "source_contract_hash": str(graph.get("source_contract_hash") or ""),
        "teaching_order_sha256": order.sha256,
        "placements": {
            qid: copy.deepcopy(contracts[qid]) for qid in sorted(contracts)
        },
    }


def _reask_type_case_batch(
    generation: Any,
    *,
    claims: list[dict[str, Any]],
    graph: dict[str, Any],
    canonical: dict[str, Any],
    order: placement_policy.TeachingOrder,
    block_directory: dict[str, dict[str, Any]],
    provider: PlacementProvider,
    critic: PlacementCritic,
    contracts: dict[str, dict[str, Any]],
    depth: int = 0,
) -> list[str]:
    """Certify ``claims``, re-asking the model for whatever it did not answer.

    Ownership is a semantic judgement, so the provider/critic pair decides it
    -- always.  What used to end the run was treating one unusable batch
    response as final.  It is not: a batch fails as a *batch*, most often
    because one claim in it was hard or one qid came back reformatted, and
    the pair answers correctly when asked about that claim on its own.

    So a failure splits the batch and asks again, down to single claims,
    which is also what turns the original defect -- a provider collapsing
    ``QINV-0016.2`` into its parent -- into a second, narrower question
    rather than a lost task.

    Returns the claim IDs still unanswered after the bounded retries.
    """

    if not claims:
        return []
    try:
        fresh = _certify_type_case_batch(
            generation,
            claims=claims,
            graph=graph,
            canonical=canonical,
            order=order,
            block_directory=block_directory,
            provider=provider,
            critic=critic,
        )
    except HumanDecisionRequired:
        # A recoverable pause belongs to the recovery ladder above, which can
        # still answer it properly.
        raise
    except Exception as exc:
        fresh = {}
        failure = f"{type(exc).__name__}: {exc}"
    else:
        failure = ""

    answered = {
        str(claim.get("claim_id") or ""): claim
        for claim in claims
        if str(claim.get("qid") or "") in fresh
    }
    for claim in claims:
        qid = str(claim.get("qid") or "")
        if qid in fresh:
            contracts[qid] = fresh[qid]
    missing = [
        claim for claim in claims
        if str(claim.get("claim_id") or "") not in answered
    ]
    if not missing:
        return []

    if len(missing) == 1 and depth >= _max_type_case_reask_depth():
        claim_id = str(missing[0].get("claim_id") or "")
        progress.log(
            f"Phase 3.3 asked the placement pair about {claim_id} on its own "
            f"and it still returned no certifiable ownership ({failure or 'no contract'}).",
            level="warning",
        )
        return [claim_id]

    if len(missing) == 1:
        # One claim, asked alone, with a fresh attempt budget of its own.
        return _reask_type_case_batch(
            generation,
            claims=missing,
            graph=graph,
            canonical=canonical,
            order=order,
            block_directory=block_directory,
            provider=provider,
            critic=critic,
            contracts=contracts,
            depth=depth + 1,
        )

    progress.log(
        f"Phase 3.3 splitting {len(missing)} unanswered placement claim(s) "
        f"and asking the model again ({failure or 'partial response'}).",
        level="warning",
    )
    half = len(missing) // 2
    unanswered: list[str] = []
    for part in (missing[:half], missing[half:]):
        unanswered.extend(_reask_type_case_batch(
            generation,
            claims=part,
            graph=graph,
            canonical=canonical,
            order=order,
            block_directory=block_directory,
            provider=provider,
            critic=critic,
            contracts=contracts,
            depth=depth + 1,
        ))
    return unanswered


def _max_type_case_reask_depth() -> int:
    try:
        return max(0, int(
            os.getenv("AEGIS_TYPE_CASE_PLACEMENT_REASK_DEPTH", "2")
        ))
    except (TypeError, ValueError):
        return 2


def _unplaced_type_case_contract(
    generation: Any,
    *,
    claim_row: dict[str, Any],
    graph: dict[str, Any],
    order: placement_policy.TeachingOrder,
) -> dict[str, Any]:
    """Record a task the model would not place, without inventing an owner.

    Reached only when the provider/critic pair has been asked about this one
    claim on its own and still produced nothing certifiable -- in practice,
    when the API itself is unusable.  Deterministic code must not answer the
    question in its place: guessing an owner from wording would be exactly
    the lexical shortcut this stage exists to remove.

    So the task keeps the topic it is *printed* under, and the contract says
    plainly that this is provenance rather than ownership. The run continues
    and the log names every task in this state.
    """

    located = str(claim_row.get("source_location_topic_id") or "")
    topic_title = {
        str(row.get("topic_id") or ""): str(row.get("title") or "")
        for row in graph.get("topics") or [] if isinstance(row, dict)
    }
    contract = {
        "version": _TYPE_CASE_OWNERSHIP_VERSION,
        "certified": False,
        "basis": placement_policy.UNPLACED_BASIS,
        "policy_version": placement_policy.POLICY_VERSION,
        "teaching_order_sha256": order.sha256,
        "source_contract_hash": str(graph.get("source_contract_hash") or ""),
        "claim_id": str(claim_row.get("claim_id") or ""),
        "qid": str(claim_row.get("qid") or ""),
        "normalized_claim": str(claim_row.get("normalized_claim") or ""),
        "source_location_topic_id": located,
        "source_location_topic_ids": [located],
        "owner_topic_id": located,
        "owner_topic_title": topic_title.get(located, ""),
        "required_topic_ids": [located] if located else [],
        "prerequisite_topic_ids": [],
        "reference_edges": [],
        "illustration_topic_ids": [],
        "topic_relationships": [],
        "rationale": (
            "the placement provider/critic pair returned no certifiable "
            "ownership for this task even when asked about it alone; it "
            "keeps the topic it is printed under, which is provenance and "
            "not a placement decision"
        ),
    }
    contract["placement_certificate_sha256"] = (
        generation._type_case_placement_digest(contract)
    )
    return contract


def _record_unplaced_type_case_claims(
    generation: Any,
    *,
    claim_by_qid: dict[str, dict[str, Any]],
    contracts: dict[str, dict[str, Any]],
    graph: dict[str, Any],
    order: placement_policy.TeachingOrder,
) -> list[str]:
    """Keep every QID the model would not place. Never raises."""

    missing = sorted(set(claim_by_qid) - set(contracts))
    if not missing:
        return []
    for qid in missing:
        contracts[qid] = _unplaced_type_case_contract(
            generation,
            claim_row=claim_by_qid[qid],
            graph=graph,
            order=order,
        )
    progress.log(
        f"Phase 3.3 could not obtain certified ownership for {len(missing)} "
        f"QID/task(s) after per-claim re-asks: {', '.join(missing[:8])}"
        + (", ..." if len(missing) > 8 else "")
        + ". They keep their printed topic, are recorded as unplaced, and no "
        "owner was invented for them.",
        level="error",
    )
    return missing


def _project_type_case_contracts(
    generation: Any,
    *,
    mined_types: dict[str, Any],
    inventory: dict[str, Any],
    rows_by_qid: dict[str, dict[str, Any]],
    contracts: dict[str, dict[str, Any]],
    graph: dict[str, Any],
    order: placement_policy.TeachingOrder,
) -> None:
    if set(rows_by_qid) != set(contracts):
        missing = sorted(set(rows_by_qid) - set(contracts))
        raise RuntimeError(
            "type_case_owner_uncertified: no certified placement for QID(s) "
            + ", ".join(missing)
        )
    for qid, item in rows_by_qid.items():
        item["_type_case_placement_contract"] = copy.deepcopy(contracts[qid])
        item["owner_topic_id"] = str(
            contracts[qid].get("owner_topic_id") or ""
        )
        item["owner_topic_title"] = str(
            contracts[qid].get("owner_topic_title") or ""
        )
    ledger = _type_case_ledger_material(
        graph=graph, order=order, contracts=contracts
    )
    ledger["certificate_sha256"] = phase3._sha256_json(ledger)
    inventory[_TYPE_CASE_QID_PLACEMENT_LEDGER_KEY] = ledger

    routed = generation._annotate_mined_type_case_routes(
        list((mined_types or {}).get("types") or []), inventory
    )
    annotated = phase3.annotate_mined_types(
        {**copy.deepcopy(mined_types), "types": routed}, graph
    )
    mined_types.clear()
    mined_types.update(annotated)
    mined_types[_TYPE_CASE_QID_PLACEMENT_LEDGER_KEY] = copy.deepcopy(ledger)


def _certify_type_case_ownership(
    generation: Any,
    *,
    mined_types: dict[str, Any],
    inventory: dict[str, Any] | None,
    units: list[dict[str, Any]],
    graph: dict[str, Any],
    canonical: dict[str, Any],
    provider: PlacementProvider | None = None,
    critic: PlacementCritic | None = None,
) -> bool:
    if not isinstance(inventory, dict) or not (inventory.get("items") or []):
        return False
    claims, rows_by_qid = _type_case_claim_rows(
        generation, units=units, inventory=inventory, graph=graph
    )
    if not claims:
        return False
    order = placement_policy.seal_teaching_order(
        graph,
        source_contract_hash=str(graph.get("source_contract_hash") or ""),
    )
    block_directory = phase32._exact_block_directory(graph, canonical)

    claim_by_qid = {
        str(row.get("qid") or ""): row for row in claims
    }
    contracts: dict[str, dict[str, Any]] = {}
    inventory_ledger = inventory.get(_TYPE_CASE_QID_PLACEMENT_LEDGER_KEY)
    mined_ledger = mined_types.get(_TYPE_CASE_QID_PLACEMENT_LEDGER_KEY)
    if (
        isinstance(inventory_ledger, dict)
        and isinstance(mined_ledger, dict)
        and phase3._sha256_json(inventory_ledger)
        != phase3._sha256_json(mined_ledger)
    ):
        # Two saved ledgers that disagree cannot both be right, and picking
        # one would be arbitrary. Distrust both and re-derive instead.
        progress.log(
            "Phase 3.3 found disagreeing inventory and mined-Type placement "
            "ledgers; both were discarded and ownership is being "
            "re-established rather than guessed.",
            level="warning",
        )
        inventory_ledger = None
        mined_ledger = None
    ledger = (
        inventory_ledger
        if isinstance(inventory_ledger, dict)
        else mined_ledger
    )
    if isinstance(ledger, dict):
        stored = dict(ledger)
        stored_digest = str(stored.pop("certificate_sha256", "") or "")
        if stored_digest == phase3._sha256_json(stored):
            placements = stored.get("placements")
            if isinstance(placements, dict):
                for qid, claim in claim_by_qid.items():
                    candidate = placements.get(qid)
                    if _type_case_contract_is_current(
                        generation,
                        candidate,
                        claim=claim,
                        graph=graph,
                        order=order,
                        block_directory=block_directory,
                    ):
                        contracts[qid] = copy.deepcopy(candidate)
        if set(contracts) == set(claim_by_qid):
            _project_type_case_contracts(
                generation,
                mined_types=mined_types,
                inventory=inventory,
                rows_by_qid=rows_by_qid,
                contracts=contracts,
                graph=graph,
                order=order,
            )
            return True
        contracts.clear()

    provider = provider or (
        _type_case_placement_via_openai
        if phase3.semantic_api_enabled() else None
    )
    critic = critic or (
        _type_case_placement_critic_via_openai
        if phase3.semantic_api_enabled() else None
    )
    if provider is None and critic is None:
        return False
    if provider is None or critic is None:
        # Half a certification pair certifies nothing, and running the
        # provider without its critic would be worse than running neither.
        _record_unplaced_type_case_claims(
            generation,
            claim_by_qid=claim_by_qid,
            contracts=contracts,
            graph=graph,
            order=order,
        )
        _project_type_case_contracts(
            generation,
            mined_types=mined_types,
            inventory=inventory,
            rows_by_qid=rows_by_qid,
            contracts=contracts,
            graph=graph,
            order=order,
        )
        return True

    cache = _read_type_case_placement_cache(graph=graph, order=order)
    entries = cache.setdefault("entries", {})
    pending: list[dict[str, Any]] = []
    for claim in claims:
        qid = str(claim.get("qid") or "")
        key = _type_case_placement_cache_key(
            claim=claim, graph=graph, order=order
        )
        candidate = entries.get(key)
        if _type_case_contract_is_current(
            generation,
            candidate,
            claim=claim,
            graph=graph,
            order=order,
            block_directory=block_directory,
        ):
            contracts[qid] = copy.deepcopy(candidate)
        else:
            pending.append(claim)

    size = _type_case_placement_batch_size()
    unanswered: list[str] = []
    for start in range(0, len(pending), size):
        batch = pending[start : start + size]
        unanswered.extend(_reask_type_case_batch(
            generation,
            claims=batch,
            graph=graph,
            canonical=canonical,
            order=order,
            block_directory=block_directory,
            provider=provider,
            critic=critic,
            contracts=contracts,
        ))
        for claim in batch:
            qid = str(claim.get("qid") or "")
            certified = contracts.get(qid)
            if certified is None:
                continue
            key = _type_case_placement_cache_key(
                claim=claim, graph=graph, order=order
            )
            entries[key] = copy.deepcopy(certified)
        _write_type_case_placement_cache(
            cache, graph=graph, order=order
        )

    _record_unplaced_type_case_claims(
        generation,
        claim_by_qid=claim_by_qid,
        contracts=contracts,
        graph=graph,
        order=order,
    )

    _project_type_case_contracts(
        generation,
        mined_types=mined_types,
        inventory=inventory,
        rows_by_qid=rows_by_qid,
        contracts=contracts,
        graph=graph,
        order=order,
    )
    progress.log(
        "Phase 3.3 independently certified semantic topic ownership for "
        f"{len(contracts)} QID/task(s); physical source location remains "
        "immutable provenance.",
        level="success",
    )
    return True


def _normal_concept_payloads(
    records: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int], dict[str, list[str]]]:
    payload: list[dict[str, Any]] = []
    index_by_id: dict[str, int] = {}
    cids_by_topic: dict[str, list[str]] = {}
    for index, record in enumerate(records):
        title = str(record.get("concept_title") or record.get("concept") or "")
        if cr.is_culmination(title):
            continue
        concept_id = f"HOST-CONCEPT-{index + 1:04d}"
        topic_id = str(record.get("_semantic_topic_id") or "")
        index_by_id[concept_id] = index
        cids_by_topic.setdefault(topic_id, []).append(concept_id)
        payload.append(
            {
                "concept_id": concept_id,
                "topic_id": topic_id,
                "topic": str(record.get("topic") or ""),
                "parent_concept": str(record.get("parent_concept") or ""),
                "concept_title": title,
                "source_claim": phase31._description_source_claim(record),
                "source_block_ids": [
                    str(value)
                    for value in record.get("_source_block_ids") or []
                    if str(value)
                ],
                "_placement_contract": copy.deepcopy(
                    record.get("_placement_contract") or {}
                ),
            }
        )
    return payload, index_by_id, cids_by_topic


def _source_block_page_number(
    graph_block: Mapping[str, Any],
    canonical_block: Mapping[str, Any],
) -> int | str | None:
    """Preserve available audit-page provenance in the human evidence packet."""

    fields = (
        "page_number",
        "pdf_page",
        "page",
        "page_number_for_audit",
        "pdf_page_number_for_audit",
        "pdf_page_number_for_audit_only",
    )
    for row in (graph_block, canonical_block):
        for field in fields:
            value = row.get(field)
            if value not in (None, ""):
                return value
        for field in ("_source_page_numbers", "source_page_numbers"):
            values = [
                value for value in row.get(field) or [] if value not in (None, "")
            ]
            if len(values) == 1:
                return values[0]
    return None


def _topic_source_blocks(
    graph: dict[str, Any],
    canonical: dict[str, Any],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, str]]:
    canonical_blocks = {
        str(row.get("block_id") or ""): row
        for row in canonical.get("blocks") or []
        if isinstance(row, dict)
    }
    blocks_by_topic: dict[str, list[dict[str, Any]]] = {}
    subtopic_by_block: dict[str, str] = {}
    for block in graph.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        if str(block.get("kind") or "") in {"layout", "heading", "navigation"}:
            continue
        block_id = str(block.get("block_id") or "")
        topic_id = str(block.get("topic_id") or "")
        source = canonical_blocks.get(block_id, {})
        text = phase3._clean_public_text(phase3._graph_block_text(block, source))
        if not block_id or not topic_id or not text:
            continue
        subtopic = str(block.get("subtopic_id") or "")
        subtopic_by_block[block_id] = subtopic
        page_number = _source_block_page_number(block, source)
        blocks_by_topic.setdefault(topic_id, []).append(
            {
                "block_id": block_id,
                "kind": str(block.get("kind") or ""),
                "subtopic_id": subtopic,
                "page_number": page_number,
                "text": text[:2600],
            }
        )
    return blocks_by_topic, subtopic_by_block


def _host_schema(
    unit_ids: list[str],
    existing_concept_ids: list[str],
    new_keys: list[str],
    topic_ids: list[str],
    block_ids: list[str],
) -> dict[str, Any]:
    relationship_items = {
        "type": "object",
        "properties": {
            "topic_id": {"type": "string", "enum": topic_ids},
            "relationship_type": {
                "type": "string",
                "enum": [
                    kind.value for kind in placement_policy.RelationshipType
                ],
            },
            "necessity": {"type": "boolean"},
            "evidence_block_ids": {
                "type": "array",
                "minItems": 1,
                "items": {"type": "string", "enum": block_ids},
            },
            "reason": {"type": "string", "minLength": 1},
        },
        "required": [
            "topic_id",
            "relationship_type",
            "necessity",
            "evidence_block_ids",
            "reason",
        ],
        "additionalProperties": False,
    }
    return {
        "name": "phase33_type_host_preflight",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "assignments": {
                    "type": "array",
                    "minItems": len(unit_ids),
                    "maxItems": len(unit_ids),
                    "items": {
                        "type": "object",
                        "properties": {
                            "assignment_unit_id": {
                                "type": "string",
                                "enum": unit_ids,
                            },
                            "decision": {
                                "type": "string",
                                "enum": [
                                    "existing",
                                    "expand_existing",
                                    "create_new",
                                    "review_required",
                                ],
                            },
                            "existing_concept_id": {
                                "type": "string",
                                "enum": ["NONE", *existing_concept_ids],
                            },
                            "new_concept_key": {
                                "type": "string",
                                "enum": ["NONE", *new_keys],
                            },
                            "confidence": {
                                "type": "number",
                                "minimum": 0,
                                "maximum": 1,
                            },
                            "reason": {"type": "string"},
                        },
                        "required": [
                            "assignment_unit_id",
                            "decision",
                            "existing_concept_id",
                            "new_concept_key",
                            "confidence",
                            "reason",
                        ],
                        "additionalProperties": False,
                    },
                },
                "new_concepts": {
                    "type": "array",
                    "maxItems": len(unit_ids),
                    "items": {
                        "type": "object",
                        "properties": {
                            "new_concept_key": {
                                "type": "string",
                                "enum": new_keys,
                            },
                            "topic_id": {"type": "string", "enum": topic_ids},
                            "concept_title": {"type": "string"},
                            "parent_concept": {"type": "string"},
                            "description": {"type": "string"},
                            "achieving_mastery": {"type": "string"},
                            "keywords": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "source_block_ids": {
                                "type": "array",
                                "minItems": 1,
                                "items": {"type": "string", "enum": block_ids},
                            },
                            "topic_relationships": {
                                "type": "array",
                                "minItems": 1,
                                "items": copy.deepcopy(relationship_items),
                            },
                            "assignment_unit_ids": {
                                "type": "array",
                                "minItems": 1,
                                "items": {"type": "string", "enum": unit_ids},
                            },
                            "confidence": {
                                "type": "number",
                                "minimum": 0,
                                "maximum": 1,
                            },
                            "reason": {"type": "string"},
                        },
                        "required": [
                            "new_concept_key",
                            "topic_id",
                            "concept_title",
                            "parent_concept",
                            "description",
                            "achieving_mastery",
                            "keywords",
                            "source_block_ids",
                            "topic_relationships",
                            "assignment_unit_ids",
                            "confidence",
                            "reason",
                        ],
                        "additionalProperties": False,
                    },
                },
                "existing_concept_updates": {
                    "type": "array",
                    "maxItems": len(unit_ids),
                    "items": {
                        "type": "object",
                        "properties": {
                            "existing_concept_id": {
                                "type": "string",
                                "enum": ["NONE", *existing_concept_ids],
                            },
                            "topic_id": {"type": "string", "enum": topic_ids},
                            "description": {"type": "string"},
                            "achieving_mastery": {"type": "string"},
                            "keywords": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "source_block_ids": {
                                "type": "array",
                                "minItems": 1,
                                "items": {"type": "string", "enum": block_ids},
                            },
                            "topic_relationships": {
                                "type": "array",
                                "minItems": 1,
                                "items": copy.deepcopy(relationship_items),
                            },
                            "assignment_unit_ids": {
                                "type": "array",
                                "minItems": 1,
                                "items": {"type": "string", "enum": unit_ids},
                            },
                            "confidence": {
                                "type": "number",
                                "minimum": 0,
                                "maximum": 1,
                            },
                            "reason": {"type": "string"},
                        },
                        "required": [
                            "existing_concept_id",
                            "topic_id",
                            "description",
                            "achieving_mastery",
                            "keywords",
                            "source_block_ids",
                            "topic_relationships",
                            "assignment_unit_ids",
                            "confidence",
                            "reason",
                        ],
                        "additionalProperties": False,
                    },
                },
            },
            "required": [
                "assignments",
                "new_concepts",
                "existing_concept_updates",
            ],
            "additionalProperties": False,
        },
    }


def _host_critic_schema(
    unit_ids: list[str],
    relationship_ids: list[str] | None = None,
) -> dict[str, Any]:
    relationship_ids = list(dict.fromkeys(relationship_ids or []))
    return {
        "name": "phase33_type_host_preflight_critic",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "verdict": {"type": "string", "enum": ["verified", "rejected"]},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "accepted_concept_ids": {
                    "type": "array",
                    "items": {"type": "string", "enum": unit_ids},
                },
                "rejected_concept_ids": {
                    "type": "array",
                    "items": {"type": "string", "enum": unit_ids},
                },
                "issues": {"type": "array", "items": {"type": "string"}},
                "relationship_reviews": {
                    "type": "array",
                    "minItems": len(relationship_ids),
                    "maxItems": len(relationship_ids),
                    "items": {
                        "type": "object",
                        "properties": {
                            "relationship_id": {
                                "type": "string",
                                "enum": relationship_ids,
                            },
                            "verdict": {
                                "type": "string",
                                "enum": ["accepted", "rejected"],
                            },
                            "evidence_supported": {"type": "boolean"},
                            "necessity_supported": {"type": "boolean"},
                            "direction_supported": {"type": "boolean"},
                            "reason": {"type": "string"},
                        },
                        "required": [
                            "relationship_id",
                            "verdict",
                            "evidence_supported",
                            "necessity_supported",
                            "direction_supported",
                            "reason",
                        ],
                        "additionalProperties": False,
                    },
                },
            },
            "required": [
                "verdict",
                "confidence",
                "accepted_concept_ids",
                "rejected_concept_ids",
                "issues",
                "relationship_reviews",
            ],
            "additionalProperties": False,
        },
    }


def _host_plan_via_openai(payload: dict[str, Any]) -> dict[str, Any]:
    unit_ids = [str(row.get("assignment_unit_id") or "") for row in payload.get("assignment_units") or []]
    existing_ids = [str(row.get("concept_id") or "") for row in payload.get("existing_concepts") or []]
    new_keys = [f"NEW-HOST-{index:04d}" for index in range(1, len(unit_ids) + 1)]
    topic_ids = sorted({
        str(row.get("topic_id") or "")
        for row in (
            payload.get("topics")
            or payload.get("assignment_units")
            or []
        )
        if isinstance(row, dict) and str(row.get("topic_id") or "")
    })
    block_ids = [str(row.get("block_id") or "") for row in payload.get("source_blocks") or []]
    system = (
        "You are the Aegis Phase 3.3 Type-host preflight resolver. Every supplied "
        "assignment unit is source-owned and must have one durable normal concept "
        "host before topology freeze. Choose decision=existing when an existing "
        "source-grounded concept naturally teaches and assesses the unit without "
        "semantic distortion. Choose decision=expand_existing when the durable "
        "concept identity is correct but its source-facing Description must be "
        "expanded to cover another idea explicitly supported by the supplied "
        "same-topic source blocks. Prefer this over a near-duplicate new concept "
        "when the existing concept already owns the durable idea. Emit one "
        "existing_concept_updates entry for "
        "that concept; preserve its title and parent, cite only supplied block "
        "IDs, and do not add knowledge absent from those blocks. Choose "
        "decision=create_new only when the unit exposes "
        "a distinct, reusable, source-supported teaching objective absent from all "
        "existing concepts. Low confidence, a narrow example, different wording, "
        "or a new question variation alone never justifies a new concept. Group "
        "units behind one new_concept_key when they share the same durable idea. "
        "New concepts must be grade-appropriate, non-duplicative, not question- or "
        "person-shaped, and grounded only to supplied source_block_ids in the same "
        "topic. Achieving Mastery must be observable. Return every assignment unit "
        "exactly once. Return existing_concept_updates as an empty array when no "
        "existing concept is expanded. Use review_required when no safe "
        "source-supported plan exists. If human_resolution is present, follow "
        "that directed choice exactly while still remaining source-grounded. "
        "Never substitute a different semantic choice. On mechanical correction "
        "retries, repair only the response contract and do not invent broader "
        "content. For every expand_existing update and every create_new concept, "
        "classify the complete resulting concept claim with concept-level "
        "topic_relationships. Use the placement-policy relationship enum exactly, "
        "cite exact supplied blocks for every relationship, and include all topics "
        "whose knowledge, prerequisite, reference, illustration, or later method "
        "actually relates to the claim. Do not decide ownership: deterministic "
        "code will compute the owner from the sealed teaching order."
    )
    return phase3.phase22._openai_multimodal_json(
        system=system,
        prompt=json.dumps(payload, ensure_ascii=False, indent=2),
        pages=[],
        response_schema=_host_schema(
            unit_ids,
            existing_ids,
            new_keys,
            topic_ids,
            block_ids,
        ),
        purpose="concept_mapping",
        max_tokens=max(6000, min(28000, len(unit_ids) * 1000)),
        single_attempt=bool(
            payload.get("human_resolution")
            or payload.get("human_resolutions")
        ),
    )


def _host_critic_via_openai(payload: dict[str, Any]) -> dict[str, Any]:
    unit_ids = [str(row.get("assignment_unit_id") or "") for row in payload.get("assignment_units") or []]
    relationship_ids = [
        str(row.get("relationship_id") or "")
        for row in payload.get("proposed_relationships") or []
        if isinstance(row, dict) and str(row.get("relationship_id") or "")
    ]
    system = (
        "You are the independent Aegis Phase 3.3 Type-host critic. Verify every "
        "assignment unit has exactly one semantically valid normal concept host. "
        "For existing hosts, require genuine entailment between the authoritative "
        "source task, Type/Case method, and the concept's source-facing claim. For "
        "new concepts, verify necessity, source-block support, durable teachability, "
        "grade appropriateness, non-duplication, and exact unit coverage. Reject a "
        "directed expansion unless it preserves the existing concept identity, "
        "adds only claims entailed by its cited same-topic source blocks, and "
        "genuinely covers the assigned unit without over-merging. Reject a "
        "new concept created merely because confidence was low or because an example "
        "uses different wording. Reject over-merged hosts, micro-concepts that are "
        "only one question variation, wrong-topic plans, unused new concepts, or "
        "units assigned more than once. When human_resolution or "
        "human_resolutions are present, independently verify that every directed "
        "choice and custom instruction was followed exactly. Reject an ignored or "
        "substituted direction, but never let a human instruction override the "
        "authoritative source, topic, QID, or Figure constraints. Put every "
        "assignment_unit_id in exactly one "
        "accepted_concept_ids or rejected_concept_ids list. Verified requires all "
        "accepted, none rejected, confidence at least "
        f"{confidence_policy.threshold_text()}, and no issues. Independently "
        "review every proposed_relationship relationship_id exactly once. Accept "
        "one only when its exact cited blocks support the relationship, its "
        "necessity flag is justified, and its dependency direction is correct."
    )
    return phase3.phase22._openai_multimodal_json(
        system=system,
        prompt=json.dumps(payload, ensure_ascii=False, indent=2),
        pages=[],
        response_schema=_host_critic_schema(unit_ids, relationship_ids),
        purpose="concept_mapping",
        max_tokens=max(4000, min(12000, len(unit_ids) * 320)),
        single_attempt=bool(
            payload.get("human_resolution")
            or payload.get("human_resolutions")
        ),
    )


def _parse_host_plan(
    response: dict[str, Any],
    *,
    unit_payloads: list[dict[str, Any]],
    existing_concepts: list[dict[str, Any]],
    source_blocks: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, list[str]]:
    unit_ids = {str(row.get("assignment_unit_id") or "") for row in unit_payloads}
    unit_topic = {
        str(row.get("assignment_unit_id") or ""): str(row.get("topic_id") or "")
        for row in unit_payloads
    }
    existing_by_id = {
        str(row.get("concept_id") or ""): row for row in existing_concepts
    }
    block_topic = {
        str(row.get("block_id") or ""): str(row.get("topic_id") or "")
        for row in source_blocks
    }
    assignments = response.get("assignments") if isinstance(response, dict) else None
    new_rows = response.get("new_concepts") if isinstance(response, dict) else None
    update_rows = (
        response.get("existing_concept_updates", [])
        if isinstance(response, dict)
        else None
    )
    if not isinstance(assignments, list) or not isinstance(new_rows, list):
        return None, ["host resolver returned no assignments/new_concepts arrays"]
    if not isinstance(update_rows, list):
        return None, ["host resolver returned a non-array existing_concept_updates"]
    errors: list[str] = []
    assignment_by_id: dict[str, dict[str, Any]] = {}
    for raw in assignments:
        if not isinstance(raw, dict):
            errors.append("host resolver returned a non-object assignment")
            continue
        unit_id = str(raw.get("assignment_unit_id") or "")
        if unit_id not in unit_ids:
            errors.append(f"unknown assignment unit ID {unit_id or '<empty>'}")
            continue
        if unit_id in assignment_by_id:
            errors.append(f"duplicate assignment unit ID {unit_id}")
            continue
        decision = str(raw.get("decision") or "")
        existing_id = str(raw.get("existing_concept_id") or "")
        new_key = str(raw.get("new_concept_key") or "")
        confidence = float(raw.get("confidence") or 0.0)
        if not confidence_policy.accepts(confidence):
            errors.append(
                f"{unit_id} host confidence {confidence:.3f} is below "
                f"{confidence_policy.threshold_text()}"
            )
            continue
        if decision == "review_required":
            errors.append(
                f"{unit_id} requires review: {str(raw.get('reason') or '')[:500]}"
            )
            continue
        if decision == "existing":
            concept = existing_by_id.get(existing_id)
            if concept is None or new_key != "NONE":
                errors.append(f"{unit_id} returned an invalid existing-host decision")
                continue
            if str(concept.get("topic_id") or "") != unit_topic[unit_id]:
                errors.append(f"{unit_id} selected an existing concept from another topic")
                continue
        elif decision == "expand_existing":
            concept = existing_by_id.get(existing_id)
            if concept is None or new_key != "NONE":
                errors.append(
                    f"{unit_id} returned an invalid expand-existing decision"
                )
                continue
            if str(concept.get("topic_id") or "") != unit_topic[unit_id]:
                errors.append(
                    f"{unit_id} selected an expandable concept from another topic"
                )
                continue
        elif decision == "create_new":
            if existing_id != "NONE" or not new_key or new_key == "NONE":
                errors.append(f"{unit_id} returned an invalid create-new decision")
                continue
        else:
            errors.append(f"{unit_id} returned invalid decision {decision or '<empty>'}")
            continue
        assignment_by_id[unit_id] = {
            "assignment_unit_id": unit_id,
            "decision": decision,
            "existing_concept_id": existing_id,
            "new_concept_key": new_key,
            "confidence": confidence,
            "reason": str(raw.get("reason") or ""),
        }
    missing = sorted(unit_ids - set(assignment_by_id))
    if missing:
        errors.append("missing or invalid assignment unit ID(s): " + ", ".join(missing))

    referenced_new: dict[str, set[str]] = {}
    referenced_updates: dict[str, set[str]] = {}
    for unit_id, assignment in assignment_by_id.items():
        if assignment["decision"] == "create_new":
            referenced_new.setdefault(assignment["new_concept_key"], set()).add(unit_id)
        elif assignment["decision"] == "expand_existing":
            referenced_updates.setdefault(
                assignment["existing_concept_id"], set()
            ).add(unit_id)

    existing_title_keys = {
        _normal(row.get("concept_title")) for row in existing_concepts
        if _normal(row.get("concept_title"))
    }
    definitions: dict[str, dict[str, Any]] = {}
    new_title_keys: set[str] = set()
    for raw in new_rows:
        if not isinstance(raw, dict):
            errors.append("host resolver returned a non-object new concept")
            continue
        key = str(raw.get("new_concept_key") or "")
        if key not in referenced_new:
            errors.append(f"unused or unknown new concept key {key or '<empty>'}")
            continue
        if key in definitions:
            errors.append(f"duplicate new concept key {key}")
            continue
        topic_id = str(raw.get("topic_id") or "")
        assigned = {
            str(value) for value in raw.get("assignment_unit_ids") or [] if str(value)
        }
        title = phase3._clean_public_text(raw.get("concept_title") or "")
        parent = phase3._clean_public_text(raw.get("parent_concept") or "")
        description = phase3._clean_public_text(raw.get("description") or "")
        mastery = phase3._clean_public_text(raw.get("achieving_mastery") or "")
        confidence = float(raw.get("confidence") or 0.0)
        block_ids = [
            str(value) for value in raw.get("source_block_ids") or [] if str(value)
        ]
        if assigned != referenced_new[key]:
            errors.append(f"{key} assignment_unit_ids do not match referencing units")
            continue
        if not assigned or any(unit_topic[unit_id] != topic_id for unit_id in assigned):
            errors.append(f"{key} mixes assignment units from another topic")
            continue
        if not confidence_policy.accepts(confidence):
            errors.append(
                f"{key} concept confidence {confidence:.3f} is below "
                f"{confidence_policy.threshold_text()}"
            )
            continue
        if not title or not parent or not description or not mastery or not block_ids:
            errors.append(f"{key} omitted title, parent, Description, mastery, or source blocks")
            continue
        if title.endswith("?") or re.match(
            r"^(?:what|why|how|who|when|where|which)\b", title, re.IGNORECASE
        ):
            errors.append(f"{key} returned a question-shaped concept title")
            continue
        if cr.is_culmination(title):
            errors.append(f"{key} returned a culmination-like concept")
            continue
        title_key = _normal(title)
        if title_key in existing_title_keys or title_key in new_title_keys:
            errors.append(f"{key} duplicates an existing or proposed concept title")
            continue
        if any(block_topic.get(block_id) != topic_id for block_id in block_ids):
            errors.append(f"{key} used unknown or wrong-topic source block IDs")
            continue
        new_title_keys.add(title_key)
        definitions[key] = {
            "new_concept_key": key,
            "topic_id": topic_id,
            "concept_title": title,
            "parent_concept": parent,
            "description": description,
            "achieving_mastery": mastery,
            "keywords": [
                phase3._clean_public_text(value)
                for value in raw.get("keywords") or []
                if phase3._clean_public_text(value)
            ],
            "source_block_ids": list(dict.fromkeys(block_ids)),
            "topic_relationships": copy.deepcopy(
                raw.get("topic_relationships") or []
            ),
            "assignment_unit_ids": sorted(assigned),
            "confidence": confidence,
            "reason": str(raw.get("reason") or ""),
        }
    absent_definitions = sorted(set(referenced_new) - set(definitions))
    if absent_definitions:
        errors.append(
            "create-new assignments lack definitions: " + ", ".join(absent_definitions)
        )

    updates: dict[str, dict[str, Any]] = {}
    for raw in update_rows:
        if not isinstance(raw, dict):
            errors.append("host resolver returned a non-object existing concept update")
            continue
        concept_id = str(raw.get("existing_concept_id") or "")
        concept = existing_by_id.get(concept_id)
        if concept_id not in referenced_updates or concept is None:
            errors.append(
                "unused or unknown existing concept update "
                f"{concept_id or '<empty>'}"
            )
            continue
        if concept_id in updates:
            errors.append(f"duplicate existing concept update {concept_id}")
            continue
        topic_id = str(raw.get("topic_id") or "")
        assigned = {
            str(value)
            for value in raw.get("assignment_unit_ids") or []
            if str(value)
        }
        description = phase3._clean_public_text(raw.get("description") or "")
        mastery = phase3._clean_public_text(raw.get("achieving_mastery") or "")
        confidence = float(raw.get("confidence") or 0.0)
        block_ids = [
            str(value)
            for value in raw.get("source_block_ids") or []
            if str(value)
        ]
        if assigned != referenced_updates[concept_id]:
            errors.append(
                f"{concept_id} assignment_unit_ids do not match expansion units"
            )
            continue
        if (
            not assigned
            or topic_id != str(concept.get("topic_id") or "")
            or any(unit_topic[unit_id] != topic_id for unit_id in assigned)
        ):
            errors.append(
                f"{concept_id} expansion mixes assignments from another topic"
            )
            continue
        if not confidence_policy.accepts(confidence):
            errors.append(
                f"{concept_id} expansion confidence {confidence:.3f} is below "
                f"{confidence_policy.threshold_text()}"
            )
            continue
        if not description or not mastery or not block_ids:
            errors.append(
                f"{concept_id} expansion omitted Description, mastery, or source blocks"
            )
            continue
        if any(block_topic.get(block_id) != topic_id for block_id in block_ids):
            errors.append(
                f"{concept_id} expansion used unknown or wrong-topic source block IDs"
            )
            continue
        updates[concept_id] = {
            "existing_concept_id": concept_id,
            "topic_id": topic_id,
            "description": description,
            "achieving_mastery": mastery,
            "keywords": [
                phase3._clean_public_text(value)
                for value in raw.get("keywords") or []
                if phase3._clean_public_text(value)
            ],
            "source_block_ids": list(dict.fromkeys(block_ids)),
            "topic_relationships": copy.deepcopy(
                raw.get("topic_relationships") or []
            ),
            "assignment_unit_ids": sorted(assigned),
            "confidence": confidence,
            "reason": str(raw.get("reason") or ""),
        }
    absent_updates = sorted(set(referenced_updates) - set(updates))
    if absent_updates:
        errors.append(
            "expand-existing assignments lack grounded updates: "
            + ", ".join(absent_updates)
        )
    if errors:
        return None, errors
    return {
        "assignments": [assignment_by_id[key] for key in sorted(assignment_by_id)],
        "new_concepts": [definitions[key] for key in sorted(definitions)],
        "existing_concept_updates": [
            updates[key] for key in sorted(updates)
        ],
    }, []


def _stable_new_host_claim_id(
    source_contract_hash: str,
    qids: list[str],
) -> str:
    """Return the immutable identity of one source-owned created host claim."""

    values = sorted({str(qid).strip() for qid in qids if str(qid).strip()})
    if not source_contract_hash or not values:
        raise ValueError(
            "Phase 3.3 cannot create a placement claim without a source "
            "contract and at least one protected QID"
        )
    digest = phase3._sha256_json({
        "version": "phase3.3-created-host-claim-1",
        "source_contract_hash": source_contract_hash,
        "qids": values,
    })
    return f"HOST-CLAIM-{digest[:24].upper()}"


def _host_plan_semantic_material(plan: Mapping[str, Any]) -> dict[str, Any]:
    """Return only the provider/critic/apply material sealed by the envelope."""

    return {
        "assignments": copy.deepcopy(plan.get("assignments") or []),
        "new_concepts": copy.deepcopy(plan.get("new_concepts") or []),
        "existing_concept_updates": copy.deepcopy(
            plan.get("existing_concept_updates") or []
        ),
    }


def _host_mutation_output_digests(
    plan: Mapping[str, Any],
) -> dict[str, str] | None:
    digests: dict[str, str] = {}
    rows = [
        ("create", "new_concept_key", row)
        for row in plan.get("new_concepts") or []
        if isinstance(row, Mapping)
    ] + [
        ("expand", "existing_concept_id", row)
        for row in plan.get("existing_concept_updates") or []
        if isinstance(row, Mapping)
    ]
    for kind, identity_field, row in rows:
        identity = str(row.get(identity_field) or "")
        contract = row.get("_placement_contract")
        if not identity or not isinstance(contract, Mapping):
            return None
        supplied = str(contract.get("placement_certificate_sha256") or "")
        expected = placement_policy.placement_contract_sha256(contract)
        if supplied != expected:
            return None
        digests[f"{kind}:{identity}"] = expected
    return dict(sorted(digests.items()))


def _host_mutation_authority(
    generation: Any,
    *,
    graph: dict[str, Any],
    order: placement_policy.TeachingOrder,
    inventory: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Validate and bind the exact per-QID authority used by host mutation."""

    ledger = generation._valid_type_case_qid_placement_ledger(
        inventory.get(_TYPE_CASE_QID_PLACEMENT_LEDGER_KEY)
    )
    if not isinstance(ledger, dict):
        raise RuntimeError(
            "type_case_owner_uncertified: Phase 3.3 host mutation has no "
            "current QID placement ledger"
        )
    placements = ledger.get("placements")
    if not isinstance(placements, dict) or not placements:
        raise RuntimeError(
            "type_case_owner_uncertified: Phase 3.3 host mutation QID "
            "placement ledger is empty"
        )
    contracts: dict[str, dict[str, Any]] = {}
    for raw_qid, raw_contract in placements.items():
        qid = str(raw_qid or "").strip()
        contract = generation._certified_type_case_placement_contract(
            raw_contract
        )
        if not qid or contract is None:
            raise RuntimeError(
                "type_case_owner_uncertified: Phase 3.3 host mutation found "
                f"an uncertified QID placement for {qid or '<empty>'}"
            )
        contracts[qid] = copy.deepcopy(contract)
    authority = {
        "version": _HOST_MUTATION_PLACEMENT_VERSION,
        "policy_version": placement_policy.POLICY_VERSION,
        "teaching_order_sha256": order.sha256,
        "source_contract_hash": str(graph.get("source_contract_hash") or ""),
        "semantic_topology_sha256": (
            grounding_certificate.semantic_topology_sha256(graph)
        ),
        "qid_ledger_certificate_sha256": str(
            ledger.get("certificate_sha256") or ""
        ),
        "qid_contract_sha256s": {
            qid: phase3._sha256_json(contracts[qid])
            for qid in sorted(contracts)
        },
        "qid_placement_sha256s": {
            qid: str(
                contracts[qid].get("placement_certificate_sha256") or ""
            )
            for qid in sorted(contracts)
        },
    }
    if any(not str(authority.get(field) or "") for field in (
        "policy_version",
        "teaching_order_sha256",
        "source_contract_hash",
        "semantic_topology_sha256",
        "qid_ledger_certificate_sha256",
    )):
        raise RuntimeError(
            "type_case_owner_uncertified: Phase 3.3 host mutation authority "
            "is incomplete"
        )
    return authority, contracts


def _existing_host_contract_is_current(
    concept: Mapping[str, Any],
    *,
    order: placement_policy.TeachingOrder,
    block_directory: Mapping[str, Mapping[str, Any]],
) -> bool:
    """Validate the old placement contract before an expansion may replace it."""

    raw = concept.get("_placement_contract")
    if not isinstance(raw, Mapping):
        return False
    if any((
        raw.get("certified") is not True,
        str(raw.get("policy_version") or "") != placement_policy.POLICY_VERSION,
        str(raw.get("teaching_order_sha256") or "") != order.sha256,
        str(raw.get("placement_certificate_sha256") or "")
        != placement_policy.placement_contract_sha256(raw),
        _normal(raw.get("normalized_claim"))
        != _normal(concept.get("source_claim")),
        str(raw.get("owner_topic_id") or "")
        != str(concept.get("topic_id") or ""),
    )):
        return False
    claim_id = str(raw.get("claim_id") or "")
    relationships = placement_policy.parse_relationships(
        {"relationships": [
            {
                **copy.deepcopy(row),
                "reason": str(
                    row.get("provider_reason") or row.get("reason") or ""
                ),
            }
            for row in raw.get("topic_relationships") or []
            if isinstance(row, Mapping)
        ]},
        known_claim_ids=[claim_id],
        known_topic_ids=order.ranks,
    )
    raw_relationships = [
        row for row in raw.get("topic_relationships") or []
        if isinstance(row, Mapping)
    ]
    if not claim_id or len(relationships) != len(raw_relationships):
        return False
    reviewed: list[placement_policy.TopicRelationship] = []
    for relation, relation_raw in zip(relationships, raw_relationships):
        if (
            not relation.evidence_block_ids
            or str(relation_raw.get("relationship_id") or "")
            != relation.relationship_id
            or any(
                not isinstance(block_directory.get(block_id), Mapping)
                or str(block_directory[block_id].get("topic_id") or "")
                != relation.topic_id
                for block_id in relation.evidence_block_ids
            )
        ):
            return False
        reviewed.append(placement_policy.TopicRelationship(
            claim_id=relation.claim_id,
            topic_id=relation.topic_id,
            relationship_type=relation.relationship_type,
            necessity=relation.necessity,
            evidence_block_ids=relation.evidence_block_ids,
            provider_reason=relation.provider_reason,
            critic_verdict=str(relation_raw.get("critic_verdict") or ""),
        ))
    claim = placement_policy.AtomicClaim(
        claim_id=claim_id,
        normalized_claim=str(raw.get("normalized_claim") or ""),
        source_location_topic_id=str(
            raw.get("source_location_topic_id") or ""
        ),
        origin_claim_ids=tuple(
            str(value) for value in raw.get("origin_claim_ids") or []
            if str(value)
        ),
        split_group_id=str(raw.get("split_group_id") or ""),
        protected_source_items=tuple(
            str(value) for value in raw.get("protected_source_items") or []
            if str(value)
        ),
    )
    try:
        decision = placement_policy.compute_placement(
            claim, reviewed, order
        )
    except placement_policy.PlacementPolicyError:
        return False
    return all((
        decision.owner_topic_id == str(raw.get("owner_topic_id") or ""),
        list(decision.required_topic_ids)
        == sorted({str(value) for value in raw.get("required_topic_ids") or []}),
        list(decision.prerequisite_topic_ids)
        == sorted({
            str(value) for value in raw.get("prerequisite_topic_ids") or []
        }),
        [list(edge) for edge in decision.reference_edges]
        == sorted([
            list(edge) for edge in raw.get("reference_edges") or []
            if isinstance(edge, (list, tuple)) and len(edge) == 2
        ]),
        list(decision.illustration_topic_ids)
        == sorted({
            str(value) for value in raw.get("illustration_topic_ids") or []
        }),
    ))


def _host_mutation_specs(
    plan: dict[str, Any],
    *,
    plan_topic_id: str,
    unit_payloads: list[dict[str, Any]],
    existing_concepts: list[dict[str, Any]],
    graph: dict[str, Any],
    order: placement_policy.TeachingOrder,
    block_directory: dict[str, dict[str, Any]],
    qid_contracts: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Parse provider relationships without granting them commit authority."""

    unit_by_id = {
        str(row.get("assignment_unit_id") or ""): row
        for row in unit_payloads
        if isinstance(row, dict)
    }
    concept_by_id = {
        str(row.get("concept_id") or ""): row
        for row in existing_concepts
        if isinstance(row, dict)
    }
    rows = [
        ("create", "new_concept_key", row)
        for row in plan.get("new_concepts") or []
        if isinstance(row, dict)
    ] + [
        ("expand", "existing_concept_id", row)
        for row in plan.get("existing_concept_updates") or []
        if isinstance(row, dict)
    ]
    specs: list[dict[str, Any]] = []
    errors: list[str] = []
    for kind, identity_field, row in rows:
        identity = str(row.get(identity_field) or "")
        unit_ids = sorted({
            str(value) for value in row.get("assignment_unit_ids") or []
            if str(value)
        })
        qids = sorted({
            str(qid).strip()
            for unit_id in unit_ids
            for qid in unit_by_id.get(unit_id, {}).get(
                "source_question_ids"
            ) or []
            if str(qid).strip()
        })
        missing_qids = [qid for qid in qids if qid not in qid_contracts]
        if not qids or missing_qids:
            errors.append(
                f"{kind}:{identity} has no complete certified QID authority"
                + (f"; missing {missing_qids}" if missing_qids else "")
            )
            continue
        qid_owners = {
            str(qid_contracts[qid].get("owner_topic_id") or "")
            for qid in qids
        }
        row_topic_id = str(row.get("topic_id") or "")
        if (
            len(qid_owners) != 1
            or "" in qid_owners
            or next(iter(qid_owners)) != plan_topic_id
            or row_topic_id != plan_topic_id
        ):
            errors.append(
                f"{kind}:{identity} owner conflict: QIDs={sorted(qid_owners)}, "
                f"plan={plan_topic_id!r}, row={row_topic_id!r}"
            )
            continue
        description = str(row.get("description") or "").strip()
        old_contract: dict[str, Any] | None = None
        if kind == "expand":
            concept = concept_by_id.get(identity)
            if concept is None or not _existing_host_contract_is_current(
                concept,
                order=order,
                block_directory=block_directory,
            ):
                errors.append(
                    f"expand:{identity} has no current certified old placement "
                    "contract"
                )
                continue
            old_contract = copy.deepcopy(concept["_placement_contract"])
            claim_id = str(old_contract.get("claim_id") or "")
            source_location_topic_id = str(
                old_contract.get("source_location_topic_id") or ""
            )
            origin_claim_sha256 = str(
                old_contract.get("origin_claim_sha256") or ""
            )
            origin_claim_ids = tuple(
                str(value)
                for value in old_contract.get("origin_claim_ids") or []
                if str(value)
            )
            split_group_id = str(old_contract.get("split_group_id") or "")
            protected = tuple(sorted({
                *(
                    str(value)
                    for value in old_contract.get("protected_source_items") or []
                    if str(value)
                ),
                *qids,
            }))
        else:
            claim_id = _stable_new_host_claim_id(
                str(graph.get("source_contract_hash") or ""), qids
            )
            source_location_topic_id = row_topic_id
            origin_claim_sha256 = placement_policy.claim_text_sha256(
                description
            )
            origin_claim_ids = (claim_id,)
            split_group_id = ""
            protected = tuple(qids)
        raw_relationships = [
            value for value in row.get("topic_relationships") or []
            if isinstance(value, dict)
        ]
        relationships = placement_policy.parse_relationships(
            {"relationships": [
                {**copy.deepcopy(value), "claim_id": claim_id}
                for value in raw_relationships
            ]},
            known_claim_ids=[claim_id],
            known_topic_ids=order.ranks,
        )
        if not raw_relationships or len(relationships) != len(raw_relationships):
            errors.append(
                f"{kind}:{identity} omitted or malformed topic_relationships"
            )
            continue
        seen_relationship_ids: set[str] = set()
        invalid = False
        for relation in relationships:
            expected_necessity = (
                relation.relationship_type in placement_policy.NECESSARY_TYPES
            )
            if relation.relationship_id in seen_relationship_ids:
                errors.append(
                    f"{kind}:{identity} repeated relationship "
                    f"{relation.relationship_id}"
                )
                invalid = True
            seen_relationship_ids.add(relation.relationship_id)
            if relation.necessity is not expected_necessity:
                errors.append(
                    f"{relation.relationship_id} has inconsistent necessity"
                )
                invalid = True
            if not relation.provider_reason.strip() or not relation.evidence_block_ids:
                errors.append(
                    f"{relation.relationship_id} lacks reason or exact evidence"
                )
                invalid = True
            if len(set(relation.evidence_block_ids)) != len(
                relation.evidence_block_ids
            ):
                errors.append(
                    f"{relation.relationship_id} repeats exact evidence blocks"
                )
                invalid = True
            for block_id in relation.evidence_block_ids:
                block = block_directory.get(block_id)
                if not isinstance(block, dict):
                    errors.append(
                        f"{relation.relationship_id} cites fabricated block "
                        f"{block_id}"
                    )
                    invalid = True
                elif str(block.get("topic_id") or "") != relation.topic_id:
                    errors.append(
                        f"{relation.relationship_id} cites {block_id} from "
                        f"{block.get('topic_id')} as evidence for "
                        f"{relation.topic_id}"
                    )
                    invalid = True
        if invalid:
            continue
        claim = placement_policy.AtomicClaim(
            claim_id=claim_id,
            normalized_claim=description,
            source_location_topic_id=source_location_topic_id,
            origin_claim_ids=origin_claim_ids,
            split_group_id=split_group_id,
            protected_source_items=protected,
        )
        try:
            provisional = placement_policy.compute_provisional_placement(
                claim, relationships, order
            )
        except placement_policy.PlacementPolicyError as exc:
            errors.append(
                f"{kind}:{identity} placement is uncertifiable: {exc}"
            )
            continue
        if provisional.owner_topic_id != plan_topic_id:
            errors.append(
                f"{kind}:{identity} computed owner "
                f"{provisional.owner_topic_id!r} disagrees with plan/QID owner "
                f"{plan_topic_id!r}; post-host moves are forbidden"
            )
            continue
        specs.append({
            "kind": kind,
            "identity": identity,
            "row": row,
            "claim": claim,
            "origin_claim_sha256": origin_claim_sha256,
            "old_contract": old_contract,
            "qids": qids,
            "relationships": relationships,
            "provisional": provisional,
        })
    return specs, list(dict.fromkeys(errors))


def _host_mutation_critic_packet(
    specs: list[dict[str, Any]],
    *,
    block_directory: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    proposed: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    for spec in specs:
        for relation in spec["relationships"]:
            proposed.append(placement_policy.relationship_audit_row(relation))
            evidence.append({
                "relationship_id": relation.relationship_id,
                "claim_id": relation.claim_id,
                "claim": spec["claim"].normalized_claim,
                "mutation_kind": spec["kind"],
                "mutation_identity": spec["identity"],
                "assigned_qids": list(spec["qids"]),
                "topic_id": relation.topic_id,
                "relationship_type": relation.relationship_type.value,
                "necessity": relation.necessity,
                "provider_reason": relation.provider_reason,
                "exact_source_blocks": [
                    copy.deepcopy(block_directory[block_id])
                    for block_id in relation.evidence_block_ids
                ],
            })
    return (
        sorted(proposed, key=lambda row: str(row.get("relationship_id") or "")),
        sorted(evidence, key=lambda row: str(row.get("relationship_id") or "")),
    )


def _finalize_host_mutation_placement(
    plan: dict[str, Any],
    *,
    specs: list[dict[str, Any]],
    review: dict[str, Any],
    authority: dict[str, Any],
    plan_topic_id: str,
    order: placement_policy.TeachingOrder,
) -> dict[str, Any]:
    expected_ids = {
        relation.relationship_id
        for spec in specs
        for relation in spec["relationships"]
    }
    verdicts, errors = phase32._relationship_review_verdicts(
        review, expected_ids
    )
    if errors:
        raise RuntimeError(
            "type_host_placement_uncertified: " + "; ".join(errors[:8])
        )
    out = copy.deepcopy(plan)
    output_rows: dict[tuple[str, str], dict[str, Any]] = {
        ("create", str(row.get("new_concept_key") or "")): row
        for row in out.get("new_concepts") or []
        if isinstance(row, dict)
    }
    output_rows.update({
        ("expand", str(row.get("existing_concept_id") or "")): row
        for row in out.get("existing_concept_updates") or []
        if isinstance(row, dict)
    })
    for spec in specs:
        reviewed = placement_policy.apply_critic_verdicts(
            spec["relationships"], verdicts
        )
        try:
            decision = placement_policy.compute_placement(
                spec["claim"], reviewed, order
            )
        except placement_policy.PlacementPolicyError as exc:
            raise RuntimeError(
                "type_host_placement_uncertified: "
                f"{spec['kind']}:{spec['identity']}: {exc}"
            ) from exc
        if decision.owner_topic_id != plan_topic_id:
            raise RuntimeError(
                "type_host_placement_uncertified: independently reviewed owner "
                f"{decision.owner_topic_id!r} disagrees with plan/QID owner "
                f"{plan_topic_id!r}; post-host moves are forbidden"
            )
        claim = spec["claim"]
        relationship_rows = sorted(
            (
                placement_policy.relationship_audit_row(relation)
                for relation in reviewed
            ),
            key=lambda row: str(row.get("relationship_id") or ""),
        )
        contract = {
            "certified": True,
            "policy_version": placement_policy.POLICY_VERSION,
            "teaching_order_sha256": order.sha256,
            "claim_id": claim.claim_id,
            "normalized_claim": claim.normalized_claim,
            "origin_claim_sha256": spec["origin_claim_sha256"],
            "source_location_topic_id": claim.source_location_topic_id,
            "owner_topic_id": decision.owner_topic_id,
            "required_topic_ids": list(decision.required_topic_ids),
            "prerequisite_topic_ids": list(decision.prerequisite_topic_ids),
            "reference_edges": [list(edge) for edge in decision.reference_edges],
            "illustration_topic_ids": list(decision.illustration_topic_ids),
            "topic_relationships": relationship_rows,
            "origin_claim_ids": list(claim.origin_claim_ids),
            "split_group_id": claim.split_group_id,
            "protected_source_items": list(claim.protected_source_items),
        }
        contract["placement_certificate_sha256"] = (
            placement_policy.placement_contract_sha256(contract)
        )
        row = output_rows[(spec["kind"], spec["identity"])]
        row["topic_relationships"] = copy.deepcopy(relationship_rows)
        row["_placement_contract"] = contract

    reviews = sorted(
        (
            copy.deepcopy(row)
            for row in review.get("relationship_reviews") or []
            if isinstance(row, dict)
        ),
        key=lambda row: str(row.get("relationship_id") or ""),
    )
    digests = _host_mutation_output_digests(out)
    if digests is None:
        raise RuntimeError(
            "type_host_placement_uncertified: mutation output placement "
            "contracts are incomplete"
        )
    envelope = {
        "version": _HOST_MUTATION_PLACEMENT_VERSION,
        "authority": copy.deepcopy(authority),
        "plan_topic_id": plan_topic_id,
        "plan_semantic_sha256": phase3._sha256_json(
            _host_plan_semantic_material(out)
        ),
        "output_placement_sha256s": digests,
        "relationship_reviews": reviews,
    }
    envelope["certificate_sha256"] = phase3._sha256_json(envelope)
    out[_HOST_MUTATION_ENVELOPE_KEY] = envelope
    return out


def _host_mutation_envelope_is_current(
    plan: object,
    *,
    authority: Mapping[str, Any],
    plan_topic_id: str,
) -> bool:
    if not isinstance(plan, Mapping):
        return False
    envelope = plan.get(_HOST_MUTATION_ENVELOPE_KEY)
    if not isinstance(envelope, Mapping):
        return False
    stored = copy.deepcopy(dict(envelope))
    supplied = str(stored.pop("certificate_sha256", "") or "")
    if supplied != phase3._sha256_json(stored):
        return False
    if (
        stored.get("version") != _HOST_MUTATION_PLACEMENT_VERSION
        or stored.get("authority") != dict(authority)
        or str(stored.get("plan_topic_id") or "") != plan_topic_id
        or str(stored.get("plan_semantic_sha256") or "")
        != phase3._sha256_json(_host_plan_semantic_material(plan))
    ):
        return False
    digests = _host_mutation_output_digests(plan)
    if digests is None or stored.get("output_placement_sha256s") != digests:
        return False
    expected_ids = {
        str(relation.get("relationship_id") or "")
        for collection in (
            plan.get("new_concepts") or [],
            plan.get("existing_concept_updates") or [],
        )
        for row in collection
        if isinstance(row, Mapping)
        for relation in (
            (row.get("_placement_contract") or {}).get(
                "topic_relationships"
            ) or []
        )
        if isinstance(relation, Mapping)
        and str(relation.get("relationship_id") or "")
    }
    for collection in (
        plan.get("new_concepts") or [],
        plan.get("existing_concept_updates") or [],
    ):
        for row in collection:
            if not isinstance(row, Mapping):
                return False
            contract = row.get("_placement_contract")
            if not isinstance(contract, Mapping) or any((
                str(contract.get("policy_version") or "")
                != placement_policy.POLICY_VERSION,
                str(contract.get("teaching_order_sha256") or "")
                != str(authority.get("teaching_order_sha256") or ""),
                str(contract.get("owner_topic_id") or "") != plan_topic_id,
                _normal(contract.get("normalized_claim"))
                != _normal(row.get("description")),
            )):
                return False
    _verdicts, review_errors = phase32._relationship_review_verdicts(
        {"relationship_reviews": stored.get("relationship_reviews") or []},
        expected_ids,
    )
    return not review_errors


def _host_plan_cache_key(
    *,
    graph: dict[str, Any],
    topic_id: str,
    units: list[dict[str, Any]],
    concepts: list[dict[str, Any]],
    source_blocks: list[dict[str, Any]],
    placement_authority: Mapping[str, Any] | None = None,
) -> str:
    return phase3._sha256_json(
        {
            "version": _HOST_VERSION,
            "model": str(config.OPENAI_MODEL),
            "semantic_confidence_policy": confidence_policy.cache_identity(),
            "source_contract_hash": str(graph.get("source_contract_hash") or ""),
            "topic_id": topic_id,
            "units": phase31._json_safe(units),
            "concepts": phase31._json_safe(concepts),
            "source_blocks": [
                {
                    "block_id": row.get("block_id"),
                    "text_sha256": phase3._sha256_text(row.get("text") or ""),
                }
                for row in source_blocks
            ],
            "placement_authority": phase31._json_safe(
                placement_authority or {}
            ),
        }
    )


def _read_host_plan_cache(
    key: str,
    *,
    placement_authority: Mapping[str, Any] | None = None,
    topic_id: str = "",
) -> dict[str, Any] | None:
    directory = _artifact_dir()
    if directory is None:
        return None
    cache = _read_json(directory / _HOST_PLAN_CACHE_FILENAME)
    if cache.get("version") != _HOST_VERSION:
        return None
    entry = (cache.get("entries") or {}).get(key)
    if not isinstance(entry, dict) or entry.get("status") != "verified":
        return None
    plan = entry.get("plan")
    if not isinstance(plan, dict):
        return None
    if str(entry.get("plan_sha256") or "") != phase3._sha256_json(plan):
        return None
    if placement_authority is not None:
        if (
            entry.get("placement_authority") != dict(placement_authority)
            or not _host_mutation_envelope_is_current(
                plan,
                authority=placement_authority,
                plan_topic_id=topic_id,
            )
        ):
            return None
    return copy.deepcopy(plan)


def _write_host_plan_cache(
    key: str,
    *,
    graph: dict[str, Any],
    topic_id: str,
    plan: dict[str, Any],
    confidence: float,
    placement_authority: Mapping[str, Any] | None = None,
) -> None:
    directory = _artifact_dir()
    if directory is None:
        return
    path = directory / _HOST_PLAN_CACHE_FILENAME
    cache = _read_json(path)
    if cache.get("version") != _HOST_VERSION:
        cache = {"version": _HOST_VERSION, "entries": {}}
    cache.setdefault("entries", {})[key] = {
        "status": "verified",
        "created_at": time.time(),
        "model": str(config.OPENAI_MODEL),
        "source_contract_hash": str(graph.get("source_contract_hash") or ""),
        "topic_id": topic_id,
        "review_confidence": float(confidence),
        "placement_authority": copy.deepcopy(
            dict(placement_authority or {})
        ),
        "plan_sha256": phase3._sha256_json(plan),
        "plan": copy.deepcopy(plan),
    }
    _write_json(path, cache)


def _provider_review_required_issues(
    response: dict[str, Any],
) -> tuple[list[str], list[str]]:
    issues: list[str] = []
    unit_ids: list[str] = []
    for row in response.get("assignments") or []:
        if not isinstance(row, dict) or row.get("decision") != "review_required":
            continue
        unit_id = str(row.get("assignment_unit_id") or "")
        reason = re.sub(r"\s+", " ", str(row.get("reason") or "")).strip()
        unit_ids.append(unit_id)
        issues.append(
            f"{unit_id or 'assignment unit'} requires review"
            + (f": {reason[:1000]}" if reason else "")
        )
    return issues, unit_ids


def _host_parse_errors_are_mechanical(errors: list[str]) -> bool:
    """Only response-shape defects qualify for a bounded provider correction."""

    mechanical_prefixes = (
        "host resolver returned no assignments/new_concepts arrays",
        "host resolver returned a non-array existing_concept_updates",
        "host resolver returned a non-object assignment",
        "unknown assignment unit ID ",
        "duplicate assignment unit ID ",
    )
    meaningful = [
        value
        for value in errors
        if not value.startswith("missing or invalid assignment unit ID(s):")
    ]
    return bool(meaningful) and all(
        value.startswith(mechanical_prefixes) for value in meaningful
    )


def _directed_resolution_issues(
    resolution: dict[str, Any],
    *,
    plan: dict[str, Any],
    units: list[dict[str, Any]],
    concepts: list[dict[str, Any]],
) -> list[str]:
    choice = str(resolution.get("choice") or "")
    if choice == "custom":
        if not str(
            resolution.get("instruction")
            or resolution.get("custom_instruction")
            or ""
        ).strip():
            return ["custom resolution omitted its instruction"]
        return []
    target = str(
        resolution.get("target_concept_id")
        or resolution.get("existing_concept_id")
        or ""
    )
    concept_ids = {
        str(row.get("concept_id") or "") for row in concepts
    }
    if choice in {"expand_existing", "select_existing"} and target not in concept_ids:
        return [
            f"human resolution selected unavailable concept {target or '<empty>'}"
        ]
    requested_units = {
        str(value)
        for value in resolution.get("assignment_unit_ids") or []
        if str(value)
    }
    unit_id = str(
        resolution.get("assignment_unit_id")
        or resolution.get("unit_id")
        or (
            (resolution.get("item") or {}).get("unit_id")
            if isinstance(resolution.get("item"), Mapping)
            else ""
        )
        or ""
    )
    if unit_id:
        requested_units.add(unit_id)
    if not requested_units:
        requested_units = {
            str(row.get("assignment_unit_id") or "")
            for row in units
            if str(row.get("assignment_unit_id") or "")
        }
    assignments = {
        str(row.get("assignment_unit_id") or ""): row
        for row in plan.get("assignments") or []
        if isinstance(row, dict)
    }
    issues: list[str] = []
    for requested in sorted(requested_units):
        assignment = assignments.get(requested)
        if assignment is None:
            issues.append(
                f"directed resolution omitted assignment unit {requested}"
            )
            continue
        decision = str(assignment.get("decision") or "")
        if choice == "expand_existing":
            if (
                decision != "expand_existing"
                or str(assignment.get("existing_concept_id") or "") != target
            ):
                issues.append(
                    f"{requested} did not apply the directed expansion to {target}"
                )
        elif choice == "select_existing":
            if (
                decision != "existing"
                or str(assignment.get("existing_concept_id") or "") != target
            ):
                issues.append(
                    f"{requested} did not use the selected existing concept {target}"
                )
        elif choice == "create_new" and decision != "create_new":
            issues.append(
                f"{requested} did not apply the directed create-new choice"
            )
    return issues


def _resolve_host_plan(
    *,
    graph: dict[str, Any],
    topic_id: str,
    topic: dict[str, Any],
    units: list[dict[str, Any]],
    concepts: list[dict[str, Any]],
    source_blocks: list[dict[str, Any]],
    provider: HostProvider,
    critic: HostCritic,
    placement_authority: dict[str, Any] | None = None,
    placement_order: placement_policy.TeachingOrder | None = None,
    block_directory: dict[str, dict[str, Any]] | None = None,
    qid_contracts: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    strict_placement = placement_authority is not None
    if strict_placement and any((
        placement_order is None,
        not isinstance(block_directory, dict) or not block_directory,
        not isinstance(qid_contracts, dict) or not qid_contracts,
    )):
        raise RuntimeError(
            "type_host_placement_uncertified: Phase 3.3 host resolver was "
            "started with incomplete placement authority"
        )
    identity = _host_human_context_identity(
        graph=graph,
        topic_id=topic_id,
        units=units,
        concepts=concepts,
        source_blocks=source_blocks,
    )
    resolutions = _human_resolutions_for(identity)
    resolution = resolutions[-1] if resolutions else None
    resolved_unit_ids = {
        unit_id
        for row in resolutions
        for unit_id in _resolution_unit_ids(row)
    }
    deferred_unit_ids = list(
        dict.fromkeys(
            unit_id
            for row in resolutions
            for unit_id in row.get("deferred_assignment_unit_ids") or []
            if str(unit_id) and str(unit_id) not in resolved_unit_ids
        )
    )
    if deferred_unit_ids:
        next_unit_id = str(deferred_unit_ids[0])
        raise HumanDecisionRequired(
            _host_pending_decision(
                identity=identity,
                topic=topic,
                units=units,
                concepts=concepts,
                source_blocks=source_blocks,
                issues=[
                    f"{next_unit_id} was also rejected in the previous "
                    "independent review and needs your decision."
                ],
                proposed_plan={
                    "rejected_assignment_unit_ids": deferred_unit_ids,
                },
                resolved_choices=resolutions,
            )
        )
    key = _host_plan_cache_key(
        graph=graph,
        topic_id=topic_id,
        units=units,
        concepts=concepts,
        source_blocks=source_blocks,
        placement_authority=placement_authority,
    )
    cached = _read_host_plan_cache(
        key,
        placement_authority=placement_authority,
        topic_id=topic_id,
    )
    if cached is not None:
        progress.log(
            "Reused API-verified Phase 3.3 Type-host plan for "
            f"{str(topic.get('title') or topic_id)!r}; no host model call was repeated.",
            level="success",
        )
        return cached

    # One correction is enough for a strict-schema response-shape defect. This
    # budget is separate from semantic adjudication, which never retries.
    attempts = 1 if resolution is not None else min(2, _max_host_attempts())
    last_errors: list[str] = []
    contract_feedback: list[str] = []
    for attempt in range(1, attempts + 1):
        payload = {
            "metadata": copy.deepcopy(graph.get("metadata") or {}),
            "topic": copy.deepcopy(topic),
            "topics": copy.deepcopy(graph.get("topics") or []),
            "placement_policy_version": placement_policy.POLICY_VERSION,
            "placement_authority": copy.deepcopy(
                placement_authority or {}
            ),
            "assignment_units": copy.deepcopy(units),
            "existing_concepts": copy.deepcopy(concepts),
            "source_blocks": copy.deepcopy(source_blocks),
            "attempt": attempt,
            "max_attempts": attempts,
            "previous_plan": {},
            "critic_feedback": {},
            "response_contract_feedback": copy.deepcopy(contract_feedback),
        }
        if resolution is not None:
            payload["human_resolution"] = copy.deepcopy(resolution)
            payload["human_resolutions"] = copy.deepcopy(resolutions)
        response = provider(copy.deepcopy(payload))
        review_issues, review_unit_ids = _provider_review_required_issues(
            response if isinstance(response, dict) else {}
        )
        if review_issues:
            proposed = (
                copy.deepcopy(response) if isinstance(response, dict) else {}
            )
            proposed["rejected_assignment_unit_ids"] = review_unit_ids
            raise HumanDecisionRequired(
                _host_pending_decision(
                    identity=identity,
                    topic=topic,
                    units=units,
                    concepts=concepts,
                    source_blocks=source_blocks,
                    issues=review_issues,
                    proposed_plan=proposed,
                    resolved_choice=resolution,
                    resolved_choices=resolutions,
                )
            )
        plan, parse_errors = _parse_host_plan(
            response if isinstance(response, dict) else {},
            unit_payloads=units,
            existing_concepts=concepts,
            source_blocks=source_blocks,
        )
        if parse_errors or plan is None:
            last_errors = parse_errors
            if (
                resolution is not None
                or not _host_parse_errors_are_mechanical(parse_errors)
            ):
                raise HumanDecisionRequired(
                    _host_pending_decision(
                        identity=identity,
                        topic=topic,
                        units=units,
                        concepts=concepts,
                        source_blocks=source_blocks,
                        issues=parse_errors,
                        proposed_plan=(
                            copy.deepcopy(response)
                            if isinstance(response, dict)
                            else {}
                        ),
                        resolved_choice=resolution,
                        resolved_choices=resolutions,
                    )
                )
            progress.log(
                "Phase 3.3 Type-host response-contract correction "
                f"{attempt}/{attempts} for {str(topic.get('title') or topic_id)!r} "
                "requires correction: " + "; ".join(parse_errors[:5]),
                level="warning",
            )
            contract_feedback = list(parse_errors)
            continue
        if resolutions:
            directed_issues = [
                issue
                for saved_resolution in resolutions
                for issue in _directed_resolution_issues(
                    saved_resolution,
                    plan=plan,
                    units=units,
                    concepts=concepts,
                )
            ]
            if directed_issues:
                raise HumanDecisionRequired(
                    _host_pending_decision(
                        identity=identity,
                        topic=topic,
                        units=units,
                        concepts=concepts,
                        source_blocks=source_blocks,
                        issues=directed_issues,
                        proposed_plan=plan,
                        resolved_choice=resolution,
                        resolved_choices=resolutions,
                    )
                )
        specs: list[dict[str, Any]] = []
        proposed_relationships: list[dict[str, Any]] = []
        relationship_evidence: list[dict[str, Any]] = []
        if strict_placement:
            assert placement_order is not None
            assert isinstance(block_directory, dict)
            assert isinstance(qid_contracts, dict)
            specs, placement_errors = _host_mutation_specs(
                plan,
                plan_topic_id=topic_id,
                unit_payloads=units,
                existing_concepts=concepts,
                graph=graph,
                order=placement_order,
                block_directory=block_directory,
                qid_contracts=qid_contracts,
            )
            if placement_errors:
                raise ProviderResponseContractError(
                    "type_host_placement_uncertified: provider host mutation "
                    "contract failed closed: "
                    + "; ".join(placement_errors[:8])
                )
            proposed_relationships, relationship_evidence = (
                _host_mutation_critic_packet(
                    specs,
                    block_directory=block_directory,
                )
            )
        review_payload = {
            **copy.deepcopy(payload),
            "proposed_plan": copy.deepcopy(plan),
            "proposed_relationships": proposed_relationships,
            "placement_relationship_evidence": relationship_evidence,
        }
        review = critic(copy.deepcopy(review_payload))
        state = phase31._review_state(
            review,
            concept_ids={str(row.get("assignment_unit_id") or "") for row in units},
        )
        critic_band = confidence_policy.semantic_band(state["confidence"])
        if state["verified"] and critic_band == "accepted":
            if strict_placement:
                assert placement_order is not None
                assert placement_authority is not None
                plan = _finalize_host_mutation_placement(
                    plan,
                    specs=specs,
                    review=review if isinstance(review, dict) else {},
                    authority=placement_authority,
                    plan_topic_id=topic_id,
                    order=placement_order,
                )
            _write_host_plan_cache(
                key,
                graph=graph,
                topic_id=topic_id,
                plan=plan,
                confidence=float(state["confidence"]),
                placement_authority=placement_authority,
            )
            progress.log(
                "Phase 3.3 independently verified "
                f"{len(units)} Type/Case host decision(s) for "
                f"{str(topic.get('title') or topic_id)!r}"
                + (f" after {attempt} attempt(s)." if attempt > 1 else "."),
                level="success",
            )
            return plan
        last_errors = list(state["issues"]) or [
            "critic verdict was " + str(state.get("verdict") or "missing")
        ]
        if critic_band == "human_review":
            last_errors.insert(
                0,
                "independent critic confidence "
                f"{float(state['confidence']):.3f} is in the "
                "0.900–0.919 human-review band",
            )
        elif critic_band == "rejected":
            last_errors.insert(
                0,
                "independent critic confidence "
                f"{float(state['confidence']):.3f} is below the "
                f"{confidence_policy.threshold_text()} semantic threshold",
            )
        pending_plan = copy.deepcopy(plan)
        pending_plan["rejected_assignment_unit_ids"] = sorted(
            str(value) for value in state["rejected"] if str(value)
        )
        raise HumanDecisionRequired(
            _host_pending_decision(
                identity=identity,
                topic=topic,
                units=units,
                concepts=concepts,
                source_blocks=source_blocks,
                issues=last_errors,
                proposed_plan=pending_plan,
                resolved_choice=resolution,
                resolved_choices=resolutions,
            )
        )
    details = "; ".join(last_errors[:8])
    raise ProviderResponseContractError(
        "Phase 3.3 provider could not return a valid Type-host response contract "
        f"for {str(topic.get('title') or topic_id)!r} after {attempts} bounded "
        "mechanical correction attempt(s)"
        + (f": {details}" if details else "")
    )


def _record_placement_digests(
    records: list[dict[str, Any]],
    *,
    require_all: bool = False,
) -> dict[str, str]:
    digests: dict[str, str] = {}
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            continue
        contract = record.get("_placement_contract")
        if not isinstance(contract, Mapping):
            title = str(
                record.get("concept_title") or record.get("concept") or ""
            )
            if require_all and not cr.is_culmination(title):
                raise RuntimeError(
                    "type_host_placement_uncertified: output record is missing "
                    f"a placement contract at index {index}"
                )
            continue
        digest = str(contract.get("placement_certificate_sha256") or "")
        if digest != placement_policy.placement_contract_sha256(contract):
            raise RuntimeError(
                "type_host_placement_uncertified: output record carries a "
                f"stale placement contract at index {index}"
            )
        if require_all:
            grounding_certificate.verify_placement_contract(record)
        digests[f"{index:04d}"] = digest
    return digests


def _host_result_cache_key(
    records: list[dict[str, Any]],
    *,
    graph: dict[str, Any],
    mined_types: dict[str, Any],
    inventory: dict[str, Any] | None,
    placement_authority: Mapping[str, Any] | None = None,
) -> str:
    clean_types = copy.deepcopy((mined_types or {}).get("types") or [])
    for mtype in clean_types:
        if isinstance(mtype, dict):
            for key in list(mtype):
                if key.startswith("_phase33_"):
                    mtype.pop(key, None)
    inventory_identity = [
        {
            "qid": str(row.get("qid") or ""),
            "topic_hint": str(row.get("topic_hint") or ""),
            "task_sha256": phase3._sha256_text(
                row.get("raw_task") or row.get("normalized_task") or ""
            ),
        }
        for row in (inventory or {}).get("items") or []
        if isinstance(row, dict)
    ]
    return phase3._sha256_json(
        {
            "version": _HOST_VERSION,
            "model": str(config.OPENAI_MODEL),
            "semantic_confidence_policy": confidence_policy.cache_identity(),
            "source_contract_hash": str(graph.get("source_contract_hash") or ""),
            "records": phase31._json_safe(records),
            "types": phase31._json_safe(clean_types),
            "inventory": inventory_identity,
            "placement_authority": phase31._json_safe(
                placement_authority or {}
            ),
        }
    )


def _read_host_result_cache(
    key: str,
    *,
    placement_authority: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    directory = _artifact_dir()
    if directory is None:
        return None
    cache = _read_json(directory / _HOST_RESULT_CACHE_FILENAME)
    records = cache.get("records")
    host_map = cache.get("host_map")
    qid_map = cache.get("qid_map")
    if (
        cache.get("version") != _HOST_VERSION
        or cache.get("cache_key") != key
        or not isinstance(records, list)
        or not isinstance(host_map, dict)
        or not isinstance(qid_map, dict)
        or cache.get("records_sha256") != phase3._sha256_json(records)
        or cache.get("host_map_sha256") != phase3._sha256_json(host_map)
        or cache.get("qid_map_sha256") != phase3._sha256_json(qid_map)
    ):
        return None
    try:
        output_digests = _record_placement_digests(
            records, require_all=placement_authority is not None
        )
    except RuntimeError:
        return None
    if cache.get("output_placement_sha256s") != output_digests:
        return None
    if placement_authority is not None and cache.get(
        "placement_authority"
    ) != dict(placement_authority):
        return None
    envelope = cache.get("result_envelope")
    if not isinstance(envelope, dict):
        return None
    material = {
        "version": _HOST_MUTATION_PLACEMENT_VERSION,
        "cache_key": key,
        "placement_authority": copy.deepcopy(
            dict(placement_authority or {})
        ),
        "records_sha256": str(cache.get("records_sha256") or ""),
        "host_map_sha256": str(cache.get("host_map_sha256") or ""),
        "qid_map_sha256": str(cache.get("qid_map_sha256") or ""),
        "output_placement_sha256s": output_digests,
    }
    if envelope != {
        **material,
        "certificate_sha256": phase3._sha256_json(material),
    }:
        return None
    return copy.deepcopy(cache)


def _write_host_result_cache(
    key: str,
    *,
    graph: dict[str, Any],
    records: list[dict[str, Any]],
    host_map: dict[str, dict[str, Any]],
    qid_map: dict[str, dict[str, Any]],
    summary: dict[str, int],
    placement_authority: Mapping[str, Any] | None = None,
) -> None:
    directory = _artifact_dir()
    if directory is None:
        return
    records_sha256 = phase3._sha256_json(records)
    host_map_sha256 = phase3._sha256_json(host_map)
    qid_map_sha256 = phase3._sha256_json(qid_map)
    output_digests = _record_placement_digests(
        records, require_all=placement_authority is not None
    )
    envelope = {
        "version": _HOST_MUTATION_PLACEMENT_VERSION,
        "cache_key": key,
        "placement_authority": copy.deepcopy(
            dict(placement_authority or {})
        ),
        "records_sha256": records_sha256,
        "host_map_sha256": host_map_sha256,
        "qid_map_sha256": qid_map_sha256,
        "output_placement_sha256s": output_digests,
    }
    envelope["certificate_sha256"] = phase3._sha256_json(envelope)
    _write_json(
        directory / _HOST_RESULT_CACHE_FILENAME,
        {
            "version": _HOST_VERSION,
            "cache_key": key,
            "created_at": time.time(),
            "model": str(config.OPENAI_MODEL),
            "source_contract_hash": str(graph.get("source_contract_hash") or ""),
            "placement_authority": copy.deepcopy(
                dict(placement_authority or {})
            ),
            "records_sha256": records_sha256,
            "records": copy.deepcopy(records),
            "host_map": copy.deepcopy(host_map),
            "host_map_sha256": host_map_sha256,
            "qid_map": copy.deepcopy(qid_map),
            "qid_map_sha256": qid_map_sha256,
            "output_placement_sha256s": output_digests,
            "result_envelope": envelope,
            "summary": copy.deepcopy(summary),
        },
    )


def _attach_host_maps(
    mined_types: dict[str, Any],
    *,
    host_map: dict[str, dict[str, Any]],
    qid_map: dict[str, dict[str, Any]],
    units: list[dict[str, Any]],
) -> None:
    mined_types["_phase33_verified_host_map"] = copy.deepcopy(host_map)
    mined_types["_phase33_verified_host_by_qid"] = copy.deepcopy(qid_map)
    mined_types["_phase33_type_host_contract"] = {
        "version": _HOST_VERSION,
        "state": "verified_before_topology_freeze",
        "assignment_unit_count": len(host_map),
        "host_map_sha256": phase3._sha256_json(host_map),
    }
    units_by_origin: dict[str, list[str]] = {}
    for unit in units:
        origin = str(unit.get("_origin_type_id") or unit.get("type_id") or "")
        unit_id = str(unit.get("_phase33_assignment_unit_id") or "")
        if origin and unit_id:
            units_by_origin.setdefault(origin, []).append(unit_id)
    for mtype in mined_types.get("types") or []:
        if not isinstance(mtype, dict):
            continue
        mtype["_phase33_verified_host_map"] = copy.deepcopy(host_map)
        mtype["_phase33_verified_host_by_qid"] = copy.deepcopy(qid_map)
        origin = str(mtype.get("type_id") or "")
        destinations = {
            phase3._sha256_json(host_map[unit_id]): host_map[unit_id]
            for unit_id in units_by_origin.get(origin, [])
            if unit_id in host_map
        }
        if len(destinations) == 1:
            destination = next(iter(destinations.values()))
            mtype["concept_match_hint"] = destination["concept_title"]
            mtype["parent_concept_match_hint"] = destination["parent_concept"]
            mtype["topic_match_hint"] = destination["topic"]
            mtype["_phase33_host_verified"] = True


def _materialize_existing_host_updates(
    records: list[dict[str, Any]],
    *,
    plans: list[dict[str, Any]],
    concept_payload: list[dict[str, Any]],
    concept_index: dict[str, int],
    subtopic_by_block: dict[str, str],
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    int,
]:
    """Apply verified expansions, then normalize them to ordinary host choices.

    Phase 3.3.3 replaces the downstream host materializer and intentionally
    understands only ``existing``/``create_new``. Applying the source-grounded
    update here keeps that adapter compatible while making the resulting host
    assignment a normal existing-concept destination.
    """

    out = [copy.deepcopy(row) for row in records]
    payloads = [copy.deepcopy(row) for row in concept_payload]
    payload_by_id = {
        str(row.get("concept_id") or ""): row for row in payloads
    }
    normalized_plans = [copy.deepcopy(plan) for plan in plans]
    applied: set[str] = set()
    for plan in normalized_plans:
        if isinstance(plan.get(_HOST_MUTATION_ENVELOPE_KEY), dict):
            plan[_HOST_MUTATION_SOURCE_PLAN_KEY] = copy.deepcopy(plan)
        for update in plan.get("existing_concept_updates") or []:
            concept_id = str(update.get("existing_concept_id") or "")
            if concept_id in applied:
                raise ValueError(
                    "Phase 3.3 repeated a verified existing-concept expansion: "
                    f"{concept_id}"
                )
            if concept_id not in concept_index or concept_id not in payload_by_id:
                raise ValueError(
                    "Phase 3.3 could not materialize existing-concept expansion: "
                    f"{concept_id or '<empty>'}"
                )
            applied.add(concept_id)
            index = concept_index[concept_id]
            record = out[index]
            description = str(update.get("description") or "").strip()
            mastery = str(update.get("achieving_mastery") or "").strip()
            sections = cr.split_sections(str(record.get("concept_details") or ""))
            description_body = (
                description + "\nAchieving Mastery: " + mastery
            )
            replaced = False
            for section_index, (label, _body) in enumerate(sections):
                if _normal(label) != "description":
                    continue
                sections[section_index] = (label, description_body)
                replaced = True
                break
            if not replaced:
                sections.insert(0, ("Description", description_body))
            record["concept_details"] = cr.join_sections(sections)
            prior_keywords = [
                value.strip()
                for value in str(record.get("keywords") or "").split(",")
                if value.strip()
            ]
            update_keywords = [
                str(value).strip()
                for value in update.get("keywords") or []
                if str(value).strip()
            ]
            record["keywords"] = ", ".join(
                dict.fromkeys([*prior_keywords, *update_keywords])
            )
            block_ids = list(
                dict.fromkeys(
                    [
                        *[
                            str(value)
                            for value in record.get("_source_block_ids") or []
                            if str(value)
                        ],
                        *[
                            str(value)
                            for value in update.get("source_block_ids") or []
                            if str(value)
                        ],
                    ]
                )
            )
            record["_source_block_ids"] = block_ids
            record["_semantic_subtopic_ids"] = sorted(
                {
                    subtopic_by_block.get(block_id, "")
                    for block_id in block_ids
                }
                - {""}
            )
            # Phase 3.1 already treats this contract as independently verified
            # Type-host evidence and therefore preserves the cited block IDs
            # without another provider/critic pair.
            record["_source_grounding_contract"] = (
                "api-created-missing-type-host"
            )
            record["_source_grounding_version"] = _HOST_VERSION
            record["_source_grounding_confidence"] = float(
                update.get("confidence") or 0.0
            )
            placement_contract = update.get("_placement_contract")
            if isinstance(placement_contract, dict):
                if str(placement_contract.get("owner_topic_id") or "") != str(
                    record.get("_semantic_topic_id") or ""
                ):
                    raise ValueError(
                        "Phase 3.3 expanded host placement owner disagrees "
                        "with the immutable record topic"
                    )
                record["_placement_contract"] = copy.deepcopy(
                    placement_contract
                )
            record["_phase33_expanded_type_host"] = True
            record["_phase3_assignment_unit_ids"] = list(
                dict.fromkeys(
                    [
                        *[
                            str(value)
                            for value in record.get(
                                "_phase3_assignment_unit_ids"
                            )
                            or []
                            if str(value)
                        ],
                        *[
                            str(value)
                            for value in update.get("assignment_unit_ids") or []
                            if str(value)
                        ],
                    ]
                )
            )
            payload = payload_by_id[concept_id]
            payload["source_claim"] = description
            payload["source_block_ids"] = block_ids
        for assignment in plan.get("assignments") or []:
            if assignment.get("decision") != "expand_existing":
                continue
            assignment["decision"] = "existing"
            assignment["new_concept_key"] = "NONE"
            assignment["resolved_via"] = "expand_existing"
        plan["existing_concept_updates"] = []
    return out, payloads, normalized_plans, len(applied)


def _apply_host_plan(
    records: list[dict[str, Any]],
    *,
    plans: list[dict[str, Any]],
    concept_payload: list[dict[str, Any]],
    concept_index: dict[str, int],
    topic_by_id: dict[str, dict[str, Any]],
    graph: dict[str, Any],
    subtopic_by_block: dict[str, str],
    units: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]], int]:
    out = [copy.deepcopy(row) for row in records]
    concept_by_id = {str(row.get("concept_id") or ""): row for row in concept_payload}
    unit_by_id = {
        str(row.get("_phase33_assignment_unit_id") or ""): row for row in units
    }
    new_record_by_key: dict[str, dict[str, Any]] = {}
    for plan in plans:
        for definition in plan.get("new_concepts") or []:
            key = str(definition.get("new_concept_key") or "")
            topic_id = str(definition.get("topic_id") or "")
            topic = topic_by_id[topic_id]
            block_ids = [str(value) for value in definition.get("source_block_ids") or []]
            record = {
                "topic": str(topic.get("title") or ""),
                "parent_concept": str(definition.get("parent_concept") or ""),
                "concept_title": str(definition.get("concept_title") or ""),
                "concept_details": cr.join_sections(
                    [
                        (
                            "Description",
                            str(definition.get("description") or "").strip()
                            + "\nAchieving Mastery: "
                            + str(definition.get("achieving_mastery") or "").strip(),
                        )
                    ]
                ),
                "keywords": ", ".join(
                    str(value).strip()
                    for value in definition.get("keywords") or []
                    if str(value).strip()
                ),
                "_semantic_topic_id": topic_id,
                "_semantic_graph_contract": graph.get("source_contract_hash"),
                "_semantic_topic_contract": "phase3.3-api-verified-type-host",
                "_source_block_ids": block_ids,
                "_semantic_subtopic_ids": sorted(
                    {
                        subtopic_by_block.get(block_id, "")
                        for block_id in block_ids
                    }
                    - {""}
                ),
                "_source_grounding_contract": "api-created-missing-type-host",
                "_source_grounding_version": _HOST_VERSION,
                "_source_grounding_confidence": float(
                    definition.get("confidence") or 0.0
                ),
                "_phase3_assignment_unit_ids": list(
                    definition.get("assignment_unit_ids") or []
                ),
                "_phase33_new_type_host": True,
            }
            if isinstance(definition.get("_placement_contract"), dict):
                record["_placement_contract"] = copy.deepcopy(
                    definition["_placement_contract"]
                )
            new_record_by_key[key] = record
            out.append(record)

    host_map: dict[str, dict[str, Any]] = {}
    qid_map: dict[str, dict[str, Any]] = {}
    for plan in plans:
        for assignment in plan.get("assignments") or []:
            unit_id = str(assignment.get("assignment_unit_id") or "")
            if assignment.get("decision") == "existing":
                concept_id = str(assignment.get("existing_concept_id") or "")
                payload = concept_by_id[concept_id]
                record = out[concept_index[concept_id]]
            else:
                key = str(assignment.get("new_concept_key") or "")
                record = new_record_by_key[key]
                payload = {
                    "concept_title": record.get("concept_title"),
                    "parent_concept": record.get("parent_concept"),
                    "topic": record.get("topic"),
                    "topic_id": record.get("_semantic_topic_id"),
                }
            destination = {
                "topic_id": str(
                    payload.get("topic_id")
                    or record.get("_semantic_topic_id")
                    or ""
                ),
                "topic": str(payload.get("topic") or record.get("topic") or ""),
                "parent_concept": str(
                    payload.get("parent_concept")
                    or record.get("parent_concept")
                    or ""
                ),
                "concept_title": str(
                    payload.get("concept_title")
                    or record.get("concept_title")
                    or ""
                ),
                "decision": str(assignment.get("decision") or ""),
                "confidence": float(assignment.get("confidence") or 0.0),
            }
            host_map[unit_id] = destination
            for qid in unit_by_id.get(unit_id, {}).get("source_question_ids") or []:
                qid = str(qid or "").strip()
                if not qid:
                    continue
                if qid in qid_map and qid_map[qid] != destination:
                    raise ValueError(
                        f"Phase 3.3 host plan assigned qid {qid} to multiple destinations"
                    )
                qid_map[qid] = copy.deepcopy(destination)
    return out, host_map, qid_map, len(new_record_by_key)


def _assert_unchanged_host_contracts_preserved(
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
) -> None:
    """Prove ordinary existing hosts were not silently re-certified."""

    if len(after) < len(before):
        raise ValueError("Phase 3.3 host mutation removed an existing concept")
    for index, original in enumerate(before):
        current = after[index]
        if current.get("_phase33_expanded_type_host"):
            continue
        if original.get("_placement_contract") != current.get(
            "_placement_contract"
        ):
            raise ValueError(
                "Phase 3.3 changed an unchanged existing host placement "
                f"contract at record index {index}"
            )


def _bind_and_verify_applied_host_mutations(
    records: list[dict[str, Any]],
    *,
    plans: list[dict[str, Any]],
    host_map: dict[str, dict[str, Any]],
    qid_map: dict[str, dict[str, Any]],
    units: list[dict[str, Any]],
    placement_authority: Mapping[str, Any],
    qid_contracts: dict[str, dict[str, Any]],
    graph: dict[str, Any],
) -> list[dict[str, Any]]:
    """Attach created contracts and reject every post-plan owner mutation."""

    out = [copy.deepcopy(row) for row in records]
    allowed_block_ids = {
        str(row.get("block_id") or "")
        for row in graph.get("blocks") or []
        if isinstance(row, dict) and str(row.get("block_id") or "")
    }
    for plan in plans:
        certified_plan = plan.get(_HOST_MUTATION_SOURCE_PLAN_KEY)
        if not isinstance(certified_plan, dict):
            certified_plan = plan
        envelope = certified_plan.get(_HOST_MUTATION_ENVELOPE_KEY)
        plan_topic_id = str(
            (envelope or {}).get("plan_topic_id") or ""
        )
        if not _host_mutation_envelope_is_current(
            certified_plan,
            authority=placement_authority,
            plan_topic_id=plan_topic_id,
        ):
            raise ValueError(
                "Phase 3.3 host mutation plan lost its current placement "
                "envelope before apply"
            )
        for definition in certified_plan.get("new_concepts") or []:
            if not isinstance(definition, dict):
                continue
            contract = definition.get("_placement_contract")
            topic_id = str(definition.get("topic_id") or "")
            title = str(definition.get("concept_title") or "")
            parent = str(definition.get("parent_concept") or "")
            local_key = str(definition.get("new_concept_key") or "")
            candidates = [
                row for row in out
                if row.get("_phase33_new_type_host")
                and str(row.get("_semantic_topic_id") or "") == topic_id
                and str(row.get("concept_title") or "") == title
                and str(row.get("parent_concept") or "") == parent
                and str(
                    row.get("_phase33_local_new_concept_key") or local_key
                ) == local_key
            ]
            if len(candidates) != 1 or not isinstance(contract, dict):
                raise ValueError(
                    "Phase 3.3 could not bind one certified placement to "
                    f"created host {local_key!r}"
                )
            existing = candidates[0].get("_placement_contract")
            if isinstance(existing, dict) and existing != contract:
                raise ValueError(
                    "Phase 3.3 created host carried a conflicting placement "
                    f"contract for {local_key!r}"
                )
            candidates[0]["_placement_contract"] = copy.deepcopy(contract)

    record_by_destination: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for record in out:
        if not isinstance(record.get("_placement_contract"), dict):
            continue
        key = (
            str(record.get("_semantic_topic_id") or ""),
            str(record.get("parent_concept") or ""),
            str(record.get("concept_title") or ""),
        )
        record_by_destination.setdefault(key, []).append(record)
        grounding_certificate.verify_placement_contract(
            record,
            allowed_block_ids=allowed_block_ids,
        )

    unit_by_id = {
        str(unit.get("_phase33_assignment_unit_id") or ""): unit
        for unit in units
        if isinstance(unit, dict)
    }
    for unit_id, destination in host_map.items():
        owner = str(destination.get("topic_id") or "")
        unit = unit_by_id.get(unit_id)
        if unit is None:
            raise ValueError(
                f"Phase 3.3 host map contains unknown unit {unit_id!r}"
            )
        qids = [
            str(value).strip()
            for value in unit.get("source_question_ids") or []
            if str(value).strip()
        ]
        if not qids or any(
            str((qid_contracts.get(qid) or {}).get("owner_topic_id") or "")
            != owner
            for qid in qids
        ):
            raise ValueError(
                f"Phase 3.3 host {unit_id!r} disagrees with a certified QID owner"
            )
        key = (
            owner,
            str(destination.get("parent_concept") or ""),
            str(destination.get("concept_title") or ""),
        )
        matches = record_by_destination.get(key) or []
        if len(matches) != 1:
            raise ValueError(
                f"Phase 3.3 host {unit_id!r} does not resolve to one placed record"
            )
        placement_digest = str(
            matches[0]["_placement_contract"].get(
                "placement_certificate_sha256"
            ) or ""
        )
        destination["placement_certificate_sha256"] = placement_digest
        for qid in qids:
            qid_destination = qid_map.get(qid)
            if (
                not isinstance(qid_destination, dict)
                or str(qid_destination.get("topic_id") or "") != owner
            ):
                raise ValueError(
                    f"Phase 3.3 QID {qid} lost its certified owner during apply"
                )
            qid_destination["placement_certificate_sha256"] = placement_digest
    return out


def _reconcile_type_hosts(
    generation: Any,
    records: list[dict[str, Any]],
    *,
    mined_types: dict[str, Any],
    question_task_inventory: dict[str, Any] | None,
    meta: dict[str, Any],
    mmd_text: str,
    method_anchors: list[dict[str, Any]],
    graph: dict[str, Any] | None = None,
    canonical: dict[str, Any] | None = None,
    provider: HostProvider | None = None,
    critic: HostCritic | None = None,
    placement_provider: PlacementProvider | None = None,
    placement_critic: PlacementCritic | None = None,
) -> list[dict[str, Any]]:
    graph = graph or phase3.active_graph()
    if not isinstance(graph, dict) or not (mined_types or {}).get("types"):
        return records
    canonical = (
        canonical
        or (phase3.active_session() or {}).get("canonical")
        or {}
    )
    out = phase3.canonicalize_record_topics(records, graph)
    # Exact source-block grounding is a prerequisite for deciding whether a Type
    # already has a valid host.
    out = phase31.ground_concepts(out, graph=graph, canonical=canonical)
    annotated = phase3.annotate_mined_types(mined_types, graph)
    mined_types.clear()
    mined_types.update(annotated)
    units = _stable_assignment_units(generation, mined_types, graph)
    ownership_certified = _certify_type_case_ownership(
        generation,
        mined_types=mined_types,
        inventory=question_task_inventory,
        units=units,
        graph=graph,
        canonical=canonical,
        provider=placement_provider,
        critic=placement_critic,
    )
    if ownership_certified:
        # Certification may split one reusable Type's Cases by their
        # independently computed semantic owners.  Never retain the earlier
        # source-location projection or its assignment-unit identities.
        units = _stable_assignment_units(generation, mined_types, graph)
    elif phase3.semantic_api_enabled() and (question_task_inventory or {}).get(
        "items"
    ):
        raise RuntimeError(
            "type_case_owner_uncertified: production host reconciliation "
            "cannot use physical source location as semantic ownership"
        )
    normal_units: list[dict[str, Any]] = []
    task_by_qid, _inventory_rows = _inventory_task_evidence(
        generation, question_task_inventory
    )
    scope_payload = generation._scope_payload_from_records(out)
    for unit in units:
        if unit.get("is_activity"):
            continue
        scope = generation._assignment_placement_scope(unit, scope_payload)
        placement = generation._certified_type_case_placement_contract(
            unit.get("_type_case_placement_contract")
        )
        if ownership_certified and placement is None:
            raise RuntimeError(
                "type_case_owner_uncertified: assignment unit "
                f"{unit.get('_phase33_assignment_unit_id') or '<empty>'} "
                "lost its certified owner before host resolution"
            )
        topic_ids = (
            [str(placement.get("owner_topic_id") or "")]
            if placement is not None
            else [
                str(value)
                for value in unit.get("_semantic_topic_ids") or []
            ]
        )
        topic_ids = [value for value in topic_ids if value]
        if scope != "normal" or len(topic_ids) != 1:
            continue
        unit["_semantic_topic_id"] = topic_ids[0]
        qids = [str(value) for value in unit.get("source_question_ids") or []]
        unit["_source_task_evidence"] = " ".join(
            task_by_qid[qid] for qid in qids if qid in task_by_qid
        )
        normal_units.append(unit)
    if not normal_units:
        return out

    placement_order: placement_policy.TeachingOrder | None = None
    placement_authority: dict[str, Any] | None = None
    qid_contracts: dict[str, dict[str, Any]] = {}
    block_directory: dict[str, dict[str, Any]] = {}
    if ownership_certified:
        placement_order = placement_policy.seal_teaching_order(
            graph,
            source_contract_hash=str(graph.get("source_contract_hash") or ""),
        )
        block_directory = phase32._exact_block_directory(graph, canonical)
        if not block_directory:
            raise RuntimeError(
                "type_host_placement_uncertified: semantic graph has no exact "
                "block directory for host mutation"
            )
        placement_authority, qid_contracts = _host_mutation_authority(
            generation,
            graph=graph,
            order=placement_order,
            inventory=question_task_inventory,
        )

    result_key = _host_result_cache_key(
        out,
        graph=graph,
        mined_types=mined_types,
        inventory=question_task_inventory,
        placement_authority=placement_authority,
    )
    cached_result = _read_host_result_cache(
        result_key,
        placement_authority=placement_authority,
    )
    if cached_result is not None:
        _attach_host_maps(
            mined_types,
            host_map=copy.deepcopy(cached_result["host_map"]),
            qid_map=copy.deepcopy(cached_result.get("qid_map") or {}),
            units=units,
        )
        progress.log(
            "Reused the API-verified Phase 3.3 Type-host preflight, including "
            "any source-grounded concepts created before topology freeze; no host "
            "or learner-analysis model call was repeated.",
            level="success",
        )
        return copy.deepcopy(cached_result["records"])

    if provider is None:
        provider = _host_plan_via_openai if phase3.semantic_api_enabled() else None
    if critic is None:
        critic = _host_critic_via_openai if phase3.semantic_api_enabled() else None
    if provider is None or critic is None:
        # Offline and injected-test paths retain the existing topology. The live
        # production path is fail-closed and always uses the independent resolver.
        return out

    progress.step(
        "Concept extraction — certifying Type hosts before topology freeze",
        value=0.925,
    )
    progress.log(
        "Phase 3.3 is independently checking every normal mined Type/Case unit "
        "against source-grounded concepts. A new concept may be added only when "
        "no existing row can host a distinct durable source idea.",
        level="warning",
    )

    concept_payload, concept_index, cids_by_topic = _normal_concept_payloads(out)
    concept_by_id = {str(row.get("concept_id") or ""): row for row in concept_payload}
    blocks_by_topic, subtopic_by_block = _topic_source_blocks(graph, canonical)
    all_source_blocks = [
        {**copy.deepcopy(row), "topic_id": source_topic_id}
        for source_topic_id in sorted(blocks_by_topic)
        for row in blocks_by_topic[source_topic_id]
    ]
    topic_by_id = {
        str(row.get("topic_id") or ""): row
        for row in graph.get("topics") or []
        if isinstance(row, dict) and str(row.get("topic_id") or "")
    }
    units_by_topic: dict[str, list[dict[str, Any]]] = {}
    for unit in normal_units:
        topic_id = str(unit.get("_semantic_topic_id") or "")
        units_by_topic.setdefault(topic_id, []).append(
            _unit_payload(unit, task_by_qid=task_by_qid)
        )

    plans: list[dict[str, Any]] = []
    for topic_id, topic_units in units_by_topic.items():
        topic = topic_by_id.get(topic_id)
        topic_source_blocks = blocks_by_topic.get(topic_id, [])
        if topic is None or not topic_source_blocks:
            raise ValueError(
                "Phase 3.3 cannot certify Type hosts because canonical topic "
                f"{topic_id or '<empty>'} has no usable source blocks"
            )
        existing = [
            copy.deepcopy(concept_by_id[cid])
            for cid in cids_by_topic.get(topic_id, [])
            if cid in concept_by_id
        ]
        plans.append(
            _resolve_host_plan(
                graph=graph,
                topic_id=topic_id,
                topic=topic,
                units=topic_units,
                concepts=existing,
                source_blocks=all_source_blocks,
                provider=provider,
                critic=critic,
                placement_authority=placement_authority,
                placement_order=placement_order,
                block_directory=block_directory,
                qid_contracts=qid_contracts,
            )
        )

    pre_mutation_records = copy.deepcopy(out)
    out, concept_payload, plans, expanded = _materialize_existing_host_updates(
        out,
        plans=plans,
        concept_payload=concept_payload,
        concept_index=concept_index,
        subtopic_by_block=subtopic_by_block,
    )
    reconciled, host_map, qid_map, created = _apply_host_plan(
        out,
        plans=plans,
        concept_payload=concept_payload,
        concept_index=concept_index,
        topic_by_id=topic_by_id,
        graph=graph,
        subtopic_by_block=subtopic_by_block,
        units=normal_units,
    )
    _assert_unchanged_host_contracts_preserved(
        pre_mutation_records,
        reconciled,
    )
    if placement_authority is not None:
        reconciled = _bind_and_verify_applied_host_mutations(
            reconciled,
            plans=plans,
            host_map=host_map,
            qid_map=qid_map,
            units=normal_units,
            placement_authority=placement_authority,
            qid_contracts=qid_contracts,
            graph=graph,
        )
    # Re-run exact grounding. Existing rows reuse Phase 3.1 caches; new hosts carry
    # independently verified block IDs and are preserved by that contract.
    reconciled = phase31.ground_concepts(
        reconciled,
        graph=graph,
        canonical=canonical,
    )
    if placement_authority is not None:
        allowed_blocks = set(block_directory)
        for record in reconciled:
            title = str(
                record.get("concept_title") or record.get("concept") or ""
            )
            if cr.is_culmination(title):
                continue
            grounding_certificate.verify_placement_contract(
                record,
                allowed_block_ids=allowed_blocks,
            )
    reconciled = phase32._order_grounded_records(
        reconciled,
        graph=graph,
        canonical=canonical,
    )
    reconciled = cr.set_culmination_recap(reconciled)
    if callable(getattr(generation, "_ensure_parent_concepts", None)):
        reconciled = generation._ensure_parent_concepts(reconciled)
    if created:
        reconciled = generation._ensure_misconceptions_via_api(
            reconciled,
            meta=meta,
            max_attempts=max(7, int(os.environ.get("AEGIS_PHASE33_LEARNER_ANALYSIS_ATTEMPTS", "7"))),
        )
    reconciled = generation._canonicalize_concept_rich_text(reconciled)
    generation._validate_final_or_raise(
        reconciled,
        stage="Phase 3.3 pre-freeze Type-host validation",
        inventory=None,
        mined_types=None,
        method_anchors=[],
        source_text=mmd_text,
    )
    _attach_host_maps(
        mined_types,
        host_map=host_map,
        qid_map=qid_map,
        units=normal_units,
    )
    summary = {
        "normal_assignment_units": len(normal_units),
        "existing_hosts": sum(
            1 for value in host_map.values() if value.get("decision") == "existing"
        ),
        "expanded_concepts": expanded,
        "created_concepts": created,
        "output_rows": len(reconciled),
    }
    _write_host_result_cache(
        result_key,
        graph=graph,
        records=reconciled,
        host_map=host_map,
        qid_map=qid_map,
        summary=summary,
        placement_authority=placement_authority,
    )
    progress.log(
        "Phase 3.3 certified every normal Type/Case unit before topology freeze: "
        f"{summary['existing_hosts']} unit(s) use existing concepts and "
        f"{summary['expanded_concepts']} existing concept(s) were expanded; "
        f"{created} necessary source-grounded concept(s) were created; "
        f"{len(host_map)} assignment unit(s) now have independently verified hosts.",
        level="success",
    )
    return reconciled


def _apply_verified_host_to_unit(
    unit: dict[str, Any],
    destination: dict[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(destination, dict):
        return unit
    out = copy.deepcopy(unit)
    out["concept_match_hint"] = str(destination.get("concept_title") or "")
    out["parent_concept_match_hint"] = str(destination.get("parent_concept") or "")
    out["topic_match_hint"] = str(destination.get("topic") or "")
    out["_phase33_host_verified"] = True
    out["_phase33_host_confidence"] = float(destination.get("confidence") or 0.999)
    return out


def install(generation: Any | None = None) -> None:
    if (
        getattr(phase3, "_PHASE33_PREFLIGHT_CONTRACT_VERSION", 0)
        >= _CONTRACT_VERSION
    ):
        return
    if generation is None:
        from . import generation as generation

    # Phase 3.2 production provider/critic caches are per concept. Injected
    # providers remain untouched for focused tests and extensions.
    original_phase32_provider = phase32._adjudicate_via_openai
    original_phase32_critic = phase32._critic_via_openai

    @wraps(original_phase32_provider)
    def cached_phase32_provider(payload: dict[str, Any]) -> dict[str, Any]:
        return _phase32_provider_with_cache(original_phase32_provider, payload)

    @wraps(original_phase32_critic)
    def cached_phase32_critic(payload: dict[str, Any]) -> dict[str, Any]:
        return _phase32_critic_with_cache(original_phase32_critic, payload)

    phase32._adjudicate_via_openai = cached_phase32_provider
    phase32._critic_via_openai = cached_phase32_critic

    original_phase32_adjudicate = phase32.adjudicate_topology

    @wraps(original_phase32_adjudicate)
    def convergent_adjudicate(records, *args, **kwargs):
        return _phase32_adjudicate_with_convergence(
            original_phase32_adjudicate,
            records,
            *args,
            **kwargs,
        )

    phase32.adjudicate_topology = convergent_adjudicate

    # Preserve independently verified host identity through Case expansion and
    # terminal qid-driven validation.
    original_expand = generation._expand_mined_types_to_assignment_units

    @wraps(original_expand)
    def expand_with_verified_hosts(types):
        units = original_expand(types)
        counts: dict[str, int] = {}
        for index, unit in enumerate(units, start=1):
            if not isinstance(unit, dict):
                continue
            base = str(unit.get("type_id") or f"UNIT-{index:04d}")
            counts[base] = counts.get(base, 0) + 1
            unit_id = base if counts[base] == 1 else f"{base}--{counts[base]:03d}"
            host_map = unit.get("_phase33_verified_host_map")
            destination = (
                host_map.get(unit_id)
                if isinstance(host_map, dict)
                else None
            )
            if destination is not None:
                units[index - 1] = _apply_verified_host_to_unit(unit, destination)
                units[index - 1]["_phase33_assignment_unit_id"] = unit_id
        return units

    generation._expand_mined_types_to_assignment_units = expand_with_verified_hosts

    original_units_by_qid = getattr(generation, "_mined_assignment_units_by_qid", None)
    if callable(original_units_by_qid):

        @wraps(original_units_by_qid)
        def units_by_qid_with_verified_hosts(mined_types):
            units = original_units_by_qid(mined_types)
            qid_map = (mined_types or {}).get("_phase33_verified_host_by_qid")
            if not isinstance(qid_map, dict):
                qid_map = {}
                for mtype in (mined_types or {}).get("types") or []:
                    if isinstance(mtype, dict) and isinstance(
                        mtype.get("_phase33_verified_host_by_qid"), dict
                    ):
                        qid_map.update(mtype["_phase33_verified_host_by_qid"])
            return {
                qid: _apply_verified_host_to_unit(unit, qid_map.get(qid))
                for qid, unit in units.items()
            }

        generation._mined_assignment_units_by_qid = units_by_qid_with_verified_hosts

    original_expected_host = getattr(generation, "_expected_normal_type_host_cid", None)
    if callable(original_expected_host):

        @wraps(original_expected_host)
        def expected_host(unit, topic_cids, concept_payload_by_id, **kwargs):
            if unit.get("_phase33_host_verified"):
                title = _normal(unit.get("concept_match_hint"))
                parent = _normal(unit.get("parent_concept_match_hint"))
                matches = [
                    cid
                    for cid in topic_cids
                    if cid in concept_payload_by_id
                    and not concept_payload_by_id[cid].get("is_culmination")
                    and _normal(concept_payload_by_id[cid].get("concept")) == title
                    and (
                        not parent
                        or _normal(concept_payload_by_id[cid].get("parent_concept"))
                        == parent
                    )
                ]
                if len(matches) == 1:
                    return matches[0]
            return original_expected_host(
                unit, topic_cids, concept_payload_by_id, **kwargs
            )

        generation._expected_normal_type_host_cid = expected_host

    original_learner_analysis = generation._ensure_misconceptions_via_api

    @wraps(original_learner_analysis)
    def learner_analysis_with_extra_convergence(records, *args, **kwargs):
        kwargs.setdefault(
            "max_attempts",
            max(7, int(os.environ.get("AEGIS_PHASE33_LEARNER_ANALYSIS_ATTEMPTS", "7"))),
        )
        return original_learner_analysis(records, *args, **kwargs)

    generation._ensure_misconceptions_via_api = learner_analysis_with_extra_convergence

    original_allocate = topology._allocate_after_freeze

    @wraps(original_allocate)
    def allocate_after_host_preflight(
        generation_module,
        records,
        *,
        subject,
        mmd_text,
        meta,
        source_sections,
        question_task_inventory,
        mined_types,
        method_anchors,
    ):
        reconciled = _reconcile_type_hosts(
            generation_module,
            records,
            mined_types=mined_types,
            question_task_inventory=question_task_inventory,
            meta=meta,
            mmd_text=mmd_text,
            method_anchors=method_anchors,
            graph=phase3.active_graph(),
            canonical=(phase3.active_session() or {}).get("canonical") or {},
        )
        return original_allocate(
            generation_module,
            reconciled,
            subject=subject,
            mmd_text=mmd_text,
            meta=meta,
            source_sections=source_sections,
            question_task_inventory=question_task_inventory,
            mined_types=mined_types,
            method_anchors=method_anchors,
        )

    topology._allocate_after_freeze = allocate_after_host_preflight
    phase3._PHASE33_PREFLIGHT_CONTRACT_VERSION = _CONTRACT_VERSION

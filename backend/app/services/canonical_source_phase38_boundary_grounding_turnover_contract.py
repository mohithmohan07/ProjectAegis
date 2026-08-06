"""Phase 3.8 boundary-aware exact grounding and targeted topology turnover.

A live RNE acceptance run exposed one final convergence gap. A concept can be
correctly placed under a main topic while one continuation paragraph or Figure
is assigned to the adjacent graph topic because a converter/page reading order
crosses a main-heading boundary. Phase 3.1 previously supplied only blocks whose
graph ``topic_id`` exactly matched the concept. The grounding critic therefore
had to reject a valid concept even though the missing source block was immediately
beside the topic boundary.

This contract:

* keeps native topic blocks authoritative;
* adds a small, source-ordered window from the immediately adjacent topics as
  explicitly labelled boundary evidence;
* permits boundary evidence only when it visibly continues the target topic,
  never to excuse a genuinely cross-topic concept;
* gives exact grounding the same verified visual-page channel as topology review;
* maps a grounding failure back to the exact original topology concept and retries
  only that concept for move/refine/split;
* allows several targeted convergence passes with cycle-aware instructions rather
  than replaying the whole chapter twice and stopping;
* gives an exhausted concept one final quality-first atomisation/minimal-concept
  verification pass;
* and, when even that fails, disposes of the candidate deterministically —
  narrowing each unsupported concept to exactly what its canonical evidence
  states — so the run always reaches the output workbook without deleting a
  concept or waiting for a person;
* invalidates cached final topology rows grounded under an older contract.

No textbook wording, QID, Figure, or source identity is invented or removed.

The terminal disposition deserves a note, because it inverts what this module
used to do.  Every bounded repair above it exists to make a concept correct;
none of them may run forever, so something has to happen when the last one
fails.  Raising was the old answer, and it was the wrong one: it stopped a
nine-tenths-finished job that nobody was watching, and it stopped it *after*
the expensive work was already paid for.  ``evidence_narrowing`` replaces it
with a rewrite the source itself dictates — no provider call, no invention, no
deletion — so the failure mode becomes "this concept says less than it wanted
to, and the log says which clause went" instead of "there is no workbook".
"""
from __future__ import annotations

import copy
import json
import os
import re
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from typing import Any, Callable, Iterator

from .. import config
from . import canonical_source_phase22 as phase22
from . import canonical_source_phase3 as phase3
from . import canonical_source_phase31_grounding_contract as phase31
from . import canonical_source_phase32_topology_adjudication_contract as phase32
from . import canonical_source_phase33_preflight_contract as phase33
from . import canonical_source_phase37_visual_topology_convergence_contract as phase37
from . import concept_refiner as cr
from . import early_semantic_gate as early_gate
from . import evidence_narrowing
from . import grounding_certificate
from . import placement_policy
from . import progress
from . import semantic_confidence_policy as confidence_policy
from . import semantic_recovery

_CONTRACT_VERSION = 5
_GROUNDING_VERSION = "phase3.8-certified-required-grounding-5"

_LAST_REPAIRED_TOPOLOGY: ContextVar[list[dict[str, Any]] | None] = ContextVar(
    "aegis_phase38_last_repaired_topology",
    default=None,
)
_ACTIVE_GROUNDING_RECORDS: ContextVar[
    list[dict[str, Any]] | None
] = ContextVar(
    "aegis_phase38_active_grounding_records",
    default=None,
)

_EXCLUDED_BLOCK_KINDS = frozenset({"layout", "heading", "navigation"})
_MAX_CONVERGENCE_ISSUES = 100
#: Statuses meaning "this issue will receive no further paid pass".
#: ``exhausted`` is the pre-disposition spelling and is still read.
_TERMINAL_STATUSES = frozenset({"exhausted", "disposed"})
_GROUNDING_ISSUE_ID_RE = re.compile(
    r"\b(?:CONCEPT-GROUND|TOPOLOGY-CONCEPT)-\d{1,6}\b",
    re.IGNORECASE,
)


def _boundary_block_limit() -> int:
    return max(
        1,
        min(
            32,
            int(os.environ.get("AEGIS_PHASE38_BOUNDARY_BLOCKS_PER_SIDE", "8")),
        ),
    )


def _max_convergence_passes() -> int:
    return max(
        2,
        min(
            12,
            int(os.environ.get("AEGIS_PHASE38_TOPOLOGY_CONVERGENCE_PASSES", "6")),
        ),
    )


def _max_convergence_candidates() -> int:
    """Bound materially different candidates for one persisted job issue."""

    try:
        value = int(
            os.environ.get("AEGIS_PHASE38_MAX_CANDIDATES_PER_ISSUE", "8")
        )
    except (TypeError, ValueError):
        value = 8
    return max(1, min(24, value))


class Phase38ConvergenceExhausted(RuntimeError):
    """Quality-first candidate verification ended without a safe output."""


# Production binds this context to one UploadJob checkpoint.  The ledger is
# JSON-only and every mutation is persisted by the orchestration callback
# before another semantic action can unwind the stack.  The fallback ContextVar
# exists only for pure unit calls that have no UploadJob; it is task-local and
# is never the production durability mechanism.
_ACTIVE_CONVERGENCE_STORE: ContextVar[dict[str, Any] | None] = ContextVar(
    "aegis_phase38_active_convergence_store",
    default=None,
)
_EPHEMERAL_CONVERGENCE_STATES: ContextVar[dict[str, dict[str, Any]] | None] = (
    ContextVar("aegis_phase38_ephemeral_convergence_states", default=None)
)


def _fresh_convergence_state(
    *,
    scope: str,
    source_contract_hash: str = "",
) -> dict[str, Any]:
    return {
        "version": 1,
        "contract": _GROUNDING_VERSION,
        "scope": scope,
        "source_contract_hash": source_contract_hash,
        "base_candidate_sha256": "",
        "candidate_sha256": "",
        "candidate_history": [],
        "attempts": 0,
        "signatures": {},
        "suppressed_resolution_ids": [],
        "feedback": {},
        "final_verification_pending": False,
        "status": "active",
        "terminal_reason": "",
        # Names the deterministic disposition that produced the final
        # candidate, so an auditor can tell a converged run from a disposed
        # one without re-reading the log.
        "disposition": "",
        # Phase 3.8 originally kept one global attempt counter.  Keep those
        # top-level fields as a mirror of the active issue so existing UI,
        # exports, and checkpoints remain readable, while the durable buckets
        # below enforce the configured budget independently per issue.
        "issue_buckets": {},
        "active_issue_key": "",
        "legacy_unscoped_issue": False,
        # A provider request is claimed durably before dispatch.  A worker that
        # observes request_started cannot know whether the remote request was
        # billed, so it must fail closed instead of replaying it.
        "dispatch_status": "idle",
        "dispatch_sequence": 0,
        "dispatch_candidate_sha256": "",
        "dispatch_issue_key": "",
        "dispatch_decision_id": "",
        "dispatch_decision_context_hash": "",
    }


def _fresh_issue_bucket() -> dict[str, Any]:
    return {
        "candidate_history": [],
        "attempts": 0,
        "signatures": {},
        "feedback": {},
        "final_verification_pending": False,
        "status": "active",
        "terminal_reason": "",
    }


def _normalized_issue_bucket(value: Any) -> dict[str, Any]:
    bucket = _fresh_issue_bucket()
    if not isinstance(value, dict):
        return bucket
    try:
        attempts = max(0, int(value.get("attempts") or 0))
    except (TypeError, ValueError):
        attempts = 0
    signatures = (
        {
            str(key): max(0, int(count))
            for key, count in (value.get("signatures") or {}).items()
            if re.fullmatch(r"[0-9a-f]{64}", str(key))
            and isinstance(count, int)
            and not isinstance(count, bool)
            and count > 0
        }
        if isinstance(value.get("signatures"), dict)
        else {}
    )
    feedback = (
        {
            str(key): str(text)[:16000]
            for key, text in (value.get("feedback") or {}).items()
            if str(key)
        }
        if isinstance(value.get("feedback"), dict)
        else {}
    )
    history = list(
        dict.fromkeys(
            str(item)
            for item in value.get("candidate_history") or []
            if re.fullmatch(r"[0-9a-f]{64}", str(item))
        )
    )[:_max_convergence_candidates()]
    status = str(value.get("status") or "active")
    # "exhausted" is retained for version-1 checkpoints written before the
    # terminal disposition existed; both terminal statuses mean the same
    # thing to the reader -- no further paid pass for this issue.
    if status not in _TERMINAL_STATUSES | {
        "active", "final_verification_pending"
    }:
        status = "active"
    final_pending = bool(
        value.get("final_verification_pending")
        or status == "final_verification_pending"
    )
    if final_pending and status not in _TERMINAL_STATUSES:
        status = "final_verification_pending"
    bucket.update({
        "candidate_history": history,
        "attempts": attempts,
        "signatures": signatures,
        "feedback": feedback,
        "final_verification_pending": final_pending,
        "status": status,
        "terminal_reason": str(value.get("terminal_reason") or "")[:4000],
    })
    return bucket


def _legacy_bucket_from_state(state: dict[str, Any]) -> dict[str, Any]:
    return _normalized_issue_bucket({
        "candidate_history": state.get("candidate_history") or [],
        "attempts": state.get("attempts") or 0,
        "signatures": state.get("signatures") or {},
        "feedback": state.get("feedback") or {},
        "final_verification_pending": bool(
            state.get("final_verification_pending")
        ),
        "status": state.get("status") or "active",
        "terminal_reason": state.get("terminal_reason") or "",
    })


def _mirror_active_issue(
    state: dict[str, Any],
    issue_key: str,
    bucket: dict[str, Any],
) -> None:
    """Update one issue bucket and its backward-compatible top-level mirror."""

    normalized = _normalized_issue_bucket(bucket)
    buckets = dict(state.get("issue_buckets") or {})
    buckets[issue_key] = normalized
    state["issue_buckets"] = buckets
    state["active_issue_key"] = issue_key
    state["legacy_unscoped_issue"] = False
    for field in (
        "candidate_history",
        "attempts",
        "signatures",
        "feedback",
        "final_verification_pending",
        "status",
        "terminal_reason",
    ):
        state[field] = copy.deepcopy(normalized[field])


def _clear_dispatch_claim(state: dict[str, Any]) -> None:
    state["dispatch_status"] = "idle"
    state["dispatch_candidate_sha256"] = ""
    state["dispatch_issue_key"] = ""
    state["dispatch_decision_id"] = ""
    state["dispatch_decision_context_hash"] = ""


def _json_state(value: dict[str, Any]) -> dict[str, Any]:
    """Return a detached JSON-only state or fail before corrupting a checkpoint."""

    return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True))


def _normalized_convergence_state(
    value: dict[str, Any] | None,
    *,
    scope: str,
    source_contract_hash: str = "",
) -> dict[str, Any]:
    """Normalize a persisted ledger without accepting process-only containers."""

    fresh = _fresh_convergence_state(
        scope=scope,
        source_contract_hash=source_contract_hash,
    )
    if not isinstance(value, dict):
        return fresh
    try:
        loaded = _json_state(value)
    except (TypeError, ValueError):
        return fresh
    if (
        loaded.get("version") != 1
        or loaded.get("contract") != _GROUNDING_VERSION
        or str(loaded.get("scope") or "") != scope
    ):
        return fresh

    legacy_bucket = _normalized_issue_bucket(loaded)
    suppressed = list(dict.fromkeys(
        str(item)
        for item in loaded.get("suppressed_resolution_ids") or []
        if str(item)
    ))[:100]

    issue_buckets: dict[str, dict[str, Any]] = {}
    raw_buckets = loaded.get("issue_buckets")
    if isinstance(raw_buckets, dict):
        for raw_key, raw_bucket in list(raw_buckets.items())[
            :_MAX_CONVERGENCE_ISSUES
        ]:
            key = str(raw_key)
            if not re.fullmatch(r"[0-9a-f]{64}", key):
                continue
            issue_buckets[key] = _normalized_issue_bucket(raw_bucket)
    active_issue_key = str(loaded.get("active_issue_key") or "")
    if active_issue_key not in issue_buckets:
        active_issue_key = ""

    has_legacy_activity = bool(
        legacy_bucket["attempts"]
        or legacy_bucket["candidate_history"]
        or legacy_bucket["signatures"]
        or legacy_bucket["feedback"]
        or legacy_bucket["final_verification_pending"]
        or legacy_bucket["status"] != "active"
        or legacy_bucket["terminal_reason"]
    )
    legacy_unscoped_issue = bool(
        not issue_buckets
        and (
            loaded.get("legacy_unscoped_issue")
            or (
                "issue_buckets" not in loaded
                and has_legacy_activity
            )
        )
    )

    try:
        dispatch_sequence = max(
            0, int(loaded.get("dispatch_sequence") or 0)
        )
    except (TypeError, ValueError):
        dispatch_sequence = 0
    dispatch_status = str(loaded.get("dispatch_status") or "idle")
    if dispatch_status not in {
        "idle", "request_started", "decision_returned"
    }:
        dispatch_status = "idle"
    dispatch_candidate = str(
        loaded.get("dispatch_candidate_sha256") or ""
    )
    if not re.fullmatch(r"[0-9a-f]{64}", dispatch_candidate):
        dispatch_candidate = ""
    dispatch_issue_key = str(loaded.get("dispatch_issue_key") or "")
    if dispatch_issue_key and not re.fullmatch(
        r"[0-9a-f]{64}", dispatch_issue_key
    ):
        dispatch_issue_key = ""
    dispatch_decision_id = str(loaded.get("dispatch_decision_id") or "")
    dispatch_decision_context_hash = str(
        loaded.get("dispatch_decision_context_hash") or ""
    )
    if dispatch_status == "idle":
        dispatch_candidate = ""
        dispatch_issue_key = ""
        dispatch_decision_id = ""
        dispatch_decision_context_hash = ""
    elif dispatch_status == "request_started":
        dispatch_decision_id = ""
        dispatch_decision_context_hash = ""

    fresh.update({
        "source_contract_hash": str(
            loaded.get("source_contract_hash") or source_contract_hash
        ),
        "base_candidate_sha256": (
            str(loaded.get("base_candidate_sha256") or "")
            if re.fullmatch(
                r"[0-9a-f]{64}",
                str(loaded.get("base_candidate_sha256") or ""),
            )
            else ""
        ),
        "candidate_sha256": (
            str(loaded.get("candidate_sha256") or "")
            if re.fullmatch(
                r"[0-9a-f]{64}",
                str(loaded.get("candidate_sha256") or ""),
            )
            else ""
        ),
        "candidate_history": legacy_bucket["candidate_history"],
        "attempts": legacy_bucket["attempts"],
        "signatures": legacy_bucket["signatures"],
        "suppressed_resolution_ids": suppressed,
        "feedback": legacy_bucket["feedback"],
        "final_verification_pending": legacy_bucket[
            "final_verification_pending"
        ],
        "status": legacy_bucket["status"],
        "terminal_reason": legacy_bucket["terminal_reason"],
        "disposition": str(loaded.get("disposition") or "")[:128],
        "issue_buckets": issue_buckets,
        "active_issue_key": active_issue_key,
        "legacy_unscoped_issue": legacy_unscoped_issue,
        "dispatch_status": dispatch_status,
        "dispatch_sequence": dispatch_sequence,
        "dispatch_candidate_sha256": dispatch_candidate,
        "dispatch_issue_key": dispatch_issue_key,
        "dispatch_decision_id": dispatch_decision_id[:128],
        "dispatch_decision_context_hash": (
            dispatch_decision_context_hash[:64]
        ),
    })
    if active_issue_key:
        _mirror_active_issue(
            fresh,
            active_issue_key,
            issue_buckets[active_issue_key],
        )
    return fresh


@contextmanager
def convergence_checkpoint_context(
    *,
    scope: str,
    state: dict[str, Any] | None,
    persist: Callable[
        [dict[str, Any] | None, dict[str, Any] | None],
        None,
    ],
) -> Iterator[None]:
    """Bind Phase 3.8 to one durable UploadJob checkpoint namespace."""

    loaded = _normalized_convergence_state(state, scope=scope)
    store = {
        "scope": scope,
        "state": loaded,
        # Keep the exact state this context observed in durable storage.  It
        # may differ from ``loaded`` when an old contract/scope is normalized;
        # the checkpoint writer must compare against the observed value before
        # replacing it so a stale worker cannot silently rebase its mutation.
        "persisted_state": (
            _json_state(state) if isinstance(state, dict) else None
        ),
        "persist": persist,
    }
    token = _ACTIVE_CONVERGENCE_STORE.set(store)
    try:
        yield
    finally:
        _ACTIVE_CONVERGENCE_STORE.reset(token)


def _row_identity(row: Any) -> tuple[str, str]:
    """Identity that survives row reordering across a restart."""

    if not isinstance(row, dict):
        return ("", "")
    return (
        _normal(row.get("concept_title") or row.get("concept")),
        _normal(row.get("topic")),
    )


def _convergence_scope_key(records: list[dict[str, Any]]) -> str:
    """Return the per-job scope, or a deterministic pure-call fallback."""

    active = _ACTIVE_CONVERGENCE_STORE.get()
    if isinstance(active, dict) and str(active.get("scope") or ""):
        return str(active["scope"])

    contract = ""
    try:
        graph = phase3.active_graph()
        if isinstance(graph, dict):
            contract = str(graph.get("source_contract_hash") or "")
    except Exception:
        contract = ""
    if contract:
        return contract
    return phase3._sha256_json(sorted(
        "|".join(_row_identity(row))
        for row in records
        if isinstance(row, dict)
    ))


def _convergence_state(scope: str) -> dict[str, Any]:
    active = _ACTIVE_CONVERGENCE_STORE.get()
    if (
        isinstance(active, dict)
        and str(active.get("scope") or "") == scope
        and isinstance(active.get("state"), dict)
    ):
        return active["state"]
    states = _EPHEMERAL_CONVERGENCE_STATES.get()
    if not isinstance(states, dict):
        states = {}
        _EPHEMERAL_CONVERGENCE_STATES.set(states)
    state = states.get(scope)
    if not isinstance(state, dict):
        state = _fresh_convergence_state(scope=scope)
        states[scope] = state
    return state


def _persist_convergence_state(state: dict[str, Any] | None) -> None:
    active = _ACTIVE_CONVERGENCE_STORE.get()
    if isinstance(active, dict):
        replacement = _json_state(state) if isinstance(state, dict) else None
        expected = active.get("persisted_state")
        expected = (
            _json_state(expected) if isinstance(expected, dict) else None
        )
        persist = active.get("persist")
        if callable(persist):
            persist(
                copy.deepcopy(expected),
                copy.deepcopy(replacement),
            )
        # Advance the observed durable value only after the persistence
        # callback succeeds. A rejected CAS must leave this context stale and
        # unable to dispatch another candidate.
        active["state"] = replacement
        active["persisted_state"] = replacement
        return
    states = _EPHEMERAL_CONVERGENCE_STATES.get()
    if not isinstance(states, dict):
        states = {}
        _EPHEMERAL_CONVERGENCE_STATES.set(states)
    if state is None:
        active_scope = ""
        active = _ACTIVE_CONVERGENCE_STORE.get()
        if isinstance(active, dict):
            active_scope = str(active.get("scope") or "")
        if active_scope:
            states.pop(active_scope, None)
        else:
            states.clear()
    else:
        detached = _json_state(state)
        states[str(detached.get("scope") or "")] = detached


def _clear_convergence_state(scope: str) -> None:
    """Delete this job/scope ledger only after verified convergence."""

    active = _ACTIVE_CONVERGENCE_STORE.get()
    if isinstance(active, dict) and str(active.get("scope") or "") == scope:
        _persist_convergence_state(None)
        return
    states = _EPHEMERAL_CONVERGENCE_STATES.get()
    if isinstance(states, dict):
        states.pop(scope, None)


def reset_convergence_state(scope: str | None = None) -> None:
    """Clear task-local pure-call state; production state lives in checkpoint."""

    states = _EPHEMERAL_CONVERGENCE_STATES.get()
    if not isinstance(states, dict):
        return
    if scope is None:
        states.clear()
    else:
        states.pop(scope, None)


def _normal(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def _semantic_blocks(graph: dict[str, Any]) -> list[dict[str, Any]]:
    return sorted(
        [
            row
            for row in graph.get("blocks") or []
            if isinstance(row, dict)
            and str(row.get("block_id") or "")
            and str(row.get("kind") or "") not in _EXCLUDED_BLOCK_KINDS
        ],
        key=lambda row: (
            int(row.get("order") or 0),
            int(row.get("source_start") or 0),
            str(row.get("block_id") or ""),
        ),
    )


def _prerequisite_block_limit() -> int:
    """Bounded number of earlier-topic blocks offered as prerequisite context."""

    try:
        value = int(
            os.environ.get("AEGIS_PHASE38_PREREQUISITE_BLOCKS", "12")
        )
    except (TypeError, ValueError):
        value = 12
    return max(0, min(40, value))


_TOKEN_RE = re.compile(r"[^\W_]{4,}", re.UNICODE)


def _significant_tokens(text: str) -> set[str]:
    return {token.casefold() for token in _TOKEN_RE.findall(str(text or ""))}


def _prerequisite_rows(
    ordered: list[dict[str, Any]],
    *,
    topic_id: str,
    first_native_index: int,
    native_tokens: set[str],
    excluded_ids: set[str],
) -> list[dict[str, Any]]:
    """Select earlier-topic blocks a later topic visibly builds on.

    Textbook reasoning is cumulative: a section legitimately depends on a
    definition established several sections earlier, which the immediate
    adjacent window cannot reach. Selection is deterministic and relevance
    ranked - an earlier block is offered only when it shares meaningful
    vocabulary with the target topic's own blocks - so this stays a bounded
    prerequisite channel rather than "the whole chapter".
    """

    limit = _prerequisite_block_limit()
    if limit <= 0 or not native_tokens:
        return []
    scored: list[tuple[int, int, dict[str, Any]]] = []
    for index, row in enumerate(ordered[:first_native_index]):
        block_id = str(row.get("block_id") or "")
        if not block_id or block_id in excluded_ids:
            continue
        if str(row.get("topic_id") or "") in {"", topic_id}:
            continue
        overlap = len(
            _significant_tokens(row.get("text") or "") & native_tokens
        )
        if overlap >= 2:
            scored.append((-overlap, -index, row))
    scored.sort(key=lambda item: (item[0], item[1]))
    return [row for _score, _order, row in scored[:limit]]


def _certified_prerequisite_rows(
    ordered: list[dict[str, Any]],
    *,
    topic_id: str,
) -> tuple[bool, list[dict[str, Any]]]:
    """Return exact cross-topic blocks from certified necessary relationships.

    Candidate blocks are shared by all concepts in one topic.  Once any row in
    that topic declares a certified placement contract, lexical similarity is
    no longer an admissible evidence authority for the topic: use only the
    union of exact necessary relationship blocks outside the certified owner
    topic. This includes earlier ``CORE_TEACHING`` evidence when a later
    required method owns an irreducible cumulative claim. Incidental mentions
    and reference-only edges never become grounding candidates.
    """

    records = _ACTIVE_GROUNDING_RECORDS.get()
    if not isinstance(records, list):
        return False, []

    block_by_id = {
        str(row.get("block_id") or ""): row
        for row in ordered
        if isinstance(row, dict) and str(row.get("block_id") or "")
    }
    allowed_block_ids = set(block_by_id)
    certified_contracts: list[dict[str, Any]] = []
    for row in records:
        if not isinstance(row, dict):
            continue
        raw = row.get(grounding_certificate.PLACEMENT_CONTRACT_FIELD)
        if not isinstance(raw, dict) or not bool(raw.get("certified")):
            continue
        assigned_topic_id = str(row.get("_semantic_topic_id") or "").strip()
        claimed_owner_id = str(raw.get("owner_topic_id") or "").strip()
        # Route by the actual candidate row assignment first.  A tampered owner
        # must be verified and rejected here, not skipped into lexical fallback.
        row_topic_id = assigned_topic_id or claimed_owner_id
        if row_topic_id != topic_id:
            continue
        try:
            contract = grounding_certificate.verify_placement_contract(
                row,
                allowed_block_ids=allowed_block_ids,
            )
        except grounding_certificate.GroundingCertificateError as exc:
            raise ValueError(
                "failed exact source-block grounding before freeze: "
                f"certified placement contract is invalid for topic "
                f"{topic_id}: {exc}"
            ) from exc
        if contract is None:
            raise ValueError(
                "failed exact source-block grounding before freeze: "
                f"topic {topic_id} declared certified placement without a "
                "verifiable placement contract"
            )
        certified_contracts.append(contract)

    if not certified_contracts:
        return False, []

    selected_ids: set[str] = set()
    necessary_types = {
        relationship_type.value
        for relationship_type in placement_policy.NECESSARY_TYPES
    }
    for contract in certified_contracts:
        required_topics = {
            str(value).strip()
            for value in contract.get("required_topic_ids") or []
            if str(value).strip()
        }
        owner_topic_id = str(contract.get("owner_topic_id") or "").strip()
        covered_topics: set[str] = set()
        relationships = (
            contract.get("topic_relationships")
            or contract.get("relationships")
            or []
        )
        for relation in relationships:
            if not isinstance(relation, dict):
                continue
            relationship_type = str(
                relation.get("relationship_type") or ""
            ).strip().upper()
            if relationship_type not in necessary_types:
                continue
            asserted_topic = str(relation.get("topic_id") or "").strip()
            if asserted_topic not in required_topics:
                raise ValueError(
                    "failed exact source-block grounding before freeze: "
                    "certified necessary relationship names topic "
                    f"{asserted_topic or '(missing)'} outside the placement "
                    "required-topic set"
                )
            evidence_ids = {
                str(value).strip()
                for value in relation.get("evidence_block_ids") or []
                if str(value).strip()
            }
            if not evidence_ids:
                raise ValueError(
                    "failed exact source-block grounding before freeze: "
                    f"certified required topic {asserted_topic} has no "
                    "exact evidence blocks"
                )
            for block_id in evidence_ids:
                block = block_by_id.get(block_id)
                if not isinstance(block, dict):
                    raise ValueError(
                        "failed exact source-block grounding before freeze: "
                        f"certified required block {block_id} does not "
                        "exist in the active semantic graph"
                    )
                actual_topic = str(block.get("topic_id") or "").strip()
                if actual_topic != asserted_topic:
                    raise ValueError(
                        "failed exact source-block grounding before freeze: "
                        f"certified required block {block_id} asserts "
                        f"topic {asserted_topic}, but the graph assigns "
                        f"{actual_topic or '(missing)'}"
                    )
                if str(block.get("kind") or "") in _EXCLUDED_BLOCK_KINDS:
                    raise ValueError(
                        "failed exact source-block grounding before freeze: "
                        f"certified required block {block_id} is not a "
                        "semantic evidence block"
                    )
                if asserted_topic != owner_topic_id:
                    selected_ids.add(block_id)
            covered_topics.add(asserted_topic)
        missing_topics = sorted(required_topics - covered_topics)
        if missing_topics:
            raise ValueError(
                "failed exact source-block grounding before freeze: "
                "certified placement has required topic(s) without exact "
                "relationship evidence: " + ",".join(missing_topics)
            )

    return True, [
        row for row in ordered
        if str(row.get("block_id") or "") in selected_ids
    ]


def _contiguous_boundary_rows(
    rows: list[dict[str, Any]],
    *,
    reverse: bool,
) -> list[dict[str, Any]]:
    if not rows:
        return []
    ordered = list(reversed(rows)) if reverse else list(rows)
    adjacent_topic = str(ordered[0].get("topic_id") or "")
    if not adjacent_topic:
        return []
    selected: list[dict[str, Any]] = []
    for row in ordered:
        if str(row.get("topic_id") or "") != adjacent_topic:
            break
        selected.append(row)
        if len(selected) >= _boundary_block_limit():
            break
    if reverse:
        selected.reverse()
    return selected


def _candidate_payload_row(
    block: dict[str, Any],
    *,
    canonical_blocks: dict[str, dict[str, Any]],
    canonical: dict[str, Any],
    topic_titles: dict[str, str],
    target_topic_id: str,
    relation: str,
) -> dict[str, Any] | None:
    block_id = str(block.get("block_id") or "")
    source = canonical_blocks.get(block_id, {})
    text = phase37._evidence_text(block, source, canonical)
    if not block_id or not text:
        return None
    source_topic_id = str(block.get("topic_id") or "")
    provider_text = phase37._bounded_visual_evidence(text, limit=3000)
    return {
        "block_id": block_id,
        "kind": str(block.get("kind") or ""),
        "subtopic_id": str(block.get("subtopic_id") or ""),
        "figure_id": str(block.get("figure_id") or source.get("figure_id") or ""),
        "text": provider_text,
        "text_sha256": phase3._sha256_text(provider_text),
        "source_page": str(
            block.get("page_number")
            or block.get("pdf_page")
            or block.get("page")
            or source.get("page_number")
            or source.get("pdf_page")
            or source.get("page")
            or ""
        ),
        "source_order": int(block.get("order") or 0),
        "source_start": int(block.get("source_start") or 0),
        "source_topic_id": source_topic_id,
        "source_topic_title": topic_titles.get(source_topic_id, ""),
        "target_topic_id": target_topic_id,
        "boundary_relation": relation,
    }


def _candidate_blocks(
    graph: dict[str, Any],
    canonical: dict[str, Any],
    topic_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return native blocks plus a bounded immediate-neighbour evidence window."""
    native_usable, native_payload = (
        phase31._PHASE38_ORIGINAL_CANDIDATE_BLOCKS(
            graph,
            canonical,
            topic_id,
        )
    )
    ordered = _semantic_blocks(graph)
    native_positions = [
        index
        for index, row in enumerate(ordered)
        if str(row.get("topic_id") or "") == topic_id
    ]
    if not native_positions:
        return native_usable, native_payload

    first = min(native_positions)
    last = max(native_positions)
    before = _contiguous_boundary_rows(ordered[:first], reverse=True)
    after = _contiguous_boundary_rows(ordered[last + 1 :], reverse=False)

    strict_prerequisites, exact_prerequisite = _certified_prerequisite_rows(
        ordered,
        topic_id=topic_id,
    )
    exact_prerequisite_ids = {
        str(row.get("block_id") or "") for row in exact_prerequisite
    }
    if exact_prerequisite_ids:
        # Exact certified prerequisite authority wins over adjacency labels.
        # A block cannot be presented as a loose boundary hint when placement
        # has certified it as a required prerequisite.
        before = [
            row for row in before
            if str(row.get("block_id") or "") not in exact_prerequisite_ids
        ]
        after = [
            row for row in after
            if str(row.get("block_id") or "") not in exact_prerequisite_ids
        ]

    canonical_blocks = {
        str(row.get("block_id") or ""): row
        for row in canonical.get("blocks") or []
        if isinstance(row, dict) and str(row.get("block_id") or "")
    }
    topic_titles = {
        str(row.get("topic_id") or ""): str(row.get("title") or "")
        for row in graph.get("topics") or []
        if isinstance(row, dict) and str(row.get("topic_id") or "")
    }

    native_by_id = {
        str(row.get("block_id") or ""): copy.deepcopy(row)
        for row in native_payload
        if isinstance(row, dict) and str(row.get("block_id") or "")
    }
    usable_by_id = {
        str(row.get("block_id") or ""): row
        for row in native_usable
        if isinstance(row, dict) and str(row.get("block_id") or "")
    }

    for block in ordered:
        block_id = str(block.get("block_id") or "")
        if block_id not in native_by_id:
            continue
        source = canonical_blocks.get(block_id, {})
        native_by_id[block_id].setdefault("source_order", int(block.get("order") or 0))
        native_by_id[block_id].setdefault(
            "source_start", int(block.get("source_start") or 0)
        )
        native_by_id[block_id].setdefault("source_topic_id", topic_id)
        native_by_id[block_id].setdefault(
            "source_topic_title", topic_titles.get(topic_id, "")
        )
        native_by_id[block_id].setdefault("target_topic_id", topic_id)
        native_by_id[block_id].setdefault("boundary_relation", "native_topic")
        native_by_id[block_id].setdefault(
            "source_page",
            str(
                block.get("page_number")
                or block.get("pdf_page")
                or block.get("page")
                or source.get("page_number")
                or source.get("pdf_page")
                or source.get("page")
                or ""
            ),
        )
        native_by_id[block_id].setdefault(
            "text_sha256",
            phase3._sha256_text(native_by_id[block_id].get("text") or ""),
        )

    prerequisite = exact_prerequisite
    if not strict_prerequisites:
        prerequisite = _prerequisite_rows(
            ordered,
            topic_id=topic_id,
            first_native_index=first,
            native_tokens={
                token
                for row in ordered[first : last + 1]
                if str(row.get("topic_id") or "") == topic_id
                for token in _significant_tokens(row.get("text") or "")
            },
            excluded_ids={
                str(row.get("block_id") or "")
                for row in [*before, *after]
            }
            | set(native_by_id),
        )

    for relation, rows in (
        ("previous_topic_boundary", before),
        ("next_topic_boundary", after),
        (
            "certified_required_topic_evidence"
            if strict_prerequisites
            else "prerequisite_topic_evidence",
            prerequisite,
        ),
    ):
        for block in rows:
            block_id = str(block.get("block_id") or "")
            if not block_id or block_id in native_by_id:
                continue
            payload = _candidate_payload_row(
                block,
                canonical_blocks=canonical_blocks,
                canonical=canonical,
                topic_titles=topic_titles,
                target_topic_id=topic_id,
                relation=relation,
            )
            if payload is None:
                continue
            native_by_id[block_id] = payload
            usable_by_id[block_id] = block

    source_order = {
        str(row.get("block_id") or ""): int(row.get("order") or 0)
        for row in ordered
    }
    ordered_ids = sorted(
        native_by_id,
        key=lambda block_id: (
            source_order.get(block_id, 10**9),
            block_id,
        ),
    )
    return (
        [usable_by_id[block_id] for block_id in ordered_ids if block_id in usable_by_id],
        [native_by_id[block_id] for block_id in ordered_ids],
    )


def _grounding_schema_ids(payload: dict[str, Any]) -> tuple[list[str], list[str]]:
    concept_ids = [
        str(row.get("concept_id") or "")
        for row in payload.get("concepts") or []
        if isinstance(row, dict) and str(row.get("concept_id") or "")
    ]
    block_ids = [
        str(row.get("block_id") or "")
        for row in payload.get("source_blocks") or []
        if isinstance(row, dict) and str(row.get("block_id") or "")
    ]
    return concept_ids, block_ids


def _augment_grounding_payload(
    payload: dict[str, Any],
    *,
    page_numbers: list[int],
) -> dict[str, Any]:
    value = copy.deepcopy(payload)
    value["boundary_grounding_contract"] = {
        "native_relation": "native_topic",
        "allowed_boundary_relations": [
            "previous_topic_boundary",
            "next_topic_boundary",
        ],
        "allowed_context_relations": [
            "prerequisite_topic_evidence",
            "certified_required_topic_evidence",
        ],
        "rule": (
            "An adjacent boundary block may be selected only when it visibly "
            "continues the target topic despite converter/page reading-order drift. "
            "It must not be used to keep a concept under the wrong academic topic."
        ),
        "prerequisite_rule": (
            "Blocks marked prerequisite_topic_evidence come from an earlier "
            "topic this one builds on. They may support the foundational part "
            "of the claim, but they never establish ownership by themselves. "
            "The concept must draw on at least one native_topic block that "
            "supports knowledge, a method, or an interpretive framework from "
            "THIS topic which is necessary to understand or perform the atomic "
            "claim. Mere mention, chronology, physical evidence location, or an "
            "optional later method is insufficient. A concept supported only "
            "by prerequisite blocks is misplaced: reject it so topology can "
            "move, refine, or split it."
        ),
        "certified_required_evidence_rule": (
            "Blocks marked certified_required_topic_evidence are the exact "
            "blocks cited by independently accepted necessary topic "
            "relationships. They may express REQUIRED_PREREQUISITE, "
            "CORE_TEACHING, or REQUIRED_LATER_METHOD outside the owner topic; "
            "they are admissible only for the exact row whose placement "
            "contract cites them. Reference-only and incidental relationships "
            "are excluded. They supplement, but never replace, native evidence "
            "from the certified owner topic."
        ),
        "advanced_placement_rule": (
            "START FROM THE BOOK. A concept belongs to the topic whose section "
            "actually teaches it. That is the default and it is usually the "
            "answer: if the source teaches the Greek War of Independence in "
            "section 3, the concept is a section 3 concept. Move it only when "
            "understanding the claim genuinely REQUIRES a later topic's method "
            "or framework - not because it cites, compares with, alludes to, or "
            "sits near earlier material. Citing an earlier topic is a "
            "reference, and references never relocate a concept. "
            "When a claim falls dominantly under two concepts in the same "
            "topic, it belongs to that topic's culmination concept rather than "
            "being pushed into another topic. "
            "Placement follows teaching order among genuinely required topics. "
            "An atomic claim belongs to the latest topic whose knowledge, "
            "method, or interpretive framework is necessary to understand or "
            "perform it. A foundational-looking claim may therefore belong to "
            "a later topic, but touching, mentioning, or physically appearing "
            "near that topic never establishes ownership. Do not push a concept "
            "back merely because much of its prerequisite evidence appears "
            "earlier in the book. "
            "EXCEPTION - retrospective reference: when the later topic only "
            "mentions or illustrates the earlier one, rather than the concept "
            "needing this topic's method to be understood, the concept stays "
            "in the earlier topic and this topic instead gains its own concept "
            "about the illustration. Test the direction of dependence: if "
            "understanding the concept requires this topic, place it here; if "
            "this topic merely refers back to it, leave it where it is taught. "
            "Chronological or thematic chapters refer backwards often, so "
            "later in the book does not by itself mean later in teaching."
        ),
        "repair_route": (
            "If the claim genuinely teaches two topics at once, SPLIT it and "
            "keep BOTH parts: the earlier topic keeps a concept covering the "
            "foundational idea on its own terms, and this later topic gains a "
            "concept covering how that idea behaves under this topic's method. "
            "Splitting must never leave either topic without its concept - do "
            "not delete the foundational concept when promoting the advanced "
            "one, and do not leave the advanced behaviour untaught by keeping "
            "everything in the earlier topic. Use move only when the whole "
            "claim belongs elsewhere. Never retire or delete a concept because "
            "grounding convergence is difficult; refine it to its smallest "
            "source-supported objective or fail closed."
        ),
    }
    value["original_pdf_visual_page_ids"] = [
        f"PDF-PAGE-{number:04d}" for number in page_numbers
    ]
    return value


def _ground_via_openai(payload: dict[str, Any]) -> dict[str, Any]:
    concept_ids, block_ids = _grounding_schema_ids(payload)
    pages, page_numbers = phase37._visual_evidence_pages(payload)
    augmented = _augment_grounding_payload(
        payload,
        page_numbers=page_numbers,
    )
    system = (
        "You are the Aegis Phase 3.8 exact source-grounding mapper. Ground only "
        "each source_claim to the smallest sufficient set of supplied opaque "
        "source_block_ids. Blocks marked native_topic are ordinary evidence. "
        "Blocks marked previous_topic_boundary or next_topic_boundary are a "
        "bounded recovery window for converter/page-order drift and may be used "
        "only when their visible content clearly continues the target academic "
        "topic. Blocks marked prerequisite_topic_evidence come from an earlier "
        "topic this one builds on: cite them when they support the prerequisite "
        "part of the claim, but never use them to establish this topic's "
        "ownership. Blocks marked certified_required_topic_evidence are exact "
        "cross-topic evidence authorized by this row's accepted necessary "
        "relationships; never transfer them to another row. Require at least "
        "one native_topic block that supports "
        "knowledge, a method, or an interpretive framework from this topic "
        "which is necessary to understand or perform the atomic claim. Mere "
        "mention, chronology, physical evidence location, or an optional later "
        "method is insufficient. Reject a mapping supported only by prerequisite "
        "blocks. One exception runs the other way: "
        "when this topic merely mentions or illustrates the earlier material "
        "rather than being needed to understand it, the concept belongs to the "
        "earlier topic and this one gets its own concept about the "
        "illustration. Never use boundary or "
        "prerequisite evidence to conceal a genuinely cross-topic or "
        "over-merged concept. If the claim belongs elsewhere or needs narrowing or "
        "splitting, return a low-confidence mapping and explain that topology "
        "repair is required. Figure captions and supplied original PDF pages are "
        "authoritative visual evidence. Do not require textbook support for "
        "Achieving Mastery, learner analysis, Types, hubs, or other generated "
        "pedagogy. Use only supplied IDs, do not rewrite text, and return every "
        "requested concept exactly once. On retries, repair only unresolved IDs "
        "using critic_feedback and previous_grounding. If human_resolutions is "
        "supplied, include its selected verified evidence or follow its custom "
        "instruction exactly, then return the ordinary proposal for independent "
        "criticism; the human direction is not verification."
    )
    return phase22._openai_multimodal_json(
        system=system,
        prompt=json.dumps(augmented, ensure_ascii=False, indent=2),
        pages=pages,
        response_schema=phase31._grounding_schema(concept_ids, block_ids),
        purpose="concept_mapping",
        max_tokens=config.OPENAI_MAX_OUTPUT_TOKENS,
        single_attempt=bool(payload.get("human_resolutions")),
    )


def _critic_via_openai(payload: dict[str, Any]) -> dict[str, Any]:
    concept_ids, _block_ids = _grounding_schema_ids(payload)
    pages, page_numbers = phase37._visual_evidence_pages(payload)
    augmented = _augment_grounding_payload(
        payload,
        page_numbers=page_numbers,
    )
    system = (
        "You are the independent Aegis Phase 3.8 exact-grounding critic. Verify "
        "that every source_claim is fully and visibly supported by the proposed "
        "smallest sufficient block set. A selected adjacent boundary block is "
        "valid only when its content clearly belongs with the target topic and "
        "repairs local source-order drift. A selected prerequisite_topic_evidence "
        "block may support the prerequisite part of the explanation, but it "
        "cannot establish this topic's ownership. A selected "
        "certified_required_topic_evidence block must be authorized by this "
        "exact row's accepted necessary relationship. Accept the mapping only when "
        "at least one native_topic block supports knowledge, a method, or an "
        "interpretive framework from this topic which is necessary to understand "
        "or perform the atomic claim. Mere mention, chronology, physical "
        "evidence location, or an optional later method is insufficient. Apply "
        "the retrospective-reference "
        "exception: when this topic only mentions or illustrates the earlier "
        "material rather than being needed to understand it, reject the "
        "placement so the concept returns to the topic that teaches it. "
        "Reject also when it is supported only by prerequisite blocks, when the "
        "boundary blocks instead show that the concept belongs to the adjacent "
        "topic, when the claim over-merges separate ideas, or when a selected "
        "Figure is the wrong visual. In that case state whether topology should "
        "move, refine, or split the row, preferring split - advanced part "
        "here, foundational part in the earlier topic - when the claim genuinely "
        "teaches both. Figure captions and supplied original PDF pages "
        "are authoritative. Do not demand source support for mastery, learner "
        "analysis, Types, hubs, keywords, or parent labels. Never recommend "
        "retirement merely to make convergence finish. Put every concept ID "
        "in exactly one accepted or rejected list. Verification requires all "
        "accepted, none rejected, confidence at least "
        f"{confidence_policy.threshold_text()}, and no issues. Do not rewrite "
        "proposals."
    )
    return phase22._openai_multimodal_json(
        system=system,
        prompt=json.dumps(augmented, ensure_ascii=False, indent=2),
        pages=pages,
        response_schema=phase31._critic_schema(concept_ids),
        purpose="concept_mapping",
        max_tokens=config.OPENAI_MAX_OUTPUT_TOKENS,
        single_attempt=bool(payload.get("human_resolutions")),
    )


def _apply_proposals(
    records: list[dict[str, Any]],
    *,
    proposals: dict[str, dict[str, Any]],
    index_by_id: dict[str, int],
    candidates: list[dict[str, Any]],
) -> None:
    topic_by_block = {
        str(row.get("block_id") or ""): str(row.get("topic_id") or "")
        for row in candidates
        if isinstance(row, dict) and str(row.get("block_id") or "")
    }
    relation_by_block = {
        str(row.get("block_id") or ""): str(
            row.get("boundary_relation") or ""
        )
        for row in candidates
        if isinstance(row, dict) and str(row.get("block_id") or "")
    }
    for concept_id, proposal in proposals.items():
        index = index_by_id[concept_id]
        record = records[index]
        raw_contract = record.get(
            grounding_certificate.PLACEMENT_CONTRACT_FIELD
        )
        if not isinstance(raw_contract, dict) or not raw_contract.get(
            "certified"
        ):
            continue
        try:
            contract = grounding_certificate.verify_placement_contract(record)
        except grounding_certificate.GroundingCertificateError as exc:
            raise ValueError(
                "failed exact source-block grounding before freeze: "
                f"{concept_id} has an invalid certified placement contract: "
                f"{exc}"
            ) from exc
        assert contract is not None
        owner_topic_id = str(contract.get("owner_topic_id") or "")
        selected_ids = {
            str(block_id)
            for block_id in proposal.get("source_block_ids") or []
            if str(block_id)
        }
        native_owner_ids = {
            block_id
            for block_id in selected_ids
            if topic_by_block.get(block_id, "") == owner_topic_id
            and relation_by_block.get(block_id, "") in {"", "native_topic"}
        }
        if not native_owner_ids:
            raise ValueError(
                "failed exact source-block grounding before freeze: "
                f"{concept_id} selected no native block from its certified "
                f"owner topic {owner_topic_id}"
            )
        # A block from an earlier topic is a *reference*, not a placement claim.
        # Textbooks are cumulative: a later section compares against, alludes
        # to, or builds on earlier material constantly, and citing it as
        # evidence says nothing about where the concept belongs.
        #
        # This used to reject any cross-topic block the placement contract had
        # not declared under a NECESSARY relationship, which stopped whole runs
        # for legitimate references -- a Greek War of Independence concept in
        # "The Age of Revolutions" citing a paragraph from "The Making of
        # Nationalism in Europe" ended a live run after three repair attempts.
        # Phase 3.2 decides placement, and it does not enumerate every earlier
        # paragraph a concept might reasonably point at; requiring it to was a
        # contract mismatch between the two phases, not a grounding defect.
        #
        # Placement is still enforced, by the check above: the concept must
        # cite at least one native block from its own topic, so its principal
        # claim is grounded where the textbook teaches it. Anything beyond that
        # is supporting reference and is recorded, not refused.
        selected_cross_topic_ids = sorted(
            str(block_id)
            for block_id in proposal.get("source_block_ids") or []
            if topic_by_block.get(str(block_id), "") != owner_topic_id
        )
        if selected_cross_topic_ids:
            progress.log(
                f"{concept_id} cites {len(selected_cross_topic_ids)} block(s) "
                f"from earlier topics as supporting reference "
                f"({', '.join(selected_cross_topic_ids)}); it stays in its "
                f"own topic {owner_topic_id}, where the source teaches it.",
                level="info",
            )

    phase31._PHASE38_ORIGINAL_APPLY_PROPOSALS(
        records,
        proposals=proposals,
        index_by_id=index_by_id,
        candidates=candidates,
    )
    relation_by_block: dict[str, str] = {}
    target_topic_by_concept = {
        concept_id: str(records[index].get("_semantic_topic_id") or "")
        for concept_id, index in index_by_id.items()
    }
    for concept_id, proposal in proposals.items():
        index = index_by_id[concept_id]
        target_topic_id = target_topic_by_concept.get(concept_id, "")
        boundary_ids = [
            block_id
            for block_id in proposal.get("source_block_ids") or []
            if topic_by_block.get(str(block_id), target_topic_id) != target_topic_id
        ]
        if boundary_ids:
            for block_id in boundary_ids:
                source_topic = topic_by_block.get(str(block_id), "")
                relation_by_block[str(block_id)] = source_topic
            records[index]["_source_grounding_contract"] = (
                "api-verified-boundary-aware-source-block-ids"
            )
            records[index]["_source_grounding_boundary_blocks"] = [
                {
                    "block_id": str(block_id),
                    "source_topic_id": relation_by_block.get(str(block_id), ""),
                    "target_topic_id": target_topic_id,
                }
                for block_id in boundary_ids
            ]
        else:
            records[index].pop("_source_grounding_boundary_blocks", None)


def _ground_concepts_with_placement_context(
    records: list[dict[str, Any]],
    *args: Any,
    **kwargs: Any,
) -> list[dict[str, Any]]:
    """Expose the exact candidate row contracts to topic candidate selection."""

    token = _ACTIVE_GROUNDING_RECORDS.set(copy.deepcopy(records))
    try:
        return phase31._PHASE38_ORIGINAL_GROUND_CONCEPTS(
            records,
            *args,
            **kwargs,
        )
    finally:
        _ACTIVE_GROUNDING_RECORDS.reset(token)


def _capture_repaired_topology(*args: Any, **kwargs: Any):
    result = phase32._PHASE38_ORIGINAL_APPLY_DECISIONS(*args, **kwargs)
    _LAST_REPAIRED_TOPOLOGY.set(copy.deepcopy(result))
    return result


def _original_concept_directory(
    records: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    directory: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(records):
        title = str(row.get("concept_title") or row.get("concept") or "")
        if cr.is_culmination(title):
            continue
        concept_id = f"TOPOLOGY-CONCEPT-{index + 1:04d}"
        directory[concept_id] = {
            "concept_title": title,
            "topic": str(row.get("topic") or ""),
            "source_claim": phase31._description_source_claim(row),
        }
    return directory


def _feedback_for_failure(
    exc: Exception,
    *,
    original_records: list[dict[str, Any]],
    repaired_records: list[dict[str, Any]] | None,
    repeated: bool,
    final_atomisation: bool = False,
) -> dict[str, str]:
    origins: dict[str, str] = {}
    if repaired_records:
        origins = phase33._grounding_feedback_origins(
            exc,
            records=repaired_records,
        )
    directory = _original_concept_directory(original_records)
    if not origins:
        topic_match = phase33._GROUNDING_TOPIC_RE.search(str(exc))
        if topic_match:
            topic_key = _normal(topic_match.group("topic"))
            origins = {
                concept_id: str(exc)[:12000]
                for concept_id, row in directory.items()
                if _normal(row.get("topic")) == topic_key
            }
    if not origins:
        origins = {
            concept_id: str(exc)[:12000]
            for concept_id in directory
        }

    instruction = (
        "EXACT SOURCE-BLOCK GROUNDING FAILED. Reconsider this original concept "
        "using all canonical topic evidence, adjacent boundary evidence, Figure "
        "captions, and supplied original PDF pages. Do not repeat an unchanged "
        "placement and source_claim when the diagnostic says one or more clauses "
        "lack support. Preserve the concept only if all clauses are now supported "
        "in the academically correct topic; otherwise move, refine, or split it "
        "under the verified topology contract. Never retire or return zero "
        "concept segments."
    )
    if repeated:
        instruction += (
            " This failure signature has repeated. Returning the same effective "
            "title/topic/Description is forbidden; choose a materially different "
            "evidence-supported resolution."
        )
    if final_atomisation:
        instruction += (
            " FINAL QUALITY-FIRST VERIFICATION PASS: retirement, deletion, an "
            "empty segment list, and another unchanged proposal are forbidden. "
            "If the source supports multiple independent clauses, atomise them "
            "with split into the smallest independently teachable concepts. If "
            "only one clause is supportable, refine the row into exactly one "
            "minimal source-grounded concept and remove every unsupported "
            "qualification. If the original over-inference has no support as "
            "written, use refine to create the smallest relevant teaching "
            "concept explicitly supported by the supplied canonical evidence. "
            "Every resulting concept will receive ordinary independent topology "
            "criticism and exact source-block grounding; do not invent wording, "
            "choose an arbitrary block, or weaken either verifier."
        )

    feedback: dict[str, str] = {}
    for concept_id, diagnostic in origins.items():
        row = directory.get(concept_id, {})
        feedback[concept_id] = (
            f"{instruction}\n"
            f"Original concept title: {row.get('concept_title') or '(unknown)'}\n"
            f"Original topic: {row.get('topic') or '(unknown)'}\n"
            f"Original source claim: {row.get('source_claim') or '(unknown)'}\n"
            f"Exact grounding diagnostic: {str(diagnostic)[:12000]}"
        )
    return feedback


def _candidate_sha256(
    records: list[dict[str, Any]] | None,
    *,
    fallback: list[dict[str, Any]],
) -> str:
    """Hash the actual candidate independently verified by exact grounding."""

    candidate = records if isinstance(records, list) and records else fallback
    return phase3._sha256_json(phase31._json_safe(candidate))


def _grounding_issue_key(exc: Exception) -> str:
    """Return a candidate-independent identity for one grounding issue.

    Exact-grounding diagnostics retain stable CONCEPT-GROUND identities while
    descriptions and evidence lists can legitimately change between repair
    candidates.  Keying those diagnostics by their stable unit set prevents a
    wording change from manufacturing a fresh budget.  Synthetic lineage
    failures have no unit ID, so their normalized message is the stable issue.
    """

    message = _normal(str(exc))
    unit_ids = sorted({
        match.group(0).upper()
        for match in _GROUNDING_ISSUE_ID_RE.finditer(str(exc))
    })
    topic_match = phase33._GROUNDING_TOPIC_RE.search(str(exc))
    topic = _normal(topic_match.group("topic")) if topic_match else ""
    material: dict[str, Any] = {
        "failure_type": type(exc).__name__,
        "unit_ids": unit_ids,
    }
    if topic:
        material["topic"] = topic
    if not unit_ids:
        material["message"] = message
    return phase3._sha256_json(material)


def _candidate_preserves_every_origin(
    original_records: list[dict[str, Any]],
    result: list[dict[str, Any]],
) -> bool:
    """Prove a candidate refined/split rows instead of substituting one."""

    expected = set(_original_concept_directory(original_records))
    actual = {
        str(row.get("_phase32_origin_concept_id") or "")
        for row in result
        if isinstance(row, dict)
        and str(row.get("_phase32_origin_concept_id") or "")
        and not cr.is_culmination(
            str(row.get("concept_title") or row.get("concept") or "")
        )
    }
    # Count parity cannot prove identity: a provider could drop one original
    # concept and replace it with an unrelated row. Every non-culmination
    # output therefore has to carry the Phase 3.2 origin seal, including test
    # and extension boundaries.
    return bool(actual) and expected <= actual


def _final_turn_for_claim(state: dict[str, Any]) -> bool:
    """Is the dispatch in flight the issue's last allowed turn?

    A pause raised before the final turn still deserves the upstream
    agent-first repair ladder, which can fix the concept properly.  A pause
    raised *on* the final turn has nothing left above it, so the only choices
    are a deterministic disposition or a stopped run.
    """

    claimed = str(state.get("dispatch_issue_key") or "")
    if not claimed:
        return bool(state.get("final_verification_pending"))
    bucket = _normalized_issue_bucket(
        (state.get("issue_buckets") or {}).get(claimed)
    )
    return bool(
        bucket.get("final_verification_pending")
        or bucket.get("status") == "final_verification_pending"
    )


def _diagnostic_concept_titles(
    exc: Exception | None,
    candidate: list[dict[str, Any]],
    repaired: list[dict[str, Any]] | None,
) -> list[str] | None:
    """Return the titles in ``candidate`` a grounding diagnostic named.

    ``None`` means "the diagnostic identified nobody in this candidate",
    which the disposition reads as "consider every row".  That is the safe
    default in both directions: narrowing an already-supported row is a
    no-op, whereas a selection that misses the real offender would leave the
    ungroundable claim in the workbook untouched.
    """

    if exc is None:
        return None
    origins: dict[str, str] = {}
    for source in (repaired, candidate):
        if not source:
            continue
        try:
            origins = phase33._grounding_feedback_origins(
                exc,
                records=source,
            )
        except Exception:  # pragma: no cover - diagnostics must never stop us
            origins = {}
        if origins:
            break
    if not origins:
        return None

    titles: list[str] = []
    directory = _original_concept_directory(candidate)
    for row in candidate:
        if not isinstance(row, dict):
            continue
        if str(row.get("_phase32_origin_concept_id") or "") in origins:
            title = str(row.get("concept_title") or row.get("concept") or "")
            if title:
                titles.append(title)
    # A candidate that lost its origin seals still has to be reachable, so
    # fall back to the positional directory the diagnostic IDs index into.
    for concept_id in origins:
        row = directory.get(concept_id)
        if row and str(row.get("concept_title") or ""):
            titles.append(str(row["concept_title"]))
    return list(dict.fromkeys(titles)) or None


def _terminal_disposition(
    state: dict[str, Any],
    *,
    scope: str,
    records: list[dict[str, Any]],
    repaired: list[dict[str, Any]] | None = None,
    exc: Exception | None = None,
    reason: str,
) -> list[dict[str, Any]]:
    """Produce the final candidate deterministically. Never raises.

    This is the last rung of the ladder.  It makes no provider request, so a
    sealed ``request_started`` claim stays sealed and nothing is paid for
    twice; it removes no concept, so the topology the user approved survives;
    and it returns records, so the caller reaches the output workbook.

    The rewrite is a pure function of the candidate and the canonical source,
    which is what lets Resume repeat it instead of re-earning it.
    """

    candidate = records
    if (
        isinstance(repaired, list)
        and repaired
        and _candidate_preserves_every_origin(records, repaired)
    ):
        # The last repaired topology is strictly closer to grounded than the
        # untouched input, provided it still carries every original lineage.
        candidate = repaired

    graph = phase3.active_graph()
    canonical = (phase3.active_session() or {}).get("canonical") or {}
    try:
        disposed, audit = evidence_narrowing.narrow_records(
            candidate,
            graph=graph,
            canonical=canonical,
            concept_titles=_diagnostic_concept_titles(
                exc, candidate, repaired
            ),
        )
    except Exception as narrowing_error:  # pragma: no cover - last resort
        # Even a defect inside the disposition must not become a stopped run.
        # Shipping the unmodified candidate is worse than narrowing it and
        # better than producing nothing at all, so it is what happens.
        disposed = [copy.deepcopy(row) for row in candidate]
        audit = evidence_narrowing.NarrowingAudit()
        progress.log(
            "Phase 3.8 evidence narrowing failed internally "
            f"({narrowing_error}); the unmodified final candidate was "
            "released so the run still produces its workbook.",
            level="warning",
        )

    diagnostic = (
        f"{reason} The run continued: {audit.summary()}. No concept was "
        "retired or deleted."
    )
    if exc is not None:
        diagnostic += f" Last diagnostic: {exc}"

    issue_key = str(state.get("active_issue_key") or "")
    bucket = (
        copy.deepcopy((state.get("issue_buckets") or {}).get(issue_key))
        if issue_key
        else None
    )
    if isinstance(bucket, dict):
        bucket["status"] = "disposed"
        bucket["final_verification_pending"] = False
        bucket["terminal_reason"] = diagnostic[:4000]
        _mirror_active_issue(state, issue_key, bucket)
    else:
        state["status"] = "disposed"
        state["final_verification_pending"] = False
        state["terminal_reason"] = diagnostic[:4000]
    state["scope"] = scope
    state["disposition"] = evidence_narrowing.DISPOSITION_VERSION
    # A returned decision or an unobserved request is settled the moment the
    # disposition takes over: nothing further will be dispatched for it, so
    # holding the claim would only block a future run for no benefit.
    _clear_dispatch_claim(state)
    _persist_convergence_state(state)

    for row in audit.changed_rows:
        progress.log(
            f"Phase 3.8 terminal disposition {row.kind} "
            f"'{row.concept_title}' under '{row.topic}': {row.note}",
            level="warning",
        )
    progress.log(diagnostic, level="warning")
    return disposed


def _phase32_adjudicate_with_targeted_convergence(
    original: Callable[..., list[dict[str, Any]]],
    records: list[dict[str, Any]],
    *args: Any,
    **kwargs: Any,
) -> list[dict[str, Any]]:
    if any(
        kwargs.get(name) is not None
        for name in ("provider", "critic", "grounding_provider", "grounding_critic")
    ):
        return original(records, *args, **kwargs)

    passes = _max_convergence_passes()
    scope = _convergence_scope_key(records)
    state = _convergence_state(scope)
    if state.get("dispatch_status") in {
        "request_started", "decision_returned"
    }:
        status = str(state.get("dispatch_status") or "")
        # An unreconciled claim still forbids a duplicate paid request, and
        # the disposition makes none. Refusing to continue would leave the
        # job parked on a claim only an operator could clear.
        return _terminal_disposition(
            state,
            scope=scope,
            records=records,
            reason=(
                f"Phase 3.8 found a durable provider {status} claim that was "
                "never reconciled. It issued no duplicate paid request and "
                "disposed of the candidate deterministically instead."
            ),
        )
    source_contract_hash = ""
    graph = phase3.active_graph()
    if isinstance(graph, dict):
        source_contract_hash = str(graph.get("source_contract_hash") or "")
    if (
        state.get("source_contract_hash")
        and source_contract_hash
        and state.get("source_contract_hash") != source_contract_hash
    ):
        # A source-contract change is a different verification workspace. The
        # checkpoint owner persists this replacement before any model call.
        state = _fresh_convergence_state(
            scope=scope,
            source_contract_hash=source_contract_hash,
        )
        _persist_convergence_state(state)

    base_candidate_sha256 = _candidate_sha256(None, fallback=records)
    saved_base_candidate = str(state.get("base_candidate_sha256") or "")
    if saved_base_candidate and saved_base_candidate != base_candidate_sha256:
        # A durable checkpoint repair produced a materially different input.
        # It receives its own bounded verification budget; an exhausted old
        # candidate must not poison the repaired checkpoint.
        state = _fresh_convergence_state(
            scope=scope,
            source_contract_hash=source_contract_hash,
        )
        state["base_candidate_sha256"] = base_candidate_sha256
        state["candidate_sha256"] = base_candidate_sha256
        _persist_convergence_state(state)
    initial_changed = False
    if state.get("source_contract_hash") != source_contract_hash:
        state["source_contract_hash"] = source_contract_hash
        initial_changed = True
    if state.get("base_candidate_sha256") != base_candidate_sha256:
        state["base_candidate_sha256"] = base_candidate_sha256
        initial_changed = True
    if not state.get("candidate_sha256"):
        state["candidate_sha256"] = base_candidate_sha256
        initial_changed = True
    if initial_changed:
        _persist_convergence_state(state)

    if state.get("status") in _TERMINAL_STATUSES:
        # Resume reaches the same terminal rung. Narrowing is deterministic,
        # so it reproduces the earlier output without replaying a paid pass.
        return _terminal_disposition(
            state,
            scope=scope,
            records=records,
            reason=(
                "Phase 3.8 had already spent its quality-first attempt budget "
                "for this unchanged candidate. Resume replayed no paid "
                "evidence check and reproduced the deterministic disposition."
            ),
        )

    suppressed_resolutions = {
        str(value)
        for value in state.get("suppressed_resolution_ids") or []
        if str(value)
    }
    feedback: dict[str, str] = dict(state.get("feedback") or {})
    repaired_token = _LAST_REPAIRED_TOPOLOGY.set(None)
    try:
        while True:
            if state.get("dispatch_status") in {
                "request_started", "decision_returned"
            }:
                return _terminal_disposition(
                    state,
                    scope=scope,
                    records=records,
                    repaired=_LAST_REPAIRED_TOPOLOGY.get(),
                    reason=(
                        "Phase 3.8 would have had to replay an unresolved "
                        "provider dispatch claim to continue converging. It "
                        "disposed of the candidate deterministically instead."
                    ),
                )
            transport_claimed = False

            def claim_transport() -> None:
                """Persist once, after local validation and before transport."""

                nonlocal transport_claimed
                if transport_claimed:
                    return
                if state.get("dispatch_status") in {
                    "request_started", "decision_returned"
                }:
                    raise Phase38ConvergenceExhausted(
                        "Phase 3.8 will not cross an unresolved provider "
                        "dispatch claim."
                    )
                state["dispatch_sequence"] = int(
                    state.get("dispatch_sequence") or 0
                ) + 1
                state["dispatch_status"] = "request_started"
                state["dispatch_candidate_sha256"] = str(
                    state.get("candidate_sha256")
                    or state.get("base_candidate_sha256")
                    or base_candidate_sha256
                )
                state["dispatch_issue_key"] = str(
                    state.get("active_issue_key") or ""
                )
                transport_claimed = True
                # Exactly-once boundary: the actual OpenAI transport invokes
                # this callback immediately before its first request byte.
                _persist_convergence_state(state)

            _LAST_REPAIRED_TOPOLOGY.set(None)
            feedback_token = phase33._EXTERNAL_GROUNDING_FEEDBACK.set(feedback)
            try:
                with phase22.openai_transport_claim(claim_transport):
                    with early_gate.suppress_resolution_ids(
                        suppressed_resolutions
                    ):
                        # Every pass starts from the complete original topology.
                        # A provider cannot mutate one pass into a reduced next pass.
                        result = original(copy.deepcopy(records), *args, **kwargs)
                if not _candidate_preserves_every_origin(records, result):
                    loss = ValueError(
                        "Phase 3.8 candidate failed exact source-block "
                        "grounding before freeze because it omitted one or "
                        "more original concept lineages"
                    )
                    # Treat a downstream retirement exactly like another
                    # independently rejected candidate. It receives bounded
                    # refine/split feedback and can never manufacture success.
                    raise loss
                # Exact grounding passed. Remove every suppression, failure
                # signature, and final-pass flag rather than leaving poison for
                # a later run of the same source.
                _clear_convergence_state(scope)
                return result
            except semantic_recovery.HumanDecisionRequired as exc:
                if not transport_claimed:
                    # A local semantic gate paused before transport. It owns no
                    # paid-request ambiguity and must not poison Resume.
                    raise
                # The provider returned a recognized decision, but the outer
                # orchestration layer has not saved that pause yet. Retain a
                # bound claim so a crash in between cannot replay the paid
                # request. `_persist_pending_human_decision` clears it only in
                # the same commit that installs this exact decision identity.
                pending = exc.pending_decision
                decision_id = str(pending.get("decision_id") or "")
                context_hash = str(pending.get("context_hash") or "")
                if (
                    not re.fullmatch(
                        r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}",
                        decision_id,
                    )
                    or not re.fullmatch(r"[0-9a-f]{64}", context_hash)
                ):
                    # The outcome is not safe to bridge into the durable
                    # decision schema, so there is no pause to raise and no
                    # repair left to try. Dispose rather than stop.
                    return _terminal_disposition(
                        state,
                        scope=scope,
                        records=records,
                        repaired=_LAST_REPAIRED_TOPOLOGY.get(),
                        exc=exc,
                        reason=(
                            "Phase 3.8 received an invalid decision identity "
                            "and issued no further provider request."
                        ),
                    )
                if _final_turn_for_claim(state):
                    # The upstream agent-first ladder has already had every
                    # bounded turn for this issue. Raising the pause again
                    # could only end in a stop or a park, and neither may
                    # happen while a deterministic disposition exists.
                    return _terminal_disposition(
                        state,
                        scope=scope,
                        records=records,
                        repaired=_LAST_REPAIRED_TOPOLOGY.get(),
                        exc=exc,
                        reason=(
                            "Phase 3.8 reached a semantic decision on its "
                            "final verification turn, after the autonomous "
                            "repair ladder was spent."
                        ),
                    )
                state["dispatch_status"] = "decision_returned"
                state["dispatch_decision_id"] = decision_id
                state["dispatch_decision_context_hash"] = context_hash
                _persist_convergence_state(state)
                raise
            except ValueError as exc:
                if not transport_claimed:
                    # Deterministic pre-transport validation is ordinary
                    # actionable failure, not an unknown paid outcome.
                    raise
                message = str(exc)
                if "failed exact source-block grounding before freeze" not in message:
                    # An unrecognized outcome cannot prove whether a paid byte
                    # was sent, so no further request is made. The disposition
                    # makes none either, which is what lets the run continue.
                    return _terminal_disposition(
                        state,
                        scope=scope,
                        records=records,
                        repaired=_LAST_REPAIRED_TOPOLOGY.get(),
                        exc=exc,
                        reason=(
                            "Phase 3.8 met an unrecognized provider outcome "
                            "and made no further request."
                        ),
                    )

                observed_issue_key = _grounding_issue_key(exc)
                buckets = dict(state.get("issue_buckets") or {})
                claimed_issue_key = str(
                    state.get("dispatch_issue_key") or ""
                )
                claimed_bucket = _normalized_issue_bucket(
                    buckets.get(claimed_issue_key)
                )
                final_dispatch = bool(
                    claimed_issue_key
                    and (
                        claimed_bucket.get("final_verification_pending")
                        or claimed_bucket.get("status")
                        == "final_verification_pending"
                    )
                )
                # A final verification is the last turn for the candidate, not
                # an opportunity for its output to manufacture a new issue
                # bucket. Any verifier failure on that turn exhausts the issue
                # whose final candidate was dispatched.
                issue_key = (
                    claimed_issue_key if final_dispatch else observed_issue_key
                )
                if state.get("legacy_unscoped_issue") and not buckets:
                    # Version-1 checkpoints had no issue identity. Charge their
                    # already-spent budget to the first observed matching issue
                    # instead of silently granting a fresh allowance.
                    bucket = _legacy_bucket_from_state(state)
                elif final_dispatch:
                    bucket = claimed_bucket
                else:
                    bucket = _normalized_issue_bucket(buckets.get(issue_key))
                if issue_key not in buckets and len(buckets) >= _MAX_CONVERGENCE_ISSUES:
                    return _terminal_disposition(
                        state,
                        scope=scope,
                        records=records,
                        repaired=_LAST_REPAIRED_TOPOLOGY.get(),
                        exc=exc,
                        reason=(
                            "Phase 3.8 reached its bounded distinct-issue "
                            f"safety limit of {_MAX_CONVERGENCE_ISSUES} in one "
                            "job and made no further provider request."
                        ),
                    )

                final_verification_pending = bool(
                    bucket.get("final_verification_pending")
                    or bucket.get("status") == "final_verification_pending"
                )
                attempts = int(bucket.get("attempts") or 0) + 1
                signature = phase3._sha256_json(
                    {
                        "message": _normal(message),
                        "issue_key": issue_key,
                    }
                )
                signatures: dict[str, int] = dict(
                    bucket.get("signatures") or {}
                )
                signatures[signature] = signatures.get(signature, 0) + 1
                repeated = signatures[signature] > 1
                if (
                    isinstance(exc, early_gate.TopologyRepairRequired)
                    and exc.decision_id
                ):
                    # A drift-triggered repair carries no saved resolution to
                    # suppress; only a human-directed one does.
                    suppressed_resolutions.add(exc.decision_id)
                repaired = _LAST_REPAIRED_TOPOLOGY.get()
                candidate_sha256 = _candidate_sha256(
                    repaired,
                    fallback=records,
                )
                history = list(bucket.get("candidate_history") or [])
                if candidate_sha256 not in history:
                    history.append(candidate_sha256)
                history = history[:_max_convergence_candidates()]
                bucket.update({
                    "attempts": attempts,
                    "signatures": signatures,
                    "candidate_history": history,
                })
                state["suppressed_resolution_ids"] = sorted(
                    suppressed_resolutions
                )
                state["candidate_sha256"] = candidate_sha256

                if final_verification_pending:
                    _mirror_active_issue(state, issue_key, bucket)
                    # Every bounded repair is spent, including the final
                    # atomisation pass. What is left is the concept's own
                    # evidence, so the concept is rewritten to match it.
                    return _terminal_disposition(
                        state,
                        scope=scope,
                        records=records,
                        repaired=repaired,
                        exc=exc,
                        reason=(
                            "Phase 3.8 exhausted its bounded topology and "
                            "grounding convergence, and the final "
                            "atomisation/minimal-source-concept candidate "
                            "still failed independent exact source-block "
                            "grounding."
                        ),
                    )

                budget_spent = (
                    attempts >= passes
                    or len(history) >= _max_convergence_candidates()
                )
                feedback = _feedback_for_failure(
                    exc,
                    original_records=records,
                    repaired_records=repaired,
                    repeated=repeated,
                    final_atomisation=budget_spent,
                )
                bucket["feedback"] = feedback
                bucket["terminal_reason"] = ""
                if budget_spent:
                    bucket["final_verification_pending"] = True
                    bucket["status"] = "final_verification_pending"
                    _mirror_active_issue(state, issue_key, bucket)
                    _clear_dispatch_claim(state)
                    # Rejection, next feedback, suppression, and final-pass
                    # disposition are one durable transition. There is no
                    # attempts-with-stale-feedback crash window.
                    _persist_convergence_state(state)
                    progress.log(
                        "Phase 3.8 spent its ordinary convergence budget. "
                        "The complete original topology is receiving one final "
                        "atomisation/minimal-source-concept pass; retirement "
                        "and deletion are forbidden, and the resulting candidate "
                        "must still pass independent exact grounding.",
                        level="warning",
                    )
                    continue

                bucket["status"] = "active"
                bucket["final_verification_pending"] = False
                _mirror_active_issue(state, issue_key, bucket)
                _clear_dispatch_claim(state)
                _persist_convergence_state(state)
                progress.log(
                    "Phase 3.8 mapped exact grounding rejection back to "
                    f"{len(feedback)} original topology concept(s); only "
                    "those concepts will be reconsidered in convergence "
                    f"pass {attempts + 1}/{passes}.",
                    level="warning",
                )
                continue
            except Phase38ConvergenceExhausted:
                raise
            except Exception as exc:
                # An unknown failure cannot establish whether a paid call
                # began or completed, so none is retried. It also cannot be
                # allowed to end the job: this is the catch-all that used to
                # turn any unexpected defect anywhere below Phase 3.8 into a
                # run with no workbook. Whether a provider request had
                # actually started only changes what the log says.
                return _terminal_disposition(
                    state,
                    scope=scope,
                    records=records,
                    repaired=_LAST_REPAIRED_TOPOLOGY.get(),
                    exc=exc,
                    reason=(
                        "Phase 3.8 met an unrecognized failure "
                        f"({type(exc).__name__}) "
                        + (
                            "after claiming a provider request"
                            if transport_claimed
                            else "before any provider request began"
                        )
                        + " and retried nothing."
                    ),
                )
            finally:
                phase33._EXTERNAL_GROUNDING_FEEDBACK.reset(feedback_token)
    finally:
        _LAST_REPAIRED_TOPOLOGY.reset(repaired_token)


def _cached_records_have_current_grounding(
    records: list[dict[str, Any]] | None,
) -> bool:
    if not isinstance(records, list) or not records:
        return False
    normal = [
        row
        for row in records
        if isinstance(row, dict)
        and not cr.is_culmination(
            str(row.get("concept_title") or row.get("concept") or "")
        )
    ]
    return bool(normal) and all(
        str(row.get("_source_grounding_version") or "") == _GROUNDING_VERSION
        for row in normal
    )


def _read_cached_records(cache_key: str) -> list[dict[str, Any]] | None:
    records = phase32._PHASE38_ORIGINAL_READ_CACHED_RECORDS(cache_key)
    if records is None:
        return None
    if _cached_records_have_current_grounding(records):
        return records
    progress.log(
        "Ignored a cached Phase 3.2 final topology grounded under an older "
        "source-block contract; verified topology decisions remain reusable.",
        level="warning",
    )
    return None


def install() -> None:
    if getattr(phase31, "_PHASE38_BOUNDARY_GROUNDING_VERSION", 0) >= _CONTRACT_VERSION:
        return

    phase31._PHASE38_ORIGINAL_CANDIDATE_BLOCKS = phase31._candidate_blocks
    phase31._PHASE38_ORIGINAL_GROUND_PROVIDER = phase31._ground_via_openai
    phase31._PHASE38_ORIGINAL_GROUND_CRITIC = phase31._critic_via_openai
    phase31._PHASE38_ORIGINAL_APPLY_PROPOSALS = phase31._apply_proposals
    phase31._PHASE38_ORIGINAL_GROUND_CONCEPTS = phase31.ground_concepts
    phase32._PHASE38_ORIGINAL_APPLY_DECISIONS = phase32._apply_decisions
    phase32._PHASE38_ORIGINAL_READ_CACHED_RECORDS = phase32._read_cached_records
    phase33._PHASE38_ORIGINAL_CONVERGENCE = (
        phase33._phase32_adjudicate_with_convergence
    )

    phase31._GROUNDING_VERSION = _GROUNDING_VERSION
    phase31._candidate_blocks = _candidate_blocks
    phase31._ground_via_openai = _ground_via_openai
    phase31._critic_via_openai = _critic_via_openai
    phase31._apply_proposals = _apply_proposals
    phase31.ground_concepts = _ground_concepts_with_placement_context
    if phase3.ground_concepts is phase31._PHASE38_ORIGINAL_GROUND_CONCEPTS:
        phase3.ground_concepts = _ground_concepts_with_placement_context

    phase32._apply_decisions = _capture_repaired_topology
    phase32._read_cached_records = _read_cached_records
    phase33._phase32_adjudicate_with_convergence = (
        _phase32_adjudicate_with_targeted_convergence
    )

    phase31._PHASE38_BOUNDARY_GROUNDING_VERSION = _CONTRACT_VERSION

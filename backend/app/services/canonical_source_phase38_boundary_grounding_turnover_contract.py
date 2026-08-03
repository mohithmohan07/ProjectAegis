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
  verification pass, then fails closed without deleting any source concept;
* invalidates cached final topology rows grounded under an older contract.

No textbook wording, QID, Figure, or source identity is invented or removed.
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
from . import progress
from . import semantic_confidence_policy as confidence_policy
from . import semantic_recovery

_CONTRACT_VERSION = 2
_GROUNDING_VERSION = "phase3.8-boundary-aware-source-grounding-2"

_LAST_REPAIRED_TOPOLOGY: ContextVar[list[dict[str, Any]] | None] = ContextVar(
    "aegis_phase38_last_repaired_topology",
    default=None,
)

_EXCLUDED_BLOCK_KINDS = frozenset({"layout", "heading", "navigation"})
_MAX_CONVERGENCE_ISSUES = 100
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
    if status not in {"active", "final_verification_pending", "exhausted"}:
        status = "active"
    final_pending = bool(
        value.get("final_verification_pending")
        or status == "final_verification_pending"
    )
    if final_pending and status != "exhausted":
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
        ("prerequisite_topic_evidence", prerequisite),
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
        ],
        "rule": (
            "An adjacent boundary block may be selected only when it visibly "
            "continues the target topic despite converter/page reading-order drift. "
            "It must not be used to keep a concept under the wrong academic topic."
        ),
        "prerequisite_rule": (
            "Blocks marked prerequisite_topic_evidence come from an earlier "
            "topic this one builds on. A concept placed here may rest on them "
            "as heavily as it needs to, including for its main explanation. "
            "The only requirement is that it genuinely involves THIS topic: it "
            "must draw on at least one native_topic block for the part that "
            "makes it belong here. A concept supported entirely by "
            "prerequisite blocks involves no content from this topic at all "
            "and is misplaced: reject it so topology can move it back."
        ),
        "advanced_placement_rule": (
            "Placement follows teaching order. If a concept involves content "
            "from this topic at all, it belongs to THIS later topic, even when "
            "most of what it explains is foundational material from an earlier "
            "one - a teacher can only reach it once this topic is taught. Do "
            "not push such a concept back to the earlier topic on the grounds "
            "that its main idea looks foundational, and do not reject it "
            "because most of its evidence appears earlier in the book. "
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
        "topic this one builds on: cite them as freely as the claim needs, "
        "including for its main explanation. A concept belongs to this later "
        "topic whenever it involves this topic's content at all, even if most "
        "of what it explains is foundational, because teaching only reaches it "
        "here; require only that it draws on at least one native_topic block. "
        "Reject it only when it rests entirely on prerequisite blocks and so "
        "involves nothing from this topic. One exception runs the other way: "
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
        "block may carry as much of the explanation as the claim needs. "
        "Accept the mapping when the concept draws on at least one "
        "native_topic block, treating it as correctly placed advanced material "
        "even when most of its support is prerequisite: teaching reaches such "
        "a concept only in this topic. Apply the retrospective-reference "
        "exception: when this topic only mentions or illustrates the earlier "
        "material rather than being needed to understand it, reject the "
        "placement so the concept returns to the topic that teaches it. "
        "Reject also when it rests entirely on "
        "prerequisite blocks and involves nothing from this topic, when the "
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
    phase31._PHASE38_ORIGINAL_APPLY_PROPOSALS(
        records,
        proposals=proposals,
        index_by_id=index_by_id,
        candidates=candidates,
    )
    topic_by_block = {
        str(row.get("block_id") or ""): str(row.get("topic_id") or "")
        for row in candidates
        if isinstance(row, dict) and str(row.get("block_id") or "")
    }
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


def _terminal_convergence_error(
    state: dict[str, Any],
    *,
    scope: str,
    exc: Exception,
) -> Phase38ConvergenceExhausted:
    """Persist one terminal outcome so Resume cannot replay the final pass."""

    diagnostic = (
        "Phase 3.8 exhausted its bounded topology/grounding convergence and "
        "the final atomisation/minimal-source-concept candidate still failed "
        "independent exact source-block grounding. No concept was retired or "
        f"deleted; the original topology remains intact. Last diagnostic: {exc}"
    )
    public_reason = (
        "Phase 3.8 quality-first attempt budget ended after its final "
        "evidence-check turn. No concept was retired or deleted, and Resume "
        "is blocked for this unchanged candidate."
    )
    issue_key = str(state.get("active_issue_key") or "")
    bucket = (
        copy.deepcopy((state.get("issue_buckets") or {}).get(issue_key))
        if issue_key
        else None
    )
    if isinstance(bucket, dict):
        bucket["status"] = "exhausted"
        bucket["final_verification_pending"] = False
        bucket["terminal_reason"] = diagnostic[:4000]
        _mirror_active_issue(state, issue_key, bucket)
    else:
        state["status"] = "exhausted"
        state["final_verification_pending"] = False
        state["terminal_reason"] = diagnostic[:4000]
    state["scope"] = scope
    _clear_dispatch_claim(state)
    _persist_convergence_state(state)
    return Phase38ConvergenceExhausted(public_reason)


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
        raise Phase38ConvergenceExhausted(
            f"Phase 3.8 found a durable provider {status} claim that has not "
            "been atomically reconciled. It will not issue a duplicate paid "
            "request; operator reconciliation or a materially new checkpoint "
            "is required."
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

    if state.get("status") == "exhausted":
        raise Phase38ConvergenceExhausted(
            "Phase 3.8 quality-first attempt budget already ended for this "
            "unchanged candidate; Resume will not replay the final evidence "
            "check."
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
                raise Phase38ConvergenceExhausted(
                    "Phase 3.8 will not replay an unresolved provider "
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
            # Exactly-once boundary: no provider/critic byte is sent until
            # this claim wins the durable checkpoint CAS.
            _persist_convergence_state(state)
            _LAST_REPAIRED_TOPOLOGY.set(None)
            feedback_token = phase33._EXTERNAL_GROUNDING_FEEDBACK.set(feedback)
            try:
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
                    # The provider outcome is not safe to bridge into the
                    # durable decision schema. Leave request_started intact.
                    raise Phase38ConvergenceExhausted(
                        "Phase 3.8 received an invalid decision identity; its "
                        "provider request remains sealed and will not replay."
                    ) from exc
                state["dispatch_status"] = "decision_returned"
                state["dispatch_decision_id"] = decision_id
                state["dispatch_decision_context_hash"] = context_hash
                _persist_convergence_state(state)
                raise
            except ValueError as exc:
                message = str(exc)
                if "failed exact source-block grounding before freeze" not in message:
                    # A generic exception does not prove that no paid provider
                    # byte was sent. Leave request_started durable and fail
                    # closed; only recognized grounding rejection, verified
                    # success, or atomically saved decision may clear it.
                    raise Phase38ConvergenceExhausted(
                        "Phase 3.8 stopped on an unrecognized provider outcome. "
                        "Its durable request_started claim remains sealed, so "
                        "neither this run nor Resume will replay the request."
                    ) from exc

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
                    state["status"] = "exhausted"
                    state["final_verification_pending"] = False
                    state["terminal_reason"] = (
                        "Phase 3.8 refused more than 100 distinct grounding "
                        "issue identities in one job."
                    )
                    _clear_dispatch_claim(state)
                    _persist_convergence_state(state)
                    raise Phase38ConvergenceExhausted(
                        "Phase 3.8 reached its bounded distinct-issue safety "
                        "limit; no additional provider request will be made."
                    ) from exc

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
                if isinstance(exc, early_gate.TopologyRepairRequired):
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
                    raise _terminal_convergence_error(
                        state,
                        scope=scope,
                        exc=exc,
                    ) from exc

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
            except Exception as exc:
                # Unknown failures cannot establish whether a paid call began
                # or completed. Retain request_started so Resume fails closed.
                raise Phase38ConvergenceExhausted(
                    "Phase 3.8 stopped on an unrecognized provider outcome. "
                    "Its durable request_started claim remains sealed, so "
                    "neither this run nor Resume will replay the request."
                ) from exc
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

    phase32._apply_decisions = _capture_repaired_topology
    phase32._read_cached_records = _read_cached_records
    phase33._phase32_adjudicate_with_convergence = (
        _phase32_adjudicate_with_targeted_convergence
    )

    phase31._PHASE38_BOUNDARY_GROUNDING_VERSION = _CONTRACT_VERSION

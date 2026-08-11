"""The decision kernel: the one way the rewritten Phase 3 talks to a model.

Doctrine (docs/phase3-rewrite-spec.md §4, "Decide once"):

* a decision is made exactly once and cached in the decision store;
* mechanical defects get bounded corrections (3 attempts), then the run
  fails closed with the exact defect list — a run may fail, never wait;
* the critic is an auditor: its dissent becomes ``review_flags`` on the
  decision and can never block, retry, or replay anything;
* store entries are immutable — a changed envelope or policy yields new
  keys, and stale entries simply become unreachable.
"""
from __future__ import annotations

import copy
import hashlib
import json
import threading
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from . import envelope as envelope_mod
from .. import semantic_confidence_policy as confidence_policy

MAX_ATTEMPTS = 3

Provider = Callable[[dict[str, Any]], Mapping[str, Any]]
Checker = Callable[[Mapping[str, Any]], list[str]]
Critic = Callable[[dict[str, Any]], Mapping[str, Any]]


class ContractError(RuntimeError):
    """The provider failed its mechanical contract after bounded corrections."""

    def __init__(self, message: str, defects: list[str] | None = None) -> None:
        super().__init__(message)
        self.defects = list(defects or [])


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def decision_key(
    *,
    kind: str,
    unit_id: str,
    envelope_sha256: str,
    payload: Mapping[str, Any],
    policy_version: str = "",
) -> str:
    identity = {
        "kind": str(kind),
        "unit_id": str(unit_id),
        "envelope_sha256": str(envelope_sha256),
        "policy_version": str(policy_version),
        "payload_sha256": hashlib.sha256(
            _canonical_json(payload).encode("utf-8")
        ).hexdigest(),
    }
    return hashlib.sha256(
        _canonical_json(identity).encode("utf-8")
    ).hexdigest()


class DecisionStore:
    """Content-addressed, immutable, append-only decision storage.

    Directory-backed when given a path; in-memory otherwise (tests).
    Immutability is the resume guarantee: an existing key is never
    overwritten, so a resumed run replays its decisions for free and can
    never re-litigate one.
    """

    def __init__(self, directory: str | Path | None = None) -> None:
        self._directory = Path(directory) if directory else None
        self._memory: dict[str, dict[str, Any]] = {}
        # Independent decisions (Settle topics, Host batches) may resolve
        # from a bounded worker pool; keys are distinct per decision, so the
        # lock only guards the map/file bookkeeping, never serializes model
        # calls.
        self._lock = threading.Lock()
        if self._directory is not None:
            self._directory.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        assert self._directory is not None
        return self._directory / f"{key}.json"

    def get(self, key: str) -> dict[str, Any] | None:
        if self._directory is None:
            with self._lock:
                found = self._memory.get(key)
                return copy.deepcopy(found) if found else None
        path = self._path(key)
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def put(self, key: str, decision: Mapping[str, Any]) -> dict[str, Any]:
        record = copy.deepcopy(dict(decision))
        if self._directory is None:
            with self._lock:
                existing = self._memory.get(key)
                if existing is not None:
                    return copy.deepcopy(existing)
                self._memory[key] = record
                return copy.deepcopy(record)
        existing = self.get(key)
        if existing is not None:
            return existing
        self._path(key).write_text(
            json.dumps(record, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        return copy.deepcopy(record)

    def keys(self) -> list[str]:
        if self._directory is None:
            return sorted(self._memory)
        return sorted(p.stem for p in self._directory.glob("*.json"))


def parallel_map_in_order(items, worker, *, max_workers: int) -> list:
    """Run ``worker`` over ``items`` on a bounded pool; results in input order.

    For independent decisions only (Settle topics, Host unit batches): the
    shared OpenAI gate still bounds real provider concurrency, the decision
    store is content-addressed per decision, and results merge in input
    order so output is byte-identical to the sequential path. Each task
    runs under a copy of the caller's contextvars so progress events keep
    flowing to the active sink. ``max_workers <= 1`` degrades to a plain
    loop.
    """
    items = list(items)
    if max_workers <= 1 or len(items) <= 1:
        return [worker(item) for item in items]
    import contextvars
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(
        max_workers=min(int(max_workers), len(items))
    ) as pool:
        futures = [
            pool.submit(contextvars.copy_context().run, worker, item)
            for item in items
        ]
        results = []
        try:
            for future in futures:
                results.append(future.result())
        except BaseException:
            # Fail fast: a failed decision stops the run, so queued sibling
            # batches must not keep spending provider calls.
            for other in futures:
                other.cancel()
            raise
        return results


def advisory_flags(review: Mapping[str, Any] | None) -> list[str]:
    """Convert a critic response into review flags. Never raises."""

    if not isinstance(review, Mapping):
        return ["critic returned no readable review; decision stands"]
    flags: list[str] = []
    verdict = str(review.get("verdict") or "").strip().lower()
    try:
        confidence = float(review.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    band = confidence_policy.semantic_band(confidence)
    if verdict and verdict != "verified":
        flags.append(
            f"independent critic verdict {verdict!r} with confidence "
            f"{confidence:.3f} (band: {band}); decision stands under "
            "decide-once and this dissent is recorded for review"
        )
    elif band == "human_review":
        flags.append(
            f"independent critic confidence {confidence:.3f} is in the "
            "0.900–0.919 human-review band; decision stands, flagged"
        )
    for issue in list(review.get("issues") or [])[:8]:
        text = str(issue).strip()
        if text:
            flags.append(f"critic: {text}")
    return flags


def decide(
    *,
    kind: str,
    unit_id: str,
    envelope_sha256: str,
    payload: Mapping[str, Any],
    provider: Provider,
    checker: Checker,
    critic: Critic | None = None,
    store: DecisionStore,
    policy_version: str = "",
    attempts: int = MAX_ATTEMPTS,
    provider_label: str = "",
) -> dict[str, Any]:
    """Make one decision, exactly once, under the written rules."""

    key = decision_key(
        kind=kind,
        unit_id=unit_id,
        envelope_sha256=envelope_sha256,
        payload=payload,
        policy_version=policy_version,
    )
    cached = store.get(key)
    if cached is not None:
        return cached

    defects: list[str] = []
    response: Mapping[str, Any] | None = None
    for attempt in range(1, max(1, attempts) + 1):
        request = copy.deepcopy(dict(payload))
        request["attempt"] = attempt
        request["max_attempts"] = attempts
        if defects:
            request["response_contract_feedback"] = list(defects)
        response = provider(request)
        defects = [
            str(row) for row in checker(response or {}) if str(row).strip()
        ]
        if not defects:
            break
    else:  # pragma: no cover - loop always breaks or raises below
        pass
    confidence_only = defects and all(
        defect.startswith("[confidence] ") for defect in defects
    )
    if confidence_only:
        # An honest sub-floor confidence after every bounded re-ask is a
        # judgment signal, not a structural defect: the decision ships
        # with the shortfall recorded for review (a run must produce
        # output; one weak grounding must not kill a chapter).
        pass
    elif defects:
        raise ContractError(
            f"{kind} decision for {unit_id} failed its mechanical response "
            f"contract after {attempts} bounded correction attempt(s): "
            + "; ".join(defects[:8]),
            defects=defects,
        )

    flags: list[str] = []
    if confidence_only:
        flags.extend(
            defect[len("[confidence] "):] + "; shipped for review after "
            f"{attempts} bounded attempt(s)"
            for defect in defects
        )
    if critic is not None:
        review_payload = copy.deepcopy(dict(payload))
        review_payload["proposed_decision"] = copy.deepcopy(dict(response or {}))
        try:
            review = critic(review_payload)
        except Exception as exc:  # the auditor can never take the run down
            review = None
            flags.append(
                f"critic failed to run ({type(exc).__name__}); decision "
                "stands unaudited"
            )
        flags.extend(advisory_flags(review) if review is not None else [])

    decision = {
        "key": key,
        "kind": str(kind),
        "unit_id": str(unit_id),
        "envelope_sha256": str(envelope_sha256),
        "policy_version": str(policy_version),
        "response": copy.deepcopy(dict(response or {})),
        "review_flags": flags,
        "provider": str(provider_label),
        "created_at": time.time(),
    }
    return store.put(key, decision)

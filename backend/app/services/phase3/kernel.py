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

import contextlib
import copy
import hashlib
import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from . import envelope as envelope_mod
from .. import progress
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
        # Locked and atomic: parallel workers store distinct keys today, but
        # a torn write would hand a RESUMED run half a decision — the one
        # corruption the immutability guarantee exists to prevent.
        with self._lock:
            existing = self.get(key)
            if existing is not None:
                return existing
            path = self._path(key)
            fd, tmp = tempfile.mkstemp(
                dir=str(self._directory), prefix=".decision-", suffix=".tmp",
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(json.dumps(record, ensure_ascii=False, indent=1))
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(tmp, path)
            except BaseException:
                with contextlib.suppress(OSError):
                    os.unlink(tmp)
                raise
        return copy.deepcopy(record)

    def keys(self) -> list[str]:
        if self._directory is None:
            return sorted(self._memory)
        return sorted(p.stem for p in self._directory.glob("*.json"))


def parallel_map_in_order(
    items,
    worker,
    *,
    max_workers: int,
    labels: Sequence[str] | None = None,
    announce: str = "",
    on_result=None,
) -> list:
    """Run ``worker`` over ``items`` on a bounded pool; results in input order.

    For independent decisions only (Settle topics, Host unit batches): the
    shared OpenAI gate still bounds real provider concurrency, the decision
    store is content-addressed per decision, and results merge in input
    order so output is byte-identical to the sequential path. Each task
    runs under a copy of the caller's contextvars so progress events keep
    flowing to the active sink. ``max_workers <= 1`` degrades to a plain
    loop.

    ``labels`` (one per item) tags every log line a worker emits with its
    unit — the console stays attributable when lines interleave — and
    ``announce`` logs the fan-out width once, so the log says HOW the
    stage ran, not just what it produced. Both apply on the sequential
    degrade path too, so a one-worker run reads the same.

    ``on_result(index, item, result)``, when given, runs in the CALLER's
    thread in strict input order as each result becomes collectable —
    item 3 finishing early still waits for items 1 and 2. It exists for
    ordered side effects that must see a clean prefix: durable resume
    checkpoints ("chunks 1..k complete") and monotone progress updates.
    A worker that raises fails fast before its ``on_result`` runs.
    """
    items = list(items)
    label_list = [str(label) for label in labels] if labels is not None else None
    if label_list is not None and len(label_list) != len(items):
        raise ValueError("labels must match items one-to-one")

    def run_one(index: int, item):
        if label_list is None:
            return worker(item)
        with progress.label_scope(label_list[index]):
            return worker(item)

    workers = min(int(max_workers), len(items))
    if announce and len(items) > 0:
        progress.log(
            f"{announce}: {len(items)} unit(s) across "
            f"{max(1, workers)} parallel worker(s)"
        )
    if max_workers <= 1 or len(items) <= 1:
        results = []
        for index, item in enumerate(items):
            result = run_one(index, item)
            if on_result is not None:
                on_result(index, item, result)
            results.append(result)
        return results
    import contextvars
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(contextvars.copy_context().run, run_one, index, item)
            for index, item in enumerate(items)
        ]
        results = []
        try:
            for index, future in enumerate(futures):
                result = future.result()
                if on_result is not None:
                    on_result(index, items[index], result)
                results.append(result)
        except BaseException:
            # Fail fast: a failed decision stops the run, so queued sibling
            # batches must not keep spending provider calls.
            for other in futures:
                other.cancel()
            raise
        return results


def pin_flags(
    flags: list[str],
    batch_ids: list[str],
    unit_id: str,
) -> list[str]:
    """Attach unit-specific flags to their unit; general ones to all.

    A critic issue naming a specific unit must land only on that unit —
    staging showed whole batches flagged for one concept's dissent
    (settle's fix, now the one shared implementation for every batched
    stage: Settle, Place, Host, Analyse, Premap, Polish).
    """
    unit = str(unit_id or "")
    ids = [str(candidate) for candidate in batch_ids if str(candidate)]
    pinned = [flag for flag in flags if unit and unit in flag]
    general = [
        flag for flag in flags
        if not any(candidate in flag for candidate in ids)
    ]
    return pinned + general


def advisory_flags(review: Mapping[str, Any] | None) -> list[str]:
    """Convert a critic response into review flags. Never raises.

    Only genuine DISSENT flags (owner steer, 2026-08-20): a passing
    audit emits nothing — not its confidence, not its side notes. The
    0.900–0.919 "human-review band" flag is gone with it: nothing in the
    pipeline ever routed on it (emit-only, verified before removal), the
    0.92 acceptance floor and its ``[confidence]`` defect mechanics are
    untouched, and anything the machine can rectify is the Refiner's and
    The Fixer's job — a human is asked to review dissent, never a
    passing number.
    """

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
    elif band in ("rejected", "invalid"):
        # "Verified" at sub-floor confidence contradicts itself — that is
        # a broken audit, not a passing one, and it must never look
        # clean (the malformed/subfloor pins in the assessment suites).
        flags.append(
            f"critic verified at sub-floor confidence {confidence:.3f} "
            f"(band: {band}); decision stands, audit recorded as "
            "unreliable"
        )
    # A clean pass's CONFIDENCE is never a flag (the 0.900–0.919 band is
    # gone), but any issue the critic names records whatever the verdict
    # label says — dissent-content is dissent (Q10). The review page
    # projects these notes out of the editor's way (release_review).
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
    fixer: Provider | None = None,
) -> dict[str, Any]:
    """Make one decision, exactly once, under the written rules.

    ``fixer`` is The Fixer seam (docs/aegis-restructure.md §8.2, Q13):
    when the bounded corrections exhaust with structural defects still
    standing and a fixer is provided, the block — the failing check, the
    contract, the original payload (which carries the rules/prompts and
    source evidence), and the last response — goes to the Fixer for one
    recorded best-judgment decision, validated by the SAME checker. A
    passing Fixer decision ships under the same decision key, flagged
    per original defect; a Fixer that cannot satisfy the checker either
    is protocol impossibility and raises ContractError exactly as a
    fixer-less run does.
    """

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
    fixer_flags: list[str] = []
    fixer_engaged = False
    if confidence_only:
        # An honest sub-floor confidence after every bounded re-ask is a
        # judgment signal, not a structural defect: the decision ships
        # with the shortfall recorded for review (a run must produce
        # output; one weak grounding must not kill a chapter).
        pass
    elif defects and fixer is not None:
        # The Fixer seam (Q13): one recorded, flagged best-judgment
        # decision at the block, validated by the same checker, and the
        # run completes. Nothing is guessed silently — every original
        # defect becomes a review flag naming what was blocked and what
        # was decided.
        blocked = list(defects)
        fixer_payload = {
            "fixer": True,
            "blocked_check": list(defects),
            "contract": {
                "kind": str(kind),
                "unit_id": str(unit_id),
                "policy_version": str(policy_version),
            },
            "original_payload": copy.deepcopy(dict(payload)),
            "last_response": copy.deepcopy(dict(response or {})),
        }
        fixer_defects = list(defects)
        for attempt in range(1, max(1, attempts) + 1):
            request = copy.deepcopy(fixer_payload)
            request["attempt"] = attempt
            request["max_attempts"] = attempts
            request["response_contract_feedback"] = list(fixer_defects)
            candidate = fixer(request)
            fixer_defects = [
                str(row)
                for row in checker(candidate or {})
                if str(row).strip()
            ]
            if not any(
                not defect.startswith("[confidence] ")
                for defect in fixer_defects
            ):
                response = candidate
                defects = list(fixer_defects)
                confidence_only = bool(defects)
                fixer_engaged = True
                rationale = " ".join(
                    str((candidate or {}).get("rationale") or "").split()
                )[:240] or "corrected by the Fixer's best judgment"
                fixer_flags = [
                    f"fixer: blocked={defect}; decided={rationale}"
                    for defect in blocked
                ]
                break
        if not fixer_engaged:
            raise ContractError(
                f"{kind} decision for {unit_id} failed its mechanical "
                f"response contract after {attempts} bounded correction "
                "attempt(s), and the Fixer could not produce a "
                "contract-satisfying decision either: "
                + "; ".join((fixer_defects or blocked)[:8]),
                defects=fixer_defects or blocked,
            )
    elif defects:
        raise ContractError(
            f"{kind} decision for {unit_id} failed its mechanical response "
            f"contract after {attempts} bounded correction attempt(s): "
            + "; ".join(defects[:8]),
            defects=defects,
        )

    flags: list[str] = list(fixer_flags)
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
    if fixer_engaged:
        decision["fixer"] = True
    return store.put(key, decision)

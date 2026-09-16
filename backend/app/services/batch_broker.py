"""Wave batching for the provider call, so a cohort pays the Batch API price.

Aegis makes one model call at a time and blocks on it. That is what a
chapter's work IS: thirty-six recorded decision kinds, each an author wave,
then the independent critic wave that reads what the author wrote, then a
correction wave for whatever failed the checker. Roughly seventy to a
hundred waves, in order, per chapter — a count that does not shrink when
more chapters are pushed (register Q53).

What DOES scale is the width of a wave. Every chapter in a cohort reaches
the same stage at roughly the same moment, and the sixteen workers inside
one stage are independent by construction. Batching that burst — every
request that arrives while the cohort is at the same seam — buys the
provider's batch price for the whole cohort while leaving the number of
waves exactly where it was. The owner's instruction of 14 September 2026:
*"output till each sequence can be stalled, and then the next sequence can
be pushed to the next together"*.

This module is that seam, and nothing else. It makes no judgment about
content: it takes a fully-formed request body, returns the provider's
response body for it, and is free to be slower than a direct call. Rule 1
is untouched — the decision is still the model's, the checker's and the
critic's.

Three properties matter more than the discount:

* **A submitted wave is never paid for twice.** The wave record is written
  before the request leaves, the provider batch id is written as soon as it
  exists, and every returned line is stored content-addressed by its
  request hash. A process that dies mid-wave re-attaches on the next boot
  and harvests what was already bought.
* **Waiting never changes the selected price.** A caller deadline produces
  ``BatchPending``; the submitted wave stays open and continues to be polled.
  A resumed caller attaches to that wave. Neither a timeout nor shutdown
  cancels purchased work or authorizes a synchronous request.
* **The cache is the crash plan, not an optimisation.** Responses are keyed
  by the sha256 of the canonical request body, so replay is exact: the same
  body gets the same answer without a second charge, across processes and
  across runs.
"""
from __future__ import annotations

import contextvars
import hashlib
import json
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

from .. import config
from . import run_control

log = logging.getLogger(__name__)

# One wave closes when the cohort has gone quiet for this long: every thread
# that was going to arrive at this seam has arrived, and the ones that have
# not are behind a checker or a critic and will form the next wave.
QUIET_SECONDS = "AEGIS_BATCH_QUIET_SECONDS"
# ...unless it has been open this long already. A cohort whose chapters are
# genuinely out of step must still make progress.
MAX_WAIT_SECONDS = "AEGIS_BATCH_MAX_WAIT_SECONDS"
# A caller can yield back to the durable queue while provider work continues.
# This deadline never cancels the batch or authorizes a synchronous charge.
DEADLINE_SECONDS = "AEGIS_BATCH_DEADLINE_SECONDS"
POLL_SECONDS = "AEGIS_BATCH_POLL_SECONDS"
MAX_LINES = "AEGIS_BATCH_MAX_LINES"

_DEFAULTS = {
    QUIET_SECONDS: 20.0,
    MAX_WAIT_SECONDS: 180.0,
    DEADLINE_SECONDS: 5400.0,
    POLL_SECONDS: 20.0,
    MAX_LINES: 400.0,
}


def _setting(name: str) -> float:
    raw = os.environ.get(name, "")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return _DEFAULTS[name]
    return value if value > 0 else _DEFAULTS[name]


class BatchUnavailable(RuntimeError):
    """A terminal batch failure; never permission to change the price lane."""

    retryable = False


class BatchPending(BatchUnavailable):
    """Durable batch work is waiting; resume it instead of buying it again."""

    retryable = True

    def __init__(self, message: str, *, wave_id: str = "", batch_id: str = "",
                 request_sha256: str = ""):
        super().__init__(message)
        self.wave_id = wave_id
        self.batch_id = batch_id
        self.request_sha256 = request_sha256


@dataclass(frozen=True)
class BatchResult:
    body: dict[str, Any]
    batch_id: str
    request_sha256: str
    reused: bool
    owner_job_id: int | None = None

    @property
    def receipt_id(self) -> str:
        return f"{self.batch_id}:{self.request_sha256}"


def request_sha256(body: Mapping[str, Any]) -> str:
    """Identity of one provider request: the canonical bytes of its body."""
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                   default=str).encode("utf-8")
    ).hexdigest()


class BatchApi(Protocol):
    """The provider seam, so the broker is testable without spending."""

    def submit(self, jsonl: bytes, *, wave_id: str) -> str:
        """Upload the lines and create the batch; return the provider's id."""

    def status(self, batch_id: str) -> str:
        """``in_progress`` | ``completed`` | ``failed`` | ``expired`` | …"""

    def results(self, batch_id: str) -> list[dict[str, Any]]:
        """Parsed output lines, each carrying ``custom_id`` and ``response``."""

    def cancel(self, batch_id: str) -> None:
        ...

    def find(self, wave_id: str) -> str | None:
        """The batch this wave created, for a boot that lost its record."""


#: The Files API's own upload purpose — NOT one of Aegis's routing purposes
#: (``openai_policy.REASONING_EFFORT_BY_PURPOSE``). It is a named constant so
#: the app-wide sweep that pins every declared routing purpose does not read
#: this unrelated keyword as an unknown route.
_FILES_UPLOAD_PURPOSE = "batch"


class OpenAIBatchApi:
    """The live adapter. One client per broker, built from the bound route."""

    def __init__(self, client_factory: Callable[[], Any], *, endpoint: str = "/v1/chat/completions"):
        self._client_factory = client_factory
        self._endpoint = endpoint

    def submit(self, jsonl: bytes, *, wave_id: str) -> str:
        client = self._client_factory()
        uploaded = client.files.create(
            file=(f"aegis-{wave_id}.jsonl", jsonl),
            purpose=_FILES_UPLOAD_PURPOSE,
        )
        # Retrying a non-idempotent create inside the SDK can purchase another
        # batch before the broker ever sees the uncertain result. Reconcile the
        # recorded wave after a transport error instead; GETs may still retry.
        created = client.with_options(max_retries=0).batches.create(
            input_file_id=uploaded.id,
            endpoint=self._endpoint,
            completion_window="24h",
            metadata={"aegis_wave": wave_id},
        )
        return str(created.id)

    def status(self, batch_id: str) -> str:
        return str(self._client_factory().batches.retrieve(batch_id).status or "")

    def results(self, batch_id: str) -> list[dict[str, Any]]:
        client = self._client_factory()
        batch = client.batches.retrieve(batch_id)
        lines: list[dict[str, Any]] = []
        for file_id in (batch.output_file_id, batch.error_file_id):
            if not file_id:
                continue
            content = client.files.content(file_id)
            text = content.text if hasattr(content, "text") else content.read().decode("utf-8")
            for row in str(text).splitlines():
                row = row.strip()
                if not row:
                    continue
                try:
                    lines.append(json.loads(row))
                except json.JSONDecodeError:
                    # A malformed line is one request's loss, never the wave's:
                    # it remains unresolved and the durable harvest retries.
                    log.warning("batch %s: unparseable output line", batch_id)
        return lines

    def cancel(self, batch_id: str) -> None:
        try:
            self._client_factory().batches.cancel(batch_id)
        except Exception:  # pragma: no cover - cancellation is best effort
            log.warning("batch %s: cancel failed", batch_id, exc_info=True)

    def find(self, wave_id: str) -> str | None:
        # A failed listing is not evidence that a submission never happened.
        page = self._client_factory().batches.list(limit=100)
        while True:
            for item in getattr(page, "data", []) or []:
                metadata = getattr(item, "metadata", None) or {}
                if str(metadata.get("aegis_wave") or "") == wave_id:
                    return str(item.id)
            if not callable(getattr(page, "has_next_page", None)) or not page.has_next_page():
                break
            page = page.get_next_page()
        return None


class BatchStore:
    """Durable wave records and content-addressed responses on the volume.

    Responses are shared across jobs deliberately: identical bytes are
    identical work, and a cohort re-run after a crash must not re-buy what
    the provider already returned.
    """

    def __init__(self, root: Path | None = None):
        self.root = Path(root or (config.DATA_DIR / "batch"))
        self.responses = self.root / "responses"
        self.waves = self.root / "waves"
        self.receipts = self.root / "receipts"
        for path in (self.responses, self.waves, self.receipts):
            path.mkdir(parents=True, exist_ok=True)

    # -- responses ---------------------------------------------------------
    def response(self, sha: str) -> dict[str, Any] | None:
        path = self.responses / f"{sha}.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def put_response(self, sha: str, body: Mapping[str, Any], *, batch_id: str,
                     created_at_ns: int = 0, owner_job_id: int | None = None) -> None:
        record = {"request_sha256": sha, "batch_id": batch_id, "body": dict(body),
                  "created_at_ns": created_at_ns, "owner_job_id": owner_job_id}
        self._atomic(self.responses / f"{sha}.json", record)

    def record_paid_receipt(self, sha: str, body: Mapping[str, Any], *, batch_id: str,
                            owner_job_id: int | None = None) -> None:
        """Retain every paid completion, including rejected author retries.

        This ledger is independent of callers' usage journals: a shutdown after
        harvest cannot erase the bill, and overwriting a cached bad answer cannot
        erase its earlier paid attempt. Dashboard totals deduplicate by receipt_id.
        """
        receipt_id = f"{batch_id}:{sha}"
        key = hashlib.sha256(receipt_id.encode()).hexdigest()
        path = self.receipts / f"{key}.json"
        if path.exists():
            return
        record = {
            "receipt_id": receipt_id, "batch_id": batch_id, "request_sha256": sha,
            "owner_job_id": owner_job_id, "provider": "openai", "batched": True,
            "model": str(body.get("model") or ""), "usage": body.get("usage"),
            "service_tier": body.get("service_tier"), "response_id": body.get("id"),
            "recorded_at": time.time(),
        }
        from . import openai_usage

        record["cost_estimate"] = openai_usage.estimate_batch_receipt(record, for_storage=True)
        self._atomic(path, record)

    def paid_receipts(self) -> list[dict[str, Any]]:
        records = []
        for path in sorted(self.receipts.glob("*.json")):
            try:
                records.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
        return records

    # -- waves -------------------------------------------------------------
    def put_wave(self, record: Mapping[str, Any]) -> None:
        self._atomic(self.waves / f"{record['wave_id']}.json", dict(record))

    def wave(self, wave_id: str) -> dict[str, Any] | None:
        try:
            return json.loads((self.waves / f"{wave_id}.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def open_waves(self) -> list[dict[str, Any]]:
        """Every wave this machine has not seen through to a terminal state."""
        out: list[dict[str, Any]] = []
        for path in sorted(self.waves.glob("*.json")):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if str(record.get("state") or "") not in {"harvested", "abandoned", "failed"}:
                out.append(record)
        return out

    def _atomic(self, path: Path, record: Mapping[str, Any]) -> None:
        tmp = path.with_suffix(f".{uuid.uuid4().hex[:8]}.tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump(record, handle, ensure_ascii=False, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)


@dataclass
class _Waiter:
    sha: str
    body: dict[str, Any]
    event: threading.Event = field(default_factory=threading.Event)
    response: dict[str, Any] | None = None
    failed: str = ""
    fresh: bool = False
    wave_id: str = ""
    batch_id: str = ""
    owner_job_id: int | None = None


class BatchBroker:
    """Collect waves while continuously reconciling already purchased work.

    The request index includes pending AND submitted work. It is rebuilt from
    durable wave records before callers can enqueue, not from completed answers
    alone. A slow wave never blocks submission/polling of an independent wave.
    """

    def __init__(
        self,
        api: BatchApi,
        *,
        store: BatchStore | None = None,
        on_event: Callable[[str], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._api = api
        self._store = store or BatchStore()
        self._on_event = on_event or (lambda message: log.info("%s", message))
        self._clock = clock
        self._sleep = sleep  # retained for callers constructing the test seam
        self._lock = threading.RLock()
        self._maintenance_lock = threading.Lock()
        self._pending: dict[str, _Waiter] = {}
        self._requests: dict[str, _Waiter] = {}
        self._records: dict[str, dict[str, Any]] = {}
        self._wave_waiters: dict[str, list[_Waiter]] = {}
        self._receipt_consumers: set[str] = set()
        self._arrived_at = 0.0
        self._opened_at = 0.0
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._dispatcher: threading.Thread | None = None
        self._restore_open_waves()

    def _restore_open_waves(self) -> None:
        with self._lock:
            for record in self._store.open_waves():
                wave_id = str(record.get("wave_id") or "")
                if not wave_id or wave_id in self._records:
                    continue
                self._records[wave_id] = dict(record)
                waiters = []
                for entry in record.get("entries") or []:
                    sha = str(entry.get("request_sha256") or entry.get("custom_id") or "")
                    if not sha:
                        continue
                    waiter = _Waiter(
                        sha=sha, body=dict(entry.get("body") or {}),
                        fresh=bool(entry.get("fresh")), wave_id=wave_id,
                        batch_id=str(record.get("batch_id") or ""),
                        owner_job_id=entry.get("owner_job_id"),
                    )
                    waiters.append(waiter)
                    # Historical duplicate waves are still harvested, but never
                    # spawn another duplicate. The first open owner answers.
                    self._requests.setdefault(sha, waiter)
                self._wave_waiters[wave_id] = waiters

    def start(self) -> None:
        with self._lock:
            if self._dispatcher is not None:
                return
            if self._stop.is_set():
                raise BatchPending("the batch worker is draining; resume after restart")
            self._dispatcher = threading.Thread(
                target=self._supervise, name="aegis-batch-dispatcher", daemon=True,
            )
            self._dispatcher.start()

    def stop(self) -> None:
        # Do not cancel provider batches. Persist requests still waiting to form
        # a wave before releasing callers, so a restart can submit them once.
        self._stop.set()
        self._wake.set()
        with self._lock:
            pending = list(self._pending.values())
            self._pending.clear()
            if pending:
                self._prepare_wave(pending)
            thread = self._dispatcher
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5.0)
        # Keep a still-running dispatcher referenced; start() must not create a
        # second one while an HTTP operation from this instance is returning.
        if thread is None or not thread.is_alive():
            self._dispatcher = None

    def call(self, body: Mapping[str, Any], *, fresh: bool = False) -> dict[str, Any]:
        return self.call_result(body, fresh=fresh).body

    def call_result(self, body: Mapping[str, Any], *, fresh: bool = False,
                    owner_job_id: int | None = None) -> BatchResult:
        sha = request_sha256(body)
        with self._lock:
            if self._stop.is_set() or run_control.pausing():
                raise BatchPending("the batch worker is draining; resume after restart",
                                   request_sha256=sha)
            # An explicit author retry also attaches to its surviving retry wave.
            # It must not evade recovery merely because its body is unchanged.
            waiter = self._requests.get(sha)
            if waiter is None and not fresh:
                cached = self._store.response(sha)
                if cached is not None:
                    return BatchResult(dict(cached.get("body") or {}),
                                       str(cached.get("batch_id") or ""), sha, True,
                                       cached.get("owner_job_id"))
            if waiter is None:
                waiter = _Waiter(sha=sha, body=dict(body), fresh=fresh,
                                 owner_job_id=owner_job_id)
                self._requests[sha] = waiter
                self._pending[sha] = waiter
                now = self._clock()
                self._arrived_at = now
                if len(self._pending) == 1:
                    self._opened_at = now
            elif not waiter.wave_id:
                waiter.fresh = waiter.fresh or fresh
        self.start()
        self._wake.set()
        deadline = self._clock() + _setting(DEADLINE_SECONDS)
        while not waiter.event.wait(timeout=min(0.1, max(0.001, deadline - self._clock()))):
            if self._stop.is_set() or run_control.pausing() or self._clock() >= deadline:
                raise BatchPending(
                    "batch work remains saved and pending; resume without changing the price lane",
                    wave_id=waiter.wave_id, batch_id=waiter.batch_id, request_sha256=sha,
                )
        if waiter.failed:
            raise BatchUnavailable(waiter.failed)
        if waiter.response is None:
            raise BatchPending("the batch result is awaiting reconciliation",
                               wave_id=waiter.wave_id, batch_id=waiter.batch_id,
                               request_sha256=sha)
        receipt_id = f"{waiter.batch_id}:{sha}"
        with self._lock:
            reused = receipt_id in self._receipt_consumers
            self._receipt_consumers.add(receipt_id)
        return BatchResult(dict(waiter.response), waiter.batch_id, sha, reused,
                           waiter.owner_job_id)

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "open_waves": len(self._records),
                "pending_requests": len(self._pending),
                "submitted_requests": sum(len(rows) for rows in self._wave_waiters.values()),
                "uncertain_waves": sum(not bool(row.get("batch_id")) and
                                       row.get("state") != "prepared"
                                       for row in self._records.values()),
                "draining": self._stop.is_set(),
            }

    def _supervise(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(timeout=self._next_check())
            self._wake.clear()
            if self._stop.is_set():
                return
            try:
                with self._maintenance_lock:
                    self._maybe_close_wave()
                    self._poll_open_waves()
            except Exception:
                log.exception("batch dispatcher pass failed; durable waves remain open")

    def _next_check(self) -> float:
        with self._lock:
            poll = _setting(POLL_SECONDS)
            if not self._pending:
                return poll
            now = self._clock()
            return max(0.005, min(
                poll,
                _setting(QUIET_SECONDS) - (now - self._arrived_at),
                _setting(MAX_WAIT_SECONDS) - (now - self._opened_at),
            ))

    def _maybe_close_wave(self) -> None:
        with self._lock:
            if not self._pending or self._stop.is_set():
                return
            now = self._clock()
            line_limit = max(1, int(_setting(MAX_LINES)))
            full = len(self._pending) >= line_limit
            if not (full or now - self._arrived_at >= _setting(QUIET_SECONDS)
                    or now - self._opened_at >= _setting(MAX_WAIT_SECONDS)):
                return
            # Respect the configured line ceiling even when one fan-out arrives
            # between scheduler passes.
            keys = list(self._pending)[:line_limit]
            waiters = [self._pending.pop(sha) for sha in keys]
            if self._pending:
                self._opened_at = now
            record = self._prepare_wave(waiters)
        self._submit(record)

    def _prepare_wave(self, waiters: Sequence[_Waiter]) -> dict[str, Any]:
        wave_id = uuid.uuid4().hex[:16]
        record = {
            "wave_id": wave_id, "state": "prepared", "batch_id": "",
            "created_at_ns": time.time_ns(),
            "entries": [{"custom_id": w.sha, "request_sha256": w.sha,
                         "body": w.body, "fresh": w.fresh,
                         "owner_job_id": w.owner_job_id} for w in waiters],
            "line_count": len(waiters),
        }
        self._store.put_wave(record)
        self._records[wave_id] = record
        self._wave_waiters[wave_id] = list(waiters)
        for waiter in waiters:
            waiter.wave_id = wave_id
        return record

    def _submit(self, record: dict[str, Any]) -> None:
        if self._stop.is_set() or run_control.pausing():
            return
        wave_id = str(record["wave_id"])
        lines = [json.dumps({
            "custom_id": entry["request_sha256"], "method": "POST",
            "url": "/v1/chat/completions", "body": entry["body"],
        }, ensure_ascii=False, default=str) for entry in record["entries"]]
        payload = ("\n".join(lines) + "\n").encode()
        del lines
        # Durable prepared means no submission was attempted. Every other state
        # without an ID is uncertain and must be reconciled, never resubmitted.
        record["state"] = "submitting"
        # Only never-submitted work needs its body for restart. Once a create
        # may leave, recovery uses the provider wave ID, not another upload.
        # In particular, do not retain base64 PDF pages in every open record
        # and waiter for the many hours a batch can remain queued.
        for entry in record["entries"]:
            entry.pop("body", None)
        with self._lock:
            for waiter in self._wave_waiters.get(wave_id, []):
                waiter.body = {}
        self._store.put_wave(record)
        try:
            batch_id = self._api.submit(payload, wave_id=wave_id)
        except Exception as exc:
            record["error"] = str(exc)
            # An explicit client rejection establishes no batch was accepted.
            # Transport failures, timeouts, conflicts and rate limits do not.
            status_code = getattr(exc, "status_code", None)
            if isinstance(status_code, int) and 400 <= status_code < 500 and status_code not in {408, 409, 429}:
                self._finish(record, failure=f"batch submission rejected: {exc}")
            else:
                record["state"] = "submission_unknown"
                self._store.put_wave(record)
                self._on_event(f"Batch wave {wave_id} submission awaiting reconciliation; no synchronous fallback.")
            return
        record["batch_id"] = str(batch_id)
        record["state"] = "submitted"
        self._store.put_wave(record)
        with self._lock:
            for waiter in self._wave_waiters.get(wave_id, []):
                waiter.batch_id = str(batch_id)
        self._on_event(f"Batch wave {wave_id} submitted: {record['line_count']} request(s) at the batch price.")

    def _poll_open_waves(self) -> int:
        harvested = 0
        with self._lock:
            records = list(self._records.values())
        for record in records:
            try:
                harvested += self._poll_wave(record)
            except Exception as exc:
                # In particular, an output download/listing error leaves the
                # wave open for the next pass instead of discarding paid work.
                log.warning("batch wave %s reconciliation pending: %s", record.get("wave_id"), exc)
        return harvested

    def _poll_wave(self, record: dict[str, Any]) -> int:
        wave_id = str(record["wave_id"])
        if record.get("state") == "prepared":
            self._submit(record)
            if record.get("state") in {"prepared", "failed"}:
                return 0
        batch_id = str(record.get("batch_id") or "")
        if not batch_id:
            found = self._api.find(wave_id)
            if not found:
                # Absence in a listing is not a proof of non-submission after a
                # lost network response; retain the uncertainty visibly.
                return 0
            batch_id = str(found)
            record["batch_id"] = batch_id
            record["state"] = "submitted"
            self._store.put_wave(record)
            with self._lock:
                for waiter in self._wave_waiters.get(wave_id, []):
                    waiter.batch_id = batch_id
        status = self._api.status(batch_id)
        if status not in {"completed", "failed", "expired", "cancelled"}:
            return 0
        record["status"] = status
        return self.harvest(record)

    def harvest(self, record: Mapping[str, Any]) -> int:
        batch_id = str(record.get("batch_id") or "")
        if not batch_id:
            return 0
        # A failed download propagates to the supervisor. It must never be
        # mistaken for an empty successful harvest.
        lines = self._api.results(batch_id)
        wave_id = str(record.get("wave_id") or "")
        with self._lock:
            waiting = {w.sha: w for w in self._wave_waiters.get(wave_id, [])}
        expected = {str(e.get("request_sha256") or e.get("custom_id") or "")
                    for e in record.get("entries") or []}
        seen: set[str] = set()
        stored = 0
        for line in lines:
            sha = str(line.get("custom_id") or "")
            if sha not in expected:
                continue
            response = line.get("response") or {}
            if not isinstance(response, Mapping):
                continue
            body = response.get("body")
            status_code = response.get("status_code")
            waiter = waiting.get(sha)
            if status_code != 200 or not isinstance(body, Mapping):
                if status_code or line.get("error"):
                    seen.add(sha)
                    if waiter is not None:
                        self._release([waiter], failure=f"batch line failed ({status_code or 'error'})")
                continue
            seen.add(sha)
            with self._lock:
                owner_job_id = next((entry.get("owner_job_id")
                                     for entry in record.get("entries") or []
                                     if entry.get("request_sha256") == sha), None)
                self._store.record_paid_receipt(sha, body, batch_id=batch_id,
                                                owner_job_id=owner_job_id)
                existing = self._store.response(sha)
                created = int(record.get("created_at_ns") or 0)
                if existing is None or created > int(existing.get("created_at_ns") or 0):
                    self._store.put_response(sha, body, batch_id=batch_id,
                                             created_at_ns=created, owner_job_id=owner_job_id)
                    stored += 1
                if waiter is not None and not waiter.event.is_set():
                    waiter.batch_id = batch_id
                    waiter.response = dict(body)
                    waiter.event.set()
                    if self._requests.get(sha) is waiter:
                        self._requests.pop(sha, None)
        status = str(record.get("status") or "")
        if expected <= seen or status in {"failed", "expired", "cancelled"}:
            mutable = self._records.get(wave_id, dict(record))
            mutable["harvested"] = int(mutable.get("harvested") or 0) + stored
            self._finish(mutable, failure=(f"batch {batch_id} ended {status} without this result"
                                          if expected - seen else ""))
        # Completed without every expected line remains open: a partial/malformed
        # download must not permanently erase the missing purchased result.
        return stored

    def _finish(self, record: dict[str, Any], *, failure: str = "") -> None:
        wave_id = str(record["wave_id"])
        record["state"] = "failed" if failure else "harvested"
        if failure:
            record["error"] = failure
        self._store.put_wave(record)
        with self._lock:
            waiters = self._wave_waiters.pop(wave_id, [])
            self._records.pop(wave_id, None)
            for waiter in waiters:
                if not waiter.event.is_set():
                    self._release([waiter], failure=failure or "batch returned no usable result")
                if self._requests.get(waiter.sha) is waiter:
                    self._requests.pop(waiter.sha, None)

    def recover(self) -> int:
        """Reattach both running and completed waves, retaining transport doubt."""
        with self._maintenance_lock:
            self._restore_open_waves()
            return self._poll_open_waves()

    def _release(self, waiters: Sequence[_Waiter], *, failure: str) -> None:
        with self._lock:
            for waiter in waiters:
                if waiter.event.is_set():
                    continue
                waiter.failed = failure
                waiter.event.set()
                if self._requests.get(waiter.sha) is waiter:
                    self._requests.pop(waiter.sha, None)


# --------------------------------------------------------------------------- #
# Binding: only a run started in cohort mode calls through the broker.
# --------------------------------------------------------------------------- #

_bound: contextvars.ContextVar[BatchBroker | None] = contextvars.ContextVar(
    "aegis_batch_broker", default=None,
)
_process_broker: BatchBroker | None = None
_process_lock = threading.Lock()


def bind(broker: BatchBroker | None) -> contextvars.Token:
    """Bind (or clear) the broker for this run.

    A contextvar, not a thread local, because the stage fan-out runs each
    worker under a COPY of the caller's context (``kernel.parallel_map_in_order``)
    — which is precisely the sixteen sibling requests that have to land in
    the same wave. A thread local would bind only the orchestrating thread
    and every real request would miss the batch.
    """
    return _bound.set(broker)


def bound() -> BatchBroker | None:
    return _bound.get()


def process_broker(factory: Callable[[], BatchBroker]) -> BatchBroker:
    """One broker per process: every cohort shares its waves, which is the point."""
    global _process_broker
    with _process_lock:
        if _process_broker is None:
            _process_broker = factory()
            _process_broker.start()
        return _process_broker


def process_status() -> dict[str, Any]:
    """Read-only dashboard snapshot; inspecting it never creates a provider."""
    with _process_lock:
        broker = _process_broker
    if broker is not None:
        return broker.status()
    records = BatchStore().open_waves()
    return {
        "open_waves": len(records), "pending_requests": 0,
        "submitted_requests": sum(len(record.get("entries") or []) for record in records),
        "uncertain_waves": sum(not bool(record.get("batch_id")) and
                               record.get("state") != "prepared" for record in records),
        "draining": False,
    }


def reset_process_broker() -> None:
    """Test seam: drop the process broker without leaving its thread running."""
    global _process_broker
    with _process_lock:
        if _process_broker is not None:
            _process_broker.stop()
        _process_broker = None


class session:
    """Run a chapter's work with its provider calls batched.

    Inherited by the threads a run spawns is NOT automatic — Python thread
    locals are per thread — so the fan-out helper binds each worker from the
    parent's binding. That is deliberate: a call made outside a cohort run
    (an interactive Build Concepts run, say) must never be parked in a wave
    someone else is waiting on.
    """

    def __init__(self, broker: BatchBroker | None):
        self._broker = broker
        self._token: contextvars.Token | None = None

    def __enter__(self) -> BatchBroker | None:
        self._token = bind(self._broker)
        return self._broker

    def __exit__(self, *exc: object) -> None:
        if self._token is not None:
            _bound.reset(self._token)
            self._token = None

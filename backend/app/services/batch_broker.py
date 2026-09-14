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
* **A slow wave never strands a run.** Every waiter carries a deadline. On
  expiry the broker answers ``BatchUnavailable`` and the caller makes the
  ordinary synchronous call it would have made anyway. The batch is
  cancelled; anything it had already produced still lands in the store and
  is served free to whoever asks for it next.
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

log = logging.getLogger(__name__)

# One wave closes when the cohort has gone quiet for this long: every thread
# that was going to arrive at this seam has arrived, and the ones that have
# not are behind a checker or a critic and will form the next wave.
QUIET_SECONDS = "AEGIS_BATCH_QUIET_SECONDS"
# ...unless it has been open this long already. A cohort whose chapters are
# genuinely out of step must still make progress.
MAX_WAIT_SECONDS = "AEGIS_BATCH_MAX_WAIT_SECONDS"
# The provider guarantees only "within 24 hours". A run that waits that long
# is not a run, so a waiter gives up here and pays the synchronous price.
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
    """The wave could not answer this request; make the ordinary call.

    Never a failure of the decision — only of the transport. The caller has
    a complete, valid request in hand and every synchronous path still open.
    """


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
        created = client.batches.create(
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
                    # its waiter times out and makes the synchronous call.
                    log.warning("batch %s: unparseable output line", batch_id)
        return lines

    def cancel(self, batch_id: str) -> None:
        try:
            self._client_factory().batches.cancel(batch_id)
        except Exception:  # pragma: no cover - cancellation is best effort
            log.warning("batch %s: cancel failed", batch_id, exc_info=True)

    def find(self, wave_id: str) -> str | None:
        try:
            page = self._client_factory().batches.list(limit=100)
        except Exception:  # pragma: no cover - listing is best effort
            log.warning("batch: listing failed while recovering %s", wave_id, exc_info=True)
            return None
        for item in getattr(page, "data", []) or []:
            metadata = getattr(item, "metadata", None) or {}
            if str(metadata.get("aegis_wave") or "") == wave_id:
                return str(item.id)
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
        for path in (self.responses, self.waves):
            path.mkdir(parents=True, exist_ok=True)

    # -- responses ---------------------------------------------------------
    def response(self, sha: str) -> dict[str, Any] | None:
        path = self.responses / f"{sha}.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def put_response(self, sha: str, body: Mapping[str, Any], *, batch_id: str) -> None:
        record = {"request_sha256": sha, "batch_id": batch_id, "body": dict(body)}
        self._atomic(self.responses / f"{sha}.json", record)

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
            if str(record.get("state") or "") not in {"harvested", "abandoned"}:
                out.append(record)
        return out

    def _atomic(self, path: Path, record: Mapping[str, Any]) -> None:
        tmp = path.with_suffix(f".{uuid.uuid4().hex[:8]}.tmp")
        tmp.write_text(json.dumps(record, ensure_ascii=False, sort_keys=True),
                       encoding="utf-8")
        os.replace(tmp, path)


@dataclass
class _Waiter:
    sha: str
    body: dict[str, Any]
    event: threading.Event = field(default_factory=threading.Event)
    response: dict[str, Any] | None = None
    failed: str = ""


class BatchBroker:
    """Collect concurrent requests into waves and answer them from batches."""

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
        self._on_event = on_event or (lambda message: None)
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._pending: dict[str, _Waiter] = {}
        self._arrived_at = 0.0
        self._opened_at = 0.0
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._dispatcher: threading.Thread | None = None
        self._in_flight: dict[str, list[_Waiter]] = {}

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        if self._dispatcher is not None:
            return
        self._dispatcher = threading.Thread(
            target=self._supervise, name="aegis-batch-dispatcher", daemon=True,
        )
        self._dispatcher.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        thread = self._dispatcher
        if thread is not None:
            thread.join(timeout=5.0)
        self._dispatcher = None

    # -- the call ----------------------------------------------------------
    def call(self, body: Mapping[str, Any]) -> dict[str, Any]:
        """Answer one request body, or raise ``BatchUnavailable``."""
        sha = request_sha256(body)
        cached = self._store.response(sha)
        if cached is not None:
            return dict(cached.get("body") or {})

        waiter = _Waiter(sha=sha, body=dict(body))
        with self._lock:
            existing = self._pending.get(sha)
            if existing is not None:
                # Two threads asking the identical question is one line.
                waiter = existing
            else:
                self._pending[sha] = waiter
                now = self._clock()
                self._arrived_at = now
                if len(self._pending) == 1:
                    self._opened_at = now
        self.start()
        self._wake.set()

        if not waiter.event.wait(timeout=_setting(DEADLINE_SECONDS)):
            with self._lock:
                self._pending.pop(sha, None)
            raise BatchUnavailable("the batch wave did not return in time")
        if waiter.failed:
            raise BatchUnavailable(waiter.failed)
        if waiter.response is None:  # pragma: no cover - defensive
            raise BatchUnavailable("the batch wave returned nothing")
        return dict(waiter.response)

    # -- the dispatcher ----------------------------------------------------
    def _supervise(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(timeout=self._next_check())
            self._wake.clear()
            if self._stop.is_set():
                return
            try:
                self._maybe_close_wave()
            except Exception:  # pragma: no cover - the loop must survive
                log.exception("batch dispatcher pass failed")

    def _next_check(self) -> float:
        """Sleep exactly as long as the open wave can still afford.

        A fixed poll would hold a ready wave for up to a full tick after its
        quiet period expired — dead time added to every one of the seventy to
        a hundred seams a chapter has, which is the whole cost of the lane.
        """
        with self._lock:
            if not self._pending:
                return 1.0
            now = self._clock()
            return max(0.005, min(
                _setting(QUIET_SECONDS) - (now - self._arrived_at),
                _setting(MAX_WAIT_SECONDS) - (now - self._opened_at),
            ))

    def _maybe_close_wave(self) -> None:
        with self._lock:
            if not self._pending:
                return
            now = self._clock()
            quiet = now - self._arrived_at
            open_for = now - self._opened_at
            full = len(self._pending) >= int(_setting(MAX_LINES))
            if not (full or quiet >= _setting(QUIET_SECONDS)
                    or open_for >= _setting(MAX_WAIT_SECONDS)):
                return
            waiters = list(self._pending.values())
            self._pending.clear()
        self._run_wave(waiters)

    def _run_wave(self, waiters: Sequence[_Waiter]) -> None:
        wave_id = uuid.uuid4().hex[:16]
        entries = [{"custom_id": w.sha, "request_sha256": w.sha} for w in waiters]
        record = {
            "wave_id": wave_id, "state": "preparing", "batch_id": "",
            "entries": entries, "line_count": len(entries),
        }
        # Written BEFORE the request leaves: a crash between here and the
        # provider's answer leaves a record the next boot can reconcile by
        # asking the provider for the batch carrying this wave id.
        self._store.put_wave(record)
        lines = [
            json.dumps({
                "custom_id": w.sha,
                "method": "POST",
                "url": "/v1/chat/completions",
                "body": w.body,
            }, ensure_ascii=False, default=str)
            for w in waiters
        ]
        payload = ("\n".join(lines) + "\n").encode("utf-8")
        try:
            batch_id = self._api.submit(payload, wave_id=wave_id)
        except Exception as exc:
            record["state"] = "abandoned"
            record["error"] = str(exc)
            self._store.put_wave(record)
            self._release(waiters, failure=f"batch submission failed: {exc}")
            return
        record["batch_id"] = batch_id
        record["state"] = "submitted"
        self._store.put_wave(record)
        self._on_event(
            f"Batch wave {wave_id} submitted: {len(lines)} request(s) at the batch price."
        )
        with self._lock:
            self._in_flight[batch_id] = list(waiters)
        self._await_batch(record, waiters)

    def _await_batch(self, record: dict[str, Any], waiters: Sequence[_Waiter]) -> None:
        batch_id = str(record.get("batch_id") or "")
        deadline = self._clock() + _setting(DEADLINE_SECONDS)
        status = ""
        while self._clock() < deadline and not self._stop.is_set():
            try:
                status = self._api.status(batch_id)
            except Exception as exc:  # pragma: no cover - transport only
                log.warning("batch %s: status failed (%s)", batch_id, exc)
                status = ""
            if status in {"completed", "failed", "expired", "cancelled"}:
                break
            self._sleep(_setting(POLL_SECONDS))
        harvested = self.harvest(record)
        with self._lock:
            self._in_flight.pop(batch_id, None)
        if status not in {"completed", "failed", "expired", "cancelled"}:
            self._api.cancel(batch_id)
        outstanding = [w for w in waiters if not w.event.is_set()]
        if outstanding:
            self._release(outstanding, failure=f"batch {batch_id} status {status or 'unknown'}")
        record["state"] = "harvested"
        record["harvested"] = harvested
        record["status"] = status
        self._store.put_wave(record)

    def harvest(self, record: Mapping[str, Any]) -> int:
        """Store every line the provider produced and wake its waiter.

        Safe to call on a wave this process never submitted: that is exactly
        what recovery does. Results already in the store are not re-written.
        """
        batch_id = str(record.get("batch_id") or "")
        if not batch_id:
            return 0
        try:
            lines = self._api.results(batch_id)
        except Exception as exc:  # pragma: no cover - transport only
            log.warning("batch %s: result fetch failed (%s)", batch_id, exc)
            return 0
        with self._lock:
            waiting = {w.sha: w for w in self._in_flight.get(batch_id, [])}
        stored = 0
        for line in lines:
            sha = str(line.get("custom_id") or "")
            if not sha:
                continue
            response = line.get("response") or {}
            body = response.get("body") if isinstance(response, Mapping) else None
            status_code = int((response or {}).get("status_code") or 0)
            waiter = waiting.get(sha)
            if not isinstance(body, Mapping) or status_code != 200:
                if waiter is not None:
                    self._release([waiter], failure=f"batch line failed ({status_code or 'error'})")
                continue
            if self._store.response(sha) is None:
                self._store.put_response(sha, body, batch_id=batch_id)
                stored += 1
            if waiter is not None and not waiter.event.is_set():
                waiter.response = dict(body)
                waiter.event.set()
        return stored

    def recover(self) -> int:
        """Re-attach to waves this machine left open, and bank what they bought."""
        recovered = 0
        for record in self._store.open_waves():
            wave_id = str(record.get("wave_id") or "")
            batch_id = str(record.get("batch_id") or "")
            if not batch_id:
                found = self._api.find(wave_id)
                if not found:
                    record["state"] = "abandoned"
                    record["error"] = "no batch was created for this wave"
                    self._store.put_wave(record)
                    continue
                batch_id = found
                record["batch_id"] = batch_id
            try:
                status = self._api.status(batch_id)
            except Exception:  # pragma: no cover - transport only
                continue
            if status not in {"completed", "failed", "expired", "cancelled"}:
                # Still running somewhere. Leave it open; a later boot, or the
                # run that is waiting on it, will harvest it.
                record["state"] = "submitted"
                self._store.put_wave(record)
                continue
            recovered += self.harvest(record)
            record["state"] = "harvested"
            record["status"] = status
            self._store.put_wave(record)
        return recovered

    def _release(self, waiters: Sequence[_Waiter], *, failure: str) -> None:
        for waiter in waiters:
            if waiter.event.is_set():
                continue
            waiter.failed = failure
            waiter.event.set()


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

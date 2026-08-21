"""Live progress + log plumbing for long-running operations.

Service code calls :func:`log` / :func:`step` / :func:`set_progress` while it
works. When a request is served through :func:`stream`, those calls are routed
to an NDJSON event stream the frontend renders as a CLI-style console with a
progress bar. Outside a streaming context the calls are cheap no-ops, so the
same service functions keep working for non-streaming callers and tests.
"""
from __future__ import annotations

import contextlib
import contextvars
import json
import queue
import threading
import time
import traceback
from collections.abc import Callable, Iterator
from typing import Any

# The active event sink for the current logical operation (set per stream).
_sink: contextvars.ContextVar[Callable[[dict], None] | None] = contextvars.ContextVar(
    "aegis_progress_sink", default=None,
)
_history: contextvars.ContextVar[list[dict] | None] = contextvars.ContextVar(
    "aegis_progress_history", default=None,
)
# Progress samples [(ts, value), ...] for the active run — one shared list
# object per stream, so worker threads under copied contexts feed the same
# record; ``current_value`` reads the latest sample.
_track: contextvars.ContextVar[list | None] = contextvars.ContextVar(
    "aegis_progress_track", default=None,
)

# The active worker label (e.g. "Refine 12/58"): parallel workers set it so
# every log line says which unit produced it, in the live console and in the
# persisted generation log alike.
_label: contextvars.ContextVar[str] = contextvars.ContextVar(
    "aegis_progress_label", default="",
)

# The active STAGE (the last ``step`` label seen in this context). Worker
# threads copy the context at spawn, so a pool's calls attribute to the
# stage that spawned it — which is the stage that paid for them. Read by
# the usage accumulator to build the per-stage time/cost table the run
# console renders.
_stage: contextvars.ContextVar[str] = contextvars.ContextVar(
    "aegis_progress_stage", default="",
)


def current_stage() -> str:
    """The last ``step`` label seen in this context ("" before the first)."""
    return _stage.get()


def current_lane() -> str:
    """The composed worker-label scope active in this context ("" outside)."""
    return _label.get()


@contextlib.contextmanager
def label_scope(label: str):
    """Prefix every ``log`` line emitted in this scope with ``[label]``.

    Scopes nest by composition: a pool running inside an outer labelled
    scope (a batch inside a Phase 3 stage lane) emits ``[outer · inner]``,
    so an interleaved console still says which lane AND which unit spoke.
    """
    current = _label.get()
    composed = f"{current} · {label}" if current else str(label)
    token = _label.set(composed)
    try:
        yield
    finally:
        _label.reset(token)


_SENTINEL = object()

# Decision stages may emit from a bounded worker pool (parallel Settle
# topics / Host batches). Sinks append to queues or job logs that are not
# themselves synchronized, so one lock serializes delivery.
_emit_lock = threading.Lock()


def _emit(event: dict) -> None:
    event.setdefault("ts", time.time())
    with _emit_lock:
        history = _history.get()
        if history is not None:
            history.append(dict(event))
        sink = _sink.get()
        if sink is not None:
            sink(event)


def log(message: str, *, level: str = "info") -> None:
    """Emit a console log line (info | success | warn | error | debug)."""
    label = _label.get()
    text = f"[{label}] {message}" if label else str(message)
    event = {"type": "log", "level": level, "message": text}
    if label:
        # The lane as a structured field beside the [label] text prefix,
        # so the console can render parallel tracks as separate rails
        # while the raw view and persisted logs keep the prefixed line.
        event["lane"] = label
    _emit(event)


def step(label: str, *, value: float | None = None) -> None:
    """Emit a named step; optionally also set the progress fraction (0..1)."""
    _stage.set(str(label))
    _emit({"type": "step", "label": str(label)})
    if value is not None:
        set_progress(value, label=label)
    # A stage boundary is the natural moment to refresh the console's
    # time/cost table. Lazy import mirrors stream(); a run with no
    # recorded usage emits nothing extra. console_summary carries the
    # stage table on the LIVE event only — never a persisted summary.
    from . import openai_usage

    if openai_usage.is_tracking():
        summary = openai_usage.console_summary()
        if summary.get("request_count"):
            usage(summary)


def set_progress(value: float, *, label: str = "") -> None:
    """Set the progress bar fraction (clamped to 0..1).

    No remaining-time estimate is emitted: model stages are too uneven for
    a rate-derived ETA to be honest, so the stream reports what actually
    happened (steps, logs, percent) and nothing predictive.
    """
    v = max(0.0, min(1.0, float(value)))
    with _emit_lock:
        track = _track.get()
        if track is not None:
            track.append((time.time(), v))
    _emit({"type": "progress", "value": v, "label": str(label)})


def current_value() -> float:
    """The last progress fraction emitted by the active run (0.0 if none)."""
    track = _track.get()
    if track:
        return float(track[-1][1])
    return 0.0


def usage(data: dict) -> None:
    """Emit the latest aggregate OpenAI usage for the active run."""
    _emit({"type": "usage", "data": data})


def current_events(*, limit: int | None = None) -> list[dict]:
    """Return a copy of events emitted by the active streamed operation.

    Upload-backed generation uses this to persist the same diagnostic log the
    browser saw.  The history is scoped to one worker context, so concurrent
    runs cannot leak messages into each other.
    """
    events = list(_history.get() or [])
    if limit is not None:
        bounded = max(0, int(limit))
        events = [] if bounded == 0 else events[-bounded:]
    return events


def stream(
    fn: Callable[[], Any],
    *,
    title: str = "",
    journal_job_id: int | None = None,
) -> "StreamingResponse":  # type: ignore[name-defined]
    """Run ``fn`` in a worker thread, streaming its progress as NDJSON.

    The final event is ``{"type":"result","data":...}`` on success or
    ``{"type":"error","message":...}`` on failure.

    ``journal_job_id`` tees every event — the terminal result/error
    included — into that job's durable run journal, stamped with a
    monotonic ``seq`` (run_journal.py). The work itself already survives
    a dropped client; the journal preserves the events emitted while
    nobody was attached, so a returning client catches up losslessly via
    ``run-events?after=N`` instead of guessing from job status.
    """
    from fastapi.responses import StreamingResponse

    events: "queue.Queue[Any]" = queue.Queue()

    journal = None
    if journal_job_id is not None:
        from . import run_journal

        try:
            journal = run_journal.RunJournal(journal_job_id)
        except OSError:
            journal = None  # an assist, never a gate

    def publish(event: dict) -> None:
        if journal is not None:
            journal.publish(event, events.put)
        else:
            events.put(event)

    def sink(event: dict) -> None:
        publish(event)

    def worker() -> None:
        token = _sink.set(sink)
        history_token = _history.set([])
        track_token = _track.set([])
        from . import openai_usage

        usage_token = openai_usage.start_tracking()
        try:
            if title:
                log(title)
            result = fn()
            summary = openai_usage.visible_summary()
            if (
                summary["request_count"] > 0
                and isinstance(result, dict)
                and "openai_usage" not in result
            ):
                result = {**result, "openai_usage": summary}
            publish({"type": "result", "data": result, "ts": time.time()})
        except Exception as exc:  # noqa: BLE001 — surface to the client stream
            publish({
                "type": "error",
                "message": str(exc) or exc.__class__.__name__,
                "trace": traceback.format_exc(limit=4),
                "openai_usage": openai_usage.visible_summary(),
                "ts": time.time(),
            })
        finally:
            openai_usage.stop_tracking(usage_token)
            _track.reset(track_token)
            _history.reset(history_token)
            _sink.reset(token)
            if journal is not None:
                journal.close()
            events.put(_SENTINEL)

    def generator() -> Iterator[bytes]:
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        # A periodic heartbeat keeps proxies from buffering/closing the stream.
        while True:
            try:
                item = events.get(timeout=15)
            except queue.Empty:
                yield (json.dumps({"type": "heartbeat", "ts": time.time()}) + "\n").encode()
                continue
            if item is _SENTINEL:
                break
            yield (json.dumps(item, ensure_ascii=False) + "\n").encode()

    return StreamingResponse(
        generator(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )

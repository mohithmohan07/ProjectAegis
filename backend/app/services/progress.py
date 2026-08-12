"""Live progress + log plumbing for long-running operations.

Service code calls :func:`log` / :func:`step` / :func:`set_progress` while it
works. When a request is served through :func:`stream`, those calls are routed
to an NDJSON event stream the frontend renders as a CLI-style console with a
progress bar. Outside a streaming context the calls are cheap no-ops, so the
same service functions keep working for non-streaming callers and tests.
"""
from __future__ import annotations

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
# ETA estimate.
_track: contextvars.ContextVar[list | None] = contextvars.ContextVar(
    "aegis_progress_track", default=None,
)

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
    _emit({"type": "log", "level": level, "message": str(message)})


def step(label: str, *, value: float | None = None) -> None:
    """Emit a named step; optionally also set the progress fraction (0..1)."""
    _emit({"type": "step", "label": str(label)})
    if value is not None:
        set_progress(value, label=label)


def _eta_seconds(value: float) -> int | None:
    """Remaining-time estimate from the run's observed progress rate.

    Blends the overall rate with the recent five-minute rate so long model
    stages that slow down late do not keep promising an optimistic finish.
    Returns None until there is enough signal to be honest about.
    """
    track = _track.get()
    if track is None:
        return None
    now = time.time()
    if track and value + 0.05 < track[-1][1]:
        # A restarted/rewound bar invalidates earlier samples.
        del track[:]
    track.append((now, value))
    if len(track) < 2 or value >= 0.999:
        return None
    t0, v0 = track[0]
    if value <= v0 + 0.01 or now - t0 < 15:
        return None
    overall = (value - v0) / (now - t0)
    rate = overall
    window = [sample for sample in track if now - sample[0] <= 300]
    if len(window) >= 2 and window[-1][1] > window[0][1] + 0.005:
        recent = (
            (window[-1][1] - window[0][1]) / max(1e-6, window[-1][0] - window[0][0])
        )
        rate = 0.5 * overall + 0.5 * recent
    if rate <= 0:
        return None
    return max(1, int(round((1.0 - value) / rate)))


def _eta_label(seconds: int) -> str:
    if seconds < 90:
        return f"~{max(5, (seconds // 5) * 5)} s left"
    minutes = (seconds + 30) // 60
    if minutes < 90:
        return f"~{minutes} min left"
    return f"~{minutes / 60:.1f} h left"


def set_progress(value: float, *, label: str = "") -> None:
    """Set the progress bar fraction (clamped to 0..1) with a live ETA."""
    v = max(0.0, min(1.0, float(value)))
    event: dict = {"type": "progress", "value": v, "label": str(label)}
    with _emit_lock:
        eta = _eta_seconds(v)
    if eta is not None:
        event["eta_seconds"] = eta
        event["eta_label"] = _eta_label(eta)
    _emit(event)


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
) -> "StreamingResponse":  # type: ignore[name-defined]
    """Run ``fn`` in a worker thread, streaming its progress as NDJSON.

    The final event is ``{"type":"result","data":...}`` on success or
    ``{"type":"error","message":...}`` on failure.
    """
    from fastapi.responses import StreamingResponse

    events: "queue.Queue[Any]" = queue.Queue()

    def sink(event: dict) -> None:
        events.put(event)

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
            events.put({"type": "result", "data": result, "ts": time.time()})
        except Exception as exc:  # noqa: BLE001 — surface to the client stream
            events.put({
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

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

_SENTINEL = object()


def _emit(event: dict) -> None:
    sink = _sink.get()
    if sink is not None:
        event.setdefault("ts", time.time())
        sink(event)


def log(message: str, *, level: str = "info") -> None:
    """Emit a console log line (info | success | warn | error | debug)."""
    _emit({"type": "log", "level": level, "message": str(message)})


def step(label: str, *, value: float | None = None) -> None:
    """Emit a named step; optionally also set the progress fraction (0..1)."""
    _emit({"type": "step", "label": str(label)})
    if value is not None:
        set_progress(value, label=label)


def set_progress(value: float, *, label: str = "") -> None:
    """Set the progress bar fraction (clamped to 0..1)."""
    v = max(0.0, min(1.0, float(value)))
    _emit({"type": "progress", "value": v, "label": str(label)})


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
        try:
            if title:
                log(title)
            result = fn()
            events.put({"type": "result", "data": result, "ts": time.time()})
        except Exception as exc:  # noqa: BLE001 — surface to the client stream
            events.put({
                "type": "error",
                "message": str(exc) or exc.__class__.__name__,
                "trace": traceback.format_exc(limit=4),
                "ts": time.time(),
            })
        finally:
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

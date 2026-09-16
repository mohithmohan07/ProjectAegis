"""Cooperative suspension at paid-work boundaries, without semantic failure.

The control exception deliberately bypasses ``except Exception`` author/Fixer
loops. A deployment or an unfinished provider batch is not invalid content.
Durable decisions and batch records remain the resume boundary.
"""
from __future__ import annotations

import threading

_pausing = threading.Event()


class RunDeferred(BaseException):
    def __init__(self, message: str, *, reason: str = "batch_wait", delay: float = 30):
        super().__init__(message)
        self.reason = reason
        self.delay = max(1.0, float(delay))


def request_pause() -> None:
    _pausing.set()


def reset() -> None:
    _pausing.clear()


def pausing() -> bool:
    return _pausing.is_set()


def check() -> None:
    if pausing():
        raise RunDeferred(
            "The server is updating. Saved work will resume automatically.",
            reason="deployment", delay=5,
        )


def install_shutdown_handlers():
    """Pause paid-work admission before Uvicorn drains active HTTP requests.

    Uvicorn installs callable handlers before entering lifespan. Chaining those
    handlers preserves its normal graceful/forced exit behavior; non-main-thread
    TestClient lifespans cannot and need not install OS signal handlers.
    """
    import signal
    if threading.current_thread() is not threading.main_thread():
        return lambda: None
    installed = {}
    for signum in (signal.SIGTERM, signal.SIGINT):
        previous = signal.getsignal(signum)
        if not callable(previous):
            continue
        def handler(number, frame, previous=previous):
            request_pause()
            previous(number, frame)
        signal.signal(signum, handler)
        installed[signum] = (previous, handler)
    def restore():
        for signum, (previous, handler) in installed.items():
            if signal.getsignal(signum) is handler:
                signal.signal(signum, previous)
    return restore

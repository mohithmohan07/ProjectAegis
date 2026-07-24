"""In-process coordination for the shared Bulk Import output workbook.

Every read/modify/write cycle must hold the same re-entrant lock. The lock is
deliberately process-local: it protects the current single-worker deployment,
but it is not a substitute for a distributed lock when multiple application
processes share the same filesystem.
"""
from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
from threading import RLock
from typing import Callable, Iterator, ParamSpec, TypeVar


_OUTPUT_WORKBOOK_LOCK = RLock()
_P = ParamSpec("_P")
_R = TypeVar("_R")


@contextmanager
def output_workbook_lock() -> Iterator[None]:
    """Serialize access to the canonical output workbook in this process."""
    with _OUTPUT_WORKBOOK_LOCK:
        yield


def synchronized_output_workbook(
    function: Callable[_P, _R],
) -> Callable[_P, _R]:
    """Wrap a complete workbook operation in the process-wide lock."""
    @wraps(function)
    def wrapped(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        with output_workbook_lock():
            return function(*args, **kwargs)

    return wrapped


def atomic_save_workbook(workbook, target: Path) -> None:
    """Save an openpyxl workbook to a sibling temp file, then publish it."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{target.stem}-",
        suffix=target.suffix or ".xlsx",
        dir=target.parent,
    )
    os.close(descriptor)
    temporary = Path(temp_name)
    try:
        workbook.save(temporary)
        # Release any handle retained from load_workbook before replacing the
        # original path; Windows otherwise rejects the atomic publish.
        workbook.close()
        os.replace(temporary, target)
    finally:
        workbook.close()
        temporary.unlink(missing_ok=True)


def atomic_publish(staged: Path, target: Path) -> None:
    """Atomically publish an already-written sibling workbook."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(Path(staged), target)

"""In-process coordination for the shared Bulk Import output workbook.

Every read/modify/write cycle must hold the same re-entrant lock. The lock is
deliberately process-local: it protects the current single-worker deployment,
but it is not a substitute for a distributed lock when multiple application
processes share the same filesystem.

Publication uses a durable filesystem outbox so the database and the exported
workbook cannot silently diverge: the intent to publish a staged sibling is
recorded before the database transaction commits, and any interrupted
publication is completed the next time the workbook is touched under the lock.
"""
from __future__ import annotations

import json
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
_OUTBOX_SUFFIX = ".outbox.json"


class WorkbookPublicationPending(RuntimeError):
    """The database committed but the workbook export is still queued.

    The staged workbook and its durable outbox record survive this failure;
    :func:`recover_pending_publication` completes the export on the next
    workbook operation, so the canonical database state and the exported
    workbook converge instead of diverging.
    """


def _outbox_path(target: Path) -> Path:
    return Path(str(Path(target)) + _OUTBOX_SUFFIX)


def _fsync_write(path: Path, payload: bytes) -> None:
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def record_publication_intent(staged: Path, target: Path) -> None:
    """Durably record that ``staged`` must replace ``target``.

    Called while holding the workbook lock, immediately before the owning
    database transaction commits. Once this record exists, publication is
    guaranteed to complete (now, or on recovery) or the record explains what
    remains.
    """

    _fsync_write(
        _outbox_path(target),
        json.dumps({
            "version": 1,
            "staged": str(Path(staged)),
            "target": str(Path(target)),
        }, ensure_ascii=False).encode("utf-8"),
    )


def clear_publication_intent(target: Path) -> None:
    _outbox_path(Path(target)).unlink(missing_ok=True)


def recover_pending_publication(target: Path) -> bool:
    """Complete an interrupted staged publication for ``target``.

    Returns True when a pending staged workbook was published. A record whose
    staged file no longer exists means the previous ``os.replace`` succeeded
    but the record removal was interrupted; that record is simply cleared.
    Callers must hold the workbook lock.
    """

    target = Path(target)
    outbox = _outbox_path(target)
    if not outbox.exists():
        return False
    try:
        record = json.loads(outbox.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        record = {}
    staged_name = str(record.get("staged") or "")
    staged = Path(staged_name) if staged_name else None
    if (
        staged is not None
        and staged.exists()
        and str(record.get("target") or "") == str(target)
    ):
        os.replace(staged, target)
        outbox.unlink(missing_ok=True)
        return True
    outbox.unlink(missing_ok=True)
    return False


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

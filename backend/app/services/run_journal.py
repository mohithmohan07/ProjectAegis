"""Durable per-job journal of a run's streamed events, with a cursor.

The run itself already survives a dropped connection — ``progress.stream``
executes the work on a daemon thread that never blocks on the client. What
was lost was the EVIDENCE: every event emitted while a phone tab was
frozen vanished with the closed stream, so a returning client could only
poll job status and guess. This journal tees every streamed event —
including the terminal ``result``/``error`` — into an append-only NDJSON
file under the job's upload directory, stamped with a monotonic ``seq``.
A returning client asks ``run-events?after=N`` and silently catches up to
the exact current state; the same ``seq`` rides the live stream so the
client can dedupe an overlap instead of double-printing.

Mechanics only: sequence numbers, file appends, cursor reads. The journal
is per-run — starting a new stream for the job truncates it — and lives
beside the job's other artifacts, so a data reset removes it with them.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Callable

from .. import config

JOURNAL_FILENAME = "run-events.ndjson"

# Event types worth replaying to a catch-up reader. Heartbeats are
# generator-side padding and never reach the journal at all.
_SKIP_TYPES = frozenset({"heartbeat"})


def journal_path(job_id: int) -> Path:
    return config.UPLOAD_DIR / str(int(job_id)) / JOURNAL_FILENAME


class RunJournal:
    """One run's append-only event file. Thread-safe.

    ``publish`` stamps ``seq``, appends the line, and hands the stamped
    event to ``sink`` inside ONE critical section, so the live stream's
    order, the file's byte order, and the sequence numbers can never
    disagree — worker pool threads all emit through the same instance.
    """

    def __init__(self, job_id: int) -> None:
        self._path = journal_path(job_id)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._seq = 0
        # A new stream is a new run: the journal restarts with it.
        self._handle = open(self._path, "w", encoding="utf-8")

    def publish(
        self, event: dict[str, Any], sink: Callable[[dict[str, Any]], None],
    ) -> None:
        if not isinstance(event, dict) or event.get("type") in _SKIP_TYPES:
            sink(event)
            return
        with self._lock:
            self._seq += 1
            stamped = {**event, "seq": self._seq}
            try:
                self._handle.write(
                    json.dumps(stamped, ensure_ascii=False) + "\n"
                )
                self._handle.flush()
            except (OSError, ValueError):
                # The journal is an assist, never a gate: a full disk or a
                # closed handle must not take the live stream down.
                pass
            sink(stamped)

    def close(self) -> None:
        with self._lock:
            try:
                self._handle.close()
            except OSError:
                pass


def read_after(job_id: int, after: int) -> dict[str, Any]:
    """Every journaled event with ``seq > after``, plus the next cursor."""

    path = journal_path(job_id)
    events: list[dict[str, Any]] = []
    next_cursor = max(0, int(after))
    if path.exists():
        try:
            with open(path, encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue  # a torn tail line from a live append
                    seq = event.get("seq")
                    if not isinstance(seq, int):
                        continue
                    if seq > next_cursor:
                        events.append(event)
                        next_cursor = seq
        except OSError:
            pass
    return {"events": events, "next": next_cursor}

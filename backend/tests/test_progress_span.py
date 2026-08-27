"""progress.Span — the fixed band the long tail stages fill per unit.

Mechanics only (CLAUDE.md: progress is mechanics): a Span maps unit
counts into a fixed slice of the bar, and its emission is monotone even
when two concurrent writers (the two Master lanes) report out of step.
"""
from __future__ import annotations

import threading

from app.services import progress


def _capture(monkeypatch) -> list[tuple[float, str]]:
    emitted: list[tuple[float, str]] = []
    monkeypatch.setattr(
        progress,
        "set_progress",
        lambda value, *, label="": emitted.append((value, label)),
    )
    return emitted


def test_span_fills_its_band_linearly(monkeypatch):
    emitted = _capture(monkeypatch)
    span = progress.Span(0.5, 0.7, label="band")
    tracker = span.tracker(4)
    tracker.advance(1.0, label="one")
    tracker.advance(1.0, label="two")
    tracker.set_units(4.0, label="all")
    values = [round(value, 6) for value, _ in emitted]
    assert values == [0.55, 0.6, 0.7]
    assert emitted[0][1] == "one"
    assert emitted[-1][1] == "all"


def test_span_emission_is_monotone_across_two_writers(monkeypatch):
    emitted = _capture(monkeypatch)
    span = progress.Span(0.9, 1.0)
    fast = span.tracker(10)
    slow = span.tracker(10)
    fast.set_units(8)
    slow.set_units(1)  # combined fraction rises; never re-emits lower
    slow.set_units(2)
    fast.set_units(9)
    values = [value for value, _ in emitted]
    assert values == sorted(values)
    assert all(0.9 <= value <= 1.0 for value in values)


def test_span_writer_cursors_never_move_backward(monkeypatch):
    emitted = _capture(monkeypatch)
    span = progress.Span(0.0, 1.0)
    tracker = span.tracker(10)
    tracker.set_units(5)
    tracker.set_units(3)  # ignored: a cursor only moves forward
    tracker.set_units(5)  # no re-emission of an already-emitted value
    values = [value for value, _ in emitted]
    assert values == [0.5]


def test_span_clamps_overflow_to_its_end(monkeypatch):
    emitted = _capture(monkeypatch)
    span = progress.Span(0.2, 0.4)
    tracker = span.tracker(2)
    tracker.advance(5.0)
    assert [round(value, 6) for value, _ in emitted] == [0.4]


def test_zero_total_tracker_reports_complete(monkeypatch):
    emitted = _capture(monkeypatch)
    span = progress.Span(0.1, 0.2)
    tracker = span.tracker(0)
    tracker.set_units(0.0, label="empty stage")
    assert [round(value, 6) for value, _ in emitted] == [0.2]


def test_concurrent_advances_stay_monotone(monkeypatch):
    emitted = _capture(monkeypatch)
    span = progress.Span(0.0, 1.0)
    trackers = [span.tracker(50) for _ in range(2)]

    def hammer(tracker) -> None:
        for _ in range(50):
            tracker.advance(1.0)

    threads = [
        threading.Thread(target=hammer, args=(tracker,))
        for tracker in trackers
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    values = [value for value, _ in emitted]
    assert values == sorted(values)
    assert abs(values[-1] - 1.0) < 1e-9

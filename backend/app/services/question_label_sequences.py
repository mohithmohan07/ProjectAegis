"""Durable question-number bookkeeping; no content or category judgments.

Ranges are allocated with one database upsert per family, in the caller's
transaction. Callers must commit before exposing the labels outside that
transaction. The Master runner uses a short dedicated transaction before its
external Refiner calls. Gaps are intentional; numbers are never reclaimed.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping

from sqlalchemy import case, select

MAX_LABEL_INDEX = (1 << 63) - 1


class QuestionLabelExhausted(ValueError):
    """A family cannot issue another number within the database integer range."""


def _parts(label: object) -> tuple[str, int] | None:
    base, separator, tail = str(label or "").rpartition(" Q")
    if not separator or not tail.isdigit():
        return None
    try:
        number = int(tail)
    except ValueError:
        return None
    return base, number


def _insert(connection, table):
    dialect = connection.dialect.name
    if dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    elif dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        raise NotImplementedError(
            f"atomic question label allocation is not supported for {dialect}"
        )
    return insert(table)


def _advance(connection, base: str, floor: int, count: int = 0) -> int:
    from ..models import QuestionLabelSequence

    table = QuestionLabelSequence.__table__
    if count and floor > MAX_LABEL_INDEX - count:
        raise QuestionLabelExhausted(f"question label range exhausted for {base!r}")
    # Oversized historical/imported labels remain verbatim and bootable. Their
    # family is saturated, so generation cannot reuse a smaller number. Check
    # the UPDATE too: another transaction may have advanced after our scan.
    floor = min(floor, MAX_LABEL_INDEX)
    current = table.c.last_issued
    statement = _insert(connection, table).values(
        family_base=base, last_issued=floor + count,
    ).on_conflict_do_update(
        index_elements=[table.c.family_base],
        set_={"last_issued": case((current > floor, current), else_=floor) + count},
        where=(current <= MAX_LABEL_INDEX - count),
    ).returning(table.c.last_issued)
    result = connection.execute(statement).scalar_one_or_none()
    if result is None:
        raise QuestionLabelExhausted(f"question label range exhausted for {base!r}")
    return int(result)


def record_issued_labels(connection, labels: Iterable[object]) -> None:
    """Remember imported/staged labels, retaining every previous highwater."""
    floors: dict[str, int] = {}
    for label in labels:
        parsed = _parts(label)
        if parsed is not None:
            base, number = parsed
            floors[base] = max(floors.get(base, 0), number)
    # Use a stable lock order when a batch carries several families.
    for base, floor in sorted(floors.items()):
        _advance(connection, base, floor)


def release_labels(payload: object) -> Iterable[object]:
    if isinstance(payload, Mapping):
        for candidate in payload.get("candidates") or []:
            if isinstance(candidate, Mapping):
                yield candidate.get("question_label")


def _live_highest(connection, base: str) -> int:
    from ..models import Question

    prefix = f"{base} Q"
    labels = connection.execute(select(Question.question_label).where(
        Question.question_label.startswith(prefix, autoescape=True)
    )).scalars()
    return max((
        parsed[1] for label in labels
        if (parsed := _parts(label)) is not None and parsed[0] == base
    ), default=0)


def next_label_index(db, base: str) -> int:
    """Non-mutating peek. Only reserve_label_indices grants a number."""
    from ..models import QuestionLabelSequence

    connection = db.connection()
    recorded = connection.execute(select(QuestionLabelSequence.last_issued).where(
        QuestionLabelSequence.family_base == base
    )).scalar_one_or_none()
    return max(int(recorded or 0), _live_highest(connection, base)) + 1


def reserve_label_indices(
    db, counts: Mapping[str, int], *, reservation_key: str | None = None,
) -> dict[str, int]:
    """Atomically reserve a complete range per base, without committing db.

    The upsert serializes first allocation as well as continuation; reading a
    maximum then incrementing a Python counter alone is not a reservation.
    Flushing first also accounts for supplied labels pending in this unit of
    work. A rollback before publication rolls back this unissued reservation.
    """
    for base, count in counts.items():
        if not isinstance(base, str) or type(count) is not int or count < 0:
            raise ValueError("label reservations require string bases and nonnegative integer counts")
    if not any(counts.values()) and reservation_key is None:
        return {}
    db.flush()
    connection = db.connection()
    counts = {base: count for base, count in counts.items() if count}
    if reservation_key is not None:
        from ..models import QuestionLabelReservation

        table = QuestionLabelReservation.__table__
        inserted = connection.execute(_insert(connection, table).values(
            reservation_key=reservation_key, counts=dict(counts), starts={},
        ).on_conflict_do_nothing(
            index_elements=[table.c.reservation_key],
        ).returning(table.c.reservation_key)).scalar_one_or_none()
        if inserted is None:
            previous = connection.execute(select(table.c.counts, table.c.starts).where(
                table.c.reservation_key == reservation_key,
            )).one()
            if previous.counts != counts or set(previous.starts) != set(counts):
                raise ValueError("question label reservation does not match its recorded counts")
            return dict(previous.starts)
    starts = {}
    for base, count in sorted(counts.items()):
        if count:
            end = _advance(connection, base, _live_highest(connection, base), count)
            starts[base] = end - count + 1
    if reservation_key is not None:
        connection.execute(table.update().where(
            table.c.reservation_key == reservation_key,
        ).values(starts=starts))
    return starts


def backfill(connection) -> None:
    """Recover all retained history on startup, without rewriting any label.

    Previously deleted labels absent every surviving question/release cannot
    be reconstructed. From this migration onward the counter outlives both.
    """
    from ..models import AssessmentRelease, Question

    def labels():
        yield from connection.execute(select(Question.question_label)).scalars()
        for payload, snapshot in connection.execute(select(
            AssessmentRelease.payload, AssessmentRelease.concept_snapshot,
        )):
            yield from release_labels(payload)
            yield from release_labels(snapshot)

    record_issued_labels(connection, labels())

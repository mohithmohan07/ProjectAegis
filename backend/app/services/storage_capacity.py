"""Mechanical storage-capacity guards for durable Master publication.

The checks in this module judge only filesystem capacity.  They never inspect
or classify learner content.  A Master run reserves a conservative share of
the currently available filesystem before any provider work begins; the
publisher then performs a second check against the exact rendered byte count
before it creates its staging directory.

Reservations are process-local because the shipped container runs one Uvicorn
process.  The filesystem check remains authoritative at publication time, so
an out-of-process writer still fails closed before a partial release is
exposed.
"""
from __future__ import annotations

import errno
import logging
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Sequence

from .. import config

_LOGGER = logging.getLogger(__name__)

_MIB = 1024 * 1024
_DEFAULT_MASTER_RESERVATION_BYTES = 256 * _MIB
_DEFAULT_LEDGER_HEADROOM_BYTES = 16 * _MIB
_DEFAULT_PUBLICATION_MARGIN_BYTES = 8 * _MIB
_DEFAULT_MASTER_RESERVATION_INODES = 256
_DEFAULT_LEDGER_HEADROOM_INODES = 32

_RESERVATION_LOCK = threading.Lock()
_RESERVATIONS: dict[str, tuple[int, int]] = {}
_CURRENT_RESERVATION: ContextVar[tuple[str, int, int] | None] = ContextVar(
    "aegis_master_storage_reservation", default=None,
)


@dataclass(frozen=True)
class MasterBatchReservation:
    """Per-lane leases installed atomically for one sibling fan-out."""

    batch_token: str
    job_id: int
    lanes: tuple[str, ...]
    lane_tokens: tuple[tuple[str, str], ...]
    snapshot: CapacitySnapshot
    lane_bytes: int
    lane_inodes: int

    def token_for(self, lane: str) -> str:
        for reserved_lane, token in self.lane_tokens:
            if reserved_lane == str(lane):
                return token
        raise RuntimeError("Master lane has no storage reservation")


_CURRENT_BATCH_LANE: ContextVar[
    tuple[MasterBatchReservation, str] | None
] = ContextVar("aegis_master_batch_lane", default=None)


def _configured_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        _LOGGER.warning(
            "invalid %s=%r; using %d", name, raw, default,
        )
        return default


def master_reservation_bytes() -> int:
    return _configured_int(
        "AEGIS_MASTER_STORAGE_RESERVATION_BYTES",
        _DEFAULT_MASTER_RESERVATION_BYTES,
    )


def master_reservation_inodes() -> int:
    return _configured_int(
        "AEGIS_MASTER_STORAGE_RESERVATION_INODES",
        _DEFAULT_MASTER_RESERVATION_INODES,
    )


def ledger_headroom_bytes() -> int:
    return _configured_int(
        "AEGIS_STORAGE_LEDGER_HEADROOM_BYTES",
        _DEFAULT_LEDGER_HEADROOM_BYTES,
    )


def ledger_headroom_inodes() -> int:
    return _configured_int(
        "AEGIS_STORAGE_LEDGER_HEADROOM_INODES",
        _DEFAULT_LEDGER_HEADROOM_INODES,
    )


def publication_margin_bytes() -> int:
    return _configured_int(
        "AEGIS_MASTER_PUBLICATION_MARGIN_BYTES",
        _DEFAULT_PUBLICATION_MARGIN_BYTES,
    )


@dataclass(frozen=True)
class CapacitySnapshot:
    path: str
    available_bytes: int
    available_inodes: int | None
    reserved_bytes: int = 0
    reserved_inodes: int = 0

    def as_dict(self) -> dict[str, int | str | None]:
        return {
            "path": self.path,
            "available_bytes": self.available_bytes,
            "available_inodes": self.available_inodes,
            "reserved_bytes": self.reserved_bytes,
            "reserved_inodes": self.reserved_inodes,
        }


class StorageCapacityError(RuntimeError):
    """A durable write cannot safely start or finish on this filesystem."""

    code = "insufficient_storage"
    retryable = True

    def __init__(
        self,
        message: str,
        *,
        phase: str,
        snapshot: CapacitySnapshot | None = None,
        required_bytes: int = 0,
        required_inodes: int = 0,
    ) -> None:
        super().__init__(message)
        self.phase = str(phase or "storage")
        self.snapshot = snapshot
        self.required_bytes = max(0, int(required_bytes))
        self.required_inodes = max(0, int(required_inodes))

    def details(self) -> dict[str, object]:
        return {
            "failure_code": self.code,
            "retryable": self.retryable,
            "storage_phase": self.phase,
            "required_bytes": self.required_bytes,
            "required_inodes": self.required_inodes,
            "capacity": self.snapshot.as_dict() if self.snapshot else {},
        }

    def public_detail(self) -> dict[str, object]:
        return {
            "code": self.code,
            "retryable": self.retryable,
            "message": str(self),
            "storage_phase": self.phase,
            "required_bytes": self.required_bytes,
            "required_inodes": self.required_inodes,
            "capacity": self.snapshot.as_dict() if self.snapshot else {},
        }


def capacity_snapshot(path: Path | None = None) -> CapacitySnapshot:
    target = Path(path or config.DATA_DIR)
    stat = os.statvfs(target)
    fragment_size = int(stat.f_frsize or stat.f_bsize or 1)
    # Some filesystems report no inode accounting (f_files == 0).  In that
    # case bytes remain enforceable and inode capacity is honestly unknown.
    available_inodes = (
        int(stat.f_favail) if int(stat.f_files or 0) > 0 else None
    )
    with _RESERVATION_LOCK:
        reserved_bytes = sum(value[0] for value in _RESERVATIONS.values())
        reserved_inodes = sum(value[1] for value in _RESERVATIONS.values())
    return CapacitySnapshot(
        path=str(target),
        available_bytes=int(stat.f_bavail) * fragment_size,
        available_inodes=available_inodes,
        reserved_bytes=reserved_bytes,
        reserved_inodes=reserved_inodes,
    )


def _insufficient(
    snapshot: CapacitySnapshot,
    *,
    required_bytes: int,
    required_inodes: int,
) -> bool:
    if snapshot.available_bytes < required_bytes:
        return True
    return bool(
        snapshot.available_inodes is not None
        and snapshot.available_inodes < required_inodes
    )


def _capacity_message(phase: str) -> str:
    return (
        f"Insufficient server filesystem capacity for {phase}. Free or "
        "expand the storage used by the application, then rebuild only this "
        "Master lane; its Concept File is unaffected."
    )


def _effective_snapshot_locked(path: Path) -> CapacitySnapshot:
    """Sample free capacity minus every process-local active reservation.

    The caller must hold ``_RESERVATION_LOCK`` so sampling and admission are
    one indivisible operation with respect to other Master batches/lanes.
    """

    stat = os.statvfs(path)
    fragment_size = int(stat.f_frsize or stat.f_bsize or 1)
    raw_inodes = int(stat.f_favail) if int(stat.f_files or 0) > 0 else None
    reserved_bytes = sum(value[0] for value in _RESERVATIONS.values())
    reserved_inodes = sum(value[1] for value in _RESERVATIONS.values())
    return CapacitySnapshot(
        path=str(path),
        available_bytes=max(
            0, int(stat.f_bavail) * fragment_size - reserved_bytes,
        ),
        available_inodes=(
            None
            if raw_inodes is None
            else max(0, raw_inodes - reserved_inodes)
        ),
        reserved_bytes=reserved_bytes,
        reserved_inodes=reserved_inodes,
    )


@contextmanager
def reserve_master_batch_capacity(
    *,
    job_id: int,
    lanes: Sequence[str],
) -> Iterator[MasterBatchReservation]:
    """Atomically admit and reserve an automatic Pre/Post Master batch.

    Unlike a check followed by independent lane reservations, every lane token
    is installed under the same lock as the capacity sample. Each token stays
    until its worker returns, when actual filesystem consumption replaces the
    estimate; a competing job therefore never observes partial admission.
    """

    resolved_lanes = tuple(str(lane) for lane in lanes)
    count = len(resolved_lanes)
    lane_bytes = master_reservation_bytes()
    lane_inodes = master_reservation_inodes()
    required_bytes = count * lane_bytes + ledger_headroom_bytes()
    required_inodes = count * lane_inodes + ledger_headroom_inodes()
    if len(set(resolved_lanes)) != count:
        raise ValueError("Master batch lanes must be unique")
    batch_token = f"batch:{int(job_id)}:{uuid.uuid4().hex}"
    lane_tokens = tuple(
        (lane, f"{batch_token}:{lane}") for lane in resolved_lanes
    )
    with _RESERVATION_LOCK:
        snapshot = _effective_snapshot_locked(Path(config.DATA_DIR))
        if _insufficient(
            snapshot,
            required_bytes=required_bytes,
            required_inodes=required_inodes,
        ):
            error = StorageCapacityError(
                _capacity_message("the Pre/Post Master batch"),
                phase="master_batch_preflight",
                snapshot=snapshot,
                required_bytes=required_bytes,
                required_inodes=required_inodes,
            )
            _LOGGER.error(
                "master batch storage reservation refused job=%s lanes=%s "
                "available_bytes=%s required_bytes=%s available_inodes=%s "
                "required_inodes=%s",
                job_id,
                resolved_lanes,
                snapshot.available_bytes,
                required_bytes,
                snapshot.available_inodes,
                required_inodes,
            )
            raise error
        # Install every lane token under this same lock. A concurrent batch
        # therefore sees either none of this reservation or all of it—never a
        # partially admitted sibling set.
        for _lane, lane_token in lane_tokens:
            _RESERVATIONS[lane_token] = (lane_bytes, lane_inodes)
    reservation = MasterBatchReservation(
        batch_token=batch_token,
        job_id=int(job_id),
        lanes=resolved_lanes,
        lane_tokens=lane_tokens,
        snapshot=snapshot,
        lane_bytes=lane_bytes,
        lane_inodes=lane_inodes,
    )
    _LOGGER.info(
        "master batch storage reserved job=%s lanes=%s bytes=%s inodes=%s",
        job_id,
        resolved_lanes,
        count * lane_bytes,
        count * lane_inodes,
    )
    try:
        yield reservation
    finally:
        with _RESERVATION_LOCK:
            for _lane, lane_token in lane_tokens:
                _RESERVATIONS.pop(lane_token, None)
        _LOGGER.info(
            "master batch storage reservation released job=%s lanes=%s",
            job_id,
            resolved_lanes,
        )


@contextmanager
def use_master_batch_lane(
    reservation: MasterBatchReservation,
    *,
    job_id: int,
    lane: str,
) -> Iterator[None]:
    """Bind one explicitly spawned worker to its owning batch allocation."""

    resolved_lane = str(lane)
    if int(job_id) != reservation.job_id or resolved_lane not in reservation.lanes:
        raise RuntimeError("Master worker does not belong to this storage batch")
    lane_token = reservation.token_for(resolved_lane)
    with _RESERVATION_LOCK:
        if lane_token not in _RESERVATIONS:
            raise RuntimeError("Master storage batch reservation is no longer active")
    token = _CURRENT_BATCH_LANE.set((reservation, resolved_lane))
    try:
        yield
    finally:
        _CURRENT_BATCH_LANE.reset(token)
        # Once this worker has finished, its real filesystem consumption is
        # already reflected by statvfs. Drop only its logical estimate so a
        # later sibling does not count the completed lane twice.
        with _RESERVATION_LOCK:
            _RESERVATIONS.pop(lane_token, None)


@contextmanager
def reserve_master_capacity(
    *,
    job_id: int,
    lane: str,
) -> Iterator[CapacitySnapshot]:
    """Reserve one lane's budget before its first expensive Master action."""

    requested_bytes = master_reservation_bytes()
    requested_inodes = master_reservation_inodes()
    headroom_bytes = ledger_headroom_bytes()
    headroom_inodes = ledger_headroom_inodes()
    token = f"{int(job_id)}:{str(lane)}:{uuid.uuid4().hex}"

    # Automatic sibling workers borrow their explicitly bound slice of the
    # already-held batch. They set their own publication allowance here, but
    # must not install a second global reservation for the same bytes.
    bound_batch = _CURRENT_BATCH_LANE.get()
    if bound_batch is not None:
        batch, bound_lane = bound_batch
        if int(job_id) != batch.job_id or str(lane) != bound_lane:
            raise RuntimeError("Master lane does not match its storage batch")
        lane_token = batch.token_for(bound_lane)
        with _RESERVATION_LOCK:
            if lane_token not in _RESERVATIONS:
                raise RuntimeError(
                    "Master storage batch reservation is no longer active"
                )
        context_token = _CURRENT_RESERVATION.set(
            (lane_token, batch.lane_bytes, batch.lane_inodes),
        )
        try:
            yield batch.snapshot
        finally:
            _CURRENT_RESERVATION.reset(context_token)
        return

    # Sampling and reservation happen under one lock so concurrent Pre/Post
    # threads cannot both approve the same available bytes.
    with _RESERVATION_LOCK:
        snapshot = _effective_snapshot_locked(Path(config.DATA_DIR))
        required_bytes = requested_bytes + headroom_bytes
        required_inodes = requested_inodes + headroom_inodes
        if _insufficient(
            snapshot,
            required_bytes=required_bytes,
            required_inodes=required_inodes,
        ):
            error = StorageCapacityError(
                _capacity_message("Master generation"),
                phase="master_preflight",
                snapshot=snapshot,
                required_bytes=required_bytes,
                required_inodes=required_inodes,
            )
            _LOGGER.error(
                "master storage preflight refused job=%s lane=%s "
                "available_bytes=%s required_bytes=%s available_inodes=%s "
                "required_inodes=%s",
                job_id,
                lane,
                snapshot.available_bytes,
                required_bytes,
                snapshot.available_inodes,
                required_inodes,
            )
            raise error
        _RESERVATIONS[token] = (requested_bytes, requested_inodes)

    context_token = _CURRENT_RESERVATION.set(
        (token, requested_bytes, requested_inodes),
    )
    _LOGGER.info(
        "master storage reserved job=%s lane=%s bytes=%s inodes=%s",
        job_id, lane, requested_bytes, requested_inodes,
    )
    try:
        yield snapshot
    finally:
        _CURRENT_RESERVATION.reset(context_token)
        with _RESERVATION_LOCK:
            _RESERVATIONS.pop(token, None)
        _LOGGER.info(
            "master storage reservation released job=%s lane=%s",
            job_id, lane,
        )


def require_publication_capacity(
    payload_bytes: int,
    *,
    required_inodes: int = 4,
    path: Path | None = None,
) -> CapacitySnapshot:
    """Check exact rendered bytes before any staging file is created."""

    snapshot = capacity_snapshot(path)
    current = _CURRENT_RESERVATION.get()
    own_bytes = current[1] if current else 0
    own_inodes = current[2] if current else 0
    # Other active Master reservations remain unavailable to this publisher;
    # this lane may consume the budget it reserved for itself.
    effective = CapacitySnapshot(
        path=snapshot.path,
        available_bytes=max(
            0,
            snapshot.available_bytes
            - max(0, snapshot.reserved_bytes - own_bytes),
        ),
        available_inodes=(
            None
            if snapshot.available_inodes is None
            else max(
                0,
                snapshot.available_inodes
                - max(0, snapshot.reserved_inodes - own_inodes),
            )
        ),
        reserved_bytes=snapshot.reserved_bytes,
        reserved_inodes=snapshot.reserved_inodes,
    )
    required_bytes = (
        max(0, int(payload_bytes))
        + publication_margin_bytes()
        + ledger_headroom_bytes()
    )
    required_inode_count = (
        max(0, int(required_inodes)) + ledger_headroom_inodes()
    )
    if _insufficient(
        effective,
        required_bytes=required_bytes,
        required_inodes=required_inode_count,
    ):
        raise StorageCapacityError(
            _capacity_message("Master publication"),
            phase="master_publication_preflight",
            snapshot=effective,
            required_bytes=required_bytes,
            required_inodes=required_inode_count,
        )
    return effective


def capacity_error_from(
    error: BaseException,
    *,
    phase: str,
) -> StorageCapacityError | None:
    """Normalize OS/quota capacity failures without parsing content text."""

    if isinstance(error, StorageCapacityError):
        return error
    candidate: BaseException = error
    original = getattr(error, "orig", None)
    if isinstance(original, BaseException):
        candidate = original
    error_number = getattr(candidate, "errno", None)
    sqlite_code = getattr(candidate, "sqlite_errorcode", None)
    sqlite_full = getattr(sqlite3, "SQLITE_FULL", 13)
    if error_number not in {errno.ENOSPC, errno.EDQUOT} and (
        sqlite_code != sqlite_full
    ):
        return None
    # A raw OS/SQLite failure does not prove which mounted filesystem was
    # full. Only attach capacity evidence when the exception itself names a
    # path from which an existing statvfs target can be derived; otherwise a
    # neutral empty snapshot is more accurate than falsely asserting /data.
    snapshot = None
    filename = getattr(candidate, "filename", None)
    if filename:
        try:
            evidence_path = Path(filename)
            while not evidence_path.exists():
                parent = evidence_path.parent
                if parent == evidence_path:
                    evidence_path = None
                    break
                evidence_path = parent
            if evidence_path is not None:
                snapshot = capacity_snapshot(evidence_path)
        except (OSError, TypeError, ValueError):
            snapshot = None
    return StorageCapacityError(
        _capacity_message(phase),
        phase=phase,
        snapshot=snapshot,
    )


def health_status() -> dict[str, object]:
    """Non-throwing storage state for the liveness response."""

    one_lane_bytes = master_reservation_bytes() + ledger_headroom_bytes()
    one_lane_inodes = (
        master_reservation_inodes() + ledger_headroom_inodes()
    )
    batch_bytes = 2 * master_reservation_bytes() + ledger_headroom_bytes()
    batch_inodes = (
        2 * master_reservation_inodes() + ledger_headroom_inodes()
    )
    try:
        snapshot = capacity_snapshot()
    except Exception as exc:
        # Liveness must remain available even if a platform-specific
        # ``statvfs`` implementation fails in a non-OSError shape.
        return {
            "status": "unknown",
            "error": type(exc).__name__,
            "one_lane_retry": {
                "ready": None,
                "required_bytes": one_lane_bytes,
                "required_inodes": one_lane_inodes,
            },
            "two_lane_batch": {
                "ready": None,
                "required_bytes": batch_bytes,
                "required_inodes": batch_inodes,
            },
            "master_required_bytes": batch_bytes,
            "master_required_inodes": batch_inodes,
        }
    effective = CapacitySnapshot(
        path=snapshot.path,
        available_bytes=max(
            0, snapshot.available_bytes - snapshot.reserved_bytes,
        ),
        available_inodes=(
            None
            if snapshot.available_inodes is None
            else max(0, snapshot.available_inodes - snapshot.reserved_inodes)
        ),
        reserved_bytes=snapshot.reserved_bytes,
        reserved_inodes=snapshot.reserved_inodes,
    )
    one_lane_ready = not _insufficient(
        effective,
        required_bytes=one_lane_bytes,
        required_inodes=one_lane_inodes,
    )
    batch_ready = not _insufficient(
        effective,
        required_bytes=batch_bytes,
        required_inodes=batch_inodes,
    )
    return {
        # Normal generation promises both Master siblings, so the aggregate
        # is healthy only when that complete batch can start. A lane-only
        # reviewer retry is reported separately and may still be possible.
        "status": "ok" if batch_ready else "critical",
        **effective.as_dict(),
        "one_lane_retry": {
            "ready": one_lane_ready,
            "required_bytes": one_lane_bytes,
            "required_inodes": one_lane_inodes,
        },
        "two_lane_batch": {
            "ready": batch_ready,
            "required_bytes": batch_bytes,
            "required_inodes": batch_inodes,
        },
        # Compatibility aliases: "Master required" now means the normal
        # two-lane generation promise, not the cheaper explicit retry path.
        "master_required_bytes": batch_bytes,
        "master_required_inodes": batch_inodes,
    }

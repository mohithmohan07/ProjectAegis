"""The background worker that makes a pushed chapter run by itself.

One supervisor thread claims queued tasks within an admission budget and runs
each in its own thread. There is nothing clever here on purpose: the hard parts
are the lease (``chapter_queue``) and the honesty of the outcomes, not the
threading.

**Admission is the part that decides whether this feature works.** The provider
gate is a process-wide semaphore of ``AEGIS_OPENAI_MAX_CONCURRENCY`` slots, and
``config.phase3_decision_workers`` fan-outs contend for it: one Step 01 presents
about one fan-out, one Step 02 presents two because the Post and Pre Master
lanes overlap. Over-subscribing does not merely slow a run down — sustained
queueing past ``AEGIS_OPENAI_SLOT_WAIT_TIMEOUT_SECONDS`` FAILS it, after real
money has been spent. So the worker holds a reserve back for interactive use
and refuses to start more work than the gate can carry.

The honest throughput that follows from those numbers is written down in
``docs/chapter-batch-console-contract.md`` §7 and is not flattering: on the
current machine a twelve-hour night clears roughly a dozen Step 01s or about
six Step 02s. Raising the concurrency knobs is not a lever; it is the failure.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from typing import Any

from .. import config, models
from . import chapter_batches, chapter_queue

log = logging.getLogger(__name__)

_GENERATION_KINDS = ("step01", "step02")
_PUBLISH_KINDS = ("publish",)

#: How much of the provider gate one running step presents. Step 02 runs the
#: Post and Pre Master lanes concurrently, so it presents two fan-outs.
_STEP_COST = {"step01": 1, "step02": 2, "publish": 0}


def _int_env(name: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return default


def max_concurrent_runs() -> int:
    return max(1, _int_env("AEGIS_QUEUE_MAX_CONCURRENT_RUNS", 2))


def max_concurrent_masters() -> int:
    return max(1, _int_env("AEGIS_QUEUE_MAX_CONCURRENT_MASTERS", 1))


def provider_reserve() -> int:
    """Slots never given to the queue, so a person can still run something."""
    return _int_env("AEGIS_QUEUE_PROVIDER_RESERVE", 16)


def poll_seconds() -> float:
    try:
        return max(1.0, float(os.environ.get("AEGIS_QUEUE_POLL_SECONDS", "5")))
    except (TypeError, ValueError):
        return 5.0


def enabled() -> bool:
    """The worker is on by default and can be turned off for a deployment."""
    return os.environ.get("AEGIS_QUEUE_WORKER", "1").strip().lower() not in {
        "0", "false", "no", "off",
    }


class ChapterQueueWorker:
    """Claim, run and settle queued chapter steps."""

    def __init__(
        self,
        session_factory: Callable[[], Any],
        *,
        runner: Callable[[Any, models.ChapterBatchTask], dict[str, Any]] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._session_factory = session_factory
        self._runner = runner or run_task
        self._sleep = sleep
        self._wake = threading.Condition()
        self._stopping = False
        self._supervisor: threading.Thread | None = None
        self._heartbeat: threading.Thread | None = None
        self._lock = threading.Lock()
        #: task id -> kind, for admission arithmetic AND for the sweep, which
        #: must never reclaim a task this process is actually running.
        self._in_flight: dict[int, str] = {}
        self._started_at = 0.0

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if self._supervisor is not None:
            return
        db = self._session_factory()
        try:
            # Before any thread runs: every lease held by a process that no
            # longer exists is recovered. A restart must not leave a chapter
            # looking busy forever.
            recovered = chapter_queue.reclaim_orphans(db)
            if recovered["requeued"] or recovered["failed"]:
                log.info(
                    "chapter queue: recovered %s interrupted task(s), "
                    "%s exhausted",
                    recovered["requeued"], recovered["failed"],
                )
        except Exception:  # noqa: BLE001 — a sweep must never block startup
            log.warning("chapter queue: startup sweep failed", exc_info=True)
        finally:
            db.close()

        self._stopping = False
        self._started_at = time.time()
        self._supervisor = threading.Thread(
            target=self._supervise, name="chapter-queue", daemon=True,
        )
        self._supervisor.start()
        self._heartbeat = threading.Thread(
            target=self._beat, name="chapter-queue-heartbeat", daemon=True,
        )
        self._heartbeat.start()

    def stop(self, *, wait: bool = True, timeout: float = 5.0) -> None:
        with self._wake:
            self._stopping = True
            self._wake.notify_all()
        if wait and self._supervisor is not None:
            self._supervisor.join(timeout=timeout)
        self._supervisor = None
        self._heartbeat = None

    def nudge(self) -> None:
        """Wake the supervisor now — a push should not wait for the poll."""
        with self._wake:
            self._wake.notify_all()

    def alive(self) -> bool:
        return bool(self._supervisor is not None and self._supervisor.is_alive())

    def status(self) -> dict[str, Any]:
        with self._lock:
            in_flight = dict(self._in_flight)
        return {
            "alive": self.alive(),
            "started_at": self._started_at,
            "in_flight": in_flight,
            "capacity": max_concurrent_runs(),
            "provider_reserve": provider_reserve(),
        }

    # -- admission ---------------------------------------------------------

    def _provider_budget(self) -> int:
        """Fan-outs the queue may have in flight without crowding the gate."""
        workers = max(1, config.phase3_decision_workers())
        usable = max(0, config.OPENAI_MAX_CONCURRENCY - provider_reserve())
        return max(1, usable // workers)

    def admits(self, kind: str) -> bool:
        with self._lock:
            in_flight = dict(self._in_flight)
        if kind in _PUBLISH_KINDS:
            # One publish at a time: they serialize on the one process-wide
            # output-workbook lock anyway, and a fan of them would hold the
            # shared threadpool that also serves the console's own poll.
            return not any(
                value in _PUBLISH_KINDS for value in in_flight.values()
            )
        generation = [
            value for value in in_flight.values() if value in _GENERATION_KINDS
        ]
        if len(generation) >= max_concurrent_runs():
            return False
        if kind == "step02":
            masters = [value for value in generation if value == "step02"]
            if len(masters) >= max_concurrent_masters():
                return False
        spent = sum(_STEP_COST.get(value, 1) for value in generation)
        return spent + _STEP_COST.get(kind, 1) <= self._provider_budget()

    # -- loops -------------------------------------------------------------

    def _supervise(self) -> None:
        while not self._stopping:
            try:
                started = self._dispatch_once()
            except Exception:  # noqa: BLE001 — a bad pass must not kill the loop
                log.warning("chapter queue: dispatch pass failed", exc_info=True)
                started = 0
            if self._stopping:
                break
            if started:
                continue
            with self._wake:
                if not self._stopping:
                    self._wake.wait(poll_seconds())

    def _dispatch_once(self) -> int:
        db = self._session_factory()
        started = 0
        try:
            running = set(self._in_flight_ids())
            chapter_queue.reclaim_orphans(db, in_flight=running)
            for kinds in (_PUBLISH_KINDS, _GENERATION_KINDS):
                for task in chapter_queue.claimable(db, kinds=kinds):
                    if self._stopping:
                        return started
                    if int(task.id) in running:
                        # ``claimable`` includes a lease that has expired, which
                        # is right for a lease nobody is renewing — but this
                        # process may still be running this very task behind a
                        # late heartbeat. Claiming it again would start a second
                        # thread on the same chapter and charge it twice.
                        continue
                    if not self.admits(str(task.kind)):
                        break
                    claimed = chapter_queue.claim(db, int(task.id))
                    if claimed is None:
                        continue
                    self._launch(int(claimed.id), str(claimed.kind))
                    started += 1
        finally:
            db.close()
        return started

    def _in_flight_ids(self) -> list[int]:
        with self._lock:
            return list(self._in_flight)

    def _launch(self, task_id: int, kind: str) -> None:
        with self._lock:
            self._in_flight[task_id] = kind
        thread = threading.Thread(
            target=self._run_one, args=(task_id,),
            name=f"chapter-queue-{kind}-{task_id}", daemon=True,
        )
        thread.start()

    def _run_one(self, task_id: int) -> None:
        db = self._session_factory()
        task = None
        try:
            task = db.get(models.ChapterBatchTask, int(task_id))
            if task is None:
                return
            verdict = chapter_queue.reconcile_before_dispatch(db, task)
            if verdict is not None:
                chapter_queue.finish(
                    db, task_id, state=verdict["state"],
                    failure_code=verdict.get("failure_code", ""),
                    error=verdict.get("error", ""),
                )
                return
            outcome = self._runner(db, task)
            self._settle(db, task_id, outcome)
            _schedule_checkpoint_backup(db, task)
        except Exception as exc:  # noqa: BLE001 — recorded, never lost
            log.warning("chapter queue: task %s failed", task_id, exc_info=True)
            try:
                # The failure may have left this session mid-transaction, and
                # settling the task is the one write that must still land —
                # otherwise the row stays leased and looks busy forever.
                db.rollback()
                self._settle(db, task_id, classify_exception(exc))
                _schedule_checkpoint_backup(db, task)
            except Exception:  # noqa: BLE001
                log.error(
                    "chapter queue: task %s could not be settled", task_id,
                    exc_info=True,
                )
        finally:
            db.close()
            with self._lock:
                self._in_flight.pop(int(task_id), None)
            self.nudge()

    def _settle(self, db, task_id: int, outcome: dict[str, Any]) -> None:
        state = str(outcome.get("state") or "failed")
        if state == "retry":
            task = db.get(models.ChapterBatchTask, int(task_id))
            attempts = int(task.attempt or 0) if task else 0
            budget = int(task.max_attempts or 0) if task else 0
            if attempts < budget:
                chapter_queue.requeue(
                    db, task_id, error=str(outcome.get("error") or ""),
                    refund_attempt=bool(outcome.get("refund_attempt")),
                )
                return
            chapter_queue.finish(
                db, task_id, state="failed",
                failure_code=str(outcome.get("failure_code") or "attempts_exhausted"),
                error=str(outcome.get("error") or ""),
            )
            return
        chapter_queue.finish(
            db, task_id, state=state,
            blocked_kind=str(outcome.get("blocked_kind") or ""),
            failure_code=str(outcome.get("failure_code") or ""),
            error=str(outcome.get("error") or ""),
            refund_attempt=bool(outcome.get("refund_attempt")),
        )

    def _beat(self) -> None:
        while not self._stopping:
            time.sleep(min(chapter_queue.HEARTBEAT_SECONDS, 15))
            ids = self._in_flight_ids()
            if not ids:
                continue
            db = self._session_factory()
            try:
                for task_id in ids:
                    if not chapter_queue.heartbeat(db, task_id):
                        # Someone else owns this lease now. The running call
                        # cannot be interrupted mid-flight, so say so loudly
                        # rather than let a silent double-run pass unnoticed.
                        log.warning(
                            "chapter queue: lost the lease on task %s while it "
                            "was still running", task_id,
                        )
            except Exception:  # noqa: BLE001
                log.debug("chapter queue: heartbeat pass failed", exc_info=True)
            finally:
                db.close()


# ---------------------------------------------------------------------------
# Step bodies
# ---------------------------------------------------------------------------

def _schedule_checkpoint_backup(db, task) -> None:
    """Mirror the finished run's checkpoint, exactly as the HTTP routes do.

    Every interactive generation act queues this in its ``finally`` — the
    Google Drive mirror is how a run survives losing the volume. A chapter run
    from the console must not be the one kind of run that is never backed up.
    Success and failure both qualify: a failed run's checkpoint is what a
    resume needs most.
    """
    from . import drive_checkpoints

    if task is None:
        return
    try:
        row = db.get(models.ChapterBatchRow, int(task.batch_row_id))
        if row is not None and row.job_id:
            drive_checkpoints.schedule_checkpoint_backup(int(row.job_id))
    except Exception:  # noqa: BLE001 — a mirror is an assist, never a gate
        log.debug("chapter queue: could not queue a checkpoint backup",
                  exc_info=True)


def classify_exception(exc: BaseException) -> dict[str, Any]:
    """Turn a raised step into an outcome a person can act on.

    Every branch here answers one question honestly: does this need a person,
    is it worth another charge, or is it over. Nothing is guessed from the
    words in a message — each case is a typed exception the pipeline raises.
    """
    from . import storage_capacity, uploads

    if isinstance(exc, uploads.JobAlreadyRunningError):
        # Another route holds the per-job lock. This was never a real try, so
        # the attempt is refunded and the row goes back in line.
        return {
            "state": "retry", "refund_attempt": True,
            "error": "another operation held this upload; it will be retried",
        }
    if isinstance(exc, storage_capacity.StorageCapacityError):
        return {
            "state": "blocked", "blocked_kind": "storage_capacity",
            "error": str(exc),
        }
    try:
        from . import semantic_recovery

        if isinstance(exc, semantic_recovery.HumanDecisionRequired):
            return {
                "state": "blocked", "blocked_kind": "human_decision",
                "error": str(exc),
            }
    except ImportError:  # pragma: no cover - the module is always present
        pass
    try:
        from . import master_review

        if isinstance(exc, master_review.MasterReviewConflict):
            return {
                "state": "blocked", "blocked_kind": "publication_order",
                "error": str(exc),
            }
    except ImportError:  # pragma: no cover
        pass
    return {"state": "retry", "error": str(exc) or exc.__class__.__name__}


def run_task(db, task: models.ChapterBatchTask) -> dict[str, Any]:
    """Execute one claimed task and report what actually happened."""
    row = db.get(models.ChapterBatchRow, int(task.batch_row_id))
    if row is None or not row.job_id:
        return {
            "state": "failed", "failure_code": "job_missing",
            "error": "the staged upload for this chapter is gone",
        }
    kind = str(task.kind or "")
    if kind == "step01":
        return _run_step01(db, row, task)
    if kind == "step02":
        return _run_step02(db, row, task)
    if kind == "publish":
        return _run_publish(db, row, task)
    return {
        "state": "failed", "failure_code": "wrong_state",
        "error": f"unknown step {kind!r}",
    }


def _job_owner(db, job_id: int) -> str:
    job = db.get(models.UploadJob, int(job_id))
    return str(job.owner_sub or "") if job is not None else ""


def _run_step01(db, row: models.ChapterBatchRow, task) -> dict[str, Any]:
    """Convert the staged source if it still needs it, then Step 01.

    Conversion is part of generating the Concept files, not a separate push:
    the owner stages a PDF and pushes once. Spending only ever starts at the
    push, never at the upload.
    """
    from . import build_concepts_release_contract as release_contract
    from . import openai_usage, progress, uploads

    job_id = int(row.job_id)
    # Every service call below resolves the job through ``uploads.get_job``,
    # which filters on ``owner_sub``. Passing the person who pushed the button
    # would raise UploadJobNotFound before a single call. Who acted is recorded
    # on the batch row, never by rewriting the job's owner.
    owner_sub = _job_owner(db, job_id)

    with openai_usage.track(), progress.capture_to_journal(
        job_id, title="Step 01 — generating the Concept files",
    ) as capture:
        job = db.get(models.UploadJob, job_id)
        if job is not None and str(job.status or "") == "uploaded":
            uploads.convert_job(
                db, job_id, owner_sub=owner_sub, module="build_concepts",
            )
        result = uploads.run_with_openai_usage(
            db,
            job_id,
            lambda: release_contract.generate_post_learning(
                db, job_id, int(row.chapter_id), owner_sub=owner_sub,
            ),
            owner_sub=owner_sub,
        )
        capture.set_result(result)
    return _after_generation(db, job_id, result)


def _run_step02(db, row: models.ChapterBatchRow, task) -> dict[str, Any]:
    from . import build_concepts_release_contract as release_contract
    from . import openai_usage, progress, uploads

    job_id = int(row.job_id)
    owner_sub = _job_owner(db, job_id)
    # The Master build takes its own atomic storage reservation inside
    # ``_build_master_siblings``; a refusal arrives here as StorageCapacityError
    # and becomes a visible blocked row rather than a retry into a full volume.
    with openai_usage.track(), progress.capture_to_journal(
        job_id, title="Step 02 — building the Master files",
        continue_existing=True,
    ) as capture:
        result = uploads.run_with_openai_usage(
            db,
            job_id,
            lambda: release_contract.build_review_masters(
                db, job_id, owner_sub=owner_sub,
            ),
            owner_sub=owner_sub,
        )
        capture.set_result(result)
    return _after_generation(db, job_id, result)


def _after_generation(db, job_id: int, result: Any) -> dict[str, Any]:
    """A step that returned still has to be read honestly.

    The Step 01 review pause is a SUCCESS — it is what Step 01 is for. What is
    not success is a run that stopped on a decision only a person can make, or
    one that recorded a do-not-resume verdict on its way out.
    """
    from . import generation_recovery

    job = db.get(models.UploadJob, int(job_id))
    if job is None:
        return {
            "state": "failed", "failure_code": "job_missing",
            "error": "the upload disappeared while this step ran",
        }
    db.refresh(job)
    blocked = generation_recovery.blocked_recovery(job)
    if blocked:
        return {
            "state": "failed", "failure_code": "non_resumable",
            "error": str(
                blocked.get("recovery_action") or blocked.get("message") or ""
            ),
        }
    pending = job.pending_decision
    if pending:
        return {
            "state": "blocked", "blocked_kind": "human_decision",
            "error": str(
                pending.get("question") or pending.get("prompt")
                or "this run needs a recorded decision before it can continue"
            ),
        }
    return {"state": "done"}


def _run_publish(db, row: models.ChapterBatchRow, task) -> dict[str, Any]:
    """Publish each named lane: the Concept release first, then the Master.

    A lane is done only when the CMS workbook says so. ``master_review``
    reports a queued append rather than claiming a publication, and this
    reports the same thing: ``blocked`` with the reason, converging when the
    person publishes again.
    """
    from . import build_concepts_release as release_svc
    from . import build_concepts_release_publication as release_publication
    from . import master_review, openai_usage, progress

    job_id = int(row.job_id)
    owner_sub = _job_owner(db, job_id)
    lanes = [str(lane) for lane in (task.lanes or [])]
    if not lanes:
        return {
            "state": "failed", "failure_code": "no_lanes",
            "error": "this publish task names no lane",
        }

    queued: list[str] = []
    with openai_usage.track(), progress.capture_to_journal(
        job_id, title="Step 03 — publishing to the database and the CMS workbook",
        continue_existing=True,
    ) as capture:
        receipts: list[dict[str, Any]] = []
        for lane in lanes:
            job = db.get(models.UploadJob, job_id)
            if job is None:
                return {
                    "state": "failed", "failure_code": "job_missing",
                    "error": "the upload disappeared while publishing",
                }
            payload = release_svc.release_payload(job, lane=lane) or {}
            if not (payload.get("summary") or {}).get("database_uploaded"):
                # The Concept file must reach the database before the Master's
                # groups and questions can attach to its concepts.
                release_publication.upload_release_to_database(
                    db, job_id, lane=lane, owner_sub=owner_sub,
                )
                db.refresh(job)
            receipt = master_review.publish_reviewed_master(
                db, job, lane=lane, owner_sub=owner_sub,
            )
            receipts.append({"lane": lane, **{
                key: receipt.get(key)
                for key in ("publication_status", "version", "release_uid")
            }})
            if str(receipt.get("publication_status") or "") != "published":
                queued.append(lane)
        capture.set_result({"job_id": job_id, "published": receipts})

    if queued:
        return {
            "state": "blocked", "blocked_kind": "cms_workbook_queued",
            "error": (
                "the database write succeeded but the CMS workbook append is "
                f"queued for {', '.join(queued)}; publish again once the "
                "workbook is writable"
            ),
        }
    return {"state": "done"}


# ---------------------------------------------------------------------------
# Module-level handle, wired from the application lifespan
# ---------------------------------------------------------------------------

_worker: ChapterQueueWorker | None = None


def initialize_chapter_queue(session_factory) -> ChapterQueueWorker | None:
    global _worker
    if not enabled():
        log.info("chapter queue worker disabled by AEGIS_QUEUE_WORKER")
        return None
    if _worker is not None:
        return _worker
    _worker = ChapterQueueWorker(session_factory)
    _worker.start()
    return _worker


def shutdown_chapter_queue() -> None:
    global _worker
    if _worker is not None:
        _worker.stop()
        _worker = None


def current_worker() -> ChapterQueueWorker | None:
    return _worker


def nudge() -> None:
    if _worker is not None:
        _worker.nudge()


def worker_alive() -> bool:
    return bool(_worker is not None and _worker.alive())


def capacity() -> int:
    return max_concurrent_runs()

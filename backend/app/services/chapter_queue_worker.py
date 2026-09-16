"""The background worker that makes a pushed chapter run by itself.

One supervisor thread claims queued tasks within an admission budget and runs
each in its own thread. There is nothing clever here on purpose: the hard parts
are the lease (``chapter_queue``) and the honesty of the outcomes, not the
threading.

Explicit synchronous work shares the provider semaphore and reserves capacity
for interactive use. Selected Batch tasks use the separate chapter and Master
limits: provider work waits durably outside that semaphore. A deployment stops
new admission, retains active leases while work yields, and resumes saved steps.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable, Mapping
from typing import Any

from .. import config, models
from . import chapter_batches, chapter_queue, process_memory, run_control

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


def live_broker():
    """The process's wave broker, bound to the OpenAI credentials in use.

    One per process so concurrent chapters share durable waves and request
    deduplication. The batch discount itself is per request, not per wave width.
    """
    from . import batch_broker, model_provider

    def _client():
        from openai import OpenAI

        return OpenAI(timeout=config.OPENAI_REQUEST_TIMEOUT_SECONDS, max_retries=2,
                      **model_provider.client_kwargs())

    def _build():
        return batch_broker.BatchBroker(batch_broker.OpenAIBatchApi(_client))

    return batch_broker.process_broker(_build)


def cohort_concurrency() -> int:
    """How many COHORT chapters may run at once (register Q73).

    A cohort's provider calls go to the batch endpoint, where the provider —
    not this machine — holds the queue, so the synchronous fan-out budget is
    not what bounds it. What bounds it is this machine: each concurrent run
    holds its own source text, inventories and workbook buffers. Six is a
    chosen default, not a measured one — no cohort of six has been run on the
    deployed machine, and this repository has already lost a run to guessing
    at that number (register Q57). The heartbeat's ``chapter queue memory:``
    line is what turns it into a measurement. Raise it against that log.

    Cohorts never switch to synchronous pricing. The synchronous gate remains
    for explicitly ordinary runs, independently of this chapter limit.
    """
    return max(1, _int_env("AEGIS_QUEUE_COHORT_CONCURRENCY", 6))


def cohort_masters() -> int:
    """How many COHORT Step 02s may build at once (register Q73).

    Deliberately far below ``cohort_concurrency``: a Master build holds two
    lanes and their workbook buffers resident, and this machine has already
    died of that pressure once (Q57). Narrowing it slows a cohort's Step 02
    down; it does not make it dearer, because the batch rate is charged per
    request and not per wave. Raise it only with memory to match.
    """
    return max(1, _int_env("AEGIS_QUEUE_COHORT_MASTERS", 2))


def provider_reserve() -> int:
    """Slots never given to the queue, so a person can still run something."""
    return _int_env("AEGIS_QUEUE_PROVIDER_RESERVE", 16)


def _volume_can_hold_a_master_batch() -> bool:
    """Whether the Pre+Post Master batch reservation would be granted now."""
    from . import storage_capacity

    try:
        snapshot = storage_capacity.capacity_snapshot()
    except OSError:
        return True   # an unreadable volume is the reservation's to refuse
    lanes = 2
    required_bytes = (
        lanes * storage_capacity.master_reservation_bytes()
        + storage_capacity.ledger_headroom_bytes()
    )
    required_inodes = (
        lanes * storage_capacity.master_reservation_inodes()
        + storage_capacity.ledger_headroom_inodes()
    )
    free_bytes = snapshot.available_bytes - snapshot.reserved_bytes
    if free_bytes < required_bytes:
        return False
    if snapshot.available_inodes is not None:
        free_inodes = snapshot.available_inodes - snapshot.reserved_inodes
        if free_inodes < required_inodes:
            return False
    return True


def admission_shortfall() -> str:
    """Why this deployment can never admit the queue's most expensive step.

    Empty when the gate can pay for every step kind. The budget is
    ``(gate - reserve) // workers``, floored at 1, and a Step 02 costs 2 —
    so with the code defaults (gate 8, reserve 16, workers 6) the floor
    hides a reserve that exceeds the whole gate, a step01 is admitted, and a
    step02 sits "Queued for Step 02" forever with no error, no log line and
    no badge (verified audit, 13 September 2026: every configuration but
    fly.toml's exact 48/16 was dead, and that one at zero margin). Refusing
    to start, with the arithmetic in the message, is the honest answer.
    """
    workers = max(1, config.phase3_decision_workers())
    reserve = provider_reserve()
    gate = int(config.OPENAI_MAX_CONCURRENCY)
    dearest = max(_STEP_COST.values())
    needed = reserve + workers * dearest
    if gate >= needed:
        return ""
    return (
        f"AEGIS_OPENAI_MAX_CONCURRENCY={gate} cannot admit a step costing "
        f"{dearest} fan-out(s): with AEGIS_QUEUE_PROVIDER_RESERVE={reserve} "
        f"and {workers} decision worker(s) the queue needs a gate of at "
        f"least {needed}. Raise the gate, lower the reserve, or set "
        "AEGIS_QUEUE_WORKER=0 to run without the batch console."
    )


def collision_backoff_seconds() -> float:
    """How long a task refunded for a per-job lock collision waits."""
    try:
        return max(1.0, float(os.environ.get(
            "AEGIS_QUEUE_COLLISION_BACKOFF_SECONDS", "30")))
    except (TypeError, ValueError):
        return 30.0


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
        self._draining = False
        self._supervisor: threading.Thread | None = None
        self._heartbeat: threading.Thread | None = None
        self._lock = threading.Lock()
        #: task id -> kind, for admission arithmetic AND for the sweep, which
        #: must never reclaim a task this process is actually running. Its
        #: shape is load-bearing: ``status()`` publishes it and both the sweep
        #: and ``admits`` read its VALUES as kinds, so the cohort label rides
        #: alongside in its own map rather than widening this one.
        self._in_flight: dict[int, str] = {}
        #: task id -> cohort label (``""`` outside a cohort). Written and
        #: popped under the same lock as ``_in_flight``; read-only diagnostics.
        self._in_flight_cohort: dict[int, str] = {}
        #: task id -> upload job id (``0`` until ``_run_one`` resolves it).
        #: ``uploads`` marks a live run by holding that JOB's lock, so naming
        #: the OTHER lanes in this process needs job ids, not task ids. Same
        #: lock, same lifetime, same reason to ride alongside ``_in_flight``
        #: rather than inside it.
        self._in_flight_job: dict[int, int] = {}
        #: The largest ``VmHWM`` any beat has reported, so a line can say how
        #: much of the peak is NEW. A peak that stops growing is the signal
        #: that a cohort width has settled; a peak that climbs every beat is
        #: the signal that it has not.
        self._peak_hwm_bytes = 0
        self._started_at = 0.0
        #: task ids whose wait has been logged once; cleared on claim.
        self._denied_logged: set[int] = set()
        #: task id -> epoch seconds before which a refunded task is not
        #: re-claimed, so a per-job lock collision cannot spin the loop.
        self._backoff_until: dict[int, float] = {}

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
        deadline = time.monotonic() + max(0.0, timeout)
        with self._wake:
            self._stopping = True
            self._draining = True
            self._wake.notify_all()
        if wait and self._supervisor is not None:
            self._supervisor.join(timeout=max(0.0, deadline - time.monotonic()))
        # Paid calls reach the cooperative provider boundary and requeue their
        # task. During this bounded drain, heartbeat continues protecting every
        # still-running lease. Anything not yet yielded keeps its TTL for boot.
        while wait and self._in_flight_ids() and time.monotonic() < deadline:
            with self._wake:
                self._wake.wait(min(0.1, max(0.0, deadline - time.monotonic())))
        self._draining = False
        if self._supervisor is not None and not self._supervisor.is_alive():
            self._supervisor = None

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
            **capacity_details(),
            "draining": self._stopping,
            "provider_reserve": provider_reserve(),
        }

    # -- admission ---------------------------------------------------------

    def _provider_budget(self) -> int:
        """Fan-outs the queue may have in flight without crowding the gate."""
        workers = max(1, config.phase3_decision_workers())
        usable = max(0, config.OPENAI_MAX_CONCURRENCY - provider_reserve())
        return max(1, usable // workers)

    def admits(self, kind: str, *, reserved: int = 0, cohort: bool = False) -> bool:
        """Whether one more ``kind`` fits, with ``reserved`` fan-outs held back
        for an older task this pass could not admit.

        ``cohort`` says the task belongs to a batch cohort, which is bounded
        by this machine's capacity rather than by the synchronous provider
        gate (register Q73).
        """
        if self._stopping or run_control.pausing():
            return False
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
        if cohort:
            # A wider cohort improves throughput; every selected request keeps
            # the batch price regardless of wave width. The machine's memory,
            # rather than its synchronous provider fan-out, bounds concurrency.
            if len(generation) >= cohort_concurrency():
                return False
            if kind == "step02":
                # A Master build is the memory-heavy step: two lanes, the
                # reviewed workbook and every buffer they need, all resident.
                # This machine has already died of exactly that pressure
                # (register Q57), so Master builds stay far narrower than the
                # cohort even inside one — which costs latency, never price:
                # the batch rate is per request, not per wave.
                masters = [value for value in generation if value == "step02"]
                if len(masters) >= cohort_masters():
                    return False
                if not _volume_can_hold_a_master_batch():
                    return False
            return True
        if admission_shortfall():
            # This concerns synchronous fan-out only. It must not disable a
            # strict Batch queue, whose work waits at the provider instead.
            return False
        if len(generation) >= max_concurrent_runs():
            return False
        if kind == "step02":
            masters = [value for value in generation if value == "step02"]
            if len(masters) >= max_concurrent_masters():
                return False
            if not _volume_can_hold_a_master_batch():
                # Contract section 7: a full volume refuses admission instead
                # of burning an attempt. The batch reservation inside
                # _build_master_siblings still decides for real; this only
                # keeps a task queued while the answer is plainly no.
                return False
        spent = sum(_STEP_COST.get(value, 1) for value in generation)
        return spent + reserved + _STEP_COST.get(kind, 1) <= self._provider_budget()

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
        if self._stopping or run_control.pausing():
            return 0
        db = self._session_factory()
        started = 0
        try:
            running = set(self._in_flight_ids())
            chapter_queue.reclaim_orphans(db, in_flight=running)
            for kinds in (_PUBLISH_KINDS, _GENERATION_KINDS):
                # The oldest task this pass could not admit. Its cost is held
                # back from everything enqueued after it, so a steady supply
                # of cheap step01s cannot cut in front of a step02 forever —
                # it gets its turn as soon as the in-flight work drains.
                # Before this the scan simply STOPPED at the first
                # inadmissible task, so a queue of [step02, step01, step01]
                # started nothing while two admissible steps waited behind
                # it (verified against the real dispatcher, 13 September
                # 2026). Ordering mechanics only; no content is judged.
                held: models.ChapterBatchTask | None = None
                for task in chapter_queue.claimable(db, kinds=kinds):
                    if self._stopping or run_control.pausing():
                        return started
                    if int(task.id) in running:
                        # ``claimable`` includes a lease that has expired, which
                        # is right for a lease nobody is renewing — but this
                        # process may still be running this very task behind a
                        # late heartbeat. Claiming it again would start a second
                        # thread on the same chapter and charge it twice.
                        continue
                    until = self._backoff_until.get(int(task.id), 0.0)
                    if until > time.time():
                        continue
                    self._backoff_until.pop(int(task.id), None)
                    kind = str(task.kind)
                    reserved = _STEP_COST.get(str(held.kind), 1) if held is not None else 0
                    if not self.admits(kind, reserved=reserved,
                                       cohort=bool(str(task.cohort_id or ""))):
                        if held is None and kind in _GENERATION_KINDS:
                            held = task
                        self._note_denied(task, reserved=reserved)
                        continue
                    claimed = chapter_queue.claim(db, int(task.id))
                    if claimed is None:
                        continue
                    self._denied_logged.discard(int(claimed.id))
                    self._launch(int(claimed.id), str(claimed.kind),
                                 cohort=str(claimed.cohort_id or ""))
                    started += 1
        finally:
            db.close()
        return started

    def _note_denied(self, task, *, reserved: int) -> None:
        """One log line per task per wait, naming the arithmetic."""
        task_id = int(task.id)
        if task_id in self._denied_logged:
            return
        self._denied_logged.add(task_id)
        with self._lock:
            in_flight = dict(self._in_flight)
        spent = sum(
            _STEP_COST.get(value, 1) for value in in_flight.values()
            if value in _GENERATION_KINDS
        )
        log.info(
            "chapter queue: task %s (%s, cost %s) waits — %s fan-out(s) in "
            "flight, %s held for an older task, budget %s",
            task_id, task.kind, _STEP_COST.get(str(task.kind), 1),
            spent, reserved, self._provider_budget(),
        )

    def _in_flight_ids(self) -> list[int]:
        with self._lock:
            return list(self._in_flight)

    def _launch(self, task_id: int, kind: str, cohort: str = "") -> None:
        with self._lock:
            self._in_flight[task_id] = kind
            self._in_flight_cohort[task_id] = cohort
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
            self._note_job(task_id, task, db)
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
        except run_control.RunDeferred as exc:
            # Scheduling suspension is not failed content, and must not reach
            # a failure notification or exhaust the real failure-attempt cap.
            db.rollback()
            chapter_queue.requeue(
                db, task_id, error=str(exc), refund_attempt=True,
                delay_seconds=exc.delay, reason=exc.reason,
            )
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
                self._in_flight_cohort.pop(int(task_id), None)
                self._in_flight_job.pop(int(task_id), None)
            self.nudge()

    def _note_job(self, task_id: int, task, db) -> None:
        """Record which upload job this task is about, before it can lock it.

        Resolved here and not in ``_launch`` because ``_launch`` holds only a
        task id and a kind, and because this runs before ``self._runner`` — so
        the job id is always recorded BEFORE the lock that it explains can be
        taken, which is what keeps the attribution below from ever counting
        this queue's own run as somebody else's.

        Diagnostics only: a task whose row has gone records ``0`` and runs
        exactly as it would have.
        """
        try:
            row = db.get(models.ChapterBatchRow, int(task.batch_row_id or 0))
            job_id = int(getattr(task, "job_id", 0) or getattr(row, "job_id", 0) or 0)
        except Exception:  # noqa: BLE001 — a diagnostic never fails a run
            job_id = 0
        with self._lock:
            self._in_flight_job[int(task_id)] = job_id

    def _settle(self, db, task_id: int, outcome: dict[str, Any]) -> None:
        state = str(outcome.get("state") or "failed")
        if state == "retry":
            task = db.get(models.ChapterBatchTask, int(task_id))
            attempts = int(task.attempt or 0) if task else 0
            budget = int(task.max_attempts or 0) if task else 0
            refund = bool(outcome.get("refund_attempt"))
            if refund:
                # A refunded attempt was never a try — another route held
                # the per-job lock — so it cannot be the one that exhausts
                # the budget. Before this, a collision on the LAST attempt
                # was recorded failed/attempts_exhausted for a step that
                # never ran, with an error text saying the opposite (verified
                # audit, 13 September 2026). And re-queueing it at once made
                # the dispatcher claim it again on the next poll, collide
                # again, and spin at 60-100 cycles/s for the whole of the
                # conflicting run: hold it back for one backoff interval
                # first. Process-local is enough — a restart retries once.
                self._backoff_until[int(task_id)] = (
                    time.time() + collision_backoff_seconds()
                )
            if attempts < budget or refund:
                chapter_queue.requeue(
                    db, task_id, error=str(outcome.get("error") or ""),
                    refund_attempt=refund,
                    delay_seconds=(float(outcome.get("delay_seconds") or collision_backoff_seconds())
                                   if refund else poll_seconds()),
                    reason=str(outcome.get("failure_code") or ""),
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

    def _other_generation_lanes(self, our_job_ids: set[int]) -> int | None:
        """Generation lanes live in this process that are NOT this queue's.

        The queue is not the only thing spending memory here: a person can
        run Step 01 or Step 02 from the console while a cohort is running,
        and the RSS on the line is the sum of both. ``uploads`` marks a live
        run by holding that JOB's process-local lock — the marker
        ``is_job_running`` reads — so the held locks that belong to no job
        this queue is running are what let a reader attribute the number to a
        cohort width rather than to a coincidence.

        It is a SET DIFFERENCE and not a subtraction of counts. Subtracting
        counts assumed every in-flight task holds a lock, and a publish task
        holds none — ``_run_publish`` calls ``openai_usage.track()`` directly,
        never ``run_with_openai_usage`` — while ``admits`` lets a publish run
        beside generation as an ordinary state. One publish in flight
        therefore cancelled one genuine interactive lane and printed a
        confident ``other=0``: precisely the reading this field exists to
        prevent, in precisely the case it was written for.

        It is still an attribution and not an identity, in one direction: an
        interactive run on the SAME job as an in-flight publish is indexed by
        that one job id and is not separable from it here. Everything the
        field was written for — an interactive lane beside a cohort — is on
        different jobs by construction.

        ``None`` when the marker cannot be read at all. The caller prints
        ``other=?`` for that rather than dropping the field: an absent field
        reads as zero, and zero is the answer that would send someone looking
        at the wrong knob.
        """
        try:
            from . import uploads

            # Read-only, under the module's own guard. There is no public
            # accessor for the whole set — ``is_job_running`` answers for one
            # job id, and a caller has to know which id to ask about.
            with uploads._usage_job_locks_guard:
                held = {
                    int(job_id)
                    for job_id, lock in uploads._usage_job_locks.items()
                    if lock.locked()
                }
        except Exception:  # noqa: BLE001 — a diagnostic never fails a beat
            return None
        return len(held - {int(job_id) for job_id in our_job_ids if job_id})

    def _log_memory(self) -> None:
        """One line per beat: what this process holds, and what is running.

        Sampled BEFORE the lock is taken, so reading ``/proc`` can never make
        a dispatch pass or a lease pop wait on instrumentation.

        The counts are on the same line as the bytes on purpose. "RSS is
        2.9 GiB" is not actionable; "RSS is 2.9 GiB with six step01s in cohort
        slot-20260914T1230 in flight" is the sentence that decides whether
        ``AEGIS_QUEUE_COHORT_CONCURRENCY`` can stay at six. Bytes are raw —
        this is a series to grep and plot, and a rounded MiB hides exactly the
        growth worth seeing.
        """
        reading = process_memory.sample()
        with self._lock:
            in_flight = dict(self._in_flight)
            cohort_of = dict(self._in_flight_cohort)
            job_of = dict(self._in_flight_job)
        if reading is None:
            rss = hwm = new_peak = "-"
            ceiling = "-(unreadable)"
        else:
            grew = max(0, reading.hwm_bytes - self._peak_hwm_bytes)
            self._peak_hwm_bytes = max(self._peak_hwm_bytes, reading.hwm_bytes)
            rss = str(reading.rss_bytes)
            hwm = str(reading.hwm_bytes)
            new_peak = str(grew)
            bound = reading.limit.limit_bytes
            ceiling = f"{'-' if bound is None else bound}({reading.limit.source})"
        kinds = list(in_flight.values())
        # ``cohort_of`` is read with .get: a test — and any future caller that
        # assigns ``_in_flight`` directly, as several already do — leaves it
        # empty, and a subscript would turn a diagnostic into a KeyError
        # inside the heartbeat thread.
        labels = sorted({
            cohort_of.get(task_id, "") for task_id in in_flight
        } - {""})
        other = self._other_generation_lanes(
            {job_of.get(task_id, 0) for task_id in in_flight})
        process_memory.logger().info(
            "chapter queue memory: rss=%s hwm=%s new_peak=%s limit=%s "
            "in_flight=%s step01=%s step02=%s publish=%s cohort=%s "
            "cohorts=%s other=%s",
            rss, hwm, new_peak, ceiling,
            len(in_flight),
            kinds.count("step01"), kinds.count("step02"),
            kinds.count("publish"),
            sum(1 for task_id in in_flight if cohort_of.get(task_id, "")),
            ",".join(labels) or "-",
            "?" if other is None else other,
        )

    def _beat(self) -> None:
        while not self._stopping or self._draining:
            # ``self._sleep`` and not ``time.sleep``: the seam is already on
            # the constructor, and without using it here the heartbeat is a
            # thread no test can step — which is why the beat was untested
            # while it was the only place that knows what this process holds.
            self._sleep(min(chapter_queue.HEARTBEAT_SECONDS, 15))
            try:
                # ABOVE the idle guard. An idle beat is the reading that says
                # what this process costs with nothing running, which is the
                # baseline every other line is read against.
                self._log_memory()
            except Exception:  # noqa: BLE001 — a lease outranks a diagnostic
                # Deliberately NOT ``log.debug`` on the module logger: that is
                # the channel this whole module exists because production
                # discards (uvicorn leaves root at WARNING with no handler).
                # A ``_log_memory`` that raised every beat would take the
                # memory series away from the one box it is for with no trace
                # at all — the same silence Q57 was debugged without.
                process_memory.logger().warning(
                    "chapter queue: memory beat failed", exc_info=True)
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
        job_id = int(getattr(task, "job_id", 0) or getattr(row, "job_id", 0) or 0)
        if job_id:
            drive_checkpoints.schedule_checkpoint_backup(job_id)
    except Exception:  # noqa: BLE001 — a mirror is an assist, never a gate
        log.debug("chapter queue: could not queue a checkpoint backup",
                  exc_info=True)


def classify_exception(exc: BaseException) -> dict[str, Any]:
    """Turn a raised step into an outcome a person can act on.

    Every branch here answers one question honestly: does this need a person,
    is it worth another charge, or is it over. Nothing is guessed from the
    words in a message — each case is a typed exception the pipeline raises.
    """
    from . import batch_broker, storage_capacity, uploads

    if isinstance(exc, batch_broker.BatchPending):
        return {"state": "retry", "refund_attempt": True,
                "failure_code": "batch_wait", "delay_seconds": 30,
                "error": str(exc)}
    if isinstance(exc, batch_broker.BatchUnavailable):
        return {"state": "failed", "failure_code": "batch_failed", "error": str(exc)}

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
            from . import chapter_batches

            # The pause names itself (contract section 6): the kind it
            # recorded on its pending decision, not one shared label.
            pending = getattr(exc, "pending_decision", None)
            return {
                "state": "blocked",
                "blocked_kind": chapter_batches.blocked_kind_for_pending(
                    pending if isinstance(pending, Mapping) else None
                ),
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
    run_control.check()
    if task.job_id and int(task.job_id) != int(row.job_id):
        return {"state": "failed", "failure_code": "source_replaced",
                "error": "this task belongs to an earlier source upload"}
    kind = str(task.kind or "")
    if kind in ("step01", "step02"):
        job = db.get(models.UploadJob, int(task.job_id or row.job_id))
        if job is not None:
            job.execution_mode = "batch" if task.cohort_id else "synchronous"
            job.requested_chapter_id = int(row.chapter_id)
            db.commit()
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


def _cohort_session(task):
    """Bind selected Batch tasks; explicitly synchronous tasks stay unbound."""
    from . import batch_broker

    if not str(getattr(task, "cohort_id", "") or ""):
        return batch_broker.session(None)
    return batch_broker.session(live_broker())


def _run_step01(db, row: models.ChapterBatchRow, task) -> dict[str, Any]:
    """Convert the staged source if it still needs it, then Step 01.

    Conversion is part of generating the Concept files, not a separate push:
    the owner stages a PDF and pushes once. Spending only ever starts at the
    push, never at the upload.
    """
    from . import build_concepts_release_contract as release_contract
    from . import openai_usage, progress, uploads

    job_id = int(getattr(task, "job_id", 0) or row.job_id)
    # Every service call below resolves the job through ``uploads.get_job``,
    # which filters on ``owner_sub``. Passing the person who pushed the button
    # would raise UploadJobNotFound before a single call. Who acted is recorded
    # on the batch row, never by rewriting the job's owner.
    owner_sub = _job_owner(db, job_id)

    with openai_usage.track(), _cohort_session(task), progress.capture_to_journal(
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
                # Contract section 5 spells this call out with the kwarg, and
                # the interactive route passes it
                # (build_concepts_release_api_contract.py:143). Without it the
                # flag defaults to False, the pause branch is skipped, and
                # step01 falls through to _build_master_siblings: it renders
                # the job's OWN staged Concept workbook, records it as an
                # accepted "unchanged" reviewed input, and spends the whole of
                # Step 02 on machine-authored content the team never saw —
                # against Q49/Q51, which exist to keep Step 02 reading only a
                # file a person reviewed. It also never calls
                # initialize_concept_review, whose sole caller is that skipped
                # branch, so the row ends with no marker and the console
                # derives it as blocked/no_review_marker with every action but
                # "upload source" greyed out. A full paid run, stranded.
                pause_for_concept_review=True,
            ),
            owner_sub=owner_sub,
        )
        capture.set_result(result)
    return _after_generation(db, job_id, result)


def _run_step02(db, row: models.ChapterBatchRow, task) -> dict[str, Any]:
    from . import build_concepts_release_contract as release_contract
    from . import openai_usage, progress, uploads

    job_id = int(getattr(task, "job_id", 0) or row.job_id)
    owner_sub = _job_owner(db, job_id)
    # The Master build takes its own atomic storage reservation inside
    # ``_build_master_siblings``; a refusal arrives here as StorageCapacityError
    # and becomes a visible blocked row rather than a retry into a full volume.
    with openai_usage.track(), _cohort_session(task), progress.capture_to_journal(
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
    from . import chapter_batches

    pending = job.pending_decision
    if pending:
        return {
            "state": "blocked",
            "blocked_kind": chapter_batches.blocked_kind_for_pending(pending),
            "error": chapter_batches.pending_reason(pending),
        }
    # Step 01's failure wrapper (``_stage_generation_release``) catches the
    # exception, stages whatever the run had already paid for, and RETURNS a
    # result carrying ``run_incomplete`` — so a provider failure mid-way never
    # reaches ``classify_exception``. Reading only the exception path settled
    # that return as a clean ``done`` for a row with no Concept files and no
    # review marker (verified audit, 13 September 2026). The marker is the
    # wrapper's own honest verdict: a resumable one is the contract's "any
    # other exception" row — queued while attempts remain, since a re-run
    # resumes from the saved checkpoint — and a non-resumable one is over.
    incomplete = (
        result.get("run_incomplete") if isinstance(result, Mapping) else None
    )
    if isinstance(incomplete, Mapping):
        message = str(
            incomplete.get("message") or incomplete.get("error")
            or "generation did not complete"
        )
        if incomplete.get("resume_allowed") is False:
            return {
                "state": "failed", "failure_code": "non_resumable",
                "error": str(incomplete.get("recovery") or message),
            }
        return {
            "state": "retry", "failure_code": "run_incomplete",
            "error": message,
        }
    # Step 02 returns normally even when a Master lane was refused — the
    # Concept files are finished and must stay available (Q13), so the failure
    # rides the review marker instead of an exception. Reading only the return
    # therefore recorded a lane that produced no Master as a green ``done``
    # row on the console. Ask the marker.
    from . import build_concepts_release as concept_release

    review = concept_release.concept_review_state(job)
    if review.get("status") == concept_release.CONCEPT_REVIEW_MASTER_FAILED:
        outputs = review.get("master_outputs")
        reasons = [
            f"{lane}: {str((outputs or {}).get(lane, {}).get('reason') or '').strip()}"
            for lane in ("pre", "post")
            if isinstance(outputs, Mapping)
            and not (outputs.get(lane) or {}).get("ready")
        ]
        return {
            "state": "failed", "failure_code": "master_lane_unavailable",
            "error": "; ".join(r for r in reasons if r.strip(": "))
            or "a Master lane did not build; retry Step 02",
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

    job_id = int(getattr(task, "job_id", 0) or row.job_id)
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


_disabled_reason = ""


def disabled_reason() -> str:
    """Why the worker is not running, for the console; empty when it is."""
    return _disabled_reason


def _recover_batch_waves() -> int:
    """Bank what a wave already bought before this process died (Q73).

    A submitted batch is money already spent. Re-attaching to it at boot and
    storing its answers is the difference between a crash costing a restart
    and a crash costing the cohort. Never fatal: a machine with no batch
    credentials, or no open waves, simply has nothing to recover.
    """
    from . import batch_broker

    try:
        if not batch_broker.BatchStore().open_waves():
            return 0
        recovered = live_broker().recover()
    except Exception:  # noqa: BLE001 — recovery must never stop the queue
        log.warning("batch wave recovery failed", exc_info=True)
        return 0
    if recovered:
        log.info("batch wave recovery banked %s response(s)", recovered)
    return recovered


def _announce_memory_ceiling() -> None:
    """Install the memory logger and record this machine's ceiling, once.

    FIRST, before ``enabled()`` and before ``admission_shortfall()``. A
    deployment that refuses to start the worker is exactly the deployment
    somebody is about to reconfigure, and the ceiling is the number that
    reconfiguration has to respect — so it has to be in the log of the boot
    that refused, not only in the log of a boot that worked.
    """
    process_memory.install_logging()
    bound = process_memory.limit()
    process_memory.logger().info(
        "chapter queue: memory limit %s (source=%s, bytes=%s)",
        process_memory.human_bytes(bound.limit_bytes), bound.source,
        "-" if bound.limit_bytes is None else bound.limit_bytes,
    )


def initialize_chapter_queue(session_factory) -> ChapterQueueWorker | None:
    global _worker, _disabled_reason
    try:
        _announce_memory_ceiling()
    except Exception:  # noqa: BLE001 — a reading must never break a boot
        # ``main.py``'s lifespan calls this unguarded, so an exception here is
        # the whole application failing to start for the sake of a diagnostic.
        # Every internal is already defensive; this is the one call site where
        # the module's stated stance was not actually enforced.
        log.warning(
            "chapter queue: memory ceiling not recorded", exc_info=True)
    if not enabled():
        _disabled_reason = "disabled by AEGIS_QUEUE_WORKER"
        log.info("chapter queue worker disabled by AEGIS_QUEUE_WORKER")
        return None
    shortfall = admission_shortfall()
    if shortfall:
        # A missing synchronous budget cannot disable strict Batch work.
        # The corresponding reason remains visible in capacity_details().
        log.warning("chapter queue: synchronous admission unavailable; Batch remains enabled: %s", shortfall)
    _disabled_reason = ""
    if _worker is not None:
        return _worker
    _recover_batch_waves()
    _worker = ChapterQueueWorker(session_factory)
    _worker.start()
    return _worker


def shutdown_chapter_queue() -> None:
    global _worker
    if _worker is not None:
        _worker.stop()
        _worker = None
    from . import batch_broker

    batch_broker.reset_process_broker()


def current_worker() -> ChapterQueueWorker | None:
    return _worker


def nudge() -> None:
    if _worker is not None:
        _worker.nudge()


def worker_alive() -> bool:
    return bool(_worker is not None and _worker.alive())


def capacity() -> int:
    return cohort_concurrency()


def capacity_details() -> dict[str, Any]:
    from . import batch_broker

    return {
        "capacity": cohort_concurrency(),
        "batch_capacity": cohort_concurrency(),
        "batch_master_capacity": cohort_masters(),
        "synchronous_capacity": max_concurrent_runs(),
        "synchronous_master_capacity": max_concurrent_masters(),
        "synchronous_admission_reason": admission_shortfall(),
        "broker": batch_broker.process_status(),
    }

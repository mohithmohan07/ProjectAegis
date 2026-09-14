"""The heartbeat's memory line: the one thing this process says about itself.

Q73 widened a cohort to six concurrent chapter runs on the machine that
already died of memory pressure once (register Q57), on the strength of a
default nobody has measured. The measurement is this line, and it has to
survive three things that have each silently swallowed a log line in this
repository before: uvicorn's shipped logging configuration, an idle worker,
and a heartbeat thread with no seam a test can step.

These tests pin the line itself. They deliberately do NOT pin a threshold on
any of its numbers: the line exists so a person reads it and decides, and a
number in here that decided anything would be the deterministic judgment
Rule 1 forbids.
"""
from __future__ import annotations

import io
import logging
import logging.config
import re
import threading

import pytest

from app import models
from app.services import chapter_queue, chapter_queue_worker, process_memory


class _FakeSession:
    """The heartbeat opens a session only when something is in flight."""

    def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def clean_memory_module():
    process_memory._reset_for_tests()
    yield
    process_memory._reset_for_tests()


@pytest.fixture()
def memory_lines():
    """Install the real handler and read exactly what it would print."""
    process_memory.install_logging()
    handler = next(h for h in process_memory.logger().handlers
                   if getattr(h, "_aegis_tag", "") == process_memory._HANDLER_TAG)
    handler.stream = io.StringIO()
    yield handler.stream


def _sample(rss: int, hwm: int, *, limit_bytes: int | None = 4294967296,
            source: str = "cgroup.v2") -> process_memory.MemorySample:
    return process_memory.MemorySample(
        rss_bytes=rss, hwm_bytes=hwm,
        limit=process_memory.MemoryLimit(limit_bytes=limit_bytes, source=source),
    )


def _worker(monkeypatch, *, beats: int, samples=None, session_factory=None):
    """A worker whose heartbeat runs ``beats`` times and then stops.

    The injected sleep sets ``_stopping`` itself because ``stop()`` notifies
    the supervisor's condition and joins the SUPERVISOR — it never joins or
    signals the heartbeat thread at all, so nothing else would end this loop.
    """
    worker = chapter_queue_worker.ChapterQueueWorker(
        session_factory or (lambda: _FakeSession()),
    )
    slept: list[float] = []

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        if len(slept) >= beats:
            worker._stopping = True

    worker._sleep = sleep
    if samples is not None:
        pending = list(samples)

        def take() -> process_memory.MemorySample | None:
            return pending.pop(0) if pending else None

        monkeypatch.setattr(process_memory, "sample", take)
    return worker, slept


def _run_beat(worker, *, timeout: float = 10.0) -> None:
    """Run one heartbeat loop to completion, and FAIL rather than hang.

    The loop's only exit is the injected sleep setting ``_stopping``, so a
    change that stops routing the wait through ``self._sleep`` — a revert to
    ``time.sleep`` — leaves every test in this module spinning at fifteen
    seconds a turn, forever. No pytest-timeout is configured in this
    repository, so that is a CI hang rather than a red test: the one failure
    mode that costs a whole run and names nothing. Running the beat on a
    daemon thread and joining with a deadline turns it back into a failure a
    person can read.
    """
    thread = threading.Thread(
        target=worker._beat, name="beat-under-test", daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        worker._stopping = True
        raise AssertionError(
            f"the heartbeat did not finish within {timeout}s: it is no longer "
            "waiting through the injected self._sleep seam"
        )


def _fields(line: str) -> dict[str, str]:
    body = line.split("chapter queue memory:", 1)[1]
    return dict(part.split("=", 1) for part in body.split() if "=" in part)


def _memory_lines(stream) -> list[str]:
    return [line for line in stream.getvalue().splitlines()
            if "chapter queue memory:" in line]


# --------------------------------------------------------------------------- #
# The line survives production's own logging configuration
# --------------------------------------------------------------------------- #

@pytest.fixture()
def uvicorn_logging():
    """Production's real logging configuration, applied and then undone."""
    import uvicorn.config

    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    try:
        logging.config.dictConfig(uvicorn.config.LOGGING_CONFIG)
        yield
    finally:
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)


def test_the_beat_is_visible_under_uvicorns_own_logging_config(
    uvicorn_logging, monkeypatch,
):
    """THE load-bearing test.

    ``uvicorn.config.LOGGING_CONFIG`` configures the ``uvicorn*`` loggers and
    leaves root exactly as it found it — no handler, level WARNING. So every
    ``logging.getLogger(__name__).info(...)`` in this codebase is discarded in
    production, which is why the machine that OOMed said nothing. A memory
    line routed through the module logger would be invisible on the one box it
    exists for, and invisible in a way no test would notice, because pytest
    attaches its own root handler.

    So this asserts both halves at once: the line lands, with a timestamp, on
    the handler ``aegis.memory`` owns — and, in the same breath, that the
    module logger is still NOT enabled for INFO. A refactor that "tidies" the
    beat back onto ``log.info`` fails here rather than on the next incident.
    """
    process_memory.install_logging()
    handler = next(h for h in process_memory.logger().handlers
                   if getattr(h, "_aegis_tag", "") == process_memory._HANDLER_TAG)
    handler.stream = io.StringIO()

    worker, _ = _worker(monkeypatch, beats=1, samples=[_sample(100, 100)])
    _run_beat(worker)

    lines = _memory_lines(handler.stream)
    assert len(lines) == 1
    assert re.match(
        r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} INFO aegis\.memory "
        r"chapter queue memory: ",
        lines[0],
    ), lines[0]

    module_log = logging.getLogger("app.services.chapter_queue_worker")
    assert module_log.isEnabledFor(logging.INFO) is False, (
        "under uvicorn's config the module logger is silent at INFO; the "
        "memory beat must not be routed through it"
    )


# --------------------------------------------------------------------------- #
# What the line says
# --------------------------------------------------------------------------- #

def test_an_idle_worker_still_reports_what_it_holds(memory_lines, monkeypatch):
    """The idle reading is the baseline every other line is read against: it
    says what this process costs with nothing running. Logging only when
    something is in flight would leave the most useful number — the floor a
    cohort is added to — permanently unrecorded."""
    worker, slept = _worker(monkeypatch, beats=1, samples=[_sample(99, 99)])
    assert worker._in_flight == {}
    _run_beat(worker)

    lines = _memory_lines(memory_lines)
    assert len(lines) == 1
    fields = _fields(lines[0])
    assert fields["rss"] == "99"
    assert fields["in_flight"] == "0"
    assert slept == [min(chapter_queue.HEARTBEAT_SECONDS, 15)]


def test_new_peak_reports_only_growth_since_the_last_beat(memory_lines, monkeypatch):
    """A high-water mark is monotonic, so printing it alone cannot tell "this
    cohort just cost another 700 MB" from "something did, an hour ago". The
    delta is what says whether a width has settled: a peak that stops growing
    is a cohort that fits, a peak that climbs every beat is one that does
    not."""
    worker, _ = _worker(
        monkeypatch, beats=3,
        samples=[_sample(100, 100), _sample(120, 150), _sample(110, 150)],
    )
    _run_beat(worker)

    peaks = [_fields(line)["new_peak"] for line in _memory_lines(memory_lines)]
    assert peaks == ["100", "50", "0"]


def test_a_reading_this_machine_cannot_take_still_reports_the_running_work(
    memory_lines, monkeypatch,
):
    """A machine with no ``/proc`` loses the bytes, not the line: which steps
    were in flight is still worth having, and a dropped line reads as a dead
    heartbeat thread."""
    worker, _ = _worker(monkeypatch, beats=1, samples=[None])
    worker._in_flight = {7: "step01"}
    monkeypatch.setattr(chapter_queue, "heartbeat", lambda db, task_id: True)
    _run_beat(worker)

    fields = _fields(_memory_lines(memory_lines)[0])
    assert fields["rss"] == "-" and fields["hwm"] == "-"
    assert fields["limit"] == "-(unreadable)"
    assert fields["in_flight"] == "1" and fields["step01"] == "1"


def test_a_raising_sample_never_stops_the_heartbeat(memory_lines, monkeypatch):
    """Instrumentation that can kill the heartbeat thread is worse than none:
    every lease this process holds would then silently expire and every
    chapter it is running would be claimed a second time and charged twice."""
    def explode():
        raise RuntimeError("/proc is gone")

    monkeypatch.setattr(process_memory, "sample", explode)
    beaten: list[int] = []
    monkeypatch.setattr(chapter_queue, "heartbeat",
                        lambda db, task_id: beaten.append(task_id) or True)

    worker, _ = _worker(monkeypatch, beats=3)
    worker._in_flight = {7: "step01"}
    _run_beat(worker)

    assert beaten == [7, 7, 7], "every beat must still renew the lease"


def test_an_in_flight_map_with_no_cohort_labels_is_a_complete_line(
    memory_lines, monkeypatch,
):
    """``_in_flight`` is assigned directly by several existing tests and by the
    sweep's own reasoning, so the cohort map can legitimately be empty while
    tasks are in flight. Subscripting it would raise KeyError inside the
    heartbeat thread — the one thread whose death costs leases."""
    worker, _ = _worker(monkeypatch, beats=1, samples=[_sample(100, 100)])
    worker._in_flight = {1: "step01", 2: "step02", 3: "publish"}
    worker._in_flight_cohort = {}
    monkeypatch.setattr(chapter_queue, "heartbeat", lambda db, task_id: True)
    _run_beat(worker)

    fields = _fields(_memory_lines(memory_lines)[0])
    assert fields["in_flight"] == "3"
    assert fields["step01"] == "1" and fields["step02"] == "1"
    assert fields["publish"] == "1"
    assert fields["cohort"] == "0"
    assert fields["cohorts"] == "-"


def test_the_line_names_the_cohorts_that_are_running(memory_lines, monkeypatch):
    """"RSS is 2.9 GiB" is not actionable. "RSS is 2.9 GiB with six step01s in
    slot-20260914T1230" is the sentence that decides whether
    ``AEGIS_QUEUE_COHORT_CONCURRENCY`` can stay at six."""
    worker, _ = _worker(monkeypatch, beats=1, samples=[_sample(100, 100)])
    worker._in_flight = {1: "step01", 2: "step01", 3: "step01"}
    worker._in_flight_cohort = {1: "slot-20260914T1230", 2: "slot-20260914T1230",
                                3: ""}
    monkeypatch.setattr(chapter_queue, "heartbeat", lambda db, task_id: True)
    _run_beat(worker)

    fields = _fields(_memory_lines(memory_lines)[0])
    assert fields["cohort"] == "2"
    assert fields["cohorts"] == "slot-20260914T1230"


def test_the_line_carries_the_ceiling_and_where_it_came_from(
    memory_lines, monkeypatch,
):
    """A limit read from ``meminfo`` on a container is the HOST's RAM — a
    reassuring number that is not the ceiling at all. Naming the source on
    every line is what lets a reader tell that apart from a real cgroup
    limit."""
    worker, _ = _worker(
        monkeypatch, beats=1,
        samples=[_sample(1, 1, limit_bytes=4294967296, source="cgroup.v2")],
    )
    _run_beat(worker)
    assert _fields(_memory_lines(memory_lines)[0])["limit"] == \
        "4294967296(cgroup.v2)"


def test_other_lanes_are_attributed_by_job_not_by_arithmetic(
    memory_lines, monkeypatch,
):
    """The queue is not the only thing spending memory in this process: a
    person can run Step 02 from the console while a cohort runs, and the RSS
    on the line is the sum. Without this field a spike caused by an
    interactive run would be read as a cohort that does not fit, and the
    cohort would be narrowed for nothing.

    The identity is what makes it an attribution: the queue's own JOB is
    named and removed, and what is left over is somebody else's. Subtracting
    the NUMBER of in-flight tasks instead assumes each of them holds a lock,
    which is only sometimes true — a task between ``_run_one`` recording its
    job and ``run_with_openai_usage`` acquiring holds nothing, and the old
    arithmetic charged it against a stranger's lane anyway."""
    from app.services import uploads

    monkeypatch.setattr(chapter_queue, "heartbeat", lambda db, task_id: True)

    mine, _ = _worker(monkeypatch, beats=1, samples=[_sample(1, 1)])
    mine._in_flight = {1: "step01"}
    mine._in_flight_job = {1: 910001}
    with uploads.exclusive_job_operation(910001), \
            uploads.exclusive_job_operation(910002):
        _run_beat(mine)

    # Two live runs, one of them this queue's own in-flight task.
    assert _fields(_memory_lines(memory_lines)[0])["other"] == "1"

    # Same two runs, but this queue's task is on a job neither of them is:
    # it has not reached its lock yet, so it explains neither and subtracts
    # neither.
    waiting, _ = _worker(monkeypatch, beats=1, samples=[_sample(1, 1)])
    waiting._in_flight = {1: "step01"}
    waiting._in_flight_job = {1: 910055}
    with uploads.exclusive_job_operation(910001), \
            uploads.exclusive_job_operation(910002):
        _run_beat(waiting)

    assert _fields(_memory_lines(memory_lines)[1])["other"] == "2"


def test_a_publish_task_cannot_cancel_out_a_genuine_other_lane(
    memory_lines, monkeypatch,
):
    """``_run_publish`` never takes an uploads job lock — it calls
    ``openai_usage.track()`` directly rather than ``run_with_openai_usage`` —
    and ``admits`` lets a publish run beside generation as an ordinary state.
    Subtracting a COUNT of in-flight tasks from the count of held locks
    therefore cancelled a real interactive lane against a queue task that
    holds nothing, and printed a confident ``other=0``: exactly the reading
    this field exists to prevent, in exactly the case it was written for."""
    from app.services import uploads

    worker, _ = _worker(monkeypatch, beats=1, samples=[_sample(1, 1)])
    worker._in_flight = {5: "publish"}
    worker._in_flight_job = {5: 910003}
    monkeypatch.setattr(chapter_queue, "heartbeat", lambda db, task_id: True)

    with uploads.exclusive_job_operation(910004):
        _run_beat(worker)

    fields = _fields(_memory_lines(memory_lines)[0])
    assert fields["publish"] == "1"
    assert fields["other"] == "1", (
        "the publish holds no job lock, so it can subtract nothing from the "
        "interactive lane that does"
    )


def test_a_running_task_records_the_job_whose_lock_it_will_hold(monkeypatch):
    """The attribution above needs a job id, and ``_launch`` holds only a task
    id and a kind. It is resolved at the top of ``_run_one``, BEFORE the
    runner — so the id is recorded before the lock it explains can be taken —
    and dropped with the other two maps when the task finishes."""
    seen: list[dict] = []

    class _Row:
        job_id = 4242

    class _Task:
        id = 77
        batch_row_id = 9
        kind = "publish"

    class _Session:
        def get(self, model, ident):
            return _Row() if model is models.ChapterBatchRow else _Task()

        def close(self) -> None:
            pass

    worker = chapter_queue_worker.ChapterQueueWorker(lambda: _Session())
    monkeypatch.setattr(chapter_queue, "reconcile_before_dispatch",
                        lambda db, task: None)
    monkeypatch.setattr(chapter_queue, "finish", lambda *a, **k: None)
    monkeypatch.setattr(chapter_queue_worker, "_schedule_checkpoint_backup",
                        lambda db, task: None)
    worker._runner = lambda db, task: (
        seen.append(dict(worker._in_flight_job)) or {"state": "done"}
    )

    worker._run_one(77)

    assert seen == [{77: 4242}], "recorded before the step runs"
    assert worker._in_flight_job == {}, "and dropped when it finishes"


def test_a_failing_memory_line_says_so_where_someone_can_read_it(
    memory_lines, monkeypatch,
):
    """``log.debug`` on the module logger is the channel this whole module
    exists because production discards. Routed there, a ``_log_memory`` that
    raised every beat would take the memory series away from the one box it is
    for with no trace at all — the same silence Q57 was debugged without."""
    def explode(self) -> None:
        raise RuntimeError("no /proc")

    monkeypatch.setattr(
        chapter_queue_worker.ChapterQueueWorker, "_log_memory", explode)
    worker, _ = _worker(monkeypatch, beats=1)
    _run_beat(worker)

    text = memory_lines.getvalue()
    assert "chapter queue: memory beat failed" in text
    assert "WARNING aegis.memory" in text, text


def test_an_unreadable_marker_says_question_mark_rather_than_zero(
    memory_lines, monkeypatch,
):
    """An absent field reads as zero, and zero is the answer that would send
    someone to narrow the cohort instead of looking for the other run."""
    worker, _ = _worker(monkeypatch, beats=1, samples=[_sample(1, 1)])
    monkeypatch.setattr(worker, "_other_generation_lanes", lambda job_ids: None)
    _run_beat(worker)
    assert _fields(_memory_lines(memory_lines)[0])["other"] == "?"


# --------------------------------------------------------------------------- #
# The cohort label rides beside _in_flight, not inside it
# --------------------------------------------------------------------------- #

def test_launch_carries_the_cohort_beside_the_kind_not_inside_it():
    """``_in_flight``'s ``dict[int, str]`` of KINDS is read by the sweep and by
    ``admits``, and published by ``status()``. Widening it to carry the cohort
    would change all three; the label rides in its own map, and the original
    ``(task_id, kind)`` call still means what it meant."""
    # The signature, not a live call: ``_launch`` starts a real thread.
    import inspect

    signature = inspect.signature(chapter_queue_worker.ChapterQueueWorker._launch)
    assert signature.parameters["cohort"].default == ""
    assert list(signature.parameters) == ["self", "task_id", "kind", "cohort"]


def test_a_launched_task_records_its_cohort_and_a_finished_one_clears_both(
    monkeypatch,
):
    worker = chapter_queue_worker.ChapterQueueWorker(lambda: _FakeSession())
    started: list[str] = []

    class _Thread:
        def __init__(self, **kwargs):
            started.append(kwargs.get("name", ""))

        def start(self):
            pass

    monkeypatch.setattr(chapter_queue_worker.threading, "Thread", _Thread)
    worker._launch(11, "step01", cohort="slot-20260914T1230")
    worker._launch(12, "step02")

    assert worker._in_flight == {11: "step01", 12: "step02"}
    assert worker._in_flight_cohort == {11: "slot-20260914T1230", 12: ""}
    assert started

    # What ``_run_one``'s finally does; both maps must drain together or the
    # cohort map grows for the life of the process.
    with worker._lock:
        worker._in_flight.pop(11, None)
        worker._in_flight_cohort.pop(11, None)
    assert worker._in_flight_cohort == {12: ""}


def test_the_dispatcher_passes_the_claimed_tasks_cohort(monkeypatch):
    """The label the line prints has to be the one the task was CLAIMED with,
    not one re-read later: a cohort id is a stamped label with no lifecycle,
    and re-reading it from a detached row is how it becomes empty."""
    worker = chapter_queue_worker.ChapterQueueWorker(lambda: _FakeSession())
    launched: list[tuple] = []
    monkeypatch.setattr(worker, "_launch",
                        lambda task_id, kind, cohort="": launched.append(
                            (task_id, kind, cohort)))

    class _Task:
        id = 42
        kind = "step01"
        cohort_id = "slot-20260914T1230"

    monkeypatch.setattr(chapter_queue, "reclaim_orphans",
                        lambda db, **kw: {"requeued": 0, "failed": 0})
    monkeypatch.setattr(
        chapter_queue, "claimable",
        lambda db, kinds: [_Task()] if "step01" in kinds else [],
    )
    monkeypatch.setattr(chapter_queue, "claim", lambda db, task_id: _Task())
    monkeypatch.setattr(worker, "admits", lambda *a, **k: True)

    assert worker._dispatch_once() == 1
    assert launched == [(42, "step01", "slot-20260914T1230")]


# --------------------------------------------------------------------------- #
# Where the series does NOT go
# --------------------------------------------------------------------------- #

def test_the_queue_table_stores_no_memory_series():
    """A sample every fifteen seconds per task belongs in the log, not in the
    one table the lease depends on. Columns here would add a write per beat to
    the row whose integrity decides whether a chapter runs twice, and would
    drift the row's meaning from "what this step is" to "what this machine was
    doing". ``status()`` carries none of it either, for the same reason: it is
    the console's view of the queue, not a metrics endpoint."""
    names = {column.name.lower()
             for column in models.ChapterBatchTask.__table__.columns}
    assert not [name for name in names
                if "rss" in name or "hwm" in name or "peak" in name], names

    worker = chapter_queue_worker.ChapterQueueWorker(lambda: _FakeSession())
    assert "memory" not in worker.status()


def test_the_boot_line_names_the_ceiling_before_the_worker_can_refuse(
    memory_lines, monkeypatch,
):
    """A deployment that refuses to start the queue is exactly the deployment
    somebody is about to reconfigure, and the ceiling is the number that
    reconfiguration has to respect. Recorded only on a successful boot it
    would be missing from every log that needed it."""
    monkeypatch.setenv("AEGIS_QUEUE_WORKER", "0")
    chapter_queue_worker.shutdown_chapter_queue()
    assert chapter_queue_worker.initialize_chapter_queue(lambda: None) is None

    text = memory_lines.getvalue()
    assert "chapter queue: memory limit" in text
    assert "source=" in text


def test_a_boot_that_cannot_read_its_ceiling_still_boots(monkeypatch):
    """``_announce_memory_ceiling`` is the FIRST statement of the queue's
    initialisation, and ``main.py``'s lifespan calls that unguarded. Every
    internal here is defensive, but this module's stated stance — a reading
    never breaks its caller — was not actually enforced at the one call site
    where breaking the caller means the application does not start."""
    def explode() -> None:
        raise RuntimeError("no /proc")

    monkeypatch.setattr(process_memory, "install_logging", explode)
    monkeypatch.setenv("AEGIS_QUEUE_WORKER", "0")
    chapter_queue_worker.shutdown_chapter_queue()

    assert chapter_queue_worker.initialize_chapter_queue(lambda: None) is None

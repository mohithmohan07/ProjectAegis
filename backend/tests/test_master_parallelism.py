"""Exercise decision concurrency through the automatic Master lane wrapper."""
from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from app import config
from app.services import build_concepts_release as release
from app.services import build_concepts_release_contract as contract
from app.services import generation, openai_usage, progress
from app.services.phase3 import kernel
from tests.test_release_core import OWNER, _both_lanes_job, _chapter_with_concepts


@pytest.mark.parametrize("provider_limit", [4, 48])
def test_automatic_master_decisions_overlap_within_lane_and_provider_bounds(
    db, monkeypatch, provider_limit,
):
    """16 workers per lane can overlap; the real transport gate still wins.

    A second decision map inside each question reproduces Settle-style nested
    batches: those must use the existing worker, not multiply 32 into 512.
    No request leaves the process; only the SDK transport is replaced.
    """
    chapter = _chapter_with_concepts(db)
    job = _both_lanes_job(db, chapter)
    workers = 16
    expected_peak = min(2 * workers, provider_limit)
    transport_barrier = threading.Barrier(expected_peak)
    lock = threading.Lock()
    active = peak = started_calls = 0
    lane_active: dict[str, int] = {}
    lane_peak: dict[str, int] = {}
    lane_threads: dict[str, set[int]] = {}
    sessions = {}
    applied = {}
    events = []

    def create(**_kwargs):
        nonlocal active, peak, started_calls
        lane = progress.current_lane()
        with lock:
            active += 1
            started_calls += 1
            join_first_wave = started_calls <= expected_peak
            peak = max(peak, active)
            lane_active[lane] = lane_active.get(lane, 0) + 1
            lane_peak[lane] = max(lane_peak.get(lane, 0), lane_active[lane])
            lane_threads.setdefault(lane, set()).add(threading.get_ident())
        try:
            if join_first_wave:
                transport_barrier.wait(timeout=10)
            return SimpleNamespace(
                id="mock-response", model="gpt-5.6-luna", service_tier="default",
                choices=[SimpleNamespace(
                    message=SimpleNamespace(content='{"ok": true}'),
                    finish_reason="stop",
                )],
                usage=SimpleNamespace(
                    prompt_tokens=100, completion_tokens=20, total_tokens=120,
                ),
            )
        finally:
            with lock:
                active -= 1
                lane_active[lane] -= 1

    monkeypatch.setattr(
        "openai.OpenAI",
        lambda **_kwargs: SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
        ),
    )
    monkeypatch.setattr(config, "OPENAI_MAX_CONCURRENCY", provider_limit)
    monkeypatch.setattr(
        generation, "_openai_gate", threading.BoundedSemaphore(provider_limit),
    )

    def rebuild(lane_db, _job_id, lane, **_kwargs):
        sessions[lane] = lane_db
        caller_thread = threading.get_ident()
        applied[lane] = []

        def leaf(_subtask):
            return generation._openai_json("Return JSON", "Test", single_attempt=True)

        def question(index):
            results = kernel.parallel_map_in_order(
                range(2), leaf, max_workers=workers,
            )
            assert results == [{"ok": True}, {"ok": True}]
            return index

        def apply(index, item, result):
            assert threading.get_ident() == caller_thread
            assert index == item == result
            applied[lane].append(index)

        results = kernel.parallel_map_in_order(
            range(workers), question, max_workers=workers,
            on_result=apply, announce="Master question decisions",
        )
        assert results == list(range(workers))
        return SimpleNamespace(id=f"REL-{lane}")

    monkeypatch.setattr(contract, "rebuild_lane_master", rebuild)
    sink_token = progress._sink.set(events.append)
    try:
        with openai_usage.track():
            built = contract._build_master_siblings(
                db, job.id, chapter.id, owner_sub=OWNER,
            )
            summary = openai_usage.current_summary()
    finally:
        progress._sink.reset(sink_token)

    assert built == {
        release.LANE_PRE: {"release_id": "REL-pre"},
        release.LANE_POST: {"release_id": "REL-post"},
    }
    assert peak == expected_peak
    assert len(lane_threads) == 2
    assert all(value <= workers for value in lane_peak.values())
    assert all(len(threads) <= workers for threads in lane_threads.values())
    if provider_limit == 48:
        assert set(lane_peak.values()) == {workers}
        assert {len(threads) for threads in lane_threads.values()} == {workers}
    assert sessions[release.LANE_PRE] is not sessions[release.LANE_POST]
    assert all(session is not db for session in sessions.values())
    assert applied == {lane: list(range(workers)) for lane in built}
    assert summary["request_count"] == summary["provider_request_count"] == 64
    assert summary["total_tokens"] == 64 * 120
    assert {row["lane"] for row in summary["stages"]} == set(lane_threads)
    announcements = [
        event["message"] for event in events
        if "Master question decisions:" in str(event.get("message", ""))
    ]
    assert len(announcements) == 2
    assert all("16 parallel worker(s)" in message for message in announcements)


def test_master_lane_failure_stops_new_work_preserves_paid_work_and_sibling(
    db, monkeypatch, tmp_path,
):
    chapter = _chapter_with_concepts(db)
    job = _both_lanes_job(db, chapter)
    author_started = threading.Event()
    pre_joined = threading.Event()
    pre_started = []
    pre_applied = []
    post_completed = []
    store = kernel.DecisionStore(tmp_path / "decisions")

    def rebuild(_lane_db, _job_id, lane, **_kwargs):
        if lane == release.LANE_PRE:
            def question(index):
                pre_started.append(index)
                if index == 1:
                    assert author_started.wait(5)
                    raise RuntimeError("second Pre question failed")

                def author(_payload):
                    author_started.set()
                    # The first already-started call completes after another
                    # question fails; joining must let it save its receipt.
                    assert kernel._pool_cancel.get().wait(5)
                    return {"answer": "paid result"}

                return kernel.decide(
                    kind="master-test", unit_id=str(index),
                    envelope_sha256="source", payload={"question": index},
                    provider=author, checker=lambda _response: [], store=store,
                    critic=lambda _payload: {
                        "verdict": "verified", "confidence": 1,
                    },
                )

            try:
                kernel.parallel_map_in_order(
                    range(20), question, max_workers=2,
                    on_result=lambda *args: pre_applied.append(args),
                )
            finally:
                pre_joined.set()
        else:
            def question(index):
                assert pre_joined.wait(5)
                post_completed.append(index)
                return index

            assert kernel.parallel_map_in_order(
                range(8), question, max_workers=2,
            ) == list(range(8))
        return SimpleNamespace(id=f"REL-{lane}")

    monkeypatch.setattr(contract, "rebuild_lane_master", rebuild)
    built = contract._build_master_siblings(
        db, job.id, chapter.id, owner_sub=OWNER,
    )
    assert built == {
        release.LANE_PRE: None,
        release.LANE_POST: {"release_id": "REL-post"},
    }
    assert set(pre_started) == {0, 1}
    assert pre_applied == []
    assert sorted(post_completed) == list(range(8))
    reloaded = kernel.DecisionStore(tmp_path / "decisions")
    assert len(reloaded.keys()) == 1
    saved = reloaded.get(reloaded.keys()[0])
    assert saved["state"] == "reviewed"
    assert saved["response"] == {"answer": "paid result"}


def test_orchestration_failure_reaches_child_pool_without_new_work():
    leaves_started = threading.Barrier(3)
    started = []
    parent_cancel = []

    def lane(name):
        if name == "fail":
            leaves_started.wait(timeout=5)
            raise RuntimeError("orchestration failed")
        cancellation = kernel._pool_cancel.get()
        parent_cancel.append(cancellation)

        def leaf(index):
            started.append(index)
            leaves_started.wait(timeout=5)
            assert cancellation.wait(5)
            return index

        return kernel.parallel_map_in_order(range(20), leaf, max_workers=2)

    with pytest.raises(RuntimeError, match="orchestration failed"):
        kernel.parallel_map_in_order(
            ["work", "fail"], lane, max_workers=2, orchestration=True,
        )
    assert set(started) == {0, 1}
    assert parent_cancel[0].is_set()
    assert kernel._pool_cancel.get() is None
    assert kernel._pool_cancel_parents.get() == ()
    assert kernel._decision_pool_active.get() is False


def test_orchestration_marker_cannot_bypass_an_existing_decision_worker():
    first_leaves = threading.Barrier(2)

    def decision(index):
        worker_thread = threading.get_ident()

        def leaf(subtask):
            assert threading.get_ident() == worker_thread
            if subtask == 0:
                first_leaves.wait(timeout=5)
            return subtask

        return kernel.parallel_map_in_order(
            range(4), leaf, max_workers=16, orchestration=True,
        )

    assert kernel.parallel_map_in_order(
        range(2), decision, max_workers=2,
    ) == [list(range(4)), list(range(4))]

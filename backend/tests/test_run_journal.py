"""The durable run journal: lossless catch-up for a client that was away.

The run already survives a dropped connection (progress.stream executes
on a detached daemon thread); the journal preserves the EVENTS emitted
while nobody was attached, stamped with a monotonic ``seq`` that rides
the live stream too, so a returning client dedupes the overlap and can
even finish the run from the journal's terminal event.
"""
from __future__ import annotations

import io
import json
import threading

from app.services import run_journal
from tests.conftest import convert_concept_upload, stream_events


# --------------------------------------------------------------------- #
# RunJournal mechanics
# --------------------------------------------------------------------- #

def test_publish_orders_seq_file_and_sink_identically_under_threads(
    tmp_path, monkeypatch,
):
    from app import config

    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path)
    journal = run_journal.RunJournal(41)
    sunk: list[dict] = []
    lock = threading.Lock()

    def sink(event: dict) -> None:
        with lock:
            sunk.append(event)

    def hammer(worker: int) -> None:
        for i in range(50):
            journal.publish(
                {"type": "log", "message": f"w{worker}-{i}"}, sink,
            )

    threads = [
        threading.Thread(target=hammer, args=(w,)) for w in range(8)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    journal.close()

    assert len(sunk) == 400
    # Every event got a unique seq, 1..400.
    assert sorted(e["seq"] for e in sunk) == list(range(1, 401))
    # The file's byte order IS the seq order — the cursor contract.
    on_disk = [
        json.loads(line)
        for line in run_journal.journal_path(41).read_text().splitlines()
    ]
    assert [e["seq"] for e in on_disk] == list(range(1, 401))


def test_read_after_filters_by_cursor_and_survives_a_torn_tail(
    tmp_path, monkeypatch,
):
    from app import config

    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path)
    journal = run_journal.RunJournal(42)
    for i in range(1, 6):
        journal.publish({"type": "log", "message": f"line {i}"}, lambda _e: None)
    journal.close()
    # A live append can leave a torn final line; the reader skips it.
    with open(run_journal.journal_path(42), "a", encoding="utf-8") as handle:
        handle.write('{"type": "log", "mess')

    tail = run_journal.read_after(42, 3)
    assert [e["seq"] for e in tail["events"]] == [4, 5]
    assert tail["next"] == 5
    empty = run_journal.read_after(42, 5)
    assert empty["events"] == []
    assert empty["next"] == 5


def test_heartbeats_never_enter_the_journal(tmp_path, monkeypatch):
    from app import config

    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path)
    journal = run_journal.RunJournal(43)
    passed: list[dict] = []
    journal.publish({"type": "heartbeat"}, passed.append)
    journal.publish({"type": "log", "message": "real"}, passed.append)
    journal.close()
    assert [e["type"] for e in passed] == ["heartbeat", "log"]
    assert "seq" not in passed[0]
    on_disk = run_journal.read_after(43, 0)["events"]
    assert [e["type"] for e in on_disk] == ["log"]


# --------------------------------------------------------------------- #
# End to end: the convert stream journals itself; run-events serves it
# --------------------------------------------------------------------- #

def test_a_real_run_is_journaled_and_served_with_a_cursor(client):
    files = {"file": ("notes.txt", io.BytesIO(
        b"## Sound\nVibrations travel as waves through a medium."
    ), "text/plain")}
    job = client.post("/build-concepts/post-learning/uploads", files=files).json()
    resp = client.post(f"/build-concepts/uploads/{job['id']}/convert")
    live = [e for e in stream_events(resp) if e.get("type") != "heartbeat"]
    # The live stream now carries the journal's seq on every event.
    assert all(isinstance(e.get("seq"), int) for e in live)
    assert [e["seq"] for e in live] == sorted(e["seq"] for e in live)
    assert live[-1]["type"] == "result"

    # Full catch-up equals the live stream, event for event.
    tail = client.get(
        f"/build-concepts/uploads/{job['id']}/run-events?after=0"
    ).json()
    assert tail["running"] is False
    assert [e["seq"] for e in tail["events"]] == [e["seq"] for e in live]
    assert tail["events"][-1]["type"] == "result"
    assert tail["next"] == live[-1]["seq"]

    # A cursor mid-way returns exactly the missed suffix.
    midway = live[len(live) // 2]["seq"]
    suffix = client.get(
        f"/build-concepts/uploads/{job['id']}/run-events?after={midway}"
    ).json()
    assert [e["seq"] for e in suffix["events"]] == [
        e["seq"] for e in live if e["seq"] > midway
    ]

    # A fresh run truncates the journal: seq restarts from 1.
    resp2 = client.post(f"/build-concepts/uploads/{job['id']}/convert")
    live2 = [e for e in stream_events(resp2) if e.get("type") != "heartbeat"]
    assert live2[0]["seq"] == 1


def test_run_events_is_owner_scoped(client):
    resp = client.get("/build-concepts/uploads/999999/run-events?after=0")
    assert resp.status_code == 404

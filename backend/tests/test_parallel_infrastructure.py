"""The parallel-run contract: deterministic fan-out, attributable logs,
exact accounting, atomic decisions, and the PDF-lane Chapter Reading
exemption. These are the properties that make raising the concurrency
defaults safe — each one pinned mechanically, no model calls anywhere."""
from __future__ import annotations

import json
import threading
from types import ModuleType

from app.services import canonical_source_phase2 as phase2
from app.services import chapter_reading
from app.services import chapter_reading_contract
from app.services import openai_usage, progress
from app.services.phase3 import kernel


# --------------------------------------------------------------------- #
# parallel_map_in_order
# --------------------------------------------------------------------- #

def test_results_arrive_in_input_order_whatever_finishes_first():
    import time

    def worker(n: int) -> int:
        # Later items finish first; the output order must not care.
        time.sleep((5 - n) * 0.01)
        return n * 10

    out = kernel.parallel_map_in_order([1, 2, 3, 4, 5], worker, max_workers=5)
    assert out == [10, 20, 30, 40, 50]


def test_worker_log_lines_carry_their_unit_label():
    events: list[dict] = []
    token = progress._sink.set(events.append)
    try:
        def worker(n: int) -> int:
            progress.log(f"unit {n} working")
            return n

        kernel.parallel_map_in_order(
            [1, 2], worker, max_workers=2,
            labels=["Alpha 1/2", "Beta 2/2"], announce="Demo stage",
        )
    finally:
        progress._sink.reset(token)
    messages = [e["message"] for e in events if e.get("type") == "log"]
    assert any("Demo stage: 2 unit(s) across 2 parallel worker(s)" in m
               for m in messages)
    assert any(m.startswith("[Alpha 1/2] ") for m in messages)
    assert any(m.startswith("[Beta 2/2] ") for m in messages)


def test_labels_apply_on_the_sequential_degrade_path_too():
    events: list[dict] = []
    token = progress._sink.set(events.append)
    try:
        kernel.parallel_map_in_order(
            [7], lambda n: progress.log("only unit"), max_workers=6,
            labels=["Solo 1/1"], announce="Tiny stage",
        )
    finally:
        progress._sink.reset(token)
    messages = [e["message"] for e in events if e.get("type") == "log"]
    assert any(m == "[Solo 1/1] only unit" for m in messages)


def test_a_label_count_mismatch_is_refused():
    import pytest

    with pytest.raises(ValueError):
        kernel.parallel_map_in_order(
            [1, 2], lambda n: n, max_workers=2, labels=["only-one"],
        )


# --------------------------------------------------------------------- #
# Usage accounting under threads
# --------------------------------------------------------------------- #

def test_parallel_workers_never_lose_a_token_from_the_cost_report():
    with openai_usage.track() as accumulator:
        def hammer():
            for _ in range(200):
                accumulator.add(
                    model="m", input_tokens=3, output_tokens=2,
                    estimated_cost_usd=None,
                )

        threads = [threading.Thread(target=hammer) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        usage = accumulator.models["m"]
        assert usage.request_count == 1600
        assert usage.input_tokens == 4800
        assert usage.output_tokens == 3200


# --------------------------------------------------------------------- #
# DecisionStore: atomic, locked, first-write-wins
# --------------------------------------------------------------------- #

def test_directory_store_writes_are_atomic_and_first_write_wins(tmp_path):
    store = kernel.DecisionStore(tmp_path)
    results: list[dict] = [None] * 8  # type: ignore[list-item]

    def put(i: int) -> None:
        results[i] = store.put("one-key", {"winner": i})

    threads = [threading.Thread(target=put, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # Exactly one winner, and every caller observed THAT record.
    winners = {json.dumps(r, sort_keys=True) for r in results}
    assert len(winners) == 1
    on_disk = json.loads((tmp_path / "one-key.json").read_text())
    assert on_disk == results[0]
    # No temp litter, no half-written siblings.
    assert sorted(p.name for p in tmp_path.iterdir()) == ["one-key.json"]


# --------------------------------------------------------------------- #
# Chapter Reading: the PDF lane is not re-read (or re-billed)
# --------------------------------------------------------------------- #

def _install_on_stub():
    stub = ModuleType("stub_generation")
    calls: list[str] = []

    def concepts_from_mmd(mmd_text: str, *args, **kwargs):
        calls.append(mmd_text)
        return {"ok": True}

    stub.concepts_from_mmd = concepts_from_mmd
    stub._newest_compatible_concept_checkpoint = lambda *_a, **_k: None
    chapter_reading_contract.install(stub)
    return stub, calls


def test_the_gpt_read_pdf_lane_skips_chapter_reading(monkeypatch):
    stub, calls = _install_on_stub()
    monkeypatch.setattr(
        phase2, "active_canonical",
        lambda: {"source_contract": {
            "source_reader": "gpt-pdf-to-acsd-1+verifier",
        }},
    )
    monkeypatch.setattr(
        chapter_reading, "read_chapter",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("the PDF lane must not re-read the chapter")
        ),
    )
    events: list[dict] = []
    token = progress._sink.set(events.append)
    try:
        result = stub.concepts_from_mmd("## Chapter\nBody.", live=True)
    finally:
        progress._sink.reset(token)
    assert result == {"ok": True}
    assert calls == ["## Chapter\nBody."], "raw source proceeds untouched"
    assert any(
        "Chapter Reading skipped" in e.get("message", "")
        for e in events if e.get("type") == "log"
    )


def test_a_text_upload_still_reads_the_chapter(monkeypatch):
    stub, calls = _install_on_stub()
    monkeypatch.setattr(phase2, "active_canonical", lambda: None)
    monkeypatch.setattr(
        chapter_reading, "cached_reading", lambda *a, **k: None,
    )
    monkeypatch.setattr(
        chapter_reading, "read_chapter",
        lambda raw, **k: {"mode": "live", "normalized_mmd": "NORMALIZED",
                          "census": [], "dropped_furniture": [],
                          "provenance": {}},
    )
    result = stub.concepts_from_mmd("## Raw\nBody.", live=True)
    assert result == {"ok": True}
    assert calls == ["NORMALIZED"], "the text lane keeps its reading pass"

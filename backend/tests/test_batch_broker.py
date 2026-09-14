"""The wave broker: one batch per seam, paid for once, never stranding a run.

Every test here runs against a fake provider. The live adapter is exercised
by shape only — no test in this repository spends money.
"""
from __future__ import annotations

import json
import threading
import time

import pytest

from app.services import batch_broker as bb


@pytest.fixture(autouse=True)
def _fast_waves(monkeypatch):
    """Close a wave almost immediately so a test is a test, not a wait."""
    monkeypatch.setenv(bb.QUIET_SECONDS, "0.05")
    monkeypatch.setenv(bb.MAX_WAIT_SECONDS, "0.5")
    monkeypatch.setenv(bb.DEADLINE_SECONDS, "10")
    monkeypatch.setenv(bb.POLL_SECONDS, "0.01")
    yield
    bb.reset_process_broker()


def _completion(text: str) -> dict:
    return {
        "id": "chatcmpl-x", "object": "chat.completion", "model": "gpt-5.6-luna",
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": text}}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 5, "total_tokens": 16},
    }


class FakeApi:
    """Records what was submitted; answers whatever the test set up."""

    def __init__(self, *, answers=None, fail_submit=False, status="completed"):
        self.answers = dict(answers or {})
        self.submissions: list[tuple[str, list[dict]]] = []
        self.fail_submit = fail_submit
        self._status = status
        self.cancelled: list[str] = []
        self.listed = 0

    def submit(self, jsonl: bytes, *, wave_id: str) -> str:
        if self.fail_submit:
            raise RuntimeError("provider said no")
        lines = [json.loads(row) for row in jsonl.decode("utf-8").splitlines() if row.strip()]
        self.submissions.append((wave_id, lines))
        return f"batch_{wave_id}"

    def status(self, batch_id: str) -> str:
        return self._status

    def results(self, batch_id: str) -> list[dict]:
        wave_id = batch_id.removeprefix("batch_")
        out = []
        for submitted_wave, lines in self.submissions:
            if submitted_wave != wave_id:
                continue
            for line in lines:
                sha = line["custom_id"]
                answer = self.answers.get(sha, _completion("ok"))
                if answer == "error":
                    out.append({"custom_id": sha,
                                "response": {"status_code": 400, "body": {"error": "bad"}}})
                else:
                    out.append({"custom_id": sha,
                                "response": {"status_code": 200, "body": answer}})
        return out

    def cancel(self, batch_id: str) -> None:
        self.cancelled.append(batch_id)

    def find(self, wave_id: str) -> str | None:
        self.listed += 1
        for submitted_wave, _ in self.submissions:
            if submitted_wave == wave_id:
                return f"batch_{wave_id}"
        return None


def _broker(tmp_path, api) -> bb.BatchBroker:
    broker = bb.BatchBroker(api, store=bb.BatchStore(tmp_path / "batch"))
    broker.start()
    return broker


def _body(marker: str) -> dict:
    return {"model": "gpt-5.6-luna", "messages": [{"role": "user", "content": marker}]}


# --------------------------------------------------------------------------- #
# The wave
# --------------------------------------------------------------------------- #

def test_concurrent_requests_become_one_batch_and_each_gets_its_own_answer(tmp_path):
    bodies = [_body(f"q{i}") for i in range(4)]
    answers = {bb.request_sha256(b): _completion(f"a{i}") for i, b in enumerate(bodies)}
    api = FakeApi(answers=answers)
    broker = _broker(tmp_path, api)
    results: dict[int, dict] = {}

    def ask(index: int) -> None:
        results[index] = broker.call(bodies[index])

    threads = [threading.Thread(target=ask, args=(i,)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)

    assert len(api.submissions) == 1, "four concurrent asks are one wave"
    _, lines = api.submissions[0]
    assert len(lines) == 4
    assert {line["url"] for line in lines} == {"/v1/chat/completions"}
    for index in range(4):
        assert results[index]["choices"][0]["message"]["content"] == f"a{index}"
    broker.stop()


def test_two_threads_asking_the_identical_question_are_one_line(tmp_path):
    body = _body("same")
    api = FakeApi(answers={bb.request_sha256(body): _completion("once")})
    broker = _broker(tmp_path, api)
    out: list[dict] = []
    threads = [threading.Thread(target=lambda: out.append(broker.call(body))) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)
    assert len(api.submissions) == 1
    assert len(api.submissions[0][1]) == 1, "identical bytes are one request"
    assert [row["choices"][0]["message"]["content"] for row in out] == ["once"] * 3
    broker.stop()


# --------------------------------------------------------------------------- #
# Paid once
# --------------------------------------------------------------------------- #

def test_a_stored_response_is_served_without_asking_the_provider(tmp_path):
    body = _body("cached")
    api = FakeApi(answers={bb.request_sha256(body): _completion("first")})
    broker = _broker(tmp_path, api)
    assert broker.call(body)["choices"][0]["message"]["content"] == "first"
    assert len(api.submissions) == 1
    # Second ask, same bytes: no wave at all.
    assert broker.call(body)["choices"][0]["message"]["content"] == "first"
    assert len(api.submissions) == 1, "a second identical ask must not be bought again"
    broker.stop()


def test_a_wave_record_is_written_before_the_request_leaves(tmp_path):
    body = _body("recorded")
    api = FakeApi(answers={bb.request_sha256(body): _completion("ok")})
    store = bb.BatchStore(tmp_path / "batch")
    broker = bb.BatchBroker(api, store=store)
    broker.start()
    broker.call(body)
    broker.stop()
    waves = [json.loads(path.read_text()) for path in (store.waves).glob("*.json")]
    assert len(waves) == 1
    wave = waves[0]
    assert wave["batch_id"].startswith("batch_")
    assert wave["state"] == "harvested"
    assert wave["entries"][0]["request_sha256"] == bb.request_sha256(body)


def test_a_crashed_process_harvests_what_its_wave_already_bought(tmp_path):
    """The crash plan: a wave left open is re-attached, its results banked,
    and the next identical ask is free."""
    body = _body("survivor")
    sha = bb.request_sha256(body)
    api = FakeApi(answers={sha: _completion("bought")})
    store = bb.BatchStore(tmp_path / "batch")

    # A wave that was submitted by a process which then died: the record
    # exists, the provider has the answer, the response store does not.
    wave_id = "deadbeefdeadbeef"
    api.submissions.append((wave_id, [{"custom_id": sha, "method": "POST",
                                       "url": "/v1/chat/completions", "body": body}]))
    store.put_wave({"wave_id": wave_id, "state": "submitted",
                    "batch_id": f"batch_{wave_id}",
                    "entries": [{"custom_id": sha, "request_sha256": sha}]})

    broker = bb.BatchBroker(api, store=store)
    assert broker.recover() == 1
    assert store.wave(wave_id)["state"] == "harvested"
    # And the run that comes back asks for nothing.
    broker.start()
    assert broker.call(body)["choices"][0]["message"]["content"] == "bought"
    assert len(api.submissions) == 1, "recovery must not re-submit a paid wave"
    broker.stop()


def test_a_wave_whose_record_lost_its_batch_id_is_found_by_its_wave_id(tmp_path):
    body = _body("orphan")
    sha = bb.request_sha256(body)
    api = FakeApi(answers={sha: _completion("found")})
    store = bb.BatchStore(tmp_path / "batch")
    wave_id = "0123456789abcdef"
    api.submissions.append((wave_id, [{"custom_id": sha, "method": "POST",
                                       "url": "/v1/chat/completions", "body": body}]))
    store.put_wave({"wave_id": wave_id, "state": "preparing", "batch_id": "",
                    "entries": [{"custom_id": sha, "request_sha256": sha}]})
    broker = bb.BatchBroker(api, store=store)
    assert broker.recover() == 1
    assert api.listed == 1
    assert store.response(sha)["body"]["choices"][0]["message"]["content"] == "found"


def test_a_wave_that_never_reached_the_provider_is_abandoned_not_replayed(tmp_path):
    store = bb.BatchStore(tmp_path / "batch")
    store.put_wave({"wave_id": "nevercreated00", "state": "preparing", "batch_id": "",
                    "entries": [{"custom_id": "x", "request_sha256": "x"}]})
    broker = bb.BatchBroker(FakeApi(), store=store)
    assert broker.recover() == 0
    assert store.wave("nevercreated00")["state"] == "abandoned"


# --------------------------------------------------------------------------- #
# Never strands a run
# --------------------------------------------------------------------------- #

def test_a_refused_submission_sends_the_caller_back_to_the_ordinary_call(tmp_path):
    broker = _broker(tmp_path, FakeApi(fail_submit=True))
    with pytest.raises(bb.BatchUnavailable):
        broker.call(_body("refused"))
    broker.stop()


def test_a_failed_line_releases_only_its_own_caller(tmp_path):
    good, bad = _body("good"), _body("bad")
    api = FakeApi(answers={
        bb.request_sha256(good): _completion("fine"),
        bb.request_sha256(bad): "error",
    })
    broker = _broker(tmp_path, api)
    out: dict[str, object] = {}

    def ask(name: str, body: dict) -> None:
        try:
            out[name] = broker.call(body)
        except bb.BatchUnavailable as exc:
            out[name] = exc

    threads = [threading.Thread(target=ask, args=("good", good)),
               threading.Thread(target=ask, args=("bad", bad))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)
    assert out["good"]["choices"][0]["message"]["content"] == "fine"
    assert isinstance(out["bad"], bb.BatchUnavailable)
    broker.stop()


def test_a_batch_that_never_completes_times_the_caller_out_and_is_cancelled(tmp_path, monkeypatch):
    monkeypatch.setenv(bb.DEADLINE_SECONDS, "1")
    api = FakeApi(status="in_progress")
    broker = _broker(tmp_path, api)
    with pytest.raises(bb.BatchUnavailable):
        broker.call(_body("slow"))
    assert api.submissions, "the wave was submitted; only the answer never came"
    # The dispatcher gives up on its own deadline and cancels, so nothing is
    # left running at the provider's expense.
    threading.Event().wait(2.0)
    assert api.cancelled, "an abandoned wave must be cancelled"
    broker.stop()


def test_a_ready_wave_is_not_held_for_a_poll_tick(tmp_path, monkeypatch):
    """The dispatcher sleeps only as long as the open wave can afford: a
    fixed tick would be added to every one of a chapter's seams."""
    monkeypatch.setenv(bb.QUIET_SECONDS, "0.05")
    body = _body("prompt")
    api = FakeApi(answers={bb.request_sha256(body): _completion("quick")})
    broker = _broker(tmp_path, api)
    started = time.monotonic()
    broker.call(body)
    assert time.monotonic() - started < 0.9, "a ready wave waited for a tick"
    broker.stop()


# --------------------------------------------------------------------------- #
# Binding
# --------------------------------------------------------------------------- #

def test_only_a_bound_thread_calls_through_the_broker(tmp_path):
    broker = _broker(tmp_path, FakeApi())
    assert bb.bound() is None
    with bb.session(broker) as active:
        assert active is broker
        assert bb.bound() is broker
    assert bb.bound() is None
    broker.stop()


def test_request_identity_is_the_canonical_body(tmp_path):
    a = {"model": "m", "messages": [{"role": "user", "content": "x"}]}
    b = {"messages": [{"role": "user", "content": "x"}], "model": "m"}
    assert bb.request_sha256(a) == bb.request_sha256(b), "key order is not identity"
    c = {"model": "m", "messages": [{"role": "user", "content": "y"}]}
    assert bb.request_sha256(a) != bb.request_sha256(c)


def test_the_binding_reaches_the_stage_fan_out_workers(tmp_path):
    """The sixteen sibling requests of one stage are the wave. They run on
    pool threads under a COPY of the caller's context, so the binding has to
    be a contextvar — a thread local would bind only the orchestrator and
    every real request would miss the batch."""
    from app.services.phase3 import kernel

    broker = _broker(tmp_path, FakeApi())
    seen: list[object] = []
    with bb.session(broker):
        kernel.parallel_map_in_order(
            [1, 2, 3], lambda item: seen.append(bb.bound()), max_workers=3,
        )
    assert seen == [broker, broker, broker]
    broker.stop()

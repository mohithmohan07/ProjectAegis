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


def test_an_uncertain_legacy_submission_remains_open_not_rebought(tmp_path):
    store = bb.BatchStore(tmp_path / "batch")
    store.put_wave({"wave_id": "nevercreated00", "state": "preparing", "batch_id": "",
                    "entries": [{"custom_id": "x", "request_sha256": "x"}]})
    broker = bb.BatchBroker(FakeApi(), store=store)
    assert broker.recover() == 0
    assert store.wave("nevercreated00")["state"] == "preparing"
    assert store.open_waves(), "a missing listing cannot prove no batch was accepted"


# --------------------------------------------------------------------------- #
# Never strands a run
# --------------------------------------------------------------------------- #

def test_an_uncertain_submission_pauses_without_changing_price_lane(tmp_path, monkeypatch):
    monkeypatch.setenv(bb.DEADLINE_SECONDS, "0.15")
    broker = _broker(tmp_path, FakeApi(fail_submit=True))
    with pytest.raises(bb.BatchPending):
        broker.call(_body("refused"))
    assert broker._store.open_waves()[0]["state"] == "submission_unknown"
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


def test_slow_batch_stays_open_and_resume_attaches_without_cancellation(tmp_path, monkeypatch):
    monkeypatch.setenv(bb.DEADLINE_SECONDS, "0.15")
    api = FakeApi(status="in_progress")
    broker = _broker(tmp_path, api)
    with pytest.raises(bb.BatchPending):
        broker.call(_body("slow"))
    assert api.submissions, "the wave was submitted; only the answer never came"
    assert not api.cancelled, "a waiter timeout must preserve purchased work"
    assert broker._store.open_waves()
    api._status = "completed"
    assert broker.call(_body("slow"))["choices"][0]["message"]["content"] == "ok"
    assert len(api.submissions) == 1
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


def test_every_provider_call_path_goes_through_the_one_door(tmp_path):
    """A cohort is billed at the batch price for its WHOLE run.

    Three modules build an OpenAI client: the main generation call and the
    two source-reading paths. If a new one appears without going through
    ``generation.batched_completion``, a cohort would silently pay the
    synchronous price for part of its run — the exact defect this pins.
    """
    import pathlib
    import re

    from app.services import generation

    root = pathlib.Path(generation.__file__).parent
    builders = {
        path.name
        for path in root.rglob("*.py")
        if re.search(r"^\s*(client\s*=\s*)?OpenAI\(", path.read_text(encoding="utf-8"),
                     re.MULTILINE)
    }
    # The queue worker builds one for the BATCH endpoint itself, which is the
    # far side of the door rather than a caller of it.
    callers = builders - {"chapter_queue_worker.py"}
    assert callers == {
        "generation.py",
        "canonical_source_phase22.py",
        "canonical_source_phase34_structured_output_contract.py",
    }, callers
    for name in callers:
        text = (root / name).read_text(encoding="utf-8")
        assert "batched_completion" in text, f"{name} bypasses the wave"


# --------------------------------------------------------------------------- #
# A retry must not be answered from the store
# --------------------------------------------------------------------------- #

def test_a_retry_asks_the_provider_again_instead_of_replaying_a_bad_answer(tmp_path):
    """The caller's retry loop replays a BYTE-IDENTICAL body.

    ``messages``, ``response_format`` and the token limit are all built once
    above ``_generate``'s loop, so a truncated or schema-failing completion
    hashes to the same request as its retry. Answering that retry from the
    content-addressed store hands it the same bad bytes — and the record is
    durable on the volume, so it would answer every future run that builds the
    same body too. Synchronously the replay is a fresh sample; batched it has
    to be one as well.
    """
    body = _body("retried")
    sha = bb.request_sha256(body)
    api = FakeApi(answers={sha: _completion("truncated")})
    broker = _broker(tmp_path, api)

    assert broker.call(body)["choices"][0]["message"]["content"] == "truncated"
    assert len(api.submissions) == 1

    # The ordinary path still answers from the store — that is the saving.
    assert broker.call(body)["choices"][0]["message"]["content"] == "truncated"
    assert len(api.submissions) == 1

    # ...and the retry does not: it forms a new wave and asks again.
    api.answers[sha] = _completion("good")
    assert broker.call(body, fresh=True)["choices"][0]["message"]["content"] == "good"
    assert len(api.submissions) == 2, "a retry must reach the provider"
    broker.stop()


def test_a_healed_answer_serves_every_later_caller(tmp_path):
    """The store is content-addressed, so a successful retry overwrites the
    failure under the same hash and the poisoning ends there."""
    body = _body("healed")
    sha = bb.request_sha256(body)
    api = FakeApi(answers={sha: _completion("truncated")})
    broker = _broker(tmp_path, api)
    broker.call(body)
    api.answers[sha] = _completion("good")
    broker.call(body, fresh=True)

    assert broker.call(body)["choices"][0]["message"]["content"] == "good"
    assert len(api.submissions) == 2, "the healed answer is served, not re-bought"
    broker.stop()


def _saved_wave(store, api, body, *, wave_id="surviving", fresh=False):
    sha = bb.request_sha256(body)
    api.submissions.append((wave_id, [{"custom_id": sha, "body": body}]))
    store.put_wave({"wave_id": wave_id, "batch_id": f"batch_{wave_id}",
                    "state": "submitted", "line_count": 1,
                    "created_at_ns": time.time_ns(),
                    "entries": [{"request_sha256": sha, "body": body,
                                 "fresh": fresh}]})
    return wave_id


def test_restart_attaches_to_still_running_wave_and_polls_without_another_boot(tmp_path):
    body = _body("restart while provider still runs")
    api = FakeApi(status="in_progress")
    store = bb.BatchStore(tmp_path / "batch")
    _saved_wave(store, api, body)
    broker = bb.BatchBroker(api, store=store)
    assert broker.recover() == 0
    out = []
    thread = threading.Thread(target=lambda: out.append(broker.call_result(body)))
    thread.start()
    # A new wave would be submitted by this point under the old implementation.
    time.sleep(0.12)
    assert len(api.submissions) == 1
    api._status = "completed"
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert out[0].body["choices"][0]["message"]["content"] == "ok"
    assert len(api.submissions) == 1
    broker.stop()
    assert store.wave("surviving")["state"] == "harvested"


def test_fresh_retry_after_restart_attaches_to_its_pending_retry_wave(tmp_path):
    body = _body("fresh restart")
    sha = bb.request_sha256(body)
    api = FakeApi(status="in_progress", answers={sha: _completion("repaired")})
    store = bb.BatchStore(tmp_path / "batch")
    store.put_response(sha, _completion("bad"), batch_id="older")
    _saved_wave(store, api, body, fresh=True)
    broker = bb.BatchBroker(api, store=store)
    out = []
    thread = threading.Thread(target=lambda: out.append(broker.call(body, fresh=True)))
    thread.start()
    time.sleep(0.1)
    api._status = "completed"
    thread.join(timeout=2)
    assert out[0]["choices"][0]["message"]["content"] == "repaired"
    assert len(api.submissions) == 1
    assert store.response(sha)["body"]["choices"][0]["message"]["content"] == "repaired"
    broker.stop()


def test_lost_submission_response_is_found_before_any_resubmission(tmp_path):
    class LostResponseApi(FakeApi):
        def submit(self, jsonl, *, wave_id):
            super().submit(jsonl, wave_id=wave_id)
            raise TimeoutError("batch created but the response was lost")

    api = LostResponseApi()
    broker = _broker(tmp_path, api)
    result = broker.call_result(_body("lost create response"))
    assert result.body["choices"][0]["message"]["content"] == "ok"
    assert api.listed >= 1
    assert len(api.submissions) == 1
    broker.stop()
    assert not broker._store.open_waves()


def test_temporary_result_download_failure_retains_paid_wave_for_retry(tmp_path):
    class FlakyDownloadApi(FakeApi):
        def __init__(self):
            super().__init__()
            self.downloads = 0

        def results(self, batch_id):
            self.downloads += 1
            if self.downloads == 1:
                raise ConnectionError("output download interrupted")
            return super().results(batch_id)

    api = FlakyDownloadApi()
    store = bb.BatchStore(tmp_path / "batch")
    _saved_wave(store, api, _body("download retry"))
    broker = bb.BatchBroker(api, store=store)
    assert broker.recover() == 0
    assert store.open_waves(), "failed download must not mark the wave harvested"
    assert broker.recover() == 1
    assert not store.open_waves()
    assert len(api.submissions) == 1


def test_slow_wave_does_not_block_independent_wave_submission(tmp_path):
    class IndependentApi(FakeApi):
        def status(self, batch_id):
            return "in_progress" if batch_id == "batch_surviving" else "completed"

    api = IndependentApi()
    store = bb.BatchStore(tmp_path / "batch")
    _saved_wave(store, api, _body("slow old chapter"))
    broker = bb.BatchBroker(api, store=store)
    assert broker.call(_body("independent new chapter"))["choices"]
    assert len(api.submissions) == 2
    assert store.wave("surviving")["state"] == "submitted"
    broker.stop()


def test_shared_and_cached_responses_expose_one_new_receipt(tmp_path):
    broker = _broker(tmp_path, FakeApi())
    body = _body("same paid response")
    out = []
    threads = [threading.Thread(target=lambda: out.append(broker.call_result(body)))
               for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)
    assert len(out) == 4
    assert sum(not row.reused for row in out) == 1
    replay = broker.call_result(body)
    assert replay.reused
    assert len({row.receipt_id for row in out + [replay]}) == 1
    broker.stop()


def test_shutdown_preserves_pending_wave_for_restart_without_provider_cancel(tmp_path, monkeypatch):
    monkeypatch.setenv(bb.QUIET_SECONDS, "10")
    api = FakeApi()
    store = bb.BatchStore(tmp_path / "batch")
    broker = bb.BatchBroker(api, store=store)
    failures = []

    def ask():
        try:
            broker.call(_body("shutdown before submission"))
        except bb.BatchPending as exc:
            failures.append(exc)

    thread = threading.Thread(target=ask)
    thread.start()
    for _ in range(100):
        if broker._pending:
            break
        time.sleep(0.001)
    broker.stop()
    thread.join(timeout=2)
    assert len(failures) == 1
    assert api.submissions == []
    assert not api.cancelled
    assert store.open_waves()[0]["state"] == "prepared"
    restarted = bb.BatchBroker(api, store=store)
    assert restarted.call(_body("shutdown before submission"))["choices"]
    assert len(api.submissions) == 1
    restarted.stop()


def test_live_adapter_searches_later_pages_during_reconciliation():
    from types import SimpleNamespace

    class Page:
        def __init__(self, data, next_page=None):
            self.data, self.next_page = data, next_page

        def has_next_page(self):
            return self.next_page is not None

        def get_next_page(self):
            return self.next_page

    second = Page([SimpleNamespace(id="older-batch", metadata={"aegis_wave": "wanted"})])
    first = Page([SimpleNamespace(id="newer-batch", metadata={})], second)
    api = bb.OpenAIBatchApi(lambda: SimpleNamespace(
        batches=SimpleNamespace(list=lambda **kwargs: first)))
    assert api.find("wanted") == "older-batch"


def test_live_adapter_disables_hidden_sdk_retries_on_batch_creation():
    from types import SimpleNamespace

    selected_options = []
    creates = []

    def create(**kwargs):
        creates.append(kwargs)
        return SimpleNamespace(id="paid-once")

    def with_options(**kwargs):
        selected_options.append(kwargs)
        return SimpleNamespace(batches=SimpleNamespace(create=create))

    client = SimpleNamespace(
        files=SimpleNamespace(create=lambda **kwargs: SimpleNamespace(id="input")),
        with_options=with_options,
    )
    api = bb.OpenAIBatchApi(lambda: client)
    assert api.submit(b"{}\\n", wave_id="durable") == "paid-once"
    assert selected_options == [{"max_retries": 0}]
    assert len(creates) == 1
    assert creates[0]["metadata"] == {"aegis_wave": "durable"}


def test_paid_receipt_ledger_retains_retries_and_original_owner(tmp_path):
    broker = _broker(tmp_path, FakeApi())
    body = _body("receipt survives cache replacement")
    first = broker.call_result(body, owner_job_id=41)
    reused = broker.call_result(body, owner_job_id=99)
    second = broker.call_result(body, fresh=True, owner_job_id=41)
    broker.stop()
    assert first.owner_job_id == reused.owner_job_id == second.owner_job_id == 41
    assert reused.reused and reused.receipt_id == first.receipt_id
    ledger = broker._store.paid_receipts()
    assert len(ledger) == 2
    assert {item["receipt_id"] for item in ledger} == {first.receipt_id, second.receipt_id}
    assert all(item["owner_job_id"] == 41 for item in ledger)
    assert all("cost_estimate" in item for item in ledger)
    assert all(item["usage"]["prompt_tokens"] == 11 for item in ledger)
    assert broker._store.response(bb.request_sha256(body))["batch_id"] == second.batch_id


def test_early_deploy_pause_releases_waiter_before_broker_shutdown(tmp_path, monkeypatch):
    """SIGTERM precedes Uvicorn's HTTP drain; don't wait for lifespan stop."""
    pausing = [False]
    monkeypatch.setattr(bb.run_control, "pausing", lambda: pausing[0])
    api = FakeApi(status="in_progress")
    broker = _broker(tmp_path, api)
    failures = []

    def ask():
        try:
            broker.call(_body("early deployment pause"))
        except bb.BatchPending as exc:
            failures.append(exc)

    thread = threading.Thread(target=ask)
    thread.start()
    for _ in range(200):
        if api.submissions:
            break
        time.sleep(0.005)
    assert api.submissions
    pausing[0] = True
    thread.join(timeout=1)
    assert not thread.is_alive()
    assert failures and failures[0].batch_id
    assert not broker._stop.is_set(), "early pause must work before broker.stop"
    assert not api.cancelled
    assert broker._store.open_waves()[0]["state"] == "submitted"
    broker.stop()


def test_early_deploy_pause_keeps_prepared_wave_without_submitting(tmp_path, monkeypatch):
    monkeypatch.setattr(bb.run_control, "pausing", lambda: True)
    api = FakeApi()
    broker = bb.BatchBroker(api, store=bb.BatchStore(tmp_path / "batch"))
    body = _body("prepared when deployment starts")
    waiter = bb._Waiter(sha=bb.request_sha256(body), body=body)
    record = broker._prepare_wave([waiter])
    broker.recover()
    assert api.submissions == []
    assert api.listed == 0
    assert broker._store.wave(record["wave_id"])["state"] == "prepared"
    assert not api.cancelled
    broker.stop()

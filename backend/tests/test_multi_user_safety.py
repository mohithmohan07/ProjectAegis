"""Multi-user safety: shared-API-key concurrency gate, rate-limit backoff,
and SQLite write-concurrency pragmas."""
import threading
import time

import httpx
import pytest
from openai import APITimeoutError, RateLimitError

from app import config
from app.services import generation as g


def _rate_limit_error(
    retry_after: str | None = None, *, code: str | None = None,
) -> RateLimitError:
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    headers = {"retry-after": retry_after} if retry_after is not None else {}
    response = httpx.Response(429, request=request, headers=headers)
    body = {"error": {"code": code}} if code else None
    return RateLimitError("rate limited", response=response, body=body)


class _FakeResponse:
    def __init__(self, content: str = "{\"rows\": []}"):
        message = type("M", (), {"content": content})()
        self.choices = [type("C", (), {"message": message, "finish_reason": "stop"})()]


class _FakeClient:
    """Stands in for openai.OpenAI; behavior driven by a scripted plan."""

    lock = threading.Lock()
    in_flight = 0
    max_in_flight = 0
    plan: list = []  # each entry: exception to raise, or None to succeed
    init_kwargs: list[dict] = []

    def __init__(self, *a, **kw):
        type(self).init_kwargs.append(dict(kw))
        completions = type("Completions", (), {"create": self._create})()
        self.chat = type("Chat", (), {"completions": completions})()

    def _create(self, **kw):
        cls = _FakeClient
        with cls.lock:
            cls.in_flight += 1
            cls.max_in_flight = max(cls.max_in_flight, cls.in_flight)
            step = cls.plan.pop(0) if cls.plan else None
        try:
            time.sleep(0.05)
            if step is not None:
                raise step
            return _FakeResponse()
        finally:
            with cls.lock:
                cls.in_flight -= 1


@pytest.fixture()
def fake_openai(monkeypatch):
    import openai

    _FakeClient.plan = []
    _FakeClient.in_flight = 0
    _FakeClient.max_in_flight = 0
    _FakeClient.init_kwargs = []
    monkeypatch.setattr(openai, "OpenAI", _FakeClient)
    # Fresh gate per test so config changes take effect.
    g._openai_gate = None
    yield _FakeClient
    g._openai_gate = None


def test_concurrent_calls_respect_the_gate(fake_openai, monkeypatch):
    """8 simultaneous callers (multiple users) never exceed the configured cap."""
    monkeypatch.setattr(config, "OPENAI_MAX_CONCURRENCY", 2)
    results: list[dict] = []
    errors: list[Exception] = []

    def call():
        try:
            results.append(g._openai_json("s", "u"))
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=call) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert len(results) == 8
    assert fake_openai.max_in_flight <= 2


def test_rate_limit_is_retried_until_success(fake_openai, monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))
    fake_openai.plan = [_rate_limit_error(), _rate_limit_error(), None]
    out = g._openai_json("s", "u")
    assert out == {"rows": []}
    backoffs = [s for s in sleeps if s >= 1]  # ignore the fake client's 0.05s work
    assert len(backoffs) == 2
    assert all(s > 0 for s in backoffs)


def test_insufficient_quota_fails_immediately_without_retry(
    fake_openai, monkeypatch,
):
    sleeps: list[float] = []
    monkeypatch.setattr(time, "sleep", lambda seconds: sleeps.append(seconds))
    fake_openai.plan = [
        _rate_limit_error(code="insufficient_quota"),
        None,
    ]

    with pytest.raises(RuntimeError, match="quota exhausted.*not retried"):
        g._openai_json("s", "u")

    # The scripted success remains untouched: only one provider call occurred.
    assert fake_openai.plan == [None]
    assert not [seconds for seconds in sleeps if seconds >= 1]


def test_retry_after_header_is_honoured(fake_openai, monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))
    fake_openai.plan = [_rate_limit_error(retry_after="7"), None]
    g._openai_json("s", "u")
    backoffs = [s for s in sleeps if s >= 1]
    assert backoffs and backoffs[0] >= 7.0


def test_timeouts_are_transient_too(fake_openai, monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    fake_openai.plan = [APITimeoutError(request=httpx.Request("POST", "https://x")), None]
    assert g._openai_json("s", "u") == {"rows": []}


def test_openai_client_uses_the_configured_request_timeout(
    fake_openai, monkeypatch,
):
    monkeypatch.setattr(config, "OPENAI_REQUEST_TIMEOUT_SECONDS", 123.0)

    assert g._openai_json("s", "u") == {"rows": []}

    assert fake_openai.init_kwargs == [{
        "timeout": 123.0,
        "max_retries": 0,
    }]


def test_busy_openai_slot_fails_after_the_configured_wait(
    fake_openai, monkeypatch,
):
    gate = threading.BoundedSemaphore(1)
    assert gate.acquire(blocking=False)
    g._openai_gate = gate
    monkeypatch.setattr(config, "OPENAI_SLOT_WAIT_TIMEOUT_SECONDS", 0.0)
    logs: list[str] = []
    monkeypatch.setattr(
        g.progress,
        "log",
        lambda message, **_kwargs: logs.append(str(message)),
    )

    try:
        with pytest.raises(g.OpenAIQueueTimeoutError, match="capacity is busy"):
            g._openai_json("s", "u")
    finally:
        gate.release()

    assert any("waiting for a free" in message for message in logs)
    assert fake_openai.plan == []


def test_a_slot_freed_within_the_quiet_grace_logs_nothing(monkeypatch):
    # At full concurrency a handoff routinely takes well under a second;
    # a live run's console was buried in busy/acquired pairs for 0-second
    # waits. Within the quiet grace the wait is silent.
    gate = threading.BoundedSemaphore(1)
    assert gate.acquire(blocking=False)
    monkeypatch.setattr(config, "OPENAI_SLOT_WAIT_TIMEOUT_SECONDS", 30.0)
    monkeypatch.setattr(config, "OPENAI_SLOT_WAIT_QUIET_SECONDS", 5.0)
    logs: list[str] = []
    monkeypatch.setattr(
        g.progress,
        "log",
        lambda message, **_kwargs: logs.append(str(message)),
    )
    releaser = threading.Timer(0.05, gate.release)
    releaser.start()
    try:
        g._acquire_openai_slot(gate, purpose="concept_mapping")
    finally:
        releaser.cancel()
        gate.release()

    assert logs == []


def test_a_wait_beyond_the_quiet_grace_is_spoken(monkeypatch):
    gate = threading.BoundedSemaphore(1)
    assert gate.acquire(blocking=False)
    monkeypatch.setattr(config, "OPENAI_SLOT_WAIT_TIMEOUT_SECONDS", 30.0)
    monkeypatch.setattr(config, "OPENAI_SLOT_WAIT_QUIET_SECONDS", 0.05)
    monkeypatch.setattr(config, "OPENAI_SLOT_WAIT_LOG_SECONDS", 5.0)
    logs: list[str] = []
    monkeypatch.setattr(
        g.progress,
        "log",
        lambda message, **_kwargs: logs.append(str(message)),
    )
    releaser = threading.Timer(0.3, gate.release)
    releaser.start()
    try:
        g._acquire_openai_slot(gate, purpose="concept_mapping")
    finally:
        releaser.cancel()
        gate.release()

    assert any("waiting for a free concept mapping slot" in m for m in logs)
    assert any("slot acquired after" in m for m in logs)


def test_a_grace_that_consumes_the_whole_budget_reports_a_timeout(
    monkeypatch,
):
    # With OPENAI_SLOT_WAIT_QUIET_SECONDS >= the timeout budget, the
    # quiet acquire absorbs everything: the console must then say the
    # wait TIMED OUT, not promise a wait that cannot follow.
    gate = threading.BoundedSemaphore(1)
    assert gate.acquire(blocking=False)
    monkeypatch.setattr(config, "OPENAI_SLOT_WAIT_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(config, "OPENAI_SLOT_WAIT_QUIET_SECONDS", 5.0)
    logs: list[str] = []
    monkeypatch.setattr(
        g.progress,
        "log",
        lambda message, **_kwargs: logs.append(str(message)),
    )
    try:
        with pytest.raises(g.OpenAIQueueTimeoutError):
            g._acquire_openai_slot(gate, purpose="concept_mapping")
    finally:
        gate.release()

    assert any("timed out after" in m for m in logs)
    # The plain "about to start waiting" promise must not appear — the
    # timed-out line (which also names the slot) replaces it.
    assert not any("busy; waiting for a free" in m for m in logs)


def test_persistent_rate_limit_eventually_fails_clearly(fake_openai, monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    monkeypatch.setattr(config, "OPENAI_TRANSIENT_RETRIES", 3)
    fake_openai.plan = [_rate_limit_error() for _ in range(10)]
    with pytest.raises(RuntimeError, match="transient retries"):
        g._openai_json("s", "u")


def test_bad_json_still_uses_bounded_retries(fake_openai, monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    fake_openai.plan = [ValueError("boom"), ValueError("boom"), ValueError("boom")]
    with pytest.raises(RuntimeError, match="failed after 3 retries"):
        g._openai_json("s", "u")


def test_section_numbers_are_scrubbed_deterministically():
    records = [
        {"topic": "EXERCISE 1.2", "parent_concept": "P",
         "concept_title": "Locating rationals 1.3 on the line",
         "concept_details": "Description: x", "keywords": ""},
        {"topic": "Irrational Numbers", "parent_concept": "P",
         "concept_title": "Definition of irrationals",
         "concept_details": "Description: y", "keywords": ""},
    ]
    # Exercise-only topic merges into the nearest real topic; numbering is
    # stripped from titles. First row has no preceding topic, so it falls
    # back to "General".
    out = g._scrub_section_numbers(records)
    assert out[0]["concept_title"] == "Locating rationals on the line"
    assert out[0]["topic"] == "General"
    records2 = [
        {"topic": "Real Numbers", "parent_concept": "P", "concept_title": "A",
         "concept_details": "Description: x", "keywords": ""},
        {"topic": "EXERCISE 1.4", "parent_concept": "P", "concept_title": "B",
         "concept_details": "Description: y", "keywords": ""},
    ]
    out2 = g._scrub_section_numbers(records2)
    assert out2[1]["topic"] == "Real Numbers"


def test_culminations_are_enforced_mechanically():
    def row(topic, title):
        return {"topic": topic, "parent_concept": "P", "concept_title": title,
                "concept_details": "Description: x", "keywords": ""}

    records = [
        # Topic A: two culminations (model duplicated) and one out of order,
        # over two taught concepts.
        row("A", "Culmination - First"),
        row("A", "Concept A1"),
        row("A", "Concept A2"),
        row("A", "Culmination - Second"),
        # Topic B: two concepts and no culmination at all.
        row("B", "Concept B1"),
        row("B", "Concept B2"),
        # Topic C: a single concept — nothing to consolidate.
        row("C", "Concept C1"),
        # Topic D: a single concept carrying a stray culmination.
        row("D", "Concept D1"),
        row("D", "Culmination - D"),
    ]
    out = g._enforce_culminations(records)
    a_rows = [r for r in out if r["topic"] == "A"]
    b_rows = [r for r in out if r["topic"] == "B"]
    c_rows = [r for r in out if r["topic"] == "C"]
    d_rows = [r for r in out if r["topic"] == "D"]
    a_culms = [r for r in a_rows if r["concept_title"].startswith("Culmination")]
    b_culms = [r for r in b_rows if r["concept_title"].startswith("Culmination")]
    # Duplicates normalize to the first AUTHORED row, positioned last; its
    # authored title is never rebuilt from code.
    assert len(a_culms) == 1 and a_rows[-1] is a_culms[0]
    assert a_culms[0]["concept_title"] == "Culmination - First"
    # A topic the model left without a culmination ships WITHOUT one — the
    # validator report flags it; no row is invented.
    assert b_culms == []
    # A topic teaching one concept gets no culmination, and a stray one is
    # dropped rather than kept.
    assert [r["concept_title"] for r in c_rows] == ["Concept C1"]
    assert [r["concept_title"] for r in d_rows] == ["Concept D1"]
    # Normal rows all survive.
    assert [r["concept_title"] for r in a_rows[:-1]] == [
        "Concept A1", "Concept A2"]
    assert [r["concept_title"] for r in b_rows] == [
        "Concept B1", "Concept B2"]


def test_source_artifacts_are_neutralized_even_in_types():
    from app.services import concept_cleanup as cc
    from app.services import concept_validator as cv

    rec = {
        "topic": "Real Numbers", "parent_concept": "P",
        "concept_title": "Rationalising denominators",
        "concept_details": (
            "Description: Convert as shown in Example 11 on page 14. // "
            "Types: Type 01: Rationalise a surd denominator "
            "Case 01: Rationalise the expressions given in Exercise 1.5"
        ),
        "keywords": "",
    }
    out = cc.clean_concept_record(dict(rec))
    details = out["concept_details"]
    assert "Example 11" not in details
    assert "page 14" not in details
    assert "Exercise 1.5" not in details
    # Structure and task content survive.
    assert "Type 01:" in details and "Case 01:" in details
    assert "Rationalise the expressions" in details
    report = cv.validate_concept_rows(
        [out], allow_types=True, require_culmination=False, allow_culmination=True)
    assert not [e for e in report["errors"] if e["code"] == "source_artifact"]


def test_pre_repair_cleanup_keeps_references_for_content_inlining():
    """Before the final repair, references stay intact so the LLM can replace
    them with the full actual problem content from the source."""
    from app.services import concept_cleanup as cc

    rec = {
        "topic": "Real Numbers", "parent_concept": "P",
        "concept_title": "Rationalising denominators",
        "concept_details": (
            "Description: Convert recurring decimals as in Example 8. // "
            "Types: Type 01: Rationalise a surd denominator "
            "Case 01: Rationalise the expressions given in Exercise 1.5"
        ),
        "keywords": "",
    }
    kept = cc.clean_concept_record(dict(rec), neutralize_artifacts=False)
    assert "Example 8" in kept["concept_details"]
    assert "Exercise 1.5" in kept["concept_details"]
    # The default (post-repair last resort) still removes them.
    scrubbed = cc.clean_concept_record(dict(rec))
    assert "Example 8" not in scrubbed["concept_details"]
    assert "Exercise 1.5" not in scrubbed["concept_details"]


def test_prompts_require_full_source_content():
    mining = g.prompts.get_text("concepts.type_mining.system")
    assert "EXAMPLES CARRY THE FULL SOURCE QUESTION" in mining
    assert "Do not shorten or truncate source questions" in mining
    assert "Rationalise the denominator of 1/(7 + 3*sqrt(2))" in mining
    repair = g.prompts.get_text("concepts.repair.system")
    assert "substitute the FULL" in repair
    refine = g.prompts.get_text("concepts.description_refine.system")
    assert "substitute the full actual" in refine


def test_source_artifacts_in_titles_and_topics_are_removed():
    from app.services import concept_cleanup as cc
    from app.services import concept_validator as cv

    rec = {
        "topic": "Triangles Exercise 6.2",
        "parent_concept": "Similarity from Fig 6.4",
        "concept_title": "Applying the midpoint theorem as in Example 5 on page 14",
        "concept_details": "Description: Valid content.",
        "keywords": "",
    }
    out = cc.clean_concept_record(dict(rec))
    combined = " ".join(
        [out["topic"], out["parent_concept"], out["concept_title"], out["concept_details"]])
    assert "Example 5" not in combined
    assert "Fig" not in combined
    assert "page 14" not in combined.lower()
    assert "6.2" not in combined
    # The real wording survives.
    assert "Midpoint Theorem" in out["concept_title"]
    assert out["topic"].startswith("Triangles")
    report = cv.validate_concept_rows(
        [out], allow_types=True, require_culmination=False, allow_culmination=True)
    assert not [e for e in report["errors"] if e["code"] == "source_artifact"]


def test_sqlite_uses_wal_and_busy_timeout():
    from app.db import engine

    with engine.connect() as conn:
        mode = conn.exec_driver_sql("PRAGMA journal_mode").scalar()
        busy = conn.exec_driver_sql("PRAGMA busy_timeout").scalar()
    assert str(mode).lower() == "wal"
    assert int(busy) >= 30000


def test_a_moving_queue_extends_the_wait_instead_of_timing_out(monkeypatch):
    """A busy-but-flowing gate must never kill a queued run.

    Two parallel chapter runs exhaust the wait budget while slots keep
    turning over normally; before the movement-anchored deadline, the
    resulting OpenAIQueueTimeoutError silently became a Post-only release
    with no Pre lane (owner report, 2026-08-28).

    Deterministic: the "other run's" handoffs are simulated by stamping
    the shared last-release marker WITHOUT freeing the slot — exactly a
    waiter that keeps losing the (non-FIFO) handoff race — so the waiter
    cannot luck into the slot before its arrival-anchored budget expires.
    Arrival-anchored code raises at 0.4s here; movement-anchored code
    survives to the real release at ~1.0s.
    """
    gate = threading.BoundedSemaphore(1)
    assert gate.acquire(blocking=False)
    monkeypatch.setattr(config, "OPENAI_SLOT_WAIT_TIMEOUT_SECONDS", 0.4)
    monkeypatch.setattr(config, "OPENAI_SLOT_WAIT_QUIET_SECONDS", 0.05)
    monkeypatch.setattr(config, "OPENAI_SLOT_WAIT_LOG_SECONDS", 0.1)
    monkeypatch.setattr(config, "OPENAI_SLOT_WAIT_MAX_SECONDS", 30.0)
    logs: list[str] = []
    monkeypatch.setattr(
        g.progress,
        "log",
        lambda message, **_kwargs: logs.append(str(message)),
    )

    release_at = time.monotonic() + 1.0

    def churn():
        while time.monotonic() < release_at:
            time.sleep(0.15)
            with g._openai_gate_lock:
                g._openai_slot_last_release = time.monotonic()
        gate.release()

    churner = threading.Thread(target=churn, daemon=True)
    churner.start()
    try:
        # Far beyond the 0.4s budget in wall time, yet never 0.4s without
        # queue movement: the waiter must eventually acquire, not raise.
        g._acquire_openai_slot(gate, purpose="concept_mapping")
    finally:
        churner.join(timeout=3)
        gate.release()

    assert any("slot acquired after" in m for m in logs)


def test_a_starved_waiter_still_fails_at_the_absolute_cap(monkeypatch):
    """Movement extends the deadline, but never past the absolute cap.

    Semaphores are not FIFO: a waiter can lose every handoff while churn
    re-anchors its deadline forever. The cap bounds that starvation with
    the same clear, resumable failure instead of a permanently hung run.
    """
    gate = threading.BoundedSemaphore(1)
    assert gate.acquire(blocking=False)
    monkeypatch.setattr(config, "OPENAI_SLOT_WAIT_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(config, "OPENAI_SLOT_WAIT_QUIET_SECONDS", 0.02)
    monkeypatch.setattr(config, "OPENAI_SLOT_WAIT_LOG_SECONDS", 0.05)
    monkeypatch.setattr(config, "OPENAI_SLOT_WAIT_MAX_SECONDS", 0.6)
    logs: list[str] = []
    monkeypatch.setattr(
        g.progress,
        "log",
        lambda message, **_kwargs: logs.append(str(message)),
    )
    stop = threading.Event()

    def churn():
        while not stop.is_set():
            time.sleep(0.05)
            with g._openai_gate_lock:
                g._openai_slot_last_release = time.monotonic()

    churner = threading.Thread(target=churn, daemon=True)
    churner.start()
    started = time.monotonic()
    try:
        with pytest.raises(g.OpenAIQueueTimeoutError, match="waited"):
            g._acquire_openai_slot(gate, purpose="concept_mapping")
    finally:
        stop.set()
        churner.join(timeout=2)
        gate.release()
    # It failed at the cap, not at the (perpetually re-anchored) window.
    assert 0.5 <= time.monotonic() - started <= 5.0


def test_a_wedged_gate_still_times_out(monkeypatch):
    """No movement for the whole window stays a clear, resumable failure."""
    gate = threading.BoundedSemaphore(1)
    assert gate.acquire(blocking=False)
    monkeypatch.setattr(config, "OPENAI_SLOT_WAIT_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(config, "OPENAI_SLOT_WAIT_QUIET_SECONDS", 0.05)
    monkeypatch.setattr(config, "OPENAI_SLOT_WAIT_LOG_SECONDS", 0.05)
    monkeypatch.setattr(g, "_openai_slot_last_release", 0.0)
    logs: list[str] = []
    monkeypatch.setattr(
        g.progress,
        "log",
        lambda message, **_kwargs: logs.append(str(message)),
    )
    try:
        with pytest.raises(g.OpenAIQueueTimeoutError, match="wedged"):
            g._acquire_openai_slot(gate, purpose="concept_mapping")
    finally:
        gate.release()

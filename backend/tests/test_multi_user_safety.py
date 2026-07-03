"""Multi-user safety: shared-API-key concurrency gate, rate-limit backoff,
and SQLite write-concurrency pragmas."""
import threading
import time

import httpx
import pytest
from openai import APITimeoutError, RateLimitError

from app import config
from app.services import generation as g


def _rate_limit_error(retry_after: str | None = None) -> RateLimitError:
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    headers = {"retry-after": retry_after} if retry_after is not None else {}
    response = httpx.Response(429, request=request, headers=headers)
    return RateLimitError("rate limited", response=response, body=None)


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

    def __init__(self, *a, **kw):
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


def test_sqlite_uses_wal_and_busy_timeout():
    from app.db import engine

    with engine.connect() as conn:
        mode = conn.exec_driver_sql("PRAGMA journal_mode").scalar()
        busy = conn.exec_driver_sql("PRAGMA busy_timeout").scalar()
    assert str(mode).lower() == "wal"
    assert int(busy) >= 30000

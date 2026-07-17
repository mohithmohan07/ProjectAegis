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
        # Topic A: two culminations (model duplicated) and one out of order.
        row("A", "Culmination - First"),
        row("A", "Concept A1"),
        row("A", "Culmination - Second"),
        # Topic B: no culmination at all.
        row("B", "Concept B1"),
    ]
    out = g._enforce_culminations(records)
    a_rows = [r for r in out if r["topic"] == "A"]
    b_rows = [r for r in out if r["topic"] == "B"]
    a_culms = [r for r in a_rows if r["concept_title"].startswith("Culmination")]
    b_culms = [r for r in b_rows if r["concept_title"].startswith("Culmination")]
    assert len(a_culms) == 1 and a_rows[-1] is a_culms[0]
    assert a_culms[0]["concept_title"] == "Culmination - Concept A1"
    assert len(b_culms) == 1 and b_rows[-1] is b_culms[0]
    # Normal rows all survive.
    assert [r["concept_title"] for r in a_rows[:-1]] == ["Concept A1"]
    assert [r["concept_title"] for r in b_rows[:-1]] == ["Concept B1"]


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

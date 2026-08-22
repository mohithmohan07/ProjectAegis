"""The run console's stage/lane attribution, pinned.

Every recorded provider call lands in a per-(stage, lane) usage row —
stage from the last ``progress.step`` seen by the calling context, lane
from the composed ``label_scope`` — and stage boundaries refresh the
console's usage event so the time/cost table stays live.
"""
from __future__ import annotations

from app.services import openai_usage, progress


class _Response:
    def __init__(self, model="test-model", prompt=100, completion=40,
                 reasoning=25, cached=0, cache_write=0):
        self.model = model
        self.usage = type("U", (), {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
            "prompt_tokens_details": type("D", (), {
                "cached_tokens": cached, "cache_write_tokens": cache_write,
            })(),
            "completion_tokens_details": type("D", (), {
                "reasoning_tokens": reasoning,
            })(),
        })()


def test_calls_attribute_to_the_active_stage_and_lane():
    with openai_usage.track():
        progress.step("Stage One")
        openai_usage.record_response(_Response(cached=30, cache_write=20))
        with progress.label_scope("Inventory · early track"):
            openai_usage.record_response(_Response(prompt=10, completion=5,
                                                   reasoning=1))
        progress.step("Stage Two")
        openai_usage.record_response(_Response())
        summary = openai_usage.console_summary()
        # Persisted-shape summaries stay EXACTLY as they were: the stage
        # table rides the live console summary only, never a summary a
        # checkpoint bundle or job record stores (strict schemas refuse
        # unknown fields, and free repeats must not move the record).
        assert "stages" not in openai_usage.current_summary()
        assert "stages" not in openai_usage.visible_summary()

    rows = {(row["stage"], row["lane"]): row for row in summary["stages"]}
    assert set(rows) == {
        ("Stage One", ""),
        ("Stage One", "Inventory · early track"),
        ("Stage Two", ""),
    }
    plain = rows[("Stage One", "")]
    assert plain["request_count"] == 1
    assert plain["input_tokens"] == 100
    assert plain["cached_input_tokens"] == 30
    assert plain["cache_write_tokens"] == 20
    assert plain["output_tokens"] == 40
    assert plain["reasoning_tokens"] == 25
    assert plain["first_ts"] > 0 and plain["last_ts"] >= plain["first_ts"]
    lane_row = rows[("Stage One", "Inventory · early track")]
    assert lane_row["total_tokens"] == 15
    assert lane_row["cached_input_tokens"] == 0
    assert lane_row["cache_write_tokens"] == 0
    # Order is first-seen — the console renders stages as they happened.
    assert [row["stage"] for row in summary["stages"]] == [
        "Stage One", "Stage One", "Stage Two"]


def test_a_stage_boundary_refreshes_the_usage_event(monkeypatch):
    seen: list[dict] = []
    monkeypatch.setattr(progress, "usage", lambda data: seen.append(data))
    with openai_usage.track():
        progress.step("Quiet stage")
        assert seen == [], "no usage yet, nothing to refresh"
        openai_usage.record_response(_Response())
        progress.step("Next stage")
    assert seen, "the boundary emitted a usage refresh"
    assert any(data.get("stages") for data in seen)


def test_log_events_carry_their_lane_as_a_structured_field():
    events: list[dict] = []
    token = progress._sink.set(events.append)
    try:
        progress.log("plain line")
        with progress.label_scope("Master · Output 02 (Pre)"):
            progress.log("lane line")
    finally:
        progress._sink.reset(token)
    assert "lane" not in events[0]
    assert events[1]["lane"] == "Master · Output 02 (Pre)"
    assert events[1]["message"].startswith("[Master · Output 02 (Pre)]")

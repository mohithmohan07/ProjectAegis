"""Phase 3.3 settles conflicts in place — no pipeline replay per decision.

Job 15 paid one full Phase-3 replay per conflict: the raise unwound the
whole stack, the orchestrator settled the decision, and generation re-ran
from the top just to apply it — 17 replays, ~2.5 hours, and the next
conflict was only discoverable after the previous replay. With a settler
installed, the conflict is settled where it happened, the fresh resolution
becomes visible to the same attempt, and only the affected topic's host
plan is re-asked.
"""
from __future__ import annotations

import pytest

from app.services import canonical_source_phase33_preflight_contract as phase33
from app.services.semantic_recovery import HumanDecisionRequired


def _packet(decision_id: str = "phase33-host-test") -> dict:
    return {
        "decision_id": decision_id,
        "context_hash": "hash-" + decision_id,
        "kind": "phase33_type_host_semantic_conflict",
        "phase": "3.3",
    }


def _plan_raising_once(calls: dict):
    def plan(**kwargs):
        calls["n"] = calls.get("n", 0) + 1
        if calls["n"] == 1:
            raise HumanDecisionRequired(_packet())
        return {"topic_id": kwargs.get("topic_id"), "assignments": []}

    return plan


def test_a_conflict_is_settled_in_place_and_only_the_topic_is_reasked(
    monkeypatch,
):
    calls: dict = {}
    settled: list[dict] = []
    monkeypatch.setattr(phase33, "_resolve_host_plan", _plan_raising_once(calls))

    def settler(raw_pending: dict) -> list[dict]:
        settled.append(raw_pending)
        return [{
            "decision_id": raw_pending["decision_id"],
            "context_hash": raw_pending["context_hash"],
            "status": "ready",
            "choice": "create_new",
            "resolved_by": "agent",
        }]

    with phase33.human_resolution_context([]):
        with phase33.inline_decision_settler(settler):
            plan = phase33._resolve_host_plan_settling_in_place(
                topic_id="TOPIC-0005", topic={"title": "Visualising the Nation"},
            )
            # The settled resolution is visible to this same attempt.
            visible = phase33._human_resolutions_for({
                "decision_id": "phase33-host-test",
                "context_hash": "hash-phase33-host-test",
            })

    assert plan["topic_id"] == "TOPIC-0005"
    assert calls["n"] == 2  # raised once, re-asked once — no replay
    assert [p["decision_id"] for p in settled] == ["phase33-host-test"]
    assert visible and visible[0]["choice"] == "create_new"


def test_companion_decisions_settle_in_the_same_round(monkeypatch):
    calls: dict = {}

    def plan(**kwargs):
        calls["n"] = calls.get("n", 0) + 1
        if calls["n"] == 1:
            raise HumanDecisionRequired(
                _packet("lead"), companions=(_packet("second"),),
            )
        return {"assignments": []}

    monkeypatch.setattr(phase33, "_resolve_host_plan", plan)
    settled: list[str] = []

    def settler(raw_pending: dict) -> list[dict]:
        settled.append(raw_pending["decision_id"])
        return []

    with phase33.inline_decision_settler(settler):
        phase33._resolve_host_plan_settling_in_place(
            topic_id="T", topic={},
        )

    assert settled == ["lead", "second"]
    assert calls["n"] == 2


def test_without_a_settler_the_replay_path_is_unchanged(monkeypatch):
    calls: dict = {}
    monkeypatch.setattr(phase33, "_resolve_host_plan", _plan_raising_once(calls))

    with pytest.raises(HumanDecisionRequired):
        phase33._resolve_host_plan_settling_in_place(topic_id="T", topic={})

    assert calls["n"] == 1


def test_settler_failures_propagate_instead_of_spinning(monkeypatch):
    """Budget ceilings raised by the settler end the round immediately."""
    calls: dict = {}
    monkeypatch.setattr(phase33, "_resolve_host_plan", _plan_raising_once(calls))

    def exhausted(raw_pending: dict) -> list[dict]:
        raise RuntimeError("issue pathway budget exhausted")

    with phase33.inline_decision_settler(exhausted):
        with pytest.raises(RuntimeError, match="budget exhausted"):
            phase33._resolve_host_plan_settling_in_place(topic_id="T", topic={})

    assert calls["n"] == 1

"""A settled decision stays settled — across replays AND across resumes.

The run in job 18 settled twelve 3.2 topology decisions, lost the network,
resumed — and re-litigated them one full replay apiece with identical issue
ids, because consumed agent resolutions were only reactivated for Phase 3.3.
Every agent-settled semantic resolution of the three conflict kinds is now
re-exposed to every attempt, so a resume can never forget an answer.
"""
from __future__ import annotations

from app.services import build_concepts
from app.services import canonical_source_phase33_preflight_contract as phase33


def _checkpoint_with(kind: str, status: str = "consumed") -> dict:
    return {
        "human_decisions": {
            "resolutions": [{
                "status": status,
                "choice": "accept_recommended",
                "resolved_by": "agent",
                "pending_decision": {
                    "decision_id": f"{kind}-decision",
                    "context_hash": f"{kind}-context",
                    "kind": kind,
                },
            }],
        },
    }


def _active_rows() -> list:
    value = phase33._HUMAN_DECISION_RESOLUTIONS.get()
    if not value:
        return []
    return list(value) if isinstance(value, list) else [value]


def test_consumed_phase32_agent_resolution_survives_a_resume():
    checkpoint = _checkpoint_with("phase32_concept_blueprint_semantic_conflict")

    with build_concepts._human_decision_resolution_context(checkpoint):
        rows = _active_rows()

    assert any(
        row.get("decision_id")
        == "phase32_concept_blueprint_semantic_conflict-decision"
        and row.get("status") == "ready"
        for row in rows
    )


def test_consumed_phase31_agent_resolution_survives_a_resume():
    checkpoint = _checkpoint_with("phase31_source_grounding_semantic_conflict")

    with build_concepts._human_decision_resolution_context(checkpoint):
        rows = _active_rows()

    assert any(
        row.get("status") == "ready"
        and row.get("kind") == "phase31_source_grounding_semantic_conflict"
        for row in rows
    )


def test_consumed_human_resolutions_are_not_blanket_reactivated():
    """Only agent-settled rows re-arm; a human's consumed answer stays spent."""
    checkpoint = _checkpoint_with("phase32_concept_blueprint_semantic_conflict")
    row = checkpoint["human_decisions"]["resolutions"][0]
    row["resolved_by"] = "human"

    with build_concepts._human_decision_resolution_context(checkpoint):
        rows = _active_rows()

    assert not any(r.get("status") == "ready" for r in rows)

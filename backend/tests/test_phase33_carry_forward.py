"""Phase 3.3 must see a carried decision, and must not enforce it.

Every phase's saved-resolution lookup privately lists the choices it accepts,
and Phase 3.3's copy was the last one that still omitted ``carry_forward``.
The cost is not cosmetic: when a Type-host scope exhausts its repair budget,
the orchestrator settles it by carrying -- and a lookup that drops the carried
row makes the answer invisible, so the identical decision is raised again,
refused a second carry, and the ceiling ends the run one phase short of Type
allocation.
"""
from __future__ import annotations

from app.services import (
    canonical_source_phase33_preflight_contract as phase33,
)


def _carried_row(unit_id: str = "TYPE-0002::CASE-0010::0003") -> dict:
    return {
        "decision_id": "phase33-host-carrytest0001",
        "context_hash": "a" * 64,
        "choice": "carry_forward",
        "instruction": "",
        "target_id": "",
        "target_concept_id": "",
        "status": "consumed",
        "item": {"unit_id": unit_id},
    }


def test_lookup_accepts_a_carried_resolution():
    """The filter that made carried answers invisible now passes them."""

    identity = {
        "decision_id": "phase33-host-carrytest0001",
        "context_hash": "a" * 64,
    }
    with phase33.human_resolution_context([_carried_row()]):
        rows = phase33._human_resolutions_for(identity)

    assert [row["choice"] for row in rows] == ["carry_forward"]
    # The settled unit is recorded, so it is not raised again.
    assert phase33._resolution_unit_ids(rows[0])


def test_a_carried_row_directs_nothing():
    """``_directed_resolution_issues`` must never enforce a carry.

    A directed create_new that the provider ignores re-raises the decision --
    that loop is what exhausts the budget in the first place. The carry that
    follows must not restart it: there is no direction to enforce.
    """

    issues = phase33._directed_resolution_issues(
        _carried_row(),
        plan={"assignments": []},
        units=[{"assignment_unit_id": "TYPE-0002::CASE-0010::0003"}],
        concepts=[],
    )

    assert issues == []

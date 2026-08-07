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


def test_host_candidates_are_bound_and_selectable():
    """Every Phase 3.3 host candidate carries a verifiable server seal.

    Job 14 died after 25 repair rounds on one Type/Case unit. The audited
    resolver reasons say why nothing could ever work: every catalog row was
    ``resolver_legacy`` -- no target_id, no text_sha256, no binding_hash --
    and with ``legacy_exact_source_matches`` empty the integrity rule made
    every existing concept unselectable. The correct host sat in the list the
    whole time; the resolver was structurally forbidden from choosing it and
    could only guess between wrong expansions and create_new plans that then
    failed block validation.
    """

    from app import schemas
    from app.services import early_semantic_gate as early_gate

    pending = phase33._host_pending_decision(
        identity={
            "decision_id": "phase33-host-boundcand0001",
            "context_hash": "b" * 64,
        },
        topic={"topic_id": "TOPIC-0003", "title": "The Age of Revolutions"},
        units=[{
            "assignment_unit_id": "TYPE-0002::CASE-0010::0003",
            "type_id": "TYPE-0002",
            "type_title": "Explain the role of culture",
            "source_question_ids": ["QINV-0006"],
        }],
        concepts=[{
            "concept_id": "HOST-CONCEPT-0026",
            "concept_title": "Folk culture and Volksgeist as nation-building",
            "topic": "The Age of Revolutions",
            "source_claim": (
                "Romantic artists promoted folk culture as the true spirit "
                "of the nation."
            ),
            "_source_block_ids": ["BLK-00120", "BLK-00121"],
        }],
        source_blocks=[],
        issues=["No safe Type host was certified."],
    )

    rows = pending["candidates"]
    assert rows, "the catalog must not be empty"
    for row in rows:
        # Schema-clean: extra keys would be rejected at persist time.
        schemas.SemanticDecisionCandidate.model_validate(row)
        # Selectable: a generic target identity and a verifiable seal.
        assert row["target_id"] == row["concept_id"]
        assert row["binding_hash"]
        assert early_gate.candidate_binding_is_valid(row)
        assert row["source_block_ids"] == ["BLK-00120", "BLK-00121"]
        assert row["text_sha256"]

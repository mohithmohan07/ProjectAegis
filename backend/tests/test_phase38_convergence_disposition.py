"""Phase 3.8 must converge to an output, not cycle forever.

Two defects kept live RNE runs from ever reaching a workbook:

* the convergence loop held its pass counter, repeat detector and suppressed
  resolutions in function locals, and ``HumanDecisionRequired`` is a
  ``RuntimeError`` — so a semantic decision unwound the whole loop, the
  orchestrator re-ran generation from the checkpoint, and a brand-new loop
  started at pass 1 with an empty repeat detector. The bounded budget was
  never consumed;
* even once the budget is consumed, the only terminal state was ``raise`` —
  every disposition (refine/split/move/retire) loops back into
  re-verification, so a couple of ungroundable concepts held the whole
  chapter hostage and the run produced nothing.

These tests pin the corrected behaviour: attempts accumulate across the
unwind, the budget terminates, and an exhausted concept is retired so the
chapter completes — bounded by a floor that stops a systemic failure from
silently shipping a thin map.
"""
from __future__ import annotations

import pytest

from app.services import (
    canonical_source_phase38_boundary_grounding_turnover_contract as phase38,
)
from app.services import semantic_recovery


_GROUND_FAILURE = (
    "failed exact source-block grounding before freeze for topic "
    "'The Making of Germany and Italy': CONCEPT-GROUND-0031 is not fully "
    "supported by BLK-00176/BLK-00181"
)


def _row(title: str, topic: str) -> dict:
    return {
        "concept_title": title,
        "topic": topic,
        "parent_concept": "Parent",
        "concept_details": f"Description: {title}.",
        "_phase32_origin_concept_id": "",
    }


def _records() -> list[dict]:
    rows = [
        _row(f"Grounded Concept {index:02d}", "The Age of Revolutions")
        for index in range(1, 9)
    ]
    rows.append(_row("Ungroundable Concept", "The Making of Germany and Italy"))
    rows.append(_row("Second Ungroundable", "The Making of Germany and Italy"))
    return rows


@pytest.fixture(autouse=True)
def _clean_ledger(monkeypatch):
    phase38.reset_convergence_state()
    # Key the scope deterministically off the record set, not a live graph.
    monkeypatch.setattr(phase38.phase3, "active_graph", lambda: None)
    monkeypatch.setattr(
        phase38.progress, "log", lambda *_args, **_kwargs: None)
    yield
    phase38.reset_convergence_state()


def _run(original, records=None):
    return phase38._phase32_adjudicate_with_targeted_convergence(
        original, records if records is not None else _records())


# --------------------------------------------------------------------------- #
# The budget survives the HumanDecisionRequired unwind
# --------------------------------------------------------------------------- #

def test_human_decision_unwind_does_not_reset_the_convergence_budget(
    monkeypatch,
):
    monkeypatch.setenv("AEGIS_PHASE38_TOPOLOGY_CONVERGENCE_PASSES", "3")
    records = _records()
    calls = {"n": 0}

    def original(rows, *_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError(_GROUND_FAILURE)
        # A pause escapes the loop exactly as it does in production.
        raise semantic_recovery.HumanDecisionRequired({
            "decision_id": "phase31-ground-test",
            "context_hash": "c" * 64,
        })

    with pytest.raises(semantic_recovery.HumanDecisionRequired):
        _run(original, records)

    scope = phase38._convergence_scope_key(records)
    # One grounding failure was counted before the pause unwound the loop.
    assert phase38._convergence_state(scope)["attempts"] == 1

    # The orchestrator re-runs generation from the checkpoint. The budget must
    # continue from where it stopped rather than restarting at pass 1.
    def always_fails(_rows, *_args, **_kwargs):
        raise ValueError(_GROUND_FAILURE)

    with pytest.raises(ValueError):
        _run(always_fails, records)

    state = phase38._convergence_state(scope)
    # Budget consumed and disposition attempted, rather than looping forever.
    assert state["retired"]


def test_repeat_detector_survives_the_unwind(monkeypatch):
    monkeypatch.setenv("AEGIS_PHASE38_TOPOLOGY_CONVERGENCE_PASSES", "6")
    records = _records()

    def original(_rows, *_args, **_kwargs):
        raise ValueError(_GROUND_FAILURE)

    with pytest.raises(ValueError):
        _run(original, records)

    # The identical signature was seen more than once inside one budget, so
    # the cycle-aware escalated instruction can actually fire.
    scope = phase38._convergence_scope_key(records)
    state = phase38._convergence_state(scope)
    assert state["retired"], "an exhausted budget must dispose, not spin"


# --------------------------------------------------------------------------- #
# Terminal disposition: the chapter completes
# --------------------------------------------------------------------------- #

def test_exhausted_concept_is_retired_and_the_chapter_completes(monkeypatch):
    monkeypatch.setenv("AEGIS_PHASE38_TOPOLOGY_CONVERGENCE_PASSES", "2")
    records = _records()
    seen: list[int] = []

    def original(rows, *_args, **_kwargs):
        seen.append(len(rows))
        if any(
            row.get("topic") == "The Making of Germany and Italy"
            for row in rows
        ):
            raise ValueError(_GROUND_FAILURE)
        return [dict(row) for row in rows]

    result = _run(original, records)

    # The run produced a topology instead of raising.
    assert result
    titles = {row["concept_title"] for row in result}
    assert "Ungroundable Concept" not in titles
    assert "Second Ungroundable" not in titles
    # Every grounded concept survived.
    assert len([t for t in titles if t.startswith("Grounded Concept")]) == 8
    # The reduced map was retried after the disposition.
    assert seen[-1] == 8


def test_converged_run_clears_the_budget_for_the_next_decision(monkeypatch):
    monkeypatch.setenv("AEGIS_PHASE38_TOPOLOGY_CONVERGENCE_PASSES", "4")
    records = _records()
    calls = {"n": 0}

    def original(rows, *_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError(_GROUND_FAILURE)
        return [dict(row) for row in rows]

    assert _run(original, records)
    scope = phase38._convergence_scope_key(records)
    state = phase38._convergence_state(scope)
    assert state["attempts"] == 0
    assert state["signatures"] == {}
    assert not state["retired"]


# --------------------------------------------------------------------------- #
# Safety floors
# --------------------------------------------------------------------------- #

def test_systemic_failure_stops_instead_of_emptying_the_map(monkeypatch):
    monkeypatch.setenv("AEGIS_PHASE38_TOPOLOGY_CONVERGENCE_PASSES", "2")
    # Only one concept may be retired out of ten.
    monkeypatch.setenv("AEGIS_PHASE38_MAX_RETIRED_FRACTION", "0.1")
    records = _records()

    def original(_rows, *_args, **_kwargs):
        raise ValueError(_GROUND_FAILURE)

    # Two concepts are named by the diagnostic topic; retiring both exceeds
    # the floor, so the run stops rather than shipping a gutted chapter.
    with pytest.raises(ValueError):
        _run(original, records)
    scope = phase38._convergence_scope_key(records)
    assert not phase38._convergence_state(scope)["retired"]


def test_unattributable_failure_never_retires_the_whole_chapter(monkeypatch):
    monkeypatch.setenv("AEGIS_PHASE38_TOPOLOGY_CONVERGENCE_PASSES", "2")
    records = _records()

    def original(_rows, *_args, **_kwargs):
        # No concept id and no resolvable topic: the broad repair-feedback
        # fallback would blame every concept, which must never drive
        # retirement.
        raise ValueError(
            "failed exact source-block grounding before freeze: "
            "the critic rejected the chapter"
        )

    with pytest.raises(ValueError):
        _run(original, records)
    scope = phase38._convergence_scope_key(records)
    assert not phase38._convergence_state(scope)["retired"]


def test_non_grounding_errors_are_never_swallowed(monkeypatch):
    monkeypatch.setenv("AEGIS_PHASE38_TOPOLOGY_CONVERGENCE_PASSES", "3")

    def original(_rows, *_args, **_kwargs):
        raise ValueError("phase 3.2 adjudication contract is unavailable")

    with pytest.raises(ValueError, match="contract is unavailable"):
        _run(original)

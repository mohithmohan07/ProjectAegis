"""Regressions for task-bearing support routing under the language plan."""
from __future__ import annotations

from app.services import postlearning_formation_contract as post
from app.services import postlearning_support_contract as support
from app.services import postlearning_support_routing_contract as routing
from app.services.phase3 import place, polish
from tests import test_postlearning_support_contract as fixtures


def _row(rows, plan_id):
    return next(
        row for row in rows
        if (
            (row.get(post.PLAN_IDENTITY_FIELD) or {}).get("plan_concept_id")
            == plan_id
        )
    )


def test_task_support_uses_activity_renderer_while_non_task_support_stays_whole():
    env = support.prepare_envelope(fixtures._raw_env())
    attached = support.attach_support(env, env["skeleton_rows"])
    cleaned = routing.strip_task_support_bodies(env, attached)
    opening = _row(cleaned, "PC-1")
    details = opening["concept_details"]

    assert "Think and write.\nNew things I want to learn this year." not in details
    assert (
        "Alliteration is a figure of speech where the same initial "
        "consonant sound is repeated in nearby words."
    ) in details
    assert details.count("Activity/Info Hub:") == 1
    # The audit still records both source occurrences even though the task's
    # public note is rendered later from its QID.
    assert [
        row["block_id"] for row in opening[support.SUPPORT_FIELD]
    ] == ["BLK-WARM", "BLK-DEVICE"]


def test_place_result_is_overridden_only_for_the_planned_task_hub():
    env = support.prepare_envelope(fixtures._raw_env())
    rows = env["skeleton_rows"]
    concept_ids = place.mint_concept_ids(rows)
    opening_id = next(
        concept_id for concept_id, row in zip(concept_ids, rows)
        if (
            (row.get(post.PLAN_IDENTITY_FIELD) or {}).get("plan_concept_id")
            == "PC-1"
        )
    )
    original = {
        "hub_placements": {"Q-WARM": "SOME-OTHER-CONCEPT"},
        "figure_placements": {},
        "rationales": {"Q-WARM": "ordinary Place draft"},
        "review_flags": {},
    }
    result = routing.enforce_place_result(env, rows, original)

    assert routing.planned_hub_qids(env) == {"Q-WARM": "PC-1"}
    assert result["hub_placements"]["Q-WARM"] == opening_id
    assert "language topology plan threaded" in result["rationales"]["Q-WARM"]
    assert "Q-DEVICE" not in result["hub_placements"]
    assert original["hub_placements"]["Q-WARM"] == "SOME-OTHER-CONCEPT"


def test_install_wraps_place_and_post_support_polish_once():
    routing.install()
    place_once = place.place
    polish_once = polish.polish
    routing.install()

    assert place.place is place_once
    assert polish.polish is polish_once
    assert getattr(place.place, "_aegis_postlearning_support_route", False)
    assert getattr(polish.polish, "_aegis_postlearning_support_dedupe", False)


def test_a_double_claimed_hub_qid_goes_to_the_fixer_not_down(monkeypatch):
    """§8.2/Q13: a decidable mid-run block gets ONE recorded decision.

    [measured] job "The School Bell Rings Again..." (owner report,
    2026-08-29): two threading verdicts claimed the same task-bearing
    support occurrence and the unconditional raise ended the run
    incomplete at Place — Outputs 02/04 unbuildable. With a Fixer and
    store in scope the conflict is now resolved by one recorded,
    flagged decision and the run continues; without them the block
    still raises exactly as before.
    """
    import pytest

    from app.services.phase3 import kernel

    env = support.prepare_envelope(fixtures._raw_env())
    rows = env["skeleton_rows"]

    # The production shape: two DIFFERENT threaded blocks, two different
    # destinations, one shared task id — one Hub QID claimed twice.
    def conflicted_records(_env):
        return {
            "PC-1": [{
                "block_id": "BLK-WARM",
                "text": "Think and write.",
                "placement_context": "opening_pre_reading",
                "skill": "personal response",
                "rationale": "opening claim",
                "task_ids": ["TASK-WARM"],
            }],
            "PC-3": [{
                "block_id": "BLK-QUESTION",
                "text": "Find more examples of Alliteration from the poem.",
                "placement_context": "contextual_support",
                "skill": "device practice",
                "rationale": "second claim",
                "task_ids": ["TASK-WARM"],
            }],
        }

    monkeypatch.setattr(support, "support_records", conflicted_records)
    fixer_requests = []

    def fixer(request):
        fixer_requests.append(request)
        candidates = [
            row["plan_concept_id"] for row in request["candidates"]
        ]
        assert candidates == ["PC-1", "PC-3"]
        return {
            "destination_plan_id": "PC-1",
            "rationale": "The warm-up belongs with the opening stanza.",
        }

    original = {
        "hub_placements": {"Q-WARM": "SOME-OTHER-CONCEPT"},
        "figure_placements": {},
        "rationales": {"Q-WARM": "ordinary Place draft"},
        "review_flags": {},
    }
    result = routing.enforce_place_result(
        env, rows, original,
        store=kernel.DecisionStore(), fixer=fixer,
    )

    assert len(fixer_requests) == 1
    concept_ids = place.mint_concept_ids(rows)
    opening_id = next(
        concept_id for concept_id, row in zip(concept_ids, rows)
        if (
            (row.get(post.PLAN_IDENTITY_FIELD) or {}).get("plan_concept_id")
            == "PC-1"
        )
    )
    assert result["hub_placements"]["Q-WARM"] == opening_id
    assert any(
        "hub-route-conflict" in flag
        for flag in result["review_flags"]["Q-WARM"]
    )

    # The PRODUCTION shape ([measured] 2026-08-29, second occurrence):
    # callers pass fixer=None — the runner's providers dict is test-only
    # injection — and the wrapper must resolve the deployment's own
    # Fixer instead of taking the fail-closed raise on a live run.
    monkeypatch.setattr(
        "app.services.phase3.fixer.default_provider", lambda: fixer,
    )
    live_result = routing.enforce_place_result(env, rows, original)
    assert live_result["hub_placements"]["Q-WARM"] == opening_id
    assert len(fixer_requests) == 2

    # A dry/test deployment (no live Fixer) still raises exactly as before.
    monkeypatch.setattr(
        "app.services.phase3.fixer.default_provider", lambda: None,
    )
    with pytest.raises(ValueError, match="one activity identity"):
        routing.enforce_place_result(env, rows, original)
    with pytest.raises(ValueError, match="one activity identity"):
        routing.planned_hub_qids(env)

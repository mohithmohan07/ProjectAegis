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

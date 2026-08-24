"""Job 78 regression: Q19 must not create a second source-task authority.

The Balbharati Grade-6 poem run failed after Phase 2 had already recorded the
Alliteration definition/example as context for QINV-0012.  Q19's early
inventory worker bypassed the installed Phase-2 extractor and re-read raw
sections; its completeness reviewer then promoted that context paragraph into
a separate inventory item and killed the run at the 55% checkpoint.

These tests pin the architectural rule rather than the English chapter name:
when an ACSD is active, its task/context ledger is authoritative through the
early-track join and no downstream inventory-extraction provider call occurs.
"""
from __future__ import annotations

from app.services import canonical_source_phase2 as phase2
from app.services import canonical_source_phase2_contract as contract
from app.services import generation as g


def _canonical_alliteration_case() -> dict:
    context = (
        "Alliteration is a figure of speech where the same initial consonant "
        "sound is repeated in a series of nearby words. "
        "e.g. ‘A year in which we will all take part.’"
    )
    return {
        "source_contract": {
            "mode": phase2.SOURCE_CONTRACT_MODE,
            "source_reader": "job78-regression",
        },
        "tasks": [{
            "task_id": "TASK-00012",
            "qid": "QINV-0012",
            "order": 12,
            "source_kind": "checkpoint_question",
            "source_label": "Task",
            "raw_prompt": "6. Find more examples of Alliteration from the poem.",
            "display_prompt": "6. Find more examples of Alliteration from the poem.",
            "requires_context": True,
            "shared_context": context,
            "content_objects": {
                "shared_context_blocks": [{
                    "block_id": "BLK-00065",
                    "kind": "paragraph",
                    "display_text": context,
                }],
            },
            "source_section_index": 14,
            "source_position": 9,
            "identity_key": "job78-qinv-0012",
        }],
    }


def _provider_must_not_run(*_args, **_kwargs):  # pragma: no cover
    raise AssertionError(
        "an active Phase-2 ACSD must not spend a second inventory extraction"
    )


def test_q19_early_track_reuses_acsd_task_context_without_provider(monkeypatch):
    contract.install()
    monkeypatch.setattr(g, "_openai_json", _provider_must_not_run)

    canonical = _canonical_alliteration_case()
    with phase2.activate(canonical):
        track = g._EarlyInventoryTrack(meta={}, sections=[{
            "heading": "Poetic Device",
            "body": "Alliteration definition followed by Task 6",
        }])
        pre_joined, anchors = track.join()
        track.halt()

        assert anchors == []
        assert [row["qid"] for row in pre_joined["items"]] == ["QINV-0012"]
        item = pre_joined["items"][0]
        assert item["raw_task"] == (
            "6. Find more examples of Alliteration from the poem."
        )
        assert "Alliteration is a figure of speech" in item["shared_context"]
        assert item["content_objects"]["shared_context_blocks"][0][
            "block_id"
        ] == "BLK-00065"


def test_q19_join_preserves_canonical_membership_and_only_finishes_mechanics(
    monkeypatch,
):
    contract.install()
    monkeypatch.setattr(g, "_openai_json", _provider_must_not_run)

    canonical = _canonical_alliteration_case()
    with phase2.activate(canonical):
        inventory = phase2.inventory_from_canonical(canonical)
        finished = g._finish_inventory_with_topics(
            inventory,
            [],
            meta={},
            sections=[],
            records=[],
        )

    assert [row["qid"] for row in finished["items"]] == ["QINV-0012"]
    assert "Alliteration is a figure of speech" in (
        finished["items"][0]["shared_context"]
    )
    assert "_topic_scope" not in finished["items"][0]
    assert finished["stats"]["total_inventory_items"] == 1

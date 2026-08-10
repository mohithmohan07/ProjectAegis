"""Polish: only failing rows are repaired, and only their content."""
from __future__ import annotations

import copy

import pytest

from app.services.phase3 import envelope as envelope_mod
from app.services.phase3 import kernel
from app.services.phase3 import polish as polish_mod

_SOURCE = (
    "The French Revolution proclaimed that it was the people who would "
    "henceforth constitute the nation and shape its destiny through "
    "collective participation in political life across France."
)


def _env() -> dict:
    return envelope_mod.build(
        graph={
            "source_contract_hash": "S-1",
            "topics": [{"topic_id": "T-1", "title": "Topic"}],
            "subtopics": [],
            "blocks": [{"block_id": "B-1", "topic_id": "T-1"}],
        },
        canonical={
            "blocks": [{"block_id": "B-1", "display_text": _SOURCE}]
        },
        skeleton_rows=[{"concept_title": "A concept"}],
        inventory={"items": []},
        mined_types={"types": []},
    )


def _clean_row() -> dict:
    return {
        "topic": "Topic",
        "parent_concept": "Ideas",
        "concept_title": "The nation as a people's project",
        "concept_details": (
            "Description: Learners see how ordinary citizens, not "
            "monarchs, came to define political belonging.\n"
            "Achieving Mastery: Explaining who constitutes a nation in "
            "revolutionary thought. // Misconception/ Error Analysis: "
            "Misconceptions: Learners believe monarchs granted "
            "nationhood as a favour.; Error Analysis: The learner "
            "attributes the 1789 constitution to royal initiative "
            "instead of popular sovereignty."
        ),
        "keywords": "nation, sovereignty",
        "_semantic_topic_id": "T-1",
        "_source_block_ids": ["B-1"],
    }


def _verbatim_row() -> dict:
    row = _clean_row()
    row["concept_title"] = "Popular sovereignty after 1789"
    row["concept_details"] = (
        "Description: " + _SOURCE + " // Misconception/ Error Analysis: "
        "Misconceptions: Learners believe monarchs granted nationhood "
        "as a favour.; Error Analysis: The learner attributes the 1789 "
        "constitution to royal initiative instead of popular "
        "sovereignty."
    )
    return row


_REPAIRED_DETAILS = (
    "Description: After 1789 the people themselves, acting together in "
    "political life, became the makers of the nation's future.\n"
    "Achieving Mastery: Explaining who constitutes a nation in "
    "revolutionary thought. // Misconception/ Error Analysis: "
    "Misconceptions: Learners believe monarchs granted nationhood as a "
    "favour.; Error Analysis: The learner attributes the 1789 "
    "constitution to royal initiative instead of popular sovereignty."
)


def test_clean_rows_pass_through_without_any_provider():
    rows = [_clean_row()]
    out = polish_mod.polish(_env(), copy.deepcopy(rows), provider=None)
    assert out == rows  # no failures -> no live API demanded, no changes


def test_only_the_failing_rows_content_is_repaired():
    rows = [_clean_row(), _verbatim_row()]
    calls: list[dict] = []

    def provider(request: dict) -> dict:
        calls.append(request)
        return {"rows": [
            {
                "row_ref": row["row_ref"],
                "concept_title": row["concept_title"],
                "concept_details": _REPAIRED_DETAILS,
                "keywords": "nation, sovereignty, participation",
            }
            for row in request["rows"]
        ]}

    out = polish_mod.polish(
        _env(), copy.deepcopy(rows), provider=provider,
        store=kernel.DecisionStore(),
    )

    assert len(calls) == 1
    assert [row["row_ref"] for row in calls[0]["rows"]] == [1]
    assert calls[0]["rows"][0]["validation_errors"]
    assert calls[0]["rows"][0]["source_blocks"][0]["block_id"] == "B-1"
    # The clean row is untouched; the failing row keeps its identity.
    assert out[0] == rows[0]
    assert out[1]["concept_details"] == _REPAIRED_DETAILS
    assert out[1]["concept_title"] == rows[1]["concept_title"]
    assert out[1]["_source_block_ids"] == ["B-1"]
    assert out[1]["keywords"] == "nation, sovereignty, participation"


def test_a_repair_that_still_fails_the_gate_fails_closed():
    rows = [_verbatim_row()]
    calls = 0

    def provider(request: dict) -> dict:
        nonlocal calls
        calls += 1
        return {"rows": [
            {
                "row_ref": row["row_ref"],
                "concept_title": row["concept_title"],
                # Still verbatim: the checker must refuse it.
                "concept_details": "Description: " + _SOURCE,
            }
            for row in request["rows"]
        ]}

    with pytest.raises(kernel.ContractError) as failed:
        polish_mod.polish(
            _env(), rows, provider=provider, store=kernel.DecisionStore(),
        )
    assert calls == 3
    assert "verbatim_source_description" in str(failed.value)


def test_polish_decisions_replay_free_from_the_store(tmp_path):
    rows = [_verbatim_row()]
    calls = 0

    def provider(request: dict) -> dict:
        nonlocal calls
        calls += 1
        return {"rows": [
            {
                "row_ref": row["row_ref"],
                "concept_title": row["concept_title"],
                "concept_details": _REPAIRED_DETAILS,
            }
            for row in request["rows"]
        ]}

    store = kernel.DecisionStore(tmp_path)
    first = polish_mod.polish(
        _env(), copy.deepcopy(rows), provider=provider, store=store,
    )
    second = polish_mod.polish(
        _env(), copy.deepcopy(rows), provider=provider,
        store=kernel.DecisionStore(tmp_path),
    )
    assert calls == 1
    assert second == first

"""Regression coverage for post-freeze Activity/Info Hub convergence."""
from __future__ import annotations

from app.services import canonical_source_phase2 as phase2
from app.services import canonical_source_phase21 as phase21
from app.services import canonical_source_phase21_render as phase21_render
from app.services import canonical_source_phase39_post_freeze_hub_convergence_contract as phase39
from app.services import generation as g


TOPIC = "The Making of Nationalism in Europe"


def _record(index: int) -> dict:
    return {
        "topic": TOPIC,
        "parent_concept": "Source-grounded classroom work",
        "concept_title": f"Activity Host {index:02d}",
        "concept_details": (
            "Description: A source-grounded classroom task helps the learner "
            "interpret historical evidence in context.\n"
            "Achieving Mastery: The learner completes the assigned source task "
            "and explains the evidence used. // "
            "Misconception/ Error Analysis: Misconceptions: The learner may "
            "believe the activity is detached from the historical topic.; "
            "Error Analysis: The learner may record an observation without "
            "linking it to the supplied evidence."
        ),
        "keywords": "activity, source evidence, historical interpretation",
    }


def _activity(index: int, *, generic_label: bool) -> dict:
    label = "Activity" if generic_label else f"Activity {index}.1"
    return {
        "qid": f"QINV-ACT-{index:04d}",
        "source_kind": "activity",
        "source_label": label,
        "parent_source_label": label,
        "topic_hint": TOPIC,
        "raw_task": (
            f"Compare source visual {index} with the accompanying passage, "
            f"record two observations, and explain conclusion {index}."
        ),
    }


def _fixture() -> tuple[list[dict], dict, dict, list[int]]:
    records = [_record(index) for index in range(1, 9)]
    items = [
        _activity(index, generic_label=index <= 8)
        for index in range(1, 11)
    ]
    # Ten source-owned activities are hosted on eight independently certified
    # normal concepts, matching the live RNE failure shape (hub_contract=8).
    host_indices = [0, 1, 2, 3, 4, 5, 6, 7, 0, 1]
    inventory = {"items": items, "stats": {}}
    mined = {"types": []}
    g._reset_placement_certifications(mined)
    for item, host_index in zip(items, host_indices):
        g._certify_inventory_host(
            mined,
            item["qid"],
            records[host_index],
            basis="phase39-regression",
        )
    return records, inventory, mined, host_indices


def _phase21_canonical() -> dict:
    return {
        "phase21_hardening": {
            "version": phase21.HARDENING_VERSION,
        }
    }


def test_generic_activity_note_matches_the_phase21_shipped_wire_text():
    item = _activity(1, generic_label=True)

    legacy = g._PHASE39_ORIGINAL_COMPACT_HUB_NOTE(item)
    canonical = g._compact_activity_hub_note(item)

    assert "Activity — Activity —" in legacy
    assert "Activity — Activity —" not in canonical
    assert canonical == phase21_render.clean_activity_hub_content(legacy)
    assert canonical == phase21_render.clean_activity_hub_content(canonical)
    assert not g.kr.rich_text_issues(canonical)


def test_ten_rne_like_activity_items_remain_exact_after_phase21_rendering():
    records, inventory, mined, host_indices = _fixture()

    with phase2.activate(_phase21_canonical()):
        normalized = g._normalize_activity_hubs_from_inventory(
            records,
            inventory,
            mined,
        )

    assert g._hub_inventory_contract_violations(normalized, inventory) == []
    placed_qids = [
        qid
        for row in normalized
        for qid in row.get("_activity_hub_qids") or []
    ]
    assert sorted(placed_qids) == sorted(
        item["qid"] for item in inventory["items"]
    )

    for record_index, row in enumerate(normalized):
        expected = " ".join(
            g._compact_activity_hub_note(item)
            for item, host_index in zip(inventory["items"], host_indices)
            if host_index == record_index
        )
        actual = g.cr.activity_hub_body(row["concept_details"])
        assert g._inventory_comparison_text(actual) == (
            g._inventory_comparison_text(expected)
        )


def test_post_freeze_validation_reconverges_after_a_second_phase21_render():
    records, inventory, mined, _host_indices = _fixture()
    validator_calls: list[int] = []

    def simulated_phase21_validator(rows, *_args, **_kwargs):
        validator_calls.append(1)
        # The real Phase 2.1 wrapper performs this transformation immediately
        # before the closed inventory gate.
        rows[:] = phase21_render.normalize_final_records(
            g,
            rows,
            inventory,
            mined,
        )
        assert g._hub_inventory_contract_violations(rows, inventory) == []
        return "validated"

    with phase2.activate(_phase21_canonical()):
        result = phase39._validate_with_hub_convergence(
            g,
            simulated_phase21_validator,
            records,
            (),
            {
                "stage": "post-freeze allocation",
                "inventory": inventory,
                "mined_types": mined,
            },
        )

    assert result == "validated"
    assert validator_calls == [1]
    assert g._hub_inventory_contract_violations(records, inventory) == []

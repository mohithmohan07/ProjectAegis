"""Focused compatibility tests for the Phase 2 ACSD source boundary."""
from __future__ import annotations

from app.services import canonical_source_phase2 as phase2
from app.services import generation


def test_repeated_label_cannot_enrich_a_task_from_another_section():
    task = {
        "raw_prompt": "A prompt whose exact wording is not present in anchors.",
        "source_label": "Question 1",
        "source_section_index": 2,
        "source_position": 10,
    }
    wrong_section = {
        "raw_task": "A completely different prompt in the earlier topic.",
        "source_label": "Question 1",
        "_source_section_index": 1,
        "_source_position": 10,
    }
    same_section = {
        "raw_task": "A completely different prompt in the intended topic.",
        "source_label": "Question 1",
        "_source_section_index": 2,
        "_source_position": 12,
    }

    assert phase2._match_anchor(task, [wrong_section], set()) is None
    match = phase2._match_anchor(task, [wrong_section, same_section], set())
    assert match is not None
    assert match[0] == 1
    assert match[1] is same_section


def test_exact_task_text_remains_stronger_than_section_label_metadata():
    task = {
        "raw_prompt": "Explain how the customs union supported economic unity.",
        "source_label": "Discuss",
        "source_section_index": 3,
        "source_position": 100,
    }
    exact = {
        "raw_task": "Explain how the customs union supported economic unity.",
        "source_label": "Question",
        "_source_section_index": 2,
        "_source_position": 80,
    }

    match = phase2._match_anchor(task, [exact], set())

    assert match is not None
    assert match[1] is exact


def _final_checkpoint(with_phase2_inventory: bool):
    inventory = {"items": [{"qid": "QINV-0001"}]}
    if with_phase2_inventory:
        inventory["source_contract"] = {"mode": phase2.SOURCE_CONTRACT_MODE}
    return generation._make_concept_checkpoint(
        "final_content_ready",
        records=[{"concept_title": "A"}],
        question_task_inventory=inventory,
        mined_types={
            "types": [],
            "_topology_allocation_contract": {
                "version": 1,
                "state": "allocated_after_freeze",
            },
        },
        method_row_snapshot=[],
    )


def test_active_phase2_keeps_existing_terminal_recovery_rules():
    for checkpoint in (
        _final_checkpoint(False),
        _final_checkpoint(True),
    ):
        with phase2.activate({
            "source_contract": {"mode": phase2.SOURCE_CONTRACT_MODE}
        }):
            reasons = generation._final_checkpoint_refresh_reasons(
                checkpoint,
                sections=[],
                source_topic_excerpts=[],
            )
        assert "Phase 2 ACSD source inventory" not in " ".join(reasons)

from __future__ import annotations

import copy

import pytest

from app.services import generation as g
from app.services import semantic_recovery
from app.services import type_granularity_decision as gate


def _inventory(count: int = 12) -> dict:
    return {
        "items": [
            {
                "qid": f"QINV-{index:04d}",
                "source_kind": "exercise",
                "topic_hint": "Topic",
                "raw_task": (
                    f"Explain source method {index} using the supplied evidence "
                    "and show the complete reasoning."
                ),
                "task_kind": "question",
            }
            for index in range(1, count + 1)
        ],
        "stats": {"total_inventory_items": count},
    }


def _types(count: int = 12) -> dict:
    return {
        "types": [
            {
                "type_id": f"TYPE-{index:04d}",
                "type_title": f"Distinct assessment method {index}",
                "type_description": f"Apply method {index}.",
                "task_pattern": f"Given input {index}, apply method {index}.",
                "topic_match_hint": "Topic",
                "is_activity": False,
                "placement_scope": "normal",
                "source_question_ids": [f"QINV-{index:04d}"],
                "case_prompts": [{
                    "case_id": f"CASE-{index:04d}",
                    "case_title": f"Variation {index}",
                    "examples": [{
                        "source_question_id": f"QINV-{index:04d}",
                        "example_prompt": (
                            f"Explain source method {index} using the supplied "
                            "evidence and show the complete reasoning."
                        ),
                    }],
                }],
            }
            for index in range(1, count + 1)
        ],
    }


def _question_inventory_checkpoint(*, count: int = 12) -> dict:
    return g._make_concept_checkpoint(
        "question_inventory",
        records=[{
            "topic": "Topic",
            "parent_concept": "Topic",
            "concept_title": "Existing Concept",
            "concept_details": "Description: Existing source-grounded concept.",
            "keywords": "existing, concept",
        }],
        question_task_inventory=_inventory(count),
        method_row_snapshot=[],
    )


def _run_pre_final(*, checkpoint: dict, emitted: list[dict]):
    return g._run_live_concept_pre_final_stages(
        "## Topic\nSource body",
        subject="History",
        board="CBSE",
        chapter_title="Chapter",
        chunks=[],
        sections=[],
        method_anchors=[],
        headings=[],
        source_topic_excerpts=[],
        allow_chapter_title_topic=False,
        meta={"subject": "History", "chapter_title": "Chapter"},
        artifacts={},
        resume_checkpoint=checkpoint,
        checkpoint_callback=emitted.append,
    )


def _preserve_inventory(monkeypatch) -> None:
    monkeypatch.setattr(
        g,
        "_refresh_inventory_from_source_anchors",
        lambda inventory, _sections: copy.deepcopy(inventory),
    )


def test_fragmentation_pause_precedes_sufficiency_mastery_and_culmination(
    monkeypatch,
):
    calls: list[str] = []
    mined = _types()
    _preserve_inventory(monkeypatch)
    monkeypatch.setattr(
        g,
        "_mine_types_from_inventory_via_api",
        lambda **_kwargs: calls.append("mine") or copy.deepcopy(mined),
    )
    monkeypatch.setattr(
        g,
        "_consolidate_semantic_types_via_api",
        lambda current, **_kwargs: (
            calls.append("ordinary_consolidation") or copy.deepcopy(current)
        ),
    )

    def unexpected(*_args, **_kwargs):
        pytest.fail("downstream work must not run before the human decision")

    for name in (
        "_add_missing_type_method_concepts_via_api",
        "_ensure_mastery_lines_via_api",
        "_build_culminations_via_api",
        "_assign_types_via_api",
    ):
        monkeypatch.setattr(g, name, unexpected)

    emitted: list[dict] = []
    with pytest.raises(semantic_recovery.HumanDecisionRequired) as caught:
        _run_pre_final(
            checkpoint=_question_inventory_checkpoint(), emitted=emitted)

    assert calls == ["mine", "ordinary_consolidation"]
    assert [checkpoint["stage"] for checkpoint in emitted] == [
        g._TYPE_TAXONOMY_CHECKPOINT_STAGE,
    ]
    review_checkpoint = emitted[0]
    assert g._compatible_concept_checkpoint_entry(review_checkpoint)
    assert review_checkpoint["progress"] == pytest.approx(0.76)
    assert review_checkpoint["records"][0]["concept_title"] == (
        "Existing Concept"
    )
    assert caught.value.pending_decision["checkpoint_progress"] == (
        pytest.approx(0.76)
    )
    assert caught.value.pending_decision["evidence"][-1] == {
        "page": "",
        "label": "Concept sufficiency timing",
        "text": "Runs once after this decision",
    }


def test_fragmentation_resume_runs_downstream_once_then_promotes_checkpoint(
    monkeypatch,
):
    mined = _types()
    _preserve_inventory(monkeypatch)
    monkeypatch.setattr(
        g,
        "_mine_types_from_inventory_via_api",
        lambda **_kwargs: copy.deepcopy(mined),
    )
    monkeypatch.setattr(
        g,
        "_consolidate_semantic_types_via_api",
        lambda current, **_kwargs: copy.deepcopy(current),
    )
    first_emitted: list[dict] = []
    with pytest.raises(semantic_recovery.HumanDecisionRequired) as caught:
        _run_pre_final(
            checkpoint=_question_inventory_checkpoint(),
            emitted=first_emitted,
        )
    review_checkpoint = first_emitted[-1]
    pending = caught.value.pending_decision
    resolution = {
        **copy.deepcopy(pending),
        "status": "ready",
        "choice": "keep_distinct_types",
        "instruction": "",
    }

    calls: list[str] = []

    def sufficiency(records, *, mined_types, **_kwargs):
        calls.append("sufficiency")
        assert len(mined_types["types"]) == 12
        return [*records, {
            "topic": "Topic",
            "parent_concept": "Topic",
            "concept_title": "Added Method Concept",
            "concept_details": "Description: Added for the accepted taxonomy.",
            "keywords": "method",
        }]

    def mastery(records, **_kwargs):
        calls.append("mastery")
        return records

    def culminations(records, **_kwargs):
        calls.append("culmination")
        return [*records, {
            "topic": "Topic",
            "parent_concept": "Culmination",
            "concept_title": "Culmination - Topic",
            "concept_details": "Description: Topic recap.",
            "keywords": "recap",
        }]

    monkeypatch.setattr(
        g, "_add_missing_type_method_concepts_via_api", sufficiency)
    monkeypatch.setattr(g, "_ensure_mastery_lines_via_api", mastery)
    monkeypatch.setattr(g, "_build_culminations_via_api", culminations)
    monkeypatch.setattr(
        g, "_placement_certification_contract_complete", lambda *_args: True)
    taxonomy_reconciles: list[bool] = []

    def deterministic_taxonomy_reconcile(current, **kwargs):
        taxonomy_reconciles.append(bool(kwargs.get("use_api")))
        return copy.deepcopy(current)

    monkeypatch.setattr(
        g,
        "_reconcile_resumed_mined_types",
        deterministic_taxonomy_reconcile,
    )

    resumed_emitted: list[dict] = []
    with gate.human_resolution_context([resolution]):
        rows, _inventory_result, resumed_types, _snapshot = _run_pre_final(
            checkpoint=review_checkpoint,
            emitted=resumed_emitted,
        )

    assert calls == ["sufficiency", "mastery", "culmination"]
    assert taxonomy_reconciles == [False]
    assert any(
        row["concept_title"] == "Added Method Concept" for row in rows)
    assert any(row["concept_title"] == "Culmination - Topic" for row in rows)
    assert [checkpoint["stage"] for checkpoint in resumed_emitted] == [
        g._TYPE_TAXONOMY_CHECKPOINT_STAGE,
        g._CONCEPT_CHECKPOINT_STAGE,
    ]
    pre_type_checkpoint = resumed_emitted[1]
    review = resumed_types["_granularity_review"]
    assert review["sufficiency_audit_complete"] is True
    assert review["sufficiency_added_concepts"] == 1
    assert review["human_resolution"]["choice"] == "keep_distinct_types"

    monkeypatch.setattr(
        g,
        "_reconcile_resumed_mined_types",
        lambda current, **_kwargs: copy.deepcopy(current),
    )
    replay_emitted: list[dict] = []
    _run_pre_final(
        checkpoint=pre_type_checkpoint,
        emitted=replay_emitted,
    )

    # The topology compatibility layer deliberately defers assignment, but the
    # three paid passes moved behind this gate must not replay.
    assert calls == ["sufficiency", "mastery", "culmination"]
    assert replay_emitted == []

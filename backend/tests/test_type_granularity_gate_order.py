from __future__ import annotations

import copy

import pytest

from app.services import build_concepts
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
                    "topic_match_hint": "Topic",
                    "is_activity": False,
                    "placement_scope": "normal",
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


def test_generation_gate_uses_parent_count_for_expanded_leaf_inventory(
    monkeypatch,
):
    checkpoint = _question_inventory_checkpoint(count=31)
    checkpoint["question_task_inventory"]["source_contract"] = {
        "parent_task_count": 26,
    }
    mined = _types(24)
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

    with pytest.raises(semantic_recovery.HumanDecisionRequired) as caught:
        _run_pre_final(checkpoint=checkpoint, emitted=[])

    pending = caught.value.pending_decision
    assert pending["item"]["type_title"] == (
        "24 Types for 26 parent tasks (31 leaf QIDs)"
    )
    assert pending["evidence"][0]["text"].startswith("24/26 (92.3%)")


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


def test_consolidation_resume_feeds_only_accepted_taxonomy_downstream(
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

    pending = caught.value.pending_decision
    resolution = {
        **copy.deepcopy(pending),
        "status": "ready",
        "choice": "consolidate_types",
        "instruction": "Use one reusable assessment method.",
    }
    merged_type = copy.deepcopy(mined["types"][0])
    merged_type["source_question_ids"] = [
        qid
        for mtype in mined["types"]
        for qid in mtype["source_question_ids"]
    ]
    merged_type["case_prompts"] = [
        case
        for mtype in copy.deepcopy(mined["types"])
        for case in mtype["case_prompts"]
    ]
    accepted = {"types": [merged_type]}
    monkeypatch.setattr(
        g,
        "_human_directed_type_consolidation_via_api",
        lambda *_args, **_kwargs: (
            copy.deepcopy(accepted),
            "",
            {"critic_verdict": "accept", "critic_confidence": 0.95},
        ),
    )
    monkeypatch.setattr(
        g,
        "_reconcile_resumed_mined_types",
        lambda current, **_kwargs: copy.deepcopy(current),
    )

    downstream_type_counts: list[int] = []

    def sufficiency(records, *, mined_types, **_kwargs):
        downstream_type_counts.append(len(mined_types["types"]))
        return records

    monkeypatch.setattr(
        g, "_add_missing_type_method_concepts_via_api", sufficiency)
    monkeypatch.setattr(
        g, "_build_culminations_via_api", lambda records, **_kwargs: records)
    monkeypatch.setattr(
        g, "_assign_types_via_api", lambda records, **_kwargs: records)
    monkeypatch.setattr(
        g,
        "_populate_activity_hubs_via_api",
        lambda records, *_args, **_kwargs: records,
    )
    monkeypatch.setattr(
        g, "_placement_certification_contract_complete", lambda *_args: True)

    resumed_emitted: list[dict] = []
    with gate.human_resolution_context([resolution]):
        _rows, _inventory_result, accepted_types, _snapshot = _run_pre_final(
            checkpoint=first_emitted[-1],
            emitted=resumed_emitted,
        )

    assert downstream_type_counts == [1]
    assert len(accepted_types["types"]) == 1
    assert accepted_types["_granularity_review"]["type_count"] == 1
    assert accepted_types["_granularity_review"]["last_attempt"][
        "status"
    ] == "succeeded"
    assert resumed_emitted[0]["mined_types"]["_granularity_review"][
        "last_attempt"
    ]["status"] == "request_started"


def test_failed_authorized_type_pair_checkpoints_then_requires_fresh_decision(
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
    pending = caught.value.pending_decision
    resolution = {
        **copy.deepcopy(pending),
        "status": "ready",
        "choice": "consolidate_types",
        "instruction": "Consolidate only source-safe reusable methods.",
    }
    calls: list[int] = []
    resumed_emitted: list[dict] = []

    def fail_pair(*_args, **_kwargs):
        assert resumed_emitted[-1]["mined_types"][
            "_granularity_review"
        ]["last_attempt"]["status"] == "request_started"
        calls.append(1)
        raise RuntimeError("critic response schema invalid")

    monkeypatch.setattr(
        g, "_human_directed_type_consolidation_via_api", fail_pair)
    with gate.human_resolution_context([resolution]):
        with pytest.raises(semantic_recovery.HumanDecisionRequired) as follow:
            _run_pre_final(
                checkpoint=first_emitted[-1],
                emitted=resumed_emitted,
            )

    assert calls == [1]
    assert follow.value.pending_decision["decision_id"] != pending[
        "decision_id"
    ]
    saved_review = resumed_emitted[-1]["mined_types"][
        "_granularity_review"
    ]
    assert saved_review["last_attempt"]["status"] == "failed"
    assert saved_review["pending_followup"]["prior_decision_id"] == pending[
        "decision_id"
    ]

    # Re-entering the saved state with only the old answer must pause before a
    # second proposal/critic pair.
    with gate.human_resolution_context([resolution]):
        with pytest.raises(semantic_recovery.HumanDecisionRequired):
            _run_pre_final(
                checkpoint=resumed_emitted[-1],
                emitted=[],
            )
    assert calls == [1]


def test_request_started_type_checkpoint_consumes_ready_authorization():
    review = gate.build_review(
        raw_type_count=12,
        consolidated_type_count=12,
        inventory_count=12,
        sufficiency_added_concepts=0,
        sufficiency_audit_complete=False,
    )
    mined = _types()
    with pytest.raises(semantic_recovery.HumanDecisionRequired) as caught:
        gate.resolve_or_pause(
            review=review,
            inventory=_inventory(),
            mined_types=mined,
            meta={"subject": "History", "chapter_title": "Chapter"},
        )
    pending = caught.value.pending_decision
    base_mined = copy.deepcopy(mined)
    base_mined["_granularity_review"] = copy.deepcopy(review)
    base = g._make_concept_checkpoint(
        g._TYPE_TAXONOMY_CHECKPOINT_STAGE,
        records=[{
            "topic": "Topic",
            "parent_concept": "Topic",
            "concept_title": "Existing Concept",
            "concept_details": "Description: Existing content.",
            "keywords": "existing",
        }],
        question_task_inventory=_inventory(),
        mined_types=base_mined,
        method_row_snapshot=[],
    )
    common = {
        "fingerprint": "f" * 64,
        "target_identity": {"chapter_title": "Chapter"},
        "target_chapter_id": 1,
    }
    stored = build_concepts._merge_generation_checkpoint_history(
        {}, base, **common)
    stored[build_concepts._HUMAN_DECISIONS_KEY] = {
        "version": 1,
        "context": {
            "fingerprint": common["fingerprint"],
            "target_chapter_id": 1,
        },
        "pending": None,
        "deferred_assignment_unit_ids": [],
        "resolutions": [{
            "decision_id": pending["decision_id"],
            "context_hash": pending["context_hash"],
            "choice": "consolidate_types",
            "instruction": "Merge only reusable task patterns.",
            "target_id": "",
            "target_concept_id": "",
            "resolved_at": "2026-08-01T00:00:00+00:00",
            "status": "ready",
            "pending_decision": copy.deepcopy(pending),
        }],
    }
    started_mined = copy.deepcopy(base_mined)
    started_mined["_granularity_review"]["last_attempt"] = {
        "decision_id": pending["decision_id"],
        "context_hash": pending["context_hash"],
        "status": "request_started",
    }
    started_mined["_granularity_review"]["pending_followup"] = {
        "prior_decision_id": pending["decision_id"],
        "failure": "The dispatched pair has no saved certified result.",
    }
    started = g._make_concept_checkpoint(
        g._TYPE_TAXONOMY_CHECKPOINT_STAGE,
        records=base["records"],
        question_task_inventory=_inventory(),
        mined_types=started_mined,
        method_row_snapshot=[],
    )
    assert g._compatible_concept_checkpoint_entry(started)

    stored = build_concepts._merge_generation_checkpoint_history(
        stored, started, **common)

    resolution = stored[build_concepts._HUMAN_DECISIONS_KEY][
        "resolutions"
    ][0]
    assert resolution["status"] == "consumed"
    assert resolution["consumed_at"] == started["saved_at"]


def test_still_fragmented_human_consolidation_pauses_before_downstream_work(
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
    pending = caught.value.pending_decision
    resolution = {
        **copy.deepcopy(pending),
        "status": "ready",
        "choice": "consolidate_types",
        "instruction": "Merge any genuinely reusable patterns.",
    }
    still_fragmented = _types(10)
    monkeypatch.setattr(
        g,
        "_human_directed_type_consolidation_via_api",
        lambda *_args, **_kwargs: (
            copy.deepcopy(still_fragmented),
            "",
            {"critic_verdict": "accept", "critic_confidence": 0.95},
        ),
    )

    def downstream_forbidden(*_args, **_kwargs):
        pytest.fail("fragmented accepted result must pause before downstream")

    monkeypatch.setattr(
        g, "_add_missing_type_method_concepts_via_api", downstream_forbidden)
    emitted: list[dict] = []
    with gate.human_resolution_context([resolution]):
        with pytest.raises(semantic_recovery.HumanDecisionRequired) as follow:
            _run_pre_final(
                checkpoint=first_emitted[-1],
                emitted=emitted,
            )

    assert follow.value.pending_decision["decision_id"] != pending[
        "decision_id"
    ]
    saved = emitted[-1]["mined_types"]["_granularity_review"]
    assert saved["type_count"] == 10
    assert saved["last_attempt"]["status"] == "incomplete"

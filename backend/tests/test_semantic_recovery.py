from __future__ import annotations

import copy

import pytest

from app import models
from app.bulk_import import writer
from app.db import SessionLocal
from app.services import build_concepts
from app.services import semantic_recovery as recovery


def _row(
    title: str,
    *,
    topic: str = "Topic One",
    details: str = "",
    topic_id: str = "TOPIC-0001",
) -> dict:
    return {
        "topic": topic,
        "parent_concept": "Parent",
        "concept_title": title,
        "concept_details": details or (
            f"Description: Source-grounded explanation of {title}. // "
            "Misconception/ Error Analysis: Misconceptions: Students may "
            "confuse the central idea.; Error Analysis: Students may apply "
            "the relationship in the wrong order."
        ),
        "keywords": "source, concept",
        "_semantic_topic_id": topic_id,
        "_semantic_graph_contract": "SOURCE-CONTRACT-1",
        "_source_block_ids": ["BLK-0001"],
        "_source_grounding_contract": "api-verified-source-block-ids",
    }


def _checkpoint(records: list[dict]) -> dict:
    entry = {
        "schema_version": 3,
        "stage_schema_version": 1,
        "stage": "pre_type_assignment",
        "stage_order": 60,
        "stage_label": "Reusable Types mined",
        "saved_at": "2026-07-30T00:00:00+00:00",
        "progress": 0.81,
        "records": records,
        "question_task_inventory": {"items": [], "stats": {}},
        "mined_types": {"types": []},
        "method_row_snapshot": [],
    }
    return {
        "schema_version": 3,
        "checkpoint_format": "aegis-concept-stage-history",
        "checkpoints": [entry],
    }


def _valid_pre_map(
    *,
    prefix: str = "Foundation",
    first_concept: str = "",
) -> dict:
    return {
        "topics": [
            {
                "topic_name": f"{prefix} Area {topic_index}",
                "concepts": [
                    {
                        "concept_name": (
                            first_concept
                            if first_concept
                            and topic_index == 1
                            and concept_index == 1
                            else (
                                f"{prefix} Prerequisite "
                                f"{topic_index}.{concept_index}"
                            )
                        ),
                        "parent_concept": (
                            f"{prefix} Area {topic_index}"
                        ),
                        "concept_description": (
                            "Description: Learners should already command "
                            f"{prefix.lower()} foundation "
                            f"{topic_index}.{concept_index}. // "
                            "Misconception/ Error Analysis: Misconceptions: "
                            "Students may confuse the prerequisite.; Error "
                            "Analysis: Students may apply the prerequisite "
                            "in the wrong order."
                        ),
                        "tag": "NU",
                    }
                    for concept_index in range(1, 6)
                ],
            }
            for topic_index in range(1, 5)
        ],
    }


@pytest.mark.parametrize(
    ("exc", "kind"),
    [
        (
            RuntimeError(
                "source contract hash mismatch for the converted source"),
            recovery.FailureKind.SOURCE_IDENTITY,
        ),
        (
            RuntimeError(
                "Phase 3 semantic source graph is not safe for concept "
                "generation: source review required"),
            recovery.FailureKind.SOURCE_IDENTITY,
        ),
        (
            RuntimeError(
                "Phase 3 hierarchy critic requires review: numbered parent "
                "section is unresolved"),
            recovery.FailureKind.SOURCE_IDENTITY,
        ),
        (
            RuntimeError(
                "Phase 3.4 hierarchy batch returned an invalid role"),
            recovery.FailureKind.SOURCE_IDENTITY,
        ),
        (
            RuntimeError(
                "terminal Figure reference unresolved for Figure 4(a)"),
            recovery.FailureKind.EXPLICIT_FIGURE,
        ),
        (
            RuntimeError(
                "final validation failed: "
                "unresolved_explicit_figure_reference"
            ),
            recovery.FailureKind.EXPLICIT_FIGURE,
        ),
        (
            RuntimeError(
                "final validation failed: "
                "phase2_unresolved_figure_reference"
            ),
            recovery.FailureKind.EXPLICIT_FIGURE,
        ),
        (
            RuntimeError("duplicate QID QINV-0002 in source inventory"),
            recovery.FailureKind.QID_INTEGRITY,
        ),
        (
            RuntimeError(
                "final validation failed: invalid_source_inventory; "
                "1 invalid row"),
            recovery.FailureKind.QID_INTEGRITY,
        ),
        (
            OSError("workbook publish failed"),
            recovery.FailureKind.PERSISTENCE,
        ),
        (
            writer.ConceptWorkbookValidationError(
                "concept workbook read-back validation failed: "
                "topic order mismatch"
            ),
            recovery.FailureKind.PERSISTENCE,
        ),
        (
            RuntimeError(
                "GPT PDF-to-ACSD batch 1/9 requires review: "
                "PDF-PAGE-0002 non-task block 4 has a source cue"
            ),
            recovery.FailureKind.SOURCE_IDENTITY,
        ),
        (
            ValueError(
                "concept grounding failed independent verification at "
                "row_index=2"),
            recovery.FailureKind.RECOVERABLE_SEMANTIC,
        ),
        (
            ValueError(
                "Phase 3.2 could not source-verify concept topology: "
                "TOPOLOGY-CONCEPT-0013 requires human review"),
            recovery.FailureKind.RECOVERABLE_SEMANTIC,
        ),
    ],
)
def test_failure_classification_preserves_hard_boundaries(exc, kind):
    assessment = recovery.classify_failure(exc)
    assert assessment.kind is kind
    assert assessment.recoverable is (
        kind is recovery.FailureKind.RECOVERABLE_SEMANTIC)


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        (-4, 0),
        (0, 0),
        (1, 1),
        (2, 1),
        (99, 1),
        ("invalid", 1),
    ],
)
def test_recovery_policy_clamps_every_construction_path(configured, expected):
    assert recovery.RecoveryPolicy(max_attempts=configured).max_attempts == (
        expected
    )


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        ("-1", 0),
        ("0", 0),
        ("1", 1),
        ("3", 1),
        ("invalid", 1),
    ],
)
def test_recovery_policy_environment_allows_at_most_one_repair(
    monkeypatch,
    configured,
    expected,
):
    monkeypatch.setenv("AEGIS_SEMANTIC_RECOVERY_MAX_ATTEMPTS", configured)
    assert recovery.RecoveryPolicy.from_environment().max_attempts == expected


def test_recovery_policy_defaults_to_one_repair(monkeypatch):
    monkeypatch.delenv("AEGIS_SEMANTIC_RECOVERY_MAX_ATTEMPTS", raising=False)
    assert recovery.RecoveryPolicy().max_attempts == 1
    assert recovery.RecoveryPolicy.from_environment().max_attempts == 1


def test_provider_response_contract_failure_is_never_semantically_repaired():
    failure = recovery.ProviderResponseContractError(
        "Type-host response contract failed semantic validation after "
        "mechanical correction"
    )
    assessment = recovery.classify_failure(failure)
    calls = {"operation": 0, "repair": 0}

    def operation():
        calls["operation"] += 1
        raise failure

    def repair(_checkpoint, _context):
        calls["repair"] += 1
        raise AssertionError(
            "mechanical provider output must not enter semantic recovery"
        )

    with pytest.raises(recovery.ProviderResponseContractError):
        recovery.run_with_semantic_recovery(
            operation,
            checkpoint_snapshot=lambda: _checkpoint([_row("Safe Row")]),
            repair_checkpoint=repair,
            persist_repair=lambda *_args: pytest.fail(
                "mechanical provider output must not alter a checkpoint"
            ),
        )

    assert assessment.kind is recovery.FailureKind.PROVIDER
    assert assessment.recoverable is False
    assert calls == {"operation": 1, "repair": 0}


def test_runner_repairs_checkpoint_and_continues_same_operation():
    current = _checkpoint([_row("Incorrectly Grounded Concept")])
    calls = 0
    persisted: list[recovery.RepairResult] = []

    def operation():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ValueError(
                "concept grounding failed independent verification at "
                "row_index=0")
        return "completed"

    def repair(
        checkpoint: dict,
        context: recovery.RecoveryContext,
    ) -> recovery.RepairResult:
        changed = copy.deepcopy(
            recovery.select_recovery_checkpoint(checkpoint))
        assert changed is not None
        changed["records"][0]["concept_title"] = "Verified Concept"
        return recovery.RepairResult(
            checkpoint=changed,
            base_stage="pre_type_assignment",
            changed_row_indexes=(0,),
            repair_signature="repair-one",
            scope="checkpoint_rows",
        )

    def persist(
        result: recovery.RepairResult,
        _context: recovery.RecoveryContext,
    ) -> None:
        nonlocal current
        persisted.append(result)
        current = _checkpoint(result.checkpoint["records"])

    result = recovery.run_with_semantic_recovery(
        operation,
        checkpoint_snapshot=lambda: copy.deepcopy(current),
        repair_checkpoint=repair,
        persist_repair=persist,
        policy=recovery.RecoveryPolicy(max_attempts=1),
    )

    assert result == "completed"
    assert calls == 2
    assert len(persisted) == 1
    assert current["checkpoints"][0]["records"][0]["concept_title"] == (
        "Verified Concept")


def test_runner_persists_dispatch_boundary_before_paid_repair():
    current = _checkpoint([_row("Incorrectly Grounded Concept")])
    events: list[str] = []
    calls = 0

    def operation():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ValueError(
                "concept grounding failed independent verification at "
                "row_index=0"
            )
        return "completed"

    def before(_checkpoint, _context):
        events.append("dispatch_saved")

    def repair(checkpoint, _context):
        events.append("provider_called")
        selected = recovery.select_recovery_checkpoint(checkpoint)
        assert selected is not None
        return recovery.RepairResult(
            checkpoint=selected,
            base_stage="pre_type_assignment",
            changed_row_indexes=(0,),
            repair_signature="sealed-repair",
            scope="checkpoint_rows",
        )

    result = recovery.run_with_semantic_recovery(
        operation,
        checkpoint_snapshot=lambda: copy.deepcopy(current),
        repair_checkpoint=repair,
        before_repair=before,
        persist_repair=lambda *_args: events.append("repair_saved"),
        policy=recovery.RecoveryPolicy(max_attempts=1),
    )

    assert result == "completed"
    assert events == ["dispatch_saved", "provider_called", "repair_saved"]


def test_runner_never_calls_repair_when_dispatch_seal_refuses_replay():
    checkpoint = _checkpoint([_row("Unsupported Concept")])

    with pytest.raises(
        recovery.SemanticRecoveryExhausted,
        match="already dispatched",
    ):
        recovery.run_with_semantic_recovery(
            lambda: (_ for _ in ()).throw(ValueError(
                "unsupported concept failed semantic validation at row_index=0"
            )),
            checkpoint_snapshot=lambda: checkpoint,
            before_repair=lambda *_args: (_ for _ in ()).throw(
                recovery.SemanticRecoveryExhausted(
                    "semantic recovery already dispatched this issue"
                )
            ),
            repair_checkpoint=lambda *_args: pytest.fail(
                "repair provider must not run after a saved dispatch"
            ),
            persist_repair=lambda *_args: pytest.fail(
                "no replayed repair may be persisted"
            ),
            policy=recovery.RecoveryPolicy(max_attempts=1),
        )


def test_runner_does_not_retry_when_repair_is_noop():
    checkpoint = _checkpoint([_row("Unsupported Concept")])
    calls = 0

    def operation():
        nonlocal calls
        calls += 1
        raise ValueError(
            "unsupported concept failed semantic validation at row_index=0")

    with pytest.raises(
        recovery.SemanticRecoveryExhausted,
        match="no safe checkpoint change",
    ):
        recovery.run_with_semantic_recovery(
            operation,
            checkpoint_snapshot=lambda: checkpoint,
            repair_checkpoint=lambda *_args: None,
            persist_repair=lambda *_args: pytest.fail(
                "no-op repair must not be persisted"),
            policy=recovery.RecoveryPolicy(max_attempts=1),
        )

    assert calls == 1


def test_runner_never_repairs_workbook_readback_corruption():
    calls = 0

    def operation():
        nonlocal calls
        calls += 1
        raise writer.ConceptWorkbookValidationError(
            "concept workbook read-back validation failed: "
            "accepted topology does not match serialized workbook"
        )

    with pytest.raises(writer.ConceptWorkbookValidationError):
        recovery.run_with_semantic_recovery(
            operation,
            checkpoint_snapshot=lambda: _checkpoint([_row("Safe Row")]),
            repair_checkpoint=lambda *_args: pytest.fail(
                "persistence corruption must never be sent to GPT"
            ),
            persist_repair=lambda *_args: pytest.fail(
                "persistence corruption must never alter a checkpoint"
            ),
        )

    assert calls == 1


def test_recovery_scope_can_isolate_one_rejected_source_page():
    records = [
        {
            **_row("Page Six Concept"),
            "_source_page_numbers": [6],
        },
        {
            **_row("Page Seven Concept"),
            "_source_page_numbers": [7],
        },
        {
            **_row("Page Eight Concept"),
            "_source_page_numbers": [8],
        },
    ]
    checkpoint = recovery.select_recovery_checkpoint(_checkpoint(records))
    assert checkpoint is not None

    indexes = recovery.implicated_row_indexes(
        checkpoint,
        ValueError(
            "semantic grounding on PDF-PAGE-0007 failed independent verification"
        ),
    )

    assert indexes == (1,)


@pytest.mark.parametrize(
    ("diagnostic_id", "expected"),
    [
        ("CONCEPT-GROUND-0002", (1,)),
        ("TOPOLOGY-CONCEPT-0003", (2,)),
        ("HOST-CONCEPT-0001", (0,)),
    ],
)
def test_recovery_scope_resolves_phase3_diagnostic_id_families(
    diagnostic_id,
    expected,
):
    checkpoint = recovery.select_recovery_checkpoint(_checkpoint([
        _row("First Concept"),
        _row("Second Concept"),
        _row("Third Concept"),
    ]))
    assert checkpoint is not None

    indexes = recovery.implicated_row_indexes(
        checkpoint,
        ValueError(
            f"{diagnostic_id} failed exact source-block grounding and "
            "requires human review"
        ),
    )

    assert indexes == expected


def test_unresolvable_diagnostic_id_does_not_trigger_topic_wide_repair():
    checkpoint = recovery.select_recovery_checkpoint(_checkpoint([
        _row("First Concept", topic="Shared Topic"),
        _row("Second Concept", topic="Shared Topic"),
    ]))
    assert checkpoint is not None

    indexes = recovery.implicated_row_indexes(
        checkpoint,
        ValueError(
            "CONCEPT-GROUND-0099 for Shared Topic requires human review"
        ),
    )

    assert indexes == ()


def test_exact_late_row_is_not_crowded_out_by_shared_topic_matches():
    checkpoint = recovery.select_recovery_checkpoint(_checkpoint([
        _row(f"Shared Concept {index}", topic="Shared Topic")
        for index in range(20)
    ]))
    assert checkpoint is not None

    indexes = recovery.implicated_row_indexes(
        checkpoint,
        ValueError(
            "Shared Topic grounding failed at row_index=18 after review"
        ),
        max_rows=12,
    )

    assert indexes == (18,)


def test_count_only_inventory_coverage_does_not_shotgun_topic_rows():
    checkpoint = recovery.select_recovery_checkpoint(_checkpoint([
        _row(f"Concept {index}", topic="Shared Topic")
        for index in range(20)
    ]))
    assert checkpoint is not None
    checkpoint["question_task_inventory"] = {
        "items": [
            {"qid": "QINV-0001", "topic_hint": "Shared Topic"},
        ],
        "stats": {},
    }

    indexes = recovery.implicated_row_indexes(
        checkpoint,
        ValueError(
            "final inventory placement validation failed: "
            "exact_inventory_coverage expected 15 but found 14"
        ),
        max_rows=12,
    )

    assert indexes == ()


def test_structured_validator_row_precedes_broad_topic_match():
    checkpoint = recovery.select_recovery_checkpoint(_checkpoint([
        _row(f"Concept {index}", topic="Shared Topic")
        for index in range(20)
    ]))
    assert checkpoint is not None

    indexes = recovery.implicated_row_indexes(
        checkpoint,
        ValueError("Shared Topic failed final semantic validation"),
        validation_errors=lambda _records: [{
            "row_index": 18,
            "severity": "error",
        }],
        max_rows=12,
    )

    assert indexes == (18,)


def test_exact_qid_uses_certified_host_identity():
    checkpoint = recovery.select_recovery_checkpoint(_checkpoint([
        _row(f"Concept {index}", topic="Shared Topic")
        for index in range(20)
    ]))
    assert checkpoint is not None
    checkpoint["question_task_inventory"] = {
        "items": [{
            "qid": "QINV-0042",
            "topic_hint": "Shared Topic",
        }],
        "stats": {},
    }
    checkpoint["mined_types"] = {
        "types": [],
        "placement_certifications": {
            "version": 1,
            "hosts": {
                "QINV-0042": {
                    "topic": "Shared Topic",
                    "topic_key": "shared topic",
                    "concept": "Concept 18",
                    "concept_key": "concept 18",
                    "is_culmination": False,
                    "basis": "reviewed",
                },
            },
        },
    }

    indexes = recovery.implicated_row_indexes(
        checkpoint,
        ValueError(
            "question/task inventory coverage failed before deposit: "
            "missing=QINV-0042"
        ),
        max_rows=12,
    )

    assert indexes == (18,)


def test_leaf_qid_uses_its_exact_certified_host_identity():
    checkpoint = recovery.select_recovery_checkpoint(_checkpoint([
        _row(f"Concept {index}", topic="Shared Topic")
        for index in range(20)
    ]))
    assert checkpoint is not None
    checkpoint["question_task_inventory"] = {
        "items": [{
            "qid": "QINV-0016.2",
            "parent_qid": "QINV-0016",
            "topic_hint": "Shared Topic",
        }],
        "stats": {},
    }
    checkpoint["mined_types"] = {
        "types": [],
        "placement_certifications": {
            "version": 1,
            "hosts": {
                "QINV-0016.2": {
                    "topic": "Shared Topic",
                    "topic_key": "shared topic",
                    "concept": "Concept 18",
                    "concept_key": "concept 18",
                    "is_culmination": False,
                    "basis": "reviewed",
                },
            },
        },
    }

    indexes = recovery.implicated_row_indexes(
        checkpoint,
        ValueError(
            "question/task inventory coverage failed before deposit: "
            "missing=QINV-0016.2"
        ),
        max_rows=12,
    )

    assert indexes == (18,)


def test_gpt_repair_changes_only_implicated_row_and_preserves_source_owned_text():
    protected_types = (
        "Types: Type 01: Source task Case 01: Interpret the supplied Figure "
        "Example 01: Use [Figure: Figure 2 | "
        "https://example.test/figure-2.png] to explain QINV-0001."
    )
    records = [
        _row(
            "Trade Networks",
            topic="Political Nationalism",
            topic_id="TOPIC-0001",
            details=(
                "Description: Trade networks were a political voting system. "
                f"// {protected_types} // "
                "Misconception/ Error Analysis: Misconceptions: Students may "
                "confuse trade with elections.; Error Analysis: Students may "
                "attribute an economic effect to voting rules."
            ),
        ),
        _row(
            "Constitutional Government",
            topic="Political Nationalism",
            topic_id="TOPIC-0001",
        ),
        _row(
            "Economic Integration",
            topic="Economic Nationalism",
            topic_id="TOPIC-0002",
        ),
    ]
    checkpoint = _checkpoint(records)
    checkpoint["checkpoints"][0]["records"][0].update({
        "_semantic_topic_confidence": 0.98,
        "_semantic_topic_contract": "old-topic-review",
        "_semantic_subtopic_id": "TOPIC-0001-SUB-001",
        "_semantic_subtopic_ids": ["TOPIC-0001-SUB-001"],
        "_semantic_subtopic_title": "Old Subtopic",
    })
    context = recovery.RecoveryContext(
        attempt=1,
        failure=ValueError(
            "topic assignment mismatch at row_index=0, concept='Trade Networks'"),
        assessment=recovery.FailureAssessment(
            recovery.FailureKind.RECOVERABLE_SEMANTIC,
            "bounded repair",
        ),
        failure_signature="failure-one",
    )

    def api_call(_system: str, _user: str, **_kwargs):
        return {
            "blocked": False,
            "reason": "",
            "repairs": [{
                "row_index": 0,
                "topic": "Economic Nationalism",
                "parent_concept": "Economic Change",
                "concept_title": "Trade Networks and Market Integration",
                "concept_details": (
                    "Description: Trade networks connected markets and "
                    "supported economic integration. // "
                    "Misconception/ Error Analysis: Misconceptions: Students "
                    "may assume market links were political elections.; Error "
                    "Analysis: Students may place an economic process under a "
                    "constitutional topic."
                ),
                "keywords": "trade, markets, integration",
                "repair_reason": "The source evidence is economic.",
            }],
        }

    repaired = recovery.repair_concept_checkpoint_via_gpt(
        checkpoint,
        context,
        api_call=api_call,
        metadata={"subject": "History", "grade": "10"},
        source_text=(
            "Economic Nationalism\nTrade networks connected markets and "
            "supported economic integration."
        ),
        topic_id_by_title={
            "Political Nationalism": "TOPIC-0001",
            "Economic Nationalism": "TOPIC-0002",
        },
    )

    assert repaired is not None
    assert repaired.changed_row_indexes == (0,)
    changed = repaired.checkpoint["records"][0]
    assert changed["topic"] == "Economic Nationalism"
    assert changed["_semantic_topic_id"] == "TOPIC-0002"
    assert "_semantic_topic_confidence" not in changed
    assert "_semantic_topic_contract" not in changed
    assert "_semantic_subtopic_ids" not in changed
    assert "_source_block_ids" not in changed
    assert "_source_grounding_contract" not in changed
    assert protected_types in changed["concept_details"]
    assert repaired.checkpoint["records"][1:] == records[1:]


def test_post_row_repair_dispatches_one_provider_attempt():
    checkpoint = _checkpoint([_row("Unsupported Concept")])
    context = recovery.RecoveryContext(
        attempt=1,
        failure=ValueError(
            "concept grounding failed independent verification at row_index=0"
        ),
        assessment=recovery.FailureAssessment(
            recovery.FailureKind.RECOVERABLE_SEMANTIC,
            "bounded repair",
        ),
        failure_signature="single-attempt-post-row-repair",
    )
    calls: list[dict] = []

    def api_call(_system: str, _user: str, **kwargs):
        calls.append(kwargs)
        return {"blocked": True, "reason": "Test stops after dispatch."}

    repaired = recovery.repair_concept_checkpoint_via_gpt(
        checkpoint,
        context,
        api_call=api_call,
        metadata={"learning_kind": "Post"},
        source_text="Verified source evidence.",
    )

    assert repaired is None
    assert calls == [{
        "purpose": "concept_validation",
        "single_attempt": True,
    }]


def test_gpt_repair_rejects_figure_or_qid_mutation():
    details = (
        "Description: Inspect [Figure: Figure 3 | "
        "https://example.test/figure-3.png]. // "
        "Misconception/ Error Analysis: Misconceptions: Students may confuse "
        "the panels.; Error Analysis: Students may omit QINV-0004."
    )
    checkpoint = _checkpoint([_row("Visual Evidence", details=details)])
    context = recovery.RecoveryContext(
        attempt=1,
        failure=ValueError(
            "concept validation failed at row_index=0"),
        assessment=recovery.FailureAssessment(
            recovery.FailureKind.RECOVERABLE_SEMANTIC,
            "bounded repair",
        ),
        failure_signature="failure-two",
    )

    repaired = recovery.repair_concept_checkpoint_via_gpt(
        checkpoint,
        context,
        api_call=lambda *_args, **_kwargs: {
            "blocked": False,
            "repairs": [{
                "row_index": 0,
                "topic": "Topic One",
                "parent_concept": "Parent",
                "concept_title": "Visual Evidence",
                "concept_details": details.replace("Figure 3", "Figure 4"),
                "keywords": "visual",
                "repair_reason": "unsafe mutation",
            }],
        },
        metadata={},
        source_text="",
    )

    assert repaired is None


@pytest.mark.parametrize(
    "changed_details",
    [
        (
            "Description: Inspect Fig. 3 "
            "![panel](https://example.test/other.png). // "
            "Misconception/ Error Analysis: Misconceptions: Students may "
            "confuse panels.; Error Analysis: Students may use the wrong panel."
        ),
        (
            "Description: Inspect Fig. 3. // "
            "Misconception/ Error Analysis: Misconceptions: Students may "
            "confuse panels.; Error Analysis: Students may omit the image."
        ),
    ],
)
def test_gpt_repair_rejects_visual_markup_mutation(changed_details):
    details = (
        "Description: Inspect Fig. 3 "
        "![panel](https://example.test/original.png). // "
        "Misconception/ Error Analysis: Misconceptions: Students may "
        "confuse panels.; Error Analysis: Students may use the wrong panel."
    )
    checkpoint = _checkpoint([_row("Visual Markup", details=details)])
    context = recovery.RecoveryContext(
        attempt=1,
        failure=ValueError("concept validation failed at row_index=0"),
        assessment=recovery.FailureAssessment(
            recovery.FailureKind.RECOVERABLE_SEMANTIC,
            "bounded repair",
        ),
        failure_signature="visual-markup-failure",
    )

    repaired = recovery.repair_concept_checkpoint_via_gpt(
        checkpoint,
        context,
        api_call=lambda *_args, **_kwargs: {
            "blocked": False,
            "repairs": [{
                "row_index": 0,
                "topic": "Topic One",
                "parent_concept": "Parent",
                "concept_title": "Visual Markup",
                "concept_details": changed_details,
                "keywords": "visual",
            }],
        },
        metadata={},
        source_text="",
    )

    assert repaired is None


def test_gpt_repair_cannot_inject_types_into_untyped_row():
    checkpoint = _checkpoint([_row("Untyped Concept")])
    context = recovery.RecoveryContext(
        attempt=1,
        failure=ValueError("concept validation failed at row_index=0"),
        assessment=recovery.FailureAssessment(
            recovery.FailureKind.RECOVERABLE_SEMANTIC,
            "bounded repair",
        ),
        failure_signature="types-injection-failure",
    )

    repaired = recovery.repair_concept_checkpoint_via_gpt(
        checkpoint,
        context,
        api_call=lambda *_args, **_kwargs: {
            "blocked": False,
            "repairs": [{
                "row_index": 0,
                "topic": "Topic One",
                "parent_concept": "Parent",
                "concept_title": "Corrected Untyped Concept",
                "concept_details": (
                    "Description: Corrected source-grounded explanation. // "
                    "Types: Type 01: Injected Type Case 01: Injected Case // "
                    "Misconception/ Error Analysis: Misconceptions: Students "
                    "may confuse the idea.; Error Analysis: Students may "
                    "apply it in the wrong order."
                ),
                "keywords": "corrected",
            }],
        },
        metadata={},
        source_text="",
    )

    assert repaired is not None
    assert "Types:" not in repaired.checkpoint["records"][0]["concept_details"]


def test_authoritative_graph_topics_exclude_stale_checkpoint_topic():
    checkpoint = _checkpoint([
        _row("Misplaced Concept", topic="Stale Topic", topic_id="OLD-1")
    ])
    context = recovery.RecoveryContext(
        attempt=1,
        failure=ValueError("topic mismatch at row_index=0"),
        assessment=recovery.FailureAssessment(
            recovery.FailureKind.RECOVERABLE_SEMANTIC,
            "bounded repair",
        ),
        failure_signature="authoritative-topic-failure",
    )

    repaired = recovery.repair_concept_checkpoint_via_gpt(
        checkpoint,
        context,
        api_call=lambda *_args, **_kwargs: {
            "blocked": False,
            "repairs": [{
                "row_index": 0,
                "topic": "Stale Topic",
                "parent_concept": "Parent",
                "concept_title": "Still Misplaced",
                "concept_details": (
                    "Description: Reworded but still misplaced. // "
                    "Misconception/ Error Analysis: Misconceptions: Students "
                    "may confuse the idea.; Error Analysis: Students may "
                    "apply it in the wrong order."
                ),
                "keywords": "stale",
            }],
        },
        metadata={},
        source_text="",
        topic_id_by_title={"Verified Topic": "TOPIC-0001"},
    )

    assert repaired is None


def test_gpt_repair_rejects_removal_of_one_duplicate_source_token():
    details = (
        "Description: QINV-0004 is referenced twice for identity checking: "
        "QINV-0004. // Misconception/ Error Analysis: Misconceptions: Students "
        "may miss one occurrence.; Error Analysis: Students may delete one "
        "occurrence."
    )
    checkpoint = _checkpoint([_row("Occurrence Integrity", details=details)])
    context = recovery.RecoveryContext(
        attempt=1,
        failure=ValueError("concept validation failed at row_index=0"),
        assessment=recovery.FailureAssessment(
            recovery.FailureKind.RECOVERABLE_SEMANTIC,
            "bounded repair",
        ),
        failure_signature="failure-three",
    )

    repaired = recovery.repair_concept_checkpoint_via_gpt(
        checkpoint,
        context,
        api_call=lambda *_args, **_kwargs: {
            "blocked": False,
            "repairs": [{
                "row_index": 0,
                "topic": "Topic One",
                "parent_concept": "Parent",
                "concept_title": "Occurrence Integrity",
                "concept_details": details.replace(
                    "QINV-0004 is referenced twice for identity checking: ",
                    "",
                ),
                "keywords": "occurrence",
                "repair_reason": "unsafe occurrence removal",
            }],
        },
        metadata={},
        source_text="",
    )

    assert repaired is None


def test_build_recovery_uses_session_graph_after_active_scope_unwinds(
    monkeypatch,
):
    from app.services import canonical_source_phase3 as phase3

    graph = {
        "topics": [{
            "topic_id": "TOPIC-0007",
            "title": "Verified Session Topic",
        }],
    }
    canonical = {"blocks": []}
    monkeypatch.setattr(phase3, "active_graph", lambda: None)
    monkeypatch.setattr(
        phase3,
        "active_session",
        lambda: {"graph": graph, "canonical": canonical},
    )
    monkeypatch.setattr(
        phase3,
        "render_semantic_source",
        lambda supplied_graph, supplied_canonical: (
            "VERIFIED SEMANTIC SOURCE"
            if supplied_graph is graph and supplied_canonical is canonical
            else pytest.fail("wrong semantic recovery graph")
        ),
    )

    assert build_concepts._semantic_recovery_topic_ids() == {
        "Verified Session Topic": "TOPIC-0007",
    }
    assert build_concepts._semantic_recovery_source_text("raw source") == (
        "VERIFIED SEMANTIC SOURCE")


def test_build_recovery_does_not_fallback_when_verified_graph_render_fails(
    monkeypatch,
):
    from app.services import canonical_source_phase3 as phase3

    monkeypatch.setattr(
        phase3,
        "active_session",
        lambda: {"graph": {"topics": []}, "canonical": {"blocks": []}},
    )
    monkeypatch.setattr(phase3, "active_graph", lambda: None)
    monkeypatch.setattr(
        phase3,
        "render_semantic_source",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("verified graph render failed")
        ),
    )
    monkeypatch.setattr(
        build_concepts.canonical_source_phase22,
        "active_semantic_source",
        lambda *_args, **_kwargs: pytest.fail(
            "verified graph failure must not fall back to Phase 2"
        ),
    )

    with pytest.raises(RuntimeError, match="verified graph render failed"):
        build_concepts._semantic_recovery_source_text("raw source")


def test_pre_learning_repair_demotes_audited_map_and_reruns_auditor(
    monkeypatch,
):
    target_row = _row(
        "Proportional Reasoning",
        topic="Target Topic",
    )
    current = _valid_pre_map(
        prefix="Draft",
        first_concept="Proportional Reasoning",
    )
    candidate = _valid_pre_map(
        prefix="Arithmetic",
        first_concept="Equivalent Fraction Foundations",
    )
    audited = build_concepts.generation._make_concept_checkpoint(
        "pre_derivation_audited",
        records=[target_row],
        pre_audited=current,
    )
    envelope = {
        "schema_version": 3,
        "checkpoint_format": "aegis-concept-stage-history",
        "checkpoints": [audited],
    }
    context = recovery.RecoveryContext(
        attempt=1,
        failure=RuntimeError(
            "live pre-learning derivation returned no concepts"
        ),
        assessment=recovery.FailureAssessment(
            recovery.FailureKind.RECOVERABLE_SEMANTIC,
            "bounded repair",
        ),
        failure_signature="pre-audit-failure",
    )

    repaired = recovery.repair_pre_learning_checkpoint_via_gpt(
        envelope,
        context,
        api_call=lambda *_args, **_kwargs: {
            "blocked": False,
            "pre_map": candidate,
        },
        metadata={
            "learning_kind": "Pre",
            "grade": "8",
            "board": "CBSE",
        },
        source_text="The target chapter introduces proportional reasoning.",
    )

    assert repaired is not None
    assert repaired.base_stage == "pre_derivation_draft"
    assert repaired.changed_row_indexes == ()
    assert repaired.changed_units == ("pre_draft",)
    assert recovery._pre_map_shape(
        repaired.checkpoint["pre_draft"]
    ) == (5, 5, 5, 5)
    assert (
        repaired.checkpoint["pre_draft"]["topics"][0]["concepts"][0][
            "concept_name"
        ]
        == "Equivalent Fraction Foundations"
    )

    audit_calls = 0

    def audited_api(_system, _user, *, purpose):
        nonlocal audit_calls
        assert purpose == "pre_learning"
        audit_calls += 1
        return copy.deepcopy(repaired.checkpoint["pre_draft"])

    monkeypatch.setattr(
        build_concepts.generation,
        "_openai_json",
        audited_api,
    )
    monkeypatch.setattr(
        build_concepts.generation,
        "_ensure_misconceptions_via_api",
        lambda records, **_kwargs: records,
    )
    resumed_rows = build_concepts.generation.pre_learning_from_rows(
        [target_row],
        subject="Mathematics",
        grade="8",
        board="CBSE",
        chapter_title="Proportional Reasoning",
        live=True,
        resume_checkpoint={
            "schema_version": 3,
            "checkpoint_format": "aegis-concept-stage-history",
            "checkpoints": [repaired.checkpoint],
        },
    )

    assert audit_calls == 1
    assert len(resumed_rows) == 20
    assert resumed_rows[0]["concept_title"] == (
        "Equivalent Fraction Foundations"
    )


def test_explicit_pre_generation_scope_overrides_generic_failure_wording():
    target_row = _row(
        "Target Concept",
        topic="Target Topic",
    )
    envelope = _checkpoint([target_row])
    candidate = _valid_pre_map(prefix="Recovered")
    context = recovery.RecoveryContext(
        attempt=1,
        failure=RuntimeError("semantic validation failed"),
        assessment=recovery.FailureAssessment(
            recovery.FailureKind.RECOVERABLE_SEMANTIC,
            "bounded repair",
        ),
        failure_signature="generic-pre-generation-failure",
    )
    dispatches: list[tuple[str, bool]] = []

    def api_call(_system, _user, *, purpose, single_attempt):
        dispatches.append((purpose, single_attempt))
        return {
            "blocked": False,
            "pre_map": candidate,
        }

    repaired = recovery.repair_pre_learning_checkpoint_via_gpt(
        envelope,
        context,
        api_call=api_call,
        metadata={"learning_kind": "Pre"},
        source_text="Verified target-chapter source.",
        failure_scope="pre_generation",
    )

    assert repaired is not None
    assert repaired.scope == "pre_learning_map"
    assert repaired.base_stage == "pre_derivation_draft"
    assert dispatches == [("pre_learning", True)]


def test_pre_map_repair_dispatches_one_provider_attempt():
    envelope = _checkpoint([_row("Target Chapter Concept")])
    context = recovery.RecoveryContext(
        attempt=1,
        failure=RuntimeError("pre-learning prerequisite map failed validation"),
        assessment=recovery.FailureAssessment(
            recovery.FailureKind.RECOVERABLE_SEMANTIC,
            "bounded repair",
        ),
        failure_signature="single-attempt-pre-map-repair",
    )
    calls: list[dict] = []

    def api_call(_system: str, _user: str, **kwargs):
        calls.append(kwargs)
        return {"blocked": True, "reason": "Test stops after dispatch."}

    repaired = recovery.repair_pre_learning_checkpoint_via_gpt(
        envelope,
        context,
        api_call=api_call,
        metadata={"learning_kind": "Pre"},
        source_text="Verified target-chapter source.",
        failure_scope="pre_generation",
    )

    assert repaired is None
    assert calls == [{
        "purpose": "pre_learning",
        "single_attempt": True,
    }]


def test_under_generated_saved_pre_map_can_expand_to_contract_shape():
    target_row = _row(
        "Target Concept",
        topic="Target Topic",
    )
    undersized = {
        "topics": [{
            "topic_name": "Incomplete Foundation",
            "concepts": [{
                "concept_name": "Incomplete Prerequisite",
                "parent_concept": "Incomplete Foundation",
                "concept_description": (
                    "Description: Incomplete prior knowledge. // "
                    "Misconception/ Error Analysis: Misconceptions: Students "
                    "may confuse it.; Error Analysis: Students may omit it."
                ),
                "tag": "FL",
            }],
        }],
    }
    draft = build_concepts.generation._make_concept_checkpoint(
        "pre_derivation_draft",
        records=[target_row],
        pre_draft=undersized,
    )
    envelope = {
        "schema_version": 3,
        "checkpoint_format": "aegis-concept-stage-history",
        "checkpoints": [draft],
    }
    context = recovery.RecoveryContext(
        attempt=1,
        failure=RuntimeError("semantic validation failed"),
        assessment=recovery.FailureAssessment(
            recovery.FailureKind.RECOVERABLE_SEMANTIC,
            "bounded repair",
        ),
        failure_signature="undersized-saved-pre-map",
    )
    candidate = _valid_pre_map(prefix="Complete")

    repaired = recovery.repair_pre_learning_checkpoint_via_gpt(
        envelope,
        context,
        api_call=lambda *_args, **_kwargs: {
            "blocked": False,
            "pre_map": candidate,
        },
        metadata={"learning_kind": "Pre"},
        source_text="Verified target-chapter source.",
        failure_scope="pre_generation",
    )

    assert repaired is not None
    assert repaired.base_stage == "pre_derivation_draft"
    assert recovery._pre_map_shape(
        repaired.checkpoint["pre_draft"]
    ) == (5, 5, 5, 5)


def test_pre_envelope_routes_phase3_failure_to_post_checkpoint():
    bad_post_row = _row(
        "Unsupported Target Concept",
        topic="Target Topic",
    )
    post_stage = build_concepts.generation._make_concept_checkpoint(
        "pre_type_assignment",
        records=[bad_post_row],
        question_task_inventory={"items": [], "stats": {}},
        mined_types={"types": []},
        method_row_snapshot=[],
    )
    pre_stage = build_concepts.generation._make_concept_checkpoint(
        "pre_derivation_audited",
        records=[bad_post_row],
        pre_audited={
            "topics": [{
                "topic_name": "Prior Knowledge",
                "concepts": [{
                    "concept_name": "Existing Prerequisite",
                    "parent_concept": "Prior Knowledge",
                    "concept_description": (
                        "Description: Existing prior knowledge. // "
                        "Misconception/ Error Analysis: Misconceptions: "
                        "Students may confuse it.; Error Analysis: Students "
                        "may apply it in the wrong order."
                    ),
                    "tag": "FL",
                }],
            }],
        },
    )
    envelope = {
        "schema_version": 3,
        "checkpoint_format": "aegis-concept-stage-history",
        "checkpoints": [post_stage, pre_stage],
    }
    context = recovery.RecoveryContext(
        attempt=1,
        failure=ValueError(
            "Phase 3 concept grounding failed independent verification "
            "at row_index=0"
        ),
        assessment=recovery.FailureAssessment(
            recovery.FailureKind.RECOVERABLE_SEMANTIC,
            "bounded repair",
        ),
        failure_signature="post-failure-with-stale-pre",
    )

    repaired = recovery.repair_pre_learning_checkpoint_via_gpt(
        envelope,
        context,
        api_call=lambda *_args, **_kwargs: {
            "blocked": False,
            "repairs": [{
                "row_index": 0,
                "topic": "Target Topic",
                "parent_concept": "Verified Target Parent",
                "concept_title": "Verified Target Concept",
                "concept_details": (
                    "Description: Verified target-chapter explanation. // "
                    "Misconception/ Error Analysis: Misconceptions: Students "
                    "may accept an unsupported claim.; Error Analysis: "
                    "Students may omit the verified condition."
                ),
                "keywords": "verified, target",
            }],
        },
        metadata={"learning_kind": "Pre"},
        source_text="Verified target-chapter explanation.",
    )

    assert repaired is not None
    assert repaired.base_stage == "pre_type_assignment"
    assert repaired.changed_row_indexes == (0,)
    assert repaired.checkpoint["records"][0]["concept_title"] == (
        "Verified Target Concept"
    )
    assert "pre_audited" not in repaired.checkpoint


@pytest.mark.parametrize(
    "candidate",
    [
        {
            "topics": [{
                "topic_name": "Too Small",
                "concepts": [{
                    "concept_name": "One Prerequisite",
                    "parent_concept": "Too Small",
                    "concept_description": (
                        "Description: Prior knowledge. // Misconception/ "
                        "Error Analysis: Misconceptions: Students may confuse "
                        "it.; Error Analysis: Students may omit a step."
                    ),
                    "tag": "FL",
                }],
            }],
        },
        {
            "topics": [
                {
                    "topic_name": f"Foundation {topic_index}",
                    "concepts": [
                        {
                            "concept_name": (
                                f"Prerequisite {topic_index}.{concept_index}"
                            ),
                            "parent_concept": f"Foundation {topic_index}",
                            "concept_description": (
                                "Description: Prior knowledge. // "
                                "Misconception/ Error Analysis: "
                                "Misconceptions: Students may confuse it.; "
                                "Error Analysis: Students may omit a step."
                            ),
                            "tag": (
                                ""
                                if topic_index == 1 and concept_index == 1
                                else "FL"
                            ),
                        }
                        for concept_index in range(1, 6)
                    ],
                }
                for topic_index in range(1, 5)
            ],
        },
    ],
)
def test_synthesized_pre_draft_enforces_full_shape_and_valid_tags(candidate):
    target_row = _row("Target Concept", topic="Target Topic")
    envelope = _checkpoint([target_row])
    context = recovery.RecoveryContext(
        attempt=1,
        failure=RuntimeError(
            "live pre-learning derivation returned no topics"
        ),
        assessment=recovery.FailureAssessment(
            recovery.FailureKind.RECOVERABLE_SEMANTIC,
            "bounded repair",
        ),
        failure_signature="invalid-synthetic-pre-map",
    )

    repaired = recovery.repair_pre_learning_checkpoint_via_gpt(
        envelope,
        context,
        api_call=lambda *_args, **_kwargs: {
            "blocked": False,
            "pre_map": candidate,
        },
        metadata={"learning_kind": "Pre"},
        source_text="Target chapter source.",
    )

    assert repaired is None


@pytest.mark.parametrize("duplicate_kind", ["topic", "concept"])
def test_pre_map_rejects_duplicate_names(duplicate_kind):
    candidate = _valid_pre_map()
    if duplicate_kind == "topic":
        candidate["topics"][1]["topic_name"] = (
            candidate["topics"][0]["topic_name"]
        )
    else:
        candidate["topics"][1]["concepts"][0]["concept_name"] = (
            candidate["topics"][0]["concepts"][0]["concept_name"]
        )

    assert recovery._normalize_pre_map(candidate) is None


def test_post_learning_recovers_semantic_rejection_in_same_run(
    db,
    first_chapter,
    monkeypatch,
):
    job = models.UploadJob(
        module="build_concepts",
        upload_type="document",
        learning_kind="post",
        filename="same-run-recovery.mmd",
        mmd_text=(
            "## Source Topic\nVerified source explanation for the central "
            "relationship."
        ),
        status="converted",
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    bad_row = _row(
        "Unsupported Central Relationship",
        topic="Source Topic",
        details=(
            "Description: An unsupported statement. // "
            "Misconception/ Error Analysis: Misconceptions: Students may "
            "accept the unsupported statement.; Error Analysis: Students may "
            "repeat it without source evidence."
        ),
        topic_id="TOPIC-0001",
    )
    checkpoint = build_concepts.generation._make_concept_checkpoint(
        "pre_type_assignment",
        records=[bad_row],
        question_task_inventory={"items": [], "stats": {}},
        mined_types={"types": []},
        method_row_snapshot=[],
    )
    generation_calls = 0

    def generate(
        *_args,
        artifacts=None,
        resume_checkpoint=None,
        checkpoint_callback=None,
        **_kwargs,
    ):
        nonlocal generation_calls
        generation_calls += 1
        if generation_calls == 1:
            assert checkpoint_callback is not None
            checkpoint_callback(checkpoint)
            raise ValueError(
                "unsupported concept failed independent grounding verification "
                "at row_index=0, concept='Unsupported Central Relationship'"
            )
        saved = (
            build_concepts.generation._newest_compatible_concept_checkpoint(
                resume_checkpoint)
        )
        assert saved is not None
        dispatches = resume_checkpoint["semantic_recovery_dispatches"]
        assert dispatches["attempts"][0]["status"] == "succeeded"
        assert len(dispatches["attempts"]) == 1
        assert saved["stage"] == "pre_type_assignment"
        assert saved["records"][0]["concept_title"] == (
            "Verified Central Relationship")
        if artifacts is not None:
            artifacts["question_task_inventory"] = {"items": [], "stats": {}}
            artifacts["mined_types"] = {"types": []}
        return copy.deepcopy(saved["records"])

    monkeypatch.setattr(
        build_concepts.generation, "concepts_from_mmd", generate)
    monkeypatch.setattr(
        build_concepts.config, "use_live_generation", lambda: True)
    monkeypatch.setattr(
        build_concepts.generation,
        "_openai_json",
        lambda *_args, **_kwargs: {
            "blocked": False,
            "repairs": [{
                "row_index": 0,
                "topic": "Source Topic",
                "parent_concept": "Verified Relationships",
                "concept_title": "Verified Central Relationship",
                "concept_details": (
                    "Description: The source explains the verified central "
                    "relationship. // Misconception/ Error Analysis: "
                    "Misconceptions: Students may substitute an unsupported "
                    "claim.; Error Analysis: Students may omit the source "
                    "condition while explaining the relationship."
                ),
                "keywords": "verified, source, relationship",
                "repair_reason": "Removed the unsupported claim.",
            }],
        },
    )
    monkeypatch.setattr(
        build_concepts,
        "_deposit_and_publish_concepts",
        lambda *_args, **_kwargs: (
            [],
            [],
            {
                "written": 0,
                "sources_updated": 0,
                "parent_column": True,
            },
        ),
    )
    monkeypatch.setattr(
        build_concepts.drive_checkpoints,
        "schedule_checkpoint_backup",
        lambda *_args, **_kwargs: None,
    )

    result = build_concepts.generate_post_learning(
        db, job.id, first_chapter["id"])

    assert result["job_id"] == job.id
    assert generation_calls == 2
    db.expire_all()
    completed = db.get(models.UploadJob, job.id)
    assert completed.status == "generated"
    assert completed.generation_checkpoint == {}


def test_post_learning_recovers_deposit_semantics_without_manual_resume(
    db,
    first_chapter,
    monkeypatch,
):
    job = models.UploadJob(
        module="build_concepts",
        upload_type="document",
        learning_kind="post",
        filename="same-run-deposit-recovery.mmd",
        mmd_text="## Source Topic\nA verified source relationship.",
        status="converted",
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    bad_row = _row(
        "Unverified Deposit Concept",
        topic="Source Topic",
        topic_id="TOPIC-0001",
    )
    checkpoint = build_concepts.generation._make_concept_checkpoint(
        "pre_type_assignment",
        records=[bad_row],
        question_task_inventory={"items": [], "stats": {}},
        mined_types={"types": []},
        method_row_snapshot=[],
    )
    generation_calls = 0
    deposit_calls = 0

    def generate(
        *_args,
        artifacts=None,
        resume_checkpoint=None,
        checkpoint_callback=None,
        **_kwargs,
    ):
        nonlocal generation_calls
        generation_calls += 1
        if generation_calls == 1:
            checkpoint_callback(checkpoint)
            records = [bad_row]
        else:
            saved = (
                build_concepts.generation
                ._newest_compatible_concept_checkpoint(resume_checkpoint)
            )
            assert saved is not None
            records = copy.deepcopy(saved["records"])
            assert records[0]["concept_title"] == "Verified Deposit Concept"
        if artifacts is not None:
            artifacts["question_task_inventory"] = {"items": [], "stats": {}}
            artifacts["mined_types"] = {"types": []}
        return records

    def deposit(*_args, **_kwargs):
        nonlocal deposit_calls
        deposit_calls += 1
        if deposit_calls == 1:
            raise build_concepts.DepositValidationError(
                "deposit validation failed: unsupported concept; "
                "row_index=0, concept='Unverified Deposit Concept'"
            )
        return [], [], {
            "written": 0,
            "sources_updated": 0,
            "parent_column": True,
        }

    monkeypatch.setattr(
        build_concepts.config, "use_live_generation", lambda: True)
    monkeypatch.setattr(
        build_concepts.generation, "concepts_from_mmd", generate)
    monkeypatch.setattr(
        build_concepts.generation,
        "_openai_json",
        lambda *_args, **_kwargs: {
            "blocked": False,
            "repairs": [{
                "row_index": 0,
                "topic": "Source Topic",
                "parent_concept": "Verified Relationships",
                "concept_title": "Verified Deposit Concept",
                "concept_details": (
                    "Description: A verified source relationship. // "
                    "Misconception/ Error Analysis: Misconceptions: Students "
                    "may accept an unverified relationship.; Error Analysis: "
                    "Students may omit the source condition."
                ),
                "keywords": "verified, relationship",
                "repair_reason": "Aligned the concept with the source.",
            }],
        },
    )
    monkeypatch.setattr(
        build_concepts, "_deposit_and_publish_concepts", deposit)
    monkeypatch.setattr(
        build_concepts.drive_checkpoints,
        "schedule_checkpoint_backup",
        lambda *_args, **_kwargs: None,
    )

    result = build_concepts.generate_post_learning(
        db, job.id, first_chapter["id"])

    assert result["job_id"] == job.id
    assert generation_calls == 2
    assert deposit_calls == 2


def test_durable_semantic_repair_dispatch_blocks_worker_restart_replay(
    db,
    first_chapter,
    monkeypatch,
):
    chapter = db.get(models.Chapter, first_chapter["id"])
    job = models.UploadJob(
        module="build_concepts",
        upload_type="document",
        learning_kind="post",
        filename="durable-repair-dispatch.mmd",
        mmd_text="## Source Topic\nVerified source wording.",
        status="converted",
    )
    db.add(job)
    db.flush()
    stage = build_concepts.generation._make_concept_checkpoint(
        "pre_type_assignment",
        records=[_row("Unsupported Concept", topic="Source Topic")],
        question_task_inventory={"items": [], "stats": {}},
        mined_types={"types": []},
        method_row_snapshot=[],
    )
    job.generation_checkpoint = build_concepts._merge_generation_checkpoint_history(
        {},
        stage,
        fingerprint=build_concepts._generation_checkpoint_fingerprint(
            job, chapter
        ),
        target_identity=build_concepts._generation_target_identity(chapter),
        target_chapter_id=chapter.id,
    )
    db.commit()
    db.refresh(job)
    failure = ValueError(
        "unsupported concept failed semantic validation at row_index=0"
    )
    assessment = recovery.classify_failure(failure)
    context = recovery.RecoveryContext(
        attempt=1,
        failure=failure,
        assessment=assessment,
        failure_signature=recovery.failure_signature(
            failure, job.generation_checkpoint
        ),
    )
    monkeypatch.setattr(
        build_concepts.drive_checkpoints,
        "schedule_checkpoint_backup",
        lambda *_args, **_kwargs: None,
    )

    issue_key = build_concepts._persist_semantic_recovery_dispatch_started(
        db, job, copy.deepcopy(job.generation_checkpoint), context
    )
    db.refresh(job)
    attempts = job.generation_checkpoint[
        "semantic_recovery_dispatches"
    ]["attempts"]
    assert len(attempts) == 1
    assert attempts[0]["issue_key"] == issue_key
    assert attempts[0]["status"] == "request_started"
    assert attempts[0]["completed_at"] == ""
    assert recovery.failure_signature(
        failure, job.generation_checkpoint
    ) == context.failure_signature

    with pytest.raises(
        recovery.SemanticRecoveryExhausted,
        match="will not be billed again",
    ):
        build_concepts._persist_semantic_recovery_dispatch_started(
            db, job, copy.deepcopy(job.generation_checkpoint), context
        )
    db.refresh(job)
    assert len(job.generation_checkpoint[
        "semantic_recovery_dispatches"
    ]["attempts"]) == 1


def test_semantic_repair_dispatch_claim_is_atomic_across_workers(
    db,
    first_chapter,
    monkeypatch,
):
    chapter = db.get(models.Chapter, first_chapter["id"])
    job = models.UploadJob(
        module="build_concepts",
        upload_type="document",
        learning_kind="post",
        filename="atomic-repair-dispatch.mmd",
        mmd_text="## Source Topic\nVerified source wording.",
        status="converted",
    )
    db.add(job)
    db.flush()
    stage = build_concepts.generation._make_concept_checkpoint(
        "pre_type_assignment",
        records=[_row("Unsupported Concept", topic="Source Topic")],
        question_task_inventory={"items": [], "stats": {}},
        mined_types={"types": []},
        method_row_snapshot=[],
    )
    job.generation_checkpoint = build_concepts._merge_generation_checkpoint_history(
        {},
        stage,
        fingerprint=build_concepts._generation_checkpoint_fingerprint(
            job, chapter
        ),
        target_identity=build_concepts._generation_target_identity(chapter),
        target_chapter_id=chapter.id,
    )
    db.commit()
    job_id = job.id
    failure = ValueError(
        "unsupported concept failed semantic validation at row_index=0"
    )
    monkeypatch.setattr(
        build_concepts.drive_checkpoints,
        "schedule_checkpoint_backup",
        lambda *_args, **_kwargs: None,
    )

    worker_one = SessionLocal()
    worker_two = SessionLocal()
    try:
        first_job = worker_one.get(models.UploadJob, job_id)
        second_job = worker_two.get(models.UploadJob, job_id)
        assert first_job is not None
        assert second_job is not None
        first_checkpoint = copy.deepcopy(first_job.generation_checkpoint)
        second_checkpoint = copy.deepcopy(second_job.generation_checkpoint)
        assert first_checkpoint == second_checkpoint
        context = recovery.RecoveryContext(
            attempt=1,
            failure=failure,
            assessment=recovery.classify_failure(failure),
            failure_signature=recovery.failure_signature(
                failure, first_checkpoint
            ),
        )

        issue_key = build_concepts._persist_semantic_recovery_dispatch_started(
            worker_one,
            first_job,
            first_checkpoint,
            context,
        )

        # Worker two read the same pre-claim checkpoint. Reproduce the race
        # window after that read so its optimistic UPDATE, rather than an
        # in-memory duplicate check, must reject the stale claim.
        real_refresh = worker_two.refresh
        refresh_calls = 0

        def refresh_after_racing_update(instance, *args, **kwargs):
            nonlocal refresh_calls
            refresh_calls += 1
            if refresh_calls == 1:
                return None
            return real_refresh(instance, *args, **kwargs)

        monkeypatch.setattr(worker_two, "refresh", refresh_after_racing_update)
        with pytest.raises(
            recovery.SemanticRecoveryExhausted,
            match="another worker already claimed",
        ):
            build_concepts._persist_semantic_recovery_dispatch_started(
                worker_two,
                second_job,
                second_checkpoint,
                context,
            )
    finally:
        worker_one.close()
        worker_two.close()

    db.expire_all()
    persisted = db.get(models.UploadJob, job_id)
    assert persisted is not None
    attempts = persisted.generation_checkpoint[
        "semantic_recovery_dispatches"
    ]["attempts"]
    assert len(attempts) == 1
    assert attempts[0]["issue_key"] == issue_key
    assert attempts[0]["status"] == "request_started"


def test_upload_pre_learning_creates_repaired_draft_and_continues_same_run(
    db,
    first_chapter,
    monkeypatch,
):
    job = models.UploadJob(
        module="build_concepts",
        upload_type="document",
        learning_kind="pre",
        filename="same-run-pre-recovery.mmd",
        mmd_text=(
            "## Target Topic\nThe target chapter introduces proportional "
            "reasoning from established arithmetic foundations."
        ),
        status="converted",
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    target_row = _row(
        "Proportional Reasoning",
        topic="Target Topic",
        topic_id="TOPIC-0001",
    )
    post_checkpoint = build_concepts.generation._make_concept_checkpoint(
        "pre_type_assignment",
        records=[target_row],
        question_task_inventory={"items": [], "stats": {}},
        mined_types={"types": []},
        method_row_snapshot=[],
    )
    concept_calls = 0
    pre_calls = 0

    def concepts_from_mmd(
        *_args,
        artifacts=None,
        resume_checkpoint=None,
        checkpoint_callback=None,
        **_kwargs,
    ):
        nonlocal concept_calls
        concept_calls += 1
        if concept_calls == 1:
            assert checkpoint_callback is not None
            checkpoint_callback(post_checkpoint)
        else:
            restored = (
                build_concepts.generation
                ._newest_compatible_concept_checkpoint(
                    resume_checkpoint,
                    allowed_stages=(
                        build_concepts.generation
                        ._POST_CONCEPT_CHECKPOINT_STAGES
                    ),
                )
            )
            assert restored is not None
            assert restored["stage"] == "pre_type_assignment"
        if artifacts is not None:
            artifacts["question_task_inventory"] = {
                "items": [],
                "stats": {},
            }
            artifacts["mined_types"] = {"types": []}
        return [copy.deepcopy(target_row)]

    prerequisite_row = _row(
        "Equivalent Fraction Foundations",
        topic="Arithmetic Foundations (Pre-Learning)",
        topic_id="",
        details=(
            "Description: Learners should already recognize equivalent "
            "fractions. // Misconception/ Error Analysis: Misconceptions: "
            "Students may treat numerator and denominator independently.; "
            "Error Analysis: Students may scale only one part of a fraction."
        ),
    )

    def pre_learning_from_rows(
        _rows,
        *,
        resume_checkpoint=None,
        checkpoint_callback=None,
        **_kwargs,
    ):
        nonlocal pre_calls
        pre_calls += 1
        if pre_calls == 1:
            raise RuntimeError(
                "live pre-learning derivation returned no topics"
            )
        restored = (
            build_concepts.generation
            ._newest_compatible_concept_checkpoint(
                resume_checkpoint,
                allowed_stages=(
                    build_concepts.generation
                    ._PRE_DERIVATION_CHECKPOINT_STAGES
                ),
            )
        )
        assert restored is not None
        assert restored["stage"] == "pre_derivation_draft"
        assert (
            restored["pre_draft"]["topics"][0]["concepts"][0][
                "concept_name"
            ]
            == "Equivalent Fraction Foundations"
        )
        assert checkpoint_callback is not None
        checkpoint_callback(
            build_concepts.generation._make_concept_checkpoint(
                "pre_learner_analysis",
                records=[prerequisite_row],
                base_records=[target_row],
            )
        )
        return [copy.deepcopy(prerequisite_row)]

    repaired_pre_map = {
        "topics": [
            {
                "topic_name": f"Foundation Area {topic_index}",
                "concepts": [
                    {
                        "concept_name": (
                            "Equivalent Fraction Foundations"
                            if topic_index == 1 and concept_index == 1
                            else (
                                f"Prerequisite {topic_index}."
                                f"{concept_index}"
                            )
                        ),
                        "parent_concept": (
                            f"Foundation Area {topic_index}"
                        ),
                        "concept_description": (
                            "Description: Learners should already command "
                            f"foundation {topic_index}.{concept_index}. // "
                            "Misconception/ Error Analysis: Misconceptions: "
                            "Students may confuse the prerequisite.; Error "
                            "Analysis: Students may apply the prerequisite "
                            "in the wrong order."
                        ),
                        "tag": "NU",
                    }
                    for concept_index in range(1, 6)
                ],
            }
            for topic_index in range(1, 5)
        ],
    }

    monkeypatch.setattr(
        build_concepts.config, "use_live_generation", lambda: True)
    monkeypatch.setattr(
        build_concepts.generation, "concepts_from_mmd", concepts_from_mmd)
    monkeypatch.setattr(
        build_concepts.generation,
        "pre_learning_from_rows",
        pre_learning_from_rows,
    )
    monkeypatch.setattr(
        build_concepts.generation,
        "_openai_json",
        lambda *_args, **kwargs: {
            "blocked": False,
            "pre_map": repaired_pre_map,
        } if kwargs.get("purpose") == "pre_learning" else pytest.fail(
            "unexpected recovery purpose"
        ),
    )
    monkeypatch.setattr(
        build_concepts,
        "_deposit_and_publish_concepts",
        lambda *_args, **_kwargs: (
            [],
            [],
            {
                "written": 0,
                "sources_updated": 0,
                "parent_column": True,
            },
        ),
    )
    monkeypatch.setattr(
        build_concepts.drive_checkpoints,
        "schedule_checkpoint_backup",
        lambda *_args, **_kwargs: None,
    )

    result = build_concepts.generate_pre_learning_from_upload(
        db,
        job.id,
        first_chapter["id"],
    )

    assert result["job_id"] == job.id
    assert concept_calls == 2
    assert pre_calls == 2
    db.expire_all()
    completed = db.get(models.UploadJob, job.id)
    assert completed.status == "generated"
    assert completed.generation_checkpoint == {}


def test_upload_pre_learning_repairs_rejected_deposit_row_not_post_row(
    db,
    first_chapter,
    monkeypatch,
):
    job = models.UploadJob(
        module="build_concepts",
        upload_type="document",
        learning_kind="pre",
        filename="same-run-pre-deposit-recovery.mmd",
        mmd_text="## Target Topic\nVerified target chapter source.",
        status="converted",
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    target_row = _row(
        "Target Chapter Concept",
        topic="Target Topic",
        topic_id="TOPIC-0001",
    )
    bad_pre_row = _row(
        "Unsupported Prerequisite",
        topic="Prior Knowledge (Pre-Learning)",
        topic_id="",
    )
    post_checkpoint = build_concepts.generation._make_concept_checkpoint(
        "pre_type_assignment",
        records=[target_row],
        question_task_inventory={"items": [], "stats": {}},
        mined_types={"types": []},
        method_row_snapshot=[],
    )
    pre_checkpoint = build_concepts.generation._make_concept_checkpoint(
        "pre_learner_analysis",
        records=[bad_pre_row],
        base_records=[target_row],
    )
    concept_calls = 0
    pre_calls = 0
    deposit_calls = 0

    def concepts_from_mmd(
        *_args,
        artifacts=None,
        checkpoint_callback=None,
        **_kwargs,
    ):
        nonlocal concept_calls
        concept_calls += 1
        if concept_calls == 1:
            assert checkpoint_callback is not None
            checkpoint_callback(post_checkpoint)
        if artifacts is not None:
            artifacts["question_task_inventory"] = {
                "items": [],
                "stats": {},
            }
            artifacts["mined_types"] = {"types": []}
        return [copy.deepcopy(target_row)]

    def pre_learning_from_rows(
        _rows,
        *,
        resume_checkpoint=None,
        checkpoint_callback=None,
        **_kwargs,
    ):
        nonlocal pre_calls
        pre_calls += 1
        if pre_calls == 1:
            assert checkpoint_callback is not None
            checkpoint_callback(pre_checkpoint)
            return [copy.deepcopy(bad_pre_row)]
        restored = (
            build_concepts.generation
            ._newest_compatible_concept_checkpoint(
                resume_checkpoint,
                allowed_stages={"pre_learner_analysis"},
            )
        )
        assert restored is not None
        assert restored["records"][0]["concept_title"] == (
            "Verified Prerequisite"
        )
        # The target Post row is retained only as base_records and must not be
        # rewritten by a Pre deposit recovery.
        assert restored["base_records"] == [target_row]
        return copy.deepcopy(restored["records"])

    def deposit(_db, *, records, **_kwargs):
        nonlocal deposit_calls
        deposit_calls += 1
        if deposit_calls == 1:
            assert records[0]["concept_title"] == "Unsupported Prerequisite"
            raise build_concepts.DepositValidationError(
                "deposit validation failed: unsupported prerequisite "
                "at row_index=0"
            )
        assert records[0]["concept_title"] == "Verified Prerequisite"
        return [], [], {
            "written": 0,
            "sources_updated": 0,
            "parent_column": True,
        }

    monkeypatch.setattr(
        build_concepts.config, "use_live_generation", lambda: True)
    monkeypatch.setattr(
        build_concepts.generation, "concepts_from_mmd", concepts_from_mmd)
    monkeypatch.setattr(
        build_concepts.generation,
        "pre_learning_from_rows",
        pre_learning_from_rows,
    )
    monkeypatch.setattr(
        build_concepts.generation,
        "_openai_json",
        lambda *_args, **kwargs: {
            "blocked": False,
            "repairs": [{
                "row_index": 0,
                "topic": "Prior Knowledge (Pre-Learning)",
                "parent_concept": "Prior Knowledge",
                "concept_title": "Verified Prerequisite",
                "concept_details": (
                    "Description: Verified prerequisite knowledge. // "
                    "Misconception/ Error Analysis: Misconceptions: Students "
                    "may confuse this prerequisite.; Error Analysis: Students "
                    "may omit a required prior step."
                ),
                "keywords": "verified, prerequisite",
            }],
        } if kwargs.get("purpose") == "concept_validation" else pytest.fail(
            "unexpected recovery purpose"
        ),
    )
    monkeypatch.setattr(
        build_concepts, "_deposit_and_publish_concepts", deposit)
    monkeypatch.setattr(
        build_concepts.drive_checkpoints,
        "schedule_checkpoint_backup",
        lambda *_args, **_kwargs: None,
    )

    result = build_concepts.generate_pre_learning_from_upload(
        db,
        job.id,
        first_chapter["id"],
    )

    assert result["job_id"] == job.id
    assert concept_calls == 2
    assert pre_calls == 2
    assert deposit_calls == 2

"""Coverage for the diagnostics stop report exported with a staged release."""

from __future__ import annotations

from app.services import concept_run_report as run_report


def _payload(**overrides) -> dict:
    payload = {
        "job_id": 20,
        "checkpoint_stage": "pre_type_assignment",
        "checkpoint_progress": 0.81,
        "release_reason": "Generation failed after creating a durable checkpoint.",
        "summary": {"row_count": 39, "database_uploaded": False},
        "issues": [{
            "code": "GroundingCertificateError",
            "message": "placement contract row 1 is missing",
            "phase": "generation",
            "severity": "error",
            "unit_id": "",
            "qids": [],
            "block_ids": [],
            "details": {
                "exception_type": "GroundingCertificateError",
                "message": "placement contract row 1 is missing",
            },
        }],
    }
    payload.update(overrides)
    return payload


def _checkpoint() -> dict:
    return {
        "stage": "pre_type_assignment",
        "checkpoints": [{
            "stage": "pre_type_assignment",
            "stage_label": "Reusable Types mined; ready for Type assignment",
            "records": [
                {"concept_title": "An earlier concept", "topic": "Topic One"},
                {
                    "concept_title": "Political liberalism",
                    "topic": "The Making of Nationalism in Europe",
                },
            ],
        }],
        "semantic_recovery_dispatches": {
            "attempts": [{
                "stage": "pre_type_assignment",
                "failure_type": "TopologyRepairRequired",
                "status": "applied",
                "issue_key": "d6d90dc8",
            }],
        },
        "human_decisions": {
            "resolutions": [{
                "decision_id": "phase31-ground-c9d8a419",
                "choice": "select_candidate",
                "pending_decision": {
                    "kind": "phase31_source_grounding_semantic_conflict",
                    "phase": "3.1",
                    "conflict": "the claim was not supported",
                    "item": {
                        "unit_id": "CONCEPT-GROUND-0002",
                        "topic": "The Making of Nationalism in Europe",
                    },
                    "agent_review": {"choice": "select_candidate"},
                },
            }],
        },
    }


def _log(resumes: int = 3) -> list[dict]:
    log: list[dict] = []
    for _ in range(resumes):
        log.append({"level": "info", "message": "Concept generation metadata received:"})
        log.append({
            "level": "success",
            "message": "Restored checkpoint stage 'pre_type_assignment' (39 rows).",
        })
        log.append({
            "level": "success",
            "message": "Reused the API-verified Phase 3.2 concept topology.",
        })
    return log


def test_report_states_why_an_integrity_failure_could_not_recover():
    report = run_report.build_run_report(
        _payload(), generation_log=_log(), generation_checkpoint=_checkpoint()
    )

    assert report["stopped"] is True
    assert report["disposition"]["kind"] == "non_semantic"
    assert report["disposition"]["recoverable"] is False
    assert "forbids a semantic repair" in report["disposition"]["consequence"]


def test_report_states_when_a_failure_would_have_recovered():
    payload = _payload(issues=[{
        "code": "TopologyRepairRequired",
        "severity": "error",
        "message": (
            "failed exact source-block grounding before freeze: "
            "CONCEPT-GROUND-0002 (Political liberalism) source claim changed"
        ),
        "phase": "generation",
        "details": {"exception_type": "TopologyRepairRequired"},
    }])

    report = run_report.build_run_report(
        payload, generation_log=_log(), generation_checkpoint=_checkpoint()
    )

    assert report["disposition"]["recoverable"] is True
    assert report["disposition"]["kind"] == "recoverable_semantic"
    # The live scoper resolves a CONCEPT-GROUND id, so no report-local fallback.
    assert report["implicated_rows"] == [{
        "row_index": 1,
        "concept_title": "Political liberalism",
        "topic": "The Making of Nationalism in Europe",
        "resolved_by": "semantic_recovery",
    }]


def test_report_marks_a_row_only_it_could_resolve():
    report = run_report.build_run_report(
        _payload(), generation_log=_log(), generation_checkpoint=_checkpoint()
    )

    # "row 1" carries no separator, so the live repair scoper finds nothing and
    # the report says which resolver actually named the row.
    assert [row["resolved_by"] for row in report["implicated_rows"]] == [
        "report_row_scan"
    ]
    assert report["implicated_rows"][0]["concept_title"] == "Political liberalism"


def test_report_flags_resumes_that_never_advanced():
    report = run_report.build_run_report(
        _payload(), generation_log=_log(resumes=5), generation_checkpoint=_checkpoint()
    )

    assert report["resumes"]["resume_count"] == 5
    assert report["resumes"]["stage_repeat_count"] == 5
    assert report["resumes"]["final_segment_cache_reuse"] == {"reused": 1}
    assert "without advancing" in run_report.render_run_report(report)


def test_report_refuses_to_guess_an_unclassified_exception():
    payload = _payload(issues=[{
        "code": "SomeUnknownError",
        "severity": "error",
        "message": "grounding went wrong",
        "phase": "generation",
        "details": {"exception_type": "SomeUnknownError"},
    }])

    disposition = run_report.build_run_report(
        payload, generation_log=[], generation_checkpoint={}
    )["disposition"]

    # Reporting an unresolved disposition beats classifying it as a builtin the
    # orchestration boundary never saw.
    assert disposition["resolved"] is False
    assert "cannot be recomputed" in disposition["reason"]
    assert "recoverable" not in disposition


def test_report_carries_history_and_log_pointers():
    report = run_report.build_run_report(
        _payload(), generation_log=_log(), generation_checkpoint=_checkpoint()
    )

    assert report["recovery_history"] == [{
        "stage": "pre_type_assignment",
        "failure_type": "TopologyRepairRequired",
        "status": "applied",
        "started_at": None,
        "completed_at": None,
        "issue_key": "d6d90dc8",
    }]
    assert report["decision_history"][0]["unit_id"] == "CONCEPT-GROUND-0002"
    assert report["decision_history"][0]["resolved_by"] == "agent"
    assert all(
        "log_index" in pointer for pointer in report["log_pointers"]
    )


def test_a_clean_release_reports_no_stop():
    report = run_report.build_run_report(
        _payload(issues=[]), generation_log=_log(), generation_checkpoint=_checkpoint()
    )

    assert report["stopped"] is False
    assert report["outcome"] == "completed"
    assert "no terminal failure was recorded" in run_report.render_run_report(
        report
    )


def _completed_payload(records: list[dict] | None = None) -> dict:
    payload = _payload(issues=[])
    payload["release_reason"] = (
        "Generation completed and was staged for explicit publication."
    )
    payload["summary"] = {"row_count": 2, "database_uploaded": True}
    payload["records"] = records or []
    return payload


def _grounded_row(**overrides) -> dict:
    row = {
        "concept_title": "A verified concept",
        "topic": "Topic One",
        "_source_grounding_contract": "api-verified-source-block-ids",
        "_source_grounding_confidence": 0.99,
        "_source_block_ids": ["BLK-0001"],
    }
    row.update(overrides)
    return row


def test_completed_run_is_not_silent_about_what_it_accepted():
    report = run_report.build_run_report(
        _completed_payload([_grounded_row()]),
        generation_log=_log(resumes=1),
        generation_checkpoint=_checkpoint(),
    )
    rendered = run_report.render_run_report(report)

    assert report["outcome"] == "completed"
    assert report["stopped"] is False
    assert "ACCEPTED WITH RISK" in rendered
    assert "Completing is not the same as being correct" in rendered


def test_report_names_rows_that_were_not_independently_grounded():
    payload = _completed_payload([
        _grounded_row(),
        _grounded_row(
            concept_title="A deterministically grounded concept",
            _source_grounding_contract="topic-bounded-deterministic",
        ),
        _grounded_row(
            concept_title="A concept with no evidence",
            _source_block_ids=[],
        ),
        _grounded_row(
            concept_title="A concept in the review band",
            _source_grounding_confidence=0.905,
        ),
    ])

    grounding = run_report.build_run_report(
        payload, generation_log=[], generation_checkpoint={}
    )["accepted_with_risk"]["grounding"]

    assert grounding["grounding_metadata_present"] is True
    assert [
        row["concept_title"]
        for row in grounding["rows_without_independent_grounding"]
    ] == ["A deterministically grounded concept"]
    assert [
        row["concept_title"] for row in grounding["rows_without_source_evidence"]
    ] == ["A concept with no evidence"]
    assert [
        row["concept_title"] for row in grounding["rows_in_human_review_band"]
    ] == ["A concept in the review band"]


def test_report_says_when_there_was_no_grounding_metadata_to_inspect():
    # A checkpoint-staged release carries public fields only.  Saying so beats
    # implying every row passed an inspection that never ran.
    grounding = run_report.build_run_report(
        _completed_payload([{"concept_title": "Public only", "topic": "T"}]),
        generation_log=[],
        generation_checkpoint={},
    )["accepted_with_risk"]["grounding"]

    assert grounding["grounding_metadata_present"] is False
    assert grounding["inspected_rows"] == 0
    assert "could not be inspected" in grounding["note"]


def test_report_surfaces_validation_errors_that_survived_repair():
    log = _log(resumes=1) + [
        {
            "level": "warning",
            "message": "final: keeping best output with 2 validation error(s).",
        },
        {
            "level": "warning",
            "message": (
                "  unrepaired [rich_text_format] row 3 'A malformed concept': "
                "'broken'"
            ),
        },
    ]

    accepted = run_report.build_run_report(
        _completed_payload([_grounded_row()]),
        generation_log=log,
        generation_checkpoint={},
    )["accepted_with_risk"]

    assert [row["code"] for row in accepted["unrepaired_validation_errors"]] == [
        "",
        "rich_text_format",
    ]
    assert accepted["unrepaired_validation_errors"][0]["error_count"] == 2
    assert accepted["unrepaired_validation_errors"][1]["concept_title"] == (
        "A malformed concept"
    )


def test_report_counts_unattended_resolutions_and_content_rewrites():
    log = _log(resumes=1) + [
        {"level": "info", "message": "final: repaired 1 row(s) on attempt 1."},
        {
            "level": "warning",
            "message": "Retrying specific learner analysis for 3 unresolved concept(s).",
        },
    ]

    accepted = run_report.build_run_report(
        _completed_payload([_grounded_row()]),
        generation_log=log,
        generation_checkpoint=_checkpoint(),
    )["accepted_with_risk"]

    assert accepted["autonomous_decision_count"] == 1
    assert accepted["human_decision_count"] == 0
    assert accepted["content_rewrites"] == {
        "validation_repairs": 1,
        "learner_analysis_retries": 3,
    }


def test_a_clean_completed_run_says_nothing_was_flagged():
    rendered = run_report.render_run_report(
        run_report.build_run_report(
            _completed_payload([_grounded_row()]),
            generation_log=[],
            generation_checkpoint={},
        )
    )

    assert "Nothing flagged" in rendered


def test_report_surfaces_subtopic_labels_cleared_as_layout_artifacts():
    payload = _completed_payload([_grounded_row()])
    payload["question_task_inventory"] = {"items": [
        {
            "qid": "QINV-0011.1",
            "_semantic_subtopic_boundary_artifact": "FIG-00014",
            "source_location_topic_title": "The Making of Germany and Italy",
        },
        {"qid": "QINV-0012"},
    ]}

    report = run_report.build_run_report(
        payload, generation_log=[], generation_checkpoint={}
    )
    cleared = report["accepted_with_risk"]["cleared_subtopic_labels"]

    assert cleared == [{
        "qid": "QINV-0011.1",
        "figure_id": "FIG-00014",
        "topic": "The Making of Germany and Italy",
    }]
    rendered = run_report.render_run_report(report)
    assert "subtopic labels cleared as layout artifacts: 1" in rendered
    assert "split from FIG-00014 by a page boundary" in rendered


def test_report_is_quiet_when_no_subtopic_label_was_cleared():
    payload = _completed_payload([_grounded_row()])
    payload["question_task_inventory"] = {"items": [{"qid": "QINV-0012"}]}

    report = run_report.build_run_report(
        payload, generation_log=[], generation_checkpoint={}
    )

    assert report["accepted_with_risk"]["cleared_subtopic_labels"] == []
    assert "subtopic labels cleared" not in run_report.render_run_report(report)

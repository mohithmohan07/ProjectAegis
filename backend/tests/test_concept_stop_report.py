"""Coverage for the diagnostics stop report exported with a staged release."""

from __future__ import annotations

from app.services import concept_stop_report as stop_report


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
    report = stop_report.build_stop_report(
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

    report = stop_report.build_stop_report(
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
    report = stop_report.build_stop_report(
        _payload(), generation_log=_log(), generation_checkpoint=_checkpoint()
    )

    # "row 1" carries no separator, so the live repair scoper finds nothing and
    # the report says which resolver actually named the row.
    assert [row["resolved_by"] for row in report["implicated_rows"]] == [
        "report_row_scan"
    ]
    assert report["implicated_rows"][0]["concept_title"] == "Political liberalism"


def test_report_flags_resumes_that_never_advanced():
    report = stop_report.build_stop_report(
        _payload(), generation_log=_log(resumes=5), generation_checkpoint=_checkpoint()
    )

    assert report["resumes"]["resume_count"] == 5
    assert report["resumes"]["stage_repeat_count"] == 5
    assert report["resumes"]["final_segment_cache_reuse"] == {"reused": 1}
    assert "without advancing" in stop_report.render_stop_report(report)


def test_report_refuses_to_guess_an_unclassified_exception():
    payload = _payload(issues=[{
        "code": "SomeUnknownError",
        "severity": "error",
        "message": "grounding went wrong",
        "phase": "generation",
        "details": {"exception_type": "SomeUnknownError"},
    }])

    disposition = stop_report.build_stop_report(
        payload, generation_log=[], generation_checkpoint={}
    )["disposition"]

    # Reporting an unresolved disposition beats classifying it as a builtin the
    # orchestration boundary never saw.
    assert disposition["resolved"] is False
    assert "cannot be recomputed" in disposition["reason"]
    assert "recoverable" not in disposition


def test_report_carries_history_and_log_pointers():
    report = stop_report.build_stop_report(
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
    report = stop_report.build_stop_report(
        _payload(issues=[]), generation_log=_log(), generation_checkpoint=_checkpoint()
    )

    assert report["stopped"] is False
    assert "No terminal failure" in stop_report.render_stop_report(report)

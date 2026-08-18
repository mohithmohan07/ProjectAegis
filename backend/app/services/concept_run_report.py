"""Explain, in one place, how a Build Concepts run ended and what it accepted.

A diagnostics export already carries the log, the checkpoint, the payload and
the source.  Answering the two questions that matter from those still means
correlating a terminal message against the recovery dispatch ledger, the
resolution history and several resume segments of the log by hand.

**Why did it stop?**  The decisive fact is usually not the message but its
disposition: ``semantic_recovery.classify_failure`` types every
``GroundingCertificateError`` as an integrity failure it is forbidden to
repair, so a run carrying one stops outright while a recoverable failure of
similar wording would have retried.  This module recomputes that verdict,
resolves the failure to the rows it implicates, and reports the stage each
resume restarted from -- the signature of a poisoned durable cache rather than
a one-off rejection.

**What did it accept?**  Completing is not the same as being correct.  A run
can deposit rows the validator gave up repairing, rows grounded deterministically
rather than by independent review, rows with no source evidence, and semantic
conflicts an agent resolved unattended.  None of that raises an error, so the
report states it plainly for a completed run and a stopped one alike.

Everything here is derived from already-saved state.  Nothing is inferred from
a live exception object, so the report is reproducible from the archive alone.
"""
from __future__ import annotations

import copy
import re
from typing import Any, Mapping, Sequence

from . import grounding_certificate
from . import early_semantic_gate as early_gate
from . import semantic_recovery


_BARE_ROW_RE = re.compile(r"\brow\s+(\d+)\b", re.IGNORECASE)
_RESTORED_RE = re.compile(r"restored checkpoint stage '([^']+)'", re.IGNORECASE)
_RESUME_MARKER = "concept generation metadata received"
_CHECKPOINT_MARKER = "saved durable checkpoint"
_PAUSE_MARKER = "saved the semantic decision before resolution"
_REUSE_MARKERS = ("reused ", "cached the ", "discarded the ")
_MAX_LOG_POINTERS = 60


def _normal(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _message_of(entry: object) -> str:
    return _normal(entry.get("message")) if isinstance(entry, Mapping) else ""


def _terminal_issue(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return the error that ended the run, preferring a real exception."""

    issues = [row for row in payload.get("issues") or [] if isinstance(row, Mapping)]
    errors = [row for row in issues if _normal(row.get("severity")) == "error"]
    for row in errors:
        if (row.get("details") or {}).get("exception_type"):
            return copy.deepcopy(dict(row))
    if errors:
        return copy.deepcopy(dict(errors[0]))
    return {}


# Only the exception types the orchestration boundary actually classifies.  An
# unresolved name is reported as such rather than silently classified as an
# unrelated builtin.
_FAILURE_CLASSES: dict[str, type[BaseException]] = {
    "GroundingCertificateError": grounding_certificate.GroundingCertificateError,
    "ProviderResponseContractError": semantic_recovery.ProviderResponseContractError,
    # Declared in build_concepts as a RuntimeError subclass. Named here rather
    # than imported, so the report never pulls the generation stack into an
    # export, and so an unattended stop reports a disposition instead of
    # "cannot be recomputed".
    "UnattendedDecisionUnavailable": RuntimeError,
    "SemanticResolutionCyclesExhausted": RuntimeError,
    "ValueError": ValueError,
    "RuntimeError": RuntimeError,
    "OSError": OSError,
    "IOError": OSError,
}


def _rebuild_failure(
    exception_type: str, message: str,
) -> tuple[BaseException | None, bool]:
    if exception_type == "TopologyRepairRequired":
        return early_gate.TopologyRepairRequired(message, decision_id=""), True
    klass = _FAILURE_CLASSES.get(exception_type)
    if klass is None:
        return None, False
    try:
        return klass(message), True
    except Exception:
        return None, False


def _disposition(issue: Mapping[str, Any]) -> dict[str, Any]:
    """Recompute why the orchestration boundary did or did not recover."""

    exception_type = _normal((issue.get("details") or {}).get("exception_type"))
    message = _normal(issue.get("message"))
    if not exception_type and not message:
        return {"resolved": False, "reason": "no terminal failure was recorded"}
    if exception_type == "HumanDecisionRequired":
        return {
            "resolved": True,
            "kind": semantic_recovery.FailureKind.HUMAN_DECISION.value,
            "recoverable": False,
            "reason": "a semantic choice is waiting for human input",
            "recomputed_at_export": True,
        }
    failure, resolved = _rebuild_failure(exception_type, message)
    if failure is None:
        return {
            "resolved": False,
            "reason": (
                f"exception type {exception_type or '<missing>'!r} is not one "
                "of the classified orchestration failures; its disposition "
                "cannot be recomputed from saved state alone"
            ),
        }
    assessment = semantic_recovery.classify_failure(failure)
    return {
        "resolved": resolved,
        "kind": assessment.kind.value,
        "recoverable": assessment.recoverable,
        "reason": assessment.reason,
        "recomputed_at_export": True,
        "consequence": (
            "the run could retry this failure under the bounded recovery budget"
            if assessment.recoverable
            else "the run stopped; this disposition forbids a semantic repair"
        ),
    }


def _stage_checkpoint(checkpoint: Mapping[str, Any]) -> dict[str, Any]:
    entries = [
        row for row in checkpoint.get("checkpoints") or []
        if isinstance(row, Mapping) and row.get("records")
    ]
    if not entries:
        return {}
    stage = _normal(checkpoint.get("stage"))
    for row in reversed(entries):
        if _normal(row.get("stage")) == stage:
            return dict(row)
    return dict(entries[-1])


def _implicated_rows(
    issue: Mapping[str, Any], checkpoint: Mapping[str, Any],
) -> list[dict[str, Any]]:
    stage = _stage_checkpoint(checkpoint)
    records = [row for row in stage.get("records") or [] if isinstance(row, Mapping)]
    if not records:
        return []
    message = _normal(issue.get("message"))
    if not message:
        return []
    failure, _resolved = _rebuild_failure(
        _normal((issue.get("details") or {}).get("exception_type")) or "ValueError",
        message,
    )
    if failure is None:
        failure = ValueError(message)
    try:
        indexes = tuple(semantic_recovery.implicated_row_indexes(stage, failure))
    except Exception:
        indexes = ()
    resolved_by = "semantic_recovery"
    if not indexes:
        # Report-local fallback.  ``_ROW_INDEX_RE`` requires "row=N"/"row: N",
        # so a bare "row N" diagnostic resolves to nothing for the live repair
        # scoper.  A read-only report may parse it, and saying which resolver
        # found the row tells you whether a bounded repair could have been
        # scoped to it at all.
        indexes = tuple(sorted({
            int(match.group(1))
            for match in _BARE_ROW_RE.finditer(message)
            if 0 <= int(match.group(1)) < len(records)
        }))
        resolved_by = "report_row_scan" if indexes else "unresolved"
    return [
        {
            "row_index": index,
            "concept_title": _normal(
                records[index].get("concept_title")
                or records[index].get("concept")
            ),
            "topic": _normal(records[index].get("topic")),
            "resolved_by": resolved_by,
        }
        for index in indexes
        if 0 <= index < len(records)
    ]


def _resume_segments(log: Sequence[Any]) -> list[int]:
    return [
        index
        for index, entry in enumerate(log)
        if _RESUME_MARKER in _message_of(entry).casefold()
    ]


def _log_pointers(log: Sequence[Any], terminal_message: str) -> list[dict[str, Any]]:
    """Return the log rows worth reading first, with their exact indexes."""

    wanted = terminal_message.casefold()
    pointers: list[dict[str, Any]] = []
    for index, entry in enumerate(log):
        if not isinstance(entry, Mapping):
            continue
        message = _message_of(entry)
        lowered = message.casefold()
        level = _normal(entry.get("level"))
        reason = ""
        if wanted and wanted in lowered:
            reason = "terminal failure"
        elif level == "error":
            reason = "error"
        elif _PAUSE_MARKER in lowered:
            reason = "paused for a decision"
        elif "semantic recovery attempt" in lowered:
            reason = "semantic recovery"
        elif _CHECKPOINT_MARKER in lowered:
            reason = "durable checkpoint"
        if not reason:
            continue
        pointers.append({
            "log_index": index,
            "level": level,
            "reason": reason,
            "ts": entry.get("ts"),
            "message": message[:400],
        })
    if len(pointers) <= _MAX_LOG_POINTERS:
        return pointers
    # Keep the newest rows: a stopped run is diagnosed from its tail.
    return pointers[-_MAX_LOG_POINTERS:]


def _restored_stages(log: Sequence[Any]) -> list[str]:
    """Return the stage each resume restored from.

    A terminal failure is captured into the release payload rather than logged,
    so counting the message itself proves nothing.  The stage a run restarts
    from is logged every time, and the same stage restored again and again is
    the durable signal that resumes are not advancing.
    """

    out: list[str] = []
    for entry in log:
        match = _RESTORED_RE.search(_message_of(entry))
        if match:
            out.append(match.group(1))
    return out


def _final_segment_reuse(log: Sequence[Any]) -> dict[str, int]:
    """Count durable-cache reuse in the last resume segment.

    A run that reuses everything and still fails the same way is failing on
    cached material, not on fresh model output.
    """

    segments = _resume_segments(log)
    start = segments[-1] if segments else 0
    counts: dict[str, int] = {}
    for entry in list(log)[start:]:
        lowered = _message_of(entry).casefold()
        for marker in _REUSE_MARKERS:
            if lowered.startswith(marker):
                key = marker.strip()
                counts[key] = counts.get(key, 0) + 1
    return counts


def _recovery_history(checkpoint: Mapping[str, Any]) -> list[dict[str, Any]]:
    dispatches = checkpoint.get("semantic_recovery_dispatches")
    attempts = (
        dispatches.get("attempts") if isinstance(dispatches, Mapping) else None
    )
    return [
        {
            "stage": _normal(row.get("stage")),
            "failure_type": _normal(row.get("failure_type")),
            "status": _normal(row.get("status")),
            "started_at": row.get("started_at"),
            "completed_at": row.get("completed_at"),
            "issue_key": _normal(row.get("issue_key")),
        }
        for row in attempts or []
        if isinstance(row, Mapping)
    ]


def _decision_history(checkpoint: Mapping[str, Any]) -> list[dict[str, Any]]:
    decisions = checkpoint.get("human_decisions")
    resolutions = (
        decisions.get("resolutions") if isinstance(decisions, Mapping) else None
    )
    out: list[dict[str, Any]] = []
    for row in resolutions or []:
        if not isinstance(row, Mapping):
            continue
        pending = row.get("pending_decision")
        pending = pending if isinstance(pending, Mapping) else {}
        item = pending.get("item")
        item = item if isinstance(item, Mapping) else {}
        review = pending.get("agent_review")
        review = review if isinstance(review, Mapping) else {}
        out.append({
            "decision_id": _normal(row.get("decision_id")),
            "choice": _normal(row.get("choice")),
            "consumed_at": row.get("consumed_at"),
            "kind": _normal(pending.get("kind")),
            "phase": _normal(pending.get("phase")),
            "unit_id": _normal(item.get("unit_id")),
            "topic": _normal(item.get("topic")),
            "resolved_by": "agent" if review else "human",
            "conflict": _normal(pending.get("conflict"))[:400],
        })
    return out


_UNREPAIRED_RE = re.compile(
    r"unrepaired \[(?P<code>[^\]]+)\] row (?P<row>\d+) '(?P<title>[^']*)'",
    re.IGNORECASE,
)
_KEPT_BEST_RE = re.compile(
    r"(?P<stage>\w+): keeping best output with (?P<count>\d+) validation error",
    re.IGNORECASE,
)
_REWRITE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("validation_repairs", re.compile(
        r"repaired (\d+) row\(s\) on attempt", re.IGNORECASE)),
    ("learner_analysis_retries", re.compile(
        r"retrying specific learner analysis for (\d+)", re.IGNORECASE)),
    ("recovered_missing_rows", re.compile(
        r"recovered (\d+) missing", re.IGNORECASE)),
    ("granularity_rows_added", re.compile(
        r"added (\d+) source-grounded concept", re.IGNORECASE)),
    ("semantic_recovery_repairs", re.compile(
        r"semantic recovery attempt \d+/\d+ applied", re.IGNORECASE)),
)


def _unrepaired_validation_errors(log: Sequence[Any]) -> list[dict[str, Any]]:
    """Return rows the validator gave up on and deposited anyway.

    ``_validate_and_repair`` logs this explicitly when it exhausts its repair
    budget, so a completed run can still carry rows it knows are malformed.
    """

    out: list[dict[str, Any]] = []
    for index, entry in enumerate(log):
        message = _message_of(entry)
        kept = _KEPT_BEST_RE.search(message)
        if kept:
            out.append({
                "log_index": index,
                "stage": kept.group("stage"),
                "error_count": int(kept.group("count")),
                "code": "",
                "row_index": None,
                "concept_title": "",
            })
            continue
        detail = _UNREPAIRED_RE.search(message)
        if detail:
            out.append({
                "log_index": index,
                "stage": "",
                "error_count": 1,
                "code": detail.group("code"),
                "row_index": int(detail.group("row")),
                "concept_title": detail.group("title"),
            })
    return out


def _content_rewrites(log: Sequence[Any]) -> dict[str, int]:
    """Count the places the pipeline regenerated content rather than kept it."""

    counts: dict[str, int] = {}
    for entry in log:
        message = _message_of(entry)
        for name, pattern in _REWRITE_PATTERNS:
            match = pattern.search(message)
            if not match:
                continue
            amount = int(match.group(1)) if match.groups() else 1
            counts[name] = counts.get(name, 0) + amount
    return counts


def _grounding_review(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Describe how strongly the released rows are actually grounded.

    A release staged from a checkpoint carries public fields only, so say when
    there was no grounding metadata to inspect rather than implying every row
    passed.
    """

    records = [row for row in payload.get("records") or [] if isinstance(row, Mapping)]
    inspected = [
        row for row in records
        if any(str(key).startswith("_source_grounding_") for key in row)
    ]
    if not inspected:
        return {
            "grounding_metadata_present": False,
            "inspected_rows": 0,
            "note": (
                "This payload carries public concept fields only, so per-row "
                "grounding strength could not be inspected."
            ),
        }

    unverified: list[dict[str, Any]] = []
    without_evidence: list[dict[str, Any]] = []
    review_band: list[dict[str, Any]] = []
    for index, row in enumerate(records):
        if not any(str(key).startswith("_source_grounding_") for key in row):
            continue
        identity = {
            "row_index": index,
            "concept_title": _normal(
                row.get("concept_title") or row.get("concept")
            ),
            "topic": _normal(row.get("topic")),
        }
        contract = _normal(row.get("_source_grounding_contract"))
        if contract not in grounding_certificate.VERIFIED_GROUNDING_CONTRACTS:
            unverified.append({**identity, "grounding_contract": contract})
        if not (row.get("_source_block_ids") or []):
            without_evidence.append(identity)
        confidence = row.get("_source_grounding_confidence")
        if confidence is not None and early_gate.confidence_band(
            confidence
        ) == "human_review":
            review_band.append({**identity, "confidence": confidence})
    return {
        "grounding_metadata_present": True,
        "inspected_rows": len(inspected),
        "rows_without_independent_grounding": unverified,
        "rows_without_source_evidence": without_evidence,
        "rows_in_human_review_band": review_band,
    }


def _cleared_subtopic_labels(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return inventory rows whose subtopic label the source contradicted.

    A two-column page can linearize an Activity box after the next subheading,
    splitting it from its own figure.  Phase 3 clears the label rather than
    assert one the source contradicts, and clearing is silent -- so surface it
    here: the row kept its main topic but lost the finer placement signal.
    """

    inventory = payload.get("question_task_inventory")
    items = (
        inventory.get("items") if isinstance(inventory, Mapping) else None
    ) or []
    return [
        {
            "qid": _normal(row.get("qid")),
            "figure_id": _normal(row.get("_semantic_subtopic_boundary_artifact")),
            "topic": _normal(
                row.get("source_location_topic_title") or row.get("topic_hint")
            ),
        }
        for row in items
        if isinstance(row, Mapping)
        and _normal(row.get("_semantic_subtopic_boundary_artifact"))
    ]


def _accepted_with_risk(
    payload: Mapping[str, Any],
    log: Sequence[Any],
    decisions: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Summarize what this run accepted without full independent verification."""

    non_error_issues = [
        {
            "code": _normal(row.get("code")),
            "severity": _normal(row.get("severity")),
            "message": _normal(row.get("message"))[:400],
        }
        for row in payload.get("issues") or []
        if isinstance(row, Mapping) and _normal(row.get("severity")) != "error"
    ]
    routed = [
        row for row in payload.get("type_case_rows") or []
        if isinstance(row, Mapping)
        and _normal(row.get("audit_status")) not in {"", "ready"}
    ]
    return {
        "unrepaired_validation_errors": _unrepaired_validation_errors(log),
        "content_rewrites": _content_rewrites(log),
        "autonomous_decision_count": sum(
            1 for row in decisions if row.get("resolved_by") == "agent"
        ),
        "human_decision_count": sum(
            1 for row in decisions if row.get("resolved_by") == "human"
        ),
        "cleared_subtopic_labels": _cleared_subtopic_labels(payload),
        "non_error_issues": non_error_issues,
        "type_case_rows_needing_review": [
            {
                "type_id": _normal(row.get("type_id")),
                "case_id": _normal(row.get("case_id")),
                "audit_status": _normal(row.get("audit_status")),
                "error": _normal(row.get("error"))[:300],
            }
            for row in routed
        ],
        "grounding": _grounding_review(payload),
    }


def _pre_learning(
    pre_map: Mapping[str, Any] | None,
    coverage: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """What the PRE lane produced, readable without opening the JSON.

    §6.6: the report had zero lane awareness, so a run carrying a
    Pre-Learning map read exactly like one that never built one. This is
    counting and identities only — how many pre-topics and pre-concepts
    the run built, what each is called, which of them carry an analysis
    section, how many carry a review flag, and whether the map was
    refused. It never says whether the Pre map is any good; the flags it
    counts are the critic's and the validator's, already recorded.
    """
    source = dict(pre_map or {})
    rows = [row for row in source.get("rows") or [] if isinstance(row, Mapping)]
    topics = [
        topic for topic in source.get("topics") or []
        if isinstance(topic, Mapping)
    ]
    analysis = dict(source.get("analysis") or {})
    flags = source.get("review_flags") or {}
    coverage_pre = dict(
        (coverage or {}).get("summary", {}).get("pre_learning") or {}
    )
    # Silent only when the Pre lane has nothing to say. A run whose map
    # snapshot is missing or empty while its capture holds prerequisites
    # is precisely the state in which prerequisites were lost, and the
    # ledger already marks it incomplete — so the report must not go
    # quiet on it (R4).
    owed = any((
        (coverage_pre.get("prerequisites") or {}).get("unaccounted"),
        (coverage_pre.get("analysis_items") or {}).get("unaccounted"),
        (coverage_pre.get("needed_for_links") or {}).get("unresolved"),
        coverage_pre.get("rows_missing_learner_analysis"),
    ))
    if not rows and not topics and not source.get("refused") and not owed:
        return {}
    return {
        "refused": _normal(source.get("refused")),
        "pre_topics": [
            {
                "pre_topic_id": _normal(topic.get("pre_topic_id")),
                "title": _normal(topic.get("title")),
                "pre_concept_ids": [
                    str(ref) for ref in topic.get("pre_concept_ids") or []
                ],
            }
            for topic in topics
        ],
        "pre_concepts": [
            {
                "pre_concept_id": _normal(row.get("_pre_concept_id")),
                "concept_title": _normal(row.get("concept_title")),
                "topic": _normal(row.get("topic")),
                "prerequisite_ids": [
                    _normal(entry.get("prerequisite_id"))
                    for entry in row.get("_aegis_pre_prerequisites") or []
                    if isinstance(entry, Mapping)
                ],
                "analysis_item_ids": [
                    str(item_id)
                    for item_id in row.get("_aegis_analysis_allotments") or []
                ],
                "needed_for_count": len(row.get("_aegis_needed_for") or []),
                "review_flag_count": len(row.get("review_flags") or []),
            }
            for row in rows
        ],
        "analysis_item_count": len([
            item for item in analysis.get("inventory") or []
            if isinstance(item, Mapping)
        ]),
        "allotted_item_count": len(analysis.get("allotments") or {}),
        "flagged_pre_concept_count": len(flags),
        "validation_codes": sorted({
            _normal(finding.get("code"))
            for finding in source.get("validation") or []
            if isinstance(finding, Mapping) and _normal(finding.get("code"))
        }),
        "coverage": coverage_pre,
    }


def build_run_report(
    payload: Mapping[str, Any],
    *,
    generation_log: Sequence[Any] | None = None,
    generation_checkpoint: Mapping[str, Any] | None = None,
    dropped_furniture: Mapping[str, Any] | None = None,
    pre_map: Mapping[str, Any] | None = None,
    coverage: Mapping[str, Any] | None = None,
    release_lane: str = "post",
) -> dict[str, Any]:
    """Return the reproducible explanation of how this run ended.

    ``dropped_furniture`` carries both source lanes' verbatim dropped
    lines ({"chapter_reading": [...], "acsd": [...]}) so the report can
    list what was dropped and what it said (R4) — never a bare count.
    ``pre_map`` is the run's recorded Phase 03 Pre-Learning map
    (``source.phase3-prelearn-map.json``) and ``coverage`` the coverage
    ledger built beside it, so the report can surface the Pre lane the
    way it already surfaces the Post one. Both optional: a run without a
    Pre lane reports no Pre section at all.

    ``release_lane`` names which of the job's two staged releases
    ``payload`` is — the report is generated per lane, and a reviewer
    holding the Pre diagnostics must not have to infer which output they
    are reading.
    """

    log = list(generation_log or [])
    checkpoint = dict(generation_checkpoint or {})
    issue = _terminal_issue(payload)
    message = _normal(issue.get("message"))
    segments = _resume_segments(log)
    restored = _restored_stages(log)
    summary = payload.get("summary")
    summary = summary if isinstance(summary, Mapping) else {}

    decisions = _decision_history(checkpoint)
    furniture = dict(dropped_furniture or {})
    return {
        "version": "aegis-concept-run-report-1",
        "dropped_furniture": {
            "chapter_reading": [
                str(line)
                for line in furniture.get("chapter_reading") or []
                if str(line or "").strip()
            ],
            "acsd": [
                str(line)
                for line in furniture.get("acsd") or []
                if str(line or "").strip()
            ],
        },
        "stopped": bool(issue),
        "outcome": "stopped" if issue else "completed",
        "release_lane": str(release_lane or "post"),
        "job_id": payload.get("job_id"),
        "stage": _normal(payload.get("checkpoint_stage")),
        "stage_label": _normal(_stage_checkpoint(checkpoint).get("stage_label")),
        "progress": payload.get("checkpoint_progress"),
        "release_reason": _normal(payload.get("release_reason")),
        "released_row_count": summary.get("row_count"),
        "database_uploaded": summary.get("database_uploaded"),
        "terminal_failure": {
            "code": _normal(issue.get("code")),
            "exception_type": _normal(
                (issue.get("details") or {}).get("exception_type")
            ),
            "message": message,
            "phase": _normal(issue.get("phase")),
            "unit_id": _normal(issue.get("unit_id")),
            "qids": list(issue.get("qids") or []),
            "block_ids": list(issue.get("block_ids") or []),
        } if issue else {},
        "disposition": _disposition(issue),
        "implicated_rows": _implicated_rows(issue, checkpoint),
        "resumes": {
            "resume_count": len(segments),
            "segment_start_log_indexes": segments,
            "restored_stages": restored,
            "stage_repeat_count": max(
                (restored.count(stage) for stage in set(restored)), default=0
            ),
            "final_segment_cache_reuse": _final_segment_reuse(log),
        },
        "recovery_history": _recovery_history(checkpoint),
        "decision_history": decisions,
        "accepted_with_risk": _accepted_with_risk(payload, log, decisions),
        "pre_learning": _pre_learning(pre_map, coverage),
        "pending_decision": {
            key: _normal(value)
            for key, value in (payload.get("pending_decision_snapshot") or {}).items()
            if key in {"kind", "phase", "decision_id", "conflict", "diagnosis"}
        },
        "log_pointers": _log_pointers(log, message),
    }


def _render_furniture(report: Mapping[str, Any]) -> list[str]:
    """R4: list dropped furniture with what it said — never a bare count."""

    furniture = report.get("dropped_furniture") or {}
    rows = [
        (lane, str(line))
        for lane in ("chapter_reading", "acsd")
        for line in furniture.get(lane) or []
        if str(line or "").strip()
    ]
    if not rows:
        return []
    lines = ["", "DROPPED FURNITURE"]
    lines.append(
        f"  {len(rows)} line(s) were dropped as running heads, footers, or "
        "page numbers; each is listed verbatim:"
    )
    for lane, line in rows[:60]:
        lines.append(f"  [{lane}] {line}")
    if len(rows) > 60:
        lines.append(
            f"  … {len(rows) - 60} more line(s) in context/run_report.json"
        )
    return lines


def _render_lane(report: Mapping[str, Any]) -> list[str]:
    """Name the lane, and ONLY when it is not the long-standing default.

    A Post report renders byte-identically to every recorded one; a Pre
    report says so on its first line, because a reviewer holding two
    diagnostics archives from one run must be able to tell them apart.
    """

    if str(report.get("release_lane") or "post") == "post":
        return []
    return [
        "Output     : 03/04 — the Phase 03 Pre-Learning outputs "
        "(this run's Post-Learning outputs 01/02 have their own report)",
    ]


def _render_pre_learning(report: Mapping[str, Any]) -> list[str]:
    """Render what the PRE lane produced. Silent when there was none."""

    pre = report.get("pre_learning") or {}
    if not pre:
        return []
    concepts = pre.get("pre_concepts") or []
    topics = pre.get("pre_topics") or []
    lines = ["", "PRE-LEARNING MAP (Phase 03)"]
    if pre.get("refused"):
        lines.append(
            "  REFUSED and not shipped: " + str(pre["refused"])[:300]
        )
        lines.append(
            "  The chapter's finished Post-Learning map is unaffected."
        )
        return lines
    if not concepts and not topics:
        # No map, but the ledger has Pre obligations to report — the one
        # state in which prerequisites are lost. Say so here rather than
        # leave the COVERAGE verdict below unexplained (R4).
        prerequisites = (pre.get("coverage") or {}).get("prerequisites") or {}
        lines.append(
            "  no Pre-Learning map is recorded for this run, and "
            f"{prerequisites.get('unaccounted', 0)} of "
            f"{prerequisites.get('total', 0)} captured prerequisite(s) "
            "reached no pre-concept"
        )
        lines.append(
            "  each one is named in the COVERAGE section below."
        )
        return lines
    with_analysis = [row for row in concepts if row.get("analysis_item_ids")]
    lines.append(
        f"  {len(concepts)} pre-concept(s) across {len(topics)} pre-topic(s), "
        "built from this run's own prerequisite capture"
    )
    lines.append(
        f"  prerequisite analysis: {pre.get('allotted_item_count', 0)}/"
        f"{pre.get('analysis_item_count', 0)} inventory item(s) allotted, "
        f"on {len(with_analysis)} pre-concept(s); the remaining "
        f"{len(concepts) - len(with_analysis)} carry no section, which is "
        "Q1's design and not a gap"
    )
    if pre.get("flagged_pre_concept_count"):
        lines.append(
            f"  pre-concepts carrying a review flag: "
            f"{pre['flagged_pre_concept_count']}"
            + (
                " (validator codes: "
                + ", ".join(pre.get("validation_codes") or [])
                + ")"
                if pre.get("validation_codes") else ""
            )
        )
    for topic in topics:
        members = {
            row["pre_concept_id"]: row for row in concepts
        }
        lines.append(
            f"  [{topic.get('pre_topic_id')}] {topic.get('title')}"
        )
        for ref in topic.get("pre_concept_ids") or []:
            row = members.get(ref) or {}
            marks = []
            if row.get("analysis_item_ids"):
                marks.append(
                    f"{len(row['analysis_item_ids'])} analysis item(s)"
                )
            marks.append(f"{row.get('needed_for_count', 0)} needed-for link(s)")
            if row.get("review_flag_count"):
                marks.append(f"{row['review_flag_count']} flag(s)")
            lines.append(
                f"    {ref} {row.get('concept_title', '')} "
                f"({'; '.join(marks)})"
            )
    return lines


def _render_accepted(report: Mapping[str, Any]) -> list[str]:
    """Render what was accepted without full independent verification."""

    accepted = report.get("accepted_with_risk") or {}
    grounding = accepted.get("grounding") or {}
    lines = ["", "ACCEPTED WITH RISK"]
    quiet = True

    unrepaired = accepted.get("unrepaired_validation_errors") or []
    if unrepaired:
        quiet = False
        lines.append(f"  unrepaired validation errors: {len(unrepaired)}")
        for row in unrepaired[:10]:
            where = (
                f"row {row['row_index']} {row['concept_title']!r}"
                if row.get("row_index") is not None
                else f"stage {row.get('stage')}"
            )
            lines.append(
                f"    [{row.get('code') or 'summary'}] {where} "
                f"(log {row['log_index']})"
            )

    if not grounding.get("grounding_metadata_present"):
        lines.append(f"  grounding: {grounding.get('note')}")
    else:
        for key, label in (
            ("rows_without_independent_grounding",
             "rows not independently grounded"),
            ("rows_without_source_evidence", "rows with no source evidence"),
            ("rows_in_human_review_band", "rows in the human-review band"),
        ):
            rows = grounding.get(key) or []
            if not rows:
                continue
            quiet = False
            lines.append(f"  {label}: {len(rows)}")
            for row in rows[:10]:
                lines.append(
                    f"    row {row['row_index']}: {row['concept_title']}"
                )

    agent = accepted.get("autonomous_decision_count") or 0
    human = accepted.get("human_decision_count") or 0
    if agent or human:
        quiet = False
        lines.append(
            f"  semantic conflicts resolved: {agent} by agent, {human} by human"
        )

    cleared = accepted.get("cleared_subtopic_labels") or []
    if cleared:
        quiet = False
        lines.append(f"  subtopic labels cleared as layout artifacts: {len(cleared)}")
        for row in cleared[:10]:
            lines.append(
                f"    {row['qid']}: split from {row['figure_id']} by a page "
                f"boundary; kept topic {row['topic'] or '<unknown>'}"
            )

    rewrites = accepted.get("content_rewrites") or {}
    if rewrites:
        quiet = False
        lines.append(f"  content rewrites: {rewrites}")

    routed = accepted.get("type_case_rows_needing_review") or []
    if routed:
        quiet = False
        lines.append(f"  Type/Case rows needing review: {len(routed)}")

    issues = accepted.get("non_error_issues") or []
    if issues:
        quiet = False
        lines.append(f"  non-error release issues: {len(issues)}")
        for row in issues[:6]:
            lines.append(f"    [{row['severity']}] {row['code']}: {row['message'][:120]}")

    if quiet:
        lines.append(
            "  Nothing flagged. Every released row carried independently "
            "verified grounding,"
        )
        lines.append(
            "  no validation error survived repair, and no semantic conflict "
            "needed resolving."
        )
    return lines


def render_run_report(report: Mapping[str, Any]) -> str:
    """Render the same facts as the first thing a human opens."""

    lane_line = _render_lane(report)
    if not report.get("stopped"):
        return "\n".join([
            "Project Aegis run report",
            "",
            *lane_line,
            f"Stage      : {report.get('stage')} ({report.get('stage_label')})",
            f"Progress   : {report.get('progress')}",
            f"Released   : {report.get('released_row_count')} row(s); "
            f"database_uploaded={report.get('database_uploaded')}",
            "",
            "OUTCOME",
            "  The run completed; no terminal failure was recorded.",
            "  Completing is not the same as being correct. What this run "
            "accepted without",
            "  full independent verification is below.",
            *_render_accepted(report),
            *_render_pre_learning(report),
            *_render_furniture(report),
            "",
            "Full detail, including exact log indexes, is in "
            "context/run_report.json.",
            "",
        ])
    failure = report.get("terminal_failure") or {}
    disposition = report.get("disposition") or {}
    resumes = report.get("resumes") or {}
    lines = [
        "Project Aegis run report",
        "",
        *lane_line,
        f"Stage      : {report.get('stage')} ({report.get('stage_label')})",
        f"Progress   : {report.get('progress')}",
        f"Released   : {report.get('released_row_count')} row(s); "
        f"database_uploaded={report.get('database_uploaded')}",
        "",
        "WHAT STOPPED THE RUN",
        f"  {failure.get('exception_type') or failure.get('code')}: "
        f"{failure.get('message')}",
        f"  phase: {failure.get('phase') or 'unknown'}",
        "",
        "WHY IT DID NOT RECOVER",
        f"  disposition : {disposition.get('kind', 'unresolved')} "
        f"(recoverable={disposition.get('recoverable')})",
        f"  reason      : {disposition.get('reason')}",
    ]
    if disposition.get("consequence"):
        lines.append(f"  consequence : {disposition['consequence']}")
    rows = report.get("implicated_rows") or []
    lines += ["", "ROWS IMPLICATED"]
    lines += (
        [
            f"  row {row['row_index']}: {row['concept_title']}  [{row['topic']}]"
            f"  (resolved by {row['resolved_by']})"
            for row in rows
        ]
        or ["  none resolved from the diagnostic"]
    )
    if any(row.get("resolved_by") == "report_row_scan" for row in rows):
        lines.append(
            "  NOTE: only this report resolved the row. The live repair scoper "
            "could not, so no bounded row repair was possible."
        )
    repeats = resumes.get("stage_repeat_count") or 0
    lines += [
        "",
        "RESUME HISTORY",
        f"  resumes                      : {resumes.get('resume_count')}",
        f"  stages restored              : {resumes.get('restored_stages')}",
        f"  same stage restored          : {repeats} time(s)",
        f"  final-segment cache reuse    : {resumes.get('final_segment_cache_reuse')}",
    ]
    if repeats > 1:
        lines.append(
            "  NOTE: resumes kept restarting from the same stage without "
            "advancing, which points at durable cached material rather than a "
            "one-off rejection."
        )
    recovery = report.get("recovery_history") or []
    if recovery:
        lines += ["", "SEMANTIC RECOVERY"]
        lines += [
            f"  {row['stage']}: {row['failure_type']} -> {row['status']}"
            for row in recovery
        ]
    decisions = report.get("decision_history") or []
    if decisions:
        lines += ["", f"DECISIONS RESOLVED ({len(decisions)})"]
        lines += [
            f"  {row['phase']} {row['unit_id']} [{row['resolved_by']}] "
            f"{row['choice']}"
            for row in decisions
        ]
    # A stopped run still released rows, so the same quality question applies.
    lines += _render_accepted(report)
    lines += _render_pre_learning(report)
    lines += _render_furniture(report)
    lines += [
        "",
        "Full detail, including exact log indexes, is in "
        "context/run_report.json.",
        "",
    ]
    return "\n".join(lines)

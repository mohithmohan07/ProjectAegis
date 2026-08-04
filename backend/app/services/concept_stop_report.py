"""Explain, in one place, exactly what stopped a Build Concepts run.

A diagnostics export already carries the log, the checkpoint, the payload and
the source.  Reconstructing *why the run stopped* from those still means
correlating a terminal message against the recovery dispatch ledger, the
resolution history and several resume segments of the log by hand.

The decisive fact is usually not the message itself but its disposition:
``semantic_recovery.classify_failure`` types every ``GroundingCertificateError``
as an integrity failure it is forbidden to repair, so a run carrying one stops
outright while a recoverable failure of similar wording would have retried.
This module recomputes that verdict at export time, resolves the failure to the
rows it implicates, and counts how many resumes replayed the same failure --
the signature of a poisoned durable cache rather than a one-off rejection.

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


def build_stop_report(
    payload: Mapping[str, Any],
    *,
    generation_log: Sequence[Any] | None = None,
    generation_checkpoint: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the reproducible explanation of how this run ended."""

    log = list(generation_log or [])
    checkpoint = dict(generation_checkpoint or {})
    issue = _terminal_issue(payload)
    message = _normal(issue.get("message"))
    segments = _resume_segments(log)
    restored = _restored_stages(log)
    summary = payload.get("summary")
    summary = summary if isinstance(summary, Mapping) else {}

    return {
        "version": "aegis-concept-stop-report-1",
        "stopped": bool(issue),
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
        "decision_history": _decision_history(checkpoint),
        "pending_decision": {
            key: _normal(value)
            for key, value in (payload.get("pending_decision_snapshot") or {}).items()
            if key in {"kind", "phase", "decision_id", "conflict", "diagnosis"}
        },
        "log_pointers": _log_pointers(log, message),
    }


def render_stop_report(report: Mapping[str, Any]) -> str:
    """Render the same facts as the first thing a human opens."""

    if not report.get("stopped"):
        return (
            "Project Aegis run stop report\n\n"
            "No terminal failure was recorded for this release.\n"
        )
    failure = report.get("terminal_failure") or {}
    disposition = report.get("disposition") or {}
    resumes = report.get("resumes") or {}
    lines = [
        "Project Aegis run stop report",
        "",
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
    lines += [
        "",
        "Full detail, including exact log indexes, is in "
        "context/stop_report.json.",
        "",
    ]
    return "\n".join(lines)

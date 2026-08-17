"""Unattended Build Concepts release staging.

Generation and publication are deliberately separate:

* every upload generation attempt first stages a portable release payload;
* semantic pauses and ordinary failures release the newest durable rows with
  row-level diagnostics instead of asking the user to choose mid-run;
* downloadable artifacts remain available even when there are zero completed
  concept rows; and
* database mutation happens only through the explicit upload action.

The release payload is stored inside ``UploadJob.question_inventory`` so no
schema migration is required and checkpoint export retains the complete audit.
"""
from __future__ import annotations

import copy
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from sqlalchemy.orm import Session

from .. import models
from . import generation, uploads


RELEASE_VERSION = "aegis-concept-release-1"
RELEASE_KEY = "_aegis_release_output"
RELEASE_STATUS = "released"
RELEASE_ROW_STATUS_FIELD = "_aegis_release_status"
RELEASE_ROW_ERRORS_FIELD = "_aegis_release_errors"
RELEASE_ROW_QIDS_FIELD = "_aegis_release_qids"
RELEASE_ROW_BLOCKS_FIELD = "_aegis_release_block_ids"
RELEASE_ROW_ROUTES_FIELD = "_aegis_release_type_case_routes"
RELEASE_ROW_REFINED_FIELD = "_aegis_release_refined"

_RELEASE_AUDIT_FIELDS = frozenset({
    RELEASE_ROW_STATUS_FIELD,
    RELEASE_ROW_ERRORS_FIELD,
    RELEASE_ROW_QIDS_FIELD,
    RELEASE_ROW_BLOCKS_FIELD,
    RELEASE_ROW_ROUTES_FIELD,
    # The Refiner's per-row mark (docs/aegis-restructure.md §8.3): rides the
    # release for the reviewer's audit, stripped before DB upload.
    RELEASE_ROW_REFINED_FIELD,
    # Fixer-accepted validator codes (Q13, seams F22/F39/F40): a recorded
    # acceptance ships in the release payload for the reviewer's audit and
    # is stripped before DB upload like every other audit field.
    "_fixer_accepted_codes",
    # The Phase 2.2 placement pass's stamped verdicts (place.py): which
    # hub qids and which source figures (block_id + url + caption) the
    # model placed on this row. They ride the release for the reviewer's
    # audit and are stripped before DB upload.
    "_aegis_hub_placements",
    "_aegis_figure_placements",
})

_UNIT_ID_RE = re.compile(
    r"\b(?:TOPOLOGY-CONCEPT|CONCEPT-GROUND)-\d{1,6}\b",
    re.IGNORECASE,
)
_BLOCK_ID_RE = re.compile(r"\bBLK-[A-Za-z0-9_-]+\b")
_QID_RE = re.compile(r"\bQINV-[A-Za-z0-9_.-]+\b")


class ReleaseUnavailableError(ValueError):
    """No staged release exists for the requested operation."""


def _instruction_set_summary(job: models.UploadJob) -> dict[str, Any]:
    """The Architect's assembled set for this job, summarized for the payload.

    Reads the persisted ``source.instruction-set.json`` from the job's
    artifact directory (written at generate time). Empty when no set was
    assembled (legacy runs, pre-learning).
    """
    from . import instruction_architect

    helper = getattr(uploads, "source_artifact_directory", None)
    if not callable(helper):
        return {}
    try:
        stored = instruction_architect.load_instruction_set(
            helper(int(job.id)))
    except Exception:  # noqa: BLE001 - a missing artifact never blocks release
        stored = None
    if not isinstance(stored, dict):
        return {}
    return {
        key: copy.deepcopy(stored.get(key))
        for key in (
            "architect_version",
            "instruction_set_sha256",
            "slots_source",
            "slots",
            "review_flags",
        )
        if key in stored
    }


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _json_safe(raw)
            for key, raw in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_json_safe(raw) for raw in value]
    if isinstance(value, set):
        return sorted(
            (_json_safe(raw) for raw in value),
            key=lambda raw: json.dumps(
                raw, ensure_ascii=False, sort_keys=True, default=str
            ),
        )
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _normal(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _list_strings(value: object) -> list[str]:
    if isinstance(value, (str, bytes)):
        value = [value]
    if not isinstance(value, Iterable):
        return []
    return list(dict.fromkeys(
        _normal(raw) for raw in value if _normal(raw)
    ))


def _type_rows(mined_types: object) -> list[dict[str, Any]]:
    if isinstance(mined_types, Mapping):
        values = mined_types.get("types") or []
    else:
        values = mined_types or []
    return [copy.deepcopy(dict(row)) for row in values if isinstance(row, Mapping)]


def _case_examples(case: Mapping[str, Any]) -> list[dict[str, Any]]:
    helper = getattr(generation, "_case_examples", None)
    if callable(helper):
        try:
            values = helper(dict(case))
            return [
                copy.deepcopy(dict(row))
                for row in values or []
                if isinstance(row, Mapping)
            ]
        except Exception:
            pass
    values = case.get("examples") or case.get("source_examples") or []
    return [
        copy.deepcopy(dict(row))
        for row in values
        if isinstance(row, Mapping)
    ]


def _case_qids(case: Mapping[str, Any]) -> list[str]:
    helper = getattr(generation, "_assignment_case_qids", None)
    if callable(helper):
        try:
            return _list_strings(helper(dict(case)))
        except Exception:
            pass
    qids = _list_strings(
        case.get("source_question_ids")
        or case.get("qids")
        or []
    )
    for example in _case_examples(case):
        qid = _normal(
            example.get("source_question_id")
            or example.get("qid")
        )
        if qid and qid not in qids:
            qids.append(qid)
    return qids


def _definition(row: Mapping[str, Any], *names: str) -> str:
    for name in names:
        value = _normal(row.get(name))
        if value:
            return value
    return ""


def _inventory_items(inventory: object) -> list[dict[str, Any]]:
    if not isinstance(inventory, Mapping):
        return []
    return [
        copy.deepcopy(dict(row))
        for row in inventory.get("items") or []
        if isinstance(row, Mapping)
    ]


def _newest_checkpoint_material(
    checkpoint: object,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any], dict[str, Any]]:
    envelope = copy.deepcopy(checkpoint) if isinstance(checkpoint, Mapping) else {}
    newest = generation._newest_compatible_concept_checkpoint(envelope) or {}
    records = [
        copy.deepcopy(dict(row))
        for row in newest.get("records") or []
        if isinstance(row, Mapping)
    ]
    inventory = copy.deepcopy(
        newest.get("question_task_inventory")
        or newest.get("inventory")
        or {}
    )
    mined_types = copy.deepcopy(newest.get("mined_types") or {})
    return records, inventory, mined_types, copy.deepcopy(newest)


def release_payload(job: models.UploadJob) -> dict[str, Any] | None:
    inventory = job.question_inventory
    if not isinstance(inventory, Mapping):
        return None
    raw = inventory.get(RELEASE_KEY)
    if not isinstance(raw, Mapping):
        return None
    value = copy.deepcopy(dict(raw))
    if value.get("version") != RELEASE_VERSION:
        return None
    return value


def release_available(job: models.UploadJob) -> bool:
    return release_payload(job) is not None


def _pending_from_checkpoint(checkpoint: object) -> dict[str, Any] | None:
    if not isinstance(checkpoint, Mapping):
        return None
    ledger = checkpoint.get("human_decisions")
    if not isinstance(ledger, Mapping):
        return None
    pending = ledger.get("pending")
    return copy.deepcopy(dict(pending)) if isinstance(pending, Mapping) else None


def _clear_pending(checkpoint: object) -> dict[str, Any]:
    value = copy.deepcopy(dict(checkpoint)) if isinstance(checkpoint, Mapping) else {}
    ledger = value.get("human_decisions")
    if isinstance(ledger, Mapping):
        normalized = copy.deepcopy(dict(ledger))
        normalized["pending"] = None
        value["human_decisions"] = normalized
    return value


def _issue(
    *,
    code: str,
    message: str,
    severity: str = "error",
    phase: str = "",
    unit_id: str = "",
    topic: str = "",
    qids: Iterable[str] = (),
    block_ids: Iterable[str] = (),
    details: object = None,
) -> dict[str, Any]:
    return {
        "code": _normal(code) or "release_issue",
        "severity": _normal(severity).lower() or "error",
        "phase": _normal(phase),
        "unit_id": _normal(unit_id),
        "topic": _normal(topic),
        "qids": _list_strings(qids),
        "block_ids": _list_strings(block_ids),
        "message": _normal(message) or "Generation released with an issue.",
        "details": _json_safe(details),
    }


def _extraction_provenance(inventory: Mapping[str, Any]) -> dict[str, Any]:
    """How the source was read, recorded by the Phase 2 inventory build."""
    value = inventory.get("extraction_provenance")
    return copy.deepcopy(dict(value)) if isinstance(value, Mapping) else {}


def _extraction_provenance_issues(
    provenance: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Say so in the release when the chapter was not read end to end.

    "The exercise questions were not picked up" has to be answerable from the
    release itself. A chapter whose outline pass did not apply was sectioned
    and split deterministically, which is exactly the failure mode that leaves
    a whole exercise section standing as one task.
    """
    if not provenance:
        return []
    issues: list[dict[str, Any]] = []
    if not provenance.get("chapter_outline_applied"):
        issues.append(_issue(
            code="chapter_outline_not_applied",
            severity="warning",
            phase="source-conversion",
            message=(
                "No model-decided chapter outline reached this run, so topics "
                "and question boundaries fell back to deterministic reading. "
                "Multi-part exercise sections are likely to have stayed whole. "
                "Re-run the source conversion for this chapter."
            ),
            details=dict(provenance),
        ))
    elif not provenance.get("chapter_outline_topics"):
        issues.append(_issue(
            code="chapter_outline_topics_unusable",
            severity="warning",
            phase="source-conversion",
            message=(
                "The chapter outline decided question boundaries but no usable "
                "topic, so the chapter was sectioned deterministically and may "
                "have landed under a single topic."
            ),
            details=dict(provenance),
        ))
    unruled = int(provenance.get("chapter_outline_unruled_tasks") or 0)
    if unruled:
        issues.append(_issue(
            code="task_blocks_left_unruled",
            severity="warning",
            phase="source-conversion",
            message=(
                f"{unruled} task block(s) were never ruled on by the chapter "
                "outline, even after a follow-up pass. They shipped whole, so "
                "any independent questions inside them are not in this "
                "release."
            ),
            details=dict(provenance),
        ))
    flags = [str(flag) for flag in provenance.get("chapter_outline_review_flags") or []]
    if flags:
        issues.append(_issue(
            code="chapter_outline_review_flags",
            severity="info",
            phase="source-conversion",
            message=(
                f"The chapter outline was accepted with {len(flags)} "
                "normalization flag(s); see the details for what was adjusted."
            ),
            details=flags,
        ))
    return issues


def _pending_issue(pending: Mapping[str, Any]) -> dict[str, Any]:
    item = pending.get("item") if isinstance(pending.get("item"), Mapping) else {}
    evidence = [
        row for row in pending.get("evidence") or [] if isinstance(row, Mapping)
    ]
    candidates = [
        row for row in pending.get("candidates") or [] if isinstance(row, Mapping)
    ]
    qids = _list_strings(item.get("qids") or pending.get("qids") or [])
    block_ids: list[str] = []
    for row in [*evidence, *candidates]:
        block_ids.extend(_list_strings(row.get("source_block_ids") or []))
        for field in ("evidence_id", "target_id", "title", "text"):
            block_ids.extend(_BLOCK_ID_RE.findall(str(row.get(field) or "")))
    unit_id = _normal(item.get("unit_id") or pending.get("item_id"))
    message = _normal(
        pending.get("conflict")
        or pending.get("diagnosis")
        or pending.get("reason")
        or "A semantic conflict remained after autonomous review."
    )
    return _issue(
        code=_normal(pending.get("kind")) or "semantic_conflict",
        message=message,
        phase=_normal(pending.get("phase")),
        unit_id=unit_id,
        topic=_normal(item.get("topic") or pending.get("topic")),
        qids=qids,
        block_ids=block_ids,
        details={
            "decision_id": pending.get("decision_id"),
            "context_hash": pending.get("context_hash"),
            "decision_question": pending.get("decision_question"),
            "item": item,
            "candidates": candidates,
            "evidence": evidence,
            "options": pending.get("options") or [],
            "source_patch": pending.get("source_patch"),
            "agent_review": pending.get("agent_review"),
        },
    )


def _exception_issue(exc: Exception) -> dict[str, Any]:
    text = _normal(str(exc)) or repr(exc)
    return _issue(
        code=type(exc).__name__,
        message=text,
        phase="generation",
        unit_id=next(iter(_UNIT_ID_RE.findall(text)), ""),
        qids=_QID_RE.findall(text),
        block_ids=_BLOCK_ID_RE.findall(text),
        details={"exception_type": type(exc).__name__, "message": text},
    )


def _qid_manifest(record: Mapping[str, Any]) -> dict[str, Any]:
    raw = record.get("_type_case_qid_host_placement_manifest")
    return copy.deepcopy(dict(raw)) if isinstance(raw, Mapping) else {}


def _record_qids(record: Mapping[str, Any]) -> list[str]:
    manifest = _qid_manifest(record)
    placements = manifest.get("placements")
    qids = list(placements) if isinstance(placements, Mapping) else []
    qids.extend(_list_strings(record.get(RELEASE_ROW_QIDS_FIELD) or []))
    return list(dict.fromkeys(str(value) for value in qids if str(value)))


def _record_blocks(record: Mapping[str, Any]) -> list[str]:
    blocks: list[str] = []
    for field in (
        "_source_block_ids",
        "_source_grounding_evidence_block_ids",
        "_source_grounding_boundary_blocks",
    ):
        raw = record.get(field)
        if isinstance(raw, Mapping):
            raw = [raw]
        if isinstance(raw, Iterable) and not isinstance(raw, (str, bytes)):
            for item in raw:
                if isinstance(item, Mapping):
                    item = item.get("block_id")
                value = _normal(item)
                if value and value not in blocks:
                    blocks.append(value)
    placement = record.get("_placement_contract")
    if isinstance(placement, Mapping):
        for relation in placement.get("topic_relationships") or []:
            if isinstance(relation, Mapping):
                for value in _list_strings(relation.get("evidence_block_ids") or []):
                    if value not in blocks:
                        blocks.append(value)
    return blocks


def audit_type_cases(
    mined_types: object,
    inventory: object,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    """Return ordered Type/Case/Example rows, issues, and QID routes.

    A reusable Type may legitimately span topics. Ownership is recorded at the
    Case/QID level, so ``Type 01 / Case 01`` and ``Type 01 / Case 02`` can be
    hosted under different concepts without duplicating either question.
    """

    output: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    routes: dict[str, list[dict[str, Any]]] = {}
    qid_locations: dict[str, list[dict[str, Any]]] = {}

    for type_index, type_row in enumerate(_type_rows(mined_types), start=1):
        type_id = _normal(type_row.get("type_id")) or f"TYPE-{type_index:04d}"
        type_title = _definition(
            type_row, "type_title", "title", "name"
        )
        type_definition = _definition(
            type_row,
            "type_definition",
            "definition",
            "type_description",
            "description",
            "method_definition",
        )
        owner_topics = _list_strings(
            type_row.get("owner_topic_ids")
            or ([type_row.get("owner_topic_id")] if type_row.get("owner_topic_id") else [])
        )
        output.append({
            "row_kind": "type",
            "type_id": type_id,
            "type_title": type_title,
            "type_definition": type_definition,
            "case_id": "",
            "case_definition": "",
            "owner_topic_ids": owner_topics,
            "qids": _list_strings(type_row.get("source_question_ids") or []),
            "example_qid": "",
            "example_prompt": "",
            "audit_status": "ready",
            "error": "",
        })
        if not type_title:
            issues.append(_issue(
                code="type_title_missing",
                message=f"{type_id} has no usable Type title.",
                phase="type_case_release",
                qids=type_row.get("source_question_ids") or [],
            ))
        if not type_definition:
            issues.append(_issue(
                code="type_definition_missing",
                message=f"{type_id} has no explicit Type definition.",
                phase="type_case_release",
                qids=type_row.get("source_question_ids") or [],
            ))

        cases = [
            row for row in type_row.get("case_prompts") or []
            if isinstance(row, Mapping)
        ]
        if not cases:
            issues.append(_issue(
                code="type_cases_missing",
                message=f"{type_id} has no Case definition.",
                phase="type_case_release",
                qids=type_row.get("source_question_ids") or [],
            ))
        for case_index, case in enumerate(cases, start=1):
            case_id = _normal(case.get("case_id")) or (
                f"{type_id}:CASE-{case_index:04d}"
            )
            case_definition = _definition(
                case,
                "case_definition",
                "definition",
                "case_prompt",
                "prompt",
                "case_title",
                "description",
            )
            qids = _case_qids(case)
            case_owner_topics = _list_strings(
                case.get("owner_topic_ids")
                or ([case.get("owner_topic_id")] if case.get("owner_topic_id") else [])
                or owner_topics
            )
            case_row = {
                "row_kind": "case",
                "type_id": type_id,
                "type_title": type_title,
                "type_definition": "",
                "case_id": case_id,
                "case_definition": case_definition,
                "owner_topic_ids": case_owner_topics,
                "qids": qids,
                "example_qid": "",
                "example_prompt": "",
                "audit_status": "ready",
                "error": "",
            }
            output.append(case_row)
            if not case_definition:
                issues.append(_issue(
                    code="case_definition_missing",
                    message=f"{type_id} / {case_id} has no explicit Case definition.",
                    phase="type_case_release",
                    qids=qids,
                ))

            examples = _case_examples(case)
            if not examples:
                issues.append(_issue(
                    code="case_examples_missing",
                    message=f"{type_id} / {case_id} has no source example below the Case.",
                    severity="warning",
                    phase="type_case_release",
                    qids=qids,
                ))
            for example_index, example in enumerate(examples, start=1):
                qid = _normal(
                    example.get("source_question_id") or example.get("qid")
                )
                prompt = _definition(
                    example,
                    "example_prompt",
                    "prompt",
                    "question",
                    "raw_task",
                    "text",
                )
                output.append({
                    "row_kind": "example",
                    "type_id": type_id,
                    "type_title": "",
                    "type_definition": "",
                    "case_id": case_id,
                    "case_definition": "",
                    "owner_topic_ids": case_owner_topics,
                    "qids": [qid] if qid else [],
                    "example_qid": qid,
                    "example_prompt": prompt,
                    "example_number": example_index,
                    "audit_status": "ready",
                    "error": "",
                })
                if not qid:
                    issues.append(_issue(
                        code="example_qid_missing",
                        message=f"{type_id} / {case_id} has an example without a QID.",
                        phase="type_case_release",
                    ))
                elif qid:
                    route = {
                        "type_id": type_id,
                        "type_title": type_title,
                        "case_id": case_id,
                        "case_definition": case_definition,
                        "owner_topic_ids": case_owner_topics,
                        "example_prompt": prompt,
                    }
                    routes.setdefault(qid, []).append(copy.deepcopy(route))
                    qid_locations.setdefault(qid, []).append(copy.deepcopy(route))

    for qid, locations in sorted(qid_locations.items()):
        if len(locations) > 1:
            issues.append(_issue(
                code="duplicate_qid_assignment",
                message=(
                    f"{qid} is assigned {len(locations)} times across Types/Cases; "
                    "each source QID must appear exactly once."
                ),
                phase="type_case_release",
                qids=[qid],
                details={"assignments": locations},
            ))

    inventory_qids = {
        _normal(item.get("qid"))
        for item in _inventory_items(inventory)
        if _normal(item.get("qid"))
    }
    assigned_qids = set(qid_locations)
    for qid in sorted(inventory_qids - assigned_qids):
        issues.append(_issue(
            code="unassigned_inventory_qid",
            message=f"{qid} is present in the source inventory but has no Type/Case assignment.",
            phase="type_case_release",
            qids=[qid],
        ))
    for qid in sorted(assigned_qids - inventory_qids):
        issues.append(_issue(
            code="unknown_type_case_qid",
            message=f"{qid} is rendered under a Type/Case but is absent from the source inventory.",
            phase="type_case_release",
            qids=[qid],
        ))
    issues.extend(_repeated_question_issues(output))
    return output, issues, routes


_QUESTION_TEXT_NOISE_RE = re.compile(r"[^0-9a-z ]+")
# Only a LEADING item marker — "(2)", "3.", "b)", "(iv)". Digits inside the
# question are part of it ("What is 2 + 3?") and must survive.
_QUESTION_ITEM_MARKER_RE = re.compile(
    r"^\(?\s*(?:[0-9]{1,3}|[a-z]|[ivxl]{1,5})\s*[\).:]\s+"
)


def _question_text_key(value: object) -> str:
    """Compare questions by their words, not their punctuation or numbering."""
    text = _QUESTION_ITEM_MARKER_RE.sub("", _normal(value))
    text = _QUESTION_TEXT_NOISE_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def _repeated_question_issues(
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Flag one question reaching the learner twice under different QIDs.

    ``duplicate_qid_assignment`` catches the same QID placed twice. This
    catches the other half: two distinct source questions whose wording is the
    same, which a reviewer reads as the deck simply repeating itself.
    """
    qids_by_text: dict[str, list[str]] = {}
    for row in rows:
        if str(row.get("row_kind") or "") != "example":
            continue
        key = _question_text_key(row.get("example_prompt"))
        qid = _normal(row.get("example_qid"))
        if len(key) < 25 or not qid:
            # Very short prompts ("Why?", "Explain.") legitimately recur as
            # the tail of different questions; they are not a repeat.
            continue
        seen = qids_by_text.setdefault(key, [])
        if qid not in seen:
            seen.append(qid)
    issues: list[dict[str, Any]] = []
    for key, qids in sorted(qids_by_text.items()):
        if len(qids) < 2:
            continue
        issues.append(_issue(
            code="repeated_question_text",
            message=(
                f"{', '.join(qids)} carry the same question wording; the "
                "learner would meet this question more than once."
            ),
            severity="warning",
            phase="type_case_release",
            qids=list(qids),
            details={"question_text": key[:400]},
        ))
    return issues


def _issue_matches_record(issue: Mapping[str, Any], record: Mapping[str, Any]) -> bool:
    unit_id = _normal(issue.get("unit_id"))
    origin_ids = {
        _normal(record.get("_phase32_origin_concept_id")),
        _normal(record.get("_source_grounding_concept_id")),
        _normal(record.get("_semantic_concept_id")),
    } - {""}
    if unit_id and unit_id in origin_ids:
        return True
    issue_qids = set(_list_strings(issue.get("qids") or []))
    if issue_qids and issue_qids.intersection(_record_qids(record)):
        return True
    topic = _normal(issue.get("topic")).casefold()
    if topic and topic == _normal(record.get("topic")).casefold():
        return True
    return False


def _annotate_records(
    records: Sequence[Mapping[str, Any]],
    issues: Sequence[Mapping[str, Any]],
    routes: Mapping[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw in records:
        record = copy.deepcopy(dict(raw))
        qids = _record_qids(record)
        block_ids = _record_blocks(record)
        matched = [
            issue for issue in issues if _issue_matches_record(issue, record)
        ]
        route_rows = [
            copy.deepcopy(route)
            for qid in qids
            for route in routes.get(qid, [])
        ]
        record[RELEASE_ROW_STATUS_FIELD] = (
            "released_with_errors" if matched else "ready"
        )
        record[RELEASE_ROW_ERRORS_FIELD] = [
            f"{issue.get('code')}: {issue.get('message')}"
            for issue in matched
        ]
        record[RELEASE_ROW_QIDS_FIELD] = qids
        record[RELEASE_ROW_BLOCKS_FIELD] = block_ids
        record[RELEASE_ROW_ROUTES_FIELD] = route_rows
        out.append(record)
    return out


def _release_summary(
    records: Sequence[Mapping[str, Any]],
    issues: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    error_count = sum(
        1 for issue in issues if str(issue.get("severity") or "error") == "error"
    )
    warning_count = len(issues) - error_count
    affected = sum(
        1 for row in records
        if row.get(RELEASE_ROW_STATUS_FIELD) == "released_with_errors"
    )
    return {
        "row_count": len(records),
        "affected_row_count": affected,
        "issue_count": len(issues),
        "error_count": error_count,
        "warning_count": warning_count,
        "database_uploaded": False,
    }


_FINAL_TOPOLOGY_ARTIFACT = "source.phase31-final-topology-cache.json"
_SETTLED_ROWS_ARTIFACT = "source.phase3-settled-rows.json"


def _learner_analysis_count(rows: Iterable[Mapping[str, Any]]) -> int:
    return sum(
        1
        for row in rows
        if isinstance(row, Mapping)
        and "misconception" in _normal(row.get("concept_details")).casefold()
    )


def _validated_artifact_topology(
    job: models.UploadJob,
    current_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]] | None:
    """Return the validated final-topology rows when they beat the checkpoint.

    Job 23 failed after caching a fully validated topology (every normal
    concept carrying complete learner analysis) and then released the older
    81% checkpoint rows without any learner analysis. A failure release must
    ship the most complete rows the run actually produced and verified.
    """
    helper = getattr(uploads, "source_artifact_directory", None)
    if not callable(helper) or not getattr(job, "id", None):
        return None
    try:
        directory = Path(helper(int(job.id))).resolve()
    except Exception:
        return None
    from . import canonical_source_phase3 as phase3

    best: list[dict[str, Any]] | None = None
    # The rewritten Phase 3 snapshots its settled rows separately from the
    # legacy validated-topology cache; a failure release must consider both
    # (job 26 shipped bare checkpoint rows because it only knew the old one).
    for filename in (_SETTLED_ROWS_ARTIFACT, _FINAL_TOPOLOGY_ARTIFACT):
        try:
            raw = json.loads(
                (directory / filename).read_text(encoding="utf-8")
            )
        except Exception:
            continue
        if not isinstance(raw, Mapping):
            continue
        rows = raw.get("records")
        if not isinstance(rows, list) or not rows:
            continue
        if raw.get("records_sha256") != phase3._sha256_json(rows):
            continue
        cache_contract = str(raw.get("source_contract_hash") or "")
        current_contracts = {
            str(row.get("_semantic_graph_contract") or "")
            for row in current_rows
            if isinstance(row, Mapping)
            and row.get("_semantic_graph_contract")
        }
        if cache_contract and current_contracts and (
            cache_contract not in current_contracts
        ):
            continue
        candidate = [
            copy.deepcopy(dict(row))
            for row in rows
            if isinstance(row, Mapping)
        ]
        if best is None or _learner_analysis_count(candidate) > (
            _learner_analysis_count(best)
        ):
            best = candidate
    if best is None or _learner_analysis_count(best) <= (
        _learner_analysis_count(current_rows)
    ):
        return None
    return best


def _chapter_meta_for_release(
    db: Session,
    target_chapter_id: int,
    record_rows: Sequence[Mapping[str, Any]],
    *,
    pre_post: str,
) -> dict[str, Any]:
    """Author chapter/topic metadata while the model is still in the loop.

    The explicit upload action is contractually model-free, so the chapter
    description, chapter duration, and per-topic descriptions must be
    written here and ride the release payload — reviewers received files
    with an empty topic-description column, a zero concept count, and no
    duration because the upload had nothing authored to apply.
    """
    if not record_rows or not target_chapter_id:
        return {}
    try:
        chapter = db.get(models.Chapter, int(target_chapter_id))
        if chapter is None:
            return {}
        from . import chapter_durations

        grouped: dict[str, list[str]] = {}
        order: list[str] = []
        for row in record_rows:
            topic = str(row.get("topic") or "").strip()
            title = str(
                row.get("concept_title") or row.get("concept") or ""
            ).strip()
            if not topic or not title:
                continue
            if topic not in grouped:
                grouped[topic] = []
                order.append(topic)
            grouped[topic].append(title)
        if not order:
            return {}
        expected = chapter_durations.lookup_duration_minutes(
            board=chapter.board,
            grade=chapter.grade,
            subject=chapter.subject,
            chapter_title=chapter.chapter_title,
        )
        meta = generation._metadata(
            subject=chapter.subject,
            board=chapter.board,
            grade=chapter.grade,
            unit=chapter.unit,
            chapter_title=chapter.chapter_title,
            chapter_id=chapter.id,
            chapter_code=chapter.chapter_code,
            finalized_duration_minutes=expected or 0,
        )
        last_exc: Exception | None = None
        for _attempt in range(2):
            try:
                return generation.chapter_meta_via_api(
                    meta=meta,
                    topics=[
                        {
                            "topic": topic,
                            "pre_post_learning": pre_post,
                            "concepts": grouped[topic],
                        }
                        for topic in order
                    ],
                )
            except Exception as exc:  # noqa: BLE001 — retried once below
                last_exc = exc
        raise last_exc if last_exc else RuntimeError("metadata pass failed")
    except Exception as exc:  # noqa: BLE001 — metadata never blocks release
        from . import progress

        progress.log(
            f"Chapter/topic metadata pass failed during release staging "
            f"({exc}); the upload will fall back to deterministic "
            "summaries.",
            level="warning",
        )
        return {}


def stage_release(
    db: Session,
    job: models.UploadJob,
    *,
    target_chapter_id: int | None = None,
    records: Sequence[Mapping[str, Any]] | None = None,
    inventory: Mapping[str, Any] | None = None,
    mined_types: Mapping[str, Any] | None = None,
    final_grounding_certificate: Mapping[str, Any] | None = None,
    checkpoint: Mapping[str, Any] | None = None,
    pending_decision: Mapping[str, Any] | None = None,
    error: Exception | None = None,
    reason: str = "",
    refinements: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist one release payload and clear every manual decision gate.

    ``refinements`` is The Refiner's recorded diff on this release
    (docs/aegis-restructure.md §8.3): its changes, summary, review flags,
    and re-seal marker. ``None`` (callers that never entered the Refiner
    seam) stores no key; the payload stays byte-compatible.
    """

    checkpoint_value = copy.deepcopy(
        dict(checkpoint)
        if isinstance(checkpoint, Mapping)
        else dict(job.generation_checkpoint or {})
    )
    checkpoint_records, checkpoint_inventory, checkpoint_types, newest = (
        _newest_checkpoint_material(checkpoint_value)
    )
    record_rows = [
        copy.deepcopy(dict(row))
        for row in (records if records is not None else checkpoint_records)
        if isinstance(row, Mapping)
    ]
    upgraded_from_cache = False
    if records is None:
        validated = _validated_artifact_topology(job, record_rows)
        if validated is not None:
            record_rows = validated
            upgraded_from_cache = True
    inventory_value = copy.deepcopy(
        dict(inventory)
        if isinstance(inventory, Mapping)
        else checkpoint_inventory
        if isinstance(checkpoint_inventory, Mapping)
        else dict(job.question_inventory or {})
    )
    types_value = copy.deepcopy(
        dict(mined_types)
        if isinstance(mined_types, Mapping)
        else checkpoint_types
        if isinstance(checkpoint_types, Mapping)
        else {"types": inventory_value.get("mined_types") or []}
    )

    pending = copy.deepcopy(
        dict(pending_decision)
        if isinstance(pending_decision, Mapping)
        else _pending_from_checkpoint(checkpoint_value) or {}
    )
    issues: list[dict[str, Any]] = []
    if pending:
        issues.append(_pending_issue(pending))
    if error is not None:
        issues.append(_exception_issue(error))
    type_case_rows, type_case_issues, routes = audit_type_cases(
        types_value, inventory_value
    )
    issues.extend(type_case_issues)
    if not record_rows:
        issues.append(_issue(
            code="no_materialized_concept_rows",
            message=(
                "No concept row had been materialized at the newest durable "
                "checkpoint. The release still contains the full source and "
                "diagnostic context."
            ),
            phase="release",
        ))
    provenance = _extraction_provenance(inventory_value)
    issues.extend(_extraction_provenance_issues(provenance))
    if upgraded_from_cache:
        issues.append(_issue(
            code="release_rows_upgraded_from_validated_cache",
            message=(
                "The released rows come from the validated final concept "
                "topology this run cached (complete learner analysis "
                "included), which is more complete than the newest durable "
                "checkpoint rows."
            ),
            phase="release",
            severity="info",
        ))

    annotated = _annotate_records(record_rows, issues, routes)
    summary = _release_summary(annotated, issues)
    target = int(
        target_chapter_id
        or newest.get("target_chapter_id")
        or checkpoint_value.get("target_chapter_id")
        or (job.deposit_scope_ids or [0])[0]
        or 0
    )
    released_at = datetime.now(timezone.utc).isoformat()
    payload = {
        "version": RELEASE_VERSION,
        "released_at": released_at,
        "release_reason": _normal(reason) or (
            "Generation completed and was staged for explicit publication."
            if not issues
            else "Generation released the newest durable output with diagnostics."
        ),
        "job_id": job.id,
        "learning_kind": job.learning_kind,
        "source_book": job.source_book,
        "filename": job.filename,
        "target_chapter_id": target,
        "target_identity": _json_safe(
            checkpoint_value.get("target_identity") or {}
        ),
        "checkpoint_stage": _normal(
            newest.get("stage") or checkpoint_value.get("stage")
        ),
        "checkpoint_progress": float(
            newest.get("progress")
            or checkpoint_value.get("progress")
            or 0.0
        ),
        "records": _json_safe(annotated),
        "issues": _json_safe(issues),
        "type_case_rows": _json_safe(type_case_rows),
        "question_task_inventory": _json_safe(inventory_value),
        "extraction_provenance": _json_safe(provenance),
        "mined_types": _json_safe(types_value),
        "pending_decision_snapshot": _json_safe(pending),
        "final_grounding_certificate": _json_safe(
            final_grounding_certificate or {}
        ),
        "chapter_meta": _json_safe(_chapter_meta_for_release(
            db,
            target,
            annotated,
            pre_post="Pre" if job.learning_kind == "pre" else "Post",
        )),
        # The Architect's assembled instruction set for this run
        # (docs/aegis-restructure.md §8.1): version, hash, authored slots,
        # and the critic's advisory flags, for the reviewer's audit. The
        # full set (frozen-core hashes included) ships in the diagnostics
        # zip via the artifact directory.
        "instruction_set": _json_safe(_instruction_set_summary(job)),
        "summary": summary,
    }
    if refinements is not None:
        # The Refiner's diff on the release (§8.3): every refinement is a
        # recorded change beside the rows it polished.
        payload["refinements"] = _json_safe(dict(refinements))

    durable_inventory = copy.deepcopy(dict(job.question_inventory or {}))
    durable_inventory[RELEASE_KEY] = copy.deepcopy(payload)
    job.question_inventory = durable_inventory
    job.generation_checkpoint = _clear_pending(checkpoint_value)
    job.deposit_scope_type = "chapter"
    job.deposit_scope_ids = [target] if target else []
    job.status = RELEASE_STATUS
    job.result_ids = []
    job.detail = (
        f"Released {summary['row_count']} concept row(s) for review; "
        f"{summary['issue_count']} issue(s) are attached. Nothing has been "
        "uploaded to the database."
    )
    db.commit()
    db.refresh(job)
    return release_result(job)


def release_result(job: models.UploadJob) -> dict[str, Any]:
    payload = release_payload(job)
    if payload is None:
        raise ReleaseUnavailableError("this upload has no staged release")
    summary = copy.deepcopy(payload.get("summary") or {})
    return {
        "job_id": job.id,
        "status": RELEASE_STATUS,
        "released": True,
        "database_uploaded": bool(summary.get("database_uploaded")),
        "row_count": int(summary.get("row_count") or 0),
        "affected_row_count": int(summary.get("affected_row_count") or 0),
        "issue_count": int(summary.get("issue_count") or 0),
        "release_workbook_url": (
            f"/build-concepts/uploads/{job.id}/release.xlsx"
        ),
        "diagnostics_url": (
            f"/build-concepts/uploads/{job.id}/diagnostics.zip"
        ),
        "release_payload_url": (
            f"/build-concepts/uploads/{job.id}/release.json"
        ),
        "database_upload_url": (
            f"/build-concepts/uploads/{job.id}/upload-release"
        ),
        "detail": job.detail,
    }


def force_release(
    db: Session,
    job_id: int,
    *,
    owner_sub: str | None = None,
) -> models.UploadJob:
    job = uploads.get_job(
        db,
        job_id,
        owner_sub=owner_sub,
        module="build_concepts",
    )
    if job.status == "generated":
        raise ValueError("this upload has already been published to the database")
    if uploads.is_job_running(job_id):
        raise uploads.JobAlreadyRunningError(
            "generation is still running; release it after the active request finishes"
        )
    stage_release(
        db,
        job,
        reason="The user explicitly released the newest durable checkpoint.",
    )
    return job


def _strip_release_fields(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: copy.deepcopy(value)
        for key, value in record.items()
        if key not in _RELEASE_AUDIT_FIELDS
    }


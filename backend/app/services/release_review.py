"""Step 9 — the review & edit surface over the STAGED release (§7).

Outputs 01/03 open as rendered pages built from the staged release
payload; the reviewer edits **in place** (a manual edit is a human
decision: Aegis applies it verbatim and records it — nothing re-runs),
and the instruction box applies a plain-language change list with ONE
bounded model pass over the staged records. Every applied round mints a
new ``staged_release_uid`` + ``staged_version`` (a legitimate re-stage,
so the S10 freeze seal keeps protecting frozen Masters) and appends an
immutable :class:`models.ConceptReleaseVersion` row carrying the
instruction, operations, diff, and full payload — the §7 version record.

Authorities preserved:

* the job's release SLOT stays the one authority for the CURRENT staged
  payload (readers unchanged);
* publication is untouched (Rule G) — an edited release re-validates
  through the same ``structural_defects``/seal gates, and its summary's
  ``database_uploaded`` resets because the edited content is no longer
  what was uploaded;
* identity is never edited here: machine ids are not payload fields
  (S10), and topic/title edits flow into publication's ordinary
  resolution with the reviewer's text winning byte-exactly (T7.2).

Recorded limits (owed follow-ups, not silently claimed): §7's merge /
split / remove operations are not yet in the instruction schema (the
same vocabulary the existing revision engine ships); a reviewer's edit
leaves the row's source-grounding seal describing the pre-edit
provenance — the edit's own record is the ``_aegis_manual_edit_rounds``
trail plus the review flag.
"""
from __future__ import annotations

import copy
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

from sqlalchemy.orm import Session

from .. import models
from . import build_concepts_release as bcr

EDITABLE_FIELDS = (
    "topic",
    "parent_concept",
    "concept_title",
    "concept_details",
    "keywords",
)
# One authority: build_concepts_release both names the trail field and
# registers it in _RELEASE_AUDIT_FIELDS, so publication strips it.
MANUAL_EDIT_TRAIL_FIELD = bcr.MANUAL_EDIT_TRAIL_FIELD
MAX_INSTRUCTION_CHARS = 20_000
MAX_ADDITIONS_PER_ROUND = 50

_SYSTEM = """\
You are applying a reviewer's plain-language corrections to the STAGED
concept release of one textbook chapter. The reviewer's word is final:
apply exactly what the instruction asks — reword, re-tag, rename, move,
re-level — nothing more. Each change names one record and one field and
gives the complete replacement value. You may add a missing concept under
an existing topic. You may not delete a record. Do not rewrite anything
the instruction does not ask about."""


class ReviewUnavailable(RuntimeError):
    """No staged release exists in the requested lane (a 404, not a 422)."""


class ReviewConflict(RuntimeError):
    """The staged release changed under the reviewer (stale uid/before)."""


class ReviewEditError(RuntimeError):
    """A mechanically invalid edit (unknown record/field, emptied identity)."""


class InstructionRoundFailed(RuntimeError):
    """The bounded model pass could not produce an applicable change list."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slot_key(lane: str) -> str:
    return bcr.release_key_for_lane(bcr.normalize_lane(lane))


def _current_payload(job: models.UploadJob, lane: str) -> dict[str, Any]:
    payload = bcr.release_payload(job, lane=bcr.normalize_lane(lane))
    if payload is None:
        raise ReviewUnavailable(
            f"no staged release exists in the {bcr.normalize_lane(lane)!r} "
            "lane for this job"
        )
    return payload


def _records(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        row for row in payload.get("records") or [] if isinstance(row, dict)
    ]


def _versions(
    db: Session, job_id: int, lane: str
) -> list[models.ConceptReleaseVersion]:
    return list(
        db.query(models.ConceptReleaseVersion)
        .filter(
            models.ConceptReleaseVersion.job_id == int(job_id),
            models.ConceptReleaseVersion.lane == bcr.normalize_lane(lane),
        )
        .order_by(models.ConceptReleaseVersion.id.asc())
        .all()
    )


def review_view(
    db: Session, job: models.UploadJob, lane: str = bcr.LANE_POST
) -> dict[str, Any]:
    """The rendered-page projection of the staged release (read-only)."""
    lane = bcr.normalize_lane(lane)
    payload = _current_payload(job, lane)
    records = _records(payload)
    topics: list[dict[str, Any]] = []
    by_topic: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(records):
        topic_name = str(row.get("topic") or "")
        bucket = by_topic.get(topic_name)
        if bucket is None:
            bucket = {"topic": topic_name, "concepts": []}
            by_topic[topic_name] = bucket
            topics.append(bucket)
        bucket["concepts"].append({
            "record_index": index,
            "parent_concept": str(row.get("parent_concept") or ""),
            "concept_title": str(
                row.get("concept_title") or row.get("concept") or ""
            ),
            "concept_details": str(
                row.get("concept_details")
                or row.get("concept_description")
                or ""
            ),
            "keywords": str(row.get("keywords") or ""),
            "review_flags": [
                str(flag) for flag in row.get("review_flags") or []
            ],
            "release_status": str(
                row.get(bcr.RELEASE_ROW_STATUS_FIELD) or ""
            ),
            "release_errors": [
                str(item) for item in row.get("_aegis_release_errors") or []
            ],
        })
    summary = dict(payload.get("summary") or {})
    return {
        "job_id": int(job.id),
        "lane": lane,
        "staged_version": bcr.staged_version(payload),
        "staged_release_uid": str(
            payload.get(bcr.STAGED_RELEASE_UID_FIELD) or ""
        ),
        "state": bcr.release_state(payload),
        "summary": {
            "row_count": int(summary.get("row_count") or 0),
            "issue_count": int(summary.get("issue_count") or 0),
            "error_count": int(summary.get("error_count") or 0),
            "warning_count": int(summary.get("warning_count") or 0),
            "database_uploaded": bool(summary.get("database_uploaded")),
        },
        "topics": topics,
        "issues": [
            {
                "code": str(issue.get("code") or ""),
                "severity": str(issue.get("severity") or ""),
                "message": str(issue.get("message") or ""),
            }
            for issue in payload.get("issues") or []
            if isinstance(issue, Mapping)
        ],
        "versions": [
            {
                "version": int(row.version or 0),
                "staged_release_uid": str(row.staged_release_uid or ""),
                "origin": str(row.origin or ""),
                "instruction": row.instruction or None,
                "change_count": len(row.operations or []),
                "created_at": (
                    row.created_at.isoformat() if row.created_at else ""
                ),
            }
            for row in _versions(db, int(job.id), lane)
        ],
    }


def _seed_history(
    db: Session,
    job: models.UploadJob,
    lane: str,
    owner_sub: str,
) -> None:
    """Snapshot the pre-round staging as the first immutable version row.

    Staging itself does not write history (kept out of its blast radius);
    the first review round therefore preserves the payload it is about to
    supersede, so the "before" of round one is as immutable as every
    later round. Read from the SLOT, never from the working copy: by the
    time a round commits, its working copy already carries the round's
    edits, and the slot is the one authority still holding the pre-round
    truth (``release_payload`` deep-copies on read).
    """
    if _versions(db, int(job.id), lane):
        return
    slot = bcr.release_payload(job, lane=lane)
    if slot is None:  # pragma: no cover - callers read the slot first
        return
    db.add(models.ConceptReleaseVersion(
        owner_sub=owner_sub or "local:default",
        job_id=int(job.id),
        lane=lane,
        version=bcr.staged_version(slot),
        staged_release_uid=str(
            slot.get(bcr.STAGED_RELEASE_UID_FIELD) or ""
        ),
        parent_uid="",
        origin="staged",
        instruction="",
        operations=[],
        diff={},
        payload=slot,
    ))


def _commit_round(
    db: Session,
    job: models.UploadJob,
    lane: str,
    payload: dict[str, Any],
    *,
    origin: str,
    owner_sub: str,
    instruction: str = "",
    operations: list[dict[str, Any]],
    diff: dict[str, Any],
    parent_uid: str,
) -> dict[str, Any]:
    """Mint the new immutable version and make it the current slot."""
    new_uid = uuid.uuid4().hex
    new_version = bcr.next_staged_version(job, lane=lane)
    payload[bcr.STAGED_RELEASE_UID_FIELD] = new_uid
    payload[bcr.STAGED_VERSION_FIELD] = new_version
    # The edited content is no longer what any earlier upload published,
    # and counts may have moved: recompute the summary from the payload's
    # own material (database_uploaded honestly resets).
    payload["summary"] = bcr._release_summary(
        _records(payload), list(payload.get("issues") or [])
    )
    db.add(models.ConceptReleaseVersion(
        owner_sub=owner_sub or "local:default",
        job_id=int(job.id),
        lane=lane,
        version=new_version,
        staged_release_uid=new_uid,
        parent_uid=parent_uid,
        origin=origin,
        instruction=instruction,
        operations=operations,
        diff=diff,
        payload=copy.deepcopy(payload),
    ))
    durable = dict(job.question_inventory or {})
    durable[_slot_key(lane)] = payload
    job.question_inventory = durable
    db.commit()
    db.refresh(job)
    return payload


def apply_manual_edits(
    db: Session,
    job: models.UploadJob,
    *,
    lane: str,
    staged_release_uid: str,
    edits: Sequence[Mapping[str, Any]],
    owner_sub: str = "",
) -> dict[str, Any]:
    """Apply the reviewer's verbatim field edits as one recorded round."""
    lane = bcr.normalize_lane(lane)
    payload = _current_payload(job, lane)
    current_uid = str(payload.get(bcr.STAGED_RELEASE_UID_FIELD) or "")
    if str(staged_release_uid or "") != current_uid:
        raise ReviewConflict(
            "the staged release changed since this page was loaded; reload "
            "and re-apply the edit"
        )
    if not edits:
        raise ReviewEditError("no edits were supplied")
    records = payload.get("records")
    if not isinstance(records, list):
        raise ReviewEditError("the staged release carries no record list")

    operations: list[dict[str, Any]] = []
    round_stamp = _now_iso()
    for edit in edits:
        try:
            index = int(edit.get("record_index"))
        except (TypeError, ValueError):
            raise ReviewEditError("an edit names no record_index")
        field = str(edit.get("field") or "")
        if field not in EDITABLE_FIELDS:
            raise ReviewEditError(
                f"field {field!r} is not reviewer-editable"
            )
        if not (0 <= index < len(records)) or not isinstance(
            records[index], dict
        ):
            raise ReviewEditError(f"record {index} does not exist")
        row = records[index]
        before = str(edit.get("before") if edit.get("before") is not None
                     else "")
        current = str(
            row.get(field)
            or (row.get("concept") if field == "concept_title" else "")
            or (
                row.get("concept_description")
                if field == "concept_details" else ""
            )
            or ""
        )
        if before != current:
            raise ReviewConflict(
                f"record {index} field {field!r} changed since the page "
                "was loaded; reload and re-apply the edit"
            )
        after = str(edit.get("after") if edit.get("after") is not None
                    else "")
        if field in ("topic", "concept_title") and not after.strip():
            raise ReviewEditError(
                f"{field} may not be emptied — the row would fall out of "
                "the release projection"
            )
        # Applied VERBATIM (§7): no cleaning, no normalization — the
        # reviewer's text is the last word, and publication already
        # records any divergence from house normalization (T7.2).
        row[field] = after
        trail = row.setdefault(MANUAL_EDIT_TRAIL_FIELD, [])
        trail.append({
            "field": field,
            "at": round_stamp,
            "editor": owner_sub or "local:default",
        })
        flags = row.setdefault("review_flags", [])
        note = f"manual reviewer edit: {field} replaced in place"
        if note not in flags:
            flags.append(note)
        operations.append({
            "record_index": index,
            "field": field,
            "before": before,
            "after": after,
        })

    _seed_history(db, job, lane, owner_sub)
    _commit_round(
        db, job, lane, payload,
        origin="manual_edit",
        owner_sub=owner_sub,
        operations=operations,
        diff={"changes": operations, "additions": []},
        parent_uid=current_uid,
    )
    return review_view(db, job, lane)


def _instruction_schema(record_ids: Sequence[str]) -> dict[str, Any]:
    return {
        "name": "release_review_instruction",
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["change_summary", "changes", "additions"],
            "properties": {
                "change_summary": {"type": "string"},
                "changes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["record_id", "field", "after", "reason"],
                        "properties": {
                            "record_id": {
                                "type": "string",
                                "enum": list(record_ids) or ["REC-0000"],
                            },
                            "field": {
                                "type": "string",
                                "enum": list(EDITABLE_FIELDS),
                            },
                            "after": {"type": "string"},
                            "reason": {"type": "string"},
                        },
                    },
                },
                "additions": {
                    "type": "array",
                    "maxItems": MAX_ADDITIONS_PER_ROUND,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "topic", "concept_title", "concept_details",
                            "reason",
                        ],
                        "properties": {
                            "topic": {"type": "string"},
                            "parent_concept": {"type": "string"},
                            "concept_title": {"type": "string"},
                            "concept_details": {"type": "string"},
                            "keywords": {"type": "string"},
                            "reason": {"type": "string"},
                        },
                    },
                },
            },
        },
        "strict": True,
    }


def _default_provider(**kwargs: Any) -> Mapping[str, Any]:
    from . import canonical_source_phase22 as phase22

    return phase22._openai_multimodal_json(pages=[], **kwargs)


def apply_instruction_round(
    db: Session,
    job: models.UploadJob,
    *,
    lane: str,
    staged_release_uid: str,
    instruction: str,
    owner_sub: str = "",
    provider: Callable[..., Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """One bounded model pass applies the reviewer's change list (§7)."""
    lane = bcr.normalize_lane(lane)
    instruction = str(instruction or "").strip()
    if not instruction:
        raise ReviewEditError("the instruction is empty")
    if len(instruction) > MAX_INSTRUCTION_CHARS:
        raise ReviewEditError(
            f"the instruction exceeds {MAX_INSTRUCTION_CHARS} characters"
        )
    payload = _current_payload(job, lane)
    current_uid = str(payload.get(bcr.STAGED_RELEASE_UID_FIELD) or "")
    if str(staged_release_uid or "") != current_uid:
        raise ReviewConflict(
            "the staged release changed since this page was loaded; reload "
            "and re-apply the instruction"
        )
    records = payload.get("records")
    if not isinstance(records, list):
        raise ReviewEditError("the staged release carries no record list")

    record_ids = [f"REC-{index + 1:04d}" for index in range(len(records))]
    packet = {
        "instruction": instruction,
        "editable_fields": list(EDITABLE_FIELDS),
        "topics": list(dict.fromkeys(
            str(row.get("topic") or "") for row in records
            if isinstance(row, dict) and str(row.get("topic") or "")
        )),
        "records": [
            {
                "record_id": record_ids[index],
                "topic": str(row.get("topic") or ""),
                "parent_concept": str(row.get("parent_concept") or ""),
                "concept_title": str(
                    row.get("concept_title") or row.get("concept") or ""
                ),
                "concept_details": str(
                    row.get("concept_details")
                    or row.get("concept_description") or ""
                ),
                "keywords": str(row.get("keywords") or ""),
            }
            for index, row in enumerate(records)
            if isinstance(row, dict)
        ],
        "constraints": (
            "Apply the instruction exactly. Every change carries the "
            "complete replacement value. No deletions. Additions only when "
            "the instruction asks for a concept generation missed."
        ),
    }
    call = provider or _default_provider

    def _record_failure(error: str) -> None:
        _seed_history(db, job, lane, owner_sub)
        db.add(models.ConceptReleaseVersion(
            owner_sub=owner_sub or "local:default",
            job_id=int(job.id),
            lane=lane,
            version=bcr.staged_version(payload),
            staged_release_uid=current_uid,
            parent_uid=current_uid,
            origin="instruction_failed",
            instruction=instruction,
            operations=[],
            diff={"error": error},
            payload=None,
        ))
        db.commit()

    try:
        response = call(
            system=_SYSTEM,
            prompt=json.dumps(packet, ensure_ascii=False),
            response_schema=_instruction_schema(record_ids),
            purpose="revision_editing",
        )
    except Exception as exc:
        # The failed round is itself a §7 record — the reviewer's words
        # are never lost with the outage.
        _record_failure(f"{type(exc).__name__}: {exc}")
        raise InstructionRoundFailed(
            f"the instruction pass failed: {exc}"
        ) from exc

    index_by_id = {rid: index for index, rid in enumerate(record_ids)}
    round_stamp = _now_iso()
    operations: list[dict[str, Any]] = []
    for change in response.get("changes") or []:
        if not isinstance(change, Mapping):
            continue
        rid = str(change.get("record_id") or "")
        field = str(change.get("field") or "")
        if rid not in index_by_id or field not in EDITABLE_FIELDS:
            _record_failure(
                f"the model named an unknown target ({rid!r}, {field!r})"
            )
            raise InstructionRoundFailed(
                "the instruction pass named an unknown record or field; "
                "nothing was applied"
            )
        index = index_by_id[rid]
        row = records[index]
        after = str(change.get("after") or "")
        if field in ("topic", "concept_title") and not after.strip():
            _record_failure(f"the model emptied {field} on {rid}")
            raise InstructionRoundFailed(
                f"the instruction pass tried to empty {field}; nothing "
                "was applied"
            )
        before = str(
            row.get(field)
            or (row.get("concept") if field == "concept_title" else "")
            or (
                row.get("concept_description")
                if field == "concept_details" else ""
            )
            or ""
        )
        row[field] = after
        flags = row.setdefault("review_flags", [])
        note = "reviewer instruction applied: " + str(
            change.get("reason") or field
        )
        if note not in flags:
            flags.append(note)
        operations.append({
            "record_index": index,
            "field": field,
            "before": before,
            "after": after,
            "reason": str(change.get("reason") or ""),
        })

    additions: list[dict[str, Any]] = []
    for addition in (response.get("additions") or [])[
        :MAX_ADDITIONS_PER_ROUND
    ]:
        if not isinstance(addition, Mapping):
            continue
        topic = str(addition.get("topic") or "").strip()
        title = str(addition.get("concept_title") or "").strip()
        details = str(addition.get("concept_details") or "").strip()
        if not topic or not title or not details:
            continue
        new_row = {
            "topic": topic,
            "parent_concept": str(addition.get("parent_concept") or topic),
            "concept_title": title,
            "concept_details": details,
            "keywords": str(addition.get("keywords") or ""),
            "review_flags": [
                "added by reviewer instruction: "
                + str(addition.get("reason") or "requested addition")
            ],
            bcr.RELEASE_ROW_STATUS_FIELD: "ready",
            "_aegis_release_errors": [],
        }
        records.append(new_row)
        additions.append({
            "record_index": len(records) - 1,
            "topic": topic,
            "concept_title": title,
        })

    if not operations and not additions:
        _record_failure("the model returned no applicable change")
        raise InstructionRoundFailed(
            "the instruction pass returned no applicable change; the "
            "release is unchanged"
        )

    _seed_history(db, job, lane, owner_sub)
    _commit_round(
        db, job, lane, payload,
        origin="instruction",
        owner_sub=owner_sub,
        instruction=instruction,
        operations=operations + additions,
        diff={
            "change_summary": str(response.get("change_summary") or ""),
            "changes": operations,
            "additions": additions,
            "applied_at": round_stamp,
        },
        parent_uid=current_uid,
    )
    return review_view(db, job, lane)

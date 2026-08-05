"""Post-run reviewer instructions and the edits Aegis makes from them.

Generation always produces an output. Where placement could not be certified,
Aegis places the concept by its best reading of
``docs/concept-placement-rules.md`` and flags it rather than retiring it — a
reviewer can correct a wrong placement, but cannot correct one that is missing.

A reviewer then reads the delivered workbook and writes what needs changing in
plain language. That instruction is applied here by one bounded model pass over
the job's concepts.

Two properties this module exists to guarantee:

* **The output file is never annotated.** Review history lives in the
  ``concept_revisions`` table, never in the workbook. A round that changes
  nothing leaves the artifact byte-identical.
* **Rounds are unbounded.** There is no ceiling on how many times an output may
  be corrected. Each round is numbered per job and applied against the current
  state, so instructions compose in the order they were given.

The stored instructions are the point, not a side effect: they are a record of
what Aegis should have done unprompted, and are meant to be mined later.
"""
from __future__ import annotations

import copy
import json
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from . import openai_usage, progress

# A reviewer may only redirect placement and wording of concepts that already
# exist. Creating or deleting concepts is generation's job, driven by source
# evidence; an instruction cannot conjure a claim the source does not support.
EDITABLE_FIELDS = (
    "topic",
    "parent_concept",
    "concept_title",
    "concept_display_name",
    "concept_details",
    "keywords",
)

MAX_INSTRUCTION_CHARS = 20_000


class RevisionError(RuntimeError):
    """An instruction could not be applied."""


def _now() -> datetime:
    return datetime.utcnow()


def next_round_number(db: Session, job_id: int) -> int:
    """Return the next 1-based round for a job. Deliberately uncapped."""

    highest = db.execute(
        select(models.ConceptRevision.round_number)
        .where(models.ConceptRevision.job_id == job_id)
        .order_by(models.ConceptRevision.round_number.desc())
        .limit(1)
    ).scalar_one_or_none()
    return int(highest or 0) + 1


def list_revisions(
    db: Session,
    job_id: int,
    *,
    owner_sub: str | None = None,
) -> list[models.ConceptRevision]:
    """Return one job's review history, oldest first."""

    query = select(models.ConceptRevision).where(
        models.ConceptRevision.job_id == job_id
    )
    if owner_sub:
        query = query.where(models.ConceptRevision.owner_sub == owner_sub)
    return list(
        db.execute(
            query.order_by(models.ConceptRevision.round_number.asc())
        ).scalars()
    )


def revision_summary(revision: models.ConceptRevision) -> dict[str, Any]:
    """Shape one round for the API and the review UI."""

    return {
        "id": revision.id,
        "job_id": revision.job_id,
        "round_number": revision.round_number,
        "instruction": revision.instruction,
        "status": revision.status,
        "change_summary": revision.change_summary,
        "changes": copy.deepcopy(revision.changes or []),
        "change_count": revision.change_count,
        "flagged_placements": copy.deepcopy(
            revision.flagged_placements or []
        ),
        "model": revision.model,
        "error": revision.error,
        "created_at": (
            revision.created_at.isoformat() if revision.created_at else ""
        ),
        "completed_at": (
            revision.completed_at.isoformat() if revision.completed_at else ""
        ),
    }


# Phase 3.8 narrows an unprovable claim to what its evidence supports and
# places it by best judgement; it never retires or deletes a concept. It says so
# in the run log, and that line is the only signal a reviewer gets that a
# placement was Aegis's reading rather than a certified one.
_FLAG_MARKERS = (
    "phase 3.8 terminal disposition",
    "placed by best judgement",
)


def _collect_flagged_placements(
    job: models.UploadJob,
) -> list[dict[str, Any]]:
    """Return the placements Aegis made by best judgement, for review.

    A reviewer can correct a wrong placement but not a missing one, so nothing
    is dropped — these are shipped and flagged. Surfacing them alongside the
    instruction box points the reviewer at what Aegis itself was least sure of.
    """

    out: list[dict[str, Any]] = []
    for row in job.generation_log or []:
        if not isinstance(row, dict) or row.get("type") != "log":
            continue
        message = str(row.get("message") or "")
        lowered = message.casefold()
        if any(marker in lowered for marker in _FLAG_MARKERS):
            out.append({
                "level": str(row.get("level") or "warning"),
                "message": message[:2000],
            })
    return out


# Public name; the parameter of ``record_instruction`` shadows it otherwise.
flagged_placements = _collect_flagged_placements


def record_instruction(
    db: Session,
    job: models.UploadJob,
    instruction: str,
    *,
    owner_sub: str | None = None,
    flagged_placements: list[dict[str, Any]] | None = None,
) -> models.ConceptRevision:
    """Persist one reviewer instruction before anything is attempted.

    Committed first and on its own. If the model pass then fails, the
    instruction survives as a ``failed`` round: the reviewer's words are the
    research artifact and must not be lost with the attempt that used them.
    """

    text = str(instruction or "").strip()
    if not text:
        raise RevisionError("A revision instruction cannot be empty.")
    if len(text) > MAX_INSTRUCTION_CHARS:
        raise RevisionError(
            f"A revision instruction is limited to {MAX_INSTRUCTION_CHARS} "
            f"characters; received {len(text)}."
        )

    flags = (
        copy.deepcopy(flagged_placements)
        if flagged_placements is not None
        else _collect_flagged_placements(job)
    )
    revision = models.ConceptRevision(
        owner_sub=str(owner_sub or job.owner_sub or "local:default"),
        job_id=job.id,
        round_number=next_round_number(db, job.id),
        instruction=text,
        status="pending",
        flagged_placements=flags,
    )
    db.add(revision)
    db.commit()
    db.refresh(revision)
    return revision


def _concept_rows(
    db: Session,
    job: models.UploadJob,
) -> list[tuple[models.Concept, models.Topic]]:
    """Return this job's deposited concepts with their owning topic."""

    chapter_ids = [
        int(value)
        for value in (job.deposit_scope_ids or [])
        if str(value).strip().lstrip("-").isdigit()
    ]
    if not chapter_ids:
        return []
    rows = db.execute(
        select(models.Concept, models.Topic)
        .join(models.Topic, models.Concept.topic_id == models.Topic.id)
        .where(models.Topic.chapter_id.in_(chapter_ids))
        .order_by(models.Topic.source_order, models.Concept.source_order)
    ).all()
    return [(concept, topic) for concept, topic in rows]


def _editable_view(
    rows: list[tuple[models.Concept, models.Topic]],
) -> list[dict[str, Any]]:
    return [
        {
            "concept_id": concept.id,
            "topic": topic.topic_title,
            "topic_id": topic.id,
            "parent_concept": concept.parent_concept,
            "concept_title": concept.concept_title,
            "concept_display_name": concept.concept_display_name,
            "concept_details": concept.concept_details,
            "keywords": concept.keywords,
        }
        for concept, topic in rows
    ]


def _edit_schema(concept_ids: list[int]) -> dict[str, Any]:
    return {
        "name": "aegis_concept_revision",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["change_summary", "changes"],
            "properties": {
                "change_summary": {"type": "string", "minLength": 1},
                "changes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "concept_id", "field", "after", "reason"
                        ],
                        "properties": {
                            "concept_id": {
                                "type": "integer",
                                "enum": concept_ids or [0],
                            },
                            "field": {
                                "type": "string",
                                "enum": list(EDITABLE_FIELDS),
                            },
                            "after": {"type": "string"},
                            "reason": {"type": "string", "minLength": 1},
                        },
                    },
                },
            },
        },
    }


_SYSTEM = (
    "You are Aegis's concept revision editor. A human reviewer has read the "
    "generated concept map and written what needs correcting. Apply exactly "
    "what they asked and nothing more.\n\n"
    "You may only change these fields of concepts that already exist: "
    + ", ".join(EDITABLE_FIELDS)
    + ". You may not create or delete a concept: generation owns what claims "
    "the source supports, and an instruction cannot add a claim the source "
    "does not make. If the reviewer asks for something outside those fields, "
    "return no change for it and say so in change_summary.\n\n"
    "When the instruction concerns which topic owns a concept, follow the "
    "product placement rules: a concept whose understanding requires a later "
    "topic's method or framework belongs to that later topic, with the earlier "
    "topic a prerequisite rather than the owner; a later topic that merely "
    "refers back to earlier material does not take ownership of it; and a task "
    "or Type spanning topics belongs to the latest topic it requires. Follow "
    "the reviewer over your own reading where they conflict — they are "
    "correcting you.\n\n"
    "Return one entry per field you actually change, citing the reviewer's "
    "words as the reason. Return an empty changes array if the instruction "
    "requires no edit."
)


def _default_provider(**kwargs: Any) -> dict[str, Any]:
    # Imported lazily: the offline revision tests never reach a provider.
    from . import canonical_source_phase22 as phase22

    return phase22._openai_multimodal_json(**kwargs)


def apply_instruction(
    db: Session,
    job: models.UploadJob,
    revision: models.ConceptRevision,
    *,
    provider=None,
) -> models.ConceptRevision:
    """Apply one recorded instruction to the job's concepts.

    The workbook is not touched here. Callers rebuild it from the concepts
    afterwards, so a round that changes nothing produces an identical file.
    """

    rows = _concept_rows(db, job)
    if not rows:
        return _finish(
            db, revision, status="failed",
            error="This job has no deposited concepts to revise.",
        )

    by_id = {concept.id: (concept, topic) for concept, topic in rows}
    view = _editable_view(rows)
    packet = {
        "instruction": revision.instruction,
        "round_number": revision.round_number,
        "concepts": view,
        "flagged_placements": revision.flagged_placements or [],
        "editable_fields": list(EDITABLE_FIELDS),
        "constraints": {
            "may_not_create_or_delete_concepts": True,
            "only_listed_concept_ids_may_change": True,
            "follow_the_reviewer_over_your_own_placement_reading": True,
        },
    }

    try:
        raw = (provider or _default_provider)(
            system=_SYSTEM,
            prompt=json.dumps(packet, ensure_ascii=False),
            pages=[],
            response_schema=_edit_schema(sorted(by_id)),
            purpose="revision_editing",
            max_tokens=None,
            single_attempt=False,
        )
    except Exception as exc:  # provider/transport failure
        progress.log(
            f"Revision round {revision.round_number} could not be applied "
            f"({type(exc).__name__}). The instruction is kept.",
            level="error",
        )
        return _finish(db, revision, status="failed", error=str(exc)[:4000])

    if not isinstance(raw, dict):
        return _finish(
            db, revision, status="failed",
            error="The revision editor returned no structured result.",
        )

    applied: list[dict[str, Any]] = []
    for entry in raw.get("changes") or []:
        if not isinstance(entry, dict):
            continue
        concept_id = entry.get("concept_id")
        field = str(entry.get("field") or "")
        if concept_id not in by_id or field not in EDITABLE_FIELDS:
            continue
        concept, topic = by_id[concept_id]
        after = str(entry.get("after") or "")
        before = (
            topic.topic_title if field == "topic"
            else str(getattr(concept, field, "") or "")
        )
        if before == after:
            continue
        if field == "topic":
            moved = _move_to_topic(db, concept, topic, after)
            if not moved:
                continue
        else:
            setattr(concept, field, after)
        applied.append({
            "concept_id": concept_id,
            "field": field,
            "before": before,
            "after": after,
            "reason": str(entry.get("reason") or ""),
        })

    revision.changes = applied
    revision.change_summary = str(raw.get("change_summary") or "")[:8000]
    revision.model = str(raw.get("_model") or "")
    if openai_usage.is_tracking():
        revision.openai_usage = openai_usage.current_summary()
    return _finish(
        db,
        revision,
        status="applied" if applied else "no_change",
        error="",
    )


def _move_to_topic(
    db: Session,
    concept: models.Concept,
    current: models.Topic,
    topic_title: str,
) -> bool:
    """Repoint a concept at another existing topic in the same chapter.

    A reviewer redirects placement among the topics the chapter already
    teaches. Inventing a topic here would create structure the source never
    established, so an unknown title is declined rather than created.
    """

    wanted = str(topic_title or "").strip().casefold()
    if not wanted:
        return False
    target = db.execute(
        select(models.Topic).where(models.Topic.chapter_id == current.chapter_id)
    ).scalars()
    for topic in target:
        if str(topic.topic_title or "").strip().casefold() == wanted:
            concept.topic_id = topic.id
            return True
    return False


def _finish(
    db: Session,
    revision: models.ConceptRevision,
    *,
    status: str,
    error: str,
) -> models.ConceptRevision:
    revision.status = status
    revision.error = error
    revision.completed_at = _now()
    db.commit()
    db.refresh(revision)
    return revision

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

**Two lanes, two sibling slots (spec T3).** One run produces all four
outputs (Q3), so one job stages two release payloads: the Post-Learning
one under ``RELEASE_KEY`` (Outputs 01/02) and the Pre-Learning one under
the sibling ``PRE_RELEASE_KEY`` (Outputs 03/04). They are siblings, not a
lane-keyed sub-map inside one slot: a sub-map would change the Post
payload's byte shape and every recorded release fixture. Which slot a
payload came out of IS its lane — every reader that must know takes the
``lane`` parameter and gets the answer from the key it read, never from a
field inside the payload it happens to be holding (``payload_lane`` exists
only to catch a payload mis-staged into the wrong slot, and is a union
with the key, never a replacement for it).

``UploadJob.learning_kind`` stays a per-JOB column that every live
creation site hardwires to ``"post"`` (spec T2); Pre/Post is a per-OUTPUT
property here. Reading a job's column to decide an output's lane is
therefore always wrong, and the Pre payload's own ``learning_kind: "pre"``
is a projection detail the workbook/publication writers consume — not the
lane's authority.
"""
from __future__ import annotations

import copy
import hashlib
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
# The Pre-Learning sibling slot (spec T3). Outputs 03/04 live here; the
# key itself carries the lane.
PRE_RELEASE_KEY = "_aegis_pre_release_output"
LANE_POST = "post"
LANE_PRE = "pre"
RELEASE_LANES = (LANE_POST, LANE_PRE)
# Self-declared lane marker written into the Pre payload. Redundant with
# the key by design: it lets a payload that has been copied out of its
# slot (a diagnostics export, a fixture) still say what it is, and it
# lets ``payload_lane`` catch a Pre payload mis-staged into the POST slot.
# It is never the authority when the key is available.
RELEASE_LANE_FIELD = "release_lane"
RELEASE_STATUS = "released"
RELEASE_ROW_STATUS_FIELD = "_aegis_release_status"
RELEASE_ROW_ERRORS_FIELD = "_aegis_release_errors"
RELEASE_ROW_QIDS_FIELD = "_aegis_release_qids"
RELEASE_ROW_BLOCKS_FIELD = "_aegis_release_block_ids"
RELEASE_ROW_ROUTES_FIELD = "_aegis_release_type_case_routes"
RELEASE_ROW_REFINED_FIELD = "_aegis_release_refined"
RELEASE_ROW_LANE_FIELD = "_aegis_release_lane"
PRE_ROW_GENERATED_QUESTIONS_FIELD = "_aegis_pre_generated_questions"

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
    # The Phase 2.4/4.3 chapter analysis inventory's allotments (Q1,
    # phase3/assemble.py): the LA-item ids this row received. Rides the
    # release for the reviewer's audit (every LA-id accounted, allotted
    # to exactly one concept) and is stripped before DB upload.
    "_aegis_analysis_allotments",
    # The Phase 03 Pre-Learning map's row-private records (doc §4,
    # phase3/premap.py): the captured prerequisites a pre-concept teaches,
    # and its explicit needed-for links to the Post concepts that require
    # it. Both ride the release for the reviewer's audit and are stripped
    # before DB upload; their column home is step 8's related_concepts
    # (Q5), and neither adds a house-format section.
    "_aegis_pre_prerequisites",
    "_aegis_needed_for",
    # Assessment grouping verdicts (step 6): private release audit carried by
    # candidates/groups and stripped before concept-row database publication.
    # The assessment renderer has no visible slots for these records.
    "_aegis_assessment_level_verdict",
    "_aegis_assessment_cell_verdict",
    "_aegis_assessment_materialization",
    "_aegis_assessment_answer_restriction",
    "_aegis_assessment_marking",
    "_aegis_assessment_route",
    "_aegis_assessment_variant_cluster",
    "_aegis_assessment_group_description",
    "_aegis_assessment_group_quality",
    # Slice-5 Master Refiner decisions. Candidate and group units share one
    # private audit marker while retaining distinct decision kinds/policies.
    "_aegis_assessment_master_refinement",
    # Output 03 (spec T3): the row-private marks the Pre release stamps on
    # every one of its concept rows. ``_aegis_release_lane`` says which
    # lane's slot the row shipped in, so a row lifted out of the payload
    # into a diagnostics export still says what it is; and
    # ``_aegis_pre_generated_questions`` carries the identities of the
    # GENERATED questions Output 04 authored for that pre-concept, so a
    # reviewer holding Output 03 can see what Output 04 shipped for each
    # row. Both ride the release for the reviewer's audit and are stripped
    # before DB upload like every other audit field; neither adds a
    # house-format section and neither is ever workbook-visible.
    #
    # Registered through the CONSTANTS, not through repeated string
    # literals: registration is the load-bearing act — an unregistered
    # marker is not stripped and leaks a private field into a published
    # concept row — and a literal here would silently unregister itself
    # the day someone renames the constant.
    RELEASE_ROW_LANE_FIELD,
    PRE_ROW_GENERATED_QUESTIONS_FIELD,
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
    assembled (legacy runs).
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


def normalize_lane(lane: object) -> str:
    """``"post"`` unless the caller explicitly asked for the Pre lane.

    Mechanics: a name lookup over two known slot names, defaulting to the
    lane that existed before Outputs 03/04 did, so every caller that has
    not been told about the Pre lane keeps reading the Post slot.
    """

    value = str(lane or "").strip().lower()
    if value == LANE_PRE:
        return LANE_PRE
    if value in ("", LANE_POST):
        return LANE_POST
    raise ValueError(
        f"unknown release lane {lane!r}; the staged lanes are "
        f"{RELEASE_LANES!r}"
    )


def release_key_for_lane(lane: object = LANE_POST) -> str:
    return PRE_RELEASE_KEY if normalize_lane(lane) == LANE_PRE else RELEASE_KEY


def release_payload(
    job: models.UploadJob, *, lane: object = LANE_POST,
) -> dict[str, Any] | None:
    """The staged payload in one lane's slot, or None.

    ``lane`` names the SLOT, which is what makes a caller's lane explicit
    at the call site rather than inferred from the payload it receives.
    """

    inventory = job.question_inventory
    if not isinstance(inventory, Mapping):
        return None
    raw = inventory.get(release_key_for_lane(lane))
    if not isinstance(raw, Mapping):
        return None
    value = copy.deepcopy(dict(raw))
    if value.get("version") != RELEASE_VERSION:
        return None
    return value


def payload_lane(payload: Mapping[str, Any] | None) -> str:
    """The lane a payload DECLARES about itself.

    A cross-check for a payload held outside its slot, never a substitute
    for the slot it came out of: ``staged_release_for_lane`` unions this
    with the key, and the key wins whenever the two disagree in the Pre
    direction. Both markers are read because ``learning_kind`` is the one
    every existing projection writer already reads off a staged payload
    (``build_concepts_release_files.py`` and
    ``build_concepts_release_publication.py`` both derive ``pre_post``
    from it), so a payload that renders as Pre in those lanes must never
    read as Post here — the barrier's predicate must not be narrower than
    the lane's own definition elsewhere in the tree.
    """

    if not isinstance(payload, Mapping):
        return LANE_POST
    declared = str(payload.get(RELEASE_LANE_FIELD) or "").strip().lower()
    if declared == LANE_PRE:
        return LANE_PRE
    if str(payload.get("learning_kind") or "").strip().lower() == LANE_PRE:
        return LANE_PRE
    return LANE_POST


def staged_release_for_lane(
    job: models.UploadJob, lane: object = LANE_POST,
) -> tuple[dict[str, Any] | None, str]:
    """Return ``(payload, resolved_lane)`` for one staged slot.

    The resolved lane is decided by the KEY the payload was read out of —
    reading the sibling slot resolves to ``"pre"`` whatever the payload's
    own fields say. That binding is the point (spec T3): the Pre release
    rides a POST job, so every field-keyed test for "is this the Pre
    lane" — ``job.learning_kind`` above all — answers "post" on a payload
    that is unambiguously Pre. A caller that reads the sibling slot and
    then takes a Post-lane code path has already lost, so the lane travels
    with the read.

    The payload's self-declared markers are unioned in, never subtracted:
    a Pre payload mis-staged into the POST slot still resolves to Pre.
    """

    resolved = normalize_lane(lane)
    payload = release_payload(job, lane=resolved)
    if resolved == LANE_PRE:
        return payload, LANE_PRE
    return payload, payload_lane(payload)


def release_available(
    job: models.UploadJob, *, lane: object = LANE_POST,
) -> bool:
    return release_payload(job, lane=lane) is not None


def pre_release_available(job: models.UploadJob) -> bool:
    return release_payload(job, lane=LANE_PRE) is not None


def staged_generated_questions(
    job: models.UploadJob,
) -> list[dict[str, Any]] | None:
    """The GENERATED questions the staged Pre release carries for Output 04.

    None when there is no staged Pre release at all — which is different
    from a staged Pre release that authored no question, and the two must
    stay distinguishable: the first means "this run built no Pre lane",
    the second means "it built one and it is empty", and only the second
    is a legal empty Output 04.
    """

    payload = release_payload(job, lane=LANE_PRE)
    if payload is None:
        return None
    return [
        copy.deepcopy(dict(row))
        for row in payload.get("generated_questions") or []
        if isinstance(row, Mapping)
    ]


# --------------------------------------------------------------------------- #
# The three named release states (docs/aegis-restructure.md §4, Rules E-G)
# --------------------------------------------------------------------------- #

READY = "ready"
READY_WITH_FLAGS = "ready_with_flags"
DIAGNOSTIC_RELEASE = "diagnostic_release"


def _publishable_record(record: object) -> bool:
    """A row the deterministic publication can actually deposit.

    Mechanics: it has the two identities the upsert joins on. No reading
    of what the row means.
    """

    return (
        isinstance(record, Mapping)
        and bool(_normal(record.get("topic")))
        and bool(
            _normal(record.get("concept_title") or record.get("concept"))
        )
    )


def structural_defects(payload: Mapping[str, Any] | None) -> list[str]:
    """Structural/import-integrity defects that block the database upload.

    "Semantic doubt flags; structural corruption blocks" (§4). This is the
    structural half, and it is deliberately the SAME set of conditions the
    explicit upload already refused before the Pre lane existed — a
    payload with no depositable row — plus the two new structural states
    the Pre lane can reach: a lane that refused its own artefact (the
    fail-closed source-question barrier) and therefore has nothing to
    publish, and an input snapshot that was on disk and unreadable, so
    what this release is missing is missing from the ARTEFACT rather than
    from the chapter. A gate that refuses a broken artifact, judging
    nothing about content: it counts identities, reads a recorded
    refusal, and reads whether a file parsed.

    The unreadable-snapshot case is what makes this a safety property
    rather than bookkeeping. Publishing a Pre release whose questions
    snapshot could not be read would write a chapter's generated
    questions out of existence and report zero — "silently losing a
    learner's question is never recoverable" (CLAUDE.md, R4). Blocked
    here, the loss is loud and every download still opens.

    Evidence always still ships — this blocks the database write only, never
    a download (Rule E).
    """

    if not isinstance(payload, Mapping):
        return ["no staged release"]
    defects: list[str] = []
    refused = _normal(payload.get("refused"))
    if refused:
        defects.append(f"the lane refused its own artefact: {refused}")
    for defect in payload.get("snapshot_defects") or []:
        if _normal(defect):
            defects.append(
                "an input snapshot could not be read, so this release is "
                f"incomplete rather than empty: {_normal(defect)}"
            )
    if not [
        row for row in payload.get("records") or []
        if _publishable_record(row)
    ]:
        defects.append("the release contains no concept rows to upload")
    return defects


def release_state(payload: Mapping[str, Any] | None) -> str:
    """One of the three named states, for either lane.

    Derived, never stored: computing it from the payload keeps the Post
    payload's byte shape exactly as recorded while giving the Pre outputs
    the same three states. *Diagnostic release* when structure is corrupt
    (downloads open, database upload blocked); *Ready with flags* when the
    run recorded any issue or any row carries one (downloads AND explicit
    publication both open — flags never block, Rule E); *Ready* otherwise.
    """

    if not isinstance(payload, Mapping):
        return DIAGNOSTIC_RELEASE
    if structural_defects(payload):
        return DIAGNOSTIC_RELEASE
    summary = payload.get("summary") or {}
    flagged = bool(
        int(summary.get("issue_count") or 0)
        or int(summary.get("affected_row_count") or 0)
        or any(
            row.get("review_flags")
            for row in payload.get("records") or []
            if isinstance(row, Mapping)
        )
    )
    return READY_WITH_FLAGS if flagged else READY


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


def _case_uniqueness_issues(
    records: Sequence[Mapping[str, Any]],
    mined_types: object,
) -> list[dict[str, Any]]:
    """Q2 deterministic uniqueness audit over the released rows.

    Runs ``phase3.assemble.audit_case_uniqueness`` (mechanics: rendered
    Case identities exactly-once, QID-keyed Example exactly-once, no
    example-less Case shells) and converts its findings into release
    issues — the release audit fails visibly, nothing is silently
    repaired.
    """

    from .phase3 import assemble as p3_assemble

    rows = [dict(row) for row in records if isinstance(row, Mapping)]
    if not rows:
        return []
    try:
        _types, cases = p3_assemble._type_catalog(
            {"mined_types": dict(mined_types or {})
             if isinstance(mined_types, Mapping) else {}}
        )
    except Exception:  # noqa: BLE001 - a malformed catalog never blocks release
        cases = {}
    prompt_by_qid: dict[str, str] = {}
    for case in cases.values():
        for example in case.get("examples") or []:
            qid = _normal(example.get("qid"))
            if qid and qid not in prompt_by_qid:
                prompt_by_qid[qid] = str(example.get("prompt") or "")
    findings = p3_assemble.audit_case_uniqueness(
        rows,
        cases=cases or None,
        expected_examples=[
            {"qid": qid, "prompt": prompt}
            for qid, prompt in sorted(prompt_by_qid.items())
        ],
    )
    return [
        _issue(
            code=f"case_uniqueness_{finding.get('code')}",
            message=str(finding.get("message") or ""),
            phase="type_case_release",
            qids=finding.get("qids") or [],
        )
        for finding in findings
    ]


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


def _directory_metadata_for_release(
    db: Session, target_chapter_id: int,
) -> dict[str, Any]:
    """Freeze the target Chapter fields later projections may consume.

    Topic and Concept meaning comes from the released records. The Chapter is
    only a directory anchor, but even that metadata must travel with the staged
    Output-01 payload: rereading a mutable Chapter after staging would let the
    same release seal produce different workbooks and model decisions.
    """

    if not target_chapter_id:
        return {}
    chapter = db.get(models.Chapter, int(target_chapter_id))
    if chapter is None:
        return {}
    return {
        "chapter_code": str(chapter.chapter_code or ""),
        "board": str(chapter.board or ""),
        "grade": str(chapter.grade or ""),
        "subject": str(chapter.subject or ""),
        "unit": str(chapter.unit or ""),
        "chapter_title": str(chapter.chapter_title or ""),
        "chapter_display_name": str(chapter.chapter_display_name or ""),
        "chapter_description": str(chapter.chapter_description or ""),
        "chapter_duration": str(chapter.chapter_duration or ""),
        "pre_topics": str(chapter.pre_topics or ""),
        "post_topics": str(chapter.post_topics or ""),
    }


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
    issues.extend(_case_uniqueness_issues(record_rows, types_value))
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
    directory_metadata = _directory_metadata_for_release(db, target)
    source_document_hash = "sha256:" + hashlib.sha256(
        str(job.mmd_text or "").encode("utf-8")
    ).hexdigest()
    chapter_meta = _chapter_meta_for_release(
        db,
        target,
        annotated,
        pre_post="Pre" if job.learning_kind == "pre" else "Post",
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
        "source_document_hash": source_document_hash,
        "target_chapter_id": target,
        "directory_metadata": _json_safe(directory_metadata),
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
        "chapter_meta": _json_safe(chapter_meta),
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


PRE_MAP_SNAPSHOT = "source.phase3-prelearn-map.json"
PRE_QUESTIONS_SNAPSHOT = "source.phase3-prelearn-questions.json"
# The Refiner's own name for this lane's rendered output (§8.3). Distinct
# from "concepts_release" so a Pre refinement is legible as one in the
# recorded diff and in the decision store.
PRE_REFINER_OUTPUT_KIND = "pre_concepts_release"


def _run_snapshot(
    job: models.UploadJob, name: str,
) -> tuple[dict[str, Any] | None, str]:
    """One phase-3 snapshot, as ``(snapshot, defect)``.

    The Pre lane's map and questions leave the sealed phase-3 boundary as
    snapshots (``runner._snapshot_premap`` / ``_snapshot_prequestions``),
    which is also how they survive a resume that skips the whole phase-3
    run. Reading them back here is the same move
    ``concept_topology_contract.restored_prerequisites`` already makes,
    and it keeps Outputs 03/04 out of the generation call chain entirely.

    THREE states, kept distinguishable (R4) — this used to be two:

    * ``(None, "")`` — the file is absent. This run authored no such
      artefact, which for the map means "no Pre lane at all";
    * ``(snapshot, "")`` — read;
    * ``(None, reason)`` — the file is THERE and cannot be read.

    Collapsing the third into the first is the failure this signature
    exists to prevent. An unreadable questions snapshot would otherwise
    be indistinguishable from "the lane authored no question", and
    Output 04 would ship empty, flagless and ``ready`` with a chapter's
    worth of generated questions gone — "silently losing a learner's
    question is never recoverable" (CLAUDE.md). An unreadable map would
    be indistinguishable from a run that never reached Phase 03.

    Reading a file is mechanics; ``defect`` records what failed and
    judges nothing about content.
    """

    helper = getattr(uploads, "source_artifact_directory", None)
    if not callable(helper) or not getattr(job, "id", None):
        return None, ""
    try:
        path = Path(helper(int(job.id))).resolve() / name
    except Exception as exc:  # noqa: BLE001 - cannot even locate the artifact
        return None, f"{name} could not be located ({type(exc).__name__}: {exc})"
    try:
        if not path.is_file():
            return None, ""
    except OSError as exc:
        return None, f"{name} could not be located ({type(exc).__name__}: {exc})"
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - present but unreadable
        return None, (
            f"{name} is on disk but could not be read "
            f"({type(exc).__name__}: {exc})"
        )
    if not isinstance(loaded, dict):
        return None, (
            f"{name} is on disk but does not hold a Phase 03 snapshot "
            f"(read {type(loaded).__name__})"
        )
    return loaded, ""


def _refine_pre_records(
    db: Session,
    job: models.UploadJob,
    pre_map: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The Refiner's designed hook, for Output 03 (§8.3).

    ``release_refiner``'s docstring reserved this seam for "the Phase 03
    prerequisite (Pre) outputs — … the replacement outputs 03/04 join this
    seam when step 7 builds them", with an ``output_kind`` parameter so
    they join without redesign. This is that join: the Pre rows carry the
    same rendered house format (Description + Achieving Mastery, plus the
    Misconception/ Error Analysis section its own Q1 inventory allotted),
    so the same whitelist mechanics apply unchanged — nothing about the
    Refiner is widened for the Pre lane, only pointed at it.

    Like the Post lane's hook, the Refiner never blocks: any failure
    stages the UNREFINED rows with an availability flag.
    """

    from . import release_refiner

    records = [
        copy.deepcopy(dict(row))
        for row in pre_map.get("rows") or []
        if isinstance(row, Mapping)
    ]
    if not records:
        return records, {
            "policy_version": release_refiner.REFINER_POLICY_VERSION,
            "output_kind": PRE_REFINER_OUTPUT_KIND,
            "changes": [],
            "summary": "no Pre-Learning rows to refine",
            "resealed_after_refinement": False,
            "review_flags": [],
        }
    try:
        chapter = db.get(
            models.Chapter, int((job.deposit_scope_ids or [0])[0] or 0)
        )
        metadata = {
            "board": chapter.board if chapter else "",
            "grade": chapter.grade if chapter else "",
            "subject": chapter.subject if chapter else "",
            "unit": chapter.unit if chapter else "",
            "chapter_title": chapter.chapter_title if chapter else "",
            "chapter_code": chapter.chapter_code if chapter else "",
            "pre_post": "Pre",
            "source_book": job.source_book or job.filename or "",
            # Deliberately absent: the chapter's question/task inventory,
            # its mined Types, AND its source text — all three of which
            # the Post hook passes. The Pre lane extracts no question
            # from the source (owner steer, 17 Aug 2026), and the
            # chapter's MMD is not neutral prose: it contains the
            # exercises verbatim, so handing it over would open exactly
            # the channel that steer closes.
            #
            # ``source_text`` was passed here until the comment above was
            # checked against the code and found to overstate it. It cost
            # nothing to remove: ``release_refiner`` forwards it to the
            # validator only when ``pre_post == "Post"``
            # (``_terminal_error_keys``), and the model is never shown it
            # at all. Now the comment is true rather than nearly true.
            "inventory": {},
            "mined_types": {},
            "source_text": "",
        }
        refined, diff, flags = release_refiner.refine_release(
            records,
            metadata=metadata,
            instruction_set=_instruction_set_summary(job),
            store=release_refiner.decision_store_for_job(int(job.id)),
            output_kind=PRE_REFINER_OUTPUT_KIND,
        )
        return refined, {**diff, "review_flags": list(flags)}
    except Exception as exc:  # noqa: BLE001 - the Refiner never blocks
        flag = f"refiner unavailable: {type(exc).__name__}: {exc}"
        return records, {
            "policy_version": release_refiner.REFINER_POLICY_VERSION,
            "output_kind": PRE_REFINER_OUTPUT_KIND,
            "changes": [],
            "summary": flag,
            "resealed_after_refinement": False,
            "review_flags": [flag],
        }


def stage_pre_release_from_run(
    db: Session,
    job: models.UploadJob,
    *,
    target_chapter_id: int | None = None,
    inventory: Mapping[str, Any] | None = None,
    reason: str = "",
) -> dict[str, Any] | None:
    """Stage Outputs 03/04 from this run's recorded Phase 03 snapshots.

    Never raises: the Pre lane must not be able to take down a finished
    Post release ("finished work always ships"). A failure is logged and
    leaves the sibling slot empty, which every reader already treats as
    "this run built no Pre lane".

    Returns ``None`` for exactly one reason — the map snapshot is ABSENT,
    i.e. this run built no Pre lane. A snapshot that is on disk and
    unreadable is NOT that: it stages a Pre release carrying the recorded
    defect, so the reviewer sees a corrupt artefact as a corrupt artefact
    instead of as a chapter that needed nothing (R4). Which of the two
    happened is decided by the filesystem, never by anything read out of
    the file.
    """

    from . import progress

    pre_map, map_defect = _run_snapshot(job, PRE_MAP_SNAPSHOT)
    if pre_map is None and not map_defect:
        return None
    pre_questions, questions_defect = _run_snapshot(
        job, PRE_QUESTIONS_SNAPSHOT
    )
    snapshot_defects = [
        defect for defect in (map_defect, questions_defect) if defect
    ]
    for defect in snapshot_defects:
        progress.log(
            "A Phase 03 Pre-Learning snapshot could not be read: "
            f"{defect}. The Pre release is staged carrying this defect "
            "rather than as an empty lane, so nothing it authored is "
            "reported as nothing it needed; its database upload is "
            "blocked and its evidence still downloads.",
            level="error",
        )
    try:
        refined, refinements = _refine_pre_records(
            db, job, pre_map or {"rows": []}
        )
        pre_map = copy.deepcopy(dict(pre_map or {}))
        pre_map["rows"] = refined
        return stage_pre_release(
            db,
            job,
            target_chapter_id=target_chapter_id,
            pre_map=pre_map,
            pre_questions=pre_questions,
            inventory=inventory,
            reason=reason,
            refinements=refinements,
            snapshot_defects=snapshot_defects,
        )
    except Exception as exc:  # noqa: BLE001 - the Pre lane never blocks Post
        db.rollback()
        progress.log(
            "The Pre-Learning outputs could not be staged "
            f"({type(exc).__name__}: {exc}); the Post-Learning release is "
            "unaffected and ships. Outputs 03/04 are not available for this "
            "run and their absence is recorded here rather than reported as "
            "an empty Pre map.",
            level="error",
        )
        return None


def _pre_release_issues(
    pre_map: Mapping[str, Any],
    pre_questions: Mapping[str, Any],
    records: Sequence[Mapping[str, Any]],
    snapshot_defects: Sequence[str] = (),
) -> list[dict[str, Any]]:
    """Everything the Pre lane recorded, as release issues.

    Severity follows the doctrine split exactly (§4, Q10): a recorded
    REFUSAL — the fail-closed source-question barrier, which is identity
    accounting — is an ``error``; every critic dissent, Fixer decision and
    unresolved advisory is a ``warning``, because semantic doubt flags and
    never blocks. Nothing here re-judges the lane's own verdicts; it
    transcribes them so the reviewer sees them without opening the
    artifact JSON.
    """

    issues: list[dict[str, Any]] = []
    for defect in snapshot_defects:
        # An input artefact that is on disk and unreadable. Recorded as an
        # error, and (via ``structural_defects``) it blocks the database
        # write while every download stays open: "semantic doubt flags;
        # structural corruption blocks" (§4). Nothing is judged here — a
        # file either parsed or it did not.
        issues.append(_issue(
            code="pre_learning_snapshot_unreadable",
            message=(
                "A Phase 03 Pre-Learning snapshot could not be read, so "
                "what it held is missing from this release rather than "
                "absent from the chapter: " + _normal(defect)
            ),
            phase="phase03",
        ))
    map_refused = _normal(pre_map.get("refused"))
    if map_refused:
        issues.append(_issue(
            code="pre_learning_map_refused",
            message=(
                "The Pre-Learning concept map was refused and not shipped: "
                + map_refused
            ),
            phase="phase03",
        ))
    questions_refused = _normal(pre_questions.get("refused"))
    if questions_refused:
        issues.append(_issue(
            code="pre_learning_questions_refused",
            message=(
                "The generated Pre-Learning questions were refused and not "
                "shipped: " + questions_refused
            ),
            phase="phase03",
        ))
    for finding in pre_map.get("validation") or []:
        if not isinstance(finding, Mapping):
            continue
        issues.append(_issue(
            code=_normal(finding.get("code")) or "pre_learning_validation",
            message=_normal(finding.get("message")) or _normal(finding),
            severity="warning",
            phase="phase03",
            unit_id=_normal(finding.get("pre_concept_id")),
        ))
    for record in records:
        flags = [_normal(flag) for flag in record.get("review_flags") or []]
        for flag in [flag for flag in flags if flag]:
            issues.append(_issue(
                code="pre_learning_review_flag",
                message=flag,
                severity="warning",
                phase="phase03",
                unit_id=_normal(record.get("_pre_concept_id")),
                topic=_normal(record.get("topic")),
            ))
    for pre_concept_id, reason in (
        pre_questions.get("blocked") or {}
    ).items():
        issues.append(_issue(
            code="pre_learning_questions_blocked",
            message=(
                f"No question was authored for {pre_concept_id}: "
                f"{_normal(reason)}"
            ),
            severity="warning",
            phase="phase03",
            unit_id=str(pre_concept_id),
        ))
    return issues


def _account_for_source_identity(
    job: models.UploadJob,
    payload: dict[str, Any],
    inventory: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Identity accounting at Output 03's release boundary.

    The owner steer of 17 Aug 2026 makes this mechanical and names both
    halves of the Pre lane: "no QID from the chapter's question/task
    inventory may appear anywhere in a Pre row **or the Pre release
    payload**. That is identity accounting, not judgment". Output 04
    already does exactly this at its own boundary
    (``assessment_release_run``); Output 03 is a Pre release too, and
    this is the same two acts, in the same order, over the same
    identities — reused from that module rather than re-implemented, so
    the two boundaries can never drift into disagreeing about what a
    source identity is.

    THE ORDER IS THE WHOLE DESIGN, and it is the same one recorded there:

    1. **Redact the audit channels** — review flags, issues, the
       Refiner's diff, every ``_aegis_*`` marker. It never raises.
       ``premap``'s own doctrine puts review flags OUTSIDE the guarded
       surface by ordering, precisely so the advisory critic can name the
       source question it compared a prerequisite against; Q10 forbids a
       critic's dissent from gating anything. But those flags are
       transcribed verbatim into ``payload["issues"]`` and ride the
       records, so without this step a legitimate dissent puts a QID in a
       downloadable Pre artefact. Dropping an id token from auditor prose
       is plainly mechanically applicable, which is CLAUDE.md's own test
       for acting rather than stopping.
    2. **Refuse the authored surface** — the rows, cells and generated
       questions the lane actually wrote. ``premap._refuse_source_qids``
       already guarded these upstream, before any flag was stamped; this
       re-runs the same guard AFTER the Refiner's model pass has rewritten
       them, which is the one channel that reaches the authored rows after
       the upstream guard has run.

    A refusal does not delete the lane and does not stop the run. It
    stages a Pre release that records the refusal and ships no rows: the
    evidence still downloads, the database write is blocked
    (``structural_defects`` reads ``refused``), and the reviewer is told
    exactly which identity was found. Losing Outputs 03/04 without a word
    would be the R4 failure; halting the run would spend a finished
    Post release on a Pre-lane defect.
    """

    from . import assessment_release_run as release_run
    from .phase3 import premap

    source = (
        dict(inventory)
        if isinstance(inventory, Mapping) and inventory.get("items")
        else dict(job.question_inventory or {})
    )
    source_qids = premap.inventory_qids({"inventory": source})
    if not source_qids:
        return payload
    # Output 03's audit channels. The shared set covers ``review_flags``
    # and the Refiner's diff; ``issues`` is this payload's own, and it is
    # the reachable one — ``_pre_release_issues`` transcribes every
    # review flag into it verbatim, so a critic dissent naming the source
    # question it compared against lands here. The same set goes to both
    # helpers, which is the invariant they document: redacted by one,
    # skipped by the other.
    audit_keys = release_run._LEAK_GUARD_SKIPPED_KEYS | {"issues"}
    payload = release_run._redact_audit_source_qids(
        payload, source_qids, audit_keys=audit_keys,
    )
    try:
        premap._refuse_source_qids(
            release_run._authored_surface(payload, audit_keys=audit_keys),
            list(source_qids),
            where="the staged Pre-Learning release payload (Output 03)",
        )
    except premap.PreExtractionError as exc:
        payload["records"] = []
        payload["generated_questions"] = []
        payload["refused"] = "; ".join(
            part for part in (_normal(payload.get("refused")), str(exc))
            if part
        )
        issues = list(payload.get("issues") or [])
        issues.append(_issue(
            code="pre_learning_source_identity_refused",
            message=(
                "Output 03 was refused at its release boundary and ships "
                "no rows: " + str(exc)
            ),
            phase="phase03",
        ))
        payload["issues"] = _json_safe(issues)
        payload["summary"] = _release_summary([], issues)
    return payload


def stage_pre_release(
    db: Session,
    job: models.UploadJob,
    *,
    target_chapter_id: int | None = None,
    pre_map: Mapping[str, Any] | None = None,
    pre_questions: Mapping[str, Any] | None = None,
    inventory: Mapping[str, Any] | None = None,
    reason: str = "",
    refinements: Mapping[str, Any] | None = None,
    snapshot_defects: Sequence[str] = (),
) -> dict[str, Any] | None:
    """Stage Outputs 03/04 into the sibling slot on THIS job.

    Outputs 03 and 04 are projections of one accepted snapshot exactly as
    01 and 02 are (§4's first release invariant): the Pre concept rows and
    the generated questions authored against them are staged together,
    once, and every later projection reads this payload rather than
    re-deriving Pre meaning.

    Returns ``None`` — staging nothing — when the run built no Pre lane at
    all. That is not the same as an empty Pre map: a chapter that
    genuinely assumes no prior knowledge stages a real, empty Output 03
    (``pre_map`` present with zero rows), while a run that never reached
    Phase 03 stages no sibling at all. Collapsing the two would make
    "this run has no Pre map" indistinguishable from "this chapter needs
    nothing" — exactly the R4 confusion the capture snapshot's own
    absent-marker exists to prevent.

    That empty release DOWNLOADS but does not PUBLISH, and the docstring
    used to claim otherwise. ``structural_defects`` reports "the release
    contains no concept rows to upload" for it — the same refusal the
    Post lane has always made for a release with nothing to deposit, on
    the same message — so ``release_state`` reads *Diagnostic release*
    and the database write is blocked. Stated plainly rather than left as
    a contradiction between prose and code: the block is correct (there
    is genuinely nothing to write, and publishing nothing is not a
    publication), but the STATE NAME is a poor fit for a healthy chapter
    that assumes nothing, because §4 defines *Diagnostic release* as
    "structural/import integrity failed" and nothing failed here.
    Renaming it needs a fourth named state, which is a §4 change and
    therefore step 8's convergence work, not this slice's.

    ``snapshot_defects`` carries input artefacts that were on disk and
    unreadable. They ride the payload, become error issues, and block the
    upload — see ``_run_snapshot`` for why they must never be collapsed
    into "the lane authored nothing".

    The job's status, ``result_ids`` and POST slot are untouched: this is
    a sibling, and the Post release remains the job's headline state.
    """

    if pre_map is None:
        return None
    source = copy.deepcopy(dict(pre_map))
    questions_source = copy.deepcopy(dict(pre_questions or {}))
    raw_rows = [
        copy.deepcopy(dict(row))
        for row in source.get("rows") or []
        if isinstance(row, Mapping)
    ]
    questions_by_concept: dict[str, list[dict[str, Any]]] = {}
    for pre_concept_id, authored in (
        questions_source.get("questions") or {}
    ).items():
        questions_by_concept[str(pre_concept_id)] = [
            copy.deepcopy(dict(row))
            for row in authored or []
            if isinstance(row, Mapping)
        ]
    generated: list[dict[str, Any]] = []
    for row in raw_rows:
        pre_concept_id = _normal(row.get("_pre_concept_id"))
        authored = questions_by_concept.get(pre_concept_id) or []
        row[RELEASE_ROW_LANE_FIELD] = LANE_PRE
        row[PRE_ROW_GENERATED_QUESTIONS_FIELD] = [
            _normal(entry.get("pre_question_id")) for entry in authored
        ]
        generated.extend(copy.deepcopy(entry) for entry in authored)

    issues = _pre_release_issues(
        source, questions_source, raw_rows, snapshot_defects,
    )
    annotated = _annotate_records(raw_rows, issues, {})
    summary = _release_summary(annotated, issues)
    target = int(
        target_chapter_id
        or (job.deposit_scope_ids or [0])[0]
        or 0
    )
    source_document_hash = "sha256:" + hashlib.sha256(
        str(job.mmd_text or "").encode("utf-8")
    ).hexdigest()
    chapter_meta = _chapter_meta_for_release(
        db, target, annotated, pre_post="Pre",
    )
    payload = {
        "version": RELEASE_VERSION,
        RELEASE_LANE_FIELD: LANE_PRE,
        "released_at": datetime.now(timezone.utc).isoformat(),
        "release_reason": _normal(reason) or (
            "The Phase 03 Pre-Learning outputs were staged for explicit "
            "publication."
        ),
        "job_id": job.id,
        # Read by the projection writers (transient_release_hierarchy,
        # publication) to stamp Pre topics. It is a PROJECTION detail, not
        # the lane's authority — the slot is (spec T3).
        "learning_kind": LANE_PRE,
        "source_book": job.source_book,
        "filename": job.filename,
        "source_document_hash": source_document_hash,
        "target_chapter_id": target,
        "directory_metadata": _json_safe(
            _directory_metadata_for_release(db, target)
        ),
        "records": _json_safe(annotated),
        "issues": _json_safe(issues),
        # DELIBERATELY EMPTY, and this is the steer's own mechanical rule.
        # The Post payload carries the chapter's question/task inventory
        # here; the Pre payload must not, because "no QID from the
        # chapter's question/task inventory may appear anywhere in a Pre
        # row OR THE PRE RELEASE PAYLOAD" (owner steer, 17 Aug 2026) — and
        # the inventory is not merely QIDs, it is the source questions
        # themselves, ``raw_task`` and all, in an artefact the reviewer
        # downloads. The key stays, empty, so every reader of a release
        # payload finds the shape it expects.
        #
        # Output 04's leak barrier still needs that identity set, and this
        # is exactly why it does not read it from here: it reads it from
        # the job's own inventory column and the Post sibling slot (see
        # ``assessment_release_run._generated_lane_source_qids``), which
        # hold it without putting it in a Pre artefact.
        "question_task_inventory": {},
        "chapter_meta": _json_safe(chapter_meta),
        "snapshot_defects": _json_safe(
            [_normal(defect) for defect in snapshot_defects if _normal(defect)]
        ),
        "refused": _normal(source.get("refused")),
        "pre_topics": _json_safe([
            topic for topic in source.get("topics") or []
            if isinstance(topic, Mapping)
        ]),
        "needed_for": _json_safe(source.get("needed_for") or {}),
        "analysis": _json_safe(source.get("analysis") or {}),
        # Output 04's material, staged beside Output 03 so the pair is
        # projected from one snapshot and the assessment lane never
        # re-derives Pre meaning.
        "generated_questions": _json_safe(generated),
        "generated_question_plans": _json_safe(
            questions_source.get("plans") or {}
        ),
        "generated_questions_blocked": _json_safe(
            questions_source.get("blocked") or {}
        ),
        "generated_questions_refused": _normal(
            questions_source.get("refused")
        ),
        "instruction_set": _json_safe(_instruction_set_summary(job)),
        "summary": summary,
    }
    if refinements is not None:
        payload["refinements"] = _json_safe(dict(refinements))
    payload = _account_for_source_identity(job, payload, inventory)
    durable_inventory = copy.deepcopy(dict(job.question_inventory or {}))
    durable_inventory[PRE_RELEASE_KEY] = copy.deepcopy(payload)
    job.question_inventory = durable_inventory
    db.commit()
    db.refresh(job)
    return release_result(job, lane=LANE_PRE)


def release_result(
    job: models.UploadJob, *, lane: object = LANE_POST,
) -> dict[str, Any]:
    resolved = normalize_lane(lane)
    payload = release_payload(job, lane=resolved)
    if payload is None:
        raise ReleaseUnavailableError("this upload has no staged release")
    if resolved == LANE_PRE:
        summary = copy.deepcopy(payload.get("summary") or {})
        query = f"?lane={LANE_PRE}"
        return {
            "job_id": job.id,
            "lane": LANE_PRE,
            "status": RELEASE_STATUS,
            "released": True,
            "release_state": release_state(payload),
            "structural_defects": structural_defects(payload),
            "database_uploaded": bool(summary.get("database_uploaded")),
            "row_count": int(summary.get("row_count") or 0),
            "affected_row_count": int(summary.get("affected_row_count") or 0),
            "issue_count": int(summary.get("issue_count") or 0),
            "generated_question_count": len(
                payload.get("generated_questions") or []
            ),
            "release_workbook_url": (
                f"/build-concepts/uploads/{job.id}/release.xlsx{query}"
            ),
            "release_bulk_import_url": (
                f"/build-concepts/uploads/{job.id}"
                f"/release-bulk-import.xlsx{query}"
            ),
            "diagnostics_url": (
                f"/build-concepts/uploads/{job.id}/diagnostics.zip{query}"
            ),
            "release_payload_url": (
                f"/build-concepts/uploads/{job.id}/release.json{query}"
            ),
            "database_upload_url": (
                f"/build-concepts/uploads/{job.id}/upload-release{query}"
            ),
            "detail": job.detail,
        }
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

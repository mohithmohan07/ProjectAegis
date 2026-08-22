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
one under ``RELEASE_KEY`` (Outputs 03/04) and the Pre-Learning one under
the sibling ``PRE_RELEASE_KEY`` (Outputs 01/02). They are siblings, not a
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
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from uuid import uuid4

from sqlalchemy.orm import Session

from .. import models
from . import generation, uploads


RELEASE_VERSION = "aegis-concept-release-1"
RELEASE_KEY = "_aegis_release_output"
# The Pre-Learning sibling slot (spec T3). Outputs 01/02 live here; the
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
# The staged draft's VERSION (spec-step8 T1/T2/S6).
#
# This slot used to be overwritten in place — ``durable_inventory[KEY] =
# payload`` — which is why §7:551's "a new immutable release version per
# applied round" was unexpressible on it and why the hazard T2 names was
# live: publish the Concept File, then ``force_release`` re-stages, the
# payload changes, ``source_release_sha256`` changes, and the already
# published Master can never be matched to its source again.
#
# The slot is still ONE slot — after the convergence it is the staging
# DRAFT, and ``models.AssessmentRelease`` is the immutable row of record
# (T2). What changes here is that every staging MINTS a version instead
# of silently replacing what was there: the number is monotonic per lane,
# it travels into the frozen row's ``provider_identity``, and a re-stage
# after a freeze is therefore visible on both sides instead of being an
# overwrite nothing recorded.
STAGED_VERSION_FIELD = "staged_version"
# The draft's LINEAGE (S10 repair, Round 7). The version above is a per-slot
# counter, and [measured] it RESTARTS at 1 whenever ``question_inventory``
# is legitimately cleared (``replace_file``, ``convert_job``, the
# source-review checkpoint reset) — so "same version" cannot distinguish
# "the same draft, edited in place" from "a fresh draft that happens to
# count from 1 again", and the seal gate built on the version alone accused
# a legitimate fresh run of tampering. Every staging act therefore mints one
# opaque uid; the freeze records it; the seal gate compares seals only
# within one recorded lineage.
STAGED_RELEASE_UID_FIELD = "staged_release_uid"
# One recorded, named issue: the lane's Master File did not build on this
# run (spec-step8 T15-2). It is an ISSUE, never a structural defect — the
# CONCEPT payload is not corrupt, a later lane simply did not produce, and
# refusing the concept lane's database write for a fault in a different
# lane is Rule G's two-publication separation collapsed.
ASSESSMENT_LANE_UNAVAILABLE = "assessment_lane_unavailable"
RELEASE_STATUS = "released"
RELEASE_ROW_STATUS_FIELD = "_aegis_release_status"
RELEASE_ROW_ERRORS_FIELD = "_aegis_release_errors"
RELEASE_ROW_QIDS_FIELD = "_aegis_release_qids"
RELEASE_ROW_BLOCKS_FIELD = "_aegis_release_block_ids"
RELEASE_ROW_ROUTES_FIELD = "_aegis_release_type_case_routes"
RELEASE_ROW_REFINED_FIELD = "_aegis_release_refined"
RELEASE_ROW_LANE_FIELD = "_aegis_release_lane"
PRE_ROW_GENERATED_QUESTIONS_FIELD = "_aegis_pre_generated_questions"
# OD3/T3.3: the Pre row's ``related_concepts`` CONTENT — the resolved,
# persisted Post ``machine_id``s this pre-concept is needed for. Resolved
# at STAGING (there is no durable Post identity to join on at render time,
# and a title join is forbidden by T11.2), stamped here, read by the
# renderer, and lifted onto the DB column at publication so the column does
# not empty the moment a reviewer clicks Upload (T3.3b).
PRE_ROW_RELATED_CONCEPTS_FIELD = "_aegis_pre_related_concepts"
# A link that does not resolve is a RECORDED REVIEW FLAG on the row, never
# a blank and never a block (T3.3).
PRE_ROW_RELATED_UNRESOLVED_FIELD = "_aegis_pre_related_concepts_unresolved"
# Step 9 (doc §7): the reviewer edit trail a review round stamps on every
# row it touched — who changed which field, from what, to what, in which
# staged version. Rides the staged payload for the audit; the published
# concept row carries the edited VALUES, never the trail.
MANUAL_EDIT_TRAIL_FIELD = "_aegis_manual_edit_rounds"

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
    # before DB upload. CORRECTED (spec-step8 T3.3): only ONE of the two
    # has ``related_concepts`` as its column home. ``_aegis_needed_for``
    # does — resolved to persisted Post ``machine_id``s at Pre staging and
    # carried on ``PRE_ROW_RELATED_CONCEPTS_FIELD`` below.
    # ``_aegis_pre_prerequisites`` does NOT: it is
    # ``{prerequisite_id, text}``, copied provenance prose rather than a
    # label, and putting prose in a join column would corrupt the join.
    "_aegis_pre_prerequisites",
    "_aegis_needed_for",
    # The RESOLVED form of ``_aegis_needed_for``: the persisted Post
    # ``machine_id``s this Pre concept is needed for, newline-joined
    # (concept titles legitimately contain commas). Publication LIFTS it
    # into an explicit ``related_concepts`` key before this frozenset is
    # applied — see ``_lift_resolved_related_concepts`` — because
    # ``_strip_release_fields`` drops every key here by construction.
    "_aegis_pre_related_concepts",
    "_aegis_pre_related_concepts_unresolved",
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
    # Output 01 (spec T3): the row-private marks the Pre release stamps on
    # every one of its concept rows. ``_aegis_release_lane`` says which
    # lane's slot the row shipped in, so a row lifted out of the payload
    # into a diagnostics export still says what it is; and
    # ``_aegis_pre_generated_questions`` carries the identities of the
    # GENERATED questions Output 02 authored for that pre-concept, so a
    # reviewer holding Output 01 can see what Output 02 shipped for each
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
    # Step 9's reviewer edit trail (doc §7, release_review.py): preserved
    # verbatim in the staged payload across rounds, stripped before DB
    # publication — the reviewer's edited values publish, the audit of how
    # they got there stays in the release record.
    MANUAL_EDIT_TRAIL_FIELD,
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


def _job_inventory_fallback(job: models.UploadJob) -> dict[str, Any]:
    """The job's stored inventory, with the lane release slots removed.

    The fallback source when neither the caller nor the resolved
    checkpoint supplies an inventory. A release-first job keeps its
    staged releases INSIDE ``question_inventory`` under the lane keys;
    copying those into a new payload's inventory would nest releases
    within releases, so they are stripped here — mechanics only.
    """

    value = dict(job.question_inventory or {})
    value.pop(RELEASE_KEY, None)
    value.pop(PRE_RELEASE_KEY, None)
    return value


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
    lane that existed before Outputs 01/02 did, so every caller that has
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


def staged_version(payload: Mapping[str, Any] | None) -> int:
    """The staged draft's version, or 0 when it carries none.

    0 means "staged before this counter existed", not "version zero": a
    release payload recorded by an earlier build has no such key and must
    keep opening.
    """

    if not isinstance(payload, Mapping):
        return 0
    try:
        return int(payload.get(STAGED_VERSION_FIELD) or 0)
    except (TypeError, ValueError):
        return 0


def next_staged_version(
    job: models.UploadJob, *, lane: object = LANE_POST,
) -> int:
    """Mint the next version for one lane's staging slot.

    Mechanics: read what is in the slot, add one. Per LANE, because the
    two lanes are separate drafts with separate publications (Rule G), and
    a shared counter would make a Pre re-stage look like a Post one.
    """

    return staged_version(release_payload(job, lane=lane)) + 1


def assessment_lane_issue(
    payload: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """The recorded ``assessment_lane_unavailable`` issue, or None.

    The read half of the transport ``record_assessment_lane_unavailable``
    writes: the manifest's Master entry uses it as the stated reason the
    entry is disabled, so the reviewer is told WHY the file is missing
    instead of only that it is.
    """

    if not isinstance(payload, Mapping):
        return None
    recorded = [
        dict(issue)
        for issue in payload.get("issues") or []
        if isinstance(issue, Mapping)
        and _normal(issue.get("code")) == ASSESSMENT_LANE_UNAVAILABLE
    ]
    return recorded[-1] if recorded else None


def record_assessment_lane_unavailable(
    db: Session,
    job: models.UploadJob,
    *,
    lane: object,
    error: BaseException,
) -> dict[str, Any] | None:
    """Record that one lane's Master File did not build, and keep going.

    Q13, and the reason is the whole point rather than a robustness
    nicety: by the time this is called the two CONCEPT outputs are
    finished and durable — the payload is staged, the bulk-import workbook
    already renders for both lanes. An exception escaping the assessment
    lane would propagate out of ``generate_post_learning`` and take them
    with it: a mid-run halt after the model budget is spent, which
    CLAUDE.md and Q13 forbid. So the failure is CAUGHT, and then it is
    NAMED — nothing is guessed silently and nothing finished is lost.

    It touches ``issues`` and NOTHING else, and both halves of that are
    deliberate:

    * it is not a re-stage. It is also seal-safe by measurement —
      ``assessment_release_snapshot.source_release_sha256`` hashes
      thirteen payload keys and ``issues`` is not among them — so
      appending here moves no seal, no ``machine_id`` and no
      ``concept_snapshot_sha256``, and the frozen Master of the OTHER
      lane still matches its source.
    * it does not touch ``summary``. The concept release's
      ``release_state`` reads the summary counts, and a later lane's
      fault must not change the CONCEPT lane's public state or close its
      database upload (T15-2). The counts and this ledger therefore
      disagree by one on a failed run, deliberately: the alternative is a
      Master-lane fault silently downgrading a clean Concept File.

    Never raises. A recorder that can raise is the same defect one level
    up.
    """

    try:
        resolved = normalize_lane(lane)
        payload = release_payload(job, lane=resolved)
        if payload is None:
            return None
        issue = _issue(
            code=ASSESSMENT_LANE_UNAVAILABLE,
            severity="error",
            phase="assessment_release",
            message=(
                f"The {resolved!r} lane's Master File was not built on this "
                f"run: {type(error).__name__}: {error}. The Concept File for "
                "this lane is unaffected and its database upload is still "
                "open; re-run the lane from the release page."
            ),
            details={
                "lane": resolved,
                "exception": type(error).__name__,
                "error": str(error),
                STAGED_VERSION_FIELD: staged_version(payload),
            },
        )
        key = release_key_for_lane(resolved)
        durable = copy.deepcopy(dict(job.question_inventory or {}))
        slot = durable.get(key)
        if not isinstance(slot, Mapping):
            return None
        slot = copy.deepcopy(dict(slot))
        slot["issues"] = list(slot.get("issues") or []) + [issue]
        durable[key] = slot
        job.question_inventory = durable
        db.commit()
        db.refresh(job)
        return issue
    except Exception:  # pragma: no cover - the recorder must never raise
        try:
            db.rollback()
        except Exception:
            pass
        return None


def release_available(
    job: models.UploadJob, *, lane: object = LANE_POST,
) -> bool:
    return release_payload(job, lane=lane) is not None


def pre_release_available(job: models.UploadJob) -> bool:
    return release_payload(job, lane=LANE_PRE) is not None


def staged_generated_questions(
    job: models.UploadJob,
) -> list[dict[str, Any]] | None:
    """The GENERATED questions the staged Pre release carries for Output 02.

    None when there is no staged Pre release at all — which is different
    from a staged Pre release that authored no question, and the two must
    stay distinguishable: the first means "this run built no Pre lane",
    the second means "it built one and it is empty", and only the second
    is a legal empty Output 02.
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

# --------------------------------------------------------------------------- #
# S9 — the row-level projection gate, decided ONCE at staging
#
# D8.5's proof: ``structural_defects`` is a pure function of the staged
# payload, and ``transient_release_hierarchy`` runs only at DOWNLOAD time.
# A row skipped at projection time could therefore reach the release state
# only by mutating a frozen payload during a GET. So the verdict is taken
# at STAGING, rides the payload under ``staged_row_defects``, and the
# projection reads a decision the payload already holds.
#
# ONE implementation for both (T1): ``build_concepts_release_files.
# transient_release_hierarchy`` calls the same function on the same rows,
# so the reviewer's issue and the skipped row can never disagree.
#
# It compares presence of the two join identities. It judges nothing about
# what a row MEANS — a gate under CLAUDE.md's "mechanics, not meaning".
# --------------------------------------------------------------------------- #

STAGED_ROW_DEFECTS_FIELD = "staged_row_defects"
STAGED_ROW_UNUSABLE = "staged_row_unusable"

# T9-1's closed concept-lane identity set (S11): issue codes that name an
# IDENTITY corruption and therefore block the database write. Enumerated,
# never derived — extending it is a deliberate spec change, not a one-line
# edit. T9-2's stays-flags list (``unassigned_inventory_qid``,
# ``case_uniqueness_qid_render_count_mismatch``,
# ``case_uniqueness_example_less_case_shell``) is deliberately absent.
# T9-1's machine-id pair (a duplicate persisted id, a row still blank
# after the mint) is NOT here and cannot be: machine ids exist only on
# persisted rows, never in this payload (S10), so those two block at the
# publication act, where the ids exist (Round 9, correcting Round 8's
# mis-assignment).
T9_IDENTITY_DEFECT_CODES = frozenset({
    "duplicate_qid_assignment",
    "unknown_type_case_qid",
    "case_uniqueness_duplicate_case_identity",
    "case_uniqueness_duplicate_qid_route",
    "type_catalog_unreadable",
})

# Round 9: the QC audit's blocking findings ride their OWN named key.
# V2/T10-0 ordered them onto ``snapshot_defects`` — and [measured] that
# reader stamps every entry "an input snapshot could not be read", a false
# factual preamble on a coverage finding, and the Pre lane's issue minting
# turned each one into a spurious ``pre_learning_snapshot_unreadable``.
# The receipt may not claim a fact nobody measured, so the blocking set
# gets a key whose reader says what it is; ``snapshot_defects`` keeps its
# original meaning (input-artifact unreadability) on both lanes.
QC_BLOCKING_FIELD = "qc_blocking_defects"


def row_projection_defect(
    row: object, position: int,
) -> dict[str, Any] | None:
    """Why this staged concept row cannot be projected, or ``None``.

    The three conditions ``transient_release_hierarchy`` used to raise on,
    one row at a time. Raising cost every one of the four outputs for one
    bad row; naming the row costs the row and ships the rest (Rule E).
    """

    if not isinstance(row, Mapping):
        return {
            "code": STAGED_ROW_UNUSABLE,
            "position": int(position),
            "message": (
                f"staged concept row {position} is not an object, so it "
                "reaches no workbook row and no database row"
            ),
        }
    if not _normal(row.get("topic")):
        return {
            "code": STAGED_ROW_UNUSABLE,
            "position": int(position),
            "concept_title": _normal(
                row.get("concept_title") or row.get("concept")
            ),
            "message": (
                f"staged concept row {position} has no topic, so it has no "
                "place in the chapter hierarchy the outputs are projected "
                "onto"
            ),
        }
    if not _normal(row.get("concept_title") or row.get("concept")):
        return {
            "code": STAGED_ROW_UNUSABLE,
            "position": int(position),
            "topic": _normal(row.get("topic")),
            "message": (
                f"staged concept row {position} has no concept title, so it "
                "cannot be addressed by a machine id or a workbook cell"
            ),
        }
    return None


def staged_row_defects(records: object) -> list[dict[str, Any]]:
    """Every unusable row in one staged record list, in source order."""

    findings: list[dict[str, Any]] = []
    for position, row in enumerate(records or [], start=1):
        finding = row_projection_defect(row, position)
        if finding is not None:
            findings.append(finding)
    return findings


# --------------------------------------------------------------------------- #
# S9 / D8.3 — the Pre lane's empty-capture verdict, as the payload records it
#
# ``phase3/premap.build`` spends ONE model verdict when the prerequisite
# capture is empty: does this chapter genuinely assume no prior knowledge,
# or did the capture fail to reach it? An empty list cannot answer that,
# and inferring "empty therefore fine" is exactly the shape-inference
# Rule 1 forbids. The verdict rides the Pre map, then this payload, and
# ``structural_defects`` reads it — it never re-decides it.
# --------------------------------------------------------------------------- #

PRE_LANE_VERDICT_FIELD = "pre_lane_verdict"
PRE_LANE_ASSUMES_NOTHING = "assumes_nothing"
PRE_LANE_CAPTURE_INCOMPLETE = "capture_incomplete"


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


# The two keys that make a payload the ASSESSMENT release's payload rather
# than a concept release's. This reads which SCHEMA is in hand — not what
# any content means — and the two shapes are disjoint by construction:
# neither staged concept payload (Post or Pre) carries either key, and the
# assessment payload carries no ``records``.
#
# It exists because ``release_state`` became the ONE public vocabulary for
# both release systems (spec-step8 T1). [measured] before this,
# ``release_state({"source_atoms": …, "candidates": …, "groups": …})``
# returned ``diagnostic_release``, because the only depositable unit this
# function knew about was a concept row — so every healthy assessment
# release was labelled structurally corrupt the moment the shared core
# started asking.
_ASSESSMENT_PAYLOAD_KEYS = ("candidates", "groups")


def _is_assessment_payload(payload: Mapping[str, Any]) -> bool:
    return any(key in payload for key in _ASSESSMENT_PAYLOAD_KEYS)


def _publishable_candidate(candidate: object) -> bool:
    """The assessment lane's depositable unit.

    The same mechanic one level over: it has the two identities the
    assessment upsert joins on — its home concept and its group. No
    reading of what the question means, and no judgment about whether it
    is a GOOD question; that is what the flags and the model's own
    verdicts are for.
    """

    return (
        isinstance(candidate, Mapping)
        and bool(_normal(candidate.get("concept_key")))
        and bool(_normal(candidate.get("group_key")))
    )


def nothing_to_publish(payload: Mapping[str, Any] | None) -> bool:
    """True when this lane has no depositable unit — EMPTINESS, not corruption.

    Split out of ``structural_defects`` by spec-step8 D8.1, because the two
    answer different questions and conflating them refused an
    empty-but-correct Pre release as if it were corrupt. "This release has
    nothing in it" and "this release is broken" are not the same fact, and
    only the second one is a reason to distrust what IS in it.

    What follows from emptiness is D8.2: publishing nothing is the
    idempotent zero-row success, not an error — §4:475-478 asks
    publication to be "idempotent, model-free, never drops a highlighted
    row", and writing nothing when there is nothing IS that answer.

    Counts identities; reads no content.
    """

    if not isinstance(payload, Mapping):
        return True
    if _is_assessment_payload(payload):
        return not [
            candidate for candidate in payload.get("candidates") or []
            if _publishable_candidate(candidate)
        ]
    return not [
        row for row in payload.get("records") or []
        if _publishable_record(row)
    ]


def structural_defects(payload: Mapping[str, Any] | None) -> list[str]:
    """Structural/import-integrity defects that block the database upload.

    "Semantic doubt flags; structural corruption blocks" (§4). This is the
    structural half: a lane that refused its own artefact (the fail-closed
    source-question barrier), an input snapshot that was on disk and
    unreadable, a staged row that cannot be projected onto any output, and
    a Pre lane whose empty capture no verdict has accounted for. A gate
    that refuses a broken artifact, judging nothing about content: it
    counts identities, reads a recorded refusal, reads whether a file
    parsed, and reads a verdict the model already made.

    **CORRUPTION ONLY, since spec-step8 D8.1.** "The release contains no
    concept rows to upload" has MOVED to ``nothing_to_publish`` — it is
    emptiness, not corruption, and while it lived here an empty-but-correct
    Pre release was named *Diagnostic release* and refused as if the
    artefact were broken. §4 defines that name as "structural/import
    integrity failed"; nothing failed. This is why no fourth state was
    needed: the two questions were always two questions.

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
    if _is_assessment_payload(payload):
        # B's payload shape (spec-step8 S6). Its depositable unit is a
        # candidate carrying its home concept and its group, not a
        # concept row — asking for ``records`` here labelled every
        # healthy assessment release ``diagnostic_release``.
        #
        # This does NOT take over the assessment lane's publication gate.
        # That stays where it is: ``diagnostics["payload_errors"]`` and
        # ``_readiness``'s read of the renderer's ``unplaced`` list, both
        # in ``assessment_release_service``, are what refuse the write.
        # This function only says which of the three §4 NAMES the row
        # wears, through one vocabulary for both lanes.
        #
        # RESIDUE, named rather than left implicit: D8.1's emptiness split
        # is applied to the CONCEPT branch only. The assessment lane's own
        # publication act (``assessment_release_service``) is S10's
        # convergence, and moving its zero-candidate refusal out of here
        # ahead of that would open its database write with nothing on the
        # other side yet deciding a zero-row publication is correct for a
        # lane whose depositable unit is a QUESTION.
        if nothing_to_publish(payload):
            return ["the release contains no assessment rows to upload"]
        return []
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
    # A ``records`` value that is not an array carries no row at all, and
    # the emptiness split must not let it read as an empty release. It is
    # CORRUPTION — [measured] with only ``nothing_to_publish`` answering,
    # ``records={"a": 1}`` returned no defect, read *Ready with flags* and
    # published as a zero-row success, because ``payload.get("records") or
    # []`` iterates a dict's KEYS and none of them is a publishable record.
    # A pure read of the payload's own shape; it judges no content.
    records = payload.get("records")
    if records is not None and not isinstance(records, list):
        defects.append(
            "the staged release concept records are not an array, so no "
            "concept row can be read from them and this release is corrupt "
            "rather than empty"
        )
    # S9: the row-level verdict the payload already holds, taken at staging
    # (D8.5). Every entry names one row that reaches no output — the
    # producer whose consumer this is.
    #
    # FAIL CLOSED WHEN THE PAYLOAD DOES NOT HOLD IT. A payload with no
    # ``staged_row_defects`` key at all is not a payload with no defects —
    # it is a payload staged before this key existed, restored from an
    # export, or built by a caller other than the two staging functions.
    # [measured] reading the key alone, a Post payload whose single record
    # had no ``topic`` and no such key returned NO defect, read *Ready with
    # flags*, and published ``database_uploaded: True`` with an empty id
    # list and a receipt that said the emptiness was correct — the row
    # dropped in silence, and ``database_uploaded`` latched so no later
    # attempt could find it. At 76c84fb that same payload was refused.
    # Recomputing is legal and cannot drift: ``staged_row_defects`` is a
    # pure function of the payload calling the SAME
    # ``row_projection_defect`` the projection and the staging pass call,
    # and D8.5 forbids a projection-time DISCOVERY mutating a frozen
    # payload, not a pure recomputation inside the gate.
    recorded = (
        payload.get(STAGED_ROW_DEFECTS_FIELD)
        if STAGED_ROW_DEFECTS_FIELD in payload
        else staged_row_defects(records if isinstance(records, list) else [])
    )
    for finding in recorded or []:
        if not isinstance(finding, Mapping):
            continue
        message = _normal(finding.get("message"))
        if message:
            defects.append(
                "a staged concept row cannot be projected onto any output, "
                f"so publishing this release would deposit it nowhere: "
                f"{message}"
            )
    # T9-1 (S11): the concept lane's CLOSED identity set. A recorded issue
    # whose code names an identity corruption — the same QID placed twice,
    # a rendered QID the inventory never owned, a duplicate rendered Case
    # identity or QID route, a Type catalog that would not parse — blocks
    # the DATABASE WRITE, never a download. [measured] before this,
    # ``duplicate_qid_assignment`` was an ordinary (error-severity) issue
    # that this gate simply never read, so a duplicate QID published
    # without complaint while keyword checks halted finished runs — the
    # exact polarity T9 inverts. The set is CLOSED and enumerated: T9-2
    # keeps ``unassigned_inventory_qid`` (a coverage verdict — R4 says
    # Placed OR Flagged) and the two text-derived ``case_uniqueness_*``
    # codes (``qid_render_count_mismatch``, ``example_less_case_shell``)
    # as flags, because a reviewer reword of ``concept_details`` must
    # never refuse the upload on a deterministic prose comparison (§7:577).
    #
    # Round 9, twice over: (a) FAIL CLOSED like the row gate above; (b) NO
    # severity carve-out — T9-1 conditions blocking on the CODE, and a
    # display severity must not be a one-word side door out of a set whose
    # own comment says extending it is a spec change.
    #
    # Round 10 (audit finding 9a): the recompute is UNCONDITIONAL and
    # UNIONED with the recorded list. Round 9 recomputed only when the
    # ``issues`` key was ABSENT, so a present-but-stale list won — replacing
    # the recorded issues with ``[]`` was one in-place edit away from
    # publishing a ``duplicate_qid_assignment``. The T9 identity issues are
    # a pure function of the payload's own records/mined-Type material
    # (the same recompute the absent-key branch already ran), so BOTH
    # sources are read: an honest record nothing recomputes still blocks,
    # and a recomputed corruption nothing records blocks too — neither
    # absence nor tampering can open the gate. Mechanics, not meaning: the
    # union counts identities twice over and judges no content.
    recorded_issues = payload.get("issues")
    identity_sources: list[Any] = (
        list(recorded_issues)
        if isinstance(recorded_issues, (list, tuple))
        else []
    )
    identity_sources.extend(_recomputed_identity_issues(payload, records))
    seen_identity_defects: set[str] = set()
    for issue in identity_sources:
        if not isinstance(issue, Mapping):
            continue
        code = _normal(issue.get("code"))
        if code not in T9_IDENTITY_DEFECT_CODES:
            continue
        line = (
            f"identity corruption recorded by the release audit "
            f"({code}): {_normal(issue.get('message'))}"
        )
        if line in seen_identity_defects:
            continue
        seen_identity_defects.add(line)
        defects.append(line)
    # Round 9: the QC audit's blocking findings, under their own honest
    # preamble — never the input-snapshot sentence, which states a fact
    # these findings did not measure.
    #
    # Round 10 (audit finding 9b): the recorded key is UNIONED with a live
    # recompute, exactly like the identity gate above. Round 9 trusted
    # ``qc_blocking_defects`` verbatim, so clearing the list was one
    # in-place edit away from publishing an unaccounted question.
    # ``release_qc.audit`` is a pure function of this payload's own
    # material (it is what wrote the recorded key at staging) and it NEVER
    # raises — a pass that cannot run returns its own named blocking
    # finding — so recomputing here is cheap and honest. Chosen over
    # sealing the key into ``source_release_sha256``: the seal exists only
    # once a Master froze this draft, while the recompute holds from the
    # moment the payload exists — and the seal deliberately excludes
    # ``issues``/``summary`` so the publication's own recorded writes
    # never move it (see ``_refuse_a_moved_seal``).
    recorded_qc = list(dict.fromkeys(
        text
        for defect in payload.get(QC_BLOCKING_FIELD) or []
        if (text := _normal(defect))
    ))
    from . import release_qc as _release_qc

    recomputed_qc = list(dict.fromkeys(
        text
        for defect in _release_qc.audit(payload)[1]
        if (text := _normal(defect))
    ))
    for text in recorded_qc:
        defects.append(
            f"the release QC audit recorded a blocking finding: {text}"
        )
    recorded_qc_set = set(recorded_qc)
    for text in recomputed_qc:
        if text in recorded_qc_set:
            continue
        defects.append(
            "the release QC audit recomputes a blocking finding this "
            f"payload does not record: {text}"
        )
    defects.extend(_pre_lane_verdict_defects(payload))
    return defects


def _recomputed_identity_issues(
    payload: Mapping[str, Any], records: object,
) -> list[dict[str, Any]]:
    """The T9 identity issues, recomputed from the payload's own material.

    Pure, like the row-defect recompute above: ``audit_type_cases`` and
    ``_case_uniqueness_issues`` are functions of the payload's
    ``mined_types`` and inventory. Round 10 (audit finding 9a) runs this
    UNCONDITIONALLY and unions it with the recorded ``issues`` — a payload
    stripped of the key, or one whose present list went stale or was
    emptied in place, cannot pass the identity gate on what it records.
    """

    mined = payload.get("mined_types")
    inventory = payload.get("question_task_inventory")
    try:
        _rows, issues, _routes = audit_type_cases(
            dict(mined) if isinstance(mined, Mapping) else {},
            dict(inventory) if isinstance(inventory, Mapping) else {},
        )
    except Exception as exc:  # noqa: BLE001 — named and blocking, never a void
        issues = [_issue(
            code="type_catalog_unreadable",
            message=(
                "the identity recompute could not read the staged "
                f"mined-Type material ({type(exc).__name__}: {exc}); the "
                "release cannot pass the identity gate on absence"
            ),
            severity="error",
            phase="type_case_release",
        )]
    issues = list(issues)
    issues.extend(_case_uniqueness_issues(
        [row for row in (records if isinstance(records, list) else [])
         if isinstance(row, Mapping)],
        mined,
    ))
    return issues


def _pre_lane_verdict_defects(payload: Mapping[str, Any]) -> list[str]:
    """The empty Pre lane's one question: was the emptiness DECIDED?

    D8.3. ``lane_content_decided`` recorded "Phase 03 ran", which cannot
    tell an OCR-degraded scan, a thin lower-grade chapter and a chapter
    that genuinely assumes nothing apart. So premap spends one verdict and
    this reads it — an empty capture opens the zero-row publication only
    on a positive ``assumes_nothing``; ``capture_incomplete``, a Fixer
    decision that could not reach either, and a run that recorded no
    verdict at all all keep the release *Diagnostic*.

    Reads a recorded verdict. Decides nothing itself, and above all does
    not answer the question with a count.
    """

    if _normal(payload.get(RELEASE_LANE_FIELD)) != LANE_PRE:
        return []
    if not nothing_to_publish(payload):
        return []
    verdict_row = payload.get(PRE_LANE_VERDICT_FIELD)
    verdict_row = verdict_row if isinstance(verdict_row, Mapping) else {}
    verdict = _normal(verdict_row.get("verdict"))
    if verdict == PRE_LANE_ASSUMES_NOTHING:
        return []
    if not verdict:
        return [
            "this Pre-Learning release has no concept row and no recorded "
            "verdict on whether the chapter genuinely assumes no prior "
            "knowledge, so an empty capture and a failed one are not "
            "distinguishable here"
        ]
    return [
        "the Pre-Learning capture was empty and the run decided "
        f"{verdict!r} rather than {PRE_LANE_ASSUMES_NOTHING!r}, so what "
        "this release is missing may be missing from the CAPTURE rather "
        "than from the chapter"
        + (
            f": {_normal(verdict_row.get('rationale'))}"
            if _normal(verdict_row.get("rationale"))
            else ""
        )
    ]


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
        if provenance.get("task_verdict_ledger_applied"):
            # QX (docs/spec-qx.md): the text lane has no outline, but every
            # source block received a recorded model membership verdict, so
            # "nobody read this chapter" is no longer true. Record the
            # authority positively instead of warning about the outline.
            issues.append(_issue(
                code="task_membership_model_verdict",
                severity="info",
                phase="source-conversion",
                message=(
                    "Task membership for this chapter was adjudicated "
                    "block-by-block by the QX author verdict ledger "
                    f"({int(provenance.get('task_missed_asks_recovered') or 0)}"
                    " ask(s) recovered beyond the parser, "
                    f"{int(provenance.get('task_candidates_rejected') or 0)}"
                    " candidate(s) ruled not_task)."
                ),
                details=dict(provenance),
            ))
        else:
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
    fixer_count = int(provenance.get("task_fixer_decisions") or 0)
    if fixer_count:
        issues.append(_issue(
            code="task_membership_fixer_decided",
            severity="warning",
            phase="source-conversion",
            message=(
                f"{fixer_count} source block(s) could not be decided by the "
                "ordinary QX author pass; The Fixer recorded a best-judgment "
                "membership verdict for each and the affected occurrences "
                "carry review flags."
            ),
            details=dict(provenance),
        ))
    dissents = int(provenance.get("task_critic_dissents") or 0)
    ledger_flags = [
        str(flag)
        for flag in provenance.get("task_ledger_review_flags") or []
    ]
    if dissents or ledger_flags:
        issues.append(_issue(
            code="task_membership_critic_dissent",
            severity="warning",
            phase="source-conversion",
            message=(
                f"The QX membership critic recorded {dissents} dissent(s); "
                "the affected occurrences carry review flags"
                + (
                    f" and {len(ledger_flags)} finding(s) could not be "
                    "attached to a single occurrence (see details)."
                    if ledger_flags else "."
                )
            ),
            details=ledger_flags or dict(provenance),
        ))
    if provenance.get("task_critic_unavailable"):
        issues.append(_issue(
            code="task_membership_critic_unavailable",
            severity="info",
            phase="source-conversion",
            message=(
                "The QX membership critic pass was unavailable for this "
                "chapter; the author verdicts stand unreviewed: "
                + str(provenance.get("task_critic_unavailable"))
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


def _question_text_key(value: object) -> str:
    """Compare questions by their exact text, losslessly normalized.

    T10-5 (S11) made the key Unicode-aware; Round 10 (audit finding 18)
    makes it LOSSLESS. [measured] the successor key (a ``[^\\w\\s]+`` noise
    class plus a leading item-marker strip) was still a semantic
    classifier: Python's ``\\w`` excludes Unicode combining marks, so the
    noise class deleted every Devanagari vowel sign and two DISTINCT
    strings (e.g. one differing from the other only by a matra) collided
    into one false ``repeated_question_text`` warning. The item-marker
    strip was a shape judgment ("a leading '(iv)' is numbering") that
    cannot be verified lossless, so it is gone too (CLAUDE.md Rule 1 — the
    mechanical duplicate warning may only compare, never classify).

    What remains is exactly the lossless set: NFC canonical normalization
    (one encoding of the same glyphs), whitespace-run collapse
    (``_normal``), and ``casefold`` — nothing is deleted, so no two
    distinct wordings can be folded into one.
    """
    text = unicodedata.normalize("NFC", _normal(value)).casefold()
    # Casefolding can denormalize a composed sequence; re-normalize so the
    # key is one canonical spelling, still byte-for-byte reversible in
    # meaning (canonical equivalence only, never compatibility folding).
    return unicodedata.normalize("NFC", text)


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
        if not key or not qid:
            # No wording or no identity — nothing to compare. The
            # ``len(key) < 25`` threshold that stood here was a character
            # count deciding whether two questions are the same question
            # (T10-5); a short repeat is now REPORTED as one grouped
            # warning a reviewer dismisses in one glance — reporting a
            # collision is identity accounting, suppressing it was the
            # judgment.
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
    catalog_defects: list[dict[str, Any]] = []
    try:
        _types, cases = p3_assemble._type_catalog(
            {"mined_types": dict(mined_types or {})
             if isinstance(mined_types, Mapping) else {}}
        )
    except Exception as exc:  # noqa: BLE001 — named, never swallowed (T9-3)
        # A catalog that will not parse used to silently disable this
        # audit — ``except Exception: cases = {}`` with a comment calling
        # that safety. It is a NAMED structural finding now: the audit
        # still runs over what it can read, and the reviewer is told the
        # gate ran half-blind instead of being led to believe it ran.
        cases = {}
        catalog_defects.append(_issue(
            code="type_catalog_unreadable",
            message=(
                "the staged mined-Type catalog could not be parsed "
                f"({type(exc).__name__}: {exc}); the case-uniqueness audit "
                "ran without its expected-example evidence"
            ),
            severity="error",
            phase="type_case_release",
        ))
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
    return catalog_defects + [
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


# Round 10 (audit finding 12): the two cached-topology sides recorded
# incomparable (or unreadable) allotment identity sets, so neither can be
# preferred over the other and the CURRENT rows ship. Advisory only — a
# review question, never a gate.
TOPOLOGY_ALLOTMENT_AMBIGUITY = "failure_release_topology_ambiguous"


def _analysis_allotment_ids(
    rows: Iterable[Mapping[str, Any]],
) -> frozenset[str] | None:
    """The exact set of recorded Q1 allotment identities on these rows.

    T10-6 (S11) replaced the "misconception" keyword count with the
    recorded ``_aegis_analysis_allotments`` marker; Round 10 (audit
    finding 12) replaces COUNTING ROWS that carry the marker with the SET
    of identities the markers record. [measured] the row count re-derived
    meaning from partitioning: a stale split cache (two rows carrying one
    allotment id each) out-counted the current merged topology (one row
    carrying both ids) while recording exactly the same allotments — a
    volume-derived preference of the kind CLAUDE.md Rule 1 bans. The id
    SET is pure identity accounting: it cannot be moved by how the same
    recorded verdicts are distributed across rows.

    Returns ``None`` when a recorded marker cannot be read as identities
    (a truthy non-list marker, or a non-string entry) — an unreadable side
    is never preferred, and the caller records the ambiguity.
    """

    ids: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        marker = row.get("_aegis_analysis_allotments")
        if not marker:
            continue
        if not isinstance(marker, (list, tuple)):
            return None
        for item in marker:
            text = item.strip() if isinstance(item, str) else ""
            if not text:
                return None
            ids.add(text)
    return frozenset(ids)


def _validated_artifact_topology(
    job: models.UploadJob,
    current_rows: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]] | None, list[dict[str, Any]]]:
    """Return ``(validated rows when they beat the checkpoint, advisories)``.

    Job 23 failed after caching a fully validated topology (every normal
    concept carrying complete learner analysis) and then released the older
    81% checkpoint rows without any learner analysis. A failure release must
    ship the most complete rows the run actually produced and verified.

    Round 10 (audit finding 12): "more complete" is decided by the exact
    SETS of recorded allotment identities, never by counting rows. A side
    is preferred only when its id set is a STRICT SUPERSET of the other's
    — it records everything the other side records, plus more. Equal sets
    keep the current rows (nothing is gained by swapping); incomparable
    sets — each side records identities the other lacks — or unreadable
    markers keep the current rows AND append a named advisory issue
    (``failure_release_topology_ambiguous``), because neither side's
    record contains the other and preferring one would silently drop the
    other's recorded verdicts. The advisory rides the release's issue
    ledger; it flags, it never gates.
    """
    advisories: list[dict[str, Any]] = []
    helper = getattr(uploads, "source_artifact_directory", None)
    if not callable(helper) or not getattr(job, "id", None):
        return None, advisories
    try:
        directory = Path(helper(int(job.id))).resolve()
    except Exception:
        return None, advisories
    from . import canonical_source_phase3 as phase3

    best: list[dict[str, Any]] | None = None
    best_ids: frozenset[str] = frozenset()
    best_name = ""
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
        candidate_ids = _analysis_allotment_ids(candidate)
        if candidate_ids is None:
            advisories.append(_issue(
                code=TOPOLOGY_ALLOTMENT_AMBIGUITY,
                message=(
                    f"the cached topology {filename} carries an allotment "
                    "marker that cannot be read as recorded identities, so "
                    "it was never preferred over the checkpoint rows — "
                    "review which topology is the complete one"
                ),
                severity="warning",
                phase="release",
            ))
            continue
        if best is None or candidate_ids > best_ids:
            best, best_ids, best_name = candidate, candidate_ids, filename
        elif not (candidate_ids <= best_ids):
            # The two caches record incomparable identity sets: preferring
            # either would drop allotments the other records.
            advisories.append(_issue(
                code=TOPOLOGY_ALLOTMENT_AMBIGUITY,
                message=(
                    f"the cached topologies {best_name} and {filename} "
                    "record incomparable allotment identity sets (each "
                    "carries identities the other lacks); the checkpoint "
                    "rows were kept — review which topology is the "
                    "complete one"
                ),
                severity="warning",
                phase="release",
            ))
            return None, advisories
    if best is None:
        return None, advisories
    current_ids = _analysis_allotment_ids(current_rows)
    if current_ids is None:
        advisories.append(_issue(
            code=TOPOLOGY_ALLOTMENT_AMBIGUITY,
            message=(
                "the checkpoint rows carry an allotment marker that cannot "
                "be read as recorded identities, so no cached topology was "
                "preferred over them — review which topology is the "
                "complete one"
            ),
            severity="warning",
            phase="release",
        ))
        return None, advisories
    if best_ids > current_ids:
        return best, advisories
    if best_ids <= current_ids:
        # The checkpoint records everything the cache records (or more):
        # the current rows are at least as complete, no ambiguity.
        return None, advisories
    advisories.append(_issue(
        code=TOPOLOGY_ALLOTMENT_AMBIGUITY,
        message=(
            f"the cached topology {best_name} and the checkpoint rows "
            "record incomparable allotment identity sets (each carries "
            "identities the other lacks); the checkpoint rows were kept — "
            "review which topology is the complete one"
        ),
        severity="warning",
        phase="release",
    ))
    return None, advisories


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

    LANE-GENERIC: [measured] both lanes call this — the Post staging path
    at ``:1426`` and ``stage_pre_release`` at ``:2051`` — so it names no
    output number. The pre-OD4 text said "the staged Output-01 payload"
    and was already lane-specific for a helper that serves both; carrying
    that forward as "Output-03" would have preserved the defect under a
    new number. T14's remedy for the same shape is
    ``assessment_release_run.py:1459``: stop naming a lane.

    Topic and Concept meaning comes from the released records. The Chapter is
    only a directory anchor, but even that metadata must travel with the staged
    release payload: rereading a mutable Chapter after staging would let the
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
    snapshot_defects: Sequence[str] = (),
    live_example_adjudication: bool = True,
) -> dict[str, Any]:
    """Persist one release payload and clear every manual decision gate.

    ``refinements`` is The Refiner's recorded diff on this release
    (docs/aegis-restructure.md §8.3): its changes, summary, review flags,
    and re-seal marker. ``None`` (callers that never entered the Refiner
    seam) stores no key; the payload stays byte-compatible.

    ``snapshot_defects`` (S11, V2/T10-0): the Post lane's transport for
    input-artifact and audit BLOCKING findings — the parameter and the
    payload key ``stage_pre_release`` has carried since S9, gained here
    because [measured] the audit's blocking set had NOWHERE to land on the
    Post lane. The audit's blocking findings ride ``qc_blocking_defects``
    (their own key with their own honest reader in ``structural_defects``);
    ``snapshot_defects`` keeps its original unreadable-input-snapshot
    meaning on both lanes and is NOT the QC transport.
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
    topology_advisories: list[dict[str, Any]] = []
    if records is None:
        validated, topology_advisories = _validated_artifact_topology(
            job, record_rows
        )
        if validated is not None:
            record_rows = validated
            upgraded_from_cache = True
    # _newest_checkpoint_material returns {} (a Mapping) when the
    # checkpoint carries no inventory/types, so a bare isinstance test
    # made both job-level fallbacks unreachable: a checkpoint-resolved
    # staging ran the whole issues ledger — the ownership scan included —
    # against an EMPTY inventory and flagged every rendered Example as
    # unowned. An empty snapshot now falls through to the job's stored
    # inventory (minus the lane release slots, which must never nest
    # inside a new payload).
    inventory_value = copy.deepcopy(
        dict(inventory)
        if isinstance(inventory, Mapping)
        else checkpoint_inventory
        if isinstance(checkpoint_inventory, Mapping) and checkpoint_inventory
        else _job_inventory_fallback(job)
    )
    types_value = copy.deepcopy(
        dict(mined_types)
        if isinstance(mined_types, Mapping)
        else checkpoint_types
        if isinstance(checkpoint_types, Mapping) and checkpoint_types
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
    # S9: the row-level projection verdict, decided HERE because
    # ``structural_defects`` is a pure function of this payload and the
    # projection runs at download time (D8.5). Recorded twice on purpose —
    # as a reviewer-facing issue and as the machine-readable key
    # ``structural_defects`` reads — because a defect with no place in the
    # state vocabulary is invisible.
    row_defects = staged_row_defects(record_rows)
    for finding in row_defects:
        issues.append(_issue(
            code=STAGED_ROW_UNUSABLE,
            message=finding["message"],
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
    # Round 10 (audit finding 12): a topology choice that could not be
    # decided by identity-set containment is RECORDED, never guessed.
    issues.extend(topology_advisories)

    # T10-0 (S11): the QC audit runs at STAGING, for both lanes,
    # immediately before the payload dict is assembled — never at download
    # time, never inside an artifact builder, never at the publication
    # act. Its issues join the existing ledger; its blocking findings ride
    # ``snapshot_defects``, which ``structural_defects`` reads.
    from . import release_qc

    qc_issues, qc_blocking = release_qc.audit({
        "records": record_rows,
        "issues": issues,
        "question_task_inventory": inventory_value,
        "mined_types": types_value,
        "type_case_rows": type_case_rows,
        "learning_kind": job.learning_kind,
    })
    issues.extend(qc_issues)
    # Q13/R4: public Examples whose wording has no exact owner in the
    # source inventory are adjudicated by one recorded decision and ride
    # this same ledger. Decided HERE, beside the QC audit, so every exit
    # that stages rows — the generation wrapper's four exits and the
    # interactive force_release route — carries the record; the helper
    # never raises, and a failed or unavailable judge still leaves the
    # deterministic finding behind. Interactive callers pass
    # ``live_example_adjudication=False``: they answer a plain HTTP
    # request and must not block on provider latency, so they record the
    # finding without spending.
    from . import concept_example_ownership

    # The judge's chapter context comes from the checkpoint THIS staging
    # resolved: on the clean exit generate_post_learning has already
    # cleared job.generation_checkpoint (so the live property is empty),
    # while the wrapper hands the captured checkpoint in — and the module
    # normalizes both to the same projection so the decide-once key is
    # stable across every exit.
    ownership_meta = checkpoint_value.get("target_identity")
    if not isinstance(ownership_meta, Mapping):
        ownership_meta = job.checkpoint_target_identity or {}
    ownership_issue = concept_example_ownership.adjudication_issue(
        record_rows,
        inventory_value,
        meta=dict(ownership_meta),
        job_id=int(job.id),
        allow_live=live_example_adjudication,
    )
    if ownership_issue is not None:
        issues.append(ownership_issue)
    staged_snapshot_defects = [
        _normal(defect) for defect in snapshot_defects if _normal(defect)
    ]
    qc_blocking_defects = [
        _normal(defect) for defect in qc_blocking if _normal(defect)
    ]

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
        # The SCHEMA version above; the DRAFT version here. Minted, not
        # overwritten (spec-step8 T2/S6) — read the constant's note.
        STAGED_VERSION_FIELD: next_staged_version(job, lane=LANE_POST),
        STAGED_RELEASE_UID_FIELD: uuid4().hex,
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
        # S9 — the row-level defect record. It is a NEW key on the Post
        # payload's recorded shape (``tests/test_pre_release_lane_wiring``'s
        # ``_POST_PAYLOAD_KEYS`` gains it here), added deliberately: the
        # state split is only half a fix without somewhere for a
        # staging-time row defect to live, and S8 closed exactly this
        # producer-with-no-consumer hole for ``unplaced``.
        STAGED_ROW_DEFECTS_FIELD: _json_safe(row_defects),
        # S11 (V2) — the Post lane's input-artifact defect transport, in
        # the exact shape ``stage_pre_release`` writes: normalised
        # non-empty strings, defaulting to []. Dormant until a Post caller
        # records one; kept for lane parity and for the honest reader
        # (its entries really are unreadable-input findings).
        "snapshot_defects": _json_safe(staged_snapshot_defects),
        # Round 9 — the QC audit's blocking findings, on their own key
        # with their own honest reader (see ``QC_BLOCKING_FIELD``).
        QC_BLOCKING_FIELD: _json_safe(qc_blocking_defects),
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
    and it keeps Outputs 01/02 out of the generation call chain entirely.

    THREE states, kept distinguishable (R4) — this used to be two:

    * ``(None, "")`` — the file is absent. This run authored no such
      artefact, which for the map means "no Pre lane at all";
    * ``(snapshot, "")`` — read;
    * ``(None, reason)`` — the file is THERE and cannot be read.

    Collapsing the third into the first is the failure this signature
    exists to prevent. An unreadable questions snapshot would otherwise
    be indistinguishable from "the lane authored no question", and
    Output 02 would ship empty, flagless and ``ready`` with a chapter's
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
    """The Refiner's designed hook, for Output 01 (§8.3).

    ``release_refiner``'s docstring reserved this seam for "the Phase 03
    prerequisite (Pre) outputs — … the replacement outputs 03/04 join this
    seam when step 7 builds them", with an ``output_kind`` parameter so
    they join without redesign. (That quotation is left verbatim; under
    the owner's numbering — OD4 / register entry D9-Q22 — the same pair
    of Pre-Learning files is Outputs 01/02. Renumbering the words inside
    the quotation marks would make it a quotation of something nobody
    wrote.) This is that join: the Pre rows carry the
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
    """Stage Outputs 01/02 from this run's recorded Phase 03 snapshots.

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
            "unaffected and ships. Outputs 01/02 are not available for this "
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
    row_defects: Sequence[Mapping[str, Any]] = (),
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
    for finding in row_defects:
        # S9: the same row-level verdict the Post lane records, on the same
        # code, so a reviewer reading either lane's issues reads one
        # vocabulary. It reaches ``structural_defects`` through the
        # payload's ``staged_row_defects`` key, not through this issue.
        issues.append(_issue(
            code=STAGED_ROW_UNUSABLE,
            message=_normal(finding.get("message")),
            phase="release",
        ))
    verdict_row = pre_map.get(PRE_LANE_VERDICT_FIELD)
    verdict = (
        _normal(verdict_row.get("verdict"))
        if isinstance(verdict_row, Mapping) else ""
    )
    if verdict and verdict != PRE_LANE_ASSUMES_NOTHING:
        # ONLY the non-positive verdict becomes an issue. A recorded
        # ``assumes_nothing`` is PROVENANCE, not doubt: an issue for it
        # would count toward ``issue_count`` and name a correct empty
        # release *Ready with flags*, and flags mean "a human should look
        # at this", which is the opposite of what the run just decided.
        # The positive verdict is still visible to a reviewer holding only
        # the workbook — ``_provenance_manifest_rows`` writes it — and it
        # rides ``payload["pre_lane_verdict"]`` for every reader.
        issues.append(_issue(
            code="pre_learning_empty_capture_verdict",
            message=(
                "The prerequisite capture was empty and the run decided "
                f"{verdict!r} rather than "
                f"{PRE_LANE_ASSUMES_NOTHING!r}: "
                + (
                    _normal(verdict_row.get("rationale"))
                    or "no rationale was recorded"
                )
            ),
            phase="phase03",
        ))
    # Q10, enforced at the release boundary rather than only at the
    # decision. The empty-capture critic ADVISES and never gates — but its
    # dissent is the codebase's own second pass on the ONE decision that
    # decides whether an empty Pre release is *Ready* or *Diagnostic*, and
    # [measured] it reached the release nowhere: not an issue, not a flag,
    # not a manifest row, not even as a substring of the payload. A critic
    # that said "the source shows two missing pages" on that decision was
    # readable only inside premap's own return value. An advisory flag a
    # reviewer cannot read is not recorded, it is discarded.
    #
    # It becomes an ISSUE, not a defect: it is doubt about what the source
    # means, so it flags. The release moves to *Ready with flags*, every
    # download stays open, and the publication stays open too — which is
    # exactly what "the critic never gates" means.
    for flag in (
        verdict_row.get("review_flags") or []
        if isinstance(verdict_row, Mapping) else []
    ):
        if _normal(flag):
            issues.append(_issue(
                code="pre_learning_empty_capture_review_flag",
                message=(
                    "The empty-capture verdict carries a review flag from "
                    "its own second pass, which advises and never gates: "
                    + _normal(flag)
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
    """Identity accounting at Output 01's release boundary.

    The owner steer of 17 Aug 2026 makes this mechanical and names both
    halves of the Pre lane: "no QID from the chapter's question/task
    inventory may appear anywhere in a Pre row **or the Pre release
    payload**. That is identity accounting, not judgment". Output 02
    already does exactly this at its own boundary
    (``assessment_release_run``); Output 01 is a Pre release too, and
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
    exactly which identity was found. Losing Outputs 01/02 without a word
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
    # Output 01's audit channels. The shared set covers ``review_flags``
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
    # The two reviewer-visible strings below are RENUMBERED here, under the
    # owner's ruling OD4 / register entry D9-Q22: this is the PRE lane, and
    # the Pre-Learning Concept File is Output 01, not Output 03.
    #
    # T14's string table listed both against S9 because S9 is the slice it
    # expected to open this file first. T14's own rule is "renumbered in the
    # slice that already opens its file", and S3 opens it — so they are
    # corrected now rather than left inverted for six more slices. They are
    # live reviewer text, not comments: the second is the ``message`` of an
    # issue that ships in the payload. [measured] no test asserts either
    # string — ``tests/test_pre_release_lane_wiring.py:1551`` asserts the
    # ``code``, which does not move.
    #
    # S9 still owns the surrounding REGION (the ``:1918-1975`` rewrite and
    # the release-state split). Only the numbers move here.
    try:
        premap._refuse_source_qids(
            release_run._authored_surface(payload, audit_keys=audit_keys),
            list(source_qids),
            where="the staged Pre-Learning release payload (Output 01)",
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
                "Output 01 was refused at its release boundary and ships "
                "no rows: " + str(exc)
            ),
            phase="phase03",
        ))
        payload["issues"] = _json_safe(issues)
        payload["summary"] = _release_summary([], issues)
    return payload


# --------------------------------------------------------------------------- #
# T3.3 — the Pre lane's ``related_concepts`` content, resolved at staging
# --------------------------------------------------------------------------- #

def _post_machine_ids_by_concept_id(db, job) -> dict[str, tuple[str, str]]:
    """``CONCEPT-%04d`` → (persisted Post ``machine_id``, concept title).

    ``phase3.place.mint_concept_ids`` mints those ids POSITIONALLY over the
    settled+created row list, and the Post release records are that list in
    that order. So the join is positional bookkeeping, not a title match
    (T11.2 forbids the latter) — and the recorded ``post_concept_title`` is
    used only to CONFIRM the position, never to find it.

    Never raises: a job with no staged Post release resolves nothing, every
    link becomes a recorded review flag, and the Pre lane still ships.
    """

    from . import build_concepts_release_files as release_files

    try:
        post = release_payload(job, lane=LANE_POST)
        if not post:
            return {}
        _chapter, concepts, records, _defects = (
            release_files.transient_release_hierarchy(db, job, payload=post))
    except Exception:  # noqa: BLE001 - the Pre lane never blocks on this
        return {}
    resolved: dict[str, tuple[str, str]] = {}
    for position, (concept, record) in enumerate(
        zip(concepts, records), start=1
    ):
        machine_id = str(getattr(concept, "machine_id", "") or "")
        if not machine_id:
            continue
        title = str(
            record.get("concept_title") or record.get("concept") or "")
        resolved[f"CONCEPT-{position:04d}"] = (machine_id, title)
    return resolved


def _resolve_needed_for(
    row: Mapping[str, Any], post_ids: Mapping[str, tuple[str, str]],
) -> tuple[list[str], list[dict[str, Any]]]:
    """Return (resolved machine ids, unresolved links) for one Pre row."""

    resolved: list[str] = []
    unresolved: list[dict[str, Any]] = []
    for link in row.get("_aegis_needed_for") or []:
        if not isinstance(link, Mapping):
            continue
        concept_id = _normal(link.get("post_concept_id"))
        recorded_title = _normal(link.get("post_concept_title"))
        entry = post_ids.get(concept_id)
        if entry is None:
            unresolved.append({
                "post_concept_id": concept_id,
                "post_concept_title": recorded_title,
                "reason": "no Post concept was staged under this id",
            })
            continue
        machine_id, title = entry
        if recorded_title and _normal(title) != recorded_title:
            # The position and the recorded identity disagree. Never
            # guessed silently: the link becomes a review flag on the row.
            unresolved.append({
                "post_concept_id": concept_id,
                "post_concept_title": recorded_title,
                "reason": (
                    f"the Post concept staged at this position is "
                    f"{title!r}"),
            })
            continue
        if machine_id not in resolved:
            resolved.append(machine_id)
    return resolved, unresolved


def _lift_resolved_related_concepts(row: Mapping[str, Any]) -> dict[str, Any]:
    """Lift the resolved marker onto an explicit ``related_concepts`` key.

    Called at publication, BEFORE ``_strip_release_fields`` (T3.3b): the
    marker is a registered audit field, so the strip drops it by
    construction and the column would empty at exactly the moment the
    reviewer publishes.
    """

    lifted = dict(row)
    resolved = str(lifted.get(PRE_ROW_RELATED_CONCEPTS_FIELD) or "")
    if resolved:
        lifted["related_concepts"] = resolved
    return lifted


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
    """Stage Outputs 01/02 into the sibling slot on THIS job.

    Outputs 01 and 02 are projections of one accepted snapshot exactly as
    01 and 02 are (§4's first release invariant): the Pre concept rows and
    the generated questions authored against them are staged together,
    once, and every later projection reads this payload rather than
    re-deriving Pre meaning.

    Returns ``None`` — staging nothing — when the run built no Pre lane at
    all. That is not the same as an empty Pre map: a chapter that
    genuinely assumes no prior knowledge stages a real, empty Output 01
    (``pre_map`` present with zero rows), while a run that never reached
    Phase 03 stages no sibling at all. Collapsing the two would make
    "this run has no Pre map" indistinguishable from "this chapter needs
    nothing" — exactly the R4 confusion the capture snapshot's own
    absent-marker exists to prevent.

    That empty release DOWNLOADS, and since spec-step8 S9 it also
    PUBLISHES — as a zero-row idempotent success — but only when the run
    positively DECIDED the emptiness. ``structural_defects`` no longer
    reports "the release contains no concept rows to upload" (that moved
    to ``nothing_to_publish``, D8.1: emptiness is not corruption, and §4
    defines *Diagnostic release* as "structural/import integrity failed",
    which an empty-but-correct chapter is not). What decides it now is
    premap's recorded ``pre_lane_verdict``: ``assumes_nothing`` reads
    *Ready*, anything else — including no verdict at all — keeps the
    release *Diagnostic*. That replaced the fourth named state §4 would
    otherwise have needed, so §4's three names are unamended.

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
    # T3.3: resolve every ``_aegis_needed_for[].post_concept_id`` against
    # the POST payload staged in the SAME run. ``_stage_pre_sibling`` runs
    # after the Post release is staged and both slots hang off this one
    # job, so the identity exists here and exists nowhere earlier.
    post_ids = _post_machine_ids_by_concept_id(db, job)
    for row in raw_rows:
        pre_concept_id = _normal(row.get("_pre_concept_id"))
        authored = questions_by_concept.get(pre_concept_id) or []
        row[RELEASE_ROW_LANE_FIELD] = LANE_PRE
        row[PRE_ROW_GENERATED_QUESTIONS_FIELD] = [
            _normal(entry.get("pre_question_id")) for entry in authored
        ]
        resolved, unresolved = _resolve_needed_for(row, post_ids)
        row[PRE_ROW_RELATED_CONCEPTS_FIELD] = "\n".join(resolved)
        row[PRE_ROW_RELATED_UNRESOLVED_FIELD] = unresolved
        generated.extend(copy.deepcopy(entry) for entry in authored)

    row_defects = staged_row_defects(raw_rows)
    # T10-0 (S11): the QC audit's Pre-lane call, immediately before the
    # payload dict is assembled. Its blocking findings join the
    # input-artifact defects on the transport this lane already carries.
    from . import release_qc

    qc_issues, qc_blocking = release_qc.audit({
        "records": raw_rows,
        "issues": [],
        "question_task_inventory": dict(inventory or {}),
        RELEASE_LANE_FIELD: LANE_PRE,
    })
    # Round 9: QC blocking findings ride their OWN key. Folding them into
    # ``snapshot_defects`` [measured] minted one spurious
    # ``pre_learning_snapshot_unreadable`` issue per finding — a fake
    # snapshot-corruption record for a coverage verdict.
    qc_blocking_defects = [
        _normal(defect) for defect in qc_blocking if _normal(defect)
    ]
    issues = _pre_release_issues(
        source, questions_source, raw_rows, snapshot_defects,
        row_defects=row_defects,
    )
    issues.extend(qc_issues)
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
        # Minted per LANE, so a Pre re-stage never reads as a Post one
        # (spec-step8 T2/S6).
        STAGED_VERSION_FIELD: next_staged_version(job, lane=LANE_PRE),
        STAGED_RELEASE_UID_FIELD: uuid4().hex,
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
        # S9 — the row-level defect record, the same key and the same shape
        # the Post lane carries.
        STAGED_ROW_DEFECTS_FIELD: _json_safe(row_defects),
        # S9 / D8.3 — premap's recorded empty-capture verdict, carried
        # verbatim. ``structural_defects`` reads it; nothing re-decides it.
        PRE_LANE_VERDICT_FIELD: _json_safe(
            dict(source.get(PRE_LANE_VERDICT_FIELD))
            if isinstance(source.get(PRE_LANE_VERDICT_FIELD), Mapping)
            else {}
        ),
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
        # Output 02's leak barrier still needs that identity set, and this
        # is exactly why it does not read it from here: it reads it from
        # the job's own inventory column and the Post sibling slot (see
        # ``assessment_release_run._generated_lane_source_qids``), which
        # hold it without putting it in a Pre artefact.
        "question_task_inventory": {},
        "chapter_meta": _json_safe(chapter_meta),
        "snapshot_defects": _json_safe(
            [_normal(defect) for defect in snapshot_defects if _normal(defect)]
        ),
        # Round 9 — the QC audit's blocking findings, on their own key
        # (see ``QC_BLOCKING_FIELD``'s note).
        QC_BLOCKING_FIELD: _json_safe(qc_blocking_defects),
        "refused": _normal(source.get("refused")),
        "pre_topics": _json_safe([
            topic for topic in source.get("topics") or []
            if isinstance(topic, Mapping)
        ]),
        "needed_for": _json_safe(source.get("needed_for") or {}),
        "analysis": _json_safe(source.get("analysis") or {}),
        # Output 02's material, staged beside Output 01 so the pair is
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
    summary = copy.deepcopy(payload.get("summary") or {})
    # ONE builder for both lanes.
    #
    # These were two branches, and the Post branch omitted five of the
    # Pre branch's keys — ``lane``, ``release_state``,
    # ``structural_defects``, ``generated_question_count`` and
    # ``release_bulk_import_url``. The Post dict IS the terminal
    # ``{"type": "result"}`` event of a Build Concepts run, so the Post
    # lane's release state was computed by ``release_state`` and
    # surfaced by nothing: a reviewer could be shown a finished run whose
    # publication the structural gate would refuse, with no way to see
    # that from the result. A lane-shaped answer to "what is this
    # release?" is the defect; one shape, filled per lane, is the fix.
    #
    # The lane rides the query string, and for the four DOWNLOADS the Post
    # lane's suffix is empty — those four URLs are byte-identical to the
    # ones already published, so no reviewer's bookmark and no recorded
    # URL moves.
    #
    # The PUBLISH url is the one exception, and deliberately so: the
    # publish endpoint (``api/build_concepts.upload_released_output_to_
    # database``) refuses a blank or absent lane, because a defaulted
    # lane there is an authenticated write against a lane nobody named.
    # A server that refuses a lane-less publication must not itself hand
    # the reviewer a lane-less publish URL, so this one always states its
    # lane — including "post", which the download URLs still leave
    # implicit.
    query = f"?lane={LANE_PRE}" if resolved == LANE_PRE else ""
    publish_query = f"?lane={resolved}"
    return {
        "job_id": job.id,
        "lane": resolved,
        "status": RELEASE_STATUS,
        "released": True,
        "release_state": release_state(payload),
        "structural_defects": structural_defects(payload),
        "database_uploaded": bool(summary.get("database_uploaded")),
        "row_count": int(summary.get("row_count") or 0),
        "affected_row_count": int(summary.get("affected_row_count") or 0),
        "issue_count": int(summary.get("issue_count") or 0),
        # ALWAYS 0 on the Post lane, and that is the shape, not a count.
        # ``generated_questions`` is a key only the Pre payload carries —
        # the Post lane's questions belong to the assessment release, not
        # to the concept release — so on Post this reads "the Post concept
        # release does not carry generated questions", NOT "this run
        # generated none". The identical key set is what makes one reader
        # able to read both lanes; the alternative was omitting the key on
        # Post, which is precisely the asymmetry this builder replaces.
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
            f"/build-concepts/uploads/{job.id}/upload-release{publish_query}"
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
    # MINTS a new staged version rather than overwriting the slot
    # (spec-step8 T2/S6). This is the route that made the hazard concrete:
    # it refuses only when ``job.status == "generated"``, so a reviewer
    # could re-stage after the Master File had already been frozen against
    # the previous draft, and nothing on either side recorded that the
    # source had moved. ``stage_release`` mints the version; the frozen
    # row carries it.
    stage_release(
        db,
        job,
        reason="The user explicitly released the newest durable checkpoint.",
        # This is a plain interactive HTTP route: record any unowned
        # rendered Examples without blocking on the live judge.
        live_example_adjudication=False,
    )
    return job


def _strip_release_fields(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: copy.deepcopy(value)
        for key, value in record.items()
        if key not in _RELEASE_AUDIT_FIELDS
    }

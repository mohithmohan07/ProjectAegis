"""Terminal-generation authority for Build Concepts staged releases.

A diagnostic checkpoint is useful review evidence, but it is not a completed
Concept run. Before this contract, ``stage_release`` used the same ``released``
job status for both shapes and publication blocked only on incidental structural
defects. A run such as job 78 could therefore keep a durable 55% checkpoint
while the UI hid Resume, and publication safety depended on some unrelated
Type/QID defect happening to be present.

Restructure A (owner approval, 2026-08-29): "is this run finished" is decided
ONCE, at staging, and recorded on the staged payload as the explicit
``terminal_generation_complete`` field (mirrored into its summary). Every
later consumer — Master eligibility, publication — reads that recorded fact.
Payloads staged before the field existed are backfilled once from durable
evidence (``ensure_explicit_terminal_verdict``); the checkpoint/issue
derivation in ``payload_terminal_generation_complete`` remains only as that
legacy-read fallback. The contract provides four guarantees:

* a recorded non-terminal checkpoint with a generation failure remains
  downloadable and returns to the converted/unpublished lifecycle so Resume is
  visible;
* a failure with no resumable checkpoint remains a terminal diagnostic release
  (downloadable, but not falsely offered as resumable);
* ``structural_defects`` always refuses a non-terminal payload independently of
  row shape, inventory size, or semantic issue anchoring; and
* legacy/manual releases with no recorded checkpoint remain compatible when
  they carry no generation failure.

The Pre sibling has no independent run lifecycle. When its Post sibling is
non-terminal, the Pre payload records that fact in its existing
``snapshot_defects`` channel, preserving the payload's established key shape.
"""
from __future__ import annotations

import copy
from functools import wraps
from typing import Any, Mapping

from .. import models
from . import build_concepts_release as release
from . import build_concepts_release_files as release_files
from . import build_concepts_release_manifest as release_manifest
from . import build_concepts_release_publication as publication
from . import generation


CONTRACT_VERSION = 2
# Restructure A (2026-08-29): the run's terminal verdict, written once at
# staging by ``record_terminal_verdict`` onto the payload and its summary.
# Consumers read this recorded fact; the checkpoint/issues derivation below
# survives only to read (and backfill) payloads staged before the field.
TERMINAL_GENERATION_FIELD = "terminal_generation_complete"
TERMINAL_GENERATION_DEFECT = "terminal_generation_incomplete"
PARTIAL_RELEASE_STATUS = "converted"


def _job_from_call(args, kwargs) -> models.UploadJob | None:
    if len(args) > 1 and isinstance(args[1], models.UploadJob):
        return args[1]
    value = kwargs.get("job")
    return value if isinstance(value, models.UploadJob) else None


def _db_from_call(args, kwargs):
    return args[0] if args else kwargs.get("db")


def _checkpoint_stage(checkpoint: object) -> str:
    if not isinstance(checkpoint, Mapping):
        return ""
    try:
        newest = generation._newest_compatible_concept_checkpoint(
            dict(checkpoint)
        )
    except Exception:
        newest = None
    if isinstance(newest, Mapping):
        return str(newest.get("stage") or "").strip()
    return str(checkpoint.get("stage") or "").strip()


def _payload_has_generation_error(payload: Mapping[str, Any]) -> bool:
    for issue in payload.get("issues") or []:
        if not isinstance(issue, Mapping):
            continue
        if (
            str(issue.get("phase") or "").strip() == "generation"
            and str(issue.get("severity") or "").strip().lower() == "error"
        ):
            return True
    return False


def _explicit_terminal_value(payload: Mapping[str, Any]) -> bool | None:
    """Read the recorded verdict from the payload or its summary mirror."""
    if TERMINAL_GENERATION_FIELD in payload:
        return payload.get(TERMINAL_GENERATION_FIELD) is True
    summary = payload.get("summary")
    if (
        isinstance(summary, Mapping)
        and TERMINAL_GENERATION_FIELD in summary
    ):
        return summary.get(TERMINAL_GENERATION_FIELD) is True
    return None


def _has_terminal_snapshot_defect(payload: Mapping[str, Any]) -> bool:
    return any(
        TERMINAL_GENERATION_DEFECT in str(defect or "")
        for defect in payload.get("snapshot_defects") or []
    )


def payload_terminal_generation_complete(
    payload: Mapping[str, Any] | None,
) -> bool:
    """Derive the publication authority from durable release evidence."""
    if not isinstance(payload, Mapping):
        return False
    explicit = _explicit_terminal_value(payload)
    if explicit is not None:
        return explicit
    if _has_terminal_snapshot_defect(payload):
        return False
    if _payload_has_generation_error(payload):
        return False
    stage = str(payload.get("checkpoint_stage") or "").strip()
    # Historical successful/manual releases can predate checkpoint recording.
    # Absence plus no generation error is compatible. A *recorded* partial
    # checkpoint is never compatible.
    return stage in {"", "final_content_ready"}


def _checkpoint_from_call(
    job: models.UploadJob,
    kwargs: Mapping[str, Any],
) -> Mapping[str, Any]:
    checkpoint = kwargs.get("checkpoint")
    if isinstance(checkpoint, Mapping):
        return checkpoint
    stored = job.generation_checkpoint
    return stored if isinstance(stored, Mapping) else {}


def _post_terminal_from_call(
    job: models.UploadJob,
    kwargs: Mapping[str, Any],
) -> bool:
    if kwargs.get("error") is not None:
        return False
    # A pending semantic decision is a review flag under the established
    # release-first contract; it is not, by itself, proof of a resumable run.
    # Only a recorded partial checkpoint can establish that lifecycle state.
    stage = _checkpoint_stage(_checkpoint_from_call(job, kwargs))
    return stage in {"", "final_content_ready"}


def _has_resumable_checkpoint(
    job: models.UploadJob,
    kwargs: Mapping[str, Any],
) -> bool:
    stage = _checkpoint_stage(_checkpoint_from_call(job, kwargs))
    return bool(stage and stage != "final_content_ready")


def _restore_resumable_status(db, job: models.UploadJob) -> None:
    job.status = PARTIAL_RELEASE_STATUS
    job.result_ids = []
    job.detail = (
        "Generation stopped before terminal completion. Aegis staged the "
        "newest durable rows as a diagnostic release; the saved checkpoint "
        "is still resumable and nothing has been uploaded to the database."
    )
    db.commit()
    db.refresh(job)


def record_terminal_verdict(
    db,
    job: models.UploadJob,
    *,
    lane: object,
    complete: bool,
) -> None:
    """Stamp the run's terminal verdict ONTO the staged payload, once.

    Restructure A (owner approval, 2026-08-29): "is this run finished" is
    a fact the run itself decides at staging — recorded here as the
    explicit ``terminal_generation_complete`` field — and every later
    consumer (Master eligibility, publication) READS that recorded fact
    instead of re-deriving it from checkpoint echoes. [measured] job
    'Patterns': a fully completed run whose payload echoed a mid-run
    stage flunked the derived check on every Master click while both
    Concept files sat staged and healthy.
    """
    key = release.release_key_for_lane(lane)
    durable = copy.deepcopy(dict(job.question_inventory or {}))
    payload = durable.get(key)
    if not isinstance(payload, Mapping):
        return
    marked = copy.deepcopy(dict(payload))
    marked[TERMINAL_GENERATION_FIELD] = bool(complete)
    summary = dict(marked.get("summary") or {})
    summary[TERMINAL_GENERATION_FIELD] = bool(complete)
    marked["summary"] = summary
    durable[key] = marked
    job.question_inventory = durable
    db.commit()
    db.refresh(job)


def ensure_explicit_terminal_verdict(
    db,
    job: models.UploadJob,
    *,
    lane: object,
) -> bool | None:
    """Return the lane payload's terminal verdict, backfilling a legacy one.

    A payload staged before restructure A carries no explicit verdict.
    This migrates the fact ONCE from durable evidence — the payload's own
    recorded error/defect state plus the checkpoint stage, accepting the
    LIVE checkpoint's ``final_content_ready`` where the payload's echoed
    stage lags behind it (the staging-order race job 'Patterns' hit: the
    final checkpoint write landed after the release was staged, so the
    echo said mid-run forever). Mechanics over recorded state; nothing
    here judges content. Returns ``None`` when the lane has no payload.
    """
    payload = release.release_payload(job, lane=lane)
    if payload is None:
        return None
    explicit = _explicit_terminal_value(payload)
    if explicit is not None:
        return explicit
    live = (
        job.generation_checkpoint
        if isinstance(job.generation_checkpoint, Mapping)
        else {}
    )
    recorded_stage = str(payload.get("checkpoint_stage") or "").strip()
    live_stage = _checkpoint_stage(live)
    complete = (
        not _has_terminal_snapshot_defect(payload)
        and not _payload_has_generation_error(payload)
        and (
            recorded_stage in {"", "final_content_ready"}
            or live_stage == "final_content_ready"
        )
    )
    record_terminal_verdict(db, job, lane=lane, complete=complete)
    return complete


def _record_pre_run_authority(
    db,
    job: models.UploadJob,
    *,
    complete: bool,
) -> None:
    """Carry Post run authority into Pre's existing snapshot-defect channel."""
    key = release.release_key_for_lane(release.LANE_PRE)
    durable = copy.deepcopy(dict(job.question_inventory or {}))
    payload = durable.get(key)
    if not isinstance(payload, Mapping):
        return
    marked = copy.deepcopy(dict(payload))
    defects = [
        str(defect)
        for defect in marked.get("snapshot_defects") or []
        if TERMINAL_GENERATION_DEFECT not in str(defect or "")
    ]
    if not complete:
        defects.append(
            f"{TERMINAL_GENERATION_DEFECT}: the sibling Post Concept run did "
            "not reach terminal generation completion; this Pre release may "
            "be downloaded for diagnosis but cannot be published"
        )
    marked["snapshot_defects"] = defects
    durable[key] = marked
    job.question_inventory = durable
    db.commit()
    db.refresh(job)


def install() -> None:
    if getattr(release, "_TERMINAL_RELEASE_CONTRACT_VERSION", 0) >= (
        CONTRACT_VERSION
    ):
        # Rebind public consumers even on an idempotent call: tests and
        # compatibility installers may have reassigned an imported alias.
        publication.structural_defects = release.structural_defects
        release_files.structural_defects = release.structural_defects
        release_manifest.structural_defects = release.structural_defects
        return

    original_stage_release = release.stage_release
    original_stage_pre_release = release.stage_pre_release
    original_structural_defects = release.structural_defects

    @wraps(original_stage_release)
    def stage_release(*args, **kwargs):
        job = _job_from_call(args, kwargs)
        db = _db_from_call(args, kwargs)
        result = original_stage_release(*args, **kwargs)
        if job is None or db is None:
            return result
        complete = _post_terminal_from_call(job, kwargs)
        # Restructure A (owner approval, 2026-08-29): the verdict is
        # DECIDED HERE, once, and stamped onto the staged payload. Every
        # later consumer reads the recorded fact; a checkpoint that
        # drifts, re-validates differently, or is cleared afterwards can
        # never re-open the question.
        record_terminal_verdict(
            db, job, lane=release.LANE_POST, complete=complete,
        )
        # Only a real partial checkpoint earns Resume. Quota/provider death
        # before any checkpoint remains a released diagnostic rather than a
        # converted job that can only restart from zero.
        if not complete and _has_resumable_checkpoint(job, kwargs):
            _restore_resumable_status(db, job)
        return release.release_result(job)

    @wraps(original_stage_pre_release)
    def stage_pre_release(*args, **kwargs):
        job = _job_from_call(args, kwargs)
        db = _db_from_call(args, kwargs)
        result = original_stage_pre_release(*args, **kwargs)
        if job is None or db is None or result is None:
            return result
        post_payload = release.release_payload(job, lane=release.LANE_POST)
        complete = payload_terminal_generation_complete(post_payload)
        # Restructure A: Pre is a sibling projection of the same Concept
        # run, so it carries the Post run's verdict — recorded explicitly
        # here, at staging, exactly as the Post payload records its own.
        record_terminal_verdict(
            db, job, lane=release.LANE_PRE, complete=complete,
        )
        _record_pre_run_authority(db, job, complete=complete)
        # Preserve stage_pre_release's established lane-specific return shape.
        return result

    @wraps(original_structural_defects)
    def structural_defects(payload):
        defects = list(original_structural_defects(payload))
        if (
            isinstance(payload, Mapping)
            and not payload_terminal_generation_complete(payload)
        ):
            message = (
                f"{TERMINAL_GENERATION_DEFECT}: the staged Concept release "
                "comes from a non-terminal generation checkpoint or recorded "
                "generation failure; it may be downloaded for diagnosis (and "
                "resumed when a checkpoint exists), but it cannot be published "
                "to the database"
            )
            if message not in defects:
                defects.append(message)
        return defects

    release.stage_release = stage_release
    release.stage_pre_release = stage_pre_release
    release.structural_defects = structural_defects
    # These modules imported the gate by name. Rebind every public consumer so
    # the publication act, eager/lazy output manifests, and UI affordances all
    # report the same terminal authority.
    publication.structural_defects = structural_defects
    release_files.structural_defects = structural_defects
    release_manifest.structural_defects = structural_defects
    release._TERMINAL_RELEASE_CONTRACT_VERSION = CONTRACT_VERSION

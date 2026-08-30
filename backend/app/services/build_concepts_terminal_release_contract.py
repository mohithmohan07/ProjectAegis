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
from . import progress


CONTRACT_VERSION = 3
# Restructure A (2026-08-29): the run's terminal verdict, written once at
# staging by ``record_terminal_verdict`` onto the payload and its summary.
# Consumers read this recorded fact; the checkpoint/issues derivation below
# survives only to read (and backfill) payloads staged before the field.
TERMINAL_GENERATION_FIELD = "terminal_generation_complete"
TERMINAL_GENERATION_DEFECT = "terminal_generation_incomplete"
PARTIAL_RELEASE_STATUS = "converted"
RUN_RECOVERY_FIELD = "generation_recovery"


def _job_from_call(args, kwargs) -> models.UploadJob | None:
    if len(args) > 1 and isinstance(args[1], models.UploadJob):
        return args[1]
    value = kwargs.get("job")
    return value if isinstance(value, models.UploadJob) else None


def _db_from_call(args, kwargs):
    return args[0] if args else kwargs.get("db")


def _checkpoint_stage(checkpoint: object) -> str:
    """The newest stage this checkpoint durably RECORDED — a raw read.

    Deliberately not ``_newest_compatible_concept_checkpoint``: strict
    resume-compatibility (seals, live-graph certificate re-verification)
    answers "can this entry be resumed NOW", which legitimately changes
    over time — exactly what a stamped-once terminal verdict must not
    depend on. It also inverted severity: a checkpoint whose entries were
    ALL incompatible fell through to an absent top-level stage and read
    as terminal, while one whose terminal entry alone failed a strict
    check read as mid-run ([measured] 2026-08-30: a completed run froze
    complete=False onto both payloads and lost both Master outputs).
    What stage the run reached is a recorded fact; this reads it as one.
    """
    if not isinstance(checkpoint, Mapping):
        return ""
    try:
        entries = [
            entry
            for entry in generation._concept_checkpoint_entries(
                dict(checkpoint)
            )
            if isinstance(entry, Mapping)
            and str(entry.get("stage") or "").strip()
        ]
    except Exception:
        entries = []
    if entries:
        newest = max(
            enumerate(entries),
            key=lambda indexed: (
                generation._checkpoint_order(
                    str(indexed[1].get("stage") or "")
                ),
                indexed[0],
            ),
        )[1]
        return str(newest.get("stage") or "").strip()
    if (
        str(checkpoint.get("checkpoint_format") or "")
        and isinstance(checkpoint.get("checkpoints"), list)
        and checkpoint.get("checkpoints")
    ):
        # A stage-history envelope whose entries this build cannot read
        # (an unrecognized schema version, malformed entries) records a
        # run state we cannot call terminal — falling through to the
        # absent top-level stage would resurrect the severity inversion
        # through the schema gate.
        return "unreadable_checkpoint_envelope"
    return str(checkpoint.get("stage") or "").strip()


def _resumable_checkpoint_stage(checkpoint: object) -> str:
    """The stage RESUME would actually accept — strictly filtered.

    "Resumable" is a promise about the resume machinery, so here the strict
    compatibility filter is exactly right: labelling a job resumable on a
    checkpoint resume will reject produces the converted-job-that-restarts-
    from-zero shape the diagnostic-release lifecycle exists to prevent.
    """
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
    # Legacy single-entry checkpoints keep their top-level stage as the
    # resumable signal (resume itself falls back the same way); a v3
    # envelope has no top-level stage, so strictly-unusable entries
    # correctly read as not resumable.
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
    # The run's own first-hand fact outranks every checkpoint reading: a
    # staging that carries a captured terminal deposit IS a completed
    # generation — the deposit interceptor only fires when the run reached
    # its final deposit ([measured] 2026-08-30: deriving this from the
    # checkpoint snapshot instead froze complete=False onto a clean run).
    if release.TERMINAL_DEPOSIT_STAGING.get():
        return True
    # A pending semantic decision is a review flag under the established
    # release-first contract; it is not, by itself, proof of a resumable run.
    # Only a recorded partial checkpoint can establish that lifecycle state.
    stage = _checkpoint_stage(_checkpoint_from_call(job, kwargs))
    return stage in {"", "final_content_ready"}


def _has_resumable_checkpoint(
    job: models.UploadJob,
    kwargs: Mapping[str, Any],
) -> bool:
    stage = _resumable_checkpoint_stage(_checkpoint_from_call(job, kwargs))
    return bool(stage and stage != "final_content_ready")


def _failure_allows_resume(error: object) -> bool:
    """Read an exception's explicit recovery contract, defaulting compatibly."""

    return getattr(error, "resume_allowed", True) is not False


def _record_non_resumable_recovery(
    db,
    job: models.UploadJob,
    error: Exception,
) -> None:
    """Persist a Q24-style recovery route without deleting paid evidence.

    The marker is stored both as job-level lifecycle metadata (for checkpoint
    discovery and ``UploadJobOut`` after a reload) and on the Post diagnostic
    release (for later release readers).  It records mechanics only: the raise
    site already decided that this exact checkpoint cannot advance.
    """

    recovery_message = str(getattr(error, "recovery_message", "") or "") or (
        "This saved checkpoint cannot complete by resuming. Start a new "
        "upload and conversion before generation."
    )
    marker = {
        "error": f"{type(error).__name__}: {error}",
        "message": (
            "Generation did not complete and this checkpoint is not "
            "resumable. " + recovery_message
        ),
        "resume_allowed": False,
        "recovery_action": str(
            getattr(error, "recovery_action", "reconvert_new_upload") or ""
        ),
        "recovery": recovery_message,
    }
    durable = copy.deepcopy(dict(job.question_inventory or {}))
    durable[models.GENERATION_RECOVERY_INVENTORY_KEY] = copy.deepcopy(marker)
    post = durable.get(release.RELEASE_KEY)
    if isinstance(post, Mapping):
        marked_post = copy.deepcopy(dict(post))
        marked_post[RUN_RECOVERY_FIELD] = copy.deepcopy(marker)
        durable[release.RELEASE_KEY] = marked_post
    job.question_inventory = durable
    # ``original_stage_release`` just set ``released``. Keep that diagnostic
    # lifecycle: unlike an ordinary partial failure, this checkpoint must not
    # be flipped back to ``converted`` and rediscovered as resumable.
    job.status = release.RELEASE_STATUS
    job.result_ids = []
    job.detail = marker["message"]
    db.commit()
    db.refresh(job)


def _clear_non_resumable_recovery(db, job: models.UploadJob) -> None:
    """Remove a stale recovery marker after a later non-Q24 staging."""

    durable = copy.deepcopy(dict(job.question_inventory or {}))
    if models.GENERATION_RECOVERY_INVENTORY_KEY not in durable:
        return
    durable.pop(models.GENERATION_RECOVERY_INVENTORY_KEY, None)
    post = durable.get(release.RELEASE_KEY)
    if isinstance(post, Mapping):
        marked_post = copy.deepcopy(dict(post))
        marked_post.pop(RUN_RECOVERY_FIELD, None)
        durable[release.RELEASE_KEY] = marked_post
    job.question_inventory = durable
    db.commit()
    db.refresh(job)


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


# The exact staging sentences ONLY the clean-capture path writes
# (``_release_after_result`` and its ``_stage_pre_sibling`` call in the
# release contract). A payload carrying one of these was staged because
# generation finished and delivered its deposit; a False verdict on it can
# only be the strict-filter mis-derivation this file's fix retired.
_CLEAN_CAPTURE_REASONS = frozenset({
    "Generation completed. The output was staged and was not uploaded to "
    "the database.",
    "Generation completed. The Phase 03 Pre-Learning outputs were staged "
    "and were not uploaded to the database.",
})


def _repair_misrecorded_terminal_verdict(
    db,
    job: models.UploadJob,
    *,
    lane: object,
    payload: Mapping[str, Any],
) -> bool:
    """Correct a provably mis-derived False verdict on a clean-capture payload.

    Before 2026-08-30 the verdict stamped at staging was derived from the
    deposit-time checkpoint through the strict resume-compatibility filter;
    a terminal entry that flunked one strict check (while an earlier stage
    passed) froze ``complete=False`` onto a genuinely completed run — both
    Master outputs skipped, the explicit rebuild routes refusing, no path
    back. This repairs exactly that record and nothing else: the payload
    must carry the clean-capture staging sentence only the completed-run
    path writes, no recorded generation error, and (Post) captured records.
    Correcting a mis-recorded fact from the payload's own recorded evidence
    — never a re-litigation of a genuine failure verdict, which keeps its
    different staging sentence and its recorded error.
    """
    reason = str(payload.get("release_reason") or "").strip()
    if reason not in _CLEAN_CAPTURE_REASONS:
        return False
    if _payload_has_generation_error(payload):
        return False
    pre_key = release.release_key_for_lane(release.LANE_PRE)
    is_pre = release.release_key_for_lane(lane) == pre_key
    if is_pre:
        # Pre carries the Post run's verdict; only a repaired/true Post
        # authority can carry Pre with it. The bug itself wrote Pre's
        # terminal snapshot defect, so that line does not block the
        # repair — it is removed with the corrected record below.
        if ensure_explicit_terminal_verdict(
            db, job, lane=release.LANE_POST,
        ) is not True:
            return False
        record_terminal_verdict(
            db, job, lane=release.LANE_PRE, complete=True,
        )
        _record_pre_run_authority(db, job, complete=True)
    else:
        if _has_terminal_snapshot_defect(payload):
            return False
        if not payload.get("records"):
            return False
        record_terminal_verdict(
            db, job, lane=release.LANE_POST, complete=True,
        )
        if str(job.status or "") == PARTIAL_RELEASE_STATUS:
            # The same bug flipped the finished run back to a "resumable"
            # converted lifecycle. Restore the released state the clean
            # staging had already established, with an honest detail.
            job.status = release.RELEASE_STATUS
            job.result_ids = []
            job.detail = (
                "Repaired: this run completed and its release is staged "
                "for review. The earlier 'resumable' state came from a "
                "mis-recorded terminal verdict; nothing has been uploaded "
                "to the database."
            )
            db.commit()
            db.refresh(job)
    progress.log(
        "Repaired a mis-recorded terminal verdict: this lane's staged "
        "release carries the completed-run staging record, but the "
        "pre-2026-08-30 verdict derivation had frozen it as non-terminal. "
        "The recorded verdict now matches the recorded run.",
        level="warning",
    )
    return True


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
    if explicit is False and _repair_misrecorded_terminal_verdict(
        db, job, lane=lane, payload=payload,
    ):
        return True
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
        error = kwargs.get("error")
        if (
            isinstance(error, Exception)
            and not _failure_allows_resume(error)
        ):
            _record_non_resumable_recovery(db, job, error)
        elif complete or error is not None:
            # A completed new run, or a new ordinary failure, supersedes an
            # older recovery verdict.  A no-error staging of the SAME partial
            # checkpoint (the explicit force-release route) does not: clearing
            # resume_allowed=False there would turn a diagnostic download into
            # a back door that re-opens provider-backed generation.
            _clear_non_resumable_recovery(db, job)
        # Only a real partial checkpoint earns Resume. Quota/provider death
        # before any checkpoint remains a released diagnostic rather than a
        # converted job that can only restart from zero.
        if (
            not complete
            and _failure_allows_resume(error)
            and job.generation_recovery.get("resume_allowed") is not False
            and _has_resumable_checkpoint(job, kwargs)
        ):
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
                "generation failure; it may be downloaded for diagnosis"
                + (
                    ", but its recorded recovery contract forbids resuming "
                    "this checkpoint"
                    if isinstance(payload.get(RUN_RECOVERY_FIELD), Mapping)
                    and payload[RUN_RECOVERY_FIELD].get("resume_allowed")
                    is False
                    else " (and resumed when a checkpoint exists)"
                )
                + ", and it cannot be published to the database"
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

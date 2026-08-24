"""Terminal-generation authority for Build Concepts staged releases.

A diagnostic checkpoint is useful review evidence, but it is not a completed
Concept run.  Before this contract, ``stage_release`` used the same ``released``
job status for both shapes and publication blocked only on incidental structural
defects.  A run such as job 78 could therefore keep a durable 55% checkpoint
while the UI hid Resume, and publication safety depended on some unrelated
Type/QID defect happening to be present.

This contract makes completion explicit without changing the release schema
family or invalidating historical clean releases:

* every newly staged lane records ``terminal_generation_complete``;
* Post diagnostic releases remain resumable by restoring the job to the
  converted/unpublished lifecycle state after staging the evidence;
* ``structural_defects`` always refuses a non-terminal payload, independent of
  row shape, inventory size, or semantic issue anchoring; and
* legacy payloads infer terminality only from their recorded final checkpoint
  plus the absence of a generation error, so already-completed releases remain
  publishable after deployment.
"""
from __future__ import annotations

import copy
from functools import wraps
from typing import Any, Mapping

from .. import models
from . import build_concepts_release as release
from . import build_concepts_release_publication as publication
from . import generation


CONTRACT_VERSION = 1
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


def payload_terminal_generation_complete(
    payload: Mapping[str, Any] | None,
) -> bool:
    """Return the explicit seal, or safely infer it for pre-contract payloads."""
    if not isinstance(payload, Mapping):
        return False
    if TERMINAL_GENERATION_FIELD in payload:
        return payload.get(TERMINAL_GENERATION_FIELD) is True
    # Backward compatibility: historical successful releases did not carry the
    # field.  ``final_content_ready`` is the durable terminal Concept checkpoint;
    # a recorded generation error keeps even a final-row diagnostic release
    # non-terminal.
    return (
        str(payload.get("checkpoint_stage") or "").strip()
        == "final_content_ready"
        and not _payload_has_generation_error(payload)
    )


def _post_terminal_from_call(
    job: models.UploadJob,
    kwargs: Mapping[str, Any],
) -> bool:
    if kwargs.get("error") is not None:
        return False
    if isinstance(kwargs.get("pending_decision"), Mapping) and kwargs.get(
        "pending_decision"
    ):
        return False
    checkpoint = kwargs.get("checkpoint")
    if not isinstance(checkpoint, Mapping):
        checkpoint = job.generation_checkpoint or {}
    return _checkpoint_stage(checkpoint) == "final_content_ready"


def _write_terminal_marker(
    db,
    job: models.UploadJob,
    *,
    lane: str,
    complete: bool,
) -> None:
    key = release.release_key_for_lane(lane)
    durable = copy.deepcopy(dict(job.question_inventory or {}))
    payload = durable.get(key)
    if not isinstance(payload, Mapping):
        return
    marked = copy.deepcopy(dict(payload))
    marked[TERMINAL_GENERATION_FIELD] = bool(complete)
    durable[key] = marked
    job.question_inventory = durable
    if lane == release.LANE_POST and not complete:
        # The staged diagnostic evidence remains in ``question_inventory``, but
        # the run itself is not finished.  ``UploadJob.checkpoint_available``
        # excludes only released/generated jobs, so the normal Resume UX remains
        # available without introducing a new database enum/status migration.
        job.status = PARTIAL_RELEASE_STATUS
        job.result_ids = []
        job.detail = (
            "Generation stopped before terminal completion. Aegis staged the "
            "newest durable rows as a diagnostic release; the saved checkpoint "
            "is still resumable and nothing has been uploaded to the database."
        )
    db.commit()
    db.refresh(job)


def install() -> None:
    if getattr(release, "_TERMINAL_RELEASE_CONTRACT_VERSION", 0) >= (
        CONTRACT_VERSION
    ):
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
        _write_terminal_marker(
            db,
            job,
            lane=release.LANE_POST,
            complete=complete,
        )
        # The original result was built before the lifecycle correction above.
        # Rebuild it from the refreshed job so callers immediately see the
        # resumable partial status when applicable.
        return release.release_result(job)

    @wraps(original_stage_pre_release)
    def stage_pre_release(*args, **kwargs):
        job = _job_from_call(args, kwargs)
        db = _db_from_call(args, kwargs)
        result = original_stage_pre_release(*args, **kwargs)
        if job is None or db is None or result is None:
            return result
        # Pre belongs to the same Concept run.  The Post staged payload is the
        # run-level authority and already carries the explicit seal for new
        # runs; legacy payloads use the same safe inference.
        post_payload = release.release_payload(job, lane=release.LANE_POST)
        complete = payload_terminal_generation_complete(post_payload)
        _write_terminal_marker(
            db,
            job,
            lane=release.LANE_PRE,
            complete=complete,
        )
        # Preserve stage_pre_release's existing lane-specific return contract;
        # the terminal marker is durable state, not a reason to reshape what
        # the caller receives.
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
                "comes from a non-terminal generation checkpoint; it may be "
                "downloaded for diagnosis or resumed, but it cannot be "
                "published to the database"
            )
            if message not in defects:
                defects.append(message)
        return defects

    release.stage_release = stage_release
    release.stage_pre_release = stage_pre_release
    release.structural_defects = structural_defects
    # Publication imported the function by name, so update that bound symbol as
    # well; otherwise the explicit CMS action would keep the old gate even while
    # every other release reader saw the new one.
    publication.structural_defects = structural_defects
    release._TERMINAL_RELEASE_CONTRACT_VERSION = CONTRACT_VERSION

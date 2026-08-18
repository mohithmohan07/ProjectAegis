"""Unattended release staging for user-facing Build Concepts generation.

The low-level generation services retain their original contracts for internal
callers, tests, recovery tools and deliberately programmatic workflows. The
Build Concepts upload API calls the wrappers in this module, which stage a
release instead of publishing directly or surfacing a semantic choice.
"""
from __future__ import annotations

import copy
import inspect
from contextvars import ContextVar
from functools import wraps
from typing import Any, Callable, Mapping

from .. import models
from . import build_concepts, uploads
from . import build_concepts_release as release
from . import build_concepts_release_files as release_files
from . import release_refiner


_CONTRACT_VERSION = 4
_RELEASE_MODE: ContextVar[bool] = ContextVar(
    "aegis_build_concepts_release_mode", default=False
)
_RELEASE_CAPTURE: ContextVar[dict[str, Any] | None] = ContextVar(
    "aegis_build_concepts_release_capture", default=None
)


def _capture_deposit(original, args, kwargs) -> tuple[list[int], list[int], dict[str, Any]]:
    signature = inspect.signature(original)
    bound = signature.bind_partial(*args, **kwargs)
    values = bound.arguments
    records = [
        copy.deepcopy(dict(row))
        for row in values.get("records") or []
        if isinstance(row, Mapping)
    ]
    job = values.get("grounding_audit_job")
    inventory = copy.deepcopy(
        values.get("inventory")
        or (
            job.question_inventory
            if isinstance(job, models.UploadJob)
            and isinstance(job.question_inventory, dict)
            else {}
        )
    )
    mined_types = copy.deepcopy(
        values.get("mined_types")
        or (
            inventory.get("mined_types")
            if isinstance(inventory, dict)
            else {}
        )
        or {}
    )
    final_certificate = copy.deepcopy(
        values.get("final_grounding_certificate") or {}
    )
    checkpoint = copy.deepcopy(
        job.generation_checkpoint
        if isinstance(job, models.UploadJob)
        else {}
    )
    _RELEASE_CAPTURE.set({
        "records": records,
        "inventory": inventory,
        "mined_types": mined_types,
        "final_grounding_certificate": final_certificate,
        "checkpoint": checkpoint,
        "target_chapter_id": int(values.get("chapter_id") or 0),
        "pre_post": str(values.get("pre_post") or ""),
        "source_book": str(values.get("source_book") or ""),
    })
    # The legacy generation function reads this object after its deposit call.
    # It is deliberately truthful: no DB row and no shared workbook has been
    # written yet; the complete rows were captured for the released artifact.
    written = {
        "written": len(records),
        "sources_updated": 0,
        "parent_column": True,
        "grounding_certificate": final_certificate,
        "publication_status": "staged_release_only",
        "database_uploaded": False,
    }
    return [], [], written


def _refine_captured_records(
    db,
    job: models.UploadJob,
    target_chapter_id: int,
    captured: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The Refiner seam (docs/aegis-restructure.md §8.3), release mode.

    Runs after ``_capture_deposit`` produced the captured records and before
    ``release.stage_release``. Returns the rows to stage plus the recorded
    ``refinements`` payload entry. The Refiner must never block a release:
    any failure here stages the UNREFINED rows with an availability flag.
    """

    records = [
        copy.deepcopy(dict(row))
        for row in captured.get("records") or []
        if isinstance(row, Mapping)
    ]
    try:
        chapter = db.get(models.Chapter, int(target_chapter_id or 0))
        metadata = {
            "board": chapter.board if chapter else "",
            "grade": chapter.grade if chapter else "",
            "subject": chapter.subject if chapter else "",
            "unit": chapter.unit if chapter else "",
            "chapter_title": chapter.chapter_title if chapter else "",
            "chapter_code": chapter.chapter_code if chapter else "",
            "pre_post": "Pre" if job.learning_kind == "pre" else "Post",
            "source_book": job.source_book or job.filename or "",
            "inventory": captured.get("inventory") or {},
            "mined_types": captured.get("mined_types") or {},
            "source_text": str(job.mmd_text or ""),
        }
        refined, diff, flags = release_refiner.refine_release(
            records,
            metadata=metadata,
            instruction_set=release._instruction_set_summary(job),
            store=release_refiner.decision_store_for_job(int(job.id)),
        )
        return refined, {**diff, "review_flags": list(flags)}
    except Exception as exc:  # noqa: BLE001 - the Refiner never blocks
        flag = f"refiner unavailable: {type(exc).__name__}: {exc}"
        return records, {
            "policy_version": release_refiner.REFINER_POLICY_VERSION,
            "output_kind": "concepts_release",
            "changes": [],
            "summary": flag,
            "resealed_after_refinement": False,
            "review_flags": [flag],
        }


def _stage_pre_sibling(
    db,
    job,
    target_chapter_id: int,
    *,
    inventory: Mapping[str, Any] | None,
    reason: str,
) -> None:
    """Stage Outputs 03/04 beside whatever the Post lane just released.

    Called after EVERY ``stage_release`` on this path, not only after a
    clean capture. That is the point: this function's inputs are the
    Phase 03 snapshots already on disk (``phase3.runner`` writes them as
    phase 3 finishes), not the captured rows — so a run that completed
    Phase 03 and then failed after the deposit boundary, or reached an
    unresolved semantic boundary and released a checkpoint instead, HAS a
    Pre map available and its Outputs 03/04 should ship beside the Post
    release rather than vanish. Staged from only one of the four exits,
    their absence on the other three is indistinguishable from a chapter
    with no Pre lane at all — the same R4 confusion ``_run_snapshot``'s
    three states exist to prevent, one level up.

    Never raises, and always runs AFTER the Post release is staged:
    ``stage_pre_release_from_run`` logs and returns ``None`` on any
    failure, so a Pre-lane problem can never cost the finished Post rows.
    """

    db.refresh(job)
    release.stage_pre_release_from_run(
        db,
        job,
        target_chapter_id=target_chapter_id,
        inventory=inventory or {},
        reason=reason,
    )


def _release_after_result(
    db,
    job_id: int,
    target_chapter_id: int,
    *,
    owner_sub: str | None,
    result: object,
    captured: dict[str, Any] | None,
) -> dict[str, Any]:
    job = uploads.get_job(
        db,
        job_id,
        owner_sub=owner_sub,
        module="build_concepts",
    )
    pending = None
    if isinstance(result, Mapping):
        raw_pending = result.get("pending_decision")
        if isinstance(raw_pending, Mapping):
            pending = dict(raw_pending)
    if captured:
        refined_records, refinements = _refine_captured_records(
            db,
            job,
            target_chapter_id,
            captured,
        )
        staged = release.stage_release(
            db,
            job,
            target_chapter_id=target_chapter_id,
            records=refined_records,
            inventory=captured.get("inventory") or {},
            mined_types=captured.get("mined_types") or {},
            final_grounding_certificate=(
                captured.get("final_grounding_certificate") or {}
            ),
            checkpoint=captured.get("checkpoint") or job.generation_checkpoint,
            pending_decision=pending,
            reason=(
                "Generation completed. The output was staged and was not "
                "uploaded to the database."
            ),
            refinements=refinements,
        )
        # Outputs 03/04 (§5, spec T3): the SIBLING slot on this same job.
        # One run produces all four outputs (Q3), so the Pre release is
        # staged here beside the Post one rather than on a job of its own
        # — ``learning_kind`` stays "post" on the row and the lane rides
        # the key.
        _stage_pre_sibling(
            db,
            job,
            target_chapter_id,
            inventory=captured.get("inventory") or {},
            reason=(
                "Generation completed. The Phase 03 Pre-Learning outputs "
                "were staged and were not uploaded to the database."
            ),
        )
        return staged
    staged = release.stage_release(
        db,
        job,
        target_chapter_id=target_chapter_id,
        pending_decision=pending,
        reason=(
            "Generation reached an unresolved semantic boundary. Aegis "
            "released the newest durable checkpoint instead of asking the "
            "user to choose during generation."
        ),
    )
    _stage_pre_sibling(
        db,
        job,
        target_chapter_id,
        inventory=None,
        reason=(
            "Generation reached an unresolved semantic boundary. The "
            "Phase 03 Pre-Learning outputs this run had already recorded "
            "were staged beside the released checkpoint."
        ),
    )
    return staged


def _run_generation_release(
    original: Callable[..., object],
    db,
    job_id: int,
    target_chapter_id: int,
    *args,
    **kwargs,
) -> dict[str, Any]:
    owner_sub = kwargs.get("owner_sub")
    mode_token = _RELEASE_MODE.set(True)
    capture_token = _RELEASE_CAPTURE.set(None)
    try:
        try:
            result = original(
                db,
                job_id,
                target_chapter_id,
                *args,
                **kwargs,
            )
        except Exception as exc:
            # A failure after the deposit boundary may occur after the final
            # rows were already captured. Releasing only the newest checkpoint
            # here would throw away the most complete candidate.
            captured = copy.deepcopy(_RELEASE_CAPTURE.get())
            db.rollback()
            job = uploads.get_job(
                db,
                job_id,
                owner_sub=owner_sub,
                module="build_concepts",
            )
            if captured:
                staged = release.stage_release(
                    db,
                    job,
                    target_chapter_id=target_chapter_id,
                    records=captured.get("records") or [],
                    inventory=captured.get("inventory") or {},
                    mined_types=captured.get("mined_types") or {},
                    final_grounding_certificate=(
                        captured.get("final_grounding_certificate") or {}
                    ),
                    checkpoint=(
                        captured.get("checkpoint")
                        or job.generation_checkpoint
                    ),
                    error=exc,
                    reason=(
                        "Generation failed after its final rows were "
                        "materialized. Aegis released those captured rows "
                        "with the failure attached instead of falling back "
                        "to an older or empty checkpoint."
                    ),
                )
                _stage_pre_sibling(
                    db,
                    job,
                    target_chapter_id,
                    inventory=captured.get("inventory") or {},
                    reason=(
                        "Generation failed after its final rows were "
                        "materialized. The Phase 03 Pre-Learning outputs "
                        "this run had already recorded were staged beside "
                        "the released rows."
                    ),
                )
                return staged
            staged = release.stage_release(
                db,
                job,
                target_chapter_id=target_chapter_id,
                error=exc,
                reason=(
                    "Generation failed after creating a durable checkpoint. "
                    "Aegis released the newest available rows with the "
                    "failure attached instead of returning no output."
                ),
            )
            _stage_pre_sibling(
                db,
                job,
                target_chapter_id,
                inventory=None,
                reason=(
                    "Generation failed after creating a durable checkpoint. "
                    "The Phase 03 Pre-Learning outputs this run had already "
                    "recorded were staged beside the released rows."
                ),
            )
            return staged
        captured = copy.deepcopy(_RELEASE_CAPTURE.get())
        return _release_after_result(
            db,
            job_id,
            target_chapter_id,
            owner_sub=owner_sub,
            result=result,
            captured=captured,
        )
    finally:
        _RELEASE_CAPTURE.reset(capture_token)
        _RELEASE_MODE.reset(mode_token)


def generate_post_learning(
    db,
    job_id: int,
    target_chapter_id: int,
    *args,
    **kwargs,
) -> dict[str, Any]:
    return _run_generation_release(
        build_concepts.generate_post_learning,
        db,
        job_id,
        target_chapter_id,
        *args,
        **kwargs,
    )


def _wrap_generation(original):
    """Test/helper adapter retaining a wrapper-shaped interface."""

    @wraps(original)
    def wrapped(db, job_id: int, target_chapter_id: int, *args, **kwargs):
        return _run_generation_release(
            original,
            db,
            job_id,
            target_chapter_id,
            *args,
            **kwargs,
        )

    return wrapped


def _install_manifest_extension() -> None:
    current = getattr(models.UploadJob, "source_artifacts", None)
    if not isinstance(current, property):
        return
    original_getter = current.fget
    if original_getter is None or getattr(original_getter, "_aegis_release", False):
        return

    def source_artifacts(job: models.UploadJob) -> dict[str, Any]:
        base = original_getter(job)
        manifest = copy.deepcopy(base) if isinstance(base, dict) else {
            "available": False,
            "status": "unavailable",
            "files": [],
            "summary": {},
        }
        entries = release_files.release_artifact_entries(job)
        if not entries:
            return manifest
        files = [
            copy.deepcopy(row)
            for row in manifest.get("files") or []
            if isinstance(row, dict)
        ]
        existing = {str(row.get("kind") or "") for row in files}
        files.extend(
            copy.deepcopy(row)
            for row in entries
            if str(row.get("kind") or "") not in existing
        )
        payload = release.release_payload(job) or {}
        summary = copy.deepcopy(manifest.get("summary") or {})
        release_summary = copy.deepcopy(payload.get("summary") or {})
        summary["release_rows"] = int(release_summary.get("row_count") or 0)
        summary["release_issues"] = int(release_summary.get("issue_count") or 0)
        manifest.update({
            "available": True,
            "files": files,
            "summary": summary,
            "release_output": {
                "available": True,
                "status": (
                    "uploaded"
                    if release_summary.get("database_uploaded")
                    else "released"
                ),
                **release_summary,
            },
        })
        return manifest

    source_artifacts._aegis_release = True
    models.UploadJob.source_artifacts = property(source_artifacts)


def _install_deposit_interceptor() -> None:
    current = build_concepts._deposit_and_publish_concepts
    if getattr(current, "_aegis_release_interceptor", False):
        return
    original = current

    @wraps(original)
    def deposit_and_publish(*args, **kwargs):
        if not _RELEASE_MODE.get():
            return original(*args, **kwargs)
        return _capture_deposit(original, args, kwargs)

    deposit_and_publish._aegis_release_interceptor = True
    deposit_and_publish._aegis_release_original = original
    build_concepts._deposit_and_publish_concepts = deposit_and_publish


def install() -> None:
    """Install context-aware release capture and artifact projection.

    Only the user-facing API wrappers set ``_RELEASE_MODE``. Every other caller
    traverses the original deposit and generation contracts unchanged.
    """

    if getattr(models.UploadJob, "_RELEASE_STAGING_CONTRACT_VERSION", 0) >= (
        _CONTRACT_VERSION
    ):
        return
    _install_deposit_interceptor()
    _install_manifest_extension()
    models.UploadJob._RELEASE_STAGING_CONTRACT_VERSION = _CONTRACT_VERSION

"""Unattended release staging for user-facing Build Concepts generation.

The low-level generation services retain their original contracts for internal
callers, tests, recovery tools and any deliberately programmatic workflow. The
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


_CONTRACT_VERSION = 3
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
        return release.stage_release(
            db,
            job,
            target_chapter_id=target_chapter_id,
            records=captured.get("records") or [],
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
        )
    return release.stage_release(
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
    original_deposit = build_concepts._deposit_and_publish_concepts

    @wraps(original_deposit)
    def capture_only(*deposit_args, **deposit_kwargs):
        return _capture_deposit(
            original_deposit,
            deposit_args,
            deposit_kwargs,
        )

    build_concepts._deposit_and_publish_concepts = capture_only
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
            # A failure after the old deposit boundary may occur after the
            # final rows were already captured. Releasing only the newest
            # checkpoint here would throw away the most complete candidate.
            captured = copy.deepcopy(_RELEASE_CAPTURE.get())
            db.rollback()
            job = uploads.get_job(
                db,
                job_id,
                owner_sub=owner_sub,
                module="build_concepts",
            )
            if captured:
                return release.stage_release(
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
            return release.stage_release(
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
        build_concepts._deposit_and_publish_concepts = original_deposit
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


def generate_pre_learning_from_upload(
    db,
    job_id: int,
    target_chapter_id: int,
    *args,
    **kwargs,
) -> dict[str, Any]:
    return _run_generation_release(
        build_concepts.generate_pre_learning_from_upload,
        db,
        job_id,
        target_chapter_id,
        *args,
        **kwargs,
    )


def _wrap_generation(original):
    """Test/helper adapter retaining the earlier wrapper-shaped interface."""

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


def install() -> None:
    """Install only the release artifact projection.

    Generation services are intentionally not monkey-patched. The user-facing
    API invokes the scoped wrappers above, while low-level internal callers keep
    their established success, failure and checkpoint contracts.
    """

    if getattr(models.UploadJob, "_RELEASE_STAGING_CONTRACT_VERSION", 0) >= (
        _CONTRACT_VERSION
    ):
        return
    _install_manifest_extension()
    models.UploadJob._RELEASE_STAGING_CONTRACT_VERSION = _CONTRACT_VERSION

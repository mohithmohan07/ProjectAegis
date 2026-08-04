"""Install unattended release staging around Build Concepts generation."""
from __future__ import annotations

import copy
import inspect
from contextvars import ContextVar
from functools import wraps
from typing import Any, Mapping

from .. import models
from . import build_concepts, uploads
from . import build_concepts_release as release
from . import build_concepts_release_files as release_files


_CONTRACT_VERSION = 1
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
    # The original generation functions inspect this object for publication
    # metadata. It is deliberately truthful: no DB row and no shared workbook
    # has been written yet.
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


def _wrap_generation(original):
    @wraps(original)
    def wrapped(
        db,
        job_id: int,
        target_chapter_id: int,
        *args,
        **kwargs,
    ):
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
                db.rollback()
                job = uploads.get_job(
                    db,
                    job_id,
                    owner_sub=owner_sub,
                    module="build_concepts",
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
            _RELEASE_CAPTURE.reset(capture_token)
            _RELEASE_MODE.reset(mode_token)

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


def _hide_manual_pending_property() -> None:
    current = getattr(models.UploadJob, "pending_decision", None)
    if not isinstance(current, property):
        return
    getter = current.fget
    if getter is None or getattr(getter, "_aegis_unattended", False):
        return

    def pending_decision(job: models.UploadJob):
        # Build Concepts retains the complete decision inside its exported
        # checkpoint/release audit, but the public job contract never asks the
        # user to select a semantic action during generation.
        if str(getattr(job, "module", "")) == "build_concepts":
            return None
        return getter(job)

    pending_decision._aegis_unattended = True
    models.UploadJob.pending_decision = property(pending_decision)


def install() -> None:
    if getattr(build_concepts, "_RELEASE_STAGING_CONTRACT_VERSION", 0) >= (
        _CONTRACT_VERSION
    ):
        return

    original_deposit = build_concepts._deposit_and_publish_concepts

    @wraps(original_deposit)
    def deposit_and_publish(*args, **kwargs):
        if not _RELEASE_MODE.get():
            return original_deposit(*args, **kwargs)
        return _capture_deposit(original_deposit, args, kwargs)

    build_concepts._deposit_and_publish_concepts = deposit_and_publish
    build_concepts.generate_post_learning = _wrap_generation(
        build_concepts.generate_post_learning
    )
    build_concepts.generate_pre_learning_from_upload = _wrap_generation(
        build_concepts.generate_pre_learning_from_upload
    )
    _install_manifest_extension()
    _hide_manual_pending_property()
    build_concepts._RELEASE_STAGING_CONTRACT_VERSION = _CONTRACT_VERSION

"""Install Phase 2.2.1 protocol hardening and the GPT PDF-to-ACSD reader."""
from __future__ import annotations

import copy
from functools import wraps
from pathlib import Path
from typing import Any

from . import canonical_source_phase2 as phase2
from . import canonical_source_phase221_fallback as fallback

_CONTRACT_VERSION = 1


def install() -> None:
    from . import mmd, openai_usage, progress, uploads
    from .. import config

    if getattr(uploads, "_CANONICAL_SOURCE_PHASE221_VERSION", 0) >= _CONTRACT_VERSION:
        return

    original_convert = uploads.convert_job
    original_manifest = phase2.artifact_manifest
    original_download = uploads.source_artifact_download
    original_load_or_refresh = phase2._load_or_refresh_for_job

    def _manifest_for_job(job: Any) -> dict[str, Any]:
        manifest = phase2.artifact_manifest(
            uploads.source_artifact_directory(int(job.id))
        )
        manifest["manifest_url"] = f"/source-artifacts/uploads/{int(job.id)}"
        for item in manifest.get("files") or []:
            if isinstance(item, dict) and item.get("kind"):
                item["download_url"] = (
                    f"/source-artifacts/uploads/{int(job.id)}/{item['kind']}"
                )
        return manifest

    def _run_reader(
        db: Any,
        job: Any,
        *,
        reason: list[str],
    ) -> dict[str, Any]:
        if not config.use_live_generation():
            raise mmd.ConversionError(
                "GPT PDF-to-ACSD conversion requires OPENAI_API_KEY"
            )
        path = uploads.upload_file_path(job)
        artifact_dir = uploads.source_artifact_directory(int(job.id))
        progress.log(
            "Reading the PDF with GPT PDF-to-ACSD: OpenAI will return strict "
            "page/block JSON, an independent pass will verify it, and Aegis "
            "will render the accepted structure deterministically.",
            level="info",
        )
        persistence_key = f"upload-job:{int(job.id)}"
        persisted = (
            job.openai_usage if isinstance(job.openai_usage, dict) else {}
        )
        summary: dict[str, Any] = copy.deepcopy(persisted)
        bundle: dict[str, Any] | None = None
        try:
            if openai_usage.is_tracking():
                openai_usage.bind_persisted_summary(persistence_key, persisted)
                try:
                    bundle = fallback.reconstruct_pdf_to_acsd(
                        path,
                        job_id=int(job.id),
                        artifact_dir=artifact_dir,
                        fallback_reason=reason,
                    )
                finally:
                    summary = openai_usage.cumulative_summary(
                        persisted, persistence_key=persistence_key
                    )
            else:
                with openai_usage.track():
                    openai_usage.bind_persisted_summary(
                        persistence_key, persisted
                    )
                    try:
                        bundle = fallback.reconstruct_pdf_to_acsd(
                            path,
                            job_id=int(job.id),
                            artifact_dir=artifact_dir,
                            fallback_reason=reason,
                        )
                    finally:
                        summary = openai_usage.cumulative_summary(
                            persisted, persistence_key=persistence_key
                        )
        except Exception:
            # Conversion-time source recovery can still incur billable calls.
            # Preserve that usage even though the canonical bundle remains
            # unchanged and the conversion is reported as failed.
            job.openai_usage = summary
            db.commit()
            db.refresh(job)
            progress.usage(summary)
            raise
        if bundle is None:  # pragma: no cover - defensive contract guard
            raise RuntimeError("GPT PDF-to-ACSD returned no source bundle")
        job.mmd_text = str(bundle["mmd_text"])
        job.question_inventory = {}
        job.generation_checkpoint = {}
        job.generation_log = []
        job.openai_usage = summary
        job.status = "converted"
        reconstruction = bundle["reconstruction"]
        job.detail = (
            "Converted through verified GPT PDF-to-ACSD "
            f"({reconstruction['page_count']} pages, "
            f"{reconstruction['asset_count']} visual assets)."
        )
        db.commit()
        db.refresh(job)
        progress.usage(summary)
        progress.set_progress(1.0, label="Converted through GPT PDF-to-ACSD")
        progress.log(
            "GPT PDF-to-ACSD verified "
            f"{reconstruction['page_count']} page(s), materialized "
            f"{reconstruction['asset_count']} source visual(s), and compiled the "
            "result through every canonical-source gate.",
            level="success",
        )
        manifest = _manifest_for_job(job)
        return {
            "job_id": int(job.id),
            "status": job.status,
            "filename": job.filename,
            "mmd_chars": len(job.mmd_text),
            "mmd_text": job.mmd_text,
            "conversion_source": fallback.FALLBACK_ORIGIN,
            "source_artifacts": manifest,
            "openai_usage": summary,
        }

    @wraps(original_convert)
    def convert_job(*args, **kwargs):
        db = args[0] if args else kwargs.get("db")
        job_id = args[1] if len(args) > 1 else kwargs.get("job_id")
        module = str(kwargs.get("module") or "")
        owner_sub = kwargs.get("owner_sub")
        if module != "build_concepts" or not job_id:
            return original_convert(*args, **kwargs)
        staged = uploads.get_job(
            db,
            int(job_id),
            owner_sub=owner_sub,
            module="build_concepts",
        )
        path = uploads.upload_file_path(staged)
        if path.suffix.lower() != ".pdf" or not fallback._enabled():
            return original_convert(*args, **kwargs)

        # The GPT reader is the only PDF converter. There is no second
        # converter to try first, nothing to compare it against, and so no
        # quality gate to choose between them. Only this reader applies the
        # chapter outline, which is what gives a chapter its topics and its
        # questions.
        with uploads.exclusive_job_operation(int(job_id)):
            current = uploads.get_job(
                db, int(job_id), owner_sub=owner_sub,
                module="build_concepts",
            )
            return _run_reader(db, current, reason=["pdf_source"])

    @wraps(original_load_or_refresh)
    def load_or_refresh_for_job(job: Any):
        canonical, report = original_load_or_refresh(job)
        return fallback.rehydrate_verified_fallback(
            job,
            canonical,
            report,
            artifact_dir=uploads.source_artifact_directory(int(job.id)),
        )

    @wraps(original_manifest)
    def artifact_manifest(directory: Path):
        manifest = original_manifest(directory)
        optional_files, reconstruction = fallback.optional_artifact_manifest(directory)
        existing = {str(item.get("kind") or "") for item in manifest.get("files") or []}
        for item in optional_files:
            if item["kind"] not in existing:
                manifest.setdefault("files", []).append(item)
        manifest["source_reconstruction"] = (
            copy.deepcopy(reconstruction) if reconstruction else {
                "version": fallback.FALLBACK_VERSION,
                "status": "not_used",
                "source_origin": "uploaded_mmd",
            }
        )
        if reconstruction.get("status") == "verified":
            for item in manifest.get("files") or []:
                if isinstance(item, dict) and item.get("kind") == "raw_mmd":
                    item["label"] = (
                        "Deterministic MMD rendered from verified GPT page ACSD"
                    )
        return manifest

    def source_artifact_download(job: Any, kind: str):
        try:
            return original_download(job, kind)
        except ValueError:
            return fallback.optional_artifact_path(
                uploads.source_artifact_directory(int(job.id)), kind
            )

    uploads.convert_job = convert_job
    phase2._load_or_refresh_for_job = load_or_refresh_for_job
    phase2.artifact_manifest = artifact_manifest
    uploads.source_artifact_download = source_artifact_download
    uploads._CANONICAL_SOURCE_PHASE221_VERSION = _CONTRACT_VERSION

"""Install the Phase-1 canonical-source compiler as a conversion shadow.

The wrapper is deliberately additive: the converted raw MMD remains the only input
used by current assessment and concept generation.  The canonical JSON, derived Aegis
MMD, immutable raw copy, and validation report are written beside the upload for
inspection and comparison only.
"""
from __future__ import annotations

import re
from functools import wraps
from pathlib import Path
from typing import Any

from .. import config
from . import canonical_source

_CONTRACT_VERSION = 1


def _artifact_directory(job_id: int) -> Path:
    root = config.UPLOAD_DIR.resolve()
    directory = (root / str(int(job_id)) / canonical_source.ARTIFACT_DIRNAME).resolve()
    if directory == root or root not in directory.parents:
        raise ValueError("invalid canonical-source artifact directory")
    return directory


def _manifest(job_id: int) -> dict[str, Any]:
    manifest = canonical_source.artifact_manifest(_artifact_directory(job_id))
    manifest["manifest_url"] = f"/source-artifacts/uploads/{int(job_id)}"
    for item in manifest.get("files") or []:
        if isinstance(item, dict) and item.get("kind"):
            item["download_url"] = (
                f"/source-artifacts/uploads/{int(job_id)}/{item['kind']}"
            )
    return manifest


def _download(job_id: int, kind: str):
    return canonical_source.artifact_path(_artifact_directory(job_id), kind)


def _install_heading_title_normalization() -> None:
    """Expose clean semantic titles while the raw numbered heading stays intact."""
    if getattr(canonical_source, "_NUMBERED_HEADING_TITLE_NORMALIZED", False):
        return
    original_heading_info = canonical_source._heading_info

    @wraps(original_heading_info)
    def heading_info(line: str):
        result = original_heading_info(line)
        if result is None:
            return None
        level, title, kind = result
        title = re.sub(
            r"^\s*\d+(?:\.\d+)*[.)]?\s+",
            "",
            str(title or ""),
        ).strip()
        return level, title, kind

    canonical_source._heading_info = heading_info
    canonical_source._NUMBERED_HEADING_TITLE_NORMALIZED = True


def install() -> None:
    """Wrap upload conversion exactly once without changing its generation source."""
    from . import progress, uploads

    _install_heading_title_normalization()
    if getattr(uploads, "_CANONICAL_SOURCE_SHADOW_VERSION", 0) >= _CONTRACT_VERSION:
        return

    original_convert = uploads.convert_job
    original_replace = uploads.replace_file

    @wraps(original_convert)
    def convert_job(*args, **kwargs):
        result = original_convert(*args, **kwargs)
        db = args[0] if args else kwargs.get("db")
        job_id = args[1] if len(args) > 1 else kwargs.get("job_id")
        owner_sub = kwargs.get("owner_sub")
        module = kwargs.get("module", "")
        try:
            job = uploads.get_job(
                db,
                int(job_id),
                owner_sub=owner_sub,
                module=module,
            )
            directory = _artifact_directory(job.id)
            canonical_source.clear_shadow_artifacts(directory)
            progress.log(
                "Compiling Phase 1 canonical-source shadow; the existing "
                "generation path will continue using the raw MMD unchanged."
            )
            canonical_source.write_shadow_artifacts(
                directory,
                mmd_text=job.mmd_text,
                source_filename=job.filename,
            )
            manifest = _manifest(job.id)
            summary = manifest.get("summary") or {}
            progress.log(
                "Canonical-source shadow "
                f"{manifest.get('status', 'unknown')}: "
                f"{summary.get('sections', 0)} section(s), "
                f"{summary.get('blocks', 0)} block(s), "
                f"{summary.get('tasks', 0)} task(s), "
                f"{summary.get('images', 0)} image(s), "
                f"{summary.get('math_spans', 0)} math span(s).",
                level=(
                    "success"
                    if manifest.get("status") == "passed"
                    else "warning"
                ),
            )
        except Exception as exc:  # shadow output must not invalidate conversion
            progress.log(
                "Canonical-source shadow could not be written "
                f"({exc}); raw MMD conversion remains valid and unchanged.",
                level="warning",
            )
            manifest = _manifest(int(job_id)) if job_id else {
                "available": False,
                "shadow_mode": True,
                "used_for_generation": False,
                "status": "unavailable",
                "files": [],
            }
        if isinstance(result, dict):
            result = {
                **result,
                "source_artifacts": manifest,
            }
        return result

    @wraps(original_replace)
    def replace_file(*args, **kwargs):
        job_id = args[1] if len(args) > 1 else kwargs.get("job_id")
        result = original_replace(*args, **kwargs)
        if job_id:
            canonical_source.clear_shadow_artifacts(
                _artifact_directory(int(job_id))
            )
        return result

    uploads.convert_job = convert_job
    uploads.replace_file = replace_file
    uploads.source_artifact_directory = _artifact_directory
    uploads.source_artifact_manifest = lambda job: _manifest(int(job.id))
    uploads.source_artifact_download = (
        lambda job, kind: _download(int(job.id), kind)
    )
    uploads._CANONICAL_SOURCE_SHADOW_VERSION = _CONTRACT_VERSION

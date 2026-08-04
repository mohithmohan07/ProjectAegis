"""Lazy source-artifact manifest entries for released concept output.

Large diagnostic archives are generated only by their download endpoint. Job
serialization must stay cheap even when the archive contains the original PDF,
canonical source bundles, page evidence and the entire generation checkpoint.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import models
from . import build_concepts_release_files as release_files
from .build_concepts_release import release_payload


def _safe_filename(value: str, fallback: str) -> str:
    name = Path(str(value or "")).stem
    name = "_".join(name.split()).strip("._")
    return name[:100] or fallback


def release_artifact_entries(job: models.UploadJob) -> list[dict[str, Any]]:
    payload = release_payload(job)
    if payload is None:
        return []
    stem = _safe_filename(job.filename, "concepts")
    uploaded = bool((payload.get("summary") or {}).get("database_uploaded"))
    return [
        {
            "kind": "released_concepts",
            "label": "Download released output",
            "filename": f"{stem}_released.xlsx",
            "media_type": (
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
            "size_bytes": 0,
            "download_url": f"/build-concepts/uploads/{job.id}/release.xlsx",
            "action": "download",
        },
        {
            "kind": "release_diagnostics",
            "label": "Export full issue context",
            "filename": f"{stem}_diagnostics.zip",
            "media_type": "application/zip",
            "size_bytes": 0,
            "download_url": f"/build-concepts/uploads/{job.id}/diagnostics.zip",
            "action": "download",
        },
        {
            "kind": "release_payload",
            "label": "Download release JSON",
            "filename": f"{stem}_release.json",
            "media_type": "application/json",
            "size_bytes": 0,
            "download_url": f"/build-concepts/uploads/{job.id}/release.json",
            "action": "download",
        },
        {
            "kind": "database_upload",
            "label": (
                "Already uploaded to database"
                if uploaded else "Upload released output to database"
            ),
            "filename": "",
            "media_type": "application/json",
            "size_bytes": 0,
            "download_url": f"/build-concepts/uploads/{job.id}/upload-release",
            "action": "post",
            "disabled": uploaded,
            "requires_confirmation": True,
        },
    ]


def install() -> None:
    release_files.release_artifact_entries = release_artifact_entries

"""Lazy source-artifact manifest entries for released concept output.

Large diagnostic archives are generated only by their download endpoint. Job
serialization must stay cheap even when the archive contains the original PDF,
canonical source bundles, page evidence and the entire generation checkpoint.
"""
from __future__ import annotations

from typing import Any

from .. import models
from . import build_concepts_release_files as release_files
from .build_concepts_release import LANE_PRE, release_payload


# The filename stem comes from ``release_files``, which owns it.
#
# This module used to carry its own copy, and the two disagreed: this one
# only collapsed whitespace, so a source named ``Grade 6: Science.mmd``
# advertised ``Grade_6:_Science`` here and ``Grade_6_Science`` there. The
# advertised stem is what the browser saves the file as, so a divergence
# is user-visible, and a twin pin that asserts the two implementations
# agree is only as good as the pieces they share. One owner, no twin.
_safe_filename = release_files._safe_filename


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
    ] + _pre_entries(job, stem)


def _pre_entries(job: models.UploadJob, stem: str) -> list[dict[str, Any]]:
    """Outputs 03/04, lazily — the twin of ``release_files``' own version.

    This module MONKEYPATCHES ``release_files.release_artifact_entries``
    (see ``install()``), so this list is the one production actually
    serves — and, once ``install()`` has run, the one a caller reaching
    through that module attribute gets as well. The eager twin is
    reachable only as ``release_files.eager_release_artifact_entries``,
    which is how ``tests/test_pre_release_lane_wiring.py`` pins the two
    against each other. An entry added to only one of them is a silent
    no-op; the pin exists so that fails loudly.
    The only intended difference is this module's contract: every
    ``size_bytes`` is 0 because generating an archive to measure it is
    exactly what the lazy manifest exists to avoid.
    """

    payload = release_payload(job, lane=LANE_PRE)
    if payload is None:
        return []
    query = f"?lane={LANE_PRE}"
    uploaded = bool((payload.get("summary") or {}).get("database_uploaded"))
    return [
        {
            "kind": "released_pre_concepts",
            "label": "Download released Pre-Learning output",
            "filename": f"{stem}_pre_released.xlsx",
            "media_type": (
                "application/vnd.openxmlformats-officedocument"
                ".spreadsheetml.sheet"
            ),
            "size_bytes": 0,
            "download_url": (
                f"/build-concepts/uploads/{job.id}/release.xlsx{query}"
            ),
            "action": "download",
        },
        {
            "kind": "pre_release_diagnostics",
            "label": "Export full Pre-Learning issue context",
            "filename": f"{stem}_pre_diagnostics.zip",
            "media_type": "application/zip",
            "size_bytes": 0,
            "download_url": (
                f"/build-concepts/uploads/{job.id}/diagnostics.zip{query}"
            ),
            "action": "download",
        },
        {
            "kind": "pre_release_payload",
            "label": "Download Pre-Learning release JSON",
            "filename": f"{stem}_pre_release.json",
            "media_type": "application/json",
            "size_bytes": 0,
            "download_url": (
                f"/build-concepts/uploads/{job.id}/release.json{query}"
            ),
            "action": "download",
        },
        {
            "kind": "pre_database_upload",
            "label": (
                "Already uploaded to database"
                if uploaded
                else "Upload released Pre-Learning output to database"
            ),
            "filename": "",
            "media_type": "application/json",
            "size_bytes": 0,
            "download_url": (
                f"/build-concepts/uploads/{job.id}/upload-release{query}"
            ),
            "action": "post",
            "disabled": uploaded,
            "requires_confirmation": True,
        },
    ]


def install() -> None:
    release_files.release_artifact_entries = release_artifact_entries

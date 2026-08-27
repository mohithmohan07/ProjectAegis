"""Lazy source-artifact manifest entries for released concept output.

Large diagnostic archives are generated only by their download endpoint. Job
serialization must stay cheap even when the archive contains the original PDF,
canonical source bundles, page evidence and the entire generation checkpoint.
"""
from __future__ import annotations

from typing import Any

from .. import models
from . import build_concepts_release_files as release_files
from .build_concepts_release import (
    LANE_POST,
    LANE_PRE,
    release_payload,
    release_state,
    structural_defects,
)


# The filename stem comes from ``release_files``, which owns it.
#
# This module used to carry its own copy, and the two disagreed: this one
# only collapsed whitespace, so a source named ``Grade 6: Science.mmd``
# advertised ``Grade_6:_Science`` here and ``Grade_6_Science`` there. The
# advertised stem is what the browser saves the file as, so a divergence
# is user-visible, and a twin pin that asserts the two implementations
# agree is only as good as the pieces they share. One owner, no twin.
_safe_filename = release_files._safe_filename

# The owner's output numbering and the order that follows from it come
# from ``release_files`` too, for exactly the reason above: an order each
# twin defines separately is an order ``install()`` can silently break.
OUTPUT_KIND_ORDER = release_files.OUTPUT_KIND_ORDER
in_owner_order = release_files.in_owner_order
# The Master entries themselves come from one builder, for the same
# reason the ORDER does: two implementations of "is this lane's Master
# servable, and if not why not" is two answers waiting to disagree.
master_entry = release_files.master_entry
_XLSX_MEDIA_TYPE = release_files._XLSX_MEDIA_TYPE


def release_artifact_entries(job: models.UploadJob) -> list[dict[str, Any]]:
    payload = release_payload(job)
    if payload is None:
        return []
    stem = _safe_filename(job.filename, "concepts")
    uploaded = bool((payload.get("summary") or {}).get("database_uploaded"))
    return in_owner_order([
        {
            # Output 03 · Post-Learning Concept File. Same builder as
            # Output 01, same route, no ``lane`` parameter — so no
            # already-published URL moves.
            "kind": "release_bulk_import",
            "label": "Download the Post-Learning Concept File",
            "filename": f"{stem}_bulk_import.xlsx",
            "media_type": _XLSX_MEDIA_TYPE,
            "size_bytes": 0,
            "download_url": (
                f"/build-concepts/uploads/{job.id}"
                f"/release-bulk-import.xlsx"
            ),
            "action": "download",
        },
        # Output 04 · Post-Learning Master File — the shared builder, so
        # the lazy twin and the eager one cannot disagree about it.
        master_entry(job, lane=LANE_POST),
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
            # States its lane where the download URLs do not — the publish
            # endpoint refuses a blank or absent lane, so a lane-less
            # publish URL would be an affordance its own gate rejects.
            "download_url": (
                f"/build-concepts/uploads/{job.id}"
                f"/upload-release?lane={LANE_POST}"
            ),
            "action": "post",
            "disabled": uploaded,
            "requires_confirmation": True,
            "release_state": release_state(payload),
            "structural_defects": structural_defects(payload),
        },
    ] + _pre_entries(job, stem))


def _pre_entries(job: models.UploadJob, stem: str) -> list[dict[str, Any]]:
    """Outputs 01/02, lazily — the twin of ``release_files``' own version.

    (Numbered under the owner's ruling OD4 / register entry D9-Q22: the
    Pre lane is 01/02 and the Post lane 03/04. Earlier text in this repo
    called the same pair "Outputs 03/04"; it is superseded, not wrong.)

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
        # The four outputs are enumerated ALWAYS (OD4 / map P16) — the
        # eager twin's rule, mirrored here so production (which serves
        # THIS list) shows the same present-and-disabled pair.
        return in_owner_order([
            {
                "kind": "pre_release_bulk_import",
                "label": "Download the Pre-Learning Concept File",
                "filename": f"{stem}_pre_bulk_import.xlsx",
                "media_type": _XLSX_MEDIA_TYPE,
                "size_bytes": 0,
                "download_url": "",
                "action": "download",
                "disabled": True,
                "disabled_reason": release_files.pre_not_staged_reason(job),
            },
            master_entry(job, lane=LANE_PRE),
        ])
    query = f"?lane={LANE_PRE}"
    uploaded = bool((payload.get("summary") or {}).get("database_uploaded"))
    empty_note = release_files.pre_lane_empty_reason(payload)
    return in_owner_order([
        {
            # Output 01 · Pre-Learning Concept File.
            "kind": "pre_release_bulk_import",
            "label": "Download the Pre-Learning Concept File",
            "filename": f"{stem}_pre_bulk_import.xlsx",
            "media_type": _XLSX_MEDIA_TYPE,
            "size_bytes": 0,
            "download_url": (
                f"/build-concepts/uploads/{job.id}"
                f"/release-bulk-import.xlsx{query}"
            ),
            "action": "download",
            # The zero-row reason (the run's own recorded refusal or
            # verdict) — same helper as the eager twin, so the two
            # manifests cannot disagree on it.
            **({"note": empty_note} if empty_note else {}),
        },
        # Output 02 · Pre-Learning Master File — the shared builder.
        master_entry(job, lane=LANE_PRE),
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
            "release_state": release_state(payload),
            "structural_defects": structural_defects(payload),
        },
    ])


def install() -> None:
    release_files.release_artifact_entries = release_artifact_entries

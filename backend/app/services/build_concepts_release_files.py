"""Files exported by the unattended Build Concepts release contract."""
from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import Path
from typing import Any, Iterable, Mapping

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .. import models
from . import concept_run_report
from . import uploads
from .build_concepts_release import (
    RELEASE_ROW_BLOCKS_FIELD,
    RELEASE_ROW_ERRORS_FIELD,
    RELEASE_ROW_QIDS_FIELD,
    RELEASE_ROW_ROUTES_FIELD,
    RELEASE_ROW_STATUS_FIELD,
    release_payload,
)


_HEADER_FILL = PatternFill("solid", fgColor="1F4E78")
_HEADER_FONT = Font(color="FFFFFF", bold=True)
_ERROR_FILL = PatternFill("solid", fgColor="F4CCCC")
_WARNING_FILL = PatternFill("solid", fgColor="FFF2CC")
_READY_FILL = PatternFill("solid", fgColor="D9EAD3")
_TYPE_FILL = PatternFill("solid", fgColor="D9EAF7")
_CASE_FILL = PatternFill("solid", fgColor="EADCF8")
_EXAMPLE_FILL = PatternFill("solid", fgColor="F3F3F3")
_BLOCK_ID_RE = re.compile(r"\bBLK-[A-Za-z0-9_-]+\b")


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
        default=str,
    ).encode("utf-8")


def _safe_filename(value: str, fallback: str) -> str:
    name = Path(str(value or "")).stem
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return name[:100] or fallback


def _cell_text(value: object) -> str:
    if isinstance(value, (dict, list, tuple, set)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return str(value or "")


def _header(sheet, headers: list[str]) -> None:
    sheet.append(headers)
    for cell in sheet[1]:
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center")
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = f"A1:{get_column_letter(len(headers))}1"


def _fit_columns(sheet, maximum: int = 70) -> None:
    for column in range(1, sheet.max_column + 1):
        width = 10
        for row in range(1, min(sheet.max_row, 250) + 1):
            width = max(width, min(maximum, len(_cell_text(sheet.cell(row, column).value)) + 2))
        sheet.column_dimensions[get_column_letter(column)].width = width


def build_release_workbook(job: models.UploadJob) -> bytes:
    payload = release_payload(job)
    if payload is None:
        raise ValueError("this upload has no staged release")

    workbook = Workbook()
    concepts = workbook.active
    concepts.title = "Released Concepts"
    concept_headers = [
        "Release Row",
        "Release Status",
        "Errors / Warnings",
        "Topic",
        "Parent Concept",
        "Concept Title",
        "Concept Details",
        "Keywords",
        "Semantic Topic ID",
        "Source BLKs",
        "QIDs",
        "Type / Case Routes",
    ]
    _header(concepts, concept_headers)
    records = [row for row in payload.get("records") or [] if isinstance(row, Mapping)]
    for index, record in enumerate(records, start=1):
        status = str(record.get(RELEASE_ROW_STATUS_FIELD) or "ready")
        errors = record.get(RELEASE_ROW_ERRORS_FIELD) or []
        concepts.append([
            index,
            status,
            "\n".join(str(value) for value in errors),
            record.get("topic", ""),
            record.get("parent_concept", ""),
            record.get("concept_title") or record.get("concept") or "",
            record.get("concept_details") or record.get("concept_description") or "",
            record.get("keywords", ""),
            record.get("_semantic_topic_id", ""),
            ", ".join(str(value) for value in record.get(RELEASE_ROW_BLOCKS_FIELD) or []),
            ", ".join(str(value) for value in record.get(RELEASE_ROW_QIDS_FIELD) or []),
            _cell_text(record.get(RELEASE_ROW_ROUTES_FIELD) or []),
        ])
        fill = _ERROR_FILL if status == "released_with_errors" else _READY_FILL
        for cell in concepts[concepts.max_row]:
            cell.fill = fill
            cell.alignment = Alignment(vertical="top", wrap_text=True)

    routes = workbook.create_sheet("Type Case Routing")
    route_headers = [
        "Row Kind",
        "Type ID",
        "Type Title",
        "Type Definition",
        "Case ID",
        "Case Definition",
        "Owner Topic IDs",
        "QIDs",
        "Example QID",
        "Example Prompt",
        "Audit Status",
        "Error",
    ]
    _header(routes, route_headers)
    for row in payload.get("type_case_rows") or []:
        if not isinstance(row, Mapping):
            continue
        kind = str(row.get("row_kind") or "")
        routes.append([
            kind,
            row.get("type_id", ""),
            row.get("type_title", ""),
            row.get("type_definition", ""),
            row.get("case_id", ""),
            row.get("case_definition", ""),
            ", ".join(str(value) for value in row.get("owner_topic_ids") or []),
            ", ".join(str(value) for value in row.get("qids") or []),
            row.get("example_qid", ""),
            row.get("example_prompt", ""),
            row.get("audit_status", "ready"),
            row.get("error", ""),
        ])
        fill = _TYPE_FILL if kind == "type" else _CASE_FILL if kind == "case" else _EXAMPLE_FILL
        for cell in routes[routes.max_row]:
            cell.fill = fill
            cell.alignment = Alignment(vertical="top", wrap_text=True)

    issues = workbook.create_sheet("Release Issues")
    issue_headers = [
        "Severity",
        "Code",
        "Phase",
        "Unit ID",
        "Topic",
        "QIDs",
        "BLKs",
        "Message",
        "Full Details",
    ]
    _header(issues, issue_headers)
    for issue in payload.get("issues") or []:
        if not isinstance(issue, Mapping):
            continue
        severity = str(issue.get("severity") or "error")
        issues.append([
            severity,
            issue.get("code", ""),
            issue.get("phase", ""),
            issue.get("unit_id", ""),
            issue.get("topic", ""),
            ", ".join(str(value) for value in issue.get("qids") or []),
            ", ".join(str(value) for value in issue.get("block_ids") or []),
            issue.get("message", ""),
            _cell_text(issue.get("details")),
        ])
        fill = _ERROR_FILL if severity == "error" else _WARNING_FILL
        for cell in issues[issues.max_row]:
            cell.fill = fill
            cell.alignment = Alignment(vertical="top", wrap_text=True)

    manifest = workbook.create_sheet("Release Manifest")
    _header(manifest, ["Field", "Value"])
    manifest_rows = {
        "Release version": payload.get("version"),
        "Released at": payload.get("released_at"),
        "Release reason": payload.get("release_reason"),
        "Job ID": payload.get("job_id"),
        "Learning kind": payload.get("learning_kind"),
        "Source file": payload.get("filename"),
        "Source book": payload.get("source_book"),
        "Target chapter ID": payload.get("target_chapter_id"),
        "Checkpoint stage": payload.get("checkpoint_stage"),
        "Checkpoint progress": payload.get("checkpoint_progress"),
        "Summary": payload.get("summary"),
        "Database publication": (
            "uploaded" if (payload.get("summary") or {}).get("database_uploaded")
            else "not uploaded; use the separate Upload to Database action"
        ),
    }
    for key, value in manifest_rows.items():
        manifest.append([key, _cell_text(value)])
        manifest.cell(manifest.max_row, 1).font = Font(bold=True)
        manifest.cell(manifest.max_row, 2).alignment = Alignment(wrap_text=True, vertical="top")

    for sheet in workbook.worksheets:
        sheet.sheet_view.showGridLines = False
        _fit_columns(sheet)

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def release_payload_bytes(job: models.UploadJob) -> bytes:
    payload = release_payload(job)
    if payload is None:
        raise ValueError("this upload has no staged release")
    return _json_bytes(payload)


def _iter_blocks(value: object) -> Iterable[dict[str, Any]]:
    if isinstance(value, Mapping):
        block_id = str(value.get("block_id") or "")
        if block_id:
            yield dict(value)
        for raw in value.values():
            yield from _iter_blocks(raw)
    elif isinstance(value, (list, tuple)):
        for raw in value:
            yield from _iter_blocks(raw)


def _add_block_reference(
    blocks: dict[str, dict[str, Any]],
    block_id: object,
    *,
    reference: str,
) -> None:
    value = str(block_id or "").strip()
    if not value:
        return
    current = blocks.setdefault(value, {
        "block_id": value,
        "record_status": "referenced_id_only",
        "references": [],
    })
    references = current.setdefault("references", [])
    if isinstance(references, list) and reference not in references:
        references.append(reference)


def _index_string_block_references(
    blocks: dict[str, dict[str, Any]],
    payload: Mapping[str, Any],
) -> None:
    for index, record in enumerate(payload.get("records") or [], start=1):
        if not isinstance(record, Mapping):
            continue
        for block_id in record.get(RELEASE_ROW_BLOCKS_FIELD) or []:
            _add_block_reference(
                blocks,
                block_id,
                reference=f"released_record:{index}",
            )
    for index, issue in enumerate(payload.get("issues") or [], start=1):
        if not isinstance(issue, Mapping):
            continue
        for block_id in issue.get("block_ids") or []:
            _add_block_reference(
                blocks,
                block_id,
                reference=f"release_issue:{index}",
            )
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    for block_id in sorted(set(_BLOCK_ID_RE.findall(raw))):
        _add_block_reference(
            blocks,
            block_id,
            reference="release_payload_text",
        )


def _artifact_directory(job: models.UploadJob) -> Path | None:
    helper = getattr(uploads, "source_artifact_directory", None)
    if not callable(helper) or not getattr(job, "id", None):
        return None
    try:
        # The helper is keyed by job id, not by the job row.  Passing the row
        # raised inside this guard, so every export silently shipped without
        # the canonical artifacts the README promises -- including the Phase
        # 3.1/3.2 caches that explain a failure repeating across resumes.
        path = Path(helper(int(job.id))).resolve()
    except Exception:
        return None
    return path if path.is_dir() else None


def _original_source(job: models.UploadJob) -> Path | None:
    try:
        path = Path(uploads.upload_file_path(job)).resolve()
    except Exception:
        return None
    return path if path.is_file() else None


def build_diagnostics_zip(job: models.UploadJob) -> bytes:
    payload = release_payload(job)
    if payload is None:
        raise ValueError("this upload has no staged release")
    release_workbook = build_release_workbook(job)
    source_manifest = (
        uploads.source_artifact_manifest(job)
        if callable(getattr(uploads, "source_artifact_manifest", None))
        else {}
    )
    blocks: dict[str, dict[str, Any]] = {}
    for source in (
        payload,
        job.generation_checkpoint or {},
        job.question_inventory or {},
        source_manifest,
    ):
        for block in _iter_blocks(source):
            block_id = str(block.get("block_id") or "")
            if block_id:
                blocks.setdefault(block_id, block)
    _index_string_block_references(blocks, payload)

    run_report = concept_run_report.build_run_report(
        payload,
        generation_log=job.generation_log or [],
        generation_checkpoint=job.generation_checkpoint or {},
    )

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "README.txt",
            (
                "Project Aegis diagnostic context export\n\n"
                "Start with RUN_REPORT.txt: it states what stopped the run, "
                "why the orchestration boundary did or did not recover from "
                "it, which rows the failure implicates, and whether the same "
                "failure repeated across resumes. context/run_report.json "
                "carries the same facts with exact log indexes.\n\n"
                "The rest of the archive keeps the released workbook beside "
                "the complete saved generation log, checkpoint, source "
                "evidence, BLK index, Question/Task Inventory, Type/Case "
                "routing, original upload, and canonical-source artifacts. "
                "The release is not proof that every highlighted row is "
                "error-free. See Release Issues.\n"
            ),
        )
        archive.writestr(
            "RUN_REPORT.txt",
            concept_run_report.render_run_report(run_report).encode("utf-8"),
        )
        archive.writestr("context/run_report.json", _json_bytes(run_report))
        archive.writestr("release/released_concepts.xlsx", release_workbook)
        archive.writestr("release/release_payload.json", _json_bytes(payload))
        archive.writestr(
            "context/generation_log.json",
            _json_bytes(job.generation_log or []),
        )
        archive.writestr(
            "context/generation_checkpoint.json",
            _json_bytes(job.generation_checkpoint or {}),
        )
        archive.writestr(
            "context/question_inventory.json",
            _json_bytes(job.question_inventory or {}),
        )
        archive.writestr(
            "context/source_artifact_manifest.json",
            _json_bytes(source_manifest),
        )
        archive.writestr("context/blks.json", _json_bytes({
            "count": len(blocks),
            "blocks": [blocks[key] for key in sorted(blocks)],
        }))
        archive.writestr(
            "context/source_evidence.json",
            _json_bytes({
                "pending_decision": payload.get("pending_decision_snapshot") or {},
                "issues": payload.get("issues") or [],
                "type_case_rows": payload.get("type_case_rows") or [],
            }),
        )
        archive.writestr(
            "source/converted_source.mmd",
            str(job.mmd_text or "").encode("utf-8"),
        )

        original = _original_source(job)
        if original is not None:
            archive.write(original, f"source/original/{original.name}")
        directory = _artifact_directory(job)
        if directory is not None:
            for path in sorted(directory.rglob("*")):
                if path.is_file():
                    archive.write(
                        path,
                        "source/canonical_artifacts/"
                        + str(path.relative_to(directory)).replace("\\", "/"),
                    )
    return buffer.getvalue()


def release_artifact_entries(job: models.UploadJob) -> list[dict[str, Any]]:
    payload = release_payload(job)
    if payload is None:
        return []
    workbook = build_release_workbook(job)
    diagnostics = build_diagnostics_zip(job)
    raw_payload = release_payload_bytes(job)
    return [
        {
            "kind": "released_concepts",
            "label": "Download released output",
            "filename": f"{_safe_filename(job.filename, 'concepts')}_released.xlsx",
            "media_type": (
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
            "size_bytes": len(workbook),
            "download_url": f"/build-concepts/uploads/{job.id}/release.xlsx",
            "action": "download",
        },
        {
            "kind": "release_diagnostics",
            "label": "Export full issue context",
            "filename": f"{_safe_filename(job.filename, 'concepts')}_diagnostics.zip",
            "media_type": "application/zip",
            "size_bytes": len(diagnostics),
            "download_url": f"/build-concepts/uploads/{job.id}/diagnostics.zip",
            "action": "download",
        },
        {
            "kind": "release_payload",
            "label": "Download release JSON",
            "filename": f"{_safe_filename(job.filename, 'concepts')}_release.json",
            "media_type": "application/json",
            "size_bytes": len(raw_payload),
            "download_url": f"/build-concepts/uploads/{job.id}/release.json",
            "action": "download",
        },
        {
            "kind": "database_upload",
            "label": (
                "Already uploaded to database"
                if (payload.get("summary") or {}).get("database_uploaded")
                else "Upload released output to database"
            ),
            "filename": "",
            "media_type": "application/json",
            "size_bytes": 0,
            "download_url": f"/build-concepts/uploads/{job.id}/upload-release",
            "action": "post",
            "disabled": bool(
                (payload.get("summary") or {}).get("database_uploaded")
            ),
            "requires_confirmation": True,
        },
    ]

"""Install the Phase 3 semantic-graph contract after Phase 2.2.1.

This contract keeps the Phase 2 ACSD as immutable evidence, materializes an
optional semantic graph artifact before paid concept calls, and carries stable
source IDs into the Question / Task Inventory and mined Types.  Later Phase 3
batches consume those IDs for concept extraction and Type-host allocation.
"""
from __future__ import annotations

import copy
import json
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from pathlib import Path
from types import ModuleType
from typing import Any

from . import canonical_source
from . import canonical_source_phase2 as phase2
from . import canonical_source_phase22 as phase22
from . import canonical_source_phase3 as phase3

_CONTRACT_VERSION = 1
_ACTIVE_JOB: ContextVar[Any | None] = ContextVar(
    "aegis_phase3_active_upload_job", default=None
)

OPTIONAL_ARTIFACT_SPECS: dict[str, dict[str, str]] = {
    "semantic_graph": {
        "filename": "source.semantic-graph.json",
        "media_type": "application/json; charset=utf-8",
        "label": "Aegis Phase 3 semantic source graph",
    },
    "semantic_graph_report": {
        "filename": "source.semantic-graph-report.json",
        "media_type": "application/json; charset=utf-8",
        "label": "Phase 3 semantic graph validation report",
    },
}


def _json_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def _graph_report(graph: dict[str, Any]) -> dict[str, Any]:
    validation = graph.get("validation") if isinstance(graph.get("validation"), dict) else {}
    topics = [row for row in graph.get("topics") or [] if isinstance(row, dict)]
    subtopics = [row for row in graph.get("subtopics") or [] if isinstance(row, dict)]
    tasks = [row for row in graph.get("tasks") or [] if isinstance(row, dict)]
    blocks = [row for row in graph.get("blocks") or [] if isinstance(row, dict)]
    figures = [row for row in graph.get("figures") or [] if isinstance(row, dict)]
    return {
        "phase": phase3.PHASE,
        "version": phase3.VERSION,
        "compiler_version": phase3.COMPILER_VERSION,
        "status": str(graph.get("status") or "unavailable"),
        "source_contract_sha256": str(graph.get("source_contract_sha256") or ""),
        "graph_sha256": str(graph.get("graph_sha256") or ""),
        "subject_adapter": str(graph.get("subject_adapter") or "general"),
        "errors": copy.deepcopy(validation.get("errors") or []),
        "warnings": copy.deepcopy(validation.get("warnings") or []),
        "summary": {
            "topics": len(topics),
            "subtopics": len(subtopics),
            "blocks": len(blocks),
            "tasks": len(tasks),
            "figures": len(figures),
            "chapter_wide_tasks": sum(1 for row in tasks if row.get("chapter_wide")),
            "source_order_locked": bool(graph.get("source_order_locked")),
        },
    }


def write_graph_artifacts(directory: Path, graph: dict[str, Any]) -> dict[str, Any]:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    canonical_source._atomic_write(
        directory / OPTIONAL_ARTIFACT_SPECS["semantic_graph"]["filename"],
        _json_text(graph),
    )
    report = _graph_report(graph)
    canonical_source._atomic_write(
        directory / OPTIONAL_ARTIFACT_SPECS["semantic_graph_report"]["filename"],
        _json_text(report),
    )
    return report


def optional_artifact_manifest(directory: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    directory = Path(directory)
    files: list[dict[str, Any]] = []
    for kind, spec in OPTIONAL_ARTIFACT_SPECS.items():
        path = directory / spec["filename"]
        if not path.exists() or not path.is_file():
            continue
        files.append({
            "kind": kind,
            "label": spec["label"],
            "filename": spec["filename"],
            "media_type": spec["media_type"],
            "size_bytes": path.stat().st_size,
        })
    report_path = directory / OPTIONAL_ARTIFACT_SPECS["semantic_graph_report"]["filename"]
    report: dict[str, Any] = {}
    if report_path.exists():
        try:
            value = json.loads(report_path.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                report = value
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            report = {}
    return files, report


def optional_artifact_path(directory: Path, kind: str) -> tuple[Path, dict[str, str]]:
    spec = OPTIONAL_ARTIFACT_SPECS.get(str(kind or ""))
    if spec is None:
        raise ValueError("unknown Phase 3 source artifact")
    path = Path(directory) / spec["filename"]
    if not path.exists() or not path.is_file():
        raise ValueError("Phase 3 source artifact is not available for this upload")
    return path, spec


def _metadata_dict(metadata: dict[str, object] | None) -> dict[str, str]:
    return {
        str(key): str(value or "")
        for key, value in (metadata or {}).items()
        if str(key).strip()
    }


@contextmanager
def activate_job(job: Any):
    token = _ACTIVE_JOB.set(job)
    try:
        yield
    finally:
        _ACTIVE_JOB.reset(token)


def active_job() -> Any | None:
    return _ACTIVE_JOB.get()


def _generation_metadata(kwargs: dict[str, Any]) -> dict[str, str]:
    return _metadata_dict({
        "board": kwargs.get("board"),
        "grade": kwargs.get("grade"),
        "subject": kwargs.get("subject"),
        "unit": kwargs.get("unit"),
        "chapter_title": kwargs.get("chapter_title"),
        "chapter_id": kwargs.get("chapter_id"),
        "chapter_code": kwargs.get("chapter_code"),
        "learning_kind": kwargs.get("learning_kind"),
    })


def prepare_job_graph(job: Any, *, metadata: dict[str, object] | None = None) -> dict[str, Any]:
    """Build and persist the semantic graph before paid generation begins."""
    from . import progress, uploads

    canonical = phase2.active_canonical()
    if canonical is None:
        canonical, _report = phase2._load_or_refresh_for_job(job)
    if not isinstance(canonical, dict) or not canonical:
        raise ValueError("Phase 3 requires an active verified Phase 2 canonical source")

    effective_metadata = _metadata_dict(metadata)
    semantic_source = phase22.active_semantic_source(str(job.mmd_text or ""))
    graph = phase3.build_semantic_graph(
        canonical,
        semantic_source,
        metadata=effective_metadata,
    )
    validation = graph.get("validation") or {}
    errors = validation.get("errors") or []
    if errors:
        sample = "; ".join(
            f"{item.get('code')}: {item.get('message')}"
            for item in errors[:5]
            if isinstance(item, dict)
        )
        raise ValueError(
            "Phase 3 semantic source graph validation blocked concept generation "
            f"before paid semantic calls ({len(errors)} issue(s)): {sample}"
        )

    report = write_graph_artifacts(
        uploads.source_artifact_directory(int(job.id)), graph
    )
    summary = report.get("summary") or {}
    progress.log(
        "Phase 3 semantic source graph active: "
        f"{summary.get('topics', 0)} topic(s), "
        f"{summary.get('subtopics', 0)} subtopic(s), "
        f"{summary.get('tasks', 0)} stable task(s); concept and Type stages "
        "can now use immutable graph identities instead of heading-string equality.",
        level="success",
    )
    return graph


def install(generation: ModuleType | None = None) -> None:
    """Attach Phase 3 IDs and optional artifacts after all Phase 2 contracts."""
    from . import uploads

    if getattr(phase2, "_PHASE3_CONTRACT_VERSION", 0) >= _CONTRACT_VERSION:
        return

    original_manifest = phase2.artifact_manifest
    original_download = uploads.source_artifact_download
    original_run = uploads.run_with_openai_usage
    original_concepts = generation.concepts_from_mmd if generation is not None else None

    @wraps(original_manifest)
    def artifact_manifest(directory: Path):
        manifest = original_manifest(directory)
        optional_files, report = optional_artifact_manifest(directory)
        existing = {str(item.get("kind") or "") for item in manifest.get("files") or []}
        for item in optional_files:
            if item["kind"] not in existing:
                manifest.setdefault("files", []).append(item)
        manifest["semantic_graph"] = (
            copy.deepcopy(report) if report else {
                "phase": phase3.PHASE,
                "version": phase3.VERSION,
                "status": "not_built",
                "summary": {},
            }
        )
        return manifest

    def source_artifact_download(job: Any, kind: str):
        try:
            return original_download(job, kind)
        except ValueError:
            return optional_artifact_path(
                uploads.source_artifact_directory(int(job.id)), kind
            )

    @wraps(original_run)
    def run_with_openai_usage(
        db,
        job_id: int,
        fn,
        *,
        owner_sub: str | None = None,
    ):
        job = uploads.get_job(db, job_id, owner_sub=owner_sub)
        if str(getattr(job, "module", "")) != "build_concepts":
            return original_run(
                db, job_id, fn, owner_sub=owner_sub
            )

        def phase3_job_fn():
            current = uploads.get_job(
                db, job_id, owner_sub=owner_sub, module="build_concepts"
            )
            with activate_job(current):
                return fn()

        return original_run(
            db, job_id, phase3_job_fn, owner_sub=owner_sub
        )

    phase2.artifact_manifest = artifact_manifest
    uploads.source_artifact_download = source_artifact_download
    uploads.run_with_openai_usage = run_with_openai_usage

    if generation is not None:
        original_inventory = generation._extract_question_task_inventory_via_api
        original_refresh = generation._refresh_inventory_from_source_anchors
        original_consolidate = generation._consolidate_semantic_types_via_api
        original_reconcile = generation._reconcile_resumed_mined_types

        @wraps(original_inventory)
        def extract_inventory(*args, **kwargs):
            inventory = original_inventory(*args, **kwargs)
            graph = phase3.active_graph()
            return phase3.annotate_inventory(inventory, graph) if graph else inventory

        @wraps(original_refresh)
        def refresh_inventory(*args, **kwargs):
            inventory = original_refresh(*args, **kwargs)
            graph = phase3.active_graph()
            return phase3.annotate_inventory(inventory, graph) if graph else inventory

        @wraps(original_consolidate)
        def consolidate_types(*args, **kwargs):
            mined = original_consolidate(*args, **kwargs)
            graph = phase3.active_graph()
            return phase3.annotate_mined_types(mined, graph) if graph else mined

        @wraps(original_reconcile)
        def reconcile_types(*args, **kwargs):
            mined = original_reconcile(*args, **kwargs)
            graph = phase3.active_graph()
            return phase3.annotate_mined_types(mined, graph) if graph else mined

        if original_concepts is not None:
            @wraps(original_concepts)
            def concepts_from_mmd(mmd_text: str, *args, **kwargs):
                job = active_job()
                if job is None or phase2.active_canonical() is None:
                    return original_concepts(mmd_text, *args, **kwargs)
                metadata = _generation_metadata(kwargs)
                graph = prepare_job_graph(job, metadata=metadata)
                with (
                    phase3.activate_target_metadata(metadata),
                    phase3.activate(graph),
                ):
                    records = original_concepts(mmd_text, *args, **kwargs)
                    artifacts = kwargs.get("artifacts")
                    if isinstance(artifacts, dict):
                        inventory = artifacts.get("question_task_inventory")
                        if isinstance(inventory, dict):
                            artifacts["question_task_inventory"] = (
                                phase3.annotate_inventory(inventory, graph)
                            )
                        mined = artifacts.get("mined_types")
                        if isinstance(mined, dict):
                            artifacts["mined_types"] = (
                                phase3.annotate_mined_types(mined, graph)
                            )
                    return records

            generation.concepts_from_mmd = concepts_from_mmd

        generation._extract_question_task_inventory_via_api = extract_inventory
        generation._refresh_inventory_from_source_anchors = refresh_inventory
        generation._consolidate_semantic_types_via_api = consolidate_types
        generation._reconcile_resumed_mined_types = reconcile_types
        generation._PHASE3_SEMANTIC_GRAPH_VERSION = _CONTRACT_VERSION

    uploads._CANONICAL_SOURCE_PHASE3_VERSION = _CONTRACT_VERSION
    phase2._PHASE3_CONTRACT_VERSION = _CONTRACT_VERSION

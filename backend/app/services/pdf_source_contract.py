"""Versioned dispatch and durable adoption of the fresh PDF source engine.

Only an untouched staged PDF selects this engine. Its selection precedes paid
work; suspension reuses it. Previously converted or partially read uploads keep
their original reader and every saved downstream decision.
"""
from __future__ import annotations

import copy
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from . import pdf_source_adapter as adapter
from . import pdf_source_evidence as evidence

SELECTION_FILE = "source.pdf-engine-selection.json"
BUNDLE_FILE = "source.pdf-source-bundle.json"
IR_FILE = "source.pdf-source-ir.json"
EVIDENCE_FILE = "source.pdf-evidence.json"
_SPECS = {
    "pdf_source_ir": {"filename": IR_FILE, "label": "Grounded PDF source blocks and relationships", "media_type": "application/json"},
    "pdf_source_bundle": {"filename": BUNDLE_FILE, "label": "Sealed PDF source projection", "media_type": "application/json"},
    "pdf_source_evidence": {"filename": EVIDENCE_FILE, "label": "Original PDF evidence manifest", "media_type": "application/json"},
}


def selected(directory: Path) -> bool:
    return (Path(directory) / SELECTION_FILE).is_file()


def selected_job(job: Any) -> bool:
    from . import uploads
    return selected(uploads.source_artifact_directory(int(job.id)))


def should_dispatch(job: Any, source_path: Path, directory: Path) -> bool:
    if str(getattr(job, "module", "")) != "build_concepts" or source_path.suffix.lower() != ".pdf":
        return False
    if selected(directory):
        return True
    # A historical source is an immutable result, even if conversion is clicked
    # again. The dispatch returns that result without invoking either reader.
    if getattr(job, "mmd_text", ""):
        return True
    if (getattr(job, "status", "") != "uploaded" or getattr(job, "question_inventory", None)
            or getattr(job, "generation_checkpoint", None)):
        return False
    # Old partial conversion files may contain accepted paid decisions. Never
    # silently move one of those requests into a new engine namespace.
    return not Path(directory).exists() or not any(Path(directory).iterdir())


def _read_bundle(directory: Path) -> dict[str, Any]:
    bundle = evidence.read_json(Path(directory) / BUNDLE_FILE)
    seal = bundle.get("bundle_sha256")
    if seal != evidence.digest_json({key: value for key, value in bundle.items() if key != "bundle_sha256"}):
        raise ValueError("Saved PDF source projection failed its integrity check")
    if not adapter.is_canonical(bundle.get("canonical")):
        raise ValueError("Saved PDF source projection has an unsupported version")
    if bundle["canonical"]["source_contract"]["source_sha256"] != adapter.text_sha256(bundle["mmd_text"]):
        raise ValueError("Saved PDF source view does not match its canonical contract")
    return bundle


def convert_pdf(path: Path, *, job_id: int, artifact_dir: Path) -> dict[str, Any]:
    """Return the existing conversion bundle shape, without parsing its view."""
    from .pdf_source_engine import read_pdf

    path, artifact_dir = Path(path), Path(artifact_dir)
    pdf_hash = evidence.file_sha256(path)
    selection_path = artifact_dir / SELECTION_FILE
    if selection_path.exists():
        selection = evidence.read_json(selection_path)
        if selection != {"engine_version": adapter.ENGINE_VERSION, "pdf_sha256": pdf_hash, "job_id": int(job_id)}:
            raise ValueError("The PDF differs from this upload's frozen source-engine selection")
    else:
        evidence.atomic_json(selection_path, {"engine_version": adapter.ENGINE_VERSION,
                                             "pdf_sha256": pdf_hash, "job_id": int(job_id)})
    if (artifact_dir / BUNDLE_FILE).is_file():
        bundle = _read_bundle(artifact_dir)
        if bundle["canonical"]["document"]["pdf_sha256"] != pdf_hash:
            raise ValueError("Saved PDF source belongs to different original bytes")
        _write_views(artifact_dir, bundle)
        return bundle
    document = read_pdf(path, job_id=job_id, artifact_dir=artifact_dir)
    manifest = evidence.read_json(artifact_dir / EVIDENCE_FILE)
    bundle = adapter.project_document(document, source_filename=path.name, job_id=job_id, evidence=manifest)
    bundle["document"] = document
    bundle["reconstruction"] = {
        "version": adapter.ENGINE_VERSION, "source_origin": adapter.ENGINE_VERSION,
        "pdf_sha256": pdf_hash, "status": document["status"],
        "page_count": len(document["pages"]), "asset_count": len(bundle["canonical"]["images"]),
    }
    # A single sealed bundle is authoritative. Conventional files are views for
    # existing authenticated downloads and may be reconstructed without a call.
    bundle["bundle_sha256"] = evidence.digest_json(bundle)
    evidence.atomic_json(artifact_dir / BUNDLE_FILE, bundle)
    _write_views(artifact_dir, bundle)
    return bundle


def _write_views(directory: Path, bundle: dict[str, Any]) -> None:
    from .canonical_source import ARTIFACT_SPECS
    evidence.atomic_json(directory / IR_FILE, bundle["document"])
    for kind, value in (("canonical_json", bundle["canonical"]), ("report", bundle["report"])):
        evidence.atomic_json(directory / ARTIFACT_SPECS[kind]["filename"], value)
    for kind in ("raw_mmd", "aegis_mmd"):
        evidence.atomic_bytes(directory / ARTIFACT_SPECS[kind]["filename"], bundle["mmd_text"].encode("utf-8"))


def load_for_job(job: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    from . import uploads
    directory = uploads.source_artifact_directory(int(job.id))
    bundle = _read_bundle(directory)
    selection = evidence.read_json(directory / SELECTION_FILE)
    pdf_hash = bundle["canonical"]["document"]["pdf_sha256"]
    if (selection.get("job_id") != int(job.id) or selection.get("pdf_sha256") != pdf_hash
            or selection.get("engine_version") != adapter.ENGINE_VERSION):
        raise ValueError("Saved PDF source selection does not match this upload")
    if str(getattr(job, "mmd_text", "") or "") != bundle["mmd_text"]:
        raise ValueError("The upload's source view differs from its frozen PDF projection")
    if evidence.file_sha256(uploads.upload_file_path(job)) != pdf_hash:
        raise ValueError("The original PDF changed after source conversion")
    return copy.deepcopy(bundle["canonical"]), copy.deepcopy(bundle["report"])


def load_page_evidence(source_path: Path, directory: Path, canonical: dict[str, Any]) -> dict[str, Any]:
    bundle = _read_bundle(directory)
    if (bundle["canonical"] != canonical
            or evidence.file_sha256(source_path) != bundle["document"]["pdf_sha256"]):
        raise ValueError("PDF page evidence does not match the frozen canonical source")
    return adapter.page_evidence(bundle["document"], canonical)


def _result(job: Any) -> dict[str, Any]:
    from . import uploads
    return {"job_id": int(job.id), "status": job.status, "filename": job.filename,
            "mmd_chars": len(job.mmd_text or ""), "mmd_text": job.mmd_text,
            "source_artifacts": uploads.source_artifact_manifest(job),
            "openai_usage": job.openai_usage or {}}


def convert_job(db: Any, job_id: int, *, owner_sub: str | None = None, module: str = "") -> dict[str, Any]:
    from . import generation_recovery, model_routing_run, openai_usage, progress, uploads
    with uploads.exclusive_job_operation(job_id):
        job = uploads.get_job(db, job_id, owner_sub=owner_sub, module=module)
        db.refresh(job)
        # Source identity cannot be replaced by a repeat conversion request.
        if job.mmd_text:
            return _result(job)
        generation_recovery.require_mutation_allowed(job, operation="convert this upload")
        if job.status != "uploaded" or job.question_inventory or job.generation_checkpoint:
            raise ValueError("This upload already has downstream work; use a new upload for a new PDF source")
        persisted = copy.deepcopy(job.openai_usage if isinstance(job.openai_usage, dict) else {})
        persistence_key = f"upload-job:{job_id}"
        progress.step("Parse source document")
        summary = persisted
        try:
            with model_routing_run.bind_job(job), (nullcontext() if openai_usage.is_tracking() else openai_usage.track()):
                openai_usage.bind_persisted_summary(persistence_key, persisted)
                try:
                    bundle = convert_pdf(uploads.upload_file_path(job), job_id=job_id,
                                         artifact_dir=uploads.source_artifact_directory(job_id))
                finally:
                    summary = openai_usage.cumulative_summary(persisted, persistence_key=persistence_key)
        finally:
            # RunDeferred/RunSuspended derive from BaseException. Paid receipts
            # must survive these too, and those control signals propagate intact.
            job.openai_usage = summary
            db.commit()
            db.refresh(job)
            progress.usage(summary)
        job.mmd_text = bundle["mmd_text"]
        job.status = "converted"
        job.detail = (f"PDF source preserved and read ({bundle['reconstruction']['page_count']} pages; "
                      f"{bundle['reconstruction']['status']}).")
        db.commit()
        db.refresh(job)
        progress.set_progress(1.0, label="PDF source converted")
        return {**_result(job), "conversion_source": adapter.ENGINE_VERSION}


def _artifact_specs(directory: Path) -> dict[str, dict[str, Any]]:
    from .canonical_source import ARTIFACT_SPECS
    specs = {**copy.deepcopy(ARTIFACT_SPECS), **copy.deepcopy(_SPECS)}
    manifest_path = directory / EVIDENCE_FILE
    if manifest_path.is_file():
        manifest = evidence.read_json(manifest_path)
        root = Path(manifest["root"])
        # Every path is rooted in this authenticated upload's evidence directory.
        if not root.resolve().is_relative_to(directory.resolve()):
            raise ValueError("PDF evidence directory is outside this upload")
        def add(kind, filename, label, media_type):
            path = evidence._private_path(root, filename)
            specs[kind] = {"filename": path.name, "label": label, "media_type": media_type, "path": str(path)}
        add("pdf_original", manifest["original_path"], "Original uploaded PDF", "application/pdf")
        for page in manifest["pages"]:
            number = int(page["page_number"])
            add(f"pdf_page_{number:04d}", page["image_path"], f"Original page {number} image", "image/jpeg")
            add(f"pdf_native_{number:04d}", page["native_text_path"], f"Page {number} native positioned text", "application/json")
    return specs


def artifact_manifest(directory: Path) -> dict[str, Any]:
    directory = Path(directory)
    report = _read_bundle(directory)["report"] if (directory / BUNDLE_FILE).is_file() else {}
    files = []
    for kind, spec in _artifact_specs(directory).items():
        path = Path(spec.get("path") or directory / spec["filename"])
        if path.is_file():
            files.append({"kind": kind, **{key: value for key, value in spec.items() if key != "path"},
                          "size_bytes": path.stat().st_size})
    return {"available": bool(report), "shadow_mode": False, "used_for_generation": bool(report),
            "phase": "pdf-source", "status": report.get("status", "reading"),
            "schema_version": "1.1.0", "compiler_version": adapter.ADAPTER_VERSION,
            "phase2_inventory_ready": bool(report), "source_sha256": report.get("source_sha256", ""),
            "summary": report.get("summary", {}), "source_review": report.get("source_review", {}), "files": files}


def artifact_path(directory: Path, kind: str) -> tuple[Path, dict[str, Any]]:
    directory = Path(directory)
    spec = _artifact_specs(directory).get(kind)
    if spec is None:
        raise ValueError("Unknown PDF source artifact")
    path = Path(spec.get("path") or directory / spec["filename"])
    if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(directory.resolve()):
        raise ValueError("PDF source artifact is unavailable")
    return path, {key: value for key, value in spec.items() if key != "path"}

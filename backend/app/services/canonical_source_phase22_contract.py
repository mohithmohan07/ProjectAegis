"""Install Phase 2.2 source adjudication after every deterministic source gate."""
from __future__ import annotations

import copy
from functools import wraps
from pathlib import Path
from types import ModuleType
from typing import Any

from . import canonical_source
from . import canonical_source_phase2 as phase2
from . import canonical_source_phase211_contract as phase211
from . import canonical_source_phase22 as phase22

_CONTRACT_VERSION = 1


def _phase22_current(canonical: dict[str, Any] | None) -> bool:
    marker = (canonical or {}).get("source_adjudication")
    return bool(
        isinstance(marker, dict)
        and marker.get("version") == phase22.ADJUDICATION_VERSION
    )


def install(generation: ModuleType | None = None) -> None:
    """Activate bounded PDF-backed adjudication at generation start only."""
    if getattr(phase2, "_PHASE22_CONTRACT_VERSION", 0) >= _CONTRACT_VERSION:
        return

    from . import progress, uploads

    original_compile = phase2.compile_phase2_source
    original_manifest = phase2.artifact_manifest
    original_concepts = generation.concepts_from_mmd if generation is not None else None

    @wraps(original_compile)
    def compile_phase2_source(*args, **kwargs):
        compiled = original_compile(*args, **kwargs)
        canonical, report = phase22.annotate_pending_adjudication(
            compiled.canonical, compiled.report
        )
        return canonical_source.CompiledSource(
            canonical=canonical,
            aegis_mmd=compiled.aegis_mmd,
            report=report,
        )

    @wraps(original_manifest)
    def artifact_manifest(directory: Path):
        manifest = original_manifest(directory)
        report_path = (
            Path(directory)
            / canonical_source.ARTIFACT_SPECS["report"]["filename"]
        )
        try:
            report = phase2._read_json(report_path) if report_path.exists() else {}
        except ValueError:
            report = {}
        adjudication = report.get("source_adjudication")
        manifest["source_adjudication"] = (
            copy.deepcopy(adjudication) if isinstance(adjudication, dict) else {
                "version": phase22.ADJUDICATION_VERSION,
                "status": "unavailable",
                "packet_count": 0,
            }
        )
        return manifest

    def _reload_phase22_artifacts(job: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        directory = uploads.source_artifact_directory(int(job.id))
        phase2.write_phase2_artifacts(
            directory,
            mmd_text=str(job.mmd_text or ""),
            source_filename=str(job.filename or "source.mmd"),
            consumer_module="build_concepts",
        )
        return (
            phase2._read_json(
                directory
                / canonical_source.ARTIFACT_SPECS["canonical_json"]["filename"]
            ),
            phase2._read_json(
                directory / canonical_source.ARTIFACT_SPECS["report"]["filename"]
            ),
        )

    def prepare_job_context(db: Any, job: Any) -> dict[str, Any]:
        canonical, report = phase2._load_or_refresh_for_job(job)
        if not _phase22_current(canonical):
            canonical, report = _reload_phase22_artifacts(job)

        issues = phase2.phase2_inventory_issues(canonical, report)
        eligible = [
            issue for issue in issues
            if issue.get("code") in phase22.ELIGIBLE_ISSUE_CODES
        ]
        if issues and eligible:
            if not config_source_adjudication_enabled():
                progress.log(
                    "Phase 2.2 source adjudication is disabled by configuration; "
                    "leaving the deterministic source gate closed.",
                    level="warning",
                )
            elif not config_has_openai():
                progress.log(
                    "Phase 2.2 requires OPENAI_API_KEY to inspect unresolved "
                    "original-document evidence; leaving the source gate closed.",
                    level="warning",
                )
            else:
                canonical, report, _ready = phase22.adjudicate_job_source(
                    db, job, canonical, report
                )
                issues = phase2.phase2_inventory_issues(canonical, report)

        if issues:
            sample = "; ".join(
                phase211.format_issue_for_ui(item) for item in issues[:5]
            )
            adjudication = canonical.get("source_adjudication") or {}
            status = str(adjudication.get("status") or "not_run")
            raise ValueError(
                "Phase 2.2 canonical source adjudication could not verify every "
                "source-critical issue before concept generation "
                f"({len(issues)} issue(s), adjudication={status}): {sample}"
            )

        phase2.prune_legacy_inventory_checkpoints(db, job)
        marker = canonical.get("source_adjudication") or {}
        repairs = int(marker.get("verified_repairs") or 0)
        semantic = phase22.semantic_source(canonical, str(job.mmd_text or ""))
        message = (
            "Phase 2.2 source contract active: deterministic ACSD order, QIDs, "
            "Figures, images, KaTeX, and exact source inventory are retained; "
            f"{repairs} verified original-document overlay(s) are included in "
            "semantic concept extraction."
            if repairs
            else
            "Phase 2.2 source contract active: deterministic ACSD order, QIDs, "
            "Figures, images, KaTeX, and exact source inventory are verified; "
            "no semantic source overlay was required."
        )
        progress.log(message, level="success")
        marker["semantic_source_sha256"] = phase22._sha256_text(semantic)
        canonical["source_adjudication"] = marker
        return canonical

    def config_source_adjudication_enabled() -> bool:
        value = str(
            __import__("os").environ.get("AEGIS_SOURCE_ADJUDICATION_ENABLED", "1")
        ).strip().lower()
        return value not in {"0", "false", "no", "off"}

    def config_has_openai() -> bool:
        from .. import config

        return config.use_live_generation()

    phase2.compile_phase2_source = compile_phase2_source
    phase2.artifact_manifest = artifact_manifest
    phase2.prepare_job_context = prepare_job_context

    if generation is not None and original_concepts is not None:
        @wraps(original_concepts)
        def concepts_from_mmd(mmd_text: str, *args, **kwargs):
            return original_concepts(
                phase22.active_semantic_source(mmd_text), *args, **kwargs
            )

        generation.concepts_from_mmd = concepts_from_mmd
        generation._PHASE22_SOURCE_ADJUDICATION_VERSION = _CONTRACT_VERSION

    phase2._PHASE22_CONTRACT_VERSION = _CONTRACT_VERSION

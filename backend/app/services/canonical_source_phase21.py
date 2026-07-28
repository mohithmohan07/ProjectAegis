"""Phase 2.1 source hardening orchestration for Build Concepts."""
from __future__ import annotations

import copy
import json
from typing import Any

from . import canonical_source
from . import canonical_source_phase21_structure as structure
from . import canonical_source_phase21_visuals as visuals

HARDENING_VERSION = "2.1.0"
COMPILER_LABEL = "phase-2.1-source-hardening-1"


def dedupe_issues(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for issue in issues:
        key = json.dumps(issue, ensure_ascii=False, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        unique.append(issue)
    return unique


def source_boundary_issues(canonical: dict[str, Any]) -> list[dict[str, Any]]:
    return dedupe_issues([
        *structure.section_integrity_issues(canonical),
        *structure.task_boundary_issues(canonical),
        *visuals.orphan_figure_issues(canonical),
    ])


def harden_compiled_source(
    compiled: canonical_source.CompiledSource,
    *,
    source: str,
    consumer_module: str,
    base_issue_reader,
) -> canonical_source.CompiledSource:
    """Apply deterministic Phase 2.1 repairs, then recalculate fail-closed status."""
    canonical = copy.deepcopy(compiled.canonical)
    report = copy.deepcopy(compiled.report)
    active = str(consumer_module or "") == "build_concepts"

    recovered = structure.recover_plain_task_cues(canonical)
    boundary_repairs = structure.trim_task_boundaries(canonical)
    structure.renumber_tasks(canonical)
    visual_repairs = visuals.normalize_visual_ownership(canonical)
    context_links = visuals.link_shared_context(canonical)
    structure.renumber_tasks(canonical)

    source_issues = source_boundary_issues(canonical)
    canonical["phase21_hardening"] = {
        "version": HARDENING_VERSION,
        "compiler": COMPILER_LABEL,
        "plain_task_cues_recovered": recovered,
        "task_boundaries_repaired": boundary_repairs,
        "visual_ownership_rows_normalized": visual_repairs,
        "shared_context_links": context_links,
        "blocking_issues": len(source_issues),
    }
    canonical["phase21_issues"] = source_issues
    source_contract = canonical.setdefault("source_contract", {})
    source_contract["hardening_version"] = HARDENING_VERSION
    source_contract["hardening_compiler"] = COMPILER_LABEL
    source_contract["task_count"] = len(canonical.get("tasks") or [])
    shadow = canonical.setdefault("shadow_validation", {})
    shadow["phase21_blocking_issues"] = len(source_issues)

    base_issues = base_issue_reader(canonical, report)
    all_issues = dedupe_issues([*base_issues, *source_issues])
    ready = active and not all_issues
    canonical["phase2_inventory_ready"] = ready
    shadow["phase2_inventory_ready"] = ready
    shadow["phase2_blocking_issues"] = len(all_issues)

    report.update({
        "phase21_hardening": copy.deepcopy(canonical["phase21_hardening"]),
        "phase21_issues": copy.deepcopy(source_issues),
        "phase2_issues": copy.deepcopy(all_issues),
        "phase2_inventory_ready": ready,
    })
    summary = report.setdefault("summary", {})
    summary["tasks"] = len(canonical.get("tasks") or [])
    summary["phase21_blocking_issues"] = len(source_issues)
    summary["phase2_blocking_issues"] = len(all_issues)
    if active and all_issues:
        report["status"] = "failed"

    aegis_mmd = canonical_source._render_aegis_mmd(canonical)
    aegis_mmd = aegis_mmd.replace(
        f"<!-- schema_version: {canonical_source.SCHEMA_VERSION} -->",
        f"<!-- schema_version: {canonical.get('schema_version') or canonical_source.SCHEMA_VERSION} -->",
        1,
    ).replace(
        f"<!-- compiler_version: {canonical_source.COMPILER_VERSION} -->",
        f"<!-- compiler_version: {canonical.get('compiler_version') or canonical_source.COMPILER_VERSION} -->",
        1,
    )
    if active:
        aegis_mmd = aegis_mmd.replace(
            "AEGIS CANONICAL SOURCE SHADOW",
            "AEGIS CANONICAL SOURCE PHASE 2.1",
            1,
        ).replace(
            "<!-- used_for_generation: false -->",
            "<!-- used_for_generation: source-critical -->",
            1,
        )
    return canonical_source.CompiledSource(
        canonical=canonical,
        aegis_mmd=aegis_mmd,
        report=report,
    )


def phase21_inventory_issues(canonical: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(canonical.get("phase21_hardening"), dict):
        return []
    return dedupe_issues([
        copy.deepcopy(issue)
        for issue in canonical.get("phase21_issues") or []
        if isinstance(issue, dict)
    ])

"""Phase 2.1 source hardening orchestration for Build Concepts."""
from __future__ import annotations

import copy
import json
from typing import Any

from . import canonical_source
from . import canonical_source_phase21_structure as structure
from . import canonical_source_phase21_visuals as visuals

HARDENING_VERSION = "2.1.3"
COMPILER_LABEL = "phase-2.1-source-leaf-inventory-2"


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
        *structure.inventory_contract_issues(canonical),
        *visuals.orphan_figure_issues(canonical),
    ])


def hardening_artifact_valid(
    canonical: dict[str, Any] | None,
    report: dict[str, Any] | None = None,
) -> bool:
    """Whether a persisted artifact proves the current leaf inventory shape."""
    if not isinstance(canonical, dict):
        return False
    marker = canonical.get("phase21_hardening")
    source_contract = canonical.get("source_contract")
    if not (
        isinstance(marker, dict)
        and marker.get("version") == HARDENING_VERSION
        and marker.get("compiler") == COMPILER_LABEL
        and marker.get("patch_version") == HARDENING_VERSION
        and isinstance(source_contract, dict)
        and source_contract.get("hardening_version") == HARDENING_VERSION
        and source_contract.get("hardening_compiler") == COMPILER_LABEL
        and source_contract.get("hardening_patch") == HARDENING_VERSION
    ):
        return False
    parent_count, decomposed_count, inventory_count = structure.inventory_shape(
        canonical
    )
    expected = {
        "parent_task_count": parent_count,
        "decomposed_parent_task_count": decomposed_count,
        "inventory_item_count": inventory_count,
    }
    for container in (marker, source_contract):
        for field, value in expected.items():
            try:
                if int(container.get(field)) != value:
                    return False
            except (TypeError, ValueError):
                return False
    if structure.inventory_contract_issues(canonical):
        return False
    phase21_issues = [
        issue for issue in canonical.get("phase21_issues") or []
        if isinstance(issue, dict)
    ]
    try:
        if int(marker.get("blocking_issues")) != len(phase21_issues):
            return False
    except (TypeError, ValueError):
        return False
    if isinstance(report, dict):
        report_marker = report.get("phase21_hardening")
        if not isinstance(report_marker, dict):
            return False
        for field in (
            "version", "compiler", "parent_task_count",
            "patch_version", "decomposed_parent_task_count",
            "inventory_item_count", "blocking_issues",
        ):
            if report_marker.get(field) != marker.get(field):
                return False
    return True


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
    structure.recover_followup_task_prompts(canonical)
    visual_repairs = visuals.normalize_visual_ownership(canonical)
    context_links = visuals.link_shared_context(canonical)
    structure.renumber_tasks(canonical)
    inventory_items = structure.materialize_task_leaf_cases(canonical)
    parent_count, decomposed_count, inventory_items = structure.inventory_shape(
        canonical
    )
    canonical["phase21_hardening"] = {
        "version": HARDENING_VERSION,
        "compiler": COMPILER_LABEL,
        "plain_task_cues_recovered": recovered,
        "task_boundaries_repaired": boundary_repairs,
        "followup_task_prompts_recovered": structure.followup_prompt_count(
            canonical
        ),
        "visual_ownership_rows_normalized": visual_repairs,
        "shared_context_links": context_links,
        "parent_task_count": parent_count,
        "decomposed_parent_task_count": decomposed_count,
        "inventory_item_count": inventory_items,
        "blocking_issues": 0,
    }
    source_issues = source_boundary_issues(canonical)
    canonical["phase21_hardening"]["blocking_issues"] = len(source_issues)
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
    summary["inventory_items"] = inventory_items
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

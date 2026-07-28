"""Install the Phase 2.1 source and final-render hardening contract."""
from __future__ import annotations

from functools import wraps
from types import ModuleType
from typing import Any

from . import canonical_source
from . import canonical_source_phase2 as phase2
from . import canonical_source_phase21 as phase21
from . import canonical_source_phase21_compat as phase21_compat
from . import canonical_source_phase21_render as render
from . import canonical_source_phase21_visuals as visuals
from . import source_visual_contract

_CONTRACT_VERSION = 1


def _phase21_active(canonical: dict[str, Any] | None) -> bool:
    marker = (canonical or {}).get("phase21_hardening")
    return bool(
        isinstance(marker, dict)
        and marker.get("version") == phase21.HARDENING_VERSION
    )


def install(generation: ModuleType) -> None:
    """Activate Phase 2.1 after Phase 2 and compatibility wrappers."""
    if getattr(phase2, "_PHASE21_CONTRACT_VERSION", 0) >= _CONTRACT_VERSION:
        return

    phase21_compat.install()
    original_issue_reader = phase2.phase2_inventory_issues
    original_compile = phase2.compile_phase2_source
    original_load = phase2._load_or_refresh_for_job
    original_expand = generation._expand_mined_types_to_assignment_units
    original_normalize_hubs = generation._normalize_activity_hubs_from_inventory
    original_validate = generation._validate_final_or_raise

    phase2._PHASE21_ORIGINAL_ISSUES = original_issue_reader
    phase2._PHASE21_ORIGINAL_COMPILE = original_compile
    phase2._PHASE21_ORIGINAL_LOAD = original_load
    generation._PHASE21_ORIGINAL_EXPAND_TYPES = original_expand
    generation._PHASE21_ORIGINAL_NORMALIZE_HUBS = original_normalize_hubs
    generation._PHASE21_ORIGINAL_VALIDATE_FINAL = original_validate

    source_visual_contract._nearby_unique_named_figure = (
        visuals.strict_named_figure_repair
    )

    @wraps(original_issue_reader)
    def phase2_inventory_issues(canonical, report=None):
        return phase21.dedupe_issues([
            *original_issue_reader(canonical, report),
            *phase21.phase21_inventory_issues(canonical),
        ])

    @wraps(original_compile)
    def compile_phase2_source(*args, **kwargs):
        compiled = original_compile(*args, **kwargs)
        source = (args[0] if args else kwargs.get("mmd_text")) or ""
        consumer_module = str(kwargs.get("consumer_module") or "")
        return phase21.harden_compiled_source(
            compiled,
            source=str(source),
            consumer_module=consumer_module,
            base_issue_reader=original_issue_reader,
        )

    @wraps(original_load)
    def load_or_refresh_for_job(job):
        canonical, report = original_load(job)
        if _phase21_active(canonical):
            return canonical, report
        from . import uploads

        directory = uploads.source_artifact_directory(int(job.id))
        phase2.write_phase2_artifacts(
            directory,
            mmd_text=str(job.mmd_text or ""),
            source_filename=str(job.filename or "source.mmd"),
            consumer_module="build_concepts",
        )
        canonical_path = (
            directory
            / canonical_source.ARTIFACT_SPECS["canonical_json"]["filename"]
        )
        report_path = (
            directory / canonical_source.ARTIFACT_SPECS["report"]["filename"]
        )
        return phase2._read_json(canonical_path), phase2._read_json(report_path)

    @wraps(original_expand)
    def expand_mined_types_to_assignment_units(types):
        canonical = phase2.active_canonical()
        if not _phase21_active(canonical):
            return original_expand(types)
        return render.intact_type_assignment_units(
            generation, list(types or []), canonical
        )

    @wraps(original_normalize_hubs)
    def normalize_activity_hubs_from_inventory(
        records, inventory, mined_types=None, *args, **kwargs
    ):
        normalized = original_normalize_hubs(
            records, inventory, mined_types, *args, **kwargs
        )
        canonical = phase2.active_canonical()
        if not _phase21_active(canonical):
            return normalized
        return render.normalize_final_records(
            generation, normalized, inventory, mined_types
        )

    @wraps(original_validate)
    def validate_final(records, *args, **kwargs):
        inventory = kwargs.get("inventory")
        mined_types = kwargs.get("mined_types")
        canonical = phase2.active_canonical()
        if (
            _phase21_active(canonical)
            and isinstance(records, list)
            and isinstance(inventory, dict)
            and isinstance(mined_types, dict)
        ):
            records[:] = render.normalize_final_records(
                generation, records, inventory, mined_types
            )
        return original_validate(records, *args, **kwargs)

    phase2.phase2_inventory_issues = phase2_inventory_issues
    phase2.compile_phase2_source = compile_phase2_source
    phase2._load_or_refresh_for_job = load_or_refresh_for_job
    generation._expand_mined_types_to_assignment_units = (
        expand_mined_types_to_assignment_units
    )
    generation._normalize_activity_hubs_from_inventory = (
        normalize_activity_hubs_from_inventory
    )
    generation._validate_final_or_raise = validate_final
    generation._PHASE21_SOURCE_HARDENING_VERSION = _CONTRACT_VERSION
    phase2._PHASE21_CONTRACT_VERSION = _CONTRACT_VERSION

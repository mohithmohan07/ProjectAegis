"""Compatibility layer for the final-topology allocation contract.

The topology contract intentionally changes the live stage order, but it must still
respect test/extension monkeypatches and must not replay valid terminal checkpoints.
This module keeps those integration boundaries explicit without weakening any final
source, host, culmination, or rich-text gate.
"""
from __future__ import annotations

from functools import wraps
from types import ModuleType

from . import concept_topology_contract as topology

_COMPAT_VERSION = 1


def _semantic_topology_identity(generation: ModuleType, row: tuple) -> tuple:
    """Ignore cosmetic case/spacing while preserving semantic row identity."""
    return (
        generation.bi.normalize_question_text(str(row[0] or "")),
        generation.bi.normalize_question_text(str(row[1] or "")),
        generation.bi.normalize_question_text(str(row[2] or "")),
        bool(row[3]),
    )


def _assert_topology_unchanged(
    generation: ModuleType,
    expected: tuple,
    records: list[dict],
    *,
    stage: str,
) -> None:
    actual = topology._topology_signature(generation, records)
    expected_identity = tuple(
        _semantic_topology_identity(generation, row) for row in expected
    )
    actual_identity = tuple(
        _semantic_topology_identity(generation, row) for row in actual
    )
    if actual_identity == expected_identity:
        return
    first_difference = next(
        (
            index,
            expected[index] if index < len(expected) else "<missing>",
            actual[index] if index < len(actual) else "<missing>",
        )
        for index in range(max(len(expected), len(actual)))
        if (
            index >= len(expected_identity)
            or index >= len(actual_identity)
            or expected_identity[index] != actual_identity[index]
        )
    )
    raise RuntimeError(
        f"{stage} changed frozen concept topology; refusing to deposit Type "
        "content after a row was renamed, moved, added, removed, or reordered; "
        f"expected_rows={len(expected)}, actual_rows={len(actual)}, "
        f"first_difference={first_difference!r}"
    )


def _allocate_after_freeze(
    generation: ModuleType,
    records: list[dict],
    *,
    subject: str,
    mmd_text: str,
    meta: dict,
    source_sections: list[dict],
    question_task_inventory: dict,
    mined_types: dict,
    method_anchors: list[dict],
) -> list[dict]:
    """Allocate through public delegates so mocks/extensions remain effective."""
    frozen = topology._strip_source_owned_allocations(generation, records)
    signature = topology._topology_signature(generation, frozen)
    generation._reset_placement_certifications(mined_types)
    generation.progress.step(
        "Concept extraction — allocating Types on frozen concept topology",
        value=0.94,
    )

    assigned = generation._assign_types_via_api(
        frozen,
        subject=subject,
        mmd_text=mmd_text,
        meta=meta,
        sections=source_sections,
        question_task_inventory=question_task_inventory,
        mined_types=mined_types,
    )
    assigned = generation._populate_activity_hubs_via_api(
        assigned,
        question_task_inventory,
        meta=meta,
        mined_types=mined_types,
    )
    if not generation._placement_certification_contract_complete(
        mined_types, question_task_inventory
    ):
        raise RuntimeError(
            "post-freeze Type allocation did not certify every source qid to "
            "one durable concept host"
        )

    assigned = generation._salvage_short_case_examples(
        assigned, inventory=question_task_inventory
    )
    assigned = generation._neutralize_unrepaired_rows(
        assigned, inventory=question_task_inventory
    )
    assigned = generation._enforce_rendered_inventory_coverage(
        assigned, question_task_inventory, mined_types
    )
    assigned = generation._normalize_activity_hubs_from_inventory(
        assigned, question_task_inventory, mined_types
    )
    assigned = generation._disambiguate_certified_split_type_cases(
        assigned, question_task_inventory, mined_types
    )
    assigned = generation.cr.renumber_types_continuously(assigned)
    assigned = generation._canonicalize_concept_rich_text(assigned)
    assigned, rich_text_repaired = generation._repair_final_rich_text_via_api(
        assigned,
        meta=meta,
        inventory=question_task_inventory,
        mined_types=mined_types,
    )
    if rich_text_repaired:
        assigned, _ = generation._reconcile_explicit_figure_images(
            assigned, source_sections
        )
    assigned = generation._canonicalize_concept_rich_text(assigned)
    _assert_topology_unchanged(
        generation,
        signature,
        assigned,
        stage="post-freeze Type allocation",
    )
    generation._validate_final_or_raise(
        assigned,
        stage="post-freeze allocation",
        inventory=question_task_inventory,
        mined_types=mined_types,
        method_anchors=method_anchors,
        source_text=mmd_text,
    )
    mined_types["_topology_allocation_contract"] = {
        "version": topology._CONTRACT_VERSION,
        "state": "allocated_after_freeze",
        "row_count": len(assigned),
    }
    generation.progress.log(
        "Concept topology frozen before Type allocation; Types and "
        "Activity/Info Hubs were assigned once on the final row map.",
        level="success",
    )
    return assigned


def install(generation: ModuleType) -> None:
    if getattr(generation, "_CONCEPT_TOPOLOGY_COMPAT_VERSION", 0) >= _COMPAT_VERSION:
        return

    # The closure in the primary contract resolves this module-level helper at
    # call time, so replacing it keeps one implementation of the pipeline wrapper.
    topology._allocate_after_freeze = _allocate_after_freeze
    topology._assert_topology_unchanged = _assert_topology_unchanged

    original_run = generation._TOPOLOGY_CONTRACT_ORIGINAL_RUN_STAGES

    @wraps(original_run)
    def run_stages(*args, **kwargs):
        resume_checkpoint = kwargs.get("resume_checkpoint")
        allow_final = bool(kwargs.get("allow_final_checkpoint", True))
        saved_final = (
            generation._newest_compatible_concept_checkpoint(
                resume_checkpoint,
                allowed_stages={"final_content_ready"},
            )
            if allow_final
            else None
        )
        if saved_final:
            # A terminal checkpoint has already passed its own strict gate. Keep
            # the existing API-free repair/validation path; do not strip its Types
            # or manufacture a new semantic allocation.
            return original_run(*args, **kwargs)

        # Preserve whichever public delegates a test, extension, or caller has
        # installed. Temporarily replace them only while the pre-final stage runs,
        # then restore them for the one allocation after topology freeze.
        assign_delegate = generation._assign_types_via_api
        hub_delegate = generation._populate_activity_hubs_via_api
        certification_delegate = (
            generation._placement_certification_contract_complete
        )

        def deferred_assign(records, **_kwargs):
            generation.progress.log(
                "Deferred Type allocation until semantic concept topology is final.",
                level="success",
            )
            return topology._strip_source_owned_allocations(generation, records)

        def deferred_hubs(records, *_args, **_kwargs):
            return topology._strip_source_owned_allocations(generation, records)

        generation._assign_types_via_api = deferred_assign
        generation._populate_activity_hubs_via_api = deferred_hubs
        generation._placement_certification_contract_complete = (
            lambda *_args, **_kwargs: True
        )
        token = topology._DEFER_INITIAL_ALLOCATION.set(True)
        try:
            out, inventory, mined_types, snapshot = original_run(*args, **kwargs)
        finally:
            topology._DEFER_INITIAL_ALLOCATION.reset(token)
            generation._assign_types_via_api = assign_delegate
            generation._populate_activity_hubs_via_api = hub_delegate
            generation._placement_certification_contract_complete = (
                certification_delegate
            )

        out = topology._strip_source_owned_allocations(generation, out)
        generation._reset_placement_certifications(mined_types)
        mined_types["_topology_allocation_contract"] = {
            "version": topology._CONTRACT_VERSION,
            "state": "deferred",
        }
        return out, inventory, mined_types, snapshot

    @wraps(generation._TOPOLOGY_CONTRACT_ORIGINAL_REFRESH_REASONS)
    def checkpoint_refresh_reasons(checkpoint, **kwargs):
        # Call through the stored attribute dynamically so tests/extensions can
        # replace the underlying structural refresh policy.
        reasons = list(
            generation._TOPOLOGY_CONTRACT_ORIGINAL_REFRESH_REASONS(
                checkpoint, **kwargs
            )
        )
        # A shape-less legacy payload is not a usable terminal checkpoint. Mark it
        # as predating the freeze contract, while leaving every valid serialized
        # final checkpoint on the existing API-free validation path.
        if checkpoint and not checkpoint.get("stage"):
            contract = (
                (checkpoint.get("mined_types") or {}).get(
                    "_topology_allocation_contract"
                )
                or {}
            )
            if (
                contract.get("version") != topology._CONTRACT_VERSION
                or contract.get("state") != "allocated_after_freeze"
            ):
                reasons.append(
                    "final checkpoint predates topology-frozen Type allocation"
                )
        return list(dict.fromkeys(reasons))

    original_validate = generation._validate_final_or_raise

    @wraps(original_validate)
    def validate_final(records: list[dict], **kwargs):
        try:
            return original_validate(records, **kwargs)
        except RuntimeError:
            for row_index, record in enumerate(records):
                detail = topology.rich_text_defect_detail(
                    generation, record.get("concept_details") or ""
                )
                if not detail:
                    continue
                title = generation._diagnostic_snippet(
                    record.get("concept_title") or record.get("concept") or "",
                    limit=80,
                )
                generation.progress.log(
                    "  exact rich-text defect: "
                    f"row_index={row_index}; concept={title!r}; "
                    f"section={detail['section']!r}; "
                    f"defect={detail['defect']!r}; offset={detail['offset']}; "
                    f"match={detail['match']!r}; context="
                    f"{generation._diagnostic_snippet(detail['context'])!r}",
                    level="error",
                )
            raise

    # Preserve the historical fatal log exactly. The wrapper above adds a second,
    # more precise diagnostic instead of changing the established snippet format.
    generation._validation_error_context = (
        generation._TOPOLOGY_CONTRACT_ORIGINAL_VALIDATION_CONTEXT
    )
    generation._run_live_concept_pre_final_stages = run_stages
    generation._final_checkpoint_refresh_reasons = checkpoint_refresh_reasons
    generation._validate_final_or_raise = validate_final
    generation._CONCEPT_TOPOLOGY_COMPAT_VERSION = _COMPAT_VERSION

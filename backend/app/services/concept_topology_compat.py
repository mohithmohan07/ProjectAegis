"""Compatibility wrappers around the final-validation boundary.

Adds exact rich-text defect diagnostics to the strict final gate and
marks shape-less legacy payloads as unusable terminal checkpoints,
without weakening any final source, host, culmination, or rich-text
gate.
"""
from __future__ import annotations

from functools import wraps
from types import ModuleType

from . import concept_topology_contract as topology

_COMPAT_VERSION = 1


def install(generation: ModuleType) -> None:
    if getattr(generation, "_CONCEPT_TOPOLOGY_COMPAT_VERSION", 0) >= _COMPAT_VERSION:
        return


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
    generation._final_checkpoint_refresh_reasons = checkpoint_refresh_reasons
    generation._validate_final_or_raise = validate_final
    generation._CONCEPT_TOPOLOGY_COMPAT_VERSION = _COMPAT_VERSION

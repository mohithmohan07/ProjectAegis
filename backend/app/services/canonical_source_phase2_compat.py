"""Compatibility refinements for the Phase 2 ACSD source-critical boundary."""
from __future__ import annotations

import copy
from functools import wraps
from types import ModuleType
from typing import Any

from . import canonical_source, canonical_source_phase2 as phase2

_COMPAT_VERSION = 4


def _match_anchor(
    task: dict[str, Any],
    anchors: list[dict[str, Any]],
    used: set[int],
) -> tuple[int, dict[str, Any]] | None:
    """Match enrichment metadata without trusting a repeated label alone."""
    raw_key = phase2._normal_text(task.get("raw_prompt") or "")
    label_key = phase2._normal_text(task.get("source_label") or "")
    try:
        task_section = int(task.get("source_section_index") or 0)
    except (TypeError, ValueError):
        task_section = 0
    try:
        task_position = int(task.get("source_position") or 0)
    except (TypeError, ValueError):
        task_position = 0

    ranked: list[tuple[tuple[int, int, int, int], int, dict[str, Any]]] = []
    for index, anchor in enumerate(anchors):
        if index in used:
            continue
        anchor_raw = str(
            anchor.get("raw_task") or anchor.get("normalized_task") or ""
        )
        anchor_key = phase2._normal_text(anchor_raw)
        if not anchor_key:
            continue
        anchor_label = phase2._normal_text(anchor.get("source_label") or "")
        try:
            anchor_section = int(anchor.get("_source_section_index") or 0)
        except (TypeError, ValueError):
            anchor_section = 0
        try:
            anchor_position = int(anchor.get("_source_position") or 0)
        except (TypeError, ValueError):
            anchor_position = 0

        exact = int(anchor_key == raw_key and bool(raw_key))
        contained = int(
            bool(raw_key)
            and min(len(raw_key), len(anchor_key)) >= 30
            and (raw_key in anchor_key or anchor_key in raw_key)
        )
        same_label = int(bool(label_key) and label_key == anchor_label)
        same_section = int(anchor_section == task_section)
        label_scoped = same_label and same_section
        if not (exact or contained or label_scoped):
            continue
        score = (
            exact,
            contained,
            int(label_scoped),
            -abs(anchor_position - task_position),
        )
        ranked.append((score, index, anchor))

    if not ranked:
        return None
    ranked.sort(key=lambda item: item[0], reverse=True)
    _score, index, anchor = ranked[0]
    return index, anchor


def prune_legacy_inventory_checkpoints(db: Any, job: Any) -> bool:
    """Rewind legacy inventories only when an earlier durable stage exists.

    A historical checkpoint bundle can contain only a 91%/98% stage. Destroying
    that sole recovery point before the generation recovery code inspects it can
    discard useful semantic rows. In that shape, retain the bundle temporarily:
    a non-final stage is refreshed and reconciled in place, while the terminal
    refresh contract below refuses to accept a legacy final stage as complete.
    When a 55% or earlier stage is available, rewind immediately so Type mining
    starts cleanly from stable ACSD qids.
    """
    from . import generation, progress

    stored = copy.deepcopy(job.generation_checkpoint or {})
    raw_entries = stored.get("checkpoints")
    if isinstance(raw_entries, list):
        entries = [
            copy.deepcopy(entry)
            for entry in raw_entries
            if isinstance(entry, dict) and entry.get("stage")
        ]
    else:
        entries = [
            copy.deepcopy(entry)
            for entry in generation._concept_checkpoint_entries(stored)
            if isinstance(entry, dict) and entry.get("stage")
        ]

    cutoff = generation._checkpoint_order("question_inventory")
    stale = [
        entry for entry in entries
        if generation._checkpoint_order(str(entry.get("stage") or "")) >= cutoff
        and not phase2._checkpoint_uses_phase2(entry)
    ]
    if not stale:
        return False
    retained = [
        entry for entry in entries
        if generation._checkpoint_order(str(entry.get("stage") or "")) < cutoff
        or phase2._checkpoint_uses_phase2(entry)
    ]
    if not retained:
        progress.log(
            "Legacy checkpoint has no earlier durable pre-inventory stage; "
            "retaining it for the normal recovery selector. A legacy final "
            "checkpoint will not be accepted as terminal under the Phase 2 "
            "source contract.",
            level="warning",
        )
        return False

    newest = max(
        enumerate(retained),
        key=lambda indexed: (
            generation._checkpoint_order(
                str(indexed[1].get("stage") or "")
            ),
            indexed[0],
        ),
    )[1]
    durable = {
        key: copy.deepcopy(value)
        for key, value in stored.items()
        if key != "checkpoints"
    }
    durable["checkpoints"] = retained
    for field in (
        "stage",
        "stage_order",
        "stage_schema_version",
        "stage_label",
        "saved_at",
        "progress",
    ):
        durable[field] = copy.deepcopy(newest.get(field))
    job.generation_checkpoint = durable
    job.detail = (
        "Phase 2 retained the newest pre-inventory checkpoint and discarded "
        "legacy Question / Task Inventory stages; the next run rebuilds the "
        "inventory deterministically from ACSD."
    )
    db.commit()
    progress.log(
        "Rewound "
        f"{len(stale)} legacy checkpoint stage(s) to the pre-inventory "
        "boundary for the Phase 2 ACSD source contract.",
        level="warning",
    )
    return True


def install(generation: ModuleType | None = None) -> None:
    if getattr(phase2, "_PHASE2_COMPAT_VERSION", 0) >= _COMPAT_VERSION:
        return

    original_compile = phase2.compile_phase2_source

    @wraps(original_compile)
    def compile_phase2_source(*args, **kwargs):
        compiled = original_compile(*args, **kwargs)
        text = compiled.aegis_mmd
        text = text.replace(
            f"<!-- schema_version: {canonical_source.SCHEMA_VERSION} -->",
            f"<!-- schema_version: {phase2.SCHEMA_VERSION} -->",
            1,
        )
        text = text.replace(
            f"<!-- compiler_version: {canonical_source.COMPILER_VERSION} -->",
            f"<!-- compiler_version: {phase2.COMPILER_VERSION} -->",
            1,
        )
        if text == compiled.aegis_mmd:
            return compiled
        return canonical_source.CompiledSource(
            canonical=compiled.canonical,
            aegis_mmd=text,
            report=compiled.report,
        )

    phase2._match_anchor = _match_anchor
    phase2.compile_phase2_source = compile_phase2_source
    phase2.prune_legacy_inventory_checkpoints = prune_legacy_inventory_checkpoints

    if generation is not None:
        original_extract = generation._extract_question_task_inventory_via_api
        original_refresh_reasons = generation._final_checkpoint_refresh_reasons

        @wraps(original_extract)
        def guarded_extract(*args, **kwargs):
            inventory = original_extract(*args, **kwargs)
            contract = inventory.get("source_contract") if isinstance(inventory, dict) else {}
            if not (
                isinstance(contract, dict)
                and contract.get("mode") == phase2.SOURCE_CONTRACT_MODE
            ):
                return inventory
            items = [
                item for item in inventory.get("items") or []
                if isinstance(item, dict)
            ]
            qid_set = {str(item.get("qid") or "") for item in items}
            expected = {
                f"QINV-{index:04d}" for index in range(1, len(items) + 1)
            }
            if qid_set != expected:
                raise RuntimeError(
                    "Phase 2 chapter-wide topic placement changed the canonical "
                    "Question / Task Inventory qid set"
                )
            items.sort(
                key=lambda item: (
                    int(item.get("order_index") or 0),
                    str(item.get("qid") or ""),
                )
            )
            qids = [str(item.get("qid") or "") for item in items]
            if qids != [
                f"QINV-{index:04d}" for index in range(1, len(items) + 1)
            ]:
                raise RuntimeError(
                    "Phase 2 Question / Task Inventory order changed after "
                    "source compilation"
                )
            inventory["items"] = items
            inventory["stats"] = generation._inventory_stats(items)
            return inventory

        @wraps(original_refresh_reasons)
        def final_checkpoint_refresh_reasons(checkpoint, **kwargs):
            reasons = list(original_refresh_reasons(checkpoint, **kwargs))
            if checkpoint and not phase2._checkpoint_uses_phase2(checkpoint):
                reasons.append(
                    "final checkpoint predates Phase 2 ACSD source inventory"
                )
            return list(dict.fromkeys(reasons))

        generation._extract_question_task_inventory_via_api = guarded_extract
        generation._final_checkpoint_refresh_reasons = (
            final_checkpoint_refresh_reasons
        )

    phase2._PHASE2_COMPAT_VERSION = _COMPAT_VERSION

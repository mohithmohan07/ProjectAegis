"""Compatibility refinements for the Phase 2 ACSD checkpoint boundary."""
from __future__ import annotations

import copy
from types import ModuleType
from typing import Any

from . import canonical_source_phase2 as phase2

_COMPAT_VERSION = 1


def prune_legacy_inventory_checkpoints(db: Any, job: Any) -> bool:
    """Rewind legacy inventories only when an earlier durable stage exists.

    A historical checkpoint bundle can contain only a 91%/98% stage. Destroying
    that sole recovery point would force a complete semantic regeneration. In
    that shape, retain the checkpoint and let the active ACSD refresh path
    replace its inventory in place. When a 55% or earlier stage is available,
    rewind to it so Type mining starts cleanly from stable ACSD qids.
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
            "retaining it and replacing its Question / Task Inventory from ACSD "
            "during resume instead of discarding all semantic work.",
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


def install(_generation: ModuleType | None = None) -> None:
    if getattr(phase2, "_PHASE2_COMPAT_VERSION", 0) >= _COMPAT_VERSION:
        return
    phase2.prune_legacy_inventory_checkpoints = prune_legacy_inventory_checkpoints
    phase2._PHASE2_COMPAT_VERSION = _COMPAT_VERSION

"""Wire Pass 4 (Question Polishing) into inventory extraction and display.

Two patches:

* ``generation._extract_question_task_inventory_via_api`` — the freshly
  extracted inventory is polished before it is checkpointed, so the polish
  decisions are made exactly once per run and every later stage (mining,
  Phase 3.3 sealing, deposit validation) sees one stable wording.
* ``generation._inventory_task_text`` — the single function all public
  Example wording flows through (mining backfill deterministically
  overwrites ``example_prompt`` from it). An item carrying ``polished_task``
  presents the polished wording; everything else — including hub rows,
  ACSD task dicts from other id-spaces, and items from checkpoints that
  predate this pass — passes through byte-identical.

A run resumed from a pre-polishing checkpoint has no ``polished_task``
fields, so its sealed text identities keep matching. Fragments recorded by
the pass (``polish_fragments``) are carried but not yet placed — placement
lands with the Pass 5 rewire.
"""
from __future__ import annotations

from types import ModuleType

from . import question_polishing


def install(generation: ModuleType | None = None) -> None:
    if generation is None:
        from . import generation as generation_module

        generation = generation_module

    if not getattr(
        generation._extract_question_task_inventory_via_api,
        "_question_polishing_installed",
        False,
    ):
        original_extract = generation._extract_question_task_inventory_via_api

        def _extract_question_task_inventory_via_api(*args, **kwargs):
            inventory = original_extract(*args, **kwargs)
            return question_polishing.polish_inventory(
                inventory, meta=kwargs.get("meta") or {}
            )

        _extract_question_task_inventory_via_api._question_polishing_installed = True
        generation._extract_question_task_inventory_via_api = (
            _extract_question_task_inventory_via_api
        )

    if not getattr(
        generation._inventory_task_text, "_question_polishing_installed", False
    ):
        original_task_text = generation._inventory_task_text

        def _inventory_task_text(item: dict) -> str:
            polished = (
                str((item or {}).get("polished_task") or "")
                if isinstance(item, dict)
                else ""
            )
            if not polished.strip():
                return original_task_text(item)
            # The polished wording rides the same rendering pipeline (image
            # tags, solution stripping, rich-text canonicalization) as the
            # source wording it replaces.
            return original_task_text(
                {**item, "raw_task": polished, "normalized_task": polished}
            )

        _inventory_task_text._question_polishing_installed = True
        generation._inventory_task_text = _inventory_task_text

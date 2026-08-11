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
            polished = question_polishing.polish_inventory(
                inventory, meta=kwargs.get("meta") or {}
            )
            # Splitting changes the item count, so the extraction's stats
            # snapshot is recomputed over the expanded items.
            polished["stats"] = generation._inventory_stats([
                item for item in polished.get("items") or []
                if isinstance(item, dict)
            ])
            return polished

        _extract_question_task_inventory_via_api._question_polishing_installed = True
        generation._extract_question_task_inventory_via_api = (
            _extract_question_task_inventory_via_api
        )

    if not getattr(
        generation._refresh_inventory_from_source_anchors,
        "_question_polishing_installed",
        False,
    ):
        original_refresh = generation._refresh_inventory_from_source_anchors

        def _refresh_inventory_from_source_anchors(inventory, sections):
            # The anchor refresh reasons about source wording. Fragments
            # standing where their parent stood — several rows sharing one
            # source task — could be pruned as a redundant umbrella, so the
            # parent is restored for the refresh and the split re-applied
            # (deterministically, no model call) afterwards.
            import copy as _copy

            collapsed = question_polishing.collapse_split_items(
                _copy.deepcopy(inventory or {})
            )
            refreshed = original_refresh(collapsed, sections)
            refreshed = question_polishing.supersede_restored_parents(
                question_polishing.expand_split_items(refreshed)
            )
            refreshed["stats"] = generation._inventory_stats([
                item for item in refreshed.get("items") or []
                if isinstance(item, dict)
            ])
            return refreshed

        _refresh_inventory_from_source_anchors._question_polishing_installed = (
            True
        )
        generation._refresh_inventory_from_source_anchors = (
            _refresh_inventory_from_source_anchors
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
            # source wording it replaces. The ACSD source contract pins the
            # display to the canonical source prompt, which would silently
            # discard the polish (reviewers saw verbatim textbook prose,
            # answers included, shipping as Examples) — so the override
            # clears the contract markers and lets the standard raw-to-
            # display rendering run on the polished wording.
            return original_task_text({
                **item,
                "raw_task": polished,
                "normalized_task": polished,
                "_acsd_source_contract": "",
                "_acsd_display_prompt": "",
            })

        _inventory_task_text._question_polishing_installed = True
        generation._inventory_task_text = _inventory_task_text

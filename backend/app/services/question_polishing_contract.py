"""Wire Pass 4 (Question Polishing) into inventory extraction and display.

Inventory and display patches:

* ``generation._finish_inventory_with_topics`` — the shared inventory join
  reached directly by the early parallel track and by inline extraction.
  Polish before either path writes its question_inventory checkpoint.

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
fields, so its sealed text identities keep matching. Legacy fragments remain
compatible with their persisted checkpoints; current polishing never splits
an item. The per-item ``polish_audit`` carries source evidence and the
independent advisory review through extraction without changing source fields.
New source-task-format decisions also carry frozen_task_text and their policy
into source atoms, so Post materialization reads the same question that Type/
Case mining classified. No replay upgrades a recorded historical audit.

Three-step workflow (Q51, ``reviewed_file_workflow_policy.V2``): Step 1
extracts every question AS IS under its Types/Cases, so the two inventory
wrappers above do not run Pass 4 for a run bound to (or metadata stamped
with) the current workflow version. The inventory leaves the join with its
raw wording, recomputed ``stats`` and an explicit ``question_polishing_deferred``
marker; polishing runs in Step 2 over the reviewed Post questions. V1 runs
and historical runs (no recorded version) keep the Step 1 polish unchanged.
The display override needs no gate: an item without polish fields already
renders its raw wording byte-identically.
"""
from __future__ import annotations

import copy
from types import ModuleType

from . import progress
from . import question_polishing
from . import reviewed_file_workflow_policy as workflow
from . import source_task_polishing_policy as source_format

DEFERRED_KEY = "question_polishing_deferred"
DEFERRED_STAGE = "step_2_reviewed_file"
DEFERRED_REASON = (
    "Step 1 extracts questions as is; polishing runs in Step 2 on the "
    "reviewed Post questions"
)


def deferred_marker() -> dict:
    """The recorded Step 1 decision that Pass 4 belongs to Step 2."""
    return {
        "policy": workflow.V2,
        "stage": DEFERRED_STAGE,
        "reason": DEFERRED_REASON,
    }


def polishing_deferred(meta) -> bool:
    """Whether this run's recorded workflow moves Pass 4 to Step 2."""
    return bool(workflow.defers_polishing(meta) or workflow.run_defers_polishing())


def install(generation: ModuleType | None = None) -> None:
    if generation is None:
        from . import generation as generation_module

        generation = generation_module

    def _polish_finished_inventory(inventory, meta):
        if polishing_deferred(meta):
            # Q51 Step 1: the inventory ships as extracted. Same copy
            # discipline as the polish (callers never receive an alias);
            # only stats and the recorded deferral marker are added. The
            # inline extraction wrapper re-enters here after the join
            # wrapper, so the marker also keeps the log line to one.
            deferred = copy.deepcopy(inventory or {})
            already_marked = isinstance(deferred.get(DEFERRED_KEY), dict)
            deferred[DEFERRED_KEY] = deferred_marker()
            deferred["stats"] = generation._inventory_stats([
                item for item in deferred.get("items") or []
                if isinstance(item, dict)
            ])
            if not already_marked:
                progress.log(
                    "Step 1 keeps every extracted question as is: question "
                    "polishing is deferred to Step 2, where it runs on the "
                    "reviewed Post questions before the Master is authored."
                )
            return deferred
        polished = question_polishing.polish_inventory(inventory, meta=meta or {})
        polished["stats"] = generation._inventory_stats([
            item for item in polished.get("items") or []
            if isinstance(item, dict)
        ])
        return polished

    original_finish = getattr(generation, "_finish_inventory_with_topics", None)
    if callable(original_finish) and not getattr(
        original_finish, "_question_polishing_installed", False,
    ):
        def _finish_inventory_with_topics(*args, **kwargs):
            inventory = original_finish(*args, **kwargs)
            return _polish_finished_inventory(inventory, kwargs.get("meta"))

        _finish_inventory_with_topics._question_polishing_installed = True
        generation._finish_inventory_with_topics = _finish_inventory_with_topics

    if not getattr(
        generation._extract_question_task_inventory_via_api,
        "_question_polishing_installed",
        False,
    ):
        original_extract = generation._extract_question_task_inventory_via_api

        def _extract_question_task_inventory_via_api(*args, **kwargs):
            inventory = original_extract(*args, **kwargs)
            # Inline extraction normally already passed the shared join;
            # recorded audits make this compatibility wrapper a no-spend
            # replay for it, while alternate extractors still receive polish.
            return _polish_finished_inventory(inventory, kwargs.get("meta"))

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
                str((item or {}).get(
                    "frozen_task_text" if source_format.applies(item) else "polished_task"
                ) or "")
                if isinstance(item, dict)
                else ""
            )
            if not polished.strip():
                return original_task_text(item)
            if source_format.applies(item) and source_format.context_applies(item):
                # Corrected Concept review may separately author a grounded
                # context while its question remains an exact edited quote.
                # Display that accepted context once; no raw-context fallback.
                accepted_context = str(item.get("learner_context") or "")
                if item.get("reviewed_context") and accepted_context and accepted_context not in polished:
                    polished = accepted_context + "<br>" + polished
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
                # Current upstream wording already embeds the API-selected
                # learner_context. The historical formatter would prepend the
                # complete raw shared_context merely because its substring is
                # absent, resurrecting intentionally omitted chapter extracts.
                # This is an explicit authority projection, not local pruning;
                # the original item and all source evidence stay unchanged.
                **({"requires_context": False}
                   if source_format.applies(item) and source_format.context_applies(item)
                   else {}),
            })

        _inventory_task_text._question_polishing_installed = True
        generation._inventory_task_text = _inventory_task_text

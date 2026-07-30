"""Phase 3.10 terminal Figure and exact-inventory convergence.

The RNE acceptance run reached the deposit boundary with exact Example coverage,
then the terminal Figure contract correctly added the chapter-registry image for
one explicit Figure reference.  Exact inventory coverage subsequently reported
that same qid as missing because the authoritative inventory projection still
contained the pre-reconciliation visual metadata.

This contract makes the inventory and rendered Example travel through the same
source-authoritative Figure projection before every terminal gate.  Raw task
wording and qid identity remain untouched; only display-owned image URLs,
captions, and the rendered Example are reconciled.  One bounded deterministic
retry is allowed if an inner legacy wrapper mutates terminal presentation after
the first convergence pass.
"""
from __future__ import annotations

import copy
import re
from functools import wraps
from typing import Any, Callable

from . import progress
from . import terminal_figure_contract as terminal_figure

_CONTRACT_VERSION = 1
_PROJECTION_VERSION = "phase3.10-terminal-figure-inventory-convergence-1"
_PROJECTION_KEY = "_terminal_figure_registry_projection"

FinalValidator = Callable[..., Any]


def _terminal_inventory_context(
    generation: Any,
    records: object,
    inventory: object,
    source_text: object,
    stage: object,
) -> bool:
    return bool(
        isinstance(records, list)
        and isinstance(inventory, dict)
        and str(source_text or "").strip()
        and terminal_figure._terminal_stage(stage)
    )


def _registry_entries_for_prompt(
    generation: Any,
    prompt: str,
    registry: dict[str, list[dict[str, Any]]],
) -> tuple[list[str], list[dict[str, Any]]] | None:
    """Resolve every explicit Figure reference from visible prompt wording.

    Existing image tags are deliberately ignored while reading identifiers.  A
    stale tag can name a different Figure and must never become source authority.
    All references must resolve before any inventory display metadata is changed.
    """
    visible = generation._RENDERED_IMAGE_TAG_RE.sub(" ", str(prompt or ""))
    figure_ids = generation._figure_reference_ids(visible)
    if not figure_ids:
        return None

    entries: list[dict[str, Any]] = []
    for figure_id in figure_ids:
        matches = generation._source_figure_registry_entries(
            registry,
            figure_id,
        )
        if not matches:
            return None
        for entry in matches:
            url = str(entry.get("url") or "").strip()
            if not url.startswith("https://"):
                return None
            if any(str(row.get("url") or "") == url for row in entries):
                continue
            entries.append(copy.deepcopy(entry))
    return figure_ids, entries


def _project_inventory_figures(
    generation: Any,
    inventory: dict[str, Any],
    *,
    source_text: str,
) -> list[str]:
    """Project exact source-registry Figures onto inventory display metadata.

    ``raw_task`` / ``normalized_task`` / qid are never rewritten.  The existing
    inventory renderer remains authoritative for public wording and KaTeX; this
    helper supplies the same URL/caption set that terminal Example repair uses.
    """
    sections = generation.parse_mmd_sections(source_text)
    registry = generation._source_figure_registry(sections)
    if not registry:
        return []

    projected_qids: list[str] = []
    for item in inventory.get("items") or []:
        if not isinstance(item, dict):
            continue
        before = generation._inventory_task_text(item)
        resolved = _registry_entries_for_prompt(
            generation,
            before,
            registry,
        )
        if resolved is None:
            continue
        figure_ids, entries = resolved
        urls = [str(entry.get("url") or "").strip() for entry in entries]
        captions = {
            str(entry.get("url") or "").strip(): generation._clean_visual_caption(
                entry.get("caption") or ""
            )
            for entry in entries
            if str(entry.get("url") or "").strip()
        }
        projection = {
            "version": _PROJECTION_VERSION,
            "figure_ids": list(figure_ids),
            "image_urls": list(urls),
        }
        existing = item.get(_PROJECTION_KEY)
        existing_urls = [
            str(value or "").strip()
            for value in item.get("image_urls") or []
            if str(value or "").strip()
        ]
        existing_captions = {
            str(url): generation._clean_visual_caption(caption or "")
            for url, caption in (item.get("_image_captions") or {}).items()
            if str(url or "").strip()
        } if isinstance(item.get("_image_captions"), dict) else {}

        item["image_urls"] = list(urls)
        item["_image_captions"] = dict(captions)
        item["_figure_images_resolved"] = True
        item["requires_visual"] = True
        item[_PROJECTION_KEY] = projection
        after = generation._inventory_task_text(item)
        if (
            existing != projection
            or existing_urls != urls
            or existing_captions != captions
            or generation._inventory_coverage_key(before)
            != generation._inventory_coverage_key(after)
        ):
            qid = str(item.get("qid") or "").strip()
            projected_qids.append(qid or "<unidentified>")
    return projected_qids


def _log_exact_inventory_defects(
    generation: Any,
    defects: dict[str, list[str]],
    inventory: dict[str, Any],
    *,
    stage: str,
) -> None:
    missing = list(defects.get("missing") or [])
    duplicate = list(defects.get("duplicate") or [])
    if not missing and not duplicate:
        return
    progress.log(
        "Phase 3.10 terminal Figure/inventory convergence still found "
        f"{len(missing)} missing and {len(duplicate)} duplicate qid(s) at "
        f"{stage}.",
        level="warning",
    )
    items = {
        str(item.get("qid") or "").strip(): item
        for item in inventory.get("items") or []
        if isinstance(item, dict) and str(item.get("qid") or "").strip()
    }
    for status, qids in (("missing", missing), ("duplicate", duplicate)):
        for qid in qids[:20]:
            item = items.get(qid, {})
            prompt = generation._inventory_task_text(item) if item else ""
            visible = generation._RENDERED_IMAGE_TAG_RE.sub(" ", prompt)
            figure_ids = generation._figure_reference_ids(visible)
            snippet = generation._diagnostic_snippet(prompt, limit=260)
            progress.log(
                "Phase 3.10 exact-inventory defect: "
                f"qid={qid!r}; status={status!r}; "
                f"figure_ids={figure_ids!r}; prompt={snippet!r}",
                level="warning",
            )


def _converge_terminal_figure_inventory(
    generation: Any,
    records: list[dict[str, Any]],
    inventory: dict[str, Any],
    mined_types: dict[str, Any] | None,
    *,
    source_text: str,
    stage: str,
) -> dict[str, Any]:
    """Converge inventory display, rendered Examples, and exact coverage."""
    projected_qids = _project_inventory_figures(
        generation,
        inventory,
        source_text=source_text,
    )
    repaired_examples = generation._reconcile_terminal_figure_images(
        records,
        source_text,
    )

    defects = generation._rendered_inventory_coverage_defects(records, inventory)
    if defects["missing"] or defects["duplicate"]:
        repaired = generation._enforce_rendered_inventory_coverage(
            records,
            inventory,
            mined_types,
        )
        records[:] = repaired
        # A restored source prompt may have been written by an older checkpoint;
        # reassert exact Figure tags once after the exact-coverage repair.
        repaired_examples += generation._reconcile_terminal_figure_images(
            records,
            source_text,
        )
        defects = generation._rendered_inventory_coverage_defects(
            records,
            inventory,
        )

    if projected_qids or repaired_examples:
        progress.log(
            "Phase 3.10 converged terminal Figure ownership with exact inventory: "
            f"{len(projected_qids)} inventory projection(s), "
            f"{repaired_examples} rendered Example repair(s); "
            f"{len(defects['missing'])} missing / "
            f"{len(defects['duplicate'])} duplicate.",
            level=(
                "success"
                if not defects["missing"] and not defects["duplicate"]
                else "warning"
            ),
        )
    if defects["missing"] or defects["duplicate"]:
        _log_exact_inventory_defects(
            generation,
            defects,
            inventory,
            stage=stage,
        )
    return {
        "projected_qids": projected_qids,
        "repaired_examples": repaired_examples,
        "defects": defects,
    }


def _is_exact_inventory_failure(exc: Exception) -> bool:
    message = str(exc).casefold()
    return bool(
        "exact_inventory_coverage" in message
        or "exact inventory coverage" in message
        or "rendered types failed exact inventory coverage" in message
    )


def _validate_with_terminal_figure_inventory_convergence(
    generation: Any,
    original: FinalValidator,
    records: list[dict[str, Any]],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> Any:
    inventory = kwargs.get("inventory")
    mined_types = kwargs.get("mined_types")
    source_text = str(kwargs.get("source_text") or "")
    stage = str(kwargs.get("stage") or "final")
    enabled = _terminal_inventory_context(
        generation,
        records,
        inventory,
        source_text,
        stage,
    )

    if enabled:
        _converge_terminal_figure_inventory(
            generation,
            records,
            inventory,
            mined_types,
            source_text=source_text,
            stage=f"before {stage}",
        )

    try:
        result = original(records, *args, **kwargs)
    except RuntimeError as exc:
        if not enabled or not _is_exact_inventory_failure(exc):
            raise
        # One bounded deterministic turnover handles a legacy inner renderer that
        # changes Figure presentation after the outer preflight has completed.
        second = _converge_terminal_figure_inventory(
            generation,
            records,
            inventory,
            mined_types,
            source_text=source_text,
            stage=f"retry before {stage}",
        )
        if second["defects"]["missing"] or second["defects"]["duplicate"]:
            raise
        progress.log(
            "Phase 3.10 repaired terminal Figure-driven inventory drift and is "
            f"retrying {stage} validation once.",
            level="success",
        )
        result = original(records, *args, **kwargs)

    if enabled:
        defects = generation._rendered_inventory_coverage_defects(
            records,
            inventory,
        )
        if defects["missing"] or defects["duplicate"]:
            _log_exact_inventory_defects(
                generation,
                defects,
                inventory,
                stage=f"after {stage}",
            )
            raise RuntimeError(
                f"{stage} Phase 3.10 terminal Figure/inventory convergence "
                f"failed: {len(defects['missing'])} missing, "
                f"{len(defects['duplicate'])} duplicate qid(s)"
            )
    return result


def install(generation: Any | None = None) -> None:
    if generation is None:
        from . import generation as generation

    if (
        getattr(generation, "_PHASE310_TERMINAL_FIGURE_INVENTORY_VERSION", 0)
        >= _CONTRACT_VERSION
    ):
        return

    original_validate = generation._validate_final_or_raise
    generation._PHASE310_ORIGINAL_VALIDATE_FINAL = original_validate

    @wraps(original_validate)
    def validate_final(
        records: list[dict[str, Any]],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        return _validate_with_terminal_figure_inventory_convergence(
            generation,
            generation._PHASE310_ORIGINAL_VALIDATE_FINAL,
            records,
            args,
            kwargs,
        )

    generation._validate_final_or_raise = validate_final
    generation._PHASE310_TERMINAL_FIGURE_INVENTORY_CONTRACT = _PROJECTION_VERSION
    generation._PHASE310_TERMINAL_FIGURE_INVENTORY_VERSION = _CONTRACT_VERSION

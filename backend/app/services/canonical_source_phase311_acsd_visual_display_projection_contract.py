"""Phase 3.11 ACSD terminal visual-display projection.

Phase 3.10 correctly projected source-registry Figure URLs/captions onto the
inventory metadata and repaired the rendered Type Example.  A live RNE run then
exposed a wrapper-order gap for Phase 2 ACSD inventory rows: the Phase 2
``_inventory_task_text`` contract returns the immutable ``_acsd_display_prompt``
directly, so later ``image_urls`` metadata is intentionally ignored.  The
rendered Example therefore carried both Fig. 14(a) and Fig. 14(b), while the
exact-inventory side still compared the pre-projection display prompt.

This contract installs after Phase 3.10 and gives a verified terminal Figure
projection an explicit public-display override.  The ACSD source prompt, raw
question, qid, topic, and Figure identities remain unchanged.  Only canonical
``[img]`` tags are replaced, using the same ordered source-registry entries and
caption rules as terminal Example reconciliation.
"""
from __future__ import annotations

import re
from functools import wraps
from typing import Any, Callable

from . import canonical_source_phase310_terminal_figure_inventory_convergence_contract as phase310

_CONTRACT_VERSION = 1
_DISPLAY_VERSION = "phase3.11-acsd-terminal-visual-display-1"
_DISPLAY_KEY = "_terminal_figure_inventory_display_override"

InventoryTextProvider = Callable[[dict[str, Any]], str]
HubNoteProvider = Callable[[dict[str, Any]], str]


def _projection_payload(item: object) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    projection = item.get(phase310._PROJECTION_KEY)
    if not isinstance(projection, dict):
        return None
    if projection.get("version") != phase310._PROJECTION_VERSION:
        return None
    urls = [
        str(value or "").strip()
        for value in projection.get("image_urls") or []
        if str(value or "").strip()
    ]
    if not urls:
        return None
    return {
        "version": _DISPLAY_VERSION,
        "figure_ids": [
            str(value or "").strip()
            for value in projection.get("figure_ids") or []
            if str(value or "").strip()
        ],
        "image_urls": urls,
    }


def _projection_tags(
    generation: Any,
    item: dict[str, Any],
    projection: dict[str, Any],
) -> list[str]:
    captions = item.get("_image_captions")
    caption_by_url = (
        {
            str(url): generation._clean_visual_caption(caption or "")
            for url, caption in captions.items()
            if str(url or "").strip()
        }
        if isinstance(captions, dict)
        else {}
    )
    figure_ids = list(projection.get("figure_ids") or [])
    tags: list[str] = []
    for image_index, url in enumerate(projection.get("image_urls") or []):
        url = str(url or "").strip()
        if not url.startswith("https://"):
            return []
        alt = generation._clean_visual_caption(caption_by_url.get(url) or "")
        if not alt:
            figure_id = (
                str(figure_ids[image_index] or "").strip()
                if image_index < len(figure_ids)
                else ""
            )
            alt = f"Fig. {figure_id}".strip() if figure_id else "Source visual"
        if image_index:
            alt = f"{alt}, visual {image_index + 1}"
        try:
            tags.append(generation.kr.image(url, alt))
        except ValueError:
            return []
    return tags


def _replace_visual_tags(
    generation: Any,
    rendered: str,
    *,
    item: dict[str, Any],
    projection: dict[str, Any],
) -> str:
    tags = _projection_tags(generation, item, projection)
    if not tags:
        return str(rendered or "").strip()
    body = generation._RENDERED_IMAGE_TAG_RE.sub(" ", str(rendered or ""))
    body = re.sub(r"\s+", " ", body).strip()
    output = " ".join(part for part in (body, *tags) if part).strip()
    output = generation.kr.canonicalize_rich_text(output).strip()
    issues = generation.kr.rich_text_issues(output)
    if issues:
        repaired = generation.kr.repair_unwrapped_math(output)
        if not generation.kr.rich_text_issues(repaired):
            output = repaired
    return output


def _inventory_text_with_projection(
    generation: Any,
    original: InventoryTextProvider,
    item: dict[str, Any],
) -> str:
    rendered = str(original(item) or "").strip()
    projection = _projection_payload(item)
    if projection is None:
        item.pop(_DISPLAY_KEY, None)
        return rendered
    output = _replace_visual_tags(
        generation,
        rendered,
        item=item,
        projection=projection,
    )
    item[_DISPLAY_KEY] = {
        **projection,
        "display_prompt": output,
    }
    return output


def _hub_note_with_projection(
    generation: Any,
    original: HubNoteProvider,
    item: dict[str, Any],
) -> str:
    rendered = str(original(item) or "").strip()
    projection = _projection_payload(item)
    if projection is None:
        return rendered
    return _replace_visual_tags(
        generation,
        rendered,
        item=item,
        projection=projection,
    )


def install(generation: Any | None = None) -> None:
    if generation is None:
        from . import generation as generation

    if (
        getattr(generation, "_PHASE311_ACSD_VISUAL_DISPLAY_VERSION", 0)
        >= _CONTRACT_VERSION
    ):
        return

    original_inventory_text = generation._inventory_task_text
    original_hub_note = generation._compact_activity_hub_note
    generation._PHASE311_ORIGINAL_INVENTORY_TASK_TEXT = original_inventory_text
    generation._PHASE311_ORIGINAL_COMPACT_HUB_NOTE = original_hub_note

    @wraps(original_inventory_text)
    def inventory_task_text(item: dict[str, Any]) -> str:
        return _inventory_text_with_projection(
            generation,
            generation._PHASE311_ORIGINAL_INVENTORY_TASK_TEXT,
            item,
        )

    @wraps(original_hub_note)
    def compact_activity_hub_note(item: dict[str, Any]) -> str:
        return _hub_note_with_projection(
            generation,
            generation._PHASE311_ORIGINAL_COMPACT_HUB_NOTE,
            item,
        )

    generation._inventory_task_text = inventory_task_text
    generation._compact_activity_hub_note = compact_activity_hub_note
    generation._PHASE311_ACSD_VISUAL_DISPLAY_CONTRACT = _DISPLAY_VERSION
    generation._PHASE311_ACSD_VISUAL_DISPLAY_VERSION = _CONTRACT_VERSION

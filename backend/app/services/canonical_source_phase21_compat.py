"""Compatibility refinements for Phase 2.1 task display and visual ownership."""
from __future__ import annotations

import copy
from typing import Any

from . import canonical_source_phase21_structure as structure
from . import canonical_source_phase21_visuals as visuals
from . import katex_rules as kr

_COMPAT_VERSION = 1


def _enclosed_figure_ids(
    canonical: dict[str, Any], task_id: str,
) -> list[str]:
    found: list[str] = []
    if not task_id:
        return found
    for block in canonical.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        if task_id not in (block.get("task_ids") or []):
            continue
        figure_id = str(block.get("figure_id") or "").strip()
        if figure_id and figure_id not in found:
            found.append(figure_id)
    return found


def _canonical_tags(
    urls: list[str], captions: dict[str, str],
) -> list[str]:
    tags: list[str] = []
    for index, url in enumerate(urls):
        caption = str(captions.get(url) or f"Source visual {index + 1}").strip()
        try:
            tags.append(kr.image(url, caption))
        except ValueError:
            continue
    return tags


def _normalize_visual_ownership(canonical: dict[str, Any]) -> int:
    original = visuals._PHASE21_ORIGINAL_NORMALIZE_VISUALS
    changed = original(canonical)
    figures_by_id, images_by_id, _figures_by_reference = visuals.figure_maps(
        canonical
    )
    for task in canonical.get("tasks") or []:
        if not isinstance(task, dict):
            continue
        task_id = str(task.get("task_id") or "")
        raw_prompt = visuals.strip_public_layout(task.get("raw_prompt") or "")
        compatible_enclosed: list[str] = []
        for figure_id in _enclosed_figure_ids(canonical, task_id):
            caption = str(
                figures_by_id.get(figure_id, {}).get("caption_raw") or ""
            )
            if visuals.visuals_compatible(raw_prompt, caption):
                compatible_enclosed.append(figure_id)

        raw_figure_ids = visuals.unique([
            *[str(value) for value in task.get("raw_figure_refs") or []],
            *compatible_enclosed,
        ])
        display_figure_ids = visuals.unique([
            *[str(value) for value in task.get("display_figure_refs") or []],
            *compatible_enclosed,
        ])
        raw_urls, raw_captions = visuals.figure_urls_and_captions(
            raw_figure_ids, figures_by_id, images_by_id
        )
        display_urls, display_captions = visuals.figure_urls_and_captions(
            display_figure_ids, figures_by_id, images_by_id
        )
        raw_urls = visuals.unique([
            *raw_urls,
            *[str(value) for value in task.get("raw_image_urls") or []],
        ])
        display_urls = visuals.unique([
            *display_urls,
            *[str(value) for value in task.get("display_image_urls") or []],
        ])
        raw_captions = {
            **(task.get("raw_image_captions") or {}),
            **raw_captions,
        }
        display_captions = {
            **(task.get("display_image_captions") or {}),
            **display_captions,
        }

        body = visuals.replace_figure_ids(
            raw_prompt,
            [
                copy.deepcopy(item)
                for item in task.get("display_overrides") or []
                if isinstance(item, dict)
            ],
        )
        body = structure.canonical_task_display(body)
        rendered = " ".join(
            part for part in (
                body,
                *_canonical_tags(display_urls, display_captions),
            )
            if part
        ).strip()
        rendered = kr.canonicalize_rich_text(rendered)

        before = (
            tuple(task.get("display_figure_refs") or []),
            tuple(task.get("display_image_urls") or []),
            str(task.get("display_prompt") or ""),
        )
        task["raw_prompt"] = raw_prompt
        task["display_prompt"] = rendered
        task["raw_figure_refs"] = raw_figure_ids
        task["display_figure_refs"] = display_figure_ids
        task["figure_refs"] = display_figure_ids
        task["raw_image_urls"] = raw_urls
        task["display_image_urls"] = display_urls
        task["image_urls"] = display_urls
        task["raw_image_captions"] = raw_captions
        task["display_image_captions"] = display_captions
        task["_image_captions"] = display_captions
        task["requires_visual"] = bool(
            task.get("display_figure_reference_ids")
            or display_figure_ids
            or display_urls
        )
        after = (
            tuple(display_figure_ids),
            tuple(display_urls),
            rendered,
        )
        if before != after:
            changed += 1
    return changed


def _link_shared_context(canonical: dict[str, Any]) -> int:
    """Link only explicitly named Box context, not a generic inline table cue."""
    blocks = [
        block for block in canonical.get("blocks") or []
        if isinstance(block, dict)
    ]
    linked = 0
    for task in canonical.get("tasks") or []:
        if not isinstance(task, dict):
            continue
        prompt = str(task.get("raw_prompt") or "")
        if visuals._BOX_REF_RE.search(prompt) is None:
            # Remove a broad Phase 2.1 context guess if this compiler is invoked
            # repeatedly against an already-materialized in-memory object.
            task.pop("shared_context_block_ids", None)
            task.pop("shared_context", None)
            content_objects = task.get("content_objects")
            if isinstance(content_objects, dict):
                content_objects.pop("shared_context_blocks", None)
                if not content_objects:
                    task.pop("content_objects", None)
            continue
        task_start = int(task.get("source_start") or 0)
        candidates = [
            block for block in blocks
            if block.get("kind") == "table"
            and int(block.get("source_end") or 0) <= task_start
            and task_start - int(block.get("source_end") or 0) <= 6000
        ]
        if not candidates:
            continue
        block = max(candidates, key=lambda item: int(item.get("source_end") or 0))
        context = visuals.tabular_context(block.get("raw_text") or "")
        if not context:
            continue
        task["shared_context_block_ids"] = [str(block.get("block_id") or "")]
        task["shared_context"] = context
        task["requires_context"] = True
        content_objects = task.get("content_objects")
        if not isinstance(content_objects, dict):
            content_objects = {}
        content_objects["shared_context_blocks"] = [{
            "block_id": block.get("block_id"),
            "kind": block.get("kind"),
            "display_text": context,
        }]
        task["content_objects"] = content_objects
        linked += 1
    return linked


def install() -> None:
    if getattr(visuals, "_PHASE21_COMPAT_VERSION", 0) >= _COMPAT_VERSION:
        return
    visuals._PHASE21_ORIGINAL_NORMALIZE_VISUALS = (
        visuals.normalize_visual_ownership
    )
    visuals._PHASE21_ORIGINAL_LINK_SHARED_CONTEXT = visuals.link_shared_context
    visuals.normalize_visual_ownership = _normalize_visual_ownership
    visuals.link_shared_context = _link_shared_context
    visuals._PHASE21_COMPAT_VERSION = _COMPAT_VERSION

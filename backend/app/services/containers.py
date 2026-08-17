"""The three §4 Phase-1.2 containers: one vocabulary, pure projections.

Containers are the sorted VIEWS of verdicts the model has already made —
the Chapter Reading census, the ACSD/outline task rulings, and the
Question/Task Inventory. Nothing here judges content (Rule 1): this
module only projects existing verdicts into id-keyed role lists, and a
block keeps its primary role while participating in every downstream
relationship it already has (an ``_activity_origin`` item stays a
Container-02 enrichment AND a Container-03 question). Projections never
mutate their inputs and never drop a relationship.

This module is also the single home of the pooled-hub kind vocabulary.
``generation``, ``coverage_ledger`` and ``question_polishing`` all
consume ``HUB_INVENTORY_KINDS`` from here — the three previously
duplicated frozensets are retired.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Mapping

CONTAINERS_VERSION = 1
CONTAINERS_FILENAME = "source.containers.json"

# Inventory kinds that belong in Activity/Info Hub (Container 02).
# Assessable prompts originating in an Activity also appear in Types while
# reusing the same inventory identity (``_activity_origin``). ``info_hub``
# covers enrichment boxes (asides, "do you know?" panels, biography boxes,
# source excerpts): pooled chapter-wide and placed by the model with the
# material they enrich, never treated as questions.
HUB_INVENTORY_KINDS = frozenset({"activity", "experiment_task", "info_hub"})

# The single recorded disposition a pooled FIGURE may carry instead of a
# placement (hub items have no disposition escape — they must be placed).
FIGURE_DISPOSITION = "decorative_or_duplicate"

# Chapter Reading census kinds behind each container (§4 Phase 1.2 table).
_CONTAINER_01_CENSUS_KINDS = frozenset({"prose", "heading"})
_CONTAINER_02_CENSUS_KINDS = frozenset({"activity", "info_hub"})


def _items(inventory: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    return [
        item
        for item in (inventory or {}).get("items") or []
        if isinstance(item, dict)
    ]


def hub_item_qids(inventory: Mapping[str, Any] | None) -> list[str]:
    """QIDs of every pooled Container-02 inventory item, in inventory order."""
    qids: list[str] = []
    for item in _items(inventory):
        qid = str(item.get("qid") or "").strip()
        if not qid or qid in qids:
            continue
        kind = str(item.get("source_kind") or "").strip().lower()
        if kind in HUB_INVENTORY_KINDS or bool(item.get("_activity_origin")):
            qids.append(qid)
    return qids


def figure_block_evidence(
    canonical: Mapping[str, Any] | None,
    inventory: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    """Mechanical evidence table for every canonical ``kind=figure`` block.

    Pure parsing, no judgment: extracts each figure block's image URLs and
    caption from its own source markup (LaTeX ``\\includegraphics``,
    markdown images, canonical ``[img]`` tags) and records which inventory
    items already claim each URL through their ``image_urls``. The
    placement pass and the coverage ledger both consume this table.
    """
    from . import canonical_source as cs

    claimed_by: dict[str, list[str]] = {}
    for item in _items(inventory):
        qid = str(item.get("qid") or "").strip()
        for url in item.get("image_urls") or []:
            value = str(url or "").strip()
            if not value:
                continue
            claimed_by.setdefault(value, [])
            if qid and qid not in claimed_by[value]:
                claimed_by[value].append(qid)

    rows: list[dict[str, Any]] = []
    for block in (canonical or {}).get("blocks") or []:
        if not isinstance(block, Mapping):
            continue
        if str(block.get("kind") or "") != "figure":
            continue
        block_id = str(block.get("block_id") or "")
        if not block_id:
            continue
        raw = str(block.get("raw_text") or block.get("display_text") or "")
        occurrences = (
            cs._image_occurrences(raw, absolute_start=0) if raw else []
        )
        urls: list[str] = []
        alt_caption = ""
        for occurrence in occurrences:
            url = str(occurrence.get("url") or "").strip()
            if url and url not in urls:
                urls.append(url)
            if not alt_caption:
                alt_caption = cs._clean_caption(
                    str(occurrence.get("alt_raw") or "")
                )
        caption = (
            cs._clean_caption(
                cs._balanced_command_body(raw, cs._CAPTION_START_RE)
            )
            if raw
            else ""
        ) or alt_caption
        claimants = sorted({
            qid
            for url in urls
            for qid in claimed_by.get(url, [])
        })
        rows.append({
            "block_id": block_id,
            "image_urls": urls,
            "unclaimed_image_urls": [
                url for url in urls if not claimed_by.get(url)
            ],
            "caption": caption,
            "claimed_by_qids": claimants,
        })
    return rows


def _census_rows(census: Any) -> list[dict[str, Any]]:
    """Accept a chapter-reading result dict or a bare census row list."""
    if isinstance(census, Mapping):
        rows = census.get("census") or []
    else:
        rows = census or []
    return [row for row in rows if isinstance(row, Mapping)]


def _census_furniture(census: Any) -> list[str]:
    if isinstance(census, Mapping):
        return [
            str(line)
            for line in census.get("dropped_furniture") or []
            if str(line or "").strip()
        ]
    return []


def _census_refs(
    rows: list[dict[str, Any]], kinds: frozenset[str]
) -> list[dict[str, Any]]:
    return [
        {
            "ref": f"census:{index:04d}",
            "kind": str(row.get("kind") or ""),
            "label": str(row.get("label") or ""),
            "page": row.get("page"),
        }
        for index, row in enumerate(rows)
        if str(row.get("kind") or "") in kinds
    ]


def container_projections(
    *,
    census: Any,
    canonical: Mapping[str, Any] | None,
    inventory: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Project recorded model verdicts into the three container views.

    Returns id-keyed role lists (never destructive buckets): an id may
    appear in more than one container, and ``dual_roles`` names every such
    shared membership explicitly. ``furniture`` lists what was dropped and
    what it said — verbatim — from both source lanes (R4).
    """
    rows = _census_rows(census)
    figure_rows = figure_block_evidence(canonical, inventory)
    hub_qids = hub_item_qids(inventory)
    all_qids: list[str] = []
    for item in _items(inventory):
        qid = str(item.get("qid") or "").strip()
        if qid and qid not in all_qids:
            all_qids.append(qid)

    claimed_figure_ids = [
        row["block_id"] for row in figure_rows if row["claimed_by_qids"]
    ]
    unclaimed_figure_ids = [
        row["block_id"] for row in figure_rows if not row["claimed_by_qids"]
    ]

    projections = {
        "version": CONTAINERS_VERSION,
        # Container 01 — chapter learning text (prose/headings). Its figures
        # are the canonical figure blocks no inventory item claims: they ride
        # the teaching prose until the Phase 2.2 placement pass places them.
        "container_01": {
            "census_rows": _census_refs(rows, _CONTAINER_01_CENSUS_KINDS),
            "figure_block_ids": list(unclaimed_figure_ids),
        },
        # Container 02 — activities, info hubs, "do you know?", facts,
        # project work: the pooled hub inventory items, the census blocks
        # the reading ruled activity/info_hub, and the figures those items
        # already claim.
        "container_02": {
            "inventory_qids": list(hub_qids),
            "census_rows": _census_refs(rows, _CONTAINER_02_CENSUS_KINDS),
            "figure_block_ids": list(claimed_figure_ids),
        },
        # Container 03 — every question/assessment identity, wherever it
        # appears (activity-embedded questions included).
        "container_03": {
            "inventory_qids": list(all_qids),
        },
        # Non-destructive dual participation: an _activity_origin item is a
        # Container-02 enrichment AND a Container-03 question with the same
        # QID. The projection lists both memberships instead of choosing.
        "dual_roles": sorted(set(hub_qids) & set(all_qids)),
        # R4: dropped furniture is listed with what it said, never a count.
        "furniture": {
            "chapter_reading": _census_furniture(census),
            "acsd": [
                str(line)
                for line in (canonical or {}).get("dropped_furniture") or []
                if str(line or "").strip()
            ],
        },
        "figure_blocks": copy.deepcopy(figure_rows),
    }
    return projections


def persist_containers(
    artifact_dir: str | Path | None,
    *,
    census: Any,
    canonical: Mapping[str, Any] | None,
    inventory: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Build the projections and persist them beside the instruction set.

    Persistence is best-effort diagnostics (the projections are pure
    functions of durable inputs and can always be rebuilt); the built
    projection is returned either way.
    """
    projections = container_projections(
        census=census, canonical=canonical, inventory=inventory
    )
    if artifact_dir:
        try:
            directory = Path(artifact_dir)
            directory.mkdir(parents=True, exist_ok=True)
            (directory / CONTAINERS_FILENAME).write_text(
                json.dumps(projections, ensure_ascii=False, indent=1),
                encoding="utf-8",
            )
        except OSError:
            pass
    return projections


def load_containers(artifact_dir: str | Path | None) -> dict[str, Any] | None:
    """Read a persisted projection artifact, or None when absent/unreadable."""
    if not artifact_dir:
        return None
    path = Path(artifact_dir) / CONTAINERS_FILENAME
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None

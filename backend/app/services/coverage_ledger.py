"""The coverage ledger: "everything is covered well", made checkable.

The completion test from ``docs/build-concepts-manual-process.md``: at the
end of a run, every item the source contained is accounted for — placed
somewhere, or flagged with a reason — and every shipped concept carries its
learner analysis. This module computes that accounting as a pure function of
the durable job state (question inventory, Type/Case placement ledger,
released records, and — when available — the persisted container
projections and Phase 2.2 placement snapshot), so it can be rebuilt for any
finished or stopped run and shipped in the diagnostics export beside the
run report.

The ledger reports; it does not block. Mid-run gates are a separate concern
(and the process document says what should become of them). What this makes
impossible is *silent* incompleteness: a question, hub, figure or fragment
that reached no output row is named here — canonical figure blocks
included, with their placement / attachment / recorded-disposition state —
as is a concept shipped without its Achieving Mastery line or
Misconception/ Error Analysis section, and every dropped furniture line is
listed with what it said (R4), never reduced to a count.
"""
from __future__ import annotations

import json
import re
from typing import Any, Mapping

from . import containers

LEDGER_VERSION = 2

_TYPE_CASE_LEDGER_KEY = "_type_case_qid_placement_ledger"
# Q1 allotment marker stamped by phase3/assemble.py (kept in lockstep
# with concept_validator.ANALYSIS_ALLOTMENTS_FIELD).
_ANALYSIS_ALLOTMENTS_FIELD = "_aegis_analysis_allotments"
# The pooled hub kind vocabulary lives in ``containers`` (single source);
# this name is a consumer alias, pinned by the lockstep test.
_HUB_KINDS = containers.HUB_INVENTORY_KINDS
_MASTERY_MARK = "Achieving Mastery:"
_ANALYSIS_MARK = "Misconception/ Error Analysis"
_CULMINATION_MARK = "culmination"


def _qid_present(qid: str, text: str) -> bool:
    """Whole-qid match: ``QINV-0002`` never matches inside ``QINV-0002.1``."""
    return bool(re.search(re.escape(qid) + r"(?![.\d])", text))


def _item_rows(
    items: list[dict[str, Any]],
    placed_qids: set[str],
    records_text: str,
    hub_placements: Mapping[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        qid = str(item.get("qid") or "").strip()
        if not qid:
            continue
        kind = str(item.get("source_kind") or "").strip().lower()
        channel = "hub" if kind in _HUB_KINDS else "question"
        if channel == "hub":
            # Hub notes carry private qid markers in the released rows; the
            # Phase 2.2 placement snapshot is a second recorded authority.
            placed = _qid_present(qid, records_text) or (
                qid in hub_placements
            )
        else:
            placed = qid in placed_qids or _qid_present(qid, records_text)
        rows.append({
            "qid": qid,
            "channel": channel,
            "source_kind": kind,
            "status": "placed" if placed else "unaccounted",
            "flag": str(item.get("polish_flag") or ""),
            "parent_qid": str(item.get("parent_qid") or ""),
        })
    return rows


def _figure_rows(
    items: list[dict[str, Any]], records_text: str
) -> list[dict[str, Any]]:
    seen: dict[str, str] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        for url in item.get("image_urls") or []:
            value = str(url or "").strip()
            if value and value not in seen:
                seen[value] = str(item.get("qid") or "")
    return [
        {
            "image_url": url,
            "first_qid": qid,
            "status": "placed" if url in records_text else "unaccounted",
        }
        for url, qid in seen.items()
    ]


def _figure_block_rows(
    figure_blocks: list[Mapping[str, Any]],
    records_text: str,
    figure_placements: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Account for EVERY canonical figure block — the blind spot closes.

    States: ``placed`` (the Phase 2.2 pass placed it on a concept — the
    stamped row marker or the recorded placement says so),
    ``disposition_recorded`` (the pass recorded a disposition instead —
    never silently dropped), ``attached_to_item`` (an inventory item
    already carries its image), ``no_image_evidence`` (the block carries
    no extractable image URL, so no embed is possible), ``unaccounted``.
    """
    rows: list[dict[str, Any]] = []
    for block in figure_blocks:
        if not isinstance(block, Mapping):
            continue
        block_id = str(block.get("block_id") or "")
        if not block_id:
            continue
        urls = [str(url) for url in block.get("image_urls") or [] if url]
        claimed = [
            str(qid) for qid in block.get("claimed_by_qids") or [] if qid
        ]
        verdict = figure_placements.get(block_id)
        if verdict == containers.FIGURE_DISPOSITION:
            status = "disposition_recorded"
        elif verdict or _qid_present(block_id, records_text):
            status = "placed"
        elif claimed:
            status = "attached_to_item"
        elif not urls:
            status = "no_image_evidence"
        else:
            status = "unaccounted"
        rows.append({
            "block_id": block_id,
            "status": status,
            "image_urls": urls,
            "caption": str(block.get("caption") or "")[:300],
            "claimed_by_qids": claimed,
            "disposition": (
                verdict
                if status == "disposition_recorded"
                else ""
            ),
        })
    return rows


def _learner_analysis_rows(
    records: list[Mapping[str, Any]],
    *,
    allotment_aware: bool = False,
) -> list[dict[str, Any]]:
    """Rows missing their Mastery line or their OWED analysis section.

    ``allotment_aware`` is the Q1 contract: the analysis section is
    owed only by rows carrying the ``_aegis_analysis_allotments``
    marker (the chapter inventory allotted them an item); an unallotted
    row without a section is complete. Legacy jobs (no allotment ledger
    anywhere) keep the every-row accounting. Achieving Mastery stays
    owed by every row either way.
    """
    rows: list[dict[str, Any]] = []
    for index, record in enumerate(records, start=1):
        if not isinstance(record, Mapping):
            continue
        text = json.dumps(dict(record), ensure_ascii=False, default=str)
        title = str(
            record.get("concept_title") or record.get("title") or ""
        )
        analysis_owed = (
            bool(record.get(_ANALYSIS_ALLOTMENTS_FIELD))
            if allotment_aware
            else True
        )
        missing = [
            label for label, mark, owed in (
                ("achieving_mastery", _MASTERY_MARK, True),
                (
                    "misconception_error_analysis",
                    _ANALYSIS_MARK,
                    analysis_owed,
                ),
            )
            if owed and mark not in text
        ]
        if not missing:
            continue
        rows.append({
            "row_index": index,
            "concept_title": title[:160],
            "missing": missing,
            "culmination": _CULMINATION_MARK in title.casefold(),
        })
    return rows


def _analysis_item_rows(
    analysis_snapshot: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    """Account every Phase 2.4 inventory item (R4: allotted exactly once).

    The allotments map carries at most one concept per item by
    construction, so the checkable facts are: every LA-id received an
    allotment, and no allotment names an item outside the inventory.
    """
    snapshot = dict(analysis_snapshot or {})
    allotments = {
        str(item_id): str(concept_id)
        for item_id, concept_id in (snapshot.get("allotments") or {}).items()
    }
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in snapshot.get("inventory") or []:
        if not isinstance(item, Mapping):
            continue
        item_id = str(item.get("item_id") or "")
        if not item_id:
            continue
        seen.add(item_id)
        concept_id = allotments.get(item_id, "")
        rows.append({
            "item_id": item_id,
            "kind": str(item.get("kind") or ""),
            "text": str(item.get("text") or "")[:300],
            "status": "allotted" if concept_id else "unaccounted",
            "concept_id": concept_id,
        })
    for item_id in sorted(set(allotments) - seen):
        rows.append({
            "item_id": item_id,
            "kind": "",
            "text": "",
            "status": "unknown_item",
            "concept_id": allotments[item_id],
        })
    return rows


def build_coverage_ledger(
    *,
    question_inventory: Mapping[str, Any] | None,
    records: list[Mapping[str, Any]] | None,
    chapter_reading: Mapping[str, Any] | None = None,
    container_projections: Mapping[str, Any] | None = None,
    place_snapshot: Mapping[str, Any] | None = None,
    analysis_snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Account for every source item and every shipped concept.

    ``container_projections`` is the persisted ``source.containers.json``
    (canonical figure-block evidence + verbatim furniture, both lanes);
    ``place_snapshot`` is the Phase 2.2 placement pass's recorded output
    (``source.phase3-place.json``); ``analysis_snapshot`` the Phase
    2.4/4.3 chapter analysis inventory
    (``source.phase3-analysis.json``, Q1). All are optional — a legacy
    job without them keeps the item/URL accounting (and the legacy
    every-row analysis expectation).
    """
    inventory = dict(question_inventory or {})
    rows = [row for row in (records or []) if isinstance(row, Mapping)]
    items = [
        item for item in inventory.get("items") or []
        if isinstance(item, dict)
    ]
    records_text = json.dumps(rows, ensure_ascii=False, default=str)

    ledger = inventory.get(_TYPE_CASE_LEDGER_KEY)
    placements = (
        ledger.get("placements") if isinstance(ledger, Mapping) else None
    )
    # An unplaced_pending_certification row is a flagged gap the ownership
    # stage recorded on purpose — it must never count as placed.
    placed_qids = {
        str(qid).strip()
        for qid, contract in (placements or {}).items()
        if str(qid or "").strip()
        and not (
            isinstance(contract, Mapping)
            and str(contract.get("basis") or "")
            == "unplaced_pending_certification"
        )
    }

    place = dict(place_snapshot or {})
    hub_placements = dict(place.get("hub_placements") or {})
    figure_placements = dict(place.get("figure_placements") or {})
    projections = dict(container_projections or {})
    figure_blocks = [
        block
        for block in projections.get("figure_blocks") or []
        if isinstance(block, Mapping)
    ]

    item_rows = _item_rows(items, placed_qids, records_text, hub_placements)
    figure_rows = _figure_rows(items, records_text)
    figure_block_rows = _figure_block_rows(
        figure_blocks, records_text, figure_placements
    )
    # Q1: with an allotment ledger (the snapshot, or markers riding the
    # rows themselves), the analysis section is owed only by allotted
    # rows; a legacy job keeps the every-row expectation.
    allotment_aware = analysis_snapshot is not None or any(
        row.get(_ANALYSIS_ALLOTMENTS_FIELD) for row in rows
    )
    analysis_rows = _learner_analysis_rows(
        rows, allotment_aware=allotment_aware
    )
    analysis_item_rows = _analysis_item_rows(analysis_snapshot)

    furniture = projections.get("furniture")
    furniture = dict(furniture) if isinstance(furniture, Mapping) else {}
    dropped_furniture = {
        "chapter_reading": [
            str(line)
            for line in (
                furniture.get("chapter_reading")
                or (chapter_reading or {}).get("dropped_furniture")
                or []
            )
            if str(line or "").strip()
        ],
        "acsd": [
            str(line)
            for line in furniture.get("acsd") or []
            if str(line or "").strip()
        ],
    }

    def channel_counts(channel: str) -> dict[str, int]:
        subset = [row for row in item_rows if row["channel"] == channel]
        placed = sum(1 for row in subset if row["status"] == "placed")
        return {"total": len(subset), "placed": placed,
                "unaccounted": len(subset) - placed}

    figures_placed = sum(
        1 for row in figure_rows if row["status"] == "placed"
    )
    block_status_counts: dict[str, int] = {}
    for row in figure_block_rows:
        block_status_counts[row["status"]] = (
            block_status_counts.get(row["status"], 0) + 1
        )
    normal_missing = [
        row for row in analysis_rows if not row["culmination"]
    ]
    analysis_items_unaccounted = [
        row for row in analysis_item_rows if row["status"] != "allotted"
    ]
    summary = {
        "questions": channel_counts("question"),
        "hubs": channel_counts("hub"),
        "figures": {
            "total": len(figure_rows),
            "placed": figures_placed,
            "unaccounted": len(figure_rows) - figures_placed,
        },
        "figure_blocks": {
            "total": len(figure_block_rows),
            **block_status_counts,
        },
        "released_rows": len(rows),
        "rows_missing_learner_analysis": len(analysis_rows),
        "normal_rows_missing_learner_analysis": len(normal_missing),
        # Q1 (R4): every LA-item accounted — allotted to exactly one
        # concept; an unallotted item is visible incompleteness.
        "learner_analysis_items": {
            "total": len(analysis_item_rows),
            "allotted": len(analysis_item_rows)
            - len(analysis_items_unaccounted),
            "unaccounted": len(analysis_items_unaccounted),
        },
        "flagged_for_review": sum(1 for row in item_rows if row["flag"]),
    }
    complete = (
        summary["questions"]["unaccounted"] == 0
        and summary["hubs"]["unaccounted"] == 0
        and summary["figures"]["unaccounted"] == 0
        and block_status_counts.get("unaccounted", 0) == 0
        and not normal_missing
        and not analysis_items_unaccounted
    )
    reading = dict(chapter_reading or {})
    return {
        "version": LEDGER_VERSION,
        "complete": complete,
        "summary": summary,
        "items": item_rows,
        "figures": figure_rows,
        "figure_blocks": figure_block_rows,
        # R4: the lines themselves, never a bare count. Job-state caps
        # (400 lines / 300 chars) apply upstream; the persisted container
        # projections carry the uncapped lines and win when present.
        "dropped_furniture": dropped_furniture,
        "rows_missing_learner_analysis": analysis_rows,
        "learner_analysis_items": analysis_item_rows,
        "chapter_reading": {
            "provenance": dict(reading.get("provenance") or {}),
            "census_rows": reading.get("census_rows"),
            "dropped_furniture_lines": len(
                reading.get("dropped_furniture") or []
            ),
        } if reading else {},
    }


def render_coverage(ledger: Mapping[str, Any]) -> str:
    """Human-readable COVERAGE section appended to RUN_REPORT.txt."""
    summary = ledger.get("summary") or {}
    lines = ["", "COVERAGE"]
    lines.append(
        "  complete: everything accounted for"
        if ledger.get("complete")
        else "  INCOMPLETE: some source items reached no output row"
    )
    for label, key in (
        ("questions", "questions"), ("hubs", "hubs"), ("figures", "figures"),
    ):
        channel = summary.get(key) or {}
        lines.append(
            f"  {label}: {channel.get('placed', 0)}/{channel.get('total', 0)}"
            " placed"
            + (
                f", {channel['unaccounted']} unaccounted"
                if channel.get("unaccounted") else ""
            )
        )
    blocks = summary.get("figure_blocks") or {}
    if blocks.get("total"):
        lines.append(
            "  figure blocks: "
            + ", ".join(
                f"{blocks.get(status, 0)} {status}"
                for status in (
                    "placed",
                    "attached_to_item",
                    "disposition_recorded",
                    "no_image_evidence",
                    "unaccounted",
                )
                if blocks.get(status)
            )
            + f" of {blocks['total']}"
        )
    lines.append(
        f"  flagged for review: {summary.get('flagged_for_review', 0)}"
    )
    missing = ledger.get("rows_missing_learner_analysis") or []
    if missing:
        lines.append(
            f"  rows missing learner analysis: {len(missing)}"
        )
        for row in list(missing)[:10]:
            suffix = " (culmination)" if row.get("culmination") else ""
            lines.append(
                f"    row {row.get('row_index')} "
                f"{str(row.get('concept_title'))!r} missing "
                f"{', '.join(row.get('missing') or [])}{suffix}"
            )
    else:
        lines.append(
            "  learner analysis: every owed section present "
            "(mastery on every row; analysis on every allotted row)"
        )
    analysis_items = summary.get("learner_analysis_items") or {}
    if analysis_items.get("total"):
        lines.append(
            "  analysis inventory items: "
            f"{analysis_items.get('allotted', 0)}/"
            f"{analysis_items.get('total', 0)} allotted"
            + (
                f", {analysis_items['unaccounted']} unaccounted"
                if analysis_items.get("unaccounted") else ""
            )
        )
        for row in list(ledger.get("learner_analysis_items") or [])[:20]:
            if row.get("status") != "allotted":
                lines.append(
                    f"    unaccounted analysis item {row.get('item_id')} "
                    f"[{row.get('kind')}]: {str(row.get('text'))[:120]!r}"
                )
    unaccounted = [
        row for row in ledger.get("items") or []
        if row.get("status") != "placed"
    ]
    for row in unaccounted[:20]:
        lines.append(
            f"    unaccounted {row.get('channel')}: {row.get('qid')}"
            + (f" [{row['flag']}]" if row.get("flag") else "")
        )
    for row in list(ledger.get("figure_blocks") or [])[:40]:
        if row.get("status") in {"unaccounted", "disposition_recorded"}:
            lines.append(
                f"    figure block {row.get('block_id')}: {row.get('status')}"
                + (
                    f" ({row['disposition']})"
                    if row.get("disposition") else ""
                )
            )
    furniture = ledger.get("dropped_furniture") or {}
    furniture_lines = [
        (lane, line)
        for lane in ("chapter_reading", "acsd")
        for line in furniture.get(lane) or []
    ]
    if furniture_lines:
        lines.append(
            f"  dropped furniture ({len(furniture_lines)} line(s), verbatim):"
        )
        for lane, line in furniture_lines[:40]:
            lines.append(f"    [{lane}] {line}")
        if len(furniture_lines) > 40:
            lines.append(
                f"    … {len(furniture_lines) - 40} more line(s) in "
                "context/coverage_ledger.json"
            )
    return "\n".join(lines) + "\n"

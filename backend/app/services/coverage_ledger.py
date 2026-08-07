"""The coverage ledger: "everything is covered well", made checkable.

The completion test from ``docs/build-concepts-manual-process.md``: at the
end of a run, every item the source contained is accounted for — placed
somewhere, or flagged with a reason — and every shipped concept carries its
learner analysis. This module computes that accounting as a pure function of
the durable job state (question inventory, Type/Case placement ledger,
released records), so it can be rebuilt for any finished or stopped run and
shipped in the diagnostics export beside the run report.

The ledger reports; it does not block. Mid-run gates are a separate concern
(and the process document says what should become of them). What this makes
impossible is *silent* incompleteness: a question, hub, figure or fragment
that reached no output row is named here, as is a concept shipped without
its Achieving Mastery line or Misconception/ Error Analysis section.
"""
from __future__ import annotations

import json
import re
from typing import Any, Mapping

LEDGER_VERSION = 1

_TYPE_CASE_LEDGER_KEY = "_type_case_qid_placement_ledger"
_HUB_KINDS = frozenset({"activity", "experiment_task"})
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
            # Hub notes carry private qid markers in the released rows.
            placed = _qid_present(qid, records_text)
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


def _learner_analysis_rows(
    records: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, record in enumerate(records, start=1):
        if not isinstance(record, Mapping):
            continue
        text = json.dumps(dict(record), ensure_ascii=False, default=str)
        title = str(
            record.get("concept_title") or record.get("title") or ""
        )
        missing = [
            label for label, mark in (
                ("achieving_mastery", _MASTERY_MARK),
                ("misconception_error_analysis", _ANALYSIS_MARK),
            )
            if mark not in text
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


def build_coverage_ledger(
    *,
    question_inventory: Mapping[str, Any] | None,
    records: list[Mapping[str, Any]] | None,
    chapter_reading: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Account for every source item and every shipped concept."""
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
    placed_qids = {
        str(qid).strip()
        for qid in (placements or {})
        if str(qid or "").strip()
    }

    item_rows = _item_rows(items, placed_qids, records_text)
    figure_rows = _figure_rows(items, records_text)
    analysis_rows = _learner_analysis_rows(rows)

    def channel_counts(channel: str) -> dict[str, int]:
        subset = [row for row in item_rows if row["channel"] == channel]
        placed = sum(1 for row in subset if row["status"] == "placed")
        return {"total": len(subset), "placed": placed,
                "unaccounted": len(subset) - placed}

    figures_placed = sum(
        1 for row in figure_rows if row["status"] == "placed"
    )
    normal_missing = [
        row for row in analysis_rows if not row["culmination"]
    ]
    summary = {
        "questions": channel_counts("question"),
        "hubs": channel_counts("hub"),
        "figures": {
            "total": len(figure_rows),
            "placed": figures_placed,
            "unaccounted": len(figure_rows) - figures_placed,
        },
        "released_rows": len(rows),
        "rows_missing_learner_analysis": len(analysis_rows),
        "normal_rows_missing_learner_analysis": len(normal_missing),
        "flagged_for_review": sum(1 for row in item_rows if row["flag"]),
    }
    complete = (
        summary["questions"]["unaccounted"] == 0
        and summary["hubs"]["unaccounted"] == 0
        and summary["figures"]["unaccounted"] == 0
        and not normal_missing
    )
    reading = dict(chapter_reading or {})
    return {
        "version": LEDGER_VERSION,
        "complete": complete,
        "summary": summary,
        "items": item_rows,
        "figures": figure_rows,
        "rows_missing_learner_analysis": analysis_rows,
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
        lines.append("  learner analysis: present on every released row")
    unaccounted = [
        row for row in ledger.get("items") or []
        if row.get("status") != "placed"
    ]
    for row in unaccounted[:20]:
        lines.append(
            f"    unaccounted {row.get('channel')}: {row.get('qid')}"
            + (f" [{row['flag']}]" if row.get("flag") else "")
        )
    return "\n".join(lines) + "\n"

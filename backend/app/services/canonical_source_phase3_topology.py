"""Semantic topology helpers for the Phase 3 source graph.

This module bridges stable graph identities into the legacy concept topology
and Type-allocation stages without making visible heading strings structural
authority. It is intentionally separate from the graph compiler so the
compiler stays a small, auditable evidence boundary.
"""
from __future__ import annotations

import copy
from typing import Any

from . import canonical_source_phase3 as phase3

VERSION = "1.0.0"


def graph_topic_titles(graph: dict[str, Any]) -> list[str]:
    rows = [row for row in graph.get("topics") or [] if isinstance(row, dict)]
    rows.sort(key=lambda row: (
        phase3._as_int(row.get("order"), 0),
        phase3._as_int(row.get("source_start"), 0),
    ))
    return [
        phase3._compact(row.get("title") or "")
        for row in rows
        if phase3._compact(row.get("title") or "")
    ]


def source_topic_excerpts(graph: dict[str, Any]) -> list[dict[str, Any]]:
    """Return graph-bounded source excerpts in immutable textbook order."""
    excerpts: list[dict[str, Any]] = []
    for packet in phase3.topic_packets(graph):
        parts: list[str] = []
        source_block_ids: list[str] = []
        for block in packet.get("blocks") or []:
            if not isinstance(block, dict) or block.get("kind") == "layout":
                continue
            block_id = str(block.get("block_id") or "")
            if block_id:
                source_block_ids.append(block_id)
            text = str(
                block.get("display_text") or block.get("exact_text") or ""
            ).strip()
            if text:
                parts.append(text)
        excerpts.append({
            "topic": str(packet.get("title") or ""),
            "topic_id": str(packet.get("topic_id") or ""),
            "excerpt": "\n\n".join(parts).strip(),
            "source_block_ids": source_block_ids,
            "source_start": int(packet.get("source_start") or 0),
            "source_end": int(packet.get("source_end") or 0),
            "source_order_locked": True,
        })
    return excerpts


def _topic_alias_index(graph: dict[str, Any]) -> tuple[
    dict[str, tuple[str, str, str]], dict[str, dict[str, Any]]
]:
    topics = {
        str(row.get("topic_id") or ""): row
        for row in graph.get("topics") or []
        if isinstance(row, dict) and str(row.get("topic_id") or "")
    }
    candidates: dict[str, set[tuple[str, str, str]]] = {}

    def add(alias: object, topic_id: str, subtopic_id: str = "") -> None:
        key = phase3._normal(alias)
        topic = topics.get(topic_id)
        if not key or topic is None:
            return
        candidates.setdefault(key, set()).add((
            topic_id,
            str(topic.get("title") or ""),
            subtopic_id,
        ))

    for topic_id, topic in topics.items():
        title = str(topic.get("title") or "")
        add(topic_id, topic_id)
        add(title, topic_id)
        number = phase3._as_int(topic.get("number"), 0)
        if number:
            add(f"{number} {title}", topic_id)
            add(f"{number}. {title}", topic_id)

    for subtopic in graph.get("subtopics") or []:
        if not isinstance(subtopic, dict):
            continue
        topic_id = str(subtopic.get("topic_id") or "")
        subtopic_id = str(subtopic.get("subtopic_id") or "")
        title = str(subtopic.get("title") or "")
        add(subtopic_id, topic_id, subtopic_id)
        add(title, topic_id, subtopic_id)
        major = phase3._as_int(subtopic.get("major"), 0)
        minor = phase3._as_int(subtopic.get("minor"), 0)
        if major and minor:
            add(f"{major}.{minor} {title}", topic_id, subtopic_id)

    aliases = {
        key: next(iter(values))
        for key, values in candidates.items()
        if len(values) == 1
    }
    return aliases, topics


def annotate_concept_topics(
    records: list[dict[str, Any]], graph: dict[str, Any]
) -> list[dict[str, Any]]:
    """Resolve model topic/subtopic labels onto stable main-topic IDs.

    For example, ``2.1 The Aristocracy and the New Middle Class`` resolves to
    ``TOPIC-0002`` and the visible main-topic title. Unknown labels remain
    untouched for the existing semantic recovery pass rather than being
    guessed from proximity.
    """
    aliases, topics = _topic_alias_index(graph)
    out: list[dict[str, Any]] = []
    for raw in records or []:
        if not isinstance(raw, dict):
            continue
        row = copy.deepcopy(raw)
        topic_id = str(row.get("_semantic_topic_id") or "")
        subtopic_id = ""
        topic = topics.get(topic_id)
        if topic is None:
            resolved = aliases.get(phase3._normal(row.get("topic") or ""))
            if resolved:
                topic_id, _title, subtopic_id = resolved
                topic = topics.get(topic_id)
        if topic is not None:
            canonical_title = str(topic.get("title") or "")
            old_title = str(row.get("topic") or "")
            if old_title and phase3._normal(old_title) != phase3._normal(canonical_title):
                row.setdefault("_legacy_topic", old_title)
            row["topic"] = canonical_title
            row["_semantic_topic_id"] = topic_id
            if subtopic_id:
                existing = [
                    str(value)
                    for value in row.get("_semantic_subtopic_ids") or []
                    if str(value)
                ]
                if subtopic_id not in existing:
                    existing.append(subtopic_id)
                row["_semantic_subtopic_ids"] = existing
            row["_semantic_topology_version"] = VERSION
        out.append(row)
    return out


def canonicalize_mined_type_topics(
    mined_types: dict[str, Any], graph: dict[str, Any]
) -> dict[str, Any]:
    """Convert QID-derived topic IDs into legacy display hints."""
    result = phase3.annotate_mined_types(mined_types, graph)
    topic_by_id = {
        str(row.get("topic_id") or ""): row
        for row in graph.get("topics") or []
        if isinstance(row, dict)
    }

    def apply_hint(target: dict[str, Any], topic_ids: list[str]) -> None:
        ordered = sorted(
            (
                topic_by_id[topic_id]
                for topic_id in topic_ids
                if topic_id in topic_by_id
            ),
            key=lambda row: phase3._as_int(row.get("order"), 0),
        )
        if not ordered:
            return
        selected = ordered[0]
        old_hint = str(target.get("topic_match_hint") or "")
        new_hint = str(selected.get("title") or "")
        if old_hint and phase3._normal(old_hint) != phase3._normal(new_hint):
            target.setdefault("_legacy_topic_match_hint", old_hint)
        target["topic_match_hint"] = new_hint
        target["_semantic_source_topic_id"] = str(
            selected.get("topic_id") or ""
        )
        target["_source_topic_evidence"] = new_hint

    for mtype in result.get("types") or []:
        if not isinstance(mtype, dict):
            continue
        apply_hint(
            mtype,
            [str(value) for value in mtype.get("canonical_topic_ids") or []],
        )
        for case in mtype.get("case_prompts") or []:
            if not isinstance(case, dict):
                continue
            ids = [
                str(value)
                for value in case.get("canonical_topic_ids") or []
                if str(value)
            ]
            if not ids and case.get("canonical_topic_id"):
                ids = [str(case.get("canonical_topic_id"))]
            apply_hint(case, ids)
    return result


def canonicalize_assignment_units(
    units: list[dict[str, Any]], graph: dict[str, Any]
) -> list[dict[str, Any]]:
    """Give every expanded Case unit its own QID-derived source topic."""
    topic_by_id = {
        str(row.get("topic_id") or ""): row
        for row in graph.get("topics") or []
        if isinstance(row, dict)
    }
    out: list[dict[str, Any]] = []
    for raw in units or []:
        if not isinstance(raw, dict):
            continue
        unit = copy.deepcopy(raw)
        ids: list[str] = []
        cases = [
            case
            for case in unit.get("case_prompts") or []
            if isinstance(case, dict)
        ]
        if len(cases) == 1:
            case = cases[0]
            ids = [
                str(value)
                for value in case.get("canonical_topic_ids") or []
                if str(value)
            ]
            if not ids and case.get("canonical_topic_id"):
                ids = [str(case.get("canonical_topic_id"))]
        if not ids:
            ids = [
                str(value)
                for value in unit.get("canonical_topic_ids") or []
                if str(value)
            ]
        ordered = sorted(
            (
                topic_by_id[topic_id]
                for topic_id in ids
                if topic_id in topic_by_id
            ),
            key=lambda row: phase3._as_int(row.get("order"), 0),
        )
        if ordered:
            selected = ordered[0]
            old_hint = str(unit.get("topic_match_hint") or "")
            new_hint = str(selected.get("title") or "")
            if old_hint and phase3._normal(old_hint) != phase3._normal(new_hint):
                unit.setdefault("_legacy_topic_match_hint", old_hint)
            unit["topic_match_hint"] = new_hint
            unit["_source_topic_evidence"] = new_hint
            unit["_semantic_source_topic_id"] = str(
                selected.get("topic_id") or ""
            )
            if len(cases) == 1:
                cases[0]["topic_match_hint"] = new_hint
                cases[0]["_semantic_source_topic_id"] = unit[
                    "_semantic_source_topic_id"
                ]
        out.append(unit)
    return out

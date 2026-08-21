"""Q19 Track A pinned: the early inventory track equals the sequential build.

The source-only half of the inventory (chunk extraction, completeness
verdicts, anchors, figures, qid numbering) forks beside the concept
passes; the finish half (topic assignment, then adjudication — same
order as ever) is the join. The fork must be invisible in the output:
same items, same qids, same stats. Halt-both (Q19): an aborted track
stops before its next chunk and surfaces loudly, never silently.
"""
from __future__ import annotations

import pytest

from app.services import generation as g

from tests.test_ap_concept_regressions import _ap_mmd


def _scripted_openai(monkeypatch):
    def fake(system, user, **kwargs):
        if "inventory-completeness reviewer" in system:
            return {"verdict": "complete", "reason": ""}
        return {"items": []}

    monkeypatch.setattr(g, "_openai_json", fake)


def test_forked_extraction_matches_the_sequential_build(monkeypatch):
    _scripted_openai(monkeypatch)
    meta = g._metadata(subject="Mathematics")
    sections = g.parse_mmd_sections(_ap_mmd())

    sequential = g._extract_question_task_inventory_via_api(
        meta=meta, sections=sections,
    )

    track = g._EarlyInventoryTrack(meta=meta, sections=sections)
    pre_joined, anchors = track.join()
    track.halt()  # idempotent after a clean join
    forked = g._finish_inventory_with_topics(
        pre_joined, anchors, meta=meta, sections=sections, records=None,
    )

    assert forked == sequential


def test_should_abort_stops_before_the_next_chunk(monkeypatch):
    _scripted_openai(monkeypatch)
    with pytest.raises(g._EarlyInventoryHalted):
        g._extract_inventory_pre_join(
            meta=g._metadata(subject="Mathematics"),
            sections=g.parse_mmd_sections(_ap_mmd()),
            should_abort=lambda: True,
        )


def test_a_track_extraction_failure_surfaces_at_the_join(monkeypatch):
    def broken(system, user, **kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(g, "_openai_json", broken)
    track = g._EarlyInventoryTrack(
        meta=g._metadata(subject="Mathematics"),
        sections=g.parse_mmd_sections(_ap_mmd()),
    )
    with pytest.raises(RuntimeError, match="provider down"):
        track.join()
    track.halt()

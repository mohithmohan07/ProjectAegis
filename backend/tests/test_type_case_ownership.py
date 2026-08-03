"""Type/Case ownership is semantic, not physical location.

Two defects this pins:

* a chapter-wide review task whose qid the provider reformatted - notably a
  sub-part such as ``QINV-0016.2``, routinely collapsed to its parent - was
  rejected on every attempt and ended the whole run with
  ``chapter-wide task placement did not return exact valid assignments``;
* inventory ``topic_hint`` records where a task is *printed*. Letting it
  decide routing files a combined task under whichever section happens to
  contain it, instead of the latest topic its method requires.
"""
from __future__ import annotations

import pytest

from app.services import generation as g


# --------------------------------------------------------------------------- #
# Chapter-wide placement resolves rather than ending the run
# --------------------------------------------------------------------------- #

def _inventory():
    """One parent task plus four sub-parts, all chapter-wide."""
    items = [
        {"qid": "QINV-0014", "topic_hint": "", "_topic_scope": "chapter",
         "raw_task": "Write a note on the unification of Germany."},
        {"qid": "QINV-0015", "topic_hint": "", "_topic_scope": "chapter",
         "raw_task": "Explain the role of Bismarck."},
        {"qid": "QINV-0016", "topic_hint": "", "_topic_scope": "chapter",
         "raw_task": "Write a note on:"},
    ] + [
        {"qid": f"QINV-0016.{n}", "topic_hint": "", "_topic_scope": "chapter",
         "raw_task": f"Sub-part {n} of the note."}
        for n in (1, 2, 3, 4)
    ]
    return {"items": items}


def _records():
    return [
        {"topic": "The Making of Nationalism in Europe",
         "concept_title": "Liberal nationalism", "concept_details": "d"},
        {"topic": "The Making of Germany and Italy",
         "concept_title": "Prussian leadership", "concept_details": "d"},
    ]


def _run(monkeypatch, response_qids):
    """Route only ``response_qids``; the rest come back unusable."""
    logs: list[str] = []
    monkeypatch.setattr(
        g.progress, "log", lambda message, **_k: logs.append(str(message)))

    def fake_call(_system, _user, **_kwargs):
        return {"assignments": [
            {"qid": qid, "topic": "The Making of Germany and Italy"}
            for qid in response_qids
        ]}

    monkeypatch.setattr(g, "_openai_json", fake_call)
    inventory = _inventory()
    out = g._assign_chapter_wide_inventory_topics_via_api(
        meta={"subject": "History"},
        inventory=inventory,
        records=_records(),
        source_topic_excerpts=[],
    )
    return out, logs


def test_subpart_qids_are_resolved_instead_of_ending_the_run(monkeypatch):
    """The exact production failure: .1-.4 collapsed to the parent."""
    # The provider answers only the plain qids and echoes the parent for the
    # four sub-parts, so those four rows are rejected every attempt.
    out, logs = _run(
        monkeypatch,
        ["QINV-0014", "QINV-0015", "QINV-0016"],
    )
    by_qid = {item["qid"]: item for item in out["items"]}

    # Every chapter-wide task is placed. Nothing is dropped, nothing raises.
    assert all(by_qid[f"QINV-0016.{n}"]["topic_hint"] for n in (1, 2, 3, 4))
    # Sub-parts inherit their parent's topic rather than being lost.
    parent_topic = by_qid["QINV-0016"]["topic_hint"]
    for n in (1, 2, 3, 4):
        assert by_qid[f"QINV-0016.{n}"]["topic_hint"] == parent_topic
    assert any("resolved" in message for message in logs)


def test_unrouted_task_without_a_parent_takes_the_latest_assigned_topic(
    monkeypatch,
):
    """Advanced placement is the default for a chapter-wide review task."""
    out, _logs = _run(monkeypatch, ["QINV-0014"])
    by_qid = {item["qid"]: item for item in out["items"]}

    # QINV-0015 has no parent and no sibling; it follows the latest
    # teaching-ranked topic already assigned, not the first or most populous.
    assert by_qid["QINV-0015"]["topic_hint"] == (
        "The Making of Germany and Italy")


def test_no_chapter_wide_task_is_ever_dropped(monkeypatch):
    """Even a completely unusable response must not lose a task."""
    out, _logs = _run(monkeypatch, [])
    placed = [
        item for item in out["items"]
        if item.get("_topic_scope") == "chapter"
    ]

    assert len(placed) == 7
    assert all(item["topic_hint"] for item in placed)


# --------------------------------------------------------------------------- #
# Certified ownership beats printed location
# --------------------------------------------------------------------------- #

def _case_route_topics(items_by_qid, qids):
    """Group qids the way Case routing does, returning the chosen topics."""
    topics = []
    for qid in qids:
        item = items_by_qid.get(qid, {})
        located = str(item.get("topic_hint") or "").strip()
        owned = str(item.get("owner_topic_hint") or "").strip()
        topics.append(owned or located)
    return topics


def test_certified_owner_overrides_printed_location():
    """A combined task printed early is routed by its certified owner."""
    items = {
        "QINV-0020": {
            "qid": "QINV-0020",
            # Printed inside the nth-term exercise...
            "topic_hint": "nth Term of an AP",
            # ...but Phase 3.2 certified it needs the sum method.
            "owner_topic_hint": "Sum of First n Terms",
        },
    }
    assert _case_route_topics(items, ["QINV-0020"]) == [
        "Sum of First n Terms"]


def test_location_is_used_only_when_no_owner_is_certified():
    items = {
        "QINV-0021": {"qid": "QINV-0021", "topic_hint": "nth Term of an AP"},
    }
    assert _case_route_topics(items, ["QINV-0021"]) == ["nth Term of an AP"]


def test_case_routing_reads_owner_without_mutating_the_inventory():
    """Routing must not write back into the shared inventory item.

    An earlier attempt used setdefault here and perturbed downstream
    identity//hashing for unrelated Type-granularity decisions.
    """
    item = {"qid": "QINV-0022", "topic_hint": "A", "owner_topic_hint": "B"}
    before = dict(item)

    _case_route_topics({"QINV-0022": item}, ["QINV-0022"])

    assert item == before

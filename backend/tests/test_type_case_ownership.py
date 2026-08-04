"""Chapter-wide task placement resolves rather than ending the run.

A chapter-wide review task whose qid the provider reformatted - notably a
sub-part such as ``QINV-0016.2``, routinely collapsed to its parent - was
rejected on every attempt and ended the whole run with ``chapter-wide task
placement did not return exact valid assignments``. That was the last live
production stop the founder reported.

Placement of a chapter-wide task is a routing decision over topics that are
already certified, so it has a deterministic answer and never needs to end a
run. Ownership itself is certified separately, by
``_type_case_contract_for_qid``; these tests deliberately do not restate
that contract.
"""
from __future__ import annotations

import copy

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


def test_resolution_never_mutates_the_records_it_routes_against():
    """Routing reads the topology; it must not write back into it.

    An earlier attempt used setdefault on shared inventory items here and
    perturbed downstream identity hashing for unrelated Type-granularity
    decisions.
    """
    records = _records()
    before = copy.deepcopy(records)
    inventory = _inventory()

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(g.progress, "log", lambda *_a, **_k: None)
        patch.setattr(
            g, "_openai_json",
            lambda *_a, **_k: {"assignments": []},
        )
        g._assign_chapter_wide_inventory_topics_via_api(
            meta={"subject": "History"},
            inventory=inventory,
            records=records,
            source_topic_excerpts=[],
        )

    assert records == before

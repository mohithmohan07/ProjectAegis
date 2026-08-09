"""Golden gate 2: Host reproduces job 23's certified host and qid maps.

Job 23's Phase 3.3 certified 19 assignment units against existing
concepts and mapped 19 QIDs (fragments included) to their hosts before
the run died. Two of those certifications were stale by run end — they
referenced first-freeze rows that the mid-run semantic repair superseded
— so the recorded fixture separates them, and the gate asserts the 17
still-valid entries exactly. The new architecture cannot produce that
staleness at all: hosting runs after Settle, and the mechanical checker
refuses any host that is not a settled concept title (proven below by
the fabricated-title test).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.services.phase3 import envelope as envelope_mod
from app.services.phase3 import host as host_mod
from app.services.phase3 import kernel, settle

GOLDEN = Path(__file__).parent / "golden"


def _normal(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


@pytest.fixture(scope="module")
def golden_envelope() -> dict:
    return envelope_mod.load(GOLDEN / "rne_envelope.json")


@pytest.fixture(scope="module")
def settled_rows() -> list[dict]:
    return json.loads(
        (GOLDEN / "rne_settled_rows.json").read_text(encoding="utf-8")
    )["records"]


@pytest.fixture(scope="module")
def golden_hosts() -> dict:
    return json.loads(
        (GOLDEN / "rne_host_maps.json").read_text(encoding="utf-8")
    )


def _replay_provider(
    golden_hosts: dict,
    settled_rows: list[dict],
):
    """Answer each unit with its recorded golden host.

    Every golden QID names its host; a unit inherits the host of any of
    its QIDs. Units the old run never certified (it died first) get a
    valid same-topic settled host — the gate asserts nothing about them.
    """

    golden_qids = golden_hosts["qid_map"]
    by_topic: dict[str, str] = {}
    for row in settled_rows:
        by_topic.setdefault(
            str(row.get("_semantic_topic_id")), row["concept_title"]
        )

    def provider(request: dict) -> dict:
        assignments = []
        for unit in request["units"]:
            title = None
            for qid in unit.get("qids") or []:
                if qid in golden_qids:
                    title = golden_qids[qid]["concept_title"]
                    break
            if title is None:
                title = by_topic.get(
                    str(unit.get("topic_id")),
                    settled_rows[0]["concept_title"],
                )
            assignments.append({
                "unit_id": unit["unit_id"],
                "decision": "existing",
                "host_concept_title": title,
                "confidence": 0.99,
                "reason": "replayed from the golden host maps",
            })
        return {"assignments": assignments}

    return provider


def _verified_critic(_request: dict) -> dict:
    return {"verdict": "verified", "confidence": 0.999, "issues": []}


def test_host_reproduces_job_23s_certified_maps(
    golden_envelope, settled_rows, golden_hosts,
):
    provider = _replay_provider(golden_hosts, settled_rows)

    result = host_mod.host(
        golden_envelope,
        settled_rows,
        provider=provider,
        critic=_verified_critic,
        store=kernel.DecisionStore(),
    )

    # Every certified golden QID keeps its exact host.
    for qid, golden in golden_hosts["qid_map"].items():
        produced = result["qid_map"].get(qid)
        assert produced is not None, f"{qid} lost its host"
        assert _normal(produced["concept_title"]) == _normal(
            golden["concept_title"]
        ), qid
        assert _normal(produced["parent_concept"]) == _normal(
            golden["parent_concept"]
        ), qid
        assert produced["topic_id"] == golden["topic_id"], qid

    # Every golden case-unit agrees too (the old pipeline keyed one
    # caseless unit as a bare TYPE id; its QIDs are covered above).
    for unit_id, golden in golden_hosts["host_map"].items():
        if "::" not in unit_id:
            continue
        produced = result["host_map"].get(unit_id)
        assert produced is not None, f"{unit_id} was not decided"
        assert _normal(produced["concept_title"]) == _normal(
            golden["concept_title"]
        ), unit_id
        assert produced["topic_id"] == golden["topic_id"], unit_id

    # Replay certifies against existing rows only: nothing new is created.
    assert result["new_concepts"] == []
    # Every derived unit was decided.
    assert len(result["host_map"]) == len(
        host_mod.derive_units(golden_envelope)
    )


def test_host_resume_is_free_and_identical(
    golden_envelope, settled_rows, golden_hosts,
):
    provider = _replay_provider(golden_hosts, settled_rows)
    calls = {"n": 0}

    def counted(request: dict) -> dict:
        calls["n"] += 1
        return provider(request)

    store = kernel.DecisionStore()
    first = host_mod.host(
        golden_envelope, settled_rows,
        provider=counted, critic=_verified_critic, store=store,
    )
    after_first = calls["n"]
    second = host_mod.host(
        golden_envelope, settled_rows,
        provider=counted, critic=_verified_critic, store=store,
    )

    assert calls["n"] == after_first
    assert second == first


def test_a_fabricated_host_title_fails_closed_after_bounded_corrections(
    golden_envelope, settled_rows,
):
    calls = {"n": 0}

    def fabricating(request: dict) -> dict:
        calls["n"] += 1
        return {"assignments": [
            {
                "unit_id": unit["unit_id"],
                "decision": "existing",
                "host_concept_title": "A Concept That Does Not Exist",
                "confidence": 0.99,
                "reason": "fabricated",
            }
            for unit in request["units"]
        ]}

    with pytest.raises(kernel.ContractError) as failed:
        host_mod.host(
            golden_envelope, settled_rows,
            provider=fabricating, critic=_verified_critic,
            store=kernel.DecisionStore(),
        )

    assert calls["n"] == 3
    assert "not a settled concept title" in str(failed.value)


def test_critic_dissent_flags_the_host_entries(
    golden_envelope, settled_rows, golden_hosts,
):
    provider = _replay_provider(golden_hosts, settled_rows)

    def dissenting(_request: dict) -> dict:
        return {
            "verdict": "rejected",
            "confidence": 0.93,
            "issues": ["unit hosts look interchangeable across cases"],
        }

    result = host_mod.host(
        golden_envelope, settled_rows,
        provider=provider, critic=dissenting, store=kernel.DecisionStore(),
    )

    assert all(
        any("interchangeable" in flag for flag in entry["review_flags"])
        for entry in result["host_map"].values()
    )

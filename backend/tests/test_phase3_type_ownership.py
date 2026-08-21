"""Q14: one concept owns each Type (owner ruling, 21 Aug 2026).

The Host pass certifies hosts per Case; when one Type's Cases certify
onto different concepts, ``host.consolidate_type_ownership`` takes ONE
recorded ownership verdict per split Type and moves every Case and QID
of that Type to the owner — flagged, decide-once, Fixer-backed. A Type
whose Cases share one host costs nothing. The assemble audit gains the
matching mechanical detector (``duplicate_type_identity``) for any lane
that bypasses the consolidation.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.phase3 import assemble
from app.services.phase3 import envelope as envelope_mod
from app.services.phase3 import host as host_mod
from app.services.phase3 import kernel

GOLDEN = Path(__file__).parent / "golden"


@pytest.fixture(scope="module")
def env() -> dict:
    return envelope_mod.load(GOLDEN / "rne_envelope.json")


@pytest.fixture(scope="module")
def settled_rows() -> list[dict]:
    return json.loads(
        (GOLDEN / "rne_settled_rows.json").read_text(encoding="utf-8")
    )["records"]


def _split_type(env: dict) -> tuple[str, list[dict]]:
    """The first mined Type with at least two assignment units."""
    units_by_type: dict[str, list[dict]] = {}
    for unit in host_mod.derive_units(env):
        units_by_type.setdefault(str(unit["type_id"]), []).append(unit)
    for type_id, units in units_by_type.items():
        if len(units) >= 2:
            return type_id, units
    raise AssertionError("golden envelope has no multi-case type")


def _entry(row: dict) -> dict:
    return {
        "decision": "existing",
        "concept_title": str(row["concept_title"]),
        "parent_concept": str(row.get("parent_concept") or ""),
        "topic": str(row.get("topic") or ""),
        "topic_id": str(row.get("_semantic_topic_id") or ""),
        "confidence": 0.95,
    }


def _hosts_with_split(env: dict, settled_rows: list[dict]) -> tuple[
    dict, str, list[dict], dict, dict,
]:
    """A hosts dict whose first multi-case Type spans two concepts."""
    type_id, units = _split_type(env)
    # Two settled concepts with DIFFERENT identities.
    first = settled_rows[0]
    second = next(
        row for row in settled_rows[1:]
        if (
            str(row.get("_semantic_topic_id")),
            str(row["concept_title"]).casefold(),
        ) != (
            str(first.get("_semantic_topic_id")),
            str(first["concept_title"]).casefold(),
        )
    )
    host_map: dict[str, dict] = {}
    qid_map: dict[str, dict] = {}
    for index, unit in enumerate(units):
        row = first if index == 0 else second
        host_map[str(unit["unit_id"])] = _entry(row)
        for qid in unit["qids"]:
            qid_map[qid] = {**_entry(row), "decision": "api_placement"}
    hosts = {
        "host_map": host_map,
        "qid_map": qid_map,
        "new_concepts": [{"concept_title": "Untouched creation"}],
    }
    return hosts, type_id, units, dict(_entry(first)), dict(_entry(second))


def test_a_split_type_moves_whole_to_one_owner(env, settled_rows):
    hosts, type_id, units, first, second = _hosts_with_split(
        env, settled_rows
    )
    calls: list[dict] = []

    def owner_provider(request: dict) -> dict:
        calls.append(request)
        assert request["stage"] == "host.type_owner"
        titles = [
            row["concept_title"]
            for row in request["candidate_concepts"]
        ]
        assert first["concept_title"] in titles
        assert second["concept_title"] in titles
        return {
            "type_id": request["type"]["type_id"],
            "owner_concept_title": second["concept_title"],
            "confidence": 0.93,
            "reason": "the Type as a whole exercises this teaching",
        }

    store = kernel.DecisionStore()
    out = host_mod.consolidate_type_ownership(
        env, hosts,
        provider=owner_provider,
        critic=lambda payload: {
            "verdict": "verified", "confidence": 0.95, "issues": [],
        },
        store=store,
    )

    assert len(calls) == 1, "one ownership verdict per split Type"
    owner_key = (second["topic_id"], second["concept_title"].casefold())
    for unit in units:
        entry = out["host_map"][str(unit["unit_id"])]
        assert (
            entry["topic_id"], entry["concept_title"].casefold()
        ) == owner_key
        for qid in unit["qids"]:
            moved = out["qid_map"][qid]
            assert (
                moved["topic_id"], moved["concept_title"].casefold()
            ) == owner_key
    # The move is recorded, never silent: the units that changed carry
    # the Q14 flag, and so do their questions.
    moved_units = [
        out["host_map"][str(unit["unit_id"])]
        for unit in units
        if str(unit["unit_id"]) in hosts["host_map"]
        and hosts["host_map"][str(unit["unit_id"])]["concept_title"]
        != second["concept_title"]
    ]
    assert moved_units
    for entry in moved_units:
        assert any(
            "Q14" in flag for flag in entry.get("review_flags") or []
        )
    assert out["new_concepts"] == hosts["new_concepts"]

    # Decide-once: replay hits the store, never the provider.
    replayed = host_mod.consolidate_type_ownership(
        env, hosts,
        provider=lambda request: (_ for _ in ()).throw(
            AssertionError("replay called the provider")
        ),
        critic=None,
        store=store,
    )
    assert len(calls) == 1
    assert replayed["host_map"] == out["host_map"]


def test_a_type_on_one_concept_costs_nothing(env, settled_rows):
    type_id, units = _split_type(env)
    row = settled_rows[0]
    hosts = {
        "host_map": {
            str(unit["unit_id"]): _entry(row) for unit in units
        },
        "qid_map": {},
        "new_concepts": [],
    }
    out = host_mod.consolidate_type_ownership(
        env, hosts,
        provider=lambda request: (_ for _ in ()).throw(
            AssertionError("a single-host type must not decide")
        ),
        critic=None,
        store=kernel.DecisionStore(),
    )
    assert out["host_map"] == hosts["host_map"]


def test_an_owner_outside_the_candidates_is_refused_to_the_fixer(
    env, settled_rows,
):
    hosts, type_id, units, first, second = _hosts_with_split(
        env, settled_rows
    )
    fixer_calls: list[dict] = []

    def bad_provider(request: dict) -> dict:
        return {
            "type_id": request["type"]["type_id"],
            "owner_concept_title": "A Concept That Does Not Exist",
            "confidence": 0.9,
            "reason": "fabricated",
        }

    def fixer(request: dict) -> dict:
        fixer_calls.append(request)
        return {
            "type_id": type_id,
            "owner_concept_title": first["concept_title"],
            "confidence": 0.5,
            "reason": "fixer: the recorded best-judgment owner",
        }

    out = host_mod.consolidate_type_ownership(
        env, hosts,
        provider=bad_provider,
        critic=None,
        store=kernel.DecisionStore(),
        fixer=fixer,
    )
    assert fixer_calls, "exhausted corrections route to The Fixer (Q13)"
    owner_key = (first["topic_id"], first["concept_title"].casefold())
    for unit in units:
        entry = out["host_map"][str(unit["unit_id"])]
        assert (
            entry["topic_id"], entry["concept_title"].casefold()
        ) == owner_key


def test_assemble_audit_names_a_type_spanning_concepts():
    records = [
        {
            "concept_title": "Concept A",
            "_aegis_release_type_case_routes": [
                "TYPE-0001::CASE-0001::QINV-0001",
            ],
        },
        {
            "concept_title": "Concept B",
            "_aegis_release_type_case_routes": [
                "TYPE-0001::CASE-0002::QINV-0002",
            ],
        },
    ]
    findings = assemble.audit_case_uniqueness(records)
    codes = {finding["code"] for finding in findings}
    assert "duplicate_type_identity" in codes
    finding = next(
        f for f in findings if f["code"] == "duplicate_type_identity"
    )
    assert finding["row_indexes"] == [0, 1]
    assert "Q14" in finding["message"]

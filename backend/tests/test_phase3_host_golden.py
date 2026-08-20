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
                # Golden-era decisions predate the routing rule: each
                # question fell under its host concept alone, so the API
                # placement keeps it on that exact concept.
                "qid_placements": {
                    qid: {
                        "falls_under": [
                            golden_qids[qid]["concept_title"]
                            if qid in golden_qids
                            else title
                        ],
                        "destination_concept_title": (
                            golden_qids[qid]["concept_title"]
                            if qid in golden_qids
                            else title
                        ),
                        "reason": "single-concept question",
                    }
                    for qid in unit.get("qids") or []
                },
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
    golden_envelope, settled_rows, monkeypatch,
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

    # Pin sequential batches so the call count isolates the per-decision
    # attempt budget: the shipped default is now parallel (workers 6), and
    # under the pool the sibling batches already in flight legitimately
    # finish their own bounded attempts before the fail-fast cancel lands.
    monkeypatch.setenv("AEGIS_PHASE3_DECISION_WORKERS", "1")
    with pytest.raises(kernel.ContractError) as failed:
        host_mod.host(
            golden_envelope, settled_rows,
            provider=fabricating, critic=_verified_critic,
            store=kernel.DecisionStore(),
        )

    assert calls["n"] == 3
    assert "not a settled concept title" in str(failed.value)


def test_host_requests_carry_the_citable_source_blocks(
    golden_envelope, settled_rows, golden_hosts,
):
    """Job 24 failed closed because create_new had no blocks to cite."""
    inner = _replay_provider(golden_hosts, settled_rows)
    seen: list[dict] = []

    def provider(request: dict) -> dict:
        seen.append(request)
        return inner(request)

    host_mod.host(
        golden_envelope, settled_rows,
        provider=provider, critic=_verified_critic,
        store=kernel.DecisionStore(),
    )

    assert seen
    for request in seen:
        blocks = request["source_blocks"]
        assert blocks and all(row["block_id"] for row in blocks)
        assert any(row["text"] for row in blocks)


def test_a_qid_cited_as_a_source_block_gets_the_question_id_hint(
    golden_envelope, settled_rows,
):
    def qid_citing(request: dict) -> dict:
        return {"assignments": [
            {
                "unit_id": unit["unit_id"],
                "decision": "create_new",
                "confidence": 0.99,
                "reason": "fabricated grounding",
                "new_concept": {
                    "concept_title": "A Brand New Concept",
                    "parent_concept": "Methods",
                    "concept_details": (
                        "Description: A new idea.\nAchieving Mastery: Do it."
                    ),
                    "keywords": "new",
                    "source_block_ids": ["QINV-0014"],
                },
            }
            for unit in request["units"]
        ]}

    with pytest.raises(kernel.ContractError) as failed:
        host_mod.host(
            golden_envelope, settled_rows,
            provider=qid_citing, critic=_verified_critic,
            store=kernel.DecisionStore(),
        )

    message = str(failed.value)
    assert "QINV-0014" in message
    assert "question ids" in message


def _routing_fixture():
    topic_order = {"TOPIC-0001": 0, "TOPIC-0002": 1}
    a1 = {"concept_title": "Alpha", "_semantic_topic_id": "TOPIC-0001"}
    a2 = {"concept_title": "Beta", "_semantic_topic_id": "TOPIC-0001"}
    b1 = {"concept_title": "Gamma", "_semantic_topic_id": "TOPIC-0002"}
    culms = {
        "TOPIC-0001": {
            "concept_title": "Culmination - One",
            "_semantic_topic_id": "TOPIC-0001",
        },
        "TOPIC-0002": {
            "concept_title": "Culmination - Two",
            "_semantic_topic_id": "TOPIC-0002",
        },
    }
    return topic_order, a1, a2, b1, culms


def test_question_routing_single_concept_keeps_the_question():
    topic_order, a1, _a2, _b1, culms = _routing_fixture()
    row, rule = host_mod._route_question(
        [a1], topic_order=topic_order, culmination_by_topic=culms
    )
    assert row is a1 and rule == "single_concept"


def test_question_routing_same_topic_multi_concept_goes_to_culmination():
    topic_order, a1, a2, _b1, culms = _routing_fixture()
    row, rule = host_mod._route_question(
        [a1, a2], topic_order=topic_order, culmination_by_topic=culms
    )
    assert row["concept_title"] == "Culmination - One"
    assert rule == "same_topic_culmination"


def test_question_routing_two_concepts_across_topics_goes_to_later_concept():
    topic_order, a1, _a2, b1, culms = _routing_fixture()
    row, rule = host_mod._route_question(
        [a1, b1], topic_order=topic_order, culmination_by_topic=culms
    )
    assert row is b1 and rule == "later_topic_concept"


def test_question_routing_many_concepts_across_topics_goes_to_later_culmination():
    topic_order, a1, a2, b1, culms = _routing_fixture()
    row, rule = host_mod._route_question(
        [a1, a2, b1], topic_order=topic_order, culmination_by_topic=culms
    )
    assert row["concept_title"] == "Culmination - Two"
    assert rule == "later_topic_culmination"


def test_question_routing_duplicate_titles_do_not_inflate_membership():
    topic_order, a1, _a2, _b1, culms = _routing_fixture()
    row, rule = host_mod._route_question(
        [a1, dict(a1)], topic_order=topic_order, culmination_by_topic=culms
    )
    assert rule == "single_concept"
    assert row["concept_title"] == "Alpha"


def _topic_layout(settled_rows):
    by_topic: dict[str, list[dict]] = {}
    culm_by_topic: dict[str, dict] = {}
    for row in settled_rows:
        topic_id = str(row.get("_semantic_topic_id"))
        title = str(row.get("concept_title") or "")
        if title.lower().startswith("culmination"):
            culm_by_topic.setdefault(topic_id, row)
        else:
            by_topic.setdefault(topic_id, []).append(row)
    return by_topic, culm_by_topic


def _placement_provider(pair, destination_title):
    def provider(request: dict) -> dict:
        return {"assignments": [
            {
                "unit_id": u["unit_id"],
                "decision": "existing",
                "host_concept_title": pair[0]["concept_title"],
                "confidence": 0.99,
                "reason": "test",
                "qid_placements": {
                    qid: {
                        "falls_under": [
                            pair[0]["concept_title"],
                            pair[1]["concept_title"],
                        ],
                        "destination_concept_title": destination_title,
                        "reason": "test placement",
                    }
                    for qid in u.get("qids") or []
                },
            }
            for u in request["units"]
        ]}

    return provider


def test_the_api_places_questions_and_culminations_are_valid_destinations(
    golden_envelope, settled_rows,
):
    """The model applies the house rules itself: a two-concept same-topic
    question it places on the topic's culmination row is accepted, with
    no disagreement flag from the advisory cross-check."""
    units = host_mod.derive_units(golden_envelope)
    unit = next(u for u in units if u["qids"])
    by_topic, culm_by_topic = _topic_layout(settled_rows)
    topic_id, pair = next(
        (tid, rows) for tid, rows in by_topic.items()
        if len(rows) >= 2 and tid in culm_by_topic
    )
    culmination_title = culm_by_topic[topic_id]["concept_title"]

    result = host_mod.host(
        golden_envelope, settled_rows,
        provider=_placement_provider(pair, culmination_title),
        critic=_verified_critic,
        store=kernel.DecisionStore(),
    )
    placed = result["qid_map"][unit["qids"][0]]
    assert placed["decision"] == "api_placement"
    assert _normal(placed["concept_title"]) == _normal(culmination_title)
    assert placed["falls_under"] == [
        _normal(pair[0]["concept_title"]),
        _normal(pair[1]["concept_title"]),
    ]
    assert not any(
        "literal reading" in flag
        for flag in placed.get("review_flags") or []
    )


def test_host_payload_grounds_placement_in_each_questions_source_position(
    golden_envelope, settled_rows,
):
    """Reviewers found visual questions clustering into the first image
    concept: the request must carry each question's own wording, kind, and
    printed location, and the rules must weigh them."""
    seen: dict = {}
    units = host_mod.derive_units(golden_envelope)
    unit = next(u for u in units if u["qids"])
    by_topic, culm_by_topic = _topic_layout(settled_rows)
    topic_id, pair = next(
        (tid, rows) for tid, rows in by_topic.items()
        if len(rows) >= 2 and tid in culm_by_topic
    )
    culmination_title = culm_by_topic[topic_id]["concept_title"]
    inner = _placement_provider(pair, culmination_title)

    def provider(request: dict) -> dict:
        seen.setdefault("requests", []).append(request)
        return inner(request)

    host_mod.host(
        golden_envelope, settled_rows,
        provider=provider,
        critic=_verified_critic,
        store=kernel.DecisionStore(),
    )
    request = next(
        r for r in seen["requests"]
        if any(u["unit_id"] == unit["unit_id"] for u in r["units"])
    )
    sent_unit = next(
        u for u in request["units"] if u["unit_id"] == unit["unit_id"]
    )
    assert sent_unit["questions"], "units must carry per-question evidence"
    entry = sent_unit["questions"][0]
    assert set(entry) >= {
        "qid", "kind", "printed_under_topic", "chapter_wide", "text",
    }
    assert entry["qid"] in unit["qids"]
    rules = request["rules"]
    assert "printed_under_topic" in rules
    assert "Surface similarity is NOT ownership" in rules


def test_an_api_placement_disagreeing_with_the_rules_ships_flagged(
    golden_envelope, settled_rows,
):
    """The API's placement stands (decide-once), but a placement that a
    literal reading of the rules would send elsewhere carries a review
    flag naming the expected destination."""
    units = host_mod.derive_units(golden_envelope)
    unit = next(u for u in units if u["qids"])
    by_topic, culm_by_topic = _topic_layout(settled_rows)
    topic_id, pair = next(
        (tid, rows) for tid, rows in by_topic.items()
        if len(rows) >= 2 and tid in culm_by_topic
    )

    result = host_mod.host(
        golden_envelope, settled_rows,
        provider=_placement_provider(pair, pair[0]["concept_title"]),
        critic=_verified_critic,
        store=kernel.DecisionStore(),
    )
    placed = result["qid_map"][unit["qids"][0]]
    assert placed["decision"] == "api_placement"
    assert _normal(placed["concept_title"]) == _normal(
        pair[0]["concept_title"]
    )
    assert any(
        "literal reading" in flag and "same_topic_culmination" in flag
        for flag in placed.get("review_flags") or []
    )


def test_a_fabricated_destination_fails_closed(
    golden_envelope, settled_rows,
):
    units = host_mod.derive_units(golden_envelope)
    by_topic, culm_by_topic = _topic_layout(settled_rows)
    _topic_id, pair = next(
        (tid, rows) for tid, rows in by_topic.items()
        if len(rows) >= 2 and tid in culm_by_topic
    )

    with pytest.raises(kernel.ContractError) as failed:
        host_mod.host(
            golden_envelope, settled_rows,
            provider=_placement_provider(
                pair, "A Destination That Does Not Exist"
            ),
            critic=_verified_critic,
            store=kernel.DecisionStore(),
        )
    assert "destination" in str(failed.value)


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

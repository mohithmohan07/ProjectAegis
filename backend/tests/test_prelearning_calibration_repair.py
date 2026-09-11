"""Job 129 shapes: assumed graph skills, substantive scope and policy replay."""
from __future__ import annotations

import copy

import pytest

from app.services import generation, generation_quality_policy as quality
from app.services import generation_repair_policy as repair
from app.services import prelearning_authority_v2 as authority
from app.services import prelearning_capture_policy as capture
from app.services.phase3 import envelope, kernel, preanalyse, prelearn, premap
from tests import test_phase3_premap as map_fixtures
from tests import test_phase3_settle_golden as golden


def _env():
    return {
        "envelope_sha256": "e" * 64,
        "metadata": {
            capture.KEY: capture.VERSION,
            capture.BOUNDARY_KEY: capture.BOUNDARY_VERSION,
            quality.KEY: quality.VERSION,
            repair.KEY: repair.VERSION,
            "subject": "English", "grade": "10",
            "chapter_title": "A scientific biography",
        },
    }


def _graph_capture():
    return {
        "captures": {"analyse": [{
            "prerequisite_id": "PR-0001",
            "text": "Read a graph's axis labels to distinguish duration from count.",
            "evidence": ["LA-0003", "BLK-00047"],
            "rationale": "The biography assumes reading its graph without teaching axes.",
        }]},
        "prerequisites": [],
        "stage_flags": {"merge": [
            "LA-0003 lacks an external earlier-year curriculum record."
        ]},
        "evidence_packets": {
            "settle": {"source_blocks": [{
                "block_id": "BLK-00047",
                "text": "The graph reports counts collected during a six-week period.",
                "source_context": {"axis_labels": ["Number collected", "Kinds"]},
            }, {
                "block_id": "BLK-00089",
                "text": "The chapter introduces a new theory explaining a cell process.",
            }]},
            "analyse": {"analysis_inventory": [{
                "item_id": "LA-0003",
                "text": "Mistaking the six-week period for the quantity count.",
                "evidence": ["BLK-00047"],
            }]},
        },
    }


def _graph_response(merged):
    _, demands = authority._evidence(merged, complete_demands=True)
    return {
        "atoms": [{
            "atom_id": "PA-0001",
            "text": "Use labelled graph axes to identify a represented quantity.",
            "capture_refs": ["analyse:PR-0001"],
            "evidence": ["BLK-00047", "LA-0003"],
            "rationale": "The source assumes this familiar representation; prior status is inferred in the supplied Grade 10 context, not a documented school-year claim.",
        }],
        "prerequisites": [{
            "prerequisite_id": "PR-0001", "atoms": ["PA-0001"],
            "text": "Interpret quantities using a graph's labelled axes.",
            "rationale": "A bounded, independently assessable skill needed to read the source.",
        }],
        "dispositions": [],
        "demand_coverage": [{
            "demand_ref": row["demand_ref"],
            "prerequisite_ids": [] if row["evidence_id"] == "BLK-00089" else ["PR-0001"],
            "rationale": "This theory is new chapter teaching." if row["evidence_id"] == "BLK-00089"
            else "The source assumes interpreting the graph's labels.",
        } for row in demands],
        "review_resolutions": [{
            "concern_id": "PC-0001", "atom_ids": ["PA-0001"],
            "rationale": "No external curriculum record is required for this explicit, source-based inference of prior capability.",
        }],
    }


def test_graph_assumption_reaches_both_reviewers_and_new_authority_keeps_substantive_scope(tmp_path):
    env, merged = _env(), _graph_capture()
    response = _graph_response(merged)
    authors, critics = [], []

    def author(payload):
        authors.append(copy.deepcopy(payload))
        return copy.deepcopy(response)

    def critic(payload):
        critics.append(copy.deepcopy(payload))
        return {"verdict": "verified", "confidence": 0.99, "issues": []}

    result = authority.adjudicate(
        env, merged, provider=author, critic=critic,
        store=kernel.DecisionStore(tmp_path / "graph"),
    )
    assert authors[0]["chapter"]["subject"] == "English"
    assert authors[0]["chapter"]["grade"] == "10"
    assert "axis_labels" in str(authors[0]["evidence_index"]["BLK-00047"])
    assert critics[0]["evidence_index"] == authors[0]["evidence_index"]
    assert "documentary provenance was incorrectly made mandatory" in critics[0]["rules"]
    assert result["prerequisites"][0]["text"] == response["prerequisites"][0]["text"]
    assert result["prerequisites"][0]["retained_atoms"][0]["text"] == response["atoms"][0]["text"]
    assert result["demand_coverage"][1]["prerequisite_ids"] == []
    assert result[repair.KEY] == repair.VERSION
    assert result["adjudication"]["policy_version"].endswith(repair.VERSION)
    assert "external earlier-year curriculum" in result["stage_flags"]["merge"][0]


def test_new_calibration_does_not_reuse_or_rewrite_historical_authority(tmp_path):
    env, merged = _env(), _graph_capture()
    env["metadata"].pop(repair.KEY)
    response = _graph_response(merged)
    calls = []

    def author(payload):
        calls.append(copy.deepcopy(payload))
        return copy.deepcopy(response)

    store = kernel.DecisionStore(tmp_path / "replay")
    historical = authority.adjudicate(env, merged, provider=author, store=store)
    historical_copy = copy.deepcopy(historical)
    env["metadata"][repair.KEY] = repair.VERSION
    current = authority.adjudicate(env, merged, provider=author, store=store)
    authority.adjudicate(env, merged, provider=author, store=store)
    assert len(calls) == 2
    assert repair.KEY not in calls[0]
    assert capture.REPAIR_INSTRUCTION not in calls[0]["rules"]
    assert capture.REPAIR_INSTRUCTION in calls[1]["rules"]
    assert historical == historical_copy
    assert historical["adjudication"]["decision_key"] != current["adjudication"]["decision_key"]
    assert repair.KEY not in historical


@pytest.mark.parametrize("adapter", [
    prelearn._live_capture, prelearn._live_merge, prelearn._live_critic,
    authority._live_author, authority._live_critic,
    premap._live_map, premap._live_links, premap._live_critic,
    premap._live_empty_capture, premap._live_empty_capture_critic,
    preanalyse._live_build, preanalyse._live_critic,
])
def test_calibration_reaches_live_author_and_independent_critic_systems(monkeypatch, adapter):
    calls = []
    monkeypatch.setattr(generation, "_openai_json", lambda system, user, **kw: calls.append(system) or {})
    adapter({repair.KEY: repair.VERSION})
    adapter({})
    assert capture.REPAIR_INSTRUCTION in calls[0]
    assert capture.REPAIR_INSTRUCTION not in calls[1]
    assert "concrete knowledge or an observable" in calls[0]
    assert "Grade 1 foundations small, familiar and single-demand" in calls[0]


@pytest.mark.parametrize("empty", [False, True])
def test_calibration_survives_pre_map_including_an_api_supported_empty_map(tmp_path, empty):
    env = envelope.load(golden.GOLDEN / "rne_envelope.json")
    env["metadata"].update({
        quality.KEY: quality.VERSION, repair.KEY: repair.VERSION,
        capture.KEY: capture.VERSION,
    })
    env["envelope_sha256"] = envelope.seal_sha256(env)
    requests = []
    base = map_fixtures._provider()

    def provider(payload):
        requests.append(copy.deepcopy(payload))
        if empty:
            return {"verdict": "assumes_nothing", "rationale": "The source teaches the representation and assumes no separate prior skill."}
        return base(payload)

    result = map_fixtures._build(
        env, prerequisites={"prerequisites": []} if empty else map_fixtures.PREREQUISITES,
        provider=provider, store=kernel.DecisionStore(tmp_path / "map"),
    )
    assert result[repair.KEY] == repair.VERSION
    assert all(request[repair.KEY] == repair.VERSION for request in requests)
    assert capture.REPAIR_INSTRUCTION in requests[0]["rules"]
    if empty:
        assert result["rows"] == []
        assert result["pre_lane_verdict"]["verdict"] == "assumes_nothing"
    else:
        assert len(result["rows"]) == 2
        assert result["rows"][0][premap.PREREQUISITES_FIELD][0]["text"] == map_fixtures.PREREQUISITES["prerequisites"][0]["text"]

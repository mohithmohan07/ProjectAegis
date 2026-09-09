"""Atomic Pre authority retains source meaning without imposing a concept quota."""
from __future__ import annotations

import copy

import pytest

from app.services import prelearning_authority_v2 as authority
from app.services import prelearning_capture_policy as policy
from app.services.phase3 import kernel


def _env() -> dict:
    return {
        "envelope_sha256": "e" * 64,
        "metadata": {
            policy.KEY: policy.VERSION,
            "subject": "Mathematics",
            "grade": "10",
            "chapter_title": "Statistics",
        },
    }


def _merged() -> dict:
    return {
        "captures": {
            "settle": [{
                "prerequisite_id": "PR-0001",
                "text": "Read table entries and add the recorded whole numbers.",
                "evidence": ["CON-0001", "BLK-0001"],
                "rationale": "The mean example assumes both capabilities.",
            }],
        },
        "prerequisites": [{
            "prerequisite_id": "PR-0001",
            "text": "Tabulation and calculation.",
            "captures": ["settle:PR-0001"],
            "rationale": "A broad suggestion, subject to final adjudication.",
        }],
        "stage_flags": {
            "host": ["UNIT-0001 assumes finding an unknown addend; inspect this omission."],
        },
        "review_flags": {},
        "evidence_packets": {
            "settle": {
                "source_blocks": [{
                    "block_id": "BLK-0001",
                    "text": "Read the table's entries, add them and divide by the count.",
                }],
                "settled_concepts": [{
                    "concept_id": "CON-0001",
                    "concept_title": "Finding an arithmetic mean",
                    "concept_details": "Description: A mean uses the sum and count of the data.",
                    "source_block_ids": ["BLK-0001"],
                }],
            },
            "host": {
                "type_case_units": [{
                    "unit_id": "UNIT-0001",
                    "type_id": "TYPE-0001",
                    "case_id": "CASE-0001",
                    "qids": ["QINV-0001"],
                    "description": "Find an absent data value from the stated mean.",
                }],
                "questions": [{
                    "qid": "QINV-0001",
                    "text": "The mean of 4, 6 and an unknown value is 5. Find the value.",
                }],
            },
        },
    }


def _payload() -> dict:
    return {
        "captures": [{"capture_ref": "settle:PR-0001"}],
        "evidence_index": {
            "BLK-0001": [], "CON-0001": [], "UNIT-0001": [], "QINV-0001": [],
        },
        "source_demands": [
            {"demand_ref": "settle:CON-0001"},
            {"demand_ref": "host:UNIT-0001"},
        ],
        "prior_review_concerns": [{"concern_id": "PC-0001"}],
    }


def _split_response() -> dict:
    return {
        "atoms": [
            {
                "atom_id": "PA-0001",
                "text": "Read a value from a row and column in a simple table.",
                "capture_refs": ["settle:PR-0001"],
                "evidence": ["CON-0001", "BLK-0001"],
                "rationale": "The example assumes earlier learning about tables.",
            },
            {
                "atom_id": "PA-0002",
                "text": "Add whole numbers accurately.",
                "capture_refs": ["settle:PR-0001"],
                "evidence": ["BLK-0001", "QINV-0001"],
                "rationale": "Whole-number addition is distinct from reading a table.",
            },
        ],
        "prerequisites": [
            {
                "prerequisite_id": "PR-0001",
                "text": "Reading values from a table",
                "atoms": ["PA-0001"],
                "rationale": "This capability is independently teachable and diagnosable.",
            },
            {
                "prerequisite_id": "PR-0002",
                "text": "Adding whole numbers",
                "atoms": ["PA-0002"],
                "rationale": "Addition has a distinct learning objective and mastery check.",
            },
        ],
        "dispositions": [],
        "demand_coverage": [
            {
                "demand_ref": "settle:CON-0001",
                "prerequisite_ids": ["PR-0001", "PR-0002"],
                "rationale": "Reading entries and adding them support the mean example.",
            },
            {
                "demand_ref": "host:UNIT-0001",
                "prerequisite_ids": ["PR-0002"],
                "rationale": "Addition supports the task; the prior concern needs review.",
            },
        ],
        "review_resolutions": [{
            "concern_id": "PC-0001",
            "atom_ids": [],
            "rationale": "The model must state why no additional atom is needed.",
        }],
    }


def _with_recovery() -> dict:
    response = _split_response()
    response["atoms"].append({
        "atom_id": "PA-0003",
        "text": "Find an unknown addend from a known total.",
        "capture_refs": [],
        "evidence": ["QINV-0001", "UNIT-0001"],
        "rationale": "The task assumes an earlier-grade skill missed by the stage captures.",
    })
    response["prerequisites"].append({
        "prerequisite_id": "PR-0003",
        "text": "Finding an unknown addend",
        "atoms": ["PA-0003"],
        "rationale": "The task and earlier critic identify this distinct prerequisite.",
    })
    response["demand_coverage"][1]["prerequisite_ids"].append("PR-0003")
    response["review_resolutions"][0].update(
        atom_ids=["PA-0003"],
        rationale="Recovered the omitted unknown-addend skill from the cited task.",
    )
    return response


def test_one_broad_capture_can_support_two_distinct_prerequisites():
    response = _split_response()

    assert authority.checker(_payload())(response) == []
    assert len(response["prerequisites"]) == 2
    assert all(atom["capture_refs"] == ["settle:PR-0001"] for atom in response["atoms"])


def test_source_supported_omission_can_be_recovered_without_a_capture_parent():
    response = _with_recovery()

    assert authority.checker(_payload())(response) == []
    assert response["atoms"][2]["capture_refs"] == []
    assert response["review_resolutions"][0]["atom_ids"] == ["PA-0003"]


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        (
            lambda response: [atom.update(capture_refs=[]) for atom in response["atoms"]],
            "unaccounted parent captures",
        ),
        (
            lambda response: response["prerequisites"].pop(),
            "every atom must be retained or disposed exactly once",
        ),
        (
            lambda response: response["prerequisites"][0]["atoms"].append("PA-0002"),
            "every atom must be retained or disposed exactly once",
        ),
        (
            lambda response: response["atoms"][0].update(evidence=["BLK-NOT-SUPPLIED"]),
            "evidence names unknown IDs",
        ),
        (
            lambda response: response["atoms"][0].update(evidence=[]),
            "evidence is empty",
        ),
        (
            lambda response: response["demand_coverage"].pop(),
            "demand_coverage must account for every supplied ID exactly once",
        ),
        (
            lambda response: response.update(review_resolutions=[]),
            "review_resolutions must account for every supplied ID exactly once",
        ),
        (
            lambda response: response["atoms"][0].update(capture_refs=["host:PR-UNKNOWN"]),
            "capture_refs names unknown IDs",
        ),
    ],
    ids=[
        "lost-parent", "unowned-atom", "double-ownership", "invented-evidence",
        "unsupported-atom", "unreviewed-demand", "ignored-critic", "invented-parent",
    ],
)
def test_atomic_contract_rejects_provenance_or_coverage_loss(mutation, expected):
    response = _split_response()
    mutation(response)

    defects = authority.checker(_payload())(response)

    assert any(expected in defect for defect in defects), defects


def test_retaining_and_disposing_parts_of_one_capture_preserves_both_parts():
    merged = _merged()
    merged["captures"]["settle"][0]["text"] += " Apply the newly taught mean formula."
    response = _split_response()
    response["atoms"].append({
        "atom_id": "PA-0003",
        "text": "Calculate an arithmetic mean using its formula.",
        "capture_refs": ["settle:PR-0001"],
        "evidence": ["CON-0001", "BLK-0001"],
        "rationale": "This third part of the broad capture is current-chapter teaching.",
    })
    response["dispositions"].append({
        "disposition_id": "PD-0001",
        "classification": "chapter_taught",
        "atoms": ["PA-0003"],
        "rationale": "The source introduces the mean formula in this chapter.",
    })

    result = authority.adjudicate(
        _env(), merged, provider=lambda _: copy.deepcopy(response),
        store=kernel.DecisionStore(),
    )

    assert [row["atoms"] for row in result["prerequisites"]] == [["PA-0001"], ["PA-0002"]]
    assert result["dispositions"][0]["atoms"] == ["PA-0003"]
    assert result["dispositions"][0]["captures"] == ["settle:PR-0001"]
    assert result["dispositions"][0]["evidence"] == ["CON-0001", "BLK-0001"]
    assert result["dispositions"][0]["stages"] == ["settle"]
    assert result["adjudication"]["disposed_atom_count"] == 1
    assert result["adjudication"]["atom_count"] == 3


def test_full_adjudication_preserves_provenance_and_replays_after_reopening(tmp_path):
    merged = _merged()
    original = copy.deepcopy(merged)
    response = _with_recovery()
    author_calls, critic_calls = [], []

    def author(request):
        author_calls.append(copy.deepcopy(request))
        return copy.deepcopy(response)

    def critic(request):
        critic_calls.append(copy.deepcopy(request))
        return {
            "verdict": "dissent", "confidence": 0.96,
            "issues": ["PR-0003: confirm the prior-grade provenance of this skill."],
        }

    result = authority.adjudicate(
        _env(), merged, provider=author, critic=critic,
        store=kernel.DecisionStore(tmp_path),
    )
    resumed = authority.adjudicate(
        _env(), merged, provider=author, critic=critic,
        store=kernel.DecisionStore(tmp_path),
    )

    assert len(author_calls) == len(critic_calls) == 1
    assert resumed == result
    assert merged == original
    assert result["captures"] == original["captures"]
    assert result["atoms"] == response["atoms"]
    assert result["demand_coverage"] == response["demand_coverage"]
    assert result["review_resolutions"] == response["review_resolutions"]
    assert result["prerequisites"][0]["captures"] == ["settle:PR-0001"]
    assert result["prerequisites"][1]["captures"] == ["settle:PR-0001"]
    assert result["prerequisites"][2]["captures"] == []
    assert result["prerequisites"][2]["evidence"] == ["QINV-0001", "UNIT-0001"]
    assert result["adjudication"]["capture_count"] == 1
    assert result["adjudication"]["prerequisite_count"] == 3
    assert result["adjudication"]["recovered_atom_count"] == 1
    assert any("PR-0003" in flag for flag in result["review_flags"]["PR-0003"])
    request = author_calls[0]
    assert {row["demand_ref"] for row in request["source_demands"]} == {
        "settle:CON-0001", "host:UNIT-0001",
    }
    assert request["prior_review_concerns"] == [{
        "concern_id": "PC-0001", "origin": "host", "issue": merged["stage_flags"]["host"][0],
    }]
    assert request["evidence_index"]["QINV-0001"][1]["content"] == (
        merged["evidence_packets"]["host"]["questions"][0]
    )
    assert critic_calls[0]["proposed_decision"] == response
    assert critic_calls[0]["evidence_index"] == request["evidence_index"]
    assert critic_calls[0]["prior_review_concerns"] == request["prior_review_concerns"]


def test_missing_figure_is_visible_to_both_reviewers_and_retained_as_a_flag():
    merged = _merged()
    missing_url = "https://unavailable.example.invalid/source-table.jpg"
    merged["evidence_packets"]["host"]["questions"][0]["image_urls"] = [missing_url]
    author_calls, critic_calls = [], []

    def author(request):
        author_calls.append(copy.deepcopy(request))
        return _with_recovery()

    def critic(request):
        critic_calls.append(copy.deepcopy(request))
        return {"verdict": "verified", "confidence": 1.0, "issues": []}

    result = authority.adjudicate(
        _env(), merged, provider=author, critic=critic, store=kernel.DecisionStore(),
    )

    bound_images = author_calls[0]["visual_evidence"]["images"]
    assert len(bound_images) == 1
    assert bound_images[0]["source_url"] == missing_url
    assert bound_images[0]["state"] == "unavailable"
    assert critic_calls[0]["visual_evidence"]["images"] == bound_images
    assert any(
        "concept_visual_evidence_unavailable" in flag and missing_url in flag
        for flag in result["stage_flags"]["adjudication"]
    )
    assert all(
        any(missing_url in flag for flag in flags)
        for flags in result["review_flags"].values()
    )
    assert len(result["prerequisites"]) == 3


def test_durable_snapshot_restores_atomic_provenance_and_coverage(tmp_path):
    from app.services import canonical_source_phase3, concept_topology_contract
    from app.services.phase3 import runner

    store_dir = tmp_path / "decisions"
    result = authority.adjudicate(
        _env(), _merged(), provider=lambda _: _with_recovery(),
        store=kernel.DecisionStore(store_dir),
    )

    status = runner._snapshot_prelearn(result, store_dir)
    assert status["state"] == "written"
    with canonical_source_phase3.activate_session({"artifact_dir": tmp_path}):
        restored = concept_topology_contract.restored_prerequisites()

    assert restored == result
    assert restored["atoms"] == _with_recovery()["atoms"]
    assert restored["demand_coverage"] == _with_recovery()["demand_coverage"]
    assert restored["review_resolutions"] == _with_recovery()["review_resolutions"]
    assert restored["evidence_packets"] == _merged()["evidence_packets"]
    assert restored["prerequisites"][2]["captures"] == []
    assert restored["prerequisites"][2]["evidence"] == ["QINV-0001", "UNIT-0001"]

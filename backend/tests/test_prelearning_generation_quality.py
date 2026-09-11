"""Fresh Pre coverage keeps source omissions visible through question planning."""
from __future__ import annotations

import copy

import pytest

from app.services import generation, generation_quality_policy as quality
from app.services import prelearning_authority_v2 as authority
from app.services import prelearning_capture_policy as capture
from app.services.phase3 import envelope, kernel, pre_coverage, prelearn, premap, prequestions
from tests import test_phase3_premap as map_fixtures
from tests import test_phase3_settle_golden as golden
from tests import test_prelearning_atomic_authority as authority_fixtures
from tests import test_prelearning_complete_evidence as evidence_fixtures


def _fresh_env():
    env = evidence_fixtures._env()
    env["metadata"][quality.KEY] = quality.VERSION
    return env


def test_canonical_block_without_graph_owner_still_reaches_capture_and_pre_map():
    env = _fresh_env()
    orphan = {
        "block_id": "BLK-2", "kind": "table", "display_text": "table preview",
        "display_text_with_visuals": "Complete standalone source table",
        "table_data": {"headers": ["Value", "Count"], "rows": [[7, 3]]},
    }
    env["canonical"]["blocks"].append(orphan)
    before = copy.deepcopy(env)
    blocks = prelearn.stage_evidence(env, "settle")["source_blocks"]
    assert [row["block_id"] for row in blocks] == ["BLK-1", "BLK-2"]
    assert blocks[-1]["text"] == orphan["display_text_with_visuals"]
    assert blocks[-1]["source_context"]["table_data"] == orphan["table_data"]
    mapped = premap.map_evidence(env, {"prerequisites": [{
        "prerequisite_id": "PR-0001", "text": "Read a row and a column",
        "source_block_ids": ["BLK-2"],
    }]})
    assert mapped["source_blocks"][0]["text"] == orphan["display_text_with_visuals"]
    assert env == before
    env["metadata"].pop(quality.KEY)
    assert [row["block_id"] for row in prelearn.stage_evidence(env, "settle")["source_blocks"]] == ["BLK-1"]


def test_authority_audits_each_evidence_address_and_recovers_uncaptured_foundamental(tmp_path):
    env = authority_fixtures._env()
    env["metadata"][quality.KEY] = quality.VERSION
    merged = authority_fixtures._merged()
    merged["evidence_packets"]["place"] = {
        "pooled_hub_items": [{"item_ref": "HUB-1", "text": "Read the table key."}],
        "pooled_figures": [{"item_ref": "FIG-1", "caption": "The key uses familiar symbols."}],
    }
    merged["evidence_packets"]["analyse"] = {
        "analysis_inventory": [{"item_id": "LA-1", "text": "Confusing the total and one addend."}],
    }
    response = authority_fixtures._with_recovery()
    _, demands = authority._evidence(merged, complete_demands=True)
    response["demand_coverage"] = [{
        "demand_ref": row["demand_ref"],
        "prerequisite_ids": ["PR-0003"] if row["evidence_id"] == "QINV-0001" else [],
        "rationale": "The task assumes the missing addend." if row["evidence_id"] == "QINV-0001"
        else "This evidence introduces chapter teaching or repeats an already reviewed demand.",
    } for row in demands]
    authors, critics = [], []

    def author(payload):
        authors.append(copy.deepcopy(payload))
        return copy.deepcopy(response)

    def critic(payload):
        critics.append(copy.deepcopy(payload))
        return {"verdict": "verified", "confidence": 0.99, "issues": []}

    result = authority.adjudicate(
        env, merged, provider=author, critic=critic,
        store=kernel.DecisionStore(tmp_path / "authority"),
    )
    assert {row["demand_ref"] for row in authors[0]["source_demands"]} == {
        "settle:source_blocks:BLK-0001", "settle:settled_concepts:CON-0001",
        "host:type_case_units:UNIT-0001", "host:questions:QINV-0001",
        "place:pooled_hub_items:HUB-1", "place:pooled_figures:FIG-1",
        "analyse:analysis_inventory:LA-1",
    }
    assert critics[0]["source_demands"] == authors[0]["source_demands"]
    assert critics[0]["evidence_index"] == authors[0]["evidence_index"]
    assert result["adjudication"]["recovered_atom_count"] == 1
    assert result["prerequisites"][2]["retained_atoms"] == [{
        "atom_id": response["atoms"][2]["atom_id"],
        "text": response["atoms"][2]["text"],
    }]
    incomplete = copy.deepcopy(response)
    incomplete["demand_coverage"].pop()
    assert any("every supplied ID" in defect for defect in authority.checker(authors[0])(incomplete))
    # Legacy atomic authority remains addressed by its original two demand IDs.
    assert authority._evidence(merged)[1] == [
        {"demand_ref": "settle:CON-0001", "evidence_id": "CON-0001"},
        {"demand_ref": "host:UNIT-0001", "evidence_id": "UNIT-0001"},
    ]


def test_empty_audit_reads_complete_table_and_binds_actual_pixels(monkeypatch, tmp_path):
    env = _fresh_env()
    url, _ = evidence_fixtures._pinned(monkeypatch, tmp_path)
    block = env["canonical"]["blocks"][0]
    block["image_urls"] = [url]
    block["owner_qid"] = "QINV-1"
    seen = []

    def author(payload):
        seen.append(copy.deepcopy(payload))
        return {"verdict": "assumes_nothing", "rationale": "This table teaches its own representation."}

    result = premap.empty_capture_verdict(
        env, ["QINV-1"], provider=author, critic=None,
        store=kernel.DecisionStore(tmp_path / "empty"), fixer=None,
    )
    request = seen[0]
    assert result["verdict"] == "assumes_nothing"
    assert request["evidence"]["source_blocks"][0]["text"] == block["display_text_with_visuals"]
    assert "QINV-1" not in str(request)
    assert request["visual_evidence"]["images"][0]["source_url"] == url
    assert prelearn._vision_kwargs(request)["image_urls"][0].startswith("data:image/")
    env["metadata"].pop(quality.KEY)
    legacy = premap.empty_capture_evidence(env, ["QINV-1"])
    assert legacy["source_blocks"][0]["text"] == "plain preview"
    assert "source_context" not in legacy["source_blocks"][0]


def test_authority_can_recover_from_entirely_empty_capture_without_inventing_a_quota(tmp_path):
    env = authority_fixtures._env()
    env["metadata"][quality.KEY] = quality.VERSION
    merged = {
        "captures": {}, "prerequisites": [],
        "evidence_packets": {"settle": {"source_blocks": [{
            "block_id": "BLK-1", "text": "The worked example assumes reading a row and column.",
        }, {"block_id": "BLK-2", "text": "The chapter introduces the new definition of mean."}]}},
    }
    response = {
        "atoms": [{
            "atom_id": "PA-0001", "text": "Read a value at a row and column intersection.",
            "capture_refs": [], "evidence": ["BLK-1"],
            "rationale": "The example assumes the representation without teaching it.",
        }],
        "prerequisites": [{
            "prerequisite_id": "PR-0001", "text": "Reading a simple table",
            "atoms": ["PA-0001"], "rationale": "The supplied earlier-learning demand supports this capability.",
        }],
        "dispositions": [], "review_resolutions": [],
        "demand_coverage": [{
            "demand_ref": "settle:source_blocks:BLK-1", "prerequisite_ids": ["PR-0001"],
            "rationale": "The source assumes reading table entries.",
        }, {
            "demand_ref": "settle:source_blocks:BLK-2", "prerequisite_ids": [],
            "rationale": "This definition is current chapter teaching.",
        }],
    }
    result = authority.adjudicate(
        env, merged, provider=lambda _: copy.deepcopy(response),
        store=kernel.DecisionStore(tmp_path / "uncaptured"),
    )
    assert result["adjudication"]["capture_count"] == 0
    assert result["adjudication"]["recovered_atom_count"] == 1
    assert len(result["prerequisites"]) == 1
    assert result["demand_coverage"][1]["prerequisite_ids"] == []


@pytest.mark.parametrize("call", [premap._live_empty_capture, premap._live_empty_capture_critic])
def test_empty_audit_live_adapters_send_bound_image_inputs(monkeypatch, call):
    observed = {}
    monkeypatch.setattr(premap.visual_evidence, "image_inputs", lambda _: ["data:image/png;base64,AA=="])
    monkeypatch.setattr(generation, "_openai_json", lambda system, user, **kwargs: observed.update(kwargs) or {})
    call({quality.KEY: quality.VERSION})
    assert observed["image_urls"] == ["data:image/png;base64,AA=="]


def test_retained_atoms_survive_map_and_plan_without_importing_source_tasks(tmp_path):
    env = envelope.load(golden.GOLDEN / "rne_envelope.json")
    env["metadata"].update({
        quality.KEY: quality.VERSION, capture.KEY: capture.VERSION,
        pre_coverage.RULE_FIELD: pre_coverage.owner_rule(),
    })
    env["envelope_sha256"] = envelope.seal_sha256(env)
    prerequisites = copy.deepcopy(map_fixtures.PREREQUISITES)
    atom_records = []
    for position, row in enumerate(prerequisites["prerequisites"], 1):
        atoms = [{"atom_id": f"PA-{position:04d}", "text": row["text"]}]
        row["retained_atoms"] = atoms
        atom_records.append(atoms)
    mapped = map_fixtures._build(
        env, prerequisites=prerequisites, store=kernel.DecisionStore(tmp_path / "map"),
    )
    assert mapped[quality.KEY] == quality.VERSION
    assert [row[premap.PREREQUISITES_FIELD][0]["retained_atoms"] for row in mapped["rows"]] == atom_records
    requests, critics = [], []

    def provider(payload):
        requests.append(copy.deepcopy(payload))
        if payload["stage"] == "prequestions.plan":
            return {"plans": [{
                "pre_concept_id": row["pre_concept_id"], "total": 1,
                "split": [{"tier": "Basic", "count": 1}],
                "rationale": "The single check diagnoses " + row["prerequisites"][0]["retained_atoms"][0]["atom_id"],
            } for row in payload["pre_concepts"]]}
        return {"questions": [{
            "question_id": "PRQ-0001", "question_text": "What does a map key tell the reader?",
            "answer": "The meaning of the symbols on the map.", "tier": "Basic",
            "rationale": "This checks the retained prerequisite's diagnostic scope.",
        }]}

    def critic(payload):
        critics.append(copy.deepcopy(payload))
        return {"verdict": "verified", "confidence": 0.99, "issues": []}

    result = prequestions.build(
        env, mapped, provider=provider, author_provider=provider, critic=critic,
        store=kernel.DecisionStore(tmp_path / "questions"),
    )
    assert not result["blocked"]
    plan = next(row for row in requests if row["stage"] == "prequestions.plan")
    assert [row["prerequisites"][0]["retained_atoms"] for row in plan["pre_concepts"]] == atom_records
    authors = [row for row in requests if row["stage"] == "prequestions.author"]
    assert len(authors) == 2
    assert all(row["pre_concept"]["prerequisites"][0]["retained_atoms"] for row in authors)
    assert all("retained_atoms" in str(row) for row in critics)
    assert all("QINV-" not in str(row) and "source_blocks" not in row for row in requests)
    assert all(row[quality.KEY] == quality.VERSION for row in requests)
    assert "Grade 1 readiness small and simple" in " ".join(plan["rules"].split())
    # The historical evidence projection does not adopt new atom fields.
    legacy = prequestions.concept_evidence(mapped["rows"][0])
    assert "retained_atoms" not in legacy["prerequisites"][0]

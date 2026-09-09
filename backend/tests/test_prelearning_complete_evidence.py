"""Pre capture keeps the evidence needed to decide prerequisite coverage."""
from __future__ import annotations

import base64
import copy
import hashlib
import io

import pytest
from PIL import Image

from app.services import assessment_visual_evidence, generation, source_asset_store
from app.services import prelearning_authority_v2, prelearning_capture_policy as policy
from app.services.phase3 import envelope, host, kernel, place, prelearn, premap


def _env():
    return {
        "envelope_sha256": "e" * 64,
        "metadata": {policy.KEY: policy.VERSION},
        "canonical": {"blocks": [{
            "block_id": "BLK-1", "display_text": "plain preview",
            "display_text_with_visuals": "Full table with its caption and visual",
            "page_start": 2, "page_end": 3, "caption_owner": "BLK-1",
        }]},
        "graph": {"blocks": [{"block_id": "BLK-1", "kind": "table", "topic_id": "T1"}],
                  "topics": [{"topic_id": "T1", "title": "Source topic"}]},
        "inventory": {"items": [{
            "qid": "QINV-1", "raw_task": "Context " * 180 + "FINAL REQUIRED OPERATION",
            "options": {"a": "first option", "b": "last option"},
            "shared_context": "The task refers to this earlier page.",
            "image_urls": ["https://missing.example/figure.jpg"],
            "_source_block_ids": ["BLK-1"],
        }]},
    }


def _units():
    return [{"unit_id": "TYPE-1:CASE-1", "type_id": "TYPE-1", "case_id": "CASE-1",
             "qids": ["QINV-1"], "task": "Apply the source task pattern", "pattern": "Complete pattern"}]


def test_capture_preserves_long_tasks_options_shared_context_and_all_citation_namespaces(monkeypatch):
    monkeypatch.setattr(host, "derive_units", lambda env: _units())
    env = _env()
    before = copy.deepcopy(env)
    packet = prelearn.stage_evidence(env, "host")
    assert packet["questions"][0]["text"] == env["inventory"]["items"][0]["raw_task"]
    assert packet["questions"][0]["source_task"] == env["inventory"]["items"][0]
    indexed = prelearn.indexed_evidence("host", packet)
    assert set(indexed) == prelearn.citable_ids("host", packet)
    assert {"TYPE-1", "CASE-1", "TYPE-1:CASE-1", "QINV-1"} <= set(indexed)
    assert any("FINAL REQUIRED OPERATION" in str(entry) for entry in indexed["QINV-1"])
    closure = prelearn.resolve_evidence(indexed, {"TYPE-1"})
    assert "QINV-1" in closure
    assert "FINAL REQUIRED OPERATION" in str(closure)
    assert "https://missing.example/figure.jpg" in str(closure)
    assert env == before
    env["metadata"].pop(policy.KEY)
    legacy = prelearn.stage_evidence(env, "host")
    assert len(legacy["questions"][0]["text"]) == 600
    assert "source_task" not in legacy["questions"][0]


def test_settle_place_and_analysis_keep_full_owned_evidence(monkeypatch):
    env = _env()
    settled = [{"concept_title": "One capability", "concept_details": "Malformed but preserved full teaching", "_source_block_ids": ["BLK-1"]}]
    packet = prelearn.stage_evidence(env, "settle", settled=settled)
    assert packet["source_blocks"][0]["text"] == "Full table with its caption and visual"
    assert packet["source_blocks"][0]["source_context"]["page_end"] == 3
    assert packet["settled_concepts"][0]["concept_details"] == settled[0]["concept_details"]
    concept_id = packet["settled_concepts"][0]["concept_id"]
    closure = prelearn.resolve_evidence(prelearn.indexed_evidence("settle", packet), {concept_id})
    assert "Full table with its caption and visual" in str(closure["BLK-1"])
    hubs = [{"item_ref": "QINV-1", "text": "Full activity", "images": [{"url": "https://missing.example/figure.jpg", "caption": "Diagram"}]}]
    figures = [{"item_ref": "BLK-1", "images": [{"url": "https://missing.example/table.jpg"}]}]
    monkeypatch.setattr(place, "hub_pool", lambda env: hubs)
    monkeypatch.setattr(place, "figure_pool", lambda env: figures)
    placed = prelearn.stage_evidence(env, "place")
    assert placed == {"pooled_hub_items": hubs, "pooled_figures": figures}
    analysis = {"inventory": [{"item_id": "LA-1", "text": "An error", "evidence": ["BLK-1"], "rationale": "Complete reasoning"}]}
    analysed = prelearn.stage_evidence(env, "analyse", analysis=analysis)
    assert analysed["analysis_inventory"] == analysis["inventory"]
    for stage, source in (("settle", packet), ("place", placed), ("analyse", analysed)):
        assert set(prelearn.indexed_evidence(stage, source)) == prelearn.citable_ids(stage, source)


def _pinned(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_PUBLIC_BASE_URL", "https://aegis.example")
    monkeypatch.setattr(source_asset_store.config, "DATA_DIR", tmp_path)
    buffer = io.BytesIO()
    Image.new("RGB", (16, 12), "white").save(buffer, format="JPEG")
    data = buffer.getvalue()
    url = f"https://aegis.example/source-assets/12/{hashlib.sha256(data).hexdigest()}.jpg"
    source_asset_store.pin_asset(data, job_id=12, asset_url=url)
    return url, data


@pytest.mark.parametrize("call", [
    prelearn._live_capture, prelearn._live_merge, prelearn._live_critic,
    prelearning_authority_v2._live_author, prelearning_authority_v2._live_critic,
    premap._live_map, premap._live_critic,
])
def test_pre_authors_and_critics_receive_actual_bound_pixels(monkeypatch, tmp_path, call):
    url, data = _pinned(monkeypatch, tmp_path)
    seen = {}

    def fake_openai(system, user, **kwargs):
        seen.update(kwargs)
        return {}

    monkeypatch.setattr(generation, "_openai_json", fake_openai)
    payload = assessment_visual_evidence.bind({"capture_policy": policy.VERSION}, {"image_urls": [url]})
    call(payload)
    assert base64.b64decode(seen["image_urls"][0].split(",", 1)[1]) == data


def test_capture_and_merge_carry_complete_evidence_and_earlier_critic_dissent(monkeypatch, tmp_path):
    monkeypatch.setattr(envelope, "validate", lambda value: copy.deepcopy(value))
    monkeypatch.setattr(host, "derive_units", lambda env: _units())
    seen = []

    def capture_author(payload):
        seen.append(payload)
        return {"prerequisites": [{"prerequisite_id": "PR-0001", "text": "One fundamental", "evidence": ["QINV-1"], "rationale": "The final operation assumes it."}]}

    captured = prelearn.capture_stage(
        _env(), "host", provider=capture_author,
        critic=lambda payload: {"verdict": "rejected", "confidence": 0.99, "issues": ["QINV-1 assumes another missed fundamental"]},
        store=kernel.DecisionStore(tmp_path / "capture"),
    )
    assert "FINAL REQUIRED OPERATION" in str(seen[0]["evidence"])
    assert "visual_evidence" in seen[0]
    assert captured["evidence_packet"] == seen[0]["evidence"]

    def merge_author(payload):
        seen.append(payload)
        return {"prerequisites": [{"prerequisite_id": "PR-0001", "text": "One fundamental", "captures": ["host:PR-0001"], "rationale": "Same captured capability"}]}

    merged = prelearn.merge(_env(), [captured], provider=merge_author, store=kernel.DecisionStore(tmp_path / "merge"))
    assert "missed fundamental" in str(seen[-1]["prior_review_flags"])
    assert "FINAL REQUIRED OPERATION" in str(seen[-1]["evidence_index"])
    assert merged["evidence_packets"]["host"] == captured["evidence_packet"]
    assert merged["capture_policy"] == policy.VERSION


def test_map_new_policy_preserves_visual_table_context_and_uses_atomic_grouping():
    env = _env()
    env["canonical"]["blocks"][0]["owner_qid"] = "QINV-1"
    evidence = premap.map_evidence(env, {"prerequisites": [{"prerequisite_id": "PR-0001", "text": "One foundation", "evidence": ["BLK-1"]}]}, ["QINV-1"])
    assert evidence["source_blocks"][0]["text"] == "Full table with its caption and visual"
    assert evidence["source_blocks"][0]["source_context"]["page_end"] == 3
    assert "QINV-1" not in str(evidence)
    current = premap._map_rules("", atomic=True)
    assert "one lesson would teach together" not in current
    assert "independently teachable" in current
    assert "one lesson would teach together" in premap._map_rules("")


def test_cross_stage_citation_closure_feeds_owned_blocks_to_task_free_map(monkeypatch, tmp_path):
    monkeypatch.setattr(host, "derive_units", lambda env: _units())
    env = _env()
    packets = {
        "settle": prelearn.stage_evidence(env, "settle"),
        "host": prelearn.stage_evidence(env, "host"),
        "analyse": prelearn.stage_evidence(env, "analyse", analysis={"inventory": [
            {"item_id": "LA-1", "text": "Misreading this task", "evidence": ["QINV-1"]},
        ]}),
    }
    index, _ = prelearning_authority_v2._evidence({"evidence_packets": packets})
    closure = prelearn.resolve_evidence(index, {"LA-1"})
    assert {"LA-1", "QINV-1", "BLK-1"} <= set(closure)
    merged = {
        "evidence_packets": packets,
        "captures": {"analyse": [{"prerequisite_id": "PR-0001", "text": "Read the underlying representation", "evidence": ["LA-1"]}]},
    }
    response = {
        "atoms": [{"atom_id": "PA-0001", "text": "Read the underlying representation", "capture_refs": ["analyse:PR-0001"], "evidence": ["LA-1"], "rationale": "The cited process error assumes this foundation"}],
        "prerequisites": [{"prerequisite_id": "PR-0001", "text": "Read the underlying representation", "atoms": ["PA-0001"], "rationale": "One diagnosable capability"}],
        "dispositions": [], "review_resolutions": [],
        "demand_coverage": [{"demand_ref": "host:TYPE-1:CASE-1", "prerequisite_ids": ["PR-0001"], "rationale": "The task pattern requires this knowledge"}],
    }
    result = prelearning_authority_v2.adjudicate(
        env, merged, provider=lambda request: response, store=kernel.DecisionStore(tmp_path),
    )
    prerequisite = result["prerequisites"][0]
    assert prerequisite["evidence"] == ["LA-1"]
    assert prerequisite["source_block_ids"] == ["BLK-1"]
    mapped = premap.map_evidence(env, {"prerequisites": [prerequisite]}, ["QINV-1"])
    assert mapped["source_blocks"][0]["text"] == "Full table with its caption and visual"
    assert "FINAL REQUIRED OPERATION" not in str(mapped)
    assert "QINV-1" not in str(mapped)

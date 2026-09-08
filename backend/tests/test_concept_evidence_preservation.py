"""Evidence and lossless-format boundaries approved by the end-to-end review."""
from __future__ import annotations

import copy
import json

import pytest

from app.services import concept_refiner, generation, release_refiner
from app.services.phase3 import analyse, kernel, place, polish, settle
from tests import test_phase3_polish as polish_fixtures
from tests import test_phase3_settle_golden as golden
from tests import test_release_refiner as refiner_fixtures


@pytest.mark.parametrize("module", [place, analyse, settle])
def test_description_keeps_internal_headings_and_late_conditions(module):
    teaching = "Definition.\nWorked Example: " + "necessary detail " * 60
    teaching += "\nConditions: only when the temperature remains constant."
    details = "Description: " + teaching + " // Types: Type 01: Task"
    assert module._description_of(details) == teaching


def test_analysis_keeps_the_complete_source_task():
    env = polish_fixtures._env()
    text = "Use the measurements. " * 60 + "Do not include the insulation."
    env["inventory"]["items"] = [{"qid": "QINV-0001", "raw_task": text}]
    result = analyse.build_evidence(env)
    assert result["question_task_inventory"][0]["text"].endswith("Do not include the insulation.")


def test_formatter_preserves_repeated_description_and_caseless_example():
    original = (
        "Description: Voltage drives charge. Description: Resistance opposes current."
        " // Types: Type 01: Direct application Example: Find the voltage."
    )
    rows = concept_refiner.refine_chapter([{
        "topic": "Electricity", "concept_title": "Voltage", "concept_details": original,
    }])
    row = rows[0]
    assert "Resistance opposes current." in row["concept_details"]
    assert "Find the voltage." in row["concept_details"]
    assert row["_aegis_structure_original"] == original
    assert any("repeated_description_marker" in flag for flag in row["review_flags"])
    assert any("type_without_case" in flag for flag in row["review_flags"])
    assert concept_refiner.refine_chapter(copy.deepcopy(rows)) == rows


def test_named_marker_repair_preserves_example_and_records_before_after():
    row = polish_fixtures._clean_row()
    row["concept_details"] += " // Types: Type 01: Political participation Example 01: Explain citizen participation."
    before = row["concept_details"]
    repaired = before.replace(" Example 01:", " Case 01: Explaining participation Example 01:")
    reviews = []

    def provider(payload):
        assert "type_without_case" in {v["code"] for v in payload["rows"][0]["validation_errors"]}
        return {"rows": [{"row_ref": 0, "concept_title": row["concept_title"], "concept_details": repaired}]}

    def critic(payload):
        reviews.append(payload)
        return {"verdict": "verified", "confidence": 1, "issues": []}

    result = polish.polish(polish_fixtures._env(), [row], provider=provider, critic=critic)
    assert len(reviews) == 1
    audit = result[0]["_aegis_polish_repairs"][0]
    assert "Explain citizen participation." in audit["before"]
    assert "Explain citizen participation." in audit["after"]
    assert any("type_without_case" in flag for flag in result[0]["review_flags"])


def test_refiner_author_and_critic_receive_immutable_complete_evidence_and_rekey():
    row = refiner_fixtures._rows()[0]
    row["_aegis_source_evidence"] = {
        "source_blocks": [{"block_id": "BLK-0001", "display_text": "Source " * 120 + "final condition"}],
        "reference_blocks": [{"block_id": "BLK-0002", "display_text": "Supporting definition"}],
        "learner_analysis_inventory": [{"item_id": "LA-0001", "text": "An inherited rule is mistaken."}],
        "analysis_allotments": ["LA-0001"],
    }
    original = copy.deepcopy(row)
    calls, reviews = [], []
    store = kernel.DecisionStore()

    def provider(payload):
        calls.append(copy.deepcopy(payload))
        return refiner_fixtures._Provider()(payload)

    def critic(payload):
        reviews.append(copy.deepcopy(payload))
        return {"verdict": "verified", "confidence": 1, "issues": []}

    def run():
        return release_refiner.refine_release([row], metadata=refiner_fixtures._METADATA, provider=provider, critic=critic, store=store)

    run()
    run()
    assert len(calls) == len(reviews) == 1
    for request in [calls[0], reviews[0]]:
        assert request["rows"][0]["source_evidence"] == original["_aegis_source_evidence"]
    assert row == original
    row["_aegis_source_evidence"]["source_blocks"][0]["display_text"] += " revised"
    run()
    assert len(calls) == len(reviews) == 2


def test_pre_refiner_uses_captured_prerequisites_without_current_chapter_tasks():
    row = refiner_fixtures._rows()[0]
    row["_aegis_source_evidence"] = {"question_task_inventory": [{"raw_task": "FORBIDDEN_CHAPTER_TASK"}]}
    metadata = {**refiner_fixtures._METADATA, "pre_post": "Pre", "source_text": "FORBIDDEN_CHAPTER_SOURCE",
                "prerequisite_evidence": {"prerequisites": [{"title": "Earlier-grade fractions"}]}}
    calls = []

    def provider(payload):
        calls.append(payload)
        return refiner_fixtures._Provider()(payload)

    release_refiner.refine_release([row], metadata=metadata, provider=provider)
    assert "Earlier-grade fractions" in json.dumps(calls)
    assert "FORBIDDEN_CHAPTER" not in json.dumps(calls)


def test_culmination_uses_completed_siblings_across_batches_without_extra_calls(monkeypatch):
    env = golden.envelope_mod.load(golden.GOLDEN / "rne_envelope.json")
    rows = json.loads((golden.GOLDEN / "rne_settled_rows.json").read_text())["records"]
    mapping = golden._replay_map(env, rows)
    topology, grounding, author, critic = golden._providers(mapping)
    original_batched = settle._batched
    monkeypatch.setattr(settle, "_batched", lambda values: original_batched(values, size=2))
    seen = []
    completed_by_topic = {}
    recap_count = 0

    def checking_author(request):
        nonlocal recap_count
        topic_id = request["topic"]["topic_id"]
        completed = completed_by_topic.setdefault(topic_id, set())
        for recap in request.get("culminations") or []:
            recap_count += 1
            members = recap["member_teaching"]
            current = {item["concept_id"] for item in request["concepts"]}
            assert {member["concept_id"] for member in members} == completed | current
            for member in members:
                if member["concept_id"] in completed:
                    assert member["teaching_status"] == "completed"
                    assert "Achieving Mastery:" in member["concept_details"]
        answer = author(request)
        completed.update(item["concept_id"] for item in request["concepts"])
        seen.append(request)
        return answer

    result = settle.settle(env, topology_provider=topology, grounding_provider=grounding,
                           analysis_provider=checking_author, critic=critic)
    ordinary = [row for row in result if not concept_refiner.is_culmination(row["concept_title"])]
    counts = {}
    for row in ordinary:
        counts[row["_semantic_topic_id"]] = counts.get(row["_semantic_topic_id"], 0) + 1
    assert len(seen) == sum((count + 1) // 2 for count in counts.values())
    assert recap_count > 0


def test_cache_adapter_preserves_complete_json_and_reuses_only_stable_map(monkeypatch):
    calls = []
    monkeypatch.setattr(generation, "_openai_json", lambda system, user, **kw: calls.append((user, kw)) or {})
    payload = {"stage": "analyse.allot", "rules": "Use complete evidence", "settled_concepts": [{"description": "Teaching " * 100}],
               "items": [{"item_id": "LA-0001", "text": "First insight"}]}
    analyse._live_allot(payload)
    other = copy.deepcopy(payload)
    other["items"] = [{"item_id": "LA-0002", "text": "Second insight"}]
    analyse._live_allot(other)
    assert calls[0][1]["prompt_cache_prefix"] == calls[1][1]["prompt_cache_prefix"]
    for expected, (suffix, kwargs) in zip((payload, other), calls):
        assert json.loads(kwargs["prompt_cache_prefix"] + suffix) == expected


def test_deposit_pipeline_does_not_discard_malformed_teaching():
    row = refiner_fixtures._rows()[0]
    row["concept_details"] = (
        "Description: Voltage drives charge. Description: Resistance opposes current."
        "\nAchieving Mastery: Explain a change in current."
        " // Types: Type 01: Direct application Example: Find the voltage."
    )
    result = release_refiner._deposit_deterministic_pipeline([row], refiner_fixtures._METADATA)
    assert "Resistance opposes current." in result[0]["concept_details"]
    assert "Find the voltage." in result[0]["concept_details"]
    assert result[0]["_aegis_structure_original"]


def test_grounding_supporting_reference_reaches_author_and_critic():
    env = golden.envelope_mod.load(golden.GOLDEN / "rne_envelope.json")
    rows = json.loads((golden.GOLDEN / "rne_settled_rows.json").read_text())["records"]
    topology, grounding, author, _critic = golden._providers(golden._replay_map(env, rows))
    text_by_id = {row["block_id"]: row["display_text"] for row in env["canonical"]["blocks"]}
    reference_id = next(block_id for block_id, text in text_by_id.items() if len(text or "") > 400)
    authors, critics = [], []

    def with_reference(payload):
        result = grounding(payload)
        for concept in result["concepts"]:
            concept["reference_block_ids"] = [reference_id]
        return result

    def checking_author(payload):
        authors.append(payload)
        for concept in payload["concepts"]:
            assert {b["block_id"]: b["text"] for b in concept["reference_blocks"]}[reference_id] == text_by_id[reference_id]
        return author(payload)

    def critic(payload):
        if payload.get("stage") == "content_authoring":
            critics.append(payload)
            assert all(concept["reference_blocks"] for concept in payload["concepts"])
        return {"verdict": "verified", "confidence": 1, "issues": []}

    settle.settle(env, topology_provider=topology, grounding_provider=with_reference,
                  analysis_provider=checking_author, critic=critic)
    assert len(authors) == len(critics) > 0


@pytest.mark.parametrize("name", [
    "settle._live_topology", "settle._live_grounding", "settle._live_analysis", "settle._live_critic",
    "host._live_host", "host._live_type_owner", "host._live_critic",
    "analyse._live_build", "analyse._live_allot", "analyse._live_critic",
    "place._live_place", "place._live_critic", "polish._live_polish", "polish._live_critic",
    "release_refiner._live_refine", "release_refiner._live_critic",
])
def test_concept_authors_and_critics_receive_pinned_pixels(monkeypatch, tmp_path, name):
    import base64
    from app.services import assessment_visual_evidence as visual
    from app.services.phase3 import host
    from tests.test_assessment_visual_evidence import _pinned

    url, data = _pinned(monkeypatch, tmp_path)
    calls = []
    monkeypatch.setattr(generation, "_openai_json", lambda system, user, **kwargs: calls.append(kwargs) or {})
    module_name, function_name = name.split(".")
    module = {"host": host, "settle": settle, "analyse": analyse, "place": place,
              "polish": polish, "release_refiner": release_refiner}[module_name]
    payload = visual.bind({"rows": [{"row_ref": "R1"}], "items": [{"item_id": "LA-0001"}]}, {"image_urls": [url]})
    getattr(module, function_name)(payload)
    assert base64.b64decode(calls[0]["image_urls"][0].split(",", 1)[1]) == data


def test_named_visual_context_is_bound_before_decision_identity(monkeypatch, tmp_path):
    from app.services.phase3.evidence import decide_with_visual_evidence
    from tests.test_assessment_visual_evidence import _pinned

    url, _data = _pinned(monkeypatch, tmp_path)
    requests = []
    reviews = []
    store = kernel.DecisionStore()
    payload = {"source_blocks": [{"block_id": "B1", "text": f'[img src="{url}" alt="Circuit"]'}]}

    def provider(request):
        requests.append(request)
        return {"accepted": True}

    def critic(request):
        reviews.append(request)
        return {"verdict": "verified", "confidence": 1, "issues": []}

    for _ in range(2):
        decide_with_visual_evidence(kind="test.bound", unit_id="B1", envelope_sha256="source", payload=payload,
                                    provider=provider, checker=lambda result: [], critic=critic, store=store)
    assert len(requests) == len(reviews) == 1
    assert requests[0]["visual_evidence"] == reviews[0]["visual_evidence"]
    assert requests[0]["visual_evidence"]["images"][0]["state"] == "attached"
    assert "visual_evidence" not in payload


@pytest.mark.parametrize("kind, raw_text", [
    ("table", "| Quantity | Value |\n| Voltage | 12 V |"),
    ("figure", '[img src="https://aegis.example/circuit.jpg" alt="Circuit labels"]'),
])
def test_source_blocks_without_display_text_retain_raw_table_or_figure(kind, raw_text):
    from app.services.phase3.evidence import block_text

    block = {"kind": kind, "raw_text": raw_text}
    assert block_text(block) == raw_text
    assert block_text({**block, "display_text_with_visuals": "complete cell-aware rendering"}) == "complete cell-aware rendering"

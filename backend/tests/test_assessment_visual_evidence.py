"""Actual pixels, adopted scoring evidence and lossless capacity diagnostics."""
from __future__ import annotations

import base64
import copy
import hashlib
import io

import pytest
from PIL import Image

from app.services import assessment_visual_evidence as visual
from app.services import assessment_materialization as materialization
from app.services import assessment_item_review as item_review
from app.services import assessment_answer_restriction as restriction
from app.services import assessment_marking as marking
from app.services import assessment_master_refiner as refiner
from app.services import assessment_cells as cells
from app.services import assessment_routing as routing
from app.services import assessment_quality as quality
from app.services import assessment_dedup as dedup
from app.services import assessment_prelearning_claim as claims
from app.services import assessment_profile, column_spec, generation, source_asset_store
from app.services.phase3 import kernel


def _pinned(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_PUBLIC_BASE_URL", "https://aegis.example")
    monkeypatch.setattr(source_asset_store.config, "DATA_DIR", tmp_path)
    buffer = io.BytesIO()
    Image.new("RGB", (16, 12), "white").save(buffer, format="JPEG")
    data = buffer.getvalue()
    sha = hashlib.sha256(data).hexdigest()
    url = f"https://aegis.example/source-assets/12/{sha}.jpg"
    source_asset_store.pin_asset(data, job_id=12, asset_url=url)
    return url, data


def test_binds_owned_pixels_deduplicates_hash_and_preserves_missing_evidence(monkeypatch, tmp_path):
    url, data = _pinned(monkeypatch, tmp_path)
    other_job_url = url.replace("/12/", "/13/")
    payload = visual.bind({}, {"image_urls": [url, other_job_url, "https://untrusted.example/figure.png"]})
    assert len(payload["visual_evidence"]["images"]) == 3
    inputs = visual.image_inputs(payload)
    assert len(inputs) == 1
    assert base64.b64decode(inputs[0].split(",", 1)[1]) == data
    assert len(visual.review_flags(payload)) == 1
    assert "untrusted.example" in visual.review_flags(payload)[0]
    assert "base64" not in str(payload)


def test_stale_or_missing_pinned_pixels_cannot_claim_inspection(monkeypatch, tmp_path):
    url, _data = _pinned(monkeypatch, tmp_path)
    before = visual.bind({}, {"image_urls": [url]})
    source_asset_store.stored_asset_path(url.rsplit("/", 1)[-1]).unlink()
    with pytest.raises(ValueError, match="changed after binding"):
        visual.image_inputs(before)
    after = visual.bind({}, {"image_urls": [url]})
    assert before != after  # changed evidence cannot replay a reviewed result
    assert visual.image_inputs(after) == []
    assert "pinned_pixels_unavailable" in visual.review_flags(after)[0]


def test_actual_producer_signed_asset_url_attaches_without_rewriting_and_survives_secret_rotation(monkeypatch, tmp_path):
    from app.services import canonical_source_phase221_fallback as fallback

    url, data = _pinned(monkeypatch, tmp_path)
    monkeypatch.setenv("AEGIS_SOURCE_ASSET_SECRET", "test-fixture-signing-secret")
    signed_url = fallback.asset_url(12, url.rsplit("/", 1)[-1])
    assert "?sig=" in signed_url
    payload = visual.bind({}, {"image_urls": [signed_url]})
    assert payload["visual_evidence"]["images"][0]["source_url"] == signed_url
    assert base64.b64decode(visual.image_inputs(payload)[0].split(",", 1)[1]) == data
    monkeypatch.setenv("AEGIS_SOURCE_ASSET_SECRET", "rotated-test-fixture-secret")
    assert visual.bind({}, {"image_urls": [signed_url]}) == payload
    assert visual.image_inputs(payload)
    assert not visual.review_flags(payload)
    for altered in (
        signed_url + "&redirect=https://elsewhere.example/",
        signed_url + "&sig=" + "a" * 40,
        url + "?redirect=https://elsewhere.example/",
    ):
        rejected = visual.bind({}, {"image_urls": [altered]})
        assert visual.image_inputs(rejected) == []
        assert visual.review_flags(rejected)


@pytest.mark.parametrize("call", [
    materialization._live_materialize, materialization._live_critic,
    restriction._live_author, restriction._live_critic,
    marking._live_author, marking._live_critic,
    item_review._live_review, refiner._live_author, refiner._live_critic,
])
def test_every_assessment_author_and_critic_receives_real_image_inputs(monkeypatch, tmp_path, call):
    url, data = _pinned(monkeypatch, tmp_path)
    seen = {}
    def fake_openai(system, user, **kwargs):
        seen.update(kwargs)
        return {}
    monkeypatch.setattr(generation, "_openai_json", fake_openai)
    request = visual.bind({"unit_kind": "candidate", "candidate_id": "C1", "candidate": {"candidate_id": "C1"}}, {"image_urls": [url]})
    call(request)
    assert base64.b64decode(seen["image_urls"][0].split(",", 1)[1]) == data
    assert seen["prompt_cache_prefix"] is not None


@pytest.mark.parametrize("call", [
    cells._live_cell, cells._live_cell_critic,
    cells._live_generated_cell, cells._live_generated_cell_critic,
    routing._live_route, routing._live_route_critic,
    quality._live_review, quality._live_critic,
    dedup._live_dedup, dedup._live_dedup_critic,
    dedup._live_source_dedup, dedup._live_source_dedup_critic,
    claims._live_claim, claims._live_claim_critic,
])
def test_cell_route_quality_and_set_reviews_receive_actual_pixels(monkeypatch, tmp_path, call):
    url, data = _pinned(monkeypatch, tmp_path)
    seen = {}
    def fake_openai(system, user, **kwargs):
        seen.update(kwargs)
        return {}
    monkeypatch.setattr(generation, "_openai_json", fake_openai)
    request = visual.bind({"candidate": {"candidate_id": "C1"}}, {"image_urls": [url]})
    call(request)
    assert base64.b64decode(seen["image_urls"][0].split(",", 1)[1]) == data


@pytest.mark.parametrize("stage", ["generated_dedup", "source_dedup", "pre_learning_claim"])
def test_set_review_flags_survive_empty_removal_or_claim_without_input_mutation(stage):
    identity = "pre_question_id" if stage == "generated_dedup" else "source_qid"
    originals = [
        {identity: "Q1", "question_text": "Read the diagram.", "normalized_public_text": "Read the diagram.", "image_urls": ["https://unavailable.example/figure.png"], "flags": ["earlier review"]},
        {identity: "Q2", "question_text": "State a property.", "normalized_public_text": "State a property."},
    ]
    input_rows = copy.deepcopy(originals)
    def author(payload):
        if stage == "pre_learning_claim":
            return {"claimed": []}
        return {"duplicate_sets": [], "confidence": 1.0, "rationale": "The questions have distinct demands."}
    def critic(payload):
        return {"verdict": "dissent", "confidence": 1.0, "issues": ["Inspect the missing figure before trusting this set verdict."]}
    kwargs = dict(meta={}, envelope_sha256="v" * 64, provider=author, critic=critic, store=kernel.DecisionStore())
    if stage == "generated_dedup":
        kept, removed = dedup.decide_generated_duplicates(input_rows, concept_id="P1", concept_evidence={}, **kwargs)
    elif stage == "source_dedup":
        kept, removed = dedup.decide_source_duplicates(input_rows, **kwargs)
    else:
        kept, removed = claims.decide_pre_learning_claims(input_rows, **kwargs)
    assert input_rows == originals
    assert not removed
    assert [row[identity] for row in kept] == ["Q1", "Q2"]
    for row, original in zip(kept, input_rows):
        assert row is not original
        assert any("assessment_visual_evidence_unavailable" in flag for flag in row["flags"])
        assert any("Inspect the missing figure" in flag for flag in row["flags"])
        assert all(stage in flag for flag in row["flags"] if flag != "earlier review")
    assert "earlier review" in kept[0]["flags"]


def test_cell_routing_and_quality_keep_missing_pixel_flags():
    source = {"source_qid": "Q1", "raw_text": "Choose the pictured shape.", "image_urls": ["https://unavailable.example/figure.png"]}
    meta = {"subject": "Mathematics", "grade": "6"}
    profile = assessment_profile.resolve_for_metadata(None, meta)
    cell = cells.decide_cells([source], meta=meta, profile=profile, envelope_sha256="v" * 64,
        provider=lambda payload: {"source_qid": "Q1", "sheet_kind": "objective", "question_category": "Multiple Choice Question", "difficulty": "Less", "cognitive_skill": "Remember", "marks": 1, "rationale": "The supplied task has a closed option set."},
        store=kernel.DecisionStore())[0]
    assert any("assessment_visual_evidence_unavailable" in flag for flag in cell["flags"])
    candidate = {"candidate_id": "C1", **source}
    route = routing.route_candidate(candidate, [{"concept_key": "A"}, {"concept_key": "B"}], meta=meta,
        envelope_sha256="v" * 64, source_concept_release_sha256="s" * 64,
        provider=lambda payload: {"candidate_id": "C1", "concept_key": "A", "evidence": "The requested property is taught in A.", "rationale": "A is the applicable concept."}, store=kernel.DecisionStore())
    assert any("assessment_visual_evidence_unavailable" in flag for flag in route["flags"])
    review = quality.review_group({"group_key": "G1"}, [candidate], concept={"concept_key": "A"}, meta=meta,
        envelope_sha256="v" * 64, provider=lambda payload: {"group_key": "G1", "flags": []}, store=kernel.DecisionStore())
    assert review["quality_review"] == "flagged"
    assert any("assessment_visual_evidence_unavailable" in flag for flag in review["authority"]["review_flags"])


def test_joint_review_receives_adopted_contract_without_prior_self_evaluation():
    contract = {
        "answer_restriction": "Specific", "answer_space_contract": "A bounded resistance value with its unit.",
        "required_elements": ["correct resistance", "ohm unit"],
        "accepted_variations": ["Equivalent unit notation and valid alternative methods."],
        "rationale": "The author claims this is perfect.", "confidence": 1.0,
    }
    candidate = {"candidate_id": "C1", "_aegis_assessment_answer_restriction": contract, "_aegis_prior_review": {"verdict": "verified"}}
    payload = item_review._payload(candidate, {}, None, meta={}, format_policy={})
    assert payload["adopted_answer_contract"] == {key: contract[key] for key in ("answer_restriction", "answer_space_contract", "required_elements", "accepted_variations")}
    assert payload["item"] == {"candidate_id": "C1"}
    assert payload["answer_contract_availability"] == "recorded"


@pytest.mark.parametrize("source_owned", [True, False])
def test_materialization_attaches_declared_item_figures_without_whole_chapter_fanout(monkeypatch, tmp_path, source_owned):
    url, _ = _pinned(monkeypatch, tmp_path)
    owner = {"image_urls": [url]}
    payload = materialization._decision_payload(
        owner if source_owned else None, {} if source_owned else {"generated_question": owner},
        candidate_id="C1", meta={}, descriptive_answer_capacity=30,
        context={"released_hierarchy": {"image_urls": ["https://unrelated.example/chapter-figure.png"]}},
    )
    assert [entry["source_url"] for entry in payload["visual_evidence"]["images"]] == [url]
    assert not visual.review_flags(payload)


def test_multipart_aggregate_overflow_is_visible_before_authoring_and_preserves_all_children():
    metadata = {"subject": "Science", "grade": "6"}
    profile = assessment_profile.resolve_for_metadata(None, metadata)
    metadata = column_spec.bind_metadata(metadata, profile)
    cell = {"cell_id": "CELL1", "sheet_kind": "descriptive", "marks": 18, "question_category": "Short Answer Type"}
    children = [
        {"text": f"{chr(97 + index)}) Explain relation {index + 1}.", "keywords": [
            {"answer_type": "Phrases", "keyword": f"Identifies feature {index + 1}.{part + 1}; accepts equivalent wording."}
            for part in range(6)
        ]} for index in range(6)
    ]
    seen = {}
    def author(payload):
        seen.update(copy.deepcopy(payload))
        return {"candidate_id": "C1", "question": "Use the observations to explain each relation.", "display_answer": "Each relation follows from its stated observations.", "answer_explanation": "Each relation follows from its stated observations.", "answers": [], "sub_questions": children, "requires_visual": False, "rationale": "Retain all source demands; parent_projection_capacity needs a schema decision."}
    result = materialization._materialize_prepared(
        {"source_qid": "QINV-0001", "raw_text": "Explain six relations."}, cell,
        candidate_id="C1", meta=metadata, context={}, descriptive_answer_capacity=30,
        envelope_sha256="a" * 64, provider=author, critic=None,
        store=kernel.DecisionStore(), fixer=None,
    )
    assert seen["workbook_capacities"]["multipart_parent_criterion_slots"] == 30
    assert sum(len(child["keywords"]) for child in result["sub_questions"]) == 36
    assert any("parent_projection_capacity" in flag for flag in result["flags"])
    assert result["answers"] == []

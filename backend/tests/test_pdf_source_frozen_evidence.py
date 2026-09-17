"""Fresh-PDF evidence reaches sealed decisions, including late re-grounding."""
from __future__ import annotations

import base64
import copy
import hashlib
import io

import pytest
from PIL import Image

from app.services import canonical_source_phase2, pdf_source_adapter, source_asset_store
from app.services.phase3 import analyse, envelope, evidence, kernel, reground, settle


def _source(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_PUBLIC_BASE_URL", "https://aegis.example")
    monkeypatch.setattr(source_asset_store.config, "DATA_DIR", tmp_path)
    image = io.BytesIO()
    Image.new("RGB", (20, 18), "white").save(image, format="JPEG")
    pixels = image.getvalue()
    digest = hashlib.sha256(pixels).hexdigest()
    url = f"https://aegis.example/source-assets/51/{digest}.jpg"
    source_asset_store.pin_asset(pixels, job_id=51, asset_url=url)

    def block(identity, kind, content, **extra):
        return {
            "block_id": identity, "page_id": "p0001", "page_number": 1,
            "bbox": [0.1, 0.1, 0.9, 0.9], "kind": kind, "content": content,
            "latex": "", "table_cells": [], "printed_label": "",
            "figure_caption": "", "legibility": "legible", "uncertainty_notes": [],
            **extra,
        }

    blocks = [
        block("B1", "task", "Use the table to compare the values."),
        block("B2", "table", "Complete printed table context. " * 25, table_cells=[
            {"row": 0, "column": 0, "row_span": 1, "column_span": 1,
             "content": "Quantity", "latex": ""},
            {"row": 1, "column": 0, "row_span": 1, "column_span": 1,
             "content": "Exact final cell", "latex": r"\frac{17}{29}"},
        ]),
        block("B3", "answer", "The printed answer has  two spaces."),
    ]
    structure = {
        "title": "Printed chapter", "sections": [
            {"section_id": "S1", "title": "Using the table", "parent_section_id": "",
             "level": 1, "block_ids": ["B1"]},
            {"section_id": "S2", "title": "Source data", "parent_section_id": "",
             "level": 1, "block_ids": ["B2", "B3"]},
        ],
        "tasks": [{
            "task_id": "task-1", "label": "1", "kind": "intext_question",
            "parent_task_id": "", "section_id": "S1", "prompt_block_ids": ["B1"],
            "context_block_ids": ["B2"], "answer_block_ids": ["B3"], "figure_block_ids": [],
        }],
        "relations": [
            {"kind": "context_for", "from_block_id": "B2", "to_block_id": "B1"},
            {"kind": "answer_for", "from_block_id": "B3", "to_block_id": "B1"},
        ],
        "unassigned_block_ids": [], "review_notes": [],
    }
    document = {
        "schema_version": "pdf-source-ir-1", "engine_version": pdf_source_adapter.ENGINE_VERSION,
        "pdf_sha256": "a" * 64, "status": "verified", "structure": structure,
        "pages": [{"page_id": "p0001", "page_number": 1, "blocks": blocks,
                   "audit": {"verdict": "verified", "source_usable": True, "issues": []}}],
        "audit": {"verdict": "verified", "issues": []},
    }
    canonical = pdf_source_adapter.project_document(
        document, source_filename="source.pdf", job_id=51,
        evidence={"pdf_sha256": document["pdf_sha256"]},
        cropper=lambda *args, **kwargs: {"url": url, "sha256": digest},
    )["canonical"]
    graph = {
        "source_contract_hash": "pdf-source-fixture",
        "topics": [{"topic_id": "T1", "title": "Using the table"},
                   {"topic_id": "T2", "title": "Source data"}],
        "subtopics": [], "blocks": [
            {"block_id": row["block_id"], "kind": row["kind"],
             "topic_id": "T1" if row["block_id"] == "B1" else "T2", "subtopic_id": ""}
            for row in blocks
        ],
    }
    row = {
        "topic": "Using the table", "_semantic_topic_id": "T1", "parent_concept": "",
        "concept_title": "Comparing table values",
        "concept_details": "Description: Compare the quantities in the printed table.\nAchieving Mastery: Compare quantities.",
        "keywords": "quantity | comparison",
    }
    return canonical, graph, row, pixels


def _build(canonical, graph, row):
    return envelope.build(
        canonical=canonical, graph=graph, skeleton_rows=[row],
        inventory=canonical_source_phase2.inventory_from_canonical(canonical),
        mined_types={"types": []},
    )


def _verified(_request):
    return {"verdict": "verified", "confidence": 0.999, "issues": []}


def test_frozen_pdf_keeps_exact_task_roles_and_structure_in_model_evidence(monkeypatch, tmp_path):
    canonical, graph, row, _pixels = _source(monkeypatch, tmp_path)
    frozen = _build(canonical, graph, row)
    assert frozen["canonical"] == canonical
    assert pdf_source_adapter.is_canonical(frozen["canonical"])
    packet = analyse.build_evidence(frozen)
    task = packet["question_task_inventory"][0]
    roles = task["content_objects"]["pdf_source"]
    assert roles["prompt"][0]["content"] == "Use the table to compare the values."
    assert roles["context"][0]["table_cells"][1]["latex"] == r"\frac{17}{29}"
    assert roles["answer"][0]["content"] == "The printed answer has  two spaces."
    assert "/source-assets/51/" in task["shared_context"]
    canonical["source_structure"]["tasks"][0]["context_block_ids"].clear()
    assert frozen["canonical"]["source_structure"]["tasks"][0]["context_block_ids"] == ["B2"]
    tampered = copy.deepcopy(frozen)
    tampered["canonical"]["source_relations"].clear()
    with pytest.raises(envelope.EnvelopeError, match="seal does not verify"):
        envelope.validate(tampered)


def test_settle_decisions_see_full_cross_topic_ir_relationships_and_crop_pixels(monkeypatch, tmp_path):
    canonical, graph, row, pixels = _source(monkeypatch, tmp_path)
    frozen = _build(canonical, graph, row)
    seen = {}

    def topology(payload):
        seen["topology"] = payload
        return {"decisions": [{
            "concept_id": item["concept_id"], "decision": "keep", "confidence": 0.999,
            "reason": "Fixture preserves the claim.", "segments": [{
                key: item[key] for key in ("concept_title", "parent_concept", "concept_details", "keywords")
            }],
        } for item in payload["concepts"]]}

    def ground(payload):
        seen["grounding"] = payload
        return {"concepts": [{
            "concept_id": item["concept_id"], "source_block_ids": ["B1"],
            "reference_block_ids": ["B2"], "confidence": 0.999, "reason": "The question names its table.",
        } for item in payload["concepts"]]}

    def author(payload):
        seen["authoring"] = payload
        return {"rows": [{
            "concept_id": item["concept_id"], "concept_description": "Compare the quantities in the printed table.",
            "achieving_mastery": "Compare quantities.",
        } for item in payload["concepts"]]}

    settle.settle(frozen, topology_provider=topology, grounding_provider=ground,
                  analysis_provider=author, critic=_verified, store=kernel.DecisionStore())
    for payload in seen.values():
        assert payload["source_structure"] == canonical["source_structure"]
        assert payload["source_relations"] == canonical["source_relations"]
    other = next(block for block in seen["grounding"]["other_topic_blocks"] if block["block_id"] == "B2")
    assert len(other["text"]) > 400
    assert "Exact final cell" in other["text"]
    assert other["source_ir"] == canonical["blocks"][1]["source_ir"]
    for stage in ("grounding", "authoring"):
        assert base64.b64decode(evidence.image_inputs(seen[stage])[0].split(",", 1)[1]) == pixels


@pytest.mark.parametrize("fresh_pdf", [True, False])
def test_reground_binds_fresh_pdf_evidence_and_preserves_historical_payloads(monkeypatch, tmp_path, fresh_pdf):
    canonical, graph, row, pixels = _source(monkeypatch, tmp_path)
    if not fresh_pdf:
        canonical.pop("pdf_source_engine")
        canonical.pop("compiler_version")
        frozen = _build(canonical, graph, row)
        assert frozen["canonical"] == {"blocks": canonical["blocks"]}
    seen = []

    def provider(payload):
        seen.append(payload)
        return {"concepts": [{
            "concept_id": item["concept_id"], "source_block_ids": ["B1"],
            "reference_block_ids": ["B2"], "confidence": 0.999, "reason": "Ground the changed claim.",
        } for item in payload["concepts"]]}

    store_dir = tmp_path / "reground-decisions"
    result = reground.reground_rows([row], [0], graph=graph, canonical=canonical,
                                   provider=provider, critic=_verified, store_dir=store_dir)
    payload = seen[0]
    other = next(block for block in payload["other_topic_blocks"] if block["block_id"] == "B2")
    if fresh_pdf:
        assert other["source_ir"] == canonical["blocks"][1]["source_ir"]
        assert "Exact final cell" in other["text"]
        assert payload["source_structure"] == canonical["source_structure"]
        assert payload["source_relations"] == canonical["source_relations"]
        assert base64.b64decode(evidence.image_inputs(payload)[0].split(",", 1)[1]) == pixels
    else:
        assert other["text"] == evidence.block_text(canonical["blocks"][1])[:400]
        assert "source_ir" not in other
        assert "source_structure" not in payload and "source_relations" not in payload
        assert "visual_evidence" not in payload
    assert result[0]["_source_block_ids"] == ["B1"]
    assert reground.reground_rows([row], [0], graph=graph, canonical=canonical,
                                 provider=provider, critic=_verified, store_dir=store_dir) == result
    assert len(seen) == 1  # The identical late correction replays without another decision.

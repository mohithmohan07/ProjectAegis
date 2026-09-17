"""Offline source-engine behavior: real PDFs, scripted model decisions only."""
from __future__ import annotations

import copy
import json
import threading
from collections import Counter
from contextvars import ContextVar

import fitz
import pytest

from app.services import pdf_source_engine as engine, pdf_source_evidence as evidence
from app.services.pdf_source_models import DocumentStructure, PageBlock, validate_structure
from app.services.run_control import RunDeferred


def _pdf(tmp_path, pages=2):
    path = tmp_path / "chapter.pdf"
    with fitz.open() as document:
        for number in range(1, pages + 1):
            page = document.new_page(width=300, height=400)
            page.insert_text((24, 40), f"Evidence page {number}: exact printed source")
        document.save(path)
    return path


def _block(content, *, kind="paragraph"):
    return {"bbox": [0.05, 0.05, 0.95, 0.45], "kind": kind, "content": content,
            "latex": "", "table_cells": [], "printed_label": "", "figure_caption": "",
            "legibility": "legible", "uncertainty_notes": []}


def _draft(number):
    blocks = [_block(f"Exact source {number}", kind="task")]
    if number == 2:
        blocks.append({**_block("Printed figure label", kind="figure"), "bbox": [0.1, 0.5, 0.9, 0.9]})
    return {"page_number": number, "source_usable": True, "blocks": blocks, "review_notes": []}


def _audit(*, verdict="verified", usable=True, block_ids=()):
    return {"verdict": verdict, "source_usable": usable,
            "issues": [] if verdict == "verified" else [{
                "code": "visible_source_uncertainty", "message": "Check this printed detail.",
                "block_ids": list(block_ids), "bbox": [],
            }]}


def _structure(blocks):
    ids = [block["block_id"] for block in blocks]
    prompts = [block["block_id"] for block in blocks if block["kind"] == "task"]
    figures = [block["block_id"] for block in blocks if block["kind"] == "figure"]
    return {"title": "Printed chapter title", "sections": [{
        "section_id": "s1", "title": "Printed section", "parent_section_id": "",
        "level": 1, "block_ids": ids,
    }], "tasks": [{"task_id": "q1", "label": "1", "kind": "question", "parent_task_id": "",
                    "section_id": "s1", "prompt_block_ids": prompts, "context_block_ids": [],
                    "answer_block_ids": [], "figure_block_ids": figures}],
        "relations": ([{"kind": "continues", "from_block_id": prompts[0], "to_block_id": prompts[1]}]
                      if len(prompts) > 1 else []), "unassigned_block_ids": [], "review_notes": []}


class ScriptedProvider:
    def __init__(self, overrides=None):
        self.overrides = overrides or {}
        self.calls = []

    def __call__(self, **kwargs):
        stage = kwargs["response_schema"]["name"].removeprefix("aegis_pdf_")
        payload = json.loads(kwargs["prompt"])
        self.calls.append((stage, payload, kwargs))
        override = self.overrides.get(stage)
        if override is not None:
            return override(payload) if callable(override) else copy.deepcopy(override)
        if stage.endswith("audit"):
            return _audit()
        if stage.startswith("p"):
            return _draft(payload["source"]["page_number"])
        source = payload.get("source", payload)
        return _structure(source["blocks"])

    def counts(self):
        return Counter(stage for stage, _, _ in self.calls)


def _read(path, artifacts, provider):
    return engine.read_pdf(path, job_id=73, artifact_dir=artifacts, provider=provider)


def test_direct_structure_keeps_cross_page_task_and_exact_source_identity(tmp_path):
    path = _pdf(tmp_path)
    provider = ScriptedProvider()
    result = _read(path, tmp_path / "artifacts", provider)
    assert result["status"] == "verified"
    assert result["pdf_sha256"] == evidence.file_sha256(path)
    assert result["structure"]["tasks"][0]["prompt_block_ids"] == ["p0001-b0001", "p0002-b0001"]
    assert result["structure"]["tasks"][0]["figure_block_ids"] == ["p0002-b0002"]
    assert result["structure"]["relations"] == [{
        "kind": "continues", "from_block_id": "p0001-b0001", "to_block_id": "p0002-b0001",
    }]
    assert [block["content"] for page in result["pages"] for block in page["blocks"]] == [
        "Exact source 1", "Exact source 2", "Printed figure label",
    ]
    original_calls = len(provider.calls)
    assert _read(path, tmp_path / "artifacts", provider) == result
    assert len(provider.calls) == original_calls


def test_independent_pages_overlap_with_parent_context_and_assemble_in_page_order(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_SOURCE_CHUNK_WORKERS", "2")
    marker = ContextVar("pdf_engine_test_parent", default="missing")
    rendezvous = threading.Barrier(2)
    seen = []
    def parallel_author(payload):
        seen.append((payload["source"]["page_number"], marker.get()))
        rendezvous.wait(timeout=5)
        return _draft(payload["source"]["page_number"])
    provider = ScriptedProvider({"p0001_author": parallel_author, "p0002_author": parallel_author})
    token = marker.set("frozen-run-context")
    try:
        result = _read(_pdf(tmp_path), tmp_path / "artifacts", provider)
    finally:
        marker.reset(token)
    assert sorted(seen) == [(1, "frozen-run-context"), (2, "frozen-run-context")]
    assert [page["page_number"] for page in result["pages"]] == [1, 2]


@pytest.mark.parametrize("pending_stage", ["p0001_audit", "p0002_author", "document_audit"])
def test_batch_pause_keeps_every_returned_decision_and_reuses_completed_pages(tmp_path, pending_stage):
    path = _pdf(tmp_path)
    artifacts = tmp_path / "artifacts"
    def pending(_payload):
        raise RunDeferred("existing paid batch pending", reason="batch_wait")
    provider = ScriptedProvider({pending_stage: pending})
    with pytest.raises(RunDeferred) as caught:
        _read(path, artifacts, provider)
    assert caught.value.reason == "batch_wait"
    completed = provider.counts() - Counter({pending_stage: 1})
    assert list(artifacts.glob("pdf-source-engine-v1/*/p0001.author.*.json"))
    provider.overrides.clear()
    result = _read(path, artifacts, provider)
    assert result["status"] == "verified"
    assert all(provider.counts()[stage] == count for stage, count in completed.items())
    assert provider.counts()[pending_stage] == 2


def test_full_native_text_and_positioned_spans_reach_author_and_whole_page_auditor(tmp_path, monkeypatch):
    path = _pdf(tmp_path, pages=1)
    original = evidence.page_input
    long_text = "Visible source evidence " * 600 + "END OF FULL NATIVE EVIDENCE"
    spans = [{"text": long_text, "bbox": [0.1, 0.1, 0.9, 0.9]}]
    def full_native(manifest, number):
        return {**original(manifest, number), "text": long_text, "spans": spans}
    monkeypatch.setattr(evidence, "page_input", full_native)
    provider = ScriptedProvider()
    _read(path, tmp_path / "artifacts", provider)
    for stage, payload, kwargs in provider.calls:
        source = payload.get("source", payload)
        if stage.startswith("p"):
            assert source["native_text"] == long_text
            assert source["native_spans"] == spans
        else:
            assert source["page_evidence"][0]["native_text"] == long_text
        assert kwargs["pages"][0].image_data_url.startswith("data:image/jpeg;base64,")


def test_audit_receives_private_magnified_regions_and_original_whole_page(tmp_path):
    provider = ScriptedProvider()
    result = _read(_pdf(tmp_path), tmp_path / "artifacts", provider)
    _stage, payload, request = next(row for row in provider.calls if row[0] == "p0002_audit")
    assert request["pages"][0].evidence_id == "p0002"
    assert len(request["pages"]) == 2
    region = payload["additional_source_regions"][0]
    assert region["block_ids"] == ["p0002-b0002"]
    assert region["bbox"] == [0.1, 0.5, 0.9, 0.9]
    assert request["pages"][1].evidence_id == region["evidence_id"]
    assert result["pages"][1]["audit"]["evidence_regions"] == [region]


def test_output_allowance_uses_current_provider_policy_without_a_smaller_engine_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(engine.openai_policy, "configured_max_output_tokens", lambda model=None: 31001)
    provider = ScriptedProvider()
    _read(_pdf(tmp_path, pages=1), tmp_path / "artifacts", provider)
    assert {kwargs["max_tokens"] for _stage, _payload, kwargs in provider.calls} == {31001}


def test_unverified_repair_cannot_drop_flagged_original_content(tmp_path):
    path = _pdf(tmp_path, pages=1)
    original = _draft(1)
    original["blocks"].append({**_block("Flagged but legible source remains"), "bbox": [0.1, 0.5, 0.9, 0.9]})
    provider = ScriptedProvider({
        "p0001_author": original,
        "p0001_audit": _audit(verdict="needs_repair", block_ids=["p0001-b0002"]),
        "p0001_repair": _draft(1),
        "p0001_repair_audit": _audit(verdict="needs_repair", block_ids=["p0001-b0001"]),
    })
    result = _read(path, tmp_path / "artifacts", provider)
    assert result["status"] == "review_required"
    assert result["pages"][0]["blocks"][1]["content"] == "Flagged but legible source remains"
    assert len(result["pages"][0]["audit"]["attempts"]) == 2
    assert provider.counts()["p0001_repair"] == 1


def test_verified_repair_is_adopted_with_both_drafts_and_independent_audits_retained(tmp_path):
    path = _pdf(tmp_path, pages=1)
    repaired = _draft(1)
    repaired["blocks"][0]["content"] = "Correct printed transcription"
    provider = ScriptedProvider({"p0001_audit": _audit(verdict="needs_repair"), "p0001_repair": repaired})
    result = _read(path, tmp_path / "artifacts", provider)
    assert result["pages"][0]["blocks"][0]["content"] == "Correct printed transcription"
    attempts = result["pages"][0]["audit"]["attempts"]
    assert attempts[0]["draft"]["blocks"][0]["content"] == "Exact source 1"
    assert attempts[1]["audit"]["verdict"] == "verified"
    audit_payload = next(payload for stage, payload, _ in provider.calls if stage == "p0001_repair_audit")
    assert audit_payload["original"]["blocks"][0]["content"] == "Exact source 1"


def test_unreadable_source_stops_before_document_work_and_retains_failed_evidence(tmp_path):
    path = _pdf(tmp_path, pages=1)
    unusable = {**_draft(1), "source_usable": False}
    provider = ScriptedProvider({
        "p0001_author": unusable, "p0001_repair": unusable,
        "p0001_audit": _audit(verdict="unreadable", usable=False),
        "p0001_repair_audit": _audit(verdict="unreadable", usable=False),
    })
    artifacts = tmp_path / "artifacts"
    with pytest.raises(engine.SourceIntegrityError, match="not faithfully readable"):
        _read(path, artifacts, provider)
    assert len(provider.calls) == 4
    assert list(artifacts.glob("pdf-source-engine-v1/*/p0001.unusable.json"))
    with pytest.raises(engine.SourceIntegrityError):
        _read(path, artifacts, provider)
    assert len(provider.calls) == 4


@pytest.mark.parametrize("defect", ["unknown", "missing", "duplicate"])
def test_invalid_structure_references_get_one_model_repair_and_independent_audit(tmp_path, defect):
    path = _pdf(tmp_path, pages=1)
    def bad_structure(payload):
        result = _structure(payload["blocks"])
        if defect == "unknown":
            result["tasks"][0]["prompt_block_ids"] = ["Exact source 1"]
        elif defect == "missing":
            result["sections"][0]["block_ids"] = []
        else:
            result["sections"][0]["block_ids"] *= 2
        return result
    provider = ScriptedProvider({"document_author": bad_structure})
    artifacts = tmp_path / "artifacts"
    result = _read(path, artifacts, provider)
    assert result["structure"]["tasks"][0]["prompt_block_ids"] == ["p0001-b0001"]
    assert result["audit"]["attempts"][0]["audit_origin"] == "mechanical_integrity"
    assert result["audit"]["attempts"][1]["audit_origin"] == "independent_model"
    assert provider.counts()["document_audit"] == 0
    assert provider.counts()["document_repair"] == 1
    assert provider.counts()["document_repair_audit"] == 1
    assert list(artifacts.glob("pdf-source-engine-v1/*/document.author.*.json"))
    payload = next(payload for stage, payload, _ in provider.calls if stage == "document_repair")
    assert payload["mechanical_validation_error"]
    assert payload["source"]["blocks"][0]["content"] == "Exact source 1"


@pytest.mark.parametrize("repair_invalid", [True, False])
def test_unusable_reference_repair_is_retained_without_looping_or_rebuying(tmp_path, repair_invalid):
    path = _pdf(tmp_path, pages=1)
    def invalid(payload):
        source = payload.get("source", payload)
        result = _structure(source["blocks"])
        result["tasks"][0]["prompt_block_ids"] = ["nonexistent-block"]
        return result
    overrides = {"document_author": invalid}
    if repair_invalid:
        overrides["document_repair"] = invalid
    else:
        overrides["document_repair_audit"] = _audit(verdict="needs_repair")
    provider = ScriptedProvider(overrides)
    artifacts = tmp_path / "artifacts"
    for _ in range(2):
        with pytest.raises(engine.SourceIntegrityError):
            _read(path, artifacts, provider)
    assert provider.counts()["document_author"] == 1
    assert provider.counts()["document_repair"] == 1
    assert provider.counts()["document_repair_audit"] == (0 if repair_invalid else 1)
    assert list(artifacts.glob("pdf-source-engine-v1/*/document.unusable.json"))


def test_pause_after_structure_reference_repair_preserves_author_and_repair(tmp_path):
    path = _pdf(tmp_path, pages=1)
    def invalid(payload):
        result = _structure(payload["blocks"])
        result["sections"][0]["block_ids"] = []
        return result
    def pending(_payload):
        raise RunDeferred("repair audit wave pending")
    provider = ScriptedProvider({"document_author": invalid, "document_repair_audit": pending})
    artifacts = tmp_path / "artifacts"
    with pytest.raises(RunDeferred):
        _read(path, artifacts, provider)
    del provider.overrides["document_repair_audit"]
    result = _read(path, artifacts, provider)
    assert result["status"] == "verified"
    assert provider.counts()["document_author"] == provider.counts()["document_repair"] == 1


@pytest.mark.parametrize("defect", ["geometry", "schema", "page_identity"])
def test_invalid_page_author_is_repaired_once_and_independently_verified(tmp_path, defect):
    path = _pdf(tmp_path, pages=1)
    draft = _draft(1)
    if defect == "geometry":
        draft["blocks"][0]["bbox"] = [0.9, 0.1, 0.1, 0.9]
    elif defect == "schema":
        del draft["blocks"][0]["content"]
    else:
        draft["page_number"] = 999
    provider = ScriptedProvider({"p0001_author": draft})
    artifacts = tmp_path / "artifacts"
    result = _read(path, artifacts, provider)
    assert result["status"] == "verified"
    assert result["pages"][0]["audit"]["attempts"][0]["draft"] == draft
    assert result["pages"][0]["audit"]["attempts"][0]["audit_origin"] == "mechanical_integrity"
    assert provider.counts()["p0001_author"] == provider.counts()["p0001_repair"] == 1
    assert provider.counts()["p0001_audit"] == 0
    assert provider.counts()["p0001_repair_audit"] == 1
    repair_payload = next(payload for stage, payload, _ in provider.calls if stage == "p0001_repair")
    assert repair_payload["mechanical_validation_error"]


@pytest.mark.parametrize("invalid_repair", [True, False])
def test_failed_page_geometry_repair_is_saved_without_second_repair_or_repurchase(tmp_path, invalid_repair):
    path = _pdf(tmp_path, pages=1)
    draft = _draft(1)
    draft["blocks"][0]["bbox"] = [0.9, 0.1, 0.1, 0.9]
    overrides = {"p0001_author": draft}
    if invalid_repair:
        overrides["p0001_repair"] = draft
    else:
        overrides["p0001_repair_audit"] = _audit(verdict="needs_repair")
    provider = ScriptedProvider(overrides)
    artifacts = tmp_path / "artifacts"
    for _ in range(2):
        with pytest.raises(engine.SourceIntegrityError):
            _read(path, artifacts, provider)
    assert provider.counts()["p0001_author"] == provider.counts()["p0001_repair"] == 1
    assert provider.counts()["p0001_repair_audit"] == (0 if invalid_repair else 1)
    assert list(artifacts.glob("pdf-source-engine-v1/*/p0001.unusable.json"))


def test_page_geometry_repair_survives_batch_audit_pause_without_repurchase(tmp_path):
    path = _pdf(tmp_path, pages=1)
    draft = _draft(1)
    draft["blocks"][0]["bbox"] = [0.0, 0.0, 2.0, 2.0]
    def pending(_payload):
        raise RunDeferred("page repair verifier batch pending")
    provider = ScriptedProvider({"p0001_author": draft, "p0001_repair_audit": pending})
    artifacts = tmp_path / "artifacts"
    with pytest.raises(RunDeferred):
        _read(path, artifacts, provider)
    del provider.overrides["p0001_repair_audit"]
    result = _read(path, artifacts, provider)
    assert result["status"] == "verified"
    assert provider.counts()["p0001_author"] == provider.counts()["p0001_repair"] == 1


def test_tampered_saved_decision_is_rejected_without_rebuying(tmp_path):
    path = _pdf(tmp_path, pages=1)
    def pause(_payload):
        raise RunDeferred("wait")
    provider = ScriptedProvider({"p0001_audit": pause})
    artifacts = tmp_path / "artifacts"
    with pytest.raises(RunDeferred):
        _read(path, artifacts, provider)
    saved = next(artifacts.glob("pdf-source-engine-v1/*/p0001.author.*.json"))
    record = evidence.read_json(saved)
    record["response"]["blocks"][0]["content"] = "Tampered source"
    evidence.atomic_json(saved, record)
    provider.overrides.clear()
    with pytest.raises(engine.SourceIntegrityError, match="corrupt"):
        _read(path, artifacts, provider)
    assert len(provider.calls) == 2


def test_changed_prompt_cannot_rebuy_a_paused_runs_paid_decisions(tmp_path, monkeypatch):
    path = _pdf(tmp_path, pages=1)
    artifacts = tmp_path / "artifacts"
    def pause(_payload):
        raise RunDeferred("existing paid batch waiting")
    provider = ScriptedProvider({"p0001_audit": pause})
    with pytest.raises(RunDeferred):
        _read(path, artifacts, provider)
    count = len(provider.calls)
    monkeypatch.setattr(engine, "PAGE_AUTHOR", engine.PAGE_AUTHOR + "\nA separately versioned prompt.")
    with pytest.raises(engine.SourceIntegrityError, match="saved reader request contract"):
        _read(path, artifacts, provider)
    assert len(provider.calls) == count
    assert provider.counts()["p0001_author"] == 1
    assert len(list(artifacts.glob("pdf-source-engine-v1/*/p0001.author.*.json"))) == 1


def test_structural_cycles_and_overlapping_table_cells_are_mechanical_errors():
    data = _structure([{**_block("prompt", kind="task"), "block_id": "b1"}])
    data["sections"][0]["parent_section_id"] = "s1"
    with pytest.raises(ValueError, match="cyclic"):
        validate_structure(DocumentStructure.model_validate(data), {"b1"})
    block = _block("table", kind="table")
    cell = {"row": 0, "column": 0, "row_span": 1, "column_span": 1, "content": "A", "latex": ""}
    block["table_cells"] = [cell, {**cell, "content": "B"}]
    with pytest.raises(ValueError, match="overlap"):
        PageBlock.model_validate(block)

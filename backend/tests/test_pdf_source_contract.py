"""Offline source identity, direct projection and durable dispatcher regressions."""
from __future__ import annotations

import copy
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import canonical_source_phase2 as phase2
from app.services import canonical_source_phase3 as phase3
from app.services import pdf_source_adapter as adapter
from app.services import pdf_source_contract as contract
from app.services import pdf_source_engine as engine
from app.services import pdf_source_evidence as evidence


def document():
    def block(identity, page, kind, content, **extra):
        return {"block_id": identity, "page_id": f"p{page:04d}", "page_number": page,
                "bbox": [0.1, 0.1, 0.9, 0.3], "kind": kind, "content": content, "latex": "",
                "table_cells": [], "printed_label": "", "figure_caption": "",
                "legibility": "legible", "uncertainty_notes": [], **extra}
    rows = [block("p0001-b0001", 1, "heading", "First Topic"),
            block("p0001-b0002", 1, "task", "7. Explain the result."),
            block("p0001-b0003", 1, "task", "(a) Justify your answer."),
            block("p0002-b0001", 2, "paragraph", "Unrelated material."),
            block("p0003-b0001", 3, "answer", "Printed answer.")]
    def task(identity, prompt, parent=""):
        return {"task_id": identity, "label": "7" if not parent else "(a)", "kind": "question",
                "parent_task_id": parent, "section_id": "sec-model", "prompt_block_ids": [prompt],
                "context_block_ids": [], "answer_block_ids": ["p0003-b0001"] if not parent else [],
                "figure_block_ids": []}
    return {"schema_version": "pdf-source-ir-1", "engine_version": adapter.ENGINE_VERSION,
            "pdf_sha256": "a" * 64, "status": "verified",
            "pages": [{"page_id": f"p{page:04d}", "page_number": page,
                       "blocks": [row for row in rows if row["page_number"] == page],
                       "audit": {"verdict": "verified", "source_usable": True, "issues": []}}
                      for page in (1, 2, 3)],
            "structure": {"title": "Chapter", "sections": [{"section_id": "sec-model", "title": "First Topic",
                           "parent_section_id": "", "level": 1,
                           "block_ids": [row["block_id"] for row in rows]}],
                          "tasks": [task("question-7", "p0001-b0002"), task("question-7-a", "p0001-b0003", "question-7")],
                          "relations": [], "unassigned_block_ids": [], "review_notes": []},
            "audit": {"verdict": "verified", "issues": []}, "provenance": {}}


def project(source=None, cropper=None):
    source = source or document()
    return adapter.project_document(source, source_filename="sample.pdf", job_id=51,
                                    evidence={"pdf_sha256": source["pdf_sha256"]}, cropper=cropper)


def test_direct_projection_preserves_model_refs_and_multipart_identity(monkeypatch):
    from app.services import canonical_source, generation
    monkeypatch.setattr(canonical_source, "compile_source", lambda *a, **k: pytest.fail("must not parse the view"))
    result = project()
    canonical = result["canonical"]
    item = phase2.inventory_from_canonical(canonical)["items"][0]
    assert item["qid"] == "QINV-0001"
    assert item["_acsd_task_id"] == "question-7"
    assert item["source_task_ids"] == ["question-7", "question-7-a"]
    assert item["source_block_ids"] == ["p0001-b0002", "p0001-b0003", "p0003-b0001"]
    assert "p0002-b0001" not in item["source_block_ids"]
    assert item["answer_block_ids"] == ["p0003-b0001"]
    assert "Printed answer" not in item["normalized_task"]
    assert "(a) Justify" in item["normalized_task"]
    assert generation._invalid_inventory_items({"items": [item]}) == []
    changed = document()
    changed["structure"]["tasks"][0]["context_block_ids"] = changed["structure"]["tasks"][0].pop("answer_block_ids")
    changed["structure"]["tasks"][0]["answer_block_ids"] = []
    assert phase3.source_contract_hash(project(changed)["canonical"]) != phase3.source_contract_hash(canonical)


def test_rendering_error_crops_exact_region_without_changing_evidence():
    source = document()
    source["pages"][0]["blocks"][1].update(kind="equation", content="Malformed printed math", latex=r"\unsupported{a}")
    original = copy.deepcopy(source)
    crops = []
    def crop(manifest, page, bbox, *, job_id):
        crops.append((manifest["pdf_sha256"], page, bbox, job_id))
        return {"url": "https://example.org/source-assets/51/exact.jpg", "sha256": "b" * 64,
                "page_number": page, "bbox": bbox}
    # Model a real renderer defect independently of its syntax classifier.
    from app.services import katex_rules
    original_validator = katex_rules.rich_text_issues
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(katex_rules, "rich_text_issues", lambda value: ["unsupported_katex_command"]
                      if "unsupported" in value else original_validator(value))
        result = project(source, cropper=crop)
    assert source == original
    assert len(crops) == 1 and crops[0][1:] == (1, [0.1, 0.1, 0.9, 0.3], 51)
    assert "exact.jpg" in result["canonical"]["tasks"][0]["display_prompt"]
    assert result["canonical"]["blocks"][1]["source_ir"]["latex"] == r"\unsupported{a}"
    assert result["report"]["phase2_inventory_ready"] is True
    from app.services.phase3.evidence import block_text, block_context
    projected = result["canonical"]["blocks"][1]
    assert "Malformed printed math" in block_text(projected)
    assert r"\\unsupported{a}" in block_text(projected)
    assert block_context(projected)["source_ir"] == original["pages"][0]["blocks"][1]
    assert result["canonical"]["tasks"][0]["content_objects"]["pdf_source"]["prompt"][0]["latex"] == r"\unsupported{a}"


def test_graph_compilation_does_not_parse_or_clean_new_source(monkeypatch):
    from app.services import generation
    source = document()
    source["pages"][1]["blocks"][0]["content"] = "Write \\noindent with  two spaces."
    result = project(source)
    monkeypatch.setattr(generation, "parse_mmd_sections", lambda *a, **k: pytest.fail("new source parsed as Markdown"))
    monkeypatch.setattr(phase3, "_virtual_missing_main_candidates", lambda *a, **k: pytest.fail("legacy section inference"))
    graph, _report = phase3.compile_semantic_graph(result["canonical"], source_text=result["mmd_text"], metadata={})
    rendered = phase3.render_semantic_source(graph, result["canonical"])
    assert adapter.literal_text("Write \\noindent with  two spaces.") in rendered
    assert graph["tasks"][0]["source_task_ids"] == ["question-7", "question-7-a"]
    direct_block = result["canonical"]["blocks"][3]
    assert phase3._block_excerpt_text({}, direct_block, result["canonical"]) == direct_block["display_text"]
    monkeypatch.setattr(phase3.structure, "numbered_heading_inventory", lambda *a, **k: pytest.fail("late numbered inference"))
    assert phase3._numbered_main_topic_mismatches(graph, canonical=result["canonical"]) == []
    assert not phase3.validate_graph(graph, canonical=result["canonical"], semantic_source=rendered)


def test_new_read_is_saved_and_resumed_without_reinvoking_engine(tmp_path, monkeypatch):
    path = tmp_path / "input.pdf"
    path.write_bytes(b"original source bytes")
    directory = tmp_path / "artifacts"
    source = document()
    source["pdf_sha256"] = evidence.file_sha256(path)
    calls = []
    def read(path, **kwargs):
        assert contract.selected(directory)
        calls.append(path)
        evidence.atomic_json(directory / contract.EVIDENCE_FILE, {"pdf_sha256": source["pdf_sha256"]})
        return source
    monkeypatch.setattr(engine, "read_pdf", read)
    first = contract.convert_pdf(path, job_id=51, artifact_dir=directory)
    (directory / "source.aegis-source.json").unlink()
    (directory / contract.IR_FILE).unlink()
    second = contract.convert_pdf(path, job_id=51, artifact_dir=directory)
    assert first == second and len(calls) == 1
    assert (directory / "source.aegis-source.json").is_file()
    assert (directory / contract.IR_FILE).is_file()
    monkeypatch.setattr(engine, "read_pdf", lambda *a, **k: pytest.fail("sealed source must not be read again"))
    page_bundle = phase3.load_page_evidence(path, directory, canonical=first["canonical"], saved_graphs=[{}])
    assert page_bundle["pages"][0]["blocks"][1]["block_id"] == "p0001-b0002"
    path.write_bytes(b"changed bytes")
    with pytest.raises(ValueError, match="frozen source-engine selection"):
        contract.convert_pdf(path, job_id=51, artifact_dir=directory)


def test_historical_and_partially_paid_sources_are_not_migrated(tmp_path):
    job = SimpleNamespace(module="build_concepts", status="uploaded", mmd_text="", question_inventory={}, generation_checkpoint={})
    path = tmp_path / "input.pdf"
    directory = tmp_path / "artifacts"
    assert contract.should_dispatch(job, path, directory)
    directory.mkdir()
    (directory / "old-paid-response.json").write_text("{}")
    assert not contract.should_dispatch(job, path, directory)
    job.mmd_text = "already converted immutable source"
    assert contract.should_dispatch(job, path, directory)  # frozen result branch


def test_task_order_uses_prompt_anchor_with_earlier_shared_context():
    source = document()
    second = source["structure"]["tasks"][1]
    second["parent_task_id"] = ""
    second["context_block_ids"] = ["p0001-b0001"]
    canonical = project(source)["canonical"]
    assert [row["qid"] for row in canonical["tasks"]] == ["QINV-0001", "QINV-0002"]
    assert canonical["tasks"][0]["source_start"] < canonical["tasks"][1]["source_start"]
    assert canonical["tasks"][1]["source_block_ids"][0] == "p0001-b0001"


def test_diagnostics_preserve_old_and_new_exact_block_identities():
    from app.services import autonomous_resolution, build_concepts_release, build_concepts_release_files, early_semantic_gate
    text = "BLK-00012 and (p0001-b0002), p10000-b10000."
    expected = ["BLK-00012", "p0001-b0002", "p10000-b10000"]
    invalid = "xp0001-b0002 p0001-b0002x _p0001-b0002 p0001-b0002_ p0001-b0002-tail p001-b0002 p0001-b002"
    for module in (autonomous_resolution, build_concepts_release, build_concepts_release_files, early_semantic_gate):
        assert module._BLOCK_ID_RE.findall(text) == expected
        assert module._BLOCK_ID_RE.findall(invalid) == []
    assert early_semantic_gate.block_ids_from_issues([text, invalid]) == set(expected)


def test_missing_new_projection_fails_without_legacy_fallback(tmp_path, monkeypatch):
    from app.services import uploads
    directory = tmp_path / "artifacts"
    evidence.atomic_json(directory / contract.SELECTION_FILE, {})
    monkeypatch.setattr(uploads, "source_artifact_directory", lambda _id: directory)
    job = SimpleNamespace(id=51, mmd_text="already converted")
    with pytest.raises(FileNotFoundError):
        phase2._load_or_refresh_for_job(job)
    with pytest.raises(FileNotFoundError):
        phase2.prepare_job_context(None, job)


def test_outer_conversion_preserves_historical_job_without_reader(tmp_path, monkeypatch):
    from app.services import uploads
    job = SimpleNamespace(id=51, module="build_concepts", status="converted", filename="original.pdf",
                          mmd_text="frozen view", openai_usage={"paid": 1})
    monkeypatch.setattr(uploads, "get_job", lambda *a, **k: job)
    monkeypatch.setattr(uploads, "upload_file_path", lambda _job: tmp_path / "original.pdf")
    monkeypatch.setattr(uploads, "source_artifact_directory", lambda _id: tmp_path / "artifacts")
    monkeypatch.setattr(uploads, "exclusive_job_operation", lambda _id: nullcontext())
    monkeypatch.setattr(uploads, "source_artifact_manifest", lambda _job: {})
    monkeypatch.setattr(contract, "convert_pdf", lambda *a, **k: pytest.fail("historical source reread"))
    db = SimpleNamespace(refresh=lambda _job: None)
    result = uploads.convert_job(db, 51, module="build_concepts")
    assert result["mmd_text"] == "frozen view" and job.openai_usage == {"paid": 1}


def test_fresh_public_conversion_uses_engine_and_authenticated_evidence(tmp_path, monkeypatch):
    import fitz
    from app import config
    from app.services import canonical_source_phase221_fallback as old_reader
    from app.services import generation_recovery, model_routing_run, uploads
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path)
    path = tmp_path / "original.pdf"
    with fitz.open() as pdf:
        for _ in range(3):
            pdf.new_page()
        pdf.save(path)
    job = SimpleNamespace(id=51, module="build_concepts", status="uploaded", filename=path.name,
                          upload_storage_key="", mmd_text="", question_inventory={}, generation_checkpoint={}, openai_usage={})
    monkeypatch.setattr(uploads, "get_job", lambda *a, **k: job)
    monkeypatch.setattr(uploads, "exclusive_job_operation", lambda _id: nullcontext())
    monkeypatch.setattr(generation_recovery, "require_mutation_allowed", lambda *a, **k: None)
    monkeypatch.setattr(model_routing_run, "bind_job", lambda _job: nullcontext())
    monkeypatch.setattr(old_reader, "reconstruct_pdf_to_acsd", lambda *a, **k: pytest.fail("old reader invoked"))
    calls = []
    def read(path, *, job_id, artifact_dir):
        calls.append(job_id)
        manifest = evidence.prepare_document(path, artifact_dir)
        source = document()
        source["pdf_sha256"] = manifest["pdf_sha256"]
        return source
    monkeypatch.setattr(engine, "read_pdf", read)
    db = SimpleNamespace(refresh=lambda _job: None, commit=lambda: None)
    result = uploads.convert_job(db, 51, module="build_concepts")
    assert result["status"] == "converted" and calls == [51]
    kinds = {row["kind"] for row in result["source_artifacts"]["files"]}
    assert {"pdf_original", "pdf_page_0001", "pdf_native_0001", "pdf_source_ir"} <= kinds
    original, spec = uploads.source_artifact_download(job, "pdf_original")
    assert original.read_bytes() == path.read_bytes() and spec["media_type"] == "application/pdf"
    canonical = phase2.prepare_job_context(db, job)
    assert adapter.is_canonical(canonical)
    assert phase3.load_page_evidence(path, uploads.source_artifact_directory(51), canonical=canonical)["pdf_sha256"] == evidence.file_sha256(path)


def test_suspension_keeps_selection_usage_and_uploaded_state(tmp_path, monkeypatch):
    from app.services import generation_recovery, model_routing_run, openai_usage, uploads
    class Suspended(BaseException):
        pass
    job = SimpleNamespace(id=51, module="build_concepts", status="uploaded", filename="original.pdf",
                          mmd_text="", question_inventory={}, generation_checkpoint={}, openai_usage={})
    monkeypatch.setattr(uploads, "get_job", lambda *a, **k: job)
    monkeypatch.setattr(uploads, "upload_file_path", lambda _job: tmp_path / "original.pdf")
    monkeypatch.setattr(uploads, "source_artifact_directory", lambda _id: tmp_path / "artifacts")
    monkeypatch.setattr(uploads, "exclusive_job_operation", lambda _id: nullcontext())
    monkeypatch.setattr(generation_recovery, "require_mutation_allowed", lambda *a, **k: None)
    monkeypatch.setattr(model_routing_run, "bind_job", lambda _job: nullcontext())
    monkeypatch.setattr(openai_usage, "cumulative_summary", lambda *a, **k: {"paid_receipt": 1})
    def suspend(*a, **k):
        evidence.atomic_json(tmp_path / "artifacts" / contract.SELECTION_FILE, {"version": "selected"})
        raise Suspended()
    monkeypatch.setattr(contract, "convert_pdf", suspend)
    commits = []
    db = SimpleNamespace(refresh=lambda _job: None, commit=lambda: commits.append(1))
    with pytest.raises(Suspended):
        contract.convert_job(db, 51, module="build_concepts")
    assert job.status == "uploaded" and job.mmd_text == ""
    assert job.openai_usage == {"paid_receipt": 1} and commits == [1]
    assert contract.selected(tmp_path / "artifacts")

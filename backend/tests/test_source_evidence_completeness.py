"""Source-contract regressions using the uploaded corpus's task forms; no API spend."""
from __future__ import annotations

import base64
import copy
import io
import json

from PIL import Image

from app.services import chapter_reading as reading
from app.services import chapter_reading_contract as reading_contract
from app.services import canonical_source_phase221_fallback as fallback
from tests.test_canonical_source_phase221_fallback import _make_pdf, _verified_provider


def test_direct_mmd_independent_comparison_retains_negation_equation_and_furniture(tmp_path, monkeypatch):
    monkeypatch.setattr(reading.config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(reading.config, "use_live_generation", lambda: True)
    monkeypatch.setattr(reading, "_memory_cache", {})
    original = "# Electricity\n\nCurrent does not flow in an open circuit.\n\n$V=IR$\n\nReprint 2024\n"
    proposed = original.replace("does not flow", "flows").replace("$V=IR$", "$V=I/R$").replace("Reprint 2024\n", "")
    calls = []

    def api(system, user, **kwargs):
        payload = json.loads(user)
        calls.append(payload)
        if "chunk_mmd" in payload:
            return {"normalized_mmd": proposed, "blocks": [{"kind": "prose", "label": "Circuit"}], "dropped_furniture": ["Reprint 2024"]}
        assert payload["original_mmd"] == original
        assert payload["proposed_normalized_mmd"] == proposed
        assert payload["dropped_furniture"] == ["Reprint 2024"]
        assert "negation" in system and "equation" in system
        return {"verdict": "needs_correction", "findings": [{"code": "source_meaning_changed", "explanation": "Negation and equation changed."}]}

    result = reading.read_chapter(original, api_call=api)
    assert len(calls) == 2
    assert result["source_comparisons"][0]["original_mmd"] == original
    assert result["source_comparisons"][0]["proposed_normalized_mmd"] == proposed
    assert result["review_flags"][0]["verdict"] == "needs_correction"
    assert result["normalized_mmd"].strip() == proposed.strip()  # Dissent remains advisory, never an intervention gate.
    assert reading.read_chapter(original, api_call=api) == result
    assert len(calls) == 2


def test_pdf_exemption_does_not_add_a_direct_mmd_comparison(monkeypatch):
    from types import ModuleType
    stub = ModuleType("source_evidence_test")
    stub.concepts_from_mmd = lambda source, **kwargs: source
    monkeypatch.setattr(reading_contract, "_reader_already_ruled", lambda: True)
    monkeypatch.setattr(reading, "read_chapter", lambda *a, **k: (_ for _ in ()).throw(AssertionError("duplicate reading")))
    reading_contract.install(stub)
    assert stub.concepts_from_mmd("verified PDF source", live=True) == "verified PDF source"


def test_existing_direct_mmd_resume_keeps_its_original_reading(tmp_path, monkeypatch):
    from types import ModuleType
    monkeypatch.setattr(reading.config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(reading, "_memory_cache", {})
    raw = "**The School Bell Rings Again**\n"
    legacy = {"mode": "live", "normalized_mmd": "# The School Bell Rings Again\n"}
    reading._store_cached(reading._cache_key(raw, version=1), legacy)
    stub = ModuleType("source_resume_test")
    stub._newest_compatible_concept_checkpoint = lambda checkpoint: {"stage": "settle"}
    assert reading_contract._reading_for_run(stub, raw, {}) == legacy
    assert reading.cached_reading(raw) is None  # Fresh runs need the new independent comparison.


def test_qualified_context_table_cells_and_source_captions_survive(tmp_path, monkeypatch):
    pdf = tmp_path / "source.pdf"
    _make_pdf(pdf)
    monkeypatch.setenv("AEGIS_PUBLIC_BASE_URL", "https://aegis.example")
    monkeypatch.setenv("AEGIS_SOURCE_ASSET_SECRET", "test-secret")
    monkeypatch.setattr(fallback, "_CACHE_DIR", tmp_path / "cache")

    def provider(pages):
        result = _verified_provider(pages)
        first, second = result["pages"]
        figure = copy.deepcopy(second["blocks"][1])
        figure.update(reading_order=4, caption="Fig. 1 — Cube", source_caption="Fig. 1 — Cube", public_alt="Solid with labelled vertices")
        table = copy.deepcopy(first["blocks"][1])
        table.update(reading_order=3, kind="table", text="", table_rows=[["Solid", "Faces"], ["", ""]],
                     table_cell_visual_refs=[{"row_index": 1, "column_index": 0, "figure_ref": {"page_id": first["page_id"], "reading_order": 4}}])
        first["blocks"].extend([table, figure])
        task = second["blocks"][0]
        task.update(text="Complete the table on the previous page.", linked_visual_orders=[], linked_context_orders=[],
                    linked_context_refs=[{"page_id": first["page_id"], "reading_order": 3}],
                    linked_visual_refs=[{"page_id": first["page_id"], "reading_order": 4}])
        second["blocks"] = [task]
        return result

    result = fallback.reconstruct_pdf_to_acsd(pdf, job_id=947, artifact_dir=tmp_path / "canonical-source", fallback_reason=["pdf_source"], provider=provider)
    canonical = result["canonical"]
    task = canonical["tasks"][0]
    assert task["content_objects"]["shared_context_blocks"][0]["source_id"] == "PDF-PAGE-0001-BLOCK-0003"
    assert task["content_objects"]["shared_context_blocks"][0]["table_cell_visual_refs"][0]["row_index"] == 1
    assert "Solid | Faces" in task["shared_context"]
    assert "[img " in task["shared_context"]
    assert len(task["image_urls"]) == 1
    figure = next(fig for fig in canonical["figures"] if fig["figure_id"] in task["figure_refs"])
    assert figure["source_caption"] == "Fig. 1 — Cube"
    assert figure["public_alt"] == "Solid with labelled vertices"
    assert task["raw_image_captions"][task["image_urls"][0]] == "Fig. 1 — Cube"
    assert task["display_image_captions"][task["image_urls"][0]] == "Solid with labelled vertices"
    inventory_item = fallback.phase2.inventory_from_canonical(canonical)["items"][0]
    assert inventory_item["content_objects"]["source_relationships"]["linked_context_refs"] == [{"page_id": "PDF-PAGE-0001", "reading_order": 3}]
    assert inventory_item["content_objects"]["source_visuals"][0]["source_caption"] == "Fig. 1 — Cube"
    assert inventory_item["content_objects"]["source_visuals"][0]["asset_url"] == task["image_urls"][0]
    assert "[img " in inventory_item["shared_context"]

    page_acsd = json.loads((tmp_path / "canonical-source" / fallback.GPT_PAGE_ACSD_FILENAME).read_text())
    task.pop("gpt_pdf_acsd_prior_context", None)
    task["shared_context"] = "Previously recorded source condition must remain."
    task["content_objects"]["shared_context_blocks"] = [{"source_id": "PRIOR-SOURCE", "display_text": task["shared_context"]}]
    fallback.apply_page_acsd_relationships(canonical, page_acsd)
    assert "Previously recorded source condition must remain." in task["shared_context"]
    assert "Solid | Faces" in task["shared_context"]
    before = task["shared_context"]
    fallback.apply_page_acsd_relationships(canonical, page_acsd)
    assert task["shared_context"] == before
    assert any(obj["source_id"] == "PRIOR-SOURCE" for obj in task["content_objects"]["shared_context_blocks"])

    # A source dependency outside the extraction batch is resolved by the
    # existing full-chapter outline, then independently reviewed with that
    # outline. The page transcriber need not guess an unseen block identity.
    source_task = page_acsd["pages"][1]["blocks"][0]
    source_task["linked_context_refs"] = []
    source_task["linked_visual_refs"] = []
    outline, _flags = fallback._normalize_chapter_outline(page_acsd, {
        "chapter_title": "Solids", "topics": [{"title": "Solids", "kind": "content", "start_page_id": "PDF-PAGE-0001", "start_reading_order": 1}],
        "task_partitions": [], "whole_tasks": [{"page_id": "PDF-PAGE-0002", "reading_order": 1, "task_kind": "question"}], "notes": [],
        "task_dependency_links": [{"page_id": "PDF-PAGE-0002", "reading_order": 1,
                                   "linked_context_refs": [{"page_id": "PDF-PAGE-0001", "reading_order": 3}],
                                   "linked_visual_refs": [], "source_evidence": "Complete the table on the previous page."}],
    })
    assert outline is not None and outline["task_dependency_links"]
    page_acsd["chapter_outline"] = outline
    task.pop("gpt_pdf_acsd_prior_context", None)
    task["shared_context"] = ""
    task["content_objects"] = {}
    fallback.apply_page_acsd_relationships(canonical, page_acsd)
    assert "Solid | Faces" in task["shared_context"]
    assert "[img " in task["shared_context"]
    assert task["gpt_pdf_acsd_relationship"]["linked_context_refs"] == [{"page_id": "PDF-PAGE-0001", "reading_order": 3}]


def test_existing_pdf_verifier_receives_actual_candidate_crop(monkeypatch):
    output = io.BytesIO()
    Image.new("RGB", (1000, 1000), "white").save(output, format="JPEG")
    page = fallback.PdfPage(page_id="PDF-PAGE-0001", page_number=1, text="Labels and units", width=1000, height=1000,
                            image_data_url="data:image/jpeg;base64," + base64.b64encode(output.getvalue()).decode())
    blocks = _verified_provider([page])["pages"][0]["blocks"]
    blocks.append({"reading_order": 3, "kind": "figure", "bbox": [100, 100, 800, 800], "confidence": 1, "caption": "", "text": ""})
    calls = []

    def api(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return {"pages": [{"page_id": page.page_id, "blocks": blocks, "confidence": 1, "dropped_furniture": ["Reprint"]}]}
        assert len(kwargs["pages"]) == 2
        crop = kwargs["pages"][1]
        assert crop.evidence_id == "PDF-PAGE-0001-FIGURE-CROP-0003"
        with Image.open(io.BytesIO(base64.b64decode(crop.image_data_url.split(",")[1]))) as image:
            assert image.size == (700, 700)
        assert "legend" in kwargs["system"] and "source_caption" in kwargs["system"]
        return {"verdict": "verified", "approved_page_ids": [page.page_id], "rejected_page_ids": [], "confidence": 1, "issues": []}

    monkeypatch.setattr(fallback.phase22, "_openai_multimodal_json", api)
    result = fallback.extract_batch_via_openai([page])
    assert result["status"] == "verified"
    assert result["pages"][0]["dropped_furniture"] == ["Reprint"]
    assert len(calls) == 2


def test_identical_table_grids_keep_their_own_page_figures(tmp_path, monkeypatch):
    pdf = tmp_path / "source.pdf"
    _make_pdf(pdf)
    monkeypatch.setenv("AEGIS_PUBLIC_BASE_URL", "https://aegis.example")
    monkeypatch.setenv("AEGIS_SOURCE_ASSET_SECRET", "test-secret")
    monkeypatch.setattr(fallback, "_CACHE_DIR", tmp_path / "cache")

    def provider(pages):
        result = _verified_provider(pages)
        figure_template = copy.deepcopy(result["pages"][1]["blocks"][1])
        task_template = copy.deepcopy(result["pages"][1]["blocks"][0])
        for page in result["pages"]:
            heading = copy.deepcopy(result["pages"][0]["blocks"][0])
            heading.update(reading_order=1, text=f"Topic {page['page_number']}")
            table = copy.deepcopy(heading)
            table.update(kind="table", reading_order=2, heading_level=0, text="", table_rows=[["Solid", "Faces"], ["", ""]],
                         table_cell_visual_refs=[{"row_index": 1, "column_index": 0, "figure_ref": {"page_id": page["page_id"], "reading_order": 3}}])
            figure = copy.deepcopy(figure_template)
            figure.update(reading_order=3, caption=f"Solid {page['page_number']}")
            task = copy.deepcopy(task_template)
            task.update(reading_order=4, text=f"Complete table {page['page_number']}.", linked_visual_orders=[], linked_context_orders=[2])
            page["blocks"] = [heading, table, figure, task]
        return result

    result = fallback.reconstruct_pdf_to_acsd(pdf, job_id=948, artifact_dir=tmp_path / "canonical-source", fallback_reason=["pdf_source"], provider=provider)
    canonical = result["canonical"]
    tables = [block for block in canonical["blocks"] if block.get("table_cell_visual_refs")]
    assert len(tables) == 2
    assert {table["source_page_block_ref"]["page_id"] for table in tables} == {"PDF-PAGE-0001", "PDF-PAGE-0002"}
    for table in tables:
        owner = table["source_page_block_ref"]["page_id"]
        assert table["table_cell_visual_refs"][0]["figure_ref"]["page_id"] == owner
    table_by_id = {table["block_id"]: table for table in tables}
    for task in canonical["tasks"]:
        context = task["content_objects"]["shared_context_blocks"][0]
        assert table_by_id[context["block_id"]]["source_page_block_ref"]["page_id"] == context["page_id"]


def test_new_source_review_contract_cannot_be_satisfied_by_old_verification():
    assert not fallback._legacy_cache_envelope_matches(
        {"version": "2.4.0", "model": fallback.config.OPENAI_MODEL, "pdf_sha256": "old"},
        fallback_version="2.4.0", pdf_sha256="old",
    )

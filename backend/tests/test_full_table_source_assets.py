"""Complete source-table pixels remain one owned stimulus, with no provider spend."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import fitz
import pytest

from app.services import canonical_source_phase221_fallback as fallback
from app.services import source_asset_store


PAGE_ID = "PDF-PAGE-0001"


def _make_table_pdf(path: Path) -> None:
    document = fitz.open()
    page = document.new_page(width=600, height=800)
    page.insert_text((60, 55), "Shapes and their properties", fontsize=18)
    page.insert_text((60, 110), "Read the table and compare the shapes.", fontsize=12)
    for x in (60, 300, 540):
        page.draw_line((x, 160), (x, 560), color=(0, 0, 0), width=2)
    for y in (160, 260, 410, 560):
        page.draw_line((60, y), (540, y), color=(0, 0, 0), width=2)
    page.insert_text((100, 220), "Shape", fontsize=18)
    page.insert_text((355, 220), "Corners", fontsize=18)
    page.draw_polyline([(120, 360), (175, 280), (230, 360), (120, 360)], color=(1, 0, 0), width=3)
    page.insert_text((390, 345), "3", fontsize=22)
    page.draw_circle((160, 480), 40, color=(0, 0, 1), width=3)
    page.insert_text((390, 490), "0", fontsize=22)
    document.save(path)
    document.close()


def _bundle() -> dict:
    def block(kind, order, bbox, **extra):
        return {"kind": kind, "reading_order": order, "bbox": bbox,
                "text": "", "heading_level": 0, "source_label": "", "latex": "",
                "table_rows": [], "linked_visual_orders": [], "linked_context_orders": [],
                "caption": "", "confidence": 0.999, **extra}
    table = block("table", 3, [98, 198, 902, 702],
        table_rows=[["Shape", "Corners"], ["", "3"], ["", "0"]],
        table_cell_visual_refs=[
            {"row_index": 1, "column_index": 0, "figure_ref": {"page_id": PAGE_ID, "reading_order": 4}},
            {"row_index": 2, "column_index": 0, "figure_ref": {"page_id": PAGE_ID, "reading_order": 5}},
        ])
    return {"pages": [{"page_id": PAGE_ID, "page_number": 1, "confidence": 0.999, "blocks": [
        block("heading", 1, [100, 35, 900, 90], text="Shapes and their properties", heading_level=1),
        block("task", 2, [100, 105, 900, 155], text="Read the table and compare the shapes.", source_label="Activity", linked_context_orders=[3]),
        table,
        block("figure", 4, [190, 340, 395, 465], caption="Triangle"),
        block("figure", 5, [190, 540, 350, 665], caption="Circle"),
    ]}]}


@pytest.fixture(autouse=True)
def _isolated_assets(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_PUBLIC_BASE_URL", "https://aegis.example.org")
    monkeypatch.setattr(source_asset_store.config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(fallback, "_CACHE_DIR", tmp_path / "cache")


def test_complete_visual_table_crop_matches_every_source_pixel_and_keeps_rows(tmp_path):
    pdf = tmp_path / "table.pdf"
    _make_table_pdf(pdf)
    bundle = _bundle()
    original_table = copy.deepcopy(bundle["pages"][0]["blocks"][2])
    count = fallback.materialize_visual_assets(pdf, bundle, job_id=801, artifact_dir=tmp_path / "artifacts")
    table = bundle["pages"][0]["blocks"][2]
    assert count == 3  # One complete table and the two original figure records.
    assert table["asset_scope"] == "full_table"
    assert table["asset_bbox"] == original_table["bbox"]
    assert table["table_rows"] == original_table["table_rows"]
    assert table["table_cell_visual_refs"] == original_table["table_cell_visual_refs"]
    data = source_asset_store.stored_asset_path(table["asset_filename"]).read_bytes()
    assert hashlib.sha256(data).hexdigest() + ".jpg" == table["asset_filename"]
    with fitz.open(pdf) as document:
        page = document[0]
        clip = fallback._clip_bbox(page, original_table["bbox"])
        expected = page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=clip, alpha=False).tobytes("jpeg", jpg_quality=88)
        assert data == expected
        assert set(page.get_text(clip=clip).split()) == {"Shape", "Corners", "3", "0"}
    assert fallback._page_context_text(table).count("[img ") == 1
    assert table["asset_url"] in fallback._page_context_text(table)
    assert fallback._reconstruction_manifest(bundle, artifact_dir=tmp_path / "artifacts", relationship_count=1)["asset_count"] == 3
    assert any("crop review not recorded" in flag for flag in bundle["pages"][0]["review_flags"])


def test_full_table_reaches_owning_task_context_and_canonical_source(tmp_path):
    pdf = tmp_path / "table.pdf"
    _make_table_pdf(pdf)
    def provider(pages):
        return {**_bundle(), "status": "verified", "verification": {"verdict": "verified", "confidence": 0.999,
            "approved_page_ids": [PAGE_ID], "rejected_page_ids": [], "findings": []}}
    result = fallback.reconstruct_pdf_to_acsd(pdf, job_id=802, artifact_dir=tmp_path / "source", fallback_reason=["test"], provider=provider)
    canonical = result["canonical"]
    table = next(block for block in canonical["blocks"] if block.get("asset_scope") == "full_table")
    task = canonical["tasks"][0]
    assert table["asset_url"] in task["shared_context"]
    assert task["shared_context"].count("[img ") == 1
    assert task["image_urls"] == [table["asset_url"]]
    context = task["content_objects"]["shared_context_blocks"][0]
    assert context["asset_url"] == table["asset_url"]
    assert context["table_rows"] == _bundle()["pages"][0]["blocks"][2]["table_rows"]
    assert context["block_id"] == table["block_id"]
    item = fallback.phase2.inventory_from_canonical(canonical)["items"][0]
    assert table["asset_url"] in item["shared_context"]
    assert item["content_objects"]["shared_context_blocks"][0]["asset_scope"] == "full_table"
    snapshot = json.loads(json.dumps(canonical))
    assert table["asset_url"] in snapshot["tasks"][0]["shared_context"]
    from app.services import canonical_source_phase21_visuals as visuals
    assert not visuals.orphan_figure_issues(canonical)
    detached = copy.deepcopy(canonical)
    detached["tasks"][0]["shared_context"] = ""
    assert len(visuals.orphan_figure_issues(detached)) == 2
    wrong_owner = copy.deepcopy(canonical)
    wrong_owner["tasks"][0]["content_objects"]["shared_context_blocks"][0]["block_id"] = "ANOTHER-TABLE"
    assert len(visuals.orphan_figure_issues(wrong_owner)) == 2
    # Identical image bytes printed elsewhere are another source occurrence.
    # The table owns this exact cell figure, never every matching crop URL.
    duplicated_pixels = copy.deepcopy(canonical)
    visual = table["table_cell_visuals"][0]
    original_figure = next(figure for figure in canonical["figures"] if figure["figure_id"] == visual["figure_id"])
    original_block = next(block for block in canonical["blocks"] if block["block_id"] == visual["block_id"])
    other_ref = {"page_id": PAGE_ID, "reading_order": 99}
    other_figure = {**copy.deepcopy(original_figure), "figure_id": "FIG-UNRELATED", "block_id": "BLK-UNRELATED", "source_page_block_ref": other_ref}
    other_block = {**copy.deepcopy(original_block), "block_id": "BLK-UNRELATED", "source_task_ids": ["TASK-UNRELATED"], "task_ids": [], "source_page_block_ref": other_ref}
    duplicated_pixels["figures"].append(other_figure)
    duplicated_pixels["blocks"].append(other_block)
    assert other_figure["image_urls"] == original_figure["image_urls"]
    assert [issue["figure_id"] for issue in visuals.orphan_figure_issues(duplicated_pixels)] == ["FIG-UNRELATED"]


def test_existing_verifier_receives_whole_table_crop_and_records_its_scope(tmp_path, monkeypatch):
    pdf = tmp_path / "table.pdf"
    _make_table_pdf(pdf)
    pages = fallback.collect_pdf_pages(pdf)
    seen = []
    def provider(**kwargs):
        seen.append(kwargs)
        if len(seen) == 1:
            return _bundle()
        assert any(page.evidence_id == PAGE_ID + "-TABLE-CROP-0003" for page in kwargs["pages"])
        assert "every header" in kwargs["system"]
        return {"verdict": "verified", "confidence": 0.999, "approved_page_ids": [PAGE_ID], "rejected_page_ids": [], "findings": []}
    monkeypatch.setattr(fallback.phase22, "_openai_multimodal_json", provider)
    result = fallback.extract_batch_via_openai(pages)
    assert len(seen) == 2
    table = result["pages"][0]["blocks"][2]
    assert table["full_table_crop_review"]["version"] == fallback._FULL_TABLE_REVIEW_VERSION
    fallback.materialize_visual_assets(pdf, result, job_id=803, artifact_dir=tmp_path / "source")
    assert not any("crop review not recorded" in flag for flag in result["pages"][0].get("review_flags", []))


@pytest.mark.parametrize("defect", ["missing_bbox", "clipped_bbox", "cross_page", "unknown_figure", "outside_figure"])
def test_incomplete_table_crop_is_named_and_does_not_replace_the_table(tmp_path, defect):
    pdf = tmp_path / "table.pdf"
    _make_table_pdf(pdf)
    bundle = _bundle()
    table = bundle["pages"][0]["blocks"][2]
    if defect == "missing_bbox":
        table.pop("bbox")
    elif defect == "clipped_bbox":
        table["bbox"] = [-20, 200, 900, 700]
    elif defect == "cross_page":
        table["table_cell_visual_refs"][0]["figure_ref"]["page_id"] = "PDF-PAGE-0002"
    elif defect == "unknown_figure":
        table["table_cell_visual_refs"][0]["figure_ref"]["reading_order"] = 99
    else:
        table["bbox"] = [100, 200, 900, 400]
    original = copy.deepcopy(table)
    fallback.materialize_visual_assets(pdf, bundle, job_id=804, artifact_dir=tmp_path / "source")
    assert table == original
    assert "asset_scope" not in table
    assert any("full table asset could not be materialized" in flag for flag in bundle["pages"][0]["review_flags"])


def test_text_only_table_is_not_cropped(tmp_path):
    pdf = tmp_path / "table.pdf"
    _make_table_pdf(pdf)
    bundle = _bundle()
    table = bundle["pages"][0]["blocks"][2]
    table["table_cell_visual_refs"] = []
    original = copy.deepcopy(table)
    fallback.materialize_visual_assets(pdf, bundle, job_id=805, artifact_dir=tmp_path / "source")
    assert table == original
    assert "asset_url" not in table

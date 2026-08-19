"""Step 12 — the PDF and scanned corpus cases (spec D2/D3, cases 8–10).

Built at test time with fitz (PyMuPDF is version-floating, so PDF cases
are STRUCTURE-locked, never byte-locked) and driven through
``reconstruct_pdf_to_acsd`` with scripted verified providers — the same
reader, verification gate, asset materialization, and sealed-bundle
mechanics production uses. The scripted provider IS the model verdict.
"""
from __future__ import annotations

import json
from pathlib import Path

import fitz
import pytest

from app.services import canonical_source_phase221_fallback as fallback


def _base_env(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_PUBLIC_BASE_URL", "https://aegis.example")
    monkeypatch.setenv("AEGIS_SOURCE_ASSET_SECRET", "test-secret")
    monkeypatch.setattr(fallback, "_CACHE_DIR", tmp_path / "cache")


def _block(order, kind, text, bbox, **over):
    row = {
        "reading_order": order, "kind": kind, "bbox": bbox, "text": text,
        "heading_level": 1 if kind == "heading" else 0, "source_label": "",
        "latex": "", "table_rows": [], "linked_visual_orders": [],
        "linked_context_orders": [], "caption": "", "confidence": 0.99,
    }
    row.update(over)
    return row


def _provider(blocks_by_page):
    def provider(pages):
        rows = []
        for page in pages:
            rows.append({
                "page_id": page.page_id,
                "page_number": page.page_number,
                "confidence": 0.99,
                "blocks": json.loads(
                    json.dumps(blocks_by_page[page.page_number])
                ),
            })
        return {
            "status": "verified",
            "pages": rows,
            "verification": {
                "verdict": "verified",
                "approved_page_ids": [page.page_id for page in pages],
                "rejected_page_ids": [],
                "confidence": 0.99,
                "issues": [],
            },
        }
    return provider


def _reconstruct(pdf, provider, tmp_path, job_id):
    return fallback.reconstruct_pdf_to_acsd(
        pdf,
        job_id=job_id,
        artifact_dir=tmp_path / "artifacts",
        fallback_reason=["pdf_source"],
        provider=provider,
    )


def _tasks(bundle):
    return [
        block
        for page in bundle["reconstruction"]["pages"]
        for block in page["blocks"]
        if block.get("kind") == "task"
    ] if "reconstruction" in bundle else []


def test_cbse10_physics_figures_text_pdf(tmp_path, monkeypatch):
    """Case 8 — image-dependent tasks: a figure on page 1, a 'describe
    Fig.' task on page 2 referencing it across the page break."""
    _base_env(monkeypatch, tmp_path)
    pdf = tmp_path / "cbse10_physics_figures.pdf"
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    page.insert_text((50, 70), "10 Light - Reflection", fontsize=18)
    page.insert_text((50, 130), "A ray of light obeys two laws.", fontsize=12)
    page.draw_rect(fitz.Rect(60, 200, 420, 460), fill=(0.85, 0.85, 0.85))
    page.insert_text((80, 480), "Fig. 10.1 - Reflection at a mirror.",
                     fontsize=11)
    page2 = doc.new_page(width=600, height=800)
    page2.insert_text((50, 70), "Questions", fontsize=15)
    page2.insert_text((50, 120), "Describe Fig. 10.1 in your own words.",
                      fontsize=12)
    doc.save(pdf)
    doc.close()

    provider = _provider({
        1: [
            _block(1, "heading", "10 Light - Reflection", [70, 50, 720, 120]),
            _block(2, "paragraph", "A ray of light obeys two laws.",
                   [70, 130, 800, 210]),
            _block(3, "figure", "", [80, 260, 560, 620],
                   caption="Fig. 10.1 - Reflection at a mirror."),
        ],
        2: [
            _block(1, "heading", "Questions", [70, 50, 400, 120]),
            _block(2, "task", "Describe Fig. 10.1 in your own words.",
                   [70, 130, 850, 210], source_label="Questions",
                   linked_visual_orders=[]),
        ],
    })
    bundle = _reconstruct(pdf, provider, tmp_path, job_id=812)
    reconstruction = bundle["reconstruction"]
    assert reconstruction["status"] == "verified"
    assert reconstruction["page_count"] == 2
    # Structure locks: the model-ruled task and the figure both survive
    # into the canonical MMD; the figure crop materialized as an asset.
    assert "Describe Fig. 10.1 in your own words." in bundle["mmd_text"]
    assert (tmp_path / "artifacts" / "assets").exists()
    assert any((tmp_path / "artifacts" / "assets").iterdir())


def test_mh6_maths_scanned_image_only(tmp_path, monkeypatch):
    """Case 9 — the scanned route: pages with NO text layer at all. The
    reader renders page images regardless, so the scripted verdicts are
    the only text the pipeline ever sees."""
    _base_env(monkeypatch, tmp_path)
    pdf = tmp_path / "mh6_maths_scanned.pdf"
    doc = fitz.open()
    for _ in range(2):
        page = doc.new_page(width=600, height=800)
        # Image-only content: filled shapes, zero extractable text.
        page.draw_rect(fitz.Rect(40, 40, 560, 760), fill=(0.96, 0.96, 0.9))
        page.draw_rect(fitz.Rect(80, 100, 520, 300), fill=(0.7, 0.7, 0.9))
    doc.save(pdf)
    doc.close()
    # Prove the fixture is genuinely scanned-shaped.
    check = fitz.open(pdf)
    assert all(not page.get_text().strip() for page in check)
    check.close()

    provider = _provider({
        1: [
            _block(1, "heading", "6 Perimeter", [70, 60, 500, 130]),
            _block(2, "paragraph",
                   "Perimeter is the distance around a figure.",
                   [70, 140, 820, 260]),
        ],
        2: [
            _block(1, "task",
                   "Find the perimeter of the rectangle in the picture.",
                   [70, 60, 850, 200], source_label="Try this"),
            _block(2, "figure", "", [80, 260, 560, 620],
                   caption="A rectangle drawn on a grid."),
        ],
    })
    bundle = _reconstruct(pdf, provider, tmp_path, job_id=813)
    assert bundle["reconstruction"]["status"] == "verified"
    assert "Find the perimeter of the rectangle" in bundle["mmd_text"]


def test_cbse5_evs_mixed_sparse_lower_grade(tmp_path, monkeypatch):
    """Case 10 — the lower-grade PDF: sparse pages, one figure, an
    activity-embedded question. The population CLAUDE.md names as
    hardest-bitten gets a corpus case of its own."""
    _base_env(monkeypatch, tmp_path)
    pdf = tmp_path / "cbse5_evs_mixed.pdf"
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    page.insert_text((50, 70), "Super Senses", fontsize=18)
    page.insert_text((50, 130), "An ant smells its way home.", fontsize=12)
    page.draw_rect(fitz.Rect(60, 200, 300, 360), fill=(0.9, 0.85, 0.8))
    page.insert_text((50, 420), "Activity: Watch an ant line. What happens "
                                "when you rub the line away?", fontsize=12)
    doc.save(pdf)
    doc.close()

    provider = _provider({
        1: [
            _block(1, "heading", "Super Senses", [70, 50, 400, 120]),
            _block(2, "paragraph", "An ant smells its way home.",
                   [70, 130, 700, 200]),
            _block(3, "figure", "", [80, 260, 400, 480],
                   caption="Ants following a scent line."),
            _block(4, "task",
                   "Watch an ant line. What happens when you rub the line "
                   "away?",
                   [70, 540, 850, 640], source_label="Activity"),
        ],
    })
    bundle = _reconstruct(pdf, provider, tmp_path, job_id=814)
    assert bundle["reconstruction"]["status"] == "verified"
    assert bundle["reconstruction"]["page_count"] == 1
    assert "What happens when you rub the line" in bundle["mmd_text"]

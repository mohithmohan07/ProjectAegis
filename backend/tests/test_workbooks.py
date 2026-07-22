"""Create Workbooks (revision-PDF generator): metadata, dry generation, API."""
import io
from pathlib import Path

import fitz
import pytest
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app.services import workbooks


def _make_source_pdf(path: Path) -> None:
    """A small but structurally NCERT-like chapter source PDF."""
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(60, 780, "Quadrilaterals")
    c.setFont("Helvetica", 11)
    y = 740
    lines = [
        "4.1 Introduction To Quadrilaterals",
        "A quadrilateral is a four-sided polygon with four vertices and four angles. "
        "The sum of its interior angles is always 360 degrees, a property used in many "
        "constructions and proofs throughout this chapter of the textbook.",
        "4.2 Properties Of Parallelograms",
        "A parallelogram is a quadrilateral whose opposite sides are parallel and equal. "
        "Its diagonals bisect each other, and opposite angles are equal, which gives us "
        "powerful tests for identifying parallelograms in coordinate geometry problems.",
    ]
    for ln in lines:
        for chunk in [ln[i:i + 90] for i in range(0, len(ln), 90)]:
            c.drawString(60, y, chunk)
            y -= 16
        y -= 8
    c.save()


@pytest.fixture()
def source_pdf(tmp_path):
    p = tmp_path / "CBSE_NCERT_G08_CH04_QUADRILATERALS.pdf"
    _make_source_pdf(p)
    return p


def test_infer_workbook_metadata():
    meta = workbooks.infer_workbook_metadata(
        "CBSE_NCERT_G08_CH04_QUADRILATERALS.pdf", "Mathematics")
    assert meta["grade"] == "Grade 8"
    assert meta["grade_folder"] == "Class 08"
    assert meta["subject"] == "Mathematics"
    assert meta["chapter_number"] == "04"
    assert meta["chapter_title"] == "Quadrilaterals"

    meta_un = workbooks.infer_workbook_metadata(
        "CBSE_NCERT_G08_UN02_VALUES_AND_DISPOSITIONS.pdf", "English")
    assert meta_un["chapter_number"] == "02"
    assert meta_un["chapter_title"] == "Values and Dispositions"


def test_infer_metadata_rejects_unknown_pattern():
    with pytest.raises(ValueError):
        workbooks.infer_workbook_metadata("random_notes.pdf")


def test_dry_generation_renders_real_pdf(source_pdf):
    result = workbooks.generate(source_pdf, "Mathematics", live=False)
    assert result["mode"] == "dry"
    out = Path(result["output_pdf"])
    assert out.exists()
    # Published into the subject-wise library tree.
    assert "Class 08" in str(out) and "Mathematics" in str(out)

    doc = fitz.open(out)
    text = "".join(page.get_text() for page in doc)
    assert doc.page_count >= 2
    assert "Quadrilaterals" in text
    # Real source content flowed through the renderer.
    assert "four-sided polygon" in text
    assert Path(result["build_log"]).exists()
    doc.close()


def test_api_generate_library_and_download(client, source_pdf):
    files = {"file": (source_pdf.name, io.BytesIO(source_pdf.read_bytes()),
                      "application/pdf")}
    from tests.conftest import stream_result
    body = stream_result(
        client.post("/workbooks/generate", files=files, data={"subject": "Mathematics"}))
    assert body["mode"] == "dry"
    assert "MODE: DRY" in body["log"]
    assert body["openai_usage"]["request_count"] == 0
    assert body["openai_usage"]["estimated_cost_usd"] == 0.0

    lib = client.get("/workbooks/library").json()
    entry = next(e for e in lib if e["name"] == f"{source_pdf.stem}.pdf")
    assert entry["class_folder"] == "Class 08"
    assert entry["subject"] == "Mathematics"
    assert entry["openai_usage"] == body["openai_usage"]

    dl = client.get(f"/workbooks/file?rel={entry['rel']}")
    assert dl.status_code == 200
    assert dl.headers["content-type"].startswith("application/pdf")


def test_library_file_traversal_blocked(client):
    assert client.get("/workbooks/file?rel=../../app/main.py").status_code == 404

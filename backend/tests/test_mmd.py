"""MMD conversion: image vs PDF routing + graceful conversion errors.

Live Mathpix is never hit in tests; the conversion functions are monkeypatched.
These tests pin the routing (images -> /v3/text path, PDFs -> vendored client)
and that a conversion failure surfaces as a clean 400, not a 500.
"""
import io

from pathlib import Path

from app.services import mmd


def test_dry_image_does_not_call_mathpix(tmp_path: Path):
    img = tmp_path / "scan.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0not-a-real-jpeg")
    # Dry mode: returns the placeholder stub, never touches Mathpix.
    out = mmd.to_mmd(img, live=False)
    assert out.startswith("# scan")
    assert "binary" in out.lower()


def test_live_routes_image_to_text_endpoint(tmp_path: Path, monkeypatch):
    img = tmp_path / "hand.png"
    img.write_bytes(b"\x89PNGfake")
    called = {}

    def fake_image(path: Path) -> str:
        called["image"] = path
        return "# hand\n\nWhat is $2+2$ ?\n"

    def fake_pdf(path: Path) -> str:  # must NOT be called for an image
        called["pdf"] = path
        return "nope"

    monkeypatch.setattr(mmd, "_mathpix_image_to_mmd", fake_image)
    monkeypatch.setattr(mmd, "_live_pdf_to_mmd", fake_pdf)

    out = mmd.to_mmd(img, live=True)
    assert out == "# hand\n\nWhat is $2+2$ ?\n"
    assert "image" in called and "pdf" not in called


def test_live_routes_pdf_to_vendored_client(tmp_path: Path, monkeypatch):
    pdf = tmp_path / "chapter.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    called = {}

    def fake_image(path: Path) -> str:
        called["image"] = path
        return "img"

    def fake_pdf(path: Path) -> str:
        called["pdf"] = path
        return "# pdf\n\nbody\n"

    monkeypatch.setattr(mmd, "_mathpix_image_to_mmd", fake_image)
    monkeypatch.setattr(mmd, "_live_pdf_to_mmd", fake_pdf)

    out = mmd.to_mmd(pdf, live=True)
    assert out == "# pdf\n\nbody\n"
    assert "pdf" in called and "image" not in called


def test_live_conversion_failure_becomes_conversion_error(tmp_path: Path, monkeypatch):
    img = tmp_path / "bad.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0")

    def boom(path: Path) -> str:
        raise RuntimeError("Invalid content type: image/jpeg")

    monkeypatch.setattr(mmd, "_mathpix_image_to_mmd", boom)
    try:
        mmd.to_mmd(img, live=True)
        assert False, "expected ConversionError"
    except mmd.ConversionError as e:
        assert isinstance(e, ValueError)  # so the API returns 400
        assert "bad.jpg" in str(e)


def _force_live_image_failure(monkeypatch):
    monkeypatch.setattr(mmd.config, "use_live_mmd", lambda: True)

    def boom(path: Path) -> str:
        raise mmd.ConversionError("Mathpix image OCR failed: unreadable")

    monkeypatch.setattr(mmd, "_mathpix_image_to_mmd", boom)


def test_build_assessments_image_upload_failure_returns_400(client, monkeypatch):
    _force_live_image_failure(monkeypatch)
    files = {"file": ("images.jpg", io.BytesIO(b"\xff\xd8\xff\xe0"), "image/jpeg")}
    r = client.post("/build-assessments/uploads?upload_type=handwritten", files=files)
    assert r.status_code == 400
    assert "Mathpix" in r.json()["detail"]


def test_build_concepts_image_upload_failure_returns_400(client, monkeypatch):
    _force_live_image_failure(monkeypatch)
    files = {"file": ("notes.png", io.BytesIO(b"\x89PNG"), "image/png")}
    r = client.post("/build-concepts/post-learning/uploads", files=files)
    assert r.status_code == 400
    assert "Mathpix" in r.json()["detail"]

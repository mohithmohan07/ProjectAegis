"""MMD conversion: image vs PDF routing + graceful conversion errors.

Live Mathpix is never hit in tests; the conversion functions are monkeypatched.
These tests pin the routing (images -> /v3/text path, PDFs -> vendored client)
and that a conversion failure surfaces as a clean 400, not a 500.
"""
import io

from pathlib import Path

from app.services import mmd


def test_text_upload_decodes_utf8_independently_of_windows_locale(
    tmp_path: Path,
):
    source = tmp_path / "nationalism.mmd"
    source.write_bytes(
        "Frédéric Sorrieu’s vision — ₹500; \\(a_n = a + (n-1)d\\).".encode(
            "utf-8"
        )
    )

    out = mmd.to_mmd(source, live=False)

    assert "Frédéric Sorrieu’s vision — ₹500" in out
    assert "FrÃ" not in out
    assert "â€" not in out


def test_markdown_upload_accepts_utf8_bom(tmp_path: Path):
    source = tmp_path / "chapter.md"
    source.write_bytes(
        b"\xef\xbb\xbf" + "Électricité — circuits".encode("utf-8")
    )

    out = mmd.to_mmd(source, live=False)

    assert "Électricité — circuits" in out
    assert "\ufeff" not in out


def test_markdown_upload_rejects_mixed_or_invalid_utf8(tmp_path: Path):
    source = tmp_path / "corrupt.mmd"
    source.write_bytes("Mostly valid Frédéric ".encode("utf-8") + b"\xff")

    try:
        mmd.to_mmd(source, live=False)
        assert False, "expected ConversionError"
    except mmd.ConversionError as exc:
        assert "not valid UTF-8" in str(exc)
        assert "corrupt.mmd" in str(exc)


def test_text_upload_falls_back_to_legacy_cp1252(tmp_path: Path):
    source = tmp_path / "legacy.txt"
    source.write_bytes("Café costs £5.".encode("cp1252"))

    out = mmd.to_mmd(source, live=False)

    assert "Café costs £5." in out


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


def test_build_assessments_image_convert_failure_streams_error(client, monkeypatch):
    from tests.conftest import stream_error_message
    _force_live_image_failure(monkeypatch)
    files = {"file": ("images.jpg", io.BytesIO(b"\xff\xd8\xff\xe0"), "image/jpeg")}
    # Upload only stages the file (no conversion, so no failure yet).
    job = client.post(
        "/build-assessments/uploads?upload_type=handwritten", files=files).json()
    assert job["status"] == "uploaded"
    # Conversion is the explicit step where Mathpix is hit; the error streams back.
    msg = stream_error_message(
        client.post(f"/build-assessments/uploads/{job['id']}/convert"))
    assert msg and "Mathpix" in msg


def test_build_concepts_image_convert_failure_streams_error(client, monkeypatch):
    from tests.conftest import stream_error_message
    _force_live_image_failure(monkeypatch)
    files = {"file": ("notes.png", io.BytesIO(b"\x89PNG"), "image/png")}
    job = client.post("/build-concepts/post-learning/uploads", files=files).json()
    assert job["status"] == "uploaded"
    msg = stream_error_message(
        client.post(f"/build-concepts/uploads/{job['id']}/convert"))
    assert msg and "Mathpix" in msg

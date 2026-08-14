"""MMD normalization: text decoding + refusal of formats with no converter.

Aegis has no OCR service any more. PDFs reach canonical source through the GPT
PDF-to-ACSD reader, which intercepts ``uploads.convert_job`` and never calls
this module; images have no converter at all. Both must fail as a clean 400
naming the route that still works, not as a 500 or a silent empty document.
"""
import io

from pathlib import Path

from app.services import generation as g, mmd


def test_direct_parser_normalizes_non_lf_latex_headings():
    lines = [
        r"\section*{5.1 Arithmetic Progressions}",
        "",
        "Opening body.",
        "",
        r"\subsection*{EXERCISE 5.1}",
        "",
        "1. Find the next term.",
        "",
    ]

    for newline in ("\r\n", "\r"):
        source = newline.join(lines)
        normalized = g.normalize_mmd_headings(source)
        sections = g.parse_mmd_sections(source)

        assert "\r" not in normalized
        assert [section["heading"] for section in sections] == [
            "Arithmetic Progressions",
            "EXERCISE 5.1",
        ]
        assert "Opening body." in sections[0]["body"]
        assert "Find the next term." in sections[1]["body"]


def test_text_upload_decodes_utf8_independently_of_windows_locale(
    tmp_path: Path,
):
    source = tmp_path / "nationalism.mmd"
    source.write_bytes(
        "Frédéric Sorrieu’s vision — ₹500; \\(a_n = a + (n-1)d\\).".encode(
            "utf-8"
        )
    )

    out = mmd.to_mmd(source)

    assert "Frédéric Sorrieu’s vision — ₹500" in out
    assert "FrÃ" not in out
    assert "â€" not in out


def test_markdown_upload_accepts_utf8_bom(tmp_path: Path):
    source = tmp_path / "chapter.md"
    source.write_bytes(
        b"\xef\xbb\xbf" + "Électricité — circuits".encode("utf-8")
    )

    out = mmd.to_mmd(source)

    assert "Électricité — circuits" in out
    assert "﻿" not in out


def test_markdown_upload_rejects_mixed_or_invalid_utf8(tmp_path: Path):
    source = tmp_path / "corrupt.mmd"
    source.write_bytes("Mostly valid Frédéric ".encode("utf-8") + b"\xff")

    try:
        mmd.to_mmd(source)
        assert False, "expected ConversionError"
    except mmd.ConversionError as exc:
        assert "not valid UTF-8" in str(exc)
        assert "corrupt.mmd" in str(exc)


def test_text_upload_falls_back_to_legacy_cp1252(tmp_path: Path):
    source = tmp_path / "legacy.txt"
    source.write_bytes("Café costs £5.".encode("cp1252"))

    out = mmd.to_mmd(source)

    assert "Café costs £5." in out


def test_pdf_here_names_the_reader_instead_of_stubbing_the_chapter(
    tmp_path: Path,
):
    """A PDF this module sees belongs to a lane the reader does not serve.

    Returning a placeholder body would look like a successful conversion and
    poison a checkpoint, so it has to be an error that names Build Concepts.
    """
    pdf = tmp_path / "chapter.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")

    try:
        mmd.to_mmd(pdf)
        assert False, "expected ConversionError"
    except mmd.ConversionError as exc:
        assert isinstance(exc, ValueError)  # so the API returns 400
        assert "chapter.pdf" in str(exc)
        assert "Build Concepts" in str(exc)


def test_image_upload_is_refused_because_there_is_no_ocr(tmp_path: Path):
    img = tmp_path / "scan.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0not-a-real-jpeg")

    try:
        mmd.to_mmd(img)
        assert False, "expected ConversionError"
    except mmd.ConversionError as exc:
        assert isinstance(exc, ValueError)
        assert "scan.jpg" in str(exc)
        assert "OCR" in str(exc)


def test_build_assessments_image_convert_failure_streams_error(client):
    from tests.conftest import stream_error_message
    files = {"file": ("images.jpg", io.BytesIO(b"\xff\xd8\xff\xe0"), "image/jpeg")}
    # Upload only stages the file (no conversion, so no failure yet).
    job = client.post(
        "/build-assessments/uploads?upload_type=handwritten", files=files).json()
    assert job["status"] == "uploaded"
    # Conversion is the explicit step that has no converter; it streams back.
    msg = stream_error_message(
        client.post(f"/build-assessments/uploads/{job['id']}/convert"))
    assert msg and "OCR" in msg


def test_build_concepts_image_convert_failure_streams_error(client):
    from tests.conftest import stream_error_message
    files = {"file": ("notes.png", io.BytesIO(b"\x89PNG"), "image/png")}
    job = client.post("/build-concepts/post-learning/uploads", files=files).json()
    assert job["status"] == "uploaded"
    msg = stream_error_message(
        client.post(f"/build-concepts/uploads/{job['id']}/convert"))
    assert msg and "OCR" in msg

"""A reviewed PDF is rendered at the house form, and stored as rendered.

Two defects lived in one line of ``read_document``'s PDF branch.

The render was ``page.get_pixmap().tobytes("png")`` — a 1x page image. That
is not the cheap choice it looks like: at ``detail: "high"`` the model scales
the picture to a shortest side of 768, so a 595x842 1x render is UPSCALED to
768x1087 and the 2x render is DOWNSCALED to the same 768x1087. Identical tile
count, identical token cost — the 1x form simply threw the detail away first
and paid an upscaler to invent it back. Every other page render in this repo
already uses the house form (``canonical_source_phase221_fallback``: 2x
matrix, no alpha, JPEG q88); this lane was the one that did not.

Then ``store_jpeg`` re-encoded whatever it was handed at q95. ``queue`` reads
the whole document — which pins every picture — BEFORE it compares the file
hash to decide whether anything changed, so every re-upload of an unchanged
file decodes and re-encodes the complete page set and then discards the
document it came from. The re-encode inflates a page render by 36-55% and the
inflated copy is what the store keeps forever, under a content hash only this
lane can produce; a 2x render without the pass-through would have made that
bill several times larger. Hence one commit for both halves.


``VERSION`` is deliberately NOT bumped. It is both the ``policy_version`` in
``kernel.decide`` and the ``active()`` marker on a stored document, so a bump
would rekey every xlsx/csv/docx/txt extraction this change never touches.
"""
import base64
import hashlib
import io

import fitz
import pytest
from PIL import Image

from app import config
from app.services import reviewed_file_input as reviewed, source_asset_store
from tests.test_independent_reviewed_files import setup_job


@pytest.fixture
def assets(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AEGIS_PUBLIC_BASE_URL", "https://aegis.example")
    return tmp_path


def reviewed_pdf(path):
    """Two pages of reviewer text — the shape a scanned Concept file arrives in."""
    document = fitz.open()
    document.new_page().insert_text((50, 60), "Use this frequency table.")
    document.new_page().insert_text((50, 60), "Prove the triangles are similar.")
    document.save(path)
    document.close()
    return path


def house_render(path):
    """The house page render: app/services/canonical_source_phase221_fallback.py:314.

    The same matrix, alpha and quality that
    tests/test_full_table_source_assets.py:83 pins — that line adds a
    ``clip`` because it crops one table out of a page, so it is the same
    form, not the identical expression.

    Written out here rather than imported so the two lanes cannot drift into
    agreeing with each other while both drifting away from the house form.
    """
    with fitz.open(path) as document:
        return [page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                .tobytes("jpeg", jpg_quality=88) for page in document]


def test_a_reviewed_pdf_page_is_rendered_at_two_times_as_jpeg(tmp_path):
    """The picture the author actually reads must carry 2x detail.

    Decoded dimensions, not byte length: a JPEG that merely got bigger would
    satisfy a size assertion while still being a 1x render.
    """
    path = reviewed_pdf(tmp_path / "review.pdf")
    with fitz.open(path) as document:
        rect = document[0].rect
    assert (rect.width, rect.height) == (595.0, 842.0)

    parsed = reviewed.read_document(path, path.name)

    assert [image["url"][:24] for image in parsed["images"]] == [
        "data:image/jpeg;base64,/"] * 2
    for image in parsed["images"]:
        stored = base64.b64decode(image["url"].split(",", 1)[1])
        with Image.open(io.BytesIO(stored)) as rendered:
            assert rendered.format == "JPEG"
            assert rendered.size == (1190, 1684)  # 2x the 595x842 page.


def test_the_page_image_is_byte_for_byte_the_house_render(tmp_path):
    """One expression owns every page render in this repo.

    An equivalent-looking render (a different matrix, alpha kept, another
    quality) would still read correctly and would still cost a different
    number of bytes to store forever.
    """
    path = reviewed_pdf(tmp_path / "review.pdf")

    parsed = reviewed.read_document(path, path.name)

    expected = house_render(path)
    assert [image["url"].split(",", 1)[1] for image in parsed["images"]] == [
        base64.b64encode(page).decode() for page in expected]


def test_the_stored_asset_is_the_render_itself_and_not_a_re_encode(assets):
    """What is pinned must be the bytes that were rendered.

    This is the half that keeps the store honest: the durable file's own name
    is the hash of the render, so a re-read of the same PDF addresses the file
    that is already on the volume instead of minting a second copy of the same
    page at a different quality.
    """
    path = reviewed_pdf(assets / "review.pdf")

    parsed = reviewed.read_document(path, path.name, job_id=57)

    for image, page_bytes in zip(parsed["images"], house_render(path)):
        assert image["sha256"] == hashlib.sha256(page_bytes).hexdigest()
        stored = source_asset_store.stored_asset_path(f"{image['sha256']}.jpg")
        assert stored.read_bytes() == page_bytes
        assert image["url"] == image["asset_url"]
        assert image["url"].startswith(
            f"https://aegis.example/source-assets/57/{image['sha256']}.jpg?sig=")


def test_an_identical_re_upload_neither_rekeys_nor_stores_a_second_form(db, assets):
    """``queue`` pins before it compares, so a re-upload pays the reader again.

    ``read_document`` runs — and therefore every page is re-rendered and
    re-pinned — before ``changed`` is computed from the file hash, and the
    second document is then discarded. Two properties have to hold across
    that: the second read must address exactly the files the first one wrote
    (identity is the content hash, and a moved identity strands the links the
    stored document already carries), and what sits on the volume must be the
    render itself, not a q95 copy of it that only this lane can produce.
    """
    job = setup_job(db)
    path = reviewed_pdf(assets / "review.pdf")
    pages = house_render(path)  # In page order; the store sorts by hash.
    expected = {hashlib.sha256(page).hexdigest() + ".jpg": page for page in pages}

    first = reviewed.queue(db, job, lane="post", path=path,
                           filename=path.name, owner_sub="local:default")
    stored = sorted(p.name for p in source_asset_store.store_root().glob("*.jpg"))
    second = reviewed.queue(db, job, lane="post", path=path,
                            filename=path.name, owner_sub="local:default")

    assert first["round_recorded"] and not second["round_recorded"]
    assert stored == sorted(expected), "the store holds the renders themselves"
    assert sorted(p.name for p in source_asset_store.store_root().glob("*.jpg")) == stored
    for name, page in expected.items():
        assert source_asset_store.stored_asset_path(name).read_bytes() == page
    assert [image["sha256"] for image
            in job.question_inventory[reviewed.INPUTS]["post"]["images"]] == [
        hashlib.sha256(page).hexdigest() for page in pages]


def test_the_pdf_blocks_are_unchanged_by_the_render(tmp_path):
    """The render owns pictures only; the text evidence is a frozen golden.

    Block refs, page numbers, ordering and the page text itself are what the
    extraction quotes from and what ``_checker`` places a quote against. A
    render change that moved any of them would be a silent contract change.
    """
    path = reviewed_pdf(tmp_path / "review.pdf")

    parsed = reviewed.read_document(path, path.name)

    assert parsed["blocks"] == [
        {"ref": "B1", "text": "Use this frequency table.\n",
         "page": 1, "image_refs": ["I1"]},
        {"ref": "B2", "text": "Prove the triangles are similar.\n",
         "page": 2, "image_refs": ["I2"]},
    ]
    assert [{k: v for k, v in image.items() if k != "url"}
            for image in parsed["images"]] == [
        {"ref": "I1", "page": 1}, {"ref": "I2", "page": 2}]


def test_the_reviewed_file_version_is_not_bumped_for_a_render_change():
    """A render is not a policy: bumping ``VERSION`` would rekey four lanes.

    ``VERSION`` is the ``policy_version`` ``kernel.decide`` hashes into the
    decision key AND the ``active()`` marker on an already stored document.
    Moving it for a change that only touches the PDF branch would re-ask
    every xlsx, csv, docx and txt extraction that is already paid for.
    """
    assert reviewed.VERSION == "independent-reviewed-file-2026-09-11-v1"


def test_store_jpeg_passes_an_alpha_free_jpeg_through_byte_for_byte():
    """Re-encoding an already-lossy JPEG buys nothing and costs twice.

    It adds a generation of loss, inflates the bytes, and — because the store
    is content addressed — changes the identity of a picture that has not
    changed. MuPDF's own encoder emits a progressive JPEG, so this must not
    be narrowed to baseline scans: that would exclude exactly the page
    renders the pass-through exists for.
    """
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((50, 60), "Use this frequency table.")
        rendered = page.get_pixmap(matrix=fitz.Matrix(2, 2),
                                   alpha=False).tobytes("jpeg", jpg_quality=88)
    with Image.open(io.BytesIO(rendered)) as probe:
        assert probe.info.get("progressive"), "fixture must carry MuPDF's scan form"

    assert reviewed.store_jpeg(rendered) is rendered


def test_store_jpeg_passes_a_greyscale_scan_through_byte_for_byte():
    """A greyscale JPEG has no alpha either, and is the usual scanned page.

    Re-encoding one is the worst case this function has: the single channel
    becomes three and is written at q95, so the durable copy is several times
    the size of the picture it replaced — for a form the vision API already
    reads. ``L`` therefore passes through with ``RGB``.
    """
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((50, 60), "Use this frequency table.")
        grey = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False,
                               colorspace=fitz.csGRAY).tobytes("jpeg", jpg_quality=88)
    with Image.open(io.BytesIO(grey)) as probe:
        assert probe.mode == "L", "fixture must be a single-channel scan"

    assert reviewed.store_jpeg(grey) is grey


def test_store_jpeg_still_converts_a_jpeg_the_browser_would_mis_render():
    """The gate is alpha-free GREY or RGB, not every JPEG.

    A CMYK JPEG is a JPEG with no alpha channel, and it is exactly the form
    that must not be passed through: it is read as inverted colour by much of
    what displays a stored asset. Widening the gate to greyscale must not
    widen it to the whole container format.
    """
    buffer = io.BytesIO()
    Image.new("CMYK", (40, 24), (0, 255, 255, 0)).save(buffer, format="JPEG")

    data = reviewed.store_jpeg(buffer.getvalue())

    assert data != buffer.getvalue()
    with Image.open(io.BytesIO(data)) as converted:
        assert converted.format == "JPEG"
        assert converted.mode == "RGB"


def test_store_jpeg_still_composites_transparency_onto_white():
    """The Q59 branch stands: a pasted line drawing must not become a black box.

    A fully transparent PNG has no JPEG form of its own, so it is converted —
    onto the white Excel and Word show behind a pasted picture, never the
    black a bare ``RGB`` conversion gives.
    """
    buffer = io.BytesIO()
    Image.new("RGBA", (40, 24), (0, 0, 0, 0)).save(buffer, format="PNG")

    data = reviewed.store_jpeg(buffer.getvalue())

    with Image.open(io.BytesIO(data)) as flattened:
        assert flattened.format == "JPEG"
        assert flattened.convert("RGB").getpixel((0, 0)) == (255, 255, 255)


def test_store_jpeg_normalises_a_format_the_vision_api_refuses():
    """Pass-through is for JPEGs alone; a pasted BMP is still converted.

    Q59's second reason for this function was that a reviewer's pasted BMP or
    TIFF would otherwise reach the vision API in a format it declines.
    """
    buffer = io.BytesIO()
    Image.new("RGB", (40, 24), (200, 30, 30)).save(buffer, format="BMP")

    data = reviewed.store_jpeg(buffer.getvalue())

    assert data != buffer.getvalue()
    with Image.open(io.BytesIO(data)) as converted:
        assert converted.format == "JPEG"

"""Physical PDF evidence: pixels, provenance, resumability and private storage."""
from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
import io
from pathlib import Path
import socket
import urllib.request

import fitz
from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
from PIL import Image, ImageChops, ImageStat
import pytest

from app import config
from app.api.source_assets import router as source_assets_router
from app.services import pdf_source_evidence as evidence
from app.services import run_control, source_asset_store
from app.services.storage_capacity import StorageCapacityError


@pytest.fixture(autouse=True)
def private_offline_store(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "data" / "uploads")
    monkeypatch.setenv("AEGIS_PUBLIC_BASE_URL", "https://aegis.example.org")

    def no_network(*args, **kwargs):
        pytest.fail("Saving local PDF evidence must not contact a provider or URL")

    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(urllib.request, "urlopen", no_network)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", no_network)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", no_network)


@pytest.fixture()
def original_pdf(tmp_path):
    """A rotated crop, native vectors/text, a scanned page and a final page."""
    path = tmp_path / "uploaded-original.pdf"
    with fitz.open() as document:
        page = document.new_page(width=400, height=300)
        # Relative to the eventual CropBox this square is x=.1..3, y=.1..3.
        # A clockwise quarter-turn displays it at x=.7..9, y=.1..3.
        page.draw_rect(fitz.Rect(72, 54, 136, 102), color=None, fill=(1, 0, 0))
        page.draw_line(fitz.Point(80, 65), fitz.Point(125, 88), color=(0, 0, 0), width=2)
        page.draw_rect(fitz.Rect(250, 65, 300, 100), color=None, fill=(0, 0, 1))
        page.insert_text((80, 145), "Vector source alpha", fontsize=12)
        page.insert_text((80, 170), "Second source line", fontsize=10, fontname="cour")
        page.insert_text((80, 195), "42", fontsize=12)
        page.insert_text((103, 195), "units", fontsize=12, fontname="tiro")
        page.set_cropbox(fitz.Rect(40, 30, 360, 270))
        page.set_rotation(90)

        raster = Image.new("RGB", (240, 180), "white")
        raster.paste("navy", (30, 40, 210, 140))
        raster.paste("yellow", (70, 60, 170, 120))
        stream = io.BytesIO()
        raster.save(stream, format="PNG")
        page = document.new_page(width=240, height=180)
        page.insert_image(page.rect, stream=stream.getvalue())

        page = document.new_page(width=240, height=180)
        page.insert_text((25, 45), "Final page omega", fontsize=12)
        document.save(path)
    return path


def test_original_and_every_native_span_are_retained_without_publishing(original_pdf, tmp_path):
    original = original_pdf.read_bytes()
    manifest = evidence.prepare_document(original_pdf, tmp_path / "artifacts")
    expected_hash = hashlib.sha256(original).hexdigest()
    assert manifest["pdf_sha256"] == expected_hash
    assert Path(manifest["original_path"]).read_bytes() == original
    assert original_pdf.read_bytes() == original
    assert manifest["page_count"] == 3
    assert [page["page_number"] for page in manifest["pages"]] == [1, 2, 3]
    assert not source_asset_store.store_root().exists()

    first = evidence.page_input(manifest, 1)
    assert [span["text"] for span in first["spans"]] == [
        "Vector source alpha", "Second source line", "42", "units",
    ]
    assert first["rotation"] == 90
    assert first["crop_box"] == [40, 30, 360, 270]
    assert (first["width"], first["height"]) == (240, 320)
    with Image.open(first["image_path"]) as image:
        assert image.size == (600, 800)
        for span in first["spans"]:
            x0, y0, x1, y1 = span["display_bbox"]
            assert 0 <= x0 < x1 <= 240 and 0 <= y0 < y1 <= 320
            # Native span bounds must actually cover ink in the retained raster.
            region = image.crop((int(x0 * 2.5), int(y0 * 2.5),
                                 int(x1 * 2.5) + 1, int(y1 * 2.5) + 1))
            assert region.convert("L").getextrema()[0] < 80
    encoded = first["image_data_url"].split(",", 1)[1]
    assert base64.b64decode(encoded) == Path(first["image_path"]).read_bytes()

    scanned = evidence.page_input(manifest, 2)
    assert scanned["spans"] == []
    assert scanned["text"] == ""
    with Image.open(scanned["image_path"]) as image:
        red, green, blue = image.getpixel((image.width // 2, image.height // 2))
        assert red > 230 and green > 230 and blue < 25
    assert evidence.page_input(manifest, 3)["text"] == "Final page omega"


def test_changed_upload_keeps_the_old_immutable_source_bundle(original_pdf, tmp_path):
    before_bytes = original_pdf.read_bytes()
    before = evidence.prepare_document(original_pdf, tmp_path / "artifacts")
    with fitz.open() as replacement:
        replacement.new_page().insert_text((30, 40), "A different uploaded source")
        original_pdf.write_bytes(replacement.tobytes())
    after = evidence.prepare_document(original_pdf, tmp_path / "artifacts")
    assert before["root"] != after["root"]
    assert before["pdf_sha256"] != after["pdf_sha256"]
    assert Path(before["original_path"]).read_bytes() == before_bytes
    assert evidence.page_input(before, 3)["text"] == "Final page omega"


def test_paused_render_resumes_completed_pages_without_rendering_them_again(original_pdf, tmp_path, monkeypatch):
    checks = 0

    def pause_before_second_page():
        nonlocal checks
        checks += 1
        if checks == 2:
            raise run_control.RunDeferred("Deploying", reason="deployment")

    monkeypatch.setattr(run_control, "check", pause_before_second_page)
    with pytest.raises(run_control.RunDeferred):
        evidence.prepare_document(original_pdf, tmp_path / "artifacts")
    first = next((tmp_path / "artifacts").rglob("page-0001.jpg"))
    saved_stat, saved_bytes = first.stat(), first.read_bytes()
    assert not (tmp_path / "artifacts" / "source.pdf-evidence.json").exists()

    rendered = []
    render = fitz.Page.get_pixmap

    def record_render(page, *args, **kwargs):
        rendered.append(page.number + 1)
        return render(page, *args, **kwargs)

    monkeypatch.setattr(run_control, "check", lambda: None)
    monkeypatch.setattr(fitz.Page, "get_pixmap", record_render)
    manifest = evidence.prepare_document(original_pdf, tmp_path / "artifacts")
    assert manifest["page_count"] == 3
    assert rendered == [2, 3]
    assert first.read_bytes() == saved_bytes
    assert first.stat().st_mtime_ns == saved_stat.st_mtime_ns


@pytest.mark.parametrize("damaged_field", ["image_path", "native_text_path"])
def test_corrupted_page_is_refused_and_only_that_page_is_rebuilt(original_pdf, tmp_path, monkeypatch, damaged_field):
    manifest = evidence.prepare_document(original_pdf, tmp_path / "artifacts")
    intact = {page["page_number"]: Path(page["image_path"]).stat().st_mtime_ns
              for page in manifest["pages"] if page["page_number"] != 2}
    Path(manifest["pages"][1][damaged_field]).write_bytes(b"corrupted evidence")
    with pytest.raises(evidence.PdfEvidenceError) as error:
        evidence.page_input(manifest, 2)
    assert error.value.code == "evidence_hash_mismatch"
    rendered = []
    render = fitz.Page.get_pixmap

    def record_render(page, *args, **kwargs):
        rendered.append(page.number + 1)
        return render(page, *args, **kwargs)

    monkeypatch.setattr(fitz.Page, "get_pixmap", record_render)
    restored = evidence.prepare_document(original_pdf, tmp_path / "artifacts")
    assert rendered == [2]
    assert evidence.page_input(restored, 2)["spans"] == []
    for page in restored["pages"]:
        if page["page_number"] in intact:
            assert Path(page["image_path"]).stat().st_mtime_ns == intact[page["page_number"]]


def test_declared_crop_uses_displayed_pixels_and_only_crop_becomes_public(original_pdf, tmp_path):
    manifest = evidence.prepare_document(original_pdf, tmp_path / "artifacts")
    page = manifest["pages"][0]
    app = FastAPI()
    app.include_router(source_assets_router)
    client = TestClient(app)
    assert client.get(f"/source-assets/77/{page['image_sha256']}.jpg").status_code == 404
    crop = evidence.crop_region(manifest, 1, [0.6, 0.05, 0.95, 0.4], job_id=77)
    response = client.get(crop["url"])
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert hashlib.sha256(response.content).hexdigest() == crop["sha256"]
    assert crop["page_raster_sha256"] == page["image_sha256"]
    assert crop["pdf_sha256"] == manifest["pdf_sha256"]
    with Image.open(io.BytesIO(response.content)) as published:
        # Hand-calculated from the physical rotated 240x320pt page at180dpi.
        assert published.size == (210, 280)
        assert (crop["width"], crop["height"]) == (210, 280)
        red, green, blue = published.getpixel((120, 110))
        assert red > 230 and green < 25 and blue < 25
        assert min(published.getpixel((10, 10))) > 240
        with Image.open(page["image_path"]) as retained:
            expected = retained.crop((360, 40, 570, 320))
            # Only JPEG re-encoding loss is allowed; no shifted/rotated/re-rendered crop.
            difference = ImageStat.Stat(ImageChops.difference(published, expected))
            assert max(difference.mean) < 4
    assert client.get(f"/source-assets/77/{page['image_sha256']}.jpg").status_code == 404
    assert len(list(source_asset_store.store_root().glob("*.jpg"))) == 1


def test_private_region_reuses_verified_bytes_and_repairs_corruption_without_public_pin(original_pdf, tmp_path, monkeypatch):
    manifest = evidence.prepare_document(original_pdf, tmp_path / "artifacts")
    retained_page = Path(manifest["pages"][0]["image_path"])
    page_stat = retained_page.stat()
    monkeypatch.setenv("AEGIS_PUBLIC_BASE_URL", "")

    def refuse_public_pin(*args, **kwargs):
        pytest.fail("An auditor's private crop must never enter the public asset store")

    monkeypatch.setattr(source_asset_store, "pin_asset", refuse_public_pin)
    region = evidence.region_input(manifest, 1, [0.6, 0.05, 0.95, 0.4])
    private_path = Path(region["image_path"])
    original_crop, original_stat = private_path.read_bytes(), private_path.stat()
    assert private_path.is_relative_to(Path(manifest["root"]))
    assert region["source_page_id"] == "p0001"
    assert region["source_evidence_sha256"] == manifest["pages"][0]["evidence_sha256"]
    assert hashlib.sha256(original_crop).hexdigest() == region["image_sha256"]
    assert base64.b64decode(region["image_data_url"].split(",", 1)[1]) == original_crop
    assert "url" not in region and "asset_url" not in region
    with Image.open(io.BytesIO(original_crop)) as crop:
        assert crop.size == (210, 280)
        red, green, blue = crop.getpixel((120, 110))
        assert red > 230 and green < 25 and blue < 25

    writes = []
    write = evidence.atomic_bytes

    def record_write(path, data):
        writes.append(Path(path))
        return write(path, data)

    monkeypatch.setattr(evidence, "atomic_bytes", record_write)
    with monkeypatch.context() as changed_encoder:
        def refuse_reencoding(*args, **kwargs):
            pytest.fail("A cached paid audit crop must keep its exact bytes across encoder upgrades")

        changed_encoder.setattr(evidence, "_crop_bytes", refuse_reencoding)
        assert evidence.region_input(manifest, 1, [0.6, 0.05, 0.95, 0.4]) == region
    assert writes == []
    assert private_path.stat().st_mtime_ns == original_stat.st_mtime_ns

    private_path.write_bytes(b"corrupted private audit crop")
    repaired = evidence.region_input(manifest, 1, [0.6, 0.05, 0.95, 0.4])
    assert repaired == region
    assert private_path.read_bytes() == original_crop
    assert writes == [private_path]
    assert retained_page.stat().st_mtime_ns == page_stat.st_mtime_ns
    assert not source_asset_store.store_root().exists()


def test_corrupted_private_region_cannot_change_paid_bytes_after_encoder_upgrade(original_pdf, tmp_path, monkeypatch):
    manifest = evidence.prepare_document(original_pdf, tmp_path / "artifacts")
    region = evidence.region_input(manifest, 1, [0.6, 0.05, 0.95, 0.4])
    private_path = Path(region["image_path"])
    metadata_path = private_path.with_suffix(".json")
    sealed_metadata = metadata_path.read_bytes()
    original_bytes = private_path.read_bytes()
    private_path.write_bytes(b"damaged stored crop")
    repaired_bytes = original_bytes + b"\nnew encoder trailer"

    def upgraded_encoder(*args, **kwargs):
        return manifest["pages"][0], repaired_bytes, region["width"], region["height"]

    monkeypatch.setattr(evidence, "_crop_bytes", upgraded_encoder)
    with pytest.raises(evidence.PdfEvidenceError) as error:
        evidence.region_input(manifest, 1, [0.6, 0.05, 0.95, 0.4])
    assert error.value.code == "region_bytes_changed"
    assert error.value.page_number == 1
    assert metadata_path.read_bytes() == sealed_metadata
    assert private_path.read_bytes() == b"damaged stored crop"
    assert hashlib.sha256(repaired_bytes).hexdigest() != region["image_sha256"]
    assert not source_asset_store.store_root().exists()


@pytest.mark.parametrize("bbox", [[0, 0, 1.01, 1], [0.8, 0, 0.2, 1],
                                  [False, 0, 1, 1], [0, 0, float("nan"), 1]])
def test_invalid_crop_coordinates_publish_nothing(original_pdf, tmp_path, bbox):
    manifest = evidence.prepare_document(original_pdf, tmp_path / "artifacts")
    with pytest.raises(evidence.PdfEvidenceError) as error:
        evidence.crop_region(manifest, 1, bbox, job_id=77)
    assert error.value.code == "invalid_region"
    assert not source_asset_store.store_root().exists()


@pytest.mark.parametrize("kind, expected_code", [("missing", "source_missing"),
                                                 ("truncated", "pdf_unreadable"),
                                                 ("encrypted", "pdf_encrypted")])
def test_unusable_originals_have_named_errors_and_no_complete_manifest(tmp_path, kind, expected_code):
    path = tmp_path / f"{kind}.pdf"
    if kind == "truncated":
        path.write_bytes(b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog ")
    elif kind == "encrypted":
        with fitz.open() as doc:
            doc.new_page().insert_text((30, 40), "Locked source")
            doc.save(path, encryption=fitz.PDF_ENCRYPT_AES_256,
                     owner_pw="owner-secret", user_pw="reader-secret")
    with pytest.raises(evidence.PdfEvidenceError) as error:
        evidence.prepare_document(path, tmp_path / "artifacts")
    assert error.value.code == expected_code
    assert not (tmp_path / "artifacts" / "source.pdf-evidence.json").exists()
    assert not list((tmp_path / "artifacts").rglob("manifest.json"))


def test_capacity_failure_during_atomic_replacement_preserves_old_bytes_and_removes_partial(tmp_path, monkeypatch):
    destination = tmp_path / "accepted-evidence.json"
    destination.write_bytes(b"previous complete evidence")
    writes = 0

    @contextmanager
    def fail_second_chunk(payload_bytes, **kwargs):
        nonlocal writes
        if payload_bytes:
            writes += 1
            if writes == 2:
                raise StorageCapacityError("Test filesystem exhausted", phase="pdf_evidence")
        yield

    monkeypatch.setattr(evidence, "reserve_evidence_write", fail_second_chunk)
    with pytest.raises(StorageCapacityError):
        evidence.atomic_bytes(destination, b"new" * 900_000)
    assert destination.read_bytes() == b"previous complete evidence"
    assert not list(tmp_path.glob(".pdf-evidence-*"))


def test_page_storage_failure_keeps_its_storage_code_and_completed_pages(original_pdf, tmp_path, monkeypatch):
    save_bytes = evidence.atomic_bytes

    def fail_second_page(path, content):
        if Path(path).name == "page-0002.jpg":
            raise StorageCapacityError("Test disk full", phase="pdf_evidence")
        return save_bytes(path, content)

    monkeypatch.setattr(evidence, "atomic_bytes", fail_second_page)
    with pytest.raises(StorageCapacityError) as error:
        evidence.prepare_document(original_pdf, tmp_path / "artifacts")
    assert error.value.code == "insufficient_storage"
    assert len(list((tmp_path / "artifacts").rglob("page-0001.json"))) == 1
    assert not list((tmp_path / "artifacts").rglob("page-0002.json"))
    assert not list((tmp_path / "artifacts").rglob(".pdf-evidence-*"))
    assert not (tmp_path / "artifacts" / "source.pdf-evidence.json").exists()

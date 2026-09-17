"""Immutable PDF evidence, with one coordinate frame for text and page crops.

This module does no content classification. The PDF renderer supplies pixels
and native spans; the reader supplies every semantic region and relationship.
Full pages remain private. Only explicitly requested source crops are published.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable

import fitz

from . import run_control, source_asset_publication, source_asset_store
from .storage_capacity import StorageCapacityError
from .storage_capacity import reserve_review_evidence_write as reserve_evidence_write

VERSION = "pdf-page-evidence-1"
RENDER_VERSION = "cropbox-display-rgb-180dpi-jpeg95-1"
DPI = 180
MAX_PAGE_PIXELS = 16_000_000
_CHUNK = 1024 * 1024


class PdfEvidenceError(ValueError):
    """A named physical PDF problem, separate from transcription/rendering."""

    def __init__(self, message: str, *, code: str, page_number: int | None = None):
        super().__init__(message)
        self.code = code
        self.page_number = page_number


def digest_json(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("PDF evidence metadata must be an object")
    return value


def _sync_directory(path: Path) -> None:
    descriptor = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _mkdir(path: Path) -> None:
    path = Path(path)
    missing = 0
    parent = path
    while not parent.exists():
        missing += 1
        parent = parent.parent
    if missing:
        with reserve_evidence_write(0, required_inodes=missing, path=parent):
            path.mkdir(parents=True, exist_ok=True, mode=0o700)


def _atomic_chunks(path: Path, chunks: Iterable[bytes]) -> None:
    path = Path(path)
    _mkdir(path.parent)
    temporary: str | None = None
    try:
        with reserve_evidence_write(0, required_inodes=1, path=path.parent):
            descriptor, temporary = tempfile.mkstemp(prefix=".pdf-evidence-", dir=path.parent)
        with os.fdopen(descriptor, "wb", buffering=0) as output:
            for data in chunks:
                view = memoryview(data)
                while view:
                    part = view[:_CHUNK]
                    with reserve_evidence_write(len(part), path=path.parent):
                        count = output.write(part)
                        if not count:
                            raise OSError("PDF evidence write made no progress")
                    view = view[count:]
            os.fsync(output.fileno())
        with reserve_evidence_write(1, path=path.parent):
            os.replace(temporary, path)
        temporary = None
        _sync_directory(path.parent)
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)


def atomic_bytes(path: Path, data: bytes) -> None:
    _atomic_chunks(path, [data])


def atomic_json(path: Path, value: Any) -> None:
    encoded = json.JSONEncoder(ensure_ascii=False, sort_keys=True, allow_nan=False)
    _atomic_chunks(path, (part.encode("utf-8") for part in encoded.iterencode(value)))


def _file_matches(path: Path, expected: str) -> bool:
    return bool(expected and path.is_file() and not path.is_symlink()
                and file_sha256(path) == expected)


def _private_path(root: Path, value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = root / path
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise PdfEvidenceError("PDF evidence path is outside its source bundle", code="invalid_evidence_path")
    return path


def _native_page(page: Any) -> dict[str, Any]:
    # Text boxes are expressed in the PDF's unrotated crop-relative frame.
    # Keep that original frame and the equivalent displayed-page frame.
    spans: list[dict[str, Any]] = []
    text_lines: list[str] = []
    flags = fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES
    for block in page.get_text("dict", flags=flags, sort=False).get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            line_text: list[str] = []
            for span in line.get("spans", []):
                text = str(span.get("text") or "")
                line_text.append(text)
                bbox = fitz.Rect(span["bbox"])
                displayed = bbox * page.rotation_matrix
                spans.append({
                    "text": text, "bbox": list(bbox), "display_bbox": list(displayed),
                    "font": str(span.get("font") or ""), "font_size": span.get("size"),
                    "flags": span.get("flags"), "direction": list(line.get("dir") or [1, 0]),
                })
            text_lines.append("".join(line_text))
    return {"text": "\n".join(text_lines), "spans": spans,
            "coordinate_frame": "unrotated_crop_relative_pdf_points",
            "display_coordinate_frame": "rotated_crop_relative_pdf_points"}


def _page_is_current(root: Path, meta: dict[str, Any], *, pdf_sha256: str, number: int) -> bool:
    try:
        return bool(meta.get("pdf_sha256") == pdf_sha256
                    and meta.get("page_number") == number
                    and meta.get("render_version") == RENDER_VERSION
                    and _file_matches(_private_path(root, meta["image_path"]), meta["image_sha256"])
                    and _file_matches(_private_path(root, meta["native_text_path"]), meta["native_text_sha256"]))
    except (KeyError, OSError, ValueError, TypeError):
        return False


def prepare_document(path: Path, artifact_dir: Path) -> dict[str, Any]:
    """Render original pages once; retain each completed page across suspension."""
    path, artifact_dir = Path(path), Path(artifact_dir)
    if not path.is_file():
        raise PdfEvidenceError("The uploaded PDF bytes are unavailable", code="source_missing")
    pdf_sha256 = file_sha256(path)
    root = artifact_dir / "pdf-evidence-v1" / pdf_sha256
    _mkdir(root)
    original = root / "source.pdf"
    if not _file_matches(original, pdf_sha256):
        with path.open("rb") as source:
            _atomic_chunks(original, iter(lambda: source.read(_CHUNK), b""))
        if file_sha256(original) != pdf_sha256:
            raise PdfEvidenceError("The PDF changed while evidence was being saved", code="source_changed")
    try:
        document = fitz.open(original)
    except Exception as exc:
        raise PdfEvidenceError("The PDF container could not be opened", code="pdf_unreadable") from exc
    with document:
        if not document.is_pdf:
            raise PdfEvidenceError("The uploaded file is not a PDF document", code="not_pdf")
        if document.needs_pass:
            raise PdfEvidenceError("The PDF needs a password before it can be read", code="pdf_encrypted")
        if document.page_count == 0:
            raise PdfEvidenceError("The PDF has no pages", code="pdf_empty")
        pages: list[dict[str, Any]] = []
        for index in range(document.page_count):
            run_control.check()
            number = index + 1
            metadata_path = root / f"page-{number:04d}.json"
            try:
                previous = read_json(metadata_path)
            except (OSError, ValueError):
                previous = {}
            if _page_is_current(root, previous, pdf_sha256=pdf_sha256, number=number):
                pages.append(previous)
                continue
            try:
                page = document.load_page(index)
                rect = page.rect
                if not all(math.isfinite(value) and value > 0 for value in (rect.width, rect.height)):
                    raise ValueError("non-finite or empty page geometry")
                scale = min(DPI / 72, math.sqrt(MAX_PAGE_PIXELS / (rect.width * rect.height)))
                pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), colorspace=fitz.csRGB, alpha=False)
                image_bytes = pixmap.tobytes("jpeg", jpg_quality=95)
                image_path = root / f"page-{number:04d}.jpg"
                atomic_bytes(image_path, image_bytes)
                native_path = root / f"native-{number:04d}.json"
                atomic_json(native_path, _native_page(page))
                image_hash = hashlib.sha256(image_bytes).hexdigest()
                native_hash = file_sha256(native_path)
                meta = {
                    "page_id": f"p{number:04d}", "page_number": number,
                    "label": page.get_label() or "", "page_label": page.get_label() or "",
                    "pdf_sha256": pdf_sha256, "render_version": RENDER_VERSION,
                    "width": rect.width, "height": rect.height, "rotation": page.rotation,
                    "media_box": list(page.mediabox), "crop_box": list(page.cropbox),
                    "crop_box_position": list(page.cropbox_position),
                    "rotation_matrix": list(page.rotation_matrix),
                    "derotation_matrix": list(page.derotation_matrix),
                    "pixel_width": pixmap.width, "pixel_height": pixmap.height,
                    "render_scale": scale,
                    "region_coordinate_frame": "normalized_displayed_page_top_left",
                    "image_path": str(image_path.resolve()), "image_sha256": image_hash,
                    "raster_sha256": image_hash, "native_text_path": str(native_path.resolve()),
                    "native_text_sha256": native_hash,
                    "evidence_sha256": digest_json({"pdf": pdf_sha256, "page": number,
                                                    "raster": image_hash, "native": native_hash}),
                }
                atomic_json(metadata_path, meta)
                pages.append(meta)
            except StorageCapacityError:
                raise
            except (OSError, ValueError, RuntimeError) as exc:
                atomic_json(root / "last-failure.json", {"code": "page_evidence_failed", "page_number": number,
                                                        "completed_pages": [row["page_number"] for row in pages]})
                raise PdfEvidenceError(f"PDF page {number} could not be read; completed pages remain saved",
                                       code="page_evidence_failed", page_number=number) from exc
        manifest = {"version": VERSION, "render_version": RENDER_VERSION,
                    "pdf_sha256": pdf_sha256, "page_count": document.page_count,
                    "root": str(root.resolve()), "original_path": str(original.resolve()),
                    "pdf_repaired_by_renderer": bool(document.is_repaired), "pages": pages}
    atomic_json(root / "manifest.json", manifest)
    atomic_json(artifact_dir / "source.pdf-evidence.json", manifest)
    return manifest


def page_input(manifest: dict[str, Any], page_number: int) -> dict[str, Any]:
    root = Path(manifest["root"])
    matches = [row for row in manifest["pages"] if row["page_number"] == int(page_number)]
    if len(matches) != 1:
        raise PdfEvidenceError("Requested PDF page is missing or duplicated", code="page_missing", page_number=page_number)
    page = matches[0]
    if not _page_is_current(root, page, pdf_sha256=manifest["pdf_sha256"], number=int(page_number)):
        raise PdfEvidenceError("Saved PDF page evidence failed its integrity check", code="evidence_hash_mismatch",
                               page_number=page_number)
    native = read_json(_private_path(root, page["native_text_path"]))
    image_data = _private_path(root, page["image_path"]).read_bytes()
    return {**page, "text": native["text"], "spans": native["spans"], "native_text": native["spans"],
            "mime_type": "image/jpeg", "image_data_url": "data:image/jpeg;base64," + base64.b64encode(image_data).decode()}


def _validate_region(page_number: int, bbox: list[float]) -> None:
    if (not isinstance(bbox, (list, tuple)) or len(bbox) != 4
            or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in bbox)
            or not (0 <= bbox[0] < bbox[2] <= 1 and 0 <= bbox[1] < bbox[3] <= 1)):
        raise PdfEvidenceError("Source region has invalid page coordinates", code="invalid_region", page_number=page_number)


def _crop_bytes(manifest: dict[str, Any], page_number: int, bbox: list[float]) -> tuple[dict, bytes, int, int]:
    _validate_region(page_number, bbox)
    page = page_input(manifest, page_number)
    # Cropping the retained raster makes CropBox offset and rotation exact;
    # no model-supplied file path or URL is ever fetched.
    from PIL import Image
    import io
    with Image.open(page["image_path"]) as image:
        box = (math.floor(bbox[0] * image.width), math.floor(bbox[1] * image.height),
               math.ceil(bbox[2] * image.width), math.ceil(bbox[3] * image.height))
        crop = image.crop(box).convert("RGB")
        output = io.BytesIO()
        crop.save(output, format="JPEG", quality=95)
        data, width, height = output.getvalue(), crop.width, crop.height
    return page, data, width, height


def region_input(manifest: dict[str, Any], page_number: int, bbox: list[float]) -> dict[str, Any]:
    """Private magnified evidence for an audit; no source asset is published."""
    _validate_region(page_number, bbox)
    page = page_input(manifest, page_number)
    crop_identity = digest_json({"page": page["evidence_sha256"], "bbox": list(bbox)})
    path = Path(manifest["root"]) / "regions" / f"{crop_identity}.jpg"
    metadata_path = path.with_suffix(".json")
    metadata = read_json(metadata_path) if metadata_path.is_file() else None
    expected = {"crop_identity": crop_identity, "source_evidence_sha256": page["evidence_sha256"],
                "bbox": list(bbox), "page_number": page_number}
    if metadata is not None and (
        metadata.get("record_sha256") != digest_json({key: value for key, value in metadata.items() if key != "record_sha256"})
        or any(metadata.get(key) != value for key, value in expected.items())
    ):
        raise PdfEvidenceError("Saved PDF region metadata failed its integrity check", code="region_hash_mismatch",
                               page_number=page_number)
    if metadata is not None and _file_matches(path, metadata.get("image_sha256", "")):
        # Reuse the exact previously audited JPEG, including across encoder
        # upgrades. Re-encoding a valid crop could change a paid request hash.
        data, sha256 = path.read_bytes(), metadata["image_sha256"]
        width, height = metadata["width"], metadata["height"]
    else:
        page, data, width, height = _crop_bytes(manifest, page_number, bbox)
        sha256 = hashlib.sha256(data).hexdigest()
        if metadata is not None and sha256 != metadata.get("image_sha256"):
            raise PdfEvidenceError("The saved PDF region cannot be restored byte-for-byte; paid evidence retained",
                                   code="region_bytes_changed", page_number=page_number)
        atomic_bytes(path, data)
        metadata = {**expected, "image_sha256": sha256, "width": width, "height": height}
        metadata["record_sha256"] = digest_json(metadata)
        atomic_json(metadata_path, metadata)
    return {
        "page_id": f"{page['page_id']}-region-{crop_identity[:16]}",
        "page_number": page_number, "bbox": list(bbox), "width": width, "height": height,
        "image_sha256": sha256, "raster_sha256": sha256,
        "image_path": str(path.resolve()), "mime_type": "image/jpeg",
        "image_data_url": "data:image/jpeg;base64," + base64.b64encode(data).decode(),
        "text": "Exact source region from the retained page raster; inspect alongside the whole page.",
        "source_page_id": page["page_id"], "source_evidence_sha256": page["evidence_sha256"],
    }


def crop_region(manifest: dict[str, Any], page_number: int, bbox: list[float], *, job_id: int) -> dict[str, Any]:
    """Publish only a model-declared source region for an output projection."""
    page, data, width, height = _crop_bytes(manifest, page_number, bbox)
    sha256 = hashlib.sha256(data).hexdigest()
    origin = source_asset_publication.configured_origin()
    if not source_asset_publication.valid_public_origin(origin):
        raise PdfEvidenceError("Configure AEGIS_PUBLIC_BASE_URL to serve source crops", code="source_asset_origin_missing")
    url = f"{origin}/source-assets/{int(job_id)}/{sha256}.jpg"
    # pin_asset atomically flushes both the crop and its small sidecar.
    _mkdir(source_asset_store.store_root())
    with reserve_evidence_write(len(data) + 4096, required_inodes=2,
                                path=source_asset_store.store_root()):
        source_asset_store.pin_asset(data, job_id=job_id, asset_url=url, public_base_url=origin)
    return {"url": url, "asset_url": url, "sha256": sha256, "width": width, "height": height,
            "bbox": list(bbox), "page_number": page_number, "page_id": page["page_id"],
            "pdf_sha256": manifest["pdf_sha256"], "page_raster_sha256": page["raster_sha256"]}

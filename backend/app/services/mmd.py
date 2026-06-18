"""Document -> MMD (Mathpix Markdown / KaTeX) conversion.

Every upload path in the integrated tool first normalizes its source document
to MMD so maths is handled consistently. Live conversion uses Mathpix
(``extract_mmds`` in the vendored pipeline); when no Mathpix keys are present
the dry path produces a faithful MMD-shaped stub from the raw text so the rest
of the flow stays exercised.
"""
from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from .. import config

# Upload types accepted by the Build Assessments / Build Concepts upload paths.
UPLOAD_TYPES = ["textbook", "questions", "questions_and_answers", "handwritten", "document"]

# Image extensions Mathpix OCRs via the /v3/text endpoint (NOT /v3/pdf, which
# rejects image content types).
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
_PDF_SUFFIXES = {".pdf"}
_MATHPIX_TEXT_URL = "https://api.mathpix.com/v3/text"


class ConversionError(ValueError):
    """Raised when a document could not be converted to MMD (e.g. Mathpix OCR
    rejected the file). Subclasses ValueError so the API layer turns it into a
    clean 400 instead of an opaque 500."""


def _read_text(path: Path) -> str:
    if path.suffix.lower() in {".txt", ".md", ".mmd"}:
        return path.read_text(errors="ignore")
    if path.suffix.lower() == ".pdf":
        # Dry mode: we don't OCR; emit a placeholder body keyed to the filename.
        return f"(binary PDF: {path.name} — Mathpix OCR required for live extraction)"
    return f"(binary {path.suffix} upload: {path.name})"


def to_mmd(path: Path, *, live: bool | None = None) -> str:
    """Convert an uploaded document to MMD text.

    PDFs and images go through Mathpix in live mode (different endpoints —
    /v3/pdf for PDFs, /v3/text for images). Any conversion failure is surfaced
    as a ConversionError (a ValueError) so the API returns a clean 400 with a
    helpful message rather than a 500.
    """
    use_live = config.use_live_mmd() if live is None else live
    suffix = path.suffix.lower()
    if use_live and suffix in (_PDF_SUFFIXES | _IMAGE_SUFFIXES):
        try:
            if suffix in _IMAGE_SUFFIXES:
                return _mathpix_image_to_mmd(path)
            return _live_pdf_to_mmd(path)
        except ConversionError:
            raise
        except Exception as exc:  # noqa: BLE001 — surface as a clean 400
            raise ConversionError(
                f"Could not convert {path.name!r} to MMD: {exc}"
            ) from exc
    if suffix in (_PDF_SUFFIXES | _IMAGE_SUFFIXES):
        config.require_mmd_live(pdf_or_image=True)
    raw = _read_text(path)
    # Dry MMD stub: wrap the text with a minimal MMD structure.
    body = raw.strip() or "(empty document)"
    return f"# {path.stem}\n\n{body}\n"


def _cache_dir() -> Path:
    """MMD cache shared with the Create Workbooks pipeline (never re-bill)."""
    from . import workbooks
    d = workbooks.CACHE_ROOT / "mmd"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _live_pdf_to_mmd(path: Path) -> str:
    """Mathpix PDF -> MMD via the vendored client (sha-keyed disk cache)."""
    from . import workbooks
    workbooks._vendor()
    from mathpix import MathpixClient  # vendored (create_workbooks/src)

    return MathpixClient(cache_dir=_cache_dir()).convert_to_mmd(path)


# Back-compat alias (older callers / tests referenced _live_to_mmd).
_live_to_mmd = _live_pdf_to_mmd


def _mathpix_image_to_mmd(path: Path) -> str:
    """OCR an image to MMD via Mathpix /v3/text (sha-keyed disk cache).

    The /v3/pdf endpoint the vendored client uses rejects image content types
    ("Invalid content type: image/jpeg"); single images must use /v3/text.
    """
    app_id = os.environ.get("MATHPIX_APP_ID")
    app_key = os.environ.get("MATHPIX_APP_KEY")
    if not (app_id and app_key):
        raise ConversionError(
            "Mathpix credentials missing. Set MATHPIX_APP_ID and MATHPIX_APP_KEY."
        )

    raw = path.read_bytes()
    cache_dir = _cache_dir()
    key = hashlib.sha256(path.name.encode("utf-8") + raw).hexdigest()[:24]
    cache_file = cache_dir / f"{key}.mmd"
    if cache_file.exists():
        return cache_file.read_text(encoding="utf-8")

    options = json.dumps({
        "formats": ["text"],
        "math_inline_delimiters": ["$", "$"],
        "math_display_delimiters": ["$$", "$$"],
        "rm_spaces": True,
    })
    content_type = {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".gif": "image/gif", ".bmp": "image/bmp", ".webp": "image/webp",
    }.get(path.suffix.lower(), "application/octet-stream")
    boundary = "----aegisimg" + os.urandom(8).hex()
    parts = [
        f"--{boundary}\r\n".encode(),
        b'Content-Disposition: form-data; name="options_json"\r\n\r\n',
        options.encode("utf-8") + b"\r\n",
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'.encode(),
        f"Content-Type: {content_type}\r\n\r\n".encode(),
        raw,
        f"\r\n--{boundary}--\r\n".encode(),
    ]
    req = urllib.request.Request(_MATHPIX_TEXT_URL, data=b"".join(parts), method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    req.add_header("app_id", app_id)
    req.add_header("app_key", app_key)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")[:300]
        raise ConversionError(f"Mathpix image OCR failed ({exc.code}): {detail}") from exc

    if payload.get("error"):
        raise ConversionError(f"Mathpix image OCR failed: {payload['error']}")
    text = (payload.get("text") or "").strip()
    mmd = f"# {path.stem}\n\n{text or '(no text detected in image)'}\n"
    cache_file.write_text(mmd, encoding="utf-8")
    return mmd

"""Document -> MMD (Mathpix Markdown / KaTeX) conversion.

Every upload path in the integrated tool first normalizes its source document
to MMD so maths is handled consistently. Live conversion uses Mathpix
(``extract_mmds`` in the vendored pipeline); when no Mathpix keys are present
the dry path produces a faithful MMD-shaped stub from the raw text so the rest
of the flow stays exercised.
"""
from __future__ import annotations

from pathlib import Path

from .. import config

# Upload types accepted by the Build Assessments / Build Concepts upload paths.
UPLOAD_TYPES = ["textbook", "questions", "questions_and_answers", "handwritten", "document"]


def _read_text(path: Path) -> str:
    if path.suffix.lower() in {".txt", ".md", ".mmd"}:
        return path.read_text(errors="ignore")
    if path.suffix.lower() == ".pdf":
        # Dry mode: we don't OCR; emit a placeholder body keyed to the filename.
        return f"(binary PDF: {path.name} — Mathpix OCR required for live extraction)"
    return f"(binary {path.suffix} upload: {path.name})"


def to_mmd(path: Path, *, live: bool | None = None) -> str:
    """Convert an uploaded document to MMD text."""
    use_live = config.use_live_mmd() if live is None else live
    if use_live and path.suffix.lower() in {".pdf", ".png", ".jpg", ".jpeg"}:
        return _live_to_mmd(path)
    raw = _read_text(path)
    # Dry MMD stub: wrap the text with a minimal MMD structure.
    body = raw.strip() or "(empty document)"
    return f"# {path.stem}\n\n{body}\n"


def _live_to_mmd(path: Path) -> str:
    """Mathpix PDF/image -> MMD via the vendored client (sha-keyed disk cache).

    The cache directory is shared with the Create Workbooks pipeline so the
    same source document is never billed twice across modules.
    """
    from . import workbooks
    workbooks._vendor()
    from mathpix import MathpixClient  # vendored (create_workbooks/src)

    cache_dir = workbooks.CACHE_ROOT / "mmd"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return MathpixClient(cache_dir=cache_dir).convert_to_mmd(path)

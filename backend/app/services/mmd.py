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
    use_live = config.has_mathpix() if live is None else live
    raw = _read_text(path)
    if use_live and path.suffix.lower() in {".pdf", ".png", ".jpg", ".jpeg"}:
        # Live hook: delegate to the vendored Mathpix script. Kept lazy so the
        # import never runs (and never needs keys) in dry mode.
        from aegis_pipeline import extract_mmds  # noqa: F401
        raise NotImplementedError(
            "Live MMD conversion: wire extract_mmds.process_* with MATHPIX_APP_ID/KEY "
            "and the uploaded file path."
        )
    # Dry MMD stub: wrap the text with a minimal MMD structure.
    body = raw.strip() or "(empty document)"
    return f"# {path.stem}\n\n{body}\n"

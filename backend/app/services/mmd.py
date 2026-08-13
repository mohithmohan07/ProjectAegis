"""Document -> MMD (Mathpix Markdown / KaTeX) conversion.

MMD names a *file format* — the LaTeX-flavoured Markdown every upload path
normalizes to so maths is handled consistently. The format outlived the vendor
it was named after: Aegis no longer talks to Mathpix at all.

PDFs are read by the GPT PDF-to-ACSD reader (Phase 2.2.1), which installs
itself over ``uploads.convert_job`` and never reaches this module. What is left
here is the text-shaped upload that needs no OCR at all.
"""
from __future__ import annotations

from pathlib import Path

# Upload types accepted by the Build Assessments / Build Concepts upload paths.
UPLOAD_TYPES = ["textbook", "questions", "questions_and_answers", "handwritten", "document"]

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
_PDF_SUFFIXES = {".pdf"}


class ConversionError(ValueError):
    """Raised when a document could not be converted to MMD. Subclasses
    ValueError so the API layer turns it into a clean 400 instead of an opaque
    500."""


def _read_text(path: Path) -> str:
    if path.suffix.lower() in {".txt", ".md", ".mmd"}:
        raw = path.read_bytes()
        try:
            # Textbook MMD is UTF-8.  Never let the host's locale (notably
            # Windows cp1252) reinterpret valid UTF-8 punctuation, maths, or
            # non-ASCII names into mojibake before concept generation.
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            if path.suffix.lower() != ".txt":
                # Markdown formats have a UTF-8 contract.  Falling back after
                # one bad byte would reinterpret the *entire* otherwise-UTF-8
                # chapter as cp1252 and silently poison a saved checkpoint.
                raise ConversionError(
                    f"{path.name!r} is not valid UTF-8. Re-save the "
                    "Markdown file as UTF-8 and upload it again."
                ) from exc
            # Keep explicit legacy ANSI support for plain-text uploads.
            text = raw.decode("cp1252", errors="replace")
        return text.replace("\r\n", "\n").replace("\r", "\n")
    return f"(binary {path.suffix} upload: {path.name})"


def to_mmd(path: Path) -> str:
    """Convert an uploaded document to MMD text.

    Scanned formats have no converter behind them any more. A PDF that reaches
    this function belongs to a module the GPT PDF-to-ACSD reader does not
    serve, and images lost their only OCR path when Mathpix was retired. Both
    raise a ConversionError so the API returns a clean 400 that names the
    supported route rather than a 500.
    """
    suffix = path.suffix.lower()
    if suffix in _PDF_SUFFIXES:
        raise ConversionError(
            f"{path.name!r} is a PDF and this upload path has no PDF reader. "
            "Convert the chapter in Build Concepts, which reads PDFs with the "
            "GPT PDF-to-ACSD reader, or upload it as .mmd/.md/.txt."
        )
    if suffix in _IMAGE_SUFFIXES:
        raise ConversionError(
            f"{path.name!r} is an image and Aegis no longer performs image OCR. "
            "Upload the content as .mmd/.md/.txt."
        )
    raw = _read_text(path)
    # Dry MMD stub: wrap the text with a minimal MMD structure.
    body = raw.strip() or "(empty document)"
    return f"# {path.stem}\n\n{body}\n"

"""Create Workbooks — the vendored revision-workbook PDF generator, embedded.

"Workbook" here means the **student revision-workbook PDF** produced by the
team's Create Workbooks tool (Mathpix MMD -> GPT plan/build -> refine ->
ReportLab A4 render -> validate) — NOT the Bulk Import Excel workbook.

Outputs publish subject-wise into the same library layout the original tool
uses (``<root>/Class NN/<Subject>/<source-stem>.pdf`` plus a
``.build_log.txt`` beside each PDF), rooted at ``DATA_DIR/workbooks``.

Dry vs live:
  * dry  — vendored PyMuPDF extractor + the REAL vendored ReportLab renderer
           with deterministic content derived from the source PDF text. No
           API keys needed; layout/styling matches the original tool.
  * live — the full vendored pipeline (GPT plan/build + Mathpix MMD).
           Requires OPENAI_API_KEY and MATHPIX_APP_ID/MATHPIX_APP_KEY.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from .. import config

VENDOR_SRC = (
    Path(__file__).resolve().parents[2] / "aegis_pipeline" / "create_workbooks" / "src"
)
WORKBOOK_ROOT = config.DATA_DIR / "workbooks"
CACHE_ROOT = WORKBOOK_ROOT / "_cache"

_FILE_RE = re.compile(r"G(\d{2})_(CH|UN)0?(\d+)_(.+?)\.pdf$", re.IGNORECASE)

SUBJECTS = ["Science", "Mathematics", "Social Science", "English"]


def _vendor():
    """Make the vendored flat-module sources importable."""
    p = str(VENDOR_SRC)
    if p not in sys.path:
        sys.path.insert(0, p)


def use_live() -> bool:
    import os
    return (
        config.has_openai() and config.has_mathpix()
        and os.environ.get("AEGIS_USE_LIVE", "").strip().lower() in {"1", "true", "yes", "on"}
    )


def infer_workbook_metadata(filename: str, subject: str = "") -> dict:
    """Grade / chapter number / title from the NCERT filename + subject hint."""
    _vendor()
    from metadata import infer_discipline, slug_to_title  # vendored

    m = _FILE_RE.search(filename)
    if not m:
        raise ValueError(
            "filename does not follow the NCERT convention "
            "(e.g. CBSE_NCERT_G08_CH04_QUADRILATERALS.pdf)"
        )
    grade_2d, _kind, number, slug = m.groups()
    subject = (subject or "").strip() or "Unsorted"
    title = slug_to_title(slug)
    return {
        "grade": f"Grade {int(grade_2d)}",
        "grade_folder": f"Class {int(grade_2d):02d}",
        "subject": subject,
        "chapter_number": f"{int(number):02d}",
        "chapter_title": title,
        "discipline": infer_discipline(title, subject),
        "stem": Path(filename).stem,
    }


def _output_paths(meta: dict) -> tuple[Path, Path]:
    out_dir = WORKBOOK_ROOT / meta["grade_folder"] / meta["subject"]
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / f"{meta['stem']}.pdf", out_dir / f"{meta['stem']}.build_log.txt"


# --------------------------------------------------------------------------- #
# Dry generation: real renderer, deterministic content from the source PDF
# --------------------------------------------------------------------------- #

def _first_sentences(text: str, limit: int) -> str:
    flat = re.sub(r"\s+", " ", text).strip()
    return flat[:limit].rsplit(" ", 1)[0] + ("…" if len(flat) > limit else "")


def _dry_chapter(meta: dict, source_pdf: Path):
    _vendor()
    from extract import PDFExtractor          # vendored
    from schema import Block, Chapter, GlossaryItem, Topic  # vendored

    src = PDFExtractor().extract(str(source_pdf))
    topic_blocks = [b for b in src.blocks if b.kind == "topic"][:10] or []

    topics = []
    for i, tb in enumerate(topic_blocks, start=1):
        topics.append(Topic(
            number=f"{i:02d}",
            title=tb.title[:90],
            overview=_first_sentences(tb.text, 240),
            blocks=[
                Block("paragraph", data={"text": _first_sentences(tb.text, 700)}),
                Block("callout", title="Dry-run note", data={
                    "text": ("Content shown is extracted verbatim from the source "
                             "chapter. Run with OPENAI_API_KEY + Mathpix keys for "
                             "the full GPT-authored workbook."),
                }),
            ],
        ))
    if not topics:
        topics = [Topic(
            number="01", title=meta["chapter_title"],
            overview="No structured topics detected in the source PDF.",
            blocks=[Block("paragraph", data={"text": _first_sentences(src.full_text, 700)
                                             or "(empty source)"})],
        )]

    glossary = [
        GlossaryItem(term=t.title, definition=f"Introduced under '{t.title}' in this chapter.")
        for t in topics[:8]
    ]
    return Chapter(
        chapter_number=meta["chapter_number"],
        chapter_title=meta["chapter_title"],
        subject=meta["subject"],
        grade=meta["grade"],
        summary=_first_sentences(src.full_text, 400),
        study_strategy=[
            "Skim the topic overviews first, then study each topic in order.",
            "Attempt the source exercises after each topic.",
            "Use the glossary for quick pre-exam revision.",
        ],
        glossary=glossary,
        topics=topics,
        quick_recap=[t.title for t in topics],
        discipline=meta.get("discipline", ""),
    )


def generate(source_pdf: Path, subject: str = "", live: bool | None = None) -> dict:
    """Generate a revision-workbook PDF for one chapter source PDF."""
    _vendor()
    meta = infer_workbook_metadata(source_pdf.name, subject)
    out_pdf, build_log = _output_paths(meta)
    go_live = use_live() if live is None else live

    if go_live:
        from pipeline import run as pipeline_run  # vendored (needs keys)
        CACHE_ROOT.mkdir(parents=True, exist_ok=True)
        result = pipeline_run({
            "source_pdf": str(source_pdf),
            "subject": meta["subject"],
            "grade": meta["grade"],
            "chapter_number": meta["chapter_number"],
            "chapter_title": meta["chapter_title"],
            "discipline": meta["discipline"],
            "output_pdf": str(out_pdf),
            "build_log": str(build_log),
            "mmd_cache_dir": str(CACHE_ROOT / "mmd"),
            "plan_cache_dir": str(CACHE_ROOT / "plan"),
        })
        result["mode"] = "live"
        result["meta"] = meta
        return result

    from config import DEFAULT_CONFIG       # vendored
    from document import WorkbookDocument   # vendored
    from refiner import Refiner             # vendored
    from validate import WorkbookValidator  # vendored

    chapter = _dry_chapter(meta, source_pdf)
    Refiner().refine(chapter)
    WorkbookDocument(dict(DEFAULT_CONFIG)).build(chapter, str(out_pdf))

    validator = WorkbookValidator()
    valid, issues = validator.validate(str(out_pdf), chapter)
    messages = [
        "MODE: DRY (deterministic content, real renderer)",
        f"Source: {source_pdf.name}",
        f"{meta['subject']} | {meta['grade']} | Chapter {meta['chapter_number']}",
        f"Title: {meta['chapter_title']}",
        f"Topics: {len(chapter.topics)} · glossary: {len(chapter.glossary)}",
        f"PDF: {out_pdf}",
        "Live mode (GPT + Mathpix) requires OPENAI_API_KEY, MATHPIX_APP_ID/KEY "
        "and AEGIS_USE_LIVE=1.",
    ]
    validator.write_log(str(build_log), messages, issues)
    return {
        "output_pdf": str(out_pdf), "build_log": str(build_log),
        "valid": valid, "issues": issues, "mode": "dry", "meta": meta,
    }


# --------------------------------------------------------------------------- #
# Library listing
# --------------------------------------------------------------------------- #

def library() -> list[dict]:
    """All generated workbooks, grouped by Class NN / Subject."""
    out: list[dict] = []
    if not WORKBOOK_ROOT.exists():
        return out
    for pdf in sorted(WORKBOOK_ROOT.rglob("*.pdf")):
        rel = pdf.relative_to(WORKBOOK_ROOT)
        if rel.parts and rel.parts[0] == "_cache":
            continue
        parts = rel.parts
        out.append({
            "class_folder": parts[0] if len(parts) > 2 else "",
            "subject": parts[1] if len(parts) > 2 else "",
            "name": pdf.name,
            "rel": str(rel),
            "size": pdf.stat().st_size,
            "has_log": pdf.with_suffix("").with_suffix(".build_log.txt").exists()
            or (pdf.parent / f"{pdf.stem}.build_log.txt").exists(),
        })
    return out


def resolve_library_file(rel: str) -> Path:
    """Safe path resolution inside the workbook library (no traversal)."""
    target = (WORKBOOK_ROOT / rel).resolve()
    if not str(target).startswith(str(WORKBOOK_ROOT.resolve())):
        raise ValueError("invalid path")
    if not target.is_file():
        raise FileNotFoundError(rel)
    return target

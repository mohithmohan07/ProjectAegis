"""Phase 2.2.1 GPT PDF-to-ACSD fallback and source-asset contract.

Mathpix remains the preferred converter.  This module is used only when a PDF
conversion hard-fails or an objective quality gate classifies the result as
unusable.  GPT never writes free-form MMD: it returns strict page/block JSON,
that JSON is independently verified against the same original PDF pages, and
Aegis deterministically renders the accepted structure into MMD before the
normal ACSD compiler and source gates run.
"""
from __future__ import annotations

import base64
import copy
import hashlib
from collections import Counter
import hmac
import json
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urlencode

from .. import config
from . import canonical_source
from . import canonical_source_phase2 as phase2
from . import canonical_source_phase21_structure as structure
from . import canonical_source_phase22 as phase22
from . import katex_rules as kr
from . import progress

# Part of every transcription cache key: bump on any material change to the
# extraction/verification contract so sealed pages re-derive under the new
# rules instead of replaying stale judgments.
FALLBACK_VERSION = "2.3.2"
FALLBACK_COMPILER = "gpt-pdf-to-acsd-2"
FALLBACK_ORIGIN = "gpt_pdf_acsd_fallback"
# The marker written into the rendered MMD header. Deliberately distinct
# from FALLBACK_ORIGIN, which labels the job-level conversion source.
MMD_SOURCE_ORIGIN = "gpt-pdf-to-acsd"
# Version stamped on the extracted page bundle. Phase 3's page-evidence cache
# validates against this exact constant, so producer and consumer can never
# drift apart again (a hardcoded mismatch previously made that cache dead and
# re-entered this lane on every Phase 3 rebuild).
PAGE_ACSD_SCHEMA_VERSION = "1.1.0"
GPT_PAGE_ACSD_FILENAME = "source.gpt-page-acsd.json"
MATHPIX_RAW_FILENAME = "source.mathpix.raw.mmd"
ASSET_DIRNAME = "assets"
OPTIONAL_ARTIFACT_SPECS: dict[str, dict[str, str]] = {
    "gpt_page_acsd": {
        "filename": GPT_PAGE_ACSD_FILENAME,
        "media_type": "application/json; charset=utf-8",
        "label": "GPT page-level ACSD extraction",
    },
    "mathpix_raw": {
        "filename": MATHPIX_RAW_FILENAME,
        "media_type": "text/markdown; charset=utf-8",
        "label": "Preserved Mathpix MMD",
    },
}

_ALLOWED_KINDS = (
    "heading",
    "paragraph",
    "source",
    "task",
    "list",
    "table",
    "figure",
    "math",
    "other",
)
_BBOX_RE = re.compile(r"^[0-9a-f]{64}\.jpg$")
_SPACE_RE = re.compile(r"\s+")
_WORD_RE = re.compile(r"[^\W_]{3,}", re.UNICODE)
_CACHE_DIR = config.DATA_DIR / "pdf-acsd-cache"
_SOURCE_COMPATIBLE_KINDS = frozenset({"heading", "paragraph", "list", "other"})


@dataclass(frozen=True)
class PdfPage:
    page_id: str
    page_number: int
    text: str
    image_data_url: str
    width: float
    height: float


BatchProvider = Callable[[list[PdfPage]], dict[str, Any]]


def _enabled() -> bool:
    return os.environ.get(
        "AEGIS_GPT_PDF_ACSD_FALLBACK_ENABLED", "1"
    ).strip().lower() not in {"0", "false", "no", "off"}


def _forced() -> bool:
    return os.environ.get(
        "AEGIS_GPT_PDF_ACSD_FALLBACK_FORCE", "0"
    ).strip().lower() in {"1", "true", "yes", "on"}


def _batch_size() -> int:
    return max(1, min(4, int(os.environ.get(
        "AEGIS_GPT_PDF_ACSD_BATCH_PAGES", "3"
    ))))


def _parallel_batches() -> int:
    """Concurrent page-batch extractions. Batches are independent (own page
    range, own cache key), so they overlap safely up to the shared OpenAI
    concurrency gate. Default 1: every concurrent conversion contributes
    this many contenders for the shared slots, so a multi-creator
    deployment raises it only together with AEGIS_OPENAI_MAX_CONCURRENCY
    (keep runs x workers <= gate)."""
    return max(1, min(8, int(os.environ.get(
        "AEGIS_GPT_PDF_ACSD_PARALLEL_BATCHES", "1"
    ))))


def _max_pages() -> int:
    return max(1, int(os.environ.get(
        "AEGIS_GPT_PDF_ACSD_MAX_PAGES", "120"
    )))


def _min_page_confidence() -> float:
    return max(0.0, min(1.0, float(os.environ.get(
        "AEGIS_GPT_PDF_ACSD_MIN_CONFIDENCE", "0.96"
    ))))


def _max_output_tokens() -> int:
    return max(4000, int(os.environ.get(
        "AEGIS_GPT_PDF_ACSD_MAX_OUTPUT_TOKENS", "32000"
    )))


def _max_correction_attempts() -> int:
    return max(0, min(3, int(os.environ.get(
        "AEGIS_GPT_PDF_ACSD_MAX_CORRECTIONS", "2"
    ))))


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise

def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: object) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _normal(value: object) -> str:
    return _SPACE_RE.sub(" ", str(value or "")).strip().casefold()


def _public_base_url() -> str:
    configured = (
        os.environ.get("AEGIS_PUBLIC_BASE_URL", "").strip()
        or str(config.PUBLIC_BASE_URL or "").strip()
    ).rstrip("/")
    if configured.startswith("https://"):
        return configured
    for origin in config.CORS_ORIGINS:
        origin = str(origin or "").strip().rstrip("/")
        if origin.startswith("https://"):
            return origin
    raise ValueError(
        "AEGIS_PUBLIC_BASE_URL must be configured to the public HTTPS Aegis "
        "origin before GPT PDF-to-ACSD visual assets can be published"
    )


def _asset_secret() -> bytes:
    secret = (
        os.environ.get("AEGIS_SOURCE_ASSET_SECRET", "").strip()
        or str(config.SOURCE_ASSET_SECRET or "").strip()
        or "aegis-local-source-asset-secret"
    )
    return secret.encode("utf-8")


def asset_signature(job_id: int, filename: str) -> str:
    material = f"{int(job_id)}:{filename}".encode("utf-8")
    return hmac.new(_asset_secret(), material, hashlib.sha256).hexdigest()[:40]


def asset_url(job_id: int, filename: str) -> str:
    query = urlencode({"sig": asset_signature(job_id, filename)})
    return f"{_public_base_url()}/source-assets/{int(job_id)}/{filename}?{query}"


def validate_asset_signature(job_id: int, filename: str, signature: str) -> bool:
    if not _BBOX_RE.fullmatch(str(filename or "")):
        return False
    expected = asset_signature(job_id, filename)
    return hmac.compare_digest(expected, str(signature or ""))


def source_asset_path(job_id: int, filename: str) -> Path:
    from . import uploads

    if not _BBOX_RE.fullmatch(str(filename or "")):
        raise ValueError("invalid source asset filename")
    root = (uploads.source_artifact_directory(int(job_id)) / ASSET_DIRNAME).resolve()
    path = (root / filename).resolve()
    if path.parent != root:
        raise ValueError("invalid source asset path")
    return path


def _pdf_page_count(path: Path) -> int:
    import fitz

    document = fitz.open(path)
    try:
        return len(document)
    finally:
        document.close()


def _semantic_char_count(mmd_text: str) -> int:
    text = re.sub(r"https://\S+", " ", str(mmd_text or ""))
    text = re.sub(r"\\[A-Za-z]+(?:\[[^\]]*\])?", " ", text)
    text = re.sub(r"[{}$#*_`|<>]", " ", text)
    return len(_SPACE_RE.sub(" ", text).strip())


def _source_token_counter(value: object) -> Counter[str]:
    text = re.sub(r"https://\S+", " ", str(value or ""))
    text = re.sub(r"\\[A-Za-z]+(?:\[[^\]]*\])?", " ", text)
    return Counter(token.casefold() for token in _WORD_RE.findall(text))


def _pdf_text_coverage(path: Path, mmd_text: str) -> tuple[int, float | None]:
    """Return usable PDF text-layer token count and MMD coverage when available."""
    import fitz

    document = fitz.open(path)
    try:
        pdf_tokens: Counter[str] = Counter()
        for page in document:
            pdf_tokens.update(_source_token_counter(page.get_text("text") or ""))
    finally:
        document.close()
    total = sum(pdf_tokens.values())
    if total < 200:
        return total, None
    mmd_tokens = _source_token_counter(mmd_text)
    overlap = sum(
        min(count, mmd_tokens.get(token, 0))
        for token, count in pdf_tokens.items()
    )
    return total, overlap / max(1, total)


def _minimum_text_coverage() -> float:
    return max(0.0, min(1.0, float(os.environ.get(
        "AEGIS_GPT_PDF_ACSD_MIN_TEXT_COVERAGE", "0.45"
    ))))


def assess_mathpix_quality(
    mmd_text: str,
    source_path: Path,
    *,
    report: dict[str, Any] | None = None,
    hard_failure: str = "",
) -> dict[str, Any]:
    """Return an objective, conservative fallback decision.

    Isolated Phase 2.1/2.2 source gaps are not a full-conversion failure and are
    intentionally left to bounded adjudication.  The fallback is reserved for
    empty/truncated output or a broad failure surface.
    """
    reasons: list[str] = []
    suffix = source_path.suffix.lower()
    if suffix != ".pdf":
        return {"eligible": False, "use_fallback": False, "reasons": []}
    if hard_failure:
        reasons.append(f"mathpix_hard_failure:{hard_failure[:500]}")
    try:
        pages = _pdf_page_count(source_path)
    except Exception:
        pages = 0
    chars = len(str(mmd_text or "").strip())
    semantic_chars = _semantic_char_count(mmd_text)
    if _forced():
        reasons.append("forced_by_configuration")
    pdf_text_tokens = 0
    pdf_text_coverage: float | None = None
    if not hard_failure:
        if chars < max(800, pages * 70):
            reasons.append("mmd_too_short_for_pdf")
        if pages and semantic_chars / pages < 55:
            reasons.append("semantic_text_density_too_low")
        try:
            pdf_text_tokens, pdf_text_coverage = _pdf_text_coverage(
                source_path, mmd_text
            )
        except Exception:
            pdf_text_tokens, pdf_text_coverage = 0, None
        if (
            pdf_text_coverage is not None
            and pdf_text_coverage < _minimum_text_coverage()
        ):
            reasons.append(
                f"pdf_text_coverage_too_low:{pdf_text_coverage:.3f}"
            )
        phase2_issues = list((report or {}).get("phase2_issues") or [])
        issue_codes = {str(item.get("code") or "") for item in phase2_issues if isinstance(item, dict)}
        bounded = issue_codes and issue_codes.issubset(phase22.ELIGIBLE_ISSUE_CODES)
        broad_threshold = max(8, pages // 2) if pages else 8
        if len(phase2_issues) >= broad_threshold and not bounded:
            reasons.append("broad_canonical_source_failure")
        if "(no text detected" in str(mmd_text or "").casefold():
            reasons.append("no_text_detected")
    use_fallback = bool(reasons) and _enabled()
    return {
        "eligible": True,
        "use_fallback": use_fallback,
        "reasons": reasons,
        "page_count": pages,
        "mmd_chars": chars,
        "semantic_chars": semantic_chars,
        "pdf_text_tokens": pdf_text_tokens,
        "pdf_text_coverage": pdf_text_coverage,
    }


def collect_pdf_pages(
    path: Path,
    *,
    start_page: int = 1,
    end_page: int | None = None,
) -> list[PdfPage]:
    """Render a bounded 1-based PDF page range without loading the whole book."""
    import fitz

    document = fitz.open(path)
    try:
        total = len(document)
        if total > _max_pages():
            raise ValueError(
                f"GPT PDF-to-ACSD fallback supports at most {_max_pages()} pages; "
                f"this PDF has {total} pages"
            )
        first = max(1, int(start_page))
        last = total if end_page is None else min(total, max(first, int(end_page)))
        pages: list[PdfPage] = []
        for page_number in range(first, last + 1):
            page = document[page_number - 1]
            pixmap = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0), alpha=False)
            data = pixmap.tobytes("jpeg", jpg_quality=88)
            pages.append(PdfPage(
                page_id=f"PDF-PAGE-{page_number:04d}",
                page_number=page_number,
                text=page.get_text("text") or "",
                image_data_url=(
                    "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")
                ),
                width=float(page.rect.width),
                height=float(page.rect.height),
            ))
        return pages
    finally:
        document.close()


def _block_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "reading_order": {"type": "integer"},
            "kind": {"type": "string", "enum": list(_ALLOWED_KINDS)},
            "bbox": {
                "type": "array",
                "items": {"type": "number"},
            },
            "text": {"type": "string"},
            "heading_level": {"type": "integer"},
            "source_label": {"type": "string"},
            "latex": {"type": "string"},
            "table_rows": {
                "type": "array",
                "items": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "linked_visual_orders": {
                "type": "array",
                "items": {"type": "integer"},
            },
            "linked_context_orders": {
                "type": "array",
                "items": {"type": "integer"},
            },
            "caption": {"type": "string"},
            "confidence": {"type": "number"},
        },
        "required": [
            "reading_order", "kind", "bbox", "text", "heading_level",
            "source_label", "latex", "table_rows", "linked_visual_orders",
            "linked_context_orders", "caption", "confidence",
        ],
        "additionalProperties": False,
    }


def extraction_schema(pages: list[PdfPage]) -> dict[str, Any]:
    page_ids = [page.page_id for page in pages]
    return {
        "name": "aegis_pdf_page_acsd_extract",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "pages": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "page_id": {"type": "string", "enum": page_ids},
                            "blocks": {
                                "type": "array",
                                "items": _block_schema(),
                            },
                            "confidence": {"type": "number"},
                        },
                        "required": ["page_id", "blocks", "confidence"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["pages"],
            "additionalProperties": False,
        },
    }


def verification_schema(pages: list[PdfPage]) -> dict[str, Any]:
    page_ids = [page.page_id for page in pages]
    return {
        "name": "aegis_pdf_page_acsd_verify",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "verdict": {
                    "type": "string",
                    "enum": ["verified", "needs_correction", "ambiguous"],
                },
                "approved_page_ids": {
                    "type": "array",
                    "items": {"type": "string", "enum": page_ids},
                },
                "rejected_page_ids": {
                    "type": "array",
                    "items": {"type": "string", "enum": page_ids},
                },
                "confidence": {"type": "number"},
                "issues": {
                    "type": "array",
                    "items": {"type": "string"},
                    },
            },
            "required": [
                "verdict", "approved_page_ids", "rejected_page_ids",
                "confidence", "issues",
            ],
            "additionalProperties": False,
        },
    }


def _extraction_system_prompt() -> str:
    return """
You are the Aegis PDF-to-ACSD source transcriber. The supplied original PDF
page images are the only authority; the text layer is supporting evidence and
may be incomplete. Return every meaningful textbook block in exact visual
reading order. Never paraphrase, summarise, complete, or infer unseen wording.
Verbatim includes the source's own printed spelling errors — never silently
correct a typo the page visibly carries.
Omit only repeated running headers, footers, and bare page numbers.

Block rules:
- heading: preserve the complete visible heading and hierarchy level.
- paragraph/list: preserve exact wording, punctuation, numbering, and order.
- source: use only for a visibly labelled historical source, excerpt, passage,
  case study, or source box that is not itself a learner task. Preserve the
  exact visible cue in source_label and the body in text.
- task: separate learner instructions/questions from surrounding narrative and
  preserve the visible task cue (Activity, Discuss, Project, etc.) in source_label.
  A task with no visible cue leaves source_label empty — never invent one.
- Every item the learner is asked to complete is a task, in any subject:
  a fill-in-the-blanks statement whose blanks sit in the sentence itself, a
  match-the-pairs or true/false set, a discussion or think-about prompt, and
  a write/draw/observe instruction. Lower-grade books carry many such small
  items rather than long exercises; capture every one of them.
- A table the learner must complete is NOT itself a task. Emit the printed
  instruction ("Complete the table below.") as the task and the grid as one
  kind=table block that the task names in linked_context_orders. Never emit
  the same table as both a task and a table.
- ▯ marks a blank the learner fills INSIDE a sentence, where the printed
  blank interrupts the wording ("A cuboid has ▯ vertices, ▯ edges."). Never
  append ▯ to the end of a prompt: an answer box, ruled line, or dashed
  space printed AFTER a complete question is the place to write the answer,
  not part of the question — leave it out of the task text entirely.
- A table cell is transcribed only when it prints text. A cell whose content
  is a drawing carries no cell text: leave it empty, keep the drawing as its
  own figure block, and never describe it in words ("triangle figure"),
  substitute a lookalike character or emoji, or mark it ▯.
- A task block's text is never just its cue: a bare banner word ("Do it.",
  "Activity", "Discuss.") is not a task — attach the cue to the instruction
  that follows it in source_label, or emit no task at all.
- An activity's numbered steps ("(1) ... (2) ... (3) ...") are ONE procedure:
  keep them inside a single task block, including a closing
  write-down/record step; never emit an individual step as its own task.
- A printed conversation — speech bubbles, a teacher-and-pupil exchange, a
  cartoon dialogue — that poses ONE activity is ONE task: keep the whole
  exchange in a single task block in printed order. Never emit a bubble, a
  speaker's line, or a follow-up question as its own task.
- A worked example — a cue like "Example 3 :" followed by a problem statement
  whose solution is printed right after it — is a real question: emit the
  problem statement as a task block with the exact cue (e.g. "Example 3") in
  source_label, ending exactly where the printed solution begins. The printed
  solution returns as ordinary paragraph/math blocks in reading order, never
  inside the task text. Never emit the bare "Example N :" cue as a heading.
- source_label must be empty on heading, paragraph, list, table, figure, math,
  and other blocks. Never attach a source-box cue to an ordinary paragraph.
- table: return every visible cell in table_rows.
- figure: return a tight normalized bbox around the visual, plus its exact
  visible caption; do not invent a caption.
- math: return exact LaTeX in latex. In ordinary text, preserve inline maths with
  canonical [Katex] ... [/Katex] wrappers.
- linked_visual_orders: for a task, list only figure reading_order values visibly
  owned by that task on the page.
- linked_context_orders: for a task, list visible table, list, paragraph, source,
  or math block reading_order values required to understand or answer the task.
- bbox coordinates are normalized 0..1000 as [x0,y0,x1,y1].
If a page is unreadable, still return the page with low confidence rather than
inventing content. Output strict JSON only.
""".strip()


def _verification_system_prompt() -> str:
    return """
You are the independent Aegis PDF-to-ACSD verification reviewer. Compare the
candidate page/block extraction against the supplied original PDF page images.
Approve only when every meaningful block is present, wording is verbatim,
reading order and block roles are correct, task/figure ownership is supported,
and no content was invented. Judge ONLY against the extraction contract:
linked_visual_orders/linked_context_orders belong to task blocks alone — an
illustrative figure beside prose or poetry needs only its tight bbox and its
exact visible caption, and must never be rejected for missing ownership
links or any field the contract does not define for its kind. A candidate's
review_flags field is Aegis bookkeeping — ignore it. Canonical
[Katex] ... [/Katex] wrapping of visibly printed mathematics IS the
contract's required wire format — never reject it as invented markup or an
extraction artifact; judge only whether the wrapped content matches the
printed mathematics. Verbatim includes printed spelling errors: a candidate
that silently corrects the source's own typo is not verbatim. A visibly labelled historical source, excerpt,
passage, case study, or source box that is not a learner task must use
kind=source and retain its exact cue in source_label. A worked example's
problem statement (a cue like "Example 3 :" with its solution printed after
it) must be a kind=task block carrying the exact cue in source_label, with
the printed solution left as ordinary blocks. Fill-in-the-blanks statements
and discussion/think-about prompts are learner tasks: their presence as
kind=task blocks is required by the contract, not invented content. ▯ marks a
blank INSIDE a sentence; an answer box printed after a complete question is
the answer space and is correctly absent from the task text — never demand it
back. A table the learner completes is the instruction as kind=task PLUS one
kind=table grid it links through linked_context_orders — never the same table
twice. A table cell whose printed content is a drawing correctly has empty
cell text with the drawing kept as its own figure block: do not require a
worded description, a lookalike character, or ▯ in that cell. A printed
conversation or speech-bubble exchange posing one activity is correctly ONE
task block. A task whose text is only its bare cue ("Do it.") is a defect. An
activity's numbered steps must stay one task block. Do not rewrite or repair
the candidate. Return needs_correction or ambiguous when any material defect
remains. Output strict JSON only.
""".strip()


def _correction_system_prompt() -> str:
    return """
You are the bounded Aegis PDF-to-ACSD correction reviewer. Compare the supplied
candidate and deterministic validation failure against the original PDF page
images. Return the complete corrected page batch, changing only fields or block
roles required to resolve the stated defect. Preserve every visible word,
punctuation mark, reading-order position, bounding box, table cell, formula, and
figure link unless the original page proves the candidate wrong.

Use kind=source for a visibly labelled historical source, excerpt, passage, case
study, or source box that is not a learner task. Put its exact visible cue in
source_label and its body in text. Use kind=task for learner instructions or
questions AND for a worked example's problem statement (cue like "Example 3"
in source_label, printed solution left as ordinary blocks), with the exact
task cue in source_label. source_label must be empty on
all other block kinds. Never discard a visible cue merely to satisfy the schema.
Output the complete strict JSON pages array only.
""".strip()


def _correction_prompt(
    pages: list[PdfPage],
    *,
    candidate: dict[str, Any],
    reason: str,
) -> str:
    ledger = [
        {
            "page_id": page.page_id,
            "pdf_page_number_for_audit": page.page_number,
            "text_layer": page.text[:12000],
        }
        for page in pages
    ]
    return json.dumps({
        "page_ledger": ledger,
        "instruction": (
            "Correct only the stated structural defect and return the complete "
            "page batch. Preserve all source-visible content."
        ),
        "validation_failure": str(reason or "unspecified validation failure")[:4000],
        "candidate": candidate,
    }, ensure_ascii=False)


def _page_prompt(pages: list[PdfPage], *, candidate: dict[str, Any] | None = None) -> str:
    ledger = [
        {
            "page_id": page.page_id,
            "pdf_page_number_for_audit": page.page_number,
            "text_layer": page.text[:12000],
        }
        for page in pages
    ]
    payload: dict[str, Any] = {"page_ledger": ledger}
    if candidate is None:
        payload["instruction"] = "Extract page-level ACSD blocks exactly."
    else:
        payload["instruction"] = "Verify this candidate without rewriting it."
        payload["candidate"] = candidate
    return json.dumps(payload, ensure_ascii=False)


def _pdf_sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _batch_cache_key_from_sha(pdf_sha256: str, pages: list[PdfPage]) -> str:
    material = "\u241f".join([
        FALLBACK_VERSION,
        FALLBACK_COMPILER,
        config.OPENAI_MODEL,
        str(pdf_sha256 or ""),
        ",".join(page.page_id for page in pages),
    ])
    return _sha256_text(material)


def _bundle_cache_key(pdf_sha256: str) -> str:
    """Key for the sealed complete verified bundle of one source hash."""
    material = "\u241f".join([
        FALLBACK_VERSION,
        FALLBACK_COMPILER,
        config.OPENAI_MODEL,
        str(pdf_sha256 or ""),
        "full-verified-bundle",
    ])
    return _sha256_text(material)


def _batch_cache_key(path: Path, pages: list[PdfPage]) -> str:
    """Backward-compatible helper retained for focused unit tests."""
    return _batch_cache_key_from_sha(_pdf_sha256(path), pages)


def _batch_cache_path(key: str) -> Path:
    return _CACHE_DIR / f"{key}.json"


def _read_verified_batch_cache(key: str) -> dict[str, Any] | None:
    path = _batch_cache_path(key)
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict) or value.get("status") != "verified":
        return None
    return value


def _write_verified_batch_cache(key: str, value: dict[str, Any]) -> None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    canonical_source._atomic_write(
        _batch_cache_path(key), canonical_source._json_text(value)
    )


def _tokens(value: str) -> set[str]:
    return {token.casefold() for token in _WORD_RE.findall(value)}


# --- GPT chapter outline & question boundaries -------------------------------
#
# Structure is a semantic judgment, not a typographic one. Textbooks decorate
# pages with pedagogy banners ("Understand", "Do it.", "Discuss.", "Think
# about") that look exactly like headings, and boards disagree about whether
# "1 a) b)" is one question or three. Deterministic heading/enumeration
# heuristics produced 50 micro-sections for an 8-page Balbharati chapter and
# glued three independent MCQs into one item. This pass asks the model to
# decide the chapter title, the topic outline, and per-task question
# boundaries; deterministic code only validates references and compiles.

OUTLINE_VERSION = "chapter-outline-6"


_SOURCE_READER_RE = re.compile(r"<!--\s*source_reader:\s*([^>]*?)\s*-->")
_SOURCE_ORIGIN_RE = re.compile(r"<!--\s*source_origin:\s*([^>]*?)\s*-->")


def source_reader_version() -> str:
    """Identifies everything that decides the SHAPE of the rendered MMD.

    The compiler renders blocks; the outline decides which of them open a
    topic and where questions divide. A change to either produces different
    MMD from the same PDF, so both belong in the stamp.
    """
    return f"{FALLBACK_COMPILER}+{OUTLINE_VERSION}"


def mmd_reader_version(mmd_text: object) -> str:
    """The reader version stamped into an MMD, or "" when it carries none."""
    match = _SOURCE_READER_RE.search(str(mmd_text or ""))
    return match.group(1).strip() if match else ""


def mmd_is_gpt_reconstructed(mmd_text: object) -> bool:
    # Note this is the marker written into the MMD header, which is not the
    # same string as FALLBACK_ORIGIN (the job-level conversion-source label).
    match = _SOURCE_ORIGIN_RE.search(str(mmd_text or ""))
    return bool(match) and match.group(1).strip() == MMD_SOURCE_ORIGIN


def stale_mmd_reader(mmd_text: object) -> str:
    """The superseded reader version behind this MMD, or "" when current.

    Only MMD this module produced can be judged: a Mathpix conversion carries
    no stamp of ours and must not be called stale on our versioning.
    """
    if not mmd_is_gpt_reconstructed(mmd_text):
        return ""
    stamped = mmd_reader_version(mmd_text)
    if stamped == source_reader_version():
        return ""
    return stamped or "unstamped (pre-dates source reader versioning)"


def _outline_cache_key(pdf_sha256: str) -> str:
    material = "␟".join([
        FALLBACK_VERSION,
        OUTLINE_VERSION,
        config.OPENAI_MODEL,
        str(pdf_sha256 or ""),
        "chapter-outline",
    ])
    return _sha256_text(material)


def _outline_block_line(block: dict[str, Any]) -> str:
    order = int(block.get("reading_order") or 0)
    kind = str(block.get("kind") or "other")
    text = str(block.get("text") or "").strip()
    if kind == "heading":
        return f"  {order} heading[L{int(block.get('heading_level') or 1)}]: {text}"
    if kind in {"task", "source"}:
        label = str(block.get("source_label") or "").strip()
        body = re.sub(r"\s+", " ", text)[:1400]
        return f"  {order} {kind}[{label or 'no cue'}]: {body}"
    if kind == "table":
        rows = [
            " | ".join(str(cell or "").strip() for cell in row)
            for row in (block.get("table_rows") or [])[:2]
            if isinstance(row, list)
        ]
        return f"  {order} table: " + " // ".join(rows)[:300]
    if kind == "figure":
        return f"  {order} figure: {str(block.get('caption') or '').strip()[:120]}"
    words = re.sub(r"\s+", " ", text).split(" ")
    return f"  {order} {kind}: " + " ".join(words[:30])[:400]


def _task_block_refs(page_acsd: dict[str, Any]) -> list[tuple[str, int]]:
    """Every task block in the transcription, in reading order."""
    refs: list[tuple[str, int]] = []
    for page in page_acsd.get("pages") or []:
        if not isinstance(page, dict):
            continue
        page_id = str(page.get("page_id") or "")
        for block in sorted(
            [b for b in page.get("blocks") or [] if isinstance(b, dict)],
            key=lambda b: int(b.get("reading_order") or 0),
        ):
            if str(block.get("kind") or "") == "task":
                refs.append((page_id, int(block.get("reading_order") or 0)))
    return refs


def _outline_digest(
    page_acsd: dict[str, Any],
    *,
    only_refs: set[tuple[str, int]] | None = None,
) -> str:
    lines: list[str] = []
    for page in page_acsd.get("pages") or []:
        if not isinstance(page, dict):
            continue
        page_id = str(page.get("page_id") or "")
        blocks = sorted(
            [b for b in page.get("blocks") or [] if isinstance(b, dict)],
            key=lambda b: int(b.get("reading_order") or 0),
        )
        if only_refs is not None:
            blocks = [
                b for b in blocks
                if (page_id, int(b.get("reading_order") or 0)) in only_refs
            ]
            if not blocks:
                continue
        lines.append(page_id)
        for block in blocks:
            lines.append(_outline_block_line(block))
    digest = "\n".join(lines)
    if len(digest) > 90000:
        # Structure survives; long prose lines are the first to go.
        lines = [
            line for line in lines
            if not re.match(r"\s+\d+ (?:paragraph|other|list)\b", line)
        ]
        digest = "\n".join(lines)[:90000]
    return digest


def _outline_schema() -> dict[str, Any]:
    return {
        "name": "aegis_chapter_outline",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "chapter_title": {"type": "string"},
                "topics": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "kind": {
                                "type": "string",
                                "enum": ["content", "assessment"],
                            },
                            "start_page_id": {"type": "string"},
                            "start_reading_order": {"type": "integer"},
                        },
                        "required": [
                            "title", "kind",
                            "start_page_id", "start_reading_order",
                        ],
                        "additionalProperties": False,
                    },
                },
                "task_partitions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "page_id": {"type": "string"},
                            "reading_order": {"type": "integer"},
                            "independent_parts": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "label": {"type": "string"},
                                        "stem": {"type": "string"},
                                        "text": {"type": "string"},
                                    },
                                    "required": ["label", "stem", "text"],
                                    "additionalProperties": False,
                                },
                            },
                        },
                        "required": [
                            "page_id", "reading_order", "independent_parts",
                        ],
                        "additionalProperties": False,
                    },
                },
                "whole_tasks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "page_id": {"type": "string"},
                            "reading_order": {"type": "integer"},
                        },
                        "required": ["page_id", "reading_order"],
                        "additionalProperties": False,
                    },
                },
                "notes": {"type": "array", "items": {"type": "string"}},
            },
            "required": [
                "chapter_title", "topics", "task_partitions", "whole_tasks",
                "notes",
            ],
            "additionalProperties": False,
        },
    }


def _outline_system_prompt() -> str:
    return """
You are the Aegis chapter-outline judge. You receive a structural digest of a
verified textbook-chapter transcription: pages, blocks in reading order, block
kinds, heading levels, task cues, and text. Decide the chapter's semantic
structure. Deterministic code will only validate that your references exist —
your judgment IS the structure.

1. chapter_title — the chapter's own printed title, verbatim from the opening
   page (keep printed typos). Never use a series/book name or a section name.

2. topics — the ordered learning sections of the chapter, exactly as the book
   itself segments them. This holds for every board, subject, and grade —
   mathematics, science, history, geography, language and literature alike:
   - A topic starts where the book starts one: a heading that names a subject
     of study the chapter then teaches. Use the printed wording verbatim.
   - Pedagogy/activity banners are NEVER topics. They cue an action, not a
     subject: "Understand", "Do it.", "Discuss.", "Think about", "Revision",
     "At a glance", "Activity", "Let's recall", "Find out", "Project",
     "Demonstration / Practical", "Warm-up", "Working with the text",
     "Thinking about language" and the like are cues inside a topic.
   - A sub-head that only continues the topic already under way stays INSIDE
     it; start a new topic only where the book visibly turns to a new subject
     of the chapter.
   - PRESERVE the book's own segmentation even when a topic carries very
     little content. Lower-grade books have short topics by design: never
     merge topics because they look thin, never split one because it looks
     long, and never invent a topic the book does not have.
   - kind="assessment" marks question/exercise collections — an end-of-topic
     practice set, an end-of-chapter exercise, a question bank — under
     whatever name the book prints. They are not learning topics; their
     questions belong to the whole chapter.

3. task_partitions — question boundaries for task blocks that contain more
   than one INDEPENDENT question:
   - Boards differ, and this is exactly why you decide it: in some books
     "1. (a) (b)" is one question with dependent subparts; in others the
     serial is just a group number and each lettered/numbered item under it
     is its own complete question. Judge by the content itself, never by the
     numbering style: a subpart that can be asked and answered on its own
     (its own MCQ with options, its own fill-in, its own prompt about its own
     material) is an independent question. Subparts that share one stem's
     data, passage, or figure, or that build on each other's answers, stay
     together — do not partition such tasks at all.
   - A printed marker is NOT required. A task block that lists several
     separate prompts as bullets, dashes, or plain successive sentences —
     "What will happen if…" scenario lists, a set of unrelated observation
     questions under one banner — partitions exactly like a lettered one.
     Judge independence by the content; leave label "" when nothing is
     printed.
   - Each part: label = the printed item marker ("(i)", "2)", "b.") or "" if
     the book prints none; text = the part's complete wording COPIED VERBATIM
     from the task block; stem = any shared instruction that the part needs to
     stand alone (for example "Select the correct option."), also verbatim, or
     "" if none.
   - Never rewrite, complete, or merge wording. Every part text must be a
     contiguous passage of the task block's text.
   - Do not partition a task that is a single question, an activity's
     numbered steps (steps are one procedure, not questions), or a table to
     complete.

4. whole_tasks — EVERY task block you did NOT partition, by page_id and
   reading_order. This is not optional bookkeeping: between task_partitions
   and whole_tasks you must account for every single block the digest marks
   as `task`, exactly once. A task block you leave out of both lists is a
   question the chapter silently loses, so walk the digest and rule on each
   one. A task that is a single question, an activity, or a table to
   complete belongs here.

Return JSON per the schema. notes: anything you judged worth flagging.
""".strip()


def _resolve_partition_block(
    blocks_by_ref: dict[tuple[str, int], dict[str, Any]],
    page_numbers: dict[str, int],
    claimed: tuple[str, int],
    raw_parts: list[Any],
) -> tuple[tuple[str, int], dict[str, Any] | None]:
    """Find the task block that verbatim holds a partition's parts.

    Returns the claimed reference untouched when it is already right. When it
    is not, the only blocks considered are task blocks containing EVERY part
    verbatim, so a repair is grounded in the source rather than guessed; the
    nearest such block to the claim wins. Returns ``None`` when no block
    qualifies, which stays a dropped partition.
    """
    part_keys = [
        _normal(part.get("text"))
        for part in raw_parts
        if isinstance(part, dict) and _normal(part.get("text"))
    ]
    if len(part_keys) < 2:
        # Nothing to resolve against, and a one-part partition is dropped
        # downstream anyway. Report the claim so the caller flags it normally.
        block = blocks_by_ref.get(claimed)
        if block is not None and str(block.get("kind") or "") == "task":
            return claimed, block
        return claimed, None

    def _holds_every_part(block: dict[str, Any]) -> bool:
        if str(block.get("kind") or "") != "task":
            return False
        text_key = _normal(block.get("text"))
        return all(key in text_key for key in part_keys)

    claimed_block = blocks_by_ref.get(claimed)
    if claimed_block is not None and _holds_every_part(claimed_block):
        return claimed, claimed_block

    claimed_page = page_numbers.get(claimed[0], 0)
    candidates = [
        (candidate_ref, block)
        for candidate_ref, block in blocks_by_ref.items()
        if _holds_every_part(block)
    ]
    if not candidates:
        return claimed, None
    # Nearest to the claim: same page first, then closest reading order.
    candidates.sort(key=lambda item: (
        abs(page_numbers.get(item[0][0], 0) - claimed_page),
        abs(item[0][1] - claimed[1]),
        page_numbers.get(item[0][0], 0),
        item[0][1],
    ))
    return candidates[0]


_RUN_IN_PUNCTUATION_RE = re.compile(r"[\s\-–—:;,\.]+$")


def _trim_run_in_punctuation(value: object) -> str:
    """Drop the dash or colon a run-in heading uses to join its own body.

    Balbharati prints its characteristics as "Growth and Development- ..." on
    one line with the paragraph. The words are the topic; the joiner is
    typesetting, and carrying it through puts "Excretion -" on screen.
    """
    text = str(value or "").strip()
    trimmed = _RUN_IN_PUNCTUATION_RE.sub("", text)
    # Never let the trim empty a title that was only punctuation to begin with.
    return trimmed or text


def _normalize_chapter_outline(
    page_acsd: dict[str, Any],
    candidate: dict[str, Any],
) -> tuple[dict[str, Any] | None, list[str]]:
    """Validate references and verbatim bounds; normalize-and-flag, never fight."""
    flags: list[str] = []
    if not isinstance(candidate, dict):
        return None, ["outline response is not an object"]
    blocks_by_ref: dict[tuple[str, int], dict[str, Any]] = {}
    page_numbers: dict[str, int] = {}
    for page in page_acsd.get("pages") or []:
        if not isinstance(page, dict):
            continue
        page_id = str(page.get("page_id") or "")
        page_numbers[page_id] = int(page.get("page_number") or 0)
        for block in page.get("blocks") or []:
            if isinstance(block, dict):
                blocks_by_ref[(page_id, int(block.get("reading_order") or 0))] = block

    title = str(candidate.get("chapter_title") or "").strip()
    if not title:
        for (page_id, _order), block in sorted(
            blocks_by_ref.items(),
            key=lambda item: (page_numbers.get(item[0][0], 0), item[0][1]),
        ):
            if block.get("kind") == "heading" and str(block.get("text") or "").strip():
                title = str(block["text"]).strip()
                flags.append("chapter title missing; used the first heading")
                break

    topics: list[dict[str, Any]] = []
    seen_starts: set[tuple[str, int]] = set()
    for topic in candidate.get("topics") or []:
        if not isinstance(topic, dict):
            continue
        topic_title = _trim_run_in_punctuation(topic.get("title"))
        ref = (
            str(topic.get("start_page_id") or ""),
            int(topic.get("start_reading_order") or 0),
        )
        if not topic_title:
            flags.append(f"dropped an untitled topic at {ref}")
            continue
        if ref not in blocks_by_ref:
            flags.append(f"dropped topic {topic_title!r}: start {ref} does not exist")
            continue
        if ref in seen_starts:
            flags.append(f"dropped topic {topic_title!r}: duplicate start {ref}")
            continue
        seen_starts.add(ref)
        topics.append({
            "title": topic_title,
            "kind": (
                "assessment"
                if str(topic.get("kind") or "") == "assessment"
                else "content"
            ),
            "start_page_id": ref[0],
            "start_reading_order": ref[1],
        })
    topics.sort(key=lambda t: (
        page_numbers.get(t["start_page_id"], 0), t["start_reading_order"],
    ))
    if not any(t["kind"] == "content" for t in topics):
        # The chapter falls back to deterministic sectioning, but the model's
        # question boundaries are a separate judgment and still stand. Throwing
        # the whole outline away here used to silently re-deterministize every
        # question split as well.
        topics = []
        flags.append("no usable content topic in the outline")

    partitions: list[dict[str, Any]] = []
    resolved_refs: set[tuple[str, int]] = set()
    for partition in candidate.get("task_partitions") or []:
        if not isinstance(partition, dict):
            continue
        claimed = (
            str(partition.get("page_id") or ""),
            int(partition.get("reading_order") or 0),
        )
        # The model's judgment is WHICH parts are independent questions; the
        # block pointer is bookkeeping it sometimes gets wrong — aiming at the
        # "Exercises" heading instead of the task beneath it, which used to
        # drop the whole partition and its questions with it. The parts are
        # required to be verbatim, so the source itself says which block was
        # meant: resolve by that evidence, and keep the pointer only as a hint
        # for which candidate is nearest.
        ref, block = _resolve_partition_block(
            blocks_by_ref, page_numbers, claimed,
            partition.get("independent_parts") or [],
        )
        if block is None:
            flags.append(
                f"dropped a partition: no task block holds the parts claimed "
                f"at {claimed}"
            )
            continue
        if ref != claimed:
            flags.append(
                f"partition claimed at {claimed} was re-aimed at {ref}, the "
                "task block that verbatim holds its parts"
            )
        if ref in resolved_refs:
            # One task cannot be partitioned twice; downstream keys partitions
            # by block, so a second one would silently replace the first.
            flags.append(
                f"dropped a partition: task {ref} was already partitioned"
            )
            continue
        task_key = _normal(str(block.get("text") or ""))
        # A shared instruction is normally printed as the cue ABOVE the list it
        # governs — "What Will Happen, if…" over seven scenarios, "(1) Suggest
        # the appropriate word for the blanks." over four fill-ins — and the
        # transcription captures that cue as the block's source_label, not as
        # part of its text. Checking the text alone cleared every real stem.
        stem_key = task_key + " ␟ " + _normal(block.get("source_label"))
        parts: list[dict[str, str]] = []
        seen_texts: set[str] = set()
        for part in partition.get("independent_parts") or []:
            if not isinstance(part, dict):
                continue
            text = str(part.get("text") or "").strip()
            stem = str(part.get("stem") or "").strip()
            text_key = _normal(text)
            if not text_key or text_key in seen_texts:
                continue
            if text_key not in task_key:
                flags.append(
                    f"dropped a part of task {ref}: not verbatim in the block"
                )
                continue
            if stem and _normal(stem) not in stem_key:
                flags.append(
                    f"part stem of task {ref} is not verbatim; cleared it"
                )
                stem = ""
            seen_texts.add(text_key)
            parts.append({
                "label": str(part.get("label") or "").strip(),
                "stem": stem,
                "text": text,
            })
        if len(parts) >= 2:
            resolved_refs.add(ref)
            partitions.append({
                "page_id": ref[0],
                "reading_order": ref[1],
                "independent_parts": parts,
            })
        elif partition.get("independent_parts"):
            flags.append(
                f"dropped a partition of task {ref}: fewer than 2 verbatim parts"
            )

    # Between the two lists the model must account for every task block. One
    # it mentions in neither is not "left whole" — it is a block nobody ruled
    # on, and the questions inside it vanish without a trace. Name them.
    ruled: set[tuple[str, int]] = set(resolved_refs)
    for whole in candidate.get("whole_tasks") or []:
        if not isinstance(whole, dict):
            continue
        whole_ref = (
            str(whole.get("page_id") or ""),
            int(whole.get("reading_order") or 0),
        )
        block = blocks_by_ref.get(whole_ref)
        if block is None or str(block.get("kind") or "") != "task":
            flags.append(
                f"ignored a whole-task ruling: {whole_ref} is not a task block"
            )
            continue
        ruled.add(whole_ref)
    unruled = [ref for ref in _task_block_refs(page_acsd) if ref not in ruled]

    if not topics and not partitions:
        # Nothing of the model's reading survived validation. Claiming a
        # model-judged outline here would suppress the deterministic
        # enumeration without putting anything in its place.
        return None, flags + ["the outline carried neither topics nor partitions"]

    return {
        "version": OUTLINE_VERSION,
        "chapter_title": title,
        "topics": topics,
        "task_partitions": partitions,
        "unruled_task_refs": [list(ref) for ref in unruled],
        "notes": [
            str(note) for note in candidate.get("notes") or [] if str(note).strip()
        ],
        "review_flags": flags,
    }, flags


def _rule_on_omitted_tasks(
    page_acsd: dict[str, Any],
    candidate: dict[str, Any],
    outline: dict[str, Any],
) -> dict[str, Any]:
    """Ask once more about task blocks the first reading never ruled on.

    A partition list the model volunteers makes an omission invisible: a task
    it simply does not mention looks exactly like a task it judged whole, and
    any independent questions inside it are lost silently. The blocks are
    known, so they get put back in front of the model — scoped to just those
    blocks — rather than being assumed whole.
    """
    unruled = [
        (str(ref[0]), int(ref[1]))
        for ref in outline.get("unruled_task_refs") or []
        if isinstance(ref, (list, tuple)) and len(ref) == 2
    ]
    if not unruled:
        return outline
    progress.log(
        f"Chapter outline: {len(unruled)} task block(s) were left unruled; "
        "asking the model to rule on those blocks specifically.",
        level="warning",
    )
    try:
        follow_up = phase22._openai_multimodal_json(
            system=_outline_system_prompt(),
            prompt=(
                "You previously read this chapter but did not rule on every "
                "task block. Below are ONLY the blocks you left out, in "
                "context. For each one decide whether it is a single question "
                "(whole_tasks) or several independent questions "
                "(task_partitions), under the same rules. Repeat the chapter "
                "title and topics you already decided.\n\n"
                + _outline_digest(page_acsd, only_refs=set(unruled))
            ),
            pages=[],
            response_schema=_outline_schema(),
            purpose="chapter_outline",
            max_tokens=_max_output_tokens(),
        )
    except ValueError:
        raise
    except Exception as exc:  # the first reading still stands
        progress.log(
            f"Chapter outline: the follow-up ruling failed ({exc}); the "
            f"{len(unruled)} unruled task(s) stay whole.",
            level="warning",
        )
        return outline

    # Merge into the ORIGINAL candidate and re-validate the whole thing, so
    # the follow-up's partitions go through exactly the same grounding checks
    # as the first reading's — including verbatim parts and re-aiming.
    merged = copy.deepcopy(candidate)
    merged["task_partitions"] = list(merged.get("task_partitions") or []) + [
        row for row in follow_up.get("task_partitions") or []
        if isinstance(row, dict)
    ]
    merged["whole_tasks"] = list(merged.get("whole_tasks") or []) + [
        row for row in follow_up.get("whole_tasks") or []
        if isinstance(row, dict)
    ]
    repaired, flags = _normalize_chapter_outline(page_acsd, merged)
    if repaired is None:
        return outline
    for flag in flags:
        progress.log(f"Chapter outline: {flag}", level="warning")
    recovered = (
        len(repaired.get("task_partitions") or [])
        - len(outline.get("task_partitions") or [])
    )
    still_unruled = len(repaired.get("unruled_task_refs") or [])
    progress.log(
        f"Chapter outline: the follow-up ruling recovered {recovered} "
        f"partition(s); {still_unruled} task(s) remain unruled and stay whole.",
        level="success" if not still_unruled else "warning",
    )
    return repaired


def derive_chapter_outline(page_acsd: dict[str, Any]) -> dict[str, Any] | None:
    """Decide chapter title, topic outline, and question boundaries via GPT.

    Cached per source hash. An unusable response degrades to the deterministic
    structure (no outline) with a warning — it never blocks the conversion.
    """
    pdf_sha = str(page_acsd.get("pdf_sha256") or "")
    key = _outline_cache_key(pdf_sha)
    cached = _read_verified_batch_cache(key)
    if cached is not None and isinstance(cached.get("result"), dict):
        if cached["result"].get("version") == OUTLINE_VERSION:
            return copy.deepcopy(cached["result"])
    digest = _outline_digest(page_acsd)
    prompt = (
        "Structural digest of the verified chapter transcription "
        "(page id, then blocks as `<reading_order> <kind>[cue]: text`):\n\n"
        + digest
    )
    outline: dict[str, Any] | None = None
    for attempt in range(2):
        try:
            candidate = phase22._openai_multimodal_json(
                system=_outline_system_prompt(),
                prompt=prompt,
                pages=[],
                response_schema=_outline_schema(),
                purpose="chapter_outline",
                max_tokens=_max_output_tokens(),
            )
        except ValueError as exc:
            # A wiring mistake (unknown request purpose, malformed schema) is
            # ours, not the model's: it would silently disable the pass on
            # every book. Fail loudly instead of quietly shipping the old
            # deterministic structure.
            raise RuntimeError(
                f"chapter-outline pass is misconfigured: {exc}"
            ) from exc
        except Exception as exc:  # degraded, never fatal
            progress.log(
                f"Chapter-outline pass failed ({exc}); the deterministic "
                "structure remains in effect for this conversion.",
                level="warning",
            )
            return None
        outline, flags = _normalize_chapter_outline(page_acsd, candidate)
        if outline is not None:
            for flag in flags:
                progress.log(f"Chapter outline: {flag}", level="warning")
            break
        progress.log(
            "Chapter-outline response was unusable"
            + (f" ({'; '.join(flags[:3])})" if flags else "")
            + ("; retrying once." if attempt == 0 else "; continuing without it."),
            level="warning",
        )
    if outline is None:
        return None
    outline = _rule_on_omitted_tasks(page_acsd, candidate, outline)
    content_topics = [t["title"] for t in outline["topics"] if t["kind"] == "content"]
    progress.log(
        f"Chapter outline: {outline['chapter_title']!r}; "
        f"{len(content_topics)} topic(s): "
        + ", ".join(repr(t) for t in content_topics[:10])
        + (" …" if len(content_topics) > 10 else "")
        + f"; {len(outline['task_partitions'])} task(s) partitioned into "
        "independent questions.",
        level="success",
    )
    _write_verified_batch_cache(key, {
        "version": FALLBACK_VERSION,
        "status": "verified",
        "created_at": time.time(),
        "model": config.OPENAI_MODEL,
        "pdf_sha256": pdf_sha,
        "result": copy.deepcopy(outline),
    })
    return outline


# GPT page transcription occasionally double-escapes an in-cell line break
# ("Pyramid /\nPrism", "No. of\nvertical faces"), which later trips the
# raw-LaTeX wire-format validator. A lowercase-continuation heuristic is not
# enough ("\nvertical" continues lowercase yet is no command), so the judge is
# a whitelist of real LaTeX commands that begin with the JSON escape letters:
# those survive untouched; any other backslash-n/r/t is an escape artifact
# whose backslash+letter pair is replaced with spacing, keeping the rest of
# the word ("\nPrism" → " Prism", "\nvertical" → " vertical").
_LATEX_ESCAPE_LETTER_COMMANDS = frozenset({
    "ne", "neq", "nabla", "nu", "nmid", "notin", "nless", "ngtr", "nleq",
    "ngeq", "nleqslant", "ngeqslant", "nsim", "ncong", "nsubseteq",
    "nsupseteq", "nparallel", "nexists", "natural", "newline", "nolimits",
    "nonumber", "not", "notag", "nprec", "nsucc",
    "rho", "rightarrow", "rangle", "rceil", "rfloor", "rbrace", "rbrack",
    "real", "rtimes", "rightharpoonup", "rightharpoondown",
    "rightleftharpoons", "rvert",
    "tan", "tanh", "tau", "theta", "text", "textbf", "textit", "textrm",
    "times", "top", "tilde", "tfrac", "therefore", "thickapprox", "thicksim",
    "to", "triangle", "triangledown", "triangleleft", "triangleright",
    "tbinom",
})
_LITERAL_ESCAPE_ARTIFACT_RE = re.compile(r"[ \t]*\\([nrt][A-Za-z]*)")


def _scrub_literal_escape_artifacts(value: str) -> str:
    def _replace(match: re.Match[str]) -> str:
        token = match.group(1)
        if token in _LATEX_ESCAPE_LETTER_COMMANDS:
            return match.group(0)
        return f" {token[1:]}" if len(token) > 1 else " "

    return _LITERAL_ESCAPE_ARTIFACT_RE.sub(_replace, value)


# An answer box printed AFTER a complete question is where the learner writes,
# not part of the question. Boxes that interrupt a sentence ("has ▯ vertices")
# are the question and must survive.
_TRAILING_ANSWER_BOX_RE = re.compile(r"(?:\s*[▯□☐])+\s*$")


def _without_trailing_answer_boxes(text: str) -> str:
    stripped = _TRAILING_ANSWER_BOX_RE.sub("", text).rstrip()
    # Never empty a task: a prompt that is nothing but boxes keeps its text.
    return stripped or text


def _scrub_page_acsd_escape_artifacts(page_acsd: dict[str, Any]) -> None:
    """Scrub escape artifacts from every text field of a page-ACSD object.

    Fresh transcriptions are scrubbed during page validation, but a
    content-addressed cache replays earlier accepted pages verbatim — so the
    consumers (MMD rendering, ledger building) normalize again in place.
    A math block's ``latex`` field keeps its backslashes untouched.
    """
    for page in page_acsd.get("pages") or []:
        if not isinstance(page, dict):
            continue
        for block in page.get("blocks") or []:
            if not isinstance(block, dict):
                continue
            for field in ("text", "source_label", "caption"):
                value = block.get(field)
                if isinstance(value, str) and value:
                    block[field] = _scrub_literal_escape_artifacts(value)
            if str(block.get("kind") or "") == "task":
                block["text"] = _without_trailing_answer_boxes(
                    str(block.get("text") or "")
                )
            rows_value = block.get("table_rows")
            if isinstance(rows_value, list):
                block["table_rows"] = [
                    [
                        _scrub_literal_escape_artifacts(cell)
                        if isinstance(cell, str) else cell
                        for cell in table_row
                    ]
                    if isinstance(table_row, list) else table_row
                    for table_row in rows_value
                ]


def _canonicalize_source_cue_block(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize the model's historical-source cue into an explicit source block.

    The original schema allowed source_label on every block even though the
    validator reserved it for tasks. Source-heavy textbooks therefore produced
    a valid JSON object that the deterministic contract could never accept. This
    normalization is semantic-field reconciliation, not content inference: the
    exact label, text, bbox, order, and confidence are retained.
    """
    block = copy.deepcopy(raw)
    kind = str(block.get("kind") or "")
    source_label = str(block.get("source_label") or "").strip()
    if source_label and kind in _SOURCE_COMPATIBLE_KINDS:
        block["kind"] = "source"
        block["heading_level"] = 0
    elif kind == "source":
        block["heading_level"] = 0
    return block


def validate_page_extraction(
    pages: list[PdfPage],
    candidate: dict[str, Any],
) -> tuple[dict[str, Any] | None, str]:
    rows = candidate.get("pages")
    if not isinstance(rows, list):
        return None, "extractor returned no pages array"
    expected = [page.page_id for page in pages]
    received = [str(row.get("page_id") or "") for row in rows if isinstance(row, dict)]
    if sorted(received) != sorted(expected) or len(received) != len(set(received)):
        return None, "extractor page IDs do not exactly match the supplied batch"
    page_map = {page.page_id: page for page in pages}
    normalized_pages: list[dict[str, Any]] = []
    for row in rows:
        page_id = str(row.get("page_id") or "")
        page = page_map[page_id]
        confidence = float(row.get("confidence") or 0.0)
        blocks = row.get("blocks")
        if not isinstance(blocks, list):
            return None, f"{page_id} blocks are missing"
        # Structural opinions never kill a conversion: every book lays its
        # pages out differently, so deterministic code NORMALIZES what it
        # can fix mechanically and FLAGS what it cannot — hard rejection is
        # reserved for content that is genuinely unusable (missing pages,
        # missing text coverage). The model verifier remains the judge of
        # verbatim wording and completeness.
        flags: list[str] = []
        if confidence < _min_page_confidence():
            flags.append(
                f"page transcription confidence {confidence:.3f} is below "
                f"{_min_page_confidence():.3f}"
            )
        orders: list[int] = []
        normalized_blocks: list[dict[str, Any]] = []
        figure_orders: set[int] = set()
        for raw in blocks:
            if not isinstance(raw, dict):
                flags.append("dropped a non-object block")
                continue
            raw = copy.deepcopy(_canonicalize_source_cue_block(raw))
            order = int(raw.get("reading_order") or 0)
            kind = str(raw.get("kind") or "")
            bbox = raw.get("bbox")
            block_confidence = float(raw.get("confidence") or 0.0)
            if kind not in _ALLOWED_KINDS:
                flags.append(f"block {order}: unknown kind {kind!r} kept as 'other'")
                kind = "other"
                raw["kind"] = "other"
            if order < 1 or order in orders:
                new_order = (max(orders) + 1) if orders else 1
                flags.append(
                    f"reassigned invalid/duplicate reading_order {order} "
                    f"to {new_order}"
                )
                order = new_order
                raw["reading_order"] = order
            if (
                not isinstance(bbox, list) or len(bbox) != 4
                or any(not isinstance(value, (int, float)) for value in bbox)
            ):
                flags.append(f"block {order}: unusable bbox replaced with full page")
                raw["bbox"] = [0, 0, 1000, 1000]
            else:
                clamped = [min(1000.0, max(0.0, float(v))) for v in bbox]
                if clamped[0] >= clamped[2] or clamped[1] >= clamped[3]:
                    flags.append(
                        f"block {order}: degenerate bbox replaced with full page"
                    )
                    clamped = [0, 0, 1000, 1000]
                raw["bbox"] = clamped
            if block_confidence < 0.90:
                flags.append(
                    f"block {order}: transcription confidence "
                    f"{block_confidence:.3f} is low"
                )
            text = str(raw.get("text") or "").strip()
            latex = str(raw.get("latex") or "").strip()
            rows_value = raw.get("table_rows") or []
            heading_level = int(raw.get("heading_level") or 0)
            source_label = str(raw.get("source_label") or "").strip()
            scrubbed_text = _scrub_literal_escape_artifacts(text).strip()
            scrubbed_label = _scrub_literal_escape_artifacts(source_label).strip()
            scrubbed_rows = [
                [_scrub_literal_escape_artifacts(str(cell or "")) for cell in table_row]
                if isinstance(table_row, list) else table_row
                for table_row in rows_value
            ] if isinstance(rows_value, list) else rows_value
            if (
                scrubbed_text != text
                or scrubbed_label != source_label
                or scrubbed_rows != rows_value
            ):
                # Prose and table cells only — a math block's latex field keeps
                # its backslashes untouched.
                flags.append(
                    f"block {order}: normalized literal escape artifact(s) "
                    "in transcribed text"
                )
                text = scrubbed_text
                source_label = scrubbed_label
                rows_value = scrubbed_rows
                raw["text"] = scrubbed_text
                raw["source_label"] = scrubbed_label
                if isinstance(raw.get("table_rows"), list):
                    raw["table_rows"] = scrubbed_rows
            if kind not in {"figure", "math", "table", "source"} and not text:
                flags.append(f"dropped empty {kind} block {order}")
                continue
            if kind == "source" and not (source_label or text):
                flags.append(f"dropped empty source block {order}")
                continue
            if kind == "heading" and heading_level < 1:
                raw["heading_level"] = 2
            if kind != "heading" and heading_level != 0:
                raw["heading_level"] = 0
            # A task without a visible cue is legitimate (many books print
            # bare instructions); whether a VISIBLE cue was dropped is the
            # model verifier's judgement, not a deterministic presence rule.
            if kind == "source" and not source_label:
                flags.append(f"source block {order} carries no visible cue")
            if kind not in {"task", "source"} and source_label:
                # Keep the words: fold a stray cue into the block text.
                if source_label.casefold() not in text.casefold():
                    raw["text"] = f"{source_label} {text}".strip()
                raw["source_label"] = ""
                flags.append(
                    f"moved stray cue {source_label!r} into block {order}'s text"
                )
            if kind != "task" and (raw.get("linked_visual_orders") or []):
                raw["linked_visual_orders"] = []
                flags.append(f"stripped visual links from non-task block {order}")
            if kind == "math" and not latex:
                if text:
                    raw["kind"] = "paragraph"
                    kind = "paragraph"
                    flags.append(
                        f"math block {order} without LaTeX kept as paragraph"
                    )
                else:
                    flags.append(f"dropped empty math block {order}")
                    continue
            if kind == "table" and not rows_value:
                if text:
                    raw["kind"] = "paragraph"
                    kind = "paragraph"
                    flags.append(
                        f"table block {order} without rows kept as paragraph"
                    )
                else:
                    flags.append(f"dropped empty table block {order}")
                    continue
            if kind == "figure":
                figure_orders.add(order)
            orders.append(order)
            normalized_blocks.append(raw)
        if orders != sorted(orders):
            normalized_blocks.sort(key=lambda block: int(block["reading_order"]))
        blocks_by_order = {
            int(block.get("reading_order") or 0): block
            for block in normalized_blocks
        }
        for block in normalized_blocks:
            kept_visuals = []
            for linked in block.get("linked_visual_orders") or []:
                if int(linked) in figure_orders:
                    kept_visuals.append(linked)
                else:
                    flags.append(
                        f"removed link to unknown figure reading_order {linked}"
                    )
            if len(kept_visuals) != len(block.get("linked_visual_orders") or []):
                block["linked_visual_orders"] = kept_visuals
            kept_context = []
            for linked in block.get("linked_context_orders") or []:
                target = blocks_by_order.get(int(linked))
                if target is None or target.get("kind") in {"figure", "task", "heading"}:
                    flags.append(
                        f"removed invalid context link to reading_order {linked}"
                    )
                else:
                    kept_context.append(linked)
            if len(kept_context) != len(block.get("linked_context_orders") or []):
                block["linked_context_orders"] = kept_context
        text_layer_tokens = _tokens(page.text)
        extracted_text = " ".join(
            str(block.get("text") or "")
            + " "
            + " ".join(
                cell for table_row in block.get("table_rows") or [] for cell in table_row
            )
            for block in normalized_blocks
        )
        extracted_tokens = _tokens(extracted_text)
        if len(text_layer_tokens) >= 30:
            # The embedded text layer is corroborating evidence, not the
            # authority — the page image is. A book whose text layer is
            # garbled, differently ordered, or in another script would fail
            # this comparison while the model read the page perfectly, so a
            # divergence is reported for review and the independent verifier
            # decides whether the transcription is faithful.
            overlap = len(text_layer_tokens & extracted_tokens)
            coverage = overlap / max(1, len(text_layer_tokens))
            precision = overlap / max(1, len(extracted_tokens))
            if coverage < 0.35:
                flags.append(
                    f"text-layer token coverage {coverage:.2f} is low; the "
                    "transcription diverges from the embedded text layer"
                )
            if len(extracted_tokens) >= 30 and precision < 0.55:
                flags.append(
                    f"extracted-token precision {precision:.2f} is low; the "
                    "transcription diverges from the embedded text layer"
                )
        normalized_page: dict[str, Any] = {
            "page_id": page_id,
            "page_number": page.page_number,
            "confidence": confidence,
            "blocks": normalized_blocks,
        }
        if flags:
            normalized_page["review_flags"] = [
                f"{page_id}: {flag}" for flag in flags
            ]
        normalized_pages.append(normalized_page)
    normalized_pages.sort(key=lambda row: row["page_number"])
    return {"pages": normalized_pages}, ""


def _verification_rejection_reason(
    pages: list[PdfPage],
    verification: dict[str, Any],
) -> str:
    verdict = str(verification.get("verdict") or "")
    approved = sorted(str(value) for value in verification.get("approved_page_ids") or [])
    expected = sorted(page.page_id for page in pages)
    confidence = float(verification.get("confidence") or 0.0)
    rejected = sorted(str(value) for value in verification.get("rejected_page_ids") or [])
    issues = [
        str(value).strip() for value in verification.get("issues") or []
        if str(value).strip()
    ]
    reasons = list(issues)
    if verdict != "verified":
        reasons.append(f"verification verdict was {verdict or 'missing'}")
    if approved != expected:
        reasons.append("verification did not approve every supplied page exactly once")
    if rejected:
        reasons.append(f"verification rejected page(s): {', '.join(rejected)}")
    if confidence < _min_page_confidence():
        reasons.append(
            f"verification confidence {confidence:.3f} is below "
            f"{_min_page_confidence():.3f}"
        )
    return "; ".join(dict.fromkeys(reasons))


def extract_batch_via_openai(pages: list[PdfPage]) -> dict[str, Any]:
    evidence_pages = [
        phase22.EvidencePage(
            evidence_id=page.page_id,
            page_number=page.page_number,
            text=page.text,
            image_data_url=page.image_data_url,
            score=1.0,
        )
        for page in pages
    ]
    candidate = phase22._openai_multimodal_json(
        system=_extraction_system_prompt(),
        prompt=_page_prompt(pages),
        pages=evidence_pages,
        response_schema=extraction_schema(pages),
        purpose="page_transcription",
        max_tokens=_max_output_tokens(),
    )
    correction_history: list[dict[str, Any]] = []
    max_corrections = _max_correction_attempts()
    verification: dict[str, Any] = {}

    for pass_index in range(max_corrections + 1):
        normalized, reason = validate_page_extraction(pages, candidate)
        if normalized is None:
            if pass_index >= max_corrections:
                return {
                    "status": "review_required",
                    "reason": reason,
                    "correction_history": correction_history,
                }
            attempt = pass_index + 1
            correction_history.append({
                "attempt": attempt,
                "stage": "deterministic_validation",
                "reason": reason,
            })
            progress.log(
                f"GPT PDF-to-ACSD bounded correction {attempt}/{max_corrections}: "
                f"{reason}",
                level="warning",
            )
            candidate = phase22._openai_multimodal_json(
                system=_correction_system_prompt(),
                prompt=_correction_prompt(
                    pages, candidate=candidate, reason=reason
                ),
                pages=evidence_pages,
                response_schema=extraction_schema(pages),
                purpose="page_transcription",
                max_tokens=_max_output_tokens(),
            )
            continue

        verification = phase22._openai_multimodal_json(
            system=_verification_system_prompt(),
            prompt=_page_prompt(pages, candidate=normalized),
            pages=evidence_pages,
            response_schema=verification_schema(pages),
            purpose="page_transcription",
            max_tokens=6000,
        )
        reason = _verification_rejection_reason(pages, verification)
        if not reason:
            return {
                "status": "verified",
                "pages": normalized["pages"],
                "verification": verification,
                "correction_history": correction_history,
            }
        if pass_index >= max_corrections:
            # The best structurally-normalized candidate travels with the
            # failure so the orchestrator can ship it under review flags
            # instead of refusing the whole book.
            return {
                "status": "review_required",
                "reason": reason,
                "pages": copy.deepcopy(normalized["pages"]),
                "verification": verification,
                "correction_history": correction_history,
            }

        attempt = pass_index + 1
        correction_history.append({
            "attempt": attempt,
            "stage": "independent_verification",
            "reason": reason,
        })
        progress.log(
            f"GPT PDF-to-ACSD bounded correction {attempt}/{max_corrections}: "
            f"{reason}",
            level="warning",
        )
        candidate = phase22._openai_multimodal_json(
            system=_correction_system_prompt(),
            prompt=_correction_prompt(
                pages, candidate=normalized, reason=reason
            ),
            pages=evidence_pages,
            response_schema=extraction_schema(pages),
            purpose="page_transcription",
            max_tokens=_max_output_tokens(),
        )

    return {
        "status": "review_required",
        "reason": "bounded correction loop ended without a verified candidate",
        "verification": verification,
        "correction_history": correction_history,
    }


def extract_pdf_to_page_acsd(
    path: Path,
    *,
    provider: BatchProvider | None = None,
) -> dict[str, Any]:
    page_count = _pdf_page_count(path)
    if page_count < 1:
        raise ValueError("the PDF contains no pages")
    if page_count > _max_pages():
        raise ValueError(
            f"GPT PDF-to-ACSD fallback supports at most {_max_pages()} pages; "
            f"this PDF has {page_count} pages"
        )
    provider = provider or extract_batch_via_openai
    accepted: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    size = _batch_size()
    batch_count = (page_count + size - 1) // size
    pdf_sha = _pdf_sha256(path)
    # Nearest-stage resume: the fallback materializes at most once per source
    # hash. A sealed complete verified bundle is returned without re-entering
    # batch orchestration, so a semantic-only repair or Phase 3 rebuild causes
    # zero lane replay while the source is unchanged.
    sealed = _read_verified_batch_cache(_bundle_cache_key(pdf_sha))
    if sealed is not None and isinstance(sealed.get("result"), dict):
        bundle = copy.deepcopy(sealed["result"])
        if (
            bundle.get("pdf_sha256") == pdf_sha
            and len(bundle.get("pages") or []) == page_count
        ):
            progress.log(
                "Reusing the sealed verified GPT PDF-to-ACSD bundle for this "
                f"unchanged source ({page_count} page(s)); no model batches "
                "were replayed.",
                level="info",
            )
            # A bundle sealed before the outline pass existed (or under an
            # older outline version) still gets the semantic structure.
            existing_outline = bundle.get("chapter_outline")
            if (
                not isinstance(existing_outline, dict)
                or existing_outline.get("version") != OUTLINE_VERSION
            ):
                outline = derive_chapter_outline(bundle)
                if outline is not None:
                    bundle["chapter_outline"] = outline
            return bundle
    # The transcription is the longest stage of a conversion; the bar walks
    # through its own band batch by batch instead of freezing until the end.
    band_start = progress.current_value()
    band_end = (
        0.92 if band_start >= 0.25 else max(band_start + 0.02, 0.26)
    )
    progress.step(
        "Canonical source — GPT PDF-to-ACSD fallback",
        value=max(0.01, band_start),
    )
    progress.log(
        f"GPT PDF-to-ACSD fallback will inspect {page_count} original page(s) "
        f"in {batch_count} verified batch(es).",
        level="warning",
    )
    batch_progress_lock = threading.Lock()
    batches_done = [0]

    def _advance_batch_progress() -> None:
        with batch_progress_lock:
            batches_done[0] += 1
            done = batches_done[0]
        progress.set_progress(
            band_start + (band_end - band_start) * done / max(1, batch_count),
            label=(
                f"PDF transcription: {done}/{batch_count} page batch(es) "
                "verified"
            ),
        )
    def _one_batch(batch_index: int, start_page: int) -> dict[str, Any]:
        batch = collect_pdf_pages(
            path,
            start_page=start_page,
            end_page=min(page_count, start_page + size - 1),
        )
        key = _batch_cache_key_from_sha(pdf_sha, batch)
        cached = _read_verified_batch_cache(key)
        if cached is not None:
            result = copy.deepcopy(cached["result"])
            cache_state = "hit"
        else:
            result = provider(batch)
            cache_state = "miss"
            if result.get("status") == "verified":
                _write_verified_batch_cache(key, {
                    "version": FALLBACK_VERSION,
                    "status": "verified",
                    "created_at": time.time(),
                    "model": config.OPENAI_MODEL,
                    "pdf_sha256": pdf_sha,
                    "page_ids": [page.page_id for page in batch],
                    "result": result,
                })
        page_ids = [page.page_id for page in batch]
        # Drop page image data before returning to the orchestrator.
        del batch
        if result.get("status") == "verified":
            progress.log(
                f"Verified GPT PDF-to-ACSD batch {batch_index}/{batch_count} "
                f"({', '.join(page_ids)}; cache {cache_state}).",
                level="success",
            )
        _advance_batch_progress()
        return {
            "batch_index": batch_index,
            "page_ids": page_ids,
            "result": result,
            "cache": cache_state,
        }

    starts = list(enumerate(range(1, page_count + 1, size), start=1))
    outcomes: dict[int, dict[str, Any]] = {}
    workers = min(_parallel_batches(), batch_count)
    if workers <= 1:
        for batch_index, start_page in starts:
            outcomes[batch_index] = _one_batch(batch_index, start_page)
    else:
        # Batches are independent by construction (disjoint page ranges,
        # per-batch cache keys, per-thread PDF handles), so they overlap up
        # to the shared OpenAI concurrency gate. Assembly below stays in
        # deterministic batch order regardless of completion order. Each
        # task runs under a copy of the caller's contextvars so progress
        # events keep flowing to the active sink.
        import contextvars
        from concurrent.futures import ThreadPoolExecutor, as_completed

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(
                    contextvars.copy_context().run,
                    _one_batch, batch_index, start_page,
                ): batch_index
                for batch_index, start_page in starts
            }
            for future in as_completed(futures):
                outcome = future.result()
                outcomes[outcome["batch_index"]] = outcome
    for batch_index, _start_page in starts:
        outcome = outcomes[batch_index]
        result = outcome["result"]
        accepted_with_flags = False
        if result.get("status") != "verified":
            candidate_pages = result.get("pages") or []
            if not candidate_pages:
                # Only a genuinely unusable transcription (no candidate at
                # all) stops the source pipeline; residual model disputes
                # ship under review flags instead.
                raise ValueError(
                    f"GPT PDF-to-ACSD batch {batch_index}/{batch_count} "
                    "requires review: "
                    f"{result.get('reason') or 'verification failed'}"
                )
            unresolved = (
                "verification unresolved after bounded correction: "
                + str(result.get("reason") or "unknown reason")[:500]
            )
            result = copy.deepcopy(result)
            for row in result["pages"]:
                row.setdefault("review_flags", []).append(unresolved)
            accepted_with_flags = True
            progress.log(
                f"GPT PDF-to-ACSD batch {batch_index}/{batch_count} ships "
                "under review flags — the independent verifier did not "
                "fully approve the final candidate: "
                + str(result.get("reason") or "")[:300],
                level="warning",
            )
        rows = result.get("pages") or []
        accepted.extend(copy.deepcopy(rows))
        decisions.append({
            "batch": batch_index,
            "page_ids": outcome["page_ids"],
            "cache": outcome["cache"],
            "status": (
                "accepted_with_review_flags"
                if accepted_with_flags
                else "verified"
            ),
        })
    accepted.sort(key=lambda row: int(row.get("page_number") or 0))
    expected_pages = list(range(1, page_count + 1))
    received_pages = [int(row.get("page_number") or 0) for row in accepted]
    if received_pages != expected_pages:
        raise ValueError(
            "verified GPT page ACSD does not cover every original PDF page exactly once"
        )
    bundle = {
        "schema_name": "Aegis GPT Page ACSD",
        "schema_version": PAGE_ACSD_SCHEMA_VERSION,
        "compiler_version": FALLBACK_COMPILER,
        "source_origin": FALLBACK_ORIGIN,
        "model": config.OPENAI_MODEL,
        "pdf_sha256": pdf_sha,
        "pages": accepted,
        "batches": decisions,
    }
    outline = derive_chapter_outline(bundle)
    if outline is not None:
        bundle["chapter_outline"] = outline
    _write_verified_batch_cache(_bundle_cache_key(pdf_sha), {
        "version": FALLBACK_VERSION,
        "status": "verified",
        "created_at": time.time(),
        "model": config.OPENAI_MODEL,
        "pdf_sha256": pdf_sha,
        "result": copy.deepcopy(bundle),
    })
    return bundle


def _clip_bbox(page: Any, bbox: list[float]) -> Any:
    import fitz

    x0, y0, x1, y1 = [float(value) for value in bbox]
    rect = fitz.Rect(
        x0 / 1000.0 * page.rect.width,
        y0 / 1000.0 * page.rect.height,
        x1 / 1000.0 * page.rect.width,
        y1 / 1000.0 * page.rect.height,
    )
    rect.intersect(page.rect)
    if rect.width < 8 or rect.height < 8:
        raise ValueError("figure bbox is too small")
    return rect


def materialize_visual_assets(
    path: Path,
    page_acsd: dict[str, Any],
    *,
    job_id: int,
    artifact_dir: Path,
) -> int:
    import fitz

    asset_dir = artifact_dir / ASSET_DIRNAME
    asset_dir.mkdir(parents=True, exist_ok=True)
    document = fitz.open(path)
    count = 0
    try:
        for page_row in page_acsd.get("pages") or []:
            page_number = int(page_row.get("page_number") or 0)
            if page_number < 1 or page_number > len(document):
                raise ValueError("page ACSD references a page outside the PDF")
            page = document[page_number - 1]
            for block in page_row.get("blocks") or []:
                if block.get("kind") != "figure":
                    continue
                clip = _clip_bbox(page, list(block.get("bbox") or []))
                pixmap = page.get_pixmap(
                    matrix=fitz.Matrix(2.0, 2.0), clip=clip, alpha=False
                )
                data = pixmap.tobytes("jpeg", jpg_quality=88)
                filename = f"{_sha256_bytes(data)}.jpg"
                destination = asset_dir / filename
                if not destination.exists():
                    _atomic_write_bytes(destination, data)
                block["asset_filename"] = filename
                block["asset_url"] = asset_url(job_id, filename)
                count += 1
    finally:
        document.close()
    return count


def _escape_latex_cell(value: object) -> str:
    text = str(value or "")
    replacements = {
        "\\": r"\textbackslash{}",
        "{": r"\{",
        "}": r"\}",
        "&": r"\&",
        "%": r"\%",
        "#": r"\#",
        "_": r"\_",
        "$": r"\$",
        "^": r"\textasciicircum{}",
        "~": r"\textasciitilde{}",
    }
    return "".join(replacements.get(character, character) for character in text)


def _render_table(rows: list[list[str]]) -> str:
    width = max((len(row) for row in rows), default=1)
    spec = "|" + "|".join("l" for _ in range(width)) + "|"
    lines = [f"\\begin{{tabular}}{{{spec}}}", "\\hline"]
    for row in rows:
        padded = list(row) + [""] * (width - len(row))
        lines.append(" & ".join(_escape_latex_cell(cell) for cell in padded) + r" \\")
        lines.append("\\hline")
    lines.append("\\end{tabular}")
    return "\n".join(lines)


def _markdown_heading(level: int, text: str) -> str:
    depth = min(6, max(1, int(level or 1)))
    clean = re.sub(r"\s+", " ", str(text or "")).strip()
    return f"{'#' * depth} {clean}"


def _markdown_image(url: str, caption: str) -> str:
    safe = re.sub(r"\s+", " ", str(caption or "Source visual")).strip()
    safe = safe.replace("[", "(").replace("]", ")")
    return f"![{safe}]({url})"


def _canonical_task_heading(source_label: object) -> str:
    """Map arbitrary visible task cues to a parser-stable structural heading."""
    label = _normal(source_label)
    if label == "project":
        return "Project"
    if label == "activity":
        return "Activity"
    if label in {"write in brief", "questions", "exercises"}:
        return "Write in brief"
    return "Discuss"


_TASK_FIGURE_ISSUE_CODES = frozenset({
    "unresolved_required_visual",
    "unresolved_explicit_figure_reference",
    "ambiguous_explicit_figure_reference",
})


def _refresh_task_figure_issues(
    canonical: dict[str, Any],
    report: dict[str, Any],
) -> None:
    """Drop figure verdicts the model's own links have since answered.

    Validation runs once, on the deterministic parse of the rendered MMD,
    BEFORE the verified page relationships are applied — so its task/figure
    verdicts are guesses made from prose ("draw a circle of radius 6 cm"
    reads like a figure reference). Once the model's explicit
    linked_visual_orders are authoritative, those guesses are stale: a task
    was reported as needing an unresolvable visual while carrying no visual
    requirement at all. Re-check each task-scoped figure issue against the
    task as it now stands, and keep only what is still true.
    """
    tasks_by_id = {
        str(task.get("task_id") or ""): task
        for task in canonical.get("tasks") or []
        if isinstance(task, dict)
    }
    kept: list[dict[str, Any]] = []
    for issue in report.get("issues") or []:
        if not isinstance(issue, dict):
            continue
        code = str(issue.get("code") or "")
        task = tasks_by_id.get(str(issue.get("task_id") or ""))
        if code not in _TASK_FIGURE_ISSUE_CODES or task is None:
            kept.append(issue)
            continue
        if code == "unresolved_required_visual":
            still_true = bool(
                task.get("requires_visual")
                and not task.get("figure_refs")
                and not task.get("image_urls")
            )
        elif code == "unresolved_explicit_figure_reference":
            still_true = bool(task.get("unresolved_figure_reference_ids"))
        else:
            still_true = bool(task.get("ambiguous_figure_reference_ids"))
        if still_true:
            kept.append(issue)
    report["issues"] = kept


def _accept_gate_issues_with_flags(
    canonical: dict[str, Any],
    report: dict[str, Any],
    issues: list[dict[str, Any]],
) -> None:
    """Ship a usable GPT-parsed source under flags instead of refusing a book.

    The deterministic gate promotes some advisory warnings to blocking —
    a task that names a visual the Figure registry could not resolve, a
    residual rich-text nit. Under GPT-authoritative parsing those are review
    notes, not grounds to reject an otherwise complete chapter (an 8-page
    Grade 6 book was refused whole over one unresolved figure link). Only a
    genuine defect still stops the pipeline: a real ``error`` issue, or a
    source with no tasks at all to build from.
    """
    fatal, advisory = phase2.partition_gate_issues(issues)
    if not (canonical.get("tasks") or []):
        fatal = fatal or [{
            "severity": "error",
            "code": "phase2_no_tasks",
            "message": "the parsed source contains no learner tasks",
        }]
    if fatal:
        codes = ", ".join(
            str(issue.get("code") or "unknown") for issue in fatal[:8]
        )
        raise ValueError(
            "verified GPT page extraction did not pass the deterministic "
            "canonical-source gate" + (f": {codes}" if codes else "")
        )

    if advisory:
        notes = [
            f"{issue.get('code') or 'unknown'}"
            + (f" ({issue['task_id']})" if issue.get("task_id") else "")
            for issue in advisory
        ]
        progress.log(
            "GPT-parsed source ships under review flags — "
            f"{len(advisory)} advisory gate issue(s): "
            + "; ".join(notes[:8])
            + (" …" if len(notes) > 8 else ""),
            level="warning",
        )
        canonical.setdefault("source_review_flags", []).extend(notes)
        report.setdefault("source_review_flags", []).extend(notes)

    # The source is usable: let the downstream contract treat it as ready.
    canonical["phase2_inventory_ready"] = True
    report["phase2_inventory_ready"] = True
    canonical.setdefault("shadow_validation", {})["phase2_inventory_ready"] = True
    canonical["phase"] = phase22.ADJUDICATION_PHASE
    report["phase"] = phase22.ADJUDICATION_PHASE
    if report.get("status") == "failed":
        report["status"] = "passed_with_warnings"


def _attach_chapter_outline(
    canonical: dict[str, Any],
    page_acsd: dict[str, Any],
) -> None:
    """Carry the GPT-decided outline onto the compiled canonical source."""
    outline = page_acsd.get("chapter_outline")
    if not isinstance(outline, dict):
        return
    # The outline's chapter_title stays internal provenance only: the chapter
    # NAME the user sees always comes from the directory row they selected in
    # the concept-building page dropdowns, never from the book.
    canonical["chapter_outline"] = copy.deepcopy(outline)


def render_page_acsd_to_mmd(page_acsd: dict[str, Any]) -> str:
    """Render verified page objects to deterministic, parser-safe MMD.

    Every block is emitted exactly once in page reading order. Task-to-visual
    ownership is restored on the compiled ACSD object by
    :func:`apply_page_acsd_relationships`; the renderer never moves a Figure to
    make a parser heuristic happy.
    """
    _scrub_page_acsd_escape_artifacts(page_acsd)
    outline = page_acsd.get("chapter_outline") or {}
    topic_by_start: dict[tuple[str, int], dict[str, Any]] = {
        (
            str(topic.get("start_page_id") or ""),
            int(topic.get("start_reading_order") or 0),
        ): topic
        for topic in outline.get("topics") or []
        if isinstance(topic, dict)
    }
    outline_active = any(
        topic.get("kind") == "content" for topic in topic_by_start.values()
    )
    parts = [
        f"<!-- source_origin: {MMD_SOURCE_ORIGIN} -->",
        f"<!-- compiler_version: {FALLBACK_COMPILER} -->",
        # The MMD is written once, at conversion, and every later phase reads
        # it rather than the PDF. Without a stamp, improving the reader left
        # already-converted jobs silently on the old structure — the same
        # chapter kept yielding 4 topics while a fresh conversion gave 10.
        f"<!-- source_reader: {source_reader_version()} -->",
        "",
    ]
    for page in page_acsd.get("pages") or []:
        # Page provenance lives in ``source.gpt-page-acsd.json``. Avoid adding
        # synthetic page-marker prose to the semantic source consumed by the
        # concept extractor.
        page_id = str(page.get("page_id") or "")
        blocks = sorted(
            [block for block in page.get("blocks") or [] if isinstance(block, dict)],
            key=lambda block: int(block.get("reading_order") or 0),
        )
        for block in blocks:
            kind = str(block.get("kind") or "other")
            text = str(block.get("text") or "").strip()
            topic = topic_by_start.get(
                (page_id, int(block.get("reading_order") or 0))
            )
            if topic is not None and topic.get("kind") == "content":
                # The outline judged this block to open a chapter topic. The
                # topic heading IS the structure; the block itself is skipped
                # only when it was that same heading.
                parts.append(_markdown_heading(1, str(topic.get("title") or text)))
                parts.append("")
                if kind == "heading":
                    continue
            if kind == "figure":
                url = str(block.get("asset_url") or "").strip()
                if url:
                    parts.append(_markdown_image(
                        url, str(block.get("caption") or "")
                    ))
            elif kind == "heading":
                if outline_active:
                    # Under a decided outline, every other heading is a
                    # pedagogy banner or decorative sub-head: keep the words
                    # without the structural weight that minted a section per
                    # banner ("Understand", "Do it.", 50 sections a chapter).
                    if text:
                        parts.append(f"**{text}**")
                else:
                    parts.append(_markdown_heading(
                        int(block.get("heading_level") or 1), text
                    ))
            elif kind == "source":
                label = str(block.get("source_label") or "").strip()
                label_key = _normal(label)
                text_key = _normal(text)
                text_already_contains_label = bool(
                    label_key and (
                        text_key == label_key
                        or text_key.startswith(label_key + " ")
                    )
                )
                if label and not text_already_contains_label:
                    parts.append(_markdown_heading(1, label))
                if text:
                    parts.append(text)
            elif kind == "task":
                # The exact publisher/language cue remains in page ACSD and is
                # restored on the canonical task. The derived MMD uses a stable
                # structural cue so an arbitrary label cannot masquerade as a
                # chapter topic during semantic extraction.
                cue = _canonical_task_heading(block.get("source_label"))
                if outline_active:
                    # Same reasoning as the banner demotion above: the outline
                    # already decided the topics, so an activity cue must not
                    # mint a section of its own. A chapter with 24 task blocks
                    # was minting 24 "Discuss" sections around them.
                    parts.append(f"**{cue}**")
                else:
                    parts.append(_markdown_heading(1, cue))
                parts.append(text)
            elif kind == "table":
                parts.append(_render_table(list(block.get("table_rows") or [])))
            elif kind == "math":
                latex = str(block.get("latex") or "").strip()
                if latex:
                    parts.append(f"[Katex] {latex} [/Katex]")
            elif text:
                parts.append(text)
            parts.append("")
    return "\n".join(parts).strip() + "\n"


_TASK_MARKDOWN_CUE_RE = re.compile(
    r"^(?:#{1,6}\s+|\*\*)"
    r"(?:activity|discuss|project|write\s+in\s+brief|"
    r"think\s+about\s+it|let['’]?s\s+discuss|questions?|exercises?)"
    r"\b[\s:—-]*\*{0,2}[\s:—-]*",
    re.IGNORECASE,
)


def _task_match_key(value: object) -> str:
    """Return task wording without a parser-injected Markdown cue heading.

    The production task parser may flatten ``# Activity\nPrompt`` into one line.
    A generic heading regex is unsafe there because it can greedily consume the
    whole prompt up to its final whitespace. Strip only known task-cue headings.
    """
    text = kr._IMAGE_TAG_RE.sub(" ", str(value or "")).strip()
    text = _TASK_MARKDOWN_CUE_RE.sub("", text, count=1)
    return _normal(text)


def _canonical_block_text(block: dict[str, Any]) -> str:
    raw = str(block.get("raw_text") or block.get("display_text") or "")
    if block.get("kind") == "table":
        # Comparison key, not display: the page-side key is raw cells
        # joined with pipes, so tables flatten the same way here.
        return structure.flatten_table_markup(raw)
    return kr._IMAGE_TAG_RE.sub(" ", raw).strip()


def _page_block_match_key(block: dict[str, Any]) -> str:
    return _normal(_page_context_text(block))


def _match_canonical_block(
    canonical_blocks: list[dict[str, Any]],
    page_block: dict[str, Any],
    *,
    allowed_kinds: set[str],
    used_block_ids: set[str] | None = None,
) -> dict[str, Any] | None:
    """Resolve one verified page block back to its rendered ACSD block."""
    page_key = _page_block_match_key(page_block)
    if not page_key:
        return None
    used = used_block_ids or set()
    ranked: list[tuple[int, int, int, dict[str, Any]]] = []
    for candidate in canonical_blocks:
        block_id = str(candidate.get("block_id") or "")
        if not block_id or block_id in used:
            continue
        if str(candidate.get("kind") or "") not in allowed_kinds:
            continue
        candidate_key = _normal(_canonical_block_text(candidate))
        if not candidate_key:
            continue
        exact = candidate_key == page_key
        contained = (
            min(len(candidate_key), len(page_key)) >= 24
            and (candidate_key in page_key or page_key in candidate_key)
        )
        if exact or contained:
            ranked.append((
                0 if exact else 1,
                abs(len(candidate_key) - len(page_key)),
                int(candidate.get("source_start") or 0),
                candidate,
            ))
    if not ranked:
        return None
    ranked.sort(key=lambda item: item[:3])
    return ranked[0][3]



def _figure_payload_from_canonical(
    canonical: dict[str, Any],
) -> dict[str, tuple[str, list[str], str]]:
    images = {
        str(item.get("image_id") or ""): item
        for item in canonical.get("images") or []
        if isinstance(item, dict)
    }
    payload: dict[str, tuple[str, list[str], str]] = {}
    for figure in canonical.get("figures") or []:
        if not isinstance(figure, dict):
            continue
        urls = [str(value) for value in figure.get("image_urls") or [] if value]
        if not urls:
            for image_id in figure.get("image_ids") or []:
                url = str(images.get(str(image_id), {}).get("url") or "").strip()
                if url and url not in urls:
                    urls.append(url)
        figure_id = str(figure.get("figure_id") or "")
        caption = str(figure.get("caption_raw") or "").strip()
        for url in urls:
            payload[url] = (figure_id, urls, caption)
    return payload


def _without_asset_tags(value: object, asset_urls: set[str]) -> str:
    text = str(value or "")
    for match in list(kr._IMAGE_TAG_RE.finditer(text)):
        token = match.group(0)
        source = re.search(r'\bsrc="(?P<src>https://[^"]+)"', token)
        if source and source.group("src") in asset_urls:
            text = text.replace(token, " ")
    return re.sub(r"\s+", " ", text).strip()



def _page_context_text(block: dict[str, Any]) -> str:
    kind = str(block.get("kind") or "")
    if kind == "table":
        rows = [
            " | ".join(str(cell or "").strip() for cell in row)
            for row in block.get("table_rows") or []
            if isinstance(row, list)
        ]
        return "\n".join(row for row in rows if row.strip()).strip()
    if kind == "math":
        latex = str(block.get("latex") or "").strip()
        return kr.katex(latex) if latex else ""
    if kind == "source":
        label = str(block.get("source_label") or "").strip()
        text = str(block.get("text") or "").strip()
        label_key = _normal(label)
        text_key = _normal(text)
        if label and not (
            text_key == label_key or text_key.startswith(label_key + " ")
        ):
            return "\n".join(value for value in (label, text) if value)
        return text or label
    return str(block.get("text") or "").strip()

def apply_page_acsd_relationships(
    canonical: dict[str, Any],
    page_acsd: dict[str, Any],
) -> int:
    """Make verified page task relationships authoritative without reordering.

    The deterministic MMD parser is retained as a structural compiler, but its
    proximity-based task locations and Figure ownership are only provisional for
    GPT page ACSD.  This pass maps each verified page task to the compiled task,
    restores its exact source block location, preserves explicit cross-page
    Figure/context references, and applies page-local ownership links.
    """
    _scrub_page_acsd_escape_artifacts(page_acsd)
    figure_payload = _figure_payload_from_canonical(canonical)
    figures_by_id = {
        str(figure.get("figure_id") or ""): figure
        for figure in canonical.get("figures") or []
        if isinstance(figure, dict) and figure.get("figure_id")
    }
    fallback_urls = {
        str(block.get("asset_url") or "")
        for page in page_acsd.get("pages") or []
        for block in page.get("blocks") or []
        if isinstance(block, dict) and block.get("kind") == "figure"
        and str(block.get("asset_url") or "")
    }
    tasks = [
        task for task in canonical.get("tasks") or [] if isinstance(task, dict)
    ]
    tasks.sort(key=lambda task: (
        int(task.get("source_start") or 0),
        int(task.get("order") or 0),
    ))
    canonical_blocks = [
        block for block in canonical.get("blocks") or [] if isinstance(block, dict)
    ]
    sections = {
        str(section.get("section_id") or ""): section
        for section in canonical.get("sections") or []
        if isinstance(section, dict)
    }

    # Parser-proximity ownership is audit evidence only for this source lane.
    # Start a clean relationship ledger and repopulate it from verified page ACSD.
    for canonical_block in canonical_blocks:
        previous = [
            str(value)
            for field in ("task_ids", "source_task_ids")
            for value in canonical_block.get(field) or []
            if value
        ]
        if previous:
            canonical_block["gpt_pdf_acsd_parser_task_ids"] = sorted(set(previous))
        canonical_block.pop("source_task_ids", None)
        canonical_block["task_ids"] = []

    used_task_ids: set[str] = set()
    used_prompt_block_ids: set[str] = set()
    ownership: dict[str, list[str]] = {}
    mapped = 0
    expected_page_tasks = sum(
        1
        for page in page_acsd.get("pages") or []
        for block in page.get("blocks") or []
        if isinstance(block, dict) and block.get("kind") == "task"
    )

    outline = page_acsd.get("chapter_outline") or {}
    outline_partitions: dict[tuple[str, int], list[dict[str, Any]]] = {
        (
            str(partition.get("page_id") or ""),
            int(partition.get("reading_order") or 0),
        ): [
            part for part in partition.get("independent_parts") or []
            if isinstance(part, dict)
        ]
        for partition in outline.get("task_partitions") or []
        if isinstance(partition, dict)
    }
    page_number_by_id = {
        str(page.get("page_id") or ""): int(page.get("page_number") or 0)
        for page in page_acsd.get("pages") or []
        if isinstance(page, dict)
    }
    outline_starts = sorted(
        (
            (
                page_number_by_id.get(str(t.get("start_page_id") or ""), 0),
                int(t.get("start_reading_order") or 0),
                str(t.get("kind") or "content"),
            )
            for t in outline.get("topics") or []
            if isinstance(t, dict)
        ),
    )

    def _in_assessment_span(ref: tuple[str, int]) -> bool:
        position = (page_number_by_id.get(ref[0], 0), ref[1])
        latest_kind = ""
        for start_page, start_order, kind in outline_starts:
            if (start_page, start_order) <= position:
                latest_kind = kind
            else:
                break
        return latest_kind == "assessment"

    def add_figure(
        figure_id: str,
        task_id: str,
        linked_urls: list[str],
        linked_figure_ids: list[str],
        captions: dict[str, str],
        *,
        preferred_caption: str = "",
    ) -> None:
        figure = figures_by_id.get(str(figure_id), {})
        urls = [str(value) for value in figure.get("image_urls") or [] if value]
        canonical_caption = str(figure.get("caption_raw") or "").strip()
        for url in urls:
            if url and url not in linked_urls:
                linked_urls.append(url)
            if url:
                captions[url] = (
                    preferred_caption.strip()
                    or canonical_caption
                    or captions.get(url)
                    or "Source visual"
                )
        if figure_id and figure_id not in linked_figure_ids:
            linked_figure_ids.append(figure_id)
        if figure_id:
            ownership.setdefault(figure_id, []).append(task_id)

    for page in page_acsd.get("pages") or []:
        blocks = sorted(
            [block for block in page.get("blocks") or [] if isinstance(block, dict)],
            key=lambda block: int(block.get("reading_order") or 0),
        )
        blocks_by_order = {
            int(block.get("reading_order") or 0): block for block in blocks
        }
        figures_by_order = {
            order: block
            for order, block in blocks_by_order.items()
            if block.get("kind") == "figure"
        }
        page_figure_urls = {
            str(block.get("asset_url") or "")
            for block in figures_by_order.values()
            if str(block.get("asset_url") or "")
        }
        for block in blocks:
            if block.get("kind") != "task":
                continue
            prompt_key = _task_match_key(block.get("text") or "")
            if not prompt_key:
                raise ValueError(
                    f"{page.get('page_id') or 'PDF page'} has an empty verified task"
                )
            verified_prompt = str(block.get("text") or "").strip()
            prompt_source_block = _match_canonical_block(
                canonical_blocks,
                block,
                allowed_kinds={"paragraph", "list"},
                used_block_ids=used_prompt_block_ids,
            )
            if prompt_source_block is None:
                raise ValueError(
                    "verified GPT task text has no deterministic source block: "
                    f"{verified_prompt[:240]}"
                )

            candidates = []
            for candidate_task in tasks:
                candidate_id = str(candidate_task.get("task_id") or "")
                if candidate_id in used_task_ids:
                    continue
                task_key = _task_match_key(
                    candidate_task.get("raw_prompt")
                    or candidate_task.get("display_prompt")
                    or ""
                )
                if not task_key:
                    continue
                exact = task_key == prompt_key
                contained = (
                    min(len(task_key), len(prompt_key)) >= 24
                    and (task_key in prompt_key or prompt_key in task_key)
                )
                if exact or contained:
                    candidates.append((
                        0 if exact else 1,
                        abs(len(task_key) - len(prompt_key)),
                        int(candidate_task.get("source_start") or 0),
                        candidate_task,
                    ))

            label = str(block.get("source_label") or "").strip() or "Task"
            if candidates:
                candidates.sort(key=lambda item: item[:3])
                task = candidates[0][3]
                task_id = str(task.get("task_id") or "")
                old_raw = str(task.get("raw_prompt") or "")
                if old_raw and old_raw != verified_prompt:
                    task["gpt_pdf_acsd_parser_raw_prompt"] = old_raw
            else:
                # The legacy parser recognises a finite cue vocabulary. A
                # verified page task with another publisher/language cue is still
                # canonical source evidence, so create it directly from the page
                # ledger rather than forcing regex knowledge into the fallback.
                task_id = (
                    f"TASK-GPT-{int(page.get('page_number') or 0):04d}-"
                    f"{int(block.get('reading_order') or 0):04d}"
                )
                task = {
                    "task_id": task_id,
                    "qid": "",
                    "order": 0,
                    "order_index": 0,
                    "identity_key": "",
                    "chapter_wide": _normal(label) in {
                        "write in brief", "questions", "exercises"
                    },
                    "requires_context": False,
                    "shared_context": "",
                    "content_objects": {},
                    "explicit_figure_reference_ids": [],
                    "display_figure_reference_ids": [],
                    "raw_figure_reference_ids": [],
                    "figure_refs": [],
                    "image_urls": [],
                }
                tasks.append(task)
            used_task_ids.add(task_id)

            # Replace parser-flattened task text and location with the exact
            # verified page task block, while retaining parser values for audit.
            task["raw_prompt"] = verified_prompt
            task["source_label"] = label
            task["parent_source_label"] = label
            activity = _normal(label) in {"activity", "project"}
            task["activity_origin"] = activity
            worked = bool(re.fullmatch(
                r"(?:solved\s+)?examples?\s*\d*", _normal(label)
            ))
            task["source_kind"] = (
                "activity" if activity else
                "worked_example" if worked else (
                    "exercise"
                    if _normal(label) in {"write in brief", "questions", "exercises"}
                    else "checkpoint_question"
                )
            )

            prompt_block_id = str(prompt_source_block.get("block_id") or "")
            used_prompt_block_ids.add(prompt_block_id)
            prompt_source_block.setdefault("task_ids", []).append(task_id)
            task["source_start"] = int(prompt_source_block.get("source_start") or 0)
            task["source_end"] = int(prompt_source_block.get("source_end") or 0)
            task["section_id"] = str(prompt_source_block.get("section_id") or "")
            section = sections.get(task["section_id"], {})
            task["source_section_index"] = max(
                0, int(section.get("order") or 1) - 1
            )
            task["source_position"] = max(
                0,
                task["source_start"] - int(section.get("source_start") or 0),
            )

            raw_urls: list[str] = []
            raw_figure_ids: list[str] = []
            raw_captions: dict[str, str] = {}
            display_urls: list[str] = []
            display_figure_ids: list[str] = []
            display_captions: dict[str, str] = {}

            # Preserve deterministic explicit references as raw audit ownership.
            # A verified page-local link controls teacher-facing display when it
            # points at a different visual on the same page. Explicit Figures on
            # another page remain visible alongside any local visual.
            local_link_orders = [
                int(value) for value in block.get("linked_visual_orders") or []
            ]
            explicit_reference_ids = {
                str(value)
                for field in (
                    "explicit_figure_reference_ids",
                    "display_figure_reference_ids",
                    "raw_figure_reference_ids",
                )
                for value in task.get(field) or []
                if value
            }
            for figure_id in [
                str(value) for value in task.get("figure_refs") or [] if value
            ]:
                figure = figures_by_id.get(figure_id, {})
                reference_ids = {
                    str(value) for value in figure.get("reference_ids") or [] if value
                }
                if not (explicit_reference_ids and reference_ids & explicit_reference_ids):
                    continue
                add_figure(
                    figure_id,
                    task_id,
                    raw_urls,
                    raw_figure_ids,
                    raw_captions,
                )
                figure_urls = {
                    str(value) for value in figure.get("image_urls") or [] if value
                }
                is_cross_page = not bool(figure_urls & page_figure_urls)
                if not local_link_orders or is_cross_page:
                    add_figure(
                        figure_id,
                        task_id,
                        display_urls,
                        display_figure_ids,
                        display_captions,
                    )

            for linked in local_link_orders:
                figure_block = figures_by_order.get(linked)
                if not figure_block:
                    continue
                url = str(figure_block.get("asset_url") or "").strip()
                payload = figure_payload.get(url)
                if not url or payload is None:
                    continue
                figure_id, _urls, canonical_caption = payload
                preferred_caption = (
                    str(figure_block.get("caption") or "").strip()
                    or canonical_caption
                )
                add_figure(
                    figure_id,
                    task_id,
                    raw_urls,
                    raw_figure_ids,
                    raw_captions,
                    preferred_caption=preferred_caption,
                )
                add_figure(
                    figure_id,
                    task_id,
                    display_urls,
                    display_figure_ids,
                    display_captions,
                    preferred_caption=preferred_caption,
                )

            task["figure_refs"] = list(display_figure_ids)
            task["raw_figure_refs"] = list(raw_figure_ids)
            task["display_figure_refs"] = list(display_figure_ids)
            task["image_urls"] = list(display_urls)
            task["raw_image_urls"] = list(raw_urls)
            task["display_image_urls"] = list(display_urls)
            task["_image_captions"] = copy.deepcopy(display_captions)
            task["raw_image_captions"] = copy.deepcopy(raw_captions)
            task["display_image_captions"] = copy.deepcopy(display_captions)
            task["requires_visual"] = bool(display_urls)

            linked_context_orders = [
                int(value) for value in block.get("linked_context_orders") or []
            ]
            if linked_context_orders:
                context_objects: list[dict[str, Any]] = []
                context_parts: list[str] = []
                for linked in linked_context_orders:
                    context_block = blocks_by_order.get(linked)
                    if context_block is None:
                        continue
                    display_text = _page_context_text(context_block)
                    if not display_text:
                        continue
                    canonical_context = _match_canonical_block(
                        canonical_blocks,
                        context_block,
                        allowed_kinds={"paragraph", "source", "list", "table", "math", "other"},
                    )
                    context_parts.append(display_text)
                    context_object = {
                        "source_id": (
                            f"{page.get('page_id') or 'PDF-PAGE'}-"
                            f"BLOCK-{linked:04d}"
                        ),
                        "page_id": page.get("page_id"),
                        "reading_order": linked,
                        "kind": context_block.get("kind"),
                        "display_text": display_text,
                    }
                    if canonical_context is not None:
                        context_object["block_id"] = canonical_context.get("block_id")
                        ids = canonical_context.setdefault(
                            "gpt_pdf_acsd_context_task_ids", []
                        )
                        if task_id not in ids:
                            ids.append(task_id)
                    context_objects.append(context_object)
                shared_context = kr.canonicalize_rich_text(
                    "\n".join(context_parts).strip()
                ).strip()
                task["shared_context"] = shared_context
                task["requires_context"] = bool(shared_context)
                content_objects = task.get("content_objects")
                if not isinstance(content_objects, dict):
                    content_objects = {}
                if context_objects:
                    content_objects["shared_context_blocks"] = context_objects
                else:
                    content_objects.pop("shared_context_blocks", None)
                task["content_objects"] = content_objects
            else:
                # An explicit Box/table reference may have been resolved by the
                # deterministic cross-page context linker. Absence of a page-local
                # link is not evidence that such context should be deleted.
                task["requires_context"] = bool(
                    task.get("requires_context") and task.get("shared_context")
                )

            task["source_location_confidence"] = "gpt_pdf_acsd_verified_block"
            task["gpt_pdf_acsd_relationship"] = {
                "page_id": page.get("page_id"),
                "task_reading_order": int(block.get("reading_order") or 0),
                "linked_visual_orders": [
                    int(value) for value in block.get("linked_visual_orders") or []
                ],
                "linked_context_orders": linked_context_orders,
            }
            task_ref = (
                str(page.get("page_id") or ""),
                int(block.get("reading_order") or 0),
            )
            gpt_parts = outline_partitions.get(task_ref)
            if gpt_parts:
                task["gpt_boundary_parts"] = copy.deepcopy(gpt_parts)
            if _in_assessment_span(task_ref):
                # Questions of an exercise/practice collection belong to the
                # chapter, not to whichever topic happens to precede the set;
                # the existing chapter-wide machinery assigns real topics.
                task["chapter_wide"] = True
                task["_topic_scope"] = "chapter"
            body = _without_asset_tags(verified_prompt, fallback_urls)
            tags = []
            for url in display_urls:
                try:
                    tags.append(kr.image(url, display_captions[url]))
                except ValueError:
                    continue
            task["display_prompt"] = kr.canonicalize_rich_text(
                " ".join([body, *tags]).strip()
            ).strip()
            mapped += 1

    if mapped != expected_page_tasks:
        raise ValueError(
            "verified GPT page ACSD task reconciliation is incomplete: "
            f"mapped {mapped}/{expected_page_tasks} task(s)"
        )

    unmatched = [
        task for task in tasks
        if str(task.get("task_id") or "") not in used_task_ids
    ]
    if unmatched:
        canonical["gpt_pdf_acsd_discarded_parser_tasks"] = [
            {
                "task_id": task.get("task_id"),
                "raw_prompt": str(task.get("raw_prompt") or "")[:500],
                "reason": "not_present_in_verified_page_task_ledger",
            }
            for task in unmatched
        ]
    canonical["tasks"] = [
        task for task in tasks
        if str(task.get("task_id") or "") in used_task_ids
    ]

    blocks_by_id = {
        str(block.get("block_id") or ""): block for block in canonical_blocks
    }
    for figure_id, figure in figures_by_id.items():
        urls = [str(value) for value in figure.get("image_urls") or [] if value]
        if not any(url in fallback_urls for url in urls):
            continue
        block = blocks_by_id.get(str(figure.get("block_id") or ""))
        if block is not None:
            block["task_ids"] = sorted(set(ownership.get(figure_id, [])))
    return mapped



def _persist_bundle(
    artifact_dir: Path,
    *,
    mmd_text: str,
    compiled: canonical_source.CompiledSource,
    page_acsd: dict[str, Any],
    mathpix_mmd: str,
) -> None:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    payloads: dict[str, str] = {
        canonical_source.ARTIFACT_SPECS["raw_mmd"]["filename"]: mmd_text,
        canonical_source.ARTIFACT_SPECS["canonical_json"]["filename"]: (
            canonical_source._json_text(compiled.canonical)
        ),
        canonical_source.ARTIFACT_SPECS["aegis_mmd"]["filename"]: compiled.aegis_mmd,
        canonical_source.ARTIFACT_SPECS["report"]["filename"]: (
            canonical_source._json_text(compiled.report)
        ),
        GPT_PAGE_ACSD_FILENAME: canonical_source._json_text(page_acsd),
    }
    if mathpix_mmd:
        payloads[MATHPIX_RAW_FILENAME] = mathpix_mmd
    for filename, content in payloads.items():
        canonical_source._atomic_write(artifact_dir / filename, content)


def _commit_staged_bundle(staging: Path, artifact_dir: Path) -> None:
    """Publish a fully verified fallback bundle without exposing partial files."""
    artifact_dir.mkdir(parents=True, exist_ok=True)
    backup = artifact_dir / f".phase221-backup-{uuid.uuid4().hex}"
    backup.mkdir(parents=True, exist_ok=False)
    published: list[Path] = []
    backed_up: list[tuple[Path, Path]] = []
    try:
        managed_names = {
            spec["filename"] for spec in canonical_source.ARTIFACT_SPECS.values()
        } | {
            spec["filename"] for spec in OPTIONAL_ARTIFACT_SPECS.values()
        } | {ASSET_DIRNAME}
        staged_names = {child.name for child in staging.iterdir()}
        # Remove stale managed artifacts only as part of the same rollback-safe
        # transaction. This matters when a later hard failure has no partial
        # Mathpix MMD but an earlier fallback left ``source.mathpix.raw.mmd``.
        for name in sorted(managed_names - staged_names):
            target = artifact_dir / name
            if not target.exists():
                continue
            saved = backup / name
            os.replace(target, saved)
            backed_up.append((saved, target))
        for child in list(staging.iterdir()):
            target = artifact_dir / child.name
            if target.exists():
                saved = backup / child.name
                os.replace(target, saved)
                backed_up.append((saved, target))
            os.replace(child, target)
            published.append(target)
    except Exception:
        for target in reversed(published):
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
            else:
                target.unlink(missing_ok=True)
        for saved, target in reversed(backed_up):
            if saved.exists():
                os.replace(saved, target)
        raise
    finally:
        shutil.rmtree(backup, ignore_errors=True)
        shutil.rmtree(staging, ignore_errors=True)




def _reconstruction_manifest(
    page_acsd: dict[str, Any],
    *,
    artifact_dir: Path,
    relationship_count: int,
    fallback_reason: Iterable[str] | None = None,
) -> dict[str, Any]:
    pages = [
        page for page in page_acsd.get("pages") or [] if isinstance(page, dict)
    ]
    asset_count = sum(
        1
        for page in pages
        for block in page.get("blocks") or []
        if isinstance(block, dict)
        and block.get("kind") == "figure"
        and block.get("asset_url")
    )
    reasons = list(
        fallback_reason
        if fallback_reason is not None
        else page_acsd.get("fallback_reason") or []
    )
    return {
        "version": FALLBACK_VERSION,
        "compiler": FALLBACK_COMPILER,
        "status": "verified",
        "source_origin": FALLBACK_ORIGIN,
        "fallback_reason": reasons,
        "model": str(page_acsd.get("model") or config.OPENAI_MODEL),
        "pdf_sha256": str(page_acsd.get("pdf_sha256") or ""),
        "page_count": len(pages),
        "batch_count": len(page_acsd.get("batches") or []),
        "asset_count": asset_count,
        "verified_task_visual_relationships": relationship_count,
        "mathpix_raw_preserved": (
            Path(artifact_dir) / MATHPIX_RAW_FILENAME
        ).exists(),
        "raw_pdf_changed": False,
        "page_acsd_sha256": _sha256_text(
            canonical_source._json_text(page_acsd)
        ),
    }


def _attach_reconstruction_metadata(
    canonical: dict[str, Any],
    report: dict[str, Any],
    reconstruction: dict[str, Any],
    *,
    source_filename: str,
) -> None:
    canonical["source_reconstruction"] = copy.deepcopy(reconstruction)
    canonical.setdefault("source_contract", {}).update({
        "source_origin": FALLBACK_ORIGIN,
        "reconstruction_version": FALLBACK_VERSION,
        "original_pdf_sha256": reconstruction["pdf_sha256"],
    })
    canonical.setdefault("document", {})["original_source_filename"] = (
        source_filename
    )
    report["source_reconstruction"] = copy.deepcopy(reconstruction)
    report.setdefault("summary", {})["source_reconstruction_pages"] = (
        reconstruction["page_count"]
    )
    report["summary"]["source_reconstruction_assets"] = reconstruction[
        "asset_count"
    ]


def rehydrate_verified_fallback(
    job: Any,
    canonical: dict[str, Any],
    report: dict[str, Any],
    *,
    artifact_dir: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Reapply the durable page ledger after a future core-compiler refresh."""
    artifact_dir = Path(artifact_dir)
    page_path = artifact_dir / GPT_PAGE_ACSD_FILENAME
    if not page_path.exists():
        return canonical, report
    marker = canonical.get("source_reconstruction")
    if (
        isinstance(marker, dict)
        and marker.get("status") == "verified"
        and (canonical.get("source_contract") or {}).get("source_origin")
        == FALLBACK_ORIGIN
    ):
        return canonical, report
    try:
        page_acsd = json.loads(page_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"verified GPT page ACSD artifact is unreadable: {exc}") from exc
    if not isinstance(page_acsd, dict) or page_acsd.get("source_origin") != FALLBACK_ORIGIN:
        raise ValueError("GPT page ACSD artifact has an invalid source origin")

    from . import uploads

    source_path = uploads.upload_file_path(job)
    expected_pdf_hash = str(page_acsd.get("pdf_sha256") or "")
    if (
        not source_path.exists()
        or source_path.suffix.lower() != ".pdf"
        or not expected_pdf_hash
        or _pdf_sha256(source_path) != expected_pdf_hash
    ):
        raise ValueError(
            "verified GPT page ACSD no longer matches the original uploaded PDF"
        )

    canonical = copy.deepcopy(canonical)
    report = copy.deepcopy(report)
    _attach_chapter_outline(canonical, page_acsd)
    relationship_count = apply_page_acsd_relationships(canonical, page_acsd)
    _refresh_task_figure_issues(canonical, report)
    canonical, report, issues = phase22._recalculate_after_adjudication(
        canonical, report
    )
    _accept_gate_issues_with_flags(canonical, report, issues)
    reconstruction = _reconstruction_manifest(
        page_acsd,
        artifact_dir=artifact_dir,
        relationship_count=relationship_count,
    )
    reconstruction["derived_mmd_sha256"] = _sha256_text(
        str(job.mmd_text or "")
    )
    _attach_reconstruction_metadata(
        canonical,
        report,
        reconstruction,
        source_filename=str(job.filename or "source.pdf"),
    )
    report["source_sha256"] = _sha256_text(str(job.mmd_text or ""))
    aegis_mmd = canonical_source._render_aegis_mmd(canonical).replace(
        "AEGIS CANONICAL SOURCE SHADOW",
        "AEGIS CANONICAL SOURCE GPT PDF-TO-ACSD",
        1,
    ).replace(
        "<!-- used_for_generation: false -->",
        "<!-- used_for_generation: source-critical -->",
        1,
    )
    canonical_source._atomic_write(
        artifact_dir / canonical_source.ARTIFACT_SPECS["canonical_json"]["filename"],
        canonical_source._json_text(canonical),
    )
    canonical_source._atomic_write(
        artifact_dir / canonical_source.ARTIFACT_SPECS["report"]["filename"],
        canonical_source._json_text(report),
    )
    canonical_source._atomic_write(
        artifact_dir / canonical_source.ARTIFACT_SPECS["aegis_mmd"]["filename"],
        aegis_mmd,
    )
    return canonical, report


def reconstruct_pdf_to_acsd(
    path: Path,
    *,
    job_id: int,
    artifact_dir: Path,
    fallback_reason: list[str],
    mathpix_mmd: str = "",
    provider: BatchProvider | None = None,
) -> dict[str, Any]:
    artifact_dir = Path(artifact_dir)
    staging = artifact_dir / f".phase221-staging-{uuid.uuid4().hex}"
    staging.mkdir(parents=True, exist_ok=False)
    try:
        page_acsd = extract_pdf_to_page_acsd(path, provider=provider)
        page_acsd["fallback_reason"] = list(fallback_reason)
        page_acsd["original_source_filename"] = path.name
        asset_count = materialize_visual_assets(
            path, page_acsd, job_id=job_id, artifact_dir=staging
        )
        mmd_text = render_page_acsd_to_mmd(page_acsd)
        compiled = phase2.compile_phase2_source(
            mmd_text,
            source_filename=path.name,
            consumer_module="build_concepts",
        )
        canonical = copy.deepcopy(compiled.canonical)
        report = copy.deepcopy(compiled.report)
        _attach_chapter_outline(canonical, page_acsd)
        relationship_count = apply_page_acsd_relationships(canonical, page_acsd)
        _refresh_task_figure_issues(canonical, report)
        canonical, report, issues = phase22._recalculate_after_adjudication(
            canonical, report
        )
        _accept_gate_issues_with_flags(canonical, report, issues)
        reconstruction = _reconstruction_manifest(
            page_acsd,
            artifact_dir=staging,
            relationship_count=relationship_count,
            fallback_reason=fallback_reason,
        )
        reconstruction["mathpix_raw_preserved"] = bool(mathpix_mmd)
        reconstruction["derived_mmd_sha256"] = _sha256_text(mmd_text)
        _attach_reconstruction_metadata(
            canonical,
            report,
            reconstruction,
            source_filename=path.name,
        )
        report["source_sha256"] = _sha256_text(mmd_text)
        aegis_mmd = canonical_source._render_aegis_mmd(canonical)
        aegis_mmd = aegis_mmd.replace(
            "AEGIS CANONICAL SOURCE SHADOW",
            "AEGIS CANONICAL SOURCE GPT PDF-TO-ACSD",
            1,
        ).replace(
            "<!-- used_for_generation: false -->",
            "<!-- used_for_generation: source-critical -->",
            1,
        )
        final = canonical_source.CompiledSource(
            canonical=canonical,
            aegis_mmd=aegis_mmd,
            report=report,
        )
        _persist_bundle(
            staging,
            mmd_text=mmd_text,
            compiled=final,
            page_acsd=page_acsd,
            mathpix_mmd=mathpix_mmd,
        )
        _commit_staged_bundle(staging, artifact_dir)
        return {
            "mmd_text": mmd_text,
            "canonical": canonical,
            "report": report,
            "page_acsd": page_acsd,
            "reconstruction": reconstruction,
        }
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def mark_fallback_review_required(
    artifact_dir: Path,
    *,
    fallback_reason: Iterable[str],
    error: Exception,
) -> None:
    """Persist a blocking source issue when unusable Mathpix cannot be replaced."""
    artifact_dir = Path(artifact_dir)
    canonical_path = (
        artifact_dir
        / canonical_source.ARTIFACT_SPECS["canonical_json"]["filename"]
    )
    report_path = (
        artifact_dir / canonical_source.ARTIFACT_SPECS["report"]["filename"]
    )
    if not canonical_path.exists() or not report_path.exists():
        return
    try:
        canonical = json.loads(canonical_path.read_text(encoding="utf-8"))
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return
    if not isinstance(canonical, dict) or not isinstance(report, dict):
        return
    reasons = [str(value) for value in fallback_reason if str(value)]
    issue = {
        "severity": "error",
        "code": "phase221_gpt_pdf_acsd_fallback_failed",
        "message": (
            "Mathpix failed the objective source-quality gate and the verified "
            "GPT PDF-to-ACSD fallback could not produce an accepted replacement."
        ),
        "fallback_reason": reasons,
        "fallback_error": str(error)[:1000],
    }
    report_issues = [
        item for item in report.get("issues") or [] if isinstance(item, dict)
    ]
    report_issues = [
        item for item in report_issues
        if item.get("code") != issue["code"]
    ]
    report["issues"] = [*report_issues, issue]
    report["phase2_inventory_ready"] = False
    report["status"] = "failed"
    report.setdefault("summary", {})["phase2_blocking_issues"] = max(
        1, int((report.get("summary") or {}).get("phase2_blocking_issues") or 0)
    )
    reconstruction = {
        "version": FALLBACK_VERSION,
        "compiler": FALLBACK_COMPILER,
        "status": "review_required",
        "source_origin": FALLBACK_ORIGIN,
        "fallback_reason": reasons,
        "failure_reason": str(error)[:1000],
        "raw_pdf_changed": False,
    }
    report["source_reconstruction"] = copy.deepcopy(reconstruction)
    canonical["source_reconstruction"] = copy.deepcopy(reconstruction)
    canonical["phase2_inventory_ready"] = False
    canonical.setdefault("shadow_validation", {})["phase2_inventory_ready"] = False
    canonical["shadow_validation"]["phase2_blocking_issues"] = max(
        1,
        int(
            (canonical.get("shadow_validation") or {}).get(
                "phase2_blocking_issues"
            )
            or 0
        ),
    )
    canonical_source._atomic_write(
        canonical_path, canonical_source._json_text(canonical)
    )
    canonical_source._atomic_write(
        report_path, canonical_source._json_text(report)
    )


def optional_artifact_manifest(directory: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    directory = Path(directory)
    files: list[dict[str, Any]] = []
    for kind, spec in OPTIONAL_ARTIFACT_SPECS.items():
        path = directory / spec["filename"]
        if not path.exists() or not path.is_file():
            continue
        files.append({
            "kind": kind,
            "label": spec["label"],
            "filename": spec["filename"],
            "media_type": spec["media_type"],
            "size_bytes": path.stat().st_size,
        })
    report_path = directory / canonical_source.ARTIFACT_SPECS["report"]["filename"]
    reconstruction: dict[str, Any] = {}
    if report_path.exists():
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            value = report.get("source_reconstruction")
            if isinstance(value, dict):
                reconstruction = value
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            pass
    return files, reconstruction


def optional_artifact_path(directory: Path, kind: str) -> tuple[Path, dict[str, str]]:
    spec = OPTIONAL_ARTIFACT_SPECS.get(str(kind or ""))
    if spec is None:
        raise ValueError("unknown optional source artifact")
    path = Path(directory) / spec["filename"]
    if not path.exists() or not path.is_file():
        raise ValueError("source artifact is not available for this upload")
    return path, spec

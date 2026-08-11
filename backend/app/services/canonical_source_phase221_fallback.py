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

FALLBACK_VERSION = "2.2.2"
FALLBACK_COMPILER = "gpt-pdf-to-acsd-2"
FALLBACK_ORIGIN = "gpt_pdf_acsd_fallback"
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
Omit only repeated running headers, footers, and bare page numbers.

Block rules:
- heading: preserve the complete visible heading and hierarchy level.
- paragraph/list: preserve exact wording, punctuation, numbering, and order.
- source: use only for a visibly labelled historical source, excerpt, passage,
  case study, or source box that is not itself a learner task. Preserve the
  exact visible cue in source_label and the body in text.
- task: separate learner instructions/questions from surrounding narrative and
  preserve the visible task cue (Activity, Discuss, Project, etc.) in source_label.
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
and no content was invented. A visibly labelled historical source, excerpt,
passage, case study, or source box that is not a learner task must use
kind=source and retain its exact cue in source_label. Do not rewrite or repair
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
source_label and its body in text. Use kind=task only for learner instructions or
questions, with the exact task cue in source_label. source_label must be empty on
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
        if confidence < _min_page_confidence():
            return None, f"{page_id} confidence {confidence:.3f} is below threshold"
        blocks = row.get("blocks")
        if not isinstance(blocks, list):
            return None, f"{page_id} blocks are missing"
        orders: list[int] = []
        normalized_blocks: list[dict[str, Any]] = []
        figure_orders: set[int] = set()
        for raw in blocks:
            if not isinstance(raw, dict):
                return None, f"{page_id} contains a non-object block"
            raw = _canonicalize_source_cue_block(raw)
            order = int(raw.get("reading_order") or 0)
            kind = str(raw.get("kind") or "")
            bbox = raw.get("bbox")
            block_confidence = float(raw.get("confidence") or 0.0)
            if order < 1 or kind not in _ALLOWED_KINDS:
                return None, f"{page_id} has an invalid block order/kind"
            if order in orders:
                return None, f"{page_id} repeats reading_order {order}"
            if (
                not isinstance(bbox, list) or len(bbox) != 4
                or any(not isinstance(value, (int, float)) for value in bbox)
                or not (0 <= bbox[0] < bbox[2] <= 1000)
                or not (0 <= bbox[1] < bbox[3] <= 1000)
            ):
                return None, f"{page_id} block {order} has an invalid bbox"
            if block_confidence < 0.90:
                return None, f"{page_id} block {order} confidence is too low"
            text = str(raw.get("text") or "").strip()
            latex = str(raw.get("latex") or "").strip()
            rows_value = raw.get("table_rows") or []
            linked_visuals = list(raw.get("linked_visual_orders") or [])
            heading_level = int(raw.get("heading_level") or 0)
            source_label = str(raw.get("source_label") or "").strip()
            if kind not in {"figure", "math", "table", "source"} and not text:
                return None, f"{page_id} block {order} has no visible text"
            if kind == "source" and not (source_label or text):
                return None, f"{page_id} source block {order} has no visible content"
            if kind == "heading" and heading_level < 1:
                return None, f"{page_id} heading block {order} has no hierarchy level"
            if kind != "heading" and heading_level != 0:
                return None, f"{page_id} non-heading block {order} has a heading level"
            if kind == "task" and not source_label:
                return None, f"{page_id} task block {order} has no source cue"
            if kind == "source" and not source_label:
                return None, f"{page_id} source block {order} has no source cue"
            if kind not in {"task", "source"} and source_label:
                return None, f"{page_id} non-task/non-source block {order} has a source cue"
            if kind != "task" and linked_visuals:
                return None, f"{page_id} non-task block {order} owns visual links"
            if kind == "math" and not latex:
                return None, f"{page_id} math block {order} has no LaTeX"
            if kind == "table" and not rows_value:
                return None, f"{page_id} table block {order} has no rows"
            if kind == "figure":
                figure_orders.add(order)
            orders.append(order)
            normalized_blocks.append(copy.deepcopy(raw))
        if orders != sorted(orders):
            normalized_blocks.sort(key=lambda block: int(block["reading_order"]))
        blocks_by_order = {
            int(block.get("reading_order") or 0): block
            for block in normalized_blocks
        }
        for block in normalized_blocks:
            for linked in block.get("linked_visual_orders") or []:
                if int(linked) not in figure_orders:
                    return None, (
                        f"{page_id} task links unknown figure reading_order {linked}"
                    )
            for linked in block.get("linked_context_orders") or []:
                target = blocks_by_order.get(int(linked))
                if target is None or target.get("kind") in {"figure", "task", "heading"}:
                    return None, (
                        f"{page_id} task links invalid context reading_order {linked}"
                    )
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
            overlap = len(text_layer_tokens & extracted_tokens)
            coverage = overlap / max(1, len(text_layer_tokens))
            precision = overlap / max(1, len(extracted_tokens))
            if coverage < 0.35:
                return None, f"{page_id} text-layer token coverage {coverage:.2f} is too low"
            if len(extracted_tokens) >= 30 and precision < 0.55:
                return None, f"{page_id} extracted-token precision {precision:.2f} is too low"
        normalized_pages.append({
            "page_id": page_id,
            "page_number": page.page_number,
            "confidence": confidence,
            "blocks": normalized_blocks,
        })
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
            return {
                "status": "review_required",
                "reason": reason,
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
            return bundle
    progress.step("Canonical source — GPT PDF-to-ACSD fallback", value=0.01)
    progress.log(
        f"GPT PDF-to-ACSD fallback will inspect {page_count} original page(s) "
        f"in {batch_count} verified batch(es).",
        level="warning",
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
        if result.get("status") != "verified":
            raise ValueError(
                f"GPT PDF-to-ACSD batch {batch_index}/{batch_count} requires review: "
                f"{result.get('reason') or 'verification failed'}"
            )
        rows = result.get("pages") or []
        accepted.extend(copy.deepcopy(rows))
        decisions.append({
            "batch": batch_index,
            "page_ids": outcome["page_ids"],
            "cache": outcome["cache"],
            "status": "verified",
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


def render_page_acsd_to_mmd(page_acsd: dict[str, Any]) -> str:
    """Render verified page objects to deterministic, parser-safe MMD.

    Every block is emitted exactly once in page reading order. Task-to-visual
    ownership is restored on the compiled ACSD object by
    :func:`apply_page_acsd_relationships`; the renderer never moves a Figure to
    make a parser heuristic happy.
    """
    parts = [
        "<!-- source_origin: gpt-pdf-to-acsd -->",
        f"<!-- compiler_version: {FALLBACK_COMPILER} -->",
        "",
    ]
    for page in page_acsd.get("pages") or []:
        # Page provenance lives in ``source.gpt-page-acsd.json``. Avoid adding
        # synthetic page-marker prose to the semantic source consumed by the
        # concept extractor.
        blocks = sorted(
            [block for block in page.get("blocks") or [] if isinstance(block, dict)],
            key=lambda block: int(block.get("reading_order") or 0),
        )
        for block in blocks:
            kind = str(block.get("kind") or "other")
            text = str(block.get("text") or "").strip()
            if kind == "figure":
                url = str(block.get("asset_url") or "").strip()
                if url:
                    parts.append(_markdown_image(
                        url, str(block.get("caption") or "")
                    ))
            elif kind == "heading":
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
                parts.append(_markdown_heading(
                    1, _canonical_task_heading(block.get("source_label"))
                ))
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
    r"^#{1,6}\s+(?:activity|discuss|project|write\s+in\s+brief|"
    r"think\s+about\s+it|let['’]?s\s+discuss|questions?|exercises?)"
    r"\b[\s:—-]*",
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
        return structure.normalize_task_table_markup(raw)
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
            task["source_kind"] = (
                "activity" if activity else (
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
    relationship_count = apply_page_acsd_relationships(canonical, page_acsd)
    canonical, report, issues = phase22._recalculate_after_adjudication(
        canonical, report
    )
    if issues or not canonical.get("phase2_inventory_ready"):
        raise ValueError(
            "rehydrated GPT page ACSD did not pass deterministic source gates"
        )
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
        relationship_count = apply_page_acsd_relationships(canonical, page_acsd)
        canonical, report, issues = phase22._recalculate_after_adjudication(
            canonical, report
        )
        if issues or not canonical.get("phase2_inventory_ready"):
            codes = ", ".join(
                str(issue.get("code") or "unknown")
                for issue in issues[:8]
                if isinstance(issue, dict)
            )
            raise ValueError(
                "verified GPT page extraction did not pass the deterministic "
                "canonical-source gate" + (f": {codes}" if codes else "")
            )
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

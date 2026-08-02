"""Phase 2.2 evidence-backed adjudication for unresolved canonical-source gaps.

The deterministic ACSD compiler remains authoritative for order, source spans,
Figure URLs, mathematics, and task identity.  This module is invoked only when
that compiler has already found a small, explicit set of unresolved source
issues.  It sends bounded evidence packets and a few relevant pages from the
*original uploaded document* to OpenAI, verifies the returned transcription a
second time, and applies only source-visible overlays to the canonical JSON.

The immutable raw MMD is never edited.  Every accepted repair retains PDF-page,
issue, model, cache, and verification provenance, then runs the complete
Phase-2/2.1 deterministic gate again before Build Concepts may continue.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from .. import config
from . import canonical_source
from . import canonical_source_phase2 as phase2
from . import canonical_source_phase21 as phase21
from . import canonical_source_phase21_structure as structure
from . import canonical_source_phase21_visuals as visuals
from . import katex_rules as kr
from . import progress

ADJUDICATION_VERSION = "2.2.1"
ADJUDICATION_COMPILER = "phase-2.2.1-evidence-adjudication-1"
ADJUDICATION_PHASE = "phase-2.2-source-adjudicated"
ELIGIBLE_ISSUE_CODES = frozenset({
    "phase21_missing_numbered_parent_section",
    "phase21_numbered_section_gap",
    "phase21_orphan_task_figure",
})

_MIN_CONFIDENCE = float(
    os.environ.get("AEGIS_SOURCE_ADJUDICATION_MIN_CONFIDENCE", "0.96")
)
_NO_TEXT_LAYER_MIN_CONFIDENCE = float(
    os.environ.get("AEGIS_SOURCE_ADJUDICATION_NO_TEXT_MIN_CONFIDENCE", "0.985")
)
_MAX_PAGES = max(
    1, int(os.environ.get("AEGIS_SOURCE_ADJUDICATION_MAX_PAGES", "3"))
)
_MAX_OUTPUT_TOKENS = max(
    1000, int(os.environ.get("AEGIS_SOURCE_ADJUDICATION_MAX_OUTPUT_TOKENS", "6000"))
)
_CACHE_DIR = config.DATA_DIR / "source-adjudication-cache"

_SPACE_RE = re.compile(r"\s+")
_NON_WORD_RE = re.compile(r"[^0-9a-z]+")
_MATHPIX_PAGE_RE = re.compile(r"-(?P<page>\d{1,4})\.jpg(?:\?|$)", re.IGNORECASE)
_SOURCE_LABELS = {
    "activity": "Activity",
    "discuss": "Discuss",
    "project": "Project",
    "write in brief": "Write in brief",
    "think about it": "Think about it",
    "let's discuss": "Let's discuss",
}


@dataclass(frozen=True)
class EvidencePage:
    evidence_id: str
    page_number: int  # human-facing, 1-based; never selected by the model
    text: str
    image_data_url: str
    score: float


DecisionProvider = Callable[[dict[str, Any], list[EvidencePage]], dict[str, Any]]


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: object) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _normal(value: object) -> str:
    text = str(value or "").replace("\u00ad", "")
    return _SPACE_RE.sub(" ", text).strip().casefold()


def _comparison_key(value: object) -> str:
    return _NON_WORD_RE.sub("", _normal(value))


def _compact(value: object, limit: int = 420) -> str:
    text = _SPACE_RE.sub(" ", str(value or "")).strip()
    if len(text) > limit:
        return text[: max(0, limit - 1)].rstrip() + "…"
    return text


def _json_clone(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


def _block_map(canonical: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(block.get("block_id") or ""): block
        for block in canonical.get("blocks") or []
        if isinstance(block, dict) and block.get("block_id")
    }


def _figure_map(canonical: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(figure.get("figure_id") or ""): figure
        for figure in canonical.get("figures") or []
        if isinstance(figure, dict) and figure.get("figure_id")
    }


def _image_map(canonical: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(image.get("image_id") or ""): image
        for image in canonical.get("images") or []
        if isinstance(image, dict) and image.get("image_id")
    }


def _nearby_blocks(
    canonical: dict[str, Any],
    block_id: str,
    *,
    before: int = 8,
    after: int = 3,
) -> list[dict[str, Any]]:
    blocks = [block for block in canonical.get("blocks") or [] if isinstance(block, dict)]
    index = next(
        (i for i, block in enumerate(blocks) if block.get("block_id") == block_id),
        None,
    )
    if index is None:
        return []
    return blocks[max(0, index - before): min(len(blocks), index + after + 1)]


def _packet_fingerprint(packet: dict[str, Any]) -> str:
    stable = {
        key: value
        for key, value in packet.items()
        if key not in {"issue_id", "cache_key", "candidate_pages"}
    }
    return _sha256_text(json.dumps(stable, ensure_ascii=False, sort_keys=True))


def _heading_packet(
    canonical: dict[str, Any],
    issues: list[dict[str, Any]],
    section_number: int,
) -> dict[str, Any] | None:
    matching = [
        issue for issue in issues
        if int(issue.get("section_number") or -1) == section_number
        and issue.get("code") in {
            "phase21_missing_numbered_parent_section",
            "phase21_numbered_section_gap",
        }
    ]
    subsection_ids = [
        str(block_id)
        for issue in matching
        for block_id in issue.get("subsection_block_ids") or []
        if block_id
    ]
    blocks_by_id = _block_map(canonical)
    subsection_blocks = [blocks_by_id[value] for value in subsection_ids if value in blocks_by_id]
    if not subsection_blocks:
        return None
    first = min(subsection_blocks, key=lambda block: int(block.get("source_start") or 0))
    nearby = _nearby_blocks(canonical, str(first.get("block_id") or ""), before=10, after=2)
    allowed = [
        str(block.get("block_id") or "")
        for block in nearby
        if block.get("block_id")
        and block.get("kind") not in {"layout"}
    ]
    evidence = [
        {
            "block_id": block.get("block_id"),
            "kind": block.get("kind"),
            "source_start": int(block.get("source_start") or 0),
            "text": _compact(block.get("raw_text") or block.get("display_text") or ""),
        }
        for block in nearby
        if block.get("kind") != "layout"
    ]
    search_terms = [
        str((block.get("heading") or {}).get("title") or "").strip()
        for block in subsection_blocks[:3]
    ]
    packet = {
        "issue_type": "missing_parent_section",
        "issue_codes": sorted({str(issue.get("code") or "") for issue in matching}),
        "section_number": section_number,
        "source_start": min(int(block.get("source_start") or 0) for block in subsection_blocks),
        "allowed_insert_before_block_ids": allowed,
        "nearby_blocks": evidence,
        "search_terms": [value for value in search_terms if value],
        "expected_output": {
            "recovered_text": "exact numbered parent heading visible in the PDF",
            "insert_before_block_id": "one allowed block id",
        },
    }
    packet["fingerprint"] = _packet_fingerprint(packet)
    packet["issue_id"] = f"ADJ-SECTION-{section_number}-{packet['fingerprint'][:10]}"
    return packet


def _orphan_figure_packet(
    canonical: dict[str, Any],
    issue: dict[str, Any],
) -> dict[str, Any] | None:
    figure_id = str(issue.get("figure_id") or "").strip()
    block_id = str(issue.get("block_id") or "").strip()
    figure = _figure_map(canonical).get(figure_id)
    if not figure or not block_id:
        return None
    nearby = _nearby_blocks(canonical, block_id, before=6, after=4)
    allowed = [
        str(block.get("block_id") or "")
        for block in nearby
        if block.get("block_id")
        and int(block.get("source_start") or 0)
        <= int(figure.get("source_start") or 0)
    ]
    caption = str(figure.get("caption_raw") or issue.get("caption") or "").strip()
    evidence = [
        {
            "block_id": block.get("block_id"),
            "kind": block.get("kind"),
            "source_start": int(block.get("source_start") or 0),
            "text": _compact(block.get("raw_text") or block.get("display_text") or ""),
        }
        for block in nearby
        if block.get("kind") != "layout"
    ]
    packet = {
        "issue_type": "orphan_figure_task",
        "issue_codes": [str(issue.get("code") or "")],
        "figure_id": figure_id,
        "figure_caption": caption,
        "figure_image_urls": list(figure.get("image_urls") or []),
        "source_start": int(figure.get("source_start") or 0),
        "allowed_insert_before_block_ids": allowed,
        "nearby_blocks": evidence,
        "search_terms": [caption.split(".", 1)[0], "Club of Thinkers"],
        "expected_output": {
            "recovered_text": "exact source task visibly printed with the Figure",
            "source_label": "visible task cue such as Discuss or Activity",
            "insert_before_block_id": "one allowed block id",
        },
    }
    packet["fingerprint"] = _packet_fingerprint(packet)
    packet["issue_id"] = f"ADJ-FIGURE-{figure_id}-{packet['fingerprint'][:10]}"
    return packet


def build_issue_packets(
    canonical: dict[str, Any],
    issues: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    issue_list = [copy.deepcopy(issue) for issue in issues if isinstance(issue, dict)]
    packets: list[dict[str, Any]] = []
    section_numbers = sorted({
        int(issue.get("section_number"))
        for issue in issue_list
        if issue.get("code") in {
            "phase21_missing_numbered_parent_section",
            "phase21_numbered_section_gap",
        }
        and str(issue.get("section_number") or "").isdigit()
    })
    for number in section_numbers:
        packet = _heading_packet(canonical, issue_list, number)
        if packet:
            packets.append(packet)
    for issue in issue_list:
        if issue.get("code") != "phase21_orphan_task_figure":
            continue
        packet = _orphan_figure_packet(canonical, issue)
        if packet:
            packets.append(packet)
    packets.sort(key=lambda packet: (int(packet.get("source_start") or 0), packet["issue_id"]))
    return packets


def annotate_pending_adjudication(
    canonical: dict[str, Any],
    report: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    canonical = copy.deepcopy(canonical)
    report = copy.deepcopy(report)
    issues = phase2.phase2_inventory_issues(canonical, report)
    eligible = [issue for issue in issues if issue.get("code") in ELIGIBLE_ISSUE_CODES]
    packets = build_issue_packets(canonical, eligible)
    previous = canonical.get("source_adjudication")
    if isinstance(previous, dict) and previous.get("version") == ADJUDICATION_VERSION:
        decisions = copy.deepcopy(previous.get("decisions") or [])
    else:
        decisions = []
    status = "not_required" if not packets else "pending"
    marker = {
        "version": ADJUDICATION_VERSION,
        "compiler": ADJUDICATION_COMPILER,
        "status": status,
        "eligible_issue_count": len(eligible),
        "packet_count": len(packets),
        "packets": packets,
        "decisions": decisions,
        "raw_mmd_changed": False,
    }
    canonical["source_adjudication"] = marker
    canonical.setdefault("source_contract", {})["adjudication_version"] = ADJUDICATION_VERSION
    report["source_adjudication"] = copy.deepcopy(marker)
    report.setdefault("summary", {})["source_adjudication_packets"] = len(packets)
    return canonical, report


def _pdf_sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _page_hint_from_urls(packet: dict[str, Any]) -> list[int]:
    pages: list[int] = []
    for url in packet.get("figure_image_urls") or []:
        match = _MATHPIX_PAGE_RE.search(str(url))
        if not match:
            continue
        value = int(match.group("page"))
        if value > 0 and value not in pages:
            pages.append(value)
    return pages


def _candidate_page_numbers(
    page_texts: list[str],
    packet: dict[str, Any],
    *,
    source_chars: int,
) -> list[tuple[int, float]]:
    terms = [
        _normal(term)
        for term in packet.get("search_terms") or []
        if len(_normal(term)) >= 8
    ]
    scored: dict[int, float] = {}
    for index, page_text in enumerate(page_texts):
        normalized = _normal(page_text)
        score = 0.0
        for term in terms:
            if term and term in normalized:
                score += min(8.0, 2.0 + len(term) / 60.0)
            elif term:
                words = [word for word in term.split() if len(word) >= 5]
                score += min(2.0, sum(0.25 for word in words if word in normalized))
        if score:
            scored[index] = score

    page_count = max(1, len(page_texts))
    position = max(0, int(packet.get("source_start") or 0))
    ratio = position / max(1, source_chars)
    approximate = min(page_count - 1, max(0, round(ratio * (page_count - 1))))
    for index in range(max(0, approximate - 1), min(page_count, approximate + 2)):
        scored[index] = scored.get(index, 0.0) + (1.5 if index == approximate else 0.75)

    for human_page in _page_hint_from_urls(packet):
        for index in {human_page - 1, human_page - 2, human_page}:
            if 0 <= index < page_count:
                scored[index] = scored.get(index, 0.0) + 2.0

    if not scored:
        scored[approximate] = 1.0
    ranked = sorted(scored.items(), key=lambda pair: (-pair[1], pair[0]))
    selected = ranked[:_MAX_PAGES]
    return selected


def _evidence_id(index: int) -> str:
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    if 0 <= index < len(alphabet):
        return f"EVIDENCE-PAGE-{alphabet[index]}"
    return f"EVIDENCE-PAGE-{index + 1:02d}"


def collect_evidence_pages(
    path: Path,
    packet: dict[str, Any],
    *,
    source_chars: int,
) -> list[EvidencePage]:
    """Return bounded original-document pages with opaque model-facing IDs."""
    import fitz

    document = fitz.open(path)
    try:
        page_texts = [page.get_text("text") or "" for page in document]
        ranked = _candidate_page_numbers(page_texts, packet, source_chars=source_chars)
        evidence: list[EvidencePage] = []
        for evidence_index, (page_index, score) in enumerate(ranked):
            page = document[page_index]
            matrix = fitz.Matrix(2.0, 2.0)
            pixmap = page.get_pixmap(matrix=matrix, alpha=False)
            data = pixmap.tobytes("jpeg", jpg_quality=88)
            evidence.append(EvidencePage(
                evidence_id=_evidence_id(evidence_index),
                page_number=page_index + 1,
                text=page_texts[page_index],
                image_data_url=(
                    "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")
                ),
                score=float(score),
            ))
        return evidence
    finally:
        document.close()

def _system_prompt(*, verification: bool) -> str:
    role = "verification reviewer" if verification else "source transcription adjudicator"
    verdict_rule = (
        'Return verdict "matches_exactly", "does_not_match", or "ambiguous".'
        if verification
        else 'Return verdict "visible_exact", "not_visible", or "ambiguous".'
    )
    addressing_rule = (
        "Do not choose a page or insertion anchor; verify only the fixed candidate."
        if verification
        else "Choose evidence_id and insert_before_block_id only from supplied allowed IDs."
    )
    return f"""
You are the Aegis {role}. The supplied original textbook page images are the
only authority. MMD excerpts are diagnostic context and may be incomplete.

Rules:
1. Never reconstruct, paraphrase, complete, or infer missing textbook wording.
2. Return text only when it is visibly printed on one supplied page.
3. Preserve wording, spelling, punctuation, numbering, and order exactly.
4. {addressing_rule}
5. A Figure caption is not evidence that an unseen task exists.
6. {verdict_rule}
7. Output one JSON object only, with no markdown.
""".strip()


def _extraction_schema(packet: dict[str, Any], pages: list[EvidencePage]) -> dict[str, Any]:
    evidence_ids = [page.evidence_id for page in pages]
    anchors = [
        str(value) for value in packet.get("allowed_insert_before_block_ids") or []
        if str(value)
    ]
    return {
        "name": "aegis_source_adjudication_extract",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "verdict": {
                    "type": "string",
                    "enum": ["visible_exact", "not_visible", "ambiguous"],
                },
                "evidence_id": {
                    "type": "string",
                    "enum": evidence_ids or ["NO-EVIDENCE-PAGE"],
                },
                "recovered_text": {"type": "string"},
                "source_label": {"type": "string", "maxLength": 120},
                "insert_before_block_id": {
                    "type": "string",
                    "enum": anchors or ["NO-INSERTION-ANCHOR"],
                },
                "confidence": {"type": "number"},
                "evidence": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": [
                "verdict", "evidence_id", "recovered_text", "source_label",
                "insert_before_block_id", "confidence", "evidence",
            ],
            "additionalProperties": False,
        },
    }


def _verification_schema() -> dict[str, Any]:
    return {
        "name": "aegis_source_adjudication_verify",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "verdict": {
                    "type": "string",
                    "enum": ["matches_exactly", "does_not_match", "ambiguous"],
                },
                "recovered_text": {"type": "string"},
                "source_label_matches": {"type": "boolean"},
                "confidence": {"type": "number"},
                "evidence": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": [
                "verdict", "recovered_text", "source_label_matches",
                "confidence", "evidence",
            ],
            "additionalProperties": False,
        },
    }


def _packet_prompt(
    packet: dict[str, Any],
    pages: list[EvidencePage],
    *,
    verification_candidate: dict[str, Any] | None = None,
    protocol_retry: str = "",
) -> str:
    page_ledger = [
        {
            "evidence_id": page.evidence_id,
            "pdf_page_number_for_audit_only": page.page_number,
        }
        for page in pages
    ]
    body: dict[str, Any] = {
        "issue_packet": packet,
        "evidence_page_ledger": page_ledger,
    }
    if verification_candidate is not None:
        body["candidate_to_verify"] = {
            "recovered_text": verification_candidate.get("recovered_text") or "",
            "source_label": verification_candidate.get("source_label") or "",
        }
        body["instruction"] = (
            "Independently verify the exact candidate transcription on this single "
            "supplied page. Do not choose a page or insertion anchor."
        )
    else:
        body["instruction"] = (
            "Transcribe only the exact missing heading or task if visibly present. "
            "Select evidence_id only from the supplied opaque evidence IDs and select "
            "insert_before_block_id only from the packet's allowed list."
        )
    if protocol_retry:
        body["protocol_retry"] = protocol_retry
    return json.dumps(body, ensure_ascii=False, indent=2)

def _openai_multimodal_json(
    *,
    system: str,
    prompt: str,
    pages: list[EvidencePage],
    response_schema: dict[str, Any],
    purpose: str = "source_adjudication",
    max_tokens: int = _MAX_OUTPUT_TOKENS,
    single_attempt: bool = False,
    model: str | None = None,
) -> dict[str, Any]:
    """One bounded strict-schema multimodal call using Aegis controls."""
    from openai import (
        APIConnectionError,
        APITimeoutError,
        InternalServerError,
        OpenAI,
        RateLimitError,
    )
    from aegis_pipeline.openai_policy import chat_request_policy
    from . import generation, openai_usage

    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for page in pages:
        extracted = _compact(page.text, limit=6000)
        content.append({
            "type": "text",
            "text": (
                f"{page.evidence_id} (audit PDF page {page.page_number})\n"
                f"Extracted text layer:\n{extracted}"
            ),
        })
        content.append({
            "type": "image_url",
            "image_url": {"url": page.image_data_url, "detail": "high"},
        })

    transient_errors = (
        RateLimitError,
        APIConnectionError,
        APITimeoutError,
        InternalServerError,
    )
    selected_model = str(model or config.OPENAI_MODEL)
    request_policy = chat_request_policy(purpose, model=selected_model)
    client = OpenAI(timeout=config.OPENAI_REQUEST_TIMEOUT_SECONDS, max_retries=0)
    gate = generation._get_openai_gate()
    transient = 0
    hard = 0
    last_error: Exception | None = None
    while True:
        try:
            generation._acquire_openai_slot(gate, purpose=purpose)
            try:
                response = client.chat.completions.create(
                    **request_policy,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": content},
                    ],
                    response_format={
                        "type": "json_schema",
                        "json_schema": response_schema,
                    },
                    max_completion_tokens=max_tokens,
                )
            finally:
                gate.release()
            try:
                openai_usage.record_response(
                    response, requested_model=request_policy["model"]
                )
            except Exception:
                pass
            choice = response.choices[0]
            if getattr(choice, "finish_reason", None) == "length":
                raise RuntimeError("source adjudication response was truncated")
            value = json.loads(choice.message.content or "{}")
            if not isinstance(value, dict):
                raise ValueError("source adjudication response must be an object")
            return value
        except generation.OpenAIQueueTimeoutError:
            raise
        except transient_errors as exc:
            code = generation._openai_error_code(exc)
            if code == "insufficient_quota":
                raise RuntimeError("OpenAI quota exhausted during source adjudication") from exc
            if single_attempt:
                raise RuntimeError(
                    "OpenAI source adjudication single attempt failed: "
                    f"{exc!r}"
                ) from exc
            transient += 1
            last_error = exc
            if transient > config.OPENAI_TRANSIENT_RETRIES:
                raise RuntimeError(
                    "OpenAI unavailable during source adjudication after "
                    f"{transient - 1} transient retries: {exc!r}"
                ) from exc
            delay = generation._transient_backoff(exc, transient)
            progress.log(
                "OpenAI source adjudication busy "
                f"({type(exc).__name__}); retrying in {delay:.0f}s.",
                level="warning",
            )
            time.sleep(delay)
        except Exception as exc:
            if single_attempt:
                raise RuntimeError(
                    "OpenAI source adjudication single attempt failed: "
                    f"{exc!r}"
                ) from exc
            hard += 1
            last_error = exc
            if hard >= 3:
                raise RuntimeError(
                    f"OpenAI source adjudication failed: {last_error!r}"
                ) from exc
            time.sleep(2)

def _page_by_number(pages: list[EvidencePage], page_number: int) -> EvidencePage | None:
    return next((page for page in pages if page.page_number == page_number), None)


def _page_by_evidence_id(pages: list[EvidencePage], evidence_id: str) -> EvidencePage | None:
    return next((page for page in pages if page.evidence_id == evidence_id), None)


def _confidence(value: object) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _normalize_source_label(value: object, *, issue_type: str) -> str:
    visible = re.sub(r"\s+", " ", str(value or "")).strip()[:120]
    raw = _normal(visible)
    if raw in _SOURCE_LABELS:
        return _SOURCE_LABELS[raw]
    if issue_type == "orphan_figure_task":
        return visible or "Task"
    return ""


def _text_layer_contains(page: EvidencePage, recovered: str) -> bool:
    page_key = _comparison_key(page.text)
    recovered_key = _comparison_key(recovered)
    return bool(recovered_key and len(recovered_key) >= 8 and recovered_key in page_key)


def _validated_candidate(
    packet: dict[str, Any],
    candidate: dict[str, Any],
    pages: list[EvidencePage],
) -> tuple[dict[str, Any] | None, str, bool]:
    """Return candidate, reason, and whether the rejection is protocol-only."""
    verdict = str(candidate.get("verdict") or "").strip().lower()
    if verdict != "visible_exact":
        return None, f"extractor verdict was {verdict or 'missing'}", False
    recovered = str(candidate.get("recovered_text") or "").strip()
    if not recovered:
        return None, "extractor returned no source text", False
    evidence_id = str(candidate.get("evidence_id") or "").strip()
    page = _page_by_evidence_id(pages, evidence_id)
    if page is None:
        allowed = ", ".join(page.evidence_id for page in pages) or "(none)"
        return (
            None,
            f"extractor selected unsupported evidence_id={evidence_id!r}; allowed: {allowed}",
            True,
        )
    anchor = str(candidate.get("insert_before_block_id") or "").strip()
    allowed_anchors = set(packet.get("allowed_insert_before_block_ids") or [])
    if anchor not in allowed_anchors:
        return (
            None,
            f"extractor selected unsupported insertion anchor {anchor!r}",
            True,
        )
    confidence = _confidence(candidate.get("confidence"))
    if confidence < _MIN_CONFIDENCE:
        return None, f"extractor confidence {confidence:.3f} is below threshold", False

    issue_type = str(packet.get("issue_type") or "")
    if issue_type == "missing_parent_section":
        number = int(packet.get("section_number") or 0)
        if not re.match(rf"^\s*{number}(?:\s|[.)-])", recovered):
            return None, "recovered heading does not preserve the expected section number", False
    elif issue_type == "orphan_figure_task":
        if not structure.is_task_like(recovered):
            return None, "recovered text is not a question or instructional task", False
        caption_key = _comparison_key(packet.get("figure_caption") or "")
        recovered_key = _comparison_key(recovered)
        if recovered_key and recovered_key == caption_key:
            return None, "recovered text merely repeats the Figure caption", False
    else:
        return None, "unsupported adjudication packet", False

    normalized = copy.deepcopy(candidate)
    normalized.update({
        "verdict": "visible_exact",
        "evidence_id": evidence_id,
        "page_number": page.page_number,
        "recovered_text": recovered,
        "source_label": _normalize_source_label(
            candidate.get("source_label"), issue_type=issue_type
        ),
        "insert_before_block_id": anchor,
        "confidence": confidence,
        "text_layer_match": _text_layer_contains(page, recovered),
    })
    return normalized, "", False


def _verify_candidate(
    packet: dict[str, Any],
    candidate: dict[str, Any],
    pages: list[EvidencePage],
) -> tuple[dict[str, Any] | None, str]:
    page = _page_by_evidence_id(pages, str(candidate.get("evidence_id") or ""))
    if page is None:
        return None, "candidate evidence page is unavailable"
    verification = _openai_multimodal_json(
        system=_system_prompt(verification=True),
        prompt=_packet_prompt(
            packet,
            [page],
            verification_candidate=candidate,
        ),
        pages=[page],
        response_schema=_verification_schema(),
    )
    verdict = str(verification.get("verdict") or "").strip().lower()
    if verdict != "matches_exactly":
        return None, f"verification verdict was {verdict or 'missing'}"
    if not bool(verification.get("source_label_matches")):
        return None, "verification rejected the visible source label"
    verified_text = str(verification.get("recovered_text") or "").strip()
    if _comparison_key(verified_text) != _comparison_key(candidate["recovered_text"]):
        return None, "extractor and verifier transcriptions disagree"
    verified_confidence = _confidence(verification.get("confidence"))
    text_layer = bool(candidate.get("text_layer_match"))
    minimum = _MIN_CONFIDENCE if text_layer else _NO_TEXT_LAYER_MIN_CONFIDENCE
    if min(candidate["confidence"], verified_confidence) < minimum:
        return None, "verified confidence is below the evidence threshold"
    accepted = copy.deepcopy(candidate)
    accepted["verification"] = {
        "confidence": verified_confidence,
        "page_number": page.page_number,
        "evidence_id": page.evidence_id,
        "text_layer_match": text_layer,
        "transcription_key": _comparison_key(verified_text),
    }
    return accepted, ""


def adjudicate_packet_via_openai(
    packet: dict[str, Any],
    pages: list[EvidencePage],
) -> dict[str, Any]:
    if not pages:
        return {
            "status": "review_required",
            "reason": "no original-document pages could be selected",
        }
    allowed = ", ".join(
        f"{page.evidence_id}=PDF page {page.page_number}" for page in pages
    )
    progress.log(f"Source adjudication evidence ledger: {allowed}.")
    retry_note = ""
    candidate: dict[str, Any] = {}
    for attempt in range(1, 3):
        candidate = _openai_multimodal_json(
            system=_system_prompt(verification=False),
            prompt=_packet_prompt(
                packet,
                pages,
                protocol_retry=retry_note,
            ),
            pages=pages,
            response_schema=_extraction_schema(packet, pages),
        )
        validated, reason, protocol_error = _validated_candidate(
            packet, candidate, pages
        )
        if validated is not None:
            accepted, verify_reason = _verify_candidate(packet, validated, pages)
            if accepted is None:
                return {
                    "status": "review_required",
                    "reason": verify_reason,
                    "extractor": validated,
                }
            return {"status": "verified", "decision": accepted}
        if protocol_error and attempt == 1:
            retry_note = (
                f"The previous response violated the addressing protocol: {reason}. "
                f"Use exactly one evidence_id from {[p.evidence_id for p in pages]} "
                f"and one insertion anchor from "
                f"{packet.get('allowed_insert_before_block_ids') or []}."
            )
            progress.log(
                "Source adjudicator protocol rejection; retrying once with the "
                "strict evidence/anchor contract.",
                level="warning",
            )
            continue
        return {
            "status": "review_required",
            "reason": reason,
            "extractor": candidate,
            "protocol_error": protocol_error,
        }
    return {
        "status": "review_required",
        "reason": "source adjudicator exhausted the protocol retry",
        "extractor": candidate,
        "protocol_error": True,
    }

def _cache_path(cache_key: str) -> Path:
    return _CACHE_DIR / f"{cache_key}.json"


def _cache_key(
    *,
    canonical: dict[str, Any],
    source_path: Path,
    packet: dict[str, Any],
) -> str:
    source_sha = str(
        (canonical.get("source_contract") or {}).get("source_sha256")
        or (canonical.get("document") or {}).get("source_sha256")
        or ""
    )
    material = "\u241f".join([
        ADJUDICATION_VERSION,
        config.OPENAI_MODEL,
        source_sha,
        _pdf_sha256(source_path),
        str(packet.get("fingerprint") or ""),
    ])
    return _sha256_text(material)


def _read_cache(cache_key: str) -> dict[str, Any] | None:
    path = _cache_path(cache_key)
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _write_cache(cache_key: str, value: dict[str, Any]) -> None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    canonical_source._atomic_write(
        _cache_path(cache_key), canonical_source._json_text(value)
    )


def _record_overlay(
    canonical: dict[str, Any],
    *,
    offset: int,
    text: str,
    repair_id: str,
    kind: str,
) -> None:
    overlays = canonical.setdefault("source_overlays", [])
    if not isinstance(overlays, list):
        overlays = []
        canonical["source_overlays"] = overlays
    entry = {
        "offset": int(offset),
        "text": str(text),
        "repair_id": repair_id,
        "kind": kind,
        "text_sha256": _sha256_text(text),
    }
    if not any(
        isinstance(item, dict)
        and item.get("repair_id") == repair_id
        for item in overlays
    ):
        overlays.append(entry)


def _heading_title(recovered: str, section_number: int) -> str:
    return re.sub(
        rf"^\s*{section_number}(?:\s+|[.)-]\s*)",
        "",
        recovered,
        count=1,
    ).strip()


def _section_sort_key(section: dict[str, Any]) -> tuple[int, int, str]:
    return (
        int(section.get("source_start") or 0),
        0 if section.get("adjudicated_heading") else 1,
        str(section.get("section_id") or ""),
    )


def _refresh_sections(canonical: dict[str, Any]) -> None:
    sections = [
        section for section in canonical.get("sections") or []
        if isinstance(section, dict)
    ]
    sections.sort(key=_section_sort_key)
    for order, section in enumerate(sections, start=1):
        section["order"] = order
    canonical["sections"] = sections
    canonical["section_sequence"] = [str(section.get("section_id") or "") for section in sections]
    canonical.setdefault("source_contract", {})["section_sequence"] = list(
        canonical["section_sequence"]
    )
    if isinstance(canonical.get("statistics"), dict):
        canonical["statistics"]["sections"] = len(sections)


def _refresh_task_topics(canonical: dict[str, Any]) -> None:
    for task in canonical.get("tasks") or []:
        if not isinstance(task, dict) or task.get("chapter_wide"):
            continue
        topic = structure.topic_for_position(
            canonical, int(task.get("source_start") or 0)
        )
        if topic:
            task["topic_hint"] = topic


def apply_missing_heading(
    canonical: dict[str, Any],
    packet: dict[str, Any],
    decision: dict[str, Any],
    provenance: dict[str, Any],
) -> None:
    number = int(packet.get("section_number") or 0)
    recovered = str(decision.get("recovered_text") or "").strip()
    anchor_id = str(decision.get("insert_before_block_id") or "")
    blocks_by_id = _block_map(canonical)
    anchor = blocks_by_id.get(anchor_id)
    if not anchor:
        raise ValueError("adjudicated heading anchor is not present in ACSD")
    anchor_start = int(anchor.get("source_start") or 0)
    title = _heading_title(recovered, number)
    if not title:
        raise ValueError("adjudicated heading has no title")

    mains, subsections = structure.numbered_heading_inventory(canonical)
    subsection_blocks = subsections.get(number) or []
    if not subsection_blocks:
        raise ValueError("adjudicated heading has no matching numbered subsections")
    first_sub_start = min(int(block.get("source_start") or 0) for block in subsection_blocks)
    next_main_start = min(
        (
            int(block.get("source_start") or 0)
            for major, block in mains.items()
            if major > number
        ),
        default=max(
            [int(block.get("source_end") or 0) for block in canonical.get("blocks") or []]
            or [anchor_start]
        ),
    )
    sections = [section for section in canonical.get("sections") or [] if isinstance(section, dict)]
    owner = next(
        (
            section for section in sections
            if anchor_id in (section.get("block_ids") or [])
        ),
        None,
    )
    if owner is None:
        raise ValueError("adjudicated heading anchor has no source section owner")
    moved_ids = [
        str(block_id)
        for block_id in owner.get("block_ids") or []
        if int(blocks_by_id.get(str(block_id), {}).get("source_start") or -1) >= anchor_start
    ]
    owner["block_ids"] = [
        block_id for block_id in owner.get("block_ids") or [] if str(block_id) not in set(moved_ids)
    ]
    owner["source_end"] = anchor_start

    virtual_id = f"SEC-ADJ-{number:04d}"
    existing = next((section for section in sections if section.get("section_id") == virtual_id), None)
    virtual = existing or {
        "section_id": virtual_id,
        "block_ids": [],
        "level": 1,
        "depth": 1,
        "parent_section_id": "",
        "heading_kind": "adjudicated_pdf",
    }
    virtual.update({
        "title": title,
        "source_start": anchor_start,
        "source_end": first_sub_start,
        "block_ids": moved_ids,
        "adjudicated_heading": {
            "section_number": number,
            "raw_text": recovered,
            "title": title,
            "repair_id": provenance["repair_id"],
            "page_number": provenance["page_number"],
        },
    })
    if existing is None:
        sections.append(virtual)
    for index, block_id in enumerate(moved_ids, start=1):
        block = blocks_by_id.get(block_id)
        if block is not None:
            block["section_id"] = virtual_id
            block["section_order"] = index

    for section in sections:
        if section.get("section_id") in {virtual_id, owner.get("section_id")}:
            continue
        start = int(section.get("source_start") or 0)
        if anchor_start <= start < next_main_start:
            section["parent_section_id"] = virtual_id
            section["depth"] = max(2, int(section.get("depth") or 1))

    canonical["sections"] = sections
    canonical.setdefault("adjudicated_main_sections", {})[str(number)] = virtual_id
    _record_overlay(
        canonical,
        offset=anchor_start,
        text=f"\\section*{{{recovered}}}\n\n",
        repair_id=provenance["repair_id"],
        kind="missing_parent_heading",
    )
    _refresh_sections(canonical)
    _refresh_task_topics(canonical)


def _figure_payload(
    canonical: dict[str, Any], figure_id: str
) -> tuple[list[str], dict[str, str]]:
    figure = _figure_map(canonical).get(figure_id, {})
    images = _image_map(canonical)
    urls: list[str] = []
    captions: dict[str, str] = {}
    caption = str(figure.get("caption_raw") or "")
    for image_id in figure.get("image_ids") or []:
        image = images.get(str(image_id), {})
        url = str(image.get("url") or "").strip()
        if not url:
            continue
        if url not in urls:
            urls.append(url)
        captions[url] = caption or str(image.get("alt_raw") or "")
    return urls, captions


def _remove_figure_from_task(
    task: dict[str, Any],
    *,
    figure_id: str,
    urls: list[str],
    figure_start: int,
) -> None:
    for field in ("figure_refs", "display_figure_refs", "raw_figure_refs"):
        task[field] = [value for value in task.get(field) or [] if str(value) != figure_id]
    for field in ("image_urls", "display_image_urls", "raw_image_urls"):
        task[field] = [value for value in task.get(field) or [] if str(value) not in set(urls)]
    for field in ("_image_captions", "display_image_captions", "raw_image_captions"):
        captions = task.get(field)
        if isinstance(captions, dict):
            task[field] = {url: caption for url, caption in captions.items() if url not in set(urls)}
    task["requires_visual"] = bool(
        task.get("display_image_urls")
        or task.get("display_figure_reference_ids")
        or task.get("figure_refs")
    )
    task["source_end"] = min(int(task.get("source_end") or figure_start), figure_start)


def apply_orphan_figure_task(
    canonical: dict[str, Any],
    packet: dict[str, Any],
    decision: dict[str, Any],
    provenance: dict[str, Any],
) -> None:
    figure_id = str(packet.get("figure_id") or "")
    figure = _figure_map(canonical).get(figure_id)
    if not figure:
        raise ValueError("adjudicated Figure is not present in ACSD")
    anchor_id = str(decision.get("insert_before_block_id") or "")
    blocks_by_id = _block_map(canonical)
    anchor = blocks_by_id.get(anchor_id)
    figure_block = blocks_by_id.get(str(figure.get("block_id") or ""))
    if anchor is None or figure_block is None:
        raise ValueError("adjudicated task anchor is not present in ACSD")
    figure_start = int(figure.get("source_start") or 0)
    urls, captions = _figure_payload(canonical, figure_id)
    recovered = str(decision.get("recovered_text") or "").strip()
    label = str(decision.get("source_label") or "Discuss").strip() or "Discuss"

    prior_ids = [str(value) for value in figure_block.get("task_ids") or [] if value]
    if prior_ids:
        figure_block.setdefault("source_task_ids", prior_ids)
    figure_block["task_ids"] = []
    for task in canonical.get("tasks") or []:
        if not isinstance(task, dict):
            continue
        if (
            figure_id in {str(value) for value in task.get("figure_refs") or []}
            or str(task.get("task_id") or "") in prior_ids
            or int(task.get("source_end") or 0) > figure_start
            and int(task.get("source_start") or 0) < figure_start
        ):
            if not visuals.visuals_compatible(
                str(task.get("raw_prompt") or task.get("display_prompt") or ""),
                str(figure.get("caption_raw") or ""),
            ):
                _remove_figure_from_task(
                    task, figure_id=figure_id, urls=urls, figure_start=figure_start
                )

    tags = []
    for index, url in enumerate(urls):
        try:
            tags.append(kr.image(url, captions.get(url) or f"Source visual {index + 1}"))
        except ValueError:
            continue
    display = kr.canonicalize_rich_text(
        " ".join([recovered, *tags]).strip()
    )
    section_id = str(figure_block.get("section_id") or "")
    sections = {
        str(section.get("section_id") or ""): section
        for section in canonical.get("sections") or []
        if isinstance(section, dict)
    }
    section = sections.get(section_id, {})
    activity = _normal(label) in {"activity", "project"}
    task = {
        "task_id": f"TASK-ADJ-{provenance['repair_id'][-12:]}",
        "qid": "",
        "order": 0,
        "order_index": 0,
        "source_kind": "activity" if activity else "checkpoint_question",
        "source_label": label,
        "parent_source_label": label,
        "topic_hint": structure.topic_for_position(canonical, figure_start),
        "raw_prompt": recovered,
        "display_prompt": display,
        "identity_key": "",
        "section_id": section_id,
        "source_section_index": max(0, int(section.get("order") or 1) - 1),
        "source_position": max(0, figure_start - int(section.get("source_start") or 0)),
        "source_start": figure_start,
        "source_end": int(figure.get("source_end") or figure_start),
        "source_location_confidence": "phase22_verified_pdf_page",
        "chapter_wide": False,
        "activity_origin": activity,
        "requires_visual": bool(urls),
        "requires_context": False,
        "image_urls": urls,
        "raw_image_urls": urls,
        "display_image_urls": urls,
        "_image_captions": captions,
        "raw_image_captions": captions,
        "display_image_captions": captions,
        "figure_refs": [figure_id],
        "raw_figure_refs": [figure_id],
        "display_figure_refs": [figure_id],
        "explicit_figure_reference_ids": [],
        "raw_figure_reference_ids": [],
        "display_figure_reference_ids": [],
        "unresolved_figure_reference_ids": [],
        "ambiguous_figure_reference_ids": [],
        "display_overrides": [],
        "phase22_recovery": copy.deepcopy(provenance),
    }
    canonical.setdefault("tasks", []).append(task)
    _record_overlay(
        canonical,
        offset=int(anchor.get("source_start") or figure_start),
        text=f"\\section*{{{label}}}\n\n{recovered}\n\n",
        repair_id=provenance["repair_id"],
        kind="missing_figure_task",
    )
    structure.renumber_tasks(canonical)
    _refresh_task_topics(canonical)


def apply_verified_decision(
    canonical: dict[str, Any],
    packet: dict[str, Any],
    decision: dict[str, Any],
    *,
    cache_key: str,
    source_path: Path,
) -> dict[str, Any]:
    repair_id = f"REPAIR-{packet['fingerprint'][:16]}"
    provenance = {
        "repair_id": repair_id,
        "issue_id": packet["issue_id"],
        "issue_type": packet["issue_type"],
        "adjudication_version": ADJUDICATION_VERSION,
        "model": config.OPENAI_MODEL,
        "page_number": int(decision.get("page_number") or 0),
        "confidence": float(decision.get("confidence") or 0.0),
        "verification": copy.deepcopy(decision.get("verification") or {}),
        "cache_key": cache_key,
        "source_file_sha256": _pdf_sha256(source_path),
        "raw_mmd_changed": False,
        "recovered_text": str(decision.get("recovered_text") or ""),
    }
    if packet["issue_type"] == "missing_parent_section":
        apply_missing_heading(canonical, packet, decision, provenance)
    elif packet["issue_type"] == "orphan_figure_task":
        apply_orphan_figure_task(canonical, packet, decision, provenance)
    else:
        raise ValueError("unsupported verified source adjudication")
    return provenance


def semantic_source(canonical: dict[str, Any] | None, raw_source: str) -> str:
    if not isinstance(canonical, dict):
        return str(raw_source or "")
    overlays = [
        item for item in canonical.get("source_overlays") or []
        if isinstance(item, dict) and str(item.get("text") or "")
    ]
    if not overlays:
        return str(raw_source or "")
    raw = str(raw_source or "")
    semantic_hash = str(
        (canonical.get("source_adjudication") or {}).get("semantic_source_sha256")
        or ""
    )
    if semantic_hash and _sha256_text(raw) == semantic_hash:
        return raw
    ordered = sorted(
        overlays,
        key=lambda item: (int(item.get("offset") or 0), str(item.get("repair_id") or "")),
        reverse=True,
    )
    output = raw
    for overlay in ordered:
        offset = max(0, min(len(output), int(overlay.get("offset") or 0)))
        output = output[:offset] + str(overlay.get("text") or "") + output[offset:]
    return output


def active_semantic_source(raw_source: str) -> str:
    return semantic_source(phase2.active_canonical(), raw_source)


def _recalculate_after_adjudication(
    canonical: dict[str, Any],
    report: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    structure.renumber_tasks(canonical)
    _refresh_sections(canonical)
    _refresh_task_topics(canonical)
    # Adjudication can insert a parent task or refresh block ownership after
    # the original hardening pass.  Leaf Cases are a derived source contract,
    # so never trust their pre-adjudication shape: recover folded follow-ups
    # and materialize every leaf again from the now-final parent sequence.
    structure.recover_followup_task_prompts(canonical)
    inventory_items = structure.materialize_task_leaf_cases(canonical)
    parent_count, decomposed_count, inventory_items = structure.inventory_shape(
        canonical
    )
    marker = canonical.setdefault("phase21_hardening", {})
    marker.update({
        "version": phase21.HARDENING_VERSION,
        "compiler": phase21.COMPILER_LABEL,
        "followup_task_prompts_recovered": structure.followup_prompt_count(
            canonical
        ),
        "parent_task_count": parent_count,
        "decomposed_parent_task_count": decomposed_count,
        "inventory_item_count": inventory_items,
        "blocking_issues": 0,
    })
    source_contract = canonical.setdefault("source_contract", {})
    source_contract["hardening_version"] = phase21.HARDENING_VERSION
    source_contract["hardening_compiler"] = phase21.COMPILER_LABEL
    source_issues = phase21.source_boundary_issues(canonical)
    canonical["phase21_issues"] = source_issues
    marker["blocking_issues"] = len(source_issues)
    report["phase21_hardening"] = copy.deepcopy(marker)
    report["phase21_issues"] = copy.deepcopy(source_issues)
    issues = phase2.phase2_inventory_issues(canonical, report)
    ready = not issues
    canonical["phase2_inventory_ready"] = ready
    canonical["phase"] = ADJUDICATION_PHASE if ready else canonical.get("phase")
    source_contract["task_count"] = len(
        canonical.get("tasks") or []
    )
    source_contract["section_sequence"] = list(
        canonical.get("section_sequence") or []
    )
    canonical.setdefault("shadow_validation", {})["phase2_inventory_ready"] = ready
    canonical["shadow_validation"]["phase21_blocking_issues"] = len(source_issues)
    canonical["shadow_validation"]["phase2_blocking_issues"] = len(issues)
    if isinstance(canonical.get("statistics"), dict):
        canonical["statistics"]["tasks"] = len(canonical.get("tasks") or [])
        canonical["statistics"]["sections"] = len(canonical.get("sections") or [])
    report["phase"] = ADJUDICATION_PHASE if ready else report.get("phase")
    report["phase2_inventory_ready"] = ready
    report["phase2_issues"] = copy.deepcopy(issues)
    report.setdefault("summary", {})["tasks"] = len(canonical.get("tasks") or [])
    report["summary"]["sections"] = len(canonical.get("sections") or [])
    report["summary"]["inventory_items"] = inventory_items
    report["summary"]["phase21_blocking_issues"] = len(source_issues)
    report["summary"]["phase2_blocking_issues"] = len(issues)
    report["status"] = "passed_with_warnings" if ready and report.get("issues") else (
        "passed" if ready else "failed"
    )
    return canonical, report, issues


def persist_adjudicated_artifacts(
    directory: Path,
    *,
    raw_mmd: str,
    canonical: dict[str, Any],
    report: dict[str, Any],
) -> None:
    aegis_mmd = canonical_source._render_aegis_mmd(canonical)
    aegis_mmd = aegis_mmd.replace(
        "AEGIS CANONICAL SOURCE SHADOW",
        "AEGIS CANONICAL SOURCE PHASE 2.2",
        1,
    ).replace(
        "<!-- used_for_generation: false -->",
        "<!-- used_for_generation: source-critical -->",
        1,
    )
    payloads = {
        "raw_mmd": str(raw_mmd or ""),
        "canonical_json": canonical_source._json_text(canonical),
        "aegis_mmd": aegis_mmd,
        "report": canonical_source._json_text(report),
    }
    for key, content in payloads.items():
        canonical_source._atomic_write(
            Path(directory) / canonical_source.ARTIFACT_SPECS[key]["filename"],
            content,
        )


def adjudicate_job_source(
    db: Any,
    job: Any,
    canonical: dict[str, Any],
    report: dict[str, Any],
    *,
    decision_provider: DecisionProvider | None = None,
) -> tuple[dict[str, Any], dict[str, Any], bool]:
    """Attempt every eligible packet, persist provenance, and return readiness."""
    from . import uploads

    source_path = uploads.upload_file_path(job)
    if not source_path.exists():
        raise ValueError("the original uploaded source is unavailable for adjudication")
    if source_path.suffix.lower() not in {
        ".pdf", ".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"
    }:
        raise ValueError(
            "Phase 2.2 can recover omitted source text only from an original PDF/image; "
            "upload a complete MMD or the original document."
        )
    issues = phase2.phase2_inventory_issues(canonical, report)
    eligible = [issue for issue in issues if issue.get("code") in ELIGIBLE_ISSUE_CODES]
    packets = build_issue_packets(canonical, eligible)
    if not packets:
        return canonical, report, not issues

    provider = decision_provider or adjudicate_packet_via_openai
    decisions: list[dict[str, Any]] = []
    repaired = False
    source_chars = int((canonical.get("document") or {}).get("source_chars") or len(job.mmd_text or ""))
    progress.step("Canonical source — adjudicating unresolved PDF evidence", value=0.005)
    progress.log(
        f"Phase 2.2 prepared {len(packets)} bounded source issue packet(s); "
        "only relevant original-document pages will be inspected.",
        level="warning",
    )

    for index, packet in enumerate(packets, start=1):
        cache_key = _cache_key(canonical=canonical, source_path=source_path, packet=packet)
        cached = _read_cache(cache_key)
        if cached is not None and (cached.get("result") or {}).get("status") == "verified":
            result = copy.deepcopy(cached.get("result") or {})
            cache_state = "hit"
            progress.log(
                f"Source packet {index}/{len(packets)} cache: verified hit.",
                level="success",
            )
        else:
            pages = collect_evidence_pages(
                source_path, packet, source_chars=source_chars
            )
            progress.log(
                f"Source packet {index}/{len(packets)} ({packet['issue_type']}) "
                f"candidate pages: "
                + ", ".join(
                    f"{page.evidence_id}=PDF {page.page_number}" for page in pages
                )
                + ". Cache: miss."
            )
            result = provider(copy.deepcopy(packet), pages)
            cache_state = "miss"
            if result.get("status") == "verified":
                cache_payload = {
                    "version": ADJUDICATION_VERSION,
                    "created_at": time.time(),
                    "packet_fingerprint": packet["fingerprint"],
                    "source_file_sha256": _pdf_sha256(source_path),
                    "model": config.OPENAI_MODEL,
                    "result": result,
                }
                _write_cache(cache_key, cache_payload)

        entry = {
            "issue_id": packet["issue_id"],
            "issue_type": packet["issue_type"],
            "cache_key": cache_key,
            "cache": cache_state,
            "status": str(result.get("status") or "review_required"),
            "reason": str(result.get("reason") or ""),
        }
        if result.get("status") == "verified" and isinstance(result.get("decision"), dict):
            provenance = apply_verified_decision(
                canonical,
                packet,
                copy.deepcopy(result["decision"]),
                cache_key=cache_key,
                source_path=source_path,
            )
            entry["provenance"] = provenance
            repaired = True
            progress.log(
                f"Verified source repair {index}/{len(packets)} from original "
                f"page {provenance['page_number']}: {packet['issue_type']}.",
                level="success",
            )
        else:
            progress.log(
                f"Source issue {index}/{len(packets)} remains unresolved: "
                f"{entry['reason'] or packet['issue_type']}.",
                level="warning",
            )
        decisions.append(entry)

    canonical, report, remaining = _recalculate_after_adjudication(canonical, report)
    semantic = semantic_source(canonical, str(job.mmd_text or ""))
    marker = canonical.setdefault("source_adjudication", {})
    marker.update({
        "version": ADJUDICATION_VERSION,
        "compiler": ADJUDICATION_COMPILER,
        "status": "verified" if not remaining else "review_required",
        "decisions": decisions,
        "verified_repairs": sum(1 for item in decisions if item.get("status") == "verified"),
        "remaining_issues": len(remaining),
        "semantic_source_sha256": _sha256_text(semantic),
        "raw_mmd_changed": False,
    })
    report["source_adjudication"] = copy.deepcopy(marker)
    report.setdefault("summary", {})["source_adjudication_verified_repairs"] = marker[
        "verified_repairs"
    ]
    report["summary"]["source_adjudication_remaining_issues"] = len(remaining)
    persist_adjudicated_artifacts(
        uploads.source_artifact_directory(int(job.id)),
        raw_mmd=str(job.mmd_text or ""),
        canonical=canonical,
        report=report,
    )

    if repaired:
        job.generation_checkpoint = {}
        job.question_inventory = {}
        job.detail = (
            "Verified original-document source adjudication updated the canonical "
            "ledger; generation checkpoints were cleared because semantic source "
            "structure changed."
        )
        db.commit()
    return canonical, report, not remaining

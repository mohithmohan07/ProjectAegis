"""Phase 2.2.1 evidence-page protocol and cache-safety patch.

Phase 2.2 originally asked the model to return a numeric ``page_number``.  A
textbook page image can contain its own printed page number, while Aegis also
tracks the one-based page index inside the uploaded PDF.  The model could
therefore transcribe a visible printed number that was not one of the selected
PDF page indexes, causing the otherwise-valid source read to be rejected as
"outside the evidence packet".

This patch removes that ambiguity.  Every supplied page has an opaque,
server-issued evidence ID (for example ``EVIDENCE-PDF-00009``); the model only
copies that ID.  The server retains sole authority over the actual PDF page and
the deterministic ACSD insertion anchor.  Only verified decisions are cached,
so a malformed or uncertain model response cannot poison all later retries.
"""
from __future__ import annotations

import copy
import json
from typing import Any

from . import canonical_source_phase21_structure as structure
from . import canonical_source_phase22 as phase22
from . import progress

PATCH_VERSION = "2.2.1"
PATCH_COMPILER = "phase-2.2.1-evidence-page-protocol-1"
_PROTOCOL = "opaque-evidence-page-id-v1"
_CONTRACT_VERSION = 1


def evidence_page_id(page: phase22.EvidencePage) -> str:
    """Return a stable opaque ID for one selected internal PDF page."""
    return f"EVIDENCE-PDF-{int(page.page_number):05d}"


def _available_pages(pages: list[phase22.EvidencePage]) -> list[dict[str, Any]]:
    return [
        {
            "evidence_page_id": evidence_page_id(page),
            "internal_pdf_page_number": int(page.page_number),
            "instruction": (
                "Copy evidence_page_id exactly. Do not use a page number printed "
                "inside the textbook page image."
            ),
        }
        for page in pages
    ]


def deterministic_anchor(packet: dict[str, Any]) -> str:
    """Choose the insertion boundary from ACSD data, never from model prose."""
    allowed = {
        str(value)
        for value in packet.get("allowed_insert_before_block_ids") or []
        if value
    }
    candidates = [
        block
        for block in packet.get("nearby_blocks") or []
        if isinstance(block, dict) and str(block.get("block_id") or "") in allowed
    ]
    if not candidates:
        return ""

    source_start = int(packet.get("source_start") or 0)
    issue_type = str(packet.get("issue_type") or "")
    if issue_type == "missing_parent_section":
        at_or_after = [
            block
            for block in candidates
            if int(block.get("source_start") or 0) >= source_start
        ]
        pool = at_or_after or candidates
        chosen = min(
            pool,
            key=lambda block: (
                abs(int(block.get("source_start") or 0) - source_start),
                0 if block.get("kind") == "heading" else 1,
                str(block.get("block_id") or ""),
            ),
        )
    else:
        # A recovered task owning an orphan Figure is inserted immediately before
        # the Figure block.  The exact-source-start candidate wins.
        chosen = min(
            candidates,
            key=lambda block: (
                abs(int(block.get("source_start") or 0) - source_start),
                0 if int(block.get("source_start") or 0) == source_start else 1,
                0 if block.get("kind") == "figure" else 1,
                str(block.get("block_id") or ""),
            ),
        )
    return str(chosen.get("block_id") or "")


def system_prompt(*, verification: bool) -> str:
    role = "verification reviewer" if verification else "source transcription adjudicator"
    return f"""
You are the Aegis {role}. The supplied original textbook page images are the
only authority. MMD excerpts are diagnostic context and may be incomplete.

Rules:
1. Never reconstruct, paraphrase, complete, or infer missing textbook wording.
2. Return text only when it is visibly printed on one supplied page.
3. Preserve wording, spelling, punctuation, numbering, and order exactly.
4. Copy one supplied evidence_page_id exactly. Never report a page number printed
   inside the textbook image and never invent an evidence ID.
5. Do not choose or rewrite an insertion location. Aegis determines the ACSD
   insertion anchor from immutable source order.
6. A Figure caption is not evidence that an unseen task exists.
7. If visibility is uncertain, return verdict "ambiguous" or "not_visible".
8. Output one JSON object only, with no markdown.
""".strip()


def packet_prompt(
    packet: dict[str, Any],
    *,
    pages: list[phase22.EvidencePage] | None = None,
    verification_candidate: dict[str, Any] | None = None,
) -> str:
    pages = list(pages or [])
    schema = {
        "verdict": "visible_exact | not_visible | ambiguous",
        "evidence_page_id": "copy exactly one supplied EVIDENCE-PDF-##### ID",
        "recovered_text": "exact visible text or empty string",
        "source_label": "exact visible task cue or empty string",
        "confidence": 0.0,
        "evidence": ["brief visual evidence, no chain of thought"],
    }
    body: dict[str, Any] = {
        "issue_packet": packet,
        "evidence_page_protocol": _PROTOCOL,
        "available_evidence_pages": _available_pages(pages),
        "deterministic_insert_before_block_id": deterministic_anchor(packet),
        "required_response_schema": schema,
    }
    if verification_candidate is not None:
        body["candidate_to_verify"] = verification_candidate
        body["instruction"] = (
            "Independently verify that the candidate text is visibly present on "
            "the page identified by candidate_to_verify.evidence_page_id. Return "
            "that same evidence_page_id only when the visible source agrees."
        )
    else:
        body["instruction"] = (
            "Transcribe only the exact missing heading or task if visibly present. "
            "The evidence_page_id is an opaque server label, not a textbook page number."
        )
    return json.dumps(body, ensure_ascii=False, indent=2)


def _page_for_candidate(
    candidate: dict[str, Any],
    pages: list[phase22.EvidencePage],
) -> tuple[phase22.EvidencePage | None, str]:
    available = {evidence_page_id(page): page for page in pages}
    returned_id = str(candidate.get("evidence_page_id") or "").strip()
    if returned_id:
        page = available.get(returned_id)
        if page is not None:
            return page, ""
        return None, (
            f"extractor returned unknown evidence_page_id {returned_id!r}; "
            f"available IDs are {', '.join(available) or '(none)'}"
        )

    # Backward-compatible acceptance for a provider that ignored the new field
    # but copied the *internal PDF page* exactly. A printed textbook page number
    # that is not present in the packet remains rejected.
    try:
        legacy_page = int(
            candidate.get("internal_pdf_page_number")
            or candidate.get("pdf_page_number")
            or candidate.get("page_number")
            or 0
        )
    except (TypeError, ValueError):
        legacy_page = 0
    page = next((item for item in pages if item.page_number == legacy_page), None)
    if page is not None:
        return page, ""
    return None, (
        "extractor did not copy a valid evidence_page_id; "
        f"available IDs are {', '.join(available) or '(none)'}"
    )


def validated_candidate(
    packet: dict[str, Any],
    candidate: dict[str, Any],
    pages: list[phase22.EvidencePage],
) -> tuple[dict[str, Any] | None, str]:
    verdict = str(candidate.get("verdict") or "").strip().lower()
    if verdict != "visible_exact":
        return None, f"extractor verdict was {verdict or 'missing'}"
    recovered = str(candidate.get("recovered_text") or "").strip()
    if not recovered:
        return None, "extractor returned no source text"

    page, reason = _page_for_candidate(candidate, pages)
    if page is None:
        return None, reason
    confidence = phase22._confidence(candidate.get("confidence"))
    if confidence < phase22._MIN_CONFIDENCE:
        return None, f"extractor confidence {confidence:.3f} is below threshold"

    issue_type = str(packet.get("issue_type") or "")
    if issue_type == "missing_parent_section":
        number = int(packet.get("section_number") or 0)
        if not phase22.re.match(rf"^\s*{number}(?:\s|[.)-])", recovered):
            return None, "recovered heading does not preserve the expected section number"
    elif issue_type == "orphan_figure_task":
        if not structure.is_task_like(recovered):
            return None, "recovered text is not a question or instructional task"
        caption_key = phase22._comparison_key(packet.get("figure_caption") or "")
        recovered_key = phase22._comparison_key(recovered)
        if recovered_key and recovered_key == caption_key:
            return None, "recovered text merely repeats the Figure caption"
    else:
        return None, "unsupported adjudication packet"

    anchor = deterministic_anchor(packet)
    if not anchor:
        return None, "Aegis could not determine a unique ACSD insertion anchor"

    normalized = copy.deepcopy(candidate)
    normalized.update({
        "verdict": "visible_exact",
        "evidence_page_id": evidence_page_id(page),
        "page_number": int(page.page_number),
        "internal_pdf_page_number": int(page.page_number),
        "recovered_text": recovered,
        "source_label": phase22._normalize_source_label(
            candidate.get("source_label"), issue_type=issue_type
        ),
        "insert_before_block_id": anchor,
        "confidence": confidence,
        "text_layer_match": phase22._text_layer_contains(page, recovered),
        "evidence_page_protocol": _PROTOCOL,
    })
    return normalized, ""


def verify_candidate(
    packet: dict[str, Any],
    candidate: dict[str, Any],
    pages: list[phase22.EvidencePage],
) -> tuple[dict[str, Any] | None, str]:
    evidence_id = str(candidate.get("evidence_page_id") or "")
    page = next((item for item in pages if evidence_page_id(item) == evidence_id), None)
    if page is None:
        return None, "candidate evidence page is unavailable"
    verification = phase22._openai_multimodal_json(
        system=system_prompt(verification=True),
        prompt=packet_prompt(
            packet,
            pages=[page],
            verification_candidate=candidate,
        ),
        pages=[page],
    )
    verified, reason = validated_candidate(packet, verification, [page])
    if verified is None:
        return None, f"verification rejected the candidate: {reason}"
    if phase22._comparison_key(verified["recovered_text"]) != phase22._comparison_key(
        candidate["recovered_text"]
    ):
        return None, "extractor and verifier transcriptions disagree"
    if verified["evidence_page_id"] != candidate["evidence_page_id"]:
        return None, "extractor and verifier evidence-page IDs disagree"
    if verified["insert_before_block_id"] != candidate["insert_before_block_id"]:
        return None, "deterministic insertion anchors disagree"
    if packet.get("issue_type") == "orphan_figure_task":
        left = phase22._normal(verified.get("source_label"))
        right = phase22._normal(candidate.get("source_label"))
        if left and right and left != right:
            return None, "extractor and verifier source labels disagree"
    text_layer = bool(candidate.get("text_layer_match") or verified.get("text_layer_match"))
    minimum = (
        phase22._MIN_CONFIDENCE
        if text_layer
        else phase22._NO_TEXT_LAYER_MIN_CONFIDENCE
    )
    if min(candidate["confidence"], verified["confidence"]) < minimum:
        return None, "verified confidence is below the evidence threshold"
    accepted = copy.deepcopy(candidate)
    accepted["verification"] = {
        "confidence": verified["confidence"],
        "page_number": verified["page_number"],
        "evidence_page_id": verified["evidence_page_id"],
        "text_layer_match": text_layer,
        "transcription_key": phase22._comparison_key(verified["recovered_text"]),
    }
    return accepted, ""


def adjudicate_packet_via_openai(
    packet: dict[str, Any],
    pages: list[phase22.EvidencePage],
) -> dict[str, Any]:
    if not pages:
        return {
            "status": "review_required",
            "reason": "no original-document pages could be selected",
        }
    page_ids = [evidence_page_id(page) for page in pages]
    progress.log(
        f"Source evidence packet {packet.get('issue_id') or '(unknown)'} supplied "
        f"{', '.join(page_ids)}; model must copy one opaque evidence-page ID.",
    )
    candidate = phase22._openai_multimodal_json(
        system=system_prompt(verification=False),
        prompt=packet_prompt(packet, pages=pages),
        pages=pages,
    )
    validated, reason = validated_candidate(packet, candidate, pages)
    if validated is None:
        return {
            "status": "review_required",
            "reason": reason,
            "extractor": candidate,
            "available_evidence_page_ids": page_ids,
        }
    accepted, reason = verify_candidate(packet, validated, pages)
    if accepted is None:
        return {
            "status": "review_required",
            "reason": reason,
            "extractor": validated,
            "available_evidence_page_ids": page_ids,
        }
    return {"status": "verified", "decision": accepted}


def install() -> None:
    if getattr(phase22, "_PHASE221_CONTRACT_VERSION", 0) >= _CONTRACT_VERSION:
        return

    original_read_cache = phase22._read_cache
    original_write_cache = phase22._write_cache

    def read_cache(cache_key: str) -> dict[str, Any] | None:
        value = original_read_cache(cache_key)
        if not isinstance(value, dict) or value.get("version") != PATCH_VERSION:
            return None
        result = value.get("result")
        if not isinstance(result, dict) or result.get("status") != "verified":
            return None
        return value

    def write_cache(cache_key: str, value: dict[str, Any]) -> None:
        # Never persist protocol errors, uncertain reads, or review-required
        # responses. A later retry must be allowed to inspect the source again.
        result = value.get("result") if isinstance(value, dict) else None
        if not isinstance(result, dict) or result.get("status") != "verified":
            return
        payload = copy.deepcopy(value)
        payload["version"] = PATCH_VERSION
        payload["evidence_page_protocol"] = _PROTOCOL
        original_write_cache(cache_key, payload)

    phase22.ADJUDICATION_VERSION = PATCH_VERSION
    phase22.ADJUDICATION_COMPILER = PATCH_COMPILER
    phase22._system_prompt = system_prompt
    phase22._packet_prompt = packet_prompt
    phase22._validated_candidate = validated_candidate
    phase22._verify_candidate = verify_candidate
    phase22.adjudicate_packet_via_openai = adjudicate_packet_via_openai
    phase22._read_cache = read_cache
    phase22._write_cache = write_cache
    phase22.evidence_page_id = evidence_page_id
    phase22.deterministic_anchor = deterministic_anchor
    phase22._PHASE221_CONTRACT_VERSION = _CONTRACT_VERSION

"""Phase 3.4.2 PDF page-batch isolation and semantic salvage.

The verified GPT PDF-to-ACSD path already reconstructs missing converter content
from the original page image and text layer.  A batch-level disagreement should
not, however, discard pages that can be verified independently.  This contract:

* gives the normal correction loop one additional attempt by default;
* decomposes an unresolved multi-page batch into independently verified pages;
* gives a still-unresolved single page one final semantic reconstruction pass
  using the original image/text and a simpler non-overlapping geometry contract;
* accepts that salvage only after the unchanged deterministic validator and an
  independent original-page verifier both approve it.

It does not invent source questions or bypass evidence.  If the page is not
visibly recoverable, the existing review-required boundary remains intact.
"""
from __future__ import annotations

import copy
import json
import os
from typing import Any

from . import canonical_source_phase22 as phase22
from . import canonical_source_phase221_fallback as page_acsd
from . import progress

_CONTRACT_VERSION = 1


def _semantic_salvage(
    pages: list[page_acsd.PdfPage],
    previous: dict[str, Any],
) -> dict[str, Any]:
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
    reason = str(previous.get("reason") or "verification did not converge")
    system = (
        page_acsd._extraction_system_prompt()
        + "\n\nFINAL SEMANTIC SALVAGE CONTRACT: Reconstruct every meaningful "
        "visible block from the supplied original page. Preserve exact visible "
        "wording, tasks, source labels, tables, mathematics, captions, and figure "
        "ownership. Do not invent missing textbook prose or questions. If precise "
        "geometry caused the earlier rejection, use conservative, non-overlapping "
        "horizontal bbox bands in true reading order while keeping each visible "
        "semantic block separate. Running headers/footers/navigation may be "
        "omitted. Return every supplied page exactly once."
    )
    prompt = json.dumps(
        {
            "pages": [
                {
                    "page_id": page.page_id,
                    "page_number_for_audit_only": page.page_number,
                    "text_layer": page.text[:12000],
                }
                for page in pages
            ],
            "previous_failure": reason[:2000],
            "instruction": (
                "Produce a fresh page ACSD candidate from the original evidence. "
                "Do not copy an invalid prior block order or bbox."
            ),
        },
        ensure_ascii=False,
        indent=2,
    )
    candidate = phase22._openai_multimodal_json(
        system=system,
        prompt=prompt,
        pages=evidence_pages,
        response_schema=page_acsd.extraction_schema(pages),
        purpose="source_adjudication",
        max_tokens=max(16000, page_acsd._max_output_tokens()),
    )
    normalized, validation_reason = page_acsd.validate_page_extraction(
        pages, candidate
    )
    if normalized is None:
        return {
            "status": "review_required",
            "reason": (
                "final semantic salvage failed deterministic validation: "
                + validation_reason
            ),
            "salvage_candidate": candidate,
            "previous_result": copy.deepcopy(previous),
        }

    verification = phase22._openai_multimodal_json(
        system=page_acsd._verification_system_prompt(),
        prompt=page_acsd._page_prompt(pages, candidate=normalized),
        pages=evidence_pages,
        response_schema=page_acsd.verification_schema(pages),
        purpose="source_adjudication",
        max_tokens=max(8000, 2400 * len(pages)),
    )
    verification_reason = page_acsd._verification_rejection_reason(
        pages, verification
    )
    if verification_reason:
        return {
            "status": "review_required",
            "reason": (
                "final semantic salvage failed independent verification: "
                + verification_reason
            ),
            "verification": verification,
            "previous_result": copy.deepcopy(previous),
        }
    history = list(previous.get("correction_history") or [])
    history.append({
        "attempt": len(history) + 1,
        "stage": "semantic_salvage",
        "reason": reason,
    })
    return {
        "status": "verified",
        "pages": normalized["pages"],
        "verification": verification,
        "correction_history": history,
        "recovered_by": "phase3.4.2-semantic-salvage",
    }


def install() -> None:
    if (
        getattr(page_acsd, "_PHASE342_PDF_SALVAGE_CONTRACT_VERSION", 0)
        >= _CONTRACT_VERSION
    ):
        return

    original_extract = page_acsd.extract_batch_via_openai
    original_max_corrections = page_acsd._max_correction_attempts
    page_acsd._PHASE342_ORIGINAL_EXTRACT_BATCH = original_extract
    page_acsd._PHASE342_ORIGINAL_MAX_CORRECTIONS = original_max_corrections

    def max_correction_attempts() -> int:
        # Explicit operator configuration remains authoritative. The production
        # default receives one additional bounded repair pass.
        if "AEGIS_GPT_PDF_ACSD_MAX_CORRECTIONS" in os.environ:
            return original_max_corrections()
        return max(3, original_max_corrections())

    def resilient_extract_batch(
        pages: list[page_acsd.PdfPage],
    ) -> dict[str, Any]:
        result = original_extract(pages)
        if result.get("status") == "verified":
            return result

        if len(pages) > 1:
            progress.log(
                "GPT PDF-to-ACSD batch did not converge as a group; isolating "
                f"{len(pages)} page(s) and independently verifying each page "
                "instead of stopping the source pipeline.",
                level="warning",
            )
            accepted: list[dict[str, Any]] = []
            histories: list[dict[str, Any]] = []
            verifications: list[dict[str, Any]] = []
            for page in pages:
                single = resilient_extract_batch([page])
                if single.get("status") != "verified":
                    return {
                        "status": "review_required",
                        "reason": (
                            f"{page.page_id} remained unresolved after page "
                            f"isolation: {single.get('reason') or 'unknown reason'}"
                        ),
                        "failed_page_id": page.page_id,
                        "batch_result": copy.deepcopy(result),
                        "page_result": single,
                    }
                accepted.extend(copy.deepcopy(single.get("pages") or []))
                histories.extend(
                    copy.deepcopy(single.get("correction_history") or [])
                )
                if isinstance(single.get("verification"), dict):
                    verifications.append(copy.deepcopy(single["verification"]))
            accepted.sort(key=lambda row: int(row.get("page_number") or 0))
            return {
                "status": "verified",
                "pages": accepted,
                "verification": {
                    "verdict": "verified",
                    "approved_page_ids": [page.page_id for page in pages],
                    "rejected_page_ids": [],
                    "confidence": min(
                        [
                            float(row.get("confidence") or 1.0)
                            for row in verifications
                        ]
                        or [1.0]
                    ),
                    "issues": [],
                    "page_verifications": verifications,
                },
                "correction_history": histories,
                "recovered_by": "phase3.4.2-page-isolation",
            }

        progress.log(
            f"{pages[0].page_id if pages else 'PDF page'} remained unresolved "
            "after ordinary correction; attempting one final evidence-bound "
            "semantic reconstruction from the original page.",
            level="warning",
        )
        return _semantic_salvage(pages, result)

    page_acsd._max_correction_attempts = max_correction_attempts
    page_acsd.extract_batch_via_openai = resilient_extract_batch
    page_acsd._PHASE342_PDF_SALVAGE_CONTRACT_VERSION = _CONTRACT_VERSION

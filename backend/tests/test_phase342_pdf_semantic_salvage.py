"""Regression coverage for Phase 3.4.2 PDF semantic salvage."""
from __future__ import annotations

from app.services import canonical_source_phase22 as phase22
from app.services import canonical_source_phase221_fallback as page_acsd
from app.services import canonical_source_phase342_pdf_semantic_salvage_contract as phase342


def _page(number: int, text: str) -> page_acsd.PdfPage:
    return page_acsd.PdfPage(
        page_id=f"PDF-PAGE-{number:04d}",
        page_number=number,
        text=text,
        image_data_url="data:image/jpeg;base64,AA==",
        width=600.0,
        height=800.0,
    )


def _block(text: str, *, order: int = 1) -> dict:
    return {
        "reading_order": order,
        "kind": "paragraph",
        "bbox": [50, 80, 950, 240],
        "text": text,
        "heading_level": 0,
        "source_label": "",
        "latex": "",
        "table_rows": [],
        "linked_visual_orders": [],
        "linked_context_orders": [],
        "caption": "",
        "confidence": 0.999,
    }


def test_semantic_salvage_still_requires_deterministic_and_independent_verification(
    monkeypatch,
):
    page = _page(1, "Visible paragraph from the original page.")
    calls: list[str] = []

    def fake_call(**kwargs):
        name = kwargs["response_schema"]["name"]
        calls.append(name)
        if "verify" in name:
            return {
                "verdict": "verified",
                "approved_page_ids": [page.page_id],
                "rejected_page_ids": [],
                "confidence": 0.999,
                "issues": [],
            }
        return {
            "pages": [{
                "page_id": page.page_id,
                "confidence": 0.999,
                "blocks": [_block(page.text)],
            }]
        }

    monkeypatch.setattr(phase22, "_openai_multimodal_json", fake_call)
    result = phase342._semantic_salvage(
        [page],
        {
            "status": "review_required",
            "reason": "the earlier bbox geometry did not converge",
            "correction_history": [],
        },
    )

    assert result["status"] == "verified"
    assert result["recovered_by"] == "phase3.4.2-semantic-salvage"
    assert result["pages"][0]["blocks"][0]["text"] == page.text
    assert result["correction_history"][-1]["stage"] == "semantic_salvage"
    assert calls == [
        "aegis_pdf_page_acsd_extract",
        "aegis_pdf_page_acsd_verify",
    ]


def test_unresolved_multi_page_batch_isolated_instead_of_stopping(monkeypatch):
    pages = [
        _page(1, "First visible page paragraph."),
        _page(2, "Second visible page paragraph."),
    ]
    extraction_batch_sizes: list[int] = []

    def extraction_page_ids(schema: dict) -> list[str]:
        return list(
            schema["schema"]["properties"]["pages"]["items"][
                "properties"
            ]["page_id"]["enum"]
        )

    def verification_page_ids(schema: dict) -> list[str]:
        return list(
            schema["schema"]["properties"]["approved_page_ids"][
                "items"
            ]["enum"]
        )

    def fake_call(**kwargs):
        schema = kwargs["response_schema"]
        name = schema["name"]
        if "verify" in name:
            ids = verification_page_ids(schema)
            return {
                "verdict": "verified",
                "approved_page_ids": ids,
                "rejected_page_ids": [],
                "confidence": 0.999,
                "issues": [],
            }
        ids = extraction_page_ids(schema)
        extraction_batch_sizes.append(len(ids))
        if len(ids) > 1:
            # Keep the page identities exact but force deterministic
            # rejection: a missing blocks array is genuinely unusable
            # (structural quirks are normalized, never rejected, now).
            return {
                "pages": [
                    {
                        "page_id": page_id,
                        "confidence": 0.999,
                        "blocks": None,
                    }
                    for page_id in ids
                ]
            }
        source = next(page.text for page in pages if page.page_id == ids[0])
        return {
            "pages": [{
                "page_id": ids[0],
                "confidence": 0.999,
                "blocks": [_block(source)],
            }]
        }

    monkeypatch.delenv("AEGIS_GPT_PDF_ACSD_MAX_CORRECTIONS", raising=False)
    monkeypatch.setattr(phase22, "_openai_multimodal_json", fake_call)

    result = page_acsd.extract_batch_via_openai(pages)

    assert result["status"] == "verified"
    assert result["recovered_by"] == "phase3.4.2-page-isolation"
    assert [row["page_number"] for row in result["pages"]] == [1, 2]
    assert [
        row["blocks"][0]["text"] for row in result["pages"]
    ] == [page.text for page in pages]
    assert extraction_batch_sizes.count(2) == 4  # initial + three corrections
    assert extraction_batch_sizes[-2:] == [1, 1]


def test_default_pdf_correction_budget_is_three_but_operator_override_wins(
    monkeypatch,
):
    monkeypatch.delenv("AEGIS_GPT_PDF_ACSD_MAX_CORRECTIONS", raising=False)
    assert page_acsd._max_correction_attempts() == 3

    monkeypatch.setenv("AEGIS_GPT_PDF_ACSD_MAX_CORRECTIONS", "1")
    assert page_acsd._max_correction_attempts() == 1

"""Regression coverage for source-labelled blocks and bounded ACSD correction."""
from __future__ import annotations

from app.services import canonical_source_phase221_fallback as fallback


def _page(text: str = "Visible historical text") -> fallback.PdfPage:
    return fallback.PdfPage(
        page_id="PDF-PAGE-0002",
        page_number=2,
        text=text,
        image_data_url="data:image/jpeg;base64,AA==",
        width=600.0,
        height=800.0,
    )


def _block(
    *,
    kind: str,
    text: str,
    source_label: str = "",
    order: int = 1,
    linked_context_orders: list[int] | None = None,
) -> dict:
    return {
        "reading_order": order,
        "kind": kind,
        "bbox": [40, 40 + order * 80, 920, 100 + order * 80],
        "text": text,
        "heading_level": 0,
        "source_label": source_label,
        "latex": "",
        "table_rows": [],
        "linked_visual_orders": [],
        "linked_context_orders": linked_context_orders or [],
        "caption": "",
        "confidence": 0.999,
    }


def _candidate(*blocks: dict) -> dict:
    return {
        "pages": [{
            "page_id": "PDF-PAGE-0002",
            "confidence": 0.999,
            "blocks": list(blocks),
        }]
    }


def _verified() -> dict:
    return {
        "verdict": "verified",
        "approved_page_ids": ["PDF-PAGE-0002"],
        "rejected_page_ids": [],
        "confidence": 0.999,
        "issues": [],
    }


def test_rne_page2_non_task_source_cue_becomes_explicit_source_block():
    pages = [_page()]
    candidate = _candidate(_block(
        kind="paragraph",
        source_label="Source A",
        text="The first clear expression of nationalism came with the French Revolution.",
    ))

    normalized, reason = fallback.validate_page_extraction(pages, candidate)

    assert reason == ""
    assert normalized is not None
    block = normalized["pages"][0]["blocks"][0]
    assert block["kind"] == "source"
    assert block["source_label"] == "Source A"
    assert block["text"].startswith("The first clear expression")
    rendered = fallback.render_page_acsd_to_mmd(normalized)
    assert "# Source A" in rendered
    assert rendered.index("# Source A") < rendered.index("The first clear expression")


def test_source_block_is_valid_task_context_without_losing_its_label():
    pages = [_page()]
    candidate = _candidate(
        _block(
            kind="paragraph",
            source_label="Source A",
            text="A contemporary observer described the political transformation.",
            order=1,
        ),
        _block(
            kind="task",
            source_label="Discuss",
            text="What does this source reveal?",
            order=2,
            linked_context_orders=[1],
        ),
    )

    normalized, reason = fallback.validate_page_extraction(pages, candidate)

    assert reason == ""
    assert normalized is not None
    source = normalized["pages"][0]["blocks"][0]
    assert source["kind"] == "source"
    assert fallback._page_context_text(source).startswith("Source A\n")


def test_validation_failure_is_self_corrected_before_human_review(monkeypatch):
    """A genuinely unusable candidate (no blocks at all) still drives the
    bounded deterministic correction loop; structural opinions are
    normalized instead (covered below)."""
    pages = [_page("Describe the visible transformation")]
    calls: list[str] = []
    invalid = {
        "pages": [{"page_id": "PDF-PAGE-0002", "confidence": 0.999, "blocks": None}]
    }
    corrected = _candidate(_block(
        kind="task", text="Describe the visible transformation", source_label="Activity"
    ))

    def fake_openai(*, response_schema, **_kwargs):
        calls.append(response_schema["name"])
        if response_schema["name"] == "aegis_pdf_page_acsd_verify":
            return _verified()
        extraction_calls = sum(
            name == "aegis_pdf_page_acsd_extract" for name in calls
        )
        return invalid if extraction_calls == 1 else corrected

    monkeypatch.setenv("AEGIS_GPT_PDF_ACSD_MAX_CORRECTIONS", "2")
    monkeypatch.setattr(fallback.phase22, "_openai_multimodal_json", fake_openai)
    monkeypatch.setattr(fallback.progress, "log", lambda *_args, **_kwargs: None)

    result = fallback.extract_batch_via_openai(pages)

    assert result["status"] == "verified"
    assert result["pages"][0]["blocks"][0]["source_label"] == "Activity"
    assert result["correction_history"] == [{
        "attempt": 1,
        "stage": "deterministic_validation",
        "reason": "PDF-PAGE-0002 blocks are missing",
    }]
    assert calls == [
        "aegis_pdf_page_acsd_extract",
        "aegis_pdf_page_acsd_extract",
        "aegis_pdf_page_acsd_verify",
    ]


def test_correction_loop_is_bounded_when_candidate_never_becomes_valid(monkeypatch):
    pages = [_page("Describe the visible transformation")]
    invalid = {
        "pages": [{"page_id": "PDF-PAGE-0002", "confidence": 0.999, "blocks": None}]
    }
    calls = 0

    def fake_openai(**_kwargs):
        nonlocal calls
        calls += 1
        return invalid

    monkeypatch.setenv("AEGIS_GPT_PDF_ACSD_MAX_CORRECTIONS", "2")
    monkeypatch.setattr(fallback.phase22, "_openai_multimodal_json", fake_openai)
    monkeypatch.setattr(fallback.progress, "log", lambda *_args, **_kwargs: None)

    result = fallback.extract_batch_via_openai(pages)

    assert result["status"] == "review_required"
    assert result["reason"] == "PDF-PAGE-0002 blocks are missing"
    assert len(result["correction_history"]) == 2
    assert calls == 3


def test_verifier_rejection_gets_one_bounded_repair(monkeypatch):
    pages = [_page("Describe the visible transformation")]
    candidate = _candidate(_block(
        kind="task", text="Describe the visible transformation", source_label="Activity"
    ))
    extraction_calls = 0
    verification_calls = 0

    def fake_openai(*, response_schema, **_kwargs):
        nonlocal extraction_calls, verification_calls
        if response_schema["name"] == "aegis_pdf_page_acsd_extract":
            extraction_calls += 1
            return candidate
        verification_calls += 1
        if verification_calls == 1:
            return {
                "verdict": "needs_correction",
                "approved_page_ids": [],
                "rejected_page_ids": ["PDF-PAGE-0002"],
                "confidence": 0.80,
                "issues": ["task cue must be checked against the page"],
            }
        return _verified()

    monkeypatch.setenv("AEGIS_GPT_PDF_ACSD_MAX_CORRECTIONS", "2")
    monkeypatch.setattr(fallback.phase22, "_openai_multimodal_json", fake_openai)
    monkeypatch.setattr(fallback.progress, "log", lambda *_args, **_kwargs: None)

    result = fallback.extract_batch_via_openai(pages)

    assert result["status"] == "verified"
    assert extraction_calls == 2
    assert verification_calls == 2
    assert result["correction_history"][0]["stage"] == "independent_verification"

"""Regression coverage for vertical running-navigation recovery."""
from __future__ import annotations

from app.services import canonical_source_phase221_fallback as fallback


def _page() -> fallback.PdfPage:
    return fallback.PdfPage(
        page_id="PDF-PAGE-0025",
        page_number=25,
        text="Nationalism in Europe A concluding historical paragraph.",
        image_data_url="data:image/jpeg;base64,AA==",
        width=600.0,
        height=800.0,
    )


def _block(
    *,
    kind: str,
    text: str,
    order: int,
    bbox: list[int],
    heading_level: int = 0,
    source_label: str = "",
    linked_context_orders: list[int] | None = None,
) -> dict:
    return {
        "reading_order": order,
        "kind": kind,
        "bbox": bbox,
        "text": text,
        "heading_level": heading_level,
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
            "page_id": "PDF-PAGE-0025",
            "confidence": 0.999,
            "blocks": list(blocks),
        }]
    }


def test_rne_page25_sidebar_alias_and_zero_order_are_recovered():
    candidate = _candidate(
        _block(
            kind="heading",
            text="Nationalism and Imperialism",
            order=1,
            bbox=[220, 80, 900, 150],
            heading_level=1,
        ),
        _block(
            kind="paragraph",
            text="A concluding historical paragraph.",
            order=2,
            bbox=[220, 180, 900, 320],
        ),
        _block(
            kind="sidebar",
            text="Nationalism in Europe",
            order=0,
            bbox=[20, 120, 105, 900],
        ),
    )

    normalized, reason = fallback.validate_page_extraction([_page()], candidate)

    assert reason == ""
    assert normalized is not None
    blocks = normalized["pages"][0]["blocks"]
    navigation = next(block for block in blocks if block["kind"] == "navigation")
    assert navigation["text"] == "Nationalism in Europe"
    assert navigation["reading_order"] == 3
    assert navigation["heading_level"] == 0
    assert navigation["source_label"] == ""

    rendered = fallback.render_page_acsd_to_mmd(normalized)
    assert "Nationalism and Imperialism" in rendered
    assert "A concluding historical paragraph." in rendered
    assert "Nationalism in Europe" not in rendered


def test_vertical_margin_heading_is_navigation_even_with_valid_order():
    candidate = _candidate(
        _block(
            kind="heading",
            text="Main Section",
            order=1,
            bbox=[220, 80, 900, 150],
            heading_level=1,
        ),
        _block(
            kind="paragraph",
            text="A concluding historical paragraph.",
            order=2,
            bbox=[220, 180, 900, 320],
        ),
        _block(
            kind="heading",
            text="Nationalism in Europe",
            order=3,
            bbox=[15, 120, 95, 900],
            heading_level=1,
        ),
    )

    normalized, reason = fallback.validate_page_extraction([_page()], candidate)

    assert reason == ""
    assert normalized is not None
    sidebar = normalized["pages"][0]["blocks"][2]
    assert sidebar["kind"] == "navigation"
    assert sidebar["heading_level"] == 0


def test_invalid_semantic_block_order_is_normalized_and_flagged():
    """Structural quirks no longer fail a book closed: an invalid reading
    order is reassigned deterministically and the page carries a review
    flag naming the repair."""
    candidate = _candidate(_block(
        kind="paragraph",
        text="A wide semantic paragraph must not be silently reordered.",
        order=0,
        bbox=[180, 180, 920, 360],
    ))

    normalized, reason = fallback.validate_page_extraction([_page()], candidate)

    assert reason == ""
    assert normalized is not None
    page = normalized["pages"][0]
    assert page["blocks"][0]["reading_order"] == 1
    assert any(
        "reading_order" in flag for flag in page.get("review_flags") or []
    )


def test_navigation_cannot_become_task_context():
    candidate = _candidate(
        _block(
            kind="paragraph",
            text="A concluding historical paragraph.",
            order=1,
            bbox=[220, 180, 900, 320],
        ),
        _block(
            kind="task",
            text="What does the paragraph explain?",
            order=2,
            bbox=[220, 350, 900, 430],
            source_label="Discuss",
            linked_context_orders=[1, 3],
        ),
        _block(
            kind="navigation",
            text="Nationalism in Europe",
            order=3,
            bbox=[20, 120, 105, 900],
        ),
    )

    normalized, reason = fallback.validate_page_extraction([_page()], candidate)

    assert reason == ""
    assert normalized is not None
    task = normalized["pages"][0]["blocks"][1]
    assert task["linked_context_orders"] == [1]


def test_prompts_treat_vertical_running_labels_as_non_semantic_navigation():
    extraction = fallback._extraction_system_prompt()
    verification = fallback._verification_system_prompt()
    correction = fallback._correction_system_prompt()

    assert "kind=navigation" in extraction
    assert "such navigation is omitted" in verification
    assert "Never introduce kind=sidebar or reading_order=0" in correction

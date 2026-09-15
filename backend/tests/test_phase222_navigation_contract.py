"""Regression coverage for vertical running-navigation recovery."""
from __future__ import annotations

import inspect

from app.services import canonical_source_phase221_fallback as fallback
from app.services import (
    canonical_source_phase342_pdf_semantic_salvage_contract as salvage,
)


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


def test_a_margin_heading_the_model_declared_stays_a_heading():
    """Geometry is not a verdict about meaning.

    The bbox shape-matcher this contract used to carry — short, slender, pinned
    to a page edge — rewrote the model's ``heading`` into ``navigation``, which
    strips the block out of the semantic MMD altogether. A tinted definition box
    in a wide outer margin, a vertical pull quote and a sideways-printed stanza
    all have that shape, so the rule silently deleted real teaching from books
    it had never seen. Rule 1: only the model decides what a block means, and
    the geometry now lives in the prompts where an independent verifier checks
    the answer against the page image.
    """
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
    page = normalized["pages"][0]
    margin_heading = page["blocks"][2]
    assert margin_heading["kind"] == "heading"
    assert margin_heading["heading_level"] == 1
    assert margin_heading["reading_order"] == 3
    # Nothing was reclassified, so the page carries no navigation flag at all.
    assert not [
        flag for flag in page.get("review_flags") or []
        if "navigation" in flag
    ]
    # The decisive consequence: the block still reaches the semantic MMD.
    assert "Nationalism in Europe" in fallback.render_page_acsd_to_mmd(normalized)


def test_declared_navigation_normalizes_and_leaves_one_page_flag():
    """A block the MODEL called navigation is still contained — and recorded.

    Containment is the half of this contract that was never in dispute: page
    furniture must not become task context and must not render into the
    semantic MMD. What it must also do is leave a trace, because removing a
    block from the MMD is invisible downstream; the page ledger is the only
    place a reviewer can see that the page had something the chapter never did.
    """
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
            heading_level=2,
            source_label="Chapter 1",
        ),
    )

    normalized, reason = fallback.validate_page_extraction([_page()], candidate)

    assert reason == ""
    assert normalized is not None
    page = normalized["pages"][0]
    navigation = page["blocks"][2]
    assert navigation["kind"] == "navigation"
    assert navigation["heading_level"] == 0
    assert navigation["source_label"] == ""
    assert navigation["linked_context_orders"] == []
    # Stripped from the task's shared context, so no learner ever reads the
    # margin label as part of the question.
    assert page["blocks"][1]["linked_context_orders"] == [1]
    assert "Nationalism in Europe" not in fallback.render_page_acsd_to_mmd(
        normalized)

    flags = [
        flag for flag in page.get("review_flags") or []
        if "navigation" in flag
    ]
    assert len(flags) == 1
    assert flags[0].startswith("PDF-PAGE-0025: ")
    assert "Nationalism in Europe" in flags[0]


def test_an_alias_kind_is_recorded_on_the_page_the_caller_receives():
    """The flag has to survive ``validate_page_extraction``'s rebuild.

    That function composes each returned page dict from scratch and fills
    ``review_flags`` from its own local list, so a flag written onto the
    candidate rows during normalization is discarded before any caller sees it.
    Reconciling the model's ``sidebar`` to ``navigation`` silently would leave
    no record that this contract, and not the model, chose the kind the rest of
    the pipeline reads.
    """
    candidate = _candidate(
        _block(
            kind="paragraph",
            text="A concluding historical paragraph.",
            order=1,
            bbox=[220, 180, 900, 320],
        ),
        _block(
            kind="sidebar",
            text="Nationalism in Europe",
            order=2,
            bbox=[20, 120, 105, 900],
        ),
    )

    normalized, reason = fallback.validate_page_extraction([_page()], candidate)

    assert reason == ""
    assert normalized is not None
    page = normalized["pages"][0]
    assert page["blocks"][1]["kind"] == "navigation"
    flags = page.get("review_flags") or []
    assert any(
        flag == "PDF-PAGE-0025: block kind 'sidebar' recorded as 'navigation'"
        for flag in flags
    ), flags


def test_two_navigation_blocks_on_one_page_leave_one_flag():
    """A repeated margin label must not bury the page's other findings.

    These flags ride in the candidate ``_page_prompt`` hands the independent
    verifier and the corrector. One line per retained block would let a page
    whose label repeats down its edge push a genuine transcription flag out of
    the reviewer's attention, so the page gets exactly one line naming every
    retained text.
    """
    candidate = _candidate(
        _block(
            kind="paragraph",
            text="A concluding historical paragraph.",
            order=1,
            bbox=[220, 180, 900, 320],
        ),
        _block(
            kind="navigation",
            text="Nationalism in Europe",
            order=2,
            bbox=[20, 120, 105, 900],
        ),
        _block(
            kind="navigation",
            text="24",
            order=3,
            bbox=[480, 950, 520, 980],
        ),
    )

    normalized, reason = fallback.validate_page_extraction([_page()], candidate)

    assert reason == ""
    assert normalized is not None
    page = normalized["pages"][0]
    flags = [
        flag for flag in page.get("review_flags") or []
        if "navigation" in flag
    ]
    assert len(flags) == 1
    assert "Nationalism in Europe" in flags[0]
    assert "24" in flags[0]


def test_navigation_source_label_recovers_a_missing_text():
    """The alias kinds never reach the source-cue promotion, so this branch runs.

    ``_canonicalize_source_cue_block`` promotes a labelled block to kind=source
    only from {heading, paragraph, list, other}; no navigation alias is in that
    set, whatever ``source_label`` it carries. The label therefore survives to
    this contract, and a model that put the printed label in ``source_label``
    and left ``text`` empty would otherwise have its wording dropped without a
    record of what the page said.
    """
    candidate = _candidate(
        _block(
            kind="paragraph",
            text="A concluding historical paragraph.",
            order=1,
            bbox=[220, 180, 900, 320],
        ),
        _block(
            kind="running label",
            text="",
            order=2,
            bbox=[20, 120, 105, 900],
            source_label="Nationalism in Europe",
        ),
    )

    normalized, reason = fallback.validate_page_extraction([_page()], candidate)

    assert reason == ""
    assert normalized is not None
    navigation = normalized["pages"][0]["blocks"][1]
    assert navigation["kind"] == "navigation"
    assert navigation["text"] == "Nationalism in Europe"
    assert navigation["source_label"] == ""


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


def test_prompts_carry_both_directions_of_the_navigation_judgment():
    """The geometry rule moved out of the code and into the model's brief.

    With the bbox shape-matcher gone, these three prompts are the whole of the
    navigation judgment, so they must state both directions: what page
    furniture is, and that a margin box which TEACHES is ordinary content. The
    old verification line "Do not reject a candidate solely because such
    navigation is omitted" is deliberately absent — the base extraction
    contract requires every omitted line to come back verbatim in
    ``dropped_furniture`` and tells the verifier to "reject a candidate that
    silently omitted furniture without listing it there", so the exemption
    licensed exactly the silent loss the base rule forbids.
    """
    extraction = fallback._extraction_system_prompt()
    verification = fallback._verification_system_prompt()
    correction = fallback._correction_system_prompt()

    # Extraction: two branches, and the teaching converse.
    assert "kind=navigation" in extraction
    assert "dropped_furniture" in extraction
    assert "does not by itself make a" in extraction
    assert "TEACHES" in extraction

    # Verification: judged in both directions, and no blanket exemption.
    assert "such navigation is omitted" not in verification
    assert "kind=navigation" in verification
    assert "dropped_furniture" in verification
    assert "dropped such a box as furniture" in verification

    # Correction: the same two branches, restated where the model is being
    # told what it got wrong.
    assert "Never introduce kind=sidebar or reading_order=0" in correction
    assert "dropped_furniture" in correction
    assert "margin box that teaches is not navigation" in correction


def test_the_salvage_contract_does_not_relax_the_navigation_rule():
    """Last word wins in a system prompt, and salvage speaks last.

    ``_semantic_salvage`` builds its system prompt as
    ``_extraction_system_prompt() + FINAL SEMANTIC SALVAGE CONTRACT``, so it
    inherits every rule above and then appends its own. Its old closing line,
    "Running headers/footers/navigation may be omitted.", contradicted the
    inherited requirement to record each omitted line in ``dropped_furniture``
    — on the one path that runs after every other attempt has failed.
    """
    source = inspect.getsource(salvage._semantic_salvage)

    assert "Running headers/footers/navigation may be " not in source
    assert "dropped_furniture" in source
    assert "kind=navigation" in source


def test_literal_escape_artifacts_are_scrubbed_from_transcribed_text():
    # Job 13 (3D Shapes): GPT transcribed an in-cell line break as the two
    # literal characters backslash+n ("Pyramid /\nPrism"), which later trips
    # the raw-LaTeX wire-format validator when the table rides along as a
    # task's shared context. Real LaTeX commands beginning with \n, \t, or
    # \r continue in lowercase and must survive untouched.
    table = _block(
        kind="table",
        text="",
        order=1,
        bbox=[100, 100, 900, 400],
    )
    table["table_rows"] = [
        ["Pyramid /\\nPrism", "Triangular"],
        # Lowercase continuation is still an artifact ("\nvertical" is no
        # LaTeX command) — the judge is a command whitelist, not case.
        ["No. of\\nvertical faces", "3"],
        ["Picture", ""],
    ]
    paragraph = _block(
        kind="paragraph",
        text="A concluding historical paragraph with \\neq preserved.",
        order=2,
        bbox=[100, 450, 900, 600],
    )

    normalized, reason = fallback.validate_page_extraction(
        [_page()], _candidate(table, paragraph))

    assert reason == ""
    assert normalized is not None
    page = normalized["pages"][0]
    blocks = page["blocks"]
    scrubbed_table = next(b for b in blocks if b["kind"] == "table")
    assert scrubbed_table["table_rows"][0][0] == "Pyramid / Prism"
    scrubbed_paragraph = next(b for b in blocks if b["kind"] == "paragraph")
    assert "\\neq" in scrubbed_paragraph["text"]
    assert any(
        "literal escape artifact" in flag
        for flag in page.get("review_flags") or []
    )


def test_cached_page_acsd_replay_is_scrubbed_at_consume_time():
    # The content-addressed batch cache replays earlier accepted pages
    # verbatim, bypassing extraction-time validation — so the consumers must
    # normalize again (run 3 replayed run 2's "Pyramid /\nPrism" untouched).
    page_acsd = {
        "pages": [{
            "page_id": "PDF-PAGE-0025",
            "blocks": [{
                "reading_order": 1,
                "kind": "table",
                "bbox": [100, 100, 900, 400],
                "text": "",
                "heading_level": 0,
                "source_label": "",
                "latex": "",
                "table_rows": [
                    ["Pyramid /\\nPrism", "Triangular"],
                    ["No. of\\nvertical faces", "3"],
                ],
                "linked_visual_orders": [],
                "linked_context_orders": [],
                "caption": "",
                "confidence": 0.999,
            }],
        }],
    }

    rendered = fallback.render_page_acsd_to_mmd(page_acsd)

    assert "\\nPrism" not in rendered
    assert "Pyramid / Prism" in rendered

    # The ledger path normalizes the same replayed object before building
    # shared contexts from its table cells.
    fallback._scrub_page_acsd_escape_artifacts(page_acsd)
    rows = page_acsd["pages"][0]["blocks"][0]["table_rows"]
    assert rows[0][0] == "Pyramid / Prism"
    assert rows[1][0] == "No. of vertical faces"
    context = fallback._page_context_text(
        page_acsd["pages"][0]["blocks"][0]
    )
    assert r"\text{Pyramid / Prism} & \text{Triangular}" in context
    assert r"\text{No. of vertical faces} & \text{3}" in context
    assert context.count(r"\begin{array}") == 1

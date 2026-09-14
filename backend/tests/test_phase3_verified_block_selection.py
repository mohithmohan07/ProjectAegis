"""Q74: how a verified PDF page block becomes replacement source text.

Two Rule 1 removals share one commit, each gated on a stamp so a graph that
has already been paid for replays exactly as it was sealed.

* ``SOURCE_FUSION_POLICY_VERSION`` (on the GRAPH) drops the 0.35 token-overlap
  floor that silently deleted correct evidence from the reviewer's shortlist,
  and lets one page block offer more than one faithful rendering.
* ``STRUCTURAL_BASELINE_VERSION`` (on the run's METADATA) replaces the
  ``baseline_role`` verdict — six title regexes deciding what "Activity",
  "Source A" and "Summary" mean — with the printed structure those regexes
  were reading, leaving the role to the hierarchy model and its critic.

The measured failure both of them come from is the same: a threshold or a
vocabulary answering a question only the book can answer.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from app.services import autonomous_resolution
from app.services import build_concepts
from app.services import canonical_source_phase2 as phase2
from app.services import canonical_source_phase22 as phase22
from app.services import canonical_source_phase3 as phase3
from app.services import (
    canonical_source_phase34_structured_output_contract as phase34,
)


DATA = Path(__file__).parents[1] / "data" / "Testing"

# The exact v1 row shape. A candidate row is content-addressed into
# ``_source_review_context_hash`` and re-derived on every reattach, so an
# extra key on a v1 row is a changed decision id on a paid pause.
_V1_CANDIDATE_KEYS = {
    "target_id",
    "page_id",
    "page_number",
    "reading_order",
    "kind",
    "visible_text",
    "resolved_text",
    "resolved_sha256",
    "retrieval_score",
}


def _lattice_source() -> str:
    return (
        "# Salt Structures\n\n"
        "## Ionic Lattices\n\n"
        "Sodium chloride packs as a <smiles>[Na+].[Cl-]</smiles> repeating "
        "unit.\n"
    )


def _lattice_pages() -> dict:
    """One page whose only usable evidence shares no words with the source.

    This is the shape the 0.35 floor was measured on: the sentence that cites
    a cubic-lattice diagram names sodium and chloride, and the diagram's own
    printed caption names neither.
    """

    return {
        "schema_version": "1.1.0",
        "pdf_sha256": "verified-lattice-pdf",
        "pages": [{
            "page_id": "PAGE-0001",
            "page_number": 1,
            "confidence": 0.999,
            "blocks": [
                {
                    "reading_order": 1,
                    "kind": "figure",
                    "text": "",
                    "table_rows": [],
                    "linked_visual_orders": [],
                    "caption": "Fig. 2.4 Cubic lattice",
                    "confidence": 0.999,
                    "asset_url": (
                        "https://aegis.example/source-assets/9/lattice.jpg"
                    ),
                },
            ],
        }],
    }


def _compile(source: str, *, filename: str, chapter_title: str):
    canonical = phase2.compile_phase2_source(
        source,
        source_filename=filename,
        consumer_module="build_concepts",
    ).canonical
    graph, _report = phase3.compile_semantic_graph(
        canonical,
        source_text=source,
        metadata={
            "board": "CBSE",
            "grade": "10",
            "subject": "Science",
            "chapter_title": chapter_title,
            "learning_kind": "Post",
        },
    )
    return canonical, graph


def _block_with(canonical: dict, needle: str) -> dict:
    return next(
        row for row in canonical["blocks"]
        if needle in str(row.get("raw_text") or "")
    )


def _as_v1(graph: dict) -> dict:
    """A graph exactly as one sealed before Q74 reads off disk."""
    sealed = copy.deepcopy(graph)
    sealed.pop(phase3.SOURCE_FUSION_POLICY_KEY, None)
    return sealed


# ---------------------------------------------------------------------------
# Gate A — the graph stamp and the 0.35 floor
# ---------------------------------------------------------------------------


def test_sealed_graph_without_the_stamp_keeps_the_v1_candidate_contract():
    """A pre-Q74 graph must re-derive byte-identical candidate rows.

    Every ``target_id`` and ``resolved_sha256`` on this list is hashed into
    ``_source_review_context_hash``, which is the decision id a reviewer was
    already shown and may already have answered. A row that gains a key, or a
    target that gains a ``#rendering`` suffix, silently invalidates an
    answered pause and re-charges the run for it.
    """

    source = _lattice_source()
    canonical, graph = _compile(
        source, filename="lattice.pdf", chapter_title="Salt Structures")
    block = _block_with(canonical, "<smiles>")
    pages = _lattice_pages()
    source_chars = int(canonical["document"]["source_chars"])

    rows = phase3._source_review_candidate_rows(
        block, page_bundle=pages, source_chars=source_chars, policy_v2=False,
    )
    assert all(set(row) == _V1_CANDIDATE_KEYS for row in rows)
    assert all("#" not in row["target_id"] for row in rows)

    sealed = _as_v1(graph)
    state = phase3._build_source_review_state(
        sealed,
        canonical=canonical,
        page_bundle=pages,
        source_path=None,
        block_id=block["block_id"],
        allow_diagnostic_call=False,
    )
    assert "rejected_candidates" not in state
    # ``reason`` is the one field the diagnostic pass adds after the rows are
    # hashed; everything else is the frozen v1 shape.
    assert all(
        set(row) == _V1_CANDIDATE_KEYS | {"reason"}
        for row in state["candidates"]
    )
    assert state["context_hash"] == phase3._source_review_context_hash(
        sealed,
        item=phase3._source_review_item_context(
            sealed, canonical=canonical, block_id=block["block_id"]),
        candidates=rows,
        pdf_sha256="verified-lattice-pdf",
        revision=None,
    )


def test_cubic_lattice_crop_is_dropped_by_the_overlap_floor_and_kept_under_v2():
    """The 0.35 floor deletes correct evidence, and cannot know it has.

    The sentence cites a diagram; the diagram's caption repeats none of the
    sentence's words. Token overlap is therefore 0, the floor calls that "does
    not match the source block context", and the one page block that could
    have repaired the converter's chemistry markup never reaches the reviewer
    at all. Nothing downstream records the deletion — which is exactly the
    silent loss Rule 1 exists to prevent.
    """

    source = _lattice_source()
    canonical, graph = _compile(
        source, filename="lattice.pdf", chapter_title="Salt Structures")
    block = _block_with(canonical, "<smiles>")
    pages = _lattice_pages()
    page = pages["pages"][0]
    figure = page["blocks"][0]

    with pytest.raises(ValueError, match="does not match the source block"):
        phase3._resolve_verified_page_candidate(
            block, selected_page=page, selected_block=figure, policy_v2=False,
        )

    resolved = phase3._resolve_verified_page_candidate(
        block, selected_page=page, selected_block=figure, policy_v2=True,
    )
    assert resolved == (
        '[img src="https://aegis.example/source-assets/9/lattice.jpg" '
        'alt="Fig. 2.4 Cubic lattice"]'
    )

    source_chars = int(canonical["document"]["source_chars"])
    assert phase3._source_review_candidate_rows(
        block, page_bundle=pages, source_chars=source_chars, policy_v2=False,
    ) == []
    sealable = [
        row for row in phase3._source_review_candidate_rows(
            block, page_bundle=pages, source_chars=source_chars,
            policy_v2=True,
        )
        if row["sealable"]
    ]
    assert [row["target_id"] for row in sealable] == ["PAGE-0001:0001"]
    assert sealable[0]["resolved_text"] == resolved

    # And the same difference end to end: the automatic fusion lane repairs
    # the block on a v2 graph and refuses on a graph sealed before the policy.
    def provider(packet: dict) -> dict:
        return {
            "decision": "use_verified_page_block",
            "selected_block_key": packet["candidate_blocks"][0]["block_key"],
            "confidence": 0.999,
            "reason": "The cited cubic-lattice crop is the source evidence.",
        }

    def critic(payload: dict) -> dict:
        return {
            "verdict": "verified",
            "selected_block_key": payload["selection"]["selected_block_key"],
            "confidence": 0.999,
            "issues": [],
        }

    repaired = phase3.reconcile_source_anomalies(
        copy.deepcopy(graph),
        canonical=canonical,
        page_bundle=pages,
        provider=provider,
        critic=critic,
    )
    assert [row["block_id"] for row in repaired["source_fusion_repairs"]] == [
        block["block_id"]
    ]
    assert "lattice.jpg" in phase3.render_semantic_source(repaired, canonical)

    refused = phase3.reconcile_source_anomalies(
        _as_v1(graph),
        canonical=canonical,
        page_bundle=pages,
        provider=provider,
        critic=critic,
    )
    assert refused["source_fusion_repairs"] == []
    assert "<smiles>" in phase3.render_semantic_source(refused, canonical)


def test_unsealable_rendering_is_named_evidence_and_never_a_candidate():
    """An unusable page block is shown and explained, not silently dropped.

    Under v1 a page block whose own transcription violates the rich-text
    contract simply vanished from the shortlist, so the reviewer saw "no safe
    candidate is available" with no way to learn why. Under v2 it comes back
    as a NAMED rejection carrying its defect codes — and stays out of
    ``candidates``, which is the only list ``autonomous_resolution`` may pick
    from, so honesty here can never widen what an unattended run applies.

    Q24 must still fire on this pending: carrying non-canonical rich text
    forward is the measured dead end, and naming the evidence does not make
    the source usable.
    """

    source = "# Review Chapter\n\n## Verified Topic\n\nSource item 1 has $x+1\n"
    canonical, graph = _compile(
        source, filename="review.pdf", chapter_title="Review Chapter")
    block = _block_with(canonical, "$x+1")
    pages = {
        "schema_version": "1.1.0",
        "pdf_sha256": "verified-pdf",
        "pages": [{
            "page_id": "PAGE-0001",
            "page_number": 1,
            "confidence": 0.999,
            "blocks": [{
                "reading_order": 1,
                "kind": "paragraph",
                # The page transcription carries the same unclosed delimiter,
                # so it can never be sealed — but it IS the right page block.
                "text": "Source item 1 has $x+1",
                "table_rows": [],
                "confidence": 0.999,
            }],
        }],
    }
    source_chars = int(canonical["document"]["source_chars"])

    assert phase3._source_review_candidate_rows(
        block, page_bundle=pages, source_chars=source_chars, policy_v2=False,
    ) == []
    rows = phase3._source_review_candidate_rows(
        block, page_bundle=pages, source_chars=source_chars, policy_v2=True,
    )
    assert [row["sealable"] for row in rows] == [False]
    assert rows[0]["target_id"] == "PAGE-0001:0001"
    assert rows[0]["defects"] == ["raw_math_delimiter"]
    assert rows[0]["resolved_sha256"] == ""

    state = phase3._build_source_review_state(
        graph,
        canonical=canonical,
        page_bundle=pages,
        source_path=None,
        block_id=block["block_id"],
        allow_diagnostic_call=False,
    )
    assert state["candidates"] == []
    assert [row["defects"] for row in state["rejected_candidates"]] == [
        ["raw_math_delimiter"]
    ]

    pending = phase3._source_review_pending(
        graph, state, canonical=canonical)
    assert pending["candidates"] == []
    assert not any(
        row["choice"] == "select_candidate" for row in pending["options"]
    )
    assert any(
        row["label"].startswith("PAGE-0001:0001 — not applicable: ")
        and "raw_math_delimiter" in row["label"]
        for row in pending["evidence"]
    )
    assert len(pending["evidence"]) <= 100

    assert pending["item"]["type_id"] == "semantic_source_rich_text"
    assert build_concepts._is_dead_end_rich_text_pending(pending)
    safe = autonomous_resolution.safe_continuation_option(pending)
    assert safe is not None
    assert safe["choice"] == autonomous_resolution.CARRY_FORWARD_CHOICE


def test_linked_visual_rendering_applies_end_to_end_and_rejects_are_closed():
    """A second faithful reading of one page block must be selectable.

    A paragraph that carries the converter's markup and links a figure has
    two honest repairs: its own transcription, and the figure it cites. Under
    v1 only the first had an id, so the second could not be chosen at all. The
    composite target must survive the whole human-decision path — the page
    lookup takes the block key, the rendering is re-derived, and the override
    records which rendering was applied, or a later revalidation would
    re-derive the wrong bytes and refuse a decision that was correct.

    A resolution naming a REJECTED rendering must still fail closed: naming a
    row is not the same as that row being applicable.
    """

    source = _lattice_source()
    canonical, graph = _compile(
        source, filename="lattice.pdf", chapter_title="Salt Structures")
    block = _block_with(canonical, "<smiles>")
    pages = _lattice_pages()
    pages["pages"][0]["blocks"].insert(0, {
        "reading_order": 0,
        "kind": "paragraph",
        "text": "Sodium chloride packs as a $lattice repeating unit.",
        "table_rows": [],
        # The page-qualified link the extraction wrote, not the page-local
        # ``linked_visual_orders`` guess it may have left stale.
        "linked_visual_refs": [{"page_id": "PAGE-0001", "reading_order": 1}],
        "confidence": 0.999,
    })
    graph["vision_evidence"] = {"pdf_sha256": "verified-lattice-pdf"}

    state = phase3._build_source_review_state(
        graph,
        canonical=canonical,
        page_bundle=pages,
        source_path=None,
        block_id=block["block_id"],
        allow_diagnostic_call=False,
    )
    targets = [row["target_id"] for row in state["candidates"]]
    assert "PAGE-0001:0000#visual-0001" in targets
    # The paragraph's OWN transcription carries a raw delimiter, so its
    # default rendering is a named rejection rather than a silent absence.
    rejected = {row["target_id"] for row in state["rejected_candidates"]}
    assert "PAGE-0001:0000" in rejected

    applied = phase3._apply_human_source_candidate(
        copy.deepcopy(graph),
        state,
        canonical=canonical,
        page_bundle=pages,
        target_id="PAGE-0001:0000#visual-0001",
    )
    override = next(
        row["source_override"] for row in applied["blocks"]
        if row["block_id"] == block["block_id"]
    )
    assert override["selected_block_key"] == "PAGE-0001:0000#visual-0001"
    assert override["rendering_id"] == "visual-0001"
    assert override["page_id"] == "PAGE-0001"
    assert override["reading_order"] == 0
    assert "lattice.jpg" in override["resolved_text"]
    assert applied["source_fusion_repairs"][-1]["rendering_id"] == "visual-0001"
    assert "lattice.jpg" in phase3.render_semantic_source(applied, canonical)
    # The stored composite override survives a reattach unchanged.
    assert phase3._current_human_source_overrides_valid(
        applied, canonical=canonical, page_bundle=pages)

    with pytest.raises(ValueError, match="not a supplied candidate"):
        phase3._apply_human_source_candidate(
            copy.deepcopy(graph),
            state,
            canonical=canonical,
            page_bundle=pages,
            target_id="PAGE-0001:0000",
        )

    # And a v1 graph refuses a composite target outright rather than reading
    # it as a block key that no longer exists ("the PDF changed").
    with pytest.raises(ValueError, match="was not compiled to apply"):
        phase3._apply_human_source_candidate(
            _as_v1(graph),
            state,
            canonical=canonical,
            page_bundle=pages,
            target_id="PAGE-0001:0000#visual-0001",
        )


def test_default_rendering_is_first_and_keeps_the_bare_block_key():
    """Identity rule: the default rendering must never gain a suffix."""

    pages = _lattice_pages()
    page = pages["pages"][0]
    page["blocks"].insert(0, {
        "reading_order": 0,
        "kind": "paragraph",
        "text": "Sodium chloride packs as a repeating unit.",
        "table_rows": [],
        "linked_visual_refs": [
            {"page_id": "PAGE-0001", "reading_order": 1},
            # A duplicate and a cross-page ref must not mint extra ids here.
            {"page_id": "PAGE-0001", "reading_order": 1},
            {"page_id": "PAGE-0009", "reading_order": 3},
        ],
        "confidence": 0.999,
    })
    renderings = phase3._verified_page_renderings(page, page["blocks"][0])

    assert [identifier for identifier, _row in renderings] == [
        "", "visual-0001",
    ]
    assert renderings[0][1] is page["blocks"][0]
    assert phase3._verified_target_id("PAGE-0001:0000", "") == "PAGE-0001:0000"
    assert phase3._split_verified_target("PAGE-0001:0000") == (
        "PAGE-0001:0000", "",
    )
    assert phase3._split_verified_target("PAGE-0001:0000#visual-0001") == (
        "PAGE-0001:0000", "visual-0001",
    )


def test_a_rendering_row_shows_the_block_it_renders_not_the_one_it_cites():
    """Each candidate row must describe the evidence it would actually apply.

    A page block and the figure it cites are two candidates with ONE parent
    key. Reading ``kind`` and ``visible_text`` off the parent gives the
    reviewer two rows that both read "PDF page 1 · paragraph" over the same
    OCR text, distinguishable only by an opaque id — and pays for that
    duplicate text again in the one bounded diagnosis request, once per
    rendering, up to 8 KB each.

    What identifies the row stays the parent block, because
    ``_apply_human_source_candidate`` re-verifies page_id/page_number/
    reading_order against it before applying anything.
    """

    source = _lattice_source()
    canonical, graph = _compile(
        source, filename="lattice.pdf", chapter_title="Salt Structures")
    block = _block_with(canonical, "<smiles>")
    pages = _lattice_pages()
    pages["pages"][0]["blocks"].insert(0, {
        "reading_order": 0,
        "kind": "paragraph",
        "text": "Sodium chloride packs as a repeating unit.",
        "table_rows": [],
        "linked_visual_refs": [{"page_id": "PAGE-0001", "reading_order": 1}],
        "confidence": 0.999,
    })
    source_chars = int(canonical["document"]["source_chars"])

    rows = {
        row["target_id"]: row
        for row in phase3._source_review_candidate_rows(
            block, page_bundle=pages, source_chars=source_chars,
            policy_v2=True,
        )
    }
    parent = rows["PAGE-0001:0000"]
    visual = rows["PAGE-0001:0000#visual-0001"]

    assert parent["kind"] == "paragraph"
    assert parent["visible_text"] == (
        "Sodium chloride packs as a repeating unit."
    )
    # The figure's own kind and its own printed caption — not the citing
    # paragraph's transcription a second time.
    assert visual["kind"] == "figure"
    assert visual["visible_text"] == "Fig. 2.4 Cubic lattice"
    assert visual["visible_text"] != parent["visible_text"]
    # The identity the apply path re-verifies is still the parent page block.
    assert visual["page_id"] == parent["page_id"]
    assert visual["page_number"] == parent["page_number"]
    assert visual["reading_order"] == parent["reading_order"] == 0

    # And the reviewer's own list says which is which.
    state = phase3._build_source_review_state(
        graph,
        canonical=canonical,
        page_bundle=pages,
        source_path=None,
        block_id=block["block_id"],
        allow_diagnostic_call=False,
    )
    titles = {
        row["target_id"]: row["title"]
        for row in phase3._source_review_pending(
            graph, state, canonical=canonical)["candidates"]
    }
    assert titles["PAGE-0001:0000"] == "PDF page 1 · paragraph"
    assert titles["PAGE-0001:0000#visual-0001"] == "PDF page 1 · figure"


def test_a_named_rendering_may_answer_a_table_anomaly_the_default_cannot():
    """Pin the widening the composite target introduces, deliberately.

    The ``table_requires_verified_table_block`` gate guards the DEFAULT
    rendering: "you asked to replace a table with this block's own
    transcription, and this block is not a table". A NAMED rendering is a
    different verified block on the same page, chosen on purpose, and
    contract §11/Q38 want exactly that — one complete faithful image — where
    a table cannot be transcribed. Nothing else about the widening is silent:
    v1 still refuses the composite target outright, and the rich-text
    contract still runs on whatever the rendering produces.
    """

    canonical_block = {
        "block_id": "BLK-00001",
        "kind": "table",
        "raw_text": "| Metal | Reactivity |\n| --- | --- |\n| Sodium | High |",
    }
    page = _lattice_pages()["pages"][0]
    page["blocks"].insert(0, {
        "reading_order": 0,
        "kind": "paragraph",
        "text": "Table 2.1 lists the metals in order of reactivity.",
        "table_rows": [],
        "linked_visual_refs": [{"page_id": "PAGE-0001", "reading_order": 1}],
        "confidence": 0.999,
    })
    paragraph = page["blocks"][0]

    with pytest.raises(ValueError, match="verified table block"):
        phase3._resolve_verified_page_candidate(
            canonical_block,
            selected_page=page,
            selected_block=paragraph,
            policy_v2=True,
        )

    resolved = phase3._resolve_verified_page_candidate(
        canonical_block,
        selected_page=page,
        selected_block=paragraph,
        rendering_id="visual-0001",
        policy_v2=True,
    )
    assert resolved == (
        '[img src="https://aegis.example/source-assets/9/lattice.jpg" '
        'alt="Fig. 2.4 Cubic lattice"]'
    )

    with pytest.raises(ValueError, match="requires the verified-block"):
        phase3._resolve_verified_page_candidate(
            canonical_block,
            selected_page=page,
            selected_block=paragraph,
            rendering_id="visual-0001",
            policy_v2=False,
        )


def test_only_sealable_candidates_carry_a_page_into_the_bounded_diagnosis(
    monkeypatch: pytest.MonkeyPatch,
):
    """A named rejection costs prose, never another rendered page image.

    ``_diagnose_source_review_via_openai`` renders one 2x JPEG per DISTINCT
    candidate page number, and the page images are the expensive half of that
    one bounded request. Widening the shortlist with named rejections must
    therefore never widen the image set: rejected rows are evidence in the
    prompt, and only ``candidates`` — the rows a resolver may actually pick —
    decide which pages are rendered.
    """

    payload = {
        "candidates": [
            {"target_id": "PAGE-0001:0001", "page_number": 1,
             "kind": "figure", "visible_text": "Fig. 2.4 Cubic lattice"},
        ],
        "rejected_candidates": [
            {"target_id": f"PAGE-{number:04d}:0001", "page_number": number,
             "kind": "paragraph", "visible_text": "unsealable",
             "defects": ["raw_math_delimiter"]}
            for number in range(2, 8)
        ],
    }
    seen: dict = {}

    def fake_call(*, system: str, prompt: str, pages, **kwargs):
        seen["pages"] = list(pages)
        seen["prompt"] = prompt
        return {
            "diagnosis": "d", "question": "q",
            "recommended_target_id": "NONE", "candidate_reasons": [],
        }

    rendered: list[int] = []

    def fake_pages(_source_path, page_numbers):
        rendered.extend(sorted({int(value) for value in page_numbers}))
        return [object() for _ in rendered]

    monkeypatch.setattr(phase22, "_openai_multimodal_json", fake_call)
    monkeypatch.setattr(phase3, "_anomaly_evidence_pages", fake_pages)

    pdf = Path(__file__).parent / "_fake_source.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    try:
        phase3._diagnose_source_review_via_openai(payload, source_path=pdf)
    finally:
        pdf.unlink()

    assert rendered == [1]
    # The rejected evidence still reaches the diagnostician as text.
    assert "raw_math_delimiter" in seen["prompt"]


# ---------------------------------------------------------------------------
# Gate B — the metadata stamp and the title vocabularies
# ---------------------------------------------------------------------------


def _payload_sections(metadata: dict) -> list[dict]:
    source = "# Review Chapter\n\n## 1.1 Verified Topic\n\nBody text here.\n"
    canonical = phase2.compile_phase2_source(
        source,
        source_filename="review.mmd",
        consumer_module="build_concepts",
    ).canonical
    seen: dict = {}

    def classify(payload: dict) -> dict:
        seen["payload"] = copy.deepcopy(payload)
        return {"sections": [{
            "section_id": row["section_id"],
            "role": "main_topic",
            "parent_section_id": "",
            "confidence": 0.999,
            "evidence": ["verified test hierarchy"],
        } for row in payload["sections"]]}

    phase3.compile_semantic_graph(
        canonical,
        source_text=source,
        metadata={"chapter_title": "Review Chapter", **metadata},
        hierarchy_provider=classify,
    )
    return seen["payload"]


def test_unstamped_classification_payload_does_not_move_one_byte():
    """The hierarchy cache key is ``_sha256_json`` of this payload.

    Phase 3.4 caches an authored batch under ``payload_sha256``. If the
    unstamped payload changed at all, every warm chapter would silently
    re-pay for a classifier and a critic call per batch for a policy it is
    not even running.
    """

    payload = _payload_sections({})
    assert all("baseline_role" in row for row in payload["sections"])
    assert all(
        "structural_evidence" not in row for row in payload["sections"]
    )
    assert phase3._sha256_json(payload) == phase3._sha256_json(
        _payload_sections({})
    )


def test_stamped_payload_trades_the_role_verdict_for_printed_structure():
    """Under the stamp the model sees the evidence, not the regex verdict.

    ``_baseline_section_role`` answers "is this a main topic?" with six title
    vocabularies. Those vocabularies still exist for the offline no-API
    baseline, but a run that HAS a hierarchy model must not hand it a
    pre-cooked answer: what goes across is the printed number, the heading
    kind, the depth and whether the heading repeats the chapter title.
    """

    payload = _payload_sections({
        phase3.STRUCTURAL_BASELINE_KEY: phase3.STRUCTURAL_BASELINE_VERSION,
    })
    assert all("baseline_role" not in row for row in payload["sections"])
    evidence = {
        row["title"]: row["structural_evidence"]
        for row in payload["sections"]
    }
    assert evidence["Review Chapter"]["title_matches_chapter_title"] is True
    assert evidence["Review Chapter"]["structural_number"] == ""
    assert evidence["Verified Topic"]["structural_number"] == "1.1"
    assert set(evidence["Verified Topic"]) == {
        "numbered_main",
        "numbered_sub",
        "structural_number",
        "heading_kind",
        "title_matches_chapter_title",
        "level",
    }


def test_structural_evidence_never_passes_off_a_parser_verdict_as_printing():
    """``numbered_main`` may only mean "the page prints this number".

    An unnumbered chapter has no numbered-heading inventory, so
    ``compile_semantic_graph`` falls back to
    ``generation._topic_headings`` — a shallowest-level-with-enough-sections
    rule that here calls "Activity 1" a main topic. That verdict is exactly
    what the stamp removes. Sending it across as ``numbered_main: true`` with
    an empty ``structural_number`` would put it back with the prompt
    vouching for it: both sentences tell the model the field is "the number
    printed on its heading".

    The recorded baseline is untouched — offline compiles and every sealed
    run still read it.
    """

    source = (
        "# Water In Our Lives\n\n"
        "## Where Water Comes From\n\n"
        "Rain fills the ponds and wells.\n\n"
        "## Activity 1\n\n"
        "Draw a water pot.\n"
    )
    canonical = phase2.compile_phase2_source(
        source,
        source_filename="water.mmd",
        consumer_module="build_concepts",
    ).canonical
    seen: dict = {}

    def classify(payload: dict) -> dict:
        seen.setdefault("payloads", []).append(copy.deepcopy(payload))
        return {"sections": [{
            "section_id": row["section_id"],
            "role": "main_topic",
            "parent_section_id": "",
            "confidence": 0.999,
            "evidence": ["verified test hierarchy"],
        } for row in payload["sections"]]}

    for metadata in (
        {},
        {phase3.STRUCTURAL_BASELINE_KEY: phase3.STRUCTURAL_BASELINE_VERSION},
    ):
        phase3.compile_semantic_graph(
            canonical,
            source_text=source,
            metadata={"chapter_title": "Water In Our Lives", **metadata},
            hierarchy_provider=classify,
        )
    legacy, stamped = seen["payloads"]

    # The parser's verdict, exactly as v1 recorded it.
    assert {
        row["title"]: row["baseline_role"] for row in legacy["sections"]
    } == {
        "Water In Our Lives": "chapter_heading",
        "Where Water Comes From": "main_topic",
        "Activity 1": "main_topic",
    }

    # And nothing of it in the evidence, because the page prints no numbers.
    evidence = {
        row["title"]: row["structural_evidence"]
        for row in stamped["sections"]
    }
    assert [row["numbered_main"] for row in evidence.values()] == [
        False, False, False,
    ]
    assert [row["structural_number"] for row in evidence.values()] == [
        "", "", "",
    ]


def test_phase34_carries_the_projection_it_was_given_and_invents_no_role():
    """The batch projection must not put the vocabulary back.

    ``_section_directory`` and ``_section_evidence`` both defaulted a missing
    ``baseline_role`` to ``"other"``. Left alone, that would re-inject the
    exact verdict the stamp removes — and on the evidence row, where it is
    hardest to notice.
    """

    stamped = _payload_sections({
        phase3.STRUCTURAL_BASELINE_KEY: phase3.STRUCTURAL_BASELINE_VERSION,
    })
    sections = phase34._ordered_payload_sections(stamped)
    target = [sections[0]["section_id"]]

    directory = phase34._section_directory(sections)
    assert all("baseline_role" not in row for row in directory)
    assert all("structural_evidence" in row for row in directory)

    evidence = phase34._section_evidence(sections, target)
    assert evidence
    assert all("baseline_role" not in row for row in evidence)
    assert all("structural_evidence" in row for row in evidence)

    legacy = _payload_sections({})
    legacy_sections = phase34._ordered_payload_sections(legacy)
    legacy_directory = phase34._section_directory(legacy_sections)
    assert all(row["baseline_role"] for row in legacy_directory)
    assert all(
        "structural_evidence" not in row for row in legacy_directory
    )


@pytest.mark.parametrize("kind", ["classifier", "critic"])
def test_batched_prompts_gain_the_sentence_only_with_the_payload_key(
    kind: str, monkeypatch: pytest.MonkeyPatch,
):
    """Prompt and cache key must move together.

    ``_cache_key`` hashes ``payload_sha256`` and never the system string, so a
    prompt edit alone is served straight out of a stale cache entry that was
    authored under the old wording. Conditioning the sentence on the payload
    key ties the two: the same payload that carries ``structural_evidence``
    is the payload whose hash is new.
    """

    systems: list[str] = []

    def fake_call(*, system: str, prompt: str, **kwargs):
        systems.append(system)
        payload = json.loads(prompt)
        rows = [
            {
                "section_id": section_id,
                "role": "main_topic",
                "parent_section_id": "",
                "confidence": 0.999,
                "evidence": ["verified test hierarchy"],
            }
            for section_id in payload["target_section_ids"]
        ]
        if kind == "classifier":
            return {"sections": rows}
        return {
            "verdict": "verified",
            "confidence": 0.999,
            "repairs": [],
            "issues": [],
        }

    monkeypatch.setattr(phase22, "_openai_multimodal_json", fake_call)
    run = (
        phase34._classify_hierarchy_batched
        if kind == "classifier" else phase34._critic_hierarchy_batched
    )
    sentence = (
        phase34._STRUCTURAL_EVIDENCE_CLASSIFIER_RULE
        if kind == "classifier" else phase34._STRUCTURAL_EVIDENCE_CRITIC_RULE
    )

    legacy = {
        "metadata": {},
        "sections": [{
            "section_id": "SEC-0001",
            "title": "Verified Topic",
            "level": 1,
            "source_order": 1,
            "source_start": 0,
            "baseline_role": "main_topic",
            "excerpt": "Body text here.",
        }],
        "proposed_hierarchy": [{
            "section_id": "SEC-0001",
            "role": "main_topic",
            "parent_section_id": "",
            "confidence": 0.999,
            "evidence": ["proposed"],
        }],
    }
    stamped = copy.deepcopy(legacy)
    stamped["sections"][0].pop("baseline_role")
    stamped["sections"][0]["structural_evidence"] = {
        "numbered_main": True,
        "numbered_sub": False,
        "structural_number": "1",
        "heading_kind": "markdown",
        "title_matches_chapter_title": False,
        "level": 1,
    }

    run(legacy)
    assert systems and sentence not in systems[-1]
    run(stamped)
    assert sentence in systems[-1]

    # And the key material moved with the wording. Had the sentence been added
    # unconditionally, a warm chapter would have replayed the OLD prompt's
    # cached answer under the new one and nothing would have said so.
    build = (
        phase34._classification_payload_for_batch
        if kind == "classifier" else phase34._critic_payload_for_batch
    )
    assert phase34._cache_key(
        kind=kind, payload=build(legacy, ["SEC-0001"]),
        target_ids=["SEC-0001"],
    ) != phase34._cache_key(
        kind=kind, payload=build(stamped, ["SEC-0001"]),
        target_ids=["SEC-0001"],
    )


def _collapsed_rne_graph() -> tuple[dict, dict]:
    """An RNE graph whose only error is the numbered-topic coverage one.

    That is exactly the precondition ``_numbered_topic_patch_preview`` demands
    before it will rebuild the topic spine.
    """

    source = (DATA / "RNE.mmd").read_text(encoding="utf-8")
    canonical = phase2.compile_phase2_source(
        source,
        source_filename="RNE.mmd",
        consumer_module="build_concepts",
    ).canonical
    graph, _report = phase3.compile_semantic_graph(
        canonical,
        source_text=source,
        metadata={
            "subject": "History",
            "chapter_title": "The Rise of Nationalism in Europe",
        },
    )
    collapsed = copy.deepcopy(graph)
    removed = next(
        row for row in collapsed["topics"]
        if row["title"] == "The Making of Nationalism in Europe"
    )
    replacement = collapsed["topics"][0]["topic_id"]
    collapsed["topics"] = [
        row for row in collapsed["topics"]
        if row["topic_id"] != removed["topic_id"]
    ]
    for collection in ("blocks", "tasks", "subtopics", "sections"):
        for row in collapsed.get(collection) or []:
            if row.get("topic_id") == removed["topic_id"]:
                row["topic_id"] = replacement
    collapsed["semantic_source_sha256"] = phase3._sha256_text(
        phase3.render_semantic_source(collapsed, canonical)
    )
    return canonical, collapsed


def test_topic_spine_rebuild_replays_recorded_roles_without_a_baseline(
    monkeypatch: pytest.MonkeyPatch,
):
    """The topic-spine patch replays a hierarchy; it never re-classifies.

    Its provider used to fall back to the payload's ``baseline_role`` for any
    section it had no recorded role for. Under the stamp there is no
    ``baseline_role`` at all — and the old fallback value, ``"other"``, is the
    one role ``render_semantic_source`` SUPPRESSES. A rebuild whose entire
    contract is "preserve every source-owned identity" would have deleted the
    very heading it was rebuilding, and the identity-inventory guard does not
    cover section ROLES.

    So: the recorded role wins, and a section with no recorded role becomes a
    neutral ``content_heading`` whether or not a stale ``baseline_role`` is
    sitting in the payload saying "other". The one exception — the structural
    binding the patch exists to restore — is pinned by the test below.
    """

    canonical, collapsed = _collapsed_rne_graph()
    collapsed["metadata"] = {
        **collapsed["metadata"],
        phase3.STRUCTURAL_BASELINE_KEY: phase3.STRUCTURAL_BASELINE_VERSION,
    }
    captured: dict = {}

    class _Stop(RuntimeError):
        pass

    def fake_compile(_canonical, **kwargs):
        captured["metadata"] = kwargs["metadata"]
        captured["provider"] = kwargs["hierarchy_provider"]
        raise _Stop()

    monkeypatch.setattr(phase3, "compile_semantic_graph", fake_compile)
    with pytest.raises(_Stop):
        phase3._numbered_topic_patch_preview(
            collapsed, canonical=canonical, page_bundle=None)

    assert captured["metadata"][phase3.STRUCTURAL_BASELINE_KEY] == (
        phase3.STRUCTURAL_BASELINE_VERSION
    )
    recorded = next(
        row for row in collapsed["sections"] if row["role"] == "main_topic"
    )
    unknown = "PHASE3-NUMBERED-0099-NOTINGRAPH"

    for stale_baseline in ({"baseline_role": "other"}, {}):
        replayed = captured["provider"]({"sections": [
            {"section_id": recorded["section_id"], "title": recorded["title"],
             **stale_baseline},
            {"section_id": unknown, "title": "Recovered heading",
             **stale_baseline},
        ]})
        roles = {
            row["section_id"]: row["role"] for row in replayed["sections"]
        }
        assert roles[recorded["section_id"]] == "main_topic"
        assert roles[unknown] == "content_heading"


def test_topic_spine_rebuild_replays_the_binding_it_has_no_recorded_role_for(
    monkeypatch: pytest.MonkeyPatch,
):
    """The one thing the rebuild must NOT forget about a new section.

    A payload section with no recorded role is one the rebuild has just
    materialized: a ``PHASE3-NUMBERED-`` projection standing in for a numbered
    main heading whose block carries a stale section id, or a virtual parent
    restored from verified page evidence. Those ARE the repair's subject.
    Neutralising them to ``content_heading`` drops the numbered main topic the
    patch was built to restore — the payload says so in the same breath, in
    ``baseline_role`` on an unstamped run and in ``structural_evidence`` under
    the stamp, and the rebuild reads neither.

    ``_forced_structural_roles`` happens to re-force a numbered binding
    afterwards on a graph that is not policy-bound, which is why this never
    showed as a failure — but it does not cover a restored virtual parent, it
    does not run at all once the graph owns its own topics, and a replay that
    throws away what it was handed is a trap either way.
    """

    canonical, collapsed = _collapsed_rne_graph()
    captured: dict = {}

    class _Stop(RuntimeError):
        pass

    def fake_compile(_canonical, **kwargs):
        captured["provider"] = kwargs["hierarchy_provider"]
        raise _Stop()

    monkeypatch.setattr(phase3, "compile_semantic_graph", fake_compile)
    with pytest.raises(_Stop):
        phase3._numbered_topic_patch_preview(
            collapsed, canonical=canonical, page_bundle=None)

    projection = "PHASE3-NUMBERED-0002-NOTINGRAPH"
    numbered = {"numbered_main": True, "numbered_sub": False,
                "structural_number": "2", "heading_kind": "markdown",
                "title_matches_chapter_title": False, "level": 1}
    sub = {**numbered, "numbered_main": False, "numbered_sub": True,
           "structural_number": "2.1", "level": 2}

    for row, expected in (
        ({"baseline_role": "main_topic"}, "main_topic"),
        ({"baseline_role": "subtopic"}, "subtopic"),
        ({"structural_evidence": numbered}, "main_topic"),
        ({"structural_evidence": sub}, "subtopic"),
        # Anything else stays neutral: "other" is the render-suppressing role
        # and no vocabulary verdict may be replayed onto a new section.
        ({"baseline_role": "activity"}, "content_heading"),
        ({"structural_evidence": {**numbered, "numbered_main": False}},
         "content_heading"),
    ):
        replayed = captured["provider"]({"sections": [
            {"section_id": projection, "title": "Recovered heading", **row},
        ]})
        assert [
            value["role"] for value in replayed["sections"]
        ] == [expected], row


# ---------------------------------------------------------------------------
# The offline corpus: neither stamp may move a single byte of it
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "filename,subject,chapter_title,contract_hash",
    [
        (
            "RNE.mmd", "History", "The Rise of Nationalism in Europe",
            "6ee17db639ef680984215487f0e74ee2a335da08c836eae9e5f8489312dc1891",
        ),
        (
            "jemh105 (1).mmd", "Mathematics", "Arithmetic Progressions",
            "75679bc60f7a3f6d38d4ecf715ddb35aea9ee1439bb3fd3cea7930423d293670",
        ),
        (
            "Class 10 Chapter 5 Electricity.mmd", "Science", "Electricity",
            "86677f1237615fdf03e488e512ee36267f3da559d8d4350b082895eda8c53b6f",
        ),
    ],
)
def test_offline_corpus_renders_identically_with_and_without_the_stamp(
    filename: str, subject: str, chapter_title: str, contract_hash: str,
):
    """No graph stamp may reach the deterministic source the authors read.

    ``SOURCE_FUSION_POLICY_KEY`` now rides every freshly compiled graph, and
    ``render_semantic_source`` takes that graph. If the stamp changed the
    rendering by so much as a space, a resumed run's byte-equality against its
    saved ``source.semantic.mmd`` would fail and the chapter would recompile —
    which, for a PDF upload, means reading every page again.
    """

    source = (DATA / filename).read_text(encoding="utf-8")
    canonical = phase2.compile_phase2_source(
        source,
        source_filename=filename,
        consumer_module="build_concepts",
    ).canonical
    graph, _report = phase3.compile_semantic_graph(
        canonical,
        source_text=source,
        metadata={"subject": subject, "chapter_title": chapter_title},
    )

    assert graph[phase3.SOURCE_FUSION_POLICY_KEY] == (
        phase3.SOURCE_FUSION_POLICY_VERSION
    )
    assert graph["source_contract_hash"] == contract_hash
    assert phase3.render_semantic_source(
        graph, canonical
    ) == phase3.render_semantic_source(_as_v1(graph), canonical)
    # An offline compile has no hierarchy model, so it never freezes the
    # structural-baseline stamp and keeps the recorded title-vocabulary
    # baseline exactly as it was.
    assert phase3.STRUCTURAL_BASELINE_KEY not in graph["metadata"]
    assert graph["semantic_context_hash"] == phase3.semantic_context_hash({
        "subject": subject,
        "chapter_title": chapter_title,
        phase3.STRUCTURAL_BASELINE_KEY: phase3.STRUCTURAL_BASELINE_VERSION,
    })

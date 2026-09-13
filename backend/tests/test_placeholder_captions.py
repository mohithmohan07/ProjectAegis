"""Q72: no placeholder caption is minted into the source the authors read,
and a repeated image URL inside one row is a visible warning.

``_markdown_image`` wrote ``![Source visual](url)`` for every uncaptioned
figure, and the flat parser recorded that literal as the figure's printed
caption; ``render_semantic_source`` printed "Source visual" as a standalone
caption line whenever the printed caption was empty. Both stop for NEW work:
the MMD keeps an empty alt (the Mathpix shape the parser already reads) and
a graph stamp gates the renderer so every sealed graph renders the line it
was sealed with.
"""
from __future__ import annotations

import copy

from app.services import canonical_source_phase2 as phase2
from app.services import canonical_source_phase221_fallback as fallback
from app.services import canonical_source_phase3 as phase3
from app.services import concept_validator as cv

SOURCE = """# Reproduction

## Flowers

A flower has four whorls.

![](https://cdn.mathpix.com/uncaptioned.jpg)

The sepals protect the bud.

Figure 7.7 Longitudinal section of a flower

![Figure 7.7 Longitudinal section of a flower](https://cdn.mathpix.com/captioned.jpg)

Exercise 1. Name the whorls of a flower.
"""


def _compile():
    canonical = phase2.compile_phase2_source(
        SOURCE, source_filename="inline.mmd", consumer_module="build_concepts",
    ).canonical
    graph, _report = phase3.compile_semantic_graph(
        canonical, source_text=SOURCE,
        metadata={"subject": "Science", "chapter_title": "Reproduction", "board": "CBSE"},
    )
    return canonical, graph


def test_the_mmd_keeps_an_empty_alt_for_an_uncaptioned_figure():
    assert fallback._markdown_image("https://x.test/f.png", "") == "![](https://x.test/f.png)"
    assert fallback._markdown_image("https://x.test/f.png", None) == "![](https://x.test/f.png)"
    assert fallback._markdown_image("https://x.test/f.png", "Fig. 7.7  A [flower]") == (
        "![Fig. 7.7 A (flower)](https://x.test/f.png)"
    )
    # The reader stamp is deliberately not bumped (the owner's question).
    assert fallback.RENDER_VERSION == "task-cues-verbatim-full-table-assets-2"


def test_a_new_graph_prints_no_caption_line_for_an_unprinted_caption():
    canonical, graph = _compile()
    assert graph[phase3.FIGURE_CAPTION_RENDER_KEY] == phase3.FIGURE_CAPTION_RENDER_VERSION
    rendered = phase3.render_semantic_source(graph, canonical)
    lines = [line.strip() for line in rendered.splitlines()]
    assert "Source visual" not in lines
    assert "https://cdn.mathpix.com/uncaptioned.jpg" in rendered
    assert "Figure 7.7 Longitudinal section of a flower" in rendered
    # A sealed graph (no stamp) renders the line it was sealed with, so the
    # resume path's byte-equality against the stored semantic source holds.
    sealed = copy.deepcopy(graph)
    sealed.pop(phase3.FIGURE_CAPTION_RENDER_KEY)
    legacy = phase3.render_semantic_source(sealed, canonical)
    assert "Source visual" in [line.strip() for line in legacy.splitlines()]
    assert legacy != rendered
    # The stamp is outside the semantic context hash: same source identity.
    assert graph["semantic_context_hash"] == phase3.compile_semantic_graph(
        canonical, source_text=SOURCE,
        metadata={"subject": "Science", "chapter_title": "Reproduction", "board": "CBSE"},
    )[0]["semantic_context_hash"]


def test_a_repeated_image_url_in_one_row_is_a_warning_never_a_drop():
    tag = '[img src="https://x.test/f77.png" alt="Fig. 7.7 Longitudinal section"]'
    row = {
        "topic": "Flowers", "parent_concept": "Reproduction",
        "concept_title": "Parts of a Flower",
        "concept_details": (
            "Description: A flower has four whorls. " + tag +
            "\nAchieving Mastery: Name each whorl. // Activity/ Info Hub: " + tag
        ),
        "keywords": "flower",
    }
    report = cv.validate_concept_rows([row])
    findings = [e for e in report["errors"] if e["code"] == "duplicate_image_url"]
    assert len(findings) == 1
    assert findings[0]["severity"] == "warning"
    assert "https://x.test/f77.png" in findings[0]["message"]
    distinct = dict(row)
    distinct["concept_details"] = row["concept_details"].replace(
        'src="https://x.test/f77.png" alt="Fig. 7.7 Longitudinal section"]',
        'src="https://x.test/f78.png" alt="Fig. 7.8 Cross section"]', 1,
    )
    assert not [
        e for e in cv.validate_concept_rows([distinct])["errors"]
        if e["code"] == "duplicate_image_url"
    ]
    # Markdown images count too, and the code is outside every blocking set.
    md = dict(row)
    md["concept_details"] = (
        "Description: See ![a](https://x.test/a.png) and again ![a](https://x.test/a.png)."
        "\nAchieving Mastery: Name each whorl."
    )
    assert cv._repeated_image_urls(md["concept_details"]) == ["https://x.test/a.png"]
    from app.services import generation as g

    assert "duplicate_image_url" not in g._BLOCKING_CODES


# ---------------------------------------------------------------------------
# verification follow-ups (Q72)

URL = "https://cdn.example.org/fig.png"


def _page_acsd(*, public_alt: str = "A flower in section") -> dict:
    def block(order, kind, text, **extra):
        return {
            "reading_order": order, "kind": kind, "bbox": [0, 0, 10, 10],
            "text": text, "heading_level": 0, "source_label": "", "latex": "",
            "table_rows": [], "linked_visual_orders": [], "linked_context_orders": [],
            "caption": "", "confidence": 0.999, **extra,
        }
    return {
        "status": "verified",
        "pages": [{
            "page_id": "p1", "page_number": 1, "confidence": 0.999,
            "blocks": [
                block(1, "heading", "1 Reproduction", heading_level=1),
                block(2, "paragraph", "Light travels in straight lines."),
                block(3, "figure", "", source_caption="", public_alt=public_alt,
                      asset_url=URL),
            ],
        }],
        "verification": {"verdict": "verified", "approved_page_ids": ["p1"],
                         "rejected_page_ids": [], "confidence": 0.999, "issues": []},
    }


def test_the_page_acsd_mmd_keeps_an_empty_alt_and_the_reader_stamp_accepts_both_forms():
    from app.services import containers

    mmd = fallback.render_page_acsd_to_mmd(_page_acsd())
    assert f"![]({URL})" in mmd
    assert "Source visual" not in mmd
    assert "A flower in section" not in mmd          # public_alt never leaks into the MMD
    assert fallback.stale_mmd_reader(mmd) == ""
    old_form = mmd.replace(f"![]({URL})", f"![Source visual]({URL})")
    assert fallback.stale_mmd_reader(old_form) == ""  # Option A: no reconversion demanded
    canonical = phase2.compile_phase2_source(
        mmd, source_filename="x.mmd", consumer_module="build_concepts",
    ).canonical
    assert canonical["figures"][0]["caption_raw"] == ""
    assert canonical["images"][0]["alt_raw"] == ""
    assert containers.figure_block_evidence(canonical, {"items": []})[0]["caption"] == ""


def test_an_old_form_mmd_keeps_its_pool_caption_so_the_hub_guard_stays():
    """An already-converted job's MMD keeps ``![Source visual](url)``: its pool
    caption is the literal whatever the canonical says, which is why the Q67
    hub guard is kept."""
    from app.services import containers, generation

    old_form = fallback.render_page_acsd_to_mmd(_page_acsd()).replace(
        f"![]({URL})", f"![Source visual]({URL})"
    )
    canonical = phase2.compile_phase2_source(
        old_form, source_filename="x.mmd", consumer_module="build_concepts",
    ).canonical
    assert canonical["figures"][0]["caption_raw"] == "Source visual"
    assert containers.figure_block_evidence(canonical, {"items": []})[0]["caption"] == "Source visual"
    assert canonical["images"][0]["alt_raw"] == "Source visual"
    note = generation._figure_hub_note({"block_id": "BLK-0005", "caption": "Source visual", "url": URL})
    assert "Source visual" not in note
    assert f'[img src="{URL}"' in note


def test_the_sealed_render_is_the_new_render_plus_exactly_the_caption_piece():
    canonical, graph = _compile()
    new = phase3.render_semantic_source(graph, canonical)
    sealed = copy.deepcopy(graph)
    sealed.pop(phase3.FIGURE_CAPTION_RENDER_KEY)
    old = phase3.render_semantic_source(sealed, canonical)
    tag = '[img src="https://cdn.mathpix.com/uncaptioned.jpg" alt="Source visual"]'
    assert tag in new
    assert old == new.replace(tag, tag + "\n\nSource visual")


def test_the_warning_is_outside_every_blocking_set():
    from app.services import generation, release_refiner

    tag = '[img src="https://x.test/f77.png" alt="Fig. 7.7 Longitudinal section"]'
    row = {
        "topic": "Flowers", "parent_concept": "Reproduction",
        "concept_title": "Parts of a Flower",
        "concept_details": (
            "Description: A flower has four whorls. " + tag +
            "\nAchieving Mastery: Name each whorl. // Activity/ Info Hub: " + tag
        ),
        "keywords": "flower",
    }
    report = cv.validate_concept_rows([row])
    assert "duplicate_image_url" not in generation._FATAL_CODES
    assert generation._fatal_errors(report) == []
    assert report["ok"] is True
    assert report["summary"]["warnings"] >= 1
    assert release_refiner._terminal_error_keys([row], {"pre_post": "Post"}) == {}

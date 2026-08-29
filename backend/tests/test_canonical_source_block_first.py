"""Restructure C1 shadow mode: the block-first compiler runs beside the
authoritative render-then-reparse path, records every divergence, and never
changes what a conversion ships."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services import canonical_source_block_first as block_first
from app.services import canonical_source_phase221_fallback as fallback

from tests.test_canonical_source_phase221_fallback import (
    _make_pdf,
    _verified_provider,
)


def _convert(tmp_path: Path, monkeypatch, provider, job_id: int = 90):
    pdf = tmp_path / "source.pdf"
    _make_pdf(pdf)
    artifact_dir = tmp_path / "canonical-source"
    monkeypatch.setenv("AEGIS_PUBLIC_BASE_URL", "https://aegis.example")
    monkeypatch.setenv("AEGIS_SOURCE_ASSET_SECRET", "test-secret")
    monkeypatch.setattr(fallback, "_CACHE_DIR", tmp_path / "cache")
    result = fallback.reconstruct_pdf_to_acsd(
        pdf,
        job_id=job_id,
        artifact_dir=artifact_dir,
        fallback_reason=["pdf_source"],
        provider=provider,
    )
    return result, artifact_dir


def _shadow_payload(artifact_dir: Path) -> dict:
    spec = fallback.OPTIONAL_ARTIFACT_SPECS["block_first_shadow"]
    return json.loads(
        (artifact_dir / spec["filename"]).read_text(encoding="utf-8")
    )


def _full_outline() -> dict:
    """Content topic, assessment span, a partition, and a kind ruling."""
    return {
        "version": fallback.OUTLINE_VERSION,
        "chapter_title": "1 Number Patterns",
        "topics": [
            {"title": "Number Patterns", "kind": "content",
             "start_page_id": "PDF-PAGE-0001", "start_reading_order": 1},
            {"title": "Exercises", "kind": "assessment",
             "start_page_id": "PDF-PAGE-0002", "start_reading_order": 1},
        ],
        "task_partitions": [
            {"page_id": "PDF-PAGE-0002", "reading_order": 1,
             "independent_parts": [
                 {"label": "a", "stem": "Describe the pattern.",
                  "text": "a) Describe the pattern."},
                 {"label": "b", "stem": "Extend the pattern.",
                  "text": "b) Extend the pattern."},
             ]},
        ],
        "ruled_task_kinds": [["PDF-PAGE-0002", 1, "question"]],
        "unruled_task_refs": [],
        "notes": [],
        "review_flags": [],
    }


def test_span_render_is_byte_identical_and_spans_index_the_projection():
    page_acsd = {
        "chapter_outline": {
            "version": fallback.OUTLINE_VERSION,
            "topics": [
                {"title": "Patterns", "kind": "content",
                 "start_page_id": "P1", "start_reading_order": 1},
            ],
        },
        "pages": [{
            "page_id": "P1",
            "page_number": 1,
            "blocks": [
                {"reading_order": 1, "kind": "heading", "heading_level": 1,
                 "text": "Patterns", "source_label": "", "latex": "",
                 "table_rows": [], "linked_visual_orders": [],
                 "linked_context_orders": [], "caption": "", "confidence": 0.99,
                 "bbox": [0, 0, 100, 100]},
                {"reading_order": 2, "kind": "paragraph", "heading_level": 0,
                 "text": "A rule repeats.", "source_label": "", "latex": "",
                 "table_rows": [], "linked_visual_orders": [],
                 "linked_context_orders": [], "caption": "", "confidence": 0.99,
                 "bbox": [0, 0, 100, 100]},
                {"reading_order": 3, "kind": "task", "heading_level": 0,
                 "text": "Extend the pattern.", "source_label": "Try This",
                 "latex": "", "table_rows": [], "linked_visual_orders": [],
                 "linked_context_orders": [], "caption": "", "confidence": 0.99,
                 "bbox": [0, 0, 100, 100]},
            ],
        }],
    }
    flat = fallback.render_page_acsd_to_mmd(json.loads(json.dumps(page_acsd)))
    text, spans = fallback.render_page_acsd_to_mmd_with_spans(
        json.loads(json.dumps(page_acsd))
    )
    assert text == flat
    assert spans, "the span projection must record emitted parts"
    # Every span slices to a real emitted part; page identity rides along.
    for span in spans:
        assert 0 <= span["start"] <= span["end"] <= len(text)
        if span.get("role") != "mmd_header":
            assert span["page_id"] == "P1"
            assert span["reading_order"] in {1, 2, 3}
    cue = next(span for span in spans if span["role"] == "task_cue")
    assert text[cue["start"]:cue["end"]] == "### Try This"
    body = next(
        span for span in spans
        if span["role"] == "body" and span["reading_order"] == 3
    )
    assert text[body["start"]:body["end"]] == "Extend the pattern."


def test_shadow_matches_on_a_clean_conversion(tmp_path: Path, monkeypatch):
    result, artifact_dir = _convert(tmp_path, monkeypatch, _verified_provider)

    summary = result["reconstruction"]["block_first_shadow"]
    assert summary["status"] == "match"
    assert summary["divergences"] == 0
    assert summary["recalculated"] is True
    # The advisory record rides the canonical and the report through the
    # reconstruction manifest, and the full diff is a published artifact.
    assert result["canonical"]["source_reconstruction"][
        "block_first_shadow"
    ]["status"] == "match"
    assert result["report"]["source_reconstruction"][
        "block_first_shadow"
    ]["status"] == "match"
    payload = _shadow_payload(artifact_dir)
    assert payload["status"] == "match"
    assert payload["divergences"] == []
    assert payload["compiler_version"] == block_first.BLOCK_FIRST_COMPILER


def test_shadow_matches_under_a_content_topic_outline(tmp_path: Path, monkeypatch):
    outline = _full_outline()
    # Both spans are content topics here; the assessment-span case is pinned
    # separately below because it carries one known, recorded divergence.
    outline["topics"][1]["kind"] = "content"
    outline["task_partitions"] = []
    monkeypatch.setattr(
        fallback, "derive_chapter_outline", lambda _bundle: outline
    )
    result, artifact_dir = _convert(
        tmp_path, monkeypatch, _verified_provider
    )

    live_task = result["canonical"]["tasks"][0]
    # Precondition: the outline genuinely flowed into the conversion.
    assert live_task["scope_ruling"] == "chapter_outline_content_span"
    assert result["reconstruction"]["block_first_shadow"]["status"] == "match"
    payload = _shadow_payload(artifact_dir)
    shadow_canonical = payload["block_first_canonical"]
    shadow_task = shadow_canonical["tasks"][0]
    # The scope and kind verdicts reach both compilers through one shared
    # implementation, and the shadow agrees with the shipped result.
    assert shadow_task["scope_ruling"] == "chapter_outline_content_span"
    assert shadow_task["source_kind"] == live_task["source_kind"]
    # The outline's content topics opened the level-1 sections on both sides.
    shadow_titles = [
        section["title"] for section in shadow_canonical["sections"]
        if section["level"] == 1
    ]
    live_titles = [
        section["title"] for section in result["canonical"]["sections"]
        if section["level"] == 1
    ]
    assert "Number Patterns" in shadow_titles
    assert shadow_titles == live_titles


def test_assessment_span_leaf_partition_agrees_and_topic_hint_is_the_one_gap(
    tmp_path: Path, monkeypatch,
):
    """Under an assessment span with a ruled two-part partition, both
    compilers materialize the same leaf cases and the same scope. The ONE
    recorded divergence is ``topic_hint``: the re-parse path leaves the
    parser's cue-derived hint on a chapter-wide task, the native compiler
    records none — a measured cutover decision item, kept visible here."""
    outline = _full_outline()
    monkeypatch.setattr(
        fallback, "derive_chapter_outline", lambda _bundle: outline
    )

    def provider(pages):
        result = _verified_provider(pages)
        for page in result["pages"]:
            if page["page_number"] == 2:
                page["blocks"][0]["text"] = (
                    "a) Describe the pattern. b) Extend the pattern."
                )
        return result

    result, artifact_dir = _convert(tmp_path, monkeypatch, provider)

    live_task = result["canonical"]["tasks"][0]
    assert live_task["scope_ruling"] == "chapter_outline_assessment_span"
    assert len(live_task["leaf_cases"]) == 2
    summary = result["reconstruction"]["block_first_shadow"]
    payload = _shadow_payload(artifact_dir)
    fields = sorted(
        record["field"]
        for record in payload["divergences"]
        if record["code"] == "task_field_divergence"
    )
    assert fields == ["topic_hint"], payload["divergences"]
    assert summary["divergences"] == 1
    # The leaf materialization itself agreed exactly — no leaf divergence.
    assert not any(
        record["code"] == "task_leaf_cases_divergence"
        for record in payload["divergences"]
    )
    shadow_task = payload["block_first_canonical"]["tasks"][0]
    assert len(shadow_task["leaf_cases"]) == 2
    assert shadow_task["chapter_wide"] is True


def test_block_first_canonical_carries_native_identity(tmp_path: Path, monkeypatch):
    _result, artifact_dir = _convert(tmp_path, monkeypatch, _verified_provider)

    payload = _shadow_payload(artifact_dir)
    canonical = payload["block_first_canonical"]
    body_blocks = [
        block for block in canonical["blocks"]
        if (block.get("block_first") or {}).get("role") == "body"
    ]
    assert body_blocks
    for block in body_blocks:
        # Durable native identity: page + reading order, no re-parse mint.
        assert block["page_id"].startswith("PDF-PAGE-")
        assert block["page_number"] >= 1
        assert block["reading_order"] >= 1
        assert block["page_kind"]
        assert block["page_confidence"] > 0
    task = canonical["tasks"][0]
    relationship = task["gpt_pdf_acsd_relationship"]
    assert relationship["page_id"] == "PDF-PAGE-0002"
    assert relationship["task_reading_order"] == 1
    assert task["source_location_confidence"] == "gpt_pdf_acsd_native_block"
    # The projection is fully tiled: ordered block slices reconstruct the MMD.
    reconstructed = "".join(
        block["raw_text"] for block in canonical["blocks"]
    )
    raw_mmd = (artifact_dir / "source.raw.mmd").read_text(encoding="utf-8")
    assert reconstructed == raw_mmd


def test_shadow_records_a_misanchored_task_and_ships_the_conversion_unchanged(
    tmp_path: Path, monkeypatch,
):
    """The text-match re-join's known failure class: when the task's wording
    also appears verbatim earlier in the book, the re-parse anchors the task
    to the WRONG block. The shadow records the divergence; the conversion
    still ships the authoritative result untouched."""

    def provider(pages):
        result = _verified_provider(pages)
        result["pages"][0]["blocks"].append({
            "reading_order": 3, "kind": "paragraph",
            "bbox": [70, 300, 800, 380],
            "text": "Describe the pattern shown in Fig. 1.",
            "heading_level": 0, "source_label": "", "latex": "",
            "table_rows": [], "linked_visual_orders": [],
            "linked_context_orders": [], "caption": "", "confidence": 0.999,
        })
        return result

    result, artifact_dir = _convert(tmp_path, monkeypatch, provider)

    summary = result["reconstruction"]["block_first_shadow"]
    assert summary["status"] == "diverged"
    assert summary["divergence_codes"].get("task_field_divergence")
    payload = _shadow_payload(artifact_dir)
    fields = {
        record["field"]
        for record in payload["divergences"]
        if record["code"] == "task_field_divergence"
    }
    assert "source_start" in fields
    # The shipped conversion is exactly the authoritative result: the task
    # still carries the re-join's anchor, not the shadow's.
    task = result["canonical"]["tasks"][0]
    assert task["source_location_confidence"] == "gpt_pdf_acsd_verified_block"


def test_a_shadow_failure_never_blocks_the_conversion(tmp_path: Path, monkeypatch):
    def explode(**_kwargs):
        raise RuntimeError("shadow compiler crashed")

    monkeypatch.setattr(block_first, "run_shadow", explode)
    result, artifact_dir = _convert(tmp_path, monkeypatch, _verified_provider)

    assert result["reconstruction"]["status"] == "verified"
    summary = result["reconstruction"]["block_first_shadow"]
    assert summary["status"] == "error"
    assert "shadow compiler crashed" in summary["error"]
    spec = fallback.OPTIONAL_ARTIFACT_SPECS["block_first_shadow"]
    assert not (artifact_dir / spec["filename"]).exists()
    assert len(result["canonical"]["tasks"]) == 1


def test_shadow_artifact_is_published_and_downloadable(tmp_path: Path, monkeypatch):
    _result, artifact_dir = _convert(tmp_path, monkeypatch, _verified_provider)

    files, _reconstruction = fallback.optional_artifact_manifest(artifact_dir)
    kinds = {item["kind"] for item in files}
    assert "block_first_shadow" in kinds
    path, spec = fallback.optional_artifact_path(
        artifact_dir, "block_first_shadow"
    )
    assert path.exists()
    assert spec["filename"] == "source.block-first-shadow.json"


def test_a_stale_shadow_artifact_is_swept_by_the_next_publish(
    tmp_path: Path, monkeypatch,
):
    """The artifact is in the managed set: a conversion that produces no
    shadow record (an errored shadow) must retire the previous one rather
    than serve a diff of a bundle that no longer exists."""
    result, artifact_dir = _convert(tmp_path, monkeypatch, _verified_provider)
    spec = fallback.OPTIONAL_ARTIFACT_SPECS["block_first_shadow"]
    assert (artifact_dir / spec["filename"]).exists()

    def explode(**_kwargs):
        raise RuntimeError("shadow compiler crashed")

    monkeypatch.setattr(block_first, "run_shadow", explode)
    _result, artifact_dir = _convert(
        tmp_path, monkeypatch, _verified_provider, job_id=91
    )
    assert not (artifact_dir / spec["filename"]).exists()

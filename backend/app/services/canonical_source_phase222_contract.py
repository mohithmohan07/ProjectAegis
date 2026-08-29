"""Phase 2.2.2 contract for running navigation and vertical page furniture.

Phase 2.2.1 made source-labelled historical passages explicit and added bounded
self-correction.  A live RNE acceptance run then exposed a distinct page-layout
case: a repeated vertical chapter label was treated as missing semantic content,
and the correction model attempted to add it with an invalid block role/order.

This contract keeps that page furniture available in the verified page ledger
without allowing it to become a topic, source passage, task, or semantic MMD
block.  It deliberately leaves the Phase 2.2.1 cache identity unchanged so
already verified batches remain reusable; failed batches are never cached.
"""
from __future__ import annotations

import copy
import re
from typing import Any

from . import canonical_source_phase221_fallback as fallback

_CONTRACT_VERSION = 1
_NAVIGATION_KIND = "navigation"
_NAVIGATION_ALIASES = frozenset({
    "navigation",
    "sidebar",
    "vertical sidebar",
    "running label",
    "running header",
    "running footer",
    "page header",
    "page footer",
    "chapter label",
    "section label",
    "page label",
    "folio",
})
_NAVIGATION_TEXT_KINDS = frozenset({"heading", "paragraph", "list", "other"})
_SPACE_RE = re.compile(r"\s+")


def _normal(value: object) -> str:
    return _SPACE_RE.sub(" ", str(value or "")).strip().casefold()


def _as_int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _looks_like_vertical_navigation(block: dict[str, Any]) -> bool:
    """Return True only for short, slender text pinned to a page edge."""
    text = _SPACE_RE.sub(" ", str(block.get("text") or "")).strip()
    if not text or len(text) > 180:
        return False
    bbox = block.get("bbox")
    if (
        not isinstance(bbox, list)
        or len(bbox) != 4
        or any(not isinstance(value, (int, float)) for value in bbox)
    ):
        return False
    x0, y0, x1, y1 = [float(value) for value in bbox]
    if not (0 <= x0 < x1 <= 1000 and 0 <= y0 < y1 <= 1000):
        return False
    width = x1 - x0
    height = y1 - y0
    at_vertical_edge = x1 <= 220 or x0 >= 780
    slender = width <= 180 and height >= max(120.0, width * 1.8)
    return at_vertical_edge and slender


def install() -> None:
    if (
        getattr(fallback, "_PHASE222_NAVIGATION_CONTRACT_VERSION", 0)
        >= _CONTRACT_VERSION
    ):
        return

    original_extraction_prompt = fallback._extraction_system_prompt
    original_verification_prompt = fallback._verification_system_prompt
    original_correction_prompt = fallback._correction_system_prompt
    original_canonicalize = fallback._canonicalize_source_cue_block
    original_validate = fallback.validate_page_extraction
    original_render = fallback.render_page_acsd_to_mmd
    original_render_spans = fallback.render_page_acsd_to_mmd_with_spans

    if _NAVIGATION_KIND not in fallback._ALLOWED_KINDS:
        fallback._ALLOWED_KINDS = (*fallback._ALLOWED_KINDS, _NAVIGATION_KIND)

    navigation_rules = """

Running-navigation rule:
- Repeated page furniture such as running headers, running footers, vertically
  printed chapter/section labels in an outer margin, and folios is navigational,
  not semantic textbook content. It may be omitted under the same rule as a
  repeated running header/footer.
- If retained for page-level audit, use kind=navigation, preserve its exact text
  and bbox, set heading_level=0 and source_label="", and attach no visual or
  context links. It must never become a heading, source passage, or learner task.
- Every retained block must have a positive, unique reading_order. Navigation
  blocks may be placed after all semantic blocks because bbox retains their exact
  physical location.
""".rstrip()

    verification_rules = """

Treat repeated vertical chapter/section labels and equivalent running page
furniture exactly like running headers or footers. Do not reject a candidate
solely because such navigation is omitted. If the candidate retains it, require
kind=navigation with exact text/bbox, no semantic role, no links, and a positive
unique reading_order after semantic blocks.
""".rstrip()

    correction_rules = """

A repeated vertical chapter/section label is navigation, not a missing semantic
heading. It may remain omitted. If it is retained for audit, emit kind=navigation,
heading_level=0, source_label="", no links, and a positive unique reading_order
after all semantic blocks. Never introduce kind=sidebar or reading_order=0.
""".rstrip()

    def extraction_prompt() -> str:
        return original_extraction_prompt() + navigation_rules

    def verification_prompt() -> str:
        return original_verification_prompt() + verification_rules

    def correction_prompt() -> str:
        return original_correction_prompt() + correction_rules

    def canonicalize_navigation_block(raw: dict[str, Any]) -> dict[str, Any]:
        block = original_canonicalize(raw)
        kind_key = _normal(block.get("kind"))
        should_be_navigation = (
            kind_key in _NAVIGATION_ALIASES
            or (
                _looks_like_vertical_navigation(block)
                and (
                    kind_key in _NAVIGATION_TEXT_KINDS
                    or kind_key not in set(fallback._ALLOWED_KINDS)
                )
            )
        )
        if not should_be_navigation:
            return block

        visible_text = str(block.get("text") or "").strip()
        source_label = str(block.get("source_label") or "").strip()
        if not visible_text and source_label:
            block["text"] = source_label
        block["kind"] = _NAVIGATION_KIND
        block["heading_level"] = 0
        block["source_label"] = ""
        block["linked_visual_orders"] = []
        block["linked_context_orders"] = []
        return block

    def normalize_candidate(candidate: dict[str, Any]) -> tuple[dict[str, Any], int]:
        value = copy.deepcopy(candidate)
        rows = value.get("pages") if isinstance(value, dict) else None
        if not isinstance(rows, list):
            return value, 0

        repair_count = 0
        for row in rows:
            if not isinstance(row, dict):
                continue
            blocks = row.get("blocks")
            if not isinstance(blocks, list):
                continue

            normalized_blocks: list[Any] = []
            for raw in blocks:
                if not isinstance(raw, dict):
                    normalized_blocks.append(raw)
                    continue
                normalized = canonicalize_navigation_block(raw)
                if normalized != raw:
                    repair_count += 1
                normalized_blocks.append(normalized)

            non_navigation_orders = {
                _as_int(block.get("reading_order"))
                for block in normalized_blocks
                if isinstance(block, dict)
                and block.get("kind") != _NAVIGATION_KIND
                and _as_int(block.get("reading_order")) > 0
            }
            occupied = set(non_navigation_orders)
            pending_navigation: list[dict[str, Any]] = []
            old_navigation_orders: set[int] = set()

            for block in normalized_blocks:
                if not isinstance(block, dict) or block.get("kind") != _NAVIGATION_KIND:
                    continue
                order = _as_int(block.get("reading_order"))
                if order > 0:
                    old_navigation_orders.add(order)
                if order < 1 or order in occupied:
                    pending_navigation.append(block)
                else:
                    occupied.add(order)

            next_order = max(occupied, default=0) + 1
            for block in pending_navigation:
                while next_order in occupied:
                    next_order += 1
                if _as_int(block.get("reading_order")) != next_order:
                    repair_count += 1
                block["reading_order"] = next_order
                occupied.add(next_order)
                next_order += 1

            navigation_orders = {
                _as_int(block.get("reading_order"))
                for block in normalized_blocks
                if isinstance(block, dict)
                and block.get("kind") == _NAVIGATION_KIND
                and _as_int(block.get("reading_order")) > 0
            }
            forbidden_context_orders = old_navigation_orders | navigation_orders
            if forbidden_context_orders:
                for block in normalized_blocks:
                    if not isinstance(block, dict) or block.get("kind") != "task":
                        continue
                    links = [
                        _as_int(value)
                        for value in block.get("linked_context_orders") or []
                    ]
                    filtered = [
                        value for value in links
                        if value not in forbidden_context_orders
                    ]
                    if filtered != links:
                        repair_count += 1
                        block["linked_context_orders"] = filtered

            row["blocks"] = normalized_blocks
        return value, repair_count

    def validate_page_extraction(
        pages: list[fallback.PdfPage],
        candidate: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, str]:
        normalized_candidate, repairs = normalize_candidate(candidate)
        if repairs:
            fallback.progress.log(
                f"Normalized {repairs} running-navigation field(s) before "
                "deterministic PDF-to-ACSD validation.",
                level="warning",
            )
        return original_validate(pages, normalized_candidate)

    def _semantic_only(page_acsd: dict[str, Any]) -> dict[str, Any]:
        semantic = copy.deepcopy(page_acsd)
        for page in semantic.get("pages") or []:
            if not isinstance(page, dict):
                continue
            page["blocks"] = [
                block for block in page.get("blocks") or []
                if not (
                    isinstance(block, dict)
                    and block.get("kind") == _NAVIGATION_KIND
                )
            ]
        return semantic

    def render_page_acsd_to_mmd(page_acsd: dict[str, Any]) -> str:
        return original_render(_semantic_only(page_acsd))

    def render_page_acsd_to_mmd_with_spans(page_acsd: dict[str, Any]):
        # The block-first shadow's span projection must see exactly the
        # semantic ledger the flat projection sees, or the two renderings
        # of the same page ACSD would legitimately differ.
        return original_render_spans(_semantic_only(page_acsd))

    fallback._extraction_system_prompt = extraction_prompt
    fallback._verification_system_prompt = verification_prompt
    fallback._correction_system_prompt = correction_prompt
    fallback._canonicalize_source_cue_block = canonicalize_navigation_block
    fallback.validate_page_extraction = validate_page_extraction
    fallback.render_page_acsd_to_mmd = render_page_acsd_to_mmd
    fallback.render_page_acsd_to_mmd_with_spans = render_page_acsd_to_mmd_with_spans
    fallback._PHASE222_NAVIGATION_CONTRACT_VERSION = _CONTRACT_VERSION

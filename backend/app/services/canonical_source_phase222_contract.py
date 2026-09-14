"""Phase 2.2.2 contract for running navigation and vertical page furniture.

Phase 2.2.1 made source-labelled historical passages explicit and added bounded
self-correction.  A live RNE acceptance run then exposed a distinct page-layout
case: a repeated vertical chapter label was treated as missing semantic content,
and the correction model attempted to add it with an invalid block role/order.

This contract keeps that page furniture available in the verified page ledger
without allowing it to become a topic, source passage, task, or semantic MMD
block.  It deliberately leaves the Phase 2.2.1 cache identity unchanged so
already verified batches remain reusable; failed batches are never cached.

Contract version 2 removes ``_looks_like_vertical_navigation``, the bbox
shape-matcher that decided, with no model in the loop, that a short slender
block pinned to a page edge was navigation whatever kind the model had
declared.  A tinted definition box in a wide outer margin, a vertical pull
quote and a sideways-printed poem stanza all match that shape, and the rule
silently rewrote the model's ``heading`` or ``paragraph`` verdict into
``navigation`` — which strips the block out of the semantic MMD entirely.  That
is a deterministic judgment about what the page means, which Rule 1 forbids.
The only navigation a block now becomes is the navigation the model itself
declared, by kind or by one of its printed aliases; the geometry rule is moved
into the extraction, verification and correction prompts, where the model reads
it against the page image and an independent verifier checks the answer.
"""
from __future__ import annotations

import copy
import re
from typing import Any

from . import canonical_source_phase221_fallback as fallback

_CONTRACT_VERSION = 2
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
_SPACE_RE = re.compile(r"\s+")


def _normal(value: object) -> str:
    return _SPACE_RE.sub(" ", str(value or "")).strip().casefold()


def _as_int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


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

    # This append is what puts "navigation" into the strict extraction enum
    # (_block_schema's kind enum) and into the validator's _ALLOWED_KINDS
    # membership test. Without it the model cannot legally declare the kind at
    # all, and a declared navigation block is downgraded to "other" — where it
    # renders into the semantic MMD as page furniture masquerading as content.
    if _NAVIGATION_KIND not in fallback._ALLOWED_KINDS:
        fallback._ALLOWED_KINDS = (*fallback._ALLOWED_KINDS, _NAVIGATION_KIND)

    navigation_rules = """

Running-navigation rule:
- Repeated page furniture — a running header, a running footer, a folio or bare
  page number, and a repeated chapter/section/unit label printed in an outer
  margin, whether set horizontally or sideways — is navigational, not semantic
  textbook content. Do exactly one of these two things with it:
  (a) omit it, and return its exact printed line in that page's
      dropped_furniture array, under the same rule as a running header; or
  (b) retain it with kind=navigation, its exact text and bbox, heading_level=0,
      source_label="", linked_visual_orders=[] and linked_context_orders=[].
  It must never become a heading, source passage, or learner task.
- The converse, which is the commoner case in a school textbook: a box printed
  in a margin that TEACHES — a definition, a fact or know-more box, a vocabulary
  note, a hint, an activity, or any learner instruction — is semantic content.
  Transcribe it under its own kind, with its full visible wording, exactly as
  you would the same box set in the main column. Being narrow, tinted, printed
  sideways, or positioned at the edge of the page does not by itself make a
  block navigation; carrying no teaching and repeating as page furniture does.
- Every retained block must have a positive, unique reading_order. Navigation
  blocks may be placed after all semantic blocks because bbox retains their
  exact physical location.
""".rstrip()

    verification_rules = """

Treat a repeated chapter/section label printed in an outer margin, set
horizontally or sideways, exactly like a running header or footer, and check it
in both directions. If the candidate omitted it, it must appear verbatim in that
page's dropped_furniture array, as the furniture rule above already requires. If
the candidate retained it, require kind=navigation with exact text and bbox,
heading_level=0, empty source_label, no ownership links, and a positive unique
reading_order — navigation placed after the semantic blocks is correct, not a
reading-order error. In the other direction, a margin box that teaches — a
definition, fact, vocabulary, hint, activity or learner instruction — is
semantic content: reject a candidate that dropped such a box as furniture or
recorded it as kind=navigation.
""".rstrip()

    correction_rules = """

A repeated chapter/section label in an outer margin is navigation, not a missing
semantic heading. Either leave it omitted with its exact printed line recorded in
that page's dropped_furniture array, or emit it as kind=navigation with
heading_level=0, source_label="", no links, and a positive unique reading_order
after all semantic blocks. Never introduce kind=sidebar or reading_order=0. A
margin box that teaches is not navigation: restore it under its own semantic
kind, with its full visible wording, rather than as navigation.
""".rstrip()

    def extraction_prompt() -> str:
        return original_extraction_prompt() + navigation_rules

    def verification_prompt() -> str:
        return original_verification_prompt() + verification_rules

    def correction_prompt() -> str:
        return original_correction_prompt() + correction_rules

    def canonicalize_navigation_block(raw: dict[str, Any]) -> dict[str, Any]:
        block = original_canonicalize(raw)
        # The model's own declared kind is the whole predicate. The aliases are
        # the printed names a page uses for the same thing, not a judgment about
        # the block: reconciling "sidebar" to "navigation" is the same
        # field-level reconciliation _canonicalize_source_cue_block performs for
        # source cues.
        if _normal(block.get("kind")) not in _NAVIGATION_ALIASES:
            return block

        # _SOURCE_COMPATIBLE_KINDS is {heading, paragraph, list, other}, so
        # _canonicalize_source_cue_block above promotes none of these aliases to
        # kind=source, whatever source_label they carry. The label therefore
        # survives to here — which is exactly why the text-recovery branch below
        # is reachable: a model that put the label text in source_label and left
        # text empty would otherwise have its wording silently dropped.
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

    def normalize_candidate(
        candidate: dict[str, Any],
    ) -> tuple[dict[str, Any], int, dict[str, list[str]]]:
        value = copy.deepcopy(candidate)
        alias_flags: dict[str, list[str]] = {}
        rows = value.get("pages") if isinstance(value, dict) else None
        if not isinstance(rows, list):
            return value, 0, alias_flags

        repair_count = 0
        for row in rows:
            if not isinstance(row, dict):
                continue
            blocks = row.get("blocks")
            if not isinstance(blocks, list):
                continue
            page_id = str(row.get("page_id") or "")

            normalized_blocks: list[Any] = []
            for raw in blocks:
                if not isinstance(raw, dict):
                    normalized_blocks.append(raw)
                    continue
                declared_kind = str(raw.get("kind") or "").strip()
                normalized = canonicalize_navigation_block(raw)
                if normalized != raw:
                    repair_count += 1
                if (
                    normalized.get("kind") == _NAVIGATION_KIND
                    and _normal(declared_kind) != _NAVIGATION_KIND
                ):
                    alias_flags.setdefault(page_id, []).append(
                        f"block kind {declared_kind!r} recorded as "
                        f"{_NAVIGATION_KIND!r}"
                    )
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
        return value, repair_count, alias_flags

    def navigation_review_flags(
        page_row: dict[str, Any],
        alias_flags: dict[str, list[str]],
    ) -> list[str]:
        """Compose this page's navigation review flags.

        WORDING IS CONSTRAINED, and this is not house style. A page's
        review_flags ride inside the candidate that ``_page_prompt`` puts in
        front of the independent verifier and, on a rejection, the corrector.
        A flag may therefore transcribe two things and nothing else: the kind
        the MODEL declared, and the renderer's mechanical consequence of that
        kind. It must never characterise the block — "page furniture",
        "not real content", "correctly omitted" would be this module telling
        the reviewing model what the page means, which is the judgment Rule 1
        reserves for the model, laundered through bookkeeping the verification
        prompt is told to ignore.
        """
        page_id = str(page_row.get("page_id") or "")
        flags = list(alias_flags.get(page_id) or [])
        retained = [
            _SPACE_RE.sub(" ", str(block.get("text") or "")).strip()
            for block in page_row.get("blocks") or []
            if isinstance(block, dict)
            and block.get("kind") == _NAVIGATION_KIND
        ]
        if retained:
            # One flag per page, not one per block: a page whose margin label
            # repeats down the edge would otherwise bury every other flag it
            # carries under a stack of identical lines.
            flags.append(
                f"retained {len(retained)} block(s) of kind "
                f"{_NAVIGATION_KIND!r}, which the semantic MMD does not "
                "render: " + " | ".join(retained)
            )
        return flags

    def validate_page_extraction(
        pages: list[fallback.PdfPage],
        candidate: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, str]:
        normalized_candidate, repairs, alias_flags = normalize_candidate(candidate)
        if repairs:
            fallback.progress.log(
                f"Normalized {repairs} running-navigation field(s) before "
                "deterministic PDF-to-ACSD validation.",
                level="warning",
            )
        result, reason = original_validate(pages, normalized_candidate)
        if not isinstance(result, dict):
            return result, reason
        # The flags are appended to the RESULT rows, not to the candidate:
        # validate_page_extraction builds each normalized page dict from
        # scratch and sets review_flags from its own local list, so anything
        # written onto the candidate's rows is discarded before the caller
        # ever sees it.
        for page_row in result.get("pages") or []:
            if not isinstance(page_row, dict):
                continue
            flags = navigation_review_flags(page_row, alias_flags)
            if not flags:
                continue
            label = str(page_row.get("page_id") or "PDF page")
            page_row["review_flags"] = list(page_row.get("review_flags") or []) + [
                f"{label}: {flag}" for flag in flags
            ]
        return result, reason

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

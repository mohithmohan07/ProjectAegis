"""Lossless, mechanical transport of edited Concept question quotes.

The Concept workbook is the authority for wording.  This module only bridges
the two representations that a workbook cell can expose while a model copies
it: ``CRLF`` versus ``LF`` and HTML ``br`` markers versus a displayed line
break.  It never compares words approximately or repairs meaning.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


_BR = re.compile(r"<br\s*/?>", re.IGNORECASE)


@dataclass(frozen=True)
class QuoteMatch:
    """A source-authoritative quote and its raw cell boundaries."""

    raw: str
    start: int
    end: int


def view(value: object) -> str:
    """Return the display view used only for reversible quote lookup."""

    return _BR.sub("\n", str(value or "").replace("\r\n", "\n").replace("\r", "\n"))


def _view_with_raw_offsets(source: str) -> tuple[str, list[int]]:
    """Build a view and map each view character back to a raw index.

    The mapping deliberately handles only representation changes.  In
    particular, it keeps markdown, URLs, math delimiters, entities and every
    other source character byte-for-byte available in the returned raw slice.
    """

    out: list[str] = []
    offsets: list[int] = []
    index = 0
    while index < len(source):
        if source[index] == "\r":
            raw_start = index
            if index + 1 < len(source) and source[index + 1] == "\n":
                index += 1
            out.append("\n")
            offsets.append(raw_start)
            index += 1
            continue
        match = _BR.match(source, index)
        if match:
            out.append("\n")
            offsets.append(index)
            index = match.end()
            continue
        out.append(source[index])
        offsets.append(index)
        index += 1
    return "".join(out), offsets


def _all_occurrences(haystack: str, needle: str) -> list[int]:
    if not needle:
        return []
    found: list[int] = []
    offset = 0
    while True:
        position = haystack.find(needle, offset)
        if position < 0:
            return found
        found.append(position)
        offset = position + 1


def locate(source: object, candidate: object, *, after: int = 0) -> QuoteMatch | None:
    """Locate an exact raw or display-view quote in ``source``.

    Raw lookup is preferred.  View lookup is accepted only when there is one
    possible view occurrence; ambiguity fails closed instead of selecting a
    semantic guess.  The result always contains the original raw cell slice.
    """

    raw = str(source or "")
    quote = str(candidate or "")
    if not quote:
        return None
    positions = [position for position in _all_occurrences(raw, quote) if position >= after]
    if len(positions) == 1:
        start = positions[0]
        return QuoteMatch(raw[start:start + len(quote)], start, start + len(quote))
    # A direct quote is already a valid source quote even when repeated.  The
    # first occurrence is only used for span transport, where the caller also
    # checks ordering and the complete concatenated rendering.
    if positions:
        start = positions[0]
        return QuoteMatch(raw[start:start + len(quote)], start, start + len(quote))

    rendered, offsets = _view_with_raw_offsets(raw)
    view_positions = [
        position for position in _all_occurrences(rendered, view(quote))
        if offsets[position] >= after
    ]
    if len(view_positions) != 1:
        return None
    start_view = view_positions[0]
    end_view = start_view + len(view(quote))
    start = offsets[start_view]
    if end_view < len(offsets):
        end = offsets[end_view]
    else:
        end = len(raw)
    return QuoteMatch(raw[start:end], start, end)


def materialize_spans(source: object, spans: object) -> str | None:
    """Materialize ordered exact source slices supplied as question spans.

    Span transport is for a question whose visible wording is split by
    source labels or answer material that the reviewer deliberately excluded.
    Each part must itself be source-backed.  No separator is invented: any
    punctuation, whitespace or line break that belongs in the wording must
    be included in a returned span.
    """

    if not isinstance(spans, list) or not spans:
        return None
    source_text = str(source or "")
    matches: list[QuoteMatch] = []
    cursor = 0
    for span in spans:
        match = locate(source_text, span, after=cursor)
        if match is None or match.start < cursor:
            return None
        matches.append(match)
        cursor = match.end
    return "".join(match.raw for match in matches)


def add_view(row: dict) -> dict:
    """Copy a Concept row and expose its reversible display view to the model."""

    copied = dict(row)
    copied["concept_details_view"] = view(copied.get("concept_details") or "")
    return copied

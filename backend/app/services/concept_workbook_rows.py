"""Mechanical handling for repeated Concept rows across workbook sheets.

Concept workbooks normally carry their content rows on Objective while the
other two tabs are header-only.  Some compatible workbooks repeat the same
hierarchy row on more than one tab, however.  This module provides the small
transport-only coalescing step for those copies; identity and content remain
the values supplied by the workbook.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any


CONCEPT_PROJECTION_FIELDS = (
    "tag",
    "topic",
    "concept_title",
    "parent_concept",
    "concept_details",
    "keywords",
)

# Kept on the retained row so the first row's existing ``sheet``/``row``
# location remains authoritative while the other identical copies remain
# auditable.  The leading underscore marks this as parser provenance rather
# than an editable Concept field.
DUPLICATE_SHEET_PROVENANCE_FIELD = "_duplicate_sheet_provenance"


def _same_projection(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return all(
        left.get(field) == right.get(field)
        for field in CONCEPT_PROJECTION_FIELDS
    )


def coalesce_repeated_sheet_rows(
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Coalesce exact tagged copies that occur on different sheets.

    A tagged projection is eligible only when every row carrying that tag has
    exactly the same Concept projection and occurs once per sheet.  A
    tagless row, a same-sheet duplicate, or any conflicting projection is
    returned unchanged so the caller's existing loud identity refusal still
    handles the ambiguity.  Input order is preserved; the first eligible row
    is retained and receives locations for the removed identical copies.

    The function is deliberately content-blind: it does not trim, case-fold,
    normalize whitespace, or infer identity from topic/title text.
    """

    copied = [dict(row) for row in rows]
    by_tag: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(copied):
        tag = row.get("tag")
        if isinstance(tag, str) and tag.strip():
            by_tag[tag].append(index)

    coalesced: set[int] = set()
    for indexes in by_tag.values():
        if len(indexes) < 2:
            continue
        projections = [copied[index] for index in indexes]
        if any(
            not _same_projection(projections[0], projection)
            for projection in projections[1:]
        ):
            continue
        sheets = [str(row.get("sheet") or "") for row in projections]
        if any(not sheet for sheet in sheets) or len(set(sheets)) != len(sheets):
            continue
        keeper_index = indexes[0]
        keeper = copied[keeper_index]
        keeper[DUPLICATE_SHEET_PROVENANCE_FIELD] = [
            {"sheet": str(row.get("sheet") or ""), "row": str(row.get("row") or "")}
            for row in projections[1:]
        ]
        coalesced.update(indexes[1:])

    return [row for index, row in enumerate(copied) if index not in coalesced]


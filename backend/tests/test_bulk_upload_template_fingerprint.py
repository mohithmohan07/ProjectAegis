"""The owner's physical CMS bulk-upload template IS the universal layout.

Master Governing Contract v2.0 §14 freezes the template filename, SHA-256,
sheet order, row-1 bands, row-2 header sequence and slot capacity before
authoring, and lets a later physical template supersede the quoted widths
only once its full fingerprint is explicitly accepted. Register Q27
(2026-09-04) accepts ``Bulk_Upload_New_Format.xlsx``: its fingerprint is
transcribed byte-exactly into ``app/bulk_import/templates`` and these tests
prove, column for column, that the template's INTENDED geometry (every
header once, trimmed) is exactly the registered ``update-aware-master-2``
layout every output renders — and that the template's own defects are
recorded rather than reproduced (§14: duplicate and whitespace-tainted
headers block a write; B.3: every row-2 header is unique and trimmed).
"""
from __future__ import annotations

import json
from pathlib import Path

from app.bulk_import import layouts

FINGERPRINT = (
    Path(layouts.__file__).with_name("templates")
    / "bulk_upload_new_format_2026-09-04.json"
)


def _fingerprint() -> dict:
    return json.loads(FINGERPRINT.read_text(encoding="utf-8"))


def _intended_headers(sheet: dict) -> list[str]:
    """The template's headers with its recorded duplicates removed, trimmed."""
    duplicates = {
        int(entry["column"])
        for entry in sheet["recorded_defects"]["duplicate_columns"]
    }
    return [
        str(header or "").strip()
        for column, header in enumerate(sheet["row_2_headers"], start=1)
        if column not in duplicates
    ]


def test_the_template_names_the_universal_layout_and_a_sha256() -> None:
    recorded = _fingerprint()
    assert recorded["layout_id"] == layouts.UPDATE_AWARE_MASTER_LAYOUT_ID
    assert recorded["layout_id"] == "update-aware-master-2"
    assert recorded["register_entry"] == "Q27"
    assert len(recorded["sha256"]) == 64
    assert recorded["filename"] == "Bulk_Upload_New_Format.xlsx"


def test_every_sheet_of_the_template_is_the_registered_layout_column_for_column() -> None:
    recorded = _fingerprint()
    universal = layouts.layout(layouts.UPDATE_AWARE_MASTER_LAYOUT_ID)
    fields_by_kind = universal.fields_by_kind()
    seen_kinds = []
    for sheet in recorded["sheets"]:
        kind = sheet["kind"]
        seen_kinds.append(kind)
        assert _intended_headers(sheet) == list(fields_by_kind[kind]), kind
    assert sorted(seen_kinds) == ["descriptive", "objective", "subjective"]
    # Q27 widths: the template's slot capacity is the universal one.
    assert [
        len(fields_by_kind[kind])
        for kind in ("objective", "descriptive", "subjective")
    ] == [72, 440, 149]
    assert layouts.UNIVERSAL_DESCRIPTIVE_ANSWER_SLOTS == 30


def test_the_recorded_defects_are_exactly_the_templates_extra_and_tainted_headers() -> None:
    """A repaired template changes this pin deliberately, never silently."""
    recorded = _fingerprint()
    by_kind = {sheet["kind"]: sheet for sheet in recorded["sheets"]}

    def duplicates(kind: str) -> list[tuple[int, str]]:
        return [
            (int(entry["column"]), entry["header"])
            for entry in by_kind[kind]["recorded_defects"]["duplicate_columns"]
        ]

    def tainted(kind: str) -> list[str]:
        return [
            entry["header"]
            for entry in by_kind[kind]["recorded_defects"][
                "whitespace_tainted_headers"
            ]
        ]

    assert duplicates("objective") == []
    assert duplicates("subjective") == [(3, "chapter_display_name")]
    descriptive = duplicates("descriptive")
    assert descriptive[0] == (3, "chapter_display_name")
    assert descriptive[-1] == (458, "sq15_keyword_6")
    # Seventeen copy-pasted answer-block header cells between blocks 9-13.
    assert len(descriptive) == 18
    assert all(
        header.startswith(("answer_type_", "answer_weightage_", "answer_content_"))
        for _column, header in descriptive[1:-1]
    )
    # Every recorded duplicate really is a repeat of a header the sheet
    # already carries: none of them names a column the layout lacks.
    for kind in ("subjective", "descriptive"):
        intended = set(_intended_headers(by_kind[kind]))
        assert all(header in intended for _column, header in duplicates(kind))
    assert tainted("objective") == ["is_update_topic\n"]
    assert tainted("subjective") == ["is_update_chapter\n", "is_update_topic\n"]
    assert tainted("descriptive") == ["is_update_topic\n"]


def test_the_template_sheet_order_is_recorded_and_the_outputs_keep_the_contract_order() -> None:
    """Contract §12 orders the sheets Objective, Descriptive, Subjective; the
    physical template lists Subjective before Descriptive. Recorded, not
    adopted: the reader refuses a non-canonical order, so the difference is
    the CMS template's to settle (Q27)."""
    recorded = _fingerprint()
    assert [
        name.strip() for name in recorded["template_sheet_order"]
    ] == ["Objective", "Subjective", "Descriptive"]
    assert recorded["output_sheet_order"] == [
        "Objective", "Descriptive", "Subjective",
    ]
    universal = layouts.layout(layouts.UPDATE_AWARE_MASTER_LAYOUT_ID)
    assert [
        universal.sheet(kind).sheet_name
        for kind in ("objective", "descriptive", "subjective")
    ] == recorded["output_sheet_order"]


def test_the_previous_380_column_geometry_stays_readable() -> None:
    """A Master published before Q27 still identifies on import."""
    older = layouts.layout(layouts.UPDATE_AWARE_MASTER_380_LAYOUT_ID)
    assert [
        len(older.sheet(kind).fields)
        for kind in ("objective", "descriptive", "subjective")
    ] == [72, 380, 149]
    assert layouts.UPDATE_AWARE_MASTER_380_LAYOUT_ID in layouts.CURRENT_TARGET_LAYOUT_IDS
    assert layouts.UPDATE_AWARE_MASTER_LAYOUT_ID in layouts.STRICT_COMPLETE_LAYOUT_IDS
    headers = {
        sheet.sheet_name: sheet.fields for sheet in older.sheets.values()
    }
    assert layouts.identify_workbook(headers).layout_id == (
        layouts.UPDATE_AWARE_MASTER_380_LAYOUT_ID
    )

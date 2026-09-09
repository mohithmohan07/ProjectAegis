"""Readable Excel cells without changing the canonical import vocabulary.

The CMS consumes ``<br>`` while Excel displays a physical line feed. Pair
them at the final workbook seam; the shared importer consumes the pair as
one break. Question text, evidence, KaTeX and image tokens remain content.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

from openpyxl.styles import Alignment, PatternFill
from openpyxl.utils import get_column_letter

PRESENTATION_VERSION = "excel-visible-breaks-2026-09-09"
DATA_ALIGNMENT = Alignment(vertical="top", wrap_text=True)
HEADER_ALIGNMENT = Alignment(vertical="top", wrap_text=True)

# These are schema-field roles, never content classification. Unknown fields
# retain a useful default width and all header labels remain byte-exact.
_FIELD_WIDTHS = {
    "chapter_title": 42, "chapter_display_name": 36,
    "topic_title": 42, "topic_display_name": 36,
    "concept_title": 46, "concept_display_name": 40,
    "group_name": 42, "group_display_name": 40,
    "question_label": 42,
    "chapter_description": 64, "topic_description": 64,
    "concept_details": 96, "group_description": 64,
    "question": 80, "question_text": 88,
    "display_answer": 72, "answer_explanation": 80,
    "question_disclaimer": 60,
    "keywords": 42, "digicards": 42, "related_digicards": 42,
    "pre_topics": 42, "post_topics": 42, "related_topics": 42,
    "related_concepts": 42, "topic_concept_labels": 42,
    "concept_question_labels": 42, "group_question_labels": 42,
    "basic_groups": 42, "intermediate_groups": 42, "advanced_groups": 42,
    "marks": 14, "question_duration": 18, "chapter_duration": 18,
}
_RICH_SLOT_FIELD = re.compile(
    r"(?:answer_content_\d+|answer_\d+|answer_display_\d+"
    r"|sub_question_\d+|sq\d+_keyword_\d+)\Z"
)
_NUMERIC_SLOT_FIELD = re.compile(
    r"(?:answer_weightage_\d+|weightage_\d+|sub_question_marks_\d+"
    r"|sq\d+_weightage_\d+)\Z"
)
_DISPLAY_BREAK = re.compile(r"(<br\s*/?>)(?:\r\n|\r|\n)", re.IGNORECASE)


def to_display_rich_text(value, *, raw_equation: bool = False) -> str:
    """Project every logical break to one CMS token plus one Excel break.

    Decoding first makes writing an already displayed cell idempotent. A
    paragraph retains two breaks; literal ``\\n`` is not interpreted and
    KaTeX's ``\\\\`` row separator is untouched.
    """
    from . import _outside_katex, from_workbook_rich_text

    if raw_equation:
        return str(value if value is not None else "")
    return _outside_katex(
        from_workbook_rich_text(value),
        lambda segment: segment.replace("\n", "<br>\n"),
    )


def canonical_cell_value(value, *, raw_equation: bool = False):
    """Remove Excel-only LF partners from read-back wire records.

    The workbook parser historically returns canonical ``<br>`` text, while
    the database importer turns those tokens into model newlines. Keep that
    boundary stable and preserve numeric types and unpaired authored breaks.
    """
    from . import _outside_katex

    if raw_equation or not isinstance(value, str):
        return value
    return _outside_katex(
        value, lambda segment: _DISPLAY_BREAK.sub(r"\1", segment),
    )


def equation_type_field(field: str) -> str | None:
    """The declared type column of one raw answer/criterion payload column."""
    match = re.fullmatch(r"(?:answer_content|answer)_(\d+)", field)
    if match:
        return f"answer_type_{match.group(1)}"
    match = re.fullmatch(r"sq(\d+)_keyword_(\d+)", field)
    if match:
        return f"sq{match.group(1)}_answer_type_{match.group(2)}"
    return None


def is_equation_field(field: str, record: Mapping) -> bool:
    type_field = equation_type_field(field)
    return bool(
        type_field
        and str(record.get(type_field) or "").strip().casefold() == "equation"
    )


def is_equation_cell(cell) -> bool:
    """Read a cell's sibling type; never infer mathematical meaning from text."""
    worksheet = cell.parent
    field = str(worksheet.cell(row=2, column=cell.column).value or "")
    type_field = equation_type_field(field)
    if type_field is None:
        return False
    columns = getattr(worksheet, "_aegis_field_columns", None)
    if columns is None:
        columns = {
            str(header.value): header.column for header in worksheet[2]
            if header.value is not None
        }
        worksheet._aegis_field_columns = columns
    type_column = columns.get(type_field)
    if type_column is None:
        return False
    declared = worksheet.cell(row=cell.row, column=type_column).value
    return str(declared or "").strip().casefold() == "equation"


def field_width(field: str) -> float:
    if field in _FIELD_WIDTHS:
        return _FIELD_WIDTHS[field]
    if _RICH_SLOT_FIELD.fullmatch(field):
        return 64
    if _NUMERIC_SLOT_FIELD.fullmatch(field):
        return 16
    return 22


def apply_sheet_presentation(worksheet, fields: Iterable[str]) -> None:
    """Style existing columns and headers, without changing their geometry.

    Data rows have no fixed height so spreadsheet viewers can auto-fit the
    wrapped content. No assessment row, native data column or sheet is added.
    """
    fields = tuple(fields)
    worksheet._aegis_field_columns = {
        field: column for column, field in enumerate(fields, start=1)
    }
    for column, field in enumerate(fields, start=1):
        worksheet.column_dimensions[get_column_letter(column)].width = (
            field_width(field)
        )
        cell = worksheet.cell(row=2, column=column)
        cell.alignment = HEADER_ALIGNMENT
        cell.fill = PatternFill("solid", fgColor="EAF0F6")
    worksheet.row_dimensions[1].height = 24
    worksheet.row_dimensions[2].height = 44

"""Registry of the Bulk Import workbook layouts this tool can read.

A workbook is *identified* by its header row (row 2) and its sheet name, by
exact equality against a registered layout. Nothing is guessed: a header that
matches no registered layout is refused, and every column is then addressed
**by name** through the identified layout instead of by a position derived
from today's canonical constants.

Why this module exists (spec-step8 T6, slice S2). ``reader.import_workbook``
used to address columns positionally against ``bulk_import.FIELDS_BY_KIND``
and to skip any sheet whose *name* it did not recognise, with no issue
recorded. On the accepted reference workbooks that silently dropped the whole
Objective sheet (``SHEET_OBJECTIVE`` is ``"Objective "`` — with a trailing
space — and the reference sheet is ``"Objective"``) and mis-banded the other
two (342 of 344 Descriptive and 63 of 63 Subjective question-band positions
read the wrong column). Identification + name addressing is pure mechanics: it
makes no judgment about what any cell MEANS, it only declines to guess which
column a value came from.

Two kinds of entry live here, and the difference is load-bearing:

* ``sop-mes-1`` is read AT IMPORT TIME from the committed format workbook
  ``backend/data/Testing/reference_bulk_import/bulk_import_format.xlsx`` — the
  owner-supplied layout authority (T3.1b). ``assessment_workbook_template.json``
  is compared against it and is a derived convenience cache: **on any
  disagreement the workbook wins** and a ``layout_manifest_drift`` defect is
  recorded (never a halt — Q13).
* the three ``canonical-*`` entries are **FROZEN LITERAL transcriptions** of
  the layouts this repo has historically written. They are deliberately NOT
  derived from ``bulk_import.FIELDS_BY_KIND``: slice S7 redefines those
  constants to the reference layout, and an entry derived from them would
  silently BECOME the reference layout, un-registering the canonical one and
  turning every older workbook into a 422. S7 must not touch the literals in
  this file.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import openpyxl

# --------------------------------------------------------------------------- #
# Errors and recorded defects
# --------------------------------------------------------------------------- #


class WorkbookLayoutError(ValueError):
    """A workbook's header row matches no registered layout.

    Mechanical gate on an artifact (CLAUDE.md "gates that refuse to accept a
    broken artifact"): it judges nothing about content, it declines to read
    columns whose meaning it cannot establish. ``import_workbook`` raises it
    before any ``db.add``, so an unidentified workbook imports nothing at all
    rather than importing part of itself wrongly.
    """

    def __init__(self, message: str, *, detail: Mapping | None = None) -> None:
        super().__init__(message)
        self.detail = dict(detail or {})


_REGISTRY_DEFECTS: list[dict] = []


def registry_defects() -> list[dict]:
    """Defects recorded while BUILDING the registry (never a halt).

    Today this carries ``layout_manifest_drift`` (the committed format
    workbook and the template JSON disagree; the workbook won) and
    ``layout_source_missing`` (the committed workbook is not on disk, so the
    reference layout is not registered in this process).
    """
    return [dict(defect) for defect in _REGISTRY_DEFECTS]


# --------------------------------------------------------------------------- #
# Layout objects
# --------------------------------------------------------------------------- #

_ANSWER_BLOCK_RE = re.compile(r"^answer_type_(\d+)$")
_SUBQUESTION_RE = re.compile(r"^sub_question_(\d+)$")

@dataclass(frozen=True)
class Band:
    """One row-1 section band, 1-based inclusive column range."""

    label: str
    start: int
    end: int

    def as_dict(self) -> dict:
        return {"label": self.label, "start": self.start, "end": self.end}


class SheetLayout:
    """One sheet of one layout: its exact field names and their addresses."""

    __slots__ = ("layout_id", "kind", "sheet_name", "fields", "bands",
                 "blocks", "_index")

    def __init__(self, layout_id: str, kind: str, sheet_name: str,
                 blocks: Sequence[tuple[str, Sequence[str]]],
                 bands: Sequence[Band]) -> None:
        self.layout_id = layout_id
        self.kind = kind
        self.sheet_name = sheet_name
        self.blocks: tuple[tuple[str, tuple[str, ...]], ...] = tuple(
            (name, tuple(names)) for name, names in blocks
        )
        self.fields: tuple[str, ...] = tuple(
            name for _block, names in self.blocks for name in names
        )
        self.bands: tuple[Band, ...] = tuple(bands)
        index: dict[tuple[str, str], int] = {}
        position = 0
        for block, names in self.blocks:
            for name in names:
                # Band-qualified so a layout that repeats a field name across
                # bands (canonical Descriptive carries ``question_label`` in
                # both the Group and the Question band) resolves explicitly
                # instead of by scanning.
                index.setdefault((block, name), position)
                position += 1
        self._index = index

    # -- addressing ------------------------------------------------------- #

    def block_fields(self, block: str) -> tuple[str, ...]:
        for name, names in self.blocks:
            if name == block:
                return names
        return ()

    def block_start(self, block: str) -> int:
        position = 0
        for name, names in self.blocks:
            if name == block:
                return position
            position += len(names)
        return position

    def column(self, block: str, field: str) -> int | None:
        """0-based column index of one band-qualified field, or None."""
        return self._index.get((block, field))

    def block_values(self, row: Sequence, block: str) -> dict[str, str]:
        """Every field of one band, read BY NAME, stripped."""
        out: dict[str, str] = {}
        for field in self.block_fields(block):
            index = self._index[(block, field)]
            value = row[index] if index < len(row) else None
            out.setdefault(
                field, "" if value is None else str(value).strip())
        return out

    # -- derived, mechanical counts --------------------------------------- #

    @property
    def question_scalar_fields(self) -> tuple[str, ...]:
        """Question-band fields before the first answer block.

        Derived from the layout's own field list, never hard-coded: the
        canonical sheets yield 10/11/12 and the reference sheets 12/13/14.
        """
        out: list[str] = []
        for field in self.block_fields("question"):
            if _ANSWER_BLOCK_RE.match(field):
                break
            out.append(field)
        return tuple(out)

    @property
    def answer_block_numbers(self) -> tuple[int, ...]:
        return tuple(sorted(
            int(match.group(1))
            for match in (
                _ANSWER_BLOCK_RE.match(field)
                for field in self.block_fields("question")
            )
            if match
        ))

    @property
    def sub_question_numbers(self) -> tuple[int, ...]:
        return tuple(sorted(
            int(match.group(1))
            for match in (
                _SUBQUESTION_RE.match(field)
                for field in self.block_fields("question")
            )
            if match
        ))

    def sub_question_keyword_numbers(self, number: int) -> tuple[int, ...]:
        prefix = re.compile(rf"^sq{number}_answer_type_(\d+)$")
        return tuple(sorted(
            int(match.group(1))
            for match in (
                prefix.match(field) for field in self.block_fields("question")
            )
            if match
        ))


class Layout:
    """One registered workbook layout: its sheets and its ignored sheets."""

    __slots__ = ("id", "sheets", "ignored_sheets", "source")

    def __init__(self, layout_id: str, sheets: Mapping[str, SheetLayout],
                 ignored_sheets: Sequence[str] = (), source: str = "") -> None:
        self.id = layout_id
        self.sheets = dict(sheets)
        self.ignored_sheets = tuple(ignored_sheets)
        self.source = source

    def sheet(self, kind: str) -> SheetLayout:
        return self.sheets[kind]

    def sheet_name_by_kind(self) -> dict[str, str]:
        return {kind: sheet.sheet_name for kind, sheet in self.sheets.items()}

    def fields_by_kind(self) -> dict[str, tuple[str, ...]]:
        return {kind: sheet.fields for kind, sheet in self.sheets.items()}

    def bands_by_kind(self) -> dict[str, list[dict]]:
        return {
            kind: [band.as_dict() for band in sheet.bands]
            for kind, sheet in self.sheets.items()
        }


# --------------------------------------------------------------------------- #
# FROZEN LITERAL canonical transcriptions
#
# Every tuple below is written out here on purpose. It is a transcription of
# the layout this repo wrote before slice S7, and it must NEVER be re-derived
# from ``bulk_import.FIELDS_BY_KIND`` / ``SECTION_BANDS`` / ``SHEET_BY_KIND``:
# S7 redefines those to the reference layout, and a derived entry would follow
# it, silently un-registering the canonical layout that older workbooks use.
# The regular answer / sub-question blocks are expanded from FROZEN LITERAL
# counts below, the way this package has always described them ("the answer /
# sub-question blocks are regular, so they are generated rather than
# transcribed" — bulk_import/__init__.py), so the expansion depends on nothing
# outside this module.
# --------------------------------------------------------------------------- #

# Sheet names, frozen — note the trailing space on Objective. It is the exact
# byte sequence historical workbooks carry, and getting it wrong is the bug
# this module exists to make impossible.
_FROZEN_SHEET_NAMES = {
    "objective": "Objective ",
    "subjective": "Subjective",
    "descriptive": "Descriptive",
}
_FROZEN_DOC_LINK_SHEET = "Doc Link <> Each fields "

_FROZEN_CHAPTER_FIELDS = (
    "chapter_title", "chapter_display_name", "chapter_duration",
    "pre_topics", "post_topics", "chapter_description",
)
_FROZEN_TOPIC_FIELDS = (
    "topic_title", "topic_display_name", "pre_post_learning",
    "topic_concept_labels", "related_topics", "topic_description",
)
_FROZEN_CONCEPT_FIELDS = (
    "concept_title", "concept_display_name", "parent_concept",
    "concept_details", "digicards",
    "basic_groups", "intermediate_groups", "advanced_groups",
    "concept_source",
)
_FROZEN_LEGACY_CONCEPT_FIELDS = (
    "concept_title", "concept_display_name", "concept_details",
    "keywords", "digicards", "related_concepts",
    "basic_groups", "intermediate_groups", "advanced_groups",
)
_FROZEN_OBJECTIVE_GROUP_FIELDS = (
    "concept_question_labels",
    "group_name", "group_display_name", "group_description",
    "group_status", "group_type",
    "group_question_labels", "related_digicards",
)
_FROZEN_SUBJECTIVE_GROUP_FIELDS = _FROZEN_OBJECTIVE_GROUP_FIELDS
_FROZEN_DESCRIPTIVE_GROUP_FIELDS = (
    "concept_question_labels", "group_display_name", "group_description",
    "group_name", "group_status", "group_type",
    "question_label", "group_question_labels", "related_digicards",
)

# The 10 / 11 / 12 scalar question fields, frozen literally. The reader used
# to hard-code these three counts (reader.py:85/96/108); they are properties
# of a LAYOUT, and every consumer now derives them from the field list above
# (`SheetLayout.question_scalar_fields`).
_FROZEN_OBJECTIVE_SCALARS = (
    "question_label", "question_category", "cognitive_skills",
    "question_source", "question_disclaimer", "question_duration",
    "question_appears_in", "level_of_difficulty", "question", "marks",
)
_FROZEN_SUBJECTIVE_SCALARS = (
    "question_label", "question_category", "cognitive_skills",
    "question_source", "question_disclaimer", "question_duration",
    "math_keyboard", "question_appears_in", "level_of_difficulty",
    "question", "marks",
)
_FROZEN_DESCRIPTIVE_SCALARS = (
    "question_label", "question_category", "cognitive_skills",
    "question_source", "question_disclaimer", "question_duration",
    "math_keyboard", "question_appears_in", "level_of_difficulty",
    "question", "marks", "display_answer",
)

# Frozen block counts of the canonical layout.
_FROZEN_OBJECTIVE_ANSWER_BLOCKS = 6      # answer_type_1 .. answer_type_6
_FROZEN_SUBJECTIVE_ANSWER_BLOCKS = 10    # the reader's historical range(10)
_FROZEN_DESCRIPTIVE_ANSWER_BLOCKS = 10
_FROZEN_DESCRIPTIVE_SUBQUESTIONS = 15
_FROZEN_DESCRIPTIVE_SUBQUESTION_KEYWORDS = 6


def _frozen_objective_question_fields(*, question_text: bool) -> tuple[str, ...]:
    fields = list(_FROZEN_OBJECTIVE_SCALARS)
    for n in range(1, _FROZEN_OBJECTIVE_ANSWER_BLOCKS + 1):
        for prefix in ("answer_type", "answer_content", "correct_answer",
                       "answer_weightage"):
            fields.append(f"{prefix}_{n}")
    fields.append("answer_explanation")
    if question_text:
        fields.append("question_text")
    return tuple(fields)


def _frozen_subjective_question_fields(*, question_text: bool) -> tuple[str, ...]:
    fields = list(_FROZEN_SUBJECTIVE_SCALARS)
    for n in range(1, _FROZEN_SUBJECTIVE_ANSWER_BLOCKS + 1):
        for prefix in ("answer_type", "answer", "answer_display", "weightage",
                       "placeholder"):
            fields.append(f"{prefix}_{n}")
    fields.append("answer_explanation")
    if question_text:
        fields.append("question_text")
    return tuple(fields)


def _frozen_descriptive_question_fields(*, question_text: bool) -> tuple[str, ...]:
    fields = list(_FROZEN_DESCRIPTIVE_SCALARS)
    for n in range(1, _FROZEN_DESCRIPTIVE_ANSWER_BLOCKS + 1):
        for prefix in ("answer_type", "answer_weightage", "answer_content"):
            fields.append(f"{prefix}_{n}")
    fields.append("answer_explanation")
    for n in range(1, _FROZEN_DESCRIPTIVE_SUBQUESTIONS + 1):
        fields.append(f"sub_question_{n}")
        fields.append(f"sub_question_marks_{n}")
        for m in range(1, _FROZEN_DESCRIPTIVE_SUBQUESTION_KEYWORDS + 1):
            for prefix in ("answer_type", "weightage", "keyword"):
                fields.append(f"sq{n}_{prefix}_{m}")
    if question_text:
        fields.append("question_text")
    return tuple(fields)


def _bands_from_spans(spans: Sequence[tuple[str, int]]) -> tuple[Band, ...]:
    bands: list[Band] = []
    column = 1
    for label, span in spans:
        bands.append(Band(label, column, column + span - 1))
        column += span
    return tuple(bands)


def _canonical_layout(layout_id: str, *, concept_fields: Sequence[str],
                      question_text: bool) -> Layout:
    """Build one frozen canonical entry from the literals above."""
    question_by_kind = {
        "objective": _frozen_objective_question_fields(
            question_text=question_text),
        "subjective": _frozen_subjective_question_fields(
            question_text=question_text),
        "descriptive": _frozen_descriptive_question_fields(
            question_text=question_text),
    }
    group_by_kind = {
        "objective": _FROZEN_OBJECTIVE_GROUP_FIELDS,
        "subjective": _FROZEN_SUBJECTIVE_GROUP_FIELDS,
        "descriptive": _FROZEN_DESCRIPTIVE_GROUP_FIELDS,
    }
    sheets: dict[str, SheetLayout] = {}
    for kind, question_fields in question_by_kind.items():
        blocks = (
            ("chapter", _FROZEN_CHAPTER_FIELDS),
            ("topic", _FROZEN_TOPIC_FIELDS),
            ("concept", tuple(concept_fields)),
            ("group", group_by_kind[kind]),
            ("question", question_fields),
        )
        # Row-1 bands of the canonical sheets: the Concept band visually owns
        # the trailing linkage column and the Group band is one shorter, which
        # is why ``bands`` and ``blocks`` are separate things here.
        spans = (
            ("Chapter", len(_FROZEN_CHAPTER_FIELDS)),
            ("Topic", len(_FROZEN_TOPIC_FIELDS)),
            ("Concept", len(concept_fields) + 1),
            ("Group", len(group_by_kind[kind]) - 1),
            ("Question", len(question_fields)),
        )
        sheets[kind] = SheetLayout(
            layout_id, kind, _FROZEN_SHEET_NAMES[kind], blocks,
            _bands_from_spans(spans),
        )
    return Layout(layout_id, sheets,
                  ignored_sheets=(_FROZEN_DOC_LINK_SHEET,),
                  source="frozen literal transcription (spec-step8 T6)")


# --------------------------------------------------------------------------- #
# sop-mes-1 — read from the COMMITTED FORMAT WORKBOOK (T3.1b)
# --------------------------------------------------------------------------- #

REFERENCE_LAYOUT_ID = "sop-mes-1"
REFERENCE_WORKBOOK_PATH = (
    Path(__file__).resolve().parents[2]
    / "data" / "Testing" / "reference_bulk_import" / "bulk_import_format.xlsx"
)
# The template JSON is a DERIVED convenience cache of the workbook above, not
# an independent authority. It is compared, never trusted over the workbook.
MANIFEST_PATH = Path(__file__).with_name("assessment_workbook_template.json")

_REFERENCE_KIND_BY_SHEET = {
    "Objective": "objective",
    "Descriptive": "descriptive",
    "Subjective": "subjective",
}


def _header_names(values: Iterable) -> tuple[str, ...]:
    names = ["" if value is None else str(value).strip() for value in values]
    while names and not names[-1]:
        names.pop()
    return tuple(names)


def _blocks_from_bands(fields: Sequence[str],
                       bands: Sequence[Band]) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Partition a sheet's columns using its own row-1 bands.

    A column that carries no band of its own belongs to the nearest band that
    starts at or before it — pure column arithmetic (the reference Objective
    sheet leaves ``concept_source`` unbanded between Concept and Group, and
    every column after the one-cell ``Question`` band is question content).
    """
    ordered = sorted(bands, key=lambda band: band.start)
    blocks: list[tuple[str, list[str]]] = []
    for band in ordered:
        blocks.append((band.label.strip().casefold(), []))
    for position, field in enumerate(fields, start=1):
        target = 0
        for i, band in enumerate(ordered):
            if band.start <= position:
                target = i
        blocks[target][1].append(field)
    return tuple((name, tuple(names)) for name, names in blocks)


def _sheet_bands(worksheet, field_count: int) -> tuple[Band, ...]:
    merged = {
        cell_range.min_col: cell_range.max_col
        for cell_range in worksheet.merged_cells.ranges
        if cell_range.min_row == 1 and cell_range.max_row == 1
    }
    bands: list[Band] = []
    for column in range(1, field_count + 1):
        value = worksheet.cell(row=1, column=column).value
        label = "" if value is None else str(value)
        if not label.strip():
            continue
        bands.append(Band(label, column, merged.get(column, column)))
    return tuple(bands)


def build_reference_layout(
    workbook_path: Path | None = None,
    manifest: Mapping | None = None,
) -> tuple[Layout | None, list[dict]]:
    """Build ``sop-mes-1`` from the committed workbook; compare the JSON.

    Returns ``(layout, defects)``. The WORKBOOK WINS on any disagreement: the
    returned layout is always the workbook's, and a ``layout_manifest_drift``
    defect names the sheet, the first divergent column index and both values.
    A drift never halts anything (Q13) — the run completes on the workbook's
    layout.
    """
    path = Path(workbook_path or REFERENCE_WORKBOOK_PATH)
    defects: list[dict] = []
    if not path.exists():
        defects.append({
            "code": "layout_source_missing",
            "path": str(path),
            "detail": (
                "the committed Bulk Import format workbook is not on disk, so "
                f"{REFERENCE_LAYOUT_ID} is not registered in this process"
            ),
        })
        return None, defects

    workbook = openpyxl.load_workbook(path, data_only=True)
    try:
        sheets: dict[str, SheetLayout] = {}
        sheet_order: list[str] = []
        for sheet_name in workbook.sheetnames:
            kind = _REFERENCE_KIND_BY_SHEET.get(sheet_name)
            if kind is None:
                defects.append({
                    "code": "layout_sheet_unknown",
                    "sheet": sheet_name,
                    "detail": "no content kind is declared for this sheet",
                })
                continue
            worksheet = workbook[sheet_name]
            fields = _header_names(
                next(worksheet.iter_rows(min_row=2, max_row=2,
                                         values_only=True), ())
            )
            bands = _sheet_bands(worksheet, len(fields))
            sheets[kind] = SheetLayout(
                REFERENCE_LAYOUT_ID, kind, sheet_name,
                _blocks_from_bands(fields, bands), bands,
            )
            sheet_order.append(sheet_name)
    finally:
        workbook.close()

    if not sheets:
        defects.append({
            "code": "layout_source_missing",
            "path": str(path),
            "detail": "the format workbook carries no recognised content sheet",
        })
        return None, defects

    cache = manifest
    if cache is None:
        try:
            cache = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cache = None
    if cache is not None:
        defects.extend(_manifest_drift(sheets, sheet_order, cache, path))

    return Layout(REFERENCE_LAYOUT_ID, sheets, ignored_sheets=(),
                  source=f"committed format workbook {path.name}"), defects


def _manifest_drift(sheets: Mapping[str, SheetLayout], sheet_order: Sequence[str],
                    manifest: Mapping, path: Path) -> list[dict]:
    """Tuple inequality between the workbook and its derived JSON cache."""
    defects: list[dict] = []
    cached_order = list(manifest.get("sheet_order") or [])
    if cached_order != list(sheet_order):
        defects.append({
            "code": "layout_manifest_drift",
            "layout_id": REFERENCE_LAYOUT_ID,
            "sheet": "",
            "column_index": None,
            "workbook_value": list(sheet_order),
            "manifest_value": cached_order,
            "detail": "sheet order differs; the workbook's order is used",
            "source": str(path),
        })
    cached_sheets = manifest.get("sheets") or {}
    for kind, sheet in sheets.items():
        cached = (cached_sheets.get(sheet.sheet_name) or {}).get("fields")
        if cached is None:
            defects.append({
                "code": "layout_manifest_drift",
                "layout_id": REFERENCE_LAYOUT_ID,
                "sheet": sheet.sheet_name,
                "column_index": None,
                "workbook_value": len(sheet.fields),
                "manifest_value": None,
                "detail": "the JSON cache carries no field list for this sheet",
                "source": str(path),
            })
            continue
        cached_fields = list(cached)
        if cached_fields == list(sheet.fields):
            continue
        index = next(
            (
                i for i in range(max(len(cached_fields), len(sheet.fields)))
                if (sheet.fields[i] if i < len(sheet.fields) else None)
                != (cached_fields[i] if i < len(cached_fields) else None)
            ),
            None,
        )
        defects.append({
            "code": "layout_manifest_drift",
            "layout_id": REFERENCE_LAYOUT_ID,
            "sheet": sheet.sheet_name,
            "column_index": index,
            "workbook_value": (
                sheet.fields[index] if index is not None
                and index < len(sheet.fields) else None
            ),
            "manifest_value": (
                cached_fields[index] if index is not None
                and index < len(cached_fields) else None
            ),
            "detail": (
                "the committed format workbook and its JSON cache disagree; "
                "the workbook wins"
            ),
            "source": str(path),
        })
    return defects


# --------------------------------------------------------------------------- #
# The registry
# --------------------------------------------------------------------------- #

CANONICAL_CURRENT = _canonical_layout(
    "canonical-current",
    concept_fields=_FROZEN_CONCEPT_FIELDS, question_text=True,
)
# The 64-column Objective variant (and its siblings): templates predating the
# trailing ``question_text`` column. These really do reach the reader.
CANONICAL_NO_QUESTION_TEXT = _canonical_layout(
    "canonical-no-question-text",
    concept_fields=_FROZEN_CONCEPT_FIELDS, question_text=False,
)
# The concept band of the OLD layout (no parent_concept / concept_source, with
# keywords / related_concepts).
CANONICAL_LEGACY_CONCEPT_BAND = _canonical_layout(
    "canonical-legacy-concept-band",
    concept_fields=_FROZEN_LEGACY_CONCEPT_FIELDS, question_text=True,
)

LAYOUTS: dict[str, Layout] = {}


def register(layout: Layout) -> None:
    LAYOUTS[layout.id] = layout


_reference_layout, _reference_defects = build_reference_layout()
_REGISTRY_DEFECTS.extend(_reference_defects)
for _defect in _REGISTRY_DEFECTS:
    # Recorded and visible, never a halt (Q13). Surfacing these on the release
    # audit is the audit seam's job and arrives with it; nothing here guesses.
    logging.getLogger(__name__).warning(
        "bulk-import layout registry: %s (%s)",
        _defect.get("code"), _defect.get("detail"),
    )
if _reference_layout is not None:
    register(_reference_layout)
register(CANONICAL_CURRENT)
register(CANONICAL_NO_QUESTION_TEXT)
register(CANONICAL_LEGACY_CONCEPT_BAND)


def layout(layout_id: str) -> Layout:
    return LAYOUTS[layout_id]


def sheet(layout_id: str, kind: str) -> SheetLayout:
    return LAYOUTS[layout_id].sheet(kind)


@dataclass(frozen=True)
class SheetIdentity:
    layout_id: str
    kind: str
    sheet_name: str

    @property
    def sheet(self) -> SheetLayout:
        return LAYOUTS[self.layout_id].sheet(self.kind)


def identify_sheet(sheet_name: str, header_names: Iterable) -> SheetIdentity | None:
    """Identify one sheet by EXACT sheet name + header tuple equality.

    No prefix matching, no fuzzy matching, no scanning. A trailing space in a
    sheet name makes a different layout, not a skip.
    """
    names = _header_names(header_names)
    if not names:
        return None
    for entry in LAYOUTS.values():
        for kind, candidate in entry.sheets.items():
            if candidate.sheet_name == sheet_name and candidate.fields == names:
                return SheetIdentity(entry.id, kind, sheet_name)
    return None


@dataclass(frozen=True)
class WorkbookIdentity:
    layout_id: str
    sheets: tuple[SheetIdentity, ...]
    ignored_sheets: tuple[str, ...]

    @property
    def layout(self) -> Layout:
        return LAYOUTS[self.layout_id]


def _closest(sheet_name: str, names: Sequence[str]) -> tuple[SheetLayout | None, int | None]:
    """The registered sheet a header most nearly is, by common prefix."""
    best: SheetLayout | None = None
    best_score = -1
    for entry in LAYOUTS.values():
        for candidate in entry.sheets.values():
            shared = 0
            for a, b in zip(candidate.fields, names):
                if a != b:
                    break
                shared += 1
            score = shared + (1000 if candidate.sheet_name == sheet_name else 0)
            if score > best_score:
                best, best_score = candidate, score
    if best is None:
        return None, None
    divergent = next(
        (
            i for i in range(max(len(best.fields), len(names)))
            if (best.fields[i] if i < len(best.fields) else None)
            != (names[i] if i < len(names) else None)
        ),
        None,
    )
    return best, divergent


def _refuse(sheet_name: str, names: Sequence[str], reason: str) -> WorkbookLayoutError:
    closest, divergent = _closest(sheet_name, names)
    detail = {
        "sheet": sheet_name,
        "column_count": len(names),
        "reason": reason,
        "closest_layout_id": closest.layout_id if closest else None,
        "closest_kind": closest.kind if closest else None,
        "closest_sheet_name": closest.sheet_name if closest else None,
        "closest_column_count": len(closest.fields) if closest else None,
        "first_divergent_index": divergent,
        "found": (
            names[divergent]
            if divergent is not None and divergent < len(names) else None
        ),
        "expected": (
            closest.fields[divergent]
            if closest and divergent is not None
            and divergent < len(closest.fields) else None
        ),
    }
    message = (
        f"unrecognised Bulk Import layout: sheet {sheet_name!r} carries "
        f"{len(names)} columns and matches no registered layout ({reason})."
    )
    if closest is not None:
        message += (
            f" Closest registered layout: {closest.layout_id!r} sheet "
            f"{closest.sheet_name!r} ({len(closest.fields)} columns)"
        )
        if divergent is not None:
            message += (
                f"; first difference at column {divergent + 1}: found "
                f"{detail['found']!r}, expected {detail['expected']!r}"
            )
        message += "."
    return WorkbookLayoutError(message, detail=detail)


def identify_workbook(headers: Mapping[str, Sequence]) -> WorkbookIdentity:
    """Identify a whole workbook, or refuse it.

    ``headers`` maps every sheet name in the file to its row-2 values, in
    workbook order. Exactly one registered layout must claim every content
    sheet; a sheet that no layout claims and that the claimed layout does not
    declare as a non-content sheet refuses the WHOLE workbook — reading part
    of a file whose geometry is unknown is the corruption this gate exists to
    prevent.
    """
    identified: list[SheetIdentity] = []
    unidentified: list[tuple[str, tuple[str, ...]]] = []
    for sheet_name, values in headers.items():
        names = _header_names(values)
        found = identify_sheet(sheet_name, names)
        if found is None:
            unidentified.append((sheet_name, names))
        else:
            identified.append(found)

    if not identified:
        sheet_name, names = next(
            ((name, values) for name, values in unidentified if values),
            (next(iter(headers), ""), ()),
        )
        raise _refuse(sheet_name, names, "no sheet matched a registered layout")

    layout_ids = {found.layout_id for found in identified}
    if len(layout_ids) > 1:
        first = identified[0]
        raise _refuse(
            first.sheet_name, first.sheet.fields,
            "the sheets of this workbook match "
            f"{len(layout_ids)} different layouts ({', '.join(sorted(layout_ids))})",
        )

    layout_id = layout_ids.pop()
    entry = LAYOUTS[layout_id]
    ignored: list[str] = []
    for sheet_name, names in unidentified:
        if sheet_name in entry.ignored_sheets:
            ignored.append(sheet_name)
            continue
        raise _refuse(
            sheet_name, names,
            f"the rest of this workbook is layout {layout_id!r}",
        )
    return WorkbookIdentity(layout_id, tuple(identified), tuple(ignored))

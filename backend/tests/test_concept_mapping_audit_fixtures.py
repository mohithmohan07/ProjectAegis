"""Exhaustive, read-only checks for the 2026-08-27 audit workbooks.

The reviewer-supplied originals and ``Corrected Outputs`` are evidence, not a
license to copy their remaining defects.  These tests therefore do three
separate jobs:

* pin every fixture byte;
* compare every original/corrected pair across the union of logical rows and
  columns, and pin every value in every corrected logical row/schema column;
* assert the corrected-intent production contract independently of the raw
  header defects and content contradictions recorded in those fixtures.

The small XML reader is intentional.  Several workbooks contain hundreds of
styled empty rows/columns; loading their styles through openpyxl makes a
content-only audit needlessly slow and can hide the distinction between a
logical cell and a styled empty tail.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from typing import Iterable
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest

from app.bulk_import import assessment_workbook as workbook
from app.services import assessment_profile
from app.services import identity
from app.services import katex_rules


FIXTURE_DIR = Path(__file__).resolve().parents[1] / (
    "data/Testing/concept_mapping_audit"
)
TEMPLATE_PATH = Path(__file__).resolve().parents[1] / (
    "app/bulk_import/assessment_workbook_template.json"
)

FILE_HASHES = {
    "english_post_concept.xlsx": (
        "9b2eba7458927851698b4b4fcdde4b2490b38643aa9db996934d8e1d87e10a05"
    ),
    "english_post_concept_original.xlsx": (
        "b2b268648d18d4544d97c610a5f72869254ba4b721f421fba4e2160b629b959c"
    ),
    "english_post_master.xlsx": (
        "7c96d40927c05ac025b7ac9e57848d200610387ad7ff2cda9dcdd0b18cdfb6db"
    ),
    "english_post_master_original.xlsx": (
        "9afd438ec1ee24a5ed586e6357066a63dcd5f15e33424fcc5f72df575dd0c1e5"
    ),
    "english_pre_concept.xlsx": (
        "a50c5dc2b0393da58f6ee8fc94cb03b85d0fdbdcc0813d42f57512f81058b6a8"
    ),
    "english_pre_concept_original.xlsx": (
        "ad19e404ce9b61cb8c5f74d036019ec944ae9bf11509f243d0d5900c477d3755"
    ),
    "english_pre_master.xlsx": (
        "a9f7deddacaea53293bfeb10a9662377019c46efb635beef21593a46cf1e014b"
    ),
    "english_pre_master_original.xlsx": (
        "44e8b7033a6b647c00c086d7cd60369fa70449088346dac19999658d2e4c92ba"
    ),
    "math_post_concept.xlsx": (
        "e940d16bfd972e5bd842e43542e8c211feeaa1a39bf8fdd907734fb9e5b006ae"
    ),
    "math_post_concept_original.xlsx": (
        "adcd5b05a47dd60fbe8e1540273fa8bbdede0c6bbfef28ba2969ac42a31a07d8"
    ),
    "math_post_master.xlsx": (
        "045257aad750cc8e80b3d1401238d2102f1d825ce11e53b6a433918b6021d198"
    ),
    "math_post_master_original.xlsx": (
        "252c31be2ae8b794e38d8355782ccc6de5726eeb7374e23a7877c4559832f1da"
    ),
    "math_pre_concept.xlsx": (
        "81e409b8b4a5b81ae8fcd1e1e72d6fd88a9eb3eac6b5db8cec30f9560bc074c3"
    ),
    "math_pre_concept_original.xlsx": (
        "421c3ebc72f65241efa30cd98fc39b1eec28d266e74cf98b9445e100eaa56388"
    ),
    "math_pre_master.xlsx": (
        "93f3311bc6b41ddac51b00fe8bde73f4956a73bfc5314b18c7ece1825165caa1"
    ),
    "math_pre_master_original.xlsx": (
        "5475c56db37addabe9c8ed79f6a34f2dd12f8926f68f0dd9b9c11bc8348f2262"
    ),
}

_SPREADSHEET_NS = (
    "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
)
_OFFICE_REL_NS = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
)
_NS = {"main": _SPREADSHEET_NS, "rel": _OFFICE_REL_NS}
_CELL_REF = re.compile(r"^([A-Z]+)([0-9]+)$", re.IGNORECASE)


@dataclass(frozen=True)
class RawSheet:
    title: str
    physical_rows: tuple[int, ...]
    rows: dict[int, dict[int, str]]

    @property
    def logical_rows(self) -> tuple[int, ...]:
        return tuple(
            row_number
            for row_number in self.physical_rows
            if row_number >= 3
            and any(value != "" for value in self.rows[row_number].values())
        )

    @property
    def header_width(self) -> int:
        return max(
            (
                column
                for column, value in self.rows.get(2, {}).items()
                if value != ""
            ),
            default=0,
        )

    @property
    def headers(self) -> tuple[str, ...]:
        row = self.rows.get(2, {})
        return tuple(row.get(column, "") for column in range(1, self.header_width + 1))

    @property
    def occupied_column(self) -> int:
        return max(
            (
                column
                for row in self.rows.values()
                for column in row
            ),
            default=0,
        )

    def first_column(self, field: str) -> int:
        for column, header in enumerate(self.headers, start=1):
            if header.strip() == field:
                return column
        raise KeyError(f"{self.title}: missing {field!r}")

    def logical_records(self) -> list[dict[str, str]]:
        """Rows keyed by the first stripped occurrence of each raw header."""

        positions: dict[str, int] = {}
        for column, header in enumerate(self.headers, start=1):
            positions.setdefault(header.strip(), column)
        return [
            {
                field: self.rows[row_number].get(column, "")
                for field, column in positions.items()
                if field
            }
            for row_number in self.logical_rows
        ]


@dataclass(frozen=True)
class RawWorkbook:
    sheet_order: tuple[str, ...]
    sheets: dict[str, RawSheet]


def _column_number(reference: str) -> tuple[int, int]:
    match = _CELL_REF.fullmatch(reference)
    if match is None:
        raise AssertionError(f"invalid XLSX cell reference {reference!r}")
    column = 0
    for character in match.group(1).upper():
        column = column * 26 + ord(character) - ord("A") + 1
    return column, int(match.group(2))


def _relationship_target(target: str) -> str:
    path = PurePosixPath(target)
    if path.is_absolute():
        return str(path).lstrip("/")
    if path.parts and path.parts[0] == "xl":
        return str(path)
    return str(PurePosixPath("xl") / path)


def _shared_strings(archive: ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    return [
        "".join(
            node.text or ""
            for node in item.iter(f"{{{_SPREADSHEET_NS}}}t")
        )
        for item in root.findall("main:si", _NS)
    ]


def _cell_value(cell: ET.Element, shared_strings: list[str]) -> str:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        value = "".join(
            node.text or ""
            for node in cell.iter(f"{{{_SPREADSHEET_NS}}}t")
        )
    else:
        value_node = cell.find("main:v", _NS)
        raw = value_node.text or "" if value_node is not None else ""
        if cell_type == "s" and raw:
            value = shared_strings[int(raw)]
        elif cell_type == "b":
            value = "TRUE" if raw == "1" else "FALSE"
        else:
            value = raw

    # No audit cell currently contains a formula.  Keeping it in the pinned
    # representation makes the traversal fail loudly if one is introduced.
    formula = cell.find("main:f", _NS)
    if formula is not None:
        return f"={formula.text or ''}||{value}"
    return value


@lru_cache(maxsize=None)
def _read_fixture(filename: str) -> RawWorkbook:
    with ZipFile(FIXTURE_DIR / filename) as archive:
        shared_strings = _shared_strings(archive)
        workbook_xml = ET.fromstring(archive.read("xl/workbook.xml"))
        relationships_xml = ET.fromstring(
            archive.read("xl/_rels/workbook.xml.rels")
        )
        relationships = {
            element.attrib["Id"]: element.attrib["Target"]
            for element in relationships_xml
        }

        sheet_order: list[str] = []
        sheets: dict[str, RawSheet] = {}
        sheet_nodes = workbook_xml.find("main:sheets", _NS)
        assert sheet_nodes is not None
        for sheet_node in sheet_nodes:
            title = sheet_node.attrib["name"]
            relationship_id = sheet_node.attrib[
                f"{{{_OFFICE_REL_NS}}}id"
            ]
            target = _relationship_target(relationships[relationship_id])
            sheet_xml = ET.fromstring(archive.read(target))
            rows: dict[int, dict[int, str]] = {}
            physical_rows: list[int] = []
            for row_node in sheet_xml.findall(
                ".//main:sheetData/main:row", _NS
            ):
                row_number = int(row_node.attrib["r"])
                row = rows.setdefault(row_number, {})
                for cell in row_node.findall("main:c", _NS):
                    column, reference_row = _column_number(cell.attrib["r"])
                    assert reference_row == row_number
                    row[column] = _cell_value(cell, shared_strings)
                # Empty row elements are another style artefact, not a
                # physical cell-bearing row in the evidence geometry.
                if row:
                    physical_rows.append(row_number)

            sheet_order.append(title)
            sheets[title] = RawSheet(
                title=title,
                physical_rows=tuple(physical_rows),
                rows=rows,
            )
    return RawWorkbook(tuple(sheet_order), sheets)


@dataclass(frozen=True)
class SheetEvidence:
    title: str
    physical_row_count: int
    last_physical_row: int
    logical_row_count: int
    question_row_count: int
    header_width: int
    occupied_column: int
    header_digest: str
    logical_matrix_digest: str


# The two digests deliberately cover different material.  ``header_digest``
# pins every raw position (including duplicate/newline headers), while the
# matrix digest visits every logical row x every one of those schema columns.
RAW_EVIDENCE: dict[str, tuple[SheetEvidence, ...]] = {
    "english_post_concept.xlsx": (
        SheetEvidence("Objective", 1000, 1000, 19, 0, 67, 67,
                      "e3ba619262cede3ed6615801c60143982fee0f9c798b7136ad9387af120e199f",
                      "990a578e29f4753121428a5f8cc8f89facf332683e66683891d84cb9bf748222"),
        SheetEvidence("Descriptive", 2, 2, 0, 0, 374, 374,
                      "60d0a2c362509c19bdc773f4c1de92af91b1a43cad62ed713e4e925cefe6c5e3",
                      "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"),
        SheetEvidence("Subjective", 2, 2, 0, 0, 144, 144,
                      "f2a72339a09e6edab390f72f3403787e9d9225426deaf66cc4b169100629012c",
                      "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"),
    ),
    "english_post_master.xlsx": (
        SheetEvidence("Objective", 1000, 1000, 10, 1, 72, 98,
                      "35a7ac29f2513f3c18db043f03ca598a127f3e5f5122beaaa94fbe83030209fb",
                      "8b8014ebc2713a096854ccb2d50d29c82a75e9754aff6bfb5b57119c1c1ecd17"),
        SheetEvidence("Subjective", 218, 218, 6, 6, 150, 170,
                      "eb94eace131cb3e0fdc13dd0693b6283c42968c3bfd0807472ce92ea02bc7341",
                      "7d5430d50b44650500c7e91a409e49131a467b1df31d5749d11dced598e1c3f7"),
        SheetEvidence("Descriptive", 220, 220, 13, 13, 442, 443,
                      "9d74a5e78c138fb53b496be255b5edc22aa7e474d3759a8ef174612aa74f0734",
                      "e3469d7b12e1d204fb5f335a8319073ef1c1307a5e96bc813f3e819fc66af009"),
    ),
    "english_pre_concept.xlsx": (
        SheetEvidence("Objective", 1000, 1000, 10, 0, 67, 67,
                      "e3ba619262cede3ed6615801c60143982fee0f9c798b7136ad9387af120e199f",
                      "1e22f05561c06b62cb9fde443d2d08ac0cbfa3d0af5f4d02e4feeac776729993"),
        SheetEvidence("Descriptive", 2, 2, 0, 0, 374, 374,
                      "60d0a2c362509c19bdc773f4c1de92af91b1a43cad62ed713e4e925cefe6c5e3",
                      "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"),
        SheetEvidence("Subjective", 2, 2, 0, 0, 144, 144,
                      "f2a72339a09e6edab390f72f3403787e9d9225426deaf66cc4b169100629012c",
                      "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"),
    ),
    "english_pre_master.xlsx": (
        SheetEvidence("Objective", 1001, 1001, 19, 5, 71, 71,
                      "8da3b9dbc2367b62dbc896378176df1f7fcafe885c3cfd29eb96f480c8b1b980",
                      "db9849f90d82fc23bf44661de49e52ea6d95f16051be808b114f2d660c88d45c"),
        SheetEvidence("Descriptive", 238, 238, 36, 36, 379, 379,
                      "753bc1b325b11eb47fdb95201ce8395ea05ccec0d2a7ba80520734ffadc59baa",
                      "aaf0ead6f59e2d0bfe98a0bf2ade8093851f029c5dee3fa3f649aeb21827cb46"),
        SheetEvidence("Subjective", 220, 220, 0, 0, 144, 144,
                      "f2a72339a09e6edab390f72f3403787e9d9225426deaf66cc4b169100629012c",
                      "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"),
    ),
    "math_post_concept.xlsx": (
        SheetEvidence("Objective", 993, 993, 18, 0, 67, 67,
                      "e3ba619262cede3ed6615801c60143982fee0f9c798b7136ad9387af120e199f",
                      "7c7dab8edda13507b45fb907d73a78cbd221dbdbab1693e9b2584d2b11856247"),
        SheetEvidence("Descriptive", 2, 2, 0, 0, 374, 374,
                      "60d0a2c362509c19bdc773f4c1de92af91b1a43cad62ed713e4e925cefe6c5e3",
                      "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"),
        SheetEvidence("Subjective", 2, 2, 0, 0, 144, 144,
                      "f2a72339a09e6edab390f72f3403787e9d9225426deaf66cc4b169100629012c",
                      "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"),
    ),
    "math_post_master.xlsx": (
        SheetEvidence("Descriptive", 1012, 1012, 35, 24, 380, 380,
                      "38e2a75c07b383079fefd9087155f708582b286d37245d8bbb119da03292dbe8",
                      "6a060b582606f1e62b1627b685c02fa268e8c40b51c82affac8d95a168dcad86"),
        SheetEvidence("Subjective", 1015, 1015, 4, 4, 149, 149,
                      "808a8695a14488e7597cef5d86e214a36862f254437ffa454431ccea4f898256",
                      "55a086815e486a30557e8a0f564bee9b140e9d159e82fd6c95ebcb01041b904b"),
        SheetEvidence("Objective", 1019, 1019, 34, 34, 72, 72,
                      "b5e876c7a1cce6282f75e98afaf7eca1235359f7c79a8f6eae1be7969198af54",
                      "fd8485714df2cfd53e4d9110409b9ea0fec573697c001c19e30d1005c6d0651a"),
    ),
    "math_pre_concept.xlsx": (
        SheetEvidence("Objective", 1000, 1000, 6, 0, 70, 70,
                      "19f9fc985346cdc2fc655a0ef217a67a5318a4c5ff5420a9d4d7e6a4c31bc743",
                      "bafef422dce8179f24b411bd63857cc2f34d4c16221362dadc588c7bcfeb84ef"),
        SheetEvidence("Descriptive", 2, 2, 0, 0, 374, 374,
                      "60d0a2c362509c19bdc773f4c1de92af91b1a43cad62ed713e4e925cefe6c5e3",
                      "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"),
        SheetEvidence("Subjective", 2, 2, 0, 0, 144, 144,
                      "f2a72339a09e6edab390f72f3403787e9d9225426deaf66cc4b169100629012c",
                      "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"),
    ),
    "math_pre_master.xlsx": (
        SheetEvidence("Objective", 1000, 1000, 15, 6, 72, 72,
                      "b5e876c7a1cce6282f75e98afaf7eca1235359f7c79a8f6eae1be7969198af54",
                      "b7f0f7324eb8533ada6acb0c57028f1fc83f7d482e1bd6f21700584d65200b07"),
        SheetEvidence("Descriptive", 1000, 1000, 21, 21, 379, 379,
                      "708e66c9ae603350fb28c7e5c2430aab1bba9fdc009e83d0a30152449940ed83",
                      "4ea0898ee502bd68bf4bb2cae39b1e938002d9a695f61f2e3152688cb883f8cd"),
        SheetEvidence("Subjective", 2, 2, 0, 0, 144, 144,
                      "f2a72339a09e6edab390f72f3403787e9d9225426deaf66cc4b169100629012c",
                      "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"),
    ),
}


_TEMPLATE = json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))
CANONICAL_SHEET_ORDER = tuple(_TEMPLATE["sheet_order"])
BASE_FIELDS = {
    sheet: tuple(payload["fields"])
    for sheet, payload in _TEMPLATE["sheets"].items()
}
UPDATE_FIELDS = (
    "is_update_chapter",
    "is_update_topic",
    "is_update_concept",
    "is_update_group",
    "is_update_question",
)
CHAPTER_FIELDS = (
    "chapter_title", "chapter_display_name", "chapter_duration",
    "pre_topics", "post_topics", "chapter_description",
)
TOPIC_FIELDS = (
    "topic_title", "topic_display_name", "pre_post_learning",
    "topic_concept_labels", "related_topics", "topic_description",
)
CONCEPT_FIELDS = (
    "concept_title", "concept_display_name", "concept_details", "keywords",
    "digicards", "related_concepts", "basic_groups",
    "intermediate_groups", "advanced_groups", "concept_source",
)
GROUP_FIELDS = (
    "group_name", "group_display_name", "group_description", "group_status",
    "group_type", "related_digicards",
)


def _insert_after(fields: list[str], anchor: str, value: str) -> None:
    fields.insert(fields.index(anchor) + 1, value)


def _expected_master_fields(sheet: str, answer_slots: int) -> list[str]:
    """Build the README contract without calling production output_schema."""

    fields = list(BASE_FIELDS[sheet])
    for value, anchor in (
        ("is_update_chapter", "chapter_title"),
        ("is_update_topic", "topic_title"),
        ("is_update_concept", "concept_title"),
        ("is_update_question", "question_label"),
    ):
        _insert_after(fields, anchor, value)
    _insert_after(
        fields,
        "group_display_name" if sheet == "Descriptive" else "group_name",
        "is_update_group",
    )
    if sheet == "Descriptive":
        _insert_after(fields, "concept_question_labels", "concept_source")
        insertion = fields.index("answer_explanation")
        fields[insertion:insertion] = [
            field
            for number in range(11, answer_slots + 1)
            for field in (
                f"answer_type_{number}",
                f"answer_weightage_{number}",
                f"answer_content_{number}",
            )
        ]
    return fields


def _expected_raw_headers(filename: str, sheet: str) -> list[str]:
    """Reproduce only the documented raw defects, position for position."""

    if "_concept" in filename:
        fields = list(BASE_FIELDS[sheet])
        if filename == "math_pre_concept.xlsx" and sheet == "Objective":
            for value, anchor in (
                ("is_update_chapter", "chapter_title"),
                ("is_update_topic", "topic_title"),
                ("is_update_concept", "concept_title"),
            ):
                _insert_after(fields, anchor, value)
        return fields

    answer_slots = 30 if filename == "english_post_master.xlsx" else 10
    fields = _expected_master_fields(sheet, answer_slots)
    if filename == "english_post_master.xlsx":
        fields[fields.index("is_update_topic")] = "is_update_topic\n"
        if sheet == "Subjective":
            fields[fields.index("is_update_chapter")] = (
                "is_update_chapter\n"
            )
        if sheet in {"Descriptive", "Subjective"}:
            _insert_after(
                fields, "chapter_display_name", "chapter_display_name"
            )
        if sheet == "Descriptive":
            _insert_after(fields, "sq15_keyword_6", "sq15_keyword_6")
    elif filename == "english_pre_master.xlsx":
        if sheet in {"Objective", "Descriptive"}:
            fields.remove("chapter_duration")
        else:
            fields = [field for field in fields if field not in UPDATE_FIELDS]
    elif filename == "math_pre_master.xlsx":
        if sheet == "Descriptive":
            fields.remove("concept_source")
        elif sheet == "Subjective":
            fields = [field for field in fields if field not in UPDATE_FIELDS]
    return fields


def _field_values(row: dict[str, str], fields: Iterable[str]) -> dict[str, str]:
    return {field: row.get(field, "") for field in fields}


def _normalize_chapter(chapter: dict[str, str], subject: str) -> dict[str, str]:
    normalized = dict(chapter)
    if subject == "Mathematics":
        normalized["chapter_duration"] = "362"
    return normalized


def _concept_snapshot(
    filename: str, subject: str,
) -> tuple[dict, list[dict[str, str]]]:
    raw_rows = _read_fixture(filename).sheets["Objective"].logical_records()
    assert raw_rows
    snapshot: dict = {
        "chapter": _normalize_chapter(
            _field_values(raw_rows[0], CHAPTER_FIELDS), subject
        ),
        "topics": [],
        "groups": [],
        "candidates": [],
    }
    topic_index: dict[str, dict] = {}
    seen_concepts: set[str] = set()
    for row in raw_rows:
        topic_title = row["topic_title"]
        topic = topic_index.get(topic_title)
        if topic is None:
            topic = {**_field_values(row, TOPIC_FIELDS), "concepts": []}
            topic_index[topic_title] = topic
            snapshot["topics"].append(topic)
        concept_title = row["concept_title"]
        if concept_title not in seen_concepts:
            seen_concepts.add(concept_title)
            topic["concepts"].append({
                **_field_values(row, CONCEPT_FIELDS),
                "concept_key": concept_title,
            })
    if filename in {
        "english_post_concept.xlsx", "english_pre_concept.xlsx",
    }:
        # The corrected workbook accidentally replaced the persisted,
        # source-aligned identities with one shared legacy ``Best_Help`` tag.
        # The paired original retains the valid persisted IDs.  Match on the
        # readable names and carry those IDs into the normalized snapshot;
        # rendering then replaces the bad carried tags rather than pinning
        # the collision as a golden output.
        original_rows = _read_fixture(
            filename.replace(".xlsx", "_original.xlsx")
        ).sheets["Objective"].logical_records()
        topic_ids = {
            row["topic_display_name"]: identity.title_tag(row["topic_title"])
            for row in original_rows
        }
        concept_ids = {
            (row["topic_display_name"], row["concept_display_name"]):
            identity.title_tag(row["concept_title"])
            for row in original_rows
        }
        assert len(set(topic_ids.values())) == len(topic_ids)
        for topic in snapshot["topics"]:
            display_name = topic["topic_display_name"]
            topic["topic_machine_id"] = topic_ids[display_name]
            for concept in topic["concepts"]:
                concept["concept_machine_id"] = concept_ids[
                    (display_name, concept["concept_display_name"])
                ]

    # Roster cells are derived summaries.  Rebuild them from the surviving
    # normalized concepts; the Math Post fixture carries a stale Topic-04
    # roster and is evidence of the defect, not the expected output.
    for topic in snapshot["topics"]:
        topic["topic_concept_labels"] = ", ".join(
            identity.titled(
                concept["concept_title"],
                concept.get("concept_machine_id", ""),
            )
            for concept in topic["concepts"]
        )
    lane_positions: Counter[str] = Counter()
    lane_titles: dict[str, list[str]] = {"PL": [], "PrL": []}
    for topic in snapshot["topics"]:
        lane = identity.lane_token(topic.get("pre_post_learning", ""))
        lane_positions[lane] += 1
        lane_titles[lane].append(identity.topic_title_cell(
            topic["topic_title"],
            topic.get("topic_machine_id", ""),
            lane_positions[lane],
        ))
    snapshot["chapter"]["post_topics"] = ", ".join(lane_titles["PL"])
    snapshot["chapter"]["pre_topics"] = ", ".join(lane_titles["PrL"])
    return snapshot, raw_rows


def _rendered_topic_title(snapshot: dict, selected: dict) -> str:
    lane_positions: Counter[str] = Counter()
    for topic in snapshot["topics"]:
        lane = identity.lane_token(topic.get("pre_post_learning", ""))
        lane_positions[lane] += 1
        if topic is selected:
            return identity.topic_title_cell(
                topic["topic_title"],
                topic.get("topic_machine_id", ""),
                lane_positions[lane],
            )
    raise AssertionError("topic is not part of the normalized snapshot")


def _rendered_concept_title(concept: dict) -> str:
    machine_id = concept.get("concept_machine_id", "")
    return (
        identity.titled(concept["concept_title"], machine_id)
        if machine_id else concept["concept_title"]
    )


def _without_duplicated_subparts(
    value: str, sub_questions: list[dict]
) -> str:
    text = str(value or "")
    for subquestion in sub_questions:
        part = str(subquestion.get("text") or "").strip()
        if not part:
            continue
        start = text.find(part)
        if start < 0:
            continue
        end = start + len(part)
        marks = str(subquestion.get("marks") or "").strip()
        if marks:
            suffix = re.match(
                rf"\s*\(\s*{re.escape(marks)}\s*\)", text[end:]
            )
            if suffix is not None:
                end += suffix.end()
        text = f"{text[:start]} {text[end:]}"
    return re.sub(r"\s+", " ", text).strip()


def _canonical_rubric(value: str) -> str:
    return re.sub(
        r"^\[(content|language|organisation|organization|presentation)\]"
        r"\s*:?[ \t]*",
        lambda match: f"[{match.group(1)}]: ",
        str(value or ""),
        flags=re.IGNORECASE,
    )


def _answers_from_audit_row(
    row: dict[str, str], sheet: str, *, answer_slots: int
) -> list[dict]:
    answers: list[dict] = []
    if sheet == "Objective":
        for number in range(1, 7):
            answer = {
                "answer_type": row.get(f"answer_type_{number}", ""),
                "answer_content": row.get(f"answer_content_{number}", ""),
                "correct_answer": row.get(f"correct_answer_{number}", ""),
                "answer_weightage": row.get(
                    f"answer_weightage_{number}", ""
                ),
            }
            if any(str(value) for value in answer.values()):
                answers.append(answer)
    elif sheet == "Subjective":
        for number in range(1, 21):
            answer = {
                "answer_type": row.get(f"answer_type_{number}", ""),
                "answer_content": row.get(f"answer_{number}", ""),
                "answer_display": row.get(f"answer_display_{number}", ""),
                "answer_weightage": row.get(f"weightage_{number}", ""),
                "placeholder": row.get(f"placeholder_{number}", ""),
            }
            if any(str(value) for value in answer.values()):
                answers.append(answer)
    else:
        for number in range(1, answer_slots + 1):
            answer = {
                "answer_type": row.get(f"answer_type_{number}", ""),
                "answer_weightage": row.get(
                    f"answer_weightage_{number}", ""
                ),
                "answer_content": _canonical_rubric(
                    row.get(f"answer_content_{number}", "")
                ),
            }
            if any(str(value) for value in answer.values()):
                answers.append(answer)
    return answers


def _subquestions_from_audit_row(row: dict[str, str]) -> list[dict]:
    subquestions: list[dict] = []
    for number in range(1, 16):
        keywords = []
        for keyword_number in range(1, 7):
            keyword = {
                "answer_type": row.get(
                    f"sq{number}_answer_type_{keyword_number}", ""
                ),
                "weightage": row.get(
                    f"sq{number}_weightage_{keyword_number}", ""
                ),
                "keyword": _canonical_rubric(
                    row.get(f"sq{number}_keyword_{keyword_number}", "")
                ),
            }
            if any(str(value) for value in keyword.values()):
                keywords.append(keyword)
        text = row.get(f"sub_question_{number}", "")
        marks = row.get(f"sub_question_marks_{number}", "")
        if text or marks or keywords:
            subquestions.append({
                "text": text, "marks": marks, "keywords": keywords,
            })
    return subquestions


_ARTICLE_LABELS = {
    f"06MSEN_SelfHelpIsth_PL_T05_C05 Q{number:02d}"
    for number in range(4, 10)
}


def _candidate_from_audit_row(
    row: dict[str, str], sheet: str, subject: str, answer_slots: int
) -> dict:
    subquestions = (
        _subquestions_from_audit_row(row) if sheet == "Descriptive" else []
    )
    question = row.get("question", "")
    question_text = row.get("question_text", "")
    if sheet == "Objective":
        # The fixture already contains rendered option lines.  The candidate
        # source is the authored stem; the renderer composes those lines.
        question_text = question
    elif subquestions:
        question = _without_duplicated_subparts(question, subquestions)
        question_text = _without_duplicated_subparts(
            question_text, subquestions
        )

    category = row.get("question_category", "")
    if subject == "Mathematics":
        category = MATH_CATEGORY_NORMALIZATION.get(category, category)
    restriction = row.get("answer_restriction", "")
    if restriction.casefold() in {"open", "specific"}:
        restriction = restriction.title()
    candidate = {
        "candidate_id": f"AUDIT-{row['question_label']}",
        "question_label": row["question_label"],
        "sheet_kind": sheet.casefold(),
        "question_category": category,
        "cognitive_skill": row.get("cognitive_skills", ""),
        "question_source": row.get("question_source", ""),
        "question_disclaimer": "",
        "question_duration": row.get("question_duration", ""),
        "question_appears_in": row.get("question_appears_in", ""),
        "answer_restriction": restriction,
        "difficulty": row.get("level_of_difficulty", ""),
        "question": question,
        "question_text": question_text,
        "marks": row.get("marks", ""),
        "math_keyboard": row.get("math_keyboard", ""),
        "display_answer": row.get("display_answer", ""),
        "answer_explanation": row.get("answer_explanation", ""),
        "answers": _answers_from_audit_row(
            row, sheet, answer_slots=answer_slots
        ),
        "sub_questions": subquestions,
        "concept_key": row.get("concept_title", ""),
        "group_key": row.get("group_name", ""),
        "flags": [],
    }
    if subject == "Mathematics":
        candidate["question_duration"] = str(_logged_duration(sheet, row))
    if row["question_label"] in _ARTICLE_LABELS:
        for answer in candidate["answers"]:
            answer["answer_content"] = str(
                answer.get("answer_content") or ""
            ).casefold()
            answer["answer_display"] = str(
                answer.get("answer_display") or ""
            ).casefold()
    return candidate


def _master_snapshot(
    master_filename: str,
    concept_filename: str,
    subject: str,
    answer_slots: int,
) -> tuple[dict, dict[str, tuple[str, dict[str, str], dict]]]:
    snapshot, _raw_concepts = _concept_snapshot(concept_filename, subject)
    topics = {topic["topic_title"]: topic for topic in snapshot["topics"]}
    concepts = {
        concept["concept_title"]: concept
        for topic in snapshot["topics"]
        for concept in topic["concepts"]
    }
    groups: dict[str, dict] = {}
    normalized_group_keys: dict[str, str] = {}
    candidates: dict[str, tuple[str, dict[str, str], dict]] = {}
    question_row_count = 0
    raw_question_labels: set[str] = set()
    raw = _read_fixture(master_filename)
    for sheet in raw.sheet_order:
        for row in raw.sheets[sheet].logical_records():
            topic_title = row.get("topic_title", "")
            topic = topics.get(topic_title)
            if topic is None:
                topic = {**_field_values(row, TOPIC_FIELDS), "concepts": []}
                topics[topic_title] = topic
                snapshot["topics"].append(topic)
            concept_title = row.get("concept_title", "")
            if concept_title and concept_title not in concepts:
                concept = {
                    **_field_values(row, CONCEPT_FIELDS),
                    "concept_key": concept_title,
                }
                concepts[concept_title] = concept
                topic["concepts"].append(concept)
            group_name = row.get("group_name", "")
            if group_name and group_name not in normalized_group_keys:
                normalized_group_key = group_name
                if subject == "English":
                    concept_machine_id = str(
                        concepts[concept_title].get("concept_machine_id")
                        or identity.title_tag(
                            concepts[concept_title]["concept_title"]
                        )
                    )
                    suffix = re.search(
                        r"\)\s+(BG|IG|AG)([0-9]+)$", group_name
                    )
                    assert concept_machine_id and suffix is not None
                    normalized_group_key = (
                        f"({concept_machine_id}) {suffix.group(1)}"
                        f"{int(suffix.group(2)):02d}"
                    )
                assert normalized_group_key not in groups
                normalized_group_keys[group_name] = normalized_group_key
                group = _field_values(row, GROUP_FIELDS)
                groups[normalized_group_key] = {
                    "group_key": normalized_group_key,
                    "concept_key": concept_title,
                    "group_name": normalized_group_key,
                    "group_display_name": normalized_group_key,
                    "semantic_description": group["group_description"],
                    "group_status": group["group_status"],
                    "group_type": group["group_type"],
                    "related_digicards": group["related_digicards"],
                }
            if row.get("question_label"):
                question_row_count += 1
                raw_label = row["question_label"]
                assert raw_label not in raw_question_labels
                raw_question_labels.add(raw_label)
                candidate = _candidate_from_audit_row(
                    row, sheet, subject, answer_slots
                )
                candidate["group_key"] = normalized_group_keys[group_name]
                concept_machine_id = str(
                    concepts[concept_title].get("concept_machine_id")
                    or identity.title_tag(
                        concepts[concept_title]["concept_title"]
                    )
                )
                assert concept_machine_id
                ordinal_match = re.search(
                    r"(?:\s+|_)Q([0-9]+)$", raw_label
                )
                assert ordinal_match is not None
                normalized_label = (
                    f"{concept_machine_id} "
                    f"Q{int(ordinal_match.group(1)):02d}"
                )
                candidate["question_label"] = normalized_label
                candidate["candidate_id"] = f"AUDIT-{normalized_label}"
                normalized_label = candidate["question_label"]
                assert normalized_label not in candidates
                candidates[normalized_label] = (sheet, row, candidate)
    assert len(candidates) == question_row_count
    snapshot["groups"] = list(groups.values())
    snapshot["candidates"] = [
        candidate for _sheet, _row, candidate in candidates.values()
    ]
    return snapshot, candidates


def _normalized_cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float, Decimal)):
        number = Decimal(str(value))
        if number == number.to_integral():
            return str(int(number))
    return str(value)


def _expected_question_record(
    candidate: dict, sheet: str, *, answer_slots: int
) -> dict[str, str]:
    record: dict[str, object] = {
        "question_label": candidate["question_label"],
        "is_update_question": "No",
        "question_category": candidate["question_category"],
        "cognitive_skills": candidate["cognitive_skill"],
        "question_source": candidate["question_source"],
        "question_disclaimer": "",
        "question_duration": candidate["question_duration"],
        "question_appears_in": candidate["question_appears_in"],
        "answer_restriction": candidate["answer_restriction"],
        "level_of_difficulty": candidate["difficulty"],
        "question": candidate["question"],
        "question_text": candidate["question_text"],
        "marks": candidate["marks"],
        "answer_explanation": candidate["answer_explanation"],
    }
    answers = candidate["answers"]
    if sheet == "Objective":
        option_lines = []
        for number, answer in enumerate(answers, start=1):
            record[f"answer_type_{number}"] = answer["answer_type"]
            record[f"answer_content_{number}"] = katex_rules.raw_answer_cell(
                answer["answer_type"], answer["answer_content"]
            )
            raw_correct = str(answer["correct_answer"]).strip().casefold()
            record[f"correct_answer_{number}"] = (
                "Yes" if raw_correct in {"yes", "1", "true"} else "No"
            ) if raw_correct else ""
            record[f"answer_weightage_{number}"] = answer[
                "answer_weightage"
            ]
            if str(answer["answer_content"]).strip():
                option_lines.append(
                    f"{chr(ord('a') + len(option_lines))}) "
                    + katex_rules.rich_answer_display(
                        answer["answer_type"], answer["answer_content"]
                    )
                )
        if option_lines:
            record["question_text"] = (
                str(record["question_text"]).rstrip()
                + "\n" + "\n".join(option_lines)
            ).strip()
    elif sheet == "Subjective":
        record["math_keyboard"] = candidate["math_keyboard"]
        for number, answer in enumerate(answers, start=1):
            record[f"answer_type_{number}"] = answer["answer_type"]
            record[f"answer_{number}"] = katex_rules.raw_answer_cell(
                answer["answer_type"], answer["answer_content"]
            )
            record[f"answer_display_{number}"] = (
                answer["answer_display"]
                or katex_rules.rich_answer_display(
                    answer["answer_type"], answer["answer_content"]
                )
            )
            record[f"weightage_{number}"] = answer["answer_weightage"]
            record[f"placeholder_{number}"] = answer["placeholder"]
    else:
        record["math_keyboard"] = candidate["math_keyboard"]
        record["display_answer"] = candidate["display_answer"]
        for number, answer in enumerate(answers[:answer_slots], start=1):
            record[f"answer_type_{number}"] = answer["answer_type"]
            record[f"answer_weightage_{number}"] = answer[
                "answer_weightage"
            ]
            record[f"answer_content_{number}"] = katex_rules.raw_answer_cell(
                answer["answer_type"], answer["answer_content"]
            )
        for number, subquestion in enumerate(
            candidate["sub_questions"], start=1
        ):
            record[f"sub_question_{number}"] = subquestion["text"]
            record[f"sub_question_marks_{number}"] = subquestion["marks"]
            for keyword_number, keyword in enumerate(
                subquestion["keywords"], start=1
            ):
                record[f"sq{number}_answer_type_{keyword_number}"] = (
                    keyword["answer_type"]
                )
                record[f"sq{number}_weightage_{keyword_number}"] = (
                    keyword["weightage"]
                )
                record[f"sq{number}_keyword_{keyword_number}"] = (
                    katex_rules.raw_answer_cell(
                        keyword["answer_type"], keyword["keyword"]
                    )
                )
    for field in (
        "question", "question_text", "display_answer", "answer_explanation",
    ):
        if field in record:
            record[field] = katex_rules.replace_unsupported_tables(
                str(record[field])
            )
    return {field: _normalized_cell(value) for field, value in record.items()}


def _digest(values: object) -> str:
    encoded = json.dumps(
        values, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@pytest.mark.parametrize("filename", sorted(FILE_HASHES))
def test_all_sixteen_fixture_bytes_are_pinned(filename: str) -> None:
    path = FIXTURE_DIR / filename
    assert hashlib.sha256(path.read_bytes()).hexdigest() == FILE_HASHES[filename]


@pytest.mark.parametrize("filename", sorted(RAW_EVIDENCE))
def test_every_corrected_logical_schema_cell_is_pinned(filename: str) -> None:
    raw = _read_fixture(filename)
    expected_sheets = RAW_EVIDENCE[filename]
    assert raw.sheet_order == tuple(item.title for item in expected_sheets)
    for expected in expected_sheets:
        sheet = raw.sheets[expected.title]
        assert len(sheet.physical_rows) == expected.physical_row_count
        assert max(sheet.physical_rows) == expected.last_physical_row
        assert len(sheet.logical_rows) == expected.logical_row_count
        assert sheet.header_width == expected.header_width
        assert sheet.occupied_column == expected.occupied_column
        assert _digest(sheet.headers) == expected.header_digest

        question_column = sheet.first_column("question_label")
        assert sum(
            sheet.rows[row].get(question_column, "") != ""
            for row in sheet.logical_rows
        ) == expected.question_row_count

        # This is the exhaustive traversal: every cell, including blanks, in
        # every logical row across the complete raw schema width is visited.
        matrix = [
            [
                sheet.rows[row].get(column, "")
                for column in range(1, sheet.header_width + 1)
            ]
            for row in sheet.logical_rows
        ]
        assert sum(map(len, matrix)) == (
            expected.logical_row_count * expected.header_width
        )
        assert _digest(matrix) == expected.logical_matrix_digest
        assert not [
            (row, column, value)
            for row in sheet.logical_rows
            for column, value in sheet.rows[row].items()
            if column > sheet.header_width and value != ""
        ]


ORIGINAL_CORRECTED_PAIRS = {
    filename: filename.replace(".xlsx", "_original.xlsx")
    for filename in RAW_EVIDENCE
}


def _pair_comparison(corrected_filename: str) -> dict[str, object]:
    """Compare all values over the union logical rectangle of each sheet."""

    original_filename = ORIGINAL_CORRECTED_PAIRS[corrected_filename]
    original = _read_fixture(original_filename)
    corrected = _read_fixture(corrected_filename)
    assert set(original.sheets) == set(corrected.sheets)

    sheet_order = tuple(dict.fromkeys(
        (*CANONICAL_SHEET_ORDER, *original.sheet_order, *corrected.sheet_order)
    ))
    digest = hashlib.sha256()
    totals = Counter()
    by_sheet: dict[str, tuple[int, int, int, int, int, int, int]] = {}
    for title in sheet_order:
        original_sheet = original.sheets[title]
        corrected_sheet = corrected.sheets[title]
        row_numbers = sorted({
            1,
            2,
            *original_sheet.logical_rows,
            *corrected_sheet.logical_rows,
        })
        column_numbers = sorted({
            column
            for sheet in (original_sheet, corrected_sheet)
            for row_number in row_numbers
            for column, value in sheet.rows.get(row_number, {}).items()
            if value != ""
        })
        assert column_numbers == list(range(1, max(column_numbers) + 1))

        counts = Counter()
        for row_number in row_numbers:
            for column in column_numbers:
                original_value = original_sheet.rows.get(
                    row_number, {}
                ).get(column, "")
                corrected_value = corrected_sheet.rows.get(
                    row_number, {}
                ).get(column, "")
                digest.update(json.dumps(
                    [title, row_number, column, original_value, corrected_value],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8"))
                digest.update(b"\n")
                counts["compared"] += 1
                if original_value == corrected_value:
                    counts["equal"] += 1
                elif original_value == "":
                    counts["added"] += 1
                elif corrected_value == "":
                    counts["removed"] += 1
                else:
                    counts["replaced"] += 1

        assert counts["compared"] == len(row_numbers) * len(column_numbers)
        assert counts["compared"] == sum(
            counts[key] for key in ("equal", "added", "removed", "replaced")
        )
        by_sheet[title] = (
            len(row_numbers),
            len(column_numbers),
            counts["compared"],
            counts["equal"],
            counts["added"],
            counts["removed"],
            counts["replaced"],
        )
        totals.update(counts)

    return {
        "digest": digest.hexdigest(),
        "compared": totals["compared"],
        "equal": totals["equal"],
        "added": totals["added"],
        "removed": totals["removed"],
        "replaced": totals["replaced"],
        "sheets": by_sheet,
    }


# Filled from the cell-by-cell comparison above.  It pins the comparison,
# not merely a count of differences, so a value change cannot be exchanged
# for another change while keeping the totals constant.
PAIR_EVIDENCE: dict[str, dict[str, object]] = {
    "english_post_concept.xlsx": {
        "digest": "5892de3c851f4f0e569aa933e7e22c0b57a6e8b7f460869fb8ac440a3d869922",
        "compared": 2443, "equal": 2310, "added": 0,
        "removed": 19, "replaced": 114,
        "sheets": {
            "Objective": (21, 67, 1407, 1274, 0, 19, 114),
            "Descriptive": (2, 374, 748, 748, 0, 0, 0),
            "Subjective": (2, 144, 288, 288, 0, 0, 0),
        },
    },
    "english_post_master.xlsx": {
        "digest": "f9633252f4ca1d44dbe4423354525f26feeaeb3a1d079124aaedbd0524075507",
        "compared": 10864, "equal": 8232, "added": 654,
        "removed": 850, "replaced": 1128,
        "sheets": {
            "Objective": (36, 72, 2592, 1635, 67, 678, 212),
            "Descriptive": (16, 442, 7072, 5824, 307, 168, 773),
            "Subjective": (8, 150, 1200, 773, 280, 4, 143),
        },
    },
    "english_pre_concept.xlsx": {
        "digest": "4e488bc572aa8d4b43a300a80b257a7f1b61fd7724df994ceb67f3166ce04537",
        "compared": 1840, "equal": 1760, "added": 0,
        "removed": 20, "replaced": 60,
        "sheets": {
            "Objective": (12, 67, 804, 724, 0, 20, 60),
            "Descriptive": (2, 374, 748, 748, 0, 0, 0),
            "Subjective": (2, 144, 288, 288, 0, 0, 0),
        },
    },
    "english_pre_master.xlsx": {
        "digest": "6b1d812294352318dd8febe3e5a5a2dd2a7f09547b931053e8327580fdf1bd5f",
        "compared": 16939, "equal": 13903, "added": 567,
        "removed": 422, "replaced": 2047,
        "sheets": {
            "Objective": (21, 71, 1491, 820, 118, 103, 450),
            "Descriptive": (40, 379, 15160, 12795, 449, 319, 1597),
            "Subjective": (2, 144, 288, 288, 0, 0, 0),
        },
    },
    "math_post_concept.xlsx": {
        "digest": "cb82c6a70735a7a94f3047b85e951a0ff85d0447abcc59a462ac40e6f31c2f51",
        "compared": 2856, "equal": 2336, "added": 57,
        "removed": 200, "replaced": 263,
        "sheets": {
            "Objective": (26, 70, 1820, 1300, 57, 200, 263),
            "Descriptive": (2, 374, 748, 748, 0, 0, 0),
            "Subjective": (2, 144, 288, 288, 0, 0, 0),
        },
    },
    "math_post_master.xlsx": {
        "digest": "7c4da7000731053945180d77fe2dc8722dd929cb49951e742041a14f9af520b7",
        "compared": 22430, "equal": 16027, "added": 1266,
        "removed": 2367, "replaced": 2770,
        "sheets": {
            "Objective": (88, 72, 6336, 2821, 520, 1630, 1365),
            "Descriptive": (40, 380, 15200, 12681, 524, 733, 1262),
            "Subjective": (6, 149, 894, 525, 222, 4, 143),
        },
    },
    "math_pre_concept.xlsx": {
        "digest": "c2902b07126ab57067237f93d68ec72a657ae9afc01df5b141e5665b24dceb3a",
        "compared": 1596, "equal": 1393, "added": 43,
        "removed": 22, "replaced": 138,
        "sheets": {
            "Objective": (8, 70, 560, 357, 43, 22, 138),
            "Descriptive": (2, 374, 748, 748, 0, 0, 0),
            "Subjective": (2, 144, 288, 288, 0, 0, 0),
        },
    },
    "math_pre_master.xlsx": {
        "digest": "276789f58765a91568a9c6da77fbcd422e19038bae2efcfb8644770e3035879a",
        "compared": 10229, "equal": 8020, "added": 456,
        "removed": 198, "replaced": 1555,
        "sheets": {
            "Objective": (17, 72, 1224, 517, 168, 62, 477),
            "Descriptive": (23, 379, 8717, 7215, 288, 136, 1078),
            "Subjective": (2, 144, 288, 288, 0, 0, 0),
        },
    },
}


@pytest.mark.parametrize("corrected_filename", sorted(ORIGINAL_CORRECTED_PAIRS))
def test_every_original_corrected_union_cell_is_compared(
    corrected_filename: str,
) -> None:
    observed = _pair_comparison(corrected_filename)
    assert observed == PAIR_EVIDENCE[corrected_filename]
    assert observed["compared"] == (
        observed["equal"]
        + observed["added"]
        + observed["removed"]
        + observed["replaced"]
    )


def test_raw_headers_match_documented_defects_position_for_position() -> None:
    for filename, sheet_evidence in RAW_EVIDENCE.items():
        raw = _read_fixture(filename)
        for evidence in sheet_evidence:
            headers = list(raw.sheets[evidence.title].headers)
            assert headers == _expected_raw_headers(filename, evidence.title), (
                filename,
                evidence.title,
            )

    english_post = _read_fixture("english_post_master.xlsx")
    assert {
        sheet: {
            field: count
            for field, count in Counter(
                header.strip() for header in raw_sheet.headers
            ).items()
            if field and count > 1
        }
        for sheet, raw_sheet in english_post.sheets.items()
    } == {
        "Objective": {},
        "Subjective": {"chapter_display_name": 2},
        "Descriptive": {
            "chapter_display_name": 2,
            "sq15_keyword_6": 2,
        },
    }
    assert {
        sheet: tuple(header for header in raw_sheet.headers if "\n" in header)
        for sheet, raw_sheet in english_post.sheets.items()
    } == {
        "Objective": ("is_update_topic\n",),
        "Subjective": ("is_update_chapter\n", "is_update_topic\n"),
        "Descriptive": ("is_update_topic\n",),
    }

    # Styled tails are not schema.  No other fixture has columns beyond its
    # last nonblank header, and the exhaustive matrix test proves these tails
    # carry no logical data.
    assert {
        (filename, evidence.title): (
            _read_fixture(filename).sheets[evidence.title].occupied_column
            - evidence.header_width
        )
        for filename, sheets in RAW_EVIDENCE.items()
        for evidence in sheets
        if _read_fixture(filename).sheets[evidence.title].occupied_column
        > evidence.header_width
    } == {
        ("english_post_master.xlsx", "Objective"): 26,
        ("english_post_master.xlsx", "Subjective"): 20,
        ("english_post_master.xlsx", "Descriptive"): 1,
    }


MASTER_CONTRACTS = (
    (
        "english_post_master.xlsx",
        "English",
        "Post",
        30,
        "msbshse-grade-6-english-post-master-2026-08-27",
    ),
    (
        "english_pre_master.xlsx",
        "English",
        "Pre",
        10,
        "msbshse-grade-6-master-2026-08-27",
    ),
    (
        "math_post_master.xlsx",
        "Mathematics",
        "Post",
        10,
        "msbshse-grade-6-master-2026-08-27",
    ),
    (
        "math_pre_master.xlsx",
        "Mathematics",
        "Pre",
        10,
        "msbshse-grade-6-master-2026-08-27",
    ),
)


def _profile(subject: str) -> dict:
    return assessment_profile.resolve_for_metadata(
        None,
        {"board": "MSBSHSE", "grade": "06", "subject": subject},
    )


def _lane_snapshot(phase: str) -> dict:
    return {"topics": [{"pre_post_learning": phase}]}


@pytest.mark.parametrize(
    "_filename,subject,phase,answer_slots,contract_id", MASTER_CONTRACTS
)
def test_production_master_contract_normalizes_raw_schema_defects(
    _filename: str,
    subject: str,
    phase: str,
    answer_slots: int,
    contract_id: str,
) -> None:
    schema = workbook.output_schema(
        "master", _profile(subject), _lane_snapshot(phase)
    )
    assert tuple(workbook.SHEET_ORDER) == CANONICAL_SHEET_ORDER
    assert schema["contract_id"] == contract_id
    assert schema["descriptive_answer_slots"] == answer_slots
    assert schema["natural_label_aggregates"] is True
    assert schema["aggregate_rendered_questions_only"] is True

    for sheet in CANONICAL_SHEET_ORDER:
        expected = _expected_master_fields(sheet, answer_slots)
        assert schema["fields"][sheet] == expected
        assert len(expected) == len(set(expected))
        assert [
            expected.index(field) + 1 for field in UPDATE_FIELDS
        ] == [2, 9, 16, 28, 36]
        assert expected.index("concept_source") + 1 == 26

    assert [
        len(schema["fields"][sheet]) for sheet in CANONICAL_SHEET_ORDER
    ] == [72, 440 if answer_slots == 30 else 380, 149]


@pytest.mark.parametrize(
    "filename,subject,phase",
    (
        ("english_post_concept.xlsx", "English", "Post"),
        ("english_pre_concept.xlsx", "English", "Pre"),
        ("math_post_concept.xlsx", "Mathematics", "Post"),
        ("math_pre_concept.xlsx", "Mathematics", "Pre"),
    ),
)
def test_production_concept_contract_rejects_raw_update_columns(
    filename: str, subject: str, phase: str
) -> None:
    schema = workbook.output_schema(
        "concept", _profile(subject), _lane_snapshot(phase)
    )
    assert schema["contract_id"] == "concept-reference-1"
    assert {
        sheet: schema["fields"][sheet] for sheet in CANONICAL_SHEET_ORDER
    } == {
        sheet: list(BASE_FIELDS[sheet]) for sheet in CANONICAL_SHEET_ORDER
    }
    assert [
        len(schema["fields"][sheet]) for sheet in CANONICAL_SHEET_ORDER
    ] == [67, 374, 144]

    raw_objective = _read_fixture(filename).sheets["Objective"]
    if filename == "math_pre_concept.xlsx":
        assert [
            (field, raw_objective.headers.index(field) + 1)
            for field in UPDATE_FIELDS[:3]
        ] == [
            ("is_update_chapter", 2),
            ("is_update_topic", 9),
            ("is_update_concept", 16),
        ]
        assert all(
            field not in schema["fields"]["Objective"]
            for field in UPDATE_FIELDS[:3]
        )


CONCEPT_ROLES = (
    ("english_post_concept.xlsx", "English"),
    ("english_pre_concept.xlsx", "English"),
    ("math_post_concept.xlsx", "Mathematics"),
    ("math_pre_concept.xlsx", "Mathematics"),
)


@pytest.mark.parametrize("filename,subject", CONCEPT_ROLES)
def test_normalized_concept_rows_render_field_for_field(
    filename: str, subject: str
) -> None:
    snapshot, raw_rows = _concept_snapshot(filename, subject)
    rendered = workbook.parse_workbook(
        workbook.render_concept_file(snapshot, _profile(subject))
    )
    rows = rendered["sheets"]["Objective"]["rows"]
    assert len(rows) == len(raw_rows)
    assert rendered["sheets"]["Descriptive"]["rows"] == []
    assert rendered["sheets"]["Subjective"]["rows"] == []

    for row_number, (raw_row, rendered_row) in enumerate(
        zip(raw_rows, rows, strict=True), start=3
    ):
        expected = dict(raw_row)
        expected.update(snapshot["chapter"])
        topic = next(
            topic for topic in snapshot["topics"]
            if topic["topic_title"] == raw_row["topic_title"]
        )
        concept = next(
            concept for concept in topic["concepts"]
            if concept["concept_key"] == raw_row["concept_title"]
        )
        expected["topic_title"] = _rendered_topic_title(snapshot, topic)
        expected["concept_title"] = _rendered_concept_title(concept)
        expected["topic_concept_labels"] = topic["topic_concept_labels"]
        for field in (
            "basic_groups", "intermediate_groups", "advanced_groups",
            "concept_question_labels",
        ):
            expected[field] = ""
        assert {
            field: _normalized_cell(rendered_row.get(field, ""))
            for field in BASE_FIELDS["Objective"]
        } == {
            field: _normalized_cell(expected.get(field, ""))
            for field in BASE_FIELDS["Objective"]
        }, (filename, row_number)


@pytest.mark.parametrize(
    "master_filename,subject,phase,answer_slots,_contract_id",
    MASTER_CONTRACTS,
)
def test_normalized_master_questions_and_hierarchy_render_field_for_field(
    master_filename: str,
    subject: str,
    phase: str,
    answer_slots: int,
    _contract_id: str,
) -> None:
    concept_filename = master_filename.replace("_master", "_concept")
    snapshot, candidates = _master_snapshot(
        master_filename, concept_filename, subject, answer_slots
    )
    profile = _profile(subject)
    data, _issues = workbook.render_master_file(snapshot, profile)
    rendered = workbook.parse_workbook(data)
    schema = workbook.output_schema("master", profile, _lane_snapshot(phase))

    rendered_by_label: dict[str, tuple[str, dict]] = {}
    for sheet in CANONICAL_SHEET_ORDER:
        for row in rendered["sheets"][sheet]["rows"]:
            label = str(row.get("question_label") or "")
            if label:
                assert label not in rendered_by_label
                rendered_by_label[label] = (sheet, row)
    assert set(rendered_by_label) == set(candidates)

    concept_home = {
        concept["concept_key"]: (topic, concept)
        for topic in snapshot["topics"]
        for concept in topic["concepts"]
    }
    group_home = {
        group["group_key"]: group for group in snapshot["groups"]
    }
    for label, (raw_sheet, _raw_row, candidate) in candidates.items():
        rendered_sheet, rendered_row = rendered_by_label[label]
        assert rendered_sheet == raw_sheet
        rendered_concept_id = identity.title_tag(
            str(rendered_row["concept_title"])
        )
        assert rendered_concept_id
        assert re.sub(r"\s+Q[0-9]+$", "", label) == rendered_concept_id

        question_fields = schema["fields"][raw_sheet]
        question_fields = question_fields[
            question_fields.index("question_label"):
        ]
        expected_question = _expected_question_record(
            candidate, raw_sheet, answer_slots=answer_slots
        )
        assert {
            field: _normalized_cell(rendered_row.get(field, ""))
            for field in question_fields
        } == {
            field: expected_question.get(field, "")
            for field in question_fields
        }, (master_filename, raw_sheet, label)

        topic, concept = concept_home[candidate["concept_key"]]
        group = group_home[candidate["group_key"]]
        expected_hierarchy = {
            **snapshot["chapter"],
            "is_update_chapter": "No",
            "topic_title": _rendered_topic_title(snapshot, topic),
            "topic_display_name": topic["topic_display_name"],
            "pre_post_learning": topic["pre_post_learning"],
            "related_topics": topic["related_topics"],
            "topic_description": topic["topic_description"],
            "is_update_topic": "No",
            "concept_title": _rendered_concept_title(concept),
            "concept_display_name": concept["concept_display_name"],
            "concept_details": concept["concept_details"],
            "keywords": concept["keywords"],
            "digicards": concept["digicards"],
            "related_concepts": concept["related_concepts"],
            "concept_source": concept["concept_source"],
            "is_update_concept": "No",
            "group_name": group["group_name"],
            "group_display_name": group["group_display_name"],
            "group_description": group["semantic_description"],
            "group_status": group["group_status"],
            "group_type": group["group_type"],
            "related_digicards": "",
            "is_update_group": "No",
        }
        comparable = [
            field for field in expected_hierarchy
            if field in schema["fields"][raw_sheet]
        ]
        assert {
            field: _normalized_cell(rendered_row.get(field, ""))
            for field in comparable
        } == {
            field: _normalized_cell(expected_hierarchy[field])
            for field in comparable
        }, (master_filename, raw_sheet, label, "hierarchy")


def _audit_natural_key(value: str) -> tuple:
    return tuple(
        (0, int(token)) if token.isdigit() else (1, token.casefold())
        for token in re.split(r"([0-9]+)", str(value))
    )


@pytest.mark.parametrize(
    "master_filename,subject,phase,answer_slots,_contract_id",
    MASTER_CONTRACTS,
)
def test_normalized_aggregates_and_tails_are_derived_from_survivors(
    master_filename: str,
    subject: str,
    phase: str,
    answer_slots: int,
    _contract_id: str,
) -> None:
    concept_filename = master_filename.replace("_master", "_concept")
    snapshot, _candidates = _master_snapshot(
        master_filename, concept_filename, subject, answer_slots
    )
    profile = _profile(subject)
    data, _issues = workbook.render_master_file(snapshot, profile)
    parsed = workbook.parse_workbook(data)
    schema = workbook.output_schema("master", profile, _lane_snapshot(phase))

    questions_by_concept: dict[str, list[str]] = {}
    questions_by_group: dict[str, list[str]] = {}
    for candidate in snapshot["candidates"]:
        questions_by_concept.setdefault(
            candidate["concept_key"], []
        ).append(candidate["question_label"])
        questions_by_group.setdefault(
            candidate["group_key"], []
        ).append(candidate["question_label"])
    for labels in (*questions_by_concept.values(), *questions_by_group.values()):
        labels.sort(key=_audit_natural_key)

    rollup_field = {
        "Basic": "basic_groups",
        "Intermediate": "intermediate_groups",
        "Advanced": "advanced_groups",
    }
    rollups: dict[str, dict[str, list[str]]] = {}
    groups_by_key = {group["group_key"]: group for group in snapshot["groups"]}
    for group in snapshot["groups"]:
        if not questions_by_group.get(group["group_key"]):
            continue
        field = rollup_field[group["group_type"]]
        rollups.setdefault(group["concept_key"], {}).setdefault(
            field, []
        ).append(group["group_name"])

    rendered_by_concept: dict[str, list[tuple[str, dict]]] = {}
    for sheet in CANONICAL_SHEET_ORDER:
        for row in parsed["sheets"][sheet]["rows"]:
            rendered_by_concept.setdefault(
                str(row["concept_title"]), []
            ).append((sheet, row))

    concepts = [
        concept
        for topic in snapshot["topics"]
        for concept in topic["concepts"]
    ]
    assert set(rendered_by_concept) == {
        _rendered_concept_title(concept) for concept in concepts
    }
    for concept in concepts:
        concept_key = concept["concept_key"]
        rows = rendered_by_concept[_rendered_concept_title(concept)]
        expected_labels = questions_by_concept.get(concept_key, [])
        expected_concept_aggregate = ", ".join(expected_labels)
        expected_rollups = {
            field: ", ".join(rollups.get(concept_key, {}).get(field, []))
            for field in rollup_field.values()
        }

        if not expected_labels:
            assert len(rows) == 1
            sheet, row = rows[0]
            assert sheet == "Objective"
            assert row["concept_question_labels"] == ""
            assert {
                field: _normalized_cell(row.get(field, ""))
                for field in expected_rollups
            } == {field: "" for field in expected_rollups}
            assert {
                field: _normalized_cell(row.get(field, ""))
                for field in (*GROUP_FIELDS, "is_update_group")
            } == {
                field: "" for field in (*GROUP_FIELDS, "is_update_group")
            }
            question_fields = schema["fields"]["Objective"]
            question_fields = question_fields[
                question_fields.index("question_label"):
            ]
            assert all(
                _normalized_cell(row.get(field, "")) == ""
                for field in question_fields
            )
            assert [
                row[field]
                for field in (
                    "is_update_chapter", "is_update_topic",
                    "is_update_concept",
                )
            ] == ["No", "No", "No"]
            assert _normalized_cell(row["concept_source"]) == (
                _normalized_cell(concept["concept_source"])
            )
            continue

        assert len(rows) == len(expected_labels)
        assert sorted(
            str(row["question_label"])
            for _sheet, row in rows
        ) == sorted(expected_labels)
        for _sheet, row in rows:
            assert row["concept_question_labels"] == (
                expected_concept_aggregate
            )
            assert {
                field: row[field] for field in expected_rollups
            } == expected_rollups
            group = groups_by_key[str(row["group_name"])]
            concept_id = identity.title_tag(str(row["concept_title"]))
            assert row["group_name"].startswith(f"({concept_id}) ")
            assert row["group_display_name"] == row["group_name"]
            assert row["group_question_labels"] == ", ".join(
                questions_by_group[group["group_key"]]
            )
            assert row["question_label"].startswith(
                f"{identity.title_tag(str(row['concept_title']))} Q"
            )


def _full_rendered_master_evidence(
    master_filename: str,
    subject: str,
    phase: str,
    answer_slots: int,
) -> dict[str, object]:
    concept_filename = master_filename.replace("_master", "_concept")
    snapshot, _candidates = _master_snapshot(
        master_filename, concept_filename, subject, answer_slots
    )
    profile = _profile(subject)
    data, _issues = workbook.render_master_file(snapshot, profile)
    parsed = workbook.parse_workbook(data)
    schema = workbook.output_schema("master", profile, _lane_snapshot(phase))

    combined: list[object] = []
    sheets: dict[str, tuple[int, int, int, str]] = {}
    for sheet in CANONICAL_SHEET_ORDER:
        fields = schema["fields"][sheet]
        rows = parsed["sheets"][sheet]["rows"]
        matrix = [
            [
                _normalized_cell(row.get(field, ""))
                for field in fields
            ]
            for row in rows
        ]
        cell_count = len(rows) * len(fields)
        assert sum(map(len, matrix)) == cell_count
        matrix_digest = _digest(matrix)
        sheets[sheet] = (
            len(rows), len(fields), cell_count, matrix_digest,
        )
        combined.append([sheet, fields, matrix])
    return {"digest": _digest(combined), "sheets": sheets}


FULL_RENDERED_MASTER_EVIDENCE: dict[str, dict[str, object]] = {
    "english_post_master.xlsx": {
        "digest": "8d80a35ff5fffa1841093f99b8d2954fac66cba941a6912a9f9e1737e27da727",
        "sheets": {
            "Objective": (
                10, 72, 720,
                "1335550f1cb0036f7e2a6ad8f48620fdb019df5c13dd4bdba3e57a4cc50c7d0d",
            ),
            "Descriptive": (
                13, 440, 5720,
                "707f2490da7876b621c25df53425b181efedaf052fd8f5a58ad625b988b9148c",
            ),
            "Subjective": (
                6, 149, 894,
                "79b8145303fba0fd043be400d8a48e8eec5043a0e219f601830938eea77e81e7",
            ),
        },
    },
    "english_pre_master.xlsx": {
        "digest": "b228178e9e800b3243fda9f8c6c5016ac8fbfd476738d94a4febc1d446349c0d",
        "sheets": {
            "Objective": (
                5, 72, 360,
                "1e398a9fa649a6f33923489bb8dd8d957a63b00efcd6a9e4368dab459c483815",
            ),
            "Descriptive": (
                36, 380, 13680,
                "c1bbf1481cbc8f59b080c52e5a9f2a8b3f301f6ae75a41712d195e4e953eebfa",
            ),
            "Subjective": (
                0, 149, 0,
                "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
            ),
        },
    },
    "math_post_master.xlsx": {
        "digest": "13566d6a2834a3ad5e3d807e9c06b38ce207bbb3eccda4184a17fce1ec50c865",
        "sheets": {
            "Objective": (
                47, 72, 3384,
                "5fc27a1aa3e721e9ccefc35eac150b110bd214c8bf05a9c183820f1ec68a0c67",
            ),
            "Descriptive": (
                24, 380, 9120,
                "3909c2100034c46a2b8dc663b6f06b4d73e69a5011c3d87603ca8e421e69f124",
            ),
            "Subjective": (
                4, 149, 596,
                "c0614760168131b86ab30391f83f2c0688e5a002d93ff5fa60f8248a0ec64dcd",
            ),
        },
    },
    "math_pre_master.xlsx": {
        "digest": "f5d622a57d8f83c373b2424740e341a8752be9f0176e0a297f9cac9a96e41f14",
        "sheets": {
            "Objective": (
                6, 72, 432,
                "58a09920c7a78415abed7ec25b6ba0a97de990e814e00bd511f4f254b3f9580b",
            ),
            "Descriptive": (
                21, 380, 7980,
                "1a97a679411c525b5d14b29622bb285a0b5762387b82ca1d073ff0c80df32d1d",
            ),
            "Subjective": (
                0, 149, 0,
                "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
            ),
        },
    },
}


@pytest.mark.parametrize(
    "master_filename,subject,phase,answer_slots,_contract_id",
    MASTER_CONTRACTS,
)
def test_every_normalized_rendered_master_cell_is_pinned(
    master_filename: str,
    subject: str,
    phase: str,
    answer_slots: int,
    _contract_id: str,
) -> None:
    assert _full_rendered_master_evidence(
        master_filename, subject, phase, answer_slots
    ) == FULL_RENDERED_MASTER_EVIDENCE[master_filename]


def test_english_post_article_blanks_are_six_subjective_rows_in_log_order() -> None:
    raw = _read_fixture("english_post_master.xlsx")
    subjective = raw.sheets["Subjective"]
    rows = subjective.logical_records()
    assert len(rows) == 6

    labels = [row["question_label"] for row in rows]
    assert labels == [
        "06MSEN_SelfHelpIsth_PL_T05_C05 Q04",
        "06MSEN_SelfHelpIsth_PL_T05_C05 Q05",
        "06MSEN_SelfHelpIsth_PL_T05_C05 Q06",
        "06MSEN_SelfHelpIsth_PL_T05_C05 Q07",
        "06MSEN_SelfHelpIsth_PL_T05_C05 Q08",
        "06MSEN_SelfHelpIsth_PL_T05_C05 Q09",
    ]
    raw_answers = [row["answer_1"] for row in rows]
    assert raw_answers == ["an", "The", "an", "The", "a", "An"]
    assert [answer.casefold() for answer in raw_answers] == [
        "an", "the", "an", "the", "a", "an",
    ]
    assert [row["math_keyboard"] for row in rows] == ["No"] * 6
    assert [row["question_category"] for row in rows] == [
        "Fill in the Blanks"
    ] * 6

    # Each blank has one answer and no alternative/option list.  Objective
    # option columns do not exist on this lane, and the other 19 Subjective
    # answer slots are empty on every migrated row.
    assert not {
        "answer_content_1", "correct_answer_1"
    }.intersection(header.strip() for header in subjective.headers)
    for row in rows:
        assert [
            number
            for number in range(1, 21)
            if row.get(f"answer_{number}", "") != ""
        ] == [1]
        assert "\n" not in row["question_text"]

    for sheet in ("Objective", "Descriptive"):
        other_labels = {
            row.get("question_label", "")
            for row in raw.sheets[sheet].logical_records()
        }
        assert set(labels).isdisjoint(other_labels)


MATH_CATEGORY_NORMALIZATION = {
    "Fill in the Blanks": "Fill in the blanks",
    "Very Short answer Questions": "Very Short Answer Questions",
    "Short Answer Type (2 marks)": "Short Answer Type (2 Marks)",
    "Short Answer Type (3 marks)": "Short Answer Type (3 Marks)",
    "Long Answer Type (4 marks)": "Long Answer Type (4 Marks)",
    "Long Answer Type (5 marks)": "Long Answer Type (5 Marks)",
}
MATH_RAW_CATEGORY_COUNTS = Counter({
    "Fill in the blanks": 31,
    "Very Short answer Questions": 12,
    "Short Answer Type (2 marks)": 16,
    "Multiple Choice Question": 7,
    "Long Answer Type (4 marks)": 6,
    "Very Short Answer Questions": 6,
    "True or False": 5,
    "Short Answer Type (3 marks)": 3,
    "Long Answer Type (5 marks)": 2,
    "Fill in the Blanks": 1,
})
MATH_NORMALIZED_CATEGORY_COUNTS = Counter({
    "Fill in the blanks": 32,
    "Very Short Answer Questions": 18,
    "Short Answer Type (2 Marks)": 16,
    "Multiple Choice Question": 7,
    "Long Answer Type (4 Marks)": 6,
    "True or False": 5,
    "Short Answer Type (3 Marks)": 3,
    "Long Answer Type (5 Marks)": 2,
})


def _math_question_rows() -> Iterable[tuple[str, str, dict[str, str]]]:
    for filename in ("math_post_master.xlsx", "math_pre_master.xlsx"):
        raw = _read_fixture(filename)
        for sheet in raw.sheet_order:
            for row in raw.sheets[sheet].logical_records():
                if row.get("question_label"):
                    yield filename, sheet, row


def test_math_category_casing_is_normalized_to_the_log_taxonomy() -> None:
    rows = list(_math_question_rows())
    assert len(rows) == 89
    assert Counter(row["question_category"] for _, _, row in rows) == (
        MATH_RAW_CATEGORY_COUNTS
    )

    normalized = [
        (
            filename,
            sheet,
            MATH_CATEGORY_NORMALIZATION.get(
                row["question_category"], row["question_category"]
            ),
        )
        for filename, sheet, row in rows
    ]
    assert Counter(category for _, _, category in normalized) == (
        MATH_NORMALIZED_CATEGORY_COUNTS
    )
    assert sum(MATH_RAW_CATEGORY_COUNTS[key] for key in (
        MATH_CATEGORY_NORMALIZATION
    )) == 40

    metadata = {
        "board": "MSBSHSE", "grade": "06", "subject": "Mathematics"
    }
    categories = assessment_profile.question_categories(
        _profile("Mathematics"), metadata
    )
    for _filename, sheet, category in normalized:
        assert category in categories[sheet.casefold()]


MATH_DURATION_MATRIX = {
    "Very Short Answer Questions": {
        "Less": Decimal(1), "Moderate": Decimal(1), "High": Decimal(2),
    },
    "Short Answer Type (2 Marks)": {
        "Less": Decimal(2), "Moderate": Decimal(2), "High": Decimal(3),
    },
    "Short Answer Type (3 Marks)": {
        "Less": Decimal(4), "Moderate": Decimal(5), "High": Decimal(6),
    },
    "Long Answer Type (4 Marks)": {
        "Less": Decimal(5), "Moderate": Decimal(6), "High": Decimal(7),
    },
    "Long Answer Type (5 Marks)": {
        "Less": Decimal(5), "Moderate": Decimal(7), "High": Decimal(7),
    },
}
MATH_DURATION_CONTRADICTIONS = {
    "06MSMA_IntheWorldof_c71b5a49_T01_C01_PL_Q01": (4, 7),
    "06MSMA_IntheWorldof_c71b5a49_T01_C01_PL_Q02": (4, 7),
    "06MSMA_IntheWorldof_c71b5a49_T02_C03_PL_Q08": (4, 7),
    "06MSMA_IntheWorldof_c71b5a49_T03_C02_PL_Q09": (5, 7),
    "06MSMA_IntheWorldof_c71b5a49_T05_C01_PL_Q18": (4, 6),
    "06MSMA_IntheWorldof_c71b5a49_T05_C02_PL_Q22": (4, 6),
    "06MSMA_IntheWorldof_c71b5a49_T08_C01_PL_Q23": (5, 7),
    "06MSMA_IntheWorldof_c71b5a49_T02_C02_PL_Q25": (2, 1),
    "06MSMA_IntheWorldof_c71b5a49_T02_C02_PL_Q26": (2, 1),
    "06MSMA_IntheWorldof_c71b5a49_T05_C01_PL_Q50": (2, 1),
    "06MSMA_IntheWorldof_c71b5a49_PrL_T01_C01 Q04": (3, 5),
    "06MSMA_IntheWorldof_c71b5a49_PrL_T01_C02 Q03": (3, 5),
    "06MSMA_IntheWorldof_c71b5a49_PrL_T02_C01 Q05": (2, 1),
    "06MSMA_IntheWorldof_c71b5a49_PrL_T02_C01 Q06": (3, 5),
    "06MSMA_IntheWorldof_c71b5a49_PrL_T03_C02 Q03": (1, 2),
    "06MSMA_IntheWorldof_c71b5a49_PrL_T03_C02 Q05": (5, 6),
}


def _logged_duration(sheet: str, row: dict[str, str]) -> Decimal:
    category = MATH_CATEGORY_NORMALIZATION.get(
        row["question_category"], row["question_category"]
    )
    if sheet in {"Objective", "Subjective"}:
        # Every raw Objective/Subjective row is one independently scored
        # item/blank, so the log's per-subpoint rule resolves to one minute.
        return Decimal(1)
    return MATH_DURATION_MATRIX[category][row["level_of_difficulty"]]


def test_math_raw_files_contain_exactly_sixteen_duration_contradictions() -> None:
    contradictions: dict[str, tuple[int, int]] = {}
    for _filename, sheet, row in _math_question_rows():
        observed = Decimal(row["question_duration"])
        expected = _logged_duration(sheet, row)
        if observed != expected:
            contradictions[row["question_label"]] = (
                int(observed), int(expected)
            )

    assert len(contradictions) == 16
    assert contradictions == MATH_DURATION_CONTRADICTIONS


def test_math_chapter_duration_outlier_is_evidence_not_the_target() -> None:
    observed: dict[str, dict[str, set[str]]] = {}
    for filename in (
        "math_post_concept.xlsx",
        "math_post_master.xlsx",
        "math_pre_concept.xlsx",
        "math_pre_master.xlsx",
    ):
        raw = _read_fixture(filename)
        observed[filename] = {}
        for sheet_name, sheet in raw.sheets.items():
            if not sheet.logical_rows:
                continue
            column = sheet.first_column("chapter_duration")
            observed[filename][sheet_name] = {
                sheet.rows[row].get(column, "") for row in sheet.logical_rows
            }

    assert observed == {
        "math_post_concept.xlsx": {"Objective": {"480 minutes"}},
        "math_post_master.xlsx": {
            "Objective": {"362"},
            "Descriptive": {"362"},
            "Subjective": {"362"},
        },
        "math_pre_concept.xlsx": {"Objective": {"362"}},
        "math_pre_master.xlsx": {
            "Objective": {"362"},
            "Descriptive": {"362"},
        },
    }
    assert observed["math_post_concept.xlsx"]["Objective"] != {"362"}


def test_english_post_raw_topic_ids_record_collision_and_wrong_title_token() -> None:
    rows = _read_fixture("english_post_concept.xlsx").sheets[
        "Objective"
    ].logical_records()
    topic_titles = list(dict.fromkeys(row["topic_title"] for row in rows))
    assert len(topic_titles) == 5
    machine_ids = {
        re.search(r"\(([^()]*)\)$", title).group(1)  # type: ignore[union-attr]
        for title in topic_titles
    }
    assert machine_ids == {"06MSEN_Self_Help_Is_the_Best_Help_PL"}
    assert {
        row["chapter_title"] for row in rows
    } == {"Self Help Is the Only Way (06_English_MSBSHSE_Balbharati)"}


def test_english_post_normalized_render_uses_unique_source_aligned_ids() -> None:
    snapshot, _rows = _concept_snapshot(
        "english_post_concept.xlsx", "English"
    )
    profile = _profile("English")
    concept_rows = workbook.parse_workbook(
        workbook.render_concept_file(snapshot, profile)
    )["sheets"]["Objective"]["rows"]
    topic_titles = list(dict.fromkeys(
        str(row["topic_title"]) for row in concept_rows
    ))
    expected_topic_ids = {
        f"06MSEN_SelfHelpIsth_89ddc8fc_PL_T{number:02d}"
        for number in range(1, 6)
    }
    assert len(topic_titles) == 5
    assert {identity.title_tag(title) for title in topic_titles} == (
        expected_topic_ids
    )
    assert all("Best_Help" not in title for title in topic_titles)
    assert all(
        "Best_Help" not in str(row["concept_title"])
        for row in concept_rows
    )
    assert all(
        "Best_Help" not in str(value)
        for row in concept_rows
        for value in row.values()
    )
    assert {
        row["chapter_title"] for row in concept_rows
    } == {"Self Help Is the Only Way (06_English_MSBSHSE_Balbharati)"}
    assert any("Self Help Is the Only Way" in title for title in topic_titles)

    master_snapshot, _candidates = _master_snapshot(
        "english_post_master.xlsx",
        "english_post_concept.xlsx",
        "English",
        30,
    )
    master_data, _issues = workbook.render_master_file(
        master_snapshot, profile
    )
    master_rows = workbook.parse_workbook(master_data)["sheets"]
    rendered_master_topics = {
        str(row["topic_title"])
        for sheet in CANONICAL_SHEET_ORDER
        for row in master_rows[sheet]["rows"]
    }
    assert {identity.title_tag(title) for title in rendered_master_topics} == (
        expected_topic_ids
    )
    assert all("Best_Help" not in title for title in rendered_master_topics)
    assert all(
        "Best_Help" not in str(value)
        for sheet in CANONICAL_SHEET_ORDER
        for row in master_rows[sheet]["rows"]
        for value in row.values()
    )

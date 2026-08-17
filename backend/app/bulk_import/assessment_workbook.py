"""Assessment workbook: dual projections of one immutable release snapshot.

The positional authority for everything here is the Grade-6 MES Bulk Import
FINAL template set (spec §0 item 2, §23), captured verbatim in
``assessment_workbook_template.json``: three sheets (Objective, Descriptive,
Subjective — no Doc Link, no trailing spaces in sheet names), the exact
two-row headers, the row-1 merged bands with their sheet-specific labels,
``answer_restriction`` between ``question_appears_in`` and
``level_of_difficulty``, ``question_text`` directly after ``question``, and
``answer_explanation`` closing the Objective/Subjective bands.

Two renderers project ONE snapshot (spec §1, §13.1):

* Concept File — hierarchy catalogue only: chapter/topic/concept bands in
  source order, Group and Question bands blank, Descriptive and Subjective
  header-only.
* Master File — the complete import: every concept including questionless
  ones, every group (required shells and occupied groups, with catalogue
  rows for groups no question row represents), every Objective and
  Descriptive assessment with answers, rubrics, subquestions, restriction,
  the exact MES appears-in wire value, and complete label aggregates; zero
  Subjective data rows.

Deterministic mechanics only: positional serialization, formula-injection
escaping, cell limits, read-back parsing, and count/arithmetic validation.
Nothing here decides content — flags and unplaced candidates ride into the
returned manifest, never disappear.
"""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from typing import Any, Mapping

import openpyxl
from openpyxl.styles import Alignment, Font

from ..services import assessment_profile
from ..services import assessment_release as rel

CELL_LIMIT = 32_767

_MANIFEST_PATH = Path(__file__).with_name("assessment_workbook_template.json")
MANIFEST: dict = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
SHEET_ORDER: list[str] = list(MANIFEST["sheet_order"])
FIELDS: dict[str, list[str]] = {
    name: list(sheet["fields"]) for name, sheet in MANIFEST["sheets"].items()
}
BANDS: dict[str, list[dict]] = {
    name: list(sheet["bands"]) for name, sheet in MANIFEST["sheets"].items()
}
_INDEX: dict[str, dict[str, int]] = {
    name: {field: i for i, field in enumerate(fields)}
    for name, fields in FIELDS.items()
}

# MES §3.8: these fields ship blank regardless of what upstream rows carry.
_FORCED_BLANK = {"chapter_duration", "question_disclaimer"}

MAX_OBJECTIVE_OPTIONS = 6
MAX_DESCRIPTIVE_ANSWERS = 10
MAX_SUBQUESTIONS = 15
MAX_SUBQUESTION_KEYWORDS = 6


class WorkbookRenderError(ValueError):
    """A workbook cannot be rendered without violating a mechanical rule."""


def _cell_value(value: Any, *, context: str) -> Any:
    if value is None:
        return ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    text = str(value)
    if len(text) > CELL_LIMIT:
        raise WorkbookRenderError(
            f"{context}: cell exceeds {CELL_LIMIT} characters "
            f"({len(text)})")
    if text[:1] in {"=", "+", "-", "@"}:
        return "'" + text  # formula-injection guard (spec §11)
    return text


def _row_values(sheet: str, record: Mapping[str, Any]) -> list:
    fields = FIELDS[sheet]
    row = []
    for field in fields:
        value = "" if field in _FORCED_BLANK else record.get(field, "")
        row.append(_cell_value(
            value, context=f"{sheet}:{field}"))
    return row


def _write_headers(ws, sheet: str) -> None:
    for band in BANDS[sheet]:
        cell = ws.cell(row=1, column=band["start"])
        cell.value = band["label"]
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal="center")
        if band["end"] > band["start"]:
            ws.merge_cells(
                start_row=1, start_column=band["start"],
                end_row=1, end_column=band["end"])
    for i, field in enumerate(FIELDS[sheet], start=1):
        cell = ws.cell(row=2, column=i)
        cell.value = field
        cell.font = Font(bold=True, size=9)
    ws.freeze_panes = "A3"


def _new_workbook() -> openpyxl.Workbook:
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name in SHEET_ORDER:
        _write_headers(wb.create_sheet(name), name)
    return wb


def _workbook_bytes(wb: openpyxl.Workbook) -> bytes:
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


# --------------------------------------------------------------------------- #
# Snapshot access
# --------------------------------------------------------------------------- #
# The snapshot is one immutable dict (the release projection):
#   chapter: {chapter_title, chapter_display_name, pre_topics, post_topics,
#             chapter_description}
#   topics:  ordered [{topic fields..., "concepts": ordered [concept fields
#            ..., "concept_key"]}]
#   groups:  [group records (assessment_grouping.group_record) each with
#            "concept_key"]
#   candidates: [materialized candidates with "question_label" and
#            "concept_key"/"group_key" filled from their placement]
# ``concept_key`` is any stable join key (machine id); titles are display
# values and never used for joining.


def _concept_rows(snapshot: Mapping) -> list[dict]:
    rows = []
    chapter = dict(snapshot.get("chapter") or {})
    for topic in snapshot.get("topics") or []:
        topic_fields = {
            k: v for k, v in topic.items() if k != "concepts"
        }
        for concept in topic.get("concepts") or []:
            rows.append({
                "chapter": chapter,
                "topic": topic_fields,
                "concept": dict(concept),
            })
    return rows


def _bands_record(entry: Mapping) -> dict:
    record: dict = {}
    record.update(entry["chapter"])
    record.update(entry["topic"])
    record.update({
        k: v for k, v in entry["concept"].items() if k != "concept_key"
    })
    return record


def snapshot_sha256(snapshot: Mapping) -> str:
    return rel.sha256_json(snapshot)


# --------------------------------------------------------------------------- #
# Output A — Concept File (spec §1, §9)
# --------------------------------------------------------------------------- #

def render_concept_file(snapshot: Mapping) -> bytes:
    wb = _new_workbook()
    ws = wb["Objective"]
    for entry in _concept_rows(snapshot):
        record = _bands_record(entry)
        # A clean catalogue: Group and Question bands stay blank, and the
        # concept-band group labels stay blank too — groups do not exist in
        # Output A (spec §1: "no populated Group fields").
        for field in ("basic_groups", "intermediate_groups",
                      "advanced_groups", "concept_question_labels"):
            record[field] = ""
        ws.append(_row_values("Objective", record))
    return _workbook_bytes(wb)


# --------------------------------------------------------------------------- #
# Output B — Master File (spec §1, §10)
# --------------------------------------------------------------------------- #

def _question_record(candidate: Mapping, sheet: str) -> dict:
    record = {
        "question_label": candidate.get("question_label", ""),
        "question_category": candidate.get("question_category", ""),
        "cognitive_skills": candidate.get("cognitive_skill", ""),
        "question_source": candidate.get("question_source", ""),
        "question_duration": candidate.get("question_duration", 1),
        "question_appears_in": candidate.get("question_appears_in", ""),
        "answer_restriction": candidate.get("answer_restriction", ""),
        "level_of_difficulty": candidate.get("difficulty", ""),
        "question": candidate.get("question", ""),
        "question_text": candidate.get("question_text", ""),
        "marks": candidate.get("marks", ""),
        "answer_explanation": candidate.get("answer_explanation", ""),
    }
    answers = [
        a for a in candidate.get("answers") or [] if isinstance(a, Mapping)
    ]
    if sheet == "Objective":
        if len(answers) > MAX_OBJECTIVE_OPTIONS:
            raise WorkbookRenderError(
                f"{record['question_label']}: more than "
                f"{MAX_OBJECTIVE_OPTIONS} options")
        for n, answer in enumerate(answers, start=1):
            record[f"answer_type_{n}"] = answer.get("answer_type", "")
            record[f"answer_content_{n}"] = answer.get("answer_content", "")
            record[f"correct_answer_{n}"] = answer.get("correct_answer", "")
            record[f"answer_weightage_{n}"] = answer.get(
                "answer_weightage", "")
    else:  # Descriptive
        record["math_keyboard"] = candidate.get("math_keyboard", "")
        record["display_answer"] = candidate.get("display_answer", "")
        if len(answers) > MAX_DESCRIPTIVE_ANSWERS:
            raise WorkbookRenderError(
                f"{record['question_label']}: more than "
                f"{MAX_DESCRIPTIVE_ANSWERS} answer blocks")
        for n, answer in enumerate(answers, start=1):
            record[f"answer_type_{n}"] = answer.get("answer_type", "")
            record[f"answer_weightage_{n}"] = answer.get(
                "answer_weightage", "")
            record[f"answer_content_{n}"] = answer.get("answer_content", "")
        sub_questions = [
            s for s in candidate.get("sub_questions") or []
            if isinstance(s, Mapping)
        ]
        if len(sub_questions) > MAX_SUBQUESTIONS:
            raise WorkbookRenderError(
                f"{record['question_label']}: more than "
                f"{MAX_SUBQUESTIONS} subquestions")
        for n, sub in enumerate(sub_questions, start=1):
            record[f"sub_question_{n}"] = sub.get("text", "")
            record[f"sub_question_marks_{n}"] = sub.get("marks", "")
            keywords = [
                k for k in sub.get("keywords") or [] if isinstance(k, Mapping)
            ]
            if len(keywords) > MAX_SUBQUESTION_KEYWORDS:
                raise WorkbookRenderError(
                    f"{record['question_label']}: subquestion {n} has more "
                    f"than {MAX_SUBQUESTION_KEYWORDS} keyword slots")
            for m, keyword in enumerate(keywords, start=1):
                record[f"sq{n}_answer_type_{m}"] = keyword.get(
                    "answer_type", "")
                record[f"sq{n}_weightage_{m}"] = keyword.get("weightage", "")
                record[f"sq{n}_keyword_{m}"] = keyword.get("keyword", "")
    return record


def _group_record_fields(group: Mapping, group_labels: list[str]) -> dict:
    return {
        "group_name": group.get("group_name", ""),
        "group_display_name": group.get("group_display_name", ""),
        "group_description": group.get("semantic_description", ""),
        "group_status": group.get("group_status", "Active"),
        "group_type": group.get("group_type", ""),
        "group_question_labels": ", ".join(group_labels),
        "related_digicards": "",
    }


def render_master_file(snapshot: Mapping) -> tuple[bytes, dict]:
    """Render the Master File; returns (bytes, issues manifest).

    Nothing disappears: a candidate whose placement never resolved to a
    concept/group cannot be positioned in the hierarchy, so it is recorded
    in the issues manifest (spec §13.3 keeps the full ledger in the release
    manifest, never an unrecognized extra sheet).
    """
    concept_entries = {
        str(entry["concept"].get("concept_key") or ""): entry
        for entry in _concept_rows(snapshot)
    }
    groups = [dict(g) for g in snapshot.get("groups") or []]
    groups_by_key: dict[str, dict] = {}
    for group in groups:
        group_key = str(group.get("group_key") or "")
        if not group_key:
            raise WorkbookRenderError("group_key must not be blank")
        if group_key in groups_by_key:
            raise WorkbookRenderError(
                f"duplicate group_key {group_key!r}")
        groups_by_key[group_key] = group
    for group_key, group in groups_by_key.items():
        concept_key = str(group.get("concept_key") or "")
        concept_entry = concept_entries.get(concept_key)
        if concept_entry is None:
            raise WorkbookRenderError(
                f"group {group_key!r} has unknown concept home "
                f"{concept_key!r}")
        concept_name = str(
            concept_entry["concept"].get("concept_display_name") or ""
        ).strip()
        if not concept_name:
            raise WorkbookRenderError(
                f"group {group_key!r} home {concept_key!r} has no explicit "
                "concept_display_name")
        tier = str(group.get("group_type") or "")
        if tier not in rel.GROUP_TYPES:
            raise WorkbookRenderError(
                f"group {group_key!r} has invalid group_type {tier!r}")
        visible_name = f"{concept_name} — {tier}"
        if (
            str(group.get("group_name") or "") != visible_name
            or str(group.get("group_display_name") or "") != visible_name
        ):
            raise WorkbookRenderError(
                f"group {group_key!r} visible names must both equal "
                f"{visible_name!r}")
    candidates = [dict(c) for c in snapshot.get("candidates") or []]

    # Complete ordered label aggregates (spec §8.4).
    concept_labels: dict[str, list[str]] = {}
    group_labels: dict[str, list[str]] = {}
    unplaced: list[dict] = []
    placed: list[dict] = []
    for candidate in candidates:
        concept_key = str(candidate.get("concept_key") or "")
        group_key = str(candidate.get("group_key") or "")
        label = str(candidate.get("question_label") or "")
        if (
            concept_key not in concept_entries
            or group_key not in groups_by_key
            or not label
        ):
            unplaced.append({
                "candidate_id": str(candidate.get("candidate_id") or ""),
                "question_label": label,
                "reason": "unresolved home concept/group placement",
                "flags": list(candidate.get("flags") or []),
            })
            continue
        group_concept_key = str(
            groups_by_key[group_key].get("concept_key") or "")
        if group_concept_key != concept_key:
            identity = label or str(candidate.get("candidate_id") or "")
            raise WorkbookRenderError(
                f"{identity}: concept_key {concept_key!r} does not match "
                f"group {group_key!r} home {group_concept_key!r}")
        concept_labels.setdefault(concept_key, []).append(label)
        group_labels.setdefault(group_key, []).append(label)
        placed.append(candidate)

    wb = _new_workbook()
    represented_groups: set[str] = set()
    group_provenance: list[dict] = []

    def _append_group_row(
        sheet: str, group_key: str, record: Mapping[str, Any],
    ) -> None:
        ws = wb[sheet]
        ws.append(_row_values(sheet, record))
        group_provenance.append({
            "sheet": sheet,
            "row": ws.max_row,
            "group_key": group_key,
        })

    def _full_record(candidate: Mapping, sheet: str) -> dict:
        concept_key = str(candidate.get("concept_key") or "")
        group_key = str(candidate.get("group_key") or "")
        entry = concept_entries[concept_key]
        record = _bands_record(entry)
        record["concept_question_labels"] = ", ".join(
            concept_labels.get(concept_key, []))
        record.update(_group_record_fields(
            groups_by_key[group_key], group_labels.get(group_key, [])))
        record.update(_question_record(candidate, sheet))
        return record

    sheet_for_kind = {"objective": "Objective", "descriptive": "Descriptive"}
    question_rows = 0
    for candidate in placed:
        kind = str(candidate.get("sheet_kind") or "")
        sheet = sheet_for_kind.get(kind)
        if sheet is None:
            # The reference profile emits no Subjective data rows.
            unplaced.append({
                "candidate_id": str(candidate.get("candidate_id") or ""),
                "question_label": str(candidate.get("question_label") or ""),
                "reason": f"sheet kind {kind!r} has no MES data sheet",
                "flags": list(candidate.get("flags") or []),
            })
            continue
        group_key = str(candidate.get("group_key") or "")
        _append_group_row(
            sheet, group_key, _full_record(candidate, sheet))
        question_rows += 1
        represented_groups.add(group_key)

    # Group catalogue rows: every created group not otherwise represented by
    # a question row — required empty shells included — appears once on the
    # Objective sheet (the catalogue carrier), Question band blank. This is
    # also what keeps questionless concepts in the Master (spec §10).
    for group in groups:
        group_key = str(group.get("group_key") or "")
        if group_key in represented_groups:
            continue
        concept_key = str(group.get("concept_key") or "")
        entry = concept_entries[concept_key]
        record = _bands_record(entry)
        record["concept_question_labels"] = ", ".join(
            concept_labels.get(concept_key, []))
        record.update(_group_record_fields(
            group, group_labels.get(group_key, [])))
        _append_group_row("Objective", group_key, record)

    issues = {
        "unplaced": unplaced,
        "placed_questions": question_rows,
        "groups": len(groups),
        "group_provenance": group_provenance,
    }
    return _workbook_bytes(wb), issues


# --------------------------------------------------------------------------- #
# Read-back parsing and validation (spec §13.2 steps 2–4)
# --------------------------------------------------------------------------- #

def parse_workbook(data: bytes) -> dict:
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True,
                                data_only=True)
    parsed: dict = {"sheet_order": list(wb.sheetnames), "sheets": {}}
    for name in wb.sheetnames:
        ws = wb[name]
        rows = ws.iter_rows(values_only=True)
        next(rows, ())  # band row
        header = [h for h in next(rows, ()) if h is not None]
        records = []
        row_numbers = []
        for row_number, row in enumerate(rows, start=3):
            if row is None or not any(row):
                continue
            records.append({
                field: ("" if i >= len(row) or row[i] is None else row[i])
                for i, field in enumerate(header)
            })
            row_numbers.append(row_number)
        parsed["sheets"][name] = {
            "fields": header,
            "rows": records,
            "row_numbers": row_numbers,
        }
    wb.close()
    return parsed


def _header_errors(parsed: Mapping) -> list[str]:
    errors = []
    if parsed.get("sheet_order") != SHEET_ORDER:
        errors.append(
            f"sheet order {parsed.get('sheet_order')} != {SHEET_ORDER}")
    for name in SHEET_ORDER:
        sheet = (parsed.get("sheets") or {}).get(name)
        if sheet is None:
            errors.append(f"missing sheet {name!r}")
            continue
        if sheet["fields"] != FIELDS[name]:
            errors.append(f"{name}: header row differs from the template")
    return errors


def validate_concept_file(parsed: Mapping, snapshot: Mapping) -> list[str]:
    errors = _header_errors(parsed)
    if errors:
        return errors
    rows = parsed["sheets"]["Objective"]["rows"]
    expected = [
        str(e["concept"].get("concept_title") or "")
        for e in _concept_rows(snapshot)
    ]
    actual = [str(r.get("concept_title") or "") for r in rows]
    if actual != expected:
        errors.append(
            f"concept rows differ: expected {len(expected)}, "
            f"got {len(actual)} (or out of source order)")
    for i, row in enumerate(rows, start=3):
        populated = [
            field for field in (
                "group_name", "group_type", "question_label", "question",
                "basic_groups", "intermediate_groups", "advanced_groups",
                "concept_question_labels", "chapter_duration",
            )
            if str(row.get(field) or "").strip()
        ]
        if populated:
            errors.append(
                f"Objective row {i}: Concept File must keep {populated} "
                "blank")
    for name in ("Descriptive", "Subjective"):
        if parsed["sheets"][name]["rows"]:
            errors.append(f"{name}: Concept File must be header-only")
    return errors


def validate_master_file(
    parsed: Mapping, snapshot: Mapping, profile: Mapping | str | None = None,
    *, group_provenance: list[Mapping[str, Any]] | None = None,
) -> list[str]:
    profile = assessment_profile.resolve(profile)
    errors = _header_errors(parsed)
    if errors:
        return errors
    if not profile["allow_subjective_rows"] and (
        parsed["sheets"]["Subjective"]["rows"]
    ):
        errors.append(
            f"Subjective: profile {profile['name']!r} allows no data rows")

    groups = [dict(g) for g in snapshot.get("groups") or []]
    groups_by_key: dict[str, dict] = {}
    for group in groups:
        group_key = str(group.get("group_key") or "")
        if not group_key:
            errors.append("snapshot contains a blank group_key")
        elif group_key in groups_by_key:
            errors.append(f"snapshot contains duplicate group_key {group_key!r}")
        else:
            groups_by_key[group_key] = group
    expected_group_keys = set(groups_by_key)
    keys_by_visible_name: dict[str, list[str]] = {}
    for group_key, group in groups_by_key.items():
        group_name = str(group.get("group_name") or "")
        keys_by_visible_name.setdefault(group_name, []).append(group_key)

    provenance_by_row: dict[tuple[str, int], str] = {}
    if group_provenance is not None:
        for n, item in enumerate(group_provenance, start=1):
            sheet = str(item.get("sheet") or "")
            try:
                row_number = int(item.get("row"))
            except (TypeError, ValueError):
                errors.append(
                    f"group provenance entry {n}: row must be an integer")
                continue
            group_key = str(item.get("group_key") or "")
            coordinate = (sheet, row_number)
            if sheet not in {"Objective", "Descriptive"} or row_number < 3:
                errors.append(
                    f"group provenance entry {n}: invalid coordinate "
                    f"{coordinate!r}")
                continue
            if coordinate in provenance_by_row:
                errors.append(
                    f"group provenance repeats {sheet} row {row_number}")
                continue
            if group_key not in groups_by_key:
                errors.append(
                    f"group provenance {sheet} row {row_number}: unknown "
                    f"group_key {group_key!r}")
                continue
            provenance_by_row[coordinate] = group_key

    concept_entries = {
        str(entry["concept"].get("concept_key") or ""): entry
        for entry in _concept_rows(snapshot)
    }
    concept_keys = set(concept_entries)
    expected_group_labels: dict[str, list[str]] = {}
    for candidate in snapshot.get("candidates") or []:
        concept_key = str(candidate.get("concept_key") or "")
        group_key = str(candidate.get("group_key") or "")
        label = str(candidate.get("question_label") or "")
        if (
            concept_key in concept_keys
            and group_key in groups_by_key
            and label
        ):
            group_concept_key = str(
                groups_by_key[group_key].get("concept_key") or "")
            if group_concept_key != concept_key:
                errors.append(
                    f"{label}: candidate concept_key {concept_key!r} does "
                    f"not match group {group_key!r} home "
                    f"{group_concept_key!r}")
                continue
            expected_group_labels.setdefault(group_key, []).append(label)

    seen_group_keys: set[str] = set()
    seen_concepts: set[str] = set()
    question_rows = 0
    aggregate_by_group: dict[str, set[str]] = {}
    visited_provenance: set[tuple[str, int]] = set()
    for name in ("Objective", "Descriptive"):
        sheet_rows = parsed["sheets"][name]["rows"]
        row_numbers = parsed["sheets"][name].get("row_numbers")
        if row_numbers is None:
            row_numbers = list(range(3, 3 + len(sheet_rows)))
        elif len(row_numbers) != len(sheet_rows):
            errors.append(
                f"{name}: read-back row number ledger differs from rows")
            row_numbers = list(range(3, 3 + len(sheet_rows)))
        for i, row in zip(row_numbers, sheet_rows):
            seen_concepts.add(str(row.get("concept_title") or ""))
            group_name = str(row.get("group_name") or "")
            coordinate = (name, i)
            group_key = provenance_by_row.get(coordinate, "")
            if group_key:
                visited_provenance.add(coordinate)
            elif group_name and group_provenance is not None:
                errors.append(
                    f"{name} row {i}: group provenance is missing")
            elif group_name:
                matching_keys = keys_by_visible_name.get(group_name, [])
                if len(matching_keys) == 1:
                    group_key = matching_keys[0]
                elif len(matching_keys) > 1:
                    errors.append(
                        f"{name} row {i}: group provenance required for "
                        f"non-unique visible group name {group_name!r}")
                else:
                    errors.append(
                        f"{name} row {i}: unknown group name {group_name!r}")
            if group_key:
                seen_group_keys.add(group_key)
                expected_group = groups_by_key[group_key]
                expected_name = str(expected_group.get("group_name") or "")
                expected_display = str(
                    expected_group.get("group_display_name") or "")
                expected_type = str(expected_group.get("group_type") or "")
                group_concept_key = str(
                    expected_group.get("concept_key") or "")
                concept_entry = concept_entries.get(group_concept_key)
                if group_name != expected_name:
                    errors.append(
                        f"{name} row {i}: group_name {group_name!r} does "
                        f"not match {group_key!r}")
                if str(row.get("group_display_name") or "") != expected_display:
                    errors.append(
                        f"{name} row {i}: group_display_name does not "
                        f"match {group_key!r}")
                if str(row.get("group_type") or "") != expected_type:
                    errors.append(
                        f"{name} row {i}: group_type does not match "
                        f"{group_key!r}")
                if concept_entry is None:
                    errors.append(
                        f"{name} row {i}: {group_key!r} has unknown "
                        f"concept home {group_concept_key!r}")
                else:
                    expected_concept_title = str(
                        concept_entry["concept"].get("concept_title") or "")
                    if str(row.get("concept_title") or "") != (
                        expected_concept_title
                    ):
                        errors.append(
                            f"{name} row {i}: concept home does not match "
                            f"{group_key!r}")
                aggregate_by_group.setdefault(group_key, set()).add(
                    str(row.get("group_question_labels") or ""))
            if str(row.get("chapter_duration") or "").strip():
                errors.append(f"{name} row {i}: chapter_duration must be blank")
            if str(row.get("question_disclaimer") or "").strip():
                errors.append(
                    f"{name} row {i}: question_disclaimer must be blank")
            label = str(row.get("question_label") or "")
            if not label:
                continue
            question_rows += 1
            appears = str(row.get("question_appears_in") or "")
            if appears != profile["appears_in"]:
                errors.append(
                    f"{label}: question_appears_in {appears!r} is not the "
                    f"profile wire value {profile['appears_in']!r}")
            restriction = str(row.get("answer_restriction") or "")
            if restriction not in rel.ANSWER_RESTRICTIONS:
                errors.append(
                    f"{label}: answer_restriction {restriction!r} invalid")
            if name == "Objective":
                marks = float(row.get("marks") or 0)
                correct_weight = 0.0
                correct_count = 0
                for n in range(1, MAX_OBJECTIVE_OPTIONS + 1):
                    if rel.is_correct_option(row.get(f"correct_answer_{n}")):
                        correct_count += 1
                        try:
                            correct_weight = float(
                                row.get(f"answer_weightage_{n}") or 0)
                        except (TypeError, ValueError):
                            correct_weight = -1.0
                if correct_count != 1:
                    errors.append(
                        f"{label}: {correct_count} correct options")
                elif abs(correct_weight - marks) > 0.01:
                    errors.append(
                        f"{label}: correct weightage {correct_weight:g} != "
                        f"marks {marks:g}")

    # Every concept (questionless included) and every created group appears.
    expected_concepts = {
        str(e["concept"].get("concept_title") or "")
        for e in _concept_rows(snapshot)
    }
    for concept_title in sorted(expected_concepts - seen_concepts):
        errors.append(f"concept missing from Master: {concept_title!r}")
    for group_key in sorted(expected_group_keys - seen_group_keys):
        errors.append(f"group missing from Master: {group_key!r}")
    for sheet, row_number in sorted(
        set(provenance_by_row) - visited_provenance
    ):
        errors.append(
            f"group provenance {sheet} row {row_number}: no read-back row")
    for group_key, aggregates in aggregate_by_group.items():
        if len(aggregates) > 1:
            errors.append(
                f"{group_key}: group_question_labels differs across "
                "repeated rows")
            continue
        actual_aggregate = next(iter(aggregates), "")
        expected_aggregate = ", ".join(
            expected_group_labels.get(group_key, []))
        if actual_aggregate != expected_aggregate:
            errors.append(
                f"{group_key}: group_question_labels {actual_aggregate!r} "
                f"!= {expected_aggregate!r}")
    return errors


# --------------------------------------------------------------------------- #
# Dual projection (spec §13.1–§13.2)
# --------------------------------------------------------------------------- #

def build_dual_output(
    snapshot: Mapping, profile: Mapping | str | None = None,
) -> dict:
    """Render, read back, validate, and hash both projections of one
    snapshot. Returns concepts/master bytes plus the shared manifest."""
    profile = assessment_profile.resolve(profile)
    snapshot_hash = snapshot_sha256(snapshot)
    concepts_bytes = render_concept_file(snapshot)
    master_bytes, issues = render_master_file(snapshot)
    concept_errors = validate_concept_file(
        parse_workbook(concepts_bytes), snapshot)
    master_errors = validate_master_file(
        parse_workbook(master_bytes), snapshot, profile,
        group_provenance=issues["group_provenance"])
    manifest = {
        "profile": profile["name"],
        "template_source": MANIFEST.get("source", ""),
        "concept_snapshot_sha256": snapshot_hash,
        "workbook_sha256s": {
            "concepts_xlsx": hashlib.sha256(concepts_bytes).hexdigest(),
            "master_xlsx": hashlib.sha256(master_bytes).hexdigest(),
        },
        "read_back": {
            "concepts_errors": concept_errors,
            "master_errors": master_errors,
        },
        "issues": issues,
    }
    return {
        "concepts_xlsx": concepts_bytes,
        "master_xlsx": master_bytes,
        "manifest": manifest,
        "valid": not concept_errors and not master_errors,
    }

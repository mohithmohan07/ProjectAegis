"""Staging acceptance against the reference school's gold workbooks
(spec §18 PR 7).

The three Grade-6 FINAL workbooks are the accepted reference output. The
harness reads each one with the profile parser, reconstructs a release
snapshot per chapter (a release is chapter-scoped, spec §1), renders the
Master through the production renderer, and compares every gold question
row field-by-field against what Aegis produces. Any divergence fails with
the exact sheet, label, and field named — a defect is reported, never
hidden behind polished output.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.bulk_import import assessment_workbook as aw
from app.services import katex_rules

FIXTURE_DIR = Path(__file__).resolve().parents[1] / (
    "data/Testing/reference_bulk_import")
FIXTURES = [
    "grade6_english.xlsx",
    "grade6_mathematics.xlsx",
    "grade6_science.xlsx",
]

_CHAPTER_FIELDS = (
    "chapter_title", "chapter_display_name", "chapter_duration",
    "pre_topics", "post_topics", "chapter_description",
)
_TOPIC_FIELDS = (
    "topic_title", "topic_display_name", "pre_post_learning",
    "topic_concept_labels", "related_topics", "topic_description",
)
_CONCEPT_FIELDS = (
    "concept_title", "concept_display_name", "concept_details", "keywords",
    "digicards", "related_concepts", "basic_groups", "intermediate_groups",
    "advanced_groups", "concept_source",
)
_GROUP_FIELDS = (
    "group_name", "group_display_name", "group_description", "group_status",
    "group_type", "related_digicards",
)
_ROLLUP_FIELD_BY_GROUP_TYPE = {
    "Basic": "basic_groups",
    "Intermediate": "intermediate_groups",
    "Advanced": "advanced_groups",
}


def _norm(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        as_float = float(value)
        return (
            repr(int(as_float)) if as_float.is_integer() else repr(as_float))
    return str(value)


def _fields(row, names) -> dict:
    return {name: row.get(name, "") for name in names}


def _answers_from_row(row, sheet: str) -> list[dict]:
    answers = []
    if sheet == "Objective":
        for n in range(1, aw.MAX_OBJECTIVE_OPTIONS + 1):
            block = {
                "answer_type": row.get(f"answer_type_{n}", ""),
                "answer_content": row.get(f"answer_content_{n}", ""),
                "correct_answer": row.get(f"correct_answer_{n}", ""),
                "answer_weightage": row.get(f"answer_weightage_{n}", ""),
            }
            if any(_norm(v) for v in block.values()):
                answers.append(block)
    elif sheet == "Subjective":
        for n in range(1, aw.MAX_SUBJECTIVE_ANSWERS + 1):
            block = {
                "answer_type": row.get(f"answer_type_{n}", ""),
                "answer_content": row.get(f"answer_{n}", ""),
                "answer_display": row.get(f"answer_display_{n}", ""),
                "answer_weightage": row.get(f"weightage_{n}", ""),
                "placeholder": row.get(f"placeholder_{n}", ""),
            }
            if any(_norm(v) for v in block.values()):
                answers.append(block)
    else:  # Descriptive
        for n in range(1, aw.MAX_DESCRIPTIVE_ANSWERS + 1):
            block = {
                "answer_type": row.get(f"answer_type_{n}", ""),
                "answer_weightage": row.get(f"answer_weightage_{n}", ""),
                "answer_content": row.get(f"answer_content_{n}", ""),
            }
            if any(_norm(v) for v in block.values()):
                answers.append(block)
    return answers


def _sub_questions_from_row(row) -> list[dict]:
    subs = []
    for n in range(1, aw.MAX_SUBQUESTIONS + 1):
        text = row.get(f"sub_question_{n}", "")
        marks = row.get(f"sub_question_marks_{n}", "")
        keywords = []
        for m in range(1, aw.MAX_SUBQUESTION_KEYWORDS + 1):
            block = {
                "answer_type": row.get(f"sq{n}_answer_type_{m}", ""),
                "weightage": row.get(f"sq{n}_weightage_{m}", ""),
                "keyword": row.get(f"sq{n}_keyword_{m}", ""),
            }
            if any(_norm(v) for v in block.values()):
                keywords.append(block)
        if _norm(text) or _norm(marks) or keywords:
            subs.append({"text": text, "marks": marks, "keywords": keywords})
    return subs


def _without_duplicated_subparts(value, sub_questions: list[dict]) -> str:
    """Retain the historical stem while moving each part to its own block."""

    text = str(value or "")
    for subquestion in sub_questions:
        part = str(subquestion.get("text") or "").strip()
        if not part:
            continue
        start = text.find(part)
        if start < 0:
            continue
        end = start + len(part)
        marks = _norm(subquestion.get("marks"))
        if marks:
            suffix = re.match(
                rf"\s*\(\s*{re.escape(marks)}\s*\)", text[end:]
            )
            if suffix is not None:
                end += suffix.end()
        text = f"{text[:start]} {text[end:]}"
    return re.sub(r"\s+", " ", text).strip()


def _candidate_from_row(row, sheet: str) -> dict:
    sheet_kind = {
        "Objective": "objective",
        "Descriptive": "descriptive",
        "Subjective": "subjective",
    }[sheet]
    sub_questions = (
        _sub_questions_from_row(row) if sheet == "Descriptive" else []
    )
    # The historical gold rows predate the exclusive multipart contract and
    # carry the same marks in both the main answer blocks and the subquestion
    # keyword rubrics.  Preserve the authoritative part rubrics; do not
    # reconstruct the retired duplicate scoring authority.
    answers = [] if sub_questions else _answers_from_row(row, sheet)
    question = row.get("question", "")
    question_text = row.get("question_text", "")
    if sub_questions:
        question = _without_duplicated_subparts(question, sub_questions)
        question_text = _without_duplicated_subparts(
            question_text, sub_questions
        )
    candidate = {
        "candidate_id": f"GOLD-{_norm(row.get('question_label'))}",
        "question_label": row.get("question_label", ""),
        "sheet_kind": sheet_kind,
        "question_category": row.get("question_category", ""),
        "cognitive_skill": row.get("cognitive_skills", ""),
        "question_source": row.get("question_source", ""),
        "question_duration": row.get("question_duration", ""),
        "question_appears_in": row.get("question_appears_in", ""),
        "answer_restriction": row.get("answer_restriction", ""),
        "difficulty": row.get("level_of_difficulty", ""),
        "question": question,
        "question_text": question_text,
        "marks": row.get("marks", ""),
        "answer_explanation": row.get("answer_explanation", ""),
        "answers": answers,
        "sub_questions": sub_questions,
        # Objective has no public keyboard column; the accepted reference
        # fixture records the explicit internal no-keyboard verdict here.
        "math_keyboard": "" if sheet == "Objective" else row.get(
            "math_keyboard", ""
        ),
        "concept_key": row.get("concept_title", ""),
        "group_key": row.get("group_name", ""),
        "flags": [],
    }
    if sheet == "Descriptive":
        candidate["display_answer"] = row.get("display_answer", "")
    return candidate


def _snapshots_by_chapter(parsed) -> dict[str, dict]:
    """One release snapshot per chapter, reconstructed from the gold rows."""
    chapters: dict[str, dict] = {}
    for sheet in aw.SHEET_ORDER:
        for row in parsed["sheets"][sheet]["rows"]:
            chapter_title = _norm(row.get("chapter_title"))
            if not chapter_title:
                continue
            snapshot = chapters.setdefault(chapter_title, {
                "chapter": _fields(row, _CHAPTER_FIELDS),
                "topics": [],
                "groups": [],
                "candidates": [],
                "_topic_index": {},
                "_concept_seen": set(),
                "_group_seen": set(),
            })
            topic_title = _norm(row.get("topic_title"))
            topic = snapshot["_topic_index"].get(topic_title)
            if topic is None:
                topic = {**_fields(row, _TOPIC_FIELDS), "concepts": []}
                snapshot["_topic_index"][topic_title] = topic
                snapshot["topics"].append(topic)
            concept_title = _norm(row.get("concept_title"))
            if concept_title not in snapshot["_concept_seen"]:
                snapshot["_concept_seen"].add(concept_title)
                topic["concepts"].append({
                    **_fields(row, _CONCEPT_FIELDS),
                    "concept_key": concept_title,
                })
            group_name = _norm(row.get("group_name"))
            if group_name and group_name not in snapshot["_group_seen"]:
                snapshot["_group_seen"].add(group_name)
                group = _fields(row, _GROUP_FIELDS)
                snapshot["groups"].append({
                    "group_key": group_name,
                    "concept_key": concept_title,
                    "group_type": group["group_type"],
                    "group_name": group["group_name"],
                    "group_display_name": group["group_display_name"],
                    "semantic_description": group["group_description"],
                    "group_status": group["group_status"],
                    "related_digicards": group["related_digicards"],
                })
            if _norm(row.get("question_label")):
                snapshot["candidates"].append(_candidate_from_row(row, sheet))
    for snapshot in chapters.values():
        for key in ("_topic_index", "_concept_seen", "_group_seen"):
            snapshot.pop(key)
    return chapters


def _rows_for_chapter(parsed, sheet: str, chapter_title: str) -> list[dict]:
    return [
        row for row in parsed["sheets"][sheet]["rows"]
        if _norm(row.get("chapter_title")) == chapter_title
        and _norm(row.get("question_label"))
    ]


def _expected_question_text(sheet: str, gold: dict) -> str:
    """The SOP fill guide (§5.1) and the owner's 2026-08-21 ruling:
    ``question_text`` is the whole question. Objective options are composed
    into that one cell at render time. Descriptive part text remains only in
    its dedicated ``sub_question_N`` columns and is never appended again."""
    text = str(gold.get("question_text") or "")
    if sheet == "Descriptive":
        sub_questions = _sub_questions_from_row(gold)
        if sub_questions:
            return _without_duplicated_subparts(text, sub_questions)
    if sheet == "Objective":
        options = []
        for n in range(1, aw.MAX_OBJECTIVE_OPTIONS + 1):
            content = str(gold.get(f"answer_content_{n}") or "").strip()
            if content:
                answer_type = str(gold.get(f"answer_type_{n}") or "")
                options.append(
                    f"{chr(ord('a') + len(options))}) "
                    f"{katex_rules.rich_answer_display(answer_type, content)}"
                )
        if not options:
            return text
        return (text.rstrip() + "\n" + "\n".join(options)).strip()
    return text


def _occupied_rollups(snapshot: dict) -> dict[str, dict[str, str]]:
    """Derive concept rollups only from groups that own a question."""

    occupied_group_keys = {
        _norm(candidate.get("group_key"))
        for candidate in snapshot["candidates"]
    }
    values: dict[str, dict[str, list[str]]] = {}
    for group in snapshot["groups"]:
        group_key = _norm(group.get("group_key"))
        if group_key not in occupied_group_keys:
            continue
        field = _ROLLUP_FIELD_BY_GROUP_TYPE.get(
            _norm(group.get("group_type"))
        )
        if field is None:
            continue
        concept_key = _norm(group.get("concept_key"))
        visible_name = _norm(group.get("group_name")) or group_key
        values.setdefault(concept_key, {}).setdefault(field, []).append(
            visible_name
        )
    return {
        concept_key: {
            field: ", ".join(fields.get(field, []))
            for field in _ROLLUP_FIELD_BY_GROUP_TYPE.values()
        }
        for concept_key, fields in values.items()
    }


def _omitted_group_shells(snapshot: dict) -> list[str]:
    occupied_group_keys = {
        _norm(candidate.get("group_key"))
        for candidate in snapshot["candidates"]
    }
    return [
        _norm(group.get("group_key"))
        for group in snapshot["groups"]
        if _norm(group.get("group_key")) not in occupied_group_keys
    ]


def _diff_rows(
    sheet: str,
    gold: dict,
    rendered: dict,
    *,
    occupied_rollups: dict[str, dict[str, str]],
) -> list[str]:
    diffs = []
    multipart = (
        sheet == "Descriptive" and bool(_sub_questions_from_row(gold))
    )
    concept_rollups = occupied_rollups.get(
        _norm(gold.get("concept_title")), {}
    )
    for field in aw.FIELDS[sheet]:
        got = _norm(rendered.get(field))
        if field == "question_text":
            want = _norm(_expected_question_text(sheet, gold))
        elif multipart and field == "question":
            want = _norm(_without_duplicated_subparts(
                gold.get("question", ""), _sub_questions_from_row(gold)
            ))
        elif field in _ROLLUP_FIELD_BY_GROUP_TYPE.values():
            want = concept_rollups.get(field, "")
        elif multipart and field.startswith(
            ("answer_type_", "answer_content_", "answer_weightage_")
        ):
            # Current multipart Descriptive rows score exclusively through
            # subquestion marks and keyword rubrics.
            want = ""
        elif field.startswith("answer_content_"):
            number = field.removeprefix("answer_content_")
            want = _norm(katex_rules.raw_answer_cell(
                str(gold.get(f"answer_type_{number}") or ""),
                str(gold.get(field) or ""),
            ))
        elif (
            sheet == "Subjective"
            and field.startswith("answer_")
            and field.removeprefix("answer_").isdigit()
        ):
            number = field.removeprefix("answer_")
            want = _norm(katex_rules.raw_answer_cell(
                str(gold.get(f"answer_type_{number}") or ""),
                str(gold.get(field) or ""),
            ))
        elif field.startswith("sq") and "_keyword_" in field:
            prefix, number = field.split("_keyword_", 1)
            want = _norm(katex_rules.raw_answer_cell(
                str(gold.get(f"{prefix}_answer_type_{number}") or ""),
                str(gold.get(field) or ""),
            ))
        else:
            want = _norm(gold.get(field))
        if got != want:
            diffs.append(
                f"{sheet} {gold.get('question_label')!r} {field}: "
                f"rendered {got!r} != gold {want!r}")
    return diffs


@pytest.mark.parametrize("fixture", FIXTURES)
def test_reference_workbook_round_trips_through_the_renderer(fixture):
    parsed = aw.parse_workbook((FIXTURE_DIR / fixture).read_bytes())
    assert parsed["sheet_order"] == aw.SHEET_ORDER
    for sheet in aw.SHEET_ORDER:
        assert parsed["sheets"][sheet]["fields"] == aw.FIELDS[sheet]
    # The reference program ships no Subjective data rows.
    assert parsed["sheets"]["Subjective"]["rows"] == []

    snapshots = _snapshots_by_chapter(parsed)
    assert snapshots, "no chapters found in the gold workbook"
    all_diffs: list[str] = []
    for chapter_title, snapshot in snapshots.items():
        master, issues = aw.render_master_file(snapshot)
        assert issues["unplaced"] == [], (chapter_title, issues["unplaced"])
        assert issues["omitted_empty_group_shells"] == (
            _omitted_group_shells(snapshot)
        ), (chapter_title, issues["omitted_empty_group_shells"])
        rendered = aw.parse_workbook(master)
        errors = aw.validate_master_file(rendered, snapshot)
        unexpected = [
            error for error in errors
            if "does not start with an allowed functional tag" not in error
        ]
        assert unexpected == [], (chapter_title, errors)
        occupied_rollups = _occupied_rollups(snapshot)
        for sheet in aw.SHEET_ORDER:
            gold_rows = _rows_for_chapter(parsed, sheet, chapter_title)
            rendered_rows = [
                row for row in rendered["sheets"][sheet]["rows"]
                if _norm(row.get("question_label"))
            ]
            assert len(rendered_rows) == len(gold_rows), (
                chapter_title, sheet, len(rendered_rows), len(gold_rows))
            for gold_row, rendered_row in zip(gold_rows, rendered_rows):
                all_diffs.extend(_diff_rows(
                    sheet,
                    gold_row,
                    rendered_row,
                    occupied_rollups=occupied_rollups,
                ))
    if all_diffs:
        pytest.fail(
            f"{fixture}: {len(all_diffs)} field divergence(s):\n"
            + "\n".join(all_diffs[:40]))


@pytest.mark.parametrize("fixture", FIXTURES)
def test_reference_concept_file_projects_from_the_same_snapshots(fixture):
    parsed = aw.parse_workbook((FIXTURE_DIR / fixture).read_bytes())
    for chapter_title, snapshot in _snapshots_by_chapter(parsed).items():
        concepts = aw.parse_workbook(aw.render_concept_file(snapshot))
        errors = aw.validate_concept_file(concepts, snapshot)
        assert errors == [], (chapter_title, errors)


def test_reference_identity_values_survive_the_round_trip():
    """The spec's exact identity examples, spelling variants included."""
    expected = {
        "grade6_mathematics.xlsx": [
            "Three-Dimensional Shapes (06_Mathematics_MSBSHSE_Balbharati)",
            "Lines and Angles (06_Mathematics_MSBSHSE_Balbharati)",
            "In the World of Numbers (06_Mathematics_MSBSHSE_Balbharati)",
        ],
        "grade6_science.xlsx": [
            "Charateristics of Living Organisms (06_Science_MSBSHSE_Balbharti)",
            "Measurement (06_Science_MSBSHSE_Balbharti)",
        ],
        "grade6_english.xlsx": [
            "Self Help is the Only Way (06_English_MSBSHSE_Balbharti)",
            "The School Bell Rings Again... (06_English_MSBSHSE_Balbharti)",
        ],
    }
    for fixture, titles in expected.items():
        parsed = aw.parse_workbook((FIXTURE_DIR / fixture).read_bytes())
        snapshots = _snapshots_by_chapter(parsed)
        for title in titles:
            assert title in snapshots, (fixture, title, list(snapshots))
            master, _ = aw.render_master_file(snapshots[title])
            rendered = aw.parse_workbook(master)
            rendered_titles = {
                _norm(row.get("chapter_title"))
                for sheet in aw.SHEET_ORDER
                for row in rendered["sheets"][sheet]["rows"]
            }
            assert rendered_titles == {title}


def test_reference_wire_values_hold_across_every_gold_question():
    for fixture in FIXTURES:
        parsed = aw.parse_workbook((FIXTURE_DIR / fixture).read_bytes())
        for sheet in aw.SHEET_ORDER:
            for row in parsed["sheets"][sheet]["rows"]:
                if not _norm(row.get("question_label")):
                    continue
                assert _norm(row.get("question_appears_in")) == (
                    "Pre/Post-Worksheet/Test"), row.get("question_label")
                assert _norm(row.get("answer_restriction")) in {
                    "Open", "Specific"}, row.get("question_label")
                assert _norm(row.get("chapter_duration")) == ""
                assert _norm(row.get("question_disclaimer")) == ""

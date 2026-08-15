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

from pathlib import Path

import pytest

from app.bulk_import import assessment_workbook as aw

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
    else:
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


def _candidate_from_row(row, sheet: str) -> dict:
    candidate = {
        "candidate_id": f"GOLD-{_norm(row.get('question_label'))}",
        "question_label": row.get("question_label", ""),
        "sheet_kind": "objective" if sheet == "Objective" else "descriptive",
        "question_category": row.get("question_category", ""),
        "cognitive_skill": row.get("cognitive_skills", ""),
        "question_source": row.get("question_source", ""),
        "question_duration": row.get("question_duration", ""),
        "question_appears_in": row.get("question_appears_in", ""),
        "answer_restriction": row.get("answer_restriction", ""),
        "difficulty": row.get("level_of_difficulty", ""),
        "question": row.get("question", ""),
        "question_text": row.get("question_text", ""),
        "marks": row.get("marks", ""),
        "answer_explanation": row.get("answer_explanation", ""),
        "answers": _answers_from_row(row, sheet),
        "sub_questions": [],
        "concept_key": row.get("concept_title", ""),
        "group_key": row.get("group_name", ""),
        "flags": [],
    }
    if sheet != "Objective":
        candidate["math_keyboard"] = row.get("math_keyboard", "")
        candidate["display_answer"] = row.get("display_answer", "")
        candidate["sub_questions"] = _sub_questions_from_row(row)
    return candidate


def _snapshots_by_chapter(parsed) -> dict[str, dict]:
    """One release snapshot per chapter, reconstructed from the gold rows."""
    chapters: dict[str, dict] = {}
    for sheet in ("Objective", "Descriptive"):
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


def _diff_rows(sheet: str, gold: dict, rendered: dict) -> list[str]:
    diffs = []
    for field in aw.FIELDS[sheet]:
        got, want = _norm(rendered.get(field)), _norm(gold.get(field))
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
        rendered = aw.parse_workbook(master)
        errors = aw.validate_master_file(rendered, snapshot)
        assert errors == [], (chapter_title, errors)
        for sheet in ("Objective", "Descriptive"):
            gold_rows = _rows_for_chapter(parsed, sheet, chapter_title)
            rendered_rows = [
                row for row in rendered["sheets"][sheet]["rows"]
                if _norm(row.get("question_label"))
            ]
            assert len(rendered_rows) == len(gold_rows), (
                chapter_title, sheet, len(rendered_rows), len(gold_rows))
            for gold_row, rendered_row in zip(gold_rows, rendered_rows):
                all_diffs.extend(_diff_rows(sheet, gold_row, rendered_row))
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
                for sheet in ("Objective", "Descriptive")
                for row in rendered["sheets"][sheet]["rows"]
            }
            assert rendered_titles == {title}


def test_reference_wire_values_hold_across_every_gold_question():
    for fixture in FIXTURES:
        parsed = aw.parse_workbook((FIXTURE_DIR / fixture).read_bytes())
        for sheet in ("Objective", "Descriptive"):
            for row in parsed["sheets"][sheet]["rows"]:
                if not _norm(row.get("question_label")):
                    continue
                assert _norm(row.get("question_appears_in")) == (
                    "Pre/Post-Worksheet/Test"), row.get("question_label")
                assert _norm(row.get("answer_restriction")) in {
                    "Open", "Specific"}, row.get("question_label")
                assert _norm(row.get("chapter_duration")) == ""
                assert _norm(row.get("question_disclaimer")) == ""

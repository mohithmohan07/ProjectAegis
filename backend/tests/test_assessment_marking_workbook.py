"""Fail-closed read-back arithmetic for Output-02 marking columns."""
from __future__ import annotations

import copy

import pytest

from app.bulk_import import assessment_workbook as workbook
from app.services import assessment_profile
from app.services import assessment_release as rel
from tests.test_mes_dual_output import _snapshot


def _single_part_descriptive(snapshot: dict) -> None:
    candidate = next(
        row for row in snapshot["candidates"]
        if row["sheet_kind"] == "descriptive"
    )
    candidate["answers"] = [
        {
            "answer_type": "Phrases",
            "answer_content": "[content]: identifies two dimensions",
            "answer_weightage": "2",
        },
        {
            "answer_type": "Phrases",
            "answer_content": "[content]: contrasts three dimensions",
            "answer_weightage": "2",
        },
    ]
    candidate["sub_questions"] = []


def _add_subjective(snapshot: dict) -> None:
    snapshot["candidates"].append({
        "candidate_id": "CAND-3",
        "question_label": "06MSMA_T01_TwoDim Q03",
        "sheet_kind": "subjective",
        "question_category": "Fill in the blanks",
        "cognitive_skill": "Understand",
        "difficulty": "Moderate",
        "marks": 2.0,
        "question_duration": 2.0,
        "math_keyboard": "No",
        "question_appears_in": "Pre/Post-Worksheet/Test",
        "answer_restriction": "Specific",
        "question": "A square is $$a$$ and a cube is $$b$$.",
        "question_text": "A square is $$a$$ and a cube is $$b$$.",
        "display_answer": "",
        "answers": [
            {
                "answer_type": "Phrases",
                "answer_content": "two-dimensional",
                "answer_display": "two-dimensional",
                "answer_weightage": "1",
                "placeholder": "a",
            },
            {
                "answer_type": "Phrases",
                "answer_content": "three-dimensional",
                "answer_display": "three-dimensional",
                "answer_weightage": "1",
                "placeholder": "b",
            },
        ],
        "sub_questions": [],
        "answer_explanation": "",
        "concept_key": "C_A",
        "group_key": "(06MSMA_T01_TwoDim) BG01",
        "flags": [],
    })


def _parsed_master(*, multipart: bool = False, subjective: bool = False):
    snapshot = _snapshot()
    if not multipart:
        _single_part_descriptive(snapshot)
    if subjective:
        _add_subjective(snapshot)
    profile = None
    if subjective:
        profile = assessment_profile.resolve_for_metadata(None, {
            "board": "MSBSHSE",
            "grade": "6",
            "subject": "Mathematics",
        })
    data, issues = workbook.render_master_file(snapshot, profile=profile)
    parsed = workbook.parse_workbook(data)
    assert workbook.validate_master_file(
        parsed,
        snapshot,
        profile=profile,
        group_provenance=issues["group_provenance"],
    ) == []
    return parsed, snapshot, issues["group_provenance"]


def _question_row(parsed: dict, sheet: str) -> dict:
    return next(
        row
        for row in parsed["sheets"][sheet]["rows"]
        if row.get("question_label")
    )


def _clear_descriptive_answers(row: dict) -> None:
    for n in range(1, workbook.MAX_DESCRIPTIVE_ANSWERS + 1):
        row[f"answer_type_{n}"] = ""
        row[f"answer_content_{n}"] = ""
        row[f"answer_weightage_{n}"] = ""


@pytest.mark.parametrize(
    ("candidate_index", "field", "value", "expected"),
    [
        pytest.param(
            0,
            "question_duration",
            None,
            "question_duration must be authored",
            id="objective-duration-missing",
        ),
        pytest.param(
            0,
            "math_keyboard",
            None,
            "objective math_keyboard must be exactly blank",
            id="objective-keyboard-missing",
        ),
        pytest.param(
            1,
            "question_duration",
            float("nan"),
            "question_duration must be authored",
            id="descriptive-duration-nonfinite",
        ),
        pytest.param(
            1,
            "math_keyboard",
            "",
            "descriptive math_keyboard must be exactly Yes or No",
            id="descriptive-keyboard-missing",
        ),
    ],
)
def test_the_duration_and_keyboard_refusal_moved_to_freeze(
    candidate_index: int, field: str, value, expected: str,
) -> None:
    """INVERTED by spec-step8 T7.5/B4 — ``_question_record``'s ``:207``,
    ``:213`` and ``:217``.

    Each already had a staging twin in ``rel.validate_candidate``, so the
    renderer's copy of the decision bought nothing and cost every row on
    every sheet of all four outputs. The renderer now writes the workbook
    and the SAME refusal fires at freeze, where it reaches
    ``diagnostics["payload_errors"]`` and refuses only the database write.
    """
    snapshot = _snapshot()
    snapshot["candidates"][candidate_index][field] = value

    data, _ = workbook.render_master_file(snapshot)
    assert data[:2] == b"PK"

    candidate = dict(snapshot["candidates"][candidate_index])
    candidate.setdefault("blueprint_cell_id", "CELL-1")
    candidate.setdefault("restriction_reason", "bounded")
    candidate.setdefault("source_atom_ids", ["QINV-0001"])
    assert any(
        expected.replace("must be authored", "must be finite") in error
        or expected in error
        for error in rel.validate_candidate(candidate)
    )


@pytest.mark.parametrize(
    ("sheet", "mutate", "expected"),
    [
        pytest.param(
            "Objective",
            lambda row: row.__setitem__("marks", float("nan")),
            "marks must be finite and positive",
            id="objective-marks-nan",
        ),
        pytest.param(
            "Objective",
            lambda row: row.__setitem__("question_duration", float("nan")),
            "question_duration must be finite and positive",
            id="objective-duration-nan-readback",
        ),
        pytest.param(
            "Objective",
            lambda row: row.__setitem__("question_duration", "1e-10000"),
            "question_duration must be finite and positive",
            id="objective-duration-underflow-readback",
        ),
        pytest.param(
            "Objective",
            lambda row: row.__setitem__("question_duration", "1e1000000"),
            "question_duration must be finite and positive",
            id="objective-duration-overflow-readback",
        ),
        pytest.param(
            "Objective",
            lambda row: row.__setitem__("answer_weightage_1", float("nan")),
            "option 1 weight must be finite and numeric",
            id="objective-correct-weight-nan",
        ),
        pytest.param(
            "Objective",
            lambda row: row.__setitem__("answer_weightage_1", float("inf")),
            "option 1 weight must be finite and numeric",
            id="objective-correct-weight-infinity",
        ),
        pytest.param(
            "Objective",
            lambda row: row.__setitem__("answer_weightage_1", -1),
            "correct option 1 weight must be positive",
            id="objective-correct-weight-negative",
        ),
        pytest.param(
            "Objective",
            lambda row: row.__setitem__("answer_weightage_2", -1),
            "wrong option 2 weight must be exact zero",
            id="objective-wrong-weight-negative",
        ),
        pytest.param(
            "Objective",
            lambda row: row.__setitem__("answer_weightage_2", ""),
            "option 2 weight must be finite and numeric",
            id="objective-wrong-weight-blank",
        ),
        pytest.param(
            "Objective",
            lambda row: row.__setitem__("answer_weightage_1", 2),
            "correct weightage 2 != marks 1",
            id="objective-wrong-sum",
        ),
        pytest.param(
            "Objective",
            lambda row: row.__setitem__(
                "question_text",
                str(row.get("question_text") or "").replace(
                    "\na) ", "\nA) ", 1,
                ),
            ),
            "question_text uses uppercase objective option label(s) A)",
            id="objective-uppercase-option-label-readback",
        ),
        pytest.param(
            "Objective",
            lambda row: row.__setitem__(
                "question_text",
                "| Name of peak | Altitude |\n|---|---:|\n| K-2 | 8611 |",
            ),
            "question_text rich-text: unsupported_table",
            id="objective-markdown-table-readback",
        ),
        pytest.param(
            "Objective",
            lambda row: (
                row.__setitem__("answer_type_1", "Equation"),
                row.__setitem__("answer_content_1", "[Katex]x=2[/Katex]"),
            ),
            "option 1 violates declared medium: equation_katex_wrapper",
            id="objective-equation-wrapper-readback",
        ),
        pytest.param(
            "Descriptive",
            lambda row: row.__setitem__("marks", float("nan")),
            "marks must be finite and positive",
            id="descriptive-marks-nan",
        ),
        pytest.param(
            "Descriptive",
            lambda row: row.__setitem__("question_duration", 0),
            "question_duration must be finite and positive",
            id="descriptive-duration-zero-readback",
        ),
        pytest.param(
            "Descriptive",
            lambda row: row.__setitem__("math_keyboard", ""),
            "descriptive math_keyboard must be exactly Yes or No",
            id="descriptive-keyboard-blank-readback",
        ),
        pytest.param(
            "Descriptive",
            lambda row: row.__setitem__("answer_weightage_1", float("nan")),
            "answer/rubric weight 1 must be finite and positive",
            id="descriptive-answer-weight-nan",
        ),
        pytest.param(
            "Descriptive",
            lambda row: row.__setitem__("answer_weightage_1", float("inf")),
            "answer/rubric weight 1 must be finite and positive",
            id="descriptive-answer-weight-infinity",
        ),
        pytest.param(
            "Descriptive",
            lambda row: row.__setitem__(
                "answer_weightage_1", "1e1000000"
            ),
            "answer/rubric weight 1 must be finite and positive",
            id="descriptive-answer-weight-outside-workbook-domain",
        ),
        pytest.param(
            "Descriptive",
            lambda row: row.__setitem__("answer_weightage_1", "+1.5"),
            "answer/rubric weight 1 must be finite and positive",
            id="descriptive-answer-weight-signed-string",
        ),
        pytest.param(
            "Descriptive",
            lambda row: row.__setitem__("answer_weightage_1", 0),
            "answer/rubric weight 1 must be finite and positive",
            id="descriptive-answer-weight-zero",
        ),
        pytest.param(
            "Descriptive",
            lambda row: (
                row.__setitem__("answer_weightage_1", -1),
                row.__setitem__("answer_weightage_2", 5),
            ),
            "answer/rubric weight 1 must be finite and positive",
            id="descriptive-answer-negative-cancellation",
        ),
        pytest.param(
            "Descriptive",
            lambda row: row.__setitem__("answer_weightage_1", 1),
            "answer/rubric weights must sum exactly",
            id="descriptive-answer-wrong-sum",
        ),
        pytest.param(
            "Descriptive",
            lambda row: row.__setitem__("sub_question_marks_1", float("nan")),
            "subquestion 1 marks must be finite and positive",
            id="subquestion-marks-nan",
        ),
        pytest.param(
            "Descriptive",
            lambda row: row.__setitem__(
                "question_text",
                str(row.get("question_text") or "")
                + " Name the number of faces of a cube.",
            ),
            "subquestion 1 text is duplicated in the main question/question_text",
            id="subquestion-duplicated-in-question-text",
        ),
        pytest.param(
            "Descriptive",
            lambda row: (
                row.__setitem__("sub_question_marks_1", -1),
                row.__setitem__("sub_question_marks_2", 5),
            ),
            "subquestion 1 marks must be finite and positive",
            id="subquestion-negative-cancellation",
        ),
        pytest.param(
            "Descriptive",
            lambda row: row.__setitem__("sub_question_marks_1", 1),
            "subquestion marks must sum exactly",
            id="subquestion-wrong-sum",
        ),
        pytest.param(
            "Descriptive",
            lambda row: row.__setitem__("sq1_weightage_1", float("nan")),
            "subquestion 1 keyword 1 weight must be finite and positive",
            id="keyword-weight-nan",
        ),
        pytest.param(
            "Descriptive",
            lambda row: row.__setitem__("sq1_weightage_1", float("inf")),
            "subquestion 1 keyword 1 weight must be finite and positive",
            id="keyword-weight-infinity",
        ),
        pytest.param(
            "Descriptive",
            lambda row: row.__setitem__("sq1_weightage_1", -1),
            "subquestion 1 keyword 1 weight must be finite and positive",
            id="keyword-weight-negative",
        ),
        pytest.param(
            "Descriptive",
            lambda row: row.__setitem__("sq1_weightage_1", 1),
            "subquestion 1 keyword weights must sum exactly",
            id="keyword-wrong-sum",
        ),
        pytest.param(
            "Descriptive",
            _clear_descriptive_answers,
            "descriptive has no answer/rubric blocks",
            id="missing-descriptive-rubric",
        ),
        pytest.param(
            "Descriptive",
            lambda row: (
                row.__setitem__("answer_weightage_1", 4),
                row.__setitem__("answer_type_2", ""),
                row.__setitem__("answer_content_2", ""),
                row.__setitem__("answer_weightage_2", ""),
            ),
            (
                "4-mark single-part descriptive requires at least two "
                "answer/rubric blocks"
            ),
            id="four-mark-single-rubric",
        ),
    ],
)
def test_readback_rejects_invalid_marking_arithmetic(
    sheet: str, mutate, expected: str,
) -> None:
    # Main-answer arithmetic belongs to a single-part Descriptive fixture;
    # part/keyword arithmetic belongs to a separate multipart fixture whose
    # main answer block is empty.  No test row carries both scoring systems.
    multipart = expected.startswith(("subquestion", "keyword"))
    parsed, snapshot, provenance = _parsed_master(multipart=multipart)
    row = _question_row(parsed, sheet)
    mutate(row)

    errors = workbook.validate_master_file(
        parsed,
        copy.deepcopy(snapshot),
        group_provenance=provenance,
    )

    assert any(expected in error for error in errors), errors


def test_single_and_multipart_descriptive_scoring_read_back_exclusively():
    single, _single_snapshot, _single_provenance = _parsed_master()
    single_row = _question_row(single, "Descriptive")
    assert single_row["answer_content_1"].startswith("[content]:")
    assert single_row["answer_weightage_1"] == "2"
    assert single_row["sub_question_1"] == ""

    multipart, _multipart_snapshot, _multipart_provenance = _parsed_master(
        multipart=True,
    )
    multipart_row = _question_row(multipart, "Descriptive")
    assert multipart_row["answer_content_1"] == ""
    assert multipart_row["sub_question_1"] == (
        "Name the number of faces of a cube."
    )
    assert multipart_row["sq1_keyword_1"] == "[content]: six faces"


def test_readback_rejects_multipart_descriptive_main_rubric_duplication():
    parsed, snapshot, provenance = _parsed_master(multipart=True)
    row = _question_row(parsed, "Descriptive")
    row["answer_type_1"] = "Phrases"
    row["answer_content_1"] = "[content]: duplicated shared scoring"
    row["answer_weightage_1"] = 1

    errors = workbook.validate_master_file(
        parsed,
        copy.deepcopy(snapshot),
        group_provenance=provenance,
    )

    assert any(
        "multipart descriptive duplicates scoring" in error
        for error in errors
    ), errors


@pytest.mark.parametrize("multipart", [False, True])
def test_readback_rejects_a_tag_without_rubric_content(multipart: bool):
    parsed, snapshot, provenance = _parsed_master(multipart=multipart)
    row = _question_row(parsed, "Descriptive")
    field = "sq1_keyword_1" if multipart else "answer_content_1"
    row[field] = "[content]:   "

    errors = workbook.validate_master_file(
        parsed,
        copy.deepcopy(snapshot),
        group_provenance=provenance,
    )

    assert any(
        "allowed functional tag" in error for error in errors
    ), errors


def test_subjective_answers_render_and_read_back_without_options():
    parsed, _snapshot_row, _provenance = _parsed_master(subjective=True)
    row = _question_row(parsed, "Subjective")

    assert row["question"] == "A square is $$a$$ and a cube is $$b$$."
    assert row["answer_type_1"] == "Phrases"
    assert row["answer_1"] == "two-dimensional"
    assert row["answer_display_1"] == "two-dimensional"
    assert row["weightage_1"] == "1"
    assert row["placeholder_1"] == "a"
    assert row["answer_2"] == "three-dimensional"
    assert row["placeholder_2"] == "b"
    assert row["math_keyboard"] == "No"
    assert "correct_answer_1" not in row
    assert "sub_question_1" not in row


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        pytest.param(
            lambda row: row.__setitem__("answer_1", ""),
            "subjective answer 1 has no content",
            id="missing-answer",
        ),
        pytest.param(
            lambda row: row.__setitem__("answer_display_1", ""),
            "subjective answer 1 has no answer_display",
            id="missing-answer-display",
        ),
        pytest.param(
            lambda row: row.__setitem__("placeholder_1", "b"),
            "subjective placeholder 1 'b' != 'a'",
            id="out-of-order-placeholder",
        ),
        pytest.param(
            lambda row: row.__setitem__(
                "question", "A square is flat and a cube is $$b$$."
            ),
            "subjective question must contain '$$a$$' exactly once",
            id="missing-placeholder-token",
        ),
        pytest.param(
            lambda row: row.__setitem__("weightage_1", 2),
            "subjective weights must sum exactly",
            id="wrong-weight-sum",
        ),
        pytest.param(
            lambda row: row.__setitem__("math_keyboard", ""),
            "subjective math_keyboard must be exactly Yes or No",
            id="blank-keyboard",
        ),
    ],
)
def test_subjective_readback_fails_closed(mutate, expected):
    parsed, snapshot, provenance = _parsed_master(subjective=True)
    row = _question_row(parsed, "Subjective")
    mutate(row)
    profile = assessment_profile.resolve_for_metadata(None, {
        "board": "MSBSHSE",
        "grade": "6",
        "subject": "Mathematics",
    })

    errors = workbook.validate_master_file(
        parsed,
        copy.deepcopy(snapshot),
        profile=profile,
        group_provenance=provenance,
    )

    assert any(expected in error for error in errors), errors

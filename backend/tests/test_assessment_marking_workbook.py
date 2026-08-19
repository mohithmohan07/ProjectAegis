"""Fail-closed read-back arithmetic for Output-02 marking columns."""
from __future__ import annotations

import copy

import pytest

from app.bulk_import import assessment_workbook as workbook
from app.services import assessment_release as rel
from tests.test_mes_dual_output import _snapshot


def _parsed_master():
    snapshot = _snapshot()
    data, issues = workbook.render_master_file(snapshot)
    parsed = workbook.parse_workbook(data)
    assert workbook.validate_master_file(
        parsed,
        snapshot,
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
    ],
)
def test_readback_rejects_invalid_marking_arithmetic(
    sheet: str, mutate, expected: str,
) -> None:
    parsed, snapshot, provenance = _parsed_master()
    row = _question_row(parsed, sheet)
    mutate(row)

    errors = workbook.validate_master_file(
        parsed,
        copy.deepcopy(snapshot),
        group_provenance=provenance,
    )

    assert any(expected in error for error in errors), errors

"""Regression coverage for the SOP's model-authored Objective MSQ mode."""
from __future__ import annotations

from decimal import Decimal

from app import bulk_import as bi
from app.bulk_import import assessment_workbook as workbook
from app.services import assessment_marking as marking
from app.services import assessment_materialization as materialization
from app.services import assessment_cells as cells
from app.services import assessment_profile
from app.services import assessment_release as release
from app.services.phase3 import kernel


def _cell() -> dict:
    return {
        "cell_id": "CELL-MSQ",
        "sheet_kind": "objective",
        "question_category": "Multiple Choice Question",
        "cognitive_skill": "Understand",
        "difficulty": "Moderate",
        "marks": 2,
        "count": 1,
        "appears_in": ["Pre/Post-Worksheet/Test"],
        "source_policy": "reuse",
        "selection_mode": "multiple",
    }


def _answers(with_weights: bool = True) -> list[dict]:
    values = [
        ("Cube", "1", 1),
        ("Sphere", "1", 1),
        ("Circle", "0", 0),
    ]
    return [
        {
            "answer_type": "Phrases",
            "answer_content": content,
            "correct_answer": marker,
            **({"answer_weightage": weight} if with_weights else {}),
        }
        for content, marker, weight in values
    ]


def _proposal() -> dict:
    return {
        "candidate_id": "CAND-MSQ",
        "question": "Which shapes are solids?",
        "display_answer": "",
        "answer_explanation": "Cube and Sphere are solids; Circle is flat.",
        "answers": _answers(with_weights=False),
        "sub_questions": [],
        "requires_visual": False,
        "rationale": "The source supplies three options and two are correct.",
    }


def _candidate() -> dict:
    return {
        "candidate_id": "CAND-MSQ",
        "source_atom_ids": ["Q-MSQ"],
        "source_policy": "reuse",
        "blueprint_cell_id": "CELL-MSQ",
        "question": "Which shapes are solids?",
        "question_text": "Which shapes are solids?",
        "sheet_kind": "objective",
        "question_category": "Multiple Choice Question",
        "cognitive_skill": "Understand",
        "difficulty": "Moderate",
        "marks": 2,
        "question_duration": 2,
        "math_keyboard": "",
        "answer_restriction": "Specific",
        "restriction_reason": "The supplied option set bounds the response.",
        "selection_mode": "multiple",
        "answers": _answers(),
        "sub_questions": [],
        "answer_explanation": "Cube and Sphere are solids; Circle is flat.",
    }


def test_cell_verdict_carries_the_authored_objective_selection_mode():
    profile = {
        **assessment_profile.DEFAULT_PROFILE,
        "name": "msq-regression",
        "appears_in": "Pre/Post-Worksheet/Test",
        "sheet_kinds": ("objective", "descriptive"),
    }
    source = {
        "source_qid": "Q-MSQ",
        "raw_text": "Which shapes are solids?",
        "options": ["Cube", "Sphere", "Circle"],
    }
    result = cells.decide_cells(
        [source],
        meta={"subject": "Mathematics", "grade": "6"},
        profile=profile,
        envelope_sha256="m" * 64,
        provider=lambda request: {
            "source_qid": request["source_atom"]["source_qid"],
            "sheet_kind": "objective",
            "question_category": "Multiple Choice Question",
            "cognitive_skill": "Understand",
            "difficulty": "Moderate",
            "marks": 1,
            "selection_mode": "multiple",
            "rationale": "The source supplies a multiple-correct option set.",
        },
        store=kernel.DecisionStore(),
    )
    assert result[0]["selection_mode"] == "multiple"


def test_materialization_preserves_source_option_cardinality_for_msq():
    defects = materialization._proposal_defects(
        _proposal(),
        _cell(),
        "CAND-MSQ",
        atom={
            "source_qid": "Q-MSQ",
            "options": ["Cube", "Sphere", "Circle"],
        },
    )
    assert defects == []

    dropped = _proposal()
    dropped["answers"] = dropped["answers"][:2]
    defects = materialization._proposal_defects(
        dropped,
        _cell(),
        "CAND-MSQ",
        atom={
            "source_qid": "Q-MSQ",
            "options": ["Cube", "Sphere", "Circle"],
        },
    )
    assert any("option cardinality must preserve the source" in defect for defect in defects)


def test_release_and_marking_accept_multiple_correct_scores_that_sum_to_marks():
    candidate = _candidate()
    assert release.validate_candidate(
        candidate, assessment_profile.DEFAULT_PROFILE,
    ) == []

    legacy = dict(candidate)
    legacy.pop("selection_mode")
    legacy_errors = release.validate_candidate(
        legacy, assessment_profile.DEFAULT_PROFILE,
    )
    assert any("exactly one correct option" in error for error in legacy_errors)

    response = {
        "candidate_id": candidate["candidate_id"],
        "question": candidate["question"],
        "question_text": candidate["question_text"],
        "answers": _answers(),
        "sub_questions": [],
        "question_duration": 2,
        "duration_basis_count": None,
        "math_keyboard": "",
        "rationale": "Two supplied options independently earn one mark each.",
    }
    checker = marking._checker(
        candidate,
        candidate_id="CAND-MSQ",
        kind="objective",
        total_marks=Decimal(2),
        duration_rule={
            "mode": "matrix",
            "minutes_by_difficulty": {"Moderate": 2},
        },
        marks_rule={},
    )
    assert checker(response) == []


def test_workbook_readback_accepts_multiple_yes_options_and_exact_sum():
    row = {
        "question_text": "Which shapes are solids?\na) Cube\nb) Sphere\nc) Circle",
    }
    for index, answer in enumerate(_answers(), start=1):
        row[f"answer_type_{index}"] = bi.wire_answer_type(
            answer["answer_type"], "objective",
        )
        row[f"answer_content_{index}"] = answer["answer_content"]
        row[f"correct_answer_{index}"] = (
            "Yes" if answer["correct_answer"] == "1" else "No"
        )
        row[f"answer_weightage_{index}"] = answer["answer_weightage"]

    assert workbook._objective_marking_errors(
        row,
        label="Q-MSQ",
        marks=Decimal(2),
        selection_mode="multiple",
    ) == []

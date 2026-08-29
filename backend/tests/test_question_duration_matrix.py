"""The owner's Question Duration Matrix (uploaded 2026-08-29).

Board-wide MSBSHSE format policies per subject — Math+Physics, English,
Social Science — carry the matrix's exact categories and minutes. Scope
rulings recorded the same day: the matrix binds every MH Board grade;
grade-scoped policies are listed FIRST and win, so Class 6 keeps its
audited closed sets (no Case Based / Assertion & Reasons) and Mathematics
keeps Match the Following / True or False / Fill in the blanks at one
minute per sub-point even though the matrix sheet does not list them.
"""
from __future__ import annotations

import copy
import json

import pytest

from app.services import assessment_marking as marking
from app.services import assessment_profile as profiles
from app.services.phase3 import kernel
from tests.test_assessment_marking import (
    ENVELOPE_SHA256,
    _candidate,
    _cell,
    _valid_response,
)


def _policy(metadata: dict) -> dict:
    return profiles.assessment_format_policy(None, metadata)


def _meta(subject: str, grade: str) -> dict:
    return {
        "subject": subject,
        "board": "MSBSHSE",
        "grade": grade,
        "chapter_title": "Motion",
    }


# --------------------------------------------------------------------------- #
# Policy selection and layering
# --------------------------------------------------------------------------- #

def test_class_6_keeps_its_audited_sets_over_the_board_wide_matrix():
    maths = _policy(_meta("Mathematics", "6"))
    assert maths["policy_id"] == "msbshse-grade-6-mathematics-2026-08-27"
    descriptive = maths["formats_by_sheet"]["descriptive"]
    assert "Case Based Questions" not in descriptive
    objective = maths["formats_by_sheet"]["objective"]
    assert "Assertion & Reasons Type" not in objective
    # The owner kept the per-subpoint trio for Mathematics.
    assert {
        "Match the Following", "True or False", "Fill in the blanks",
    } <= set(objective)

    english = _policy(_meta("English", "6"))
    assert english["policy_id"] == "msbshse-grade-6-english-2026-08-29"
    assert "Reading Comprehension" not in (
        english["formats_by_sheet"]["descriptive"]
    )


def test_other_grades_get_the_matrix_policy_per_subject():
    physics = _policy(_meta("Physics", "8"))
    assert physics["policy_id"] == "msbshse-mathematics-physics-2026-08-29"
    assert "Assertion & Reasons Type" in physics["formats_by_sheet"]["objective"]
    assert "Case Based Questions" in physics["formats_by_sheet"]["descriptive"]

    maths_9 = _policy(_meta("Maths", "9"))
    assert maths_9["policy_id"] == "msbshse-mathematics-physics-2026-08-29"

    english_8 = _policy(_meta("English", "8"))
    assert english_8["policy_id"] == "msbshse-english-2026-08-29"
    assert {
        "Sentence Transformation", "Error Correction",
        "Extract Based Question", "Reading Comprehension",
        "Long Answer Type (6 Marks)",
    } <= set(english_8["formats_by_sheet"]["descriptive"])

    history_9 = _policy(_meta("History", "9"))
    assert history_9["policy_id"] == "msbshse-social-science-2026-08-29"
    assert {
        "Extract based on Map Survey", "Locating and Plotting on Map",
    } <= set(history_9["formats_by_sheet"]["descriptive"])


def test_other_boards_keep_the_generic_vocabulary():
    policy = _policy({
        "subject": "Physics", "board": "CBSE", "grade": "8",
        "chapter_title": "Motion",
    })
    assert policy.get("policy_id") == "generic-cms"


# --------------------------------------------------------------------------- #
# Duration resolution (matrix and marks_matrix)
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    ("subject", "grade", "kind", "category", "marks", "difficulty", "minutes"),
    [
        # Matrix rows shared by every subject sheet.
        ("Physics", "8", "descriptive", "Very Short Answer Questions",
         1, "High", 2.0),
        ("Physics", "8", "descriptive", "Case Based Questions",
         4, "Moderate", 5.0),
        ("Physics", "8", "objective", "Assertion & Reasons Type",
         1, "High", 1.0),
        ("Mathematics", "8", "descriptive", "Long Answer Type (5 Marks)",
         5, "Moderate", 7.0),
        # Marks-dependent tiers.
        ("English", "8", "descriptive", "Composition Writing",
         5, "Less", 7.0),
        ("English", "8", "descriptive", "Composition Writing",
         10, "Moderate", 10.0),
        ("English", "8", "descriptive", "Composition Writing",
         20, "High", 20.0),
        ("English", "8", "descriptive", "Reading Comprehension",
         10, "High", 16.0),
        ("English", "8", "descriptive", "Extract Based Question",
         16, "Less", 15.0),
        ("English", "8", "subjective", "Fill in the Blanks",
         4, "Moderate", 5.0),
        ("History", "9", "descriptive", "Locating and Plotting on Map",
         3, "High", 4.0),
        ("History", "9", "descriptive", "Locating and Plotting on Map",
         10, "Less", 9.0),
        ("History", "9", "descriptive", "Extract based on Map Survey",
         10, "High", 10.0),
        # Grade 6 English now carries the matrix contract too.
        ("English", "6", "descriptive", "Short Answer Type (3 Marks)",
         3, "High", 6.0),
        ("English", "6", "descriptive", "Composition Writing",
         10, "Moderate", 10.0),
    ],
)
def test_matrix_minutes_resolve_exactly(
    subject, grade, kind, category, marks, difficulty, minutes,
):
    assert profiles.question_duration_minutes(
        None,
        _meta(subject, grade),
        sheet_kind=kind,
        question_category=category,
        difficulty=difficulty,
        marks=marks,
    ) == minutes


def test_marks_matrix_requires_marks_and_survives_json_string_keys():
    meta = _meta("English", "8")
    # Without the mark tier there is no honest answer.
    assert profiles.question_duration_minutes(
        None, meta,
        sheet_kind="descriptive",
        question_category="Composition Writing",
        difficulty="Less",
    ) is None
    # A policy that crossed a JSON boundary keys its tiers by string; the
    # same table must resolve identically.
    policy = json.loads(json.dumps(_policy(meta)))
    rule = policy["formats_by_sheet"]["descriptive"]["Composition Writing"]
    assert set(rule["duration"]["minutes_by_marks"]) == {"5", "10", "20"}
    profile = {
        **profiles.DEFAULT_PROFILE,
        "assessment_format": policy,
        "assessment_format_overrides": (),
    }
    assert profiles.question_duration_minutes(
        profile,
        meta,
        sheet_kind="descriptive",
        question_category="Composition Writing",
        difficulty="High",
        marks=20,
    ) == 20.0


# --------------------------------------------------------------------------- #
# The marking checker enforces marks_matrix durations
# --------------------------------------------------------------------------- #

_ENGLISH_8_META = {
    "subject": "English",
    "board": "MSBSHSE",
    "grade": "8",
    "chapter_title": "The Brook",
}


def test_marks_matrix_duration_is_enforced_end_to_end(monkeypatch):
    monkeypatch.setattr(marking.config, "phase3_decision_workers", lambda: 1)
    candidate = _candidate(
        "CAND-COMP",
        cell_id="CELL-COMP",
        marks=5,
        category="Composition Writing",
        difficulty="Less",
    )
    cell = _cell(
        "CELL-COMP",
        marks=5,
        category="Composition Writing",
        difficulty="Less",
    )

    def provider(request: dict) -> dict:
        response = _valid_response(request)
        response["question_duration"] = 7
        response["duration_basis_count"] = None
        return response

    verdict = marking.decide_markings(
        [(candidate, cell)],
        meta=_ENGLISH_8_META,
        envelope_sha256=ENVELOPE_SHA256,
        provider=provider,
        store=kernel.DecisionStore(),
    )[0]
    assert verdict["question_duration"] == 7.0
    assert verdict["duration_basis_count"] is None


def test_marks_matrix_rejects_a_wrong_tier_value(monkeypatch):
    monkeypatch.setattr(marking.config, "phase3_decision_workers", lambda: 1)
    candidate = _candidate(
        "CAND-COMP",
        cell_id="CELL-COMP",
        marks=5,
        category="Composition Writing",
        difficulty="Less",
    )
    cell = _cell(
        "CELL-COMP",
        marks=5,
        category="Composition Writing",
        difficulty="Less",
    )

    def wrong(request: dict) -> dict:
        response = _valid_response(request)
        # The 10-mark tier's minutes on a 5-mark composition.
        response["question_duration"] = 10
        return response

    with pytest.raises(kernel.ContractError) as exc_info:
        marking.decide_markings(
            [(candidate, cell)],
            meta=_ENGLISH_8_META,
            envelope_sha256=ENVELOPE_SHA256,
            provider=wrong,
            store=kernel.DecisionStore(),
        )
    assert any(
        "active profile contract (7 minutes)" in defect
        for defect in exc_info.value.defects
    )


def test_marks_matrix_forbids_a_basis_count(monkeypatch):
    monkeypatch.setattr(marking.config, "phase3_decision_workers", lambda: 1)
    candidate = _candidate(
        "CAND-COMP",
        cell_id="CELL-COMP",
        marks=5,
        category="Composition Writing",
        difficulty="Less",
    )
    cell = _cell(
        "CELL-COMP",
        marks=5,
        category="Composition Writing",
        difficulty="Less",
    )

    def with_basis(request: dict) -> dict:
        response = _valid_response(request)
        response["question_duration"] = 7
        response["duration_basis_count"] = 5
        return response

    with pytest.raises(kernel.ContractError) as exc_info:
        marking.decide_markings(
            [(candidate, cell)],
            meta=_ENGLISH_8_META,
            envelope_sha256=ENVELOPE_SHA256,
            provider=with_basis,
            store=kernel.DecisionStore(),
        )
    assert any(
        "marks-matrix" in defect for defect in exc_info.value.defects
    )

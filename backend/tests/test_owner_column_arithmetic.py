"""Half-mark awards must survive every exact marking arithmetic gate."""
from __future__ import annotations

from decimal import Decimal, localcontext

import pytest

from app.bulk_import import assessment_workbook as workbook
from app.services import assessment_marking as marking
from app.services import assessment_profile as profiles
from app.services import assessment_release as release
from app.services import column_spec
from tests.test_assessment_master_refiner import _payload


def _candidate(scoring: str, large_weight: str = "1E+50") -> dict:
    candidate = _payload()["candidates"][1]
    candidate["marks"] = large_weight
    candidate["answers"] = []
    candidate["sub_questions"] = []
    if scoring == "main":
        candidate["answers"] = [
            {"answer_type": "Phrases", "answer_content": "two dimensions",
             "answer_weightage": large_weight},
            {"answer_type": "Phrases", "answer_content": "length and breadth",
             "answer_weightage": "0.5"},
        ]
    elif scoring == "keyword":
        candidate["sub_questions"] = [{
            "text": "a) State the dimensions of a square.",
            "marks": large_weight,
            "keywords": [
                {"answer_type": "Phrases", "keyword": "two dimensions",
                 "weightage": large_weight},
                {"answer_type": "Phrases", "keyword": "length and breadth",
                 "weightage": "0.5"},
            ],
        }]
    else:
        for label, weight in (("a", large_weight), ("b", "0.5")):
            candidate["sub_questions"].append({
                "text": f"{label}) State the dimensions.", "marks": weight,
                "keywords": [{"answer_type": "Phrases",
                              "keyword": "dimensions", "weightage": weight}],
            })
    return candidate


def _gate_errors(candidate: dict) -> list[list[str]]:
    profile = profiles.resolve_for_metadata(None, {"subject": "Mathematics"})
    marks = Decimal(candidate["marks"])
    return [
        marking._weight_defects(
            candidate, kind="descriptive", total_marks=marks,
            marks_rule={}, half_step=True,
        ),
        release.validate_candidate(candidate, profile),
        workbook._descriptive_marking_errors(
            workbook._question_record(candidate, "Descriptive", profile),
            label="arithmetic regression", marks=marks, tags_required=False,
            column_policy=column_spec.from_profile(profile),
        ),
    ]


def test_exact_weight_sum_preserves_small_awards_without_changing_context():
    with localcontext() as context:
        context.prec = 2
        result = release.exact_weight_sum([Decimal("1E50"), Decimal("0.5")])
        assert result == Decimal("1" + "0" * 50 + ".5")
        assert context.prec == 2
        assert release.exact_weight_sum([Decimal("2.5"), Decimal("1.5")]) == 4
        assert release.exact_weight_sum([]) == 0


@pytest.mark.parametrize("scoring", ["main", "keyword", "children"])
def test_half_mark_cannot_disappear_at_author_freeze_or_readback(scoring):
    for errors in _gate_errors(_candidate(scoring)):
        assert any("sum" in error for error in errors), errors


def test_valid_half_mark_allocation_passes_all_three_gates():
    candidate = _candidate("main", "3.5")
    candidate["marks"] = "4"
    assert _gate_errors(candidate) == [[], [], []]

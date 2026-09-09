"""Parent and child rubric views retain one non-additive scoring contract."""
from __future__ import annotations

import copy
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.bulk_import import assessment_workbook as workbook
from app.services import column_spec
from app.services import assessment_release_service as release_service
from tests.test_owner_column_spec import _english_snapshot, _profile


def _render(snapshot, profile):
    data, manifest = workbook.render_master_file(snapshot, profile)
    parsed = workbook.parse_workbook(data)
    return parsed, manifest, parsed["sheets"]["Descriptive"]["rows"][0]


def _errors(parsed, manifest, snapshot, profile):
    return workbook.validate_master_file(
        parsed, snapshot, profile,
        group_provenance=manifest["group_provenance"],
    )


def test_parent_is_exact_ordered_child_projection_without_candidate_mutation():
    snapshot = _english_snapshot()
    candidate = snapshot["candidates"][1]
    candidate["sub_questions"][0]["keywords"][0] = {
        "answer_type": "Equation", "keyword": r"2^3", "weightage": "1",
    }
    before = copy.deepcopy(snapshot)
    profile = _profile()
    parsed, manifest, row = _render(snapshot, profile)
    assert _errors(parsed, manifest, snapshot, profile) == []
    assert snapshot == before
    assert candidate["answers"] == []
    expected = workbook.multipart_parent_answers(candidate["sub_questions"])
    for number, criterion in enumerate(expected, start=1):
        assert row[f"answer_type_{number}"] == criterion["answer_type"]
        assert row[f"answer_content_{number}"] == criterion["answer_content"]
        assert Decimal(str(row[f"answer_weightage_{number}"])) == Decimal(criterion["answer_weightage"])
    assert sum(row[f"answer_weightage_{n}"] for n in range(1, 5)) == row["marks"]
    assert row["sub_question_marks_1"] + row["sub_question_marks_2"] == row["marks"]


@pytest.mark.parametrize("field,value", [
    ("answer_content_1", "[content]: Unrelated parent criterion."),
    ("answer_weightage_1", 0.5),
    ("answer_type_1", "Equation"),
])
def test_readback_rejects_parent_child_content_type_or_weight_mismatch(field, value):
    snapshot = _english_snapshot()
    profile = _profile()
    parsed, manifest, row = _render(snapshot, profile)
    row[field] = value
    assert any("multipart parent criterion" in error for error in _errors(
        parsed, manifest, snapshot, profile,
    ))


def test_legacy_frozen_release_remains_child_only():
    snapshot = _english_snapshot()
    profile = _profile()
    profile.pop(column_spec.POLICY_KEY)
    snapshot["candidates"][0]["answer_explanation"] = snapshot["candidates"][0]["answer_explanation"].removeprefix("a) ")
    parsed, manifest, row = _render(snapshot, profile)
    assert not row["answer_content_1"]
    assert row["sq1_keyword_1"]
    assert _errors(parsed, manifest, snapshot, profile) == []


def test_parent_capacity_overflow_blocks_readback_and_preserves_every_child():
    snapshot = _english_snapshot()
    candidate = snapshot["candidates"][1]
    candidate["marks"] = 31
    candidate["sub_questions"] = [
        {
            "text": f"{chr(97 + part)}) State detail {part + 1}.",
            "marks": count,
            "keywords": [
                {"answer_type": "Phrases", "keyword": f"[content]: Detail {part + 1}.{criterion + 1}.", "weightage": 1}
                for criterion in range(count)
            ],
        }
        for part, count in enumerate([6, 6, 6, 6, 6, 1])
    ]
    original = copy.deepcopy(snapshot)
    result = workbook.build_dual_output(snapshot, _profile())
    assert result["valid"] is False
    manifest = result["manifest"]
    assert any("parent projection capacity 30 cannot hold 31" in error for error in manifest["read_back"]["master_errors"])
    assert release_service._readiness(
        SimpleNamespace(diagnostics={}, payload=snapshot), manifest,
    ) == release_service.BLOCKED
    contract = manifest["workbook_contracts"]["master_xlsx"]
    assert contract["descriptive_answer_slots"] == 30
    assert contract["required_descriptive_answer_slots"] == 31
    finding = next(item for item in manifest["issues"]["truncated_rows"] if item.get("reason") == "parent_projection_capacity")
    assert len(finding["omitted_projection"]) == 31
    row = workbook.parse_workbook(result["master_xlsx"])["sheets"]["Descriptive"]["rows"][0]
    assert not row["answer_content_1"]
    assert row["sq6_keyword_1"] == "[content]: Detail 6.1."
    assert snapshot == original

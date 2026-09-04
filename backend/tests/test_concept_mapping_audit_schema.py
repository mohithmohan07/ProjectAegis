"""Exact workbook geometry recorded by the 2026-08-27 mapping audit.

Master Governing Contract v2.0 (§12/§14) made the update-aware 72/380/149
schema universal — every output of every board, Concept files included — so
the geometry pinned here is the contract's, not a Grade-6 board fact.
"""
from __future__ import annotations

import io

import openpyxl
import pytest

from app.bulk_import import assessment_workbook as workbook
from app.bulk_import import layouts
from app.services import assessment_profile


UPDATE_FIELDS = (
    "is_update_chapter",
    "is_update_topic",
    "is_update_concept",
    "is_update_group",
    "is_update_question",
)
# Contract v2.0 §18: the publication every candidate carries as its source.
PUBLICATION = "Balbharati"


def _profile(subject: str) -> dict:
    return assessment_profile.resolve_for_metadata(None, {
        "board": "MSBSHSE",
        "grade": "06",
        "subject": subject,
    })


def _snapshot(
    phase: str,
    *,
    candidates: list[dict] | None = None,
    groups: list[dict] | None = None,
) -> dict:
    return {
        "chapter": {
            "chapter_title": "Audit Chapter (06_Subject_MSBSHSE_Balbharati)",
            "chapter_display_name": "Audit Chapter",
            "chapter_duration": "362",
            "pre_topics": "",
            "post_topics": "",
            "chapter_description": "Audit description.",
        },
        "source_book": PUBLICATION,
        "topics": [{
            "topic_title": "Audit Topic",
            "topic_display_name": "Audit Topic",
            "pre_post_learning": phase,
            "topic_concept_labels": "Audit Concept (AUDIT_C01)",
            "related_topics": "",
            "topic_description": "Audit topic description.",
            "concepts": [{
                "concept_key": "concept-1",
                "concept_machine_id": "AUDIT_C01",
                "concept_title": "Audit Concept",
                "concept_display_name": "Audit Concept",
                "concept_details": (
                    "Description: Audit concept.\nAchieving Mastery: "
                    "Explain the audit concept."
                ),
                "keywords": "audit",
                "digicards": "",
                "related_concepts": "",
                "concept_source": "Balbharati",
            }],
        }],
        "groups": list(groups or []),
        "candidates": list(candidates or []),
    }


def _group() -> dict:
    return {
        "group_key": "AUDIT_C01 BG01",
        "concept_key": "concept-1",
        "group_name": "AUDIT_C01 BG01",
        "group_display_name": "AUDIT_C01 BG01",
        "semantic_description": "Audit question group.",
        "group_status": "Active",
        "group_type": "Basic",
    }


def _objective(label: str) -> dict:
    return {
        "candidate_id": f"candidate-{label}",
        "question_label": label,
        "sheet_kind": "objective",
        "question_category": "Multiple Choice Question",
        "cognitive_skill": "Understand",
        "question_source": PUBLICATION,
        "question_disclaimer": "",
        "question_duration": 1,
        "question_appears_in": "Pre/Post-Worksheet/Test",
        "answer_restriction": "Specific",
        "difficulty": "Less",
        "question": f"Choose the correct audit option for {label}.",
        "question_text": f"Choose the correct audit option for {label}.",
        "marks": 1,
        # Contract §22.5: the explanation opens with the exact correct
        # option text, never an option letter or number.
        "answer_explanation": (
            "correct option is the answer because it satisfies the audit."
        ),
        "answers": [
            {
                "answer_type": "Phrases",
                "answer_content": "correct option",
                "correct_answer": "Yes",
                "answer_weightage": 1,
            },
            {
                "answer_type": "Phrases",
                "answer_content": "wrong option",
                "correct_answer": "No",
                "answer_weightage": 0,
            },
        ],
        "sub_questions": [],
        "concept_key": "concept-1",
        "group_key": "AUDIT_C01 BG01",
        "flags": [],
    }


def _descriptive_with_thirty_answers() -> dict:
    return {
        "candidate_id": "candidate-desc-30",
        "question_label": "AUDIT_C01 Q30",
        "sheet_kind": "descriptive",
        "question_category": "Long Answer",
        "cognitive_skill": "Apply",
        "question_source": PUBLICATION,
        "question_disclaimer": "",
        "question_duration": 7,
        "question_appears_in": "Pre/Post-Worksheet/Test",
        "answer_restriction": "Specific",
        "difficulty": "High",
        "question": "Write a complete audit response.",
        "question_text": "Write a complete audit response.",
        "marks": 30,
        "math_keyboard": "No",
        # Contract §24: display_answer and answer_explanation are the same
        # complete model answer; each criterion is a registry-tagged 1-mark
        # English rubric line (§27.5/§28).
        "display_answer": "A complete response addresses all thirty criteria.",
        "answer_explanation": (
            "A complete response addresses all thirty criteria."
        ),
        "answers": [
            {
                "answer_type": "Phrases",
                "answer_content": f"[content]: criterion {number}",
                "answer_weightage": 1,
            }
            for number in range(1, 31)
        ],
        "sub_questions": [],
        "concept_key": "concept-1",
        "group_key": "AUDIT_C01 BG01",
        "flags": [],
    }


def test_concept_role_shares_the_update_aware_schema() -> None:
    """Contract v2.0 §12/§14 retires the committed 67/374/144 Concept
    geometry: the Concept role renders the Master's update-aware schema so
    all four outputs are byte-consistent in their identity/update columns."""

    profile = _profile("Mathematics")
    snapshot = _snapshot("Post")

    schema = workbook.output_schema("concept", profile, snapshot)
    assert schema["contract_id"] == "concept-update-aware-1"
    assert [
        len(schema["fields"][sheet]) for sheet in workbook.SHEET_ORDER
    ] == [72, 380, 149]
    for sheet, fields in schema["fields"].items():
        assert len(fields) == len(set(fields)), sheet
        assert [
            field for field in fields if field.startswith("is_update_")
        ] == list(UPDATE_FIELDS)
    assert "concept_source" in schema["fields"]["Descriptive"]
    assert schema["fields"] == workbook.output_schema(
        "master", None, snapshot
    )["fields"]

    rendered = workbook.parse_workbook(
        workbook.render_concept_file(snapshot, profile)
    )
    assert [
        len(rendered["sheets"][sheet]["fields"])
        for sheet in workbook.SHEET_ORDER
    ] == [72, 380, 149]
    rows = rendered["sheets"]["Objective"]["rows"]
    assert len(rows) == 1
    assert [rows[0][field] for field in UPDATE_FIELDS] == ["No"] * 5
    assert rendered["sheets"]["Descriptive"]["rows"] == []
    assert rendered["sheets"]["Subjective"]["rows"] == []


def test_msbshse_grade6_master_has_one_clean_update_schema() -> None:
    profile = _profile("Mathematics")
    snapshot = _snapshot("Post")
    schema = workbook.output_schema("master", profile, snapshot)

    assert [
        len(schema["fields"][sheet]) for sheet in workbook.SHEET_ORDER
    ] == [72, 380, 149]
    for sheet, fields in schema["fields"].items():
        assert len(fields) == len(set(fields)), sheet
        assert [
            field for field in fields if field.startswith("is_update_")
        ] == [
            "is_update_chapter",
            "is_update_topic",
            "is_update_concept",
            "is_update_group",
            "is_update_question",
        ]
    assert "concept_source" in schema["fields"]["Descriptive"]

    data, _issues = workbook.render_master_file(snapshot, profile)
    parsed = workbook.parse_workbook(data)
    assert [
        len(parsed["sheets"][sheet]["fields"])
        for sheet in workbook.SHEET_ORDER
    ] == [72, 380, 149]

    raw = openpyxl.load_workbook(io.BytesIO(data), data_only=False)
    try:
        assert raw.sheetnames == ["Objective", "Descriptive", "Subjective"]
        assert {str(value) for value in raw["Descriptive"].merged_cells.ranges} == {
            "A1:G1", "H1:N1", "O1:Z1", "AA1:AH1", "AI1:BX1",
        }
    finally:
        raw.close()

    built = workbook.build_dual_output(snapshot, profile)
    assert built["valid"] is True
    # Contract v2.0 §14: both projections share the update-aware layout;
    # the ``MSBSHSE_GRADE_6_*`` layout names are aliases of it.
    assert layouts.MSBSHSE_GRADE_6_MASTER_LAYOUT_ID == (
        layouts.UPDATE_AWARE_MASTER_LAYOUT_ID
    )
    assert built["manifest"]["workbook_contracts"] == {
        "concepts_xlsx": {
            "layout_id": "update-aware-master-1",
            "contract_id": "concept-update-aware-1",
            "field_counts": {
                "Objective": 72, "Descriptive": 380, "Subjective": 149,
            },
        },
        "master_xlsx": {
            "layout_id": "update-aware-master-1",
            "contract_id": "msbshse-grade-6-master-2026-08-27",
            "field_counts": {
                "Objective": 72, "Descriptive": 380, "Subjective": 149,
            },
            "descriptive_answer_slots": 10,
        },
    }


@pytest.mark.parametrize(
    ("subject", "profile_factory", "master_layout_id", "contract_id"),
    [
        # Contract v2.0 §14: an unresolved profile renders the same
        # update-aware geometry as every board; only the frozen English
        # Post profile widens the Descriptive rubric capacity.
        pytest.param(
            "Default",
            None,
            layouts.UPDATE_AWARE_MASTER_LAYOUT_ID,
            "update-aware-master-1",
            id="default",
        ),
        pytest.param(
            "Mathematics",
            _profile,
            layouts.UPDATE_AWARE_MASTER_LAYOUT_ID,
            "msbshse-grade-6-master-2026-08-27",
            id="mathematics",
        ),
        pytest.param(
            "English",
            _profile,
            layouts.UPDATE_AWARE_EXPANDED_DESCRIPTIVE_LAYOUT_ID,
            "english-post-master-expanded-1",
            id="english-post",
        ),
    ],
)
def test_manifest_records_each_projection_layout_and_contract(
    subject, profile_factory, master_layout_id, contract_id,
) -> None:
    profile = profile_factory(subject) if profile_factory else None
    contracts = workbook.build_dual_output(
        _snapshot("Post"), profile,
    )["manifest"]["workbook_contracts"]

    assert contracts["concepts_xlsx"]["layout_id"] == (
        layouts.UPDATE_AWARE_MASTER_LAYOUT_ID
    )
    assert contracts["concepts_xlsx"]["contract_id"] == (
        "concept-update-aware-1"
    )
    assert contracts["master_xlsx"]["layout_id"] == master_layout_id
    assert contracts["master_xlsx"]["contract_id"] == contract_id


def test_update_flags_are_no_on_every_band_of_every_row() -> None:
    """Contract v2.0 §14.1 (superseding owner decision D3 of 2026-08-29):
    every authored data row carries exact ``No`` in all five is_update_*
    fields, blank Group/Question bands included — the Mathematics
    corrector's "Yes" polarity and the English corrector's blank-on-absent
    convention are both rejected."""
    profile = _profile("Mathematics")
    snapshot = _snapshot("Post")
    data, issues = workbook.render_master_file(snapshot, profile)
    row = workbook.parse_workbook(data)["sheets"]["Objective"]["rows"][0]

    assert row["group_name"] == ""
    assert row["question_label"] == ""
    assert {field: row[field] for field in UPDATE_FIELDS} == {
        field: "No" for field in UPDATE_FIELDS
    }
    assert workbook.validate_master_file(
        workbook.parse_workbook(data), snapshot, profile,
        group_provenance=issues["group_provenance"],
    ) == []


def test_label_aggregates_are_natural_without_reordering_question_rows() -> None:
    profile = _profile("Mathematics")
    group = _group()
    snapshot = _snapshot(
        "Post",
        groups=[group],
        candidates=[
            _objective("AUDIT_C01 Q10"),
            _objective("AUDIT_C01 Q2"),
        ],
    )

    data, issues = workbook.render_master_file(snapshot, profile)
    parsed = workbook.parse_workbook(data)
    rows = parsed["sheets"]["Objective"]["rows"]

    assert [row["question_label"] for row in rows] == [
        "AUDIT_C01 Q10", "AUDIT_C01 Q2",
    ]
    # Contract v2.0 §16: label aggregates are ``" | "`` lists.
    for row in rows:
        assert row["concept_question_labels"] == (
            "AUDIT_C01 Q2 | AUDIT_C01 Q10"
        )
        assert row["group_question_labels"] == (
            "AUDIT_C01 Q2 | AUDIT_C01 Q10"
        )
        assert [row[field] for field in UPDATE_FIELDS] == ["No"] * 5
        assert row["question_source"] == PUBLICATION
    assert workbook.validate_master_file(
        parsed, snapshot, profile,
        group_provenance=issues["group_provenance"],
    ) == []


def test_english_post_master_has_thirty_descriptive_slots_and_reads_back() -> None:
    profile = _profile("English")
    group = _group()
    snapshot = _snapshot(
        "Post",
        groups=[group],
        candidates=[_descriptive_with_thirty_answers()],
    )
    schema = workbook.output_schema("master", profile, snapshot)

    assert [
        len(schema["fields"][sheet]) for sheet in workbook.SHEET_ORDER
    ] == [72, 440, 149]
    assert schema["descriptive_answer_slots"] == 30
    assert schema["fields"]["Descriptive"].count("answer_type_30") == 1

    data, issues = workbook.render_master_file(snapshot, profile)
    assert issues["truncated_rows"] == []
    parsed = workbook.parse_workbook(data)
    row = parsed["sheets"]["Descriptive"]["rows"][0]
    assert row["answer_type_30"] == "Phrases"
    assert row["answer_content_30"] == "[content]: criterion 30"
    assert row["concept_source"] == "Balbharati"
    assert workbook.validate_master_file(
        parsed, snapshot, profile,
        group_provenance=issues["group_provenance"],
    ) == []


def test_english_pre_master_retains_ten_descriptive_slots() -> None:
    profile = _profile("English")
    schema = workbook.output_schema("master", profile, _snapshot("Pre"))

    assert [
        len(schema["fields"][sheet]) for sheet in workbook.SHEET_ORDER
    ] == [72, 380, 149]
    assert schema["descriptive_answer_slots"] == 10
    assert "answer_type_11" not in schema["fields"]["Descriptive"]


def test_unresolved_metadata_keeps_update_aware_master_byte_shape() -> None:
    """Contract v2.0 §14: the update-aware schema is universal, so a run
    with no resolved board profile still renders 72/380/149 (the former
    67/374/144 ``reference-master-1`` default is retired)."""
    snapshot = _snapshot("Post")
    schema = workbook.output_schema("master", None, snapshot)

    assert [
        len(schema["fields"][sheet]) for sheet in workbook.SHEET_ORDER
    ] == [72, 380, 149]
    assert schema["contract_id"] == "update-aware-master-1"
    assert schema["descriptive_answer_slots"] == 10


@pytest.mark.parametrize("field", [
    "is_update_chapter",
    "is_update_topic",
    "is_update_concept",
    "is_update_group",
    "is_update_question",
])
def test_master_readback_rejects_each_mutated_populated_update_flag(
    field: str,
) -> None:
    profile = _profile("Mathematics")
    snapshot = _snapshot(
        "Post", groups=[_group()], candidates=[_objective("AUDIT_C01 Q1")],
    )
    data, issues = workbook.render_master_file(snapshot, profile)
    parsed = workbook.parse_workbook(data)
    assert workbook.validate_master_file(
        parsed, snapshot, profile,
        group_provenance=issues["group_provenance"],
    ) == []

    parsed["sheets"]["Objective"]["rows"][0][field] = "Yes"
    errors = workbook.validate_master_file(
        parsed, snapshot, profile,
        group_provenance=issues["group_provenance"],
    )
    assert any(field in error and "!= 'No'" in error for error in errors)


@pytest.mark.parametrize("mutated", ["", "Yes"])
@pytest.mark.parametrize("field", [
    "is_update_group",
    "is_update_question",
])
def test_concept_tail_readback_requires_no_in_absent_entity_flags(
    field: str, mutated: str,
) -> None:
    """Contract v2.0 §14.1 retires the blank-on-absent-band convention: a
    questionless concept tail carries exact ``No`` in the Group and Question
    markers too, and the read-back refuses a blank as firmly as a ``Yes``."""
    profile = _profile("Mathematics")
    snapshot = _snapshot("Post")
    data, issues = workbook.render_master_file(snapshot, profile)
    parsed = workbook.parse_workbook(data)
    row = parsed["sheets"]["Objective"]["rows"][0]
    assert row[field] == "No"
    assert workbook.validate_master_file(
        parsed, snapshot, profile,
        group_provenance=issues["group_provenance"],
    ) == []

    row[field] = mutated
    errors = workbook.validate_master_file(
        parsed, snapshot, profile,
        group_provenance=issues["group_provenance"],
    )
    assert any(field in error and "!= 'No'" in error for error in errors)


@pytest.mark.parametrize("mutated_source", ["", "Wrong source"])
def test_descriptive_concept_source_readback_is_exact(
    mutated_source: str,
) -> None:
    profile = _profile("English")
    snapshot = _snapshot(
        "Post",
        groups=[_group()],
        candidates=[_descriptive_with_thirty_answers()],
    )
    data, issues = workbook.render_master_file(snapshot, profile)
    parsed = workbook.parse_workbook(data)
    row = parsed["sheets"]["Descriptive"]["rows"][0]
    assert row["concept_source"] == "Balbharati"
    assert workbook.validate_master_file(
        parsed, snapshot, profile,
        group_provenance=issues["group_provenance"],
    ) == []

    row["concept_source"] = mutated_source
    errors = workbook.validate_master_file(
        parsed, snapshot, profile,
        group_provenance=issues["group_provenance"],
    )
    assert any(
        "concept_source" in error and "snapshot value 'Balbharati'" in error
        for error in errors
    )

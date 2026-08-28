"""MES PR 1 — versioned schema and release skeleton (spec §5, §7.5, §9).

Covers the release-domain mechanics and the bulk-import round-trip fixes:
one label = one Question with every placement reconstructed, conflicting
content rejected, no default Basic group minted by a pure Concept row, and
stable group identity in the placement key.
"""
from __future__ import annotations

import pytest

from app import bulk_import as bi
from app import models
from app.bulk_import import reader, writer
from app.services import assessment_release as rel
from app.services import assessment_profile


# --------------------------------------------------------------------------- #
# Release-domain mechanics
# --------------------------------------------------------------------------- #

def test_release_state_machine_moves_forward_only():
    state = "draft"
    for target in ("materialized", "ready_for_upload",
                   "publication_pending", "uploaded", "superseded"):
        state = rel.advance_state(state, target)
    assert state == "superseded"

    with pytest.raises(rel.ReleaseStateError):
        rel.advance_state("uploaded", "draft")
    with pytest.raises(rel.ReleaseStateError):
        rel.advance_state("draft", "uploaded")
    with pytest.raises(rel.ReleaseStateError):
        rel.advance_state("draft", "not_a_state")


def test_release_hashes_are_stable_across_key_order():
    a = {"x": 1, "nested": {"b": 2, "a": [1, 2]}}
    b = {"nested": {"a": [1, 2], "b": 2}, "x": 1}
    assert rel.sha256_json(a) == rel.sha256_json(b)
    assert rel.sha256_json(a) != rel.sha256_json({"x": 2})


def test_final_payload_rechecks_exact_matrix_and_subpoint_durations():
    policy = assessment_profile.assessment_format_policy(
        metadata={
            "board": "MSBSHSE",
            "grade": "6",
            "subject": "Mathematics",
        }
    )
    cells = [{
        "cell_id": "CELL-MATRIX",
        "sheet_kind": "descriptive",
        "question_category": "Short Answer Type (2 Marks)",
        "cognitive_skill": "Apply",
        "difficulty": "High",
        "marks": 2,
    }, {
        "cell_id": "CELL-SUBJECTIVE",
        "sheet_kind": "subjective",
        "question_category": "Fill in the blanks",
        "cognitive_skill": "Remember",
        "difficulty": "Less",
        "marks": 2,
    }]
    candidates = [{
        **cells[0],
        "candidate_id": "C-MATRIX",
        "blueprint_cell_id": "CELL-MATRIX",
        "question_duration": 2,
        "duration_basis_count": None,
    }, {
        **cells[1],
        "candidate_id": "C-SUBJECTIVE",
        "blueprint_cell_id": "CELL-SUBJECTIVE",
        "question_duration": 3,
        "duration_basis_count": 3,
        "answers": [{}],
    }]

    errors = rel.assessment_format_contract_errors({
        "assessment_format_policy": policy,
        "blueprint_cells": cells,
        "candidates": candidates,
    })

    assert any("policy duration 3" in error for error in errors)
    assert any("represented subpoint count 2" in error for error in errors)
    assert any("Subjective answer count" in error for error in errors)


@pytest.mark.parametrize(
    "malformed_maximum",
    [
        pytest.param("one", id="nonnumeric"),
        pytest.param(True, id="bool"),
        pytest.param(0, id="zero"),
        pytest.param(-1, id="negative"),
        pytest.param(1.5, id="fractional"),
    ],
)
def test_final_policy_fails_closed_on_malformed_max_subpoints(
    malformed_maximum,
):
    errors = rel.assessment_format_contract_errors({
        "assessment_format_policy": {
            "policy_id": "malformed-maximum-test",
            "formats_by_sheet": {
                "objective": {
                    "Match the Following": {
                        "marks": {
                            "mode": "per_subpoint",
                            "marks_per_subpoint": 1,
                            "max_subpoints": malformed_maximum,
                        },
                    },
                },
            },
        },
        "blueprint_cells": [{
            "cell_id": "CELL-BAD-MAX",
            "sheet_kind": "objective",
            "question_category": "Match the Following",
            "marks": 1,
        }],
        "candidates": [],
    })

    assert any(
        "max_subpoints must be a positive integer" in error
        for error in errors
    ), errors


def test_compound_parent_and_split_child_cannot_be_separate_questions():
    errors = rel.parent_child_candidate_errors({
        "source_atoms": [
            {"source_qid": "QINV-1", "parent_qid": None},
            {"source_qid": "QINV-1.1", "parent_qid": "QINV-1"},
        ],
        "candidates": [
            {"candidate_id": "C-PARENT", "source_atom_ids": ["QINV-1"]},
            {"candidate_id": "C-CHILD", "source_atom_ids": ["QINV-1.1"]},
        ],
    })

    assert errors == [
        "compound source 'QINV-1' and subpart 'QINV-1.1' are materialized "
        "as separate questions"
    ]


def test_zero_loss_invariant_names_every_break():
    ok = rel.zero_loss_report(
        obligations=["QINV-0001", "QINV-0002.1", "CELL-01"],
        accepted=["QINV-0001", "CELL-01"],
        flagged=["QINV-0002.1"],
    )
    assert ok["holds"] and not ok["missing"] and not ok["unexpected"]

    broken = rel.zero_loss_report(
        obligations=["QINV-0001", "QINV-0002"],
        accepted=["QINV-0001", "QINV-0009"],
        flagged=["QINV-0001"],
    )
    assert not broken["holds"]
    assert broken["missing"] == ["QINV-0002"]
    assert broken["unexpected"] == ["QINV-0009"]
    assert broken["double_counted"] == ["QINV-0001"]


def test_exactly_one_primary_placement_per_question():
    placements = [
        {"candidate_id": "C1", "concept_id": 1, "group_key": "(A) BG01",
         "evidence": "e"},
        {"candidate_id": "C1", "concept_id": 2, "group_key": "(B) BG01",
         "evidence": "e"},
    ]
    errors = rel.primary_placement_errors(placements)
    assert len(errors) == 1 and "more than one primary placement" in errors[0]

    secondary = rel.validate_placement({
        "candidate_id": "C2", "concept_id": 1, "group_key": "(A) BG01",
        "evidence": "e", "secondary_placements": [{"concept_id": 3}],
    })
    assert any("secondary_placements must be empty" in e for e in secondary)


def test_duplicate_group_identity_is_rejected_but_multiple_tiers_are_not():
    groups = [
        {"concept_id": 1, "group_type": "Basic", "group_key": "(A) BG01"},
        {"concept_id": 1, "group_type": "Basic", "group_key": "(A) BG02"},
        {"concept_id": 1, "group_type": "Basic", "group_key": "(A) BG01"},
    ]
    duplicates = rel.duplicate_group_identities(groups)
    # Two Basic groups under one concept are legal; the same identity twice
    # is not (spec §5.5: never constrain a concept to one group per type).
    assert duplicates == [(1, "Basic", "(A) BG01")]


def test_duplicate_group_key_is_rejected_globally_in_frozen_payload():
    groups = [
        {
            "concept_id": 1,
            "group_type": "Basic",
            "group_key": "(A) BG01",
            "group_sequence": 1,
            "group_name": "Alpha — Basic",
            "group_display_name": "Alpha — Basic",
        },
        {
            "concept_id": 2,
            "group_type": "Advanced",
            "group_key": "(A) BG01",
            "group_sequence": 1,
            "group_name": "Beta — Advanced",
            "group_display_name": "Beta — Advanced",
        },
    ]

    assert rel.duplicate_group_identities(groups) == []
    assert rel.duplicate_group_keys(groups) == ["(A) BG01"]
    frozen = rel.freeze_payload({"groups": groups})
    assert "duplicate group_key '(A) BG01'" in frozen["errors"]
    assert not any(
        error.startswith("duplicate group identity")
        for error in frozen["errors"]
    )


def test_answer_restriction_is_never_silently_defaulted():
    base = {
        "candidate_id": "C1", "source_atom_ids": ["QINV-0001"],
        "blueprint_cell_id": "CELL-01", "question": "Q?",
        "question_text": "Q?", "sheet_kind": "objective",
        "question_category": "MCQ", "cognitive_skill": "Remember",
        "difficulty": "Less", "marks": 1, "question_duration": 2,
        "math_keyboard": "", "restriction_reason": "Authored evidence.",
        "answers": [
            {
                "answer_type": "Phrases", "answer_content": "A",
                "correct_answer": "Yes", "answer_weightage": 1,
            },
            {
                "answer_type": "Phrases", "answer_content": "B",
                "correct_answer": "No", "answer_weightage": 0,
            },
        ],
        "sub_questions": [],
    }
    missing = rel.validate_candidate(dict(base))
    assert any("answer_restriction" in e for e in missing)
    open_objective = rel.validate_candidate(
        dict(base, answer_restriction="Open"))
    assert open_objective == []
    assert rel.validate_candidate(
        dict(base, answer_restriction="Specific")) == []

    near_match = dict(
        base,
        answer_restriction="Open",
        answers=[dict(answer) for answer in base["answers"]],
    )
    near_match["answers"][0]["answer_weightage"] = "0.999"
    assert any(
        "objective answer 1 weightage 0.999 != 1" in error
        for error in rel.validate_candidate(near_match)
    )

    for out_of_range in ("1e-10000", "1e1000000"):
        invalid_duration = dict(
            base,
            answer_restriction="Open",
            question_duration=out_of_range,
        )
        assert any(
            "question_duration must be finite and positive" in error
            for error in rel.validate_candidate(invalid_duration)
        )

    invalid_weight = dict(
        base,
        answer_restriction="Open",
        answers=[dict(answer) for answer in base["answers"]],
    )
    invalid_weight["answers"][0]["answer_weightage"] = "1e1000000"
    assert any(
        "objective answer 1 weightage must be finite" in error
        for error in rel.validate_candidate(invalid_weight)
    )

    invalid_weight["answers"][0]["answer_weightage"] = "+1"
    invalid_weight["answers"][1]["answer_weightage"] = "-0"
    assert any(
        "weightage must be finite" in error
        for error in rel.validate_candidate(invalid_weight)
    )


def test_release_refuses_mixed_answer_media_and_one_four_mark_rubric():
    candidate = {
        "candidate_id": "C4", "source_atom_ids": ["QINV-0004"],
        "blueprint_cell_id": "CELL-04", "question": "Calculate the value.",
        "question_text": (
            "| Name of peak | Altitude |\n|---|---:|\n| K-2 | 8611 |"
        ),
        "sheet_kind": "descriptive", "question_category": "Long Answer",
        "cognitive_skill": "Apply", "difficulty": "Moderate", "marks": 4,
        "question_duration": 5, "math_keyboard": "Yes",
        "answer_restriction": "Specific",
        "restriction_reason": "The result is bounded.",
        "display_answer": "[Katex] x=2 [/Katex]",
        "answer_explanation": "[Katex] x=2 [/Katex]",
        "answers": [{
            "answer_type": "Equation",
            "answer_content": "Result: [Katex]x=2[/Katex]",
            "answer_weightage": 4,
        }],
        "sub_questions": [],
    }

    errors = rel.validate_candidate(candidate)

    assert any("equation_katex_wrapper" in error for error in errors)
    assert any("requires at least two rubric blocks" in error for error in errors)
    assert "question_text rich-text: unsupported_table" in errors


@pytest.mark.parametrize(
    "prefixed_option", [
        "a) Circle", "A) Circle", "(a) Circle", "a. Circle", "A: Circle",
    ],
)
def test_release_refuses_prefixed_options_and_malformed_rubric_tags(
    prefixed_option,
):
    objective = {
        "candidate_id": "C-OPTION",
        "source_atom_ids": ["QINV-OPTION"],
        "blueprint_cell_id": "CELL-OPTION",
        "question": "Choose the correct shape.",
        "question_text": "Choose the correct shape.",
        "sheet_kind": "objective",
        "question_category": "Multiple Choice Question",
        "cognitive_skill": "Remember",
        "difficulty": "Less",
        "marks": 1,
        "question_duration": 1,
        "math_keyboard": "",
        "answer_restriction": "Specific",
        "restriction_reason": "One option is correct.",
        "answers": [{
            "answer_type": "Phrases",
            "answer_content": prefixed_option,
            "correct_answer": "1",
            "answer_weightage": 1,
        }, {
            "answer_type": "Phrases",
            "answer_content": "Sphere",
            "correct_answer": "0",
            "answer_weightage": 0,
        }],
        "sub_questions": [],
    }
    descriptive = {
        **objective,
        "candidate_id": "C-RUBRIC",
        "source_atom_ids": ["QINV-RUBRIC"],
        "blueprint_cell_id": "CELL-RUBRIC",
        "sheet_kind": "descriptive",
        "question_category": "Long Answer",
        "marks": 2,
        "question_duration": 2,
        "math_keyboard": "No",
        "answer_restriction": "Open",
        "restriction_reason": "Equivalent explanations are accepted.",
        "display_answer": "A valid explanation.",
        "answers": [{
            "answer_type": "Phrases",
            "answer_content": "[content] missing colon",
            "answer_weightage": 2,
        }],
    }

    assert any(
        "repeats its letter label" in error
        for error in rel.validate_candidate(objective)
    )
    assert any(
        "without its required colon" in error
        for error in rel.validate_candidate(descriptive)
    )

    descriptive["answers"][0]["answer_content"] = "[content]:   "
    assert any(
        "allowed functional tag" in error
        for error in rel.validate_candidate(descriptive)
    )


def test_release_accepts_canonical_subjective_placeholders():
    prompt = "Complete $$a$$, then complete $$b$$."
    candidate = {
        "candidate_id": "C-SUBJ",
        "source_atom_ids": ["QINV-SUBJ"],
        "blueprint_cell_id": "CELL-SUBJ",
        "question": prompt,
        "question_text": prompt,
        "sheet_kind": "subjective",
        "question_category": "Fill in the Blanks",
        "cognitive_skill": "Apply",
        "difficulty": "Less",
        "marks": 2,
        "question_duration": 2,
        "math_keyboard": "No",
        "answer_restriction": "Specific",
        "restriction_reason": "The source fixes both missing words.",
        "answers": [
            {
                "answer_type": "Phrases",
                "answer_content": "first",
                "answer_display": "first",
                "answer_weightage": 1,
                "placeholder": "a",
            },
            {
                "answer_type": "Phrases",
                "answer_content": "second",
                "answer_display": "second",
                "answer_weightage": 1,
                "placeholder": "b",
            },
        ],
        "sub_questions": [],
    }

    wide_profile = {
        **assessment_profile.DEFAULT_PROFILE,
        "sheet_kinds": ("objective", "descriptive", "subjective"),
    }
    assert rel.validate_candidate(candidate, wide_profile) == []

    undeclared = {
        **candidate,
        "question": prompt + " Do not hide $$x$$.",
        "question_text": prompt + " Do not hide $$x$$.",
    }
    assert any(
        "raw_math_delimiter" in error
        for error in rel.validate_candidate(undeclared, wide_profile)
    )


def test_release_accepts_exclusive_multipart_descriptive_scoring():
    candidate = {
        "candidate_id": "C-MULTIPART",
        "source_atom_ids": ["QINV-MULTIPART"],
        "blueprint_cell_id": "CELL-MULTIPART",
        "question": "Answer both parts.",
        "question_text": "Answer both parts.",
        "sheet_kind": "descriptive",
        "question_category": "Long Answer",
        "cognitive_skill": "Apply",
        "difficulty": "Moderate",
        "marks": 4,
        "question_duration": 5,
        "math_keyboard": "No",
        "answer_restriction": "Specific",
        "restriction_reason": "Each source part fixes its own result.",
        "display_answer": "First result; second result.",
        "answer_explanation": "Apply the stated method to each part.",
        "answers": [],
        "sub_questions": [
            {
                "text": "State the first result.",
                "marks": 2,
                "keywords": [
                    {
                        "answer_type": "Phrases",
                        "keyword": "[content]: first result",
                        "weightage": 2,
                    },
                ],
            },
            {
                "text": "Explain the second result.",
                "marks": 2,
                "keywords": [
                    {
                        "answer_type": "Phrases",
                        "keyword": "[method]: second result",
                        "weightage": 2,
                    },
                ],
            },
        ],
    }

    assert rel.validate_candidate(candidate) == []

    duplicated_scoring = {
        **candidate,
        "answers": [
            {
                "answer_type": "Phrases",
                "answer_content": "Duplicated shared rubric.",
                "answer_weightage": 4,
            },
        ],
    }
    errors = rel.validate_candidate(duplicated_scoring)
    assert any("duplicates scoring" in error for error in errors)
    assert not any(
        "requires at least two rubric blocks" in error for error in errors
    )


def test_release_model_persists_with_hashes(db):
    payload = {"source_atoms": [], "blueprint_cells": [], "candidates": [],
               "groups": [], "placements": []}
    frozen = rel.freeze_payload(payload)
    assert frozen["errors"] == []
    release = models.AssessmentRelease(
        release_uid=rel.new_release_uid(),
        job_id=None,
        payload=payload,
        concept_snapshot_sha256=rel.sha256_json({}),
        source_inventory_sha256=frozen["hashes"]["source_atoms"],
        blueprint_sha256=frozen["hashes"]["blueprint_cells"],
    )
    db.add(release)
    db.flush()
    assert release.state == "draft"
    assert release.version == 1
    db.rollback()


def test_question_carries_mes_identity_columns():
    columns = models.Question.__table__.columns
    for name in (
        "answer_restriction", "source_qid", "blueprint_cell_id",
        "source_paper_number", "alternative_set_id", "parent_question_id",
        "image_manifest", "source_evidence", "assessment_gist",
        "route_audit", "validation_status", "validation_errors",
    ):
        assert name in columns, name
    assert "group_key" in models.Group.__table__.columns
    assert "group_sequence" in models.Group.__table__.columns


# --------------------------------------------------------------------------- #
# Bulk-import round-trip fixes
# --------------------------------------------------------------------------- #

def _obj_row(**fields) -> list:
    row = [""] * len(bi.OBJECTIVE_FIELDS)
    for name, value in fields.items():
        row[bi.OBJECTIVE_FIELDS.index(name)] = value
    return row


def _save_workbook(tmp_path, rows, name="mes_pr1.xlsx"):
    wb = writer._new_workbook()
    ws = wb[bi.SHEET_OBJECTIVE]
    for row in rows:
        ws.append(row)
    path = tmp_path / name
    wb.save(path)
    return path


def test_pure_concept_row_mints_no_default_group(db, tmp_path):
    path = _save_workbook(tmp_path, [
        _obj_row(
            chapter_title="MES PR1 Concept Only Chapter",
            topic_title="Topic 01: Plain Topic",
            concept_title="Plain Concept Without Group",
            concept_details="Description: a concept catalogue row.",
        ),
    ])
    counts = reader.import_workbook(db, path)
    assert counts["concepts"] == 1
    assert counts["groups"] == 0
    assert counts["questions"] == 0
    concept = db.query(models.Concept).filter_by(
        concept_title="Plain Concept Without Group").one()
    assert concept.groups == []


def test_repeated_label_reconstructs_placements_in_empty_db(db, tmp_path):
    question = "Which of these is a three-dimensional shape?"
    home = _obj_row(
        chapter_title="MES PR1 Tag Chapter",
        topic_title="Topic 01: Solids",
        concept_title="Solid Shapes",
        group_type="Basic", group_name="(SolidShapes) BG01",
        question_label="MESQ-PR1-0001",
        question=question, marks="1",
    )
    tag = _obj_row(
        chapter_title="MES PR1 Tag Chapter",
        topic_title="Topic 01: Solids",
        concept_title="Everyday Objects",
        group_type="Basic", group_name="(EverydayObjects) BG01",
        question_label="MESQ-PR1-0001",
        question=question, marks="1",
    )
    counts = reader.import_workbook(db, _save_workbook(tmp_path, [home, tag]))
    assert counts["questions"] == 1
    assert counts["question_tags"] == 1
    assert not any("conflicting" in issue for issue in counts["issues"])

    q = db.query(models.Question).filter_by(
        question_label="MESQ-PR1-0001").one()
    assert q.group.concept.concept_title == "Solid Shapes"
    assert [t.group.concept.concept_title for t in q.tags] == [
        "Everyday Objects"]


def test_conflicting_content_under_one_label_is_rejected(db, tmp_path):
    home = _obj_row(
        chapter_title="MES PR1 Conflict Chapter",
        topic_title="Topic 01: Angles",
        concept_title="Types of Angles",
        group_type="Basic", group_name="(TypesOfAngles) BG01",
        question_label="MESQ-PR1-0002",
        question="Name the angle smaller than a right angle.", marks="1",
    )
    conflict = _obj_row(
        chapter_title="MES PR1 Conflict Chapter",
        topic_title="Topic 01: Angles",
        concept_title="Measuring Angles",
        group_type="Basic", group_name="(MeasuringAngles) BG01",
        question_label="MESQ-PR1-0002",
        question="A completely different question about protractors.",
        marks="1",
    )
    counts = reader.import_workbook(
        db, _save_workbook(tmp_path, [home, conflict]))
    assert counts["questions"] == 1
    assert counts["question_tags"] == 0
    assert any(
        "conflicting question content" in issue for issue in counts["issues"])
    q = db.query(models.Question).filter_by(
        question_label="MESQ-PR1-0002").one()
    assert q.tags == []


def test_imported_group_carries_stable_key_and_sequence(db, tmp_path):
    path = _save_workbook(tmp_path, [
        _obj_row(
            chapter_title="MES PR1 Key Chapter",
            topic_title="Topic 01: Numbers",
            concept_title="Place Value",
            group_type="Basic", group_name="(PlaceValue) BG02",
            question_label="MESQ-PR1-0003",
            question="What is the place value of 5 in 3572?", marks="1",
        ),
    ])
    reader.import_workbook(db, path)
    group = db.query(models.Group).filter_by(
        group_name="(PlaceValue) BG02").one()
    assert group.group_key == "(PlaceValue) BG02"
    assert group.group_sequence == 2


def test_placement_key_includes_stable_group_identity(db, tmp_path):
    """Two same-tier groups under one concept never collapse (spec §7.5)."""
    path = _save_workbook(tmp_path, [
        _obj_row(
            chapter_title="MES PR1 Two Groups Chapter",
            topic_title="Topic 01: Fractions",
            concept_title="Comparing Fractions",
            group_type="Basic", group_name="(ComparingFractions) BG01",
            question_label="MESQ-PR1-0004",
            question="Which fraction is larger: 1/2 or 1/3?", marks="1",
        ),
    ])
    reader.import_workbook(db, path)
    q = db.query(models.Question).filter_by(
        question_label="MESQ-PR1-0004").one()
    concept = q.group.concept
    second = models.Group(
        concept_id=concept.id, group_type="Basic",
        group_name="(ComparingFractions) BG02",
        group_key="(ComparingFractions) BG02", group_sequence=2,
    )
    db.add(second)
    db.flush()
    key_home = writer.question_placement_key(q.question_label, q.group)
    key_second = writer.question_placement_key(q.question_label, second)
    assert key_home != key_second
    assert key_home[-1] == "(ComparingFractions) BG01"
    assert key_second[-1] == "(ComparingFractions) BG02"
    db.rollback()

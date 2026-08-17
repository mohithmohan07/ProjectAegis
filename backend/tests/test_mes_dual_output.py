"""MES PR 5 — Concept and Master renderers from one release snapshot.

The uploaded Grade-6 MES FINAL templates are the positional authority;
``assessment_workbook_template.json`` captures them verbatim and these tests pin
both renderers to it (spec §0 item 2, §1, §9, §10, §13).
"""
from __future__ import annotations

import copy
import io

import openpyxl
import pytest

from app.bulk_import import assessment_workbook as mp
from app.services import assessment_grouping as ag


def _snapshot() -> dict:
    chapter = {
        "chapter_title": (
            "Three-Dimensional Shapes (06_Mathematics_MSBSHSE_Balbharati)"),
        "chapter_display_name": "Three-Dimensional Shapes",
        "pre_topics": "", "post_topics": "Topic 01",
        "chapter_description": "Solids and their properties.",
    }
    concept_a = {
        "concept_key": "C_A",
        "concept_title": "Two-dimensional shape (06MSMA_T01_TwoDim)",
        "concept_display_name": "Two-dimensional shape",
        "concept_details": "Description: flat shapes.",
        "keywords": "shape; plane",
        "related_concepts": "",
        "digicards": "",
        "concept_source": "Balbharati",
    }
    concept_b = {  # deliberately questionless
        "concept_key": "C_B",
        "concept_title": "Three-dimensional shape (06MSMA_T01_ThreeDim)",
        "concept_display_name": "Three-dimensional shape",
        "concept_details": "Description: solid shapes.",
        "keywords": "solid",
        "related_concepts": "",
        "digicards": "",
        "concept_source": "Balbharati",
    }
    groups = []
    for key, machine, name in (
        ("C_A", "06MSMA_T01_TwoDim", "Two-dimensional shape"),
        ("C_B", "06MSMA_T01_ThreeDim", "Three-dimensional shape"),
    ):
        for shell in ag.required_shells(key, machine, name):
            shell = dict(shell)
            shell["concept_key"] = key
            groups.append(shell)
    objective = {
        "candidate_id": "CAND-1",
        "question_label": "06MSMA_T01_TwoDim Q01",
        "sheet_kind": "objective",
        "question_category": "Multiple Choice Question",
        "cognitive_skill": "Remember",
        "difficulty": "Less",
        "marks": 1.0,
        "question_appears_in": "Pre/Post-Worksheet/Test",
        "answer_restriction": "Specific",
        "question": "Which of these is a two-dimensional shape?",
        "question_text": "Which of these is a two-dimensional shape?",
        "answers": [
            {"answer_type": "Phrases", "answer_content": "Circle",
             "correct_answer": "1", "answer_weightage": "1"},
            {"answer_type": "Phrases", "answer_content": "Sphere",
             "correct_answer": "0", "answer_weightage": "0"},
        ],
        "sub_questions": [],
        "answer_explanation": "A circle is flat; a sphere is a solid.",
        "concept_key": "C_A",
        "group_key": "(06MSMA_T01_TwoDim) BG01",
        "flags": [],
    }
    descriptive = {
        "candidate_id": "CAND-2",
        "question_label": "06MSMA_T01_TwoDim Q02",
        "sheet_kind": "descriptive",
        "question_category": "Long Answer",
        "cognitive_skill": "Understand",
        "difficulty": "Moderate",
        "marks": 4.0,
        "question_appears_in": "Pre/Post-Worksheet/Test",
        "answer_restriction": "Open",
        "question": "Explain how a square differs from a cube.",
        "question_text": "Explain how a square differs from a cube.",
        "display_answer": "A square is flat with two dimensions ...",
        "answers": [
            {"answer_type": "Phrases", "answer_weightage": "2",
             "answer_content": "square described as two-dimensional"},
            {"answer_type": "Phrases", "answer_weightage": "2",
             "answer_content": "cube described as three-dimensional"},
        ],
        "sub_questions": [
            {"text": "Name the number of faces of a cube.", "marks": "2",
             "keywords": [
                 {"answer_type": "Phrases", "weightage": "2",
                  "keyword": "six faces"}]},
            {"text": "State the dimensions of a square.", "marks": "2",
             "keywords": []},
        ],
        "answer_explanation": "",
        "concept_key": "C_A",
        "group_key": "(06MSMA_T01_TwoDim) IG01",
        "flags": [],
    }
    return {
        "chapter": chapter,
        "topics": [{
            "topic_title": "Topic 01: Dimensions (06MSMA_ThreeDim_PL)",
            "topic_display_name": "Dimensions",
            "pre_post_learning": "Post",
            "topic_concept_labels": "",
            "related_topics": "",
            "topic_description": "Dimensions of shapes.",
            "concepts": [concept_a, concept_b],
        }],
        "groups": groups,
        "candidates": [objective, descriptive],
    }


def test_manifest_matches_the_uploaded_templates():
    assert mp.SHEET_ORDER == ["Objective", "Descriptive", "Subjective"]
    assert len(mp.FIELDS["Objective"]) == 67
    assert len(mp.FIELDS["Descriptive"]) == 374
    assert len(mp.FIELDS["Subjective"]) == 144
    obj = mp.FIELDS["Objective"]
    # answer_restriction sits between appears_in and difficulty; question_text
    # directly after question; answer_explanation is the last column.
    assert obj[obj.index("question_appears_in") + 1] == "answer_restriction"
    assert obj[obj.index("answer_restriction") + 1] == "level_of_difficulty"
    assert obj[obj.index("question") + 1] == "question_text"
    assert obj[-1] == "answer_explanation"
    # Concept band keeps keywords/related_concepts; no parent_concept.
    assert "keywords" in obj and "related_concepts" in obj
    assert "parent_concept" not in obj
    # Subjective carries twenty answer blocks.
    assert "placeholder_20" in mp.FIELDS["Subjective"]


def test_dual_output_shares_one_snapshot_and_validates():
    result = mp.build_dual_output(_snapshot())
    assert result["valid"], result["manifest"]["read_back"]
    manifest = result["manifest"]
    assert manifest["concept_snapshot_sha256"] == mp.snapshot_sha256(
        _snapshot())
    assert set(manifest["workbook_sha256s"]) == {
        "concepts_xlsx", "master_xlsx"}
    assert manifest["issues"]["unplaced"] == []
    assert manifest["issues"]["placed_questions"] == 2


def test_concept_file_is_a_clean_catalogue():
    parsed = mp.parse_workbook(mp.render_concept_file(_snapshot()))
    assert parsed["sheet_order"] == ["Objective", "Descriptive", "Subjective"]
    rows = parsed["sheets"]["Objective"]["rows"]
    assert [r["concept_title"] for r in rows] == [
        "Two-dimensional shape (06MSMA_T01_TwoDim)",
        "Three-dimensional shape (06MSMA_T01_ThreeDim)",
    ]
    for row in rows:
        assert row["group_name"] == "" and row["question_label"] == ""
        assert row["basic_groups"] == "" and row["concept_question_labels"] == ""
        assert row["chapter_duration"] == ""
        # Identity values survive byte-exact, spelling variants included.
        assert row["chapter_title"].endswith("(06_Mathematics_MSBSHSE_Balbharati)")
        assert row["keywords"] != "" or row["concept_title"].startswith("Three")
    assert parsed["sheets"]["Descriptive"]["rows"] == []
    assert parsed["sheets"]["Subjective"]["rows"] == []


def test_master_contains_everything_including_questionless_concepts():
    master, issues = mp.render_master_file(_snapshot())
    parsed = mp.parse_workbook(master)
    assert issues["unplaced"] == []
    objective_rows = parsed["sheets"]["Objective"]["rows"]
    descriptive_rows = parsed["sheets"]["Descriptive"]["rows"]
    assert parsed["sheets"]["Subjective"]["rows"] == []
    group_keys = {group["group_key"] for group in _snapshot()["groups"]}
    for row in [*objective_rows, *descriptive_rows]:
        assert row["group_name"] == row["group_display_name"]
        assert row["group_name"] not in group_keys
        assert all(key not in row["group_name"] for key in group_keys)
    provenance = {
        (item["sheet"], item["row"]): item["group_key"]
        for item in issues["group_provenance"]
    }
    assert provenance[("Objective", 3)] == "(06MSMA_T01_TwoDim) BG01"
    assert provenance[("Descriptive", 3)] == "(06MSMA_T01_TwoDim) IG01"
    assert provenance[("Objective", 4)] == "(06MSMA_T01_TwoDim) AG01"
    assert parsed["sheets"]["Objective"]["row_numbers"] == [3, 4, 5, 6, 7]
    assert parsed["sheets"]["Descriptive"]["row_numbers"] == [3]

    # One objective question row + catalogue rows for the five groups no
    # question represents (2 concepts x 3 shells - the occupied BG01... the
    # occupied IG01 lives on the Descriptive sheet).
    question_rows = [r for r in objective_rows if r["question_label"]]
    catalogue_rows = [r for r in objective_rows if not r["question_label"]]
    assert len(question_rows) == 1
    assert len(catalogue_rows) == 4
    # The questionless concept appears via its shells.
    assert any(
        r["concept_title"].startswith("Three-dimensional")
        for r in catalogue_rows)
    shells = {r["group_name"] for r in catalogue_rows}
    assert "Three-dimensional shape — Basic" in shells
    assert all(
        r["group_description"] == "NA" for r in catalogue_rows
        if r["group_type"] in {"Basic", "Intermediate", "Advanced"}
        and r["concept_title"].startswith("Three-dimensional"))

    q = question_rows[0]
    assert q["question_appears_in"] == "Pre/Post-Worksheet/Test"
    assert q["answer_restriction"] == "Specific"
    assert q["answer_content_1"] == "Circle"
    assert str(q["correct_answer_1"]) == "1"
    assert q["group_question_labels"] == "06MSMA_T01_TwoDim Q01"
    assert q["concept_question_labels"] == (
        "06MSMA_T01_TwoDim Q01, 06MSMA_T01_TwoDim Q02")

    d = descriptive_rows[0]
    assert d["sub_question_1"] == "Name the number of faces of a cube."
    assert d["sq1_keyword_1"] == "six faces"
    assert d["display_answer"].startswith("A square is flat")
    assert d["answer_restriction"] == "Open"


def test_unresolved_placement_rides_the_manifest_never_disappears():
    snapshot = copy.deepcopy(_snapshot())
    snapshot["candidates"][0]["group_key"] = "(unknown) BG01"
    master, issues = mp.render_master_file(snapshot)
    assert issues["unplaced"][0]["candidate_id"] == "CAND-1"
    parsed = mp.parse_workbook(master)
    labels = [
        r["question_label"] for r in parsed["sheets"]["Objective"]["rows"]
        if r["question_label"]
    ]
    assert labels == []  # not silently placed anywhere
    errors = mp.validate_master_file(parsed, snapshot)
    assert errors == []  # structure stays valid; the ledger carries the issue


def test_master_validation_names_wire_value_and_arithmetic_defects():
    snapshot = copy.deepcopy(_snapshot())
    snapshot["candidates"][0]["question_appears_in"] = (
        "Pre-test, Post-test, Worksheet, Test")  # expanded = wrong for MES
    snapshot["candidates"][0]["answers"][0]["answer_weightage"] = "3"
    master, _ = mp.render_master_file(snapshot)
    errors = mp.validate_master_file(mp.parse_workbook(master), snapshot)
    assert any("not the profile wire value" in e for e in errors)
    assert any("correct weightage 3 != marks 1" in e for e in errors)


def test_master_provenance_distinguishes_shared_friendly_group_names():
    snapshot = copy.deepcopy(_snapshot())
    first_group = next(
        group for group in snapshot["groups"]
        if group["group_key"] == "(06MSMA_T01_TwoDim) BG01"
    )
    first_group["group_name"] = "Two-dimensional shape — Basic"
    first_group["group_display_name"] = "Two-dimensional shape — Basic"
    second_group = copy.deepcopy(first_group)
    second_group["group_key"] = "(06MSMA_T01_TwoDim) BG02"
    second_group["semantic_description"] = "A second Basic variant family."
    snapshot["groups"].append(second_group)

    second_question = copy.deepcopy(snapshot["candidates"][0])
    second_question["candidate_id"] = "CAND-3"
    second_question["question_label"] = "06MSMA_T01_TwoDim Q03"
    second_question["question"] = "Which listed figure is flat?"
    second_question["question_text"] = "Which listed figure is flat?"
    second_question["group_key"] = "(06MSMA_T01_TwoDim) BG02"
    snapshot["candidates"].append(second_question)

    master, issues = mp.render_master_file(snapshot)
    parsed = mp.parse_workbook(master)
    friendly_rows = [
        (row_number, row)
        for row_number, row in zip(
            parsed["sheets"]["Objective"]["row_numbers"],
            parsed["sheets"]["Objective"]["rows"],
        )
        if row["group_name"] == "Two-dimensional shape — Basic"
    ]
    assert [row["group_display_name"] for _, row in friendly_rows] == [
        "Two-dimensional shape — Basic",
        "Two-dimensional shape — Basic",
    ]
    assert [row["group_question_labels"] for _, row in friendly_rows] == [
        "06MSMA_T01_TwoDim Q01",
        "06MSMA_T01_TwoDim Q03",
    ]

    provenance = {
        (item["sheet"], item["row"]): item["group_key"]
        for item in issues["group_provenance"]
    }
    assert [
        provenance[("Objective", row_number)]
        for row_number, _ in friendly_rows
    ] == [
        "(06MSMA_T01_TwoDim) BG01",
        "(06MSMA_T01_TwoDim) BG02",
    ]
    assert mp.validate_master_file(
        parsed, snapshot,
        group_provenance=issues["group_provenance"],
    ) == []
    assert any(
        "group provenance required" in error
        for error in mp.validate_master_file(parsed, snapshot)
    )

    result = mp.build_dual_output(snapshot)
    assert result["valid"], result["manifest"]["read_back"]


def test_master_refuses_duplicate_internal_group_keys():
    snapshot = copy.deepcopy(_snapshot())
    duplicate = copy.deepcopy(snapshot["groups"][0])
    duplicate["concept_key"] = "C_B"
    duplicate["group_type"] = "Advanced"
    duplicate["group_name"] = "Three-dimensional shape — Advanced"
    duplicate["group_display_name"] = duplicate["group_name"]
    snapshot["groups"].append(duplicate)
    with pytest.raises(mp.WorkbookRenderError, match="duplicate group_key"):
        mp.render_master_file(snapshot)


def test_master_refuses_non_q12_visible_group_names():
    snapshot = copy.deepcopy(_snapshot())
    group = snapshot["groups"][0]
    group["group_name"] = group["group_key"]
    group["group_display_name"] = group["group_key"]
    with pytest.raises(mp.WorkbookRenderError, match="visible names"):
        mp.render_master_file(snapshot)

    missing_display = copy.deepcopy(_snapshot())
    missing_display["topics"][0]["concepts"][0][
        "concept_display_name"
    ] = ""
    with pytest.raises(
        mp.WorkbookRenderError, match="no explicit concept_display_name",
    ):
        mp.render_master_file(missing_display)


def test_master_refuses_candidate_group_concept_mismatch():
    snapshot = copy.deepcopy(_snapshot())
    snapshot["candidates"][0]["concept_key"] = "C_B"
    with pytest.raises(mp.WorkbookRenderError, match="does not match group"):
        mp.render_master_file(snapshot)


def test_master_provenance_uses_physical_sheet_row_numbers():
    snapshot = _snapshot()
    master, issues = mp.render_master_file(snapshot)
    workbook = openpyxl.load_workbook(io.BytesIO(master))
    workbook["Objective"].insert_rows(3)
    buffer = io.BytesIO()
    workbook.save(buffer)
    workbook.close()

    parsed = mp.parse_workbook(buffer.getvalue())
    assert parsed["sheets"]["Objective"]["row_numbers"] == [4, 5, 6, 7, 8]
    assert parsed["sheets"]["Descriptive"]["row_numbers"] == [3]
    shifted_provenance = copy.deepcopy(issues["group_provenance"])
    for item in shifted_provenance:
        if item["sheet"] == "Objective":
            item["row"] += 1
    assert mp.validate_master_file(
        parsed, snapshot, group_provenance=shifted_provenance,
    ) == []


def test_formula_injection_and_cell_limit_guards():
    snapshot = copy.deepcopy(_snapshot())
    snapshot["candidates"][0]["question"] = "=HYPERLINK evil"
    master, _ = mp.render_master_file(snapshot)
    parsed = mp.parse_workbook(master)
    q = [r for r in parsed["sheets"]["Objective"]["rows"]
         if r["question_label"]][0]
    assert q["question"] == "'=HYPERLINK evil"

    oversized = copy.deepcopy(_snapshot())
    oversized["candidates"][0]["question"] = "x" * (mp.CELL_LIMIT + 1)
    with pytest.raises(mp.WorkbookRenderError, match="exceeds"):
        mp.render_master_file(oversized)


def test_capacity_overflow_is_refused_with_the_label_named():
    snapshot = copy.deepcopy(_snapshot())
    snapshot["candidates"][0]["answers"] *= 4  # 8 options
    with pytest.raises(mp.WorkbookRenderError, match="Q01"):
        mp.render_master_file(snapshot)

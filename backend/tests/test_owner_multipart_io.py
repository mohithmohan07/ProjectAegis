"""Multipart wire views round-trip without scoring twice or losing criteria."""
from __future__ import annotations

import copy
import io
from decimal import Decimal

import openpyxl
import pytest

from app import models
from app.bulk_import import assessment_workbook as workbook
from app.bulk_import import reader, writer
from app.services import assessment_prompts
from tests.test_bulk_import_layout_migration import _graph
from tests.test_owner_column_reimport import _english_workbook


def test_multipart_review_does_not_require_single_part_parent_rubrics():
    record = {
        "sheet_kind": "descriptive", "question": "Answer both parts.",
        "question_text": "Answer both parts.<br>a) First task.<br>b) Second task.",
        "marks": 4, "cognitive_skills": "Understand",
        "level_of_difficulty": "Moderate", "answers": [],
        "sub_questions": [{
            "text": f"{label}) {task} task.", "marks": 2,
            "keywords": [{"answer_type": "Phrases", "weightage": 2,
                          "keyword": f"Evidence for the {task.lower()} response."}],
        } for label, task in (("a", "First"), ("b", "Second"))],
    }
    assert not any("at least two rubric" in issue for issue in assessment_prompts.review_question(record))


def test_projected_parent_roundtrips_to_one_internal_scoring_source(db, tmp_path):
    data, descriptive = _english_workbook("evidence")
    source = tmp_path / "parent-projection.xlsx"
    source.write_bytes(data)
    reader.import_workbook(db, source, strict_content=True)
    question = db.query(models.Question).filter_by(
        question_label=descriptive["question_label"],
    ).one()
    assert question.answers == []
    expected = workbook.multipart_parent_answers(question.sub_questions)
    assert expected
    # Legacy ORM residue is not silently mutated during a fresh export.
    question.answers = [{
        "answer_type": "Phrases", "answer_weightage": question.marks,
        "answer_content": "Historical parent rubric retained in storage.",
    }]
    original_answers = copy.deepcopy(question.answers)
    original_children = copy.deepcopy(question.sub_questions)
    exported = writer.write_workbook(db, question_ids=[question.id])
    row = workbook.parse_workbook(exported)["sheets"]["Descriptive"]["rows"][0]
    assert [row[f"answer_content_{n}"] for n in range(1, len(expected) + 1)] == [
        answer["answer_content"] for answer in expected
    ]
    assert sum(Decimal(row[f"answer_weightage_{n}"]) for n in range(1, len(expected) + 1)) == Decimal(str(question.marks))
    assert question.answers == original_answers
    assert question.sub_questions == original_children
    source.write_bytes(exported)
    reader.import_workbook(db, source, strict_content=True)


def test_mismatched_parent_projection_is_rejected_before_import(db, tmp_path):
    data, _ = _english_workbook("evidence")
    book = openpyxl.load_workbook(io.BytesIO(data))
    sheet = book["Descriptive"]
    headers = [cell.value for cell in sheet[2]]
    sheet.cell(row=3, column=headers.index("answer_content_1") + 1).value = (
        "[creative]: A criterion not represented in the child scoring."
    )
    source = tmp_path / "mismatched-parent.xlsx"
    book.save(source)
    book.close()
    before = db.query(models.Question).count()
    with pytest.raises(reader.WorkbookContentError, match="parent criterion|parent projection"):
        reader.import_workbook(db, source, strict_content=True)
    assert db.query(models.Question).count() == before


def test_db_export_rejects_parent_capacity_before_writing_destination(db, tmp_path):
    _, _, _, question = _graph(db, "ParentProjectionCapacity")
    question.question = "Answer both parts."
    question.question_text = "Answer both parts.<br>a) Give the first response.<br>b) Give the second response."
    question.marks = 6
    question.answers = []
    question.sub_questions = [{
        "text": f"{label}) Give the {ordinal} response.",
        "marks": 3,
        "keywords": [{
            "answer_type": "Phrases", "weightage": 0.5,
            "keyword": f"Criterion {label}{number}.",
        } for number in range(6)],
    } for label, ordinal in (("a", "first"), ("b", "second"))]
    before = copy.deepcopy(question.sub_questions)
    destination = tmp_path / "unchanged-on-capacity-error.xlsx"
    destination.write_bytes(b"existing destination")
    with pytest.raises(writer.WorkbookCapacityError, match="12 parent rubric slots"):
        writer.write_workbook(db, dest=destination, question_ids=[question.id])
    assert destination.read_bytes() == b"existing destination"
    assert question.answers == []
    assert question.sub_questions == before

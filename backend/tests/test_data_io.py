import copy
import io

import openpyxl
import pytest

from app import bulk_import as bi
from app import config


@pytest.mark.parametrize(("path", "writer_name"), [
    ("/data/export?scope=all", "write_workbook"),
    ("/data/export/questions?ids=1", "write_workbook"),
    ("/data/export/concepts?ids=1", "write_concepts_workbook"),
    ("/data/workbook/new?subject=History", "write_subject_workbook"),
])
def test_export_cell_limit_is_an_actionable_422(
    client, monkeypatch, path, writer_name,
):
    from app.api import data as data_api

    def oversized(*_args, **_kwargs):
        raise data_api.writer.ExcelCellLimitError(
            "Excel export blocked to prevent data loss: concept_details is "
            "32,768 characters."
        )

    monkeypatch.setattr(data_api.writer, writer_name, oversized)

    response = client.get(path)

    assert response.status_code == 422
    assert response.json()["detail"].startswith(
        "Excel export blocked to prevent data loss"
    )


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_export_all_is_canonical_workbook(client):
    r = client.get("/data/export?scope=all")
    assert r.status_code == 200
    wb = openpyxl.load_workbook(io.BytesIO(r.content))
    assert bi.SHEET_OBJECTIVE in wb.sheetnames
    assert bi.SHEET_SUBJECTIVE in wb.sheetnames
    assert bi.SHEET_DESCRIPTIVE in wb.sheetnames
    ws = wb[bi.SHEET_OBJECTIVE]
    # Row 2 carries the canonical field names.
    field_row = [c.value for c in ws[2]]
    assert field_row[: len(bi.OBJECTIVE_FIELDS)] == bi.OBJECTIVE_FIELDS


def test_export_all_includes_questionless_concepts(client, first_chapter, db):
    """A DB holding only generated concepts (no assessments yet) must not
    export as an empty workbook — concept-catalog rows are emitted."""
    from app import models

    concept_titles = {
        c.concept_title for t in db.get(models.Chapter, first_chapter["id"]).topics
        for c in t.concepts
    }
    assert concept_titles

    r = client.get("/data/export?scope=all")
    wb = openpyxl.load_workbook(io.BytesIO(r.content))
    ws = wb[bi.SHEET_OBJECTIVE]
    exported = {
        str(row[13] or "") for row in ws.iter_rows(min_row=3, values_only=True)
    }  # concept_display_name column carries the clean title
    assert concept_titles <= exported


def test_generation_appends_to_output_workbook(client, first_concept):
    session = client.post("/build-assessments/sessions", json={
        "scope_type": "concept", "scope_ids": [first_concept["id"]],
    }).json()
    client.post(f"/build-assessments/sessions/{session['id']}/batches", json={
        "cognitive_skills": ["Understanding"], "difficulty_levels": ["Moderate"],
        "categories": ["Multiple Choice Question"], "question_type": "objective",
        "num_questions": 1,
    })
    client.post(f"/build-assessments/sessions/{session['id']}/generate")

    r = client.get("/data/export?scope=output")
    assert r.status_code == 200
    assert len(r.content) > 0


def test_append_only_never_overwrites(client, first_concept, db):
    """Re-running export of the same questions must not duplicate labels."""
    from app.bulk_import import writer

    # Run one generation so an output workbook exists.
    session = client.post("/build-assessments/sessions", json={
        "scope_type": "concept", "scope_ids": [first_concept["id"]],
    }).json()
    client.post(f"/build-assessments/sessions/{session['id']}/batches", json={
        "cognitive_skills": ["Applying"], "difficulty_levels": ["High"],
        "categories": ["Long Answer"], "question_type": "descriptive",
        "num_questions": 1,
    })
    from tests.conftest import stream_result
    gen = stream_result(client.post(f"/build-assessments/sessions/{session['id']}/generate"))
    ids = gen["pipeline"]
    # Append the same question ids again -> all skipped.
    again = writer.append_questions(
        db, config.BULK_IMPORT_OUTPUT,
        [q["id"] for q in client.get("/data/questions?origin=concept_mapping").json()],
    )
    assert again["skipped"] >= 1
    assert again["objective"] == again["subjective"] == 0


def test_export_questions_selection(client, first_concept):
    """Per-functionality export: download just the generated questions."""
    session = client.post("/build-assessments/sessions", json={
        "scope_type": "concept", "scope_ids": [first_concept["id"]],
    }).json()
    client.post(f"/build-assessments/sessions/{session['id']}/batches", json={
        "cognitive_skills": ["Understanding"], "difficulty_levels": ["Moderate"],
        "categories": ["Multiple Choice Question"], "question_type": "objective",
        "num_questions": 2,
    })
    from tests.conftest import stream_result
    gen = stream_result(client.post(f"/build-assessments/sessions/{session['id']}/generate"))
    ids = gen["question_ids"]
    assert len(ids) == 2

    r = client.get("/data/export/questions", params={"ids": ",".join(map(str, ids))})
    assert r.status_code == 200
    assert r.headers["content-disposition"].endswith('bulk_import_questions.xlsx"')
    wb = openpyxl.load_workbook(io.BytesIO(r.content))
    ws = wb[bi.SHEET_OBJECTIVE]
    # Header rows + exactly the two generated questions.
    assert ws.max_row == 2 + 2


def test_export_questions_requires_ids(client):
    assert client.get("/data/export/questions", params={"ids": ""}).status_code == 400
    assert client.get("/data/export/questions", params={"ids": "x"}).status_code == 400


def test_export_concepts_selection(client, first_chapter, db):
    """Per-functionality export for Build Concepts: download generated concepts."""
    from app import models

    concept_ids = [
        c.id for t in db.get(models.Chapter, first_chapter["id"]).topics
        for c in t.concepts
    ][:2]
    assert concept_ids

    r = client.get("/data/export/concepts", params={"ids": ",".join(map(str, concept_ids))})
    assert r.status_code == 200
    assert r.headers["content-disposition"].endswith('bulk_import_concepts.xlsx"')
    wb = openpyxl.load_workbook(io.BytesIO(r.content))
    ws = wb[bi.SHEET_OBJECTIVE]
    field_row = [c.value for c in ws[2]]
    assert field_row[: len(bi.OBJECTIVE_FIELDS)] == bi.OBJECTIVE_FIELDS
    # One concept-catalog row per concept (no tags here).
    assert ws.max_row == 2 + len(concept_ids)


def test_import_workbook_roundtrip(client, db):
    """Export the DB then re-import it: append-only means no new questions land."""
    from app import models

    before_export = {
        question.id: (
            copy.deepcopy(question.answers),
            copy.deepcopy(question.sub_questions),
        )
        for question in db.query(models.Question).all()
    }
    export = client.get("/data/export?scope=all")
    assert export.status_code == 200
    db.expire_all()
    after_export = {
        question.id: (
            copy.deepcopy(question.answers),
            copy.deepcopy(question.sub_questions),
        )
        for question in db.query(models.Question).all()
    }
    assert after_export == before_export
    files = {"file": ("roundtrip.xlsx", io.BytesIO(export.content),
                      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    response = client.post("/data/import", files=files)
    assert response.status_code == 200, response.text
    counts = response.json()
    # Labels already present -> questions not re-created.
    assert counts["questions"] == 0


@pytest.mark.parametrize("append_kind", ["concepts", "questions"])
def test_append_migrates_historical_cells_before_strict_reimport(
    db, tmp_path, append_kind,
):
    """Both staged append paths repair old rows on the workbook copy."""

    from app import models
    from app.bulk_import import layouts, reader, writer
    from app.services import katex_rules

    path = tmp_path / f"legacy-{append_kind}-output.xlsx"
    workbook = writer._new_workbook()

    def put(worksheet, sheet_layout, block, field, value, *, row=3):
        column = sheet_layout.column(block, field)
        assert column is not None, (sheet_layout.kind, block, field)
        worksheet.cell(row=row, column=column + 1).value = value

    def seed_front(worksheet, sheet_layout, suffix):
        put(worksheet, sheet_layout, "chapter", "chapter_title", suffix)
        put(worksheet, sheet_layout, "topic", "topic_title", "Topic")
        put(
            worksheet, sheet_layout, "topic", "pre_post_learning", "Post",
        )
        put(worksheet, sheet_layout, "concept", "concept_title", "Concept")
        put(worksheet, sheet_layout, "group", "group_name", "Basic Group 01")
        put(worksheet, sheet_layout, "group", "group_type", "Basic")

    objective_layout = writer._target_sheet("objective")
    objective = workbook[objective_layout.sheet_name]
    seed_front(objective, objective_layout, f"LEGACY-OBJ-{append_kind}")
    put(
        objective, objective_layout, "question", "question_label",
        f"LEGACY-OBJ-{append_kind}-Q1",
    )
    put(
        objective, objective_layout, "question", "question",
        "Answer A) stays prose.\nA) Alpha\nB) Beta",
    )
    put(
        objective, objective_layout, "question", "question_text",
        "Study the table.\n| Name | Value |\n|---|---:|\n| Alpha | 1 |"
        "\nA) Alpha\nB) Beta",
    )
    put(objective, objective_layout, "question", "marks", "1")
    put(
        objective, objective_layout, "question", "answer_type_1", "Phrases",
    )
    put(
        objective, objective_layout, "question", "answer_content_1",
        "[Katex] x+1 [/Katex]",
    )
    put(
        objective, objective_layout, "question", "correct_answer_1", "1",
    )
    put(
        objective, objective_layout, "question", "answer_weightage_1", "1",
    )

    descriptive_layout = writer._target_sheet("descriptive")
    descriptive = workbook[descriptive_layout.sheet_name]
    seed_front(descriptive, descriptive_layout, f"LEGACY-DES-{append_kind}")
    put(
        descriptive, descriptive_layout, "question", "question_label",
        f"LEGACY-DES-{append_kind}-Q1",
    )
    put(
        descriptive, descriptive_layout, "question", "question",
        "Explain the supplied relationship.",
    )
    put(
        descriptive, descriptive_layout, "question", "question_text",
        "Explain the supplied relationship.",
    )
    put(descriptive, descriptive_layout, "question", "marks", "1")
    put(
        descriptive, descriptive_layout, "question", "answer_type_1",
        "Phrases",
    )
    put(
        descriptive, descriptive_layout, "question", "answer_content_1",
        "One complete explanation.",
    )
    table = (
        r"[Katex] \begin{array}{cc}\text{Name}&\text{Value}\\A&1"
        r"\end{array} [/Katex]"
    )
    put(
        descriptive, descriptive_layout, "question", "display_answer",
        table,
    )
    put(
        descriptive, descriptive_layout, "question", "answer_weightage_1",
        "1",
    )
    put(
        descriptive, descriptive_layout, "question", "sub_question_1",
        table,
    )
    put(
        descriptive, descriptive_layout, "question", "sub_question_marks_1",
        "1",
    )
    put(
        descriptive, descriptive_layout, "question", "sq1_answer_type_1",
        "Phrases",
    )
    put(
        descriptive, descriptive_layout, "question", "sq1_weightage_1", "1",
    )
    put(
        descriptive, descriptive_layout, "question", "sq1_keyword_1",
        "[Katex] y^2 [/Katex]",
    )
    workbook.save(path)
    workbook.close()

    if append_kind == "concepts":
        concept_id = db.query(models.Concept.id).order_by(models.Concept.id).first()
        assert concept_id is not None
        result = writer.append_concepts(db, path, [concept_id[0]])
    else:
        # Even a no-new-row append is a publication touch of this durable
        # workbook and must migrate its historical rows before saving.
        result = writer.append_questions(db, path, [])
    assert result["legacy_cells_normalized"] > 0

    migrated = openpyxl.load_workbook(path, data_only=True)
    objective = migrated[objective_layout.sheet_name]
    objective_type = objective.cell(
        row=3,
        column=objective_layout.column("question", "answer_type_1") + 1,
    ).value
    objective_content = objective.cell(
        row=3,
        column=objective_layout.column("question", "answer_content_1") + 1,
    ).value
    assert objective_type == "Equation"
    assert "[Katex]" not in objective_content
    assert katex_rules.answer_cell_issues(
        objective_type, objective_content,
    ) == []
    migrated_question = objective.cell(
        row=3,
        column=objective_layout.column("question", "question") + 1,
    ).value
    migrated_question_text = objective.cell(
        row=3,
        column=objective_layout.column("question", "question_text") + 1,
    ).value
    assert "Answer A) stays prose." in migrated_question
    assert "\na) Alpha\nb) Beta" in migrated_question
    assert "Table row 1, column 1: Name" in migrated_question_text
    assert "|---|" not in migrated_question_text
    assert "\na) Alpha\nb) Beta" in migrated_question_text

    descriptive = migrated[descriptive_layout.sheet_name]
    answer_display = descriptive.cell(
        row=3,
        column=descriptive_layout.column("question", "display_answer") + 1,
    ).value
    sub_question = descriptive.cell(
        row=3,
        column=descriptive_layout.column("question", "sub_question_1") + 1,
    ).value
    keyword_type = descriptive.cell(
        row=3,
        column=descriptive_layout.column(
            "question", "sq1_answer_type_1",
        ) + 1,
    ).value
    keyword = descriptive.cell(
        row=3,
        column=descriptive_layout.column("question", "sq1_keyword_1") + 1,
    ).value
    migrated.close()
    assert "Table row 1, column 1: Name" in answer_display
    assert "Table row 1, column 1: Name" in sub_question
    assert keyword_type == "Equation"
    assert "[Katex]" not in keyword
    assert katex_rules.answer_cell_issues(keyword_type, keyword) == []

    # The exact workbook the append path published now clears the same strict
    # preflight used by POST /data/import.
    reader.import_workbook(db, path, strict_content=True)


def test_fresh_export_lowercases_legacy_objective_labels_on_the_copy(
    client, db,
):
    """Fresh public export is strict-importable without rewriting storage."""

    from app import models
    from app.bulk_import import writer

    question = (
        db.query(models.Question)
        .filter(models.Question.sheet_kind == "objective")
        .order_by(models.Question.id)
        .first()
    )
    assert question is not None
    before = (question.question, question.question_text)
    legacy_question = "Answer A) stays prose.\nA) Alpha\nB) Beta"
    legacy_text = (
        "Study the table.\n| Name | Value |\n|---|---:|\n| Alpha | 1 |"
        "\nA) Alpha\nB) Beta"
    )
    question.question = legacy_question
    question.question_text = legacy_text
    db.commit()
    label = question.question_label

    try:
        exported = client.get("/data/export?scope=all")
        assert exported.status_code == 200, exported.text
        db.expire_all()
        stored = db.get(models.Question, question.id)
        assert (stored.question, stored.question_text) == (
            legacy_question, legacy_text,
        )

        workbook = openpyxl.load_workbook(io.BytesIO(exported.content))
        sheet_layout = writer._target_sheet("objective")
        worksheet = workbook[sheet_layout.sheet_name]
        label_column = sheet_layout.column("question", "question_label")
        row_number = next(
            row
            for row in range(3, worksheet.max_row + 1)
            if worksheet.cell(row=row, column=label_column + 1).value == label
        )
        question_value = worksheet.cell(
            row=row_number,
            column=sheet_layout.column("question", "question") + 1,
        ).value
        text_value = worksheet.cell(
            row=row_number,
            column=sheet_layout.column("question", "question_text") + 1,
        ).value
        workbook.close()
        assert "Answer A) stays prose." in question_value
        assert "\na) Alpha\nb) Beta" in question_value
        assert "Table row 1, column 1: Name" in text_value
        assert "|---|" not in text_value
        assert "\na) Alpha\nb) Beta" in text_value

        response = client.post(
            "/data/import",
            files={
                "file": (
                    "fresh-strict-roundtrip.xlsx",
                    io.BytesIO(exported.content),
                    "application/vnd.openxmlformats-officedocument."
                    "spreadsheetml.sheet",
                ),
            },
        )
        assert response.status_code == 200, response.text
    finally:
        stored = db.get(models.Question, question.id)
        stored.question, stored.question_text = before
        db.commit()

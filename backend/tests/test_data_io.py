import io

import openpyxl

from app import bulk_import as bi
from app import config, models


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


def test_import_workbook_roundtrip(client):
    """Export the DB then re-import it: append-only means no new questions land."""
    export = client.get("/data/export?scope=all")
    files = {"file": ("roundtrip.xlsx", io.BytesIO(export.content),
                      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    counts = client.post("/data/import", files=files).json()
    # Labels already present -> questions not re-created.
    assert counts["questions"] == 0


def test_import_normalizes_and_checks_existing_concept(
    db, tmp_path,
):
    from app.bulk_import import reader, writer
    from app.services import directory

    chapter_title = "94721 Rich Text Contract"
    chapter = models.Chapter(
        chapter_code=directory.make_chapter_code(
            "CBSE", "12", "Physics", chapter_title),
        board="CBSE",
        grade="12",
        subject="Physics",
        unit="Physics Unit",
        chapter_title=chapter_title,
        chapter_display_name=chapter_title,
    )
    topic = models.Topic(
        topic_title="Existing Rich Text Topic 94721",
        topic_display_name="Existing Rich Text Topic 94721",
        pre_post_learning="Post",
    )
    concept = models.Concept(
        concept_title="Existing Rich Text Concept 94721",
        concept_display_name="Existing Rich Text Concept 94721",
        parent_concept="Rich Text",
        concept_details="Description: Legacy [katex] x^2 [/katex] notation.",
    )
    concept.groups.append(models.Group(
        group_type="Basic",
        group_name="Existing Rich Text Basic 94721",
        group_display_name="Existing Rich Text Basic 94721",
    ))
    topic.concepts.append(concept)
    chapter.topics.append(topic)
    db.add(chapter)
    db.commit()
    try:
        path = tmp_path / "existing_concept.xlsx"
        writer.append_concepts(db, path, [concept.id])
        wb = openpyxl.load_workbook(path)
        ws = wb[bi.SHEET_OBJECTIVE]
        headers = [cell.value for cell in ws[2]]
        ws.cell(
            row=3,
            column=headers.index("concept_details") + 1,
            value=(
                'Description: [img src="http://images.example/legacy.png" '
                'alt="Legacy visual"]'
            ),
        )
        group = concept.groups[0]
        ws.cell(
            row=3,
            column=headers.index("group_name") + 1,
            value=group.group_name,
        )
        ws.cell(
            row=3,
            column=headers.index("group_type") + 1,
            value=group.group_type,
        )
        wb.save(path)

        counts = reader.import_workbook(db, path)
        db.refresh(concept)
        assert "[Katex] x^2 [/Katex]" in concept.concept_details
        assert any(
            "full HTTPS src URL" in issue for issue in counts["issues"])
    finally:
        db.delete(chapter)
        db.commit()

import io

import openpyxl

from app import bulk_import as bi
from app import config


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
    gen = client.post(f"/build-assessments/sessions/{session['id']}/generate").json()
    ids = gen["pipeline"]
    # Append the same question ids again -> all skipped.
    again = writer.append_questions(
        db, config.BULK_IMPORT_OUTPUT,
        [q["id"] for q in client.get("/data/questions?origin=concept_mapping").json()],
    )
    assert again["skipped"] >= 1
    assert again["objective"] == again["subjective"] == 0


def test_import_workbook_roundtrip(client):
    """Export the DB then re-import it: append-only means no new questions land."""
    export = client.get("/data/export?scope=all")
    files = {"file": ("roundtrip.xlsx", io.BytesIO(export.content),
                      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    counts = client.post("/data/import", files=files).json()
    # Labels already present -> questions not re-created.
    assert counts["questions"] == 0

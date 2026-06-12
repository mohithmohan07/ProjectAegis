"""Multi-source tracking: concept/question dedupe across books + source merge."""
import io

import openpyxl

from app import bulk_import as bi
from app import config, models
from app.bulk_import import writer


def test_merge_sources_dedupes_case_insensitively():
    assert bi.merge_sources("NCERT", "RD Sharma") == "NCERT; RD Sharma"
    assert bi.merge_sources("NCERT; RD Sharma", "ncert") == "NCERT; RD Sharma"
    assert bi.merge_sources("", "Arihant") == "Arihant"
    assert bi.merge_sources("S Chand", "") == "S Chand"


def test_vocab_exposes_book_sources(client):
    v = client.get("/directory/vocab").json()
    assert "NCERT" in v["book_sources"]
    assert "RD Sharma" in v["book_sources"]


def test_legacy_workbook_without_concept_source_still_imports(client, db, tmp_path):
    """Old-layout files (no concept_source column) must not mis-align bands."""
    legacy_fields = (
        bi.CHAPTER_FIELDS + bi.TOPIC_FIELDS + bi.CONCEPT_FIELDS[:bi.LEGACY_CONCEPT_LEN]
        + bi.OBJECTIVE_GROUP_FIELDS + bi.OBJECTIVE_QUESTION_FIELDS
    )
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for sheet in (bi.SHEET_OBJECTIVE, bi.SHEET_SUBJECTIVE, bi.SHEET_DESCRIPTIVE):
        ws = wb.create_sheet(sheet)
        ws.append(["Chapter"])  # band row (content irrelevant)
        if sheet == bi.SHEET_OBJECTIVE:
            ws.append(legacy_fields)
        else:
            ws.append(["chapter_title"])

    ws = wb[bi.SHEET_OBJECTIVE]
    row = [""] * len(legacy_fields)
    row[0] = "Legacy Chapter (10CBMA_Legacy)"
    row[6] = "Legacy Topic"
    row[12] = "Legacy Concept Unique XYZ"
    row[21] = "10CBMA_Lgcy_PL_T01_X Q99"   # group-band label
    row[26] = "Basic"                       # group_type
    row[28] = "10CBMA_Lgcy_PL_T01_X Q99"   # question-band label
    row[29] = "Multiple Choice Question"
    row[30] = "Remembering"
    row[31] = "NCERT"
    row[36] = "Legacy unique question text 9871?"
    row[37] = 1
    ws.append(row)
    path = tmp_path / "legacy.xlsx"
    wb.save(path)

    files = {"file": ("legacy.xlsx", io.BytesIO(path.read_bytes()),
                      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    counts = client.post("/data/import", files=files).json()
    assert counts["questions"] == 1

    q = db.query(models.Question).filter_by(
        question_label="10CBMA_Lgcy_PL_T01_X Q99").one()
    assert q.question == "Legacy unique question text 9871?"
    assert q.group.concept.concept_title == "Legacy Concept Unique XYZ"
    assert q.group.group_type == "Basic"


def test_concept_resused_across_books_merges_sources(client, db, first_chapter):
    """Same concept from a second book: not duplicated, sources accumulate."""
    body = (b"## Optics Basics\n"
            b"Refraction of light through glass slabs\n"
            b"Total internal reflection in prisms")

    def upload_and_generate(book):
        files = {"file": (f"{book.replace(' ', '_')}.txt", io.BytesIO(body), "text/plain")}
        job = client.post(
            f"/build-concepts/post-learning/uploads?source_book={book}", files=files,
        ).json()
        assert job["source_book"] == book
        return client.post(
            f"/build-concepts/post-learning/uploads/{job['id']}/generate",
            json={"target_chapter_id": first_chapter["id"]},
        ).json()

    first = upload_and_generate("NCERT")
    assert first["concepts_created"] == 2
    assert first["concepts_merged"] == 0

    second = upload_and_generate("RD Sharma")
    assert second["concepts_created"] == 0
    assert second["concepts_merged"] == 2

    c = (db.query(models.Concept)
         .filter(models.Concept.concept_title.like("Refraction of light%")).one())
    assert c.sources == "NCERT; RD Sharma"


def test_duplicate_questions_across_books_merge_sources(client, db, first_chapter):
    """Same question text from another book: skipped, question_source merged."""
    body = (b"# Qs\n\n"
            b"State the law of refraction with one worked example 4417.\n\n"
            b"Define critical angle for a glass-air interface 4417.")

    def run(book):
        files = {"file": (f"q_{book.replace(' ', '_')}.txt", io.BytesIO(body), "text/plain")}
        job = client.post(
            f"/build-assessments/uploads?upload_type=questions&source_book={book}",
            files=files,
        ).json()
        client.post(f"/build-assessments/uploads/{job['id']}/deposit", json={
            "scope_type": "chapter", "scope_ids": [first_chapter["id"]],
        })
        return client.post(
            f"/build-assessments/uploads/{job['id']}/generate",
            json={"question_type": "objective"},
        ).json()

    first = run("S Chand")
    assert first["created"] == 2
    assert first["duplicates_merged"] == 0

    second = run("Arihant")
    assert second["created"] == 0
    assert second["duplicates_merged"] == 2

    q = (db.query(models.Question)
         .filter(models.Question.question.like("State the law of refraction%")).one())
    assert q.question_source == "S Chand; Arihant"


def test_output_workbook_source_cells_update_in_place(db, tmp_path, client, first_chapter):
    """Re-appending an existing concept refreshes its concept_source cell."""
    detail = client.get(f"/directory/chapters/{first_chapter['id']}").json()
    concept_id = detail["topics"][0]["concepts"][0]["id"]
    concept = db.get(models.Concept, concept_id)
    concept.sources = "NCERT"
    db.commit()

    path = tmp_path / "out.xlsx"
    first = writer.append_concepts(db, path, [concept_id])
    assert first["written"] >= 1

    concept.sources = bi.merge_sources(concept.sources, "RS Aggarwal")
    db.commit()
    db.expire_all()

    second = writer.append_concepts(db, path, [concept_id])
    assert second["written"] == 0
    assert second["sources_updated"] >= 1

    wb = openpyxl.load_workbook(path)
    ws = wb[bi.SHEET_OBJECTIVE]
    src_col = bi.OBJECTIVE_FIELDS.index("concept_source")
    values = {
        writer._cell_str(row, src_col)
        for row in ws.iter_rows(min_row=3, values_only=True)
        if writer._cell_str(row, 12) == concept.concept_title
    }
    assert "NCERT; RS Aggarwal" in values

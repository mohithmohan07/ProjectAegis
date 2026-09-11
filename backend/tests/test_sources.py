"""Concept source merging and exact question-source provenance across books."""
import copy
import io

import openpyxl
import pytest

from app import bulk_import as bi
from app import models
from app.bulk_import import layouts, reader, writer


def test_merge_sources_dedupes_case_insensitively():
    """Contract v2.0 §16: merged sources are ``" | "``-joined; legacy comma
    and semicolon lists still split on the way in."""
    assert bi.merge_sources("NCERT", "RD Sharma") == "NCERT | RD Sharma"
    # legacy "; " / ", " input normalizes onto the v2.0 pipe delimiter
    assert bi.merge_sources("NCERT; RD Sharma", "ncert") == "NCERT | RD Sharma"
    assert bi.merge_sources("NCERT, RD Sharma", "ncert") == "NCERT | RD Sharma"
    assert bi.merge_sources("", "Arihant") == "Arihant"
    assert bi.merge_sources("S Chand", "") == "S Chand"


def test_vocab_exposes_book_sources(client):
    v = client.get("/directory/vocab").json()
    assert "NCERT" in v["book_sources"]
    assert "RD Sharma" in v["book_sources"]
    # Maharashtra State Board's own textbook imprint.
    assert "Balbharati" in v["book_sources"]


def test_legacy_workbook_without_concept_source_still_imports(db, tmp_path):
    """Old-layout files (no concept_source column) must not mis-align bands.

    The header rows are built from the FROZEN registry entry, never from
    ``writer._write_headers``: that writer migrates to the reference layout in
    S7 and a fixture built from it would follow the writer instead of pinning
    the legacy layout this test exists for. All three sheets carry their real
    header — the reader's layout gate identifies a workbook, not a sheet, and
    a one-column stub is not a layout.
    """
    legacy = layouts.layout("canonical-legacy-concept-band")
    legacy_fields = list(legacy.sheet("objective").fields)
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for kind in ("objective", "subjective", "descriptive"):
        sheet_layout = legacy.sheet(kind)
        ws = wb.create_sheet(sheet_layout.sheet_name)
        ws.append(["Chapter"])  # band row (content irrelevant)
        ws.append(list(sheet_layout.fields))

    ws = wb[legacy.sheet("objective").sheet_name]
    row = [""] * len(legacy_fields)
    # Group band now has 8 fields (added group_question_labels) -> question
    # band starts one column later than the old layout.
    row[0] = "Legacy Chapter (10CBMA_Legacy)"
    row[6] = "Legacy Topic"
    row[12] = "Legacy Concept Unique XYZ"
    row[21] = "10CBMA_Lgcy_PL_T01_X Q99"   # concept_question_labels (group label)
    row[26] = "Basic"                       # group_type
    row[29] = "10CBMA_Lgcy_PL_T01_X Q99"   # question-band label
    row[30] = "Multiple Choice Question"
    row[31] = "Remembering"
    row[32] = "NCERT"
    row[37] = "Legacy unique question text 9871?"
    row[38] = 1
    ws.append(row)
    path = tmp_path / "legacy.xlsx"
    wb.save(path)

    counts = reader.import_workbook(db, path)
    assert counts["questions"] == 1

    q = db.query(models.Question).filter_by(
        question_label="10CBMA_Lgcy_PL_T01_X Q99").one()
    assert q.question == "Legacy unique question text 9871?"
    assert q.group.concept.concept_title == "Legacy Concept Unique XYZ"
    assert q.group.group_type == "Basic"


def test_concept_resused_across_books_merges_sources(
    client, db, monkeypatch,
):
    """Two reviewed releases reuse identities only on explicit publication.

    Persisted provenance accumulates while each run's export continues to
    name its own publication, including after the second book is published.
    """
    from app.services import build_concepts_release as release
    from app.services import build_concepts_terminal_release_contract as terminal
    from app.services import generation
    from tests.conftest import convert_concept_upload

    terminal.install()
    chapter = models.Chapter(
        chapter_code="10CBPH_OpticsSourceMerge", board="CBSE", grade="10",
        subject="Physics", unit="Optics", chapter_title="Optics Source Merge",
        chapter_display_name="Optics Source Merge", chapter_duration="40 minutes",
    )
    db.add(chapter)
    db.commit()
    chapter_id = chapter.id
    records = [{
        "topic": "Optics Basics",
        "concept_title": "Refraction of Light through Glass Slabs",
        "concept_details": "Description: Light changes direction at a glass boundary.",
        "keywords": "refraction",
        "_semantic_topic_id": "TOPIC-OPTICS",
    }, {
        "topic": "Optics Basics",
        "concept_title": "Total Internal Reflection in Prisms",
        "concept_details": "Description: Light reflects inside glass above the critical angle.",
        "keywords": "reflection",
        "_semantic_topic_id": "TOPIC-OPTICS",
    }]
    monkeypatch.setattr(generation, "chapter_meta_via_api", lambda **_kwargs: {
        "chapter_description": "An authored account of light crossing glass boundaries.",
        "topic_descriptions": {
            bi.normalize_question_text("Optics Basics"): "Refraction and internal reflection.",
        },
    })
    monkeypatch.setattr(
        generation, "_openai_json",
        lambda *_args, **_kwargs: pytest.fail("publication must not call a provider"),
    )
    body = (b"## Optics Basics\n"
            b"Refraction of light through glass slabs\n"
            b"Total internal reflection in prisms")

    def upload_and_stage(book):
        files = {"file": (f"{book.replace(' ', '_')}.txt", io.BytesIO(body), "text/plain")}
        job = client.post(
            f"/build-concepts/post-learning/uploads?source_book={book}", files=files,
        ).json()
        assert job["source_book"] == book
        convert_concept_upload(client, job["id"])
        db.expire_all()
        release.stage_release(
            db, db.get(models.UploadJob, job["id"]),
            target_chapter_id=chapter_id, records=records,
            inventory={"items": [], "stats": {}}, mined_types={"types": []},
        )
        return job["id"]

    def exported_rows(job_id):
        response = client.get(
            f"/build-concepts/uploads/{job_id}/release-bulk-import.xlsx"
        )
        assert response.status_code == 200
        workbook = openpyxl.load_workbook(io.BytesIO(response.content), read_only=True)
        try:
            sheet = workbook[bi.SHEET_OBJECTIVE]
            header = next(sheet.iter_rows(min_row=2, max_row=2, values_only=True))
            return [dict(zip(header, row)) for row in sheet.iter_rows(
                min_row=3, values_only=True,
            ) if any(value is not None for value in row)]
        finally:
            workbook.close()

    def concepts():
        db.expire_all()
        return (db.query(models.Concept).join(models.Topic)
                .filter(models.Topic.chapter_id == chapter_id)
                .order_by(models.Concept.id).all())

    def publish(job_id):
        response = client.post(
            f"/build-concepts/uploads/{job_id}/upload-release?lane=post"
        )
        assert response.status_code == 200, response.json()
        assert response.json()["database_uploaded"] is True
        return response.json()

    first_job = upload_and_stage("NCERT")
    assert concepts() == []
    first_export = exported_rows(first_job)
    assert len(first_export) == len(records)
    assert {row["concept_source"] for row in first_export} == {"NCERT"}
    first = publish(first_job)
    assert len(first["created_concept_ids"]) == len(records)
    assert first["updated_concept_ids"] == []
    identities = [(concept.id, concept.machine_id) for concept in concepts()]
    assert all(machine_id for _, machine_id in identities)
    assert {concept.sources for concept in concepts()} == {"NCERT"}

    second_job = upload_and_stage("RD Sharma")
    assert second_job != first_job
    assert [(concept.id, concept.machine_id) for concept in concepts()] == identities
    assert {concept.sources for concept in concepts()} == {"NCERT"}
    second_export = exported_rows(second_job)
    assert len(second_export) == len(records)
    assert {row["concept_source"] for row in second_export} == {"RD Sharma"}
    second = publish(second_job)
    assert second["created_concept_ids"] == []
    assert second["updated_concept_ids"] == first["created_concept_ids"]
    assert [(concept.id, concept.machine_id) for concept in concepts()] == identities
    assert {concept.sources for concept in concepts()} == {"NCERT | RD Sharma"}
    for job_id, book in ((first_job, "NCERT"), (second_job, "RD Sharma")):
        rows = exported_rows(job_id)
        assert len(rows) == len(records)
        assert {row["concept_source"] for row in rows} == {book}


def _run_question_source_upload(client, first_chapter, book, body):
    from tests.conftest import convert_assessment_upload, stream_result
    files = {"file": (f"q_{book.replace(' ', '_')}.txt", io.BytesIO(body), "text/plain")}
    response = client.post(
        "/build-assessments/uploads",
        params={"upload_type": "questions", "source_book": book}, files=files,
    )
    assert response.status_code == 200, response.text
    job = response.json()
    convert_assessment_upload(client, job["id"])
    client.post(f"/build-assessments/uploads/{job['id']}/deposit", json={
        "scope_type": "chapter", "scope_ids": [first_chapter["id"]],
    })
    result = stream_result(client.post(
        f"/build-assessments/uploads/{job['id']}/generate",
        json={"question_type": "objective"}))
    return job["id"], result


def test_duplicate_questions_across_books_keep_one_source_and_audit_incoming_book(client, db, first_chapter):
    """A duplicate keeps its original source; the second book is an audit receipt."""
    body = (b"# Qs\n\n"
            b"State the law of refraction with one worked example 4417.\n\n"
            b"Define critical angle for a glass-air interface 4417.")
    _, first = _run_question_source_upload(client, first_chapter, "NCERT", body)
    assert first["created"] == 2
    assert first["duplicates_merged"] == 0
    original_ids = set(first["question_ids"])
    original_labels = {qid: db.get(models.Question, qid).question_label for qid in original_ids}
    second_job_id, second = _run_question_source_upload(client, first_chapter, "Selina", body)
    assert second["created"] == 0
    assert second["duplicates_merged"] == 2
    assert set(second["question_ids"]) == original_ids
    assert second["blockers"] == []
    assert len(second["source_receipts"]) == 2
    db.expire_all()
    for qid in original_ids:
        question = db.get(models.Question, qid)
        assert question.question_source == "NCERT"
        assert question.question_label == original_labels[qid]
        receipts = question.route_audit["additional_source_receipts"]
        assert len(receipts) == 1
        receipt = receipts[0]
        assert receipt in second["source_receipts"]
        assert receipt["job_id"] == second_job_id
        assert receipt["existing_question_id"] == qid
        assert receipt["incoming_source"] == receipt["incoming_record"]["question_source"] == "Selina"
        assert receipt["original_fields"]["question_source"] == "NCERT"
        assert receipt["original_source_provenance"]["question_source"] == "NCERT"
        assert receipt["disposition"] == "retained_existing_source"
        assert receipt["errors"] == []
    second_job = db.get(models.UploadJob, second_job_id)
    assert second_job.question_inventory["_assessment_source_receipts"] == second["source_receipts"]
    assert second_job.status == "generated"


@pytest.mark.parametrize("historical_source", ["NCERT | Selina", "Unlisted publisher"])
def test_duplicate_with_unapproved_historical_source_keeps_original_and_incoming_evidence(
    client, db, first_chapter, historical_source,
):
    # The database fixture persists commits between parameter cases.
    body = f"# Qs\n\nExplain the source preservation example 88291 for {historical_source}.".encode()
    _, first = _run_question_source_upload(client, first_chapter, "NCERT", body)
    assert first["created"] == 1
    question = db.get(models.Question, first["question_ids"][0])
    question.question_source = historical_source
    question.route_audit = {}
    db.commit()
    before = {"id": question.id, "label": question.question_label, "text": question.question,
              "source": question.question_source, "audit": copy.deepcopy(question.route_audit)}
    incoming_job_id, result = _run_question_source_upload(client, first_chapter, "Selina", body)
    assert result["created"] == result["duplicates_merged"] == 0
    assert result["question_ids"] == []
    assert len(result["blockers"]) == len(result["source_receipts"]) == 1
    receipt = result["source_receipts"][0]
    assert receipt["disposition"] == "unresolved_existing_vocabulary"
    assert receipt["original_fields"]["question_source"] == historical_source
    assert receipt["incoming_source"] == receipt["incoming_record"]["question_source"] == "Selina"
    assert any("question_source" in error for error in receipt["errors"])
    db.expire_all()
    question = db.get(models.Question, before["id"])
    assert (question.question_label, question.question, question.question_source, question.route_audit) == (
        before["label"], before["text"], before["source"], before["audit"],
    )
    job = db.get(models.UploadJob, incoming_job_id)
    assert job.status == "deposited"
    assert job.question_inventory["_assessment_source_receipts"] == [receipt]


def test_unapproved_extraction_source_is_rejected_before_question_authoring(db, first_chapter, monkeypatch):
    from app.services import auth, build_assessments, generation
    job = models.UploadJob(
        owner_sub=auth.LOCAL_OWNER_SUB, module="build_assessments", filename="source_gate.txt",
        upload_type="questions", source_book="Unlisted publisher", status="deposited",
        mmd_text="Explain the source gate example 55182.",
        deposit_scope_type="chapter", deposit_scope_ids=[first_chapter["id"]],
    )
    db.add(job)
    db.commit()
    monkeypatch.setattr(generation, "identify_questions_from_mmd",
        lambda *args, **kwargs: pytest.fail("Unapproved extracted source must fail before the author"))
    with pytest.raises(ValueError, match="question_source"):
        build_assessments.generate_from_upload(db, job.id, question_type="objective")
    assert job.source_book == "Unlisted publisher"
    assert job.status == "deposited"


def test_questions_generated_from_unlisted_book_keep_generation_source_and_original_book_evidence(db, first_chapter):
    from app.services import auth, build_assessments
    job = models.UploadJob(
        owner_sub=auth.LOCAL_OWNER_SUB, module="build_assessments", filename="generated_source.txt",
        upload_type="textbook", textbook_mode="create", source_book="Unlisted publisher",
        status="deposited", mmd_text="Create an example of a light source numbered 91287.",
        deposit_scope_type="chapter", deposit_scope_ids=[first_chapter["id"]],
    )
    db.add(job)
    db.commit()
    result = build_assessments.generate_from_upload(db, job.id, question_type="objective")
    assert result["created"] == 1 and result["blockers"] == []
    question = db.get(models.Question, result["question_ids"][0])
    assert question.question_source == "UpSchool DB"
    assert question.route_audit["question_source_provenance"] == {
        "question_source": "UpSchool DB", "origin": "generated_from_upload",
        "job_id": job.id, "recorded_source_book": "Unlisted publisher",
    }
    assert job.source_book == "Unlisted publisher"


def test_output_workbook_source_cells_update_in_place(db, tmp_path, client, first_chapter):
    """Re-appending an existing concept refreshes its concept_source cell
    (contract v2.0 §16: rendered ``" | "``-joined)."""
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
        if bi.strip_title_tag(writer._cell_str(row, 12)) == concept.concept_title
    }
    assert "NCERT | RS Aggarwal" in values

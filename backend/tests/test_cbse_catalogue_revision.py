"""A supplied syllabus revision must not erase or re-key paid chapter work."""
from collections import Counter
from pathlib import Path
from shutil import copyfile

import pytest
from openpyxl import Workbook
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import config, models
from app.services import chapter_batches, directory, syllabus_import as svc


SUPPLIED = Path(__file__).parents[1] / "data/syllabus/UnitChapter_List__CBSE.xlsx"
EXPECTED_SOCIAL = [
    "Understanding Social Science",
    "Shaping of the Earth’s Surface",
    "Atmosphere and Climate",
    "Early Humans and Beginning of Civilisation",
    "State and Society Up to 1000 CE",
    "Democracy",
    "Elections",
    "Building Blocks in Economics: the Problem of Choice",
    "The Price Puzzle: What Drives the Market",
]
OLD_SOCIAL = [
    ("Introduction", "Social Science: Meaning, Scope and Importance"),
    ("Earth", "Landforms: Earth's Living Canvas"),
    ("Atmosphere", "The Dynamic Atmosphere and Changing Climate"),
    ("Civilisations", "The Earliest People: the Stone Age"),
    ("Civilisations", "Harappan and Contemporary Mesopotamian Civilisations"),
    ("Civilisations", "Egyptian and Chinese Civilisations"),
    ("Vedic Period", "The Vedic Age"),
    ("Ancient India", "Rise of Kingdoms, and Republics, and Early Empires"),
    ("Democracy", "Understanding Democracy"),
    ("Democracy", "Elections in Indian Democracy"),
    ("Economics", "Why Choices Matter: the Basics of Economics"),
    ("Economics", "Why Prices Change: the Story of Demand and Supply"),
]


def write_workbook(path, title):
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Grade 09"
    sheet.append(["Subject", "Unit", "Chapter"])
    sheet.append(["Social Science", "Introduction", title])
    workbook.save(path)


@pytest.fixture()
def catalogue(tmp_path, monkeypatch):
    bundled, runtime = tmp_path / "bundled", tmp_path / "runtime"
    bundled.mkdir()
    runtime.mkdir()
    copyfile(SUPPLIED, bundled / svc.SYLLABUS_FILES["cbse"])
    monkeypatch.setattr(config, "BUNDLED_SYLLABUS_DIR", bundled)
    monkeypatch.setattr(config, "SYLLABUS_DIR", runtime)
    engine = create_engine(f"sqlite:///{tmp_path / 'catalogue.db'}")
    models.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session, bundled, runtime
    finally:
        session.close()
        engine.dispose()


def test_supplied_catalogue_has_all_named_rows_and_correct_grade09_revision():
    rows = svc.parse_workbook(SUPPLIED, default_board="CBSE")
    assert len(rows) == 294
    assert Counter(row.grade for row in rows) == {
        "06": 52, "07": 62, "08": 57, "09": 46, "10": 77,
    }
    assert sum(row.grade == "09" and row.subject == "Mathematics" for row in rows) == 8
    assert [row.chapter for row in rows
            if row.grade == "09" and row.subject == "Social Science"] == EXPECTED_SOCIAL


def test_catalogue_upgrade_keeps_historical_ids_codes_jobs_and_leases(catalogue):
    db, _bundled, _runtime = catalogue
    svc.upsert_chapters(db, [
        svc.SyllabusRow("CBSE", "09", "Social Science", unit, title)
        for unit, title in OLD_SOCIAL
    ])
    old = db.query(models.Chapter).order_by(models.Chapter.id).all()
    old_identity = {chapter.id: (chapter.chapter_code, chapter.chapter_title, chapter.unit)
                    for chapter in old}
    job = models.UploadJob(module="build_concepts", filename="already-paid.pdf", status="generating",
                           generation_checkpoint={"kept": "unchanged"})
    db.add(job)
    db.flush()
    batch = models.ChapterBatchRow(chapter_id=old[0].id, job_id=job.id,
                                  source_filename="already-paid.pdf")
    db.add(batch)
    db.flush()
    task = models.ChapterBatchTask(batch_row_id=batch.id, state="leased",
                                   lease_owner="old-process", attempt=1)
    db.add(task)
    db.add(models.Topic(chapter_id=old[1].id, topic_title="Existing content"))
    # Shared language and a different grade stay visible and untouched.
    language = models.Chapter(chapter_code="09CBEL_Language", board="CBSE", grade="09",
                              subject="English Language", chapter_title="Determiners")
    other_grade = models.Chapter(chapter_code="05CBSC_Custom", board="CBSE", grade="05",
                                 subject="Science", chapter_title="Existing Grade 5")
    db.add_all([language, other_grade])
    db.commit()
    binding = (batch.id, batch.chapter_id, batch.job_id, task.id, task.lease_owner, task.attempt)

    result = svc.bootstrap_syllabus(db)
    assert result["catalogue_active"] == 294
    assert result["catalogue_retired"] == 12
    assert {chapter.id: (chapter.chapter_code, chapter.chapter_title, chapter.unit)
            for chapter in db.query(models.Chapter).filter(models.Chapter.id.in_(old_identity))} == old_identity
    assert all(chapter.catalogue_active is False for chapter in old)
    assert (batch.id, batch.chapter_id, batch.job_id, task.id, task.lease_owner, task.attempt) == binding
    assert job.generation_checkpoint == {"kept": "unchanged"}
    assert task.state == "leased"
    assert language.catalogue_active is True
    assert other_grade.catalogue_active is True
    assert db.query(models.Topic).filter_by(chapter_id=old[1].id).count() == 1

    current = chapter_batches.list_page(db, board="CBSE", grade="09", subject="Social Science")
    assert [row["chapter_title"] for row in current["items"]] == EXPECTED_SOCIAL
    history = chapter_batches.list_page(db, board="CBSE", grade="09",
                                       subject="Social Science", catalogue="history")
    assert history["total"] == 12
    assert any(row["job_id"] == job.id for row in history["items"])
    assert chapter_batches.list_page(db, board="CBSE", grade="09",
                                    subject="Social Science", catalogue="all")["total"] == 21
    assert chapter_batches.project_one(db, old[0].id)["job_id"] == job.id
    assert not any(ch["id"] in old_identity for board in directory.tree(db)
                   for grade in board["grades"] for subject in grade["subjects"]
                   for unit in subject["units"] for ch in unit["chapters"])
    all_tree = directory.tree(db, include_history=True)
    assert any(ch["id"] == old[0].id for board in all_tree for grade in board["grades"]
               for subject in grade["subjects"] for unit in subject["units"] for ch in unit["chapters"])

    snapshot = [(c.id, c.chapter_code, c.catalogue_active, c.catalogue_order)
                for c in db.query(models.Chapter).order_by(models.Chapter.id)]
    again = svc.bootstrap_syllabus(db)
    assert again["created"] == 0
    assert again["catalogue_retired"] == 0
    assert [(c.id, c.chapter_code, c.catalogue_active, c.catalogue_order)
            for c in db.query(models.Chapter).order_by(models.Chapter.id)] == snapshot


def test_source_order_follows_supplied_chapters_not_alphabetical_units(catalogue):
    db, _bundled, _runtime = catalogue
    svc.bootstrap_syllabus(db)
    page = chapter_batches.list_page(db, board="CBSE", grade="06", subject="Mathematics")
    assert [row["chapter_title"] for row in page["items"]] == [
        "Patterns in Mathematics", "Lines and Angles", "Number Play",
        "Data Handling and Presentation", "Prime Time", "Perimeter and Area",
        "Fractions", "Playing with Constructions", "Symmetry", "The Other Side of Zero",
    ]


def test_legacy_runtime_names_cannot_union_or_shadow_supplied_revision(catalogue):
    db, bundled, runtime = catalogue
    write_workbook(runtime / svc.SYLLABUS_FILES["cbse"], "Old same-name revision")
    write_workbook(runtime / "Unit-Chapter List_ CBSE.xlsx", "Old alternate-name revision")
    assert svc._discover_workbooks() == [bundled / svc.SYLLABUS_FILES["cbse"]]
    svc.bootstrap_syllabus(db)
    assert chapter_batches.list_page(db, board="CBSE", grade="09",
                                    subject="Social Science")["total"] == 9


def test_explicit_uploaded_revision_is_durable_and_a_new_bundle_supersedes_it(catalogue):
    db, bundled, runtime = catalogue
    svc.bootstrap_syllabus(db)
    upload = runtime / "catalogue_uploads" / "Unit-Chapter List_ CBSE.xlsx"
    write_workbook(upload, "Explicit next revision")
    digest = svc.activate_cbse_upload(upload)
    active = svc._active_cbse_source()
    assert active.parent.name == digest
    assert svc._discover_workbooks() == [active]
    svc.bootstrap_syllabus(db)
    page = chapter_batches.list_page(db, board="CBSE", grade="09", subject="Social Science")
    assert [row["chapter_title"] for row in page["items"]] == ["Explicit Next Revision"]
    assert page["items"][0]["catalogue_revision"] == digest
    assert chapter_batches.list_page(db, catalogue="history", page_size=200)["total"] == 294
    # The on-disk manifest still selects the same revision on the next boot.
    assert svc.bootstrap_syllabus(db)["created"] == 0
    write_workbook(bundled / svc.SYLLABUS_FILES["cbse"], "Owner supplied later bundle")
    assert svc._active_cbse_source() == bundled / svc.SYLLABUS_FILES["cbse"]


def test_additive_migration_keeps_existing_chapter_identity(tmp_path, monkeypatch):
    from app import db as database

    engine = create_engine(f"sqlite:///{tmp_path / 'old-schema.db'}")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE chapters (id INTEGER PRIMARY KEY, chapter_code VARCHAR(64))")
        connection.exec_driver_sql("INSERT INTO chapters VALUES (17, 'old-code')")
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "DB_URL", "sqlite://")
    database._ensure_columns()
    database._ensure_columns()
    with engine.connect() as connection:
        row = connection.exec_driver_sql(
            "SELECT id, chapter_code, catalogue_active, catalogue_source, "
            "catalogue_revision, catalogue_order FROM chapters").one()
        assert tuple(row) == (17, "old-code", 1, "", "", 0)
    engine.dispose()


def test_api_upload_registers_one_revision_without_overwriting_the_bundle(catalogue, client):
    from app.db import get_db
    from app.main import app

    db, bundled, runtime = catalogue
    svc.bootstrap_syllabus(db)
    source = bundled / svc.SYLLABUS_FILES["cbse"]
    original_bytes = source.read_bytes()
    incoming = runtime / "incoming.xlsx"
    write_workbook(incoming, "A newly supplied chapter")
    def session_override():
        yield db
    app.dependency_overrides[get_db] = session_override
    try:
        response = client.post("/data/syllabus/upload", files={
            "files": (svc.SYLLABUS_FILES["cbse"], incoming.read_bytes(),
                      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        })
        assert response.status_code == 200, response.text
        assert source.read_bytes() == original_bytes
        assert response.json()["catalogue_active"] == 1
        current = client.get("/chapter-batches?board=CBSE&grade=09&subject=Social%20Science").json()
        assert [row["chapter_title"] for row in current["items"]] == ["A Newly Supplied Chapter"]
        history = client.get("/chapter-batches?catalogue=history&board=CBSE&grade=09&subject=Social%20Science").json()
        assert history["total"] == 9
        assert all(not row["catalogue_active"] for row in history["items"])
    finally:
        app.dependency_overrides.pop(get_db, None)

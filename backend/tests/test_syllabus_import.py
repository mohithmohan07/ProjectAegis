"""Tests for syllabus structure import."""
from pathlib import Path

import pytest
from openpyxl import Workbook

from app import models
from app.services import syllabus_import as svc


def _write_xlsx(path: Path, rows: list[list], sheet_name: str = "Sheet1") -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_name
    for row in rows:
        ws.append(row)
    wb.save(path)


@pytest.fixture()
def cbse_xlsx(tmp_path):
    path = tmp_path / "cbse.xlsx"
    _write_xlsx(path, [
        ["Grade", "Subject", "Unit", "Chapter"],
        ["6", "Mathematics", "Number Systems", "Knowing Our Numbers"],
        ["6", "Mathematics", "Number Systems", "Whole Numbers"],
        ["6", "Science", "Materials", "Sorting Materials Into Groups"],
    ])
    return path


@pytest.fixture()
def english_xlsx(tmp_path):
    path = tmp_path / "english.xlsx"
    _write_xlsx(path, [
        ["Grade", "Unit", "Chapter"],
        ["8", "Reading Skills", "Comprehension Passages"],
        ["8", "Writing Skills", "Formal Letters"],
    ])
    return path


def test_parse_workbook_cbse(cbse_xlsx):
    rows = svc.parse_workbook(cbse_xlsx, default_board="CBSE")
    assert len(rows) == 3
    assert rows[0].board == "CBSE"
    assert rows[0].grade == "06"
    assert rows[0].subject == "Mathematics"
    assert rows[0].unit == "Number Systems"
    assert rows[0].chapter == "Knowing Our Numbers"


def test_english_universal_across_boards(english_xlsx):
    rows = svc.parse_workbook(
        english_xlsx,
        default_subject="English Language",
        universal_boards=svc.ALL_SYLLABUS_BOARDS,
    )
    boards = {r.board for r in rows}
    assert boards == set(svc.ALL_SYLLABUS_BOARDS)
    assert all(r.subject == "English Language" for r in rows)
    assert len(rows) == len(svc.ALL_SYLLABUS_BOARDS) * 2


def test_upsert_chapters_creates_shells(db, cbse_xlsx):
    before = db.query(models.Chapter).count()
    rows = svc.parse_workbook(cbse_xlsx, default_board="CBSE")
    result = svc.upsert_chapters(db, rows)
    assert result["created"] == 3
    assert db.query(models.Chapter).count() == before + 3
    ch = db.query(models.Chapter).filter_by(
        board="CBSE", chapter_title="Knowing Our Numbers",
    ).one()
    assert ch.board == "CBSE"
    assert ch.grade == "06"
    assert ch.unit == "Number Systems"
    assert ch.topics == []
    # Remove test rows so later tests keep the seeded fixture tree stable.
    db.query(models.Chapter).filter(
        models.Chapter.chapter_title.in_(
            ["Knowing Our Numbers", "Whole Numbers", "Sorting Materials Into Groups"],
        ),
    ).delete(synchronize_session=False)
    db.commit()


def test_upsert_skips_duplicates(db, cbse_xlsx):
    rows = svc.parse_workbook(cbse_xlsx, default_board="CBSE")
    svc.upsert_chapters(db, rows)
    again = svc.upsert_chapters(db, rows)
    assert again["created"] == 0
    assert again["skipped"] == 3


def test_bootstrap_syllabus_only_when_empty(db, cbse_xlsx, monkeypatch):
    import app.config as cfg

    db.query(models.Chapter).delete()
    db.commit()

    syllabus_dir = cbse_xlsx.parent
    monkeypatch.setattr(cfg, "BUNDLED_SYLLABUS_DIR", syllabus_dir)
    monkeypatch.setattr(cfg, "SYLLABUS_DIR", syllabus_dir)
    monkeypatch.setattr(svc, "_discover_workbooks", lambda: [cbse_xlsx])

    result = svc.bootstrap_syllabus(db)
    assert result is not None
    assert result["created"] == 3
    assert svc.bootstrap_syllabus(db) is None

    db.query(models.Chapter).delete()
    db.commit()
    # Restore shared session fixture data for downstream tests.
    from tests.conftest import _load_test_fixtures
    _load_test_fixtures()


def test_imported_chapter_display_names_carry_no_codes(db, cbse_xlsx):
    """Display names are the human-readable half of each band.

    The *_title columns carry the code tag; the *_display_name columns stay
    clean, the same rule the workbook writer follows for topics and concepts.
    Chapters were storing the tagged form, so the directory API served
    "Determiners (06_English_Language_CBSE)" wherever a display name showed.
    """
    import re

    rows = svc.parse_workbook(cbse_xlsx, default_board="CBSE")
    svc.upsert_chapters(db, rows)

    chapter = db.query(models.Chapter).filter_by(
        chapter_title="Knowing Our Numbers").one()
    assert chapter.chapter_display_name == "Knowing Our Numbers"
    tagged = [
        c for c in db.query(models.Chapter).all()
        if re.search(r"\([A-Za-z0-9]+(?:_[A-Za-z0-9]+)+\)",
                     c.chapter_display_name or "")
    ]
    assert tagged == []


def test_english_workbook_routes_regardless_of_name_separators(tmp_path):
    """A separator change in the filename must not reroute a workbook.

    The English Language sheet carries only Unit + Chapter and is replicated
    across every board and grade by a dedicated parser. Routing matched the
    literal "english language", so re-exporting the file as
    "English_Language_Units_and_Chapters.xlsx" sent it to the generic parser,
    which found no board or grade and silently dropped all 580 chapters.
    """
    rows = [
        ["Unit", "Chapter Name"],
        ["Grammar", "Determiners"],
        ["Grammar", "Modals"],
    ]
    for name in (
        "English Language Units and Chapters.xlsx",
        "English_Language_Units_and_Chapters.xlsx",
        "English-Language-Units-and-Chapters.xlsx",
    ):
        path = tmp_path / name
        _write_xlsx(path, rows)
        parsed = svc.parse_workbook(path)
        assert parsed, f"{name} produced no rows"
        # Replicated across every board and grade.
        assert {r.board for r in parsed} == set(svc.ALL_SYLLABUS_BOARDS)
        assert {r.grade for r in parsed} == set(svc.ENGLISH_LANGUAGE_GRADES)
        assert {r.subject for r in parsed} == {"English Language"}
        assert {r.chapter for r in parsed} == {"Determiners", "Modals"}


def test_bundled_syllabus_filenames_all_route_to_a_real_parser(tmp_path):
    """Every shipped workbook name resolves to board-tagged rows."""
    for key, filename in svc.SYLLABUS_FILES.items():
        options = svc._infer_file_options(filename)
        if key == "english_language":
            assert options.get("universal_boards"), filename
        else:
            assert options.get("default_board"), filename

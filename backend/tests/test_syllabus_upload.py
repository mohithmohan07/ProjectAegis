"""Tests for syllabus upload API."""
from io import BytesIO

import pytest
from openpyxl import Workbook

from app import models


def _xlsx_bytes(rows: list[list]) -> bytes:
    wb = Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_upload_syllabus_populates_tree(client, db):
    before = db.query(models.Chapter).count()
    data = _xlsx_bytes([
        ["Grade", "Subject", "Unit", "Chapter"],
        ["8", "Social Science", "India and the World", "Reshaping India's Political Map"],
    ])
    r = client.post(
        "/data/syllabus/upload",
        files={"files": ("Unit-Chapter List_ CBSE.xlsx", data, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["created"] >= 1

    tree = client.get("/directory/tree").json()
    assert tree, "tree should not be empty after syllabus upload"
    assert db.query(models.Chapter).count() >= before + 1

    # Clean up test chapter so fixture tree stays stable.
    db.query(models.Chapter).filter_by(
        chapter_title="Reshaping India's Political Map",
    ).delete(synchronize_session=False)
    db.commit()

"""Unversioned workbook import preserves both accepted English registries."""
from __future__ import annotations

import json

import pytest

from app import models
from app.bulk_import import assessment_workbook as workbook
from app.bulk_import import reader, writer
from app.services import column_spec
from tests.test_owner_column_spec import _english_snapshot, _profile


def _english_workbook(tag: str):
    snapshot = json.loads(json.dumps(_english_snapshot()).replace(
        "06MSMA_T01_Shapes", f"06MSEN_T01_{tag.capitalize()}",
    ))
    snapshot["chapter"]["chapter_title"] = (
        f"A choice {tag} (06_English_MSBSHSE_Balbharati)"
    )
    snapshot["chapter"]["chapter_display_name"] = f"A choice {tag}"
    profile = _profile()
    if tag == "creativity":
        # A persisted pre-amendment release retains its own format policy.
        profile.pop(column_spec.POLICY_KEY)
        snapshot["candidates"][0]["answer_explanation"] = (
            snapshot["candidates"][0]["answer_explanation"].removeprefix("a) ")
        )
    descriptive = snapshot["candidates"][1]
    for part in descriptive["sub_questions"]:
        for criterion in part["keywords"]:
            criterion["keyword"] = criterion["keyword"].replace(
                "[content]:", f"[{tag}]:",
            )
    data, _ = workbook.render_master_file(snapshot, profile)
    return data, descriptive


@pytest.mark.parametrize("tag", ["creative", "creativity"])
def test_current_and_legacy_english_tags_survive_strict_import_and_export(
    db, tmp_path, tag,
):
    data, descriptive = _english_workbook(tag)
    source = tmp_path / f"{tag}.xlsx"
    source.write_bytes(data)
    counts = reader.import_workbook(db, source, strict_content=True)
    assert counts["questions"] == 2
    assert not any("functional tag" in issue for issue in counts["issues"])

    question = db.query(models.Question).filter_by(
        question_label=descriptive["question_label"],
    ).one()
    assert question.answers == []
    assert all(
        criterion["keyword"].startswith(f"[{tag}]: ")
        for part in question.sub_questions for criterion in part["keywords"]
    )
    assert writer._complete_subquestion_scoring(
        question.sub_questions, question.marks,
    )
    exported = writer.write_workbook(db, question_ids=[question.id])
    parsed = workbook.parse_workbook(exported)
    row = parsed["sheets"]["Descriptive"]["rows"][0]
    assert row["sq1_keyword_1"].startswith(f"[{tag}]: ")
    assert row["answer_content_1"] == row["sq1_keyword_1"]
    assert row["answer_weightage_1"] == row["sq1_weightage_1"]
    source.write_bytes(exported)
    # The complete public pre-write validation also applies to a re-upload.
    reader.import_workbook(db, source, strict_content=True)


def test_malformed_current_tag_blocks_import_before_database_changes(db, tmp_path):
    import io

    import openpyxl

    data, _ = _english_workbook("creative")
    book = openpyxl.load_workbook(io.BytesIO(data))
    sheet = book["Descriptive"]
    headers = [cell.value for cell in sheet[2]]
    criterion = sheet.cell(row=3, column=headers.index("sq1_keyword_1") + 1)
    criterion.value = criterion.value.replace("[creative]:", "[creative]")
    source = tmp_path / "malformed-tag.xlsx"
    book.save(source)
    book.close()
    before = db.query(models.Question).count()
    with pytest.raises(reader.WorkbookContentError, match="functional tag|rubric-tag"):
        reader.import_workbook(db, source, strict_content=True)
    assert db.query(models.Question).count() == before

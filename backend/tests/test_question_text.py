"""Bulk Import workflow spec: question_text, comma-only multi-values,
cognitive-skill normalization, standard values, validation report."""
import io

import openpyxl

from app import bulk_import as bi
from app import models
from app.db import _backfill_and_normalize
from app.services import generation


# ---------------------- multi-value parsing (comma only) ---------------------- #

def test_multi_value_comma_only():
    assert bi.split_multi("Remember, Understand") == ["Remember", "Understand"]
    assert bi.split_multi("Pre-test, Post-test, Worksheet") == [
        "Pre-test", "Post-test", "Worksheet"]
    # newline / semicolon / pipe are NOT separators — they stay inside the value.
    assert bi.split_multi("Remember\nUnderstand") == ["Remember\nUnderstand"]
    assert bi.split_multi("Remember; Understand") == ["Remember; Understand"]
    assert bi.split_multi("Remember | Understand") == ["Remember | Understand"]


def test_newlines_preserved_in_plain_text():
    s = "Line one.\nLine two with [katex] F = ma [/katex] kept."
    out = bi.to_plain_text(s)
    assert "\n" in out
    assert "F = ma" in out
    assert "[katex]" not in out


# ------------------------ cognitive skill normalization ----------------------- #

def test_cognitive_normalization_map():
    cases = {
        "Remembering": "Remember", "Understanding": "Understand",
        "Applying": "Apply", "Analysing": "Analyse",
        "Evaluating": "Evaluate", "Creating": "Create",
        "Remember": "Remember",
    }
    for old, new in cases.items():
        assert bi.normalize_cognitive_skills(old) == new
    # Multi-value (comma) normalizes element-wise.
    assert bi.normalize_cognitive_skills("Remembering, Understanding") == \
        "Remember, Understand"


def test_batch_normalizes_old_cognitive_values(client, first_concept):
    s = client.post("/build-assessments/sessions", json={
        "scope_type": "concept", "scope_ids": [first_concept["id"]],
    }).json()
    batch = client.post(f"/build-assessments/sessions/{s['id']}/batches", json={
        "cognitive_skills": ["Remembering", "Understanding"],
        "difficulty_levels": ["Less"],
        "categories": ["Multiple Choice Question"],
        "question_type": "objective", "num_questions": 1,
    }).json()
    assert batch["cognitive_skills"] == ["Remember", "Understand"]


def test_other_standard_value_normalizers():
    assert bi.normalize_appears_in("Pre/Post-Worksheet/Test") == \
        "Pre-test, Post-test, Worksheet, Test"
    assert bi.normalize_appears_in("Pre-test, Worksheet") == "Pre-test, Worksheet"
    assert bi.normalize_answer_type("Words") == "Phrases"
    assert bi.normalize_answer_type("Equation") == "Equation"


# ------------------------------ question_text -------------------------------- #

def test_generated_questions_populate_question_text(client, first_concept, db):
    s = client.post("/build-assessments/sessions", json={
        "scope_type": "concept", "scope_ids": [first_concept["id"]],
    }).json()
    for q_type in ("objective", "subjective", "descriptive"):
        client.post(f"/build-assessments/sessions/{s['id']}/batches", json={
            "cognitive_skills": ["Apply"], "difficulty_levels": ["Moderate"],
            "categories": [], "question_type": q_type, "num_questions": 1,
        })
    from tests.conftest import stream_result
    gen = stream_result(client.post(f"/build-assessments/sessions/{s['id']}/generate"))
    assert gen["created"] == 3
    ids = client.get(f"/build-assessments/sessions/{s['id']}").json()[
        "generated_question_ids"]
    for qid in ids:
        q = db.get(models.Question, qid)
        assert q.question_text, f"question_text empty for {q.sheet_kind}"
        assert "[katex]" not in q.question_text  # plain text, not markup


def test_export_workbook_has_question_text_as_last_column(client):
    r = client.get("/data/export?scope=all")
    wb = openpyxl.load_workbook(io.BytesIO(r.content))
    for kind, sheet in bi.SHEET_BY_KIND.items():
        ws = wb[sheet]
        header = [c.value for c in ws[2]]
        assert header[len(bi.FIELDS_BY_KIND[kind]) - 1] == "question_text"
    # Only the 3 supported content sheets + doc link — no new sheets.
    assert set(wb.sheetnames) == {
        bi.SHEET_OBJECTIVE, bi.SHEET_SUBJECTIVE, bi.SHEET_DESCRIPTIVE,
        bi.SHEET_DOC_LINK,
    }


def test_legacy_import_backfills_question_text(client, db, tmp_path):
    """Template WITHOUT question_text imports safely; backfill = plain question."""
    legacy_fields = (
        bi.CHAPTER_FIELDS + bi.TOPIC_FIELDS + bi.CONCEPT_FIELDS[:bi.LEGACY_CONCEPT_LEN]
        + bi.OBJECTIVE_GROUP_FIELDS
        + [f for f in bi.OBJECTIVE_FIELDS[
            len(bi.CHAPTER_FIELDS) + len(bi.TOPIC_FIELDS) + len(bi.CONCEPT_FIELDS)
            + len(bi.OBJECTIVE_GROUP_FIELDS):] if f != "question_text"]
    )
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for sheet in (bi.SHEET_OBJECTIVE, bi.SHEET_SUBJECTIVE, bi.SHEET_DESCRIPTIVE):
        ws = wb.create_sheet(sheet)
        ws.append(["Chapter"])
        ws.append(legacy_fields if sheet == bi.SHEET_OBJECTIVE else ["chapter_title"])
    ws = wb[bi.SHEET_OBJECTIVE]
    row = [""] * len(legacy_fields)
    row[0] = "Legacy QT Chapter (09CBPH_LegacyQT)"
    row[6] = "Legacy QT Topic"
    row[12] = "Legacy QT Concept"
    # Group band now has 8 fields (added group_question_labels) -> question
    # band starts one column later than the old layout.
    row[21] = "09CBPH_LgQT_PL_T01_X Q01"   # concept_question_labels (group label)
    row[26] = "Basic"                       # group_type
    row[29] = "09CBPH_LgQT_PL_T01_X Q01"   # question_label
    row[31] = "Remembering"                 # cognitive — old value, must normalize
    row[37] = "State [katex] v = u + at [/katex] in words."  # question
    row[38] = 1                             # marks
    ws.append(row)
    path = tmp_path / "legacy_qt.xlsx"
    wb.save(path)

    files = {"file": ("legacy_qt.xlsx", io.BytesIO(path.read_bytes()),
                      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    counts = client.post("/data/import", files=files).json()
    assert counts["questions"] == 1

    q = db.query(models.Question).filter_by(
        question_label="09CBPH_LgQT_PL_T01_X Q01").one()
    assert q.cognitive_skills == "Remember"          # normalized on import
    assert q.question_text == "State v = u + at in words."  # backfilled, plain
    assert q.question_appears_in == "Pre-test, Post-test, Worksheet, Test"


def test_db_backfill_for_existing_questions(db, first_concept):
    """Existing DB rows without question_text are backfilled, never overwritten."""
    concept = db.get(models.Concept, first_concept["id"])
    group = concept.groups[0]
    q = models.Question(
        group_id=group.id, sheet_kind="objective",
        question_label="BACKFILL TEST Q01",
        question="What is [katex] E = mc^2 [/katex]?", question_text="",
        cognitive_skills="Evaluating",
        question_appears_in="Pre/Post-Worksheet/Test",
        answers=[{"answer_type": "Words", "answer_content": "x",
                  "correct_answer": "Yes", "answer_weightage": "1"}],
    )
    db.add(q)
    db.commit()
    qid = q.id

    _backfill_and_normalize()

    db.expire_all()
    q2 = db.get(models.Question, qid)
    assert q2.question_text == "What is E = mc^2?"
    assert q2.cognitive_skills == "Evaluate"
    assert q2.question_appears_in == "Pre-test, Post-test, Worksheet, Test"
    assert q2.answers[0]["answer_type"] == "Phrases"

    # Re-running never overwrites an existing value.
    q2.question_text = "Custom evaluator context."
    db.commit()
    _backfill_and_normalize()
    db.expire_all()
    assert db.get(models.Question, qid).question_text == "Custom evaluator context."


# --------------------------- context handling -------------------------------- #

def test_context_attached_to_question_text():
    mmd = (
        "# Source\n\n"
        "Rahul and Meera discuss how shadows form at noon and at dusk.\n\n"
        "Based on the above passage, explain why shadows are shortest at noon."
    )
    records = generation.identify_questions_from_mmd(
        mmd, upload_type="questions", question_type="subjective", live=False)
    target = next(r for r in records if "shortest at noon" in r["question"])
    assert target["question_text"].startswith("Context:")
    assert "Rahul and Meera" in target["question_text"]


# ------------------------------ validation ----------------------------------- #

def test_import_validation_reports_issues(client, tmp_path):
    fields = bi.OBJECTIVE_FIELDS
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for sheet in (bi.SHEET_OBJECTIVE, bi.SHEET_SUBJECTIVE, bi.SHEET_DESCRIPTIVE):
        ws = wb.create_sheet(sheet)
        ws.append(["Chapter"])
        ws.append(list(fields) if sheet == bi.SHEET_OBJECTIVE else ["chapter_title"])
    ws = wb[bi.SHEET_OBJECTIVE]
    row = [""] * len(fields)
    row[0] = "Validation Chapter (09CBPH_Validate)"
    row[6] = "T"
    row[12] = "C"
    row[26] = "Basic"
    row[fields.index("question_label") + 22] = ""  # noqa — label set below
    q_start = len(bi.CHAPTER_FIELDS) + len(bi.TOPIC_FIELDS) + len(bi.CONCEPT_FIELDS) \
        + len(bi.OBJECTIVE_GROUP_FIELDS)
    row[21] = "09CBPH_Val_PL_T01_X Q01"
    row[q_start] = "09CBPH_Val_PL_T01_X Q01"
    row[q_start + 2] = "Memorising"                 # unknown skill -> flagged
    row[q_start + 7] = "Extreme"                    # unknown difficulty -> flagged
    row[q_start + 8] = "Compute $$x^2$$ quickly"    # raw $$ -> flagged
    row[q_start + 9] = "abc"                        # marks not numeric -> flagged
    ws.append(row)
    path = tmp_path / "validate.xlsx"
    wb.save(path)

    files = {"file": ("validate.xlsx", io.BytesIO(path.read_bytes()),
                      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    counts = client.post("/data/import", files=files).json()
    issues = "\n".join(counts["issues"])
    assert "unknown cognitive skill" in issues
    assert "unknown level_of_difficulty" in issues
    assert "$$" in issues
    assert "marks not numeric" in issues

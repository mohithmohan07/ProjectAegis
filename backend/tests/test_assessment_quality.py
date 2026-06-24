"""Assessment-creation quality: prompt assembly, variety, rubrics, review."""
from app import models
from app.services import assessment_prompts as ap
from app.services import generation


# ----------------------------- prompt assembly ----------------------------- #

def test_prompt_assembly_combines_all_blocks():
    p = ap.build_prompt(
        question_type="descriptive", difficulty="High", skill="Evaluate",
        subject="Science", grade="08", board="CBSE", marks=5,
        category="Long Answer", purpose="Pre-test, Test",
    )
    assert "DIFFICULTY: HIGH" in p
    assert "COGNITIVE SKILL: EVALUATE" in p
    assert "QUESTION TYPE: DESCRIPTIVE" in p
    assert "SUBJECT CREATIVITY (Science)" in p
    assert "PURPOSE (Pre-test)" in p and "PURPOSE (Test)" in p
    assert "RUBRIC PLACEMENT" in p
    assert "CREATIVITY AND VARIETY" in p
    assert "justified judgment" in p  # High+Evaluate matrix line


def test_prompt_differs_per_difficulty_and_skill():
    a = ap.build_prompt(question_type="objective", difficulty="Less", skill="Remember")
    b = ap.build_prompt(question_type="objective", difficulty="High", skill="Analyse")
    assert a != b
    assert "direct recall" in a.lower()
    assert "error analysis" in b.lower() or "inference" in b.lower()


def test_unnatural_combo_flagged_but_allowed():
    p = ap.build_prompt(question_type="objective", difficulty="High", skill="Create")
    assert "usually not ideal" in p


# ------------------------- dry generation variety -------------------------- #

def _concept(db, first_concept):
    return db.get(models.Concept, first_concept["id"])


def test_stem_variety_within_batch(db, first_concept):
    concept = _concept(db, first_concept)
    recs = generation.generate_questions_for_concept(
        concept, question_type="subjective", cognitive_skill="Understand",
        difficulty="Moderate", category="Short Answer", count=5,
        start_index=1, live=False,
    )
    openers = {r["question"].split()[0].lower() for r in recs}
    assert len(openers) >= 3, f"stems too repetitive: {openers}"
    report = ap.stem_monotony_report([r["question"] for r in recs])
    assert not report["monotonous"], report


def test_monotony_report_detects_repetition():
    qs = ["Define osmosis.", "Define diffusion.", "Define turgor.", "Define plasmolysis."]
    assert ap.stem_monotony_report(qs)["monotonous"]
    varied = ["Identify the process...", "A student claims...",
              "Compare the two cases...", "Predict what happens..."]
    assert not ap.stem_monotony_report(varied)["monotonous"]


# --------------------------- rubric quality (dry) --------------------------- #

def test_subjective_rubric_markwise_and_sums_to_marks(db, first_concept):
    concept = _concept(db, first_concept)
    rec = generation.generate_questions_for_concept(
        concept, question_type="subjective", cognitive_skill="Apply",
        difficulty="Moderate", category="Short Answer", count=1, live=False,
    )[0]
    weights = [float(a["weightage"]) for a in rec["answers"]]
    assert sum(weights) == rec["marks"]
    assert all("mark:" in a["answer"] for a in rec["answers"])


def test_descriptive_rubric_placement(db, first_concept):
    concept = _concept(db, first_concept)
    rec = generation.generate_questions_for_concept(
        concept, question_type="descriptive", cognitive_skill="Evaluate",
        difficulty="High", category="Long Answer", count=1, live=False,
    )[0]
    # display_answer = model answer (not "Yes"); answer_content = rubric points.
    assert len(rec["display_answer"]) > 20
    weights = [float(a["answer_weightage"]) for a in rec["answers"]]
    assert sum(weights) == rec["marks"]
    assert all(a["answer_content"].startswith("1 mark:") for a in rec["answers"])
    # Subparts stay inside the question — labelled (a), (b).
    assert rec["sub_questions"][0]["text"].startswith("(a)")
    assert rec["sub_questions"][1]["text"].startswith("(b)")


def test_objective_mcq_quality(db, first_concept):
    concept = _concept(db, first_concept)
    rec = generation.generate_questions_for_concept(
        concept, question_type="objective", cognitive_skill="Remember",
        difficulty="Less", category="Multiple Choice Question", count=1, live=False,
    )[0]
    correct = [a for a in rec["answers"] if a["correct_answer"] == "Yes"]
    wrong = [a for a in rec["answers"] if a["correct_answer"] == "No"]
    assert len(correct) == 1 and len(wrong) == 3
    assert all(a["answer_weightage"] == "0" for a in wrong)
    assert all(a["answer_type"] == "Phrases" for a in rec["answers"])
    # Explanation says why correct is right AND why distractors are wrong.
    assert "distractors" in rec["answer_explanation"].lower()


# ------------------------------ review checks ------------------------------ #

def test_review_question_catches_problems():
    bad = {
        "sheet_kind": "descriptive", "question": "Explain.",
        "question_text": "", "cognitive_skills": "Understanding",
        "level_of_difficulty": "Medium", "marks": 5,
        "answers": [{"answer_type": "Words", "answer_weightage": "2",
                     "answer_content": "x"}],
    }
    problems = " | ".join(ap.review_question(bad))
    assert "question_text empty" in problems
    assert "non-standard cognitive skill" in problems
    assert "non-standard difficulty" in problems
    assert "weightage sum" in problems
    assert "non-standard answer_type" in problems


def test_review_passes_good_question(db, first_concept):
    concept = _concept(db, first_concept)
    rec = generation.generate_questions_for_concept(
        concept, question_type="descriptive", cognitive_skill="Analyse",
        difficulty="High", category="Long Answer", count=1, live=False,
    )[0]
    assert ap.review_question(rec) == []


# ------------------------- appears_in / purpose flow ------------------------ #

def test_batch_appears_in_flows_to_questions(client, db, first_concept):
    s = client.post("/build-assessments/sessions", json={
        "scope_type": "concept", "scope_ids": [first_concept["id"]],
    }).json()
    batch = client.post(f"/build-assessments/sessions/{s['id']}/batches", json={
        "cognitive_skills": ["Apply"], "difficulty_levels": ["Easy"],  # normalizes
        "categories": ["Multiple Choice Question"], "question_type": "objective",
        "num_questions": 1, "appears_in": ["Pre-test", "Worksheet"],
    }).json()
    assert batch["appears_in"] == ["Pre-test", "Worksheet"]
    assert batch["difficulty_levels"] == ["Less"]

    from tests.conftest import stream_result
    result = stream_result(client.post(f"/build-assessments/sessions/{s['id']}/generate"))
    assert result["created"] == 1
    assert result["review"]["problems"] == []
    qid = client.get(f"/build-assessments/sessions/{s['id']}").json()[
        "generated_question_ids"][0]
    q = db.get(models.Question, qid)
    assert q.question_appears_in == "Pre-test, Worksheet"
    assert q.level_of_difficulty == "Less"


def test_difficulty_legacy_normalization():
    from app import bulk_import as bi
    assert bi.normalize_difficulty("Easy") == "Less"
    assert bi.normalize_difficulty("Medium") == "Moderate"
    assert bi.normalize_difficulty("Hard") == "High"
    assert bi.normalize_difficulty("Moderate") == "Moderate"

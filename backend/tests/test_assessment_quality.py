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


# --------------------------- category prompting --------------------------- #

def test_prompt_includes_category_contract():
    p = ap.build_prompt(
        question_type="objective", difficulty="Moderate", skill="Understand",
        category="Assertion & Reasons")
    assert "CATEGORY: ASSERTION & REASONS" in p
    assert "Reason (R)" in p


def test_prompt_differs_per_category():
    a = ap.build_prompt(question_type="objective", difficulty="Less",
                        skill="Remember", category="True/False")
    b = ap.build_prompt(question_type="objective", difficulty="Less",
                        skill="Remember", category="Fill in the Blanks")
    assert a != b
    assert "CATEGORY: TRUE/FALSE" in a
    assert "CATEGORY: FILL IN THE BLANKS" in b


def test_every_category_has_a_block():
    from app import bulk_import as bi
    for cats in bi.QUESTION_CATEGORIES.values():
        for cat in cats:
            assert ap.canonical_category(cat) in ap.CATEGORY_BLOCKS, cat


def test_canonical_category_maps_legacy_labels():
    assert ap.canonical_category("Short Answer Type (3 Marks)") == "Short Answer"
    assert ap.canonical_category("Long Answer Type (5 Marks)") == "Long Answer"
    assert ap.canonical_category("True or False") == "True/False"
    assert ap.canonical_category("Assertion & Reasons Type") == "Assertion & Reasons"
    assert ap.canonical_category("Case Study") == "Case Based Questions"
    assert ap.canonical_category("MCQ") == "Multiple Choice Question"


def test_category_guidance_falls_back_per_type():
    # Unknown category -> sensible per-type default block, never empty.
    assert "CATEGORY: LONG ANSWER" in ap.category_guidance("descriptive", "Mystery")
    assert "CATEGORY: SHORT ANSWER" in ap.category_guidance("subjective", "")


# --------------------- deterministic category checks ---------------------- #

def test_review_flags_fill_in_blanks_without_blank():
    bad = {"sheet_kind": "objective", "question": "What is osmosis?",
           "question_text": "What is osmosis?", "cognitive_skills": "Remember",
           "level_of_difficulty": "Less", "marks": 1,
           "question_category": "Fill in the Blanks",
           "answers": [{"answer_type": "Phrases", "answer_content": "x",
                        "correct_answer": "Yes", "answer_weightage": "1"}]}
    assert any("no blank" in p for p in ap.review_question(bad))


def test_review_flags_assertion_reason_missing_parts():
    bad = {"sheet_kind": "objective", "question": "Pick the right statement.",
           "question_text": "Pick the right statement.", "cognitive_skills": "Understand",
           "level_of_difficulty": "Moderate", "marks": 1,
           "question_category": "Assertion & Reasons",
           "answers": [{"answer_type": "Phrases", "answer_content": "x",
                        "correct_answer": "Yes", "answer_weightage": "1"}]}
    assert any("Assertion (A) and a Reason (R)" in p for p in ap.review_question(bad))


# --------------------- dry generation per category ------------------------ #

def test_dry_true_false(db, first_concept):
    concept = _concept(db, first_concept)
    rec = generation.generate_questions_for_concept(
        concept, question_type="objective", cognitive_skill="Remember",
        difficulty="Less", category="True/False", count=1, live=False)[0]
    contents = sorted(a["answer_content"] for a in rec["answers"])
    assert contents == ["False", "True"]
    assert sum(1 for a in rec["answers"] if a["correct_answer"] == "Yes") == 1
    assert ap.review_question(rec) == []


def test_dry_assertion_reasons(db, first_concept):
    concept = _concept(db, first_concept)
    rec = generation.generate_questions_for_concept(
        concept, question_type="objective", cognitive_skill="Understand",
        difficulty="Moderate", category="Assertion & Reasons", count=1, live=False)[0]
    assert "Assertion (A)" in rec["question"] and "Reason (R)" in rec["question"]
    assert len(rec["answers"]) == 4
    assert sum(1 for a in rec["answers"] if a["correct_answer"] == "Yes") == 1
    assert ap.review_question(rec) == []


def test_dry_fill_in_the_blanks_objective(db, first_concept):
    concept = _concept(db, first_concept)
    rec = generation.generate_questions_for_concept(
        concept, question_type="objective", cognitive_skill="Remember",
        difficulty="Less", category="Fill in the Blanks", count=1, live=False)[0]
    assert "____" in rec["question"]
    assert sum(1 for a in rec["answers"] if a["correct_answer"] == "Yes") == 1
    assert ap.review_question(rec) == []


def test_dry_case_based_embeds_context(db, first_concept):
    concept = _concept(db, first_concept)
    rec = generation.generate_questions_for_concept(
        concept, question_type="descriptive", cognitive_skill="Apply",
        difficulty="High", category="Case Based Questions", count=1, live=False)[0]
    assert "CASE:" in rec["question"]
    weights = [float(a["answer_weightage"]) for a in rec["answers"]]
    assert sum(weights) == rec["marks"]
    assert ap.review_question(rec) == []


def test_dry_mcq_unchanged_default(db, first_concept):
    concept = _concept(db, first_concept)
    rec = generation.generate_questions_for_concept(
        concept, question_type="objective", cognitive_skill="Remember",
        difficulty="Less", category="Multiple Choice Question", count=1, live=False)[0]
    correct = [a for a in rec["answers"] if a["correct_answer"] == "Yes"]
    wrong = [a for a in rec["answers"] if a["correct_answer"] == "No"]
    assert len(correct) == 1 and len(wrong) == 3
    assert "distractors" in rec["answer_explanation"].lower()


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

    result = client.post(f"/build-assessments/sessions/{s['id']}/generate").json()
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

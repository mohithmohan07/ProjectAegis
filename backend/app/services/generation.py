"""Content generation: questions from concepts, concepts from MMD.

All functions have a dry path (deterministic, no API keys — used for the MVP
and tests) and a live hook that delegates to the vendored OpenAI-backed
scripts. The dry path is intentionally realistic: it returns fully-populated
records so the post-generation pipeline and the canonical writer are always
exercised end to end.
"""
from __future__ import annotations

import re

from .. import bulk_import as bi
from .. import config, models
from . import katex_rules as kr

_SLUG_RE = re.compile(r"[^A-Za-z0-9]")
_MATH_SUBJECTS = {"Mathematics", "Physics", "Chemistry"}


def _is_math(concept: models.Concept) -> bool:
    return concept.topic.chapter.subject in _MATH_SUBJECTS


def _sample_equation(concept: models.Concept) -> str:
    """A representative [katex] expression that references the concept."""
    name = _slug(concept.concept_title, 16) or "X"
    return kr.katex(rf"\text{{{name}}} = f(x)")


def _concept_reference_link(concept: models.Concept) -> str:
    """Link to a public reference for the concept (Wikipedia search)."""
    from urllib.parse import quote_plus
    q = quote_plus(f"{concept.concept_title} {concept.topic.chapter.subject or ''}".strip())
    return kr.link(concept.concept_title, f"https://en.wikipedia.org/wiki/Special:Search?search={q}")


def _slug(text: str, length: int = 22) -> str:
    return _SLUG_RE.sub("", (text or "").title())[:length] or "X"


def _topic_index(concept: models.Concept) -> int:
    topic = concept.topic
    siblings = sorted(topic.chapter.topics, key=lambda t: t.id)
    return siblings.index(topic) + 1


def question_label(concept: models.Concept, n: int) -> str:
    """Build a canonical question label, e.g. 10CBMA_Crcls_PL_T01_CncptlMnng Q03."""
    ch = concept.topic.chapter
    prefix = ch.chapter_code.split("_")[0] if ch.chapter_code else _slug(ch.chapter_title, 6)
    return (
        f"{prefix}_{_slug(ch.chapter_title, 6)}_PL_"
        f"T{_topic_index(concept):02d}_{_slug(concept.concept_title)} Q{n:02d}"
    )


# --------------------------------------------------------------------------- #
# Questions from concepts (Build Assessments - concept mapping path)
# --------------------------------------------------------------------------- #

def _objective_answers(concept: models.Concept) -> list[dict]:
    correct = f"{concept.concept_title} (correct)"
    if _is_math(concept):
        correct = f"{correct} — {_sample_equation(concept)}"
    return [
        {"answer_type": "Phrases", "answer_content": correct,
         "correct_answer": "Yes", "answer_weightage": "1"},
        {"answer_type": "Phrases", "answer_content": "Plausible distractor A",
         "correct_answer": "No", "answer_weightage": "0"},
        {"answer_type": "Phrases", "answer_content": "Plausible distractor B",
         "correct_answer": "No", "answer_weightage": "0"},
        {"answer_type": "Phrases", "answer_content": "Plausible distractor C",
         "correct_answer": "No", "answer_weightage": "0"},
    ]


def _subjective_answers(concept: models.Concept, marks: float) -> list[dict]:
    ans = concept.concept_title
    if _is_math(concept):
        ans = f"{ans} {_sample_equation(concept)}"
    return [
        {"answer_type": "Phrases", "answer": ans,
         "answer_display": "Yes", "weightage": str(marks), "placeholder": "answer"},
    ]


def _descriptive_answers(concept: models.Concept, marks: float) -> tuple[list[dict], list[dict]]:
    body = f"Model answer covering {concept.concept_title}. See {_concept_reference_link(concept)}."
    if _is_math(concept):
        body = f"{body} Key relation: {_sample_equation(concept)}."
    answers = [
        {"answer_type": "Phrases", "answer_weightage": str(marks), "answer_content": body},
    ]
    # Keyword cells are NOT rich text — they hold raw KaTeX / plain text.
    sub = [
        {"text": f"i. Define {concept.concept_title}.", "marks": "2",
         "keywords": [{"answer_type": "Phrases", "weightage": "2",
                       "keyword": concept.concept_title}]},
        {"text": f"ii. Apply {concept.concept_title} to a worked example.",
         "marks": str(max(marks - 2, 1)),
         "keywords": [{"answer_type": "Phrases", "weightage": str(max(marks - 2, 1)),
                       "keyword": rf"\text{{{concept.concept_title}}} = f(x)"
                       if _is_math(concept) else "worked example"}]},
    ]
    return answers, sub


def _default_marks(kind: str) -> float:
    return {"objective": 1, "subjective": 3, "descriptive": 5}[kind]


# Varied question stems per cognitive skill (anti-monotony: rotated per
# question index so a batch never repeats one opening pattern). {t} = concept
# title, {d} = short concept description.
_DRY_STEMS: dict[str, list[str]] = {
    "Remember": [
        "Identify the term described here: {d}",
        "Name the concept that matches: {d}",
        "Complete the statement: '{t}' is best described as ____.",
        "Select the option that correctly states '{t}'.",
        "Match '{t}' with its correct description from the options.",
    ],
    "Understand": [
        "Explain why '{t}' matters, using the idea that {d}",
        "Describe how '{t}' works in your own words.",
        "Give a reason why {d}",
        "Distinguish '{t}' from a closely related idea, with one example.",
        "Interpret what '{t}' means in a classroom example.",
    ],
    "Apply": [
        "A classmate faces this situation: {d} Use '{t}' to resolve it.",
        "Use '{t}' to solve the following case: {d}",
        "Predict what happens when '{t}' is applied here: {d}",
        "Apply the rule behind '{t}' to a new example and show the steps.",
        "Choose the correct method based on '{t}' and carry it out.",
    ],
    "Analyse": [
        "A student's working contains an error involving '{t}'. Identify the error and correct it.",
        "Analyse the relationship described here and explain its cause: {d}",
        "Compare the two cases implied by '{t}' and infer the difference.",
        "Find the pattern behind '{t}' and explain what produces it.",
        "Break the process of '{t}' into its parts and explain each briefly.",
    ],
    "Evaluate": [
        "A student claims: \"{d}\" Is this claim fully correct? Justify your judgment.",
        "Evaluate whether '{t}' is the better approach in this case, with reasons.",
        "Support or refute: '{t}' always holds. Use evidence from the concept.",
        "Decide which of two interpretations of '{t}' is stronger and explain why.",
        "Assess the validity of this conclusion about '{t}': {d}",
    ],
    "Create": [
        "Design a simple example or demonstration that shows '{t}' in action.",
        "Construct a short plan (or flowchart) that uses '{t}' step by step.",
        "Propose a solution to a real-life problem using '{t}'.",
        "Frame your own example question that tests '{t}', and answer it.",
        "Develop a brief method to verify '{t}' experimentally or by calculation.",
    ],
}

# Mark-wise rubric point templates per difficulty (spec section 6).
_RUBRIC_POINTS = {
    "Less": [
        "1 mark: States the correct term/fact/answer for '{t}'.",
        "1 mark: Gives the correct explanation or example for '{t}'.",
        "1 mark: Uses correct terminology/units where applicable.",
        "1 mark: Presents the answer clearly and completely.",
        "1 mark: Connects the answer back to the question correctly.",
    ],
    "Moderate": [
        "1 mark: Identifies the relevant concept/principle ('{t}').",
        "1 mark: Applies or explains it correctly in this context.",
        "1 mark: Gives the correct conclusion/final answer/example.",
        "1 mark: Shows the working/reasoning clearly.",
        "1 mark: Uses correct terminology and units where applicable.",
    ],
    "High": [
        "1 mark: Identifies the relevant concept/principle ('{t}').",
        "1 mark: Selects the correct approach/method.",
        "1 mark: Applies the concept with correct reasoning/intermediate steps.",
        "1 mark: Interprets/justifies the result against the given context.",
        "1 mark: Gives the final conclusion with correct terminology.",
    ],
}


def _stem_for(skill: str, difficulty: str, concept: models.Concept, idx: int) -> str:
    stems = _DRY_STEMS.get(skill, _DRY_STEMS["Understand"])
    details = (concept.concept_details or "").split("//")[0]
    details = details.replace("Description:", "").strip()[:140] or concept.concept_title
    if not details.endswith((".", "?", "!")):
        details += "."
    stem = stems[(idx - 1) % len(stems)].format(t=concept.concept_title, d=details)
    if difficulty == "High" and skill in {"Apply", "Analyse", "Evaluate", "Create"}:
        stem += " Justify each step of your reasoning."
    elif difficulty == "Moderate" and skill in {"Understand", "Apply"}:
        stem += " Give a reason for your answer."
    return stem


def _rubric_points(marks: float, difficulty: str, concept: models.Concept) -> list[str]:
    n = max(int(marks), 1)
    pool = _RUBRIC_POINTS.get(difficulty, _RUBRIC_POINTS["Moderate"])
    return [pool[i % len(pool)].format(t=concept.concept_title) for i in range(n)]


def _dry_distractors(concept: models.Concept) -> list[str]:
    """Plausible same-family distractors built from the concept's own context."""
    kws = [k.strip() for k in (concept.keywords or "").split(",") if k.strip()]
    siblings = [c.concept_title for c in concept.topic.concepts
                if c.id != concept.id][:2]
    out = []
    if siblings:
        out.append(f"A property of '{siblings[0]}' (related but not '{concept.concept_title}')")
    if kws:
        out.append(f"The converse of the {kws[0]} relationship (common student error)")
    while len(out) < 3:
        out.append(f"A partially-correct restatement of '{concept.concept_title}' "
                   f"missing the key condition ({len(out) + 1})")
    return out[:3]


def _dry_objective(category: str, concept: models.Concept, details: str, idx: int,
                   base_stem: str) -> tuple[str, list[dict], str]:
    """Category-shaped objective question: (question, answers, explanation).

    MCQ (and unknown categories) keep the skill-rotated stem + 1-correct +
    3-distractor layout; True/False, Assertion & Reasons and Fill in the Blanks
    get their distinctive stem + option structure.
    """
    from . import assessment_prompts as ap
    cat = ap.canonical_category(category)
    t = concept.concept_title
    fact = details[:90].rstrip(".") or "as defined in the chapter"

    if cat == "True/False":
        framings = [
            f"State whether the following is True or False: '{t}' is {fact}.",
            f"True or False: '{t}' is correctly described as — {fact}.",
            f"Decide if this statement is True or False: '{t}' means {fact}.",
        ]
        answers = [
            {"answer_type": "Phrases", "answer_content": "True",
             "correct_answer": "Yes", "answer_weightage": "1"},
            {"answer_type": "Phrases", "answer_content": "False",
             "correct_answer": "No", "answer_weightage": "0"},
        ]
        return (framings[(idx - 1) % len(framings)], answers,
                f"The statement accurately describes '{t}', so it is True.")

    if cat == "Assertion & Reasons":
        kws = [k.strip() for k in (concept.keywords or "").split(",") if k.strip()]
        because = kws[0] if kws else "the defining condition of the concept"
        stem = (f"Assertion (A): '{t}' is {fact}. "
                f"Reason (R): This holds because {because} ensures '{t}' behaves as stated. "
                "Choose the correct option:")
        opts = [
            "Both A and R are true, and R is the correct explanation of A.",
            "Both A and R are true, but R is not the correct explanation of A.",
            "A is true, but R is false.",
            "A is false, but R is true.",
        ]
        answers = [{"answer_type": "Phrases", "answer_content": opts[0],
                    "correct_answer": "Yes", "answer_weightage": "1"}] + [
            {"answer_type": "Phrases", "answer_content": o,
             "correct_answer": "No", "answer_weightage": "0"} for o in opts[1:]]
        return (stem, answers,
                "Both the Assertion and the Reason are true, and the Reason "
                "correctly explains the Assertion.")

    if cat == "Fill in the Blanks":
        framings = [
            f"Fill in the blank: '{t}' is best defined as ____.",
            f"Complete the statement by choosing the correct option: the key idea here is ____.",
            f"Fill in the blank: in this topic, ____ refers to '{t}'.",
        ]
        correct = f"{t} ({fact})"
        if _is_math(concept):
            correct = f"{correct} — {_sample_equation(concept)}"
        answers = [{"answer_type": "Phrases", "answer_content": correct,
                    "correct_answer": "Yes", "answer_weightage": "1"}] + [
            {"answer_type": "Phrases", "answer_content": d,
             "correct_answer": "No", "answer_weightage": "0"}
            for d in _dry_distractors(concept)]
        return (framings[(idx - 1) % len(framings)], answers,
                f"The blank is correctly filled by '{t}'; the other options name "
                "related but incorrect ideas.")

    # Multiple Choice Question (and any unknown objective category).
    correct = f"{t} (correct: {details[:80] or 'as defined'})"
    if _is_math(concept):
        correct = f"{correct} — {_sample_equation(concept)}"
    answers = [{"answer_type": "Phrases", "answer_content": correct,
                "correct_answer": "Yes", "answer_weightage": "1"}] + [
        {"answer_type": "Phrases", "answer_content": d,
         "correct_answer": "No", "answer_weightage": "0"}
        for d in _dry_distractors(concept)]
    return (base_stem, answers,
            f"The correct option states '{t}' accurately. The distractors are wrong "
            "because they describe a related concept, the converse relation, or omit "
            "the key condition.")


def _dry_subjective_stem(category: str, concept: models.Concept, details: str,
                         idx: int) -> str | None:
    """Category-shaped subjective stem, or None to use the skill-rotated default."""
    from . import assessment_prompts as ap
    cat = ap.canonical_category(category)
    t = concept.concept_title
    if cat == "Fill in the Blanks":
        return f"Complete the sentence about '{t}': the key idea is that {t} is ____."
    if cat == "Sentence Transformation":
        return (f"Rewrite the following sentence without changing its meaning, as a "
                f"complex sentence: 'The idea of {t} is important and it is used often.'")
    if cat == "Error Correction":
        return (f"The following sentence contains one grammatical error. Identify and "
                f"correct it: 'The concept of {t} are explained in this chapter.'")
    if cat == "Very Short Answer":
        return f"In one word or short phrase, state the key idea of '{t}'."
    return None


def _dry_descriptive_stem(category: str, concept: models.Concept, details: str,
                          idx: int) -> str | None:
    """Category-shaped descriptive stem (with embedded context where the category
    needs it), or None to use the skill-rotated default."""
    from . import assessment_prompts as ap
    cat = ap.canonical_category(category)
    t = concept.concept_title
    ctx = details[:160] or f"the chapter's treatment of {t}"
    if cat == "Case Based Questions":
        return (f"Read the case and answer the parts that follow. CASE: A student "
                f"encounters a real situation where {ctx} Using '{t}', work through "
                "the parts below.")
    if cat == "Passage Based Questions":
        return (f"Read the passage and answer the parts that follow. PASSAGE: {ctx} "
                "Answer the questions below, citing the passage.")
    if cat == "Extract Based Questions":
        return (f"Read the extract and answer the parts that follow. EXTRACT: \"{ctx}\" "
                "Answer the reference-to-context parts below.")
    if cat == "Composition Writing":
        return (f"Write a short composition (about 120-150 words) on a topic that uses "
                f"'{t}'. Plan your ideas, organise them clearly, and use accurate language.")
    return None


def generate_questions_for_concept(
    concept: models.Concept,
    *,
    question_type: str,
    cognitive_skill: str,
    difficulty: str,
    category: str,
    count: int,
    start_index: int = 1,
    live: bool | None = None,
    appears_in: str = "",
) -> list[dict]:
    """Return ``count`` question dicts for one concept under one blueprint cell."""
    use_live = config.use_live_generation() if live is None else live
    if use_live:
        return _live_questions_for_concept(
            concept, question_type=question_type, cognitive_skill=cognitive_skill,
            difficulty=difficulty, category=category, count=count,
            start_index=start_index, appears_in=appears_in,
        )
    config.require_generation_live()
    marks = _default_marks(question_type)
    out: list[dict] = []
    details = (concept.concept_details or "").split("//")[0].strip()[:160]
    for i in range(count):
        idx = start_index + i
        base_stem = _stem_for(cognitive_skill, difficulty, concept, idx)
        if _is_math(concept):
            base_stem = f"{base_stem} Express the key relation as {_sample_equation(concept)}."

        # Category shapes the stem (and, for objective, the option set).
        obj_answers: list[dict] | None = None
        obj_explanation = ""
        if question_type == "objective":
            stem, obj_answers, obj_explanation = _dry_objective(
                category, concept, details, idx, base_stem)
        elif question_type == "subjective":
            stem = _dry_subjective_stem(category, concept, details, idx) or base_stem
        else:
            stem = _dry_descriptive_stem(category, concept, details, idx) or base_stem

        model_answer = (
            f"{concept.concept_title}: {details or 'see concept details'} "
            f"(complete, mark-worthy answer covering every rubric point)."
        )
        record: dict = {
            "sheet_kind": question_type,
            "question_label": question_label(concept, idx),
            "question_category": category,
            "cognitive_skills": cognitive_skill,
            "question_source": bi.QUESTION_SOURCE_DEFAULT,
            "level_of_difficulty": difficulty,
            "marks": marks,
            "question": stem,
            "question_appears_in": appears_in,
            # Plain-text question (+ concept context) for the AI evaluator.
            "question_text": bi.to_plain_text(
                f"{stem}\nConcept context: {details}" if details else stem),
            "answer_explanation": (
                f"{model_answer} Reference: {_concept_reference_link(concept)}."
            ),
            "answers": [],
            "sub_questions": [],
            "origin": "concept_mapping",
        }
        if question_type == "objective":
            record["answers"] = obj_answers or []
            record["answer_explanation"] = obj_explanation
        elif question_type == "subjective":
            # Rubric points live in the answer blocks; each carries weightage 1.
            record["answers"] = [
                {"answer_type": "Phrases", "answer": point,
                 "answer_display": "Yes" if n == 0 else "",
                 "weightage": "1", "placeholder": "answer"}
                for n, point in enumerate(_rubric_points(marks, difficulty, concept))
            ]
            record["math_keyboard"] = "Yes" if concept.topic.chapter.subject in {
                "Mathematics", "Physics", "Chemistry"} else ""
        else:  # descriptive
            # display_answer = clean model answer; answer_content = rubric points.
            record["display_answer"] = model_answer
            record["answers"] = [
                {"answer_type": "Phrases", "answer_weightage": "1",
                 "answer_content": point}
                for point in _rubric_points(marks, difficulty, concept)
            ]
            record["sub_questions"] = [
                {"text": f"(a) Define '{concept.concept_title}' in your own words.",
                 "marks": "2",
                 "keywords": [{"answer_type": "Phrases", "weightage": "2",
                               "keyword": concept.concept_title}]},
                {"text": f"(b) Apply '{concept.concept_title}' to a worked example.",
                 "marks": str(max(marks - 2, 1)),
                 "keywords": [{"answer_type": "Phrases",
                               "weightage": str(max(marks - 2, 1)),
                               "keyword": rf"\text{{{concept.concept_title}}} = f(x)"
                               if _is_math(concept) else "worked example"}]},
            ]
        out.append(record)
    return out


def _live_questions_for_concept(
    concept: models.Concept, *, question_type: str, cognitive_skill: str,
    difficulty: str, category: str, count: int, start_index: int,
    appears_in: str = "",
) -> list[dict]:
    """Live generation: modular prompt assembly + review/repair before accept."""
    import json as _json
    from . import assessment_prompts as ap

    chapter = concept.topic.chapter
    marks = _default_marks(question_type)
    system = ap.build_prompt(
        question_type=question_type, difficulty=difficulty, skill=cognitive_skill,
        subject=chapter.subject, grade=chapter.grade, board=chapter.board,
        marks=marks, category=category, purpose=appears_in,
    )
    user = (
        f"CONCEPT: {concept.concept_title}\n"
        f"CONCEPT DETAILS: {concept.concept_details}\n"
        f"KEYWORDS: {concept.keywords}\n"
        f"CHAPTER: {chapter.chapter_title} | TOPIC: {concept.topic.topic_title}\n\n"
        f"Generate exactly {count} question(s) of type {question_type}, "
        f"category '{category}', difficulty {difficulty}, cognitive skill "
        f"{cognitive_skill}, {marks:g} mark(s) each. Vary the stems/framing "
        f"across the batch (batch seed {start_index})."
    )

    def _parse(data: dict) -> list[dict]:
        records: list[dict] = []
        for n, row in enumerate(data.get("questions", [])[:count]):
            answers = []
            for a in row.get("answers", []) or []:
                a = dict(a)
                a["answer_type"] = bi.normalize_answer_type(
                    a.get("answer_type", "")) or "Phrases"
                # Normalize block shape per sheet kind (the model may emit
                # either objective-style or subjective-style keys).
                if question_type == "subjective":
                    a.setdefault("answer", a.pop("answer_content", ""))
                    a.setdefault("weightage", str(a.pop("answer_weightage", "") or "1"))
                    a.setdefault("answer_display", "Yes" if not answers else "")
                    a.setdefault("placeholder", "answer")
                else:
                    a.setdefault("answer_content", a.pop("answer", ""))
                    a.setdefault("answer_weightage", str(a.pop("weightage", "") or
                                                         ("1" if question_type == "descriptive" else "0")))
                answers.append(a)
            rec = {
                "sheet_kind": question_type,
                "question_label": question_label(concept, start_index + n),
                "question_category": row.get("question_category") or category,
                "cognitive_skills": bi.normalize_cognitive_skills(
                    row.get("cognitive_skills") or cognitive_skill),
                "question_source": bi.QUESTION_SOURCE_DEFAULT,
                "level_of_difficulty": bi.normalize_difficulty(
                    row.get("level_of_difficulty") or difficulty),
                "marks": float(row.get("marks") or marks),
                "question": row.get("question", ""),
                "question_appears_in": appears_in,
                "question_text": (row.get("question_text", "").strip()
                                  or bi.to_plain_text(row.get("question", ""))),
                "display_answer": row.get("display_answer", ""),
                "answer_explanation": row.get("answer_explanation", ""),
                "answers": answers,
                "sub_questions": row.get("sub_questions", []) or [],
                "origin": "concept_mapping",
            }
            records.append(rec)
        return records

    records = _parse(_openai_json(system, user))
    # Deterministic review; one repair round for failing questions.
    failing = {i: ap.review_question(r) for i, r in enumerate(records)}
    failing = {i: p for i, p in failing.items() if p}
    if failing or len(records) < count:
        feedback = "; ".join(
            f"question {i + 1}: {', '.join(p)}" for i, p in failing.items())
        retry = _openai_json(
            system,
            user + "\n\nREVIEW FEEDBACK — regenerate the FULL batch fixing these "
            f"problems and keep everything else compliant: {feedback or 'wrong count'}",
        )
        retry_records = _parse(retry)
        if retry_records:
            for i, r in enumerate(retry_records):
                if i < len(records) and (i in failing or len(records) < count):
                    records[i] = r
            if len(retry_records) > len(records):
                records = retry_records[:count]
    # Anti-monotony: regenerate once if the batch repeats one stem too much.
    report = ap.stem_monotony_report([r["question"] for r in records])
    if report["monotonous"]:
        varied = _openai_json(
            system,
            user + "\n\nThe previous batch was too repetitive (opening "
            f"'{report['worst']}' used {report['worst_count']}x). Regenerate "
            "with clearly varied framings/patterns per question.",
        )
        varied_records = _parse(varied)
        if varied_records and not ap.stem_monotony_report(
                [r["question"] for r in varied_records])["monotonous"]:
            records = varied_records[:count]
    return records


# --------------------------------------------------------------------------- #
# Questions identified from an uploaded document (Build Assessments - upload path)
# --------------------------------------------------------------------------- #

# Question types the upload path can deposit. "auto" means: detect each
# question's type from the document and absorb a mix (the default).
_SHEET_KINDS = ("objective", "subjective", "descriptive")


def _default_category_for(kind: str) -> str:
    return {"objective": "Multiple Choice Question",
            "subjective": "Short Answer",
            "descriptive": "Long Answer"}.get(kind, "Multiple Choice Question")


def _normalize_sheet_kind(value: str, default: str = "objective") -> str:
    v = (value or "").strip().lower()
    if v in _SHEET_KINDS:
        return v
    # Map a few common synonyms the model might emit.
    aliases = {"mcq": "objective", "objective question": "objective",
               "short answer": "subjective", "short": "subjective",
               "long answer": "descriptive", "long": "descriptive",
               "essay": "descriptive"}
    return aliases.get(v, default)


def identify_questions_from_mmd(
    mmd_text: str, *, upload_type: str, question_type: str = "auto",
    textbook_mode: str = "", live: bool | None = None,
) -> list[dict]:
    """Extract / create question records from an uploaded document's MMD.

    ``question_type`` is one of objective | subjective | descriptive, or
    ``auto`` (the default) to detect each question's type and absorb a mix of
    all three (descriptive questions may carry sub-questions).
    """
    use_live = config.use_live_generation() if live is None else live
    if use_live:
        return _live_identify_questions_from_mmd(
            mmd_text, upload_type=upload_type, question_type=question_type,
            textbook_mode=textbook_mode,
        )
    config.require_generation_live()
    # Dry: split the MMD body into question-like chunks. Dry mode can't truly
    # classify, so "auto" falls back to objective for a deterministic stub.
    effective = "objective" if question_type == "auto" else question_type
    chunks = [c.strip() for c in re.split(r"\n\s*\n+", mmd_text) if c.strip()]
    chunks = [c for c in chunks if not c.startswith("#")] or ["(no question content detected)"]

    # Shared-context handling: when a question references surrounding context
    # ("based on the above passage", "from the conversation", "refer to the
    # diagram"...), the preceding block is attached into question_text so the
    # AI evaluator receives the full context.
    context_triggers = re.compile(
        r"based on the (above|following)|from the (conversation|passage|dialogue)|"
        r"refer(ring)? to the (diagram|table|figure|graph)|using the table|"
        r"according to the (case study|passage)|answer the following",
        re.IGNORECASE,
    )
    records: list[dict] = []
    prev_chunk = ""
    for i, chunk in enumerate(chunks[:25], start=1):
        q_text = bi.to_plain_text(chunk[:400])
        if prev_chunk and context_triggers.search(chunk):
            q_text = f"Context: {bi.to_plain_text(prev_chunk[:600])}\n\n{q_text}"
        rec = {
            "sheet_kind": effective,
            "question_category": _default_category_for(effective),
            "cognitive_skills": "Understand",
            "question_source": bi.QUESTION_SOURCE_DEFAULT,
            "level_of_difficulty": "Moderate",
            "marks": _default_marks(effective),
            "question": chunk[:400],
            "question_text": q_text,
            "answer_explanation": "",
            "answers": [],
            "sub_questions": [],
            "origin": "upload",
        }
        prev_chunk = chunk
        if upload_type in {"questions_and_answers", "textbook"} and effective == "objective":
            rec["answers"] = [
                {"answer_type": "Phrases", "answer_content": "Extracted option",
                 "correct_answer": "Yes", "answer_weightage": "1"},
            ]
        records.append(rec)
    return records


# Cap on questions identified from a single upload (keeps the JSON response and
# the round-robin deposit bounded for very large documents).
_IDENTIFY_MAX = 40


def _identify_is_extract(upload_type: str, textbook_mode: str) -> bool:
    """Whether the upload should EXTRACT existing questions vs CREATE new ones.

    Question banks / Q&A sheets / handwritten work and textbooks explicitly set
    to 'extract' carry questions to lift out verbatim; a textbook set to
    'create' (or a generic document) is content to author fresh questions from.
    """
    if upload_type == "textbook":
        return textbook_mode != "create"
    return upload_type in {"questions", "questions_and_answers", "handwritten"}


def _coerce_answers(raw_answers: list, question_type: str) -> list[dict]:
    """Normalize model-emitted answer blocks to the per-sheet canonical shape.

    The model may emit either objective-style ({answer_type, answer_content,
    correct_answer, answer_weightage}) or subjective-style ({answer_type,
    answer, answer_display, weightage, placeholder}) keys; coerce to the shape
    the writer expects for ``question_type``.
    """
    answers: list[dict] = []
    for a in raw_answers or []:
        if not isinstance(a, dict):
            continue
        a = dict(a)
        a["answer_type"] = bi.normalize_answer_type(a.get("answer_type", "")) or "Phrases"
        if question_type == "subjective":
            a.setdefault("answer", a.pop("answer_content", ""))
            a.setdefault("weightage", str(a.pop("answer_weightage", "") or "1"))
            a.setdefault("answer_display", "Yes" if not answers else "")
            a.setdefault("placeholder", "answer")
        else:
            a.setdefault("answer_content", a.pop("answer", ""))
            a.setdefault("answer_weightage", str(a.pop("weightage", "") or
                         ("1" if question_type == "descriptive" else "0")))
        answers.append(a)
    return answers


_TYPE_HINTS = {
    "objective": "OBJECTIVE — MCQ / fill-in-the-blank. For MCQs emit 3-4 "
                 "options with exactly one correct_answer = 'Yes'.",
    "subjective": "SUBJECTIVE — short answer; emit mark-wise rubric points "
                  "whose weightages sum to the marks.",
    "descriptive": "DESCRIPTIVE — long answer; emit mark-wise rubric points "
                   "(and sub_questions for multi-part questions) summing to marks.",
}


def _identify_system(upload_type: str, question_type: str, *, extract: bool) -> str:
    """System prompt for live question identification from an uploaded document."""
    from . import assessment_prompts as ap

    intent = (
        "EXTRACT every assessment question already present in the document. "
        "Preserve each question's original wording and intent — do NOT invent "
        "new questions. When a question's options, answer, solution or marking "
        "scheme is present, capture it faithfully; otherwise leave answers empty."
        if extract else
        "CREATE fresh, exam-grade questions from the document's content. Cover "
        "the key ideas across the material; never copy sentences verbatim as "
        "questions, and never drift off the document's topic."
    )
    if question_type == "auto":
        type_block = (
            "QUESTION TYPES — the document may contain a MIX of types. For EACH "
            "question, set \"sheet_kind\" to the type that best fits it and shape "
            "it accordingly:\n"
            f"- objective: {_TYPE_HINTS['objective']}\n"
            f"- subjective: {_TYPE_HINTS['subjective']}\n"
            f"- descriptive: {_TYPE_HINTS['descriptive']}\n"
            "Preserve a question's natural type — do NOT force everything into one "
            "type. A long/multi-part question with parts (a),(b),(c) is descriptive "
            "and MUST keep its parts in the sub_questions slots, never split into "
            "separate questions."
        )
    else:
        type_block = (
            f"TARGET QUESTION TYPE (every question is this type): "
            f"{_TYPE_HINTS[question_type]}\n"
            f"Set \"sheet_kind\" to \"{question_type}\" on every question."
        )
    return f"""\
You are an assessment digitizer for Indian school boards (ICSE/CBSE). You read
a document already converted to Markdown/MMD (mathematics in LaTeX) and return
assessment questions in a STRICT JSON schema.

TASK: {intent}
{type_block}
Classify each question's question_category, cognitive_skills and
level_of_difficulty. Add a "sheet_kind" field (objective|subjective|descriptive)
to every question object.

STANDARD VALUES (use EXACTLY these):
- cognitive_skills: Remember | Understand | Apply | Analyse | Evaluate | Create
- level_of_difficulty: Less | Moderate | High
- answer_type: Phrases | Equation | Image

{ap.CONTENT_FORMAT_BLOCK}

{ap.OUTPUT_BLOCK}

Return ONLY the JSON object. Emit at most {_IDENTIFY_MAX} questions."""


def _live_identify_questions_from_mmd(
    mmd_text: str, *, upload_type: str, question_type: str, textbook_mode: str = "",
) -> list[dict]:
    """Live (OpenAI) question identification from an uploaded document's MMD."""
    extract = _identify_is_extract(upload_type, textbook_mode)
    system = _identify_system(upload_type, question_type, extract=extract)
    auto = question_type == "auto"
    user = (
        "DOCUMENT (MMD):\n" + _trim(mmd_text, 200_000) + "\n\n"
        + (f"Return up to {_IDENTIFY_MAX} question(s), each tagged with its own "
           "\"sheet_kind\" (objective|subjective|descriptive), as a JSON object "
           "with a \"questions\" array."
           if auto else
           f"Return up to {_IDENTIFY_MAX} {question_type} question(s) as "
           "specified above, as a JSON object with a \"questions\" array.")
    )
    data = _openai_json(system, user)
    records: list[dict] = []
    for row in (data.get("questions") or [])[:_IDENTIFY_MAX]:
        if not isinstance(row, dict):
            continue
        question = (row.get("question") or "").strip()
        if not question:
            continue
        # In auto mode each question carries its own type; otherwise force the
        # requested type.
        kind = (_normalize_sheet_kind(row.get("sheet_kind") or row.get("question_type"))
                if auto else question_type)
        try:
            marks = float(row.get("marks") or _default_marks(kind))
        except (TypeError, ValueError):
            marks = _default_marks(kind)
        records.append({
            "sheet_kind": kind,
            "question_category": row.get("question_category") or _default_category_for(kind),
            "cognitive_skills": bi.normalize_cognitive_skills(
                row.get("cognitive_skills") or "Understand") or "Understand",
            "question_source": bi.QUESTION_SOURCE_DEFAULT,
            "level_of_difficulty": bi.normalize_difficulty(
                row.get("level_of_difficulty") or "Moderate") or "Moderate",
            "marks": marks,
            "question": question,
            "question_appears_in": "",
            "question_text": (str(row.get("question_text", "")).strip()
                              or bi.to_plain_text(question)),
            "display_answer": row.get("display_answer", ""),
            "answer_explanation": row.get("answer_explanation", ""),
            "answers": _coerce_answers(row.get("answers", []), kind),
            "sub_questions": row.get("sub_questions") or [] if kind == "descriptive" else [],
            "origin": "upload",
        })
    if not records:
        raise RuntimeError("live question identification returned no questions")
    return records


# --------------------------------------------------------------------------- #
# Concepts from MMD (Build Concepts - post learning)
# --------------------------------------------------------------------------- #

# Live concept-extraction prompts: ported from the vendored
# mmd_to_concepts_excel engine (subject-specific variants, 40-60 concept
# quota, quality rules) PLUS the team's review fixes, which the vendored
# prompt predates: inline worked examples (never bare "Example 19" refs),
# no '&' chains in names, distinct concept-name stems, Types must carry
# example prompts, syllabus-scoped length, no 'MMD' references.

_MATH_NAME_TEMPLATES = """\
   - Properties and Applications of <X>
   - Proof and Derivation of <rule/law>
   - Conditions for Applying <rule/law>
   - Representation of <X>
   - Conceptual Meaning of <X>
   - Methods of <procedure>
   - Laws and Applications of <X>
   - Converting Between <A> and <B>
   - Simplifying Using <rule/law>"""

_DESCRIPTIVE_NAME_TEMPLATES = """\
   - Structure and Function of <X>
   - Process of <X>
   - Types and Classification of <X>
   - Characteristics of <X>
   - Relationship between <A> and <B>
   - Causes and Effects of <X>
   - Importance and Significance of <X>
   - Comparison of <A> and <B>"""


def _concepts_system(subject: str) -> str:
    s = (subject or "the subject").strip() or "the subject"
    math_like = s.lower() in {"mathematics", "math", "physics"}
    templates = _MATH_NAME_TEMPLATES if math_like else _DESCRIPTIVE_NAME_TEMPLATES
    detail_line = (
        "definition, explanation, key properties, when/how to use, with worked "
        "examples and step-by-step reasoning INLINED in full"
        if math_like else
        "complete definition and explanation, key characteristics, processes or "
        "relationships, with concrete examples INLINED within the description"
    )
    return f"""\
You are a STRICT concept mapping engine for school {s} (board-level rigor).
Return ONLY a JSON object: {{"rows": [{{"topic": "", "concept": "", "concept_description": "", "keywords": ""}}, ...]}}.

OUTPUT CONTRACT (MUST FOLLOW EXACTLY):
- concept_description is ONE string with sections separated by " // " in this order:
  Description: <{detail_line}> // Types: <Type 01: Name Case 01: <concrete example prompt, e.g. 'Evaluate: ...', 'Prove: ...'> Case 02: ... Type 02: ...> // Misconception: <common student misconceptions> (omit section if none apply)
- Use " // " as the separator. Do NOT use newlines inside concept_description.

CONCEPT NAMING:
1) Names must be academic, specific and content-based. Prefer these templates:
{templates}
2) Sibling concepts must NOT repeat the same leading phrase; vary the stems.
3) NEVER chain names with '&'. Culmination rows are named
   "Culmination - <A>, <B> and <C>" (comma list with a final 'and').

TOPIC SEGREGATION:
- A topic groups 5-15 related concepts; follow the chapter's section flow.
- The LAST concept of every topic is exactly one culmination row synthesizing it.
- NEVER create a topic for exercises (Exercise 1.1, Ex 2.1...). Distribute
  exercise problems into the content topics they test, as extra Types/Cases.

TYPES:
- Classify EVERY question/numerical/problem variety found in the chapter
  (including exercise sections) under its concept.
- Zero-padded numbering (Type 01, Case 01). Every Case MUST carry a concrete
  example question/prompt.

SOURCE HYGIENE:
- NEVER reference source artifacts: no "Example 19", "Examples Type III",
  "Fig 2", "Table no. 1", "ex 1" - resolve each reference by inlining its
  actual worked content instead.
- NEVER use the words "MMD" or "MMDs"; say "chapter", "problem", "example".

QUALITY RULES:
- Produce 40-60 concepts (excluding culmination rows).
- No duplicates or near-duplicates: theory + exercise overlap -> output ONCE.
- No vague filler ("Introduction", "Misc", "Basics").
- Small, testable, taggable concepts; descriptions stay within syllabus scope
  (max ~90 words per section).
- keywords: 3-6 comma-separated lowercase terms.
"""


def _openai_json(system: str, user: str, max_tokens: int = 32000,
                 retries: int = 3) -> dict:
    """One JSON-mode chat call with retries; returns the parsed object."""
    import json
    import time
    from openai import OpenAI

    client = OpenAI()
    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            resp = client.chat.completions.create(
                model=config.OPENAI_MODEL,
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": user}],
                response_format={"type": "json_object"},
                max_completion_tokens=max_tokens,
            )
            return json.loads(resp.choices[0].message.content or "{}")
        except Exception as e:  # noqa: BLE001 — retry then surface
            last_err = e
            if attempt < retries:
                time.sleep(2)
    raise RuntimeError(f"OpenAI extraction failed after {retries} retries: {last_err!r}")


def _trim(text: str, max_chars: int = 220_000) -> str:
    if len(text) <= max_chars:
        return text
    return text[: int(max_chars * 0.7)] + "\n\n[...TRIMMED...]\n\n" + text[-int(max_chars * 0.3):]


def _is_culmination(title: str) -> bool:
    return (title or "").strip().lower().startswith("culmination")


def _ensure_culmination_per_topic(records: list[dict]) -> list[dict]:
    """Guarantee exactly one culmination row, last, for every topic.

    Mirrors the vendored engine's deterministic post-pass: keep the first
    culmination if several exist, synthesize one if the model omitted it.
    """
    topic_order: list[str] = []
    by_topic: dict[str, list[dict]] = {}
    for rec in records:
        topic = (rec.get("topic") or "General").strip() or "General"
        if topic not in by_topic:
            by_topic[topic] = []
            topic_order.append(topic)
        by_topic[topic].append(rec)

    out: list[dict] = []
    for topic in topic_order:
        rows = by_topic[topic]
        regular = [r for r in rows if not _is_culmination(r.get("concept_title", ""))]
        culms = [r for r in rows if _is_culmination(r.get("concept_title", ""))]
        out.extend(regular)
        if culms:
            out.append(culms[0])
        else:
            out.append({
                "topic": topic,
                "concept_title": f"Culmination - {topic}",
                "concept_details": (
                    f"Description: Consolidates the key ideas of '{topic}' into one "
                    "integrated understanding. // Types: Type 01: Mixed application "
                    "Case 01: Solve questions combining multiple concepts from this topic."
                ),
                "keywords": "",
            })
    return out


def concepts_from_mmd(mmd_text: str, *, subject: str = "",
                      live: bool | None = None) -> list[dict]:
    """Parse an MMD document into concept records (post-learning)."""
    use_live = config.use_live_generation() if live is None else live
    if use_live:
        data = _openai_json(
            _concepts_system(subject),
            "Extract 40-60 high-quality concepts from this chapter. Group them "
            "under topics (5-15+ concepts each, last row per topic is the "
            "culmination), following the chapter's section flow:\n\n"
            + _trim(mmd_text),
        )
        out = []
        for row in data.get("rows", []):
            title = (row.get("concept") or "").strip()
            if not title:
                continue
            out.append({
                "topic": (row.get("topic") or "Topic 01").strip(),
                "concept_title": title,
                "concept_details": (row.get("concept_description") or "").strip(),
                "keywords": (row.get("keywords") or "").strip(),
            })
        if not out:
            raise RuntimeError("live concept extraction returned no rows")
        return _ensure_culmination_per_topic(out)
    config.require_generation_live()
    # Dry: treat markdown headings as topics and bullet/para lines as concepts.
    topic = "Topic 01: Overview"
    out: list[dict] = []
    for line in mmd_text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("##"):
            topic = line.lstrip("# ").strip() or topic
        elif line.startswith("#"):
            continue
        else:
            title = line.split(":")[0].split(".")[0].strip()[:80] or "Concept"
            out.append({
                "topic": topic,
                "concept_title": title,
                "concept_details": (
                    f"Description: {line[:200]} "
                    "// Types: Type 01: Standard "
                    "// Misconception: commonly confused with related ideas."
                ),
                "keywords": ", ".join(title.lower().split()[:5]),
            })
    return out or [{
        "topic": topic, "concept_title": "Concept 01",
        "concept_details": "Description: (empty document) // Types: // Misconception:",
        "keywords": "",
    }]


# Pre-learning derivation: ported from the vendored
# concept_mapping_to_prelearning engine — dependency-architecture prompt with
# CRITICAL SYLLABUS FILTER, naming patterns, cognitive tags (FL/NU/VC/RS/GR),
# strict topic/concept counts, and a second "syllabus boundary" auditor pass.
_PRE_MIN_T, _PRE_MAX_T = 4, 6
_PRE_MIN_CT, _PRE_MAX_CT = 5, 7


def _board_guidance(board: str) -> str:
    b = (board or "").strip().upper()
    if "CBSE" in b:
        return ("BOARD-SPECIFIC CURRICULUM: CBSE-aligned. Judge previous-grade vs "
                "current-grade content and chapter order using official CBSE/NCERT "
                "progression (Classes 6-10) for this subject — not ICSE ordering.")
    if "ICSE" in b:
        return ("BOARD-SPECIFIC CURRICULUM: ICSE-aligned. Use typical official ICSE "
                "syllabus progression for this subject and grade; do not substitute "
                "NCERT/CBSE chapter order.")
    return f"BOARD-SPECIFIC CURRICULUM: Board {board!r}; use its official progression."


def _prelearning_system(subject: str, grade: str, board: str) -> str:
    return f"""\
You are an expert curriculum designer specializing in dependency-based learning
architecture aligned with formal school syllabi (ICSE/CBSE and equivalents).
Generate PRE-LEARNING concepts for the given chapter.

OBJECTIVE — output ONLY concepts that are strict prerequisites for the chapter,
belong to previous grade levels OR foundational knowledge expected before this
grade, and were reasonably taught/encountered before this chapter. They are NOT
chapter content, simplified re-teaching, or topic introductions.

CRITICAL SYLLABUS FILTER (MANDATORY): reject any concept explicitly taught as
new in the CURRENT grade for this subject, and any concept typically introduced
in this chapter or later chapters of the same course. Only include
previous-grade or clearly foundational concepts (basic arithmetic, basic
algebra, general science literacy, earlier-level graph reading...).

STRICT EXCLUSIONS: no "Introduction to...", "Definition of...", "Overview
of...", "Examples of..."; nothing taught inside the chapter itself.

INCLUSION TEST per concept: "If a student does NOT know this, will they
struggle to understand the chapter even after teaching?" Include only if YES.

CONCEPT DESIGN: atomic but meaningful; each concept is a skill, relationship,
or reasoning structure; do not fragment definition/formula/example apart.

NAMING RULES: prefer patterns like "Relationship Between _ and _",
"Application of _ in _ Contexts", "Interpretation of _ in
Mathematical/Scientific Situations", "Quantitative Handling of _",
"Structural Understanding of _", "Transformation and Manipulation of _".
NEVER "Types of _", "Definition of _", "Basics of _". NEVER chain names
with '&' (use commas with a final 'and'). Sibling concepts must not repeat
the same leading phrase.

COGNITIVE TAGGING (MANDATORY): one primary tag per concept:
FL=Foundational Logic | NU=Numerical Handling | VC=Vocabulary Concept |
RS=Real-world Sense | GR=Graphical Reasoning.

COUNTS (STRICT): {_PRE_MIN_T}-{_PRE_MAX_T} topics; every topic has
{_PRE_MIN_CT}-{_PRE_MAX_CT} concepts. Order by dependency. No duplicates.

CONCEPT DESCRIPTION FORMAT (MANDATORY): one string, exactly three sections,
separated by " // ":
Description: <what the student should already know; 2-4 short lines; must not
teach the chapter> // Types: <at least two numbered types, each with concrete
cases: Type 01: <title> Case 01: <example prompt> Case 02: ... Type 02: ...>
// Misconception: <typical prior-knowledge gaps, or N/A>.
Zero-padded labels exactly (Type 01:, Case 01:). NEVER reference source
artifacts ("Example 19", "Fig 2", "Table no. 1") and never the words "MMD".

OUTPUT (STRICT JSON ONLY): {{"topics": [{{"topic_name": "", "concepts":
[{{"parent_concept": "", "concept_name": "", "concept_description": "",
"tag": ""}}]}}]}}.

FINAL VALIDATION: for each concept ask "Was this already expected knowledge
BEFORE this grade (or clearly foundational)?" — if unsure or borderline,
REMOVE or REPLACE with a safer prior-grade prerequisite.

RUN CONTEXT: Subject: {subject} | Grade: {grade} | Board: {board}
{_board_guidance(board)}"""


_PRE_AUDITOR_SYSTEM = """\
You are a strict curriculum auditor for ICSE/CBSE-aligned pre-learning.
You receive draft pre-learning JSON ("topics" with nested "concepts") plus
chapter context. REMOVE or REPLACE any concept that is taught as new in the
current grade, introduced in this chapter or later in the same course, or
fails "was this already expected knowledge before this grade?" (unsure or
borderline -> REPLACE). Allow previous-grade ideas and foundational skills.
STRUCTURE: output exactly the same number of topics, and per topic exactly
the same number of concepts — substitute rejected rows, never delete slots.
Keep the same schema and the Description: // Types: // Misconception: format
with Type 01/02 and Case 01/02 labels, plus the tag (FL|NU|VC|RS|GR).
Return ONLY JSON with one key "topics". No markdown, no commentary."""


def _flatten_pre_topics(data: dict) -> list[dict]:
    out: list[dict] = []
    for topic in data.get("topics", []):
        t_name = (topic.get("topic_name") or "Foundations").strip()
        if "(pre-learning)" not in t_name.lower():
            t_name = f"{t_name} (Pre-Learning)"
        for c in topic.get("concepts", []):
            title = (c.get("concept_name") or "").strip()
            if not title:
                continue
            tag = (c.get("tag") or "").strip().upper()
            parent = (c.get("parent_concept") or "").strip()
            keyword_bits = [b for b in (f"tag {tag}" if tag else "",
                                        f"parent: {parent}" if parent else "") if b]
            out.append({
                "topic": t_name,
                "concept_title": title,
                "concept_details": (c.get("concept_description") or "").strip(),
                "keywords": "; ".join(keyword_bits),
            })
    return out


def pre_learning_from_rows(
    rows: list[dict], *, subject: str = "", grade: str = "", board: str = "",
    chapter_title: str = "", live: bool | None = None,
) -> list[dict]:
    """Derive pre-learning records from concept-mapping rows (dicts).

    rows: [{concept_title, concept_details, topic}, ...] — the chapter's
    post-learning concept map.
    """
    use_live = config.use_live_generation() if live is None else live
    if not use_live:
        config.require_generation_live()
    if not use_live:
        return [{
            "topic": f"{(r.get('topic') or 'Topic 01')} (Pre-Learning)",
            "concept_title": f"Pre: {r['concept_title']}",
            "concept_details": (
                f"Description: foundational idea required before learning "
                f"'{r['concept_title']}'. // Types: Type 01: Prerequisite recall "
                "// Misconception: assuming the prerequisite is already mastered."
            ),
            "keywords": r.get("keywords", ""),
        } for r in rows]

    listing = "\n".join(
        f"- [{(r.get('topic') or '')[:60]}] {r['concept_title']}: "
        f"{(r.get('concept_details') or '')[:260]}"
        for r in rows
    )
    user = (
        f"CHAPTER: {chapter_title or '(untitled)'}\n"
        f"Subject: {subject} | Grade: {grade} | Board: {board}\n\n"
        "CONCEPT MAPPING (current chapter content — exclude from pre-learning):\n"
        + _trim(listing, 80_000)
    )
    system = _prelearning_system(subject, grade, board)
    draft = _openai_json(system, user)
    if not draft.get("topics"):
        raise RuntimeError("live pre-learning derivation returned no topics")

    # Stage 2: syllabus boundary auditor (replaces violating rows in place).
    import json as _json
    audited = _openai_json(
        _PRE_AUDITOR_SYSTEM,
        f"Chapter: {chapter_title} | Subject: {subject} | Grade: {grade} | "
        f"Board: {board}\n\nDRAFT:\n" + _json.dumps(draft)[:120_000],
    )
    final = audited if audited.get("topics") else draft

    out = _flatten_pre_topics(final)
    if not out:
        raise RuntimeError("live pre-learning derivation returned no concepts")
    return out


def pre_learning_from_concepts(concepts: list[models.Concept], *, live: bool | None = None) -> list[dict]:
    """Derive pre-learning concept records from existing post-learning concepts."""
    use_live = config.use_live_generation() if live is None else live
    if use_live:
        chapter = concepts[0].topic.chapter if concepts else None
        return pre_learning_from_rows(
            [{
                "topic": c.topic.topic_title,
                "concept_title": c.concept_title,
                "concept_details": c.concept_details,
                "keywords": c.keywords,
            } for c in concepts],
            subject=chapter.subject if chapter else "",
            grade=chapter.grade if chapter else "",
            board=chapter.board if chapter else "",
            chapter_title=chapter.chapter_title if chapter else "",
            live=True,
        )
    config.require_generation_live()
    out: list[dict] = []
    for c in concepts:
        out.append({
            "source_concept_id": c.id,
            "topic": f"{c.topic.topic_title} (Pre-Learning)",
            "concept_title": f"Pre: {c.concept_title}",
            "concept_details": (
                f"Description: foundational idea required before learning "
                f"'{c.concept_title}'. // Types: Type 01: Prerequisite recall "
                "// Misconception: assuming the prerequisite is already mastered."
            ),
            "keywords": c.keywords,
        })
    return out

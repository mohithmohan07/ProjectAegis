"""Content generation: questions from concepts, concepts from MMD.

All functions have a dry path (deterministic, no API keys — used for the MVP
and tests) and a live hook that delegates to the vendored OpenAI-backed
scripts. The dry path is intentionally realistic: it returns fully-populated
records so the post-generation pipeline and the canonical writer are always
exercised end to end.
"""
from __future__ import annotations

import copy
import os
import random
import re
import threading

from .. import bulk_import as bi
from .. import config, models
from . import concept_cleanup
from . import concept_validator as cv
from . import katex_rules as kr
from . import concept_refiner as cr
from . import prompts
from . import progress
# Imported for its prompt registrations (assessment.* keys used by _identify_system).
from . import assessment_prompts as _assessment_prompts_registration  # noqa: F401

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
        question_text = _stem_for(cognitive_skill, difficulty, concept, idx)
        if _is_math(concept):
            question_text = f"{question_text} Express the key relation as {_sample_equation(concept)}."
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
            "question": question_text,
            "question_appears_in": appears_in,
            # Plain-text question (+ concept context) for the AI evaluator.
            "question_text": bi.to_plain_text(
                f"{question_text}\nConcept context: {details}" if details
                else question_text),
            "answer_explanation": (
                f"{model_answer} Reference: {_concept_reference_link(concept)}."
            ),
            "answers": [],
            "sub_questions": [],
            "origin": "concept_mapping",
        }
        if question_type == "objective":
            correct = f"{concept.concept_title} (correct: {details[:80] or 'as defined'})"
            if _is_math(concept):
                correct = f"{correct} — {_sample_equation(concept)}"
            record["answers"] = [
                {"answer_type": "Phrases", "answer_content": correct,
                 "correct_answer": "Yes", "answer_weightage": "1"},
            ] + [
                {"answer_type": "Phrases", "answer_content": d,
                 "correct_answer": "No", "answer_weightage": "0"}
                for d in _dry_distractors(concept)
            ]
            record["answer_explanation"] = (
                f"The correct option states '{concept.concept_title}' accurately. "
                "The distractors are wrong because they describe a related concept, "
                "the converse relation, or omit the key condition."
            )
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


_IDENTIFY_CAT = "Build Assessments · upload extraction"

_TYPE_HINTS = {
    "objective": "OBJECTIVE — MCQ / fill-in-the-blank. For MCQs emit 3-4 "
                 "options with exactly one correct_answer = 'Yes'.",
    "subjective": "SUBJECTIVE — short answer; emit mark-wise rubric points "
                  "whose weightages sum to the marks.",
    "descriptive": "DESCRIPTIVE — long answer; emit mark-wise rubric points "
                   "(and sub_questions for multi-part questions) summing to marks.",
}
for _k, _v in _TYPE_HINTS.items():
    prompts.register(f"identify.type_hint.{_k}", category=_IDENTIFY_CAT,
                     label=f"Upload type hint · {_k}", default=_v)

prompts.register(
    "identify.intent.extract", category=_IDENTIFY_CAT,
    label="Upload intent · extract existing questions",
    default="EXTRACT every assessment question already present in the document. "
            "Preserve each question's original wording and intent — do NOT invent "
            "new questions. When a question's options, answer, solution or marking "
            "scheme is present, capture it faithfully; otherwise leave answers empty.")

prompts.register(
    "identify.intent.create", category=_IDENTIFY_CAT,
    label="Upload intent · create new questions",
    default="CREATE fresh, exam-grade questions from the document's content. Cover "
            "the key ideas across the material; never copy sentences verbatim as "
            "questions, and never drift off the document's topic.")

prompts.register(
    "identify.system", category=_IDENTIFY_CAT,
    label="Upload question-identification system prompt",
    description="Variables: {{intent}}, {{type_block}}, {{content_format}}, "
                "{{output}}.",
    variables=("intent", "type_block", "content_format", "output"),
    default="""\
You are an assessment digitizer for Indian school boards (ICSE/CBSE). You read
a document already converted to Markdown/MMD (mathematics in LaTeX) and return
assessment questions in a STRICT JSON schema.

TASK: {{intent}}
{{type_block}}
Classify each question's question_category, cognitive_skills and
level_of_difficulty. Add a "sheet_kind" field (objective|subjective|descriptive)
to every question object.

STANDARD VALUES (use EXACTLY these):
- cognitive_skills: Remember | Understand | Apply | Analyse | Evaluate | Create
- level_of_difficulty: Less | Moderate | High
- answer_type: Phrases | Equation | Image

{{content_format}}

{{output}}

Return ONLY the JSON object.""")


def _identify_system(upload_type: str, question_type: str, *, extract: bool) -> str:
    """System prompt for live question identification from an uploaded document."""
    intent = prompts.get_text(
        "identify.intent.extract" if extract else "identify.intent.create")
    if question_type == "auto":
        type_block = (
            "QUESTION TYPES — the document may contain a MIX of types. For EACH "
            "question, set \"sheet_kind\" to the type that best fits it and shape "
            "it accordingly:\n"
            f"- objective: {prompts.get_text('identify.type_hint.objective')}\n"
            f"- subjective: {prompts.get_text('identify.type_hint.subjective')}\n"
            f"- descriptive: {prompts.get_text('identify.type_hint.descriptive')}\n"
            "Preserve a question's natural type — do NOT force everything into one "
            "type. A long/multi-part question with parts (a),(b),(c) is descriptive "
            "and MUST keep its parts in the sub_questions slots, never split into "
            "separate questions."
        )
    else:
        type_block = (
            f"TARGET QUESTION TYPE (every question is this type): "
            f"{prompts.get_text('identify.type_hint.' + question_type)}\n"
            f"Set \"sheet_kind\" to \"{question_type}\" on every question."
        )
    return prompts.render(
        "identify.system",
        intent=intent, type_block=type_block,
        content_format=prompts.get_text("content.katex_rules"),
        output=prompts.get_text("assessment.output"),
    )


# Safety bound on questions identified from one upload (prevents a runaway
# response from exhausting memory); high enough never to truncate real banks.
_IDENTIFY_SAFETY_CAP = 5000


def _identify_row_to_record(row: dict, *, auto: bool, question_type: str) -> dict | None:
    if not isinstance(row, dict):
        return None
    question = (row.get("question") or "").strip()
    if not question:
        return None
    kind = (_normalize_sheet_kind(row.get("sheet_kind") or row.get("question_type"))
            if auto else question_type)
    try:
        marks = float(row.get("marks") or _default_marks(kind))
    except (TypeError, ValueError):
        marks = _default_marks(kind)
    return {
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
    }


def _live_identify_questions_from_mmd(
    mmd_text: str, *, upload_type: str, question_type: str, textbook_mode: str = "",
) -> list[dict]:
    """Live (OpenAI) question identification from an uploaded document's MMD.

    The document is processed in ordered chunks (never trimmed) so every
    question in a large bank is captured; results are merged and de-duplicated.
    """
    extract = _identify_is_extract(upload_type, textbook_mode)
    system = _identify_system(upload_type, question_type, extract=extract)
    auto = question_type == "auto"
    tail = (
        "Return EVERY question you find in this section, each tagged with its own "
        "\"sheet_kind\" (objective|subjective|descriptive), as a JSON object with "
        "a \"questions\" array."
        if auto else
        f"Return EVERY {question_type} question in this section as specified "
        "above, as a JSON object with a \"questions\" array."
    )
    chunks = _split_mmd_into_chunks(mmd_text)
    progress.log(
        f"Identifying questions from {len(mmd_text):,} chars across "
        f"{len(chunks)} chunk(s) (type: {question_type}, "
        f"{'extract' if extract else 'create'}).")

    records: list[dict] = []
    seen: set[str] = set()
    for i, chunk in enumerate(chunks, start=1):
        progress.step(f"Question identification — chunk {i}/{len(chunks)}",
                      value=(i - 1) / max(len(chunks), 1))
        user = f"DOCUMENT (MMD) — section {i} of {len(chunks)}:\n{chunk}\n\n{tail}"
        data = _openai_json(system, user)
        added = 0
        for row in (data.get("questions") or []):
            rec = _identify_row_to_record(row, auto=auto, question_type=question_type)
            if rec is None:
                continue
            norm = bi.normalize_question_text(rec["question"])
            if norm and norm in seen:
                continue
            if norm:
                seen.add(norm)
            records.append(rec)
            added += 1
            if len(records) >= _IDENTIFY_SAFETY_CAP:
                break
        progress.log(f"  chunk {i}/{len(chunks)}: {added} new questions")
        if len(records) >= _IDENTIFY_SAFETY_CAP:
            progress.log("Reached safety cap; stopping.", level="warn")
            break
    if not records:
        raise RuntimeError("live question identification returned no questions")
    progress.set_progress(1.0, label="Question identification complete")
    progress.log(f"Identified {len(records)} unique questions.", level="success")
    return records


# --------------------------------------------------------------------------- #
# Concepts from MMD (Build Concepts - post learning)
# --------------------------------------------------------------------------- #

# Live concept-extraction prompts: API-driven extraction with a second-pass
# consolidation call for chapter-wide intelligence (dedup, naming variety,
# culminations, Types discipline). Minimal Python cleanup only (& names,
# dangling refs) — no Type renumbering or group-column output at this stage.

_CONCEPTS_CAT = "Build Concepts · post-learning extraction"

prompts.register(
    "concepts.name_templates.math", category=_CONCEPTS_CAT,
    label="Concept naming guidance (math/physics)",
    default="""\
   Name each concept after the specific idea it teaches — use the chapter's own
   vocabulary. Vary sentence structure across siblings (do NOT repeat a shared
   opener like "Properties of…" or "Applications of…" on multiple rows). Good
   names read like precise textbook sub-headings, not formulaic labels.""")

prompts.register(
    "concepts.name_templates.descriptive", category=_CONCEPTS_CAT,
    label="Concept naming guidance (other subjects)",
    default="""\
   Name each concept after the specific idea it teaches — use the chapter's own
   vocabulary. Vary sentence structure across siblings (do NOT repeat a shared
   opener like "Structure and Function of…" or "Importance of…" on multiple rows).
   Good names read like precise textbook sub-headings, not formulaic labels.""")

prompts.register(
    "concepts.types_guidance.math", category=_CONCEPTS_CAT,
    label="Types classification guidance (math-heavy subjects)",
    default="""\
   Types classify EVERY distinct question/task pattern under the concept —
   numerical, formula, proof, construction, graph, diagram, reasoning, or word
   problem patterns as the source demands. Mine the Question / Task Inventory
   first, then fold each reusable pattern into the concept it assesses.
   A Type is one solving/answering/task pattern. A Case is a DEFINED sub-type
   of that pattern (what is given, what is asked, with what constraint) —
   never a raw question. Every concrete source question goes on its own
   "Example:" line under the Case it instantiates, copied in FULL without
   truncation (all givens, values, conditions, figure references and their
   image URLs). Include EVERY source example available for each Case; only
   skip Types when a concept has zero meaningful assessable task varieties.""")

prompts.register(
    "concepts.types_guidance.descriptive", category=_CONCEPTS_CAT,
    label="Types classification guidance (all subjects)",
    default="""\
   Types classify EVERY distinct question/task variety under the concept:
   explanation, comparison, reasoning, diagram, data/table/graph, map, source,
   passage, case, experiment, observation/inference, grammar transformation,
   writing, literature extract, coding/debugging, short-answer, long-answer, or
   numerical patterns as appropriate to the subject. Mine the Question / Task
   Inventory first; never force non-math material into numerical templates.
   A Type is one reusable assessable format. A Case is a DEFINED sub-type of
   that format (what is given, what is asked, with what constraint) — never a
   raw question. Every concrete source question goes on its own "Example:"
   line under the Case it instantiates, copied in FULL without truncation.
   Include EVERY source example available for each Case; only skip Types when
   the concept has zero meaningful assessable varieties.""")

prompts.register(
    "concepts.types_example", category=_CONCEPTS_CAT,
    label="Types section format example",
    default=(
        "Types: Type 01: Questions based on computing resistance — given "
        "electrical readings, compute resistance using Ohm's law "
        "Case 01: Ohm's law formula-based question when V and I are given "
        "(without circuit) "
        "Example: Calculate the resistance of the circuit if V is 220 V and "
        "I is 0.5 mA. "
        "Case 02: Ohm's law formula-based question when the circuit diagram "
        "is given "
        "Example: Calculate the resistance for the given circuit. "
        "(Refer fig. 11.1) ![](https://cdn.mathpix.com/cropped/fig_11_1.jpg)"
    ))

prompts.register(
    "concepts.detail.math", category=_CONCEPTS_CAT,
    label="Description guidance (math/physics)",
    default="45-90 words, source-grounded: define the idea, state the key "
            "rule/property or method, include conditions/when to use it, and "
            "add one compact worked cue only when it clarifies the concept")

prompts.register(
    "concepts.detail.descriptive", category=_CONCEPTS_CAT,
    label="Description guidance (other subjects)",
    default="45-90 words, source-grounded: explain the idea clearly for lesson "
            "planning, include the key characteristics/process/relationship, "
            "and add one compact example only when it clarifies the concept")

prompts.register(
    "concepts.system", category=_CONCEPTS_CAT,
    label="Concept-mapping system prompt",
    description="Variables: {{subject}}, {{detail_line}}, {{name_templates}}, "
                "{{types_guidance}}, {{types_example}}.",
    variables=("subject", "detail_line", "name_templates",
               "types_guidance", "types_example"),
    default="""\
You are a concept mapping engine for school {{subject}} (board-level rigor) that
mirrors how the chapter is actually TAUGHT in class.
Return ONLY a JSON object: {"rows": [{"topic": "", "concept": "", "concept_description": "", "keywords": ""}, ...]}.

TOPICS MUST FOLLOW THE TEXTBOOK (coherence is non-negotiable):
- Use the chapter's OWN section structure. Each topic = a real section of the
  text, in the SAME reading order the chapter presents it.
- Name each topic EXACTLY as the textbook section heading reads — strip any
  leading decimal/section numbers (1., 1.1, 1.2, 2.3, etc.) and use the words
  only. Do not invent new thematic umbrella topics, and do not merge two
  textbook sections into one.
- A concept belongs to the topic where the textbook teaches it. NEVER pull
  concepts from different sections together under one synthesized topic.
- Emit topics and their concepts in textbook progression (top to bottom).
- NEVER create a topic for exercises. Fold exercise problems into the content
  concept they practise, as solving varieties under Types.

CONCEPT GRANULARITY (fine-grained, discrete, non-redundant):
- Break each section into small, isolated, testable concepts (mastery-friendly).
- Each idea appears EXACTLY ONCE across the chapter. Merge or drop near-duplicates;
  if two sections share an idea, teach it once and reference it elsewhere.
- No vague filler ("Introduction", "Misc", "Basics").

CONCEPT NAMING (no repetition, no section numbers):
{{name_templates}}
- NEVER prefix or embed decimal section numbers (1., 1.1, 1.2, 2.3, Exercise 1.1,
  Ex 2.1, etc.) in topic or concept names — use descriptive words only.
- Sibling concepts under the same topic must use DISTINCT stems; never repeat the
  same opening phrase on multiple rows.
- NEVER chain names with '&'. Culmination rows are named
  "Culmination - <A>, <B> and <C>" (comma list with a final 'and').

OUTPUT CONTRACT for concept_description (ONE string, sections joined by " // "):
- ALWAYS start with: Description: <{{detail_line}}>
  The Description is used for lesson planning, assessments, and downstream
  content. It must be clear, text-material aligned, and complete enough to teach
  from, but not a long chapter dump. Prefer 2-4 compact sentences.
- Then include Types ONLY IF the concept has assessable question/problem
  varieties. {{types_guidance}}
  Format — use zero-padded numeric labels exactly "Type 01:", "Case 01:", and
  an "Example:" line for every concrete source question:
  Types: Type 01: <pattern definition> Case 01: <defined sub-type>
  Example: <full source question> Example: <another full source question>
  Case 02: <defined sub-type> Example: <...> Type 02: <next pattern> ...
  Restart at Type 01 within each concept — they are renumbered continuously
  across the whole chapter afterwards, so do NOT try to continue numbers yourself.
- Example Types block:
  {{types_example}}
- End with Misconceptions for normal concepts: name every REAL likely learner
  error from the material — one is the minimum, and when the material triggers
  several distinct errors, list them all in the same Misconceptions section.
  Do not invent filler misconceptions, and never write
  "N/A", "None", "Not applicable", or placeholder text.
- Valid structures:
  Description: ...
  Description: ... // Types: ...
  Description: ... // Misconception: ...
  Description: ... // Types: ... // Misconception: ...
- Use " // " as the separator. Do NOT use newlines inside concept_description.
- Do NOT mention groups, group columns, or assessment labels — not required here.

TOPIC CULMINATION:
- The LAST concept of every topic is exactly one culmination row that integrates
  that section's ideas (named "Culmination - ..."). Its Description will be set to
  "Recap"; still provide its Types (mixed multi-concept application problems) and
  Misconception.

SOURCE HYGIENE:
- NEVER reference source artifacts: no "Example 19", "Examples Type III",
  "Fig 2", "Table no. 1", "ex 1" - inline the actual worked content instead.
- NEVER use the words "MMD" or "MMDs"; say "chapter", "section", "problem".

QUALITY RULES:
- Cover the section exhaustively at concept level, but stay within syllabus scope
  (max ~90 words per section of the description).
- keywords: 3-6 comma-separated lowercase terms.
""")

prompts.register(
    "concepts.user", category=_CONCEPTS_CAT,
    label="Concept-mapping user instruction",
    description="Prepended to each chapter section/chunk. No variables.",
    default="Below is a section of the chapter in reading order. Map it into "
            "discrete, non-redundant concepts using the textbook's own topic "
            "headings (strip section numbers like 1.2 from names). One "
            "culmination per topic. Write clear source-grounded Descriptions; "
            "add Types only when there are source question/task/"
            "assessable formats; add Misconceptions for likely learner errors. "
            "Types use zero-padded 'Type 01:'/'Case 01:' labels:")


prompts.register(
    "concepts.consolidate", category=_CONCEPTS_CAT,
    label="Concept-map consolidation prompt",
    description="Variables: {{subject}}. Second-pass chapter-wide refinement.",
    variables=("subject",),
    default="""\
You are a senior curriculum editor reviewing a draft concept map for school
{{subject}}. You receive the merged output from chunked extraction. Return ONLY
a JSON object: {"rows": [{"topic": "", "concept": "", "concept_description": "",
"keywords": ""}, ...]}.

Your job (apply ALL of these intelligently — do not rely on downstream code):

1. **De-duplicate & de-redundancy.** Merge or drop concepts whose descriptions
   overlap heavily. Each distinct idea appears exactly once in the chapter.

2. **Distinct naming.** Rewrite sibling concept names so no two share the same
   leading phrase or formulaic opener. Names must be specific, not templated.

3. **Strip section numbers.** Remove decimal/section prefixes (1., 1.1, 1.2,
   2.3, Exercise 1.1, Ex 2.1, etc.) from topic and concept names — words only.

4. **Types (critical — preserve and enrich, never strip).** Types are how
   teachers segregate question varieties under each concept — generate them
   generously like a standalone types list, then the team picks what to keep.
   NEVER remove a Types block from the draft. If a concept involves calculation,
   problem-solving, application, diagrams, or exercises, it MUST have a rich
   Types section classifying ALL distinct question/task patterns (including
   exercise, source, diagram, data, language, coding, practical, or numerical
   items folded into the concept they test). Use zero-padded
   numeric labels: Type 01: <name> Case 01: <prompt> Case 02: ... Type 02: ...
   (restart at Type 01 per concept; continuous renumbering happens downstream).
   Only omit Types for concepts that are purely definitional with zero assessable
   formats. If the draft omitted Types where they belong, ADD them.

5. **Culmination.** Every topic ends with exactly one "Culmination - ..." row
   that integrates that topic's ideas. Place it last within its topic.

6. **Preserve order.** Keep textbook reading order for topics and concepts.

7. **No groups.** Do not mention groups, group columns, or assessment labels.

8. **Hygiene.** Keep Description // Types // Misconception structure; no source-artifact
   references ("Example 19", "Fig 2", "MMD"). Misconceptions should be present
   for normal concepts and must be specific and useful; never write N/A/None/filler.

9. **Chapter source.** When CHAPTER SOURCE text is provided, mine it for all
   assessable question/task patterns to populate Types under the concepts they test.

10. **Description quality.** Descriptions are used for lesson planning,
    assessments, and downstream content. Keep them source-grounded, 2-4 compact
    sentences, clear enough to teach from, and not overloaded with every detail.

Return the full refined chapter map — same schema, improved quality. Do NOT
remove Types sections — a dedicated Types pass follows; preserve any Types already
present.""")


prompts.register(
    "concepts.description_refine", category=_CONCEPTS_CAT,
    label="Description-only refinement pass",
    description="Variables: {{subject}}. Uses chapter source to polish descriptions.",
    variables=("subject",),
    default="""\
You are a description-only editor for school {{subject}} concept maps.

INPUT: a concept map plus CHAPTER SOURCE text.
OUTPUT: Return ONLY JSON {"rows": [{"topic": "", "concept": "",
"concept_description": "", "keywords": ""}, ...]} with the SAME rows.

Your ONLY job is to make the Description section useful for lesson planning,
assessment building, and downstream content.

Rules:
1. Keep topic names, concept names, keywords, and row order the same.
2. Rewrite ONLY the Description section using the CHAPTER SOURCE.
3. Preserve any Types section exactly if it already exists.
4. Preserve Misconception only if it is specific and useful; otherwise omit it.
   Do not write "N/A", "None", "Not applicable", or generic filler.
5. Description must be source-grounded, clear, and complete enough to teach from:
   include what the concept means, the key rule/process/relationship, important
   conditions, and one compact example only when it helps.
6. Do NOT dump the full textbook. Target 2-4 compact sentences, roughly 45-90
   words. Avoid repetitive wording across sibling concepts.
7. Valid concept_description forms:
   Description: ...
   Description: ... // Types: ...
   Description: ... // Misconception: ...
   Description: ... // Types: ... // Misconception: ...
8. Do not mention groups, group columns, assessment labels, source artifacts, or
   the words "MMD"/"MMDs".""" )


prompts.register(
    "concepts.types_assign", category=_CONCEPTS_CAT,
    label="Types-only assignment pass",
    description="Variables: {{subject}}, {{types_guidance}}, {{types_example}}.",
    variables=("subject", "types_guidance", "types_example"),
    default="""\
You are a Types-only classifier for school {{subject}} concept maps.

Your ONLY job: populate a rich Types section in every concept_description that
has assessable question, numerical, diagram, or exercise formats. This mirrors
how curriculum teams first generate a comprehensive types list, then manually
keep what they need.

INPUT: a draft concept map (Description is already refined; Types may or may
not exist, and Misconceptions should already be present) plus CHAPTER SOURCE text.

OUTPUT: Return ONLY JSON {"rows": [{"topic","concept","concept_description","keywords"}, ...]}
with the SAME rows (same topics and concept names) but Types sections filled in.

RULES:
1. Keep each Description and any existing useful Misconception text UNCHANGED
   (do not rewrite them).
2. Insert or replace ONLY the Types section. Place it after Description and
   before Misconception if Misconception exists:
   Description: ... // Types: ... // Misconception: ...
   Description: ... // Types: ...
3. {{types_guidance}}
4. Format — zero-padded numeric labels exactly "Type 01:", "Case 01:", and an
   "Example:" line per concrete source question:
   Types: Type 01: <pattern definition> Case 01: <defined sub-type>
   Example: <full source question> Example: <another full source question>
   Case 02: <defined sub-type> Example: <...> Type 02: <next pattern> ...
   (restart at Type 01 per concept; continuous renumbering across the chapter
   happens downstream).
5. Example:
   {{types_example}}
6. Mine CHAPTER SOURCE for ALL assessable question/task patterns; fold each into
   the concept it tests as Types/Cases/Examples.
7. Omit Types for purely definitional concepts with zero assessable formats.
   Every problem-solving, calculation, application, or exercise-backed concept
   MUST have Types with at least two varieties and at least one Case per Type.
   Cases are DEFINED sub-types (what is given, what is asked, with what
   constraint) — never raw questions; list a Case ONLY when a concrete source
   example exists; never invent empty Case placeholders.
8. Example lines MUST quote the full source question/task verbatim — do not
   shorten, paraphrase, or abbreviate; teachers execute from these cells.
   When the question needs a figure/diagram, keep the figure reference AND
   embed the Mathpix image URL from the source right after it, e.g.
   "(Refer fig. 11.1) ![](https://cdn.mathpix.com/...)".
9. Mine ALL assessable problems from the source; skipping exercises, in-text
   checkpoint questions, or activities defeats homework / in-class /
   board-teaching categorisation downstream.
10. Place each question under a concept that is taught at or before the point
    of the chapter where the question appears — NEVER attach a question to an
    earlier concept when it actually assesses later material.
11. Textbook ACTIVITY tasks belong on the topic's culmination row (mixed
    multi-concept application), not on single-focus concepts.
12. Culmination rows MUST include Types for mixed multi-concept application problems.
13. NEVER mention groups or group columns.""")


prompts.register(
    "concepts.skeleton.system", category=_CONCEPTS_CAT,
    label="Concept skeleton extraction system prompt",
    default="""\
Extract ONLY a clean teachable concept skeleton from a textbook section.
Return ONLY strict JSON:
{"rows":[{"topic":"","parent_concept":"","concept":"","concept_description":"","keywords":"","source_evidence":""}]}.

COVERAGE IS MANDATORY (most important rule):
- Build a compact teacher-facing concept map from the first line to the last.
- A normal textbook section yields 2-5 concepts; a full chapter usually yields
  18-40 concepts, depending on chapter size. Prefer discrete mastery units
  over broad umbrella concepts.
- A concept is a durable teaching/mastery objective, not every term, example,
  subheading, exercise prompt, case, or factual detail.
- When several definitions, examples, sub-types, or procedures serve one
  reusable objective, merge them under the same concept.
- Keep SEPARATE concepts when the textbook teaches distinct country cases,
  people, events, laws, or processes that a teacher would lesson-plan apart
  (e.g. under "The Making of Germany and Italy", create separate concepts for
  German unification and Italian unification — do not collapse them into one
  "Germany and Italy Unified..." row plus a culmination).
- Chapter-opening / pre-section narrative (HEADING PATH: [Chapter opening])
  MUST yield at least one teachable concept under the first main topic (e.g.
  Frédéric Sorrieu's utopian vision / the idea of the nation). Never skip
  opening content just because it precedes section 1.
- Do not create separate concept rows for cases/examples/questions. These are
  captured later as Types/Cases with full source questions.
- Explicit proofs and reusable methods/procedures are durable concepts. For
  Mathematics, derivations and formula-building sequences remain durable
  concepts even when they are presented inside worked exposition; never reduce
  them to disposable examples. When the input supplies MANDATORY DERIVATION /
  METHOD ANCHORS, emit one normal concept for every anchor and copy its
  anchor_id verbatim into source_evidence.
- All worked, numerical, contextual, or real-life problems are inventory items,
  not concept rows. They are classified later into distinct Types/Cases under
  the concept they assess; never include their solutions in the skeleton.
- A missed main teaching objective is a defect; a micro-concept row that should
  be a case/example is also a defect.

TOPIC SEGREGATION IS MANDATORY (second most important rule):
- topic MUST be the textbook MAIN SECTION heading the content sits under (use
  the HEADING PATH / SECTION HEADINGS given with the text); strip section numbers.
- When the textbook nests subsections under a main numbered section (e.g.
  "2 The Making of Nationalism in Europe" containing "2.1 The Aristocracy and
  the New Middle Class"), the MAIN section is the topic; each subsection
  becomes a parent_concept cluster (or concepts) under that topic — NEVER a
  topic of its own.
- An unnumbered chapter title or book title is NEVER a topic. Exception: when a
  numbered MAIN section intentionally has the same title as the chapter, that
  numbered section is a valid topic. Filing every concept under one unnumbered
  umbrella topic is still a defect.
- When the text spans several main section headings it MUST produce several
  topics, in the same reading order. Cover EVERY main section of the chapter —
  missing tail sections is a defect.

Rules:
- Do not invent textbook topics; preserve the section order from the source.
- Do not create exercise, example, review, or practice topics.
- Parent Concept is a meaningful cluster heading within a topic.
- Concept is one compact teachable mastery unit.
- Concept names must be specific and non-repetitive.
- No Types, no culmination rows, no groups, no assessment labels.
- No vague or structural names: Introduction, Overview, Basics, Basic Concepts,
  Misc, Miscellaneous, Examples, Practice, Definition of, Types of. Prefer a
  content-specific title for opening material (e.g. "Frédéric Sorrieu's Vision
  of Democratic and Social Republics") instead of the word "Introduction".
- Do not use exercise/question-type headings as concepts.
- Avoid repeated sibling openers.
- concept_description starts with "Description:" and is 2-4 compact sentences
  that name the key people, places, rules, formulas, or relationships from the
  source — not a vague summary.
- Keep source_evidence short: the phrase/heading/problem source that justifies the concept.
- source_evidence is for validation/debug only and must not be written to workbook.
""")

prompts.register(
    "concepts.method_anchor_recovery.system", category=_CONCEPTS_CAT,
    label="Focused derivation/method anchor recovery system prompt",
    default="""\
Perform focused recovery of missing derivation/method concepts.
Return ONLY strict JSON:
{"rows":[{"topic":"","parent_concept":"","concept":"","concept_description":"","keywords":"","source_evidence":""}]}.

Rules:
- Emit exactly one normal concept row for each supplied missing anchor and no
  other rows.
- Copy each supplied anchor_id verbatim, with identical uppercase spelling,
  into that row's source_evidence. Never substitute or invent an ID.
- Use the anchor's topic_hint exactly as topic.
- Ground the concept title and 2-4 sentence Description in that anchor's source
  evidence, required formulas, and the relevant chunk text. Explain the actual
  reusable derivation or method; never write a vague placeholder.
- Keep source_evidence concise but include the exact anchor_id and the specific
  source phrase/formula that supports the row.
- Include a meaningful parent_concept and keywords.
- Do not emit Types, Cases, Examples, exercises, culmination rows, or unrelated
  concepts.
""")

prompts.register(
    "concepts.canonicalize.system", category=_CONCEPTS_CAT,
    label="Chapter-wide concept canonicalization system prompt",
    default="""\
Clean a full chapter concept skeleton after all chunks have been merged.
Return ONLY strict JSON with the same schema:
{"rows":[{"topic":"","parent_concept":"","concept":"","concept_description":"","keywords":"","source_evidence":""}]}.

Rules:
- Produce a compact teacher-facing chapter map, not a micro-index.
- Merge duplicate, overlapping, repeated, or too-narrow rows into their nearest
  durable teaching concept. Terms, cases, examples, and exercise-question types
  belong inside concept descriptions/Types later, not as separate rows.
- Do not over-merge unrelated major objectives; each main topic should retain
  enough concepts for lesson planning. Distinct country/case studies under one
  topic (Germany vs Italy; Belgium vs Sri Lanka) stay as separate concepts.
- Keep chapter-opening concepts (named people, paintings, framing ideas that
  appear before section 1) — do not fold them away into a later section concept.
- Remove a concept when it is a duplicate, pure filler, a structural heading,
  a question/example label, or only a sub-type/case of another concept.
- Rows whose source_evidence contains a METHOD-* anchor are mandatory
  derivation/method concepts: never drop them, merge away their distinct
  method, or remove their anchor IDs.
- Ensure concept titles are unique across the chapter.
- Preserve textbook/topic order.
- Rewrite repetitive names.
- Parent concepts should group related concepts where possible, but a topic may
  legitimately have only 2-3 concepts when the source is thin — never invent
  filler to pad a parent.
- Do not create culmination rows.
- Do not generate Types.
- Do not rewrite good concepts unnecessarily.
- Do not invent exercise/example/review/practice topics.
- Never add filler concepts.
""")

prompts.register(
    "concepts.description_refine.system", category=_CONCEPTS_CAT,
    label="Description-only concept refinement system prompt",
    default="""\
You are a description-only editor. Rewrite only Description sections for a refined concept map.
Return ONLY strict JSON:
{"rows":[{"topic":"","parent_concept":"","concept":"","concept_description":"","keywords":""}]}.

Rules:
- Keep topic, parent_concept, concept name, keywords, and row order unchanged.
- Rewrite only the Description section.
- Description answers: what the concept is; what rule/process/relationship/method matters;
  when/why it is used. Ground it in the source: name the key people, places,
  dates, formulas, quantities, conditions, and causal links that a teacher
  needs — do not stop at a vague one-sentence gloss.
- END every Description with a mastery statement on its OWN line — a literal
  line break (\\n) followed by exactly this format:
  Achieving Mastery: <one short sentence stating what the learner can do when this concept is mastered>
  Example ending: "...\\nAchieving Mastery: Using the midpoint property to set up the smaller triangles correctly."
- Use 45-90 words unless the concept is very simple. Never leave a Description
  truncated mid-sentence.
- Do not include Types.
- Include a Misconceptions section for every non-culmination concept. Make it
  specific to the learner error this concept usually triggers; list EVERY real
  distinct misconception (one or more) in the same section; never use filler.
- Write the mastery statement exactly ONCE, at the end of the Description —
  never repeat it inside or after the Misconceptions section.
- No N/A, None, Not applicable, or placeholder text.
- No source artifacts such as MMD, Example 3, Fig 2, Table 1, Exercise 1.1, or
  page references. When the source text cites one, substitute the full actual
  content it points to (the real numbers, expression, conditions, or task) —
  e.g. write "such as expressing 1.272727... as 14/11", never "as in Example 8".
- Do NOT embed Mathpix / CDN image URLs in Description. Describe visual content
  in words here; image URLs belong only in Types Example lines (with their
  figure reference).
""")

prompts.register(
    "concepts.types_assign.system", category=_CONCEPTS_CAT,
    label="Types-only concept assignment system prompt",
    default="""\
You are a Types-only classifier. Assign Types only for assessable concepts.
Return ONLY strict JSON:
{"rows":[{"topic":"","parent_concept":"","concept":"","concept_description":"","keywords":""}]}.

Rules:
- Preserve Description exactly.
- Preserve topic, parent_concept, concept title, keywords, and row order exactly.
- Insert or replace only Types.
- Use the provided Question / Task Inventory and mined Types as the primary evidence.
- One Type = one distinct reusable subject-appropriate assessment/task pattern.
- One Case = one sub-type carrying one concrete source question prompt/stem.
- For Mathematics, Types may be numerical/formula/problem-solving/proof/graph/diagram patterns.
- For Science, Types may be numerical, diagram, experiment, observation, reasoning,
  comparison, process, or application patterns.
- For Social Science, Types may be definition, cause-effect, comparison, source,
  map, chronology, case, data, or long-answer patterns.
- For Languages and Literature, Types may be grammar transformation, comprehension,
  extract analysis, vocabulary-in-context, writing format, literary interpretation,
  theme, character, or reference-to-context patterns.
- For Computer Science, Types may be code tracing, debugging, output prediction,
  algorithm writing, logic correction, or concept explanation.
- Omit Types only for concepts with zero meaningful assessable question/task varieties.
- If a Type is present, every Case must include a full self-contained example
  question from the source. Do not shorten source questions; preserve all
  given values, conditions, data, quotations, and the exact ask needed for a
  teacher to execute the example.
- Include as many source examples as are available for each Type. Skip only
  purely introductory or rhetorical prompts with no expected student response.
- Each Type must be properly defined: name the action, object, and
  condition/method (e.g. "Finding the Unknown Exponent Using the Product Law"),
  never vague labels like "Direct Questions" or "Word Problems".
- Culmination rows may receive Types when a pattern mixes/combines several
  concepts of the topic (synthesis, mixed application, multi-step); keep their
  Description ("Description: Recap") unchanged.
- Use zero-padded labels exactly "Type 01:" and "Case 01:".
- Do not rewrite Misconception except to keep an existing useful one in place.
- Do not include source labels such as "Example 3" or "Exercise 1.2" in public concept_details.
""")

prompts.register(
    "concepts.question_task_inventory.system", category=_CONCEPTS_CAT,
    label="Universal Question / Task Inventory extraction prompt",
    default="""\
Extract a universal Question / Task Inventory from an uploaded school-subject chapter.
This is subject-agnostic and board-agnostic: Mathematics, Science, Social Science,
languages, literature, Computer Science, practical work, and any school subject.

Return ONLY strict JSON:
{"items":[{"qid":"QINV-0001","source_kind":"worked_example|solved_example|exercise|intext_question|checkpoint_question|activity|mcq|fill_blank|true_false|match|assertion_reason|diagram_task|map_task|table_task|graph_task|source_task|case_task|passage_task|grammar_task|writing_task|experiment_task|coding_task|long_answer|short_answer|other","source_label":"","parent_source_label":"","topic_hint":"","page_hint":"","block_ids":[],"raw_task":"","raw_solution_or_answer":"","normalized_task":"","shared_context":"","subpart_label":"","image_urls":[],"content_objects":{"numbers":[],"variables":[],"equations":[],"coordinates":[],"ratios":[],"diagrams":[],"graphs":[],"tables":[],"maps":[],"passages":[],"sources":[],"experiments":[],"observations":[],"characters":[],"events":[],"dates":[],"places":[],"terms":[],"definitions":[],"processes":[],"comparisons":[],"causes":[],"effects":[],"code_snippets":[],"grammar_items":[],"unknowns":[],"given_values":[],"conditions":[]},"requires_visual":false,"requires_context":false,"order_index":1}],"stats":{"worked_examples":0,"solved_examples":0,"exercise_questions":0,"checkpoint_questions":0,"activities":0,"objective_items":0,"subjective_items":0,"descriptive_items":0,"subparts":0,"visual_tasks":0,"table_or_graph_tasks":0,"source_or_passage_tasks":0,"total_inventory_items":0}}.

COVERAGE IS MANDATORY (most important rule):
- Extract EVERY assessable question/task from the first line to the last,
  including the chapter opening / pre-section narrative.
- Each numbered problem, intext question, think-and-reflect prompt, and worked
  example is its OWN item — never summarize an exercise set or question list
  into one item.
- Multi-part questions with subquestions (a/b/c, i/ii/iii, or "Answer the
  following") stay ONE inventory item: put the full stem + all subparts in
  raw_task, and leave subpart_label empty unless the textbook numbers each
  subpart as a standalone question with its own number.
- In-text CHECKPOINT questions (boxed "?" questions, "Let's recall",
  "Check your progress", mid-section question boxes) are inventory items
  exactly like end-of-chapter exercises. Chapters typically carry a dozen or
  more of them — walk every section and capture each one. Missing even one
  checkpoint is a defect.
- Picture-/source-/map-based questions (including opening-page source analysis
  such as describing Frédéric Sorrieu's prints) are inventory items with
  source_kind "source_task" / "diagram_task" / "map_task" as appropriate —
  never skip them as "introductory".
- Textbook ACTIVITY tasks (Activity 11.1 etc.) are inventory items with
  source_kind "activity" — they later feed culmination concepts.
- A missed question is a defect; an extra item is not.
- Skip only purely rhetorical prompts that do not expect a student answer or
  action (e.g. "Look at the picture" with no ask). If the text asks the student
  to describe, explain, list, or interpret, extract it.

Rules:
- Extract all assessable questions/tasks from first to last: examples, intext
  questions, checkpoints, exercises, objective items, diagrams, graphs, maps,
  data/tables, sources/passages/cases, experiments, observations, grammar,
  writing, literature extracts, vocabulary, coding, proof/reasoning, numerical,
  application, project or activity prompts if assessable.
- raw_task must carry the COMPLETE question wording verbatim — never truncate,
  paraphrase, or drop givens, data, sub-parts, quotations, or conditions.
- Inventory prompts only, never worked answers: stop each worked/solved example
  immediately before "Solution:" or "Answer:", and always return
  raw_solution_or_answer as an empty string. Types must expose questions, not
  answer keys or textbook solutions.
- For Mathematics, inventory every worked, numerical, contextual, and real-life problem
  as its own item, including problems embedded in explanatory prose.
  Capture its complete givens, context, and ask, but never include its solution.
- When the question depends on a figure/diagram/table image, copy the Mathpix
  image URL(s) from the source markdown (![](https://cdn.mathpix.com/...))
  into image_urls AND keep the figure reference in raw_task.
- Set topic_hint to the nearest MAIN section heading (or "[Chapter opening]"
  for pre-section items) so later placement stays in reading order.
- Never group inventory items from different topic_hint values into one mined
  Type. Source topic is a hard placement boundary even when two topics use a
  similar formula or task pattern.
- Use content_objects for all extracted subject matter and representations.
- A task may be non-numerical; do not reject it as generic because it is descriptive.
- Preserve source traceability in this debug JSON only; source labels must not be
  copied into public concept_details.
- Preserve shared context for passage/source/case/table/graph/map items.
""")

prompts.register(
    "concepts.type_mining.system", category=_CONCEPTS_CAT,
    label="Universal Type Mining prompt",
    default="""\
Classify the Question / Task Inventory into reusable academic Types appropriate
to the subject and chapter. A Type is a reusable assessment/task pattern found
in the source. A Case is a DEFINED sub-type of that pattern (what is given,
what is asked, with what constraint) — never a raw question. An Example is one
concrete source question that instantiates a Case, copied in full.

Return ONLY strict JSON:
{"types":[{"type_id":"TYPE-0001","type_title":"","type_description":"","task_pattern":"","source_question_ids":["QINV-0001"],"case_prompts":[{"case_id":"CASE-0001","case_title":"","examples":[{"source_question_id":"QINV-0001","example_prompt":""}],"case_signature":""}],"concept_match_hint":"","parent_concept_match_hint":"","topic_match_hint":"","difficulty_hint":"Basic|Intermediate|Advanced","cognitive_skill_hint":"","subject_skill_hint":"","is_activity":false}]}.

COVERAGE IS MANDATORY (most important rule):
- EVERY inventory item MUST appear in EXACTLY ONE Type's source_question_ids
  AND EXACTLY ONE example_prompt under a Case. The same qid/question must
  never appear in two Types, two Cases, or twice in the same Case.
- NEVER skip an item because it looks trivial, routine, descriptive, or hard to
  classify. If an item fits no existing Type, CREATE a new Type for it.
- In-text checkpoint questions, boxed "?" questions, and textbook activities
  count exactly like exercise questions — every one of them must be classified.
- Classification should be inclusive, not strict: when unsure between dropping
  an item and creating an extra Type, always create the extra Type.
- A missed question is a defect; an extra Type is not.

Rules:
- One inventory item maps to exactly one best-fit Type. If it combines several
  skills, choose the Type that most directly assesses the final ask, or create
  one integrated Type for that mixed skill — never duplicate the question.
- For Mathematics, classify every worked, numerical, contextual, and real-life
  problem into a distinct Type or Case whenever its assessed action, givens,
  representation, constraint, or ask differs. Preserve the complete problem as
  its Example, but never copy solutions or worked-answer steps.
- A Type may contain source questions from exactly ONE topic_hint. Never group
  questions across textbook topics even when their formulas or surface patterns
  resemble each other; create separate topic-scoped Types instead.
- Group items that share the same pattern under one Type, but do not force
  dissimilar items together just to keep the Type count low.
- Do not merge different academic, solving, answering, writing, interpretation,
  coding, experimental, or practical patterns.
- Preserve source_question_ids and source traceability in debug JSON.
- Do not include source labels in public concept_details.
- Set "is_activity": true when the Type groups textbook Activity tasks
  (hands-on classroom activities); these are embedded on culmination rows.

CASE WORDING (each Case must be properly defined):
- case_title DEFINES the sub-type: what is given to the student, what must be
  done, and the distinguishing condition — e.g. "Ohm's law formula-based
  question when V and I are given (without circuit)" vs "... (circuit diagram
  given)". A case_title is NEVER a raw question.
- Create a separate Case for every distinct given/asked/constraint combination.
- A multi-part source question with subquestions stays ONE Example under ONE
  Case unless the textbook numbers the subparts as separate standalone
  questions; do not split the same prompt across multiple Cases, and never
  invent multiple Cases that repeat the same stem with different subparts.

EXAMPLES CARRY THE FULL SOURCE QUESTION (mandatory):
- Every example_prompt must be fully self-contained: copy the ACTUAL numbers,
  expressions, equations, data, quotations, conditions, and task text from the
  source question (its raw_task / normalized_task) into the prompt.
- Do not shorten or truncate source questions. Keep the full teacher-executable
  wording, including all givens and the exact ask; omit only source labels and
  page refs.
- Include EVERY inventory question that fits a Case as its own example_prompt —
  more examples per Case is always better; never keep just one representative.
- When the source question relies on a figure/diagram/table image, KEEP the
  figure reference and append the Mathpix image URL from the source markdown
  immediately after it, e.g.
  "Calculate the resistance for the given circuit. (Refer fig. 11.1)
  ![](https://cdn.mathpix.com/cropped/...)".
- Correct: "Rationalise the denominator of 1/(7 + 3*sqrt(2))".
- WRONG: "Rationalise the expressions given in Exercise 1.5",
  "Solve the problem from Example 11".
- NEVER write Exercise/Example/page references in example_prompt, case_title,
  type_title, type_description, or task_pattern — always substitute the real
  content those labels point to. Figure references WITH their image URL are
  allowed and encouraged.

TYPE WORDING (each Type must be properly defined):
- type_title must be a precise, self-explanatory pattern name that states the
  action, the object, and the condition/method, e.g. "Finding the Unknown
  Exponent Using the Product Law" or "Identifying the Tense of an Underlined
  Verb in a Sentence" — never vague labels like "Exponent Problems",
  "Word Problems", "Direct Questions", or "Miscellaneous".
- type_description must DEFINE the pattern in 1-2 sentences: what is given to
  the student, what the student must do, and what form the answer takes.
- task_pattern must be a reusable template of the task, with the changing
  quantities/objects generalized (e.g. "Given a^m x a^n, simplify to a single
  power of a").
- For Mathematics, Types may be numerical/formula/problem-solving patterns.
- For Science, Types may be numerical, diagram, experiment, observation,
  reasoning, comparison, process, or application patterns.
- For Social Science, Types may be definition, cause-effect, comparison,
  source-based, map-based, chronology, case-based, data, or long-answer patterns.
- For Languages, Types may be grammar transformation, comprehension, extract
  analysis, vocabulary-in-context, writing format, literary interpretation, or
  theme/character analysis.
- For Computer Science, Types may be code tracing, debugging, output prediction,
  algorithm writing, logic correction, or concept explanation.
- Use subject_skill_hint values such as Mathematical Calculation, Algebraic
  Reasoning, Diagram Interpretation, Experimental Inference, Conceptual
  Explanation, Definition Recall, Comparative Analysis, Source Interpretation,
  Map Skill, Data Interpretation, Grammar Transformation, Literary
  Interpretation, Code Tracing, Algorithm Design, Case Application, or
  Long-Answer Structuring.
""")

prompts.register(
    "concepts.type_mining_delta.system", category=_CONCEPTS_CAT,
    label="Focused Type coverage delta prompt",
    default="""\
Add classifications only for the provided MISSED inventory items. Existing Type
metadata is context, not content to restate. Return ONLY an incremental delta;
never return an already classified question, an existing Example, or a complete
replacement Type list.

Return ONLY strict JSON:
{"types":[{"type_id":"TYPE-0001 or NEW-TYPE-0001","type_title":"","type_description":"","task_pattern":"","source_question_ids":["QINV-0001"],"case_prompts":[{"case_id":"existing CASE id or NEW-CASE-0001","case_title":"","examples":[{"source_question_id":"QINV-0001","example_prompt":""}],"case_signature":""}],"concept_match_hint":"","parent_concept_match_hint":"","topic_match_hint":"","difficulty_hint":"Basic|Intermediate|Advanced","cognitive_skill_hint":"","subject_skill_hint":"","is_activity":false}]}.

DELTA RULES:
- Use an existing type_id (and optionally an existing case_id) to append only
  new Cases/Examples to that Type. Its existing metadata is immutable.
- If no existing Type fits, create a new topic-scoped Type with a new temporary
  type_id and complete, precise Type and Case metadata.
- Claim only qids present in MISSED INVENTORY ITEMS. Each claimed qid must occur
  exactly once in source_question_ids and exactly once as an Example.
- Every returned Example must copy that missed item's complete source task
  verbatim, including all givens, subparts, conditions, context, figure
  references, and image URLs. Never include a solution or answer.
- A Type may cover only one exact topic_hint. Do not attach a missed item to an
  existing Type from another source topic; create a new topic-scoped Type.
- Cover every provided missed qid, but emit no unchanged Type, Case, or Example.
""")

prompts.register(
    "concepts.type_embedding.system", category=_CONCEPTS_CAT,
    label="Universal Type-to-concept assignment prompt",
    default="""\
Assign every mined Type to the concept it best belongs to. You are given a list
of concepts (each with a stable concept_id) and a list of mined Types (each with
a stable type_id). Decide the mapping using academic judgement about which
concept each Type assesses.

Return ONLY strict JSON:
{"assignments":[{"concept_id":"CONCEPT-0001","type_ids":["TYPE-0001","TYPE-0002"]}]}.

Rules:
- Every provided type_id MUST be assigned to exactly one concept_id.
- Never invent concept_id or type_id values; use only the ones provided.
- When a mined Type includes allowed_concept_ids, its source topic is proven:
  assign it to exactly one of those concept IDs and never any other concept.
- A concept may receive multiple type_ids; a Type belongs to one concept.
- Choose the concept that the Type most directly assesses (subject-appropriate).
- Assign each worked, numerical, contextual, or real-life problem Type to the
  concept the problem actually assesses, never to a nearby formula merely
  because it shares notation.
- Respect chapter position: a question assesses the concept taught at (or just
  before) the point of the chapter where it appears. NEVER assign a Type whose
  questions come from a LATER part of the chapter to an EARLIER concept — e.g.
  heating-effect questions never belong under a resistivity concept. Use the
  Type's topic_match_hint and the concepts' topic order to keep placements in
  reading order.
- Picture-/source-/map-based questions belong with the concept that teaches the
  visual's subject (the painting, map, diagram, or source discussed nearby),
  not with a later unrelated concept that happens to share a keyword. Opening-
  page source tasks (e.g. Sorrieu prints) go on the opening/first-topic concept.
- Concepts flagged "is_culmination": true are topic recap rows. Assign a Type
  there when the Type combines/mixes several concepts of that topic (synthesis,
  mixed application, multi-step, cross-concept comparison). Single-concept
  Types go to the specific concept, not the culmination.
- Types flagged "is_activity": true group textbook Activity tasks; assign them
  to the culmination row of the topic they belong to, not to a single concept.
- Do not drop any type_id. If unsure, pick the closest concept_id.
- Return no prose, only the JSON object.
""")

prompts.register(
    "concepts.type_alignment_review.system", category=_CONCEPTS_CAT,
    label="Type/concept alignment review prompt",
    default="""\
Review and repair the final concept map's Types/Cases/Examples against the
Question / Task Inventory. This is a quality-control pass: Types and Examples
must match the concept they are under, and every source question must appear
exactly once.

Return ONLY strict JSON:
{"rows":[{"topic":"","parent_concept":"","concept":"","concept_description":"","keywords":""}]}.

Rules:
- Return the SAME concept rows in the SAME order. You may only move/rewrite the
  Types section of each concept_description; keep Description, Achieving
  Mastery, Misconceptions, topic, parent_concept, concept, and keywords intact.
- Every inventory qid/question must appear exactly once as an Example under
  exactly one Case in exactly one concept. Missing qids are defects. Duplicate
  qids/questions are defects.
- A Type must belong to the concept it directly assesses. Use topic_hint,
  concept_match_hint, parent_concept_match_hint, topic order, and the actual
  question wording. Do not attach a later-section question to an earlier
  concept just because formulas overlap.
- If a question combines several concepts or is a textbook Activity, place it
  on the topic's culmination concept, not a narrow concept.
- Cases are defined sub-types; Examples are full source questions. Do not turn
  a raw question into a Case name.
- Keep all full question wording, subquestions, values, units, conditions, and
  Mathpix image URLs. Never truncate.
- If a source question already appears under the correct concept, preserve it.
- Never drop a question to fix duplication; move the duplicate to its correct
  single home.
""")

prompts.register(
    "concepts.culmination.system", category=_CONCEPTS_CAT,
    label="Topic culmination builder system prompt",
    default="""\
Build culmination rows after the normal concept map is finalized. The Types
assignment pass runs AFTER this one and may place mixed/synthesis Types mined
from the source onto these culmination rows.
Return ONLY strict JSON:
{"rows":[{"topic":"","parent_concept":"Culmination","concept":"","concept_description":"","keywords":""}]}.

Rules:
- Return ONLY the culmination rows — exactly one per topic, nothing else.
  The normal concept rows are merged back programmatically; NEVER restate,
  rewrite, drop, or return them.
- Name: "Culmination - <A>, <B> and <C>".
- Use the main ideas in that topic.
- Description must be exactly: "Description: Recap" (the final output expands
  it automatically to "Recap of <every merged concept in the topic>").
- Give each culmination a starter Types section with mixed multi-concept
  application/problem formats (the later Types pass may refine it).
- parent_concept must be "Culmination".
- Do not create culmination during chunk extraction; this pass runs only after the full topic map exists.
""")

prompts.register(
    "concepts.repair.system", category=_CONCEPTS_CAT,
    label="Concept validation repair system prompt",
    default="""\
Repair only concept rows that failed validation.
Return ONLY strict JSON:
{"rows":[{"topic":"","parent_concept":"","concept":"","concept_description":"","keywords":""}]}.

Rules:
- Fix only the listed issues.
- Preserve valid rows.
- Preserve valid fields, including parent_concept, Types, and useful Misconception.
- Do not rewrite the full chapter unnecessarily.
- Never add filler.
- Keep strict JSON.
- For source_artifact issues (references like "Example 5", "Exercise 1.2",
  "Fig 6.4", "fig.11.1", "page 14"): NEVER just delete or reword the reference.
  Look the label up in the provided source context and substitute the FULL
  actual content: the real numbers, expressions, equations, data, conditions,
  and task, e.g. "solve the problem in Exercise 1.5" becomes
  "rationalise the denominator of 1/(7 + 3*sqrt(2))".
  A figure/table reference WITH its Mathpix image URL embedded right after it
  is valid content — keep it (in Types Example lines). Never leave a
  Description truncated mid-sentence while fixing artifacts.
- Mathpix / CDN image URLs belong in Types Example lines next to the figure
  reference. Do not put image URLs in the Description section; describe the
  visual in words there instead.
- For merged_description issues (one cell carrying two or more concepts'
  "Description:" blocks): keep ONLY the content belonging to THIS row's
  concept — rewrite the cell so it describes exactly one concept. NEVER
  delete the other concept's material blindly; if it clearly belongs to a
  different provided row, move it there.
- For short_case_example issues: replace the truncated Example with the FULL
  source question wording (and Mathpix URL when the question is visual).
""")

prompts.register(
    "concepts.mastery_line.system", category=_CONCEPTS_CAT,
    label="Missing mastery-line writer system prompt",
    default="""\
Add the missing mastery statement to concept Descriptions.
Return ONLY strict JSON:
{"rows":[{"topic":"","parent_concept":"","concept":"","concept_description":"","keywords":""}]}.

Rules:
- Each provided row's Description is missing its final mastery statement.
- Return the SAME rows: identical topic, parent_concept, concept, keywords,
  and Description text — the ONLY change is appending a line break (\\n)
  followed by exactly:
  Achieving Mastery: <one short sentence stating what the learner can do when this concept is mastered>
- The sentence must be specific to the concept, e.g.
  "Achieving Mastery: Using the midpoint property to set up the smaller triangles correctly."
- Do not add Types or Misconception sections. No source artifacts
  (Example 3, Exercise 1.2, Fig 4, page numbers) and never the words
  "MMD"/"MMDs".
""")

prompts.register(
    "concepts.merge_duplicates.system", category=_CONCEPTS_CAT,
    label="Near-duplicate concept merge system prompt",
    default="""\
Merge concept rows that restate the SAME idea under different titles
(e.g. "Basic Proportionality Theorem" appearing again as "BPT" or
"The Basic Proportionality Theorem" under another topic).
Return ONLY strict JSON:
{"rows":[{"topic":"","parent_concept":"","concept":"","concept_description":"","keywords":""}]}.

Rules:
- You receive one GROUP of rows that all describe the same concept. Return
  EXACTLY ONE merged row for the group.
- Keep the clearest, most textbook-faithful title.
- Keep the topic where the textbook actually TEACHES the concept (usually the
  first row's topic in reading order).
- MERGE the content — never discard it: combine the Descriptions into one
  coherent Description (no repetition), keep the union of all Types/Cases/
  Examples, and the union of all specific Misconceptions.
- Keep the "Description: ... // Types: ... // Misconceptions: ..." structure
  and the single mastery line.
- Never invent new content; only reorganize what the rows carry.
""")

prompts.register(
    "concepts.misconceptions.system", category=_CONCEPTS_CAT,
    label="Missing/generic misconception writer system prompt",
    default="""\
Write the missing or too-generic Misconceptions for concept rows.
Return ONLY strict JSON:
{"rows":[{"topic":"","parent_concept":"","concept":"","concept_description":"","keywords":""}]}.

Rules:
- Each provided row is missing its Misconceptions section, or carries only a
  generic filler one. Return the SAME rows: identical topic, parent_concept,
  concept, keywords, Description, and Types — the ONLY change is a rewritten
  "Misconceptions:" section at the end.
- Every misconception must name a REAL, specific learner error this exact
  concept triggers, grounded in the chapter material (wrong condition, sign,
  unit, cause-effect reversal, term confusion, misapplied formula, ...).
- When the material triggers several distinct errors, list them all in the
  same Misconceptions section — one is the minimum, more are welcome.
- NEVER write templated filler like "Students may apply X as a memorized rule
  without checking the conditions", and never "N/A"/"None"/placeholders.
- No source artifacts (Example 3, Exercise 1.2, page numbers) and never the
  words "MMD"/"MMDs".
""")

prompts.register(
    "concepts.topic_structure.system", category=_CONCEPTS_CAT,
    label="Topic re-segregation system prompt",
    default="""\
Re-segregate a chapter concept map into its real textbook topics. Your ONLY
job is to assign each concept to the textbook MAIN SECTION that actually
teaches it, using the source file's own headings.
Return ONLY strict JSON:
{"rows":[{"topic":"","parent_concept":"","concept":"","concept_description":"","keywords":""}]}.

Rules:
- You are given the concept rows and the chapter's SECTION HEADINGS in reading
  order. Reassign ONLY the topic of each row.
- Topic names must be the given source headings VERBATIM (only the section
  number stripped) — never invent, rename, merge, or paraphrase headings.
- The given headings are the MAIN sections. When a concept comes from a
  subsection (e.g. "2.1 The Aristocracy and the New Middle Class"), file it
  under its MAIN section heading ("The Making of Nationalism in Europe") —
  subsections are never topics.
- Keep EVERY row: same concept names, descriptions, keywords, and
  parent_concept, in the same relative order. Never add, drop, merge, split,
  or rename concepts.
- Use several topics — a chapter is never one topic. Cover the chapter's full
  span: rows from tail sections belong to those tail headings, not to an
  earlier catch-all.
- Assign each concept to the section whose content teaches it; consecutive
  concepts usually stay in the same section until the source moves on.
- Do not create exercise, example, review, or practice topics.
- Do not use an unnumbered chapter title or book title as a topic. Exception:
  when a numbered MAIN section intentionally has the same title as the chapter,
  that numbered section is a valid topic and must remain available for rows
  taught there.
""")

prompts.register(
    "concepts.english.system", category=_CONCEPTS_CAT,
    label="English literature concept-mapping supplement",
    default="""\
ENGLISH LITERATURE RULES (mandatory when Subject is English):
- Topic names MUST be the prose piece, poem, or drama title — never pedagogy
  labels like "Pre-reading", "Informal Letter Writing", or "Oral Checks".
- Map ONLY literary concepts from the text (characters, themes, episodes,
  poetic devices, narrative moves) following the universal English concept map.
- When a unit contains both prose and poem, list ALL prose episode topics first,
  then poem episode topics; never drop the poem because the chapter title names
  only the prose piece.
- Do not create writing-skills, letter-format, or classroom-activity concepts.
""")

prompts.register(
    "concepts.chapter_meta.system", category=_CONCEPTS_CAT,
    label="Chapter/topic metadata writer system prompt",
    default="""\
Write chapter-level and topic-level metadata for a finished school concept map.
Return ONLY strict JSON:
{"chapter_description":"","chapter_duration_minutes":0,"topics":[{"topic":"","topic_description":""}]}.

Rules:
- chapter_description: 3-5 sentences a teacher can plan from — what the chapter
  covers, the storyline across its topics, the key skills built, and what
  learners can do at the end. It must be specific to THIS chapter's content;
  never generic filler like "This chapter develops N concepts across M topics".
- chapter_duration_minutes: a realistic INTEGER estimate of total classroom
  minutes needed to teach the full chapter (typical school periods are
  35-45 minutes; a standard chapter runs roughly 4-14 periods). When a
  FINALIZED chapter duration is provided in the metadata block, return that
  exact integer — do not override it.
- topics: one entry per provided topic, using the EXACT same topic strings.
- topic_description: 2-3 sentences specific to that topic — what it teaches,
  the key ideas/skills among its concepts, and how it connects to the
  neighbouring topics. NEVER just list the concept names.
- No source artifacts (Example 3, Exercise 1.2, Fig 4, page numbers) and never
  the words "MMD"/"MMDs".
""")


def _concepts_system(subject: str) -> str:
    return prompts.get_text("concepts.skeleton.system")


def _metadata(
    *, subject: str = "", board: str = "", grade: str = "", unit: str = "",
    chapter_title: str = "", chapter_id: int | str | None = None,
    chapter_code: str = "", learning_kind: str = "Post",
    finalized_duration_minutes: int = 0,
) -> dict:
    return {
        "subject": subject or "",
        "board": board or "",
        "grade": grade or "",
        "unit": unit or "",
        "chapter_title": chapter_title or "",
        "chapter_id": "" if chapter_id is None else str(chapter_id),
        "chapter_code": chapter_code or "",
        "learning_kind": learning_kind or "Post",
        "finalized_duration_minutes": int(finalized_duration_minutes or 0),
    }


def _metadata_block(meta: dict) -> str:
    block = (
        f"Subject: {meta.get('subject', '')}\n"
        f"Board: {meta.get('board', '')}\n"
        f"Grade: {meta.get('grade', '')}\n"
        f"Unit: {meta.get('unit', '')}\n"
        f"Chapter: {meta.get('chapter_title', '')}\n"
        f"Chapter ID/Code: {meta.get('chapter_id', '')} / {meta.get('chapter_code', '')}\n"
        f"Learning kind: {meta.get('learning_kind', 'Post')}"
    )
    finalized = int(meta.get("finalized_duration_minutes") or 0)
    if finalized > 0:
        block += f"\nFinalized chapter duration (minutes): {finalized}"
    subj = (meta.get("subject") or "").lower()
    if "english" in subj:
        block += "\n\n" + prompts.get_text("concepts.english.system")
    if _needs_power_sharing_topic_guidance(meta):
        block += (
            "\n\nReview-required Civics coverage: include the textbook section "
            "'Forms of Power-sharing' as its own topic. Do not merge horizontal "
            "distribution, vertical division, social-group/community sharing, "
            "political parties, pressure groups, or movements into unrelated "
            "outcome/democracy topics."
        )
    return block


def _needs_power_sharing_topic_guidance(meta: dict) -> bool:
    subject = (meta.get("subject") or "").strip().lower()
    chapter = bi.normalize_question_text(meta.get("chapter_title") or "")
    return "power sharing" in chapter and (
        "civic" in subject
        or "social science" in subject
        or subject in {"politics", "political science"}
    )


# Process-wide gate on in-flight OpenAI calls. All users of this instance
# share one API key, so concurrent generation runs must interleave their
# calls instead of stampeding the API into rate limits. Created lazily so
# tests can adjust config.OPENAI_MAX_CONCURRENCY and reset the gate.
_openai_gate: "threading.BoundedSemaphore | None" = None
_openai_gate_lock = threading.Lock()


def _get_openai_gate() -> "threading.BoundedSemaphore":
    global _openai_gate
    with _openai_gate_lock:
        if _openai_gate is None:
            _openai_gate = threading.BoundedSemaphore(config.OPENAI_MAX_CONCURRENCY)
        return _openai_gate


def _retry_after_seconds(exc: Exception) -> float | None:
    """Server-suggested wait from a rate-limit response, when present."""
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if not headers:
        return None
    raw = headers.get("retry-after") or headers.get("Retry-After")
    try:
        return max(0.0, float(raw)) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _openai_error_code(exc: Exception) -> str:
    """Return the provider error code without exposing response contents."""
    direct = getattr(exc, "code", None)
    if direct:
        return str(direct).strip().lower()
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        nested = body.get("error")
        if isinstance(nested, dict) and nested.get("code"):
            return str(nested["code"]).strip().lower()
        if body.get("code"):
            return str(body["code"]).strip().lower()
    return ""


def _transient_backoff(exc: Exception, attempt: int) -> float:
    suggested = _retry_after_seconds(exc)
    backoff = min(2.0 * (2 ** (attempt - 1)), config.OPENAI_BACKOFF_MAX_SECONDS)
    backoff *= 0.8 + 0.4 * random.random()  # jitter to de-synchronize users
    return max(suggested or 0.0, backoff)


def _openai_json(system: str, user: str, max_tokens: int | None = None,
                 retries: int = 3) -> dict:
    """One JSON-mode chat call; returns the parsed object.

    Concurrency-safe for multiple simultaneous users on one shared API key:
    calls queue on a process-wide gate (never stampede the API), and
    transient failures — rate limits, timeouts, connection errors, 5xx —
    are retried patiently with exponential backoff + Retry-After, so heavy
    load makes jobs slower but never changes their output quality.
    """
    import json
    import time
    from openai import (
        APIConnectionError,
        APITimeoutError,
        InternalServerError,
        OpenAI,
        RateLimitError,
    )

    transient_errors = (
        RateLimitError, APIConnectionError, APITimeoutError, InternalServerError)
    limit = config.OPENAI_MAX_OUTPUT_TOKENS if max_tokens is None else max_tokens
    client = OpenAI()
    gate = _get_openai_gate()
    last_err: Exception | None = None
    attempt = 0  # hard failures (bad JSON, truncation, 4xx)
    transient = 0  # rate limits / timeouts / 5xx — retried patiently
    while True:
        try:
            with gate:
                resp = client.chat.completions.create(
                    model=config.OPENAI_MODEL,
                    messages=[{"role": "system", "content": system},
                              {"role": "user", "content": user}],
                    response_format={"type": "json_object"},
                    max_completion_tokens=limit,
                )
            choice = resp.choices[0]
            if getattr(choice, "finish_reason", None) == "length":
                raise RuntimeError(
                    f"OpenAI response truncated at max_completion_tokens={limit}. "
                    "Set AEGIS_OPENAI_MAX_OUTPUT_TOKENS higher or reduce input size."
                )
            return json.loads(choice.message.content or "{}")
        except transient_errors as e:
            error_code = _openai_error_code(e)
            if error_code == "insufficient_quota":
                progress.log(
                    "OpenAI quota is exhausted (insufficient_quota); not "
                    "retrying a definitive billing/quota denial.",
                    level="error",
                )
                raise RuntimeError(
                    "OpenAI quota exhausted (insufficient_quota); the request "
                    "was not retried because quota errors are non-transient."
                ) from e
            transient += 1
            last_err = e
            if transient > config.OPENAI_TRANSIENT_RETRIES:
                raise RuntimeError(
                    f"OpenAI unavailable after {transient - 1} transient retries "
                    f"(rate limit/timeout): {e!r}"
                ) from e
            delay = _transient_backoff(e, transient)
            progress.log(
                f"OpenAI busy ({type(e).__name__}) — waiting {delay:.0f}s before "
                f"retry {transient}/{config.OPENAI_TRANSIENT_RETRIES}.",
                level="warning",
            )
            time.sleep(delay)
        except Exception as e:  # noqa: BLE001 — retry then surface
            last_err = e
            attempt += 1
            if attempt >= retries:
                break
            time.sleep(2)
    raise RuntimeError(f"OpenAI extraction failed after {retries} retries: {last_err!r}")


def _trim(text: str, max_chars: int = 220_000) -> str:
    if len(text) <= max_chars:
        return text
    return text[: int(max_chars * 0.7)] + "\n\n[...TRIMMED...]\n\n" + text[-int(max_chars * 0.3):]


# How many characters of MMD to send per GPT call. We chunk (never trim) so no
# chapter content is lost: each chunk is processed in full and the results are
# merged. Kept deliberately small: when a whole chapter fits into one giant
# chunk, models under-extract (a handful of broad concepts instead of every
# teachable unit). Smaller chunks force section-level attention and denser,
# more complete extraction; quality is preferred over call count.
_MMD_CHUNK_CHARS = int(os.environ.get("AEGIS_MMD_CHUNK_CHARS", "24000"))


def _split_mmd_into_chunks(mmd_text: str, max_chars: int | None = None) -> list[str]:
    """Split an MMD document into ordered chunks without dropping any content.

    Splits on Markdown headings so each chunk is a run of whole sections; a
    single section larger than ``max_chars`` is hard-split on paragraph
    boundaries. The concatenation of all chunks equals the original text
    (whitespace aside) — nothing is trimmed.
    """
    if max_chars is None:
        max_chars = _MMD_CHUNK_CHARS
    text = normalize_mmd_headings(mmd_text or "")
    if len(text) <= max_chars:
        return [text] if text.strip() else []

    # Break into sections that each start at a heading line.
    lines = text.splitlines(keepends=True)
    sections: list[str] = []
    current: list[str] = []
    for line in lines:
        if line.lstrip().startswith("#") and current:
            sections.append("".join(current))
            current = [line]
        else:
            current.append(line)
    if current:
        sections.append("".join(current))

    # Hard-split any oversized section on blank lines (paragraphs).
    def _hard_split(block: str) -> list[str]:
        if len(block) <= max_chars:
            return [block]
        paras = re.split(r"(\n\s*\n)", block)
        out: list[str] = []
        buf = ""
        for piece in paras:
            if len(buf) + len(piece) > max_chars and buf:
                out.append(buf)
                buf = piece
            elif len(piece) > max_chars:
                # A single paragraph longer than the budget: slice it.
                if buf:
                    out.append(buf)
                    buf = ""
                for i in range(0, len(piece), max_chars):
                    out.append(piece[i:i + max_chars])
            else:
                buf += piece
        if buf:
            out.append(buf)
        return out

    # Pack sections into chunks up to max_chars.
    chunks: list[str] = []
    buf = ""
    for section in sections:
        for piece in _hard_split(section):
            if len(buf) + len(piece) > max_chars and buf:
                chunks.append(buf)
                buf = piece
            else:
                buf += piece
    if buf.strip():
        chunks.append(buf)
    return [c for c in chunks if c.strip()]


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_EXERCISE_RE = re.compile(
    r"\b(exercise|ex\.|review|practice|problems?|questions?)\b", re.IGNORECASE)
_SECTION_NUM_PREFIX_RE = re.compile(
    r"^\s*(?:chapter\s+)?(?:\d+(?:\.\d+)*[\).\s:-]+|[A-Z][\).:-]+\s*)",
    re.IGNORECASE,
)

# Mathpix PDF->MMD output marks headings with LaTeX commands, not Markdown '#'.
_LATEX_HEADING_RE = re.compile(
    r"^[ \t]*\\(title|chapter|section|subsection|subsubsection|paragraph)\*?"
    r"\{(.+?)\}[ \t]*$",
    re.MULTILINE,
)
_LATEX_HEADING_LEVELS = {
    "title": 1, "chapter": 1, "section": 2, "subsection": 3,
    "subsubsection": 4, "paragraph": 5,
}
# Mathpix OCR sometimes emits fullwidth punctuation/digits (e.g. "1．1"),
# which breaks section-number stripping and heading comparison.
_FULLWIDTH_TRANS = str.maketrans(
    "０１２３４５６７８９．：；，（）　", "0123456789.:;,() ")


def normalize_mmd_headings(mmd_text: str) -> str:
    """Convert LaTeX-style headings in Mathpix MMD to Markdown headings.

    Real Mathpix PDF conversions mark headings as ``\\section*{1.1 Intro}`` /
    ``\\subsection*{...}`` rather than Markdown ``#``. Without this pass a
    whole OCR'd chapter parses as ONE headingless section, which collapses
    section-aware chunking to a single giant chunk and starves extraction of
    heading/topic context. Idempotent: already-Markdown text is unchanged.
    """

    def _sub(m: "re.Match[str]") -> str:
        title = re.sub(r"\\[a-zA-Z]+\*?", " ", m.group(2))
        title = title.replace("{", " ").replace("}", " ")
        title = re.sub(r"\s+", " ", title).strip()
        return "#" * _LATEX_HEADING_LEVELS[m.group(1)] + " " + title

    return _LATEX_HEADING_RE.sub(_sub, mmd_text or "")


def _clean_heading_text(title: str) -> str:
    title = re.sub(r"\s+", " ", (title or "").strip())
    title = title.translate(_FULLWIDTH_TRANS)
    for _ in range(3):
        title = re.sub(
            r"\\(?:mathbf|boldsymbol|mathrm|text)\s*\{([^{}]*)\}", r"\1", title)
    title = (
        title.replace("\\(", " ").replace("\\)", " ")
        .replace("\\[", " ").replace("\\]", " ")
        .replace("$", " ")
    )
    title = re.sub(r"\\[a-zA-Z]+\*?", " ", title)
    title = title.replace("{", " ").replace("}", " ")
    title = re.sub(r"\s+", " ", title).strip()
    return title


def _strip_section_number(title: str) -> str:
    title = _clean_heading_text(title)
    return _SECTION_NUM_PREFIX_RE.sub("", title).strip() or title


def _topic_comparison_key(topic: str) -> str:
    """Canonical key for source-topic constraints.

    Mathpix may render the same heading as ``$ n $``, ``\\boldsymbol{n}``, or
    plain ``n``. Strip those presentational wrappers plus punctuation before
    comparing topics while retaining the original heading for display.
    """
    text = _strip_section_number(topic)
    text = text.replace("$", " ")
    text = re.sub(r"[\W_]+", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip().casefold()


def _heading_number_prefix(title: str) -> str:
    title = _clean_heading_text(title)
    m = re.match(r"^\s*(?:chapter\s+)?(\d+(?:\.\d+)*)[\).\s:-]+", title,
                 re.IGNORECASE)
    return m.group(1) if m else ""


def parse_mmd_sections(mmd_text: str) -> list[dict]:
    """Parse MMD into ordered heading-aware sections with exercise tagging."""
    text = normalize_mmd_headings(mmd_text or "")
    lines = text.splitlines()
    sections: list[dict] = []
    stack: list[tuple[int, str]] = []
    current: dict | None = None

    def finish() -> None:
        if current and (current["body"].strip() or current["heading_path"]):
            body = current["body"]
            exercise_blocks = []
            paras = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
            for para in paras:
                if _EXERCISE_RE.search(para):
                    exercise_blocks.append(para)
            current["exercise_blocks"] = exercise_blocks
            sections.append(current)

    for line in lines:
        m = _HEADING_RE.match(line)
        if m:
            finish()
            level = len(m.group(1))
            raw_heading = _clean_heading_text(m.group(2))
            heading = _strip_section_number(raw_heading)
            stack = [(lv, h) for lv, h in stack if lv < level]
            stack.append((level, heading))
            current = {
                "heading": heading,
                "heading_raw": raw_heading,
                "heading_level": level,
                "heading_path": [h for _, h in stack],
                "heading_numbered": bool(_SECTION_NUM_PREFIX_RE.match(raw_heading)),
                "heading_number_prefix": _heading_number_prefix(raw_heading),
                "heading_chapter": bool(
                    re.match(r"^\s*chapter\b", raw_heading, re.IGNORECASE)),
                "body": line + "\n",
            }
            continue
        if current is None:
            current = {
                "heading": "",
                "heading_raw": "",
                "heading_level": 1,
                "heading_path": [],
                "heading_numbered": False,
                "heading_number_prefix": "",
                "heading_chapter": False,
                "body": "",
            }
        current["body"] += line + "\n"
    finish()
    if not sections and text.strip():
        sections = [{
            "heading": "General",
            "heading_raw": "General",
            "heading_level": 1,
            "heading_path": ["General"],
            "heading_numbered": False,
            "heading_number_prefix": "",
            "heading_chapter": False,
            "body": text,
            "exercise_blocks": [
                p.strip() for p in re.split(r"\n\s*\n", text) if _EXERCISE_RE.search(p)
            ],
        }]
    for i, section in enumerate(sections):
        section["previous_heading"] = sections[i - 1]["heading"] if i else ""
        section["next_heading"] = sections[i + 1]["heading"] if i + 1 < len(sections) else ""
    return sections


def _format_section_chunk(sections: list[dict]) -> str:
    blocks = []
    for section in sections:
        exercises = "\n".join(section.get("exercise_blocks") or [])
        path = section.get("heading_path") or []
        # Pre-heading chapter body (NCERT openings like Frédéric Sorrieu) has
        # an empty path — label it so skeleton/inventory treat it as content.
        heading_path = " > ".join(path) if path else "[Chapter opening]"
        block = (
            f"HEADING PATH: {heading_path}\n"
            f"PREVIOUS HEADING: {section.get('previous_heading', '')}\n"
            f"NEXT HEADING: {section.get('next_heading', '')}\n"
            "SECTION TEXT:\n" + section.get("body", "")
        )
        if exercises:
            block += "\nEXERCISE BLOCKS FOR TYPES PASS:\n" + exercises
        blocks.append(block.strip())
    return "\n\n--- SECTION ---\n\n".join(blocks)


def _split_oversized_section(section: dict, max_chars: int) -> list[dict]:
    """Hard-split a section bigger than the chunk budget on paragraph bounds.

    Documents whose headings Mathpix/OCR failed to mark parse as one giant
    section; without this split the whole chapter would travel as a single
    chunk, which reliably under-extracts. Each part keeps the heading context.
    """
    body = section.get("body", "")
    if len(body) <= max_chars:
        return [section]
    paras = re.split(r"(\n\s*\n)", body)
    parts: list[str] = []
    buf = ""
    for piece in paras:
        if len(buf) + len(piece) > max_chars and buf:
            parts.append(buf)
            buf = piece
        elif len(piece) > max_chars:
            if buf:
                parts.append(buf)
                buf = ""
            for i in range(0, len(piece), max_chars):
                parts.append(piece[i:i + max_chars])
        else:
            buf += piece
    if buf.strip():
        parts.append(buf)
    out = []
    for i, part in enumerate(parts, start=1):
        sub = dict(section)
        sub["body"] = part
        sub["exercise_blocks"] = [
            p.strip() for p in re.split(r"\n\s*\n", part) if _EXERCISE_RE.search(p)]
        if section.get("heading"):
            sub["heading"] = f"{section['heading']} (part {i}/{len(parts)})"
        out.append(sub)
    return out


def _pack_section_chunks(
    sections: list[dict], max_chars: int | None = None,
) -> list[dict]:
    """Pack already-parsed sections without discarding their heading paths."""
    if max_chars is None:
        max_chars = _MMD_CHUNK_CHARS
    split_sections = [
        sub for s in sections
        for sub in _split_oversized_section(s, max_chars)
    ]
    chunks: list[dict] = []
    buf: list[dict] = []
    for section in split_sections:
        candidate = buf + [section]
        if buf and len(_format_section_chunk(candidate)) > max_chars:
            chunks.append({"sections": buf, "text": _format_section_chunk(buf)})
            buf = [section]
        else:
            buf = candidate
    if buf:
        chunks.append({"sections": buf, "text": _format_section_chunk(buf)})
    return chunks


def _section_aware_chunks(mmd_text: str, max_chars: int | None = None) -> list[dict]:
    """Pack parsed sections into chunks while preserving heading context."""
    return _pack_section_chunks(parse_mmd_sections(mmd_text), max_chars)


def _sections_with_source_topics(sections: list[dict]) -> list[tuple[str, dict]]:
    """Pair each section with its nearest real main-section topic.

    Structural OCR headings such as ``Solution`` and ``EXERCISE 5.2`` inherit
    the preceding main topic. This association is the source of truth for
    question inventory and mined-Type placement.
    """
    headings = _topic_headings(sections)
    canonical: dict[str, str] = {}
    for heading in headings:
        key = _topic_comparison_key(heading)
        if key:
            canonical.setdefault(key, _strip_section_number(heading))
    first_topic = next(iter(canonical.values()), "General")
    current = first_topic
    paired: list[tuple[str, dict]] = []
    for section in sections:
        key = _topic_comparison_key(section.get("heading") or "")
        if key in canonical:
            current = canonical[key]
        paired.append((current, section))
    return paired


def _inventory_chunks_by_topic(
    sections: list[dict], max_chars: int | None = None,
) -> list[dict]:
    """Build inventory chunks that never cross a source-topic boundary."""
    groups: list[tuple[str, list[dict]]] = []
    for topic, section in _sections_with_source_topics(sections):
        if groups and groups[-1][0] == topic:
            groups[-1][1].append(section)
        else:
            groups.append((topic, [section]))
    chunks: list[dict] = []
    for topic, topic_sections in groups:
        for chunk in _pack_section_chunks(topic_sections, max_chars):
            chunk["source_topic"] = topic
            chunks.append(chunk)
    return chunks


def _source_for_topic(topic: str, sections: list[dict]) -> str:
    """Return source/exercise context most relevant to a topic."""
    topic_n = _topic_comparison_key(topic)
    selected = [
        s for s in sections
        if topic_n and (
            topic_n == _topic_comparison_key(s.get("heading") or "")
            or topic_n in _topic_comparison_key(
                " > ".join(s.get("heading_path") or []))
        )
    ]
    if not selected:
        selected = sections
    return _format_section_chunk(selected)


def _record_key(rec: dict) -> tuple[str, str]:
    return (
        _topic_comparison_key(rec.get("topic") or ""),
        bi.normalize_question_text(rec.get("concept_title", "")),
    )


def _types_body(details: str) -> str:
    """Return the content of the Types section, or '' if absent."""
    for label, content in cr.split_sections(details):
        if label.strip().lower().startswith("type"):
            return content.strip()
    return ""


def _has_meaningful_types(details: str) -> bool:
    body = _types_body(details)
    return len(body) > 12 and re.search(r"\bCase\b", body, re.IGNORECASE) is not None


def _inject_types(details: str, types_body: str) -> str:
    """Insert or replace the Types section in a concept_description string."""
    if not types_body.strip():
        return details
    sections = cr.split_sections(details)
    out: list[tuple[str, str]] = []
    replaced = False
    for label, content in sections:
        if label.strip().lower().startswith("type"):
            out.append(("Types", types_body.strip()))
            replaced = True
        else:
            out.append((label, content))
    if not replaced:
        inserted = False
        out = []
        for label, content in sections:
            if not inserted and label.strip().lower().startswith("misconception"):
                out.append(("Types", types_body.strip()))
                inserted = True
            out.append((label, content))
        if not inserted:
            out.append(("Types", types_body.strip()))
    return cr.join_sections(out)


def _types_assign_system(subject: str) -> str:
    return prompts.get_text("concepts.types_assign.system")


def _description_refine_system(subject: str) -> str:
    return prompts.get_text("concepts.description_refine.system")


def _empty_inventory() -> dict:
    return {
        "items": [],
        "stats": {
            "worked_examples": 0,
            "solved_examples": 0,
            "exercise_questions": 0,
            "checkpoint_questions": 0,
            "activities": 0,
            "objective_items": 0,
            "subjective_items": 0,
            "descriptive_items": 0,
            "subparts": 0,
            "visual_tasks": 0,
            "table_or_graph_tasks": 0,
            "source_or_passage_tasks": 0,
            "total_inventory_items": 0,
        },
    }


_WORKED_EXAMPLE_START_RE = re.compile(
    r"(?im)^[ \t]*(?:worked[ \t]+)?example[ \t]+"
    r"([A-Za-z0-9]+)[ \t]*[:：.)-][ \t]*",
)
_NUMBERED_TASK_START_RE = re.compile(r"(?m)^[ \t]*(\d{1,3})[.)][ \t]+")
_SOLUTION_START_RE = re.compile(
    r"(?im)^[ \t]*(?:solutions?|answers?)[ \t]*[:：][ \t]*",
)
_INVENTORY_TASK_MARKER_RE = re.compile(
    r"(?im)(?:"
    r"^\s*(?:worked\s+)?example\s+[A-Za-z0-9]+\s*[:：.)-]|"
    r"^\s*\d{1,3}[.)]\s+|"
    r"^\s*(?:questions?|checkpoint|activity|do\s+this|try\s+these|"
    r"let['’]s\s+recall)\b|"
    r"^\s*(?:find|calculate|determine|solve|show|prove|choose|write|state|"
    r"explain|identify|check|fill|match|draw|compare|discuss|analy[sz]e)\b|"
    r"\?"
    r")",
)


def _inventory_task_without_solution(text: str, *, aggressive: bool = False) -> str:
    """Return only the assessable prompt, never a worked answer/solution."""
    text = (text or "").strip()
    match = _SOLUTION_START_RE.search(text)
    if match is None and aggressive:
        match = re.search(r"(?i)\b(?:solutions?|answers?)\s*[:：]", text)
    if match is not None:
        text = text[:match.start()]
    return re.sub(r"\s+", " ", text).strip(" \n\t")


def _inventory_chunk_has_task_markers(chunk: dict) -> bool:
    """Whether source text explicitly signals an assessable inventory item."""
    for section in chunk.get("sections") or []:
        body = str(section.get("body") or "").strip()
        if not body:
            continue
        heading = str(section.get("heading") or "")
        if _EXERCISE_RE.search(heading):
            return True
        if _INVENTORY_TASK_MARKER_RE.search(body):
            return True
    return False


def _source_task_anchors(sections: list[dict]) -> list[dict]:
    """Deterministically inventory source-labelled examples and exercises.

    GPT remains the primary extractor. These anchors are a completeness audit
    for source structures that are mechanically unambiguous, and are merged
    into its output when a worked example or numbered exercise was missed.
    """
    ordered: list[tuple[int, int, dict]] = []
    for section_index, (topic, section) in enumerate(
            _sections_with_source_topics(sections)):
        body = section.get("body") or ""
        example_matches = list(_WORKED_EXAMPLE_START_RE.finditer(body))
        for i, match in enumerate(example_matches):
            end = (
                example_matches[i + 1].start()
                if i + 1 < len(example_matches)
                else len(body)
            )
            task = _inventory_task_without_solution(
                body[match.end():end], aggressive=True)
            if not task:
                continue
            label = f"Example {match.group(1)}"
            ordered.append((section_index, match.start(), {
                "source_kind": "worked_example",
                "source_label": label,
                "parent_source_label": "",
                "topic_hint": topic,
                "page_hint": "",
                "block_ids": [],
                "raw_task": task,
                "raw_solution_or_answer": "",
                "normalized_task": task,
                "shared_context": "",
                "subpart_label": "",
                "image_urls": re.findall(
                    r"https?://cdn\.mathpix\.com/[^)\s]+", task),
                "content_objects": {},
                "requires_visual": bool(re.search(
                    r"\bfig(?:ure)?\.?\s*\d|!\[[^\]]*\]\(", task,
                    re.IGNORECASE)),
                "requires_context": False,
            }))

        heading = section.get("heading") or ""
        if not _EXERCISE_RE.search(heading):
            continue
        task_matches = list(_NUMBERED_TASK_START_RE.finditer(body))
        for i, match in enumerate(task_matches):
            end = (
                task_matches[i + 1].start()
                if i + 1 < len(task_matches)
                else len(body)
            )
            task = _inventory_task_without_solution(body[match.end():end])
            if not task:
                continue
            label = f"{heading} Q{match.group(1)}".strip()
            ordered.append((section_index, match.start(), {
                "source_kind": "exercise",
                "source_label": label,
                "parent_source_label": heading,
                "topic_hint": topic,
                "page_hint": "",
                "block_ids": [],
                "raw_task": task,
                "raw_solution_or_answer": "",
                "normalized_task": task,
                "shared_context": "",
                "subpart_label": "",
                "image_urls": re.findall(
                    r"https?://cdn\.mathpix\.com/[^)\s]+", task),
                "content_objects": {},
                "requires_visual": bool(re.search(
                    r"\bfig(?:ure)?\.?\s*\d|!\[[^\]]*\]\(", task,
                    re.IGNORECASE)),
                "requires_context": False,
            }))
    return [
        item for _, _, item in sorted(ordered, key=lambda row: (row[0], row[1]))
    ]


def _sanitize_inventory_item(item: dict, *, source_topic: str = "") -> dict:
    """Normalize one GPT inventory row and remove answer material."""
    cleaned = dict(item)
    kind = (cleaned.get("source_kind") or "other").strip().lower()
    aggressive = kind in {"worked_example", "solved_example"}
    raw_task = _inventory_task_without_solution(
        str(cleaned.get("raw_task") or cleaned.get("normalized_task") or ""),
        aggressive=aggressive,
    )
    normalized = _inventory_task_without_solution(
        str(cleaned.get("normalized_task") or raw_task),
        aggressive=aggressive,
    )
    cleaned["source_kind"] = kind
    cleaned["raw_task"] = raw_task
    cleaned["normalized_task"] = normalized or raw_task
    cleaned["raw_solution_or_answer"] = ""
    cleaned["topic_hint"] = (
        source_topic
        or (cleaned.get("topic_hint") or "").strip()
        or "General"
    )
    cleaned.setdefault("content_objects", {})
    cleaned.setdefault("image_urls", [])
    cleaned.setdefault("requires_visual", False)
    cleaned.setdefault("requires_context", False)
    return cleaned


_LEADING_INVENTORY_TASK_NUMBER_RE = re.compile(
    r"^\s*(?:q(?:uestion)?\s*)?\d+\s*[.)]\s*",
    re.IGNORECASE,
)


def _inventory_task_match_key(item: dict) -> str:
    """Task text normalized for GPT-row/deterministic-anchor matching."""
    task = bi.normalize_question_text(
        item.get("raw_task") or item.get("normalized_task") or "")
    return _LEADING_INVENTORY_TASK_NUMBER_RE.sub("", task, count=1)


def _inventory_items_match(item: dict, anchor: dict) -> bool:
    label = bi.normalize_question_text(item.get("source_label", ""))
    anchor_label = bi.normalize_question_text(anchor.get("source_label", ""))
    if label and anchor_label and label == anchor_label:
        return True
    task = _inventory_task_match_key(item)
    anchor_task = _inventory_task_match_key(anchor)
    if not task or not anchor_task:
        return False
    if task == anchor_task:
        return True
    shorter, longer = sorted((task, anchor_task), key=len)
    return len(shorter) >= 40 and shorter in longer


def _merge_source_task_anchors(items: list[dict], anchors: list[dict]) -> list[dict]:
    """Backfill missing deterministic anchors and canonicalize matching rows."""
    merged = [dict(item) for item in items]
    for anchor in anchors:
        match_index = next(
            (i for i, item in enumerate(merged)
             if _inventory_items_match(item, anchor)),
            None,
        )
        if match_index is None:
            merged.append(dict(anchor))
            continue
        existing = merged[match_index]
        for field in (
            "source_kind", "source_label", "parent_source_label", "topic_hint",
            "raw_task", "normalized_task", "raw_solution_or_answer",
        ):
            existing[field] = anchor.get(field, "")
        if anchor.get("image_urls"):
            existing["image_urls"] = anchor["image_urls"]
        existing["requires_visual"] = bool(
            existing.get("requires_visual") or anchor.get("requires_visual"))

    deduped: list[dict] = []
    seen_labels: set[str] = set()
    seen_tasks: set[str] = set()
    for item in merged:
        label = bi.normalize_question_text(item.get("source_label", ""))
        task = bi.normalize_question_text(
            item.get("raw_task") or item.get("normalized_task") or "")
        if (label and label in seen_labels) or (task and task in seen_tasks):
            continue
        if label:
            seen_labels.add(label)
        if task:
            seen_tasks.add(task)
        deduped.append(item)
    return deduped


def _inventory_stats(items: list[dict]) -> dict:
    kinds = [(item.get("source_kind") or "").strip().lower() for item in items]
    objective = {
        "mcq", "fill_blank", "true_false", "match", "assertion_reason",
    }
    descriptive = {
        "long_answer", "short_answer", "source_task", "case_task",
        "passage_task", "writing_task",
    }
    return {
        "worked_examples": kinds.count("worked_example"),
        "solved_examples": kinds.count("solved_example"),
        "exercise_questions": kinds.count("exercise"),
        "checkpoint_questions": kinds.count("checkpoint_question"),
        "activities": kinds.count("activity"),
        "objective_items": sum(kind in objective for kind in kinds),
        "subjective_items": sum(kind not in objective for kind in kinds),
        "descriptive_items": sum(kind in descriptive for kind in kinds),
        "subparts": sum(bool(item.get("subpart_label")) for item in items),
        "visual_tasks": sum(bool(item.get("requires_visual")) for item in items),
        "table_or_graph_tasks": sum(
            kind in {"table_task", "graph_task"} for kind in kinds),
        "source_or_passage_tasks": sum(
            kind in {"source_task", "case_task", "passage_task"}
            for kind in kinds),
        "total_inventory_items": len(items),
    }


def _extract_question_task_inventory_via_api(*, meta: dict, sections: list[dict]) -> dict:
    import json as _json

    system = prompts.get_text("concepts.question_task_inventory.system")
    inventory = _empty_inventory()
    chunks = _inventory_chunks_by_topic(sections)
    if not chunks and sections:
        chunks = [{"sections": sections, "text": _format_section_chunk(sections)}]
    progress.log(f"Building Question / Task Inventory from {len(chunks)} chunk(s).")
    for i, chunk in enumerate(chunks, start=1):
        user = (
            _metadata_block(meta)
            + f"\nQuestion / Task Inventory chunk {i} of {len(chunks)}:\n"
            + chunk["text"]
        )
        data = _openai_json(system, user)
        items = [
            _sanitize_inventory_item(
                x, source_topic=chunk.get("source_topic") or "")
            for x in (data.get("items") or [])
            if isinstance(x, dict)
        ]
        # A chapter-scale chunk yielding a handful of items means the model
        # summarized question lists instead of itemizing them — retry once.
        expected_min = max(2, min(40, len(chunk["text"]) // 2_000))
        if len(items) < expected_min and _inventory_chunk_has_task_markers(chunk):
            progress.log(
                f"  inventory chunk {i}/{len(chunks)} returned only {len(items)} "
                f"item(s) for {len(chunk['text']):,} chars (expected >= "
                f"{expected_min}) — retrying with a density instruction.",
                level="warning",
            )
            retry_user = (
                user
                + f"\n\nYOUR PREVIOUS ANSWER HAD ONLY {len(items)} ITEMS — that is "
                "under-extraction. Re-read the chunk and itemize EVERY assessable "
                "question/task: every numbered exercise, every in-text checkpoint "
                "/ boxed '?' / 'Let's recall' prompt, every picture- or "
                "source-based ask (including chapter-opening source analysis), "
                "every activity, and every worked example is its own item. "
                "Multi-part questions with subquestions stay ONE item carrying "
                "the full stem + all subparts. Never merge a question list into "
                "one item, and never skip a checkpoint."
            )
            retry_data = _openai_json(system, retry_user)
            retry_items = [
                _sanitize_inventory_item(
                    x, source_topic=chunk.get("source_topic") or "")
                for x in (retry_data.get("items") or [])
                if isinstance(x, dict)
            ]
            if len(retry_items) > len(items):
                items = retry_items
        for item in items:
            inventory["items"].append(item)

    anchors = _source_task_anchors(sections)
    inventory["items"] = _merge_source_task_anchors(
        inventory["items"], anchors)
    for i, item in enumerate(inventory["items"], start=1):
        item["qid"] = f"QINV-{i:04d}"
        item["order_index"] = i
    inventory["stats"] = _inventory_stats(inventory["items"])
    if anchors:
        progress.log(
            f"Question / Task Inventory deterministic audit covered "
            f"{len(anchors)} worked-example/exercise anchor(s).")
    progress.log(f"Question / Task Inventory items: {len(inventory['items'])}.")
    return inventory


def _case_examples(case: dict) -> list[dict]:
    """Normalized example list of a Case (supports the legacy case_prompt form)."""
    out: list[dict] = []
    for ex in case.get("examples") or []:
        if isinstance(ex, dict):
            out.append(ex)
        elif isinstance(ex, str) and ex.strip():
            out.append({"source_question_id": "", "example_prompt": ex})
    legacy = (case.get("case_prompt") or "").strip()
    if legacy and not out:
        out.append({
            "source_question_id": (case.get("source_question_id") or "").strip(),
            "example_prompt": legacy,
        })
    return out


def _uncovered_inventory_items(inventory: dict, types: list[dict]) -> list[dict]:
    """Inventory items whose qid appears in no mined Type's source_question_ids."""
    covered: set[str] = set()
    for t in types:
        for qid in t.get("source_question_ids") or []:
            covered.add((qid or "").strip())
        for case in t.get("case_prompts") or []:
            if isinstance(case, dict):
                covered.add((case.get("source_question_id") or "").strip())
                for ex in _case_examples(case):
                    covered.add((ex.get("source_question_id") or "").strip())
    return [
        item for item in inventory.get("items", [])
        if (item.get("qid") or "").strip() not in covered
    ]


def _inventory_assignment_counts(types: list[dict]) -> dict[str, int]:
    """Count concrete Example placements per inventory qid.

    ``source_question_ids`` is debug traceability; the public output is driven
    by Examples. Count Examples first, then fall back to source_question_ids
    only when a Type has not emitted any examples for a qid yet.
    """
    counts: dict[str, int] = {}
    trace_only: dict[str, int] = {}
    for t in types:
        for qid in t.get("source_question_ids") or []:
            qid = (qid or "").strip()
            if qid:
                trace_only[qid] = trace_only.get(qid, 0) + 1
        for case in t.get("case_prompts") or []:
            if not isinstance(case, dict):
                continue
            for ex in _case_examples(case):
                qid = (ex.get("source_question_id") or "").strip()
                if qid:
                    counts[qid] = counts.get(qid, 0) + 1
    for qid, n in trace_only.items():
        counts.setdefault(qid, n)
    return counts


def _duplicate_inventory_assignments(inventory: dict, types: list[dict]) -> list[dict]:
    """Inventory items assigned more than once across Types/Cases/Examples."""
    counts = _inventory_assignment_counts(types)
    by_qid = {
        (item.get("qid") or "").strip(): item
        for item in inventory.get("items", [])
        if (item.get("qid") or "").strip()
    }
    return [
        {**by_qid.get(qid, {"qid": qid}), "assignment_count": count}
        for qid, count in counts.items()
        if qid and count > 1
    ]


def _repair_nested_mined_types(raw_types: list) -> list[dict]:
    """Lift Type-shaped objects that the model nested in ``case_prompts``."""
    repaired: list[dict] = []
    nested_count = 0

    def is_type_payload(value: object) -> bool:
        return (
            isinstance(value, dict)
            and bool(value.get("type_id"))
            and bool(value.get("type_title") or value.get("task_pattern"))
            and "case_prompts" in value
        )

    def visit(raw_type: dict, *, nested: bool = False) -> None:
        nonlocal nested_count
        item = dict(raw_type)
        cases: list = []
        nested_types: list[dict] = []
        for case in item.get("case_prompts") or []:
            if is_type_payload(case):
                nested_types.append(case)
            else:
                cases.append(case)
        item["case_prompts"] = cases
        repaired.append(item)
        if nested:
            nested_count += 1
        for nested_type in nested_types:
            visit(nested_type, nested=True)

    for raw_type in raw_types:
        if isinstance(raw_type, dict):
            visit(raw_type)
    if nested_count:
        progress.log(
            f"Recovered {nested_count} Type(s) nested inside case_prompts.",
            level="warning",
        )
    return repaired


_CASE_SOURCE_ARTIFACT_RE = re.compile(
    r"\b(?:examples?|exercises?|ex|fig(?:ure)?s?|tables?|page|p\.)\.?\s*\d",
    re.IGNORECASE,
)


def _inventory_task_text(item: dict) -> str:
    """Full source task text used for public example prompts.

    ``raw_task`` is preferred over ``normalized_task`` — reviewers require the
    complete untruncated source wording, and normalization tends to compress.
    Mathpix image URLs captured for the item are appended so figure-dependent
    questions ship their visuals.
    """
    task = (
        item.get("raw_task")
        or item.get("normalized_task")
        or item.get("question")
        or ""
    )
    source_kind = (item.get("source_kind") or "").strip().lower()
    task = _inventory_task_without_solution(
        str(task),
        aggressive=source_kind in {"worked_example", "solved_example"},
    )
    task = bi.to_plain_text(str(task)).strip()
    context = bi.to_plain_text(str(item.get("shared_context") or "")).strip()
    if context and item.get("requires_context") and context not in task:
        task = f"{context} {task}".strip()
    task = re.sub(r"\s+", " ", task)
    for url in item.get("image_urls") or []:
        url = str(url or "").strip()
        if url and url not in task:
            task = f"{task} ![]({url})"
    return task.strip()


def _case_prompt_needs_source(prompt: str, source_text: str) -> bool:
    prompt = re.sub(r"\s+", " ", (prompt or "").strip())
    if not source_text:
        return False
    if not prompt:
        return True
    if _CASE_SOURCE_ARTIFACT_RE.search(prompt):
        return True
    # When the source task is substantially richer, keep the teacher-facing
    # example faithful instead of a shortened paraphrase.
    return len(prompt) < max(25, int(len(source_text) * 0.7))


def _backfill_type_cases_from_inventory(types: list[dict], inventory: dict) -> list[dict]:
    """Ensure every source question attached to a Type appears as a full example.

    Works on the Case -> Examples schema (a Case is a defined sub-type; its
    ``examples`` carry the full source questions) and remains compatible with
    the legacy one-question-per-case ``case_prompt`` form.
    """
    by_qid = {
        (item.get("qid") or "").strip(): item
        for item in inventory.get("items", [])
        if (item.get("qid") or "").strip()
    }
    for mtype in types:
        if not isinstance(mtype, dict):
            continue
        source_ids = [
            (qid or "").strip()
            for qid in (mtype.get("source_question_ids") or [])
            if (qid or "").strip()
        ]
        cases = [
            dict(case) if isinstance(case, dict) else {"case_prompt": str(case)}
            for case in (mtype.get("case_prompts") or [])
        ]
        example_by_qid: dict[str, dict] = {}
        seen_prompts: set[str] = set()
        for case in cases:
            legacy = (case.get("case_prompt") or "").strip()
            if legacy:
                qid = (case.get("source_question_id") or "").strip()
                if qid:
                    example_by_qid[qid] = case
                seen_prompts.add(bi.normalize_question_text(legacy))
            for ex in case.get("examples") or []:
                if not isinstance(ex, dict):
                    continue
                qid = (ex.get("source_question_id") or "").strip()
                if qid:
                    example_by_qid[qid] = ex
                if ex.get("example_prompt"):
                    seen_prompts.add(bi.normalize_question_text(ex["example_prompt"]))
        for qid in source_ids:
            source_text = _inventory_task_text(by_qid.get(qid, {}))
            if not source_text:
                continue
            existing = example_by_qid.get(qid)
            if existing is not None:
                field = "example_prompt" if "example_prompt" in existing else "case_prompt"
                if _case_prompt_needs_source(existing.get(field, ""), source_text):
                    existing[field] = source_text
                continue
            key = bi.normalize_question_text(source_text)
            if key in seen_prompts:
                continue
            example = {"source_question_id": qid, "example_prompt": source_text}
            cases.append({
                "case_id": f"CASE-{len(cases) + 1:04d}",
                "case_title": (
                    mtype.get("task_pattern") or mtype.get("type_title") or ""
                ).strip(),
                "examples": [example],
                "case_signature": "",
            })
            seen_prompts.add(key)
        mtype["case_prompts"] = cases
    return types


def _type_source_qids(mtype: dict) -> list[str]:
    """Ordered source qids claimed by one mined Type."""
    out: list[str] = []
    for qid in mtype.get("source_question_ids") or []:
        qid = (qid or "").strip()
        if qid and qid not in out:
            out.append(qid)
    for case in mtype.get("case_prompts") or []:
        if not isinstance(case, dict):
            continue
        qid = (case.get("source_question_id") or "").strip()
        if qid and qid not in out:
            out.append(qid)
        for example in _case_examples(case):
            qid = (example.get("source_question_id") or "").strip()
            if qid and qid not in out:
                out.append(qid)
    return out


def _split_mined_types_by_source_topic(
    types: list[dict], inventory: dict,
) -> list[dict]:
    """Enforce one source topic per Type, splitting cross-topic model output."""
    topic_by_qid = {
        (item.get("qid") or "").strip(): (item.get("topic_hint") or "").strip()
        for item in inventory.get("items") or []
        if (item.get("qid") or "").strip()
    }
    split: list[dict] = []
    split_count = 0
    for original in types:
        if not isinstance(original, dict):
            continue
        source_qids = _type_source_qids(original)
        groups: dict[str, list[str]] = {}
        for qid in source_qids:
            topic = topic_by_qid.get(qid, "")
            groups.setdefault(topic, []).append(qid)
        nonempty_topics = [topic for topic in groups if topic]
        if len(nonempty_topics) <= 1:
            item = dict(original)
            if nonempty_topics:
                item["topic_match_hint"] = nonempty_topics[0]
            split.append(item)
            continue

        split_count += 1
        # A blank/unknown hint is not permission to discard the qid. Attach
        # those qids to the topic with the most sourced items; ties preserve
        # source order because ``nonempty_topics`` follows insertion order.
        dominant_topic = max(
            nonempty_topics, key=lambda topic: len(groups[topic]))
        for topic in nonempty_topics:
            qids = [
                qid for qid in source_qids
                if topic_by_qid.get(qid, "") == topic
                or (
                    not topic_by_qid.get(qid, "")
                    and topic == dominant_topic
                )
            ]
            qid_set = set(qids)
            item = dict(original)
            item["source_question_ids"] = qids
            item["topic_match_hint"] = topic
            filtered_cases: list[dict] = []
            for raw_case in original.get("case_prompts") or []:
                if not isinstance(raw_case, dict):
                    continue
                case = dict(raw_case)
                examples = [
                    dict(example)
                    for example in _case_examples(raw_case)
                    if (example.get("source_question_id") or "").strip()
                    in qid_set
                ]
                legacy_qid = (
                    raw_case.get("source_question_id") or "").strip()
                if examples:
                    case.pop("case_prompt", None)
                    case.pop("source_question_id", None)
                    case["examples"] = examples
                    filtered_cases.append(case)
                elif legacy_qid in qid_set:
                    filtered_cases.append(case)
            item["case_prompts"] = filtered_cases
            split.append(item)

    for i, item in enumerate(split, start=1):
        item["type_id"] = f"TYPE-{i:04d}"
    if split_count:
        progress.log(
            f"Split {split_count} cross-topic mined Type(s) into "
            f"{len(split)} source-topic-scoped Type(s).",
            level="warning",
        )
    return _backfill_type_cases_from_inventory(split, inventory)


def _normalize_mined_type_candidate(
    raw_types: list, inventory: dict,
) -> list[dict]:
    """Repair model schema drift, then apply deterministic Type normalization."""
    types = _repair_nested_mined_types(raw_types)
    types = _backfill_type_cases_from_inventory(types, inventory)
    return _split_mined_types_by_source_topic(types, inventory)


def _compact_mined_type_metadata(types: list[dict]) -> dict:
    """Small, assignment-free Type context for focused coverage calls."""
    type_fields = (
        "type_id", "type_title", "type_description", "task_pattern",
        "concept_match_hint", "parent_concept_match_hint", "topic_match_hint",
        "difficulty_hint", "cognitive_skill_hint", "subject_skill_hint",
        "is_activity",
    )
    case_fields = ("case_id", "case_title", "case_signature")
    compact: list[dict] = []
    for mtype in types:
        if not isinstance(mtype, dict):
            continue
        item = {
            field: mtype[field]
            for field in type_fields
            if mtype.get(field) not in (None, "", [], {})
        }
        cases = []
        for case in mtype.get("case_prompts") or []:
            if not isinstance(case, dict):
                continue
            case_meta = {
                field: case[field]
                for field in case_fields
                if case.get(field) not in (None, "", [], {})
            }
            if case_meta:
                cases.append(case_meta)
        if cases:
            item["cases"] = cases
        compact.append(item)
    return {"types": compact}


def _validate_focused_type_delta(
    data: dict, *, missed_items: list[dict], existing_types: list[dict],
) -> list[dict]:
    """Validate that a focused response is an additive, full-source delta."""
    import copy

    if not isinstance(data, dict) or not isinstance(data.get("types"), list):
        raise ValueError("response must contain a types list")
    raw_types = data["types"]
    if not raw_types:
        raise ValueError("response contains no delta Types")

    allowed_by_qid = {
        (item.get("qid") or "").strip(): item
        for item in missed_items
        if isinstance(item, dict) and (item.get("qid") or "").strip()
    }
    existing_counts = _inventory_assignment_counts(existing_types)
    existing_by_id = {
        (mtype.get("type_id") or "").strip(): mtype
        for mtype in existing_types
        if isinstance(mtype, dict) and (mtype.get("type_id") or "").strip()
    }
    errors: list[str] = []
    delta_counts: dict[str, int] = {}

    for type_index, raw_type in enumerate(raw_types, start=1):
        label = f"delta Type {type_index}"
        if not isinstance(raw_type, dict):
            errors.append(f"{label} is not an object")
            continue
        type_id = (raw_type.get("type_id") or "").strip()
        if not type_id:
            errors.append(f"{label} has no type_id")
        existing_type = existing_by_id.get(type_id)
        if existing_type is None and not (
            raw_type.get("type_title") or raw_type.get("task_pattern") or ""
        ).strip():
            errors.append(f"{label} has no precise Type title")
        if existing_type is not None:
            for field in (
                "type_title", "type_description", "task_pattern",
                "concept_match_hint", "parent_concept_match_hint",
                "topic_match_hint", "difficulty_hint", "cognitive_skill_hint",
                "subject_skill_hint", "is_activity",
            ):
                proposed = raw_type.get(field)
                current = existing_type.get(field)
                if (
                    proposed not in (None, "")
                    and current not in (None, "")
                    and proposed != current
                ):
                    errors.append(
                        f"{label} attempts to change immutable {field}")

        raw_source_ids = raw_type.get("source_question_ids")
        if not isinstance(raw_source_ids, list) or not raw_source_ids:
            errors.append(f"{label} has no source_question_ids list")
            source_ids: list[str] = []
        else:
            source_ids = []
            for raw_qid in raw_source_ids:
                if not isinstance(raw_qid, str) or not raw_qid.strip():
                    errors.append(f"{label} has an invalid source qid")
                    continue
                source_ids.append(raw_qid.strip())
        if len(source_ids) != len(set(source_ids)):
            errors.append(f"{label} repeats a source qid")

        source_topics = {
            (allowed_by_qid[qid].get("topic_hint") or "").strip()
            for qid in source_ids
            if qid in allowed_by_qid
        }
        if len(source_topics) > 1:
            errors.append(f"{label} crosses source topics")
        if existing_type is not None and source_topics:
            existing_topic = (
                existing_type.get("topic_match_hint") or "").strip()
            source_topic = next(iter(source_topics))
            if (
                existing_topic
                and source_topic
                and _topic_comparison_key(existing_topic)
                != _topic_comparison_key(source_topic)
            ):
                errors.append(
                    f"{label} attaches a missed qid to a different-topic Type")

        existing_cases_by_id = {
            (case.get("case_id") or "").strip(): case
            for case in (
                existing_type.get("case_prompts") or []
                if existing_type is not None else []
            )
            if isinstance(case, dict) and (case.get("case_id") or "").strip()
        }
        raw_cases = raw_type.get("case_prompts")
        if not isinstance(raw_cases, list) or not raw_cases:
            errors.append(f"{label} has no Case delta")
            raw_cases = []
        example_ids: list[str] = []
        for case_index, case in enumerate(raw_cases, start=1):
            case_label = f"{label} Case {case_index}"
            if not isinstance(case, dict):
                errors.append(f"{case_label} is not an object")
                continue
            case_id = (case.get("case_id") or "").strip()
            existing_case = existing_cases_by_id.get(case_id)
            if (
                existing_case is None
                and not (case.get("case_title") or "").strip()
            ):
                errors.append(f"{case_label} has no precise case_title")
            if existing_case is not None:
                for field in ("case_title", "case_signature"):
                    proposed = case.get(field)
                    current = existing_case.get(field)
                    if (
                        proposed not in (None, "")
                        and current not in (None, "")
                        and proposed != current
                    ):
                        errors.append(
                            f"{case_label} attempts to change immutable {field}")
            if (case.get("source_question_id") or "").strip() or (
                case.get("case_prompt") or ""
            ).strip():
                errors.append(
                    f"{case_label} uses a legacy Case instead of full Examples")
            examples = case.get("examples")
            if not isinstance(examples, list) or not examples:
                errors.append(f"{case_label} has no Examples")
                continue
            for example_index, example in enumerate(examples, start=1):
                example_label = f"{case_label} Example {example_index}"
                if not isinstance(example, dict):
                    errors.append(f"{example_label} is not an object")
                    continue
                qid = (example.get("source_question_id") or "").strip()
                if not qid:
                    errors.append(f"{example_label} has no source_question_id")
                    continue
                example_ids.append(qid)
                delta_counts[qid] = delta_counts.get(qid, 0) + 1
                if qid not in allowed_by_qid:
                    errors.append(f"{example_label} claims non-missed qid {qid}")
                    continue
                if existing_counts.get(qid, 0):
                    errors.append(
                        f"{example_label} duplicates an existing assignment")
                prompt = example.get("example_prompt")
                expected = _inventory_task_text(allowed_by_qid[qid])
                actual = re.sub(
                    r"\s+", " ", prompt.strip()
                ) if isinstance(prompt, str) else ""
                if not expected or actual != re.sub(r"\s+", " ", expected):
                    errors.append(
                        f"{example_label} does not carry the full source task")

        for qid in source_ids:
            if qid not in allowed_by_qid:
                errors.append(f"{label} claims non-missed qid {qid}")
            elif existing_counts.get(qid, 0):
                errors.append(f"{label} duplicates existing qid {qid}")
        if sorted(source_ids) != sorted(example_ids):
            errors.append(
                f"{label} source_question_ids do not match its Examples")

    repeated = sorted(qid for qid, count in delta_counts.items() if count != 1)
    if repeated:
        errors.append(
            "delta assigns qids more than once: " + ", ".join(repeated))
    if errors:
        raise ValueError("; ".join(errors))
    return copy.deepcopy(raw_types)


def _merge_focused_type_delta(
    types: list[dict], delta_types: list[dict],
) -> list[dict]:
    """Append a validated delta without replacing existing Type metadata."""
    import copy

    merged = copy.deepcopy(types)
    by_id = {
        (mtype.get("type_id") or "").strip(): mtype
        for mtype in merged
        if isinstance(mtype, dict) and (mtype.get("type_id") or "").strip()
    }
    for raw_delta in delta_types:
        delta = copy.deepcopy(raw_delta)
        type_id = (delta.get("type_id") or "").strip()
        target = by_id.get(type_id)
        if target is None:
            merged.append(delta)
            if type_id:
                by_id[type_id] = delta
            continue

        source_ids = target.setdefault("source_question_ids", [])
        for qid in delta.get("source_question_ids") or []:
            if qid not in source_ids:
                source_ids.append(qid)

        target_cases = target.setdefault("case_prompts", [])
        mergeable_cases = {
            (case.get("case_id") or "").strip(): case
            for case in target_cases
            if (
                isinstance(case, dict)
                and (case.get("case_id") or "").strip()
                and "examples" in case
                and not (case.get("case_prompt") or "").strip()
                and not (case.get("source_question_id") or "").strip()
            )
        }
        for delta_case in delta.get("case_prompts") or []:
            case_id = (delta_case.get("case_id") or "").strip()
            target_case = mergeable_cases.get(case_id)
            if target_case is None:
                target_cases.append(delta_case)
                continue
            target_case.setdefault("examples", []).extend(
                delta_case.get("examples") or [])
    return merged


def _recover_missed_type_deltas_via_api(
    *, meta: dict, inventory: dict, types: list[dict], max_attempts: int = 2,
) -> list[dict]:
    """Incrementally classify only missed qids, preserving existing assignments."""
    import copy
    import json as _json

    current = copy.deepcopy(types)
    system = prompts.get_text("concepts.type_mining_delta.system")
    for attempt in range(1, max(0, max_attempts) + 1):
        missed = _uncovered_inventory_items(inventory, current)
        if not missed:
            break
        user = (
            _metadata_block(meta)
            + "\nMISSED INVENTORY ITEMS (the only qids this delta may claim):\n"
            + _json.dumps({"items": missed}, ensure_ascii=False)
            + "\n\nCOMPACT EXISTING TYPE METADATA (immutable context; do not "
            "restate existing assignments):\n"
            + _json.dumps(
                _compact_mined_type_metadata(current), ensure_ascii=False)
        )
        try:
            data = _openai_json(system, user)
            delta = _validate_focused_type_delta(
                data, missed_items=missed, existing_types=current)
            candidate = _normalize_mined_type_candidate(
                _merge_focused_type_delta(current, delta), inventory)
        except Exception as exc:  # noqa: BLE001 — bounded fallback follows
            progress.log(
                f"Focused Type coverage attempt {attempt}/{max_attempts} "
                f"rejected: {exc}",
                level="warning",
            )
            continue

        current_counts = _inventory_assignment_counts(current)
        candidate_counts = _inventory_assignment_counts(candidate)
        altered = [
            qid for qid, count in current_counts.items()
            if count and candidate_counts.get(qid, 0) != count
        ]
        candidate_duplicates = _duplicate_inventory_assignments(
            inventory, candidate)
        candidate_missed = _uncovered_inventory_items(inventory, candidate)
        if (
            altered
            or candidate_duplicates
            or len(candidate_missed) >= len(missed)
        ):
            reasons = []
            if altered:
                reasons.append(
                    f"altered {len(altered)} classified assignment(s)")
            if candidate_duplicates:
                reasons.append(
                    f"created {len(candidate_duplicates)} duplicate(s)")
            if len(candidate_missed) >= len(missed):
                reasons.append("did not reduce missed coverage")
            progress.log(
                f"Focused Type coverage attempt {attempt}/{max_attempts} "
                "rejected: " + ", ".join(reasons) + ".",
                level="warning",
            )
            continue
        accepted = len(missed) - len(candidate_missed)
        current = candidate
        progress.log(
            f"Focused Type coverage attempt {attempt}/{max_attempts}: accepted "
            f"{accepted} missed item(s); {len(candidate_missed)} still missed.",
            level="success" if not candidate_missed else "warning",
        )
    return current


_FALLBACK_TYPE_WORDING = {
    "worked_example": (
        "Solving a Worked-Example Problem",
        "Worked-example problem with all stated givens and the requested result",
    ),
    "solved_example": (
        "Solving a Worked-Example Problem",
        "Worked-example problem with all stated givens and the requested result",
    ),
    "exercise": (
        "Solving an Exercise Problem",
        "Exercise problem with all stated givens, constraints, and asks",
    ),
    "intext_question": (
        "Answering an In-text Question",
        "In-text question with its complete context and requested response",
    ),
    "checkpoint_question": (
        "Answering a Checkpoint Question",
        "Checkpoint question with its complete context and requested response",
    ),
    "activity": (
        "Completing a Source Activity",
        "Activity task with every stated action, condition, and observation",
    ),
    "mcq": (
        "Selecting the Correct Multiple-Choice Response",
        "Multiple-choice question with the complete stem and all options",
    ),
    "fill_blank": (
        "Completing a Fill-in-the-Blank Task",
        "Fill-in-the-blank prompt with every supplied statement and blank",
    ),
    "true_false": (
        "Evaluating a True-or-False Statement",
        "True-or-false task with the complete statement and required justification",
    ),
    "match": (
        "Matching Corresponding Items",
        "Matching task with every item and available correspondence",
    ),
    "assertion_reason": (
        "Evaluating an Assertion and Reason",
        "Assertion-and-reason task with both complete statements",
    ),
    "diagram_task": (
        "Interpreting a Diagram to Complete a Task",
        "Diagram-dependent task with its referenced visual and complete ask",
    ),
    "map_task": (
        "Interpreting a Map to Complete a Task",
        "Map-dependent task with its referenced visual and complete ask",
    ),
    "table_task": (
        "Interpreting a Table to Complete a Task",
        "Table-dependent task with all supplied data and the complete ask",
    ),
    "graph_task": (
        "Interpreting a Graph to Complete a Task",
        "Graph-dependent task with its referenced visual and complete ask",
    ),
    "source_task": (
        "Interpreting a Source to Answer a Question",
        "Source-based task with the full source context and complete ask",
    ),
    "case_task": (
        "Applying a Concept to a Case",
        "Case-based task with the full case context and complete ask",
    ),
    "passage_task": (
        "Interpreting a Passage to Answer a Question",
        "Passage-based task with the full passage context and complete ask",
    ),
    "grammar_task": (
        "Applying a Grammar Operation",
        "Grammar task with the complete language context and requested operation",
    ),
    "writing_task": (
        "Producing a Constrained Written Response",
        "Writing task with its complete purpose, audience, form, and constraints",
    ),
    "experiment_task": (
        "Completing an Experimental Task",
        "Experimental task with all apparatus, conditions, actions, and observations",
    ),
    "coding_task": (
        "Completing a Coding Task",
        "Coding task with the full input, constraints, and requested output",
    ),
    "long_answer": (
        "Constructing a Long-form Answer",
        "Long-answer task with the complete context and every requested part",
    ),
    "short_answer": (
        "Constructing a Short Answer",
        "Short-answer task with the complete context and requested response",
    ),
    "other": (
        "Completing a Source-defined Task",
        "Source-defined task with all supplied context, conditions, and asks",
    ),
}


def _deterministic_fallback_type(item: dict) -> dict | None:
    """Build one source-faithful, topic-scoped Type for one missed item."""
    qid = (item.get("qid") or "").strip()
    if not qid:
        return None
    topic = (item.get("topic_hint") or "").strip()
    cleaned = _sanitize_inventory_item(item, source_topic=topic)
    cleaned["topic_hint"] = topic
    cleaned["raw_task"] = _inventory_task_without_solution(
        cleaned.get("raw_task") or "", aggressive=True)
    cleaned["normalized_task"] = _inventory_task_without_solution(
        cleaned.get("normalized_task") or cleaned["raw_task"], aggressive=True)
    source_task = _inventory_task_text(cleaned)
    if not source_task:
        return None
    source_kind = (cleaned.get("source_kind") or "other").strip().lower()
    title, case_title = _FALLBACK_TYPE_WORDING.get(
        source_kind,
        (
            "Completing the Stated Source Task",
            "Source task with all supplied context, conditions, and requested outputs",
        ),
    )
    return {
        "type_id": f"FALLBACK-{qid}",
        "type_title": title,
        "type_description": (
            f"The learner completes the {source_kind.replace('_', ' ')} exactly "
            "as stated, retaining every supplied condition and representation."
        ),
        "task_pattern": (
            f"Given a complete {source_kind.replace('_', ' ')} prompt, produce "
            "the requested response without omitting its stated constraints."
        ),
        "source_question_ids": [qid],
        "case_prompts": [{
            "case_id": "CASE-FALLBACK-0001",
            "case_title": case_title,
            "examples": [{
                "source_question_id": qid,
                "example_prompt": source_task,
            }],
            "case_signature": source_kind,
        }],
        "concept_match_hint": "",
        "parent_concept_match_hint": "",
        "topic_match_hint": topic,
        "difficulty_hint": "",
        "cognitive_skill_hint": "",
        "subject_skill_hint": "",
        "is_activity": source_kind == "activity",
    }


def _append_deterministic_type_fallbacks(
    types: list[dict], *, missed_items: list[dict], inventory: dict,
) -> tuple[list[dict], int]:
    """Append, normalize, and source-scope one fallback per still-missed qid."""
    import copy

    fallbacks = [
        fallback
        for item in missed_items
        if (fallback := _deterministic_fallback_type(item)) is not None
    ]
    if not fallbacks:
        return types, 0
    merged = copy.deepcopy(types)
    merged.extend(fallbacks)
    return _normalize_mined_type_candidate(merged, inventory), len(fallbacks)


def _apply_exact_once_duplicate_backstop(
    types: list[dict], inventory: dict,
) -> tuple[list[dict], int]:
    """Prune duplicate qids while retaining one stable, full Example placement."""
    inventory_by_qid = {
        (item.get("qid") or "").strip(): item
        for item in inventory.get("items") or []
        if (item.get("qid") or "").strip()
    }
    before_counts = _inventory_assignment_counts(types)
    duplicate_qids = [
        qid for qid, count in before_counts.items()
        if count > 1 and qid in inventory_by_qid
    ]
    if not duplicate_qids:
        return types, 0

    duplicate_set = set(duplicate_qids)
    concrete: dict[str, list[dict]] = {
        qid: [] for qid in duplicate_qids}
    trace_types: dict[str, list[int]] = {
        qid: [] for qid in duplicate_qids}
    for type_index, mtype in enumerate(types):
        if not isinstance(mtype, dict):
            continue
        for source_qid in mtype.get("source_question_ids") or []:
            qid = (source_qid or "").strip()
            if qid in duplicate_set:
                trace_types[qid].append(type_index)
        for case_index, case in enumerate(mtype.get("case_prompts") or []):
            if not isinstance(case, dict):
                continue
            raw_examples = case.get("examples") or []
            usable_examples = [
                example for example in raw_examples
                if isinstance(example, dict)
                or (isinstance(example, str) and example.strip())
            ]
            for example_index, example in enumerate(raw_examples):
                if not isinstance(example, dict):
                    continue
                qid = (example.get("source_question_id") or "").strip()
                if qid in duplicate_set:
                    concrete[qid].append({
                        "type_index": type_index,
                        "case_index": case_index,
                        "example_index": example_index,
                        "kind": "example",
                    })
            legacy_qid = (case.get("source_question_id") or "").strip()
            if (
                legacy_qid in duplicate_set
                and (case.get("case_prompt") or "").strip()
                and not usable_examples
            ):
                concrete[legacy_qid].append({
                    "type_index": type_index,
                    "case_index": case_index,
                    "example_index": None,
                    "kind": "legacy",
                })

    def topic_matches(qid: str, placement: dict) -> bool:
        source_topic = _topic_comparison_key(
            inventory_by_qid[qid].get("topic_hint") or "")
        type_index = placement["type_index"]
        if not source_topic or not 0 <= type_index < len(types):
            return False
        mtype = types[type_index]
        return (
            isinstance(mtype, dict)
            and _topic_comparison_key(
                mtype.get("topic_match_hint") or "") == source_topic
        )

    winners: dict[str, dict] = {}
    for qid in duplicate_qids:
        placements = concrete[qid]
        if not placements:
            # A trace-only duplicate can be made concrete only when the
            # inventory has full source wording for deterministic backfill.
            if not _inventory_task_text(inventory_by_qid[qid]):
                continue
            placements = [
                {
                    "type_index": type_index,
                    "case_index": None,
                    "example_index": None,
                    "kind": "trace",
                }
                for type_index in trace_types[qid]
            ]
        if not placements:
            continue
        winners[qid] = next(
            (
                placement for placement in placements
                if topic_matches(qid, placement)
            ),
            placements[0],
        )
    if not winners:
        return types, 0

    pruned: list[dict] = []
    winner_qids = set(winners)
    for type_index, raw_type in enumerate(types):
        if not isinstance(raw_type, dict):
            continue
        mtype = dict(raw_type)
        source_ids: list[str] = []
        kept_source_ids: set[str] = set()
        for source_qid in raw_type.get("source_question_ids") or []:
            qid = (source_qid or "").strip()
            if qid not in winner_qids:
                source_ids.append(source_qid)
                continue
            if (
                winners[qid]["type_index"] == type_index
                and qid not in kept_source_ids
            ):
                source_ids.append(qid)
                kept_source_ids.add(qid)
        for qid, winner in winners.items():
            if (
                winner["type_index"] == type_index
                and qid not in kept_source_ids
            ):
                source_ids.append(qid)
                kept_source_ids.add(qid)
        mtype["source_question_ids"] = source_ids

        cases: list = []
        for case_index, raw_case in enumerate(
            raw_type.get("case_prompts") or []
        ):
            if not isinstance(raw_case, dict):
                cases.append(raw_case)
                continue
            case = dict(raw_case)
            if "examples" in raw_case:
                examples: list = []
                for example_index, raw_example in enumerate(
                    raw_case.get("examples") or []
                ):
                    if not isinstance(raw_example, dict):
                        examples.append(raw_example)
                        continue
                    example = dict(raw_example)
                    qid = (
                        example.get("source_question_id") or "").strip()
                    if qid not in winner_qids:
                        examples.append(example)
                        continue
                    winner = winners[qid]
                    if (
                        winner["kind"] == "example"
                        and winner["type_index"] == type_index
                        and winner["case_index"] == case_index
                        and winner["example_index"] == example_index
                    ):
                        examples.append(example)
                case["examples"] = examples

            legacy_qid = (
                raw_case.get("source_question_id") or "").strip()
            if legacy_qid in winner_qids:
                winner = winners[legacy_qid]
                keep_legacy = (
                    winner["kind"] == "legacy"
                    and winner["type_index"] == type_index
                    and winner["case_index"] == case_index
                )
                if not keep_legacy:
                    case.pop("source_question_id", None)
                    usable_examples = [
                        example
                        for example in (case.get("examples") or [])
                        if isinstance(example, dict)
                        or (isinstance(example, str) and example.strip())
                    ]
                    if not usable_examples:
                        case.pop("case_prompt", None)

            case_examples = _case_examples(case)
            has_example = any(
                (example.get("source_question_id") or "").strip()
                or (example.get("example_prompt") or "").strip()
                for example in case_examples
            )
            if (
                not has_example
                and not (case.get("source_question_id") or "").strip()
            ):
                continue
            cases.append(case)
        mtype["case_prompts"] = cases
        if source_ids or cases:
            pruned.append(mtype)

    normalized = _normalize_mined_type_candidate(pruned, inventory)
    after_counts = _inventory_assignment_counts(normalized)
    if any(after_counts.get(qid, 0) < 1 for qid in winners):
        return types, 0
    removed = sum(
        max(0, before_counts[qid] - after_counts.get(qid, 0))
        for qid in winners
    )
    return normalized, removed


def _mine_types_from_inventory_via_api(
    *, meta: dict, inventory: dict, max_coverage_attempts: int = 4,
    max_focused_attempts: int = 2,
) -> dict:
    """Mine reusable Types with mandatory inventory coverage.

    Broad replacement repairs resolve duplicate placements. Once only missed
    qids remain, focused calls return additive deltas and a deterministic
    single-item fallback closes any residual gap without replacing authored
    Types. The final exact-once gate remains mandatory.
    """
    import json as _json

    if not inventory.get("items"):
        progress.log("Type Mining skipped — no Question / Task Inventory items.", level="warning")
        return {"types": []}
    system = prompts.get_text("concepts.type_mining.system")
    user = (
        _metadata_block(meta)
        + "\nQuestion / Task Inventory:\n"
        + _json.dumps(inventory, ensure_ascii=False)
    )
    progress.log(
        f"Mining reusable Types from {len(inventory.get('items', []))} inventory item(s).")
    data = _openai_json(system, user)
    types = _normalize_mined_type_candidate(
        list(data.get("types") or []), inventory)
    progress.log(f"Type Mining produced {len(types)} reusable Type(s).")

    for attempt in range(1, max_coverage_attempts + 1):
        missed = _uncovered_inventory_items(inventory, types)
        duplicates = _duplicate_inventory_assignments(inventory, types)
        if not missed and not duplicates:
            break
        if missed and not duplicates:
            progress.log(
                f"Type Mining broad repairs left {len(missed)} missed item(s) "
                "and no duplicates — switching to focused coverage deltas.",
                level="warning",
            )
            break
        progress.log(
            f"Type Mining coverage attempt {attempt}: {len(missed)} inventory "
            f"item(s) unclassified, {len(duplicates)} duplicate assignment(s) "
            "— asking GPT for a complete corrected Type list.",
            level="warning",
        )
        follow_up = (
            _metadata_block(meta)
            + "\nALREADY MINED TYPES (for context; return a COMPLETE corrected list):\n"
            + _json.dumps({"types": types}, ensure_ascii=False)
            + "\n\nCOVERAGE DEFECTS TO FIX:\n"
            + _json.dumps({
                "unclassified_items": missed,
                "duplicate_assignments": duplicates,
            }, ensure_ascii=False)
            + "\n\nReturn the COMPLETE corrected {\"types\": [...]} list. "
            "Every inventory qid must appear exactly once as one Example under "
            "one Case in one Type. Remove duplicate placements; never drop the "
            "question entirely. Keep full source wording."
        )
        corrected = _openai_json(system, follow_up)
        corrected_types = corrected.get("types") or []
        candidate = _normalize_mined_type_candidate(
            list(corrected_types), inventory)
        candidate_missed = _uncovered_inventory_items(inventory, candidate)
        candidate_duplicates = _duplicate_inventory_assignments(
            inventory, candidate)
        current_defects = len(missed) + len(duplicates)
        candidate_defects = len(candidate_missed) + len(candidate_duplicates)
        if candidate_defects < current_defects:
            types = candidate
        else:
            progress.log(
                "Rejected Type Mining coverage repair because exact-once "
                f"defects did not improve ({candidate_defects} candidate vs "
                f"{current_defects} current).",
                level="warning",
            )

    remaining_duplicates = _duplicate_inventory_assignments(inventory, types)
    if remaining_duplicates:
        types, removed = _apply_exact_once_duplicate_backstop(types, inventory)
        progress.log(
            "Type Mining exact-once duplicate backstop removed "
            f"{removed} duplicate placement(s) across "
            f"{len(remaining_duplicates)} inventory item(s).",
            level="warning",
        )

    remaining_missed = _uncovered_inventory_items(inventory, types)
    remaining_duplicates = _duplicate_inventory_assignments(inventory, types)
    if remaining_missed and not remaining_duplicates:
        types = _recover_missed_type_deltas_via_api(
            meta=meta,
            inventory=inventory,
            types=types,
            max_attempts=max_focused_attempts,
        )

    remaining_missed = _uncovered_inventory_items(inventory, types)
    if remaining_missed:
        types, fallback_count = _append_deterministic_type_fallbacks(
            types, missed_items=remaining_missed, inventory=inventory)
        if fallback_count:
            progress.log(
                "Type Mining deterministic coverage fallback added "
                f"{fallback_count} single-item Type(s) for still-missed qids.",
                level="warning",
            )

    # Re-run the exact-once pruning after focused merges/fallbacks; malformed
    # or unrepairable residual defects still fail the hard gate below.
    remaining_duplicates = _duplicate_inventory_assignments(inventory, types)
    if remaining_duplicates:
        types, removed = _apply_exact_once_duplicate_backstop(types, inventory)
        progress.log(
            "Type Mining final exact-once duplicate pruning removed "
            f"{removed} duplicate placement(s) across "
            f"{len(remaining_duplicates)} inventory item(s).",
            level="warning",
        )

    still_missed = _uncovered_inventory_items(inventory, types)
    still_duplicate = _duplicate_inventory_assignments(inventory, types)
    total = len(inventory.get("items", []))
    progress.log(
        f"Type Mining coverage: {total - len(still_missed)}/{total} inventory "
        f"item(s) classified into {len(types)} Type(s); "
        f"{len(still_duplicate)} duplicate assignment(s).",
        level="warning" if still_missed or still_duplicate else "success",
    )
    if still_missed or still_duplicate:
        raise RuntimeError(
            "Type Mining failed exact inventory coverage after "
            f"{max_coverage_attempts} broad and {max_focused_attempts} focused "
            "repair attempt(s): "
            f"{len(still_missed)} unclassified item(s), "
            f"{len(still_duplicate)} duplicate assignment(s)."
        )
    return {"types": types}


def _mined_type_to_body(mtype: dict, start_type: int) -> tuple[str, int]:
    """Render one mined Type into a ``Type NN: ... Case NN: ...`` fragment.

    This is deterministic formatting only — the Type's title/definition/cases
    are authored by the API mining step. The Type line carries the precise
    pattern name plus its 1-2 sentence definition so every Type reads as a
    properly defined assessment pattern. Source labels (e.g. "Exercise 1.2")
    are intentionally NOT stripped here: if mining disobeyed the prompt and
    left one, final validation flags the row and the repair pass substitutes
    the full actual problem content from the source (preferred), with
    deterministic neutralization as the post-repair last resort.
    """
    title = concept_cleanup.strip_dangling_references(
        (mtype.get("type_title") or mtype.get("task_pattern") or "").strip())
    definition = concept_cleanup.strip_dangling_references(
        (mtype.get("type_description") or "").strip())
    if definition and _norm_for_compare(definition) != _norm_for_compare(title):
        title = f"{title} — {definition.rstrip('.')}"
    cases: list[tuple[str, list[str]]] = []
    for case in (mtype.get("case_prompts") or []):
        if isinstance(case, str):
            case = {"case_prompt": case}
        if not isinstance(case, dict):
            continue
        case_title = (case.get("case_title") or "").strip()
        examples = [
            (ex.get("example_prompt") or "").strip()
            for ex in _case_examples(case)
        ]
        examples = [ex for ex in examples if ex]
        if not case_title and not examples:
            continue
        # Legacy mined output has no sub-type definition; the full question
        # doubles as the Case line so nothing is lost.
        if not case_title:
            case_title = examples[0]
            examples = examples[1:]
        cases.append((case_title, examples))
    if not title or not cases:
        return "", start_type
    n = start_type + 1
    parts = [f"Type {n:02d}: {title}"]
    for c_i, (case_title, examples) in enumerate(cases, start=1):
        parts.append(f"Case {c_i:02d}: {case_title}")
        for example in examples:
            parts.append(f"Example: {example}")
    return " ".join(parts), n


def _norm_for_compare(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _concept_description_only(details: str) -> str:
    for label, content in cr.split_sections(details or ""):
        if label.lower().startswith("description"):
            return content
    return ""


def _set_description(details: str, new_description: str) -> str:
    """Replace (or prepend) the Description section content."""
    sections = cr.split_sections(details or "")
    for i, (label, _content) in enumerate(sections):
        if label.lower().startswith("description"):
            sections[i] = (label, new_description)
            return cr.join_sections(sections)
    sections.insert(0, ("Description", new_description))
    return cr.join_sections(sections)


def _has_mastery_line(details: str) -> bool:
    return bool(cr._MASTERY_LABEL_RE.search(_concept_description_only(details)))


def _ensure_mastery_lines_via_api(
    records: list[dict], *, meta: dict, use_api: bool = True,
) -> list[dict]:
    """Guarantee every normal concept Description ends with the line-broken
    "Achieving Mastery: ..." statement.

    The description-refine prompt asks for it, but models skip it on a
    fraction of rows; this pass sends ONLY the missing Descriptions back for
    completion, and falls back to a deterministic statement so the required
    format is always present.
    """
    import json as _json

    targets = [
        i for i, rec in enumerate(records)
        if not cr.is_culmination(rec.get("concept_title", ""))
        and not _has_mastery_line(rec.get("concept_details", ""))
    ]
    if not targets:
        return records
    progress.log(
        f"Adding the missing 'Achieving Mastery' line to {len(targets)} concept(s).")
    system = prompts.get_text("concepts.mastery_line.system")
    rows = [
        {
            "topic": records[i].get("topic", ""),
            "parent_concept": records[i].get("parent_concept", ""),
            "concept": records[i].get("concept_title", ""),
            "concept_description": "Description: "
            + _concept_description_only(records[i].get("concept_details", "")),
            "keywords": records[i].get("keywords", ""),
        }
        for i in targets
    ]
    user = (
        _metadata_block(meta)
        + "\nDescriptions missing their final mastery statement:\n"
        + _json.dumps({"rows": rows}, ensure_ascii=False)
    )
    by_title: dict[str, str] = {}
    if use_api:
        try:
            data = _openai_json(system, user)
            for row in _concept_rows_to_records(data):
                desc = _concept_description_only(row.get("concept_details", ""))
                if cr._MASTERY_LABEL_RE.search(desc):
                    by_title[bi.normalize_question_text(row["concept_title"])] = desc
        except Exception as exc:  # noqa: BLE001 — fall back deterministically
            progress.log(f"Mastery-line pass failed ({exc}) — using fallback lines.",
                         level="warning")
    completed = 0
    for i in targets:
        rec = records[i]
        desc = by_title.get(bi.normalize_question_text(rec.get("concept_title", "")))
        if not desc:
            title = (rec.get("concept_title") or "this concept").strip().rstrip(".")
            desc = (
                _concept_description_only(rec.get("concept_details", "")).rstrip()
                + f"\nAchieving Mastery: Applying {title} correctly in new problems."
            )
        rec["concept_details"] = cr.format_mastery_statement(
            _set_description(rec.get("concept_details", ""), desc))
        completed += 1
    progress.log(f"Mastery lines completed for {completed} concept(s).",
                 level="success")
    return records


def _merge_similar_concepts_via_api(records: list[dict], *, meta: dict) -> list[dict]:
    """Merge near-duplicate concept rows via GPT instead of dropping them.

    ``concept_cleanup.find_similar_title_groups`` only DETECTS suspects; the
    content decision (which title/topic survives, how Descriptions, Types and
    Misconceptions combine) is GPT's. On failure the deterministic drop is the
    last resort so duplicates never ship.
    """
    import json as _json

    groups = concept_cleanup.find_similar_title_groups(records)
    if not groups:
        return records
    progress.log(
        f"Merging {len(groups)} group(s) of near-duplicate concepts via API.")
    system = prompts.get_text("concepts.merge_duplicates.system")
    merged_by_first: dict[int, dict] = {}
    drop: set[int] = set()
    for group in groups:
        rows = [records[i] for i in group]
        user = (
            _metadata_block(meta)
            + "\nRows restating the same concept — merge into ONE row:\n"
            + _json.dumps({"rows": _records_to_api_rows(rows)}, ensure_ascii=False)
        )
        try:
            data = _openai_json(system, user)
            merged_rows = _concept_rows_to_records(data)
        except Exception as exc:  # noqa: BLE001 — deterministic drop still guards
            progress.log(
                f"Duplicate-merge pass failed ({exc}) — deterministic dedupe "
                "will handle this group.",
                level="warning")
            continue
        if len(merged_rows) != 1:
            progress.log(
                f"Duplicate-merge returned {len(merged_rows)} row(s) for a "
                f"group of {len(group)} — keeping deterministic dedupe.",
                level="warning")
            continue
        merged_by_first[group[0]] = merged_rows[0]
        drop.update(group[1:])
    if not merged_by_first:
        return records
    out: list[dict] = []
    for i, rec in enumerate(records):
        if i in drop:
            continue
        out.append(merged_by_first.get(i, rec))
    progress.log(
        f"Merged {len(drop)} duplicate row(s) into {len(merged_by_first)} concept(s).",
        level="success")
    return out


def _misconception_body(details: str) -> str:
    for label, content in cr.split_sections(details or ""):
        if label.strip().lower().startswith("misconception"):
            return content.strip()
    return ""


def _ensure_misconceptions_via_api(
    records: list[dict], *, meta: dict, use_api: bool = True,
) -> list[dict]:
    """Have GPT write missing or generic-only Misconceptions sections.

    Reviewers flagged the deterministic fallback text as too generic; concept
    quality requires real, concept-specific learner errors. Only rows whose
    Misconceptions are missing, empty, or generic are sent. The deterministic
    template remains the dry-mode / API-failure last resort (added later by
    ``concept_refiner.ensure_misconceptions``).
    """
    import json as _json

    targets = [
        i for i, rec in enumerate(records)
        if not cr.is_culmination(rec.get("concept_title", ""))
        and (rec.get("concept_details") or "").strip()
        and (
            not _misconception_body(rec.get("concept_details", ""))
            or cr._is_generic_misconception(
                _misconception_body(rec.get("concept_details", "")))
        )
    ]
    if not targets or not use_api:
        return records
    progress.log(
        f"Writing specific Misconceptions for {len(targets)} concept(s) via API.")
    system = prompts.get_text("concepts.misconceptions.system")
    rows = [
        {
            "topic": records[i].get("topic", ""),
            "parent_concept": records[i].get("parent_concept", ""),
            "concept": records[i].get("concept_title", ""),
            "concept_description": records[i].get("concept_details", ""),
            "keywords": records[i].get("keywords", ""),
        }
        for i in targets
    ]
    user = (
        _metadata_block(meta)
        + "\nRows missing a specific Misconceptions section:\n"
        + _json.dumps({"rows": rows}, ensure_ascii=False)
    )
    by_title: dict[str, str] = {}
    try:
        data = _openai_json(system, user)
        for row in _concept_rows_to_records(data):
            body = _misconception_body(row.get("concept_details", ""))
            if body and not cr._is_generic_misconception(body):
                by_title[bi.normalize_question_text(row["concept_title"])] = body
    except Exception as exc:  # noqa: BLE001 — deterministic backstop follows later
        progress.log(
            f"Misconception pass failed ({exc}) — deterministic fallback will apply.",
            level="warning")
        return records
    completed = 0
    for i in targets:
        rec = records[i]
        body = by_title.get(bi.normalize_question_text(rec.get("concept_title", "")))
        if not body:
            continue
        sections = [
            (label, content)
            for label, content in cr.split_sections(rec.get("concept_details", ""))
            if not label.strip().lower().startswith("misconception")
        ]
        sections.append(("Misconceptions", body))
        rec["concept_details"] = cr.join_sections(sections)
        completed += 1
    progress.log(f"Specific Misconceptions written for {completed} concept(s).",
                 level="success")
    return records


def _assign_mined_types_via_api(
    records: list[dict], *, meta: dict, mined_types: dict, max_attempts: int = 4,
) -> list[dict]:
    """Embed every mined Type within its source topic using exact API IDs.

    Source-topic-scoped Types are grouped by canonical topic and allowed
    concept IDs. Each group sees only those concepts, including that topic's
    culmination, and omitted IDs are retried against the same candidate list.
    Placement is joined by exact IDs only — no regex, token, or word matching.
    """
    import json as _json

    types = (mined_types or {}).get("types") or []
    if not types:
        return records

    # Culmination rows are included so mixed/synthesis Types mined from the
    # source can be placed on them (this pass runs after the culmination pass).
    cid_map: dict[str, dict] = {}
    concept_payload: list[dict] = []
    for i, rec in enumerate(records, start=1):
        cid = f"CONCEPT-{i:04d}"
        cid_map[cid] = rec
        concept_payload.append({
            "concept_id": cid,
            "topic": rec.get("topic", ""),
            "parent_concept": rec.get("parent_concept", ""),
            "concept": rec.get("concept_title", ""),
            "concept_description": _concept_description_only(rec.get("concept_details", "")),
            "is_culmination": cr.is_culmination(rec.get("concept_title", "")),
            # Reading-order position — placements must not jump ahead of the
            # chapter (e.g. heating-effect questions under resistivity).
            "chapter_position": i,
        })

    types_by_id: dict[str, dict] = {}
    for i, t in enumerate(types, start=1):
        tid = (t.get("type_id") or f"TYPE-{i:04d}").strip() or f"TYPE-{i:04d}"
        t = dict(t)
        t["type_id"] = tid
        if tid in types_by_id:
            raise RuntimeError(
                "type embedding failed: duplicate mined Type ID "
                f"{tid} prevents exact-once assignment")
        types_by_id[tid] = t

    concept_ids_by_topic: dict[str, list[str]] = {}
    for row in concept_payload:
        topic_key = _topic_comparison_key(row.get("topic", ""))
        concept_ids_by_topic.setdefault(topic_key, []).append(row["concept_id"])

    all_concept_ids = tuple(cid_map)
    topic_key_by_tid: dict[str, str] = {}
    allowed_cids_by_tid: dict[str, set[str] | None] = {}
    candidate_cids_by_tid: dict[str, tuple[str, ...]] = {}
    missing_scopes: list[tuple[str, str, str]] = []
    for tid, mtype in types_by_id.items():
        source_topic = (mtype.get("topic_match_hint") or "").strip()
        topic_key = _topic_comparison_key(source_topic)
        topic_key_by_tid[tid] = topic_key
        if topic_key:
            allowed = set(concept_ids_by_topic.get(topic_key, []))
            allowed_cids_by_tid[tid] = allowed
            candidate_cids_by_tid[tid] = tuple(
                cid for cid in all_concept_ids if cid in allowed)
            if not allowed:
                missing_scopes.append((tid, source_topic, topic_key))
        else:
            allowed_cids_by_tid[tid] = None
            candidate_cids_by_tid[tid] = all_concept_ids

    if missing_scopes:
        failures = "; ".join(
            f"{tid} source topic {topic!r} normalizes to {topic_key!r}"
            for tid, topic, topic_key in missing_scopes
        )
        available_topics = sorted({
            (
                (row.get("topic") or "").strip(),
                _topic_comparison_key(row.get("topic", "")),
            )
            for row in concept_payload
            if _topic_comparison_key(row.get("topic", ""))
        })
        available = ", ".join(
            f"{topic!r} -> {topic_key!r}"
            for topic, topic_key in available_topics
        ) or "(none)"
        raise RuntimeError(
            "type embedding source-topic normalization failed: no allowed "
            f"concept candidates for {failures}; available normalized concept "
            f"topics: {available}"
        )

    if not all_concept_ids:
        raise RuntimeError(
            "type embedding failed: no concept candidates for mined Types "
            + ", ".join(types_by_id)
        )

    system = prompts.get_text("concepts.type_embedding.system")
    concept_payload_by_id = {
        row["concept_id"]: row for row in concept_payload
    }
    groups: dict[tuple[str, tuple[str, ...]], list[str]] = {}
    for tid in types_by_id:
        group_key = (topic_key_by_tid[tid], candidate_cids_by_tid[tid])
        groups.setdefault(group_key, []).append(tid)

    per_concept: dict[str, list[dict]] = {}
    unassigned: set[str] = set()
    for (_, candidate_cids), group_type_ids in groups.items():
        scoped_concepts = [
            concept_payload_by_id[cid] for cid in candidate_cids
        ]
        candidate_cid_set = set(candidate_cids)
        remaining_in_group = set(group_type_ids)
        scope_label = (
            types_by_id[group_type_ids[0]].get("topic_match_hint")
            or "unscoped chapter"
        )
        for attempt in range(1, max_attempts + 1):
            if not remaining_in_group:
                break
            pending = []
            for tid in group_type_ids:
                if tid not in remaining_in_group:
                    continue
                item = dict(types_by_id[tid])
                allowed = allowed_cids_by_tid[tid]
                if allowed is not None:
                    item["allowed_concept_ids"] = sorted(allowed)
                pending.append(item)
            user = (
                _metadata_block(meta)
                + "\nCONCEPTS (assign every mined Type to exactly one concept_id):\n"
                + _json.dumps({"concepts": scoped_concepts}, ensure_ascii=False)
                + "\n\nMINED TYPES TO ASSIGN (every type_id MUST be assigned):\n"
                + _json.dumps({"types": pending}, ensure_ascii=False)
            )
            data = _openai_json(system, user)
            rejected_wrong_topic = 0
            for assignment in data.get("assignments") or []:
                if not isinstance(assignment, dict):
                    continue
                cid = (assignment.get("concept_id") or "").strip()
                for tid in assignment.get("type_ids") or []:
                    tid = (tid or "").strip()
                    if tid not in remaining_in_group:
                        continue
                    allowed = allowed_cids_by_tid.get(tid)
                    if (
                        cid not in candidate_cid_set
                        or (allowed is not None and cid not in allowed)
                    ):
                        rejected_wrong_topic += 1
                        continue
                    per_concept.setdefault(cid, []).append(types_by_id[tid])
                    remaining_in_group.discard(tid)
            if rejected_wrong_topic:
                progress.log(
                    f"Rejected {rejected_wrong_topic} mined Type placement(s) "
                    "outside their source topic; retrying those Type IDs.",
                    level="warning",
                )
            placed = len(group_type_ids) - len(remaining_in_group)
            progress.log(
                f"Type embedding scope {scope_label!r} attempt {attempt}: "
                f"{placed}/{len(group_type_ids)} mined Types assigned.")
        unassigned.update(remaining_in_group)

    if unassigned:
        progress.log(
            f"{len(unassigned)} mined Type(s) unassigned after {max_attempts} "
            "attempt(s); failing instead of filing questions under the wrong "
            "concept.",
            level="error",
        )
        raise RuntimeError(
            "type embedding failed: unassigned mined Types "
            + ", ".join(sorted(unassigned))
        )

    for cid, tlist in per_concept.items():
        rec = cid_map[cid]
        fragments: list[str] = []
        counter = 0
        for mtype in tlist:
            body, counter = _mined_type_to_body(mtype, counter)
            if body:
                fragments.append(body)
        if fragments:
            rec["concept_details"] = _inject_types(
                rec.get("concept_details", ""), " ".join(fragments))
    return records


def _mined_type_topic_violations(
    records: list[dict], mined_types: dict | None,
) -> list[dict]:
    """Return missing or unexpected placements, grouped by normalized Type title."""
    violations: list[dict] = []
    expected_by_title: dict[str, list[dict]] = {}
    for mtype in (mined_types or {}).get("types") or []:
        topic = (mtype.get("topic_match_hint") or "").strip()
        title = concept_cleanup.strip_dangling_references(
            (mtype.get("type_title") or mtype.get("task_pattern") or "").strip())
        if not topic or not title:
            continue
        topic_key = _topic_comparison_key(topic)
        title_key = bi.normalize_question_text(title)
        expected_by_title.setdefault(title_key, []).append({
            "mtype": mtype,
            "title": title,
            "topic": topic,
            "topic_key": topic_key,
        })

    # Parse rendered Type headers rather than matching title text in Case/Example
    # prose. Match longest normalized titles first because the rendered header
    # may append a description after the title.
    type_header_re = re.compile(
        r"\b(?:Miscellaneous\s+)?Type\s+\d+\s*:\s*(?P<title>.*?)"
        r"(?=\s+\bCase\s+\d+\s*:)",
        re.IGNORECASE | re.DOTALL,
    )
    actual_by_title: dict[str, list[dict]] = {
        title_key: [] for title_key in expected_by_title
    }
    known_title_keys = sorted(expected_by_title, key=len, reverse=True)
    for rec in records:
        body = _types_body(rec.get("concept_details", ""))
        for match in type_header_re.finditer(body):
            rendered_key = bi.normalize_question_text(match.group("title"))
            title_key = rendered_key if rendered_key in expected_by_title else next(
                (
                    known_key for known_key in known_title_keys
                    if rendered_key.startswith(f"{known_key} ")
                ),
                "",
            )
            if title_key:
                actual_by_title[title_key].append(rec)

    for title_key, expected in expected_by_title.items():
        matches = actual_by_title[title_key]
        remaining_by_topic: dict[str, int] = {}
        for rec in matches:
            actual_key = _topic_comparison_key((rec.get("topic") or "").strip())
            remaining_by_topic[actual_key] = remaining_by_topic.get(actual_key, 0) + 1

        for entry in expected:
            mtype = entry["mtype"]
            title = entry["title"]
            topic = entry["topic"]
            topic_key = entry["topic_key"]
            if remaining_by_topic.get(topic_key, 0):
                remaining_by_topic[topic_key] -= 1
                continue

            violations.append({
                "type_id": mtype.get("type_id") or "",
                "type_title": title,
                "expected_topic": topic,
                "actual_topic": "",
                "reason": "missing",
            })

        expected_topic_keys = {entry["topic_key"] for entry in expected}
        representative = expected[0]
        for rec in matches:
            actual = (rec.get("topic") or "").strip()
            actual_key = _topic_comparison_key(actual)
            if actual_key in expected_topic_keys:
                continue
            mtype = representative["mtype"]
            title = representative["title"]
            topic = representative["topic"]
            violations.append({
                "type_id": mtype.get("type_id") or "",
                "type_title": title,
                "expected_topic": topic,
                "actual_topic": actual,
                "reason": "wrong_topic",
            })
    return violations


def _accept_topic_safe_type_review(
    original: list[dict], candidate: list[dict], mined_types: dict | None,
) -> list[dict]:
    """Reject an alignment review that moves/drops source-topic-scoped Types."""
    violations = _mined_type_topic_violations(candidate, mined_types)
    if violations:
        progress.log(
            f"Rejected Type alignment review with {len(violations)} "
            "source-topic placement violation(s); keeping constrained "
            "pre-review assignments.",
            level="warning",
        )
        return original
    return candidate


def _merge_types_from_fallback(
    records: list[dict], fallback: list[dict],
) -> list[dict]:
    """Restore Types from an earlier snapshot when a later pass dropped them."""
    fb_types = {
        _record_key(r): _types_body(r.get("concept_details", ""))
        for r in fallback
        if _has_meaningful_types(r.get("concept_details", ""))
    }
    if not fb_types:
        return records
    restored = 0
    for rec in records:
        if _has_meaningful_types(rec.get("concept_details", "")):
            continue
        body = fb_types.get(_record_key(rec))
        if body:
            rec["concept_details"] = _inject_types(rec["concept_details"], body)
            restored += 1
    if restored:
        progress.log(f"Restored Types on {restored} concept(s) from pre-pass snapshot.")
    return records


def _review_type_concept_alignment_via_api(
    records: list[dict], *, meta: dict,
    question_task_inventory: dict | None = None,
    mined_types: dict | None = None,
    source_context: str = "",
) -> list[dict]:
    """Ask GPT to verify Type/Case/Example placement against the inventory.

    This is the quality pass for the user's core issue: Types and concepts must
    go hand in hand; every source question must appear exactly once; no later
    section question should be filed under an earlier concept.
    """
    import json as _json

    if not records:
        return records
    inventory = question_task_inventory or _empty_inventory()
    if not inventory.get("items"):
        return records
    system = prompts.get_text("concepts.type_alignment_review.system")
    payload = {
        "rows": _records_to_api_rows(records),
        "question_task_inventory": inventory,
        "mined_types": mined_types or {"types": []},
    }
    user = (
        _metadata_block(meta)
        + "\nReview this concept map for Type/Case/Example placement defects:\n"
        + _json.dumps(payload, ensure_ascii=False)
    )
    if source_context:
        user += "\n\nCHAPTER SOURCE CONTEXT:\n" + _trim(source_context, 160_000)
    progress.log("Reviewing Type/concept alignment via API.")
    try:
        data = _openai_json(system, user)
    except Exception as exc:  # noqa: BLE001 — keep best output; validator follows
        progress.log(
            f"Type/concept alignment review failed ({exc}) — keeping best output.",
            level="warning",
        )
        return records
    reviewed = _concept_rows_to_records(data)
    if not reviewed:
        progress.log("Type/concept alignment review returned no rows.", level="warning")
        return records
    if len(reviewed) != len(records):
        progress.log(
            f"Type/concept alignment review returned {len(reviewed)} row(s) for "
            f"{len(records)} input row(s); merging by concept key.",
            level="warning",
        )
        candidate = _merge_repaired_rows(records, reviewed)
        return _accept_topic_safe_type_review(
            records, candidate, mined_types)
    out: list[dict] = []
    for original, updated in zip(records, reviewed):
        # Contract says only Types may move; enforce stable non-Type fields.
        updated["topic"] = original.get("topic", "")
        updated["parent_concept"] = original.get("parent_concept", "")
        updated["concept_title"] = original.get("concept_title", "")
        updated["keywords"] = original.get("keywords", "")
        new_types = _types_body(updated.get("concept_details", ""))
        if new_types:
            updated["concept_details"] = _inject_types(
                _strip_types_from_records([dict(original)])[0].get("concept_details", ""),
                new_types,
            )
        else:
            updated["concept_details"] = _strip_types_from_records([dict(original)])[0].get(
                "concept_details", "")
        out.append(updated)
    return _accept_topic_safe_type_review(records, out, mined_types)


def _ensure_parent_concepts(records: list[dict]) -> list[dict]:
    """Fill a conservative parent cluster when the model omitted one."""
    for rec in records:
        if cr.is_culmination(rec.get("concept_title", "")):
            rec.setdefault("parent_concept", "Culmination")
        elif not (rec.get("parent_concept") or "").strip():
            rec["parent_concept"] = rec.get("topic", "") or "Core Concepts"
    return records


def _strip_types_from_records(records: list[dict]) -> list[dict]:
    for rec in records:
        sections = [
            (label, content)
            for label, content in cr.split_sections(rec.get("concept_details", ""))
            if not label.lower().startswith("type")
        ]
        if sections:
            rec["concept_details"] = cr.join_sections(sections)
    return records


def _validation_options(stage: str) -> dict:
    return {
        "skeleton": {"allow_types": False, "require_culmination": False, "allow_culmination": False},
        "canonicalize": {"allow_types": False, "require_culmination": False, "allow_culmination": False},
        "description": {"allow_types": False, "require_culmination": False, "allow_culmination": False},
        # Types run AFTER the culmination pass, so culmination rows are present
        # (and may themselves receive mixed/synthesis Types).
        "types": {"allow_types": True, "require_culmination": True, "allow_culmination": True},
        "culmination": {"allow_types": True, "require_culmination": True, "allow_culmination": True},
        "final": {"allow_types": True, "require_culmination": True, "allow_culmination": True},
    }.get(stage, {"allow_types": True, "require_culmination": False, "allow_culmination": True})


def _merge_repaired_rows(records: list[dict], repaired: list[dict]) -> list[dict]:
    if len(repaired) == len(records):
        return repaired
    by_key = {_record_key(r): r for r in repaired}
    by_title = {bi.normalize_question_text(r.get("concept_title", "")): r for r in repaired}
    out: list[dict] = []
    for rec in records:
        replacement = by_key.get(_record_key(rec)) or by_title.get(
            bi.normalize_question_text(rec.get("concept_title", "")))
        out.append(replacement or rec)
    return out


_FATAL_CODES = {
    "required", "required_parent", "description_prefix", "duplicate_title",
    "duplicate_topic_concept", "source_artifact", "types_too_early",
    "culmination_too_early", "types_format", "case_without_type",
    "type_without_case", "culmination_description", "culmination_count",
    "culmination_order", "section_number", "empty_types", "short_case_example",
    "merged_description",
}


def _fatal_errors(report: dict) -> list[dict]:
    return [
        e for e in report.get("errors", [])
        if e.get("severity") == "error" and e.get("code") in _FATAL_CODES
    ]


def _repair_records_via_api(
    records: list[dict], *, meta: dict, stage: str, source_context: str = "",
    max_attempts: int = 2, strict: bool = False,
) -> list[dict]:
    """Validate rows and ask the repair prompt to fix hard failures."""
    records = _ensure_parent_concepts(records)
    opts = _validation_options(stage)
    for attempt in range(max_attempts + 1):
        report = cv.validate_concept_rows(records, **opts)
        hard = [e for e in report["errors"] if e["severity"] == "error"]
        progress.log(
            f"{stage}: validation found {len(hard)} error(s), "
            f"{report['summary'].get('warnings', 0)} warning(s).")
        if not hard:
            if report["errors"]:
                progress.log(
                    f"{stage}: validation passed with {len(report['errors'])} warning(s).")
            return records
        if attempt >= max_attempts:
            fatal = _fatal_errors(report)
            progress.log(
                f"{stage}: keeping best output with {len(hard)} validation error(s).",
                level="warning",
            )
            for e in hard[:10]:
                idx = e.get("row_index", -1)
                row = records[idx] if 0 <= idx < len(records) else {}
                progress.log(
                    f"  unrepaired [{e.get('code')}] row {idx} "
                    f"'{(row.get('concept_title') or '')[:60]}': "
                    f"{(row.get(e.get('field', '')) or '')[:120]!r}",
                    level="warning",
                )
            if strict and fatal:
                codes = ", ".join(sorted({e["code"] for e in fatal}))
                raise RuntimeError(
                    f"{stage} validation failed after repair attempts: {codes}"
                )
            return records
        import json as _json
        failed_indexes = sorted({e["row_index"] for e in hard if e["row_index"] >= 0})
        failed_rows = [records[i] for i in failed_indexes if i < len(records)]
        user = (
            _metadata_block(meta)
            + f"\nStage: {stage}\nValidation errors:\n"
            + _json.dumps(hard, ensure_ascii=False)
            + "\nFailed rows:\n"
            + _json.dumps({"rows": _records_to_api_rows(failed_rows)}, ensure_ascii=False)
        )
        if source_context:
            user += "\nRelevant source context:\n" + _trim(source_context, 120_000)
        data = _openai_json(prompts.get_text("concepts.repair.system"), user)
        repaired = _concept_rows_to_records(data)
        if not repaired:
            progress.log(f"{stage}: repair attempt returned no rows.", level="warning")
            return records
        if len(repaired) == len(failed_indexes):
            next_records = list(records)
            for idx, repaired_row in zip(failed_indexes, repaired):
                if idx < len(next_records):
                    next_records[idx] = repaired_row
            records = next_records
        else:
            records = _merge_repaired_rows(records, repaired)
        records = _ensure_parent_concepts(records)
        progress.log(f"{stage}: repaired {len(repaired)} row(s) on attempt {attempt + 1}.")
    return records


def _artifact_match_snippet(rec: dict) -> str:
    """Return the first source_artifact substring in a row (for diagnostics)."""
    row_text = " ".join([
        str(rec.get("topic") or ""),
        str(rec.get("parent_concept") or ""),
        str(rec.get("concept_title") or ""),
        str(rec.get("concept_details") or ""),
    ])
    details = str(rec.get("concept_details") or "")
    artifact_re = (
        cv._SOURCE_ARTIFACT_NO_FIG_RE if cv._IMAGE_URL_RE.search(details)
        else cv._SOURCE_ARTIFACT_RE
    )
    m = artifact_re.search(row_text)
    return m.group(0) if m else ""


def _neutralize_unrepaired_rows(records: list[dict]) -> list[dict]:
    """Destructively neutralize source artifacts ONLY where GPT repair failed.

    Rows that validate cleanly keep their GPT-authored wording verbatim; the
    deterministic rewriter is a per-row last resort, never a blanket pass.
    Re-runs once if a cleaned row still matches (regex drift / OCR forms).
    """
    report = cv.validate_concept_rows(
        records, allow_types=True, require_culmination=True, allow_culmination=True)
    failing = {
        e["row_index"] for e in report["errors"]
        if e.get("severity") == "error" and e.get("row_index", -1) >= 0
        and e.get("code") == "source_artifact"
    }
    out: list[dict] = []
    for i, rec in enumerate(records):
        out.append(concept_cleanup.clean_concept_record(
            dict(rec), neutralize_artifacts=i in failing))
    if failing:
        snippets = []
        for idx in sorted(failing)[:5]:
            if 0 <= idx < len(records):
                snip = _artifact_match_snippet(records[idx])
                if snip:
                    snippets.append(snip)
        progress.log(
            f"Neutralized source artifacts on {len(failing)} unrepaired row(s); "
            f"{len(records) - len(failing)} row(s) kept verbatim"
            + (f" (matched: {', '.join(repr(s) for s in snippets)})" if snippets else "")
            + ".",
            level="warning",
        )
        # Second pass: any row that STILL fails after neutralize (regex gap)
        # gets cleaned again so final validation is not blocked.
        again = cv.validate_concept_rows(
            out, allow_types=True, require_culmination=True, allow_culmination=True)
        still = {
            e["row_index"] for e in again["errors"]
            if e.get("severity") == "error" and e.get("row_index", -1) >= 0
            and e.get("code") == "source_artifact"
        }
        if still:
            for idx in still:
                if 0 <= idx < len(out):
                    snip = _artifact_match_snippet(out[idx])
                    out[idx] = concept_cleanup.clean_concept_record(
                        dict(out[idx]), neutralize_artifacts=True)
                    progress.log(
                        f"  re-scrubbed row {idx} after neutralize residual"
                        + (f" ({snip!r})" if snip else "") + ".",
                        level="warning",
                    )
    return out


_TYPE_SPLIT_RE = re.compile(r"(?=\b(?:Miscellaneous\s+)?Type\s+\d{1,2}:)", re.IGNORECASE)
_CASE_SPLIT_RE = re.compile(r"(?=\bCase\s+\d{1,2}:)", re.IGNORECASE)
_EXAMPLE_LINE_RE = re.compile(r"\bExamples?\s*:\s*", re.IGNORECASE)


def _inventory_lookup_texts(inventory: dict | None) -> list[str]:
    """Full teacher-facing task texts from the Question / Task Inventory."""
    out: list[str] = []
    seen: set[str] = set()
    for item in (inventory or {}).get("items") or []:
        if not isinstance(item, dict):
            continue
        text = _inventory_task_text(item)
        key = bi.normalize_question_text(text)
        if text and key and key not in seen:
            seen.add(key)
            out.append(text)
    return out


def _match_inventory_for_short_example(
    stub: str, inventory_texts: list[str], *, used: set[str],
    context: str = "",
) -> str:
    """Best full inventory question for a truncated Example stub.

    ``context`` may carry the Case title / concept title so short stubs like
    "Describe the print." can still match a Germania / allegory inventory item.
    """
    stub_key = bi.normalize_question_text(stub)
    context_key = bi.normalize_question_text(context)
    if not stub_key and not context_key:
        return ""
    stub_tokens = {t for t in stub_key.split() if len(t) > 2}
    context_tokens = {t for t in context_key.split() if len(t) > 2}
    # Prefer inventory items that already contain / start with the stub.
    candidates: list[tuple[int, int, str, str]] = []
    for text in inventory_texts:
        key = bi.normalize_question_text(text)
        if key in used:
            continue
        if stub_key and (stub_key in key or key.startswith(stub_key)):
            used.add(key)
            return text
        text_tokens = set(key.split())
        stub_overlap = len(stub_tokens & text_tokens) if stub_tokens else 0
        ctx_overlap = len(context_tokens & text_tokens) if context_tokens else 0
        if stub_overlap or ctx_overlap:
            candidates.append((stub_overlap, ctx_overlap, key, text))
    # Pure placeholder stubs ("q") with no useful context cannot be matched.
    if len(stub_key.split()) <= 1 and not context_tokens:
        return ""
    if not candidates:
        return ""
    candidates.sort(key=lambda x: (-x[0], -x[1], -len(x[2])))
    best_stub, best_ctx, best_key, best_text = candidates[0]
    # Accept when the stub itself overlaps enough, OR when context strongly
    # points at one unused inventory question (History allegory / source tasks).
    if best_stub >= max(2, (len(stub_tokens) + 1) // 2 if stub_tokens else 2):
        used.add(best_key)
        return best_text
    if best_ctx >= 2 and (best_stub >= 1 or len(stub_key.split()) <= 3):
        used.add(best_key)
        return best_text
    return ""


def _rebuild_types_body(
    types: list[tuple[str, list[tuple[str, list[str]]]]],
) -> str:
    """Render ``[(type_header, [(case_title, [examples])])...]`` to Types body."""
    parts: list[str] = []
    for type_header, cases in types:
        if not cases:
            continue
        parts.append(type_header.strip())
        for case_i, (case_title, examples) in enumerate(cases, start=1):
            parts.append(f"Case {case_i:02d}: {case_title.strip()}")
            for example in examples:
                parts.append(f"Example: {example.strip()}")
    # Preserve original Type NN labels from headers; only Case indexes restart.
    return " ".join(parts)


def _salvage_short_case_examples(
    records: list[dict], *, inventory: dict | None = None,
) -> list[dict]:
    """Expand truncated Case Examples from inventory; drop irrecoverable stubs.

    GPT repair sometimes leaves ``Example: q`` / one-word stubs that hard-fail
    final validation. Prefer inventory wording; if none matches, drop the stub
    Example (and empty Cases) so the chapter can still deposit.
    """
    inventory_texts = _inventory_lookup_texts(inventory)
    used: set[str] = set()
    expanded = 0
    dropped = 0
    out: list[dict] = []
    for rec in records:
        rec = dict(rec)
        details = rec.get("concept_details") or ""
        sections = cr.split_sections(details)
        types_idx = next(
            (i for i, (label, _) in enumerate(sections)
             if label.strip().lower().startswith("type")),
            -1,
        )
        if types_idx < 0:
            out.append(rec)
            continue
        label, body = sections[types_idx]
        type_chunks = [c.strip() for c in _TYPE_SPLIT_RE.split(body) if c.strip()]
        rebuilt_types: list[tuple[str, list[tuple[str, list[str]]]]] = []
        changed = False
        for chunk in type_chunks:
            case_parts = [p.strip() for p in _CASE_SPLIT_RE.split(chunk) if p.strip()]
            if not case_parts:
                continue
            type_header = case_parts[0]
            # If the first piece is itself a Case, there is no Type header.
            if re.match(r"^Case\s+\d{1,2}:", type_header, re.IGNORECASE):
                type_header = "Type 01: Assessment pattern"
                case_parts = case_parts
            else:
                case_parts = case_parts[1:]
            cases: list[tuple[str, list[str]]] = []
            for case_chunk in case_parts:
                m = re.match(
                    r"^Case\s+\d{1,2}:\s*(.*)$", case_chunk, re.IGNORECASE | re.DOTALL)
                case_body = (m.group(1) if m else case_chunk).strip()
                pieces = _EXAMPLE_LINE_RE.split(case_body)
                case_title = (pieces[0] or "").strip() or "Source-based question"
                examples = [p.strip() for p in pieces[1:] if p.strip()]
                match_context = " ".join([
                    case_title,
                    rec.get("concept_title") or "",
                    rec.get("parent_concept") or "",
                    rec.get("topic") or "",
                ])
                # Legacy: question lived in the Case line with no Example: label.
                if not examples and case_title:
                    if cv._example_too_short(case_title):
                        replacement = _match_inventory_for_short_example(
                            case_title, inventory_texts, used=used,
                            context=match_context,
                        )
                        if replacement:
                            examples = [replacement]
                            case_title = (
                                rec.get("concept_title") or "Source-based question"
                            )
                            expanded += 1
                            changed = True
                        else:
                            dropped += 1
                            changed = True
                            continue
                    else:
                        examples = [case_title]
                        case_title = (
                            rec.get("concept_title") or "Source-based question"
                        )
                        changed = True
                new_examples: list[str] = []
                for ex in examples:
                    needs_source = (
                        cv._example_too_short(ex)
                        or bool(_CASE_SOURCE_ARTIFACT_RE.search(ex or ""))
                    )
                    if not needs_source:
                        new_examples.append(ex)
                        used.add(bi.normalize_question_text(ex))
                        continue
                    replacement = _match_inventory_for_short_example(
                        ex, inventory_texts, used=used, context=match_context,
                    )
                    if replacement:
                        new_examples.append(replacement)
                        expanded += 1
                        changed = True
                    elif cv._example_too_short(ex):
                        dropped += 1
                        changed = True
                    else:
                        # Keep longer artifact-bearing text for neutralize/repair.
                        new_examples.append(ex)
                        used.add(bi.normalize_question_text(ex))
                if new_examples:
                    cases.append((case_title, new_examples))
                elif case_title and not cv._example_too_short(case_title):
                    # Keep a defined Case with no Example only when the Case
                    # title itself is already a full question (legacy form).
                    cases.append((case_title, []))
            if cases:
                # Keep the original Type NN: title from the header line.
                header = type_header.split("Case", 1)[0].strip()
                if not re.match(
                        r"^(?:Miscellaneous\s+)?Type\s+\d{1,2}:", header, re.IGNORECASE):
                    header = f"Type 01: {header}" if header else "Type 01: Assessment pattern"
                rebuilt_types.append((header, cases))
        if changed:
            new_body = _rebuild_types_body(rebuilt_types)
            if new_body.strip():
                sections[types_idx] = (label, new_body)
            else:
                sections.pop(types_idx)
            rec["concept_details"] = cr.join_sections(sections)
        out.append(rec)
    if expanded or dropped:
        progress.log(
            f"Short Case Example salvage: expanded {expanded} from inventory, "
            f"dropped {dropped} irrecoverable stub(s).",
            level="warning" if dropped else "success",
        )
    return out


def _validate_final_or_raise(records: list[dict], *, stage: str = "final") -> dict:
    report = cv.validate_concept_rows(
        records, allow_types=True, require_culmination=True, allow_culmination=True)
    fatal = _fatal_errors(report)
    progress.log(
        f"{stage}: final validation found {len(fatal)} fatal error(s), "
        f"{report['summary'].get('warnings', 0)} warning(s).")
    if fatal:
        codes = ", ".join(sorted({e["code"] for e in fatal}))
        raise RuntimeError(f"{stage} validation failed: {codes}")
    return report


def _refine_descriptions_via_api(
    records: list[dict], *, subject: str, mmd_text: str = "",
    meta: dict | None = None, sections: list[dict] | None = None,
) -> list[dict]:
    """Dedicated Description-only API pass for source-grounded concept details."""
    import json as _json

    if not records:
        return records
    if not (mmd_text or "").strip():
        progress.log("Description refinement skipped — no chapter source text.", level="warning")
        return records

    meta = meta or _metadata(subject=subject)
    sections = sections or parse_mmd_sections(mmd_text)
    system = _description_refine_system(subject)
    progress.log(f"Refining descriptions for {len(records)} concepts (dedicated API pass).")
    topics: dict[str, list[dict]] = {}
    for rec in records:
        topics.setdefault(rec.get("topic", ""), []).append(rec)
    refined_rows: list[dict] = []
    for topic, topic_records in topics.items():
        payload = _json.dumps({"rows": _records_to_api_rows(topic_records)}, ensure_ascii=False)
        source = _source_for_topic(topic, sections)
        user = (
            _metadata_block(meta)
            + f"\nTopic: {topic}\nConcept map — refine Description only:\n"
            + payload
            + "\n\nRELEVANT SOURCE TEXT:\n"
            + _trim(source, 220_000)
        )
        data = _openai_json(system, user)
        refined_rows.extend(_concept_rows_to_records(data))
    if not refined_rows:
        raise RuntimeError("description refinement returned no rows")

    by_key = {_record_key(r): r for r in refined_rows}
    merged: list[dict] = []
    for rec in records:
        updated = by_key.get(_record_key(rec))
        if not updated:
            merged.append(rec)
            continue
        # Description pass must not preserve or introduce Types in the new architecture.
        updated = _strip_types_from_records([updated])[0]
        updated["topic"] = rec.get("topic", "")
        updated["parent_concept"] = rec.get("parent_concept", "")
        updated["concept_title"] = rec.get("concept_title", "")
        updated["keywords"] = rec.get("keywords", "")
        updated["source_evidence"] = rec.get("source_evidence", "")
        merged.append(updated)

    before_repair = merged
    merged = _repair_records_via_api(
        merged, meta=meta, stage="description", source_context=mmd_text)
    merged = _preserve_required_method_rows(before_repair, merged)
    progress.log(f"Descriptions refined: {len(merged)}.", level="success")
    return merged


def _assign_types_via_api(
    records: list[dict], *, subject: str, mmd_text: str = "",
    meta: dict | None = None, sections: list[dict] | None = None,
    question_task_inventory: dict | None = None,
    mined_types: dict | None = None,
) -> list[dict]:
    """Dedicated Types-only API pass — mirrors manual types-first workflow.

    When mined Types are available they are embedded via a pure-API ID
    assignment (``_assign_mined_types_via_api``): the model maps each mined
    ``type_id`` to a concept and we join by exact IDs, guaranteeing every
    refined Type is embedded without any regex/word matching. When no mined
    Types exist (e.g. no source/inventory), the model authors Types per topic.
    """
    import json as _json

    if not records:
        return records
    meta = meta or _metadata(subject=subject)
    mined_types = mined_types or {"types": []}
    if mined_types.get("types"):
        progress.log(
            f"Embedding {len(mined_types['types'])} mined Types into concepts "
            "via API ID assignment.")
        merged = _assign_mined_types_via_api(records, meta=meta, mined_types=mined_types)
        merged = _review_type_concept_alignment_via_api(
            merged,
            meta=meta,
            question_task_inventory=question_task_inventory,
            mined_types=mined_types,
            source_context=mmd_text,
        )
        before_repair = merged
        repaired = _repair_records_via_api(
            merged, meta=meta, stage="types", source_context=mmd_text)
        merged = _accept_topic_safe_type_review(
            before_repair, repaired, mined_types)
        with_types = sum(1 for r in merged if _has_meaningful_types(r.get("concept_details", "")))
        progress.log(
            f"Types assignment complete: {with_types}/{len(merged)} concepts have Types.",
            level="success" if with_types else "warning",
        )
        return merged
    if not (mmd_text or "").strip():
        progress.log("Types assignment skipped — no chapter source text.", level="warning")
        return records
    sections = sections or parse_mmd_sections(mmd_text)
    question_task_inventory = question_task_inventory or _empty_inventory()
    system = _types_assign_system(subject)
    progress.log(f"Assigning Types to {len(records)} concepts (dedicated API pass).")
    topics: dict[str, list[dict]] = {}
    for rec in records:
        topics.setdefault(rec.get("topic", ""), []).append(rec)
    out: list[dict] = []
    for topic, topic_records in topics.items():
        payload = _json.dumps({"rows": _records_to_api_rows(topic_records)}, ensure_ascii=False)
        source = _source_for_topic(topic, sections)
        user = (
            _metadata_block(meta)
            + f"\nTopic: {topic}\nConcept map — add Types to assessable concepts:\n"
            + payload
            + "\n\nQUESTION / TASK INVENTORY (debug trace, do not copy source labels into concept_details):\n"
            + _json.dumps(question_task_inventory, ensure_ascii=False)
            + "\n\nMINED REUSABLE TYPES TO EMBED:\n"
            + _json.dumps(mined_types, ensure_ascii=False)
            + "\n\nRELEVANT TOPIC SOURCE + EXERCISE BLOCKS:\n"
            + _trim(source, 220_000)
        )
        data = _openai_json(system, user)
        out.extend(_concept_rows_to_records(data))
    if not out:
        raise RuntimeError("Types assignment returned no rows")
    # Match by key; keep original row if API omitted it.
    by_key = {_record_key(r): r for r in out}
    merged: list[dict] = []
    for rec in records:
        updated = by_key.get(_record_key(rec))
        if not updated:
            merged.append(rec)
            continue
        # This pass is Types-only: keep the refined Description and any existing
        # useful Misconception from the incoming record, and take only the Types
        # body returned by the API.
        types_body = _types_body(updated.get("concept_details", ""))
        if types_body:
            rec = dict(rec)
            rec["concept_details"] = _inject_types(rec.get("concept_details", ""), types_body)
        merged.append(rec)
    merged = _repair_records_via_api(merged, meta=meta, stage="types", source_context=mmd_text)
    merged = _review_type_concept_alignment_via_api(
        merged,
        meta=meta,
        question_task_inventory=question_task_inventory,
        mined_types=mined_types,
        source_context=mmd_text,
    )
    merged = _repair_records_via_api(merged, meta=meta, stage="types", source_context=mmd_text)
    with_types = sum(1 for r in merged if _has_meaningful_types(r.get("concept_details", "")))
    progress.log(
        f"Types assignment complete: {with_types}/{len(merged)} concepts have Types.",
        level="success" if with_types else "warning",
    )
    if with_types < len(merged) // 2:
        progress.log(
            "Fewer than half the concepts have Types — check chapter source or "
            "raise AEGIS_OPENAI_MAX_OUTPUT_TOKENS.",
            level="warning",
        )
    return merged


def _records_to_api_rows(records: list[dict]) -> list[dict]:
    """Serialize concept records for a consolidation API call."""
    return [
        {
            "topic": rec.get("topic", ""),
            "parent_concept": rec.get("parent_concept", ""),
            "concept": rec.get("concept_title", ""),
            "concept_description": rec.get("concept_details", ""),
            "keywords": rec.get("keywords", ""),
            **({"source_evidence": rec.get("source_evidence", "")}
               if rec.get("source_evidence") else {}),
        }
        for rec in records
    ]


_CANONICALIZE_MIN_CHAPTER_ROWS = 4
_CANONICALIZE_MIN_PER_TOPIC = 2
_CANONICALIZE_MAX_PER_TOPIC = 6
_CANONICALIZE_MAX_CHAPTER_ROWS = 50


def _canonicalize_target_bounds(records: list[dict]) -> tuple[int, int]:
    """Return compact-but-not-collapsed row-count bounds for a chapter map."""
    if not records:
        return 0, 0
    topics = {
        bi.normalize_question_text(r.get("topic", ""))
        for r in records
        if (r.get("topic") or "").strip()
    }
    topic_count = max(1, len(topics))
    min_keep = max(
        _CANONICALIZE_MIN_CHAPTER_ROWS,
        topic_count * _CANONICALIZE_MIN_PER_TOPIC,
    )
    max_keep = max(
        12,
        min(
            _CANONICALIZE_MAX_CHAPTER_ROWS,
            topic_count * _CANONICALIZE_MAX_PER_TOPIC,
        ),
    )
    max_keep = min(len(records), max_keep)
    min_keep = min(len(records), min_keep)
    if min_keep > max_keep:
        min_keep = max(1, max_keep)
    return min_keep, max_keep


def _consolidate_concepts_via_api(
    records: list[dict], *, subject: str, mmd_text: str = "",
    meta: dict | None = None,
) -> list[dict]:
    """Chapter-wide skeleton refinement: compact, dedup, name, parent-group.

    The input comes from section chunks and can contain many term/example/case
    fragments. Canonicalization is expected to merge those into durable
    teaching concepts while staying above a minimum count per main topic.
    """
    import json as _json

    if not records:
        return records
    meta = meta or _metadata(subject=subject)
    system = prompts.get_text("concepts.canonicalize.system")
    payload = _json.dumps({"rows": _records_to_api_rows(records)}, ensure_ascii=False)
    user = (
        _metadata_block(meta)
        + f"\nDraft skeleton map ({len(records)} rows):\n"
        + payload
    )
    progress.log(f"Canonicalizing {len(records)} skeleton concepts via API pass.")
    data = _openai_json(system, user)
    out = _concept_rows_to_records(data)
    min_keep, max_keep = _canonicalize_target_bounds(records)
    if out and len(out) < min_keep:
        progress.log(
            f"Canonicalization returned {len(out)} rows for {len(records)} "
            f"input rows (target {min_keep}-{max_keep}) — over-merging "
            "detected, retrying.",
            level="warning",
        )
        retry_user = (
            user
            + f"\n\nYOUR PREVIOUS ANSWER KEPT ONLY {len(out)} OF {len(records)} ROWS — "
            "that is over-merging. Keep the main teaching objectives for every "
            "topic, but still merge duplicates, examples, cases, and narrow "
            f"fragments. Return roughly {min_keep}-{max_keep} rows."
        )
        retry_data = _openai_json(system, retry_user)
        retry_out = _concept_rows_to_records(retry_data)
        if len(retry_out) > len(out):
            out = retry_out
    elif out and len(out) > max_keep:
        progress.log(
            f"Canonicalization kept {len(out)} rows for {len(records)} input "
            f"rows (target {min_keep}-{max_keep}) — still too granular, "
            "retrying with a compaction instruction.",
            level="warning",
        )
        retry_user = (
            user
            + f"\n\nYOUR PREVIOUS ANSWER KEPT {len(out)} ROWS, WHICH IS TOO "
            "GRANULAR FOR A TEACHER-FACING CHAPTER MAP. Merge repeated terms, "
            "sub-types, examples, cases, and exercise-question headings into "
            "their parent teaching concepts. Preserve all main objectives and "
            f"topic order. Return at most {max_keep} rows and at least "
            f"{min_keep} rows."
        )
        retry_data = _openai_json(system, retry_user)
        retry_out = _concept_rows_to_records(retry_data)
        if retry_out and min_keep <= len(retry_out) < len(out):
            out = retry_out
    if not out:
        raise RuntimeError("concept consolidation returned no rows")
    if len(out) < min_keep:
        progress.log(
            f"Canonicalization still over-merged ({len(out)}/{len(records)} rows) — "
            "keeping the full de-duplicated skeleton instead.",
            level="warning",
        )
        out = [dict(r) for r in records]
    elif len(out) > max_keep:
        progress.log(
            f"Canonicalization remained above target ({len(out)}/{max_keep} rows); "
            "keeping the most compact API output for downstream refinement.",
            level="warning",
        )
    out = _preserve_required_method_rows(records, out)
    out = _strip_types_from_records(_ensure_parent_concepts(out))
    out = _dedupe_titles_chapter_wide(out)
    before_repair = out
    out = _repair_records_via_api(
        out, meta=meta, stage="canonicalize")
    out = _preserve_required_method_rows(before_repair, out)
    out = _dedupe_titles_chapter_wide(out)
    progress.log(f"Rows after canonicalization: {len(out)}.", level="success")
    return out


_DESCRIPTION_PREFIX_RE = re.compile(r"^\s*description\s*[:：]\s*", re.IGNORECASE)


def _normalize_description_prefix(details: str) -> str:
    """Deterministically enforce the required "Description:" prefix.

    Models routinely drift on this exact formatting (lowercase, fullwidth
    colon, missing prefix), and repeated API repair attempts often recreate
    the same drift — normalizing here fixes it once for every stage.
    """
    details = (details or "").strip()
    if not details or details.startswith("Description:"):
        return details
    m = _DESCRIPTION_PREFIX_RE.match(details)
    if m:
        return "Description: " + details[m.end():].strip()
    if details.startswith(("Type ", "Types:", "Case ")):
        return details  # Types-only content is handled by Types validation.
    return "Description: " + details


def _concept_rows_to_records(data: dict) -> list[dict]:
    out: list[dict] = []
    for row in data.get("rows", []):
        title = (row.get("concept") or "").strip()
        if not title:
            continue
        out.append({
            "topic": (row.get("topic") or "General").strip(),
            "parent_concept": (row.get("parent_concept") or "").strip(),
            "concept_title": title,
            "concept_details": _normalize_description_prefix(
                row.get("concept_description") or ""),
            "keywords": (row.get("keywords") or "").strip(),
            "source_evidence": (row.get("source_evidence") or "").strip(),
        })
    return out


def _merge_concept_records(records: list[dict]) -> list[dict]:
    """De-duplicate by topic/title, preferring mandatory anchor-tagged rows."""
    seen: dict[tuple[str, str], int] = {}
    out: list[dict] = []
    for rec in records:
        key = (_topic_comparison_key(rec.get("topic", "")),
               bi.normalize_question_text(rec["concept_title"]))
        if key in seen:
            kept_index = seen[key]
            if (
                _method_anchor_ids(rec)
                and not _method_anchor_ids(out[kept_index])
            ):
                out[kept_index] = rec
            continue
        seen[key] = len(out)
        out.append(rec)
    return out


def _dedupe_titles_chapter_wide(records: list[dict]) -> list[dict]:
    """Keep the FIRST row for each normalized concept title, chapter-wide.

    The validator requires every concept to appear exactly once per chapter,
    but chunked extraction occasionally restates the same concept under two
    different topics, and the LLM repair pass cannot merge rows — it can only
    rewrite them. The duplicate is therefore dropped mechanically (the first
    statement of a concept is its teaching home) so a whole finished chapter
    never fails final validation on a duplicate title.
    """
    seen: dict[str, int] = {}
    out: list[dict] = []
    dropped = 0
    for rec in records:
        key = bi.normalize_question_text(rec.get("concept_title", ""))
        if key and key in seen:
            kept_index = seen[key]
            if (
                _method_anchor_ids(rec)
                and not _method_anchor_ids(out[kept_index])
            ):
                out[kept_index] = rec
            dropped += 1
            continue
        if key:
            seen[key] = len(out)
        out.append(rec)
    if dropped:
        progress.log(
            f"Dropped {dropped} duplicate concept-title row(s) chapter-wide.",
            level="warning",
        )
    return out


def _expected_min_skeleton_rows(chunk_text: str) -> int:
    """Minimum plausible concept count for a chunk, from its content size.

    Roughly one teachable concept per ~2,500 chars of source, floored at 2 for
    any substantial chunk. Deliberately conservative — this only flags clear
    under-extraction (e.g. a whole chapter collapsed into a handful of rows).
    Slightly denser than earlier so History-style chapters keep discrete
    country/case concepts instead of one umbrella row per topic.
    """
    content = len((chunk_text or "").strip())
    if content < 2_000:
        return 1
    return max(2, min(28, content // 2_500))


def _expected_max_skeleton_rows(chunk_text: str, headings: list[str]) -> int:
    """Maximum useful skeleton density before a chunk is clearly micro-split."""
    content = len((chunk_text or "").strip())
    heading_count = max(1, len(headings or []))
    by_headings = heading_count * 4
    by_size = max(8, content // 900) if content >= 2_000 else 8
    return max(8, min(45, max(by_headings, by_size)))


_METHOD_CUE_RE = re.compile(
    r"\b(?:deriv(?:e|ed|ation|ing)|proof|prove|method|procedure|algorithm|"
    r"same technique|general form|general term|looking at the pattern|"
    r"rewrit(?:e|ing).*reverse order|on adding|formula|is given by)\b",
    re.IGNORECASE,
)
_MATH_EXPRESSION_RE = re.compile(
    r"\$\$(.+?)\$\$|\$(.+?)\$", re.DOTALL,
)
_METHOD_ANCHOR_ID_RE = re.compile(r"\bMETHOD-[A-F0-9]{10}\b")
_METHOD_EVIDENCE_STOPWORDS = {
    "about", "after", "again", "all", "also", "and", "any", "are", "before",
    "being", "between", "build", "can", "chapter", "could", "derived",
    "derivation", "derive", "does", "every", "formula", "from", "general",
    "given", "gives", "had", "has", "have", "how", "into", "its", "may",
    "method", "methods", "more", "most", "must", "not", "one", "only", "our",
    "procedure", "proof", "rule", "same", "should", "some", "such", "technique",
    "than", "that", "the", "their", "them", "then", "there", "these", "they",
    "this", "those", "through", "two", "using", "was", "were", "what", "when",
    "where", "which", "while", "why", "will", "with", "would", "your",
}


def _normalize_math_evidence(text: str) -> str:
    """Canonicalize a compact formula enough for source/output comparison."""
    out = (text or "").lower().translate(str.maketrans({"−": "-", "–": "-"}))
    for _ in range(4):
        out = re.sub(
            r"\\frac\s*\{([^{}]+)\}\s*\{([^{}]+)\}",
            r"(\1)/(\2)",
            out,
        )
        out = re.sub(
            r"\\(?:mathbf|boldsymbol|mathrm|text)\s*\{([^{}]*)\}",
            r"\1",
            out,
        )
    out = re.sub(r"\\(?:begin|end)\s*\{[^{}]+\}", "", out)
    out = re.sub(r"\\(?:left|right|quad|qquad|,|;|!|:)", "", out)
    out = out.replace("{", "").replace("}", "").replace("_", "")
    return re.sub(r"\s+", "", out)


def _method_evidence_terms(text: str, *, topic: str = "") -> list[str]:
    """Ordered, distinctive prose terms used for formula-less method coverage."""
    topic_terms = set(_topic_comparison_key(topic).split())
    out: list[str] = []
    for term in _topic_comparison_key(text).split():
        if (
            len(term) < 3
            or term.isdigit()
            or term in topic_terms
            or term in _METHOD_EVIDENCE_STOPWORDS
            or term in out
        ):
            continue
        out.append(term)
    return out


def _method_coverage_anchors(sections: list[dict]) -> list[dict]:
    """Find explicit source derivation/method blocks that must be concepts."""
    import hashlib

    anchors: list[dict] = []
    seen: set[str] = set()
    for topic, section in _sections_with_source_topics(sections):
        heading = section.get("heading") or ""
        if _EXERCISE_RE.search(heading) or _is_non_topic_heading(heading):
            continue
        body = section.get("body") or ""
        example = _WORKED_EXAMPLE_START_RE.search(body)
        teaching_text = body[:example.start()] if example else body
        searchable = f"{heading}\n{teaching_text}"
        cue = _METHOD_CUE_RE.search(searchable)
        if cue is None:
            continue
        formulas = []
        for match in _MATH_EXPRESSION_RE.finditer(searchable):
            formula = (match.group(1) or match.group(2) or "").strip()
            normalized = _normalize_math_evidence(formula)
            if "=" not in normalized or not re.search(r"[a-z]", normalized):
                continue
            if normalized not in {
                    _normalize_math_evidence(existing) for existing in formulas}:
                formulas.append(formula)
        has_method_word = bool(re.search(
            r"\b(?:deriv|proof|prove|method|procedure|algorithm|technique)\w*\b",
            searchable, re.IGNORECASE))
        if not formulas and not has_method_word:
            continue
        start = max(0, cue.start() - 180)
        evidence = re.sub(
            r"\s+", " ", searchable[start:cue.start() + 1_000]).strip()
        cue_context = searchable[
            max(0, cue.start() - 160):cue.end() + 400
        ]
        # The same source block can inherit a different topic when viewed in a
        # section chunk versus the full chapter. Identity must not depend on
        # that chunk-local context; the full-chapter topic is enforced later.
        digest = hashlib.sha1(
            f"{heading}|{evidence}|{formulas}".encode("utf-8")
        ).hexdigest()[:10].upper()
        anchor_id = f"METHOD-{digest}"
        if anchor_id in seen:
            continue
        seen.add(anchor_id)
        anchors.append({
            "anchor_id": anchor_id,
            "topic_hint": topic,
            "kind": "derivation_or_method",
            "source_evidence": evidence[:1_200],
            "required_formulas": formulas[-3:],
            "evidence_terms": _method_evidence_terms(
                cue_context, topic=topic)[:24],
        })
    return anchors


def _method_anchor_ids(rec: dict) -> set[str]:
    return set(_METHOD_ANCHOR_ID_RE.findall(
        str(rec.get("source_evidence") or "").upper()))


def _method_anchor_match_priority(rec: dict, anchor: dict) -> int:
    """Rank exact-tag, formula, then prose coverage within the source topic."""
    anchor_id = (anchor.get("anchor_id") or "").upper()
    topic_hint = anchor.get("topic_hint", "")
    topic_key = _topic_comparison_key(topic_hint)
    if _topic_comparison_key(rec.get("topic", "")) != topic_key:
        return 0
    if anchor_id and anchor_id in _method_anchor_ids(rec):
        return 3
    formulae = [
        _normalize_math_evidence(formula)
        for formula in anchor.get("required_formulas") or []
        if len(_normalize_math_evidence(formula)) >= 8
    ]
    evidence_terms = _method_evidence_terms(
        " ".join(str(term) for term in anchor.get("evidence_terms") or []),
        topic=topic_hint,
    )
    if not evidence_terms:
        evidence_terms = _method_evidence_terms(
            anchor.get("source_evidence", ""), topic=topic_hint)
    record_text = " ".join([
        str(rec.get("concept_title") or ""),
        str(rec.get("concept_details") or ""),
        str(rec.get("source_evidence") or ""),
    ])
    if formulae and any(
            formula in _normalize_math_evidence(record_text)
            for formula in formulae):
        return 2
    if not formulae and evidence_terms:
        record_terms = set(
            _method_evidence_terms(record_text, topic=topic_hint))
        overlap = record_terms.intersection(evidence_terms)
        required_overlap = 1 if len(evidence_terms) == 1 else 2
        if len(overlap) >= required_overlap:
            return 1
    return 0


def _method_anchor_covered(records: list[dict], anchor: dict) -> bool:
    return any(_method_anchor_match_priority(rec, anchor) for rec in records)


def _missing_method_anchors(
    records: list[dict], anchors: list[dict],
) -> list[dict]:
    return [
        anchor for anchor in anchors
        if not _method_anchor_covered(records, anchor)
    ]


def _method_anchor_tagged_in_topic(
    records: list[dict], anchor_id: str, topic: str,
) -> bool:
    """Whether an exact METHOD tag survives in its authoritative source topic."""
    anchor_id = (anchor_id or "").upper()
    topic_key = _topic_comparison_key(topic)
    return any(
        anchor_id in _method_anchor_ids(rec)
        and _topic_comparison_key(rec.get("topic", "")) == topic_key
        for rec in records
    )


def _method_row_quality(rec: dict, *, source_topic: str) -> tuple:
    """Prefer source-topic rows with the richest post-description content."""
    details = rec.get("concept_details", "")
    return (
        _topic_comparison_key(rec.get("topic", ""))
        == _topic_comparison_key(source_topic),
        _has_meaningful_types(details),
        _has_mastery_line(details),
        bool(_misconception_body(details)),
        len(details),
        len(rec.get("source_evidence", "")),
    )


def _snapshot_method_anchor_rows(
    records: list[dict], anchors: list[dict] | None = None,
) -> dict[tuple[str, str], dict]:
    """Deep-copy the best tagged row for every ``(METHOD ID, source topic)``.

    The snapshot is intentionally independent of the mutable records that flow
    through later Type, repair, cleanup, and dedupe passes.
    """
    requested: list[tuple[str, str]] = []
    if anchors is None:
        seen: set[tuple[str, str]] = set()
        for rec in records:
            topic = (rec.get("topic") or "").strip()
            topic_key = _topic_comparison_key(topic)
            for anchor_id in sorted(_method_anchor_ids(rec)):
                key = (anchor_id, topic_key)
                if key not in seen:
                    seen.add(key)
                    requested.append((anchor_id, topic))
    else:
        requested = [
            (
                str(anchor.get("anchor_id") or "").upper(),
                (anchor.get("topic_hint") or "").strip(),
            )
            for anchor in anchors
            if str(anchor.get("anchor_id") or "").strip()
        ]

    snapshot: dict[tuple[str, str], dict] = {}
    for anchor_id, source_topic in requested:
        candidates = [
            rec for rec in records
            if anchor_id in _method_anchor_ids(rec)
            and not cr.is_culmination(rec.get("concept_title", ""))
        ]
        if not candidates:
            continue
        best = max(
            candidates,
            key=lambda rec: _method_row_quality(
                rec, source_topic=source_topic or rec.get("topic", "")),
        )
        authoritative_topic = source_topic or (best.get("topic") or "").strip()
        saved = copy.deepcopy(best)
        saved["topic"] = authoritative_topic
        snapshot[(anchor_id, _topic_comparison_key(authoritative_topic))] = saved
    return snapshot


def _merge_method_source_evidence(*values: str) -> str:
    """Merge exact METHOD IDs and de-duplicated source-grounding prose."""
    anchor_ids: list[str] = []
    prose: list[str] = []
    seen_prose: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        for anchor_id in _METHOD_ANCHOR_ID_RE.findall(text.upper()):
            if anchor_id not in anchor_ids:
                anchor_ids.append(anchor_id)
        without_ids = re.sub(
            _METHOD_ANCHOR_ID_RE.pattern, " ", text, flags=re.IGNORECASE)
        for part in re.split(r"\s*\|\s*", without_ids):
            part = re.sub(r"\s+", " ", part).strip(" |;,:-")
            key = bi.normalize_question_text(part)
            if key and key not in seen_prose:
                seen_prose.add(key)
                prose.append(part)
    return " | ".join(anchor_ids + prose)


def _restore_method_anchor_rows(
    records: list[dict], snapshot: dict[tuple[str, str], dict],
) -> list[dict]:
    """Restore exact METHOD tags/rows without replacing richer final content."""
    out = [dict(rec) for rec in records]
    groups: dict[tuple[str, str], dict] = {}
    for (anchor_id, topic_key), saved in snapshot.items():
        title_key = bi.normalize_question_text(saved.get("concept_title", ""))
        if not title_key:
            continue
        group = groups.setdefault(
            (topic_key, title_key),
            {
                "row": copy.deepcopy(saved),
                "anchor_ids": [],
                "evidence": [],
            },
        )
        if _method_row_quality(
            saved, source_topic=saved.get("topic", ""),
        ) > _method_row_quality(
            group["row"], source_topic=group["row"].get("topic", ""),
        ):
            group["row"] = copy.deepcopy(saved)
        if anchor_id not in group["anchor_ids"]:
            group["anchor_ids"].append(anchor_id)
        group["evidence"].append(saved.get("source_evidence", ""))

    present = {
        (anchor_id, _topic_comparison_key(rec.get("topic", "")))
        for rec in out
        for anchor_id in _method_anchor_ids(rec)
    }
    merged = 0
    reinserted = 0
    for (topic_key, title_key), group in groups.items():
        missing_ids = [
            anchor_id for anchor_id in group["anchor_ids"]
            if (anchor_id, topic_key) not in present
        ]
        if not missing_ids:
            continue
        saved = group["row"]
        source_topic = saved.get("topic", "")
        same_title = [
            i for i, rec in enumerate(out)
            if bi.normalize_question_text(rec.get("concept_title", "")) == title_key
        ]
        if same_title:
            exact_topic = [
                i for i in same_title
                if _topic_comparison_key(out[i].get("topic", "")) == topic_key
            ]
            candidates = exact_topic or same_title
            target_index = max(
                candidates,
                key=lambda i: _method_row_quality(
                    out[i], source_topic=source_topic),
            )
            target = dict(out[target_index])
            target["topic"] = source_topic
            target["source_evidence"] = _merge_method_source_evidence(
                target.get("source_evidence", ""),
                *group["evidence"],
                *missing_ids,
            )
            out[target_index] = target
            merged += 1
        else:
            restored = copy.deepcopy(saved)
            restored["topic"] = source_topic
            restored["source_evidence"] = _merge_method_source_evidence(
                restored.get("source_evidence", ""),
                *group["evidence"],
                *missing_ids,
            )
            topic_indexes = [
                i for i, rec in enumerate(out)
                if _topic_comparison_key(rec.get("topic", "")) == topic_key
            ]
            culmination_indexes = [
                i for i in topic_indexes
                if cr.is_culmination(out[i].get("concept_title", ""))
            ]
            insert_at = (
                culmination_indexes[0]
                if culmination_indexes
                else (topic_indexes[-1] + 1 if topic_indexes else len(out))
            )
            out.insert(insert_at, restored)
            reinserted += 1
        present.update((anchor_id, topic_key) for anchor_id in group["anchor_ids"])

    if merged or reinserted:
        progress.log(
            f"Method-row preservation merged METHOD evidence onto "
            f"{merged} surviving row(s) and reinserted {reinserted} dropped "
            "row(s).",
            level="warning",
        )
    return out


def _preserve_required_method_rows(
    before: list[dict], after: list[dict],
) -> list[dict]:
    """Restore tags/rows from an immediate pre-pass immutable snapshot."""
    return _restore_method_anchor_rows(
        after, _snapshot_method_anchor_rows(before))


def _enforce_method_anchor_topics(
    records: list[dict], anchors: list[dict],
) -> list[dict]:
    """Keep anchor-tagged derivations under their source section topic."""
    topic_by_anchor = {
        str(anchor.get("anchor_id") or "").upper():
        (anchor.get("topic_hint") or "").strip()
        for anchor in anchors
        if anchor.get("anchor_id") and (anchor.get("topic_hint") or "").strip()
    }
    canonical_by_topic_key: dict[str, str] = {}
    for source_topic in topic_by_anchor.values():
        canonical_by_topic_key.setdefault(
            _topic_comparison_key(source_topic), source_topic)
    corrected: set[int] = set()
    for i, rec in enumerate(records):
        source_topics = {
            topic_by_anchor[anchor_id]
            for anchor_id in _method_anchor_ids(rec)
            if anchor_id in topic_by_anchor
        }
        if len(source_topics) != 1:
            continue
        source_topic = next(iter(source_topics))
        if _topic_comparison_key(rec.get("topic", "")) != _topic_comparison_key(
                source_topic):
            rec["topic"] = source_topic
            corrected.add(i)
    # Keep siblings and the culmination on the exact same source spelling;
    # otherwise case/LaTeX normalization can split one logical topic in two.
    for i, rec in enumerate(records):
        source_topic = canonical_by_topic_key.get(
            _topic_comparison_key(rec.get("topic", "")))
        if source_topic and rec.get("topic") != source_topic:
            rec["topic"] = source_topic
            corrected.add(i)
    if corrected:
        progress.log(
            f"Restored exact source topics on {len(corrected)} "
            "derivation/method topic row(s).",
            level="warning",
        )
    return records


def _recover_method_anchor_rows_via_api(
    missing_anchors: list[dict], *, chunk_text: str, meta: dict,
    max_attempts: int = 3,
) -> list[dict]:
    """Recover only missing method rows, accepting exact tagged normal rows."""
    import json as _json

    ordered_ids = [
        str(anchor.get("anchor_id") or "").strip()
        for anchor in missing_anchors
        if str(anchor.get("anchor_id") or "").strip()
    ]
    pending = {
        str(anchor.get("anchor_id") or "").strip(): anchor
        for anchor in missing_anchors
        if str(anchor.get("anchor_id") or "").strip()
    }
    recovered: dict[str, dict] = {}
    recovered_keys: set[tuple[str, str]] = set()
    attempt_limit = max(1, int(max_attempts))
    system = prompts.get_text("concepts.method_anchor_recovery.system")

    for attempt in range(1, attempt_limit + 1):
        requested = [
            {
                "anchor_id": anchor_id,
                "topic_hint": pending[anchor_id].get("topic_hint", ""),
                "source_evidence": pending[anchor_id].get(
                    "source_evidence", ""),
                "required_formulas": pending[anchor_id].get(
                    "required_formulas") or [],
            }
            for anchor_id in ordered_ids
            if anchor_id in pending
        ]
        user = (
            _metadata_block(meta)
            + "\nSTILL-MISSING METHOD ANCHORS:\n"
            + _json.dumps(requested, ensure_ascii=False)
            + "\n\nRELEVANT CHUNK TEXT:\n"
            + _trim(chunk_text, 120_000)
        )
        data = _openai_json(system, user)
        raw_rows = data.get("rows") if isinstance(data, dict) else []
        if not isinstance(raw_rows, list):
            raw_rows = []

        tagged_counts: dict[str, int] = {}
        valid_by_anchor: dict[str, list[dict]] = {}
        invalid_count = 0
        for raw_row in raw_rows:
            if not isinstance(raw_row, dict):
                invalid_count += 1
                continue
            raw_evidence = raw_row.get("source_evidence")
            if not isinstance(raw_evidence, str):
                invalid_count += 1
                continue
            exact_ids = set(_METHOD_ANCHOR_ID_RE.findall(raw_evidence))
            matching_ids = exact_ids.intersection(pending)
            if len(exact_ids) != 1 or len(matching_ids) != 1:
                invalid_count += 1
                continue
            anchor_id = next(iter(matching_ids))
            tagged_counts[anchor_id] = tagged_counts.get(anchor_id, 0) + 1

            required_fields = (
                "topic", "parent_concept", "concept",
                "concept_description", "source_evidence",
            )
            if any(
                not isinstance(raw_row.get(field), str)
                or not raw_row.get(field, "").strip()
                for field in required_fields
            ):
                invalid_count += 1
                continue
            if (
                raw_row.get("keywords") is not None
                and not isinstance(raw_row.get("keywords"), str)
            ):
                invalid_count += 1
                continue

            parsed = _concept_rows_to_records({"rows": [raw_row]})
            if len(parsed) != 1:
                invalid_count += 1
                continue
            record = parsed[0]
            anchor = pending[anchor_id]
            source_topic = (anchor.get("topic_hint") or "").strip()
            if source_topic:
                record["topic"] = source_topic
            source_evidence = re.sub(
                r"\s+", " ",
                str(anchor.get("source_evidence") or ""),
            ).strip()
            record["source_evidence"] = (
                f"{anchor_id} | {source_evidence}"
                if source_evidence else anchor_id
            )

            report = cv.validate_concept_rows(
                [record],
                allow_types=False,
                require_culmination=False,
                allow_culmination=False,
            )
            if not report["ok"]:
                invalid_count += 1
                continue
            valid_by_anchor.setdefault(anchor_id, []).append(record)

        accepted = 0
        for anchor_id in ordered_ids:
            if anchor_id not in pending:
                continue
            candidates = valid_by_anchor.get(anchor_id, [])
            if tagged_counts.get(anchor_id) != 1 or len(candidates) != 1:
                continue
            record = candidates[0]
            key = (
                _topic_comparison_key(record.get("topic", "")),
                bi.normalize_question_text(record.get("concept_title", "")),
            )
            if key in recovered_keys:
                invalid_count += 1
                continue
            recovered[anchor_id] = record
            recovered_keys.add(key)
            del pending[anchor_id]
            accepted += 1

        progress.log(
            f"  focused method-anchor recovery attempt {attempt}/"
            f"{attempt_limit}: accepted {accepted} row(s), "
            f"{len(pending)} anchor(s) still missing"
            + (
                f"; rejected {invalid_count} malformed row(s)."
                if invalid_count else "."
            ),
            level="warning" if pending else "success",
        )
        if not pending:
            return [recovered[anchor_id] for anchor_id in ordered_ids]

    raise RuntimeError(
        "focused method-anchor recovery failed after "
        f"{attempt_limit} attempt(s); missing valid normal concept rows with "
        "exact METHOD IDs: "
        + ", ".join(
            anchor_id for anchor_id in ordered_ids if anchor_id in pending)
    )


def _canonicalize_method_anchor_tags(
    records: list[dict], anchors: list[dict], *, chunk_text: str, meta: dict,
) -> list[dict]:
    """Attach every full-chapter METHOD ID to its deterministic semantic row."""
    out = _enforce_method_anchor_topics(
        [dict(record) for record in records], anchors)

    def tag_covered(candidates: list[dict]) -> list[dict]:
        uncovered: list[dict] = []
        for anchor in candidates:
            anchor_id = str(anchor.get("anchor_id") or "").upper()
            if not anchor_id:
                continue
            best_index: int | None = None
            best_priority = 0
            for index, record in enumerate(out):
                if cr.is_culmination(record.get("concept_title", "")):
                    continue
                priority = _method_anchor_match_priority(record, anchor)
                if priority > best_priority:
                    best_index = index
                    best_priority = priority
            if best_index is None:
                uncovered.append(anchor)
                continue
            if anchor_id in _method_anchor_ids(out[best_index]):
                continue
            tagged = dict(out[best_index])
            existing = str(tagged.get("source_evidence") or "").strip()
            tagged["source_evidence"] = (
                f"{existing} | {anchor_id}" if existing else anchor_id
            )
            out[best_index] = tagged
        return uncovered

    uncovered = tag_covered(anchors)
    if uncovered:
        recovered = _recover_method_anchor_rows_via_api(
            uncovered, chunk_text=chunk_text, meta=meta)
        out = _merge_concept_records(out + recovered)
        out = _enforce_method_anchor_topics(out, anchors)
        uncovered = tag_covered(uncovered)
    if uncovered:
        raise RuntimeError(
            "canonical method-anchor tagging could not preserve focused "
            "recovery rows for: "
            + ", ".join(
                str(anchor.get("anchor_id") or "") for anchor in uncovered)
        )
    return out


def _extract_skeleton_via_api(
    chunks: list[dict], *, meta: dict,
    progress_start: float = 0.03, progress_end: float = 0.24,
) -> list[dict]:
    system = prompts.get_text("concepts.skeleton.system")
    all_records: list[dict] = []
    progress.log(
        f"Section-aware skeleton extraction across {len(chunks)} chunk(s).")
    for i, chunk in enumerate(chunks, start=1):
        fraction = (i - 1) / max(len(chunks), 1)
        progress.step(f"Concept skeleton — chunk {i}/{len(chunks)}",
                      value=progress_start
                      + (progress_end - progress_start) * fraction)
        chunk_headings = _topic_headings(chunk.get("sections") or [])
        method_anchors = _method_coverage_anchors(
            chunk.get("sections") or [])
        heading_block = (
            "\nSECTION HEADINGS IN THIS CHUNK (use ONLY these as topics; never "
            "invent your own topic names):\n- "
            + "\n- ".join(chunk_headings) + "\n"
        ) if chunk_headings else ""
        method_block = ""
        if method_anchors:
            import json as _json
            method_block = (
                "\nMANDATORY DERIVATION / METHOD ANCHORS:\n"
                + _json.dumps(method_anchors, ensure_ascii=False)
                + "\nEvery anchor is a durable normal concept, not an Example "
                "or Type. Cover each one and copy its anchor_id verbatim into "
                "that row's source_evidence.\n"
            )
        user = (
            _metadata_block(meta)
            + heading_block
            + method_block
            + f"\nChunk {i} of {len(chunks)}:\n"
            + chunk["text"]
        )
        data = _openai_json(system, user)
        chunk_records = _strip_types_from_records(_concept_rows_to_records(data))
        chunk_records = [
            r for r in chunk_records
            if not cr.is_culmination(r.get("concept_title", ""))
        ]
        expected_min = _expected_min_skeleton_rows(chunk["text"])
        if len(chunk_records) < expected_min:
            progress.log(
                f"  chunk {i}/{len(chunks)} returned only {len(chunk_records)} "
                f"concept(s) for {len(chunk['text']):,} chars (expected >= "
                f"{expected_min}) — retrying with a density instruction.",
                level="warning",
            )
            retry_user = (
                user
                + f"\n\nYOUR PREVIOUS ANSWER HAD ONLY {len(chunk_records)} CONCEPTS — "
                "that is under-extraction. Re-read the section text and extract "
                "EVERY distinct teachable concept (each definition, rule, law, "
                "method, procedure, property, distinction, relationship, "
                "country/case study, or skill). Keep chapter-opening framing "
                "ideas as their own concept. Do not summarize; split broad "
                "umbrella concepts (e.g. Germany+Italy as one row) into "
                "smaller mastery units."
            )
            retry_data = _openai_json(system, retry_user)
            retry_records = _strip_types_from_records(_concept_rows_to_records(retry_data))
            retry_records = [
                r for r in retry_records
                if not cr.is_culmination(r.get("concept_title", ""))
            ]
            if len(retry_records) > len(chunk_records):
                chunk_records = retry_records
        expected_max = _expected_max_skeleton_rows(chunk["text"], chunk_headings)
        if len(chunk_records) > expected_max:
            progress.log(
                f"  chunk {i}/{len(chunks)} returned {len(chunk_records)} "
                f"concept(s) (target <= {expected_max}) — retrying as a "
                "compact teaching skeleton.",
                level="warning",
            )
            retry_user = (
                user
                + f"\n\nYOUR PREVIOUS ANSWER HAD {len(chunk_records)} CONCEPTS — "
                "that is too granular. Merge terms, cases, examples, sub-types, "
                "and question headings into their parent teaching concepts. "
                "Keep only durable teacher-facing mastery objectives. Do not "
                "lose main coverage. Return no more than "
                f"{expected_max} concepts for this chunk."
            )
            retry_data = _openai_json(system, retry_user)
            retry_records = _strip_types_from_records(_concept_rows_to_records(retry_data))
            retry_records = [
                r for r in retry_records
                if not cr.is_culmination(r.get("concept_title", ""))
            ]
            if expected_min <= len(retry_records) < len(chunk_records):
                chunk_records = retry_records
        missing_method_anchors = _missing_method_anchors(
            chunk_records, method_anchors)
        if missing_method_anchors:
            import json as _json
            progress.log(
                f"  chunk {i}/{len(chunks)} omitted "
                f"{len(missing_method_anchors)} mandatory derivation/method "
                "anchor(s) — retrying coverage.",
                level="warning",
            )
            retry_user = (
                user
                + "\n\nYOUR PREVIOUS SKELETON OMITTED THESE MANDATORY "
                "DERIVATION / METHOD ANCHORS:\n"
                + _json.dumps(missing_method_anchors, ensure_ascii=False)
                + "\nReturn the COMPLETE corrected skeleton. Add a normal "
                "concept for every missing anchor and copy each anchor_id "
                "verbatim into source_evidence. Preserve all prior concepts."
            )
            retry_data = _openai_json(system, retry_user)
            retry_records = _strip_types_from_records(
                _concept_rows_to_records(retry_data))
            retry_records = [
                r for r in retry_records
                if not cr.is_culmination(r.get("concept_title", ""))
            ]
            chunk_records = _merge_concept_records(
                chunk_records + retry_records)
            missing_method_anchors = _missing_method_anchors(
                chunk_records, method_anchors)
        if missing_method_anchors:
            focused_records = _recover_method_anchor_rows_via_api(
                missing_method_anchors,
                chunk_text=chunk["text"],
                meta=meta,
            )
            chunk_records = _merge_concept_records(
                chunk_records + focused_records)
            chunk_records = _enforce_method_anchor_topics(
                chunk_records, method_anchors)
            missing_method_anchors = _missing_method_anchors(
                chunk_records, method_anchors)
        if missing_method_anchors:
            raise RuntimeError(
                "concept skeleton focused recovery did not preserve mandatory "
                "derivation/method anchors: "
                + ", ".join(
                    anchor["anchor_id"] for anchor in missing_method_anchors)
            )
        chunk_records = _ensure_parent_concepts(chunk_records)
        progress.log(f"  chunk {i}/{len(chunks)} skeleton rows: {len(chunk_records)}")
        all_records.extend(chunk_records)
        progress.set_progress(
            progress_start
            + (progress_end - progress_start)
            * (i / max(len(chunks), 1)),
            label=f"Concept skeleton — chunk {i}/{len(chunks)} complete",
        )
    out = _merge_concept_records(all_records)
    progress.log(f"Rows after skeleton merge: {len(out)}.")
    repaired = _repair_records_via_api(out, meta=meta, stage="skeleton")
    return _preserve_required_method_rows(out, repaired)


def _culmination_title(topic_records: list[dict]) -> str:
    names = [
        r.get("concept_title", "") for r in topic_records
        if not cr.is_culmination(r.get("concept_title", ""))
    ][:3]
    if not names:
        return "Culmination - Topic Recap"
    if len(names) == 1:
        body = names[0]
    elif len(names) == 2:
        body = f"{names[0]} and {names[1]}"
    else:
        body = f"{names[0]}, {names[1]} and {names[2]}"
    return f"Culmination - {body}"


# Deterministic final normalization: these two failure modes kept surviving
# LLM repair attempts in live runs, so they are fixed mechanically instead of
# failing the whole job (multi-user rule: output quality is never compromised,
# and a job must not die on formatting the code can fix itself).
_SECTION_NUMBER_SCRUB_RE = re.compile(
    r"\b(?:exercise|ex)?\s*\d+(?:\.\d+)+\b", re.IGNORECASE)
_EXERCISE_ONLY_RE = re.compile(
    r"^\s*(?:exercise|exercises|ex|intext(?:\s+questions?)?|review|practice|"
    r"problems?|questions?)\b[\s\d.:()\-]*$",
    re.IGNORECASE,
)
# OCR'd textbooks mark structural blocks as headings too ("Solution",
# "Example", "Summary", "Note to the Reader", activity prompts...). These are
# NEVER topics — their content belongs to the preceding real section.
_NON_TOPIC_RE = re.compile(
    r"^\s*(?:solutions?|examples?|summary|answers?|"
    r"alternative\s+solutions?|remarks?|"
    r"(?:a\s+)?notes?\s+to\s+the\s+reader|"
    r"learning\s+outcomes?|questions?\s+to\s+ponder|"
    r"check\s+your\s+understanding|quick\s+camp|"
    r"tick\s+the\s+correct\s+answer(?:\s+and\s+justify)?|"
    r"what\s+have\s+we\s+(?:learnt|learned|discussed)|"
    r"try\s+these|think\s+and\s+discuss|think,?\s+discuss\s+and\s+write|"
    r"(?:very\s+)?short\s+answer(?:\s+type)?(?:\s+questions?)?|"
    r"long\s+answer(?:\s+type)?(?:\s+questions?)?|"
    r"multiple\s+choice(?:\s+questions?)?|objective(?:\s+type)?(?:\s+questions?)?|"
    r"subjective(?:\s+questions?)?|descriptive(?:\s+questions?)?|"
    r"fill\s+in\s+the\s+blanks?|true\s*/?\s*false|match(?:ing)?(?:\s+the\s+following)?|"
    r"assertion\s*(?:and|&)?\s*reason(?:s)?|case\s+based(?:\s+questions?)?|"
    r"passage[-\s]+based(?:\s+questions?)?|source[-\s]+based(?:\s+questions?)?|"
    r"map\s+(?:work|skills?|questions?)|"
    r"do\s+this|.*\bactivity\b.*|activities|project\s+work|things\s+to\s+remember|"
    r"points\s+to\s+remember|key\s+points|glossary)\b[\s\d.:()\-]*$",
    re.IGNORECASE,
)


def _collapse_spaced_heading_word(heading: str) -> str:
    text = re.sub(r"\s+", " ", (heading or "").strip())
    if re.fullmatch(r"(?:[A-Za-z]\s+){2,}[A-Za-z]s?", text):
        return re.sub(r"\s+", "", text).lower()
    return text.lower()


def _is_non_topic_heading(heading: str) -> bool:
    # "(Optional)" suffixes and asterisks ("EXERCISE 6.6 (Optional)*") must not
    # hide an exercise heading from the match.
    h = re.sub(r"\(\s*optional\s*\)|\*", " ", heading or "", flags=re.IGNORECASE)
    if re.fullmatch(r"\s*(?:\d+|[ivxlcdm]+)\s*", h, re.IGNORECASE):
        return True
    if _collapse_spaced_heading_word(h) in {"questions", "exercises"}:
        return True
    return bool(_EXERCISE_ONLY_RE.match(h) or _NON_TOPIC_RE.match(h))


def _scrub_section_numbers(records: list[dict]) -> list[dict]:
    """Remove section/exercise numbering from topics and titles.

    Rows whose topic is a bare exercise or structural heading (e.g.
    "EXERCISE 1.2", "Solution", "Summary", "Tick the Correct Answer" — these
    slip through when OCR'd chapters mark such blocks as headings) are merged
    into the preceding real topic so no content is dropped.
    """

    def _scrub(text: str) -> str:
        return re.sub(r"\s+", " ", _SECTION_NUMBER_SCRUB_RE.sub(" ", text or "")
                      ).strip(" -:.,")

    prev_topic = ""
    for rec in records:
        topic = rec.get("topic", "")
        scrubbed = _scrub(topic)
        if _is_non_topic_heading(topic) or not scrubbed or _is_non_topic_heading(scrubbed):
            rec["topic"] = prev_topic or "General"
        elif scrubbed != topic:
            rec["topic"] = scrubbed
        prev_topic = rec.get("topic", "") or prev_topic
        title = rec.get("concept_title", "")
        scrubbed_title = _scrub(title)
        if scrubbed_title and scrubbed_title != title:
            rec["concept_title"] = scrubbed_title
    return records


def _culmination_starter_types(topic_records: list[dict]) -> str:
    """Deterministic mixed-application Types body for a culmination row.

    Renumbered downstream into the culmination-only continuous
    "Miscellaneous Type NN" sequence.
    """
    names = [
        r.get("concept_title", "") for r in topic_records
        if not cr.is_culmination(r.get("concept_title", ""))
    ][:3]
    combo = ", ".join(n for n in names if n) or "the topic's main ideas"
    return (
        "Type 01: Mixed application combining the topic's concepts "
        f"Case 01: Solve or explain a problem that combines {combo}"
    )


def _enforce_culminations(records: list[dict]) -> list[dict]:
    """Guarantee exactly one culmination row at the end of every topic.

    Keeps the authored culmination (first one when the model produced
    duplicates), appends the deterministic fallback when a topic has none,
    and always positions it last. A kept culmination that lost its Types gets
    the deterministic mixed-application starter (Miscellaneous sequence).
    Normal rows are never touched.
    """
    normal: dict[str, list[dict]] = {}
    culms: dict[str, list[dict]] = {}
    order: list[str] = []
    for rec in records:
        topic = rec.get("topic", "")
        if topic not in normal:
            normal[topic] = []
            culms[topic] = []
            order.append(topic)
        target = culms if cr.is_culmination(rec.get("concept_title", "")) else normal
        target[topic].append(rec)
    out: list[dict] = []
    for topic in order:
        out.extend(normal[topic])
        topic_culms = culms[topic]
        if topic_culms:
            keep = dict(topic_culms[0])
            keep["parent_concept"] = "Culmination"
            if not _has_meaningful_types(keep.get("concept_details", "")):
                keep["concept_details"] = _inject_types(
                    keep.get("concept_details", ""),
                    _culmination_starter_types(normal[topic]))
            out.append(keep)
            if len(topic_culms) > 1:
                progress.log(
                    f"Dropped {len(topic_culms) - 1} extra culmination row(s) "
                    f"in topic '{topic}'.",
                    level="warning",
                )
        else:
            fallback = _ensure_culmination_rows(normal[topic])
            out.extend(fallback[len(normal[topic]):])
            progress.log(
                f"Added deterministic culmination for topic '{topic}'.",
                level="warning",
            )
    return cr.set_culmination_recap(out)


def _ensure_culmination_rows(records: list[dict]) -> list[dict]:
    """Deterministic safety net: exactly one culmination row at each topic end."""
    out: list[dict] = []
    topics: dict[str, list[dict]] = {}
    order: list[str] = []
    for rec in records:
        topic = rec.get("topic", "")
        if topic not in topics:
            topics[topic] = []
            order.append(topic)
        if not cr.is_culmination(rec.get("concept_title", "")):
            topics[topic].append(rec)
    for topic in order:
        topic_records = topics[topic]
        out.extend(topic_records)
        out.append({
            "topic": topic,
            "parent_concept": "Culmination",
            "concept_title": _culmination_title(topic_records),
            "concept_details": (
                "Description: Recap // Types: Type 01: Mixed topic application "
                "Case 01: Solve or explain a problem that combines the topic's main ideas"
            ),
            "keywords": "culmination, recap, mixed application",
        })
    return out


def _merge_culmination_rows(records: list[dict], culms: list[dict]) -> list[dict]:
    """Insert one authored culmination row at the end of each topic.

    The normal rows are NEVER touched — the model only authors the culmination
    rows, so no chapter content can be lost in this pass. Topics the model
    missed get the deterministic fallback culmination.
    """
    normal = [r for r in records if not cr.is_culmination(r.get("concept_title", ""))]
    culm_by_topic: dict[str, dict] = {}
    for c in culms:
        topic = (c.get("topic") or "").strip().lower()
        if topic and cr.is_culmination(c.get("concept_title", "")):
            culm_by_topic.setdefault(topic, c)

    out: list[dict] = []
    topics: dict[str, list[dict]] = {}
    order: list[str] = []
    for rec in normal:
        topic = rec.get("topic", "")
        if topic not in topics:
            topics[topic] = []
            order.append(topic)
        topics[topic].append(rec)
    for topic in order:
        topic_records = topics[topic]
        out.extend(topic_records)
        authored = culm_by_topic.get(topic.strip().lower())
        if authored:
            authored = dict(authored)
            authored["topic"] = topic
            authored["parent_concept"] = "Culmination"
            out.append(authored)
        else:
            out.extend(
                _ensure_culmination_rows(topic_records)[len(topic_records):])
    return out


def _build_culminations_via_api(records: list[dict], *, meta: dict) -> list[dict]:
    import json as _json

    if not records:
        return records
    system = prompts.get_text("concepts.culmination.system")
    payload = _json.dumps({"rows": _records_to_api_rows(records)}, ensure_ascii=False)
    user = (
        _metadata_block(meta)
        + "\nFinal normal concept map — return ONLY one culmination row per topic:\n"
        + payload
    )
    progress.log("Building topic culmination rows.")
    data = _openai_json(system, user)
    authored = _concept_rows_to_records(data)
    # The model authors ONLY the culmination rows; the normal rows are merged
    # back programmatically so this pass can never drop chapter content.
    out = _merge_culmination_rows(records, authored)
    out = cr.set_culmination_recap(out)
    out = _repair_records_via_api(out, meta=meta, stage="culmination")
    culms = sum(1 for r in out if cr.is_culmination(r.get("concept_title", "")))
    progress.log(f"Culminations added: {culms}.", level="success")
    return out


_PART_SUFFIX_RE = re.compile(r"\s*\(part \d+/\d+\)$", re.IGNORECASE)
_MIN_MAIN_TOPIC_HEADINGS = 3
_MAX_MAIN_TOPIC_HEADINGS = 12


def _looks_like_math_fragment_heading(heading: str) -> bool:
    if "$" not in heading and "\\(" not in heading and "\\[" not in heading:
        return False
    plain = re.sub(r"\$.*?\$", " ", heading)
    plain = re.sub(r"\\[\(\[].*?\\[\)\]]", " ", plain)
    return len(re.findall(r"[A-Za-z]", plain)) < 3


def _dedupe_topic_candidates(candidates: list[dict]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = candidate["key"]
        if key in seen:
            continue
        seen.add(key)
        out.append(candidate["heading"])
    return out


def _topic_headings(sections: list[dict]) -> list[str]:
    """Ordered, de-duplicated main topic headings from parsed sections."""
    candidates: list[dict] = []
    for section in sections or []:
        heading = _PART_SUFFIX_RE.sub("", (section.get("heading") or "").strip())
        if not heading or heading.lower() == "general":
            continue
        # OCR sometimes promotes a displayed equation to a heading
        # (e.g. "$ AMC PNR $") — math fragments are never topics.
        if _looks_like_math_fragment_heading(heading):
            continue
        if _is_non_topic_heading(heading):
            continue
        key = _topic_comparison_key(heading)
        if not key:
            continue
        try:
            level = int(section.get("heading_level") or 1)
        except (TypeError, ValueError):
            level = 1
        candidates.append({
            "heading": heading,
            "key": key,
            "level": max(1, level),
            "numbered": bool(section.get("heading_numbered")),
            "number_prefix": section.get("heading_number_prefix") or "",
            "chapter": bool(section.get("heading_chapter")),
        })
    numbered = [c for c in candidates if c["numbered"] and not c["chapter"]]
    # Main topics are the SHALLOWEST numbering level with enough sections:
    # NCERT History numbers main sections "1", "2", ... with "2.1", "2.2"
    # subtopics beneath them, while NCERT Math numbers main sections "1.1",
    # "1.2" (the integer level is only the chapter). Reviewers require the
    # main textbook sections as topics — never their subtopics.
    by_depth: dict[int, list[dict]] = {}
    for c in numbered:
        if c["number_prefix"]:
            by_depth.setdefault(c["number_prefix"].count("."), []).append(c)
    for depth in sorted(by_depth):
        if len(by_depth[depth]) >= _MIN_MAIN_TOPIC_HEADINGS:
            numbered = by_depth[depth]
            break
    if len(numbered) >= _MIN_MAIN_TOPIC_HEADINGS:
        if len(numbered) > _MAX_MAIN_TOPIC_HEADINGS:
            progress.log(
                f"Collapsed {len(numbered)} numbered headings to "
                f"{_MAX_MAIN_TOPIC_HEADINGS} main topic headings.",
                level="warning",
            )
            numbered = numbered[:_MAX_MAIN_TOPIC_HEADINGS]
        return _dedupe_topic_candidates(numbered)

    levels = sorted({c["level"] for c in candidates})
    if len(levels) > 1 and sum(1 for c in candidates if c["level"] == levels[0]) == 1:
        candidates = [c for c in candidates if c["level"] != levels[0]]
        levels = sorted({c["level"] for c in candidates})
    if len(candidates) <= _MAX_MAIN_TOPIC_HEADINGS:
        return _dedupe_topic_candidates(candidates)

    start = 0

    selected: list[dict] = []
    for level in levels[start:]:
        selected.extend(c for c in candidates if c["level"] == level)
        if len(selected) >= _MIN_MAIN_TOPIC_HEADINGS:
            break
    if len(selected) < _MIN_MAIN_TOPIC_HEADINGS:
        selected = candidates

    if len(selected) > _MAX_MAIN_TOPIC_HEADINGS:
        progress.log(
            f"Collapsed {len(candidates)} candidate headings to "
            f"{_MAX_MAIN_TOPIC_HEADINGS} main topic headings.",
            level="warning",
        )
        selected = selected[:_MAX_MAIN_TOPIC_HEADINGS]
    return _dedupe_topic_candidates(selected)


def _chapter_title_is_main_topic(
    sections: list[dict], chapter_title: str,
) -> bool:
    """Whether a numbered main section intentionally repeats the chapter name."""
    chapter_key = _topic_comparison_key(chapter_title)
    if not chapter_key:
        return False
    return any(
        section.get("heading_numbered")
        and not section.get("heading_chapter")
        and _topic_comparison_key(section.get("heading") or "") == chapter_key
        for section in sections
    )


def _snap_topics_to_headings(
    records: list[dict], headings: list[str], *, chapter_title: str = "",
    allow_chapter_title_topic: bool = False,
) -> list[dict]:
    """Deterministically constrain topics to the textbook's section headings.

    Models drift in both directions — collapsing a chapter into one umbrella
    topic, or inventing dozens of micro-topics. The textbook's own section
    headings are the ground truth: rows whose topic is not a real section
    heading are filed under the nearest preceding real section (reading
    order). Skipped when the source exposes fewer than 3 usable headings
    (unreliable OCR) — the API re-segregation pass covers that case.
    """
    if len(headings) < 3:
        return records
    chapter_key = _topic_comparison_key(chapter_title)
    valid: dict[str, str] = {}
    for h in headings:
        key = _topic_comparison_key(h)
        # ``_topic_headings`` has already selected the real main sections.
        # A legitimate numbered section may intentionally repeat the chapter
        # title (NCERT Ch. 5 "Arithmetic Progressions" / §5.2
        # "Arithmetic Progressions"), so title equality cannot disqualify it.
        if key and (key != chapter_key or allow_chapter_title_topic):
            valid.setdefault(key, _strip_section_number(h))
    if len(valid) < 3:
        return records
    canonical = list(valid.values())
    prev: str | None = None
    snapped = 0
    for rec in records:
        key = _topic_comparison_key(rec.get("topic", ""))
        if key in valid:
            rec["topic"] = valid[key]
            prev = valid[key]
            continue
        rec["topic"] = prev or canonical[0]
        snapped += 1
    if snapped:
        progress.log(
            f"Snapped {snapped} row(s) onto the textbook's "
            f"{len(canonical)} section topics.")
    return records


def _topics_look_collapsed(records: list[dict], headings: list[str]) -> bool:
    """True when the map filed (nearly) everything under one umbrella topic
    although the source clearly has several section headings."""
    if not records or len(headings) < 2:
        return False
    topics = {_topic_comparison_key(r.get("topic") or "") for r in records}
    topics.discard("")
    if len(topics) <= 1:
        return True
    return len(records) >= 12 and len(topics) <= 2 and len(headings) >= 4


def _restructure_topics_via_api(
    records: list[dict], *, meta: dict, headings: list[str],
) -> list[dict]:
    """Re-segregate collapsed topics using the source's section headings.

    Only the ``topic`` field is taken from the model, matched back to the
    original rows by concept title — no concept can be added, dropped, or
    rewritten by this pass.
    """
    import json as _json

    system = prompts.get_text("concepts.topic_structure.system")
    payload = _json.dumps({"rows": _records_to_api_rows(records)}, ensure_ascii=False)
    user = (
        _metadata_block(meta)
        + "\nSECTION HEADINGS (reading order):\n- "
        + "\n- ".join(headings)
        + f"\n\nConcept map with collapsed topics ({len(records)} rows):\n"
        + payload
    )
    data = _openai_json(system, user)
    topic_by_title = {
        bi.normalize_question_text(r["concept_title"]): r["topic"].strip()
        for r in _concept_rows_to_records(data)
        if (r.get("topic") or "").strip()
    }
    updated = 0
    for rec in records:
        new_topic = topic_by_title.get(
            bi.normalize_question_text(rec.get("concept_title", "")))
        if new_topic and new_topic != rec.get("topic"):
            rec["topic"] = new_topic
            updated += 1
    distinct = {(r.get("topic") or "").strip().lower() for r in records}
    distinct.discard("")
    progress.log(
        f"Topic re-segregation: {updated} row(s) reassigned; "
        f"{len(distinct)} distinct topic(s).",
        level="success" if len(distinct) > 1 else "warning",
    )
    return records


def chapter_meta_via_api(
    *, meta: dict, topics: list[dict], live: bool | None = None,
) -> dict:
    """Chapter description/duration + per-topic descriptions in one API pass.

    ``topics`` is ``[{"topic": ..., "concepts": [titles...]}, ...]``. Returns a
    (possibly empty) dict with ``chapter_description``,
    ``chapter_duration_minutes`` and ``topic_descriptions`` (keyed by
    normalized topic title); callers fall back to deterministic summaries for
    anything missing.
    """
    import json as _json

    use_live = config.use_live_generation() if live is None else live
    if not use_live or not topics:
        return {}
    system = prompts.get_text("concepts.chapter_meta.system")
    user = (
        _metadata_block(meta)
        + "\nTopics and their concepts:\n"
        + _json.dumps({"topics": topics}, ensure_ascii=False)
    )
    progress.log(
        "Writing chapter/topic metadata (chapter description, duration, "
        "topic descriptions) via API pass.")
    data = _openai_json(system, user)
    out: dict = {}
    description = (data.get("chapter_description") or "").strip()
    if description:
        out["chapter_description"] = description
    try:
        minutes = int(float(data.get("chapter_duration_minutes") or 0))
    except (TypeError, ValueError):
        minutes = 0
    finalized = int(meta.get("finalized_duration_minutes") or 0)
    if finalized > 0:
        out["chapter_duration_minutes"] = finalized
    elif minutes > 0:
        out["chapter_duration_minutes"] = minutes
    topic_descriptions: dict[str, str] = {}
    for row in data.get("topics", []) or []:
        if not isinstance(row, dict):
            continue
        topic = (row.get("topic") or "").strip()
        topic_description = (row.get("topic_description") or "").strip()
        if topic and topic_description:
            topic_descriptions[bi.normalize_question_text(topic)] = topic_description
    if topic_descriptions:
        out["topic_descriptions"] = topic_descriptions
    return out


def concepts_from_mmd(
    mmd_text: str, *, subject: str = "", board: str = "", grade: str = "",
    unit: str = "", chapter_title: str = "", chapter_id: int | str | None = None,
    chapter_code: str = "", learning_kind: str = "Post",
    live: bool | None = None, artifacts: dict | None = None,
) -> list[dict]:
    """Parse an MMD document into concept records (post-learning).

    Large chapters are processed in ordered chunks (never trimmed) and the
    per-chunk concepts are merged, so no chapter content is lost.

    When ``artifacts`` is provided it is filled with the intermediate
    ``question_task_inventory`` and ``mined_types`` so callers can persist
    them (e.g. for the extraction-completeness CSV download).
    """
    use_live = config.use_live_generation() if live is None else live
    meta = _metadata(
        subject=subject, board=board, grade=grade, unit=unit,
        chapter_title=chapter_title, chapter_id=chapter_id,
        chapter_code=chapter_code, learning_kind=learning_kind,
    )
    if use_live:
        progress.step("Concept extraction — parsing source structure", value=0.01)
        chunks = _section_aware_chunks(mmd_text)
        sections = [s for c in chunks for s in c["sections"]]
        method_anchors = _method_coverage_anchors(sections)
        progress.log("Concept generation metadata received:\n" + _metadata_block(meta))
        progress.log(
            f"Extracting concepts from {len(mmd_text):,} chars "
            f"across {len(chunks)} section-aware chunk(s) "
            f"(subject: {subject or 'general'}).")
        out = _extract_skeleton_via_api(chunks, meta=meta)
        if not out:
            raise RuntimeError("live concept extraction returned no rows")
        out = _canonicalize_method_anchor_tags(
            out, method_anchors, chunk_text=mmd_text, meta=meta)
        # The full-chapter anchors provide the authoritative METHOD IDs and
        # source topics.  Keep immutable skeleton rows before any chapter-wide
        # canonicalization or topic pass can merge them away.
        skeleton_method_row_snapshot = _snapshot_method_anchor_rows(
            out, method_anchors)
        progress.step("Concept extraction — canonicalizing skeleton", value=0.27)
        # Structural OCR headings ("Solution", "Summary", "EXERCISE 6.1") must
        # never become topics: merge their rows into the preceding real topic
        # BEFORE any chapter-wide pass builds on the topic structure.
        out = _scrub_section_numbers(out)
        headings = _topic_headings(sections)
        allow_chapter_title_topic = _chapter_title_is_main_topic(
            sections, chapter_title)
        out = _snap_topics_to_headings(
            out, headings, chapter_title=chapter_title,
            allow_chapter_title_topic=allow_chapter_title_topic)
        out = _consolidate_concepts_via_api(out, subject=subject, mmd_text=mmd_text, meta=meta)
        progress.step("Concept extraction — aligning source topics", value=0.35)
        # Topic names must follow the SOURCE file's own section headings.
        # GPT re-segregates on EVERY run with a reliable heading list (not
        # only when topics collapsed); the deterministic snap afterwards is
        # only a straggler backstop.
        if _topics_look_collapsed(out, headings):
            progress.log(
                f"Topic segregation collapsed: {len(out)} concepts share almost "
                f"one topic while the source has {len(headings)} section "
                "headings — re-segregating topics via API.",
                level="warning",
            )
        if len(headings) >= 3 or (headings and _topics_look_collapsed(out, headings)):
            out = _restructure_topics_via_api(out, meta=meta, headings=headings)
        out = _snap_topics_to_headings(
            out, headings, chapter_title=chapter_title,
            allow_chapter_title_topic=allow_chapter_title_topic)
        out = _restore_method_anchor_rows(
            out, skeleton_method_row_snapshot)
        out = _enforce_method_anchor_topics(out, method_anchors)
        out = _canonicalize_method_anchor_tags(
            out, method_anchors, chunk_text=mmd_text, meta=meta)
        progress.step("Concept extraction — refining descriptions", value=0.42)
        out = _refine_descriptions_via_api(
            out, subject=subject, mmd_text=mmd_text, meta=meta, sections=sections)
        out = _ensure_mastery_lines_via_api(out, meta=meta)
        # Description refinement receives every restored skeleton row.  Restore
        # once more before taking the richer authoritative snapshot in case a
        # description repair omitted a tagged row.
        out = _restore_method_anchor_rows(
            out, skeleton_method_row_snapshot)
        out = _enforce_method_anchor_topics(out, method_anchors)
        # From this point onward, every whole-map/API cleanup is disposable:
        # these immutable, source-topic-keyed rows are the authoritative method
        # fallback immediately before the final gate.
        method_row_snapshot = _snapshot_method_anchor_rows(
            out, method_anchors)
        unsnapshotted_anchors = [
            anchor for anchor in method_anchors
            if (
                str(anchor.get("anchor_id") or "").upper(),
                _topic_comparison_key(anchor.get("topic_hint", "")),
            ) not in method_row_snapshot
        ]
        if unsnapshotted_anchors:
            raise RuntimeError(
                "post-description method-row restoration could not snapshot "
                "mandatory full-chapter anchors: "
                + ", ".join(
                    anchor["anchor_id"] for anchor in unsnapshotted_anchors)
            )
        if method_row_snapshot:
            snapshotted_rows = {
                (
                    _topic_comparison_key(row.get("topic", "")),
                    bi.normalize_question_text(row.get("concept_title", "")),
                )
                for row in method_row_snapshot.values()
            }
            progress.log(
                f"Snapshotted {len(snapshotted_rows)} refined method row(s) "
                f"covering {len(method_row_snapshot)} mandatory anchor(s).")
        progress.set_progress(
            0.55, label="Concept extraction — descriptions complete")
        progress.step(
            "Concept extraction — inventorying questions and worked examples",
            value=0.58,
        )
        question_task_inventory = _extract_question_task_inventory_via_api(
            meta=meta, sections=sections)
        progress.set_progress(
            0.70, label="Concept extraction — question inventory complete")
        progress.step("Concept extraction — mining reusable Types", value=0.72)
        mined_types = _mine_types_from_inventory_via_api(
            meta=meta, inventory=question_task_inventory)
        progress.set_progress(
            0.79, label="Concept extraction — reusable Types mined")
        if artifacts is not None:
            artifacts["question_task_inventory"] = question_task_inventory
            artifacts["mined_types"] = mined_types
        # Culminations are built BEFORE Types assignment so mixed/synthesis
        # Types mined from the source can be placed on culmination rows too.
        progress.step("Concept extraction — building culminations", value=0.81)
        out = _build_culminations_via_api(out, meta=meta)
        progress.step(
            "Concept extraction — assigning Types within source topics",
            value=0.85,
        )
        out = _assign_types_via_api(
            out,
            subject=subject,
            mmd_text=mmd_text,
            meta=meta,
            sections=sections,
            question_task_inventory=question_task_inventory,
            mined_types=mined_types,
        )
        progress.set_progress(
            0.91, label="Concept extraction — Type assignment complete")
        # Deterministic normalization BEFORE the strict repair: formatting
        # failures the code can fix itself (section numbering in topics/titles,
        # missing/duplicate culminations) must never fail a job or burn repair
        # attempts needed for semantic issues. Source references ("Example 5",
        # "Exercise 1.2") are deliberately KEPT here: the repair pass has the
        # chapter source and substitutes the full actual problem content,
        # which is preferred over neutral rewording.
        out = _scrub_section_numbers(out)
        out = _merge_concept_records(out)
        out = _dedupe_titles_chapter_wide(out)
        # Near-duplicate titles: GPT merges the rows' content; the
        # deterministic drop only guards whatever the merge pass left behind.
        progress.step(
            "Concept extraction — validating and repairing final map",
            value=0.93,
        )
        before_duplicate_merge = out
        out = _merge_similar_concepts_via_api(out, meta=meta)
        out = _preserve_required_method_rows(before_duplicate_merge, out)
        out = concept_cleanup.dedupe_similar_titles_chapter_wide(out)
        out = concept_cleanup.filter_review_violations(
            out, subject=subject, board=board, chapter_title=chapter_title)
        out = [
            concept_cleanup.clean_concept_record(dict(r), neutralize_artifacts=False)
            for r in out
        ]
        out = _enforce_culminations(out)
        out = _ensure_misconceptions_via_api(out, meta=meta)
        before_final_repair = out
        out = _repair_records_via_api(
            out, meta=meta, stage="final", source_context=mmd_text, strict=False,
            max_attempts=3)
        out = _preserve_required_method_rows(before_final_repair, out)
        # Post-repair: neutralize ONLY rows the repair pass could not fix —
        # rows that already validate cleanly keep their full GPT-authored
        # wording untouched (no blanket deterministic rewriting).
        out = _neutralize_unrepaired_rows(out)
        # Truncated Case Examples ("Example: q") that GPT repair could not
        # expand are filled from the Question / Task Inventory, or dropped
        # when no match exists — never leave short_case_example as a fatal.
        out = _salvage_short_case_examples(
            out, inventory=question_task_inventory)
        # Salvage may leave a bare figure ref if inventory lacked the URL;
        # re-neutralize any residual source_artifact rows only.
        out = _neutralize_unrepaired_rows(out)
        out = cr.refine_chapter(out)
        # The repair/cleanup passes may reorder, rename, or re-collide rows;
        # re-assert the duplicate-title, culmination, mastery-line, and
        # misconception invariants before the final gate (rows the repair pass
        # rewrote may have lost them).
        out = _dedupe_titles_chapter_wide(out)
        out = concept_cleanup.dedupe_similar_titles_chapter_wide(out)
        out = concept_cleanup.filter_review_violations(
            out, subject=subject, board=board, chapter_title=chapter_title)
        out = _ensure_mastery_lines_via_api(out, meta=meta)
        out = _ensure_misconceptions_via_api(out, meta=meta)
        out = _enforce_culminations(out)
        # Mastery/misconception GPT passes can reintroduce Example/Fig/page
        # pointers — scrub source_artifact one last time immediately before
        # the hard final gate so deposit is never blocked by residual refs.
        out = _neutralize_unrepaired_rows(out)
        out = _restore_method_anchor_rows(out, method_row_snapshot)
        # Restoration is the terminal row-membership operation. Only
        # non-dropping field/ordering guarantees may run after this point.
        out = _ensure_mastery_lines_via_api(
            out, meta=meta, use_api=False)
        out = cr.ensure_misconceptions(out)
        out = _enforce_method_anchor_topics(out, method_anchors)
        out = _enforce_culminations(out)
        missing_method_anchors = [
            anchor for anchor in method_anchors
            if (
                not _method_anchor_tagged_in_topic(
                    out,
                    str(anchor.get("anchor_id") or ""),
                    anchor.get("topic_hint", ""),
                )
                or not _method_anchor_covered(out, anchor)
            )
        ]
        if missing_method_anchors:
            raise RuntimeError(
                "final concept map lost mandatory derivation/method anchors: "
                + ", ".join(
                    anchor["anchor_id"] for anchor in missing_method_anchors)
            )
        type_topic_violations = _mined_type_topic_violations(
            out, mined_types)
        if type_topic_violations:
            summary = ", ".join(
                f"{item['type_id']}:{item['reason']}"
                for item in type_topic_violations[:10]
            )
            raise RuntimeError(
                "mined Type source-topic validation failed: " + summary)
        _validate_final_or_raise(out, stage="final")
        missing = sum(
            1 for r in out
            if not _has_meaningful_types(r.get("concept_details", ""))
            and not cr.is_culmination(r.get("concept_title", ""))
        )
        if missing:
            progress.log(
                f"{missing} non-culmination concept(s) still lack Types after all passes.",
                level="warning",
            )
        normal_count = sum(
            1 for r in out if not cr.is_culmination(r.get("concept_title", "")))
        expected_min = _expected_min_skeleton_rows(mmd_text)
        if normal_count < expected_min:
            progress.log(
                f"Only {normal_count} concept(s) extracted from "
                f"{len(mmd_text):,} chars of source (expected >= {expected_min}). "
                "The chapter was likely under-extracted — check the per-stage "
                "row counts above to see which pass lost rows.",
                level="warning",
            )
        progress.set_progress(1.0, label="Concept extraction complete")
        progress.log(f"Final concept count: {len(out)}.", level="success")
        return out
    config.require_generation_live()
    progress.log(f"Extracting concepts (dry) from {len(mmd_text):,} chars.")
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
                "parent_concept": topic,
                "concept_title": title,
                "concept_details": f"Description: {line[:200]}",
                "keywords": ", ".join(title.lower().split()[:5]),
            })
    out = out or [{
        "topic": topic, "concept_title": "Overview",
        "parent_concept": topic,
        "concept_details": "Description: (empty document)",
        "keywords": "",
    }]
    return cr.refine_chapter(_ensure_culmination_rows(_ensure_parent_concepts(out)))


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


_PRELEARN_CAT = "Build Concepts · pre-learning derivation"

prompts.register(
    "prelearning.system", category=_PRELEARN_CAT,
    label="Pre-learning derivation system prompt",
    description="Variables: {{subject}} {{grade}} {{board}} {{board_guidance}} "
                "{{min_t}} {{max_t}} {{min_ct}} {{max_ct}}.",
    variables=("subject", "grade", "board", "board_guidance",
               "min_t", "max_t", "min_ct", "max_ct"),
    default="""\
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

NAMING RULES: each name must be specific to the prerequisite skill — vary
structure across siblings. Do NOT repeat a shared opener on multiple rows.
NEVER "Types of _", "Definition of _", "Basics of _", "Introduction to _".
NEVER prefix names with decimal section numbers (1., 1.1, 1.2, etc.).
NEVER chain names with '&' (use commas with a final 'and').

COGNITIVE TAGGING (MANDATORY): one primary tag per concept:
FL=Foundational Logic | NU=Numerical Handling | VC=Vocabulary Concept |
RS=Real-world Sense | GR=Graphical Reasoning.

COUNTS (STRICT): {{min_t}}-{{max_t}} topics; every topic has
{{min_ct}}-{{max_ct}} concepts. Order by dependency. No duplicates.

CONCEPT DESCRIPTION FORMAT (MANDATORY): one string, sections separated by " // ":
Description: <what the student should already know; 2-4 short lines; must not
teach the chapter> // Types: <classify ALL distinct prerequisite-check
varieties for this skill using zero-padded numeric labels exactly "Type 01:",
"Case 01:": Type 01: <variety title> Case 01: <example prompt> Case 02: ...
Type 02: <variety> Case 01: ...> // Misconception: <typical prior-knowledge gaps>.
Description is the important lesson-planning input: source/syllabus-grounded,
clear, and concise (2-4 compact sentences, not a chapter dump). Include Types
only when the prerequisite has assessable check formats; pure vocabulary recall
may omit Types. Include Misconception only when there is a real likely
prior-knowledge error; never write N/A/None/filler. Restart at Type 01 per
concept; continuous renumbering happens downstream.
NEVER reference source artifacts and never the words "MMD".
Do NOT mention groups or group columns.

OUTPUT (STRICT JSON ONLY): {"topics": [{"topic_name": "", "concepts":
[{"parent_concept": "", "concept_name": "", "concept_description": "",
"tag": ""}]}]}.

FINAL VALIDATION: for each concept ask "Was this already expected knowledge
BEFORE this grade (or clearly foundational)?" — if unsure or borderline,
REMOVE or REPLACE with a safer prior-grade prerequisite.

RUN CONTEXT: Subject: {{subject}} | Grade: {{grade}} | Board: {{board}}
{{board_guidance}}""")

prompts.register(
    "prelearning.auditor", category=_PRELEARN_CAT,
    label="Pre-learning syllabus-boundary auditor prompt",
    default="""\
You are a strict curriculum auditor for ICSE/CBSE-aligned pre-learning.
You receive draft pre-learning JSON ("topics" with nested "concepts") plus
chapter context. REMOVE or REPLACE any concept that is taught as new in the
current grade, introduced in this chapter or later in the same course, or
fails "was this already expected knowledge before this grade?" (unsure or
borderline -> REPLACE). Allow previous-grade ideas and foundational skills.
STRUCTURE: output exactly the same number of topics, and per topic exactly
the same number of concepts — substitute rejected rows, never delete slots.
Keep the same schema and the Description: // Types: // Misconception format
(Types and Misconception are optional when not useful), with zero-padded numeric
labels (Type 01:, Case 01:) where Types exist, plus the tag (FL|NU|VC|RS|GR).
Rewrite repetitive sibling names to be distinct.
Return ONLY JSON with one key "topics". No markdown, no commentary.""")


def _prelearning_system(subject: str, grade: str, board: str) -> str:
    return prompts.render(
        "prelearning.system",
        subject=subject, grade=grade, board=board,
        board_guidance=_board_guidance(board),
        min_t=_PRE_MIN_T, max_t=_PRE_MAX_T,
        min_ct=_PRE_MIN_CT, max_ct=_PRE_MAX_CT,
    )


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
                                        ) if b]
            out.append({
                "topic": t_name,
                "parent_concept": parent or t_name.replace(" (Pre-Learning)", ""),
                "concept_title": title,
                "concept_details": (c.get("concept_description") or "").strip(),
                "keywords": "; ".join(keyword_bits),
            })
    return out


def _exclude_current_chapter_concepts(pre_rows: list[dict], current_rows: list[dict]) -> list[dict]:
    current = {
        bi.normalize_question_text(r.get("concept_title", ""))
        for r in current_rows
        if r.get("concept_title") and not cr.is_culmination(r.get("concept_title", ""))
    }
    out = [
        r for r in pre_rows
        if bi.normalize_question_text(r.get("concept_title", "")) not in current
    ]
    return out


def pre_learning_from_rows(
    rows: list[dict], *, subject: str = "", grade: str = "", board: str = "",
    chapter_title: str = "", unit: str = "", live: bool | None = None,
) -> list[dict]:
    """Derive pre-learning records from concept-mapping rows (dicts).

    rows: [{concept_title, concept_details, topic}, ...] — the chapter's
    post-learning concept map.
    """
    use_live = config.use_live_generation() if live is None else live
    if not use_live:
        config.require_generation_live()
    if not use_live:
        pre = [{
            "topic": f"{(r.get('topic') or 'Topic 01')} (Pre-Learning)",
            "parent_concept": f"Foundations for {r.get('parent_concept') or r.get('topic') or 'Chapter'}",
            "concept_title": f"Prerequisite for {r['concept_title']}",
            "concept_details": (
                f"Description: foundational idea required before learning "
                f"'{r['concept_title']}'. "
                "// Misconception: assuming the prerequisite is already mastered."
            ),
            "keywords": r.get("keywords", ""),
        } for r in rows if not cr.is_culmination(r.get("concept_title", ""))]
        return _ensure_parent_concepts(_exclude_current_chapter_concepts(pre, rows))

    listing = "\n".join(
        f"- [{(r.get('topic') or '')[:60]} / {(r.get('parent_concept') or '')[:60]}] {r['concept_title']}: "
        f"{(r.get('concept_details') or '')[:260]}"
        for r in rows
        if not cr.is_culmination(r.get("concept_title", ""))
    )
    user = (
        f"CHAPTER: {chapter_title or '(untitled)'}\n"
        f"Subject: {subject} | Grade: {grade} | Board: {board} | Unit: {unit}\n\n"
        "CONCEPT MAPPING (current chapter content — exclude from pre-learning):\n"
        # Pre-learning reasons over the whole concept map at once; keep a high
        # bound so realistic chapters are never truncated.
        + _trim(listing, 400_000)
    )
    system = _prelearning_system(subject, grade, board)
    draft = _openai_json(system, user)
    if not draft.get("topics"):
        raise RuntimeError("live pre-learning derivation returned no topics")

    # Stage 2: syllabus boundary auditor (replaces violating rows in place).
    import json as _json
    audited = _openai_json(
        prompts.get_text("prelearning.auditor"),
        f"Chapter: {chapter_title} | Subject: {subject} | Grade: {grade} | "
        f"Board: {board} | Unit: {unit}\n\nDRAFT:\n" + _json.dumps(draft)[:120_000],
    )
    final = audited if audited.get("topics") else draft

    out = _exclude_current_chapter_concepts(_flatten_pre_topics(final), rows)
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
                "parent_concept": c.parent_concept,
                "concept_title": c.concept_title,
                "concept_details": c.concept_details,
                "keywords": c.keywords,
            } for c in concepts],
            subject=chapter.subject if chapter else "",
            grade=chapter.grade if chapter else "",
            board=chapter.board if chapter else "",
            chapter_title=chapter.chapter_title if chapter else "",
            unit=chapter.unit if chapter else "",
            live=True,
        )
    config.require_generation_live()
    out: list[dict] = []
    for c in concepts:
        out.append({
            "source_concept_id": c.id,
            "topic": f"{c.topic.topic_title} (Pre-Learning)",
            "parent_concept": f"Foundations for {c.parent_concept or c.topic.topic_title}",
            "concept_title": f"Pre: {c.concept_title}",
            "concept_details": (
                f"Description: foundational idea required before learning "
                f"'{c.concept_title}'. "
                "// Misconception: assuming the prerequisite is already mastered."
            ),
            "keywords": c.keywords,
        })
    return out

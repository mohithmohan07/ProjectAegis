"""Content generation: questions from concepts, concepts from MMD.

All functions have a dry path (deterministic, no API keys — used for the MVP
and tests) and a live hook that delegates to the vendored OpenAI-backed
scripts. The dry path is intentionally realistic: it returns fully-populated
records so the post-generation pipeline and the canonical writer are always
exercised end to end.
"""
from __future__ import annotations

import copy
import hashlib
import os
import random
import re
import threading
import unicodedata
from collections import Counter
from datetime import datetime, timezone

from aegis_pipeline.openai_policy import OpenAIPurpose, chat_request_policy

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
    """A representative [Katex] expression that references the concept."""
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

    records = _parse(_openai_json(
        system, user, purpose="assessment_generation"))
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
            purpose="assessment_generation",
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
            purpose="assessment_generation",
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
        data = _openai_json(system, user, purpose="source_extraction")
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
   Types classify EVERY distinct assessable question/task pattern under the
   concept — numerical, formula, proof, construction, graph, diagram, reasoning,
   or word-problem patterns as the source demands. Mine the Question / Task
   Inventory first; fold each reusable assessable pattern into the concept it
   assesses. Major concepts that exercises assess MUST carry their own Types —
   do not park them only under Culmination.
   A Type is one solving/answering/task pattern. A Case is a DEFINED conceptual
   sub-type named by the learning objective / problem variety (what is given,
   what is asked, with what constraint) — never a vague label like
   "Definition of …", never a raw question, and never a textbook Activity title.
   Textbook activities, experiments, and discussion cases belong in
   Activity/Info Hub, not as Cases.
   Every concrete source question goes on its own numbered "Example 01:" line
   under the Case it instantiates, copied in FULL without truncation. Restart
   Example numbering at 01 for each Case. Include EVERY source
   example available for each Case; only skip Types when a concept has zero
   meaningful assessable task varieties.""")

prompts.register(
    "concepts.types_guidance.descriptive", category=_CONCEPTS_CAT,
    label="Types classification guidance (all subjects)",
    default="""\
   Types classify EVERY distinct assessable question/task variety under the
   concept: explanation, comparison, reasoning, diagram, data/table/graph, map,
   source, passage, grammar, writing, literature extract, coding/debugging,
   short-answer, long-answer, or numerical patterns as appropriate. Mine the
   Question / Task Inventory first; major concepts that the exercises assess
   MUST carry their own Types — do not dump them only under Culmination.
   A Type is one reusable assessable format. A Case is a DEFINED conceptual
   sub-type named by the learning objective (what is given, what is asked, with
   what constraint or context) — never "Definition of …", never a raw question,
   and never a textbook Activity / discussion-case title. Activities,
   experiments, and classroom discussion cases belong in Activity/Info Hub, not
   as Cases.
   Every concrete source question goes on its own numbered "Example 01:" line
   under the Case it instantiates, copied in FULL without truncation. Restart
   Example numbering at 01 for each Case. Include EVERY source
   example available for each Case; only skip Types when the concept has zero
   meaningful assessable varieties.""")

prompts.register(
    "concepts.types_example", category=_CONCEPTS_CAT,
    label="Types section format example",
    default=(
        "Types: Type 01: <reusable assessable pattern> "
        "Case 01: <conceptual sub-type named by givens/ask/constraint> "
        "Example 01: <full source question verbatim> "
        "Example 02: <another source question for the same Case> "
        "Case 02: <another conceptual sub-type for the same pattern> "
        "Example 01: <another full source question verbatim, with figure URL "
        "when the ask is visual: (Refer fig. X) "
        "[img src=\"https://full-public-image-url\" alt=\"meaningful visual description\"]>"
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
- Optionally add Activity/Info Hub AFTER Description when the concept has
  textbook activities, experiments, discussion cases, or other excess source
  material that is NOT the core teachable idea:
  Activity/Info Hub: <compact activity/experiment/discussion notes>
  Never park that material in Culmination or turn it into vague Cases.
- Then include Types ONLY IF the concept has assessable question/problem
  varieties. {{types_guidance}}
  Format — use zero-padded numeric labels exactly "Type 01:", "Case 01:", and
  a numbered "Example 01:" line for every concrete source question; restart
  Example numbering at 01 for every Case:
  Types: Type 01: <pattern definition> Case 01: <defined sub-type>
  Example 01: <full source question> Example 02: <another full source question>
  Case 02: <defined sub-type> Example 01: <...> Type 02: <next pattern> ...
  Restart at Type 01 within each concept — they are renumbered continuously
  across the whole chapter afterwards, so do NOT try to continue numbers yourself.
- Example Types block:
  {{types_example}}
- Every normal (non-culmination) concept MUST end with exactly one top-level
  learner-analysis section in this exact shape:
  Misconception/ Error Analysis: Misconceptions: <commonly held incorrect
  belief>; Error Analysis: <distinct plausible application/reasoning mistake>
  Both labelled parts are required and non-duplicative. Name the learner
  explicitly and describe the actual belief/action. Never emit separate
  top-level Misconceptions or Error Analysis sections, and never write filler.
- Valid structures:
  Description: ... // Misconception/ Error Analysis: Misconceptions: ...; Error Analysis: ...
  Description: ... // Types: ... // Misconception/ Error Analysis: Misconceptions: ...; Error Analysis: ...
  Description: ... // Activity/Info Hub: ... // Types: ... // Misconception/ Error Analysis: Misconceptions: ...; Error Analysis: ...
- Use " // " as the separator. Do NOT use newlines inside concept_description
  except the Achieving Mastery line inside Description.
- Do NOT mention groups, group columns, or assessment labels — not required here.

TOPIC CULMINATION:
- The LAST concept of every topic is exactly one culmination row that integrates
  that section's ideas (named "Culmination - ..."). Its Description will be set to
  "Recap". Culmination Types are ONLY mixed multi-concept application, revision,
  and synthesis questions — NEVER full textbook activities, experiment write-ups,
  or discussion-case dumps (those belong in Activity/Info Hub on the relevant
  normal concept).

SOURCE HYGIENE:
- NEVER reference source artifacts: no "Example 19", "Examples Type III",
  "Fig 2", "Table no. 1", "ex 1" - inline the actual worked content instead.
- NEVER use the words "MMD" or "MMDs"; say "chapter", "section", "problem".
- In every rich-text section, wrap ALL mathematics exactly as
  [Katex] valid LaTeX [/Katex]. Render every image exactly as
  [img src="https://full-public-image-url" alt="meaningful description"].
  Never emit raw $, $$, \\(...\\), \\[...\\], TeX environments, footnote commands,
  or Markdown image syntax.

QUALITY RULES (universal — apply to ANY chapter/subject; never invent
chapter-specific exceptions):
- Cover the section exhaustively at concept level, but stay within syllabus scope
  (max ~90 words per section of the description).
- keywords: 3-6 comma-separated lowercase terms.
- Infer structure from THIS upload's headings, reading order, and task blocks.
  Review feedback (Activity/Info Hub, omit Overview/Summary, Cases are
  conceptual, Culmination is synthesis-only) is structural and chapter-agnostic.
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
            "assessable formats; end each normal concept with exactly one "
            "Misconception/ Error Analysis section containing both labelled "
            "Misconceptions and Error Analysis meanings. "
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
   items folded into the concept they test). Use zero-padded labels:
   Type 01: <pattern> Case 01: <declarative sub-type definition>
   Example 01: <full question> Example 02: <another question for that Case>
   Case 02: <definition> Example 01: <full question> Type 02: ...
   Cases are definitions, never questions; questions appear only as numbered
   Examples and numbering restarts at 01 for every Case.
   (restart at Type 01 per concept; continuous renumbering happens downstream).
   Only omit Types for concepts that are purely definitional with zero assessable
   formats. If the draft omitted Types where they belong, ADD them.

5. **Culmination.** Every topic ends with exactly one "Culmination - ..." row
   that integrates that topic's ideas. Place it last within its topic.

6. **Preserve order.** Keep textbook reading order for topics and concepts.

7. **No groups.** Do not mention groups, group columns, or assessment labels.

8. **Hygiene.** Keep Description // Activity/Info Hub // Types //
   Misconception/ Error Analysis order; no source-artifact references
   ("Example 19", "Fig 2", "MMD"). Every normal concept must end with exactly
   one ``Misconception/ Error Analysis`` section containing both labelled
   ``Misconceptions:`` and ``Error Analysis:`` meanings. They must be distinct,
   learner-specific, and non-duplicative; never emit either as a separate
   top-level section and never write N/A/None/filler. Activity/Info Hub is optional and holds
   activities / experiments / discussion cases — never Culmination dumps.

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

Your primary job is to make the Description section useful for lesson planning,
assessment building, and downstream content. Your only additional responsibility
is to keep the learner-analysis contract below valid.

Rules:
1. Keep topic names, concept names, keywords, and row order the same.
2. Rewrite the Description section using the CHAPTER SOURCE. Add or repair only
   learner-analysis content that is missing, generic, mislabeled, or duplicated.
3. Do not include Types; the dedicated Types pass adds them later.
4. Preserve useful learner analysis but normalize it so every normal concept
   ends with exactly one top-level section:
   ``Misconception/ Error Analysis: Misconceptions: <incorrect belief>;
   Error Analysis: <distinct application/reasoning mistake>``.
   Both labelled parts are required and learner-specific. Never emit separate
   top-level Misconceptions or Error Analysis sections. Do not write "N/A",
   "None", "Not applicable", or generic filler.
5. Description must be source-grounded, clear, and complete enough to teach from:
   include what the concept means, the key rule/process/relationship, important
   conditions, and one compact example only when it helps.
6. Do NOT dump the full textbook. Target 2-4 compact sentences, roughly 45-90
   words. Avoid repetitive wording across sibling concepts.
   Explain the source in original teacher-facing language: do not copy a long
   contiguous sentence or paragraph from the textbook into Description.
7. Valid concept_description form:
   Description: ... // Misconception/ Error Analysis: Misconceptions: ...; Error Analysis: ...
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
not exist, and one combined Misconception/ Error Analysis section should
already be present on every normal concept) plus CHAPTER SOURCE text.

OUTPUT: Return ONLY JSON {"rows": [{"topic","concept","concept_description","keywords"}, ...]}
with the SAME rows (same topics and concept names) but Types sections filled in.

RULES:
1. Keep each Description and the existing combined learner-analysis text
   UNCHANGED (do not rewrite it).
2. Insert or replace ONLY the Types section. Place it after Description and
   before the combined learner-analysis section:
   Description: ... // Types: ... // Misconception/ Error Analysis:
   Misconceptions: ...; Error Analysis: ...
3. {{types_guidance}}
4. Format — zero-padded numeric labels exactly "Type 01:", "Case 01:", and an
   "Example 01:" line per concrete source question; restart Example numbering
   at 01 for every Case:
   Types: Type 01: <pattern definition> Case 01: <defined sub-type>
   Example 01: <full source question> Example 02: <another full source question>
   Case 02: <defined sub-type> Example 01: <...> Type 02: <next pattern> ...
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
   embed the source image in canonical rich text right after it, e.g.
   "(Refer fig. 11.1) [img src=\"https://full-public-image-url\"
   alt=\"Circuit in Fig. 11.1\"]".
9. Mine ALL assessable problems from the source; skipping exercises, in-text
   checkpoint questions, or activities defeats homework / in-class /
   board-teaching categorisation downstream.
10. Place each question under a concept that is taught at or before the point
    of the chapter where the question appears — NEVER attach a question to an
    earlier concept when it actually assesses later material. Do not dump most
    exercise questions onto the last concept or Culmination.
11. Textbook ACTIVITY / experiment / classroom discussion tasks belong in the
    concept's Activity/Info Hub section (after Description, before Types) — not
    as Cases and not as Culmination Types. Case names must be conceptual problem
    varieties (named by the assessed skill, givens, ask, or constraint), never
    "Definition of …" and never Activity / discussion-case titles.
12. Culmination rows MUST include Types only for mixed multi-concept application,
    revision, and synthesis. Major concepts that exercises assess must keep their
    own dedicated Types — do not park those only under Culmination.
13. NEVER mention groups or group columns.
14. These rules are UNIVERSAL for every upload. Do not invent subject- or
    chapter-specific exceptions from prior examples.""")


prompts.register(
    "concepts.skeleton.system", category=_CONCEPTS_CAT,
    label="Concept skeleton extraction system prompt",
    default="""\
Extract ONLY a clean teachable concept skeleton from a textbook section.
Return ONLY strict JSON:
{"rows":[{"topic":"","parent_concept":"","concept":"","concept_description":"","keywords":"","source_evidence":""}]}.

COVERAGE IS MANDATORY (most important rule):
- Build a compact teacher-facing concept map from the first line to the last.
- Infer the document's teaching structure from its headings, reading order,
  prose, representations, and task blocks. Subject metadata is context only;
  never assume a fixed structure merely because the subject has a familiar
  name. A narrative may be organized like a procedural text, and a quantitative
  text may be organized as episodes or investigations.
- A normal textbook section yields 2-5 concepts; a full chapter usually yields
  18-40 concepts, depending on chapter size. Prefer discrete mastery units
  over broad umbrella concepts.
- A concept is a durable teaching/mastery objective, not every term, example,
  subheading, exercise prompt, case, or factual detail.
- When several definitions, examples, sub-types, or procedures serve one
  reusable objective, merge them under the same concept.
- Keep SEPARATE concepts when the textbook teaches distinct country cases,
  people, events, laws, methods, or processes that a teacher would lesson-plan
  apart — do not collapse them into one umbrella row plus a culmination.
- Chapter-opening / pre-section narrative (HEADING PATH: [Chapter opening])
  MUST yield at least one teachable concept under the first main topic from
  that opening content. Never skip opening material just because it precedes
  section 1.
- Do not create separate concept rows for cases/examples/questions. These are
  captured later as Types/Cases with full source questions.
- Explicit proofs, derivations, algorithms, and reusable methods/procedures are
  durable concepts even when presented inside worked exposition; never reduce
  them to disposable examples. When the input supplies MANDATORY METHOD
  ANCHORS, cover every anchor and copy every anchor_id verbatim into
  source_evidence. Multiple anchors may share one concept when they are steps
  or equivalent forms of the same mastery objective; distinct methods remain
  distinct concepts.
- Derivations and formula-building sequences are method concepts whenever the
  source teaches them as reusable reasoning, independent of the subject label.
- When the source is a story, play, poem, speech, memoir, or other literary
  work, use its own episode/scene/stanza/argument structure. Cover major
  episodes and analytical elements evidenced by the text, including narrative
  development, character/theme, imagery, literary devices, poetic devices,
  form, tone, and point of view. Pedagogy blocks such as pre-reading, oral
  checks, letter-writing practice, and classroom instructions are tasks, not
  literary concepts.
- Classroom discussion cases, dilemma narratives, and textbook Activity blocks
  are NOT separate topics or concepts — capture them later under Activity/Info
  Hub on the related teaching concept (GPT classification; do not invent
  chapter-named filters).
- All worked, numerical, contextual, or real-life problems are inventory items,
  not concept rows. They are classified later into distinct Types/Cases under
  the concept they assess; never include their solutions in the skeleton.
- A missed main teaching objective is a defect; a micro-concept row that should
  be a case/example is also a defect.

TOPIC SEGREGATION IS MANDATORY (second most important rule):
- topic MUST be the textbook MAIN SECTION heading the content sits under (use
  the HEADING PATH / SECTION HEADINGS given with the text); strip section numbers.
- When the textbook nests subsections under a main numbered section, the MAIN
  section is the topic; each subsection becomes a parent_concept cluster (or
  concepts) under that topic — NEVER a topic of its own.
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
  content-specific title for opening material instead of the word "Introduction".
- Do not use exercise/question-type headings as concepts.
- Avoid repeated sibling openers.
- concept_description starts with "Description:" and is 2-4 compact sentences
  that name the key people, places, rules, formulas, or relationships from the
  source — not a vague summary.
- Keep source_evidence short: the phrase/heading/problem source that justifies the concept.
- source_evidence is for validation/debug only and must not be written to workbook.
""")

prompts.register(
    "concepts.missing_topic_recovery.system", category=_CONCEPTS_CAT,
    label="Source-topic concept coverage recovery prompt",
    default="""\
Recover teachable concepts only for source topics that are missing from an
otherwise valid concept map. Return ONLY strict JSON:
{"rows":[{"topic":"","parent_concept":"","concept":"","concept_description":"","keywords":"","source_evidence":""}]}.

Rules:
- Every supplied missing source topic MUST receive at least one normal concept.
- Infer concept grain from that topic's own excerpt and hierarchy, not from the
  subject label or from a conventional textbook template.
- Preserve the supplied topic string exactly. Never create another topic.
- Emit durable teaching/mastery objectives, not headings, examples, exercises,
  raw questions, activities, or culmination rows.
- Cover distinct episodes, cases, processes, methods, representations, or
  analytical elements separately when the excerpt teaches them separately.
- For literary source units, cover the substantive episodes and evidenced
  literary/poetic/analytical elements; do not turn pedagogy instructions into
  concepts.
- Description must start with "Description:" and contain 2-4 source-grounded
  sentences. Include concise literal source_evidence that proves placement.
- Do not repeat any supplied existing concept title.
- Do not emit Types, Cases, Examples, or learner analysis; the dedicated
  refinement passes add those sections after recovery.
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
  enough concepts for lesson planning. Distinct country/case studies, people,
  events, laws, or processes under one topic stay as separate concepts when a
  teacher would lesson-plan them apart.
- Keep one coherent teaching domain per row. Never combine disjoint branches
  that require separate lesson plans into one umbrella concept merely because
  they occur in one headingless chapter or source block.
- When the user supplies MUST-PRESERVE SOURCE-BACKED PARENT FAMILIES, retain at
  least one normal concept for every listed family. Merge only aliases,
  near-duplicates, cases, examples, or narrow fragments within a family.
- Keep chapter-opening concepts (named people, paintings, framing ideas that
  appear before section 1) — do not fold them away into a later section concept.
- Remove a concept when it is a duplicate, pure filler, a structural heading,
  a question/example label, or only a sub-type/case of another concept.
- Rows whose source_evidence contains a METHOD-* anchor are mandatory
  method/procedure coverage. Never drop an anchor ID. Merge anchored rows only
  when they teach the same mastery objective, and carry every merged METHOD-*
  ID plus all distinct source-grounded content onto the surviving row.
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
    "concepts.task_fragment_consolidation.system", category=_CONCEPTS_CAT,
    label="Task-grounded concept fragmentation consolidation prompt",
    default="""\
Consolidate an over-fragmented concept map for ONE source topic. Return ONLY
strict JSON with the same schema:
{"rows":[{"topic":"","parent_concept":"","concept":"","concept_description":"","keywords":"","source_evidence":""}]}.

The draft contains several rows grounded mainly in individual Examples,
Exercises, or question varieties. Those task varieties belong later as
Types/Cases/Examples; they are not automatically separate teaching concepts.

Rules:
- Infer durable mastery objectives from the supplied rows and source excerpt.
- Merge question-grounded rows that apply the same underlying idea/rule/method,
  keeping distinct contexts and asks for the later Types pass.
- A row grounded only by an Example/Exercise is not a durable concept merely
  because its ask changes (direct result, unknown value, recognition first,
  advanced/challenge item, or another difficulty/context). Merge such rows
  into the closest reusable method/application objective.
- For one underlying rule, normally retain at most one direct-application
  concept and one genuinely distinct contextual/modeling concept. Further
  givens, asks, constraints, and difficulty levels become Types and Cases.
- Keep a distinct application/modeling concept only when the source teaches a
  genuinely different transferable objective, not merely another question
  pattern or difficulty label ("advanced", "challenge", "unknown quantity").
- Preserve distinct definitions, derivations, representations, procedures, and
  conceptual relationships that require separate teaching.
- Preserve every METHOD-* ID. When anchored rows teach one objective, merge
  them and carry every ID plus all distinct formulas/evidence onto one row.
- Equivalent formula forms, notation changes, and links taught inside the same
  derivation normally belong together; separate them only when the source
  gives each a distinct reusable method or lesson-planning objective.
- Preserve the exact supplied topic and reading order. Do not create or remove
  topics, Types, Cases, Examples, culmination rows, or filler concepts.
- Keep source-grounded Description text, keywords, and meaningful parent
  concepts on every surviving row.
""")

prompts.register(
    "concepts.description_refine.system", category=_CONCEPTS_CAT,
    label="Description-only concept refinement system prompt",
    default="""\
You are a description-only editor for a refined concept map, with one narrow
learner-analysis repair responsibility.
Return ONLY strict JSON:
{"rows":[{"topic":"","parent_concept":"","concept":"","concept_description":"","keywords":""}]}.

Rules:
- Keep topic, parent_concept, concept name, keywords, and row order unchanged.
- Rewrite the Description section. You may also add or repair only the learner-
  analysis tail needed to satisfy the contract below.
- Preserve any existing Activity/Info Hub exactly. Do not include Types; the
  dedicated Types pass adds them later. Preserve valid learner analysis, but
  normalize its public shape to the single combined section required below;
  repair only missing, generic, mislabeled, overlapping, or misclassified
  content. Do not move activities into Description or Culmination.
- Description answers: what the concept is; what rule/process/relationship/method matters;
  when/why it is used. Ground it in the source: name the key people, places,
  dates, formulas, quantities, conditions, and causal links that a teacher
  needs — do not stop at a vague one-sentence gloss.
- Never cite textbook section numbers in Description (for example "Section
  5.2" or "§2.1"). State the actual idea instead.
- END every Description with a mastery statement on its OWN line — a literal
  line break (\\n) followed by exactly this format:
  Achieving Mastery: <one short sentence stating what the learner can do when this concept is mastered>
  Example ending: "...\\nAchieving Mastery: Using the midpoint property to set up the smaller triangles correctly."
- Use 45-90 words unless the concept is very simple. Never leave a Description
  truncated mid-sentence.
- Paraphrase source prose into original teacher-facing language. Do not copy a
  long contiguous sentence or paragraph from the textbook into Description.
- A derivation/proof/formula-building concept MUST include one compact,
  source-grounded worked derivation cue introduced with "Worked Example:".
  It must demonstrate that derivation's reasoning, not merely apply or verify
  the finished result.
- Do not include Types; the dedicated Types pass adds them later.
- Every non-culmination concept must end with exactly one top-level section in
  this exact form:
  Misconception/ Error Analysis: Misconceptions: <commonly held incorrect
  belief or interpretation>; Error Analysis: <plausible procedural,
  computational, representational, or reasoning mistake while applying it>
  Both labelled parts are required, must be distinct and non-duplicative, and
  must name the learner explicitly (for example, "Students may assume ..." /
  "Students may omit ..."). Never emit separate top-level "Misconceptions:" or
  "Error Analysis:" sections, and never use filler.
- Write the mastery statement exactly ONCE, at the end of the Description —
  never repeat it inside or after either learner-analysis section.
- No N/A, None, Not applicable, or placeholder text.
- No source artifacts such as MMD, Example 3, Fig 2, Table 1, Exercise 1.1, or
  page references. When the source text cites one, substitute the full actual
  content it points to (the real numbers, expression, conditions, or task) —
  e.g. write "such as expressing 1.272727... as 14/11", never "as in Example 8".
- Do NOT embed image URLs in Description. Describe visual content
  in words here; image URLs belong only in Types Example lines (with their
  figure reference).
- Wrap every mathematical expression exactly as [Katex] valid LaTeX [/Katex].
  Never emit raw math delimiters or raw TeX outside those tags.
""")

prompts.register(
    "concepts.types_assign.system", category=_CONCEPTS_CAT,
    label="Types-only concept assignment system prompt",
    default="""\
You are a Types-only classifier. Assign Types only for assessable concepts.
Return ONLY strict JSON:
{"rows":[{"topic":"","parent_concept":"","concept":"","concept_description":"","keywords":""}]}.

Rules:
- Preserve Description and any existing Activity/Info Hub exactly.
- Preserve topic, parent_concept, concept title, keywords, and row order exactly.
- Insert or replace only Types.
- Use the provided Question / Task Inventory and mined Types as the primary evidence.
- One Type = one distinct reusable assessment/task pattern evidenced by the
  source. Infer patterns from the actual action, object, representation,
  givens, constraints, and expected response—not from the subject label.
- One Case = one defined conceptual sub-type named by the learning objective
  (givens / ask / constraint / context). Never "Definition of …", never a raw
  question, and never a textbook Activity or discussion-case title. Multiple
  source questions with the same action/object/method belong to one Type;
  differences in givens, ask, representation, or constraint become Cases under
  that Type.
- Major concepts assessed by exercises MUST receive their own Types — do not
  park those only under Culmination.
- Textbook Activity / experiment / discussion tasks belong in Activity/Info Hub,
  not as Types/Cases.
- Omit Types only for concepts with zero meaningful assessable question/task varieties.
- If a Type is present, every Case must include a full self-contained numbered
  "Example 01:" question from the source. Restart Example numbering at 01 for
  each Case. Do not shorten source questions; preserve all
  given values, conditions, data, quotations, and the exact ask needed for a
  teacher to execute the example.
- Include as many source examples as are available for each Type. Skip only
  purely introductory or rhetorical prompts with no expected student response.
- Culmination rows may receive Types only for mixed multi-concept synthesis /
  revision / application; keep their Description ("Description: Recap") unchanged.
- Use zero-padded labels exactly "Type 01:", "Case 01:", and "Example 01:".
- Do not rewrite the existing canonical ``Misconception/ Error Analysis``
  section except to keep it after Types. Never split it into separate top-level
  Misconceptions and Error Analysis sections.
- Do not include source labels such as "Example 3" or "Exercise 1.2" in public concept_details.
- When an Example refers to one or more figures, preserve every figure
  reference and place the matching source image tag directly in that same
  Example, using exactly [img src="https://..." alt="..."]. Never attach an
  adjacent but unreferenced figure, and never place the URL only elsewhere in
  the concept.
""")

prompts.register(
    "concepts.question_task_inventory.system", category=_CONCEPTS_CAT,
    label="Universal Question / Task Inventory extraction prompt",
    default="""\
Extract a universal Question / Task Inventory from an uploaded school-subject chapter.
This is subject-agnostic and board-agnostic: Mathematics, Science, Social Science,
languages, literature, Computer Science, practical work, and any school subject.

Return ONLY strict JSON:
{"items":[{"qid":"QINV-0001","source_kind":"worked_example|solved_example|exercise|intext_question|checkpoint_question|activity|mcq|fill_blank|true_false|match|assertion_reason|diagram_task|map_task|table_task|graph_task|source_task|case_task|passage_task|grammar_task|writing_task|experiment_task|coding_task|long_answer|short_answer|other","source_label":"","parent_source_label":"","topic_hint":"","page_hint":"","block_ids":[],"raw_task":"","raw_solution_or_answer":"","normalized_task":"","shared_context":"","subpart_label":"","options":[],"image_urls":[],"content_objects":{"numbers":[],"variables":[],"equations":[],"coordinates":[],"ratios":[],"diagrams":[],"graphs":[],"tables":[],"maps":[],"passages":[],"sources":[],"experiments":[],"observations":[],"characters":[],"events":[],"dates":[],"places":[],"terms":[],"definitions":[],"processes":[],"comparisons":[],"causes":[],"effects":[],"code_snippets":[],"grammar_items":[],"unknowns":[],"given_values":[],"conditions":[]},"requires_visual":false,"requires_context":false,"order_index":1}],"stats":{"worked_examples":0,"solved_examples":0,"exercise_questions":0,"checkpoint_questions":0,"activities":0,"objective_items":0,"subjective_items":0,"descriptive_items":0,"subparts":0,"visual_tasks":0,"table_or_graph_tasks":0,"source_or_passage_tasks":0,"total_inventory_items":0}}.

COVERAGE IS MANDATORY (most important rule):
- Extract EVERY assessable question/task from the first line to the last,
  including the chapter opening / pre-section narrative.
- Each numbered problem, intext question, think-and-reflect prompt, and worked
  example is its OWN item — never summarize an exercise set or question list
  into one item.
- Keep every numbered parent question and all of its lettered/roman subparts
  as ONE atomic inventory item. Preserve the complete shared stem, data,
  passage, diagram, options, and every subpart in source order. If the parent
  genuinely assesses several concepts, assign the intact task to a suitable
  Culmination later; never duplicate or split its children across concepts.
- In-text CHECKPOINT questions (boxed "?" questions, "Let's recall",
  "Check your progress", mid-section question boxes) are inventory items
  exactly like end-of-chapter exercises. Chapters typically carry a dozen or
  more of them — walk every section and capture each one. Missing even one
  checkpoint is a defect.
- Picture-/source-/map-based questions (including opening-page source analysis
  of chapter illustrations, prints, maps, or passages) are inventory items with
  source_kind "source_task" / "diagram_task" / "map_task" as appropriate —
  never skip them as "introductory".
- Textbook ACTIVITY / experiment / classroom-discussion blocks are inventory
  items with source_kind "activity" or "experiment_task" as appropriate — they
  later feed Activity/Info Hub on the related teaching concept, never Culmination.
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
- For MCQ/objective items, raw_task MUST include the stem and every option in
  the original order. Never borrow options from an adjacent question. Also
  return options as an ordered list when the source exposes discrete choices;
  the inventory sanitizer uses it to verify/rebuild the public prompt.
- Inventory prompts only, never worked answers: stop each worked/solved example
  immediately before "Solution:" or "Answer:", and always return
  raw_solution_or_answer as an empty string. Types must expose questions, not
  answer keys or textbook solutions.
- Inventory every worked, numerical, contextual, interpretive, literary,
  source-based, procedural, practical, and real-life task as its own item,
  including assessable prompts embedded in explanatory prose. Capture complete
  givens, context, quotations, representations, and asks, but never solutions.
- When the question depends on a figure/diagram/table image, copy the Mathpix
  image URL(s) from the source
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
    "concepts.opening_recovery.system", category=_CONCEPTS_CAT,
    label="Chapter-opening concept coverage audit prompt",
    default="""\
Audit whether substantive chapter-opening material is represented by the existing
concept rows. The opening is source content that appears before the first numbered
main topic; it is not a generic request to create an "Introduction" concept.

Return ONLY strict JSON:
{"missing_rows":[{"parent_concept":"","concept":"","concept_description":"","keywords":[]}]}

Rules:
- Return an empty missing_rows list when every durable teachable idea in the
  opening is already represented by an existing row, even under different words.
- Otherwise return only genuinely missing concepts grounded in the supplied
  opening excerpt. A distinctive source, person, visual, event, worked idea, or
  framing that the chapter explicitly teaches may be a concept.
- Do not create rows for vocabulary lists, source labels, Activities, questions,
  figure numbers, decorative visuals, previews, summaries, or editorial matter.
- Do not duplicate or paraphrase an existing concept.
- Each concept_description starts with "Description:" and explains the actual
  source-grounded idea in 2-4 compact sentences. Do not cite section/figure/page
  numbers or mention the upload format.
- State keywords as 3-6 concise terms. Never create a Culmination row.
""")

prompts.register(
    "concepts.type_mining.system", category=_CONCEPTS_CAT,
    label="Universal Type Mining prompt",
    default="""\
Classify the Question / Task Inventory into reusable academic Types appropriate
to the source chapter. A Type is a reusable assessment/task pattern found in
the source. A Case is a DEFINED sub-type of that pattern (what is given,
what is asked, with what constraint) — never a raw question. An Example is one
concrete source question that instantiates a Case, copied in full.

Return ONLY strict JSON:
{"types":[{"type_id":"TYPE-0001","type_title":"","type_description":"","task_pattern":"","source_question_ids":["QINV-0001"],"case_prompts":[{"case_id":"CASE-0001","case_title":"","examples":[{"source_question_id":"QINV-0001","example_prompt":""}],"case_signature":"","placement_scope":"normal|mixed_synthesis|cross_topic_synthesis"}],"concept_match_hint":"","parent_concept_match_hint":"","topic_match_hint":"","difficulty_hint":"Basic|Intermediate|Advanced","cognitive_skill_hint":"","subject_skill_hint":"","is_activity":false,"placement_scope":"normal|mixed_synthesis|cross_topic_synthesis"}]}.

COVERAGE IS MANDATORY (most important rule):
- EVERY inventory item MUST appear in EXACTLY ONE Type's source_question_ids
  AND EXACTLY ONE example_prompt under a Case. The same qid/question must
  never appear in two Types, two Cases, or twice in the same Case.
- NEVER skip an item because it looks trivial, routine, descriptive, or hard to
  classify. If an item fits no existing Type, CREATE a new Type for it.
- In-text checkpoint questions, boxed "?" questions, and textbook activities
  count exactly like exercise questions — every one of them must be classified.
- Coverage and classification quality are both mandatory. Never drop an item,
  but do not create one Type per question when several questions instantiate
  the same reusable pattern; group them into Cases/Examples.
- A missed question is a defect; an unnecessary one-question Type is also a
  classification defect when that question fits an existing reusable pattern.

Rules:
- One inventory item maps to exactly one best-fit Type. If it combines several
  skills, choose the Type that most directly assesses the final ask, or create
  one integrated Type for that mixed skill — never duplicate the question.
- Every Case and Example inside one Type MUST assess the same single granular
  concept. concept_match_hint is Type-level, so it must accurately name that
  one shared concept target; never use one Type as an umbrella for Cases that
  belong to different concept rows.
- Split Types when questions share a formula or surface procedure but assess
  different concepts. In particular, direct formula calculations and
  contextual/real-life modeling or applications belong in separate Types when
  the concept map teaches them as separate rows.
- Classify every worked, numerical, contextual, interpretive, source-based,
  procedural, practical, and real-life task by its assessed action, object,
  representation, givens, constraint, and ask. Preserve the complete prompt as
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
- Set "is_activity": true when the Type groups textbook Activity / experiment /
  classroom discussion tasks. These are NOT assessable Types/Cases — they are
  later stored under Activity/Info Hub on the related concept. Case titles for
  non-activity Types must be conceptual problem varieties, never Activity names.

CASE WORDING (each Case must be properly defined):
- case_title DEFINES the sub-type: what is given to the student, what must be
  done, and the distinguishing condition — named by givens / ask / constraint /
  representation, never by a chapter-specific Activity title. A case_title is
  NEVER a raw question.
- Create a separate Case for every distinct given/asked/constraint combination.
- A numbered parent question with subquestions stays ONE Example under ONE
  Case. Do not split the same parent across Types, Cases, or concepts, and
  never repeat its stem with different children.
- Set each Case's placement_scope to "normal" when it assesses one concept.
  Use "mixed_synthesis" ONLY when that Case genuinely combines several concepts
  from the same topic into synthesis/revision. A broad Type title does not make
  every Case mixed. Type-level placement_scope is only a default; Case-level is
  authoritative.
- Use "cross_topic_synthesis" ONLY when the Case genuinely combines concepts
  taught in two or more different source topics and fits neither one ordinary
  concept nor a single-topic Culmination. Such a Case may be assigned only to
  the Culmination of the later source topic, never to an earlier topic.

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
  figure reference and append the source image URL immediately after it using
  the canonical rich-text image tag, e.g.
  "Calculate the resistance for the given circuit. (Refer fig. 11.1)
  [img src="https://full-public-image-url" alt="Circuit in Fig. 11.1"]".
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
- Infer the taxonomy from source tasks. It may include numerical/formula work,
  proof/reasoning, diagram/experiment/observation, cause-effect/comparison,
  source/map/data/chronology, comprehension/extract/literary/poetic analysis,
  grammar/writing, code tracing/debugging/algorithm design, case application,
  or structured explanation.
- Use subject_skill_hint values such as Mathematical Calculation, Algebraic
  Reasoning, Diagram Interpretation, Experimental Inference, Conceptual
  Explanation, Definition Recall, Comparative Analysis, Source Interpretation,
  Map Skill, Data Interpretation, Grammar Transformation, Literary
  Interpretation, Code Tracing, Algorithm Design, Case Application, or
  Long-Answer Structuring.
- Use Diagram Interpretation only when the learner must read a supplied visual
  and the owned inventory item has requires_visual=true. Observing a real-world
  process is not Diagram Interpretation.
""")

prompts.register(
    "concepts.chapter_wide_task_topics.system", category=_CONCEPTS_CAT,
    label="Chapter-wide task topic assignment prompt",
    default="""\
Assign each chapter-wide review/exercise task to the ONE source topic whose
concepts it most directly assesses. Return ONLY strict JSON:
{"assignments":[{"qid":"QINV-0001","topic":"exact supplied topic"}]}.

Rules:
- Return every supplied qid exactly once and invent no qids.
- Use only exact topic strings from SOURCE TOPICS.
- Base placement on the complete task wording and the supplied concepts/source
  excerpts, never on the physical location of an end-of-chapter exercise.
- A generic final Exercises/Questions/Review block may assess any earlier topic.
- For a mixed task, choose the topic containing its final or dominant assessed
  objective. Never place all tasks on the last topic merely because the review
  block follows it.
""")

prompts.register(
    "concepts.type_semantic_consolidation.system", category=_CONCEPTS_CAT,
    label="Semantic Type consolidation prompt",
    default="""\
Consolidate semantically equivalent mined Types without changing source
question coverage. Return ONLY strict JSON using the same complete schema:
{"types":[{"type_id":"TYPE-0001","type_title":"","type_description":"","task_pattern":"","source_question_ids":[],"case_prompts":[{"case_id":"CASE-0001","case_title":"","examples":[{"source_question_id":"QINV-0001","example_prompt":""}],"case_signature":"","placement_scope":"normal|mixed_synthesis|cross_topic_synthesis"}],"concept_match_hint":"","parent_concept_match_hint":"","topic_match_hint":"","difficulty_hint":"","cognitive_skill_hint":"","subject_skill_hint":"","is_activity":false,"placement_scope":"normal|mixed_synthesis|cross_topic_synthesis"}]}.

Rules:
- Every supplied source_question_id must remain exactly once in exactly one
  Example and one Type. Never add, remove, duplicate, paraphrase, or truncate
  an Example.
- Merge Types when their verb/action, assessed object, required method,
  representation, constraints, and expected output describe the same reusable
  assessment pattern, even when their titles are paraphrases.
- Keep different methods or learning objectives separate. Shared notation,
  formula, difficulty, context, person, country, or surface wording alone does
  not prove equivalence.
- Never merge across topic_match_hint, activity status, or incompatible
  placement_scope. A Type remains source-topic scoped.
- When merging, choose one precise action-object-method title and definition;
  preserve all distinct Cases in source order.
- Do not create generic fallback titles such as "Answering a Checkpoint
  Question", "Direct Questions", "Word Problems", or "Miscellaneous".
""")

prompts.register(
    "concepts.concept_type_sufficiency.system", category=_CONCEPTS_CAT,
    label="Concept sufficiency for mined Types prompt",
    default="""\
Audit whether the supplied normal concept Descriptions teach every distinct
method required by their source-topic mined Types. Return ONLY strict JSON:
{"additions":[{"after_concept_id":"CONCEPT-0001","topic":"","parent_concept":"","concept":"","concept_description":"","keywords":"","supporting_type_ids":["TYPE-0001"]}]}.

Rules:
- Return an empty additions list when existing concepts already teach the
  Type's action, inputs, method, conditions, and expected output.
- Add a concept only when one or more Types require a genuinely distinct,
  reusable method/objective that no existing Description in that exact topic
  can teach. Different givens, context, wording, or difficulty are Cases, not
  concepts.
- Use only supplied topic strings, concept_id values, and type_id values.
  Insert immediately after the closest prerequisite concept in the same topic.
- Never add a Culmination, Overview, Summary, question label, example, person/
  country micro-row, or one-concept-per-question fragment.
- concept_description starts with "Description:", is 2-4 compact
  source-grounded sentences, and fully explains the missing method. Include no
  Types; a later ID assignment pass adds them.
- When the added concept derives, proves, or establishes a formula/rule,
  include a compact source-grounded "Worked Example:" cue that demonstrates
  the derivation before any "Achieving Mastery:" line.
- Wrap mathematics as [Katex] valid LaTeX [/Katex]. Never emit raw math/TeX.
""")

prompts.register(
    "concepts.type_host_review.system", category=_CONCEPTS_CAT,
    label="Type host entailment review prompt",
    default="""\
Review every case-scoped Type assignment against the allowed concept
Descriptions. Return ONLY strict JSON:
{"assignments":[{"type_id":"TYPE-0001::CASE-0001::0001","concept_id":"CONCEPT-0001","reason":"host description teaches this exact method and output"}]}.

Rules:
- Return every supplied opaque type_id exactly once, character-for-character,
  including every ``::CASE-...::...`` suffix; never shorten it to its original
  parent Type ID and invent no IDs.
- Choose only from that unit's allowed_concept_ids.
- A host is valid only when its title and Description teach the concrete
  Examples' assessed action, inputs, method/approach, constraints, and expected
  output. Formula or keyword overlap alone is not entailment.
- Prefer the most granular method/application/modeling concept. Do not file a
  task under a nearby definition, broad formula, partial-sum relation, or
  final concept merely for convenience.
- Use actual Case/Example wording over a broad or misleading Type title.
- Ordinary and activity units never go to Culmination. A mixed-synthesis unit
  goes to Culmination only when it genuinely combines multiple taught
  concepts; cross-topic synthesis may use only an allowed later Culmination.
- Worked derivation tasks belong with the concept teaching that derivation;
  merely applying or verifying the finished formula does not.
- Keep parent questions and all of their dependent subparts together.
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
{"types":[{"type_id":"TYPE-0001 or NEW-TYPE-0001","type_title":"","type_description":"","task_pattern":"","source_question_ids":["QINV-0001"],"case_prompts":[{"case_id":"existing CASE id or NEW-CASE-0001","case_title":"","examples":[{"source_question_id":"QINV-0001","example_prompt":""}],"case_signature":"","placement_scope":"normal|mixed_synthesis|cross_topic_synthesis"}],"concept_match_hint":"","parent_concept_match_hint":"","topic_match_hint":"","difficulty_hint":"Basic|Intermediate|Advanced","cognitive_skill_hint":"","subject_skill_hint":"","is_activity":false,"placement_scope":"normal|mixed_synthesis|cross_topic_synthesis"}]}.

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
- Append to an existing Type only when the missed item assesses the same
  granular concept as every existing Case in that Type. Because
  concept_match_hint applies to the whole Type, create a new Type when the
  missed item instead assesses a distinct method, application, or contextual
  modeling concept, even if it uses the same formula.
- Cover every provided missed qid, but emit no unchanged Type, Case, or Example.
""")

prompts.register(
    "concepts.type_embedding.system", category=_CONCEPTS_CAT,
    label="Universal Type-to-concept assignment prompt",
    default="""\
Assign every mined Type assignment unit to the concept it best belongs to. You
are given a list of concepts (each with a stable concept_id) and mined Type
assignment units (each with a stable type_id). A multi-Case mined Type is
expanded before this call into one case-scoped assignment unit per Case; all
Examples belonging to that Case stay together. Legacy and single-Case Types
remain one unit with their original type_id.

Return ONLY strict JSON:
{"assignments":[{"concept_id":"CONCEPT-0001","type_ids":["TYPE-0001","TYPE-0002"]}]}.

Rules:
- Every provided type_id MUST be assigned to exactly one concept_id.
- Never invent concept_id or type_id values; use only the ones provided.
- Treat type_id as an opaque assignment-unit ID. A case-scoped ID identifies
  the one Case carried by that unit, not the whole original multi-Case Type.
- Choose from the unit's actual Case, all of its Examples, and its
  source_question_ids. Never split Examples within one Case across concepts.
- The original Type title, description, and concept hints are supporting
  context. When they are broad or conflict with the sole Case, the Case and its
  concrete Examples determine the most specific concept.
- When a mined Type includes allowed_concept_ids, its source topic is proven:
  assign it to exactly one of those concept IDs and never any other concept.
- allowed_concept_ids are also placement-scope-safe: ordinary Cases never
  include Culmination; mixed_synthesis Cases may include their source topic's
  Culmination; cross_topic_synthesis Cases may additionally include only
  later-topic Culminations. Never invent or reuse a concept ID excluded from
  that list.
- When previous_rejections is present on a Type unit, correct the stated error;
  do not repeat the rejected concept_id or omit that type_id again.
- A concept may receive multiple type_ids; a Type belongs to one concept.
- Choose the concept that the Type most directly assesses from its actual
  source task, regardless of the subject label.
- Within the already-constrained source topic, honor concept_match_hint and
  parent_concept_match_hint at the most granular level. Prefer the specific
  application, modeling, procedure, or worked-method concept that matches the
  Type's Cases over a broad definition, general formula, or culmination row.
- Formula overlap is not concept identity: direct formula calculations belong
  with the direct-calculation concept, while contextual/real-life applications
  belong with the granular application/modeling concept when that row exists.
- Assign each direct, counting, contextual/real-life, diagram, worked, or mixed
  Case unit to the concept the problem actually assesses, using its Examples;
  never choose a nearby formula merely because it shares notation.
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
- A cross_topic_synthesis Case genuinely spans concepts taught in different
  source topics. First prefer an ordinary concept or its source topic's
  Culmination when either is a truthful fit. Only when neither fits may it go
  to the Culmination of the LATER source topic represented in the task. Never
  send it to an earlier topic or to a later ordinary concept.
- Types flagged "is_activity": true group textbook Activity / experiment /
  discussion tasks; assign them to the related NORMAL concept (for Activity/Info
  Hub), never to Culmination. Culmination only receives mixed multi-concept
  synthesis / revision Types.
- Case titles must name the conceptual problem variety (what is given / asked),
  not "Definition of …" and not a textbook Activity title.
- Major concepts assessed by exercises must receive their own Types; do not park
  those Types only on Culmination.
- Do not drop any type_id. If evidence is ambiguous, use concept_match_hint,
  source order, and the concrete Case action/ask; never collapse unrelated
  assignment units onto one broad or final concept for convenience.
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
  Mastery, the single ``Misconception/ Error Analysis`` section, topic,
  parent_concept, concept, and keywords intact.
- Every inventory qid/question must appear exactly once as an Example under
  exactly one Case in exactly one concept. Missing qids are defects. Duplicate
  qids/questions are defects.
- A Type must belong to the concept it directly assesses. Use topic_hint,
  concept_match_hint, parent_concept_match_hint, topic order, and the actual
  question wording. Do not attach a later-section question to an earlier
  concept just because formulas overlap.
- If a question combines several concepts from one topic, place it on that
  topic's culmination concept. If it genuinely spans concepts across different
  source topics and fits neither an ordinary concept nor one topic's
  Culmination, it may go to the later source topic's Culmination. Textbook
  Activity / experiment / discussion tasks belong in Activity/Info Hub on the
  related normal concept — not as Culmination Cases.
- Cases are defined conceptual sub-types named by learning objective; Examples
  are full source questions. Do not turn a raw question or Activity title into
  a Case name (avoid "Definition of …").
- Keep all full question wording, subquestions, values, units, conditions, and
  canonical [Katex]/[img] content. Never truncate.
- When a question names one or more figures, every matching source image URL
  must remain embedded in that same Example as a canonical
  [img src="https://..." alt="..."] tag. Do not substitute a nearby figure.
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
  - Use ONLY normal concept names from that exact topic; never leak a concept
    from an earlier/later topic into the title or metadata.
- Description must be exactly: "Description: Recap" (the final output expands
  it automatically to "Recap of <every merged concept in the topic>").
- Do not invent starter Types. A later inventory-backed assignment pass adds
  Culmination Types only when the source contains a genuine mixed
  multi-concept application/revision/synthesis task.
- parent_concept must be "Culmination".
- Do not create culmination during chunk extraction; this pass runs only after the full topic map exists.
""")

prompts.register(
    "concepts.activity_hub.system", category=_CONCEPTS_CAT,
    label="Activity/Info Hub population system prompt",
    default="""\
Place textbook activities, experiments, and classroom discussion cases into
Activity/Info Hub on the correct teachable concepts.

These rules are UNIVERSAL for every upload (any board, subject, or chapter).
Infer placement from THIS chapter's concept map and inventory — never invent
chapter-named shortcuts.

Return ONLY strict JSON:
{"placements":[{"concept_id":"CONCEPT-0001","qid":"QINV-0001","hub_note":""}]}.

Rules:
- Activity/Info Hub holds excess classroom material that is NOT the core
  teachable idea: numbered Activity / experiment / lab procedures, discussion
  dilemmas, think-and-discuss prompts, and similar excess tasks.
- Never place that material on Culmination rows (is_culmination true).
- Never turn Activity titles or discussion-case titles into Topics, concept
  names, Types, or Cases.
- Choose the NORMAL concept whose teaching content the activity or discussion
  practices or illustrates. Prefer topic_hint alignment when it is reliable.
- Every supplied pending inventory qid MUST appear in exactly one placement.
- hub_note is a compact teacher-facing note: at most two short sentences and
  55 words, retaining only the activity's purpose, essential setup/action, and
  expected observation/discussion. Do not copy the full procedure, textbook
  prose, source heading, or full assessable question; that remains in its Type
  Example on the same concept when applicable.
- Use only provided concept_id and qid values.
- If several activities belong to one concept, return one placement per qid
  (same concept_id allowed).
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
- Preserve valid fields, including parent_concept, Types, and useful learner
  analysis.
- Every normal concept must finish with exactly one top-level section:
  ``Misconception/ Error Analysis: Misconceptions: <incorrect belief>;
  Error Analysis: <distinct mistaken action>``. Both labelled parts are
  required, learner-specific, and non-duplicative. Never emit separate
  top-level Misconceptions or Error Analysis sections. Culmination rows are
  exempt.
- Do not rewrite the full chapter unnecessarily.
- Never add filler.
- Keep strict JSON.
- For source_artifact issues (references like "Example 5", "Exercise 1.2",
  "Fig 6.4", "fig.11.1", "page 14"): NEVER just delete or reword the reference.
  Look the label up in the provided source context and substitute the FULL
  actual content: the real numbers, expressions, equations, data, conditions,
  and task, e.g. "solve the problem in Exercise 1.5" becomes
  "rationalise the denominator of 1/(7 + 3*sqrt(2))".
  A figure/table reference WITH its canonical [img] tag embedded right after it
  is valid content — keep it (in Types Example lines). Never leave a
  Description truncated mid-sentence while fixing artifacts.
- Image URLs belong in canonical [img] tags on Types Example lines next to the figure
  reference. Do not put image URLs in the Description section; describe the
  visual in words there instead.
- For merged_description issues (one cell carrying two or more concepts'
  "Description:" blocks): keep ONLY the content belonging to THIS row's
  concept — rewrite the cell so it describes exactly one concept. NEVER
  delete the other concept's material blindly; if it clearly belongs to a
  different provided row, move it there.
- For verbatim_source_description issues: retain the same facts but rewrite
  the Description in original teacher-facing language. Do not reproduce a
  long contiguous sentence or paragraph from the supplied source. This rule
  applies only to Description; full source questions remain allowed in Types
  Examples.
- For description_truncated_clause issues: complete or rewrite only the broken
  Description sentence using the supplied source context. Never guess missing
  words, and preserve all valid surrounding Description, Types, Activity/Info
  Hub, mastery, and learner-analysis content.
- For rich_text_format issues: preserve the mathematical expression exactly,
  but wrap every bare equation or LaTeX fragment as
  ``[Katex] valid LaTeX [/Katex]``. Never leave raw ``$``/``$$`` delimiters,
  ``\\(...\\)``/``\\[...\\]`` delimiters, TeX commands, subscripts, or
  superscripts outside a canonical Katex span.
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
- Do not add Types or alter the existing single ``Misconception/ Error
  Analysis`` section. No source artifacts (Example 3, Exercise 1.2, Fig 4,
  page numbers) and never the words "MMD"/"MMDs".
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
  Examples, and retain all specific learner-analysis items.
- Keep the single mastery line and canonical order:
  ``Description: ... // Types: ... // Misconception/ Error Analysis:
  Misconceptions: ...; Error Analysis: ...``. Both distinct labelled parts are
  required inside that one top-level section.
- Never invent new content; only reorganize what the rows carry.
""")

prompts.register(
    "concepts.method_worked_example.system", category=_CONCEPTS_CAT,
    label="Derivation/method worked-example prompt",
    default="""\
Add one compact source-grounded worked reasoning cue to each supplied
derivation/proof/formula-building concept. Return ONLY strict JSON:
{"rows":[{"topic":"","parent_concept":"","concept":"","concept_description":"","keywords":""}]}.

Rules:
- Return the same rows, names, topics, parent concepts, keywords, and order.
- Rewrite only the Description body enough to add exactly one explicit
  "Worked Example:" cue before the final Achieving Mastery line.
- The cue must demonstrate the concept's own derivation or method step by
  step from the supplied anchor evidence. Merely applying, checking, or naming
  the finished formula does not count.
- Keep it compact and source-grounded; never invent values or conditions.
- Preserve the existing Achieving Mastery line at the end.
- Wrap every expression exactly as [Katex] valid LaTeX [/Katex]. Never emit
  raw math delimiters, source labels, figure/page references, or Types.
""")

prompts.register(
    "concepts.misconceptions.system", category=_CONCEPTS_CAT,
    label="Missing/generic learner-analysis writer system prompt",
    default="""\
Write or repair the single learner-analysis section for concept rows.
Return ONLY strict JSON:
{"rows":[{"concept":"","misconception":"","error_analysis":""}]}.

Rules:
- Each provided normal row is missing usable learner analysis, carries generic
  filler, or duplicates the same issue across labels. Return one compact object
  for EVERY supplied concept, preserving its exact ``concept`` title. Do not
  repeat or rewrite Description, Activity/Info Hub, Types, topic, or keywords.
- ``misconception`` states one commonly held incorrect belief or interpretation
  in learner voice, such as "Students may believe/think/assume ..." or
  "Students may confuse ..."; do not write the correction as the belief.
  ``error_analysis`` states one plausible procedural, computational,
  representational, or reasoning mistakes while applying the concept, naming
  the learner and the actual mistaken step or action. Both fields are required
  and must be distinct.
- For Misconceptions, avoid "should", "instead", "correctly", "remember that",
  and declarative textbook corrections such as "A nation is not ...". The
  Description already teaches the correct idea.
- For Error Analysis, do not use belief verbs such as believe, think, assume,
  interpret, confuse, or treat. Name a concrete faulty action or reasoning
  step instead, such as omitting evidence, changing two variables, recording
  the wrong observation, comparing unlike cases, or drawing a conclusion from
  one trial.
- The Misconception and Error Analysis must concern two different learner
  problems; do not restate the same issue with different wording.
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
- You are given the concept rows and grouped SOURCE TOPIC EXCERPTS in reading
  order. Each excerpt includes all source blocks inherited by that main topic,
  including worked examples, solutions, exercises, and structural subheadings.
  Reassign ONLY the topic of each row.
- Topic names must be the given source headings VERBATIM (only the section
  number stripped) — never invent, rename, merge, or paraphrase headings.
- The given headings are the MAIN sections. When a concept comes from a
  subsection, file it under its MAIN section heading — subsections are never
  topics.
- Keep EVERY row: same concept names, descriptions, keywords, and
  parent_concept, in the same relative order. Never add, drop, merge, split,
  or rename concepts.
- Use several topics — a chapter is never one topic. Cover the chapter's full
  span: rows from tail sections belong to those tail headings, not to an
  earlier catch-all.
- Assign each concept to the section whose content teaches it; consecutive
  concepts usually stay in the same section until the source moves on.
- Use each row's source_evidence against the grouped excerpts. Formulas,
  reusable worked methods, contextual/real-life applications, and
  exercise-derived concepts belong to the section that actually teaches or
  uses that evidence—not automatically to the preceding topic or an
  unnumbered chapter-title section.
- Do not create exercise, example, review, or practice topics.
- Do not use an unnumbered chapter title or book title as a topic. Exception:
  when a numbered MAIN section intentionally has the same title as the chapter,
  that numbered section is a valid topic and must remain available for rows
  taught there.
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
    return block


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


class OpenAIQueueTimeoutError(RuntimeError):
    """Raised when a generation request cannot obtain an OpenAI slot in time."""


def _acquire_openai_slot(
    gate: "threading.BoundedSemaphore", *, purpose: OpenAIPurpose,
) -> None:
    """Acquire a shared OpenAI slot while keeping long queue waits observable.

    The previous bare ``with gate`` could wait forever if another request was
    wedged in the SDK.  Periodic messages make an expected busy period clear
    in the UI, while the configurable deadline leaves Build Concepts with its
    already-saved checkpoint rather than a permanently running request.
    """
    if gate.acquire(blocking=False):
        return

    timeout = config.OPENAI_SLOT_WAIT_TIMEOUT_SECONDS
    purpose_label = str(purpose).replace("_", " ")
    progress.log(
        "OpenAI capacity is busy; waiting for a free "
        f"{purpose_label} slot.",
        level="warning",
    )
    if timeout <= 0:
        raise OpenAIQueueTimeoutError(
            "OpenAI capacity is busy and no queue wait is configured. "
            "Try again after another generation finishes."
        )

    import time

    started = time.monotonic()
    while True:
        elapsed = time.monotonic() - started
        remaining = timeout - elapsed
        if remaining <= 0:
            raise OpenAIQueueTimeoutError(
                "Timed out waiting for an available OpenAI generation slot. "
                "If this run has a saved checkpoint, resume it after another "
                "generation finishes."
            )
        wait_for = min(config.OPENAI_SLOT_WAIT_LOG_SECONDS, remaining)
        if gate.acquire(timeout=wait_for):
            waited = time.monotonic() - started
            progress.log(
                f"OpenAI slot acquired after {waited:.0f}s; continuing.",
                level="success",
            )
            return
        waited = time.monotonic() - started
        progress.log(
            f"Still waiting for OpenAI capacity ({waited:.0f}s).",
            level="warning",
        )


def _openai_json(
    system: str,
    user: str,
    max_tokens: int | None = None,
    retries: int = 3,
    *,
    purpose: OpenAIPurpose = "source_extraction",
) -> dict:
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
    request_policy = chat_request_policy(purpose, model=config.OPENAI_MODEL)
    # Disable SDK-level retries: this layer already supplies the retry policy
    # and can surface each wait to the active progress stream.
    client = OpenAI(
        timeout=config.OPENAI_REQUEST_TIMEOUT_SECONDS,
        max_retries=0,
    )
    gate = _get_openai_gate()
    last_err: Exception | None = None
    attempt = 0  # hard failures (bad JSON, truncation, 4xx)
    transient = 0  # rate limits / timeouts / 5xx — retried patiently
    while True:
        try:
            _acquire_openai_slot(gate, purpose=purpose)
            try:
                resp = client.chat.completions.create(
                    **request_policy,
                    messages=[{"role": "system", "content": system},
                              {"role": "user", "content": user}],
                    response_format={"type": "json_object"},
                    max_completion_tokens=limit,
                )
            finally:
                gate.release()
            # Record before finish-reason/JSON validation: responses retried for
            # truncation or malformed JSON are still billable.
            try:
                from . import openai_usage

                openai_usage.record_response(
                    resp, requested_model=request_policy["model"]
                )
            except Exception:  # accounting must never trigger another API call
                pass
            choice = resp.choices[0]
            if getattr(choice, "finish_reason", None) == "length":
                raise RuntimeError(
                    f"OpenAI response truncated at max_completion_tokens={limit}. "
                    "Set AEGIS_OPENAI_MAX_OUTPUT_TOKENS higher or reduce input size."
                )
            return json.loads(choice.message.content or "{}")
        except OpenAIQueueTimeoutError:
            raise
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
    r"\b(exercises?|ex\.|review|practice|problems?|questions?)\b",
    re.IGNORECASE)
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
    heading/topic context. Normalize line endings here as well so direct
    parser callers receive the same input contract as file-upload callers.
    Idempotent apart from canonicalizing line endings to LF.
    """
    mmd_text = (mmd_text or "").replace("\r\n", "\n").replace("\r", "\n")

    def _sub(m: "re.Match[str]") -> str:
        title = re.sub(r"\\[a-zA-Z]+\*?", " ", m.group(2))
        title = title.replace("{", " ").replace("}", " ")
        title = re.sub(r"\s+", " ", title).strip()
        return "#" * _LATEX_HEADING_LEVELS[m.group(1)] + " " + title

    return _LATEX_HEADING_RE.sub(_sub, mmd_text)


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
    """Pack parsed sections into chunks while preserving heading context.

    Filler umbrella sections (Overview / Summary / Basics / …) are omitted
    entirely so their preview/recap prose is not re-extracted into neighboring
    topics.
    """
    sections = [
        section for section in parse_mmd_sections(mmd_text)
        if (
            not _is_filler_source_topic(section.get("heading") or "")
            and not _is_answer_key_source_section(section)
        )
    ]
    return _pack_section_chunks(sections, max_chars)


def _sections_with_source_topics(sections: list[dict]) -> list[tuple[str, dict]]:
    """Pair each section with its nearest real main-section topic.

    Structural OCR headings such as ``Solution`` and ``EXERCISE 5.2`` inherit
    the preceding main topic. This association is the source of truth for
    question inventory and mined-Type placement.

    Filler umbrella headings (Overview / Summary / Basics / …) are skipped
    entirely — their bodies are not attached to neighboring topics.
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
        heading = section.get("heading") or ""
        if _is_filler_source_topic(heading):
            continue
        key = _topic_comparison_key(heading)
        if key in canonical:
            current = canonical[key]
        paired.append((current, section))
    return paired


_NON_TEACHING_TOPIC_CONTEXT_RE = re.compile(
    r"^(?:chapter\s+)?(?:summary|recap(?:itulation)?)\b|"
    r"^(?:a\s+)?note\s+to\s+(?:the\s+)?reader\b|"
    r"^(?:glossary|references?|bibliography|acknowledg(?:e)?ments?)\b",
    re.IGNORECASE,
)


def _group_source_topic_excerpts(sections: list[dict]) -> list[dict]:
    """Group source sections under their canonical main topic in reading order.

    ``_sections_with_source_topics`` supplies the structural inheritance:
    Example/Solution/Exercise-style headings stay attached to the nearest real
    main section instead of becoming standalone topics.
    """
    grouped: list[dict] = []
    index_by_key: dict[str, int] = {}
    for topic, section in _sections_with_source_topics(sections):
        # Chapter-level recaps/editorial notes have no reliable main-topic
        # ownership. Treating their repeated or postscript content as evidence
        # for the preceding topic would be a semantic guess.
        section_heading = (section.get("heading") or "").strip()
        if _is_filler_source_topic(section_heading):
            continue
        if (
            _is_non_topic_heading(section_heading)
            and _NON_TEACHING_TOPIC_CONTEXT_RE.match(section_heading)
        ):
            continue
        key = _topic_comparison_key(topic)
        if not key:
            continue
        if key not in index_by_key:
            index_by_key[key] = len(grouped)
            grouped.append({
                "topic": _strip_section_number(topic),
                "sections": [],
            })
        grouped[index_by_key[key]]["sections"].append(section)
    return [
        {
            "topic": group["topic"],
            "excerpt": _format_section_chunk(group["sections"]),
        }
        for group in grouped
    ]


_SOURCE_EVIDENCE_BOUNDARY_RE = re.compile(
    r"\s*(?:\||;|\n+|…+|(?:\.\s*){2,})\s*"
)
_MIN_EXACT_EVIDENCE_WORDS = 5
_MIN_EXACT_EVIDENCE_CHARS = 20


def _normalize_exact_source_text(text: str) -> str:
    """Normalize source/evidence text for conservative exact phrase matching."""
    out = unicodedata.normalize("NFKC", str(text or "")).casefold()
    out = _METHOD_ANCHOR_ID_RE.sub(" ", out.upper()).casefold()
    for _ in range(4):
        out = re.sub(
            r"\\(?:mathbf|boldsymbol|mathrm|text|operatorname)\s*\{([^{}]*)\}",
            r" \1 ",
            out,
        )
    out = re.sub(r"\\[a-zA-Z]+\*?", " ", out)
    out = out.replace("_", " ").replace("^", " ")
    out = re.sub(r"[^\w]+", " ", out, flags=re.UNICODE)
    return re.sub(r"\s+", " ", out).strip()


def _strong_exact_source_evidence_phrases(evidence: str) -> list[str]:
    """Return only long, literal evidence fragments safe for deterministic use."""
    evidence = _METHOD_ANCHOR_ID_RE.sub(" ", str(evidence or "").upper())
    phrases: list[str] = []
    seen: set[str] = set()
    for fragment in _SOURCE_EVIDENCE_BOUNDARY_RE.split(evidence):
        normalized = _normalize_exact_source_text(fragment)
        words = normalized.split()
        content_chars = sum(len(word) for word in words)
        if (
            len(words) < _MIN_EXACT_EVIDENCE_WORDS
            or content_chars < _MIN_EXACT_EVIDENCE_CHARS
            or normalized in seen
        ):
            continue
        seen.add(normalized)
        phrases.append(normalized)
    return phrases


def _assign_topics_from_source_evidence(
    records: list[dict], source_topic_excerpts: list[dict],
) -> list[dict]:
    """Assign a topic only when exact source evidence has one unambiguous home.

    Shared formulas, short/generic snippets, ties, and conflicting unique
    snippets deliberately make no change. METHOD-tagged rows are excluded
    because method-anchor topic authority is stronger than phrase placement.
    """
    normalized_sources = [
        (
            (group.get("topic") or "").strip(),
            _normalize_exact_source_text(group.get("excerpt") or ""),
        )
        for group in source_topic_excerpts or []
        if (group.get("topic") or "").strip()
    ]
    if not normalized_sources:
        return records
    padded_sources = [
        (topic, f" {source} ") for topic, source in normalized_sources if source
    ]
    for record in records:
        if _method_anchor_ids(record):
            continue
        unique_topic_matches: set[str] = set()
        for phrase in _strong_exact_source_evidence_phrases(
                record.get("source_evidence") or ""):
            padded_phrase = f" {phrase} "
            matching_topics = {
                topic for topic, source in padded_sources
                if padded_phrase in source
            }
            if len(matching_topics) == 1:
                unique_topic_matches.update(matching_topics)
        if len(unique_topic_matches) == 1:
            record["topic"] = next(iter(unique_topic_matches))
    return records


def _inventory_chunks_by_topic(
    sections: list[dict], max_chars: int | None = None,
) -> list[dict]:
    """Build inventory chunks that never cross a source-topic boundary."""
    groups: list[tuple[str, bool, list[dict]]] = []
    paired = _sections_with_source_topics(sections)
    for section_index, (topic, section) in enumerate(paired):
        chapter_wide = _is_chapter_wide_task_section(
            section, section_index=section_index, paired_sections=paired)
        effective_topic = "" if chapter_wide else topic
        if (
            groups
            and groups[-1][0] == effective_topic
            and groups[-1][1] == chapter_wide
        ):
            groups[-1][2].append(section)
        else:
            groups.append((effective_topic, chapter_wide, [section]))
    chunks: list[dict] = []
    for topic, chapter_wide, topic_sections in groups:
        for chunk in _pack_section_chunks(topic_sections, max_chars):
            chunk["source_topic"] = topic
            chunk["chapter_wide_tasks"] = chapter_wide
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


_MATHPIX_DISPLAY_ENV_RE = re.compile(
    r"\\begin\{(?P<env>equation\*?|align\*?|aligned|gather\*?|"
    r"gathered|multline\*?|array)\}"
    r"(?P<body>.*?)"
    r"\\end\{(?P=env)\}",
    re.IGNORECASE | re.DOTALL,
)
_MATHPIX_STRUCTURAL_ENV_RE = re.compile(
    r"\\(?:begin|end)\{(?:figure\*?|table\*?|center|flushleft|flushright|"
    r"itemize|enumerate|description|quote)\}(?:\[[^\]]*\])?",
    re.IGNORECASE,
)
_MATHPIX_HEADING_RE = re.compile(
    r"\\(?:sub)*section\*?\{(?P<body>[^{}]*)\}",
    re.IGNORECASE,
)
_MATHPIX_CAPTION_RE = re.compile(
    r"\\caption(?:of\{(?:figure|table)\})?\{(?P<body>[^{}]*)\}",
    re.IGNORECASE,
)
_MATHPIX_STYLE_RE = re.compile(
    r"\\(?:textbf|textit|emph|underline|textrm|textsf|texttt)"
    r"\{(?P<body>[^{}]*)\}",
    re.IGNORECASE,
)
_MATHPIX_LABEL_RE = re.compile(
    r"\\(?:label|vspace\*?|hspace\*?)\{[^{}]*\}",
    re.IGNORECASE,
)
_MATHPIX_LAYOUT_COMMAND_RE = re.compile(
    r"\\(?:centering|noindent|newpage|clearpage|pagebreak)\b",
    re.IGNORECASE,
)
_MATHPIX_ITEM_RE = re.compile(
    r"\\item(?:\[[^\]]*\])?\s*",
    re.IGNORECASE,
)
_MATHPIX_SPACING_RE = re.compile(r"\\\\\s*\[[^\]]+\]")
_CANONICAL_KATEX_SPAN_RE = re.compile(
    r"\[katex\].*?\[/katex\]",
    re.IGNORECASE | re.DOTALL,
)


def _normalize_common_mathpix_wrappers(text: str) -> str:
    """Turn common document-layout LaTeX into supported rich text.

    Mathpix source sometimes leaks figure, caption, list, or display-environment
    wrappers into an otherwise useful generated description.  Those wrappers
    are not mathematical content and are not supported by the Bulk Import
    contract.  Normalize the known wrappers deterministically, while leaving
    unknown TeX untouched so the strict validator can still report it.
    """
    value = str(text or "")
    protected_math: list[str] = []

    def protect_existing_math(match: re.Match) -> str:
        token = f"@@AEGIS_EXISTING_KATEX_{len(protected_math):04d}@@"
        protected_math.append(match.group(0))
        return token

    # Structural commands are valid *inside* a canonical KaTeX expression.
    # Protect those expressions before stripping document-layout wrappers.
    value = _CANONICAL_KATEX_SPAN_RE.sub(protect_existing_math, value)

    def display_environment(match: re.Match) -> str:
        env = (match.group("env") or "").lower()
        body = (match.group("body") or "").strip()
        if not body:
            return ""
        if env.startswith("array"):
            expression = rf"\begin{{array}}{body}\end{{array}}"
        elif env.startswith(("align", "gather", "multline")):
            expression = rf"\begin{{aligned}}{body}\end{{aligned}}"
        else:
            expression = body
        return kr.katex(expression)

    value = _MATHPIX_DISPLAY_ENV_RE.sub(display_environment, value)
    value = _MATHPIX_HEADING_RE.sub(
        lambda match: (match.group("body") or "").strip(), value)
    value = _MATHPIX_CAPTION_RE.sub(
        lambda match: (
            f"Caption: {(match.group('body') or '').strip()}"
            if (match.group("body") or "").strip() else ""
        ),
        value,
    )
    # Style wrappers can be nested by Mathpix.  A few bounded passes unwrap the
    # common cases without attempting to interpret arbitrary LaTeX.
    for _ in range(4):
        updated = _MATHPIX_STYLE_RE.sub(
            lambda match: (match.group("body") or "").strip(), value)
        if updated == value:
            break
        value = updated
    value = _MATHPIX_STRUCTURAL_ENV_RE.sub("", value)
    value = _MATHPIX_LABEL_RE.sub("", value)
    value = _MATHPIX_LAYOUT_COMMAND_RE.sub("", value)
    value = _MATHPIX_ITEM_RE.sub("• ", value)
    value = _MATHPIX_SPACING_RE.sub("\n", value)
    value = re.sub(r"[ \t]+\n", "\n", value)
    value = re.sub(r"\n[ \t]+", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    for index, rendered in enumerate(protected_math):
        value = value.replace(
            f"@@AEGIS_EXISTING_KATEX_{index:04d}@@", rendered)
    return value


def _canonicalize_concept_rich_text(records: list[dict]) -> list[dict]:
    """Emit the exact rich-text wire format without changing row semantics."""
    for record in records:
        if record.get("concept_details"):
            canonical = kr.canonicalize_rich_text(
                _normalize_common_mathpix_wrappers(
                    _inventory_comparison_text(record["concept_details"])))
            record["concept_details"] = kr.repair_unwrapped_math(canonical)
    return records


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
            if not inserted and (
                cr.is_learner_analysis_label(label)
            ):
                out.append(("Types", types_body.strip()))
                inserted = True
            out.append((label, content))
        if not inserted:
            out.append(("Types", types_body.strip()))
    return cr.join_sections(out)


def _activity_hub_fragment(mtype: dict) -> str:
    """Compact Activity/Info Hub entry for a textbook activity Type."""
    title = concept_cleanup.strip_dangling_references(
        (mtype.get("type_title") or mtype.get("task_pattern") or "").strip())
    pieces: list[str] = []
    if title:
        pieces.append(title.rstrip("."))
    for case in mtype.get("case_prompts") or []:
        if not isinstance(case, dict):
            continue
        case_title = concept_cleanup.strip_dangling_references(
            _strip_leading_source_task_label(
                case.get("case_title") or "")).strip()
        if case_title and case_title not in pieces:
            pieces.append(case_title.rstrip("."))
        for example in _case_examples(case):
            prompt = _strip_leading_source_task_label(
                example.get("example_prompt") or "").strip()
            if prompt and prompt not in pieces:
                pieces.append(prompt)
    if not pieces:
        return ""
    return "Activity: " + " | ".join(pieces) + "."


def _append_activity_hub(details: str, hub_text: str) -> str:
    return cr.append_activity_hub(details, hub_text)


# Inventory kinds that belong in Activity/Info Hub. Assessable prompts originating
# in an Activity also appear in Types, while reusing the same inventory identity.
_HUB_INVENTORY_KINDS = frozenset({"activity", "experiment_task"})
_ACTIVITY_PUBLIC_WORD_LIMIT = 55
_ACTIVITY_PUBLIC_CHAR_LIMIT = 420
_PLACEMENT_CERTIFICATION_VERSION = 1
_PLACEMENT_CERTIFICATIONS_KEY = "placement_certifications"


def _placement_host_identity(value: dict) -> dict:
    """Stable persisted identity for one reviewed inventory-item host."""
    title = str(
        value.get("concept")
        or value.get("concept_title")
        or ""
    ).strip()
    is_culmination = bool(
        value.get("is_culmination")
        or cr.is_culmination(title)
    )
    return {
        "topic": str(value.get("topic") or "").strip(),
        "topic_key": _topic_comparison_key(value.get("topic") or ""),
        "concept": title,
        # Culmination titles are deterministic recaps and may be renamed when
        # normal concepts are consolidated. One Culmination per topic is the
        # stable identity; normal concepts retain their exact normalized title.
        "concept_key": (
            "__culmination__"
            if is_culmination
            else bi.normalize_question_text(title)
        ),
        "is_culmination": is_culmination,
    }


def _placement_certification_ledger(
    mined_types: dict | None,
) -> dict | None:
    """Return a valid qid-host ledger, distinguishing absent from malformed."""
    if not isinstance(mined_types, dict):
        return None
    raw = mined_types.get(_PLACEMENT_CERTIFICATIONS_KEY)
    if raw is None:
        return None
    if (
        not isinstance(raw, dict)
        or raw.get("version") != _PLACEMENT_CERTIFICATION_VERSION
        or not isinstance(raw.get("hosts"), dict)
    ):
        return {}
    return raw


def _placement_certification_entry_is_valid(entry: object) -> bool:
    if not isinstance(entry, dict):
        return False
    topic = str(entry.get("topic") or "").strip()
    concept = str(entry.get("concept") or "").strip()
    is_culmination = entry.get("is_culmination")
    basis = entry.get("basis")
    if (
        not topic
        or not concept
        or not isinstance(is_culmination, bool)
        or not isinstance(basis, str)
        or not basis.strip()
    ):
        return False
    return (
        entry.get("topic_key") == _topic_comparison_key(topic)
        and entry.get("concept_key")
        == (
            "__culmination__"
            if is_culmination
            else bi.normalize_question_text(concept)
        )
    )


def _placement_certification_contract_complete(
    mined_types: dict | None,
    inventory: dict | None,
) -> bool:
    """Whether a persisted late-stage artifact certifies every inventory qid."""
    expected_qids = {
        str(item.get("qid") or "").strip()
        for item in (inventory or {}).get("items") or []
        if isinstance(item, dict) and str(item.get("qid") or "").strip()
    }
    if not expected_qids:
        if (
            not isinstance(mined_types, dict)
            or _PLACEMENT_CERTIFICATIONS_KEY not in mined_types
        ):
            return True
        ledger = _placement_certification_ledger(mined_types)
        return bool(ledger) and not ledger["hosts"]
    ledger = _placement_certification_ledger(mined_types)
    if not ledger:
        return False
    hosts = ledger["hosts"]
    return (
        set(hosts) == expected_qids
        and all(
            _placement_certification_entry_is_valid(hosts[qid])
            for qid in expected_qids
        )
    )


def _reviewed_placement_authority_declared(
    mined_types: dict | None,
) -> bool:
    """Whether terminal placement must use the reviewed qid-host ledger.

    The pre-review raw-task heuristics remain useful for legacy checkpoints and
    as proposal evidence before host review. Once assignment declares the
    versioned certification ledger, however, those heuristics must not run as a
    second independent certifier. Completeness, malformed entries, and drift
    are enforced by :func:`_placement_certification_violations`.
    """
    return bool(
        isinstance(mined_types, dict)
        and _PLACEMENT_CERTIFICATIONS_KEY in mined_types
    )


def _reset_placement_certifications(mined_types: dict | None) -> dict | None:
    if not isinstance(mined_types, dict):
        return None
    ledger = {
        "version": _PLACEMENT_CERTIFICATION_VERSION,
        "hosts": {},
    }
    mined_types[_PLACEMENT_CERTIFICATIONS_KEY] = ledger
    return ledger


def _certify_inventory_host(
    mined_types: dict | None,
    qid: str,
    host: dict,
    *,
    basis: str,
) -> None:
    """Persist one exact qid -> reviewed host decision."""
    qid = str(qid or "").strip()
    if not qid or not isinstance(mined_types, dict):
        return
    ledger = _placement_certification_ledger(mined_types)
    if ledger is None or not ledger:
        ledger = _reset_placement_certifications(mined_types)
    identity = _placement_host_identity(host)
    if (
        ledger is None
        or not identity["topic_key"]
        or not identity["concept_key"]
    ):
        raise RuntimeError(
            f"cannot certify inventory host for {qid}: missing host identity"
        )
    candidate = {
        **identity,
        "basis": str(basis or "reviewed").strip() or "reviewed",
    }
    prior = ledger["hosts"].get(qid)
    if prior is not None:
        if not isinstance(prior, dict):
            raise RuntimeError(
                f"malformed prior placement certification for {qid}")
        if (
            prior.get("topic_key") != candidate["topic_key"]
            or prior.get("concept_key") != candidate["concept_key"]
            or bool(prior.get("is_culmination"))
            != candidate["is_culmination"]
        ):
            raise RuntimeError(
                f"conflicting placement certifications for {qid}"
            )
    ledger["hosts"][qid] = candidate


def _certified_host_cid(
    mined_types: dict | None,
    qid: str,
    concept_payload_by_id: dict[str, dict],
) -> str:
    ledger = _placement_certification_ledger(mined_types)
    if not ledger:
        return ""
    expected = ledger["hosts"].get(str(qid or "").strip())
    if not _placement_certification_entry_is_valid(expected):
        return ""
    matches: list[str] = []
    for cid, payload in concept_payload_by_id.items():
        actual = _placement_host_identity(payload)
        if (
            actual["topic_key"] == expected.get("topic_key")
            and actual["concept_key"] == expected.get("concept_key")
            and actual["is_culmination"]
            == bool(expected.get("is_culmination"))
        ):
            matches.append(cid)
    return matches[0] if len(matches) == 1 else ""


def _strip_public_source_heading(text: str) -> str:
    """Remove Markdown/OCR block headings from public Hub/Example prose."""
    value = re.sub(
        r"(?im)^\s*(?:#{1,6}\s*)?(?:activity|discuss|discussion|exercise|"
        r"question|questions)\b(?:\s+\d+(?:\.\d+)*)?\s*[:.)-]?\s*"
        r"(?:\n|$)",
        "",
        str(text or ""),
    )
    # Inventory sanitation intentionally collapses whitespace. Preserve the
    # prompt when a former heading and its first sentence now share one line
    # (for example ``## Activity Imagine you are a weaver ...``).
    value = re.sub(
        r"(?im)^\s*#{1,6}\s*(?:activity|discuss|discussion|exercise|"
        r"question|questions)\b\s*[:.)-]?\s*",
        "",
        value,
    )
    # OCR/plain-text extraction may collapse a container heading to
    # ``Activity: Explain ...``. Require heading punctuation here so a genuine
    # student-facing imperative such as ``Discuss why ...`` remains intact.
    value = re.sub(
        r"(?im)^\s*(?:activity|discuss|discussion|exercise|"
        r"question|questions)\b(?:\s+\d+(?:\.\d+)*)?\s*[:.)-]\s*",
        "",
        value,
    )
    value = re.sub(r"(?m)^\s*#{1,6}\s*", "", value)
    # Preserve line boundaries until solution/answer stripping has run; those
    # markers are intentionally line-anchored in source MMD.
    return re.sub(r"[ \t]+", " ", value).strip()


def _activity_hub_marker(item: dict) -> str:
    # Source labels are identifiers, not public task prose. Keep a meaningful
    # numbered label such as ``Activity 11.1`` even though the same text would
    # be stripped as a container heading at the start of an Example.
    label = re.sub(
        r"^\s*#{1,6}\s*",
        "",
        str(
            item.get("source_label")
            or item.get("parent_source_label")
            or ""
        ),
    ).strip()
    if label:
        label = label[:100].strip(" .:-")
        if _source_label_is_generic(label):
            plain = bi.to_plain_text(_inventory_task_text(item))
            words = re.findall(r"\S+", _strip_public_source_heading(plain))
            identity = " ".join(words[:6]).strip(" .:-")
            if identity:
                return f"{label} — {identity}"[:100].strip(" .:-")
        return label
    plain = bi.to_plain_text(_inventory_task_text(item))
    words = re.findall(r"\S+", _strip_public_source_heading(plain))
    return " ".join(words[:8]).strip(" .:-") or "Classroom task"


def _mark_activity_hub_placement(record: dict, item: dict) -> None:
    """Retain exact qid identity while compact public Hub prose is ambiguous."""
    qid = str(item.get("qid") or "").strip()
    if not qid:
        return
    qids = list(record.get("_activity_hub_qids") or [])
    if qid not in qids:
        qids.append(qid)
    record["_activity_hub_qids"] = qids


def _compact_activity_hub_note(item: dict, suggested: str = "") -> str:
    """Teacher-facing Activity summary; never copy the full source dump."""
    marker = _activity_hub_marker(item)
    task_raw = _strip_public_source_heading(bi.to_plain_text(
        suggested
        or item.get("raw_task")
        or item.get("normalized_task")
        or _inventory_task_text(item)
    ))
    context_raw = _strip_public_source_heading(bi.to_plain_text(
        item.get("shared_context") or ""
    ))
    # ``to_plain_text`` removes the surrounding [Katex] tags but deliberately
    # preserves their TeX body. Re-wrap only unambiguous math before the Hub
    # note re-enters the canonical rich-text pipeline.
    task_raw = kr.repair_unwrapped_math(task_raw)
    context_raw = kr.repair_unwrapped_math(context_raw)
    marker_key = bi.normalize_question_text(marker)
    if (
        marker_key
        and bi.normalize_question_text(task_raw).startswith(marker_key)
    ):
        task_raw = task_raw[len(marker):].lstrip(" .:-")
    task_sentences = re.split(r"(?<=[.!?])\s+", task_raw)
    task_gist = " ".join(task_sentences[:2]).strip()
    gist = task_gist
    if context_raw:
        context_sentence = re.split(
            r"(?<=[.!?])\s+", context_raw, maxsplit=1)[0].strip()
        with_context = " ".join(
            part for part in (context_sentence, task_gist) if part
        )
        if (
            len(with_context.split()) <= _ACTIVITY_PUBLIC_WORD_LIMIT
            and len(with_context) <= _ACTIVITY_PUBLIC_CHAR_LIMIT
        ):
            gist = with_context
    words = gist.split()
    if len(words) > _ACTIVITY_PUBLIC_WORD_LIMIT:
        gist = " ".join(words[:_ACTIVITY_PUBLIC_WORD_LIMIT]).rstrip(" ,;:") + "…"
    if len(gist) > _ACTIVITY_PUBLIC_CHAR_LIMIT:
        gist = gist[:_ACTIVITY_PUBLIC_CHAR_LIMIT].rsplit(" ", 1)[0].rstrip(
            " ,;:") + "…"
    prefix = "Activity"
    if marker and bi.normalize_question_text(marker) not in {"activity", "classroom task"}:
        prefix += f" — {marker}"
    note = f"{prefix}: {gist}".strip()
    if not gist:
        note = f"{prefix}: Complete the source-grounded classroom task."

    # A non-assessable visual activity may have no Type Example, so retain all
    # of its referenced canonical image tags in the concise Hub note.
    task = _inventory_task_text(item)
    image_tags = list(dict.fromkeys(
        match.group(0) for match in _BRACKET_IMAGE_RE.finditer(task)))
    for image_tag in image_tags:
        if image_tag not in note:
            note = f"{note.rstrip('.')} {image_tag}"
    note = note.rstrip()
    if not note.endswith((".", "!", "?")):
        note += "."
    return kr.canonicalize_rich_text(note)


def _activity_hub_locations(records: list[dict], item: dict) -> list[int]:
    qid = str(item.get("qid") or "").strip()
    tagged_locations = [
        index for index, record in enumerate(records)
        if qid and qid in (record.get("_activity_hub_qids") or [])
    ]
    if tagged_locations:
        return tagged_locations

    marker = bi.normalize_question_text(_activity_hub_marker(item))
    raw_label = str(
        item.get("source_label") or item.get("parent_source_label") or "")
    generic_label = _source_label_is_generic(
        _strip_public_source_heading(raw_label))

    def contains_marker(details: str) -> bool:
        hub = bi.normalize_question_text(cr.activity_hub_body(details))
        return bool(
            marker
            and re.search(rf"(?<!\w){re.escape(marker)}(?!\w)", hub)
        )

    locations = [
        index for index, record in enumerate(records)
        if not generic_label
        and contains_marker(record.get("concept_details") or "")
    ]
    if locations:
        return locations
    # Backward compatibility for persisted Hubs created before compact notes
    # carried a stable label marker.
    task_key = _inventory_coverage_key(_inventory_task_text(item))
    return [
        index for index, record in enumerate(records)
        if task_key and task_key in _inventory_coverage_key(
            cr.activity_hub_body(record.get("concept_details") or ""))
    ]


def _hub_inventory_items(inventory: dict | None) -> list[dict]:
    return [
        item for item in (inventory or {}).get("items") or []
        if isinstance(item, dict)
        and (
            (item.get("source_kind") or "").strip().lower()
            in _HUB_INVENTORY_KINDS
            or bool(item.get("_activity_origin"))
        )
    ]


def _normalize_activity_hubs_from_inventory(
    records: list[dict], inventory: dict | None,
    mined_types: dict | None = None,
) -> list[dict]:
    """Rebuild source-owned Hub notes exactly once from authoritative items.

    GPT still chooses the best normal concept.  The public note itself is
    regenerated from the source-owned inventory so a saved checkpoint cannot
    retain answer/result prose, a stale Figure URL, or duplicated full activity
    instructions.  Private qid markers make the compact notes auditable even
    when two activities have similar labels.
    """
    items: list[dict] = []
    seen_qids: set[str] = set()
    for item in _hub_inventory_items(inventory):
        qid = str(item.get("qid") or "").strip()
        if not qid or qid in seen_qids:
            continue
        seen_qids.add(qid)
        items.append(item)
    if inventory is None or not records:
        return records

    def eligible(index: int, item: dict) -> bool:
        if not 0 <= index < len(records):
            return False
        record = records[index]
        if cr.is_culmination(record.get("concept_title") or ""):
            return False
        expected_topic = _topic_comparison_key(item.get("topic_hint") or "")
        return (
            not expected_topic
            or _topic_comparison_key(record.get("topic") or "")
            == expected_topic
        )

    target_by_qid: dict[str, int] = {}
    certification_declared = (
        isinstance(mined_types, dict)
        and _PLACEMENT_CERTIFICATIONS_KEY in mined_types
    )
    if certification_declared and items:
        ledger = _placement_certification_ledger(mined_types)
        if not ledger:
            raise RuntimeError(
                "activity hub normalization refused a malformed placement "
                "certification ledger"
            )
        concept_payload = _scope_payload_from_records(records)
        index_by_cid = {
            f"CONCEPT-{index + 1:04d}": index
            for index in range(len(records))
        }
        for item in items:
            qid = str(item.get("qid") or "").strip()
            cid = _certified_host_cid(
                mined_types, qid, concept_payload)
            target = index_by_cid.get(cid, -1)
            if not eligible(target, item):
                raise RuntimeError(
                    "activity hub normalization could not resolve the "
                    f"certified exact-topic normal host for {qid}"
                )
            target_by_qid[qid] = target
    else:
        for item in items:
            qid = str(item.get("qid") or "").strip()
            hub_locations = [
                index for index in _activity_hub_locations(records, item)
                if eligible(index, item)
            ]
            example_locations = [
                index for index in _rendered_inventory_example_locations(
                    records, item)
                if eligible(index, item)
            ]
            target = next(
                (
                    index for index in example_locations
                    if index in hub_locations
                ),
                -1,
            )
            if target < 0 and len(hub_locations) == 1:
                target = hub_locations[0]
            if target < 0 and example_locations:
                target = example_locations[0]
            if target < 0:
                candidate = _best_record_index_for_inventory_item(
                    records, item, allow_culmination=False)
                if eligible(candidate, item):
                    target = candidate
            if target >= 0:
                target_by_qid[qid] = target

    out: list[dict] = []
    for record in records:
        updated = dict(record)
        updated["concept_details"] = cr.join_sections([
            (label, content)
            for label, content in cr.split_sections(
                updated.get("concept_details") or "")
            if not cr.is_activity_hub_label(label)
        ])
        updated.pop("_activity_hub_qids", None)
        out.append(updated)

    for item in items:
        qid = str(item.get("qid") or "").strip()
        target = target_by_qid.get(qid, -1)
        if target < 0:
            continue
        note = _compact_activity_hub_note(item)
        out[target]["concept_details"] = _append_activity_hub(
            out[target].get("concept_details") or "", note)
        _mark_activity_hub_placement(out[target], item)

    out = _align_activity_examples_with_hubs(out, inventory)
    if out != records:
        if items:
            progress.log(
                f"Normalized {len(target_by_qid)}/{len(items)} source-owned "
                "Activity/Info Hub item(s).",
                level=(
                    "success"
                    if len(target_by_qid) == len(items)
                    else "warning"
                ),
            )
        else:
            progress.log(
                "Removed stale Activity/Info Hub content because the "
                "authoritative inventory contains no Hub items.",
                level="success",
            )
    return out


def _hub_inventory_contract_violations(
    records: list[dict], inventory: dict | None,
) -> list[dict]:
    """Missing, duplicated, misplaced, or non-canonical Hub inventory."""
    items_by_qid = {
        str(item.get("qid") or "").strip(): item
        for item in _hub_inventory_items(inventory)
        if str(item.get("qid") or "").strip()
    }
    violations: list[dict] = []
    for qid, item in items_by_qid.items():
        locations = _activity_hub_locations(records, item)
        if len(locations) != 1:
            violations.append({
                "qid": qid,
                "reason": "missing" if not locations else "duplicate",
                "locations": locations,
            })
            continue
        index = locations[0]
        record = records[index]
        if cr.is_culmination(record.get("concept_title") or ""):
            violations.append({
                "qid": qid,
                "reason": "culmination_host",
                "locations": locations,
            })
        expected_topic = _topic_comparison_key(item.get("topic_hint") or "")
        actual_topic = _topic_comparison_key(record.get("topic") or "")
        if expected_topic and actual_topic != expected_topic:
            violations.append({
                "qid": qid,
                "reason": "wrong_topic",
                "expected_topic": item.get("topic_hint") or "",
                "actual_topic": record.get("topic") or "",
            })

    known_qids = set(items_by_qid)
    for index, record in enumerate(records):
        tagged_qids = [
            str(qid or "").strip()
            for qid in (record.get("_activity_hub_qids") or [])
            if str(qid or "").strip()
        ]
        unknown = [qid for qid in tagged_qids if qid not in known_qids]
        if unknown:
            violations.append({
                "qid": ",".join(unknown),
                "reason": "unknown_qid",
                "locations": [index],
            })
        expected_notes = [
            _compact_activity_hub_note(items_by_qid[qid])
            for qid in tagged_qids
            if qid in items_by_qid
        ]
        actual_body = cr.activity_hub_body(
            record.get("concept_details") or "")
        if expected_notes:
            expected_body = " ".join(expected_notes)
            if _inventory_comparison_text(
                actual_body
            ).strip() != _inventory_comparison_text(
                expected_body
            ).strip():
                violations.append({
                    "qid": ",".join(tagged_qids),
                    "reason": "noncanonical_content",
                    "locations": [index],
                })
        elif actual_body:
            violations.append({
                "qid": "",
                "reason": "unowned_hub_content",
                "locations": [index],
            })
    return violations


def _inventory_item_already_in_hubs(
    records: list[dict], item: dict,
) -> bool:
    if _activity_hub_locations(records, item):
        return True
    key = _inventory_coverage_key(_inventory_task_text(item))
    source_kind = str(item.get("source_kind") or "").strip().lower()
    return (
        source_kind in _HUB_INVENTORY_KINDS
        and _rendered_inventory_example_counts(records, {key}).get(key, 0) > 0
    )


def _place_activity_inventory_into_hubs(
    records: list[dict], inventory: dict | None,
) -> list[dict]:
    """Deterministically place only a proven Hub host; leave ambiguity open."""
    items = [
        item for item in _hub_inventory_items(inventory)
        if not _inventory_item_already_in_hubs(records, item)
    ]
    if not items or not records:
        return records
    out = [dict(rec) for rec in records]
    concept_payload = _scope_payload_from_records(out)
    placed = 0
    for item in items:
        topic_key = _topic_comparison_key(item.get("topic_hint") or "")
        candidate_cids = tuple(
            cid for cid, payload in concept_payload.items()
            if (
                not payload.get("is_culmination")
                and (
                    not topic_key
                    or _topic_comparison_key(payload.get("topic") or "")
                    == topic_key
                )
            )
        )
        evidence_unit = {
            "type_id": str(item.get("qid") or "").strip(),
            "topic_match_hint": item.get("topic_hint") or "",
            "placement_scope": "normal",
            "is_activity": True,
            "_source_task_evidence": _inventory_task_text(item),
        }
        cid = (
            candidate_cids[0]
            if len(candidate_cids) == 1
            else _high_confidence_assignment_override(
                evidence_unit, candidate_cids, concept_payload)
        )
        if not cid:
            continue
        index = int(cid.rsplit("-", 1)[-1]) - 1
        hub = _compact_activity_hub_note(item)
        out[index]["concept_details"] = _append_activity_hub(
            out[index].get("concept_details") or "", hub)
        _mark_activity_hub_placement(out[index], item)
        placed += 1
    if placed:
        progress.log(
            f"Deterministically placed {placed} activity/experiment item(s) "
            "into Activity/Info Hub.",
            level="success",
        )
    return out


def _populate_activity_hubs_via_api(
    records: list[dict], inventory: dict | None, *, meta: dict,
    mined_types: dict | None = None, max_attempts: int = 3,
) -> list[dict]:
    """Certify every Activity Hub host; never guess an ambiguous destination."""
    import json as _json

    items_by_qid: dict[str, dict] = {}
    for item in _hub_inventory_items(inventory):
        qid = str(item.get("qid") or "").strip()
        if qid:
            items_by_qid.setdefault(qid, item)
    items = list(items_by_qid.values())
    if not records:
        if items:
            raise RuntimeError(
                "activity hub placement failed: no concept hosts available")
        return records
    if not items:
        return _normalize_activity_hubs_from_inventory(
            records, inventory, mined_types)

    certification_owner = (
        mined_types if isinstance(mined_types, dict) else {"types": []}
    )
    if _PLACEMENT_CERTIFICATIONS_KEY not in certification_owner:
        _reset_placement_certifications(certification_owner)
    elif not _placement_certification_ledger(certification_owner):
        raise RuntimeError(
            "activity hub placement refused a malformed placement "
            "certification ledger"
        )

    concept_payload: list[dict] = []
    for i, rec in enumerate(records, start=1):
        cid = f"CONCEPT-{i:04d}"
        concept_payload.append({
            "concept_id": cid,
            "topic": rec.get("topic", ""),
            "parent_concept": rec.get("parent_concept", ""),
            "concept": rec.get("concept_title", ""),
            "is_culmination": cr.is_culmination(rec.get("concept_title", "")),
            "existing_activity_hub": cr.activity_hub_body(
                rec.get("concept_details") or ""),
        })
    concept_payload_by_id = {
        row["concept_id"]: row for row in concept_payload
    }
    allowed_cids_by_qid: dict[str, tuple[str, ...]] = {}
    ambiguous: dict[str, dict] = {}
    deterministic = 0
    ledger = _placement_certification_ledger(certification_owner)
    if not ledger:
        raise RuntimeError(
            "activity hub placement could not initialize its placement "
            "certification ledger"
        )
    for qid, item in items_by_qid.items():
        expected_topic = _topic_comparison_key(
            item.get("topic_hint") or "")
        allowed = tuple(
            row["concept_id"]
            for row in concept_payload
            if (
                not row["is_culmination"]
                and (
                    not expected_topic
                    or _topic_comparison_key(row.get("topic") or "")
                    == expected_topic
                )
            )
        )
        if not allowed:
            raise RuntimeError(
                "activity hub placement failed: no exact-topic normal "
                f"concept host for {qid}"
            )
        allowed_cids_by_qid[qid] = allowed

        existing_certification = ledger["hosts"].get(qid)
        if existing_certification is not None:
            if not _placement_certification_entry_is_valid(
                existing_certification
            ):
                raise RuntimeError(
                    "activity hub placement failed: malformed existing "
                    f"certification for {qid}"
                )
            certified_cid = _certified_host_cid(
                certification_owner, qid, concept_payload_by_id)
            if not certified_cid:
                raise RuntimeError(
                    "activity hub placement failed: existing certification "
                    f"for {qid} does not resolve to one current concept"
                )
            if certified_cid not in allowed:
                raise RuntimeError(
                    "activity hub placement failed: existing certification "
                    f"for {qid} is not an exact-topic normal host"
                )
            continue

        evidence_unit = {
            "type_id": qid,
            "topic_match_hint": item.get("topic_hint") or "",
            "placement_scope": "normal",
            "is_activity": True,
            "_source_task_evidence": _inventory_task_text(item),
        }
        existing_locations = _activity_hub_locations(records, item)
        existing_cid = (
            f"CONCEPT-{existing_locations[0] + 1:04d}"
            if len(existing_locations) == 1 else ""
        )
        if existing_cid not in allowed:
            existing_cid = ""
        if existing_cid:
            proven_cid = existing_cid
        elif len(allowed) == 1:
            proven_cid = allowed[0]
        else:
            proven_cid = _high_confidence_assignment_override(
                evidence_unit, allowed, concept_payload_by_id)
        if proven_cid:
            _certify_inventory_host(
                certification_owner,
                qid,
                concept_payload_by_id[proven_cid],
                basis=(
                    "existing_activity_hub"
                    if existing_cid
                    else (
                        "sole_exact_topic_host"
                        if len(allowed) == 1
                        else "source_evidence"
                    )
                ),
            )
            deterministic += 1
            continue
        ambiguous[qid] = item

    if deterministic:
        progress.log(
            f"Deterministically certified {deterministic} Activity/Info Hub "
            "host(s) from sole-host or source evidence.",
            level="success",
        )

    system = prompts.get_text("concepts.activity_hub.system")
    rejection_reason_by_qid: dict[str, str] = {}
    api_certified = 0
    for attempt in range(1, max(1, max_attempts) + 1):
        if not ambiguous:
            break
        inventory_payload = []
        for qid, item in ambiguous.items():
            payload = {
                "qid": qid,
                "source_kind": (
                    item.get("source_kind") or "").strip().lower(),
                "source_label": item.get("source_label") or "",
                "topic_hint": item.get("topic_hint") or "",
                "raw_task": _inventory_task_text(item),
                "allowed_concept_ids": list(
                    allowed_cids_by_qid[qid]),
            }
            if qid in rejection_reason_by_qid:
                payload["previous_rejection"] = (
                    rejection_reason_by_qid[qid])
            inventory_payload.append(payload)
        user = (
            _metadata_block(meta)
            + "\nPlace every pending activity/experiment/discussion inventory "
            "item into Activity/Info Hub on exactly one allowed normal "
            "concept. Return one verdict for every qid:\n"
            + _json.dumps({
                "concepts": concept_payload,
                "pending_inventory": inventory_payload,
            }, ensure_ascii=False)
        )
        progress.log(
            "Reviewing semantic Activity/Info Hub hosts via API for "
            f"{len(inventory_payload)} inventory item(s), attempt {attempt}.")
        data = _openai_json(
            system, user, purpose="concept_detailing")
        proposed: dict[str, str] = {}
        invalid: set[str] = set()
        for placement in (
            data.get("placements") or []
            if isinstance(data, dict) else []
        ):
            if not isinstance(placement, dict):
                continue
            qid = str(placement.get("qid") or "").strip()
            cid = str(placement.get("concept_id") or "").strip()
            if qid not in ambiguous:
                continue
            if qid in proposed:
                invalid.add(qid)
                continue
            if cid not in allowed_cids_by_qid[qid]:
                invalid.add(qid)
                rejection_reason_by_qid[qid] = (
                    "concept_id was not an allowed exact-topic normal host")
                continue
            proposed[qid] = cid
        for qid in invalid:
            proposed.pop(qid, None)
            rejection_reason_by_qid.setdefault(
                qid, "duplicate or invalid verdict")
        for qid in set(ambiguous) - set(proposed):
            rejection_reason_by_qid.setdefault(
                qid, "missing verdict")
        for qid, cid in proposed.items():
            _certify_inventory_host(
                certification_owner,
                qid,
                concept_payload_by_id[cid],
                basis="activity_host_review",
            )
            ambiguous.pop(qid, None)
            rejection_reason_by_qid.pop(qid, None)
            api_certified += 1
        if ambiguous:
            progress.log(
                f"Activity host review left {len(ambiguous)} uncertified "
                "item(s); retrying only those qids.",
                level="warning",
            )

    if ambiguous:
        details = ", ".join(
            f"{qid} ({rejection_reason_by_qid.get(qid, 'missing verdict')})"
            for qid in sorted(ambiguous)
        )
        raise RuntimeError(
            "activity hub placement review did not certify every inventory "
            "item: " + details
        )

    if api_certified:
        progress.log(
            f"API-certified {api_certified} Activity/Info Hub host(s).",
            level="success",
        )
    return _normalize_activity_hubs_from_inventory(
        records, inventory, certification_owner)


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
    r"(?im)^[ \t]*(?:#{1,6}[ \t]*)?(?:worked[ \t]+)?example[ \t]+"
    r"([A-Za-z0-9]+(?:\.[A-Za-z0-9]+)*)[ \t]*[:：.)-]?[ \t]*",
)
_NUMBERED_TASK_START_RE = re.compile(
    r"(?im)^[ \t]*(?:\\item[ \t]*\[[ \t]*)?"
    r"(?:q(?:uestion)?[ \t]*)?[\[(]?(\d{1,3})"
    r"(?:[ \t]*[.)\]:-][ \t]*\]?[ \t]*|[ \t]+)"
)
_BULLET_TASK_START_RE = re.compile(
    r"(?im)^[ \t]*\\item[ \t]*\[[ \t]*-[ \t]*\][ \t]*",
)
_DISCOVER_PROJECT_HEADING_RE = re.compile(
    r"^discover\s*,?\s*design\s*,?\s*and\s*debate\b",
    re.IGNORECASE,
)
_SOLUTION_START_RE = re.compile(
    r"(?im)^[ \t]*(?:(?:solutions?|answers?)|soln?|ans)[ \t]*"
    r"(?:[:：.]|[-–—])[ \t]*",
)
_CALLOUT_TASK_START_RE = re.compile(
    r"(?im)^[ \t]*(?:\\item[ \t]*\[[ \t]*\][ \t]*)?"
    r"(?:\(\s*\?\s*\)|\?)[ \t]*",
)
_BODY_FIGURE_IT_OUT_RE = re.compile(
    r"(?im)^[ \t]*(?:\\item[ \t]*\[[ \t]*\][ \t]*)?"
    r"(?:\(\s*\?\s*\)|\?)[ \t]*figure[ \t]+it[ \t]+out"
    r"[ \t]*[:.!]?[ \t]*$",
)
_CHAPTER_WIDE_TASK_HEADING_RE = re.compile(
    r"^(?:chapter\s+)?(?:exercises?|questions?|review(?:\s+questions?)?|"
    r"(?:\?\s*)?figure\s+it\s+out|"
    r"assessment|write\s+in\s+brief|discuss|project|"
    r"keep\s+the\s+curiosity\s+alive|"
    r"discover\s*,?\s*design\s*,?\s*and\s*debate|"
    r"end[-\s]+of[-\s]+chapter\s+(?:review|questions?))\s*[:.]?$",
    re.IGNORECASE,
)
_CHECKPOINT_CONTAINER_HEADING_RE = re.compile(
    r"^(?:checkpoint|check\s+your\s+progress|let['’]s\s+(?:recall|discuss)|"
    r"discuss|think(?:\s+about\s+it)?|do\s+this|try\s+these|find\s+out\s+more|"
    r"activity|project)\b",
    re.IGNORECASE,
)
_QUESTION_SENTENCE_RE = re.compile(
    r"(?:^|(?<=[.!?:])\s+)"
    r"(?P<question>(?:(?:and|but|or|now)\s+)?"
    r"(?:(?:what|why|how|when|where|who|whom|whose|which)\b|"
    r"(?:can|could|do|does|did|is|are|was|were|will|would|should|has|have|"
    r"had|may)\b)[^?]{8,800}\?)",
    re.IGNORECASE,
)
_CONDITIONAL_QUESTION_FOLLOWUP_RE = re.compile(
    r"^\s+(?P<question>If\s+(?:not|so)\s*,\s*"
    r"(?:can|could|do|does|did|is|are|was|were|will|would|should|"
    r"has|have|had|may)\b[^?]{3,400}\?)",
    re.IGNORECASE,
)
_LEADING_SOURCE_TASK_LABEL_RE = re.compile(
    r"^\s*(?:(?:worked\s+)?example\s+[A-Za-z0-9]+|"
    r"exercise\s+\d+(?:\.\d+)*(?:\s+q(?:uestion)?\s*\d+)?|"
    r"q(?:uestion)?\s*\d+)\s*[:：.)-]\s*",
    re.IGNORECASE,
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
_STANDALONE_CHECKPOINT_DIRECTIVE_RE = re.compile(
    r"^(?:summari[sz]e|describe|discuss|explain|compare|comment|"
    r"analy[sz]e|interpret|identify|write|list|state|trace|justify|"
    r"assess|evaluate|find|choose|show|why|how|what|which|who)\b",
    re.IGNORECASE,
)
_NON_TASK_NUMBERED_BLOCK_RE = re.compile(
    r"\\begin\{(?P<env>figure|tabular)\}.*?\\end\{(?P=env)\}",
    re.IGNORECASE | re.DOTALL,
)


def _normalized_inventory_heading(heading: str) -> str:
    """Normalize structural task headings, including OCR-spaced words."""
    value = _strip_section_number(str(heading or "")).strip()
    # ``_collapse_spaced_heading_word`` is defined with the source-topic
    # helpers below. Runtime lookup is intentional: this parser runs only after
    # the module is fully loaded.
    value = _collapse_spaced_heading_word(value)
    return re.sub(r"\s+", " ", value).strip().lower()


def _is_question_list_heading(heading: str) -> bool:
    """True for in-text QUESTION(S) blocks, never final exercises."""
    return _normalized_inventory_heading(heading) in {"question", "questions"}


def _inventory_task_without_solution(text: str, *, aggressive: bool = False) -> str:
    """Return only the assessable prompt, never a worked answer/solution."""
    text = (text or "").strip()
    match = _SOLUTION_START_RE.search(text)
    if match is None and aggressive:
        match = re.search(
            r"(?i)(?<=[?!.])\s+(?:(?:solutions?|answers?)|soln?|ans)\s*"
            r"(?:[:：.]|[-–—])",
            text,
        )
    if match is None and aggressive:
        match = re.search(
            r"(?i)(?<=[?？])\s+(?:(?:yes|no)\s*,\s*(?:since|because)|"
            r"we\s+know\s+that|we\s+can\s+see\s+that|thus|therefore|"
            r"hence|clearly)\b",
            text,
        )
    if match is not None:
        text = text[:match.start()]
    return re.sub(r"\s+", " ", text).strip(" \n\t")


def _strip_leading_source_task_label(text: str) -> str:
    """Remove a textbook source label while preserving the actual task."""
    return _LEADING_SOURCE_TASK_LABEL_RE.sub("", str(text or ""), count=1).strip()


def _mask_non_task_numbered_blocks(text: str) -> str:
    """Hide numeric rows/captions while retaining source string offsets."""
    return _NON_TASK_NUMBERED_BLOCK_RE.sub(
        lambda match: re.sub(r"[^\n]", " ", match.group(0)),
        str(text or ""),
    )


def _is_chapter_wide_task_section(
    section: dict, *, section_index: int,
    paired_sections: list[tuple[str, dict]],
) -> bool:
    """Whether a final generic review block semantically spans the chapter."""
    heading = _normalized_inventory_heading(section.get("heading") or "")
    if (
        not _CHAPTER_WIDE_TASK_HEADING_RE.match(heading)
        or section.get("heading_number_prefix")
    ):
        return False
    # A generic task block followed by another real source topic is local to
    # the current teaching unit. A final generic block is chapter-wide.
    current_topic = paired_sections[section_index][0]
    return not any(
        later_topic != current_topic
        for later_topic, _ in paired_sections[section_index + 1:]
    )


def _question_prompts_from_text(text: str) -> list[str]:
    """Extract standalone interrogative prompts without treating prose as tasks.

    A deterministic completeness anchor must be high precision.  Questions
    embedded later in a quotation or explanatory paragraph are often rhetorical;
    the semantic inventory pass can still classify ambiguous prose.
    """
    prompts_out: list[str] = []
    # Mathpix image URLs contain query-string ``?`` characters; those are not
    # learner questions. Remove visual markup and expose list items as ordinary
    # source lines before scanning the prompt wording.
    text = _strip_source_visual_markup(str(text or ""))
    text = re.sub(
        r"\\(?:begin|end)\{(?:itemize|enumerate)\}", "\n", text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"(?im)^\s*\\item(?:\[[^\]]*\])?\s*", "", text)
    text = _inventory_comparison_text(text)
    source = str(text or "").translate(str.maketrans({"？": "?", "！": "!"}))
    for source_line in source.splitlines():
        normalized = re.sub(r"\s+", " ", source_line).strip()
        if not normalized:
            continue
        matches = list(_QUESTION_SENTENCE_RE.finditer(normalized))
        for match_index, match in enumerate(matches):
            prefix = normalized[:match.start()].strip(" '\"“”‘’*-")
            leading_connector = bool(re.fullmatch(
                r"(?i)(?:and|but|or|now)", prefix))
            if prefix and not leading_connector:
                continue
            prompt = (
                normalized[:match.end()].strip()
                if leading_connector
                else match.group("question").strip()
            )
            # A printed discussion prompt can carry contextual prose and
            # several related questions on one source line. Preserve that
            # complete occurrence when the line ends with its last question.
            last_match = matches[-1]
            compound_prompt = bool(
                match_index == 0
                and last_match.end() == len(normalized)
                and last_match.end() > match.end()
            )
            if compound_prompt:
                prompt = normalized[:last_match.end()].strip()
            # A conditional callback on the same source line completes the
            # preceding ask. Keeping it with that prompt prevents the public
            # Example from silently dropping the requested explanation.
            followup = _CONDITIONAL_QUESTION_FOLLOWUP_RE.match(
                normalized[match.end():])
            if followup is not None:
                prompt = f"{prompt} {followup.group('question').strip()}"
            directive = re.match(
                r"(?i)^\s+(?P<directive>"
                r"(?:Record|Write|Discuss|Explain|Describe|Compare|Identify|"
                r"Give|State|List|Note)\b[^.!?]*(?:[.!?]|$))",
                normalized[match.end():],
            )
            if directive is not None:
                prompt = (
                    f"{prompt} {directive.group('directive').strip()}"
                ).strip()
            if prompt and prompt not in prompts_out:
                prompts_out.append(prompt)
            if compound_prompt:
                break
    return prompts_out


_SCIENTIFIC_INVESTIGATION_CONTEXT_RE = re.compile(
    r"\b(?:simple|scientific)\s+experiments?\b.*?"
    r"\bchange\s+only\s+one\s+thing\s+at\s+a\s+time\b.*?"
    r"\bsystematic\s+investigation\b",
    re.IGNORECASE | re.DOTALL,
)
_HEADINGLESS_OVERVIEW_QUESTION_RE = re.compile(
    r"(?im)(?:^|(?<=[.!?])\s+|(?<=:)\s+|\blike\s+)"
    r"(?:but\s+|and\s+)?"
    r"(?P<question>"
    r"(?:have\s+you\s+noticed\b|"
    r"(?:what|why|how|when|where|who|whom|whose|which)\b|"
    r"(?:can|could|do|does|did|is|are|was|were|will|would|should|"
    r"has|have|had|may)\b)"
    r"[^?.\n]{8,500}\?)",
)
_HEADINGLESS_OVERVIEW_COMPANION_RE = re.compile(
    r"(?i)^\s+(?P<companion>Or\s+how\b[^.!?\n]{8,400}\.)",
)
_NEGATIVE_AUXILIARY_QUESTION_RE = re.compile(
    r"(?i)^(?:is|are|was|were|has|have|had|do|does|did|can|could|"
    r"will|would|should|may)n['\N{RIGHT SINGLE QUOTATION MARK}]t\b",
)
_DIRECT_PROMPT_LEADING_CONNECTOR_RE = re.compile(
    r"(?i)^(?:and|or|but)\s+",
)
_DIRECT_PROMPT_CONTEXT_STOP_WORDS = frozenset({
    "a", "an", "and", "are", "but", "by", "can", "could", "did", "do",
    "does", "for", "from", "had", "has", "have", "how", "in", "is", "it",
    "may", "of", "on", "or", "should", "the", "this", "to", "was", "were",
    "what", "when", "where", "which", "who", "whom", "whose", "why", "will",
    "would", "you",
})
_SCIENTIFIC_INVESTIGATION_PROMPT_RES = (
    (
        "Scientific investigation: variable question",
        re.compile(
            r"\bWhat\s+are\s+the\s+different\s+things\s+that\s+may\s+"
            r"change\b[^?]{8,400}\?",
            re.IGNORECASE,
        ),
    ),
    (
        "Scientific investigation: controls and observations",
        re.compile(
            r"\bwhat\s+all\s+can\s+(?:we|you)\s+change\s+or\s+control\s+"
            r"when\s+(?:we|you)\s+do\s+the\s+experiment,\s*and\s+what\s+"
            r"all\s+can\s+(?:we|you)\s+observe\s+to\s+see\s+if\s+these\s+"
            r"changes\s+made\s+any\s+difference\.",
            re.IGNORECASE,
        ),
    ),
    (
        "Scientific investigation: observation and measurement",
        re.compile(
            r"\b(?:However,\s*)?to\s+make\s+sense\s+of\s+the\s+changes,\s*"
            r"(?:we|you)\s+also\s+need\s+to\s+think\s+of\s+what\s+"
            r"(?:we|you)\s+can\s+observe\s+or\s+measure\.",
            re.IGNORECASE,
        ),
    ),
    (
        "Scientific investigation: measuring an outcome",
        re.compile(
            r"\bMaybe\s+(?:we|you)\s+can\s+start\s+by\s+checking\s+whether\b"
            r"[^.]{8,500}?\bor\s+(?:we|you)\s+can\s+measure\b[^.]{8,500}\.",
            re.IGNORECASE,
        ),
    ),
    (
        "Scientific investigation: testing a changed condition",
        re.compile(
            r"\bWe\s+can\s+check\s+whether\b[^.]{8,400}\.",
            re.IGNORECASE,
        ),
    ),
    (
        "Scientific investigation: controlled variable comparison",
        re.compile(
            r"\bFor\s+example,\s*if\s+(?:we|you)\s+wanted\s+to\s+see\s+"
            r"the\s+effect\s+of\b[^.]{10,800}?\b(?:we|you)\s+would\b"
            r"[^.]{10,800}\.",
            re.IGNORECASE,
        ),
    ),
    (
        "Scientific investigation: recording observations",
        re.compile(
            r"\bIt\s+is\s+also\s+a\s+good\s+idea\s+to\s+keep\s+notes\s+"
            r"of\s+everything\s+that\s+you\s+see\s+and\s+sense\s+when\s+"
            r"doing\s+an\s+experiment\.\s*"
            r"(?:Did|What|Which|How)\b[^?]{8,400}\?",
            re.IGNORECASE,
        ),
    ),
    (
        "Scientific investigation: fresh and stored samples",
        re.compile(
            r"\bDo\b[^?]{8,300}?\bwhen\s+made\s+fresh\s+or\s+from\s+"
            r"stored\b[^?]{0,160}\?",
            re.IGNORECASE,
        ),
    ),
    (
        "Scientific investigation: changing the sample",
        re.compile(
            r"\bWhat\s+happens\s+if\s+(?:I|we|you)\b[^?]{8,300}?\b"
            r"before\b[^?]{3,160}\?",
            re.IGNORECASE,
        ),
    ),
)


def _direct_prompt_question_tokens(value: str) -> list[str]:
    """Normalize a question for a conservative single-gap comparison."""
    text = _inventory_comparison_text(str(value or "")).strip()
    text = _DIRECT_PROMPT_LEADING_CONNECTOR_RE.sub("", text, count=1)
    return re.findall(r"[a-z0-9]+", text.lower())


def _direct_prompt_single_gap(
        left: str, right: str) -> tuple[bool, tuple[str, ...]]:
    """Whether two questions differ by one short contiguous source phrase.

    This is deliberately narrower than fuzzy matching. It recognizes a
    repeated callback such as ``Why is one side [of a puri] thinner ...?``
    without treating reordered, paraphrased, or merely topically similar
    questions as the same source task. The returned tuple is the phrase
    omitted from the shorter question.
    """
    left_text = _inventory_comparison_text(str(left or "")).strip()
    right_text = _inventory_comparison_text(str(right or "")).strip()
    if not left_text.endswith("?") or not right_text.endswith("?"):
        return False, ()
    left_tokens = _direct_prompt_question_tokens(left_text)
    right_tokens = _direct_prompt_question_tokens(right_text)
    if min(len(left_tokens), len(right_tokens)) < 8:
        return False, ()
    if len(left_tokens) == len(right_tokens):
        return False, ()
    shorter, longer = sorted(
        (left_tokens, right_tokens), key=lambda tokens: len(tokens))
    if not 1 <= len(longer) - len(shorter) <= 3:
        return False, ()

    prefix = 0
    while (
        prefix < len(shorter)
        and shorter[prefix] == longer[prefix]
    ):
        prefix += 1
    suffix = 0
    while (
        suffix < len(shorter) - prefix
        and shorter[-(suffix + 1)] == longer[-(suffix + 1)]
    ):
        suffix += 1
    if prefix < 4 or suffix < 4 or prefix + suffix != len(shorter):
        return False, ()
    omitted = tuple(longer[prefix:len(longer) - suffix])
    return bool(omitted), omitted


def _headingless_overview_prompt_label(task: str, index: int) -> str:
    """Give each deterministic opening prompt a stable, non-colliding label."""
    tokens = set(_direct_prompt_question_tokens(task))
    labels = (
        (("dough", "rise"), "Chapter opening: dough rising"),
        (("world", "warmer"), "Chapter opening: global warming"),
        (("body", "healthy"), "Chapter opening: staying healthy"),
        (("fight", "infections"), "Chapter opening: fighting infections"),
        (("side", "puri", "thinner"), "Chapter opening: puri side thickness"),
        (
            ("noticed", "puri", "phulka"),
            "Chapter opening: puri and phulka observations",
        ),
        (("puff", "balloon"), "Chapter opening: puri balloon puffing"),
    )
    for terms, label in labels:
        if all(term in tokens for term in terms):
            return label
    return f"Chapter opening question {index:02d}"


def _headingless_overview_prose_prompts(text: str) -> list[dict]:
    """Recover explicit opening questions from a headingless science chapter.

    Ordinary prose questions remain semantic-model territory because many are
    rhetorical. This backstop activates only when the same section contains a
    complete, mechanically recognizable scientific-investigation passage. It
    scans only the overview before that passage and accepts questions at strong
    sentence, colon, or textbook ``like ...?`` boundaries.
    """
    source = _inventory_comparison_text(str(text or ""))
    if not _SCIENTIFIC_INVESTIGATION_CONTEXT_RE.search(source):
        return []
    method_prompts = _scientific_investigation_prose_prompts(source)
    if not method_prompts:
        return []
    cutoff = min(prompt["position"] for prompt in method_prompts)
    overview = source[:cutoff]
    # Preserve character offsets while preventing Mathpix image query strings
    # (``?height=...``) from being mistaken for prose punctuation.
    masked = re.sub(
        r"!\[[^\]]*\]\([^)]+\)",
        lambda match: " " * len(match.group(0)),
        overview,
    )
    candidates: list[dict] = []
    for match in _HEADINGLESS_OVERVIEW_QUESTION_RE.finditer(masked):
        start, end = match.span("question")
        task = re.sub(r"\s+", " ", overview[start:end]).strip()
        if (
            not task
            or _NEGATIVE_AUXILIARY_QUESTION_RE.match(task)
            or re.search(
                r"(?:\.{3}|\N{HORIZONTAL ELLIPSIS})\s*\?$", task)
        ):
            continue
        if re.match(r"(?i)^have\s+you\s+noticed\b", task):
            companion = _HEADINGLESS_OVERVIEW_COMPANION_RE.match(
                masked[match.end():])
            if companion is not None:
                companion_start = match.end() + companion.start("companion")
                companion_end = match.end() + companion.end("companion")
                companion_task = re.sub(
                    r"\s+",
                    " ",
                    overview[companion_start:companion_end],
                ).strip()
                task = f"{task} {companion_task}"

        duplicate_index = None
        for index, existing in enumerate(candidates):
            same_callback, _omitted = _direct_prompt_single_gap(
                existing["task"], task)
            if (
                same_callback
                and abs(existing["position"] - start) <= 1500
            ):
                duplicate_index = index
                break
        if duplicate_index is not None:
            existing = candidates[duplicate_index]
            if len(task) > len(existing["task"]):
                candidates[duplicate_index] = {
                    "position": start,
                    "task": task,
                }
            continue
        candidates.append({"position": start, "task": task})

    candidates.sort(key=lambda item: item["position"])
    for index, candidate in enumerate(candidates, start=1):
        candidate["label"] = _headingless_overview_prompt_label(
            candidate["task"], index)
    return candidates


def _scientific_investigation_prose_prompts(text: str) -> list[dict]:
    """Recover explicit method tasks from an unlabelled investigation passage.

    Introductory science chapters can present a complete investigation as
    headingless prose.  The semantic inventory normally identifies its prompts,
    but the source still gives us mechanically unambiguous task boundaries:
    posing the variable question, selecting controls and measurements, carrying
    out one-variable comparisons, and recording or extending observations. The
    context guard keeps this backstop away from ordinary explanatory examples.
    """
    source = _inventory_comparison_text(str(text or ""))
    if not _SCIENTIFIC_INVESTIGATION_CONTEXT_RE.search(source):
        return []
    prompts_out: list[dict] = []
    for label, pattern in _SCIENTIFIC_INVESTIGATION_PROMPT_RES:
        match = pattern.search(source)
        if match is None:
            continue
        prompts_out.append({
            "label": label,
            "position": match.start(),
            "task": re.sub(r"\s+", " ", match.group(0)).strip(),
        })
    return sorted(prompts_out, key=lambda item: item["position"])


def _activity_has_assessable_response(text: str) -> bool:
    """Whether an Activity explicitly asks for a learner-produced response."""
    # Query strings in Mathpix URLs are not learner question marks. Strip
    # visuals and expose LaTeX list items as ordinary lines before detecting
    # questions or response-producing imperatives.
    text = _strip_source_visual_markup(str(text or ""))
    text = re.sub(
        r"\\(?:begin|end)\{(?:itemize|enumerate)\}", "\n", text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"(?im)^\s*\\item(?:\[[^\]]*\])?\s*", "", text)
    text = _inventory_comparison_text(text)
    value = str(text or "").translate(str.maketrans({"？": "?", "！": "!"}))
    if "?" in value:
        return True
    return bool(
        re.search(
            r"(?im)^\s*(?:[-*]\s*)?(?:describe|explain|write|record|draw|"
            r"observe|discuss|compare|identify|interpret|analy[sz]e|justify|"
            r"comment|imagine|investigate|design|test|deduce|measure|note)\b",
            value,
        )
        or re.search(
            r"(?im)^[^\n]{0,220}\b(?:compare|discuss|draw|identify|"
            r"observe|record|write|deduce|measure|note)\b",
            value,
        )
    )


def _trim_activity_ocr_bleed(text: str) -> str:
    """Stop an Activity when OCR has joined following textbook prose.

    Mathpix can insert an Activity in the middle of a surrounding paragraph.
    The resumed prose then remains in the Activity section until the next
    heading.  A lowercase continuation immediately after ``\\end{figure}`` is
    strong structural evidence of that OCR splice, not another instruction.
    The same defect can appear before a figure: a one-line question followed
    by a lowercase fragment, or an instruction paragraph followed by a blank
    line and unrelated exposition.  Keep only that first task paragraph in
    those high-confidence forms; multi-step procedure activities remain whole.
    """
    value = str(text or "")
    # Some activities print the expected interpretation immediately after the
    # learner's question and then continue with genuine investigation
    # directives. Remove only this highly specific answer sentence.
    value = re.sub(
        r"(?i)(?<=[?？])\s+Simple\s+steps\s+like\s+good\s+sanitation\s+"
        r"can\s+greatly\s+reduce\s+the\s+spread\s+of\s+communicable\s+"
        r"diseases\.\s*",
        " ",
        value,
        count=1,
    )
    bullet_matches = list(_BULLET_TASK_START_RE.finditer(value))
    if len(bullet_matches) >= 2:
        first_bullet = value[
            bullet_matches[0].end():bullet_matches[1].start()
        ]
        second_bullet = value[bullet_matches[1].end():].lstrip()
        if (
            ("?" in first_bullet or "？" in first_bullet)
            and re.match(
                r"(?i)(?:Most\s+of\s+us\s+would|"
                r"To\s+tackle\s+the\s+problem\b)",
                second_bullet,
            )
        ):
            return (
                value[:bullet_matches[1].start()].rstrip()
                + "\n\\end{itemize}"
            )
    lines = value.splitlines()
    first_task_line = next(
        (
            index for index, line in enumerate(lines)
            if line.strip() and not line.lstrip().startswith("#")
        ),
        None,
    )
    if first_task_line is not None:
        first_line = _inventory_comparison_text(lines[first_task_line].strip())
        next_nonempty = next(
            (
                line.strip() for line in lines[first_task_line + 1:]
                if line.strip()
            ),
            "",
        )
        if first_line.endswith(("?", "？")) and next_nonempty[:1].islower():
            return "\n".join(lines[:first_task_line + 1]).rstrip()

        paragraph_end = first_task_line + 1
        while paragraph_end < len(lines) and lines[paragraph_end].strip():
            paragraph_end += 1
        first_paragraph = " ".join(
            line.strip() for line in lines[first_task_line:paragraph_end]
        )
        first_paragraph = _inventory_comparison_text(first_paragraph)
        if (
            ("?" in first_paragraph or "？" in first_paragraph)
            and paragraph_end < len(lines)
            and not re.match(
                r"\\begin\{(?:itemize|enumerate)\}",
                first_line,
                re.IGNORECASE,
            )
        ):
            return "\n".join(lines[:paragraph_end]).rstrip()
    # A table/figure can be the final part of the procedure. A new question
    # after that closed block is a distinct follow-up occurrence, not licence
    # to absorb its supplied result and answer into the Activity anchor.
    for match in re.finditer(
        r"\\end\{(?:figure|table)\}", value, re.IGNORECASE
    ):
        suffix = value[match.end():].lstrip()
        if re.match(
            r"(?i)(?:"
            r"(?:but\s+|and\s+|now\s+)?"
            r"(?:what|why|how|when|where|who|which|can|could|do|does|"
            r"did|is|are|was|were|will|would|should|has|have|may)\b"
            r"|You\s+will\s+observe\b"
            r"|After\s+some\s+time,\s+you\s+may\s+notice\b"
            r")",
            suffix,
        ):
            return value[:match.end()].rstrip()
    inline_result = re.search(
        r"(?i)\bAfter\s+some\s+time,\s+you\s+may\s+notice(?:\s+that)?\b",
        value,
    )
    if inline_result is not None:
        paragraph_tail = value[inline_result.start():]
        paragraph_boundary = re.search(r"\n\s*\n", paragraph_tail)
        paragraph = (
            paragraph_tail[:paragraph_boundary.start()]
            if paragraph_boundary is not None
            else paragraph_tail
        )
        last_question = max(paragraph.rfind("?"), paragraph.rfind("？"))
        if last_question >= 0:
            return (
                value[:inline_result.start()]
                + paragraph[:last_question + 1]
            ).rstrip()
        return value[:inline_result.start()].rstrip()
    # Textbooks commonly put the observation/derivation immediately after an
    # Activity without introducing another heading. It is answer material,
    # not part of the learner task. Stop only on high-confidence result
    # phrases at a paragraph boundary, keeping the complete procedure,
    # questions, tables-to-fill, and figures that precede it.
    result_paragraph = re.search(
        r"(?im)^(?:"
        r"In\s+this\s+Activity(?:,\s*|\s+)(?:you\s+will\s+find|we\s+observe)"
        r"|It\s+is\s+observed\s+that"
        r"|You\s+will\s+observe\b"
        r"|After\s+some\s+time,\s+you\s+may\s+notice(?:\s+that)?"
        r"|You\s+may\s+notice\s+that"
        r"|You\s+may\s+find\s+that"
        r"|You\s+may\s+observe\s+(?:small|that)\b"
        r"|This\s+(?:indicates|shows)\s+that"
        r"|By\s+studying\s+the\s+Table\b.*?\bwe\s+can\b"
        r")\b",
        value,
    )
    if result_paragraph is not None:
        # When the printed result paragraph itself ends in new reasoning
        # questions (and no figure/table boundary separated it), retain the
        # result as necessary context through the final question, but exclude
        # the explanatory answer paragraph that follows.
        paragraph_tail = value[result_paragraph.start():]
        paragraph_boundary = re.search(r"\n\s*\n", paragraph_tail)
        paragraph = (
            paragraph_tail[:paragraph_boundary.start()]
            if paragraph_boundary is not None
            else paragraph_tail
        )
        last_question = max(paragraph.rfind("?"), paragraph.rfind("ï¼Ÿ"))
        if re.search(
            r"\\begin\{(?:figure|table)\}",
            paragraph_tail[len(paragraph):],
            re.IGNORECASE,
        ):
            return value[:result_paragraph.start()].rstrip()
        if (
            last_question >= 0
            and re.match(
                r"(?i)(?:After\s+some\s+time,\s+you\s+may\s+notice\b|"
                r"You\s+may\s+(?:notice|observe)\b)",
                paragraph,
            )
        ):
            return (
                value[:result_paragraph.start()]
                + paragraph[:last_question + 1]
            ).rstrip()
        return value[:result_paragraph.start()].rstrip()

    for match in re.finditer(r"\\end\{figure\}", value, re.IGNORECASE):
        suffix = value[match.end():].lstrip()
        if suffix and suffix[0].islower():
            return value[:match.end()].rstrip()
    return value


def _activity_followup_prompts(text: str) -> list[str]:
    """Recover distinct learner asks after a mechanically bounded Activity.

    A Mathpix Activity section often also contains its printed result and the
    questions that follow it. The Activity anchor must not swallow those
    separate occurrences, but deterministic coverage should still retain
    high-confidence question paragraphs for the semantic inventory merge.
    """
    cleaned = _strip_source_visual_markup(str(text or ""))
    prompts_out: list[str] = []
    for paragraph in re.split(r"\n\s*\n", cleaned):
        normalized = re.sub(r"\s+", " ", paragraph).strip()
        if not normalized:
            continue
        direct = _question_prompts_from_text(normalized)
        if direct:
            for prompt in direct:
                if prompt not in prompts_out:
                    prompts_out.append(prompt)
            continue

        matches = list(_QUESTION_SENTENCE_RE.finditer(normalized))
        if not matches:
            continue
        result_context = re.match(
            r"(?i)(?:You\s+will\s+observe\b|"
            r"After\s+some\s+time,\s+you\s+may\s+notice\b|"
            r"You\s+may\s+(?:notice|observe)\b)",
            normalized,
        )
        if result_context is not None:
            prompt = matches[0].group("question").strip()
            if prompt and prompt not in prompts_out:
                prompts_out.append(prompt)
            continue

        # A contextual statement followed by two or more questions and no
        # supplied answer is one compound learner prompt. Preserve its givens.
        if len(matches) >= 2 and matches[-1].end() == len(normalized):
            prompt = normalized[:matches[-1].end()].strip()
            if prompt and prompt not in prompts_out:
                prompts_out.append(prompt)
    return prompts_out


def _trim_scientific_method_infographic(text: str) -> str:
    """Keep Observations through Application, excluding following prose."""
    value = str(text or "")
    application = re.search(r"(?im)^[ \t]*Application[ \t]*$", value)
    if application is None:
        return value
    suffix = value[application.end():]
    boundary = re.search(r"\n[ \t]*\n", suffix)
    if boundary is None:
        return value
    return value[:application.end() + boundary.start()].rstrip()


def _trim_standalone_checkpoint_ocr_bleed(text: str) -> str:
    """Trim a direct checkpoint prompt before adjacent resumed source prose."""
    value = _strip_public_source_heading(str(text or ""))
    lines = value.splitlines()
    first_index = next(
        (index for index, line in enumerate(lines) if line.strip()), None)
    if first_index is None:
        return value
    first_line = _inventory_comparison_text(lines[first_index].strip())
    if (
        not _STANDALONE_CHECKPOINT_DIRECTIVE_RE.match(first_line)
        or not first_line.endswith((".", "?", "!", "…", "？", "！"))
    ):
        return value
    next_nonempty = next(
        (line.strip() for line in lines[first_index + 1:] if line.strip()),
        "",
    )
    if (
        next_nonempty[:1].islower()
        or re.match(r"\\begin\{(?:figure|table)\}", next_nonempty,
                    re.IGNORECASE)
    ):
        return first_line
    return value


def _clean_list_task_fragment(text: str) -> str:
    """Remove list/page scaffolding that is not part of the source prompt."""
    value = re.split(
        r"(?i)\bPrepare\s+some\s+questions\s+based\s+on\s+your\b",
        str(text or ""),
        maxsplit=1,
    )[0]
    value = re.sub(
        r"\\(?:begin|end)\{itemize\}", " ", value, flags=re.IGNORECASE)
    value = re.sub(r"\$(?:\s*\\_){2,}\s*\$", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _callout_task_from_match(text: str, match: re.Match) -> str:
    """Read one ``?`` callout without swallowing the following explanation."""
    lines = str(text or "")[match.end():].splitlines()
    if not lines:
        return ""
    parts: list[str] = []
    first = lines[0].strip()
    if first:
        parts.append(first)
    in_figure = False
    for line_index, line in enumerate(lines[1:], start=1):
        stripped = line.strip()
        if in_figure:
            parts.append(stripped)
            if re.search(r"\\end\{figure\}", stripped, re.IGNORECASE):
                in_figure = False
            continue
        if not stripped:
            if parts:
                break
            continue
        if re.match(r"\\end\{itemize\}", stripped, re.IGNORECASE):
            next_content = next(
                (
                    later.strip()
                    for later in lines[line_index + 1:]
                    if later.strip()
                    and not re.match(
                        r"\\(?:begin|end)\{itemize\}",
                        later.strip(),
                        re.IGNORECASE,
                    )
                ),
                "",
            )
            if re.match(
                r"(?i)(?:\\item\s*\[\s*\([ivxlcdm]+\)\s*\]|"
                r"\([ivxlcdm]+\)|\([a-d]\))",
                next_content,
            ):
                continue
            break
        if re.match(r"\\begin\{itemize\}", stripped, re.IGNORECASE):
            continue
        if not parts:
            parts.append(stripped)
            continue
        if re.match(r"\\begin\{figure\}", stripped, re.IGNORECASE):
            in_figure = True
            parts.append(stripped)
            continue
        if (
            re.match(
                r"(?i)(?:hint\s*:|\\item\s*\[\s*\([ivxlcdm]+\)\s*\]|"
                r"\([ivxlcdm]+\)|\([a-d]\))",
                stripped,
            )
            or re.match(r"!\[[^\]]*\]\(", stripped)
        ):
            parts.append(stripped)
            continue
        if (
            parts
            and re.match(r"!\[[^\]]*\]\(", parts[-1])
            and stripped[:1].islower()
        ):
            # Mathpix can replace a word/formula in the middle of one prompt
            # with a cropped image on its own line. The lowercase continuation
            # still belongs to the same source sentence.
            parts.append(stripped)
            continue
        break
    return _inventory_task_without_solution(
        "\n".join(parts), aggressive=True)


def _inventory_chunk_has_task_markers(chunk: dict) -> bool:
    """Whether source text explicitly signals an assessable inventory item."""
    for section in chunk.get("sections") or []:
        body = str(section.get("body") or "").strip()
        if not body:
            continue
        heading = str(section.get("heading") or "")
        if (
            _EXERCISE_RE.search(heading)
            or _is_question_list_heading(heading)
            or _CHAPTER_WIDE_TASK_HEADING_RE.match(
                _normalized_inventory_heading(heading))
        ):
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
    paired = _sections_with_source_topics(sections)
    canonical_topics = _topic_headings(sections)

    def topic_word_family(value: str) -> str:
        words = re.findall(r"[A-Za-z]{4,}", str(value or "").lower())
        if not words:
            return ""
        word = words[0]
        if word == "cubic":
            return "cube"
        if word.endswith("ies") and len(word) > 5:
            return word[:-3] + "y"
        if word.endswith("s") and not word.endswith("ss"):
            return word[:-1]
        return word

    def section_task_topic(default_topic: str, heading: str) -> str:
        """Use a clear subheading-to-main-topic family match for task scope."""
        heading_family = topic_word_family(heading)
        if not heading_family or _is_non_topic_heading(heading):
            return default_topic
        matches = [
            candidate
            for candidate in canonical_topics
            if topic_word_family(candidate) == heading_family
        ]
        return matches[0] if len(matches) == 1 else default_topic

    def source_anchor_context(
        *, section_index: int, position: int, task: str,
    ) -> str:
        """Recover compact source context for explicit backward references."""
        if not (0 <= section_index < len(paired)):
            return ""
        body = str(paired[section_index][1].get("body") or "")
        prefix = body[:max(0, position)]
        task_key = _topic_comparison_key(
            _strip_source_visual_markup(str(task or "")))

        if re.search(
            r"\b(?:fill|complete)\b.{0,80}\btable(?:s)?\s+below\b",
            task_key,
            re.IGNORECASE,
        ):
            # Mathpix commonly places a table on the block after a short
            # callout such as ``Fill the table below.``. The callout parser
            # intentionally stops at that blank line so it does not absorb
            # supplied answers or later exposition. Preserve the table as
            # bounded source context instead: collect only tabular blocks
            # before the next explicit callout in this source section.
            task_window = body[max(0, position):]
            source_directive = re.search(
                (
                    r"\b(?:fill|complete)\b.{0,120}?"
                    r"\btable(?:s)?\s+below\b[.!?:]?"
                ),
                task_window,
                flags=re.IGNORECASE | re.DOTALL,
            )
            if source_directive is not None:
                task_window = task_window[source_directive.end():]
            # A Mathpix image of the same table may sit between the directive
            # and its machine-readable tabular transcription. Treat only
            # tightly adjacent visual wrappers as transparent.
            task_window = task_window.lstrip()
            while task_window:
                visual_match = next(
                    (
                        match
                        for pattern in (
                            _LATEX_FIGURE_BLOCK_RE,
                            _LATEX_INCLUDEGRAPHICS_RE,
                            _MARKDOWN_IMAGE_RE,
                            _BRACKET_IMAGE_RE,
                        )
                        if (match := pattern.match(task_window)) is not None
                    ),
                    None,
                )
                if visual_match is None:
                    break
                task_window = task_window[visual_match.end():].lstrip()
            # Activate only when the table is structurally adjacent. Otherwise
            # a later table in the same long source section may belong to
            # explanatory prose or a different checkpoint.
            if re.match(
                r"(?is)^\\begin\{tabular\}",
                task_window,
            ):
                boundaries = []
                next_callout = _CALLOUT_TASK_START_RE.search(task_window)
                if next_callout is not None:
                    boundaries.append(next_callout.start())
                prefix_lower = prefix.lower()
                for list_name in ("itemize", "enumerate"):
                    if prefix_lower.rfind(
                        f"\\begin{{{list_name}}}",
                    ) <= prefix_lower.rfind(f"\\end{{{list_name}}}"):
                        continue
                    list_end = re.search(
                        rf"\\end\{{{list_name}\}}",
                        task_window,
                        re.IGNORECASE,
                    )
                    if list_end is not None:
                        boundaries.append(list_end.start())
                if boundaries:
                    task_window = task_window[:min(boundaries)]
                    table_blocks = re.findall(
                        r"\\begin\{tabular\}.*?\\end\{tabular\}",
                        task_window,
                        flags=re.IGNORECASE | re.DOTALL,
                    )
                else:
                    # Without a source-owned boundary, only the immediately
                    # adjacent table can be assigned confidently. Do not absorb
                    # later tables merely because no further callout exists.
                    first_table = re.match(
                        (
                            r"(?is)^\s*"
                            r"(\\begin\{tabular\}.*?\\end\{tabular\})"
                        ),
                        task_window,
                    )
                    table_blocks = (
                        [first_table.group(1)] if first_table is not None else [])
                table_context = _public_task_without_latex_layout(
                    "\n".join(table_blocks)).strip()
                if table_context:
                    return (
                        "Use the following source table(s): "
                        f"{table_context}"
                    )

        if "this sum" in task_key:
            display_math = re.findall(
                r"\$\$(.+?)\$\$", prefix, flags=re.DOTALL)
            if display_math:
                expression = display_math[-1].strip().rstrip(" .")
                return f"The referenced sum is $${expression}$$."

        if (
            "arrange them" in task_key
            or "do the same" in task_key
            or "more than one way" in task_key
        ):
            paragraphs = [
                re.sub(r"\s+", " ", _strip_source_visual_markup(part)).strip()
                for part in re.split(r"\n\s*\n", prefix)
            ]
            for paragraph in reversed(paragraphs):
                paragraph_key = _topic_comparison_key(paragraph)
                if "arrang" in paragraph_key and (
                    "adjacent" in paragraph_key
                    and "square" in paragraph_key
                ):
                    return paragraph

        if "this insight" in task_key:
            matches = re.findall(
                r"(?i)(We\s+see\s+in\s+some\s+cases,\s+like\b[^.\n]*\.)",
                prefix,
            )
            if matches:
                return re.sub(r"\s+", " ", matches[-1]).strip()

        if "locker" in task_key or "these five" in task_key:
            context = (
                "In the 100-locker puzzle, Person 1 opens every locker and "
                "each Person k from 2 through 100 toggles every kth locker."
            )
            if "these five" in task_key:
                clue = re.findall(
                    r"(?i)(The\s+passcode\s+consists\s+of\b[^\"\n]*[.\"]?)",
                    prefix,
                )
                if clue:
                    clue_text = clue[-1].strip(' "')
                    context = f"{context} {clue_text}"
            return context
        return ""

    def append_anchor(
        *, section_index: int, position: int, topic: str, kind: str,
        label: str, parent_label: str, task: str, chapter_wide: bool = False,
        activity_origin: bool = False, prefer_task_boundary: bool = False,
    ) -> None:
        task = _strip_leading_source_task_label(
            _inventory_task_without_solution(task, aggressive=(
                kind in {"worked_example", "solved_example"})))
        if not task:
            return
        shared_context = source_anchor_context(
            section_index=section_index,
            position=position,
            task=task,
        )
        ordered.append((section_index, position, {
            "source_kind": kind,
            "source_label": label,
            "parent_source_label": parent_label,
            "topic_hint": "" if chapter_wide else topic,
            "page_hint": "",
            "block_ids": [],
            "raw_task": task,
            "raw_solution_or_answer": "",
            "normalized_task": task,
            "shared_context": shared_context,
            "subpart_label": "",
            "image_urls": re.findall(
                r"https?://cdn\.mathpix\.com/[^)\s}\]]+", task),
            "content_objects": {},
            "requires_visual": bool(re.search(
                r"\bfig(?:ure)?\.?\s*\d|!\[[^\]]*\]\(", task,
                re.IGNORECASE)),
            "requires_context": bool(shared_context),
            "_topic_scope": "chapter" if chapter_wide else "topic",
            "_activity_origin": activity_origin,
            "_source_section_index": section_index,
            "_source_position": position,
            "_source_task_boundary": (
                "direct_prompt" if prefer_task_boundary else ""),
        }))

    for section_index, (topic, section) in enumerate(paired):
        body = section.get("body") or ""
        heading = section.get("heading") or ""
        topic = section_task_topic(topic, heading)
        chapter_wide = _is_chapter_wide_task_section(
            section, section_index=section_index, paired_sections=paired)
        example_matches = list(_WORKED_EXAMPLE_START_RE.finditer(body))
        for i, match in enumerate(example_matches):
            end = (
                example_matches[i + 1].start()
                if i + 1 < len(example_matches)
                else len(body)
            )
            label = f"Example {match.group(1)}"
            append_anchor(
                section_index=section_index,
                position=match.start(),
                topic=topic,
                kind="worked_example",
                label=label,
                parent_label="",
                task=body[match.end():end],
            )

        for overview_prompt in _headingless_overview_prose_prompts(body):
            append_anchor(
                section_index=section_index,
                position=overview_prompt["position"],
                topic=topic,
                kind="intext_question",
                label=overview_prompt["label"],
                parent_label="Chapter opening",
                task=overview_prompt["task"],
                prefer_task_boundary=True,
            )

        for investigation_prompt in _scientific_investigation_prose_prompts(body):
            append_anchor(
                section_index=section_index,
                position=investigation_prompt["position"],
                topic=topic,
                kind="experiment_task",
                label=investigation_prompt["label"],
                parent_label="Scientific investigation",
                task=investigation_prompt["task"],
                prefer_task_boundary=True,
            )

        container_heading = _normalized_inventory_heading(heading)
        if (
            not example_matches
            and container_heading == "think like a scientist"
        ):
            append_anchor(
                section_index=section_index,
                position=0,
                topic=topic,
                kind="activity",
                label=heading,
                parent_label=heading,
                task=_trim_scientific_method_infographic(body),
                chapter_wide=False,
                activity_origin=False,
            )
            continue
        if (
            not example_matches
            and _DISCOVER_PROJECT_HEADING_RE.match(container_heading)
        ):
            project_matches = list(_BULLET_TASK_START_RE.finditer(body))
            for project_index, match in enumerate(project_matches, start=1):
                end = (
                    project_matches[project_index].start()
                    if project_index < len(project_matches)
                    else len(body)
                )
                append_anchor(
                    section_index=section_index,
                    position=match.start(),
                    topic=topic,
                    kind="activity",
                    label=f"{heading} Project {project_index}",
                    parent_label=heading,
                    task=_clean_list_task_fragment(
                        _strip_source_visual_markup(body[match.end():end])),
                    chapter_wide=True,
                )
            if project_matches:
                continue
        if (
            not example_matches
            and re.match(
                r"^(?:activity|project)\b", container_heading,
                re.IGNORECASE,
            )
        ):
            is_project = bool(re.match(
                r"^project\b", container_heading, re.IGNORECASE))
            activity_source = body
            activity_payload = re.sub(
                r"(?m)^\s*#{1,6}[^\n]*(?:\n|$)",
                "",
                str(body),
                count=1,
            ).strip()
            if (
                not activity_payload
                and section_index + 1 < len(paired)
            ):
                next_topic, next_section = paired[section_index + 1]
                next_heading = str(next_section.get("heading") or "")
                if (
                    next_topic == topic
                    and not next_section.get("heading_number_prefix")
                    and not re.match(
                        r"^(?:activity|project|snapshots?|"
                        r"keep\s+the\s+curiosity\s+alive|"
                        r"discover\s*,?\s*design)",
                        _normalized_inventory_heading(next_heading),
                        re.IGNORECASE,
                    )
                ):
                    activity_source = (
                        body.rstrip()
                        + "\n\n"
                        + re.sub(
                            r"(?m)^\s*#{1,6}[^\n]*(?:\n|$)",
                            "",
                            str(next_section.get("body") or ""),
                            count=1,
                        ).strip()
                    )
            activity_body = _trim_activity_ocr_bleed(activity_source)
            assessable_activity = (
                not is_project
                and _activity_has_assessable_response(activity_body)
            )
            append_anchor(
                section_index=section_index,
                position=0,
                topic=topic,
                # Activity prompts are assessable source tasks as well as
                # Activity/Info Hub material. A single inventory item feeds both
                # destinations, avoiding two qids for the same source wording.
                kind=(
                    "checkpoint_question"
                    if assessable_activity
                    else "activity"
                ),
                label=heading or f"Activity {section_index + 1}",
                parent_label=heading,
                task=activity_body,
                chapter_wide=chapter_wide,
                activity_origin=assessable_activity,
            )
            activity_remainder = (
                activity_source[len(activity_body):]
                if activity_source.startswith(activity_body)
                else ""
            )
            for followup_index, followup in enumerate(
                _activity_followup_prompts(activity_remainder), start=1
            ):
                append_anchor(
                    section_index=section_index,
                    position=len(activity_body) + followup_index,
                    topic=topic,
                    kind="intext_question",
                    label=(
                        f"{heading or f'Activity {section_index + 1}'} "
                        f"Follow-up {followup_index}"
                    ),
                    parent_label=heading,
                    task=followup,
                    chapter_wide=chapter_wide,
                    prefer_task_boundary=True,
                )
            continue
        is_question_list_heading = _is_question_list_heading(heading)
        is_exercise_list_heading = bool(
            re.search(
                r"\b(?:exercises?|ex\.|review|practice|problems?|"
                r"figure\s+it\s+out)\b",
                f"{heading} {container_heading}",
                re.IGNORECASE,
            )
            or (chapter_wide and not is_question_list_heading)
        )
        is_task_list_heading = (
            is_exercise_list_heading or is_question_list_heading)
        list_item_kind = (
            "exercise" if is_exercise_list_heading else "intext_question")
        if is_task_list_heading:
            task_matches = list(_NUMBERED_TASK_START_RE.finditer(
                _mask_non_task_numbered_blocks(body)))
            for i, match in enumerate(task_matches):
                end = (
                    task_matches[i + 1].start()
                    if i + 1 < len(task_matches)
                    else len(body)
                )
                question_label = f"{heading} Q{match.group(1)}".strip()
                task_text = _clean_list_task_fragment(body[match.end():end])
                append_anchor(
                    section_index=section_index,
                    position=match.start(),
                    topic=topic,
                    kind=list_item_kind,
                    label=question_label,
                    parent_label=heading,
                    task=task_text,
                    chapter_wide=chapter_wide,
                )
            if not task_matches and body.strip():
                append_anchor(
                    section_index=section_index,
                    position=0,
                    topic=topic,
                    kind="checkpoint_question",
                    label=heading or f"Checkpoint {section_index + 1}",
                    parent_label=heading,
                    task=body,
                    chapter_wide=chapter_wide,
                )

        body_exercise_markers = list(_BODY_FIGURE_IT_OUT_RE.finditer(body))
        if not is_task_list_heading:
            for marker_index, marker in enumerate(body_exercise_markers):
                marker_end = (
                    body_exercise_markers[marker_index + 1].start()
                    if marker_index + 1 < len(body_exercise_markers)
                    else len(body)
                )
                exercise_body = body[marker.end():marker_end]
                task_matches = list(_NUMBERED_TASK_START_RE.finditer(
                    _mask_non_task_numbered_blocks(exercise_body)))
                body_exercise_chapter_wide = not any(
                    later_topic != topic
                    for later_topic, _ in paired[section_index + 1:]
                )
                for task_index, task_match in enumerate(task_matches):
                    end = (
                        task_matches[task_index + 1].start()
                        if task_index + 1 < len(task_matches)
                        else len(exercise_body)
                    )
                    append_anchor(
                        section_index=section_index,
                        position=marker.end() + task_match.start(),
                        topic=topic,
                        kind="exercise",
                        label=f"Figure it Out Q{task_match.group(1)}",
                        parent_label="Figure it Out",
                        task=_clean_list_task_fragment(
                            exercise_body[task_match.end():end]),
                        chapter_wide=body_exercise_chapter_wide,
                    )

        body_exercise_marker_starts = {
            marker.start() for marker in body_exercise_markers
        }
        callout_matches = (
            []
            if is_task_list_heading or example_matches
            else [
                match
                for match in _CALLOUT_TASK_START_RE.finditer(body)
                if match.start() not in body_exercise_marker_starts
            ]
        )
        for callout_index, match in enumerate(callout_matches, start=1):
            task = _callout_task_from_match(body, match)
            append_anchor(
                section_index=section_index,
                position=match.start(),
                topic=topic,
                kind="checkpoint_question",
                label=f"Checkpoint {section_index + 1}.{callout_index}",
                parent_label=heading,
                task=task,
                # Generated checkpoint ordinals shift whenever an earlier
                # source prompt is recovered. Match these explicit callouts
                # by their authoritative wording, never by the stale ordinal.
                prefer_task_boundary=True,
            )

        # Explicit checkpoint questions often live inside prose/boxed blocks
        # rather than under a numbered exercise heading. Restrict the
        # deterministic backstop to structurally named containers so rhetorical
        # questions inside quotations/explanatory prose do not become tasks.
        if (
            not is_task_list_heading
            and not example_matches
            and not callout_matches
            and _CHECKPOINT_CONTAINER_HEADING_RE.match(
                _strip_section_number(heading))
        ):
            question_prompts = _question_prompts_from_text(body)
            for question_index, task in enumerate(question_prompts, start=1):
                append_anchor(
                    section_index=section_index,
                    position=body.find(task),
                    topic=topic,
                    kind="checkpoint_question",
                    label=f"Checkpoint {section_index + 1}.{question_index}",
                    parent_label=heading,
                    task=task,
                    prefer_task_boundary=True,
                )
            if not question_prompts:
                checkpoint_task = _trim_standalone_checkpoint_ocr_bleed(body)
                append_anchor(
                    section_index=section_index,
                    position=0,
                    topic=topic,
                    kind="checkpoint_question",
                    label=heading or f"Checkpoint {section_index + 1}",
                    parent_label=heading,
                    task=checkpoint_task,
                    prefer_task_boundary=(
                        checkpoint_task != _strip_public_source_heading(body)),
                )
        elif (
            not is_task_list_heading
            and not example_matches
            and not callout_matches
        ):
            # Multiple standalone interrogative paragraphs form an unlabelled
            # checkpoint list. A lone question in exposition is ambiguous and
            # remains a semantic-inventory decision.
            question_prompts = _question_prompts_from_text(body)
            if len(question_prompts) >= 2:
                for question_index, task in enumerate(
                    question_prompts, start=1
                ):
                    append_anchor(
                        section_index=section_index,
                        position=body.find(task),
                        topic=topic,
                        kind="checkpoint_question",
                        label=f"Checkpoint {section_index + 1}.{question_index}",
                        parent_label=heading,
                        task=task,
                        prefer_task_boundary=True,
                    )
    return [
        item for _, _, item in sorted(ordered, key=lambda row: (row[0], row[1]))
    ]


def _sanitize_inventory_item(
    item: dict, *, source_topic: str = "", chapter_wide: bool = False,
) -> dict:
    """Normalize one GPT inventory row and remove answer material."""
    cleaned = dict(item)
    kind = str(cleaned.get("source_kind") or "other").strip().lower()
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
    raw_task = _strip_leading_source_task_label(raw_task)
    normalized = _strip_leading_source_task_label(normalized or raw_task)
    cleaned["raw_task"] = raw_task
    cleaned["normalized_task"] = normalized or raw_task
    cleaned["raw_solution_or_answer"] = ""
    options = cleaned.get("options") or []
    structured_option_kind = kind in {"mcq", "assertion_reason"}
    if isinstance(options, list) and structured_option_kind:
        rendered_options: list[str] = []
        for index, option in enumerate(options):
            if isinstance(option, dict):
                label = str(option.get("label") or chr(65 + index)).strip()
                text = str(
                    option.get("text") or option.get("option") or "").strip()
            else:
                label = chr(65 + index)
                text = str(option or "").strip()
            if text:
                rendered_options.append(f"({label}) {text}")
        # A structured options list is authoritative for MCQ fidelity. Replace
        # an existing labelled option tail instead of appending to it: model
        # output occasionally retains stale option text while also returning
        # the corrected structured list, which used to produce two conflicting
        # ``(A)``/``(B)`` sets in the public Example.
        raw_task = _replace_structured_mcq_options(
            raw_task, rendered_options)
        cleaned["options"] = rendered_options
        cleaned["raw_task"] = raw_task
        cleaned["normalized_task"] = (
            raw_task if rendered_options else (normalized or raw_task)
        )
    elif options:
        # Lettered multipart prompts such as ``(a) ... (b) ...`` are not MCQ
        # option tails. If a model emits an ``options`` array for a non-
        # objective source item, discard that unsupported metadata rather than
        # truncating or appending to the authoritative question.
        cleaned["options"] = []
    cleaned["topic_hint"] = (
        str(cleaned.get("topic_hint") or "").strip()
        if chapter_wide
        else (
            source_topic
            or str(cleaned.get("topic_hint") or "").strip()
            or "General"
        )
    )
    cleaned["_topic_scope"] = "chapter" if chapter_wide else "topic"
    cleaned.setdefault("content_objects", {})
    cleaned["image_urls"] = list(dict.fromkeys(
        str(url or "").strip().rstrip("})]")
        for url in (cleaned.get("image_urls") or [])
        if str(url or "").strip()
    ))
    cleaned.setdefault("requires_visual", False)
    cleaned.setdefault("requires_context", False)
    return cleaned


_MCQ_OPTION_MARKER_RE = re.compile(
    r"(?<![\w])(?:\(\s*([A-Ha-h])\s*\)|"
    r"\[\s*([A-Ha-h])\s*\]|([A-Ha-h])[.)])\s*"
)


def _mcq_option_tail(
    value: str,
) -> tuple[str, tuple[tuple[str, str], ...]] | None:
    """Split a conventional A-H option tail from its question stem.

    At least two ordered labels are required so prose parentheses and
    lettered explanatory subparts are not treated as an MCQ merely because
    they contain one ``(a)`` token.
    """
    text = str(value or "").strip()
    matches = list(_MCQ_OPTION_MARKER_RE.finditer(text))
    if len(matches) < 2:
        return None
    for start in range(len(matches) - 1):
        labels = [
            next(group for group in match.groups() if group).upper()
            for match in matches[start:]
        ]
        expected_labels = [
            chr(ord("A") + offset) for offset in range(len(labels))
        ]
        if labels != expected_labels:
            continue
        options: list[tuple[str, str]] = []
        for offset, match in enumerate(matches[start:]):
            end = (
                matches[start + offset + 1].start()
                if start + offset + 1 < len(matches)
                else len(text)
            )
            option_text = text[match.end():end].strip(" \t\r\n;")
            if not option_text:
                break
            label = next(
                group for group in match.groups() if group).upper()
            options.append((label, option_text))
        if len(options) < 2:
            continue
        stem = text[:matches[start].start()].rstrip(" \t\r\n;:-")
        if stem:
            return stem, tuple(options)
    return None


def _replace_structured_mcq_options(
    raw_task: str, rendered_options: list[str],
) -> str:
    """Render one canonical option block from structured MCQ options."""
    task = str(raw_task or "").strip()
    if not rendered_options:
        return task
    parsed = _mcq_option_tail(task)
    if parsed is not None:
        task = parsed[0]
    elif _MCQ_OPTION_MARKER_RE.search(task):
        # A partial/non-consecutive lettered block is more likely a multipart
        # prompt or malformed extraction than a safe option tail. Preserve it
        # for validation instead of appending a second canonical block.
        return task
    else:
        # Preserve unusual source formatting when no complete option tail can
        # be identified, while avoiding exact duplicate option fragments.
        normalized_task = bi.normalize_question_text(task)
        rendered_options = [
            rendered for rendered in rendered_options
            if bi.normalize_question_text(rendered) not in normalized_task
        ]
    return " ".join([task, *rendered_options]).strip()


_LEADING_INVENTORY_TASK_NUMBER_RE = re.compile(
    r"^\s*(?:q(?:uestion)?\s*)?\d+\s*[.)]\s*",
    re.IGNORECASE,
)


_INVENTORY_MOJIBAKE_RE = re.compile(r"(?:Ã.|Â.|â.|ï¼)")

# The upload path can discard one CP1252 control byte from a full-width
# punctuation sequence. Repair intact forms directly before attempting a
# generic UTF-8 round-trip, then handle the incomplete prefix conservatively.
_FULLWIDTH_MOJIBAKE_REPAIRS = {
    "\u00ef\u00bc\u017d": ".",
    "\u00ef\u00bc\u02c6": "(",
    "\u00ef\u00bc\u2030": ")",
    "\u00ef\u00bc\u0178": "?",
    "\u00ef\u00bc\u0152": ",",
}
_MOJIBAKE_UTF8_FRAGMENT_RE = re.compile(
    r"(?:[\u00c2\u00c3](?:[\u0080-\u00ff]|[\u0192\u0152\u017d\u0178"
    r"\u02c6\u02dc\u20ac\u201a\u201e\u2026\u2020\u2021\u2030\u0160"
    r"\u2039\u2018\u2019\u201c\u201d\u2022\u2013\u2014\u2122\u0161"
    r"\u203a\u0153\u017e]))|(?:\u00e2(?:[\u0080-\u00ff]|[\u20ac\u201a"
    r"\u0192\u201e\u2026\u2020\u2021\u02c6\u2030\u0160\u2039\u0152"
    r"\u017d\u2018\u2019\u201c\u201d\u2022\u2013\u2014\u02dc\u2122"
    r"\u0161\u203a\u0153\u017e\u0178])(?:[\u0080-\u00ff]|[\u20ac\u201a"
    r"\u0192\u201e\u2026\u2020\u2021\u02c6\u2030\u0160\u2039\u0152"
    r"\u017d\u2018\u2019\u201c\u201d\u2022\u2013\u2014\u02dc\u2122"
    r"\u0161\u203a\u0153\u017e\u0178]))",
)


def _inventory_comparison_text(value: str) -> str:
    """Repair common UTF-8 decoding damage for comparison and public text.

    Resumed inventories can predate the current UTF-8 source parser.  Keep
    their teacher-facing text untouched, but make ``HÃ¼bner`` match
    ``Hübner`` and the CP1252 rendering of full-width Figure punctuation match
    the current source anchor.  Re-encode only strings that carry a familiar
    mojibake signature and only when the round trip is valid UTF-8.
    """
    text = str(value or "")
    for _ in range(2):
        if not _INVENTORY_MOJIBAKE_RE.search(text):
            break
        repaired = ""
        # Most damaged checkpoint strings are a Latin-1 rendering of UTF-8.
        # Full-width punctuation is often rendered through CP1252 instead.
        for encoding in ("latin-1", "cp1252"):
            try:
                candidate = text.encode(encoding).decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError):
                continue
            if candidate != text:
                repaired = candidate
                break
        if not repaired:
            break
        text = repaired

    for damaged, replacement in _FULLWIDTH_MOJIBAKE_REPAIRS.items():
        text = text.replace(damaged, replacement)
    text = re.sub(r"\u00ef\u00bc(?=[A-Za-z])", " - ", text)

    def repair_fragment(match: re.Match) -> str:
        fragment = match.group(0)
        for encoding in ("cp1252", "latin-1"):
            try:
                return fragment.encode(encoding).decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError):
                continue
        return fragment

    for _ in range(2):
        repaired = _MOJIBAKE_UTF8_FRAGMENT_RE.sub(repair_fragment, text)
        if repaired == text:
            break
        text = repaired
    return unicodedata.normalize("NFKC", text)


def _inventory_task_match_key(item: dict) -> str:
    """Task text normalized for GPT-row/deterministic-anchor matching."""
    task = bi.normalize_question_text(_inventory_comparison_text(
        item.get("raw_task") or item.get("normalized_task") or ""))
    return _LEADING_INVENTORY_TASK_NUMBER_RE.sub("", task, count=1)


_GENERIC_SOURCE_LABEL_RE = re.compile(
    r"^(?:activity|discuss|discussion|project|questions?|checkpoint|"
    r"figure\s+it\s+out|think\s+about\s+it|"
    r"let(?:'s|\s+us)\s+discuss)$",
    re.IGNORECASE,
)
_SOURCE_LABEL_SUBPART_SUFFIX_RE = re.compile(
    r"\s*\(\s*(?:[a-z]|[ivxlcdm]+|\d+)\s*\)\s*$",
    re.IGNORECASE,
)


def _source_label_is_generic(label: str) -> bool:
    normalized = bi.normalize_question_text(_inventory_comparison_text(label))
    if _GENERIC_SOURCE_LABEL_RE.fullmatch(
            _collapse_spaced_heading_word(normalized)):
        return True
    numbered = re.fullmatch(
        r"(?P<base>.+?)\s+(?:q(?:uestion)?\s*)?\d+(?:\.\d+)*",
        normalized,
        re.IGNORECASE,
    )
    if numbered is None:
        return False
    # A textbook can restart ``QUESTIONS Q1`` under every topic.  That label
    # identifies a position within a local list, not a chapter-wide task.
    # Exercise and numbered Activity labels remain authoritative.
    return _collapse_spaced_heading_word(numbered.group("base")) in {
        "questions", "checkpoint",
    }


def _inventory_question_label_root(label: str) -> str:
    """Normalize ``Question 2(ii)`` and ``Q2`` to one parent-question key."""
    value = _SOURCE_LABEL_SUBPART_SUFFIX_RE.sub("", str(label or "").strip())
    value = re.sub(
        r"\s*\(\s*optional\s*\)\s*\*?",
        " ",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(r"\bquestion\s*(?=\d)", "q", value, flags=re.IGNORECASE)
    return _topic_comparison_key(value)


def _inventory_label_has_subpart(label: str) -> bool:
    return bool(_SOURCE_LABEL_SUBPART_SUFFIX_RE.search(str(label or "").strip()))


def _inventory_scope_conflicts(item: dict, anchor: dict) -> bool:
    """Whether two task rows point at different source occurrences."""
    for field in (
        "topic_hint",
        "parent_source_label",
        "_source_section_index",
    ):
        item_value = item.get(field)
        anchor_value = anchor.get(field)
        if item_value in (None, "") or anchor_value in (None, ""):
            continue
        if field == "_source_section_index":
            if str(item_value).strip() != str(anchor_value).strip():
                return True
            continue
        if _topic_comparison_key(str(item_value)) != _topic_comparison_key(
            str(anchor_value)
        ):
            return True
    return False


def _inventory_has_specific_shared_scope(item: dict, anchor: dict) -> bool:
    """Require positive local evidence before trusting a non-unique label."""
    for field in ("topic_hint", "_source_section_index"):
        item_value = item.get(field)
        anchor_value = anchor.get(field)
        if item_value in (None, "") or anchor_value in (None, ""):
            continue
        if field == "_source_section_index":
            if str(item_value).strip() == str(anchor_value).strip():
                return True
            continue
        if _topic_comparison_key(str(item_value)) == _topic_comparison_key(
            str(anchor_value)
        ):
            return True
    return False


_INVENTORY_SUPERSCRIPT_TRANSLATION = str.maketrans({
    "⁰": "^0",
    "¹": "^1",
    "²": "^2",
    "³": "^3",
    "⁴": "^4",
    "⁵": "^5",
    "⁶": "^6",
    "⁷": "^7",
    "⁸": "^8",
    "⁹": "^9",
    "ⁿ": "^n",
})


def _inventory_semantic_task_key(item: dict) -> str:
    """Normalize equivalent Unicode/LaTeX task notation for source matching."""
    task = str(
        item.get("raw_task") or item.get("normalized_task") or ""
    ).translate(_INVENTORY_SUPERSCRIPT_TRANSLATION)
    task = _inventory_comparison_text(task)
    task = _strip_source_visual_markup(task)
    task = _normalize_exact_source_text(task)
    task = re.sub(
        r"\b([a-z])\s+(st|nd|rd|th)\b",
        r"\1\2",
        task,
        flags=re.IGNORECASE,
    )
    return _LEADING_INVENTORY_TASK_NUMBER_RE.sub("", task, count=1)


def _inventory_items_match(item: dict, anchor: dict) -> bool:
    label = bi.normalize_question_text(_inventory_comparison_text(
        item.get("source_label", "")))
    anchor_label = bi.normalize_question_text(_inventory_comparison_text(
        anchor.get("source_label", "")))
    if _inventory_scope_conflicts(item, anchor):
        return False
    task = _inventory_task_match_key(item)
    anchor_task = _inventory_task_match_key(anchor)
    shorter_task, longer_task = sorted((task, anchor_task), key=len)
    semantic_task = _inventory_semantic_task_key(item)
    semantic_anchor_task = _inventory_semantic_task_key(anchor)
    shorter_semantic, longer_semantic = sorted(
        (semantic_task, semantic_anchor_task), key=len)
    semantic_task_compatible = bool(
        semantic_task
        and semantic_anchor_task
        and (
            semantic_task == semantic_anchor_task
            or (
                len(shorter_semantic) >= 30
                and shorter_semantic in longer_semantic
            )
        )
    )
    label_task_compatible = bool(
        not task
        or not anchor_task
        or task == anchor_task
        or semantic_task_compatible
        or (
            len(shorter_task) >= 40
            and shorter_task in longer_task
        )
    )
    if (
        label
        and anchor_label
        and label == anchor_label
        and not _source_label_is_generic(label)
        and _inventory_has_specific_shared_scope(item, anchor)
        and label_task_compatible
    ):
        return True
    if not task or not anchor_task:
        return False
    if task == anchor_task:
        return True
    if semantic_task_compatible:
        return True
    if (
        anchor.get("_source_task_boundary") == "direct_prompt"
        and len(task) >= 20
        and task.endswith("?")
        and (
            anchor_task.endswith(task)
            or anchor_task.startswith(task)
        )
    ):
        # A semantic extraction can retain only the final observation question
        # and omit its immediately preceding recording instruction.  The
        # deterministic direct-prompt anchor is one source occurrence, so match
        # the unique question tail and let the merge restore its full context.
        return True
    return len(shorter_task) >= 40 and shorter_task in longer_task


def _normalized_inventory_source_label(item: dict) -> str:
    return bi.normalize_question_text(_inventory_comparison_text(
        item.get("source_label", "")))


def _anchor_source_label_counts(anchors: list[dict]) -> Counter:
    return Counter(
        label
        for anchor in anchors
        if (label := _normalized_inventory_source_label(anchor))
    )


def _inventory_matches_anchor_identity(
    item: dict, anchor: dict, label_counts: Counter,
) -> bool:
    """Match by content/scope, or by an unambiguous authoritative label."""
    if _inventory_items_match(item, anchor):
        return True
    # Ordinal checkpoint labels can shift when a newly recovered question is
    # inserted before an old checkpoint. Content, not the generated ordinal,
    # must identify direct prompts.
    if anchor.get("_source_task_boundary") == "direct_prompt":
        return False
    item_label = _normalized_inventory_source_label(item)
    anchor_label = _normalized_inventory_source_label(anchor)
    if anchor.get("_activity_origin"):
        if (
            item_label
            and item_label == anchor_label
            and _inventory_semantic_task_key(item)
            == _inventory_semantic_task_key(anchor)
        ):
            # A checkpoint can have the exact short Activity prompt but stale
            # semantic metadata (kind, topic, or Activity origin). Preserve the
            # identity match so checkpoint refresh reports and repairs that
            # semantic drift instead of misclassifying it as a missing task.
            return True
        item_root = re.search(
            r"\bactivity\s+\d+(?:\.\d+)*\b", item_label,
            re.IGNORECASE,
        )
        anchor_root = re.search(
            r"\bactivity\s+\d+(?:\.\d+)*\b", anchor_label,
            re.IGNORECASE,
        )
        same_activity = bool(
            item_root
            and anchor_root
            and item_root.group(0).lower() == anchor_root.group(0).lower()
        )
        if not same_activity:
            return False
        item_topic = _topic_comparison_key(str(item.get("topic_hint") or ""))
        anchor_topic = _topic_comparison_key(
            str(anchor.get("topic_hint") or ""))
        if item_topic and anchor_topic and item_topic != anchor_topic:
            return False
        item_semantic = _inventory_semantic_task_key(item)
        anchor_semantic = _inventory_semantic_task_key(anchor)
        if (
            min(len(item_semantic), len(anchor_semantic)) >= 120
            and (
                item_semantic in anchor_semantic
                or anchor_semantic in item_semantic
            )
        ):
            # Adding an earlier recovered source block shifts persisted section
            # indexes. A stale semantic Activity may also contain the supplied
            # result/answer after the full procedure. The exact Activity
            # number, topic, and complete contained procedure identify one
            # occurrence; refresh it from the authoritative trimmed anchor.
            return True
        # One Activity can own a full procedure and several separate
        # observation/reasoning prompts. Match only near-identical
        # representations of the complete procedure, never a child by label.
        item_words = set(item_semantic.split())
        anchor_words = set(anchor_semantic.split())
        union = item_words | anchor_words
        overlap = len(item_words & anchor_words) / len(union) if union else 0.0
        return bool(
            min(len(item_words), len(anchor_words)) >= 8
            and overlap >= 0.80
        )
    if not (
        item_label
        and item_label == anchor_label
        and label_counts.get(anchor_label, 0) == 1
    ):
        return False
    return bool(
        item_label and anchor_label
    )


def _inventory_item_covers_anchor(item: dict, anchor: dict) -> bool:
    """Whether a resumed item retains the anchor's complete current task.

    Matching labels identify which row to refresh, but never prove content
    completeness.  A GPT item may legitimately contain *more* context/options
    than the deterministic anchor; the reverse (a truncated checkpoint item
    contained by today's full anchor) is stale and must be refreshed.
    """
    item_task = _inventory_task_match_key(item)
    anchor_task = _inventory_task_match_key(anchor)
    if not item_task or not anchor_task:
        return False
    if item_task == anchor_task:
        return True
    anchor_is_authoritative = bool(
        anchor.get("_activity_origin")
        or (anchor.get("source_kind") or "").strip().lower()
        in _HUB_INVENTORY_KINDS
        or anchor.get("_source_task_boundary") == "direct_prompt"
    )
    return bool(
        not anchor_is_authoritative
        and len(anchor_task) >= 40
        and anchor_task in item_task
    )


def _merge_source_task_anchors(items: list[dict], anchors: list[dict]) -> list[dict]:
    """Backfill missing deterministic anchors and canonicalize matching rows."""
    merged = [dict(item) for item in items]
    anchor_label_counts = _anchor_source_label_counts(anchors)
    atomic_source_anchors = [
        anchor for anchor in anchors
        if (
            (task := _inventory_semantic_task_key(anchor))
            and len(task) >= 15
        )
    ]
    compound_umbrella_ids: set[int] = set()
    for item in merged:
        item_label = bi.normalize_question_text(
            _inventory_comparison_text(item.get("source_label") or ""))
        if item_label and not _source_label_is_generic(item_label):
            continue
        item_task = _inventory_semantic_task_key(item)
        if not item_task:
            continue
        covered_anchors = 0
        for anchor in atomic_source_anchors:
            anchor_task = _inventory_semantic_task_key(anchor)
            if anchor_task == item_task or anchor_task not in item_task:
                continue
            item_topic = item.get("topic_hint")
            anchor_topic = anchor.get("topic_hint")
            if (
                item_topic not in (None, "")
                and anchor_topic not in (None, "")
                and _topic_comparison_key(str(item_topic))
                != _topic_comparison_key(str(anchor_topic))
            ):
                continue
            covered_anchors += 1
            if covered_anchors >= 2:
                compound_umbrella_ids.add(id(item))
                break
    if compound_umbrella_ids:
        # A semantic pass can summarize two explicit inline questions as one
        # extra umbrella row while the deterministic anchors retain both exact
        # prompts. Keeping all three would duplicate the source occurrence.
        # Remove only unlabeled/generically labeled umbrellas containing at
        # least two complete authoritative questions.
        merged = [
            item for item in merged if id(item) not in compound_umbrella_ids
        ]
    anchors_by_question_root: dict[str, list[dict]] = {}
    for anchor in anchors:
        root = _inventory_question_label_root(
            anchor.get("source_label") or "")
        if root:
            anchors_by_question_root.setdefault(root, []).append(anchor)
    authoritative_parent_roots = {
        root
        for root, candidates in anchors_by_question_root.items()
        if len(candidates) == 1
        and not _source_label_is_generic(
            candidates[0].get("source_label") or "")
        and not candidates[0].get("_activity_origin")
        and (
            str(candidates[0].get("source_kind") or "").strip().lower()
            not in _HUB_INVENTORY_KINDS
        )
        and candidates[0].get("_source_task_boundary") != "direct_prompt"
        and not (candidates[0].get("subpart_label") or "").strip()
        and not _inventory_label_has_subpart(
            candidates[0].get("source_label") or "")
    }
    if authoritative_parent_roots:
        # GPT sometimes emits both an umbrella question and one row per
        # subpart even though the source parser proves that the complete
        # multi-part question is one assessable unit. Remove all GPT children;
        # the full authoritative anchor is merged/appended below.
        merged = [
            item for item in merged
            if not (
                (
                    (item.get("subpart_label") or "").strip()
                    or _inventory_label_has_subpart(
                        item.get("source_label") or "")
                )
                and _inventory_question_label_root(
                    item.get("source_label") or "")
                in authoritative_parent_roots
            )
        ]
    parent_counts: dict[str, int] = {}
    for anchor in anchors:
        if not (
            (anchor.get("subpart_label") or "").strip()
            or _inventory_label_has_subpart(
                anchor.get("source_label") or "")
        ):
            continue
        parent = bi.normalize_question_text(
            anchor.get("parent_source_label", ""))
        if parent and not _source_label_is_generic(parent):
            parent_counts[parent] = parent_counts.get(parent, 0) + 1
    split_parent_labels = {
        parent for parent, count in parent_counts.items() if count > 1
    }
    if split_parent_labels:
        # A model may keep a compound Q1(a-e) as one item while deterministic
        # anchors split its independently assessable subparts. Remove only the
        # trace-equivalent parent row so the full question is not classified
        # once as an umbrella and again per concept.
        merged = [
            item for item in merged
            if bi.normalize_question_text(item.get("source_label", ""))
            not in split_parent_labels
        ]
    for anchor_index, anchor in enumerate(anchors):
        anchor_root = _inventory_question_label_root(
            anchor.get("source_label") or "")
        matching_indices = [
            i for i, item in enumerate(merged)
            if (
                _inventory_matches_anchor_identity(
                    item, anchor, anchor_label_counts)
                or (
                    anchor_root in authoritative_parent_roots
                    and _inventory_question_label_root(
                        item.get("source_label") or "")
                    == anchor_root
                )
            )
        ]
        match_index = matching_indices[0] if matching_indices else None
        if len(matching_indices) > 1:
            anchor_semantic = _inventory_semantic_task_key(anchor)

            def match_score(index: int) -> tuple[int, int, int, int]:
                candidate = merged[index]
                candidate_semantic = _inventory_semantic_task_key(candidate)
                return (
                    int(bool(
                        anchor_semantic
                        and candidate_semantic == anchor_semantic
                    )),
                    int(bool(
                        candidate.get("requires_context")
                        and candidate.get("shared_context")
                    )),
                    len(candidate.get("image_urls") or []),
                    len(str(
                        candidate.get("raw_task")
                        or candidate.get("normalized_task")
                        or ""
                    )),
                )

            match_index = max(matching_indices, key=match_score)
            chosen = merged[match_index]
            for index in matching_indices:
                if index == match_index:
                    continue
                candidate = merged[index]
                if (
                    not chosen.get("shared_context")
                    and candidate.get("shared_context")
                ):
                    chosen["shared_context"] = candidate["shared_context"]
                    chosen["requires_context"] = bool(
                        candidate.get("requires_context"))
                if candidate.get("image_urls"):
                    chosen["image_urls"] = list(dict.fromkeys(
                        list(chosen.get("image_urls") or [])
                        + list(candidate.get("image_urls") or [])
                    ))
            duplicate_ids = {
                id(merged[index])
                for index in matching_indices
                if index != match_index
            }
            merged = [
                item for item in merged if id(item) not in duplicate_ids
            ]
            match_index = next(
                index for index, item in enumerate(merged)
                if item is chosen
            )
            matching_indices = [match_index]
        if (
            match_index is not None
            and anchor.get("_source_task_boundary") == "direct_prompt"
        ):
            # One model pass can return both a complete prompt and a trailing
            # clause from that same prompt as separate inventory rows. Prefer
            # an exact full-task match (otherwise the longest match), then
            # discard only strict-contained fragments. When (and only when) an
            # exact canonical row exists, also discard a callback that differs
            # by one short phrase and whose semantic context retains that
            # phrase. Equal wording in distinct scopes remains untouched.
            anchor_task_key = _inventory_task_match_key(anchor)
            exact_indices = [
                index for index in matching_indices
                if _inventory_task_match_key(merged[index]) == anchor_task_key
            ]
            match_index = (
                exact_indices[0]
                if exact_indices
                else max(
                    matching_indices,
                    key=lambda index: len(
                        _inventory_task_match_key(merged[index])),
                )
            )
            chosen = merged[match_index]
            callback_indices: set[int] = set()
            if exact_indices:
                for index, candidate in enumerate(merged):
                    if index == match_index:
                        continue
                    candidate_task = _inventory_task_match_key(candidate)
                    is_callback, omitted = _direct_prompt_single_gap(
                        candidate_task, anchor_task_key)
                    if not is_callback:
                        continue
                    scope_conflict = False
                    for field in (
                        "topic_hint", "parent_source_label", "page_hint",
                        "_source_section_index",
                    ):
                        candidate_scope = candidate.get(field)
                        anchor_scope = anchor.get(field)
                        if (
                            candidate_scope not in (None, "")
                            and anchor_scope not in (None, "")
                            and _topic_comparison_key(str(candidate_scope))
                            != _topic_comparison_key(str(anchor_scope))
                        ):
                            scope_conflict = True
                            break
                    if scope_conflict:
                        continue
                    meaningful_omissions = {
                        token for token in omitted
                        if token not in _DIRECT_PROMPT_CONTEXT_STOP_WORDS
                    }
                    semantic_context = _topic_comparison_key(" ".join((
                        str(candidate.get("normalized_task") or ""),
                        str(candidate.get("shared_context") or ""),
                    )))
                    if (
                        meaningful_omissions
                        and not meaningful_omissions.issubset(
                            set(semantic_context.split()))
                    ):
                        continue
                    callback_indices.add(index)
            fragment_ids = {
                id(merged[index])
                for index in set(matching_indices) | callback_indices
                if (
                    index != match_index
                    and (candidate_task := _inventory_task_match_key(
                        merged[index]))
                    and candidate_task != anchor_task_key
                    and len(candidate_task) < len(anchor_task_key)
                    and (
                        candidate_task in anchor_task_key
                        or index in callback_indices
                    )
                )
            }
            if fragment_ids:
                merged = [
                    item for item in merged if id(item) not in fragment_ids
                ]
                match_index = next(
                    index for index, item in enumerate(merged)
                    if item is chosen
                )
        if match_index is None:
            # Preserve source order when a model missed an anchor that appears
            # before a later anchor it did extract.  Without this look-ahead,
            # deterministic backfills are appended after the chapter's final
            # task even though their source positions are known.
            insert_index = next(
                (
                    item_index
                    for later_anchor in anchors[anchor_index + 1:]
                    for item_index, item in enumerate(merged)
                    if _inventory_items_match(item, later_anchor)
                ),
                None,
            )
            if insert_index is None:
                merged.append(dict(anchor))
            else:
                merged.insert(insert_index, dict(anchor))
            continue
        existing = merged[match_index]
        existing_task = str(
            existing.get("raw_task") or existing.get("normalized_task") or "")
        anchor_task = str(
            anchor.get("raw_task") or anchor.get("normalized_task") or "")
        # Deterministic anchors are coverage evidence, not permission to replace
        # a fuller GPT extraction. Keeping the longer task preserves MCQ options,
        # shared stems, conditions, and visual context.
        anchor_is_activity = bool(
            anchor.get("_activity_origin")
            or (anchor.get("source_kind") or "").strip().lower()
            in _HUB_INVENTORY_KINDS
        )
        existing_mcq = _mcq_option_tail(existing_task)
        anchor_mcq = _mcq_option_tail(anchor_task)
        conflicting_source_options = False
        if existing_mcq and anchor_mcq:
            existing_stem, existing_options = existing_mcq
            anchor_stem, anchor_options = anchor_mcq
            existing_stem_key = bi.normalize_question_text(existing_stem)
            anchor_stem_key = bi.normalize_question_text(anchor_stem)
            same_question = bool(
                existing_stem_key
                and anchor_stem_key
                and (
                    existing_stem_key == anchor_stem_key
                    or existing_stem_key in anchor_stem_key
                    or anchor_stem_key in existing_stem_key
                )
            )
            existing_option_key = tuple(
                (label, bi.normalize_question_text(text))
                for label, text in existing_options
            )
            anchor_option_key = tuple(
                (label, bi.normalize_question_text(text))
                for label, text in anchor_options
            )
            conflicting_source_options = (
                same_question and existing_option_key != anchor_option_key
            )
        authoritative_task = (
            anchor_task
            if (
                anchor_is_activity
                or anchor.get("_source_task_boundary") == "direct_prompt"
                or conflicting_source_options
            )
            else (
                existing_task
                if len(existing_task) >= len(anchor_task)
                else anchor_task
            )
        )
        for field in (
            "source_kind", "source_label", "parent_source_label", "topic_hint",
            "raw_solution_or_answer", "_topic_scope", "_activity_origin",
            "_source_section_index", "_source_position", "_source_task_boundary",
        ):
            if anchor.get(field) not in (None, ""):
                existing[field] = anchor.get(field)
        existing["raw_task"] = authoritative_task
        existing["normalized_task"] = authoritative_task
        if anchor.get("_source_task_boundary") == "direct_prompt":
            # The discarded GPT tail may have carried a nearby but unrelated
            # Figure. Start visual resolution again from the exact prompt.
            existing["image_urls"] = list(anchor.get("image_urls") or [])
            existing.pop("_image_captions", None)
            existing.pop("_figure_images_resolved", None)
            existing["requires_visual"] = bool(anchor.get("requires_visual"))
        elif anchor.get("image_urls"):
            existing["image_urls"] = list(dict.fromkeys(
                list(existing.get("image_urls") or [])
                + list(anchor.get("image_urls") or [])
            ))
        if anchor.get("_source_task_boundary") != "direct_prompt":
            existing["requires_visual"] = bool(
                existing.get("requires_visual") or anchor.get("requires_visual"))
        if anchor.get("requires_context") and anchor.get("shared_context"):
            existing["shared_context"] = anchor["shared_context"]
            existing["requires_context"] = True

    deduped: list[dict] = []
    seen_label_tasks: set[tuple[str, str, tuple]] = set()
    seen_tasks: set[tuple[str, tuple]] = set()
    for item in merged:
        label = bi.normalize_question_text(item.get("source_label", ""))
        task = bi.normalize_question_text(
            item.get("raw_task") or item.get("normalized_task") or "")
        # The same wording can be a distinct source occurrence when it belongs
        # to another topic, shared stem, activity boundary, or visual. Collapse
        # only trace-equivalent representations of the same occurrence.
        source_scope = (
            _topic_comparison_key(item.get("topic_hint") or ""),
            bi.normalize_question_text(item.get("parent_source_label") or ""),
            bi.normalize_question_text(item.get("shared_context") or ""),
            bool(item.get("_activity_origin")),
            tuple(sorted(str(url) for url in item.get("image_urls") or [])),
        )
        task_identity = (task, source_scope)
        label_task = (label, task, source_scope)
        if (
            (task and task_identity in seen_tasks)
            or (label and task and label_task in seen_label_tasks)
        ):
            continue
        if label and task:
            seen_label_tasks.add(label_task)
        if task:
            seen_tasks.add(task_identity)
        deduped.append(item)
    return deduped


def _unowned_inventory_row_is_non_task(item: dict) -> bool:
    """High-confidence semantic rows that describe content but ask nothing."""
    source_kind = str(item.get("source_kind") or "").strip().lower()
    raw = _strip_source_visual_markup(str(
        item.get("raw_task") or item.get("normalized_task") or ""))
    plain = re.sub(r"\s+", " ", raw).strip()
    if (
        source_kind == "source_task"
        and "?" not in plain
        and not re.match(
            r"(?i)^(?:analyse|analyze|compare|describe|discuss|draw|"
            r"explain|identify|interpret|investigate|list|observe|"
            r"prepare|record|state|study|write)\b",
            plain,
        )
    ):
        return True
    return bool(
        source_kind in {"intext_question", "checkpoint_question", "other"}
        and re.search(
            r"(?i)\?\s*(?:Let\s+us|We(?:'ll|\s+will)|You\s+will)\s+"
            r"(?:find\s+out|learn|see|explore)\.?\s*$",
            plain,
        )
    )


def _prune_unowned_stub_inventory_rows(
    items: list[dict], anchors: list[dict],
) -> tuple[list[dict], int]:
    """Remove only unusable model fragments with no exact source owner.

    Deterministic anchors are the source-of-truth safety net. A model-only
    empty or stub row cannot become a valid public Example and should not block
    an otherwise exact inventory, but an exact source-owned short task remains
    protected and must pass the normal terminal validation.
    """
    kept: list[dict] = []
    removed = 0
    for item in items:
        text = _inventory_task_text(item)
        key = _inventory_coverage_key(text)
        source_kind = (item.get("source_kind") or "").strip().lower()
        unusable = (
            not key
            or (
                source_kind not in _HUB_INVENTORY_KINDS
                and cv._example_too_short(text)
            )
        )
        item_key = _inventory_task_match_key(item)
        source_owned = any(
            item_key
            and item_key == _inventory_task_match_key(anchor)
            and (
                not (
                    bool(anchor.get("requires_visual"))
                    or bool(anchor.get("image_urls"))
                )
                or (
                    bool(item.get("requires_visual"))
                    and bool(item.get("image_urls"))
                )
            )
            for anchor in anchors
        )
        if (
            unusable
            or _unowned_inventory_row_is_non_task(item)
        ) and not source_owned:
            removed += 1
            continue
        kept.append(item)
    return kept, removed


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
        "activities": sum(
            kind == "activity" or bool(item.get("_activity_origin"))
            for kind, item in zip(kinds, items)
        ),
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
        "chapter_final_tasks": sum(
            bool(item.get("_chapter_wide_task"))
            or item.get("_topic_scope") == "chapter"
            for item in items
        ),
        "chapter_final_exercises": sum(
            (item.get("source_kind") or "").strip().lower() == "exercise"
            and (
                bool(item.get("_chapter_wide_task"))
                or item.get("_topic_scope") == "chapter"
            )
            for item in items
        ),
        "total_inventory_items": len(items),
    }


def _assign_chapter_wide_inventory_topics_via_api(
    *, meta: dict, inventory: dict, records: list[dict],
    source_topic_excerpts: list[dict], max_attempts: int = 3,
) -> dict:
    """Semantically place final chapter-review tasks across source topics."""
    import json as _json

    targets = [
        item for item in inventory.get("items") or []
        if item.get("_topic_scope") == "chapter"
    ]
    if not targets:
        return inventory
    topics: list[dict] = []
    excerpts_by_key = {
        _topic_comparison_key(group.get("topic") or ""):
        _trim(group.get("excerpt") or "", 30_000)
        for group in source_topic_excerpts or []
    }
    by_topic: dict[str, dict] = {}
    for record in records:
        topic = (record.get("topic") or "").strip()
        key = _topic_comparison_key(topic)
        if not key:
            continue
        entry = by_topic.setdefault(key, {
            "topic": topic,
            "concepts": [],
            "source_excerpt": excerpts_by_key.get(key, ""),
        })
        if not cr.is_culmination(record.get("concept_title", "")):
            entry["concepts"].append({
                "concept": record.get("concept_title", ""),
                "description": _concept_description_only(
                    record.get("concept_details", "")),
            })
    topics = list(by_topic.values())
    if not topics:
        raise RuntimeError(
            "chapter-wide task placement failed: no source topics/concepts")
    target_qids = {
        (item.get("qid") or "").strip() for item in targets
        if (item.get("qid") or "").strip()
    }
    valid_topics = {
        _topic_comparison_key(entry["topic"]): entry["topic"]
        for entry in topics
    }
    assigned: dict[str, str] = {}
    target_by_qid = {
        (item.get("qid") or "").strip(): item for item in targets
    }
    system = prompts.get_text("concepts.chapter_wide_task_topics.system")
    for attempt in range(1, max_attempts + 1):
        pending_qids = [
            qid for qid in target_by_qid if qid not in assigned
        ]
        if not pending_qids:
            break
        payload = {
            "source_topics": topics,
            "chapter_wide_tasks": [
                {
                    "qid": qid,
                    "task": _inventory_task_text(target_by_qid[qid]),
                    "source_kind": target_by_qid[qid].get("source_kind", ""),
                }
                for qid in pending_qids
            ],
        }
        user = (
            _metadata_block(meta)
            + "\nSOURCE TOPICS, CONCEPTS, AND CHAPTER-WIDE TASKS:\n"
            + _json.dumps(payload, ensure_ascii=False)
        )
        if attempt > 1:
            user += (
                "\n\nCORRECTION: Assign every pending qid exactly once using "
                "one exact topic string from source_topics. Your previous "
                "answer omitted a qid or used an invalid topic."
            )
        data = _openai_json(system, user, purpose="concept_mapping")
        rejected = 0
        for row in data.get("assignments") or []:
            if not isinstance(row, dict):
                rejected += 1
                continue
            qid = (row.get("qid") or "").strip()
            topic_key = _topic_comparison_key(row.get("topic") or "")
            if (
                qid not in pending_qids
                or qid in assigned
                or topic_key not in valid_topics
            ):
                rejected += 1
                continue
            assigned[qid] = valid_topics[topic_key]
        if rejected or len(assigned) < len(target_qids):
            progress.log(
                f"Chapter-wide task placement attempt {attempt}: "
                f"{len(assigned)}/{len(target_qids)} assigned; "
                f"{rejected} invalid row(s) rejected.",
                level="warning",
            )
    missing = sorted(target_qids - set(assigned))
    if missing:
        raise RuntimeError(
            "chapter-wide task placement did not return exact valid assignments: "
            f"missing={missing}"
        )
    for item in targets:
        item["topic_hint"] = assigned[(item.get("qid") or "").strip()]
    progress.log(
        f"Assigned {len(targets)} chapter-wide review task(s) to source topics.",
        level="success",
    )
    return inventory


def _extract_question_task_inventory_via_api(
    *, meta: dict, sections: list[dict], records: list[dict] | None = None,
) -> dict:
    import json as _json

    def sanitized_items(data: dict, chunk: dict) -> list[dict]:
        return [
            _sanitize_inventory_item(
                value,
                source_topic=chunk.get("source_topic") or "",
                chapter_wide=bool(chunk.get("chapter_wide_tasks")),
            )
            for value in (data.get("items") or [])
            if isinstance(value, dict)
        ]

    def invalid_task_indexes(items: list[dict]) -> list[int]:
        invalid: list[int] = []
        for index, item in enumerate(items):
            text = _inventory_task_text(item)
            source_kind = (item.get("source_kind") or "").strip().lower()
            if (
                not _inventory_coverage_key(text)
                or (
                    source_kind not in _HUB_INVENTORY_KINDS
                    and cv._example_too_short(text)
                )
            ):
                invalid.append(index)
        return invalid

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
        data = _openai_json(system, user, purpose="source_extraction")
        items = sanitized_items(data, chunk)
        invalid_indexes = invalid_task_indexes(items)
        # A chapter-scale chunk yielding a handful of items means the model
        # summarized question lists instead of itemizing them — retry once.
        expected_min = max(2, min(40, len(chunk["text"]) // 2_000))
        density_retry = (
            len(items) < expected_min
            and _inventory_chunk_has_task_markers(chunk)
        )
        if invalid_indexes or density_retry:
            progress.log(
                f"  inventory chunk {i}/{len(chunks)} needs correction: "
                f"{len(items)} item(s), {len(invalid_indexes)} empty/stub "
                f"row(s), expected at least {expected_min} item(s) for "
                f"{len(chunk['text']):,} chars — retrying once.",
                level="warning",
            )
            retry_user = (
                user
                + f"\n\nYOUR PREVIOUS ANSWER HAD {len(items)} ITEMS, INCLUDING "
                f"{len(invalid_indexes)} EMPTY OR STUB TASKS. Re-read the chunk "
                "because this is under-extraction, and itemize EVERY assessable "
                "question/task: every numbered exercise, every in-text checkpoint "
                "/ boxed '?' / 'Let's recall' prompt, every picture- or "
                "source-based ask (including chapter-opening source analysis), "
                "every activity, and every worked example is its own item. "
                "Multi-part questions with subquestions stay ONE item carrying "
                "the full stem + all subparts. Every returned raw_task must be "
                "complete and substantive. Never merge a question list into one "
                "item, and never skip a checkpoint."
            )
            retry_data = _openai_json(
                system, retry_user, purpose="source_extraction")
            retry_items = sanitized_items(retry_data, chunk)
            retry_invalid_indexes = invalid_task_indexes(retry_items)
            if invalid_indexes:
                original_substantive_count = (
                    len(items) - len(invalid_indexes))
                minimum_retry_count = max(1, original_substantive_count)
                if (
                    retry_invalid_indexes
                    or len(retry_items) < minimum_retry_count
                ):
                    raise RuntimeError(
                        "question inventory extraction returned "
                        f"{len(retry_invalid_indexes)} empty/stub task row(s) "
                        f"and {len(retry_items)} substantive row(s) after retry "
                        f"for chunk {i}, below the required "
                        f"{minimum_retry_count}; refusing to checkpoint an "
                        "inventory that cannot satisfy exact coverage"
                    )
                items = retry_items
            elif (
                not retry_invalid_indexes
                and len(retry_items) > len(items)
            ):
                items = retry_items
        for item in items:
            inventory["items"].append(item)

    anchors = _source_task_anchors(sections)
    inventory["items"] = _merge_source_task_anchors(
        inventory["items"], anchors)
    inventory["items"] = _attach_explicit_figure_images(
        inventory["items"], sections)
    inventory["items"], pruned_stubs = _prune_unowned_stub_inventory_rows(
        inventory["items"], anchors)
    if pruned_stubs:
        progress.log(
            "Pruned "
            f"{pruned_stubs} model-only empty/stub inventory row(s) with no "
            "exact deterministic source owner.",
            level="warning",
        )
    for i, item in enumerate(inventory["items"], start=1):
        item["qid"] = f"QINV-{i:04d}"
        item["order_index"] = i
    if records:
        inventory = _assign_chapter_wide_inventory_topics_via_api(
            meta=meta,
            inventory=inventory,
            records=records,
            source_topic_excerpts=_group_source_topic_excerpts(sections),
        )
    for item in inventory["items"]:
        if item.get("_topic_scope") == "chapter":
            item["_chapter_wide_task"] = True
        item.pop("_topic_scope", None)
    inventory["stats"] = _inventory_stats(inventory["items"])
    invalid_inventory = _invalid_inventory_items(inventory)
    if invalid_inventory:
        for issue in invalid_inventory[:8]:
            index = int(issue.get("index") or 0)
            item = (
                inventory["items"][index]
                if 0 <= index < len(inventory["items"])
                else {}
            )
            progress.log(
                "Invalid inventory row before checkpoint: "
                f"qid={issue.get('qid') or '<missing>'}; "
                f"reason={issue.get('reason')}; "
                f"label={str(item.get('source_label') or '')[:120]!r}; "
                f"task={_trim(_inventory_task_text(item), 240)!r}.",
                level="error",
            )
        raise RuntimeError(
            "question inventory extraction produced "
            f"{len(invalid_inventory)} invalid source row(s); refusing to "
            "persist an unrecoverable checkpoint"
        )
    if anchors:
        progress.log(
            f"Question / Task Inventory deterministic audit covered "
            f"{len(anchors)} explicit source task anchor(s).")
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


_FIGURE_REFERENCE_RE = re.compile(
    r"\b(?:refer(?:\s+to)?\s+)?fig(?:ure)?s?[.．]?\s*"
    r"(?P<number>\d+(?:[.．]\d+)*"
    r"(?:\s*(?:\([a-z]\)|[a-z]))?)(?!\w|[.．]\d)",
    re.IGNORECASE,
)
_LATEX_FIGURE_BLOCK_RE = re.compile(
    r"\\begin\{figure\}(?P<body>.*?)\\end\{figure\}",
    re.IGNORECASE | re.DOTALL,
)
_LATEX_INCLUDEGRAPHICS_RE = re.compile(
    r"\\includegraphics(?:\[[^\]]*\])?\{(?P<url>https?://[^}\s]+)\}",
    re.IGNORECASE,
)
_MARKDOWN_IMAGE_RE = re.compile(
    r"!\[(?P<alt>[^\]]*)\]\((?P<url>https?://[^)\s]+)\)",
    re.IGNORECASE,
)
_BRACKET_IMAGE_RE = re.compile(
    r'\[img\s+src="(?P<url>https?://[^"]+)"\s+alt="(?P<alt>[^"]*)"[^\]]*\]',
    re.IGNORECASE,
)
_PUBLIC_TASK_SECTION_REF_RE = re.compile(
    r"\bsections?\s+\d+(?:\.\d+)+\b",
    re.IGNORECASE,
)


def _clean_visual_caption(value: str) -> str:
    """Compact a source caption for safe Markdown alt text."""
    value = _inventory_comparison_text(str(value or ""))
    value = re.sub(r"\\captionsetup\{.*?\}", " ", str(value or ""),
                   flags=re.IGNORECASE | re.DOTALL)
    value = value.replace("\\(", "").replace("\\)", "")
    value = re.sub(r"\s+", " ", value).strip(" .")
    return re.sub(r"[\[\]]", "", value)[:300]


def _source_visual_captions(text: str) -> dict[str, str]:
    """Map source image URLs to their adjacent LaTeX/Markdown captions."""
    captions: dict[str, str] = {}
    source = str(text or "")
    for figure in _LATEX_FIGURE_BLOCK_RE.finditer(source):
        body = figure.group("body")
        include = _LATEX_INCLUDEGRAPHICS_RE.search(body)
        if include is None:
            continue
        caption_match = re.search(
            r"\\caption\{(?P<caption>.*)\}\s*$",
            body,
            re.IGNORECASE | re.DOTALL,
        )
        captions[include.group("url")] = _clean_visual_caption(
            caption_match.group("caption") if caption_match else "")
    for image in _MARKDOWN_IMAGE_RE.finditer(source):
        captions.setdefault(
            image.group("url"), _clean_visual_caption(image.group("alt")))
    for image in _BRACKET_IMAGE_RE.finditer(source):
        captions.setdefault(
            image.group("url"), _clean_visual_caption(image.group("alt")))
    for include in _LATEX_INCLUDEGRAPHICS_RE.finditer(source):
        captions.setdefault(include.group("url"), "")
    return captions


def _normalized_figure_id(value: str) -> str:
    """Return a stable figure identifier such as ``11.2`` or ``14(a)``."""
    text = unicodedata.normalize("NFKC", str(value or "")).lower()
    text = re.sub(r"\s+", "", text)
    match = re.search(r"\d+(?:\.\d+)*(?:\([a-z]\)|[a-z])?", text)
    if match is None:
        return ""
    figure_id = match.group(0)
    bare_panel = re.fullmatch(
        r"(?P<base>\d+(?:\.\d+)*)(?P<panel>[a-z])",
        figure_id,
    )
    if bare_panel:
        return (
            f"{bare_panel.group('base')}({bare_panel.group('panel')})"
        )
    return figure_id


def _figure_reference_ids(text: str) -> list[str]:
    """Read explicit figure references in source/question order.

    This is deliberately reference-led rather than adjacency-led: a question
    saying ``Fig. 14(a)`` receives that image even when a different figure is
    physically closer in the MMD.  A trailing ``and (b)`` inherits the same
    base number, covering common map-panel wording.
    """
    source = _inventory_comparison_text(str(text or ""))
    found: list[str] = []
    for match in _FIGURE_REFERENCE_RE.finditer(source):
        figure_id = _normalized_figure_id(match.group("number"))
        if figure_id and figure_id not in found:
            found.append(figure_id)
        base = re.sub(r"\([a-z]\)$", "", figure_id)
        suffix_window = source[match.end():match.end() + 80]
        # Only inherit panels that immediately continue the Figure reference,
        # as in ``Fig. 14(a) and (b)``. Looking anywhere in the following
        # sentence misreads ordinary question subparts such as
        # ``Fig. 11.9. Calculate (a) ..., (b) ...`` as a nonexistent
        # ``Fig. 11.9(b)``.
        panel_continuation = re.match(
            r"(?:\s*(?:,|and)\s*\([a-z]\))+",
            suffix_window,
            re.IGNORECASE,
        )
        for suffix in re.finditer(
            r"\(([a-z])\)",
            panel_continuation.group(0) if panel_continuation else "",
            re.IGNORECASE,
        ):
            panel = f"{base}({suffix.group(1).lower()})" if base else ""
            if panel and panel not in found:
                found.append(panel)
    return found


def _source_figure_layout(sections: list[dict]) -> list[dict]:
    """Return labelled source figures with stable document offsets."""
    figures: list[dict] = []
    offset = 0
    for section_index, section in enumerate(sections):
        body = str(section.get("body") or "")
        for figure in _LATEX_FIGURE_BLOCK_RE.finditer(body):
            figure_body = figure.group("body") or ""
            include = _LATEX_INCLUDEGRAPHICS_RE.search(figure_body)
            if include is None:
                continue
            caption_match = re.search(
                r"\\caption\{(?P<caption>.*)\}\s*$",
                figure_body,
                re.IGNORECASE | re.DOTALL,
            )
            caption = _clean_visual_caption(
                caption_match.group("caption") if caption_match else "")
            for figure_id in _figure_reference_ids(caption):
                figures.append({
                    "figure_id": figure_id,
                    "url": include.group("url"),
                    "caption": caption,
                    "section_index": section_index,
                    "position": figure.start(),
                    "offset": offset + figure.start(),
                })
        for image in _MARKDOWN_IMAGE_RE.finditer(body):
            caption = _clean_visual_caption(image.group("alt"))
            for figure_id in _figure_reference_ids(caption):
                figures.append({
                    "figure_id": figure_id,
                    "url": image.group("url"),
                    "caption": caption,
                    "section_index": section_index,
                    "position": image.start(),
                    "offset": offset + image.start(),
                })
        offset += len(body) + 1
    return figures


def _source_figure_registry(sections: list[dict]) -> dict[str, list[dict]]:
    """Build an exact source figure-id -> URL/caption registry for a chapter."""
    registry: dict[str, list[dict]] = {}
    for figure in _source_figure_layout(sections):
        figure_id = figure["figure_id"]
        entries = registry.setdefault(figure_id, [])
        if not any(entry.get("url") == figure["url"] for entry in entries):
            entries.append(figure)
    return registry


def _source_figure_registry_entries(
    registry: dict[str, list[dict]], figure_id: str,
) -> list[dict]:
    """Resolve an exact panel or one unique combined-panel source figure."""
    exact = registry.get(figure_id) or []
    if exact:
        return exact
    panel = re.fullmatch(r"(?P<base>\d+(?:\.\d+)*)\([a-z]\)", figure_id)
    if panel:
        combined = registry.get(panel.group("base")) or []
        if len(combined) == 1:
            return combined
        panel_letter = figure_id[-2].lower()
        matching_combined = [
            entry for entry in combined
            if re.search(
                rf"(?i)(?:\(\s*{re.escape(panel_letter)}\s*\)|"
                rf"\b{re.escape(panel_letter)}\s*\))",
                str(entry.get("caption") or ""),
            )
        ]
        if len(matching_combined) == 1:
            return matching_combined
    return []


_RENDERED_IMAGE_TAG_RE = re.compile(r"\[img\b[^\]]*\]", re.IGNORECASE)
_RENDERED_EXAMPLE_SEGMENT_RE = re.compile(
    r"(?P<marker>\bExamples?(?:\s+0*\d+)?\s*:\s*)"
    r"(?P<body>.*?)"
    r"(?=(?:\s+(?:Miscellaneous\s+)?Type\s+\d{1,2}:|"
    r"\s+Case\s+\d{1,2}:|"
    r"\s+Examples?(?:\s+0*\d+)?\s*:)|\Z)",
    re.IGNORECASE | re.DOTALL,
)


def _source_figure_tags_for_example(
    example: str, registry: dict[str, list[dict]],
) -> list[str]:
    """Return the source-authoritative image tags for a rendered Example.

    The figure IDs must come from the question itself, never from a preexisting
    image caption.  A stale checkpoint can pair ``Fig. 18`` with a perfectly
    well-formed Fig. 17 tag, and looking at the whole Example would otherwise
    treat Fig. 17 as an additional requested visual.  Strip all rendered tags
    before reading explicit references, then resolve those references solely
    through the current chapter's source registry.
    """
    prompt = _RENDERED_IMAGE_TAG_RE.sub(" ", str(example or ""))
    figure_ids = _figure_reference_ids(prompt)
    if not figure_ids:
        return []

    entries: list[dict] = []
    for figure_id in figure_ids:
        matches = _source_figure_registry_entries(registry, figure_id)
        # An unresolved source reference must remain visible for strict final
        # validation rather than deleting a possibly useful existing tag.
        if not matches:
            return []
        for entry in matches:
            if not any(existing.get("url") == entry.get("url") for existing in entries):
                entries.append(entry)
    tags: list[str] = []
    for image_index, entry in enumerate(entries):
        url = str(entry.get("url") or "").strip()
        # Existing inventory rendering requires public HTTPS image URLs.  Do
        # not turn an old checkpoint into a new runtime error when a malformed
        # source figure cannot meet that wire contract.
        if not url.startswith("https://"):
            return []
        alt = _clean_visual_caption(entry.get("caption") or "")
        if not alt:
            alt = f"Fig. {entry.get('figure_id') or ''}".strip()
        if image_index:
            alt = f"{alt}, visual {image_index + 1}"
        try:
            tags.append(kr.image(url, alt))
        except ValueError:
            return []
    return tags


def _reconcile_explicit_figure_images(
    records: list[dict], sections: list[dict],
) -> tuple[list[dict], int]:
    """Replace stale Type Example images with exact source-registry tags.

    This is intentionally a deterministic final-boundary repair.  It handles
    saved final checkpoints without re-running semantic/API stages, but only
    when every Figure named by an Example resolves in the current source.  A
    question with an unresolved figure is left unchanged so strict validation
    can still stop it rather than silently attaching a nearby image.
    """
    registry = _source_figure_registry(sections)
    if not registry:
        return records, 0

    repaired_examples = 0
    out: list[dict] = []
    for record in records:
        updated = dict(record)
        details = str(updated.get("concept_details") or "")
        split = cr.split_sections(details)
        changed = False
        rebuilt_sections: list[tuple[str, str]] = []
        for label, content in split:
            if not label.strip().lower().startswith("type"):
                rebuilt_sections.append((label, content))
                continue

            def reconcile_example(match: re.Match) -> str:
                nonlocal repaired_examples, changed
                original = match.group("body") or ""
                tags = _source_figure_tags_for_example(original, registry)
                if not tags:
                    return match.group(0)
                # Keep the authored question wording, but discard every
                # preexisting image tag in this Example.  The tags appended
                # below are the exact URLs/captions for the named Figures.
                prompt = _RENDERED_IMAGE_TAG_RE.sub(" ", original)
                prompt = re.sub(r"\s+", " ", prompt).strip()
                reconciled = " ".join(
                    part for part in (prompt, *tags) if part).strip()
                if reconciled == original.strip():
                    return match.group(0)
                changed = True
                repaired_examples += 1
                return match.group("marker") + reconciled

            rebuilt_sections.append((
                label,
                _RENDERED_EXAMPLE_SEGMENT_RE.sub(reconcile_example, content),
            ))
        if changed:
            updated["concept_details"] = cr.join_sections(rebuilt_sections)
        out.append(updated)
    return out, repaired_examples


def _attach_explicit_figure_images(
    items: list[dict], sections: list[dict],
) -> list[dict]:
    """Attach explicit figures, with a tightly bounded visual-task fallback."""
    layout = _source_figure_layout(sections)
    registry = _source_figure_registry(sections)
    section_offsets: dict[int, int] = {}
    offset = 0
    for index, section in enumerate(sections):
        section_offsets[index] = offset
        offset += len(str(section.get("body") or "")) + 1
    implicit_visual_cue = re.compile(
        r"\b(?:caricatur(?:e|ist)|artist|print|portrait|diagram|"
        r"figure|image|illustration|painting|portray(?:ed|al)?|"
        r"look\s+at|shown)\b",
        re.IGNORECASE,
    )
    missing_visual_cue = re.compile(
        r"\b(?:following|given|provided|above|below)\s+"
        r"(?:diagram|figure|image|illustration)\b|"
        r"\b(?:diagram|figure|image|illustration)\s+"
        r"(?:shown|given|provided|above|below)\b",
        re.IGNORECASE,
    )

    def visual_term_key(word: str) -> str:
        """Collapse only strong visual-word variants for caption matching."""
        value = str(word or "").lower()
        for prefix in (
            "caricatur", "illustrat", "portray", "paint", "portrait",
            "diagram", "symbol",
        ):
            if value.startswith(prefix):
                return prefix
        return value

    def meaningful_visual_terms(text: str) -> set[str]:
        return {
            visual_term_key(word) for word in re.findall(
                r"[A-Za-z]{4,}",
                _strip_source_visual_markup(str(text or "")),
            )
        } - {
            "with", "from", "that", "this", "into", "what", "which",
            "when", "where", "have", "were", "they", "their", "about",
            "help", "look", "figure", "image",
        }

    def embedded_task_visuals(task: str) -> list[dict]:
        """Return visuals structurally inside this task, excluding table art."""
        source = str(task or "")
        captions = _source_visual_captions(source)
        entries: list[dict] = []

        def add(url: str, caption: str = "") -> None:
            url = str(url or "").strip()
            if not url or any(entry["url"] == url for entry in entries):
                return
            entries.append({
                "url": url,
                "caption": _clean_visual_caption(
                    caption or captions.get(url) or ""),
            })

        for figure in _LATEX_FIGURE_BLOCK_RE.finditer(source):
            include = _LATEX_INCLUDEGRAPHICS_RE.search(
                figure.group("body") or "")
            if include is not None:
                add(include.group("url"))

        # Completed table cells can carry decorative disease thumbnails even
        # though their labels already provide all of the requested context.
        # Keep standalone source images, but do not append table art as a set of
        # unlabeled question visuals.
        without_tables = re.sub(
            r"\\begin\{table\}.*?\\end\{table\}",
            " ",
            source,
            flags=re.IGNORECASE | re.DOTALL,
        )
        without_tables = re.sub(
            r"\\begin\{tabular\}.*?\\end\{tabular\}",
            " ",
            without_tables,
            flags=re.IGNORECASE | re.DOTALL,
        )
        without_tables = _LATEX_FIGURE_BLOCK_RE.sub(" ", without_tables)
        for image in _MARKDOWN_IMAGE_RE.finditer(without_tables):
            add(image.group("url"), image.group("alt"))
        for image in _BRACKET_IMAGE_RE.finditer(without_tables):
            add(image.group("url"), image.group("alt"))
        return entries

    for item in items:
        raw_task = str(
            item.get("raw_task") or item.get("normalized_task") or "")
        textual_task = _strip_source_visual_markup(raw_task)
        implicit_task = re.sub(
            r"\\begin\{(?:table|tabular)\}.*?"
            r"\\end\{(?:table|tabular)\}",
            " ",
            textual_task,
            flags=re.IGNORECASE | re.DOTALL,
        )
        embedded = embedded_task_visuals(raw_task)
        source_embedded_urls = {
            str(url)
            for url in (item.get("image_urls") or [])
            if str(url or "") and str(url) in raw_task
        }
        task_terms = meaningful_visual_terms(textual_task)
        owned_embedded = [
            entry for entry in embedded
            if (
                entry.get("caption")
                and task_terms
                & meaningful_visual_terms(entry.get("caption") or "")
            )
        ]
        # Captions/adjacent figure blocks must not themselves decide which
        # visual belongs to a question. Inspect the textual task after they are
        # stripped, then use the document-wide registry for resolution.
        refs = _figure_reference_ids(textual_task)
        if not refs:
            refs = _figure_reference_ids(str(item.get("source_label") or ""))
        resolved: list[dict] = []
        if refs:
            for figure_id in refs:
                for entry in _source_figure_registry_entries(
                    registry, figure_id
                ):
                    if not any(existing["url"] == entry["url"] for existing in resolved):
                        resolved.append(entry)
            # Mathpix sometimes emits the task-owned image without a caption,
            # so it cannot enter the figure registry. A single image embedded
            # inside the task is the only safe fallback for an unresolved
            # explicit Figure reference.
            if not resolved and len(embedded) == 1:
                resolved.extend(embedded)
        elif (
            embedded
            and str(item.get("source_kind") or "").strip().lower()
            == "activity"
            and item.get("_activity_origin") is False
        ):
            # Named source tasks such as "Think like a scientist" can be
            # multi-panel activity infographics even though they do not carry
            # an Activity number or prose reference to each panel. Their
            # trimmed source boundary, rather than page proximity, owns the
            # images.
            resolved.extend(embedded)
        elif (
            embedded
            and str(item.get("source_kind") or "").strip().lower()
            == "exercise"
        ):
            # A figure physically inside one numbered exercise boundary belongs
            # to that atomic question even when its stem says only "the setup".
            resolved.extend(embedded)
        elif owned_embedded:
            resolved.extend(owned_embedded)
        elif implicit_visual_cue.search(implicit_task):
            try:
                section_index = int(item.get("_source_section_index"))
                position = int(item.get("_source_position") or 0)
            except (TypeError, ValueError):
                section_index = -1
                position = 0
            target = section_offsets.get(section_index, -1) + position
            if target >= 0:
                nearby = [
                    figure for figure in layout
                    if (
                        abs(
                            int(figure.get("section_index", -1))
                            - section_index
                        ) <= 2
                        and abs(
                            int(figure.get("offset") or 0) - target
                        ) <= 4_000
                    )
                ]
                if nearby:
                    def _visual_score(figure: dict) -> tuple[int, int]:
                        caption_terms = meaningful_visual_terms(
                            figure.get("caption") or "")
                        overlap = len(task_terms & caption_terms)
                        distance = abs(
                            int(figure.get("offset") or 0) - target)
                        return overlap, -distance

                    nearest = max(nearby, key=_visual_score)
                    # Proximity alone is not ownership evidence: opening art,
                    # QR codes, and decorative illustrations can be closer than
                    # the visual a prompt describes. An unnumbered visual task
                    # in the same source section must share a meaningful term
                    # with the caption. Across a heading boundary, require
                    # stronger entity-level agreement; this retains a
                    # Bismarck/caricature/Parliament plate but does not borrow
                    # a prior bacterial-cell figure for a missing exercise
                    # diagram.
                    nearest_overlap = _visual_score(nearest)[0]
                    same_section = (
                        int(nearest.get("section_index", -1))
                        == section_index
                    )
                    if (
                        (same_section and nearest_overlap > 0)
                        or (not same_section and nearest_overlap >= 3)
                    ):
                        resolved.append(nearest)
        if (
            not resolved
            and source_embedded_urls
            and embedded
            and not any(entry.get("caption") for entry in embedded)
        ):
            # Images emitted inline as part of the task remain authoritative
            # when Mathpix supplied no caption to match against and no
            # semantically stronger registry figure was found. This covers
            # multi-stage scientific-method panels while letting a named
            # painting resolve to its captioned source plate.
            resolved.extend(
                entry for entry in embedded
                if entry["url"] in source_embedded_urls
            )
        if not resolved:
            if (
                refs
                or implicit_visual_cue.search(implicit_task)
                or source_embedded_urls
                or item.get("image_urls")
                or embedded
            ):
                # Prevent a caption/URL physically adjacent to the OCR block
                # from being emitted as a substitute for an unresolved Figure.
                # The visible source reference remains, allowing strict final
                # validation to stop the job rather than ship a wrong image.
                item["image_urls"] = []
                item["_image_captions"] = {}
                item["_figure_images_resolved"] = True
                item["requires_visual"] = bool(
                    refs or missing_visual_cue.search(implicit_task))
            continue
        item["image_urls"] = [entry["url"] for entry in resolved]
        item["_image_captions"] = {
            entry["url"]: entry.get("caption") or "" for entry in resolved
        }
        item["_figure_images_resolved"] = True
        item["requires_visual"] = True
    visual_items_by_section: dict[int, list[dict]] = {}
    for item in items:
        if not item.get("image_urls"):
            continue
        try:
            section_index = int(item.get("_source_section_index"))
            position = int(item.get("_source_position") or 0)
        except (TypeError, ValueError):
            continue
        visual_items_by_section.setdefault(section_index, []).append({
            "position": position,
            "image_urls": list(item.get("image_urls") or []),
            "captions": dict(item.get("_image_captions") or {}),
        })
    for candidates in visual_items_by_section.values():
        candidates.sort(key=lambda candidate: candidate["position"])
    for item in items:
        if item.get("image_urls") or not re.search(
            r"\btable\s+above\b",
            str(item.get("raw_task") or item.get("normalized_task") or ""),
            re.IGNORECASE,
        ):
            continue
        try:
            section_index = int(item.get("_source_section_index"))
            position = int(item.get("_source_position") or 0)
        except (TypeError, ValueError):
            continue
        earlier = [
            candidate
            for candidate in visual_items_by_section.get(section_index, [])
            if candidate["position"] < position
        ]
        if not earlier:
            continue
        nearest = earlier[-1]
        if position - nearest["position"] > 4_000:
            continue
        item["image_urls"] = list(nearest["image_urls"])
        item["_image_captions"] = dict(nearest["captions"])
        item["_figure_images_resolved"] = True
        item["requires_visual"] = True
    return items


def _strip_source_visual_markup(text: str) -> str:
    """Remove source visual wrappers; public Markdown is appended uniformly."""
    value = _LATEX_FIGURE_BLOCK_RE.sub(" ", str(text or ""))
    value = _LATEX_INCLUDEGRAPHICS_RE.sub(" ", value)
    value = _MARKDOWN_IMAGE_RE.sub(" ", value)
    value = _BRACKET_IMAGE_RE.sub(" ", value)
    return value


def _visual_alt_text(
    item: dict, task: str, image_index: int = 0, source_caption: str = "",
) -> str:
    """Return a visible, source-grounded caption for a shipped image."""
    caption = _clean_visual_caption(source_caption)
    match = _FIGURE_REFERENCE_RE.search(caption or task or "")
    if caption:
        base = caption
    elif match:
        base = f"Fig. {match.group('number')}"
    else:
        label = concept_cleanup.strip_dangling_references(
            str(item.get("source_label") or "")).strip(" .:-")
        base = label if label and len(label) <= 80 else "Source visual"
    if image_index:
        base = f"{base}, visual {image_index + 1}"
    return re.sub(r"[\[\]]", "", base)


def _public_task_without_latex_layout(text: str) -> str:
    """Convert source-only list/table scaffolding into readable prompt text."""
    value = str(text or "")

    def item_marker(match: re.Match) -> str:
        label = str(match.group("label") or "").strip()
        if label in {"", "-", "*", "•"}:
            return " "
        return f" {label} "

    value = re.sub(
        r"\\item(?:\[(?P<label>[^\]]*)\])?",
        item_marker,
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(
        r"\\(?:begin|end)\{(?:itemize|enumerate)\}",
        " ",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(
        r"\\captionsetup\{[^{}]*\}", " ", value,
        flags=re.IGNORECASE,
    )
    value = re.sub(
        r"\\caption\{(?P<caption>[^{}]*)\}",
        lambda match: f" {match.group('caption').strip()}. ",
        value,
        flags=re.IGNORECASE,
    )

    def plain_tabular(match: re.Match) -> str:
        block = match.group(0)
        block = re.sub(
            r"^\\begin\{tabular\}\{[^\n]*?\}",
            "",
            block,
            count=1,
            flags=re.IGNORECASE,
        )
        block = re.sub(
            r"\\end\{tabular\}$", "", block,
            count=1,
            flags=re.IGNORECASE,
        )
        block = re.sub(
            r"\\multirow\{[^{}]*\}\{[^{}]*\}\{(?P<body>[^{}]*)\}",
            lambda nested: nested.group("body"),
            block,
            flags=re.IGNORECASE,
        )
        block = re.sub(
            r"\\multicolumn\{[^{}]*\}\{[^{}]*\}\{(?P<body>[^{}]*)\}",
            lambda nested: nested.group("body"),
            block,
            flags=re.IGNORECASE,
        )
        block = re.sub(r"\\hline\b", " ", block, flags=re.IGNORECASE)
        rows: list[str] = []
        for row in re.split(r"\\\\", block):
            cells = [
                re.sub(r"\s+", " ", cell).strip(" {}")
                for cell in row.split("&")
            ]
            cells = [cell for cell in cells if cell]
            if cells:
                rows.append(" | ".join(cells))
        return ("; ".join(rows) + ". ") if rows else " "

    value = re.sub(
        r"\\begin\{tabular\}.*?\\end\{tabular\}",
        plain_tabular,
        value,
        flags=re.IGNORECASE | re.DOTALL,
    )
    value = re.sub(
        r"\\(?:begin|end)\{table\}", " ", value,
        flags=re.IGNORECASE,
    )
    return re.sub(r"\s+", " ", value).strip()


def _inventory_task_text(item: dict) -> str:
    """Full source task text used for public example prompts.

    ``raw_task`` is preferred over ``normalized_task`` — reviewers require the
    complete untruncated source wording, and normalization tends to compress.
    Source image URLs captured for the item are appended in canonical ``[img]``
    tags so figure-dependent questions ship their visuals.
    """
    task = (
        item.get("raw_task")
        or item.get("normalized_task")
        or item.get("question")
        or ""
    )
    task = _inventory_comparison_text(str(task))
    stored_captions = item.get("_image_captions")
    visual_captions = {
        str(url): _clean_visual_caption(caption)
        for url, caption in (stored_captions or {}).items()
        if str(url or "").strip()
    } if isinstance(stored_captions, dict) else {}
    # Local source markup can contribute a caption too, but a document-level
    # exact-reference caption is authoritative when both are present. Once the
    # figure resolver ran, do not re-add visual wrappers adjacent to a task.
    if not item.get("_figure_images_resolved"):
        for url, caption in _source_visual_captions(str(task)).items():
            visual_captions.setdefault(url, caption)
    source_kind = str(item.get("source_kind") or "").strip().lower()
    task = _strip_public_source_heading(str(task))
    task = _inventory_task_without_solution(
        str(task),
        aggressive=source_kind in {"worked_example", "solved_example"},
    )
    task = _strip_leading_source_task_label(task)
    task = _strip_source_visual_markup(task)
    task = _public_task_without_latex_layout(task)
    task = kr.canonicalize_rich_text(str(task)).strip()
    task = _PUBLIC_TASK_SECTION_REF_RE.sub("the earlier chapter discussion", task)
    context = kr.canonicalize_rich_text(
        str(item.get("shared_context") or "")).strip()
    if context and item.get("requires_context") and context not in task:
        task = f"{context} {task}".strip()
    task = re.sub(r"\s+", " ", task)
    image_urls = list(item.get("image_urls") or [])
    for url in visual_captions:
        if url not in image_urls:
            image_urls.append(url)
    for image_index, url in enumerate(image_urls):
        url = str(url or "").strip()
        if not url:
            continue
        alt = _visual_alt_text(
            item, task, image_index, visual_captions.get(url, ""))
        if url not in task:
            task = f"{task} {kr.image(url, alt)}"
    return kr.canonicalize_rich_text(task.strip())


def _case_prompt_needs_source(prompt: str, source_text: str) -> bool:
    if not source_text:
        return False
    # A qid is an authoritative identity link, not a fuzzy text hint. Restore
    # every model-authored variation from the inventory so same-length
    # paraphrases, omitted context, and omitted visual URLs cannot survive into
    # rendered Types and fail the exact-coverage boundary later.
    return (
        bi.normalize_question_text(prompt)
        != bi.normalize_question_text(source_text)
    )


_CASE_TITLE_QUESTION_RE = re.compile(
    r"^(?:solve|simplify|find|write|identify|expand|compare|calculate|"
    r"rationalise|express|evaluate|convert|draw|label|explain|prove|"
    r"describe|discuss|analyse|analyze|examine|interpret|outline|assess|"
    r"state|list|mention|account|justify|trace|distinguish|define|"
    r"what|why|how|who|when|where|which)\b",
    re.IGNORECASE,
)


def _case_title_needs_definition(case_title: str, examples: list[dict]) -> bool:
    """Whether legacy Case text is a question instead of a sub-type definition."""
    title = (case_title or "").strip()
    first_example = (
        str(examples[0].get("example_prompt") or "").strip()
        if examples else ""
    )
    return bool(
        not title
        or title.endswith("?")
        or _CASE_TITLE_QUESTION_RE.match(title)
        or (
            first_example
            and bi.normalize_question_text(title)
            == bi.normalize_question_text(first_example)
        )
    )


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
                    if qid not in source_ids:
                        source_ids.append(qid)
                    source_text = _inventory_task_text(by_qid.get(qid, {}))
                    if (
                        source_text
                        and _case_prompt_needs_source(legacy, source_text)
                    ):
                        case["case_prompt"] = source_text
                        legacy = source_text
                seen_prompts.add(bi.normalize_question_text(legacy))
            for ex in case.get("examples") or []:
                if not isinstance(ex, dict):
                    continue
                qid = (ex.get("source_question_id") or "").strip()
                if qid:
                    example_by_qid[qid] = ex
                    if qid not in source_ids:
                        source_ids.append(qid)
                    source_text = _inventory_task_text(by_qid.get(qid, {}))
                    if (
                        source_text
                        and _case_prompt_needs_source(
                            ex.get("example_prompt", ""), source_text)
                    ):
                        ex["example_prompt"] = source_text
                if ex.get("example_prompt"):
                    seen_prompts.add(bi.normalize_question_text(ex["example_prompt"]))
        # Legacy mining emitted one raw ``case_prompt`` per Case. Preserve its
        # source task as an Example, then derive a reusable Case definition
        # from that task so question wording never occupies the Case line.
        for case in cases:
            legacy = (case.get("case_prompt") or "").strip()
            if legacy:
                legacy_qid = (case.get("source_question_id") or "").strip()
                example = {
                    "source_question_id": legacy_qid,
                    "example_prompt": legacy,
                }
                case["examples"] = [example, *(case.get("examples") or [])]
                case.pop("case_prompt", None)
                case.pop("source_question_id", None)
                if legacy_qid:
                    example_by_qid[legacy_qid] = example
            examples = _case_examples(case)
            if _case_title_needs_definition(
                str(case.get("case_title") or ""), examples,
            ):
                representative_qid = (
                    str(examples[0].get("source_question_id") or "").strip()
                    if examples else ""
                )
                representative = (
                    str(examples[0].get("example_prompt") or "").strip()
                    if examples else ""
                )
                if representative:
                    _, case_title = _semantic_fallback_wording(
                        by_qid.get(representative_qid, {}), representative)
                    case["case_title"] = case_title
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
            _, case_title = _semantic_fallback_wording(
                by_qid.get(qid, {}), source_text)
            cases.append({
                "case_id": f"CASE-{len(cases) + 1:04d}",
                "case_title": case_title,
                "examples": [example],
                "case_signature": "",
            })
            seen_prompts.add(key)
        mtype["source_question_ids"] = source_ids
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
            # A topic-local fragment may still be mixed, but it no longer has
            # evidence for placement on a later topic's Culmination.
            if (
                (item.get("placement_scope") or "").strip().lower()
                == "cross_topic_synthesis"
            ):
                item["placement_scope"] = "mixed_synthesis"
            item["source_question_ids"] = qids
            item["topic_match_hint"] = topic
            filtered_cases: list[dict] = []
            for raw_case in original.get("case_prompts") or []:
                if not isinstance(raw_case, dict):
                    continue
                case = dict(raw_case)
                if (
                    (case.get("placement_scope") or "").strip().lower()
                    == "cross_topic_synthesis"
                ):
                    case["placement_scope"] = "mixed_synthesis"
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


def _split_mined_types_by_inventory_role(
    types: list[dict], inventory: dict,
) -> list[dict]:
    """Make Activity ownership follow the source inventory, case by case.

    A model can group an ordinary question and a textbook experiment under one
    reusable Type, then mark the whole Type as an Activity. Type assignment
    consequently skips the ordinary question while Hub placement never sees
    it. Split that mixed family at Case/qid boundaries and derive
    ``is_activity`` from the authoritative inventory role instead.
    """
    hub_qids = {
        str(item.get("qid") or "").strip()
        for item in _hub_inventory_items(inventory)
        if str(item.get("qid") or "").strip()
    }
    split: list[dict] = []
    split_count = 0
    corrected_count = 0
    for original in types:
        if not isinstance(original, dict):
            continue
        source_qids = _type_source_qids(original)
        role_groups = [
            (
                False,
                [qid for qid in source_qids if qid not in hub_qids],
            ),
            (
                True,
                [qid for qid in source_qids if qid in hub_qids],
            ),
        ]
        role_groups = [
            (is_activity, qids)
            for is_activity, qids in role_groups
            if qids
        ]
        if len(role_groups) > 1:
            split_count += 1
        for is_activity, qids in role_groups:
            qid_set = set(qids)
            item = copy.deepcopy(original)
            if bool(item.get("is_activity")) != is_activity:
                corrected_count += 1
            item["is_activity"] = is_activity
            item["source_question_ids"] = qids
            filtered_cases: list[dict] = []
            for raw_case in original.get("case_prompts") or []:
                if not isinstance(raw_case, dict):
                    continue
                case = copy.deepcopy(raw_case)
                examples = [
                    copy.deepcopy(example)
                    for example in _case_examples(raw_case)
                    if (
                        str(
                            example.get("source_question_id") or ""
                        ).strip()
                        in qid_set
                    )
                ]
                legacy_qid = str(
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

    for index, item in enumerate(split, start=1):
        item["type_id"] = f"TYPE-{index:04d}"
    if split_count or corrected_count:
        progress.log(
            "Reconciled mined Type Activity roles with the source inventory: "
            f"split {split_count} mixed Type(s), corrected "
            f"{corrected_count} role fragment(s).",
            level="warning",
        )
    return _backfill_type_cases_from_inventory(split, inventory)


def _prune_unowned_mined_examples(
    types: list[dict], inventory: dict,
) -> list[dict]:
    """Remove model-authored Examples that have no exact inventory owner.

    The inventory is a closed world: public Examples may only be materialized
    from a known qid, and their wording is restored later from that inventory.
    Filtering here prevents an invented, missing-qid, or unknown-qid Example
    from surviving every exact *known-qid* coverage check and failing only at
    the terminal unowned-Example gate.
    """
    allowed_qids = {
        str(item.get("qid") or "").strip()
        for item in inventory.get("items") or []
        if isinstance(item, dict) and str(item.get("qid") or "").strip()
    }
    if not allowed_qids:
        return []

    pruned: list[dict] = []
    removed_examples = 0
    removed_types = 0
    for raw_type in types:
        if not isinstance(raw_type, dict):
            continue
        mtype = copy.deepcopy(raw_type)
        source_ids: list[str] = []
        for raw_qid in mtype.get("source_question_ids") or []:
            qid = str(raw_qid or "").strip()
            if qid in allowed_qids and qid not in source_ids:
                source_ids.append(qid)

        cases: list[dict] = []
        for raw_case in mtype.get("case_prompts") or []:
            if isinstance(raw_case, str):
                # A free-form legacy Case has no auditable qid and therefore
                # cannot own a public Example in the closed inventory.
                removed_examples += 1
                continue
            if not isinstance(raw_case, dict):
                continue
            case = copy.deepcopy(raw_case)
            legacy_qid = str(
                case.get("source_question_id") or "").strip()
            legacy_owned = legacy_qid in allowed_qids
            if legacy_owned and legacy_qid not in source_ids:
                source_ids.append(legacy_qid)

            examples: list[dict] = []
            for raw_example in case.get("examples") or []:
                if not isinstance(raw_example, dict):
                    removed_examples += 1
                    continue
                qid = str(
                    raw_example.get("source_question_id") or "").strip()
                if qid not in allowed_qids:
                    removed_examples += 1
                    continue
                examples.append(copy.deepcopy(raw_example))
                if qid not in source_ids:
                    source_ids.append(qid)
            if examples:
                case["examples"] = examples
                cases.append(case)
            elif legacy_owned:
                case.pop("examples", None)
                cases.append(case)
            elif (
                case.get("examples")
                or case.get("case_prompt")
                or case.get("source_question_id")
            ):
                removed_examples += 1

        if not source_ids:
            removed_types += 1
            continue
        mtype["source_question_ids"] = source_ids
        mtype["case_prompts"] = cases
        pruned.append(mtype)

    if removed_examples or removed_types:
        progress.log(
            "Pruned unowned mined output before Type rendering: "
            f"{removed_examples} Example/Case payload(s), "
            f"{removed_types} ownerless Type(s).",
            level="warning",
        )
    return pruned


def _normalize_mined_type_candidate(
    raw_types: list, inventory: dict,
) -> list[dict]:
    """Repair model schema drift, then apply deterministic Type normalization."""
    types = _repair_nested_mined_types(raw_types)
    types = _prune_unowned_mined_examples(types, inventory)
    types = _backfill_type_cases_from_inventory(types, inventory)
    types = _split_mined_types_by_source_topic(types, inventory)
    types = _split_mined_types_by_inventory_role(types, inventory)
    types = _merge_equivalent_mined_types(types)
    return _clear_nonvisual_diagram_interpretation_hints(types, inventory)


def _clear_nonvisual_diagram_interpretation_hints(
    types: list[dict], inventory: dict,
) -> list[dict]:
    """Drop a false optional visual-skill hint using authoritative inventory."""
    inventory_by_qid = {
        str(item.get("qid") or "").strip(): item
        for item in (inventory or {}).get("items") or []
        if isinstance(item, dict) and str(item.get("qid") or "").strip()
    }
    corrected = 0
    for mtype in types:
        if not isinstance(mtype, dict):
            continue
        if (
            bi.normalize_question_text(
                mtype.get("subject_skill_hint") or "")
            != "diagram interpretation"
        ):
            continue
        owned_items = [
            inventory_by_qid[qid]
            for qid in _type_source_qids(mtype)
            if qid in inventory_by_qid
        ]
        if owned_items and not any(
            item.get("requires_visual") is True for item in owned_items
        ):
            mtype["subject_skill_hint"] = ""
            corrected += 1
    if corrected:
        progress.log(
            "Cleared Diagram Interpretation from "
            f"{corrected} nonvisual Type(s) using the source inventory.",
            level="success",
        )
    return types


def _merge_equivalent_mined_types(types: list[dict]) -> list[dict]:
    """Merge repeated Type definitions into Cases without semantic guessing."""
    merged: list[dict] = []
    index_by_key: dict[tuple, int] = {}
    merge_count = 0
    for raw_type in types:
        if not isinstance(raw_type, dict):
            continue
        mtype = copy.deepcopy(raw_type)
        title = mtype.get("type_title") or mtype.get("task_pattern") or ""
        key = (
            _topic_comparison_key(mtype.get("topic_match_hint") or ""),
            bi.normalize_question_text(title),
            bi.normalize_question_text(mtype.get("type_description") or ""),
            bi.normalize_question_text(mtype.get("task_pattern") or ""),
            bi.normalize_question_text(mtype.get("concept_match_hint") or ""),
            bi.normalize_question_text(
                mtype.get("parent_concept_match_hint") or ""),
            bool(mtype.get("is_activity")),
            (mtype.get("placement_scope") or "").strip().lower(),
        )
        # Empty/generic metadata is not enough evidence that two patterns are
        # equivalent; preserve those Types for semantic review.
        if not key[1] or not key[4]:
            merged.append(mtype)
            continue
        existing_index = index_by_key.get(key)
        if existing_index is None:
            index_by_key[key] = len(merged)
            merged.append(mtype)
            continue
        target = merged[existing_index]
        source_ids = target.setdefault("source_question_ids", [])
        for qid in mtype.get("source_question_ids") or []:
            if qid not in source_ids:
                source_ids.append(qid)
        target_cases = target.setdefault("case_prompts", [])
        case_by_key = {
            (
                bi.normalize_question_text(case.get("case_title") or ""),
                bi.normalize_question_text(case.get("case_signature") or ""),
                (case.get("placement_scope") or "").strip().lower(),
            ): case
            for case in target_cases
            if isinstance(case, dict)
        }
        for raw_case in mtype.get("case_prompts") or []:
            if not isinstance(raw_case, dict):
                target_cases.append(copy.deepcopy(raw_case))
                continue
            case = copy.deepcopy(raw_case)
            case_key = (
                bi.normalize_question_text(case.get("case_title") or ""),
                bi.normalize_question_text(case.get("case_signature") or ""),
                (case.get("placement_scope") or "").strip().lower(),
            )
            existing_case = case_by_key.get(case_key)
            if existing_case is None or not case_key[0]:
                target_cases.append(case)
                if case_key[0]:
                    case_by_key[case_key] = case
                continue
            examples = existing_case.setdefault("examples", [])
            seen_qids = {
                (example.get("source_question_id") or "").strip()
                for example in examples if isinstance(example, dict)
            }
            for example in case.get("examples") or []:
                qid = (
                    (example.get("source_question_id") or "").strip()
                    if isinstance(example, dict) else ""
                )
                if qid and qid in seen_qids:
                    continue
                examples.append(example)
                if qid:
                    seen_qids.add(qid)
        merge_count += 1
    if merge_count:
        progress.log(
            f"Grouped {merge_count} repeated Type definition(s) into "
            "multi-Case reusable Types.",
            level="success",
        )
    return merged


def _compact_mined_type_metadata(types: list[dict]) -> dict:
    """Small, assignment-free Type context for focused coverage calls."""
    type_fields = (
        "type_id", "type_title", "type_description", "task_pattern",
        "concept_match_hint", "parent_concept_match_hint", "topic_match_hint",
        "difficulty_hint", "cognitive_skill_hint", "subject_skill_hint",
        "is_activity", "placement_scope",
    )
    case_fields = (
        "case_id", "case_title", "case_signature", "placement_scope")
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
    """Validate an additive delta and restore its source-owned Example text."""
    import copy

    if not isinstance(data, dict) or not isinstance(data.get("types"), list):
        raise ValueError("response must contain a types list")
    raw_types = copy.deepcopy(data["types"])
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
                "subject_skill_hint", "is_activity", "placement_scope",
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
                expected = _inventory_task_text(allowed_by_qid[qid])
                if not expected:
                    errors.append(
                        f"{example_label} has no source task to restore")
                    continue
                # Prompt wording, shared context, and image URLs belong to the
                # inventory. The model owns only the Type/Case classification.
                example["example_prompt"] = expected

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
    return raw_types


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
            data = _openai_json(
                system, user, purpose="concept_validation")
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


_FALLBACK_ACTION_GERUNDS = {
    "analyse": "Analysing", "analyze": "Analysing",
    "calculate": "Calculating", "classify": "Classifying",
    "compare": "Comparing", "complete": "Completing",
    "construct": "Constructing", "convert": "Converting",
    "derive": "Deriving", "describe": "Describing",
    "determine": "Determining", "discuss": "Discussing",
    "distinguish": "Distinguishing", "draw": "Drawing",
    "evaluate": "Evaluating", "examine": "Examining",
    "explain": "Explaining", "express": "Expressing",
    "find": "Finding", "identify": "Identifying",
    "interpret": "Interpreting", "justify": "Justifying",
    "list": "Listing", "prove": "Proving", "show": "Showing",
    "simplify": "Simplifying", "solve": "Solving",
    "state": "Stating", "trace": "Tracing", "write": "Writing",
}


def _semantic_fallback_wording(item: dict, source_task: str) -> tuple[str, str]:
    """Derive an action/object Type name instead of a source-label fallback."""
    plain = _strip_public_source_heading(bi.to_plain_text(source_task))
    tokens = re.findall(r"[A-Za-z][A-Za-z'-]*|\d+(?:\.\d+)*", plain)
    lowered = [token.lower() for token in tokens]
    action_index = next(
        (
            index for index, token in enumerate(lowered)
            if token in _FALLBACK_ACTION_GERUNDS
        ),
        None,
    )
    if action_index is not None:
        gerund = _FALLBACK_ACTION_GERUNDS[lowered[action_index]]
        object_tokens = tokens[action_index + 1:action_index + 10]
    elif lowered and lowered[0] in {"what", "which", "who", "where", "when"}:
        gerund = "Identifying"
        object_tokens = tokens[1:10]
    elif lowered and lowered[0] in {"why", "how"}:
        gerund = "Explaining"
        object_tokens = tokens[:9]
    else:
        gerund = "Completing"
        object_tokens = tokens[:8]
    while object_tokens and object_tokens[0].lower() in {
        "a", "an", "the", "this", "that", "following",
    }:
        object_tokens.pop(0)
    obj = " ".join(object_tokens).strip()
    if not obj:
        obj = str(item.get("subject_skill_hint") or "Source-defined Task")
    title = concept_cleanup.to_title_case(f"{gerund} {obj}".strip())
    case = (
        f"Given the complete source context, "
        f"{gerund.lower()} {obj.lower()} under its stated conditions"
    )
    return title, case


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
    if source_kind in {
        "worked_example", "solved_example", "exercise", "intext_question",
        "checkpoint_question", "long_answer", "short_answer", "other",
    }:
        title, case_title = _semantic_fallback_wording(cleaned, source_task)
    else:
        title, case_title = _FALLBACK_TYPE_WORDING.get(
            source_kind, _semantic_fallback_wording(cleaned, source_task))
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
            "placement_scope": "normal",
        }],
        "concept_match_hint": "",
        "parent_concept_match_hint": "",
        "topic_match_hint": topic,
        "difficulty_hint": "",
        "cognitive_skill_hint": "",
        "subject_skill_hint": "",
        "is_activity": source_kind in _HUB_INVENTORY_KINDS,
        "placement_scope": "normal",
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
    data = _openai_json(system, user, purpose="concept_mapping")
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
        corrected = _openai_json(
            system, follow_up, purpose="concept_validation")
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


def _type_qid_contracts(types: list[dict]) -> dict[str, tuple[str, bool, str]]:
    """Authoritative topic/activity/scope contract for each mined qid."""
    contracts: dict[str, tuple[str, bool, str]] = {}
    for mtype in types:
        if not isinstance(mtype, dict):
            continue
        topic = _topic_comparison_key(mtype.get("topic_match_hint") or "")
        activity = bool(mtype.get("is_activity"))
        type_scope = _assignment_placement_scope(mtype)
        case_qids: set[str] = set()
        for raw_case in mtype.get("case_prompts") or []:
            if not isinstance(raw_case, dict):
                continue
            scope = (
                (raw_case.get("placement_scope") or "").strip().lower()
                or type_scope
            )
            if scope not in _ASSIGNMENT_PLACEMENT_SCOPES:
                scope = type_scope
            for qid in _assignment_case_qids(raw_case):
                contracts[qid] = (topic, activity, scope)
                case_qids.add(qid)
        for qid in _type_source_qids(mtype):
            contracts.setdefault(qid, (topic, activity, type_scope))
    return contracts


def _consolidate_semantic_types_via_api(
    mined_types: dict, *, inventory: dict, meta: dict,
) -> dict:
    """Merge paraphrased Type definitions behind exact deterministic gates."""
    import json as _json

    original = copy.deepcopy((mined_types or {}).get("types") or [])
    if len(original) < 2:
        return {"types": original}
    system = prompts.get_text("concepts.type_semantic_consolidation.system")
    user = (
        _metadata_block(meta)
        + "\nMINED TYPES TO CONSOLIDATE:\n"
        + _json.dumps({"types": original}, ensure_ascii=False)
    )
    progress.log(
        f"Reviewing {len(original)} mined Types for semantic duplicates.")
    try:
        data = _openai_json(system, user, purpose="concept_mapping")
    except Exception as exc:  # noqa: BLE001 — exact mined set remains valid
        progress.log(
            f"Semantic Type consolidation failed ({exc}); keeping mined Types.",
            level="warning",
        )
        return {"types": original}

    candidate = _normalize_mined_type_candidate(
        list((data or {}).get("types") or []), inventory)
    for index, mtype in enumerate(candidate, start=1):
        mtype["type_id"] = f"TYPE-{index:04d}"

    missed = _uncovered_inventory_items(inventory, candidate)
    duplicates = _duplicate_inventory_assignments(inventory, candidate)
    original_contracts = _type_qid_contracts(original)
    candidate_contracts = _type_qid_contracts(candidate)
    contract_drift = {
        qid: {
            "expected": original_contracts.get(qid),
            "candidate": candidate_contracts.get(qid),
        }
        for qid in set(original_contracts) | set(candidate_contracts)
        if original_contracts.get(qid) != candidate_contracts.get(qid)
    }
    if (
        not candidate
        or len(candidate) > len(original)
        or missed
        or duplicates
        or contract_drift
    ):
        progress.log(
            "Rejected semantic Type consolidation: "
            f"{len(missed)} missing, {len(duplicates)} duplicate, "
            f"{len(contract_drift)} topic/activity/scope drift, "
            f"{len(candidate)} candidate vs {len(original)} original Types.",
            level="warning",
        )
        return {"types": original}

    merged = len(original) - len(candidate)
    progress.log(
        f"Semantic Type consolidation accepted: {len(candidate)} Type(s)"
        + (f" ({merged} paraphrased duplicate(s) merged)." if merged else "."),
        level="success",
    )
    return {"types": candidate}


def _type_sufficiency_payload(mtype: dict) -> dict:
    """Compact semantic Type payload for the concept sufficiency audit."""
    return {
        "type_id": mtype.get("type_id", ""),
        "type_title": mtype.get("type_title", ""),
        "type_description": mtype.get("type_description", ""),
        "task_pattern": mtype.get("task_pattern", ""),
        "concept_match_hint": mtype.get("concept_match_hint", ""),
        "parent_concept_match_hint": mtype.get(
            "parent_concept_match_hint", ""),
        "topic_match_hint": mtype.get("topic_match_hint", ""),
        "cases": [
            {
                "case_title": (
                    case.get("case_title") or ""
                    if isinstance(case, dict) else str(case)
                ),
                "examples": [
                    _trim(example.get("example_prompt") or "", 1800)
                    for example in _case_examples(case)
                ] if isinstance(case, dict) else [],
            }
            for case in (mtype.get("case_prompts") or [])
        ],
    }


_DERIVATION_ADDITION_RE = re.compile(
    r"\b(?:deriv(?:e|es|ed|ing|ation)?|deduc(?:e|es|ed|ing|tion)|"
    r"prov(?:e|es|ed|ing)|proof|establish(?:es|ed|ing)?|"
    r"develop(?:s|ed|ing)?\s+(?:a\s+)?(?:formula|rule))\b",
    re.IGNORECASE,
)


def _ensure_added_derivation_worked_example(
    details: str,
    *,
    title: str,
    parent: str,
    supporting_type_ids: list[str],
    type_by_id: dict[str, dict],
) -> str:
    """Give a newly split derivation concept a compact mined-Type cue."""
    supporting_types = [
        type_by_id[type_id]
        for type_id in supporting_type_ids
        if type_id in type_by_id
    ]
    context = " ".join([
        title,
        parent,
        *[
            " ".join([
                str(mtype.get("type_title") or ""),
                str(mtype.get("task_pattern") or ""),
            ])
            for mtype in supporting_types
        ],
    ])
    if (
        not _DERIVATION_ADDITION_RE.search(context)
        or re.search(r"\bWorked\s+Example\s*:", details, re.IGNORECASE)
    ):
        return details

    for mtype in supporting_types:
        method = str(
            mtype.get("type_description")
            or mtype.get("task_pattern")
            or mtype.get("type_title")
            or ""
        ).strip()
        for case in mtype.get("case_prompts") or []:
            if not isinstance(case, dict):
                continue
            for example in _case_examples(case):
                prompt = str(example.get("example_prompt") or "").strip()
                if prompt:
                    cue = " ".join(
                        part.rstrip(".") + "."
                        for part in (method, prompt)
                        if part
                    )
                    return _description_with_worked_example(details, cue)
    return details


def _add_missing_type_method_concepts_via_api(
    records: list[dict], *, mined_types: dict, meta: dict,
) -> list[dict]:
    """Add only genuinely missing method concepts identified from mined Types."""
    import json as _json

    types = [
        mtype for mtype in (mined_types or {}).get("types") or []
        if isinstance(mtype, dict) and not mtype.get("is_activity")
    ]
    if not records or not types:
        return records
    concepts: list[dict] = []
    cid_to_index: dict[str, int] = {}
    topic_by_cid: dict[str, str] = {}
    for index, record in enumerate(records, start=1):
        if cr.is_culmination(record.get("concept_title") or ""):
            continue
        cid = f"CONCEPT-{index:04d}"
        cid_to_index[cid] = index - 1
        topic_by_cid[cid] = record.get("topic") or ""
        concepts.append({
            "concept_id": cid,
            "topic": record.get("topic", ""),
            "parent_concept": record.get("parent_concept", ""),
            "concept": record.get("concept_title", ""),
            "description": _concept_description_only(
                record.get("concept_details", "")),
        })
    type_by_id = {
        (mtype.get("type_id") or "").strip(): mtype
        for mtype in types
        if (mtype.get("type_id") or "").strip()
    }
    user = (
        _metadata_block(meta)
        + "\nNORMAL CONCEPTS:\n"
        + _json.dumps({"concepts": concepts}, ensure_ascii=False)
        + "\n\nMINED TYPE METHODS TO SUPPORT:\n"
        + _json.dumps({
            "types": [_type_sufficiency_payload(mtype) for mtype in types],
        }, ensure_ascii=False)
    )
    progress.log("Auditing concept granularity against mined Type methods.")
    try:
        data = _openai_json(
            prompts.get_text("concepts.concept_type_sufficiency.system"),
            user,
            purpose="concept_validation",
        )
    except Exception as exc:  # noqa: BLE001 — existing map remains usable
        progress.log(
            f"Concept/Type sufficiency audit failed ({exc}); keeping concept map.",
            level="warning",
        )
        return records

    existing_titles = {
        bi.normalize_question_text(record.get("concept_title", ""))
        for record in records
    }
    accepted: list[tuple[int, dict]] = []
    supported_by_addition: set[str] = set()
    for addition in (data or {}).get("additions") or []:
        if not isinstance(addition, dict):
            continue
        after_cid = (addition.get("after_concept_id") or "").strip()
        topic = (addition.get("topic") or "").strip()
        title = (addition.get("concept") or "").strip()
        supporting = list(dict.fromkeys(
            str(type_id or "").strip()
            for type_id in addition.get("supporting_type_ids") or []
            if str(type_id or "").strip() in type_by_id
        ))
        if (
            after_cid not in cid_to_index
            or not topic
            or _topic_comparison_key(topic_by_cid[after_cid])
            != _topic_comparison_key(topic)
            or not title
            or cr.is_culmination(title)
            or bi.normalize_question_text(title) in existing_titles
            or not supporting
            or all(type_id in supported_by_addition for type_id in supporting)
        ):
            continue
        if any(
            _topic_comparison_key(type_by_id[type_id].get(
                "topic_match_hint") or "")
            not in {"", _topic_comparison_key(topic)}
            for type_id in supporting
        ):
            continue
        details = kr.canonicalize_rich_text(
            str(addition.get("concept_description") or "").strip())
        parent = (
            addition.get("parent_concept")
            or records[cid_to_index[after_cid]].get("parent_concept")
            or topic
        )
        details = _ensure_added_derivation_worked_example(
            details,
            title=title,
            parent=parent,
            supporting_type_ids=supporting,
            type_by_id=type_by_id,
        )
        candidate = {
            "topic": topic,
            "parent_concept": parent,
            "concept_title": title,
            "concept_details": details,
            "keywords": addition.get("keywords") or "",
        }
        report = cv.validate_concept_rows(
            [candidate],
            allow_types=False,
            require_culmination=False,
            allow_culmination=False,
        )
        if any(error["severity"] == "error" for error in report["errors"]):
            continue
        accepted.append((cid_to_index[after_cid], candidate))
        supported_by_addition.update(supporting)
        existing_titles.add(bi.normalize_question_text(title))

    if not accepted:
        progress.log("Concept/Type sufficiency audit found no missing method concepts.")
        return records
    additions_by_index: dict[int, list[dict]] = {}
    for index, candidate in accepted:
        additions_by_index.setdefault(index, []).append(candidate)
    out: list[dict] = []
    for index, record in enumerate(records):
        out.append(record)
        out.extend(additions_by_index.get(index, []))
    progress.log(
        f"Added {len(accepted)} source-grounded concept(s) for distinct "
        "previously unsupported Type methods.",
        level="success",
    )
    return out


def _canonical_mined_type_title(
    mtype: dict | None, *, include_definition: bool = True,
) -> str:
    """Return the exact title that deterministic Type rendering will emit."""
    mtype = mtype or {}
    title = concept_cleanup.strip_dangling_references(
        (mtype.get("type_title") or mtype.get("task_pattern") or "").strip())
    definition = concept_cleanup.strip_dangling_references(
        (mtype.get("type_description") or "").strip())
    origin_case_count = int(mtype.get("_origin_case_count") or 0)
    if origin_case_count and len(mtype.get("case_prompts") or []) < origin_case_count:
        definition = ""
    normalized_title = cv._normalized_type_definition(title)
    if (
        not title
        or cv._GENERIC_TYPE_DEFINITION_RE.fullmatch(normalized_title)
    ):
        cases = [
            {"case_prompt": raw_case}
            if isinstance(raw_case, str) else raw_case
            for raw_case in (mtype.get("case_prompts") or [])
            if isinstance(raw_case, (dict, str))
        ]
        source_task = next(
            (
                kr.canonicalize_rich_text(
                    _strip_leading_source_task_label(
                        example.get("example_prompt") or "").strip())
                for case in cases
                for example in _case_examples(case)
                if (example.get("example_prompt") or "").strip()
            ),
            "",
        )
        if not source_task:
            source_task = next(
                (
                    _strip_leading_source_task_label(
                        case.get("case_title") or "").strip()
                    for case in cases
                    if (case.get("case_title") or "").strip()
                ),
                "",
            )
        if source_task:
            title, _ = _semantic_fallback_wording(mtype, source_task)
    if (
        include_definition
        and definition
        and _norm_for_compare(definition) != _norm_for_compare(title)
    ):
        title = f"{title} — {definition.rstrip('.')}"
    return title.strip()


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
    title = _canonical_mined_type_title(mtype)
    cases: list[tuple[str, list[str]]] = []
    for case in (mtype.get("case_prompts") or []):
        if isinstance(case, str):
            case = {"case_prompt": case}
        if not isinstance(case, dict):
            continue
        case_title = concept_cleanup.strip_dangling_references(
            _strip_leading_source_task_label(
                case.get("case_title") or "")).strip()
        examples = [
            kr.canonicalize_rich_text(
                _strip_leading_source_task_label(
                    ex.get("example_prompt") or "").strip()
            )
            for ex in _case_examples(case)
        ]
        examples = [ex for ex in examples if ex]
        if not case_title and not examples:
            continue
        # Legacy mined output may place the complete question in ``case_prompt``
        # (or even in ``case_title``) without defining the Case. Preserve that
        # question as an Example and derive a declarative Case variation from
        # the assessed action and conditions.
        if _case_title_needs_definition(
            case_title,
            [{"example_prompt": example} for example in examples],
        ):
            _, case_title = _semantic_fallback_wording({}, examples[0])
        cases.append((case_title, examples))
    if not cases:
        return "", start_type
    if not title:
        return "", start_type
    n = start_type + 1
    parts = [f"Type {n:02d}: {title}"]
    for c_i, (case_title, examples) in enumerate(cases, start=1):
        parts.append(f"Case {c_i:02d}: {case_title}")
        for example_i, example in enumerate(examples, start=1):
            parts.append(f"Example {example_i:02d}: {example}")
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


def _has_valid_terminal_mastery(details: str) -> bool:
    """Whether Description has one substantive canonical mastery ending."""
    description = _concept_description_only(details)
    matches = list(cr._MASTERY_LABEL_RE.finditer(description))
    if len(matches) != 1:
        return False
    match = matches[0]
    statement = description[match.end():].strip()
    return bool(
        statement
        and "\n" not in statement
        and "\r" not in statement
        and description.endswith(f"\nAchieving Mastery: {statement}")
        and cv._is_substantive_mastery_statement(statement)
    )


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

    targets: list[int] = []
    for i, rec in enumerate(records):
        if cr.is_culmination(rec.get("concept_title", "")):
            continue
        rec["concept_details"] = cr.format_mastery_statement(
            rec.get("concept_details", ""))
        if not _has_valid_terminal_mastery(rec.get("concept_details", "")):
            targets.append(i)
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
            data = _openai_json(
                system, user, purpose="concept_detailing")
            for row in _concept_rows_to_records(data):
                desc = _concept_description_only(row.get("concept_details", ""))
                candidate_details = cr.format_mastery_statement(
                    "Description: " + desc)
                if _has_valid_terminal_mastery(candidate_details):
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
            current_description = _concept_description_only(
                rec.get("concept_details", ""))
            existing_mastery = cr._MASTERY_LABEL_RE.search(
                current_description)
            if existing_mastery:
                current_description = current_description[
                    :existing_mastery.start()
                ].rstrip()
            desc = (
                current_description
                + f"\nAchieving Mastery: Applying {title} correctly in new problems."
            )
        rec["concept_details"] = cr.format_mastery_statement(
            _set_description(rec.get("concept_details", ""), desc))
        completed += 1
    progress.log(f"Mastery lines completed for {completed} concept(s).",
                 level="success")
    return records


_WORKED_METHOD_EXAMPLE_RE = re.compile(
    r"\bWorked\s+Example\s*:", re.IGNORECASE)


def _description_with_worked_example(description: str, cue: str) -> str:
    """Insert a worked cue before the terminal mastery statement."""
    description = (description or "").strip()
    cue = kr.canonicalize_rich_text(
        concept_cleanup.strip_dangling_references(cue or "")).strip()
    cue = re.sub(r"\s+", " ", cue)
    cue = _trim(cue, 420).strip().rstrip(".")
    if not cue:
        return description
    mastery = cr._MASTERY_LABEL_RE.search(description)
    if mastery:
        body = description[:mastery.start()].rstrip()
        statement = description[mastery.end():].strip()
        return (
            f"{body} Worked Example: {cue}.\n"
            f"Achieving Mastery: {statement}"
        )
    return f"{description.rstrip()} Worked Example: {cue}.".strip()


def _ensure_method_worked_examples_via_api(
    records: list[dict], *, anchors: list[dict], meta: dict,
) -> list[dict]:
    """Ensure each tagged derivation/method row demonstrates its own method."""
    import json as _json

    anchors_by_id = {
        str(anchor.get("anchor_id") or "").strip().upper(): anchor
        for anchor in anchors
        if str(anchor.get("anchor_id") or "").strip()
    }
    targets = [
        index for index, record in enumerate(records)
        if _method_anchor_ids(record)
        and not _WORKED_METHOD_EXAMPLE_RE.search(
            _concept_description_only(record.get("concept_details", "")))
    ]
    if not targets:
        return records
    payload_rows = [_records_to_api_rows([records[index]])[0] for index in targets]
    relevant_anchors = [
        anchors_by_id[anchor_id]
        for index in targets
        for anchor_id in _method_anchor_ids(records[index])
        if anchor_id in anchors_by_id
    ]
    user = (
        _metadata_block(meta)
        + "\nMETHOD CONCEPT ROWS:\n"
        + _json.dumps({"rows": payload_rows}, ensure_ascii=False)
        + "\n\nSOURCE METHOD ANCHORS:\n"
        + _json.dumps({"anchors": relevant_anchors}, ensure_ascii=False)
    )
    progress.log(
        f"Adding relevant worked derivation cues to {len(targets)} "
        "method concept(s).")
    accepted: dict[tuple[str, str], str] = {}
    try:
        data = _openai_json(
            prompts.get_text("concepts.method_worked_example.system"),
            user,
            purpose="concept_detailing",
        )
        for row in _concept_rows_to_records(data):
            description = kr.canonicalize_rich_text(
                _concept_description_only(row.get("concept_details", "")))
            if _WORKED_METHOD_EXAMPLE_RE.search(description):
                accepted[_record_key(row)] = description
    except Exception as exc:  # noqa: BLE001 — source-grounded fallback follows
        progress.log(
            f"Worked derivation cue pass failed ({exc}); using anchor evidence.",
            level="warning",
        )

    fallback_count = 0
    for index in targets:
        record = records[index]
        description = accepted.get(_record_key(record))
        if not description:
            anchor = next(
                (
                    anchors_by_id[anchor_id]
                    for anchor_id in _method_anchor_ids(record)
                    if anchor_id in anchors_by_id
                ),
                {},
            )
            evidence = str(anchor.get("source_evidence") or "").strip()
            if not evidence:
                formulas = anchor.get("required_formulas") or []
                if formulas:
                    formula = str(formulas[0]).strip()
                    formula = re.sub(
                        r"^(?:\$\$?|\\\[|\\\()|(?:\$\$?|\\\]|\\\))$",
                        "",
                        formula,
                    ).strip()
                    evidence = (
                        "follow the source transformation to obtain "
                        + kr.katex(formula)
                    )
            description = _description_with_worked_example(
                _concept_description_only(
                    record.get("concept_details", "")),
                evidence,
            )
            fallback_count += 1
        record["concept_details"] = _set_description(
            record.get("concept_details", ""), description)
    progress.log(
        f"Worked derivation cues completed for {len(targets)} method concept(s)"
        + (f" ({fallback_count} from deterministic anchor evidence)." if fallback_count else "."),
        level="success",
    )
    return records


def _merge_similar_concepts_via_api(records: list[dict], *, meta: dict) -> list[dict]:
    """Merge near-duplicate concept rows via GPT instead of dropping them.

    ``concept_cleanup.find_similar_title_groups`` only DETECTS suspects; the
    content decision (which title/topic survives, how Descriptions, Types,
    Misconceptions, and Error Analysis combine) is GPT's. On failure the deterministic drop is the
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
            data = _openai_json(system, user, purpose="concept_mapping")
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
    misconception, _ = cr.analysis_components(details)
    if misconception:
        return misconception
    for label, content in cr.split_sections(details or ""):
        if cr.is_misconception_label(label):
            return content.strip()
    return ""


def _error_analysis_body(details: str) -> str:
    """Return the canonical Error Analysis body, accepting known label aliases."""
    _, error_analysis = cr.analysis_components(details)
    if error_analysis:
        return error_analysis
    for label, content in cr.split_sections(details or ""):
        if cr.is_error_analysis_label(label):
            return content.strip()
    return ""


def _learner_analysis_needs_rewrite(details: str) -> bool:
    """Whether a normal concept lacks a usable, non-duplicated analysis tail."""
    misconception = _misconception_body(details)
    error_analysis = _error_analysis_body(details)
    if (
        cv.is_terminal_generic_analysis_filler(misconception)
        or cv.is_terminal_generic_analysis_filler(error_analysis)
    ):
        return True
    misconception_ok = cv.is_valid_misconception(misconception)
    error_analysis_ok = cv.is_valid_error_analysis(error_analysis)
    if not (misconception_ok and error_analysis_ok):
        return True
    if misconception and not misconception_ok:
        return True
    if error_analysis and not error_analysis_ok:
        return True
    return bool(
        misconception_ok
        and error_analysis_ok
        and cv.issue_sections_overlap(misconception, error_analysis)
    )


def _ensure_misconceptions_via_api(
    records: list[dict], *, meta: dict, use_api: bool = True,
    max_attempts: int = 5,
) -> list[dict]:
    """Have GPT write missing or generic learner-analysis sections.

    The legacy function name remains for callers and tests. A normal concept
    needs one combined section containing both a misconception and a distinct
    application/reasoning error.
    """
    import json as _json

    targets = [
        i for i, rec in enumerate(records)
        if not cr.is_culmination(rec.get("concept_title", ""))
        and (rec.get("concept_details") or "").strip()
        and _learner_analysis_needs_rewrite(rec.get("concept_details", ""))
    ]
    if not targets or not use_api:
        return records
    progress.log(
        f"Writing specific learner analysis for {len(targets)} concept(s) via API.")
    system = prompts.get_text("concepts.misconceptions.system")
    by_title: dict[str, tuple[str, str]] = {}
    pending = {
        bi.normalize_question_text(records[i].get("concept_title", "")): i
        for i in targets
    }
    rejection_reasons: dict[str, list[str]] = {}
    attempts = max(1, int(max_attempts or 1))
    for attempt in range(1, attempts + 1):
        if not pending:
            break
        pending_keys = list(pending)
        if attempt > 1:
            progress.log(
                "Retrying specific learner analysis for "
                f"{len(pending)} unresolved concept(s), attempt "
                f"{attempt}/{attempts}.",
                level="warning",
            )
        attempt_accepted = 0
        next_reasons: dict[str, list[str]] = {
            key: ["No row with this exact concept title was returned."]
            for key in pending_keys
        }
        # Smaller batches keep each concept's description and the distinction
        # between its two learner issues salient for long or mixed chapters.
        for batch_start in range(0, len(pending_keys), 4):
            batch_keys = pending_keys[batch_start:batch_start + 4]
            rows = [
                {
                    "topic": records[pending[key]].get("topic", ""),
                    "parent_concept": records[pending[key]].get(
                        "parent_concept", ""),
                    "concept": records[pending[key]].get("concept_title", ""),
                    "concept_description": records[pending[key]].get(
                        "concept_details", ""),
                    "keywords": records[pending[key]].get("keywords", ""),
                }
                for key in batch_keys
            ]
            user = (
                _metadata_block(meta)
                + "\nRows missing usable Misconceptions and/or Error Analysis "
                "sections:\n"
                + _json.dumps({"rows": rows}, ensure_ascii=False)
            )
            if rejection_reasons:
                user += (
                    "\n\nVALIDATION FEEDBACK FROM THE PREVIOUS ATTEMPT. "
                    "Repair every listed row and return each exact concept "
                    "title:\n"
                    + _json.dumps(
                        {
                            records[pending[key]].get("concept_title", ""):
                            rejection_reasons.get(key, [])
                            for key in batch_keys
                        },
                        ensure_ascii=False,
                    )
                )
                if any(
                    any(
                        reason.startswith("Error Analysis must")
                        for reason in rejection_reasons.get(key, [])
                    )
                    for key in batch_keys
                ):
                    user += (
                        "\n\nACTION-ONLY ERROR_ANALYSIS RETRY CONTRACT: For "
                        "every row flagged for Error Analysis, start the field "
                        "with 'Students may' and then name a concrete faulty "
                        "step using an action such as omit, skip, reverse, "
                        "swap, misread, miscopy, miscalculate, mislabel, "
                        "misplace, misapply, add, subtract, multiply, divide, "
                        "count, group, or fail to check. State the specific "
                        "input, representation, operation, or inference that "
                        "the learner handles wrongly and its consequence. Do "
                        "not use believe, think, assume, expect, interpret, "
                        "misinterpret, misunderstand, confuse, mistake, or "
                        "treat anywhere in error_analysis."
                    )
            try:
                data = _openai_json(
                    system, user, purpose="concept_detailing")
                returned_rows = [
                    row for row in (data or {}).get("rows") or []
                    if isinstance(row, dict)
                ]
            except Exception as exc:  # noqa: BLE001 - retry unresolved batch
                progress.log(
                    f"Learner-analysis batch attempt {attempt}/{attempts} "
                    f"failed ({exc}).",
                    level="warning",
                )
                continue
            for row in returned_rows:
                title_key = bi.normalize_question_text(
                    row.get("concept") or row.get("concept_title") or "")
                if title_key not in pending:
                    continue

                def compact_field(*names: str) -> str:
                    value = next(
                        (
                            row.get(name) for name in names
                            if row.get(name) not in (None, "")
                        ),
                        "",
                    )
                    if isinstance(value, list):
                        value = "; ".join(
                            str(item).strip() for item in value if str(item).strip()
                        )
                    return str(value or "").strip()

                misconception = compact_field(
                    "misconception", "misconceptions")
                error_analysis = compact_field(
                    "error_analysis", "errorAnalysis")
                if not (misconception and error_analysis):
                    # Backward compatibility for cached prompts/tests and
                    # providers that still return the older full-row schema.
                    details = cr.normalize_analysis_sections(compact_field(
                        "concept_description", "concept_details"))
                    misconception = (
                        misconception or _misconception_body(details))
                    error_analysis = (
                        error_analysis or _error_analysis_body(details))
                misconception = re.sub(
                    r"^\s*Misconceptions?\s*:\s*",
                    "",
                    misconception,
                    flags=re.IGNORECASE,
                ).strip()
                error_analysis = re.sub(
                    r"^\s*Error\s*Analysis\s*:\s*",
                    "",
                    error_analysis,
                    flags=re.IGNORECASE,
                ).strip()
                reasons: list[str] = []
                if not cv.is_valid_misconception(misconception):
                    reasons.append(
                        "Misconceptions must state a specific learner belief "
                        "using learner voice, not a correction or placeholder."
                    )
                if cv.is_terminal_generic_analysis_filler(misconception):
                    reasons.append(
                        "Misconceptions used forbidden title-substitution "
                        "filler."
                    )
                if not cv.is_valid_error_analysis(error_analysis):
                    reasons.append(
                        "Error Analysis must name the learner and a concrete "
                        "faulty action or reasoning step, not another belief."
                    )
                if cv.is_terminal_generic_analysis_filler(error_analysis):
                    reasons.append(
                        "Error Analysis used forbidden title-substitution "
                        "filler."
                    )
                if (
                    misconception
                    and error_analysis
                    and cv.issue_sections_overlap(
                        misconception, error_analysis)
                ):
                    reasons.append(
                        "Misconceptions and Error Analysis restated the same "
                        "learner problem."
                    )
                if reasons:
                    next_reasons[title_key] = reasons
                    continue
                if misconception and error_analysis:
                    by_title[title_key] = (
                        misconception,
                        error_analysis,
                    )
                    attempt_accepted += 1
        for key in list(pending):
            if key in by_title:
                pending.pop(key)
        rejection_reasons = {
            key: next_reasons.get(
                key, ["The returned learner analysis was not usable."])
            for key in pending
        }
        reason_counts: dict[str, int] = {}
        for reasons in rejection_reasons.values():
            for reason in reasons:
                label = reason.split(".", 1)[0]
                reason_counts[label] = reason_counts.get(label, 0) + 1
        if reason_counts:
            progress.log(
                "Learner-analysis rejection summary: "
                + "; ".join(
                    f"{label}={count}"
                    for label, count in sorted(reason_counts.items())
                ),
                level="warning",
            )
        progress.log(
            "Specific learner-analysis attempt "
            f"{attempt}/{attempts} accepted {attempt_accepted} row(s); "
            f"{len(pending)} remain.",
            level="success" if not pending else "warning",
        )
    completed = 0
    for i in targets:
        rec = records[i]
        analysis = by_title.get(bi.normalize_question_text(rec.get("concept_title", "")))
        if not analysis:
            continue
        misconception, error_analysis = analysis
        sections = [
            (label, content)
            for label, content in cr.split_sections(rec.get("concept_details", ""))
            if not cr.is_learner_analysis_label(label)
        ]
        combined: list[str] = []
        if misconception:
            combined.append(f"Misconceptions: {misconception}")
        if error_analysis:
            combined.append(f"Error Analysis: {error_analysis}")
        if combined:
            sections.append((
                "Misconception/ Error Analysis",
                "; ".join(combined),
            ))
        rec["concept_details"] = cr.join_sections(sections)
        completed += 1
    if completed != len(targets):
        missing = [
            records[i].get("concept_title", "")
            for i in targets
            if bi.normalize_question_text(
                records[i].get("concept_title", ""))
            not in by_title
        ]
        progress.log(
            "Specific learner analysis accepted for "
            f"{completed}/{len(targets)} concept(s); final content is blocked.",
            level="error",
        )
        raise RuntimeError(
            "specific learner-analysis generation returned unusable rows for: "
            + ", ".join(missing[:8])
        )
    progress.log(
        f"Specific learner analysis written for {completed} concept(s).",
        level="success",
    )
    return records


def _assignment_case_qids(raw_case: object) -> list[str]:
    """Return one Case's ordered qids without splitting any of its Examples."""
    if isinstance(raw_case, str):
        case = {"case_prompt": raw_case}
    elif isinstance(raw_case, dict):
        case = raw_case
    else:
        return []
    qids: list[str] = []
    direct_qid = (case.get("source_question_id") or "").strip()
    if direct_qid:
        qids.append(direct_qid)
    for example in _case_examples(case):
        qid = (example.get("source_question_id") or "").strip()
        if qid and qid not in qids:
            qids.append(qid)
    return qids


def _expand_mined_types_to_assignment_units(types: list[dict]) -> list[dict]:
    """Create stable one-Case assignment units while preserving qids exactly.

    Inventory coverage is deliberately not reinterpreted here: it has already
    been validated against the original mined Types. This boundary only proves
    that expansion neither loses nor duplicates any of those original qids.
    """
    units: list[dict] = []
    original_qid_owners: dict[str, list[str]] = {}
    for mtype in types:
        type_id = (mtype.get("type_id") or "").strip()
        for qid in _type_source_qids(mtype):
            original_qid_owners.setdefault(qid, []).append(type_id)

        raw_cases = list(mtype.get("case_prompts") or [])
        if len(raw_cases) <= 1:
            unit = copy.deepcopy(mtype)
            unit["_origin_type_id"] = type_id
            unit["_origin_case_count"] = len(raw_cases)
            if raw_cases:
                unit["case_prompts"] = [copy.deepcopy(raw_cases[0])]
                case_qids = _assignment_case_qids(raw_cases[0])
                # A sole Case owns all Type-level trace qids, including legacy
                # payloads where source_question_id existed only on the Type.
                for qid in _type_source_qids(mtype):
                    if qid not in case_qids:
                        case_qids.append(qid)
                unit["source_question_ids"] = case_qids
            else:
                unit["source_question_ids"] = _type_source_qids(mtype)
            units.append(unit)
            continue

        for case_index, raw_case in enumerate(raw_cases, start=1):
            case_id = (
                (raw_case.get("case_id") or "").strip()
                if isinstance(raw_case, dict) else ""
            )
            case_id = case_id or f"CASE-{case_index:04d}"
            unit = copy.deepcopy(mtype)
            unit["_origin_type_id"] = type_id
            unit["_origin_case_count"] = len(raw_cases)
            unit["type_id"] = (
                f"{type_id}::{case_id}::{case_index:04d}")
            unit["source_question_ids"] = _assignment_case_qids(raw_case)
            unit["case_prompts"] = [copy.deepcopy(raw_case)]
            units.append(unit)

    original_duplicates = sorted(
        qid for qid, owners in original_qid_owners.items()
        if len(owners) != 1
    )
    unit_qid_owners: dict[str, list[str]] = {}
    for unit in units:
        unit_id = (unit.get("type_id") or "").strip()
        for qid in unit.get("source_question_ids") or []:
            qid = (qid or "").strip()
            if qid:
                unit_qid_owners.setdefault(qid, []).append(unit_id)

    expected_qids = set(original_qid_owners)
    actual_qids = set(unit_qid_owners)
    lost_qids = sorted(expected_qids - actual_qids)
    extra_qids = sorted(actual_qids - expected_qids)
    duplicated_qids = sorted(
        qid for qid, owners in unit_qid_owners.items()
        if len(owners) != 1
    )
    unit_counts = _inventory_assignment_counts(units)
    non_exact_counts = sorted(
        (qid, unit_counts.get(qid, 0))
        for qid in expected_qids
        if unit_counts.get(qid, 0) != 1
    )
    if (
        original_duplicates
        or lost_qids
        or extra_qids
        or duplicated_qids
        or non_exact_counts
    ):
        defects: list[str] = []
        if original_duplicates:
            defects.append(
                "original duplicate qids " + ", ".join(original_duplicates))
        if lost_qids:
            defects.append("lost qids " + ", ".join(lost_qids))
        if extra_qids:
            defects.append("unexpected qids " + ", ".join(extra_qids))
        if duplicated_qids:
            defects.append(
                "duplicated qids " + ", ".join(duplicated_qids))
        if non_exact_counts:
            defects.append(
                "non-exact unit Example counts "
                + ", ".join(f"{qid}={count}" for qid, count in non_exact_counts)
            )
        raise RuntimeError(
            "type embedding assignment-unit qid invariant failed: "
            + "; ".join(defects)
        )
    return units


def _collapse_assignment_units_for_render(units: list[dict]) -> list[dict]:
    """Rejoin same-concept Case units from one mined Type before rendering."""
    collapsed: list[dict] = []
    index_by_origin: dict[str, int] = {}
    for raw_unit in units:
        unit = copy.deepcopy(raw_unit)
        origin = (
            unit.pop("_origin_type_id", "")
            or (unit.get("type_id") or "").split("::", 1)[0]
        )
        existing_index = index_by_origin.get(origin)
        if existing_index is None:
            unit["type_id"] = origin or unit.get("type_id", "")
            index_by_origin[origin] = len(collapsed)
            collapsed.append(unit)
            continue
        target = collapsed[existing_index]
        for qid in unit.get("source_question_ids") or []:
            if qid not in target.setdefault("source_question_ids", []):
                target["source_question_ids"].append(qid)
        target.setdefault("case_prompts", []).extend(
            copy.deepcopy(unit.get("case_prompts") or []))
    return collapsed


_ASSIGNMENT_PREFIX_STOPWORDS = {
    "and", "are", "can", "did", "for", "has", "how", "its", "not",
    "one", "the", "two", "use", "why", "you",
    "appl", "base", "case", "conc", "desc", "dete", "exam", "expl",
    "find", "give", "iden", "inte", "ques", "sour", "stat", "usin",
    "writ", "thro",
}
_MIXED_ASSIGNMENT_CUE_RE = re.compile(
    r"(?:\b(?:any\s+two|two|several|multiple)(?:\s+\w+){0,2}\s+"
    r"(?:countries|cases|methods|concepts|relationships|ideas|principles)\b|"
    r"\b(?:compare|comparison|contrast)\b(?=.{0,80}\b"
    r"(?:countries|cases|methods|concepts|relationships|ideas|principles)\b)|"
    r"\bacross\s+(?:cases|concepts)\b|"
    r"\bmulti[-\s]+concept\b|"
    r"\b(?:combin(?:e|ing)|integrat(?:e|ing))\b(?=.{0,80}\b"
    r"(?:methods|concepts|relationships|ideas|principles)\b)|"
    r"\bsynthesi[sz](?:e|ing|s)\b)",
    re.IGNORECASE,
)
# Inventory-only fallback inference must be more selective than the legacy
# mined-Type heuristic above. A task can compare two values or diagrams while
# still belonging to one normal concept; these cues specifically establish
# multi-method/concept synthesis when persisted mined metadata is absent.
_INVENTORY_SYNTHESIS_CUE_RE = re.compile(
    r"(?:\b(?:any\s+two|two|several|multiple)"
    r"(?:\s+\w+){0,2}\s+"
    r"(?:cases|methods|concepts|relationships|ideas|principles)\b|"
    r"\bacross\s+(?:cases|concepts)\b|"
    r"\bsynthesi[sz]e\b|"
    r"\bcombin(?:e|ing)\b(?=.{0,80}\b"
    r"(?:methods|concepts|relationships|ideas|principles)\b))",
    re.IGNORECASE,
)
_INVENTORY_CROSS_TOPIC_SYNTHESIS_CUE_RE = re.compile(
    r"\b(?:across|between|combining)\s+(?:different\s+)?"
    r"(?:source\s+)?topics\b",
    re.IGNORECASE,
)
_CROSS_TOPIC_ASSIGNMENT_CUE_RE = re.compile(
    r"\b(?:across|between|combining)\s+(?:different\s+)?"
    r"(?:source\s+)?(?:topics|sections)\b",
    re.IGNORECASE,
)
_ASSIGNMENT_PLACEMENT_SCOPES = frozenset({
    "normal", "mixed_synthesis", "cross_topic_synthesis",
})


def _assignment_scope_evidence(mtype: dict) -> str:
    """Source/task wording allowed to certify synthesis placement."""
    source_task = str(mtype.get("_source_task_evidence") or "").strip()
    if source_task:
        return source_task
    examples = [
        str(example.get("example_prompt") or "").strip()
        for case in (mtype.get("case_prompts") or [])
        if isinstance(case, dict)
        for example in _case_examples(case)
        if str(example.get("example_prompt") or "").strip()
    ]
    if examples:
        return " ".join(examples)
    # Legacy units may lack structured Examples. task_pattern is closer to the
    # actual ask than generated taxonomy titles or destination hints.
    return str(mtype.get("task_pattern") or "").strip()


def _scope_payload_rows(
    concept_payload: dict[str, dict] | list[dict] | tuple[dict, ...] | None,
) -> list[dict]:
    if isinstance(concept_payload, dict):
        return [
            row for row in concept_payload.values()
            if isinstance(row, dict)
        ]
    return [
        row for row in (concept_payload or ())
        if isinstance(row, dict)
    ]


def _evidence_topic_matches(
    evidence: str,
    concept_payload: dict[str, dict] | list[dict] | tuple[dict, ...] | None,
) -> set[str]:
    """Topics independently named by discriminative task/concept wording."""
    # Culmination rows still carry an authoritative topic title. Include them
    # when detecting cross-topic coverage so a chapter map with no normal row
    # in the later topic can still prove that the source task names that topic.
    rows = _scope_payload_rows(concept_payload)
    prefixes_by_topic: dict[str, set[str]] = {}
    for row in rows:
        topic_key = _topic_comparison_key(row.get("topic") or "")
        if not topic_key:
            continue
        prefixes_by_topic.setdefault(topic_key, set()).update(
            _assignment_prefixes(
                " ".join([
                    str(row.get("topic") or ""),
                    str(row.get("concept") or row.get("concept_title") or ""),
                ])
            )
        )
    frequency: dict[str, int] = {}
    for prefixes in prefixes_by_topic.values():
        for prefix in prefixes:
            frequency[prefix] = frequency.get(prefix, 0) + 1
    evidence_prefixes = _assignment_prefixes(evidence)
    return {
        topic_key
        for topic_key, prefixes in prefixes_by_topic.items()
        if any(
            prefix in evidence_prefixes and frequency.get(prefix) == 1
            for prefix in prefixes
        )
    }


def _evidence_spans_concepts_in_source_topic(
    evidence: str,
    mtype: dict,
    concept_payload: dict[str, dict] | list[dict] | tuple[dict, ...] | None,
) -> bool:
    """Whether source wording names two distinct normal concepts in one topic."""
    source_topic = _topic_comparison_key(
        mtype.get("_source_topic_evidence")
        or mtype.get("topic_match_hint")
        or ""
    )
    rows = [
        row for row in _scope_payload_rows(concept_payload)
        if (
            not row.get("is_culmination")
            and (
                not source_topic
                or _topic_comparison_key(row.get("topic") or "")
                == source_topic
            )
        )
    ]
    prefixes_by_concept: dict[str, set[str]] = {}
    for index, row in enumerate(rows):
        cid = str(row.get("concept_id") or f"concept-{index}")
        prefixes_by_concept[cid] = _assignment_prefixes(
            row.get("concept") or row.get("concept_title") or "")
    frequency: dict[str, int] = {}
    for prefixes in prefixes_by_concept.values():
        for prefix in prefixes:
            frequency[prefix] = frequency.get(prefix, 0) + 1
    evidence_prefixes = _assignment_prefixes(evidence)
    matched = {
        cid
        for cid, prefixes in prefixes_by_concept.items()
        if any(
            prefix in evidence_prefixes and frequency.get(prefix) == 1
            for prefix in prefixes
        )
    }
    return len(matched) >= 2


def _assignment_placement_scope(
    mtype: dict,
    concept_payload: dict[str, dict] | list[dict] | tuple[dict, ...] | None = None,
) -> str:
    """Resolve GPT-authored Case scope, with a safe legacy fallback."""
    if mtype.get("is_activity"):
        return "normal"
    # Destination hints are routing metadata, not proof that a task really is
    # multi-concept synthesis. Otherwise a self-consistent bad hint such as
    # "Integrating Circuit Concepts" can make an ordinary calculation look
    # eligible for a Culmination.
    evidence = _assignment_scope_evidence(mtype)
    cross_topic_supported = bool(
        _CROSS_TOPIC_ASSIGNMENT_CUE_RE.search(evidence)
        or len(_evidence_topic_matches(evidence, concept_payload)) >= 2
    )
    mixed_supported = bool(
        _MIXED_ASSIGNMENT_CUE_RE.search(evidence)
        or _evidence_spans_concepts_in_source_topic(
            evidence, mtype, concept_payload)
    )
    case_scopes = {
        (case.get("placement_scope") or "").strip().lower()
        for case in (mtype.get("case_prompts") or [])
        if isinstance(case, dict)
        and (case.get("placement_scope") or "").strip().lower()
        in _ASSIGNMENT_PLACEMENT_SCOPES
    }
    if len(case_scopes) == 1:
        scope = next(iter(case_scopes))
        if (
            scope == "mixed_synthesis"
            and not mixed_supported
        ):
            return "normal"
        if (
            scope == "cross_topic_synthesis"
            and not cross_topic_supported
        ):
            return "normal"
        return scope
    type_scope = (mtype.get("placement_scope") or "").strip().lower()
    if type_scope in _ASSIGNMENT_PLACEMENT_SCOPES:
        if (
            type_scope == "mixed_synthesis"
            and not mixed_supported
        ):
            return "normal"
        if (
            type_scope == "cross_topic_synthesis"
            and not cross_topic_supported
        ):
            return "normal"
        return type_scope
    # Backward compatibility for persisted/fixture Types authored before
    # placement_scope existed. New GPT output uses the explicit Case field.
    if cross_topic_supported:
        return "cross_topic_synthesis"
    if mixed_supported:
        return "mixed_synthesis"
    return "normal"


def _assignment_prefixes(text: str) -> set[str]:
    prefixes: set[str] = set()
    for word in _topic_comparison_key(text).split():
        if len(word) < 3:
            continue
        prefix = word if len(word) == 3 else word[:4]
        if prefix not in _ASSIGNMENT_PREFIX_STOPWORDS:
            prefixes.add(prefix)
    return prefixes


def _assignment_unit_semantic_evidence(mtype: dict) -> str:
    """Task semantics used to prove scope/host, excluding routing hints."""
    source_task = str(mtype.get("_source_task_evidence") or "").strip()
    if source_task:
        # At terminal boundaries the exact inventory task is available. It is
        # the only independent evidence allowed to certify scope or host; a
        # model-authored Type title/Case must not outweigh or embellish it.
        return source_task
    parts = [
        mtype.get("type_title") or "",
        mtype.get("type_description") or "",
        mtype.get("task_pattern") or "",
    ]
    for case in mtype.get("case_prompts") or []:
        if not isinstance(case, dict):
            parts.append(str(case))
            continue
        parts.extend([
            case.get("case_title") or "",
            case.get("case_signature") or "",
        ])
        parts.extend(
            example.get("example_prompt") or ""
            for example in _case_examples(case)
        )
    return " ".join(str(part) for part in parts if part)


def _assignment_unit_text(mtype: dict) -> str:
    """Full assignment context, including non-certifying routing hints."""
    return " ".join(
        str(part)
        for part in (
            _assignment_unit_semantic_evidence(mtype),
            mtype.get("concept_match_hint") or "",
            mtype.get("parent_concept_match_hint") or "",
            mtype.get("topic_match_hint") or "",
        )
        if part
    )


def _high_confidence_assignment_override(
    mtype: dict, candidate_cids: tuple[str, ...],
    concept_payload_by_id: dict[str, dict],
) -> str:
    """Use unambiguous task/title evidence before accepting a model guess."""
    candidates = [
        concept_payload_by_id[cid] for cid in candidate_cids
        if cid in concept_payload_by_id
    ]
    normal = [row for row in candidates if not row.get("is_culmination")]
    culminations = [
        row["concept_id"] for row in candidates if row.get("is_culmination")
    ]
    # Textbook activities belong on Activity/Info Hub of a normal concept —
    # never force them onto Culmination Types.
    is_activity = bool(mtype.get("is_activity"))
    if is_activity and len(normal) == 1:
        return normal[0]["concept_id"]
    evidence = _assignment_unit_semantic_evidence(mtype)
    if (
        not is_activity
        and len(culminations) == 1
        and _assignment_placement_scope(
            mtype, concept_payload_by_id) == "mixed_synthesis"
    ):
        return culminations[0]
    if _assignment_placement_scope(
        mtype, concept_payload_by_id
    ) == "cross_topic_synthesis":
        # The explicit classifier and ID-constrained GPT assignment must decide
        # whether this genuinely needs a later Culmination. Prefix overlap with
        # the source topic is expected and must not deterministically override
        # that semantic decision.
        return ""

    title_prefixes = {
        row["concept_id"]: _assignment_prefixes(row.get("concept") or "")
        for row in normal
    }
    topic_prefixes = _assignment_prefixes(
        " ".join(row.get("topic") or "" for row in normal))
    prefix_frequency: dict[str, int] = {}
    for prefixes in title_prefixes.values():
        for prefix in prefixes - topic_prefixes:
            prefix_frequency[prefix] = prefix_frequency.get(prefix, 0) + 1
    evidence_prefixes = _assignment_prefixes(evidence)
    scores = {
        cid: len({
            prefix for prefix in prefixes - topic_prefixes
            if prefix_frequency.get(prefix) == 1
            and prefix in evidence_prefixes
        })
        for cid, prefixes in title_prefixes.items()
    }
    ranked = sorted(scores.items(), key=lambda pair: pair[1], reverse=True)
    minimum_score = 2 if is_activity and len(normal) > 1 else 1
    if ranked and ranked[0][1] >= minimum_score:
        runner_up = ranked[1][1] if len(ranked) > 1 else 0
        if ranked[0][1] > runner_up:
            return ranked[0][0]
    return ""


def _review_case_unit_hosts_via_api(
    *,
    assignment_units: list[dict],
    per_concept: dict[str, list[dict]],
    concept_payload: list[dict],
    allowed_cids_by_tid: dict[str, set[str]],
    meta: dict,
    max_attempts: int = 3,
) -> dict[str, list[dict]]:
    """Second-pass semantic host review, constrained to proven concept IDs."""
    import json as _json

    current_by_tid: dict[str, str] = {}
    for cid, units in per_concept.items():
        for unit in units:
            tid = (unit.get("type_id") or "").strip()
            if tid:
                current_by_tid[tid] = cid
    concept_payload_by_id = {
        str(row.get("concept_id") or "").strip(): row
        for row in concept_payload
        if str(row.get("concept_id") or "").strip()
    }
    review_units: list[dict] = []
    for unit in assignment_units:
        tid = (unit.get("type_id") or "").strip()
        current = current_by_tid.get(tid)
        if not current or unit.get("is_activity"):
            continue
        allowed = tuple(sorted(
            cid for cid in (allowed_cids_by_tid.get(tid) or set())
            if cid in concept_payload_by_id
        ))
        # A sole legal destination, or an independently proven destination,
        # needs no second probabilistic verdict. Ambiguous assignments still
        # fail closed through the complete-review contract below.
        proven = _high_confidence_assignment_override(
            unit, allowed, concept_payload_by_id)
        if allowed == (current,) or proven == current:
            continue
        review_units.append(unit)
    if not review_units:
        return per_concept
    progress.log(
        f"Reviewing semantic host entailment for {len(review_units)} "
        "Type/Case assignment unit(s).")
    proposed: dict[str, str] = {}
    units_by_tid = {
        str(unit.get("type_id") or "").strip(): unit
        for unit in review_units
    }
    known_tids = set(units_by_tid)
    unresolved_invalid: set[str] = set()
    for attempt in range(1, max(1, max_attempts) + 1):
        pending_tids = sorted(known_tids - set(proposed))
        if not pending_tids:
            break
        payload = []
        for tid in pending_tids:
            unit = units_by_tid[tid]
            item = copy.deepcopy(unit)
            item["current_concept_id"] = current_by_tid[tid]
            item["allowed_concept_ids"] = sorted(
                allowed_cids_by_tid.get(tid) or set())
            item["placement_scope"] = _assignment_placement_scope(
                unit, concept_payload_by_id)
            payload.append(item)
        retry_contract = ""
        if attempt > 1:
            retry_contract = (
                "\n\nRETRY CONTRACT: The previous response omitted or "
                "malformed one or more opaque IDs. Return exactly these "
                "remaining type_id values once each, character-for-character: "
                + _json.dumps(pending_tids, ensure_ascii=False)
            )
        user = (
            _metadata_block(meta)
            + "\nCONCEPT HOSTS:\n"
            + _json.dumps({"concepts": concept_payload}, ensure_ascii=False)
            + "\n\nCURRENT CASE-SCOPED ASSIGNMENTS TO REVIEW:\n"
            + _json.dumps({"types": payload}, ensure_ascii=False)
            + retry_contract
        )
        try:
            data = _openai_json(
                prompts.get_text("concepts.type_host_review.system"),
                user,
                purpose="concept_validation",
            )
        except Exception as exc:  # noqa: BLE001
            progress.log(
                f"Type host entailment review failed ({exc}); refusing to "
                "certify ambiguous concept hosts.",
                level="error",
            )
            raise RuntimeError(
                "type host entailment review failed before a complete verdict"
            ) from exc

        invalid_this_attempt: set[str] = set()
        seen_this_attempt: set[str] = set()
        canonicalized_aliases = 0
        pending_by_origin: dict[str, list[str]] = {}
        for pending_tid in pending_tids:
            origin_tid = str(
                units_by_tid[pending_tid].get("_origin_type_id") or "").strip()
            if origin_tid:
                pending_by_origin.setdefault(origin_tid, []).append(
                    pending_tid)
        for assignment in (data or {}).get("assignments") or []:
            if not isinstance(assignment, dict):
                continue
            returned_tid = (assignment.get("type_id") or "").strip()
            tid = returned_tid
            if tid not in pending_tids:
                aliases = pending_by_origin.get(returned_tid) or []
                if len(aliases) == 1:
                    # A model occasionally drops the case suffix even though
                    # only one unresolved Case of that original Type remains.
                    # The mapping is then exact and unambiguous.
                    tid = aliases[0]
                    canonicalized_aliases += 1
            cid = (assignment.get("concept_id") or "").strip()
            if tid not in pending_tids or tid in seen_this_attempt:
                if returned_tid:
                    invalid_this_attempt.add(returned_tid)
                continue
            seen_this_attempt.add(tid)
            if cid not in (allowed_cids_by_tid.get(tid) or set()):
                invalid_this_attempt.add(tid)
                continue
            proposed[tid] = cid
        if canonicalized_aliases:
            progress.log(
                "Canonicalized "
                f"{canonicalized_aliases} uniquely resolvable parent Type "
                "ID verdict(s) to their opaque Case assignment-unit IDs.",
                level="warning",
            )
        unresolved_invalid = invalid_this_attempt
        remaining = sorted(known_tids - set(proposed))
        if not remaining:
            break
        if attempt < max(1, max_attempts):
            progress.log(
                "Type host entailment review left "
                f"{len(remaining)} unresolved assignment unit(s) on attempt "
                f"{attempt}; retrying only the missing/invalid opaque IDs.",
                level="warning",
            )

    missing_verdicts = sorted(known_tids - set(proposed))
    if missing_verdicts:
        details: list[str] = []
        if unresolved_invalid:
            details.append(
                "invalid verdicts for "
                + ", ".join(sorted(unresolved_invalid)))
        details.append(
            "missing verdicts for " + ", ".join(missing_verdicts))
        raise RuntimeError(
            "type host entailment review did not certify every assignment "
            "unit: " + "; ".join(details)
        )

    rebuilt: dict[str, list[dict]] = {}
    moved = 0
    for unit in assignment_units:
        tid = (unit.get("type_id") or "").strip()
        current = current_by_tid.get(tid)
        if not current:
            continue
        # Activities deliberately bypass this semantic Type-host review; their
        # authoritative inventory placement is handled by Activity/Info Hubs.
        # Preserve those already-constrained assignments while requiring a
        # complete verdict for every unit that was actually reviewed.
        target = proposed.get(tid, current)
        if target != current:
            moved += 1
        rebuilt.setdefault(target, []).append(unit)
    progress.log(
        f"Type host entailment review moved {moved} assignment unit(s); "
        "every reviewed unit received a constrained semantic verdict.",
        level="success",
    )
    return rebuilt


def _assign_mined_types_via_api(
    records: list[dict], *, meta: dict, mined_types: dict, max_attempts: int = 4,
) -> list[dict]:
    """Embed every mined Case within its source topic using exact API IDs.

    Exact inventory coverage belongs to the original mined Types. Multi-Case
    Types are expanded only for assignment into one internal unit per Case, with
    all Examples in that Case kept together. Source-topic-scoped units are
    grouped by canonical topic and allowed concept IDs. Ordinary and same-topic
    synthesis units stay in that source topic. Explicit cross-topic synthesis
    units additionally see only later-topic Culminations. Omitted IDs are
    retried against the same candidate list. Placement is joined by exact IDs
    only — no regex, token, or word matching.
    """
    import json as _json

    types = (mined_types or {}).get("types") or []
    if not types:
        return records
    # A reassignment is a new review boundary. Never carry host verdicts from
    # an earlier row topology into the freshly materialized concept map.
    _reset_placement_certifications(mined_types)

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
    concept_payload_by_id = {
        row["concept_id"]: row for row in concept_payload
    }

    original_types_by_id: dict[str, dict] = {}
    for i, t in enumerate(types, start=1):
        tid = (t.get("type_id") or f"TYPE-{i:04d}").strip() or f"TYPE-{i:04d}"
        t = copy.deepcopy(t)
        t["type_id"] = tid
        if tid in original_types_by_id:
            raise RuntimeError(
                "type embedding failed: duplicate mined Type ID "
                f"{tid} prevents exact-once assignment")
        original_types_by_id[tid] = t

    assignment_units = _expand_mined_types_to_assignment_units(
        list(original_types_by_id.values()))
    types_by_id = {
        unit["type_id"]: unit
        for unit in assignment_units
    }
    if len(types_by_id) != len(assignment_units):
        raise RuntimeError(
            "type embedding failed: duplicate case-scoped assignment-unit ID")

    concept_ids_by_topic: dict[str, list[str]] = {}
    topic_position: dict[str, int] = {}
    for row in concept_payload:
        topic_key = _topic_comparison_key(row.get("topic", ""))
        concept_ids_by_topic.setdefault(topic_key, []).append(row["concept_id"])
        topic_position.setdefault(topic_key, row["chapter_position"])

    all_concept_ids = tuple(cid_map)
    topic_key_by_tid: dict[str, str] = {}
    allowed_cids_by_tid: dict[str, set[str]] = {}
    candidate_cids_by_tid: dict[str, tuple[str, ...]] = {}
    missing_scopes: list[tuple[str, str, str]] = []
    for tid, mtype in types_by_id.items():
        source_topic = (mtype.get("topic_match_hint") or "").strip()
        topic_key = _topic_comparison_key(source_topic)
        topic_key_by_tid[tid] = topic_key
        if topic_key:
            topic_candidates = set(concept_ids_by_topic.get(topic_key, []))
        else:
            topic_candidates = set(all_concept_ids)
        normal_candidates = {
            cid for cid in topic_candidates
            if not cr.is_culmination(
                cid_map[cid].get("concept_title", ""))
        }
        placement_scope = _assignment_placement_scope(
            mtype, concept_payload_by_id)
        allowed = normal_candidates
        if not mtype.get("is_activity") and placement_scope in {
            "mixed_synthesis", "cross_topic_synthesis",
        }:
            allowed = set(topic_candidates)
        if (
            not mtype.get("is_activity")
            and placement_scope == "cross_topic_synthesis"
            and topic_key in topic_position
        ):
            source_position = topic_position[topic_key]
            allowed.update(
                row["concept_id"]
                for row in concept_payload
                if row["is_culmination"]
                and row["chapter_position"] > source_position
                and _topic_comparison_key(row.get("topic", "")) != topic_key
            )
        allowed_cids_by_tid[tid] = allowed
        candidate_cids_by_tid[tid] = tuple(
            cid for cid in all_concept_ids if cid in allowed)
        if not allowed:
            missing_scopes.append((tid, source_topic, topic_key))

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
            "type embedding eligibility failed: no allowed normal/mixed "
            f"concept candidates for {failures}; available normalized concept "
            f"topics: {available}"
        )

    if not all_concept_ids:
        raise RuntimeError(
            "type embedding failed: no concept candidates for mined Types "
            + ", ".join(types_by_id)
        )

    system = prompts.get_text("concepts.type_embedding.system")
    groups: dict[tuple[str, tuple[str, ...]], list[str]] = {}
    for tid in types_by_id:
        group_key = (topic_key_by_tid[tid], candidate_cids_by_tid[tid])
        groups.setdefault(group_key, []).append(tid)

    per_concept: dict[str, list[dict]] = {}
    unassigned: set[str] = set()
    rejections_by_tid: dict[str, list[dict]] = {}
    for (_, candidate_cids), group_type_ids in groups.items():
        scoped_concepts = [
            concept_payload_by_id[cid] for cid in candidate_cids
        ]
        candidate_cid_set = set(candidate_cids)
        remaining_in_group = set(group_type_ids)
        override_by_tid = {
            tid: cid
            for tid in group_type_ids
            for cid in [_high_confidence_assignment_override(
                types_by_id[tid], candidate_cids, concept_payload_by_id)
            ]
            if cid
        }
        overridden: set[str] = set()
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
                item["allowed_concept_ids"] = sorted(allowed)
                item["placement_scope"] = _assignment_placement_scope(
                    item, concept_payload_by_id)
                if rejections_by_tid.get(tid):
                    item["previous_rejections"] = rejections_by_tid[tid][-3:]
                pending.append(item)
            user = (
                _metadata_block(meta)
                + "\nCONCEPTS (assign every mined Type assignment unit to "
                "exactly one concept_id):\n"
                + _json.dumps({"concepts": scoped_concepts}, ensure_ascii=False)
                + "\n\nMINED TYPE ASSIGNMENT UNITS "
                "(every type_id MUST be assigned):\n"
                + _json.dumps({"types": pending}, ensure_ascii=False)
            )
            data = _openai_json(system, user, purpose="concept_mapping")
            rejected_counts: dict[str, int] = {}
            responded_tids: set[str] = set()
            for assignment in data.get("assignments") or []:
                if not isinstance(assignment, dict):
                    continue
                cid = (assignment.get("concept_id") or "").strip()
                for tid in assignment.get("type_ids") or []:
                    tid = (tid or "").strip()
                    if tid not in remaining_in_group:
                        continue
                    responded_tids.add(tid)
                    allowed = allowed_cids_by_tid.get(tid)
                    effective_cid = override_by_tid.get(tid) or cid
                    target_is_culmination = concept_payload_by_id.get(
                        effective_cid, {}).get("is_culmination")
                    type_allows_culmination = (
                        not types_by_id[tid].get("is_activity")
                        and _assignment_placement_scope(
                            types_by_id[tid], concept_payload_by_id)
                        in {"mixed_synthesis", "cross_topic_synthesis"}
                    )
                    reason = ""
                    if effective_cid not in cid_map:
                        reason = "unknown_concept_id"
                    elif effective_cid not in candidate_cid_set:
                        reason = "not_in_eligible_scope"
                    elif effective_cid not in allowed:
                        reason = "not_in_allowed_concept_ids"
                    elif target_is_culmination and not type_allows_culmination:
                        reason = "culmination_not_eligible"
                    if reason:
                        rejected_counts[reason] = (
                            rejected_counts.get(reason, 0) + 1)
                        rejections_by_tid.setdefault(tid, []).append({
                            "attempt": attempt,
                            "concept_id": cid,
                            "reason": reason,
                        })
                        continue
                    per_concept.setdefault(effective_cid, []).append(
                        types_by_id[tid])
                    if effective_cid != cid:
                        overridden.add(tid)
                    remaining_in_group.discard(tid)
            for tid in sorted(remaining_in_group - responded_tids):
                rejected_counts["omitted_from_response"] = (
                    rejected_counts.get("omitted_from_response", 0) + 1)
                rejections_by_tid.setdefault(tid, []).append({
                    "attempt": attempt,
                    "concept_id": "",
                    "reason": "omitted_from_response",
                })
            if rejected_counts:
                summary = ", ".join(
                    f"{reason}={count}"
                    for reason, count in sorted(rejected_counts.items())
                )
                progress.log(
                    "Rejected/unresolved mined Type assignment-unit "
                    f"placement(s) ({summary}); retrying only those type IDs "
                    "with rejection feedback.",
                    level="warning",
                )
            placed = len(group_type_ids) - len(remaining_in_group)
            progress.log(
                f"Type embedding scope {scope_label!r} attempt {attempt}: "
                f"{placed}/{len(group_type_ids)} assignment units assigned.")
        for tid in list(remaining_in_group):
            cid = override_by_tid.get(tid)
            if not cid:
                continue
            per_concept.setdefault(cid, []).append(types_by_id[tid])
            remaining_in_group.discard(tid)
            overridden.add(tid)
        deferred_activities = {
            tid for tid in remaining_in_group
            if types_by_id[tid].get("is_activity")
        }
        if deferred_activities:
            remaining_in_group.difference_update(deferred_activities)
            progress.log(
                f"Deferred {len(deferred_activities)} ambiguous activity "
                "assignment unit(s) to Activity/Info Hub inventory placement.",
                level="warning",
            )
        if overridden:
            progress.log(
                f"Corrected {len(overridden)} Type assignment unit(s) using "
                "unambiguous source/concept evidence.")
        unassigned.update(remaining_in_group)

    if unassigned:
        progress.log(
            f"{len(unassigned)} mined Type assignment unit(s) unassigned after "
            f"{max_attempts} attempt(s); failing instead of filing questions "
            "under the wrong concept.",
            level="error",
        )
        raise RuntimeError(
            "type embedding failed: unassigned mined Types/case units "
            + ", ".join(sorted(unassigned))
        )

    per_concept = _review_case_unit_hosts_via_api(
        assignment_units=assignment_units,
        per_concept=per_concept,
        concept_payload=concept_payload,
        allowed_cids_by_tid=allowed_cids_by_tid,
        meta=meta,
    )
    for cid, units in per_concept.items():
        host = concept_payload_by_id[cid]
        for unit in units:
            if unit.get("is_activity"):
                continue
            for qid in unit.get("source_question_ids") or []:
                _certify_inventory_host(
                    mined_types,
                    qid,
                    host,
                    basis="type_host_review",
                )

    for cid, tlist in per_concept.items():
        rec = cid_map[cid]
        fragments: list[str] = []
        hub_fragments: list[str] = []
        counter = 0
        for mtype in _collapse_assignment_units_for_render(tlist):
            if mtype.get("is_activity"):
                # Activity procedures are placed from their authoritative
                # inventory item by the dedicated compact Hub pass. Do not
                # duplicate the full source task here or emit it as a Type.
                continue
            body, counter = _mined_type_to_body(mtype, counter)
            if body:
                fragments.append(body)
        details = rec.get("concept_details", "")
        if fragments:
            details = _inject_types(details, " ".join(fragments))
        for hub in hub_fragments:
            details = _append_activity_hub(details, hub)
        rec["concept_details"] = details
    return cr.renumber_types_continuously(records)


def _topic_first_positions(records: list[dict]) -> dict[str, int]:
    """Reading-order position of each canonical source topic."""
    positions: dict[str, int] = {}
    for index, record in enumerate(records):
        key = _topic_comparison_key(record.get("topic") or "")
        if key:
            positions.setdefault(key, index)
    return positions


def _scope_payload_from_records(records: list[dict]) -> dict[str, dict]:
    return {
        f"CONCEPT-{index + 1:04d}": {
            "concept_id": f"CONCEPT-{index + 1:04d}",
            "topic": record.get("topic") or "",
            "concept": record.get("concept_title") or "",
            "is_culmination": cr.is_culmination(
                record.get("concept_title") or ""),
        }
        for index, record in enumerate(records)
    }


def _mined_type_allows_record(
    records: list[dict], mtype: dict, record: dict,
) -> bool:
    """Whether a rendered target obeys the mined unit's source-topic scope."""
    expected_key = _topic_comparison_key(
        mtype.get("topic_match_hint") or "")
    actual_key = _topic_comparison_key(record.get("topic") or "")
    if expected_key and actual_key == expected_key:
        return True
    if (
        not expected_key
        or not actual_key
        or mtype.get("is_activity")
        or _assignment_placement_scope(
            mtype, _scope_payload_from_records(records)
        ) != "cross_topic_synthesis"
        or not cr.is_culmination(record.get("concept_title") or "")
    ):
        return False
    positions = _topic_first_positions(records)
    return (
        expected_key in positions
        and actual_key in positions
        and positions[actual_key] > positions[expected_key]
    )


def _mined_type_topic_violations(
    records: list[dict], mined_types: dict | None,
) -> list[dict]:
    """Return missing or unexpected placements, grouped by normalized Type title."""
    violations: list[dict] = []
    expected_by_title: dict[str, list[dict]] = {}
    for mtype in (mined_types or {}).get("types") or []:
        if mtype.get("is_activity"):
            continue
        topic = (mtype.get("topic_match_hint") or "").strip()
        title = _canonical_mined_type_title(
            mtype, include_definition=False)
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

    scope_payload = _scope_payload_from_records(records)
    for title_key, expected in expected_by_title.items():
        remaining_matches = list(actual_by_title[title_key])
        # Reserve exact-topic rows for ordinary/same-topic Types before a
        # cross-topic Type is allowed to consume its optional source-topic
        # placement. This makes repeated identical Type titles deterministic.
        ordered_expected = sorted(
            expected,
            key=lambda entry: (
                _assignment_placement_scope(
                    entry["mtype"], scope_payload)
                == "cross_topic_synthesis"
            ),
        )
        for entry in ordered_expected:
            mtype = entry["mtype"]
            title = entry["title"]
            topic = entry["topic"]
            match_index = next(
                (
                    index for index, record in enumerate(remaining_matches)
                    if _mined_type_allows_record(records, mtype, record)
                ),
                -1,
            )
            if match_index >= 0:
                remaining_matches.pop(match_index)
                continue

            violations.append({
                "type_id": mtype.get("type_id") or "",
                "type_title": title,
                "expected_topic": topic,
                "actual_topic": "",
                "reason": "missing",
            })

        representative = expected[0]
        for rec in remaining_matches:
            # One mined Type may render on several concepts after Case-scoped
            # assignment. Additional rows are valid when each stays within an
            # allowed topic target for that original Type.
            if any(
                _mined_type_allows_record(records, entry["mtype"], rec)
                for entry in expected
            ):
                continue
            actual = (rec.get("topic") or "").strip()
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


def _expected_normal_type_host_cid(
    unit: dict,
    topic_cids: tuple[str, ...],
    concept_payload_by_id: dict[str, dict],
    *,
    allow_model_hints: bool = True,
) -> str:
    """Resolve a uniquely supported normal host without trusting one hint."""
    normal_cids = tuple(
        cid for cid in topic_cids
        if (
            cid in concept_payload_by_id
            and not concept_payload_by_id[cid].get("is_culmination")
        )
    )
    evidence_cid = _high_confidence_assignment_override(
        unit, topic_cids, concept_payload_by_id)
    if evidence_cid and evidence_cid in normal_cids:
        return evidence_cid
    if not allow_model_hints:
        # A model-authored destination may be useful assignment context, but
        # it must not independently certify terminal per-concept coverage.
        return normal_cids[0] if len(normal_cids) == 1 else ""
    hint = bi.normalize_question_text(
        unit.get("concept_match_hint") or "")
    parent_hint = bi.normalize_question_text(
        unit.get("parent_concept_match_hint") or "")
    exact_matches = [
        cid for cid in normal_cids
        if (
            hint
            and bi.normalize_question_text(
                concept_payload_by_id[cid].get("concept") or "") == hint
        )
        or (
            parent_hint
            and bi.normalize_question_text(
                concept_payload_by_id[cid].get("parent_concept") or "")
            == parent_hint
        )
    ]
    if len(exact_matches) == 1:
        return exact_matches[0]
    if len(normal_cids) == 1:
        return normal_cids[0]
    return ""


def _rendered_type_placement_violations(
    records: list[dict], inventory: dict | None,
    mined_types: dict | None,
) -> list[dict]:
    """Case-scoped mined Types rendered on an ineligible concept host.

    Topic equality alone is insufficient: a normal single-concept task may not
    be parked on that topic's Culmination.  When the mined hint or the same
    deterministic evidence used during assignment identifies one unique normal
    concept, the terminal gate also preserves that exact host.
    """
    if _reviewed_placement_authority_declared(mined_types):
        return []

    units_by_qid = _mined_assignment_units_by_qid(mined_types)

    concept_payload_by_id: dict[str, dict] = {}
    cid_by_index: dict[int, str] = {}
    cids_by_topic: dict[str, list[str]] = {}
    index_by_cid: dict[str, int] = {}
    for index, record in enumerate(records):
        cid = f"CONCEPT-{index + 1:04d}"
        cid_by_index[index] = cid
        index_by_cid[cid] = index
        payload = {
            "concept_id": cid,
            "topic": record.get("topic") or "",
            "parent_concept": record.get("parent_concept") or "",
            "concept": record.get("concept_title") or "",
            "is_culmination": cr.is_culmination(
                record.get("concept_title") or ""),
        }
        concept_payload_by_id[cid] = payload
        cids_by_topic.setdefault(
            _topic_comparison_key(record.get("topic") or ""), []).append(cid)

    violations: list[dict] = []
    for item in (inventory or {}).get("items") or []:
        if not isinstance(item, dict):
            continue
        qid = str(item.get("qid") or "").strip()
        unit = units_by_qid.get(qid)
        if not qid:
            continue
        task_text = _inventory_task_text(item)
        if unit is None:
            placement_scope = "normal"
            if (
                _INVENTORY_CROSS_TOPIC_SYNTHESIS_CUE_RE.search(task_text)
                or (
                    item.get("_chapter_wide_task")
                    and _CROSS_TOPIC_ASSIGNMENT_CUE_RE.search(task_text)
                )
            ):
                placement_scope = "cross_topic_synthesis"
            elif (
                _INVENTORY_SYNTHESIS_CUE_RE.search(task_text)
                or (
                    item.get("_chapter_wide_task")
                    and _MIXED_ASSIGNMENT_CUE_RE.search(task_text)
                )
            ):
                placement_scope = "mixed_synthesis"
            # Older final checkpoints can carry a partial or empty mined
            # taxonomy. The source inventory still proves that an ordinary
            # task/activity may not be parked on a Culmination; infer only that
            # conservative rule and an unambiguous sole normal host.
            unit = {
                "type_id": "",
                "topic_match_hint": item.get("topic_hint") or "",
                "concept_match_hint": "",
                "parent_concept_match_hint": "",
                "placement_scope": placement_scope,
                "is_activity": bool(item.get("_activity_origin")),
                "_source_task_evidence": task_text,
            }
        else:
            # A mined Type may contain a stale or self-consistent wrong routing
            # hint. Terminal host proof must include the authoritative source
            # task and must not let that hint certify itself.
            unit = copy.deepcopy(unit)
            unit["_source_task_evidence"] = task_text
        unit["_source_topic_evidence"] = item.get("topic_hint") or ""
        locations = _rendered_inventory_example_locations(records, item)
        if not locations:
            continue
        scope = _assignment_placement_scope(
            unit, concept_payload_by_id)
        source_topic_key = _topic_comparison_key(
            unit.get("topic_match_hint")
            or item.get("topic_hint")
            or ""
        )
        topic_cids = tuple(cids_by_topic.get(source_topic_key, ()))
        normal_cids = tuple(
            cid for cid in topic_cids
            if not concept_payload_by_id[cid]["is_culmination"]
        )

        expected_cid = ""
        if scope == "normal" or unit.get("is_activity"):
            expected_cid = _expected_normal_type_host_cid(
                unit,
                topic_cids,
                concept_payload_by_id,
                allow_model_hints=False,
            )
        elif scope == "mixed_synthesis":
            expected_cid = _high_confidence_assignment_override(
                unit, topic_cids, concept_payload_by_id)

        for index in locations:
            actual_cid = cid_by_index[index]
            actual = concept_payload_by_id[actual_cid]
            if (
                (scope == "normal" or unit.get("is_activity"))
                and actual["is_culmination"]
            ):
                violations.append({
                    "qid": qid,
                    "type_id": unit.get("type_id") or "",
                    "reason": "normal_scope_on_culmination",
                    "concept": actual["concept"],
                })
                continue
            if expected_cid and actual_cid != expected_cid:
                expected = concept_payload_by_id[expected_cid]
                violations.append({
                    "qid": qid,
                    "type_id": unit.get("type_id") or "",
                    "reason": "high_confidence_wrong_host",
                    "expected_concept": expected["concept"],
                    "actual_concept": actual["concept"],
                })
    return violations


def _normal_concept_type_coverage_violations(
    records: list[dict], inventory: dict | None,
    mined_types: dict | None,
) -> list[dict]:
    """Applicable normal concepts missing their source-backed Type coverage.

    This is deliberately item-driven rather than a blanket "Types on every
    concept" rule. A theory-only concept with no assessable source task remains
    valid. When the authoritative task text (or a structurally unique sole
    normal host) proves one normal concept, however, that concept must contain
    a real Type/Case hierarchy and the exact source item as one of its
    Examples.
    """
    if _reviewed_placement_authority_declared(mined_types):
        return []

    units_by_qid = _mined_assignment_units_by_qid(mined_types)
    concept_payload_by_id: dict[str, dict] = {}
    cids_by_topic: dict[str, list[str]] = {}
    index_by_cid: dict[str, int] = {}
    for index, record in enumerate(records):
        cid = f"CONCEPT-{index + 1:04d}"
        index_by_cid[cid] = index
        payload = {
            "concept_id": cid,
            "topic": record.get("topic") or "",
            "parent_concept": record.get("parent_concept") or "",
            "concept": record.get("concept_title") or "",
            "is_culmination": cr.is_culmination(
                record.get("concept_title") or ""),
        }
        concept_payload_by_id[cid] = payload
        cids_by_topic.setdefault(
            _topic_comparison_key(record.get("topic") or ""), []).append(cid)

    violations: list[dict] = []
    for item in (inventory or {}).get("items") or []:
        if not isinstance(item, dict):
            continue
        if (
            str(item.get("source_kind") or "").strip().lower()
            in _HUB_INVENTORY_KINDS
        ):
            continue
        qid = str(item.get("qid") or "").strip()
        task_text = _inventory_task_text(item)
        if not qid or not task_text:
            continue

        mined_unit = units_by_qid.get(qid)
        if mined_unit is not None:
            unit = copy.deepcopy(mined_unit)
        else:
            placement_scope = "normal"
            if (
                _INVENTORY_CROSS_TOPIC_SYNTHESIS_CUE_RE.search(task_text)
                or (
                    item.get("_chapter_wide_task")
                    and _CROSS_TOPIC_ASSIGNMENT_CUE_RE.search(task_text)
                )
            ):
                placement_scope = "cross_topic_synthesis"
            elif (
                _INVENTORY_SYNTHESIS_CUE_RE.search(task_text)
                or (
                    item.get("_chapter_wide_task")
                    and _MIXED_ASSIGNMENT_CUE_RE.search(task_text)
                )
            ):
                placement_scope = "mixed_synthesis"
            unit = {
                "type_id": "",
                "placement_scope": placement_scope,
                "is_activity": bool(item.get("_activity_origin")),
            }
        # The inventory prompt is authoritative evidence. Routing hints stay
        # available on ``unit`` for diagnostics but are explicitly disabled as
        # a certifying signal below.
        unit["_source_task_evidence"] = task_text
        unit["_source_topic_evidence"] = item.get("topic_hint") or ""
        if (
            _assignment_placement_scope(
                unit, concept_payload_by_id) != "normal"
            and not unit.get("is_activity")
        ):
            continue

        topic_key = _topic_comparison_key(
            item.get("topic_hint")
            or unit.get("topic_match_hint")
            or ""
        )
        topic_cids = tuple(cids_by_topic.get(topic_key, ()))
        # Prove the destination from the authoritative source task alone.
        # Mined Type labels and Case summaries are model output too, so they
        # must not turn a self-consistent but wrong taxonomy into terminal
        # evidence for its own host.
        host_evidence_unit = {
            "type_id": unit.get("type_id") or "",
            "placement_scope": "normal",
            "is_activity": bool(unit.get("is_activity")),
            "_source_task_evidence": task_text,
        }
        expected_cid = _expected_normal_type_host_cid(
            host_evidence_unit,
            topic_cids,
            concept_payload_by_id,
            allow_model_hints=False,
        )
        if not expected_cid:
            continue
        expected_index = index_by_cid[expected_cid]
        expected_record = records[expected_index]
        locations = _rendered_inventory_example_locations(records, item)
        if not _has_meaningful_types(
            expected_record.get("concept_details") or ""
        ):
            reason = "missing_meaningful_types"
        elif expected_index not in locations:
            reason = "source_qid_not_on_expected_host"
        else:
            continue
        violations.append({
            "qid": qid,
            "type_id": unit.get("type_id") or "",
            "reason": reason,
            "expected_concept": (
                concept_payload_by_id[expected_cid]["concept"]
            ),
            "actual_concepts": [
                records[index].get("concept_title") or ""
                for index in locations
            ],
        })
    return violations


def _inventory_topic_type_coverage_violations(
    records: list[dict], inventory: dict | None,
) -> list[dict]:
    """Topics with assessable inventory but no rendered Types on any concept."""
    task_counts: dict[str, dict] = {}
    for item in (inventory or {}).get("items") or []:
        if (
            (item.get("source_kind") or "").strip().lower()
            in _HUB_INVENTORY_KINDS
        ):
            # Pure classroom activities are represented in Activity/Info Hub,
            # not as artificial assessment Types. Assessable questions that
            # originated inside an activity carry a non-Hub source_kind and
            # remain covered by this gate.
            continue
        topic = (item.get("topic_hint") or "").strip()
        key = _topic_comparison_key(topic)
        if not key:
            continue
        entry = task_counts.setdefault(
            key, {"topic": topic, "inventory_items": 0})
        entry["inventory_items"] += 1
    covered = {
        _topic_comparison_key(record.get("topic") or "")
        for record in records
        if _has_meaningful_types(record.get("concept_details", ""))
    }
    return [
        entry for key, entry in task_counts.items()
        if key not in covered
    ]


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
        data = _openai_json(
            system, user, purpose="concept_validation")
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
        "final": {
            "allow_types": True,
            "require_culmination": True,
            "allow_culmination": True,
            "strict_type_hierarchy": True,
            "strict_analysis_section": True,
            "strict_mastery_statement": True,
            "strict_culmination_recap": True,
        },
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
    "merged_description", "rich_text_format", "empty_misconception",
    "empty_error_analysis", "duplicate_misconception",
    "duplicate_error_analysis", "missing_misconception_or_error_analysis",
    "issue_section_order", "noncanonical_issue_label",
    "generic_misconception", "misconception_framing",
    "generic_error_analysis", "error_analysis_framing",
    "issue_section_overlap",
    "analysis_section_format", "missing_misconception",
    "missing_error_analysis", "figure_reference_without_image",
    "figure_reference_image_mismatch", "generic_case_definition",
    "missing_case_definition", "case_without_example",
    "case_question_not_definition", "example_numbering",
    "section_number_in_description", "case_example_semantic_mismatch",
    "description_truncated_clause",
    "verbatim_source_description",
    "missing_type_definition", "generic_type_definition",
    "duplicate_type_definition", "missing_mastery_statement",
    "mastery_statement_format", "mastery_statement_not_substantive",
    "duplicate_mastery_statement", "mastery_marker_outside_description",
    "culmination_recap_format", "culmination_recap_missing_concepts",
}


_DIAGNOSTIC_SECRET_RE = re.compile(
    r"\b(?:sk-(?:proj-)?[A-Za-z0-9_-]{12,}|"
    r"Bearer\s+[A-Za-z0-9._~+/=-]{12,})\b",
    re.IGNORECASE,
)


def _diagnostic_snippet(value, *, limit: int = 180) -> str:
    """Return one safe, bounded line for progress/error diagnostics."""
    text = str(value or "")
    text = "".join(
        " " if unicodedata.category(char).startswith("C") else char
        for char in text
    )
    text = _DIAGNOSTIC_SECRET_RE.sub("[REDACTED]", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        text = text[:max(0, limit - 1)].rstrip() + "…"
    return text


def _validation_error_context(
    records: list[dict], error: dict,
) -> tuple[int, str, str, str]:
    """Resolve the row/title/field/snippet represented by a validator error."""
    row_index = error.get("row_index", -1)
    if not isinstance(row_index, int):
        row_index = -1
    row = records[row_index] if 0 <= row_index < len(records) else {}
    title = _diagnostic_snippet(
        row.get("concept_title") or row.get("concept") or "<unknown>",
        limit=80,
    )
    field = _diagnostic_snippet(error.get("field") or "<unknown>", limit=60)
    snippet = _diagnostic_snippet(row.get(error.get("field", "")), limit=180)
    return row_index, title, field, snippet


def _fatal_errors(report: dict) -> list[dict]:
    return [
        e for e in report.get("errors", [])
        if e.get("code") in _FATAL_CODES
    ]


def _repair_records_via_api(
    records: list[dict], *, meta: dict, stage: str, source_context: str = "",
    max_attempts: int = 2, strict: bool = False,
    allowed_source_examples: tuple[str, ...] | list[str] = (),
) -> list[dict]:
    """Validate rows and ask the repair prompt to fix hard failures."""
    records = _ensure_parent_concepts(records)
    opts = _validation_options(stage)
    for attempt in range(max_attempts + 1):
        if stage == "final":
            # Models sometimes put the learner-analysis label on a newline
            # after Achieving Mastery. This is a deterministic section-boundary
            # defect, not a semantic-content defect. Normalize before every
            # validation attempt, including API-returned rows, so a broad
            # rewrite cannot damage an otherwise source-faithful Description
            # or leave the loop with a newly malformed terminal contract.
            records = cv.ensure_valid_learner_analysis(records)
            records = _ensure_mastery_lines_via_api(
                records, meta=meta, use_api=False)
            records = _canonicalize_concept_rich_text(records)
        report = cv.validate_concept_rows(
            records, **opts,
            allowed_source_examples=allowed_source_examples,
            source_text=source_context)
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
        repair_system = prompts.get_text("concepts.repair.system")
        if any(
            error.get("code") == "rich_text_format"
            for error in hard
        ):
            repair_system += "\n\n" + kr.PROMPT_PREAMBLE
        data = _openai_json(
            repair_system,
            user,
            purpose="concept_validation",
        )
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


_INVENTORY_EXAMPLE_SEGMENT_RE = re.compile(
    r"(\bExamples?(?:\s+0*\d+)?\s*:\s*)(.*?)"
    r"(?=\bExamples?(?:\s+0*\d+)?\s*:|"
    r"\b(?:Case|Type)\s+\d{1,2}:|\s+//\s+|$)",
    re.IGNORECASE | re.DOTALL,
)


def _inventory_source_examples(inventory: dict | None) -> list[str]:
    """Canonical public prompts that are identity-linked to inventory qids."""
    return [
        text
        for item in (inventory or {}).get("items") or []
        if isinstance(item, dict)
        for text in [_inventory_task_text(item)]
        if text
    ]


def _mask_inventory_examples(
    details: str, inventory: dict | None,
) -> tuple[str, dict[str, str]]:
    """Protect exact inventory-owned Examples during destructive cleanup."""
    source_by_key = {
        bi.normalize_question_text(text): text
        for text in _inventory_source_examples(inventory)
    }
    replacements: dict[str, str] = {}

    def replace(match: re.Match) -> str:
        source = source_by_key.get(
            bi.normalize_question_text(match.group(2)))
        if not source:
            return match.group(0)
        token = f"__INVENTORY_SOURCE_EXAMPLE_{len(replacements):04d}__"
        replacements[token] = source
        return match.group(1) + token

    return _INVENTORY_EXAMPLE_SEGMENT_RE.sub(
        replace, details or ""), replacements


def _neutralize_unrepaired_rows(
    records: list[dict], *, inventory: dict | None = None,
) -> list[dict]:
    """Destructively neutralize source artifacts ONLY where GPT repair failed.

    Rows that validate cleanly keep their GPT-authored wording verbatim; the
    deterministic rewriter is a per-row last resort, never a blanket pass.
    Re-runs once if a cleaned row still matches (regex drift / OCR forms).
    """
    allowed_source_examples = _inventory_source_examples(inventory)
    report = cv.validate_concept_rows(
        records, allow_types=True, require_culmination=True,
        allow_culmination=True,
        allowed_source_examples=allowed_source_examples)
    failing = {
        e["row_index"] for e in report["errors"]
        if e.get("severity") == "error" and e.get("row_index", -1) >= 0
        and e.get("code") == "source_artifact"
    }
    out: list[dict] = []
    for i, rec in enumerate(records):
        candidate = dict(rec)
        replacements: dict[str, str] = {}
        if i in failing and candidate.get("concept_details"):
            candidate["concept_details"], replacements = (
                _mask_inventory_examples(
                    candidate["concept_details"], inventory))
        cleaned = concept_cleanup.clean_concept_record(
            candidate, neutralize_artifacts=i in failing)
        for token, source in replacements.items():
            cleaned["concept_details"] = cleaned["concept_details"].replace(
                token, source)
        out.append(cleaned)
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
            out, allow_types=True, require_culmination=True,
            allow_culmination=True,
            allowed_source_examples=allowed_source_examples)
        still = {
            e["row_index"] for e in again["errors"]
            if e.get("severity") == "error" and e.get("row_index", -1) >= 0
            and e.get("code") == "source_artifact"
        }
        if still:
            for idx in still:
                if 0 <= idx < len(out):
                    snip = _artifact_match_snippet(out[idx])
                    candidate = dict(out[idx])
                    candidate["concept_details"], replacements = (
                        _mask_inventory_examples(
                            candidate.get("concept_details", ""), inventory))
                    cleaned = concept_cleanup.clean_concept_record(
                        candidate, neutralize_artifacts=True)
                    for token, source in replacements.items():
                        cleaned["concept_details"] = (
                            cleaned["concept_details"].replace(token, source))
                    out[idx] = cleaned
                    progress.log(
                        f"  re-scrubbed row {idx} after neutralize residual"
                        + (f" ({snip!r})" if snip else "") + ".",
                        level="warning",
                    )
    return out


_TYPE_SPLIT_RE = re.compile(r"(?=\b(?:Miscellaneous\s+)?Type\s+\d{1,2}:)", re.IGNORECASE)
_CASE_SPLIT_RE = re.compile(r"(?=\bCase\s+\d{1,2}:)", re.IGNORECASE)
_EXAMPLE_LINE_RE = re.compile(
    r"\bExamples?(?:\s+0*\d+)?\s*:\s*", re.IGNORECASE)


def _inventory_lookup_texts(inventory: dict | None) -> list[str]:
    """Full teacher-facing task texts from the Question / Task Inventory."""
    out: list[str] = []
    seen: set[str] = set()
    for item in (inventory or {}).get("items") or []:
        if not isinstance(item, dict):
            continue
        source_kind = (item.get("source_kind") or "").strip().lower()
        if source_kind in _HUB_INVENTORY_KINDS:
            continue
        text = _inventory_task_text(item)
        key = _inventory_coverage_key(text)
        if text and key and key not in seen and not cv._example_too_short(text):
            seen.add(key)
            out.append(text)
    return out


def _rendered_type_examples(records: list[dict]) -> list[str]:
    """Extract public Example prompts from rendered Types sections."""
    examples: list[str] = []
    for record in records:
        body = _types_body(record.get("concept_details", ""))
        for chunk in [
            part.strip() for part in _TYPE_SPLIT_RE.split(body)
            if part.strip()
        ]:
            case_parts = [
                part.strip() for part in _CASE_SPLIT_RE.split(chunk)
                if part.strip()
            ]
            if case_parts and not re.match(
                r"^Case\s+\d{1,2}:", case_parts[0], re.IGNORECASE
            ):
                case_parts = case_parts[1:]
            for case_chunk in case_parts:
                match = re.match(
                    r"^Case\s+\d{1,2}:\s*(.*)$",
                    case_chunk,
                    re.IGNORECASE | re.DOTALL,
                )
                case_body = (match.group(1) if match else case_chunk).strip()
                pieces = _EXAMPLE_LINE_RE.split(case_body)
                examples.extend(
                    _strip_leading_source_task_label(piece).strip()
                    for piece in pieces[1:]
                    if piece.strip()
                )
    return examples


def _inventory_coverage_key(text: str) -> str:
    """Normalize only cosmetic cleaner changes for source-Example coverage."""
    value = _inventory_comparison_text(text)
    # The final rich-text cleanup converts Mathpix document/list wrappers into
    # supported public markup (for example, ``\item`` becomes a bullet). Apply
    # that same cosmetic normalization to the authoritative inventory text so
    # an Example restored immediately before cleanup remains covered afterward.
    value = _normalize_common_mathpix_wrappers(value)
    # Adding the required wire-format wrapper around an otherwise identical
    # source formula is a formatting repair, not a change to the source task.
    # Compare the LaTeX body while retaining image tags/URLs and all wording.
    value = kr.unwrap_katex(value)
    # Mathpix roman/lettered list labels may be removed by the inventory task
    # sanitizer while the final rich-text renderer preserves their positions
    # as bullets. These are cosmetic list markers; normalize both forms away
    # while retaining every subpart's wording and order.
    value = re.sub(
        r"(?<!\w)\((?:[ivxlcdm]+|[a-z]|\d+)\)\s*",
        " ",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(r"(?<!\w)\u2022\s*", " ", value)
    value = bi.normalize_question_text(value)
    # ``clean_concept_record`` tidies OCR spacing such as ``21 ,`` and
    # ``. . .``. That must not make an otherwise identical source Example look
    # missing at the deposit boundary.
    value = re.sub(r"\s+([,;:.!?])", r"\1", value)
    return value.replace("…", "...")


def _rendered_inventory_example_counts(
    records: list[dict], expected_keys: set[str],
) -> dict[str, int]:
    """Count exact inventory prompts that occupy rendered Example slots.

    Inventory text is authoritative framing here. Parsing the flat public
    ``Types`` string structurally is ambiguous when a source question itself
    contains ``Type NN:``, ``Case NN:``, or ``Example:``. Match each known
    prompt immediately after an Example marker and require the following text
    to be the next structural marker (or the end of the Types section).
    """
    counts = {key: 0 for key in expected_keys if key}
    for record in records:
        body = _inventory_coverage_key(
            _types_body(record.get("concept_details", "")))
        for marker in _EXAMPLE_LINE_RE.finditer(body):
            suffix = body[marker.end():]
            for key in counts:
                if not suffix.startswith(key):
                    continue
                tail = suffix[len(key):].lstrip()
                if not tail or re.match(
                    r"^(?:(?:Miscellaneous\s+)?Type\s+\d{1,2}:"
                    r"|Case\s+\d{1,2}:|"
                    r"Examples?(?:\s+0*\d+)?\s*:)",
                    tail,
                    re.IGNORECASE,
                ):
                    counts[key] += 1
    return counts


def _rendered_inventory_coverage_defects(
    records: list[dict], inventory: dict | None,
) -> dict:
    """Missing/duplicate inventory prompts in rendered public Examples.

    Only inventory items with a non-empty, placeable prompt participate in the
    exact-coverage contract. Empty or stub-length inventory rows cannot become
    valid Examples, so they must not abort chapter deposit.

    Textbook ``activity`` items live in Activity/Info Hub rather than Types
    Examples, so they are excluded from this Types coverage contract.
    """
    expected_by_qid: dict[str, str] = {}
    for item in (inventory or {}).get("items") or []:
        if not isinstance(item, dict):
            continue
        qid = (item.get("qid") or "").strip()
        if not qid:
            continue
        source_kind = (item.get("source_kind") or "").strip().lower()
        if source_kind in _HUB_INVENTORY_KINDS:
            continue
        text = _inventory_task_text(item)
        key = _inventory_coverage_key(text)
        if not key or cv._example_too_short(text):
            continue
        expected_by_qid[qid] = key
    rendered_counts = _rendered_inventory_example_counts(
        records, set(expected_by_qid.values()))
    defects = {
        "missing": sorted(
            qid for qid, key in expected_by_qid.items()
            if rendered_counts.get(key, 0) == 0
        ),
        "duplicate": sorted(
            qid for qid, key in expected_by_qid.items()
            if rendered_counts.get(key, 0) > 1
        ),
    }
    return defects


def _invalid_inventory_items(inventory: dict | None) -> list[dict]:
    """Inventory rows that cannot participate in an exact public contract.

    Coverage deliberately ignores empty/one-token legacy rows because there is
    no valid Example text to render.  The terminal gate must not silently
    certify an inventory that contains those rows, though: doing so can turn an
    extraction omission into an apparently complete chapter.
    """
    invalid: list[dict] = []
    seen_qids: set[str] = set()
    for index, item in enumerate((inventory or {}).get("items") or []):
        if not isinstance(item, dict):
            invalid.append({
                "index": index,
                "qid": "",
                "reason": "not_an_object",
            })
            continue
        raw_qid = item.get("qid")
        qid = str(raw_qid or "").strip()
        if raw_qid is not None and not isinstance(raw_qid, str):
            invalid.append({
                "index": index,
                "qid": qid,
                "reason": "invalid_qid_type",
            })
        if not qid:
            invalid.append({
                "index": index,
                "qid": "",
                "reason": "missing_qid",
            })
        elif qid in seen_qids:
            invalid.append({
                "index": index,
                "qid": qid,
                "reason": "duplicate_qid",
            })
        else:
            seen_qids.add(qid)

        text = _inventory_task_text(item)
        source_kind = str(item.get("source_kind") or "").strip().lower()
        if not _inventory_coverage_key(text):
            invalid.append({
                "index": index,
                "qid": qid,
                "reason": "empty_task",
            })
        elif (
            source_kind not in _HUB_INVENTORY_KINDS
            and cv._example_too_short(text)
        ):
            invalid.append({
                "index": index,
                "qid": qid,
                "reason": "stub_task",
            })
    return invalid


def _unexpected_rendered_type_examples(
    records: list[dict], inventory: dict | None,
) -> list[dict]:
    """Public Type Examples that are not owned by the source inventory.

    The ordinary coverage gate is intentionally one-way: it proves that every
    source prompt appears, but historically did not reject an additional
    invented or shortened Example.  Final output is closed-world.  Parser
    fragments caused by a literal ``Example:`` inside an authoritative source
    prompt are ignored when they are contained by that exact inventory text.
    """
    expected_keys = {
        _inventory_coverage_key(_inventory_task_text(item))
        for item in (inventory or {}).get("items") or []
        if isinstance(item, dict)
        and (item.get("source_kind") or "").strip().lower()
        not in _HUB_INVENTORY_KINDS
    }
    expected_keys.discard("")
    parser_fragment_allowances: Counter[str] = Counter()
    for item in (inventory or {}).get("items") or []:
        if not isinstance(item, dict):
            continue
        if (
            (item.get("source_kind") or "").strip().lower()
            in _HUB_INVENTORY_KINDS
        ):
            continue
        text = _inventory_task_text(item)
        if len(list(_EXAMPLE_LINE_RE.finditer(text))) == 0:
            continue
        for fragment in _EXAMPLE_LINE_RE.split(text):
            if not fragment.strip():
                continue
            key = _inventory_coverage_key(
                _strip_leading_source_task_label(fragment).strip())
            if key:
                parser_fragment_allowances[key] += 1
    if not expected_keys:
        return [
            {
                "example": _diagnostic_snippet(example),
                "reason": "no_inventory_owner",
            }
            for example in _rendered_type_examples(records)
            if _inventory_coverage_key(example)
        ]

    unexpected: list[dict] = []
    for example in _rendered_type_examples(records):
        key = _inventory_coverage_key(example)
        if not key or key in expected_keys:
            continue
        if parser_fragment_allowances.get(key, 0) > 0:
            # A source question can itself contain the literal word
            # ``Example:``.  The flat wire format has no escaping for that
            # token, so the structural helper exposes the exact pieces on
            # either side of that inner marker. The allowance is a multiset:
            # an extra invented duplicate of a legitimate fragment must still
            # be rejected.
            parser_fragment_allowances[key] -= 1
            continue
        unexpected.append({
            "example": _diagnostic_snippet(example),
            "reason": "not_in_inventory",
        })
    return unexpected


def _rendered_inventory_example_locations(
    records: list[dict], item: dict,
) -> list[int]:
    key = _inventory_coverage_key(_inventory_task_text(item))
    if not key:
        return []
    return [
        index for index, record in enumerate(records)
        if _rendered_inventory_example_counts([record], {key}).get(key, 0)
    ]


def _rendered_inventory_topic_violations(
    records: list[dict], inventory: dict | None,
    mined_types: dict | None = None,
) -> list[dict]:
    """Exact Examples rendered outside their authoritative inventory topic."""
    violations: list[dict] = []
    mined_by_qid: dict[str, list[dict]] = {}
    for mtype in (mined_types or {}).get("types") or []:
        if not isinstance(mtype, dict):
            continue
        for qid in _type_source_qids(mtype):
            mined_by_qid.setdefault(qid, []).append(mtype)
    for item in (inventory or {}).get("items") or []:
        expected_topic = (item.get("topic_hint") or "").strip()
        expected_key = _topic_comparison_key(expected_topic)
        if not expected_key:
            continue
        for index in _rendered_inventory_example_locations(records, item):
            actual_topic = (records[index].get("topic") or "").strip()
            if _topic_comparison_key(actual_topic) == expected_key:
                continue
            qid = (item.get("qid") or "").strip()
            source_scoped_types = []
            for mtype in mined_by_qid.get(qid, []):
                scoped = copy.deepcopy(mtype)
                scoped["_source_task_evidence"] = _inventory_task_text(item)
                scoped["_source_topic_evidence"] = expected_topic
                source_scoped_types.append(scoped)
            if any(
                _mined_type_allows_record(records, mtype, records[index])
                for mtype in source_scoped_types
            ):
                continue
            violations.append({
                "qid": qid,
                "expected_topic": expected_topic,
                "actual_topic": actual_topic,
                "concept": records[index].get("concept_title") or "",
            })
    return violations


def _activity_example_hub_alignment_violations(
    records: list[dict], inventory: dict | None,
) -> list[dict]:
    """Assessable Activity Examples and Hub copies must share one concept row."""
    violations: list[dict] = []
    for item in (inventory or {}).get("items") or []:
        if not item.get("_activity_origin"):
            continue
        example_locations = set(
            _rendered_inventory_example_locations(records, item))
        hub_locations = set(_activity_hub_locations(records, item))
        if example_locations and hub_locations and not (
            example_locations & hub_locations
        ):
            violations.append({
                "qid": (item.get("qid") or "").strip(),
                "example_concepts": [
                    records[index].get("concept_title") or ""
                    for index in sorted(example_locations)
                ],
                "hub_concepts": [
                    records[index].get("concept_title") or ""
                    for index in sorted(hub_locations)
                ],
            })
    return violations


def _placement_certification_violations(
    records: list[dict],
    inventory: dict | None,
    mined_types: dict | None,
    *,
    require_complete: bool = True,
) -> list[dict]:
    """Return qid placements that drifted from their reviewed exact host.

    Absence means the caller is handling a pre-certification/legacy artifact.
    Once the versioned key is declared, malformed entries and host drift fail
    closed. Exact-review boundaries may temporarily allow an uncertified pure
    Activity while the immediately following Hub pass is still pending; late
    checkpoints and terminal/deposit validation always require completeness.
    """
    if (
        not isinstance(mined_types, dict)
        or _PLACEMENT_CERTIFICATIONS_KEY not in mined_types
    ):
        return []
    ledger = _placement_certification_ledger(mined_types)
    if not ledger:
        return [{"qid": "", "reason": "malformed_ledger"}]

    items_by_qid = {
        str(item.get("qid") or "").strip(): item
        for item in (inventory or {}).get("items") or []
        if isinstance(item, dict) and str(item.get("qid") or "").strip()
    }
    hosts = ledger["hosts"]
    violations: list[dict] = []
    for qid in sorted(
        set(hosts) - set(items_by_qid),
        key=lambda value: str(value),
    ):
        violations.append({
            "qid": str(qid),
            "reason": "unknown_certified_qid",
        })

    concept_payload_by_id = _scope_payload_from_records(records)
    index_by_cid = {
        f"CONCEPT-{index + 1:04d}": index
        for index in range(len(records))
    }
    for qid, item in items_by_qid.items():
        expected = hosts.get(qid)
        if expected is None:
            if require_complete:
                violations.append({
                    "qid": qid,
                    "reason": "missing_certification",
                })
            continue
        if not _placement_certification_entry_is_valid(expected):
            violations.append({
                "qid": qid,
                "reason": "malformed_certification",
            })
            continue

        expected_cid = _certified_host_cid(
            mined_types, qid, concept_payload_by_id)
        if not expected_cid:
            matching_hosts = [
                cid
                for cid, payload in concept_payload_by_id.items()
                if (
                    _placement_host_identity(payload)["topic_key"]
                    == expected["topic_key"]
                    and _placement_host_identity(payload)["concept_key"]
                    == expected["concept_key"]
                    and _placement_host_identity(payload)["is_culmination"]
                    == expected["is_culmination"]
                )
            ]
            violations.append({
                "qid": qid,
                "reason": (
                    "certified_host_missing"
                    if not matching_hosts
                    else "certified_host_ambiguous"
                ),
                "expected_topic": expected["topic"],
                "expected_concept": expected["concept"],
            })
            continue

        source_kind = str(
            item.get("source_kind") or "").strip().lower()
        if source_kind in _HUB_INVENTORY_KINDS:
            actual_locations = set(
                _activity_hub_locations(records, item))
        elif item.get("_activity_origin"):
            actual_locations = set(
                _rendered_inventory_example_locations(records, item)
            ) | set(_activity_hub_locations(records, item))
        else:
            actual_locations = set(
                _rendered_inventory_example_locations(records, item))
        if not actual_locations:
            violations.append({
                "qid": qid,
                "reason": "certified_placement_missing",
                "expected_topic": expected["topic"],
                "expected_concept": expected["concept"],
            })
            continue

        expected_index = index_by_cid[expected_cid]
        for actual_index in sorted(actual_locations):
            if actual_index == expected_index:
                continue
            actual = _placement_host_identity(records[actual_index])
            violations.append({
                "qid": qid,
                "reason": "certified_host_drift",
                "expected_topic": expected["topic"],
                "expected_concept": expected["concept"],
                "actual_topic": actual["topic"],
                "actual_concept": actual["concept"],
            })
    return violations


def _hub_inventory_examples_in_types(
    records: list[dict], inventory: dict | None,
) -> set[str]:
    """Pure procedure/project Hub prompts incorrectly rendered as Examples."""
    expected_keys = {
        _inventory_coverage_key(_inventory_task_text(item))
        for item in (inventory or {}).get("items") or []
        if isinstance(item, dict)
        and (item.get("source_kind") or "").strip().lower()
        in _HUB_INVENTORY_KINDS
        and not item.get("_activity_origin")
    }
    expected_keys.discard("")
    counts = _rendered_inventory_example_counts(records, expected_keys)
    return {key for key, count in counts.items() if count > 0}


def _is_source_owned_type_section(label: str) -> bool:
    return (
        (label or "").strip().lower().startswith("type")
        or cr.is_activity_hub_label(label)
    )


def _restore_source_owned_type_sections(
    original: list[dict], candidate: list[dict],
) -> tuple[list[dict], int]:
    """Keep candidate repairs while restoring the known-good Type inventory.

    A rejected repair used to roll every field back to ``original``.  This
    rebuilds rows from the candidate's non-Type fields and sections, while
    retaining the exact source-owned Types/Activity Hub sections and their
    source topic from the known-good snapshot.
    """
    used_candidate_indexes: set[int] = set()
    merged: list[dict] = []
    preserved_repairs = 0

    def find_candidate_index(original_index: int, row: dict) -> int | None:
        exact = [
            index for index, candidate_row in enumerate(candidate)
            if index not in used_candidate_indexes
            and _record_key(candidate_row) == _record_key(row)
        ]
        if len(exact) == 1:
            return exact[0]
        title_key = bi.normalize_question_text(row.get("concept_title", ""))
        title_matches = [
            index for index, candidate_row in enumerate(candidate)
            if index not in used_candidate_indexes
            and bi.normalize_question_text(
                candidate_row.get("concept_title", "")) == title_key
        ]
        if title_key and len(title_matches) == 1:
            return title_matches[0]
        method_ids = _method_anchor_ids(row)
        method_matches = [
            index for index, candidate_row in enumerate(candidate)
            if index not in used_candidate_indexes
            and method_ids
            and bool(method_ids.intersection(
                _method_anchor_ids(candidate_row)))
        ]
        if len(method_matches) == 1:
            return method_matches[0]
        if (
            len(candidate) == len(original)
            and original_index not in used_candidate_indexes
        ):
            return original_index
        return None

    for original_index, original_row in enumerate(original):
        candidate_index = find_candidate_index(original_index, original_row)
        if candidate_index is None:
            merged.append(copy.deepcopy(original_row))
            continue
        used_candidate_indexes.add(candidate_index)
        candidate_row = copy.deepcopy(candidate[candidate_index])
        original_sections = cr.split_sections(
            original_row.get("concept_details", ""))
        protected_sections = [
            (label, content)
            for label, content in original_sections
            if _is_source_owned_type_section(label)
        ]
        candidate_sections = [
            (label, content)
            for label, content in cr.split_sections(
                candidate_row.get("concept_details", ""))
            if not _is_source_owned_type_section(label)
        ]
        if not candidate_sections:
            candidate_sections = [
                (label, content)
                for label, content in original_sections
                if not _is_source_owned_type_section(label)
            ]
        insert_at = next(
            (
                index for index, (label, _) in enumerate(candidate_sections)
                if cr.is_learner_analysis_label(label)
            ),
            len(candidate_sections),
        )
        candidate_row["concept_details"] = cr.join_sections(
            candidate_sections[:insert_at]
            + protected_sections
            + candidate_sections[insert_at:]
        )
        if protected_sections:
            # Topic placement is part of the exact source-inventory contract.
            candidate_row["topic"] = original_row.get("topic", "")
        if candidate_row != original_row:
            preserved_repairs += 1
        merged.append(candidate_row)
    if merged == original:
        return original, 0
    return merged, preserved_repairs


def _exact_type_review_is_safe(
    records: list[dict], inventory: dict | None,
    mined_types: dict | None = None,
) -> bool:
    defects = _rendered_inventory_coverage_defects(records, inventory)
    if defects["missing"] or defects["duplicate"]:
        return False
    if _rendered_inventory_topic_violations(
        records, inventory, mined_types
    ):
        return False
    if _placement_certification_violations(
        records,
        inventory,
        mined_types,
        require_complete=False,
    ):
        return False
    if _activity_example_hub_alignment_violations(records, inventory):
        return False
    return not _hub_inventory_examples_in_types(records, inventory)


def _accept_exact_inventory_type_review(
    original: list[dict], candidate: list[dict], inventory: dict | None,
    mined_types: dict | None = None,
) -> list[dict]:
    """Reject a Types rewrite that breaks inventory section or coverage rules."""
    defects = _rendered_inventory_coverage_defects(candidate, inventory)
    if defects["missing"] or defects["duplicate"]:
        progress.log(
            "Rejected Types rewrite that changed exact inventory coverage: "
            f"{len(defects['missing'])} missing, "
            f"{len(defects['duplicate'])} duplicated Example(s).",
            level="warning",
        )
        restored, preserved = _restore_source_owned_type_sections(
            original, candidate)
        if _exact_type_review_is_safe(restored, inventory, mined_types):
            progress.log(
                "Restored the known-good Types/Activity inventory while "
                f"retaining non-Type repairs on {preserved} row(s).",
                level="warning",
            )
            return restored
        return original
    topic_violations = _rendered_inventory_topic_violations(
        candidate, inventory, mined_types)
    activity_violations = _activity_example_hub_alignment_violations(
        candidate, inventory)
    certification_violations = _placement_certification_violations(
        candidate,
        inventory,
        mined_types,
        require_complete=False,
    )
    if (
        topic_violations
        or activity_violations
        or certification_violations
    ):
        progress.log(
            "Rejected Types rewrite that moved exact inventory Examples: "
            f"{len(topic_violations)} outside their source topic, "
            f"{len(activity_violations)} away from their Activity/Info Hub, "
            f"{len(certification_violations)} away from their certified "
            "semantic host.",
            level="warning",
        )
        restored, preserved = _restore_source_owned_type_sections(
            original, candidate)
        if _exact_type_review_is_safe(restored, inventory, mined_types):
            progress.log(
                "Restored the known-good Types/Activity inventory while "
                f"retaining non-Type repairs on {preserved} row(s).",
                level="warning",
            )
            return restored
        return original
    misplaced_hub_items = _hub_inventory_examples_in_types(candidate, inventory)
    if misplaced_hub_items:
        progress.log(
            "Rejected Types rewrite that placed "
            f"{len(misplaced_hub_items)} Activity/Info Hub item(s) in Examples.",
            level="warning",
        )
        restored, preserved = _restore_source_owned_type_sections(
            original, candidate)
        if _exact_type_review_is_safe(restored, inventory, mined_types):
            progress.log(
                "Restored the known-good Types/Activity inventory while "
                f"retaining non-Type repairs on {preserved} row(s).",
                level="warning",
            )
            return restored
        return original
    return candidate


def _rendered_inventory_keys_present(
    records: list[dict], inventory: dict | None,
) -> set[str]:
    """Normalized inventory prompts already occupying at least one Example slot."""
    expected_keys: set[str] = set()
    for item in (inventory or {}).get("items") or []:
        if not isinstance(item, dict):
            continue
        text = _inventory_task_text(item)
        key = _inventory_coverage_key(text)
        if key and not cv._example_too_short(text):
            expected_keys.add(key)
    if not expected_keys:
        return set()
    counts = _rendered_inventory_example_counts(records, expected_keys)
    return {key for key, count in counts.items() if count > 0}


def _next_rendered_type_number(body: str) -> int:
    nums = [
        int(match.group(1))
        for match in re.finditer(
            r"\b(?:Miscellaneous\s+)?Type\s+(\d{1,2}):", body or "", re.IGNORECASE
        )
    ]
    return (max(nums) if nums else 0) + 1


def _mined_assignment_units_by_qid(
    mined_types: dict | None,
) -> dict[str, dict]:
    """Resolve each qid to its authoritative Case-scoped assignment unit."""
    units: dict[str, dict] = {}
    for mtype in (mined_types or {}).get("types") or []:
        if not isinstance(mtype, dict):
            continue
        for raw_case in mtype.get("case_prompts") or []:
            case_qids = _assignment_case_qids(raw_case)
            if not case_qids:
                continue
            unit = dict(mtype)
            unit["case_prompts"] = [raw_case]
            unit["source_question_ids"] = case_qids
            for qid in case_qids:
                units.setdefault(qid, unit)
        for qid in _type_source_qids(mtype):
            units.setdefault(qid, mtype)
    return units


def _best_record_index_for_inventory_item(
    records: list[dict], item: dict, *, allow_culmination: bool = False,
    mined_type: dict | None = None,
) -> int:
    """Pick the concept row that should host a still-missing inventory Example."""
    if not records:
        return 0
    if mined_type is not None:
        mined_type = copy.deepcopy(mined_type)
        mined_type["_source_task_evidence"] = _inventory_task_text(item)
        mined_type["_source_topic_evidence"] = item.get("topic_hint") or ""
    if item.get("_activity_origin"):
        hub_matches = [
            index for index in _activity_hub_locations(records, item)
            if not cr.is_culmination(
                records[index].get("concept_title") or "")
        ]
        if len(hub_matches) == 1:
            # The Hub was GPT-placed semantically. Keep the assessable Example
            # on that same concept instead of independently guessing again.
            return hub_matches[0]
        expected_topic = _topic_comparison_key(item.get("topic_hint") or "")
        example_matches = [
            index for index in _rendered_inventory_example_locations(
                records, item)
            if not cr.is_culmination(
                records[index].get("concept_title") or "")
            and (
                not expected_topic
                or _topic_comparison_key(records[index].get("topic") or "")
                == expected_topic
            )
        ]
        if len(example_matches) == 1:
            # If GPT omitted or invalidly cross-placed the Hub, keep fallback
            # Hub placement on the exact source Example's existing row.
            return example_matches[0]
    mined_topic = _topic_comparison_key(
        (mined_type or {}).get("topic_match_hint") or "")
    placement_scope = (
        _assignment_placement_scope(
            mined_type, _scope_payload_from_records(records))
        if mined_type is not None else "normal"
    )
    prefer_culmination = (
        mined_type is not None
        and not mined_type.get("is_activity")
        and placement_scope in {"mixed_synthesis", "cross_topic_synthesis"}
    )
    if prefer_culmination:
        allow_culmination = True
    has_eligible_culmination = prefer_culmination and any(
        cr.is_culmination(record.get("concept_title") or "")
        and (
            not mined_topic
            or _mined_type_allows_record(records, mined_type, record)
        )
        for record in records
    )
    mined_title = _canonical_mined_type_title(mined_type)
    mined_title_key = bi.normalize_question_text(mined_title)
    has_rendered_mined_type = bool(mined_title_key) and any(
        (
            not mined_topic
            or _mined_type_allows_record(records, mined_type, record)
        )
        and mined_title_key in bi.normalize_question_text(
            _types_body(record.get("concept_details") or ""))
        for record in records
    )
    topic_hint = _topic_comparison_key(item.get("topic_hint") or "")
    scored: list[tuple[int, int]] = []
    for index, record in enumerate(records):
        if (
            mined_topic
            and not _mined_type_allows_record(records, mined_type, record)
        ):
            continue
        score = 0
        record_topic = _topic_comparison_key(record.get("topic") or "")
        if topic_hint and record_topic == topic_hint:
            score += 10
        title = record.get("concept_title") or ""
        is_culmination = cr.is_culmination(title)
        if is_culmination and not allow_culmination:
            continue
        record_has_mined_type = (
            bool(mined_title_key)
            and mined_title_key in bi.normalize_question_text(
                _types_body(record.get("concept_details") or ""))
        )
        if has_rendered_mined_type and not record_has_mined_type:
            continue
        if (
            not has_rendered_mined_type
            and has_eligible_culmination
            and not is_culmination
        ):
            continue
        if is_culmination:
            score -= 3
        if _has_meaningful_types(record.get("concept_details") or ""):
            score += 2
        if record_has_mined_type:
            score += 100
        scored.append((score, -index))
    if not scored:
        return -1
    scored.sort(reverse=True)
    return -scored[0][1]


def _append_inventory_example_to_record(
    record: dict, text: str, item: dict | None = None,
) -> dict:
    """Append a missing Example under a source-kind pattern, grouping Cases."""
    updated = dict(record)
    details = updated.get("concept_details") or ""
    body = _types_body(details)
    source_kind = (
        ((item or {}).get("source_kind") or "other").strip().lower()
    )
    if source_kind in {
        "worked_example", "solved_example", "exercise", "intext_question",
        "checkpoint_question", "long_answer", "short_answer", "other",
    }:
        title, case_title = _semantic_fallback_wording(item or {}, text)
    else:
        title, case_title = _FALLBACK_TYPE_WORDING.get(
            source_kind, _semantic_fallback_wording(item or {}, text))
    existing = re.search(
        r"(?is)(?P<header>\bType\s+\d{1,2}:\s*"
        + re.escape(title)
        + r")(?P<section>.*?)(?=\bType\s+\d{1,2}:|\Z)",
        body,
    )
    if existing:
        case_numbers = [
            int(number)
            for number in re.findall(
                r"\bCase\s+(\d{1,2}):", existing.group("section"),
                re.IGNORECASE,
            )
        ]
        case_no = (max(case_numbers) if case_numbers else 0) + 1
        addition = (
            f" Case {case_no:02d}: {case_title} "
            f"Example 01: {text.strip()}")
        new_body = (
            body[:existing.end()] + addition + body[existing.end():]
        ).strip()
    else:
        type_no = _next_rendered_type_number(body)
        addition = (
            f"Type {type_no:02d}: {title} "
            f"Case 01: {case_title} Example 01: {text.strip()}"
        )
        new_body = f"{body} {addition}".strip() if body.strip() else addition
    updated["concept_details"] = _inject_types(details, new_body)
    return updated


def _dedupe_rendered_inventory_examples(
    records: list[dict], inventory: dict | None,
) -> tuple[list[dict], int]:
    """Keep the first Exact inventory Example; drop later duplicates."""
    expected_keys = {
        _inventory_coverage_key(_inventory_task_text(item))
        for item in (inventory or {}).get("items") or []
        if isinstance(item, dict)
    }
    expected_keys = {key for key in expected_keys if key}
    if not expected_keys:
        return records, 0

    seen: set[str] = set()
    removed = 0
    out: list[dict] = []
    for record in records:
        rec = dict(record)
        details = rec.get("concept_details") or ""
        sections = cr.split_sections(details)
        types_idx = next(
            (
                i for i, (label, _) in enumerate(sections)
                if label.strip().lower().startswith("type")
            ),
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
            if re.match(r"^Case\s+\d{1,2}:", type_header, re.IGNORECASE):
                type_header = "Type 01: Assessment pattern"
            else:
                case_parts = case_parts[1:]
            cases: list[tuple[str, list[str]]] = []
            for case_chunk in case_parts:
                match = re.match(
                    r"^Case\s+\d{1,2}:\s*(.*)$",
                    case_chunk,
                    re.IGNORECASE | re.DOTALL,
                )
                case_body = (match.group(1) if match else case_chunk).strip()
                pieces = _EXAMPLE_LINE_RE.split(case_body)
                case_title = (pieces[0] or "").strip() or "Source-based question"
                examples = [piece.strip() for piece in pieces[1:] if piece.strip()]
                kept: list[str] = []
                for example in examples:
                    key = _inventory_coverage_key(example)
                    if key in expected_keys:
                        if key in seen:
                            removed += 1
                            changed = True
                            continue
                        seen.add(key)
                    kept.append(example)
                if kept:
                    cases.append((case_title, kept))
                else:
                    # A deduplicated Case with no remaining Example violates
                    # the public Case -> Example hierarchy, so drop it rather
                    # than leave an empty shell behind.
                    changed = True
            if cases:
                header = type_header.strip()
                if not re.match(
                    r"^(?:Miscellaneous\s+)?Type\s+\d{1,2}:",
                    header,
                    re.IGNORECASE,
                ):
                    header = (
                        f"Type 01: {header}" if header
                        else "Type 01: Assessment pattern"
                    )
                rebuilt_types.append((header, cases))
        if changed:
            new_body = _rebuild_types_body(rebuilt_types)
            if new_body.strip():
                sections[types_idx] = (label, new_body)
            else:
                sections.pop(types_idx)
            rec["concept_details"] = cr.join_sections(sections)
        out.append(rec)
    return out, removed


def _align_activity_examples_with_hubs(
    records: list[dict], inventory: dict | None,
) -> list[dict]:
    """Move each assessable Activity Example to its exact GPT-selected Hub row."""
    out = [dict(record) for record in records]
    moved = 0
    for item in (inventory or {}).get("items") or []:
        if not isinstance(item, dict) or not item.get("_activity_origin"):
            continue
        text = _inventory_task_text(item)
        key = _inventory_coverage_key(text)
        if not text or not key:
            continue
        example_locations = _rendered_inventory_example_locations(out, item)
        hub_locations = _activity_hub_locations(out, item)
        if len(hub_locations) != 1 or not example_locations:
            continue
        target = hub_locations[0]
        if example_locations == [target]:
            continue
        if cr.is_culmination(out[target].get("concept_title") or ""):
            continue
        expected_topic = _topic_comparison_key(item.get("topic_hint") or "")
        if (
            expected_topic
            and _topic_comparison_key(out[target].get("topic") or "")
            != expected_topic
        ):
            continue

        # Add the authoritative prompt to the selected row first, then run the
        # exact-key duplicate remover with that row first. This preserves the
        # GPT-selected Hub concept while removing only byte-equivalent inventory
        # Examples from their old rows; no semantic/fuzzy matching is involved.
        candidate = [dict(record) for record in out]
        if target not in example_locations:
            candidate[target] = _append_inventory_example_to_record(
                candidate[target], text, item)
        order = [target] + [
            index for index in range(len(candidate)) if index != target
        ]
        ordered, _removed = _dedupe_rendered_inventory_examples(
            [candidate[index] for index in order],
            {"items": [item]},
        )
        rebuilt = [dict(record) for record in candidate]
        for position, index in enumerate(order):
            rebuilt[index] = ordered[position]
        if _rendered_inventory_example_locations(rebuilt, item) != [target]:
            continue
        out = rebuilt
        moved += 1
    if moved:
        progress.log(
            f"Aligned {moved} assessable Activity Example(s) with their "
            "GPT-selected Activity/Info Hub concept.",
            level="success",
        )
    return cr.renumber_types_continuously(out)


def _relocate_chapter_wide_examples_from_culminations(
    records: list[dict], inventory: dict | None,
    mined_types: dict | None = None,
) -> list[dict]:
    """Keep ordinary chapter-review questions off an unrelated Culmination.

    Chapter-end task lists are initially scoped to the whole chapter, so an
    early Type assignment can temporarily place them under one Culmination.
    Once a semantic topic hint exists, normal (non-synthesis) tasks belong on
    a normal concept in that topic.  Add the authoritative Example there
    first, then exact-dedupe with the target first so the earlier Culmination
    copy cannot win merely because it occurs first in chapter order.
    """
    out = [dict(record) for record in records]
    mined_by_qid = _mined_assignment_units_by_qid(mined_types)
    moved = 0
    for item in (inventory or {}).get("items") or []:
        if not isinstance(item, dict) or not item.get("_chapter_wide_task"):
            continue
        if (item.get("source_kind") or "").strip().lower() in _HUB_INVENTORY_KINDS:
            continue
        qid = (item.get("qid") or "").strip()
        mined_type = mined_by_qid.get(qid)
        task_text = _inventory_task_text(item)
        if mined_type is not None:
            mined_type = copy.deepcopy(mined_type)
            mined_type["_source_task_evidence"] = task_text
            mined_type["_source_topic_evidence"] = (
                item.get("topic_hint") or "")
        scope = (
            _assignment_placement_scope(
                mined_type, _scope_payload_from_records(out))
            if mined_type is not None
            else ""
        )
        if (
            scope in {"mixed_synthesis", "cross_topic_synthesis"}
            or (
                not scope
                and (
                    _MIXED_ASSIGNMENT_CUE_RE.search(task_text)
                    or _CROSS_TOPIC_ASSIGNMENT_CUE_RE.search(task_text)
                )
            )
        ):
            continue
        locations = _rendered_inventory_example_locations(out, item)
        if not any(
            cr.is_culmination(out[index].get("concept_title") or "")
            for index in locations
        ):
            continue
        certified_cid = _certified_host_cid(
            mined_types,
            qid,
            _scope_payload_from_records(out),
        )
        target = (
            int(certified_cid.rsplit("-", 1)[-1]) - 1
            if certified_cid else
            _best_record_index_for_inventory_item(
                out, item, allow_culmination=False, mined_type=None)
        )
        if target < 0 or target >= len(out):
            continue
        if cr.is_culmination(out[target].get("concept_title") or ""):
            continue
        candidate = [dict(record) for record in out]
        if target not in locations:
            candidate[target] = _append_inventory_example_to_record(
                candidate[target], task_text, item)
        order = [target] + [
            index for index in range(len(candidate)) if index != target
        ]
        ordered, _removed = _dedupe_rendered_inventory_examples(
            [candidate[index] for index in order], {"items": [item]})
        rebuilt = [dict(record) for record in candidate]
        for position, index in enumerate(order):
            rebuilt[index] = ordered[position]
        if _rendered_inventory_example_locations(rebuilt, item) != [target]:
            continue
        out = rebuilt
        moved += 1
    if moved:
        progress.log(
            f"Relocated {moved} chapter-wide Example(s) from Culmination "
            "to their assigned normal concept.",
            level="success",
        )
    return cr.renumber_types_continuously(out)


def _repair_rendered_inventory_coverage(
    records: list[dict], inventory: dict | None,
    mined_types: dict | None = None,
) -> list[dict]:
    """Restore exact-once inventory Example coverage after salvage/API drift."""
    if not records or not (inventory or {}).get("items"):
        return records

    defects = _rendered_inventory_coverage_defects(records, inventory)
    if not defects["missing"] and not defects["duplicate"]:
        return records

    out, removed = _dedupe_rendered_inventory_examples(records, inventory)
    items_by_qid = {
        (item.get("qid") or "").strip(): item
        for item in (inventory or {}).get("items") or []
        if isinstance(item, dict) and (item.get("qid") or "").strip()
    }
    mined_by_qid = _mined_assignment_units_by_qid(mined_types)
    concept_payload = _scope_payload_from_records(out)

    def placement_index(qid: str, item: dict) -> int:
        certified_cid = _certified_host_cid(
            mined_types, qid, concept_payload)
        if certified_cid:
            return int(certified_cid.rsplit("-", 1)[-1]) - 1
        return _best_record_index_for_inventory_item(
            out, item, mined_type=mined_by_qid.get(qid))

    # Coverage is keyed by normalized prompt text. Multiple qids can share one
    # key; place each unique missing prompt once, then mark the key covered so
    # sibling qids do not double-append and trip the hard duplicate gate.
    covered_keys = _rendered_inventory_keys_present(out, inventory)
    placed = 0
    skipped_unplaceable = 0
    still_missing = _rendered_inventory_coverage_defects(out, inventory)["missing"]
    for qid in still_missing:
        item = items_by_qid.get(qid)
        if not item:
            skipped_unplaceable += 1
            continue
        text = _inventory_task_text(item)
        key = _inventory_coverage_key(text)
        # Inventory wording is authoritative for coverage placement. Do not
        # refuse a still-missing source question merely because the validator
        # would prefer a longer Example — leaving it missing aborts deposit.
        if not text or not key:
            skipped_unplaceable += 1
            continue
        if key in covered_keys:
            continue
        index = placement_index(qid, item)
        if index < 0 or index >= len(out):
            skipped_unplaceable += 1
            continue
        out[index] = _append_inventory_example_to_record(out[index], text, item)
        covered_keys.add(key)
        placed += 1

    # Second pass: force-attach any placeable key still absent while preserving
    # the same mined assignment scope used by the first pass.
    still_missing = _rendered_inventory_coverage_defects(out, inventory)["missing"]
    for qid in still_missing:
        item = items_by_qid.get(qid)
        if not item:
            continue
        text = _inventory_task_text(item)
        key = _inventory_coverage_key(text)
        if not text or not key or key in covered_keys:
            continue
        index = placement_index(qid, item)
        if index < 0 or index >= len(out):
            continue
        out[index] = _append_inventory_example_to_record(
            out[index], text, item)
        covered_keys.add(key)
        placed += 1

    repaired = _rendered_inventory_coverage_defects(out, inventory)
    progress.log(
        "Repaired rendered inventory coverage: "
        f"removed {removed} duplicate Example(s), "
        f"placed {placed} missing Example(s)"
        + (
            f", skipped {skipped_unplaceable} unplaceable"
            if skipped_unplaceable else ""
        )
        + f"; now {len(repaired['missing'])} missing / "
        f"{len(repaired['duplicate'])} duplicate.",
        level=(
            "warning"
            if repaired["missing"] or repaired["duplicate"]
            else "success"
        ),
    )
    return out


def _enforce_rendered_inventory_coverage(
    records: list[dict], inventory: dict | None,
    mined_types: dict | None = None,
) -> list[dict]:
    """Repair coverage and hard-fail on any residual placeable defect."""
    out = _repair_rendered_inventory_coverage(
        records, inventory, mined_types)
    defects = _rendered_inventory_coverage_defects(out, inventory)
    if defects["duplicate"]:
        out, _removed = _dedupe_rendered_inventory_examples(out, inventory)
        defects = _rendered_inventory_coverage_defects(out, inventory)
    if defects["duplicate"]:
        raise RuntimeError(
            "rendered Types failed exact inventory coverage: "
            f"{len(defects['missing'])} missing, "
            f"{len(defects['duplicate'])} duplicate "
            "source question(s)"
        )
    if defects["missing"]:
        # Last-chance force place before enforcing the exact-once contract.
        out = _repair_rendered_inventory_coverage(
            out, inventory, mined_types)
        defects = _rendered_inventory_coverage_defects(out, inventory)
        if defects["duplicate"]:
            out, _removed = _dedupe_rendered_inventory_examples(out, inventory)
            defects = _rendered_inventory_coverage_defects(out, inventory)
        if defects["duplicate"]:
            raise RuntimeError(
                "rendered Types failed exact inventory coverage: "
                f"{len(defects['missing'])} missing, "
                f"{len(defects['duplicate'])} duplicate "
                "source question(s)"
            )
        if defects["missing"]:
            raise RuntimeError(
                "rendered Types failed exact inventory coverage with "
                f"{len(defects['missing'])} still-missing placeable "
                f"source question(s): {', '.join(defects['missing'][:8])}"
                + ("…" if len(defects["missing"]) > 8 else "")
                + ".",
            )
    # Coverage may already be exact while later merge/refinement passes have
    # moved an assessable Activity Example away from its Hub. Reassert that
    # identity-based placement invariant at this terminal repair boundary.
    out = _align_activity_examples_with_hubs(out, inventory)
    out = _relocate_chapter_wide_examples_from_culminations(
        out, inventory, mined_types)
    terminal_defects = _rendered_inventory_coverage_defects(out, inventory)
    if terminal_defects["missing"] or terminal_defects["duplicate"]:
        raise RuntimeError(
            "rendered Types failed exact inventory coverage after final "
            "placement: "
            f"{len(terminal_defects['missing'])} missing, "
            f"{len(terminal_defects['duplicate'])} duplicate source "
            "question(s)"
        )
    return out


def _rebuild_types_after_final_placement_drift(
    records: list[dict], inventory: dict | None, mined_types: dict | None,
    *, meta: GenerationMetadata,
) -> list[dict]:
    """Re-run ID-constrained GPT assignment on the final concept rows.

    Row merges and method-anchor restoration can preserve exact Example coverage
    while moving a Type away from its source topic. Rebuilding only the Types
    sections retains the final concept map and the mined taxonomy, while letting
    the semantic assignment pass choose again from its proven topic-scoped IDs.
    """
    rebuilt = _strip_types_from_records(copy.deepcopy(records))
    rebuilt = _assign_mined_types_via_api(
        rebuilt, meta=meta, mined_types=mined_types)
    rebuilt = _populate_activity_hubs_via_api(
        rebuilt,
        inventory,
        meta=meta,
        mined_types=mined_types,
    )
    # Reassignment re-renders the original mined wording after the ordinary
    # terminal cleanup boundary. Apply the same source-artifact and short
    # Example cleanup before rechecking coverage and placement.
    rebuilt = _salvage_short_case_examples(rebuilt, inventory=inventory)
    rebuilt = _neutralize_unrepaired_rows(rebuilt, inventory=inventory)
    return _enforce_rendered_inventory_coverage(
        rebuilt, inventory, mined_types)


def _normalize_activity_hubs_at_final_boundary(
    records: list[dict],
    inventory: dict | None,
    mined_types: dict | None,
    *,
    meta: GenerationMetadata | None = None,
) -> list[dict]:
    """Resolve certified-host drift before rebuilding canonical Hub notes.

    Late cleanup may rename or merge a certified normal concept. Hub
    normalization intentionally fails closed when that exact host no longer
    resolves, so fresh generation must rebuild the ID-constrained placements
    first. Restored final checkpoints pass no ``meta`` and therefore raise;
    their caller discards only the stale 98% stage and resumes from its
    preceding checkpoint.
    """
    certification_violations = _placement_certification_violations(
        records, inventory, mined_types)
    if certification_violations:
        if meta is None:
            raise RuntimeError(
                "saved final checkpoint contains unresolved certified "
                "inventory hosts"
            )
        progress.log(
            "Final cleanup changed a certified inventory host; rebuilding "
            "Types and Activity/Info Hubs before normalization.",
            level="warning",
        )
        records = _rebuild_types_after_final_placement_drift(
            records, inventory, mined_types, meta=meta)
    return _normalize_activity_hubs_from_inventory(
        records, inventory, mined_types)


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
            for example_i, example in enumerate(examples, start=1):
                parts.append(
                    f"Example {example_i:02d}: {example.strip()}")
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
    inventory_texts_by_topic: dict[str, list[str]] = {}
    unscoped_inventory_texts: list[str] = []
    for item in (inventory or {}).get("items") or []:
        if not isinstance(item, dict):
            continue
        text = _inventory_task_text(item)
        if not text or text not in inventory_texts:
            continue
        topic_key = _topic_comparison_key(item.get("topic_hint") or "")
        if topic_key:
            inventory_texts_by_topic.setdefault(topic_key, []).append(text)
        else:
            unscoped_inventory_texts.append(text)
    # Never expand a stub into an inventory prompt already rendered elsewhere;
    # that creates the exact missing+duplicate coverage failure seen when
    # short Case Examples fuzzy-match already-placed source questions.
    used: set[str] = set(_rendered_inventory_keys_present(records, inventory))
    expanded = 0
    dropped = 0
    out: list[dict] = []
    for rec in records:
        rec = dict(rec)
        record_topic_key = _topic_comparison_key(rec.get("topic") or "")
        topic_inventory_texts = inventory_texts_by_topic.get(record_topic_key)
        scoped_inventory_texts = (
            list(dict.fromkeys(
                [*(topic_inventory_texts or []), *unscoped_inventory_texts]))
            if topic_inventory_texts
            else inventory_texts
        )
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
                            case_title, scoped_inventory_texts, used=used,
                            context=match_context,
                        )
                        if replacement:
                            examples = [replacement]
                            _, case_title = _semantic_fallback_wording(
                                {}, replacement)
                            expanded += 1
                            changed = True
                        else:
                            dropped += 1
                            changed = True
                            continue
                    else:
                        examples = [case_title]
                        _, case_title = _semantic_fallback_wording(
                            {}, examples[0])
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
                        ex, scoped_inventory_texts, used=used,
                        context=match_context,
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
                    if _case_title_needs_definition(
                        case_title,
                        [{"example_prompt": example} for example in new_examples],
                    ):
                        _, case_title = _semantic_fallback_wording(
                            {}, new_examples[0])
                        changed = True
                    cases.append((case_title, new_examples))
                elif case_title:
                    # A public Case without a usable Example violates the
                    # hierarchy. Drop it; coverage repair can restore a
                    # source-owned Example when one exists.
                    dropped += 1
                    changed = True
            if cases:
                # Keep the original Type NN: title from the header line.
                header = type_header.strip()
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


def _validate_final_or_raise(
    records: list[dict], *, stage: str = "final",
    inventory: dict | None = None,
    mined_types: dict | None = None,
    method_anchors: list[dict] | None = None,
    source_text: str = "",
) -> dict:
    report = cv.validate_concept_rows(
        records, allow_types=True, require_culmination=True,
        allow_culmination=True,
        allowed_source_examples=_inventory_source_examples(inventory),
        strict_type_hierarchy=True,
        strict_analysis_section=True,
        strict_mastery_statement=True,
        strict_culmination_recap=True,
        source_text=source_text,
    )
    fatal = _fatal_errors(report)
    progress.log(
        f"{stage}: final validation found {len(fatal)} fatal error(s), "
        f"{report['summary'].get('warnings', 0)} warning(s).")
    if fatal:
        for error in fatal:
            row_index, title, field, snippet = _validation_error_context(
                records, error)
            progress.log(
                "  fatal validation error: "
                f"row_index={row_index}; concept={title!r}; field={field!r}; "
                f"code={_diagnostic_snippet(error.get('code'), limit=60)!r}; "
                f"message={_diagnostic_snippet(error.get('message'))!r}; "
                f"snippet={snippet!r}",
                level="error",
            )
        codes = ", ".join(sorted({e["code"] for e in fatal}))
        first_index, first_title, first_field, _ = _validation_error_context(
            records, fatal[0])
        raise RuntimeError(
            f"{stage} validation failed: {codes}; first at "
            f"row_index={first_index}, concept={first_title!r}, "
            f"field={first_field!r}"
        )
    invalid_inventory = (
        _invalid_inventory_items(inventory)
        if inventory is not None else []
    )
    if invalid_inventory:
        reasons = ", ".join(
            f"{item.get('qid') or 'row-' + str(item.get('index'))}:"
            f"{item['reason']}"
            for item in invalid_inventory[:10]
        )
        progress.log(
            f"{stage}: source inventory contains "
            f"{len(invalid_inventory)} invalid row(s): {reasons}.",
            level="error",
        )
        raise RuntimeError(
            f"{stage} validation failed: invalid_source_inventory; "
            f"{len(invalid_inventory)} invalid row(s)"
        )
    coverage_defects = (
        _rendered_inventory_coverage_defects(records, inventory)
        if inventory is not None
        else {"missing": [], "duplicate": []}
    )
    if coverage_defects["missing"] or coverage_defects["duplicate"]:
        progress.log(
            f"{stage}: exact source inventory coverage failed with "
            f"{len(coverage_defects['missing'])} missing and "
            f"{len(coverage_defects['duplicate'])} duplicate Example(s).",
            level="error",
        )
        raise RuntimeError(
            f"{stage} validation failed: exact_inventory_coverage; "
            f"{len(coverage_defects['missing'])} missing, "
            f"{len(coverage_defects['duplicate'])} duplicate source "
            "question(s)"
        )
    semantic_defects = (
        {
            "unexpected_examples": _unexpected_rendered_type_examples(
                records, inventory),
            "topic_placements": _rendered_inventory_topic_violations(
                records, inventory, mined_types),
            "type_hosts": _rendered_type_placement_violations(
                records, inventory, mined_types),
            "certified_hosts": _placement_certification_violations(
                records, inventory, mined_types),
            "concept_type_coverage": (
                _normal_concept_type_coverage_violations(
                    records, inventory, mined_types)
            ),
            "mined_types": _mined_type_topic_violations(
                records, mined_types),
            "uncovered_topics": _inventory_topic_type_coverage_violations(
                records, inventory),
            "activity_alignment": _activity_example_hub_alignment_violations(
                records, inventory),
            "hub_contract": _hub_inventory_contract_violations(
                records, inventory),
            "hub_items_in_types": sorted(
                _hub_inventory_examples_in_types(records, inventory)),
        }
        if inventory is not None
        else {}
    )
    nonempty_semantic = {
        name: defects
        for name, defects in semantic_defects.items()
        if defects
    }
    if nonempty_semantic:
        summary = ", ".join(
            f"{name}={len(defects)}"
            for name, defects in nonempty_semantic.items()
        )
        progress.log(
            f"{stage}: closed source-inventory validation failed "
            f"({summary}).",
            level="error",
        )
        raise RuntimeError(
            f"{stage} validation failed: source_inventory_semantics; "
            + summary
        )
    missing_method_anchors = [
        anchor for anchor in (method_anchors or [])
        if (
            not _method_anchor_tagged_in_topic(
                records,
                str(anchor.get("anchor_id") or ""),
                anchor.get("topic_hint", ""),
            )
            or not _method_anchor_covered(records, anchor)
        )
    ]
    if missing_method_anchors:
        anchor_ids = ", ".join(
            str(anchor.get("anchor_id") or "")
            for anchor in missing_method_anchors[:10]
        )
        progress.log(
            f"{stage}: mandatory method anchors are missing: {anchor_ids}.",
            level="error",
        )
        raise RuntimeError(
            f"{stage} validation failed: method_anchor_coverage; "
            f"{len(missing_method_anchors)} missing anchor(s)"
        )
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
        data = _openai_json(system, user, purpose="concept_detailing")
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
    assignment (``_assign_mined_types_via_api``): the model maps each legacy
    single-Type or case-scoped internal ``type_id`` to a concept and we join by
    exact IDs, guaranteeing every refined Case is embedded without any
    regex/word matching. When no mined Types exist (e.g. no source/inventory),
    the model authors Types per topic.
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
        before_repair = merged
        repaired = _repair_records_via_api(
            merged, meta=meta, stage="types", source_context=mmd_text,
            allowed_source_examples=_inventory_source_examples(
                question_task_inventory))
        repaired = _accept_topic_safe_type_review(
            before_repair, repaired, mined_types)
        merged = _accept_exact_inventory_type_review(
            before_repair, repaired, question_task_inventory, mined_types)
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
        data = _openai_json(system, user, purpose="concept_mapping")
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
        # useful Misconceptions or Error Analysis from the incoming record, and
        # take only the Types body returned by the API.
        types_body = _types_body(updated.get("concept_details", ""))
        if types_body:
            rec = dict(rec)
            rec["concept_details"] = _inject_types(rec.get("concept_details", ""), types_body)
        merged.append(rec)
    merged = _repair_records_via_api(
        merged, meta=meta, stage="types", source_context=mmd_text,
        allowed_source_examples=_inventory_source_examples(
            question_task_inventory))
    merged = _review_type_concept_alignment_via_api(
        merged,
        meta=meta,
        question_task_inventory=question_task_inventory,
        mined_types=mined_types,
        source_context=mmd_text,
    )
    merged = _repair_records_via_api(
        merged, meta=meta, stage="types", source_context=mmd_text,
        allowed_source_examples=_inventory_source_examples(
            question_task_inventory))
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


_GENERIC_SKELETON_FAMILY_RE = re.compile(
    r"^(?:general|chapter|concepts?|content|misc(?:ellaneous)?|"
    r"introduction|overview|summary|recap|culmination)$",
    re.IGNORECASE,
)


def _skeleton_family_labels(records: list[dict]) -> list[str]:
    """Return distinct source-backed parent families in reading order.

    A headingless chapter still has pedagogical branches.  Skeleton extraction
    expresses those branches through ``parent_concept`` even when every row has
    the selected chapter title as its topic.  Treating topic count alone as the
    compaction floor can therefore collapse several independently teachable
    domains into one broad row.
    """
    labels: list[str] = []
    seen: set[tuple[str, str]] = set()
    for record in records:
        topic = str(record.get("topic") or "").strip()
        parent = str(record.get("parent_concept") or "").strip()
        parent_key = _topic_comparison_key(parent)
        topic_key = _topic_comparison_key(topic)
        if (
            not parent_key
            or parent_key == topic_key
            or _GENERIC_SKELETON_FAMILY_RE.fullmatch(parent_key)
        ):
            continue
        key = (topic_key, parent_key)
        if key in seen:
            continue
        seen.add(key)
        labels.append(parent)
    return labels


def _canonicalize_target_bounds(records: list[dict]) -> tuple[int, int]:
    """Return compact-but-not-collapsed row-count bounds for a chapter map.

    Distinct parent families are structural evidence, especially for a
    headingless source whose selected chapter title is the only legal topic.
    Canonicalization may merge aliases and narrow fragments inside a family,
    but it must retain room for at least one coherent concept per family.
    """
    if not records:
        return 0, 0
    topics = {
        bi.normalize_question_text(r.get("topic", ""))
        for r in records
        if (r.get("topic") or "").strip()
    }
    topic_count = max(1, len(topics))
    family_count = len(_skeleton_family_labels(records))
    structural_floor = min(
        len(records),
        _CANONICALIZE_MAX_CHAPTER_ROWS,
        family_count,
    )
    min_keep = max(
        _CANONICALIZE_MIN_CHAPTER_ROWS,
        topic_count * _CANONICALIZE_MIN_PER_TOPIC,
        structural_floor,
    )
    max_keep = max(
        12,
        min(
            _CANONICALIZE_MAX_CHAPTER_ROWS,
            max(
                topic_count * _CANONICALIZE_MAX_PER_TOPIC,
                structural_floor,
            ),
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
    min_keep, max_keep = _canonicalize_target_bounds(records)
    family_labels = _skeleton_family_labels(records)
    family_block = ""
    if family_labels:
        family_block = (
            "\nMUST-PRESERVE SOURCE-BACKED PARENT FAMILIES "
            "(keep at least one coherent concept for each; merge only aliases "
            "or near-duplicates within a family):\n- "
            + "\n- ".join(family_labels)
            + "\n"
        )
    user = (
        _metadata_block(meta)
        + family_block
        + f"\nDraft skeleton map ({len(records)} rows):\n"
        + payload
    )
    progress.log(f"Canonicalizing {len(records)} skeleton concepts via API pass.")
    data = _openai_json(system, user, purpose="concept_mapping")
    out = _concept_rows_to_records(data)
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
            "topic and every MUST-PRESERVE SOURCE-BACKED PARENT FAMILY, but "
            "still merge duplicates, examples, cases, and narrow fragments. "
            "Never combine disjoint subject domains merely to reach a numeric "
            f"limit. Return roughly {min_keep}-{max_keep} rows."
        )
        retry_data = _openai_json(
            system, retry_user, purpose="concept_validation")
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
            "their parent teaching concepts. Preserve all main objectives, "
            "topic order, and every MUST-PRESERVE SOURCE-BACKED PARENT FAMILY. "
            "Never combine disjoint subject domains into one row. Return at "
            f"most {max_keep} rows and at least {min_keep} rows."
        )
        retry_data = _openai_json(
            system, retry_user, purpose="concept_validation")
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


_QUESTION_GROUNDED_EVIDENCE_RE = re.compile(
    r"\b(?:examples?|exercises?|ex)\s*(?:\d|[ivxlcdm]+\b)",
    re.IGNORECASE,
)
_NON_DURABLE_TASK_CONCEPT_RE = re.compile(
    r"\b(?:advanced|challenge|challenging|miscellaneous|unknown[-\s]+"
    r"(?:value|quantity|term)|harder\s+problems?)\b",
    re.IGNORECASE,
)


def _question_grounded_fragmentation_topics(
    records: list[dict], *, minimum_rows: int = 3,
) -> set[str]:
    """Topics with several non-method concepts grounded mainly in tasks."""
    counts: dict[str, int] = {}
    for record in records:
        if _method_anchor_ids(record):
            continue
        if not _QUESTION_GROUNDED_EVIDENCE_RE.search(
            record.get("source_evidence") or ""
        ):
            continue
        key = _topic_comparison_key(record.get("topic") or "")
        if key:
            counts[key] = counts.get(key, 0) + 1
    return {key for key, count in counts.items() if count >= minimum_rows}


def _formula_family_fragments(formula: str) -> set[str]:
    normalized = _normalize_math_evidence(formula).replace("&", "")
    if len(normalized) < 8:
        return set()
    fragments = {normalized}
    fragments.update(
        part for part in normalized.split("=") if len(part) >= 8)
    return fragments


def _method_formula_family_groups(
    records: list[dict], method_anchors: list[dict] | None,
) -> list[list[str]]:
    """Anchor-ID groups whose required formula expressions overlap."""
    formulae_by_id = {
        str(anchor.get("anchor_id") or "").upper(): {
            fragment
            for formula in anchor.get("required_formulas") or []
            for fragment in _formula_family_fragments(formula)
        }
        for anchor in method_anchors or []
    }
    indexed_formulae: list[tuple[int, set[str], set[str]]] = []
    for index, record in enumerate(records):
        anchor_ids = _method_anchor_ids(record)
        formulae = {
            formula
            for anchor_id in anchor_ids
            for formula in formulae_by_id.get(anchor_id, set())
        }
        if formulae:
            indexed_formulae.append((index, formulae, anchor_ids))
    parent = {index: index for index, _, _ in indexed_formulae}

    def root(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for offset, (left_index, left_formulae, _) in enumerate(indexed_formulae):
        for right_index, right_formulae, _ in indexed_formulae[offset + 1:]:
            overlaps = any(
                left in right or right in left
                for left in left_formulae
                for right in right_formulae
            )
            if overlaps:
                parent[root(right_index)] = root(left_index)
    families: dict[int, set[str]] = {}
    for index, _, anchor_ids in indexed_formulae:
        families.setdefault(root(index), set()).update(anchor_ids)
    return [
        sorted(anchor_ids) for anchor_ids in families.values()
        if len(anchor_ids) > 1
    ]


def _method_formula_family_reduction(
    records: list[dict], method_anchors: list[dict] | None,
) -> int:
    """Redundant anchored rows that teach overlapping formula families."""
    return sum(
        len(group) - 1
        for group in _method_formula_family_groups(records, method_anchors)
    )


def _coalesce_method_family_rows(
    records: list[dict], method_family_groups: list[list[str]],
) -> list[dict]:
    """Deterministically combine rows the source proves share one formula family."""
    out = [dict(record) for record in records]
    for family in method_family_groups:
        family_ids = set(family)
        indexes = [
            index for index, record in enumerate(out)
            if _method_anchor_ids(record) & family_ids
        ]
        if len(indexes) < 2:
            continue
        target_index = indexes[0]
        target = dict(out[target_index])
        merged_details: list[str] = []
        merged_keywords: list[str] = []
        merged_evidence: list[str] = []
        for index in indexes:
            record = out[index]
            detail = _DESCRIPTION_PREFIX_RE.sub(
                "", record.get("concept_details") or "").strip()
            if detail and detail not in merged_details:
                merged_details.append(detail)
            for keyword in re.split(r"\s*,\s*", record.get("keywords") or ""):
                keyword = keyword.strip()
                if keyword and keyword not in merged_keywords:
                    merged_keywords.append(keyword)
            merged_evidence.append(record.get("source_evidence") or "")
        if merged_details:
            target["concept_details"] = (
                "Description: " + " ".join(merged_details))
        target["keywords"] = ", ".join(merged_keywords)
        target["source_evidence"] = _merge_method_source_evidence(
            *merged_evidence, *family)
        out[target_index] = target
        for index in reversed(indexes[1:]):
            out.pop(index)
    return out


def _consolidate_task_grounded_fragments_via_api(
    records: list[dict], *, meta: dict,
    source_topic_excerpts: list[dict],
    method_anchors: list[dict] | None = None,
) -> list[dict]:
    """Merge Example/Exercise-shaped concept rows into durable objectives."""
    import json as _json

    suspicious = _question_grounded_fragmentation_topics(records)
    if not suspicious:
        return records
    excerpt_by_key = {
        _topic_comparison_key(group.get("topic") or ""):
        group.get("excerpt") or ""
        for group in source_topic_excerpts or []
    }
    replacement_by_key: dict[str, list[dict]] = {}
    system = prompts.get_text("concepts.task_fragment_consolidation.system")
    for topic_key in suspicious:
        topic_records = [
            record for record in records
            if _topic_comparison_key(record.get("topic") or "") == topic_key
            and not cr.is_culmination(record.get("concept_title", ""))
        ]
        if len(topic_records) < 3:
            continue
        topic = (topic_records[0].get("topic") or "").strip()
        task_grounded_count = sum(
            not _method_anchor_ids(record)
            and bool(_QUESTION_GROUNDED_EVIDENCE_RE.search(
                record.get("source_evidence") or ""))
            for record in topic_records
        )
        method_family_groups = _method_formula_family_groups(
            topic_records, method_anchors)
        method_family_reduction = sum(
            len(group) - 1 for group in method_family_groups)
        max_rows = max(
            2,
            len(topic_records) - task_grounded_count + 2
            - method_family_reduction,
        )
        user = (
            _metadata_block(meta)
            + f"\nSOURCE TOPIC: {topic}\n"
            + f"CONSOLIDATION BOUND: return AT MOST {max_rows} rows. "
            + f"The draft has {task_grounded_count} question-grounded rows; "
            + "retain no more than two durable application/modeling objectives "
            + "for those rows, while preserving distinct non-task objectives. "
            + f"Merge {method_family_reduction} redundant anchored row(s) whose "
            + "required formulas overlap, carrying all METHOD IDs forward. "
            + "OVERLAPPING METHOD FAMILIES (every list MUST become one row): "
            + _json.dumps(method_family_groups)
            + "\n"
            + "\nDRAFT CONCEPT ROWS:\n"
            + _json.dumps(
                {"rows": _records_to_api_rows(topic_records)},
                ensure_ascii=False,
            )
            + "\n\nSOURCE TOPIC EXCERPT:\n"
            + _trim(excerpt_by_key.get(topic_key, ""), 160_000)
        )
        candidate: list[dict] = []
        rejected_titles: list[str] = []
        unmerged_method_families: list[list[str]] = []
        for attempt in range(1, 4):
            attempt_user = user
            if attempt > 1:
                attempt_user += (
                    "\n\nCORRECTION: Your prior consolidation retained "
                    "question/difficulty labels as concepts: "
                    + (", ".join(rejected_titles) or "(none)")
                    + ". It also failed to coalesce these overlapping METHOD "
                    "families into one row per list: "
                    + _json.dumps(unmerged_method_families)
                    + ". Merge those exact IDs onto one row, merge task labels "
                    "into direct/contextual application objectives, and obey "
                    "the row bound."
                )
            data = _openai_json(
                system, attempt_user, purpose="concept_validation")
            candidate = [
                row for row in _concept_rows_to_records(data)
                if _topic_comparison_key(row.get("topic") or "") == topic_key
                and not cr.is_culmination(row.get("concept_title", ""))
            ]
            candidate = _preserve_required_method_rows(
                topic_records, candidate)
            candidate = _coalesce_method_family_rows(
                candidate, method_family_groups)
            candidate = _dedupe_titles_chapter_wide(
                _ensure_parent_concepts(candidate))
            rejected_titles = [
                row.get("concept_title", "")
                for row in candidate
                if not _method_anchor_ids(row)
                and _QUESTION_GROUNDED_EVIDENCE_RE.search(
                    row.get("source_evidence") or "")
                and _NON_DURABLE_TASK_CONCEPT_RE.search(
                    row.get("concept_title") or "")
            ]
            unmerged_method_families = [
                family for family in method_family_groups
                if not any(
                    set(family) <= _method_anchor_ids(row)
                    for row in candidate
                )
            ]
            if (
                2 <= len(candidate) <= max_rows < len(topic_records)
                and not rejected_titles
                and not unmerged_method_families
            ):
                replacement_by_key[topic_key] = candidate
                progress.log(
                    f"Consolidated task-grounded concept fragments in "
                    f"{topic!r}: {len(topic_records)} -> "
                    f"{len(candidate)} rows.",
                    level="success",
                )
                break
        if topic_key not in replacement_by_key:
            progress.log(
                f"Rejected task-fragment consolidation for {topic!r}: "
                f"{len(topic_records)} -> {len(candidate)} rows"
                + (
                    f"; non-durable titles: {', '.join(rejected_titles)}"
                    if rejected_titles else ""
                )
                + (
                    "; unmerged METHOD families: "
                    + _json.dumps(unmerged_method_families)
                    if unmerged_method_families else ""
                )
                + ".",
                level="warning",
            )
    if not replacement_by_key:
        return records
    out: list[dict] = []
    emitted: set[str] = set()
    for record in records:
        key = _topic_comparison_key(record.get("topic") or "")
        replacement = replacement_by_key.get(key)
        if replacement is None:
            out.append(record)
        elif key not in emitted:
            out.extend(replacement)
            emitted.add(key)
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


def _compact_skeleton_floor(
    records: list[dict],
    *,
    expected_min: int,
    expected_max: int,
) -> int:
    """Lower bound for a retry that compacts an over-dense skeleton.

    Landing anywhere above the very conservative under-extraction floor is not
    enough: a retry can otherwise swing from dozens of source-backed rows to a
    handful of umbrella concepts.  Preserve every distinct parent family when
    feasible and require at least sixty percent of the compact ceiling.
    """
    density_floor = (expected_max * 3 + 4) // 5
    family_floor = min(
        expected_max,
        len(_skeleton_family_labels(records)),
    )
    return min(
        expected_max,
        max(expected_min, density_floor, family_floor),
    )


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
        # "prove/proof" is often ordinary disciplinary prose ("history proves
        # that...", "no further proof..."). Keep it only in formal contexts:
        # a proof/prove heading or an imperative/theorem-shaped statement.
        formal_proof_context = bool(re.search(
            r"(?im)(?:^\s*(?:proof|proving)\b"
            r"|^\s*prove\b.{0,120}\b(?:theorem|lemma|proposition|identity|that)\b"
            r"|\b(?:proof|prove)\b.{0,80}\b"
            r"(?:theorem|lemma|proposition|identity)\b"
            r"|\b(?:theorem|lemma|proposition|identity)\b.{0,80}\b"
            r"(?:proof|prove)\b)",
            searchable,
        ))
        has_method_word = bool(re.search(
            r"\b(?:deriv|method|procedure|algorithm|technique)\w*\b",
            searchable, re.IGNORECASE)) or formal_proof_context
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
        bool(_misconception_body(details) or _error_analysis_body(details)),
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
        data = _openai_json(
            system, user, purpose="concept_validation")
        raw_rows_value = data.get("rows") if isinstance(data, dict) else None
        response_issue = ""
        if not isinstance(data, dict):
            response_issue = (
                f"response must be an object, received "
                f"{type(data).__name__}"
            )
            raw_rows: list = []
        elif not isinstance(raw_rows_value, list):
            response_issue = (
                "response field 'rows' must be a list, received "
                f"{type(raw_rows_value).__name__}"
            )
            raw_rows = []
        else:
            raw_rows = raw_rows_value
        if response_issue:
            progress.log(
                f"  method-anchor recovery attempt={attempt} response rejected: "
                f"{response_issue}.",
                level="warning",
            )
        elif not raw_rows:
            progress.log(
                f"  method-anchor recovery attempt={attempt} returned no rows.",
                level="warning",
            )

        tagged_counts: dict[str, int] = {}
        valid_by_anchor: dict[str, list[tuple[int, dict, dict]]] = {}
        rejected_rows: set[int] = set()

        def reject_row(
            row_index: int, raw_row, reason: str, *,
            anchor_id: str = "",
        ) -> None:
            rejected_rows.add(row_index)
            row_dict = raw_row if isinstance(raw_row, dict) else {}
            title = _diagnostic_snippet(
                row_dict.get("concept")
                or row_dict.get("concept_title")
                or "<unknown>",
                limit=80,
            )
            evidence = _diagnostic_snippet(
                row_dict.get("source_evidence"), limit=100)
            description = _diagnostic_snippet(
                row_dict.get("concept_description")
                or row_dict.get("concept_details"),
                limit=140,
            )
            progress.log(
                f"  method-anchor recovery attempt={attempt} "
                f"row_index={row_index} rejected: "
                f"anchor={anchor_id or '<unresolved>'!r}; "
                f"concept={title!r}; reason={_diagnostic_snippet(reason)!r}; "
                f"source_evidence={evidence!r}; snippet={description!r}",
                level="warning",
            )

        for raw_index, raw_row in enumerate(raw_rows):
            if not isinstance(raw_row, dict):
                reject_row(
                    raw_index,
                    raw_row,
                    f"row must be an object, received "
                    f"{type(raw_row).__name__}",
                )
                continue
            raw_evidence = raw_row.get("source_evidence")
            if not isinstance(raw_evidence, str):
                reject_row(
                    raw_index,
                    raw_row,
                    "source_evidence must be a non-empty string containing "
                    "one pending METHOD ID",
                )
                continue
            exact_ids = set(_METHOD_ANCHOR_ID_RE.findall(raw_evidence))
            matching_ids = exact_ids.intersection(pending)
            if len(exact_ids) != 1 or len(matching_ids) != 1:
                found = ", ".join(sorted(exact_ids)) or "<none>"
                expected = ", ".join(
                    anchor_id for anchor_id in ordered_ids
                    if anchor_id in pending
                )
                reject_row(
                    raw_index,
                    raw_row,
                    "source_evidence must contain exactly one pending METHOD "
                    f"ID; found {found}; pending {expected}",
                )
                continue
            anchor_id = next(iter(matching_ids))
            tagged_counts[anchor_id] = tagged_counts.get(anchor_id, 0) + 1

            required_fields = (
                "topic", "parent_concept", "concept",
                "concept_description", "source_evidence",
            )
            missing_fields = [
                field for field in required_fields
                if (
                not isinstance(raw_row.get(field), str)
                or not raw_row.get(field, "").strip()
                )
            ]
            if missing_fields:
                reject_row(
                    raw_index,
                    raw_row,
                    "missing or non-string required field(s): "
                    + ", ".join(missing_fields),
                    anchor_id=anchor_id,
                )
                continue
            if (
                raw_row.get("keywords") is not None
                and not isinstance(raw_row.get("keywords"), str)
            ):
                reject_row(
                    raw_index,
                    raw_row,
                    "keywords must be a string when present",
                    anchor_id=anchor_id,
                )
                continue

            parsed = _concept_rows_to_records({"rows": [raw_row]})
            if len(parsed) != 1:
                reject_row(
                    raw_index,
                    raw_row,
                    "row parser did not produce exactly one concept record "
                    f"(produced {len(parsed)})",
                    anchor_id=anchor_id,
                )
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
            # Recovery rows often repeat a source formula using raw $...$ or a
            # Mathpix display wrapper.  Normalize the row before applying the
            # same strict contract used by the final map.
            record = _canonicalize_concept_rich_text([record])[0]

            report = cv.validate_concept_rows(
                [record],
                allow_types=False,
                require_culmination=False,
                allow_culmination=False,
            )
            if not report["ok"]:
                hard_errors = [
                    error for error in report.get("errors", [])
                    if error.get("severity") == "error"
                ]
                reason = "; ".join(
                    f"{error.get('code')} field={error.get('field')}: "
                    f"{error.get('message')}"
                    for error in hard_errors
                ) or "strict concept validation failed"
                reject_row(
                    raw_index,
                    raw_row,
                    reason,
                    anchor_id=anchor_id,
                )
                continue
            valid_by_anchor.setdefault(anchor_id, []).append(
                (raw_index, record, raw_row))

        accepted = 0
        for anchor_id in ordered_ids:
            if anchor_id not in pending:
                continue
            candidates = valid_by_anchor.get(anchor_id, [])
            tagged_count = tagged_counts.get(anchor_id, 0)
            if tagged_count != 1:
                for raw_index, _, raw_row in candidates:
                    reject_row(
                        raw_index,
                        raw_row,
                        "expected exactly one returned row with this METHOD "
                        f"ID, received {tagged_count}",
                        anchor_id=anchor_id,
                    )
                continue
            if len(candidates) != 1:
                continue
            raw_index, record, raw_row = candidates[0]
            key = (
                _topic_comparison_key(record.get("topic", "")),
                bi.normalize_question_text(record.get("concept_title", "")),
            )
            if key in recovered_keys:
                reject_row(
                    raw_index,
                    raw_row,
                    "topic and concept title duplicate an already recovered "
                    "method row",
                    anchor_id=anchor_id,
                )
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
                f"; rejected {len(rejected_rows)} row(s) with detailed "
                "reasons above."
                if rejected_rows else "."
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
    resume_chunks: list[dict] | None = None,
    checkpoint_callback=None,
) -> list[dict]:
    system = prompts.get_text("concepts.skeleton.system")
    all_records: list[dict] = []
    progress.log(
        f"Section-aware skeleton extraction across {len(chunks)} chunk(s).")
    completed_chunks: list[dict] = []
    restored_by_index: dict[int, list[dict]] = {}
    for expected_index, saved in enumerate(resume_chunks or [], start=1):
        if expected_index > len(chunks) or not isinstance(saved, dict):
            break
        if (
            saved.get("chunk_index") != expected_index
            or (
                saved.get("chunk_count") is not None
                and saved.get("chunk_count") != len(chunks)
            )
            or saved.get("chunk_sha256") != _chunk_checkpoint_sha256(
                chunks[expected_index - 1])
            or not isinstance(saved.get("records"), list)
        ):
            break
        durable_chunk = {
            "chunk_index": expected_index,
            "chunk_count": len(chunks),
            "chunk_sha256": saved["chunk_sha256"],
            "records": copy.deepcopy(saved["records"]),
        }
        completed_chunks.append(durable_chunk)
        restored_by_index[expected_index] = durable_chunk["records"]
    if restored_by_index:
        progress.log(
            f"Restored {len(restored_by_index)}/{len(chunks)} completed "
            "skeleton chunk(s) from the saved checkpoint.",
            level="success",
        )
    for i, chunk in enumerate(chunks, start=1):
        fraction = (i - 1) / max(len(chunks), 1)
        progress.step(f"Concept skeleton — chunk {i}/{len(chunks)}",
                      value=progress_start
                      + (progress_end - progress_start) * fraction)
        if i in restored_by_index:
            chunk_records = copy.deepcopy(restored_by_index[i])
            all_records.extend(chunk_records)
            progress.log(
                f"  chunk {i}/{len(chunks)} restored from checkpoint: "
                f"{len(chunk_records)} skeleton row(s).",
                level="success",
            )
            progress.set_progress(
                progress_start
                + (progress_end - progress_start)
                * (i / max(len(chunks), 1)),
                label=f"Concept skeleton chunk {i}/{len(chunks)} restored",
            )
            continue
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
        data = _openai_json(system, user, purpose="source_extraction")
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
            retry_data = _openai_json(
                system, retry_user, purpose="concept_validation")
            retry_records = _strip_types_from_records(_concept_rows_to_records(retry_data))
            retry_records = [
                r for r in retry_records
                if not cr.is_culmination(r.get("concept_title", ""))
            ]
            if len(retry_records) > len(chunk_records):
                chunk_records = retry_records
        expected_max = _expected_max_skeleton_rows(chunk["text"], chunk_headings)
        if len(chunk_records) > expected_max:
            compact_floor = _compact_skeleton_floor(
                chunk_records,
                expected_min=expected_min,
                expected_max=expected_max,
            )
            family_labels = _skeleton_family_labels(chunk_records)
            family_instruction = ""
            if family_labels and len(family_labels) <= expected_max:
                family_instruction = (
                    "\nMUST-PRESERVE SOURCE-BACKED PARENT FAMILIES "
                    "(retain at least one coherent concept for each):\n- "
                    + "\n- ".join(family_labels)
                    + "\n"
                )
            progress.log(
                f"  chunk {i}/{len(chunks)} returned {len(chunk_records)} "
                f"concept(s) (target {compact_floor}-{expected_max}) — "
                "retrying as a compact teaching skeleton.",
                level="warning",
            )
            retry_user = (
                user
                + family_instruction
                + f"\n\nYOUR PREVIOUS ANSWER HAD {len(chunk_records)} CONCEPTS — "
                "that is too granular. Merge terms, cases, examples, sub-types, "
                "and question headings into their parent teaching concepts. "
                "Keep only durable teacher-facing mastery objectives, retain "
                "at least one coherent concept for every must-preserve family, "
                "and never combine disjoint subject domains into one row. Do "
                "not lose main coverage. Return between "
                f"{compact_floor} and {expected_max} concepts for this chunk."
            )
            retry_data = _openai_json(
                system, retry_user, purpose="concept_validation")
            retry_records = _strip_types_from_records(_concept_rows_to_records(retry_data))
            retry_records = [
                r for r in retry_records
                if not cr.is_culmination(r.get("concept_title", ""))
            ]
            if compact_floor <= len(retry_records) < len(chunk_records):
                chunk_records = retry_records
            elif retry_records:
                progress.log(
                    f"  rejected compact skeleton with {len(retry_records)} "
                    f"concept(s); the source-backed floor is {compact_floor}.",
                    level="warning",
                )
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
            retry_data = _openai_json(
                system, retry_user, purpose="concept_validation")
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
        completed_chunks.append({
            "chunk_index": i,
            "chunk_count": len(chunks),
            "chunk_sha256": _chunk_checkpoint_sha256(chunk),
            "records": copy.deepcopy(chunk_records),
        })
        chunk_progress = (
            progress_start
            + (progress_end - progress_start)
            * (i / max(len(chunks), 1))
        )
        _emit_concept_checkpoint(
            checkpoint_callback,
            "skeleton_chunks",
            progress_value=chunk_progress,
            stage_label=f"Concept skeleton chunk {i}/{len(chunks)} complete",
            completed_chunks=completed_chunks,
            chunk_count=len(chunks),
        )
        progress.set_progress(
            chunk_progress,
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
    r"do\s+this|write\s+in\s+brief|discuss|.*\bactivity\b.*|activities|"
    r"probe\s+and\s+ponder|keep\s+the\s+curiosity\s+alive|"
    r"discover\s*,?\s*design\s*,?\s*and\s*debate|happy\s+investigating|"
    r"(?:\?\s*)?figure\s+it\s+out|"
    r"projects?(?:\s+work)?|things\s+to\s+remember|"
    r"points\s+to\s+remember|key\s+points|glossary)\b[\s\d.!?:()\-]*$",
    re.IGNORECASE,
)
# Filler umbrella headings that cleanup remaps away from the concept map.
# Requiring them as "structurally proven source topics" aborts deposit after
# they are intentionally omitted (Overview / Summary / Basics, etc.).
# Classroom discussion cases and Activity blocks are classified by the GPT
# Activity/Info Hub pass — not by chapter-named deterministic filters.
_FILLER_SOURCE_TOPIC_KEYS = {
    "overview", "basics", "basic concepts", "general",
    "summary", "misc", "miscellaneous",
}


def _collapse_spaced_heading_word(heading: str) -> str:
    text = re.sub(r"\s+", " ", (heading or "").strip())
    if re.fullmatch(r"(?:[A-Za-z]\s+){2,}[A-Za-z]s?", text):
        return re.sub(r"\s+", "", text).lower()
    return text.lower()


def _is_filler_source_topic(heading: str) -> bool:
    """True for umbrella filler headings that must not be mandatory topics."""
    key = _topic_comparison_key(heading)
    if key in _FILLER_SOURCE_TOPIC_KEYS:
        return True
    stripped = bi.normalize_question_text(_strip_section_number(heading))
    if stripped in _FILLER_SOURCE_TOPIC_KEYS:
        return True
    return False


def _is_answer_key_source_section(section: dict) -> bool:
    """Detect an appended answer-key section without matching normal examples."""
    body = str((section or {}).get("body") or "")
    answer_markers = re.findall(
        r"(?im)^[ \t]*(?:(?:answers?|solutions?)|ans|soln?)[ \t]*"
        r"(?:[:：.]|[-–—])",
        body,
    )
    page_markers = re.findall(
        r"(?im)^[ \t]*Page[ \t]+No\.?[ \t]*\d+[ \t]*$", body)
    return len(answer_markers) >= 3 and (
        len(page_markers) >= 2 or len(answer_markers) >= 8
    )


def _is_non_topic_heading(heading: str) -> bool:
    # "(Optional)" suffixes and asterisks ("EXERCISE 6.6 (Optional)*") must not
    # hide an exercise heading from the match.
    h = re.sub(r"\(\s*optional\s*\)|\*", " ", heading or "", flags=re.IGNORECASE)
    if re.fullmatch(r"\s*(?:\d+|[ivxlcdm]+)\s*", h, re.IGNORECASE):
        return True
    if _collapse_spaced_heading_word(h) in {"questions", "exercises"}:
        return True
    if _is_filler_source_topic(h):
        return True
    return bool(_EXERCISE_ONLY_RE.match(h) or _NON_TOPIC_RE.match(h))


def _scrub_section_numbers(records: list[dict]) -> list[dict]:
    """Remove section/exercise numbering from topics and titles.

    Rows whose topic is a bare exercise or structural heading (e.g.
    "EXERCISE 1.2", "Solution", "Tick the Correct Answer" — these slip through
    when OCR'd chapters mark such blocks as headings) are merged into the
    preceding real topic so exercise/solution content is not dropped.

    Filler umbrella topics (Overview / Summary / Basics / …) are dropped
    entirely so preview/recap rows are not reassigned into neighboring topics.
    """

    def _scrub(text: str) -> str:
        return re.sub(r"\s+", " ", _SECTION_NUMBER_SCRUB_RE.sub(" ", text or "")
                      ).strip(" -:.,")

    prev_topic = ""
    out: list[dict] = []
    dropped = 0
    for rec in records:
        topic = rec.get("topic", "")
        scrubbed = _scrub(topic)
        if _is_filler_source_topic(topic) or _is_filler_source_topic(scrubbed):
            dropped += 1
            continue
        if _is_non_topic_heading(topic) or not scrubbed or _is_non_topic_heading(scrubbed):
            rec["topic"] = prev_topic or "General"
        elif scrubbed != topic:
            rec["topic"] = scrubbed
        prev_topic = rec.get("topic", "") or prev_topic
        title = rec.get("concept_title", "")
        scrubbed_title = _scrub(title)
        if scrubbed_title and scrubbed_title != title:
            rec["concept_title"] = scrubbed_title
        out.append(rec)
    if dropped:
        progress.log(
            f"Dropped {dropped} Overview/Summary/filler concept row(s).",
            level="warning",
        )
    return out


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
    and always positions it last. Its title is rebuilt deterministically from
    the normal concepts in that exact topic, so foreign-topic metadata cannot
    survive. Inventory-backed Types already assigned to it are preserved;
    synthetic starter Types are never invented. Normal rows are never touched.
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
            keep["concept_title"] = _culmination_title(normal[topic])
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


def _ensure_terminal_culmination_contract(
    records: list[dict],
) -> list[dict]:
    """Repair culmination structure/recaps only when the current rows need it.

    Rebuilding an already-valid recap from a title containing raw LaTeX can
    discard its canonical ``[Katex]`` wrappers. Avoid that non-idempotent
    rewrite on resumed final checkpoints.
    """
    report = cv.validate_concept_rows(
        records,
        require_culmination=True,
        allow_culmination=True,
        strict_culmination_recap=True,
    )
    culmination_codes = {
        "culmination_description",
        "culmination_count",
        "culmination_order",
        "culmination_recap_format",
        "culmination_recap_missing_concepts",
    }
    if any(
        error.get("severity") == "error"
        and error.get("code") in culmination_codes
        for error in report.get("errors", [])
    ):
        return _enforce_culminations(records)
    return records


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
            "concept_details": "Description: Recap",
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
            authored["concept_title"] = _culmination_title(topic_records)
            authored = _strip_types_from_records([authored])[0]
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
    data = _openai_json(system, user, purpose="concept_detailing")
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
        return _dedupe_topic_candidates(numbered)

    levels = sorted({c["level"] for c in candidates})
    if len(levels) > 1 and sum(1 for c in candidates if c["level"] == levels[0]) == 1:
        candidates = [c for c in candidates if c["level"] != levels[0]]
        levels = sorted({c["level"] for c in candidates})
    start = 0

    selected: list[dict] = []
    for level in levels[start:]:
        selected.extend(c for c in candidates if c["level"] == level)
        if len(selected) >= _MIN_MAIN_TOPIC_HEADINGS:
            break
    if len(selected) < _MIN_MAIN_TOPIC_HEADINGS:
        selected = candidates

    return _dedupe_topic_candidates(selected)


def _apply_headingless_chapter_topic_fallback(
    sections: list[dict], chapter_title: str,
) -> bool:
    """Use the selected chapter as the one topic when OCR exposes none.

    Some short introductory chapters have no teaching-section headings at all;
    their only heading can be a closing slogan such as ``Happy investigating!``.
    The directory target is authoritative in that narrow case and is safer than
    filing the complete chapter under ``General`` or the closing slogan.
    """
    title = str(chapter_title or "").strip()
    if not sections or not title or _topic_headings(sections):
        return False
    first = sections[0]
    first["heading"] = title
    first["heading_level"] = 1
    first["heading_numbered"] = False
    first["heading_number_prefix"] = ""
    first["heading_chapter"] = False
    return True


def _reorder_records_by_source_topics(
    records: list[dict], headings: list[str],
) -> list[dict]:
    """Restore textbook topic order without changing row content.

    Recovery and GPT re-segregation can append an earlier source topic at the
    end of the map.  The source heading sequence is authoritative; ordering is
    therefore a safe structural operation, unlike semantic reassignment.
    Within each topic the existing concept order is stable and Culmination is
    always moved to the end.
    """
    order = {
        _topic_comparison_key(heading): index
        for index, heading in enumerate(headings or [])
        if _topic_comparison_key(heading)
    }
    if not records or not order:
        return records
    unknown_order: dict[str, int] = {}
    for rec in records:
        key = _topic_comparison_key(rec.get("topic") or "")
        if key not in order and key not in unknown_order:
            unknown_order[key] = len(unknown_order)
    indexed = list(enumerate(records))
    indexed.sort(key=lambda pair: (
        order.get(
            _topic_comparison_key(pair[1].get("topic") or ""),
            len(order) + unknown_order.get(
                _topic_comparison_key(pair[1].get("topic") or ""), 0),
        ),
        1 if cr.is_culmination(pair[1].get("concept_title", "")) else 0,
        pair[0],
    ))
    return [record for _, record in indexed]


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


def _missing_source_topic_excerpts(
    records: list[dict], source_topic_excerpts: list[dict],
) -> list[dict]:
    """Source topics with no normal concept row, preserving reading order."""
    covered = {
        _topic_comparison_key(rec.get("topic") or "")
        for rec in records
        if not cr.is_culmination(rec.get("concept_title", ""))
    }
    return [
        group for group in source_topic_excerpts or []
        if (
            not _is_filler_source_topic(group.get("topic") or "")
            and not _is_non_topic_heading(group.get("topic") or "")
            and _topic_comparison_key(group.get("topic") or "") not in covered
        )
    ]


def _recover_missing_topic_concepts_via_api(
    records: list[dict], *, meta: dict, source_topic_excerpts: list[dict],
    max_attempts: int = 2,
) -> list[dict]:
    """Recover concepts for structurally proven topics omitted by the model."""
    import json as _json

    out = [dict(record) for record in records]
    missing = _missing_source_topic_excerpts(out, source_topic_excerpts)
    if not missing:
        return out
    system = prompts.get_text("concepts.missing_topic_recovery.system")
    for attempt in range(1, max_attempts + 1):
        missing = _missing_source_topic_excerpts(out, source_topic_excerpts)
        if not missing:
            break
        existing_titles = [
            record.get("concept_title", "") for record in out
            if (record.get("concept_title") or "").strip()
        ]
        payload = {
            "missing_source_topics": [
                {
                    "topic": (group.get("topic") or "").strip(),
                    "excerpt": _trim(group.get("excerpt") or "", 80_000),
                }
                for group in missing
            ],
            "existing_concept_titles": existing_titles,
        }
        user = (
            _metadata_block(meta)
            + "\nMissing source-topic coverage to recover:\n"
            + _json.dumps(payload, ensure_ascii=False)
        )
        progress.log(
            f"Topic coverage recovery attempt {attempt}: "
            f"{len(missing)} source topic(s) have no concept.")
        data = _openai_json(
            system, user, purpose="concept_validation")
        allowed = {
            _topic_comparison_key(group.get("topic") or ""):
            (group.get("topic") or "").strip()
            for group in missing
            if _topic_comparison_key(group.get("topic") or "")
        }
        existing_keys = {
            bi.normalize_question_text(record.get("concept_title", ""))
            for record in out
        }
        added = 0
        for candidate in _concept_rows_to_records(data):
            topic_key = _topic_comparison_key(candidate.get("topic") or "")
            title_key = bi.normalize_question_text(
                candidate.get("concept_title", ""))
            if topic_key not in allowed or not title_key or title_key in existing_keys:
                continue
            candidate["topic"] = allowed[topic_key]
            if not (candidate.get("parent_concept") or "").strip():
                candidate["parent_concept"] = allowed[topic_key]
            out.append(candidate)
            existing_keys.add(title_key)
            added += 1
        progress.log(
            f"Topic coverage recovery added {added} concept row(s).",
            level="success" if added else "warning",
        )
    missing = _missing_source_topic_excerpts(out, source_topic_excerpts)
    if missing:
        raise RuntimeError(
            "concept extraction omitted structurally proven source topics: "
            + ", ".join(
                (group.get("topic") or "").strip() for group in missing)
        )
    return out


def _chapter_opening_excerpt(
    sections: list[dict], headings: list[str],
) -> dict[str, str] | None:
    """Return substantive source material before the first main topic."""
    if not sections or not headings:
        return None
    first_topic = _strip_section_number(headings[0]).strip()
    first_key = _topic_comparison_key(first_topic)
    opening_parts: list[str] = []
    for section in sections:
        heading = (section.get("heading") or "").strip()
        if heading and _topic_comparison_key(heading) == first_key:
            break
        if _is_filler_source_topic(heading):
            continue
        body = (section.get("body") or "").strip()
        if body:
            opening_parts.append(
                (f"{heading}\n" if heading else "") + body)
    excerpt = "\n\n".join(opening_parts).strip()
    # Avoid spending a semantic audit on a title page or a decorative image.
    prose = re.sub(r"https?://\S+|\\[A-Za-z]+\{.*?\}", " ", excerpt)
    if len(re.sub(r"\W+", "", prose, flags=re.UNICODE)) < 180:
        return None
    return {"topic": first_topic, "excerpt": excerpt}


def _recover_chapter_opening_concepts_via_api(
    records: list[dict], *, meta: dict, sections: list[dict],
    headings: list[str],
) -> list[dict]:
    """Semantically audit and recover omitted pre-section teaching content."""
    import json as _json

    opening = _chapter_opening_excerpt(sections, headings)
    if not opening or not records:
        return records
    topic_key = _topic_comparison_key(opening["topic"])
    existing = [
        row for row in records
        if _topic_comparison_key(row.get("topic") or "") == topic_key
        and not cr.is_culmination(row.get("concept_title", ""))
    ]
    payload = {
        "opening_topic": opening["topic"],
        "opening_excerpt": _trim(opening["excerpt"], 50_000),
        "existing_rows": _records_to_api_rows(existing),
    }
    progress.log("Auditing substantive chapter-opening concept coverage via API.")
    data = _openai_json(
        prompts.get_text("concepts.opening_recovery.system"),
        _metadata_block(meta) + "\n" + _json.dumps(payload, ensure_ascii=False),
        purpose="concept_validation",
    )
    raw_candidates = []
    for raw in (data or {}).get("missing_rows") or []:
        if not isinstance(raw, dict):
            continue
        normalized = dict(raw)
        if isinstance(normalized.get("keywords"), list):
            normalized["keywords"] = ", ".join(
                str(value).strip()
                for value in normalized["keywords"]
                if str(value).strip()
            )
        raw_candidates.append(normalized)
    candidates = _concept_rows_to_records({
        "rows": raw_candidates,
    })
    existing_titles = {
        bi.normalize_question_text(row.get("concept_title") or "")
        for row in records
    }
    additions: list[dict] = []
    for candidate in candidates:
        title = (candidate.get("concept_title") or "").strip()
        title_key = bi.normalize_question_text(title)
        if (
            not title_key
            or title_key in existing_titles
            or cr.is_culmination(title)
            or _is_filler_source_topic(title)
        ):
            continue
        candidate["topic"] = opening["topic"]
        if not (candidate.get("parent_concept") or "").strip():
            candidate["parent_concept"] = opening["topic"]
        additions.append(candidate)
        existing_titles.add(title_key)
    if not additions:
        return records
    out = [dict(row) for row in records]
    insert_at = next(
        (
            index for index, row in enumerate(out)
            if _topic_comparison_key(row.get("topic") or "") == topic_key
        ),
        0,
    )
    out[insert_at:insert_at] = additions
    progress.log(
        f"Recovered {len(additions)} missing chapter-opening concept row(s).",
        level="success",
    )
    return out


def _restructure_topics_via_api(
    records: list[dict], *, meta: dict,
    source_topic_excerpts: list[dict] | None = None,
    headings: list[str] | None = None,
) -> list[dict]:
    """Re-segregate collapsed topics using grouped source-topic excerpts.

    Only the ``topic`` field is taken from the model, matched back to the
    original rows by concept title — no concept can be added, dropped, or
    rewritten by this pass. ``headings`` remains a compatibility fallback for
    older direct callers; the live pipeline always supplies source excerpts.
    """
    import json as _json

    source_topic_excerpts = list(source_topic_excerpts or [])
    if not source_topic_excerpts:
        source_topic_excerpts = [
            {"topic": heading, "excerpt": ""} for heading in (headings or [])
        ]
    headings = [
        (group.get("topic") or "").strip()
        for group in source_topic_excerpts
        if (group.get("topic") or "").strip()
    ]
    excerpt_budget = max(
        12_000, 220_000 // max(1, len(source_topic_excerpts)))
    prompt_excerpts = [
        {
            "topic": (group.get("topic") or "").strip(),
            "excerpt": _trim(group.get("excerpt") or "", excerpt_budget),
        }
        for group in source_topic_excerpts
        if (group.get("topic") or "").strip()
    ]
    records = _assign_topics_from_source_evidence(
        records, source_topic_excerpts)
    system = prompts.get_text("concepts.topic_structure.system")
    payload = _json.dumps({"rows": _records_to_api_rows(records)}, ensure_ascii=False)
    user = (
        _metadata_block(meta)
        + "\nSECTION HEADINGS (reading order):\n- "
        + "\n- ".join(headings)
        + "\n\nSOURCE TOPIC EXCERPTS (structural headings already inherited):\n"
        + _json.dumps({"source_topics": prompt_excerpts}, ensure_ascii=False)
        + f"\n\nConcept map with collapsed topics ({len(records)} rows):\n"
        + payload
    )
    data = _openai_json(system, user, purpose="concept_mapping")
    topic_by_title = {
        bi.normalize_question_text(r["concept_title"]): r["topic"].strip()
        for r in _concept_rows_to_records(data)
        if (r.get("topic") or "").strip()
    }
    updated = 0
    for rec in records:
        if _method_anchor_ids(rec):
            continue
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
    return _assign_topics_from_source_evidence(
        records, source_topic_excerpts)


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
    data = _openai_json(system, user, purpose="metadata")
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


_LEGACY_CONCEPT_CHECKPOINT_SCHEMA = 2
_CONCEPT_CHECKPOINT_SCHEMA = 3
_CONCEPT_CHECKPOINT_FORMAT = "aegis-concept-stage-history"
_CONCEPT_CHECKPOINT_STAGE = "pre_type_assignment"

# Stage versions describe the serialized artifact contract, not the git
# revision that produced it.  A later deployment may therefore reuse an older
# checkpoint when its stage contract is still accepted.  Bump only the stage
# whose payload or meaning becomes incompatible.
_CONCEPT_CHECKPOINT_STAGES = {
    "skeleton_chunks": {
        "order": 10,
        "version": 1,
        "progress": 0.24,
        "label": "Concept skeleton chunks",
    },
    "skeleton_complete": {
        "order": 20,
        "version": 1,
        "progress": 0.24,
        "label": "Concept skeleton complete",
    },
    "canonical_skeleton": {
        "order": 30,
        "version": 1,
        "progress": 0.35,
        "label": "Canonical skeleton and source topics complete",
    },
    "description_method_snapshot": {
        "order": 40,
        "version": 1,
        "progress": 0.55,
        "label": "Descriptions and method snapshot complete",
    },
    "question_inventory": {
        "order": 50,
        "version": 1,
        "progress": 0.70,
        "label": "Question and task inventory complete",
    },
    _CONCEPT_CHECKPOINT_STAGE: {
        "order": 60,
        "version": 1,
        "progress": 0.81,
        "label": "Reusable Types mined; ready for Type assignment",
    },
    "post_type_assignment": {
        "order": 70,
        "version": 2,
        "progress": 0.91,
        "label": "Type assignment and activity hubs complete",
    },
    "final_content_ready": {
        "order": 80,
        "version": 2,
        "progress": 0.98,
        "label": "Final content ready for deterministic validation",
    },
    "pre_derivation_draft": {
        "order": 90,
        "version": 1,
        "progress": 0.985,
        "label": "Pre-learning dependency draft complete",
    },
    "pre_derivation_audited": {
        "order": 100,
        "version": 1,
        "progress": 0.992,
        "label": "Pre-learning syllabus audit complete",
    },
    "pre_learner_analysis": {
        "order": 110,
        "version": 1,
        "progress": 0.998,
        "label": "Pre-learning learner analysis complete",
    },
}
_POST_CONCEPT_CHECKPOINT_STAGES = {
    "skeleton_chunks",
    "skeleton_complete",
    "canonical_skeleton",
    "description_method_snapshot",
    "question_inventory",
    _CONCEPT_CHECKPOINT_STAGE,
    "post_type_assignment",
    "final_content_ready",
}
_PRE_DERIVATION_CHECKPOINT_STAGES = {
    "pre_derivation_draft",
    "pre_derivation_audited",
    "pre_learner_analysis",
}


def _serialize_method_row_snapshot(
    snapshot: dict[tuple[str, str], dict],
) -> list[dict]:
    return [
        {
            "anchor_id": anchor_id,
            "topic_key": topic_key,
            "row": copy.deepcopy(row),
        }
        for (anchor_id, topic_key), row in snapshot.items()
    ]


def _deserialize_method_row_snapshot(
    entries: list[dict] | None,
) -> dict[tuple[str, str], dict]:
    snapshot: dict[tuple[str, str], dict] = {}
    for entry in entries or []:
        if not isinstance(entry, dict) or not isinstance(entry.get("row"), dict):
            continue
        anchor_id = str(entry.get("anchor_id") or "").strip().upper()
        topic_key = str(entry.get("topic_key") or "").strip()
        if anchor_id and topic_key:
            snapshot[(anchor_id, topic_key)] = copy.deepcopy(entry["row"])
    return snapshot


def _concept_checkpoint_entries(checkpoint: dict | None) -> list[dict]:
    """Return serialized stage entries from a v3 envelope or legacy entry."""
    if not isinstance(checkpoint, dict):
        return []
    if not checkpoint:
        return []
    if checkpoint.get("checkpoint_format") == _CONCEPT_CHECKPOINT_FORMAT:
        if checkpoint.get("schema_version") != _CONCEPT_CHECKPOINT_SCHEMA:
            return []
        history = checkpoint.get("checkpoints")
        if not isinstance(history, list):
            return []
        return [entry for entry in history if isinstance(entry, dict)]
    return [checkpoint]


def _checkpoint_has_fields(checkpoint: dict, *requirements: tuple[str, type]) -> bool:
    return all(isinstance(checkpoint.get(field), expected) for field, expected in requirements)


def _valid_completed_skeleton_chunks(value) -> bool:
    if not isinstance(value, list) or not value:
        return False
    expected_index = 1
    for item in value:
        if (
            not isinstance(item, dict)
            or item.get("chunk_index") != expected_index
            or not isinstance(item.get("chunk_sha256"), str)
            or not item.get("chunk_sha256")
            or not isinstance(item.get("records"), list)
        ):
            return False
        expected_index += 1
    return True


def _compatible_concept_checkpoint_entry(checkpoint: dict | None) -> bool:
    """Whether this deployment can safely consume one serialized stage."""
    if not isinstance(checkpoint, dict):
        return False
    schema = checkpoint.get("schema_version")
    stage = checkpoint.get("stage")
    if schema == _LEGACY_CONCEPT_CHECKPOINT_SCHEMA:
        return bool(
            stage == _CONCEPT_CHECKPOINT_STAGE
            and _checkpoint_has_fields(
                checkpoint,
                ("records", list),
                ("question_task_inventory", dict),
                ("mined_types", dict),
                ("method_row_snapshot", list),
            )
            and not _invalid_inventory_items(
                checkpoint.get("question_task_inventory")
            )
        )
    spec = _CONCEPT_CHECKPOINT_STAGES.get(stage)
    if schema != _CONCEPT_CHECKPOINT_SCHEMA or spec is None:
        return False
    if checkpoint.get("stage_schema_version", 1) != spec["version"]:
        return False
    if stage == "skeleton_chunks":
        return _valid_completed_skeleton_chunks(
            checkpoint.get("completed_chunks"))
    if stage == "pre_derivation_draft":
        return _checkpoint_has_fields(
            checkpoint, ("records", list), ("pre_draft", dict))
    if stage == "pre_derivation_audited":
        return _checkpoint_has_fields(
            checkpoint, ("records", list), ("pre_audited", dict))
    if stage == "pre_learner_analysis":
        return _checkpoint_has_fields(
            checkpoint, ("records", list), ("base_records", list))
    if not _checkpoint_has_fields(checkpoint, ("records", list)):
        return False
    if stage == "canonical_skeleton":
        return _checkpoint_has_fields(
            checkpoint, ("skeleton_method_row_snapshot", list))
    if stage in {
        "description_method_snapshot",
        "question_inventory",
        _CONCEPT_CHECKPOINT_STAGE,
        "post_type_assignment",
        "final_content_ready",
    } and not _checkpoint_has_fields(
        checkpoint, ("method_row_snapshot", list)
    ):
        return False
    if stage in {
        "question_inventory",
        _CONCEPT_CHECKPOINT_STAGE,
        "post_type_assignment",
        "final_content_ready",
    } and not _checkpoint_has_fields(
        checkpoint, ("question_task_inventory", dict)
    ):
        return False
    if stage in {
        "question_inventory",
        _CONCEPT_CHECKPOINT_STAGE,
        "post_type_assignment",
        "final_content_ready",
    } and _invalid_inventory_items(
        checkpoint.get("question_task_inventory")
    ):
        # Shape compatibility is not enough for a resumable inventory. Empty,
        # stub, or duplicate-qid rows can never satisfy exact coverage and
        # would otherwise fail at 98% on every retry.
        return False
    if stage in {
        _CONCEPT_CHECKPOINT_STAGE,
        "post_type_assignment",
        "final_content_ready",
    } and not _checkpoint_has_fields(checkpoint, ("mined_types", dict)):
        return False
    if (
        stage in {"post_type_assignment", "final_content_ready"}
        and not _placement_certification_contract_complete(
            checkpoint.get("mined_types"),
            checkpoint.get("question_task_inventory"),
        )
    ):
        return False
    return True


def _newest_compatible_concept_checkpoint(
    checkpoint: dict | None,
    *, allowed_stages: set[str] | None = None,
) -> dict | None:
    """Select the furthest compatible completed stage, ignoring newer unknowns."""
    candidates: list[tuple[int, int, dict]] = []
    for index, entry in enumerate(_concept_checkpoint_entries(checkpoint)):
        if not _compatible_concept_checkpoint_entry(entry):
            continue
        stage = str(entry.get("stage") or "")
        if allowed_stages is not None and stage not in allowed_stages:
            continue
        order = _CONCEPT_CHECKPOINT_STAGES.get(
            stage, {"order": 60 if stage == _CONCEPT_CHECKPOINT_STAGE else -1}
        )["order"]
        candidates.append((int(order), index, entry))
    if not candidates:
        return None
    return copy.deepcopy(max(candidates, key=lambda item: (item[0], item[1]))[2])


def _without_concept_checkpoint_stage(
    checkpoint: dict | None, stage: str,
) -> dict | None:
    """Return checkpoint history without one stage.

    This is used when a legacy final checkpoint fails today's strict gate. The
    preceding durable stage remains reusable, while the rejected final payload
    cannot be selected again during the same recovery attempt.
    """
    if not isinstance(checkpoint, dict):
        return None
    if checkpoint.get("checkpoint_format") != _CONCEPT_CHECKPOINT_FORMAT:
        if str(checkpoint.get("stage") or "") == stage:
            return None
        return copy.deepcopy(checkpoint)

    filtered = copy.deepcopy(checkpoint)
    filtered["checkpoints"] = [
        copy.deepcopy(entry)
        for entry in _concept_checkpoint_entries(checkpoint)
        if str(entry.get("stage") or "") != stage
    ]
    newest = _newest_compatible_concept_checkpoint(filtered)
    if newest is None:
        return None
    for field in (
        "stage",
        "stage_order",
        "stage_schema_version",
        "stage_label",
        "saved_at",
        "progress",
    ):
        filtered[field] = copy.deepcopy(newest.get(field))
    return filtered


def _semantic_inventory_items(value: dict | None) -> list[dict]:
    """Comparable inventory content, excluding deterministic metadata.

    Figure URLs, captions, and resolution flags are refreshed locally from the
    current source registry when a final checkpoint is restored.  They must not
    force an otherwise valid 98% checkpoint back through semantic/API stages.
    Topic, task-kind, activity, wording, and context changes remain semantic and
    still invalidate the persisted host review.
    """
    nonsemantic_keys = {
        "order_index",
        "image_urls",
        "_image_captions",
        "_figure_images_resolved",
        "requires_visual",
    }
    return [
        {
            key: copy.deepcopy(field)
            for key, field in item.items()
            if key not in nonsemantic_keys
        }
        for item in (value or {}).get("items") or []
        if isinstance(item, dict)
    ]


def _final_checkpoint_refresh_reasons(
    checkpoint: dict | None, *, sections: list[dict],
    source_topic_excerpts: list[dict],
) -> list[str]:
    """Return structural evidence that a saved final map must be rebuilt.

    A compatible checkpoint only proves that its JSON shape is safe to read. It
    does not prove that it was generated from the current source-completeness
    rules.  In particular, older final checkpoints can omit an entire source
    topic or a deterministic question anchor while still passing their schema
    check.  Resume from the preceding stage in that case so the normal final
    recovery path can repair the map.
    """
    if not checkpoint:
        return []
    reasons: list[str] = []
    missing_topics = _missing_source_topic_excerpts(
        checkpoint.get("records") or [], source_topic_excerpts)
    if missing_topics:
        reasons.append(
            "missing source topic(s): "
            + ", ".join(
                (group.get("topic") or "").strip()
                for group in missing_topics
            )
        )
    inventory_items = (
        (checkpoint.get("question_task_inventory") or {}).get("items") or []
    )
    anchors = _source_task_anchors(sections)
    anchor_label_counts = _anchor_source_label_counts(anchors)
    missing_anchors = [
        anchor for anchor in anchors
        if not any(
            isinstance(item, dict)
            and _inventory_matches_anchor_identity(
                item, anchor, anchor_label_counts)
            for item in inventory_items
        )
    ]
    if missing_anchors:
        reasons.append(
            f"missing {len(missing_anchors)} deterministic source task anchor(s)"
        )
    stale_anchor_tasks = [
        anchor for anchor in anchors
        if any(
            isinstance(item, dict)
            and _inventory_matches_anchor_identity(
                item, anchor, anchor_label_counts)
            for item in inventory_items
        )
        and not any(
            isinstance(item, dict)
            and _inventory_item_covers_anchor(item, anchor)
            for item in inventory_items
        )
    ]
    if stale_anchor_tasks:
        reasons.append(
            f"{len(stale_anchor_tasks)} checkpoint source task(s) are "
            "truncated or stale"
        )
    refreshed_inventory = _refresh_inventory_from_source_anchors(
        checkpoint.get("question_task_inventory") or {}, sections)
    if (
        _semantic_inventory_items(refreshed_inventory)
        != _semantic_inventory_items(
            checkpoint.get("question_task_inventory") or {})
        and not missing_anchors
        and not stale_anchor_tasks
    ):
        # The qid can remain stable while topic, task kind, Activity origin, or
        # another semantic field changes. Such a checkpoint must replay the
        # reviewed host decision even when its question wording still matches.
        reasons.append("source inventory semantics changed")
    analysis_report = cv.validate_concept_rows(
        checkpoint.get("records") or [], strict_analysis_section=True)
    malformed_analysis = [
        error for error in analysis_report["errors"]
        if error.get("code") == "analysis_section_format"
        and error.get("severity") == "error"
    ]
    if malformed_analysis:
        reasons.append(
            f"{len(malformed_analysis)} final learner-analysis section(s) "
            "need canonical normalization"
        )
    source_text = "\n\n".join(
        str(group.get("excerpt") or "")
        for group in source_topic_excerpts
        if isinstance(group, dict)
    )
    if source_text:
        description_report = cv.validate_concept_rows(
            checkpoint.get("records") or [], source_text=source_text)
        copied_descriptions = [
            error for error in description_report["errors"]
            if error.get("code") == "verbatim_source_description"
        ]
        if copied_descriptions:
            reasons.append(
                f"{len(copied_descriptions)} Description(s) copy source prose"
            )
    return reasons


def _refresh_inventory_from_source_anchors(
    inventory: dict | None, sections: list[dict],
) -> dict:
    """Merge authoritative source tasks into an older resumed inventory.

    Existing qids remain stable so mined Type assignments still point at their
    original tasks.  New deterministic anchors receive new qids and are later
    placed by the ordinary exact-coverage repair pass.
    """
    refreshed = copy.deepcopy(inventory or _empty_inventory())
    items = [
        dict(item) for item in refreshed.get("items") or []
        if isinstance(item, dict)
    ]
    merged = _merge_source_task_anchors(items, _source_task_anchors(sections))
    merged = _attach_explicit_figure_images(merged, sections)
    seen_qids = {
        str(item.get("qid") or "").strip()
        for item in merged
        if str(item.get("qid") or "").strip()
    }
    qid_numbers = [
        int(match.group(1))
        for qid in seen_qids
        if (match := re.fullmatch(r"QINV-(\d+)", qid))
    ]
    next_qid = (max(qid_numbers) if qid_numbers else 0) + 1
    next_order = max(
        (
            int(item.get("order_index") or 0)
            for item in merged
            if str(item.get("order_index") or "").isdigit()
        ),
        default=0,
    )
    qid_counts = Counter(
        str(item.get("qid") or "").strip()
        for item in merged
        if str(item.get("qid") or "").strip()
    )
    for item in merged:
        qid = str(item.get("qid") or "").strip()
        if not qid or qid_counts.get(qid, 0) > 1:
            qid = f"QINV-{next_qid:04d}"
            next_qid += 1
            item["qid"] = qid
        if not item.get("order_index"):
            next_order += 1
            item["order_index"] = next_order
        if item.get("_topic_scope") == "chapter":
            item["_chapter_wide_task"] = True
    refreshed["items"] = merged
    refreshed["stats"] = _inventory_stats(merged)
    return refreshed


def _reconcile_resumed_mined_types(
    mined_types: dict | None, *, inventory: dict, meta: dict,
    use_api: bool,
) -> dict:
    """Bring persisted Type/qid assignments forward to a refreshed inventory."""
    types = _normalize_mined_type_candidate(
        copy.deepcopy((mined_types or {}).get("types") or []),
        inventory,
    )
    duplicates = _duplicate_inventory_assignments(inventory, types)
    if duplicates:
        types, _removed = _apply_exact_once_duplicate_backstop(
            types, inventory)
    missed = _uncovered_inventory_items(inventory, types)
    if missed and use_api:
        types = _recover_missed_type_deltas_via_api(
            meta=meta,
            inventory=inventory,
            types=types,
            max_attempts=2,
        )
        missed = _uncovered_inventory_items(inventory, types)
    if missed:
        types, added = _append_deterministic_type_fallbacks(
            types, missed_items=missed, inventory=inventory)
        if added:
            progress.log(
                f"Added {added} source-grounded Type fallback(s) while "
                "refreshing a resumed inventory.",
                level="warning",
            )
    duplicates = _duplicate_inventory_assignments(inventory, types)
    if duplicates:
        types, _removed = _apply_exact_once_duplicate_backstop(
            types, inventory)
    remaining_missed = _uncovered_inventory_items(inventory, types)
    remaining_duplicates = _duplicate_inventory_assignments(
        inventory, types)
    if remaining_missed or remaining_duplicates:
        raise RuntimeError(
            "resumed checkpoint Type inventory could not be reconciled: "
            f"{len(remaining_missed)} missing, "
            f"{len(remaining_duplicates)} duplicate assignment(s)"
        )
    reconciled = {"types": types}
    ledger = _placement_certification_ledger(mined_types)
    if ledger:
        current_qids = {
            str(item.get("qid") or "").strip()
            for item in (inventory or {}).get("items") or []
            if isinstance(item, dict)
            and str(item.get("qid") or "").strip()
        }
        reconciled[_PLACEMENT_CERTIFICATIONS_KEY] = {
            "version": _PLACEMENT_CERTIFICATION_VERSION,
            "hosts": {
                qid: copy.deepcopy(host)
                for qid, host in ledger["hosts"].items()
                if (
                    qid in current_qids
                    and _placement_certification_entry_is_valid(host)
                )
            },
        }
    return reconciled


def _refresh_inventory_figure_metadata(
    inventory: dict | None, sections: list[dict],
) -> dict:
    """Re-resolve resumed inventory images against the current source file."""
    refreshed = copy.deepcopy(inventory or _empty_inventory())
    items = [
        dict(item) for item in refreshed.get("items") or []
        if isinstance(item, dict)
    ]
    refreshed_items = _attach_explicit_figure_images(items, sections)
    refreshed["items"] = refreshed_items
    if refreshed_items != items:
        refreshed["stats"] = _inventory_stats(refreshed_items)
    return refreshed


def _valid_concept_checkpoint(checkpoint: dict | None) -> bool:
    """Backward-compatible public predicate used by upload/bundle services."""
    return _newest_compatible_concept_checkpoint(checkpoint) is not None


def _checkpoint_order(stage: str) -> int:
    return int(_CONCEPT_CHECKPOINT_STAGES.get(stage, {}).get("order", -1))


def _make_concept_checkpoint(
    stage: str, *, progress_value: float | None = None,
    stage_label: str = "", **payload,
) -> dict:
    spec = _CONCEPT_CHECKPOINT_STAGES[stage]
    value = spec["progress"] if progress_value is None else progress_value
    return {
        "schema_version": _CONCEPT_CHECKPOINT_SCHEMA,
        "stage_schema_version": spec["version"],
        "stage": stage,
        "stage_order": spec["order"],
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "progress": max(0.0, min(1.0, float(value))),
        "stage_label": stage_label or spec["label"],
        **copy.deepcopy(payload),
    }


def _emit_concept_checkpoint(
    checkpoint_callback, stage: str, *, progress_value: float | None = None,
    stage_label: str = "", **payload,
) -> dict:
    checkpoint = _make_concept_checkpoint(
        stage,
        progress_value=progress_value,
        stage_label=stage_label,
        **payload,
    )
    if checkpoint_callback is not None:
        checkpoint_callback(checkpoint)
    return checkpoint


def _chunk_checkpoint_sha256(chunk: dict) -> str:
    return hashlib.sha256(
        str(chunk.get("text") or "").encode("utf-8")
    ).hexdigest()


def _run_live_concept_pre_final_stages(
    mmd_text: str, *,
    subject: str,
    board: str,
    chapter_title: str,
    chunks: list[dict],
    sections: list[dict],
    method_anchors: list[dict],
    headings: list[str],
    source_topic_excerpts: list[dict],
    allow_chapter_title_topic: bool,
    meta: dict,
    artifacts: dict | None,
    resume_checkpoint: dict | None,
    checkpoint_callback,
    allow_final_checkpoint: bool = True,
) -> tuple[list[dict], dict, dict, dict[tuple[str, str], dict]]:
    """Run through Type/activity assignment from the newest compatible stage."""
    resumable_stages = set(_POST_CONCEPT_CHECKPOINT_STAGES)
    if not allow_final_checkpoint:
        resumable_stages.discard("final_content_ready")
    saved = _newest_compatible_concept_checkpoint(
        resume_checkpoint,
        allowed_stages=resumable_stages,
    )
    saved_stage = str(saved.get("stage") or "") if saved else ""
    saved_order = _checkpoint_order(saved_stage)
    out: list[dict] = []
    question_task_inventory: dict = {}
    mined_types: dict = {}
    method_row_snapshot: dict[tuple[str, str], dict] = {}
    skeleton_method_row_snapshot: dict[tuple[str, str], dict] = {}

    if saved:
        label = str(saved.get("stage_label") or saved_stage).strip()
        try:
            saved_progress = float(saved.get("progress") or 0.0)
        except (TypeError, ValueError):
            saved_progress = 0.0
        progress.step(
            f"Concept extraction — resuming from {label}",
            value=saved_progress,
        )
        if saved_stage != "skeleton_chunks":
            out = copy.deepcopy(saved.get("records") or [])
            if not out:
                raise RuntimeError(
                    "saved concept checkpoint is incomplete; replace the file "
                    "or clear the checkpoint before retrying")
        if saved_order >= _checkpoint_order("canonical_skeleton"):
            skeleton_method_row_snapshot = _deserialize_method_row_snapshot(
                saved.get("skeleton_method_row_snapshot"))
        if saved_order >= _checkpoint_order("description_method_snapshot"):
            method_row_snapshot = _deserialize_method_row_snapshot(
                saved.get("method_row_snapshot"))
        if saved_order >= _checkpoint_order("question_inventory"):
            question_task_inventory = copy.deepcopy(
                saved.get("question_task_inventory") or {})
        if saved_order >= _checkpoint_order(_CONCEPT_CHECKPOINT_STAGE):
            mined_types = copy.deepcopy(saved.get("mined_types") or {})
        if saved_order >= _checkpoint_order("question_inventory"):
            restored_inventory = copy.deepcopy(question_task_inventory)
            question_task_inventory = _refresh_inventory_from_source_anchors(
                question_task_inventory, sections)
            inventory_refreshed = (
                _semantic_inventory_items(question_task_inventory)
                != _semantic_inventory_items(restored_inventory)
            )
            if inventory_refreshed:
                progress.log(
                    "Refreshed the resumed Question / Task Inventory from "
                    "current deterministic source anchors.",
                    level="success",
                )
            if (
                saved_order >= _checkpoint_order(
                    _CONCEPT_CHECKPOINT_STAGE)
                and (
                    inventory_refreshed
                    or (
                        saved_stage != "final_content_ready"
                        and bool((mined_types or {}).get("types"))
                    )
                )
            ):
                mined_types = _reconcile_resumed_mined_types(
                    mined_types,
                    inventory=question_task_inventory,
                    meta=meta,
                    use_api=True,
                )
                if (
                    inventory_refreshed
                    and _reviewed_placement_authority_declared(mined_types)
                ):
                    # QIDs are stable identifiers, not a promise that their
                    # semantic host evidence is unchanged. Reassignment below
                    # must create fresh reviewed qid-to-host certifications.
                    _reset_placement_certifications(mined_types)
            if (
                saved_order >= _checkpoint_order(
                    _CONCEPT_CHECKPOINT_STAGE)
                and bool((mined_types or {}).get("types"))
            ):
                # This optional model hint is not part of semantic inventory
                # identity, so clean it even for an otherwise unchanged saved
                # final checkpoint that can skip full Type reconciliation.
                mined_types["types"] = (
                    _clear_nonvisual_diagram_interpretation_hints(
                        list(mined_types.get("types") or []),
                        question_task_inventory,
                    )
                )
        if (
            saved_stage != "final_content_ready"
            and saved_order >= _checkpoint_order("post_type_assignment")
            and not _placement_certification_contract_complete(
                mined_types, question_task_inventory
            )
        ):
            # Deterministic source-anchor rules can discover qids that did not
            # exist when an otherwise-compatible 91% checkpoint was saved.
            # Reconciled Types alone are insufficient: every current qid must
            # pass the exact host assignment boundary and receive a durable
            # certification before finalization.
            progress.log(
                "Refreshed inventory is not fully host-certified; rerunning "
                "Type and Activity/Info Hub assignment from the 81% stage.",
                level="warning",
            )
            out = _strip_types_from_records(copy.deepcopy(out))
            saved_order = _checkpoint_order(_CONCEPT_CHECKPOINT_STAGE)
        progress.log(
            f"Restored checkpoint stage '{saved_stage}' "
            f"({len(out)} materialized concept row(s)).",
            level="success",
        )

    if saved_order < _checkpoint_order("skeleton_complete"):
        out = _extract_skeleton_via_api(
            chunks,
            meta=meta,
            resume_chunks=(
                saved.get("completed_chunks") or []
                if saved_stage == "skeleton_chunks" and saved else []
            ),
            checkpoint_callback=checkpoint_callback,
        )
        if not out:
            raise RuntimeError("live concept extraction returned no rows")
        _emit_concept_checkpoint(
            checkpoint_callback,
            "skeleton_complete",
            records=out,
        )

    if saved_order < _checkpoint_order("canonical_skeleton"):
        out = _canonicalize_method_anchor_tags(
            out, method_anchors, chunk_text=mmd_text, meta=meta)
        skeleton_method_row_snapshot = _snapshot_method_anchor_rows(
            out, method_anchors)
        progress.step("Concept extraction — canonicalizing skeleton", value=0.27)
        out = _scrub_section_numbers(out)
        out = _snap_topics_to_headings(
            out, headings, chapter_title=chapter_title,
            allow_chapter_title_topic=allow_chapter_title_topic)
        out = _consolidate_concepts_via_api(
            out, subject=subject, mmd_text=mmd_text, meta=meta)
        progress.step("Concept extraction — aligning source topics", value=0.35)
        if _topics_look_collapsed(out, headings):
            progress.log(
                f"Topic segregation collapsed: {len(out)} concepts share "
                f"almost one topic while the source has {len(headings)} "
                "section headings — re-segregating topics via API.",
                level="warning",
            )
        if len(headings) >= 3 or (
            headings and _topics_look_collapsed(out, headings)
        ):
            out = _restructure_topics_via_api(
                out, meta=meta,
                source_topic_excerpts=source_topic_excerpts)
        else:
            out = _assign_topics_from_source_evidence(
                out, source_topic_excerpts)
        out = _snap_topics_to_headings(
            out, headings, chapter_title=chapter_title,
            allow_chapter_title_topic=allow_chapter_title_topic)
        out = _recover_missing_topic_concepts_via_api(
            out, meta=meta, source_topic_excerpts=source_topic_excerpts)
        out = _reorder_records_by_source_topics(out, headings)
        out = _restore_method_anchor_rows(
            out, skeleton_method_row_snapshot)
        out = _enforce_method_anchor_topics(out, method_anchors)
        out = _canonicalize_method_anchor_tags(
            out, method_anchors, chunk_text=mmd_text, meta=meta)
        out = _consolidate_task_grounded_fragments_via_api(
            out, meta=meta,
            source_topic_excerpts=source_topic_excerpts,
            method_anchors=method_anchors)
        out = _enforce_method_anchor_topics(out, method_anchors)
        out = _canonicalize_method_anchor_tags(
            out, method_anchors, chunk_text=mmd_text, meta=meta)
        out = _recover_chapter_opening_concepts_via_api(
            out, meta=meta, sections=sections, headings=headings)
        out = _reorder_records_by_source_topics(out, headings)
        _emit_concept_checkpoint(
            checkpoint_callback,
            "canonical_skeleton",
            records=out,
            skeleton_method_row_snapshot=_serialize_method_row_snapshot(
                skeleton_method_row_snapshot),
        )

    if saved_order < _checkpoint_order("description_method_snapshot"):
        progress.step(
            "Concept extraction — refining descriptions", value=0.42)
        out = _refine_descriptions_via_api(
            out, subject=subject, mmd_text=mmd_text, meta=meta,
            sections=sections)
        out = _ensure_method_worked_examples_via_api(
            out, anchors=method_anchors, meta=meta)
        out = _ensure_mastery_lines_via_api(out, meta=meta)
        out = _restore_method_anchor_rows(
            out, skeleton_method_row_snapshot)
        out = _enforce_method_anchor_topics(out, method_anchors)
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
                "post-description method-row restoration could not "
                "snapshot mandatory full-chapter anchors: "
                + ", ".join(
                    anchor["anchor_id"] for anchor in unsnapshotted_anchors)
            )
        if method_row_snapshot:
            snapshotted_rows = {
                (
                    _topic_comparison_key(row.get("topic", "")),
                    bi.normalize_question_text(
                        row.get("concept_title", "")),
                )
                for row in method_row_snapshot.values()
            }
            progress.log(
                f"Snapshotted {len(snapshotted_rows)} refined method "
                f"row(s) covering {len(method_row_snapshot)} mandatory "
                "anchor(s).")
        progress.set_progress(
            0.55, label="Concept extraction — descriptions complete")
        _emit_concept_checkpoint(
            checkpoint_callback,
            "description_method_snapshot",
            records=out,
            method_row_snapshot=_serialize_method_row_snapshot(
                method_row_snapshot),
        )

    if saved_order < _checkpoint_order("question_inventory"):
        progress.step(
            "Concept extraction — inventorying questions and worked examples",
            value=0.58,
        )
        question_task_inventory = _extract_question_task_inventory_via_api(
            meta=meta, sections=sections, records=out)
        progress.set_progress(
            0.70, label="Concept extraction — question inventory complete")
        _emit_concept_checkpoint(
            checkpoint_callback,
            "question_inventory",
            records=out,
            question_task_inventory=question_task_inventory,
            method_row_snapshot=_serialize_method_row_snapshot(
                method_row_snapshot),
        )

    if saved_order < _checkpoint_order(_CONCEPT_CHECKPOINT_STAGE):
        progress.step(
            "Concept extraction — mining reusable Types", value=0.72)
        mined_types = _mine_types_from_inventory_via_api(
            meta=meta, inventory=question_task_inventory)
        mined_types = _consolidate_semantic_types_via_api(
            mined_types, inventory=question_task_inventory, meta=meta)
        concept_count_before_sufficiency = len(out)
        out = _add_missing_type_method_concepts_via_api(
            out, mined_types=mined_types, meta=meta)
        if len(out) > concept_count_before_sufficiency:
            out = _ensure_mastery_lines_via_api(out, meta=meta)
        progress.set_progress(
            0.79, label="Concept extraction — reusable Types mined")
        progress.step(
            "Concept extraction — building culminations", value=0.81)
        out = _build_culminations_via_api(out, meta=meta)
        _emit_concept_checkpoint(
            checkpoint_callback,
            _CONCEPT_CHECKPOINT_STAGE,
            records=out,
            question_task_inventory=question_task_inventory,
            mined_types=mined_types,
            method_row_snapshot=_serialize_method_row_snapshot(
                method_row_snapshot),
        )

    if artifacts is not None:
        artifacts["question_task_inventory"] = copy.deepcopy(
            question_task_inventory)
        artifacts["mined_types"] = copy.deepcopy(mined_types)

    if saved_order < _checkpoint_order("post_type_assignment"):
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
        out = _populate_activity_hubs_via_api(
            out,
            question_task_inventory,
            meta=meta,
            mined_types=mined_types,
        )
        if not _placement_certification_contract_complete(
            mined_types, question_task_inventory
        ):
            raise RuntimeError(
                "post-Type assignment did not certify every source inventory "
                "qid to one durable concept host"
            )
        if artifacts is not None:
            artifacts["mined_types"] = copy.deepcopy(mined_types)
        progress.set_progress(
            0.91, label="Concept extraction — Type assignment complete")
        _emit_concept_checkpoint(
            checkpoint_callback,
            "post_type_assignment",
            records=out,
            question_task_inventory=question_task_inventory,
            mined_types=mined_types,
            method_row_snapshot=_serialize_method_row_snapshot(
                method_row_snapshot),
        )
    else:
        progress.log(
            "Type assignment and activity hubs restored from checkpoint; "
            "continuing at final validation and repair.",
            level="success",
        )

    return out, question_task_inventory, mined_types, method_row_snapshot


def _prepare_final_concept_content(
    out: list[dict], *,
    subject: str,
    board: str,
    chapter_title: str,
    meta: dict,
    mmd_text: str,
    source_sections: list[dict],
    question_task_inventory: dict,
    mined_types: dict,
    method_row_snapshot: dict[tuple[str, str], dict],
    method_anchors: list[dict],
    headings: list[str],
    source_topic_excerpts: list[dict],
    refresh_chapter_wide_assignments: bool = False,
) -> list[dict]:
    """Run every semantic/API finalizer before the deterministic final gate."""
    # A resumed post-Type checkpoint can still predate topic-coverage recovery.
    # Restore any structurally proven source topic before downstream cleanup
    # merges or culminations can make that omission difficult to diagnose.
    out = _recover_missing_topic_concepts_via_api(
        out, meta=meta, source_topic_excerpts=source_topic_excerpts)
    out = _scrub_section_numbers(out)
    out = _merge_concept_records(out)
    out = _dedupe_titles_chapter_wide(out)
    progress.step(
        "Concept extraction — validating and repairing final map",
        value=0.93,
    )
    before_duplicate_merge = copy.deepcopy(out)
    out = _merge_similar_concepts_via_api(out, meta=meta)
    out = _preserve_required_method_rows(before_duplicate_merge, out)
    out = _accept_exact_inventory_type_review(
        before_duplicate_merge,
        out,
        question_task_inventory,
        mined_types,
    )
    out = concept_cleanup.dedupe_similar_titles_chapter_wide(out)
    out = concept_cleanup.filter_review_violations(
        out, subject=subject, board=board, chapter_title=chapter_title)
    out = [
        concept_cleanup.clean_concept_record(
            dict(record), neutralize_artifacts=False)
        for record in out
    ]
    out = _enforce_culminations(out)
    out = _ensure_misconceptions_via_api(out, meta=meta)
    before_final_repair = out
    out = _repair_records_via_api(
        out, meta=meta, stage="final", source_context=mmd_text, strict=False,
        max_attempts=3,
        allowed_source_examples=_inventory_source_examples(
            question_task_inventory))
    out = _preserve_required_method_rows(before_final_repair, out)
    out = _accept_exact_inventory_type_review(
        before_final_repair, out, question_task_inventory, mined_types)
    out = _neutralize_unrepaired_rows(
        out, inventory=question_task_inventory)
    out = _salvage_short_case_examples(
        out, inventory=question_task_inventory)
    out = _neutralize_unrepaired_rows(
        out, inventory=question_task_inventory)
    out = _repair_rendered_inventory_coverage(
        out, question_task_inventory, mined_types)
    coverage_safe_snapshot = copy.deepcopy(out)
    out = cr.refine_chapter(out)
    out = _dedupe_titles_chapter_wide(out)
    out = concept_cleanup.dedupe_similar_titles_chapter_wide(out)
    out = concept_cleanup.filter_review_violations(
        out, subject=subject, board=board, chapter_title=chapter_title)
    out = _ensure_mastery_lines_via_api(out, meta=meta)
    out = _ensure_misconceptions_via_api(out, meta=meta)
    out = _enforce_culminations(out)
    out = _neutralize_unrepaired_rows(
        out, inventory=question_task_inventory)
    out = _accept_topic_safe_type_review(
        coverage_safe_snapshot, out, mined_types)
    out = _accept_exact_inventory_type_review(
        coverage_safe_snapshot, out, question_task_inventory, mined_types)
    out = _restore_method_anchor_rows(out, method_row_snapshot)
    out = _ensure_mastery_lines_via_api(
        out, meta=meta, use_api=False)
    out = _ensure_misconceptions_via_api(out, meta=meta)
    out = cv.ensure_valid_learner_analysis(out)
    out = _enforce_method_anchor_topics(out, method_anchors)
    out = _enforce_culminations(out)
    out = _reorder_records_by_source_topics(out, headings)
    out = cr.renumber_types_continuously(out)
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
    missing_topics = _missing_source_topic_excerpts(
        out, source_topic_excerpts)
    if missing_topics:
        raise RuntimeError(
            "final concept map lost structurally proven source topics: "
            + ", ".join(
                (group.get("topic") or "").strip()
                for group in missing_topics)
        )
    if refresh_chapter_wide_assignments:
        # Old final checkpoints may have a valid-but-stale topic assignment
        # that sent every chapter-end question to one Culmination. Re-run the
        # constrained semantic distribution after restored topics are present,
        # then let exact inventory coverage place each source prompt once.
        question_task_inventory = _assign_chapter_wide_inventory_topics_via_api(
            meta=meta,
            inventory=question_task_inventory,
            records=out,
            source_topic_excerpts=source_topic_excerpts,
        )
        for item in question_task_inventory.get("items") or []:
            item.pop("_topic_scope", None)
    type_topic_violations = _mined_type_topic_violations(
        out, mined_types)
    if type_topic_violations:
        summary = ", ".join(
            f"{item['type_id']}:{item['reason']}"
            for item in type_topic_violations[:10]
        )
        raise RuntimeError(
            "mined Type source-topic validation failed: " + summary)
    uncovered_type_topics = _inventory_topic_type_coverage_violations(
        out, question_task_inventory)
    if uncovered_type_topics:
        raise RuntimeError(
            "source topics with assessable inventory lost all Types: "
            + ", ".join(
                f"{item['topic']} ({item['inventory_items']} items)"
                for item in uncovered_type_topics)
        )
    out = _salvage_short_case_examples(
        out, inventory=question_task_inventory)
    out = _canonicalize_concept_rich_text(out)
    # Final API/salvage passes can emit a learner-analysis label on a newline
    # after the earlier chapter refinement. Normalize that contract immediately
    # before validation without re-running the full chapter refiner: full
    # refinement can structurally alter rows after the immutable METHOD-row
    # snapshot has just been restored.
    out = cv.ensure_valid_learner_analysis(out)
    out = _canonicalize_concept_rich_text(out)
    def final_boundary_report(value: list[dict]) -> dict:
        return cv.validate_concept_rows(
            value,
            **_validation_options("final"),
            allowed_source_examples=_inventory_source_examples(
                question_task_inventory),
            source_text=mmd_text,
        )

    boundary_report = final_boundary_report(out)
    if _fatal_errors(boundary_report):
        # Refinement, row restoration, and deterministic cleanup occur after
        # the first final repair pass. Give every terminal contract—not only
        # copied prose—one last targeted repair window before the closed-world
        # deterministic rebuild/gate below.
        before_boundary_repair = out
        repaired_boundary = _repair_records_via_api(
            out,
            meta=meta,
            stage="final",
            source_context=mmd_text,
            max_attempts=2,
            allowed_source_examples=_inventory_source_examples(
                question_task_inventory),
        )
        out = _accept_exact_inventory_type_review(
            before_boundary_repair,
            repaired_boundary,
            question_task_inventory,
            mined_types,
        )
        out = _preserve_required_method_rows(
            before_boundary_repair, out)
        out = cv.ensure_valid_learner_analysis(out)
        out = _ensure_mastery_lines_via_api(
            out, meta=meta, use_api=False)
        out = _ensure_terminal_culmination_contract(out)
        out = _canonicalize_concept_rich_text(out)
        boundary_report = final_boundary_report(out)
    if any(
        error.get("code") == "source_artifact"
        and error.get("severity") == "error"
        for error in boundary_report["errors"]
    ):
        out = _neutralize_unrepaired_rows(
            out, inventory=question_task_inventory)
    out = _enforce_rendered_inventory_coverage(
        out, question_task_inventory, mined_types)
    out = _normalize_activity_hubs_at_final_boundary(
        out, question_task_inventory, mined_types, meta=meta)
    out = cr.renumber_types_continuously(out)

    type_contract_codes = {
        "missing_type_definition",
        "generic_type_definition",
        "duplicate_type_definition",
    }

    def type_contract_errors(value: list[dict]) -> list[dict]:
        report = cv.validate_concept_rows(
            value,
            allow_types=True,
            require_culmination=True,
            allow_culmination=True,
            allowed_source_examples=_inventory_source_examples(
                question_task_inventory),
            strict_type_hierarchy=True,
        )
        return [
            error for error in report["errors"]
            if error.get("severity") == "error"
            and error.get("code") in type_contract_codes
        ]

    strict_type_errors = type_contract_errors(out)
    inventory_topic_violations = _rendered_inventory_topic_violations(
        out, question_task_inventory, mined_types)
    activity_alignment_violations = (
        _activity_example_hub_alignment_violations(
            out, question_task_inventory)
    )
    type_placement_violations = _rendered_type_placement_violations(
        out, question_task_inventory, mined_types)
    placement_certification_violations = (
        _placement_certification_violations(
            out, question_task_inventory, mined_types)
    )
    concept_type_coverage_violations = (
        _normal_concept_type_coverage_violations(
            out, question_task_inventory, mined_types)
    )
    unexpected_examples = _unexpected_rendered_type_examples(
        out, question_task_inventory)
    misplaced_hub_items = _hub_inventory_examples_in_types(
        out, question_task_inventory)
    hub_contract_violations = _hub_inventory_contract_violations(
        out, question_task_inventory)
    if (
        inventory_topic_violations
        or activity_alignment_violations
        or type_placement_violations
        or placement_certification_violations
        or concept_type_coverage_violations
        or unexpected_examples
        or misplaced_hub_items
        or hub_contract_violations
        or strict_type_errors
    ):
        progress.log(
            "Final cleanup drifted from the closed source inventory or strict "
            "Type contract; rebuilding Types with the ID-constrained "
            "assignment pass.",
            level="warning",
        )
        out = _rebuild_types_after_final_placement_drift(
            out,
            question_task_inventory,
            mined_types,
            meta=meta,
        )
        out = _normalize_activity_hubs_from_inventory(
            out, question_task_inventory, mined_types)
        out = cr.renumber_types_continuously(out)
        inventory_topic_violations = _rendered_inventory_topic_violations(
            out, question_task_inventory, mined_types)
        activity_alignment_violations = (
            _activity_example_hub_alignment_violations(
                out, question_task_inventory)
        )
        type_placement_violations = _rendered_type_placement_violations(
            out, question_task_inventory, mined_types)
        placement_certification_violations = (
            _placement_certification_violations(
                out, question_task_inventory, mined_types)
        )
        concept_type_coverage_violations = (
            _normal_concept_type_coverage_violations(
                out, question_task_inventory, mined_types)
        )
        unexpected_examples = _unexpected_rendered_type_examples(
            out, question_task_inventory)
        misplaced_hub_items = _hub_inventory_examples_in_types(
            out, question_task_inventory)
        hub_contract_violations = _hub_inventory_contract_violations(
            out, question_task_inventory)
        strict_type_errors = type_contract_errors(out)
    if (
        inventory_topic_violations
        or activity_alignment_violations
        or type_placement_violations
        or placement_certification_violations
        or concept_type_coverage_violations
        or unexpected_examples
        or misplaced_hub_items
        or hub_contract_violations
        or strict_type_errors
    ):
        raise RuntimeError(
            "final inventory placement validation failed: "
            f"{len(inventory_topic_violations)} Example(s) outside their "
            "source topic, "
            f"{len(activity_alignment_violations)} assessable Activity "
            "Example(s) separated from their Activity/Info Hub, "
            f"{len(type_placement_violations)} Type host violation(s), "
            f"{len(placement_certification_violations)} certified host "
            "violation(s), "
            f"{len(concept_type_coverage_violations)} applicable concept "
            "Type coverage violation(s), "
            f"{len(unexpected_examples)} unowned Example(s), "
            f"{len(misplaced_hub_items)} Hub item(s) rendered as Types, "
            f"{len(hub_contract_violations)} Hub contract violation(s), "
            f"{len(strict_type_errors)} strict Type definition error(s)"
        )
    # Semantic repair can preserve a stale but syntactically valid image tag.
    # Reconcile the final public Examples to the source registry before this
    # exact map is checkpointed, so a later resume does not reintroduce it.
    return _reconcile_explicit_figure_images(out, source_sections)[0]


def _repair_final_rich_text_via_api(
    records: list[dict], *, meta: dict, inventory: dict | None = None,
    mined_types: dict | None = None,
) -> tuple[list[dict], bool]:
    """Repair late rich-text defects without replaying every semantic stage.

    Older ``final_content_ready`` checkpoints can contain bare TeX because that
    stage used to be persisted before the strict final gate.  Canonicalization
    intentionally cannot infer the boundary of arbitrary bare TeX, so ask the
    existing validation repair pass to wrap only the affected rows.  Exact
    source-Example coverage remains protected while Katex wrapper-only changes
    compare as the same source task.
    """
    def wrapper_only_key(value: str) -> str:
        without_tags = re.sub(
            r"\[/?katex\]", " ", str(value or ""), flags=re.IGNORECASE)
        normalized = re.sub(r"\s+", " ", without_tags).strip()
        # A closing wrapper immediately before punctuation necessarily leaves
        # a removable spacer when the tag itself is stripped.
        return re.sub(r"\s+([,.;:!?])", r"\1", normalized)

    def freeze_defect(value):
        if isinstance(value, dict):
            return tuple(sorted(
                (key, freeze_defect(item)) for key, item in value.items()
            ))
        if isinstance(value, (list, tuple)):
            return tuple(freeze_defect(item) for item in value)
        return value

    def defect_multiset(values) -> list:
        return sorted(
            (freeze_defect(value) for value in values),
            key=repr,
        )

    def added_wrappers_are_math(before: str, after: str) -> bool:
        pattern = re.compile(
            r"\[katex\]\s*(?P<body>.*?)\s*\[/katex\]",
            re.IGNORECASE | re.DOTALL,
        )

        def wrapper_spans(value: str) -> tuple[str, list[tuple[int, int, str]]]:
            source = str(value or "")
            spans: list[tuple[int, int, str]] = []
            source_cursor = 0
            unwrapped_cursor = 0
            for match in pattern.finditer(source):
                unwrapped_cursor += len(source[source_cursor:match.start()])
                body = (match.group("body") or "").strip()
                start = unwrapped_cursor
                unwrapped_cursor += len(body)
                spans.append((start, unwrapped_cursor, body))
                source_cursor = match.end()
            return kr.unwrap_katex(source), spans

        _before_text, existing = wrapper_spans(before)
        after_text, candidate_spans = wrapper_spans(after)
        for start, end, body in candidate_spans:
            identity = (start, end, body)
            if identity in existing:
                existing.remove(identity)
                continue
            if not kr.is_unambiguous_math_expression(body):
                return False
            if (
                start > 0
                and body
                and re.match(r"\w", after_text[start - 1])
                and re.match(r"\w", body[0])
            ):
                return False
            if (
                end < len(after_text)
                and body
                and re.match(r"\w", body[-1])
                and re.match(r"\w", after_text[end])
            ):
                return False
            if (
                "//" in body
                or re.search(
                    r"\b(?:Miscellaneous\s+)?Type\s+\d{1,2}:"
                    r"|\bCase\s+\d{1,2}:"
                    r"|\bExamples?(?:\s+0*\d+)?\s*:",
                    body,
                    re.IGNORECASE,
                )
            ):
                return False
        return not existing

    def formatting_only_review_is_safe(
        baseline: list[dict], candidate: list[dict],
    ) -> bool:
        """Prove a late repair changed only KaTeX wrapper boundaries."""
        if len(baseline) != len(candidate):
            return False
        for before, after in zip(baseline, candidate):
            if _record_key(before) != _record_key(after):
                return False
            before_other = {
                key: value for key, value in before.items()
                if key != "concept_details"
            }
            after_other = {
                key: value for key, value in after.items()
                if key != "concept_details"
            }
            if before_other != after_other:
                return False
            if kr.unwrap_katex(
                before.get("concept_details", "")
            ) != kr.unwrap_katex(after.get("concept_details", "")):
                return False
            if not added_wrappers_are_math(
                before.get("concept_details", ""),
                after.get("concept_details", ""),
            ):
                return False

        if _rendered_inventory_coverage_defects(
            baseline, inventory
        ) != _rendered_inventory_coverage_defects(candidate, inventory):
            return False
        comparisons = (
            (
                _rendered_inventory_topic_violations(
                    baseline, inventory, mined_types),
                _rendered_inventory_topic_violations(
                    candidate, inventory, mined_types),
            ),
            (
                _activity_example_hub_alignment_violations(
                    baseline, inventory),
                _activity_example_hub_alignment_violations(
                    candidate, inventory),
            ),
            (
                _hub_inventory_examples_in_types(baseline, inventory),
                _hub_inventory_examples_in_types(candidate, inventory),
            ),
        )
        return all(
            defect_multiset(before) == defect_multiset(after)
            for before, after in comparisons
        )

    validation_args = {
        "allow_types": True,
        "require_culmination": True,
        "allow_culmination": True,
        "allowed_source_examples": _inventory_source_examples(inventory),
    }
    original = copy.deepcopy(records)
    repaired = copy.deepcopy(records)
    initial_error_count = 0
    for attempt in range(1, 3):
        report = cv.validate_concept_rows(repaired, **validation_args)
        rich_text_errors = [
            error for error in report["errors"]
            if (
                error.get("severity") == "error"
                and error.get("code") == "rich_text_format"
            )
        ]
        if not rich_text_errors:
            break
        if not initial_error_count:
            initial_error_count = len(rich_text_errors)
            progress.log(
                "Final rich-text validation found "
                f"{initial_error_count} malformed row(s); repairing only "
                "their rich-text fields before deposit.",
                level="warning",
            )

        failed_indexes = sorted({
            error["row_index"] for error in rich_text_errors
            if isinstance(error.get("row_index"), int)
            and error["row_index"] >= 0
        })
        failed_rows = [
            repaired[index] for index in failed_indexes
            if index < len(repaired)
        ]
        if not failed_rows:
            break

        deterministic = copy.deepcopy(repaired)
        deterministic_applied = 0
        for row_index in failed_indexes:
            if row_index >= len(deterministic):
                continue
            current_details = str(
                repaired[row_index].get("concept_details", "") or "")
            defects = set(kr.rich_text_issues(current_details))
            if (
                not defects
                or not defects.issubset({
                    "raw_latex", "raw_math_expression",
                })
            ):
                continue
            candidate_details = kr.repair_unwrapped_math(current_details)
            if (
                candidate_details == current_details
                or kr.rich_text_issues(candidate_details)
                or kr.unwrap_katex(candidate_details)
                != kr.unwrap_katex(current_details)
            ):
                continue
            deterministic[row_index]["concept_details"] = candidate_details
            deterministic_applied += 1

        if deterministic_applied:
            if (
                deterministic != repaired
                and formatting_only_review_is_safe(repaired, deterministic)
            ):
                repaired = deterministic
                progress.log(
                    "Final rich-text repair deterministically wrapped "
                    f"unambiguous math in {deterministic_applied} row(s).",
                )
                report = cv.validate_concept_rows(
                    repaired, **validation_args)
                rich_text_errors = [
                    error for error in report["errors"]
                    if (
                        error.get("severity") == "error"
                        and error.get("code") == "rich_text_format"
                    )
                ]
                if not rich_text_errors:
                    break
                failed_indexes = sorted({
                    error["row_index"] for error in rich_text_errors
                    if isinstance(error.get("row_index"), int)
                    and error["row_index"] >= 0
                })
                failed_rows = [
                    repaired[index] for index in failed_indexes
                    if index < len(repaired)
                ]
                if not failed_rows:
                    break
            elif deterministic != repaired:
                progress.log(
                    "Rejected deterministic final rich-text repair because "
                    "it changed a protected row or source-inventory invariant.",
                    level="warning",
                )

        import json as _json

        user = (
            _metadata_block(meta)
            + "\nStage: final rich-text formatting\nValidation errors:\n"
            + _json.dumps(rich_text_errors, ensure_ascii=False)
            + "\nFailed rows:\n"
            + _json.dumps(
                {"rows": _records_to_api_rows(failed_rows)},
                ensure_ascii=False,
            )
        )
        data = _openai_json(
            prompts.get_text("concepts.repair.system")
            + "\n\n"
            + kr.PROMPT_PREAMBLE,
            user,
            purpose="concept_validation",
        )
        candidates = _concept_rows_to_records(data)
        if not candidates:
            progress.log(
                "Final rich-text repair returned no rows.",
                level="warning",
            )
            break

        candidate_by_key = {
            _record_key(candidate): candidate
            for candidate in candidates
        }
        candidate_by_title = {
            bi.normalize_question_text(
                candidate.get("concept_title", "")
            ): candidate
            for candidate in candidates
        }
        next_records = copy.deepcopy(repaired)
        applied = 0
        for position, row_index in enumerate(failed_indexes):
            if row_index >= len(next_records):
                continue
            current = repaired[row_index]
            candidate = None
            if len(candidates) == len(failed_indexes):
                positional = candidates[position]
                if (
                    _record_key(positional) == _record_key(current)
                    or bi.normalize_question_text(
                        positional.get("concept_title", "")
                    )
                    == bi.normalize_question_text(
                        current.get("concept_title", "")
                    )
                ):
                    candidate = positional
            if candidate is None:
                candidate = (
                    candidate_by_key.get(_record_key(current))
                    or candidate_by_title.get(
                        bi.normalize_question_text(
                            current.get("concept_title", "")
                        )
                    )
                )
            details = (
                candidate.get("concept_details", "")
                if isinstance(candidate, dict)
                else ""
            )
            if not str(details or "").strip():
                continue
            candidate_row = copy.deepcopy(current)
            candidate_row["concept_details"] = details
            candidate_row = _canonicalize_concept_rich_text(
                [candidate_row])[0]
            details = candidate_row["concept_details"]
            if wrapper_only_key(details) != wrapper_only_key(
                current.get("concept_details", "")
            ):
                progress.log(
                    "Rejected final rich-text repair for "
                    f"row_index={row_index}: response changed content beyond "
                    "Katex wrappers.",
                    level="warning",
                )
                continue
            # Rich-text repair is not a semantic edit: keep topic, title,
            # parent, keywords, evidence, prose, formulas, and every other row
            # field immutable.
            next_records[row_index]["concept_details"] = details
            applied += 1
        if not applied:
            progress.log(
                "Final rich-text repair returned no matching row details.",
                level="warning",
            )
            break

        next_records = _canonicalize_concept_rich_text(next_records)
        if not formatting_only_review_is_safe(repaired, next_records):
            progress.log(
                "Rejected final rich-text repair because it changed a "
                "protected row or source-inventory invariant.",
                level="warning",
            )
            next_records = repaired
        next_records = _accept_exact_inventory_type_review(
            repaired, next_records, inventory, mined_types)
        if next_records == repaired:
            progress.log(
                "Final rich-text repair made no safe formatting change.",
                level="warning",
            )
            break
        repaired = next_records
        progress.log(
            f"Final rich-text repair updated {applied} row(s) on "
            f"attempt {attempt}.",
        )

    if not initial_error_count:
        return records, False
    remaining_report = cv.validate_concept_rows(repaired, **validation_args)
    remaining = [
        error for error in remaining_report["errors"]
        if (
            error.get("severity") == "error"
            and error.get("code") == "rich_text_format"
        )
    ]
    if remaining:
        progress.log(
            "Final rich-text repair left "
            f"{len(remaining)} malformed row(s); strict validation will "
            "report their exact locations.",
            level="warning",
        )
    else:
        progress.log(
            "Final rich-text repair cleared all malformed rows.",
            level="success",
        )
    return repaired, repaired != original


def concepts_from_mmd(
    mmd_text: str, *, subject: str = "", board: str = "", grade: str = "",
    unit: str = "", chapter_title: str = "", chapter_id: int | str | None = None,
    chapter_code: str = "", learning_kind: str = "Post",
    live: bool | None = None, artifacts: dict | None = None,
    resume_checkpoint: dict | None = None,
    checkpoint_callback=None,
    completion_progress: float = 1.0,
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
        if _apply_headingless_chapter_topic_fallback(
            sections, chapter_title
        ):
            progress.log(
                "No usable teaching-section headings were present; using the "
                "selected chapter title as the source topic.",
                level="warning",
            )
        method_anchors = _method_coverage_anchors(sections)
        headings = _topic_headings(sections)
        source_topic_excerpts = _group_source_topic_excerpts(sections)
        saved_final = _newest_compatible_concept_checkpoint(
            resume_checkpoint,
            allowed_stages={"final_content_ready"},
        )
        final_checkpoint_refresh_reasons = _final_checkpoint_refresh_reasons(
            saved_final,
            sections=sections,
            source_topic_excerpts=source_topic_excerpts,
        )
        if final_checkpoint_refresh_reasons:
            progress.log(
                "Final checkpoint is structurally incomplete; resuming from "
                "the preceding stage: "
                + "; ".join(final_checkpoint_refresh_reasons),
                level="warning",
            )
            saved_final = None
        allow_chapter_title_topic = _chapter_title_is_main_topic(
            sections, chapter_title)
        progress.log("Concept generation metadata received:\n" + _metadata_block(meta))
        progress.log(
            f"Extracting concepts from {len(mmd_text):,} chars "
            f"across {len(chunks)} section-aware chunk(s) "
            f"(subject: {subject or 'general'}).")
        (
            out,
            question_task_inventory,
            mined_types,
            method_row_snapshot,
        ) = _run_live_concept_pre_final_stages(
            mmd_text,
            subject=subject,
            board=board,
            chapter_title=chapter_title,
            chunks=chunks,
            sections=sections,
            method_anchors=method_anchors,
            headings=headings,
            source_topic_excerpts=source_topic_excerpts,
            allow_chapter_title_topic=allow_chapter_title_topic,
            meta=meta,
            artifacts=artifacts,
            resume_checkpoint=resume_checkpoint,
            checkpoint_callback=checkpoint_callback,
            allow_final_checkpoint=not final_checkpoint_refresh_reasons,
        )
        inventory_figure_metadata_refreshed = False
        if saved_final:
            refreshed_inventory = _refresh_inventory_figure_metadata(
                question_task_inventory, sections)
            inventory_figure_metadata_refreshed = (
                refreshed_inventory != question_task_inventory
            )
            question_task_inventory = refreshed_inventory
            if (
                inventory_figure_metadata_refreshed
                and artifacts is not None
            ):
                artifacts["question_task_inventory"] = copy.deepcopy(
                    question_task_inventory)
        if final_checkpoint_refresh_reasons:
            question_task_inventory = _refresh_inventory_from_source_anchors(
                question_task_inventory, sections)
            if artifacts is not None:
                artifacts["question_task_inventory"] = copy.deepcopy(
                    question_task_inventory)
        final_checkpoint_changed = (
            not bool(saved_final) or inventory_figure_metadata_refreshed
        )
        if saved_final:
            progress.log(
                "Restored final content checkpoint; semantic/API repair stays "
                "skipped unless strict formatting finds a targeted repair.",
                level="success",
            )
        else:
            out = _prepare_final_concept_content(
                out,
                subject=subject,
                board=board,
                chapter_title=chapter_title,
                meta=meta,
                mmd_text=mmd_text,
                source_sections=sections,
                question_task_inventory=question_task_inventory,
                mined_types=mined_types,
                method_row_snapshot=method_row_snapshot,
                method_anchors=method_anchors,
                headings=headings,
                source_topic_excerpts=source_topic_excerpts,
                refresh_chapter_wide_assignments=bool(
                    final_checkpoint_refresh_reasons),
            )
        if artifacts is not None:
            artifacts["mined_types"] = copy.deepcopy(mined_types)
        # Older terminal checkpoints can predate the canonical mastery/recap
        # contract. Upgrade those fields deterministically so resume remains
        # API-free while the exact rows sent to deposit satisfy today's gate.
        before_content_contracts = copy.deepcopy(out)
        out = _ensure_mastery_lines_via_api(
            out, meta=meta, use_api=False)
        out = _ensure_terminal_culmination_contract(out)
        if out != before_content_contracts:
            final_checkpoint_changed = True
        before_final_normalization = copy.deepcopy(out)
        out = _canonicalize_concept_rich_text(out)
        if out != before_final_normalization:
            final_checkpoint_changed = True
        # A saved final checkpoint bypasses the semantic finalizer.  Its source
        # inventory remains authoritative, so restore any source Example that
        # an older finalizer/checkpoint omitted before declaring the resumed map
        # valid.  This is a deterministic placement repair only: no API call is
        # made and no question wording is invented.  Fresh maps already pass
        # through this exact repair in their finalizer.
        resumed_coverage_repaired = False
        if saved_final:
            pre_resume_repair = copy.deepcopy(out)
            coverage_before_resume_repair = (
                _rendered_inventory_coverage_defects(
                    out, question_task_inventory))
            out = _enforce_rendered_inventory_coverage(
                out, question_task_inventory, mined_types)
            resumed_coverage_repaired = out != pre_resume_repair
            if resumed_coverage_repaired:
                progress.log(
                    "Repaired saved final checkpoint source-task coverage: "
                    f"{len(coverage_before_resume_repair['missing'])} missing, "
                    f"{len(coverage_before_resume_repair['duplicate'])} duplicate.",
                    level="success",
                )
                out = cr.renumber_types_continuously(out)
                out = cv.ensure_valid_learner_analysis(out)
                out = _canonicalize_concept_rich_text(out)
                final_checkpoint_changed = True
        # A saved final checkpoint bypasses the finalizer above.  Correct any
        # stale Figure tag from the source registry immediately before the
        # outer final gate, without spending another API request.
        out, reconciled_figure_examples = _reconcile_explicit_figure_images(
            out, sections)
        post_figure_coverage = _rendered_inventory_coverage_defects(
            out, question_task_inventory)
        if (
            post_figure_coverage["missing"]
            or post_figure_coverage["duplicate"]
        ):
            # Replacing a stale figure tag can make an older near-match become
            # identical to a source Example restored just above. Re-run the
            # exact-once gate so the corrected copies are deterministically
            # deduplicated before the terminal checkpoint is validated.
            out = _enforce_rendered_inventory_coverage(
                out, question_task_inventory, mined_types)
            out = cr.renumber_types_continuously(out)
            out = cv.ensure_valid_learner_analysis(out)
            out = _canonicalize_concept_rich_text(out)
            resumed_coverage_repaired = True
        if reconciled_figure_examples or resumed_coverage_repaired:
            progress.log(
                (
                    "Reconciled "
                    f"{reconciled_figure_examples} rendered Figure Example(s) "
                    "against the source registry."
                    if reconciled_figure_examples
                    else "Repaired saved final checkpoint coverage."
                ),
                level="success",
            )
            final_checkpoint_changed = True

        try:
            if saved_final:
                # Fresh rows were normalized at the end of the semantic
                # finalizer. Only restored terminal rows bypassed that boundary
                # and need this additional deterministic pass. Any unresolved
                # certified host is part of the stale 98% payload and must
                # enter the discard/resume path below.
                before_hub_normalization = copy.deepcopy(out)
                out = _normalize_activity_hubs_at_final_boundary(
                    out, question_task_inventory, mined_types)
                if out != before_hub_normalization:
                    final_checkpoint_changed = True

            out, rich_text_repaired = _repair_final_rich_text_via_api(
                out,
                meta=meta,
                inventory=question_task_inventory,
                mined_types=mined_types,
            )
            if rich_text_repaired:
                final_checkpoint_changed = True
                # The helper preserves row identity and rejects any Types
                # rewrite that changes exact source coverage. Reconcile
                # canonical Figure tags once more, then send these exact rows
                # to the strict gate.
                out, repaired_figure_examples = (
                    _reconcile_explicit_figure_images(out, sections)
                )
                if repaired_figure_examples:
                    progress.log(
                        "Reconciled "
                        f"{repaired_figure_examples} Figure Example(s) after "
                        "rich-text repair.",
                        level="success",
                    )
            _validate_final_or_raise(
                out,
                stage="final",
                inventory=question_task_inventory,
                mined_types=mined_types,
                method_anchors=method_anchors,
                source_text=mmd_text,
            )
        except RuntimeError:
            if not saved_final:
                raise
            # A final checkpoint produced by an older deployment may violate a
            # rule that cannot be repaired safely in place. Reuse the stage
            # immediately before it (or regenerate when no prior stage exists)
            # instead of reloading the same rejected 98% payload forever.
            progress.log(
                "Saved final checkpoint did not pass strict validation; "
                "resuming from the preceding checkpoint instead of retrying "
                "the same 98% content.",
                level="warning",
            )
            if checkpoint_callback is not None:
                checkpoint_callback({
                    "checkpoint_action": "discard_stage",
                    "stage": "final_content_ready",
                    "reason": "strict terminal validation failed",
                })
            return concepts_from_mmd(
                mmd_text,
                subject=subject,
                board=board,
                grade=grade,
                unit=unit,
                chapter_title=chapter_title,
                chapter_id=chapter_id,
                chapter_code=chapter_code,
                learning_kind=learning_kind,
                live=True,
                artifacts=artifacts,
                resume_checkpoint=_without_concept_checkpoint_stage(
                    resume_checkpoint, "final_content_ready"),
                checkpoint_callback=checkpoint_callback,
                completion_progress=completion_progress,
            )
        # ``final_content_ready`` is a promise that the exact materialized rows
        # passed the strict outer gate.  Persist only after that promise is true
        # so a retry cannot loop forever on the same invalid 98% checkpoint.
        if final_checkpoint_changed:
            _emit_concept_checkpoint(
                checkpoint_callback,
                "final_content_ready",
                records=out,
                question_task_inventory=question_task_inventory,
                mined_types=mined_types,
                method_row_snapshot=_serialize_method_row_snapshot(
                    method_row_snapshot),
            )
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
        progress.set_progress(
            max(0.0, min(1.0, float(completion_progress))),
            label="Concept extraction complete",
        )
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

CONCEPT DESCRIPTION FORMAT (MANDATORY): one string, sections separated by " // ".
Every concept ends with exactly one learner-analysis section (Types may be
inserted before it):
Description: <what the student should already know; 2-4 short lines; must not
teach the chapter> // Misconception/ Error Analysis: Misconceptions: <commonly
held incorrect belief>; Error Analysis: <distinct mistaken action>
When Types are useful, classify ALL distinct prerequisite-check varieties using
zero-padded labels exactly "Type 01:", "Case 01:", and "Example 01:":
Type 01: <variety title> Case 01: <declarative sub-type definition>
Example 01: <full check question> Example 02: <another question for this Case>
Case 02: <definition> Example 01: <question> Type 02: <variety> ...
Cases define the variation and are never questions; questions appear only as
numbered Examples, restarting at Example 01 for each Case.
Description is the important lesson-planning input: source/syllabus-grounded,
clear, and concise (2-4 compact sentences, not a chapter dump). Include Types
only when the prerequisite has assessable check formats; pure vocabulary recall
may omit Types. Every concept MUST include both labelled meanings inside the
single ``Misconception/ Error Analysis`` section. Misconceptions are commonly
held incorrect beliefs or interpretations. Error Analysis describes a distinct
procedural, computational, representational, or reasoning mistake and names the
learner explicitly (for example, "Students may omit ..."). Never emit separate
top-level Misconceptions or Error Analysis sections. Use canonical order:
Description, Activity/Info Hub when present, Types when present, then the one
combined analysis section; never write N/A/None/filler. Restart at Type 01 per concept;
continuous renumbering happens downstream.
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
Keep the same schema and canonical ``Description: ... // Types: ... //
Misconception/ Error Analysis: Misconceptions: ...; Error Analysis: ...``
order. Types is optional. Every concept must contain both distinct labelled
meanings inside that one top-level analysis section; never emit separate
top-level sections. Error Analysis must name the learner explicitly and state
the mistaken action. Where Types exist, use zero-padded Type/Case/Example
labels, make each Case a declarative sub-type definition, put full questions
only in Examples, and restart Example numbering at 01 per Case. Keep the tag
(FL|NU|VC|RS|GR).
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
    resume_checkpoint: dict | None = None,
    checkpoint_callback=None,
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
                "// Error Analysis: Students may skip verifying this prerequisite "
                "before attempting a task that depends on it."
            ),
            "keywords": r.get("keywords", ""),
        } for r in rows if not cr.is_culmination(r.get("concept_title", ""))]
        return _ensure_parent_concepts(_exclude_current_chapter_concepts(pre, rows))

    saved = _newest_compatible_concept_checkpoint(
        resume_checkpoint,
        allowed_stages=_PRE_DERIVATION_CHECKPOINT_STAGES,
    )
    saved_stage = str(saved.get("stage") or "") if saved else ""
    saved_order = _checkpoint_order(saved_stage)
    if saved:
        try:
            restored_progress = float(saved.get("progress") or 0.0)
        except (TypeError, ValueError):
            restored_progress = 0.0
        progress.set_progress(
            restored_progress,
            label=(
                saved.get("stage_label")
                or "Pre-learning checkpoint restored"
            ),
        )
        progress.log(
            "Restored pre-learning checkpoint "
            f"'{saved.get('stage_label') or saved_stage}'.",
            level="success",
        )
    if saved_stage == "pre_learner_analysis":
        return copy.deepcopy(saved.get("records") or [])

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
    if saved_order >= _checkpoint_order("pre_derivation_audited"):
        final = copy.deepcopy(saved.get("pre_audited") or {})
    else:
        if saved_order >= _checkpoint_order("pre_derivation_draft"):
            draft = copy.deepcopy(saved.get("pre_draft") or {})
        else:
            progress.step(
                "Pre-learning â€” deriving prerequisite map",
                value=0.981,
            )
            progress.log(
                "Generating the prerequisite map from the completed chapter. "
                "This final AI step can take a few minutes for a large source.",
            )
            draft = _openai_json(
                system, user, purpose="pre_learning")
            if not draft.get("topics"):
                raise RuntimeError(
                    "live pre-learning derivation returned no topics")
            _emit_concept_checkpoint(
                checkpoint_callback,
                "pre_derivation_draft",
                records=rows,
                pre_draft=draft,
            )
            progress.set_progress(
                0.985,
                label="Pre-learning dependency draft complete",
            )

        # Stage 2: syllabus boundary auditor (replaces violating rows in place).
        import json as _json
        progress.step(
            "Pre-learning â€” auditing syllabus boundaries",
            value=0.988,
        )
        audited = _openai_json(
            prompts.get_text("prelearning.auditor"),
            f"Chapter: {chapter_title} | Subject: {subject} | Grade: {grade} | "
            f"Board: {board} | Unit: {unit}\n\nDRAFT:\n"
            + _json.dumps(draft)[:120_000],
            purpose="pre_learning",
        )
        final = audited if audited.get("topics") else draft
        _emit_concept_checkpoint(
            checkpoint_callback,
            "pre_derivation_audited",
            records=rows,
            pre_audited=final,
        )
        progress.set_progress(
            0.992,
            label="Pre-learning syllabus audit complete",
        )

    out = _exclude_current_chapter_concepts(_flatten_pre_topics(final), rows)
    if not out:
        raise RuntimeError("live pre-learning derivation returned no concepts")
    pre_meta = _metadata(
        subject=subject,
        grade=grade,
        board=board,
        chapter_title=chapter_title,
        unit=unit,
        learning_kind="Pre",
    )
    progress.step(
        "Pre-learning â€” writing learner analysis",
        value=0.995,
    )
    out = _ensure_misconceptions_via_api(out, meta=pre_meta)
    out = cv.ensure_valid_learner_analysis(out)
    _emit_concept_checkpoint(
        checkpoint_callback,
        "pre_learner_analysis",
        records=out,
        base_records=rows,
    )
    progress.set_progress(
        0.998,
        label="Pre-learning learner analysis complete",
    )
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
                "// Error Analysis: Students may skip verifying this prerequisite "
                "before attempting a task that depends on it."
            ),
            "keywords": c.keywords,
        })
    return out

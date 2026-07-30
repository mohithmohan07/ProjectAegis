Warning: truncated output (original token count: 210169)
Total output lines: 20129

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
from . import semantic_confidence_policy as confidence_policy
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
    "concepts.reusable_type_host_convergence.system",
    category=_CONCEPTS_CAT,
    label="Reusable Type host convergence prompt",
    default="""\
Consolidate every supplied original reusable Type onto exactly one semantically
valid concept host shared by all of its Cases. Return ONLY strict JSON:
{"assignments":[{"type_id":"TYPE-0001","concept_id":"CONCEPT-0001","reason":"this host teaches every Case's assessed method and output"}]}.

Rules:
- Return every supplied original type_id exactly once; these IDs deliberately
  have no ``::CASE`` suffix. Invent no IDs.
- Choose only from that Type's allowed_concept_ids.
- The selected concept title and Description must teach the assessed action,
  inputs, method/approach, constraints, and expected output for every Case.
- Formula or keyword overlap alone is not entailment.
- If no supplied concept safely teaches every Case, omit that Type. The caller
  will preserve its already-reviewed Case hosts as explicitly distinct public
  Types instead of forcing an unsafe move.
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
                # The outer public prefix already says ``Activity``. Returning
                # ``Activity — <opening words>`` here caused both the kind and
                # the opening words to be rendered twice:
                # ``Activity — Activity — Look at Fig...: Look at Fig...``.
                # Use only the source-derived identity; the note builder below
                # removes that identity from the beginning of the gist.
                return identity[:100].strip(" .:-")
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
    r"(?im)^[ \t]*(?:\\item[ \t]*\[[ \t]…110169 tokens truncated… "or near-duplicates within a family):\n- "
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
    """Concise, complete-title contract for one topic culmination.

    The Description already carries the exhaustive ``Recap of A, B, ...``
    inventory. Repeating an arbitrary first three concept names in the title
    made titles both very long and semantically incomplete. The stable topic
    name is the correct public label for the complete recap.
    """
    topic = next((
        re.sub(r"\s+", " ", str(record.get("topic") or "")).strip()
        for record in topic_records
        if str(record.get("topic") or "").strip()
    ), "")
    if not topic:
        return "Culmination - Topic Recap"
    # Topic model columns allow 255 chars, but a title should stay readily
    # scannable in the workbook. Preserve words when bounding an anomalous OCR
    # heading rather than silently overflowing the cell with concept names.
    body = topic
    if len(body) > 96:
        body = body[:96].rsplit(" ", 1)[0].rstrip(" ,;:-") or body[:96]
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
    if (
        checkpoint.get("semantic_confidence_policy")
        != confidence_policy.cache_identity()
    ):
        # A final checkpoint skips the semantic/API finalizer on resume.  It is
        # therefore reusable only under the exact policy that approved it.
        # Earlier checkpoints remain valuable: the caller falls back to the
        # newest preceding stage and reruns semantic review at the current gate.
        reasons.append("semantic confidence policy changed")
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
        "semantic_confidence_policy": confidence_policy.cache_identity(),
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
    out = _disambiguate_certified_split_type_cases(
        out, question_task_inventory, mined_types)
    out = cr.renumber_types_continuously(out)

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
    out = _disambiguate_certified_split_type_cases(
        out, question_task_inventory, mined_types)
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

            before_type_heading_repair = copy.deepcopy(out)
            out = _disambiguate_certified_split_type_cases(
                out, question_task_inventory, mined_types)
            if out != before_type_heading_repair:
                out = cr.renumber_types_continuously(out)
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
            before_final_type_heading_repair = copy.deepcopy(out)
            out = _disambiguate_certified_split_type_cases(
                out, question_task_inventory, mined_types)
            if out != before_final_type_heading_repair:
                out = cr.renumber_types_continuously(out)
                final_checkpoint_changed = True
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

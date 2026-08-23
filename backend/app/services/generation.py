YªçŠx-®éÜj×¢ëiºÚ+Š§j[h‘éÜ¢éíß^¶ï}µëzo+^²‰¢¶×"""Content generation: questions from concepts, concepts from MMD.

All functions have a dry path (deterministic, no API keys â€” used for the MVP
and tests) and a live hook that delegates to the vendored OpenAI-backed
scripts. The dry path is intentionally realistic: it returns fully-populated
records so the post-generation pipeline and the canonical writer are always
exercised end to end.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import random
import re
import contextvars
import threading
import unicodedata
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from aegis_pipeline.openai_policy import (
    OpenAIPurpose,
    chat_request_policy,
    is_unsupported_reasoning_effort_error,
    note_unsupported_reasoning_effort,
)

from .. import bulk_import as bi
from .. import config, models
from . import concept_cleanup
from . import concept_validator as cv
from . import containers
from . import katex_rules as kr
from . import concept_refiner as cr
from . import grounding_certificate
from . import identity
from . import prompts
from . import progress
from . import semantic_confidence_policy as confidence_policy
from . import source_topic_decision
from . import type_granularity_decision
from .semantic_recovery import (
    HumanDecisionRequired,
    ProviderResponseContractError,
)
# Imported for its prompt registrations (assessment.* keys used by _identify_system).
from . import assessment_prompts as _assessment_prompts_registration  # noqa: F401
del _assessment_prompts_registration

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


def question_label(concept: models.Concept, n: int) -> str:
    """THE one producer of D1/Q14's ``Question = <ConceptID> Q##`` pattern.

    e.g. ``10CBMA_Circles_1f4a9c2b_PL_T01_C03 Q03``.

    Nothing here is re-derived from a title. The base is the concept's
    PERSISTED ``machine_id`` through ``identity.machine_id_for_concept``, so a
    concept that already carries an id keeps it and a concept that does not
    gets one minted and persisted on the spot (T4-3/T4-7). The signature is
    unchanged â€” the helper resolves its own session with ``object_session`` â€”
    so every call site keeps its arguments.

    ``_topic_index`` is GONE, and its own docstring said why this is the fix:
    it existed only because this label "carries a literal ``_PL_`` segment and
    no lane discriminator", so lane-scoped numbering made a Pre label
    byte-identical to a Post one and ``assessment_release_service`` silently
    skipped the colliding question (R4). The ``PL|PrL`` token now sits inside
    the id, so the reason to keep a chapter-wide topic index went with it.

    Two live minters were also the reason S5's ``_next_label_index`` max-scan
    and T5-2's ``UploadRefused`` comparison could both read the wrong label
    family for one concept. There is one now.

    The legacy family is GRANDFATHERED, not migrated (T5/R5): a concept whose
    published questions carry the old shape keeps them, the new family has a
    different prefix, numbering restarts at 1 inside the new family, and no
    label is ever reassigned.
    """
    return f"{identity.machine_id_for_concept(concept)} Q{n:02d}"


# --------------------------------------------------------------------------- #
# Questions from concepts (Build Assessments - concept mapping path)
# --------------------------------------------------------------------------- #

def _objective_answers(concept: models.Concept) -> list[dict]:
    correct = f"{concept.concept_title} (correct)"
    if _is_math(concept):
        correct = f"{correct} â€” {_sample_equation(concept)}"
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
    # Keyword cells are NOT rich text â€” they hold raw KaTeX / plain text.
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
    stems = _DRY_STEMS[skill]
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
    pool = _RUBRIC_POINTS[difficulty]
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
    marks: float | None = None,
    question_duration: float | None = None,
    math_keyboard: str | None = None,
    start_index: int = 1,
    live: bool | None = None,
    appears_in: str = "",
) -> list[dict]:
    """Return ``count`` question dicts for one concept under one blueprint cell."""
    use_live = config.use_live_generation() if live is None else live
    if not use_live:
        config.require_generation_live()
    if question_type not in _SHEET_KINDS:
        raise ValueError(f"unknown recorded question_type {question_type!r}")
    if cognitive_skill not in bi.COGNITIVE_SKILLS:
        raise ValueError(
            f"unknown recorded cognitive skill {cognitive_skill!r}")
    if difficulty not in bi.DIFFICULTY_LEVELS:
        raise ValueError(f"unknown recorded difficulty {difficulty!r}")
    if not str(category or "").strip():
        raise ValueError("question_category must be recorded before generation")
    marks = _positive_recorded_number(marks, "marks")
    question_duration = _positive_recorded_number(
        question_duration, "question_duration")
    math_keyboard = _recorded_math_keyboard(math_keyboard, question_type)
    if use_live:
        return _live_questions_for_concept(
            concept, question_type=question_type, cognitive_skill=cognitive_skill,
            difficulty=difficulty, category=category, count=count,
            marks=marks, question_duration=question_duration,
            math_keyboard=math_keyboard, start_index=start_index,
            appears_in=appears_in,
        )
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
            "question_duration": question_duration,
            "math_keyboard": math_keyboard,
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
                correct = f"{correct} â€” {_sample_equation(concept)}"
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
    marks: float, question_duration: float, math_keyboard: str,
    appears_in: str = "",
) -> list[dict]:
    """Live generation: one author call with exact blueprint-cell coverage."""
    from . import assessment_prompts as ap

    chapter = concept.topic.chapter
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
                "question_category": category,
                "cognitive_skills": cognitive_skill,
                "question_source": bi.QUESTION_SOURCE_DEFAULT,
                "level_of_difficulty": difficulty,
                # The blueprint-cell kernel owns these three semantic values.
                # Model output cannot silently replace or default them.
                "marks": marks,
                "question_duration": question_duration,
                "math_keyboard": math_keyboard,
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

    authored = _openai_json(system, user, purpose="assessment_generation")
    raw_questions = authored.get("questions") if isinstance(authored, dict) else None
    if not isinstance(raw_questions, list) or len(raw_questions) != count:
        raise RuntimeError(
            "assessment generation did not fulfill the exact blueprint cell "
            f"count ({len(raw_questions) if isinstance(raw_questions, list) else 0}"
            f"/{count}); refusing partial persistence"
        )
    records = _parse(authored)
    if len(records) != count:
        raise RuntimeError(
            "assessment generation lost a blueprint-cell obligation while "
            "normalizing the recorded author response"
        )
    return records


# --------------------------------------------------------------------------- #
# Questions identified from an uploaded document (Build Assessments - upload path)
# --------------------------------------------------------------------------- #

# Question types the upload path can deposit. "auto" means: detect each
# question's type from the document and absorb a mix (the default).
_SHEET_KINDS = ("objective", "subjective", "descriptive")


def _positive_recorded_number(value: object, field: str) -> float:
    """Validate a recorded positive finite number without manufacturing it."""

    if isinstance(value, bool):
        raise ValueError(f"{field} must be a recorded finite positive number")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(
            f"{field} must be a recorded finite positive number") from None
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{field} must be a recorded finite positive number")
    return number


def _recorded_math_keyboard(value: object, question_type: str) -> str:
    """Validate the recorded keyboard contract for a generated question."""

    recorded = str(value or "").strip()
    if question_type in {"subjective", "descriptive"}:
        if recorded not in {"Yes", "No"}:
            raise ValueError(
                "math_keyboard must be a recorded Yes/No value for "
                f"{question_type} questions"
            )
        return recorded
    if recorded and recorded not in {"Yes", "No"}:
        raise ValueError("math_keyboard must be blank, Yes, or No")
    return recorded


def _normalize_sheet_kind(value: str) -> str:
    v = (value or "").strip().lower()
    if v in _SHEET_KINDS:
        return v
    # Map a few common synonyms the model might emit.
    aliases = {"mcq": "objective", "objective question": "objective",
               "short answer": "subjective", "short": "subjective",
               "long answer": "descriptive", "long": "descriptive",
               "essay": "descriptive"}
    normalized = aliases.get(v)
    if normalized is None:
        raise ValueError(
            "sheet_kind must be a recorded objective, subjective, or "
            f"descriptive value (got {value!r})"
        )
    return normalized


def _offline_assessment_identification_authority():
    """Production has no local semantic author for uploaded questions.

    Tests may replace this seam with an explicit fixture authority.  Returning
    ``None`` here is deliberate: a dry production request must fail closed
    instead of manufacturing type, category, axes, marks, or timing.
    """

    return None


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
    authority = _offline_assessment_identification_authority()
    if authority is None:
        raise config.LiveRequiredError(
            "Uploaded assessment identification requires a live semantic "
            "authority; no local type, category, axis, marks, duration, or "
            "keyboard defaults are permitted."
        )
    authored = authority({
        "mmd_text": mmd_text,
        "upload_type": upload_type,
        "question_type": question_type,
        "textbook_mode": textbook_mode,
    })
    rows = authored.get("questions") if isinstance(authored, dict) else authored
    if not isinstance(rows, list):
        raise RuntimeError(
            "offline assessment identification authority returned no rows")
    auto = question_type == "auto"
    records = [
        record
        for row in rows
        if (record := _identify_row_to_record(
            row, auto=auto, question_type=question_type)) is not None
    ]
    if not records:
        raise RuntimeError(
            "offline assessment identification authority returned no questions"
        )
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


_IDENTIFY_CAT = "Build Assessments Â· upload extraction"

_TYPE_HINTS = {
    "objective": "OBJECTIVE â€” MCQ / fill-in-the-blank. For MCQs emit 3-4 "
                 "options in display order with exactly one correct_answer = "
                 "'Yes'. The order maps to lowercase paper labels a), b), "
                 "c), d); do not include those labels in answer_content.",
    "subjective": "SUBJECTIVE â€” short answer; emit mark-wise rubric points "
                  "whose weightages sum to the marks.",
    "descriptive": "DESCRIPTIVE â€” long answer; emit mark-wise rubric points "
                   "(and sub_questions for multi-part questions) summing to marks.",
}
for _k, _v in _TYPE_HINTS.items():
    prompts.register(f"identify.type_hint.{_k}", category=_IDENTIFY_CAT,
                     label=f"Upload type hint Â· {_k}", default=_v)

prompts.register(
    "identify.intent.extract", category=_IDENTIFY_CAT,
    label="Upload intent Â· extract existing questions",
    default="EXTRACT every assessment question already present in the document. "
            "Preserve each question's original wording and intent â€” do NOT invent "
            "new questions. When a question's options, answer, solution or marking "
            "scheme is present, capture it faithfully; otherwise leave answers empty.")

prompts.register(
    "identify.intent.create", category=_IDENTIFY_CAT,
    label="Upload intent Â· create new questions",
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
            "QUESTION TYPES â€” the document may contain a MIX of types. For EACH "
            "question, set \"sheet_kind\" to the type that best fits it and shape "
            "it accordingly:\n"
            f"- objective: {prompts.get_text('identify.type_hint.objective')}\n"
            f"- subjective: {prompts.get_text('identify.type_hint.subjective')}\n"
            f"- descriptive: {prompts.get_text('identify.type_hint.descriptive')}\n"
            "Preserve a question's natural type â€” do NOT force everything into one "
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
    category = str(row.get("question_category") or "").strip()
    if not category:
        raise ValueError("identified question has no recorded question_category")
    skill = bi.normalize_cognitive_skills(row.get("cognitive_skills") or "")
    if skill not in bi.COGNITIVE_SKILLS:
        raise ValueError(
            "identified question has no valid recorded cognitive_skills")
    difficulty = bi.normalize_difficulty(
        row.get("level_of_difficulty") or "")
    if difficulty not in bi.DIFFICULTY_LEVELS:
        raise ValueError(
            "identified question has no valid recorded level_of_difficulty")
    marks = _positive_recorded_number(row.get("marks"), "marks")
    duration = _positive_recorded_number(
        row.get("question_duration"), "question_duration")
    math_keyboard = _recorded_math_keyboard(
        row.get("math_keyboard"), kind)
    return {
        "sheet_kind": kind,
        "question_category": category,
        "cognitive_skills": skill,
        "question_source": bi.QUESTION_SOURCE_DEFAULT,
        "level_of_difficulty": difficulty,
        "marks": marks,
        "question_duration": duration,
        "math_keyboard": math_keyboard,
        "question": question,
        "question_appears_in": "",
        "question_text": (str(row.get("question_text", "")).strip()
                          or bi.to_plain_text(question)),
        "display_answer": row.get("display_answer", ""),
        "answer_explanation": row.get("answer_explanation", ""),
        "answers": _coerce_answers(row.get("answers", []), kind),
        "sub_questions": (
            row.get("sub_questions") or []
            if kind == "descriptive" else []
        ),
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
    tail += (
        " Every question must also contain a nonempty question_category, one "
        "exact standard cognitive_skills value, one exact standard "
        "level_of_difficulty value, finite positive marks, finite positive "
        "question_duration, and math_keyboard='Yes' or 'No' for subjective "
        "and descriptive questions (blank is allowed only for objective)."
    )
    chunks = _split_mmd_into_chunks(mmd_text)
    progress.log(
        f"Identifying questions from {len(mmd_text):,} chars across "
        f"{len(chunks)} chunk(s) (type: {question_type}, "
        f"{'extract' if extract else 'create'}).")

    from .phase3 import kernel as _kernel

    def _identify_chunk(numbered: tuple[int, str]) -> dict:
        i, chunk = numbered
        user = f"DOCUMENT (MMD) â€” section {i} of {len(chunks)}:\n{chunk}\n\n{tail}"
        return _openai_json(system, user, purpose="source_extraction")

    records: list[dict] = []
    seen: set[str] = set()

    def _apply_chunk(index: int, _item, data) -> None:
        # Applied in chunk order whatever order the parallel reads
        # finished in, so cross-chunk dedup keeps the same survivor â€”
        # the first occurrence by document order â€” as a sequential run.
        i = index + 1
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
                raise RuntimeError(
                    "uploaded question identification reached its safety cap "
                    f"of {_IDENTIFY_SAFETY_CAP}; refusing a truncated success "
                    "because source-question coverage is not proven complete"
                )
        progress.step(
            f"Question identification â€” chunk {i}/{len(chunks)} read",
            value=i / max(len(chunks), 1))
        progress.log(f"  chunk {i}/{len(chunks)}: {added} new questions")

    _kernel.parallel_map_in_order(
        list(enumerate(chunks, start=1)),
        _identify_chunk,
        max_workers=config.source_chunk_workers(),
        labels=[f"Identify Â· chunk {i}/{len(chunks)}"
                for i in range(1, len(chunks) + 1)],
        announce="Question identification",
        on_result=_apply_chunk,
    )
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
# dangling refs) â€” no Type renumbering or group-column output at this stage.

_CONCEPTS_CAT = "Build Concepts Â· post-learning extraction"

prompts.register(
    "concepts.name_templates.math", category=_CONCEPTS_CAT,
    label="Concept naming guidance (math/physics)",
    default="""\
   Name each concept after the specific idea it teaches â€” use the chapter's own
   vocabulary. Vary sentence structure across siblings (do NOT repeat a shared
   opener like "Properties ofâ€¦" or "Applications ofâ€¦" on multiple rows). Good
   names read like precise textbook sub-headings, not formulaic labels.""")

prompts.register(
    "concepts.name_templates.descriptive", category=_CONCEPTS_CAT,
    label="Concept naming guidance (other subjects)",
    default="""\
   Name each concept after the specific idea it teaches â€” use the chapter's own
   vocabulary. Vary sentence structure across siblings (do NOT repeat a shared
   opener like "Structure and Function ofâ€¦" or "Importance ofâ€¦" on multiple rows).
   Good names read like precise textbook sub-headings, not formulaic labels.""")

prompts.register(
    "concepts.types_guidance.math", category=_CONCEPTS_CAT,
    label="Types classification guidance (math-heavy subjects)",
    default="""\
   Types classify EVERY distinct assessable question/task pattern under the
   concept â€” numerical, formula, proof, construction, graph, diagram, reasoning,
   or word-problem patterns as the source demands. Mine the Question / Task
   Inventory first; fold each reusable assessable pattern into the concept it
   assesses. Major concepts that exercises assess MUST carry their own Types â€”
   do not park them only under Culmination.
   A Type is one solving/answering/task pattern. A Case is a DEFINED conceptual
   sub-type named by the learning objective / problem variety (what is given,
   what is asked, with what constraint) â€” never a vague label like
   "Definition of â€¦", never a raw question, and never a textbook Activity title.
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
   MUST carry their own Types â€” do not dump them only under Culmination.
   A Type is one reusable assessable format. A Case is a DEFINED conceptual
   sub-type named by the learning objective (what is given, what is asked, with
   what constraint or context) â€” never "Definition of â€¦", never a raw question,
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
    default="90-180 words, source-grounded and authorable â€” this text is the "
            "basis for books, worksheets, notes, slides, and interactive "
            "content, so it must TEACH, not summarize: define the idea "
            "precisely, state the key rule/property or method with its "
            "formula and what each symbol means, give the conditions and "
            "when/why to use it, show the reasoning that makes it work, and "
            "include a compact worked cue or representative values where "
            "they make the method concrete")

prompts.register(
    "concepts.detail.descriptive", category=_CONCEPTS_CAT,
    label="Description guidance (other subjects)",
    default="90-180 words, source-grounded and authorable â€” this text is the "
            "basis for books, worksheets, notes, slides, and interactive "
            "content, so it must TEACH, not summarize: explain the idea "
            "fully with its key characteristics/process/relationship, name "
            "the specific people, places, dates, terms, and causal links "
            "involved, explain why it matters in the chapter's argument, "
            "and include a compact example or illustration where it makes "
            "the idea concrete")

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
- Name each topic EXACTLY as the textbook section heading reads â€” strip any
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
  Ex 2.1, etc.) in topic or concept names â€” use descriptive words only.
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
  Format â€” use zero-padded numeric labels exactly "Type 01:", "Case 01:", and
  a numbered "Example 01:" line for every concrete source question; restart
  Example numbering at 01 for every Case:
  Types: Type 01: <pattern definition> Case 01: <defined sub-type>
  Example 01: <full source question> Example 02: <another full source question>
  Case 02: <defined sub-type> Example 01: <...> Type 02: <next pattern> ...
  Restart at Type 01 within each concept â€” they are renumbered continuously
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
- Do NOT mention groups, group columns, or assessment labels â€” not required here.

TOPIC CULMINATION:
- The LAST concept of every topic is exactly one culmination row that integrates
  that section's ideas (named "Culmination - ..."). Its Description will be set to
  "Recap". Culmination Types are ONLY mixed multi-concept application, revision,
  and synthesis questions â€” NEVER full textbook activities, experiment write-ups,
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

QUALITY RULES (universal â€” apply to ANY chapter/subject; never invent
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

Your job (apply ALL of these intelligently â€” do not rely on downstream code):

1. **De-duplicate & de-redundancy.** Merge or drop concepts whose descriptions
   overlap heavily. Each distinct idea appears exactly once in the chapter.
   Two concepts that interpret the SAME source artifact (the same print,
   figure, map, passage, or account) from slightly different angles are ONE
   concept â€” merge them; a shared artifact never carries two sibling rows.

2. **Distinct naming.** Rewrite sibling concept names so no two share the same
   leading phrase or formulaic opener. Names must be specific, not templated.

3. **Strip section numbers.** Remove decimal/section prefixes (1., 1.1, 1.2,
   2.3, Exercise 1.1, Ex 2.1, etc.) from topic and concept names â€” words only.

4. **Types (critical â€” preserve and enrich, never strip).** Types are how
   teachers segregate question varieties under each concept â€” generate them
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
   activities / experiments / discussion cases â€” never Culmination dumps.

9. **Chapter source.** When CHAPTER SOURCE text is provided, mine it for all
   assessable question/task patterns to populate Types under the concepts they test.

10. **Description quality.** Descriptions are used for lesson planning,
    assessments, and downstream content. Keep them source-grounded, 2-4 compact
    sentences, clear enough to teach from, and not overloaded with every detail.

Return the full refined chapter map â€” same schema, improved quality. Do NOT
remove Types sections â€” a dedicated Types pass follows; preserve any Types already
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
4. Format â€” zero-padded numeric labels exactly "Type 01:", "Case 01:", and an
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
   constraint) â€” never raw questions; list a Case ONLY when a concrete source
   example exists; never invent empty Case placeholders.
8. Example lines MUST quote the full source question/task verbatim â€” do not
   shorten, paraphrase, or abbreviate; teachers execute from these cells.
   When the question needs a figure/diagram, keep the figure reference AND
   embed the source image in canonical rich text right after it, e.g.
   "(Refer fig. 11.1) [img src=\"https://full-public-image-url\"
   alt=\"Circuit in Fig. 11.1\"]".
9. Mine ALL assessable problems from the source; skipping exercises, in-text
   checkpoint questions, or activities defeats homework / in-class /
   board-teaching categorisation downstream.
10. Place each question under a concept that is taught at or before the point
    of the chapter where the question appears â€” NEVER attach a question to an
    earlier concept when it actually assesses later material. Do not dump most
    exercise questions onto the last concept or Culmination.
11. Textbook ACTIVITY / experiment / classroom discussion tasks belong in the
    concept's Activity/Info Hub section (after Description, before Types) â€” not
    as Cases and not as Culmination Types. Case names must be conceptual problem
    varieties (named by the assessed skill, givens, ask, or constraint), never
    "Definition of â€¦" and never Activity / discussion-case titles.
12. Culmination rows MUST include Types only for mixed multi-concept application,
    revision, and synthesis. Major concepts that exercises assess must keep their
    own dedicated Types â€” do not park those only under Culmination.
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
- Let the chapter's own teaching structure decide how many concepts there
  are â€” there is no quota. A concept is something a teacher would plan and
  teach as one coherent lesson segment: SUBSTANTIAL, self-standing, and
  worth a full description. Judge each candidate row: if its description
  would be thin â€” one or two sentences restating a heading, a single fact,
  a single formula variant, or a sliver of a bigger explanation â€” it is NOT
  a concept; fold it into the concept it belongs to and let that concept's
  description carry the full depth.
- A concept is a durable teaching/mastery objective, not every term, example,
  subheading, exercise prompt, case, or factual detail.
- When several definitions, examples, sub-types, steps, or procedures serve
  one reusable objective, merge them under the same concept and write ONE
  rich description covering them together. Three adjacent rows that would
  share the same mastery goal are one concept, not three.
- Keep SEPARATE concepts only when the textbook teaches distinct country
  cases, people, events, laws, methods, or processes that a teacher would
  genuinely lesson-plan apart â€” never split one explanation into fragments.
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
  are NOT separate topics or concepts â€” capture them later under Activity/Info
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
  concepts) under that topic â€” NEVER a topic of its own.
- An unnumbered chapter title or book title is NEVER a topic. Exception: when a
  numbered MAIN section intentionally has the same title as the chapter, that
  numbered section is a valid topic. Filing every concept under one unnumbered
  umbrella topic is still a defect.
- When the text spans several main section headings it MUST produce several
  topics, in the same reading order. Cover EVERY main section of the chapter â€”
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
- concept_description starts with "Description:" and TEACHES the concept in
  a full teaching paragraph (roughly 5-9 substantive sentences): name the
  key people, places, rules, formulas, relationships, and the reasoning
  that connects them, drawn from the source. This text becomes the basis
  for books, worksheets, notes, slides, and interactive content, so it
  must carry enough substance that a writer could author those materials
  from the description alone. A description that merely restates the title
  or lists a bare fact is a defect â€” if you cannot write a substantive
  description, the row is not a real concept and belongs inside a
  neighbouring one.
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
- Every recovered concept must be TAUGHT by the supplied excerpt itself. Do
  not write concepts for material the excerpt only previews, mentions, or
  asks about â€” detailed definitions belong to the sections that teach them.
  When the missing topic is thin framing material (a chapter opening), ONE
  modest framing concept grounded in its own text is the correct recovery,
  not a set of definition concepts imported from later sections.
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
  appear before section 1) â€” do not fold them away into a later section concept.
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
  legitimately have very few concepts â€” even a single one â€” when the source is
  thin; never invent filler to pad a parent.
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
  when/why it is used; and the reasoning that makes it work. Ground it in the
  source: name the key people, places, dates, formulas, quantities,
  conditions, and causal links that a teacher needs â€” do not stop at a vague
  one-sentence gloss. This text is the basis for books, worksheets, notes,
  slides, and interactive content: write a full teaching paragraph with
  enough substance that a writer could author those materials from the
  Description alone.
- Never cite textbook section numbers in Description (for example "Section
  5.2" or "Â§2.1"). State the actual idea instead.
- END every Description with a mastery statement on its OWN line â€” a literal
  line break (\\n) followed by exactly this format:
  Achieving Mastery: <one short sentence stating what the learner can do when this concept is mastered>
  Example ending: "...\\nAchieving Mastery: Using the midpoint property to set up the smaller triangles correctly."
- Use 45-90 words unless the concept is very simple. Never leave a Description
  truncated mid-sentence.
- Paraphrase source prose into original teacher-facing language. Do not copy a
  long contiguous sentence or paragraph from the textbook into Description.
- For a poem, story, or other literary source, quote ONLY the exact line or
  two the teaching point needs, clearly marked as a quotation â€” never the
  full poem, a whole stanza run, or a long passage (owner ruling,
  2026-08-21). The chapter carries the text; the Description teaches it.
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
- Write the mastery statement exactly ONCE, at the end of the Description â€”
  never repeat it inside or after either learner-analysis section.
- No N/A, None, Not applicable, or placeholder text.
- No source artifacts such as MMD, Example 3, Fig 2, Table 1, Exercise 1.1, or
  page references. When the source text cites one, substitute the full actual
  content it points to (the real numbers, expression, conditions, or task) â€”
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
  givens, constraints, and expected responseâ€”not from the subject label.
- One Case = one defined conceptual sub-type named by the learning objective
  (givens / ask / constraint / context). Never "Definition of â€¦", never a raw
  question, and never a textbook Activity or discussion-case title. Multiple
  source questions with the same action/object/method belong to one Type;
  differences in givens, ask, representation, or constraint become Cases under
  that Type.
- Major concepts assessed by exercises MUST receive their own Types â€” do not
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
{"items":[{"qid":"QINV-0001","source_kind":"worked_example|solved_example|exercise|intext_question|checkpoint_question|activity|info_hub|mcq|fill_blank|true_false|match|assertion_reason|diagram_task|map_task|table_task|graph_task|source_task|case_task|passage_task|grammar_task|writing_task|experiment_task|coding_task|long_answer|short_answer|other","source_label":"","parent_source_label":"","topic_hint":"","page_hint":"","block_ids":[],"raw_task":"","raw_solution_or_answer":"","normalized_task":"","shared_context":"","subpart_label":"","options":[],"image_urls":[],"content_objects":{"numbers":[],"variables":[],"equations":[],"coordinates":[],"ratios":[],"diagrams":[],"graphs":[],"tables":[],"maps":[],"passages":[],"sources":[],"experiments":[],"observations":[],"characters":[],"events":[],"dates":[],"places":[],"terms":[],"definitions":[],"processes":[],"comparisons":[],"causes":[],"effects":[],"code_snippets":[],"grammar_items":[],"unknowns":[],"given_values":[],"conditions":[]},"requires_visual":false,"requires_context":false,"order_index":1}],"stats":{"worked_examples":0,"solved_examples":0,"exercise_questions":0,"checkpoint_questions":0,"activities":0,"objective_items":0,"subjective_items":0,"descriptive_items":0,"subparts":0,"visual_tasks":0,"table_or_graph_tasks":0,"source_or_passage_tasks":0,"total_inventory_items":0}}.

COVERAGE IS MANDATORY (most important rule):
- Extract EVERY assessable question/task from the first line to the last,
  including the chapter opening / pre-section narrative.
- Each numbered problem, intext question, think-and-reflect prompt, and worked
  example is its OWN item â€” never summarize an exercise set or question list
  into one item.
- Preserve every numbered parent question and its source order/provenance.
  When lettered/roman subparts are independently answerable (for example,
  separate "Write a note on ..." targets), emit stable child inventory items
  carrying ``parent_qid`` and the complete shared instruction plus that one
  child ask. Those child items may later become Cases on different concepts.
  Keep dependent subparts that share data, a passage, a figure, intermediate
  results, or one integrated response as ONE atomic inventory item.
- In-text CHECKPOINT questions (boxed "?" questions, "Let's recall",
  "Check your progress", mid-section question boxes) are inventory items
  exactly like end-of-chapter exercises. Chapters typically carry a dozen or
  more of them â€” walk every section and capture each one. Missing even one
  checkpoint is a defect.
- Picture-/source-/map-based questions (including opening-page source analysis
  of chapter illustrations, prints, maps, or passages) are inventory items with
  source_kind "source_task" / "diagram_task" / "map_task" as appropriate â€”
  never skip them as "introductory".
- Textbook ACTIVITY / experiment / classroom-discussion blocks are inventory
  items with source_kind "activity" or "experiment_task" as appropriate â€” they
  later feed Activity/Info Hub on the related teaching concept, never Culmination.
- INFO HUB blocks â€” boxed asides, "do you know?" panels, biography boxes,
  source excerpts, and similar enrichment boxes that carry no student ask â€”
  are inventory items with source_kind "info_hub". raw_task carries the
  complete hub content verbatim. They later feed Activity/Info Hub on the
  concept whose material they enrich; never treat them as questions and
  never skip them as decoration.
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
- raw_task must carry the COMPLETE question wording verbatim â€” never truncate,
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
- When the question depends on a figure/diagram/table image, copy the
  image URL(s) from the source
  into image_urls AND keep the figure reference in raw_task.
- Set topic_hint to the nearest MAIN section heading (or "[Chapter opening]"
  for pre-section items) so later placement stays in reading order.
- ``topic_hint`` routes the individual inventory item; it is not a boundary on
  reuse. Two items from different topics may instantiate one reusable Type,
  while their Cases remain independently routed to their own concepts.
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
what is asked, with what constraint) â€” never a raw question. An Example is one
concrete source question that instantiates a Case, copied in full.

Return ONLY strict JSON:
{"types":[{"type_id":"TYPE-0001","type_title":"","type_description":"","task_pattern":"","source_question_ids":["QINV-0001"],"case_prompts":[{"case_id":"CASE-0001","case_title":"","examples":[{"source_question_id":"QINV-0001","example_prompt":""}],"case_signature":"","concept_match_hint":"","parent_concept_match_hint":"","topic_match_hint":"","difficulty_hint":"Basic|Intermediate|Advanced","cognitive_skill_hint":"","subject_skill_hint":"","is_activity":false,"placement_scope":"normal|mixed_synthesis|cross_topic_synthesis"}]}]}.

COVERAGE IS MANDATORY (most important rule):
- EVERY inventory item MUST appear in EXACTLY ONE Type's source_question_ids
  AND EXACTLY ONE example_prompt under a Case. The same qid/question must
  never appear in two Types, two Cases, or twice in the same Case.
- NEVER skip an item because it looks trivial, routine, descriptive, or hard to
  classify. If an item fits no existing Type, CREATE a new Type for it.
- In-text checkpoint questions, boxed "?" questions, and textbook activities
  count exactly like exercise questions â€” every one of them must be classified.
- Coverage and classification quality are both mandatory. Never drop an item,
  but do not create one Type per question when several questions instantiate
  the same reusable pattern; group them into Cases/Examples.
- A missed question is a defect; an unnecessary one-question Type is also a
  classification defect when that question fits an existing reusable pattern.

APT TAXONOMY SIZE (judge it, no quota):
- The Type list should read like the handful of question patterns a teacher
  would naturally recognize in this chapter's exercises â€” reusable patterns,
  not a near-relabelling of the question list. A taxonomy approaching one
  Type per question means the patterns were not actually mined.
- Before finalizing, re-read your own Type list and merge Types whose
  answering method and expected response form are the same pattern in
  different clothes. A Type with a single Case holding a single Example is
  legitimate only when that question is genuinely one of a kind in this
  chapter.
- Let the questions decide how many Types and Cases exist. Do not pad the
  count up, and do not force dissimilar patterns together to shrink it.

Rules:
- One inventory item maps to exactly one best-fit Type. If it combines several
  skills, choose the Type that most directly assesses the final ask, or create
  one integrated Type for that mixed skill â€” never duplicate the question.
- A Type owns only the reusable answering method: action, representation,
  constraints, and expected response form. It may span concepts and textbook
  topics. Every Case owns its own concept/topic route and must assess one
  granular destination; Cases on different concepts remain separate under the
  same Type identity.
- Split Types when questions share a formula or surface procedure but assess
  different concepts. In particular, direct formula calculations and
  contextual/real-life modeling or applications belong in separate Types when
  the concept map teaches them as separate rows.
- Classify every worked, numerical, contextual, interpretive, source-based,
  procedural, practical, and real-life task by its assessed action, object,
  representation, givens, constraint, and ask. Preserve the complete prompt as
  its Example, but never copy solutions or worked-answer steps.
- Merge genuinely identical answering methods across ``topic_hint`` values.
  Topic/content similarity alone is not enough, but a different country,
  person, event, or chapter section is not a reason to create a new Type when
  the required method and answer form are the same.
- Group items that share the same pattern under one Type, but do not force
  dissimilar items together just to keep the Type count low.
- Do not merge different academic, solving, answering, writing, interpretation,
  coding, experimental, or practical patterns.
- Preserve source_question_ids and source traceability in debug JSON.
- Do not include source labels in public concept_details.
- Set Case-level ``is_activity`` from the source role. Activity/experiment/
  classroom-discussion Cases are delivered through Activity/Info Hub, but may
  share a reusable Type identity with ordinary Cases that use the same method.
  Case titles for non-activity Cases must be conceptual problem varieties,
  never Activity names.

CASE WORDING (each Case must be properly defined):
- case_title DEFINES the sub-type: what is given to the student, what must be
  done, and the distinguishing condition â€” named by givens / ask / constraint /
  representation, never by a chapter-specific Activity title. A case_title is
  NEVER a raw question.
- Create a separate Case only when a given/asked/constraint combination is a
  genuinely different variety of the pattern; near-identical variations of
  the same variety share one Case (a Case can hold several Examples).
- A multi-part question (sub-parts a), b), c) â€¦) is ONE question and ONE
  Example â€” never spread its parts across Cases or Types. Classify it by
  everything it asks together; parts spanning several concepts make it a
  culmination-level question, not several questions.
- Advanced placement: when one Case or task requires methods from more than one
  source topic, set its topic_match_hint to the LATEST of those topics, never an
  earlier one, because a learner can only attempt it after reaching that topic.
  Needing an earlier topic's definition or formula makes that topic a
  prerequisite, not the owner. This holds for every subject and chapter, not
  only where sections look numerically ordered.
- Retrospective reference is the exception: if the later topic only mentions or
  illustrates the earlier material, rather than being needed to attempt the
  task, the task stays with the topic that teaches it and the later topic keeps
  its own separate task about the illustration. Ask which direction the
  dependence runs. Chronological or thematic chapters refer backwards often, so
  appearing later in the book does not by itself mean later in teaching.
- Set every Case's topic_match_hint, concept_match_hint,
  parent_concept_match_hint, difficulty/cognitive/subject skill hints,
  is_activity, and placement_scope independently. Set placement_scope to
  "normal" when that Case assesses one concept.
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
- Include EVERY inventory question that fits a Case as its own example_prompt â€”
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
  type_title, type_description, or task_pattern â€” always substitute the real
  content those labels point to. Figure references WITH their image URL are
  allowed and encouraged.

TYPE WORDING (each Type must be properly defined):
- type_title must be a precise, self-explanatory pattern name that states the
  action, the object, and the condition/method, e.g. "Finding the Unknown
  Exponent Using the Product Law" or "Identifying the Tense of an Underlined
  Verb in a Sentence" â€” never vague labels like "Exponent Problems",
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
{"types":[{"type_id":"TYPE-0001","type_title":"","type_description":"","task_pattern":"","source_question_ids":[],"case_prompts":[{"case_id":"CASE-0001","case_title":"","examples":[{"source_question_id":"QINV-0001","example_prompt":""}],"case_signature":"","concept_match_hint":"","parent_concept_match_hint":"","topic_match_hint":"","difficulty_hint":"","cognitive_skill_hint":"","subject_skill_hint":"","is_activity":false,"placement_scope":"normal|mixed_synthesis|cross_topic_synthesis"}]}]}.

Rules:
- Every supplied source_question_id must remain exactly once in exactly one
  Example and one Type. Never add, remove, duplicate, paraphrase, or truncate
  an Example.
- This is Type-only consolidation. Preserve every Case/QID's topic, concept,
  parent, activity role, placement scope, difficulty/cognitive/subject skill
  hints, case_signature, and source-owned requires_visual value exactly. Move
  Cases intact under a shared operator Type; never average, generalize, or
  rewrite their destinations to make a merge possible.
- Merge Types when their verb/action, assessed object, required method,
  representation, constraints, and expected output describe the same reusable
  assessment pattern, even when their titles are paraphrases.
- Keep different methods or learning objectives separate. Shared notation,
  formula, difficulty, context, person, country, or surface wording alone does
  not prove equivalence.
- Topic, concept, and activity differences belong to Cases and do not block a
  merge. Never merge two Cases into one when their route or placement scope is
  different, and never merge genuinely incompatible answering methods.
- When merging, choose one precise action-object-method title and definition;
  preserve all distinct Cases in source order.
- Do not create generic fallback titles such as "Answering a Checkpoint
  Question", "Direct Questions", "Word Problems", or "Miscellaneous".
""")

prompts.register(
    "concepts.type_granularity_critic.system", category=_CONCEPTS_CAT,
    label="Human-directed Type consolidation critic prompt",
    default="""\
Independently review a human-directed consolidation of assessment Types.
Return ONLY strict JSON:
{"verdict":"accept|reject","confidence":0.0,"reason":""}.

Accept only when the candidate merges genuinely reusable assessment patterns
without erasing a different method, assessed object, representation,
constraint, or expected output. Topic and concept differences are legitimate
Case routes, not reasons for separate Types. Reject one-Type-per-question
renaming, superficial title-only changes,
generic catch-all Types, and over-merging based only on shared context,
difficulty, notation, person, country, or formula. Every supplied QID and Case
must remain semantically accounted for. Reject any change to a QID/Case's
topic/concept/parent route, activity role, placement scope, difficulty,
cognitive skill, subject skill, case_signature, or source-owned requires_visual
value. Treat the human direction as the goal, not as evidence
that the proposed merge is correct. Confidence is your confidence in the
verdict, from 0 to 1.
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
{"types":[{"type_id":"TYPE-0001 or NEW-TYPE-0001","type_title":"","type_description":"","task_pattern":"","source_question_ids":["QINV-0001"],"case_prompts":[{"case_id":"existing CASE id or NEW-CASE-0001","case_title":"","examples":[{"source_question_id":"QINV-0001","example_prompt":""}],"case_signature":"","concept_match_hint":"","parent_concept_match_hint":"","topic_match_hint":"","difficulty_hint":"Basic|Intermediate|Advanced","cognitive_skill_hint":"","subject_skill_hint":"","is_activity":false,"placement_scope":"normal|mixed_synthesis|cross_topic_synthesis"}]}]}.

DELTA RULES:
- Use an existing type_id (and optionally an existing case_id) to append only
  new Cases/Examples to that Type. Its existing metadata is immutable.
- If no existing Type fits, create a new operator Type with a new temporary
  type_id and complete, precise Type and Case metadata.
- Claim only qids present in MISSED INVENTORY ITEMS. Each claimed qid must occur
  exactly once in source_question_ids and exactly once as an Example.
- Every returned Example must copy that missed item's complete source task
  verbatim, including all givens, subparts, conditions, context, figure
  references, and image URLs. Never include a solution or answer.
- A missed item may attach to an existing Type from another topic when its
  answering method is genuinely the same. Emit a new Case with its own exact
  topic/concept/parent/activity/placement route; never rewrite existing Cases.
- Create a new Type only for a distinct method, representation, constraint, or
  expected responseâ€”not merely for a different content target or host.
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
  questions come from a LATER part of the chapter to an EARLIER concept â€” e.g.
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
  not "Definition of â€¦" and not a textbook Activity title.
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
- Advanced placement: when one task requires methods from more than one source
  topic, it belongs to the LATEST of those topics, never an earlier one, because
  a learner can only attempt it after reaching that topic. Solving it may need
  an earlier topic's definition or formula; that makes the earlier topic a
  prerequisite, not the owner. Apply this to every subject and chapter, not only
  where the topics look numerically ordered.
- Retrospective reference is the exception: if the later topic only mentions or
  illustrates the earlier material, rather than being needed to attempt the
  task, the task stays with the topic that teaches it and the later topic keeps
  its own separate task about the illustration. Ask which direction the
  dependence runs. Chronological or thematic chapters refer backwards often, so
  appearing later in the book does not by itself mean later in teaching.
- If a question combines several concepts from one topic, place it on that
  topic's culmination concept. If it genuinely spans concepts across different
  source topics and fits neither an ordinary concept nor one topic's
  Culmination, it may go to the later source topic's Culmination. Textbook
  Activity / experiment / discussion tasks belong in Activity/Info Hub on the
  related normal concept â€” not as Culmination Cases.
- Cases are defined conceptual sub-types named by learning objective; Examples
  are full source questions. Do not turn a raw question or Activity title into
  a Case name (avoid "Definition of â€¦").
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
- Return ONLY the culmination rows â€” exactly one per topic, nothing else.
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
    label="Activity/Info Hub host proposal system prompt",
    default="""\
Propose hosts for textbook activities, experiments, classroom discussion
cases, and info hubs (boxed asides, "do you know?" panels, biography boxes,
source excerpts).  This is a first-pass semantic proposal, not a
certification.  A separate independent critic receives the source task and
the complete allowed candidate set before any ambiguous proposal can be
certified.

These rules are UNIVERSAL for every upload (any board, subject, or chapter).
Infer placement from THIS chapter's concept map and inventory â€” never invent
chapter-named shortcuts.

Return ONLY strict JSON:
{"placements":[{"concept_id":"CONCEPT-0001","qid":"QINV-0001","hub_note":""}]}.

Rules:
- Activity/Info Hub holds excess classroom material that is NOT the core
  teachable idea: numbered Activity / experiment / lab procedures, discussion
  dilemmas, think-and-discuss prompts, info hubs / enrichment boxes, and
  similar excess material.
- Never place that material on Culmination rows (is_culmination true).
- Never turn Activity titles or discussion-case titles into Topics, concept
  names, Types, or Cases.
- Choose the NORMAL concept whose teaching content the activity or discussion
  practices or illustrates. An info hub belongs with the concept whose
  material it enriches, no matter where the box was printed. Prefer
  topic_hint alignment when it is reliable.
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
    "concepts.activity_hub_critic.system", category=_CONCEPTS_CAT,
    label="Independent Activity/Info Hub host critic prompt",
    default="""\
Independently review proposed Activity/Info Hub hosts.  Do not defer to the
provider's proposal and do not infer that an allowed concept is necessarily a
good semantic fit.

Return ONLY strict JSON:
{"reviews":[{"qid":"QINV-0001","concept_id":"CONCEPT-0001","verdict":"accept|reject","confidence":0.0,"reason":""}]}.

Rules:
- Return every supplied qid exactly once, with the exact proposed concept_id.
  Invent no qids or concept IDs.
- For each qid, independently compare the complete exact source task with the
  bounded core teaching Description of every supplied allowed candidate.
  Types, Examples, Activity/Info Hub, and learner-error sections are
  deliberately excluded: copied task wording there would be circular evidence.
  Accept only when the proposal is the normal concept whose core teaching the
  task genuinely practices or illustrates; list order, physical source
  location, title overlap, and mere structural eligibility are not semantic
  evidence.
- Reject a plausible but less specific host when another allowed candidate
  teaches the task more directly.  Reject Culmination hosts and any candidate
  outside the already-certified semantic owner topic.
- confidence is confidence in this critic verdict, from 0 to 1.  reason must
  state the source-task-to-teaching-content basis for acceptance or rejection.
- The caller fails closed on missing, duplicate, unknown, mismatched, or
  malformed reviews.  It certifies only independently accepted proposals that
  clear the ordinary semantic confidence threshold.
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
  is valid content â€” keep it (in Types Example lines). Never leave a
  Description truncated mid-sentence while fixing artifacts.
- Image URLs belong in canonical [img] tags on Types Example lines next to the figure
  reference. Do not put image URLs in the Description section; describe the
  visual in words there instead.
- For merged_description issues (one cell carrying two or more concepts'
  "Description:" blocks): keep ONLY the content belonging to THIS row's
  concept â€” rewrite the cell so it describes exactly one concept. NEVER
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
  and Description text â€” the ONLY change is appending a line break (\\n)
  followed by exactly:
  Achieving Mastery: <one short sentence stating what the learner can do when this concept is mastered>
- The sentence must be specific to THIS concept alone â€” name this concept's
  own skill, rule, or reasoning move, e.g.
  "Achieving Mastery: Using the midpoint property to set up the smaller triangles correctly."
- Every row in the batch gets its OWN distinct mastery sentence. Never reuse
  one sentence (or a light paraphrase of it) across sibling concepts: if two
  rows would honestly earn the same mastery sentence, write each around the
  part of the skill that row uniquely teaches.
- Do not add Types or alter the existing single ``Misconception/ Error
  Analysis`` section. No source artifacts (Example 3, Exercise 1.2, Fig 4,
  page numbers) and never the words "MMD"/"MMDs".
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
  number stripped) â€” never invent, rename, merge, or paraphrase headings.
- The given headings are the MAIN sections. When a concept comes from a
  subsection, file it under its MAIN section heading â€” subsections are never
  topics.
- Keep EVERY row: same concept names, descriptions, keywords, and
  parent_concept, in the same relative order. Never add, drop, merge, split,
  or rename concepts.
- Use several topics â€” a chapter is never one topic. Cover the chapter's full
  span: rows from tail sections belong to those tail headings, not to an
  earlier catch-all.
- Assign each concept to the section whose content teaches it; consecutive
  concepts usually stay in the same section until the source moves on.
- Use each row's source_evidence against the grouped excerpts. Formulas,
  reusable worked methods, contextual/real-life applications, and
  exercise-derived concepts belong to the section that actually teaches or
  uses that evidenceâ€”not automatically to the preceding topic or an
  unnumbered chapter-title section.
- Do not create exercise, example, review, or practice topics.
- Do not use an unnumbered chapter title or book title as a topic. Exception:
  when a numbered MAIN section intentionally has the same title as the chapter,
  that numbered section is a valid topic and must remain available for rows
  taught there.
""")

prompts.register(
    "concepts.topic_segregation_verdict.system", category=_CONCEPTS_CAT,
    label="Topic segregation verdict system prompt",
    default="""\
Judge whether a chapter concept map's topic segregation faithfully mirrors
the source's own section structure. You are the only judge of this: no
heading count, size ratio, or other arithmetic makes the call.
Return ONLY strict JSON:
{"verdict":"faithful","reason":""} or {"verdict":"restructure","reason":""}.

Rules:
- You are given the source's MAIN section headings in reading order, a
  trimmed excerpt of what each section teaches, and every concept row with
  its current topic.
- "faithful" means the rows are filed under the source headings that
  actually teach them: each section that teaches concepts appears as a
  topic, and rows are not piled under an umbrella topic or a neighbouring
  section's heading.
- "restructure" means the map must be re-segregated against the source:
  rows sit collapsed under one umbrella topic, or under headings that do
  not teach them, or sections that clearly teach concepts have no rows
  filed under them.
- A thin chapter with two headings can be perfectly faithful; a large map
  can be unfaithful under six. Judge only by whether each row's topic is
  where the source actually teaches that content â€” never by how many
  headings, rows, or topics there are.
- reason: one sentence naming the decisive evidence.
""")

prompts.register(
    "concepts.chapter_meta.system", category=_CONCEPTS_CAT,
    label="Chapter/topic metadata writer system prompt",
    default="""\
Write chapter-level and topic-level metadata for a finished school concept map.
Return ONLY strict JSON:
{"chapter_description":"","chapter_duration_minutes":0,"topics":[{"topic":"","topic_description":""}]}.

Rules:
- chapter_description: 3-5 sentences a teacher can plan from â€” what the chapter
  covers, the storyline across its topics, the key skills built, and what
  learners can do at the end. It must be specific to THIS chapter's content;
  never generic filler like "This chapter develops N concepts across M topics".
- chapter_duration_minutes: a realistic INTEGER estimate of total classroom
  minutes needed to teach the full chapter (typical school periods are
  35-45 minutes; a standard chapter runs roughly 4-14 periods). When a
  FINALIZED chapter duration is provided in the metadata block, return that
  exact integer â€” do not override it.
- topics: one entry per provided topic, using the EXACT same topic strings.
- topic_description: 2-3 sentences specific to that topic â€” what it teaches,
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
    instruction_set_sha256: str = "",
    instruction_slots: dict | None = None,
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
        # The Architect's run instructions (docs/aegis-restructure.md Â§8.1):
        # the hash joins the sealed envelope and every decision identity; the
        # slots render into every prompt through _metadata_block.
        "instruction_set_sha256": str(instruction_set_sha256 or ""),
        "instruction_slots": copy.deepcopy(dict(instruction_slots or {})),
    }


def _instruction_slot_lines(slots: dict) -> list[str]:
    """Render the Architect's authored slots; empty slots render nothing."""
    if not isinstance(slots, dict):
        return []
    lines: list[str] = []
    for label, key in (
        ("Subject topology guidance", "subject_topology_guidance"),
        ("Grade-band vocabulary", "grade_band_vocabulary"),
        ("Board/publication conventions", "board_publication_conventions"),
    ):
        text = " ".join(str(slots.get(key) or "").split())
        if text:
            lines.append(f"- {label}: {text}")
    mode = slots.get("language_mode") or {}
    mode_name = " ".join(str(
        (mode.get("mode") or "") if isinstance(mode, dict) else ""
    ).split())
    if mode_name:
        rationale = " ".join(str(
            (mode.get("rationale") or "") if isinstance(mode, dict) else ""
        ).split())
        lines.append(
            f"- Language mode: {mode_name}"
            + (f" â€” {rationale}" if rationale else "")
        )
    cautions = [
        " ".join(str(row).split())
        for row in (
            slots.get("chapter_cautions")
            if isinstance(slots.get("chapter_cautions"), list)
            else []
        )
        if str(row).strip()
    ]
    if cautions:
        lines.append("- Chapter cautions: " + " | ".join(cautions))
    return lines


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
    slot_lines = _instruction_slot_lines(meta.get("instruction_slots") or {})
    if slot_lines:
        block += "\nRUN INSTRUCTIONS (Architect):\n" + "\n".join(slot_lines)
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


def _provider_label() -> str:
    """The human name of the provider serving the current model calls.

    Log lines and error messages must name the provider actually running â€”
    "Gemini quota is exhausted" on a Gemini run, not "OpenAI".
    """
    try:
        from . import model_provider

        return (
            "Gemini" if model_provider.active_provider() == "gemini"
            else "OpenAI"
        )
    except Exception:  # noqa: BLE001 â€” labels must never break a message
        return "OpenAI"


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
    if timeout <= 0:
        progress.log(
            f"{_provider_label()} capacity is busy; waiting for a free "
            f"{purpose_label} slot.",
            level="warning",
        )
        raise OpenAIQueueTimeoutError(
            f"{_provider_label()} capacity is busy and no queue wait is configured. "
            "Try again after another generation finishes."
        )

    import time

    started = time.monotonic()
    # Quiet grace: at full concurrency a slot ordinarily frees within a
    # few seconds, and logging every handoff buried live consoles in
    # busy/acquired pairs. Only a wait that outlives the grace is spoken;
    # the acquired line then reports the TOTAL wait including the grace.
    quiet = min(config.OPENAI_SLOT_WAIT_QUIET_SECONDS, timeout)
    if quiet > 0 and gate.acquire(timeout=quiet):
        return
    if (
        quiet >= timeout
        or timeout - (time.monotonic() - started) <= 0
    ):
        # The grace consumed the whole configured budget (quiet >=
        # timeout, decided deterministically rather than by a clock
        # race): say what actually happened instead of promising a
        # wait that cannot follow.
        progress.log(
            f"{_provider_label()} capacity is busy; timed out after "
            f"{time.monotonic() - started:.0f}s waiting for a free "
            f"{purpose_label} slot.",
            level="warning",
        )
        raise OpenAIQueueTimeoutError(
            "Timed out waiting for an available OpenAI generation slot. "
            "If this run has a saved checkpoint, resume it after another "
            "generation finishes."
        )
    progress.log(
        f"{_provider_label()} capacity is busy; waiting for a free "
        f"{purpose_label} slot.",
        level="warning",
    )
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
                f"{_provider_label()} slot acquired after {waited:.0f}s; continuing.",
                level="success",
            )
            return
        waited = time.monotonic() - started
        progress.log(
            f"Still waiting for OpenAI capacity ({waited:.0f}s).",
            level="warning",
        )


def _json_prompt_cache_parts(
    payload: Mapping[str, Any],
    *,
    stable_keys: tuple[str, ...],
) -> tuple[str, str]:
    """Serialize one JSON payload as a stable prefix plus varying suffix.

    The two strings concatenate to one valid JSON object.  ``stable_keys``
    controls only the provider-facing insertion order; the Phase-3 decision
    key continues to canonicalize the original payload with sorted keys.
    Keeping this helper mechanical is important: it moves already-recorded
    evidence without judging, summarizing, or dropping any content.
    """

    stable = {
        key: copy.deepcopy(payload[key])
        for key in stable_keys
        if key in payload
    }
    varying = {
        str(key): copy.deepcopy(value)
        for key, value in payload.items()
        if key not in stable
    }
    if not stable:
        return "", json.dumps(varying, ensure_ascii=False)
    stable_json = json.dumps(stable, ensure_ascii=False)
    if not varying:
        return stable_json, ""
    varying_json = json.dumps(varying, ensure_ascii=False)
    return stable_json[:-1] + ",", varying_json[1:]


def _prompt_cache_key(
    namespace: str,
    prompt_cache_prefix: str,
    *,
    shard_seed: str,
    shard_count: int = 4,
) -> str:
    """Return a stable, bounded GPT-5.6 cache-routing key.

    OpenAI recommends roughly 15 requests/minute per key.  Master lanes fan
    out concurrently, so a single stage-wide key would become a routing hot
    spot across Pre/Post and simultaneous runs.  The prefix digest partitions
    distinct chapters/lanes, while the candidate-stable shard preserves retry
    locality and spreads one busy lane across a small fixed set of keys.
    """

    name = str(namespace or "").strip()
    if not name or any(
        character not in "abcdefghijklmnopqrstuvwxyz0123456789-"
        for character in name
    ):
        raise ValueError(
            "prompt cache namespace must use lowercase letters, digits, "
            "and hyphens"
        )
    shards = int(shard_count)
    if shards < 1 or shards > 16:
        raise ValueError("prompt cache shard_count must be between 1 and 16")
    prefix_digest = hashlib.sha256(
        str(prompt_cache_prefix).encode("utf-8")
    ).hexdigest()[:16]
    shard_digest = hashlib.sha256(str(shard_seed).encode("utf-8")).digest()
    shard = int.from_bytes(shard_digest[:4], "big") % shards
    key = f"aegis:{name}:{prefix_digest}:{shard}"
    if len(key) > 64:
        raise ValueError("derived prompt_cache_key exceeds 64 characters")
    return key


def _explicit_prompt_cache_request(
    *,
    system: str,
    user: str,
    prompt_cache_prefix: str,
    prompt_cache_key: str,
    model: str,
    provider: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build GPT-5.6 explicit-only cache arguments when requested.

    GPT-5.6's implicit breakpoint sits after the latest user message.  Aegis
    Master requests end with candidate-specific evidence, so implicit mode
    repeatedly writes that unique suffix.  Mark the shared JSON block
    explicitly (the breakpoint includes the preceding system message) and
    disable the implicit suffix write.  Other models, providers, and ordinary
    call sites retain their byte-for-byte message shape and receive no
    OpenAI-specific cache fields.
    """

    enabled = (
        provider == "openai"
        and str(model).lower().startswith("gpt-5.6")
        and bool(prompt_cache_prefix)
        and bool(prompt_cache_key)
    )
    if not enabled:
        return [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": f"{prompt_cache_prefix}{user}",
            },
        ], {}

    key = str(prompt_cache_key).strip()
    if not key or len(key) > 64:
        raise ValueError("p×­µçkh‘éì¶»§q«^u¥½¸¥¸„‘ÕÉ…‰±”¡•­Á½¥¹Ğ‰•™½É”(€€€€Œ‘¥ÍÁ…Ñ ¸%˜Ñ¡”İ½É­•È‘¥•Ì…™Ñ•ÈÑ¡¥ÌİÉ¥Ñ”°Ñ¡”½ÕÑ½µ”¥ÌÕ¹­¹½İ¸(€€€€Œ…¹É•ÍÕµ”…Í­Ì™½È„™É•Í ‘•¥Í¥½¸¥¹ÍÑ•…½˜É•Á±…å¥¹œ„Á½ÍÍ¥‰±ä(€€€€Œ‰¥±±•É•ÅÕ•ÍĞ¸(€€€ÍÑ…Ñ•l‰±…ÍÑ}…ÑÑ•µÁĞ‰t€ôì(€€€€€€€€‰‘•¥Í¥½¹}¥ˆè‘•¥Í¥½¹}¥°(€€€€€€€€‰½¹Ñ•áÑ}¡…Í ˆè½¹Ñ•áÑ}¡…Í °(€€€€€€€€‰ÍÑ…ÑÕÌˆè€‰É•ÅÕ•ÍÑ}ÍÑ…ÉÑ•ˆ°(€€€ô(€€€ÍÑ…Ñ•l‰Á•¹‘¥¹}™½±±½İÕÀ‰t€ôì(€€€€€€€€‰ÁÉ¥½É}‘•¥Í¥½¹}¥ˆè‘•¥Í¥½¹}¥°(€€€€€€€€‰™…¥±ÕÉ”ˆè€ (€€€€€€€€€€€€‰Q¡”…ÕÑ¡½É¥é•Í½ÕÉ”µÑ½Á¥ŒÉ•ÅÕ•ÍĞİ…Ì‘¥ÍÁ…Ñ¡•°‰ÕĞ¹¼€ˆ(€€€€€€€€€€€€‰•ÉÑ¥™¥•É•ÍÕ±Ğ¡•­Á½¥¹Ğİ…ÌÍ…Ù•¸%ÑÌ½ÕÑ½µ”¥ÌÕ¹­¹½İ¸ì€ˆ(€€€€€€€€€€€€‰•¥Ìİ¥±°¹½ĞÉ•Á±…ä¥Ğ…ÕÑ½µ…Ñ¥…±±ä¸ˆ(€€€€€€€€¤°(€€€ô(€€€•µ¥Ñ}É•Ù¥•İ}¡•­Á½¥¹Ğ ¤(€€€ÑÉäè(€€€€€€€½ÕĞ€ô}É•½Ù•É}µ¥ÍÍ¥¹}Ñ½Á¥}½¹•ÁÑÍ}Ù¥…}…Á¤ (€€€€€€€€€€€½ÕĞ°(€€€€€€€€€€€µ•Ñ„õµ•Ñ„°(€€€€€€€€€€€Í½ÕÉ•}Ñ½Á¥}•á•ÉÁÑÌõÍ½ÕÉ•}Ñ½Á¥}•á•ÉÁÑÌ°(€€€€€€€€€€€µ…á}…ÑÑ•µÁÑÌôÄ°(€€€€€€€€€€€¥¹ÍÑÉÕÑ¥½¸õÍÑÈ¡‘¥É•Ñ¥Ù”¹•Ğ ‰¥¹ÍÑÉÕÑ¥½¸ˆ¤½È€ˆˆ¤°(€€€€€€€€€€€™…¥±}½¹}µ¥ÍÍ¥¹œõ…±Í”°(€€€€€€€€€€€Í¥¹±•}…ÑÑ•µÁĞõQÉÕ”°(€€€€€€€€¤(€€€•á•ÁĞá•ÁÑ¥½¸…Ì•áŒè€€Œ¹½Å„è	1ÀÀÄ€´½¹Ù•ÉĞ¥¹Ñ¼„‘ÕÉ…‰±”É”µÁ…ÕÍ”(€€€€€€€™…¥±ÕÉ”€ô€ (€€€€€€€€€€€€‰Q¡”½¹”‰½Õ¹‘•É•½Ù•ÉäÉ•ÅÕ•ÍĞ½Õ±¹½ĞÁÉ½‘Õ”„ÕÍ…‰±”€ˆ(€€€€€€€€€€€˜‰É•ÍÁ½¹Í”€¡íÑåÁ”¡•áŒ¤¹}}¹…µ•}}ô¤èí•áôˆ(€€€€€€€€¥lèÑ|ÀÀÁt(€€€€€€€ÍÑ…Ñ•l‰±…ÍÑ}…ÑÑ•µÁĞ‰t€ôì(€€€€€€€€€€€€‰‘•¥Í¥½¹}¥ˆè‘•¥Í¥½¹}¥°(€€€€€€€€€€€€‰½¹Ñ•áÑ}¡…Í ˆè½¹Ñ•áÑ}¡…Í °(€€€€€€€€€€€€‰ÍÑ…ÑÕÌˆè€‰™…¥±•ˆ°(€€€€€€€ô(€€€€€€€ÍÑ…Ñ•l‰Á•¹‘¥¹}™½±±½İÕÀ‰t€ôì(€€€€€€€€€€€€‰ÁÉ¥½É}‘•¥Í¥½¹}¥ˆè‘•¥Í¥½¹}¥°(€€€€€€€€€€€€‰™…¥±ÕÉ”ˆè™…¥±ÕÉ”°(€€€€€€€ô(€€€€€€€•µ¥Ñ}É•Ù¥•İ}¡•­Á½¥¹Ğ ¤(€€€€€€€Í½ÕÉ•}Ñ½Á¥}‘•¥Í¥½¸¹É•Í½±Ù•}½É}Á…ÕÍ” (€€€€€€€€€€€É•½É‘Ìõ½ÕĞ°(€€€€€€€€€€€Í½ÕÉ•}Ñ½Á¥ÌõÍ½ÕÉ•}Ñ½Á¥}•á•ÉÁÑÌ°(€€€€€€€€€€€µ¥ÍÍ¥¹}Ñ½Á¥Ìõµ¥ÍÍ¥¹œ°(€€€€€€€€€€€µ•Ñ„õµ•Ñ„°(€€€€€€€€€€€ÁÉ¥½É}‘•¥Í¥½¹}¥õ‘•¥Í¥½¹}¥°(€€€€€€€€€€€™…¥±ÕÉ”õ™…¥±ÕÉ”°(€€€€€€€€¤(€€€€€€€É…¥Í”IÕ¹Ñ¥µ•ÉÉ½È (€€€€€€€€€€€€‰Í½ÕÉ”µÑ½Á¥ŒÉ•½Ù•ÉäÉ•ÅÕ•ÍĞ™…¥±•İ¥Ñ¡½ÕĞÉ•…Ñ¥¹œ¥ÑÌ€ˆ(€€€€€€€€€€€€‰É•ÅÕ¥É•™½±±½ÜµÕÀ‘•¥Í¥½¸ˆ(€€€€€€€€¤™É½´•áŒ((€€€É•µ…¥¹¥¹œ€ô}µ¥ÍÍ¥¹}Í½ÕÉ•}Ñ½Á¥}•á•ÉÁÑÌ¡½ÕĞ°Í½ÕÉ•}Ñ½Á¥}•á•ÉÁÑÌ¤(€€€ÍÑ…Ñ•l‰±…ÍÑ}…ÑÑ•µÁĞ‰t€ôì(€€€€€€€€‰‘•¥Í¥½¹}¥ˆè‘•¥Í¥½¹}¥°(€€€€€€€€‰½¹Ñ•áÑ}¡…Í ˆè½¹Ñ•áÑ}¡…Í °(€€€€€€€€‰ÍÑ…ÑÕÌˆè€‰ÍÕ••‘•ˆ¥˜¹½ĞÉ•µ…¥¹¥¹œ•±Í”€‰¥¹½µÁ±•Ñ”ˆ°(€€€ô(€€€¥˜¹½ĞÉ•µ…¥¹¥¹œè(€€€€€€€ÍÑ…Ñ”¹Á½À ‰Á•¹‘¥¹}™½±±½İÕÀˆ°9½¹”¤(€€€€€€€€ŒQ¡¥Ì¥ÌÑ¡”™¥ÉÍĞ‘ÕÉ…‰±”İÉ¥Ñ”…™Ñ•ÈÑ¡”Á…¥É•ÍÁ½¹Í”¸€A•ÉÍ¥ÍĞÑ¡”(€€€€€€€€ŒÉ•½Ù•É•É½İÌ…¹½¹ÍÕµ•‘•¥Í¥½¸‰•™½É”…¹ä±…Ñ•ÈÍ•µ…¹Ñ¥ŒÍÑ…”(€€€€€€€€Œ…¸ÉÕ¸°™…¥°°½È‰”¥¹Ñ•ÉÉÕÁÑ•¸(€€€€€€€•µ¥Ñ}É•Ù¥•İ}¡•­Á½¥¹Ğ ¤(€€€€€€€É•ÑÕÉ¸½ÕĞ((€€€™…¥±ÕÉ”€ô€ (€€€€€€€€‰=¹”‰½Õ¹‘•É•½Ù•ÉäÉ•ÅÕ•ÍĞ½µÁ±•Ñ•°‰ÕĞÑ¡•Í”ÍÑÉÕÑÕÉ…±±äÁÉ½Ù•¸€ˆ(€€€€€€€€‰Í½ÕÉ”Ñ½Á¥ÌÍÑ¥±°¡…Ù”¹¼¹½Éµ…°½¹•ÁĞè€ˆ(€€€€€€€€¬€ˆ°€ˆ¹©½¥¸ (€€€€€€€€€€€ÍÑÈ¡É½ÕÀ¹•Ğ ‰Ñ½Á¥Œˆ¤½È€ˆˆ¤¹ÍÑÉ¥À ¤™½ÈÉ½ÕÀ¥¸É•µ…¥¹¥¹œ(€€€€€€€€¤(€€€€¥lèÑ|ÀÀÁt(€€€ÍÑ…Ñ•l‰Á•¹‘¥¹}™½±±½İÕÀ‰t€ôì(€€€€€€€€‰ÁÉ¥½É}‘•¥Í¥½¹}¥ˆè‘•¥Í¥½¹}¥°(€€€€€€€€‰™…¥±ÕÉ”ˆè™…¥±ÕÉ”°(€€€ô(€€€•µ¥Ñ}É•Ù¥•İ}¡•­Á½¥¹Ğ ¤(€€€€ŒQ¡”™¥ÉÍĞ…±°…±İ…åÌÉ…¥Í•Ì„™É•Í ‘ÕÉ…‰±”‘•¥Í¥½¸¸€Q¡•É”¥Ì¹¼(€€€€Œ…ÕÑ½µ…Ñ¥ŒÍ•½¹Í•µ…¹Ñ¥ŒÉ•ÅÕ•ÍĞ¸(€€€Í½ÕÉ•}Ñ½Á¥}‘•¥Í¥½¸¹É•Í½±Ù•}½É}Á…ÕÍ” (€€€€€€€É•½É‘Ìõ½ÕĞ°(€€€€€€€Í½ÕÉ•}Ñ½Á¥ÌõÍ½ÕÉ•}Ñ½Á¥}•á•ÉÁÑÌ°(€€€€€€€µ¥ÍÍ¥¹}Ñ½Á¥ÌõÉ•µ…¥¹¥¹œ°(€€€€€€€µ•Ñ„õµ•Ñ„°(€€€€€€€ÁÉ¥½É}‘•¥Í¥½¹}¥õ‘•¥Í¥½¹}¥°(€€€€€€€™…¥±ÕÉ”õ™…¥±ÕÉ”°(€€€€¤(€€€É…¥Í”IÕ¹Ñ¥µ•ÉÉ½È (€€€€€€€€‰Í½ÕÉ”µÑ½Á¥ŒÉ•½Ù•Éä™…¥±•İ¥Ñ¡½ÕĞÉ•…Ñ¥¹œ¥ÑÌÉ•ÅÕ¥É•™½±±½ÜµÕÀ€ˆ(€€€€€€€€‰‘•¥Í¥½¸ˆ(€€€€¤(()‘•˜}¡…ÁÑ•É}½Á•¹¥¹}•á•ÉÁĞ (€€€Í•Ñ¥½¹Ìè±¥ÍÑm‘¥Ñt°¡•…‘¥¹Ìè±¥ÍÑmÍÑÉt°(¤€´ø‘¥ÑmÍÑÈ°ÍÑÉtğ9½¹”è(€€€€ˆˆ‰I•ÑÕÉ¸ÍÕ‰ÍÑ…¹Ñ¥Ù”Í½ÕÉ”µ…Ñ•É¥…°‰•™½É”Ñ¡”™¥ÉÍĞµ…¥¸Ñ½Á¥Œ¸ˆˆˆ(€€€¥˜¹½ĞÍ•Ñ¥½¹Ì½È¹½Ğ¡•…‘¥¹Ìè(€€€€€€€É•ÑÕÉ¸9½¹”(€€€™¥ÉÍÑ}Ñ½Á¥Œ€ô}ÍÑÉ¥Á}Í•Ñ¥½¹}¹Õµ‰•È¡¡•…‘¥¹ÍlÁt¤¹ÍÑÉ¥À ¤(€€€™¥ÉÍÑ}­•ä€ô}Ñ½Á¥}½µÁ…É¥Í½¹}­•ä¡™¥ÉÍÑ}Ñ½Á¥Œ¤(€€€½Á•¹¥¹}Á…ÉÑÌè±¥ÍÑmÍÑÉt€ômt(€€€™½ÈÍ•Ñ¥½¸¥¸Í•Ñ¥½¹Ìè(€€€€€€€¡•…‘¥¹œ€ô€¡Í•Ñ¥½¸¹•Ğ ‰¡•…‘¥¹œˆ¤½È€ˆˆ¤¹ÍÑÉ¥À ¤(€€€€€€€¥˜¡•…‘¥¹œ…¹}Ñ½Á¥}½µÁ…É¥Í½¹}­•ä¡¡•…‘¥¹œ¤€ôô™¥ÉÍÑ}­•äè(€€€€€€€€€€€‰É•…¬(€€€€€€€‰½‘ä€ô€¡Í•Ñ¥½¸¹•Ğ ‰‰½‘äˆ¤½È€ˆˆ¤¹ÍÑÉ¥À ¤(€€€€€€€¥˜‰½‘äè(€€€€€€€€€€€½Á•¹¥¹}Á…ÉÑÌ¹…ÁÁ•¹ (€€€€€€€€€€€€€€€€¡˜‰í¡•…‘¥¹õq¸ˆ¥˜¡•…‘¥¹œ•±Í”€ˆˆ¤€¬‰½‘ä¤(€€€•á•ÉÁĞ€ô€‰q¹q¸ˆ¹©½¥¸¡½Á•¹¥¹}Á…ÉÑÌ¤¹ÍÑÉ¥À ¤(€€€€ŒÙ½¥ÍÁ•¹‘¥¹œ„Í•µ…¹Ñ¥Œ…Õ‘¥Ğ½¸„Ñ¥Ñ±”Á…”½È„‘•½É…Ñ¥Ù”¥µ…”¸(€€€ÁÉ½Í”€ôÉ”¹ÍÕˆ¡È‰¡ÑÑÁÌüè¼½qL­ñqqmµi„µét­qì¸¨ıqôˆ°€ˆ€ˆ°•á•ÉÁĞ¤(€€€¥˜±•¸¡É”¹ÍÕˆ¡È‰q\¬ˆ°€ˆˆ°ÁÉ½Í”°™±…ÌõÉ”¹U9%=¤¤€ğ€ÄàÀè(€€€€€€€É•ÑÕÉ¸9½¹”(€€€É•ÑÕÉ¸ì‰Ñ½Á¥Œˆè™¥ÉÍÑ}Ñ½Á¥Œ°€‰•á•ÉÁĞˆè•á•ÉÁÑô(()‘•˜}É•½Ù•É}¡…ÁÑ•É}½Á•¹¥¹}½¹•ÁÑÍ}Ù¥…}…Á¤ (€€€É•½É‘Ìè±¥ÍÑm‘¥Ñt°€¨°µ•Ñ„è‘¥Ğ°Í•Ñ¥½¹Ìè±¥ÍÑm‘¥Ñt°(€€€¡•…‘¥¹Ìè±¥ÍÑmÍÑÉt°(¤€´ø±¥ÍÑm‘¥Ñtè(€€€€ˆˆ‰M•µ…¹Ñ¥…±±ä…Õ‘¥Ğ…¹É•½Ù•È½µ¥ÑÑ•ÁÉ”µÍ•Ñ¥½¸Ñ•…¡¥¹œ½¹Ñ•¹Ğ¸ˆˆˆ(€€€¥µÁ½ÉĞ©Í½¸…Ì}©Í½¸((€€€½Á•¹¥¹œ€ô}¡…ÁÑ•É}½Á•¹¥¹}•á•ÉÁĞ¡Í•Ñ¥½¹Ì°¡•…‘¥¹Ì¤(€€€¥˜¹½Ğ½Á•¹¥¹œ½È¹½ĞÉ•½É‘Ìè(€€€€€€€É•ÑÕÉ¸É•½É‘Ì(€€€Ñ½Á¥}­•ä€ô}Ñ½Á¥}½µÁ…É¥Í½¹}­•ä¡½Á•¹¥¹l‰Ñ½Á¥Œ‰t¤(€€€•á¥ÍÑ¥¹œ€ôl(€€€€€€€É½Ü™½ÈÉ½Ü¥¸É•½É‘Ì(€€€€€€€¥˜}Ñ½Á¥}½µÁ…É¥Í½¹}­•ä¡É½Ü¹•Ğ ‰Ñ½Á¥Œˆ¤½È€ˆˆ¤€ôôÑ½Á¥}­•ä(€€€€€€€…¹¹½ĞÈ¹¥Í}Õ±µ¥¹…Ñ¥½¸¡É½Ü¹•Ğ ‰½¹•ÁÑ}Ñ¥Ñ±”ˆ°€ˆˆ¤¤(€€€t(€€€Á…å±½…€ôì(€€€€€€€€‰½Á•¹¥¹}Ñ½Á¥Œˆè½Á•¹¥¹l‰Ñ½Á¥Œ‰t°(€€€€€€€€‰½Á•¹¥¹}•á•ÉÁĞˆè}ÑÉ¥´¡½Á•¹¥¹l‰•á•ÉÁĞ‰t°€ÔÁ|ÀÀÀ¤°(€€€€€€€€‰•á¥ÍÑ¥¹}É½İÌˆè}É•½É‘Í}Ñ½}…Á¥}É½İÌ¡•á¥ÍÑ¥¹œ¤°(€€€ô(€€€ÁÉ½É•ÍÌ¹±½œ ‰Õ‘¥Ñ¥¹œÍÕ‰ÍÑ…¹Ñ¥Ù”¡…ÁÑ•Èµ½Á•¹¥¹œ½¹•ÁĞ½Ù•É…”Ù¥„A$¸ˆ¤(€€€‘…Ñ„€ô}½Á•¹…¥}©Í½¸ (€€€€€€€ÁÉ½µÁÑÌ¹•Ñ}Ñ•áĞ ‰½¹•ÁÑÌ¹½Á•¹¥¹}É•½Ù•Éä¹ÍåÍÑ•´ˆ¤°(€€€€€€€}µ•Ñ…‘…Ñ…}‰±½¬¡µ•Ñ„¤€¬€‰q¸ˆ€¬}©Í½¸¹‘ÕµÁÌ¡Á…å±½…°•¹ÍÕÉ•}…Í¥¤õ…±Í”¤°(€€€€€€€ÁÕÉÁ½Í”ô‰½¹•ÁÑ}Ù…±¥‘…Ñ¥½¸ˆ°(€€€€¤(€€€É…İ}…¹‘¥‘…Ñ•Ì€ômt(€€€™½ÈÉ…Ü¥¸€¡‘…Ñ„½Èíô¤¹•Ğ ‰µ¥ÍÍ¥¹}É½İÌˆ¤½Èmtè(€€€€€€€¥˜¹½Ğ¥Í¥¹ÍÑ…¹”¡É…Ü°‘¥Ğ¤è(€€€€€€€€€€€½¹Ñ¥¹Õ”(€€€€€€€¹½Éµ…±¥é•€ô‘¥Ğ¡É…Ü¤(€€€€€€€¥˜¥Í¥¹ÍÑ…¹”¡¹½Éµ…±¥é•¹•Ğ ‰­•åİ½É‘Ìˆ¤°±¥ÍĞ¤è(€€€€€€€€€€€¹½Éµ…±¥é•‘l‰­•åİ½É‘Ì‰t€ô€ˆ°€ˆ¹©½¥¸ (€€€€€€€€€€€€€€€ÍÑÈ¡Ù…±Õ”¤¹ÍÑÉ¥À ¤(€€€€€€€€€€€€€€€™½ÈÙ…±Õ”¥¸¹½Éµ…±¥é•‘l‰­•åİ½É‘Ì‰t(€€€€€€€€€€€€€€€¥˜ÍÑÈ¡Ù…±Õ”¤¹ÍÑÉ¥À ¤(€€€€€€€€€€€€¤(€€€€€€€É…İ}…¹‘¥‘…Ñ•Ì¹…ÁÁ•¹¡¹½Éµ…±¥é•¤(€€€…¹‘¥‘…Ñ•Ì€ô}½¹•ÁÑ}É½İÍ}Ñ½}É•½É‘Ì¡ì(€€€€€€€€‰É½İÌˆèÉ…İ}…¹‘¥‘…Ñ•Ì°(€€€ô¤(€€€€ŒÕ‘¥ĞĞè½¹±ä„‘ÕÁ±¥…Ñ”İ¥Ñ¡¥¸Ñ¡”MQ%9Q%=8Ñ½Á¥Œ¥Ì„(€€€€Œµ•¡…¹¥…°‘ÕÁ±¥…Ñ”ìÑ¡”Í…µ”İ½É‘ÌÕ¹‘•È…¹½Ñ¡•ÈÑ½Á¥Œ…É”„(€€€€Œ‘¥™™•É•¹Ğ½¹•ÁĞ¸(€€€½Á•¹¥¹}Ñ½Á¥}­•ä€ô}Ñ½Á¥}½µÁ…É¥Í½¹}­•ä¡½Á•¹¥¹l‰Ñ½Á¥Œ‰t¤(€€€•á¥ÍÑ¥¹}Ñ¥Ñ±•Ì€ôí}É•½É‘}­•ä¡É½Ü¤™½ÈÉ½Ü¥¸É•½É‘Íô(€€€…‘‘¥Ñ¥½¹Ìè±¥ÍÑm‘¥Ñt€ômt(€€€™½È…¹‘¥‘…Ñ”¥¸…¹‘¥‘…Ñ•Ìè(€€€€€€€Ñ¥Ñ±”€ô€¡…¹‘¥‘…Ñ”¹•Ğ ‰½¹•ÁÑ}Ñ¥Ñ±”ˆ¤½È€ˆˆ¤¹ÍÑÉ¥À ¤(€€€€€€€Ñ¥Ñ±•}­•ä€ô‰¤¹¹½Éµ…±¥é•}ÅÕ•ÍÑ¥½¹}Ñ•áĞ¡Ñ¥Ñ±”¤(€€€€€€€€ŒQ¡”É•½Ù•Éäµ½‘•°…±É•…‘ä©Õ‘•Ñ¡•Í”Ñ¥Ñ±•Ìİ½ÉÑ …‘‘¥¹œì½¹±ä(€€€€€€€€Œµ•¡…¹¥…°‘ÕÁ±¥…Ñ•Ì½Õ±µ¥¹…Ñ¥½¹Ì…É”ÍÉ••¹•½ÕĞ¡•É”¸(€€€€€€€¥˜€ (€€€€€€€€€€€¹½ĞÑ¥Ñ±•}­•ä(€€€€€€€€€€€½È€¡½Á•¹¥¹}Ñ½Á¥}­•ä°Ñ¥Ñ±•}­•ä¤¥¸•á¥ÍÑ¥¹}Ñ¥Ñ±•Ì(€€€€€€€€€€€½ÈÈ¹¥Í}Õ±µ¥¹…Ñ¥½¸¡Ñ¥Ñ±”¤(€€€€€€€€¤è(€€€€€€€€€€€½¹Ñ¥¹Õ”(€€€€€€€…¹‘¥‘…Ñ•l‰Ñ½Á¥Œ‰t€ô½Á•¹¥¹l‰Ñ½Á¥Œ‰t(€€€€€€€¥˜¹½Ğ€¡…¹‘¥‘…Ñ”¹•Ğ ‰Á…É•¹Ñ}½¹•ÁĞˆ¤½È€ˆˆ¤¹ÍÑÉ¥À ¤è(€€€€€€€€€€€…¹‘¥‘…Ñ•l‰Á…É•¹Ñ}½¹•ÁĞ‰t€ô½Á•¹¥¹l‰Ñ½Á¥Œ‰t(€€€€€€€…‘‘¥Ñ¥½¹Ì¹…ÁÁ•¹¡…¹‘¥‘…Ñ”¤(€€€€€€€•á¥ÍÑ¥¹}Ñ¥Ñ±•Ì¹…‘ ¡½Á•¹¥¹}Ñ½Á¥}­•ä°Ñ¥Ñ±•}­•ä¤¤(€€€¥˜¹½Ğ…‘‘¥Ñ¥½¹Ìè(€€€€€€€É•ÑÕÉ¸É•½É‘Ì(€€€€ŒI…É”µ…Í”±Õ‰‰¥¹œ€¡Ñ•…´É•Ù¥•Ü¤è½¹”½ÈÑİ¼É•½Ù•É•½Á•¹¥¹œ(€€€€Œ½¹•ÁÑÌ¹•Ù•È©ÕÍÑ¥™äµ¥¹Ñ¥¹œÑ¡•¥È½İ¸¡…ÁÑ•ÈµÑ¥Ñ±”Ñ½Á¥ŒƒŠP™¥±”(€€€€ŒÑ¡•´Õ¹‘•ÈÑ¡”™¥ÉÍĞÉ•…°Í•Ñ¥½¸Ñ½Á¥Œ¥¹ÍÑ•…°İ¡•É”„É•…‘•È(€€€€Œµ••ÑÌÑ¡…Ğµ…Ñ•É¥…°…¹åİ…ä¸(€€€¥˜¹½Ğ•á¥ÍÑ¥¹œ…¹±•¸¡…‘‘¥Ñ¥½¹Ì¤€ğô€Èè(€€€€€€€™¥ÉÍÑ}Í•Ñ¥½¹}Ñ½Á¥Œ€ô¹•áĞ (€€€€€€€€€€€€ (€€€€€€€€€€€€€€€ÍÑÈ¡É½Ü¹•Ğ ‰Ñ½Á¥Œˆ¤½È€ˆˆ¤¹ÍÑÉ¥À ¤(€€€€€€€€€€€€€€€™½ÈÉ½Ü¥¸É•½É‘Ì(€€€€€€€€€€€€€€€¥˜ÍÑÈ¡É½Ü¹•Ğ ‰Ñ½Á¥Œˆ¤½È€ˆˆ¤¹ÍÑÉ¥À ¤(€€€€€€€€€€€€€€€…¹}Ñ½Á¥}½µÁ…É¥Í½¹}­•ä¡É½Ü¹•Ğ ‰Ñ½Á¥Œˆ¤½È€ˆˆ¤€„ôÑ½Á¥}­•ä(€€€€€€€€€€€€¤°(€€€€€€€€€€€€ˆˆ°(€€€€€€€€¤(€€€€€€€¥˜™¥ÉÍÑ}Í•Ñ¥½¹}Ñ½Á¥Œè(€€€€€€€€€€€™½È…¹‘¥‘…Ñ”¥¸…‘‘¥Ñ¥½¹Ìè(€€€€€€€€€€€€€€€…¹‘¥‘…Ñ•l‰Ñ½Á¥Œ‰t€ô™¥ÉÍÑ}Í•Ñ¥½¹}Ñ½Á¥Œ(€€€€€€€€€€€€€€€¥˜}Ñ½Á¥}½µÁ…É¥Í½¹}­•ä (€€€€€€€€€€€€€€€€€€€…¹‘¥‘…Ñ”¹•Ğ ‰Á…É•¹Ñ}½¹•ÁĞˆ¤½È€ˆˆ(€€€€€€€€€€€€€€€€¤€ôôÑ½Á¥}­•äè(€€€€€€€€€€€€€€€€€€€…¹‘¥‘…Ñ•l‰Á…É•¹Ñ}½¹•ÁĞ‰t€ô™¥ÉÍÑ}Í•Ñ¥½¹}Ñ½Á¥Œ(€€€€€€€€€€€Ñ½Á¥}­•ä€ô}Ñ½Á¥}½µÁ…É¥Í½¹}­•ä¡™¥ÉÍÑ}Í•Ñ¥½¹}Ñ½Á¥Œ¤(€€€½ÕĞ€ôm‘¥Ğ¡É½Ü¤™½ÈÉ½Ü¥¸É•½É‘Ít(€€€¥¹Í•ÉÑ}…Ğ€ô¹•áĞ (€€€€€€€€ (€€€€€€€€€€€¥¹‘•à™½È¥¹‘•à°É½Ü¥¸•¹Õµ•É…Ñ”¡½ÕĞ¤(€€€€€€€€€€€¥˜}Ñ½Á¥}½µÁ…É¥Í½¹}­•ä¡É½Ü¹•Ğ ‰Ñ½Á¥Œˆ¤½È€ˆˆ¤€ôôÑ½Á¥}­•ä(€€€€€€€€¤°(€€€€€€€€À°(€€€€¤(€€€½ÕÑm¥¹Í•ÉÑ}…Ğé¥¹Í•ÉÑ}…Ñt€ô…‘‘¥Ñ¥½¹Ì(€€€ÁÉ½É•ÍÌ¹±½œ (€€€€€€€˜‰I•½Ù•É•í±•¸¡…‘‘¥Ñ¥½¹Ì¥ôµ¥ÍÍ¥¹œ¡…ÁÑ•Èµ½Á•¹¥¹œ½¹•ÁĞÉ½Ü¡Ì¤¸ˆ°(€€€€€€€±•Ù•°ô‰ÍÕ•ÍÌˆ°(€€€€¤(€€€É•ÑÕÉ¸½ÕĞ(()‘•˜}Ñ½Á¥}Í•É•…Ñ¥½¹}Ù•É‘¥Ñ}Ù¥…}…Á¤ (€€€É•½É‘Ìè±¥ÍÑm‘¥Ñt°€¨°µ•Ñ„è‘¥Ğ°(€€€Í½ÕÉ•}Ñ½Á¥}•á•ÉÁÑÌè±¥ÍÑm‘¥Ñtğ9½¹”€ô9½¹”°(€€€¡•…‘¥¹Ìè±¥ÍÑmÍÑÉtğ9½¹”€ô9½¹”°(¤€´ø‘¥Ğè(€€€€ˆˆ‰5½‘•°Ù•É‘¥Ğè‘½•ÌÑ¡”µ…ÀÌÑ½Á¥ŒÍ•É•…Ñ¥½¸µ¥ÉÉ½ÈÑ¡”Í½ÕÉ”ü((€€€]¡•Ñ¡•È„Í­•±•Ñ½¸¹••‘ÌÉ”µÍ•É•…Ñ¥½¸¥Ì„©Õ‘µ•¹Ğ…‰½ÕĞİ¡…ĞÑ¡”(€€€Í½ÕÉ”µ•…¹Ì°Í¼Ñ¡”µ½‘•°µ…­•Ì¥Ğ™É½´Ñ¡”Í½ÕÉ”•Ù¥‘•¹”ƒŠP¹•Ù•È„(€€€¡•…‘¥¹œ½Õ¹Ğ½È„½±±…ÁÍ”µÍ¡…Á”Ñ•ÍĞ¸Q¡”Ù•É‘¥Ğ½¹±äÉ½ÕÑ•ÌÑ¡”(€€€…±¥¹µ•¹ĞÁ…ÍÍ•Ìì¥Ğ…¹¹½Ğ…‘°‘É½À°½ÈÉ•İÉ¥Ñ”„É½Ü¸É•ÍÁ½¹Í”(€€€Ñ¡…Ğ‘½•Ì¹½ĞÁ½Í¥Ñ¥Ù•±ä‘•¥‘”ÍÑ½ÁÌÑ¡”ÉÕ¸€¡™…¥°±½Í•¤¸(€€€€ˆˆˆ(€€€¥µÁ½ÉĞ©Í½¸…Ì}©Í½¸((€€€¡•…‘¥¹Ì€ôm ¹ÍÑÉ¥À ¤™½È ¥¸€¡¡•…‘¥¹Ì½Èmt¤¥˜ ¹ÍÑÉ¥À ¥t(€€€Í½ÕÉ•}Ñ½Á¥}•á•ÉÁÑÌ€ôl(€€€€€€€É½ÕÀ™½ÈÉ½ÕÀ¥¸€¡Í½ÕÉ•}Ñ½Á¥}•á•ÉÁÑÌ½Èmt¤(€€€€€€€¥˜€¡É½ÕÀ¹•Ğ ‰Ñ½Á¥Œˆ¤½È€ˆˆ¤¹ÍÑÉ¥À ¤(€€€t(€€€•á•ÉÁÑ}‰Õ‘•Ğ€ôµ…à (€€€€€€€€É|ÀÀÀ°€ØÁ|ÀÀÀ€¼¼µ…à Ä°±•¸¡Í½ÕÉ•}Ñ½Á¥}•á•ÉÁÑÌ¤¤¤(€€€ÁÉ½µÁÑ}•á•ÉÁÑÌ€ôl(€€€€€€€ì(€€€€€€€€€€€€‰Ñ½Á¥Œˆè€¡É½ÕÀ¹•Ğ ‰Ñ½Á¥Œˆ¤½È€ˆˆ¤¹ÍÑÉ¥À ¤°(€€€€€€€€€€€€‰•á•ÉÁĞˆè}ÑÉ¥´¡É½ÕÀ¹•Ğ ‰•á•ÉÁĞˆ¤½È€ˆˆ°•á•ÉÁÑ}‰Õ‘•Ğ¤°(€€€€€€€ô(€€€€€€€™½ÈÉ½ÕÀ¥¸Í½ÕÉ•}Ñ½Á¥}•á•ÉÁÑÌ(€€€t(€€€É½İÌ€ôl(€€€€€€€ì(€€€€€€€€€€€€‰½¹•ÁĞˆèÉ•Œ¹•Ğ ‰½¹•ÁÑ}Ñ¥Ñ±”ˆ°€ˆˆ¤°(€€€€€€€€€€€€‰Ñ½Á¥ŒˆèÉ•Œ¹•Ğ ‰Ñ½Á¥Œˆ°€ˆˆ¤°(€€€€€€€ô(€€€€€€€™½ÈÉ•Œ¥¸É•½É‘Ì(€€€t(€€€ÍåÍÑ•´€ôÁÉ½µÁÑÌ¹•Ñ}Ñ•áĞ ‰½¹•ÁÑÌ¹Ñ½Á¥}Í•É•…Ñ¥½¹}Ù•É‘¥Ğ¹ÍåÍÑ•´ˆ¤(€€€ÕÍ•È€ô€ (€€€€€€€}µ•Ñ…‘…Ñ…}‰±½¬¡µ•Ñ„¤(€€€€€€€€¬€‰q¹MQ%=8!%9L€¡É•…‘¥¹œ½É‘•È¤éq¸´€ˆ(€€€€€€€€¬€‰q¸´€ˆ¹©½¥¸¡¡•…‘¥¹Ì¤(€€€€€€€€¬€‰q¹q¹M=UIQ=A%aIAQL€¡ÑÉ¥µµ•¤éq¸ˆ(€€€€€€€€¬}©Í½¸¹‘ÕµÁÌ¡ì‰Í½ÕÉ•}Ñ½Á¥ÌˆèÁÉ½µÁÑ}•á•ÉÁÑÍô°•¹ÍÕÉ•}…Í¥¤õ…±Í”¤(€€€€€€€€¬˜‰q¹q¹UII9P=9AP5@€¡í±•¸¡É½İÌ¥ôÉ½İÌ¤éq¸ˆ(€€€€€€€€¬}©Í½¸¹‘ÕµÁÌ¡ì‰É½İÌˆèÉ½İÍô°•¹ÍÕÉ•}…Í¥¤õ…±Í”¤(€€€€¤(€€€‘…Ñ„€ô}½Á•¹…¥}©Í½¸¡ÍåÍÑ•´°ÕÍ•È°ÁÕÉÁ½Í”ô‰½¹•ÁÑ}Ù…±¥‘…Ñ¥½¸ˆ¤(€€€Ù•É‘¥Ğ€ôÍÑÈ ¡‘…Ñ„½Èíô¤¹•Ğ ‰Ù•É‘¥Ğˆ¤½È€ˆˆ¤¹ÍÑÉ¥À ¤¹±½İ•È ¤(€€€É•…Í½¸€ôÍÑÈ ¡‘…Ñ„½Èíô¤¹•Ğ ‰É•…Í½¸ˆ¤½È€ˆˆ¤¹ÍÑÉ¥À ¤(€€€¥˜Ù•É‘¥Ğ¹½Ğ¥¸ì‰™…¥Ñ¡™Õ°ˆ°€‰É•ÍÑÉÕÑÕÉ”‰ôè(€€€€€€€É…¥Í”IÕ¹Ñ¥µ•ÉÉ½È (€€€€€€€€€€€€‰Ñ½Á¥ŒÍ•É•…Ñ¥½¸Ù•É‘¥Ğ‘¥¹½ĞÁ½Í¥Ñ¥Ù•±ä‘•¥‘”€ˆ(€€€€€€€€€€€˜ˆ¡½ĞíÙ•É‘¥Ğ…Éô¤ìÍÑ½ÁÁ¥¹œ¥¹ÍÑ•…½˜Õ•ÍÍ¥¹œˆ(€€€€€€€€¤(€€€É•ÑÕÉ¸ì‰É•ÍÑÉÕÑÕÉ”ˆèÙ•É‘¥Ğ€ôô€‰É•ÍÑÉÕÑÕÉ”ˆ°€‰É•…Í½¸ˆèÉ•…Í½¹ô(()‘•˜}É•ÍÑÉÕÑÕÉ•}Ñ½Á¥Í}Ù¥…}…Á¤ (€€€É•½É‘Ìè±¥ÍÑm‘¥Ñt°€¨°µ•Ñ„è‘¥Ğ°(€€€Í½ÕÉ•}Ñ½Á¥}•á•ÉÁÑÌè±¥ÍÑm‘¥Ñtğ9½¹”€ô9½¹”°(€€€¡•…‘¥¹Ìè±¥ÍÑmÍÑÉtğ9½¹”€ô9½¹”°(¤€´ø±¥ÍÑm‘¥Ñtè(€€€€ˆˆ‰I”µÍ•É•…Ñ”½±±…ÁÍ•Ñ½Á¥ÌÕÍ¥¹œÉ½ÕÁ•Í½ÕÉ”µÑ½Á¥Œ•á•ÉÁÑÌ¸((€€€=¹±äÑ¡”Ñ½Á¥€™¥•±¥ÌÑ…­•¸™É½´Ñ¡”µ½‘•°°µ…Ñ¡•‰…¬Ñ¼Ñ¡”(€€€½É¥¥¹…°É½İÌ‰ä½¹•ÁĞÑ¥Ñ±”ƒŠP¹¼½¹•ÁĞ…¸‰”…‘‘•°‘É½ÁÁ•°½È(€€€É•İÉ¥ÑÑ•¸‰äÑ¡¥ÌÁ…ÍÌ¸¡•…‘¥¹Í€É•µ…¥¹Ì„½µÁ…Ñ¥‰¥±¥Ñä™…±±‰…¬™½È(€€€½±‘•È‘¥É•Ğ…±±•ÉÌìÑ¡”±¥Ù”Á¥Á•±¥¹”…±İ…åÌÍÕÁÁ±¥•ÌÍ½ÕÉ”•á•ÉÁÑÌ¸(€€€€ˆˆˆ(€€€¥µÁ½ÉĞ©Í½¸…Ì}©Í½¸((€€€Í½ÕÉ•}Ñ½Á¥}•á•ÉÁÑÌ€ô±¥ÍĞ¡Í½ÕÉ•}Ñ½Á¥}•á•ÉÁÑÌ½Èmt¤(€€€¥˜¹½ĞÍ½ÕÉ•}Ñ½Á¥}•á•ÉÁÑÌè(€€€€€€€Í½ÕÉ•}Ñ½Á¥}•á•ÉÁÑÌ€ôl(€€€€€€€€€€€ì‰Ñ½Á¥Œˆè¡•…‘¥¹œ°€‰•á•ÉÁĞˆè€ˆ‰ô™½È¡•…‘¥¹œ¥¸€¡¡•…‘¥¹Ì½Èmt¤(€€€€€€€t(€€€¡•…‘¥¹Ì€ôl(€€€€€€€€¡É½ÕÀ¹•Ğ ‰Ñ½Á¥Œˆ¤½È€ˆˆ¤¹ÍÑÉ¥À ¤(€€€€€€€™½ÈÉ½ÕÀ¥¸Í½ÕÉ•}Ñ½Á¥}•á•ÉÁÑÌ(€€€€€€€¥˜€¡É½ÕÀ¹•Ğ ‰Ñ½Á¥Œˆ¤½È€ˆˆ¤¹ÍÑÉ¥À ¤(€€€t(€€€•á•ÉÁÑ}‰Õ‘•Ğ€ôµ…à (€€€€€€€€ÄÉ|ÀÀÀ°€ÈÈÁ|ÀÀÀ€¼¼µ…à Ä°±•¸¡Í½ÕÉ•}Ñ½Á¥}•á•ÉÁÑÌ¤¤¤(€€€ÁÉ½µÁÑ}•á•ÉÁÑÌ€ôl(€€€€€€€ì(€€€€€€€€€€€€‰Ñ½Á¥Œˆè€¡É½ÕÀ¹•Ğ ‰Ñ½Á¥Œˆ¤½È€ˆˆ¤¹ÍÑÉ¥À ¤°(€€€€€€€€€€€€‰•á•ÉÁĞˆè}ÑÉ¥´¡É½ÕÀ¹•Ğ ‰•á•ÉÁĞˆ¤½È€ˆˆ°•á•ÉÁÑ}‰Õ‘•Ğ¤°(€€€€€€€ô(€€€€€€€™½ÈÉ½ÕÀ¥¸Í½ÕÉ•}Ñ½Á¥}•á•ÉÁÑÌ(€€€€€€€¥˜€¡É½ÕÀ¹•Ğ ‰Ñ½Á¥Œˆ¤½È€ˆˆ¤¹ÍÑÉ¥À ¤(€€€t(€€€É•½É‘Ì€ô}…ÍÍ¥¹}Ñ½Á¥Í}™É½µ}Í½ÕÉ•}•Ù¥‘•¹” (€€€€€€€É•½É‘Ì°Í½ÕÉ•}Ñ½Á¥}•á•ÉÁÑÌ¤(€€€ÍåÍÑ•´€ôÁÉ½µÁÑÌ¹•Ñ}Ñ•áĞ ‰½¹•ÁÑÌ¹Ñ½Á¥}ÍÑÉÕÑÕÉ”¹ÍåÍÑ•´ˆ¤(€€€Á…å±½…€ô}©Í½¸¹‘ÕµÁÌ¡ì‰É½İÌˆè}É•½É‘Í}Ñ½}…Á¥}É½İÌ¡É•½É‘Ì¥ô°•¹ÍÕÉ•}…Í¥¤õ…±Í”¤(€€€ÕÍ•È€ô€ (€€€€€€€}µ•Ñ…‘…Ñ…}‰±½¬¡µ•Ñ„¤(€€€€€€€€¬€‰q¹MQ%=8!%9L€¡É•…‘¥¹œ½É‘•È¤éq¸´€ˆ(€€€€€€€€¬€‰q¸´€ˆ¹©½¥¸¡¡•…‘¥¹Ì¤(€€€€€€€€¬€‰q¹q¹M=UIQ=A%aIAQL€¡ÍÑÉÕÑÕÉ…°¡•…‘¥¹Ì…±É•…‘ä¥¹¡•É¥Ñ•¤éq¸ˆ(€€€€€€€€¬}©Í½¸¹‘ÕµÁÌ¡ì‰Í½ÕÉ•}Ñ½Á¥ÌˆèÁÉ½µÁÑ}•á•ÉÁÑÍô°•¹ÍÕÉ•}…Í¥¤õ…±Í”¤(€€€€€€€€¬˜‰q¹q¹½¹•ÁĞµ…Àİ¥Ñ ½±±…ÁÍ•Ñ½Á¥Ì€¡í±•¸¡É•½É‘Ì¥ôÉ½İÌ¤éq¸ˆ(€€€€€€€€¬Á…å±½…(€€€€¤(€€€‘…Ñ„€ô}½Á•¹…¥}©Í½¸¡ÍåÍÑ•´°ÕÍ•È°ÁÕÉÁ½Í”ô‰½¹•ÁÑ}µ…ÁÁ¥¹œˆ¤(€€€€ŒÕ‘¥ĞĞè„Ñ¥Ñ±”…ÉÉ¥•‰äÑ¡”É•ÍÁ½¹Í”Õ¹‘•ÈQ]<Ñ½Á¥Ì…¹¹½Ğ(€€€€ŒÉ”µ¡½µ”•Ù•ÉäÍ…µ”µ¹…µ•É•½ÉÑ¼İ¡¥¡•Ù•ÈÉ½Ü…µ”±…ÍĞ¸=¹±ä…¸(€€€€ŒÕ¹…µ‰¥Õ½ÕÌÑ¥Ñ±”€´øÑ½Á¥Œµ…ÁÁ¥¹œ¥Ì…ÁÁ±¥•ì…µ‰¥Õ½ÕÌÑ¥Ñ±•Ì…É”(€€€€Œ±•™ĞÕ¹¡…¹•…¹¹…µ•¥¸Ñ¡”±½œ¸(€€€Ñ½Á¥Í}‰å}Ñ¥Ñ±”è‘¥ÑmÍÑÈ°Í•ÑmÍÑÉut€ôíô(€€€™½ÈÈ¥¸}½¹•ÁÑ}É½İÍ}Ñ½}É•½É‘Ì¡‘…Ñ„¤è(€€€€€€€¥˜¹½Ğ€¡È¹•Ğ ‰Ñ½Á¥Œˆ¤½È€ˆˆ¤¹ÍÑÉ¥À ¤è(€€€€€€€€€€€½¹Ñ¥¹Õ”(€€€€€€€­•ä€ô‰¤¹¹½Éµ…±¥é•}ÅÕ•ÍÑ¥½¹}Ñ•áĞ¡Él‰½¹•ÁÑ}Ñ¥Ñ±”‰t¤(€€€€€€€Ñ½Á¥Í}‰å}Ñ¥Ñ±”¹Í•Ñ‘•™…Õ±Ğ¡­•ä°Í•Ğ ¤¤¹…‘¡Él‰Ñ½Á¥Œ‰t¹ÍÑÉ¥À ¤¤(€€€Ñ½Á¥}‰å}Ñ¥Ñ±”€ôì(€€€€€€€­•äè¹•áĞ¡¥Ñ•È¡Ñ½Á¥Ì¤¤(€€€€€€€™½È­•ä°Ñ½Á¥Ì¥¸Ñ½Á¥Í}‰å}Ñ¥Ñ±”¹¥Ñ•µÌ ¤(€€€€€€€¥˜±•¸¡Ñ½Á¥Ì¤€ôô€Ä(€€€ô(€€€…µ‰¥Õ½ÕÍ}Ñ¥Ñ±•Ì€ôÍ½ÉÑ• (€€€€€€€­•ä™½È­•ä°Ñ½Á¥Ì¥¸Ñ½Á¥Í}‰å}Ñ¥Ñ±”¹¥Ñ•µÌ ¤¥˜±•¸¡Ñ½Á¥Ì¤€ø€Ä(€€€€¤(€€€¥˜…µ‰¥Õ½ÕÍ}Ñ¥Ñ±•Ìè(€€€€€€€ÁÉ½É•ÍÌ¹±½œ (€€€€€€€€€€€€‰Q½Á¥ŒÉ•ÍÑÉÕÑÕÉ”±•™Ğ€ˆ(€€€€€€€€€€€˜‰í±•¸¡…µ‰¥Õ½ÕÍ}Ñ¥Ñ±•Ì¥ôÉ•½ÉÑ¥Ñ±”¡Ì¤Õ¹¡…¹•èÑ¡”€ˆ(€€€€€€€€€€€€‰É•ÍÁ½¹Í”µ…ÁÁ•Ñ¡”Í…µ”Ñ¥Ñ±”Ñ¼µ½É”Ñ¡…¸½¹”Ñ½Á¥Œ€ˆ(€€€€€€€€€€€˜ˆ¡ìœ°€œ¹©½¥¸¡…µ‰¥Õ½ÕÍ}Ñ¥Ñ±•ÍlèÕt¥ô¤¸ˆ°(€€€€€€€€€€€±•Ù•°ô‰İ…É¹¥¹œˆ°(€€€€€€€€¤(€€€ÕÁ‘…Ñ•€ô€À(€€€™½ÈÉ•Œ¥¸É•½É‘Ìè(€€€€€€€¥˜}µ•Ñ¡½‘}…¹¡½É}¥‘Ì¡É•Œ¤è(€€€€€€€€€€€½¹Ñ¥¹Õ”(€€€€€€€¹•İ}Ñ½Á¥Œ€ôÑ½Á¥}‰å}Ñ¥Ñ±”¹•Ğ (€€€€€€€€€€€‰¤¹¹½Éµ…±¥é•}ÅÕ•ÍÑ¥½¹}Ñ•áĞ¡É•Œ¹•Ğ ‰½¹•ÁÑ}Ñ¥Ñ±”ˆ°€ˆˆ¤¤¤(€€€€€€€¥˜¹•İ}Ñ½Á¥Œ…¹¹•İ}Ñ½Á¥Œ€„ôÉ•Œ¹•Ğ ‰Ñ½Á¥Œˆ¤è(€€€€€€€€€€€É•l‰Ñ½Á¥Œ‰t€ô¹•İ}Ñ½Á¥Œ(€€€€€€€€€€€ÕÁ‘…Ñ•€¬ô€Ä(€€€‘¥ÍÑ¥¹Ğ€ôì¡È¹•Ğ ‰Ñ½Á¥Œˆ¤½È€ˆˆ¤¹ÍÑÉ¥À ¤¹±½İ•È ¤™½ÈÈ¥¸É•½É‘Íô(€€€‘¥ÍÑ¥¹Ğ¹‘¥Í…É ˆˆ¤(€€€ÁÉ½É•ÍÌ¹±½œ (€€€€€€€˜‰Q½Á¥ŒÉ”µÍ•É•…Ñ¥½¸èíÕÁ‘…Ñ•‘ôÉ½Ü¡Ì¤É•…ÍÍ¥¹•ì€ˆ(€€€€€€€˜‰í±•¸¡‘¥ÍÑ¥¹Ğ¥ô‘¥ÍÑ¥¹ĞÑ½Á¥Œ¡Ì¤¸ˆ°(€€€€€€€±•Ù•°ô‰ÍÕ•ÍÌˆ¥˜±•¸¡‘¥ÍÑ¥¹Ğ¤€ø€Ä•±Í”€‰İ…É¹¥¹œˆ°(€€€€¤(€€€É•ÑÕÉ¸}…ÍÍ¥¹}Ñ½Á¥Í}™É½µ}Í½ÕÉ•}•Ù¥‘•¹” (€€€€€€€É•½É‘Ì°Í½ÕÉ•}Ñ½Á¥}•á•ÉÁÑÌ¤(()‘•˜¡…ÁÑ•É}µ•Ñ…}Ù¥…}…Á¤ (€€€€¨°µ•Ñ„è‘¥Ğ°Ñ½Á¥Ìè±¥ÍÑm‘¥Ñt°±¥Ù”è‰½½°ğ9½¹”€ô9½¹”°(¤€´ø‘¥Ğè(€€€€ˆˆ‰¡…ÁÑ•È‘•ÍÉ¥ÁÑ¥½¸½‘ÕÉ…Ñ¥½¸€¬Á•ÈµÑ½Á¥Œ‘•ÍÉ¥ÁÑ¥½¹Ì¥¸½¹”A$Á…ÍÌ¸((€€€Ñ½Á¥Í€¥Ìmì‰Ñ½Á¥Œˆè€¸¸¸°€‰½¹•ÁÑÌˆèmÑ¥Ñ±•Ì¸¸¹uô°€¸¸¹u€¸I•ÑÕÉ¹Ì„(€€€€¡Á½ÍÍ¥‰±ä•µÁÑä¤‘¥Ğİ¥Ñ ¡…ÁÑ•É}‘•ÍÉ¥ÁÑ¥½¹€°(€€€¡…ÁÑ•É}‘ÕÉ…Ñ¥½¹}µ¥¹ÕÑ•Í€…¹Ñ½Á¥}‘•ÍÉ¥ÁÑ¥½¹Í€€¡­•å•‰ä(€€€¹½Éµ…±¥é•Ñ½Á¥ŒÑ¥Ñ±”¤ì…±±•ÉÌ™…±°‰…¬Ñ¼‘•Ñ•Éµ¥¹¥ÍÑ¥ŒÍÕµµ…É¥•Ì™½È(€€€…¹åÑ¡¥¹œµ¥ÍÍ¥¹œ¸(€€€€ˆˆˆ(€€€¥µÁ½ÉĞ©Í½¸…Ì}©Í½¸((€€€ÕÍ•}±¥Ù”€ô½¹™¥œ¹ÕÍ•}±¥Ù•}•¹•É…Ñ¥½¸ ¤¥˜±¥Ù”¥Ì9½¹”•±Í”±¥Ù”(€€€¥˜¹½ĞÕÍ•}±¥Ù”½È¹½ĞÑ½Á¥Ìè(€€€€€€€É•ÑÕÉ¸íô(€€€ÍåÍÑ•´€ôÁÉ½µÁÑÌ¹•Ñ}Ñ•áĞ ‰½¹•ÁÑÌ¹¡…ÁÑ•É}µ•Ñ„¹ÍåÍÑ•´ˆ¤(€€€ÕÍ•È€ô€ (€€€€€€€}µ•Ñ…‘…Ñ…}‰±½¬¡µ•Ñ„¤(€€€€€€€€¬€‰q¹Q½Á¥Ì…¹Ñ¡•¥È½¹•ÁÑÌéq¸ˆ(€€€€€€€€¬}©Í½¸¹‘ÕµÁÌ¡ì‰Ñ½Á¥ÌˆèÑ½Á¥Íô°•¹ÍÕÉ•}…Í¥¤õ…±Í”¤(€€€€¤(€€€ÁÉ½É•ÍÌ¹±½œ (€€€€€€€€‰]É¥Ñ¥¹œ¡…ÁÑ•È½Ñ½Á¥Œµ•Ñ…‘…Ñ„€¡¡…ÁÑ•È‘•ÍÉ¥ÁÑ¥½¸°‘ÕÉ…Ñ¥½¸°€ˆ(€€€€€€€€‰Ñ½Á¥Œ‘•ÍÉ¥ÁÑ¥½¹Ì¤Ù¥„A$Á…ÍÌ¸ˆ¤(€€€‘…Ñ„€ô}½Á•¹…¥}©Í½¸¡ÍåÍÑ•´°ÕÍ•È°ÁÕÉÁ½Í”ô‰µ•Ñ…‘…Ñ„ˆ¤(€€€½ÕĞè‘¥Ğ€ôíô(€€€‘•ÍÉ¥ÁÑ¥½¸€ô€¡‘…Ñ„¹•Ğ ‰¡…ÁÑ•É}‘•ÍÉ¥ÁÑ¥½¸ˆ¤½È€ˆˆ¤¹ÍÑÉ¥À ¤(€€€¥˜‘•ÍÉ¥ÁÑ¥½¸è(€€€€€€€½ÕÑl‰¡…ÁÑ•É}‘•ÍÉ¥ÁÑ¥½¸‰t€ô‘•ÍÉ¥ÁÑ¥½¸(€€€ÑÉäè(€€€€€€€µ¥¹ÕÑ•Ì€ô¥¹Ğ¡™±½…Ğ¡‘…Ñ„¹•Ğ ‰¡…ÁÑ•É}‘ÕÉ…Ñ¥½¹}µ¥¹ÕÑ•Ìˆ¤½È€À¤¤(€€€•á•ÁĞ€¡QåÁ•ÉÉ½È°Y…±Õ•ÉÉ½È¤è(€€€€€€€µ¥¹ÕÑ•Ì€ô€À(€€€™¥¹…±¥é•€ô¥¹Ğ¡µ•Ñ„¹•Ğ ‰™¥¹…±¥é•‘}‘ÕÉ…Ñ¥½¹}µ¥¹ÕÑ•Ìˆ¤½È€À¤(€€€¥˜™¥¹…±¥é•€ø€Àè(€€€€€€€½ÕÑl‰¡…ÁÑ•É}‘ÕÉ…Ñ¥½¹}µ¥¹ÕÑ•Ì‰t€ô™¥¹…±¥é•(€€€•±¥˜µ¥¹ÕÑ•Ì€ø€Àè(€€€€€€€½ÕÑl‰¡…ÁÑ•É}‘ÕÉ…Ñ¥½¹}µ¥¹ÕÑ•Ì‰t€ôµ¥¹ÕÑ•Ì(€€€Ñ½Á¥}‘•ÍÉ¥ÁÑ¥½¹Ìè‘¥ÑmÍÑÈ°ÍÑÉt€ôíô(€€€™½ÈÉ½Ü¥¸‘…Ñ„¹•Ğ ‰Ñ½Á¥Ìˆ°mt¤½Èmtè(€€€€€€€¥˜¹½Ğ¥Í¥¹ÍÑ…¹”¡É½Ü°‘¥Ğ¤è(€€€€€€€€€€€½¹Ñ¥¹Õ”(€€€€€€€Ñ½Á¥Œ€ô€¡É½Ü¹•Ğ ‰Ñ½Á¥Œˆ¤½È€ˆˆ¤¹ÍÑÉ¥À ¤(€€€€€€€Ñ½Á¥}‘•ÍÉ¥ÁÑ¥½¸€ô€¡É½Ü¹•Ğ ‰Ñ½Á¥}‘•ÍÉ¥ÁÑ¥½¸ˆ¤½È€ˆˆ¤¹ÍÑÉ¥À ¤(€€€€€€€¥˜Ñ½Á¥Œ…¹Ñ½Á¥}‘•ÍÉ¥ÁÑ¥½¸è(€€€€€€€€€€€Ñ½Á¥}‘•ÍÉ¥ÁÑ¥½¹Ím‰¤¹¹½Éµ…±¥é•}ÅÕ•ÍÑ¥½¹}Ñ•áĞ¡Ñ½Á¥Œ¥t€ôÑ½Á¥}‘•ÍÉ¥ÁÑ¥½¸(€€€¥˜Ñ½Á¥}‘•ÍÉ¥ÁÑ¥½¹Ìè(€€€€€€€½ÕÑl‰Ñ½Á¥}‘•ÍÉ¥ÁÑ¥½¹Ì‰t€ôÑ½Á¥}‘•ÍÉ¥ÁÑ¥½¹Ì(€€€É•ÑÕÉ¸½ÕĞ(()}1e}=9AQ}!-A=%9Q}M!5€ô€È)}=9AQ}!-A=%9Q}M!5€ô€Ì)}=9AQ}!-A=%9Q}=I5P€ô€‰…•¥Ìµ½¹•ÁĞµÍÑ…”µ¡¥ÍÑ½Éäˆ)}QeA}Qa=9=5e}!-A=%9Q}MQ€ô€‰ÑåÁ•}Ñ…á½¹½µå}É•…‘äˆ)}=9AQ}!-A=%9Q}MQ€ô€‰ÁÉ•}ÑåÁ•}…ÍÍ¥¹µ•¹Ğˆ)A!MÍ}AI}I1M}%1€ô€‰Á¡…Í”Í}ÁÉ•}É•±•…Í”ˆ)A!MÍ}AI}I1M}M!5€ô€Ä((ŒMÑ…”Ù•ÉÍ¥½¹Ì‘•ÍÉ¥‰”Ñ¡”Í•É¥…±¥é•…ÉÑ¥™…Ğ½¹ÑÉ…Ğ°¹½ĞÑ¡”¥Ğ(ŒÉ•Ù¥Í¥½¸Ñ¡…ĞÁÉ½‘Õ•¥Ğ¸€±…Ñ•È‘•Á±½åµ•¹Ğµ…äÑ¡•É•™½É”É•ÕÍ”…¸½±‘•È(Œ¡•­Á½¥¹Ğİ¡•¸¥ÑÌÍÑ…”½¹ÑÉ…Ğ¥ÌÍÑ¥±°…•ÁÑ•¸€	ÕµÀ½¹±äÑ¡”ÍÑ…”(Œİ¡½Í”Á…å±½…½Èµ•…¹¥¹œ‰•½µ•Ì¥¹½µÁ…Ñ¥‰±”¸)}=9AQ}!-A=%9Q}MQL€ôì(€€€€ŒA¡…Í”€ÌÍ½ÕÉ”Ù•É¥™¥…Ñ¥½¸ÉÕ¹Ì‰•™½É”½¹•ÁĞÁ…ÉÍ¥¹œ¸Q¡¥Ì‰½½ÑÍÑÉ…À(€€€€ŒÍÑ…”•á¥ÍÑÌ½¹±äÍ¼„Í½ÕÉ”‘¥ÍÉ•Á…¹ä…¹¥ÑÌ‰½Õ¹‘•…¹‘¥‘…Ñ”(€€€€ŒÁ…­•Ğ…¸‰”Á…ÕÍ•‘ÕÉ…‰±ä¸%Ğ¥Ì‘•±¥‰•É…Ñ•±ä•á±Õ‘•™É½´(€€€€Œ}A=MQ}=9AQ}!-A=%9Q}MQM€‰•±½Üè…™Ñ•ÈÑ¡”Í½ÕÉ”‘•¥Í¥½¸¥Ì(€€€€ŒÉ•Í½±Ù•°½É‘¥¹…Éä½¹•ÁĞ•¹•É…Ñ¥½¸ÍÑ…ÉÑÌ…ĞÑ¡”Í­•±•Ñ½¸ÍÑ…”¸(€€€€‰Í½ÕÉ•}É…Á¡}É•Ù¥•Üˆèì(€€€€€€€€‰½É‘•Èˆè€Ô°(€€€€€€€€‰Ù•ÉÍ¥½¸ˆè€Ä°(€€€€€€€€‰ÁÉ½É•ÍÌˆè€À¸ÀÔ°(€€€€€€€€‰±…‰•°ˆè€‰M½ÕÉ”É…Á Á…ÕÍ•™½Èå½ÕÈ‘•¥Í¥½¸ˆ°(€€€ô°(€€€€‰Í­•±•Ñ½¹}¡Õ¹­Ìˆèì(€€€€€€€€‰½É‘•Èˆè€ÄÀ°(€€€€€€€€‰Ù•ÉÍ¥½¸ˆè€Ä°(€€€€€€€€‰ÁÉ½É•ÍÌˆè€À¸ÈĞ°(€€€€€€€€‰±…‰•°ˆè€‰½¹•ÁĞÍ­•±•Ñ½¸¡Õ¹­Ìˆ°(€€€ô°(€€€€‰Í­•±•Ñ½¹}½µÁ±•Ñ”ˆèì(€€€€€€€€‰½É‘•Èˆè€ÈÀ°(€€€€€€€€‰Ù•ÉÍ¥½¸ˆè€Ä°(€€€€€€€€‰ÁÉ½É•ÍÌˆè€À¸ÈĞ°(€€€€€€€€‰±…‰•°ˆè€‰½¹•ÁĞÍ­•±•Ñ½¸½µÁ±•Ñ”ˆ°(€€€ô°(€€€€‰Í½ÕÉ•}Ñ½Á¥}É•Ù¥•Üˆèì(€€€€€€€€‰½É‘•Èˆè€ÈÔ°(€€€€€€€€‰Ù•ÉÍ¥½¸ˆè€Ä°(€€€€€€€€‰ÁÉ½É•ÍÌˆè€À¸ÌÔ°(€€€€€€€€‰±…‰•°ˆè€‰M½ÕÉ”µÑ½Á¥ŒÑ½Á½±½äÉ•…‘ä™½Èå½ÕÈ‘•¥Í¥½¸ˆ°(€€€ô°(€€€€‰…¹½¹¥…±}Í­•±•Ñ½¸ˆèì(€€€€€€€€‰½É‘•Èˆè€ÌÀ°(€€€€€€€€‰Ù•ÉÍ¥½¸ˆè€Ä°(€€€€€€€€‰ÁÉ½É•ÍÌˆè€À¸ÌÔ°(€€€€€€€€‰±…‰•°ˆè€‰…¹½¹¥…°Í­•±•Ñ½¸…¹Í½ÕÉ”Ñ½Á¥Ì½µÁ±•Ñ”ˆ°(€€€ô°(€€€€‰‘•ÍÉ¥ÁÑ¥½¹}µ•Ñ¡½‘}Í¹…ÁÍ¡½Ğˆèì(€€€€€€€€‰½É‘•Èˆè€ĞÀ°(€€€€€€€€‰Ù•ÉÍ¥½¸ˆè€Ä°(€€€€€€€€‰ÁÉ½É•ÍÌˆè€À¸ÔÔ°(€€€€€€€€‰±…‰•°ˆè€‰•ÍÉ¥ÁÑ¥½¹Ì…¹µ•Ñ¡½Í¹…ÁÍ¡½Ğ½µÁ±•Ñ”ˆ°(€€€ô°(€€€€‰ÅÕ•ÍÑ¥½¹}¥¹Ù•¹Ñ½Éäˆèì(€€€€€€€€‰½É‘•Èˆè€ÔÀ°(€€€€€€€€‰Ù•ÉÍ¥½¸ˆè€Ì°(€€€€€€€€‰ÁÉ½É•ÍÌˆè€À¸ÜÀ°(€€€€€€€€‰±…‰•°ˆè€‰EÕ•ÍÑ¥½¸…¹Ñ…Í¬¥¹Ù•¹Ñ½Éä½µÁ±•Ñ”ˆ°(€€€ô°(€€€}QeA}Qa=9=5e}!-A=%9Q}MQèì(€€€€€€€€‰½É‘•Èˆè€ÔÔ°(€€€€€€€€‰Ù•ÉÍ¥½¸ˆè€Ì°(€€€€€€€€‰ÁÉ½É•ÍÌˆè€À¸ÜØ°(€€€€€€€€‰±…‰•°ˆè€‰I•ÕÍ…‰±”QåÁ”Ñ…á½¹½µäÉ•…‘ä™½ÈÉ…¹Õ±…É¥ÑäÉ•Ù¥•Üˆ°(€€€ô°(€€€}=9AQ}!-A=%9Q}MQèì(€€€€€€€€‰½É‘•Èˆè€ØÀ°(€€€€€€€€‰Ù•ÉÍ¥½¸ˆè€Ì°(€€€€€€€€‰ÁÉ½É•ÍÌˆè€À¸àÄ°(€€€€€€€€‰±…‰•°ˆè€‰I•ÕÍ…‰±”QåÁ•Ìµ¥¹•ìÉ•…‘ä™½ÈQåÁ”…ÍÍ¥¹µ•¹Ğˆ°(€€€ô°(€€€€‰Á½ÍÑ}ÑåÁ•}…ÍÍ¥¹µ•¹Ğˆèì(€€€€€€€€‰½É‘•Èˆè€ÜÀ°(€€€€€€€€‰Ù•ÉÍ¥½¸ˆè€Ü°(€€€€€€€€‰ÁÉ½É•ÍÌˆè€À¸äÄ°(€€€€€€€€‰±…‰•°ˆè€‰QåÁ”…ÍÍ¥¹µ•¹Ğ…¹…Ñ¥Ù¥Ñä¡Õ‰Ì½µÁ±•Ñ”ˆ°(€€€ô°(€€€€‰™¥¹…±}½¹Ñ•¹Ñ}É•…‘äˆèì(€€€€€€€€‰½É‘•Èˆè€àÀ°(€€€€€€€€‰Ù•ÉÍ¥½¸ˆè€à°(€€€€€€€€‰ÁÉ½É•ÍÌˆè€À¸äà°(€€€€€€€€‰±…‰•°ˆè€‰¥¹…°½¹Ñ•¹ĞÉ•…‘ä™½È‘•Ñ•Éµ¥¹¥ÍÑ¥ŒÙ…±¥‘…Ñ¥½¸ˆ°(€€€ô°)ô)}A=MQ}=9AQ}!-A=%9Q}MQL€ôì(€€€€‰Í­•±•Ñ½¹}¡Õ¹­Ìˆ°(€€€€‰Í­•±•Ñ½¹}½µÁ±•Ñ”ˆ°(€€€€‰Í½ÕÉ•}Ñ½Á¥}É•Ù¥•Üˆ°(€€€€‰…¹½¹¥…±}Í­•±•Ñ½¸ˆ°(€€€€‰‘•ÍÉ¥ÁÑ¥½¹}µ•Ñ¡½‘}Í¹…ÁÍ¡½Ğˆ°(€€€€‰ÅÕ•ÍÑ¥½¹}¥¹Ù•¹Ñ½Éäˆ°(€€€}QeA}Qa=9=5e}!-A=%9Q}MQ°(€€€}=9AQ}!-A=%9Q}MQ°(€€€€‰Á½ÍÑ}ÑåÁ•}…ÍÍ¥¹µ•¹Ğˆ°(€€€€‰™¥¹…±}½¹Ñ•¹Ñ}É•…‘äˆ°)ô)}1e}AI}I1M}MQ}YIM%=9L€ôì(€€€€‰Á½ÍÑ}ÑåÁ•}…ÍÍ¥¹µ•¹Ğˆè€Ø°(€€€€‰™¥¹…±}½¹Ñ•¹Ñ}É•…‘äˆè€Ü°)ô(()‘•˜}Í•É¥…±¥é•}µ•Ñ¡½‘}É½İ}Í¹…ÁÍ¡½Ğ (€€€Í¹…ÁÍ¡½Ğè‘¥ÑmÑÕÁ±•mÍÑÈ°ÍÑÉt°‘¥Ñt°(¤€´ø±¥ÍÑm‘¥Ñtè(€€€É•ÑÕÉ¸l(€€€€€€€ì(€€€€€€€€€€€€‰…¹¡½É}¥ˆè…¹¡½É}¥°(€€€€€€€€€€€€‰Ñ½Á¥}­•äˆèÑ½Á¥}­•ä°(€€€€€€€€€€€€‰É½Üˆè½Áä¹‘••Á½Áä¡É½Ü¤°(€€€€€€€ô(€€€€€€€™½È€¡…¹¡½É}¥°Ñ½Á¥}­•ä¤°É½Ü¥¸Í¹…ÁÍ¡½Ğ¹¥Ñ•µÌ ¤(€€€t(()‘•˜}‘•Í•É¥…±¥é•}µ•Ñ¡½‘}É½İ}Í¹…ÁÍ¡½Ğ (€€€•¹ÑÉ¥•Ìè±¥ÍÑm‘¥Ñtğ9½¹”°(¤€´ø‘¥ÑmÑÕÁ±•mÍÑÈ°ÍÑÉt°‘¥Ñtè(€€€Í¹…ÁÍ¡½Ğè‘¥ÑmÑÕÁ±•mÍÑÈ°ÍÑÉt°‘¥Ñt€ôíô(€€€™½È•¹ÑÉä¥¸•¹ÑÉ¥•Ì½Èmtè(€€€€€€€¥˜¹½Ğ¥Í¥¹ÍÑ…¹”¡•¹ÑÉä°‘¥Ğ¤½È¹½Ğ¥Í¥¹ÍÑ…¹”¡•¹ÑÉä¹•Ğ ‰É½Üˆ¤°‘¥Ğ¤è(€€€€€€€€€€€½¹Ñ¥¹Õ”(€€€€€€€…¹¡½É}¥€ôÍÑÈ¡•¹ÑÉä¹•Ğ ‰…¹¡½É}¥ˆ¤½È€ˆˆ¤¹ÍÑÉ¥À ¤¹ÕÁÁ•È ¤(€€€€€€€Ñ½Á¥}­•ä€ôÍÑÈ¡•¹ÑÉä¹•Ğ ‰Ñ½Á¥}­•äˆ¤½È€ˆˆ¤¹ÍÑÉ¥À ¤(€€€€€€€¥˜…¹¡½É}¥…¹Ñ½Á¥}­•äè(€€€€€€€€€€€Í¹…ÁÍ¡½Ñl¡…¹¡½É}¥°Ñ½Á¥}­•ä¥t€ô½Áä¹‘••Á½Áä¡•¹ÑÉål‰É½Ü‰t¤(€€€É•ÑÕÉ¸Í¹…ÁÍ¡½Ğ(()‘•˜}½¹•ÁÑ}¡•­Á½¥¹Ñ}•¹ÑÉ¥•Ì¡¡•­Á½¥¹Ğè‘¥Ğğ9½¹”¤€´ø±¥ÍÑm‘¥Ñtè(€€€€ˆˆ‰I•ÑÕÉ¸Í•É¥…±¥é•ÍÑ…”•¹ÑÉ¥•Ì™É½´„ØÌ•¹Ù•±½Á”½È±•…ä•¹ÑÉä¸ˆˆˆ(€€€¥˜¹½Ğ¥Í¥¹ÍÑ…¹”¡¡•­Á½¥¹Ğ°‘¥Ğ¤è(€€€€€€€É•ÑÕÉ¸mt(€€€¥˜¹½Ğ¡•­Á½¥¹Ğè(€€€€€€€É•ÑÕÉ¸mt(€€€¥˜¡•­Á½¥¹Ğ¹•Ğ ‰¡•­Á½¥¹Ñ}™½Éµ…Ğˆ¤€ôô}=9AQ}!-A=%9Q}=I5Pè(€€€€€€€¥˜¡•­Á½¥¹Ğ¹•Ğ ‰Í¡•µ…}Ù•ÉÍ¥½¸ˆ¤€„ô}=9AQ}!-A=%9Q}M!5è(€€€€€€€€€€€É•ÑÕÉ¸mt(€€€€€€€¡¥ÍÑ½Éä€ô¡•­Á½¥¹Ğ¹•Ğ ‰¡•­Á½¥¹ÑÌˆ¤(€€€€€€€¥˜¹½Ğ¥Í¥¹ÍÑ…¹”¡¡¥ÍÑ½Éä°±¥ÍĞ¤è(€€€€€€€€€€€É•ÑÕÉ¸mt(€€€€€€€É•ÑÕÉ¸m•¹ÑÉä™½È•¹ÑÉä¥¸¡¥ÍÑ½Éä¥˜¥Í¥¹ÍÑ…¹”¡•¹ÑÉä°‘¥Ğ¥t(€€€É•ÑÕÉ¸m¡•­Á½¥¹Ñt(()‘•˜}¡•­Á½¥¹Ñ}¡…Í}™¥•±‘Ì¡¡•­Á½¥¹Ğè‘¥Ğ°€©É•ÅÕ¥É•µ•¹ÑÌèÑÕÁ±•mÍÑÈ°ÑåÁ•t¤€´ø‰½½°è(€€€É•ÑÕÉ¸…±°¡¥Í¥¹ÍÑ…¹”¡¡•­Á½¥¹Ğ¹•Ğ¡™¥•±¤°•áÁ•Ñ•¤™½È™¥•±°•áÁ•Ñ•¥¸É•ÅÕ¥É•µ•¹ÑÌ¤(()‘•˜Á¡…Í”Í}ÁÉ•}É•±•…Í•}‰Õ¹‘±” (€€€ÁÉ•}µ…Àè5…ÁÁ¥¹mÍÑÈ°¹åt°(€€€ÁÉ•}ÅÕ•ÍÑ¥½¹Ìè5…ÁÁ¥¹mÍÑÈ°¹åt°(€€€€¨°(€€€Í¹…ÁÍ¡½Ñ}İÉ¥Ñ•Ìè5…ÁÁ¥¹mÍÑÈ°¹åtğ9½¹”€ô9½¹”°(¤€´ø‘¥ÑmÍÑÈ°¹åtè(€€€€ˆˆ‰Q¡”•á…ĞA¡…Í”€ÌAÉ”½ÕÑÁÕĞ…ÉÉ¥•Ñ¡É½Õ ¡•­Á½¥¹Ğ…¹É•±•…Í”¸ˆˆˆ((€€€É•ÑÕÉ¸ì(€€€€€€€€‰Í¡•µ…}Ù•ÉÍ¥½¸ˆèA!MÍ}AI}I1M}M!5°(€€€€€€€€‰ÁÉ•}µ…Àˆè½Áä¹‘••Á½Áä¡‘¥Ğ¡ÁÉ•}µ…À¤¤°(€€€€€€€€‰ÁÉ•}ÅÕ•ÍÑ¥½¹Ìˆè½Áä¹‘••Á½Áä¡‘¥Ğ¡ÁÉ•}ÅÕ•ÍÑ¥½¹Ì¤¤°(€€€€€€€€‰Í¹…ÁÍ¡½Ñ}İÉ¥Ñ•Ìˆè½Áä¹‘••Á½Áä¡‘¥Ğ¡Í¹…ÁÍ¡½Ñ}İÉ¥Ñ•Ì½Èíô¤¤°(€€€ô(()‘•˜Ù…±¥‘}Á¡…Í”Í}ÁÉ•}É•±•…Í•}‰Õ¹‘±”¡Ù…±Õ”è½‰©•Ğ¤€´ø‰½½°è(€€€€ˆˆ‰5•¡…¹¥…°Í¡…Á”…Ñ”ì…¸…ÕÑ¡½É••µÁÑäµ…À½ÅÕ•ÍÑ¥½¹Ì¥ÌÙ…±¥¸ˆˆˆ((€€€É•ÑÕÉ¸‰½½° (€€€€€€€¥Í¥¹ÍÑ…¹”¡Ù…±Õ”°5…ÁÁ¥¹œ¤(€€€€€€€…¹Ù…±Õ”¹•Ğ ‰Í¡•µ…}Ù•ÉÍ¥½¸ˆ¤€ôôA!MÍ}AI}I1M}M!5(€€€€€€€…¹¥Í¥¹ÍÑ…¹”¡Ù…±Õ”¹•Ğ ‰ÁÉ•}µ…Àˆ¤°5…ÁÁ¥¹œ¤(€€€€€€€…¹¥Í¥¹ÍÑ…¹”¡Ù…±Õ”¹•Ğ ‰ÁÉ•}ÅÕ•ÍÑ¥½¹Ìˆ¤°5…ÁÁ¥¹œ¤(€€€€€€€…¹¥Í¥¹ÍÑ…¹”¡Ù…±Õ”¹•Ğ ‰Í¹…ÁÍ¡½Ñ}İÉ¥Ñ•Ìˆ°íô¤°5…ÁÁ¥¹œ¤(€€€€¤(()‘•˜}Ù…±¥‘}½µÁ±•Ñ•‘}Í­•±•Ñ½¹}¡Õ¹­Ì¡Ù…±Õ”¤€´ø‰½½°è(€€€¥˜¹½Ğ¥Í¥¹ÍÑ…¹”¡Ù…±Õ”°±¥ÍĞ¤½È¹½ĞÙ…±Õ”è(€€€€€€€É•ÑÕÉ¸…±Í”(€€€•áÁ•Ñ•‘}¥¹‘•à€ô€Ä(€€€™½È¥Ñ•´¥¸Ù…±Õ”è(€€€€€€€¥˜€ (€€€€€€€€€€€¹½Ğ¥Í¥¹ÍÑ…¹”¡¥Ñ•´°‘¥Ğ¤(€€€€€€€€€€€½È¥Ñ•´¹•Ğ ‰¡Õ¹­}¥¹‘•àˆ¤€„ô•áÁ•Ñ•‘}¥¹‘•à(€€€€€€€€€€€½È¹½Ğ¥Í¥¹ÍÑ…¹”¡¥Ñ•´¹•Ğ ‰¡Õ¹­}Í¡„ÈÔØˆ¤°ÍÑÈ¤(€€€€€€€€€€€½È¹½Ğ¥Ñ•´¹•Ğ ‰¡Õ¹­}Í¡„ÈÔØˆ¤(€€€€€€€€€€€½È¹½Ğ¥Í¥¹ÍÑ…¹”¡¥Ñ•´¹•Ğ ‰É•½É‘Ìˆ¤°±¥ÍĞ¤(€€€€€€€€¤è(€€€€€€€€€€€É•ÑÕÉ¸…±Í”(€€€€€€€•áÁ•Ñ•‘}¥¹‘•à€¬ô€Ä(€€€É•ÑÕÉ¸QÉÕ”(()‘•˜}ÑåÁ•}É…¹Õ±…É¥Ñå}É•Á±…å}Í•…±}Ù…±¥¡¡•­Á½¥¹Ğè‘¥Ğ¤€´ø‰½½°è(€€€€ˆˆ‰Y…±¥‘…Ñ”…¸…ÁÁ±¥•¡Õµ…¸QåÁ”‘•¥Í¥½¸…Ğ•Ù•ÉäÉ•ÍÕµ…‰±”ÍÑ…”¸ˆˆˆ((€€€µ¥¹•‘}ÑåÁ•Ì€ô¡•­Á½¥¹Ğ¹•Ğ ‰µ¥¹•‘}ÑåÁ•Ìˆ¤(€€€¥˜¹½Ğ¥Í¥¹ÍÑ…¹”¡µ¥¹•‘}ÑåÁ•Ì°‘¥Ğ¤è(€€€€€€€É•ÑÕÉ¸QÉÕ”(€€€É•Ù¥•Ü€ôµ¥¹•‘}ÑåÁ•Ì¹•Ğ ‰}É…¹Õ±…É¥Ñå}É•Ù¥•Üˆ¤(€€€¥˜¹½Ğ¥Í¥¹ÍÑ…¹”¡É•Ù¥•Ü°‘¥Ğ¤è(€€€€€€€É•ÑÕÉ¸QÉÕ”(€€€±…ÍÑ}…ÑÑ•µÁĞ€ôÉ•Ù¥•Ü¹•Ğ ‰±…ÍÑ}…ÑÑ•µÁĞˆ¤(€€€¥˜±…ÍÑ}…ÑÑ•µÁĞ¥Ì¹½Ğ9½¹”…¹¹½Ğ€ (€€€€€€€¥Í¥¹ÍÑ…¹”¡±…ÍÑ}…ÑÑ•µÁĞ°‘¥Ğ¤(€€€€€€€…¹ÍÑÈ¡±…ÍÑ}…ÑÑ•µÁĞ¹•Ğ ‰ÍÑ…ÑÕÌˆ¤½È€ˆˆ¤¥¸ì(€€€€€€€€€€€€‰É•ÅÕ•ÍÑ}ÍÑ…ÉÑ•ˆ°€‰ÍÕ••‘•ˆ°€‰¥¹½µÁ±•Ñ”ˆ°€‰™…¥±•ˆ°(€€€€€€€ô(€€€€€€€…¹‰½½°¡ÍÑÈ¡±…ÍÑ}…ÑÑ•µÁĞ¹•Ğ ‰‘•¥Í¥½¹}¥ˆ¤½È€ˆˆ¤¤(€€€€€€€…¹‰½½°¡É”¹™Õ±±µ…Ñ  (€€€€€€€€€€€È‰lÀ´å„µ™uìØÑôˆ°(€€€€€€€€€€€ÍÑÈ¡±…ÍÑ}…ÑÑ•µÁĞ¹•Ğ ‰½¹Ñ•áÑ}¡…Í ˆ¤½È€ˆˆ¤°(€€€€€€€€¤¤(€€€€¤è(€€€€€€€É•ÑÕÉ¸…±Í”(€€€Á•¹‘¥¹}™½±±½İÕÀ€ôÉ•Ù¥•Ü¹•Ğ ‰Á•¹‘¥¹}™½±±½İÕÀˆ¤(€€€¥˜Á•¹‘¥¹}™½±±½İÕÀ¥Ì¹½Ğ9½¹”…¹¹½Ğ€ (€€€€€€€¥Í¥¹ÍÑ…¹”¡Á•¹‘¥¹}™½±±½İÕÀ°‘¥Ğ¤(€€€€€€€…¹‰½½°¡ÍÑÈ¡Á•¹‘¥¹}™½±±½İÕÀ¹•Ğ ‰ÁÉ¥½É}‘•¥Í¥½¹}¥ˆ¤½È€ˆˆ¤¤(€€€€€€€…¹‰½½°¡ÍÑÈ¡Á•¹‘¥¹}™½±±½İÕÀ¹•Ğ ‰™…¥±ÕÉ”ˆ¤½È€ˆˆ¤¤(€€€€¤è(€€€€€€€É•ÑÕÉ¸…±Í”(€€€…ÁÁ±¥•€ôÉ•Ù¥•Ü¹•Ğ ‰¡Õµ…¹}É•Í½±ÕÑ¥½¸ˆ¤(€€€¥˜¹½Ğ¥Í¥¹ÍÑ…¹”¡…ÁÁ±¥•°‘¥Ğ¤½È¹½Ğ…ÁÁ±¥•¹•Ğ ‰‘•¥Í¥½¹}¥ˆ¤è(€€€€€€€É•ÑÕÉ¸QÉÕ”(€€€ÍÑ½É•€ôÍÑÈ¡…ÁÁ±¥•¹•Ğ ‰Í•µ…¹Ñ¥}½¹ÑÉ…Ñ}¡…Í ˆ¤½È€ˆˆ¤(€€€¥˜¹½ĞÉ”¹™Õ±±µ…Ñ ¡È‰lÀ´å„µ™uìØÑôˆ°ÍÑ½É•¤è(€€€€€€€É•ÑÕÉ¸…±Í”(€€€•áÁ•Ñ•€ôÑåÁ•}É…¹Õ±…É¥Ñå}‘•¥Í¥½¸¹…ÁÁ±¥•‘}É•ÍÕ±Ñ}Í•µ…¹Ñ¥}¡…Í  (€€€€€€€¥¹Ù•¹Ñ½Éäõ¡•­Á½¥¹Ğ¹•Ğ ‰ÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½Éäˆ¤°(€€€€€€€µ¥¹•‘}ÑåÁ•Ìõµ¥¹•‘}ÑåÁ•Ì°(€€€€¤(€€€É•ÑÕÉ¸ÍÑ½É•€ôô•áÁ•Ñ•(()‘•˜}ÑåÁ•}…Í•}¡•­Á½¥¹Ñ}Á±…•µ•¹Ñ}±•‘•É}Ù…±¥¡¡•­Á½¥¹Ğè‘¥Ğ¤€´ø‰½½°è(€€€€ˆˆ‰Y…±¥‘…Ñ”Ñ¡”Á•ÈµE%Í•µ…¹Ñ¥Œµ½İ¹•È…ÕÑ¡½É¥Ñä½˜„±…Ñ”¡•­Á½¥¹Ğ¸((€€€Á½ÍÑ}ÑåÁ•}…ÍÍ¥¹µ•¹Ñ€¥ÌÑ¡”™¥ÉÍĞÍÑ…”…±±½İ•Ñ¼Í­¥ÀQåÁ”½…Í”(€€€½İ¹•ÉÍ¡¥À•ÉÑ¥™¥…Ñ¥½¸½¸É•ÍÕµ”¸€Q¡”½±‘•ÈÅ¥µÑ¼µÉ•¹‘•É•µ¡½ÍĞ±•‘•È(€€€‘½•Ì¹½ĞÁÉ½Ù”Í•µ…¹Ñ¥Œ½İ¹•ÉÍ¡¥À°Í¼„¹½¸µ•µÁÑä¥¹Ù•¹Ñ½Éä¥ÌÉ•ÍÕµ…‰±”…Ğ(€€€Ñ¡…Ğ‰½Õ¹‘…Éä½¹±äİ¡•¸‰½Ñ Í•É¥…±¥é•½¹Ñ…¥¹•ÉÌ…ÉÉäÑ¡”Í…µ”Ù…±¥ØÈ(€€€Á±…•µ•¹Ğ±•‘•È…¹•Ù•ÉäE%µÍÁ•¥™¥ŒÁÉ½©•Ñ¥½¸ÍÑ¥±°…É••Ìİ¥Ñ ¥Ğ¸((€€€µÁÑäµ¥¹Ù•¹Ñ½ÉäÍÑ…¹‘…±½¹”½½™™±¥¹”ÑÉ…¹Í™½Éµ…Ñ¥½¹ÌÉ•µ…¥¸Á½ÉÑ…‰±”…¹‘¼(€€€¹½Ğµ…¹Õ™…ÑÕÉ”„µ•…¹¥¹±•ÍÌ•µÁÑäÁ±…•µ•¹Ğ±•‘•È¸(€€€€ˆˆˆ((€€€¥¹Ù•¹Ñ½Éä€ô¡•­Á½¥¹Ğ¹•Ğ ‰ÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½Éäˆ¤(€€€µ¥¹•‘}ÑåÁ•Ì€ô¡•­Á½¥¹Ğ¹•Ğ ‰µ¥¹•‘}ÑåÁ•Ìˆ¤(€€€¥˜¹½Ğ¥Í¥¹ÍÑ…¹”¡¥¹Ù•¹Ñ½Éä°‘¥Ğ¤½È¹½Ğ¥Í¥¹ÍÑ…¹”¡µ¥¹•‘}ÑåÁ•Ì°‘¥Ğ¤è(€€€€€€€É•ÑÕÉ¸…±Í”(€€€•áÁ•Ñ•‘}Å¥‘Ì€ôì(€€€€€€€ÍÑÈ¡¥Ñ•´¹•Ğ ‰Å¥ˆ¤½È€ˆˆ¤¹ÍÑÉ¥À ¤(€€€€€€€™½È¥Ñ•´¥¸¥¹Ù•¹Ñ½Éä¹•Ğ ‰¥Ñ•µÌˆ¤½Èmt(€€€€€€€¥˜¥Í¥¹ÍÑ…¹”¡¥Ñ•´°‘¥Ğ¤…¹ÍÑÈ¡¥Ñ•´¹•Ğ ‰Å¥ˆ¤½È€ˆˆ¤¹ÍÑÉ¥À ¤(€€€ô(€€€‘•±…É•€ô…¹ä (€€€€€€€¥Í¥¹ÍÑ…¹”¡½¹Ñ…¥¹•È°‘¥Ğ¤(€€€€€€€…¹}QeA}M}E%}A159Q}1I}-d¥¸½¹Ñ…¥¹•È(€€€€€€€™½È½¹Ñ…¥¹•È¥¸€¡¥¹Ù•¹Ñ½Éä°µ¥¹•‘}ÑåÁ•Ì¤(€€€€¤(€€€¥˜¹½Ğ•áÁ•Ñ•‘}Å¥‘Ìè(€€€€€€€€Œµ…±™½Éµ•½ÍÑ…±”‘•±…É…Ñ¥½¸µÕÍĞ¹½Ğ‰•½µ”Ù…±¥µ•É•±ä‰•…ÕÍ”(€€€€€€€€ŒÑ¡”ÕÉÉ•¹ĞÍ½ÕÉ”¥¹Ù•¹Ñ½Éä¥Ì•µÁÑä¸€‰Í•¹”¥ÌÑ¡”Á½ÉÑ…‰±”(€€€€€€€€ŒÍÑ…¹‘…±½¹”É•ÁÉ•Í•¹Ñ…Ñ¥½¸™½ÈÑ¡¥Ì…Í”¸(€€€€€€€É•ÑÕÉ¸¹½Ğ‘•±…É•(€€€¥˜¹½Ğ…±° (€€€€€€€}QeA}M}E%}A159Q}1I}-d¥¸½¹Ñ…¥¹•È(€€€€€€€™½È½¹Ñ…¥¹•È¥¸€¡¥¹Ù•¹Ñ½Éä°µ¥¹•‘}ÑåÁ•Ì¤(€€€€¤è(€€€€€€€É•ÑÕÉ¸…±Í”(€€€ÑÉäè(€€€€€€€±•‘•È€ô}É•Í½±Ù•‘}ÑåÁ•}…Í•}Å¥‘}Á±…•µ•¹Ñ}±•‘•È (€€€€€€€€€€€¥¹Ù•¹Ñ½Éä°µ¥¹•‘}ÑåÁ•Ì(€€€€€€€€¤(€€€€€€€Õ¹¥ÑÍ}‰å}Å¥€ô}µ¥¹•‘}…ÍÍ¥¹µ•¹Ñ}Õ¹¥ÑÍ}‰å}Å¥¡µ¥¹•‘}ÑåÁ•Ì¤(€€€•á•ÁĞIÕ¹Ñ¥µ•ÉÉ½Èè(€€€€€€€É•ÑÕÉ¸…±Í”(€€€¥˜±•‘•È¥Ì9½¹”½ÈÍ•Ğ¡±•‘•È¹•Ğ ‰Á±…•µ•¹ÑÌˆ¤½Èíô¤€„ô•áÁ•Ñ•‘}Å¥‘Ìè(€€€€€€€É•ÑÕÉ¸…±Í”(€€€¥˜¹½ĞÍ•Ğ¡Õ¹¥ÑÍ}‰å}Å¥¤¹¥ÍÍÕ‰Í•Ğ¡•áÁ•Ñ•‘}Å¥‘Ì¤è(€€€€€€€É•ÑÕÉ¸…±Í”(€€€¥Ñ•µÍ}‰å}Å¥€ôì(€€€€€€€ÍÑÈ¡¥Ñ•´¹•Ğ ‰Å¥ˆ¤½È€ˆˆ¤¹ÍÑÉ¥À ¤è¥Ñ•´(€€€€€€€™½È¥Ñ•´¥¸¥¹Ù•¹Ñ½Éä¹•Ğ ‰¥Ñ•µÌˆ¤½Èmt(€€€€€€€¥˜¥Í¥¹ÍÑ…¹”¡¥Ñ•´°‘¥Ğ¤…¹ÍÑÈ¡¥Ñ•´¹•Ğ ‰Å¥ˆ¤½È€ˆˆ¤¹ÍÑÉ¥À ¤(€€€ô(€€€™½ÈÅ¥¥¸•áÁ•Ñ•‘}Å¥‘Ìè(€€€€€€€Á±…•µ•¹Ğ€ô}•ÉÑ¥™¥•‘}ÑåÁ•}…Í•}Á±…•µ•¹Ñ}½¹ÑÉ…Ğ (€€€€€€€€€€€€¡±•‘•È¹•Ğ ‰Á±…•µ•¹ÑÌˆ¤½Èíô¤¹•Ğ¡Å¥¤(€€€€€€€€¤(€€€€€€€¥Ñ•µ}½¹ÑÉ…Ğ€ô}•ÉÑ¥™¥•‘}ÑåÁ•}…Í•}Á±…•µ•¹Ñ}½¹ÑÉ…Ğ (€€€€€€€€€€€¥Ñ•µÍ}‰å}Å¥‘mÅ¥‘t¹•Ğ ‰}ÑåÁ•}…Í•}Á±…•µ•¹Ñ}½¹ÑÉ…Ğˆ¤(€€€€€€€€¤(€€€€€€€Õ¹¥Ğ€ôÕ¹¥ÑÍ}‰å}Å¥¹•Ğ¡Å¥¤(€€€€€€€Õ¹¥Ñ}½¹ÑÉ…Ğ€ô€ (€€€€€€€€€€€}ÑåÁ•}…Í•}½¹ÑÉ…Ñ}™½É}Å¥ (€€€€€€€€€€€€€€€µÑåÁ”õÕ¹¥Ğ°(€€€€€€€€€€€€€€€…Í”ô¡Õ¹¥Ğ¹•Ğ ‰…Í•}ÁÉ½µÁÑÌˆ¤½Èmíõt¥lÁt°(€€€€€€€€€€€€€€€€ŒY•É¥™äÑ¡”µ¥¹•…ÍÍ¥¹µ•¹ĞÁÉ½©•Ñ¥½¸¥¹‘•Á•¹‘•¹Ñ±ä¥¹ÍÑ•…(€€€€€€€€€€€€€€€€Œ½˜…±±½İ¥¹œÑ¡”¥¹Ù•¹Ñ½Éä½ÁäÑ¼µ…Í¬„ÍÑ…±”½µ¥ÍÍ¥¹œÉ½ÕÑ”¸(€€€€€€€€€€€€€€€¥¹Ù•¹Ñ½Éå}¥Ñ•´õíô°(€€€€€€€€€€€€€€€Å¥õÅ¥°(€€€€€€€€€€€€¤(€€€€€€€€€€€¥˜Õ¹¥Ğ¥Ì¹½Ğ9½¹”(€€€€€€€€€€€•±Í”9½¹”(€€€€€€€€¤(€€€€€€€ÁÕÉ•}¡Õ‰}¥Ñ•´€ô€ (€€€€€€€€€€€ÍÑÈ¡¥Ñ•µÍ}‰å}Å¥‘mÅ¥‘t¹•Ğ ‰Í½ÕÉ•}­¥¹ˆ¤½È€ˆˆ¤(€€€€€€€€€€€€¹ÍÑÉ¥À ¤(€€€€€€€€€€€€¹…Í•™½± ¤(€€€€€€€€€€€¥¸}!U	}%9Y9Q=Ie}-%9L(€€€€€€€€¤(€€€€€€€¥˜€ (€€€€€€€€€€€Á±…•µ•¹Ğ¥Ì9½¹”(€€€€€€€€€€€½È¥Ñ•µ}½¹ÑÉ…Ğ€„ôÁ±…•µ•¹Ğ(€€€€€€€€€€€½È€ (€€€€€€€€€€€€€€€Õ¹¥Ñ}½¹ÑÉ…Ğ€„ôÁ±…•µ•¹Ğ(€€€€€€€€€€€€€€€…¹€¡Õ¹¥Ğ¥Ì¹½Ğ9½¹”½È¹½ĞÁÕÉ•}¡Õ‰}¥Ñ•´¤(€€€€€€€€€€€€¤(€€€€€€€€¤è(€€€€€€€€€€€É•ÑÕÉ¸…±Í”(€€€É•ÑÕÉ¸QÉÕ”(()‘•˜}½µÁ…Ñ¥‰±•}½¹•ÁÑ}¡•­Á½¥¹Ñ}•¹ÑÉä (€€€¡•­Á½¥¹Ğè‘¥Ğğ9½¹”°(€€€€¨°(€€€É•ÅÕ¥É•}™¥¹…±}É½Õ¹‘¥¹œè‰½½°€ô…±Í”°(€€€…±±½İ}±•…å}ÁÉ•}É•±•…Í”è‰½½°€ô…±Í”°(¤€´ø‰½½°è(€€€€ˆˆ‰]¡•Ñ¡•ÈÑ¡¥Ì‘•Á±½åµ•¹Ğ…¸Í…™•±ä½¹ÍÕµ”½¹”Í•É¥…±¥é•ÍÑ…”¸ˆˆˆ(€€€¥˜¹½Ğ¥Í¥¹ÍÑ…¹”¡¡•­Á½¥¹Ğ°‘¥Ğ¤è(€€€€€€€É•ÑÕÉ¸…±Í”(€€€Í¡•µ„€ô¡•­Á½¥¹Ğ¹•Ğ ‰Í¡•µ…}Ù•ÉÍ¥½¸ˆ¤(€€€ÍÑ…”€ô¡•­Á½¥¹Ğ¹•Ğ ‰ÍÑ…”ˆ¤(€€€¥˜Í¡•µ„€ôô}1e}=9AQ}!-A=%9Q}M!5è(€€€€€€€€ŒM¡•µ„€ÈÁÉ•‘…Ñ•Ì¥¹‘•Á•¹‘•¹Ğ±•…˜…Í•Ì…¹…Í”µ½İ¹•Á±…•µ•¹Ğ¸(€€€€€€€€Œ%Ğ¡…Ì¹¼ÍÑ…”½¹ÑÉ…ĞÙ•ÉÍ¥½¸…Á…‰±”½˜ÁÉ½Ù¥¹œÑ¡…Ğ„Í…Ù•(€€€€€€€€ŒQåÁ”‘¥¹½Ğ½±±…ÁÍ”…±°½˜¥ÑÌ…Í•Ì½¹Ñ¼½¹”½¹•ÁĞ¸€I•Á±…å¥¹œ(€€€€€€€€Œ¥Ğİ½Õ±Í¥±•¹Ñ±äÉ•ÍÑ½É”Ñ¡”½‰Í½±•Ñ”½¹”µ¡½ÍĞÑ…á½¹½µä¸(€€€€€€€É•ÑÕÉ¸…±Í”(€€€ÍÁ•Œ€ô}=9AQ}!-A=%9Q}MQL¹•Ğ¡ÍÑ…”¤(€€€¥˜Í¡•µ„€„ô}=9AQ}!-A=%9Q}M!5½ÈÍÁ•Œ¥Ì9½¹”è(€€€€€€€É•ÑÕÉ¸…±Í”(€€€ÍÑ…•}Ù•ÉÍ¥½¸€ô¡•­Á½¥¹Ğ¹•Ğ ‰ÍÑ…•}Í¡•µ…}Ù•ÉÍ¥½¸ˆ°€Ä¤(€€€¥˜ÍÑ…•}Ù•ÉÍ¥½¸€„ôÍÁ•l‰Ù•ÉÍ¥½¸‰tè(€€€€€€€¥˜¹½Ğ€ (€€€€€€€€€€€…±±½İ}±•…å}ÁÉ•}É•±•…Í”(€€€€€€€€€€€…¹ÍÑ…•}Ù•ÉÍ¥½¸€ôô}1e}AI}I1M}MQ}YIM%=9L¹•Ğ¡ÍÑ…”¤(€€€€€€€€¤è(€€€€€€€€€€€É•ÑÕÉ¸…±Í”(€€€¥˜€ (€€€€€€€ÍÑ…”¥¸}1e}AI}I1M}MQ}YIM%=9L(€€€€€€€…¹ÍÑ…•}Ù•ÉÍ¥½¸€ôôÍÁ•l‰Ù•ÉÍ¥½¸‰t(€€€€€€€…¹¹½ĞÙ…±¥‘}Á¡…Í”Í}ÁÉ•}É•±•…Í•}‰Õ¹‘±” (€€€€€€€€€€€¡•­Á½¥¹Ğ¹•Ğ¡A!MÍ}AI}I1M}%1¤(€€€€€€€€¤(€€€€¤è(€€€€€€€€ŒQ¡”‰ÕµÁ•Ù•ÉÍ¥½¹Ìµ•…¸•á…Ñ±ä½¹”Ñ¡¥¹œèÑ¡¥Ì¡•­Á½¥¹Ğİ…Ì(€€€€€€€€ŒİÉ¥ÑÑ•¸…™Ñ•ÈÑ¡”½µÁ±•Ñ”AÉ”…ÕÑ¡½É¥Ñä‰•…µ”Á…ÉĞ½˜Ñ¡”ÍÑ…”(€€€€€€€€Œ½¹ÑÉ…Ğ¸•ÁÑ¥¹œ„ÕÉÉ•¹ĞµÙ•ÉÍ¥½¸•¹ÑÉäİ¥Ñ¡½ÕĞ¥Ğİ½Õ±±•Ğ„(€€€€€€€€Œµ…±™½Éµ•Ñ•Éµ¥¹…°Í¡½ÉÑÕĞÉ•ÁÉ½‘Õ”Ñ¡”½É¥¥¹…°é•É¼µ½ÕÑÁÕĞ‰Õœ¸(€€€€€€€€Œ=±ØØ½ØÜ•¹ÑÉ¥•ÌÉ•µ…¥¸É•…‘…‰±”½¹±äÑ¡É½Õ Ñ¡”•áÁ±¥¥Ğ±•…ä(€€€€€€€€Œµ¥É…Ñ¥½¸™±…œ…‰½Ù”°İ¡•É”Í¥‘•…ÈÉ•½Ù•Éä¥Ìµ…¹‘…Ñ½Éä¸(€€€€€€€É•ÑÕÉ¸…±Í”(€€€¥˜ÍÑ…”€ôô€‰Í­•±•Ñ½¹}¡Õ¹­Ìˆè(€€€€€€€É•ÑÕÉ¸}Ù…±¥‘}½µÁ±•Ñ•‘}Í­•±•Ñ½¹}¡Õ¹­Ì (€€€€€€€€€€€¡•­Á½¥¹Ğ¹•Ğ ‰½µÁ±•Ñ•‘}¡Õ¹­Ìˆ¤¤(€€€¥˜ÍÑ…”€ôô€‰Í½ÕÉ•}É…Á¡}É•Ù¥•Üˆè(€€€€€€€É…Á €ô¡•­Á½¥¹Ğ¹•Ğ ‰Í½ÕÉ•}É•Ù¥•İ}É…Á ˆ¤(€€€€€€€½¹Ñ•áÑ}¡…Í €ôÍÑÈ (€€€€€€€€€€€¡•­Á½¥¹Ğ¹•Ğ ‰Í½ÕÉ•}É•Ù¥•İ}½¹Ñ•áÑ}¡…Í ˆ¤½È€ˆˆ¤(€€€€€€€É•ÑÕÉ¸‰½½° (€€€€€€€€€€€¥Í¥¹ÍÑ…¹”¡É…Á °‘¥Ğ¤(€€€€€€€€€€€…¹É…Á ¹•Ğ ‰Í½ÕÉ•}½¹ÑÉ…Ñ}¡…Í ˆ¤(€€€€€€€€€€€…¹É…Á ¹•Ğ ‰Í•µ…¹Ñ¥}½¹Ñ•áÑ}¡…Í ˆ¤(€€€€€€€€€€€…¹É”¹™Õ±±µ…Ñ ¡È‰lÀ´å„µ™uìØÑôˆ°½¹Ñ•áÑ}¡…Í ¤(€€€€€€€€€€€…¹}¡•­Á½¥¹Ñ}¡…Í}™¥•±‘Ì¡¡•­Á½¥¹Ğ°€ ‰É•½É‘Ìˆ°±¥ÍĞ¤¤(€€€€€€€€¤(€€€¥˜¹½Ğ}¡•­Á½¥¹Ñ}¡…Í}™¥•±‘Ì¡¡•­Á½¥¹Ğ°€ ‰É•½É‘Ìˆ°±¥ÍĞ¤¤è(€€€€€€€É•ÑÕÉ¸…±Í”(€€€¥˜ÍÑ…”€ôô€‰Í½ÕÉ•}Ñ½Á¥}É•Ù¥•Üˆè(€€€€€€€É•½Ù•Éä€ô¡•­Á½¥¹Ğ¹•Ğ ‰Í½ÕÉ•}Ñ½Á¥}É•½Ù•Éäˆ¤(€€€€€€€¥˜¹½Ğ}¡•­Á½¥¹Ñ}¡…Í}™¥•±‘Ì (€€€€€€€€€€€¡•­Á½¥¹Ğ°(€€€€€€€€€€€€ ‰Í½ÕÉ•}Ñ½Á¥}É•½Ù•Éäˆ°‘¥Ğ¤°(€€€€€€€€€€€€ ‰Í­•±•Ñ½¹}µ•Ñ¡½‘}É½İ}Í¹…ÁÍ¡½Ğˆ°±¥ÍĞ¤°(€€€€€€€€¤è(€€€€€€€€€€€É•ÑÕÉ¸…±Í”(€€€€€€€±…ÍÑ}…ÑÑ•µÁĞ€ôÉ•½Ù•Éä¹•Ğ ‰±…ÍÑ}…ÑÑ•µÁĞˆ¤(€€€€€€€¥˜±…ÍÑ}…ÑÑ•µÁĞ¥Ì¹½Ğ9½¹”…¹¹½Ğ€ (€€€€€€€€€€€¥Í¥¹ÍÑ…¹”¡±…ÍÑ}…ÑÑ•µÁĞ°‘¥Ğ¤(€€€€€€€€€€€…¹ÍÑÈ¡±…ÍÑ}…ÑÑ•µÁĞ¹•Ğ ‰ÍÑ…ÑÕÌˆ¤½È€ˆˆ¤¥¸ì(€€€€€€€€€€€€€€€€‰É•ÅÕ•ÍÑ}ÍÑ…ÉÑ•ˆ°€‰ÍÕ••‘•ˆ°€‰¥¹½µÁ±•Ñ”ˆ°€‰™…¥±•ˆ°(€€€€€€€€€€€ô(€€€€€€€€€€€…¹‰½½°¡ÍÑÈ¡±…ÍÑ}…ÑÑ•µÁĞ¹•Ğ ‰‘•¥Í¥½¹}¥ˆ¤½È€ˆˆ¤¤(€€€€€€€€€€€…¹‰½½°¡É”¹™Õ±±µ…Ñ  (€€€€€€€€€€€€€€€È‰lÀ´å„µ™uìØÑôˆ°(€€€€€€€€€€€€€€€ÍÑÈ¡±…ÍÑ}…ÑÑ•µÁĞ¹•Ğ ‰½¹Ñ•áÑ}¡…Í ˆ¤½È€ˆˆ¤°(€€€€€€€€€€€€¤¤(€€€€€€€€¤è(€€€€€€€€€€€É•ÑÕÉ¸…±Í”(€€€€€€€™½±±½İ}ÕÀ€ôÉ•½Ù•Éä¹•Ğ ‰Á•¹‘¥¹}™½±±½İÕÀˆ¤(€€€€€€€¥˜™½±±½İ}ÕÀ¥Ì¹½Ğ9½¹”…¹¹½Ğ€ (€€€€€€€€€€€¥Í¥¹ÍÑ…¹”¡™½±±½İ}ÕÀ°‘¥Ğ¤(€€€€€€€€€€€…¹‰½½°¡ÍÑÈ¡™½±±½İ}ÕÀ¹•Ğ ‰ÁÉ¥½É}‘•¥Í¥½¹}¥ˆ¤½È€ˆˆ¤¤(€€€€€€€€€€€…¹‰½½°¡ÍÑÈ¡™½±±½İ}ÕÀ¹•Ğ ‰™…¥±ÕÉ”ˆ¤½È€ˆˆ¤¤(€€€€€€€€¤è(€€€€€€€€€€€É•ÑÕÉ¸…±Í”(€€€€€€€É•ÑÕÉ¸QÉÕ”(€€€¥˜ÍÑ…”€ôô€‰…¹½¹¥…±}Í­•±•Ñ½¸ˆè(€€€€€€€É•ÑÕÉ¸}¡•­Á½¥¹Ñ}¡…Í}™¥•±‘Ì (€€€€€€€€€€€¡•­Á½¥¹Ğ°€ ‰Í­•±•Ñ½¹}µ•Ñ¡½‘}É½İ}Í¹…ÁÍ¡½Ğˆ°±¥ÍĞ¤¤(€€€¥˜ÍÑ…”¥¸ì(€€€€€€€€‰‘•ÍÉ¥ÁÑ¥½¹}µ•Ñ¡½‘}Í¹…ÁÍ¡½Ğˆ°(€€€€€€€€‰ÅÕ•ÍÑ¥½¹}¥¹Ù•¹Ñ½Éäˆ°(€€€€€€€}QeA}Qa=9=5e}!-A=%9Q}MQ°(€€€€€€€}=9AQ}!-A=%9Q}MQ°(€€€€€€€€‰Á½ÍÑ}ÑåÁ•}…ÍÍ¥¹µ•¹Ğˆ°(€€€€€€€€‰™¥¹…±}½¹Ñ•¹Ñ}É•…‘äˆ°(€€€ô…¹¹½Ğ}¡•­Á½¥¹Ñ}¡…Í}™¥•±‘Ì (€€€€€€€¡•­Á½¥¹Ğ°€ ‰µ•Ñ¡½‘}É½İ}Í¹…ÁÍ¡½Ğˆ°±¥ÍĞ¤(€€€€¤è(€€€€€€€É•ÑÕÉ¸…±Í”(€€€¥˜ÍÑ…”¥¸ì(€€€€€€€€‰ÅÕ•ÍÑ¥½¹}¥¹Ù•¹Ñ½Éäˆ°(€€€€€€€}QeA}Qa=9=5e}!-A=%9Q}MQ°(€€€€€€€}=9AQ}!-A=%9Q}MQ°(€€€€€€€€‰Á½ÍÑ}ÑåÁ•}…ÍÍ¥¹µ•¹Ğˆ°(€€€€€€€€‰™¥¹…±}½¹Ñ•¹Ñ}É•…‘äˆ°(€€€ô…¹¹½Ğ}¡•­Á½¥¹Ñ}¡…Í}™¥•±‘Ì (€€€€€€€¡•­Á½¥¹Ğ°€ ‰ÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½Éäˆ°‘¥Ğ¤(€€€€¤è(€€€€€€€É•ÑÕÉ¸…±Í”(€€€¥˜ÍÑ…”¥¸ì(€€€€€€€€‰ÅÕ•ÍÑ¥½¹}¥¹Ù•¹Ñ½Éäˆ°(€€€€€€€}QeA}Qa=9=5e}!-A=%9Q}MQ°(€€€€€€€}=9AQ}!-A=%9Q}MQ°(€€€€€€€€‰Á½ÍÑ}ÑåÁ•}…ÍÍ¥¹µ•¹Ğˆ°(€€€€€€€€‰™¥¹…±}½¹Ñ•¹Ñ}É•…‘äˆ°(€€€ô…¹}¥¹Ù…±¥‘}¥¹Ù•¹Ñ½Éå}¥Ñ•µÌ (€€€€€€€¡•­Á½¥¹Ğ¹•Ğ ‰ÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½Éäˆ¤(€€€€¤è(€€€€€€€€ŒM¡…Á”½µÁ…Ñ¥‰¥±¥Ñä¥Ì¹½Ğ•¹½Õ ™½È„É•ÍÕµ…‰±”¥¹Ù•¹Ñ½Éä¸µÁÑä(€€€€€€€€Œ½È‘ÕÁ±¥…Ñ”µÅ¥É½İÌ…¸¹•Ù•ÈÍ…Ñ¥Í™ä•á…Ğ½Ù•É…”…¹İ½Õ±(€€€€€€€€Œ½Ñ¡•Éİ¥Í”™…¥°…Ğ€äà”½¸•Ù•ÉäÉ•ÑÉä¸(€€€€€€€É•ÑÕÉ¸…±Í”(€€€¥˜ÍÑ…”¥¸ì(€€€€€€€}QeA}Qa=9=5e}!-A=%9Q}MQ°(€€€€€€€}=9AQ}!-A=%9Q}MQ°(€€€€€€€€‰Á½ÍÑ}ÑåÁ•}…ÍÍ¥¹µ•¹Ğˆ°(€€€€€€€€‰™¥¹…±}½¹Ñ•¹Ñ}É•…‘äˆ°(€€€ô…¹¹½Ğ}¡•­Á½¥¹Ñ}¡…Í}™¥•±‘Ì¡¡•­Á½¥¹Ğ°€ ‰µ¥¹•‘}ÑåÁ•Ìˆ°‘¥Ğ¤¤è(€€€€€€€É•ÑÕÉ¸…±Í”(€€€¥˜ÍÑ…”¥¸ì(€€€€€€€}=9AQ}!-A=%9Q}MQ°(€€€€€€€€‰Á½ÍÑ}ÑåÁ•}…ÍÍ¥¹µ•¹Ğˆ°(€€€€€€€€‰™¥¹…±}½¹Ñ•¹Ñ}É•…‘äˆ°(€€€ô…¹¹½Ğ}ÑåÁ•}É…¹Õ±…É¥Ñå}É•Á±…å}Í•…±}Ù…±¥¡¡•­Á½¥¹Ğ¤è(€€€€€€€É•ÑÕÉ¸…±Í”(€€€¥˜€ (€€€€€€€ÍÑ…”¥¸ì‰Á½ÍÑ}ÑåÁ•}…ÍÍ¥¹µ•¹Ğˆ°€‰™¥¹…±}½¹Ñ•¹Ñ}É•…‘ä‰ô(€€€€€€€…¹¹½Ğ}Á±…•µ•¹Ñ}•ÉÑ¥™¥…Ñ¥½¹}½¹ÑÉ…Ñ}½µÁ±•Ñ” (€€€€€€€€€€€¡•­Á½¥¹Ğ¹•Ğ ‰µ¥¹•‘}ÑåÁ•Ìˆ¤°(€€€€€€€€€€€¡•­Á½¥¹Ğ¹•Ğ ‰ÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½Éäˆ¤°(€€€€€€€€¤(€€€€¤è(€€€€€€€É•ÑÕÉ¸…±Í”(€€€É•ÅÕ¥É•}ÑåÁ•}…Í•}½İ¹•É}±•‘•È€ôÍÑ…”¥¸ì(€€€€€€€€‰Á½ÍÑ}ÑåÁ•}…ÍÍ¥¹µ•¹Ğˆ°€‰™¥¹…±}½¹Ñ•¹Ñ}É•…‘äˆ°(€€€ô(€€€¥˜€ (€€€€€€€ÍÑ…”€ôô€‰™¥¹…±}½¹Ñ•¹Ñ}É•…‘äˆ(€€€€€€€…¹¡•­Á½¥¹Ğ¹•Ğ ‰É½Õ¹‘¥¹}•ÉÑ¥™¥…Ñ•}É•ÅÕ¥É•ˆ¤¥Ì…±Í”(€€€€€€€…¹¹½ĞÉ•ÅÕ¥É•}™¥¹…±}É½Õ¹‘¥¹œ(€€€€¤è(€€€€€€€™É½´€¸¥µÁ½ÉĞ…¹½¹¥…±}Í½ÕÉ•}Á¡…Í”Ì…ÌÁ¡…Í”Ì((€€€€€€€€ŒAÉ•Í•ÉÙ”ÍÑ…¹‘…±½¹”½½™™±¥¹”Ñ•Éµ¥¹…°Í¹…ÁÍ¡½ÑÌ¸Q¡•ä…É”¹•Ù•È„(€€€€€€€€ŒÁÉ½‘ÕÑ¥½¸ÁÕ‰±¥…Ñ¥½¸…ÕÑ¡½É¥Ñä…¹…É”…±É•…‘äÉ•©•Ñ•‰•±½Ü…Ì(€€€€€€€€ŒÍ½½¸…Ì„±¥Ù”A¡…Í”€ÌÉ…Á ½ÈÍÑÉ¥Ğ…±±•È¥ÌÁÉ•Í•¹Ğ¸(€€€€€€€É•ÅÕ¥É•}ÑåÁ•}…Í•}½İ¹•É}±•‘•È€ô¥Í¥¹ÍÑ…¹” (€€€€€€€€€€€Á¡…Í”Ì¹…Ñ¥Ù•}É…Á  ¤°‘¥Ğ(€€€€€€€€¤(€€€¥˜€ (€€€€€€€É•ÅÕ¥É•}ÑåÁ•}…Í•}½İ¹•É}±•‘•È(€€€€€€€…¹¹½Ğ}ÑåÁ•}…Í•}¡•­Á½¥¹Ñ}Á±…•µ•¹Ñ}±•‘•É}Ù…±¥¡¡•­Á½¥¹Ğ¤(€€€€¤è(€€€€€€€É•ÑÕÉ¸…±Í”(€€€¥˜ÍÑ…”€ôô€‰™¥¹…±}½¹Ñ•¹Ñ}É•…‘äˆè(€€€€€€€€Œ½¹•ÁÑÍ}™É½µ}µµ‘€¥Ì…±Í¼•á•É¥Í•…Ì„ÍÑ…¹‘…±½¹”Í•µ…¹Ñ¥Œ(€€€€€€€€ŒÑÉ…¹Í™½Éµ…Ñ¥½¸€¡¥¹±Õ‘¥¹œ‰äÕ¹¥ĞÑ•ÍÑÌ¤İ¥Ñ¡½ÕĞ…¸…Ñ¥Ù”A¡…Í”€Ì(€€€€€€€€ŒÍ½ÕÉ”É…Á ¸€MÕ „É•ÍÕ±Ğµ…ä‰”É•ÍÕµ•‰äÑ¡…ĞÍÑ…¹‘…±½¹”(€€€€€€€€Œ…±±•È°‰ÕĞ	Õ¥±½¹•ÁÑÌœ±¥Ù”ÁÕ‰±¥…Ñ¥½¸‰½Õ¹‘…ÉäÍÑ¥±°É•©•ÑÌ(€€€€€€€€Œ¥Ğ‰•…ÕÍ”¥Ğ¡…Ì¹¼•ÉÑ¥™¥…Ñ”¸€5¥ÍÍ¥¹œ±•…äµ…É­•ÉÌÉ•µ…¥¸(€€€€€€€€Œ¥¹½µÁ…Ñ¥‰±”Í¼…¸½±ÁÉ½‘ÕÑ¥½¸¡•­Á½¥¹Ğ…¹¹½Ğ‰åÁ…ÍÌÑ¡”¹•Ü(€€€€€€€€Œ‘•Á½Í¥Ğ½¹ÑÉ…Ğ¸(€€€€€€€¥˜¡•­Á½¥¹Ğ¹•Ğ ‰É½Õ¹‘¥¹}•ÉÑ¥™¥…Ñ•}É•ÅÕ¥É•ˆ¤¥Ì…±Í”è(€€€€€€€€€€€¥˜É•ÅÕ¥É•}™¥¹…±}É½Õ¹‘¥¹œè(€€€€€€€€€€€€€€€É•ÑÕÉ¸…±Í”(€€€€€€€€€€€€ŒÍÑ…¹‘…±½¹”ÑÉ…¹Í™½Éµ…Ñ¥½¸µ…äÉ•ÍÕµ”¥ÑÌ½İ¸Õ¹•ÉÑ¥™¥•(€€€€€€€€€€€€ŒÑ•Éµ¥¹…°Í¹…ÁÍ¡½Ğ¸€=¹”Ñ¡”A¡…Í”€ÌÍ½ÕÉ”É…Á ¥Ì…Ñ¥Ù”°(€€€€€€€€€€€€Œ¡½İ•Ù•È°Ñ¡…ĞÍ…µ”Í¹…ÁÍ¡½Ğ¥Ì¹½Ğ„ÁÕ‰±¥Í¡…‰±”¡•­Á½¥¹Ğ…¹(€€€€€€€€€€€€ŒµÕÍĞ™…±°‰…¬Ñ¼„É½Õ¹‘•ÍÑ…”¸(€€€€€€€€€€€™É½´€¸¥µÁ½ÉĞ…¹½¹¥…±}Í½ÕÉ•}Á¡…Í”Ì…ÌÁ¡…Í”Ì((€€€€€€€€€€€É•ÑÕÉ¸¹½Ğ¥Í¥¹ÍÑ…¹”¡Á¡…Í”Ì¹…Ñ¥Ù•}É…Á  ¤°‘¥Ğ¤(€€€€€€€ÑÉäè(€€€€€€€€€€€™É½´€¸¥µÁ½ÉĞ…¹½¹¥…±}Í½ÕÉ•}Á¡…Í”Ì…ÌÁ¡…Í”Ì((€€€€€€€€€€€…Ñ¥Ù•}Í•µ…¹Ñ¥}É…Á €ôÁ¡…Í”Ì¹…Ñ¥Ù•}É…Á  ¤(€€€€€€€€€€€ÑåÁ•}…Í•}Å¥‘}Á±…•µ•¹Ñ}±•‘•È€ô€ (€€€€€€€€€€€€€€€}É•Í½±Ù•‘}ÑåÁ•}…Í•}Å¥‘}Á±…•µ•¹Ñ}±•‘•È (€€€€€€€€€€€€€€€€€€€¡•­Á½¥¹Ğ¹•Ğ ‰ÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½Éäˆ¤°(€€€€€€€€€€€€€€€€€€€¡•­Á½¥¹Ğ¹•Ğ ‰µ¥¹•‘}ÑåÁ•Ìˆ¤°(€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€¤(€€€€€€€€€€€É½Õ¹‘¥¹}•ÉÑ¥™¥…Ñ”¹Ù•É¥™å}™¥¹…±}•ÉÑ¥™¥…Ñ” (€€€€€€€€€€€€€€€¡•­Á½¥¹Ğ¹•Ğ ‰É•½É‘Ìˆ¤½Èmt°(€€€€€€€€€€€€€€€¡•­Á½¥¹Ğ¹•Ğ (€€€€€€€€€€€€€€€€€€€É½Õ¹‘¥¹}•ÉÑ¥™¥…Ñ”¹%91}IQ%%Q}%1(€€€€€€€€€€€€€€€€¤°(€€€€€€€€€€€€€€€Í•µ…¹Ñ¥}É…Á ô (€€€€€€€€€€€€€€€€€€€…Ñ¥Ù•}Í•µ…¹Ñ¥}É…Á (€€€€€€€€€€€€€€€€€€€¥˜¥Í¥¹ÍÑ…¹”¡…Ñ¥Ù•}Í•µ…¹Ñ¥}É…Á °‘¥Ğ¤(€€€€€€€€€€€€€€€€€€€•±Í”9½¹”(€€€€€€€€€€€€€€€€¤°(€€€€€€€€€€€€€€€É•ÅÕ¥É•}Á±…•µ•¹Ñ}½¹ÑÉ…ÑÌõÉ•ÅÕ¥É•}™¥¹…±}É½Õ¹‘¥¹œ°(€€€€€€€€€€€€€€€ÑåÁ•}…Í•}Å¥‘}Á±…•µ•¹Ñ}±•‘•Èô (€€€€€€€€€€€€€€€€€€€ÑåÁ•}…Í•}Å¥‘}Á±…•µ•¹Ñ}±•‘•È(€€€€€€€€€€€€€€€€¤°(€€€€€€€€€€€€¤(€€€€€€€•á•ÁĞ€ (€€€€€€€€€€€É½Õ¹‘¥¹}•ÉÑ¥™¥…Ñ”¹É½Õ¹‘¥¹•ÉÑ¥™¥…Ñ•ÉÉ½È°(€€€€€€€€€€€IÕ¹Ñ¥µ•ÉÉ½È°(€€€€€€€€¤è(€€€€€€€€€€€É•ÑÕÉ¸…±Í”(€€€É•ÑÕÉ¸QÉÕ”(()‘•˜}¹•İ•ÍÑ}½µÁ…Ñ¥‰±•}½¹•ÁÑ}¡•­Á½¥¹Ğ (€€€¡•­Á½¥¹Ğè‘¥Ğğ9½¹”°(€€€€¨°…±±½İ•‘}ÍÑ…•ÌèÍ•ÑmÍÑÉtğ9½¹”€ô9½¹”°(€€€É•ÅÕ¥É•}™¥¹…±}É½Õ¹‘¥¹œè‰½½°€ô…±Í”°(€€€…±±½İ}±•…å}ÁÉ•}É•±•…Í”è‰½½°€ô…±Í”°(¤€´ø‘¥Ğğ9½¹”è(€€€€ˆˆ‰M•±•ĞÑ¡”™ÕÉÑ¡•ÍĞ½µÁ…Ñ¥‰±”½µÁ±•Ñ•ÍÑ…”°¥¹½É¥¹œ¹•İ•ÈÕ¹­¹½İ¹Ì¸ˆˆˆ(€€€…¹‘¥‘…Ñ•Ìè±¥ÍÑmÑÕÁ±•m¥¹Ğ°¥¹Ğ°‘¥Ñut€ômt(€€€™½È¥¹‘•à°•¹ÑÉä¥¸•¹Õµ•É…Ñ”¡}½¹•ÁÑ}¡•­Á½¥¹Ñ}•¹ÑÉ¥•Ì¡¡•­Á½¥¹Ğ¤¤è(€€€€€€€¥˜¹½Ğ}½µÁ…Ñ¥‰±•}½¹•ÁÑ}¡•­Á½¥¹Ñ}•¹ÑÉä (€€€€€€€€€€€•¹ÑÉä°(€€€€€€€€€€€É•ÅÕ¥É•}™¥¹…±}É½Õ¹‘¥¹œõÉ•ÅÕ¥É•}™¥¹…±}É½Õ¹‘¥¹œ°(€€€€€€€€€€€…±±½İ}±•…å}ÁÉ•}É•±•…Í”õ…±±½İ}±•…å}ÁÉ•}É•±•…Í”°(€€€€€€€€¤è(€€€€€€€€€€€½¹Ñ¥¹Õ”(€€€€€€€ÍÑ…”€ôÍÑÈ¡•¹ÑÉä¹•Ğ ‰ÍÑ…”ˆ¤½È€ˆˆ¤(€€€€€€€¥˜…±±½İ•‘}ÍÑ…•Ì¥Ì¹½Ğ9½¹”…¹ÍÑ…”¹½Ğ¥¸…±±½İ•‘}ÍÑ…•Ìè(€€€€€€€€€€€½¹Ñ¥¹Õ”(€€€€€€€½É‘•È€ô}=9AQ}!-A=%9Q}MQL¹•Ğ (€€€€€€€€€€€ÍÑ…”°ì‰½É‘•Èˆè€ØÀ¥˜ÍÑ…”€ôô}=9AQ}!-A=%9Q}MQ•±Í”€´Åô(€€€€€€€€¥l‰½É‘•È‰t(€€€€€€€…¹‘¥‘…Ñ•Ì¹…ÁÁ•¹ ¡¥¹Ğ¡½É‘•È¤°¥¹‘•à°•¹ÑÉä¤¤(€€€¥˜¹½Ğ…¹‘¥‘…Ñ•Ìè(€€€€€€€É•ÑÕÉ¸9½¹”(€€€É•ÑÕÉ¸½Áä¹‘••Á½Áä¡µ…à¡…¹‘¥‘…Ñ•Ì°­•äõ±…µ‰‘„¥Ñ•´è€¡¥Ñ•µlÁt°¥Ñ•µlÅt¤¥lÉt¤(()‘•˜}İ¥Ñ¡½ÕÑ}½¹•ÁÑ}¡•­Á½¥¹Ñ}ÍÑ…” (€€€¡•­Á½¥¹Ğè‘¥Ğğ9½¹”°ÍÑ…”èÍÑÈ°(¤€´ø‘¥Ğğ9½¹”è(€€€€ˆˆ‰I•ÑÕÉ¸¡•­Á½¥¹Ğ¡¥ÍÑ½Éäİ¥Ñ¡½ÕĞ½¹”ÍÑ…”¸((€€€Q¡¥Ì¥ÌÕÍ•İ¡•¸„±•…ä™¥¹…°¡•­Á½¥¹Ğ™…¥±ÌÑ½‘…äÌÍÑÉ¥Ğ…Ñ”¸Q¡”(€€€ÁÉ••‘¥¹œ‘ÕÉ…‰±”ÍÑ…”É•µ…¥¹ÌÉ•ÕÍ…‰±”°İ¡¥±”Ñ¡”É•©•Ñ•™¥¹…°Á…å±½…(€€€…¹¹½Ğ‰”Í•±•Ñ•……¥¸‘ÕÉ¥¹œÑ¡”Í…µ”É•½Ù•Éä…ÑÑ•µÁĞ¸(€€€€ˆˆˆ(€€€¥˜¹½Ğ¥Í¥¹ÍÑ…¹”¡¡•­Á½¥¹Ğ°‘¥Ğ¤è(€€€€€€€É•ÑÕÉ¸9½¹”(€€€¥˜¡•­Á½¥¹Ğ¹•Ğ ‰¡•­Á½¥¹Ñ}™½Éµ…Ğˆ¤€„ô}=9AQ}!-A=%9Q}=I5Pè(€€€€€€€¥˜ÍÑÈ¡¡•­Á½¥¹Ğ¹•Ğ ‰ÍÑ…”ˆ¤½È€ˆˆ¤€ôôÍÑ…”è(€€€€€€€€€€€É•ÑÕÉ¸9½¹”(€€€€€€€É•ÑÕÉ¸½Áä¹‘••Á½Áä¡¡•­Á½¥¹Ğ¤((€€€™¥±Ñ•É•€ô½Áä¹‘••Á½Áä¡¡•­Á½¥¹Ğ¤(€€€™¥±Ñ•É•‘l‰¡•­Á½¥¹ÑÌ‰t€ôl(€€€€€€€½Áä¹‘••Á½Áä¡•¹ÑÉä¤(€€€€€€€™½È•¹ÑÉä¥¸}½¹•ÁÑ}¡•­Á½¥¹Ñ}•¹ÑÉ¥•Ì¡¡•­Á½¥¹Ğ¤(€€€€€€€¥˜ÍÑÈ¡•¹ÑÉä¹•Ğ ‰ÍÑ…”ˆ¤½È€ˆˆ¤€„ôÍÑ…”(€€€t(€€€¹•İ•ÍĞ€ô}¹•İ•ÍÑ}½µÁ…Ñ¥‰±•}½¹•ÁÑ}¡•­Á½¥¹Ğ¡™¥±Ñ•É•¤(€€€¥˜¹•İ•ÍĞ¥Ì9½¹”è(€€€€€€€É•ÑÕÉ¸9½¹”(€€€™½È™¥•±¥¸€ (€€€€€€€€‰ÍÑ…”ˆ°(€€€€€€€€‰ÍÑ…•}½É‘•Èˆ°(€€€€€€€€‰ÍÑ…•}Í¡•µ…}Ù•ÉÍ¥½¸ˆ°(€€€€€€€€‰ÍÑ…•}±…‰•°ˆ°(€€€€€€€€‰Í…Ù•‘}…Ğˆ°(€€€€€€€€‰ÁÉ½É•ÍÌˆ°(€€€€¤è(€€€€€€€™¥±Ñ•É•‘m™¥•±‘t€ô½Áä¹‘••Á½Áä¡¹•İ•ÍĞ¹•Ğ¡™¥•±¤¤(€€€É•ÑÕÉ¸™¥±Ñ•É•(()‘•˜}Í•µ…¹Ñ¥}¥¹Ù•¹Ñ½Éå}¥Ñ•µÌ¡Ù…±Õ”è‘¥Ğğ9½¹”¤€´ø±¥ÍÑm‘¥Ñtè(€€€€ˆˆ‰½µÁ…É…‰±”¥¹Ù•¹Ñ½Éä½¹Ñ•¹Ğ°•á±Õ‘¥¹œ‘•Ñ•Éµ¥¹¥ÍÑ¥Œµ•Ñ…‘…Ñ„¸((€€€¥ÕÉ”UI1Ì°…ÁÑ¥½¹Ì°…¹É•Í½±ÕÑ¥½¸™±…Ì…É”É•™É•Í¡•±½…±±ä™É½´Ñ¡”(€€€ÕÉÉ•¹ĞÍ½ÕÉ”É•¥ÍÑÉäİ¡•¸„™¥¹…°¡•­Á½¥¹Ğ¥ÌÉ•ÍÑ½É•¸€Q¡•äµÕÍĞ¹½Ğ(€€€™½É”…¸½Ñ¡•Éİ¥Í”Ù…±¥€äà”¡•­Á½¥¹Ğ‰…¬Ñ¡É½Õ Í•µ…¹Ñ¥Œ½A$ÍÑ…•Ì¸(€€€Q½Á¥Œ°Ñ…Í¬µ­¥¹°…Ñ¥Ù¥Ñä°İ½É‘¥¹œ°…¹½¹Ñ•áĞ¡…¹•ÌÉ•µ…¥¸Í•µ…¹Ñ¥Œ…¹(€€€ÍÑ¥±°¥¹Ù…±¥‘…Ñ”Ñ¡”Á•ÉÍ¥ÍÑ•¡½ÍĞÉ•Ù¥•Ü¸(€€€€ˆˆˆ(€€€¹½¹Í•µ…¹Ñ¥}­•åÌ€ôì(€€€€€€€€‰½É‘•É}¥¹‘•àˆ°(€€€€€€€€‰¥µ…•}ÕÉ±Ìˆ°(€€€€€€€€‰}¥µ…•}…ÁÑ¥½¹Ìˆ°(€€€€€€€€‰}™¥ÕÉ•}¥µ…•Í}É•Í½±Ù•ˆ°(€€€€€€€€‰É•ÅÕ¥É•Í}Ù¥ÍÕ…°ˆ°(€€€ô(€€€É•ÑÕÉ¸l(€€€€€€€ì(€€€€€€€€€€€­•äè½Áä¹‘••Á½Áä¡™¥•±¤(€€€€€€€€€€€™½È­•ä°™¥•±¥¸¥Ñ•´¹¥Ñ•µÌ ¤(€€€€€€€€€€€¥˜­•ä¹½Ğ¥¸¹½¹Í•µ…¹Ñ¥}­•åÌ(€€€€€€€ô(€€€€€€€™½È¥Ñ•´¥¸€¡Ù…±Õ”½Èíô¤¹•Ğ ‰¥Ñ•µÌˆ¤½Èmt(€€€€€€€¥˜¥Í¥¹ÍÑ…¹”¡¥Ñ•´°‘¥Ğ¤(€€€t(()‘•˜}™¥¹…±}¡•­Á½¥¹Ñ}É•™É•Í¡}É•…Í½¹Ì (€€€¡•­Á½¥¹Ğè‘¥Ğğ9½¹”°€¨°Í•Ñ¥½¹Ìè±¥ÍÑm‘¥Ñt°(€€€Í½ÕÉ•}Ñ½Á¥}•á•ÉÁÑÌè±¥ÍÑm‘¥Ñt°(¤€´ø±¥ÍÑmÍÑÉtè(€€€€ˆˆ‰I•ÑÕÉ¸ÍÑÉÕÑÕÉ…°•Ù¥‘•¹”Ñ¡…Ğ„Í…Ù•™¥¹…°µ…ÀµÕÍĞ‰”É•‰Õ¥±Ğ¸((€€€½µÁ…Ñ¥‰±”¡•­Á½¥¹Ğ½¹±äÁÉ½Ù•ÌÑ¡…Ğ¥ÑÌ)M=8Í¡…Á”¥ÌÍ…™”Ñ¼É•…¸%Ğ(€€€‘½•Ì¹½ĞÁÉ½Ù”Ñ¡…Ğ¥Ğİ…Ì•¹•É…Ñ•™É½´Ñ¡”ÕÉÉ•¹ĞÍ½ÕÉ”µ½µÁ±•Ñ•¹•ÍÌ(€€€ÉÕ±•Ì¸€%¸Á…ÉÑ¥Õ±…È°½±‘•È™¥¹…°¡•­Á½¥¹ÑÌ…¸½µ¥Ğ…¸•¹Ñ¥É”Í½ÕÉ”(€€€Ñ½Á¥Œ½È„‘•Ñ•Éµ¥¹¥ÍÑ¥ŒÅÕ•ÍÑ¥½¸…¹¡½Èİ¡¥±”ÍÑ¥±°Á…ÍÍ¥¹œÑ¡•¥ÈÍ¡•µ„(€€€¡•¬¸€I•ÍÕµ”™É½´Ñ¡”ÁÉ••‘¥¹œÍÑ…”¥¸Ñ¡…Ğ…Í”Í¼Ñ¡”¹½Éµ…°™¥¹…°(€€€É•½Ù•ÉäÁ…Ñ …¸É•Á…¥ÈÑ¡”µ…À¸(€€€€ˆˆˆ(€€€¥˜¹½Ğ¡•­Á½¥¹Ğè(€€€€€€€É•ÑÕÉ¸mt(€€€É•…Í½¹Ìè±¥ÍÑmÍÑÉt€ômt(€€€¥˜€ (€€€€€€€¡•­Á½¥¹Ğ¹•Ğ ‰Í•µ…¹Ñ¥}½¹™¥‘•¹•}Á½±¥äˆ¤(€€€€€€€€„ô½¹™¥‘•¹•}Á½±¥ä¹…¡•}¥‘•¹Ñ¥Ñä ¤(€€€€¤è(€€€€€€€€Œ™¥¹…°¡•­Á½¥¹ĞÍ­¥ÁÌÑ¡”Í•µ…¹Ñ¥Œ½A$™¥¹…±¥é•È½¸É•ÍÕµ”¸€%Ğ¥Ì(€€€€€€€€ŒÑ¡•É•™½É”É•ÕÍ…‰±”½¹±äÕ¹‘•ÈÑ¡”•á…ĞÁ½±¥äÑ¡…Ğ…ÁÁÉ½Ù•¥Ğ¸(€€€€€€€€Œ…É±¥•È¡•­Á½¥¹ÑÌÉ•µ…¥¸Ù…±Õ…‰±”èÑ¡”…±±•È™…±±Ì‰…¬Ñ¼Ñ¡”(€€€€€€€€Œ¹•İ•ÍĞÁÉ••‘¥¹œÍÑ…”…¹É•ÉÕ¹ÌÍ•µ…¹Ñ¥ŒÉ•Ù¥•Ü…ĞÑ¡”ÕÉÉ•¹Ğ…Ñ”¸(€€€€€€€É•…Í½¹Ì¹…ÁÁ•¹ ‰Í•µ…¹Ñ¥Œ½¹™¥‘•¹”Á½±¥ä¡…¹•ˆ¤(€€€µ¥ÍÍ¥¹}Ñ½Á¥Ì€ô}µ¥ÍÍ¥¹}Í½ÕÉ•}Ñ½Á¥}•á•ÉÁÑÌ (€€€€€€€¡•­Á½¥¹Ğ¹•Ğ ‰É•½É‘Ìˆ¤½Èmt°Í½ÕÉ•}Ñ½Á¥}•á•ÉÁÑÌ¤(€€€¥˜µ¥ÍÍ¥¹}Ñ½Á¥Ìè(€€€€€€€É•…Í½¹Ì¹…ÁÁ•¹ (€€€€€€€€€€€€‰µ¥ÍÍ¥¹œÍ½ÕÉ”Ñ½Á¥Œ¡Ì¤è€ˆ(€€€€€€€€€€€€¬€ˆ°€ˆ¹©½¥¸ (€€€€€€€€€€€€€€€€¡É½ÕÀ¹•Ğ ‰Ñ½Á¥Œˆ¤½È€ˆˆ¤¹ÍÑÉ¥À ¤(€€€€€€€€€€€€€€€™½ÈÉ½ÕÀ¥¸µ¥ÍÍ¥¹}Ñ½Á¥Ì(€€€€€€€€€€€€¤(€€€€€€€€¤(€€€¥¹Ù•¹Ñ½Éå}¥Ñ•µÌ€ô€ (€€€€€€€€¡¡•­Á½¥¹Ğ¹•Ğ ‰ÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½Éäˆ¤½Èíô¤¹•Ğ ‰¥Ñ•µÌˆ¤½Èmt(€€€€¤(€€€…¹¡½ÉÌ€ô}Í½ÕÉ•}Ñ…Í­}…¹¡½ÉÌ¡Í•Ñ¥½¹Ì¤(€€€…¹¡½É}±…‰•±}½Õ¹ÑÌ€ô}…¹¡½É}Í½ÕÉ•}±…‰•±}½Õ¹ÑÌ¡…¹¡½ÉÌ¤(€€€µ¥ÍÍ¥¹}…¹¡½ÉÌ€ôl(€€€€€€€…¹¡½È™½È…¹¡½È¥¸…¹¡½ÉÌ(€€€€€€€¥˜¹½Ğ…¹ä (€€€€€€€€€€€¥Í¥¹ÍÑ…¹”¡¥Ñ•´°‘¥Ğ¤(€€€€€€€€€€€…¹}¥¹Ù•¹Ñ½Éå}µ…Ñ¡•Í}…¹¡½É}¥‘•¹Ñ¥Ñä (€€€€€€€€€€€€€€€¥Ñ•´°…¹¡½È°…¹¡½É}±…‰•±}½Õ¹ÑÌ¤(€€€€€€€€€€€™½È¥Ñ•´¥¸¥¹Ù•¹Ñ½Éå}¥Ñ•µÌ(€€€€€€€€¤(€€€t(€€€¥˜µ¥ÍÍ¥¹}…¹¡½ÉÌè(€€€€€€€É•…Í½¹Ì¹…ÁÁ•¹ (€€€€€€€€€€€˜‰µ¥ÍÍ¥¹œí±•¸¡µ¥ÍÍ¥¹}…¹¡½ÉÌ¥ô‘•Ñ•Éµ¥¹¥ÍÑ¥ŒÍ½ÕÉ”Ñ…Í¬…¹¡½È¡Ì¤ˆ(€€€€€€€€¤(€€€ÍÑ…±•}…¹¡½É}Ñ…Í­Ì€ôl(€€€€€€€…¹¡½È™½È…¹¡½È¥¸…¹¡½ÉÌ(€€€€€€€¥˜…¹ä (€€€€€€€€€€€¥Í¥¹ÍÑ…¹”¡¥Ñ•´°‘¥Ğ¤(€€€€€€€€€€€…¹}¥¹Ù•¹Ñ½Éå}µ…Ñ¡•Í}…¹¡½É}¥‘•¹Ñ¥Ñä (€€€€€€€€€€€€€€€¥Ñ•´°…¹¡½È°…¹¡½É}±…‰•±}½Õ¹ÑÌ¤(€€€€€€€€€€€™½È¥Ñ•´¥¸¥¹Ù•¹Ñ½Éå}¥Ñ•µÌ(€€€€€€€€¤(€€€€€€€…¹¹½Ğ…¹ä (€€€€€€€€€€€¥Í¥¹ÍÑ…¹”¡¥Ñ•´°‘¥Ğ¤(€€€€€€€€€€€…¹}¥¹Ù•¹Ñ½Éå}¥Ñ•µ}½Ù•ÉÍ}…¹¡½È¡¥Ñ•´°…¹¡½È¤(€€€€€€€€€€€™½È¥Ñ•´¥¸¥¹Ù•¹Ñ½Éå}¥Ñ•µÌ(€€€€€€€€¤(€€€t(€€€¥˜ÍÑ…±•}…¹¡½É}Ñ…Í­Ìè(€€€€€€€É•…Í½¹Ì¹…ÁÁ•¹ (€€€€€€€€€€€˜‰í±•¸¡ÍÑ…±•}…¹¡½É}Ñ…Í­Ì¥ô¡•­Á½¥¹ĞÍ½ÕÉ”Ñ…Í¬¡Ì¤…É”€ˆ(€€€€€€€€€€€€‰ÑÉÕ¹…Ñ•½ÈÍÑ…±”ˆ(€€€€€€€€¤(€€€É•™É•Í¡•‘}¥¹Ù•¹Ñ½Éä€ô}É•™É•Í¡}¥¹Ù•¹Ñ½Éå}™É½µ}Í½ÕÉ•}…¹¡½ÉÌ (€€€€€€€¡•­Á½¥¹Ğ¹•Ğ ‰ÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½Éäˆ¤½Èíô°Í•Ñ¥½¹Ì¤(€€€¥˜€ (€€€€€€€}Í•µ…¹Ñ¥}¥¹Ù•¹Ñ½Éå}¥Ñ•µÌ¡É•™É•Í¡•‘}¥¹Ù•¹Ñ½Éä¤(€€€€€€€€„ô}Í•µ…¹Ñ¥}¥¹Ù•¹Ñ½Éå}¥Ñ•µÌ (€€€€€€€€€€€¡•­Á½¥¹Ğ¹•Ğ ‰ÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½Éäˆ¤½Èíô¤(€€€€€€€…¹¹½Ğµ¥ÍÍ¥¹}…¹¡½ÉÌ(€€€€€€€…¹¹½ĞÍÑ…±•}…¹¡½É}Ñ…Í­Ì(€€€€¤è(€€€€€€€€ŒQ¡”Å¥…¸É•µ…¥¸ÍÑ…‰±”İ¡¥±”Ñ½Á¥Œ°Ñ…Í¬­¥¹°Ñ¥Ù¥Ñä½É¥¥¸°½È(€€€€€€€€Œ…¹½Ñ¡•ÈÍ•µ…¹Ñ¥Œ™¥•±¡…¹•Ì¸MÕ „¡•­Á½¥¹ĞµÕÍĞÉ•Á±…äÑ¡”(€€€€€€€€ŒÉ•Ù¥•İ•¡½ÍĞ‘•¥Í¥½¸•Ù•¸İ¡•¸¥ÑÌÅÕ•ÍÑ¥½¸İ½É‘¥¹œÍÑ¥±°µ…Ñ¡•Ì¸(€€€€€€€É•…Í½¹Ì¹…ÁÁ•¹ ‰Í½ÕÉ”¥¹Ù•¹Ñ½ÉäÍ•µ…¹Ñ¥Ì¡…¹•ˆ¤(€€€¡•­Á½¥¹Ñ}É•½É‘Ì€ô¡•­Á½¥¹Ğ¹•Ğ ‰É•½É‘Ìˆ¤½Èmt(€€€…¹…±åÍ¥Í}É•Á½ÉĞ€ôØ¹Ù…±¥‘…Ñ•}½¹•ÁÑ}É½İÌ (€€€€€€€¡•­Á½¥¹Ñ}É•½É‘Ì°ÍÑÉ¥Ñ}…¹…±åÍ¥Í}Í•Ñ¥½¸õQÉÕ”°(€€€€€€€€ŒDÄè„¡•­Á½¥¹ĞÌ…¹…±åÍ¥Ì½¹ÑÉ…Ğ¥Ì©Õ‘•Õ¹‘•ÈÑ¡”(€€€€€€€€Œ…±±½Ñµ•¹Ğµ…É­•ÉÌ¥ÑÌ½İ¸É½İÌ…ÉÉäƒŠP„ÁÉ”µDÄ¡•­Á½¥¹Ğ(€€€€€€€€Œ€¡Í•Ñ¥½¹Ì•Ù•Éåİ¡•É”°¹¼µ…É­•ÉÌ¤¥ÌÉ•™É•Í¡•°¹•Ù•ÈÉ•ÕÍ•¸(€€€€€€€…¹…±åÍ¥Í}…±±½ÑÑ•‘}­•åÌõØ¹…¹…±åÍ¥Í}…±±½ÑÑ•‘}­•åÌ (€€€€€€€€€€€¡•­Á½¥¹Ñ}É•½É‘Ì¤°(€€€€¤(€€€µ…±™½Éµ•‘}…¹…±åÍ¥Ì€ôl(€€€€€€€•ÉÉ½È™½È•ÉÉ½È¥¸…¹…±åÍ¥Í}É•Á½ÉÑl‰•ÉÉ½ÉÌ‰t(€€€€€€€¥˜•ÉÉ½È¹•Ğ ‰½‘”ˆ¤¥¸€ (€€€€€€€€€€€€‰…¹…±åÍ¥Í}Í•Ñ¥½¹}™½Éµ…Ğˆ°€‰Õ¹…±±½ÑÑ•‘}…¹…±åÍ¥Í}Í•Ñ¥½¸ˆ°(€€€€€€€€¤(€€€€€€€…¹•ÉÉ½È¹•Ğ ‰Í•Ù•É¥Ñäˆ¤€ôô€‰•ÉÉ½Èˆ(€€€t(€€€¥˜µ…±™½Éµ•‘}…¹…±åÍ¥Ìè(€€€€€€€É•…Í½¹Ì¹…ÁÁ•¹ (€€€€€€€€€€€˜‰í±•¸¡µ…±™½Éµ•‘}…¹…±åÍ¥Ì¥ô™¥¹…°±•…É¹•Èµ…¹…±åÍ¥ÌÍ•Ñ¥½¸¡Ì¤€ˆ(€€€€€€€€€€€€‰Ù¥½±…Ñ”Ñ¡”…¹½¹¥…°DÄ½¹ÑÉ…Ğˆ(€€€€€€€€¤(€€€Í½ÕÉ•}Ñ•áĞ€ô€‰q¹q¸ˆ¹©½¥¸ (€€€€€€€ÍÑÈ¡É½ÕÀ¹•Ğ ‰•á•ÉÁĞˆ¤½È€ˆˆ¤(€€€€€€€™½ÈÉ½ÕÀ¥¸Í½ÕÉ•}Ñ½Á¥}•á•ÉÁÑÌ(€€€€€€€¥˜¥Í¥¹ÍÑ…¹”¡É½ÕÀ°‘¥Ğ¤(€€€€¤(€€€¥˜Í½ÕÉ•}Ñ•áĞè(€€€€€€€‘•ÍÉ¥ÁÑ¥½¹}É•Á½ÉĞ€ôØ¹Ù…±¥‘…Ñ•}½¹•ÁÑ}É½İÌ (€€€€€€€€€€€¡•­Á½¥¹Ğ¹•Ğ ‰É•½É‘Ìˆ¤½Èmt°Í½ÕÉ•}Ñ•áĞõÍ½ÕÉ•}Ñ•áĞ¤(€€€€€€€½Á¥•‘}‘•ÍÉ¥ÁÑ¥½¹Ì€ôl(€€€€€€€€€€€•ÉÉ½È™½È•ÉÉ½È¥¸‘•ÍÉ¥ÁÑ¥½¹}É•Á½ÉÑl‰•ÉÉ½ÉÌ‰t(€€€€€€€€€€€¥˜•ÉÉ½È¹•Ğ ‰½‘”ˆ¤€ôô€‰Ù•É‰…Ñ¥µ}Í½ÕÉ•}‘•ÍÉ¥ÁÑ¥½¸ˆ(€€€€€€€t(€€€€€€€¥˜½Á¥•‘}‘•ÍÉ¥ÁÑ¥½¹Ìè(€€€€€€€€€€€É•…Í½¹Ì¹…ÁÁ•¹ (€€€€€€€€€€€€€€€˜‰í±•¸¡½Á¥•‘}‘•ÍÉ¥ÁÑ¥½¹Ì¥ô•ÍÉ¥ÁÑ¥½¸¡Ì¤½ÁäÍ½ÕÉ”ÁÉ½Í”ˆ(€€€€€€€€€€€€¤(€€€É•ÑÕÉ¸É•…Í½¹Ì(()‘•˜}É•™É•Í¡}¥¹Ù•¹Ñ½Éå}™É½µ}Í½ÕÉ•}…¹¡½ÉÌ (€€€¥¹Ù•¹Ñ½Éäè‘¥Ğğ9½¹”°Í•Ñ¥½¹Ìè±¥ÍÑm‘¥Ñt°(¤€´ø‘¥Ğè(€€€€ˆˆ‰5•É”…ÕÑ¡½É¥Ñ…Ñ¥Ù”Í½ÕÉ”Ñ…Í­Ì¥¹Ñ¼…¸½±‘•ÈÉ•ÍÕµ•¥¹Ù•¹Ñ½Éä¸((€€€á¥ÍÑ¥¹œÅ¥‘ÌÉ•µ…¥¸ÍÑ…‰±”Í¼µ¥¹•QåÁ”…ÍÍ¥¹µ•¹ÑÌÍÑ¥±°Á½¥¹Ğ…ĞÑ¡•¥È(€€€½É¥¥¹…°Ñ…Í­Ì¸€9•Ü‘•Ñ•Éµ¥¹¥ÍÑ¥Œ…¹¡½ÉÌÉ••¥Ù”¹•ÜÅ¥‘Ì…¹…É”±…Ñ•È(€€€Á±…•‰äÑ¡”½É‘¥¹…Éä•á…Ğµ½Ù•É…”É•Á…¥ÈÁ…ÍÌ¸(€€€€ˆˆˆ(€€€É•™É•Í¡•€ô½Áä¹‘••Á½Áä¡¥¹Ù•¹Ñ½Éä½È}•µÁÑå}¥¹Ù•¹Ñ½Éä ¤¤(€€€¥Ñ•µÌ€ôl(€€€€€€€‘¥Ğ¡¥Ñ•´¤™½È¥Ñ•´¥¸É•™É•Í¡•¹•Ğ ‰¥Ñ•µÌˆ¤½Èmt(€€€€€€€¥˜¥Í¥¹ÍÑ…¹”¡¥Ñ•´°‘¥Ğ¤(€€€t(€€€µ•É•€ô}µ•É•}Í½ÕÉ•}Ñ…Í­}…¹¡½ÉÌ¡¥Ñ•µÌ°}Í½ÕÉ•}Ñ…Í­}…¹¡½ÉÌ¡Í•Ñ¥½¹Ì¤¤(€€€µ•É•€ô}…ÑÑ…¡}•áÁ±¥¥Ñ}™¥ÕÉ•}¥µ…•Ì¡µ•É•°Í•Ñ¥½¹Ì¤(€€€Í••¹}Å¥‘Ì€ôì(€€€€€€€ÍÑÈ¡¥Ñ•´¹•Ğ ‰Å¥ˆ¤½È€ˆˆ¤¹ÍÑÉ¥À ¤(€€€€€€€™½È¥Ñ•´¥¸µ•É•(€€€€€€€¥˜ÍÑÈ¡¥Ñ•´¹•Ğ ‰Å¥ˆ¤½È€ˆˆ¤¹ÍÑÉ¥À ¤(€€€ô(€€€Å¥‘}¹Õµ‰•ÉÌ€ôl(€€€€€€€¥¹Ğ¡µ…Ñ ¹É½ÕÀ Ä¤¤(€€€€€€€™½ÈÅ¥¥¸Í••¹}Å¥‘Ì(€€€€€€€¥˜€¡µ…Ñ €èôÉ”¹™Õ±±µ…Ñ ¡È‰E%9X´¡q¬¤ˆ°Å¥¤¤(€€€t(€€€¹•áÑ}Å¥€ô€¡µ…à¡Å¥‘}¹Õµ‰•ÉÌ¤¥˜Å¥‘}¹Õµ‰•ÉÌ•±Í”€À¤€¬€Ä(€€€¹•áÑ}½É‘•È€ôµ…à (€€€€€€€€ (€€€€€€€€€€€¥¹Ğ¡¥Ñ•´¹•Ğ ‰½É‘•É}¥¹‘•àˆ¤½È€À¤(€€€€€€€€€€€™½È¥Ñ•´¥¸µ•É•(€€€€€€€€€€€¥˜ÍÑÈ¡¥Ñ•´¹•Ğ ‰½É‘•É}¥¹‘•àˆ¤½È€ˆˆ¤¹¥Í‘¥¥Ğ ¤(€€€€€€€€¤°(€€€€€€€‘•™…Õ±ĞôÀ°(€€€€¤(€€€Å¥‘}½Õ¹ÑÌ€ô½Õ¹Ñ•È (€€€€€€€ÍÑÈ¡¥Ñ•´¹•Ğ ‰Å¥ˆ¤½È€ˆˆ¤¹ÍÑÉ¥À ¤(€€€€€€€™½È¥Ñ•´¥¸µ•É•(€€€€€€€¥˜ÍÑÈ¡¥Ñ•´¹•Ğ ‰Å¥ˆ¤½È€ˆˆ¤¹ÍÑÉ¥À ¤(€€€€¤(€€€™½È¥Ñ•´¥¸µ•É•è(€€€€€€€Å¥€ôÍÑÈ¡¥Ñ•´¹•Ğ ‰Å¥ˆ¤½È€ˆˆ¤¹ÍÑÉ¥À ¤(€€€€€€€¥˜¹½ĞÅ¥½ÈÅ¥‘}½Õ¹ÑÌ¹•Ğ¡Å¥°€À¤€ø€Äè(€€€€€€€€€€€Å¥€ô˜‰E%9Xµí¹•áÑ}Å¥èÀÑ‘ôˆ(€€€€€€€€€€€¹•áÑ}Å¥€¬ô€Ä(€€€€€€€€€€€¥Ñ•µl‰Å¥‰t€ôÅ¥(€€€€€€€¥˜¹½Ğ¥Ñ•´¹•Ğ ‰½É‘•É}¥¹‘•àˆ¤è(€€€€€€€€€€€¹•áÑ}½É‘•È€¬ô€Ä(€€€€€€€€€€€¥Ñ•µl‰½É‘•É}¥¹‘•à‰t€ô¹•áÑ}½É‘•È(€€€€€€€¥˜¥Ñ•´¹•Ğ ‰}Ñ½Á¥}Í½Á”ˆ¤€ôô€‰¡…ÁÑ•Èˆè(€€€€€€€€€€€¥Ñ•µl‰}¡…ÁÑ•É}İ¥‘•}Ñ…Í¬‰t€ôQÉÕ”(€€€É•™É•Í¡•‘l‰¥Ñ•µÌ‰t€ôµ•É•(€€€É•™É•Í¡•‘l‰ÍÑ…ÑÌ‰t€ô}¥¹Ù•¹Ñ½Éå}ÍÑ…ÑÌ¡µ•É•¤(€€€É•ÑÕÉ¸É•™É•Í¡•(()‘•˜}É•½¹¥±•}É•ÍÕµ•‘}µ¥¹•‘}ÑåÁ•Ì (€€€µ¥¹•‘}ÑåÁ•Ìè‘¥Ğğ9½¹”°€¨°¥¹Ù•¹Ñ½Éäè‘¥Ğ°µ•Ñ„è‘¥Ğ°(€€€ÕÍ•}…Á¤è‰½½°°(¤€´ø‘¥Ğè(€€€€ˆˆ‰	É¥¹œÁ•ÉÍ¥ÍÑ•QåÁ”½Å¥…ÍÍ¥¹µ•¹ÑÌ™½Éİ…ÉÑ¼„É•™É•Í¡•¥¹Ù•¹Ñ½Éä¸ˆˆˆ(€€€É•ÍÕµ•‘}Á±…•µ•¹Ñ}±•‘•È€ô}Ù…±¥‘}ÑåÁ•}…Í•}Å¥‘}Á±…•µ•¹Ñ}±•‘•È (€€€€€€€€¡µ¥¹•‘}ÑåÁ•Ì½Èíô¤¹•Ğ¡}QeA}M}E%}A159Q}1I}-d¤(€€€€¤(€€€ÑåÁ•Ì€ô}¹½Éµ…±¥é•}µ¥¹•‘}ÑåÁ•}…¹‘¥‘…Ñ” (€€€€€€€½Áä¹‘••Á½Áä ¡µ¥¹•‘}ÑåÁ•Ì½Èíô¤¹•Ğ ‰ÑåÁ•Ìˆ¤½Èmt¤°(€€€€€€€¥¹Ù•¹Ñ½Éä°(€€€€¤(€€€‘ÕÁ±¥…Ñ•Ì€ô}‘ÕÁ±¥…Ñ•}¥¹Ù•¹Ñ½Éå}…ÍÍ¥¹µ•¹ÑÌ¡¥¹Ù•¹Ñ½Éä°ÑåÁ•Ì¤(€€€¥˜‘ÕÁ±¥…Ñ•Ìè(€€€€€€€ÑåÁ•Ì°}É•µ½Ù•€ô}…ÁÁ±å}•á…Ñ}½¹•}‘ÕÁ±¥…Ñ•}‰…­ÍÑ½À (€€€€€€€€€€€ÑåÁ•Ì°¥¹Ù•¹Ñ½Éä¤(€€€µ¥ÍÍ•€ô}Õ¹½Ù•É•‘}¥¹Ù•¹Ñ½Éå}¥Ñ•µÌ¡¥¹Ù•¹Ñ½Éä°ÑåÁ•Ì¤(€€€¥˜µ¥ÍÍ•…¹ÕÍ•}…Á¤è(€€€€€€€ÑåÁ•Ì€ô}É•½Ù•É}µ¥ÍÍ•‘}ÑåÁ•}‘•±Ñ…Í}Ù¥…}…Á¤ (€€€€€€€€€€€µ•Ñ„õµ•Ñ„°(€€€€€€€€€€€¥¹Ù•¹Ñ½Éäõ¥¹Ù•¹Ñ½Éä°(€€€€€€€€€€€ÑåÁ•ÌõÑåÁ•Ì°(€€€€€€€€€€€µ…á}…ÑÑ•µÁÑÌôÈ°(€€€€€€€€¤(€€€€€€€µ¥ÍÍ•€ô}Õ¹½Ù•É•‘}¥¹Ù•¹Ñ½Éå}¥Ñ•µÌ¡¥¹Ù•¹Ñ½Éä°ÑåÁ•Ì¤(€€€¥˜µ¥ÍÍ•è(€€€€€€€ÑåÁ•Ì°…‘‘•€ô}…ÁÁ•¹‘}‘•Ñ•Éµ¥¹¥ÍÑ¥}ÑåÁ•}™…±±‰…­Ì (€€€€€€€€€€€ÑåÁ•Ì°µ¥ÍÍ•‘}¥Ñ•µÌõµ¥ÍÍ•°¥¹Ù•¹Ñ½Éäõ¥¹Ù•¹Ñ½Éä¤(€€€€€€€¥˜…‘‘•è(€€€€€€€€€€€ÁÉ½É•ÍÌ¹±½œ (€€€€€€€€€€€€€€€˜‰‘‘•í…‘‘•‘ôÍ½ÕÉ”µÉ½Õ¹‘•QåÁ”™…±±‰…¬¡Ì¤İ¡¥±”€ˆ(€€€€€€€€€€€€€€€€‰É•™É•Í¡¥¹œ„É•ÍÕµ•¥¹Ù•¹Ñ½Éä¸ˆ°(€€€€€€€€€€€€€€€±•Ù•°ô‰İ…É¹¥¹œˆ°(€€€€€€€€€€€€¤(€€€‘ÕÁ±¥…Ñ•Ì€ô}‘ÕÁ±¥…Ñ•}¥¹Ù•¹Ñ½Éå}…ÍÍ¥¹µ•¹ÑÌ¡¥¹Ù•¹Ñ½Éä°ÑåÁ•Ì¤(€€€¥˜‘ÕÁ±¥…Ñ•Ìè(€€€€€€€ÑåÁ•Ì°}É•µ½Ù•€ô}…ÁÁ±å}•á…Ñ}½¹•}‘ÕÁ±¥…Ñ•}‰…­ÍÑ½À (€€€€€€€€€€€ÑåÁ•Ì°¥¹Ù•¹Ñ½Éä¤(€€€É•µ…¥¹¥¹}µ¥ÍÍ•€ô}Õ¹½Ù•É•‘}¥¹Ù•¹Ñ½Éå}¥Ñ•µÌ¡¥¹Ù•¹Ñ½Éä°ÑåÁ•Ì¤(€€€É•µ…¥¹¥¹}‘ÕÁ±¥…Ñ•Ì€ô}‘ÕÁ±¥…Ñ•}¥¹Ù•¹Ñ½Éå}…ÍÍ¥¹µ•¹ÑÌ (€€€€€€€¥¹Ù•¹Ñ½Éä°ÑåÁ•Ì¤(€€€¥˜É•µ…¥¹¥¹}µ¥ÍÍ•½ÈÉ•µ…¥¹¥¹}‘ÕÁ±¥…Ñ•Ìè(€€€€€€€É…¥Í”IÕ¹Ñ¥µ•ÉÉ½È (€€€€€€€€€€€€‰É•ÍÕµ•¡•­Á½¥¹ĞQåÁ”¥¹Ù•¹Ñ½Éä½Õ±¹½Ğ‰”É•½¹¥±•è€ˆ(€€€€€€€€€€€˜‰í±•¸¡É•µ…¥¹¥¹}µ¥ÍÍ•¥ôµ¥ÍÍ¥¹œ°€ˆ(€€€€€€€€€€€˜‰í±•¸¡É•µ…¥¹¥¹}‘ÕÁ±¥…Ñ•Ì¥ô‘ÕÁ±¥…Ñ”…ÍÍ¥¹µ•¹Ğ¡Ì¤ˆ(€€€€€€€€¤(€€€€Œ¡½ÍĞµ…À¥ÌÙ…±¥½¹±ä™½ÈÑ¡”•á…Ğ•ÉÑ¥™¥•½İ¹•È½¹ÑÉ…Ğ…¹(€€€€Œ™É½é•¸½¹•ÁĞÁ…å±½…¸I•ÍÕµ”…±İ…åÌ±•ÑÌA¡…Í”€Ì¸ÌÉ•‰Õ¥±Ñ¡…Ğµ…Àì(€€€€ŒÉ•Ñ…¥¹¥¹œ…¸½±µ…À¡•É”½Õ±É½ÕÑ”„½ÉÉ•ĞE%Ñ¼„±•…ä(€€€€ŒÍ½ÕÉ”µÑ½Á¥Œ¡½ÍĞ‰•™½É”Ñ¡”¹•Ü½İ¹•È¥Ì¡•­•¸(€€€™½ÈµÑåÁ”¥¸ÑåÁ•Ìè(€€€€€€€¥˜¹½Ğ¥Í¥¹ÍÑ…¹”¡µÑåÁ”°‘¥Ğ¤è(€€€€€€€€€€€½¹Ñ¥¹Õ”(€€€€€€€™½È­•ä¥¸±¥ÍĞ¡µÑåÁ”¤è(€€€€€€€€€€€¥˜­•ä¹ÍÑ…ÉÑÍİ¥Ñ  ‰}Á¡…Í”ÌÍ|ˆ¤è(€€€€€€€€€€€€€€€µÑåÁ”¹Á½À¡­•ä°9½¹”¤(€€€É•½¹¥±•€ôì‰ÑåÁ•ÌˆèÑåÁ•Íô(€€€¥˜É•ÍÕµ•‘}Á±…•µ•¹Ñ}±•‘•È¥Ì¹½Ğ9½¹”è(€€€€€€€É•½¹¥±•‘m}QeA}M}E%}A159Q}1I}-et€ô½Áä¹‘••Á½Áä (€€€€€€€€€€€É•ÍÕµ•‘}Á±…•µ•¹Ñ}±•‘•È¤(€€€É…¹Õ±…É¥Ñå}É•Ù¥•Ü€ô½Áä¹‘••Á½Áä (€€€€€€€€¡µ¥¹•‘}ÑåÁ•Ì½Èíô¤¹•Ğ ‰}É…¹Õ±…É¥Ñå}É•Ù¥•Üˆ¤¤(€€€¥˜¥Í¥¹ÍÑ…¹”¡É…¹Õ±…É¥Ñå}É•Ù¥•Ü°‘¥Ğ¤è(€€€€€€€É•½¹¥±•‘l‰}É…¹Õ±…É¥Ñå}É•Ù¥•Ü‰t€ôÉ…¹Õ±…É¥Ñå}É•Ù¥•Ü(€€€±•‘•È€ô}Á±…•µ•¹Ñ}•ÉÑ¥™¥…Ñ¥½¹}±•‘•È¡µ¥¹•‘}ÑåÁ•Ì¤(€€€¥˜±•‘•Èè(€€€€€€€ÕÉÉ•¹Ñ}Å¥‘Ì€ôì(€€€€€€€€€€€ÍÑÈ¡¥Ñ•´¹•Ğ ‰Å¥ˆ¤½È€ˆˆ¤¹ÍÑÉ¥À ¤(€€€€€€€€€€€™½È¥Ñ•´¥¸€¡¥¹Ù•¹Ñ½Éä½Èíô¤¹•Ğ ‰¥Ñ•µÌˆ¤½Èmt(€€€€€€€€€€€¥˜¥Í¥¹ÍÑ…¹”¡¥Ñ•´°‘¥Ğ¤(€€€€€€€€€€€…¹ÍÑÈ¡¥Ñ•´¹•Ğ ‰Å¥ˆ¤½È€ˆˆ¤¹ÍÑÉ¥À ¤(€€€€€€€ô(€€€€€€€É•½¹¥±•‘m}A159Q}IQ%%Q%=9M}-et€ôì(€€€€€€€€€€€€‰Ù•ÉÍ¥½¸ˆè}A159Q}IQ%%Q%=9}YIM%=8°(€€€€€€€€€€€€‰¡½ÍÑÌˆèì(€€€€€€€€€€€€€€€Å¥è½Áä¹‘••Á½Áä¡¡½ÍĞ¤(€€€€€€€€€€€€€€€™½ÈÅ¥°¡½ÍĞ¥¸±•‘•Él‰¡½ÍÑÌ‰t¹¥Ñ•µÌ ¤(€€€€€€€€€€€€€€€¥˜€ (€€€€€€€€€€€€€€€€€€€Å¥¥¸ÕÉÉ•¹Ñ}Å¥‘Ì(€€€€€€€€€€€€€€€€€€€…¹}Á±…•µ•¹Ñ}•ÉÑ¥™¥…Ñ¥½¹}•¹ÑÉå}¥Í}Ù…±¥¡¡½ÍĞ¤(€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€ô°(€€€€€€€ô(€€€É•ÑÕÉ¸É•½¹¥±•(()‘•˜}É•™É•Í¡}¥¹Ù•¹Ñ½Éå}™¥ÕÉ•}µ•Ñ…‘…Ñ„ (€€€¥¹Ù•¹Ñ½Éäè‘¥Ğğ9½¹”°Í•Ñ¥½¹Ìè±¥ÍÑm‘¥Ñt°(¤€´ø‘¥Ğè(€€€€ˆˆ‰I”µÉ•Í½±Ù”É•ÍÕµ•¥¹Ù•¹Ñ½Éä¥µ…•Ì……¥¹ÍĞÑ¡”ÕÉÉ•¹ĞÍ½ÕÉ”™¥±”¸ˆˆˆ(€€€É•™É•Í¡•€ô½Áä¹‘••Á½Áä¡¥¹Ù•¹Ñ½Éä½È}•µÁÑå}¥¹Ù•¹Ñ½Éä ¤¤(€€€¥Ñ•µÌ€ôl(€€€€€€€‘¥Ğ¡¥Ñ•´¤™½È¥Ñ•´¥¸É•™É•Í¡•¹•Ğ ‰¥Ñ•µÌˆ¤½Èmt(€€€€€€€¥˜¥Í¥¹ÍÑ…¹”¡¥Ñ•´°‘¥Ğ¤(€€€t(€€€É•™É•Í¡•‘}¥Ñ•µÌ€ô}…ÑÑ…¡}•áÁ±¥¥Ñ}™¥ÕÉ•}¥µ…•Ì¡¥Ñ•µÌ°Í•Ñ¥½¹Ì¤(€€€É•™É•Í¡•‘l‰¥Ñ•µÌ‰t€ôÉ•™É•Í¡•‘}¥Ñ•µÌ(€€€¥˜É•™É•Í¡•‘}¥Ñ•µÌ€„ô¥Ñ•µÌè(€€€€€€€É•™É•Í¡•‘l‰ÍÑ…ÑÌ‰t€ô}¥¹Ù•¹Ñ½Éå}ÍÑ…ÑÌ¡É•™É•Í¡•‘}¥Ñ•µÌ¤(€€€É•ÑÕÉ¸É•™É•Í¡•(()‘•˜}Ù…±¥‘}½¹•ÁÑ}¡•­Á½¥¹Ğ¡¡•­Á½¥¹Ğè‘¥Ğğ9½¹”¤€´ø‰½½°è(€€€€ˆˆ‰	…­İ…Éµ½µÁ…Ñ¥‰±”ÁÕ‰±¥ŒÁÉ•‘¥…Ñ”ÕÍ•‰äÕÁ±½…½‰Õ¹‘±”Í•ÉÙ¥•Ì¸ˆˆˆ(€€€É•ÑÕÉ¸}¹•İ•ÍÑ}½µÁ…Ñ¥‰±•}½¹•ÁÑ}¡•­Á½¥¹Ğ¡¡•­Á½¥¹Ğ¤¥Ì¹½Ğ9½¹”(()‘•˜}¡•­Á½¥¹Ñ}½É‘•È¡ÍÑ…”èÍÑÈ¤€´ø¥¹Ğè(€€€É•ÑÕÉ¸¥¹Ğ¡}=9AQ}!-A=%9Q}MQL¹•Ğ¡ÍÑ…”°íô¤¹•Ğ ‰½É‘•Èˆ°€´Ä¤¤(()‘•˜}µ…­•}½¹•ÁÑ}¡•­Á½¥¹Ğ (€€€ÍÑ…”èÍÑÈ°€¨°ÁÉ½É•ÍÍ}Ù…±Õ”è™±½…Ğğ9½¹”€ô9½¹”°(€€€ÍÑ…•}±…‰•°èÍÑÈ€ô€ˆˆ°€¨©Á…å±½…°(¤€´ø‘¥Ğè(€€€ÍÁ•Œ€ô}=9AQ}!-A=%9Q}MQMmÍÑ…•t(€€€Ù…±Õ”€ôÍÁ•l‰ÁÉ½É•ÍÌ‰t¥˜ÁÉ½É•ÍÍ}Ù…±Õ”¥Ì9½¹”•±Í”ÁÉ½É•ÍÍ}Ù…±Õ”(€€€¥˜€ (€€€€€€€ÍÑ…”€ôô€‰™¥¹…±}½¹Ñ•¹Ñ}É•…‘äˆ(€€€€€€€…¹€‰É½Õ¹‘¥¹}•ÉÑ¥™¥…Ñ•}É•ÅÕ¥É•ˆ¹½Ğ¥¸Á…å±½…(€€€€¤è(€€€€€€€É•½É‘Ì€ôÁ…å±½…¹•Ğ ‰É•½É‘Ìˆ¤½Èmt(€€€€€€€Á…å±½…‘l‰É½Õ¹‘¥¹}•ÉÑ¥™¥…Ñ•}É•ÅÕ¥É•‰t€ô…¹ä (€€€€€€€€€€€…¹ä (€€€€€€€€€€€€€€€ÍÑÈ¡É•½É¹•Ğ¡™¥•±¤½È€ˆˆ¤¹ÍÑÉ¥À ¤(€€€€€€€€€€€€€€€™½È™¥•±¥¸€ (€€€€€€€€€€€€€€€€€€€É½Õ¹‘¥¹}•ÉÑ¥™¥…Ñ”¹I=]}IQ%%Q}%1°(€€€€€€€€€€€€€€€€€€€É½Õ¹‘¥¹}•ÉÑ¥™¥…Ñ”¹M=UI}=9QIQ}%1°(€€€€€€€€€€€€€€€€€€€€‰}Í½ÕÉ•}É½Õ¹‘¥¹}½¹ÑÉ…Ğˆ°(€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€¤(€€€€€€€€€€€™½ÈÉ•½É¥¸É•½É‘Ì(€€€€€€€€€€€¥˜¥Í¥¹ÍÑ…¹”¡É•½É°‘¥Ğ¤(€€€€€€€€¤(€€€É•ÑÕÉ¸ì(€€€€€€€€‰Í¡•µ…}Ù•ÉÍ¥½¸ˆè}=9AQ}!-A=%9Q}M!5°(€€€€€€€€‰ÍÑ…•}Í¡•µ…}Ù•ÉÍ¥½¸ˆèÍÁ•l‰Ù•ÉÍ¥½¸‰t°(€€€€€€€€‰ÍÑ…”ˆèÍÑ…”°(€€€€€€€€‰ÍÑ…•}½É‘•ÈˆèÍÁ•l‰½É‘•È‰t°(€€€€€€€€‰Í…Ù•‘}…Ğˆè‘…Ñ•Ñ¥µ”¹¹½Ü¡Ñ¥µ•é½¹”¹ÕÑŒ¤¹¥Í½™½Éµ…Ğ ¤°(€€€€€€€€‰ÁÉ½É•ÍÌˆèµ…à À¸À°µ¥¸ Ä¸À°™±½…Ğ¡Ù…±Õ”¤¤¤°(€€€€€€€€‰ÍÑ…•}±…‰•°ˆèÍÑ…•}±…‰•°½ÈÍÁ•l‰±…‰•°‰t°(€€€€€€€€‰Í•µ…¹Ñ¥}½¹™¥‘•¹•}Á½±¥äˆè½¹™¥‘•¹•}Á½±¥ä¹…¡•}¥‘•¹Ñ¥Ñä ¤°(€€€€€€€€¨©½Áä¹‘••Á½Áä¡Á…å±½…¤°(€€€ô(()‘•˜}•µ¥Ñ}½¹•ÁÑ}¡•­Á½¥¹Ğ (€€€¡•­Á½¥¹Ñ}…±±‰…¬°ÍÑ…”èÍÑÈ°€¨°ÁÉ½É•ÍÍ}Ù…±Õ”è™±½…Ğğ9½¹”€ô9½¹”°(€€€ÍÑ…•}±…‰•°èÍÑÈ€ô€ˆˆ°€¨©Á…å±½…°(¤€´ø‘¥Ğè(€€€¡•­Á½¥¹Ğ€ô}µ…­•}½¹•ÁÑ}¡•­Á½¥¹Ğ (€€€€€€€ÍÑ…”°(€€€€€€€ÁÉ½É•ÍÍ}Ù…±Õ”õÁÉ½É•ÍÍ}Ù…±Õ”°(€€€€€€€ÍÑ…•}±…‰•°õÍÑ…•}±…‰•°°(€€€€€€€€¨©Á…å±½…°(€€€€¤(€€€¥˜¡•­Á½¥¹Ñ}…±±‰…¬¥Ì¹½Ğ9½¹”è(€€€€€€€¡•­Á½¥¹Ñ}…±±‰…¬¡¡•­Á½¥¹Ğ¤(€€€É•ÑÕÉ¸¡•­Á½¥¹Ğ(()‘•˜}¡Õ¹­}¡•­Á½¥¹Ñ}Í¡„ÈÔØ¡¡Õ¹¬è‘¥Ğ¤€´øÍÑÈè(€€€É•ÑÕÉ¸¡…Í¡±¥ˆ¹Í¡„ÈÔØ (€€€€€€€ÍÑÈ¡¡Õ¹¬¹•Ğ ‰Ñ•áĞˆ¤½È€ˆˆ¤¹•¹½‘” ‰ÕÑ˜´àˆ¤(€€€€¤¹¡•á‘¥•ÍĞ ¤(()±…ÍÌ}…É±å%¹Ù•¹Ñ½ÉåQÉ…¬è(€€€€ˆˆ‰DÄäQÉ…¬èÑ¡”Í½ÕÉ”µ½¹±ä¥¹Ù•¹Ñ½Éä¡…±˜°‰•Í¥‘”Ñ¡”½¹•ÁĞÁ…ÍÍ•Ì¸((€€€½É­•‰•™½É”Ñ¡”Í­•±•Ñ½¸ƒŠP¥ÑÌ¥¹ÁÕĞ¥Ì„‘••À½Áä½˜Ñ¡”(€€€Í•Ñ¥½¹Ì…¹Ñ¡”µ•Ñ…‘…Ñ„°¹½Ñ¡¥¹œ•±Í”ƒŠP…¹©½¥¹•…ĞÑ¡”(€€€ÅÕ•ÍÑ¥½¹}¥¹Ù•¹Ñ½Éå€¡•­Á½¥¹Ğ°İ¡•É”Ñ¡”™¥¹¥Í ¡…±˜€¡Ñ½Á¥Œ(€€€…ÍÍ¥¹µ•¹Ğ°Ñ¡•¸…‘©Õ‘¥…Ñ¥½¸¤ÉÕ¹Ìİ¥Ñ Ñ¡”Í•ÑÑ±•É•½É‘ÌèÑ¡”(€€€Í…µ”Á…å±½…‘Ì°¥¸Ñ¡”Í…µ”½É‘•È°…ÌÑ¡”Í•ÅÕ•¹Ñ¥…°‰Õ¥±¸((€€€•±¥‰•É…Ñ•±ä9<¡•­Á½¥¹Ğ½˜¥ÑÌ½İ¸è„É…Í ½ÈÁ…ÕÍ”‰•™½É”Ñ¡”(€€€©½¥¸±½Í•Ì½¹±äİ…±°µ±½¬°•á…Ñ±ä…Ì„ÁÉ”µ¡•­Á½¥¹ĞÉ…Í …±İ…åÌ(€€€¡…Ì°…¹É•ÍÕµ”Í•µ…¹Ñ¥Ì…É”Õ¹¡…¹•ƒŠP„É•ÍÕµ•ÉÕ¸Ñ¡…Ğ¥ÌÁ…ÍĞ(€€€ÅÕ•ÍÑ¥½¹}¥¹Ù•¹Ñ½Éå€¹•Ù•È™½É­Ì…Ğ…±°¸((€€€¡…±Ğ ¥€¥ÌÑ¡”DÄä¡…±Ğµ‰½Ñ ÉÕ±¥¹œèİ¡•¸„¡Õµ…¸…Ñ”€¡½È…¹ä(€€€É…¥Í”¤ÍÑ½ÁÌÑ¡”ÉÕ¸°Ñ¡”ÑÉ…¬ÍÑ½ÁÌ‰•™½É”¥ÑÌ9aP¡Õ¹¬ì(€€€¥¸µ™±¥¡ĞÁÉ½Ù¥‘•È…±±Ì™¥¹¥Í …¹¹½Ñ¡¥¹œ¹•ÜÍÁ•¹‘Ì¸(€€€€ˆˆˆ((€€€‘•˜}}¥¹¥Ñ}|¡Í•±˜°€¨°µ•Ñ„è‘¥Ğ°Í•Ñ¥½¹Ìè±¥ÍÑm‘¥Ñt¤€´ø9½¹”è(€€€€€€€Í•±˜¹}…¹•°€ôÑ¡É•…‘¥¹œ¹Ù•¹Ğ ¤(€€€€€€€Í•±˜¹}Á½½°€ôQ¡É•…‘A½½±á•ÕÑ½È¡µ…á}İ½É­•ÉÌôÄ¤(€€€€€€€½¹Ñ•áĞ€ô½¹Ñ•áÑÙ…ÉÌ¹½Áå}½¹Ñ•áĞ ¤(€€€€€€€Í•±˜¹}™ÕÑÕÉ”€ôÍ•±˜¹}Á½½°¹ÍÕ‰µ¥Ğ (€€€€€€€€€€€½¹Ñ•áĞ¹ÉÕ¸°(€€€€€€€€€€€Í•±˜¹}ÉÕ¸°(€€€€€€€€€€€½Áä¹‘••Á½Áä¡µ•Ñ„¤°(€€€€€€€€€€€½Áä¹‘••Á½Áä¡Í•Ñ¥½¹Ì¤°(€€€€€€€€¤((€€€‘•˜}ÉÕ¸ (€€€€€€€Í•±˜°µ•Ñ„è‘¥Ğ°Í•Ñ¥½¹Ìè±¥ÍÑm‘¥Ñt°(€€€€¤€´øÑÕÁ±•m‘¥Ğ°±¥ÍÑm‘¥Ñutè(€€€€€€€İ¥Ñ ÁÉ½É•ÍÌ¹±…‰•±}Í½Á” ‰%¹Ù•¹Ñ½Éäƒ
Ü•…É±äÑÉ…¬ˆ¤è(€€€€€€€€€€€É•ÑÕÉ¸}•áÑÉ…Ñ}¥¹Ù•¹Ñ½Éå}ÁÉ•}©½¥¸ (€€€€€€€€€€€€€€€µ•Ñ„õµ•Ñ„°(€€€€€€€€€€€€€€€Í•Ñ¥½¹ÌõÍ•Ñ¥½¹Ì°(€€€€€€€€€€€€€€€Í¡½Õ±‘}…‰½ÉĞõÍ•±˜¹}…¹•°¹¥Í}Í•Ğ°(€€€€€€€€€€€€¤((€€€‘•˜©½¥¸¡Í•±˜¤€´øÑÕÁ±•m‘¥Ğ°±¥ÍÑm‘¥Ñutè(€€€€€€€ÑÉäè(€€€€€€€€€€€É•ÑÕÉ¸Í•±˜¹}™ÕÑÕÉ”¹É•ÍÕ±Ğ ¤(€€€€€€€™¥¹…±±äè(€€€€€€€€€€€Í•±˜¹}Á½½°¹Í¡ÕÑ‘½İ¸¡İ…¥Ğõ…±Í”¤((€€€‘•˜¡…±Ğ¡Í•±˜¤€´ø9½¹”è(€€€€€€€Í•±˜¹}…¹•°¹Í•Ğ ¤(€€€€€€€Í•±˜¹}Á½½°¹Í¡ÕÑ‘½İ¸¡İ…¥ĞõQÉÕ”°…¹•±}™ÕÑÕÉ•ÌõQÉÕ”¤(()‘•˜}ÉÕ¹}±¥Ù•}½¹•ÁÑ}ÁÉ•}™¥¹…±}ÍÑ…•Ì (€€€µµ‘}Ñ•áĞèÍÑÈ°€¨°(€€€ÍÕ‰©•ĞèÍÑÈ°(€€€‰½…ÉèÍÑÈ°(€€€¡…ÁÑ•É}Ñ¥Ñ±”èÍÑÈ°(€€€¡Õ¹­Ìè±¥ÍÑm‘¥Ñt°(€€€Í•Ñ¥½¹Ìè±¥ÍÑm‘¥Ñt°(€€€µ•Ñ¡½‘}…¹¡½ÉÌè±¥ÍÑm‘¥Ñt°(€€€¡•…‘¥¹Ìè±¥ÍÑmÍÑÉt°(€€€Í½ÕÉ•}Ñ½Á¥}•á•ÉÁÑÌè±¥ÍÑm‘¥Ñt°(€€€…±±½İ}¡…ÁÑ•É}Ñ¥Ñ±•}Ñ½Á¥Œè‰½½°°(€€€µ•Ñ„è‘¥Ğ°(€€€…ÉÑ¥™…ÑÌè‘¥Ğğ9½¹”°(€€€É•ÍÕµ•}¡•­Á½¥¹Ğè‘¥Ğğ9½¹”°(€€€¡•­Á½¥¹Ñ}…±±‰…¬°(€€€…±±½İ}™¥¹…±}¡•­Á½¥¹Ğè‰½½°€ôQÉÕ”°(€€€…±±½İ}±•…å}ÁÉ•}É•±•…Í”è‰½½°€ô…±Í”°(¤€´øÑÕÁ±•m±¥ÍÑm‘¥Ñt°‘¥Ğ°‘¥Ğ°‘¥ÑmÑÕÁ±•mÍÑÈ°ÍÑÉt°‘¥Ñutè(€€€€ˆˆ‰IÕ¸Ñ¡É½Õ QåÁ”½…Ñ¥Ù¥Ñä…ÍÍ¥¹µ•¹Ğ™É½´Ñ¡”¹•İ•ÍĞ½µÁ…Ñ¥‰±”ÍÑ…”¸ˆˆˆ(€€€É•ÍÕµ…‰±•}ÍÑ…•Ì€ôÍ•Ğ¡}A=MQ}=9AQ}!-A=%9Q}MQL¤(€€€¥˜¹½Ğ…±±½İ}™¥¹…±}¡•­Á½¥¹Ğè(€€€€€€€É•ÍÕµ…‰±•}ÍÑ…•Ì¹‘¥Í…É ‰™¥¹…±}½¹Ñ•¹Ñ}É•…‘äˆ¤(€€€Í…Ù•€ô}¹•İ•ÍÑ}½µÁ…Ñ¥‰±•}½¹•ÁÑ}¡•­Á½¥¹Ğ (€€€€€€€É•ÍÕµ•}¡•­Á½¥¹Ğ°(€€€€€€€…±±½İ•‘}ÍÑ…•ÌõÉ•ÍÕµ…‰±•}ÍÑ…•Ì°(€€€€€€€…±±½İ}±•…å}ÁÉ•}É•±•…Í”õ…±±½İ}±•…å}ÁÉ•}É•±•…Í”°(€€€€¤(€€€Í…Ù•‘}ÍÑ…”€ôÍÑÈ¡Í…Ù•¹•Ğ ‰ÍÑ…”ˆ¤½È€ˆˆ¤¥˜Í…Ù••±Í”€ˆˆ(€€€Í…Ù•‘}½É‘•È€ô}¡•­Á½¥¹Ñ}½É‘•È¡Í…Ù•‘}ÍÑ…”¤(€€€½ÕĞè±¥ÍÑm‘¥Ñt€ômt(€€€ÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½Éäè‘¥Ğ€ôíô(€€€µ¥¹•‘}ÑåÁ•Ìè‘¥Ğ€ôíô(€€€µ•Ñ¡½‘}É½İ}Í¹…ÁÍ¡½Ğè‘¥ÑmÑÕÁ±•mÍÑÈ°ÍÑÉt°‘¥Ñt€ôíô(€€€Í­•±•Ñ½¹}µ•Ñ¡½‘}É½İ}Í¹…ÁÍ¡½Ğè‘¥ÑmÑÕÁ±•mÍÑÈ°ÍÑÉt°‘¥Ñt€ôíô((€€€¥˜Í…Ù•è(€€€€€€€±…‰•°€ôÍÑÈ¡Í…Ù•¹•Ğ ‰ÍÑ…•}±…‰•°ˆ¤½ÈÍ…Ù•‘}ÍÑ…”¤¹ÍÑÉ¥À ¤(€€€€€€€ÑÉäè(€€€€€€€€€€€Í…Ù•‘}ÁÉ½É•ÍÌ€ô™±½…Ğ¡Í…Ù•¹•Ğ ‰ÁÉ½É•ÍÌˆ¤½È€À¸À¤(€€€€€€€•á•ÁĞ€¡QåÁ•ÉÉ½È°Y…±Õ•ÉÉ½È¤è(€€€€€€€€€€€Í…Ù•‘}ÁÉ½É•ÍÌ€ô€À¸À(€€€€€€€ÁÉ½É•ÍÌ¹ÍÑ•À (€€€€€€€€€€€˜‰½¹•ÁĞ•áÑÉ…Ñ¥½¸ƒŠPÉ•ÍÕµ¥¹œ™É½´í±…‰•±ôˆ°(€€€€€€€€€€€Ù…±Õ”õÍ…Ù•‘}ÁÉ½É•ÍÌ°(€€€€€€€€¤(€€€€€€€¥˜Í…Ù•‘}ÍÑ…”€„ô€‰Í­•±•Ñ½¹}¡Õ¹­Ìˆè(€€€€€€€€€€€½ÕĞ€ô½Áä¹‘••Á½Áä¡Í…Ù•¹•Ğ ‰É•½É‘Ìˆ¤½Èmt¤(€€€€€€€€€€€¥˜¹½Ğ½ÕĞè(€€€€€€€€€€€€€€€É…¥Í”IÕ¹Ñ¥µ•ÉÉ½È (€€€€€€€€€€€€€€€€€€€€‰Í…Ù•½¹•ÁĞ¡•­Á½¥¹Ğ¥Ì¥¹½µÁ±•Ñ”ìÉ•Á±…”Ñ¡”™¥±”€ˆ(€€€€€€€€€€€€€€€€€€€€‰½È±•…ÈÑ¡”¡•­Á½¥¹Ğ‰•™½É”É•ÑÉå¥¹œˆ¤(€€€€€€€¥˜Í…Ù•‘}½É‘•È€øô}¡•­Á½¥¹Ñ}½É‘•È ‰Í½ÕÉ•}Ñ½Á¥}É•Ù¥•Üˆ¤è(€€€€€€€€€€€Í­•±•Ñ½¹}µ•Ñ¡½‘}É½İ}Í¹…ÁÍ¡½Ğ€ô}‘•Í•É¥…±¥é•}µ•Ñ¡½‘}É½İ}Í¹…ÁÍ¡½Ğ (€€€€€€€€€€€€€€€Í…Ù•¹•Ğ ‰Í­•±•Ñ½¹}µ•Ñ¡½‘}É½İ}Í¹…ÁÍ¡½Ğˆ¤¤(€€€€€€€¥˜Í…Ù•‘}½É‘•È€øô}¡•­Á½¥¹Ñ}½É‘•È ‰‘•ÍÉ¥ÁÑ¥½¹}µ•Ñ¡½‘}Í¹…ÁÍ¡½Ğˆ¤è(€€€€€€€€€€€µ•Ñ¡½‘}É½İ}Í¹…ÁÍ¡½Ğ€ô}‘•Í•É¥…±¥é•}µ•Ñ¡½‘}É½İ}Í¹…ÁÍ¡½Ğ (€€€€€€€€€€€€€€€Í…Ù•¹•Ğ ‰µ•Ñ¡½‘}É½İ}Í¹…ÁÍ¡½Ğˆ¤¤(€€€€€€€¥˜Í…Ù•‘}½É‘•È€øô}¡•­Á½¥¹Ñ}½É‘•È ‰ÅÕ•ÍÑ¥½¹}¥¹Ù•¹Ñ½Éäˆ¤è(€€€€€€€€€€€ÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½Éä€ô½Áä¹‘••Á½Áä (€€€€€€€€€€€€€€€Í…Ù•¹•Ğ ‰ÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½Éäˆ¤½Èíô¤(€€€€€€€¥˜Í…Ù•‘}½É‘•È€øô}¡•­Á½¥¹Ñ}½É‘•È (€€€€€€€€€€€}QeA}Qa=9=5e}!-A=%9Q}MQ(€€€€€€€€¤è(€€€€€€€€€€€µ¥¹•‘}ÑåÁ•Ì€ô½Áä¹‘••Á½Áä¡Í…Ù•¹•Ğ ‰µ¥¹•‘}ÑåÁ•Ìˆ¤½Èíô¤(€€€€€€€µ¥ÍÍ¥¹}Í…Ù•‘}Ñ½Á¥Ì€ô}µ¥ÍÍ¥¹}Í½ÕÉ•}Ñ½Á¥}•á•ÉÁÑÌ (€€€€€€€€€€€½ÕĞ°Í½ÕÉ•}Ñ½Á¥}•á•ÉÁÑÌ¤(€€€€€€€¥˜€ (€€€€€€€€€€€Í…Ù•‘}½É‘•È€øô}¡•­Á½¥¹Ñ}½É‘•È ‰…¹½¹¥…±}Í­•±•Ñ½¸ˆ¤(€€€€€€€€€€€…¹µ¥ÍÍ¥¹}Í…Ù•‘}Ñ½Á¥Ì(€€€€€€€€¤è(€€€€€€€€€€€€ŒÍÑÉÕÑÕÉ…±±ä¥¹½µÁ±•Ñ”¹•İ•È¡•­Á½¥¹ĞµÕÍĞ¹½Ğ‰åÁ…ÍÌÑ¡”(€€€€€€€€€€€€Œ¡Õµ…¸Ñ½Á½±½ä…Ñ”½ÈÍÁ•¹µ½¹•äÉ•½¹¥±¥¹œ¥ÑÌÍÑ…±”(€€€€€€€€€€€€Œ¥¹Ù•¹Ñ½Éä½QåÁ•Ì¸€ÁÁ±ä½¹”…ÁÁÉ½Ù•É•½Ù•Éä°Ñ¡•¸É•‰Õ¥±•Ù•Éä(€€€€€€€€€€€€ŒÑ½Á½±½äµ‘•Á•¹‘•¹ĞÍÑ…”™É½´Ñ¡”‘ÕÉ…‰±”€ÌÔ”¡•­Á½¥¹Ğ¸(€€€€€€€€€€€ÁÉ½É•ÍÌ¹±½œ (€€€€€€€€€€€€€€€€‰I•ÍÕµ•¡•­Á½¥¹Ğ½µ¥ÑÌÍÑÉÕÑÕÉ…±±äÁÉ½Ù•¸Í½ÕÉ”Ñ½Á¥Ìì€ˆ(€€€€€€€€€€€€€€€€‰É•İ¥¹‘¥¹œÑ¼Í½ÕÉ”µÑ½Á¥ŒÉ•Ù¥•Ü‰•™½É”™ÕÉÑ¡•ÈA$İ½É¬¸ˆ°(€€€€€€€€€€€€€€€±•Ù•°ô‰İ…É¹¥¹œˆ°(€€€€€€€€€€€€¤(€€€€€€€€€€€½ÕĞ€ô}É•½Ù•É}µ¥ÍÍ¥¹}Ñ½Á¥Í}…™Ñ•É}¡Õµ…¹}‘¥É•Ñ¥½¸ (€€€€€€€€€€€€€€€½ÕĞ°(€€€€€€€€€€€€€€€µ•Ñ„õµ•Ñ„°(€€€€€€€€€€€€€€€Í½ÕÉ•}Ñ½Á¥}•á•ÉÁÑÌõÍ½ÕÉ•}Ñ½Á¥}•á•ÉÁÑÌ°(€€€€€€€€€€€€€€€¡•­Á½¥¹Ñ}…±±‰…¬õ¡•­Á½¥¹Ñ}…±±‰…¬°(€€€€€€€€€€€€€€€Í­•±•Ñ½¹}µ•Ñ¡½‘}É½İ}Í¹…ÁÍ¡½Ğô (€€€€€€€€€€€€€€€€€€€Í­•±•Ñ½¹}µ•Ñ¡½‘}É½İ}Í¹…ÁÍ¡½Ğ½Èµ•Ñ¡½‘}É½İ}Í¹…ÁÍ¡½Ğ(€€€€€€€€€€€€€€€€¤°(€€€€€€€€€€€€¤(€€€€€€€€€€€Í­•±•Ñ½¹}µ•Ñ¡½‘}É½İ}Í¹…ÁÍ¡½Ğ€ô€ (€€€€€€€€€€€€€€€Í­•±•Ñ½¹}µ•Ñ¡½‘}É½İ}Í¹…ÁÍ¡½Ğ½Èµ•Ñ¡½‘}É½İ}Í¹…ÁÍ¡½Ğ(€€€€€€€€€€€€¤(€€€€€€€€€€€ÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½Éä€ôíô(€€€€€€€€€€€µ¥¹•‘}ÑåÁ•Ì€ôíô(€€€€€€€€€€€µ•Ñ¡½‘}É½İ}Í¹…ÁÍ¡½Ğ€ôíô(€€€€€€€€€€€Í…Ù•‘}ÍÑ…”€ô€‰Í½ÕÉ•}Ñ½Á¥}É•Ù¥•Üˆ(€€€€€€€€€€€Í…Ù•‘}½É‘•È€ô}¡•­Á½¥¹Ñ}½É‘•È¡Í…Ù•‘}ÍÑ…”¤(€€€€€€€¥˜Í…Ù•‘}½É‘•È€øô}¡•­Á½¥¹Ñ}½É‘•È ‰ÅÕ•ÍÑ¥½¹}¥¹Ù•¹Ñ½Éäˆ¤è(€€€€€€€€€€€É•ÍÑ½É•‘}¥¹Ù•¹Ñ½Éä€ô½Áä¹‘••Á½Áä¡ÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½Éä¤(€€€€€€€€€€€ÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½Éä€ô}É•™É•Í¡}¥¹Ù•¹Ñ½Éå}™É½µ}Í½ÕÉ•}…¹¡½ÉÌ (€€€€€€€€€€€€€€€ÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½Éä°Í•Ñ¥½¹Ì¤(€€€€€€€€€€€¥¹Ù•¹Ñ½Éå}É•™É•Í¡•€ô€ (€€€€€€€€€€€€€€€}Í•µ…¹Ñ¥}¥¹Ù•¹Ñ½Éå}¥Ñ•µÌ¡ÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½Éä¤(€€€€€€€€€€€€€€€€„ô}Í•µ…¹Ñ¥}¥¹Ù•¹Ñ½Éå}¥Ñ•µÌ¡É•ÍÑ½É•‘}¥¹Ù•¹Ñ½Éä¤(€€€€€€€€€€€€¤(€€€€€€€€€€€¥˜¥¹Ù•¹Ñ½Éå}É•™É•Í¡•è(€€€€€€€€€€€€€€€ÁÉ½É•ÍÌ¹±½œ (€€€€€€€€€€€€€€€€€€€€‰I•™É•Í¡•Ñ¡”É•ÍÕµ•EÕ•ÍÑ¥½¸€¼Q…Í¬%¹Ù•¹Ñ½Éä™É½´€ˆ(€€€€€€€€€€€€€€€€€€€€‰ÕÉÉ•¹Ğ‘•Ñ•Éµ¥¹¥ÍÑ¥ŒÍ½ÕÉ”…¹¡½ÉÌ¸ˆ°(€€€€€€€€€€€€€€€€€€€±•Ù•°ô‰ÍÕ•ÍÌˆ°(€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€¥˜€ (€€€€€€€€€€€€€€€Í…Ù•‘}½É‘•È€øô}¡•­Á½¥¹Ñ}½É‘•È (€€€€€€€€€€€€€€€€€€€}QeA}Qa=9=5e}!-A=%9Q}MQ¤(€€€€€€€€€€€€€€€…¹€ (€€€€€€€€€€€€€€€€€€€¥¹Ù•¹Ñ½Éå}É•™É•Í¡•(€€€€€€€€€€€€€€€€€€€½È€ (€€€€€€€€€€€€€€€€€€€€€€€Í…Ù•‘}ÍÑ…”€„ô€‰™¥¹…±}½¹Ñ•¹Ñ}É•…‘äˆ(€€€€€€€€€€€€€€€€€€€€€€€…¹‰½½° ¡µ¥¹•‘}ÑåÁ•Ì½Èíô¤¹•Ğ ‰ÑåÁ•Ìˆ¤¤(€€€€€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€¤è(€€€€€€€€€€€€€€€µ¥¹•‘}ÑåÁ•Ì€ô}É•½¹¥±•}É•ÍÕµ•‘}µ¥¹•‘}ÑåÁ•Ì (€€€€€€€€€€€€€€€€€€€µ¥¹•‘}ÑåÁ•Ì°(€€€€€€€€€€€€€€€€€€€¥¹Ù•¹Ñ½ÉäõÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½Éä°(€€€€€€€€€€€€€€€€€€€µ•Ñ„õµ•Ñ„°(€€€€€€€€€€€€€€€€€€€€ŒQ¡”•…É±äÑ…á½¹½µä¡•­Á½¥¹Ğİ…Ì…±É•…‘ä•á…Ğµ½Ù•É•(€€€€€€€€€€€€€€€€€€€€Œ‰•™½É”¥Ğİ…ÌÁ•ÉÍ¥ÍÑ•¸I•Ù…±¥‘…Ñ”¥Ğ‘•Ñ•Éµ¥¹¥ÍÑ¥…±±ä°(€€€€€€€€€€€€€€€€€€€€Œ‰ÕĞ¹•Ù•È¥¹Í•ÉĞ…¸Õ¹…ÁÁÉ½Ù•Á…¥É•Á…¥È‰•™½É”…ÁÁ±å¥¹œ(€€€€€€€€€€€€€€€€€€€€ŒÑ¡”Í…Ù•¡Õµ…¸‘¥É•Ñ¥½¸¸(€€€€€€€€€€€€€€€€€€€ÕÍ•}…Á¤ô (€€€€€€€€€€€€€€€€€€€€€€€Í…Ù•‘}ÍÑ…”€„ô}QeA}Qa=9=5e}!-A=%9Q}MQ(€€€€€€€€€€€€€€€€€€€€¤°(€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€¥˜€ (€€€€€€€€€€€€€€€€€€€¥¹Ù•¹Ñ½Éå}É•™É•Í¡•(€€€€€€€€€€€€€€€€€€€…¹}É•Ù¥•İ•‘}Á±…•µ•¹Ñ}…ÕÑ¡½É¥Ñå}‘•±…É•¡µ¥¹•‘}ÑåÁ•Ì¤(€€€€€€€€€€€€€€€€¤è(€€€€€€€€€€€€€€€€€€€€ŒE%Ì…É”ÍÑ…‰±”¥‘•¹Ñ¥™¥•ÉÌ°¹½Ğ„ÁÉ½µ¥Í”Ñ¡…ĞÑ¡•¥È(€€€€€€€€€€€€€€€€€€€€ŒÍ•µ…¹Ñ¥Œ¡½ÍĞ•Ù¥‘•¹”¥ÌÕ¹¡…¹•¸I•…ÍÍ¥¹µ•¹Ğ‰•±½Ü(€€€€€€€€€€€€€€€€€€€€ŒµÕÍĞÉ•…Ñ”™É•Í É•Ù¥•İ•Å¥µÑ¼µ¡½ÍĞ•ÉÑ¥™¥…Ñ¥½¹Ì¸(€€€€€€€€€€€€€€€€€€€}É•Í•Ñ}Á±…•µ•¹Ñ}•ÉÑ¥™¥…Ñ¥½¹Ì¡µ¥¹•‘}ÑåÁ•Ì¤(€€€€€€€€€€€¥˜€ (€€€€€€€€€€€€€€€Í…Ù•‘}½É‘•È€øô}¡•­Á½¥¹Ñ}½É‘•È (€€€€€€€€€€€€€€€€€€€}QeA}Qa=9=5e}!-A=%9Q}MQ¤(€€€€€€€€€€€€€€€…¹‰½½° ¡µ¥¹•‘}ÑåÁ•Ì½Èíô¤¹•Ğ ‰ÑåÁ•Ìˆ¤¤(€€€€€€€€€€€€¤è(€€€€€€€€€€€€€€€€ŒQ¡¥Ì½ÁÑ¥½¹…°µ½‘•°¡¥¹Ğ¥Ì¹½ĞÁ…ÉĞ½˜Í•µ…¹Ñ¥Œ¥¹Ù•¹Ñ½Éä(€€€€€€€€€€€€€€€€Œ¥‘•¹Ñ¥Ñä°Í¼±•…¸¥Ğ•Ù•¸™½È…¸½Ñ¡•Éİ¥Í”Õ¹¡…¹•Í…Ù•(€€€€€€€€€€€€€€€€Œ™¥¹…°¡•­Á½¥¹ĞÑ¡…Ğ…¸Í­¥À™Õ±°QåÁ”É•½¹¥±¥…Ñ¥½¸¸(€€€€€€€€€€€€€€€µ¥¹•‘}ÑåÁ•Íl‰ÑåÁ•Ì‰t€ô€ (€€€€€€€€€€€€€€€€€€€}±•…É}¹½¹Ù¥ÍÕ…±}‘¥…É…µ}¥¹Ñ•ÉÁÉ•Ñ…Ñ¥½¹}¡¥¹ÑÌ (€€€€€€€€€€€€€€€€€€€€€€€±¥ÍĞ¡µ¥¹•‘}ÑåÁ•Ì¹•Ğ ‰ÑåÁ•Ìˆ¤½Èmt¤°(€€€€€€€€€€€€€€€€€€€€€€€ÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½Éä°(€€€€€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€€¤(€€€€€€€¥˜€ (€€€€€€€€€€€Í…Ù•‘}ÍÑ…”€„ô€‰™¥¹…±}½¹Ñ•¹Ñ}É•…‘äˆ(€€€€€€€€€€€…¹Í…Ù•‘}½É‘•È€øô}¡•­Á½¥¹Ñ}½É‘•È ‰Á½ÍÑ}ÑåÁ•}…ÍÍ¥¹µ•¹Ğˆ¤(€€€€€€€€€€€…¹¹½Ğ}Á±…•µ•¹Ñ}•ÉÑ¥™¥…Ñ¥½¹}½¹ÑÉ…Ñ}½µÁ±•Ñ” (€€€€€€€€€€€€€€€µ¥¹•‘}ÑåÁ•Ì°ÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½Éä(€€€€€€€€€€€€¤(€€€€€€€€¤è(€€€€€€€€€€€€Œ•Ñ•Éµ¥¹¥ÍÑ¥ŒÍ½ÕÉ”µ…¹¡½ÈÉÕ±•Ì…¸‘¥Í½Ù•ÈÅ¥‘ÌÑ¡…Ğ‘¥¹½Ğ(€€€€€€€€€€€€Œ•á¥ÍĞİ¡•¸…¸½Ñ¡•Éİ¥Í”µ½µÁ…Ñ¥‰±”€äÄ”¡•­Á½¥¹Ğİ…ÌÍ…Ù•¸(€€€€€€€€€€€€ŒI•½¹¥±•QåÁ•Ì…±½¹”…É”¥¹ÍÕ™™¥¥•¹Ğè•Ù•ÉäÕÉÉ•¹ĞÅ¥µÕÍĞ(€€€€€€€€€€€€ŒÁ…ÍÌÑ¡”•á…Ğ¡½ÍĞ…ÍÍ¥¹µ•¹Ğ‰½Õ¹‘…Éä…¹É••¥Ù”„‘ÕÉ…‰±”(€€€€€€€€€€€€Œ•ÉÑ¥™¥…Ñ¥½¸‰•™½É”™¥¹…±¥é…Ñ¥½¸¸(€€€€€€€€€€€ÁÉ½É•ÍÌ¹±½œ (€€€€€€€€€€€€€€€€‰I•™É•Í¡•¥¹Ù•¹Ñ½Éä¥Ì¹½Ğ™Õ±±ä¡½ÍĞµ•ÉÑ¥™¥•ìÉ•ÉÕ¹¹¥¹œ€ˆ(€€€€€€€€€€€€€€€€‰QåÁ”…¹Ñ¥Ù¥Ñä½%¹™¼!Õˆ…ÍÍ¥¹µ•¹Ğ™É½´Ñ¡”€àÄ”ÍÑ…”¸ˆ°(€€€€€€€€€€€€€€€±•Ù•°ô‰İ…É¹¥¹œˆ°(€€€€€€€€€€€€¤(€€€€€€€€€€€½ÕĞ€ô}ÍÑÉ¥Á}ÑåÁ•Í}™É½µ}É•½É‘Ì¡½Áä¹‘••Á½Áä¡½ÕĞ¤¤(€€€€€€€€€€€Í…Ù•‘}½É‘•È€ô}¡•­Á½¥¹Ñ}½É‘•È¡}=9AQ}!-A=%9Q}MQ¤(€€€€€€€ÁÉ½É•ÍÌ¹±½œ (€€€€€€€€€€€˜‰I•ÍÑ½É•¡•­Á½¥¹ĞÍÑ…”€íÍ…Ù•‘}ÍÑ…•ôœ€ˆ(€€€€€€€€€€€˜ˆ¡í±•¸¡½ÕĞ¥ôµ…Ñ•É¥…±¥é•½¹•ÁĞÉ½Ü¡Ì¤¤¸ˆ°(€€€€€€€€€€€±•Ù•°ô‰ÍÕ•ÍÌˆ°(€€€€€€€€¤((€€€€ŒDÄäQÉ…¬€¡½İ¹•È€‰¼ˆ°€ÈÀÈØ´Àà´ÈÄ¤èÑ¡”Í½ÕÉ”µ½¹±ä¥¹Ù•¹Ñ½Éä(€€€€Œ¡…±˜ÉÕ¹Ì	M%Ñ¡”Í­•±•Ñ½¸½‘•ÍÉ¥ÁÑ¥½¸Á…ÍÍ•Ì¥¹ÍÑ•…½˜…™Ñ•È(€€€€ŒÑ¡•´ƒŠP¥ĞÉ•…‘Ì¹½Ñ¡¥¹œ‰ÕĞÑ¡”Í•Ñ¥½¹Ì¸Q¡”™¥¹¥Í ¡…±˜€¡Ñ½Á¥Œ(€€€€Œ…ÍÍ¥¹µ•¹Ğ°Ñ¡•¸…‘©Õ‘¥…Ñ¥½¸¤¥ÌÑ¡”©½¥¸‰•±½Ü°İ¥Ñ Á…å±½…‘Ì¥¸(€€€€ŒÑ¡”•á…ĞÍ•ÅÕ•¹Ñ¥…°½É‘•È¸]½É­•ÉÌôÄ­••ÁÌÑ¡”ÍÑÉ¥Ñ±ä(€€€€ŒÍ•ÅÕ•¹Ñ¥…°Á…Ñ ì„É•ÍÕµ•ÉÕ¸Á…ÍĞÑ¡”¥¹Ù•¹Ñ½Éä¹•Ù•È™½É­Ì¸(€€€•…É±å}¥¹Ù•¹Ñ½Éä€ô€ (€€€€€€€}…É±å%¹Ù•¹Ñ½ÉåQÉ…¬¡µ•Ñ„õµ•Ñ„°Í•Ñ¥½¹ÌõÍ•Ñ¥½¹Ì¤(€€€€€€€¥˜€ (€€€€€€€€€€€Í…Ù•‘}½É‘•È€ğ}¡•­Á½¥¹Ñ}½É‘•È ‰ÅÕ•ÍÑ¥½¹}¥¹Ù•¹Ñ½Éäˆ¤(€€€€€€€€€€€…¹½¹™¥œ¹Í½ÕÉ•}¡Õ¹­}İ½É­•ÉÌ ¤€ø€Ä(€€€€€€€€¤(€€€€€€€•±Í”9½¹”(€€€€¤(€€€ÑÉäè(€€€€€€€¥˜Í…Ù•‘}½É‘•È€ğ}¡•­Á½¥¹Ñ}½É‘•È ‰Í­•±•Ñ½¹}½µÁ±•Ñ”ˆ¤è(€€€€€€€€€€€½ÕĞ€ô}•áÑÉ…Ñ}Í­•±•Ñ½¹}Ù¥…}…Á¤ (€€€€€€€€€€€€€€€¡Õ¹­Ì°(€€€€€€€€€€€€€€€µ•Ñ„õµ•Ñ„°(€€€€€€€€€€€€€€€É•ÍÕµ•}¡Õ¹­Ìô (€€€€€€€€€€€€€€€€€€€Í…Ù•¹•Ğ ‰½µÁ±•Ñ•‘}¡Õ¹­Ìˆ¤½Èmt(€€€€€€€€€€€€€€€€€€€¥˜Í…Ù•‘}ÍÑ…”€ôô€‰Í­•±•Ñ½¹}¡Õ¹­Ìˆ…¹Í…Ù••±Í”mt(€€€€€€€€€€€€€€€€¤°(€€€€€€€€€€€€€€€¡•­Á½¥¹Ñ}…±±‰…¬õ¡•­Á½¥¹Ñ}…±±‰…¬°(€€€€€€€€€€€€¤(€€€€€€€€€€€¥˜¹½Ğ½ÕĞè(€€€€€€€€€€€€€€€É…¥Í”IÕ¹Ñ¥µ•ÉÉ½È ‰±¥Ù”½¹•ÁĞ•áÑÉ…Ñ¥½¸É•ÑÕÉ¹•¹¼É½İÌˆ¤(€€€€€€€€€€€}•µ¥Ñ}½¹•ÁÑ}¡•­Á½¥¹Ğ (€€€€€€€€€€€€€€€¡•­Á½¥¹Ñ}…±±‰…¬°(€€€€€€€€€€€€€€€€‰Í­•±•Ñ½¹}½µÁ±•Ñ”ˆ°(€€€€€€€€€€€€€€€É•½É‘Ìõ½ÕĞ°(€€€€€€€€€€€€¤((€€€€€€€¥˜Í…Ù•‘}½É‘•È€ğ}¡•­Á½¥¹Ñ}½É‘•È ‰Í½ÕÉ•}Ñ½Á¥}É•Ù¥•Üˆ¤è(€€€€€€€€€€€½ÕĞ€ô}…¹½¹¥…±¥é•}µ•Ñ¡½‘}…¹¡½É}Ñ…Ì (€€€€€€€€€€€€€€€½ÕĞ°µ•Ñ¡½‘}…¹¡½ÉÌ°¡Õ¹­}Ñ•áĞõµµ‘}Ñ•áĞ°µ•Ñ„õµ•Ñ„¤(€€€€€€€€€€€Í­•±•Ñ½¹}µ•Ñ¡½‘}É½İ}Í¹…ÁÍ¡½Ğ€ô}Í¹…ÁÍ¡½Ñ}µ•Ñ¡½‘}…¹¡½É}É½İÌ (€€€€€€€€€€€€€€€½ÕĞ°µ•Ñ¡½‘}…¹¡½ÉÌ¤(€€€€€€€€€€€ÁÉ½É•ÍÌ¹ÍÑ•À ‰½¹•ÁĞ•áÑÉ…Ñ¥½¸ƒŠP…¹½¹¥…±¥é¥¹œÍ­•±•Ñ½¸ˆ°Ù…±Õ”ôÀ¸ÈÜ¤(€€€€€€€€€€€½ÕĞ€ô}ÍÉÕ‰}Í•Ñ¥½¹}¹Õµ‰•ÉÌ¡½ÕĞ¤(€€€€€€€€€€€½ÕĞ€ô}Í¹…Á}Ñ½Á¥Í}Ñ½}¡•…‘¥¹Ì (€€€€€€€€€€€€€€€½ÕĞ°¡•…‘¥¹Ì°¡…ÁÑ•É}Ñ¥Ñ±”õ¡…ÁÑ•É}Ñ¥Ñ±”°(€€€€€€€€€€€€€€€…±±½İ}¡…ÁÑ•É}Ñ¥Ñ±•}Ñ½Á¥Œõ…±±½İ}¡…ÁÑ•É}Ñ¥Ñ±•}Ñ½Á¥Œ¤(€€€€€€€€€€€½ÕĞ€ô}½¹Í½±¥‘…Ñ•}½¹•ÁÑÍ}Ù¥…}…Á¤ (€€€€€€€€€€€€€€€½ÕĞ°ÍÕ‰©•ĞõÍÕ‰©•Ğ°µµ‘}Ñ•áĞõµµ‘}Ñ•áĞ°µ•Ñ„õµ•Ñ„¤(€€€€€€€€€€€ÁÉ½É•ÍÌ¹ÍÑ•À ‰½¹•ÁĞ•áÑÉ…Ñ¥½¸ƒŠP…±¥¹¥¹œÍ½ÕÉ”Ñ½Á¥Ìˆ°Ù…±Õ”ôÀ¸ÌÔ¤(€€€€€€€€€€€¥˜±•¸¡m ™½È ¥¸¡•…‘¥¹Ì¥˜ ¹ÍÑÉ¥À ¥t¤€ğ€Èè(€€€€€€€€€€€€€€€€ŒÍ¥¹±”¡•…‘¥¹œ€¡½È¹½¹”¤±•…Ù•Ì¹½Ñ¡¥¹œÑ¼É”µÍ•É•…Ñ”(€€€€€€€€€€€€€€€€Œ……¥¹ÍĞƒŠPÑ¡”‘•¥Í¥½¸ÍÁ…”¡…Ì½¹”½ÁÑ¥½¸°Í¼¹¼©Õ‘µ•¹Ğ(€€€€€€€€€€€€€€€€Œ•á¥ÍÑÌÑ¼µ…­”¸á…ĞÍ½ÕÉ”•Ù¥‘•¹”ÍÑ¥±°…ÍÍ¥¹Ìİ¡…Ğ¥Ğ(€€€€€€€€€€€€€€€€Œ…¸ÁÉ½Ù”¸(€€€€€€€€€€€€€€€½ÕĞ€ô}…ÍÍ¥¹}Ñ½Á¥Í}™É½µ}Í½ÕÉ•}•Ù¥‘•¹” (€€€€€€€€€€€€€€€€€€€½ÕĞ°Í½ÕÉ•}Ñ½Á¥}•á•ÉÁÑÌ¤(€€€€€€€€€€€•±Í”è(€€€€€€€€€€€€€€€Í•É•…Ñ¥½¸€ô}Ñ½Á¥}Í•É•…Ñ¥½¹}Ù•É‘¥Ñ}Ù¥…}…Á¤ (€€€€€€€€€€€€€€€€€€€½ÕĞ°µ•Ñ„õµ•Ñ„°(€€€€€€€€€€€€€€€€€€€Í½ÕÉ•}Ñ½Á¥}•á•ÉÁÑÌõÍ½ÕÉ•}Ñ½Á¥}•á•ÉÁÑÌ°(€€€€€€€€€€€€€€€€€€€¡•…‘¥¹Ìõ¡•…‘¥¹Ì°(€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€É•…Í½¹}ÍÕ™™¥à€ô€ (€€€€€€€€€€€€€€€€€€€˜ˆèíÍ•É•…Ñ¥½¹lÉ•…Í½¸uôˆ¥˜Í•É•…Ñ¥½¹l‰É•…Í½¸‰t•±Í”€ˆ¸ˆ(€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€¥˜Í•É•…Ñ¥½¹l‰É•ÍÑÉÕÑÕÉ”‰tè(€€€€€€€€€€€€€€€€€€€ÁÉ½É•ÍÌ¹±½œ (€€€€€€€€€€€€€€€€€€€€€€€€‰Q½Á¥ŒÍ•É•…Ñ¥½¸©Õ‘•Õ¹™…¥Ñ¡™Õ°Ñ¼Ñ¡”Í½ÕÉ”ˆ(€€€€€€€€€€€€€€€€€€€€€€€€¬É•…Í½¹}ÍÕ™™¥à€¬€ˆI”µÍ•É•…Ñ¥¹œÑ½Á¥ÌÙ¥„A$¸ˆ°(€€€€€€€€€€€€€€€€€€€€€€€±•Ù•°ô‰İ…É¹¥¹œˆ°(€€€€€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€€€€€½ÕĞ€ô}É•ÍÑÉÕÑÕÉ•}Ñ½Á¥Í}Ù¥…}…Á¤ (€€€€€€€€€€€€€€€€€€€€€€€½ÕĞ°µ•Ñ„õµ•Ñ„°(€€€€€€€€€€€€€€€€€€€€€€€Í½ÕÉ•}Ñ½Á¥}•á•ÉÁÑÌõÍ½ÕÉ•}Ñ½Á¥}•á•ÉÁÑÌ¤(€€€€€€€€€€€€€€€•±Í”è(€€€€€€€€€€€€€€€€€€€ÁÉ½É•ÍÌ¹±½œ (€€€€€€€€€€€€€€€€€€€€€€€€‰Q½Á¥ŒÍ•É•…Ñ¥½¸©Õ‘•™…¥Ñ¡™Õ°Ñ¼Ñ¡”Í½ÕÉ”ˆ(€€€€€€€€€€€€€€€€€€€€€€€€¬É•…Í½¹}ÍÕ™™¥à¤(€€€€€€€€€€€€€€€€€€€½ÕĞ€ô}…ÍÍ¥¹}Ñ½Á¥Í}™É½µ}Í½ÕÉ•}•Ù¥‘•¹” (€€€€€€€€€€€€€€€€€€€€€€€½ÕĞ°Í½ÕÉ•}Ñ½Á¥}•á•ÉÁÑÌ¤(€€€€€€€€€€€½ÕĞ€ô}Í¹…Á}Ñ½Á¥Í}Ñ½}¡•…‘¥¹Ì (€€€€€€€€€€€€€€€½ÕĞ°¡•…‘¥¹Ì°¡…ÁÑ•É}Ñ¥Ñ±”õ¡…ÁÑ•É}Ñ¥Ñ±”°(€€€€€€€€€€€€€€€…±±½İ}¡…ÁÑ•É}Ñ¥Ñ±•}Ñ½Á¥Œõ…±±½İ}¡…ÁÑ•É}Ñ¥Ñ±•}Ñ½Á¥Œ¤((€€€€€€€¥˜Í…Ù•‘}½É‘•È€ğ}¡•­Á½¥¹Ñ}½É‘•È ‰…¹½¹¥…±}Í­•±•Ñ½¸ˆ¤è(€€€€€€€€€€€É•½Ù•Éå}ÍÑ…Ñ”€ô€ (€€€€€€€€€€€€€€€½Áä¹‘••Á½Áä¡Í…Ù•¹•Ğ ‰Í½ÕÉ•}Ñ½Á¥}É•½Ù•Éäˆ¤½Èíô¤(€€€€€€€€€€€€€€€¥˜Í…Ù•‘}ÍÑ…”€ôô€‰Í½ÕÉ•}Ñ½Á¥}É•Ù¥•Üˆ…¹Í…Ù••±Í”íô(€€€€€€€€€€€€¤(€€€€€€€€€€€½ÕĞ€ô}É•½Ù•É}µ¥ÍÍ¥¹}Ñ½Á¥Í}…™Ñ•É}¡Õµ…¹}‘¥É•Ñ¥½¸ (€€€€€€€€€€€€€€€½ÕĞ°(€€€€€€€€€€€€€€€µ•Ñ„õµ•Ñ„°(€€€€€€€€€€€€€€€Í½ÕÉ•}Ñ½Á¥}•á•ÉÁÑÌõÍ½ÕÉ•}Ñ½Á¥}•á•ÉÁÑÌ°(€€€€€€€€€€€€€€€¡•­Á½¥¹Ñ}…±±‰…¬õ¡•­Á½¥¹Ñ}…±±‰…¬°(€€€€€€€€€€€€€€€É•½Ù•Éå}ÍÑ…Ñ”õÉ•½Ù•Éå}ÍÑ…Ñ”°(€€€€€€€€€€€€€€€Í­•±•Ñ½¹}µ•Ñ¡½‘}É½İ}Í¹…ÁÍ¡½ĞõÍ­•±•Ñ½¹}µ•Ñ¡½‘}É½İ}Í¹…ÁÍ¡½Ğ°(€€€€€€€€€€€€¤(€€€€€€€€€€€½ÕĞ€ô}É•½É‘•É}É•½É‘Í}‰å}Í½ÕÉ•}Ñ½Á¥Ì¡½ÕĞ°¡•…‘¥¹Ì¤(€€€€€€€€€€€½ÕĞ€ô}É•ÍÑ½É•}µ•Ñ¡½‘}…¹¡½É}É½İÌ (€€€€€€€€€€€€€€€½ÕĞ°Í­•±•Ñ½¹}µ•Ñ¡½‘}É½İ}Í¹…ÁÍ¡½Ğ¤(€€€€€€€€€€€½ÕĞ€ô}•¹™½É•}µ•Ñ¡½‘}…¹¡½É}Ñ½Á¥Ì¡½ÕĞ°µ•Ñ¡½‘}…¹¡½ÉÌ¤(€€€€€€€€€€€½ÕĞ€ô}…¹½¹¥…±¥é•}µ•Ñ¡½‘}…¹¡½É}Ñ…Ì (€€€€€€€€€€€€€€€½ÕĞ°µ•Ñ¡½‘}…¹¡½ÉÌ°¡Õ¹­}Ñ•áĞõµµ‘}Ñ•áĞ°µ•Ñ„õµ•Ñ„¤(€€€€€€€€€€€½ÕĞ€ô}½¹Í½±¥‘…Ñ•}Ñ…Í­}É½Õ¹‘•‘}™É…µ•¹ÑÍ}Ù¥…}…Á¤ (€€€€€€€€€€€€€€€½ÕĞ°µ•Ñ„õµ•Ñ„°(€€€€€€€€€€€€€€€Í½ÕÉ•}Ñ½Á¥}•á•ÉÁÑÌõÍ½ÕÉ•}Ñ½Á¥}•á•ÉÁÑÌ°(€€€€€€€€€€€€€€€µ•Ñ¡½‘}…¹¡½ÉÌõµ•Ñ¡½‘}…¹¡½ÉÌ¤(€€€€€€€€€€€½ÕĞ€ô}•¹™½É•}µ•Ñ¡½‘}…¹¡½É}Ñ½Á¥Ì¡½ÕĞ°µ•Ñ¡½‘}…¹¡½ÉÌ¤(€€€€€€€€€€€½ÕĞ€ô}…¹½¹¥…±¥é•}µ•Ñ¡½‘}…¹¡½É}Ñ…Ì (€€€€€€€€€€€€€€€½ÕĞ°µ•Ñ¡½‘}…¹¡½ÉÌ°¡Õ¹­}Ñ•áĞõµµ‘}Ñ•áĞ°µ•Ñ„õµ•Ñ„¤(€€€€€€€€€€€½ÕĞ€ô}É•½Ù•É}¡…ÁÑ•É}½Á•¹¥¹}½¹•ÁÑÍ}Ù¥…}…Á¤ (€€€€€€€€€€€€€€€½ÕĞ°µ•Ñ„õµ•Ñ„°Í•Ñ¥½¹ÌõÍ•Ñ¥½¹Ì°¡•…‘¥¹Ìõ¡•…‘¥¹Ì¤(€€€€€€€€€€€½ÕĞ€ô}É•½É‘•É}É•½É‘Í}‰å}Í½ÕÉ•}Ñ½Á¥Ì¡½ÕĞ°¡•…‘¥¹Ì¤(€€€€€€€€€€€}•µ¥Ñ}½¹•ÁÑ}¡•­Á½¥¹Ğ (€€€€€€€€€€€€€€€¡•­Á½¥¹Ñ}…±±‰…¬°(€€€€€€€€€€€€€€€€‰…¹½¹¥…±}Í­•±•Ñ½¸ˆ°(€€€€€€€€€€€€€€€É•½É‘Ìõ½ÕĞ°(€€€€€€€€€€€€€€€Í­•±•Ñ½¹}µ•Ñ¡½‘}É½İ}Í¹…ÁÍ¡½Ğõ}Í•É¥…±¥é•}µ•Ñ¡½‘}É½İ}Í¹…ÁÍ¡½Ğ (€€€€€€€€€€€€€€€€€€€Í­•±•Ñ½¹}µ•Ñ¡½‘}É½İ}Í¹…ÁÍ¡½Ğ¤°(€€€€€€€€€€€€¤((€€€€€€€¥˜Í…Ù•‘}½É‘•È€ğ}¡•­Á½¥¹Ñ}½É‘•È ‰‘•ÍÉ¥ÁÑ¥½¹}µ•Ñ¡½‘}Í¹…ÁÍ¡½Ğˆ¤è(€€€€€€€€€€€€Œ•ÍÉ¥ÁÑ¥½¸…ÕÑ¡½É¥¹œ‰•±½¹ÌÑ¼M•ÑÑ±”ÌÍ¥¹±”½¹Ñ•¹ĞÁ…ÍÌ(€€€€€€€€€€€€Œ€¡‘•ÍÉ¥ÁÑ¥½¸°µ…ÍÑ•Éä°…¹±•…É¹•È…¹…±åÍ¥Ì¥¸½¹”‘•¥Í¥½¸¤ì(€€€€€€€€€€€€ŒÑ¡”É•Ñ¥É•‘•‘¥…Ñ•ÁÉ”´àÄ”•ÍÉ¥ÁÑ¥½¸Á…ÍÌİ…ÌÉ•‘Õ¹‘…¹Ğ(€€€€€€€€€€€€ŒA$±½…Õ¹‘•È½¹ÕÉÉ•¹ĞÉ•…Ñ½ÈÉÕ¹Ì¸(€€€€€€€€€€€½ÕĞ€ô}•¹ÍÕÉ•}µ•Ñ¡½‘}İ½É­•‘}•á…µÁ±•Í}Ù¥…}…Á¤ (€€€€€€€€€€€€€€€½ÕĞ°…¹¡½ÉÌõµ•Ñ¡½‘}…¹¡½ÉÌ°µ•Ñ„õµ•Ñ„¤(€€€€€€€€€€€½ÕĞ€ô}•¹ÍÕÉ•}µ…ÍÑ•Éå}±¥¹•Í}Ù¥…}…Á¤¡½ÕĞ°µ•Ñ„õµ•Ñ„¤(€€€€€€€€€€€½ÕĞ€ô}É•ÍÑ½É•}µ•Ñ¡½‘}…¹¡½É}É½İÌ (€€€€€€€€€€€€€€€½ÕĞ°Í­•±•Ñ½¹}µ•Ñ¡½‘}É½İ}Í¹…ÁÍ¡½Ğ¤(€€€€€€€€€€€½ÕĞ€ô}•¹™½É•}µ•Ñ¡½‘}…¹¡½É}Ñ½Á¥Ì¡½ÕĞ°µ•Ñ¡½‘}…¹¡½ÉÌ¤(€€€€€€€€€€€µ•Ñ¡½‘}É½İ}Í¹…ÁÍ¡½Ğ€ô}Í¹…ÁÍ¡½Ñ}µ•Ñ¡½‘}…¹¡½É}É½İÌ (€€€€€€€€€€€€€€€½ÕĞ°µ•Ñ¡½‘}…¹¡½ÉÌ¤(€€€€€€€€€€€Õ¹Í¹…ÁÍ¡½ÑÑ•‘}…¹¡½ÉÌ€ôl(€€€€€€€€€€€€€€€…¹¡½È™½È…¹¡½È¥¸µ•Ñ¡½‘}…¹¡½ÉÌ(€€€€€€€€€€€€€€€¥˜€ (€€€€€€€€€€€€€€€€€€€ÍÑÈ¡…¹¡½È¹•Ğ ‰…¹¡½É}¥ˆ¤½È€ˆˆ¤¹ÕÁÁ•È ¤°(€€€€€€€€€€€€€€€€€€€}Ñ½Á¥}½µÁ…É¥Í½¹}­•ä¡…¹¡½È¹•Ğ ‰Ñ½Á¥}¡¥¹Ğˆ°€ˆˆ¤¤°(€€€€€€€€€€€€€€€€¤¹½Ğ¥¸µ•Ñ¡½‘}É½İ}Í¹…ÁÍ¡½Ğ(€€€€€€€€€€€t(€€€€€€€€€€€¥˜Õ¹Í¹…ÁÍ¡½ÑÑ•‘}…¹¡½ÉÌè(€€€€€€€€€€€€€€€É…¥Í”IÕ¹Ñ¥µ•ÉÉ½È (€€€€€€€€€€€€€€€€€€€€‰Á½ÍĞµ‘•ÍÉ¥ÁÑ¥½¸µ•Ñ¡½µÉ½ÜÉ•ÍÑ½É…Ñ¥½¸½Õ±¹½Ğ€ˆ(€€€€€€€€€€€€€€€€€€€€‰Í¹…ÁÍ¡½Ğµ…¹‘…Ñ½Éä™Õ±°µ¡…ÁÑ•È…¹¡½ÉÌè€ˆ(€€€€€€€€€€€€€€€€€€€€¬€ˆ°€ˆ¹©½¥¸ (€€€€€€€€€€€€€€€€€€€€€€€…¹¡½Él‰…¹¡½É}¥‰t™½È…¹¡½È¥¸Õ¹Í¹…ÁÍ¡½ÑÑ•‘}…¹¡½ÉÌ¤(€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€¥˜µ•Ñ¡½‘}É½İ}Í¹…ÁÍ¡½Ğè(€€€€€€€€€€€€€€€Í¹…ÁÍ¡½ÑÑ•‘}É½İÌ€ôì(€€€€€€€€€€€€€€€€€€€€ (€€€€€€€€€€€€€€€€€€€€€€€}Ñ½Á¥}½µÁ…É¥Í½¹}­•ä¡É½Ü¹•Ğ ‰Ñ½Á¥Œˆ°€ˆˆ¤¤°(€€€€€€€€€€€€€€€€€€€€€€€‰¤¹¹½Éµ…±¥é•}ÅÕ•ÍÑ¥½¹}Ñ•áĞ (€€€€€€€€€€€€€€€€€€€€€€€€€€€É½Ü¹•Ğ ‰½¹•ÁÑ}Ñ¥Ñ±”ˆ°€ˆˆ¤¤°(€€€€€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€€€€€™½ÈÉ½Ü¥¸µ•Ñ¡½‘}É½İ}Í¹…ÁÍ¡½Ğ¹Ù…±Õ•Ì ¤(€€€€€€€€€€€€€€€ô(€€€€€€€€€€€€€€€ÁÉ½É•ÍÌ¹±½œ (€€€€€€€€€€€€€€€€€€€˜‰M¹…ÁÍ¡½ÑÑ•í±•¸¡Í¹…ÁÍ¡½ÑÑ•‘}É½İÌ¥ôÉ•™¥¹•µ•Ñ¡½€ˆ(€€€€€€€€€€€€€€€€€€€˜‰É½Ü¡Ì¤½Ù•É¥¹œí±•¸¡µ•Ñ¡½‘}É½İ}Í¹…ÁÍ¡½Ğ¥ôµ…¹‘…Ñ½Éä€ˆ(€€€€€€€€€€€€€€€€€€€€‰…¹¡½È¡Ì¤¸ˆ¤(€€€€€€€€€€€ÁÉ½É•ÍÌ¹Í•Ñ}ÁÉ½É•ÍÌ (€€€€€€€€€€€€€€€€À¸ÔÔ°±…‰•°ô‰½¹•ÁĞ•áÑÉ…Ñ¥½¸ƒŠP‘•ÍÉ¥ÁÑ¥½¹Ì½µÁ±•Ñ”ˆ¤(€€€€€€€€€€€}•µ¥Ñ}½¹•ÁÑ}¡•­Á½¥¹Ğ (€€€€€€€€€€€€€€€¡•­Á½¥¹Ñ}…±±‰…¬°(€€€€€€€€€€€€€€€€‰‘•ÍÉ¥ÁÑ¥½¹}µ•Ñ¡½‘}Í¹…ÁÍ¡½Ğˆ°(€€€€€€€€€€€€€€€É•½É‘Ìõ½ÕĞ°(€€€€€€€€€€€€€€€µ•Ñ¡½‘}É½İ}Í¹…ÁÍ¡½Ğõ}Í•É¥…±¥é•}µ•Ñ¡½‘}É½İ}Í¹…ÁÍ¡½Ğ (€€€€€€€€€€€€€€€€€€€µ•Ñ¡½‘}É½İ}Í¹…ÁÍ¡½Ğ¤°(€€€€€€€€€€€€¤((€€€€€€€¥˜Í…Ù•‘}½É‘•È€ğ}¡•­Á½¥¹Ñ}½É‘•È ‰ÅÕ•ÍÑ¥½¹}¥¹Ù•¹Ñ½Éäˆ¤è(€€€€€€€€€€€ÁÉ½É•ÍÌ¹ÍÑ•À (€€€€€€€€€€€€€€€€‰½¹•ÁĞ•áÑÉ…Ñ¥½¸ƒŠP¥¹Ù•¹Ñ½Éå¥¹œÅÕ•ÍÑ¥½¹Ì…¹İ½É­••á…µÁ±•Ìˆ°(€€€€€€€€€€€€€€€Ù…±Õ”ôÀ¸Ôà°(€€€€€€€€€€€€¤(€€€€€€€€€€€¥˜•…É±å}¥¹Ù•¹Ñ½Éä¥Ì¹½Ğ9½¹”è(€€€€€€€€€€€€€€€€ŒQ¡”©½¥¸€¡DÄäQÉ…¬¤èÑ¡”Í½ÕÉ”µ½¹±ä¡…±˜İ…Ì(€€€€€€€€€€€€€€€€Œ•áÑÉ…Ñ•…±½¹Í¥‘”Ñ¡”½¹•ÁĞÁ…ÍÍ•Ìì½¹±äÑ½Á¥Œ(€€€€€€€€€€€€€€€€Œ…ÍÍ¥¹µ•¹Ğ…¹…‘©Õ‘¥…Ñ¥½¸É•µ…¥¸°¥¸Ñ¡”•á…Ğ(€€€€€€€€€€€€€€€€ŒÍ•ÅÕ•¹Ñ¥…°½É‘•È¸ÑÉ…¬™…¥±ÕÉ”ÍÕÉ™…•Ì¡•É”İ¥Ñ (€€€€€€€€€€€€€€€€ŒÑ¡”Í…µ”•á•ÁÑ¥½¹ÌÑ¡”¥¹±¥¹”Á…Ñ É…¥Í•Ì¸(€€€€€€€€€€€€€€€ÁÉ•}©½¥¹•°ÁÉ•}…¹¡½ÉÌ€ô•…É±å}¥¹Ù•¹Ñ½Éä¹©½¥¸ ¤(€€€€€€€€€€€€€€€ÁÉ½É•ÍÌ¹±½œ (€€€€€€€€€€€€€€€€€€€€‰)½¥¹¥¹œÑ¡”•…É±äµÑÉ…¬¥¹Ù•¹Ñ½Éä€¡•áÑÉ…Ñ•€ˆ(€€€€€€€€€€€€€€€€€€€€‰…±½¹Í¥‘”Ñ¡”½¹•ÁĞÁ…ÍÍ•Ì¤¸ˆ(€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€ÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½Éä€ô}™¥¹¥Í¡}¥¹Ù•¹Ñ½Éå}İ¥Ñ¡}Ñ½Á¥Ì (€€€€€€€€€€€€€€€€€€€ÁÉ•}©½¥¹•°ÁÉ•}…¹¡½ÉÌ°(€€€€€€€€€€€€€€€€€€€µ•Ñ„õµ•Ñ„°Í•Ñ¥½¹ÌõÍ•Ñ¥½¹Ì°É•½É‘Ìõ½ÕĞ°(€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€•±Í”è(€€€€€€€€€€€€€€€ÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½Éä€ô€ (€€€€€€€€€€€€€€€€€€€}•áÑÉ…Ñ}ÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½Éå}Ù¥…}…Á¤ (€€€€€€€€€€€€€€€€€€€€€€€µ•Ñ„õµ•Ñ„°Í•Ñ¥½¹ÌõÍ•Ñ¥½¹Ì°É•½É‘Ìõ½ÕĞ¤(€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€ÁÉ½É•ÍÌ¹Í•Ñ}ÁÉ½É•ÍÌ (€€€€€€€€€€€€€€€€À¸ÜÀ°±…‰•°ô‰½¹•ÁĞ•áÑÉ…Ñ¥½¸ƒŠPÅÕ•ÍÑ¥½¸¥¹Ù•¹Ñ½Éä½µÁ±•Ñ”ˆ¤(€€€€€€€€€€€}•µ¥Ñ}½¹•ÁÑ}¡•­Á½¥¹Ğ (€€€€€€€€€€€€€€€¡•­Á½¥¹Ñ}…±±‰…¬°(€€€€€€€€€€€€€€€€‰ÅÕ•ÍÑ¥½¹}¥¹Ù•¹Ñ½Éäˆ°(€€€€€€€€€€€€€€€É•½É‘Ìõ½ÕĞ°(€€€€€€€€€€€€€€€ÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½ÉäõÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½Éä°(€€€€€€€€€€€€€€€µ•Ñ¡½‘}É½İ}Í¹…ÁÍ¡½Ğõ}Í•É¥…±¥é•}µ•Ñ¡½‘}É½İ}Í¹…ÁÍ¡½Ğ (€€€€€€€€€€€€€€€€€€€µ•Ñ¡½‘}É½İ}Í¹…ÁÍ¡½Ğ¤°(€€€€€€€€€€€€¤(€€€™¥¹…±±äè(€€€€€€€¥˜•…É±å}¥¹Ù•¹Ñ½Éä¥Ì¹½Ğ9½¹”è(€€€€€€€€€€€€Œ!…±Ğµ‰½Ñ €¡DÄä¤è„¡Õµ…¸…Ñ”½È…¹äÉ…¥Í”‰•Ñİ••¸Ñ¡”(€€€€€€€€€€€€Œ™½É¬…¹Ñ¡”©½¥¸ÍÑ½ÁÌÑ¡”ÑÉ…¬‰•™½É”¥ÑÌ¹•áĞ¡Õ¹¬ì(€€€€€€€€€€€€Œ…™Ñ•È„±•…¸©½¥¸Ñ¡¥Ì¥Ì…¸¥‘•µÁ½Ñ•¹Ğ¹¼µ½À¸(€€€€€€€€€€€•…É±å}¥¹Ù•¹Ñ½Éä¹¡…±Ğ ¤((€€€É•Ù¥•Üè‘¥Ğ€ôíô(€€€¥˜Í…Ù•‘}½É‘•È€ğ}¡•­Á½¥¹Ñ}½É‘•È¡}QeA}Qa=9=5e}!-A=%9Q}MQ¤è(€€€€€€€ÁÉ½É•ÍÌ¹ÍÑ•À (€€€€€€€€€€€€‰½¹•ÁĞ•áÑÉ…Ñ¥½¸ƒŠPµ¥¹¥¹œÉ•ÕÍ…‰±”QåÁ•Ìˆ°Ù…±Õ”ôÀ¸ÜÈ¤(€€€€€€€µ¥¹•‘}ÑåÁ•Ì€ô}µ¥¹•}ÑåÁ•Í}™É½µ}¥¹Ù•¹Ñ½Éå}Ù¥…}…Á¤ (€€€€€€€€€€€µ•Ñ„õµ•Ñ„°¥¹Ù•¹Ñ½ÉäõÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½Éä¤(€€€€€€€É…İ}ÑåÁ•}½Õ¹Ğ€ô±•¸ ¡µ¥¹•‘}ÑåÁ•Ì½Èíô¤¹•Ğ ‰ÑåÁ•Ìˆ¤½Èmt¤(€€€€€€€µ¥¹•‘}ÑåÁ•Ì€ô}½¹Í½±¥‘…Ñ•}Í•µ…¹Ñ¥}ÑåÁ•Í}Ù¥…}…Á¤ (€€€€€€€€€€€µ¥¹•‘}ÑåÁ•Ì°¥¹Ù•¹Ñ½ÉäõÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½Éä°µ•Ñ„õµ•Ñ„¤(€€€€€€€½¹Í½±¥‘…Ñ•‘}ÑåÁ•}½Õ¹Ğ€ô±•¸ (€€€€€€€€€€€€¡µ¥¹•‘}ÑåÁ•Ì½Èíô¤¹•Ğ ‰ÑåÁ•Ìˆ¤½Èmt¤(€€€€€€€É•Ù¥•Ü€ôÑåÁ•}É…¹Õ±…É¥Ñå}‘•¥Í¥½¸¹‰Õ¥±‘}É•Ù¥•Ü (€€€€€€€€€€€É…İ}ÑåÁ•}½Õ¹ĞõÉ…İ}ÑåÁ•}½Õ¹Ğ°(€€€€€€€€€€€½¹Í½±¥‘…Ñ•‘}ÑåÁ•}½Õ¹Ğõ½¹Í½±¥‘…Ñ•‘}ÑåÁ•}½Õ¹Ğ°(€€€€€€€€€€€¥¹Ù•¹Ñ½Éå}½Õ¹Ğõ±•¸ (€€€€€€€€€€€€€€€€¡ÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½Éä½Èíô¤¹•Ğ ‰¥Ñ•µÌˆ¤½Èmt¤°(€€€€€€€€€€€ÍÕ™™¥¥•¹å}…‘‘•‘}½¹•ÁÑÌôÀ°(€€€€€€€€€€€ÍÕ™™¥¥•¹å}…Õ‘¥Ñ}½µÁ±•Ñ”õ…±Í”°(€€€€€€€€¤(€€€€€€€€ŒQ¡”™É…µ•¹Ñ…Ñ¥½¸ÅÕ•ÍÑ¥½¸¥ÌÑ¡”µ½‘•°Ì©Õ‘µ•¹Ğ€£
œÌÁÕÉ”¤°…¹(€€€€€€€€Œ¥ÑÌÙ•É‘¥Ğ¥Ì½µÁÕÑ•¡•É”ƒŠP‰•™½É”Ñ¡”‘ÕÉ…‰±”¡•­Á½¥¹ĞƒŠPÍ¼„(€€€€€€€€ŒÁ…ÕÍ”½É•ÍÕµ”É•Á±…åÌÑ¡”Í…µ”É•½É‘•Ù•É‘¥Ğ¥¹ÍÑ•…½˜É•ÍÁ•¹‘¥¹œ(€€€€€€€€ŒÑ¡”…ÕÑ¡½È½É¥Ñ¥ŒÁ…¥È¸(€€€€€€€É•Ù¥•İl‰™É…µ•¹Ñ…Ñ¥½¹}Ù•É‘¥Ğ‰t€ô€ (€€€€€€€€€€€ÑåÁ•}É…¹Õ±…É¥Ñå}‘•¥Í¥½¸¹™É…µ•¹Ñ…Ñ¥½¹}Ù•É‘¥Ğ (€€€€€€€€€€€€€€€É•Ù¥•Ü°(€€€€€€€€€€€€€€€µ¥¹•‘}ÑåÁ•Ìõµ¥¹•‘}ÑåÁ•Ì°(€€€€€€€€€€€€€€€¥¹Ù•¹Ñ½ÉäõÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½Éä°(€€€€€€€€€€€€€€€…Á¥}…±°õ}½Á•¹…¥}©Í½¸°(€€€€€€€€€€€€¤(€€€€€€€€¤(€€€€€€€µ¥¹•‘}ÑåÁ•Íl‰}É…¹Õ±…É¥Ñå}É•Ù¥•Ü‰t€ô½Áä¹‘••Á½Áä¡É•Ù¥•Ü¤(€€€€€€€ÁÉ½É•ÍÌ¹Í•Ñ}ÁÉ½É•ÍÌ (€€€€€€€€€€€€À¸ÜØ°(€€€€€€€€€€€±…‰•°ô‰½¹•ÁĞ•áÑÉ…Ñ¥½¸ƒŠPQåÁ”Ñ…á½¹½µäÉ•…‘ä™½ÈÉ•Ù¥•Üˆ°(€€€€€€€€¤(€€€€€€€€ŒQ¡¥Ì¡•­Á½¥¹Ğ¥Ì¥¹Ñ•¹Ñ¥½¹…±±ä‰•™½É”½¹•ÁĞÍÕ™™¥¥•¹ä°µ…ÍÑ•Éä°(€€€€€€€€Œ…¹Õ±µ¥¹…Ñ¥½¸…ÕÑ¡½É¥¹œ¸™É…µ•¹Ñ…Ñ¥½¸Á…ÕÍ”Ñ¡•É•™½É”¥¹ÕÉÌ¹½¹”(€€€€€€€€Œ½˜Ñ¡½Í”‘½İ¹ÍÑÉ•…´…±±Ì°…¹Ñ¡”…•ÁÑ•Ñ…á½¹½µä‰•½µ•ÌÑ¡•¥È(€€€€€€€€ŒÍ¥¹±”Í½ÕÉ”½˜ÑÉÕÑ ½¸•áÁ±¥¥ĞÉ•ÍÕµ”¸(€€€€€€€}•µ¥Ñ}½¹•ÁÑ}¡•­Á½¥¹Ğ (€€€€€€€€€€€¡•­Á½¥¹Ñ}…±±‰…¬°(€€€€€€€€€€€}QeA}Qa=9=5e}!-A=%9Q}MQ°(€€€€€€€€€€€É•½É‘Ìõ½ÕĞ°(€€€€€€€€€€€ÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½ÉäõÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½Éä°(€€€€€€€€€€€µ¥¹•‘}ÑåÁ•Ìõµ¥¹•‘}ÑåÁ•Ì°(€€€€€€€€€€€µ•Ñ¡½‘}É½İ}Í¹…ÁÍ¡½Ğõ}Í•É¥…±¥é•}µ•Ñ¡½‘}É½İ}Í¹…ÁÍ¡½Ğ (€€€€€€€€€€€€€€€µ•Ñ¡½‘}É½İ}Í¹…ÁÍ¡½Ğ¤°(€€€€€€€€¤((€€€¥˜Í…Ù•‘}½É‘•È€ğô}¡•­Á½¥¹Ñ}½É‘•È¡}=9AQ}!-A=%9Q}MQ¤è(€€€€€€€¥˜¹½ĞÉ•Ù¥•Üè(€€€€€€€€€€€É•Ù¥•Ü€ô½Áä¹‘••Á½Áä (€€€€€€€€€€€€€€€€¡µ¥¹•‘}ÑåÁ•Ì½Èíô¤¹•Ğ ‰}É…¹Õ±…É¥Ñå}É•Ù¥•Üˆ¤½Èíô¤(€€€€€€€¥˜¹½ĞÉ•Ù¥•Üè(€€€€€€€€€€€€Œ¡•­Á½¥¹ÑÌÉ•…Ñ•‰•™½É”Ñ¡¥Ì…Ñ”‘¥¹½ĞÁ•ÉÍ¥ÍĞÉ…ÜµÙÌ´(€€€€€€€€€€€€Œ½¹Í½±¥‘…Ñ•½Õ¹ÑÌ¸QÉ•…ĞÑ¡”Í…Ù•Ñ…á½¹½µä…ÌÑ¡”‰…Í•±¥¹”ì(€€€€€€€€€€€€ŒÑ¡”µ½‘•°™É…µ•¹Ñ…Ñ¥½¸…Õ‘¥Ğ‰•±½ÜÍÑ¥±°•ÑÌ½¹”¡Õµ…¸±½½¬¸(€€€€€€€€€€€€Œ€¡I•Ù¥•İÌÍ…Ù•‰ä½±‘•È…Ñ”Ù•ÉÍ¥½¹Ìµ…ä…ÉÉäÉ•Ñ¥É•É…Ñ¥¼(€€€€€€€€€€€€Œ­•åÌƒŠPÉ•…‘•ÉÌÕÍ”€¹•Ğ ¤°Í¼Ñ¡½Í”­•åÌ…É”¥¹•ÉĞ¸¤(€€€€€€€€€€€ÕÉÉ•¹Ñ}ÑåÁ•}½Õ¹Ğ€ô±•¸ (€€€€€€€€€€€€€€€€¡µ¥¹•‘}ÑåÁ•Ì½Èíô¤¹•Ğ ‰ÑåÁ•Ìˆ¤½Èmt¤(€€€€€€€€€€€É•Ù¥•Ü€ôÑåÁ•}É…¹Õ±…É¥Ñå}‘•¥Í¥½¸¹‰Õ¥±‘}É•Ù¥•Ü (€€€€€€€€€€€€€€€É…İ}ÑåÁ•}½Õ¹ĞõÕÉÉ•¹Ñ}ÑåÁ•}½Õ¹Ğ°(€€€€€€€€€€€€€€€½¹Í½±¥‘…Ñ•‘}ÑåÁ•}½Õ¹ĞõÕÉÉ•¹Ñ}ÑåÁ•}½Õ¹Ğ°(€€€€€€€€€€€€€€€¥¹Ù•¹Ñ½Éå}½Õ¹Ğõ±•¸ (€€€€€€€€€€€€€€€€€€€€¡ÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½Éä½Èíô¤¹•Ğ ‰¥Ñ•µÌˆ¤½Èmt¤°(€€€€€€€€€€€€€€€ÍÕ™™¥¥•¹å}…‘‘•‘}½¹•ÁÑÌôÀ°(€€€€€€€€€€€€€€€ÍÕ™™¥¥•¹å}…Õ‘¥Ñ}½µÁ±•Ñ”ô (€€€€€€€€€€€€€€€€€€€Í…Ù•‘}½É‘•È(€€€€€€€€€€€€€€€€€€€€øô}¡•­Á½¥¹Ñ}½É‘•È¡}=9AQ}!-A=%9Q}MQ¤(€€€€€€€€€€€€€€€€¤°(€€€€€€€€€€€€¤(€€€€€€€€€€€µ¥¹•‘}ÑåÁ•Íl‰}É…¹Õ±…É¥Ñå}É•Ù¥•Ü‰t€ô½Áä¹‘••Á½Áä¡É•Ù¥•Ü¤((€€€€€€€…ÁÁ±¥•€ôÉ•Ù¥•Ü¹•Ğ ‰¡Õµ…¹}É•Í½±ÕÑ¥½¸ˆ¤(€€€€€€€¥˜¥Í¥¹ÍÑ…¹”¡…ÁÁ±¥•°‘¥Ğ¤…¹…ÁÁ±¥•¹•Ğ ‰‘•¥Í¥½¹}¥ˆ¤è(€€€€€€€€€€€•áÁ•Ñ•‘}É•ÍÕ±Ñ}¡…Í €ô€ (€€€€€€€€€€€€€€€ÑåÁ•}É…¹Õ±…É¥Ñå}‘•¥Í¥½¸¹…ÁÁ±¥•‘}É•ÍÕ±Ñ}½¹Ñ•áÑ}¡…Í  (€€€€€€€€€€€€€€€€€€€É•Ù¥•ÜõÉ•Ù¥•Ü°(€€€€€€€€€€€€€€€€€€€¥¹Ù•¹Ñ½ÉäõÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½Éä°(€€€€€€€€€€€€€€€€€€€µ¥¹•‘}ÑåÁ•Ìõµ¥¹•‘}ÑåÁ•Ì°(€€€€€€€€€€€€€€€€€€€µ•Ñ„õµ•Ñ„°(€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€¤(€€€€€€€€€€€¥˜ÍÑÈ¡…ÁÁ±¥•¹•Ğ ‰É•ÍÕ±Ñ}½¹Ñ•áÑ}¡…Í ˆ¤½È€ˆˆ¤€„ô€ (€€€€€€€€€€€€€€€•áÁ•Ñ•‘}É•ÍÕ±Ñ}¡…Í (€€€€€€€€€€€€¤è(€€€€€€€€€€€€€€€ÁÉ½É•ÍÌ¹±½œ (€€€€€€€€€€€€€€€€€€€€‰M…Ù•QåÁ”µÉ…¹Õ±…É¥Ñä‘¥É•Ñ¥½¸¹¼±½¹•Èµ…Ñ¡•ÌÑ¡”€ˆ(€€€€€€€€€€€€€€€€€€€€‰ÕÉÉ•¹ĞÍ½ÕÉ”¥¹Ù•¹Ñ½Éä°Ñ…á½¹½µä°µ•Ñ…‘…Ñ„°½È€ˆ(€€€€€€€€€€€€€€€€€€€€‰½¹™¥‘•¹”Á½±¥äìÉ•ÅÕ¥É¥¹œ„™É•Í •á…Ğµ½¹Ñ•áĞ€ˆ(€€€€€€€€€€€€€€€€€€€€‰‘•¥Í¥½¸½¹±ä¥˜Ñ¡”…¹½µ…±äÍÑ¥±°•á¥ÍÑÌ¸ˆ°(€€€€€€€€€€€€€€€€€€€±•Ù•°ô‰İ…É¹¥¹œˆ°(€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€ÕÉÉ•¹Ñ}ÑåÁ•}½Õ¹Ğ€ô±•¸ (€€€€€€€€€€€€€€€€€€€€¡µ¥¹•‘}ÑåÁ•Ì½Èíô¤¹•Ğ ‰ÑåÁ•Ìˆ¤½Èmt¤(€€€€€€€€€€€€€€€É•Ù¥•Ü€ôÑåÁ•}É…¹Õ±…É¥Ñå}‘•¥Í¥½¸¹‰Õ¥±‘}É•Ù¥•Ü (€€€€€€€€€€€€€€€€€€€É…İ}ÑåÁ•}½Õ¹ĞõÕÉÉ•¹Ñ}ÑåÁ•}½Õ¹Ğ°(€€€€€€€€€€€€€€€€€€€½¹Í½±¥‘…Ñ•‘}ÑåÁ•}½Õ¹ĞõÕÉÉ•¹Ñ}ÑåÁ•}½Õ¹Ğ°(€€€€€€€€€€€€€€€€€€€¥¹Ù•¹Ñ½Éå}½Õ¹Ğõ±•¸ (€€€€€€€€€€€€€€€€€€€€€€€€¡ÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½Éä½Èíô¤¹•Ğ ‰¥Ñ•µÌˆ¤½Èmt¤°(€€€€€€€€€€€€€€€€€€€ÍÕ™™¥¥•¹å}…‘‘•‘}½¹•ÁÑÌõ¥¹Ğ (€€€€€€€€€€€€€€€€€€€€€€€É•Ù¥•Ü¹•Ğ ‰ÍÕ™™¥¥•¹å}…‘‘•‘}½¹•ÁÑÌˆ¤½È€À¤°(€€€€€€€€€€€€€€€€€€€ÍÕ™™¥¥•¹å}…Õ‘¥Ñ}½µÁ±•Ñ”õ‰½½° (€€€€€€€€€€€€€€€€€€€€€€€É•Ù¥•Ü¹•Ğ ‰ÍÕ™™¥¥•¹å}…Õ‘¥Ñ}½µÁ±•Ñ”ˆ°QÉÕ”¤¤°(€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€µ¥¹•‘}ÑåÁ•Íl‰}É…¹Õ±…É¥Ñå}É•Ù¥•Ü‰t€ô½Áä¹‘••Á½Áä¡É•Ù¥•Ü¤(€€€€€€€€€€€€€€€…ÁÁ±¥•€ô9½¹”(€€€€€€€¥˜¹½Ğ¥Í¥¹ÍÑ…¹”¡…ÁÁ±¥•°‘¥Ğ¤½È¹½Ğ…ÁÁ±¥•¹•Ğ ‰‘•¥Í¥½¹}¥ˆ¤è(€€€€€€€€€€€Á•¹‘¥¹}™½±±½İÕÀ€ôÉ•Ù¥•Ü¹•Ğ ‰Á•¹‘¥¹}™½±±½İÕÀˆ¤(€€€€€€€€€€€¥˜¹½Ğ¥Í¥¹ÍÑ…¹”¡Á•¹‘¥¹}™½±±½İÕÀ°‘¥Ğ¤è(€€€€€€€€€€€€€€€Á•¹‘¥¹}™½±±½İÕÀ€ôíô(€€€€€€€€€€€™É…µ•¹Ñ…Ñ¥½¸€ôÉ•Ù¥•Ü¹•Ğ ‰™É…µ•¹Ñ…Ñ¥½¹}Ù•É‘¥Ğˆ¤(€€€€€€€€€€€¥˜€ (€€€€€€€€€€€€€€€¹½ĞÍÑÈ¡Á•¹‘¥¹}™½±±½İÕÀ¹•Ğ ‰™…¥±ÕÉ”ˆ¤½È€ˆˆ¤¹ÍÑÉ¥À ¤(€€€€€€€€€€€€€€€…¹¹½Ğ€ (€€€€€€€€€€€€€€€€€€€¥Í¥¹ÍÑ…¹”¡™É…µ•¹Ñ…Ñ¥½¸°‘¥Ğ¤(€€€€€€€€€€€€€€€€€€€…¹€‰™É…µ•¹Ñ•ˆ¥¸™É…µ•¹Ñ…Ñ¥½¸(€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€¤è(€€€€€€€€€€€€€€€€ŒI•ÍÑ½É•½É•‰Õ¥±ĞÉ•Ù¥•İÌİ¥Ñ¡½ÕĞ„É•½É‘•Ù•É‘¥ĞÍÁ•¹(€€€€€€€€€€€€€€€€ŒÑ¡”…ÕÑ¡½È½É¥Ñ¥ŒÁ…¥È½¹”¡•É”¸Q¡”‘•¥Í¥½¸¥‘•¹Ñ¥Ñä(€€€€€€€€€€€€€€€€Œ‰¥¹‘Ì½¹±äÑ¡”ÍÑ…‰±”½Õ¹ÑÌ°Í¼„É”µÍÁ•¹ĞÙ•É‘¥Ğ…¹¹½Ğ(€€€€€€€€€€€€€€€€Œ½ÉÁ¡…¸„¡Õµ…¸…¹Íİ•È¸(€€€€€€€€€€€€€€€™É…µ•¹Ñ…Ñ¥½¸€ô€ (€€€€€€€€€€€€€€€€€€€ÑåÁ•}É…¹Õ±…É¥Ñå}‘•¥Í¥½¸¹™É…µ•¹Ñ…Ñ¥½¹}Ù•É‘¥Ğ (€€€€€€€€€€€€€€€€€€€€€€€É•Ù¥•Ü°(€€€€€€€€€€€€€€€€€€€€€€€µ¥¹•‘}ÑåÁ•Ìõµ¥¹•‘}ÑåÁ•Ì°(€€€€€€€€€€€€€€€€€€€€€€€¥¹Ù•¹Ñ½ÉäõÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½Éä°(€€€€€€€€€€€€€€€€€€€€€€€…Á¥}…±°õ}½Á•¹…¥}©Í½¸°(€€€€€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€É•Ù¥•İl‰™É…µ•¹Ñ…Ñ¥½¹}Ù•É‘¥Ğ‰t€ô½Áä¹‘••Á½Áä¡™É…µ•¹Ñ…Ñ¥½¸¤(€€€€€€€€€€€€€€€µ¥¹•‘}ÑåÁ•Íl‰}É…¹Õ±…É¥Ñå}É•Ù¥•Ü‰t€ô½Áä¹‘••Á½Áä¡É•Ù¥•Ü¤(€€€€€€€€€€€‘¥É•Ñ¥Ù”€ôÑåÁ•}É…¹Õ±…É¥Ñå}‘•¥Í¥½¸¹É•Í½±Ù•}½É}Á…ÕÍ” (€€€€€€€€€€€€€€€É•Ù¥•ÜõÉ•Ù¥•Ü°(€€€€€€€€€€€€€€€¥¹Ù•¹Ñ½ÉäõÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½Éä°(€€€€€€€€€€€€€€€µ¥¹•‘}ÑåÁ•Ìõµ¥¹•‘}ÑåÁ•Ì°(€€€€€€€€€€€€€€€µ•Ñ„õµ•Ñ„°(€€€€€€€€€€€€€€€ÁÉ¥½É}‘•¥Í¥½¹}¥õÍÑÈ (€€€€€€€€€€€€€€€€€€€Á•¹‘¥¹}™½±±½İÕÀ¹•Ğ ‰ÁÉ¥½É}‘•¥Í¥½¹}¥ˆ¤½È€ˆˆ¤°(€€€€€€€€€€€€€€€™…¥±ÕÉ”õÍÑÈ¡Á•¹‘¥¹}™½±±½İÕÀ¹•Ğ ‰™…¥±ÕÉ”ˆ¤½È€ˆˆ¤°(€€€€€€€€€€€€€€€Ù•É‘¥Ğô (€€€€€€€€€€€€€€€€€€€™É…µ•¹Ñ…Ñ¥½¸(€€€€€€€€€€€€€€€€€€€¥˜¥Í¥¹ÍÑ…¹”¡™É…µ•¹Ñ…Ñ¥½¸°‘¥Ğ¤•±Í”9½¹”(€€€€€€€€€€€€€€€€¤°(€€€€€€€€€€€€¤(€€€€€€€€€€€…Ñ¥½¸€ôÍÑÈ¡‘¥É•Ñ¥Ù”¹•Ğ ‰…Ñ¥½¸ˆ¤½È€‰½¹Ñ¥¹Õ”ˆ¤(€€€€€€€€€€€É•Í½±ÕÑ¥½¹}…Õ‘¥Ğè‘¥Ğ€ôíô(€€€€€€€€€€€¥˜…Ñ¥½¸€ôô€‰½¹Í½±¥‘…Ñ”ˆè(€€€€€€€€€€€€€€€‘•¥Í¥½¹}¥€ôÍÑÈ¡‘¥É•Ñ¥Ù”¹•Ğ ‰‘•¥Í¥½¹}¥ˆ¤½È€ˆˆ¤(€€€€€€€€€€€€€€€½¹Ñ•áÑ}¡…Í €ôÍÑÈ¡‘¥É•Ñ¥Ù”¹•Ğ ‰½¹Ñ•áÑ}¡…Í ˆ¤½È€ˆˆ¤(€€€€€€€€€€€€€€€€Œ½µµ¥Ğ…ÕÑ¡½É¥é…Ñ¥½¸½¹ÍÕµÁÑ¥½¸‰•™½É”Ñ¡”ÁÉ½Á½Í…°½É¥Ñ¥Œ(€€€€€€€€€€€€€€€€ŒÁ…¥È¸É…Í …™Ñ•È‘¥ÍÁ…Ñ µÕÍĞ¹•Ù•È±•…Ù”Ñ¡”½±…¹Íİ•È(€€€€€€€€€€€€€€€€ŒÉ•Á±…å…‰±”½¸É•ÍÕµ”¸(€€€€€€€€€€€€€€€É•Ù¥•İl‰±…ÍÑ}…ÑÑ•µÁĞ‰t€ôì(€€€€€€€€€€€€€€€€€€€€‰‘•¥Í¥½¹}¥ˆè‘•¥Í¥½¹}¥°(€€€€€€€€€€€€€€€€€€€€‰½¹Ñ•áÑ}¡…Í ˆè½¹Ñ•áÑ}¡…Í °(€€€€€€€€€€€€€€€€€€€€‰ÍÑ…ÑÕÌˆè€‰É•ÅÕ•ÍÑ}ÍÑ…ÉÑ•ˆ°(€€€€€€€€€€€€€€€ô(€€€€€€€€€€€€€€€É•Ù¥•İl‰Á•¹‘¥¹}™½±±½İÕÀ‰t€ôì(€€€€€€€€€€€€€€€€€€€€‰ÁÉ¥½É}‘•¥Í¥½¹}¥ˆè‘•¥Í¥½¹}¥°(€€€€€€€€€€€€€€€€€€€€‰™…¥±ÕÉ”ˆè€ (€€€€€€€€€€€€€€€€€€€€€€€€‰Q¡”…ÕÑ¡½É¥é•QåÁ”ÁÉ½Á½Í…°½É¥Ñ¥ŒÁ…¥Èİ…Ì€ˆ(€€€€€€€€€€€€€€€€€€€€€€€€‰‘¥ÍÁ…Ñ¡•°‰ÕĞ¹¼•ÉÑ¥™¥•É•ÍÕ±Ğ¡•­Á½¥¹Ğİ…Ì€ˆ(€€€€€€€€€€€€€€€€€€€€€€€€‰Í…Ù•¸%ÑÌ½ÕÑ½µ”¥ÌÕ¹­¹½İ¸ì•¥Ìİ¥±°¹½ĞÉ•Á±…ä€ˆ(€€€€€€€€€€€€€€€€€€€€€€€€‰¥Ğ…ÕÑ½µ…Ñ¥…±±ä¸ˆ(€€€€€€€€€€€€€€€€€€€€¤°(€€€€€€€€€€€€€€€ô(€€€€€€€€€€€€€€€µ¥¹•‘}ÑåÁ•Íl‰}É…¹Õ±…É¥Ñå}É•Ù¥•Ü‰t€ô½Áä¹‘••Á½Áä¡É•Ù¥•Ü¤(€€€€€€€€€€€€€€€}•µ¥Ñ}½¹•ÁÑ}¡•­Á½¥¹Ğ (€€€€€€€€€€€€€€€€€€€¡•­Á½¥¹Ñ}…±±‰…¬°(€€€€€€€€€€€€€€€€€€€€ (€€€€€€€€€€€€€€€€€€€€€€€}=9AQ}!-A=%9Q}MQ(€€€€€€€€€€€€€€€€€€€€€€€¥˜Í…Ù•‘}½É‘•È€øô}¡•­Á½¥¹Ñ}½É‘•È (€€€€€€€€€€€€€€€€€€€€€€€€€€€}=9AQ}!-A=%9Q}MQ¤(€€€€€€€€€€€€€€€€€€€€€€€•±Í”}QeA}Qa=9=5e}!-A=%9Q}MQ(€€€€€€€€€€€€€€€€€€€€¤°(€€€€€€€€€€€€€€€€€€€É•½É‘Ìõ½ÕĞ°(€€€€€€€€€€€€€€€€€€€ÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½ÉäõÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½Éä°(€€€€€€€€€€€€€€€€€€€µ¥¹•‘}ÑåÁ•Ìõµ¥¹•‘}ÑåÁ•Ì°(€€€€€€€€€€€€€€€€€€€µ•Ñ¡½‘}É½İ}Í¹…ÁÍ¡½Ğõ}Í•É¥…±¥é•}µ•Ñ¡½‘}É½İ}Í¹…ÁÍ¡½Ğ (€€€€€€€€€€€€€€€€€€€€€€€µ•Ñ¡½‘}É½İ}Í¹…ÁÍ¡½Ğ¤°(€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€ÑÉäè(€€€€€€€€€€€€€€€€€€€½¹Í½±¥‘…Ñ•°™…¥±ÕÉ”°É•Í½±ÕÑ¥½¹}…Õ‘¥Ğ€ô€ (€€€€€€€€€€€€€€€€€€€€€€€}¡Õµ…¹}‘¥É•Ñ•‘}ÑåÁ•}½¹Í½±¥‘…Ñ¥½¹}Ù¥…}…Á¤ (€€€€€€€€€€€€€€€€€€€€€€€€€€€µ¥¹•‘}ÑåÁ•Ì°(€€€€€€€€€€€€€€€€€€€€€€€€€€€¥¹Ù•¹Ñ½ÉäõÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½Éä°(€€€€€€€€€€€€€€€€€€€€€€€€€€€µ•Ñ„õµ•Ñ„°(€€€€€€€€€€€€€€€€€€€€€€€€€€€¥¹ÍÑÉÕÑ¥½¸õÍÑÈ (€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€‘¥É•Ñ¥Ù”¹•Ğ ‰¥¹ÍÑÉÕÑ¥½¸ˆ¤½È€ˆˆ¤°(€€€€€€€€€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€•á•ÁĞá•ÁÑ¥½¸…Ì•áŒè€€Œ¹½Å„è	1ÀÀÄ€´½¹ÍÕµ”…¹É”µÁ…ÕÍ”(€€€€€€€€€€€€€€€€€€€½¹Í½±¥‘…Ñ•€ô9½¹”(€€€€€€€€€€€€€€€€€€€™…¥±ÕÉ”€ô€ (€€€€€€€€€€€€€€€€€€€€€€€€‰Q¡”½¹”…ÕÑ¡½É¥é•QåÁ”ÁÉ½Á½Í…°½É¥Ñ¥ŒÁ…¥È½Õ±€ˆ(€€€€€€€€€€€€€€€€€€€€€€€€‰¹½ĞÁÉ½‘Õ”„ÕÍ…‰±”•ÉÑ¥™¥•É•ÍÕ±Ğ€ˆ(€€€€€€€€€€€€€€€€€€€€€€€˜ˆ¡íÑåÁ”¡•áŒ¤¹}}¹…µ•}}ô¤èí•áôˆ(€€€€€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€€€€€É•Í½±ÕÑ¥½¹}…Õ‘¥Ğ€ôì(€€€€€€€€€€€€€€€€€€€€€€€€‰ÁÉ½Ù¥‘•É}™…¥±ÕÉ”ˆèÑåÁ”¡•áŒ¤¹}}¹…µ•}|°(€€€€€€€€€€€€€€€€€€€ô(€€€€€€€€€€€€€€€¥˜½¹Í½±¥‘…Ñ•¥Ì9½¹”è(€€€€€€€€€€€€€€€€€€€™…¥±ÕÉ”€ôÍÑÈ¡™…¥±ÕÉ”½È€ˆˆ¥lèÑ|ÀÀÁt(€€€€€€€€€€€€€€€€€€€É•Ù¥•İl‰Á•¹‘¥¹}™½±±½İÕÀ‰t€ôì(€€€€€€€€€€€€€€€€€€€€€€€€‰ÁÉ¥½É}‘•¥Í¥½¹}¥ˆèÍÑÈ (€€€€€€€€€€€€€€€€€€€€€€€€€€€‘¥É•Ñ¥Ù”¹•Ğ ‰‘•¥Í¥½¹}¥ˆ¤½È€ˆˆ¤°(€€€€€€€€€€€€€€€€€€€€€€€€‰™…¥±ÕÉ”ˆè™…¥±ÕÉ”°(€€€€€€€€€€€€€€€€€€€ô(€€€€€€€€€€€€€€€€€€€É•Ù¥•İl‰±…ÍÑ}…ÑÑ•µÁĞ‰t€ôì(€€€€€€€€€€€€€€€€€€€€€€€€‰‘•¥Í¥½¹}¥ˆè‘•¥Í¥½¹}¥°(€€€€€€€€€€€€€€€€€€€€€€€€‰½¹Ñ•áÑ}¡…Í ˆè½¹Ñ•áÑ}¡…Í °(€€€€€€€€€€€€€€€€€€€€€€€€‰ÍÑ…ÑÕÌˆè€‰™…¥±•ˆ°(€€€€€€€€€€€€€€€€€€€€€€€€‰…Õ‘¥Ğˆè½Áä¹‘••Á½Áä¡É•Í½±ÕÑ¥½¹}…Õ‘¥Ğ¤°(€€€€€€€€€€€€€€€€€€€ô(€€€€€€€€€€€€€€€€€€€µ¥¹•‘}ÑåÁ•Íl‰}É…¹Õ±…É¥Ñå}É•Ù¥•Ü‰t€ô½Áä¹‘••Á½Áä (€€€€€€€€€€€€€€€€€€€€€€€É•Ù¥•Ü¤(€€€€€€€€€€€€€€€€€€€}•µ¥Ñ}½¹•ÁÑ}¡•­Á½¥¹Ğ (€€€€€€€€€€€€€€€€€€€€€€€¡•­Á½¥¹Ñ}…±±‰…¬°(€€€€€€€€€€€€€€€€€€€€€€€€ (€€€€€€€€€€€€€€€€€€€€€€€€€€€}=9AQ}!-A=%9Q}MQ(€€€€€€€€€€€€€€€€€€€€€€€€€€€¥˜Í…Ù•‘}½É‘•È€øô}¡•­Á½¥¹Ñ}½É‘•È (€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€}=9AQ}!-A=%9Q}MQ¤(€€€€€€€€€€€€€€€€€€€€€€€€€€€•±Í”}QeA}Qa=9=5e}!-A=%9Q}MQ(€€€€€€€€€€€€€€€€€€€€€€€€¤°(€€€€€€€€€€€€€€€€€€€€€€€É•½É‘Ìõ½ÕĞ°(€€€€€€€€€€€€€€€€€€€€€€€ÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½ÉäõÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½Éä°(€€€€€€€€€€€€€€€€€€€€€€€µ¥¹•‘}ÑåÁ•Ìõµ¥¹•‘}ÑåÁ•Ì°(€€€€€€€€€€€€€€€€€€€€€€€µ•Ñ¡½‘}É½İ}Í¹…ÁÍ¡½Ğõ}Í•É¥…±¥é•}µ•Ñ¡½‘}É½İ}Í¹…ÁÍ¡½Ğ (€€€€€€€€€€€€€€€€€€€€€€€€€€€µ•Ñ¡½‘}É½İ}Í¹…ÁÍ¡½Ğ¤°(€€€€€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€€€€€€ŒQ¡”™¥ÉÍĞ…±°…±İ…åÌÉ…¥Í•Ì„¹•Ü‘ÕÉ…‰±”‘•¥Í¥½¸¸(€€€€€€€€€€€€€€€€€€€ÑåÁ•}É…¹Õ±…É¥Ñå}‘•¥Í¥½¸¹É•Í½±Ù•}½É}Á…ÕÍ” (€€€€€€€€€€€€€€€€€€€€€€€É•Ù¥•ÜõÉ•Ù¥•Ü°(€€€€€€€€€€€€€€€€€€€€€€€¥¹Ù•¹Ñ½ÉäõÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½Éä°(€€€€€€€€€€€€€€€€€€€€€€€µ¥¹•‘}ÑåÁ•Ìõµ¥¹•‘}ÑåÁ•Ì°(€€€€€€€€€€€€€€€€€€€€€€€µ•Ñ„õµ•Ñ„°(€€€€€€€€€€€€€€€€€€€€€€€ÁÉ¥½É}‘•¥Í¥½¹}¥õÍÑÈ (€€€€€€€€€€€€€€€€€€€€€€€€€€€‘¥É•Ñ¥Ù”¹•Ğ ‰‘•¥Í¥½¹}¥ˆ¤½È€ˆˆ¤°(€€€€€€€€€€€€€€€€€€€€€€€™…¥±ÕÉ”õ™…¥±ÕÉ”°(€€€€€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€€€€€É…¥Í”IÕ¹Ñ¥µ•ÉÉ½È (€€€€€€€€€€€€€€€€€€€€€€€€‰¡Õµ…¸µ‘¥É•Ñ•QåÁ”½¹Í½±¥‘…Ñ¥½¸™…¥±•İ¥Ñ¡½ÕĞ€ˆ(€€€€€€€€€€€€€€€€€€€€€€€€‰É•…Ñ¥¹œ¥ÑÌÉ•ÅÕ¥É•™½±±½ÜµÕÀ‘•¥Í¥½¸ˆ(€€€€€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€ÁÉ•Í•ÉÙ•€ôì(€€€€€€€€€€€€€€€€€€€­•äè½Áä¹‘••Á½Áä¡Ù…±Õ”¤(€€€€€€€€€€€€€€€€€€€™½È­•ä°Ù…±Õ”¥¸€¡µ¥¹•‘}ÑåÁ•Ì½Èíô¤¹¥Ñ•µÌ ¤(€€€€€€€€€€€€€€€€€€€¥˜­•ä€„ô€‰ÑåÁ•Ìˆ(€€€€€€€€€€€€€€€ô(€€€€€€€€€€€€€€€µ¥¹•‘}ÑåÁ•Ì€ôì(€€€€€€€€€€€€€€€€€€€€¨©ÁÉ•Í•ÉÙ•°(€€€€€€€€€€€€€€€€€€€€‰ÑåÁ•Ìˆè½Áä¹‘••Á½Áä¡½¹Í½±¥‘…Ñ•¹•Ğ ‰ÑåÁ•Ìˆ¤½Èmt¤°(€€€€€€€€€€€€€€€ô(€€€€€€€€€€€€€€€É•Ù¥•İl‰ÑåÁ•}½Õ¹Ğ‰t€ô±•¸¡µ¥¹•‘}ÑåÁ•Íl‰ÑåÁ•Ì‰t¤(€€€€€€€€€€€€€€€¥¹Ù•¹Ñ½Éå}½Õ¹Ğ€ô¥¹Ğ¡É•Ù¥•Ü¹•Ğ ‰¥¹Ù•¹Ñ½Éå}½Õ¹Ğˆ¤½È€À¤(€€€€€€€€€€€€€€€É•Ù¥•İl‰½¹Í½±¥‘…Ñ¥½¹}µ•É•‘}½Õ¹Ğ‰t€ôµ…à (€€€€€€€€€€€€€€€€€€€¥¹Ğ¡É•Ù¥•Ü¹•Ğ ‰½¹Í½±¥‘…Ñ¥½¹}µ•É•‘}½Õ¹Ğˆ¤½È€À¤°(€€€€€€€€€€€€€€€€€€€¥¹Ğ¡É•Ù¥•Ü¹•Ğ ‰É…İ}ÑåÁ•}½Õ¹Ğˆ¤½È€À¤(€€€€€€€€€€€€€€€€€€€€´±•¸¡µ¥¹•‘}ÑåÁ•Íl‰ÑåÁ•Ì‰t¤°(€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€€ŒI”µ©Õ‘”Ñ¡”AQÑ…á½¹½µäİ¥Ñ „™É•Í µ½‘•°Ù•É‘¥ĞƒŠP(€€€€€€€€€€€€€€€€ŒÑ¡”½¹Í½±¥‘…Ñ¥½¸¡…¹•Ñ¡”QåÁ•ÌÑ¡”•…É±¥•ÈÙ•É‘¥ĞÍ…Ü¸(€€€€€€€€€€€€€€€Á½ÍÑ}Ù•É‘¥Ğ€ô€ (€€€€€€€€€€€€€€€€€€€ÑåÁ•}É…¹Õ±…É¥Ñå}‘•¥Í¥½¸¹™É…µ•¹Ñ…Ñ¥½¹}Ù•É‘¥Ğ (€€€€€€€€€€€€€€€€€€€€€€€É•Ù¥•Ü°(€€€€€€€€€€€€€€€€€€€€€€€µ¥¹•‘}ÑåÁ•Ìõµ¥¹•‘}ÑåÁ•Ì°(€€€€€€€€€€€€€€€€€€€€€€€¥¹Ù•¹Ñ½ÉäõÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½Éä°(€€€€€€€€€€€€€€€€€€€€€€€…Á¥}…±°õ}½Á•¹…¥}©Í½¸°(€€€€€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€É•Ù¥•İl‰™É…µ•¹Ñ…Ñ¥½¹}Ù•É‘¥Ğ‰t€ô½Áä¹‘••Á½Áä¡Á½ÍÑ}Ù•É‘¥Ğ¤(€€€€€€€€€€€€€€€¥˜Á½ÍÑ}Ù•É‘¥Ğ¹•Ğ ‰™É…µ•¹Ñ•ˆ¤è(€€€€€€€€€€€€€€€€€€€™…¥±ÕÉ”€ô€ (€€€€€€€€€€€€€€€€€€€€€€€€‰Q¡”‰½Õ¹‘•½¹Í½±¥‘…Ñ¥½¸İ…ÌÍ½ÕÉ”µÍ…™”°‰ÕĞÑ¡”€ˆ(€€€€€€€€€€€€€€€€€€€€€€€€‰µ½‘•°™É…µ•¹Ñ…Ñ¥½¸…Õ‘¥ĞÍÑ¥±°©Õ‘•ÌÑ¡”€ˆ(€€€€€€€€€€€€€€€€€€€€€€€˜‰í±•¸¡µ¥¹•‘}ÑåÁ•ÍlÑåÁ•Ìt¥ôµQåÁ”É•ÍÕ±Ğ™É…µ•¹Ñ•€ˆ(€€€€€€€€€€€€€€€€€€€€€€€˜‰™½Èí¥¹Ù•¹Ñ½Éå}½Õ¹Ñô¥¹Ù•¹Ñ½ÉäE%¡Ì¤è€ˆ(€€€€€€€€€€€€€€€€€€€€€€€˜‰íÍÑÈ¡Á½ÍÑ}Ù•É‘¥Ğ¹•Ğ É…Ñ¥½¹…±”œ¤½È€œœ¥lèÄÈÀÁuôˆ(€€€€€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€€€€€É•Ù¥•İl‰Á•¹‘¥¹}™½±±½İÕÀ‰t€ôì(€€€€€€€€€€€€€€€€€€€€€€€€‰ÁÉ¥½É}‘•¥Í¥½¹}¥ˆèÍÑÈ (€€€€€€€€€€€€€€€€€€€€€€€€€€€‘¥É•Ñ¥Ù”¹•Ğ ‰‘•¥Í¥½¹}¥ˆ¤½È€ˆˆ¤°(€€€€€€€€€€€€€€€€€€€€€€€€‰™…¥±ÕÉ”ˆè™…¥±ÕÉ”°(€€€€€€€€€€€€€€€€€€€ô(€€€€€€€€€€€€€€€€€€€É•Ù¥•İl‰±…ÍÑ}…ÑÑ•µÁĞ‰t€ôì(€€€€€€€€€€€€€€€€€€€€€€€€‰‘•¥Í¥½¹}¥ˆè‘•¥Í¥½¹}¥°(€€€€€€€€€€€€€€€€€€€€€€€€‰½¹Ñ•áÑ}¡…Í ˆè½¹Ñ•áÑ}¡…Í °(€€€€€€€€€€€€€€€€€€€€€€€€‰ÍÑ…ÑÕÌˆè€‰¥¹½µÁ±•Ñ”ˆ°(€€€€€€€€€€€€€€€€€€€€€€€€‰…Õ‘¥Ğˆè½Áä¹‘••Á½Áä¡É•Í½±ÕÑ¥½¹}…Õ‘¥Ğ¤°(€€€€€€€€€€€€€€€€€€€ô(€€€€€€€€€€€€€€€€€€€µ¥¹•‘}ÑåÁ•Íl‰}É…¹Õ±…É¥Ñå}É•Ù¥•Ü‰t€ô½Áä¹‘••Á½Áä (€€€€€€€€€€€€€€€€€€€€€€€É•Ù¥•Ü¤(€€€€€€€€€€€€€€€€€€€}•µ¥Ñ}½¹•ÁÑ}¡•­Á½¥¹Ğ (€€€€€€€€€€€€€€€€€€€€€€€¡•­Á½¥¹Ñ}…±±‰…¬°(€€€€€€€€€€€€€€€€€€€€€€€€ (€€€€€€€€€€€€€€€€€€€€€€€€€€€}=9AQ}!-A=%9Q}MQ(€€€€€€€€€€€€€€€€€€€€€€€€€€€¥˜Í…Ù•‘}½É‘•È€øô}¡•­Á½¥¹Ñ}½É‘•È (€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€}=9AQ}!-A=%9Q}MQ¤(€€€€€€€€€€€€€€€€€€€€€€€€€€€•±Í”}QeA}Qa=9=5e}!-A=%9Q}MQ(€€€€€€€€€€€€€€€€€€€€€€€€¤°(€€€€€€€€€€€€€€€€€€€€€€€É•½É‘Ìõ½ÕĞ°(€€€€€€€€€€€€€€€€€€€€€€€ÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½ÉäõÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½Éä°(€€€€€€€€€€€€€€€€€€€€€€€µ¥¹•‘}ÑåÁ•Ìõµ¥¹•‘}ÑåÁ•Ì°(€€€€€€€€€€€€€€€€€€€€€€€µ•Ñ¡½‘}É½İ}Í¹…ÁÍ¡½Ğõ}Í•É¥…±¥é•}µ•Ñ¡½‘}É½İ}Í¹…ÁÍ¡½Ğ (€€€€€€€€€€€€€€€€€€€€€€€€€€€µ•Ñ¡½‘}É½İ}Í¹…ÁÍ¡½Ğ¤°(€€€€€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€€€€€ÑåÁ•}É…¹Õ±…É¥Ñå}‘•¥Í¥½¸¹É•Í½±Ù•}½É}Á…ÕÍ” (€€€€€€€€€€€€€€€€€€€€€€€É•Ù¥•ÜõÉ•Ù¥•Ü°(€€€€€€€€€€€€€€€€€€€€€€€¥¹Ù•¹Ñ½ÉäõÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½Éä°(€€€€€€€€€€€€€€€€€€€€€€€µ¥¹•‘}ÑåÁ•Ìõµ¥¹•‘}ÑåÁ•Ì°(€€€€€€€€€€€€€€€€€€€€€€€µ•Ñ„õµ•Ñ„°(€€€€€€€€€€€€€€€€€€€€€€€ÁÉ¥½É}‘•¥Í¥½¹}¥õÍÑÈ (€€€€€€€€€€€€€€€€€€€€€€€€€€€‘¥É•Ñ¥Ù”¹•Ğ ‰‘•¥Í¥½¹}¥ˆ¤½È€ˆˆ¤°(€€€€€€€€€€€€€€€€€€€€€€€™…¥±ÕÉ”õ™…¥±ÕÉ”°(€€€€€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€€€€€É…¥Í”IÕ¹Ñ¥µ•ÉÉ½È (€€€€€€€€€€€€€€€€€€€€€€€€‰™É…µ•¹Ñ•¡Õµ…¸µ‘¥É•Ñ•QåÁ”É•ÍÕ±Ğ™…¥±•İ¥Ñ¡½ÕĞ€ˆ(€€€€€€€€€€€€€€€€€€€€€€€€‰É•…Ñ¥¹œ¥ÑÌÉ•ÅÕ¥É•™½±±½ÜµÕÀ‘•¥Í¥½¸ˆ(€€€€€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€É•Ù¥•İl‰±…ÍÑ}…ÑÑ•µÁĞ‰t€ôì(€€€€€€€€€€€€€€€€€€€€‰‘•¥Í¥½¹}¥ˆè‘•¥Í¥½¹}¥°(€€€€€€€€€€€€€€€€€€€€‰½¹Ñ•áÑ}¡…Í ˆè½¹Ñ•áÑ}¡…Í °(€€€€€€€€€€€€€€€€€€€€‰ÍÑ…ÑÕÌˆè€‰ÍÕ••‘•ˆ°(€€€€€€€€€€€€€€€€€€€€‰…Õ‘¥Ğˆè½Áä¹‘••Á½Áä¡É•Í½±ÕÑ¥½¹}…Õ‘¥Ğ¤°(€€€€€€€€€€€€€€€ô(€€€€€€€€€€€¥˜…Ñ¥½¸¥¸ì‰­••Àˆ°€‰½¹Í½±¥‘…Ñ”‰ôè(€€€€€€€€€€€€€€€É•Ù¥•Ü¹Á½À ‰Á•¹‘¥¹}™½±±½İÕÀˆ°9½¹”¤(€€€€€€€€€€€€€€€É•Ù¥•İl‰¡Õµ…¹}É•Í½±ÕÑ¥½¸‰t€ôì(€€€€€€€€€€€€€€€€€€€€‰‘•¥Í¥½¹}¥ˆèÍÑÈ (€€€€€€€€€€€€€€€€€€€€€€€‘¥É•Ñ¥Ù”¹•Ğ ‰‘•¥Í¥½¹}¥ˆ¤½È€ˆˆ¤°(€€€€€€€€€€€€€€€€€€€€‰½¹Ñ•áÑ}¡…Í ˆèÍÑÈ (€€€€€€€€€€€€€€€€€€€€€€€‘¥É•Ñ¥Ù”¹•Ğ ‰½¹Ñ•áÑ}¡…Í ˆ¤½È€ˆˆ¤°(€€€€€€€€€€€€€€€€€€€€‰¡½¥”ˆèÍÑÈ¡‘¥É•Ñ¥Ù”¹•Ğ ‰¡½¥”ˆ¤½È€ˆˆ¤°(€€€€€€€€€€€€€€€€€€€€‰…Ñ¥½¸ˆè…Ñ¥½¸°(€€€€€€€€€€€€€€€€€€€€‰…Õ‘¥Ğˆè½Áä¹‘••Á½Áä¡É•Í½±ÕÑ¥½¹}…Õ‘¥Ğ¤°(€€€€€€€€€€€€€€€ô(€€€€€€€€€€€€€€€µ¥¹•‘}ÑåÁ•Íl‰}É…¹Õ±…É¥Ñå}É•Ù¥•Ü‰t€ô½Áä¹‘••Á½Áä¡É•Ù¥•Ü¤(€€€€€€€€€€€€€€€É•Ù¥•İl‰¡Õµ…¹}É•Í½±ÕÑ¥½¸‰ul‰É•ÍÕ±Ñ}½¹Ñ•áÑ}¡…Í ‰t€ô€ (€€€€€€€€€€€€€€€€€€€ÑåÁ•}É…¹Õ±…É¥Ñå}‘•¥Í¥½¸¹…ÁÁ±¥•‘}É•ÍÕ±Ñ}½¹Ñ•áÑ}¡…Í  (€€€€€€€€€€€€€€€€€€€€€€€É•Ù¥•ÜõÉ•Ù¥•Ü°(€€€€€€€€€€€€€€€€€€€€€€€¥¹Ù•¹Ñ½ÉäõÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½Éä°(€€€€€€€€€€€€€€€€€€€€€€€µ¥¹•‘}ÑåÁ•Ìõµ¥¹•‘}ÑåÁ•Ì°(€€€€€€€€€€€€€€€€€€€€€€€µ•Ñ„õµ•Ñ„°(€€€€€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€É•Ù¥•İl‰¡Õµ…¹}É•Í½±ÕÑ¥½¸‰ul‰Í•µ…¹Ñ¥}½¹ÑÉ…Ñ}¡…Í ‰t€ô€ (€€€€€€€€€€€€€€€€€€€ÑåÁ•}É…¹Õ±…É¥Ñå}‘•¥Í¥½¸¹…ÁÁ±¥•‘}É•ÍÕ±Ñ}Í•µ…¹Ñ¥}¡…Í  (€€€€€€€€€€€€€€€€€€€€€€€¥¹Ù•¹Ñ½ÉäõÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½Éä°(€€€€€€€€€€€€€€€€€€€€€€€µ¥¹•‘}ÑåÁ•Ìõµ¥¹•‘}ÑåÁ•Ì°(€€€€€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€µ¥¹•‘}ÑåÁ•Íl‰}É…¹Õ±…É¥Ñå}É•Ù¥•Ü‰t€ô½Áä¹‘••Á½Áä¡É•Ù¥•Ü¤(€€€€€€€€€€€€€€€€ŒA•ÉÍ¥ÍĞÑ¡”…•ÁÑ•‘¥É•Ñ¥½¸‰•™½É”…¹ä‘½İ¹ÍÑÉ•…´A$Á…ÍÌ¸(€€€€€€€€€€€€€€€€Œ%˜„±…Ñ•ÈÕ¹É•±…Ñ•™…¥±ÕÉ”É•ÍÕµ•Ì¡•É”°Ñ¡”‘¥É•Ñ¥½¸¥Ì(€€€€€€€€€€€€€€€€Œ¹•¥Ñ¡•È‰¥±±•¹½ÈÉ•ÅÕ•ÍÑ•„Í•½¹Ñ¥µ”¸1•…ä€àÄ”(€€€€€€€€€€€€€€€€Œ¡•­Á½¥¹ÑÌÉ•Ñ…¥¸Ñ¡•¥È…±É•…‘äµ½µÁ±•Ñ•‘½İ¹ÍÑÉ•…´É½İÌ¸(€€€€€€€€€€€€€€€}•µ¥Ñ}½¹•ÁÑ}¡•­Á½¥¹Ğ (€€€€€€€€€€€€€€€€€€€¡•­Á½¥¹Ñ}…±±‰…¬°(€€€€€€€€€€€€€€€€€€€€ (€€€€€€€€€€€€€€€€€€€€€€€}=9AQ}!-A=%9Q}MQ(€€€€€€€€€€€€€€€€€€€€€€€¥˜Í…Ù•‘}½É‘•È€øô}¡•­Á½¥¹Ñ}½É‘•È (€€€€€€€€€€€€€€€€€€€€€€€€€€€}=9AQ}!-A=%9Q}MQ¤(€€€€€€€€€€€€€€€€€€€€€€€•±Í”}QeA}Qa=9=5e}!-A=%9Q}MQ(€€€€€€€€€€€€€€€€€€€€¤°(€€€€€€€€€€€€€€€€€€€É•½É‘Ìõ½ÕĞ°(€€€€€€€€€€€€€€€€€€€ÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½ÉäõÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½Éä°(€€€€€€€€€€€€€€€€€€€µ¥¹•‘}ÑåÁ•Ìõµ¥¹•‘}ÑåÁ•Ì°(€€€€€€€€€€€€€€€€€€€µ•Ñ¡½‘}É½İ}Í¹…ÁÍ¡½Ğõ}Í•É¥…±¥é•}µ•Ñ¡½‘}É½İ}Í¹…ÁÍ¡½Ğ (€€€€€€€€€€€€€€€€€€€€€€€µ•Ñ¡½‘}É½İ}Í¹…ÁÍ¡½Ğ¤°(€€€€€€€€€€€€€€€€¤((€€€¥˜Í…Ù•‘}½É‘•È€ğ}¡•­Á½¥¹Ñ}½É‘•È¡}=9AQ}!-A=%9Q}MQ¤è(€€€€€€€€ŒQ¡•Í”…±±ÌµÕÍĞ½‰Í•ÉÙ”Ñ¡”™¥¹…°Ñ…á½¹½µä€¡…ÕÑ½µ…Ñ¥Œ°•áÁ±¥¥Ñ±ä(€€€€€€€€Œ­•ÁĞ°½È¡Õµ…¸µ½¹Í½±¥‘…Ñ•¤…¹µÕÍĞ¹½ĞÉÕ¸‰•™½É”„¡Õµ…¸Á…ÕÍ”¸(€€€€€€€É•Ù¥•Ü€ô½Áä¹‘••Á½Áä (€€€€€€€€€€€€¡µ¥¹•‘}ÑåÁ•Ì½Èíô¤¹•Ğ ‰}É…¹Õ±…É¥Ñå}É•Ù¥•Üˆ¤½ÈÉ•Ù¥•Ü¤(€€€€€€€½¹•ÁÑ}½Õ¹Ñ}‰•™½É•}ÍÕ™™¥¥•¹ä€ô±•¸¡½ÕĞ¤(€€€€€€€½ÕĞ€ô}…‘‘}µ¥ÍÍ¥¹}ÑåÁ•}µ•Ñ¡½‘}½¹•ÁÑÍ}Ù¥…}…Á¤ (€€€€€€€€€€€½ÕĞ°µ¥¹•‘}ÑåÁ•Ìõµ¥¹•‘}ÑåÁ•Ì°µ•Ñ„õµ•Ñ„¤(€€€€€€€É•Ù¥•İl‰ÍÕ™™¥¥•¹å}…‘‘•‘}½¹•ÁÑÌ‰t€ôµ…à (€€€€€€€€€€€€À°±•¸¡½ÕĞ¤€´½¹•ÁÑ}½Õ¹Ñ}‰•™½É•}ÍÕ™™¥¥•¹ä¤(€€€€€€€É•Ù¥•İl‰ÍÕ™™¥¥•¹å}…Õ‘¥Ñ}½µÁ±•Ñ”‰t€ôQÉÕ”(€€€€€€€¥˜±•¸¡½ÕĞ¤€ø½¹•ÁÑ}½Õ¹Ñ}‰•™½É•}ÍÕ™™¥¥•¹äè(€€€€€€€€€€€½ÕĞ€ô}•¹ÍÕÉ•}µ…ÍÑ•Éå}±¥¹•Í}Ù¥…}…Á¤¡½ÕĞ°µ•Ñ„õµ•Ñ„¤(€€€€€€€ÁÉ½É•ÍÌ¹Í•Ñ}ÁÉ½É•ÍÌ (€€€€€€€€€€€€À¸Üä°±…‰•°ô‰½¹•ÁĞ•áÑÉ…Ñ¥½¸ƒŠPÉ•ÕÍ…‰±”QåÁ•Ìµ¥¹•ˆ¤(€€€€€€€ÁÉ½É•ÍÌ¹ÍÑ•À (€€€€€€€€€€€€‰½¹•ÁĞ•áÑÉ…Ñ¥½¸ƒŠP‰Õ¥±‘¥¹œÕ±µ¥¹…Ñ¥½¹Ìˆ°Ù…±Õ”ôÀ¸àÄ¤(€€€€€€€½ÕĞ€ô}‰Õ¥±‘}Õ±µ¥¹…Ñ¥½¹Í}Ù¥…}…Á¤¡½ÕĞ°µ•Ñ„õµ•Ñ„¤(€€€€€€€µ¥¹•‘}ÑåÁ•Íl‰}É…¹Õ±…É¥Ñå}É•Ù¥•Ü‰t€ô½Áä¹‘••Á½Áä¡É•Ù¥•Ü¤(€€€€€€€…ÁÁ±¥•€ôÉ•Ù¥•Ü¹•Ğ ‰¡Õµ…¹}É•Í½±ÕÑ¥½¸ˆ¤(€€€€€€€¥˜¥Í¥¹ÍÑ…¹”¡…ÁÁ±¥•°‘¥Ğ¤…¹…ÁÁ±¥•¹•Ğ ‰‘•¥Í¥½¹}¥ˆ¤è(€€€€€€€€€€€É•Ù¥•İl‰¡Õµ…¹}É•Í½±ÕÑ¥½¸‰ul‰É•ÍÕ±Ñ}½¹Ñ•áÑ}¡…Í ‰t€ô€ (€€€€€€€€€€€€€€€ÑåÁ•}É…¹Õ±…É¥Ñå}‘•¥Í¥½¸¹…ÁÁ±¥•‘}É•ÍÕ±Ñ}½¹Ñ•áÑ}¡…Í  (€€€€€€€€€€€€€€€€€€€É•Ù¥•ÜõÉ•Ù¥•Ü°(€€€€€€€€€€€€€€€€€€€¥¹Ù•¹Ñ½ÉäõÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½Éä°(€€€€€€€€€€€€€€€€€€€µ¥¹•‘}ÑåÁ•Ìõµ¥¹•‘}ÑåÁ•Ì°(€€€€€€€€€€€€€€€€€€€µ•Ñ„õµ•Ñ„°(€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€¤(€€€€€€€€€€€µ¥¹•‘}ÑåÁ•Íl‰}É…¹Õ±…É¥Ñå}É•Ù¥•Ü‰t€ô½Áä¹‘••Á½Áä¡É•Ù¥•Ü¤(€€€€€€€}•µ¥Ñ}½¹•ÁÑ}¡•­Á½¥¹Ğ (€€€€€€€€€€€¡•­Á½¥¹Ñ}…±±‰…¬°(€€€€€€€€€€€}=9AQ}!-A=%9Q}MQ°(€€€€€€€€€€€É•½É‘Ìõ½ÕĞ°(€€€€€€€€€€€ÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½ÉäõÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½Éä°(€€€€€€€€€€€µ¥¹•‘}ÑåÁ•Ìõµ¥¹•‘}ÑåÁ•Ì°(€€€€€€€€€€€µ•Ñ¡½‘}É½İ}Í¹…ÁÍ¡½Ğõ}Í•É¥…±¥é•}µ•Ñ¡½‘}É½İ}Í¹…ÁÍ¡½Ğ (€€€€€€€€€€€€€€€µ•Ñ¡½‘}É½İ}Í¹…ÁÍ¡½Ğ¤°(€€€€€€€€¤((€€€¥˜…ÉÑ¥™…ÑÌ¥Ì¹½Ğ9½¹”è(€€€€€€€…ÉÑ¥™…ÑÍl‰ÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½Éä‰t€ô½Áä¹‘••Á½Áä (€€€€€€€€€€€ÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½Éä¤(€€€€€€€…ÉÑ¥™…ÑÍl‰µ¥¹•‘}ÑåÁ•Ì‰t€ô½Áä¹‘••Á½Áä¡µ¥¹•‘}ÑåÁ•Ì¤((€€€¥˜Í…Ù•‘}½É‘•È€ğ}¡•­Á½¥¹Ñ}½É‘•È ‰Á½ÍÑ}ÑåÁ•}…ÍÍ¥¹µ•¹Ğˆ¤è(€€€€€€€€ŒQåÁ”…±±½…Ñ¥½¸‰•±½¹ÌÑ¼Ñ¡”É•İÉ¥ÑÑ•¸A¡…Í”€Ì€¡Ñ¡”!½ÍĞÁ…ÍÌ(€€€€€€€€Œ‘•¥‘•Ì°ÍÍ•µ‰±”É•¹‘•ÉÌ¤°Í¼Ñ¡”ÁÉ”µ™¥¹…°ÍÑ…•ÌÍ¡¥À„(€€€€€€€€ŒÍÑÉ¥ÁÁ•Ñ½Á½±½ä…ÉÉå¥¹œ¹¼Í½ÕÉ”µ½İ¹•…±±½…Ñ¥½¹Ì¸Q¡”€àÄ”(€€€€€€€€Œ¡•­Á½¥¹Ğ…‰½Ù”É•µ…¥¹ÌÑ¡”‘ÕÉ…‰±”É•ÍÕµ”‰½Õ¹‘…ÉäƒŠP¹¼(€€€€€€€€Œµ¥Í±•…‘¥¹œ€äÄ”…ÉÑ¥™…Ğ¥ÌÁ•ÉÍ¥ÍÑ•‰•™½É”…±±½…Ñ¥½¸•á¥ÍÑÌ¸(€€€€€€€¥µÁ½ÉĞÍåÌ…Ì}ÍåÌ((€€€€€€€™É½´€¸¥µÁ½ÉĞ½¹•ÁÑ}Ñ½Á½±½å}½¹ÑÉ…Ğ…Ì}Ñ½Á½±½ä((€€€€€€€ÁÉ½É•ÍÌ¹ÍÑ•À (€€€€€€€€€€€€‰½¹•ÁĞ•áÑÉ…Ñ¥½¸ƒŠPÁÉ•Á…É¥¹œ™¥¹…°Ñ½Á½±½ä‰•™½É”QåÁ”€ˆ(€€€€€€€€€€€€‰…±±½…Ñ¥½¸ˆ°(€€€€€€€€€€€Ù…±Õ”ôÀ¸àÔ°(€€€€€€€€¤(€€€€€€€ÁÉ½É•ÍÌ¹±½œ (€€€€€€€€€€€€‰•™•ÉÉ•QåÁ”…±±½…Ñ¥½¸Õ¹Ñ¥°Í•µ…¹Ñ¥Œ½¹•ÁĞÑ½Á½±½ä¥Ì€ˆ(€€€€€€€€€€€€‰™¥¹…°¸ˆ°(€€€€€€€€€€€±•Ù•°ô‰ÍÕ•ÍÌˆ°(€€€€€€€€¤(€€€€€€€½ÕĞ€ô}Ñ½Á½±½ä¹}ÍÑÉ¥Á}Í½ÕÉ•}½İ¹•‘}…±±½…Ñ¥½¹Ì (€€€€€€€€€€€}ÍåÌ¹µ½‘Õ±•Ím}}¹…µ•}}t°½ÕĞ(€€€€€€€€¤(€€€€€€€}É•Í•Ñ}Á±…•µ•¹Ñ}•ÉÑ¥™¥…Ñ¥½¹Ì¡µ¥¹•‘}ÑåÁ•Ì¤(€€€€€€€µ¥¹•‘}ÑåÁ•Íl‰}Ñ½Á½±½å}…±±½…Ñ¥½¹}½¹ÑÉ…Ğ‰t€ôì(€€€€€€€€€€€€‰Ù•ÉÍ¥½¸ˆè}Ñ½Á½±½ä¹}=9QIQ}YIM%=8°(€€€€€€€€€€€€‰ÍÑ…Ñ”ˆè€‰‘•™•ÉÉ•ˆ°(€€€€€€€ô(€€€€€€€¥˜…ÉÑ¥™…ÑÌ¥Ì¹½Ğ9½¹”è(€€€€€€€€€€€…ÉÑ¥™…ÑÍl‰µ¥¹•‘}ÑåÁ•Ì‰t€ô½Áä¹‘••Á½Áä¡µ¥¹•‘}ÑåÁ•Ì¤(€€€€€€€ÁÉ½É•ÍÌ¹Í•Ñ}ÁÉ½É•ÍÌ (€€€€€€€€€€€€À¸äÄ°(€€€€€€€€€€€±…‰•°ô (€€€€€€€€€€€€€€€€‰½¹•ÁĞ•áÑÉ…Ñ¥½¸ƒŠPÑ½Á½±½äÉ•…‘ä™½È™¥¹…°QåÁ”€ˆ(€€€€€€€€€€€€€€€€‰…±±½…Ñ¥½¸ˆ(€€€€€€€€€€€€¤°(€€€€€€€€¤(€€€•±Í”è(€€€€€€€ÁÉ½É•ÍÌ¹±½œ (€€€€€€€€€€€€‰QåÁ”…ÍÍ¥¹µ•¹Ğ…¹…Ñ¥Ù¥Ñä¡Õ‰ÌÉ•ÍÑ½É•™É½´¡•­Á½¥¹Ğì€ˆ(€€€€€€€€€€€€‰½¹Ñ¥¹Õ¥¹œ…Ğ™¥¹…°Ù…±¥‘…Ñ¥½¸…¹É•Á…¥È¸ˆ°(€€€€€€€€€€€±•Ù•°ô‰ÍÕ•ÍÌˆ°(€€€€€€€€¤((€€€É•ÑÕÉ¸½ÕĞ°ÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½Éä°µ¥¹•‘}ÑåÁ•Ì°µ•Ñ¡½‘}É½İ}Í¹…ÁÍ¡½Ğ(()‘•˜}ÁÉ•Á…É•}™¥¹…±}½¹•ÁÑ}½¹Ñ•¹Ğ¡½ÕĞè±¥ÍÑm‘¥Ñt°€¨©­İ…ÉÌ¤€´ø±¥ÍÑm‘¥Ñtè(€€€€ˆˆ‰Ù•ÉåÑ¡¥¹œ…™Ñ•ÈÑ¡”€àÄ”‰½Õ¹‘…ÉäÉÕ¹ÌÑ¡É½Õ Ñ¡”É•İÉ¥ÑÑ•¸A¡…Í”€Ì¸((€€€M•…±ÌÑ¡”‰½Õ¹‘…Éä•¹Ù•±½Á”™É½´Ñ¡”…Ñ¥Ù”Í•ÍÍ¥½¸½É…Á ÍÑ…Ñ”…¹(€€€ÉÕ¹ÌM•ÑÑ±”ƒŠH!½ÍĞƒŠHA½±¥Í ƒŠHÍÍ•µ‰±”İ¥Ñ Ñ¡”‘•¥Í¥½¸ÍÑ½É”¥¸Ñ¡”(€€€©½ˆÌ…ÉÑ¥™…Ğ‘¥É•Ñ½Éä€¡‘½Ì½Á¡…Í”ÌµÉ•İÉ¥Ñ”µÍÁ•Œ¹µ¤¸Q¡”±•…ä(€€€€Ì¸ÇŠLÌ¸ÄÄ…±±½…Ñ¥½¸±…¹”Ñ¡¥Ì™Õ¹Ñ¥½¸ÕÍ•Ñ¼‘É¥Ù”¥Ì‘•±•Ñ•¸((€€€…±±•ÈÑ¡…ĞÁ…ÍÍ•Ì„Á¡…Í”Í}…ÉÉå€‘¥ĞÉ••¥Ù•Ì•Ù•Éä¹½¸µÉ½Ü(€€€­•äÑ¡”ÉÕ¸ÁÉ½‘Õ•ƒŠPÑ¡”A¡…Í”€ÀÌÁÉ”µÉ•ÅÕ¥Í¥Ñ”…ÁÑÕÉ”€¡‘½Œƒ
œĞ°(€€€DÌ¤…µ½¹œÑ¡•´¸Q¡”É•ÑÕÉ¹•É½İÌ…É”Õ¹…™™•Ñ•‰ä¥Ğ¸(€€€€ˆˆˆ(€€€¥µÁ½ÉĞÍåÌ…Ì}ÍåÌ((€€€™É½´€¸¥µÁ½ÉĞ½¹•ÁÑ}Ñ½Á½±½å}½¹ÑÉ…Ğ…Ì}Ñ½Á½±½ä((€€€…ÉÉä€ô­İ…ÉÌ¹•Ğ ‰Á¡…Í”Í}…ÉÉäˆ¤(€€€É•ÑÕÉ¸}Ñ½Á½±½ä¹}ÉÕ¹}É•İÉ¥ÑÑ•¹}Á¡…Í”Ì (€€€€€€€}ÍåÌ¹µ½‘Õ±•Ím}}¹…µ•}}t°½ÕĞ°­İ…ÉÌ°(€€€€€€€…ÉÉäõ…ÉÉä¥˜¥Í¥¹ÍÑ…¹”¡…ÉÉä°‘¥Ğ¤•±Í”9½¹”°(€€€€¤(()‘•˜}É•Á…¥É}™¥¹…±}É¥¡}Ñ•áÑ}Ù¥…}…Á¤ (€€€É•½É‘Ìè±¥ÍÑm‘¥Ñt°€¨°µ•Ñ„è‘¥Ğ°¥¹Ù•¹Ñ½Éäè‘¥Ğğ9½¹”€ô9½¹”°(€€€µ¥¹•‘}ÑåÁ•Ìè‘¥Ğğ9½¹”€ô9½¹”°(¤€´øÑÕÁ±•m±¥ÍÑm‘¥Ñt°‰½½±tè(€€€€ˆˆ‰I•Á…¥È±…Ñ”É¥ µÑ•áĞ‘•™•ÑÌİ¥Ñ¡½ÕĞÉ•Á±…å¥¹œ•Ù•ÉäÍ•µ…¹Ñ¥ŒÍÑ…”¸((€€€=±‘•È™¥¹…±}½¹Ñ•¹Ñ}É•…‘å€¡•­Á½¥¹ÑÌ…¸½¹Ñ…¥¸‰…É”Q•`‰•…ÕÍ”Ñ¡…Ğ(€€€ÍÑ…”ÕÍ•Ñ¼‰”Á•ÉÍ¥ÍÑ•‰•™½É”Ñ¡”ÍÑÉ¥Ğ™¥¹…°…Ñ”¸€…¹½¹¥…±¥é…Ñ¥½¸(€€€¥¹Ñ•¹Ñ¥½¹…±±ä…¹¹½Ğ¥¹™•ÈÑ¡”‰½Õ¹‘…Éä½˜…É‰¥ÑÉ…Éä‰…É”Q•`°Í¼…Í¬Ñ¡”(€€€•á¥ÍÑ¥¹œÙ…±¥‘…Ñ¥½¸É•Á…¥ÈÁ…ÍÌÑ¼İÉ…À½¹±äÑ¡”…™™•Ñ•É½İÌ¸€á…Ğ(€€€Í½ÕÉ”µá…µÁ±”½Ù•É…”É•µ…¥¹ÌÁÉ½Ñ•Ñ•İ¡¥±”-…Ñ•àİÉ…ÁÁ•Èµ½¹±ä¡…¹•Ì(€€€½µÁ…É”…ÌÑ¡”Í…µ”Í½ÕÉ”Ñ…Í¬¸(€€€€ˆˆˆ(€€€‘•˜İÉ…ÁÁ•É}½¹±å}­•ä¡Ù…±Õ”èÍÑÈ¤€´øÍÑÈè(€€€€€€€İ¥Ñ¡½ÕÑ}Ñ…Ì€ôÉ”¹ÍÕˆ (€€€€€€€€€€€È‰ql¼ı­…Ñ•áqtˆ°€ˆ€ˆ°ÍÑÈ¡Ù…±Õ”½È€ˆˆ¤°™±…ÌõÉ”¹%9=IM¤(€€€€€€€¹½Éµ…±¥é•€ôÉ”¹ÍÕˆ¡È‰qÌ¬ˆ°€ˆ€ˆ°İ¥Ñ¡½ÕÑ}Ñ…Ì¤¹ÍÑÉ¥À ¤(€€€€€€€€Œ±½Í¥¹œİÉ…ÁÁ•È¥µµ•‘¥…Ñ•±ä‰•™½É”ÁÕ¹ÑÕ…Ñ¥½¸¹••ÍÍ…É¥±ä±•…Ù•Ì(€€€€€€€€Œ„É•µ½Ù…‰±”ÍÁ…•Èİ¡•¸Ñ¡”Ñ…œ¥ÑÍ•±˜¥ÌÍÑÉ¥ÁÁ•¸(€€€€€€€É•ÑÕÉ¸É”¹ÍÕˆ¡È‰qÌ¬¡l°¸ìè„ıt¤ˆ°È‰pÄˆ°¹½Éµ…±¥é•¤((€€€‘•˜™É••é•}‘•™•Ğ¡Ù…±Õ”¤è(€€€€€€€¥˜¥Í¥¹ÍÑ…¹”¡Ù…±Õ”°‘¥Ğ¤è(€€€€€€€€€€€É•ÑÕÉ¸ÑÕÁ±”¡Í½ÉÑ• (€€€€€€€€€€€€€€€€¡­•ä°™É••é•}‘•™•Ğ¡¥Ñ•´¤¤™½È­•ä°¥Ñ•´¥¸Ù…±Õ”¹¥Ñ•µÌ ¤(€€€€€€€€€€€€¤¤(€€€€€€€¥˜¥Í¥¹ÍÑ…¹”¡Ù…±Õ”°€¡±¥ÍĞ°ÑÕÁ±”¤¤è(€€€€€€€€€€€É•ÑÕÉ¸ÑÕÁ±”¡™É••é•}‘•™•Ğ¡¥Ñ•´¤™½È¥Ñ•´¥¸Ù…±Õ”¤(€€€€€€€É•ÑÕÉ¸Ù…±Õ”((€€€‘•˜‘•™•Ñ}µÕ±Ñ¥Í•Ğ¡Ù…±Õ•Ì¤€´ø±¥ÍĞè(€€€€€€€É•ÑÕÉ¸Í½ÉÑ• (€€€€€€€€€€€€¡™É••é•}‘•™•Ğ¡Ù…±Õ”¤™½ÈÙ…±Õ”¥¸Ù…±Õ•Ì¤°(€€€€€€€€€€€­•äõÉ•ÁÈ°(€€€€€€€€¤((€€€‘•˜…‘‘•‘}İÉ…ÁÁ•ÉÍ}…É•}µ…Ñ ¡‰•™½É”èÍÑÈ°…™Ñ•ÈèÍÑÈ¤€´ø‰½½°è(€€€€€€€Á…ÑÑ•É¸€ôÉ”¹½µÁ¥±” (€€€€€€€€€€€È‰qm­…Ñ•áquqÌ¨ ı@ñ‰½‘äø¸¨ü¥qÌ©ql½­…Ñ•áqtˆ°(€€€€€€€€€€€É”¹%9=IMğÉ”¹=Q10°(€€€€€€€€¤((€€€€€€€‘•˜İÉ…ÁÁ•É}ÍÁ…¹Ì¡Ù…±Õ”èÍÑÈ¤€´øÑÕÁ±•mÍÑÈ°±¥ÍÑmÑÕÁ±•m¥¹Ğ°¥¹Ğ°ÍÑÉuutè(€€€€€€€€€€€Í½ÕÉ”€ôÍÑÈ¡Ù…±Õ”½È€ˆˆ¤(€€€€€€€€€€€ÍÁ…¹Ìè±¥ÍÑmÑÕÁ±•m¥¹Ğ°¥¹Ğ°ÍÑÉut€ômt(€€€€€€€€€€€Í½ÕÉ•}ÕÉÍ½È€ô€À(€€€€€€€€€€€Õ¹İÉ…ÁÁ•‘}ÕÉÍ½È€ô€À(€€€€€€€€€€€™½Èµ…Ñ ¥¸Á…ÑÑ•É¸¹™¥¹‘¥Ñ•È¡Í½ÕÉ”¤è(€€€€€€€€€€€€€€€Õ¹İÉ…ÁÁ•‘}ÕÉÍ½È€¬ô±•¸¡Í½ÕÉ•mÍ½ÕÉ•}ÕÉÍ½Èéµ…Ñ ¹ÍÑ…ÉĞ ¥t¤(€€€€€€€€€€€€€€€‰½‘ä€ô€¡µ…Ñ ¹É½ÕÀ ‰‰½‘äˆ¤½È€ˆˆ¤¹ÍÑÉ¥À ¤(€€€€€€€€€€€€€€€ÍÑ…ÉĞ€ôÕ¹İÉ…ÁÁ•‘}ÕÉÍ½È(€€€€€€€€€€€€€€€Õ¹İÉ…ÁÁ•‘}ÕÉÍ½È€¬ô±•¸¡‰½‘ä¤(€€€€€€€€€€€€€€€ÍÁ…¹Ì¹…ÁÁ•¹ ¡ÍÑ…ÉĞ°Õ¹İÉ…ÁÁ•‘}ÕÉÍ½È°‰½‘ä¤¤(€€€€€€€€€€€€€€€Í½ÕÉ•}ÕÉÍ½È€ôµ…Ñ ¹•¹ ¤(€€€€€€€€€€€É•ÑÕÉ¸­È¹Õ¹İÉ…Á}­…Ñ•à¡Í½ÕÉ”¤°ÍÁ…¹Ì((€€€€€€€}‰•™½É•}Ñ•áĞ°•á¥ÍÑ¥¹œ€ôİÉ…ÁÁ•É}ÍÁ…¹Ì¡‰•™½É”¤(€€€€€€€…™Ñ•É}Ñ•áĞ°…¹‘¥‘…Ñ•}ÍÁ…¹Ì€ôİÉ…ÁÁ•É}ÍÁ…¹Ì¡…™Ñ•È¤(€€€€€€€™½ÈÍÑ…ÉĞ°•¹°‰½‘ä¥¸…¹‘¥‘…Ñ•}ÍÁ…¹Ìè(€€€€€€€€€€€¥‘•¹Ñ¥Ñä€ô€¡ÍÑ…ÉĞ°•¹°‰½‘ä¤(€€€€€€€€€€€¥˜¥‘•¹Ñ¥Ñä¥¸•á¥ÍÑ¥¹œè(€€€€€€€€€€€€€€€•á¥ÍÑ¥¹œ¹É•µ½Ù”¡¥‘•¹Ñ¥Ñä¤(€€€€€€€€€€€€€€€½¹Ñ¥¹Õ”(€€€€€€€€€€€¥˜¹½Ğ­È¹¥Í}Õ¹…µ‰¥Õ½ÕÍ}µ…Ñ¡}•áÁÉ•ÍÍ¥½¸¡‰½‘ä¤è(€€€€€€€€€€€€€€€É•ÑÕÉ¸…±Í”(€€€€€€€€€€€¥˜€ (€€€€€€€€€€€€€€€ÍÑ…ÉĞ€ø€À(€€€€€€€€€€€€€€€…¹‰½‘ä(€€€€€€€€€€€€€€€…¹É”¹µ…Ñ ¡È‰qÜˆ°…™Ñ•É}Ñ•áÑmÍÑ…ÉĞ€´€Åt¤(€€€€€€€€€€€€€€€…¹É”¹µ…Ñ ¡È‰qÜˆ°‰½‘ålÁt¤(€€€€€€€€€€€€¤è(€€€€€€€€€€€€€€€É•ÑÕÉ¸…±Í”(€€€€€€€€€€€¥˜€ (€€€€€€€€€€€€€€€•¹€ğ±•¸¡…™Ñ•É}Ñ•áĞ¤(€€€€€€€€€€€€€€€…¹‰½‘ä(€€€€€€€€€€€€€€€…¹É”¹µ…Ñ ¡È‰qÜˆ°‰½‘ål´Åt¤(€€€€€€€€€€€€€€€…¹É”¹µ…Ñ ¡È‰qÜˆ°…™Ñ•É}Ñ•áÑm•¹‘t¤(€€€€€€€€€€€€¤è(€€€€€€€€€€€€€€€É•ÑÕÉ¸…±Í”(€€€€€€€€€€€¥˜€ (€€€€€€€€€€€€€€€€ˆ¼¼ˆ¥¸‰½‘ä(€€€€€€€€€€€€€€€½ÈÉ”¹Í•…É  (€€€€€€€€€€€€€€€€€€€È‰qˆ üé5¥Í•±±…¹•½ÕÍqÌ¬¤ıQåÁ•qÌ­q‘ìÄ°Éôèˆ(€€€€€€€€€€€€€€€€€€€È‰ñq‰…Í•qÌ­q‘ìÄ°Éôèˆ(€€€€€€€€€€€€€€€€€€€È‰ñq‰á…µÁ±•Ìü üéqÌ¬À©q¬¤ıqÌ¨èˆ°(€€€€€€€€€€€€€€€€€€€‰½‘ä°(€€€€€€€€€€€€€€€€€€€É”¹%9=IM°(€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€¤è(€€€€€€€€€€€€€€€É•ÑÕÉ¸…±Í”(€€€€€€€É•ÑÕÉ¸¹½Ğ•á¥ÍÑ¥¹œ((€€€‘•˜™½Éµ…ÑÑ¥¹}½¹±å}É•Ù¥•İ}¥Í}Í…™” (€€€€€€€‰…Í•±¥¹”è±¥ÍÑm‘¥Ñt°…¹‘¥‘…Ñ”è±¥ÍÑm‘¥Ñt°(€€€€¤€´ø‰½½°è(€€€€€€€€ˆˆ‰AÉ½Ù”„±…Ñ”É•Á…¥È¡…¹•½¹±ä-…Q•`İÉ…ÁÁ•È‰½Õ¹‘…É¥•Ì¸ˆˆˆ(€€€€€€€¥˜±•¸¡‰…Í•±¥¹”¤€„ô±•¸¡…¹‘¥‘…Ñ”¤è(€€€€€€€€€€€É•ÑÕÉ¸…±Í”(€€€€€€€™½È‰•™½É”°…™Ñ•È¥¸é¥À¡‰…Í•±¥¹”°…¹‘¥‘…Ñ”¤è(€€€€€€€€€€€¥˜}É•½É‘}­•ä¡‰•™½É”¤€„ô}É•½É‘}­•ä¡…™Ñ•È¤è(€€€€€€€€€€€€€€€É•ÑÕÉ¸…±Í”(€€€€€€€€€€€‰•™½É•}½Ñ¡•È€ôì(€€€€€€€€€€€€€€€­•äèÙ…±Õ”™½È­•ä°Ù…±Õ”¥¸‰•™½É”¹¥Ñ•µÌ ¤(€€€€€€€€€€€€€€€¥˜­•ä€„ô€‰½¹•ÁÑ}‘•Ñ…¥±Ìˆ(€€€€€€€€€€€ô(€€€€€€€€€€€…™Ñ•É}½Ñ¡•È€ôì(€€€€€€€€€€€€€€€­•äèÙ…±Õ”™½È­•ä°Ù…±Õ”¥¸…™Ñ•È¹¥Ñ•µÌ ¤(€€€€€€€€€€€€€€€¥˜­•ä€„ô€‰½¹•ÁÑ}‘•Ñ…¥±Ìˆ(€€€€€€€€€€€ô(€€€€€€€€€€€¥˜‰•™½É•}½Ñ¡•È€„ô…™Ñ•É}½Ñ¡•Èè(€€€€€€€€€€€€€€€É•ÑÕÉ¸…±Í”(€€€€€€€€€€€¥˜­È¹Õ¹İÉ…Á}­…Ñ•à (€€€€€€€€€€€€€€€‰•™½É”¹•Ğ ‰½¹•ÁÑ}‘•Ñ…¥±Ìˆ°€ˆˆ¤(€€€€€€€€€€€€¤€„ô­È¹Õ¹İÉ…Á}­…Ñ•à¡…™Ñ•È¹•Ğ ‰½¹•ÁÑ}‘•Ñ…¥±Ìˆ°€ˆˆ¤¤è(€€€€€€€€€€€€€€€É•ÑÕÉ¸…±Í”(€€€€€€€€€€€¥˜¹½Ğ…‘‘•‘}İÉ…ÁÁ•ÉÍ}…É•}µ…Ñ  (€€€€€€€€€€€€€€€‰•™½É”¹•Ğ ‰½¹•ÁÑ}‘•Ñ…¥±Ìˆ°€ˆˆ¤°(€€€€€€€€€€€€€€€…™Ñ•È¹•Ğ ‰½¹•ÁÑ}‘•Ñ…¥±Ìˆ°€ˆˆ¤°(€€€€€€€€€€€€¤è(€€€€€€€€€€€€€€€É•ÑÕÉ¸…±Í”((€€€€€€€¥˜}É•¹‘•É•‘}¥¹Ù•¹Ñ½Éå}½Ù•É…•}‘•™•ÑÌ (€€€€€€€€€€€‰…Í•±¥¹”°¥¹Ù•¹Ñ½Éä(€€€€€€€€¤€„ô}É•¹‘•É•‘}¥¹Ù•¹Ñ½Éå}½Ù•É…•}‘•™•ÑÌ¡…¹‘¥‘…Ñ”°¥¹Ù•¹Ñ½Éä¤è(€€€€€€€€€€€É•ÑÕÉ¸…±Í”(€€€€€€€½µÁ…É¥Í½¹Ì€ô€ (€€€€€€€€€€€€ (€€€€€€€€€€€€€€€}É•¹‘•É•‘}¥¹Ù•¹Ñ½Éå}Ñ½Á¥}Ù¥½±…Ñ¥½¹Ì (€€€€€€€€€€€€€€€€€€€‰…Í•±¥¹”°¥¹Ù•¹Ñ½Éä°µ¥¹•‘}ÑåÁ•Ì¤°(€€€€€€€€€€€€€€€}É•¹‘•É•‘}¥¹Ù•¹Ñ½Éå}Ñ½Á¥}Ù¥½±…Ñ¥½¹Ì (€€€€€€€€€€€€€€€€€€€…¹‘¥‘…Ñ”°¥¹Ù•¹Ñ½Éä°µ¥¹•‘}ÑåÁ•Ì¤°(€€€€€€€€€€€€¤°(€€€€€€€€€€€€ (€€€€€€€€€€€€€€€}…Ñ¥Ù¥Ñå}•á…µÁ±•}¡Õ‰}…±¥¹µ•¹Ñ}Ù¥½±…Ñ¥½¹Ì (€€€€€€€€€€€€€€€€€€€‰…Í•±¥¹”°¥¹Ù•¹Ñ½Éä¤°(€€€€€€€€€€€€€€€}…Ñ¥Ù¥Ñå}•á…µÁ±•}¡Õ‰}…±¥¹µ•¹Ñ}Ù¥½±…Ñ¥½¹Ì (€€€€€€€€€€€€€€€€€€€…¹‘¥‘…Ñ”°¥¹Ù•¹Ñ½Éä¤°(€€€€€€€€€€€€¤°(€€€€€€€€€€€€ (€€€€€€€€€€€€€€€}¡Õ‰}¥¹Ù•¹Ñ½Éå}•á…µÁ±•Í}¥¹}ÑåÁ•Ì¡‰…Í•±¥¹”°¥¹Ù•¹Ñ½Éä¤°(€€€€€€€€€€€€€€€}¡Õ‰}¥¹Ù•¹Ñ½Éå}•á…µÁ±•Í}¥¹}ÑåÁ•Ì¡…¹‘¥‘…Ñ”°¥¹Ù•¹Ñ½Éä¤°(€€€€€€€€€€€€¤°(€€€€€€€€¤(€€€€€€€É•ÑÕÉ¸…±° (€€€€€€€€€€€‘•™•Ñ}µÕ±Ñ¥Í•Ğ¡‰•™½É”¤€ôô‘•™•Ñ}µÕ±Ñ¥Í•Ğ¡…™Ñ•È¤(€€€€€€€€€€€™½È‰•™½É”°…™Ñ•È¥¸½µÁ…É¥Í½¹Ì(€€€€€€€€¤((€€€Ù…±¥‘…Ñ¥½¹}…ÉÌ€ôì(€€€€€€€€‰…±±½İ}ÑåÁ•ÌˆèQÉÕ”°(€€€€€€€€‰É•ÅÕ¥É•}Õ±µ¥¹…Ñ¥½¸ˆèQÉÕ”°(€€€€€€€€‰…±±½İ}Õ±µ¥¹…Ñ¥½¸ˆèQÉÕ”°(€€€€€€€€‰…±±½İ•‘}Í½ÕÉ•}•á…µÁ±•Ìˆè}¥¹Ù•¹Ñ½Éå}Í½ÕÉ•}•á…µÁ±•Ì¡¥¹Ù•¹Ñ½Éä¤°(€€€ô(€€€½É¥¥¹…°€ô½Áä¹‘••Á½Áä¡É•½É‘Ì¤(€€€É•Á…¥É•€ô½Áä¹‘••Á½Áä¡É•½É‘Ì¤(€€€¥¹¥Ñ¥…±}•ÉÉ½É}½Õ¹Ğ€ô€À(€€€™½È…ÑÑ•µÁĞ¥¸É…¹” Ä°€Ì¤è(€€€€€€€É•Á½ÉĞ€ôØ¹Ù…±¥‘…Ñ•}½¹•ÁÑ}É½İÌ¡É•Á…¥É•°€¨©Ù…±¥‘…Ñ¥½¹}…ÉÌ¤(€€€€€€€É¥¡}Ñ•áÑ}•ÉÉ½ÉÌ€ôl(€€€€€€€€€€€•ÉÉ½È™½È•ÉÉ½È¥¸É•Á½ÉÑl‰•ÉÉ½ÉÌ‰t(€€€€€€€€€€€¥˜€ (€€€€€€€€€€€€€€€•ÉÉ½È¹•Ğ ‰Í•Ù•É¥Ñäˆ¤€ôô€‰•ÉÉ½Èˆ(€€€€€€€€€€€€€€€…¹•ÉÉ½È¹•Ğ ‰½‘”ˆ¤€ôô€‰É¥¡}Ñ•áÑ}™½Éµ…Ğˆ(€€€€€€€€€€€€¤(€€€€€€€t(€€€€€€€¥˜¹½ĞÉ¥¡}Ñ•áÑ}•ÉÉ½ÉÌè(€€€€€€€€€€€‰É•…¬(€€€€€€€¥˜¹½Ğ¥¹¥Ñ¥…±}•ÉÉ½É}½Õ¹Ğè(€€€€€€€€€€€¥¹¥Ñ¥…±}•ÉÉ½É}½Õ¹Ğ€ô±•¸¡É¥¡}Ñ•áÑ}•ÉÉ½ÉÌ¤(€€€€€€€€€€€ÁÉ½É•ÍÌ¹±½œ (€€€€€€€€€€€€€€€€‰¥¹…°É¥ µÑ•áĞÙ…±¥‘…Ñ¥½¸™½Õ¹€ˆ(€€€€€€€€€€€€€€€˜‰í¥¹¥Ñ¥…±}•ÉÉ½É}½Õ¹Ñôµ…±™½Éµ•É½Ü¡Ì¤ìÉ•Á…¥É¥¹œ½¹±ä€ˆ(€€€€€€€€€€€€€€€€‰Ñ¡•¥ÈÉ¥ µÑ•áĞ™¥•±‘Ì‰•™½É”‘•Á½Í¥Ğ¸ˆ°(€€€€€€€€€€€€€€€±•Ù•°ô‰İ…É¹¥¹œˆ°(€€€€€€€€€€€€¤((€€€€€€€™…¥±•‘}¥¹‘•á•Ì€ôÍ½ÉÑ•¡ì(€€€€€€€€€€€•ÉÉ½Él‰É½İ}¥¹‘•à‰t™½È•ÉÉ½È¥¸É¥¡}Ñ•áÑ}•ÉÉ½ÉÌ(€€€€€€€€€€€¥˜¥Í¥¹ÍÑ…¹”¡•ÉÉ½È¹•Ğ ‰É½İ}¥¹‘•àˆ¤°¥¹Ğ¤(€€€€€€€€€€€…¹•ÉÉ½Él‰É½İ}¥¹‘•à‰t€øô€À(€€€€€€€ô¤(€€€€€€€™…¥±•‘}É½İÌ€ôl(€€€€€€€€€€€É•Á…¥É•‘m¥¹‘•át™½È¥¹‘•à¥¸™…¥±•‘}¥¹‘•á•Ì(€€€€€€€€€€€¥˜¥¹‘•à€ğ±•¸¡É•Á…¥É•¤(€€€€€€€t(€€€€€€€¥˜¹½Ğ™…¥±•‘}É½İÌè(€€€€€€€€€€€‰É•…¬((€€€€€€€‘•Ñ•Éµ¥¹¥ÍÑ¥Œ€ô½Áä¹‘••Á½Áä¡É•Á…¥É•¤(€€€€€€€‘•Ñ•Éµ¥¹¥ÍÑ¥}…ÁÁ±¥•€ô€À(€€€€€€€™½ÈÉ½İ}¥¹‘•à¥¸™…¥±•‘}¥¹‘•á•Ìè(€€€€€€€€€€€¥˜É½İ}¥¹‘•à€øô±•¸¡‘•Ñ•Éµ¥¹¥ÍÑ¥Œ¤è(€€€€€€€€€€€€€€€½¹Ñ¥¹Õ”(€€€€€€€€€€€ÕÉÉ•¹Ñ}‘•Ñ…¥±Ì€ôÍÑÈ (€€€€€€€€€€€€€€€É•Á…¥É•‘mÉ½İ}¥¹‘•át¹•Ğ ‰½¹•ÁÑ}‘•Ñ…¥±Ìˆ°€ˆˆ¤½È€ˆˆ¤(€€€€€€€€€€€‘•™•ÑÌ€ôÍ•Ğ¡­È¹É¥¡}Ñ•áÑ}¥ÍÍÕ•Ì¡ÕÉÉ•¹Ñ}‘•Ñ…¥±Ì¤¤(€€€€€€€€€€€¥˜€ (€€€€€€€€€€€€€€€¹½Ğ‘•™•ÑÌ(€€€€€€€€€€€€€€€½È¹½Ğ‘•™•ÑÌ¹¥ÍÍÕ‰Í•Ğ¡ì(€€€€€€€€€€€€€€€€€€€€‰É…İ}±…Ñ•àˆ°€‰É…İ}µ…Ñ¡}•áÁÉ•ÍÍ¥½¸ˆ°(€€€€€€€€€€€€€€€ô¤(€€€€€€€€€€€€¤è(€€€€€€€€€€€€€€€½¹Ñ¥¹Õ”(€€€€€€€€€€€…¹‘¥‘…Ñ•}‘•Ñ…¥±Ì€ô­È¹É•Á…¥É}Õ¹İÉ…ÁÁ•‘}µ…Ñ ¡ÕÉÉ•¹Ñ}‘•Ñ…¥±Ì¤(€€€€€€€€€€€¥˜€ (€€€€€€€€€€€€€€€…¹‘¥‘…Ñ•}‘•Ñ…¥±Ì€ôôÕÉÉ•¹Ñ}‘•Ñ…¥±Ì(€€€€€€€€€€€€€€€½È­È¹É¥¡}Ñ•áÑ}¥ÍÍÕ•Ì¡…¹‘¥‘…Ñ•}‘•Ñ…¥±Ì¤(€€€€€€€€€€€€€€€½È­È¹Õ¹İÉ…Á}­…Ñ•à¡…¹‘¥‘…Ñ•}‘•Ñ…¥±Ì¤(€€€€€€€€€€€€€€€€„ô­È¹Õ¹İÉ…Á}­…Ñ•à¡ÕÉÉ•¹Ñ}‘•Ñ…¥±Ì¤(€€€€€€€€€€€€¤è(€€€€€€€€€€€€€€€½¹Ñ¥¹Õ”(€€€€€€€€€€€‘•Ñ•Éµ¥¹¥ÍÑ¥mÉ½İ}¥¹‘•ául‰½¹•ÁÑ}‘•Ñ…¥±Ì‰t€ô…¹‘¥‘…Ñ•}‘•Ñ…¥±Ì(€€€€€€€€€€€‘•Ñ•Éµ¥¹¥ÍÑ¥}…ÁÁ±¥•€¬ô€Ä((€€€€€€€¥˜‘•Ñ•Éµ¥¹¥ÍÑ¥}…ÁÁ±¥•è(€€€€€€€€€€€¥˜€ (€€€€€€€€€€€€€€€‘•Ñ•Éµ¥¹¥ÍÑ¥Œ€„ôÉ•Á…¥É•(€€€€€€€€€€€€€€€…¹™½Éµ…ÑÑ¥¹}½¹±å}É•Ù¥•İ}¥Í}Í…™”¡É•Á…¥É•°‘•Ñ•Éµ¥¹¥ÍÑ¥Œ¤(€€€€€€€€€€€€¤è(€€€€€€€€€€€€€€€É•Á…¥É•€ô‘•Ñ•Éµ¥¹¥ÍÑ¥Œ(€€€€€€€€€€€€€€€ÁÉ½É•ÍÌ¹±½œ (€€€€€€€€€€€€€€€€€€€€‰¥¹…°É¥ µÑ•áĞÉ•Á…¥È‘•Ñ•Éµ¥¹¥ÍÑ¥…±±äİÉ…ÁÁ•€ˆ(€€€€€€€€€€€€€€€€€€€˜‰Õ¹…µ‰¥Õ½ÕÌµ…Ñ ¥¸í‘•Ñ•Éµ¥¹¥ÍÑ¥}…ÁÁ±¥•‘ôÉ½Ü¡Ì¤¸ˆ°(€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€É•Á½ÉĞ€ôØ¹Ù…±¥‘…Ñ•}½¹•ÁÑ}É½İÌ (€€€€€€€€€€€€€€€€€€€É•Á…¥É•°€¨©Ù…±¥‘…Ñ¥½¹}…ÉÌ¤(€€€€€€€€€€€€€€€É¥¡}Ñ•áÑ}•ÉÉ½ÉÌ€ôl(€€€€€€€€€€€€€€€€€€€•ÉÉ½È™½È•ÉÉ½È¥¸É•Á½ÉÑl‰•ÉÉ½ÉÌ‰t(€€€€€€€€€€€€€€€€€€€¥˜€ (€€€€€€€€€€€€€€€€€€€€€€€•ÉÉ½È¹•Ğ ‰Í•Ù•É¥Ñäˆ¤€ôô€‰•ÉÉ½Èˆ(€€€€€€€€€€€€€€€€€€€€€€€…¹•ÉÉ½È¹•Ğ ‰½‘”ˆ¤€ôô€‰É¥¡}Ñ•áÑ}™½Éµ…Ğˆ(€€€€€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€t(€€€€€€€€€€€€€€€¥˜¹½ĞÉ¥¡}Ñ•áÑ}•ÉÉ½ÉÌè(€€€€€€€€€€€€€€€€€€€‰É•…¬(€€€€€€€€€€€€€€€™…¥±•‘}¥¹‘•á•Ì€ôÍ½ÉÑ•¡ì(€€€€€€€€€€€€€€€€€€€•ÉÉ½Él‰É½İ}¥¹‘•à‰t™½È•ÉÉ½È¥¸É¥¡}Ñ•áÑ}•ÉÉ½ÉÌ(€€€€€€€€€€€€€€€€€€€¥˜¥Í¥¹ÍÑ…¹”¡•ÉÉ½È¹•Ğ ‰É½İ}¥¹‘•àˆ¤°¥¹Ğ¤(€€€€€€€€€€€€€€€€€€€…¹•ÉÉ½Él‰É½İ}¥¹‘•à‰t€øô€À(€€€€€€€€€€€€€€€ô¤(€€€€€€€€€€€€€€€™…¥±•‘}É½İÌ€ôl(€€€€€€€€€€€€€€€€€€€É•Á…¥É•‘m¥¹‘•át™½È¥¹‘•à¥¸™…¥±•‘}¥¹‘•á•Ì(€€€€€€€€€€€€€€€€€€€¥˜¥¹‘•à€ğ±•¸¡É•Á…¥É•¤(€€€€€€€€€€€€€€€t(€€€€€€€€€€€€€€€¥˜¹½Ğ™…¥±•‘}É½İÌè(€€€€€€€€€€€€€€€€€€€‰É•…¬(€€€€€€€€€€€•±¥˜‘•Ñ•Éµ¥¹¥ÍÑ¥Œ€„ôÉ•Á…¥É•è(€€€€€€€€€€€€€€€ÁÉ½É•ÍÌ¹±½œ (€€€€€€€€€€€€€€€€€€€€‰I•©•Ñ•‘•Ñ•Éµ¥¹¥ÍÑ¥Œ™¥¹…°É¥ µÑ•áĞÉ•Á…¥È‰•…ÕÍ”€ˆ(€€€€€€€€€€€€€€€€€€€€‰¥Ğ¡…¹•„ÁÉ½Ñ•Ñ•É½Ü½ÈÍ½ÕÉ”µ¥¹Ù•¹Ñ½Éä¥¹Ù…É¥…¹Ğ¸ˆ°(€€€€€€€€€€€€€€€€€€€±•Ù•°ô‰İ…É¹¥¹œˆ°(€€€€€€€€€€€€€€€€¤((€€€€€€€¥µÁ½ÉĞ©Í½¸…Ì}©Í½¸((€€€€€€€ÕÍ•È€ô€ (€€€€€€€€€€€}µ•Ñ…‘…Ñ…}‰±½¬¡µ•Ñ„¤(€€€€€€€€€€€€¬€‰q¹MÑ…”è™¥¹…°É¥ µÑ•áĞ™½Éµ…ÑÑ¥¹q¹Y…±¥‘…Ñ¥½¸•ÉÉ½ÉÌéq¸ˆ(€€€€€€€€€€€€¬}©Í½¸¹‘ÕµÁÌ¡É¥¡}Ñ•áÑ}•ÉÉ½ÉÌ°•¹ÍÕÉ•}…Í¥¤õ…±Í”¤(€€€€€€€€€€€€¬€‰q¹…¥±•É½İÌéq¸ˆ(€€€€€€€€€€€€¬}©Í½¸¹‘ÕµÁÌ (€€€€€€€€€€€€€€€ì‰É½İÌˆè}É•½É‘Í}Ñ½}…Á¥}É½İÌ¡™…¥±•‘}É½İÌ¥ô°(€€€€€€€€€€€€€€€•¹ÍÕÉ•}…Í¥¤õ…±Í”°(€€€€€€€€€€€€¤(€€€€€€€€¤(€€€€€€€‘…Ñ„€ô}½Á•¹…¥}©Í½¸ (€€€€€€€€€€€ÁÉ½µÁÑÌ¹•Ñ}Ñ•áĞ ‰½¹•ÁÑÌ¹É•Á…¥È¹ÍåÍÑ•´ˆ¤(€€€€€€€€€€€€¬€‰q¹q¸ˆ(€€€€€€€€€€€€¬­È¹AI=5AQ}AI5	1°(€€€€€€€€€€€ÕÍ•È°(€€€€€€€€€€€ÁÕÉÁ½Í”ô‰½¹•ÁÑ}Ù…±¥‘…Ñ¥½¸ˆ°(€€€€€€€€¤(€€€€€€€…¹‘¥‘…Ñ•Ì€ô}½¹•ÁÑ}É½İÍ}Ñ½}É•½É‘Ì¡‘…Ñ„¤(€€€€€€€¥˜¹½Ğ…¹‘¥‘…Ñ•Ìè(€€€€€€€€€€€ÁÉ½É•ÍÌ¹±½œ (€€€€€€€€€€€€€€€€‰¥¹…°É¥ µÑ•áĞÉ•Á…¥ÈÉ•ÑÕÉ¹•¹¼É½İÌ¸ˆ°(€€€€€€€€€€€€€€€±•Ù•°ô‰İ…É¹¥¹œˆ°(€€€€€€€€€€€€¤(€€€€€€€€€€€‰É•…¬((€€€€€€€…¹‘¥‘…Ñ•}‰å}­•ä€ôì(€€€€€€€€€€€}É•½É‘}­•ä¡…¹‘¥‘…Ñ”¤è…¹‘¥‘…Ñ”(€€€€€€€€€€€™½È…¹‘¥‘…Ñ”¥¸…¹‘¥‘…Ñ•Ì(€€€€€€€ô(€€€€€€€…¹‘¥‘…Ñ•}‰å}Ñ¥Ñ±”€ôì(€€€€€€€€€€€‰¤¹¹½Éµ…±¥é•}ÅÕ•ÍÑ¥½¹}Ñ•áĞ (€€€€€€€€€€€€€€€…¹‘¥‘…Ñ”¹•Ğ ‰½¹•ÁÑ}Ñ¥Ñ±”ˆ°€ˆˆ¤(€€€€€€€€€€€€¤è…¹‘¥‘…Ñ”(€€€€€€€€€€€™½È…¹‘¥‘…Ñ”¥¸…¹‘¥‘…Ñ•Ì(€€€€€€€ô(€€€€€€€¹•áÑ}É•½É‘Ì€ô½Áä¹‘••Á½Áä¡É•Á…¥É•¤(€€€€€€€…ÁÁ±¥•€ô€À(€€€€€€€™½ÈÁ½Í¥Ñ¥½¸°É½İ}¥¹‘•à¥¸•¹Õµ•É…Ñ”¡™…¥±•‘}¥¹‘•á•Ì¤è(€€€€€€€€€€€¥˜É½İ}¥¹‘•à€øô±•¸¡¹•áÑ}É•½É‘Ì¤è(€€€€€€€€€€€€€€€½¹Ñ¥¹Õ”(€€€€€€€€€€€ÕÉÉ•¹Ğ€ôÉ•Á…¥É•‘mÉ½İ}¥¹‘•át(€€€€€€€€€€€…¹‘¥‘…Ñ”€ô9½¹”(€€€€€€€€€€€¥˜±•¸¡…¹‘¥‘…Ñ•Ì¤€ôô±•¸¡™…¥±•‘}¥¹‘•á•Ì¤è(€€€€€€€€€€€€€€€Á½Í¥Ñ¥½¹…°€ô…¹‘¥‘…Ñ•ÍmÁ½Í¥Ñ¥½¹t(€€€€€€€€€€€€€€€¥˜€ (€€€€€€€€€€€€€€€€€€€}É•½É‘}­•ä¡Á½Í¥Ñ¥½¹…°¤€ôô}É•½É‘}­•ä¡ÕÉÉ•¹Ğ¤(€€€€€€€€€€€€€€€€€€€½È‰¤¹¹½Éµ…±¥é•}ÅÕ•ÍÑ¥½¹}Ñ•áĞ (€€€€€€€€€€€€€€€€€€€€€€€Á½Í¥Ñ¥½¹…°¹•Ğ ‰½¹•ÁÑ}Ñ¥Ñ±”ˆ°€ˆˆ¤(€€€€€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€€€€€€ôô‰¤¹¹½Éµ…±¥é•}ÅÕ•ÍÑ¥½¹}Ñ•áĞ (€€€€€€€€€€€€€€€€€€€€€€€ÕÉÉ•¹Ğ¹•Ğ ‰½¹•ÁÑ}Ñ¥Ñ±”ˆ°€ˆˆ¤(€€€€€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€€¤è(€€€€€€€€€€€€€€€€€€€…¹‘¥‘…Ñ”€ôÁ½Í¥Ñ¥½¹…°(€€€€€€€€€€€¥˜…¹‘¥‘…Ñ”¥Ì9½¹”è(€€€€€€€€€€€€€€€…¹‘¥‘…Ñ”€ô€ (€€€€€€€€€€€€€€€€€€€…¹‘¥‘…Ñ•}‰å}­•ä¹•Ğ¡}É•½É‘}­•ä¡ÕÉÉ•¹Ğ¤¤(€€€€€€€€€€€€€€€€€€€½È…¹‘¥‘…Ñ•}‰å}Ñ¥Ñ±”¹•Ğ (€€€€€€€€€€€€€€€€€€€€€€€‰¤¹¹½Éµ…±¥é•}ÅÕ•ÍÑ¥½¹}Ñ•áĞ (€€€€€€€€€€€€€€€€€€€€€€€€€€€ÕÉÉ•¹Ğ¹•Ğ ‰½¹•ÁÑ}Ñ¥Ñ±”ˆ°€ˆˆ¤(€€€€€€€€€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€‘•Ñ…¥±Ì€ô€ (€€€€€€€€€€€€€€€…¹‘¥‘…Ñ”¹•Ğ ‰½¹•ÁÑ}‘•Ñ…¥±Ìˆ°€ˆˆ¤(€€€€€€€€€€€€€€€¥˜¥Í¥¹ÍÑ…¹”¡…¹‘¥‘…Ñ”°‘¥Ğ¤(€€€€€€€€€€€€€€€•±Í”€ˆˆ(€€€€€€€€€€€€¤(€€€€€€€€€€€¥˜¹½ĞÍÑÈ¡‘•Ñ…¥±Ì½È€ˆˆ¤¹ÍÑÉ¥À ¤è(€€€€€€€€€€€€€€€½¹Ñ¥¹Õ”(€€€€€€€€€€€…¹‘¥‘…Ñ•}É½Ü€ô½Áä¹‘••Á½Áä¡ÕÉÉ•¹Ğ¤(€€€€€€€€€€€…¹‘¥‘…Ñ•}É½İl‰½¹•ÁÑ}‘•Ñ…¥±Ì‰t€ô‘•Ñ…¥±Ì(€€€€€€€€€€€…¹‘¥‘…Ñ•}É½Ü€ô}…¹½¹¥…±¥é•}½¹•ÁÑ}É¥¡}Ñ•áĞ (€€€€€€€€€€€€€€€m…¹‘¥‘…Ñ•}É½İt¥lÁt(€€€€€€€€€€€‘•Ñ…¥±Ì€ô…¹‘¥‘…Ñ•}É½İl‰½¹•ÁÑ}‘•Ñ…¥±Ì‰t(€€€€€€€€€€€¥˜İÉ…ÁÁ•É}½¹±å}­•ä¡‘•Ñ…¥±Ì¤€„ôİÉ…ÁÁ•É}½¹±å}­•ä (€€€€€€€€€€€€€€€ÕÉÉ•¹Ğ¹•Ğ ‰½¹•ÁÑ}‘•Ñ…¥±Ìˆ°€ˆˆ¤(€€€€€€€€€€€€¤è(€€€€€€€€€€€€€€€ÁÉ½É•ÍÌ¹±½œ (€€€€€€€€€€€€€€€€€€€€‰I•©•Ñ•™¥¹…°É¥ µÑ•áĞÉ•Á…¥È™½È€ˆ(€€€€€€€€€€€€€€€€€€€˜‰É½İ}¥¹‘•àõíÉ½İ}¥¹‘•áôèÉ•ÍÁ½¹Í”¡…¹•½¹Ñ•¹Ğ‰•å½¹€ˆ(€€€€€€€€€€€€€€€€€€€€‰-…Ñ•àİÉ…ÁÁ•ÉÌ¸ˆ°(€€€€€€€€€€€€€€€€€€€±•Ù•°ô‰İ…É¹¥¹œˆ°(€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€½¹Ñ¥¹Õ”(€€€€€€€€€€€€ŒI¥ µÑ•áĞÉ•Á…¥È¥Ì¹½Ğ„Í•µ…¹Ñ¥Œ•‘¥Ğè­••ÀÑ½Á¥Œ°Ñ¥Ñ±”°(€€€€€€€€€€€€ŒÁ…É•¹Ğ°­•åİ½É‘Ì°•Ù¥‘•¹”°ÁÉ½Í”°™½ÉµÕ±…Ì°…¹•Ù•Éä½Ñ¡•ÈÉ½Ü(€€€€€€€€€€€€Œ™¥•±¥µµÕÑ…‰±”¸(€€€€€€€€€€€¹•áÑ}É•½É‘ÍmÉ½İ}¥¹‘•ául‰½¹•ÁÑ}‘•Ñ…¥±Ì‰t€ô‘•Ñ…¥±Ì(€€€€€€€€€€€…ÁÁ±¥•€¬ô€Ä(€€€€€€€¥˜¹½Ğ…ÁÁ±¥•è(€€€€€€€€€€€ÁÉ½É•ÍÌ¹±½œ (€€€€€€€€€€€€€€€€‰¥¹…°É¥ µÑ•áĞÉ•Á…¥ÈÉ•ÑÕÉ¹•¹¼µ…Ñ¡¥¹œÉ½Ü‘•Ñ…¥±Ì¸ˆ°(€€€€€€€€€€€€€€€±•Ù•°ô‰İ…É¹¥¹œˆ°(€€€€€€€€€€€€¤(€€€€€€€€€€€‰É•…¬((€€€€€€€¹•áÑ}É•½É‘Ì€ô}…¹½¹¥…±¥é•}½¹•ÁÑ}É¥¡}Ñ•áĞ¡¹•áÑ}É•½É‘Ì¤(€€€€€€€¥˜¹½Ğ™½Éµ…ÑÑ¥¹}½¹±å}É•Ù¥•İ}¥Í}Í…™”¡É•Á…¥É•°¹•áÑ}É•½É‘Ì¤è(€€€€€€€€€€€ÁÉ½É•ÍÌ¹±½œ (€€€€€€€€€€€€€€€€‰I•©•Ñ•™¥¹…°É¥ µÑ•áĞÉ•Á…¥È‰•…ÕÍ”¥Ğ¡…¹•„€ˆ(€€€€€€€€€€€€€€€€‰ÁÉ½Ñ•Ñ•É½Ü½ÈÍ½ÕÉ”µ¥¹Ù•¹Ñ½Éä¥¹Ù…É¥…¹Ğ¸ˆ°(€€€€€€€€€€€€€€€±•Ù•°ô‰İ…É¹¥¹œˆ°(€€€€€€€€€€€€¤(€€€€€€€€€€€¹•áÑ}É•½É‘Ì€ôÉ•Á…¥É•(€€€€€€€¹•áÑ}É•½É‘Ì€ô}…•ÁÑ}•á…Ñ}¥¹Ù•¹Ñ½Éå}ÑåÁ•}É•Ù¥•Ü (€€€€€€€€€€€É•Á…¥É•°¹•áÑ}É•½É‘Ì°¥¹Ù•¹Ñ½Éä°µ¥¹•‘}ÑåÁ•Ì¤(€€€€€€€¥˜¹•áÑ}É•½É‘Ì€ôôÉ•Á…¥É•è(€€€€€€€€€€€ÁÉ½É•ÍÌ¹±½œ (€€€€€€€€€€€€€€€€‰¥¹…°É¥ µÑ•áĞÉ•Á…¥Èµ…‘”¹¼Í…™”™½Éµ…ÑÑ¥¹œ¡…¹”¸ˆ°(€€€€€€€€€€€€€€€±•Ù•°ô‰İ…É¹¥¹œˆ°(€€€€€€€€€€€€¤(€€€€€€€€€€€‰É•…¬(€€€€€€€É•Á…¥É•€ô¹•áÑ}É•½É‘Ì(€€€€€€€ÁÉ½É•ÍÌ¹±½œ (€€€€€€€€€€€˜‰¥¹…°É¥ µÑ•áĞÉ•Á…¥ÈÕÁ‘…Ñ•í…ÁÁ±¥•‘ôÉ½Ü¡Ì¤½¸€ˆ(€€€€€€€€€€€˜‰…ÑÑ•µÁĞí…ÑÑ•µÁÑô¸ˆ°(€€€€€€€€¤((€€€¥˜¹½Ğ¥¹¥Ñ¥…±}•ÉÉ½É}½Õ¹Ğè(€€€€€€€É•ÑÕÉ¸É•½É‘Ì°…±Í”(€€€É•µ…¥¹¥¹}É•Á½ÉĞ€ôØ¹Ù…±¥‘…Ñ•}½¹•ÁÑ}É½İÌ¡É•Á…¥É•°€¨©Ù…±¥‘…Ñ¥½¹}…ÉÌ¤(€€€É•µ…¥¹¥¹œ€ôl(€€€€€€€•ÉÉ½È™½È•ÉÉ½È¥¸É•µ…¥¹¥¹}É•Á½ÉÑl‰•ÉÉ½ÉÌ‰t(€€€€€€€¥˜€ (€€€€€€€€€€€•ÉÉ½È¹•Ğ ‰Í•Ù•É¥Ñäˆ¤€ôô€‰•ÉÉ½Èˆ(€€€€€€€€€€€…¹•ÉÉ½È¹•Ğ ‰½‘”ˆ¤€ôô€‰É¥¡}Ñ•áÑ}™½Éµ…Ğˆ(€€€€€€€€¤(€€€t(€€€¥˜É•µ…¥¹¥¹œè(€€€€€€€ÁÉ½É•ÍÌ¹±½œ (€€€€€€€€€€€€‰¥¹…°É¥ µÑ•áĞÉ•Á…¥È±•™Ğ€ˆ(€€€€€€€€€€€˜‰í±•¸¡É•µ…¥¹¥¹œ¥ôµ…±™½Éµ•É½Ü¡Ì¤ìÍÑÉ¥ĞÙ…±¥‘…Ñ¥½¸İ¥±°€ˆ(€€€€€€€€€€€€‰É•Á½ÉĞÑ¡•¥È•á…Ğ±½…Ñ¥½¹Ì¸ˆ°(€€€€€€€€€€€±•Ù•°ô‰İ…É¹¥¹œˆ°(€€€€€€€€¤(€€€•±Í”è(€€€€€€€ÁÉ½É•ÍÌ¹±½œ (€€€€€€€€€€€€‰¥¹…°É¥ µÑ•áĞÉ•Á…¥È±•…É•…±°µ…±™½Éµ•É½İÌ¸ˆ°(€€€€€€€€€€€±•Ù•°ô‰ÍÕ•ÍÌˆ°(€€€€€€€€¤(€€€É•ÑÕÉ¸É•Á…¥É•°É•Á…¥É•€„ô½É¥¥¹…°(()‘•˜}É•É½Õ¹‘}‘É¥™Ñ•‘}™¥¹…±}Í½ÕÉ•}±…¥µÌ (€€€É•½É‘Ìè±¥ÍÑm‘¥Ñt°(¤€´ø±¥ÍÑm‘¥Ñtè(€€€€ˆˆ‰I”µÉ½Õ¹É½Üµ±½…°Á½ÍĞµ™É••é”‘É¥™Ğ•á…Ñ±ä½¹”°½È™…¥°±½Í•¸((€€€A¡…Í”€Ì¸ÄÍ•…±ÌÑ¡”Ñ½Á½±½ä‰•™½É”±…Ñ•È™½Éµ…ÑÑ¥¹œ°QåÁ”…±±½…Ñ¥½¸°…¹(€€€Ñ•Éµ¥¹…°É•Á…¥ÈÁ…ÍÍ•Ì¸€Q¡½Í”Á…ÍÍ•Ì…É”…±±½İ•Ñ¼É•‰Õ¥±„É½Ü°‰ÕĞ„(€€€¡…¹••ÍÉ¥ÁÑ¥½¸¥Ì„¹•ÜÍ½ÕÉ”±…¥´…¹µÕÍĞ¹½Ğ¥¹¡•É¥ĞÑ¡”½±(€€€•Ù¥‘•¹”Ù•É‘¥Ğ¸€Q¡”½É‘•É•±¥¹•…”¥Ì¡•­•™¥ÉÍĞ°Í¼Ñ¡¥ÌÉ•½Ù•Éä(€€€…¸¹•Ù•È±•¥Ñ¥µ¥é”„‘•±•Ñ•°‘ÕÁ±¥…Ñ•°½ÈÉ•½É‘•É•É½Õ¹‘•½¹•ÁĞ¸((€€€=¹±äÉ½İÌİ¡½Í”…ÑÑ•ÍÑ…Ñ¥½¸¹¼±½¹•ÈÙ•É¥™¥•Ì¡…Ù”Ñ¡•¥È‘•É¥Ù••Ù¥‘•¹”(€€€™¥•±‘Ì±•…É•¸€A¡…Í”€Ì¸ÄÌ±…¥´µ…‘‘É•ÍÍ•…¡”Ñ¡•É•™½É”É•ÕÍ•Ì•Ù•Éä(€€€Õ¹¡…¹•ÁÉ½Ù¥‘•È½É¥Ñ¥ŒÉ•ÍÕ±Ğ…¹Í•¹‘Ì½¹±ä¡…¹•±…¥µÌÑ¡É½Õ Ñ¡”(€€€É•…°¥¹‘•Á•¹‘•¹ĞÉ½Õ¹‘¥¹œÁ…¥È¸€Q¡•É”¥Ì‘•±¥‰•É…Ñ•±ä¹¼É•ÑÉä¡•É”è„(€€€‘¥Í…É••µ•¹ĞÕ¹İ¥¹‘ÌÑ¼Ñ¡”•á¥ÍÑ¥¹œ‰½Õ¹‘•Í•µ…¹Ñ¥ŒµÉ•Í½±ÕÑ¥½¸İ½É­™±½Ü¸(€€€€ˆˆˆ((€€€™É½´€¸¥µÁ½ÉĞ…¹½¹¥…±}Í½ÕÉ•}Á¡…Í”Ì…ÌÁ¡…Í”Ì((€€€É…Á €ôÁ¡…Í”Ì¹…Ñ¥Ù•}É…Á  ¤(€€€¥˜¹½Ğ¥Í¥¹ÍÑ…¹”¡É…Á °‘¥Ğ¤½È¹½ĞÉ•½É‘Ìè(€€€€€€€É•ÑÕÉ¸É•½É‘Ì((€€€€ŒY•É¥™äÑ½Á½±½ä¥‘•¹Ñ¥ÑäÍ•Á…É…Ñ•±ä™É½´É½Ü½¹Ñ•¹ÑÌ¸É½Üµ±½…°(€€€€Œ•ÍÉ¥ÁÑ¥½¸É•İÉ¥Ñ”É•Ñ…¥¹ÌÑ¡”½±É½Ü•ÉÑ¥™¥…Ñ”…¹Á…ÍÍ•ÌÑ¡¥Ì(€€€€Œ¡•¬ì„‘É½ÁÁ•½É•½É‘•É•É½Ü…¹¹½Ğ‰”Í¥±•¹Ñ±äÉ•Í•…±•…Ì„ÍÕ•ÍÌ¸(€€€É½Õ¹‘¥¹}•ÉÑ¥™¥…Ñ”¹Ù•É¥™å}±¥¹•…”¡É•½É‘Ì¤(€€€‘É¥™Ñ•è±¥ÍÑm¥¹Ñt€ômt(€€€…Ñ¥Ù•}Í½ÕÉ•}½¹ÑÉ…Ğ€ôÍÑÈ (€€€€€€€É…Á ¹•Ğ ‰Í½ÕÉ•}½¹ÑÉ…Ñ}¡…Í ˆ¤½È€ˆˆ(€€€€¤¹ÍÑÉ¥À ¤(€€€…Ñ¥Ù•}Í•µ…¹Ñ¥}Ñ½Á½±½ä€ô€ (€€€€€€€É½Õ¹‘¥¹}•ÉÑ¥™¥…Ñ”¹Í•µ…¹Ñ¥}Ñ½Á½±½å}Í¡„ÈÔØ¡É…Á ¤(€€€€¤(€€€™½È¥¹‘•à°É•½É¥¸•¹Õµ•É…Ñ”¡É•½É‘Ì¤è(€€€€€€€€ŒÉ•¹…µ”°Á…É•¹ĞÉ•…ÍÍ¥¹µ•¹Ğ°½ÈÑ½Á¥Œµ½Ù”¥Ì„Ñ½Á½±½ä¡…¹”°(€€€€€€€€Œ¹½Ğ…¸•Ù¥‘•¹”É•Á…¥È¸%ĞµÕÍĞÉ•ÑÕÉ¸Ñ¼Ñ½Á½±½ä…‘©Õ‘¥…Ñ¥½¸…¹(€€€€€€€€Œµ…ä¹•Ù•È‰”±•¥Ñ¥µ¥é•‰äÑ¡¥ÌÉ½Üµ±½…°É”µÉ½Õ¹‘¥¹œÍ•…´¸(€€€€€€€É½Õ¹‘¥¹}•ÉÑ¥™¥…Ñ”¹Ù•É¥™å}É½İ}¥‘•¹Ñ¥Ñä (€€€€€€€€€€€É•½É°É½İ}¥¹‘•àõ¥¹‘•à(€€€€€€€€¤(€€€€€€€…ÑÑ•ÍÑ•‘}Í½ÕÉ•}½¹ÑÉ…Ğ€ôÍÑÈ (€€€€€€€€€€€É•½É¹•Ğ¡É½Õ¹‘¥¹}•ÉÑ¥™¥…Ñ”¹M=UI}=9QIQ}%1¤½È€ˆˆ(€€€€€€€€¤¹ÍÑÉ¥À ¤(€€€€€€€¥˜€ (€€€€€€€€€€€¹½Ğ…Ñ¥Ù•}Í½ÕÉ•}½¹ÑÉ…Ğ(€€€€€€€€€€€½È…ÑÑ•ÍÑ•‘}Í½ÕÉ•}½¹ÑÉ…Ğ€„ô…Ñ¥Ù•}Í½ÕÉ•}½¹ÑÉ…Ğ(€€€€€€€€¤è(€€€€€€€€€€€É…¥Í”É½Õ¹‘¥¹}•ÉÑ¥™¥…Ñ”¹É½Õ¹‘¥¹•ÉÑ¥™¥…Ñ•ÉÉ½È (€€€€€€€€€€€€€€€€‰É½Õ¹‘¥¹œ•ÉÑ¥™¥…Ñ”Í½ÕÉ”½Ñ½Á½±½ä½¹ÑÉ…Ğ‘É¥™Ğ™½È€ˆ(€€€€€€€€€€€€€€€˜‰=9APµI=U9µí¥¹‘•à€¬€ÄèÀÑ‘ôè…ÑÑ•ÍÑ•€ˆ(€€€€€€€€€€€€€€€˜‰í…ÑÑ•ÍÑ•‘}Í½ÕÉ•}½¹ÑÉ…Ğ½È€µ¥ÍÍ¥¹œô°…Ñ¥Ù”€ˆ(€€€€€€€€€€€€€€€˜‰í…Ñ¥Ù•}Í½ÕÉ•}½¹ÑÉ…Ğ½È€µ¥ÍÍ¥¹œôˆ(€€€€€€€€€€€€¤(€€€€€€€…ÑÑ•ÍÑ•‘}Í•µ…¹Ñ¥}Ñ½Á½±½ä€ôÍÑÈ (€€€€€€€€€€€É•½É¹•Ğ¡É½Õ¹‘¥¹}•ÉÑ¥™¥…Ñ”¹M59Q%}Q=A=1=e}%1¤½È€ˆˆ(€€€€€€€€¤¹ÍÑÉ¥À ¤(€€€€€€€¥˜…ÑÑ•ÍÑ•‘}Í•µ…¹Ñ¥}Ñ½Á½±½ä€„ô…Ñ¥Ù•}Í•µ…¹Ñ¥}Ñ½Á½±½äè(€€€€€€€€€€€É…¥Í”É½Õ¹‘¥¹}•ÉÑ¥™¥…Ñ”¹É½Õ¹‘¥¹•ÉÑ¥™¥…Ñ•ÉÉ½È (€€€€€€€€€€€€€€€€‰É½Õ¹‘¥¹œ•ÉÑ¥™¥…Ñ”Í•µ…¹Ñ¥ŒÉ…Á ½Ñ½Á½±½ä‘É¥™Ğ™½È€ˆ(€€€€€€€€€€€€€€€˜‰=9APµI=U9µí¥¹‘•à€¬€ÄèÀÑ‘ôèÑ¡”…Ñ¥Ù”Ñ½Á¥Œ°€ˆ(€€€€€€€€€€€€€€€€‰ÍÕ‰Ñ½Á¥Œ°½È‰±½¬µ…ÁÁ¥¹œ¡…¹•…™Ñ•ÈÉ½Õ¹‘¥¹œˆ(€€€€€€€€€€€€¤(€€€€€€€ÑÉäè(€€€€€€€€€€€É½Õ¹‘¥¹}•ÉÑ¥™¥…Ñ”¹Ù•É¥™å}É½Ü¡É•½É°É½İ}¥¹‘•àõ¥¹‘•à¤(€€€€€€€•á•ÁĞÉ½Õ¹‘¥¹}•ÉÑ¥™¥…Ñ”¹É½Õ¹‘¥¹•ÉÑ¥™¥…Ñ•ÉÉ½Èè(€€€€€€€€€€€‘É¥™Ñ•¹…ÁÁ•¹¡¥¹‘•à¤(€€€¥˜¹½Ğ‘É¥™Ñ•è(€€€€€€€É•ÑÕÉ¸É•½É‘Ì((€€€…¹‘¥‘…Ñ”€ô½Áä¹‘••Á½Áä¡É•½É‘Ì¤(€€€™½È¥¹‘•à¥¸‘É¥™Ñ•è(€€€€€€€É½Ü€ô…¹‘¥‘…Ñ•m¥¹‘•át(€€€€€€€™½È™¥•±¥¸±¥ÍĞ¡É½Ü¤è(€€€€€€€€€€€¥˜™¥•±¹ÍÑ…ÉÑÍİ¥Ñ  ‰}Í½ÕÉ•}É½Õ¹‘¥¹|ˆ¤è(€€€€€€€€€€€€€€€É½Ü¹Á½À¡™¥•±°9½¹”¤(€€€€€€€€ŒÙ¥‘•¹”…¹ÍÕ‰Ñ½Á¥ŒÁ±…•µ•¹Ğ…É”½¹±ÕÍ¥½¹Ì‘•É¥Ù•™É½´Ñ¡”(€€€€€€€€Œ±…¥´¸MÑ…‰±”µ…¥¸µÑ½Á¥Œ¥‘•¹Ñ¥ÑäÉ•µ…¥¹ÌÍ¼„™½Éµ…ÑÑ¥¹œÉ•Á…¥È(€€€€€€€€Œ…¹¹½ĞÍµÕ±”¥¸…¸Õ¹É•Ù¥•İ•Ñ½Á½±½äµ½Ù”…ĞÑ¡¥Ì±…Ñ”‰½Õ¹‘…Éä¸(€€€€€€€É½Ü¹Á½À ‰}Í½ÕÉ•}‰±½­}¥‘Ìˆ°9½¹”¤(€€€€€€€É½Ü¹Á½À ‰}Í•µ…¹Ñ¥}ÍÕ‰Ñ½Á¥}¥ˆ°9½¹”¤(€€€€€€€É½Ü¹Á½À ‰}Í•µ…¹Ñ¥}ÍÕ‰Ñ½Á¥}¥‘Ìˆ°9½¹”¤((€€€™É½´Á…Ñ¡±¥ˆ¥µÁ½ÉĞA…Ñ ((€€€™É½´€¹Á¡…Í”Ì¥µÁ½ÉĞÉ•É½Õ¹…ÌÀÍ}É•É½Õ¹((€€€Í•ÍÍ¥½¸€ôÁ¡…Í”Ì¹…Ñ¥Ù•}Í•ÍÍ¥½¸ ¤(€€€…¹½¹¥…°€ô€ (€€€€€€€Í•ÍÍ¥½¸¹•Ğ ‰…¹½¹¥…°ˆ¤½Èíô(€€€€€€€¥˜¥Í¥¹ÍÑ…¹”¡Í•ÍÍ¥½¸°‘¥Ğ¤(€€€€€€€•±Í”íô(€€€€¤(€€€…ÉÑ¥™…Ñ}‘¥È€ô€ (€€€€€€€Í•ÍÍ¥½¸¹•Ğ ‰…ÉÑ¥™…Ñ}‘¥Èˆ¤¥˜¥Í¥¹ÍÑ…¹”¡Í•ÍÍ¥½¸°‘¥Ğ¤•±Í”9½¹”(€€€€¤(€€€ÍÑ½É•}‘¥È€ô€ (€€€€€€€A…Ñ ¡…ÉÑ¥™…Ñ}‘¥È¤€¼€‰Á¡…Í”Ìµ‘•¥Í¥½¹Ìˆ¥˜…ÉÑ¥™…Ñ}‘¥È•±Í”9½¹”(€€€€¤(€€€ÁÉ½É•ÍÌ¹±½œ (€€€€€€€€‰¥¹…°Í½ÕÉ”µ±…¥´¥¹Ñ•É¥Ñä¡•¬™½Õ¹€ˆ(€€€€€€€˜‰í±•¸¡‘É¥™Ñ•¥ôÉ½Üµ±½…°¡…¹”¡Ì¤ìÉ”µÉÕ¹¹¥¹œ•á…ĞÉ½Õ¹‘¥¹œ€ˆ(€€€€€€€€‰½¹”‰•™½É”•ÉÑ¥™¥…Ñ¥½¸¸ˆ°(€€€€€€€±•Ù•°ô‰İ…É¹¥¹œˆ°(€€€€¤(€€€É•É½Õ¹‘•€ôÀÍ}É•É½Õ¹¹É•É½Õ¹‘}É½İÌ (€€€€€€€…¹‘¥‘…Ñ”°(€€€€€€€‘É¥™Ñ•°(€€€€€€€É…Á õÉ…Á °(€€€€€€€…¹½¹¥…°õ…¹½¹¥…°°(€€€€€€€ÍÑ½É•}‘¥ÈõÍÑ½É•}‘¥È°(€€€€¤(€€€ÑÉäè(€€€€€€€€ŒI”µÉ½Õ¹‘¥¹œµÕÍĞÉ•ÑÕÉ¸½¹”½µÁ±•Ñ•±äÍ•…±•½É‘•É•Á…å±½…¸¼(€€€€€€€€Œ¹½Ğ±½½À½Èµ¥¹Ğ„Á…ÉÑ¥…°•ÉÑ¥™¥…Ñ”İ¡•¸„ÁÉ½Ù¥‘•È½É¥Ñ¥ŒÉ•ÍÕ±Ğ(€€€€€€€€Œİ…Ì…‰Í•¹Ğ°‘•Ñ•Éµ¥¹¥ÍÑ¥Œµ½¹±ä°½È½Ñ¡•Éİ¥Í”Õ¹Ù•É¥™¥•¸€Q¡”(€€€€€€€€Œ½µÁ±•Ñ”QåÁ”½…Í”E%±•‘•È¥Ì…ÑÑ…¡•½¹±ä…ĞÑ¡”Ñ•Éµ¥¹…°(€€€€€€€€Œ…±±½…Ñ¥½¸‰½Õ¹‘…Éä°Í¼Ñ¡¥ÌÉ½Üµ±½…°É”µÉ½Õ¹‘¥¹œ¡•¬(€€€€€€€€Œ‘•±¥‰•É…Ñ•±äÙ•É¥™¥•ÌÉ½ÜÍ•…±Ì…¹±¥¹•…”É…Ñ¡•ÈÑ¡…¸µ¥¹Ñ¥¹œ…¸(€€€€€€€€Œ¥¹Ñ•Éµ•‘¥…Ñ”™¥¹…°•ÉÑ¥™¥…Ñ”™É½´„Á…ÉÑ¥…°¹½Éµ…°µ¡½ÍĞµ…¹¥™•ÍĞ¸(€€€€€€€É½Õ¹‘¥¹}•ÉÑ¥™¥…Ñ”¹Ù•É¥™å}±¥¹•…”¡É•É½Õ¹‘•¤(€€€€€€€™½ÈÉ½İ}¥¹‘•à°É•½É¥¸•¹Õµ•É…Ñ”¡É•É½Õ¹‘•¤è(€€€€€€€€€€€É½Õ¹‘¥¹}•ÉÑ¥™¥…Ñ”¹Ù•É¥™å}É½Ü (€€€€€€€€€€€€€€€É•½É°É½İ}¥¹‘•àõÉ½İ}¥¹‘•à(€€€€€€€€€€€€¤(€€€•á•ÁĞÉ½Õ¹‘¥¹}•ÉÑ¥™¥…Ñ”¹É½Õ¹‘¥¹•ÉÑ¥™¥…Ñ•ÉÉ½È…Ì•áŒè(€€€€€€€É…¥Í”É½Õ¹‘¥¹}•ÉÑ¥™¥…Ñ”¹É½Õ¹‘¥¹•ÉÑ¥™¥…Ñ•ÉÉ½È (€€€€€€€€€€€€‰™¥¹…°Í½ÕÉ”µ±…¥´É”µÉ½Õ¹‘¥¹œ‘¥¹½ĞÁÉ½‘Õ”½¹”½µÁ±•Ñ”€ˆ(€€€€€€€€€€€˜‰¥¹‘•Á•¹‘•¹Ñ±äÙ•É¥™¥•Á…å±½…èí•áôˆ(€€€€€€€€¤™É½´•áŒ(€€€ÁÉ½É•ÍÌ¹±½œ (€€€€€€€€‰¥¹…°Í½ÕÉ”µ±…¥´¡…¹•ÌÁ…ÍÍ•½¹”•á…ĞÁÉ½Ù¥‘•È½É¥Ñ¥Œ€ˆ(€€€€€€€€‰É”µÉ½Õ¹‘¥¹œÁ…ÍÌ¸ˆ°(€€€€€€€±•Ù•°ô‰ÍÕ•ÍÌˆ°(€€€€¤(€€€É•ÑÕÉ¸É•É½Õ¹‘•(()‘•˜½¹•ÁÑÍ}™É½µ}µµ (€€€µµ‘}Ñ•áĞèÍÑÈ°€¨°ÍÕ‰©•ĞèÍÑÈ€ô€ˆˆ°‰½…ÉèÍÑÈ€ô€ˆˆ°É…‘”èÍÑÈ€ô€ˆˆ°(€€€Õ¹¥ĞèÍÑÈ€ô€ˆˆ°¡…ÁÑ•É}Ñ¥Ñ±”èÍÑÈ€ô€ˆˆ°¡…ÁÑ•É}¥è¥¹ĞğÍÑÈğ9½¹”€ô9½¹”°(€€€¡…ÁÑ•É}½‘”èÍÑÈ€ô€ˆˆ°±•…É¹¥¹}­¥¹èÍÑÈ€ô€‰A½ÍĞˆ°(€€€±¥Ù”è‰½½°ğ9½¹”€ô9½¹”°…ÉÑ¥™…ÑÌè‘¥Ğğ9½¹”€ô9½¹”°(€€€É•ÍÕµ•}¡•­Á½¥¹Ğè‘¥Ğğ9½¹”€ô9½¹”°(€€€¡•­Á½¥¹Ñ}…±±‰…¬õ9½¹”°(€€€½µÁ±•Ñ¥½¹}ÁÉ½É•ÍÌè™±½…Ğ€ô€Ä¸À°(€€€¥¹ÍÑÉÕÑ¥½¹}Í•Ñ}Í¡„ÈÔØèÍÑÈ€ô€ˆˆ°(€€€¥¹ÍÑÉÕÑ¥½¹}Í±½ÑÌè‘¥Ğğ9½¹”€ô9½¹”°(¤€´ø±¥ÍÑm‘¥Ñtè(€€€€ˆˆ‰A…ÉÍ”…¸55‘½Õµ•¹Ğ¥¹Ñ¼½¹•ÁĞÉ•½É‘Ì€¡Á½ÍĞµ±•…É¹¥¹œ¤¸((€€€1…É”¡…ÁÑ•ÉÌ…É”ÁÉ½•ÍÍ•¥¸½É‘•É•¡Õ¹­Ì€¡¹•Ù•ÈÑÉ¥µµ•¤…¹Ñ¡”(€€€Á•Èµ¡Õ¹¬½¹•ÁÑÌ…É”µ•É•°Í¼¹¼¡…ÁÑ•È½¹Ñ•¹Ğ¥Ì±½ÍĞ¸((€€€]¡•¸…ÉÑ¥™…ÑÍ€¥ÌÁÉ½Ù¥‘•¥Ğ¥Ì™¥±±•İ¥Ñ Ñ¡”¥¹Ñ•Éµ•‘¥…Ñ”(€€€ÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½Éå€…¹µ¥¹•‘}ÑåÁ•Í€Í¼…±±•ÉÌ…¸Á•ÉÍ¥ÍĞ(€€€Ñ¡•´€¡”¹œ¸™½ÈÑ¡”•áÑÉ…Ñ¥½¸µ½µÁ±•Ñ•¹•ÍÌMX‘½İ¹±½…¤¸(€€€€ˆˆˆ(€€€ÕÍ•}±¥Ù”€ô½¹™¥œ¹ÕÍ•}±¥Ù•}•¹•É…Ñ¥½¸ ¤¥˜±¥Ù”¥Ì9½¹”•±Í”±¥Ù”(€€€µ•Ñ„€ô}µ•Ñ…‘…Ñ„ (€€€€€€€ÍÕ‰©•ĞõÍÕ‰©•Ğ°‰½…Éõ‰½…É°É…‘”õÉ…‘”°Õ¹¥ĞõÕ¹¥Ğ°(€€€€€€€¡…ÁÑ•É}Ñ¥Ñ±”õ¡…ÁÑ•É}Ñ¥Ñ±”°¡…ÁÑ•É}¥õ¡…ÁÑ•É}¥°(€€€€€€€¡…ÁÑ•É}½‘”õ¡…ÁÑ•É}½‘”°±•…É¹¥¹}­¥¹õ±•…É¹¥¹}­¥¹°(€€€€€€€¥¹ÍÑÉÕÑ¥½¹}Í•Ñ}Í¡„ÈÔØõ¥¹ÍÑÉÕÑ¥½¹}Í•Ñ}Í¡„ÈÔØ°(€€€€€€€¥¹ÍÑÉÕÑ¥½¹}Í±½ÑÌõ¥¹ÍÑÉÕÑ¥½¹}Í±½ÑÌ°(€€€€¤(€€€¥˜ÕÍ•}±¥Ù”è(€€€€€€€ÁÉ½É•ÍÌ¹ÍÑ•À ‰½¹•ÁĞ•áÑÉ…Ñ¥½¸ƒŠPÁ…ÉÍ¥¹œÍ½ÕÉ”ÍÑÉÕÑÕÉ”ˆ°Ù…±Õ”ôÀ¸ÀÄ¤(€€€€€€€¡Õ¹­Ì€ô}Í•Ñ¥½¹}…İ…É•}¡Õ¹­Ì¡µµ‘}Ñ•áĞ¤(€€€€€€€Í•Ñ¥½¹Ì€ômÌ™½ÈŒ¥¸¡Õ¹­Ì™½ÈÌ¥¸l‰Í•Ñ¥½¹Ì‰ut(€€€€€€€¥˜}…ÁÁ±å}¡•…‘¥¹±•ÍÍ}¡…ÁÑ•É}Ñ½Á¥}™…±±‰…¬ (€€€€€€€€€€€Í•Ñ¥½¹Ì°¡…ÁÑ•É}Ñ¥Ñ±”(€€€€€€€€¤è(€€€€€€€€€€€ÁÉ½É•ÍÌ¹±½œ (€€€€€€€€€€€€€€€€‰9¼ÕÍ…‰±”Ñ•…¡¥¹œµÍ•Ñ¥½¸¡•…‘¥¹Ìİ•É”ÁÉ•Í•¹ĞìÕÍ¥¹œÑ¡”€ˆ(€€€€€€€€€€€€€€€€‰Í•±•Ñ•¡…ÁÑ•ÈÑ¥Ñ±”…ÌÑ¡”Í½ÕÉ”Ñ½Á¥Œ¸ˆ°(€€€€€€€€€€€€€€€±•Ù•°ô‰İ…É¹¥¹œˆ°(€€€€€€€€€€€€¤(€€€€€€€µ•Ñ¡½‘}…¹¡½ÉÌ€ô}µ•Ñ¡½‘}½Ù•É…•}…¹¡½ÉÌ¡Í•Ñ¥½¹Ì¤(€€€€€€€¡•…‘¥¹Ì€ô}Ñ½Á¥}¡•…‘¥¹Ì¡Í•Ñ¥½¹Ì¤(€€€€€€€Í½ÕÉ•}Ñ½Á¥}•á•ÉÁÑÌ€ô}É½ÕÁ}Í½ÕÉ•}Ñ½Á¥}•á•ÉÁÑÌ¡Í•Ñ¥½¹Ì¤(€€€€€€€€Œ5½‘•°µ‰…­•€¡±¥Ù•€¤•á•ÕÑ¥½¸¥Ì¹½Ğ…ÕÑ½µ…Ñ¥…±±ä„ÁÉ½‘ÕÑ¥½¸(€€€€€€€€ŒÁÕ‰±¥…Ñ¥½¸‰½Õ¹‘…Éä¸MÑ…¹‘…±½¹”ÑÉ…¹Í™½Éµ…Ñ¥½¹Ìµ…äÉ•ÍÕµ”Ñ¡•¥È(€€€€€€€€Œ•áÁ±¥¥Ñ±äÕ¹É½Õ¹‘•Ñ•Éµ¥¹…°Í¹…ÁÍ¡½ÑÌì…¸…Ñ¥Ù”A¡…Í”€ÌÉ…Á (€€€€€€€€Œ…±İ…åÌÉ•ÅÕ¥É•Ì½µÁ±•Ñ”É½Õ¹‘¥¹œ…¹Á±…•µ•¹Ğ…ÕÑ¡½É¥Ñä¸(€€€€€€€™É½´€¸¥µÁ½ÉĞ…¹½¹¥…±}Í½ÕÉ•}Á¡…Í”Ì…ÌÁ¡…Í”Ì((€€€€€€€ÁÉ½‘ÕÑ¥½¹}É½Õ¹‘¥¹}É•ÅÕ¥É•€ô¥Í¥¹ÍÑ…¹” (€€€€€€€€€€€Á¡…Í”Ì¹…Ñ¥Ù•}É…Á  ¤°‘¥Ğ(€€€€€€€€¤(€€€€€€€€Œ%¹ÍÁ•ĞÑ¡”¹•İ•ÍĞÍ¡…Á”µ½µÁ…Ñ¥‰±”Ñ•Éµ¥¹…°Á…å±½…™½ÈÍÑÉÕÑÕÉ…°(€€€€€€€€Œ‘É¥™Ğ•Ù•¸İ¡•¸±¥Ù”ÁÕ‰±¥…Ñ¥½¸…¹¹½ĞÉ•ÕÍ”¥Ğ‰•…ÕÍ”¥ĞÁÉ•‘…Ñ•Ì(€€€€€€€€ŒÑ¡”É½Õ¹‘¥¹œ•ÉÑ¥™¥…Ñ”¸€•ÉÑ¥™¥…Ñ”•±¥¥‰¥±¥Ñä…¹É•İ¥¹(€€€€€€€€Œ•±¥¥‰¥±¥Ñä…É”‘•±¥‰•É…Ñ•±äÍ•Á…É…Ñ”è½Ñ¡•Éİ¥Í”…¸Õ¹•ÉÑ¥™¥•(€€€€€€€€Œ€äà”¡•­Á½¥¹ĞÑ¡…Ğ½µ¥ÑÌ„Í½ÕÉ”Ñ½Á¥Œ‘¥Í…ÁÁ•…ÉÌ™É½´Ñ¡¥Ì¡•¬°(€€€€€€€€ŒÉ•µ…¥¹ÌÍ•±•Ñ…‰±”‰äÑ¡”ÁÉ”µ™¥¹…°ÍÑ…”ÉÕ¹¹•È°…¹¥¹½ÉÉ•Ñ±ä(€€€€€€€€Œ•¹Ñ•ÉÌÑ¡”±•…ä¡Õµ…¸Í½ÕÉ”µÑ½Á¥Œ…Ñ”¥¹ÍÑ•…½˜É•ÍÑ½É¥¹œÑ¡”(€€€€€€€€Œ½µÁ±•Ñ”ÁÉ••‘¥¹œ¡•­Á½¥¹Ğ¸(€€€€€€€ÍÑÉÕÑÕÉ…±}™¥¹…°€ô}¹•İ•ÍÑ}½µÁ…Ñ¥‰±•}½¹•ÁÑ}¡•­Á½¥¹Ğ (€€€€€€€€€€€É•ÍÕµ•}¡•­Á½¥¹Ğ°(€€€€€€€€€€€…±±½İ•‘}ÍÑ…•Ìõì‰™¥¹…±}½¹Ñ•¹Ñ}É•…‘ä‰ô°(€€€€€€€€€€€…±±½İ}±•…å}ÁÉ•}É•±•…Í”õQÉÕ”°(€€€€€€€€¤(€€€€€€€±•…å}ÁÉ•}É•±•…Í•}¡•­Á½¥¹Ğ€ô‰½½° (€€€€€€€€€€€ÍÑÉÕÑÕÉ…±}™¥¹…°(€€€€€€€€€€€…¹ÍÑÉÕÑÕÉ…±}™¥¹…°¹•Ğ ‰ÍÑ…•}Í¡•µ…}Ù•ÉÍ¥½¸ˆ¤(€€€€€€€€€€€€ôô}1e}AI}I1M}MQ}YIM%=9Ml‰™¥¹…±}½¹Ñ•¹Ñ}É•…‘ä‰t(€€€€€€€€¤(€€€€€€€Á¡…Í”Í}ÁÉ•}É•±•…Í•}…ÕÑ¡½É¥Ñäè‘¥ÑmÍÑÈ°¹åtğ9½¹”€ô9½¹”(€€€€€€€±•…å}ÁÉ•}É•±•…Í•}É•ÍÑ½É•€ô…±Í”(€€€€€€€¥˜ÍÑÉÕÑÕÉ…±}™¥¹…°…¹Ù…±¥‘}Á¡…Í”Í}ÁÉ•}É•±•…Í•}‰Õ¹‘±” (€€€€€€€€€€€ÍÑÉÕÑÕÉ…±}™¥¹…°¹•Ğ¡A!MÍ}AI}I1M}%1¤(€€€€€€€€¤è(€€€€€€€€€€€Á¡…Í”Í}ÁÉ•}É•±•…Í•}…ÕÑ¡½É¥Ñä€ô½Áä¹‘••Á½Áä (€€€€€€€€€€€€€€€ÍÑÉÕÑÕÉ…±}™¥¹…±mA!MÍ}AI}I1M}%1t(€€€€€€€€€€€€¤(€€€€€€€Í…Ù•‘}™¥¹…°€ô}¹•İ•ÍÑ}½µÁ…Ñ¥‰±•}½¹•ÁÑ}¡•­Á½¥¹Ğ (€€€€€€€€€€€É•ÍÕµ•}¡•­Á½¥¹Ğ°(€€€€€€€€€€€…±±½İ•‘}ÍÑ…•Ìõì‰™¥¹…±}½¹Ñ•¹Ñ}É•…‘ä‰ô°(€€€€€€€€€€€É•ÅÕ¥É•}™¥¹…±}É½Õ¹‘¥¹œõÁÉ½‘ÕÑ¥½¹}É½Õ¹‘¥¹}É•ÅÕ¥É•°(€€€€€€€€€€€…±±½İ}±•…å}ÁÉ•}É•±•…Í”õQÉÕ”°(€€€€€€€€¤(€€€€€€€™¥¹…±}¡•­Á½¥¹Ñ}É•™É•Í¡}É•…Í½¹Ì€ô}™¥¹…±}¡•­Á½¥¹Ñ}É•™É•Í¡}É•…Í½¹Ì (€€€€€€€€€€€ÍÑÉÕÑÕÉ…±}™¥¹…°°(€€€€€€€€€€€Í•Ñ¥½¹ÌõÍ•Ñ¥½¹Ì°(€€€€€€€€€€€Í½ÕÉ•}Ñ½Á¥}•á•ÉÁÑÌõÍ½ÕÉ•}Ñ½Á¥}•á•ÉÁÑÌ°(€€€€€€€€¤(€€€€€€€…Ñ¥Ù•}…ÉÑ¥™…Ñ}‘¥È€ôÍÑÈ (€€€€€€€€€€€€¡Á¡…Í”Ì¹…Ñ¥Ù•}Í•ÍÍ¥½¸ ¤½Èíô¤¹•Ğ ‰…ÉÑ¥™…Ñ}‘¥Èˆ¤½È€ˆˆ(€€€€€€€€¤(€€€€€€€¥˜€ (€€€€€€€€€€€ÍÑÉÕÑÕÉ…±}™¥¹…°(€€€€€€€€€€€…¹Á¡…Í”Í}ÁÉ•}É•±•…Í•}…ÕÑ¡½É¥Ñä¥Ì9½¹”(€€€€€€€€€€€…¹€¡±•…å}ÁÉ•}É•±•…Í•}¡•­Á½¥¹Ğ½È…Ñ¥Ù•}…ÉÑ¥™…Ñ}‘¥È¤(€€€€€€€€¤è(€€€€€€€€€€€€ŒQ•Éµ¥¹…°¡•­Á½¥¹ÑÌİÉ¥ÑÑ•¸‰•™½É”Ñ¡”AÉ”…ÕÑ¡½É¥ÑäÑÉ…Ù•±±•(€€€€€€€€€€€€Œ¥¸Ñ¡”¡•­Á½¥¹Ğ…¸ÍÑ¥±°É•ÍÕµ”™½È™É•”İ¡•¸‰½Ñ É•½É‘•(€€€€€€€€€€€€ŒÍ¥‘•…ÉÌ•á¥ÍĞ¸€½ÁäÑ¡•´Ù•É‰…Ñ¥´ì¹•Ù•È‘•É¥Ù”„AÉ”µ…À™É½´(€€€€€€€€€€€€ŒÑ¡”A½ÍĞÉ½İÌ¸€%˜•¥Ñ¡•ÈÍ¥‘•…È¥Ì…‰Í•¹Ğ½½ÉÉÕÁĞ°¥¹Ù…±¥‘…Ñ”(€€€€€€€€€€€€Œ½¹±äÑ¡”Ñ•Éµ¥¹…°Í¡½ÉÑÕĞ¸€I•İÉ¥ÑÑ•¸A¡…Í”€ÌÑ¡•¸É•½µÁÕÑ•Ì(€€€€€€€€€€€€ŒÑ¡”½É¥¥¹…°‘•¥Í¥½¸­•åÌ…¹É•Á±…åÌÑ¡”‘•¥‘”µ½¹”ÍÑ½É”¸(€€€€€€€€€€€™É½´€¸¥µÁ½ÉĞ½¹•ÁÑ}Ñ½Á½±½å}½¹ÑÉ…Ğ…Ì}Ñ½Á½±½ä((€€€€€€€€€€€É•ÍÑ½É•‘}ÁÉ”°ÁÉ•}‘•™•ÑÌ€ô}Ñ½Á½±½ä¹É•ÍÑ½É•‘}ÁÉ•}É•±•…Í” ¤(€€€€€€€€€€€¥˜¥Í¥¹ÍÑ…¹”¡É•ÍÑ½É•‘}ÁÉ”°5…ÁÁ¥¹œ¤è(€€€€€€€€€€€€€€€Á¡…Í”Í}ÁÉ•}É•±•…Í•}…ÕÑ¡½É¥Ñä€ôÁ¡…Í”Í}ÁÉ•}É•±•…Í•}‰Õ¹‘±” (€€€€€€€€€€€€€€€€€€€É•ÍÑ½É•‘}ÁÉ”¹•Ğ ‰ÁÉ•}µ…Àˆ¤½Èíô°(€€€€€€€€€€€€€€€€€€€É•ÍÑ½É•‘}ÁÉ”¹•Ğ ‰ÁÉ•}ÅÕ•ÍÑ¥½¹Ìˆ¤½Èíô°(€€€€€€€€€€€€€€€€€€€Í¹…ÁÍ¡½Ñ}İÉ¥Ñ•ÌõÉ•ÍÑ½É•‘}ÁÉ”¹•Ğ ‰Í¹…ÁÍ¡½Ñ}İÉ¥Ñ•Ìˆ¤½Èíô°(€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€±•…å}ÁÉ•}É•±•…Í•}É•ÍÑ½É•€ôQÉÕ”(€€€€€€€€€€€•±Í”è(€€€€€€€€€€€€€€€‘•Ñ…¥°€ô€ˆì€ˆ¹©½¥¸¡ÁÉ•}‘•™•ÑÌ¤½È€ (€€€€€€€€€€€€€€€€€€€€‰Ñ¡”A¡…Í”€ÀÌAÉ”µ…À½ÅÕ•ÍÑ¥½¹Ì…ÕÑ¡½É¥Ñä¥Ì…‰Í•¹Ğˆ(€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€™¥¹…±}¡•­Á½¥¹Ñ}É•™É•Í¡}É•…Í½¹Ì¹…ÁÁ•¹ (€€€€€€€€€€€€€€€€€€€€‰Ñ•Éµ¥¹…°¡•­Á½¥¹Ğ¡…Ì¹¼½µÁ±•Ñ”AÉ”µ1•…É¹¥¹œ€ˆ(€€€€€€€€€€€€€€€€€€€€‰É•±•…Í”…ÕÑ¡½É¥Ñäè€ˆ€¬‘•Ñ…¥°(€€€€€€€€€€€€€€€€¤(€€€€€€€¥˜™¥¹…±}¡•­Á½¥¹Ñ}É•™É•Í¡}É•…Í½¹Ìè(€€€€€€€€€€€ÁÉ½É•ÍÌ¹±½œ (€€€€€€€€€€€€€€€€‰¥¹…°¡•­Á½¥¹Ğ¥ÌÍÑÉÕÑÕÉ…±±ä¥¹½µÁ±•Ñ”ìÉ•ÍÕµ¥¹œ™É½´€ˆ(€€€€€€€€€€€€€€€€‰Ñ¡”ÁÉ••‘¥¹œÍÑ…”è€ˆ(€€€€€€€€€€€€€€€€¬€ˆì€ˆ¹©½¥¸¡™¥¹…±}¡•­Á½¥¹Ñ}É•™É•Í¡}É•…Í½¹Ì¤°(€€€€€€€€€€€€€€€±•Ù•°ô‰İ…É¹¥¹œˆ°(€€€€€€€€€€€€¤(€€€€€€€€€€€Í…Ù•‘}™¥¹…°€ô9½¹”(€€€€€€€Í…Ù•‘}Á¡…Í”Ì€ô9½¹”(€€€€€€€¥˜Í…Ù•‘}™¥¹…°¥Ì9½¹”è(€€€€€€€€€€€…¹‘¥‘…Ñ•}Á¡…Í”Ì€ô}¹•İ•ÍÑ}½µÁ…Ñ¥‰±•}½¹•ÁÑ}¡•­Á½¥¹Ğ (€€€€€€€€€€€€€€€É•ÍÕµ•}¡•­Á½¥¹Ğ°(€€€€€€€€€€€€€€€…±±½İ•‘}ÍÑ…•Ìõì‰Á½ÍÑ}ÑåÁ•}…ÍÍ¥¹µ•¹Ğ‰ô°(€€€€€€€€€€€€¤(€€€€€€€€€€€¥˜…¹‘¥‘…Ñ•}Á¡…Í”Ì…¹Ù…±¥‘}Á¡…Í”Í}ÁÉ•}É•±•…Í•}‰Õ¹‘±” (€€€€€€€€€€€€€€€…¹‘¥‘…Ñ•}Á¡…Í”Ì¹•Ğ¡A!MÍ}AI}I1M}%1¤(€€€€€€€€€€€€¤è(€€€€€€€€€€€€€€€Í…Ù•‘}Á¡…Í”Ì€ô…¹‘¥‘…Ñ•}Á¡…Í”Ì(€€€€€€€€€€€€€€€Á¡…Í”Í}ÁÉ•}É•±•…Í•}…ÕÑ¡½É¥Ñä€ô½Áä¹‘••Á½Áä (€€€€€€€€€€€€€€€€€€€…¹‘¥‘…Ñ•}Á¡…Í”ÍmA!MÍ}AI}I1M}%1t(€€€€€€€€€€€€€€€€¤(€€€€€€€…±±½İ}¡…ÁÑ•É}Ñ¥Ñ±•}Ñ½Á¥Œ€ô}¡…ÁÑ•É}Ñ¥Ñ±•}¥Í}µ…¥¹}Ñ½Á¥Œ (€€€€€€€€€€€Í•Ñ¥½¹Ì°¡…ÁÑ•É}Ñ¥Ñ±”¤(€€€€€€€ÁÉ½É•ÍÌ¹±½œ ‰½¹•ÁĞ•¹•É…Ñ¥½¸µ•Ñ…‘…Ñ„É••¥Ù•éq¸ˆ€¬}µ•Ñ…‘…Ñ…}‰±½¬¡µ•Ñ„¤¤(€€€€€€€ÁÉ½É•ÍÌ¹±½œ (€€€€€€€€€€€˜‰áÑÉ…Ñ¥¹œ½¹•ÁÑÌ™É½´í±•¸¡µµ‘}Ñ•áĞ¤è±ô¡…ÉÌ€ˆ(€€€€€€€€€€€˜‰…É½ÍÌí±•¸¡¡Õ¹­Ì¥ôÍ•Ñ¥½¸µ…İ…É”¡Õ¹¬¡Ì¤€ˆ(€€€€€€€€€€€˜ˆ¡ÍÕ‰©•ĞèíÍÕ‰©•Ğ½È€•¹•É…°ô¤¸ˆ¤(€€€€€€€€ (€€€€€€€€€€€½ÕĞ°(€€€€€€€€€€€ÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½Éä°(€€€€€€€€€€€µ¥¹•‘}ÑåÁ•Ì°(€€€€€€€€€€€µ•Ñ¡½‘}É½İ}Í¹…ÁÍ¡½Ğ°(€€€€€€€€¤€ô}ÉÕ¹}±¥Ù•}½¹•ÁÑ}ÁÉ•}™¥¹…±}ÍÑ…•Ì (€€€€€€€€€€€µµ‘}Ñ•áĞ°(€€€€€€€€€€€ÍÕ‰©•ĞõÍÕ‰©•Ğ°(€€€€€€€€€€€‰½…Éõ‰½…É°(€€€€€€€€€€€¡…ÁÑ•É}Ñ¥Ñ±”õ¡…ÁÑ•É}Ñ¥Ñ±”°(€€€€€€€€€€€¡Õ¹­Ìõ¡Õ¹­Ì°(€€€€€€€€€€€Í•Ñ¥½¹ÌõÍ•Ñ¥½¹Ì°(€€€€€€€€€€€µ•Ñ¡½‘}…¹¡½ÉÌõµ•Ñ¡½‘}…¹¡½ÉÌ°(€€€€€€€€€€€¡•…‘¥¹Ìõ¡•…‘¥¹Ì°(€€€€€€€€€€€Í½ÕÉ•}Ñ½Á¥}•á•ÉÁÑÌõÍ½ÕÉ•}Ñ½Á¥}•á•ÉÁÑÌ°(€€€€€€€€€€€…±±½İ}¡…ÁÑ•É}Ñ¥Ñ±•}Ñ½Á¥Œõ…±±½İ}¡…ÁÑ•É}Ñ¥Ñ±•}Ñ½Á¥Œ°(€€€€€€€€€€€µ•Ñ„õµ•Ñ„°(€€€€€€€€€€€…ÉÑ¥™…ÑÌõ…ÉÑ¥™…ÑÌ°(€€€€€€€€€€€É•ÍÕµ•}¡•­Á½¥¹ĞõÉ•ÍÕµ•}¡•­Á½¥¹Ğ°(€€€€€€€€€€€¡•­Á½¥¹Ñ}…±±‰…¬õ¡•­Á½¥¹Ñ}…±±‰…¬°(€€€€€€€€€€€…±±½İ}™¥¹…±}¡•­Á½¥¹Ğõ¹½Ğ™¥¹…±}¡•­Á½¥¹Ñ}É•™É•Í¡}É•…Í½¹Ì°(€€€€€€€€€€€…±±½İ}±•…å}ÁÉ•}É•±•…Í”ô (€€€€€€€€€€€€€€€±•…å}ÁÉ•}É•±•…Í•}¡•­Á½¥¹Ğ(€€€€€€€€€€€€€€€…¹Á¡…Í”Í}ÁÉ•}É•±•…Í•}…ÕÑ¡½É¥Ñä¥Ì¹½Ğ9½¹”(€€€€€€€€€€€€¤°(€€€€€€€€¤(€€€€€€€¥¹Ù•¹Ñ½Éå}™¥ÕÉ•}µ•Ñ…‘…Ñ…}É•™É•Í¡•€ô…±Í”(€€€€€€€¥˜Í…Ù•‘}™¥¹…°è(€€€€€€€€€€€É•™É•Í¡•‘}¥¹Ù•¹Ñ½Éä€ô}É•™É•Í¡}¥¹Ù•¹Ñ½Éå}™¥ÕÉ•}µ•Ñ…‘…Ñ„ (€€€€€€€€€€€€€€€ÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½Éä°Í•Ñ¥½¹Ì¤(€€€€€€€€€€€¥¹Ù•¹Ñ½Éå}™¥ÕÉ•}µ•Ñ…‘…Ñ…}É•™É•Í¡•€ô€ (€€€€€€€€€€€€€€€É•™É•Í¡•‘}¥¹Ù•¹Ñ½Éä€„ôÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½Éä(€€€€€€€€€€€€¤(€€€€€€€€€€€ÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½Éä€ôÉ•™É•Í¡•‘}¥¹Ù•¹Ñ½Éä(€€€€€€€€€€€¥˜€ (€€€€€€€€€€€€€€€¥¹Ù•¹Ñ½Éå}™¥ÕÉ•}µ•Ñ…‘…Ñ…}É•™É•Í¡•(€€€€€€€€€€€€€€€…¹…ÉÑ¥™…ÑÌ¥Ì¹½Ğ9½¹”(€€€€€€€€€€€€¤è(€€€€€€€€€€€€€€€…ÉÑ¥™…ÑÍl‰ÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½Éä‰t€ô½Áä¹‘••Á½Áä (€€€€€€€€€€€€€€€€€€€ÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½Éä¤(€€€€€€€¥˜™¥¹…±}¡•­Á½¥¹Ñ}É•™É•Í¡}É•…Í½¹Ìè(€€€€€€€€€€€ÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½Éä€ô}É•™É•Í¡}¥¹Ù•¹Ñ½Éå}™É½µ}Í½ÕÉ•}…¹¡½ÉÌ (€€€€€€€€€€€€€€€ÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½Éä°Í•Ñ¥½¹Ì¤(€€€€€€€€€€€¥˜…ÉÑ¥™…ÑÌ¥Ì¹½Ğ9½¹”è(€€€€€€€€€€€€€€€…ÉÑ¥™…ÑÍl‰ÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½Éä‰t€ô½Áä¹‘••Á½Áä (€€€€€€€€€€€€€€€€€€€ÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½Éä¤(€€€€€€€™¥¹…±}¡•­Á½¥¹Ñ}¡…¹•€ô€ (€€€€€€€€€€€¹½Ğ‰½½°¡Í…Ù•‘}™¥¹…°¤(€€€€€€€€€€€½È¥¹Ù•¹Ñ½Éå}™¥ÕÉ•}µ•Ñ…‘…Ñ…}É•™É•Í¡•(€€€€€€€€€€€½È±•…å}ÁÉ•}É•±•…Í•}É•ÍÑ½É•(€€€€€€€€¤(€€€€€€€¥˜Í…Ù•‘}™¥¹…°è(€€€€€€€€€€€ÁÉ½É•ÍÌ¹±½œ (€€€€€€€€€€€€€€€€‰I•ÍÑ½É•™¥¹…°½¹Ñ•¹Ğ¡•­Á½¥¹ĞìÍ•µ…¹Ñ¥Œ½A$É•Á…¥ÈÍÑ…åÌ€ˆ(€€€€€€€€€€€€€€€€‰Í­¥ÁÁ•Õ¹±•ÍÌÍÑÉ¥Ğ™½Éµ…ÑÑ¥¹œ™¥¹‘Ì„Ñ…É•Ñ•É•Á…¥È¸ˆ°(€€€€€€€€€€€€€€€±•Ù•°ô‰ÍÕ•ÍÌˆ°(€€€€€€€€€€€€¤(€€€€€€€€€€€€ŒA¡…Í”€ÀÌ€¡‘½Œƒ
œĞ°DÌ¤è„É•ÍÑ½É•™¥¹…°¡•­Á½¥¹ĞÍ­¥ÁÌÑ¡”(€€€€€€€€€€€€Œİ¡½±”Á¡…Í”´ÌÉÕ¸°Í¼Ñ¡”…ÁÑÕÉ”¥Ì¹½Ğµ…‘”¥¸Ñ¡¥Ì(€€€€€€€€€€€€ŒÁÉ½•ÍÌ¸I•…‰…¬Ñ¡”Í¹…ÁÍ¡½ĞÑ¡”½É¥¥¹…°ÉÕ¸İÉ½Ñ”(€€€€€€€€€€€€Œ‰•Í¥‘”¥ÑÌ‘•¥Í¥½¸ÍÑ½É”ìİ¡•¸Ñ¡•É”¥Ì¹½¹”°É•½ÉÑ¡”(€€€€€€€€€€€€Œ	M9•áÁ±¥¥Ñ±ä¸µ¥ÍÍ¥¹œ­•äİ½Õ±½Ñ¡•Éİ¥Í”‰”(€€€€€€€€€€€€Œ¥¹‘¥ÍÑ¥¹Õ¥Í¡…‰±”™É½´„¡…ÁÑ•ÈÑ¡…Ğ•¹Õ¥¹•±ä…ÍÍÕµ•Ì¹¼(€€€€€€€€€€€€ŒÁÉ•É•ÅÕ¥Í¥Ñ”°…¹„É•ÍÕµ•©½ˆİ½Õ±Í¡¥À…¸•µÁÑäAÉ”µ…À(€€€€€€€€€€€€Œİ¥Ñ ¹½Ñ¡¥¹œ™±…•€¡HĞ¤¸(€€€€€€€€€€€¥˜…ÉÑ¥™…ÑÌ¥Ì¹½Ğ9½¹”è(€€€€€€€€€€€€€€€™É½´€¸¥µÁ½ÉĞ½¹•ÁÑ}Ñ½Á½±½å}½¹ÑÉ…Ğ…Ì}Ñ½Á½±½ä((€€€€€€€€€€€€€€€¥˜Á¡…Í”Í}ÁÉ•}É•±•…Í•}…ÕÑ¡½É¥Ñä¥Ì¹½Ğ9½¹”è(€€€€€€€€€€€€€€€€€€€…ÉÑ¥™…ÑÍmA!MÍ}AI}I1M}%1t€ô½Áä¹‘••Á½Áä (€€€€€€€€€€€€€€€€€€€€€€€Á¡…Í”Í}ÁÉ•}É•±•…Í•}…ÕÑ¡½É¥Ñä(€€€€€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€É•ÍÑ½É•‘}ÁÉ•É•ÅÕ¥Í¥Ñ•Ì€ô}Ñ½Á½±½ä¹É•ÍÑ½É•‘}ÁÉ•É•ÅÕ¥Í¥Ñ•Ì ¤(€€€€€€€€€€€€€€€¥˜É•ÍÑ½É•‘}ÁÉ•É•ÅÕ¥Í¥Ñ•Ì¥Ì¹½Ğ9½¹”è(€€€€€€€€€€€€€€€€€€€…ÉÑ¥™…ÑÍl‰Á¡…Í”Í}ÁÉ•É•ÅÕ¥Í¥Ñ•Ì‰t€ôÉ•ÍÑ½É•‘}ÁÉ•É•ÅÕ¥Í¥Ñ•Ì(€€€€€€€€€€€€€€€•±Í”è(€€€€€€€€€€€€€€€€€€€…ÉÑ¥™…ÑÍl‰Á¡…Í”Í}ÁÉ•É•ÅÕ¥Í¥Ñ•Í}…‰Í•¹Ğ‰t€ô€ (€€€€€€€€€€€€€€€€€€€€€€€€‰™¥¹…°½¹Ñ•¹Ğ¡•­Á½¥¹ĞÉ•ÍÑ½É•…¹¹¼€ˆ(€€€€€€€€€€€€€€€€€€€€€€€˜‰í}Ñ½Á½±½ä¹AI1I9}M9AM!=Qôİ…Ì™½Õ¹‰•Í¥‘”Ñ¡”€ˆ(€€€€€€€€€€€€€€€€€€€€€€€€‰‘•¥Í¥½¸ÍÑ½É”èÑ¡¥ÌÉÕ¸µ…‘”¹¼A¡…Í”€ÀÌ…ÁÑÕÉ”€ˆ(€€€€€€€€€€€€€€€€€€€€€€€€‰…¹Ñ¡”•µÁÑäÍ•Ğ¡•É”µÕÍĞ¹½Ğ‰”É•……Ì½¹”¸ˆ(€€€€€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€€€€€ÁÉ½É•ÍÌ¹±½œ (€€€€€€€€€€€€€€€€€€€€€€€€‰Q¡”A¡…Í”€ÀÌÁÉ”µÉ•ÅÕ¥Í¥Ñ”…ÁÑÕÉ”¥ÌÕ¹…Ù…¥±…‰±”½¸€ˆ(€€€€€€€€€€€€€€€€€€€€€€€€‰Ñ¡¥ÌÉ•ÍÑ½É•¡•­Á½¥¹Ğ…¹¥ÑÌ…‰Í•¹”¥ÌÉ•½É‘•€ˆ(€€€€€€€€€€€€€€€€€€€€€€€€‰É…Ñ¡•ÈÑ¡…¸É•Á½ÉÑ•…Ì…¸•µÁÑäÁÉ•É•ÅÕ¥Í¥Ñ”Í•Ğ¸ˆ°(€€€€€€€€€€€€€€€€€€€€€€€±•Ù•°ô‰İ…É¹¥¹œˆ°(€€€€€€€€€€€€€€€€€€€€¤(€€€€€€€•±¥˜Í…Ù•‘}Á¡…Í”Ìè(€€€€€€€€€€€€ŒQ¡”¹•ÜÁ½ÍĞµQåÁ”¡•­Á½¥¹Ğ¥ÌÑ¡”•á…Ğ½ÕÑÁÕĞ½˜É•İÉ¥ÑÑ•¸(€€€€€€€€€€€€ŒA¡…Í”€ÌÁ±ÕÌ¥ÑÌAÉ”…ÕÑ¡½É¥Ñä¸½¹Ñ¥¹Õ”…ĞÑ•Éµ¥¹…°Ù…±¥‘…Ñ¥½¸ì(€€€€€€€€€€€€ŒÉ”µ•¹Ñ•É¥¹œA¡…Í”€Ì¡•É”İ½Õ±™••…±É•…‘äµ…ÍÍ•µ‰±•É½İÌ‰…¬(€€€€€€€€€€€€Œ¥¹Ñ¼M•ÑÑ±”…¹µ¥¹Ğ‘¥™™•É•¹Ğ‘•¥Í¥½¸Á…å±½…‘Ì¸(€€€€€€€€€€€¥˜…ÉÑ¥™…ÑÌ¥Ì¹½Ğ9½¹”è(€€€€€€€€€€€€€€€…ÉÑ¥™…ÑÍmA!MÍ}AI}I1M}%1t€ô½Áä¹‘••Á½Áä (€€€€€€€€€€€€€€€€€€€Á¡…Í”Í}ÁÉ•}É•±•…Í•}…ÕÑ¡½É¥Ñä(€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€™É½´€¸¥µÁ½ÉĞ½¹•ÁÑ}Ñ½Á½±½å}½¹ÑÉ…Ğ…Ì}Ñ½Á½±½ä((€€€€€€€€€€€€€€€É•ÍÑ½É•‘}ÁÉ•É•ÅÕ¥Í¥Ñ•Ì€ô}Ñ½Á½±½ä¹É•ÍÑ½É•‘}ÁÉ•É•ÅÕ¥Í¥Ñ•Ì ¤(€€€€€€€€€€€€€€€¥˜É•ÍÑ½É•‘}ÁÉ•É•ÅÕ¥Í¥Ñ•Ì¥Ì¹½Ğ9½¹”è(€€€€€€€€€€€€€€€€€€€…ÉÑ¥™…ÑÍl‰Á¡…Í”Í}ÁÉ•É•ÅÕ¥Í¥Ñ•Ì‰t€ô€ (€€€€€€€€€€€€€€€€€€€€€€€É•ÍÑ½É•‘}ÁÉ•É•ÅÕ¥Í¥Ñ•Ì(€€€€€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€ÁÉ½É•ÍÌ¹±½œ (€€€€€€€€€€€€€€€€‰I•ÍÑ½É•Ñ¡”½µÁ±•Ñ•É•İÉ¥ÑÑ•¸A¡…Í”€Ì¡•­Á½¥¹Ğ…¹€ˆ(€€€€€€€€€€€€€€€€‰¥ÑÌAÉ”µ1•…É¹¥¹œ…ÕÑ¡½É¥Ñäì½¹Ñ¥¹Õ¥¹œ…ĞÑ•Éµ¥¹…°€ˆ(€€€€€€€€€€€€€€€€‰Ù…±¥‘…Ñ¥½¸İ¥Ñ¡½ÕĞÉ•Á±…å¥¹œÍ•µ…¹Ñ¥ŒÁ…ÍÍ•Ì¸ˆ°(€€€€€€€€€€€€€€€±•Ù•°ô‰ÍÕ•ÍÌˆ°(€€€€€€€€€€€€¤(€€€€€€€•±Í”è(€€€€€€€€€€€€ŒA¡…Í”€ÀÌ€¡‘½Œƒ
œĞ°DÌ¤èÑ¡”ÉÕ¸ÌÁÉ”µÉ•ÅÕ¥Í¥Ñ”…ÁÑÕÉ”±•…Ù•Ì(€€€€€€€€€€€€ŒÑ¡”Í•…±•‰½Õ¹‘…ÉäÑ¡É½Õ Ñ¡¥Ì‘¥Ğ¸Q¡”É½İÌÉ•ÑÕÉ¹•‰ä(€€€€€€€€€€€€ŒÑ¡”…±°…É”Õ¹Ñ½Õ¡•‰ä¥Ğ¸(€€€€€€€€€€€Á¡…Í”Í}…ÉÉäè‘¥Ğ€ôíô(€€€€€€€€€€€½ÕĞ€ô}ÁÉ•Á…É•}™¥¹…±}½¹•ÁÑ}½¹Ñ•¹Ğ (€€€€€€€€€€€€€€€½ÕĞ°(€€€€€€€€€€€€€€€Á¡…Í”Í}…ÉÉäõÁ¡…Í”Í}…ÉÉä°(€€€€€€€€€€€€€€€ÍÕ‰©•ĞõÍÕ‰©•Ğ°(€€€€€€€€€€€€€€€‰½…Éõ‰½…É°(€€€€€€€€€€€€€€€¡…ÁÑ•É}Ñ¥Ñ±”õ¡…ÁÑ•É}Ñ¥Ñ±”°(€€€€€€€€€€€€€€€µ•Ñ„õµ•Ñ„°(€€€€€€€€€€€€€€€µµ‘}Ñ•áĞõµµ‘}Ñ•áĞ°(€€€€€€€€€€€€€€€Í½ÕÉ•}Í•Ñ¥½¹ÌõÍ•Ñ¥½¹Ì°(€€€€€€€€€€€€€€€ÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½ÉäõÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½Éä°(€€€€€€€€€€€€€€€µ¥¹•‘}ÑåÁ•Ìõµ¥¹•‘}ÑåÁ•Ì°(€€€€€€€€€€€€€€€µ•Ñ¡½‘}É½İ}Í¹…ÁÍ¡½Ğõµ•Ñ¡½‘}É½İ}Í¹…ÁÍ¡½Ğ°(€€€€€€€€€€€€€€€µ•Ñ¡½‘}…¹¡½ÉÌõµ•Ñ¡½‘}…¹¡½ÉÌ°(€€€€€€€€€€€€€€€¡•…‘¥¹Ìõ¡•…‘¥¹Ì°(€€€€€€€€€€€€€€€Í½ÕÉ•}Ñ½Á¥}•á•ÉÁÑÌõÍ½ÕÉ•}Ñ½Á¥}•á•ÉÁÑÌ°(€€€€€€€€€€€€€€€É•™É•Í¡}¡…ÁÑ•É}İ¥‘•}…ÍÍ¥¹µ•¹ÑÌõ‰½½° (€€€€€€€€€€€€€€€€€€€™¥¹…±}¡•­Á½¥¹Ñ}É•™É•Í¡}É•…Í½¹Ì¤°(€€€€€€€€€€€€¤(€€€€€€€€€€€ÁÉ•}µ…À€ôÁ¡…Í”Í}…ÉÉä¹•Ğ ‰ÁÉ•}µ…Àˆ¤(€€€€€€€€€€€ÁÉ•}ÅÕ•ÍÑ¥½¹Ì€ôÁ¡…Í”Í}…ÉÉä¹•Ğ ‰ÁÉ•}ÅÕ•ÍÑ¥½¹Ìˆ¤(€€€€€€€€€€€¥˜¥Í¥¹ÍÑ…¹”¡ÁÉ•}µ…À°5…ÁÁ¥¹œ¤…¹¥Í¥¹ÍÑ…¹” (€€€€€€€€€€€€€€€ÁÉ•}ÅÕ•ÍÑ¥½¹Ì°5…ÁÁ¥¹œ(€€€€€€€€€€€€¤è(€€€€€€€€€€€€€€€Á¡…Í”Í}ÁÉ•}É•±•…Í•}…ÕÑ¡½É¥Ñä€ôÁ¡…Í”Í}ÁÉ•}É•±•…Í•}‰Õ¹‘±” (€€€€€€€€€€€€€€€€€€€ÁÉ•}µ…À°(€€€€€€€€€€€€€€€€€€€ÁÉ•}ÅÕ•ÍÑ¥½¹Ì°(€€€€€€€€€€€€€€€€€€€Í¹…ÁÍ¡½Ñ}İÉ¥Ñ•Ìô (€€€€€€€€€€€€€€€€€€€€€€€Á¡…Í”Í}…ÉÉä¹•Ğ ‰ÁÉ•}Í¹…ÁÍ¡½Ñ}İÉ¥Ñ•Ìˆ¤½Èíô(€€€€€€€€€€€€€€€€€€€€¤°(€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€¥˜…ÉÑ¥™…ÑÌ¥Ì¹½Ğ9½¹”è(€€€€€€€€€€€€€€€€€€€…ÉÑ¥™…ÑÍmA!MÍ}AI}I1M}%1t€ô½Áä¹‘••Á½Áä (€€€€€€€€€€€€€€€€€€€€€€€Á¡…Í”Í}ÁÉ•}É•±•…Í•}…ÕÑ¡½É¥Ñä(€€€€€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€€ŒQ¡¥Ì¥ÌÑ¡”™¥ÉÍĞ‘ÕÉ…‰±”¡•­Á½¥¹Ğ…™Ñ•ÈÑ¡”É•İÉ¥ÑÑ•¸(€€€€€€€€€€€€€€€€ŒA¡…Í”€Ì‰½Õ¹‘…Éä¸A•ÉÍ¥ÍĞÑ¡”•á…ĞAÉ”…ÕÑ¡½É¥Ñä‰•™½É”(€€€€€€€€€€€€€€€€Œ…¹ä±…Ñ•ÈÑ•Éµ¥¹…°Ù…±¥‘…Ñ¥½¸…¸™…¥°°Í¼Ñ¡”É•±•…Í”(€€€€€€€€€€€€€€€€ŒİÉ…ÁÁ•È…¸ÍÑ¥±°Í¡¥À½µÁ±•Ñ•AÉ”İ½É¬‰•Í¥‘”A½ÍĞ¸(€€€€€€€€€€€€€€€}•µ¥Ñ}½¹•ÁÑ}¡•­Á½¥¹Ğ (€€€€€€€€€€€€€€€€€€€¡•­Á½¥¹Ñ}…±±‰…¬°(€€€€€€€€€€€€€€€€€€€€‰Á½ÍÑ}ÑåÁ•}…ÍÍ¥¹µ•¹Ğˆ°(€€€€€€€€€€€€€€€€€€€É•½É‘Ìõ½ÕĞ°(€€€€€€€€€€€€€€€€€€€ÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½ÉäõÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½Éä°(€€€€€€€€€€€€€€€€€€€µ¥¹•‘}ÑåÁ•Ìõµ¥¹•‘}ÑåÁ•Ì°(€€€€€€€€€€€€€€€€€€€µ•Ñ¡½‘}É½İ}Í¹…ÁÍ¡½Ğõ}Í•É¥…±¥é•}µ•Ñ¡½‘}É½İ}Í¹…ÁÍ¡½Ğ (€€€€€€€€€€€€€€€€€€€€€€€µ•Ñ¡½‘}É½İ}Í¹…ÁÍ¡½Ğ(€€€€€€€€€€€€€€€€€€€€¤°(€€€€€€€€€€€€€€€€€€€€¨©ì(€€€€€€€€€€€€€€€€€€€€€€€A!MÍ}AI}I1M}%1è½Áä¹‘••Á½Áä (€€€€€€€€€€€€€€€€€€€€€€€€€€€Á¡…Í”Í}ÁÉ•}É•±•…Í•}…ÕÑ¡½É¥Ñä(€€€€€€€€€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€€€€€ô°(€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€•±Í”è(€€€€€€€€€€€€€€€É…¥Í”IÕ¹Ñ¥µ•ÉÉ½È (€€€€€€€€€€€€€€€€€€€€‰É•İÉ¥ÑÑ•¸A¡…Í”€ÌÉ•ÑÕÉ¹•¹¼½µÁ±•Ñ”ÁÉ•}µ…À€¼€ˆ(€€€€€€€€€€€€€€€€€€€€‰ÁÉ•}ÅÕ•ÍÑ¥½¹Ì…ÕÑ¡½É¥Ñäˆ(€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€ÁÉ•É•ÅÕ¥Í¥Ñ•Ì€ôÁ¡…Í”Í}…ÉÉä¹•Ğ ‰ÁÉ•É•ÅÕ¥Í¥Ñ•Ìˆ¤(€€€€€€€€€€€¥˜…ÉÑ¥™…ÑÌ¥Ì¹½Ğ9½¹”è(€€€€€€€€€€€€€€€¥˜¥Í¥¹ÍÑ…¹”¡ÁÉ•É•ÅÕ¥Í¥Ñ•Ì°‘¥Ğ¤è(€€€€€€€€€€€€€€€€€€€€Œ…ÁÑÕÉ”Ñ¡…ĞÉ•½É‘•¹½Ñ¡¥¹œ¥Ì„±•¥Ñ¥µ…Ñ”(€€€€€€€€€€€€€€€€€€€€Œ…¹Íİ•È…¹Í¡¥ÁÌ…Ì¥ÑÍ•±˜ì½¹±ä„…ÁÑÕÉ”Ñ¡…Ğ(€€€€€€€€€€€€€€€€€€€€Œ¹•Ù•È¡…ÁÁ•¹•¥ÌÉ•½É‘•…Ì…‰Í•¹Ğ¸(€€€€€€€€€€€€€€€€€€€…ÉÑ¥™…ÑÍl‰Á¡…Í”Í}ÁÉ•É•ÅÕ¥Í¥Ñ•Ì‰t€ô½Áä¹‘••Á½Áä (€€€€€€€€€€€€€€€€€€€€€€€ÁÉ•É•ÅÕ¥Í¥Ñ•Ì(€€€€€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€•±Í”è(€€€€€€€€€€€€€€€€€€€…ÉÑ¥™…ÑÍl‰Á¡…Í”Í}ÁÉ•É•ÅÕ¥Í¥Ñ•Í}…‰Í•¹Ğ‰t€ô€ (€€€€€€€€€€€€€€€€€€€€€€€€‰Ñ¡”É•İÉ¥ÑÑ•¸A¡…Í”€ÌÉ•ÑÕÉ¹•¹¼ÁÉ•É•ÅÕ¥Í¥Ñ•Ì€ˆ(€€€€€€€€€€€€€€€€€€€€€€€€‰­•äèÑ¡¥ÌÉÕ¸µ…‘”¹¼A¡…Í”€ÀÌ…ÁÑÕÉ”…¹Ñ¡”€ˆ(€€€€€€€€€€€€€€€€€€€€€€€€‰•µÁÑäÍ•Ğ¡•É”µÕÍĞ¹½Ğ‰”É•……Ì½¹”¸ˆ(€€€€€€€€€€€€€€€€€€€€¤(€€€€€€€¥˜…ÉÑ¥™…ÑÌ¥Ì¹½Ğ9½¹”è(€€€€€€€€€€€…ÉÑ¥™…ÑÍl‰µ¥¹•‘}ÑåÁ•Ì‰t€ô½Áä¹‘••Á½Áä¡µ¥¹•‘}ÑåÁ•Ì¤(€€€€€€€€Œ=±‘•ÈÑ•Éµ¥¹…°¡•­Á½¥¹ÑÌ…¸ÁÉ•‘…Ñ”Ñ¡”…¹½¹¥…°µ…ÍÑ•Éä½É•…À(€€€€€€€€Œ½¹ÑÉ…Ğ¸UÁÉ…‘”Ñ¡½Í”™¥•±‘Ì‘•Ñ•Éµ¥¹¥ÍÑ¥…±±äÍ¼É•ÍÕµ”É•µ…¥¹Ì(€€€€€€€€ŒA$µ™É•”İ¡¥±”Ñ¡”•á…ĞÉ½İÌÍ•¹ĞÑ¼‘•Á½Í¥ĞÍ…Ñ¥Í™äÑ½‘…äÌ…Ñ”¸(€€€€€€€‰•™½É•}½¹Ñ•¹Ñ}½¹ÑÉ…ÑÌ€ô½Áä¹‘••Á½Áä¡½ÕĞ¤(€€€€€€€½ÕĞ€ô}•¹ÍÕÉ•}µ…ÍÑ•Éå}±¥¹•Í}Ù¥…}…Á¤ (€€€€€€€€€€€½ÕĞ°µ•Ñ„õµ•Ñ„°ÕÍ•}…Á¤õ…±Í”¤(€€€€€€€½ÕĞ€ô}•¹ÍÕÉ•}Ñ•Éµ¥¹…±}Õ±µ¥¹…Ñ¥½¹}½¹ÑÉ…Ğ¡½ÕĞ¤(€€€€€€€¥˜½ÕĞ€„ô‰•™½É•}½¹Ñ•¹Ñ}½¹ÑÉ…ÑÌè(€€€€€€€€€€€™¥¹…±}¡•­Á½¥¹Ñ}¡…¹•€ôQÉÕ”(€€€€€€€‰•™½É•}™¥¹…±}¹½Éµ…±¥é…Ñ¥½¸€ô½Áä¹‘••Á½Áä¡½ÕĞ¤(€€€€€€€½ÕĞ€ô}…¹½¹¥…±¥é•}½¹•ÁÑ}É¥¡}Ñ•áĞ¡½ÕĞ¤(€€€€€€€¥˜½ÕĞ€„ô‰•™½É•}™¥¹…±}¹½Éµ…±¥é…Ñ¥½¸è(€€€€€€€€€€€™¥¹…±}¡•­Á½¥¹Ñ}¡…¹•€ôQÉÕ”(€€€€€€€€ŒÍ…Ù•™¥¹…°¡•­Á½¥¹Ğ‰åÁ…ÍÍ•ÌÑ¡”Í•µ…¹Ñ¥Œ™¥¹…±¥é•È¸€%ÑÌÍ½ÕÉ”(€€€€€€€€Œ¥¹Ù•¹Ñ½ÉäÉ•µ…¥¹Ì…ÕÑ¡½É¥Ñ…Ñ¥Ù”°Í¼É•ÍÑ½É”…¹äÍ½ÕÉ”á…µÁ±”Ñ¡…Ğ(€€€€€€€€Œ…¸½±‘•È™¥¹…±¥é•È½¡•­Á½¥¹Ğ½µ¥ÑÑ•‰•™½É”‘•±…É¥¹œÑ¡”É•ÍÕµ•µ…À(€€€€€€€€ŒÙ…±¥¸€Q¡¥Ì¥Ì„‘•Ñ•Éµ¥¹¥ÍÑ¥ŒÁ±…•µ•¹ĞÉ•Á…¥È½¹±äè¹¼A$…±°¥Ì(€€€€€€€€Œµ…‘”…¹¹¼ÅÕ•ÍÑ¥½¸İ½É‘¥¹œ¥Ì¥¹Ù•¹Ñ•¸€É•Í µ…ÁÌ…±É•…‘äÁ…ÍÌ(€€€€€€€€ŒÑ¡É½Õ Ñ¡¥Ì•á…ĞÉ•Á…¥È¥¸Ñ¡•¥È™¥¹…±¥é•È¸(€€€€€€€™É½´€¹Á¡…Í”Ì¥µÁ½ÉĞ™¥á•È…ÌÀÍ}™¥á•È((€€€€€€€É•ÍÕµ•‘}½Ù•É…•}É•Á…¥É•€ô…±Í”(€€€€€€€¥˜Í…Ù•‘}™¥¹…°è(€€€€€€€€€€€ÁÉ•}É•ÍÕµ•}É•Á…¥È€ô½Áä¹‘••Á½Áä¡½ÕĞ¤(€€€€€€€€€€€½Ù•É…•}‰•™½É•}É•ÍÕµ•}É•Á…¥È€ô€ (€€€€€€€€€€€€€€€}É•¹‘•É•‘}¥¹Ù•¹Ñ½Éå}½Ù•É…•}‘•™•ÑÌ (€€€€€€€€€€€€€€€€€€€½ÕĞ°ÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½Éä¤¤(€€€€€€€€€€€½ÕĞ€ô}•¹™½É•}É•¹‘•É•‘}¥¹Ù•¹Ñ½Éå}½Ù•É…” (€€€€€€€€€€€€€€€½ÕĞ°ÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½Éä°µ¥¹•‘}ÑåÁ•Ì°(€€€€€€€€€€€€€€€™¥á•ÈõÀÍ}™¥á•È¹‘•™…Õ±Ñ}ÁÉ½Ù¥‘•È ¤¤(€€€€€€€€€€€É•ÍÕµ•‘}½Ù•É…•}É•Á…¥É•€ô½ÕĞ€„ôÁÉ•}É•ÍÕµ•}É•Á…¥È(€€€€€€€€€€€¥˜É•ÍÕµ•‘}½Ù•É…•}É•Á…¥É•è(€€€€€€€€€€€€€€€ÁÉ½É•ÍÌ¹±½œ (€€€€€€€€€€€€€€€€€€€€‰I•Á…¥É•Í…Ù•™¥¹…°¡•­Á½¥¹ĞÍ½ÕÉ”µÑ…Í¬½Ù•É…”è€ˆ(€€€€€€€€€€€€€€€€€€€˜‰í±•¸¡½Ù•É…•}‰•™½É•}É•ÍÕµ•}É•Á…¥Élµ¥ÍÍ¥¹œt¥ôµ¥ÍÍ¥¹œ°€ˆ(€€€€€€€€€€€€€€€€€€€˜‰í±•¸¡½Ù•É…•}‰•™½É•}É•ÍÕµ•}É•Á…¥Él‘ÕÁ±¥…Ñ”t¥ô‘ÕÁ±¥…Ñ”¸ˆ°(€€€€€€€€€€€€€€€€€€€±•Ù•°ô‰ÍÕ•ÍÌˆ°(€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€½ÕĞ€ôÈ¹É•¹Õµ‰•É}ÑåÁ•Í}½¹Ñ¥¹Õ½ÕÍ±ä¡½ÕĞ¤(€€€€€€€€€€€€€€€½ÕĞ€ôØ¹•¹ÍÕÉ•}Ù…±¥‘}±•…É¹•É}…¹…±åÍ¥Ì¡½ÕĞ¤(€€€€€€€€€€€€€€€½ÕĞ€ô}…¹½¹¥…±¥é•}½¹•ÁÑ}É¥¡}Ñ•áĞ¡½ÕĞ¤(€€€€€€€€€€€€€€€™¥¹…±}¡•­Á½¥¹Ñ}¡…¹•€ôQÉÕ”(€€€€€€€€ŒÍ…Ù•™¥¹…°¡•­Á½¥¹Ğ‰åÁ…ÍÍ•ÌÑ¡”™¥¹…±¥é•È…‰½Ù”¸€½ÉÉ•Ğ…¹ä(€€€€€€€€ŒÍÑ…±”¥ÕÉ”Ñ…œ™É½´Ñ¡”Í½ÕÉ”É•¥ÍÑÉä¥µµ•‘¥…Ñ•±ä‰•™½É”Ñ¡”(€€€€€€€€Œ½ÕÑ•È™¥¹…°…Ñ”°İ¥Ñ¡½ÕĞÍÁ•¹‘¥¹œ…¹½Ñ¡•ÈA$É•ÅÕ•ÍĞ¸(€€€€€€€½ÕĞ°É•½¹¥±•‘}™¥ÕÉ•}•á…µÁ±•Ì€ô}É•½¹¥±•}•áÁ±¥¥Ñ}™¥ÕÉ•}¥µ…•Ì (€€€€€€€€€€€½ÕĞ°Í•Ñ¥½¹Ì¤(€€€€€€€Á½ÍÑ}™¥ÕÉ•}½Ù•É…”€ô}É•¹‘•É•‘}¥¹Ù•¹Ñ½Éå}½Ù•É…•}‘•™•ÑÌ (€€€€€€€€€€€½ÕĞ°ÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½Éä¤(€€€€€€€¥˜€ (€€€€€€€€€€€Á½ÍÑ}™¥ÕÉ•}½Ù•É…•l‰µ¥ÍÍ¥¹œ‰t(€€€€€€€€€€€½ÈÁ½ÍÑ}™¥ÕÉ•}½Ù•É…•l‰‘ÕÁ±¥…Ñ”‰t(€€€€€€€€¤è(€€€€€€€€€€€€ŒI•Á±…¥¹œ„ÍÑ…±”™¥ÕÉ”Ñ…œ…¸µ…­”…¸½±‘•È¹•…Èµµ…Ñ ‰•½µ”(€€€€€€€€€€€€Œ¥‘•¹Ñ¥…°Ñ¼„Í½ÕÉ”á…µÁ±”É•ÍÑ½É•©ÕÍĞ…‰½Ù”¸I”µÉÕ¸Ñ¡”(€€€€€€€€€€€€Œ•á…Ğµ½¹”…Ñ”Í¼Ñ¡”½ÉÉ•Ñ•½Á¥•Ì…É”‘•Ñ•Éµ¥¹¥ÍÑ¥…±±ä(€€€€€€€€€€€€Œ‘•‘ÕÁ±¥…Ñ•‰•™½É”Ñ¡”Ñ•Éµ¥¹…°¡•­Á½¥¹Ğ¥ÌÙ…±¥‘…Ñ•¸(€€€€€€€€€€€½ÕĞ€ô}•¹™½É•}É•¹‘•É•‘}¥¹Ù•¹Ñ½Éå}½Ù•É…” (€€€€€€€€€€€€€€€½ÕĞ°ÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½Éä°µ¥¹•‘}ÑåÁ•Ì°(€€€€€€€€€€€€€€€™¥á•ÈõÀÍ}™¥á•È¹‘•™…Õ±Ñ}ÁÉ½Ù¥‘•È ¤¤(€€€€€€€€€€€½ÕĞ€ôÈ¹É•¹Õµ‰•É}ÑåÁ•Í}½¹Ñ¥¹Õ½ÕÍ±ä¡½ÕĞ¤(€€€€€€€€€€€½ÕĞ€ôØ¹•¹ÍÕÉ•}Ù…±¥‘}±•…É¹•É}…¹…±åÍ¥Ì¡½ÕĞ¤(€€€€€€€€€€€½ÕĞ€ô}…¹½¹¥…±¥é•}½¹•ÁÑ}É¥¡}Ñ•áĞ¡½ÕĞ¤(€€€€€€€€€€€É•ÍÕµ•‘}½Ù•É…•}É•Á…¥É•€ôQÉÕ”(€€€€€€€¥˜É•½¹¥±•‘}™¥ÕÉ•}•á…µÁ±•Ì½ÈÉ•ÍÕµ•‘}½Ù•É…•}É•Á…¥É•è(€€€€€€€€€€€ÁÉ½É•ÍÌ¹±½œ (€€€€€€€€€€€€€€€€ (€€€€€€€€€€€€€€€€€€€€‰I•½¹¥±•€ˆ(€€€€€€€€€€€€€€€€€€€˜‰íÉ•½¹¥±•‘}™¥ÕÉ•}•á…µÁ±•ÍôÉ•¹‘•É•¥ÕÉ”á…µÁ±”¡Ì¤€ˆ(€€€€€€€€€€€€€€€€€€€€‰……¥¹ÍĞÑ¡”Í½ÕÉ”É•¥ÍÑÉä¸ˆ(€€€€€€€€€€€€€€€€€€€¥˜É•½¹¥±•‘}™¥ÕÉ•}•á…µÁ±•Ì(€€€€€€€€€€€€€€€€€€€•±Í”€‰I•Á…¥É•Í…Ù•™¥¹…°¡•­Á½¥¹Ğ½Ù•É…”¸ˆ(€€€€€€€€€€€€€€€€¤°(€€€€€€€€€€€€€€€±•Ù•°ô‰ÍÕ•ÍÌˆ°(€€€€€€€€€€€€¤(€€€€€€€€€€€™¥¹…±}¡•­Á½¥¹Ñ}¡…¹•€ôQÉÕ”((€€€€€€€ÑÉäè(€€€€€€€€€€€¥˜Í…Ù•‘}™¥¹…°è(€€€€€€€€€€€€€€€€ŒÉ•Í É½İÌİ•É”¹½Éµ…±¥é•…ĞÑ¡”•¹½˜Ñ¡”Í•µ…¹Ñ¥Œ(€€€€€€€€€€€€€€€€Œ™¥¹…±¥é•È¸=¹±äÉ•ÍÑ½É•Ñ•Éµ¥¹…°É½İÌ‰åÁ…ÍÍ•Ñ¡…Ğ‰½Õ¹‘…Éä(€€€€€€€€€€€€€€€€Œ…¹¹••Ñ¡¥Ì…‘‘¥Ñ¥½¹…°‘•Ñ•Éµ¥¹¥ÍÑ¥ŒÁ…ÍÌ¸¹äÕ¹É•Í½±Ù•(€€€€€€€€€€€€€€€€Œ•ÉÑ¥™¥•¡½ÍĞ¥ÌÁ…ÉĞ½˜Ñ¡”ÍÑ…±”€äà”Á…å±½……¹µÕÍĞ(€€€€€€€€€€€€€€€€Œ•¹Ñ•ÈÑ¡”‘¥Í…É½É•ÍÕµ”Á…Ñ ‰•±½Ü¸(€€€€€€€€€€€€€€€‰•™½É•}¡Õ‰}¹½Éµ…±¥é…Ñ¥½¸€ô½Áä¹‘••Á½Áä¡½ÕĞ¤(€€€€€€€€€€€€€€€½ÕĞ€ô}¹½Éµ…±¥é•}…Ñ¥Ù¥Ñå}¡Õ‰Í}…Ñ}™¥¹…±}‰½Õ¹‘…Éä (€€€€€€€€€€€€€€€€€€€½ÕĞ°ÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½Éä°µ¥¹•‘}ÑåÁ•Ì¤(€€€€€€€€€€€€€€€¥˜½ÕĞ€„ô‰•™½É•}¡Õ‰}¹½Éµ…±¥é…Ñ¥½¸è(€€€€€€€€€€€€€€€€€€€™¥¹…±}¡•­Á½¥¹Ñ}¡…¹•€ôQÉÕ”((€€€€€€€€€€€‰•™½É•}ÑåÁ•}¡•…‘¥¹}É•Á…¥È€ô½Áä¹‘••Á½Áä¡½ÕĞ¤(€€€€€€€€€€€½ÕĞ€ô}‘¥Í…µ‰¥Õ…Ñ•}•ÉÑ¥™¥•‘}ÍÁ±¥Ñ}ÑåÁ•}…Í•Ì (€€€€€€€€€€€€€€€½ÕĞ°ÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½Éä°µ¥¹•‘}ÑåÁ•Ì¤(€€€€€€€€€€€¥˜½ÕĞ€„ô‰•™½É•}ÑåÁ•}¡•…‘¥¹}É•Á…¥Èè(€€€€€€€€€€€€€€€½ÕĞ€ôÈ¹É•¹Õµ‰•É}ÑåÁ•Í}½¹Ñ¥¹Õ½ÕÍ±ä¡½ÕĞ¤(€€€€€€€€€€€€€€€™¥¹…±}¡•­Á½¥¹Ñ}¡…¹•€ôQÉÕ”((€€€€€€€€€€€½ÕĞ°É¥¡}Ñ•áÑ}É•Á…¥É•€ô}É•Á…¥É}™¥¹…±}É¥¡}Ñ•áÑ}Ù¥…}…Á¤ (€€€€€€€€€€€€€€€½ÕĞ°(€€€€€€€€€€€€€€€µ•Ñ„õµ•Ñ„°(€€€€€€€€€€€€€€€¥¹Ù•¹Ñ½ÉäõÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½Éä°(€€€€€€€€€€€€€€€µ¥¹•‘}ÑåÁ•Ìõµ¥¹•‘}ÑåÁ•Ì°(€€€€€€€€€€€€¤(€€€€€€€€€€€¥˜É¥¡}Ñ•áÑ}É•Á…¥É•è(€€€€€€€€€€€€€€€™¥¹…±}¡•­Á½¥¹Ñ}¡…¹•€ôQÉÕ”(€€€€€€€€€€€€€€€€ŒQ¡”¡•±Á•ÈÁÉ•Í•ÉÙ•ÌÉ½Ü¥‘•¹Ñ¥Ñä…¹É•©•ÑÌ…¹äQåÁ•Ì(€€€€€€€€€€€€€€€€ŒÉ•İÉ¥Ñ”Ñ¡…Ğ¡…¹•Ì•á…ĞÍ½ÕÉ”½Ù•É…”¸I•½¹¥±”(€€€€€€€€€€€€€€€€Œ…¹½¹¥…°¥ÕÉ”Ñ…Ì½¹”µ½É”°Ñ¡•¸Í•¹Ñ¡•Í”•á…ĞÉ½İÌ(€€€€€€€€€€€€€€€€ŒÑ¼Ñ¡”ÍÑÉ¥Ğ…Ñ”¸(€€€€€€€€€€€€€€€½ÕĞ°É•Á…¥É•‘}™¥ÕÉ•}•á…µÁ±•Ì€ô€ (€€€€€€€€€€€€€€€€€€€}É•½¹¥±•}•áÁ±¥¥Ñ}™¥ÕÉ•}¥µ…•Ì¡½ÕĞ°Í•Ñ¥½¹Ì¤(€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€¥˜É•Á…¥É•‘}™¥ÕÉ•}•á…µÁ±•Ìè(€€€€€€€€€€€€€€€€€€€ÁÉ½É•ÍÌ¹±½œ (€€€€€€€€€€€€€€€€€€€€€€€€‰I•½¹¥±•€ˆ(€€€€€€€€€€€€€€€€€€€€€€€˜‰íÉ•Á…¥É•‘}™¥ÕÉ•}•á…µÁ±•Íô¥ÕÉ”á…µÁ±”¡Ì¤…™Ñ•È€ˆ(€€€€€€€€€€€€€€€€€€€€€€€€‰É¥ µÑ•áĞÉ•Á…¥È¸ˆ°(€€€€€€€€€€€€€€€€€€€€€€€±•Ù•°ô‰ÍÕ•ÍÌˆ°(€€€€€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€‰•™½É•}™¥¹…±}ÑåÁ•}¡•…‘¥¹}É•Á…¥È€ô½Áä¹‘••Á½Áä¡½ÕĞ¤(€€€€€€€€€€€½ÕĞ€ô}‘¥Í…µ‰¥Õ…Ñ•}•ÉÑ¥™¥•‘}ÍÁ±¥Ñ}ÑåÁ•}…Í•Ì (€€€€€€€€€€€€€€€½ÕĞ°ÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½Éä°µ¥¹•‘}ÑåÁ•Ì¤(€€€€€€€€€€€¥˜½ÕĞ€„ô‰•™½É•}™¥¹…±}ÑåÁ•}¡•…‘¥¹}É•Á…¥Èè(€€€€€€€€€€€€€€€½ÕĞ€ôÈ¹É•¹Õµ‰•É}ÑåÁ•Í}½¹Ñ¥¹Õ½ÕÍ±ä¡½ÕĞ¤(€€€€€€€€€€€€€€€™¥¹…±}¡•­Á½¥¹Ñ}¡…¹•€ôQÉÕ”(€€€€€€€€€€€‰•™½É•}™¥¹…±}É•É½Õ¹€ô½ÕĞ(€€€€€€€€€€€½ÕĞ€ô}É•É½Õ¹‘}‘É¥™Ñ•‘}™¥¹…±}Í½ÕÉ•}±…¥µÌ¡½ÕĞ¤(€€€€€€€€€€€¥˜½ÕĞ€„ô‰•™½É•}™¥¹…±}É•É½Õ¹è(€€€€€€€€€€€€€€€™¥¹…±}¡•­Á½¥¹Ñ}¡…¹•€ôQÉÕ”(€€€€€€€€€€€}Ù…±¥‘…Ñ•}™¥¹…±}½É}É…¥Í” (€€€€€€€€€€€€€€€½ÕĞ°(€€€€€€€€€€€€€€€ÍÑ…”ô‰™¥¹…°ˆ°(€€€€€€€€€€€€€€€¥¹Ù•¹Ñ½ÉäõÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½Éä°(€€€€€€€€€€€€€€€µ¥¹•‘}ÑåÁ•Ìõµ¥¹•‘}ÑåÁ•Ì°(€€€€€€€€€€€€€€€µ•Ñ¡½‘}…¹¡½ÉÌõµ•Ñ¡½‘}…¹¡½ÉÌ°(€€€€€€€€€€€€€€€Í½ÕÉ•}Ñ•áĞõµµ‘}Ñ•áĞ°(€€€€€€€€€€€€€€€™¥á•ÈõÀÍ}™¥á•È¹‘•™…Õ±Ñ}ÁÉ½Ù¥‘•È ¤°(€€€€€€€€€€€€¤(€€€€€€€€€€€½ÕĞ°™¥¹…±}ÑåÁ•}…Í•}Å¥‘}Á±…•µ•¹Ñ}±•‘•È€ô€ (€€€€€€€€€€€€€€€}™¥¹…±}ÑåÁ•}…Í•}Å¥‘}¡½ÍÑ}µ…¹¥™•ÍÑÌ (€€€€€€€€€€€€€€€€€€€½ÕĞ°(€€€€€€€€€€€€€€€€€€€ÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½Éä°(€€€€€€€€€€€€€€€€€€€µ¥¹•‘}ÑåÁ•Ì°(€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€¤(€€€€€€€€€€€É½Õ¹‘¥¹}•ÉÑ¥™¥…Ñ•}É•ÅÕ¥É•€ô…¹ä (€€€€€€€€€€€€€€€…¹ä (€€€€€€€€€€€€€€€€€€€ÍÑÈ¡É•½É¹•Ğ¡™¥•±¤½È€ˆˆ¤¹ÍÑÉ¥À ¤(€€€€€€€€€€€€€€€€€€€™½È™¥•±¥¸€ (€€€€€€€€€€€€€€€€€€€€€€€É½Õ¹‘¥¹}•ÉÑ¥™¥…Ñ”¹I=]}IQ%%Q}%1°(€€€€€€€€€€€€€€€€€€€€€€€É½Õ¹‘¥¹}•ÉÑ¥™¥…Ñ”¹M=UI}=9QIQ}%1°(€€€€€€€€€€€€€€€€€€€€€€€€‰}Í½ÕÉ•}É½Õ¹‘¥¹}½¹ÑÉ…Ğˆ°(€€€€€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€™½ÈÉ•½É¥¸½ÕĞ(€€€€€€€€€€€€¤(€€€€€€€€€€€¥˜ÁÉ½‘ÕÑ¥½¹}É½Õ¹‘¥¹}É•ÅÕ¥É•…¹¹½ĞÉ½Õ¹‘¥¹}•ÉÑ¥™¥…Ñ•}É•ÅÕ¥É•è(€€€€€€€€€€€€€€€É…¥Í”É½Õ¹‘¥¹}•ÉÑ¥™¥…Ñ”¹É½Õ¹‘¥¹•ÉÑ¥™¥…Ñ•ÉÉ½È (€€€€€€€€€€€€€€€€€€€€‰±¥Ù”…¹½¹¥…°µÍ½ÕÉ”½ÕÑÁÕĞ¡…Ì¹¼¥¹‘•Á•¹‘•¹Ñ±ä€ˆ(€€€€€€€€€€€€€€€€€€€€‰É½Õ¹‘•Á±…•µ•¹Ğ…ÕÑ¡½É¥Ñäˆ(€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€™¥¹…±}É½Õ¹‘¥¹}•ÉÑ¥™¥…Ñ”€ô€ (€€€€€€€€€€€€€€€É½Õ¹‘¥¹}•ÉÑ¥™¥…Ñ”¹‰Õ¥±‘}™¥¹…±}•ÉÑ¥™¥…Ñ” (€€€€€€€€€€€€€€€€€€€½ÕĞ°(€€€€€€€€€€€€€€€€€€€ÑåÁ•}…Í•}Å¥‘}Á±…•µ•¹Ñ}±•‘•Èô (€€€€€€€€€€€€€€€€€€€€€€€™¥¹…±}ÑåÁ•}…Í•}Å¥‘}Á±…•µ•¹Ñ}±•‘•È(€€€€€€€€€€€€€€€€€€€€¤°(€€€€€€€€€€€€€€€€€€€€ŒQ¡”É•İÉ¥ÑÑ•¸A¡…Í”€Ì¹•Ù•Èµ¥¹ÑÌÁ•ÈµÉ½ÜÁ±…•µ•¹Ğ(€€€€€€€€€€€€€€€€€€€€Œ½¹ÑÉ…ÑÌìÉ½Ü•ÉÑ¥™¥…Ñ•Ì…ÉÉäÑ¡”…ÕÑ¡½É¥Ñä¸(€€€€€€€€€€€€€€€€€€€É•ÅÕ¥É•}Á±…•µ•¹Ñ}½¹ÑÉ…ÑÌõ…±Í”°(€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€¥˜É½Õ¹‘¥¹}•ÉÑ¥™¥…Ñ•}É•ÅÕ¥É•(€€€€€€€€€€€€€€€•±Í”9½¹”(€€€€€€€€€€€€¤(€€€€€€€€€€€¥˜Í…Ù•‘}™¥¹…°…¹¹½Ğ™¥¹…±}¡•­Á½¥¹Ñ}¡…¹•è(€€€€€€€€€€€€€€€€ŒÉ•ÍÑ½É•€äà”¡•­Á½¥¹Ğ¥ÌÉ•ÕÍ…‰±”½¹±äİ¡•¸Ñ¡”•á…Ğ(€€€€€€€€€€€€€€€€ŒÁ…å±½…½•Ù¥‘•¹”É•±…Ñ¥½¹Í¡¥ÀÍÑ¥±°µ…Ñ¡•ÌÑ¡”•ÉÑ¥™¥…Ñ”(€€€€€€€€€€€€€€€€Œµ¥¹Ñ•…™Ñ•È¥ÑÌÉ•…°É½Õ¹‘¥¹œÁÉ½Ù¥‘•È€¬É¥Ñ¥ŒÁ…ÍÌ¸(€€€€€€€€€€€€€€€¥˜É½Õ¹‘¥¹}•ÉÑ¥™¥…Ñ•}É•ÅÕ¥É•è(€€€€€€€€€€€€€€€€€€€™É½´€¸¥µÁ½ÉĞ…¹½¹¥…±}Í½ÕÉ•}Á¡…Í”Ì…ÌÁ¡…Í”Ì((€€€€€€€€€€€€€€€€€€€…Ñ¥Ù•}Í•µ…¹Ñ¥}É…Á €ôÁ¡…Í”Ì¹…Ñ¥Ù•}É…Á  ¤(€€€€€€€€€€€€€€€€€€€É½Õ¹‘¥¹}•ÉÑ¥™¥…Ñ”¹Ù•É¥™å}™¥¹…±}•ÉÑ¥™¥…Ñ” (€€€€€€€€€€€€€€€€€€€€€€€½ÕĞ°(€€€€€€€€€€€€€€€€€€€€€€€Í…Ù•‘}™¥¹…°¹•Ğ (€€€€€€€€€€€€€€€€€€€€€€€€€€€É½Õ¹‘¥¹}•ÉÑ¥™¥…Ñ”¹%91}IQ%%Q}%1(€€€€€€€€€€€€€€€€€€€€€€€€¤°(€€€€€€€€€€€€€€€€€€€€€€€Í•µ…¹Ñ¥}É…Á ô (€€€€€€€€€€€€€€€€€€€€€€€€€€€…Ñ¥Ù•}Í•µ…¹Ñ¥}É…Á (€€€€€€€€€€€€€€€€€€€€€€€€€€€¥˜¥Í¥¹ÍÑ…¹”¡…Ñ¥Ù•}Í•µ…¹Ñ¥}É…Á °‘¥Ğ¤(€€€€€€€€€€€€€€€€€€€€€€€€€€€•±Í”9½¹”(€€€€€€€€€€€€€€€€€€€€€€€€¤°(€€€€€€€€€€€€€€€€€€€€€€€É•ÅÕ¥É•}Í•µ…¹Ñ¥}É…Á õQÉÕ”°(€€€€€€€€€€€€€€€€€€€€€€€É•ÅÕ¥É•}Á±…•µ•¹Ñ}½¹ÑÉ…ÑÌô (€€€€€€€€€€€€€€€€€€€€€€€€€€€ÁÉ½‘ÕÑ¥½¹}É½Õ¹‘¥¹}É•ÅÕ¥É•(€€€€€€€€€€€€€€€€€€€€€€€€¤°(€€€€€€€€€€€€€€€€€€€€€€€ÑåÁ•}…Í•}Å¥‘}Á±…•µ•¹Ñ}±•‘•Èô (€€€€€€€€€€€€€€€€€€€€€€€€€€€™¥¹…±}ÑåÁ•}…Í•}Å¥‘}Á±…•µ•¹Ñ}±•‘•È(€€€€€€€€€€€€€€€€€€€€€€€€¤°(€€€€€€€€€€€€€€€€€€€€¤(€€€€€€€•á•ÁĞ€¡!Õµ…¹•¥Í¥½¹I•ÅÕ¥É•°AÉ½Ù¥‘•ÉI•ÍÁ½¹Í•½¹ÑÉ…ÑÉÉ½È¤è(€€€€€€€€€€€€ŒM•µ…¹Ñ¥Œ‘•¥Í¥½¹Ì…¹µ•¡…¹¥…°ÁÉ½Ù¥‘•Èµ½¹ÑÉ…Ğ™…¥±ÕÉ•Ì…É”(€€€€€€€€€€€€Œ½ÕÑ½µ•Ì½˜Ñ¡”¹•Ü•á…ĞÉ”µÉ½Õ¹‘¥¹œÉ•ÅÕ•ÍĞ¸Q¡•äµÕÍĞÉ•… (€€€€€€€€€€€€Œ½É¡•ÍÑÉ…Ñ¥½¸•á…Ñ±ä½¹”ì‘¥Í…É‘¥¹œ„Í…Ù•™¥¹…°¡•­Á½¥¹Ğ(€€€€€€€€€€€€Œ¡•É”İ½Õ±¥µµ•‘¥…Ñ•±ä‘¥ÍÁ…Ñ Ñ¡”Í…µ”É•ÅÕ•ÍĞ……¥¸¸(€€€€€€€€€€€É…¥Í”(€€€€€€€•á•ÁĞIÕ¹Ñ¥µ•ÉÉ½Èè(€€€€€€€€€€€¥˜¹½ĞÍ…Ù•‘}™¥¹…°è(€€€€€€€€€€€€€€€É…¥Í”(€€€€€€€€€€€€Œ™¥¹…°¡•­Á½¥¹ĞÁÉ½‘Õ•‰ä…¸½±‘•È‘•Á±½åµ•¹Ğµ…äÙ¥½±…Ñ”„(€€€€€€€€€€€€ŒÉÕ±”Ñ¡…Ğ…¹¹½Ğ‰”É•Á…¥É•Í…™•±ä¥¸Á±…”¸I•ÕÍ”Ñ¡”ÍÑ…”(€€€€€€€€€€€€Œ¥µµ•‘¥…Ñ•±ä‰•™½É”¥Ğ€¡½ÈÉ••¹•É…Ñ”İ¡•¸¹¼ÁÉ¥½ÈÍÑ…”•á¥ÍÑÌ¤(€€€€€€€€€€€€Œ¥¹ÍÑ•…½˜É•±½…‘¥¹œÑ¡”Í…µ”É•©•Ñ•€äà”Á…å±½…™½É•Ù•È¸(€€€€€€€€€€€ÁÉ½É•ÍÌ¹±½œ (€€€€€€€€€€€€€€€€‰M…Ù•™¥¹…°¡•­Á½¥¹Ğ‘¥¹½ĞÁ…ÍÌÍÑÉ¥ĞÙ…±¥‘…Ñ¥½¸ì€ˆ(€€€€€€€€€€€€€€€€‰É•ÍÕµ¥¹œ™É½´Ñ¡”ÁÉ••‘¥¹œ¡•­Á½¥¹Ğ¥¹ÍÑ•…½˜É•ÑÉå¥¹œ€ˆ(€€€€€€€€€€€€€€€€‰Ñ¡”Í…µ”€äà”½¹Ñ•¹Ğ¸ˆ°(€€€€€€€€€€€€€€€±•Ù•°ô‰İ…É¹¥¹œˆ°(€€€€€€€€€€€€¤(€€€€€€€€€€€¥˜¡•­Á½¥¹Ñ}…±±‰…¬¥Ì¹½Ğ9½¹”è(€€€€€€€€€€€€€€€¡•­Á½¥¹Ñ}…±±‰…¬¡ì(€€€€€€€€€€€€€€€€€€€€‰¡•­Á½¥¹Ñ}…Ñ¥½¸ˆè€‰‘¥Í…É‘}ÍÑ…”ˆ°(€€€€€€€€€€€€€€€€€€€€‰ÍÑ…”ˆè€‰™¥¹…±}½¹Ñ•¹Ñ}É•…‘äˆ°(€€€€€€€€€€€€€€€€€€€€‰É•…Í½¸ˆè€‰ÍÑÉ¥ĞÑ•Éµ¥¹…°Ù…±¥‘…Ñ¥½¸™…¥±•ˆ°(€€€€€€€€€€€€€€€ô¤(€€€€€€€€€€€É•ÑÕÉ¸½¹•ÁÑÍ}™É½µ}µµ (€€€€€€€€€€€€€€€µµ‘}Ñ•áĞ°(€€€€€€€€€€€€€€€ÍÕ‰©•ĞõÍÕ‰©•Ğ°(€€€€€€€€€€€€€€€‰½…Éõ‰½…É°(€€€€€€€€€€€€€€€É…‘”õÉ…‘”°(€€€€€€€€€€€€€€€Õ¹¥ĞõÕ¹¥Ğ°(€€€€€€€€€€€€€€€¡…ÁÑ•É}Ñ¥Ñ±”õ¡…ÁÑ•É}Ñ¥Ñ±”°(€€€€€€€€€€€€€€€¡…ÁÑ•É}¥õ¡…ÁÑ•É}¥°(€€€€€€€€€€€€€€€¡…ÁÑ•É}½‘”õ¡…ÁÑ•É}½‘”°(€€€€€€€€€€€€€€€±•…É¹¥¹}­¥¹õ±•…É¹¥¹}­¥¹°(€€€€€€€€€€€€€€€±¥Ù”õQÉÕ”°(€€€€€€€€€€€€€€€…ÉÑ¥™…ÑÌõ…ÉÑ¥™…ÑÌ°(€€€€€€€€€€€€€€€É•ÍÕµ•}¡•­Á½¥¹Ğõ}İ¥Ñ¡½ÕÑ}½¹•ÁÑ}¡•­Á½¥¹Ñ}ÍÑ…” (€€€€€€€€€€€€€€€€€€€É•ÍÕµ•}¡•­Á½¥¹Ğ°€‰™¥¹…±}½¹Ñ•¹Ñ}É•…‘äˆ¤°(€€€€€€€€€€€€€€€¡•­Á½¥¹Ñ}…±±‰…¬õ¡•­Á½¥¹Ñ}…±±‰…¬°(€€€€€€€€€€€€€€€½µÁ±•Ñ¥½¹}ÁÉ½É•ÍÌõ½µÁ±•Ñ¥½¹}ÁÉ½É•ÍÌ°(€€€€€€€€€€€€¤(€€€€€€€€Œ™¥¹…±}½¹Ñ•¹Ñ}É•…‘å€¥Ì„ÁÉ½µ¥Í”Ñ¡…ĞÑ¡”•á…Ğµ…Ñ•É¥…±¥é•É½İÌ(€€€€€€€€ŒÁ…ÍÍ•Ñ¡”ÍÑÉ¥Ğ½ÕÑ•È…Ñ”¸€A•ÉÍ¥ÍĞ½¹±ä…™Ñ•ÈÑ¡…ĞÁÉ½µ¥Í”¥ÌÑÉÕ”(€€€€€€€€ŒÍ¼„É•ÑÉä…¹¹½Ğ±½½À™½É•Ù•È½¸Ñ¡”Í…µ”¥¹Ù…±¥€äà”¡•­Á½¥¹Ğ¸(€€€€€€€¥˜…ÉÑ¥™…ÑÌ¥Ì¹½Ğ9½¹”…¹™¥¹…±}É½Õ¹‘¥¹}•ÉÑ¥™¥…Ñ”¥Ì¹½Ğ9½¹”è(€€€€€€€€€€€…ÉÑ¥™…ÑÍl(€€€€€€€€€€€€€€€É½Õ¹‘¥¹}•ÉÑ¥™¥…Ñ”¹%91}IQ%%Q}%1(€€€€€€€€€€€t€ô½Áä¹‘••Á½Áä¡™¥¹…±}É½Õ¹‘¥¹}•ÉÑ¥™¥…Ñ”¤(€€€€€€€¥˜™¥¹…±}¡•­Á½¥¹Ñ}¡…¹•è(€€€€€€€€€€€}•µ¥Ñ}½¹•ÁÑ}¡•­Á½¥¹Ğ (€€€€€€€€€€€€€€€¡•­Á½¥¹Ñ}…±±‰…¬°(€€€€€€€€€€€€€€€€‰™¥¹…±}½¹Ñ•¹Ñ}É•…‘äˆ°(€€€€€€€€€€€€€€€É•½É‘Ìõ½ÕĞ°(€€€€€€€€€€€€€€€ÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½ÉäõÅÕ•ÍÑ¥½¹}Ñ…Í­}¥¹Ù•¹Ñ½Éä°(€€€€€€€€€€€€€€€µ¥¹•‘}ÑåÁ•Ìõµ¥¹•‘}ÑåÁ•Ì°(€€€€€€€€€€€€€€€µ•Ñ¡½‘}É½İ}Í¹…ÁÍ¡½Ğõ}Í•É¥…±¥é•}µ•Ñ¡½‘}É½İ}Í¹…ÁÍ¡½Ğ (€€€€€€€€€€€€€€€€€€€µ•Ñ¡½‘}É½İ}Í¹…ÁÍ¡½Ğ¤°(€€€€€€€€€€€€€€€€¨©ì(€€€€€€€€€€€€€€€€€€€É½Õ¹‘¥¹}•ÉÑ¥™¥…Ñ”¹%91}IQ%%Q}%1è(€€€€€€€€€€€€€€€€€€€™¥¹…±}É½Õ¹‘¥¹}•ÉÑ¥™¥…Ñ”°(€€€€€€€€€€€€€€€€€€€€‰É½Õ¹‘¥¹}•ÉÑ¥™¥…Ñ•}É•ÅÕ¥É•ˆè€ (€€€€€€€€€€€€€€€€€€€€€€€É½Õ¹‘¥¹}•ÉÑ¥™¥…Ñ•}É•ÅÕ¥É•(€€€€€€€€€€€€€€€€€€€€¤°(€€€€€€€€€€€€€€€€€€€€¨¨ (€€€€€€€€€€€€€€€€€€€€€€€ì(€€€€€€€€€€€€€€€€€€€€€€€€€€€A!MÍ}AI}I1M}%1è½Áä¹‘••Á½Áä (€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€Á¡…Í”Í}ÁÉ•}É•±•…Í•}…ÕÑ¡½É¥Ñä(€€€€€€€€€€€€€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€€€€€€€€€ô(€€€€€€€€€€€€€€€€€€€€€€€¥˜Á¡…Í”Í}ÁÉ•}É•±•…Í•}…ÕÑ¡½É¥Ñä¥Ì¹½Ğ9½¹”(€€€€€€€€€€€€€€€€€€€€€€€•±Í”íô(€€€€€€€€€€€€€€€€€€€€¤°(€€€€€€€€€€€€€€€ô°(€€€€€€€€€€€€¤(€€€€€€€µ¥ÍÍ¥¹œ€ôÍÕ´ (€€€€€€€€€€€€Ä™½ÈÈ¥¸½ÕĞ(€€€€€€€€€€€¥˜¹½Ğ}¡…Í}µ•…¹¥¹™Õ±}ÑåÁ•Ì¡È¹•Ğ ‰½¹•ÁÑ}‘•Ñ…¥±Ìˆ°€ˆˆ¤¤(€€€€€€€€€€€…¹¹½ĞÈ¹¥Í}Õ±µ¥¹…Ñ¥½¸¡È¹•Ğ ‰½¹•ÁÑ}Ñ¥Ñ±”ˆ°€ˆˆ¤¤(€€€€€€€€¤(€€€€€€€¥˜µ¥ÍÍ¥¹œè(€€€€€€€€€€€ÁÉ½É•ÍÌ¹±½œ (€€€€€€€€€€€€€€€˜‰íµ¥ÍÍ¥¹ô¹½¸µÕ±µ¥¹…Ñ¥½¸½¹•ÁĞ¡Ì¤ÍÑ¥±°±…¬QåÁ•Ì…™Ñ•È…±°Á…ÍÍ•Ì¸ˆ°(€€€€€€€€€€€€€€€±•Ù•°ô‰İ…É¹¥¹œˆ°(€€€€€€€€€€€€¤(€€€€€€€€ŒQ¡”¡…É…Ñ•Èµ±•¹Ñ €‰•áÁ•Ñ•É½Ü½Õ¹Ğˆİ…É¹¥¹œ¥ÌÁÕÉ•(€€€€€€€€Œ€¡‘½Ì½…•¥ÌµÉ•ÍÑÉÕÑÕÉ”¹µƒ
œÌ¤èÍ½ÕÉ”Ù½±Õµ”¹•Ù•È©Õ‘•Ì(€€€€€€€€Œİ¡•Ñ¡•È„¡…ÁÑ•ÈÌ½¹•ÁĞ½Õ¹Ğ¥ÌÉ¥¡ĞƒŠPÑ¡”µ½‘•°‘½•Ì°(€€€€€€€€Œ…Ğ•áÑÉ…Ñ¥½¸Ñ¥µ”°™É½´İ¡…ĞÑ¡”‰½½¬Ñ•…¡•Ì¸(€€€€€€€ÁÉ½É•ÍÌ¹Í•Ñ}ÁÉ½É•ÍÌ (€€€€€€€€€€€µ…à À¸À°µ¥¸ Ä¸À°™±½…Ğ¡½µÁ±•Ñ¥½¹}ÁÉ½É•ÍÌ¤¤¤°(€€€€€€€€€€€±…‰•°ô‰½¹•ÁĞ•áÑÉ…Ñ¥½¸½µÁ±•Ñ”ˆ°(€€€€€€€€¤(€€€€€€€ÁÉ½É•ÍÌ¹±½œ¡˜‰¥¹…°½¹•ÁĞ½Õ¹Ğèí±•¸¡½ÕĞ¥ô¸ˆ°±•Ù•°ô‰ÍÕ•ÍÌˆ¤(€€€€€€€É•ÑÕÉ¸½ÕĞ(€€€½¹™¥œ¹É•ÅÕ¥É•}•¹•É…Ñ¥½¹}±¥Ù” ¤(€€€ÁÉ½É•ÍÌ¹±½œ¡˜‰áÑÉ…Ñ¥¹œ½¹•ÁÑÌ€¡‘Éä¤™É½´í±•¸¡µµ‘}Ñ•áĞ¤è±ô¡…ÉÌ¸ˆ¤(€€€€ŒÉäèÑÉ•…Ğµ…É­‘½İ¸¡•…‘¥¹Ì…ÌÑ½Á¥Ì…¹‰Õ±±•Ğ½Á…É„±¥¹•Ì…Ì½¹•ÁÑÌ¸(€€€Ñ½Á¥Œ€ô€‰Q½Á¥Œ€ÀÄè=Ù•ÉÙ¥•Üˆ(€€€½ÕĞè±¥ÍÑm‘¥Ñt€ômt(€€€™½È±¥¹”¥¸µµ‘}Ñ•áĞ¹ÍÁ±¥Ñ±¥¹•Ì ¤è(€€€€€€€±¥¹”€ô±¥¹”¹ÍÑÉ¥À ¤(€€€€€€€¥˜¹½Ğ±¥¹”è(€€€€€€€€€€€½¹Ñ¥¹Õ”(€€€€€€€¥˜±¥¹”¹ÍÑ…ÉÑÍİ¥Ñ  ˆŒŒˆ¤è(€€€€€€€€€€€Ñ½Á¥Œ€ô±¥¹”¹±ÍÑÉ¥À ˆŒ€ˆ¤¹ÍÑÉ¥À ¤½ÈÑ½Á¥Œ(€€€€€€€•±¥˜±¥¹”¹ÍÑ…ÉÑÍİ¥Ñ  ˆŒˆ¤è(€€€€€€€€€€€½¹Ñ¥¹Õ”(€€€€€€€•±Í”è(€€€€€€€€€€€Ñ¥Ñ±”€ô±¥¹”¹ÍÁ±¥Ğ ˆèˆ¥lÁt¹ÍÁ±¥Ğ ˆ¸ˆ¥lÁt¹ÍÑÉ¥À ¥lèàÁt½È€‰½¹•ÁĞˆ(€€€€€€€€€€€½ÕĞ¹…ÁÁ•¹¡ì(€€€€€€€€€€€€€€€€‰Ñ½Á¥ŒˆèÑ½Á¥Œ°(€€€€€€€€€€€€€€€€‰Á…É•¹Ñ}½¹•ÁĞˆèÑ½Á¥Œ°(€€€€€€€€€€€€€€€€‰½¹•ÁÑ}Ñ¥Ñ±”ˆèÑ¥Ñ±”°(€€€€€€€€€€€€€€€€‰½¹•ÁÑ}‘•Ñ…¥±Ìˆè˜‰•ÍÉ¥ÁÑ¥½¸èí±¥¹•lèÈÀÁuôˆ°(€€€€€€€€€€€€€€€€‰­•åİ½É‘Ìˆè€ˆ°€ˆ¹©½¥¸¡Ñ¥Ñ±”¹±½İ•È ¤¹ÍÁ±¥Ğ ¥lèÕt¤°(€€€€€€€€€€€ô¤(€€€½ÕĞ€ô½ÕĞ½Èmì(€€€€€€€€‰Ñ½Á¥ŒˆèÑ½Á¥Œ°€‰½¹•ÁÑ}Ñ¥Ñ±”ˆè€‰=Ù•ÉÙ¥•Üˆ°(€€€€€€€€‰Á…É•¹Ñ}½¹•ÁĞˆèÑ½Á¥Œ°(€€€€€€€€‰½¹•ÁÑ}‘•Ñ…¥±Ìˆè€‰•ÍÉ¥ÁÑ¥½¸è€¡•µÁÑä‘½Õµ•¹Ğ¤ˆ°(€€€€€€€€‰­•åİ½É‘Ìˆè€ˆˆ°(€€€õt(€€€€ŒÉäÁ…Ñ è¹¼Õ±µ¥¹…Ñ¥½¸Íå¹Ñ¡•Í¥ÌƒŠP„Õ±µ¥¹…Ñ¥½¸¥Ìµ½‘•°İ½É¬¸(€€€É•ÑÕÉ¸È¹É•™¥¹•}¡…ÁÑ•È¡}•¹ÍÕÉ•}Á…É•¹Ñ}½¹•ÁÑÌ¡½ÕĞ¤¤
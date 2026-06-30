"""Content generation: questions from concepts, concepts from MMD.

All functions have a dry path (deterministic, no API keys — used for the MVP
and tests) and a live hook that delegates to the vendored OpenAI-backed
scripts. The dry path is intentionally realistic: it returns fully-populated
records so the post-generation pipeline and the canonical writer are always
exercised end to end.
"""
from __future__ import annotations

import os
import re

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
    label="Types classification guidance (math/physics)",
    default="""\
   Types classify EVERY distinct question/numerical variety under the concept —
   this is how teachers later pick which varieties to assess. Extract ALL
   varieties from the section AND from related exercise problems (fold exercises
   into the concept they test, never as separate topics).
   Each variety = one solving/answering pattern (e.g. direct evaluation, word
   problem, proof, diagram-based, unit conversion). Under each variety list
   concrete Case prompts (specific "Evaluate…", "Find…", "Prove…", "Draw…"
   stems drawn from the chapter).
   Generate generously — many varieties with multiple cases each; the team
   manually keeps what they need. Only skip Types when the concept is purely
   definitional with zero assessable question formats.""")

prompts.register(
    "concepts.types_guidance.descriptive", category=_CONCEPTS_CAT,
    label="Types classification guidance (other subjects)",
    default="""\
   Types classify EVERY distinct question/problem variety under the concept —
   numerical drills, diagram tasks, short-answer formats, application scenarios,
   comparison prompts, map/data exercises, etc. Extract ALL varieties from the
   section AND from related exercise problems (fold exercises into the concept
   they test).
   Each variety = one assessable format. Under each variety list concrete Case
   prompts tied to the chapter content.
   Generate generously — many varieties with multiple cases; the team manually
   keeps what they need. Only skip Types when the concept is purely
   definitional/recall with zero assessable formats.""")

prompts.register(
    "concepts.types_example", category=_CONCEPTS_CAT,
    label="Types section format example",
    default=(
        "Types: Type 01: Evaluating numerical exponential expressions "
        "Case 01: Evaluate 2^3 × 2^2 Case 02: Evaluate (3^2)^4 "
        "Case 03: Simplify and find the value "
        "Type 02: Simplifying using laws of indices "
        "Case 01: Simplify a^m × a^n Case 02: Express as a single power "
        "Type 03: Word problems involving exponents "
        "Case 01: Given population growth rate find final count "
        "Case 02: Compare two exponential models"
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
  Format — use zero-padded numeric labels exactly "Type 01:", "Case 01:":
  Types: Type 01: <variety title> Case 01: <concrete worked example prompt>
  Case 02: <...> Type 02: <next variety title> Case 01: <...> ...
  Restart at Type 01 within each concept — they are renumbered continuously
  across the whole chapter afterwards, so do NOT try to continue numbers yourself.
- Example Types block:
  {{types_example}}
- End with Misconception ONLY IF there is a real likely learner error from the
  material. Do not invent filler misconceptions, and never write "N/A", "None",
  "Not applicable", or placeholder text.
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
            "add Types only when there are problems/numericals/exercises/"
            "assessable formats; add Misconception only when there is a real "
            "likely learner error. Types use zero-padded 'Type 01:'/'Case 01:' labels:")


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
   Types section classifying ALL distinct question/numerical varieties (including
   exercise-section problems folded into the concept they test). Use zero-padded
   numeric labels: Type 01: <name> Case 01: <prompt> Case 02: ... Type 02: ...
   (restart at Type 01 per concept; continuous renumbering happens downstream).
   Only omit Types for concepts that are purely definitional with zero assessable
   formats. If the draft omitted Types where they belong, ADD them.

5. **Culmination.** Every topic ends with exactly one "Culmination - ..." row
   that integrates that topic's ideas. Place it last within its topic.

6. **Preserve order.** Keep textbook reading order for topics and concepts.

7. **No groups.** Do not mention groups, group columns, or assessment labels.

8. **Hygiene.** Keep Description // Types // Misconception structure; no source-artifact
   references ("Example 19", "Fig 2", "MMD"). Misconception is optional: keep it
   only when it is specific and useful; never write N/A/None/filler.

9. **Chapter source.** When CHAPTER SOURCE text is provided, mine it for exercise
   problems and numerical varieties to populate Types under the concepts they test.

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

INPUT: a draft concept map (Description is already refined; Types and
Misconception may or may not exist) plus CHAPTER SOURCE text.

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
4. Format — zero-padded numeric labels exactly "Type 01:", "Case 01:":
   Types: Type 01: <variety title> Case 01: <concrete prompt> Case 02: ...
   Type 02: <next variety> Case 01: ... (restart at Type 01 per concept;
   continuous renumbering across the chapter happens downstream).
5. Example:
   {{types_example}}
6. Mine CHAPTER SOURCE for ALL exercise problems and numerical varieties; fold
   each into the concept it tests as Types/Cases.
7. Omit Types for purely definitional concepts with zero assessable formats.
   Every problem-solving, calculation, application, or exercise-backed concept
   MUST have Types with at least two varieties and multiple Cases.
8. Culmination rows MUST include Types for mixed multi-concept application problems.
9. NEVER mention groups or group columns.""")


prompts.register(
    "concepts.skeleton.system", category=_CONCEPTS_CAT,
    label="Concept skeleton extraction system prompt",
    default="""\
Extract ONLY a clean teachable concept skeleton from a textbook section.
Return ONLY strict JSON:
{"rows":[{"topic":"","parent_concept":"","concept":"","concept_description":"","keywords":"","source_evidence":""}]}.

Rules:
- Use the textbook section heading as topic when available; strip section numbers.
- Do not create exercise/example/review topics.
- Parent Concept is a cluster heading within a topic.
- Concept is one small teachable mastery unit.
- No Types, no culmination rows, no groups, no assessment labels.
- No vague names: Introduction, Overview, Basics, Misc, Examples, Practice.
- Avoid repeated sibling openers.
- concept_description starts with "Description:" and is 2-4 compact sentences.
- Keep source_evidence short: the phrase/heading/problem source that justifies the concept.
""")

prompts.register(
    "concepts.canonicalize.system", category=_CONCEPTS_CAT,
    label="Chapter-wide concept canonicalization system prompt",
    default="""\
Clean a full chapter concept skeleton after all chunks have been merged.
Return ONLY strict JSON with the same schema:
{"rows":[{"topic":"","parent_concept":"","concept":"","concept_description":"","keywords":"","source_evidence":""}]}.

Rules:
- Merge duplicates and ensure each concept appears once.
- Remove weak filler concepts.
- Preserve textbook/topic order.
- Rewrite repetitive names.
- Parent concepts should group 3-8 related concepts where possible.
- Do not create culmination rows.
- Do not generate Types.
""")

prompts.register(
    "concepts.description_refine.system", category=_CONCEPTS_CAT,
    label="Description-only concept refinement system prompt",
    default="""\
You are a description-only editor. Rewrite only Description sections for a refined concept map.
Return ONLY strict JSON with the same rows and order.

Rules:
- Keep topic, parent_concept, concept name, keywords, and row order unchanged.
- Rewrite only the Description section.
- Description answers: what the concept is; what rule/process/relationship/method matters;
  when/why it is used; what indicates mastery.
- Use 45-90 words unless the concept is very simple.
- Do not include Types.
- Misconception may be included only if specific and useful.
- No N/A, None, Not applicable, or placeholder text.
""")

prompts.register(
    "concepts.types_assign.system", category=_CONCEPTS_CAT,
    label="Types-only concept assignment system prompt",
    default="""\
You are a Types-only classifier. Assign Types only for assessable concepts.
Return ONLY strict JSON with the same rows and order.

Rules:
- Preserve Description exactly.
- Insert or replace only Types.
- One Type = one distinct question/problem pattern.
- One Case = one concrete question prompt/stem.
- For math/physics/chemistry, mine examples and exercises deeply.
- For theory subjects, include explain, compare, reason, diagram, data/source-based,
  application, and misconception-correction formats where relevant.
- Omit Types only for pure vocabulary/recall concepts with zero meaningful assessable varieties.
- Culmination rows are not handled here.
- Use zero-padded labels exactly "Type 01:" and "Case 01:".
""")

prompts.register(
    "concepts.culmination.system", category=_CONCEPTS_CAT,
    label="Topic culmination builder system prompt",
    default="""\
Build culmination rows after normal concepts and Types are finalized.
Return ONLY strict JSON with the full concept map.

Rules:
- Exactly one culmination row per topic.
- Name: "Culmination - <A>, <B> and <C>".
- Use the main ideas in that topic.
- Description must be exactly: "Description: Recap".
- Types must contain mixed multi-concept application/problem formats.
- Place culmination last within the topic.
- Do not change normal rows except to position the culmination correctly.
""")

prompts.register(
    "concepts.repair.system", category=_CONCEPTS_CAT,
    label="Concept validation repair system prompt",
    default="""\
Repair only concept rows that failed validation.
Return ONLY strict JSON with corrected rows.

Rules:
- Fix only the listed issues.
- Preserve valid rows.
- Never add filler.
- Keep strict JSON.
""")


def _concepts_system(subject: str) -> str:
    return prompts.get_text("concepts.skeleton.system")


def _metadata(
    *, subject: str = "", board: str = "", grade: str = "", unit: str = "",
    chapter_title: str = "", chapter_id: int | str | None = None,
    chapter_code: str = "", learning_kind: str = "Post",
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
    }


def _metadata_block(meta: dict) -> str:
    return (
        f"Subject: {meta.get('subject', '')}\n"
        f"Board: {meta.get('board', '')}\n"
        f"Grade: {meta.get('grade', '')}\n"
        f"Unit: {meta.get('unit', '')}\n"
        f"Chapter: {meta.get('chapter_title', '')}\n"
        f"Chapter ID/Code: {meta.get('chapter_id', '')} / {meta.get('chapter_code', '')}\n"
        f"Learning kind: {meta.get('learning_kind', 'Post')}"
    )


def _openai_json(system: str, user: str, max_tokens: int | None = None,
                 retries: int = 3) -> dict:
    """One JSON-mode chat call with retries; returns the parsed object."""
    import json
    import time
    from openai import OpenAI

    limit = config.OPENAI_MAX_OUTPUT_TOKENS if max_tokens is None else max_tokens
    client = OpenAI()
    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
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
        except Exception as e:  # noqa: BLE001 — retry then surface
            last_err = e
            if attempt < retries:
                time.sleep(2)
    raise RuntimeError(f"OpenAI extraction failed after {retries} retries: {last_err!r}")


def _trim(text: str, max_chars: int = 220_000) -> str:
    if len(text) <= max_chars:
        return text
    return text[: int(max_chars * 0.7)] + "\n\n[...TRIMMED...]\n\n" + text[-int(max_chars * 0.3):]


# How many characters of MMD to send per GPT call. We chunk (never trim) so no
# chapter content is lost: each chunk is processed in full and the results are
# merged. Sized so a chunk's worth of concepts/questions fits comfortably in one
# response, avoiding output truncation on long chapters.
_MMD_CHUNK_CHARS = int(os.environ.get("AEGIS_MMD_CHUNK_CHARS", "45000"))


def _split_mmd_into_chunks(mmd_text: str, max_chars: int | None = None) -> list[str]:
    """Split an MMD document into ordered chunks without dropping any content.

    Splits on Markdown headings so each chunk is a run of whole sections; a
    single section larger than ``max_chars`` is hard-split on paragraph
    boundaries. The concatenation of all chunks equals the original text
    (whitespace aside) — nothing is trimmed.
    """
    if max_chars is None:
        max_chars = _MMD_CHUNK_CHARS
    text = mmd_text or ""
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
    r"^\s*(?:chapter\s+)?(?:\d+(?:\.\d+)*|[A-Z])[\).\s:-]+", re.IGNORECASE)


def _strip_section_number(title: str) -> str:
    title = re.sub(r"\s+", " ", (title or "").strip())
    return _SECTION_NUM_PREFIX_RE.sub("", title).strip() or title


def parse_mmd_sections(mmd_text: str) -> list[dict]:
    """Parse MMD into ordered heading-aware sections with exercise tagging."""
    text = mmd_text or ""
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
            heading = _strip_section_number(m.group(2))
            stack = [(lv, h) for lv, h in stack if lv < level]
            stack.append((level, heading))
            current = {"heading": heading, "heading_path": [h for _, h in stack],
                       "body": line + "\n"}
            continue
        if current is None:
            current = {"heading": "", "heading_path": [], "body": ""}
        current["body"] += line + "\n"
    finish()
    if not sections and text.strip():
        sections = [{
            "heading": "General",
            "heading_path": ["General"],
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
        block = (
            "HEADING PATH: " + " > ".join(section.get("heading_path") or []) + "\n"
            f"PREVIOUS HEADING: {section.get('previous_heading', '')}\n"
            f"NEXT HEADING: {section.get('next_heading', '')}\n"
            "SECTION TEXT:\n" + section.get("body", "")
        )
        if exercises:
            block += "\nEXERCISE BLOCKS FOR TYPES PASS:\n" + exercises
        blocks.append(block.strip())
    return "\n\n--- SECTION ---\n\n".join(blocks)


def _section_aware_chunks(mmd_text: str, max_chars: int | None = None) -> list[dict]:
    """Pack parsed sections into chunks while preserving heading context."""
    if max_chars is None:
        max_chars = _MMD_CHUNK_CHARS
    sections = parse_mmd_sections(mmd_text)
    chunks: list[dict] = []
    buf: list[dict] = []
    for section in sections:
        candidate = buf + [section]
        if buf and len(_format_section_chunk(candidate)) > max_chars:
            chunks.append({"sections": buf, "text": _format_section_chunk(buf)})
            buf = [section]
        else:
            buf = candidate
    if buf:
        chunks.append({"sections": buf, "text": _format_section_chunk(buf)})
    return chunks


def _source_for_topic(topic: str, sections: list[dict]) -> str:
    """Return source/exercise context most relevant to a topic."""
    topic_n = _strip_section_number(topic).lower()
    selected = [
        s for s in sections
        if topic_n and (
            topic_n == (s.get("heading") or "").lower()
            or topic_n in " > ".join(s.get("heading_path") or []).lower()
        )
    ]
    if not selected:
        selected = sections
    return _format_section_chunk(selected)


def _record_key(rec: dict) -> tuple[str, str]:
    return (
        (rec.get("topic") or "").lower().strip(),
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
        "skeleton": {"allow_types": False, "require_culmination": False},
        "canonicalize": {"allow_types": False, "require_culmination": False},
        "description": {"allow_types": False, "require_culmination": False},
        "types": {"allow_types": True, "require_culmination": False},
        "culmination": {"allow_types": True, "require_culmination": True},
        "final": {"allow_types": True, "require_culmination": True},
    }.get(stage, {"allow_types": True, "require_culmination": False})


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


def _repair_records_via_api(
    records: list[dict], *, meta: dict, stage: str, source_context: str = "",
    max_attempts: int = 2,
) -> list[dict]:
    """Validate rows and ask the repair prompt to fix hard failures."""
    records = _ensure_parent_concepts(records)
    opts = _validation_options(stage)
    for attempt in range(max_attempts + 1):
        report = cv.validate_concept_rows(records, **opts)
        hard = [e for e in report["errors"] if e["severity"] == "error"]
        if not hard:
            if report["errors"]:
                progress.log(
                    f"{stage}: validation passed with {len(report['errors'])} warning(s).")
            return records
        if attempt >= max_attempts:
            progress.log(
                f"{stage}: keeping best output with {len(hard)} validation error(s).",
                level="warning",
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
        updated["parent_concept"] = updated.get("parent_concept") or rec.get("parent_concept", "")
        merged.append(updated)

    merged = _repair_records_via_api(
        merged, meta=meta, stage="description", source_context=mmd_text)
    progress.log(f"Descriptions refined: {len(merged)}.", level="success")
    return merged


def _assign_types_via_api(
    records: list[dict], *, subject: str, mmd_text: str = "",
    meta: dict | None = None, sections: list[dict] | None = None,
) -> list[dict]:
    """Dedicated Types-only API pass — mirrors manual types-first workflow."""
    import json as _json

    if not records:
        return records
    if not (mmd_text or "").strip():
        progress.log("Types assignment skipped — no chapter source text.", level="warning")
        return records
    meta = meta or _metadata(subject=subject)
    sections = sections or parse_mmd_sections(mmd_text)
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


def _consolidate_concepts_via_api(
    records: list[dict], *, subject: str, mmd_text: str = "",
    meta: dict | None = None,
) -> list[dict]:
    """Chapter-wide skeleton refinement: dedup, naming, parent grouping."""
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
    if not out:
        raise RuntimeError("concept consolidation returned no rows")
    out = _strip_types_from_records(_ensure_parent_concepts(out))
    out = _repair_records_via_api(out, meta=meta, stage="canonicalize")
    progress.log(f"Rows after canonicalization: {len(out)}.", level="success")
    return out


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
            "concept_details": (row.get("concept_description") or "").strip(),
            "keywords": (row.get("keywords") or "").strip(),
            "source_evidence": (row.get("source_evidence") or "").strip(),
        })
    return out


def _merge_concept_records(records: list[dict]) -> list[dict]:
    """De-duplicate merged concept rows by (topic, normalized title)."""
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for rec in records:
        key = (rec["topic"].lower().strip(),
               bi.normalize_question_text(rec["concept_title"]))
        if key in seen:
            continue
        seen.add(key)
        out.append(rec)
    return out


def _extract_skeleton_via_api(chunks: list[dict], *, meta: dict) -> list[dict]:
    system = prompts.get_text("concepts.skeleton.system")
    all_records: list[dict] = []
    progress.log(
        f"Section-aware skeleton extraction across {len(chunks)} chunk(s).")
    for i, chunk in enumerate(chunks, start=1):
        progress.step(f"Concept skeleton — chunk {i}/{len(chunks)}",
                      value=(i - 1) / max(len(chunks), 1))
        user = (
            _metadata_block(meta)
            + f"\nChunk {i} of {len(chunks)}:\n"
            + chunk["text"]
        )
        data = _openai_json(system, user)
        chunk_records = _strip_types_from_records(_concept_rows_to_records(data))
        chunk_records = [
            r for r in chunk_records
            if not cr.is_culmination(r.get("concept_title", ""))
        ]
        chunk_records = _ensure_parent_concepts(chunk_records)
        progress.log(f"  chunk {i}/{len(chunks)} skeleton rows: {len(chunk_records)}")
        all_records.extend(chunk_records)
    out = _merge_concept_records(all_records)
    progress.log(f"Rows after skeleton merge: {len(out)}.")
    return _repair_records_via_api(out, meta=meta, stage="skeleton")


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


def _build_culminations_via_api(records: list[dict], *, meta: dict) -> list[dict]:
    import json as _json

    if not records:
        return records
    system = prompts.get_text("concepts.culmination.system")
    payload = _json.dumps({"rows": _records_to_api_rows(records)}, ensure_ascii=False)
    user = (
        _metadata_block(meta)
        + "\nFinal normal concept map — add exactly one culmination row per topic:\n"
        + payload
    )
    progress.log("Building topic culmination rows.")
    data = _openai_json(system, user)
    out = _concept_rows_to_records(data)
    if not out:
        out = _ensure_culmination_rows(records)
    else:
        out = _ensure_parent_concepts(out)
        report = cv.validate_concept_rows(
            out, allow_types=True, require_culmination=True)
        if not report["ok"]:
            out = _ensure_culmination_rows(records)
    out = cr.set_culmination_recap(out)
    out = _repair_records_via_api(out, meta=meta, stage="culmination")
    culms = sum(1 for r in out if cr.is_culmination(r.get("concept_title", "")))
    progress.log(f"Culminations added: {culms}.", level="success")
    return out


def concepts_from_mmd(
    mmd_text: str, *, subject: str = "", board: str = "", grade: str = "",
    unit: str = "", chapter_title: str = "", chapter_id: int | str | None = None,
    chapter_code: str = "", learning_kind: str = "Post",
    live: bool | None = None,
) -> list[dict]:
    """Parse an MMD document into concept records (post-learning).

    Large chapters are processed in ordered chunks (never trimmed) and the
    per-chunk concepts are merged, so no chapter content is lost.
    """
    use_live = config.use_live_generation() if live is None else live
    meta = _metadata(
        subject=subject, board=board, grade=grade, unit=unit,
        chapter_title=chapter_title, chapter_id=chapter_id,
        chapter_code=chapter_code, learning_kind=learning_kind,
    )
    if use_live:
        chunks = _section_aware_chunks(mmd_text)
        sections = [s for c in chunks for s in c["sections"]]
        progress.log(
            f"Extracting concepts from {len(mmd_text):,} chars "
            f"across {len(chunks)} section-aware chunk(s) "
            f"(subject: {subject or 'general'}).")
        out = _extract_skeleton_via_api(chunks, meta=meta)
        if not out:
            raise RuntimeError("live concept extraction returned no rows")
        out = _consolidate_concepts_via_api(out, subject=subject, mmd_text=mmd_text, meta=meta)
        out = _refine_descriptions_via_api(
            out, subject=subject, mmd_text=mmd_text, meta=meta, sections=sections)
        out = _assign_types_via_api(
            out, subject=subject, mmd_text=mmd_text, meta=meta, sections=sections)
        out = _build_culminations_via_api(out, meta=meta)
        out = [concept_cleanup.clean_concept_record(dict(r)) for r in out]
        out = cr.refine_chapter(out)
        out = _repair_records_via_api(out, meta=meta, stage="final", source_context=mmd_text)
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
    return out or pre_rows


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
        f"Subject: {subject} | Grade: {grade} | Board: {board}\n\n"
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
        f"Board: {board}\n\nDRAFT:\n" + _json.dumps(draft)[:120_000],
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

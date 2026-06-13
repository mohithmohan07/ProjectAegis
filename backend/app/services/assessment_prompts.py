"""Modular assessment-generation prompt architecture.

The final generation prompt is ASSEMBLED per question batch from blocks:

    base + question-type + difficulty + cognitive-skill + combo guidance
    + subject creativity + assessment purpose + rubric placement + variety

so different difficulty x cognitive-skill combinations receive different
guidance (never one generic prompt). A review prompt validates generated
questions before they are written to the sheet.

Rubric placement follows the REAL assessment workbooks (inspected from the
six production sheets):
  * display_answer    = clean final/model answer (student-facing)
  * answer_content_N  = one rubric/marking point per block
                        ("Student explains that ..." / "1 mark: ...")
  * answer_weightage_N= marks for that rubric point; the SUM equals marks
  * answer_explanation= aligned with / same as the display answer
  * sub_question_N    = descriptive subparts (a), (b), (c) stay IN-SHEET
"""
from __future__ import annotations

from . import katex_rules as kr

# --------------------------------------------------------------------------- #
# 1 · Base block
# --------------------------------------------------------------------------- #

BASE_BLOCK = """\
You are an expert school-assessment author for Indian boards (ICSE/CBSE).
You write exam-grade questions that are concept-aligned, grade-appropriate,
unambiguous, and evaluable by an AI evaluator using mark-wise rubrics.

STANDARD VALUES (use EXACTLY these):
- cognitive_skills: Remember | Understand | Apply | Analyse | Evaluate | Create
- level_of_difficulty: Less | Moderate | High
- answer_type: Phrases | Equation | Image
- question_source: UpSchool DB
- Multi-value fields are comma-separated ONLY (never newline/semicolon/pipe).

UNIVERSAL QUALITY RULES:
- The question must directly test the given concept; never drift off-syllabus.
- The question must match the requested cognitive skill AND difficulty.
- Scope must fit the marks: not too broad, not too narrow.
- Never reveal the answer in the stem. No ambiguity, no trick wording.
- The expected answer must match the question exactly; include all essential
  keywords, formulae, units, steps or examples.
- question_text: plain-text version of the question PLUS any context the AI
  evaluator needs (passage, conversation, data, diagram description). Never
  empty, never HTML.
"""

CONTENT_FORMAT_BLOCK = kr.PROMPT_PREAMBLE

# --------------------------------------------------------------------------- #
# 2 · Question-type blocks
# --------------------------------------------------------------------------- #

TYPE_BLOCKS = {
    "objective": """\
QUESTION TYPE: OBJECTIVE (MCQ / Fill-in-the-blank).
MCQ rules: clear stem; exactly ONE correct option; distractors plausible and
from the same conceptual family (typical student errors make the best
distractors); options similar in length and grammatical fit; no overlapping
or vague options; avoid "all/none of the above"; negative stems only when
necessary and visually flagged ("... is NOT ..."). Correct option weightage =
1 (or the marks), wrong options = 0. answer_explanation explains why the
correct option is right and briefly why key distractors are wrong.
FIB rules: the blank tests a meaningful term/value; the sentence stays
grammatically clear; list accepted alternatives comma-separated only when
several answers are genuinely valid.""",
    "subjective": """\
QUESTION TYPE: SUBJECTIVE (short answer).
Answerable in a few words/sentences; marks align with answer length
(1 mark = one keyword/fact/formula; 2 marks = two points or point +
explanation; 3 marks = three points or concept + explanation + example).
The expected answer is concise but complete; rubric identifies the required
keywords/points; include accepted variations where multiple phrasings are
valid.""",
    "descriptive": """\
QUESTION TYPE: DESCRIPTIVE (long answer).
The task verb must be explicit (explain / justify / derive / compare /
analyse / evaluate / design). Marks must match the required depth. Rubrics
are MARK-WISE and evaluation-ready, never vague. If the question has
subparts (a), (b), (c), keep them inside this SAME question using the
sub-question slots — never as separate questions — with per-subpart marks,
and make the rubric cover every subpart. Overall rubric weightage must equal
the total marks.""",
}

# --------------------------------------------------------------------------- #
# 3 · Difficulty blocks
# --------------------------------------------------------------------------- #

DIFFICULTY_BLOCKS = {
    "Less": """\
DIFFICULTY: LESS — direct recall / basic understanding / single-step use.
Clear, direct, familiar wording; one concept at a time; no traps or
multi-layer interpretation; answerable from standard classroom knowledge.
Rubric style: direct keyword-based marking with simple, clear allocation
(e.g. 1 mark: correct term/fact; for 2 marks: 1 mark identify concept +
1 mark correct explanation/example).""",
    "Moderate": """\
DIFFICULTY: MODERATE — meaningful use of the concept.
Requires understanding, not just memory: explanation, interpretation,
comparison, or a standard application; may link two ideas; fair and solvable
from taught content; not tricky. Ask "why / how / explain / compare /
calculate / interpret".
Rubric style: concept identification + reasoning/explanation + correct
conclusion (e.g. for 3 marks: 1 identify principle, 1 apply/explain,
1 correct conclusion/final answer/example). Method marks where needed.""",
    "High": """\
DIFFICULTY: HIGH — layered thinking, still syllabus-aligned and fair.
Multi-step application, unfamiliar (but fair) contexts, combining linked
concepts, justification/evaluation/inference/error-analysis. Never vague,
never outside the intended scope.
Rubric style: concept recognition + correct reasoning path + intermediate
steps + justification + final conclusion, with partial marks clearly defined
(e.g. for 5 marks: 1 identify principle, 1 correct approach, 1 apply with
correct reasoning, 1 interpret/justify, 1 final conclusion with correct
terminology).""",
}

# --------------------------------------------------------------------------- #
# 4 · Cognitive-skill blocks
# --------------------------------------------------------------------------- #

SKILL_BLOCKS = {
    "Remember": """\
COGNITIVE SKILL: REMEMBER — recall facts, terms, definitions, formulae,
rules, names, steps, symbols, units. Style verbs: identify, state, name,
recall, write the formula, complete, label, choose, match. Vary the stem —
do NOT default to "Define...". No explanation/application beyond the marks.
Rubric: marks for exact/acceptable recall; keywords matter; no lengthy
reasoning required.""",
    "Understand": """\
COGNITIVE SKILL: UNDERSTAND — meaning, explanation, classification,
comparison, interpretation. Style verbs: explain why, describe how, give a
reason, compare, distinguish, classify, interpret, summarize — in the
student's own words, with examples and non-examples where useful.
Rubric: marks for correct meaning + explanation/reason + example/comparison
where required.""",
    "Apply": """\
COGNITIVE SKILL: APPLY — use a concept/formula/rule/method in a familiar or
slightly changed situation. Style verbs: solve, calculate, use the formula,
apply the rule, predict the result, complete the process. The task must
require application, not restating the concept.
Rubric: marks for correct formula/concept selection + substitution/process +
correct answer/conclusion + unit/label where applicable.""",
    "Analyse": """\
COGNITIVE SKILL: ANALYSE — break information into parts, find relationships,
causes/effects, patterns, or errors. Style: identify the error, analyse the
relationship, compare the cases and infer, explain the cause, interpret the
pattern. Must NOT be answerable by simple recall.
Rubric: marks for identifying the relevant parts + explaining the
relationship/cause/pattern + correct inference/conclusion.""",
    "Evaluate": """\
COGNITIVE SKILL: EVALUATE — judge, justify, critique, choose with criteria.
Style: justify, evaluate the claim, decide which is better and why,
support/refute, assess whether. Reasoning must be criteria-based, never
opinion-only.
Rubric: marks for a clear judgment + valid reasoning + correct
concept/evidence + comparison/justification where needed.""",
    "Create": """\
COGNITIVE SKILL: CREATE — construct, design, propose, formulate, develop
something new but syllabus-aligned and rubric-evaluable. Style: design an
experiment, create an example, propose a solution, construct a table or
flowchart, frame a plan.
Rubric: marks for relevance to concept + correctness + completeness +
feasibility/structure; creativity earns nothing if the concept is wrong.""",
}

# --------------------------------------------------------------------------- #
# 5 · Combined difficulty x skill matrix (explicit guidance lines)
# --------------------------------------------------------------------------- #

COMBO_MATRIX = {
    ("Less", "Remember"): "Generate a direct recall question that checks basic "
        "knowledge of the concept. Keep it simple and unambiguous.",
    ("Less", "Understand"): "Generate a simple explanation-based question that "
        "checks whether the student understands the meaning of the concept.",
    ("Less", "Apply"): "Generate a one-step application question using a "
        "familiar classroom situation.",
    ("Moderate", "Remember"): "Generate a recall question that may require "
        "recalling two related facts or selecting the correct fact from a "
        "familiar context.",
    ("Moderate", "Understand"): "Generate an explanation/comparison question "
        "that checks conceptual clarity.",
    ("Moderate", "Apply"): "Generate a standard application question requiring "
        "correct method and answer.",
    ("Moderate", "Analyse"): "Generate a question requiring the student to "
        "identify a relationship, cause, pattern, or error.",
    ("High", "Apply"): "Generate a multi-step or unfamiliar-context application "
        "question that is still syllabus-aligned.",
    ("High", "Analyse"): "Generate a question requiring breakdown of "
        "information, inference, comparison, or error analysis.",
    ("High", "Evaluate"): "Generate a question requiring a justified judgment "
        "based on conceptually valid reasoning.",
    ("High", "Create"): "Generate a question requiring the student to design, "
        "propose, construct, or formulate an answer using the concept.",
}

# Combinations that are usually unnatural for a question type.
UNNATURAL_COMBOS = {
    ("objective", "Create"): "Objective + Create is usually not ideal — "
        "Create-level tasks are better as Descriptive.",
    ("objective", "Evaluate"): "High-level Evaluate tasks are usually better "
        "as Subjective or Descriptive.",
}

# --------------------------------------------------------------------------- #
# 6 · Subject-sensitive creativity blocks
# --------------------------------------------------------------------------- #

SUBJECT_BLOCKS = {
    "Mathematics": "SUBJECT CREATIVITY (Mathematics): varied numerical "
        "contexts; error analysis, pattern recognition, missing step, reverse "
        "calculation, application. No decorative word problems; preserve "
        "mathematical precision.",
    "Science": "SUBJECT CREATIVITY (Science): experiments, observations, "
        "real-life phenomena, diagrams, tables, predictions, cause-effect "
        "reasoning. Never invent scientifically false scenarios.",
    "Physics": "SUBJECT CREATIVITY (Physics): experiments, observations, "
        "real-life phenomena, data tables, predictions, cause-effect "
        "reasoning. Never invent physically false scenarios.",
    "Chemistry": "SUBJECT CREATIVITY (Chemistry): reactions, lab observations, "
        "everyday chemical phenomena, data interpretation. Never invent "
        "chemically false scenarios.",
    "Biology": "SUBJECT CREATIVITY (Biology): observations, processes, "
        "diagrams, real-life biological phenomena, cause-effect chains. Never "
        "invent biologically false scenarios.",
    "Social Science": "SUBJECT CREATIVITY (Social Science): timelines, "
        "cause-effect, source-based questions, map/context interpretation, "
        "comparison of events, policy evaluation. Evidence-based answers only "
        "— no opinion-only questions.",
    "English": "SUBJECT CREATIVITY (English): inference, phrase meaning, tone, "
        "literary devices, character motivation, sequence of events, line "
        "interpretation, creative response where applicable. Rubrics consider "
        "relevance, textual evidence, clarity and expression.",
    "Computer Science": "SUBJECT CREATIVITY (Computer Science): trace the "
        "code, find the error, predict output, complete the logic, compare "
        "algorithms, scenario-based pseudocode. Rubrics reward logic, "
        "syntax/structure and correct reasoning.",
}

# --------------------------------------------------------------------------- #
# 7 · Assessment-purpose blocks (Appears In)
# --------------------------------------------------------------------------- #

PURPOSE_BLOCKS = {
    "Pre-test": "PURPOSE (Pre-test): diagnose prior knowledge and readiness; "
        "include foundational/prerequisite checks and misconception probes; "
        "avoid questions that require the full chapter to have been taught.",
    "Post-test": "PURPOSE (Post-test): check chapter learning; include direct, "
        "application and conceptual questions aligned to taught content.",
    "Worksheet": "PURPOSE (Worksheet): support practice; scaffolding allowed; "
        "varied difficulty; repeated practice with variations is fine.",
    "Test": "PURPOSE (Test): formal assessment; cleaner wording; balanced "
        "difficulty; stronger rubrics; no excessive hints.",
}

# --------------------------------------------------------------------------- #
# 8 · Rubric placement + variety blocks
# --------------------------------------------------------------------------- #

RUBRIC_BLOCK = """\
RUBRIC PLACEMENT (existing supported columns ONLY):
- display_answer: the clean final/model answer (student-facing). Never put
  long rubrics here.
- answer_content blocks: ONE rubric/marking point per block, mark-wise
  ("1 mark: identifies the correct principle." or "Student explains that
  ..."). Never a single vague paragraph; never the model answer alone.
- answer_weightage per block: marks for that point. The SUM of weightages
  MUST equal the question marks — never exceed, never invent extra marks.
- answer_explanation: explains/matches the display answer.
- Evaluation-only rubric content never appears in the student-facing
  question field.
Rubric shape varies by question intent — explanation (concept point /
explanation point / example point), application (correct method /
process / answer+unit), analysis (identify parts / explain relationship /
inference), evaluation (judgment / reasoning / evidence / conclusion),
creation (relevance / correctness / completeness / structure) — but is
always mark-wise and totals the marks exactly.
Grammar/punctuation slips never cost marks unless meaning changes; allow
alternate valid wording where conceptually correct."""

VARIETY_BLOCK = """\
CREATIVITY AND VARIETY (controlled, never at the cost of correctness):
Questions must not sound repetitive, mechanical or template-like. Do NOT
open every question with Define/Explain/What is/State. Rotate meaningful
patterns: direct concept check, misconception check ("A student says ... is
this fully correct?"), real-life application, situation-based, error
analysis ("Identify the error and correct it"), comparison, data/table
interpretation, diagram-based reasoning, cause-effect, justification,
prediction, construct/design. Vary sentence structure and openings within
the batch; contexts must be meaningful, not decorative; creativity must
never make evaluation harder, the question vague, or the content
off-syllabus."""

# --------------------------------------------------------------------------- #
# 9 · Output + review prompts
# --------------------------------------------------------------------------- #

OUTPUT_BLOCK = """\
OUTPUT (STRICT JSON ONLY): {"questions": [{
  "question": "",            // student-facing, rich-text formats allowed
  "question_text": "",       // plain text + any evaluation context; never empty
  "question_category": "",
  "cognitive_skills": "",    // exactly the requested skill
  "level_of_difficulty": "", // exactly the requested difficulty
  "marks": 0,
  "display_answer": "",      // clean model answer
  "answer_explanation": "",
  "answers": [               // objective: options; subj/desc: rubric points
    {"answer_type": "Phrases", "answer_content": "", "correct_answer": "Yes|No",
     "answer_weightage": "1"}
  ],
  "sub_questions": [         // descriptive subparts only, else []
    {"text": "(a) ...", "marks": "2",
     "keywords": [{"answer_type": "Phrases", "weightage": "2", "keyword": ""}]}
  ]
}]}
For subjective rubric points use {"answer_type", "answer", "answer_display",
"weightage", "placeholder"} blocks instead. Weightages always sum to marks."""

REVIEW_PROMPT = """\
You are a strict assessment-quality reviewer. For EACH question, check:
concept match; difficulty match; cognitive-skill match; marks-scope fit;
clear unambiguous language; single defensible answer (MCQ: one correct,
plausible same-family distractors, no length/grammar give-aways); answer
correctness; rubric completeness and mark-wise structure; rubric weightage
sum == marks; question_text populated with all needed context; standard
values only (Remember/Understand/Apply/Analyse/Evaluate/Create;
Less/Moderate/High; Phrases/Equation/Image); no hallucinated facts;
grade-appropriate; fresh non-repetitive framing (no repeated stems across
the batch).
Return ONLY JSON: {"results": [{"index": 0, "pass": true, "problems": [""],
"fixed_question": null}]} — when pass=false and the issue is repairable,
put the corrected full question object in fixed_question (same schema as
generation output); otherwise leave it null."""


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #

def combo_guidance(difficulty: str, skill: str) -> str:
    line = COMBO_MATRIX.get((difficulty, skill))
    if line:
        return f"COMBINED TARGET: {line}"
    return (f"COMBINED TARGET: Generate a {difficulty}-difficulty question "
            f"exercising the {skill} cognitive skill, following both blocks above.")


def combo_warning(question_type: str, skill: str) -> str | None:
    return UNNATURAL_COMBOS.get((question_type, skill))


def build_prompt(
    *, question_type: str, difficulty: str, skill: str,
    subject: str = "", grade: str = "", board: str = "",
    marks: float = 1, category: str = "", purpose: str = "",
) -> str:
    """Assemble the per-batch system prompt from the modular blocks."""
    parts = [
        BASE_BLOCK,
        TYPE_BLOCKS[question_type],
        DIFFICULTY_BLOCKS.get(difficulty, DIFFICULTY_BLOCKS["Moderate"]),
        SKILL_BLOCKS.get(skill, SKILL_BLOCKS["Understand"]),
        combo_guidance(difficulty, skill),
    ]
    warning = combo_warning(question_type, skill)
    if warning:
        parts.append(f"NOTE: {warning} Proceed only because it was explicitly "
                     "requested; keep the task evaluable.")
    subj_block = SUBJECT_BLOCKS.get((subject or "").strip())
    if subj_block:
        parts.append(subj_block)
    for p in [p.strip() for p in (purpose or "").split(",") if p.strip()]:
        block = PURPOSE_BLOCKS.get(p)
        if block:
            parts.append(block)
    parts += [
        RUBRIC_BLOCK,
        VARIETY_BLOCK,
        CONTENT_FORMAT_BLOCK,
        OUTPUT_BLOCK,
        f"RUN CONTEXT: board={board or 'CBSE/ICSE'} | grade={grade or 'school'}"
        f" | subject={subject or 'general'} | question_category={category}"
        f" | marks per question={marks:g}",
    ]
    return "\n\n".join(parts)


# --------------------------------------------------------------------------- #
# Deterministic review + anti-monotony helpers
# --------------------------------------------------------------------------- #

def review_question(rec: dict) -> list[str]:
    """Deterministic checks before a question is accepted (dry AND live)."""
    from .. import bulk_import as bi

    problems: list[str] = []
    if not (rec.get("question") or "").strip():
        problems.append("question empty")
    if not (rec.get("question_text") or "").strip():
        problems.append("question_text empty")
    skill = rec.get("cognitive_skills", "")
    for part in bi.split_multi(skill):
        if part not in bi.COGNITIVE_SKILLS:
            problems.append(f"non-standard cognitive skill {part!r}")
    diff = rec.get("level_of_difficulty", "")
    if diff and diff not in bi.DIFFICULTY_LEVELS:
        problems.append(f"non-standard difficulty {diff!r}")
    marks = float(rec.get("marks") or 0)
    answers = rec.get("answers") or []
    kind = rec.get("sheet_kind", "")
    if kind == "objective":
        correct = [a for a in answers if str(a.get("correct_answer", "")).lower() == "yes"]
        if len(correct) != 1:
            problems.append(f"MCQ must have exactly 1 correct option, got {len(correct)}")
    elif kind in {"subjective", "descriptive"} and marks and answers:
        key = "weightage" if kind == "subjective" else "answer_weightage"
        try:
            total = sum(float(a.get(key) or 0) for a in answers)
            if abs(total - marks) > 0.01:
                problems.append(f"rubric weightage sum {total:g} != marks {marks:g}")
        except (TypeError, ValueError):
            problems.append("non-numeric rubric weightage")
    for a in answers:
        at = a.get("answer_type", "")
        if at and at not in bi.ANSWER_TYPES:
            problems.append(f"non-standard answer_type {at!r}")
    return problems


_GENERIC_OPENERS = {"define", "explain", "what", "state"}


def stem_monotony_report(questions: list[str], *, max_repeat_ratio: float = 0.5) -> dict:
    """Detect repetitive stems across a batch (anti-monotony control)."""
    import re
    openers: dict[str, int] = {}
    for q in questions:
        words = re.findall(r"[A-Za-z']+", q or "")
        if not words:
            continue
        first = words[0].lower()
        openers[first] = openers.get(first, 0) + 1
    total = sum(openers.values()) or 1
    worst, count = max(openers.items(), key=lambda kv: kv[1], default=("", 0))
    generic = sum(n for w, n in openers.items() if w in _GENERIC_OPENERS)
    monotonous = (
        total >= 3 and (count / total > max_repeat_ratio or generic / total > max_repeat_ratio)
    )
    return {
        "openers": openers, "worst": worst, "worst_count": count,
        "generic_ratio": round(generic / total, 2), "monotonous": monotonous,
    }

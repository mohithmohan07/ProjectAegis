"""Modular assessment-generation prompt architecture.

The final generation prompt is ASSEMBLED per question batch from blocks:

    base + question-type + difficulty + cognitive-skill + combo guidance
    + subject creativity + assessment purpose + rubric placement + variety

so different difficulty x cognitive-skill combinations receive different
guidance (never one generic prompt). The live path then applies the shared
record-contract checks before a question is written to the sheet.

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

import math

from . import katex_rules as kr
from . import prompts

# --------------------------------------------------------------------------- #
# 1 · Base block
# --------------------------------------------------------------------------- #

BASE_BLOCK = """\
You are an expert school-assessment author for Indian boards (ICSE/CBSE).
You write exam-grade questions that are concept-aligned, grade-appropriate,
unambiguous, and evaluable by an AI evaluator using mark-wise rubrics.

EVIDENCE AND DECISION BOUNDARY:
- Treat the supplied concept, source material, blueprint cell, board, grade,
  and requested assessment axes as authoritative. Do not import facts,
  assumptions, or a familiar textbook question that the evidence does not
  support.
- Resolve ordinary ambiguity by choosing the least-distorting, evidence-bound
  wording. Never return a placeholder, "needs review", or an explanation in
  place of the requested question object.

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
necessary and visually flagged ("... is NOT ..."). The answers array is the
display order and maps to lowercase paper labels a), b), c), d), e), f) —
never uppercase A), B), C), D). Do not put those labels inside answer_content;
the workbook adds them. Correct option weightage = 1 (or the marks), wrong
options = 0. answer_explanation names the correct option with its lowercase
label and text, then explains why it is right and briefly why key distractors
are wrong.
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
- A 4-mark Descriptive answer has at least TWO rubric blocks; one block with
  weightage 4 is invalid.
- Each typed block uses exactly one whole-cell medium. Equation is full raw
  LaTeX with no [Katex] wrapper (words, when needed, stay inside \\text{...}).
  Phrases is wholly plain text with no TeX or [Katex]. Never mix media.
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
# 9 · Output contract
# --------------------------------------------------------------------------- #

OUTPUT_BLOCK = """\
OUTPUT CONTRACT — return one valid JSON object and no prose or code fence.
Every question object uses these complete top-level fields (empty arrays, not
omitted fields, where a section does not apply). This example shows the
Objective/Descriptive answer-block shape:
{"questions":[{"question":"","question_text":"","question_category":"","cognitive_skills":"","level_of_difficulty":"","marks":1,"display_answer":"","answer_explanation":"","answers":[{"answer_type":"Phrases","answer_content":"","correct_answer":"Yes","answer_weightage":"1"}],"sub_questions":[{"text":"a) ...","marks":"1","keywords":[{"answer_type":"Phrases","weightage":"1","keyword":""}]}]}]}

Field rules:
- question is student-facing rich text; question_text is a complete plain-text
  evaluator copy including all necessary context and is never empty.
- cognitive_skills and level_of_difficulty exactly echo the requested values.
- Objective answers are options in a,b,c,d display order with exactly one
  correct_answer="Yes"; all others are "No". Labels are not part of content.
- Descriptive answers are rubric blocks using answer_content. Subjective
  answers instead use the supported keys answer_type, answer, answer_display,
  weightage, and placeholder.
- sub_questions contains only genuine printed parts and otherwise is []. Use
  lowercase a), b), c), d) labels (or the source's lowercase roman scheme).
- Every answer, rubric, subquestion, and keyword weightage sums exactly to the
  question marks under the applicable contract."""

# --------------------------------------------------------------------------- #
# Registration — every block above becomes an editable prompt in the Admin tab.
# build_prompt() reads each through the registry so edits apply on the next run.
# --------------------------------------------------------------------------- #

_CAT = "Build Assessments · question generation"

prompts.register("assessment.base", label="Base author persona + quality rules",
                 category=_CAT, default=BASE_BLOCK)
for _k, _v in TYPE_BLOCKS.items():
    prompts.register(f"assessment.type.{_k}", label=f"Question type · {_k}",
                     category=_CAT, default=_v)
for _k, _v in DIFFICULTY_BLOCKS.items():
    prompts.register(f"assessment.difficulty.{_k}", label=f"Difficulty · {_k}",
                     category=_CAT, default=_v)
for _k, _v in SKILL_BLOCKS.items():
    prompts.register(f"assessment.skill.{_k}", label=f"Cognitive skill · {_k}",
                     category=_CAT, default=_v)
for _k, _v in SUBJECT_BLOCKS.items():
    prompts.register(f"assessment.subject.{_k}", label=f"Subject creativity · {_k}",
                     category=_CAT, default=_v)
for _k, _v in PURPOSE_BLOCKS.items():
    prompts.register(f"assessment.purpose.{_k}", label=f"Assessment purpose · {_k}",
                     category=_CAT, default=_v)
prompts.register("assessment.rubric", label="Rubric placement rules",
                 category=_CAT, default=RUBRIC_BLOCK)
prompts.register("assessment.variety", label="Creativity & variety rules",
                 category=_CAT, default=VARIETY_BLOCK)
prompts.register("assessment.output", label="Strict JSON output schema",
                 category=_CAT, default=OUTPUT_BLOCK)
prompts.register("assessment.context_footer", category=_CAT,
                 label="Run-context footer",
                 description="Trailing line with board/grade/subject/marks. "
                             "Variables: {{board}} {{grade}} {{subject}} "
                             "{{category}} {{marks}}.",
                 variables=("board", "grade", "subject", "category", "marks"),
                 default="RUN CONTEXT: board={{board}} | grade={{grade}} | "
                         "subject={{subject}} | question_category={{category}} | "
                         "marks per question={{marks}}")


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
    marks: float | None = None, category: str = "", purpose: str = "",
) -> str:
    """Assemble the per-batch system prompt from the modular blocks.

    Every block is read fresh from the prompt registry, so Admin-tab edits take
    effect on the next generation without a restart.
    """
    if question_type not in TYPE_BLOCKS:
        raise ValueError(f"unknown recorded question_type {question_type!r}")
    if difficulty not in DIFFICULTY_BLOCKS:
        raise ValueError(f"unknown recorded difficulty {difficulty!r}")
    if skill not in SKILL_BLOCKS:
        raise ValueError(f"unknown recorded cognitive skill {skill!r}")
    if not str(category or "").strip():
        raise ValueError("question_category must be recorded before prompting")
    if isinstance(marks, bool):
        raise ValueError("marks must be a recorded finite positive number")
    try:
        recorded_marks = float(marks)
    except (TypeError, ValueError):
        raise ValueError(
            "marks must be a recorded finite positive number") from None
    if not math.isfinite(recorded_marks) or recorded_marks <= 0:
        raise ValueError("marks must be a recorded finite positive number")
    diff_key = difficulty
    skill_key = skill
    parts = [
        prompts.get_text("assessment.base"),
        prompts.get_text(f"assessment.type.{question_type}"),
        prompts.get_text(f"assessment.difficulty.{diff_key}"),
        prompts.get_text(f"assessment.skill.{skill_key}"),
        combo_guidance(difficulty, skill),
    ]
    warning = combo_warning(question_type, skill)
    if warning:
        parts.append(f"NOTE: {warning} Proceed only because it was explicitly "
                     "requested; keep the task evaluable.")
    subj = (subject or "").strip()
    if f"assessment.subject.{subj}" in {s.key for s in prompts.specs()}:
        parts.append(prompts.get_text(f"assessment.subject.{subj}"))
    for p in [p.strip() for p in (purpose or "").split(",") if p.strip()]:
        if f"assessment.purpose.{p}" in {s.key for s in prompts.specs()}:
            parts.append(prompts.get_text(f"assessment.purpose.{p}"))
    parts += [
        prompts.get_text("assessment.rubric"),
        prompts.get_text("assessment.variety"),
        prompts.get_text("content.katex_rules"),
        prompts.get_text("assessment.output"),
        prompts.render(
            "assessment.context_footer",
            board=board or "CBSE/ICSE", grade=grade or "school",
            subject=subject or "general", category=category,
            marks=f"{recorded_marks:g}",
        ),
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
    if kind == "descriptive" and marks == 4 and len(answers) < 2:
        problems.append(
            "4-mark descriptive requires at least two rubric blocks"
        )
    for a in answers:
        at = a.get("answer_type", "")
        if at and at not in bi.ANSWER_TYPES:
            problems.append(f"non-standard answer_type {at!r}")
        for issue in kr.answer_cell_issues(
            str(at or ""), str(a.get("answer_content") or "")
        ):
            problems.append(f"answer medium-format {issue}")
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

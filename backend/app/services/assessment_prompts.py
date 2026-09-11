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
                        (an observable credit-bearing criterion)
  * answer_weightage_N= marks for that rubric point; the SUM equals marks
  * answer_explanation= same model answer as display_answer for Descriptive
  * sub_question_N    = descriptive subparts (a), (b), (c) stay IN-SHEET
"""
from __future__ import annotations

import json
import math
from typing import Any, Mapping

from . import column_spec
from . import assessment_output_vocabulary as output_vocabulary
from . import assessment_response_policy as response_policy
from . import katex_rules as kr
from . import prompts

# --------------------------------------------------------------------------- #
# 1 · Base block
# --------------------------------------------------------------------------- #

BASE_BLOCK = """\
You are an expert school-assessment author for the supplied board, grade,
subject, publication and chapter. Never assume a board from an example.
You write exam-grade questions that are concept-aligned, grade-appropriate,
unambiguous, and evaluable by an AI evaluator using mark-wise rubrics.

EVIDENCE AND DECISION BOUNDARY:
- Treat the supplied concept, source material, blueprint cell, board, grade,
  and requested assessment axes as authoritative. Do not import facts,
  assumptions, or a familiar textbook question that the evidence does not
  support.
- Resolve ordinary ambiguity by choosing the least-distorting, evidence-bound
  wording. Never return a placeholder question, "needs review", or an explanation in
  place of the requested question object.

STANDARD VALUES (use EXACTLY these):
- cognitive_skills: one of Remember / Understand / Apply / Analyse / Evaluate / Create
- level_of_difficulty: one of Less / Moderate / High
- answer_type: Objective/Descriptive use Phrases, Equation or Image;
  Subjective textual accepted answers use Words.
- Provenance and identity fields are owned by the caller, not this response.
  Generated question_source is the supplied policy's generated_question_source;
  source-derived question_source is the run publication. A frozen legacy
  policy with no source override retains the run publication. Never invent it.
- Relationship lists use the exact " | " separator (space, pipe, space).
  Concept keyword cells use the separator carried by column_spec_policy
  (comma-space for new runs in every subject);
  this question-author stage does not rewrite concept keywords or rosters.

UNIVERSAL QUALITY RULES:
- The question must directly test the given concept; never drift off-syllabus.
- The question must match the requested cognitive skill AND difficulty.
- Scope must fit the marks: not too broad, not too narrow.
- Never reveal the answer in the stem. No ambiguity, no trick wording.
- The expected answer must match the question exactly; include all essential
  keywords, formulae, units, steps or examples.
- question contains the learner-facing stem or shared instruction only.
  question_text is a complete standalone learner/evaluator rendering with
  the required passage, data or visual and all options or labelled children.
  Use <br> for line breaks and the supplied rich-text/KaTeX rules. Do not
  replace a required visual with an invented description or lose its asset.
- A task's actual response demand determines its mechanics. True/False and
  bounded unoptioned blanks belong to Subjective; explanation and creative
  responses belong to Descriptive. Author a valid task for the recorded lane;
  do not disguise an open task as a one-word answer to fit a lane.
"""

CONTENT_FORMAT_BLOCK = kr.PROMPT_PREAMBLE

# --------------------------------------------------------------------------- #
# 2 · Question-type blocks
# --------------------------------------------------------------------------- #

TYPE_BLOCKS = {
    "objective": """\
QUESTION TYPE: OBJECTIVE (a closed, supplied option set).
MCQ rules: clear stem; exactly ONE correct option; distractors plausible and
from the same conceptual family (typical student errors make the best
distractors); options similar in length and grammatical fit; no overlapping
or vague options; avoid "all/none of the above"; negative stems only when
necessary and visually flagged ("... is NOT ..."). The answers array is the
display order and maps to lowercase paper labels a), b), c), d), e), f) —
never uppercase A), B), C), D). Do not put those labels inside answer_content;
the workbook adds them. question_text includes the complete stem followed by
every labelled option on <br> lines. Correct option weightage = the recorded
item marks; wrong options = 0, so option weights total the item marks exactly.
answer_explanation BEGINS with the exact correct-answer content. When
column_spec_policy.objective_explanation_prefix is option_label_and_answer,
precede it with the corresponding lowercase label (for example b)); otherwise
do not add a label. Then explain the evidence/reasoning and, where useful,
why a plausible distractor fails. Never call an unoptioned blank or True/False
item Objective. A valid Objective answer is Specific; the downstream stage
owns that field.""",
    "subjective": """\
QUESTION TYPE: SUBJECTIVE (bounded placeholder-bound accepted answers).
Use no option list. Put contiguous $$a$$, $$b$$, ... exactly once each in
question and matching visible blanks in question_text. Each answer slot uses
answer_type, answer, answer_display, weightage and placeholder. Textual
accepted values use Words; Equation and Image use their genuine typed media.
answer_display is literal Yes for every used slot; placeholder is the bare
letter without dollar signs. Each slot has bounded accepted values, never
a rubric or an explanatory model answer. Positive numeric slot weights total
the recorded marks. Do not infer marks from response length.
For True/False use one placeholder, accepted answer True or False, full item
weight and answer_display Yes. Begin answer_explanation with the accepted
answer, then the source-grounded reason. An open, explanatory or creative
task requires Descriptive; never manufacture an exact-key answer for it.
Every valid Subjective answer is Specific; the downstream stage owns that
field.""",
    "descriptive": """\
QUESTION TYPE: DESCRIPTIVE (a constructed response scored by criteria).
The task verb must be explicit (explain / justify / derive / compare /
analyse / evaluate / design). Marks must match the required depth. Rubrics
are MARK-WISE and evaluation-ready, never vague. Genuine dependent parts
sharing a passage, scenario or integrated task stay in one question with
structured sub-question slots. Independent exercise items remain separate
questions; printed labels alone do not decide parentage. For true multipart,
question carries shared context/instruction; question_text includes it and
every labelled child in order, with identical wording in sub_questions.
Each child's keyword criteria total that child's marks. Parent marks equal
the sum of child marks. For true multipart return answers=[]: child keywords
are the single internal scoring source. The workbook exporter mechanically
projects their ordered union into the parent rubric cells; those parent and
child workbook views are equivalent and NON-ADDITIVE. Do not return a second
copy of the criteria in this API response. For a single task sub_questions is [].
display_answer and answer_explanation are identical complete learner-facing
model answers, with all requested parts and no rubric instructions.""",
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
        "as Descriptive when they require a reasoned written judgment.",
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
- These criterion rules apply to Descriptive answers/child keywords only.
  Objective answer_content holds option content; Subjective answer holds an
  accepted value, never an evaluator criterion.
- display_answer: the complete clean model answer (student-facing), including
  all parts and reasoning the task asks for. No rubric narration or marks.
- answer_content blocks: ONE observable credit-bearing criterion per block.
  State what evidence earns credit and preserve valid alternative methods or
  wording. Put numeric credit in answer_weightage, not a "1 mark:" label.
- A single-part 4-mark Descriptive answer has at least TWO rubric blocks;
  one block with weightage 4 is invalid. For true multipart keep answers=[]
  and provide complete scoring criteria in each child's keywords instead.
- Each typed block uses exactly one whole-cell medium. Equation is full raw
  LaTeX with no [Katex] wrapper (words, when needed, stay inside \\text{...}).
  Phrases is wholly plain text with no TeX or [Katex]. Never mix media.
- Under column_spec_policy.rubric_half_step, criterion weights are positive
  multiples of 0.5 (including 1.5, 2 and larger); otherwise each is 0.5 or 1.
- Use only column_spec_policy.rubric_tags for textual English Descriptive
  criteria, with the exact bracket tag at the start. Never put criterion tags
  in an answer, model answer, explanation, stem, Equation or Image cell.
  An explicit empty tag list means untagged criteria. A frozen legacy policy
  with no tag key retains English tags content, evidence, reasoning,
  organisation, language, creativity, accuracy; other subjects stay untagged.
- Single-part answer_weightage values are marks for each point and sum to
  item marks. In multipart, each child's keyword weightage values sum to
  that child's marks, and child marks sum to item marks. Never invent credit.
- answer_explanation: exactly the same learner-facing model answer as
  display_answer for Descriptive, including multipart.
- Evaluation-only rubric content never appears in the student-facing
  question field.
Rubric shape varies by question intent — explanation (concept point /
explanation point / example point), application (correct method /
process / answer+unit), analysis (identify parts / explain relationship /
inference), evaluation (judgment / reasoning / evidence / conclusion),
creation (relevance / correctness / completeness / structure) — but is
always mark-wise and totals the marks exactly.
Do not score grammar/punctuation unless the question actually assesses it;
allow alternate valid wording where conceptually correct."""

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
The outer object is {"questions": [...]} with the exact requested count.
Every question uses these complete top-level fields. This is a shape guide,
not a sample question: fill the empty strings, echo the actual recorded marks
and axes, and populate the appropriate answer shape below. Keep unused
sections as empty arrays, not omitted fields.
{"question":"","question_text":"","question_category":"","cognitive_skills":"","level_of_difficulty":"","marks":1,"display_answer":"","answer_explanation":"","answers":[],"sub_questions":[]}

Objective answer shape (one per option, in display order):
{"answer_type":"Phrases","answer_content":"","correct_answer":"Yes","answer_weightage":1}
Subjective answer shape (one per placeholder):
{"answer_type":"Words","answer":"","answer_display":"Yes","weightage":1,"placeholder":"a"}
Single-part Descriptive criterion shape (one per credit-bearing point):
{"answer_type":"Phrases","answer_content":"","answer_weightage":0.5}
Descriptive child shape (true multipart only; its criteria must be complete):
{"text":"a) ...","marks":1,"keywords":[{"answer_type":"Phrases","weightage":0.5,"keyword":""},{"answer_type":"Phrases","weightage":0.5,"keyword":""}]}

Field rules:
- question is the learner-facing stem/shared instruction; question_text is
  the complete rendering including all necessary context and is never empty.
  Use <br> line breaks and the supplied rich-text/KaTeX rules in both.
- marks, answer_weightage, weightage and child marks are JSON numbers, never
  numeric strings. The shown numbers illustrate types, not required marks.
- cognitive_skills and level_of_difficulty exactly echo the requested values.
- Objective answers are options in a,b,c,d display order with exactly one
  correct_answer="Yes"; all others are "No". Labels are not part of content.
- Descriptive answers are rubric blocks using answer_content. Subjective
  answers instead use the supported keys answer_type, answer, answer_display,
  weightage, and placeholder.
- For true multipart Descriptive, answers is [] and sub_questions contains
  all scored children. The parent rubric is a workbook projection and is
  never duplicated in this API response.
- sub_questions contains only genuine dependent parts and otherwise is []. Use
  lowercase a), b), c), d) labels (or the source's lowercase roman scheme).
- Objective option weights and Subjective slot weights each total item marks.
  Single-part Descriptive criterion weights total item marks. For multipart,
  child criterion weights total child marks and child marks total item marks.
  The workbook's derived parent view totals the same item marks; never sum
  the equivalent parent and child rubric projections together.
- Do not emit provenance, IDs, routing, restriction or keyboard fields owned
  by later stages. Do not return alternatives such as Phrases|Equation as a
  literal enum, or copy these illustrative blanks/labels into real content."""

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
    metadata: Mapping[str, Any] | None = None,
) -> str:
    """Assemble the per-batch system prompt from the modular blocks.

    Every block is read fresh from the prompt registry, so Admin-tab edits take
    effect on the next generation without a restart. Optional metadata carries
    a frozen column policy; explicit context arguments take precedence over
    its board/grade/subject values.
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
    policy_metadata = dict(metadata or {})
    subject = str(subject or policy_metadata.get("subject") or "")
    grade = str(grade or policy_metadata.get("grade") or "")
    board = str(board or policy_metadata.get("board") or "")
    policy_metadata.update(subject=subject, grade=grade, board=board)
    policy = column_spec.from_metadata(policy_metadata)
    vocabulary = (
        output_vocabulary.snapshot() if metadata is None
        else policy_metadata.get(output_vocabulary.POLICY_KEY)
    )
    vocabulary_defects = output_vocabulary.field_errors(
        {"question_category": category, "cognitive_skills": skill}, vocabulary,
    )
    if vocabulary_defects:
        raise ValueError("; ".join(vocabulary_defects))
    parts = [
        column_spec.OUTPUT_DISCIPLINE,
        column_spec.ASSESSMENT_QUALITY,
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
        prompts.get_text("content.katex_rules.v2"),
        prompts.get_text("assessment.output"),
        prompts.render(
            "assessment.context_footer",
            board=board or "not supplied", grade=grade or "not supplied",
            subject=subject or "not supplied", category=category,
            marks=f"{recorded_marks:g}",
        ),
        "COLUMN SPECIFICATION POLICY (applies to the explicitly named fields):\n"
        "column_spec_policy = " + json.dumps(policy, ensure_ascii=False, sort_keys=True),
    ]
    vocabulary_instruction = output_vocabulary.instruction(vocabulary)
    if vocabulary_instruction:
        parts.append(vocabulary_instruction)
        parts.append(
            "Echo question_category and cognitive_skills exactly from the "
            "recorded blueprint cell. Do not return an alias or a new label."
        )
    quality_instruction = response_policy.quality_instruction(policy_metadata)
    if quality_instruction:
        parts.append(quality_instruction)
        parts.append(
            "Apply the recorded lane to the complete required response. "
            "For an already supplied source/generated question, preserve its "
            "accepted demand instead of inventing options or placeholders to "
            "fit the type block. Descriptive questions may require a short "
            "calculation, interpretation, drawing or construction; they do "
            "not need an essay verb or an artificially extended answer. "
            "A new question authored for a concept must genuinely test that "
            "concept through the requested response mechanism, without "
            "inflating its scope or prerequisite complexity."
        )
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
    sub_questions = rec.get("sub_questions") or []
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
    if (
        kind == "descriptive" and marks == 4
        and not sub_questions and len(answers) < 2
    ):
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

"""Owner-attached response-mechanism policy for assessment lanes.

This module contains guidance for the model-owned ``sheet_kind`` decision.
It deliberately has no classifier, keyword table, regular expression, or
response-shape fallback: the complete question and its evidence are supplied
to an API author, then checked by an independent advisory critic.

The version is part of each assessment-cell decision policy identity. Bumping
the cell policy versions that carry it causes fresh envelopes to receive a
new verdict while immutable historical decisions remain replayable.
"""
from __future__ import annotations

from typing import Any, Mapping


POLICY_VERSION = "assessment-response-mechanism-1-sop-2026-09-09"

# Authored on a new cell verdict; never infer this from option-marker counts.
# Empty is the value for non-Objective lanes. Missing on historical verdicts
# preserves the legacy single-correct contract downstream.
SELECTION_MODES = ("single", "multiple")


AUTHOR_RULES = """\
RESPONSE-MECHANISM CLASSIFICATION — OWNER-ATTACHED SOP
Policy version: assessment-response-mechanism-1-sop-2026-09-09.
This is a model-owned semantic decision. Read the complete question, its
options, answer evidence, shared context, children, tables, figures and
other response constraints. Do not classify by a command word, nominal
question category, marks, response length, typography, or any other proxy.

Use this ordered distinction:
1. OBJECTIVE: the learner selects the answer from two or more explicitly
   provided, predefined answer choices/options. The answer is selected from
   that supplied set rather than generated independently. This includes MCQ,
   multiple-select, assertion–reason with given options, matching when answer
   combinations/options are provided, choose-the-correct-statement, odd-one-
   out with options, statement-based options, and image-based selection. For
   an Objective verdict, also author selection_mode as "single" when exactly
   one supplied option is correct or "multiple" when the supplied task has
   multiple correct options. This is a model-authored judgment from complete
   source evidence; do not infer it from a count, keyword, mark value, or a
   local default. Preserve every source option and its cardinality.
2. SUBJECTIVE: there are no options and the learner supplies a short,
   fixed-format factual response: a word, phrase, value, symbol, date, name,
   missing term, or simple factual entry rather than an explanation. This
   includes fill-in-the-blank, completing a sentence, one-word/name/term
   answers, and True/False. TRUE/FALSE IS ALWAYS SUBJECTIVE UNDER THIS SOP,
   even though the learner supplies or selects a truth value; it is not
   Objective merely because the possible values are True and False.
3. DESCRIPTIVE: the learner independently constructs more than a short,
   fixed factual entry, such as an explanation, reasoning, calculation or
   working, description, analysis, application, comparison, justification,
   proof/derivation, drawing, diagram, map/locating/marking/labeling response,
   or other extended answer. "Calculate the value of x" is Descriptive when
   calculation or working is required, even when its final answer is numeric.

The response mechanism required for full credit decides the lane. Marks,
verbs, question labels, nominal categories and answer length never decide it.
For a genuine dependent multipart item, inspect each child response demand
independently and preserve every child's response mode in the complete task;
do not let the parent label or one child's format collapse another child's
format. The single cell's lane must faithfully accommodate the integrated
task; name any active-schema incompatibility in rationale rather than
silently dropping, splitting or rewriting a child.
When the evidence is ambiguous, give the best evidence-bound model verdict
and explain the response demand in rationale; do not apply a local default.
"""


CRITIC_RULES = """\
RESPONSE-MECHANISM REVIEW — OWNER-ATTACHED SOP
Policy version: assessment-response-mechanism-1-sop-2026-09-09.
Audit the proposed sheet_kind against the complete question evidence and the
following response mechanism. Objective requires two or more explicit,
predefined answer choices/options from which the learner selects. Subjective
requires a short, fixed factual word/phrase/value/symbol/simple entry without
options; every True/False item remains Subjective even though its truth value
is selected or supplied. Descriptive requires independently constructed
explanation, reasoning, calculation/working, description, analysis,
application, drawing, diagram, mapping or an extended answer; calculating x
remains Descriptive when calculation is required. Do not use marks, command
verbs, nominal category, answer length or keywords as deciding evidence.
For Objective, audit that selection_mode is explicitly authored as "single" or
"multiple", that every supplied option is retained, and that correct-marker
cardinality agrees with that mode. For genuine dependent multipart items,
audit each child response demand
independently and flag any parent-level lane collapse or dropped child mode.
The critic is advisory: record any evidence-bound dissent, but do not revise,
gate, retry or replace the author's verdict.
"""


# Additive guidance for explicitly stamped new work. Keep the earlier SOP
# strings intact: they are also part of historical decision payloads.
QUALITY_AUTHOR_RULES = """\
FULL-CREDIT RESPONSE DEMAND — OWNER GENERATION QUALITY REFINEMENT
Apply this clarification before the older ordered examples. The API decides
the response mechanism from the complete accepted task and its evidence.
First identify everything the LEARNER must submit for full credit; distinguish
that from the concise final answer, an answer key, or an evaluator's optional
explanation. Judge the supplied demand, without adding working, explanation
or complexity that the learner was never asked to provide.

- Descriptive covers independently constructed calculation/working,
  explanation, reasoning, comparison, interpretation, drawing, mapping or
  justification. A calculation or interpretation of a data/frequency table
  remains Descriptive when its final result is one fixed number or word.
  A unique correct answer, an answer line, a box, a blank in a worksheet or
  a short model answer does not turn constructed work into Subjective.
- Objective requires an actual supplied answer-choice set and a full-credit
  response that selects from it. Values in a data table, observations,
  frequencies, interval labels, objects in a diagram, examples, and premises
  are input evidence, not answer choices merely because they form a list.
  A question that only asks the learner to select an option remains Objective
  even if mental calculation is useful for choosing it. If the learner must
  also submit independent working or justification, preserve that constructed
  demand and every dependent child's response in the integrated task.
- Subjective requires only a bounded factual entry, direct identification,
  recall, or direct lookup with no independently constructed solution or
  explanation. A direct reading from a table can qualify; deriving a result
  from its data is a different demand. The SOP's True/False component remains
  Subjective; a separate required explanation retains its constructed demand.

Choose the response mechanism before choosing the closest allowed question
category and marks contract. The category name, nominal marks, Bloom level,
answer restriction Specific/Open, and output sheet layout do not decide the
lane. Never invent alternatives, insert answer placeholders, remove working,
or simplify a source/generated task merely to fit a chosen lane. Preserve
every given, table, visual, option and dependent part needed for the task.
If a recorded lane cannot represent the accepted demand, state that concrete
incompatibility in rationale/review evidence; do not disguise it by changing
the task or silently substitute a different lane downstream.

For a cell verdict, rationale must identify the required learner response,
the source/task evidence supporting it, and why a tempting neighbouring lane
does not fit. Use the actual evidence, not a category-name restatement. These
are API judgments; do not introduce a keyword, length or numeric heuristic.
"""

QUALITY_REVIEW_RULES = """\
Review the complete required response independently of the proposed lane.
Check specifically for constructed work reduced to its short final answer,
table/list inputs treated as answer options, and changed wording, invented
options or blanks used to force a lane. Compare every child and dependency
with the accepted task; an answer key or an evaluator explanation must not
invent an extra learner demand. Name the evidence and any classification
disagreement in issues. This remains an independent advisory review; it does
not replace, gate, retry or rewrite the recorded author verdict.
"""


def quality_instruction(payload: Mapping[str, Any] | None) -> str:
    """Return the stronger response test only for carried new-run policy."""
    from . import generation_quality_policy

    return (
        QUALITY_AUTHOR_RULES
        if generation_quality_policy.is_current(payload) else ""
    )


def quality_review_instruction(payload: Mapping[str, Any] | None) -> str:
    """Give the independent reviewer the same test and review boundary."""
    instruction = quality_instruction(payload)
    return instruction + "\n" + QUALITY_REVIEW_RULES if instruction else ""


__all__ = [
    "POLICY_VERSION", "SELECTION_MODES", "AUTHOR_RULES", "CRITIC_RULES",
    "QUALITY_AUTHOR_RULES", "QUALITY_REVIEW_RULES", "quality_instruction",
    "quality_review_instruction",
]

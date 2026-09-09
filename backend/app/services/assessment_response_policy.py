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


__all__ = [
    "POLICY_VERSION", "SELECTION_MODES", "AUTHOR_RULES", "CRITIC_RULES",
]

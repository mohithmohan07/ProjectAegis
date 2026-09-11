"""Owner-authorized source-format polishing, frozen before Type/Case mining.

The explicit recorded policy selects wording authority; no source phrase,
grade, layout or subject is classified here. Unmarked historical items keep
Q27's raw-source authority and existing decision identities.
"""
from __future__ import annotations

from typing import Any, Mapping

VERSION = "source-task-format-2026-09-09-v1"
FIELD = "source_task_polishing_policy"


def applies(item: Mapping[str, Any] | None) -> bool:
    return isinstance(item, Mapping) and item.get(FIELD) == VERSION


MATERIALIZE_RULES = (
    "For this source-owned item, source_wording_authority identifies the "
    "frozen upstream source-task wording. The owner's 9 September 2026 "
    "task-format amendment narrows Q27: the existing question-polishing "
    "pass may express boxes, tick/mark/select instructions, write-number "
    "spaces and other primary-grade visual response layouts in a complete "
    "Aegis question. This is the SAME SOURCE TASK, never a new question. "
    "Use source_atom.frozen_task_text, accepted before Type/Case clustering; "
    "do not re-polish, simplify, expand or redesign it here. Preserve every "
    "operand, option and its order, part and its order, condition, assessed "
    "skill, response demand and original modality. Keep raw_text and "
    "normalized_source_text as unchanged source evidence. An empty box is "
    "a response space, not permission to guess its answer or invent an "
    "additional blank. A tick must still request the source's selection, "
    "not an added explanation; numbering pictures must keep exactly the "
    "source's ordering/numbering task and all its pictures. Only mechanical "
    "projection into Aegis fields remains: source options into answers[], "
    "response spaces into the proper placeholders, supplied tables into "
    "complete KaTeX arrays or faithful images, and ordered source parts "
    "into their fields. Preserve the supplied stimulus underneath its "
    "owning instruction. Never add questions, examples, values, distractors, "
    "new response demands, hints, solutions or answer leakage to the learner "
    "question. A source task requesting creative writing, drawing, shading, "
    "observation or practical performance remains that task; its answer and "
    "rubric follow the existing assessment rules. Do not invent missing "
    "source observations, measurements or listening scripts to answer it. The Post "
    "Master contains only questions in the uploaded source: curricular "
    "context may support an answer, never invent another assessed task. "
    "If required source evidence is missing, retain the original demand "
    "and record the gap for review; never invent or change a task to hide it.\n"
)

REVIEW_RULES = (
    "This item's recorded source-task-format policy is the owner's "
    "9 September 2026 amendment to Q27. Compare the exported task with "
    "source_atom.frozen_task_text AND the complete raw source evidence. "
    "Upstream polishing was allowed to express source boxes, tick/mark "
    "responses, number-writing spaces and visual response layouts in "
    "Aegis format; that alone is not an invented question. It was frozen "
    "before Type/Case clustering. No semantic wording change is authorized "
    "during materialization or Master refinement. Flag any new question, "
    "invented option/operand/part/condition, altered part or option order, "
    "added explanation demand, lost source demand or stimulus, changed "
    "answer space, response modality or assessed skill, guessed image "
    "content, or answer leakage. Every response space must still refer to "
    "the same supplied object/value and requested action. Preserve complete "
    "question-owned tables, figures and passages in the learner question, "
    "not just in audit or answer fields. Missing material is a recorded "
    "limitation, never permission to invent a substitute task. "
)


# The September 11 contract is additive and explicitly stamped. Do not
# change the v1 rules above: they participate in historical decision keys.
CONTEXT_POLISH_RULES = """\
MINIMUM SUFFICIENT LEARNER CONTEXT — BEFORE WORDING FREEZES
For this recorded policy, resolve each source task's actual dependencies
before freezing its wording. Broad shared_context and source_context are
complete evidence for your judgment, not instructions to copy their entire
contents into the learner question. For an in-text question whose referent
is a named object, event, relationship, example or procedure, modestly
reword the original ask to name that referent and include only the supplied
facts or givens needed to understand and answer it. Preserve every demand,
condition, value, unit, option and ordered part, and the same response mode,
difficulty and assessed skill. Do not create a new question or replace
reasoning with recognition; do not add distractors or supply the answer.

Distinguish context that identifies the task from source material that is
itself assessed. Reading-comprehension, quotation, language-analysis or
comparison questions may require the original passage, stanza, dialogue or
whole work: retain the minimum COMPLETE verbatim stimulus supporting EVERY
asked part, including an entire passage or poem when its whole meaning is
needed. Do not paraphrase away textual evidence, a figure, a data table, a
procedure or an observation that the learner must inspect. A short factual
setup is appropriate only when it preserves the same task and answer space;
it must not explain the inference or copy the textbook's worked solution.

Do not repeat an expository chapter extract to establish a referent that
can be named faithfully in the question. Do not paste question-only setup
or duplicated extracts into Concept Description: the frozen Example owns
its necessary learner context, and the Description teaches its concept.
Place a shared essential stimulus once in the parent before its dependent
parts; place child-specific data with that child. Independent exported
questions must each stand alone even if that requires the same essential
source stimulus. Never remove a genuine dependency merely to avoid repeated
text. Tables retain every header, datum, unit, deliberately empty cell and
row/column relationship in a complete canonical KaTeX array or a complete
supplied image; no abbreviated rows or coordinate-labelled prose.

Choose the context by meaning against the complete evidence, never by a
word limit, excerpt length, keyword, overlap threshold or automatic cutting.
In note, identify the resolved source referent, the essential context kept
and why unused exposition is unnecessary, or name the exact missing
dependency. If essential evidence is unavailable, retain the source ask
and record the gap; never guess context or convert the task to hide it.
Raw source and complete context remain unchanged audit evidence. In each
returned item, add learner_context as a string alongside qid, polished_task
and note. It contains the exact essential stimulus/setup you retained as
a separate context block, already included verbatim within polished_task;
use an empty string when none is needed because the referent and givens
are fully expressed in the ask. polished_task always contains the COMPLETE
standalone learner task, including any learner_context, not a pointer to
that field. Never return the omitted chapter exposition in learner_context.
This explicit accepted context controls learner display; shared_context and
source_context remain evidence only. This permission ends when wording is
frozen before Type/Case clustering; no downstream Master rewrite is allowed.
"""

CONTEXT_REVIEW_RULES = """\
Apply accepted-context authority only to items that actually carry an
accepted learner_context string. A missing or rejected context decision is
not an explicit empty string: retain that item's complete existing source
context fallback and visible review findings. A run-level quality stamp
alone never authorizes suppressing context. In batch or reviewer payloads,
inspect the actual questions/source_atom/source_atoms for these decisions.
Review this item's recorded minimum-sufficient-context decision against
the complete raw evidence. The upstream author was permitted to resolve
an in-text referent and modestly reword the same ask before its wording
froze. That evidence-bound clarification alone is not a fidelity defect.
Check whether every asked demand, given, condition, option, ordered part,
response mode and essential stimulus survives without leaking an answer.
Flag unnecessary expository chapter copying or repeated question-only
setup in Concept Description, and flag missing context or a paraphrased
passage whose exact wording is assessed. Do not demand an arbitrary excerpt
length, remove a whole poem or passage needed by all the parts, or treat a
complete required table as expendable context. Whether evidence is needed
is your semantic judgment, never a length or overlap rule. Audit evidence
is deliberately complete and may be longer than the learner question.
Downstream Masters must preserve frozen_task_text and cannot re-polish it
or reinsert unused chapter exposition from shared_context/source_context.
learner_context is the accepted context. Upstream polishing includes it
within frozen_task_text. An explicit corrected-Concept reviewed_context
decision may instead supply separately accepted context beside the exact
edited question quote: mechanically place that context once with its owning
question when not already present. An empty string is an explicit
no-separate-context decision, never permission to fall back to raw
shared_context. Never render the raw evidence fields as extra context.
If the frozen task omits an essential dependency, name it for review; do
not silently redesign the question. Mechanical placement of its existing
source-owned table, image or ordered parts remains permitted.
"""


def context_polish_rules(payload: Mapping[str, Any] | None) -> str:
    from . import generation_quality_policy as quality

    return "\n" + CONTEXT_POLISH_RULES if quality.active(payload) else ""


def context_applies(item: Mapping[str, Any] | None) -> bool:
    """Only an explicitly accepted context (including empty) owns display."""
    from . import generation_quality_policy as quality

    return quality.active(item) and isinstance(item.get("learner_context"), str)


def context_review_rules(payload: Mapping[str, Any] | None) -> str:
    from . import generation_quality_policy as quality

    # Reviewer payloads carry items under questions/source_atom/context.
    # Instruction activation is separate from per-item display authority.
    return "\n" + CONTEXT_REVIEW_RULES if quality.active(payload) else ""

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

"""Explicit supporting-evidence decisions for corrected Concept workbooks.

Question wording has a verbatim quote contract. Supporting context has a
different contract: the author chooses inheritance, grounded assembly, or an
explicit removal, and the independent critic reviews that semantic choice.
This module checks references and projects the accepted decision; it never
judges relevance by text similarity or manufactures a learner task.
"""
from __future__ import annotations

import copy
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict


class ContextSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["edited_concept", "source_question"]
    concept_row: int
    source_qid: str
    reason: str


class ContextReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["inherit", "replace", "remove"]
    sources: list[ContextSource]
    rationale: str


RemovedDependency = Literal[
    "options", "source_answer", "media", "tables", "content_objects", "subquestions",
]

DEPENDENCY_FIELDS = {
    "options": ("options",),
    "source_answer": ("raw_solution_or_answer", "source_answer", "answer"),
    "media": (
        "assets", "image_urls", "image_assets", "image_manifest", "images",
        "_image_captions", "requires_visual",
    ),
    "tables": ("tables",),
    "content_objects": ("content_objects",),
    "subquestions": ("compound_subparts", "sub_questions"),
}


def validate_context(question: Mapping[str, Any], rows: list[dict],
                     originals: Mapping[str, dict]) -> list[str]:
    """Validate the wire contract and evidence addresses, not their meaning."""
    decision = question["context_review"]
    action = decision["action"]
    defects: list[str] = []
    if not decision["rationale"].strip():
        defects.append("context decision needs an explicit rationale")
    for source in decision["sources"]:
        if source["kind"] == "edited_concept":
            if not 0 <= source["concept_row"] < len(rows) or source["source_qid"]:
                defects.append("context cites an unknown edited Concept row")
        elif source["source_qid"] not in originals or source["concept_row"] != -1:
            defects.append("context cites an unknown source question")
        if not source["reason"].strip():
            defects.append("context evidence needs a reason")
    if action == "inherit" and question["source_qid"] not in originals:
        defects.append("context inheritance needs an existing source question")
    if action == "replace" and (
        not question["shared_context"].strip() or not decision["sources"]
    ):
        defects.append("replacement context needs nonempty text and cited evidence")
    if action == "remove" and question["shared_context"]:
        defects.append("removed context must have an empty replacement")
    removed = question["removed_dependencies"]
    if len(removed) != len(set(removed)):
        defects.append("a supporting dependency was removed more than once")
    if "options" in removed and question["options"]:
        defects.append("options cannot be both replaced and removed")
    if "source_answer" in removed and question["source_answer"]:
        defects.append("the source answer cannot be both replaced and removed")
    return defects


def inherited_context(original: Mapping[str, Any]) -> str:
    # Historical inventories can carry an empty top-level placeholder while
    # the actual stimulus lives in source_context. The author explicitly chose
    # inheritance; a removal is a separate action that clears both projections.
    direct = str(original.get("shared_context") or "")
    if direct:
        return direct
    nested = original.get("source_context")
    return str(nested.get("shared_context") or "") if isinstance(nested, Mapping) else ""


def resolve_contexts(verdict: dict, originals: Mapping[str, dict]) -> dict:
    """Resolve inheritance only after strict schema/reference validation."""
    resolved = copy.deepcopy(verdict)
    for question in resolved["questions"]:
        action = question["context_review"]["action"]
        if action == "inherit":
            question["shared_context"] = inherited_context(originals[question["source_qid"]])
        elif action == "remove":
            question["shared_context"] = ""
    return resolved


def apply_support_decisions(current: dict, row: Mapping[str, Any]) -> None:
    """Apply the reviewed field decisions without clearing unrelated evidence.

    Called after wording/options/answer edits so the nested context exposed
    to Master authoring agrees with the effective direct fields. Unrelated
    source metadata and child-owned evidence remain intact.
    """
    decision = row.get("context_review")
    if not isinstance(decision, Mapping):
        return  # Historical and dedicated-question-sheet contracts are separate.
    current["shared_context"] = str(row.get("shared_context") or "")
    nested = current.get("source_context")
    context = copy.deepcopy(dict(nested)) if isinstance(nested, Mapping) else {}
    # Some older inventories stored untouched answers/options only in the
    # nested context, with empty placeholders at the top. Carry that evidence
    # unless the current review explicitly replaces or removes its field.
    if not current.get("raw_solution_or_answer"):
        if current.get("source_answer"):
            current["raw_solution_or_answer"] = copy.deepcopy(current["source_answer"])
        elif context.get("source_answer"):
            current["raw_solution_or_answer"] = copy.deepcopy(context["source_answer"])
        elif "raw_solution_or_answer" in context:
            current["raw_solution_or_answer"] = copy.deepcopy(context["raw_solution_or_answer"])
    if not current.get("options") and "options" in context:
        current["options"] = copy.deepcopy(context["options"])
    for dependency in row.get("removed_dependencies") or []:
        for field in DEPENDENCY_FIELDS[dependency]:
            current.pop(field, None)
            context.pop(field, None)
    if decision["action"] == "remove":
        current["requires_context"] = False
    elif current["shared_context"]:
        current["requires_context"] = True
    if "source_answer" not in (row.get("removed_dependencies") or []):
        answer = row.get("raw_solution_or_answer")
        if answer:
            current["source_answer"] = copy.deepcopy(answer)
            if "answer" in current:
                current["answer"] = copy.deepcopy(answer)
    context.update({
        "raw_text": str(current.get("raw_task") or ""),
        "normalized_public_text": str(current.get("normalized_public_text") or ""),
        "shared_context": current["shared_context"],
        "source_answer": copy.deepcopy(current.get("raw_solution_or_answer") or ""),
        "options": copy.deepcopy(current.get("options") or []),
    })
    # Mirror known aliases only where they already exist. Do not fabricate
    # alternate schemas or discard source IDs, page refs, visuals or tables.
    for field in ("raw_task", "normalized_task", "polished_task", "frozen_task_text",
                  "question_text", "raw_solution_or_answer", "answer",
                  "requires_context", "requires_visual"):
        if field in context:
            context[field] = copy.deepcopy(current.get(field))
    current["source_context"] = context
    current["reviewed_context"] = copy.deepcopy(dict(decision))

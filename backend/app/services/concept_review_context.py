"""Explicit supporting-evidence decisions for corrected Concept workbooks.

Question wording has a verbatim quote contract. Supporting context has a
different contract: the author chooses inheritance, grounded assembly, or an
explicit removal, and the independent critic reviews that semantic choice.
This module checks references and projects the accepted decision; it never
judges relevance by text similarity or manufactures a learner task.
"""
from __future__ import annotations

import copy
from collections import Counter
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict

from . import generation_quality_policy as quality
from . import generation_repair_policy as repair


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


def source_group_ids(row: Mapping[str, Any]) -> list[str]:
    """Read an explicit accepted grouping; never infer one from the wording."""
    if "source_qids" in row:
        return list(row["source_qids"])
    qid = str(row.get("source_qid") or "")
    return [qid] if qid else []


def _remove_dependency(support: dict, dependency: str) -> None:
    for field in DEPENDENCY_FIELDS[dependency]:
        support.pop(field, None)
    # A previously corrected group can itself be retained or regrouped in a
    # later explicit revision. Apply its new decision to all active aliases;
    # predecessor receipts retain the unmodified source evidence.
    for member in support.get("reviewed_source_dependencies") or []:
        if isinstance(member, dict):
            _remove_dependency(member, dependency)
    if isinstance(support.get("source_context"), dict):
        _remove_dependency(support["source_context"], dependency)


def validate_source_group(question: Mapping[str, Any], originals: Mapping[str, dict]) -> list[str]:
    members = source_group_ids(question)
    primary = question["source_qid"]
    defects: list[str] = []
    if (not primary and members) or (primary and (not members or members[0] != primary)):
        defects.append("source_qids must start with source_qid; added questions have no source group")
    if any(not member or member not in originals for member in members):
        defects.append("source group cites an unknown source question")
    if len(members) != len(set(members)):
        defects.append("source group repeats a source question")
    decisions = question["source_dependency_reviews"]
    if Counter(item["source_qid"] for item in decisions) != Counter(members):
        defects.append("every grouped source needs exactly one dependency decision")
    for decision in decisions:
        if not decision["rationale"].strip():
            defects.append("source dependency decision needs an explicit rationale")
        removed = decision["removed_dependencies"]
        if len(removed) != len(set(removed)):
            defects.append("a grouped source dependency was removed more than once")
    if question["context_review"]["action"] == "inherit" and all(member in originals for member in members):
        contexts = [inherited_context(originals[member]) for member in members]
        if contexts and any(context != contexts[0] for context in contexts[1:]):
            defects.append("grouped inheritance needs identical contexts; explicitly replace or remove instead")
    return defects


def apply_source_group(current: dict, row: Mapping[str, Any], originals: Mapping[str, dict]) -> None:
    """Project the API's source group without manufacturing a combined task.

    Only declared dependencies enter the active task. Full originals are kept
    separately in the review audit, so their raw excerpts cannot become context.
    Answers/options remain source-addressed rather than being combined into an
    invalid whole-question answer or an invented choice set.
    """
    if "source_qids" not in row:
        return
    members = source_group_ids(row)
    decisions = {item["source_qid"]: item for item in row.get("source_dependency_reviews") or []}
    current.update(repair.fields(row))
    current["reviewed_source_qids"] = copy.deepcopy(members)
    current["source_dependency_reviews"] = copy.deepcopy(row.get("source_dependency_reviews") or [])
    if not members:
        return
    dependencies = []
    all_fields = tuple(dict.fromkeys(field for fields in DEPENDENCY_FIELDS.values() for field in fields)) + (
        "reviewed_source_dependencies",
    )
    for member in members:
        original = originals[member]
        nested = original.get("source_context")
        nested_support = {field: copy.deepcopy(nested[field]) for field in all_fields if field in nested} if isinstance(nested, Mapping) else {}
        support = copy.deepcopy(nested_support)
        for field in all_fields:
            if field in original and (field not in support or original[field] not in (None, "", [])):
                support[field] = copy.deepcopy(original[field])
        conflicts = {field: value for field, value in nested_support.items() if support.get(field) != value}
        if conflicts:
            support["source_context"] = conflicts
        removed = decisions.get(member, {}).get("removed_dependencies") or []
        for dependency in removed:
            _remove_dependency(support, dependency)
        dependencies.append({"source_qid": member, **support})
    # The accepted global replacement/removal is applied later by the existing
    # support-decision projection. Single-source reviews retain its old shape.
    if len(members) == 1:
        for dependency in decisions.get(members[0], {}).get("removed_dependencies") or []:
            _remove_dependency(current, dependency)
            if isinstance(current.get("source_context"), dict):
                _remove_dependency(current["source_context"], dependency)
        return
    context = copy.deepcopy(dict(current.get("source_context") or {}))
    for field in all_fields:
        current.pop(field, None)
        context.pop(field, None)
    for dependency in ("media", "tables", "content_objects", "subquestions"):
        for field in DEPENDENCY_FIELDS[dependency]:
            values = [support[field] for support in dependencies if field in support]
            if not values:
                continue
            if all(isinstance(value, list) for value in values):
                combined = []
                for value in values:
                    for item in value:
                        if item not in combined:
                            combined.append(copy.deepcopy(item))
                current[field] = combined
            elif all(isinstance(value, bool) for value in values):
                current[field] = any(values)
            elif all(value == values[0] for value in values):
                current[field] = copy.deepcopy(values[0])
            # Incompatible structured aliases remain source-addressed below;
            # no local conversion guesses their semantics or overwrites one.
    context["reviewed_source_qids"] = copy.deepcopy(members)
    context["reviewed_source_dependencies"] = dependencies
    current["source_context"] = context


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
    # Current question polishing has already resolved broad raw evidence
    # into its accepted learner context. Empty is a recorded decision too.
    if quality.active(original) and "learner_context" in original:
        return str(original["learner_context"] or "")
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
    if quality.active(current) or quality.active(row):
        current.update(quality.fields(current) or quality.fields(row))
        current["learner_context"] = current["shared_context"]
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
        _remove_dependency(current, dependency)
        _remove_dependency(context, dependency)
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
        **({"learner_context": current["learner_context"]}
           if quality.active(current) else {}),
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

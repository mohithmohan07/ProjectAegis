"""Model-owned question identity and boundaries in an edited Concept Excel.

The edited Concept Details are complete evidence, including Types/Cases and
question examples. No parser, positional match or word list decides whether
an example is a retained, edited, added, omitted or moved question.
"""
from __future__ import annotations

import copy
import hashlib
import json
from collections import Counter
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict

POLICY = "concept-question-review-2026-09-09-v1"

AUTHOR = """Read the complete edited Concept workbook rows and the original
accepted question bank. The reviewer edits the SAME Concept Excel, chiefly
its Types/Cases/Examples and any learner tasks in Activity/Info Hub sections.
Return exactly the questions the reviewer retained
or added there, in reviewed order and at their reviewed Concept/Type/Case.
Do not generate a question from Description, mastery prose, a definition or
an example of a misconception. Do not rely on a particular spelling, HTML,
line break, numbering convention or heading layout to discover examples.
A question added by the human is permitted; a question invented by you is not.

For each accepted occurrence, identify its prior source_qid if it is the same
question (including a wording correction or move). Use empty source_qid only
for a genuinely added human question. Numbering, row order, matching words and
similarity alone cannot determine identity. Deleting an early question must
never transfer its identity/answer/visuals to a later or newly added question.
Give every old QID exactly one disposition: retained, edited, moved, or omitted.
A deliberate omission stays omitted. Distinguish a list of independent tasks
under 'Answer the following' from a genuine shared-context multipart task.
Preserve the whole genuine multipart question and all its dependent children.

question_text must be an EXACT contiguous quote from the chosen edited row's
concept_details. Exclude the Example label and supplied worked solution from
that quote; carry separately quoted shared_context and source_answer when
present. Keep every question demand, option, number, table, image URL and
subquestion inside the quote. If question wording spans formatting tokens,
copy those tokens too. Do not polish or solve the question in this call.
The independent Master stages perform their existing faithful formatting and
answer construction against the accepted question later.

Preserve unchanged source options/context/media when the reviewed question
still depends on them: set preserve_source_dependencies=true. Set false only
when the reviewed wording explicitly replaces that evidence; then its complete
replacement must appear in the quoted text/context. An added question has no
old dependencies. New options must be exact quotes in the edited row; do not
invent distractors, tables, givens, figures or solutions. List the selected
options verbatim only when the reviewer changed or added the option set;
otherwise options=[] and preserve_source_dependencies=true keeps the old set.

Choose concept_row from the supplied edited row_index, never from print order
or an old route after the reviewer moved a question. type_id and case_id name
existing IDs from original routes when the same Type/Case remains; leave them
empty for new human-authored Type/Case definitions, and copy their title and
definition from the edited row. placement_section names the actual reviewed
section: types, activity, or info_hub. Decide this from its meaning and placement,
not from its source kind before the reviewer moved it. Explain decisions in rationale. Return one
row disposition for EVERY edited row, including those with no questions.
"""

CRITIC = """Independently audit the complete edited Concept rows, original
bank and proposed review. Check all actual Types/Cases and Activity/Info Hub
learner questions were captured,
no Description/definition/misconception prose became an invented question,
additions/omissions/identity were not inferred by position or numbering, and
reviewed Concept/Type/Case placement is honored. Check all question demands,
options, media, source context and genuine multipart children are preserved.
The reviewer may intentionally add, omit, edit and move questions. Do not
restore omissions or invent questions. Your dissent is advisory: report it
precisely without changing the accepted author verdict or asking for a rerun.
"""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReviewedQuestion(_Strict):
    source_qid: str
    concept_row: int
    question_text: str
    shared_context: str
    source_answer: str
    options: list[str]
    preserve_source_dependencies: bool
    placement_section: Literal["types", "activity", "info_hub"]
    type_id: str
    type_title: str
    type_definition: str
    case_id: str
    case_definition: str
    rationale: str


class OriginalDisposition(_Strict):
    source_qid: str
    disposition: Literal["retained", "edited", "moved", "omitted"]
    rationale: str


class RowDisposition(_Strict):
    concept_row: int
    rationale: str


class ReviewVerdict(_Strict):
    questions: list[ReviewedQuestion]
    original_dispositions: list[OriginalDisposition]
    row_dispositions: list[RowDisposition]


class ReviewCritic(_Strict):
    verdict: Literal["verified", "dissent"]
    issues: list[str]


class ReviewRows(list):
    """List-compatible accepted rows plus the durable model review receipt."""
    def __init__(self, rows=(), *, receipt=None):
        super().__init__(rows)
        self.receipt = dict(receipt or {})


def _call(system: str, payload: dict, *, critic: bool = False) -> dict:
    from . import generation
    from .response_schemas import ResponseSchema
    return generation._openai_json(
        system,
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str),
        response_schema=ResponseSchema(
            "concept_question_review_critic_v1" if critic else "concept_question_review_author_v1",
            ReviewCritic if critic else ReviewVerdict,
        ),
        purpose="advisory_critic" if critic else "source_extraction",
        stage="concept_review.critic" if critic else "concept_review.author",
    )


def _qid(row: Mapping[str, Any]) -> str:
    return str(row.get("qid") or row.get("source_qid") or row.get("question_id") or "").strip()


def _validate(verdict: dict, rows: list[dict], originals: list[dict],
              original_routes: list[dict] | None = None) -> list[str]:
    """Only exact IDs, closed-world accounting and quoted evidence checks."""
    try:
        ReviewVerdict.model_validate(verdict, strict=True)
    except Exception as exc:
        return [f"invalid review schema: {exc}"]
    defects: list[str] = []
    originals_by_id = {_qid(item): item for item in originals}
    dispositions = verdict["original_dispositions"]
    disposition_ids = [item["source_qid"] for item in dispositions]
    if Counter(disposition_ids) != Counter(originals_by_id.keys()):
        defects.append("every original QID must have exactly one disposition")
    row_ids = [item["concept_row"] for item in verdict["row_dispositions"]]
    if Counter(row_ids) != Counter(range(len(rows))):
        defects.append("every edited Concept row must have exactly one disposition")
    accepted_ids: list[str] = []
    route_ids = {
        field: {str(route.get(field) or "") for route in original_routes or []}
        for field in ("type_id", "case_id")
    }
    for question in verdict["questions"]:
        index = question["concept_row"]
        if not 0 <= index < len(rows):
            defects.append(f"unknown edited Concept row {index}")
            continue
        text = str(rows[index].get("concept_details") or "")
        prompt = question["question_text"]
        if not prompt.strip() or prompt not in text:
            defects.append(f"question in row {index} is not an exact nonempty edited-text quote")
        for name in ("shared_context", "source_answer"):
            value = question[name]
            if value and value not in text:
                defects.append(f"{name} in row {index} is not an exact edited-text quote")
        if any(not option or option not in text for option in question["options"]):
            defects.append(f"options in row {index} contain unquoted content")
        for name in ("type_title", "type_definition", "case_definition"):
            value = question[name]
            if value and value not in text:
                defects.append(f"{name} in row {index} is not an exact edited-text quote")
        if original_routes is not None:
            for field in ("type_id", "case_id"):
                if question[field] and question[field] not in route_ids[field]:
                    defects.append(f"unknown original {field} {question[field]}; new definitions require an empty ID")
        source_qid = question["source_qid"]
        if source_qid:
            accepted_ids.append(source_qid)
            if source_qid not in originals_by_id:
                defects.append(f"unknown source question {source_qid}")
        elif question["preserve_source_dependencies"]:
            defects.append("an added question cannot preserve nonexistent source dependencies")
    if len(accepted_ids) != len(set(accepted_ids)):
        defects.append("a source question was accepted more than once")
    kept = {item["source_qid"] for item in dispositions if item["disposition"] != "omitted"}
    if kept != set(accepted_ids):
        defects.append("accepted source questions disagree with their explicit dispositions")
    return defects


def review_canonical_questions(payload: Mapping[str, Any], concept_rows: list[dict]) -> ReviewRows:
    """One complete author verdict and independent advisory review.

    Returns the normalized row format consumed by release_workbook_edits.
    The caller commits ``rows.receipt`` beside the accepted revision. No source
    data or persisted release is mutated here, even if the verdict is invalid.
    """
    from .reviewed_question_set import ReviewedQuestionSetError
    originals = [copy.deepcopy(dict(row)) for row in
                 (payload.get("question_task_inventory") or {}).get("items") or []
                 if isinstance(row, Mapping)]
    ids = [_qid(row) for row in originals]
    if any(not qid for qid in ids) or len(ids) != len(set(ids)):
        raise ReviewedQuestionSetError("original reviewed bank has missing or duplicate question IDs")
    rows = [copy.deepcopy(dict(row)) for row in concept_rows]
    evidence = {
        "policy": POLICY,
        "edited_concepts": [dict(row, row_index=index) for index, row in enumerate(rows)],
        "original_concepts": copy.deepcopy(payload.get("records") or []),
        "original_questions": originals,
        "original_routes": copy.deepcopy(payload.get("type_case_rows") or []),
        "chapter_meta": copy.deepcopy(payload.get("chapter_meta") or {}),
    }
    fingerprint = hashlib.sha256(json.dumps(evidence, sort_keys=True, ensure_ascii=False,
                                             default=str).encode()).hexdigest()
    verdict = _call(AUTHOR, evidence)
    defects = _validate(verdict, rows, originals, evidence["original_routes"])
    if defects:
        # A bounded mechanical correction is not a second semantic opinion.
        verdict = _call(AUTHOR + "\nCorrect only the listed mechanical contract defects.",
                        dict(evidence, previous_verdict=verdict, mechanical_defects=defects))
        defects = _validate(verdict, rows, originals, evidence["original_routes"])
    if defects:
        raise ReviewedQuestionSetError("edited question review cannot be applied: " + "; ".join(defects))
    try:
        critic = _call(CRITIC, dict(evidence, proposed_verdict=verdict), critic=True)
        ReviewCritic.model_validate(critic, strict=True)
    except Exception as exc:
        # The accepted author remains authoritative; an unavailable critic is
        # explicit review evidence, never a reason to drop finished questions.
        critic = {"verdict": "unavailable", "issues": [f"{type(exc).__name__}: {exc}"]}
    receipt = {"policy": POLICY, "input_sha256": fingerprint,
               "author": copy.deepcopy(verdict), "critic": copy.deepcopy(critic)}
    accepted = []
    for question in verdict["questions"]:
        index = question["concept_row"]
        target = rows[index]
        source_qid = question["source_qid"]
        accepted.append({
            "row": f"Concept Details:{target.get('row', index + 1)}",
            "kind": "source" if source_qid else "reviewer_added",
            "question_id": source_qid,
            "source_qid": source_qid,
            "source_label": "",
            "topic_hint": str(target.get("topic") or ""),
            "concept_title": str(target.get("concept_title") or ""),
            "concept_row_index": index,
            "question_text": question["question_text"],
            "raw_task": "",
            "normalized_public_text": question["question_text"],
            "options": copy.deepcopy(question["options"]),
            "raw_solution_or_answer": question["source_answer"],
            "shared_context": question["shared_context"],
            "preserve_source_dependencies": question["preserve_source_dependencies"],
            "placement_section": question["placement_section"],
            "provenance": "source" if source_qid else "reviewer_added",
            **{key: question[key] for key in (
                "type_id", "type_title", "type_definition", "case_id", "case_definition",
            )},
        })
    return ReviewRows(accepted, receipt=receipt)

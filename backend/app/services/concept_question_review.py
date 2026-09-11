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
from contextlib import nullcontext
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict

from .concept_review_context import ContextReview, RemovedDependency
from . import generation_quality_policy as quality
from . import generation_repair_policy as repair

POLICY = "concept-question-review-2026-09-10-v4"
GROUP_POLICY = "concept-question-review-2026-09-11-v5"

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

question_text must be an EXACT quote from the chosen edited row's
concept_details. Normally it is one contiguous quote. The payload also
provides concept_details_view, a display-only representation that changes
only CRLF/line endings and HTML <br> markers; copying that view is allowed
because the server maps it back to the exact raw cell text. The edited cell is
the wording authority: corrections such as replacing a blank glyph with
underscores or removing a duplicated passage are intentional. Never copy the
old original-bank wording for the quote and never Markdown-escape edited
underscores. The original bank supplies identity and untouched dependencies
only. Exclude the
Example label and supplied worked solution from that quote; carry supporting
shared_context and separately quoted source_answer when present. Keep every question
demand, option, number, table, image URL and subquestion inside the quote. If
the reviewed wording is split by an interleaved source label or answer
material that is deliberately excluded, return ordered question_text_spans.
Each span must be copied exactly from concept_details (or its display view),
and the spans must concatenate to question_text; include any needed spaces,
punctuation and formatting in the spans. Return question_text_spans=[] when
one contiguous quote is sufficient. Never invent a separator. If
question wording spans formatting tokens, copy those tokens too. Do not polish
or solve the question in this call.
The independent Master stages perform their existing faithful formatting and
answer construction against the accepted question later.

Supporting context is NOT subject to the question's exact single-cell quote
rule. Choose context_review.action explicitly for each accepted question:
- inherit: the existing source_qid's supporting context still applies unchanged.
  Return shared_context="" and sources=[]; the server carries the original
  context, even if the edited cell does not repeat it. Only existing QIDs may
  inherit. Any redundant retyped context is recorded in the receipt but is not
  used: the inherit action always selects the original context. Valid optional
  sources may document the decision. Do not inherit an attribution, duplicate passage or other material
  that the reviewer deliberately removed or corrected.
- replace: supply only the source-grounded context needed to make this reviewed
  task complete. You may faithfully assemble a passage, word bank, givens or
  image references from the edited rows and original questions. Prefer exact
  reviewed wording where present; minimal connecting prose is allowed. Every
  fact must be supported by the cited evidence. Preserve exact word choices,
  numbers, URLs and KaTeX. Do not add a new task demand, solve the question, or
  restore deliberately removed text. Necessary contextual data may come from
  Description, but that does NOT authorize turning Description into a question.
  Cite each source in context_review.sources: kind=edited_concept uses the
  supplied concept_row (row_index), source_qid=""; kind=source_question uses a
  valid source_qid and concept_row=-1. State why each is needed. Cross-row
  context is allowed; it does not transfer question identity or ownership.
- remove: this question needs no separate context or the reviewer deliberately
  removed it. Return shared_context="". This affects ONLY supporting context.
Give a rationale for every context decision. The server will resolve inherited
context before the independent critic sees the accepted review.

Source options, answers, images, tables and genuine multipart children are
retained independently of the context decision. Return removed_dependencies=[]
normally. Only list a dependency when the reviewer deliberately removed it or
made it inapplicable: options, source_answer, media, tables, content_objects,
subquestions. Explain each removal in rationale. Editing wording, context or
an attribution is not permission to erase images, answer choices or children.
An added question has no inherited dependencies. New options and source_answer
must be exact quotes from the edited row; do not invent distractors, tables,
givens, figures or solutions. Return options=[] and source_answer="" to keep
unchanged source values. Nonempty values replace only their corresponding
field. Do not both replace and remove the same dependency. Preserve all needed
new visuals, tables and subquestions in the reviewed question/context text.
When a task or its options changed, decide whether the old answer and structured
children still apply. Explicitly remove obsolete source_answer or subquestions
when the reviewed text replaces them; do not let stale answer/child projections
override the edited task. Never invent a replacement answer in this review.

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
Audit context_review and removed_dependencies independently of question quotes:
inherited context must still apply, and assembled replacement context must be
fully grounded in its explicitly cited edited rows/source questions. It need
not be one verbatim substring. Check that removed attributions or duplicated
passages are not restored, and that a context correction never erases unrelated
images, options, answers or dependent children. Report unsupported contextual
facts or unjustified dependency removals precisely.
Check that each accepted question quote (and every question_text_spans part)
is grounded in the selected edited row's raw text or its stated display view;
do not approve a paraphrase merely because it is semantically similar.
The reviewer may intentionally add, omit, edit and move questions. Do not
restore omissions or invent questions. Your dissent is advisory: report it
precisely without changing the accepted author verdict or asking for a rerun.
"""

CONTEXT_QUALITY = """\
The recorded generation quality policy distinguishes accepted learner_context
from broad raw shared_context/source_context evidence. For a marked original
question, inherit selects its accepted learner_context, including an explicit
empty value; it must not restore chapter extracts from raw evidence. Resolve
inherit/replace/remove against the actual edited question: an original
context no longer needed after rewording must not survive automatically.
Use replace only for the minimum complete, source-grounded context the
edited task still needs; use remove for no separate context. Preserve
essential passages/poems, all asked givens, complete tables and figures.
The exact edited question quote remains unchanged; separately accepted
context can be projected once beside it by the existing mechanical display.
Do not repeat context already within that quote, restore deleted exposition
or copy question-only setup into Concept Description. The critic applies
these same rules using the complete raw evidence and the resolved context.
"""

GROUP_REPAIR = """\
The reviewer may combine source fragments that belong to ONE dependent multipart
question. For each accepted question return ordered source_qids: every prior
question absorbed by this exact edited question, including its source_qid first.
Keep source_qid as that group's accepted identity. For a single retained source
use [source_qid]; for a genuinely new human question use source_qid="" and [].
Each original QID belongs to at most one accepted question or is explicitly
omitted. An absorbed source is edited, retained or moved, never omitted merely
because its demands now belong to a combined question. Preserve the edited quote
as ONE question with its complete dependent children. Do not concatenate source
questions yourself, split the edited multipart quote, infer groups by numbering,
or merge independent tasks merely because their topic or Type is similar.
The edited group has ONE reviewed Concept/Type/Case placement; old routes remain
provenance and do not force unrelated Types into the new accepted group.

In source_dependency_reviews return one record for every source_qid in source_qids,
including single-source questions: source_qid, removed_dependencies and rationale.
An empty removed_dependencies list explicitly preserves that member's options,
answer evidence, media, complete tables, content objects and dependent children.
Only explicitly remove a member's dependency when the human edit has made it
obsolete; explain why. Secondary members' dependencies are as important as the
primary's. Grouped options and answers remain addressed to their own source,
not an invented overall option set or answer. The original complete sources
remain immutable audit evidence. Do not create new questions from this evidence.

Assess context against the exact revised question, even if the original source
is historical or has a very large excerpt. When the reviewer has reworded an
in-text question to stand alone, choose remove for redundant chapter exposition.
Do not inherit a removed paragraph just because the source QID was retained.
For a merged question, inherit is allowed only when every member's accepted
context is exactly the same; otherwise choose a single minimum sufficient
replace context with cited sources, or remove. Preserve an essential tested
passage, all required givens, tables and figures; length alone is not a reason
to remove them. The critic must check the complete group and these decisions.
"""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReviewedQuestion(_Strict):
    source_qid: str
    concept_row: int
    question_text: str
    # Strict provider schemas require every property, including empty lists.
    # A Python default makes this optional in model_json_schema() and causes
    # the provider to reject the entire request before reading the workbook.
    question_text_spans: list[str]
    shared_context: str
    context_review: ContextReview
    source_answer: str
    options: list[str]
    removed_dependencies: list[RemovedDependency]
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


class SourceDependencyReview(_Strict):
    source_qid: str
    removed_dependencies: list[RemovedDependency]
    rationale: str


class GroupedReviewedQuestion(ReviewedQuestion):
    source_qids: list[str]
    source_dependency_reviews: list[SourceDependencyReview]


class RowDisposition(_Strict):
    concept_row: int
    rationale: str


class ReviewVerdict(_Strict):
    questions: list[ReviewedQuestion]
    original_dispositions: list[OriginalDisposition]
    row_dispositions: list[RowDisposition]


class GroupedReviewVerdict(ReviewVerdict):
    questions: list[GroupedReviewedQuestion]


class ReviewCritic(_Strict):
    verdict: Literal["verified", "dissent"]
    issues: list[str]


class ReviewRows(list):
    """List-compatible accepted rows plus the durable model review receipt."""
    def __init__(self, rows=(), *, receipt=None):
        super().__init__(rows)
        self.receipt = dict(receipt or {})


class QuestionReviewRequestError(RuntimeError):
    """Question review failed before the corrected release could be applied.

    The original request error remains the message/cause for the existing
    redacted run diagnostics; the HTTP boundary returns a readable summary.
    """


def _call(system: str, payload: dict, *, critic: bool = False) -> dict:
    from . import generation, model_provider
    from .response_schemas import ResponseSchema
    try:
        grouped = repair.active(payload)
        # A v5 correction is newly requested work, even when its immutable
        # source upload has a historical routing profile. Bind this revision's
        # mini policy without replacing that source's saved model record.
        binding = model_provider.bind_profile(model_provider.new_profile()) if grouped else nullcontext()
        with binding:
            return generation._openai_json(
                system,
                json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str),
                response_schema=ResponseSchema(
                    ("concept_question_review_critic_v5" if critic else "concept_question_review_author_v5")
                    if grouped else
                    ("concept_question_review_critic_v4" if critic else "concept_question_review_author_v4"),
                    ReviewCritic if critic else (GroupedReviewVerdict if grouped else ReviewVerdict),
                ),
                purpose="advisory_critic" if critic else "source_extraction",
                stage="concept_review.critic" if critic else "concept_review.author",
            )
    except RuntimeError as exc:
        raise QuestionReviewRequestError(str(exc)) from exc


def _qid(row: Mapping[str, Any]) -> str:
    return str(row.get("qid") or row.get("source_qid") or row.get("question_id") or "").strip()


def _validate(verdict: dict, rows: list[dict], originals: list[dict],
              original_routes: list[dict] | None = None, *, grouped: bool = False) -> list[str]:
    """Only exact IDs, closed-world accounting and quoted evidence checks."""
    try:
        (GroupedReviewVerdict if grouped else ReviewVerdict).model_validate(verdict, strict=True)
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
        target = rows[index]
        location = (
            f"{target.get('sheet') or 'Concept workbook'!r} row "
            f"{target.get('row', index + 1)} ({target.get('concept_title') or 'Concept Details'})"
        )
        text = str(rows[index].get("concept_details") or "")
        prompt = question["question_text"]
        spans = question.get("question_text_spans") or []
        span_materialized = None
        if spans:
            from .concept_question_quote import materialize_spans, view
            span_materialized = materialize_spans(text, spans)
            if span_materialized is None or view(span_materialized) != view(prompt):
                defects.append(f"question spans in {location} are not ordered exact edited-text quotes")
        if not prompt.strip() or (
            prompt not in text and span_materialized is None and not spans
        ):
            defects.append(f"question in {location} is not an exact nonempty edited-text quote")
        from .concept_review_context import validate_context
        defects.extend(f"{defect} in {location}" for defect in
                       validate_context(question, rows, originals_by_id))
        for name in ("source_answer",):
            value = question[name]
            if value and value not in text:
                defects.append(f"{name} in {location} is not an exact edited-text quote")
        if any(not option or option not in text for option in question["options"]):
            defects.append(f"options in {location} contain unquoted content")
        for name in ("type_title", "type_definition", "case_definition"):
            value = question[name]
            if value and value not in text:
                defects.append(f"{name} in {location} is not an exact edited-text quote")
        if original_routes is not None:
            for field in ("type_id", "case_id"):
                if question[field] and question[field] not in route_ids[field]:
                    defects.append(f"unknown original {field} {question[field]}; new definitions require an empty ID")
        source_qid = question["source_qid"]
        source_qids = question["source_qids"] if grouped else ([source_qid] if source_qid else [])
        if grouped:
            from .concept_review_context import validate_source_group
            defects.extend(validate_source_group(question, originals_by_id))
        accepted_ids.extend(source_qids)
        for member in source_qids:
            if member not in originals_by_id:
                defects.append(f"unknown source question {member}")
    if len(accepted_ids) != len(set(accepted_ids)):
        defects.append("a source question was accepted more than once")
    kept = {item["source_qid"] for item in dispositions if item["disposition"] != "omitted"}
    if kept != set(accepted_ids):
        defects.append("accepted source questions disagree with their explicit dispositions")
    # A single malformed question can trigger the bounded correction pass;
    # avoid repeating the same contract defect in the user-facing blocker.
    return list(dict.fromkeys(defects))


def _repair_quote_transport(verdict: dict, rows: list[dict]) -> None:
    """Map display-view quotes back to raw source without semantic repair.

    Models often copy a rendered line break for a workbook ``<br>`` token.
    ``locate`` accepts that reversible representation and returns the raw
    source slice.  A split question may instead use explicitly ordered exact
    spans.  Any unlocatable text remains untouched and is rejected by the
    normal validator.
    """

    from .concept_question_quote import locate, materialize_spans
    quote_fields = (
        "question_text", "source_answer", "type_title",
        "type_definition", "case_definition",
    )
    for question in verdict.get("questions") or []:
        if not isinstance(question, dict):
            continue
        index = question.get("concept_row")
        if type(index) is not int or not 0 <= index < len(rows):
            continue
        source = str(rows[index].get("concept_details") or "")
        for name in quote_fields:
            value = question.get(name)
            if isinstance(value, str) and value:
                match = locate(source, value)
                if match is not None:
                    question[name] = match.raw
        spans = question.get("question_text_spans")
        if isinstance(spans, list) and spans:
            materialized = materialize_spans(source, spans)
            prompt = question.get("question_text")
            if materialized is not None and isinstance(prompt, str):
                # The model's visible rendering and source-backed spans must
                # describe the same wording before the raw transport wins.
                from .concept_question_quote import view
                if view(materialized) == view(prompt):
                    question["question_text"] = materialized
        options = question.get("options")
        if isinstance(options, list):
            repaired: list[str] = []
            for option in options:
                if not isinstance(option, str):
                    repaired.append(option)
                    continue
                match = locate(source, option)
                repaired.append(match.raw if match is not None else option)
            question["options"] = repaired


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
        "policy": GROUP_POLICY if repair.active(payload) else POLICY,
        **repair.fields(payload),
        **quality.fields(payload),
        **quality.fields(payload.get("chapter_meta")),
        "edited_concepts": [],
        "original_concepts": copy.deepcopy(payload.get("records") or []),
        "original_questions": originals,
        "original_routes": copy.deepcopy(payload.get("type_case_rows") or []),
        "chapter_meta": copy.deepcopy(payload.get("chapter_meta") or {}),
    }
    from .concept_question_quote import add_view
    evidence["edited_concepts"] = [
        add_view(dict(row, row_index=index)) for index, row in enumerate(rows)
    ]
    fingerprint = hashlib.sha256(json.dumps(evidence, sort_keys=True, ensure_ascii=False,
                                             default=str).encode()).hexdigest()
    attempts = []
    grouped = repair.active(evidence)
    context_instruction = "\n" + CONTEXT_QUALITY if quality.active(evidence) else ""
    if grouped:
        context_instruction += "\n" + GROUP_REPAIR
    author_rules = AUTHOR + context_instruction
    critic_rules = CRITIC + context_instruction
    verdict = _call(author_rules, evidence)
    attempts.append(copy.deepcopy(verdict))
    _repair_quote_transport(verdict, rows)
    defects = _validate(verdict, rows, originals, evidence["original_routes"], grouped=grouped)
    if defects:
        # A bounded mechanical correction is not a second semantic opinion.
        verdict = _call(author_rules + "\nCorrect only the listed mechanical contract defects.",
                        dict(evidence, previous_verdict=verdict, mechanical_defects=defects))
        attempts.append(copy.deepcopy(verdict))
        _repair_quote_transport(verdict, rows)
        defects = _validate(verdict, rows, originals, evidence["original_routes"], grouped=grouped)
    if defects:
        raise ReviewedQuestionSetError("edited question review cannot be applied: " + "; ".join(defects))
    from .concept_review_context import resolve_contexts
    unresolved = copy.deepcopy(verdict)
    verdict = resolve_contexts(verdict, {_qid(item): item for item in originals})
    context_resolutions = [{
        "question_index": index,
        "source_qid": question["source_qid"],
        "action": question["context_review"]["action"],
        "supplied_context": unresolved["questions"][index]["shared_context"],
        "resolved_context": question["shared_context"],
    } for index, question in enumerate(verdict["questions"])]
    try:
        critic = _call(critic_rules, dict(evidence, proposed_verdict=verdict), critic=True)
        ReviewCritic.model_validate(critic, strict=True)
    except Exception as exc:
        # The accepted author remains authoritative; an unavailable critic is
        # explicit review evidence, never a reason to drop finished questions.
        critic = {"verdict": "unavailable", "issues": [f"{type(exc).__name__}: {exc}"]}
    receipt = {"policy": evidence["policy"] + quality.suffix(evidence), "input_sha256": fingerprint,
               **quality.fields(evidence),
               **repair.fields(evidence),
               **({"original_questions": copy.deepcopy(originals)} if grouped else {}),
               # Context references use these frozen edited row indexes;
               # workbook reordering later cannot retarget their evidence.
               "edited_concepts": copy.deepcopy(evidence["edited_concepts"]),
               "author_attempts": attempts,
               "context_resolutions": context_resolutions,
               "author": copy.deepcopy(verdict), "critic": copy.deepcopy(critic)}
    accepted = []
    for question in verdict["questions"]:
        index = question["concept_row"]
        target = rows[index]
        source_qid = question["source_qid"]
        accepted.append({
            **quality.fields(evidence),
            **repair.fields(evidence),
            **({"source_qids": copy.deepcopy(question["source_qids"]),
                "source_dependency_reviews": copy.deepcopy(question["source_dependency_reviews"])}
               if grouped else {}),
            "row": f"Concept Details:{target.get('row', index + 1)}",
            "kind": "source" if source_qid else "reviewer_added",
            "question_id": source_qid,
            "source_qid": source_qid,
            "source_label": "",
            "topic_hint": str(target.get("topic") or ""),
            "concept_title": str(target.get("concept_title") or ""),
            "concept_row_index": index,
            "question_text": question["question_text"],
            "question_text_spans": copy.deepcopy(question["question_text_spans"]),
            "concept_details_sha256": hashlib.sha256(
                str(target.get("concept_details") or "").encode("utf-8")
            ).hexdigest(),
            "raw_task": "",
            "normalized_public_text": question["question_text"],
            "options": copy.deepcopy(question["options"]),
            "raw_solution_or_answer": question["source_answer"],
            "shared_context": question["shared_context"],
            **({"learner_context": question["shared_context"]}
               if quality.active(evidence) else {}),
            "context_review": copy.deepcopy(question["context_review"]),
            "removed_dependencies": copy.deepcopy(question["removed_dependencies"]),
            "preserve_source_dependencies": bool(source_qid),
            "placement_section": question["placement_section"],
            "provenance": "source" if source_qid else "reviewer_added",
            **{key: question[key] for key in (
                "type_id", "type_title", "type_definition", "case_id", "case_definition",
            )},
        })
    return ReviewRows(accepted, receipt=receipt)

"""Recorded marking verdicts for finalized Output-02 candidates.

This pass runs after ``assessment.answer_restriction``.  It receives the
complete materialized candidate, the adopted answer-space contract, and the
one explicit blueprint cell that owns the total marks.  By permanent design,
the API's per-item verdict owns the decomposition of that total.  No external
marking-rubric document is consulted or expected.

The model authors the mark decomposition, duration, and keyboard mode.  Local
code checks only the response contract: exact identity and semantic
preservation, finite positive arithmetic, exact sums, kind-valid keyboard
shape, and complete ordered coverage.  The shared decision kernel supplies
bounded mechanical correction, immutable replay, an advisory critic, and the
same-checker Fixer guarantee.
"""
from __future__ import annotations

import copy
import math
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from .. import bulk_import as bi
from .. import config
from . import assessment_profile
from . import katex_rules
from . import assessment_release as rel
from . import semantic_confidence_policy as confidence_policy
from .phase3 import kernel


# ``-5`` moved stable rules/metadata ahead of the candidate suffix and added
# the explicit GPT-5.6 cache breakpoint.  ``-6`` layers the owner-format
# contract on top so replay cannot collide with either provider input shape.
# ``-7`` adds Subjective answer blocks, the board/profile duration contract,
# and mutually exclusive main-vs-subquestion rubrics.
MARKING_POLICY_VERSION = "assessment-marking-7"
_ANSWER_RESTRICTION_AUDIT_FIELD = "_aegis_assessment_answer_restriction"

_PROMPT_CACHE_STABLE_KEYS = (
    "stage",
    "rules",
    "critic_rules",
    "metadata",
    "assessment_format_policy",
)

MARKING_SYSTEM = (
    "You are the Aegis assessment marking author. Author the marking for ONE "
    "finalized assessment candidate after its Open/Specific answer-space "
    "contract has been adopted. The supplied explicit blueprint cell is the "
    "sole authority for total marks. You intentionally own the per-item "
    "decomposition of that total from the finalized candidate and adopted "
    "answer contract. No external marking-rubric document is part of this "
    "contract, consulted, or expected; do not claim one as evidence.\n"
    "Preserve question and question_text byte-for-byte. Preserve every "
    "semantic answer, option, correct marker, rubric block, subquestion, and "
    "keyword field and preserve their order and cardinality. You may change "
    "only answer_weightage, subquestion marks, and keyword weightage. Author "
    "a finite positive question_duration IN MINUTES — the wire contract's "
    "unit; never seconds — and the response-appropriate "
    "math_keyboard value without a local default: Objective requires the "
    "authored empty string; Subjective and Descriptive require exactly Yes "
    "or No. Obey the supplied assessment_format_policy duration table. For "
    "a per-subpoint rule, author duration_basis_count from the represented "
    "subpoints and make duration its exact prescribed multiple; otherwise "
    "return duration_basis_count as null.\n"
    "For Objective, exactly one correct option receives the cell's total "
    "marks and every wrong option receives exact zero. For Subjective, every "
    "expected-answer weight is positive and their exact sum is the cell "
    "total. For a single-part Descriptive item, every answer/rubric weight is "
    "positive and their exact sum is the cell total. A 4-mark single-part "
    "Descriptive answer has at least two rubric blocks; never assign all four "
    "marks to one block. A multipart Descriptive item keeps answers=[] and "
    "scores only its subquestions/keywords. Preserve each answer/rubric and keyword "
    "cell's declared medium: Equation is full raw LaTeX without [Katex], "
    "while Phrases is wholly plain text without TeX. "
    "When subquestions exist, their positive marks sum exactly to the cell "
    "total; when a subquestion has keyword rows, their positive weightages "
    "sum exactly to that subquestion's marks. Do not use negative values, "
    "cancellation, NaN, infinity, rounding tolerance, or a heuristic/default. "
    "Where the authored question/rubric represents scored working or a "
    "diagram contribution, allocate its step or diagram marks explicitly. "
    "Enumerate every represented subquestion exactly to match the stem, and "
    "award no marks for redundant steps. Allocate marks in fair, logical "
    "increments consistent with the represented scoring evidence; never "
    "use token slivers such as 0.1 merely to force a sum.\n"
    "Return ONLY strict JSON:\n"
    '{"candidate_id":"","question":"","question_text":"",'
    '"answers":[],"sub_questions":[],"question_duration":1,'
    '"duration_basis_count":null,'
    '"math_keyboard":"Yes|No|","rationale":"evidence-bound reason"}'
)

MARKING_CRITIC_SYSTEM = (
    "You are the independent advisory critic for one Aegis assessment "
    "marking verdict. Audit the proposed mark decomposition, duration, and "
    "keyboard mode against the complete finalized candidate, its adopted "
    "Open/Specific answer-space contract, metadata, and the supplied explicit "
    "blueprint cell. Check semantic preservation, scoring coverage, correct "
    "option treatment, exact arithmetic, grade fit, duration, and whether a "
    "math keyboard is actually needed. Verify that represented working and "
    "diagram contributions receive explicit marks, every represented "
    "subquestion matches the stem, and redundant steps receive no marks. "
    "Treat the explicit cell as the total-marks authority and the API's "
    "per-item verdict as the intentional decomposition authority. No external "
    "marking-rubric document is consulted or expected; do not claim one as "
    "evidence. "
    "Do not rewrite, replace, gate, or retry the author's decision. Dissent "
    "ships only as review evidence and the mechanically valid authored "
    "decision stands. State honest confidence.\n"
    "Return ONLY strict JSON:\n"
    '{"verdict":"verified|dissent","confidence":0.0,"issues":[]}'
)

_RESPONSE_FIELDS = frozenset({
    "candidate_id",
    "question",
    "question_text",
    "answers",
    "sub_questions",
    "question_duration",
    "duration_basis_count",
    "math_keyboard",
    "rationale",
})

_PRINT_POSITION_FIELDS = frozenset({
    "bbox",
    "example_number",
    "page",
    "page_hint",
    "position",
    "printer_page",
    "row",
    "row_index",
    "row_number",
    "source_end",
    "source_order",
    "source_page",
    "source_paper_number",
    "source_start",
    "_phase32_segment_order",
    "_phase32_source_order",
})


class MarkingError(ValueError):
    """A marking obligation cannot be bound mechanically."""


def _decimal(value: Any) -> Decimal | None:
    """Return one finite, losslessly workbook-representable Decimal."""

    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.startswith(("+", "-")):
            return None
    elif isinstance(value, (int, float, Decimal)):
        text = str(value)
    else:
        return None
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite():
        return None
    try:
        wire_number = float(number)
    except (OverflowError, ValueError):
        return None
    if not math.isfinite(wire_number):
        return None
    if number != 0 and wire_number == 0:
        return None
    if Decimal(str(wire_number)) != number:
        return None
    return number


def _envelope_hash(value: str) -> str:
    envelope_sha = str(value or "").strip()
    if not envelope_sha:
        raise MarkingError("assessment marking requires an envelope hash")
    return envelope_sha


def _metadata(meta: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(meta, Mapping):
        raise MarkingError("assessment marking metadata must be an object")
    for field in ("subject", "grade"):
        if not str(meta.get(field) or "").strip():
            raise MarkingError(f"assessment marking metadata requires {field}")
    return copy.deepcopy(dict(meta))


def _nonempty_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _content_evidence(value: Any) -> Any:
    """Deep-copy semantic evidence without printer coordinates."""

    if isinstance(value, Mapping):
        return {
            str(key): _content_evidence(raw)
            for key, raw in value.items()
            if str(key) not in _PRINT_POSITION_FIELDS
        }
    if isinstance(value, list):
        return [_content_evidence(raw) for raw in value]
    if isinstance(value, tuple):
        return [_content_evidence(raw) for raw in value]
    return copy.deepcopy(value)


def _validate_answer_space(
    candidate: Mapping[str, Any], candidate_id: str, *, kind: str,
    total_marks: Decimal,
) -> None:
    answers = candidate.get("answers")
    if not isinstance(answers, list):
        raise MarkingError(
            f"marking candidate {candidate_id!r} answers is not an array"
        )
    for position, answer in enumerate(answers, start=1):
        if not isinstance(answer, Mapping):
            raise MarkingError(
                f"marking candidate {candidate_id!r} answer {position} is "
                "not an object"
            )
        if not _nonempty_text(answer.get("answer_content")):
            raise MarkingError(
                f"marking candidate {candidate_id!r} answer/rubric block "
                f"{position} has no content"
            )
        answer_type = answer.get("answer_type")
        if answer_type not in bi.ANSWER_TYPES:
            raise MarkingError(
                f"marking candidate {candidate_id!r} answer/rubric block "
                f"{position} has invalid answer_type {answer_type!r}"
            )
        format_issues = katex_rules.answer_cell_issues(
            str(answer_type or ""), str(answer.get("answer_content") or "")
        )
        if format_issues:
            raise MarkingError(
                f"marking candidate {candidate_id!r} answer/rubric block "
                f"{position} violates its declared medium: "
                + ", ".join(format_issues)
            )
    subquestions = candidate.get("sub_questions")
    if not isinstance(subquestions, list):
        raise MarkingError(
            f"marking candidate {candidate_id!r} sub_questions is not an array"
        )
    for position, subquestion in enumerate(subquestions, start=1):
        if not isinstance(subquestion, Mapping):
            raise MarkingError(
                f"marking candidate {candidate_id!r} subquestion {position} "
                "is not an object"
            )
        if not _nonempty_text(subquestion.get("text")):
            raise MarkingError(
                f"marking candidate {candidate_id!r} subquestion {position} "
                "has no text"
            )
        keywords = subquestion.get("keywords")
        if not isinstance(keywords, list):
            raise MarkingError(
                f"marking candidate {candidate_id!r} subquestion {position} "
                "keywords is not an array"
            )
        for keyword_position, keyword in enumerate(keywords, start=1):
            if not isinstance(keyword, Mapping):
                raise MarkingError(
                    f"marking candidate {candidate_id!r} subquestion "
                    f"{position} keyword {keyword_position} is not an object"
                )
            if not _nonempty_text(keyword.get("keyword")):
                raise MarkingError(
                    f"marking candidate {candidate_id!r} subquestion "
                    f"{position} keyword {keyword_position} has no text"
                )
            keyword_type = keyword.get("answer_type")
            if keyword_type not in bi.ANSWER_TYPES:
                raise MarkingError(
                    f"marking candidate {candidate_id!r} subquestion "
                    f"{position} keyword {keyword_position} has invalid "
                    f"answer_type {keyword_type!r}"
                )
            keyword_issues = katex_rules.answer_cell_issues(
                str(keyword_type or ""), str(keyword.get("keyword") or "")
            )
            if keyword_issues:
                raise MarkingError(
                    f"marking candidate {candidate_id!r} subquestion "
                    f"{position} keyword {keyword_position} violates its "
                    "declared medium: " + ", ".join(keyword_issues)
                )
    if kind in {"objective", "subjective"} and not answers:
        raise MarkingError(
            f"marking candidate {candidate_id!r} requires at least one "
            "answer block"
        )
    if kind == "subjective" and subquestions:
        raise MarkingError(
            f"subjective marking candidate {candidate_id!r} cannot carry "
            "subquestions"
        )
    if kind == "descriptive":
        if not answers and not subquestions:
            raise MarkingError(
                f"marking candidate {candidate_id!r} requires a main rubric "
                "or subquestion rubrics"
            )
        if answers and subquestions:
            raise MarkingError(
                f"multipart marking candidate {candidate_id!r} must not "
                "duplicate scoring in main answer blocks"
            )
        if (
            total_marks == Decimal(4)
            and not subquestions
            and len(answers) < 2
        ):
            raise MarkingError(
                f"marking candidate {candidate_id!r} is a 4-mark "
                "single-part descriptive item and requires at least two "
                "answer/rubric blocks"
            )


def _adopted_contract(
    candidate: Mapping[str, Any], candidate_id: str,
) -> dict[str, Any]:
    restriction = candidate.get("answer_restriction")
    if restriction not in rel.ANSWER_RESTRICTIONS:
        raise MarkingError(
            f"marking candidate {candidate_id!r} has no adopted "
            "Open/Specific verdict"
        )
    if not _nonempty_text(candidate.get("restriction_reason")):
        raise MarkingError(
            f"marking candidate {candidate_id!r} has no restriction_reason"
        )
    audit = candidate.get(_ANSWER_RESTRICTION_AUDIT_FIELD)
    if not isinstance(audit, Mapping):
        raise MarkingError(
            f"marking candidate {candidate_id!r} has no adopted answer-space "
            "contract audit"
        )
    if audit.get("answer_restriction") != restriction or (
        audit.get("restriction_reason") != candidate.get("restriction_reason")
    ):
        raise MarkingError(
            f"marking candidate {candidate_id!r} answer-space audit differs "
            "from its adopted restriction verdict"
        )
    for field in ("answer_space_contract", "evidence", "rationale"):
        if not _nonempty_text(audit.get(field)):
            raise MarkingError(
                f"marking candidate {candidate_id!r} answer-space contract "
                f"has no {field}"
            )
    for field in ("required_elements", "accepted_variations"):
        values = audit.get(field)
        if not isinstance(values, list):
            raise MarkingError(
                f"marking candidate {candidate_id!r} answer-space contract "
                f"{field} is not an array"
            )
    return {
        "answer_restriction": str(restriction),
        "restriction_reason": str(candidate["restriction_reason"]),
        "audit": copy.deepcopy(dict(audit)),
    }


def _prepare_pair(
    pair: Any, position: int,
    profile: Mapping | str | None = None,
) -> tuple[str, dict[str, Any], dict[str, Any], Decimal, dict[str, Any]]:
    if not isinstance(pair, (tuple, list)) or len(pair) != 2:
        raise MarkingError(
            f"assessment marking pair {position} is not a candidate/cell pair"
        )
    candidate, cell = pair
    if not isinstance(candidate, Mapping):
        raise MarkingError(f"marking candidate {position} is not an object")
    if not isinstance(cell, Mapping):
        raise MarkingError(f"marking blueprint cell {position} is not an object")

    candidate_id = candidate.get("candidate_id")
    if not isinstance(candidate_id, str) or not candidate_id.strip():
        raise MarkingError(f"marking candidate {position} has no candidate_id")
    candidate_id = candidate_id.strip()
    cell_id = cell.get("cell_id")
    if not isinstance(cell_id, str) or not cell_id.strip():
        raise MarkingError(
            f"marking blueprint cell for {candidate_id!r} has no cell_id"
        )
    cell_id = cell_id.strip()
    if str(candidate.get("blueprint_cell_id") or "") != cell_id:
        raise MarkingError(
            f"marking candidate {candidate_id!r} is not bound to explicit "
            f"blueprint cell {cell_id!r}"
        )

    kind = cell.get("sheet_kind")
    # The RUN profile decides the allowed kinds (spec-step8 B2).
    if kind not in assessment_profile.sheet_kinds(profile):
        raise MarkingError(
            f"marking blueprint cell {cell_id!r} has invalid sheet_kind "
            f"{kind!r}"
        )
    if candidate.get("sheet_kind") != kind:
        raise MarkingError(
            f"marking candidate {candidate_id!r} sheet_kind differs from "
            f"explicit blueprint cell {cell_id!r}"
        )
    for field in ("question_category", "cognitive_skill", "difficulty"):
        if candidate.get(field) != cell.get(field):
            raise MarkingError(
                f"marking candidate {candidate_id!r} {field} differs from "
                f"explicit blueprint cell {cell_id!r}"
            )

    total_marks = _decimal(cell.get("marks"))
    if (
        total_marks is None
        or total_marks <= 0
        or not math.isfinite(float(total_marks))
    ):
        raise MarkingError(
            f"marking blueprint cell {cell_id!r} marks must be finite and "
            "positive"
        )
    candidate_marks = _decimal(candidate.get("marks"))
    if candidate_marks is None or candidate_marks != total_marks:
        raise MarkingError(
            f"marking candidate {candidate_id!r} marks do not equal explicit "
            f"blueprint cell {cell_id!r}"
        )
    if not _nonempty_text(candidate.get("question")):
        raise MarkingError(f"marking candidate {candidate_id!r} has no question")
    if candidate.get("question_text") != candidate.get("question"):
        raise MarkingError(
            f"marking candidate {candidate_id!r} question_text must equal "
            "question before marking"
        )
    _validate_answer_space(
        candidate, candidate_id, kind=str(kind), total_marks=total_marks,
    )
    if kind == "objective":
        if candidate.get("sub_questions"):
            raise MarkingError(
                f"objective marking candidate {candidate_id!r} cannot carry "
                "subquestions"
            )
        correct_count = sum(
            1
            for answer in candidate.get("answers") or []
            if rel.is_correct_option(answer.get("correct_answer"))
        )
        if correct_count != 1:
            raise MarkingError(
                f"objective marking candidate {candidate_id!r} requires "
                f"exactly one correct option (got {correct_count})"
            )
    elif kind == "subjective" and candidate.get("sub_questions"):
        raise MarkingError(
            f"subjective marking candidate {candidate_id!r} cannot carry "
            "subquestions"
        )
    contract = _adopted_contract(candidate, candidate_id)
    candidate_copy = copy.deepcopy(dict(candidate))
    candidate_copy["candidate_id"] = candidate_id
    cell_copy = copy.deepcopy(dict(cell))
    cell_copy["cell_id"] = cell_id
    return (
        candidate_id,
        candidate_copy,
        cell_copy,
        total_marks,
        contract,
    )


def _semantic_answers(value: Any) -> list[dict[str, Any]] | None:
    if not isinstance(value, list):
        return None
    rows: list[dict[str, Any]] = []
    for answer in value:
        if not isinstance(answer, Mapping):
            return None
        row = copy.deepcopy(dict(answer))
        row.pop("answer_weightage", None)
        rows.append(row)
    return rows


def _semantic_subquestions(value: Any) -> list[dict[str, Any]] | None:
    if not isinstance(value, list):
        return None
    rows: list[dict[str, Any]] = []
    for subquestion in value:
        if not isinstance(subquestion, Mapping):
            return None
        row = copy.deepcopy(dict(subquestion))
        row.pop("marks", None)
        keywords = row.get("keywords")
        if not isinstance(keywords, list):
            return None
        semantic_keywords: list[dict[str, Any]] = []
        for keyword in keywords:
            if not isinstance(keyword, Mapping):
                return None
            semantic_keyword = copy.deepcopy(dict(keyword))
            semantic_keyword.pop("weightage", None)
            semantic_keywords.append(semantic_keyword)
        row["keywords"] = semantic_keywords
        rows.append(row)
    return rows


def _weight_defects(
    response: Mapping[str, Any], *, kind: str, total_marks: Decimal,
    marks_rule: Mapping[str, Any],
) -> list[str]:
    defects: list[str] = []
    answers = response.get("answers")
    if not isinstance(answers, list):
        return ["answers must be an array"]

    answer_weights: list[Decimal] = []
    if kind == "objective":
        correct_positions = [
            position
            for position, answer in enumerate(answers, start=1)
            if isinstance(answer, Mapping)
            and rel.is_correct_option(answer.get("correct_answer"))
        ]
        if len(correct_positions) != 1:
            defects.append(
                f"objective requires exactly one correct option "
                f"(got {len(correct_positions)})"
            )
        for position, answer in enumerate(answers, start=1):
            if not isinstance(answer, Mapping):
                continue
            weight = _decimal(answer.get("answer_weightage"))
            if weight is None:
                defects.append(
                    f"answer {position} weight must be finite and numeric"
                )
                continue
            answer_weights.append(weight)
            if position in correct_positions:
                if weight <= 0 or weight != total_marks:
                    defects.append(
                        f"correct option {position} weight must equal total "
                        f"marks {total_marks}"
                    )
            elif weight != 0:
                defects.append(
                    f"wrong option {position} weight must be exact zero"
                )
        subquestions = response.get("sub_questions")
        if isinstance(subquestions, list) and subquestions:
            defects.append("objective marking must not contain subquestions")
    elif kind == "subjective":
        if not answers:
            defects.append("subjective marking needs at least one answer")
        marks_unit = (
            _decimal(marks_rule.get("marks_per_subpoint"))
            if str(marks_rule.get("mode") or "") == "per_subpoint"
            else None
        )
        for position, answer in enumerate(answers, start=1):
            if not isinstance(answer, Mapping):
                continue
            weight = _decimal(answer.get("answer_weightage"))
            if weight is None or weight <= 0:
                defects.append(
                    f"answer {position} weight must be finite and positive"
                )
                continue
            answer_weights.append(weight)
            if marks_unit is not None and weight != marks_unit:
                defects.append(
                    f"subjective answer {position} weight must equal the "
                    f"active marks-per-subpoint unit {marks_unit:g}"
                )
        subquestions = response.get("sub_questions")
        if isinstance(subquestions, list) and subquestions:
            defects.append("subjective marking must not contain subquestions")
    else:
        subquestions = response.get("sub_questions")
        if not isinstance(subquestions, list):
            subquestions = []
        if answers and subquestions:
            defects.append(
                "multipart descriptive must not duplicate scoring in main "
                "answer blocks"
            )
        if (
            total_marks == Decimal(4)
            and not subquestions
            and len(answers) < 2
        ):
            defects.append(
                "a 4-mark single-part descriptive item requires at least two "
                "answer/rubric blocks"
            )
        for position, answer in enumerate(answers, start=1):
            if not isinstance(answer, Mapping):
                continue
            weight = _decimal(answer.get("answer_weightage"))
            if weight is None or weight <= 0:
                defects.append(
                    f"answer {position} weight must be finite and positive"
                )
                continue
            answer_weights.append(weight)

    score_in_main_answers = kind != "descriptive" or not (
        isinstance(response.get("sub_questions"), list)
        and response.get("sub_questions")
    )
    if (
        score_in_main_answers
        and len(answer_weights) == len(answers)
        and sum(answer_weights, Decimal(0)) != total_marks
    ):
        defects.append(
            f"answer weights must sum exactly to total marks {total_marks}"
        )

    if kind != "descriptive":
        return defects
    subquestions = response.get("sub_questions")
    if not isinstance(subquestions, list):
        return [*defects, "sub_questions must be an array"]
    if not subquestions:
        return defects

    sub_marks: list[Decimal] = []
    for position, subquestion in enumerate(subquestions, start=1):
        if not isinstance(subquestion, Mapping):
            continue
        sub_mark = _decimal(subquestion.get("marks"))
        if sub_mark is None or sub_mark <= 0:
            defects.append(
                f"subquestion {position} marks must be finite and positive"
            )
            continue
        sub_marks.append(sub_mark)
        keywords = subquestion.get("keywords")
        if not isinstance(keywords, list) or not keywords:
            defects.append(
                f"subquestion {position} needs at least one keyword rubric"
            )
            continue
        keyword_weights: list[Decimal] = []
        for keyword_position, keyword in enumerate(keywords, start=1):
            if not isinstance(keyword, Mapping):
                continue
            weight = _decimal(keyword.get("weightage"))
            if weight is None or weight <= 0:
                defects.append(
                    f"subquestion {position} keyword {keyword_position} "
                    "weight must be finite and positive"
                )
                continue
            keyword_weights.append(weight)
        if len(keyword_weights) == len(keywords) and sum(
            keyword_weights, Decimal(0)
        ) != sub_mark:
            defects.append(
                f"subquestion {position} keyword weights must sum exactly "
                "to its marks"
            )
    if len(sub_marks) == len(subquestions) and sum(
        sub_marks, Decimal(0)
    ) != total_marks:
        defects.append(
            f"subquestion marks must sum exactly to total marks {total_marks}"
        )
    return defects


def _format_rule(
    format_policy: Mapping[str, Any], *, kind: str, category: str,
) -> Mapping[str, Any] | None:
    """Return one exact profile-owned format rule, if it is declared."""

    formats = format_policy.get("formats_by_sheet")
    if not isinstance(formats, Mapping):
        return None
    sheet = formats.get(kind)
    if not isinstance(sheet, Mapping):
        return None
    rule = sheet.get(category)
    return rule if isinstance(rule, Mapping) else None


def _validate_cell_format_contract(
    cell: Mapping[str, Any], total_marks: Decimal,
    format_policy: Mapping[str, Any],
) -> None:
    """Fail before provider spend when a cell violates its profile contract."""

    cell_id = str(cell.get("cell_id") or "")
    kind = str(cell.get("sheet_kind") or "")
    category = str(cell.get("question_category") or "")
    rule = _format_rule(format_policy, kind=kind, category=category)
    if rule is None:
        raise MarkingError(
            f"marking blueprint cell {cell_id!r} category {category!r} is "
            f"not permitted for sheet_kind {kind!r} by the active profile"
        )
    marks_rule = rule.get("marks")
    if "marks" not in rule:
        return
    if not isinstance(marks_rule, Mapping):
        raise MarkingError(
            f"marking blueprint cell {cell_id!r} has a non-object marks policy"
        )
    mode = str(marks_rule.get("mode") or "")
    if not mode:
        raise MarkingError(
            f"marking blueprint cell {cell_id!r} has a marks policy without "
            "a mode"
        )
    if mode == "fixed":
        allowed = {
            value
            for raw in marks_rule.get("allowed") or ()
            if (value := _decimal(raw)) is not None and value > 0
        }
        if total_marks not in allowed:
            raise MarkingError(
                f"marking blueprint cell {cell_id!r} marks {total_marks:g} "
                f"are outside the active category contract {tuple(sorted(allowed))}"
            )
    elif mode == "per_subpoint":
        unit = _decimal(marks_rule.get("marks_per_subpoint"))
        raw_max_subpoints = marks_rule.get("max_subpoints")
        max_subpoints = _decimal(raw_max_subpoints)
        if "max_subpoints" in marks_rule and (
            isinstance(raw_max_subpoints, bool)
            or not isinstance(raw_max_subpoints, (int, float, Decimal))
            or max_subpoints is None
            or max_subpoints <= 0
            or max_subpoints != max_subpoints.to_integral_value()
        ):
            raise MarkingError(
                f"marking blueprint cell {cell_id!r} has invalid "
                "max_subpoints; it must be a positive integer"
            )
        if unit is None or unit <= 0 or total_marks % unit != 0:
            raise MarkingError(
                f"marking blueprint cell {cell_id!r} marks {total_marks:g} "
                "are not a positive whole-subpoint multiple"
            )
        else:
            represented = total_marks / unit
            if (
                max_subpoints is not None
                and represented > max_subpoints
            ):
                raise MarkingError(
                    f"marking blueprint cell {cell_id!r} represents "
                    f"{represented:g} subpoints, but the active category "
                    f"wire can represent at most {max_subpoints:g}"
                )
    elif mode:
        raise MarkingError(
            f"marking blueprint cell {cell_id!r} has unknown marks policy "
            f"mode {mode!r}"
        )


def _checker(
    candidate: Mapping[str, Any], *, candidate_id: str, kind: str,
    total_marks: Decimal,
    duration_rule: Mapping[str, Any],
    marks_rule: Mapping[str, Any],
) -> kernel.Checker:
    candidate_evidence = _content_evidence(candidate)
    expected_answers = _semantic_answers(candidate_evidence.get("answers"))
    expected_subquestions = _semantic_subquestions(
        candidate_evidence.get("sub_questions")
    )
    assert expected_answers is not None
    assert expected_subquestions is not None

    def check(response: Mapping[str, Any]) -> list[str]:
        if not isinstance(response, Mapping):
            return ["response is not an object"]
        defects: list[str] = []
        unexpected = sorted(str(field) for field in set(response) - _RESPONSE_FIELDS)
        if unexpected:
            defects.append(f"response has unexpected fields {unexpected!r}")
        if response.get("candidate_id") != candidate_id:
            defects.append(f"candidate_id must echo {candidate_id!r}")
        for field in ("question", "question_text"):
            if response.get(field) != candidate.get(field):
                defects.append(f"{field} must remain exactly unchanged")

        response_answers = _semantic_answers(response.get("answers"))
        if response_answers is None:
            defects.append("answers must be an array of objects")
        elif len(response_answers) != len(expected_answers):
            defects.append("answer cardinality must remain exactly unchanged")
        elif response_answers != expected_answers:
            defects.append(
                "every non-weight answer/option/rubric field must remain "
                "exactly unchanged"
            )

        response_subquestions = _semantic_subquestions(
            response.get("sub_questions")
        )
        if response_subquestions is None:
            defects.append(
                "sub_questions and keyword rows must be arrays of objects"
            )
        elif len(response_subquestions) != len(expected_subquestions):
            defects.append(
                "subquestion cardinality must remain exactly unchanged"
            )
        elif response_subquestions != expected_subquestions:
            defects.append(
                "every non-mark subquestion and non-weight keyword field "
                "must remain exactly unchanged"
            )

        duration = _decimal(response.get("question_duration"))
        if duration is None or duration <= 0:
            defects.append(
                "question_duration must be finite, numeric, and positive"
            )
        elif not math.isfinite(float(duration)) or float(duration) <= 0:
            defects.append("question_duration must fit a finite workbook number")

        basis_count = response.get("duration_basis_count")
        basis: Decimal | None = None
        if basis_count is not None:
            basis = _decimal(basis_count)
            if basis is None or basis <= 0 or basis != basis.to_integral_value():
                defects.append(
                    "duration_basis_count must be null or a positive integer"
                )

        duration_mode = str(duration_rule.get("mode") or "")
        expected_duration: Decimal | None = None
        if duration_mode == "matrix":
            if basis_count is not None:
                defects.append(
                    "duration_basis_count must be null for a matrix duration"
                )
            minutes = duration_rule.get("minutes_by_difficulty")
            if isinstance(minutes, Mapping):
                expected_duration = _decimal(
                    minutes.get(str(candidate.get("difficulty") or ""))
                )
            if expected_duration is None or expected_duration <= 0:
                defects.append(
                    "active duration matrix has no positive value for the "
                    "candidate difficulty"
                )
        elif duration_mode == "per_subpoint":
            per_subpoint = _decimal(
                duration_rule.get("minutes_per_subpoint")
            )
            if basis is None or basis <= 0 or basis != basis.to_integral_value():
                defects.append(
                    "per-subpoint duration requires duration_basis_count"
                )
            elif per_subpoint is None or per_subpoint <= 0:
                defects.append(
                    "active per-subpoint duration has no positive minute unit"
                )
            else:
                expected_duration = basis * per_subpoint
            marks_unit = _decimal(marks_rule.get("marks_per_subpoint"))
            expected_basis = (
                total_marks / marks_unit
                if marks_unit is not None
                and marks_unit > 0
                and total_marks % marks_unit == 0
                else None
            )
            if (
                basis is not None
                and expected_basis is not None
                and basis != expected_basis
            ):
                defects.append(
                    "duration_basis_count must equal the represented "
                    f"whole-subpoint count ({expected_basis:g})"
                )
            if (
                kind == "subjective"
                and expected_basis is not None
                and len(expected_answers) != int(expected_basis)
            ):
                defects.append(
                    "subjective expected-answer count must equal the "
                    f"represented whole-subpoint count ({expected_basis:g})"
                )
        elif duration_mode:
            defects.append(
                f"active duration contract has unknown mode {duration_mode!r}"
            )
        elif basis_count is not None:
            defects.append(
                "duration_basis_count must be null when no per-subpoint "
                "duration contract applies"
            )
        if (
            duration is not None
            and duration > 0
            and expected_duration is not None
            and duration != expected_duration
        ):
            defects.append(
                "question_duration must equal the active profile contract "
                f"({expected_duration:g} minutes)"
            )

        keyboard = response.get("math_keyboard")
        if not isinstance(keyboard, str):
            defects.append("math_keyboard must be a string")
        elif kind == "objective" and keyboard != "":
            defects.append("objective math_keyboard must be exactly blank")
        elif kind in {"subjective", "descriptive"} and keyboard not in {
            "Yes", "No",
        }:
            defects.append(
                f"{kind} math_keyboard must be exactly Yes or No"
            )
        if not _nonempty_text(response.get("rationale")):
            defects.append("rationale must be a non-empty string")

        defects.extend(
            _weight_defects(
                response,
                kind=kind,
                total_marks=total_marks,
                marks_rule=marks_rule,
            )
        )
        return defects

    return check


def _live_author(payload: dict[str, Any]) -> dict[str, Any]:
    from . import generation

    prefix, suffix = generation._json_prompt_cache_parts(
        payload,
        stable_keys=_PROMPT_CACHE_STABLE_KEYS,
    )
    candidate = payload.get("candidate")
    candidate_id = (
        str(candidate.get("candidate_id") or "")
        if isinstance(candidate, Mapping) else ""
    )
    return generation._openai_json(
        MARKING_SYSTEM,
        suffix,
        purpose="concept_mapping",
        prompt_cache_prefix=prefix,
        prompt_cache_key=generation._prompt_cache_key(
            "marking-author-v5",
            prefix,
            shard_seed=candidate_id,
        ),
    )


def _live_critic(payload: dict[str, Any]) -> dict[str, Any]:
    from . import generation

    prefix, suffix = generation._json_prompt_cache_parts(
        payload,
        stable_keys=_PROMPT_CACHE_STABLE_KEYS,
    )
    candidate = payload.get("candidate")
    candidate_id = (
        str(candidate.get("candidate_id") or "")
        if isinstance(candidate, Mapping) else ""
    )
    return generation._openai_json(
        MARKING_CRITIC_SYSTEM,
        suffix,
        purpose="concept_validation",
        prompt_cache_prefix=prefix,
        prompt_cache_key=generation._prompt_cache_key(
            "marking-critic-v5",
            prefix,
            shard_seed=candidate_id,
        ),
    )


def _live_authorities(
    provider: kernel.Provider | None,
    critic: kernel.Critic | None,
    fixer: kernel.Provider | None,
) -> tuple[kernel.Provider, kernel.Critic | None, kernel.Provider | None]:
    if provider is not None:
        return provider, critic, fixer
    from .phase3 import envelope as envelope_mod
    from .phase3 import fixer as fixer_mod

    envelope_mod.require_live_api()
    return _live_author, critic or _live_critic, fixer or fixer_mod.live_fixer


def _kernel_provider(provider: kernel.Provider) -> kernel.Provider:
    """Keep malformed author JSON inside the same-checker Fixer lane."""

    def call(request: dict[str, Any]) -> Mapping[str, Any]:
        response = provider(request)
        if isinstance(response, Mapping):
            return response
        return {
            "_aegis_invalid_response_type": type(response).__name__,
            "_aegis_raw_response": copy.deepcopy(response),
        }

    return call


def _kernel_critic(critic: kernel.Critic | None) -> kernel.Critic | None:
    """Make malformed advisory JSON visible without letting it gate output."""

    if critic is None:
        return None

    def call(request: dict[str, Any]) -> Mapping[str, Any]:
        review = critic(request)
        defects: list[str] = []
        if not isinstance(review, Mapping):
            defects.append(
                f"critic response is not an object ({type(review).__name__})"
            )
            copied: dict[str, Any] = {}
        else:
            copied = copy.deepcopy(dict(review))
            unexpected = sorted(
                str(field)
                for field in set(copied) - {"verdict", "confidence", "issues"}
            )
            if unexpected:
                defects.append(
                    f"critic response has unexpected fields {unexpected!r}"
                )
            if copied.get("verdict") not in {"verified", "dissent"}:
                defects.append("critic verdict must be verified or dissent")
            raw_confidence = copied.get("confidence")
            if isinstance(raw_confidence, bool):
                confidence = float("nan")
            else:
                try:
                    confidence = float(raw_confidence)
                except (TypeError, ValueError):
                    confidence = float("nan")
            if not math.isfinite(confidence) or not 0 <= confidence <= 1:
                defects.append("critic confidence must be finite in [0, 1]")
            if not isinstance(copied.get("issues"), list):
                defects.append("critic issues must be an array")
        if defects:
            return {
                "verdict": "malformed",
                "confidence": 0.0,
                "issues": defects,
            }
        if confidence_policy.semantic_band(confidence) == "rejected":
            copied["issues"] = [
                *copied["issues"],
                f"critic confidence {confidence:.3f} is below the semantic "
                "acceptance floor; decision stands flagged",
            ]
        return copied

    return call


def _review_flags(decision: Mapping[str, Any]) -> list[str]:
    return [
        str(flag)
        for flag in decision.get("review_flags") or []
        if str(flag).strip()
    ]


def _authority(decision: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "decision_key": str(decision.get("key") or ""),
        "policy_version": str(decision.get("policy_version") or ""),
        "review_flags": _review_flags(decision),
        "fixer": bool(decision.get("fixer")),
    }


def _blueprint_authority(cell: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "source": "explicit_blueprint_cell",
        "cell_id": str(cell.get("cell_id") or ""),
        "total_marks": float(_decimal(cell.get("marks")) or Decimal(0)),
        "total_marks_authority": "explicit_blueprint_cell",
        "decomposition_authority": "api_per_item_verdict",
        "answer_space_authority": "adopted_answer_space_contract",
        "external_marking_rubric": "not_part_of_contract",
        "external_marking_rubric_consulted": False,
        "authority_note": (
            "The explicit blueprint cell owns total marks and the API's "
            "per-item verdict intentionally owns their decomposition. No "
            "external marking-rubric document is consulted or expected."
        ),
    }


def _payload(
    candidate: Mapping[str, Any],
    cell: Mapping[str, Any],
    contract: Mapping[str, Any],
    *,
    meta: Mapping[str, Any],
    format_policy: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "stage": "assessment.marking",
        "rules": MARKING_SYSTEM,
        "critic_rules": MARKING_CRITIC_SYSTEM,
        "metadata": _content_evidence(meta),
        "assessment_format_policy": _content_evidence(format_policy),
        "candidate": _content_evidence(candidate),
        "adopted_answer_contract": _content_evidence(contract),
        "blueprint_evidence": {
            "total_marks_authority": "explicit_blueprint_cell",
            "explicit_blueprint_cell": _content_evidence(cell),
            "decomposition_authority": "api_per_item_verdict",
            "external_marking_rubric": "not_part_of_contract",
            "external_marking_rubric_consulted": False,
            "instruction": (
                "Author the per-item decomposition within the explicit cell's "
                "total. No external marking-rubric document is consulted or "
                "expected."
            ),
        },
    }


def _assemble(
    response: Mapping[str, Any],
    *,
    candidate_id: str,
    cell: Mapping[str, Any],
    decision: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "candidate_id": candidate_id,
        "marks": float(_decimal(cell.get("marks")) or Decimal(0)),
        "question": str(response.get("question") or ""),
        "question_text": str(response.get("question_text") or ""),
        "answers": copy.deepcopy(list(response.get("answers") or [])),
        "sub_questions": copy.deepcopy(
            list(response.get("sub_questions") or [])
        ),
        "question_duration": float(
            _decimal(response.get("question_duration")) or Decimal(0)
        ),
        "duration_basis_count": (
            int(_decimal(response.get("duration_basis_count")))
            if response.get("duration_basis_count") is not None
            and _decimal(response.get("duration_basis_count")) is not None
            else None
        ),
        "math_keyboard": str(response.get("math_keyboard") or ""),
        "rationale": str(response.get("rationale") or ""),
        "blueprint_authority": _blueprint_authority(cell),
        "flags": _review_flags(decision),
        "authority": _authority(decision),
    }


def decide_markings(
    pairs: list[tuple[Mapping, Mapping]],
    *,
    meta: Mapping,
    envelope_sha256: str,
    provider: kernel.Provider | None = None,
    critic: kernel.Critic | None = None,
    store: kernel.DecisionStore | None = None,
    fixer: kernel.Provider | None = None,
    profile: Mapping | str | None = None,
    on_result=None,
) -> list[dict[str, Any]]:
    """Return one cached marking verdict per candidate/cell pair, in order.

    ``profile`` owns the allowed sheet/category, marks, and duration contract.
    Its resolved authoring policy joins the decision payload and therefore the
    immutable decision identity.

    ``on_result`` is forwarded verbatim to the fan-out
    (``kernel.parallel_map_in_order``): an ordered progress hook only,
    never an input to any decision.
    """

    envelope_sha = _envelope_hash(envelope_sha256)
    metadata = _metadata(meta)
    run_profile = assessment_profile.resolve_for_metadata(profile, metadata)
    format_policy = assessment_profile.assessment_format_policy(
        run_profile, metadata,
    )
    prepared: list[
        tuple[str, dict[str, Any], dict[str, Any], Decimal, dict[str, Any]]
    ] = []
    seen_candidates: set[str] = set()
    seen_cells: set[str] = set()
    for position, pair in enumerate(pairs, start=1):
        unit = _prepare_pair(pair, position, run_profile)
        candidate_id, _candidate, cell, _marks, _contract = unit
        cell_id = str(cell["cell_id"])
        if candidate_id in seen_candidates:
            raise MarkingError(
                f"assessment marking repeats candidate_id {candidate_id!r}"
            )
        if cell_id in seen_cells:
            raise MarkingError(
                f"assessment marking repeats explicit cell_id {cell_id!r}"
            )
        seen_candidates.add(candidate_id)
        seen_cells.add(cell_id)
        _validate_cell_format_contract(cell, _marks, format_policy)
        prepared.append(unit)
    if not prepared:
        return []

    provider, critic, fixer = _live_authorities(provider, critic, fixer)
    provider = _kernel_provider(provider)
    critic = _kernel_critic(critic)
    decision_store = store or kernel.DecisionStore()

    def decide_one(
        unit: tuple[
            str, dict[str, Any], dict[str, Any], Decimal, dict[str, Any]
        ],
    ) -> dict[str, Any]:
        candidate_id, candidate, cell, total_marks, contract = unit
        decision = kernel.decide(
            kind="assessment.marking",
            unit_id=candidate_id,
            envelope_sha256=envelope_sha,
            payload=_payload(
                candidate,
                cell,
                contract,
                meta=metadata,
                format_policy=format_policy,
            ),
            provider=provider,
            checker=_checker(
                candidate,
                candidate_id=candidate_id,
                kind=str(cell["sheet_kind"]),
                total_marks=total_marks,
                duration_rule=(
                    _format_rule(
                        format_policy,
                        kind=str(cell["sheet_kind"]),
                        category=str(cell["question_category"]),
                    )
                    or {}
                ).get("duration") or {},
                marks_rule=(
                    _format_rule(
                        format_policy,
                        kind=str(cell["sheet_kind"]),
                        category=str(cell["question_category"]),
                    )
                    or {}
                ).get("marks") or {},
            ),
            critic=critic,
            store=decision_store,
            policy_version=MARKING_POLICY_VERSION,
            fixer=fixer,
        )
        return _assemble(
            decision["response"],
            candidate_id=candidate_id,
            cell=cell,
            decision=decision,
        )

    verdicts = kernel.parallel_map_in_order(
        prepared,
        decide_one,
        max_workers=config.phase3_decision_workers(),
        on_result=on_result,
    )
    expected_ids = [unit[0] for unit in prepared]
    returned_ids = [str(row.get("candidate_id") or "") for row in verdicts]
    if returned_ids != expected_ids or len(returned_ids) != len(set(returned_ids)):
        missing = [value for value in expected_ids if value not in returned_ids]
        unexpected = [value for value in returned_ids if value not in expected_ids]
        raise MarkingError(
            "assessment marking broke exact ordered coverage "
            f"(missing={missing}, unexpected={unexpected})"
        )
    return verdicts

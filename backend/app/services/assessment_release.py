"""Release-domain mechanics for MES assessment releases.

The MES specification (§5, §13, §14) builds everything around an immutable
``AssessmentRelease``: the Concept File and the Master File are projections
of one frozen release object, never of mutable database rows.

Everything in this module is deterministic *mechanics* the spec explicitly
allows local code to enforce (§4): schemas and enums, exact IDs and hashes,
exact-once primary placement, group-identity uniqueness, state transitions,
and the zero-loss invariant. Nothing here makes a pedagogical decision —
semantic authorship and acceptance belong to the API author/critic stages.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping

from .. import bulk_import as bi
from . import assessment_profile
# Aliased: three functions in this module use ``identity`` as a local.
from . import identity as identity_mod
from . import katex_rules

# --------------------------------------------------------------------------- #
# Release state machine (spec §5.6)
# --------------------------------------------------------------------------- #

RELEASE_STATES = (
    "draft",
    "materialized",
    "validated_with_flags",
    "ready_for_upload",
    "publication_pending",
    "uploaded",
    "superseded",
)

# A release only ever moves forward; ``superseded`` is reachable from every
# non-final state because a new version can replace an old one at any point.
_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"materialized", "superseded"}),
    "materialized": frozenset(
        {"validated_with_flags", "ready_for_upload", "superseded"}),
    # Released-with-warnings stays uploadable: only a BLOCKED readiness
    # refuses the database (spec §13.3); flags ride along visibly.
    "validated_with_flags": frozenset(
        {"ready_for_upload", "publication_pending", "superseded"}),
    "ready_for_upload": frozenset({"publication_pending", "superseded"}),
    "publication_pending": frozenset(
        {"uploaded", "ready_for_upload", "superseded"}),
    "uploaded": frozenset({"superseded"}),
    "superseded": frozenset(),
}


class ReleaseStateError(ValueError):
    """An illegal release-state transition was requested."""


def advance_state(current: str, target: str) -> str:
    """Validate one state transition; returns the target on success."""
    if current not in _ALLOWED_TRANSITIONS:
        raise ReleaseStateError(f"unknown release state {current!r}")
    if target not in RELEASE_STATES:
        raise ReleaseStateError(f"unknown release state {target!r}")
    if target not in _ALLOWED_TRANSITIONS[current]:
        raise ReleaseStateError(
            f"illegal release transition {current!r} -> {target!r}")
    return target


# --------------------------------------------------------------------------- #
# Content hashing
# --------------------------------------------------------------------------- #

def canonical_json(value: Any) -> str:
    """Stable JSON serialization for hashing (sorted keys, no whitespace)."""
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def new_release_uid() -> str:
    return f"REL-{uuid.uuid4().hex[:20]}"


# --------------------------------------------------------------------------- #
# Record schemas (spec §5.1–§5.5) — presence/enum validation only
# --------------------------------------------------------------------------- #

GROUP_TYPES = ("Basic", "Intermediate", "Advanced")

# The reference workbooks mark the correct Objective option "Yes" (wrong
# options "No"); older internal data used "1"/"true". All are accepted as
# the correct-option marker; nothing else is.
_CORRECT_OPTION_MARKERS = frozenset({"1", "true", "yes"})


def is_correct_option(value) -> bool:
    return str(value or "").strip().lower() in _CORRECT_OPTION_MARKERS
# ``SHEET_KINDS`` used to be re-declared here as "the default profile's
# sheet kinds, for sites with no profile in hand". [measured] it had ZERO
# readers under ``app/`` and ``tests/`` — a declaration with no reader is
# what C10's key-with-no-reader pin exists to catch, so it is gone.
# ``assessment_profile.sheet_kinds(profile)`` is the one owner (T12/M5b).
ANSWER_RESTRICTIONS = ("Open", "Specific")

_SOURCE_ATOM_REQUIRED = (
    "source_qid", "source_document_hash", "source_kind", "raw_text",
)
_BLUEPRINT_CELL_REQUIRED = (
    "cell_id", "sheet_kind", "question_category", "cognitive_skill",
    "difficulty", "marks", "count", "appears_in", "source_policy",
)
# ``source_atom_ids`` is deliberately NOT in this tuple.  ``_missing``
# tests ``str(record.get(field)).strip() == ""``, and ``str([])`` is
# ``"[]"`` — non-empty — so an empty list passed this check by string
# coercion rather than by design.  A generated candidate legitimately
# carries ``[]`` (it reuses no source question), and resting that lane on
# a coercion accident means a future tightening of ``_missing`` breaks it
# silently.  The field is therefore checked explicitly below, where the
# rule is written down: a list of non-empty ids, empty only when the
# candidate declares ``source_policy == "generate"``.
_CANDIDATE_REQUIRED = (
    "candidate_id", "blueprint_cell_id", "question",
    "question_text", "sheet_kind", "question_category", "cognitive_skill",
    "difficulty", "marks", "question_duration", "answer_restriction",
    "restriction_reason",
)

# The one value of a candidate's ``source_policy`` that permits an empty
# ``source_atom_ids``. Read here and in ``assessment_release_run``'s
# generated lane from this single owner so the two can never drift.
GENERATED_SOURCE_POLICY = "generate"
# Master Governing Contract v2.0 §28.1 — the approved English rubric-tag
# registry. A tag is REQUIRED at the head of every populated textual rubric
# criterion of an English Descriptive item and FORBIDDEN everywhere else:
# every other field of an English item, and every rubric of every other
# subject (§28.3). ``[creative]`` is the deprecated spelling (§2.1) and is
# invalid everywhere. The historical registry (method/working/diagram/…) is
# superseded by this contract.
RUBRIC_TAGS = (
    "content", "evidence", "reasoning", "organisation", "language",
    "creativity", "accuracy",
)
DEPRECATED_RUBRIC_TAGS = ("creative",)
# Any ``[word]`` prefix that is not one of the approved tags is malformed on
# an English rubric and forbidden on every other rubric.
_RUBRIC_TAG_PREFIX_RE = re.compile(r"^\s*\[(?P<tag>[^\]\n]*)\](?P<colon>:?)")
_RUBRIC_TAG_ANYWHERE_RE = re.compile(
    r"\[(?P<tag>"
    + "|".join(re.escape(tag) for tag in (*RUBRIC_TAGS, *DEPRECATED_RUBRIC_TAGS))
    + r")\]\s*:?",
    re.IGNORECASE,
)
_PLACEMENT_REQUIRED = (
    "candidate_id", "concept_id", "group_key", "evidence",
)
_GROUP_REQUIRED = (
    "group_key", "concept_id", "group_type", "group_sequence", "group_name",
    "group_display_name",
)


def _missing(record: Mapping, required: Iterable[str]) -> list[str]:
    return [
        field for field in required
        if str(record.get(field) if record.get(field) is not None else "")
        .strip() == ""
        and record.get(field) not in (0, 0.0)
    ]


def option_content_has_label(value: Any) -> bool:
    """Whether an option repeats the letter label supplied by the workbook."""

    return re.match(
        r"^(?:\([A-Za-z]\)|[A-Za-z][).:])",
        str(value or "").lstrip(),
    ) is not None


def rubric_tag_of(value: Any) -> tuple[str, str] | None:
    """``(tag, remainder)`` when a rubric cell opens with a bracket tag."""

    match = _RUBRIC_TAG_PREFIX_RE.match(str(value or ""))
    if match is None:
        return None
    return match.group("tag").strip(), str(value or "")[match.end():]


def malformed_rubric_tag(
    value: Any, answer_type: Any = "Phrases", *,
    tags_required: bool | None = None,
) -> bool:
    """Whether a Descriptive textual rubric criterion breaks tag containment.

    Contract v2.0 §28 / Appendix C.1. ``tags_required`` says which rule the
    run is under — ``True`` for an English run (every populated textual
    criterion MUST open with exactly one approved tag, ``[tag]: text``),
    ``False`` for every other subject (a tag is FORBIDDEN; the criterion is
    written directly), and ``None`` for a reader that does not know the
    subject (either form is accepted, but a malformed, deprecated or empty
    tagged criterion is still refused).

    The tag is metadata for a criterion, not the criterion itself.  Accepting
    ``"[content]:   "`` used to let an empty scoring block satisfy both the
    rubric count and weight arithmetic gates.  Keep the shared predicate as
    the single owner so staging, refinement, rendering, and strict import all
    reject that shape identically.  Equation and Image cells are never
    tagged (§28.3: the tag is for textual criteria only).
    """

    if str(answer_type or "") != "Phrases":
        return False
    text = str(value or "")
    if not text.strip():
        return True
    found = rubric_tag_of(text)
    if found is None:
        # Untagged criterion: valid unless the run REQUIRES tags.
        return tags_required is True
    tag, remainder = found
    if tags_required is False:
        return True  # a tag where tags are forbidden
    if not text.lstrip().startswith(f"[{tag}]:"):
        return True  # the colon is part of the syntax
    stripped = remainder.strip()
    if not stripped or stripped.startswith("["):
        return True  # empty criterion, or two adjacent tags
    if tags_required is None:
        # A reader that does not know the run's subject (a legacy import)
        # refuses only the shapes that are malformed under every rule.
        return False
    lowered = tag.casefold()
    if lowered in DEPRECATED_RUBRIC_TAGS or lowered not in RUBRIC_TAGS:
        return True
    return tag != lowered  # exact lowercase spelling (§28.2)


def rubric_tag_leaks(value: Any) -> list[str]:
    """Every approved/deprecated tag appearing in a field that may carry none.

    Contract v2.0 §28.3 / Appendix C.3: questions, options, accepted
    answers, model answers, explanations, descriptions and metadata never
    carry a rubric tag. Mechanics: a substring test for the registry, never
    a judgment about the text.
    """

    return [
        match.group("tag").casefold()
        for match in _RUBRIC_TAG_ANYWHERE_RE.finditer(str(value or ""))
    ]


def _answer_prefix_key(value: Any) -> str:
    """Whitespace/punctuation-insensitive comparison key for a leading answer."""

    text = str(value or "").replace("\n", " ").strip().casefold()
    text = re.sub(r"\s+", " ", text)
    return text.rstrip(" .;:,!?")


def objective_explanation_defects(
    answers: list[Mapping], explanation: Any,
) -> list[str]:
    """Contract v2.0 §22.5 (QST-003): the Objective explanation opens with the
    exact correct-answer text, then the rationale; never an option letter,
    number or "option b".

    Mechanics only: the correct option is the model's recorded marker and
    the comparison is a normalized prefix test — the contract's own gate
    (§42 gate 6).  An explanation that opens with the exact answer text
    cannot open with a label unless the answer IS that token ("8.", "3:4",
    "H."), so no label or "option N" regex classifies the prose: that
    clause rides the materialization prompt and the joint item review.
    An Equation key is accepted in its raw or its ``[Katex]``-wrapped
    spelling (the rich-text field forbids raw LaTeX); an Image key is a
    URL, not prose, and is left to the item review.
    """

    text = str(explanation or "")
    if not text.strip():
        return ["answer_explanation is empty"]
    correct = [
        answer
        for answer in answers
        if isinstance(answer, Mapping)
        and is_correct_option(answer.get("correct_answer"))
    ]
    if len(correct) != 1:
        return []
    key = correct[0]
    content = str(key.get("answer_content") or "")
    medium = str(key.get("answer_type") or "").strip().lower()
    if medium == "image":
        return []
    accepted = {_answer_prefix_key(content)}
    if medium == "equation":
        from . import katex_rules

        accepted.add(_answer_prefix_key(
            katex_rules.rich_answer_display("Equation", content)
        ))
    accepted.discard("")
    actual = _answer_prefix_key(text)
    if accepted and not any(actual.startswith(head) for head in accepted):
        return [
            "answer_explanation must begin with the exact correct "
            f"answer text {content.strip()!r}"
        ]
    return []


def _rich_text_key(value: Any) -> str:
    """Canonical whitespace/HTML normalization for model-answer parity."""

    text = re.sub(r"<br\s*/?>", "\n", str(value or ""), flags=re.IGNORECASE)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    return "\n".join(line for line in lines if line).strip()


def descriptive_answer_parity_defects(
    display_answer: Any, explanation: Any,
) -> list[str]:
    """Contract v2.0 §24 (ANS-001): ``display_answer`` and
    ``answer_explanation`` are byte-equivalent after canonical
    whitespace/HTML normalization — one complete model answer, twice."""

    if _rich_text_key(display_answer) != _rich_text_key(explanation):
        return [
            "display_answer and answer_explanation must be the same "
            "complete model answer (byte-equivalent after whitespace/HTML "
            "normalization)"
        ]
    return []


RUBRIC_CRITERION_WEIGHTS = (Decimal("0.5"), Decimal("1"))


def rubric_weight_quantum_defect(weight: Decimal | None, *, what: str) -> str:
    """Contract v2.0 §27.5 / §32 (RUB-002): a rubric criterion carries
    exactly ``0.5`` or ``1``; a larger award is split into discrete
    criteria.  Returns the defect text, or ``""`` when the weight is legal
    (or unparsable — that is reported by the numeric gate, not here)."""

    if weight is None or weight in RUBRIC_CRITERION_WEIGHTS:
        return ""
    return (
        f"{what} weight {weight:g} is not 0.5 or 1; each rubric criterion "
        "carries exactly 0.5 or 1 mark (split a larger award into discrete "
        "criteria)"
    )


def model_answer_leak_defects(
    candidate: Mapping, *, sheet_kind: str,
) -> list[str]:
    """Rubric tags in any field that may carry none (contract v2.0 §28.3)."""

    defects: list[str] = []
    fields = [
        ("question", candidate.get("question")),
        ("question_text", candidate.get("question_text")),
        ("display_answer", candidate.get("display_answer")),
        ("answer_explanation", candidate.get("answer_explanation")),
    ]
    sub_questions = candidate.get("sub_questions")
    answers = candidate.get("answers")
    # A malformed collection is reported by the shape checker; this pass
    # reads only what is a list.
    for position, sub in enumerate(
        sub_questions if isinstance(sub_questions, list) else [], 1,
    ):
        if isinstance(sub, Mapping):
            fields.append((f"sub_question {position} text", sub.get("text")))
    if sheet_kind in {"objective", "subjective"}:
        for position, answer in enumerate(
            answers if isinstance(answers, list) else [], 1,
        ):
            if isinstance(answer, Mapping):
                fields.append((
                    f"{sheet_kind} answer {position}",
                    answer.get("answer_content"),
                ))
                if sheet_kind == "subjective":
                    fields.append((
                        f"subjective answer {position} display",
                        answer.get("answer_display"),
                    ))
    for name, value in fields:
        tags = rubric_tag_leaks(value)
        if tags:
            defects.append(
                f"{name} carries rubric tag(s) {sorted(set(tags))}; tags are "
                "permitted only at the head of an English Descriptive "
                "rubric criterion"
            )
    return defects


def validate_source_atom(atom: Mapping) -> list[str]:
    errors = [f"missing {f}" for f in _missing(atom, _SOURCE_ATOM_REQUIRED)]
    for asset in atom.get("assets") or []:
        if not str(asset.get("url") or "").startswith("https://"):
            errors.append(
                f"asset without HTTPS url on {atom.get('source_qid')}")
        if not str(asset.get("alt") or "").strip():
            errors.append(f"asset without alt on {atom.get('source_qid')}")
    return errors


def validate_blueprint_cell(
    cell: Mapping, profile: Mapping | str | None = None,
) -> list[str]:
    errors = [
        f"missing {f}" for f in _missing(cell, _BLUEPRINT_CELL_REQUIRED)
    ]
    # The allowed set is the PROFILE's, not a school baked into a message
    # (spec-step8 T12/M6): what a school's papers use is a profile fact.
    allowed = assessment_profile.sheet_kinds(profile)
    if cell.get("sheet_kind") not in allowed:
        errors.append(
            f"sheet_kind must be one of {allowed} "
            f"(got {cell.get('sheet_kind')!r})")
    return errors


def validate_candidate(
    candidate: Mapping, profile: Mapping | str | None = None,
) -> list[str]:
    # Imported at call time to avoid the bulk-import package's workbook ->
    # release import cycle while still sharing its declared wire enum.
    from .. import bulk_import as bi

    errors = [f"missing {f}" for f in _missing(candidate, _CANDIDATE_REQUIRED)]
    # Explicit, deliberate, and independent of ``_missing``'s string
    # coercion (see ``_CANDIDATE_REQUIRED``).  Identity accounting only:
    # it reads no meaning out of the ids, it just states which lane may
    # carry none.
    atom_ids = candidate.get("source_atom_ids")
    if not isinstance(atom_ids, list):
        errors.append("source_atom_ids must be a list")
    elif any(not str(value or "").strip() for value in atom_ids):
        errors.append("source_atom_ids must not contain an empty id")
    elif not atom_ids and str(
        candidate.get("source_policy") or ""
    ) != GENERATED_SOURCE_POLICY:
        errors.append(
            "source_atom_ids may be empty only on a candidate that declares "
            f"source_policy {GENERATED_SOURCE_POLICY!r}"
        )
    kind = candidate.get("sheet_kind")
    allowed_kinds = assessment_profile.sheet_kinds(profile)
    if kind not in allowed_kinds:
        errors.append(
            f"sheet_kind must be one of {allowed_kinds} "
            f"(got {kind!r})")
    # Contract v2.0 §28: English runs require the approved tag at the head
    # of every textual Descriptive rubric criterion; every other run forbids
    # one. Read from the run's frozen subject metadata (mechanics).
    tags_required = assessment_profile.rubric_tags_required(profile)
    restriction = candidate.get("answer_restriction")
    if restriction not in ANSWER_RESTRICTIONS:
        # Never silently default an unknown restriction (spec §3.5).
        errors.append(
            f"answer_restriction must be one of {ANSWER_RESTRICTIONS} "
            f"(got {restriction!r})")
    if not str(candidate.get("restriction_reason") or "").strip():
        errors.append("restriction_reason must be non-empty")
    if candidate.get("question") != candidate.get("question_text"):
        errors.append("question must equal question_text")

    def finite(value: Any) -> Decimal | None:
        if isinstance(value, bool) or value is None:
            return None
        if isinstance(value, str):
            text = value.strip()
            if not text or text.startswith(("+", "-")):
                return None
        try:
            number = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
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

    marks = finite(candidate.get("marks"))
    if marks is None or marks <= 0:
        errors.append("marks must be finite and positive")
        marks = Decimal(0)
    duration = finite(candidate.get("question_duration"))
    if duration is None or duration <= 0:
        errors.append("question_duration must be finite and positive")

    raw_answers = candidate.get("answers")
    if not isinstance(raw_answers, list):
        errors.append("answers must be an array")
        answers: list[Mapping] = []
    else:
        answers = []
        for position, answer in enumerate(raw_answers, start=1):
            if not isinstance(answer, Mapping):
                errors.append(f"answer {position} is not an object")
            else:
                answers.append(answer)
    for position, answer in enumerate(answers, start=1):
        answer_type = answer.get("answer_type")
        if answer_type not in bi.ANSWER_TYPES:
            errors.append(
                f"answer {position} answer_type must be one of "
                f"{tuple(bi.ANSWER_TYPES)} (got {answer_type!r})"
            )
        for issue in katex_rules.answer_cell_issues(
            str(answer_type or ""), str(answer.get("answer_content") or "")
        ):
            errors.append(
                f"answer {position} violates declared medium: {issue}"
            )
        if kind == "objective" and option_content_has_label(
            answer.get("answer_content")
        ):
            errors.append(
                f"objective option {position} repeats its letter label"
            )
        if kind == "descriptive" and malformed_rubric_tag(
            answer.get("answer_content"), answer_type,
            tags_required=tags_required,
        ):
            errors.append(
                f"descriptive rubric {position} breaks English rubric-tag "
                "containment (contract v2.0 §28): "
                + (
                    "an English criterion opens with exactly one approved "
                    "tag as '[tag]: text'"
                    if tags_required
                    else "a non-English criterion carries no bracket tag "
                    "and is not empty"
                )
            )
    raw_subquestions = candidate.get("sub_questions")
    if not isinstance(raw_subquestions, list):
        errors.append("sub_questions must be an array")
        subquestions: list[Mapping] = []
    else:
        subquestions = []
        for position, subquestion in enumerate(raw_subquestions, start=1):
            if not isinstance(subquestion, Mapping):
                errors.append(f"subquestion {position} is not an object")
            else:
                subquestions.append(subquestion)

    keyboard = candidate.get("math_keyboard")
    for field in (
        "question", "question_text", "display_answer", "answer_explanation",
    ):
        rich_value = str(candidate.get(field) or "")
        if kind == "subjective" and field in {"question", "question_text"}:
            for answer in answers:
                placeholder = str(answer.get("placeholder") or "")
                if len(placeholder) == 1 and "a" <= placeholder <= "t":
                    rich_value = rich_value.replace(
                        f"$${placeholder}$$", ""
                    )
        for issue in katex_rules.rich_text_issues(
            rich_value
        ):
            errors.append(f"{field} rich-text: {issue}")
    if kind == "subjective":
        for position, answer in enumerate(answers, start=1):
            for issue in katex_rules.rich_text_issues(
                str(answer.get("answer_display") or "")
            ):
                errors.append(
                    f"subjective answer {position} answer_display rich-text: "
                    f"{issue}"
                )
    if kind == "objective":
        if keyboard != "":
            errors.append("objective math_keyboard must be exactly blank")
        correct = [
            answer for answer in answers
            if is_correct_option(answer.get("correct_answer"))
        ]
        if len(correct) != 1:
            errors.append(
                f"objective requires exactly one correct option (got "
                f"{len(correct)})"
            )
        for position, answer in enumerate(answers, start=1):
            weight = finite(answer.get("answer_weightage"))
            if weight is None:
                errors.append(
                    f"objective answer {position} weightage must be finite"
                )
                continue
            expected = marks if answer in correct else Decimal(0)
            if weight != expected:
                errors.append(
                    f"objective answer {position} weightage {weight:g} != "
                    f"{expected:g}"
                )
        if subquestions:
            errors.append("objective candidate must not have subquestions")
        errors.extend(objective_explanation_defects(
            answers, candidate.get("answer_explanation"),
        ))
    elif kind == "subjective":
        if keyboard not in {"Yes", "No"}:
            errors.append("subjective math_keyboard must be exactly Yes or No")
        if not answers:
            errors.append("subjective candidate has no expected answers")
        if subquestions:
            errors.append("subjective candidate must not have subquestions")
        weights: list[Decimal] = []
        question = str(candidate.get("question") or "")
        for position, answer in enumerate(answers, start=1):
            expected_placeholder = chr(ord("a") + position - 1)
            if answer.get("placeholder") != expected_placeholder:
                errors.append(
                    f"subjective answer {position} placeholder must be "
                    f"{expected_placeholder!r}"
                )
            if not str(answer.get("answer_display") or "").strip():
                errors.append(
                    f"subjective answer {position} needs answer_display"
                )
            token = f"$${expected_placeholder}$$"
            if question.count(token) != 1:
                errors.append(
                    f"subjective question must contain {token!r} exactly once"
                )
            weight = finite(answer.get("answer_weightage"))
            if weight is None or weight <= 0:
                errors.append(
                    f"subjective answer {position} weightage must be finite "
                    "and positive"
                )
            else:
                weights.append(weight)
        if (
            len(weights) == len(answers)
            and sum(weights, Decimal(0)) != marks
        ):
            errors.append(
                f"subjective answer weightage sum "
                f"{sum(weights, Decimal(0)):g} != marks {marks:g}"
            )
    elif kind == "descriptive":
        if keyboard not in {"Yes", "No"}:
            errors.append("descriptive math_keyboard must be exactly Yes or No")
        if not answers and not subquestions:
            errors.append("descriptive candidate has no rubric blocks")
        if answers and subquestions:
            errors.append(
                "multipart descriptive candidate duplicates scoring in main "
                "answer blocks"
            )
        if marks == Decimal(4) and not subquestions and len(answers) < 2:
            errors.append(
                "4-mark descriptive candidate requires at least two "
                "rubric blocks"
            )
        errors.extend(descriptive_answer_parity_defects(
            candidate.get("display_answer"),
            candidate.get("answer_explanation"),
        ))
        weights: list[Decimal] = []
        for position, answer in enumerate(answers, start=1):
            weight = finite(answer.get("answer_weightage"))
            if weight is None or weight <= 0:
                errors.append(
                    f"descriptive answer {position} weightage must be "
                    "finite and positive"
                )
            else:
                weights.append(weight)
                quantum = rubric_weight_quantum_defect(
                    weight, what=f"descriptive rubric {position}",
                )
                if quantum:
                    errors.append(quantum)
        weight_sum = sum(weights, Decimal(0))
        if (
            not subquestions
            and len(weights) == len(answers)
            and weight_sum != marks
        ):
            errors.append(
                f"descriptive answer weightage sum {weight_sum:g} != "
                f"marks {marks:g}"
            )
        if subquestions:
            sub_marks: list[Decimal] = []
            for position, subquestion in enumerate(subquestions, start=1):
                subquestion_text = str(
                    subquestion.get("text") or ""
                ).strip()
                if not subquestion_text:
                    errors.append(f"subquestion {position} has no text")
                elif subquestion_text in str(candidate.get("question") or ""):
                    errors.append(
                        f"subquestion {position} text is duplicated in the "
                        "main question"
                    )
                for issue in katex_rules.rich_text_issues(
                    subquestion_text
                ):
                    errors.append(
                        f"subquestion {position} text rich-text: {issue}"
                    )
                sub_mark = finite(subquestion.get("marks"))
                if sub_mark is None or sub_mark <= 0:
                    errors.append(
                        f"subquestion {position} marks must be finite and "
                        "positive"
                    )
                    continue
                sub_marks.append(sub_mark)
                keywords = subquestion.get("keywords")
                if not isinstance(keywords, list):
                    errors.append(
                        f"subquestion {position} keywords must be an array"
                    )
                    continue
                if not keywords:
                    errors.append(
                        f"subquestion {position} needs at least one keyword "
                        "rubric"
                    )
                    continue
                keyword_weights: list[Decimal] = []
                for keyword_position, keyword in enumerate(
                    keywords, start=1
                ):
                    if not isinstance(keyword, Mapping):
                        errors.append(
                            f"subquestion {position} keyword "
                            f"{keyword_position} is not an object"
                        )
                        continue
                    keyword_type = keyword.get("answer_type")
                    if keyword_type not in bi.ANSWER_TYPES:
                        errors.append(
                            f"subquestion {position} keyword "
                            f"{keyword_position} answer_type must be one of "
                            f"{tuple(bi.ANSWER_TYPES)}"
                        )
                    for issue in katex_rules.answer_cell_issues(
                        str(keyword_type or ""),
                        str(keyword.get("keyword") or ""),
                    ):
                        errors.append(
                            f"subquestion {position} keyword "
                            f"{keyword_position} violates declared medium: "
                            f"{issue}"
                        )
                    if malformed_rubric_tag(
                        keyword.get("keyword"), keyword_type,
                        tags_required=tags_required,
                    ):
                        errors.append(
                            f"subquestion {position} keyword "
                            f"{keyword_position} breaks English rubric-tag "
                            "containment (contract v2.0 §28)"
                        )
                    weight = finite(keyword.get("weightage"))
                    if weight is None or weight <= 0:
                        errors.append(
                            f"subquestion {position} keyword "
                            f"{keyword_position} weightage must be finite "
                            "and positive"
                        )
                    else:
                        keyword_weights.append(weight)
                        quantum = rubric_weight_quantum_defect(
                            weight,
                            what=(
                                f"subquestion {position} keyword "
                                f"{keyword_position}"
                            ),
                        )
                        if quantum:
                            errors.append(quantum)
                if (
                    len(keyword_weights) == len(keywords)
                    and sum(keyword_weights, Decimal(0)) != sub_mark
                ):
                    errors.append(
                        f"subquestion {position} keyword weightage sum "
                        f"{sum(keyword_weights, Decimal(0)):g} != "
                        "subquestion marks "
                        f"{sub_mark:g}"
                    )
            if (
                len(sub_marks) == len(subquestions)
                and sum(sub_marks, Decimal(0)) != marks
            ):
                errors.append(
                    f"subquestion marks sum {sum(sub_marks, Decimal(0)):g} "
                    "!= marks "
                    f"{marks:g}"
                )
    # Contract v2.0 §28.3 / Appendix C.3 (RUB-004): no rubric tag in any
    # question, option, accepted answer, model answer or explanation.
    errors.extend(model_answer_leak_defects(candidate, sheet_kind=str(kind)))
    return errors


def assessment_format_contract_errors(payload: Mapping) -> list[str]:
    """Validate the resolved profile policy carried by a final payload.

    The model chooses the category, difficulty, and represented subpoints;
    this gate checks only their already-recorded wire contract. Legacy payloads
    that predate format policies carry no policy and retain their historical
    validation path.
    """

    policy = payload.get("assessment_format_policy")
    if policy is None:
        return []
    if not isinstance(policy, Mapping):
        return ["assessment_format_policy must be an object"]
    formats = policy.get("formats_by_sheet")
    if not isinstance(formats, Mapping):
        return [
            "assessment_format_policy.formats_by_sheet must be an object"
        ]

    def finite(value: Any) -> Decimal | None:
        if isinstance(value, bool) or value is None:
            return None
        try:
            number = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            return None
        return number if number.is_finite() else None

    errors: list[str] = []
    cells_by_id: dict[str, Mapping] = {}
    for cell in payload.get("blueprint_cells") or []:
        if not isinstance(cell, Mapping):
            continue
        cell_id = str(cell.get("cell_id") or "")
        if not cell_id:
            continue
        if cell_id in cells_by_id:
            errors.append(f"duplicate blueprint cell_id {cell_id!r}")
            continue
        cells_by_id[cell_id] = cell
        kind = str(cell.get("sheet_kind") or "")
        category = str(cell.get("question_category") or "")
        sheet_formats = formats.get(kind)
        rule = (
            sheet_formats.get(category)
            if isinstance(sheet_formats, Mapping)
            else None
        )
        if not isinstance(rule, Mapping):
            errors.append(
                f"{cell_id}: category {category!r} is not permitted for "
                f"sheet_kind {kind!r} by the resolved assessment policy"
            )
            continue
        marks = finite(cell.get("marks"))
        marks_rule = rule.get("marks")
        if "marks" not in rule:
            continue
        if not isinstance(marks_rule, Mapping):
            errors.append(f"{cell_id}: marks policy must be an object")
            continue
        if marks is None:
            continue
        mode = str(marks_rule.get("mode") or "")
        if not mode:
            errors.append(f"{cell_id}: marks policy has no mode")
        elif mode == "fixed":
            allowed = {
                value
                for raw in marks_rule.get("allowed") or ()
                if (value := finite(raw)) is not None
            }
            if marks not in allowed:
                errors.append(
                    f"{cell_id}: marks {marks:g} are outside the category "
                    f"contract {tuple(sorted(allowed))}"
                )
        elif mode == "per_subpoint":
            unit = finite(marks_rule.get("marks_per_subpoint"))
            raw_maximum = marks_rule.get("max_subpoints")
            maximum = finite(raw_maximum)
            if "max_subpoints" in marks_rule and (
                isinstance(raw_maximum, bool)
                or not isinstance(raw_maximum, (int, float, Decimal))
                or maximum is None
                or maximum <= 0
                or maximum != maximum.to_integral_value()
            ):
                errors.append(
                    f"{cell_id}: max_subpoints must be a positive integer"
                )
            if unit is None or unit <= 0 or marks <= 0 or marks % unit != 0:
                errors.append(
                    f"{cell_id}: marks do not represent a positive whole "
                    "number of policy subpoints"
                )
            else:
                represented = marks / unit
                if (
                    maximum is not None
                    and represented > maximum
                ):
                    errors.append(
                        f"{cell_id}: represented subpoint count "
                        f"{represented:g} exceeds the wire maximum "
                        f"{maximum:g}"
                    )
        elif mode:
            errors.append(
                f"{cell_id}: unknown marks policy mode {mode!r}"
            )

    for candidate in payload.get("candidates") or []:
        if not isinstance(candidate, Mapping):
            continue
        candidate_id = str(candidate.get("candidate_id") or "")
        cell_id = str(candidate.get("blueprint_cell_id") or "")
        cell = cells_by_id.get(cell_id)
        if cell is None:
            errors.append(
                f"{candidate_id}: blueprint_cell_id {cell_id!r} is not in "
                "the release blueprint"
            )
            continue
        for field in (
            "sheet_kind", "question_category", "cognitive_skill",
            "difficulty", "marks",
        ):
            if candidate.get(field) != cell.get(field):
                errors.append(
                    f"{candidate_id}: {field} does not match blueprint "
                    f"cell {cell_id!r}"
                )
        kind = str(cell.get("sheet_kind") or "")
        category = str(cell.get("question_category") or "")
        sheet_formats = formats.get(kind)
        rule = (
            sheet_formats.get(category)
            if isinstance(sheet_formats, Mapping)
            else None
        )
        if not isinstance(rule, Mapping):
            continue
        marks_rule = rule.get("marks")
        if (
            kind == "subjective"
            and isinstance(marks_rule, Mapping)
            and str(marks_rule.get("mode") or "") == "per_subpoint"
        ):
            marks_unit = finite(marks_rule.get("marks_per_subpoint"))
            if marks_unit is not None and marks_unit > 0:
                for position, answer in enumerate(
                    candidate.get("answers") or [], start=1,
                ):
                    weight = (
                        finite(answer.get("answer_weightage"))
                        if isinstance(answer, Mapping) else None
                    )
                    if weight != marks_unit:
                        errors.append(
                            f"{candidate_id}: Subjective answer {position} "
                            f"weight {weight!s} != marks-per-subpoint "
                            f"{marks_unit:g}"
                        )
        duration_rule = rule.get("duration")
        if not isinstance(duration_rule, Mapping) or not duration_rule:
            continue
        duration = finite(candidate.get("question_duration"))
        mode = str(duration_rule.get("mode") or "")
        expected: Decimal | None = None
        if mode == "matrix":
            minutes = duration_rule.get("minutes_by_difficulty")
            if isinstance(minutes, Mapping):
                expected = finite(
                    minutes.get(str(candidate.get("difficulty") or ""))
                )
            if candidate.get("duration_basis_count") is not None:
                errors.append(
                    f"{candidate_id}: matrix duration must not carry a "
                    "duration_basis_count"
                )
        elif mode == "per_subpoint":
            basis = finite(candidate.get("duration_basis_count"))
            marks_unit = (
                finite(marks_rule.get("marks_per_subpoint"))
                if isinstance(marks_rule, Mapping)
                else None
            )
            marks = finite(candidate.get("marks"))
            expected_basis = (
                marks / marks_unit
                if marks is not None
                and marks_unit is not None
                and marks_unit > 0
                and marks % marks_unit == 0
                else None
            )
            if (
                basis is None
                or basis <= 0
                or basis != basis.to_integral_value()
            ):
                errors.append(
                    f"{candidate_id}: per-subpoint duration requires a "
                    "positive integer duration_basis_count"
                )
            elif expected_basis is not None and basis != expected_basis:
                errors.append(
                    f"{candidate_id}: duration_basis_count {basis:g} != "
                    f"represented subpoint count {expected_basis:g}"
                )
            per_subpoint = finite(
                duration_rule.get("minutes_per_subpoint")
            )
            if basis is not None and per_subpoint is not None:
                expected = basis * per_subpoint
            if (
                kind == "subjective"
                and expected_basis is not None
                and len(candidate.get("answers") or [])
                != int(expected_basis)
            ):
                errors.append(
                    f"{candidate_id}: Subjective answer count does not "
                    "match represented subpoint count"
                )
        if expected is None or expected <= 0:
            errors.append(
                f"{candidate_id}: resolved duration policy has no positive "
                "value"
            )
        elif duration != expected:
            errors.append(
                f"{candidate_id}: question_duration {candidate.get('question_duration')!r} "
                f"!= policy duration {expected:g}"
            )
    return errors


def parent_child_candidate_errors(payload: Mapping) -> list[str]:
    """Reject a compound parent and its split child as separate questions."""

    candidates_by_atom: dict[str, set[str]] = {}
    for candidate in payload.get("candidates") or []:
        if not isinstance(candidate, Mapping):
            continue
        candidate_id = str(candidate.get("candidate_id") or "")
        for atom_id in candidate.get("source_atom_ids") or []:
            candidates_by_atom.setdefault(str(atom_id), set()).add(
                candidate_id
            )
    errors: list[str] = []
    for atom in payload.get("source_atoms") or []:
        if not isinstance(atom, Mapping):
            continue
        child = str(atom.get("source_qid") or "")
        parent = str(atom.get("parent_qid") or "")
        if not child or not parent:
            continue
        parent_candidates = candidates_by_atom.get(parent, set())
        child_candidates = candidates_by_atom.get(child, set())
        if parent_candidates and child_candidates and (
            parent_candidates != child_candidates
        ):
            errors.append(
                f"compound source {parent!r} and subpart {child!r} are "
                "materialized as separate questions"
            )
    return errors


def validate_placement(
    placement: Mapping, profile: Mapping | str | None = None,
) -> list[str]:
    errors = [f"missing {f}" for f in _missing(placement, _PLACEMENT_REQUIRED)]
    secondary = placement.get("secondary_placements")
    # Whether automatic secondary placements exist is the profile's EXISTING
    # ``automatic_secondary_tags`` key (spec-step8 T12/M6) — not a second key,
    # and not a school name in a message.
    if secondary and not assessment_profile.automatic_secondary_tags(profile):
        errors.append(
            "secondary_placements must be empty under profile "
            f"{assessment_profile.name(profile)!r} (got {len(secondary)})")
    return errors


def validate_group(group: Mapping) -> list[str]:
    errors = [f"missing {f}" for f in _missing(group, _GROUP_REQUIRED)]
    if group.get("group_type") not in GROUP_TYPES:
        errors.append(
            f"group_type must be one of {GROUP_TYPES} "
            f"(got {group.get('group_type')!r})")
    return errors


# --------------------------------------------------------------------------- #
# Identity and placement invariants (spec §3.7, §5.5, §7.5)
# --------------------------------------------------------------------------- #

def group_identity(group: Mapping) -> tuple:
    """The uniqueness unit: (concept_id, group_type, group_key)."""
    return (
        group.get("concept_id"),
        str(group.get("group_type") or ""),
        str(group.get("group_key") or ""),
    )


def duplicate_group_identities(groups: Iterable[Mapping]) -> list[tuple]:
    seen: set[tuple] = set()
    duplicates: list[tuple] = []
    for group in groups:
        identity = group_identity(group)
        if identity in seen and identity not in duplicates:
            duplicates.append(identity)
        seen.add(identity)
    return duplicates


def duplicate_group_keys(groups: Iterable[Mapping]) -> list[str]:
    """Return repeated internal keys, regardless of concept or tier.

    ``group_key`` is the release-wide machine identity.  Reusing one key for
    a different home is structural corruption, not a semantic concern that a
    later judgment pass may accept with a flag.
    """
    seen: set[str] = set()
    duplicates: list[str] = []
    for group in groups:
        group_key = str(group.get("group_key") or "")
        if not group_key:
            continue
        if group_key in seen and group_key not in duplicates:
            duplicates.append(group_key)
        seen.add(group_key)
    return duplicates


# --------------------------------------------------------------------------- #
# The staged home/shape verdict (spec-step8 T7.5, D8.5b)
# --------------------------------------------------------------------------- #
# A candidate the renderer cannot write to a data row is decided at STAGING,
# recorded once on the release, refused at the publication act by machinery
# that already exists, and merely REPORTED by the renderer.
#
# Why it cannot live in the renderer: ``render_master_file`` runs at
# PROJECTION time, inside ``publish_release``, after the payload is frozen —
# so a discovery made there reaches no readiness computation, and a raise
# made there costs every row on every sheet of all four outputs.  These
# codes are computed here instead, from the four inputs the renderer uses,
# and ride ``freeze_payload``'s existing ``errors`` transport into
# ``diagnostics["payload_errors"]`` → ``_readiness`` BLOCKED → the upload
# refusal.  One vocabulary, one transport, one refusal.
#
# It counts identities and compares key sets against literal tuples.  It
# judges nothing about content, so it is a gate under CLAUDE.md's "what is
# still allowed to be deterministic", not a judgment under Rule 1.

UNRESOLVED_QUESTION_HOME = "unresolved_question_home"
GROUP_HOME_DISAGREEMENT = "group_home_disagreement"
SHEET_KIND_NOT_RENDERABLE = "sheet_kind_not_renderable"
GROUP_CONCEPT_HOME_UNKNOWN = "group_concept_home_unknown"
GROUP_HOME_UNNAMED = "group_home_unnamed"
GROUP_VISIBLE_NAME_MISMATCH = "group_visible_name_mismatch"
RENDER_SHAPE_OVERFLOW = "render_shape_overflow"

QUESTION_HOME_CODES = (
    UNRESOLVED_QUESTION_HOME,
    GROUP_HOME_DISAGREEMENT,
    SHEET_KIND_NOT_RENDERABLE,
    GROUP_CONCEPT_HOME_UNKNOWN,
    GROUP_HOME_UNNAMED,
    GROUP_VISIBLE_NAME_MISMATCH,
    RENDER_SHAPE_OVERFLOW,
)

# Two GROUP-identity codes for the renderer's evidence ledger
# (``issues["group_defects"]``). They are deliberately NOT question-home
# codes and NOT in ``QUESTION_HOME_CODES``: a blank or duplicate
# ``group_key`` is refused at freeze by ``validate_group`` /
# ``duplicate_group_keys`` and again by ``validate_master_file``'s
# read-back, so nothing here decides anything. They exist because the
# renderer used to tag both with ``UNRESOLVED_QUESTION_HOME`` — a
# candidate-level code on a group-level defect, which put the wrong
# vocabulary in front of a reviewer reading ``manifest.json``.
GROUP_KEY_BLANK = "group_key_blank"
GROUP_KEY_DUPLICATE = "group_key_duplicate"

GROUP_LEDGER_CODES = (
    GROUP_KEY_BLANK,
    GROUP_KEY_DUPLICATE,
    GROUP_CONCEPT_HOME_UNKNOWN,
)


def _finding(code: str, message: str, **fields: Any) -> dict:
    finding = {"code": code, "message": message}
    finding.update({k: v for k, v in fields.items()})
    return finding


def candidate_home_finding(
    candidate: Mapping,
    concept_keys,
    groups_by_key: Mapping,
    renderable,
    profile_name: str = "",
) -> dict | None:
    """THE decision "can this candidate be written to a data row".

    ONE implementation, two callers: ``unresolved_question_homes`` below
    (which stages it, so readiness and the upload refusal see it) and
    ``assessment_workbook.render_master_file`` (which records the same
    verdict in the issues manifest's unplaced ledger as the evidence
    channel).  They used
    to be two inline copies of the same three branches reading the same
    four inputs — and they had already drifted: the staged copy compared
    against ``RENDERABLE_SHEET_KINDS`` while the renderer compared against
    ``sheet_for_kind()``, which is that tuple INTERSECTED with the layout's
    sheet-name map.  [measured] under a layout carrying no Descriptive
    sheet the renderer dropped every descriptive candidate while the staged
    verdict returned nothing, so readiness read ``ready`` and the database
    write proceeded with those questions in no data row.  That is the R4
    hole this slice exists to close, one layer up, and two implementations
    of one decision is what T1 exists to end.

    ``renderable`` is the caller's own renderable-kind set, so the drift
    cannot come back: the renderer passes ``tuple(sheet_for_kind())`` and
    the staging pass passes the same.

    Returns one finding dict, or ``None`` when the candidate can be placed.
    It compares key sets and membership only — a gate under CLAUDE.md's
    "mechanics, not meaning", never a judgment about content.
    """

    candidate_id = str(candidate.get("candidate_id") or "")
    label = str(candidate.get("question_label") or "")
    concept_key = str(candidate.get("concept_key") or "")
    group_key = str(candidate.get("group_key") or "")
    identity = label or candidate_id or "candidate"
    named = {"candidate_id": candidate_id, "question_label": label}

    if (
        concept_key not in concept_keys
        or group_key not in groups_by_key
        or not label
    ):
        return _finding(
            UNRESOLVED_QUESTION_HOME,
            f"{identity}: unresolved home concept/group placement "
            f"(concept_key {concept_key!r}, group_key {group_key!r})",
            concept_key=concept_key, group_key=group_key, **named)

    group_concept_key = str(groups_by_key[group_key].get("concept_key") or "")
    if group_concept_key != concept_key:
        return _finding(
            GROUP_HOME_DISAGREEMENT,
            f"{identity}: concept_key {concept_key!r} does not match "
            f"group {group_key!r} home {group_concept_key!r}",
            concept_key=concept_key, group_key=group_key, **named)

    kind = str(candidate.get("sheet_kind") or "")
    if kind not in renderable:
        return _finding(
            SHEET_KIND_NOT_RENDERABLE,
            f"{identity}: sheet_kind {kind!r} is outside the kinds this "
            f"step renders {tuple(renderable)}",
            sheet_kind=kind, profile=profile_name,
            renderable_sheet_kinds=list(renderable), **named)
    return None


def _cell_shape_findings(
    record: Mapping, where: str, **named: Any,
) -> list[dict]:
    """Every cell in one RENDERED record that no XLSX cell can hold (S9).

    The staging twin of ``assessment_workbook._cell_value``'s repair. Two
    things keep it from being a second implementation of one rule — the
    drift ``candidate_home_finding``'s docstring records is what that
    costs:

    * the predicate is ``assessment_workbook.cell_text_defects``, that
      module's own, so the cap and the illegal-character set are read from
      the format in exactly one place;
    * the input is the record the RENDERER will build, not the raw
      candidate. A staged field the renderer never writes into a cell must
      not refuse a database write, and a field it does write must not
      escape — only the rendered record answers both.

    ONE FINDING PER SHAPE, because one cell can trip both. [measured] while
    the predicate answered with the first reason only, a value that was
    over the cap AND carried an XML-illegal code point was named
    ``cell_text_too_long`` here and nothing named the code point — so a
    reviewer reading the findings had no way to know what actually broke
    the file when openpyxl raised on it.

    It looks at LENGTH and at which code points a cell may carry. It never
    reads what the text says.
    """
    from ..bulk_import import assessment_workbook as workbook

    findings: list[dict] = []
    for field, value in record.items():
        # Measured on the ``<br>``-projected text the cell will hold (§17),
        # exactly as the renderer's cell writer measures it.
        projected = (
            bi.to_workbook_rich_text(value) if isinstance(value, str)
            else value
        )
        for defect in workbook.cell_text_defects(projected):
            findings.append(_finding(
                RENDER_SHAPE_OVERFLOW,
                f"{where}: {field} carries {defect['actual']} "
                + (
                    f"character(s), and one cell holds {defect['cap']}"
                    if defect["reason"] == workbook.CELL_TEXT_TOO_LONG
                    else "character(s) no XLSX cell may carry ("
                    + ", ".join(defect.get("code_points") or [])
                    + ")"
                ),
                field=str(field), cap=defect["cap"],
                actual=defect["actual"],
                reason=defect["reason"], **named))
    return findings


def unresolved_question_homes(
    snapshot: Mapping, profile: Mapping | str | None = None,
) -> list[dict]:
    """Every home/shape defect the Master renderer used to raise or drop.

    A PURE function of the frozen snapshot's ``candidates``, ``groups`` and
    ``concept_key`` set plus the profile's allowed sheet kinds — the same
    four inputs ``assessment_workbook.render_master_file`` reads, so it can
    decide at STAGING what the renderer used to discover at projection time.

    Returns a list of ``{"code", "message", …identity}`` findings.  The
    caller appends them to the frozen payload's ``errors``.
    """

    # Local imports: ``assessment_workbook`` and ``assessment_grouping``
    # both import this module, so the renderable set, the layout's own
    # column caps and the learner-visible group-name composition are read
    # here rather than declared twice.  Both edges are one-way and taken at
    # call time.
    from ..bulk_import import assessment_workbook as workbook
    from . import assessment_grouping as grouping

    findings: list[dict] = []

    concept_names: dict[str, str] = {}
    for topic in snapshot.get("topics") or []:
        for concept in topic.get("concepts") or []:
            concept_names[str(concept.get("concept_key") or "")] = str(
                concept.get("concept_display_name") or ""
            ).strip()
            # The concept band is written into every Master row and into
            # every Concept File row, so a concept cell no XLSX cell can
            # hold is the same defect one band over (S9). The renderer now
            # COMPOSES the title cell off the machine id (B4), so the
            # measured mapping must carry the composed value or this gate
            # measures a string the renderer no longer writes (the audit's
            # near-limit truncation hole).
            concept_machine_id = str(
                concept.get("concept_machine_id") or "").strip()
            rendered_shape = dict(concept)
            if concept_machine_id:
                rendered_shape["concept_title"] = identity_mod.titled(
                    str(concept.get("concept_title") or ""),
                    concept_machine_id,
                )
            findings.extend(_cell_shape_findings(
                rendered_shape,
                f"concept {concept.get('concept_title') or ''}".strip(),
                concept_key=str(concept.get("concept_key") or ""),
            ))

    groups_by_key: dict[str, Mapping] = {}
    for group in snapshot.get("groups") or []:
        group_key = str(group.get("group_key") or "")
        if not group_key or group_key in groups_by_key:
            # Blank and duplicate ``group_key`` are already refused at
            # freeze by ``validate_group``/``duplicate_group_keys`` and
            # again by ``validate_master_file``'s read-back; naming them a
            # third time here would be a third vocabulary for one defect.
            continue
        groups_by_key[group_key] = group

    for group_key, group in groups_by_key.items():
        concept_key = str(group.get("concept_key") or "")
        if concept_key not in concept_names:
            findings.append(_finding(
                GROUP_CONCEPT_HOME_UNKNOWN,
                f"group {group_key!r} has unknown concept home "
                f"{concept_key!r}",
                group_key=group_key, concept_key=concept_key))
            continue
        concept_name = concept_names[concept_key]
        if not concept_name:
            findings.append(_finding(
                GROUP_HOME_UNNAMED,
                f"group {group_key!r} home {concept_key!r} has no explicit "
                "concept_display_name",
                group_key=group_key, concept_key=concept_key))
            continue
        tier = str(group.get("group_type") or "")
        if tier not in GROUP_TYPES or tier not in grouping.TIER_CODES:
            # ``validate_group`` already refuses this at freeze.
            continue
        # ONE owner for the visible composition (it used to be spelled
        # inline here, so a composition change would have falsely flagged
        # every group of every release): SOP §6.1 (Q16, superseding Q12)
        # says ``group_name`` and ``group_display_name`` both carry the
        # group ID itself — the ``group_key`` this row already holds.
        if (
            str(group.get("group_name") or "") != group_key
            or str(group.get("group_display_name") or "") != group_key
        ):
            findings.append(_finding(
                GROUP_VISIBLE_NAME_MISMATCH,
                f"group {group_key!r} visible names must both equal "
                "the group ID itself (SOP §6.1)",
                group_key=group_key, concept_key=concept_key))

    allowed_kinds = assessment_profile.sheet_kinds(profile)
    # ``sheet_for_kind()``, NOT ``RENDERABLE_SHEET_KINDS``: the renderer
    # places a candidate iff ``sheet_for_kind()`` names a sheet for its
    # kind, and that map is the constant INTERSECTED with the layout's own
    # sheet-name map.  Reading the un-intersected constant here was a
    # second source for one question — see ``candidate_home_finding``.
    sheet_by_kind = workbook.sheet_for_kind()
    renderable = tuple(sheet_by_kind)
    profile_name = assessment_profile.name(profile)
    master_schema = workbook.output_schema("master", profile, snapshot)
    descriptive_answer_slots = int(
        master_schema.get("descriptive_answer_slots")
        or workbook.MAX_DESCRIPTIVE_ANSWERS
    )
    caps = (
        ("answers", workbook.MAX_OBJECTIVE_OPTIONS, ("objective",)),
        ("answers", workbook.MAX_SUBJECTIVE_ANSWERS, ("subjective",)),
        ("answers", descriptive_answer_slots, ("descriptive",)),
        ("sub_questions", workbook.MAX_SUBQUESTIONS, ("descriptive",)),
    )

    def _mapping_array(value: Any) -> list[Mapping]:
        """Read a generated array without reopening a projection crash.

        ``validate_candidate`` owns the user-facing shape finding.  This
        audit pass must remain total long enough for that blocked release to
        be persisted, so a malformed container contributes no renderable
        members here instead of being iterated or measured as an array.
        """

        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, Mapping)]

    for candidate in snapshot.get("candidates") or []:
        candidate_id = str(candidate.get("candidate_id") or "")
        label = str(candidate.get("question_label") or "")
        identity = label or candidate_id or "candidate"
        home = candidate_home_finding(
            candidate, concept_names, groups_by_key, renderable, profile_name)
        if home is not None:
            if (
                home["code"] == SHEET_KIND_NOT_RENDERABLE
                and home["sheet_kind"] not in allowed_kinds
            ):
                # A kind the PROFILE does not allow at all is already
                # refused at freeze by ``validate_candidate``. Naming it
                # here as well would be two vocabularies for one defect —
                # the negative control the reference profile pins. The
                # renderer still records it in the issues manifest's
                # unplaced ledger, so nothing about it is silent.
                continue
            findings.append(home)
            continue
        kind = str(candidate.get("sheet_kind") or "")
        for field, cap, kinds in caps:
            if kind not in kinds:
                continue
            actual = len(_mapping_array(candidate.get(field)))
            if actual > cap:
                findings.append(_finding(
                    RENDER_SHAPE_OVERFLOW,
                    f"{identity}: {actual} {field} exceeds the layout's "
                    f"{cap} slots",
                    candidate_id=candidate_id, question_label=label,
                    field=field, cap=cap, actual=actual))
        sheet_name = sheet_by_kind.get(kind)
        if sheet_name:
            findings.extend(_cell_shape_findings(
                workbook._question_record(
                    candidate,
                    sheet_name,
                    profile,
                    descriptive_answer_slots=descriptive_answer_slots,
                ),
                f"{identity}",
                candidate_id=candidate_id, question_label=label,
            ))
        if kind == "descriptive":
            for n, sub in enumerate(
                _mapping_array(candidate.get("sub_questions")), 1,
            ):
                actual = len(_mapping_array(sub.get("keywords")))
                if actual > workbook.MAX_SUBQUESTION_KEYWORDS:
                    findings.append(_finding(
                        RENDER_SHAPE_OVERFLOW,
                        f"{identity}: subquestion {n} has {actual} keyword "
                        f"slots, the layout carries "
                        f"{workbook.MAX_SUBQUESTION_KEYWORDS}",
                        candidate_id=candidate_id, question_label=label,
                        field=f"sub_questions[{n}].keywords",
                        cap=workbook.MAX_SUBQUESTION_KEYWORDS, actual=actual))
    return findings


def duplicate_question_labels(candidates: Iterable[Mapping]) -> list[str]:
    """Return repeated question labels inside one release.

    ``question_label`` is the learner-facing machine identity of a question.
    Reusing one label for a different question is structural corruption, not a
    semantic concern that a later judgment pass may accept with a flag: the
    database write has exactly one row per label, so the second question does
    not get stored differently — it does not get stored at all, and a learner
    question that vanishes with no record is the one loss R4 forbids.

    Seen HERE, at freeze, the collision reaches ``_readiness`` as a payload
    error, the release goes BLOCKED, every download still ships, and only the
    database write is refused (T9's principle; the same transport
    ``duplicate_group_keys`` already rides).

    Blank labels are refused one at a time at the upload, by name; they are not
    collisions with each other and are skipped here.
    """
    seen: set[str] = set()
    duplicates: list[str] = []
    for candidate in candidates:
        label = str(candidate.get("question_label") or "")
        if not label:
            continue
        if label in seen and label not in duplicates:
            duplicates.append(label)
        seen.add(label)
    return duplicates


def primary_placement_errors(placements: Iterable[Mapping]) -> list[str]:
    """Exactly one home concept and group per question (spec §3.7)."""
    errors: list[str] = []
    seen: dict[str, tuple] = {}
    for placement in placements:
        candidate_id = str(placement.get("candidate_id") or "")
        home = (
            placement.get("concept_id"),
            str(placement.get("group_key") or ""),
        )
        if candidate_id in seen:
            errors.append(
                f"{candidate_id}: more than one primary placement "
                f"({seen[candidate_id]} and {home})")
            continue
        seen[candidate_id] = home
    return errors


# --------------------------------------------------------------------------- #
# Zero-loss invariant (spec §14)
# --------------------------------------------------------------------------- #

def zero_loss_report(
    obligations: Iterable[str],
    accepted: Iterable[str],
    flagged: Iterable[str],
) -> dict:
    """source obligations = accepted rows + explicitly flagged rows.

    No QID, subpart, OR branch, or blueprint cell may disappear. Returns the
    exact identities that break the invariant so a defect is named, never
    hidden.
    """
    obligation_set = {str(o) for o in obligations if str(o).strip()}
    accepted_set = {str(a) for a in accepted if str(a).strip()}
    flagged_set = {str(f) for f in flagged if str(f).strip()}
    covered = accepted_set | flagged_set
    return {
        "missing": sorted(obligation_set - covered),
        "unexpected": sorted(covered - obligation_set),
        "double_counted": sorted(accepted_set & flagged_set),
        "holds": (
            obligation_set == covered and not (accepted_set & flagged_set)
        ),
    }


# --------------------------------------------------------------------------- #
# Release construction
# --------------------------------------------------------------------------- #

def freeze_payload(
    payload: Mapping, profile: Mapping | str | None = None,
) -> dict:
    """Validate and hash a complete release payload.

    Returns {"errors": [...], "hashes": {...}}. Mechanical gates only: any
    semantic concern lives in the payload's own flags, which are preserved,
    never resolved here.
    """
    errors: list[str] = []
    for atom in payload.get("source_atoms") or []:
        errors.extend(validate_source_atom(atom))
    for cell in payload.get("blueprint_cells") or []:
        errors.extend(validate_blueprint_cell(cell, profile))
    for candidate in payload.get("candidates") or []:
        errors.extend(validate_candidate(candidate, profile))
    errors.extend(assessment_format_contract_errors(payload))
    errors.extend(parent_child_candidate_errors(payload))
    for group in payload.get("groups") or []:
        errors.extend(validate_group(group))
    for placement in payload.get("placements") or []:
        errors.extend(validate_placement(placement, profile))
    for identity in duplicate_group_identities(payload.get("groups") or []):
        errors.append(f"duplicate group identity {identity}")
    for group_key in duplicate_group_keys(payload.get("groups") or []):
        errors.append(f"duplicate group_key {group_key!r}")
    for label in duplicate_question_labels(payload.get("candidates") or []):
        errors.append(f"duplicate question_label {label!r}")
    errors.extend(
        primary_placement_errors(payload.get("placements") or []))
    return {
        "errors": errors,
        "hashes": {
            "payload": sha256_json(payload),
            "source_atoms": sha256_json(payload.get("source_atoms") or []),
            "blueprint_cells": sha256_json(
                payload.get("blueprint_cells") or []),
            "candidates": sha256_json(payload.get("candidates") or []),
            "groups": sha256_json(payload.get("groups") or []),
            "placements": sha256_json(payload.get("placements") or []),
        },
    }

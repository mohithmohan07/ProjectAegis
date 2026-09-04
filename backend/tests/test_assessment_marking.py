"""Recorded, semantics-preserving assessment marking verdicts."""
from __future__ import annotations

import copy
import math
from decimal import Decimal

import pytest

from app.services import assessment_marking as marking
from app.services.phase3 import kernel


ENVELOPE_SHA256 = "m" * 64
META = {
    "subject": "Mathematics",
    "board": "State Board",
    "grade": "6",
    "chapter_title": "Shapes",
}
MSBSHSE_META = {
    **META,
    "board": "MSBSHSE",
}
SUBJECTIVE_PROFILE = {
    **marking.assessment_profile.DEFAULT_PROFILE,
    "sheet_kinds": ("objective", "descriptive", "subjective"),
}
# Master Governing Contract v2.0 §21: every True or False item is a
# Subjective row (statement + "Answer: $$a$$", one placeholder-bound
# accepted answer).  Both the generic and the MSBSHSE spellings project the
# same way.
_TRUE_FALSE_CATEGORIES = ("True or False", "True/False")
# Master Governing Contract v2.0 §27.5 (RUB-002): every Descriptive rubric
# criterion carries exactly 0.5 or 1 mark, so a single-part fixture needs
# enough discrete criteria to reach its cell total in those quanta.  Two
# contrasts cover a 1- or 2-mark cell; each further mark adds a criterion.
_DESCRIPTIVE_CRITERIA = (
    ("A square is two-dimensional.", "first contrast"),
    ("A cube is three-dimensional.", "second contrast"),
    ("A square has four equal sides.", "third criterion"),
    ("A cube has six square faces.", "fourth criterion"),
    ("A square has four right angles.", "fifth criterion"),
    ("A cube has twelve equal edges.", "sixth criterion"),
)


def _descriptive_criteria(marks) -> list[dict]:
    """Enough 0.5/1-mark rubric criteria for a single-part cell total."""

    try:
        total = float(marks)
    except (TypeError, ValueError):
        total = float("nan")
    count = max(2, math.ceil(total)) if math.isfinite(total) and total > 0 else 2
    if count > len(_DESCRIPTIVE_CRITERIA):
        raise ValueError(
            f"the descriptive fixture covers at most "
            f"{len(_DESCRIPTIVE_CRITERIA)} marks (asked for {marks!r})"
        )
    return [
        {
            "answer_type": "Phrases",
            "answer_content": content,
            "answer_weightage": "",
            "placeholder": placeholder,
        }
        for content, placeholder in _DESCRIPTIVE_CRITERIA[:count]
    ]


def _descriptive_weights(marks, count: int) -> list[str]:
    """Split ``marks`` over ``count`` criteria using only 1 and 0.5."""

    ones = int(Decimal(str(marks)) * 2) - count
    assert 0 <= ones <= count, (marks, count)
    return ["1" if position < ones else "0.5" for position in range(count)]


def _restriction_audit() -> dict:
    return {
        "answer_restriction": "Open",
        "restriction_reason": "Equivalent correct wording is accepted.",
        "answer_space_contract": "Credit both required geometric contrasts.",
        "required_elements": ["dimensional contrast", "face contrast"],
        "accepted_variations": ["equivalent mathematical wording"],
        "evidence": "The complete rubric accepts equivalent explanations.",
        "rationale": "The response space is not a single literal string.",
        "registry": {"registry_id": "registry-v2.0"},
        "flags": [],
        "authority": {
            "decision_key": "restriction-key",
            "policy_version": "assessment-answer-restriction-2;test",
            "review_flags": [],
        },
    }


def _cell(
    cell_id: str = "CELL-DESC",
    *,
    kind: str = "descriptive",
    marks=4,
    category: str | None = None,
    difficulty: str = "Moderate",
) -> dict:
    default_categories = {
        "objective": "Multiple Choice Question",
        "subjective": "Short Answer",
        "descriptive": "Long Answer",
    }
    return {
        "cell_id": cell_id,
        "sheet_kind": kind,
        "question_category": category or default_categories[kind],
        "cognitive_skill": "Understand",
        "difficulty": difficulty,
        "marks": marks,
        "count": 1,
        "appears_in": ["Pre/Post-Worksheet/Test"],
        "source_policy": "reuse",
    }


def _candidate(
    candidate_id: str = "CAND-DESC",
    *,
    cell_id: str = "CELL-DESC",
    kind: str = "descriptive",
    marks=4,
    category: str | None = None,
    difficulty: str = "Moderate",
    multipart: bool = False,
) -> dict:
    cell = _cell(
        cell_id,
        kind=kind,
        marks=marks,
        category=category,
        difficulty=difficulty,
    )
    if kind == "objective":
        answers = [
            {
                "answer_type": "Phrases",
                "answer_content": "Square",
                "correct_answer": "Yes",
                "answer_weightage": "",
                "answer_display": "Text",
            },
            {
                "answer_type": "Phrases",
                "answer_content": "Cube",
                "correct_answer": "No",
                "answer_weightage": "",
                "answer_display": "Text",
            },
        ]
        sub_questions = []
        question = "Which listed shape is two-dimensional?"
        restriction = "Specific"
    elif (
        kind == "subjective"
        and cell["question_category"] in _TRUE_FALSE_CATEGORIES
    ):
        # Contract v2.0 §21: one accepted answer (the word True or False)
        # bound to placeholder a; internal answer_type Phrases projects to
        # the Subjective wire literal Words.
        answers = [
            {
                "answer_type": "Phrases",
                "answer_content": "True",
                "answer_weightage": "",
                "answer_display": "Text",
                "placeholder": "a",
            },
        ]
        sub_questions = []
        question = "A square is a two-dimensional shape. Answer: $$a$$"
        restriction = "Specific"
    elif kind == "subjective":
        answers = [
            {
                "answer_type": "Phrases",
                "answer_content": "two-dimensional",
                "answer_weightage": "",
                "answer_display": "Text",
                "placeholder": "a",
            },
            {
                "answer_type": "Phrases",
                "answer_content": "three-dimensional",
                "answer_weightage": "",
                "answer_display": "Text",
                "placeholder": "b",
            },
        ]
        sub_questions = []
        question = "Complete: A square is $$a$$; a cube is $$b$$."
        restriction = "Specific"
    elif multipart:
        # Multipart Descriptive rows score only their subquestion rubrics.
        # Keeping answers=[] is the contract under test, not a convenience.
        answers = []
        sub_questions = [
            {
                "text": "State two properties of a square.",
                "marks": "",
                "keywords": [
                    {
                        "answer_type": "Phrases",
                        "keyword": "two-dimensional",
                        "weightage": "",
                    },
                    {
                        "answer_type": "Phrases",
                        "keyword": "four equal sides",
                        "weightage": "",
                    },
                ],
            },
            {
                "text": "State two properties of a cube.",
                "marks": "",
                "keywords": [
                    {
                        "answer_type": "Phrases",
                        "keyword": "six square faces",
                        "weightage": "",
                    },
                    {
                        "answer_type": "Phrases",
                        "keyword": "twelve equal edges",
                        "weightage": "",
                    },
                ],
            },
        ]
        question = "Answer both parts about a square and a cube."
        restriction = "Open"
    else:
        # Contract v2.0 §27.5: enough discrete 0.5/1-mark criteria to reach
        # the cell total (two contrasts for 1-2 marks, one more per mark).
        answers = _descriptive_criteria(marks)
        sub_questions = []
        question = "Explain how a square differs from a cube."
        restriction = "Open"
    audit = _restriction_audit()
    audit["answer_restriction"] = restriction
    return {
        "candidate_id": candidate_id,
        "blueprint_cell_id": cell_id,
        "source_atom_ids": [f"SOURCE-{candidate_id}"],
        "sheet_kind": kind,
        "question_category": cell["question_category"],
        "cognitive_skill": cell["cognitive_skill"],
        "difficulty": cell["difficulty"],
        "marks": marks,
        "question": question,
        "question_text": question,
        "display_answer": "A complete learner-facing answer.",
        "answer_explanation": "Scoring follows the supplied rubric.",
        "answers": answers,
        "sub_questions": sub_questions,
        "answer_restriction": restriction,
        "restriction_reason": audit["restriction_reason"],
        "_aegis_assessment_answer_restriction": audit,
        "source_evidence": "Complete source evidence.",
        "assets": [{"url": "https://example.test/shape.png", "alt": "Shapes"}],
        "question_duration": None,
        "math_keyboard": "",
    }


def _valid_response(request: dict) -> dict:
    candidate = request["candidate"]
    cell = request["blueprint_evidence"]["explicit_blueprint_cell"]
    answers = copy.deepcopy(candidate["answers"])
    sub_questions = copy.deepcopy(candidate["sub_questions"])
    if cell["sheet_kind"] == "objective":
        answers[0]["answer_weightage"] = cell["marks"]
        answers[1]["answer_weightage"] = 0
        duration = 2
        keyboard = ""
    elif cell["sheet_kind"] == "subjective":
        each = str(cell["marks"] / len(answers))
        for answer in answers:
            answer["answer_weightage"] = each
        duration = 2
        keyboard = "No"
    elif sub_questions:
        # This helper's multipart fixture is deliberately a four-mark, two-
        # part item so arithmetic mutations can target each independent sum.
        # Contract v2.0 §27.5: every keyword criterion carries 0.5 or 1.
        sub_questions[0]["marks"] = 2
        sub_questions[1]["marks"] = 2
        sub_questions[0]["keywords"][0]["weightage"] = 1
        sub_questions[0]["keywords"][1]["weightage"] = 1
        sub_questions[1]["keywords"][0]["weightage"] = 1
        sub_questions[1]["keywords"][1]["weightage"] = 1
        duration = 6
        keyboard = "Yes"
    else:
        # Contract v2.0 §27.5: each rubric criterion is exactly 0.5 or 1.
        for answer, weight in zip(
            answers, _descriptive_weights(cell["marks"], len(answers)),
        ):
            answer["answer_weightage"] = weight
        duration = 6
        keyboard = "Yes"
    return {
        "candidate_id": candidate["candidate_id"],
        "question": candidate["question"],
        "question_text": candidate["question_text"],
        "answers": answers,
        "sub_questions": sub_questions,
        "question_duration": duration,
        "duration_basis_count": None,
        "math_keyboard": keyboard,
        "rationale": "The decomposition covers every required scoring unit.",
    }


def _verified(_request: dict) -> dict:
    return {"verdict": "verified", "confidence": 1.0, "issues": []}


def _forbidden(label: str):
    def call(_request: dict) -> dict:
        raise AssertionError(f"cached replay called {label}")

    return call


def test_marking_uses_complete_candidate_cell_and_adopted_contract(
    monkeypatch,
) -> None:
    monkeypatch.setattr(marking.config, "phase3_decision_workers", lambda: 1)
    candidate = _candidate()
    cell = _cell()
    original_candidate = copy.deepcopy(candidate)
    original_cell = copy.deepcopy(cell)
    author_requests = []
    critic_requests = []
    store = kernel.DecisionStore()

    def author(request: dict) -> dict:
        author_requests.append(copy.deepcopy(request))
        return _valid_response(request)

    def critic(request: dict) -> dict:
        critic_requests.append(copy.deepcopy(request))
        return _verified(request)

    verdict = marking.decide_markings(
        [(candidate, cell)],
        meta=META,
        envelope_sha256=ENVELOPE_SHA256,
        provider=author,
        critic=critic,
        store=store,
        fixer=_forbidden("the Fixer"),
    )[0]

    assert candidate == original_candidate
    assert cell == original_cell
    assert len(author_requests) == len(critic_requests) == 1
    payload = author_requests[0]
    assert payload["stage"] == "assessment.marking"
    assert payload["candidate"] == candidate
    assert payload["assessment_format_policy"] == (
        marking.assessment_profile.assessment_format_policy(None, META)
    )
    assert payload["adopted_answer_contract"] == {
        "answer_restriction": "Open",
        "restriction_reason": candidate["restriction_reason"],
        "audit": candidate["_aegis_assessment_answer_restriction"],
    }
    assert payload["blueprint_evidence"] == {
        "total_marks_authority": "explicit_blueprint_cell",
        "explicit_blueprint_cell": cell,
        "decomposition_authority": "api_per_item_verdict",
        "external_marking_rubric": "not_part_of_contract",
        "external_marking_rubric_consulted": False,
        "instruction": (
            "Author the per-item decomposition within the explicit cell's "
            "total. No external marking-rubric document is consulted or "
            "expected."
        ),
    }
    assert "intentionally own the per-item decomposition" in payload["rules"]
    assert "No external marking-rubric document" in payload["rules"]
    assert "intentional decomposition authority" in payload["critic_rules"]
    assert "No external marking-rubric document" in payload["critic_rules"]
    assert "diagram marks explicitly" in payload["rules"]
    assert "Enumerate every represented subquestion" in payload["rules"]
    assert "no marks for redundant steps" in payload["rules"]
    assert critic_requests[0]["proposed_decision"] == _valid_response(payload)

    assert verdict["candidate_id"] == candidate["candidate_id"]
    assert verdict["marks"] == 4.0
    assert verdict["question"] == candidate["question"]
    assert verdict["question_text"] == candidate["question_text"]
    assert verdict["answers"][0]["answer_content"] == (
        candidate["answers"][0]["answer_content"]
    )
    # Contract v2.0 §27.5: four 1-mark criteria, never 1.5 + 2.5.
    assert [row["answer_weightage"] for row in verdict["answers"]] == [
        "1", "1", "1", "1",
    ]
    assert verdict["sub_questions"] == []
    assert verdict["question_duration"] == 6.0
    assert verdict["duration_basis_count"] is None
    assert verdict["math_keyboard"] == "Yes"
    assert verdict["flags"] == []
    assert verdict["blueprint_authority"] == {
        "source": "explicit_blueprint_cell",
        "cell_id": cell["cell_id"],
        "total_marks": 4.0,
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
    authority = verdict["authority"]
    assert authority["policy_version"] == "assessment-marking-8"
    assert "created_at" not in authority and "provider" not in authority
    stored = store.get(authority["decision_key"])
    assert stored is not None
    assert stored["kind"] == "assessment.marking"
    assert stored["unit_id"] == candidate["candidate_id"]


def test_marking_replays_without_author_critic_or_fixer(monkeypatch) -> None:
    monkeypatch.setattr(marking.config, "phase3_decision_workers", lambda: 1)
    pair = (_candidate(), _cell())
    store = kernel.DecisionStore()
    first = marking.decide_markings(
        [pair],
        meta=META,
        envelope_sha256=ENVELOPE_SHA256,
        provider=_valid_response,
        critic=_verified,
        store=store,
        fixer=_forbidden("the Fixer"),
    )
    second = marking.decide_markings(
        [pair],
        meta=META,
        envelope_sha256=ENVELOPE_SHA256,
        provider=_forbidden("the author"),
        critic=_forbidden("the critic"),
        store=store,
        fixer=_forbidden("the Fixer"),
    )

    assert second == first
    assert len(store.keys()) == 1


def test_stale_v7_marking_record_redecides_under_current_policy(monkeypatch) -> None:
    """Contract v2.0 §27.5 (0.5/1 rubric quantum) re-keyed the policy to v8."""
    monkeypatch.setattr(marking.config, "phase3_decision_workers", lambda: 1)
    assert marking.MARKING_POLICY_VERSION == "assessment-marking-8"
    pair = (_candidate(), _cell())
    store = kernel.DecisionStore()
    calls = 0

    def author(request: dict) -> dict:
        nonlocal calls
        calls += 1
        return _valid_response(request)

    monkeypatch.setattr(
        marking, "MARKING_POLICY_VERSION", "assessment-marking-7"
    )
    stale = marking.decide_markings(
        [pair], meta=META, envelope_sha256=ENVELOPE_SHA256,
        provider=author, store=store,
    )[0]
    monkeypatch.setattr(
        marking, "MARKING_POLICY_VERSION", "assessment-marking-8"
    )
    current = marking.decide_markings(
        [pair], meta=META, envelope_sha256=ENVELOPE_SHA256,
        provider=author, store=store,
    )[0]

    assert calls == 2
    assert stale["authority"]["policy_version"] == "assessment-marking-7"
    assert current["authority"]["policy_version"] == "assessment-marking-8"
    assert stale["authority"]["decision_key"] != (
        current["authority"]["decision_key"]
    )
    assert len(store.keys()) == 2


def test_envelope_change_rekeys_the_marking_decision(monkeypatch) -> None:
    monkeypatch.setattr(marking.config, "phase3_decision_workers", lambda: 1)
    pair = (_candidate(), _cell())
    store = kernel.DecisionStore()
    first = marking.decide_markings(
        [pair], meta=META, envelope_sha256="a" * 64,
        provider=_valid_response, store=store,
    )[0]
    second = marking.decide_markings(
        [pair], meta=META, envelope_sha256="b" * 64,
        provider=_valid_response, store=store,
    )[0]

    assert first["authority"]["decision_key"] != second["authority"]["decision_key"]
    assert len(store.keys()) == 2


def test_critic_dissent_is_advisory_and_never_retries_author(monkeypatch) -> None:
    monkeypatch.setattr(marking.config, "phase3_decision_workers", lambda: 1)
    calls = {"author": 0, "critic": 0}

    def author(request: dict) -> dict:
        calls["author"] += 1
        return _valid_response(request)

    def critic(_request: dict) -> dict:
        calls["critic"] += 1
        return {
            "verdict": "dissent",
            "confidence": 1.0,
            "issues": ["The authored duration deserves human review."],
        }

    verdict = marking.decide_markings(
        [(_candidate(), _cell())],
        meta=META,
        envelope_sha256=ENVELOPE_SHA256,
        provider=author,
        critic=critic,
        store=kernel.DecisionStore(),
    )[0]

    assert calls == {"author": 1, "critic": 1}
    assert verdict["question_duration"] == 6.0
    assert any("dissent" in flag for flag in verdict["flags"])
    assert any("duration deserves" in flag for flag in verdict["flags"])


def test_marking_payload_recursively_strips_print_positions(monkeypatch) -> None:
    monkeypatch.setattr(marking.config, "phase3_decision_workers", lambda: 1)
    candidate = _candidate()
    candidate["source_context"] = {
        "semantic": "preserved", "source_page": 18,
    }
    candidate["assets"][0]["bbox"] = [1, 2, 3, 4]
    candidate["answers"][0]["row_number"] = 9
    candidate["_aegis_assessment_answer_restriction"]["source_page"] = 18
    cell = _cell()
    cell["source_order"] = 7
    seen = {}

    def provider(request):
        seen.update(copy.deepcopy(request))
        return _valid_response(request)

    marking.decide_markings(
        [(candidate, cell)],
        meta={**META, "printer_page": 4, "semantic_meta": "preserved"},
        envelope_sha256=ENVELOPE_SHA256,
        provider=provider,
        store=kernel.DecisionStore(),
    )

    assert seen["candidate"]["source_context"] == {"semantic": "preserved"}
    assert "bbox" not in seen["candidate"]["assets"][0]
    assert "row_number" not in seen["candidate"]["answers"][0]
    assert "source_page" not in seen["adopted_answer_contract"]["audit"]
    assert "source_order" not in seen["blueprint_evidence"][
        "explicit_blueprint_cell"
    ]
    assert seen["metadata"]["semantic_meta"] == "preserved"
    assert "printer_page" not in seen["metadata"]


def test_multipart_payload_recursively_strips_subquestion_positions(
    monkeypatch,
) -> None:
    monkeypatch.setattr(marking.config, "phase3_decision_workers", lambda: 1)
    candidate = _candidate(multipart=True)
    candidate["sub_questions"][0]["source_page"] = 18
    candidate["sub_questions"][0]["keywords"][0]["bbox"] = [1, 2, 3, 4]
    seen = {}

    def provider(request):
        seen.update(copy.deepcopy(request))
        return _valid_response(request)

    marking.decide_markings(
        [(candidate, _cell())],
        meta=META,
        envelope_sha256=ENVELOPE_SHA256,
        provider=provider,
        store=kernel.DecisionStore(),
    )

    assert "source_page" not in seen["candidate"]["sub_questions"][0]
    assert "bbox" not in seen["candidate"]["sub_questions"][0][
        "keywords"
    ][0]


@pytest.mark.parametrize(
    "review",
    [
        {},
        {"verdict": "verified", "confidence": "not-a-number", "issues": []},
        {"verdict": "verified", "confidence": True, "issues": []},
        {
            "verdict": "verified", "confidence": 1.0, "issues": [],
            "adjudicator": "required",
        },
        {"verdict": "verified", "confidence": 1.0, "issues": 42},
        {"verdict": "verified", "confidence": 0.5, "issues": []},
    ],
)
def test_malformed_or_subfloor_marking_critic_never_gates_or_looks_clean(
    monkeypatch, review,
) -> None:
    monkeypatch.setattr(marking.config, "phase3_decision_workers", lambda: 1)
    verdict = marking.decide_markings(
        [(_candidate(), _cell())],
        meta=META,
        envelope_sha256=ENVELOPE_SHA256,
        provider=_valid_response,
        critic=lambda _request: copy.deepcopy(review),
        store=kernel.DecisionStore(),
    )[0]

    assert verdict["question_duration"] == 6.0
    assert verdict["flags"]
    assert any("critic" in flag for flag in verdict["flags"])


def test_non_object_marking_response_still_reaches_same_checker_fixer(
    monkeypatch,
) -> None:
    monkeypatch.setattr(marking.config, "phase3_decision_workers", lambda: 1)
    author_calls = []
    fixer_calls = []

    def author(_request):
        author_calls.append(True)
        return [1]

    def fixer(request):
        fixer_calls.append(copy.deepcopy(request))
        return _valid_response(request["original_payload"])

    verdict = marking.decide_markings(
        [(_candidate(), _cell())],
        meta=META,
        envelope_sha256=ENVELOPE_SHA256,
        provider=author,
        fixer=fixer,
        store=kernel.DecisionStore(),
    )[0]

    assert len(author_calls) == kernel.MAX_ATTEMPTS
    assert len(fixer_calls) == 1
    assert fixer_calls[0]["last_response"][
        "_aegis_invalid_response_type"
    ] == "list"
    assert verdict["authority"]["fixer"] is True


@pytest.mark.parametrize(
    ("mode", "mutate"),
    [
        pytest.param(
            "single",
            lambda row: row.__setitem__("question", "Rewritten question"),
            id="question",
        ),
        pytest.param(
            "single",
            lambda row: row.__setitem__("question_text", "Rewritten text"),
            id="question-text",
        ),
        pytest.param(
            "single",
            lambda row: row["answers"][0].__setitem__("answer_type", "Essay"),
            id="answer-type",
        ),
        pytest.param(
            "single",
            lambda row: row["answers"][0].__setitem__(
                "answer_content", "Changed rubric"
            ),
            id="answer-content",
        ),
        pytest.param(
            "single",
            lambda row: row["answers"].pop(),
            id="answer-cardinality",
        ),
        pytest.param(
            "multipart",
            lambda row: row["sub_questions"][0].__setitem__(
                "text", "Changed subquestion"
            ),
            id="subquestion-text",
        ),
        pytest.param(
            "multipart",
            lambda row: row["sub_questions"][0]["keywords"][0].__setitem__(
                "keyword", "changed keyword"
            ),
            id="keyword-text",
        ),
        pytest.param(
            "multipart",
            lambda row: row["sub_questions"][0]["keywords"][0].__setitem__(
                "answer_type", "Essay"
            ),
            id="keyword-type",
        ),
        pytest.param(
            "multipart",
            lambda row: row["sub_questions"][0]["keywords"].pop(),
            id="keyword-cardinality",
        ),
    ],
)
def test_semantic_answer_space_changes_fail_closed(
    monkeypatch, mode, mutate,
) -> None:
    monkeypatch.setattr(marking.config, "phase3_decision_workers", lambda: 1)
    candidate = _candidate(multipart=mode == "multipart")

    def invalid(request: dict) -> dict:
        response = _valid_response(request)
        mutate(response)
        return response

    with pytest.raises(kernel.ContractError) as exc_info:
        marking.decide_markings(
            [(candidate, _cell())],
            meta=META,
            envelope_sha256=ENVELOPE_SHA256,
            provider=invalid,
            store=kernel.DecisionStore(),
        )

    assert any("unchanged" in defect for defect in exc_info.value.defects)


def test_objective_correct_marker_is_semantically_immutable(monkeypatch) -> None:
    monkeypatch.setattr(marking.config, "phase3_decision_workers", lambda: 1)
    candidate = _candidate(
        "CAND-OBJ", cell_id="CELL-OBJ", kind="objective", marks=2,
    )
    cell = _cell("CELL-OBJ", kind="objective", marks=2)

    def invalid(request: dict) -> dict:
        response = _valid_response(request)
        response["answers"][0]["correct_answer"] = "No"
        response["answers"][1]["correct_answer"] = "Yes"
        response["answers"][0]["answer_weightage"] = 0
        response["answers"][1]["answer_weightage"] = 2
        return response

    with pytest.raises(kernel.ContractError) as exc_info:
        marking.decide_markings(
            [(candidate, cell)], meta=META,
            envelope_sha256=ENVELOPE_SHA256,
            provider=invalid, store=kernel.DecisionStore(),
        )
    assert any("non-weight" in defect for defect in exc_info.value.defects)


@pytest.mark.parametrize(
    ("mode", "mutate", "expected"),
    [
        pytest.param(
            "single",
            lambda row: row["answers"][0].__setitem__(
                "answer_weightage", float("nan")
            ),
            "finite and positive",
            id="answer-nan",
        ),
        pytest.param(
            "single",
            lambda row: row["answers"][0].__setitem__(
                "answer_weightage", float("inf")
            ),
            "finite and positive",
            id="answer-infinity",
        ),
        pytest.param(
            "single",
            lambda row: row["answers"][0].__setitem__(
                "answer_weightage", "1e1000000"
            ),
            "finite and positive",
            id="answer-outside-workbook-domain",
        ),
        pytest.param(
            "single",
            lambda row: row["answers"][0].__setitem__(
                "answer_weightage", "+1"
            ),
            "finite and positive",
            id="answer-signed-string",
        ),
        pytest.param(
            "single",
            lambda row: (
                row["answers"][0].__setitem__("answer_weightage", -1),
                row["answers"][1].__setitem__("answer_weightage", 5),
            ),
            "finite and positive",
            id="answer-negative-cancellation",
        ),
        pytest.param(
            "single",
            lambda row: row["answers"][0].__setitem__("answer_weightage", 0),
            "finite and positive",
            id="answer-zero",
        ),
        pytest.param(
            "single",
            lambda row: row["answers"][0].__setitem__("answer_weightage", 0.5),
            "sum exactly",
            id="answer-wrong-sum",
        ),
        # Contract v2.0 §27.5 (RUB-002): a criterion is exactly 0.5 or 1 —
        # a larger award is refused even when the arithmetic still sums.
        pytest.param(
            "single",
            lambda row: (
                row["answers"][0].__setitem__("answer_weightage", 1.5),
                row["answers"][1].__setitem__("answer_weightage", 0.5),
            ),
            "is not 0.5 or 1",
            id="answer-quantum",
        ),
        pytest.param(
            "multipart",
            lambda row: (
                row["sub_questions"][0]["keywords"][0].__setitem__(
                    "weightage", 1.5
                ),
                row["sub_questions"][0]["keywords"][1].__setitem__(
                    "weightage", 0.5
                ),
            ),
            "is not 0.5 or 1",
            id="keyword-quantum",
        ),
        pytest.param(
            "multipart",
            lambda row: row["sub_questions"][0].__setitem__(
                "marks", float("nan")
            ),
            "subquestion 1 marks",
            id="submark-nan",
        ),
        pytest.param(
            "multipart",
            lambda row: (
                row["sub_questions"][0].__setitem__("marks", -1),
                row["sub_questions"][1].__setitem__("marks", 5),
            ),
            "subquestion 1 marks",
            id="submark-negative-cancellation",
        ),
        pytest.param(
            "multipart",
            lambda row: row["sub_questions"][0].__setitem__("marks", 1),
            "subquestion marks must sum exactly",
            id="submark-wrong-sum",
        ),
        pytest.param(
            "multipart",
            lambda row: row["sub_questions"][0]["keywords"][0].__setitem__(
                "weightage", float("nan")
            ),
            "keyword 1 weight",
            id="keyword-nan",
        ),
        pytest.param(
            "multipart",
            lambda row: (
                row["sub_questions"][0]["keywords"][0].__setitem__(
                    "weightage", -1
                ),
                row["sub_questions"][0]["keywords"][1].__setitem__(
                    "weightage", 3
                ),
            ),
            "keyword 1 weight",
            id="keyword-negative-cancellation",
        ),
        pytest.param(
            "multipart",
            lambda row: row["sub_questions"][0]["keywords"][0].__setitem__(
                "weightage", 0.5
            ),
            "keyword weights must sum exactly",
            id="keyword-wrong-sum",
        ),
        pytest.param(
            "single",
            lambda row: row.__setitem__("question_duration", float("nan")),
            "question_duration",
            id="duration-nan",
        ),
        pytest.param(
            "single",
            lambda row: row.__setitem__("question_duration", float("inf")),
            "question_duration",
            id="duration-infinity",
        ),
        pytest.param(
            "single",
            lambda row: row.__setitem__("question_duration", 0),
            "question_duration",
            id="duration-zero",
        ),
        pytest.param(
            "single",
            lambda row: row.__setitem__("question_duration", "1e-10000"),
            "question_duration",
            id="duration-underflow",
        ),
        pytest.param(
            "single",
            lambda row: row.__setitem__("question_duration", "1e1000000"),
            "question_duration",
            id="duration-overflow",
        ),
        pytest.param(
            "single",
            lambda row: row.__setitem__("math_keyboard", ""),
            "descriptive math_keyboard",
            id="descriptive-keyboard-blank",
        ),
    ],
)
def test_invalid_marking_arithmetic_never_ships(
    monkeypatch, mode, mutate, expected: str,
) -> None:
    monkeypatch.setattr(marking.config, "phase3_decision_workers", lambda: 1)
    candidate = _candidate(multipart=mode == "multipart")

    def invalid(request: dict) -> dict:
        response = _valid_response(request)
        mutate(response)
        return response

    with pytest.raises(kernel.ContractError) as exc_info:
        marking.decide_markings(
            [(candidate, _cell())], meta=META,
            envelope_sha256=ENVELOPE_SHA256,
            provider=invalid, store=kernel.DecisionStore(),
        )
    assert any(expected in defect for defect in exc_info.value.defects)


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        pytest.param(
            lambda row: row["answers"][1].__setitem__("answer_weightage", -1),
            "exact zero",
            id="wrong-option-negative",
        ),
        pytest.param(
            lambda row: row["answers"][1].__setitem__("answer_weightage", ""),
            "finite and numeric",
            id="wrong-option-blank",
        ),
        pytest.param(
            lambda row: row["answers"][0].__setitem__("answer_weightage", 1),
            "must equal total marks",
            id="correct-option-underweight",
        ),
        pytest.param(
            lambda row: row.__setitem__("math_keyboard", "No"),
            "exactly blank",
            id="objective-keyboard-nonblank",
        ),
    ],
)
def test_objective_marking_contract_is_exact(monkeypatch, mutate, expected) -> None:
    monkeypatch.setattr(marking.config, "phase3_decision_workers", lambda: 1)
    candidate = _candidate(
        "CAND-OBJ", cell_id="CELL-OBJ", kind="objective", marks=2,
    )
    cell = _cell("CELL-OBJ", kind="objective", marks=2)

    def invalid(request: dict) -> dict:
        response = _valid_response(request)
        mutate(response)
        return response

    with pytest.raises(kernel.ContractError) as exc_info:
        marking.decide_markings(
            [(candidate, cell)], meta=META,
            envelope_sha256=ENVELOPE_SHA256,
            provider=invalid, store=kernel.DecisionStore(),
        )
    assert any(expected in defect for defect in exc_info.value.defects)


def test_fixer_is_revalidated_by_the_same_semantic_and_arithmetic_checker(
    monkeypatch,
) -> None:
    monkeypatch.setattr(marking.config, "phase3_decision_workers", lambda: 1)
    fixer_calls = []

    def invalid_author(request: dict) -> dict:
        response = _valid_response(request)
        response["answers"][0]["answer_content"] = "Changed by author"
        return response

    def invalid_fixer(request: dict) -> dict:
        fixer_calls.append(copy.deepcopy(request))
        response = _valid_response(request["original_payload"])
        response["answers"][0]["answer_content"] = "Changed by Fixer"
        return response

    with pytest.raises(kernel.ContractError, match="Fixer could not"):
        marking.decide_markings(
            [(_candidate(), _cell())], meta=META,
            envelope_sha256=ENVELOPE_SHA256,
            provider=invalid_author,
            fixer=invalid_fixer,
            store=kernel.DecisionStore(),
        )
    assert len(fixer_calls) == kernel.MAX_ATTEMPTS
    assert fixer_calls[0]["contract"] == {
        "kind": "assessment.marking",
        "unit_id": "CAND-DESC",
        "policy_version": "assessment-marking-8",
    }


def test_valid_fixer_verdict_is_recorded_and_flagged(monkeypatch) -> None:
    monkeypatch.setattr(marking.config, "phase3_decision_workers", lambda: 1)

    def invalid_author(request: dict) -> dict:
        response = _valid_response(request)
        response["answers"][0]["answer_weightage"] = -1
        return response

    def fixer(request: dict) -> dict:
        return _valid_response(request["original_payload"])

    verdict = marking.decide_markings(
        [(_candidate(), _cell())], meta=META,
        envelope_sha256=ENVELOPE_SHA256,
        provider=invalid_author,
        critic=_verified,
        fixer=fixer,
        store=kernel.DecisionStore(),
    )[0]

    assert verdict["authority"]["fixer"] is True
    assert any(flag.startswith("fixer:") for flag in verdict["flags"])
    assert verdict["answers"][0]["answer_weightage"] == "1"


def test_parallel_markings_preserve_exact_pair_order(monkeypatch) -> None:
    monkeypatch.setattr(marking.config, "phase3_decision_workers", lambda: 4)
    pairs = [
        (
            _candidate(f"CAND-{n}", cell_id=f"CELL-{n}"),
            _cell(f"CELL-{n}"),
        )
        for n in range(1, 7)
    ]

    verdicts = marking.decide_markings(
        pairs, meta=META, envelope_sha256=ENVELOPE_SHA256,
        provider=_valid_response, store=kernel.DecisionStore(),
    )

    assert [row["candidate_id"] for row in verdicts] == [
        candidate["candidate_id"] for candidate, _cell_row in pairs
    ]


def test_duplicate_candidate_or_explicit_cell_fails_before_spend() -> None:
    first = (_candidate("CAND-1", cell_id="CELL-1"), _cell("CELL-1"))
    duplicate_candidate = (
        _candidate("CAND-1", cell_id="CELL-2"), _cell("CELL-2")
    )
    duplicate_cell = (
        _candidate("CAND-2", cell_id="CELL-1"), _cell("CELL-1")
    )
    whitespace_duplicate_cell = (
        _candidate("CAND-3", cell_id="CELL-1"), _cell(" CELL-1 ")
    )
    with pytest.raises(marking.MarkingError, match="candidate_id"):
        marking.decide_markings(
            [first, duplicate_candidate], meta=META,
            envelope_sha256=ENVELOPE_SHA256,
            provider=_forbidden("the author"),
        )
    with pytest.raises(marking.MarkingError, match="cell_id"):
        marking.decide_markings(
            [first, duplicate_cell], meta=META,
            envelope_sha256=ENVELOPE_SHA256,
            provider=_forbidden("the author"),
        )
    with pytest.raises(marking.MarkingError, match="cell_id"):
        marking.decide_markings(
            [first, whitespace_duplicate_cell], meta=META,
            envelope_sha256=ENVELOPE_SHA256,
            provider=_forbidden("the author"),
        )


def test_missing_adopted_contract_or_cell_owned_marks_fails_before_spend() -> None:
    missing_contract = _candidate()
    missing_contract.pop("_aegis_assessment_answer_restriction")
    with pytest.raises(marking.MarkingError, match="answer-space contract"):
        marking.decide_markings(
            [(missing_contract, _cell())], meta=META,
            envelope_sha256=ENVELOPE_SHA256,
            provider=_forbidden("the author"),
        )

    wrong_marks = _candidate()
    wrong_marks["marks"] = 99
    with pytest.raises(marking.MarkingError, match="marks do not equal"):
        marking.decide_markings(
            [(wrong_marks, _cell())], meta=META,
            envelope_sha256=ENVELOPE_SHA256,
            provider=_forbidden("the author"),
        )

    with pytest.raises(marking.MarkingError, match="finite and positive"):
        marking.decide_markings(
            [(_candidate(marks=float("nan")), _cell(marks=float("nan")))],
            meta=META,
            envelope_sha256=ENVELOPE_SHA256,
            provider=_forbidden("the author"),
        )


def test_invalid_answer_medium_or_single_four_mark_rubric_fails_before_spend():
    mixed = _candidate()
    mixed["answers"][0]["answer_content"] = (
        "A square is [Katex]2[/Katex]-dimensional."
    )
    with pytest.raises(marking.MarkingError, match="declared medium"):
        marking.decide_markings(
            [(mixed, _cell())], meta=META,
            envelope_sha256=ENVELOPE_SHA256,
            provider=_forbidden("the author"),
        )

    single = _candidate()
    single["answers"] = [single["answers"][0]]
    with pytest.raises(marking.MarkingError, match="at least two"):
        marking.decide_markings(
            [(single, _cell())], meta=META,
            envelope_sha256=ENVELOPE_SHA256,
            provider=_forbidden("the author"),
        )


def test_descriptive_main_and_subquestion_rubrics_are_mutually_exclusive():
    mixed = _candidate()
    mixed["sub_questions"] = copy.deepcopy(
        _candidate(multipart=True)["sub_questions"]
    )

    with pytest.raises(marking.MarkingError, match="must not duplicate"):
        marking.decide_markings(
            [(mixed, _cell())],
            meta=META,
            envelope_sha256=ENVELOPE_SHA256,
            provider=_forbidden("the author"),
        )


def test_subjective_marking_scores_answers_without_subquestions(monkeypatch):
    monkeypatch.setattr(marking.config, "phase3_decision_workers", lambda: 1)
    candidate = _candidate(
        "CAND-SUBJ", cell_id="CELL-SUBJ", kind="subjective", marks=2,
    )
    cell = _cell("CELL-SUBJ", kind="subjective", marks=2)

    verdict = marking.decide_markings(
        [(candidate, cell)],
        meta=META,
        envelope_sha256=ENVELOPE_SHA256,
        provider=_valid_response,
        store=kernel.DecisionStore(),
        profile=SUBJECTIVE_PROFILE,
    )[0]

    assert [row["answer_weightage"] for row in verdict["answers"]] == [
        "1.0", "1.0",
    ]
    assert verdict["sub_questions"] == []
    assert verdict["math_keyboard"] == "No"
    assert verdict["question_duration"] == 2.0
    assert verdict["duration_basis_count"] is None


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        pytest.param(
            lambda row: row["answers"][0].__setitem__(
                "answer_weightage", 0
            ),
            "finite and positive",
            id="zero-answer-weight",
        ),
        pytest.param(
            lambda row: row.__setitem__("math_keyboard", ""),
            "subjective math_keyboard",
            id="blank-keyboard",
        ),
        pytest.param(
            lambda row: row["answers"][0].__setitem__(
                "answer_content", "changed"
            ),
            "unchanged",
            id="changed-answer",
        ),
    ],
)
def test_subjective_marking_contract_fails_closed(
    monkeypatch, mutate, expected,
) -> None:
    monkeypatch.setattr(marking.config, "phase3_decision_workers", lambda: 1)
    candidate = _candidate(
        "CAND-SUBJ", cell_id="CELL-SUBJ", kind="subjective", marks=2,
    )
    cell = _cell("CELL-SUBJ", kind="subjective", marks=2)

    def invalid(request: dict) -> dict:
        response = _valid_response(request)
        mutate(response)
        return response

    with pytest.raises(kernel.ContractError) as exc_info:
        marking.decide_markings(
            [(candidate, cell)],
            meta=META,
            envelope_sha256=ENVELOPE_SHA256,
            provider=invalid,
            store=kernel.DecisionStore(),
            profile=SUBJECTIVE_PROFILE,
        )
    assert any(expected in defect for defect in exc_info.value.defects)


def test_subjective_subquestions_fail_before_provider_spend():
    candidate = _candidate(
        "CAND-SUBJ", cell_id="CELL-SUBJ", kind="subjective", marks=2,
    )
    candidate["sub_questions"] = copy.deepcopy(
        _candidate(multipart=True)["sub_questions"]
    )

    with pytest.raises(marking.MarkingError, match="cannot carry subquestions"):
        marking.decide_markings(
            [(candidate, _cell("CELL-SUBJ", kind="subjective", marks=2))],
            meta=META,
            envelope_sha256=ENVELOPE_SHA256,
            provider=_forbidden("the author"),
            profile=SUBJECTIVE_PROFILE,
        )


MATRIX_DURATION_CASES = [
    ("objective", "Multiple Choice Question", 1, difficulty, 1)
    for difficulty in ("Less", "Moderate", "High")
] + [
    ("descriptive", category, marks, difficulty, minutes)
    for category, marks, durations in (
        ("Very Short Answer Questions", 1, (1, 1, 2)),
        ("Short Answer Type (2 Marks)", 2, (2, 2, 3)),
        ("Short Answer Type (3 Marks)", 3, (4, 5, 6)),
        ("Long Answer Type (4 Marks)", 4, (5, 6, 7)),
        ("Long Answer Type (5 Marks)", 5, (5, 7, 7)),
    )
    for difficulty, minutes in zip(("Less", "Moderate", "High"), durations)
]


@pytest.mark.parametrize(
    ("kind", "category", "marks", "difficulty", "minutes"),
    MATRIX_DURATION_CASES,
)
def test_msbshse_class_6_math_matrix_duration_is_exact(
    monkeypatch, kind, category, marks, difficulty, minutes,
) -> None:
    monkeypatch.setattr(marking.config, "phase3_decision_workers", lambda: 1)
    candidate = _candidate(
        "CAND-MATRIX",
        cell_id="CELL-MATRIX",
        kind=kind,
        marks=marks,
        category=category,
        difficulty=difficulty,
    )
    cell = _cell(
        "CELL-MATRIX",
        kind=kind,
        marks=marks,
        category=category,
        difficulty=difficulty,
    )
    seen = {}

    def provider(request: dict) -> dict:
        seen.update(copy.deepcopy(request))
        response = _valid_response(request)
        response["question_duration"] = minutes
        response["duration_basis_count"] = None
        return response

    verdict = marking.decide_markings(
        [(candidate, cell)],
        meta=MSBSHSE_META,
        envelope_sha256=ENVELOPE_SHA256,
        provider=provider,
        store=kernel.DecisionStore(),
    )[0]

    policy = seen["assessment_format_policy"]
    assert policy["policy_id"] == "msbshse-grade-6-mathematics-2026-08-27"
    assert policy["formats_by_sheet"][kind][category]["duration"] == {
        "mode": "matrix",
        "minutes_by_difficulty": {
            row_difficulty: expected
            for row_difficulty, expected in zip(
                ("Less", "Moderate", "High"),
                next(
                    values
                    for row_category, _row_marks, values in (
                        ("Multiple Choice Question", 1, (1, 1, 1)),
                        ("Very Short Answer Questions", 1, (1, 1, 2)),
                        ("Short Answer Type (2 Marks)", 2, (2, 2, 3)),
                        ("Short Answer Type (3 Marks)", 3, (4, 5, 6)),
                        ("Long Answer Type (4 Marks)", 4, (5, 6, 7)),
                        ("Long Answer Type (5 Marks)", 5, (5, 7, 7)),
                    )
                    if row_category == category
                ),
            )
        },
    }
    assert verdict["question_duration"] == float(minutes)
    assert verdict["duration_basis_count"] is None


def test_msbshse_matrix_duration_rejects_a_positive_but_wrong_value(
    monkeypatch,
) -> None:
    monkeypatch.setattr(marking.config, "phase3_decision_workers", lambda: 1)
    candidate = _candidate(
        "CAND-MATRIX",
        cell_id="CELL-MATRIX",
        marks=2,
        category="Short Answer Type (2 Marks)",
        difficulty="High",
    )
    cell = _cell(
        "CELL-MATRIX",
        marks=2,
        category="Short Answer Type (2 Marks)",
        difficulty="High",
    )

    def wrong_duration(request: dict) -> dict:
        response = _valid_response(request)
        response["question_duration"] = 2
        return response

    with pytest.raises(kernel.ContractError) as exc_info:
        marking.decide_markings(
            [(candidate, cell)],
            meta=MSBSHSE_META,
            envelope_sha256=ENVELOPE_SHA256,
            provider=wrong_duration,
            store=kernel.DecisionStore(),
        )
    assert any(
        "active profile contract (3 minutes)" in defect
        for defect in exc_info.value.defects
    )


@pytest.mark.parametrize(
    ("kind", "category", "marks", "basis_count"),
    [
        # The Objective wire has one correct-option score, so compound
        # source tasks must be split into one-subpoint cells.
        ("objective", "Match the Following", 1, 1),
        ("objective", "Fill in the blanks", 1, 1),
        # Contract v2.0 §21: True or False is a Subjective row with one
        # placeholder-bound answer, so it is likewise a one-subpoint cell.
        ("subjective", "True or False", 1, 1),
        # The Subjective fixture contains two declared response slots.
        ("subjective", "Fill in the blanks", 2, 2),
    ],
)
def test_msbshse_per_subpoint_duration_uses_contract_bound_basis(
    monkeypatch, kind, category, marks, basis_count,
) -> None:
    monkeypatch.setattr(marking.config, "phase3_decision_workers", lambda: 1)
    candidate = _candidate(
        "CAND-SUBPOINT",
        cell_id="CELL-SUBPOINT",
        kind=kind,
        marks=marks,
        category=category,
    )
    cell = _cell(
        "CELL-SUBPOINT", kind=kind, marks=marks, category=category,
    )

    def provider(request: dict) -> dict:
        response = _valid_response(request)
        response["question_duration"] = basis_count
        response["duration_basis_count"] = basis_count
        return response

    verdict = marking.decide_markings(
        [(candidate, cell)],
        meta=MSBSHSE_META,
        envelope_sha256=ENVELOPE_SHA256,
        provider=provider,
        store=kernel.DecisionStore(),
    )[0]

    assert verdict["question_duration"] == float(basis_count)
    assert verdict["duration_basis_count"] == basis_count


@pytest.mark.parametrize(
    ("kind", "category"),
    [
        ("objective", "Match the Following"),
        ("objective", "Fill in the blanks"),
        # Contract v2.0 §21: True or False lives on the Subjective sheet.
        ("subjective", "True or False"),
    ],
)
def test_msbshse_compound_subpoints_fail_before_provider(
    kind, category,
) -> None:
    candidate = _candidate(
        "CAND-COMPOUND",
        cell_id="CELL-COMPOUND",
        kind=kind,
        marks=2,
        category=category,
    )
    cell = _cell(
        "CELL-COMPOUND", kind=kind, marks=2, category=category,
    )

    with pytest.raises(marking.MarkingError, match="at most 1"):
        marking.decide_markings(
            [(candidate, cell)],
            meta=MSBSHSE_META,
            envelope_sha256=ENVELOPE_SHA256,
            provider=_forbidden("the author"),
        )


@pytest.mark.parametrize(
    "malformed_maximum",
    [
        pytest.param("one", id="nonnumeric"),
        pytest.param(True, id="bool"),
        pytest.param(0, id="zero"),
        pytest.param(-1, id="negative"),
        pytest.param(1.5, id="fractional"),
    ],
)
def test_marking_rejects_malformed_max_subpoints_before_provider_spend(
    malformed_maximum,
) -> None:
    custom_profile = copy.deepcopy(marking.assessment_profile.DEFAULT_PROFILE)
    custom_profile.update({
        "sheet_kinds": ("objective",),
        "assessment_format": {
            "policy_id": "malformed-maximum-test",
            "formats_by_sheet": {
                "objective": {
                    "Match the Following": {
                        "marks": {
                            "mode": "per_subpoint",
                            "marks_per_subpoint": 1,
                            "max_subpoints": malformed_maximum,
                        },
                    },
                },
            },
        },
        "assessment_format_overrides": (),
        "run_profile_overrides": (),
    })
    candidate = _candidate(
        "CAND-BAD-MAX",
        cell_id="CELL-BAD-MAX",
        kind="objective",
        marks=1,
        category="Match the Following",
    )
    cell = _cell(
        "CELL-BAD-MAX",
        kind="objective",
        marks=1,
        category="Match the Following",
    )

    with pytest.raises(
        marking.MarkingError,
        match="invalid max_subpoints; it must be a positive integer",
    ):
        marking.decide_markings(
            [(candidate, cell)],
            meta=META,
            envelope_sha256=ENVELOPE_SHA256,
            provider=_forbidden("the author"),
            profile=custom_profile,
        )


def test_msbshse_subjective_each_answer_uses_the_policy_mark_unit(
    monkeypatch,
) -> None:
    monkeypatch.setattr(marking.config, "phase3_decision_workers", lambda: 1)
    candidate = _candidate(
        "CAND-UNIT",
        cell_id="CELL-UNIT",
        kind="subjective",
        marks=2,
        category="Fill in the blanks",
    )
    cell = _cell(
        "CELL-UNIT",
        kind="subjective",
        marks=2,
        category="Fill in the blanks",
    )

    def uneven(request: dict) -> dict:
        response = _valid_response(request)
        response["answers"][0]["answer_weightage"] = 0.5
        response["answers"][1]["answer_weightage"] = 1.5
        response["question_duration"] = 2
        response["duration_basis_count"] = 2
        return response

    with pytest.raises(kernel.ContractError) as exc_info:
        marking.decide_markings(
            [(candidate, cell)],
            meta=MSBSHSE_META,
            envelope_sha256=ENVELOPE_SHA256,
            provider=uneven,
            store=kernel.DecisionStore(),
        )
    assert any(
        "marks-per-subpoint unit 1" in defect
        for defect in exc_info.value.defects
    )


@pytest.mark.parametrize(
    ("duration", "basis_count", "expected"),
    [
        (3, None, "requires duration_basis_count"),
        (3, 2.5, "positive integer"),
        (3, 2, "whole-subpoint count (3)"),
    ],
)
def test_msbshse_per_subpoint_duration_fails_closed(
    monkeypatch, duration, basis_count, expected,
) -> None:
    monkeypatch.setattr(marking.config, "phase3_decision_workers", lambda: 1)
    candidate = _candidate(
        "CAND-SUBPOINT",
        cell_id="CELL-SUBPOINT",
        kind="subjective",
        marks=3,
        category="Fill in the blanks",
    )
    cell = _cell(
        "CELL-SUBPOINT",
        kind="subjective",
        marks=3,
        category="Fill in the blanks",
    )

    def invalid(request: dict) -> dict:
        response = _valid_response(request)
        response["question_duration"] = duration
        response["duration_basis_count"] = basis_count
        return response

    with pytest.raises(kernel.ContractError) as exc_info:
        marking.decide_markings(
            [(candidate, cell)],
            meta=MSBSHSE_META,
            envelope_sha256=ENVELOPE_SHA256,
            provider=invalid,
            store=kernel.DecisionStore(),
        )
    assert any(expected in defect for defect in exc_info.value.defects)


def test_generic_profile_keeps_model_authored_duration_without_basis(
    monkeypatch,
) -> None:
    monkeypatch.setattr(marking.config, "phase3_decision_workers", lambda: 1)
    seen = {}

    def provider(request: dict) -> dict:
        seen.update(copy.deepcopy(request))
        response = _valid_response(request)
        response["question_duration"] = 9
        response["duration_basis_count"] = None
        return response

    verdict = marking.decide_markings(
        [(_candidate(), _cell())],
        meta=META,
        envelope_sha256=ENVELOPE_SHA256,
        provider=provider,
        store=kernel.DecisionStore(),
    )[0]

    assert seen["assessment_format_policy"]["policy_id"] == "generic-cms"
    assert verdict["question_duration"] == 9.0
    assert verdict["duration_basis_count"] is None


def test_generic_profile_rejects_an_invented_duration_basis(monkeypatch):
    monkeypatch.setattr(marking.config, "phase3_decision_workers", lambda: 1)

    def invalid(request: dict) -> dict:
        response = _valid_response(request)
        response["duration_basis_count"] = 1
        return response

    with pytest.raises(kernel.ContractError) as exc_info:
        marking.decide_markings(
            [(_candidate(), _cell())],
            meta=META,
            envelope_sha256=ENVELOPE_SHA256,
            provider=invalid,
            store=kernel.DecisionStore(),
        )
    assert any(
        "must be null when no per-subpoint" in defect
        for defect in exc_info.value.defects
    )

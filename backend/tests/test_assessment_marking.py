"""Recorded, semantics-preserving assessment marking verdicts."""
from __future__ import annotations

import copy
import hashlib

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
    cell_id: str = "CELL-DESC", *, kind: str = "descriptive", marks=4,
) -> dict:
    return {
        "cell_id": cell_id,
        "sheet_kind": kind,
        "question_category": (
            "Multiple Choice Question" if kind == "objective" else "Long Answer"
        ),
        "cognitive_skill": "Understand",
        "difficulty": "Moderate",
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
) -> dict:
    cell = _cell(cell_id, kind=kind, marks=marks)
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
    else:
        answers = [
            {
                "answer_type": "Phrases",
                "answer_content": "A square is two-dimensional.",
                "answer_weightage": "",
                "placeholder": "first contrast",
            },
            {
                "answer_type": "Phrases",
                "answer_content": "A cube is three-dimensional.",
                "answer_weightage": "",
                "placeholder": "second contrast",
            },
        ]
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
                "text": "State one property of a cube.",
                "marks": "",
                "keywords": [
                    {
                        "answer_type": "Phrases",
                        "keyword": "six square faces",
                        "weightage": "",
                    }
                ],
            },
        ]
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
    if cell["sheet_kind"] == "objective":
        answer_weightages = [cell["marks"], 0]
        subquestion_markings = []
        duration = 2
        keyboard = ""
    else:
        answer_weightages = ["1.5", "2.5"]
        subquestion_markings = [
            {"marks": 2, "keyword_weightages": [1, 1]},
            {"marks": 2, "keyword_weightages": [2]},
        ]
        duration = 6
        keyboard = "Yes"
    return {
        "candidate_id": candidate["candidate_id"],
        "answer_weightages": answer_weightages,
        "subquestion_markings": subquestion_markings,
        "question_duration": duration,
        "math_keyboard": keyboard,
        "rationale": "The decomposition covers every required scoring unit.",
    }


def _legacy_valid_response(request: dict) -> dict:
    candidate = request["candidate"]
    overlay = _valid_response(request)
    answers = copy.deepcopy(candidate["answers"])
    for answer, weightage in zip(
        answers, overlay["answer_weightages"], strict=True,
    ):
        answer["answer_weightage"] = weightage
    subquestions = copy.deepcopy(candidate["sub_questions"])
    for subquestion, allocation in zip(
        subquestions, overlay["subquestion_markings"], strict=True,
    ):
        subquestion["marks"] = allocation["marks"]
        for keyword, weightage in zip(
            subquestion["keywords"],
            allocation["keyword_weightages"],
            strict=True,
        ):
            keyword["weightage"] = weightage
    return {
        "candidate_id": candidate["candidate_id"],
        "question": candidate["question"],
        "question_text": candidate["question_text"],
        "answers": answers,
        "sub_questions": subquestions,
        "question_duration": overlay["question_duration"],
        "math_keyboard": overlay["math_keyboard"],
        "rationale": overlay["rationale"],
    }


def _store_legacy_v6_decision(
    store: kernel.DecisionStore, candidate: dict, cell: dict,
) -> str:
    contract = marking._adopted_contract(candidate, candidate["candidate_id"])
    payload = marking._legacy_v6_payload(
        candidate, cell, contract, meta=META,
    )
    key = kernel.decision_key(
        kind="assessment.marking",
        unit_id=candidate["candidate_id"],
        envelope_sha256=ENVELOPE_SHA256,
        payload=payload,
        policy_version="assessment-marking-6",
    )
    store.put(key, {
        "key": key,
        "kind": "assessment.marking",
        "unit_id": candidate["candidate_id"],
        "envelope_sha256": ENVELOPE_SHA256,
        "policy_version": "assessment-marking-6",
        "response": _legacy_valid_response(payload),
        "review_flags": [],
        "provider": "",
        "created_at": 1.0,
    })
    return key


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
    assert payload["response_contract"] == {
        "candidate_id": candidate["candidate_id"],
        "answer_weightages_length": 2,
        "subquestion_markings_length": 2,
        "keyword_weightages_lengths": [2, 1],
    }
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
    assert "working, diagram" in payload["rules"]
    assert "Echo its `candidate_id` exactly" in payload["rules"]
    assert "never add a placeholder row" in payload["rules"]
    assert "do not double-count redundant work" in payload["rules"]
    assert critic_requests[0]["proposed_decision"] == _valid_response(payload)

    assert verdict["candidate_id"] == candidate["candidate_id"]
    assert verdict["marks"] == 4.0
    assert verdict["question"] == candidate["question"]
    assert verdict["question_text"] == candidate["question_text"]
    assert verdict["answers"][0]["answer_content"] == (
        candidate["answers"][0]["answer_content"]
    )
    assert verdict["answers"][0]["answer_weightage"] == "1.5"
    assert verdict["sub_questions"][0]["keywords"][0]["weightage"] == 1
    assert verdict["question_duration"] == 6.0
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
    assert authority["policy_version"] == "assessment-marking-7"
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


def test_v6_paid_hit_replays_while_only_true_miss_uses_v7(monkeypatch) -> None:
    monkeypatch.setattr(marking.config, "phase3_decision_workers", lambda: 1)
    legacy_candidate = _candidate("CAND-OLD", cell_id="CELL-OLD")
    legacy_cell = _cell("CELL-OLD")
    missing_candidate = _candidate("CAND-MISS", cell_id="CELL-MISS")
    missing_cell = _cell("CELL-MISS")
    store = kernel.DecisionStore()
    legacy_key = _store_legacy_v6_decision(
        store, legacy_candidate, legacy_cell,
    )
    authored_ids: list[str] = []

    def author(request: dict) -> dict:
        authored_ids.append(request["candidate"]["candidate_id"])
        return _valid_response(request)

    first = marking.decide_markings(
        [
            (legacy_candidate, legacy_cell),
            (missing_candidate, missing_cell),
        ],
        meta=META,
        envelope_sha256=ENVELOPE_SHA256,
        provider=author,
        store=store,
        fixer=_forbidden("the Fixer"),
    )

    assert authored_ids == ["CAND-MISS"]
    assert first[0]["authority"] == {
        "decision_key": legacy_key,
        "policy_version": "assessment-marking-6",
        "review_flags": [],
        "fixer": False,
    }
    assert first[1]["authority"]["policy_version"] == "assessment-marking-7"
    assert len(store.keys()) == 2

    second = marking.decide_markings(
        [
            (legacy_candidate, legacy_cell),
            (missing_candidate, missing_cell),
        ],
        meta=META,
        envelope_sha256=ENVELOPE_SHA256,
        provider=_forbidden("the author"),
        critic=_forbidden("the critic"),
        fixer=_forbidden("the Fixer"),
        store=store,
    )

    assert second == first
    assert len(store.keys()) == 2


@pytest.mark.parametrize(
    "corrupt",
    [
        pytest.param(
            lambda response: response["answers"][0].__setitem__(
                "answer_content", "Drifted protected rubric content"
            ),
            id="protected-content",
        ),
        pytest.param(
            lambda response: response["answers"][0].__setitem__(
                "answer_weightage", -1
            ),
            id="invalid-arithmetic",
        ),
    ],
)
def test_invalid_v6_record_falls_through_to_one_v7_call(
    monkeypatch, corrupt,
) -> None:
    monkeypatch.setattr(marking.config, "phase3_decision_workers", lambda: 1)
    candidate = _candidate()
    cell = _cell()
    source_store = kernel.DecisionStore()
    legacy_key = _store_legacy_v6_decision(source_store, candidate, cell)
    legacy_record = source_store.get(legacy_key)
    assert legacy_record is not None
    corrupt(legacy_record["response"])
    store = kernel.DecisionStore()
    store.put(legacy_key, legacy_record)
    calls = 0

    def author(request: dict) -> dict:
        nonlocal calls
        calls += 1
        return _valid_response(request)

    recovered = marking.decide_markings(
        [(candidate, cell)],
        meta=META,
        envelope_sha256=ENVELOPE_SHA256,
        provider=author,
        store=store,
    )[0]

    assert calls == 1
    assert recovered["authority"]["policy_version"] == "assessment-marking-7"
    assert recovered["authority"]["decision_key"] != legacy_key
    assert len(store.keys()) == 2

    replayed = marking.decide_markings(
        [(candidate, cell)],
        meta=META,
        envelope_sha256=ENVELOPE_SHA256,
        provider=_forbidden("the author"),
        critic=_forbidden("the critic"),
        fixer=_forbidden("the Fixer"),
        store=store,
    )[0]
    assert replayed == recovered


def test_v6_replay_prompt_bytes_are_frozen() -> None:
    assert hashlib.sha256(
        marking._LEGACY_MARKING_SYSTEM_V6.encode("utf-8")
    ).hexdigest() == (
        "46e331b4309fbf3226115722b8b8a45b7114fe7202a96a1de24a4e5fe2da25ca"
    )
    assert hashlib.sha256(
        marking._LEGACY_MARKING_CRITIC_SYSTEM_V6.encode("utf-8")
    ).hexdigest() == (
        "f96876e28169ea8a706764b745a06f0c054416d82665c2c896773fb6d094848b"
    )
    candidate = _candidate()
    cell = _cell()
    payload = marking._legacy_v6_payload(
        candidate,
        cell,
        marking._adopted_contract(candidate, candidate["candidate_id"]),
        meta=META,
    )
    assert set(payload) == {
        "stage",
        "rules",
        "critic_rules",
        "metadata",
        "candidate",
        "adopted_answer_contract",
        "blueprint_evidence",
    }
    assert "response_contract" not in payload
    assert kernel.decision_key(
        kind="assessment.marking",
        unit_id=candidate["candidate_id"],
        envelope_sha256=ENVELOPE_SHA256,
        payload=payload,
        policy_version="assessment-marking-6",
    ) == "3ee97152df95b3a06a84a1b4d17cd942301db7b47a28f26b8cfbf223ab4bc32e"


def test_stale_v2_marking_record_redecides_under_current_policy(monkeypatch) -> None:
    monkeypatch.setattr(marking.config, "phase3_decision_workers", lambda: 1)
    assert marking.MARKING_POLICY_VERSION == "assessment-marking-7"
    pair = (_candidate(), _cell())
    store = kernel.DecisionStore()
    calls = 0

    def author(request: dict) -> dict:
        nonlocal calls
        calls += 1
        return _valid_response(request)

    monkeypatch.setattr(
        marking, "MARKING_POLICY_VERSION", "assessment-marking-2"
    )
    stale = marking.decide_markings(
        [pair], meta=META, envelope_sha256=ENVELOPE_SHA256,
        provider=author, store=store,
    )[0]
    monkeypatch.setattr(
        marking, "MARKING_POLICY_VERSION", "assessment-marking-7"
    )
    current = marking.decide_markings(
        [pair], meta=META, envelope_sha256=ENVELOPE_SHA256,
        provider=author, store=store,
    )[0]

    assert calls == 2
    assert stale["authority"]["policy_version"] == "assessment-marking-2"
    assert current["authority"]["policy_version"] == "assessment-marking-7"
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
    candidate["sub_questions"][0]["source_page"] = 18
    candidate["sub_questions"][0]["keywords"][0]["bbox"] = [1, 2, 3, 4]
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
    assert "source_page" not in seen["candidate"]["sub_questions"][0]
    assert "bbox" not in seen["candidate"]["sub_questions"][0][
        "keywords"
    ][0]
    assert "source_page" not in seen["adopted_answer_contract"]["audit"]
    assert "source_order" not in seen["blueprint_evidence"][
        "explicit_blueprint_cell"
    ]
    assert seen["metadata"]["semantic_meta"] == "preserved"
    assert "printer_page" not in seen["metadata"]


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


def test_failed_marking_is_not_cached_and_next_v7_retry_can_succeed(
    monkeypatch,
) -> None:
    monkeypatch.setattr(marking.config, "phase3_decision_workers", lambda: 1)
    pair = (_candidate(), _cell())
    store = kernel.DecisionStore()

    def invalid(request: dict) -> dict:
        response = _valid_response(request)
        response["question"] = "A protected field the sparse contract forbids"
        return response

    with pytest.raises(kernel.ContractError):
        marking.decide_markings(
            [pair],
            meta=META,
            envelope_sha256=ENVELOPE_SHA256,
            provider=invalid,
            store=store,
        )
    assert store.keys() == []

    recovered = marking.decide_markings(
        [pair],
        meta=META,
        envelope_sha256=ENVELOPE_SHA256,
        provider=_valid_response,
        store=store,
    )
    assert len(store.keys()) == 1
    assert recovered[0]["authority"]["policy_version"] == (
        "assessment-marking-7"
    )

    replayed = marking.decide_markings(
        [pair],
        meta=META,
        envelope_sha256=ENVELOPE_SHA256,
        provider=_forbidden("the author"),
        critic=_forbidden("the critic"),
        fixer=_forbidden("the Fixer"),
        store=store,
    )
    assert replayed == recovered


def test_sparse_marking_supports_a_widened_subjective_profile(monkeypatch) -> None:
    monkeypatch.setattr(marking.config, "phase3_decision_workers", lambda: 1)
    candidate = _candidate(
        "CAND-SUBJ", cell_id="CELL-SUBJ", kind="subjective", marks=4,
    )
    cell = _cell("CELL-SUBJ", kind="subjective", marks=4)

    verdict = marking.decide_markings(
        [(candidate, cell)],
        meta=META,
        envelope_sha256=ENVELOPE_SHA256,
        provider=_valid_response,
        store=kernel.DecisionStore(),
        profile={
            "name": "widened-test",
            "sheet_kinds": ("objective", "descriptive", "subjective"),
        },
    )[0]

    assert verdict["answers"][0]["answer_weightage"] == "1.5"
    assert verdict["sub_questions"][0]["marks"] == 2
    assert verdict["sub_questions"][0]["keywords"][0]["weightage"] == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param("question", "Rewritten question", id="question"),
        pytest.param("question_text", "Rewritten text", id="question-text"),
        pytest.param(
            "answers",
            [{"answer_content": "Changed rubric"}],
            id="answers",
        ),
        pytest.param(
            "sub_questions",
            [{"text": "Changed subquestion"}],
            id="subquestions",
        ),
    ],
)
def test_sparse_marking_response_rejects_protected_content_fields(
    monkeypatch, field, value,
) -> None:
    monkeypatch.setattr(marking.config, "phase3_decision_workers", lambda: 1)

    def invalid(request: dict) -> dict:
        response = _valid_response(request)
        response[field] = copy.deepcopy(value)
        return response

    with pytest.raises(kernel.ContractError) as exc_info:
        marking.decide_markings(
            [(_candidate(), _cell())],
            meta=META,
            envelope_sha256=ENVELOPE_SHA256,
            provider=invalid,
            store=kernel.DecisionStore(),
        )

    assert any("unexpected fields" in defect for defect in exc_info.value.defects)


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        pytest.param(
            lambda row: row["answer_weightages"].pop(),
            "answer_weightages must cover",
            id="missing-answer-weight",
        ),
        pytest.param(
            lambda row: row["answer_weightages"].append(1),
            "answer_weightages must cover",
            id="extra-answer-weight",
        ),
        pytest.param(
            lambda row: row["subquestion_markings"].pop(),
            "subquestion_markings must cover",
            id="missing-subquestion-marking",
        ),
        pytest.param(
            lambda row: row["subquestion_markings"][0][
                "keyword_weightages"
            ].pop(),
            "keyword_weightages must cover",
            id="missing-keyword-weight",
        ),
        pytest.param(
            lambda row: row["subquestion_markings"][0].__setitem__(
                "rubric", "protected"
            ),
            "unexpected fields",
            id="unexpected-nested-field",
        ),
    ],
)
def test_sparse_marking_requires_exact_ordered_coverage(
    monkeypatch, mutate, expected,
) -> None:
    monkeypatch.setattr(marking.config, "phase3_decision_workers", lambda: 1)

    def invalid(request: dict) -> dict:
        response = _valid_response(request)
        mutate(response)
        return response

    with pytest.raises(kernel.ContractError) as exc_info:
        marking.decide_markings(
            [(_candidate(), _cell())],
            meta=META,
            envelope_sha256=ENVELOPE_SHA256,
            provider=invalid,
            store=kernel.DecisionStore(),
        )

    assert any(expected in defect for defect in exc_info.value.defects)


def test_sparse_projection_preserves_production_shaped_content(monkeypatch) -> None:
    monkeypatch.setattr(marking.config, "phase3_decision_workers", lambda: 1)
    candidate = _candidate(
        "CAND-OBJ", cell_id="CELL-OBJ", kind="objective", marks=2,
    )
    cell = _cell("CELL-OBJ", kind="objective", marks=2)
    candidate["question"] = candidate["question_text"] = (
        "Which expression equals three quarters?"
    )
    candidate["answers"] = [
        {
            "answer_type": "Equation",
            "answer_content": r"\frac{3}{4}",
            "correct_answer": "1",
            "answer_weightage": "",
            "answer_display": "Text",
            "review_note": "Preserve π, Unicode, and https://example.test/a",
            "row_number": 9,
        },
        {
            "answer_type": "Equation",
            "answer_content": r"\frac{4}{3}",
            "correct_answer": "0",
            "answer_weightage": "",
            "answer_display": "Text",
        },
    ]
    original = copy.deepcopy(candidate)

    verdict = marking.decide_markings(
        [(candidate, cell)], meta=META,
        envelope_sha256=ENVELOPE_SHA256,
        provider=_valid_response, store=kernel.DecisionStore(),
    )[0]

    assert candidate == original
    assert verdict["question"] == original["question"]
    assert verdict["question_text"] == original["question_text"]
    assert verdict["answers"][0]["answer_content"] == r"\frac{3}{4}"
    assert verdict["answers"][0]["correct_answer"] == "1"
    assert verdict["answers"][0]["review_note"] == (
        "Preserve π, Unicode, and https://example.test/a"
    )
    assert "row_number" not in verdict["answers"][0]
    assert [row["answer_weightage"] for row in verdict["answers"]] == [2, 0]


def test_objective_weights_use_immutable_production_correct_markers(
    monkeypatch,
) -> None:
    monkeypatch.setattr(marking.config, "phase3_decision_workers", lambda: 1)
    candidate = _candidate(
        "CAND-OBJ", cell_id="CELL-OBJ", kind="objective", marks=2,
    )
    cell = _cell("CELL-OBJ", kind="objective", marks=2)
    candidate["answers"][0]["correct_answer"] = "1"
    candidate["answers"][1]["correct_answer"] = "0"

    def invalid(request: dict) -> dict:
        response = _valid_response(request)
        response["answer_weightages"] = [0, 2]
        return response

    with pytest.raises(kernel.ContractError) as exc_info:
        marking.decide_markings(
            [(candidate, cell)], meta=META,
            envelope_sha256=ENVELOPE_SHA256,
            provider=invalid, store=kernel.DecisionStore(),
        )
    assert any("correct option 1" in defect for defect in exc_info.value.defects)


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        pytest.param(
            lambda row: row["answer_weightages"].__setitem__(0, float("nan")),
            "finite and positive",
            id="answer-nan",
        ),
        pytest.param(
            lambda row: row["answer_weightages"].__setitem__(0, float("inf")),
            "finite and positive",
            id="answer-infinity",
        ),
        pytest.param(
            lambda row: row["answer_weightages"].__setitem__(0, "1e1000000"),
            "finite and positive",
            id="answer-outside-workbook-domain",
        ),
        pytest.param(
            lambda row: row["answer_weightages"].__setitem__(0, "+1.5"),
            "finite and positive",
            id="answer-signed-string",
        ),
        pytest.param(
            lambda row: (
                row["answer_weightages"].__setitem__(0, -1),
                row["answer_weightages"].__setitem__(1, 5),
            ),
            "finite and positive",
            id="answer-negative-cancellation",
        ),
        pytest.param(
            lambda row: row["answer_weightages"].__setitem__(0, 0),
            "finite and positive",
            id="answer-zero",
        ),
        pytest.param(
            lambda row: row["answer_weightages"].__setitem__(0, 1),
            "sum exactly",
            id="answer-wrong-sum",
        ),
        pytest.param(
            lambda row: row["subquestion_markings"][0].__setitem__(
                "marks", float("nan")
            ),
            "entry 1 marks",
            id="submark-nan",
        ),
        pytest.param(
            lambda row: (
                row["subquestion_markings"][0].__setitem__("marks", -1),
                row["subquestion_markings"][1].__setitem__("marks", 5),
            ),
            "entry 1 marks",
            id="submark-negative-cancellation",
        ),
        pytest.param(
            lambda row: row["subquestion_markings"][0].__setitem__("marks", 1),
            "subquestion marks must sum exactly",
            id="submark-wrong-sum",
        ),
        pytest.param(
            lambda row: row["subquestion_markings"][0][
                "keyword_weightages"
            ].__setitem__(
                0, float("nan")
            ),
            "entry 1 must be finite",
            id="keyword-nan",
        ),
        pytest.param(
            lambda row: (
                row["subquestion_markings"][0]["keyword_weightages"].__setitem__(
                    0, -1
                ),
                row["subquestion_markings"][0]["keyword_weightages"].__setitem__(
                    1, 3
                ),
            ),
            "entry 1 must be finite",
            id="keyword-negative-cancellation",
        ),
        pytest.param(
            lambda row: row["subquestion_markings"][0][
                "keyword_weightages"
            ].__setitem__(
                0, 0.5
            ),
            "keyword weights must sum exactly",
            id="keyword-wrong-sum",
        ),
        pytest.param(
            lambda row: row.__setitem__("question_duration", float("nan")),
            "question_duration",
            id="duration-nan",
        ),
        pytest.param(
            lambda row: row.__setitem__("question_duration", float("inf")),
            "question_duration",
            id="duration-infinity",
        ),
        pytest.param(
            lambda row: row.__setitem__("question_duration", 0),
            "question_duration",
            id="duration-zero",
        ),
        pytest.param(
            lambda row: row.__setitem__("question_duration", "1e-10000"),
            "question_duration",
            id="duration-underflow",
        ),
        pytest.param(
            lambda row: row.__setitem__("question_duration", "1e1000000"),
            "question_duration",
            id="duration-overflow",
        ),
        pytest.param(
            lambda row: row.__setitem__("math_keyboard", ""),
            "descriptive math_keyboard",
            id="descriptive-keyboard-blank",
        ),
    ],
)
def test_invalid_marking_arithmetic_never_ships(
    monkeypatch, mutate, expected: str,
) -> None:
    monkeypatch.setattr(marking.config, "phase3_decision_workers", lambda: 1)

    def invalid(request: dict) -> dict:
        response = _valid_response(request)
        mutate(response)
        return response

    with pytest.raises(kernel.ContractError) as exc_info:
        marking.decide_markings(
            [(_candidate(), _cell())], meta=META,
            envelope_sha256=ENVELOPE_SHA256,
            provider=invalid, store=kernel.DecisionStore(),
        )
    assert any(expected in defect for defect in exc_info.value.defects)


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        pytest.param(
            lambda row: row["answer_weightages"].__setitem__(1, -1),
            "exact zero",
            id="wrong-option-negative",
        ),
        pytest.param(
            lambda row: row["answer_weightages"].__setitem__(1, ""),
            "finite and numeric",
            id="wrong-option-blank",
        ),
        pytest.param(
            lambda row: row["answer_weightages"].__setitem__(0, 1),
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


def test_fixer_is_revalidated_by_the_same_sparse_arithmetic_checker(
    monkeypatch,
) -> None:
    monkeypatch.setattr(marking.config, "phase3_decision_workers", lambda: 1)
    fixer_calls = []

    def invalid_author(request: dict) -> dict:
        response = _valid_response(request)
        response["answer_weightages"].pop()
        return response

    def invalid_fixer(request: dict) -> dict:
        fixer_calls.append(copy.deepcopy(request))
        response = _valid_response(request["original_payload"])
        response["subquestion_markings"][0]["keyword_weightages"].pop()
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
        "policy_version": "assessment-marking-7",
    }


def test_valid_fixer_verdict_is_recorded_and_flagged(monkeypatch) -> None:
    monkeypatch.setattr(marking.config, "phase3_decision_workers", lambda: 1)

    def invalid_author(request: dict) -> dict:
        response = _valid_response(request)
        response["answer_weightages"][0] = -1
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
    assert verdict["answers"][0]["answer_weightage"] == "1.5"


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

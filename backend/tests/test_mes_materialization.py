"""Recorded MES materialization decisions and their mechanical contract."""
from __future__ import annotations

import copy

import pytest

from app.services import assessment_materialization as am
from app.services.phase3 import kernel

ENVELOPE_SHA256 = "e" * 64
META = {"subject": "Mathematics", "grade": "06"}


def _cell(**changes) -> dict:
    cell = {
        "cell_id": "CELL-abc123",
        "sheet_kind": "objective",
        "question_category": "MCQ",
        "cognitive_skill": "Remember",
        "difficulty": "Less",
        "marks": 1.0,
        "count": 1,
        "appears_in": ["Pre/Post-Worksheet/Test"],
        "concept_id": 7,
        "source_policy": "rewrite",
    }
    cell.update(changes)
    return cell


def _atom(**changes) -> dict:
    atom = {
        "source_qid": "QINV-0003",
        "source_document_hash": "sha256:doc",
        "source_kind": "exercise",
        "raw_text": "Look at the figure and name the solid.",
        "normalized_public_text": "Name the solid shown.",
        "shared_context": "Use the accompanying source illustration.",
        "source_answer": "Cone",
        "options": ["Cone", "Cube"],
        "route_evidence": {"owner": "recorded source host"},
        "assets": [{
            "url": "https://x/a.png",
            "alt": "a solid tapering to an apex",
            "order": 1,
            "sha256": "s1",
            "source_page": 3,
            "bbox": None,
        }],
    }
    atom.update(changes)
    return atom


def _objective_response(request: dict, **changes) -> dict:
    response = {
        "candidate_id": request["candidate_id"],
        "question": "The illustration shows a solid. Name it.",
        "answer_restriction": "Specific",
        "restriction_reason": "The response has one bounded semantic target.",
        "display_answer": "",
        "answers": [
            {
                "answer_type": "Phrases",
                "answer_content": "Cone",
                "correct_answer": "Yes",
                "answer_weightage": "1",
            },
            {
                "answer_type": "Phrases",
                "answer_content": "Cube",
                "correct_answer": "No",
                "answer_weightage": "0",
            },
        ],
        "sub_questions": [],
        "answer_explanation": "The curved face tapers to an apex.",
        "requires_visual": True,
        "rationale": "The item preserves the supplied visual identification.",
    }
    response.update(changes)
    return response


def _descriptive_response(request: dict, **changes) -> dict:
    response = {
        "candidate_id": request["candidate_id"],
        "question": "Explain the water cycle with a labelled diagram.",
        "answer_restriction": "Open",
        "restriction_reason": "Several complete explanations can earn credit.",
        "display_answer": "Evaporation, condensation and precipitation.",
        "answers": [
            {
                "answer_type": "Phrases",
                "answer_weightage": "2",
                "answer_content": "Evaporation and condensation explained.",
            },
            {
                "answer_type": "Phrases",
                "answer_weightage": "3",
                "answer_content": "Precipitation and collection explained.",
            },
        ],
        "sub_questions": [],
        "answer_explanation": "",
        "requires_visual": False,
        "rationale": "The answer and rubric satisfy the fixed cell.",
    }
    response.update(changes)
    return response


def _subjective_response(request: dict, **changes) -> dict:
    response = {
        "candidate_id": request["candidate_id"],
        "question": "Complete the sentence: I saw $$a$$ owl.",
        "display_answer": "I saw an owl.",
        "answers": [{
            "answer_type": "Phrases",
            "answer_content": "an",
            "answer_display": "an",
            "placeholder": "a",
        }],
        "sub_questions": [],
        "answer_explanation": "Use an before a vowel sound.",
        "requires_visual": False,
        "rationale": "The source has one ordered response blank.",
    }
    response.update(changes)
    return response


def _verified(_request: dict) -> dict:
    return {"verdict": "verified", "confidence": 1.0, "issues": []}


def _materialize(**overrides) -> dict:
    arguments = {
        "meta": META,
        "context": {"chapter": "complete curricular context"},
        "envelope_sha256": ENVELOPE_SHA256,
        "provider": _objective_response,
        "critic": _verified,
        "store": kernel.DecisionStore(),
    }
    arguments.update(overrides)
    return am.materialize_candidate(_atom(), _cell(), **arguments)


def test_recorded_candidate_preserves_complete_evidence_and_stable_audit():
    seen = {}

    def provider(request):
        seen.update(copy.deepcopy(request))
        return _objective_response(request)

    candidate = _materialize(provider=provider)

    assert seen["stage"] == "assessment.materialize"
    assert seen["source_atom"] == _atom()
    assert seen["blueprint_cell"] == _cell()
    assert seen["curricular_evidence"] == {
        "chapter": "complete curricular context"
    }
    assert candidate["question_text"] == candidate["question"]
    assert candidate["assets"] == _atom()["assets"]
    assert candidate["source_evidence"] == _atom()["raw_text"]
    assert candidate["source_qid"] == _atom()["source_qid"]
    assert candidate["source_document_hash"] == _atom()[
        "source_document_hash"
    ]
    assert candidate["source_kind"] == _atom()["source_kind"]
    assert candidate["shared_context"] == _atom()["shared_context"]
    assert candidate["route_evidence"] == _atom()["route_evidence"]
    assert candidate["source_context"]["source_answer"] == "Cone"
    assert candidate["source_context"]["normalized_public_text"] == (
        "Name the solid shown."
    )
    assert candidate["assessment_eligibility"] == "accepted"
    audit = candidate["_aegis_assessment_materialization"]
    assert audit["rationale"] == _objective_response(seen)["rationale"]
    assert audit["flags"] == []
    assert audit["authority"]["decision_key"]
    assert audit["authority"]["policy_version"] == (
        "assessment-materialize-11"
    )
    assert "created_at" not in audit["authority"]
    assert "provider" not in audit["authority"]


def test_materialization_cannot_author_restriction_or_marking():
    candidate = _materialize(
        provider=lambda request: _objective_response(
            request,
            answer_restriction="Open",
            restriction_reason="Different valid representations earn credit.",
        )
    )

    assert candidate["answer_restriction"] == ""
    assert candidate["restriction_reason"] == ""
    assert all(
        answer["answer_weightage"] == ""
        for answer in candidate["answers"]
    )
    assert candidate["question_duration"] is None
    assert candidate["math_keyboard"] == ""
    assert candidate["assessment_eligibility"] == "accepted"
    assert "question_display" not in am.MATERIALIZE_SYSTEM
    assert "always Specific" not in am.MATERIALIZE_SYSTEM
    assert "Do not decide Open or Specific" in am.MATERIALIZE_SYSTEM


def test_mechanical_defects_receive_one_bounded_correction_sequence():
    calls = []

    def provider(request):
        calls.append(copy.deepcopy(request))
        response = _objective_response(request)
        if len(calls) == 1:
            response["answers"][1]["correct_answer"] = "Yes"
        return response

    candidate = _materialize(provider=provider)

    assert candidate["assessment_eligibility"] == "accepted"
    assert len(calls) == 2
    assert any(
        "exactly one correct option" in defect
        for defect in calls[1]["response_contract_feedback"]
    )


def test_critic_dissent_is_advisory_and_never_retries_authorship():
    provider_calls = []
    critic_calls = []

    def provider(request):
        provider_calls.append(copy.deepcopy(request))
        return _objective_response(request)

    def critic(request):
        critic_calls.append(copy.deepcopy(request))
        return {
            "verdict": "dissent",
            "confidence": 1.0,
            "issues": ["The distractor family deserves human review."],
        }

    candidate = _materialize(provider=provider, critic=critic)

    assert len(provider_calls) == len(critic_calls) == 1
    assert critic_calls[0]["proposed_decision"] == _objective_response(
        provider_calls[0]
    )
    assert candidate["assessment_eligibility"] == "flagged"
    assert candidate["question"] == _objective_response(
        provider_calls[0]
    )["question"]
    assert any("dissent" in flag for flag in candidate["flags"])
    assert candidate["_aegis_assessment_materialization"]["flags"] == (
        candidate["flags"]
    )


def test_fixer_corrects_a_block_once_and_the_intervention_is_flagged():
    provider_calls = []
    fixer_calls = []

    def malformed(request):
        provider_calls.append(copy.deepcopy(request))
        return {"candidate_id": request["candidate_id"]}

    def fixer(request):
        fixer_calls.append(copy.deepcopy(request))
        return _objective_response(request["original_payload"])

    candidate = _materialize(provider=malformed, fixer=fixer)

    assert len(provider_calls) == 3
    assert len(fixer_calls) == 1
    assert candidate["question_text"] == candidate["question"]
    assert candidate["assessment_eligibility"] == "flagged"
    authority = candidate["_aegis_assessment_materialization"]["authority"]
    assert authority["fixer"] is True
    assert any(flag.startswith("fixer:") for flag in candidate["flags"])


def test_upstream_marking_allocation_is_erased_before_assembly():
    def wrong_weight(request):
        response = _objective_response(request)
        response["answers"][0]["answer_weightage"] = "NaN"
        response["answers"][1]["answer_weightage"] = "-500"
        return response

    candidate = _materialize(provider=wrong_weight, critic=None)

    assert [row["answer_weightage"] for row in candidate["answers"]] == [
        "", ""
    ]


def test_marking_fields_are_not_part_of_the_materialization_checker():
    objective_cell = _cell()
    objective_id = am._candidate_id(_atom(), objective_cell)
    objective = _objective_response({"candidate_id": objective_id})
    objective["answers"][1]["answer_weightage"] = "not-a-number"
    assert not any(
        "weightage" in defect
        for defect in am._proposal_defects(
            objective, objective_cell, objective_id,
        )
    )

    descriptive_cell = _cell(
        sheet_kind="descriptive", question_category="Long Answer", marks=5.0,
    )
    descriptive_id = am._candidate_id(_atom(), descriptive_cell)
    descriptive = _descriptive_response({"candidate_id": descriptive_id})
    descriptive["answers"][0]["answer_weightage"] = "NaN"
    descriptive["sub_questions"] = [{
        "text": "Explain one stage.",
        "marks": "infinite",
        "keywords": [],
    }]
    defects = am._proposal_defects(
        descriptive, descriptive_cell, descriptive_id,
    )
    assert not any("weightage" in defect for defect in defects)
    assert not any("marks" in defect for defect in defects)


def test_subjective_blank_materializes_with_ordered_placeholder_contract():
    cell = _cell(
        cell_id="CELL-SUBJ",
        sheet_kind="subjective",
        question_category="Fill in the Blanks",
    )
    atom = _atom(
        source_qid="QINV-ARTICLE-1",
        raw_text="I saw ___ owl.",
        normalized_public_text="I saw ___ owl.",
        source_answer="an",
        options=[],
        assets=[],
    )

    candidate = am.materialize_candidate(
        atom,
        cell,
        meta=META,
        context={"chapter": "Articles"},
        envelope_sha256=ENVELOPE_SHA256,
        provider=_subjective_response,
        critic=_verified,
        store=kernel.DecisionStore(),
        profile={
            **am.assessment_profile.DEFAULT_PROFILE,
            "sheet_kinds": ("objective", "descriptive", "subjective"),
        },
    )

    assert candidate["sheet_kind"] == "subjective"
    assert candidate["question"] == "Complete the sentence: I saw $$a$$ owl."
    assert candidate["sub_questions"] == []
    assert candidate["answers"] == [{
        "answer_type": "Phrases",
        "answer_content": "an",
        "answer_display": "an",
        "placeholder": "a",
        "answer_weightage": "",
    }]


def test_multipart_descriptive_uses_only_subquestion_rubrics():
    cell = _cell(
        sheet_kind="descriptive", question_category="Long Answer", marks=4,
    )
    candidate_id = am._candidate_id(_atom(), cell)
    subquestions = [{
        "text": "a) Explain evaporation.",
        "keywords": [{
            "answer_type": "Phrases",
            "keyword": "[content]: water changes into vapour",
        }],
    }]
    valid = _descriptive_response(
        {"candidate_id": candidate_id},
        question="Use the water-cycle diagram.",
        answers=[],
        sub_questions=subquestions,
    )

    assert not am._proposal_defects(valid, cell, candidate_id)

    duplicated_rubric = copy.deepcopy(valid)
    duplicated_rubric["answers"] = [{
        "answer_type": "Phrases",
        "answer_content": "[content]: water changes into vapour",
    }]
    assert any(
        "duplicates" in defect or "main answers empty" in defect
        for defect in am._proposal_defects(
            duplicated_rubric, cell, candidate_id,
        )
    )

    duplicated_text = copy.deepcopy(valid)
    duplicated_text["question"] += " a) Explain evaporation."
    assert any(
        "text is duplicated" in defect
        for defect in am._proposal_defects(duplicated_text, cell, candidate_id)
    )


def test_materialization_prompt_preserves_deliberately_open_source_values():
    assert "source deliberately leaves a quantity" in am.MATERIALIZE_SYSTEM
    assert "never invent convenient numbers" in am.MATERIALIZE_SYSTEM
    assert "fabricated values" in am.MATERIALIZE_CRITIC_SYSTEM


def test_extra_numeric_marking_never_engages_materialization_fixer():
    provider_calls = []
    fixer_calls = []

    def malformed(request):
        provider_calls.append(copy.deepcopy(request))
        response = _objective_response(request)
        response["answers"][1]["answer_weightage"] = "not-a-number"
        return response

    def fixer(request):
        fixer_calls.append(copy.deepcopy(request))
        raise AssertionError("marking defects belong to assessment.marking")

    candidate = _materialize(provider=malformed, fixer=fixer)

    assert len(provider_calls) == 1
    assert fixer_calls == []
    assert candidate["answers"][1]["answer_weightage"] == ""
    assert not candidate["_aegis_assessment_materialization"]["authority"][
        "fixer"
    ]


@pytest.mark.parametrize("marks", [float("nan"), float("inf"), "-Infinity"])
def test_nonfinite_cell_marks_are_rejected_before_provider_spend(marks):
    calls = []

    def provider(request):
        calls.append(request)
        return _objective_response(request)

    with pytest.raises(am.MaterializationError, match="marks"):
        am.materialize_candidate(
            _atom(),
            _cell(marks=marks),
            meta=META,
            context={"chapter": "complete curricular context"},
            envelope_sha256=ENVELOPE_SHA256,
            provider=provider,
            store=kernel.DecisionStore(),
        )
    assert calls == []


def test_provider_outage_is_genuine_impossibility_not_an_empty_candidate():
    def unavailable(_request):
        raise RuntimeError("provider down")

    with pytest.raises(RuntimeError, match="provider down"):
        _materialize(provider=unavailable, critic=None)


def test_decide_once_replays_without_any_provider_critic_or_fixer_call():
    store = kernel.DecisionStore()
    calls = {"provider": 0, "critic": 0}

    def provider(request):
        calls["provider"] += 1
        return _objective_response(request)

    def critic(request):
        calls["critic"] += 1
        return _verified(request)

    first = _materialize(provider=provider, critic=critic, store=store)

    def forbidden(_request):
        raise AssertionError("cached replay spent another provider call")

    second = _materialize(
        provider=forbidden,
        critic=forbidden,
        fixer=forbidden,
        store=store,
    )

    assert second == first
    assert calls == {"provider": 1, "critic": 1}
    assert len(store.keys()) == 1


def test_changed_curricular_context_rekeys_the_decision():
    store = kernel.DecisionStore()
    contexts = []

    def provider(request):
        contexts.append(copy.deepcopy(request["curricular_evidence"]))
        return _objective_response(request)

    _materialize(provider=provider, critic=None, store=store, context="first")
    _materialize(provider=provider, critic=None, store=store, context="second")

    assert contexts == ["first", "second"]
    assert len(store.keys()) == 2


def test_batch_preserves_order_and_rejects_duplicate_candidate_identity(
    monkeypatch,
):
    monkeypatch.setattr(am.config, "phase3_decision_workers", lambda: 4)
    pairs = [
        (
            _atom(source_qid=f"QINV-{index:04d}"),
            _cell(cell_id=f"CELL-{index:04d}"),
        )
        for index in range(1, 4)
    ]

    result = am.materialize_candidates(
        pairs,
        meta=META,
        context={"chapter": "complete"},
        envelope_sha256=ENVELOPE_SHA256,
        provider=_objective_response,
        critic=_verified,
        store=kernel.DecisionStore(),
    )

    expected = [am._candidate_id(atom, cell) for atom, cell in pairs]
    assert [row["candidate_id"] for row in result["candidates"]] == expected
    assert result["accepted"] == 3
    assert result["flagged"] == 0
    assert result["zero_loss"]["holds"]

    calls = []

    def should_not_run(request):
        calls.append(request)
        return _objective_response(request)

    with pytest.raises(am.MaterializationError, match="repeat candidate_id"):
        am.materialize_candidates(
            [pairs[0], pairs[0]],
            meta=META,
            envelope_sha256=ENVELOPE_SHA256,
            provider=should_not_run,
        )
    assert calls == []


def test_nested_rich_text_is_mechanical_but_marking_arithmetic_is_later():
    cell = _cell(
        sheet_kind="descriptive", question_category="Long Answer", marks=5.0,
    )
    candidate_id = am._candidate_id(_atom(), cell)
    request = {"candidate_id": candidate_id}
    malformed = _descriptive_response(
        request,
        sub_questions=[{
            "text": "Explain [Katex]x",
            "marks": "4",
            "keywords": [{
                "answer_type": "Phrases",
                "weightage": "4",
                "keyword": "[Katex]y",
            }],
        }],
    )

    defects = am._proposal_defects(malformed, cell, candidate_id)

    assert not any("subquestion marks sum" in row for row in defects)
    assert any(row.startswith("rich-text:") for row in defects)


def test_answer_medium_and_four_mark_rubric_shape_are_mechanical():
    cell = _cell(
        sheet_kind="descriptive", question_category="Long Answer", marks=4.0,
    )
    candidate_id = am._candidate_id(_atom(), cell)
    response = _descriptive_response(
        {"candidate_id": candidate_id},
        answers=[{
            "answer_type": "Equation",
            "answer_content": "Result: [Katex]x=2[/Katex]",
        }],
    )

    defects = am._proposal_defects(response, cell, candidate_id)

    assert any("equation_katex_wrapper" in defect for defect in defects)
    assert any("at least two answer/rubric blocks" in defect for defect in defects)


def test_english_post_materialization_honors_thirty_answer_master_capacity():
    meta = {
        "board": "MSBSHSE",
        "grade": "6",
        "subject": "English",
    }
    cell = _cell(
        sheet_kind="descriptive",
        question_category="Long Answer",
        marks=5.0,
    )
    seen = {}

    def provider(request):
        seen.update(copy.deepcopy(request))
        return _descriptive_response(
            request,
            answers=[{
                "answer_type": "Phrases",
                "answer_content": f"[content]: criterion {number}",
            } for number in range(1, 31)],
            answer_explanation=(
                "Evaporation, condensation and precipitation."
            ),
        )

    candidate = am.materialize_candidate(
        _atom(),
        cell,
        meta=meta,
        learning_phase="Post",
        envelope_sha256=ENVELOPE_SHA256,
        provider=provider,
        critic=_verified,
        store=kernel.DecisionStore(),
    )

    assert seen["workbook_capacities"] == {
        "descriptive_answer_slots": 30,
    }
    assert len(candidate["answers"]) == 30
    assert candidate["assessment_eligibility"] == "accepted"
    assert candidate["authority"]["policy_version"] == (
        "assessment-materialize-11"
    )


@pytest.mark.parametrize(
    ("subject", "learning_phase", "expected"),
    [
        ("English", "Post", 30),
        ("English", "Pre", 10),
        ("Mathematics", "Post", 10),
    ],
)
def test_descriptive_answer_capacity_is_profile_and_lane_aware(
    subject, learning_phase, expected,
):
    meta = {"board": "MSBSHSE", "grade": "6", "subject": subject}
    profile = am.assessment_profile.resolve_for_metadata(None, meta)

    assert am._descriptive_answer_capacity(
        profile,
        learning_phase=learning_phase,
    ) == expected


def test_materialization_accepts_canonical_katex_array_markup():
    cell = _cell()
    candidate_id = am._candidate_id(_atom(), cell)
    response = _objective_response({"candidate_id": candidate_id})
    response["question"] = (
        r"Study [Katex] \begin{array}{cc}A&B\\1&2\end{array} [/Katex]."
    )

    assert "rich-text: unsupported_table" not in am._proposal_defects(
        response, cell, candidate_id,
    )


def test_malformed_nested_collections_report_defects_without_crashing():
    cell = _cell()
    candidate_id = am._candidate_id(_atom(), cell)
    malformed = _objective_response({"candidate_id": candidate_id})
    malformed["answers"] = 7
    malformed["sub_questions"] = {"not": "an array"}

    defects = am._proposal_defects(malformed, cell, candidate_id)

    assert "answers must be an array" in defects
    assert "sub_questions must be an array" in defects


def test_bespoke_materialization_cache_and_retry_lane_are_retired():
    assert not hasattr(am, "cache_identity")
    assert not hasattr(am, "load_cached_candidate")
    assert not hasattr(am, "store_cached_candidate")
    assert not hasattr(am, "reset_cache")
    assert not hasattr(am, "MAX_ATTEMPTS")

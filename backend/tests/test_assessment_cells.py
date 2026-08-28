"""Recorded, decide-once assessment-cell verdicts."""
from __future__ import annotations

import copy

import pytest

from app.services import assessment_cells as cells
from app.services.phase3 import kernel


ENVELOPE_SHA256 = "c" * 64
META = {
    "subject": "Science",
    "board": "State Board",
    "grade": "6",
    "chapter_title": "Living Organisms",
}
PROFILE = {
    "name": "reference-test",
    "appears_in": "Pre/Post-Worksheet/Test",
    "sheet_kinds": ("objective", "descriptive"),
}


def _atom(source_qid: str = "QINV-0001") -> dict:
    return {
        "source_qid": source_qid,
        "source_document_hash": "sha256:source",
        "source_paper_number": "Exercise 1, page 12",
        "page": 12,
        "block_ids": ["BLK-00003"],
        "source_kind": "checkpoint_question",
        "parent_qid": "QINV-0000",
        "subpart": "1",
        "alternative_set_id": "OR-01",
        "raw_text": "Explain why a leaf is a living part of a plant.",
        "normalized_public_text": (
            "Explain why a leaf is a living part of a plant."
        ),
        "shared_context": "Use the labelled plant diagram.",
        "source_answer": "It grows, respires, and responds within the plant.",
        "options": [],
        "topic_hint": "Characteristics of living things",
        "polish_flag": "reviewed",
        "assets": [
            {
                "source_page": 12,
                "bbox": [10, 20, 30, 40],
                "sha256": "asset-sha",
                "url": "https://example.test/leaf.png",
                "alt": "Labelled diagram of a plant leaf",
                "order": 1,
            }
        ],
        "route_evidence": {
            "home": "Plant life processes",
            "source_start": 44,
        },
    }


def _valid_response(request: dict, **overrides) -> dict:
    response = {
        "source_qid": request["source_atom"]["source_qid"],
        "sheet_kind": "descriptive",
        "question_category": "Long Answer",
        "cognitive_skill": "Understand",
        "difficulty": "Moderate",
        "marks": 3,
        "rationale": "The learner must explain linked characteristics.",
    }
    response.update(overrides)
    return response


def _verified(_request: dict) -> dict:
    return {"verdict": "verified", "confidence": 1.0, "issues": []}


def _forbidden(label: str):
    def call(_request: dict) -> dict:
        raise AssertionError(f"cached replay called {label}")

    return call


def test_cell_decision_carries_complete_content_without_print_position(
    monkeypatch,
) -> None:
    monkeypatch.setattr(cells.config, "phase3_decision_workers", lambda: 1)
    source = _atom()
    original = copy.deepcopy(source)
    provider_payloads = []
    critic_payloads = []
    store = kernel.DecisionStore()

    def provider(request: dict) -> dict:
        provider_payloads.append(copy.deepcopy(request))
        return _valid_response(request)

    def critic(request: dict) -> dict:
        critic_payloads.append(copy.deepcopy(request))
        return _verified(request)

    result = cells.decide_cells(
        [source],
        meta=META,
        profile=PROFILE,
        envelope_sha256=ENVELOPE_SHA256,
        provider=provider,
        critic=critic,
        store=store,
        fixer=_forbidden("the Fixer"),
    )

    assert source == original
    assert len(provider_payloads) == len(critic_payloads) == 1
    payload = provider_payloads[0]
    evidence = payload["source_atom"]
    assert payload["stage"] == "assessment.cell"
    assert "There is no quota" in payload["rules"]
    assert evidence["raw_text"] == source["raw_text"]
    assert evidence["source_answer"] == source["source_answer"]
    assert evidence["parent_qid"] == "QINV-0000"
    assert evidence["alternative_set_id"] == "OR-01"
    assert evidence["block_ids"] == ["BLK-00003"]
    assert evidence["route_evidence"] == {"home": "Plant life processes"}
    assert evidence["assets"] == [
        {
            "sha256": "asset-sha",
            "url": "https://example.test/leaf.png",
            "alt": "Labelled diagram of a plant leaf",
            "order": 1,
        }
    ]
    assert "page" not in evidence
    assert "source_paper_number" not in evidence
    assert critic_payloads[0]["proposed_decision"]["source_qid"] == (
        "QINV-0001"
    )

    cell = result[0]
    assert cell["sheet_kind"] == "descriptive"
    assert cell["question_category"] == "Long Answer"
    assert cell["cognitive_skill"] == "Understand"
    assert cell["difficulty"] == "Moderate"
    assert cell["marks"] == 3.0
    assert cell["count"] == 1
    assert cell["appears_in"] == ["Pre/Post-Worksheet/Test"]
    assert cell["accepted_source_qids"] == ["QINV-0001"]
    assert cell["flags"] == []
    authority = cell["authority"]
    assert authority["policy_version"] == "assessment-cell-3"
    assert authority["review_flags"] == []
    assert "created_at" not in authority
    assert "provider" not in authority
    assert cell["cell_id"] == "CELL-" + authority["decision_key"][:16]
    stored = store.get(authority["decision_key"])
    assert stored is not None
    assert stored["kind"] == "assessment.cell"
    assert stored["unit_id"] == "QINV-0001"


def test_cell_decisions_replay_without_any_authority_calls(monkeypatch) -> None:
    monkeypatch.setattr(cells.config, "phase3_decision_workers", lambda: 1)
    store = kernel.DecisionStore()
    calls = {"provider": 0, "critic": 0}

    def provider(request: dict) -> dict:
        calls["provider"] += 1
        return _valid_response(request)

    def critic(request: dict) -> dict:
        calls["critic"] += 1
        return _verified(request)

    first = cells.decide_cells(
        [_atom()],
        meta=META,
        profile=PROFILE,
        envelope_sha256=ENVELOPE_SHA256,
        provider=provider,
        critic=critic,
        store=store,
        fixer=_forbidden("the Fixer"),
    )
    second = cells.decide_cells(
        [_atom()],
        meta=META,
        profile=PROFILE,
        envelope_sha256=ENVELOPE_SHA256,
        provider=_forbidden("the provider"),
        critic=_forbidden("the critic"),
        store=store,
        fixer=_forbidden("the Fixer"),
    )

    assert second == first
    assert calls == {"provider": 1, "critic": 1}
    assert len(store.keys()) == 1


def test_critic_dissent_flags_without_retrying_the_author(monkeypatch) -> None:
    monkeypatch.setattr(cells.config, "phase3_decision_workers", lambda: 1)
    calls = {"provider": 0, "critic": 0}

    def provider(request: dict) -> dict:
        calls["provider"] += 1
        return _valid_response(request)

    def dissent(_request: dict) -> dict:
        calls["critic"] += 1
        return {
            "verdict": "dissent",
            "confidence": 1.0,
            "issues": ["Marks may deserve independent review."],
        }

    cell = cells.decide_cells(
        [_atom()],
        meta=META,
        profile=PROFILE,
        envelope_sha256=ENVELOPE_SHA256,
        provider=provider,
        critic=dissent,
        store=kernel.DecisionStore(),
    )[0]

    assert calls == {"provider": 1, "critic": 1}
    assert cell["marks"] == 3.0
    assert any("dissent" in flag for flag in cell["flags"])
    assert any("Marks may deserve" in flag for flag in cell["flags"])
    assert cell["authority"]["review_flags"] == cell["flags"]


def test_mechanical_defect_gets_bounded_correction_before_critic(
    monkeypatch,
) -> None:
    monkeypatch.setattr(cells.config, "phase3_decision_workers", lambda: 1)
    requests = []
    critic_calls = []

    def provider(request: dict) -> dict:
        requests.append(copy.deepcopy(request))
        if len(requests) == 1:
            return _valid_response(request, source_qid="wrong", marks=0)
        return _valid_response(request)

    def critic(request: dict) -> dict:
        critic_calls.append(copy.deepcopy(request))
        return _verified(request)

    cell = cells.decide_cells(
        [_atom()],
        meta=META,
        profile=PROFILE,
        envelope_sha256=ENVELOPE_SHA256,
        provider=provider,
        critic=critic,
        store=kernel.DecisionStore(),
    )[0]

    assert len(requests) == 2
    assert len(critic_calls) == 1
    assert any(
        "source_qid must echo" in defect
        for defect in requests[1]["response_contract_feedback"]
    )
    assert any(
        "marks must be finite and positive" in defect
        for defect in requests[1]["response_contract_feedback"]
    )
    assert cell["accepted_source_qids"] == ["QINV-0001"]


@pytest.mark.parametrize(
    "invalid_marks",
    [
        pytest.param(float("nan"), id="nan"),
        pytest.param(float("inf"), id="positive-infinity"),
        pytest.param(float("-inf"), id="negative-infinity"),
    ],
)
def test_nonfinite_marks_are_a_mechanical_defect(
    monkeypatch, invalid_marks: float,
) -> None:
    monkeypatch.setattr(cells.config, "phase3_decision_workers", lambda: 1)
    requests = []

    def provider(request: dict) -> dict:
        requests.append(copy.deepcopy(request))
        if len(requests) == 1:
            return _valid_response(request, marks=invalid_marks)
        return _valid_response(request)

    cell = cells.decide_cells(
        [_atom()],
        meta=META,
        profile=PROFILE,
        envelope_sha256=ENVELOPE_SHA256,
        provider=provider,
        store=kernel.DecisionStore(),
    )[0]

    assert len(requests) == 2
    assert "marks must be finite and positive" in (
        requests[1]["response_contract_feedback"]
    )
    assert cell["marks"] == 3.0


def test_mechanical_exhaustion_routes_to_recorded_fixer(monkeypatch) -> None:
    monkeypatch.setattr(cells.config, "phase3_decision_workers", lambda: 1)
    provider_calls = []
    fixer_calls = []

    def invalid_provider(request: dict) -> dict:
        provider_calls.append(copy.deepcopy(request))
        return _valid_response(request, sheet_kind="unknown")

    def fixer(request: dict) -> dict:
        fixer_calls.append(copy.deepcopy(request))
        original = request["original_payload"]
        return _valid_response(original)

    cell = cells.decide_cells(
        [_atom()],
        meta=META,
        profile=PROFILE,
        envelope_sha256=ENVELOPE_SHA256,
        provider=invalid_provider,
        critic=_verified,
        store=kernel.DecisionStore(),
        fixer=fixer,
    )[0]

    assert len(provider_calls) == kernel.MAX_ATTEMPTS
    assert len(fixer_calls) == 1
    assert fixer_calls[0]["contract"] == {
        "kind": "assessment.cell",
        "unit_id": "QINV-0001",
        "policy_version": "assessment-cell-3",
    }
    assert cell["sheet_kind"] == "descriptive"
    assert cell["authority"]["fixer"] is True
    assert any(flag.startswith("fixer:") for flag in cell["flags"])


def test_invalid_response_never_becomes_a_local_fallback(monkeypatch) -> None:
    monkeypatch.setattr(cells.config, "phase3_decision_workers", lambda: 1)

    with pytest.raises(kernel.ContractError, match="assessment.cell decision"):
        cells.decide_cells(
            [_atom()],
            meta=META,
            profile=PROFILE,
            envelope_sha256=ENVELOPE_SHA256,
            provider=lambda request: _valid_response(
                request, difficulty="Intermediate"
            ),
            critic=_forbidden("the critic"),
            store=kernel.DecisionStore(),
        )


def test_parallel_cell_decisions_preserve_atom_order(monkeypatch) -> None:
    monkeypatch.setattr(cells.config, "phase3_decision_workers", lambda: 4)
    atoms = [_atom(f"QINV-{index:04d}") for index in range(1, 7)]

    decided = cells.decide_cells(
        atoms,
        meta=META,
        profile=PROFILE,
        envelope_sha256=ENVELOPE_SHA256,
        provider=_valid_response,
        store=kernel.DecisionStore(),
    )

    assert [cell["accepted_source_qids"][0] for cell in decided] == [
        atom["source_qid"] for atom in atoms
    ]


def test_decision_key_prefix_collision_fails_closed(monkeypatch) -> None:
    monkeypatch.setattr(cells.config, "phase3_decision_workers", lambda: 2)
    shared_prefix = "f" * 16

    def colliding_key(*, unit_id: str, **_kwargs) -> str:
        suffix = "1" if unit_id == "QINV-0001" else "2"
        return shared_prefix + (suffix * 48)

    monkeypatch.setattr(cells.kernel, "decision_key", colliding_key)

    with pytest.raises(cells.CellDecisionError, match="duplicate cell_id") as caught:
        cells.decide_cells(
            [_atom("QINV-0001"), _atom("QINV-0002")],
            meta=META,
            profile=PROFILE,
            envelope_sha256=ENVELOPE_SHA256,
            provider=_valid_response,
            store=kernel.DecisionStore(),
        )

    assert "CELL-" + shared_prefix in str(caught.value)


def test_cell_input_identity_and_profile_fail_before_provider_calls() -> None:
    provider = _forbidden("the provider")
    with pytest.raises(cells.CellDecisionError, match="repeat"):
        cells.decide_cells(
            [_atom(), _atom()],
            meta=META,
            profile=PROFILE,
            envelope_sha256=ENVELOPE_SHA256,
            provider=provider,
        )

    with pytest.raises(cells.CellDecisionError, match="appears_in"):
        cells.decide_cells(
            [_atom()],
            meta=META,
            profile={"sheet_kinds": ("objective", "descriptive")},
            envelope_sha256=ENVELOPE_SHA256,
            provider=provider,
        )


def test_a_widened_profile_widens_the_cells_wire_contract(monkeypatch) -> None:
    """RE-AUTHORED by spec-step8 T12/M5-M6b, and the name is the point.

    The old name was ``test_profile_cannot_enable_an_unsupported_subjective_
    wire``. Its profile carried ``allow_subjective_rows: True`` and no
    ``sheet_kinds``, so under M5b's accessor it would fall back to the
    default and still raise — [measured] GREEN while proving nothing. A
    green test whose name has stopped being true is worse than a red one.

    The plumbing genuinely widening the cells wire is the whole point of
    M5-M6. Subjective is now renderable end to end; this test keeps the
    profile-boundary assertion by contrasting an explicit two-sheet profile
    with an explicit three-sheet profile.
    """
    monkeypatch.setattr(cells.config, "phase3_decision_workers", lambda: 1)
    widened = {
        **PROFILE,
        "sheet_kinds": ("objective", "descriptive", "subjective"),
    }
    requests = []

    def provider(request: dict) -> dict:
        requests.append(copy.deepcopy(request))
        return _valid_response(
            request, sheet_kind="subjective", question_category="Short Answer",
            marks=2,
        )

    decided = cells.decide_cells(
        [_atom()],
        meta=META,
        profile=widened,
        envelope_sha256=ENVELOPE_SHA256,
        provider=provider,
        store=kernel.DecisionStore(),
    )
    assert decided[0]["sheet_kind"] == "subjective"
    assert requests[0]["profile"]["allowed_sheet_kinds"] == [
        "objective", "descriptive", "subjective"]

    # The un-widened profile still refuses, exactly as before.
    with pytest.raises(kernel.ContractError, match="sheet_kind"):
        cells.decide_cells(
            [_atom()],
            meta=META,
            profile=PROFILE,
            envelope_sha256=ENVELOPE_SHA256,
            provider=provider,
            store=kernel.DecisionStore(),
        )

    # One accepted attempt under the widened profile, then MAX_ATTEMPTS
    # refusals under the un-widened one.
    assert len(requests) == 1 + kernel.MAX_ATTEMPTS
    assert requests[-1]["profile"]["allowed_sheet_kinds"] == [
        "objective", "descriptive",
    ]


def test_cell_prompt_gets_profile_categories_and_marks_contract(monkeypatch) -> None:
    monkeypatch.setattr(cells.config, "phase3_decision_workers", lambda: 1)
    math_meta = {
        "board": "MSBSHSE",
        "grade": "6",
        "subject": "Mathematics",
    }
    requests = []

    def provider(request: dict) -> dict:
        requests.append(copy.deepcopy(request))
        attempt = len(requests)
        if attempt == 1:
            return _valid_response(
                request, question_category="Long Answer", marks=3,
            )
        if attempt == 2:
            return _valid_response(
                request,
                question_category="Short Answer Type (2 Marks)",
                marks=3,
            )
        return _valid_response(
            request,
            question_category="Short Answer Type (2 Marks)",
            marks=2,
        )

    cell = cells.decide_cells(
        [_atom()],
        meta=math_meta,
        profile=cells.assessment_profile.DEFAULT_PROFILE,
        envelope_sha256=ENVELOPE_SHA256,
        provider=provider,
        store=kernel.DecisionStore(),
    )[0]

    assert len(requests) == 3
    policy = requests[0]["profile"]["assessment_format_policy"]
    assert policy["policy_id"] == (
        "msbshse-grade-6-mathematics-2026-08-27"
    )
    assert tuple(policy["formats_by_sheet"]["descriptive"]) == (
        "Very Short Answer Questions",
        "Short Answer Type (2 Marks)",
        "Short Answer Type (3 Marks)",
        "Long Answer Type (4 Marks)",
        "Long Answer Type (5 Marks)",
    )
    assert any(
        "question_category must be one of" in defect
        for defect in requests[1]["response_contract_feedback"]
    )
    assert any(
        "marks must be one of (2,)" in defect
        for defect in requests[2]["response_contract_feedback"]
    )
    assert cell["question_category"] == "Short Answer Type (2 Marks)"
    assert cell["marks"] == 2.0


def test_cell_systems_do_not_pin_category_literals() -> None:
    for literal in (
        "Multiple Choice Question",
        "Assertion & Reasons",
        "Case Based Questions",
    ):
        assert literal not in cells.CELL_SYSTEM
        assert literal not in cells.GENERATED_CELL_SYSTEM


def test_per_subpoint_marks_reject_fractional_basis(monkeypatch) -> None:
    monkeypatch.setattr(cells.config, "phase3_decision_workers", lambda: 1)
    math_meta = {
        "board": "Maharashtra",
        "grade": "Class 6",
        "subject": "Maths",
    }
    requests = []

    def provider(request: dict) -> dict:
        requests.append(copy.deepcopy(request))
        return _valid_response(
            request,
            sheet_kind="subjective",
            question_category="Fill in the blanks",
            marks=1.5 if len(requests) == 1 else 2,
        )

    cell = cells.decide_cells(
        [_atom()],
        meta=math_meta,
        profile=cells.assessment_profile.DEFAULT_PROFILE,
        envelope_sha256=ENVELOPE_SHA256,
        provider=provider,
        store=kernel.DecisionStore(),
    )[0]

    assert any(
        "positive whole-number multiple" in defect
        for defect in requests[1]["response_contract_feedback"]
    )
    assert cell["sheet_kind"] == "subjective"
    assert cell["marks"] == 2.0


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
def test_cell_checker_fails_closed_on_malformed_max_subpoints(
    malformed_maximum,
) -> None:
    policy = {
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
    }
    checker = cells._cell_checker(
        "QINV-MAX", ("objective",), policy,
    )

    defects = checker({
        "source_qid": "QINV-MAX",
        "sheet_kind": "objective",
        "question_category": "Match the Following",
        "cognitive_skill": "Remember",
        "difficulty": "Less",
        "marks": 1,
        "rationale": "One represented match is within the wire shape.",
    })

    assert any(
        "invalid max_subpoints policy" in defect for defect in defects
    ), defects

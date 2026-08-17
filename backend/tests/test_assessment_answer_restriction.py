"""Dedicated, registry-governed Open/Specific decision contract."""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path

import pytest

from app.services import assessment_answer_restriction as ar
from app.services.phase3 import kernel


ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
ENVELOPE_SHA256 = "e" * 64
META = {
    "subject": "Mathematics",
    "board": "MSBSHSE",
    "grade": "06",
    "chapter_title": "Three-Dimensional Shapes",
}


def _candidate(index: int = 1, **changes) -> dict:
    candidate = {
        "candidate_id": f"CAND-{index:04d}",
        "question": "Explain two valid ways to identify the solid.",
        "question_text": "Explain two valid ways to identify the solid.",
        "sheet_kind": "descriptive",
        "question_category": "Long Answer",
        "cognitive_skill": "Analyse",
        "difficulty": "Moderate",
        "marks": 3,
        "display_answer": "A correct response may use faces or a net.",
        "answers": [{
            "answer_type": "Phrases",
            "answer_content": "One valid identification with justification.",
            "answer_weightage": "3",
        }],
        "rubric": {
            "full_credit": "Any two valid, justified representations.",
            "source_page": 18,
        },
        "marking_scheme": {
            "steps": [
                {"description": "valid representation", "marks": 2, "row": 4},
                {"description": "justification", "marks": 1, "row": 5},
            ],
        },
        "sub_questions": [],
        "answer_explanation": "Different valid representations earn credit.",
        "requires_visual": True,
        "assets": [{
            "url": "https://example.test/solid.png",
            "alt": "A solid and its net",
            "sha256": "asset-sha",
            "source_page": 18,
            "bbox": [1, 2, 3, 4],
            "order": 1,
        }],
        "tables": [{
            "caption": "Properties of the solid",
            "cells": [["faces", "edges"], ["6", "12"]],
            "page_hint": 18,
        }],
        "content_objects": {
            "figure": {
                "caption": "Two equivalent representations",
                "position": 7,
            },
        },
        "source_qid": f"QINV-{index:04d}",
        "source_atom_ids": [f"QINV-{index:04d}"],
        "source_document_hash": "sha256:source",
        "source_kind": "exercise",
        "source_evidence": "source-evidence-" + ("x" * 5_000),
        "shared_context": "Use the supplied illustration.",
        "source_context": {
            "raw_text": "Complete original source question.",
            "source_answer": "Several valid representations.",
            "source_start": 101,
            "nested": {
                "semantic": "preserved",
                "row_number": 9,
            },
        },
        "image_manifest": [{
            "url": "https://example.test/solid.png",
            "caption": "A solid and its net",
            "printer_page": 18,
        }],
        "image_urls": ["https://example.test/solid.png"],
        "blueprint_cell_id": f"CELL-{index:04d}",
        "source_order": index,
    }
    candidate.update(changes)
    return candidate


def _response(request: dict, **changes) -> dict:
    response = {
        "candidate_id": request["candidate"]["candidate_id"],
        "answer_restriction": "Open",
        "restriction_reason": (
            "Materially different representations can earn full credit."
        ),
        "answer_space_contract": (
            "Any two valid representations with evidence-based justification."
        ),
        "required_elements": [
            "two valid representations",
            "a justification for each",
        ],
        "accepted_variations": [
            "face-based reasoning",
            "net-based reasoning",
        ],
        "evidence": (
            "The question and rubric explicitly reward different valid "
            "representations."
        ),
        "rationale": "The assessed method choice is materially variable.",
        "review_required": False,
        "review_reason": "",
    }
    response.update(changes)
    return response


def _verified(_request: dict) -> dict:
    return {"verdict": "verified", "confidence": 1.0, "issues": []}


def _decide(candidates=None, **overrides) -> list[dict]:
    arguments = {
        "meta": META,
        "envelope_sha256": ENVELOPE_SHA256,
        "provider": _response,
        "critic": _verified,
        "store": kernel.DecisionStore(),
    }
    arguments.update(overrides)
    return ar.decide_restrictions(
        list(candidates or [_candidate()]),
        **arguments,
    )


def _all_mapping_keys(value) -> list[str]:
    keys: list[str] = []
    if isinstance(value, dict):
        for key, nested in value.items():
            keys.append(str(key))
            keys.extend(_all_mapping_keys(nested))
    elif isinstance(value, list):
        for nested in value:
            keys.extend(_all_mapping_keys(nested))
    return keys


def test_complete_registry_and_candidate_are_passed_whole_and_content_hashed():
    seen = {}
    store = kernel.DecisionStore()

    def provider(request):
        seen.update(copy.deepcopy(request))
        return _response(request)

    verdict = _decide(
        provider=provider,
        store=store,
        meta={**META, "printer_page": 44, "semantic_meta": "preserved"},
    )[0]

    markdown_bytes = (
        DOCS / ar.REGISTRY_MARKDOWN_FILENAME
    ).read_bytes()
    workbook_bytes = (
        DOCS / ar.REGISTRY_WORKBOOK_FILENAME
    ).read_bytes()
    registry = seen["policy_registry"]
    assert seen["stage"] == "assessment.answer_restriction"
    assert "before the later marking allocation" in seen["rules"]
    assert "unweighted rubric evidence is complete" in seen["rules"]
    assert "later marking may not change the answer space" in (
        seen["critic_rules"]
    )
    assert registry["markdown_text"] == markdown_bytes.decode("utf-8")
    assert len(registry["markdown_text"]) > 50_000
    assert registry["markdown_sha256"] == hashlib.sha256(
        markdown_bytes
    ).hexdigest()
    assert registry["workbook_sha256"] == hashlib.sha256(
        workbook_bytes
    ).hexdigest()
    assert seen["metadata"] == {**META, "semantic_meta": "preserved"}
    assert seen["candidate"] == ar._content_evidence(_candidate())
    assert len(seen["candidate"]["source_evidence"]) > 5_000
    assert seen["candidate"]["source_context"]["nested"]["semantic"] == (
        "preserved"
    )
    assert not (
        set(_all_mapping_keys(seen["candidate"]))
        & ar._PRINT_POSITION_FIELDS
    )

    assert verdict["candidate_id"] == "CAND-0001"
    assert verdict["answer_restriction"] == "Open"
    assert verdict["answer_space_contract"]
    assert verdict["required_elements"] == [
        "two valid representations",
        "a justification for each",
    ]
    assert verdict["accepted_variations"]
    assert verdict["evidence"]
    assert verdict["rationale"]
    assert verdict["flags"] == []
    assert verdict["registry"] == {
        key: registry[key]
        for key in (
            "registry_id",
            "markdown_source",
            "markdown_sha256",
            "workbook_source",
            "workbook_sha256",
        )
    }
    authority = verdict["authority"]
    assert authority["decision_key"]
    assert authority["policy_version"].startswith(
        "assessment-answer-restriction-2;"
    )
    assert registry["markdown_sha256"] in authority["policy_version"]
    assert registry["workbook_sha256"] in authority["policy_version"]
    assert "created_at" not in authority
    assert "provider" not in authority
    stored = store.get(authority["decision_key"])
    assert stored is not None
    assert stored["kind"] == "assessment.answer_restriction"


@pytest.mark.parametrize(
    ("candidate", "restriction"),
    [
        (
            _candidate(
                1,
                sheet_kind="objective",
                question_category="Multiple Choice Question",
            ),
            "Open",
        ),
        (_candidate(2, sheet_kind="descriptive"), "Specific"),
    ],
)
def test_mechanical_contract_has_no_question_type_or_subject_default(
    candidate, restriction,
):
    verdict = _decide(
        [candidate],
        provider=lambda request: _response(
            request,
            answer_restriction=restriction,
            restriction_reason=(
                "The complete semantic answer-space evidence decides it."
            ),
        ),
    )[0]

    assert verdict["answer_restriction"] == restriction


def test_batch_preserves_exact_order_and_refuses_duplicate_ids_before_spend(
    monkeypatch,
):
    monkeypatch.setattr(ar.config, "phase3_decision_workers", lambda: 4)
    candidates = [_candidate(index) for index in (3, 1, 2)]
    verdicts = _decide(candidates)

    assert [row["candidate_id"] for row in verdicts] == [
        "CAND-0003",
        "CAND-0001",
        "CAND-0002",
    ]

    calls = []

    def forbidden(request):
        calls.append(request)
        return _response(request)

    with pytest.raises(ar.AnswerRestrictionError, match="repeat candidate_id"):
        _decide(
            [_candidate(), _candidate()],
            provider=forbidden,
            critic=None,
        )
    assert calls == []


def test_decide_once_replays_without_author_critic_or_fixer_calls():
    store = kernel.DecisionStore()
    calls = {"author": 0, "critic": 0}

    def provider(request):
        calls["author"] += 1
        return _response(request)

    def critic(request):
        calls["critic"] += 1
        return _verified(request)

    first = _decide(provider=provider, critic=critic, store=store)

    def forbidden(_request):
        raise AssertionError("cached restriction replay spent a provider call")

    second = _decide(
        provider=forbidden,
        critic=forbidden,
        fixer=forbidden,
        store=store,
    )

    assert second == first
    assert calls == {"author": 1, "critic": 1}
    assert len(store.keys()) == 1


def test_changed_registry_text_rekeys_the_decision(monkeypatch, tmp_path):
    markdown = (DOCS / ar.REGISTRY_MARKDOWN_FILENAME).read_bytes()
    workbook = (DOCS / ar.REGISTRY_WORKBOOK_FILENAME).read_bytes()
    (tmp_path / ar.REGISTRY_MARKDOWN_FILENAME).write_bytes(markdown)
    (tmp_path / ar.REGISTRY_WORKBOOK_FILENAME).write_bytes(workbook)
    monkeypatch.setattr(ar, "REGISTRY_DIRECTORY_CANDIDATES", (tmp_path,))
    store = kernel.DecisionStore()
    calls = []

    def provider(request):
        calls.append(copy.deepcopy(request["policy_registry"]))
        return _response(request)

    first = _decide(provider=provider, critic=None, store=store)[0]
    (tmp_path / ar.REGISTRY_MARKDOWN_FILENAME).write_bytes(
        markdown + b"\nregistry correction\n"
    )
    second = _decide(provider=provider, critic=None, store=store)[0]

    assert len(calls) == 2
    assert len(store.keys()) == 2
    assert calls[0]["markdown_sha256"] != calls[1]["markdown_sha256"]
    assert first["authority"]["decision_key"] != second["authority"][
        "decision_key"
    ]
    assert first["authority"]["policy_version"] != second["authority"][
        "policy_version"
    ]
    assert first["registry"]["workbook_sha256"] == second["registry"][
        "workbook_sha256"
    ]


def test_tightened_response_contract_cannot_replay_a_v1_decision(monkeypatch):
    store = kernel.DecisionStore()
    calls = []

    def provider(request):
        calls.append(request)
        return _response(request)

    monkeypatch.setattr(
        ar, "POLICY_BASE_VERSION", "assessment-answer-restriction-1"
    )
    stale = _decide(provider=provider, critic=None, store=store)[0]
    monkeypatch.setattr(
        ar, "POLICY_BASE_VERSION", "assessment-answer-restriction-2"
    )
    current = _decide(provider=provider, critic=None, store=store)[0]

    assert len(calls) == 2
    assert len(store.keys()) == 2
    assert stale["authority"]["decision_key"] != current["authority"][
        "decision_key"
    ]
    assert stale["authority"]["policy_version"].startswith(
        "assessment-answer-restriction-1;"
    )
    assert current["authority"]["policy_version"].startswith(
        "assessment-answer-restriction-2;"
    )


def test_critic_dissent_is_advisory_and_never_retries_authorship():
    calls = {"author": 0, "critic": 0}

    def provider(request):
        calls["author"] += 1
        return _response(request, answer_restriction="Specific")

    def critic(_request):
        calls["critic"] += 1
        return {
            "verdict": "dissent",
            "confidence": 1.0,
            "issues": ["The accepted-variation boundary deserves review."],
        }

    verdict = _decide(provider=provider, critic=critic)[0]

    assert calls == {"author": 1, "critic": 1}
    assert verdict["answer_restriction"] == "Specific"
    assert any("dissent" in flag for flag in verdict["flags"])
    assert any("accepted-variation" in flag for flag in verdict["flags"])
    assert verdict["authority"]["review_flags"] == verdict["flags"]


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
def test_malformed_or_subfloor_critic_never_gates_or_looks_clean(review):
    verdict = _decide(critic=lambda _request: copy.deepcopy(review))[0]

    assert verdict["answer_restriction"] == "Open"
    assert verdict["flags"]
    assert any("critic" in flag for flag in verdict["flags"])


def test_non_object_author_response_still_reaches_same_checker_fixer():
    author_calls = []
    fixer_calls = []

    def author(_request):
        author_calls.append(True)
        return [1]

    def fixer(request):
        fixer_calls.append(copy.deepcopy(request))
        return _response(request["original_payload"])

    verdict = _decide(provider=author, critic=None, fixer=fixer)[0]

    assert len(author_calls) == kernel.MAX_ATTEMPTS
    assert len(fixer_calls) == 1
    assert fixer_calls[0]["last_response"][
        "_aegis_invalid_response_type"
    ] == "list"
    assert verdict["authority"]["fixer"] is True


def test_author_best_verdict_ships_with_explicit_review_flag():
    verdict = _decide(
        provider=lambda request: _response(
            request,
            answer_restriction="Open",
            review_required=True,
            review_reason="The source rubric omits one equivalence boundary.",
        ),
    )[0]

    assert verdict["answer_restriction"] == "Open"
    assert verdict["review_required"] is True
    assert any("author requested" in flag for flag in verdict["flags"])


def test_raw_legacy_unresolved_language_cannot_create_a_third_value():
    author_calls = []
    fixer_calls = []

    def legacy_provider(request):
        author_calls.append(copy.deepcopy(request))
        return _response(
            request,
            answer_restriction="classification_unresolved",
            review_required=True,
            review_reason="Following the legacy disagreement line.",
        )

    def fixer(request):
        fixer_calls.append(copy.deepcopy(request))
        original = request["original_payload"]
        return _response(
            original,
            answer_restriction="Open",
            review_required=True,
            review_reason=(
                "Q10 requires a best verdict while retaining uncertainty."
            ),
        )

    verdict = _decide(
        provider=legacy_provider,
        critic=None,
        fixer=fixer,
    )[0]

    assert len(author_calls) == kernel.MAX_ATTEMPTS
    assert len(fixer_calls) == 1
    assert "classification_unresolved" in author_calls[0][
        "policy_registry"
    ]["markdown_text"]
    assert "There is no adjudicator" in author_calls[0]["rules"]
    assert verdict["answer_restriction"] == "Open"
    assert verdict["authority"]["fixer"] is True
    assert any(flag.startswith("fixer:") for flag in verdict["flags"])
    assert "classification_unresolved" not in str(verdict)


def test_fixer_is_revalidated_by_the_same_no_third_value_checker():
    def invalid(request):
        source = request.get("original_payload", request)
        return _response(
            source,
            answer_restriction="classification_unresolved",
            review_required=True,
            review_reason="Still unresolved.",
        )

    with pytest.raises(kernel.ContractError, match="answer_restriction"):
        _decide(provider=invalid, critic=None, fixer=invalid)


def test_latent_adjudicator_field_cannot_ride_beside_a_public_verdict():
    def invalid(request):
        source = request.get("original_payload", request)
        return _response(
            source,
            answer_restriction="Open",
            classification_unresolved=True,
        )

    with pytest.raises(kernel.ContractError, match="unexpected fields"):
        _decide(provider=invalid, critic=None, fixer=invalid)


def test_registry_is_fail_closed_and_never_replaced_by_embedded_evidence(
    monkeypatch, tmp_path,
):
    (tmp_path / ar.REGISTRY_MARKDOWN_FILENAME).write_text(
        "partial", encoding="utf-8"
    )
    monkeypatch.setattr(ar, "REGISTRY_DIRECTORY_CANDIDATES", (tmp_path,))
    calls = []

    def forbidden(request):
        calls.append(request)
        return _response(request)

    with pytest.raises(ar.AnswerRestrictionError, match="incomplete"):
        _decide(provider=forbidden, critic=None)
    assert calls == []


def test_both_runtime_images_package_the_single_root_registry_sources():
    copy_line = (
        "COPY docs/open-specific-registry-v2.md "
        "docs/open-specific-registry-v2.xlsx /app/docs/"
    )
    assert copy_line in (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert copy_line in (ROOT / "backend" / "Dockerfile").read_text(
        encoding="utf-8"
    )
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "context: ." in compose
    assert "dockerfile: backend/Dockerfile" in compose
    assert not (
        ROOT / "backend" / "docs" / ar.REGISTRY_MARKDOWN_FILENAME
    ).exists()
    assert not (
        ROOT / "backend" / "docs" / ar.REGISTRY_WORKBOOK_FILENAME
    ).exists()

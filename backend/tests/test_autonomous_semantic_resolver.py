"""Focused safety coverage for the one-shot semantic resolution agent."""
from __future__ import annotations

import json

import pytest

from app.services import autonomous_resolution as resolver
from app.services import semantic_confidence_policy as confidence_policy


def _pending(
    *,
    kind: str = "type_granularity_review",
    options: list[dict] | None = None,
    candidates: list[dict] | None = None,
) -> dict:
    return {
        "decision_id": "decision-0001",
        "context_hash": "a" * 64,
        "kind": kind,
        "phase": "concept_types",
        "conflict": "Two Type hosts remain plausible.",
        "diagnosis": "The source evidence supports one bounded choice.",
        "question": "How should TYPE-0001 be hosted?",
        "item": {
            "unit_id": "UNIT-0001",
            "type_id": "TYPE-0001",
            "type_title": "Compare causes",
            "topic": "The Making of Nationalism in Europe",
            "qids": ["QID-0001"],
            "questions": ["Compare the causes of nationalism in Europe."],
        },
        "candidates": candidates or [],
        "evidence": [{
            "label": "Verified source statement",
            "page": "7",
            "text": "Nationalism developed through shared political causes.",
        }],
        "options": options or [
            {
                "choice": "consolidate_types",
                "label": "Consolidate overlapping Types",
                "recommended": True,
            },
            {
                "choice": "keep_distinct_types",
                "label": "Keep the Types distinct",
                "recommended": False,
            },
            {
                "choice": "custom_instruction",
                "label": "Give another instruction",
                "recommended": False,
            },
        ],
    }


def _response(**overrides) -> dict:
    response = {
        "disposition": "apply",
        "choice": "consolidate_types",
        "target_id": "",
        "target_concept_id": "",
        "instruction": "",
        "confidence": 0.94,
        "reason": "The verified source statement supports one shared Type.",
        "evidence_refs": ["PENDING-EVIDENCE-001"],
        "uncertainties": [],
    }
    response.update(overrides)
    return response


def test_resolves_one_high_confidence_offered_action_with_one_provider_call(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv(
        confidence_policy.SEMANTIC_ACCEPTANCE_ENV, raising=False
    )
    calls: list[dict] = []

    def provider(**kwargs):
        calls.append(kwargs)
        return _response()

    monkeypatch.setattr(resolver.phase22, "_openai_multimodal_json", provider)

    result = resolver.resolve_pending(
        _pending(),
        source_text=(
            "TYPE-0001 is a comparison Type grounded in the chapter source."
        ),
        checkpoint={},
    )

    assert result.resolved is True
    assert result.choice == "consolidate_types"
    assert result.confidence == 0.94
    assert result.evidence_refs == ("PENDING-EVIDENCE-001",)
    assert len(calls) == 1

    call = calls[0]
    assert call["single_attempt"] is True
    assert call["purpose"] == "concept_validation"
    assert call["pages"] == []
    assert call["max_tokens"] == 3_000
    assert call["response_schema"]["strict"] is True
    schema = call["response_schema"]["schema"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    assert schema["properties"]["choice"]["enum"] == [
        "",
        "consolidate_types",
        "keep_distinct_types",
        "custom_instruction",
    ]
    packet = json.loads(call["prompt"])
    assert packet["constraints"]["choose_only_offered_action"] is True
    assert packet["constraints"]["abstain_on_uncertainty"] is True


def test_low_confidence_action_abstains(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv(
        confidence_policy.SEMANTIC_ACCEPTANCE_ENV, raising=False
    )
    calls = 0

    def provider(**_kwargs):
        nonlocal calls
        calls += 1
        return _response(confidence=0.91)

    result = resolver.resolve_pending(
        _pending(),
        source_text="TYPE-0001 appears in the verified source.",
        checkpoint={},
        provider=provider,
    )

    assert calls == 1
    assert result.status == "escalated"
    assert result.resolved is False
    assert result.confidence == 0.91


def test_invented_target_is_rejected_locally():
    pending = _pending(
        options=[{
            "choice": "select_candidate",
            "label": "Use an existing candidate",
            "recommended": True,
        }],
        candidates=[{
            "target_id": "TARGET-0001",
            "title": "Supported host",
        }],
    )

    result = resolver.resolve_pending(
        pending,
        source_text="TARGET-0001 is the verified host for TYPE-0001.",
        checkpoint={},
        provider=lambda **_kwargs: _response(
            choice="select_candidate",
            target_id="INVENTED-TARGET",
        ),
    )

    assert result.status == "escalated"
    assert "not a supplied candidate" in result.reason


@pytest.mark.parametrize(
    ("choice", "instruction"),
    [
        ("replace_source", ""),
        ("custom_instruction", "Merge anything that looks similar."),
    ],
)
def test_source_replacement_and_custom_instructions_require_the_user(
    choice: str,
    instruction: str,
):
    pending = _pending(options=[
        {
            "choice": "replace_source",
            "label": "Replace the source",
            "recommended": False,
        },
        {
            "choice": "custom_instruction",
            "label": "Give another instruction",
            "recommended": False,
        },
    ])

    result = resolver.resolve_pending(
        pending,
        source_text="TYPE-0001 appears in the verified source.",
        checkpoint={},
        provider=lambda **_kwargs: _response(
            choice=choice,
            instruction=instruction,
        ),
    )

    assert result.status == "escalated"
    assert "requires the user" in result.reason


def test_mmd_retrieval_finds_issue_evidence_at_the_source_tail():
    marker = (
        "TYPE-TAIL-999 compares the political causes of nationalism and "
        "must remain distinct."
    )
    source = ("Unrelated chapter material. " * 2_000) + marker
    pending = _pending()
    pending["item"]["type_id"] = "TYPE-TAIL-999"

    packet, evidence_refs = resolver.build_packet(
        pending,
        source_text=source,
        checkpoint={},
    )

    windows = packet["mmd_windows"]
    assert len(windows) == 1
    assert marker in windows[0]["text"]
    assert windows[0]["issue_match"] is True
    assert int(windows[0]["source_offsets"].split(":", 1)[0]) > 40_000
    assert windows[0]["evidence_id"] in evidence_refs


def test_opaque_target_identity_at_contract_limit_is_never_truncated():
    target_id = "TARGET-" + ("x" * (512 - len("TARGET-")))
    pending = _pending(
        options=[{
            "choice": "select_candidate",
            "label": "Use the exact candidate",
            "recommended": True,
            "target_id": target_id,
        }],
        candidates=[{
            "target_id": target_id,
            "title": "Exact source-supported candidate",
        }],
    )

    packet, evidence_refs = resolver.build_packet(
        pending,
        source_text="TYPE-0001 has one source-supported candidate.",
        checkpoint={},
    )
    schema = resolver._response_schema(
        packet["pending_decision"], evidence_refs
    )

    assert packet["pending_decision"]["options"][0]["target_id"] == target_id
    assert packet["pending_decision"]["candidates"][0]["target_id"] == target_id
    assert target_id in schema["schema"]["properties"]["target_id"]["enum"]


def test_issue_scope_ignores_regenerated_and_reordered_candidate_ids():
    first = _pending(candidates=[
        {
            "target_id": "TARGET-RUN-A-0001",
            "concept_id": "CONCEPT-RUN-A-0001",
            "title": "First plausible host",
        },
        {
            "target_id": "TARGET-RUN-A-0002",
            "concept_id": "CONCEPT-RUN-A-0002",
            "title": "Second plausible host",
        },
    ])
    regenerated = _pending(candidates=[
        {
            "target_id": "TARGET-RUN-B-9002",
            "concept_id": "CONCEPT-RUN-B-9002",
            "title": "Second plausible host",
        },
        {
            "target_id": "TARGET-RUN-B-9001",
            "concept_id": "CONCEPT-RUN-B-9001",
            "title": "First plausible host",
        },
    ])
    regenerated["decision_id"] = "decision-regenerated-0002"
    regenerated["context_hash"] = "b" * 64

    assert resolver.issue_key(regenerated) == resolver.issue_key(first)


def test_issue_scope_is_stable_across_resolver_version_deployments(
    monkeypatch: pytest.MonkeyPatch,
):
    pending = _pending(candidates=[{
        "target_id": "TARGET-0001",
        "concept_id": "CONCEPT-0001",
        "title": "Supported host",
    }])
    original_key = resolver.issue_key(pending)

    monkeypatch.setattr(
        resolver,
        "RESOLVER_VERSION",
        "semantic-resolution-agent-future-version",
    )

    assert resolver.issue_key(pending) == original_key


def test_huge_packet_stays_within_hard_json_budget_without_losing_ids():
    pending = _pending(
        options=[{
            "choice": "select_candidate",
            "label": "Use the verified target " + ("L" * 40_000),
            "recommended": True,
            "target_id": "TARGET-LARGE-0001",
        }],
        candidates=[{
            "target_id": "TARGET-LARGE-0001",
            "concept_id": "CONCEPT-LARGE-0001",
            "title": "Large verified candidate " + ("T" * 40_000),
            "topic": "Large Topic",
            "coverage": "C" * 40_000,
            "gap": "G" * 40_000,
        }],
    )
    pending["conflict"] = "Conflict " + ("X" * 80_000)
    pending["diagnosis"] = "Diagnosis " + ("D" * 80_000)
    pending["question"] = "Question " + ("Q" * 80_000)
    pending["item"].update({
        "type_id": "TYPE-LARGE-0001",
        "topic": "Large Topic",
        "questions": ["Inventory question " + ("I" * 40_000)],
    })
    pending["evidence"] = [{
        "label": "Large exact evidence",
        "page": "77",
        "text": "Verified source wording " + ("E" * 80_000),
    }]
    checkpoint = resolver.generation._make_concept_checkpoint(
        "description_method_snapshot",
        records=[{
            "topic": "Large Topic",
            "concept_title": f"Checkpoint concept {index}",
            "concept_details": "R" * 20_000,
            "source_evidence": "S" * 20_000,
        } for index in range(100)],
        method_row_snapshot=[],
    )
    source = (
        ("Unrelated full-source material. " * 10_000)
        + "TYPE-LARGE-0001 is supported by canonical source wording."
    )

    packet, evidence_refs = resolver.build_packet(
        pending,
        source_text=source,
        checkpoint=checkpoint,
    )

    choices = {
        row["choice"] for row in packet["pending_decision"]["options"]
    }
    target_ids = {
        row["target_id"]
        for row in packet["pending_decision"]["candidates"]
    }
    packet_evidence_ids = {
        row["evidence_id"]
        for key in ("source_evidence", "mmd_windows")
        for row in packet[key]
    }
    assert choices == {"select_candidate"}
    assert target_ids == {"TARGET-LARGE-0001"}
    assert "PENDING-EVIDENCE-001" in evidence_refs
    assert any(ref.startswith("MMD-WINDOW-") for ref in evidence_refs)
    assert evidence_refs <= packet_evidence_ids
    encoded = json.dumps(packet, ensure_ascii=False, default=str)
    assert len(encoded) <= resolver._MAX_PACKET_CHARS


def test_source_critical_action_requires_issue_matched_mmd_evidence():
    pending = _pending(kind="source_topic_coverage_review")

    result = resolver.resolve_pending(
        pending,
        source_text=(
            "This unrelated source contains none of the pending issue's "
            "identifiers, questions, candidates, or quoted evidence."
        ),
        checkpoint={},
        provider=lambda **_kwargs: _response(confidence=0.99),
    )

    assert result.status == "escalated"
    assert result.resolved is False


@pytest.mark.parametrize(
    ("kind", "phase", "choice"),
    [
        (
            "phase31_source_grounding_semantic_conflict",
            "3.1",
            "select_candidate",
        ),
        (
            "phase32_concept_blueprint_semantic_conflict",
            "3.2",
            "select_candidate",
        ),
        (
            "phase33_type_host_semantic_conflict",
            "3.3",
            "create_new",
        ),
    ],
)
def test_source_topology_and_new_concepts_use_source_critical_confidence(
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    phase: str,
    choice: str,
):
    monkeypatch.delenv(
        confidence_policy.SEMANTIC_ACCEPTANCE_ENV, raising=False
    )
    pending = _pending(
        kind=kind,
        options=[{
            "choice": choice,
            "label": "Apply the bounded source-supported action",
            "recommended": True,
            "target_id": (
                "TARGET-CRITICAL-0001"
                if choice == "select_candidate" else ""
            ),
        }],
        candidates=[{
            "target_id": "TARGET-CRITICAL-0001",
            "title": "Verified source-supported action",
        }],
    )
    pending["phase"] = phase

    result = resolver.resolve_pending(
        pending,
        source_text=(
            "TYPE-0001 and TARGET-CRITICAL-0001 are present in the "
            "canonical MMD source."
        ),
        checkpoint={},
        provider=lambda **_kwargs: _response(
            choice=choice,
            target_id=(
                "TARGET-CRITICAL-0001"
                if choice == "select_candidate" else ""
            ),
            confidence=0.94,
            evidence_refs=["MMD-WINDOW-001"],
        ),
    )

    assert result.status == "escalated"
    assert result.resolved is False

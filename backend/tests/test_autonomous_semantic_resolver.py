"""Focused safety coverage for the one-shot semantic resolution agent."""
from __future__ import annotations

import hashlib
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


def test_unique_verified_working_source_patch_resolves_without_provider_call():
    material = {
        "version": "phase3-canonical-topic-patch-1",
        "kind": "canonical_topic_binding",
        "target": "working_derived_source",
        "raw_source_mutated": False,
        "source_contract_hash": "1" * 64,
        "semantic_context_hash": "2" * 64,
        "before_sha256": "3" * 64,
        "after_sha256": "4" * 64,
        "operations": [
            "Restore numbered main topic 2 The Making of Nationalism in Europe"
        ],
    }
    patch_hash = hashlib.sha256(json.dumps(
        material,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    target_id = f"canonical-topic-patch-{patch_hash[:24]}"
    pending = _pending(
        kind="phase3_source_graph_review",
        candidates=[{
            "target_id": target_id,
            "title": "Verified working-source patch",
        }],
        options=[{
            "choice": "accept_recommended",
            "label": "Apply the verified working-source patch",
            "recommended": True,
            "target_id": target_id,
        }],
    )
    pending["phase"] = "phase3_source_graph"
    pending["item"]["type_id"] = "numbered_main_topic_coverage"
    pending["source_patch"] = {
        **material,
        "verified": True,
        "patch_hash": patch_hash,
        "target_id": target_id,
        "before": "## The French Revolution and the Idea of the Nation",
        "after": "## 2 The Making of Nationalism in Europe",
    }

    def unexpected_provider(**_kwargs):
        raise AssertionError("a sealed deterministic patch must not call GPT")

    result = resolver.resolve_pending(
        pending,
        source_text="The immutable raw MMD remains available.",
        checkpoint={},
        provider=unexpected_provider,
    )

    assert result.resolved is True
    assert result.choice == "accept_recommended"
    assert result.target_id == target_id
    assert result.confidence == 1.0
    assert result.evidence_refs == (
        f"CANONICAL-PATCH-{patch_hash[:24].upper()}",
    )


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
    packet_candidates = resolver._model_candidate_rows(
        packet["pending_decision"]
    )
    assert packet_candidates[0]["target_id"] == target_id
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
    packet_candidates = resolver._model_candidate_rows(
        packet["pending_decision"]
    )
    target_ids = {row["target_id"] for row in packet_candidates}
    packet_evidence_ids = {
        row["evidence_id"]
        for key in ("source_evidence", "mmd_windows")
        for row in packet[key]
    }
    packet_evidence_ids.update(
        row["binding_hash"] for row in packet_candidates
    )
    assert choices == {"select_candidate"}
    assert target_ids == {"TARGET-LARGE-0001"}
    assert "PENDING-EVIDENCE-001" in evidence_refs
    assert any(ref.startswith("MMD-WINDOW-") for ref in evidence_refs)
    assert evidence_refs <= packet_evidence_ids
    encoded = json.dumps(packet, ensure_ascii=False, default=str)
    assert len(encoded) <= resolver._MAX_PACKET_CHARS


def test_candidate_beyond_expanded_detail_is_selectable_from_complete_catalog():
    candidates = [{
        "target_id": f"TARGET-{index:04d}",
        "title": f"Use verified evidence BLK-{index:04d}",
        "coverage": (
            "Unrelated source block."
            if index != 12
            else "Cavour engineered a diplomatic alliance with France."
        ),
    } for index in range(1, 15)]
    pending = _pending(
        kind="phase31_source_grounding_semantic_conflict",
        options=[{
            "choice": "select_candidate",
            "label": "Select verified evidence",
            "recommended": True,
        }],
        candidates=candidates,
    )
    pending["phase"] = "3.1"
    pending["conflict"] = "One disputed claim needs exact canonical support."

    def provider(*, packet, **_kwargs):
        catalog = resolver._model_candidate_rows(packet["pending_decision"])
        assert len(catalog) == 14
        selected = catalog[11]
        assert selected["target_id"] not in {
            row["target_id"]
            for row in packet["pending_decision"]["candidate_details"]
        }
        assert [11, ["MMD-WINDOW-001"]] in packet[
            "pending_decision"
        ]["legacy_exact_source_matches"]["rows"]
        mmd_ref = next(
            row["evidence_id"]
            for row in packet["mmd_windows"]
            if row["issue_match"] is True
        )
        return _response(
            choice="select_candidate",
            target_id=selected["target_id"],
            confidence=0.99,
            evidence_refs=[selected["binding_hash"], mmd_ref],
        )

    result = resolver.resolve_pending(
        pending,
        source_text=(
            "BLK-0012 Cavour engineered a diplomatic alliance with France."
        ),
        checkpoint={},
        provider=provider,
    )

    assert result.resolved is True
    assert result.target_id == "TARGET-0012"


def test_legacy_exact_match_map_uses_only_final_transmitted_mmd_text():
    coverage = (
        "Through a diplomatic alliance with France engineered by Cavour, "
        "Sardinia-Piedmont defeated Austria."
    )
    pending = _pending(candidates=[{
        "target_id": "TARGET-CAVOUR",
        "title": "Use verified evidence BLK-00249",
        "coverage": coverage,
    }])
    full = resolver._legacy_exact_source_matches(
        pending,
        [{
            "evidence_id": "MMD-WINDOW-001",
            "issue_match": True,
            "text": f"BLK-00249 {coverage}",
        }],
    )
    truncated = resolver._legacy_exact_source_matches(
        pending,
        [{
            "evidence_id": "MMD-WINDOW-001",
            "issue_match": True,
            "text": "BLK-00249 Through a diplomatic alliance with France",
        }],
    )

    assert full["rows"] == [[0, ["MMD-WINDOW-001"]]]
    assert truncated["rows"] == []


def test_legacy_blk_text_must_match_and_cite_exact_canonical_window():
    pending = _pending(
        kind="phase31_source_grounding_semantic_conflict",
        options=[{
            "choice": "select_candidate",
            "label": "Select bound source evidence",
            "recommended": True,
        }],
        candidates=[{
            "target_id": "TARGET-CAVOUR",
            "title": "Use verified evidence BLK-00249",
            "coverage": (
                "Through a diplomatic alliance with France engineered by "
                "Cavour, Sardinia-Piedmont defeated Austria."
            ),
        }],
    )
    pending["phase"] = "3.1"
    pending["conflict"] = (
        "The critic mentioned BLK-00249 for Cavour's alliance."
    )

    def provider(*, packet, **_kwargs):
        candidate = resolver._model_candidate_rows(
            packet["pending_decision"]
        )[0]
        mmd_ref = next(
            row["evidence_id"] for row in packet["mmd_windows"]
            if row["issue_match"] is True
        )
        return _response(
            choice="select_candidate",
            target_id=candidate["target_id"],
            confidence=0.99,
            evidence_refs=[candidate["binding_hash"], mmd_ref],
        )

    rejected = resolver.resolve_pending(
        pending,
        source_text=(
            "BLK-00249 appears in the conversion index, but this source "
            "window contains no diplomatic-alliance text."
        ),
        checkpoint={},
        provider=provider,
    )
    assert rejected.status == "escalated"
    assert "exact saved text" in rejected.reason

    accepted = resolver.resolve_pending(
        pending,
        source_text=(
            "BLK-00249 Through a diplomatic alliance with France engineered "
            "by Cavour, Sardinia-Piedmont defeated Austria."
        ),
        checkpoint={},
        provider=provider,
    )
    assert accepted.resolved is True


def test_all_topology_actions_remain_visible_after_many_evidence_candidates():
    evidence = [{
        "target_id": f"3.1:evidence:{index:04d}",
        "title": f"Use verified evidence BLK-{index:05d}",
        "coverage": f"Bound source text for block {index}.",
    } for index in range(1, 91)]
    topology = [{
        "target_id": f"3.1:topology:{action}",
        "concept_id": "CONCEPT-0001",
        "action": action,
        "title": f"{action.title()} the source claim",
        "topic": "The Making of Nationalism in Europe",
        "coverage": "The complete source claim.",
        "gap": f"Return to topology for {action}.",
    } for action in ("refine", "split", "move", "retire")]
    pending = _pending(
        kind="phase31_source_grounding_semantic_conflict",
        options=[{
            "choice": "select_candidate",
            "label": "Select evidence or topology repair",
            "recommended": True,
        }],
        candidates=[*evidence, *topology],
    )
    pending["phase"] = "3.1"

    packet, evidence_refs = resolver.build_packet(
        pending,
        source_text=(
            "CONCEPT-0001 belongs to The Making of Nationalism in Europe."
        ),
        checkpoint={},
    )
    catalog = resolver._model_candidate_rows(packet["pending_decision"])
    schema = resolver._response_schema(
        packet["pending_decision"], evidence_refs
    )

    assert len(catalog) == 94
    assert {row["action"] for row in catalog} >= {
        "refine", "split", "move", "retire",
    }
    assert {
        row["target_id"] for row in catalog
    } <= set(schema["schema"]["properties"]["target_id"]["enum"])
    assert packet["pending_decision"]["candidates"]["count"] == 94
    assert packet["pending_decision"]["candidates"]["complete"] is True
    assert len(json.dumps(packet, ensure_ascii=False)) <= resolver._MAX_PACKET_CHARS


def test_full_100_row_bound_catalog_keeps_top_relevant_text_under_cap():
    candidates = []
    for index in range(95):
        block_id = f"BLK-{index:05d}"
        coverage = f"Verified canonical paragraph {index} about nationalism."
        binding = resolver.early_semantic_gate.bind_candidate({
            "action": "use_verified_evidence",
            "source_block_ids": [block_id],
            "source_topic_id": "TOPIC-0002",
            "target_topic_id": "TOPIC-0002",
            "boundary_relation": "within_fixed_source_topic",
            "source_kind": "paragraph",
            "source_page": str(index + 1),
            "text_sha256": hashlib.sha256(
                coverage.encode("utf-8")
            ).hexdigest(),
        })
        candidates.append({
            "target_id": (
                "3.1:use_verified_evidence:"
                + hashlib.sha256(block_id.encode("utf-8")).hexdigest()
            ),
            "title": f"Use verified evidence {block_id}",
            "topic": "The Making of Nationalism in Europe",
            "coverage": coverage,
            "gap": "Mapper and critic must still verify this binding.",
            **binding,
        })
    for action in ("refine", "split", "retire", "move", "keep"):
        binding = resolver.early_semantic_gate.bind_candidate({
            "action": action,
            "source_block_ids": [],
            "source_topic_id": "TOPIC-0002",
            "target_topic_id": (
                "TOPIC-0003" if action == "move" else "TOPIC-0002"
            ),
            "boundary_relation": f"{action}_source_claim",
            "source_kind": "",
            "source_page": "",
            "text_sha256": "",
        })
        candidates.append({
            "target_id": (
                f"3.1:{action}:"
                + hashlib.sha256(action.encode("utf-8")).hexdigest()
            ),
            "concept_id": "CONCEPT-0001",
            "title": f"{action.title()} the complete source claim",
            "topic": "The Making of Nationalism in Europe",
            "coverage": "The complete disputed source claim.",
            "gap": "Independent verification remains mandatory.",
            **binding,
        })
    pending = _pending(
        kind="phase31_source_grounding_semantic_conflict",
        options=[{
            "choice": "select_candidate",
            "label": "Select evidence or a topology repair",
            "recommended": True,
        }],
        candidates=candidates,
    )
    pending["phase"] = "3.1"
    pending["conflict"] = "The critic mentioned BLK-00094."

    packet, _refs = resolver.build_packet(
        pending,
        source_text=(
            "BLK-00094 Verified canonical paragraph 94 about nationalism."
        ),
        checkpoint={},
    )
    catalog = resolver._model_candidate_rows(packet["pending_decision"])
    details = packet["pending_decision"]["candidate_details"]

    assert len(catalog) == 100
    assert details
    assert details[0]["target_id"] == candidates[94]["target_id"]
    assert "paragraph 94" in details[0]["coverage"]
    assert len(json.dumps(packet, ensure_ascii=False)) <= resolver._MAX_PACKET_CHARS


def test_critic_block_mention_exposes_bound_text_but_is_not_recommended():
    def bound_candidate(
        *, target_id: str, block_id: str, page: str, coverage: str
    ) -> dict:
        binding = resolver.early_semantic_gate.bind_candidate({
            "action": "use_verified_evidence",
            "source_block_ids": [block_id],
            "source_topic_id": "TOPIC-ITALY",
            "target_topic_id": "TOPIC-ITALY",
            "boundary_relation": "within_fixed_source_topic",
            "source_kind": "paragraph",
            "source_page": page,
            "text_sha256": hashlib.sha256(coverage.encode("utf-8")).hexdigest(),
        })
        return {
            "target_id": target_id,
            "title": f"Use verified evidence {block_id}",
            "topic": "The Making of Nationalism in Europe",
            "coverage": coverage,
            **binding,
        }

    candidates = [bound_candidate(
        target_id="TARGET-CAVOUR",
        block_id="BLK-00249",
        page="16",
        coverage=(
            "Through a diplomatic alliance with France engineered by Cavour, "
            "Sardinia-Piedmont defeated Austria."
        ),
    ), bound_candidate(
        target_id="TARGET-LAYOUT",
        block_id="BLK-00256",
        page="17",
        coverage=(
            "Activity: locate Sardinia-Piedmont on the map and discuss the "
            "layout illustration."
        ),
    )]
    pending = _pending(
        kind="phase31_source_grounding_semantic_conflict",
        options=[{
            "choice": "select_candidate",
            "label": "Select verified evidence",
            "recommended": True,
        }],
        candidates=candidates,
    )
    pending["phase"] = "3.1"
    pending["conflict"] = (
        "The critic mentioned BLK-00256 while discussing Cavour's alliance."
    )

    packet, _refs = resolver.build_packet(
        pending,
        source_text=(
            "BLK-00249 Through a diplomatic alliance with France engineered "
            "by Cavour, Sardinia-Piedmont defeated Austria. BLK-00256 Activity: "
            "locate Sardinia-Piedmont on the map."
        ),
        checkpoint={},
    )
    details = packet["pending_decision"]["candidate_details"]
    layout = next(row for row in details if row["target_id"] == "TARGET-LAYOUT")
    catalog_layout = next(
        row for row in resolver._model_candidate_rows(packet["pending_decision"])
        if row["target_id"] == "TARGET-LAYOUT"
    )

    assert "Activity:" in layout["coverage"]
    assert "not" in layout["retrieval_note"].casefold()
    assert layout["binding_hash"] == catalog_layout["binding_hash"]
    assert catalog_layout["source_block_ids"] == ["BLK-00256"]
    assert catalog_layout["source_topic_id"] == "TOPIC-ITALY"
    assert catalog_layout["target_topic_id"] == "TOPIC-ITALY"
    assert catalog_layout["boundary_relation"] == "within_fixed_source_topic"
    assert catalog_layout["source_kind"] == "paragraph"
    assert catalog_layout["source_page"] == "17"
    assert catalog_layout["text_sha256"] == hashlib.sha256(
        candidates[1]["coverage"].encode("utf-8")
    ).hexdigest()
    assert catalog_layout["binding_hash"] == candidates[1]["binding_hash"]
    assert catalog_layout["server_binding_valid"] is True
    assert "recommended" not in catalog_layout
    assert packet["constraints"][
        "critic_id_mentions_are_retrieval_priority_not_support"
    ] is True


def test_many_full_opaque_ids_survive_packet_cap_and_schema():
    target_ids = [
        f"TARGET-{index:03d}-" + (chr(97 + index % 26) * 180)
        for index in range(60)
    ]
    pending = _pending(
        options=[{
            "choice": "select_candidate",
            "label": "Select a candidate",
            "recommended": True,
        }],
        candidates=[{
            "target_id": target_id,
            "concept_id": f"CONCEPT-{index:03d}",
            "title": f"Candidate {index}",
            "coverage": f"Exact coverage {index}",
        } for index, target_id in enumerate(target_ids)],
    )

    packet, evidence_refs = resolver.build_packet(
        pending,
        source_text="TYPE-0001 exact source context.",
        checkpoint={},
    )
    schema = resolver._response_schema(
        packet["pending_decision"], evidence_refs
    )
    packet_ids = [
        row["target_id"]
        for row in resolver._model_candidate_rows(packet["pending_decision"])
    ]

    assert packet_ids == target_ids
    assert set(target_ids) <= set(
        schema["schema"]["properties"]["target_id"]["enum"]
    )
    assert len(json.dumps(packet, ensure_ascii=False)) <= resolver._MAX_PACKET_CHARS


def test_capability_key_tracks_full_binding_but_not_candidate_order():
    first = _pending(candidates=[{
        "target_id": "TARGET-A",
        "title": "Use verified evidence BLK-0001",
        "coverage": "First exact bound text.",
    }, {
        "target_id": "TARGET-B",
        "title": "Use verified evidence BLK-0002",
        "coverage": "Second exact bound text.",
    }])
    reordered = {**first, "candidates": list(reversed(first["candidates"]))}
    changed = {
        **first,
        "candidates": [
            first["candidates"][0],
            {**first["candidates"][1], "coverage": "Changed bound text."},
        ],
    }

    assert resolver.RESOLVER_VERSION == "semantic-resolution-agent-2"
    assert resolver.capability_key(first) == resolver.capability_key(reordered)
    assert resolver.capability_key(first) != resolver.capability_key(changed)


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

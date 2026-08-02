import copy
import hashlib
import json
import threading

import pytest

from app import models
from app.db import SessionLocal
from app.services import (
    autonomous_resolution,
    auth,
    build_concepts,
    canonical_source_phase33_preflight_contract as phase33,
    checkpoints,
    early_semantic_gate,
    generation,
    semantic_recovery,
    uploads,
)


def _pending_packet() -> dict:
    context_hash = "a" * 64
    return {
        "decision_id": f"phase33-host-{context_hash[:24]}",
        "context_hash": context_hash,
        "kind": "phase33_type_host_semantic_conflict",
        "phase": "3.3",
        "conflict": (
            "The existing Renan concept covers attributes but not why "
            "nations safeguard liberty."
        ),
        "item": {
            "unit_id": "TYPE-0002",
            "type_id": "TYPE-0002",
            "type_title": "Explain Renan's View of Nations",
            "qids": ["QINV-0002"],
            "questions": [
                "Why, in Renan's view, are nations important?",
            ],
            "topic": {
                "topic_id": "TOPIC-0001",
                "title": "The French Revolution and the Idea of the Nation",
            },
        },
        "candidates": [{
            "concept_id": "HOST-CONCEPT-0001",
            "title": "Ernest Renan's Idea of a Nation",
            "topic": "The French Revolution and the Idea of the Nation",
            "coverage": "Attributes of nationhood.",
            "gap": "Nations as guarantees of liberty.",
        }],
        "evidence": [{
            "page": 8,
            "label": "EVIDENCE-PAGE-C",
            "text": "Their existence is a guarantee of liberty.",
        }],
        "deferred_assignment_unit_ids": ["TYPE-0003"],
        "options": [
            {
                "choice": "expand_existing",
                "label": "Expand the existing concept",
                "recommended": True,
                "target_concept_id": "HOST-CONCEPT-0001",
                "directed_update": {"preserve_concept_identity": True},
            },
            {
                "choice": "create_new",
                "label": "Create a separate concept",
                "recommended": False,
            },
            {
                "choice": "select_existing",
                "label": "Select another existing concept",
                "recommended": False,
            },
            {
                "choice": "custom",
                "label": "Give a custom instruction",
                "recommended": False,
            },
        ],
    }


def _early_blueprint_pending_packet() -> dict:
    context_hash = "b" * 64
    target_id = "3.2:refine:" + "c" * 64
    return {
        "decision_id": f"phase32-blueprint-{context_hash[:24]}",
        "context_hash": context_hash,
        "kind": "phase32_concept_blueprint_semantic_conflict",
        "phase": "3.2",
        "conflict": "The independent critic rejected an overbroad source claim.",
        "diagnosis": "The first semantic review found a source discrepancy.",
        "decision_question": "How should this concept be repaired?",
        "item": {
            "unit_id": "TOPOLOGY-CONCEPT-0002",
            "type_id": "",
            "type_title": "Nation-State Definition",
            "qids": [],
            "questions": ["A nation-state is a centralised sovereign state."],
            "topic": "The Making of Nationalism in Europe",
        },
        "candidates": [{
            "target_id": target_id,
            "concept_id": "",
            "title": "Refine this concept to its verified source claim",
            "topic": "The Making of Nationalism in Europe",
            "coverage": "Citizens develop a common identity.",
            "gap": "Remove the unsupported centralised-sovereign wording.",
        }],
        "evidence": [{
            "page": "6",
            "label": "TOPIC-0002",
            "text": "Citizens developed a sense of common identity.",
        }],
        "deferred_assignment_unit_ids": [],
        "options": [
            {
                "choice": "accept_recommended",
                "label": "Use GPT's recommended source-supported action",
                "recommended": True,
                "target_id": target_id,
            },
            {
                "choice": "select_candidate",
                "label": "Choose another bounded action",
                "recommended": False,
            },
            {
                "choice": "replace_source",
                "label": "Correct or replace the source",
                "recommended": False,
            },
            {
                "choice": "custom_instruction",
                "label": "Give a custom instruction",
                "recommended": False,
            },
        ],
    }


def _phase31_topology_pending_packet() -> dict:
    context_hash = "e" * 64
    candidate = early_semantic_gate.bind_candidate({
        "target_id": "3.1:topology:refine:" + ("e" * 32),
        "concept_id": "TOPOLOGY-CONCEPT-0002",
        "action": "refine",
        "title": "Refine the unsupported source claim",
        "topic": "The Making of Nationalism in Europe",
        "coverage": "Cavour secured a diplomatic alliance with France.",
        "gap": "Remove the broader unsupported wording.",
        "source_topic_id": "TOPIC-0002",
        "target_topic_id": "TOPIC-0002",
        "boundary_relation": "same_topic",
        "source_kind": "topology_repair",
    })
    return {
        "decision_id": f"phase31-ground-{context_hash[:24]}",
        "context_hash": context_hash,
        "kind": "phase31_source_grounding_semantic_conflict",
        "phase": "3.1",
        "conflict": "The original claim is broader than its verified source.",
        "diagnosis": "One bounded topology refinement is source-supported.",
        "decision_question": "How should the claim return to topology?",
        "item": {
            "unit_id": "TOPOLOGY-CONCEPT-0002",
            "type_id": "",
            "type_title": "Cavour's diplomatic mechanism",
            "qids": [],
            "questions": [
                "Cavour secured a diplomatic alliance with France."
            ],
            "topic": "The Making of Nationalism in Europe",
        },
        "candidates": [candidate],
        "evidence": [{
            "evidence_id": "MMD-WINDOW-001",
            "page": "10",
            "label": "Verified source window",
            "text": "Cavour secured a diplomatic alliance with France.",
        }],
        "deferred_assignment_unit_ids": [],
        "options": [
            {
                "choice": "select_candidate",
                "label": "Select evidence or a topology repair",
                "recommended": True,
            },
            {
                "choice": "replace_source",
                "label": "Correct or replace the source",
                "recommended": False,
            },
            {
                "choice": "custom_instruction",
                "label": "Give a custom instruction",
                "recommended": False,
            },
        ],
    }


def _working_source_patch_pending_packet() -> dict:
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
    return {
        "decision_id": "phase3-source-" + ("d" * 24),
        "context_hash": "d" * 64,
        "kind": "phase3_source_graph_review",
        "phase": "3",
        "conflict": "A numbered main topic is missing from the working graph.",
        "diagnosis": "One deterministic canonical-source repair is verified.",
        "decision_question": "Apply the verified working-source patch?",
        "item": {
            "unit_id": "CANONICAL-TOPIC-SPINE",
            "type_id": "numbered_main_topic_coverage",
            "type_title": "Working-source topic patch",
            "qids": [],
            "questions": ["2 The Making of Nationalism in Europe"],
            "topic": "Canonical chapter topic spine",
        },
        "candidates": [{
            "target_id": target_id,
            "concept_id": target_id,
            "title": "Repair the working MMD topic spine",
            "topic": "Canonical chapter topic spine",
            "coverage": "Restores numbered main topic 2.",
            "gap": "The working topic spine omitted topic 2.",
        }],
        "evidence": [{
            "evidence_id": "CANONICAL-PATCH-" + patch_hash[:24].upper(),
            "page": "",
            "label": "Verified patched topic spine",
            "text": "## 2 The Making of Nationalism in Europe",
        }],
        "deferred_assignment_unit_ids": [],
        "options": [{
            "choice": "accept_recommended",
            "label": "Apply the verified working-source patch",
            "recommended": True,
            "target_id": target_id,
        }],
        "source_patch": {
            **material,
            "verified": True,
            "patch_hash": patch_hash,
            "target_id": target_id,
            "before": "## 1 The French Revolution",
            "after": "## 1 The French Revolution\n## 2 The Making of Nationalism in Europe",
        },
    }


def _job_at_81_percent(db, first_chapter, *, learning_kind="post"):
    chapter = db.get(models.Chapter, first_chapter["id"])
    job = models.UploadJob(
        owner_sub=auth.LOCAL_OWNER_SUB,
        module="build_concepts",
        upload_type="document",
        learning_kind=learning_kind,
        filename="rne.mmd",
        mmd_text="# Renan\nNations are a guarantee of liberty.",
        status="converted",
        question_inventory={"items": [], "stats": {}, "mined_types": []},
    )
    db.add(job)
    db.flush()
    stage = generation._make_concept_checkpoint(
        "pre_type_assignment",
        records=[{
            "topic": "The French Revolution",
            "parent_concept": "Nationalism",
            "concept_title": "Ernest Renan's Idea of a Nation",
            "concept_details": "Description: Attributes of a nation.",
            "keywords": "Renan, nation",
        }],
        question_task_inventory={"items": [], "stats": {}},
        mined_types={"types": []},
        method_row_snapshot=[],
    )
    job.generation_checkpoint = (
        build_concepts._merge_generation_checkpoint_history(
            {},
            stage,
            fingerprint=build_concepts._generation_checkpoint_fingerprint(
                job, chapter),
            target_identity=build_concepts._generation_target_identity(chapter),
            target_chapter_id=chapter.id,
        )
    )
    db.commit()
    db.refresh(job)
    return job, chapter


def _attach_pending(db, job, chapter):
    fingerprint = job.generation_checkpoint["fingerprint"]
    return build_concepts._persist_pending_human_decision(
        db,
        job,
        _pending_packet(),
        fingerprint=fingerprint,
        target_chapter_id=chapter.id,
        owner_sub=auth.LOCAL_OWNER_SUB,
    )


def test_generation_pause_preserves_81_percent_checkpoint_and_skips_reentry(
    db,
    first_chapter,
    monkeypatch,
):
    job, chapter = _job_at_81_percent(db, first_chapter)
    calls = 0

    def require_human(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise semantic_recovery.HumanDecisionRequired(_pending_packet())

    monkeypatch.setattr(
        build_concepts.config, "use_live_generation", lambda: True)
    monkeypatch.setattr(
        build_concepts.generation, "concepts_from_mmd", require_human)
    monkeypatch.setattr(
        build_concepts.drive_checkpoints,
        "schedule_checkpoint_backup",
        lambda *_args, **_kwargs: None,
    )

    result = build_concepts.generate_post_learning(
        db, job.id, chapter.id, owner_sub=auth.LOCAL_OWNER_SUB)

    assert result["status"] == "awaiting_decision"
    assert result["pending_decision"]["decision_id"].startswith("phase33-host-")
    assert result["pending_decision"]["options"][-1]["choice"] == (
        "custom_instruction")
    assert calls == 1
    db.refresh(job)
    assert job.status == "converted"
    assert job.generation_checkpoint["stage"] == "pre_type_assignment"
    assert job.checkpoint_progress == pytest.approx(0.81)
    assert job.awaiting_decision is True

    repeated = build_concepts.generate_post_learning(
        db, job.id, chapter.id, owner_sub=auth.LOCAL_OWNER_SUB)
    assert repeated["status"] == "awaiting_decision"
    assert calls == 1


def test_clear_pause_is_agent_resolved_and_continues_same_run(
    db,
    first_chapter,
    monkeypatch,
):
    job, chapter = _job_at_81_percent(db, first_chapter)
    identity = {
        "decision_id": _pending_packet()["decision_id"],
        "context_hash": _pending_packet()["context_hash"],
    }
    generation_calls = 0
    resolver_calls = 0

    def generate(*_args, **_kwargs):
        nonlocal generation_calls
        generation_calls += 1
        resolution = phase33._human_resolution_for(identity)
        if resolution is None:
            raise semantic_recovery.HumanDecisionRequired(_pending_packet())
        assert resolution["choice"] == "expand_existing"
        assert resolution["target_concept_id"] == "HOST-CONCEPT-0001"
        return [{
            "topic": "The French Revolution",
            "parent_concept": "Nationalism",
            "concept_title": "Ernest Renan's Idea of a Nation",
            "concept_details": "Description: Nations safeguard liberty.",
            "keywords": "Renan, nation, liberty",
        }]

    def resolve(*_args, **_kwargs):
        nonlocal resolver_calls
        resolver_calls += 1
        # The request-started marker must be durable before the paid call.
        db.expire_all()
        saved = db.get(models.UploadJob, job.id)
        review = saved.pending_decision["agent_review"]
        assert review["status"] == "request_started"
        return autonomous_resolution.ResolutionResult(
            status="resolved",
            reason="The supplied liberty evidence uniquely expands Renan.",
            confidence=0.97,
            evidence_refs=("PENDING-EVIDENCE-001",),
            choice="expand_existing",
            target_concept_id="HOST-CONCEPT-0001",
        )

    monkeypatch.setattr(build_concepts.config, "use_live_generation", lambda: True)
    monkeypatch.setattr(
        build_concepts.autonomous_resolution, "enabled", lambda: True
    )
    monkeypatch.setattr(
        build_concepts.autonomous_resolution, "resolve_pending", resolve
    )
    monkeypatch.setattr(build_concepts.generation, "concepts_from_mmd", generate)
    monkeypatch.setattr(
        build_concepts,
        "_deposit_and_publish_concepts",
        lambda *_args, **_kwargs: (
            [902], [],
            {"written": 1, "sources_updated": 0, "parent_column": True},
        ),
    )
    monkeypatch.setattr(
        build_concepts.drive_checkpoints,
        "schedule_checkpoint_backup",
        lambda *_args, **_kwargs: None,
    )

    result = build_concepts.generate_post_learning(
        db, job.id, chapter.id, owner_sub=auth.LOCAL_OWNER_SUB
    )

    assert result["concept_ids"] == [902]
    assert generation_calls == 2
    assert resolver_calls == 1
    db.refresh(job)
    assert job.status == "generated"
    assert job.pending_decision is None


def test_agent_selected_phase31_topology_action_is_consumed_and_continues(
    db,
    first_chapter,
    monkeypatch,
):
    job, chapter = _job_at_81_percent(db, first_chapter)
    packet = _phase31_topology_pending_packet()
    candidate = packet["candidates"][0]
    identity = {
        "decision_id": packet["decision_id"],
        "context_hash": packet["context_hash"],
    }
    generation_calls = 0
    resolver_calls = 0
    consumed_directives: list[dict] = []

    def generate(*_args, **_kwargs):
        nonlocal generation_calls
        generation_calls += 1
        resolution = early_semantic_gate.resolution_for(
            identity=identity,
            kind=packet["kind"],
            phase=packet["phase"],
            unit_id=packet["item"]["unit_id"],
            candidates=packet["candidates"],
        )
        if resolution is None:
            raise semantic_recovery.HumanDecisionRequired(packet)
        assert resolution["choice"] == "select_candidate"
        assert resolution["instruction"] == ""
        assert resolution["selected_candidate"]["action"] == "refine"
        db.expire_all()
        saved = db.get(models.UploadJob, job.id)
        durable = saved.generation_checkpoint["human_decisions"][
            "resolutions"
        ][0]
        assert durable["status"] == "consumed"
        assert durable["resolved_by"] == "agent"
        consumed_directives.append(copy.deepcopy(durable))
        return [{
            "topic": "The Making of Nationalism in Europe",
            "parent_concept": "Unification of Italy",
            "concept_title": "Cavour's Diplomacy",
            "concept_details": (
                "Description: Cavour secured a diplomatic alliance with "
                "France."
            ),
            "keywords": "Cavour, diplomacy, France",
        }]

    def resolve(*_args, **_kwargs):
        nonlocal resolver_calls
        resolver_calls += 1
        return autonomous_resolution.ResolutionResult(
            status="resolved",
            reason=(
                "The exact source uniquely supports the bounded refinement."
            ),
            confidence=0.97,
            evidence_refs=(
                "MMD-WINDOW-001",
                candidate["binding_hash"],
            ),
            choice="select_candidate",
            target_id=candidate["target_id"],
        )

    monkeypatch.setattr(build_concepts.config, "use_live_generation", lambda: True)
    monkeypatch.setattr(
        build_concepts.autonomous_resolution, "enabled", lambda: True
    )
    monkeypatch.setattr(
        build_concepts.autonomous_resolution, "resolve_pending", resolve
    )
    monkeypatch.setattr(build_concepts.generation, "concepts_from_mmd", generate)
    monkeypatch.setattr(
        build_concepts,
        "_deposit_and_publish_concepts",
        lambda *_args, **_kwargs: (
            [904], [],
            {"written": 1, "sources_updated": 0, "parent_column": True},
        ),
    )
    monkeypatch.setattr(
        build_concepts.drive_checkpoints,
        "schedule_checkpoint_backup",
        lambda *_args, **_kwargs: None,
    )

    result = build_concepts.generate_post_learning(
        db, job.id, chapter.id, owner_sub=auth.LOCAL_OWNER_SUB
    )

    assert result["concept_ids"] == [904]
    assert generation_calls == 2
    assert resolver_calls == 1
    db.refresh(job)
    resolution = consumed_directives[0]
    assert resolution["choice"] == "select_candidate"
    assert resolution["status"] == "consumed"
    assert resolution["resolved_by"] == "agent"
    assert resolution["instruction"] == ""
    assert job.pending_decision is None


def test_verified_working_source_patch_bypasses_model_and_agent_cap(
    db,
    first_chapter,
    monkeypatch,
):
    job, chapter = _job_at_81_percent(db, first_chapter)
    packet = _working_source_patch_pending_packet()
    operation_calls = 0

    def operation():
        nonlocal operation_calls
        operation_calls += 1
        if operation_calls == 1:
            raise semantic_recovery.HumanDecisionRequired(packet)
        return "continued-without-provider"

    def unexpected_provider(*_args, **_kwargs):
        pytest.fail("a verified deterministic source patch must not call GPT")

    monkeypatch.setattr(
        build_concepts.autonomous_resolution, "enabled", lambda: False)
    monkeypatch.setattr(
        build_concepts.autonomous_resolution, "maximum_decisions", lambda: 0)
    monkeypatch.setattr(
        build_concepts.autonomous_resolution,
        "resolve_pending",
        unexpected_provider,
    )
    monkeypatch.setattr(
        build_concepts.drive_checkpoints,
        "schedule_checkpoint_backup",
        lambda *_args, **_kwargs: None,
    )

    paused, result = build_concepts._run_with_human_decision_pause(
        operation,
        db=db,
        job=job,
        fingerprint=job.generation_checkpoint["fingerprint"],
        target_chapter_id=chapter.id,
        owner_sub=auth.LOCAL_OWNER_SUB,
    )

    assert paused is None
    assert result == "continued-without-provider"
    assert operation_calls == 2
    db.refresh(job)
    ledger = job.generation_checkpoint["human_decisions"]
    assert ledger["pending"] is None
    assert len(ledger["resolutions"]) == 1
    resolution = ledger["resolutions"][0]
    assert resolution["resolved_by"] == "agent"
    assert resolution["choice"] == "accept_recommended"
    assert resolution["target_id"] == packet["source_patch"]["target_id"]
    assert resolution["pending_decision"]["agent_review"][
        "evidence_refs"
    ][0].startswith("CANONICAL-PATCH-")


def test_agent_abstention_persists_pause_and_is_not_called_on_replay(
    db,
    first_chapter,
    monkeypatch,
):
    job, chapter = _job_at_81_percent(db, first_chapter)
    resolver_calls = 0
    generation_calls = 0

    def require_decision(*_args, **_kwargs):
        nonlocal generation_calls
        generation_calls += 1
        raise semantic_recovery.HumanDecisionRequired(_pending_packet())

    def abstain(*_args, **_kwargs):
        nonlocal resolver_calls
        resolver_calls += 1
        return autonomous_resolution.ResolutionResult(
            status="escalated",
            reason="Two source-supported hosts remain equally defensible.",
            confidence=0.91,
            evidence_refs=("PENDING-EVIDENCE-001",),
        )

    monkeypatch.setattr(build_concepts.config, "use_live_generation", lambda: True)
    monkeypatch.setattr(
        build_concepts.autonomous_resolution, "enabled", lambda: True
    )
    monkeypatch.setattr(
        build_concepts.autonomous_resolution, "resolve_pending", abstain
    )
    monkeypatch.setattr(
        build_concepts.generation, "concepts_from_mmd", require_decision
    )
    monkeypatch.setattr(
        build_concepts.drive_checkpoints,
        "schedule_checkpoint_backup",
        lambda *_args, **_kwargs: None,
    )

    first = build_concepts.generate_post_learning(
        db, job.id, chapter.id, owner_sub=auth.LOCAL_OWNER_SUB
    )
    assert first["status"] == "awaiting_decision"
    assert first["pending_decision"]["agent_review"]["status"] == "escalated"
    assert "equally defensible" in (
        first["pending_decision"]["agent_review"]["reason"]
    )

    repeated = build_concepts.generate_post_learning(
        db, job.id, chapter.id, owner_sub=auth.LOCAL_OWNER_SUB
    )
    assert repeated["status"] == "awaiting_decision"
    assert resolver_calls == 1
    assert generation_calls == 1


def test_saved_v2_pause_gets_one_output_contract_upgrade_on_resume(
    db,
    first_chapter,
    monkeypatch,
):
    job, chapter = _job_at_81_percent(db, first_chapter)
    pending = _attach_pending(db, job, chapter)
    issue_key = autonomous_resolution.issue_key(pending)
    started_at = build_concepts._agent_review_timestamp()
    legacy_base = {
        "resolver_version": "semantic-resolution-agent-2",
        "issue_key": issue_key,
        "capability_key": "8" * 64,
        "started_at": started_at,
    }
    build_concepts._persist_pending_agent_review(
        db,
        job,
        decision_id=pending["decision_id"],
        context_hash=pending["context_hash"],
        review={
            **legacy_base,
            "status": "request_started",
            "reason": "The legacy resolver inspected a truncated target set.",
        },
        owner_sub=auth.LOCAL_OWNER_SUB,
    )
    build_concepts._persist_pending_agent_review(
        db,
        job,
        decision_id=pending["decision_id"],
        context_hash=pending["context_hash"],
        review={
            **legacy_base,
            "status": "escalated",
            "completed_at": build_concepts._agent_review_timestamp(),
            "reason": "The required target was not offered to the agent.",
        },
        owner_sub=auth.LOCAL_OWNER_SUB,
    )
    resolver_calls = 0

    def abstain(*_args, **_kwargs):
        nonlocal resolver_calls
        resolver_calls += 1
        return autonomous_resolution.ResolutionResult(
            status="escalated",
            reason="Two fully bound candidates remain equally defensible.",
            confidence=0.93,
            evidence_refs=("PENDING-EVIDENCE-001",),
        )

    monkeypatch.setattr(
        build_concepts.autonomous_resolution, "enabled", lambda: True
    )
    monkeypatch.setattr(
        build_concepts.autonomous_resolution, "resolve_pending", abstain
    )
    monkeypatch.setattr(
        build_concepts.drive_checkpoints,
        "schedule_checkpoint_backup",
        lambda *_args, **_kwargs: None,
    )

    first = build_concepts._existing_human_decision_pause(
        db,
        job,
        copy.deepcopy(job.generation_checkpoint),
        agent_resolution_ids=set(),
        owner_sub=auth.LOCAL_OWNER_SUB,
    )

    assert first["status"] == "awaiting_decision"
    assert resolver_calls == 1
    db.refresh(job)
    ledger = job.generation_checkpoint["human_decisions"]
    assert ledger["agent_review_history"][0]["resolver_version"] == (
        "semantic-resolution-agent-2"
    )
    current = ledger["pending"]["agent_review"]
    assert current["resolver_version"] == autonomous_resolution.RESOLVER_VERSION
    assert current["capability_key"] == autonomous_resolution.capability_key(
        ledger["pending"]
    )
    assert current["offered_candidate_count"] == len(
        ledger["pending"]["candidates"]
    )

    repeated = build_concepts._existing_human_decision_pause(
        db,
        job,
        copy.deepcopy(job.generation_checkpoint),
        agent_resolution_ids=set(),
        owner_sub=auth.LOCAL_OWNER_SUB,
    )
    assert repeated["status"] == "awaiting_decision"
    assert resolver_calls == 1


def test_incompatible_81_percent_pause_is_pruned_before_agent_dispatch(
    db,
    first_chapter,
    monkeypatch,
):
    job, chapter = _job_at_81_percent(db, first_chapter)
    description_stage = generation._make_concept_checkpoint(
        "description_method_snapshot",
        records=[{
            "topic": "The Making of Nationalism in Europe",
            "parent_concept": "Nationalism",
            "concept_title": "Cavour's Diplomacy",
            "concept_details": "Description: Verified source description.",
            "keywords": "Cavour, diplomacy",
        }],
        method_row_snapshot=[],
    )
    job.generation_checkpoint = build_concepts._merge_generation_checkpoint_history(
        job.generation_checkpoint,
        description_stage,
        fingerprint=job.generation_checkpoint["fingerprint"],
        target_identity=job.generation_checkpoint["target_identity"],
        target_chapter_id=chapter.id,
    )
    db.commit()
    pending = _attach_pending(db, job, chapter)
    legacy_capability = "b" * 64
    started_at = build_concepts._agent_review_timestamp()
    build_concepts._persist_pending_agent_review(
        db,
        job,
        decision_id=pending["decision_id"],
        context_hash=pending["context_hash"],
        review={
            "status": "request_started",
            "resolver_version": "semantic-resolution-agent-2",
            "issue_key": autonomous_resolution.issue_key(pending),
            "capability_key": legacy_capability,
            "started_at": started_at,
            "reason": "Saved before the old bounded request.",
        },
        owner_sub=auth.LOCAL_OWNER_SUB,
    )
    build_concepts._persist_pending_agent_review(
        db,
        job,
        decision_id=pending["decision_id"],
        context_hash=pending["context_hash"],
        review={
            "status": "escalated",
            "resolver_version": "semantic-resolution-agent-2",
            "issue_key": autonomous_resolution.issue_key(pending),
            "capability_key": legacy_capability,
            "started_at": started_at,
            "completed_at": build_concepts._agent_review_timestamp(),
            "reason": "The old action contract could not apply the choice.",
        },
        owner_sub=auth.LOCAL_OWNER_SUB,
    )
    db.refresh(job)
    checkpoint = copy.deepcopy(job.generation_checkpoint)
    checkpoint["semantic_recovery_dispatches"] = {
        "version": 1,
        "attempts": [
            {
                "issue_key": "1" * 64,
                "failure_signature": "2" * 64,
                "status": "succeeded",
                "started_at": "2026-08-01T10:00:00+00:00",
                "completed_at": "2026-08-01T10:00:01+00:00",
                "failure_type": "ValueError",
                "stage": "description_method_snapshot",
            },
            {
                "issue_key": "3" * 64,
                "failure_signature": "4" * 64,
                "status": "succeeded",
                "started_at": "2026-08-01T10:00:02+00:00",
                "completed_at": "2026-08-01T10:00:03+00:00",
                "failure_type": "ValueError",
                "stage": "pre_type_assignment",
            },
        ],
    }
    job.generation_checkpoint = checkpoint
    db.commit()

    old_spec = generation._CONCEPT_CHECKPOINT_STAGES[
        "pre_type_assignment"
    ]
    monkeypatch.setitem(
        generation._CONCEPT_CHECKPOINT_STAGES,
        "pre_type_assignment",
        {**old_spec, "version": old_spec["version"] + 1},
    )
    resolver_calls = 0
    generation_calls = 0

    def must_not_resolve(*_args, **_kwargs):
        nonlocal resolver_calls
        resolver_calls += 1
        pytest.fail("a discarded 30-Case pause must not call the agent")

    def generate(*_args, **kwargs):
        nonlocal generation_calls
        generation_calls += 1
        resume = kwargs["resume_checkpoint"]
        assert resume["stage"] == "description_method_snapshot"
        assert resume["human_decisions"]["pending"] is None
        assert [
            row["stage"]
            for row in resume["semantic_recovery_dispatches"]["attempts"]
        ] == ["description_method_snapshot"]
        return [{
            "topic": "The Making of Nationalism in Europe",
            "parent_concept": "Nationalism",
            "concept_title": "Cavour's Diplomacy",
            "concept_details": "Description: Verified source description.",
            "keywords": "Cavour, diplomacy",
        }]

    monkeypatch.setattr(build_concepts.config, "use_live_generation", lambda: True)
    monkeypatch.setattr(
        build_concepts.autonomous_resolution,
        "resolve_pending",
        must_not_resolve,
    )
    monkeypatch.setattr(build_concepts.generation, "concepts_from_mmd", generate)
    monkeypatch.setattr(
        build_concepts,
        "_deposit_and_publish_concepts",
        lambda *_args, **_kwargs: (
            [905], [],
            {"written": 1, "sources_updated": 0, "parent_column": True},
        ),
    )
    monkeypatch.setattr(
        build_concepts.drive_checkpoints,
        "schedule_checkpoint_backup",
        lambda *_args, **_kwargs: None,
    )

    result = build_concepts.generate_post_learning(
        db, job.id, chapter.id, owner_sub=auth.LOCAL_OWNER_SUB
    )

    assert result["concept_ids"] == [905]
    assert resolver_calls == 0
    assert generation_calls == 1
    db.refresh(job)
    assert job.status == "generated"


def test_checkpoint_fallback_preserves_earlier_and_replace_source_authority():
    early_issue = "1" * 64
    stale_issue = "2" * 64
    envelope = {
        "human_decisions": {
            "version": 1,
            "context": {"fingerprint": "f" * 64, "target_chapter_id": 1},
            "pending": {
                "checkpoint_progress": 0.81,
                "agent_review": {"issue_key": stale_issue},
            },
            "deferred_assignment_unit_ids": ["STALE-CONCEPT"],
            "agent_review_history": [
                {"issue_key": early_issue, "status": "escalated"},
                {"issue_key": stale_issue, "status": "escalated"},
            ],
            "resolutions": [
                {
                    "choice": "select_candidate",
                    "pending_decision": {"checkpoint_progress": 0.35},
                },
                {
                    "choice": "custom_instruction",
                    "pending_decision": {
                        "checkpoint_progress": 0.81,
                        "agent_review": {"issue_key": stale_issue},
                    },
                },
                {
                    "choice": "replace_source",
                    "pending_decision": {"checkpoint_progress": 0.81},
                },
            ],
        },
    }

    pruned = build_concepts._prune_human_decisions_after_checkpoint_fallback(
        envelope,
        retained_stage="description_method_snapshot",
    )
    ledger = pruned["human_decisions"]

    assert ledger["pending"] is None
    assert ledger["deferred_assignment_unit_ids"] == []
    assert [row["choice"] for row in ledger["resolutions"]] == [
        "select_candidate",
        "replace_source",
    ]
    assert [row["issue_key"] for row in ledger["agent_review_history"]] == [
        early_issue
    ]


def test_sole_incompatible_same_source_stage_restarts_without_mismatch_error(
    db,
    first_chapter,
    monkeypatch,
):
    job, chapter = _job_at_81_percent(db, first_chapter)
    old_spec = generation._CONCEPT_CHECKPOINT_STAGES[
        "pre_type_assignment"
    ]
    monkeypatch.setitem(
        generation._CONCEPT_CHECKPOINT_STAGES,
        "pre_type_assignment",
        {**old_spec, "version": old_spec["version"] + 1},
    )
    generation_calls = 0

    def generate(*_args, **kwargs):
        nonlocal generation_calls
        generation_calls += 1
        assert kwargs["resume_checkpoint"] is None
        return [{
            "topic": "The Making of Nationalism in Europe",
            "parent_concept": "Nationalism",
            "concept_title": "Cavour's Diplomacy",
            "concept_details": "Description: Rebuilt from the same source.",
            "keywords": "Cavour, diplomacy",
        }]

    monkeypatch.setattr(build_concepts.config, "use_live_generation", lambda: True)
    monkeypatch.setattr(build_concepts.generation, "concepts_from_mmd", generate)
    monkeypatch.setattr(
        build_concepts,
        "_deposit_and_publish_concepts",
        lambda *_args, **_kwargs: (
            [906], [],
            {"written": 1, "sources_updated": 0, "parent_column": True},
        ),
    )
    monkeypatch.setattr(
        build_concepts.drive_checkpoints,
        "schedule_checkpoint_backup",
        lambda *_args, **_kwargs: None,
    )

    result = build_concepts.generate_post_learning(
        db, job.id, chapter.id, owner_sub=auth.LOCAL_OWNER_SUB
    )

    assert result["concept_ids"] == [906]
    assert generation_calls == 1


def test_sole_incompatible_stage_preserves_replace_source_stop(
    db,
    first_chapter,
    monkeypatch,
):
    job, chapter = _job_at_81_percent(db, first_chapter)
    pending = build_concepts._persist_pending_human_decision(
        db,
        job,
        _early_blueprint_pending_packet(),
        fingerprint=job.generation_checkpoint["fingerprint"],
        target_chapter_id=chapter.id,
        owner_sub=auth.LOCAL_OWNER_SUB,
    )
    build_concepts._record_human_semantic_decision_locked(
        db,
        job,
        pending["decision_id"],
        choice="replace_source",
        instruction="",
        target_id="",
        target_concept_id="",
    )
    old_spec = generation._CONCEPT_CHECKPOINT_STAGES[
        "pre_type_assignment"
    ]
    monkeypatch.setitem(
        generation._CONCEPT_CHECKPOINT_STAGES,
        "pre_type_assignment",
        {**old_spec, "version": old_spec["version"] + 1},
    )

    with pytest.raises(ValueError, match="Source replacement is required"):
        build_concepts.generate_post_learning(
            db, job.id, chapter.id, owner_sub=auth.LOCAL_OWNER_SUB
        )

    db.refresh(job)
    assert build_concepts._source_replacement_required(
        job.generation_checkpoint
    )


def test_request_started_agent_state_blocks_unknown_outcome_replay(
    db,
    first_chapter,
    monkeypatch,
):
    job, chapter = _job_at_81_percent(db, first_chapter)
    pending = _attach_pending(db, job, chapter)
    started_at = build_concepts._agent_review_timestamp()
    build_concepts._persist_pending_agent_review(
        db,
        job,
        decision_id=pending["decision_id"],
        context_hash=pending["context_hash"],
        review={
            "status": "request_started",
            "resolver_version": "semantic-resolution-agent-2",
            "issue_key": autonomous_resolution.issue_key(pending),
            "capability_key": "9" * 64,
            "started_at": started_at,
            "reason": "Saved before the v2 dispatch with an unknown outcome.",
        },
        owner_sub=auth.LOCAL_OWNER_SUB,
    )
    monkeypatch.setattr(build_concepts.config, "use_live_generation", lambda: True)
    monkeypatch.setattr(
        build_concepts.autonomous_resolution,
        "resolve_pending",
        lambda *_args, **_kwargs: pytest.fail(
            "an unknown prior outcome must never be dispatched again"
        ),
    )
    monkeypatch.setattr(
        build_concepts.generation,
        "concepts_from_mmd",
        lambda *_args, **_kwargs: pytest.fail(
            "a request-started decision must remain paused"
        ),
    )

    result = build_concepts.generate_post_learning(
        db, job.id, chapter.id, owner_sub=auth.LOCAL_OWNER_SUB
    )
    assert result["status"] == "awaiting_decision"
    assert result["pending_decision"]["agent_review"]["status"] == (
        "request_started"
    )
    db.refresh(job)
    assert job.status == "converted"
    assert job.generation_checkpoint["stage"] == "pre_type_assignment"
    assert job.checkpoint_progress == pytest.approx(0.81)
    assert job.awaiting_decision is True

    repeated = build_concepts.generate_post_learning(
        db, job.id, chapter.id, owner_sub=auth.LOCAL_OWNER_SUB)
    assert repeated["status"] == "awaiting_decision"


def test_saved_agent_directive_replays_without_second_agent_call(
    db,
    first_chapter,
    monkeypatch,
):
    job, chapter = _job_at_81_percent(db, first_chapter)
    pending = _attach_pending(db, job, chapter)
    now = build_concepts._agent_review_timestamp()
    build_concepts._persist_pending_agent_review(
        db,
        job,
        decision_id=pending["decision_id"],
        context_hash=pending["context_hash"],
        review={
            "status": "request_started",
            "resolver_version": autonomous_resolution.RESOLVER_VERSION,
            "issue_key": autonomous_resolution.issue_key(pending),
            "started_at": now,
            "reason": "Saved before the bounded autonomous request.",
        },
        owner_sub=auth.LOCAL_OWNER_SUB,
    )
    build_concepts._persist_pending_agent_review(
        db,
        job,
        decision_id=pending["decision_id"],
        context_hash=pending["context_hash"],
        review={
            "status": "resolved",
            "resolver_version": autonomous_resolution.RESOLVER_VERSION,
            "issue_key": autonomous_resolution.issue_key(pending),
            "started_at": now,
            "completed_at": now,
            "reason": "The exact liberty evidence supports one host.",
            "confidence": 0.97,
            "evidence_refs": ["PENDING-EVIDENCE-001"],
            "choice": "expand_existing",
            "target_concept_id": "HOST-CONCEPT-0001",
        },
        owner_sub=auth.LOCAL_OWNER_SUB,
    )
    identity = {
        "decision_id": pending["decision_id"],
        "context_hash": pending["context_hash"],
    }
    generation_calls = 0

    def generate(*_args, **_kwargs):
        nonlocal generation_calls
        generation_calls += 1
        resolution = phase33._human_resolution_for(identity)
        assert resolution is not None
        assert resolution["choice"] == "expand_existing"
        return [{
            "topic": "The French Revolution",
            "parent_concept": "Nationalism",
            "concept_title": "Ernest Renan's Idea of a Nation",
            "concept_details": "Description: Nations safeguard liberty.",
            "keywords": "Renan, liberty",
        }]

    monkeypatch.setattr(build_concepts.config, "use_live_generation", lambda: True)
    monkeypatch.setattr(build_concepts.generation, "concepts_from_mmd", generate)
    monkeypatch.setattr(
        build_concepts.autonomous_resolution,
        "resolve_pending",
        lambda *_args, **_kwargs: pytest.fail(
            "a saved directive must not call the resolution agent again"
        ),
    )
    monkeypatch.setattr(
        build_concepts,
        "_deposit_and_publish_concepts",
        lambda *_args, **_kwargs: (
            [903], [],
            {"written": 1, "sources_updated": 0, "parent_column": True},
        ),
    )
    monkeypatch.setattr(
        build_concepts.drive_checkpoints,
        "schedule_checkpoint_backup",
        lambda *_args, **_kwargs: None,
    )

    result = build_concepts.generate_post_learning(
        db, job.id, chapter.id, owner_sub=auth.LOCAL_OWNER_SUB
    )
    assert result["concept_ids"] == [903]
    assert generation_calls == 1


def test_agent_action_followup_same_scope_escalates_without_loop(
    db,
    first_chapter,
    monkeypatch,
):
    job, chapter = _job_at_81_percent(db, first_chapter)
    first_packet = _pending_packet()
    first_packet["candidates"].append({
        "concept_id": "HOST-CONCEPT-0002",
        "title": "A second Renan host candidate",
        "topic": "The French Revolution and the Idea of the Nation",
        "coverage": "Nations preserve collective inheritance.",
        "gap": "Nations as guarantees of liberty.",
    })
    followup = copy.deepcopy(first_packet)
    followup["context_hash"] = "f" * 64
    followup["decision_id"] = "phase33-host-followup-" + "f" * 16
    # Candidate opaque IDs can be regenerated and reordered by a later critic
    # pass. They must not turn the same semantic unit into a billable new issue.
    followup["candidates"] = [
        {
            **copy.deepcopy(first_packet["candidates"][1]),
            "concept_id": "HOST-REGENERATED-9002",
        },
        {
            **copy.deepcopy(first_packet["candidates"][0]),
            "concept_id": "HOST-REGENERATED-9001",
        },
    ]
    followup["conflict"] = (
        "The directed expansion was reviewed but the same Renan host scope "
        "still lacks a durable explanation."
    )
    identity = {
        "decision_id": first_packet["decision_id"],
        "context_hash": first_packet["context_hash"],
    }
    generation_calls = 0
    resolver_calls = 0

    def generate(*_args, **_kwargs):
        nonlocal generation_calls
        generation_calls += 1
        if phase33._human_resolution_for(identity) is None:
            raise semantic_recovery.HumanDecisionRequired(first_packet)
        raise semantic_recovery.HumanDecisionRequired(followup)

    def resolve(*_args, **_kwargs):
        nonlocal resolver_calls
        resolver_calls += 1
        return autonomous_resolution.ResolutionResult(
            status="resolved",
            reason="The exact evidence initially supports expanding Renan.",
            confidence=0.97,
            evidence_refs=("PENDING-EVIDENCE-001",),
            choice="expand_existing",
            target_concept_id="HOST-CONCEPT-0001",
        )

    monkeypatch.setattr(build_concepts.config, "use_live_generation", lambda: True)
    monkeypatch.setattr(
        build_concepts.autonomous_resolution, "enabled", lambda: True
    )
    monkeypatch.setattr(
        build_concepts.autonomous_resolution, "resolve_pending", resolve
    )
    monkeypatch.setattr(build_concepts.generation, "concepts_from_mmd", generate)
    monkeypatch.setattr(
        build_concepts.drive_checkpoints,
        "schedule_checkpoint_backup",
        lambda *_args, **_kwargs: None,
    )

    result = build_concepts.generate_post_learning(
        db, job.id, chapter.id, owner_sub=auth.LOCAL_OWNER_SUB
    )
    assert result["status"] == "awaiting_decision"
    assert result["pending_decision"]["decision_id"] == followup["decision_id"]
    review = result["pending_decision"]["agent_review"]
    assert review["status"] == "escalated"
    assert "same semantic scope" in review["reason"]
    assert generation_calls == 2
    assert resolver_calls == 1


def test_second_sequential_agent_dispatch_claim_is_rejected_atomically(
    db,
    first_chapter,
    monkeypatch,
):
    job, chapter = _job_at_81_percent(db, first_chapter)
    monkeypatch.setattr(
        build_concepts.drive_checkpoints,
        "schedule_checkpoint_backup",
        lambda *_args, **_kwargs: None,
    )
    pending = _attach_pending(db, job, chapter)
    issue_key = autonomous_resolution.issue_key(pending)
    first_review = {
        "status": "request_started",
        "resolver_version": autonomous_resolution.RESOLVER_VERSION,
        "issue_key": issue_key,
        "started_at": "2026-08-01T10:00:00+00:00",
        "reason": "The first worker durably claimed this decision.",
    }
    build_concepts._persist_pending_agent_review(
        db,
        job,
        decision_id=pending["decision_id"],
        context_hash=pending["context_hash"],
        review=first_review,
        owner_sub=auth.LOCAL_OWNER_SUB,
    )
    db.refresh(job)
    checkpoint_after_first_claim = copy.deepcopy(job.generation_checkpoint)

    # A competing worker can rediscover the same gate after the first worker
    # committed its dispatch claim. Re-persisting the identical raw pause must
    # preserve that claim rather than reverting it to an unclaimed decision.
    replayed = build_concepts._persist_pending_human_decision(
        db,
        job,
        _pending_packet(),
        fingerprint=job.generation_checkpoint["fingerprint"],
        target_chapter_id=chapter.id,
        owner_sub=auth.LOCAL_OWNER_SUB,
    )
    assert replayed["agent_review"]["status"] == "request_started"
    assert replayed["agent_review"]["started_at"] == first_review[
        "started_at"
    ]
    db.refresh(job)
    checkpoint_after_replayed_pause = copy.deepcopy(job.generation_checkpoint)

    with pytest.raises(
        build_concepts.HumanDecisionConflictError,
        match="already claimed",
    ):
        build_concepts._persist_pending_agent_review(
            db,
            job,
            decision_id=pending["decision_id"],
            context_hash=pending["context_hash"],
            review={
                **first_review,
                "started_at": "2026-08-01T10:00:01+00:00",
                "reason": "A second worker must not overwrite the claim.",
            },
            owner_sub=auth.LOCAL_OWNER_SUB,
        )

    db.expire_all()
    saved = db.get(models.UploadJob, job.id)
    assert checkpoint_after_replayed_pause == checkpoint_after_first_claim
    assert saved.generation_checkpoint == checkpoint_after_replayed_pause
    review = saved.pending_decision["agent_review"]
    assert review["started_at"] == first_review["started_at"]
    assert review["reason"] == first_review["reason"]


def test_consumed_agent_resolution_reappearing_after_crash_escalates_to_user(
    db,
    first_chapter,
    monkeypatch,
):
    job, chapter = _job_at_81_percent(db, first_chapter)
    monkeypatch.setattr(
        build_concepts.drive_checkpoints,
        "schedule_checkpoint_backup",
        lambda *_args, **_kwargs: None,
    )
    pending = _attach_pending(db, job, chapter)
    issue_key = autonomous_resolution.issue_key(pending)
    started_at = "2026-08-01T10:01:00+00:00"
    build_concepts._persist_pending_agent_review(
        db,
        job,
        decision_id=pending["decision_id"],
        context_hash=pending["context_hash"],
        review={
            "status": "request_started",
            "resolver_version": autonomous_resolution.RESOLVER_VERSION,
            "issue_key": issue_key,
            "started_at": started_at,
            "reason": "Saved before the autonomous request.",
        },
        owner_sub=auth.LOCAL_OWNER_SUB,
    )
    build_concepts._persist_pending_agent_review(
        db,
        job,
        decision_id=pending["decision_id"],
        context_hash=pending["context_hash"],
        review={
            "status": "resolved",
            "resolver_version": autonomous_resolution.RESOLVER_VERSION,
            "issue_key": issue_key,
            "started_at": started_at,
            "completed_at": "2026-08-01T10:01:01+00:00",
            "reason": "The source uniquely supports the existing host.",
            "confidence": 0.97,
            "evidence_refs": ["PENDING-EVIDENCE-001"],
            "choice": "expand_existing",
            "target_concept_id": "HOST-CONCEPT-0001",
        },
        owner_sub=auth.LOCAL_OWNER_SUB,
    )
    build_concepts._record_human_semantic_decision_locked(
        db,
        job,
        pending["decision_id"],
        choice="expand_existing",
        instruction="",
        target_id="",
        target_concept_id="HOST-CONCEPT-0001",
        resolved_by="agent",
        resolution_status="consumed",
    )
    db.refresh(job)
    assert job.generation_checkpoint["human_decisions"]["resolutions"][0][
        "status"
    ] == "consumed"
    checkpoints._validate_checkpoint(
        copy.deepcopy(job.generation_checkpoint),
        learning_kind="post",
        mmd_text=job.mmd_text,
        path="checkpoint",
    )

    unsealed = copy.deepcopy(job.generation_checkpoint)
    unsealed_resolution = unsealed["human_decisions"]["resolutions"][0]
    unsealed_resolution["status"] = "ready"
    unsealed_resolution["consumed_at"] = ""
    with pytest.raises(ValueError, match="must be durably sealed as consumed"):
        checkpoints._validate_checkpoint(
            unsealed,
            learning_kind="post",
            mmd_text=job.mmd_text,
            path="checkpoint",
        )

    mismatched = copy.deepcopy(job.generation_checkpoint)
    mismatched["human_decisions"]["resolutions"][0][
        "pending_decision"
    ]["agent_review"]["target_concept_id"] = "HOST-CONCEPT-CHANGED"
    with pytest.raises(ValueError, match="validated agent review directive"):
        checkpoints._validate_checkpoint(
            mismatched,
            learning_kind="post",
            mmd_text=job.mmd_text,
            path="checkpoint",
        )

    instructed = copy.deepcopy(job.generation_checkpoint)
    instructed_resolution = instructed["human_decisions"]["resolutions"][0]
    instructed_resolution["instruction"] = "Apply this extra direction."
    instructed_resolution["pending_decision"]["agent_review"][
        "instruction"
    ] = "Apply this extra direction."
    with pytest.raises(
        ValueError,
        match="instruction must be empty for an autonomous review",
    ):
        checkpoints._validate_checkpoint(
            instructed,
            learning_kind="post",
            mmd_text=job.mmd_text,
            path="checkpoint",
        )

    user_only = copy.deepcopy(job.generation_checkpoint)
    user_only_resolution = user_only["human_decisions"]["resolutions"][0]
    user_only_resolution["choice"] = "replace_source"
    user_only_resolution["pending_decision"]["agent_review"][
        "choice"
    ] = "replace_source"
    with pytest.raises(ValueError, match="not approved for autonomous"):
        checkpoints._validate_checkpoint(
            user_only,
            learning_kind="post",
            mmd_text=job.mmd_text,
            path="checkpoint",
        )

    resolver_calls = 0
    operation_calls = 0

    def must_not_resolve(*_args, **_kwargs):
        nonlocal resolver_calls
        resolver_calls += 1
        pytest.fail("a crash-ambiguous action must not call the agent again")

    def repeat_pending():
        nonlocal operation_calls
        operation_calls += 1
        raise semantic_recovery.HumanDecisionRequired(_pending_packet())

    monkeypatch.setattr(
        build_concepts.autonomous_resolution, "enabled", lambda: True
    )
    monkeypatch.setattr(
        build_concepts.autonomous_resolution,
        "resolve_pending",
        must_not_resolve,
    )

    paused, operation_result = build_concepts._run_with_human_decision_pause(
        repeat_pending,
        db=db,
        job=job,
        fingerprint=job.generation_checkpoint["fingerprint"],
        target_chapter_id=chapter.id,
        owner_sub=auth.LOCAL_OWNER_SUB,
    )

    assert operation_result is None
    assert paused["status"] == "awaiting_decision"
    assert paused["pending_decision"]["decision_id"] == pending["decision_id"]
    review = paused["pending_decision"]["agent_review"]
    assert review["status"] == "escalated"
    assert "previous worker ended" in review["reason"]
    assert review["choice"] is None
    assert resolver_calls == 0
    assert operation_calls == 1
    db.refresh(job)
    ledger = job.generation_checkpoint["human_decisions"]
    assert ledger["pending"]["agent_review"]["status"] == "escalated"
    assert ledger["resolutions"] == []


def test_distinct_agent_decisions_remain_active_across_same_run_restarts(
    db,
    first_chapter,
    monkeypatch,
):
    job, chapter = _job_at_81_percent(db, first_chapter)
    monkeypatch.setattr(
        build_concepts.drive_checkpoints,
        "schedule_checkpoint_backup",
        lambda *_args, **_kwargs: None,
    )
    packet_a = _pending_packet()
    packet_b = copy.deepcopy(packet_a)
    packet_b["context_hash"] = "c" * 64
    packet_b["decision_id"] = "phase33-host-b-" + ("c" * 20)
    packet_b["conflict"] = "A second, independent Type host needs direction."
    packet_b["item"].update({
        "unit_id": "TYPE-0100",
        "type_id": "TYPE-0100",
        "type_title": "Explain a second source idea",
        "qids": ["QINV-0100"],
        "questions": ["Why is the second source idea important?"],
    })
    packet_b["candidates"][0].update({
        "concept_id": "HOST-CONCEPT-0100",
        "title": "Second source-grounded concept",
        "coverage": "The second idea's verified attributes.",
        "gap": "Its source-supported importance.",
    })
    packet_b["options"][0]["target_concept_id"] = "HOST-CONCEPT-0100"
    identity_a = {
        "decision_id": packet_a["decision_id"],
        "context_hash": packet_a["context_hash"],
    }
    identity_b = {
        "decision_id": packet_b["decision_id"],
        "context_hash": packet_b["context_hash"],
    }
    operation_calls = 0
    resolver_decision_ids: list[str] = []

    def operation():
        nonlocal operation_calls
        operation_calls += 1
        with build_concepts._human_decision_resolution_context(
            copy.deepcopy(job.generation_checkpoint or {})
        ):
            resolution_a = phase33._human_resolution_for(identity_a)
            if resolution_a is None:
                raise semantic_recovery.HumanDecisionRequired(packet_a)
            assert resolution_a["target_concept_id"] == "HOST-CONCEPT-0001"
            resolution_b = phase33._human_resolution_for(identity_b)
            if resolution_b is None:
                raise semantic_recovery.HumanDecisionRequired(packet_b)
            assert resolution_b["target_concept_id"] == "HOST-CONCEPT-0100"
            return "operation-complete"

    def resolve(pending, **_kwargs):
        decision_id = pending["decision_id"]
        resolver_decision_ids.append(decision_id)
        target = (
            "HOST-CONCEPT-0001"
            if decision_id == packet_a["decision_id"]
            else "HOST-CONCEPT-0100"
        )
        return autonomous_resolution.ResolutionResult(
            status="resolved",
            reason="One existing host is uniquely supported by the source.",
            confidence=0.97,
            evidence_refs=("PENDING-EVIDENCE-001",),
            choice="expand_existing",
            target_concept_id=target,
        )

    monkeypatch.setattr(
        build_concepts.autonomous_resolution, "enabled", lambda: True
    )
    monkeypatch.setattr(
        build_concepts.autonomous_resolution, "resolve_pending", resolve
    )

    paused, operation_result = build_concepts._run_with_human_decision_pause(
        operation,
        db=db,
        job=job,
        fingerprint=job.generation_checkpoint["fingerprint"],
        target_chapter_id=chapter.id,
        owner_sub=auth.LOCAL_OWNER_SUB,
    )

    assert paused is None
    assert operation_result == "operation-complete"
    assert operation_calls == 3
    assert resolver_decision_ids == [
        packet_a["decision_id"],
        packet_b["decision_id"],
    ]
    db.refresh(job)
    resolutions = job.generation_checkpoint["human_decisions"]["resolutions"]
    assert [row["decision_id"] for row in resolutions] == [
        packet_a["decision_id"],
        packet_b["decision_id"],
    ]
    assert {row["status"] for row in resolutions} == {"consumed"}
    assert {row["resolved_by"] for row in resolutions} == {"agent"}


def test_fresh_resume_keeps_prior_agent_direction_with_later_human_answer(
    db,
    first_chapter,
    monkeypatch,
):
    job, chapter = _job_at_81_percent(db, first_chapter)
    monkeypatch.setattr(
        build_concepts.drive_checkpoints,
        "schedule_checkpoint_backup",
        lambda *_args, **_kwargs: None,
    )
    packet_a = _pending_packet()
    pending_a = _attach_pending(db, job, chapter)
    issue_key_a = autonomous_resolution.issue_key(pending_a)
    started_at = "2026-08-01T10:02:00+00:00"
    build_concepts._persist_pending_agent_review(
        db,
        job,
        decision_id=pending_a["decision_id"],
        context_hash=pending_a["context_hash"],
        review={
            "status": "request_started",
            "resolver_version": autonomous_resolution.RESOLVER_VERSION,
            "issue_key": issue_key_a,
            "started_at": started_at,
            "reason": "Saved before resolving the first Phase 3.3 issue.",
        },
        owner_sub=auth.LOCAL_OWNER_SUB,
    )
    build_concepts._persist_pending_agent_review(
        db,
        job,
        decision_id=pending_a["decision_id"],
        context_hash=pending_a["context_hash"],
        review={
            "status": "resolved",
            "resolver_version": autonomous_resolution.RESOLVER_VERSION,
            "issue_key": issue_key_a,
            "started_at": started_at,
            "completed_at": "2026-08-01T10:02:01+00:00",
            "reason": "The first host is uniquely source-supported.",
            "confidence": 0.97,
            "evidence_refs": ["PENDING-EVIDENCE-001"],
            "choice": "expand_existing",
            "target_concept_id": "HOST-CONCEPT-0001",
        },
        owner_sub=auth.LOCAL_OWNER_SUB,
    )
    build_concepts._record_human_semantic_decision_locked(
        db,
        job,
        pending_a["decision_id"],
        choice="expand_existing",
        instruction="",
        target_id="",
        target_concept_id="HOST-CONCEPT-0001",
        resolved_by="agent",
        resolution_status="consumed",
    )

    packet_b = copy.deepcopy(packet_a)
    # Phase 3.3 derives one context hash from the complete host-planning
    # context, then emits distinct decision IDs for sequential unit reviews.
    packet_b["context_hash"] = packet_a["context_hash"]
    packet_b["decision_id"] = "phase33-host-human-b-" + ("d" * 16)
    packet_b["conflict"] = "A later independent Type host needs direction."
    packet_b["item"].update({
        "unit_id": "TYPE-0200",
        "type_id": "TYPE-0200",
        "type_title": "Explain the later source idea",
        "qids": ["QINV-0200"],
        "questions": ["Why is the later source idea important?"],
    })
    packet_b["candidates"][0].update({
        "concept_id": "HOST-CONCEPT-0200",
        "title": "Later source-grounded concept",
        "coverage": "The later idea's verified attributes.",
        "gap": "Its source-supported importance.",
    })
    packet_b["options"][0]["target_concept_id"] = "HOST-CONCEPT-0200"
    pending_b = build_concepts._persist_pending_human_decision(
        db,
        job,
        packet_b,
        fingerprint=job.generation_checkpoint["fingerprint"],
        target_chapter_id=chapter.id,
        owner_sub=auth.LOCAL_OWNER_SUB,
    )
    build_concepts._record_human_semantic_decision_locked(
        db,
        job,
        pending_b["decision_id"],
        choice="expand_existing",
        instruction="",
        target_id="",
        target_concept_id="HOST-CONCEPT-0200",
    )
    db.refresh(job)
    durable_rows = job.generation_checkpoint["human_decisions"]["resolutions"]
    assert [
        (row["resolved_by"], row["status"]) for row in durable_rows
    ] == [("agent", "consumed"), ("human", "ready")]
    identity_a = {
        "decision_id": packet_a["decision_id"],
        "context_hash": packet_a["context_hash"],
    }
    operation_calls = 0

    def operation():
        nonlocal operation_calls
        operation_calls += 1
        with build_concepts._human_decision_resolution_context(
            copy.deepcopy(job.generation_checkpoint or {})
        ):
            resolutions = phase33._human_resolutions_for(identity_a)
            by_unit = {
                row["assignment_unit_id"]: row for row in resolutions
            }
            if "TYPE-0002" not in by_unit:
                raise semantic_recovery.HumanDecisionRequired(packet_a)
            if "TYPE-0200" not in by_unit:
                raise semantic_recovery.HumanDecisionRequired(packet_b)
            assert by_unit["TYPE-0002"]["target_concept_id"] == (
                "HOST-CONCEPT-0001"
            )
            assert by_unit["TYPE-0200"]["target_concept_id"] == (
                "HOST-CONCEPT-0200"
            )
            return "fresh-resume-complete"

    monkeypatch.setattr(
        build_concepts.autonomous_resolution,
        "resolve_pending",
        lambda *_args, **_kwargs: pytest.fail(
            "fresh resume must reuse both durable answers"
        ),
    )

    paused, operation_result = build_concepts._run_with_human_decision_pause(
        operation,
        db=db,
        job=job,
        fingerprint=job.generation_checkpoint["fingerprint"],
        target_chapter_id=chapter.id,
        owner_sub=auth.LOCAL_OWNER_SUB,
        initial_agent_resolution_ids=set(),
    )

    assert paused is None
    assert operation_result == "fresh-resume-complete"
    assert operation_calls == 1


def test_decision_submission_is_owner_scoped_one_time_and_api_free(
    client,
    db,
    first_chapter,
    monkeypatch,
):
    job, chapter = _job_at_81_percent(db, first_chapter)
    monkeypatch.setattr(
        build_concepts.drive_checkpoints,
        "schedule_checkpoint_backup",
        lambda *_args, **_kwargs: None,
    )
    pending = _attach_pending(db, job, chapter)
    monkeypatch.setattr(
        generation,
        "_openai_json",
        lambda *_args, **_kwargs: pytest.fail(
            "decision submission must not call OpenAI"),
    )

    response = client.post(
        f"/build-concepts/uploads/{job.id}/decisions/"
        f"{pending['decision_id']}",
        json={"choice": "expand_existing"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "decision_recorded"
    assert body["resume_required"] is True
    assert body["resolved_decision"]["target_concept_id"] == (
        "HOST-CONCEPT-0001")
    db.expire_all()
    saved = db.get(models.UploadJob, job.id)
    assert saved.pending_decision is None
    assert saved.generation_checkpoint["stage"] == "pre_type_assignment"

    duplicate = client.post(
        f"/build-concepts/uploads/{job.id}/decisions/"
        f"{pending['decision_id']}",
        json={"choice": "expand_existing"},
    )
    assert duplicate.status_code == 409
    with pytest.raises(uploads.UploadJobNotFound):
        build_concepts.record_human_semantic_decision(
            db,
            job.id,
            pending["decision_id"],
            choice="expand_existing",
            owner_sub="google:another-user",
        )


def test_agent_recorder_rejects_user_only_actions_and_instructions(
    db,
    first_chapter,
    monkeypatch,
):
    job, chapter = _job_at_81_percent(db, first_chapter)
    pending = build_concepts._persist_pending_human_decision(
        db,
        job,
        _phase31_topology_pending_packet(),
        fingerprint=job.generation_checkpoint["fingerprint"],
        target_chapter_id=chapter.id,
        owner_sub=auth.LOCAL_OWNER_SUB,
    )
    monkeypatch.setattr(
        build_concepts.drive_checkpoints,
        "schedule_checkpoint_backup",
        lambda *_args, **_kwargs: None,
    )

    with pytest.raises(ValueError, match="cannot record"):
        build_concepts._record_human_semantic_decision_locked(
            db,
            job,
            pending["decision_id"],
            choice="replace_source",
            instruction="",
            target_id="",
            target_concept_id="",
            resolved_by="agent",
            resolution_status="consumed",
        )
    with pytest.raises(ValueError, match="instruction must be empty"):
        build_concepts._record_human_semantic_decision_locked(
            db,
            job,
            pending["decision_id"],
            choice="select_candidate",
            instruction="Refine it using this prose.",
            target_id=pending["candidates"][0]["target_id"],
            target_concept_id="",
            resolved_by="agent",
            resolution_status="consumed",
        )

    recorded = build_concepts._record_human_semantic_decision_locked(
        db,
        job,
        pending["decision_id"],
        choice="custom_instruction",
        instruction="Refine the claim to Cavour's verified diplomacy.",
        target_id="",
        target_concept_id="",
        resolved_by="human",
    )
    assert recorded["resolved_decision"]["choice"] == "custom_instruction"
    assert recorded["resolved_decision"]["instruction"].startswith("Refine")


def test_early_blueprint_pending_replay_and_save_are_api_free(
    client,
    db,
    first_chapter,
    monkeypatch,
):
    job, chapter = _job_at_81_percent(db, first_chapter)
    monkeypatch.setattr(
        build_concepts.drive_checkpoints,
        "schedule_checkpoint_backup",
        lambda *_args, **_kwargs: None,
    )
    fingerprint = job.generation_checkpoint["fingerprint"]
    pending = build_concepts._persist_pending_human_decision(
        db,
        job,
        _early_blueprint_pending_packet(),
        fingerprint=fingerprint,
        target_chapter_id=chapter.id,
        owner_sub=auth.LOCAL_OWNER_SUB,
    )
    generation_calls = 0

    def must_not_generate(*_args, **_kwargs):
        nonlocal generation_calls
        generation_calls += 1
        pytest.fail("a saved pending decision must not re-enter generation")

    monkeypatch.setattr(
        build_concepts.config, "use_live_generation", lambda: True)
    monkeypatch.setattr(
        build_concepts.generation, "concepts_from_mmd", must_not_generate)
    monkeypatch.setattr(
        generation,
        "_openai_json",
        lambda *_args, **_kwargs: pytest.fail(
            "saving an early semantic decision must not call OpenAI"
        ),
    )

    replay = build_concepts.generate_post_learning(
        db, job.id, chapter.id, owner_sub=auth.LOCAL_OWNER_SUB)
    assert replay["status"] == "awaiting_decision"
    assert replay["pending_decision"]["decision_id"] == pending["decision_id"]
    assert generation_calls == 0

    response = client.post(
        f"/build-concepts/uploads/{job.id}/decisions/{pending['decision_id']}",
        json={
            "choice": "accept_recommended",
            "target_id": pending["candidates"][0]["target_id"],
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "decision_recorded"
    assert generation_calls == 0


def test_resolution_context_and_checkpoint_bundle_round_trip(
    db,
    first_chapter,
    monkeypatch,
):
    job, chapter = _job_at_81_percent(db, first_chapter)
    monkeypatch.setattr(
        build_concepts.drive_checkpoints,
        "schedule_checkpoint_backup",
        lambda *_args, **_kwargs: None,
    )
    pending = _attach_pending(db, job, chapter)
    build_concepts.record_human_semantic_decision(
        db,
        job.id,
        pending["decision_id"],
        choice="expand_existing",
        owner_sub=auth.LOCAL_OWNER_SUB,
    )
    db.refresh(job)

    identity = {
        "decision_id": pending["decision_id"],
        "context_hash": pending["context_hash"],
    }
    assert phase33._human_resolution_for(identity) is None
    with build_concepts._human_decision_resolution_context(
        job.generation_checkpoint
    ):
        resolution = phase33._human_resolution_for(identity)
        assert resolution["choice"] == "expand_existing"
        assert resolution["target_concept_id"] == "HOST-CONCEPT-0001"
        assert resolution["deferred_assignment_unit_ids"] == ["TYPE-0003"]
    ledger = job.generation_checkpoint["human_decisions"]
    assert ledger["deferred_assignment_unit_ids"] == ["TYPE-0003"]
    assert (
        ledger["resolutions"][0]["pending_decision"][
            "deferred_assignment_unit_ids"
        ]
        == []
    )
    assert phase33._human_resolution_for(identity) is None

    _, exported = checkpoints.export_bundle(
        db, job.id, owner_sub=auth.LOCAL_OWNER_SUB)
    restored = checkpoints.import_bundle(
        db, exported, owner_sub=auth.LOCAL_OWNER_SUB)
    assert restored.generation_checkpoint["human_decisions"] == (
        job.generation_checkpoint["human_decisions"])

    tampered = json.loads(exported)
    tampered["payload"]["generation_checkpoint"]["human_decisions"][
        "context"
    ]["fingerprint"] = "0" * 64
    tampered["payload_sha256"] = checkpoints.hashlib.sha256(
        checkpoints._json_bytes(tampered["payload"])).hexdigest()
    with pytest.raises(ValueError, match="does not match the checkpoint"):
        checkpoints.import_bundle(
            db,
            json.dumps(tampered).encode(),
            owner_sub=auth.LOCAL_OWNER_SUB,
        )


def test_large_ambiguity_queue_persists_and_compacts_losslessly(
    db,
    first_chapter,
    monkeypatch,
):
    job, chapter = _job_at_81_percent(db, first_chapter)
    monkeypatch.setattr(
        build_concepts.drive_checkpoints,
        "schedule_checkpoint_backup",
        lambda *_args, **_kwargs: None,
    )
    packet = _pending_packet()
    deferred = [f"TYPE-{index:04d}" for index in range(3, 104)]
    packet["deferred_assignment_unit_ids"] = deferred
    pending = build_concepts._persist_pending_human_decision(
        db,
        job,
        packet,
        fingerprint=job.generation_checkpoint["fingerprint"],
        target_chapter_id=chapter.id,
        owner_sub=auth.LOCAL_OWNER_SUB,
    )
    assert pending["deferred_assignment_unit_ids"] == deferred

    build_concepts.record_human_semantic_decision(
        db,
        job.id,
        pending["decision_id"],
        choice="expand_existing",
        owner_sub=auth.LOCAL_OWNER_SUB,
    )
    db.refresh(job)
    ledger = job.generation_checkpoint["human_decisions"]
    assert ledger["deferred_assignment_unit_ids"] == deferred
    assert (
        ledger["resolutions"][0]["pending_decision"][
            "deferred_assignment_unit_ids"
        ]
        == []
    )
    identity = {
        "decision_id": pending["decision_id"],
        "context_hash": pending["context_hash"],
    }
    with build_concepts._human_decision_resolution_context(
        job.generation_checkpoint
    ):
        resolution = phase33._human_resolution_for(identity)
        assert resolution["deferred_assignment_unit_ids"] == deferred


def test_select_existing_requires_a_current_candidate(
    db,
    first_chapter,
    monkeypatch,
):
    job, chapter = _job_at_81_percent(db, first_chapter)
    monkeypatch.setattr(
        build_concepts.drive_checkpoints,
        "schedule_checkpoint_backup",
        lambda *_args, **_kwargs: None,
    )
    pending = _attach_pending(db, job, chapter)

    with pytest.raises(ValueError, match="pending decision candidates"):
        build_concepts.record_human_semantic_decision(
            db,
            job.id,
            pending["decision_id"],
            choice="select_existing",
            target_concept_id="HOST-CONCEPT-DOES-NOT-EXIST",
            owner_sub=auth.LOCAL_OWNER_SUB,
        )


def test_expand_existing_never_selects_the_only_candidate_implicitly(
    db,
    first_chapter,
    monkeypatch,
):
    job, chapter = _job_at_81_percent(db, first_chapter)
    monkeypatch.setattr(
        build_concepts.drive_checkpoints,
        "schedule_checkpoint_backup",
        lambda *_args, **_kwargs: None,
    )
    packet = _pending_packet()
    packet["options"][0].pop("target_concept_id")
    pending = build_concepts._persist_pending_human_decision(
        db,
        job,
        packet,
        fingerprint=job.generation_checkpoint["fingerprint"],
        target_chapter_id=chapter.id,
        owner_sub=auth.LOCAL_OWNER_SUB,
    )

    with pytest.raises(
        ValueError,
        match="target_concept_id is required",
    ):
        build_concepts.record_human_semantic_decision(
            db,
            job.id,
            pending["decision_id"],
            choice="expand_existing",
            owner_sub=auth.LOCAL_OWNER_SUB,
        )


def test_pre_learning_pause_records_decision_then_resumes_explicitly(
    db,
    first_chapter,
    monkeypatch,
):
    job, chapter = _job_at_81_percent(
        db, first_chapter, learning_kind="pre")
    generation_calls = 0
    resolution_seen = False
    identity = {
        "decision_id": _pending_packet()["decision_id"],
        "context_hash": _pending_packet()["context_hash"],
    }

    def post_map(*_args, **_kwargs):
        nonlocal generation_calls, resolution_seen
        generation_calls += 1
        resolution = phase33._human_resolution_for(identity)
        if resolution is None:
            raise semantic_recovery.HumanDecisionRequired(_pending_packet())
        resolution_seen = True
        assert resolution["choice"] == "expand_existing"
        return [{
            "topic": "Nationalism",
            "parent_concept": "Nationalism",
            "concept_title": "Renan's Idea of a Nation",
            "concept_details": "Description: A nation safeguards liberty.",
            "keywords": "nation, liberty",
        }]

    monkeypatch.setattr(
        build_concepts.config, "use_live_generation", lambda: True)
    monkeypatch.setattr(generation, "concepts_from_mmd", post_map)
    monkeypatch.setattr(
        generation,
        "pre_learning_from_rows",
        lambda *_args, **_kwargs: [{
            "topic": "Prerequisites",
            "parent_concept": "Civic Ideas",
            "concept_title": "Meaning of Liberty",
            "concept_details": "Description: Understand liberty.",
            "keywords": "liberty",
        }],
    )
    monkeypatch.setattr(
        build_concepts,
        "_deposit_and_publish_concepts",
        lambda *_args, **_kwargs: (
            [901],
            [],
            {"written": 1, "sources_updated": 0, "parent_column": True},
        ),
    )
    monkeypatch.setattr(
        build_concepts.drive_checkpoints,
        "schedule_checkpoint_backup",
        lambda *_args, **_kwargs: None,
    )

    paused = build_concepts.generate_pre_learning_from_upload(
        db, job.id, chapter.id, owner_sub=auth.LOCAL_OWNER_SUB)
    assert paused["status"] == "awaiting_decision"
    assert generation_calls == 1
    db.refresh(job)
    assert job.learning_kind == "pre"
    assert job.status == "converted"
    assert job.generation_checkpoint["stage"] == "pre_type_assignment"

    recorded = build_concepts.record_human_semantic_decision(
        db,
        job.id,
        paused["pending_decision"]["decision_id"],
        choice="expand_existing",
        owner_sub=auth.LOCAL_OWNER_SUB,
    )
    assert recorded["resume_required"] is True
    assert generation_calls == 1  # submission never starts generation

    result = build_concepts.generate_pre_learning_from_upload(
        db, job.id, chapter.id, owner_sub=auth.LOCAL_OWNER_SUB)
    assert result["concept_ids"] == [901]
    assert generation_calls == 2
    assert resolution_seen is True
    db.refresh(job)
    assert job.status == "generated"
    assert job.generation_checkpoint == {}


def test_concurrent_decision_submission_returns_409(
    client,
    db,
    first_chapter,
    monkeypatch,
):
    job, chapter = _job_at_81_percent(db, first_chapter)
    monkeypatch.setattr(
        build_concepts.drive_checkpoints,
        "schedule_checkpoint_backup",
        lambda *_args, **_kwargs: None,
    )
    pending = _attach_pending(db, job, chapter)
    entered = threading.Event()
    release = threading.Event()
    failures: list[Exception] = []
    original = build_concepts._record_human_semantic_decision_locked

    def slow_record(*args, **kwargs):
        entered.set()
        if not release.wait(timeout=5):
            raise RuntimeError("test did not release the decision writer")
        return original(*args, **kwargs)

    monkeypatch.setattr(
        build_concepts,
        "_record_human_semantic_decision_locked",
        slow_record,
    )

    def first_writer():
        worker_db = SessionLocal()
        try:
            build_concepts.record_human_semantic_decision(
                worker_db,
                job.id,
                pending["decision_id"],
                choice="expand_existing",
                owner_sub=auth.LOCAL_OWNER_SUB,
            )
        except Exception as exc:  # pragma: no cover - asserted below
            failures.append(exc)
        finally:
            worker_db.close()

    writer = threading.Thread(target=first_writer)
    writer.start()
    assert entered.wait(timeout=5)
    contender = client.post(
        f"/build-concepts/uploads/{job.id}/decisions/"
        f"{pending['decision_id']}",
        json={"choice": "expand_existing"},
    )
    assert contender.status_code == 409
    assert "already running" in contender.json()["detail"]
    release.set()
    writer.join(timeout=5)
    assert not writer.is_alive()
    assert failures == []

"""Unattended completion: escalations degrade to the safest offered action.

The audited production runs repeatedly reached the required output only to
pause for a manual review click whenever the bounded resolution agent
escalated (for example: "This source-critical action lacks issue-matched
canonical MMD evidence."). These tests pin the corrected posture: when a safe
server-offered bounded action exists — the same one the review UI highlights
as recommended, or an explicit keep/no-change candidate — the run applies it
deterministically with a full audit trail instead of stopping. Decisions whose
only meaningful action is user-only (source replacement, custom instruction)
still pause, and the operator can restore the old behavior with
AEGIS_UNATTENDED_COMPLETION=0.
"""
from __future__ import annotations

import copy

import pytest

from app import models
from app.services import autonomous_resolution, build_concepts


def _pending_raw(
    *,
    options: list[dict] | None = None,
    candidates: list[dict] | None = None,
) -> dict:
    return {
        "decision_id": "phase31-ground-test-decision-01",
        "kind": "phase31_source_grounding_semantic_conflict",
        "phase": "3.1",
        "conflict": "Two grounding routes remain plausible for one concept.",
        "diagnosis": "The verified source supports one bounded route.",
        "decision_question": "Which grounding route should be kept?",
        "item": {
            "unit_id": "CONCEPT-GROUND-0007",
            "type_id": "",
            "type_title": "",
            "qids": [],
            "questions": [],
            "topic": "Source Topic",
        },
        "candidates": candidates
        if candidates is not None
        else [
            {
                "target_id": "CAND-KEEP-0001",
                "concept_id": "CONCEPT-GROUND-0007",
                "title": "Keep the concept as accepted",
                "topic": "Source Topic",
                "coverage": "",
                "gap": "",
                "action": "keep",
                "source_topic_id": "TOPIC-0001",
                "target_topic_id": "",
                "boundary_relation": "inside",
                "source_kind": "",
                "source_page": "",
                "text_sha256": "",
                "binding_hash": "",
                "source_block_ids": ["BLK-0001"],
            },
        ],
        "evidence": [
            {
                "evidence_id": "PENDING-EVIDENCE-001",
                "page": "7",
                "label": "Verified statement",
                "text": "The chapter source supports the accepted claim.",
            }
        ],
        "options": options
        if options is not None
        else [
            {
                "choice": "select_candidate",
                "label": "Keep the accepted concept",
                "recommended": True,
                "target_id": "CAND-KEEP-0001",
                "target_concept_id": "",
            },
            {
                "choice": "replace_source",
                "label": "Replace the uploaded source",
                "recommended": False,
            },
        ],
    }


def _seed_paused_job(db, chapter, monkeypatch, *, filename: str, raw=None):
    job = models.UploadJob(
        module="build_concepts",
        upload_type="document",
        learning_kind="post",
        filename=filename,
        mmd_text="## Source Topic\nVerified source wording about the topic.",
        status="converted",
    )
    db.add(job)
    db.flush()
    stage = build_concepts.generation._make_concept_checkpoint(
        "pre_type_assignment",
        records=[{
            "topic": "Source Topic",
            "parent_concept": "Parent",
            "concept_title": "Accepted Concept",
            "concept_details": "Description: Accepted.",
            "keywords": "accepted",
        }],
        question_task_inventory={"items": [], "stats": {}},
        mined_types={"types": []},
        method_row_snapshot=[],
    )
    fingerprint = build_concepts._generation_checkpoint_fingerprint(
        job, chapter)
    job.generation_checkpoint = (
        build_concepts._merge_generation_checkpoint_history(
            {},
            stage,
            fingerprint=fingerprint,
            target_identity=build_concepts._generation_target_identity(
                chapter),
            target_chapter_id=chapter.id,
        )
    )
    db.commit()
    db.refresh(job)
    monkeypatch.setattr(
        build_concepts.drive_checkpoints,
        "schedule_checkpoint_backup",
        lambda *_args, **_kwargs: None,
    )
    pending = build_concepts._persist_pending_human_decision(
        db,
        job,
        raw if raw is not None else _pending_raw(),
        fingerprint=fingerprint,
        target_chapter_id=chapter.id,
        owner_sub=None,
    )
    return job, pending


# --------------------------------------------------------------------------- #
# Deterministic safe-option selection
# --------------------------------------------------------------------------- #

def test_recommended_automatable_option_is_selected():
    selected = autonomous_resolution.safe_continuation_option({
        "options": [
            {"choice": "consolidate_types", "recommended": True},
            {"choice": "custom_instruction", "recommended": False},
        ],
        "candidates": [],
    })
    assert selected == {
        "choice": "consolidate_types",
        "target_id": "",
        "target_concept_id": "",
    }


def test_user_only_recommendation_is_never_taken():
    selected = autonomous_resolution.safe_continuation_option({
        "options": [
            {"choice": "replace_source", "recommended": True},
            {"choice": "custom_instruction", "recommended": False},
        ],
        "candidates": [],
    })
    assert selected is None


def test_single_keep_candidate_route_is_the_fallback():
    selected = autonomous_resolution.safe_continuation_option({
        "options": [
            {"choice": "select_candidate", "recommended": False},
            {"choice": "replace_source", "recommended": True},
        ],
        "candidates": [
            {
                "target_id": "CAND-KEEP-0001",
                "concept_id": "CONCEPT-GROUND-0007",
                "action": "keep",
            },
            {
                "target_id": "CAND-MOVE-0002",
                "concept_id": "CONCEPT-GROUND-0008",
                "action": "move",
            },
        ],
    })
    assert selected == {
        "choice": "select_candidate",
        "target_id": "CAND-KEEP-0001",
        "target_concept_id": "CONCEPT-GROUND-0007",
    }


def test_recommended_candidate_choice_requires_a_known_target():
    selected = autonomous_resolution.safe_continuation_option({
        "options": [
            {
                "choice": "accept_recommended",
                "recommended": True,
                "target_id": "CAND-UNKNOWN",
            },
        ],
        "candidates": [
            {"target_id": "CAND-KEEP-0001", "action": "keep"},
        ],
    })
    # The unknown recommended target is refused; the keep route is not
    # offered as an option here, so nothing safe exists.
    assert selected is None


def test_unattended_completion_is_env_gated(monkeypatch):
    monkeypatch.delenv("AEGIS_UNATTENDED_COMPLETION", raising=False)
    assert autonomous_resolution.unattended_completion_enabled() is True
    monkeypatch.setenv("AEGIS_UNATTENDED_COMPLETION", "0")
    assert autonomous_resolution.unattended_completion_enabled() is False


# --------------------------------------------------------------------------- #
# Escalation degrades to a recorded safe continuation, not a pause
# --------------------------------------------------------------------------- #

def test_escalated_review_applies_safe_continuation(
    db,
    first_chapter,
    monkeypatch,
):
    chapter = db.get(models.Chapter, first_chapter["id"])
    job, pending = _seed_paused_job(
        db, chapter, monkeypatch, filename="safe-continuation.mmd")
    monkeypatch.delenv("AEGIS_UNATTENDED_COMPLETION", raising=False)
    monkeypatch.setattr(autonomous_resolution, "enabled", lambda: True)
    monkeypatch.setattr(
        autonomous_resolution,
        "resolve_pending",
        lambda *_args, **_kwargs: autonomous_resolution.ResolutionResult(
            "escalated",
            "This source-critical action lacks issue-matched canonical "
            "MMD evidence.",
        ),
    )

    decision_id = build_concepts._autonomously_resolve_pending_decision(
        db, job, pending, owner_sub=None)

    assert decision_id == pending["decision_id"]
    db.refresh(job)
    ledger = job.generation_checkpoint["human_decisions"]
    assert ledger.get("pending") is None
    resolutions = {
        str(row.get("decision_id") or ""): row
        for row in ledger.get("resolutions") or []
    }
    recorded = resolutions[pending["decision_id"]]
    assert recorded["choice"] == "select_candidate"
    assert recorded["target_id"] == "CAND-KEEP-0001"
    assert recorded["resolved_by"] == "agent"
    assert recorded["status"] == "consumed"
    review = recorded["pending_decision"]["agent_review"]
    assert review["status"] == "resolved"
    assert review["reason"].startswith("Safe continuation after escalation:")
    assert "lacks issue-matched canonical MMD evidence" in review["reason"]


def test_escalation_still_pauses_when_unattended_mode_is_off(
    db,
    first_chapter,
    monkeypatch,
):
    chapter = db.get(models.Chapter, first_chapter["id"])
    job, pending = _seed_paused_job(
        db, chapter, monkeypatch, filename="safe-continuation-off.mmd")
    monkeypatch.setenv("AEGIS_UNATTENDED_COMPLETION", "0")
    monkeypatch.setattr(autonomous_resolution, "enabled", lambda: True)
    monkeypatch.setattr(
        autonomous_resolution,
        "resolve_pending",
        lambda *_args, **_kwargs: autonomous_resolution.ResolutionResult(
            "escalated",
            "Confidence did not meet the safety threshold.",
        ),
    )

    outcome = build_concepts._autonomously_resolve_pending_decision(
        db, job, pending, owner_sub=None)

    assert outcome is None
    db.refresh(job)
    ledger = job.generation_checkpoint["human_decisions"]
    saved = ledger["pending"]
    assert saved["decision_id"] == pending["decision_id"]
    assert saved["agent_review"]["status"] == "escalated"


def test_user_only_decision_still_pauses_even_in_unattended_mode(
    db,
    first_chapter,
    monkeypatch,
):
    chapter = db.get(models.Chapter, first_chapter["id"])
    raw = _pending_raw(
        options=[
            {
                "choice": "replace_source",
                "label": "Replace the uploaded source",
                "recommended": True,
            },
            {
                "choice": "custom_instruction",
                "label": "Give another instruction",
                "recommended": False,
            },
        ],
        candidates=[],
    )
    raw["decision_id"] = "phase31-ground-test-decision-02"
    job, pending = _seed_paused_job(
        db,
        chapter,
        monkeypatch,
        filename="safe-continuation-user-only.mmd",
        raw=raw,
    )
    monkeypatch.delenv("AEGIS_UNATTENDED_COMPLETION", raising=False)
    monkeypatch.setattr(autonomous_resolution, "enabled", lambda: True)
    monkeypatch.setattr(
        autonomous_resolution,
        "resolve_pending",
        lambda *_args, **_kwargs: autonomous_resolution.ResolutionResult(
            "escalated",
            "Only user-authority actions remain for this decision.",
        ),
    )

    outcome = build_concepts._autonomously_resolve_pending_decision(
        db, job, pending, owner_sub=None)

    assert outcome is None
    db.refresh(job)
    ledger = job.generation_checkpoint["human_decisions"]
    assert ledger["pending"]["decision_id"] == pending["decision_id"]


def test_pathway_cap_uses_safe_continuation_without_model_request(
    db,
    first_chapter,
    monkeypatch,
):
    chapter = db.get(models.Chapter, first_chapter["id"])
    raw = _pending_raw()
    raw["decision_id"] = "phase31-ground-test-decision-03"
    job, pending = _seed_paused_job(
        db,
        chapter,
        monkeypatch,
        filename="safe-continuation-cap.mmd",
        raw=raw,
    )
    monkeypatch.delenv("AEGIS_UNATTENDED_COMPLETION", raising=False)
    monkeypatch.setattr(autonomous_resolution, "enabled", lambda: True)
    monkeypatch.setattr(
        autonomous_resolution, "maximum_pathway_turns", lambda: 0)
    monkeypatch.setattr(
        autonomous_resolution,
        "resolve_pending",
        lambda *_args, **_kwargs: pytest.fail(
            "a capped scope must not start a model request"),
    )

    decision_id = build_concepts._autonomously_resolve_pending_decision(
        db, job, pending, owner_sub=None)

    assert decision_id == pending["decision_id"]
    db.refresh(job)
    ledger = job.generation_checkpoint["human_decisions"]
    resolutions = {
        str(row.get("decision_id") or ""): row
        for row in ledger.get("resolutions") or []
    }
    recorded = resolutions[pending["decision_id"]]
    assert recorded["choice"] == "select_candidate"
    assert recorded["resolved_by"] == "agent"
    review = recorded["pending_decision"]["agent_review"]
    assert review["status"] == "resolved"
    assert review["reason"].startswith("Safe continuation:")

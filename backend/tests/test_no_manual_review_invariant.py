"""No semantic pause survives when a bounded automatable action exists.

Unattended completion started as a fallback inside the resolution agent, so
branches that declined *before* the agent ran — a disabled resolver, a
crash-recovery guard, an already-inspected workspace on resume, another
worker's claim — still paused the run. In production that surfaced as a
manual review appearing after 90%, during Phase 3.3 Type-host assignment,
where the offered options can carry no ``recommended`` flag at all.

These tests pin the invariant at the two choke points: whatever declined,
the run continues with the safest server-offered bounded action, and only a
genuinely user-only decision (source replacement / custom instruction) or an
explicit opt-out still pauses.
"""
from __future__ import annotations

import pytest

from app import models
from app.services import autonomous_resolution, build_concepts
from app.services import semantic_recovery


def _phase33_pending(*, concepts: bool = True, target_id: str = "") -> dict:
    """Reproduce the Phase 3.3 post-90% Type-host conflict packet.

    With existing concepts but no pre-selected target, the contract marks
    *no* option as recommended — the exact shape that kept pausing.
    """

    options: list[dict] = []
    if concepts:
        expand: dict = {
            "choice": "expand_existing",
            "label": "Expand the existing concept",
            "recommended": bool(target_id),
        }
        if target_id:
            expand["target_concept_id"] = target_id
        options.append(expand)
    options.append({
        "choice": "create_new",
        "label": "Create a separate source-grounded concept",
        "recommended": not concepts,
    })
    if concepts:
        options.append({
            "choice": "select_existing",
            "label": "Select another existing concept",
            "recommended": False,
        })
    options.append({
        "choice": "custom_instruction",
        "label": "Give a custom instruction",
        "recommended": False,
    })
    return {
        "decision_id": "phase33-host-postninety-0001",
        "kind": "phase33_type_host_semantic_conflict",
        "phase": "3.3",
        "conflict": "TYPE-0003 has no certified host concept.",
        "diagnosis": "Two hosts remain plausible for the mined Type.",
        "decision_question": "Where should TYPE-0003 be hosted?",
        "item": {
            "unit_id": "UNIT-0003",
            "type_id": "TYPE-0003",
            "type_title": "Explain a nationalist symbol",
            "qids": ["QINV-0011"],
            "questions": ["Explain the symbolism of Germania."],
            "topic": "Visualising the Nation",
        },
        "candidates": [
            {
                "concept_id": "CONCEPT-0021",
                "title": "Allegories of the nation",
                "topic": "Visualising the Nation",
                "coverage": "",
                "gap": "",
                "action": "",
                "source_block_ids": ["BLK-00201"],
            },
            {
                "concept_id": "CONCEPT-0022",
                "title": "Germania and her attributes",
                "topic": "Visualising the Nation",
                "coverage": "",
                "gap": "",
                "action": "",
                "source_block_ids": ["BLK-00204"],
            },
        ] if concepts else [],
        "evidence": [{
            "evidence_id": "PENDING-EVIDENCE-001",
            "page": "21",
            "label": "BLK-00201",
            "text": "Germania wears a crown of oak leaves.",
        }],
        "options": options,
    }


def _seed_paused_job(db, chapter, monkeypatch, *, filename: str, raw: dict):
    job = models.UploadJob(
        module="build_concepts",
        upload_type="document",
        learning_kind="post",
        filename=filename,
        mmd_text="## Visualising the Nation\nGermania wears oak leaves.",
        status="converted",
    )
    db.add(job)
    db.flush()
    stage = build_concepts.generation._make_concept_checkpoint(
        "pre_type_assignment",
        records=[{
            "topic": "Visualising the Nation",
            "parent_concept": "Parent",
            "concept_title": "Allegories of the nation",
            "concept_details": "Description: Accepted.",
            "keywords": "allegory",
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
        raw,
        fingerprint=fingerprint,
        target_chapter_id=chapter.id,
        owner_sub=None,
    )
    return job, pending


# --------------------------------------------------------------------------- #
# Selector: the post-90% shape with no recommended option
# --------------------------------------------------------------------------- #

def test_phase33_host_conflict_without_recommendation_still_has_a_safe_action():
    selected = autonomous_resolution.safe_continuation_option(
        _phase33_pending())
    # The first offered automatable action binds to the first candidate
    # concept, which is what expanding the existing host means.
    assert selected == {
        "choice": "expand_existing",
        "target_id": "",
        "target_concept_id": "CONCEPT-0021",
    }


def test_phase33_without_concepts_falls_back_to_create_new():
    selected = autonomous_resolution.safe_continuation_option(
        _phase33_pending(concepts=False))
    assert selected == {
        "choice": "create_new",
        "target_id": "",
        "target_concept_id": "",
    }


def test_recommended_target_still_wins_when_present():
    selected = autonomous_resolution.safe_continuation_option(
        _phase33_pending(target_id="CONCEPT-0022"))
    assert selected == {
        "choice": "expand_existing",
        "target_id": "",
        "target_concept_id": "CONCEPT-0022",
    }


def test_only_user_authority_actions_yield_no_safe_option():
    assert autonomous_resolution.safe_continuation_option({
        "options": [
            {"choice": "replace_source", "recommended": True},
            {"choice": "custom_instruction", "recommended": False},
        ],
        "candidates": [{"concept_id": "CONCEPT-0021"}],
    }) is None


# --------------------------------------------------------------------------- #
# Choke point: every resolver-declined branch still continues
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "decline_reason",
    [
        "resolver_disabled",
        "already_inspected",
        "worker_claim_conflict",
    ],
)
def test_resolver_declined_branches_continue_without_manual_review(
    db,
    first_chapter,
    monkeypatch,
    decline_reason,
):
    chapter = db.get(models.Chapter, first_chapter["id"])
    raw = _phase33_pending()
    raw["decision_id"] = f"phase33-host-{decline_reason[:16]}"
    job, pending = _seed_paused_job(
        db,
        chapter,
        monkeypatch,
        filename=f"{decline_reason}.mmd",
        raw=raw,
    )
    monkeypatch.delenv("AEGIS_UNATTENDED_COMPLETION", raising=False)
    # Every one of these declines returns None from the resolver entry point.
    monkeypatch.setattr(
        build_concepts,
        "_autonomously_resolve_pending_decision",
        lambda *_args, **_kwargs: None,
    )

    continued = build_concepts._apply_last_resort_safe_continuation(
        db, job, pending, owner_sub=None)

    assert continued == pending["decision_id"]
    db.refresh(job)
    ledger = job.generation_checkpoint["human_decisions"]
    assert ledger["pending"] is None
    recorded = ledger["resolutions"][-1]
    assert recorded["choice"] == "expand_existing"
    assert recorded["target_concept_id"] == "CONCEPT-0021"
    assert recorded["resolved_by"] == "agent"
    assert recorded["status"] == "consumed"


def test_resume_path_continues_instead_of_returning_a_pause(
    db,
    first_chapter,
    monkeypatch,
):
    chapter = db.get(models.Chapter, first_chapter["id"])
    raw = _phase33_pending()
    raw["decision_id"] = "phase33-host-resume-0001"
    job, _pending = _seed_paused_job(
        db,
        chapter,
        monkeypatch,
        filename="resume-continues.mmd",
        raw=raw,
    )
    monkeypatch.delenv("AEGIS_UNATTENDED_COMPLETION", raising=False)
    monkeypatch.setattr(
        build_concepts,
        "_autonomously_resolve_pending_decision",
        lambda *_args, **_kwargs: None,
    )

    checkpoint = dict(job.generation_checkpoint or {})
    agent_ids: set[str] = set()
    outcome = build_concepts._existing_human_decision_pause(
        db,
        job,
        checkpoint,
        agent_resolution_ids=agent_ids,
        owner_sub=None,
    )

    # None means "no pause — keep generating".
    assert outcome is None
    assert agent_ids == {"phase33-host-resume-0001"}
    db.refresh(job)
    assert job.generation_checkpoint["human_decisions"]["pending"] is None
    # The refreshed checkpoint was handed back to the caller in place.
    assert checkpoint["human_decisions"]["pending"] is None


def test_user_only_decision_ends_the_run_instead_of_parking_it(
    db,
    first_chapter,
    monkeypatch,
):
    """Unattended runs never park in ``awaiting_decision``.

    A decision whose only routes are replacing the uploaded document or
    writing an instruction cannot be synthesized. An unattended batch cannot
    answer a pause either, so a stalled job is strictly worse than a terminal
    failure: the run ends with the reason recorded.
    """

    chapter = db.get(models.Chapter, first_chapter["id"])
    raw = _phase33_pending(concepts=False)
    raw["decision_id"] = "phase33-host-useronly-unattended"
    raw["options"] = [
        {
            "choice": "replace_source",
            "label": "Replace the uploaded source",
            "recommended": True,
        },
        {
            "choice": "custom_instruction",
            "label": "Give a custom instruction",
            "recommended": False,
        },
    ]
    job, _pending = _seed_paused_job(
        db,
        chapter,
        monkeypatch,
        filename="user-only-unattended.mmd",
        raw=raw,
    )
    monkeypatch.delenv("AEGIS_UNATTENDED_COMPLETION", raising=False)
    monkeypatch.setattr(
        build_concepts,
        "_autonomously_resolve_pending_decision",
        lambda *_args, **_kwargs: None,
    )

    with pytest.raises(
        build_concepts.UnattendedDecisionUnavailable,
        match="a person must take",
    ) as excinfo:
        build_concepts._existing_human_decision_pause(
            db,
            job,
            dict(job.generation_checkpoint or {}),
            agent_resolution_ids=set(),
            owner_sub=None,
        )

    # The message names the blocked routes and how to proceed.
    assert "replace_source" in str(excinfo.value)
    assert "AEGIS_UNATTENDED_COMPLETION=0" in str(excinfo.value)
    # Bounded semantic recovery must not retry a decision only a person can
    # answer, so it is classified non-semantic.
    assert semantic_recovery.classify_failure(
        excinfo.value).recoverable is False


def test_user_only_decision_still_pauses_when_attended(
    db,
    first_chapter,
    monkeypatch,
):
    chapter = db.get(models.Chapter, first_chapter["id"])
    raw = _phase33_pending(concepts=False)
    raw["decision_id"] = "phase33-host-useronly-0001"
    raw["options"] = [
        {
            "choice": "replace_source",
            "label": "Replace the uploaded source",
            "recommended": True,
        },
        {
            "choice": "custom_instruction",
            "label": "Give a custom instruction",
            "recommended": False,
        },
    ]
    job, _pending = _seed_paused_job(
        db,
        chapter,
        monkeypatch,
        filename="user-only-pauses.mmd",
        raw=raw,
    )
    monkeypatch.setenv("AEGIS_UNATTENDED_COMPLETION", "0")
    monkeypatch.setattr(
        build_concepts,
        "_autonomously_resolve_pending_decision",
        lambda *_args, **_kwargs: None,
    )

    outcome = build_concepts._existing_human_decision_pause(
        db,
        job,
        dict(job.generation_checkpoint or {}),
        agent_resolution_ids=set(),
        owner_sub=None,
    )

    assert outcome is not None
    assert outcome["status"] == "awaiting_decision"
    db.refresh(job)
    assert job.generation_checkpoint["human_decisions"]["pending"] is not None


def test_opt_out_restores_the_manual_pause(
    db,
    first_chapter,
    monkeypatch,
):
    chapter = db.get(models.Chapter, first_chapter["id"])
    raw = _phase33_pending()
    raw["decision_id"] = "phase33-host-optout-0001"
    job, pending = _seed_paused_job(
        db,
        chapter,
        monkeypatch,
        filename="opt-out-pauses.mmd",
        raw=raw,
    )
    monkeypatch.setenv("AEGIS_UNATTENDED_COMPLETION", "0")

    assert build_concepts._apply_last_resort_safe_continuation(
        db, job, pending, owner_sub=None) is None
    db.refresh(job)
    assert job.generation_checkpoint["human_decisions"]["pending"] is not None


# --------------------------------------------------------------------------- #
# The decision/resolution loop itself is bounded
# --------------------------------------------------------------------------- #

def test_resolution_cycles_are_bounded_and_name_the_returning_decision(
    db,
    first_chapter,
    monkeypatch,
):
    """An always-resolvable decision that keeps returning must terminate.

    Every mechanism under this loop is individually bounded, but their
    composition was not: an action that resolves one decision can regenerate
    an equivalent decision under a fresh context hash, which the per-issue
    caps never match.
    """

    chapter = db.get(models.Chapter, first_chapter["id"])
    raw = _phase33_pending()
    raw["decision_id"] = "phase33-host-cyclebound-0001"
    job, pending = _seed_paused_job(
        db, chapter, monkeypatch, filename="cycle-bound.mmd", raw=raw)
    monkeypatch.setenv("AEGIS_MAX_RESOLUTION_CYCLES", "3")
    logs: list[str] = []
    monkeypatch.setattr(
        build_concepts.progress, "log",
        lambda message, **_kw: logs.append(str(message)),
    )
    # The agent always succeeds, and the operation always needs another
    # decision — the exact shape seen in production.
    monkeypatch.setattr(
        build_concepts,
        "_autonomously_resolve_pending_decision",
        lambda *_a, **_kw: "resolved-decision",
    )
    monkeypatch.setattr(
        build_concepts,
        "_persist_pending_human_decision",
        lambda *_a, **_kw: pending,
    )

    def never_converges():
        raise semantic_recovery.HumanDecisionRequired({
            "decision_id": pending["decision_id"],
            "context_hash": pending["context_hash"],
        })

    with pytest.raises(
        build_concepts.SemanticResolutionCyclesExhausted,
    ) as excinfo:
        build_concepts._run_with_human_decision_pause(
            never_converges,
            db=db,
            job=job,
            fingerprint=str(job.generation_checkpoint.get("fingerprint") or ""),
            target_chapter_id=chapter.id,
            owner_sub=None,
        )

    message = str(excinfo.value)
    assert "3 autonomous decision cycles" in message
    # The returning decision is named so the loop is diagnosable.
    assert "phase33_type_host_semantic_conflict" in message
    assert "TYPE-0003" in message
    assert "AEGIS_MAX_RESOLUTION_CYCLES" in message
    # Bounded semantic recovery must not retry a non-converging loop.
    assert semantic_recovery.classify_failure(
        excinfo.value).recoverable is False

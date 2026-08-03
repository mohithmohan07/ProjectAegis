"""Regression fixture for the CONCEPT-GROUND-0023 production failure.

Sanitized reproduction of the 02 Aug incident: a semantic repair was reported
for checkpoint row 22, final validation reached zero errors, yet the stale
pre-repair decision/evidence state survived in the durable ledger, replayed the
old rejection, and the global one-repair clamp terminated the run with a
duplicated fatal event.

These tests pin the corrected lifecycle end to end:

* the repair persistence transactionally retires ledger state bound to the
  repaired rows (``human_decisions`` and its agent review history) in the same
  durable write as the repaired checkpoint;
* recovery attempts are keyed by issue signature + candidate hash, so a
  materially changed candidate receives one fresh verification turn while
  identical candidates never loop and a total budget bounds the run;
* a repair dispatch is only ``applied`` until the rerun completes without the
  rejection recurring, and becomes ``succeeded`` exactly then;
* exactly one terminal failure event is emitted per run;
* the database cannot silently diverge from the published workbook: the
  publication intent is durable before commit and is completed by recovery.
"""
from __future__ import annotations

import copy

import pytest

from app import models
from app.bulk_import import workbook_sync
from app.services import (
    build_concepts,
    canonical_source_phase38_boundary_grounding_turnover_contract as phase38,
    checkpoints,
    grounding_certificate,
    progress,
    uploads,
)
from app.services import semantic_recovery as recovery


_STALE_ISSUE_KEY = "1" * 64
_UNRELATED_ISSUE_KEY = "2" * 64
_GROUND_0023_FAILURE = (
    "Phase 3 concept grounding failed independent verification for topic "
    "'Source Topic' after 3 attempt(s): CONCEPT-GROUND-0023 not fully "
    "supported by BLK-00176/BLK-00181; the insecure home-based textile work "
    "claim requires BLK-00156"
)


def _row(title: str, *, topic: str = "Source Topic") -> dict:
    return {
        "topic": topic,
        "parent_concept": "Parent",
        "concept_title": title,
        "concept_details": (
            f"Description: The source describes {title} through the wage "
            "records of home-based weavers, showing how contractors lowered "
            "piece rates season by season. // Misconception/ Error Analysis: "
            f"Misconceptions: Students may believe {title} affected only "
            "urban factory workers rather than home-based weavers.; Error "
            "Analysis: While comparing seasonal piece rates, students may "
            "divide the payment by the number of looms instead of the number "
            "of weavers, overstating each family's income."
        ),
        "keywords": "source, concept",
        "_semantic_topic_id": "TOPIC-0001",
        "_semantic_graph_contract": "SOURCE-CONTRACT-1",
        "_source_block_ids": ["BLK-00176", "BLK-00181"],
        "_source_grounding_contract": "api-verified-source-block-ids",
    }


def _records() -> list[dict]:
    return [_row(f"Concept {index + 1:02d}") for index in range(23)]


def _stage(records: list[dict]) -> dict:
    return build_concepts.generation._make_concept_checkpoint(
        "pre_type_assignment",
        records=records,
        question_task_inventory={
            "items": [
                {
                    "qid": f"QINV-{index + 1:04d}",
                    "topic_hint": "Source Topic",
                    "source_kind": "exercise",
                    "normalized_task": (
                        f"Explain source question {index + 1}: how did "
                        "contractors reduce payments to home-based weavers "
                        "and what consequences followed for their families?"
                    ),
                    "raw_task": (
                        f"Explain source question {index + 1}: how did "
                        "contractors reduce payments to home-based weavers "
                        "and what consequences followed for their families?"
                    ),
                }
                for index in range(3)
            ],
            "stats": {"total": 3},
        },
        mined_types={"types": []},
        method_row_snapshot=[],
    )


def _agent_review(issue_key: str) -> dict:
    return {
        "status": "resolved",
        "resolver_version": "phase38-test-resolver-v1",
        "issue_key": issue_key,
        "started_at": "2026-08-02T16:45:00+00:00",
        "completed_at": "2026-08-02T16:45:20+00:00",
        "choice": "select_candidate",
        "target_id": "CAND-STALE-0001",
    }


def _stale_resolution(unit_id: str = "CONCEPT-GROUND-0023") -> dict:
    issue_key = (
        _STALE_ISSUE_KEY
        if unit_id == "CONCEPT-GROUND-0023"
        else _UNRELATED_ISSUE_KEY
    )
    return {
        "decision_id": f"phase31-ground-stale-{unit_id.lower()}",
        "context_hash": "c" * 64,
        "status": "ready",
        "choice": "select_candidate",
        "instruction": "",
        "target_id": "CAND-STALE-0001",
        "target_concept_id": "",
        "resolved_at": "2026-08-02T16:45:25+00:00",
        "resolved_by": "human",
        "pending_decision": {
            "decision_id": f"phase31-ground-stale-{unit_id.lower()}",
            "context_hash": "c" * 64,
            "kind": "phase31_source_grounding_semantic_conflict",
            "phase": "phase31_source_grounding",
            "conflict": "The selected evidence does not cover every claim.",
            "checkpoint_progress": 0.81,
            "item": {
                "unit_id": unit_id,
                "type_id": "",
                "type_title": "",
                "qids": [],
                "questions": [],
                "topic": "Source Topic",
            },
            "candidates": [
                {
                    "target_id": "CAND-STALE-0001",
                    "action": "use_verified_evidence",
                    "source_block_ids": ["BLK-00176", "BLK-00181"],
                    "coverage": "Silesian weavers uprising evidence",
                }
            ],
            "agent_review": _agent_review(issue_key),
        },
    }


def _seed_job(db, chapter, *, filename: str, ledger: dict | None = None):
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
    envelope = build_concepts._merge_generation_checkpoint_history(
        {},
        _stage(_records()),
        fingerprint=build_concepts._generation_checkpoint_fingerprint(
            job, chapter
        ),
        target_identity=build_concepts._generation_target_identity(chapter),
        target_chapter_id=chapter.id,
    )
    if ledger is not None:
        durable_ledger = copy.deepcopy(ledger)
        durable_ledger.setdefault("context", {
            "fingerprint": envelope["fingerprint"],
            "target_chapter_id": chapter.id,
        })
        durable_ledger.setdefault("deferred_assignment_unit_ids", [])
        envelope[build_concepts._HUMAN_DECISIONS_KEY] = durable_ledger
    job.generation_checkpoint = envelope
    db.commit()
    db.refresh(job)
    return job


# --------------------------------------------------------------------------- #
# Issue-scoped recovery policy (replaces the global 1/1 clamp)
# --------------------------------------------------------------------------- #

def test_materially_changed_candidate_gets_fresh_verification_turn():
    """The same issue may be re-verified once per materially new candidate."""

    revision = 0
    operation_calls = 0
    logs: list[tuple[str, str]] = []
    verified: list[int] = []

    def snapshot() -> dict:
        entry = _stage(_records())
        entry["revision"] = revision
        return {"checkpoints": [entry]}

    def operation():
        nonlocal operation_calls
        operation_calls += 1
        if operation_calls <= 2:
            # The identical rejection text recurs, but against a materially
            # changed candidate the second time (the repair advanced state).
            raise ValueError(_GROUND_0023_FAILURE)
        return "completed"

    def repair(checkpoint, context):
        selected = recovery.select_recovery_checkpoint(checkpoint)
        assert selected is not None
        return recovery.RepairResult(
            checkpoint=selected,
            base_stage="pre_type_assignment",
            changed_row_indexes=(22,),
            repair_signature=f"repair-{context.attempt}",
            scope="checkpoint_rows",
        )

    def persist(_result, _context):
        nonlocal revision
        revision += 1

    result = recovery.run_with_semantic_recovery(
        operation,
        checkpoint_snapshot=snapshot,
        repair_checkpoint=repair,
        persist_repair=persist,
        on_recovery_verified=lambda contexts: verified.append(len(contexts)),
        policy=recovery.RecoveryPolicy(max_attempts=1, max_total_attempts=3),
        log=lambda message, level="info": logs.append((level, str(message))),
    )

    assert result == "completed"
    assert operation_calls == 3
    assert revision == 2
    # Postcondition-first: success is reported only after the rerun completed
    # without the rejection recurring, and the verified hook saw every repair.
    assert verified == [2]
    success_logs = [msg for level, msg in logs if level == "success"]
    assert len(success_logs) == 1
    assert "postcondition verified" in success_logs[0]
    assert not any(level == "error" for level, _msg in logs)


def test_identical_candidate_for_same_issue_never_loops():
    checkpoint = {"checkpoints": [_stage(_records())]}
    repair_calls = 0
    logs: list[tuple[str, str]] = []

    def operation():
        raise ValueError(_GROUND_0023_FAILURE)

    def repair(current, context):
        nonlocal repair_calls
        repair_calls += 1
        selected = recovery.select_recovery_checkpoint(current)
        assert selected is not None
        return recovery.RepairResult(
            checkpoint=selected,
            base_stage="pre_type_assignment",
            changed_row_indexes=(22,),
            repair_signature=f"repair-{context.attempt}",
            scope="checkpoint_rows",
        )

    with pytest.raises(
        recovery.SemanticRecoveryExhausted,
        match="identical failure/checkpoint",
    ):
        recovery.run_with_semantic_recovery(
            operation,
            # The persisted repair never changes the durable candidate, so the
            # second failure is byte-identical: no second paid attempt.
            checkpoint_snapshot=lambda: copy.deepcopy(checkpoint),
            repair_checkpoint=repair,
            persist_repair=lambda *_args: None,
            policy=recovery.RecoveryPolicy(
                max_attempts=1, max_total_attempts=3),
            log=lambda message, level="info": logs.append(
                (level, str(message))),
        )

    assert repair_calls == 1
    error_logs = [msg for level, msg in logs if level == "error"]
    assert len(error_logs) == 1


def test_total_attempt_budget_bounds_distinct_wrong_candidates():
    revision = 0
    repair_calls = 0
    logs: list[tuple[str, str]] = []

    def snapshot() -> dict:
        entry = _stage(_records())
        entry["revision"] = revision
        return {"checkpoints": [entry]}

    def operation():
        raise ValueError(_GROUND_0023_FAILURE)

    def repair(current, context):
        nonlocal repair_calls
        repair_calls += 1
        selected = recovery.select_recovery_checkpoint(current)
        assert selected is not None
        return recovery.RepairResult(
            checkpoint=selected,
            base_stage="pre_type_assignment",
            changed_row_indexes=(22,),
            repair_signature=f"repair-{context.attempt}",
            scope="checkpoint_rows",
        )

    def persist(_result, _context):
        nonlocal revision
        revision += 1

    with pytest.raises(
        recovery.SemanticRecoveryExhausted,
        match="exhausted its bounded 2 repair attempt",
    ):
        recovery.run_with_semantic_recovery(
            operation,
            checkpoint_snapshot=snapshot,
            repair_checkpoint=repair,
            persist_repair=persist,
            policy=recovery.RecoveryPolicy(
                max_attempts=1, max_total_attempts=2),
            log=lambda message, level="info": logs.append(
                (level, str(message))),
        )

    assert repair_calls == 2
    # Terminal idempotency: exactly one terminal failure event for the run.
    error_logs = [msg for level, msg in logs if level == "error"]
    assert len(error_logs) == 1


def test_disabled_recovery_still_reports_zero_budget():
    with pytest.raises(
        recovery.SemanticRecoveryExhausted,
        match="bounded 0 repair attempt",
    ):
        recovery.run_with_semantic_recovery(
            lambda: (_ for _ in ()).throw(ValueError(_GROUND_0023_FAILURE)),
            checkpoint_snapshot=lambda: {
                "checkpoints": [_stage(_records())]},
            repair_checkpoint=lambda *_args: pytest.fail(
                "disabled recovery must not call the provider"),
            persist_repair=lambda *_args: pytest.fail(
                "disabled recovery must not persist"),
            policy=recovery.RecoveryPolicy(max_attempts=0),
        )


# --------------------------------------------------------------------------- #
# Transactional repair propagation across the human-decision ledger
# --------------------------------------------------------------------------- #

def test_repair_persistence_retires_stale_decisions_for_repaired_rows(
    db,
    first_chapter,
    monkeypatch,
):
    """The durable write that installs a repair retires dependent ledger rows.

    This is the exact stale-state pathway of the incident: the pre-repair
    grounding direction for CONCEPT-GROUND-0023 stayed ``ready`` in the copied
    ledger and was replayed after the row-22 repair.
    """

    chapter = db.get(models.Chapter, first_chapter["id"])
    ledger = {
        "version": 1,
        "pending": None,
        "resolutions": [
            _stale_resolution("CONCEPT-GROUND-0023"),
            _stale_resolution("CONCEPT-GROUND-0005"),
        ],
        "agent_review_history": [
            _agent_review(_STALE_ISSUE_KEY),
            _agent_review(_UNRELATED_ISSUE_KEY),
        ],
    }
    ledger["resolutions"][1]["decision_id"] = "phase31-ground-unrelated"
    ledger["resolutions"][1]["pending_decision"]["decision_id"] = (
        "phase31-ground-unrelated"
    )
    ledger["resolutions"][1]["pending_decision"]["agent_review"] = (
        _agent_review(_UNRELATED_ISSUE_KEY)
    )
    job = _seed_job(
        db, chapter, filename="cg-0023-ledger.mmd", ledger=ledger)
    monkeypatch.setattr(
        build_concepts.drive_checkpoints,
        "schedule_checkpoint_backup",
        lambda *_args, **_kwargs: None,
    )

    repaired_records = _records()
    repaired_records[22]["concept_title"] = "Repaired Concept 23"
    repair_signature = "e" * 64
    result = recovery.RepairResult(
        checkpoint=_stage(repaired_records),
        base_stage="pre_type_assignment",
        changed_row_indexes=(22,),
        repair_signature=repair_signature,
        scope="checkpoint_rows",
    )
    build_concepts._persist_semantic_recovery_checkpoint(
        db,
        job,
        result,
        fingerprint=build_concepts._generation_checkpoint_fingerprint(
            job, chapter),
        target_identity=build_concepts._generation_target_identity(chapter),
        target_chapter_id=chapter.id,
        owner_sub=None,
    )
    db.commit()
    db.refresh(job)

    stored = job.generation_checkpoint
    resolutions = {
        str(entry["decision_id"]): entry
        for entry in stored["human_decisions"]["resolutions"]
    }
    retired = resolutions["phase31-ground-stale-concept-ground-0023"]
    assert retired["status"] == "superseded"
    assert retired["superseded_by_repair_signature"] == repair_signature
    # A direction for an unrepaired row survives untouched.
    assert resolutions["phase31-ground-unrelated"]["status"] == "ready"
    history_keys = {
        row["issue_key"]
        for row in stored["human_decisions"]["agent_review_history"]
    }
    assert _STALE_ISSUE_KEY not in history_keys
    assert _UNRELATED_ISSUE_KEY in history_keys
    # The retired direction is invisible to every replay surface.
    assert all(
        row["decision_id"] != "phase31-ground-stale-concept-ground-0023"
        for row in build_concepts._ready_human_decisions(stored)
    )
    # The repaired stage is durably installed.
    newest = build_concepts.generation._newest_compatible_concept_checkpoint(
        stored)
    assert newest is not None
    assert newest["records"][22]["concept_title"] == "Repaired Concept 23"
    # Superseded decisions are a first-class durable state: the repaired
    # checkpoint must still pass the same strict validation used by portable
    # export/Drive backup instead of failing Pydantic's extra-field contract.
    checkpoints._validate_checkpoint(
        stored,
        learning_kind=job.learning_kind,
        mmd_text=job.mmd_text,
        path="checkpoint",
    )


def test_decision_returned_claim_clears_only_with_pending_decision_commit(
    db,
    first_chapter,
    monkeypatch,
):
    chapter = db.get(models.Chapter, first_chapter["id"])
    job = _seed_job(db, chapter, filename="phase38-decision-bridge.mmd")
    pending = copy.deepcopy(_stale_resolution()["pending_decision"])
    pending.pop("agent_review", None)
    checkpoint = copy.deepcopy(job.generation_checkpoint)
    fingerprint = checkpoint["fingerprint"]
    claim = phase38._fresh_convergence_state(
        scope=f"upload-job:{job.id}:{fingerprint}"
    )
    claim["base_candidate_sha256"] = "a" * 64
    claim["candidate_sha256"] = "a" * 64
    claim["dispatch_status"] = "decision_returned"
    claim["dispatch_sequence"] = 1
    claim["dispatch_candidate_sha256"] = "a" * 64
    claim["dispatch_decision_id"] = pending["decision_id"]
    claim["dispatch_decision_context_hash"] = pending["context_hash"]
    checkpoint[build_concepts._PHASE38_CONVERGENCE_KEY] = claim
    job.generation_checkpoint = checkpoint
    db.commit()
    monkeypatch.setattr(
        build_concepts.drive_checkpoints,
        "schedule_checkpoint_backup",
        lambda *_args, **_kwargs: None,
    )

    real_commit = db.commit
    committed_boundaries: list[tuple[str, str]] = []

    def assert_atomic_bridge():
        durable = job.generation_checkpoint or {}
        phase_state = durable.get(
            build_concepts._PHASE38_CONVERGENCE_KEY, {}
        )
        human_pending = (
            (durable.get(build_concepts._HUMAN_DECISIONS_KEY) or {}).get(
                "pending"
            )
            or {}
        )
        if phase_state.get("dispatch_status") == "idle":
            # There is no commit boundary at which the one-shot claim is idle
            # but its exact provider-returned decision is absent.
            assert human_pending.get("decision_id") == pending["decision_id"]
            assert human_pending.get("context_hash") == pending["context_hash"]
        committed_boundaries.append((
            str(phase_state.get("dispatch_status") or ""),
            str(human_pending.get("decision_id") or ""),
        ))
        real_commit()

    monkeypatch.setattr(db, "commit", assert_atomic_bridge)
    stored_pending = build_concepts._persist_pending_human_decision(
        db,
        job,
        pending,
        fingerprint=fingerprint,
        target_chapter_id=chapter.id,
        owner_sub=None,
    )

    assert stored_pending["decision_id"] == pending["decision_id"]
    db.refresh(job)
    durable = job.generation_checkpoint
    phase_state = durable[build_concepts._PHASE38_CONVERGENCE_KEY]
    assert phase_state["dispatch_status"] == "idle"
    assert phase_state["dispatch_decision_id"] == ""
    assert phase_state["dispatch_decision_context_hash"] == ""
    assert durable[build_concepts._HUMAN_DECISIONS_KEY]["pending"][
        "decision_id"
    ] == pending["decision_id"]
    assert committed_boundaries
    assert all(status == "idle" for status, _decision in committed_boundaries)


# --------------------------------------------------------------------------- #
# Combined lifecycle: checkpoint + human_decisions + question_inventory +
# dispatch ledger + postcondition-first success, in one live run shape
# --------------------------------------------------------------------------- #

def test_full_lifecycle_repairs_row_22_without_replaying_stale_rejection(
    db,
    first_chapter,
    monkeypatch,
):
    chapter = db.get(models.Chapter, first_chapter["id"])
    ledger = {
        "version": 1,
        "pending": None,
        "resolutions": [_stale_resolution("CONCEPT-GROUND-0023")],
        "agent_review_history": [
            _agent_review(_STALE_ISSUE_KEY),
        ],
    }
    job = _seed_job(
        db, chapter, filename="cg-0023-lifecycle.mmd", ledger=ledger)

    generation_calls = 0
    verified_statuses: list[list[str]] = []
    deposited_evidence: list[str] = []

    def generate(
        *_args,
        artifacts=None,
        resume_checkpoint=None,
        checkpoint_callback=None,
        **_kwargs,
    ):
        nonlocal generation_calls
        generation_calls += 1
        if generation_calls == 1:
            # The stale pre-repair direction is still live in the first pass —
            # exactly the state the incident run resumed with.
            live = build_concepts._ready_human_decisions(resume_checkpoint)
            assert any(
                str((row.get("item") or {}).get("unit_id") or "")
                == "CONCEPT-GROUND-0023"
                for row in live
            )
            raise ValueError(_GROUND_0023_FAILURE)
        saved = (
            build_concepts.generation._newest_compatible_concept_checkpoint(
                resume_checkpoint)
        )
        assert saved is not None
        assert saved["stage"] == "pre_type_assignment"
        assert saved["records"][22]["concept_title"] == (
            "Insecure Home-Based Textile Work")
        # The prior rejection cannot reappear: the stale direction was retired
        # in the same durable write as the repair, so nothing replays it.
        assert build_concepts._ready_human_decisions(resume_checkpoint) == []
        # The question inventory survived the transactional reduction.
        assert saved["question_task_inventory"]["items"]
        dispatches = resume_checkpoint["semantic_recovery_dispatches"]
        assert len(dispatches["attempts"]) == 1
        assert dispatches["attempts"][0]["status"] == "applied"
        assert dispatches["attempts"][0]["candidate_payload_hash"]
        grounded = copy.deepcopy(saved["records"])
        for index, row in enumerate(grounded):
            row["_source_block_ids"] = (
                ["BLK-00156", "BLK-00176", "BLK-00181"]
                if index == 22
                else ["BLK-00176", "BLK-00181"]
            )
            row["_source_grounding_contract"] = (
                "api-verified-source-block-ids"
            )
            row["_source_grounding_version"] = "lifecycle-grounding-1"
        grounding_certificate.seal_records(
            grounded,
            source_contract_hash="a" * 64,
            semantic_topology_sha256="b" * 64,
            allowed_block_ids={"BLK-00156", "BLK-00176", "BLK-00181"},
        )
        certificate = grounding_certificate.build_final_certificate(grounded)
        if artifacts is not None:
            artifacts["question_task_inventory"] = (
                copy.deepcopy(saved["question_task_inventory"]))
            artifacts["mined_types"] = {"types": []}
            artifacts["final_grounding_certificate"] = copy.deepcopy(
                certificate
            )
        return grounded

    original_verified = (
        build_concepts._persist_semantic_recovery_dispatch_verified)

    def spy_verified(db_session, spied_job, issue_keys):
        original_verified(db_session, spied_job, issue_keys)
        db_session.refresh(spied_job)
        verified_statuses.append([
            str(row.get("status") or "")
            for row in spied_job.generation_checkpoint[
                "semantic_recovery_dispatches"]["attempts"]
        ])

    monkeypatch.setattr(
        build_concepts.generation, "concepts_from_mmd", generate)
    monkeypatch.setattr(
        build_concepts.config, "use_live_generation", lambda: True)
    monkeypatch.setattr(
        build_concepts,
        "_persist_semantic_recovery_dispatch_verified",
        spy_verified,
    )
    monkeypatch.setattr(
        build_concepts.generation,
        "_openai_json",
        lambda *_args, **_kwargs: {
            "blocked": False,
            "repairs": [{
                "row_index": 22,
                "topic": "Source Topic",
                "parent_concept": "Parent",
                "concept_title": "Insecure Home-Based Textile Work",
                "concept_details": (
                    "Description: The source explains insecure home-based "
                    "textile work through the verified block evidence. // "
                    "Misconception/ Error Analysis: Misconceptions: Students "
                    "may conflate it with factory work.; Error Analysis: "
                    "Students may cite the uprising evidence for the "
                    "home-based claim."
                ),
                "keywords": "textile, home-based, insecure work",
                "repair_reason": (
                    "Bound the claim to its own supporting evidence."
                ),
            }],
        },
    )
    def deposit(*_args, **kwargs):
        verified = grounding_certificate.verify_final_certificate(
            kwargs["records"], kwargs["final_grounding_certificate"]
        )
        deposited_evidence.extend(
            kwargs["records"][22]["_source_block_ids"]
        )
        return [], [], {
            "written": 0,
            "sources_updated": 0,
            "parent_column": True,
            "grounding_certificate": verified,
        }

    monkeypatch.setattr(
        build_concepts, "_deposit_and_publish_concepts", deposit)
    monkeypatch.setattr(
        build_concepts.drive_checkpoints,
        "schedule_checkpoint_backup",
        lambda *_args, **_kwargs: None,
    )

    result = build_concepts.generate_post_learning(
        db, job.id, chapter.id)

    assert result["job_id"] == job.id
    assert generation_calls == 2
    assert deposited_evidence == ["BLK-00156", "BLK-00176", "BLK-00181"]
    # Postcondition-first success: the dispatch became ``succeeded`` only
    # after the rerun completed without the rejection recurring.
    assert verified_statuses == [["succeeded"]]
    db.expire_all()
    completed = db.get(models.UploadJob, job.id)
    assert completed.status == "generated"
    assert completed.generation_checkpoint == {}


# --------------------------------------------------------------------------- #
# Terminal idempotency at the persisted-log boundary
# --------------------------------------------------------------------------- #

def test_persisted_generation_log_keeps_one_terminal_event(db, first_chapter):
    chapter = db.get(models.Chapter, first_chapter["id"])
    job = _seed_job(db, chapter, filename="cg-0023-terminal.mmd")
    terminal = (
        "semantic recovery exhausted its bounded 2 repair attempt(s); the "
        "unsupported or rejected content remains unresolved: "
        + _GROUND_0023_FAILURE
    )
    history_token = progress._history.set([])
    try:
        # The recovery runner already emitted the terminal event once.
        progress.log(terminal, level="error")
        uploads.persist_current_generation_log(
            db,
            job.id,
            error=recovery.SemanticRecoveryExhausted(terminal),
        )
    finally:
        progress._history.reset(history_token)
    db.refresh(job)
    terminal_events = [
        event for event in job.generation_log
        if event.get("level") == "error"
        and "exhausted its bounded" in str(event.get("message") or "")
    ]
    assert len(terminal_events) == 1

    # Without a prior emission the diagnostic itself is still persisted once.
    other_job = _seed_job(db, chapter, filename="cg-0023-terminal-2.mmd")
    history_token = progress._history.set([])
    try:
        uploads.persist_current_generation_log(
            db,
            other_job.id,
            error=recovery.SemanticRecoveryExhausted(terminal),
        )
    finally:
        progress._history.reset(history_token)
    db.refresh(other_job)
    terminal_events = [
        event for event in other_job.generation_log
        if event.get("level") == "error"
        and "exhausted its bounded" in str(event.get("message") or "")
    ]
    assert len(terminal_events) == 1


def test_nonrecoverable_failure_has_one_outer_terminal_event(db, first_chapter):
    chapter = db.get(models.Chapter, first_chapter["id"])
    job = _seed_job(db, chapter, filename="provider-contract-terminal.mmd")
    failure = recovery.ProviderResponseContractError(
        "provider response contract failed after mechanical correction"
    )

    def generation_attempt():
        return recovery.run_with_semantic_recovery(
            lambda: (_ for _ in ()).throw(failure),
            checkpoint_snapshot=lambda: copy.deepcopy(
                job.generation_checkpoint
            ),
            repair_checkpoint=lambda *_args: pytest.fail(
                "a nonrecoverable provider contract failure cannot be repaired"
            ),
            persist_repair=lambda *_args: pytest.fail(
                "a nonrecoverable provider contract failure cannot be saved"
            ),
            log=progress.log,
        )

    history_token = progress._history.set([])
    try:
        with pytest.raises(recovery.ProviderResponseContractError):
            uploads.run_with_openai_usage(db, job.id, generation_attempt)
    finally:
        progress._history.reset(history_token)
    db.refresh(job)
    errors = [
        event for event in job.generation_log
        if event.get("type") == "log" and event.get("level") == "error"
    ]
    assert len(errors) == 1
    assert errors[0]["error"]["exception_type"] == (
        "ProviderResponseContractError"
    )
    assert "Generation stopped without semantic retry" not in errors[0][
        "message"
    ]

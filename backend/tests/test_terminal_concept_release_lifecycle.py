"""A diagnostic Concept checkpoint is reviewable/resumable, never publishable."""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app import models, schemas
from app.services import auth
from app.services import assessment_release_run
from app.services import assessment_release_service
from app.services import build_concepts
from app.services import build_concepts_release as release
from app.services import build_concepts_release_api_contract as release_api
from app.services import build_concepts_release_contract as release_contract
from app.services import build_concepts_release_files as release_files
from app.services import build_concepts_release_manifest as release_manifest
from app.services import build_concepts_release_publication as publication
from app.services import build_concepts_terminal_release_contract as terminal
from app.services import checkpoints, generation_recovery

from tests.test_build_concepts_release import _job, _records


def _checkpoint(stage: str, progress: float) -> dict:
    return {
        "stage": stage,
        "progress": progress,
        "target_chapter_id": 0,
    }


def _job_97_q24_message() -> str:
    return (
        "Unattended generation stopped instead of settling "
        "kind=phase3_source_graph_review, phase=3, unit=BLK-00595, "
        "type=semantic_source_rich_text, topic=ELECTRIC POWER, "
        "issue=88812f3c03e6 with carry_forward: carrying non-canonical rich "
        "text forward is a proven dead end — the semantic-graph integrity "
        "gate downstream refuses the graph and every resume replays the same "
        "refusal. Convert the PDF again as a new upload: sources converted "
        "before \\mathrm ingestion canonicalization are cured by reconversion. "
        "If the same pause returns on a fresh conversion, the named block "
        "genuinely needs a corrected source document."
    )


def _mark_non_resumable(db, job) -> None:
    inventory = dict(job.question_inventory or {})
    inventory[models.GENERATION_RECOVERY_INVENTORY_KEY] = {
        "error": "UnattendedDecisionUnavailable: Q24",
        "message": "Generation did not complete.",
        "resume_allowed": False,
        "recovery_action": "reconvert_new_upload",
        "recovery": "Start a new upload and conversion.",
    }
    job.question_inventory = inventory
    job.generation_checkpoint = _checkpoint("source_graph_review", 0.05)
    db.commit()
    db.refresh(job)


def test_failed_checkpoint_stays_resumable_and_database_closed(db):
    terminal.install()
    job, chapter = _job(db)

    release.stage_release(
        db,
        job,
        target_chapter_id=chapter.id,
        records=_records(),
        inventory={"items": [], "stats": {"items": 0}},
        mined_types={"types": []},
        checkpoint=_checkpoint("description_method_snapshot", 0.55),
        error=RuntimeError("inventory disagreement after retry"),
        reason="diagnostic checkpoint",
    )

    payload = release.release_payload(job)
    assert payload is not None
    # Restructure A: the verdict is decided once at staging and recorded
    # explicitly on the payload (and its summary), so later consumers read
    # the fact instead of re-deriving it from checkpoint echoes.
    assert payload[terminal.TERMINAL_GENERATION_FIELD] is False
    assert payload["summary"][terminal.TERMINAL_GENERATION_FIELD] is False
    assert terminal.payload_terminal_generation_complete(payload) is False
    assert job.status == terminal.PARTIAL_RELEASE_STATUS
    assert job.checkpoint_available is True
    assert job.generation_recovery == {}
    assert terminal.TERMINAL_GENERATION_DEFECT in "\n".join(
        release.structural_defects(payload)
    )

    with pytest.raises(ValueError, match=terminal.TERMINAL_GENERATION_DEFECT):
        publication.upload_release_to_database(db, job.id)

    # Every public gate must see the same defect. These modules import the
    # function by name, so the terminal contract deliberately rebinds them.
    assert release_files.structural_defects is release.structural_defects
    assert release_manifest.structural_defects is release.structural_defects
    manifest = release_manifest.release_artifact_entries(job)
    database_entry = next(
        row for row in manifest if row.get("kind") == "database_upload"
    )
    assert any(
        terminal.TERMINAL_GENERATION_DEFECT in defect
        for defect in database_entry.get("structural_defects") or []
    )


def test_q24_checkpoint_is_durable_but_never_rediscovered_as_resumable(db):
    """The immediate response and every later job reader agree on recovery."""

    terminal.install()
    job, chapter = _job(db)
    error = build_concepts.UnattendedDecisionUnavailable(
        "non-canonical rich text cannot be carried forward",
        resume_allowed=False,
        recovery_action="reconvert_new_upload",
        recovery_message=(
            "Do not resume this checkpoint. Start a new upload and conversion."
        ),
    )

    release.stage_release(
        db,
        job,
        target_chapter_id=chapter.id,
        records=_records(),
        inventory={"items": [], "stats": {"items": 0}},
        mined_types={"types": []},
        checkpoint=_checkpoint("source_graph_review", 0.05),
        error=error,
        reason="Q24 diagnostic checkpoint",
    )

    db.refresh(job)
    assert job.status == release.RELEASE_STATUS
    assert job.generation_checkpoint["stage"] == "source_graph_review"
    assert job.checkpoint_available is False
    assert job.generation_recovery == {
        "error": (
            "UnattendedDecisionUnavailable: non-canonical rich text cannot "
            "be carried forward"
        ),
        "message": (
            "Generation did not complete and this checkpoint is not "
            "resumable. Do not resume this checkpoint. Start a new upload "
            "and conversion."
        ),
        "resume_allowed": False,
        "recovery_action": "reconvert_new_upload",
        "recovery": (
            "Do not resume this checkpoint. Start a new upload and conversion."
        ),
    }
    serialized = schemas.UploadJobOut.model_validate(job).model_dump()
    assert serialized["generation_recovery"]["resume_allowed"] is False
    payload = release.release_payload(job)
    assert payload is not None
    assert payload[terminal.RUN_RECOVERY_FIELD]["resume_allowed"] is False
    defects = "\n".join(release.structural_defects(payload))
    assert "forbids resuming this checkpoint" in defects

    # Exercise the SQL predicate independently of the released-status guard:
    # an imported/legacy lifecycle can say converted, but the durable marker
    # still makes both the model property and discovery endpoint say no.
    job.status = terminal.PARTIAL_RELEASE_STATUS
    db.commit()
    db.refresh(job)
    assert job.checkpoint_available is False
    listed, _total = checkpoints.resumable_jobs(db, learning_kind="post")
    assert not any(row["id"] == job.id for row in listed)

    # A later successful staging clears a stale marker rather than poisoning
    # the new run's lifecycle forever.
    release.stage_release(
        db,
        job,
        target_chapter_id=chapter.id,
        records=_records(),
        inventory={"items": [], "stats": {"items": 0}},
        mined_types={"types": []},
        checkpoint=_checkpoint("final_content_ready", 1.0),
        reason="clean retry from a new conversion",
    )
    db.refresh(job)
    assert job.generation_recovery == {}


def test_pre_marker_q24_job_is_backfilled_without_replaying_generation(db):
    """Measured job 97 becomes non-resumable on its first read after deploy."""

    job, _chapter = _job(db)
    job.status = terminal.PARTIAL_RELEASE_STATUS
    job.generation_checkpoint = _checkpoint("source_graph_review", 0.05)
    job.generation_log = [{
        "type": "log",
        "level": "error",
        "message": _job_97_q24_message(),
    }]
    inventory = dict(job.question_inventory or {})
    inventory.pop("_aegis_generation_recovery", None)
    job.question_inventory = inventory
    db.commit()
    db.refresh(job)

    assert job.generation_recovery["resume_allowed"] is False
    assert job.checkpoint_available is False
    assert "_aegis_generation_recovery" not in job.question_inventory

    listed, _total = checkpoints.resumable_jobs(db, learning_kind="post")
    assert not any(row["id"] == job.id for row in listed)
    db.refresh(job)
    assert job.question_inventory[
        "_aegis_generation_recovery"
    ]["recovery_action"] == "reconvert_new_upload"


def test_legacy_q24_backfill_rejects_a_near_match(db, monkeypatch):
    """One changed identity byte must not classify an ordinary run as Q24."""

    # The full suite intentionally leaves many resumable fixtures in the
    # shared test database. This assertion is about the predicate, not the
    # production first-page cap, so include every matching row here.
    monkeypatch.setattr(checkpoints, "MAX_RESUMABLE_JOBS", 1_000_000)

    job, _chapter = _job(db)
    job.status = terminal.PARTIAL_RELEASE_STATUS
    job.generation_checkpoint = _checkpoint("source_graph_review", 0.05)
    job.generation_log = [{
        "type": "log",
        "level": "error",
        "message": _job_97_q24_message().replace("BLK-00595", "BLK-00596"),
    }]
    db.commit()
    db.refresh(job)

    assert job.generation_recovery == {}
    assert job.checkpoint_available is True
    listed, _total = checkpoints.resumable_jobs(db, learning_kind="post")
    assert any(row["id"] == job.id for row in listed)
    db.refresh(job)
    assert models.GENERATION_RECOVERY_INVENTORY_KEY not in job.question_inventory


def test_post_generate_refuses_non_resumable_before_stream_or_provider(
    db, monkeypatch,
):
    """A stale/direct client cannot replay job 97 around the hidden UI action."""

    job, chapter = _job(db)
    inventory = dict(job.question_inventory or {})
    inventory[models.GENERATION_RECOVERY_INVENTORY_KEY] = {
        "error": "UnattendedDecisionUnavailable: Q24",
        "message": "Generation did not complete.",
        "resume_allowed": False,
        "recovery_action": "reconvert_new_upload",
        "recovery": "Start a new upload and conversion.",
    }
    job.question_inventory = inventory
    job.status = terminal.PARTIAL_RELEASE_STATUS
    job.generation_checkpoint = _checkpoint("source_graph_review", 0.05)
    db.commit()

    monkeypatch.setattr(release_api.uploads, "is_job_running", lambda _id: False)

    def forbidden_stream(*_args, **_kwargs):
        raise AssertionError("blocked recovery entered the generation stream")

    monkeypatch.setattr(release_api.progress, "stream", forbidden_stream)
    with pytest.raises(HTTPException) as caught:
        release_api._post_generate_endpoint(
            job.id,
            schemas.PostLearningGenerateRequest(target_chapter_id=chapter.id),
            db=db,
            user=auth.LOCAL_PRINCIPAL,
        )

    assert caught.value.status_code == 409
    assert "Start a new upload and conversion" in str(caught.value.detail)


def test_post_generate_keeps_ordinary_checkpoint_resumable(db, monkeypatch):
    """Only explicit resume_allowed=false closes the existing resume route."""

    job, chapter = _job(db)
    job.status = terminal.PARTIAL_RELEASE_STATUS
    job.generation_checkpoint = _checkpoint("description_method_snapshot", 0.55)
    db.commit()

    monkeypatch.setattr(release_api.uploads, "is_job_running", lambda _id: False)
    streamed: list[int] = []

    def capture_stream(_work, **kwargs):
        streamed.append(int(kwargs["journal_job_id"]))
        return {"stream": "started"}

    monkeypatch.setattr(release_api.progress, "stream", capture_stream)
    result = release_api._post_generate_endpoint(
        job.id,
        schemas.PostLearningGenerateRequest(target_chapter_id=chapter.id),
        db=db,
        user=auth.LOCAL_PRINCIPAL,
    )

    assert result == {"stream": "started"}
    assert streamed == [job.id]


def test_service_guard_blocks_before_instruction_assembly(db, monkeypatch):
    """Internal callers cannot bypass the POST guard into provider work."""

    job, chapter = _job(db)
    inventory = dict(job.question_inventory or {})
    inventory[models.GENERATION_RECOVERY_INVENTORY_KEY] = {
        "error": "UnattendedDecisionUnavailable: Q24",
        "message": "Generation did not complete.",
        "resume_allowed": False,
        "recovery_action": "reconvert_new_upload",
        "recovery": "Start a new upload and conversion.",
    }
    job.question_inventory = inventory
    job.generation_checkpoint = _checkpoint("source_graph_review", 0.05)
    db.commit()

    def forbidden_architect(*_args, **_kwargs):
        raise AssertionError("blocked recovery reached instruction assembly")

    monkeypatch.setattr(
        build_concepts.instruction_architect,
        "ensure_instruction_set",
        forbidden_architect,
    )
    with pytest.raises(
        build_concepts.UnattendedDecisionUnavailable,
    ) as caught:
        build_concepts.generate_post_learning(
            db,
            job.id,
            chapter.id,
            owner_sub=auth.LOCAL_PRINCIPAL.sub,
        )

    assert caught.value.resume_allowed is False
    assert caught.value.recovery_action == "reconvert_new_upload"
    assert caught.value.recovery_message == "Start a new upload and conversion."


def test_release_wrapper_blocks_before_diagnostic_restaging(db, monkeypatch):
    """Direct wrapper callers cannot turn a blocked replay into a new release."""

    job, chapter = _job(db)
    _mark_non_resumable(db, job)
    before = dict(job.question_inventory)

    def forbidden_stage(*_args, **_kwargs):
        raise AssertionError("blocked wrapper staged a diagnostic release")

    monkeypatch.setattr(
        release_contract, "_stage_generation_release", forbidden_stage
    )
    with pytest.raises(generation_recovery.NonResumableRunError):
        release_contract.generate_post_learning(
            db,
            job.id,
            chapter.id,
            owner_sub=auth.LOCAL_PRINCIPAL.sub,
        )

    db.refresh(job)
    assert job.question_inventory == before


def test_explicit_restaging_cannot_clear_non_resumable_recovery(db):
    """The explicit release mutation cannot reopen a terminal checkpoint."""

    terminal.install()
    job, _chapter = _job(db)
    _mark_non_resumable(db, job)
    before = dict(job.question_inventory)

    with pytest.raises(generation_recovery.NonResumableRunError):
        release.force_release(db, job.id)

    db.refresh(job)
    assert job.question_inventory == before
    assert job.generation_recovery["resume_allowed"] is False
    assert job.checkpoint_available is False


def test_ordinary_force_release_remains_available(db):
    job, _chapter = _job(db)

    released = release.force_release(db, job.id)

    assert released.id == job.id
    assert release.release_available(released)
    assert released.generation_recovery == {}


@pytest.mark.parametrize("claim_job_lock", [False, True])
def test_master_rebuild_refuses_non_resumable_before_storage_or_provider(
    db, monkeypatch, claim_job_lock,
):
    job, _chapter = _job(db)
    _mark_non_resumable(db, job)
    before = dict(job.question_inventory)

    def forbidden_capacity(*_args, **_kwargs):
        raise AssertionError("blocked Master rebuild reserved storage")

    def forbidden_runner(*_args, **_kwargs):
        raise AssertionError("blocked Master rebuild entered a provider runner")

    monkeypatch.setattr(
        release_contract.storage_capacity,
        "reserve_master_capacity",
        forbidden_capacity,
    )
    monkeypatch.setattr(
        assessment_release_run, "run_release_for_job", forbidden_runner
    )
    with pytest.raises(generation_recovery.NonResumableRunError):
        release_contract.rebuild_lane_master(
            db,
            job.id,
            release.LANE_POST,
            owner_sub=auth.LOCAL_PRINCIPAL.sub,
            claim_job_lock=claim_job_lock,
        )

    db.refresh(job)
    assert job.question_inventory == before


def test_direct_master_runner_refuses_before_profile_or_provider(db, monkeypatch):
    job, _chapter = _job(db)
    _mark_non_resumable(db, job)

    def forbidden_profile(*_args, **_kwargs):
        raise AssertionError("blocked Master runner resolved provider profile")

    monkeypatch.setattr(
        assessment_release_run.assessment_profile, "resolve", forbidden_profile
    )
    with pytest.raises(generation_recovery.NonResumableRunError):
        assessment_release_run.run_release_for_job(
            db, job.id, owner_sub=auth.LOCAL_PRINCIPAL.sub
        )
    with pytest.raises(generation_recovery.NonResumableRunError):
        assessment_release_run.run_pre_release_for_job(
            db, job.id, owner_sub=auth.LOCAL_PRINCIPAL.sub
        )


def test_non_resumable_publication_paths_refuse_before_mutation(
    client, db, monkeypatch,
):
    job, _chapter = _job(db)
    _mark_non_resumable(db, job)

    def forbidden_verdict(*_args, **_kwargs):
        raise AssertionError("blocked Concept publication mutated its verdict")

    monkeypatch.setattr(
        terminal, "ensure_explicit_terminal_verdict", forbidden_verdict
    )
    with pytest.raises(generation_recovery.NonResumableRunError):
        publication.upload_release_to_database(
            db, job.id, owner_sub=auth.LOCAL_PRINCIPAL.sub, lane="post"
        )

    master = models.AssessmentRelease(
        release_uid=f"blocked-master-{job.id}",
        version=1,
        owner_sub=auth.LOCAL_PRINCIPAL.sub,
        job_id=job.id,
        lane="post",
        state="ready_for_upload",
    )
    db.add(master)
    db.commit()
    db.refresh(master)
    with pytest.raises(generation_recovery.NonResumableRunError):
        assessment_release_service.upload_master_to_database(
            db, master, owner_sub=auth.LOCAL_PRINCIPAL.sub
        )
    master_response = client.post(
        f"/build-assessments/releases/{master.id}/upload-to-database"
    )
    assert master_response.status_code == 409, master_response.text


def test_non_resumable_mutation_routes_answer_409_before_dispatch(
    client, db, monkeypatch,
):
    job, _chapter = _job(db)
    _mark_non_resumable(db, job)

    def forbidden_force(*_args, **_kwargs):
        raise AssertionError("blocked release route force-staged the job")

    def forbidden_decision(*_args, **_kwargs):
        raise AssertionError("blocked decision route recorded an answer")

    monkeypatch.setattr(release, "force_release", forbidden_force)

    def forbidden_locked_decision(*_args, **_kwargs):
        raise AssertionError("blocked decision service mutated its ledger")

    monkeypatch.setattr(
        build_concepts,
        "_record_human_semantic_decision_locked",
        forbidden_locked_decision,
    )
    with pytest.raises(generation_recovery.NonResumableRunError):
        build_concepts.record_human_semantic_decision(
            db,
            job.id,
            "Q24",
            choice="accept_recommended",
            owner_sub=auth.LOCAL_PRINCIPAL.sub,
        )
    with pytest.raises(generation_recovery.NonResumableRunError):
        release.backfill_missing_pre_release(db, job)

    monkeypatch.setattr(
        build_concepts, "record_human_semantic_decision", forbidden_decision
    )
    released = client.post(f"/build-concepts/uploads/{job.id}/release")
    assert released.status_code == 409
    decision = client.post(
        f"/build-concepts/uploads/{job.id}/decisions/Q24",
        json={"choice": "accept_recommended"},
    )
    assert decision.status_code == 409
    assert "Start a new upload and conversion" in decision.json()["detail"]
    concept_publish = client.post(
        f"/build-concepts/uploads/{job.id}/upload-release",
        params={"lane": "post"},
    )
    assert concept_publish.status_code == 409, concept_publish.text

    post_master = client.post(
        f"/build-assessments/releases/from-job/{job.id}"
    )
    pre_master = client.post(
        f"/build-assessments/releases/from-job/{job.id}/pre"
    )
    assert post_master.status_code == 409, post_master.text
    assert pre_master.status_code == 409, pre_master.text


def test_failure_before_any_checkpoint_is_diagnostic_not_fake_resumable(db):
    terminal.install()
    job, chapter = _job(db)

    release.stage_release(
        db,
        job,
        target_chapter_id=chapter.id,
        records=[],
        inventory={"items": [], "stats": {"items": 0}},
        mined_types={"types": []},
        checkpoint={},
        error=RuntimeError("provider quota exhausted before first checkpoint"),
        reason="failure before durable progress",
    )

    payload = release.release_payload(job)
    assert payload is not None
    assert terminal.payload_terminal_generation_complete(payload) is False
    assert job.status == release.RELEASE_STATUS
    assert job.checkpoint_available is False
    assert terminal.TERMINAL_GENERATION_DEFECT in "\n".join(
        release.structural_defects(payload)
    )


def test_final_content_ready_release_keeps_normal_released_lifecycle(db):
    terminal.install()
    job, chapter = _job(db)

    release.stage_release(
        db,
        job,
        target_chapter_id=chapter.id,
        records=_records(),
        inventory={"items": [], "stats": {"items": 0}},
        mined_types={"types": []},
        checkpoint=_checkpoint("final_content_ready", 1.0),
        reason="clean terminal release",
    )

    payload = release.release_payload(job)
    assert payload is not None
    # Restructure A: a clean terminal exit records its verdict explicitly.
    assert payload[terminal.TERMINAL_GENERATION_FIELD] is True
    assert payload["summary"][terminal.TERMINAL_GENERATION_FIELD] is True
    assert terminal.payload_terminal_generation_complete(payload) is True
    assert job.status == release.RELEASE_STATUS
    assert job.checkpoint_available is False
    assert terminal.TERMINAL_GENERATION_DEFECT not in "\n".join(
        release.structural_defects(payload)
    )


_CLEAN_CAPTURE_REASON = (
    "Generation completed. The output was staged and was not uploaded to "
    "the database."
)


def _v3_checkpoint(*stages: str) -> dict:
    return {
        "checkpoint_format": "aegis-concept-stage-history",
        "schema_version": 3,
        "checkpoints": [
            {"stage": stage, "progress": 0.5} for stage in stages
        ],
        "target_chapter_id": 0,
    }


def test_captured_deposit_staging_is_terminal_whatever_the_snapshot_reads(
    db, monkeypatch,
):
    """The run's own completion fact decides the verdict. A staging that
    carries a captured terminal deposit records complete=True even when the
    strict resume filter insists the snapshot is mid-run — that filter froze
    a completed run as non-terminal ([measured] 2026-08-30); it may still
    feed the payload's display echo, but never the verdict."""
    terminal.install()
    monkeypatch.setattr(
        terminal.generation,
        "_newest_compatible_concept_checkpoint",
        lambda *_a, **_k: {"stage": "post_type_assignment"},
    )
    job, chapter = _job(db)

    token = release.TERMINAL_DEPOSIT_STAGING.set(True)
    try:
        release.stage_release(
            db,
            job,
            target_chapter_id=chapter.id,
            records=_records(),
            inventory={"items": [], "stats": {"items": 0}},
            mined_types={"types": []},
            checkpoint=_v3_checkpoint("post_type_assignment"),
            reason=_CLEAN_CAPTURE_REASON,
        )
    finally:
        release.TERMINAL_DEPOSIT_STAGING.reset(token)

    payload = release.release_payload(job)
    assert payload is not None
    assert payload[terminal.TERMINAL_GENERATION_FIELD] is True
    assert payload["summary"][terminal.TERMINAL_GENERATION_FIELD] is True
    # The completed run keeps its released lifecycle: no flip back to a
    # "resumable" converted status.
    assert job.status == release.RELEASE_STATUS
    assert terminal.TERMINAL_GENERATION_DEFECT not in "\n".join(
        release.structural_defects(payload)
    )


def test_recorded_stage_is_read_raw_never_through_the_resume_filter(
    db, monkeypatch,
):
    """What stage the run reached is a recorded fact. The old derivation
    filtered entries through strict resume compatibility, which (a) demoted
    a terminal run whose final entry flunked one strict check and (b)
    INVERTED severity — a checkpoint whose entries were all incompatible
    fell through to an absent top-level stage and read as terminal."""
    terminal.install()
    # Simulate the strict filter disagreeing in both directions: it must
    # not be consulted at all.
    monkeypatch.setattr(
        terminal.generation,
        "_newest_compatible_concept_checkpoint",
        lambda *_a, **_k: None,
    )

    # (a) newest RECORDED stage is terminal → verdict True.
    job, chapter = _job(db)
    release.stage_release(
        db,
        job,
        target_chapter_id=chapter.id,
        records=_records(),
        inventory={"items": [], "stats": {"items": 0}},
        mined_types={"types": []},
        checkpoint=_v3_checkpoint("question_inventory", "final_content_ready"),
        reason="clean terminal release",
    )
    payload = release.release_payload(job)
    assert payload is not None
    assert payload[terminal.TERMINAL_GENERATION_FIELD] is True
    assert job.status == release.RELEASE_STATUS

    # (b) the inversion: a checkpoint recording ONLY a mid-run stage is
    # non-terminal even when the strict filter rejects every entry (the
    # pre-fix code read this exact shape as complete).
    job2, chapter2 = _job(db)
    release.stage_release(
        db,
        job2,
        target_chapter_id=chapter2.id,
        records=_records(),
        inventory={"items": [], "stats": {"items": 0}},
        mined_types={"types": []},
        checkpoint=_v3_checkpoint("question_inventory"),
        reason="checkpoint staging",
    )
    payload2 = release.release_payload(job2)
    assert payload2 is not None
    assert payload2[terminal.TERMINAL_GENERATION_FIELD] is False
    # Strictly-unusable entries are not offered as resumable: the release
    # stays a released diagnostic rather than a restart-from-zero convert.
    assert job2.status == release.RELEASE_STATUS


def test_a_misrecorded_clean_capture_verdict_is_repaired_on_read(db):
    """Runs staged by the pre-fix code can carry complete=False on a payload
    whose own record says the run completed (the clean-capture staging
    sentence, captured records, no error). The first consumer that asks
    through ``ensure_explicit_terminal_verdict`` gets the corrected fact,
    durably — the Master lanes become buildable again."""
    terminal.install()
    job, chapter = _job(db)

    # Reproduce the damaged shape exactly: clean-capture staging whose
    # snapshot reads mid-run, staged WITHOUT the deposit fact (as the
    # pre-fix deploy did) — verdict frozen False, status even flipped.
    release.stage_release(
        db,
        job,
        target_chapter_id=chapter.id,
        records=_records(),
        inventory={"items": [], "stats": {"items": 0}},
        mined_types={"types": []},
        checkpoint=_checkpoint("post_type_assignment", 0.9),
        reason=_CLEAN_CAPTURE_REASON,
    )
    payload = release.release_payload(job)
    assert payload is not None
    assert payload[terminal.TERMINAL_GENERATION_FIELD] is False
    assert job.status == terminal.PARTIAL_RELEASE_STATUS

    assert terminal.ensure_explicit_terminal_verdict(
        db, job, lane=release.LANE_POST,
    ) is True
    repaired = release.release_payload(job)
    assert repaired is not None
    assert repaired[terminal.TERMINAL_GENERATION_FIELD] is True
    assert repaired["summary"][terminal.TERMINAL_GENERATION_FIELD] is True
    assert terminal.TERMINAL_GENERATION_DEFECT not in "\n".join(
        release.structural_defects(repaired)
    )
    # The same bug flipped the finished run back to a "resumable"
    # converted lifecycle; the repair restores the released state too.
    assert job.status == release.RELEASE_STATUS
    assert "Repaired" in str(job.detail or "")


def test_an_unreadable_stage_history_envelope_is_never_terminal(db):
    """A stage-history envelope whose entries this build cannot read (an
    unrecognized schema version) records a run state we cannot call
    terminal — falling through to the absent top-level stage resurrected
    the severity inversion through the schema gate."""
    terminal.install()
    job, chapter = _job(db)

    unreadable = {
        "checkpoint_format": "aegis-concept-stage-history",
        "schema_version": 99,
        "checkpoints": [{"stage": "question_inventory", "progress": 0.4}],
        "target_chapter_id": 0,
    }
    release.stage_release(
        db,
        job,
        target_chapter_id=chapter.id,
        records=_records(),
        inventory={"items": [], "stats": {"items": 0}},
        mined_types={"types": []},
        checkpoint=unreadable,
        reason="checkpoint staging",
    )
    payload = release.release_payload(job)
    assert payload is not None
    assert payload[terminal.TERMINAL_GENERATION_FIELD] is False


def test_resumable_is_still_judged_by_the_strict_resume_filter(db, monkeypatch):
    """"Resumable" is a promise about the resume machinery. A checkpoint the
    strict filter rejects entirely must NOT flip a diagnostic release back
    to a converted job that can only restart from zero — the verdict reads
    the recorded stage raw, but resumability keeps the strict read."""
    terminal.install()
    monkeypatch.setattr(
        terminal.generation,
        "_newest_compatible_concept_checkpoint",
        lambda *_a, **_k: None,
    )
    job, chapter = _job(db)

    release.stage_release(
        db,
        job,
        target_chapter_id=chapter.id,
        records=_records(),
        inventory={"items": [], "stats": {"items": 0}},
        mined_types={"types": []},
        checkpoint=_v3_checkpoint("question_inventory"),
        error=RuntimeError("worker died mid-run"),
        reason="diagnostic checkpoint",
    )
    payload = release.release_payload(job)
    assert payload is not None
    assert payload[terminal.TERMINAL_GENERATION_FIELD] is False
    # No strict-compatible entry -> not offered as resumable: the release
    # stays a released diagnostic instead of a converted restart-from-zero.
    assert job.status == release.RELEASE_STATUS


def test_a_genuine_failure_verdict_is_never_repaired(db):
    """The repair corrects only the provable mis-record. A failure staging
    keeps its different sentence and its recorded error; its False verdict
    stands on every read."""
    terminal.install()
    job, chapter = _job(db)

    release.stage_release(
        db,
        job,
        target_chapter_id=chapter.id,
        records=_records(),
        inventory={"items": [], "stats": {"items": 0}},
        mined_types={"types": []},
        checkpoint=_checkpoint("description_method_snapshot", 0.55),
        error=RuntimeError("inventory disagreement after retry"),
        reason=(
            "Generation failed after its final rows were materialized. "
            "Aegis released those captured rows with the failure attached "
            "instead of falling back to an older or empty checkpoint."
        ),
    )
    assert terminal.ensure_explicit_terminal_verdict(
        db, job, lane=release.LANE_POST,
    ) is False
    payload = release.release_payload(job)
    assert payload is not None
    assert payload[terminal.TERMINAL_GENERATION_FIELD] is False


def test_legacy_terminal_payload_is_inferred_without_breaking_old_releases():
    clean = {
        "checkpoint_stage": "final_content_ready",
        "issues": [],
    }
    old_without_checkpoint = {
        "checkpoint_stage": "",
        "issues": [],
    }
    failed = {
        "checkpoint_stage": "final_content_ready",
        "issues": [{
            "phase": "generation",
            "severity": "error",
            "message": "failed after final rows",
        }],
    }
    partial = {
        "checkpoint_stage": "description_method_snapshot",
        "issues": [],
    }

    assert terminal.payload_terminal_generation_complete(clean) is True
    assert terminal.payload_terminal_generation_complete(
        old_without_checkpoint
    ) is True
    assert terminal.payload_terminal_generation_complete(failed) is False
    assert terminal.payload_terminal_generation_complete(partial) is False

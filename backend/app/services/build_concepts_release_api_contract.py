"""Patch only the user-facing Build Concepts upload routes.

The low-level generation service keeps its historical contracts for recovery
utilities and internal callers. API upload generation alone stages an
unattended release. Legacy paused jobs may still consume their already-saved
manual answer through the compatibility endpoint, while a released job cannot
be sent back into a selection flow.
"""
from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, File, HTTPException, Query, UploadFile
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from .. import schemas
from ..db import SessionLocal, get_db
from . import (
    auth,
    drive_checkpoints,
    generation_recovery,
    openai_usage,
    progress,
    uploads,
)
from . import build_concepts as svc
from . import build_concepts_release as release_svc
from . import build_concepts_release_contract as release_contract
from . import release_workbook_edits
from .concept_question_review import QuestionReviewRequestError
from ..api.upload_limits import read_limited_upload


_CONTRACT_VERSION = 3


def _replace_route(router, path: str, method: str, endpoint) -> None:
    for route in router.routes:
        if not isinstance(route, APIRoute):
            continue
        if route.path != path or method.upper() not in route.methods:
            continue
        route.endpoint = endpoint
        route.dependant.call = endpoint
        return
    raise RuntimeError(f"Build Concepts route not found: {method} {path}")


def _decision_endpoint(
    job_id: int,
    decision_id: str,
    req: schemas.HumanSemanticDecisionRequest,
    db: Session = Depends(get_db),
    user: auth.Principal = Depends(auth.require_user),
):
    try:
        job = uploads.get_job(
            db,
            job_id,
            owner_sub=user.sub,
            module="build_concepts",
        )
        generation_recovery.require_mutation_allowed(
            job, operation="resolve this run's semantic decision"
        )
        if release_svc.release_available(job) or job.status == release_svc.RELEASE_STATUS:
            raise HTTPException(
                409,
                "This Build Concepts job has already released its unattended "
                "output. Download the release or publish it explicitly; it "
                "cannot return to a manual semantic selection.",
            )
        return svc.record_human_semantic_decision(
            db,
            job_id,
            decision_id,
            choice=req.choice,
            instruction=req.instruction,
            target_id=req.target_id,
            target_concept_id=req.target_concept_id,
            owner_sub=user.sub,
        )
    except HTTPException:
        raise
    except uploads.UploadJobNotFound as exc:
        raise HTTPException(404, str(exc))
    except svc.HumanDecisionConflictError as exc:
        raise HTTPException(409, str(exc))
    except generation_recovery.NonResumableRunError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc))


def _post_generate_endpoint(
    job_id: int,
    req: schemas.PostLearningGenerateRequest,
    db: Session = Depends(get_db),
    user: auth.Principal = Depends(auth.require_user),
):
    try:
        job = uploads.get_job(
            db,
            job_id,
            owner_sub=user.sub,
            module="build_concepts",
            learning_kind="post",
        )
    except uploads.UploadJobNotFound as exc:
        raise HTTPException(404, str(exc))
    try:
        generation_recovery.require_mutation_allowed(
            job, operation="generate from this checkpoint"
        )
    except generation_recovery.NonResumableRunError as exc:
        raise HTTPException(409, str(exc)) from exc
    if job.status in {"generated", release_svc.RELEASE_STATUS}:
        raise HTTPException(
            409,
            "this upload already has a released or published output; start a new upload",
        )
    if uploads.is_job_running(job_id):
        raise HTTPException(
            409,
            "generation is already running for this upload; wait for the active run to finish",
        )

    def work():
        worker_db = SessionLocal()
        try:
            return uploads.run_with_openai_usage(
                worker_db,
                job_id,
                lambda: release_contract.generate_post_learning(
                    worker_db,
                    job_id,
                    req.target_chapter_id,
                    owner_sub=user.sub,
                    pause_for_concept_review=True,
                ),
                owner_sub=user.sub,
            )
        finally:
            drive_checkpoints.schedule_checkpoint_backup(job_id)
            worker_db.close()

    return progress.stream(
        work,
        title="Build Concepts — post-learning generation",
        journal_job_id=job_id,
        # A retry/restart of an existing job is the same durable run. Keep its
        # journal cursor continuous; the first run still starts a fresh file.
        continue_journal=bool(job.run_id or job.run_state),
    )


async def _concept_review_upload_endpoint(
    job_id: int,
    lane: str = Query(
        release_svc.LANE_POST,
        description="Concept lane to replace: post or pre",
    ),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: auth.Principal = Depends(auth.require_user),
):
    """Apply one corrected Concept workbook as a segment of the same run.

    Mechanical workbook validation and the optional model author/critic pass
    execute in the worker thread. The durable run is resumed for that work,
    then paused again until the reviewer explicitly starts Master authoring.
    """
    try:
        resolved = release_svc.normalize_lane(lane)
        job = uploads.get_job(
            db, job_id, owner_sub=user.sub, module="build_concepts"
        )
        state = release_svc.concept_review_state(job)
        if not state:
            raise HTTPException(
                409,
                "this upload is not waiting for Concept review; generate the "
                "Concept files first",
            )
        if state.get("status") in {
            release_svc.CONCEPT_REVIEW_MASTER_BUILDING,
            release_svc.CONCEPT_REVIEW_MASTER_READY,
        }:
            raise HTTPException(
                409,
                "Master authoring has started or completed for this upload; "
                "start a new run to submit different Concept inputs",
            )
        available = set(
            state.get("available_lanes") or state.get("required_lanes") or []
        )
        if resolved not in available:
            raise HTTPException(404, f"no staged {resolved} Concept file exists")
        if uploads.is_job_running(job_id):
            raise HTTPException(
                409,
                "this upload is busy; wait for the active run before uploading "
                "a corrected Concept workbook",
            )
        raw_bytes = await read_limited_upload(
            file, description="corrected Concept workbook"
        )
    except uploads.UploadJobNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as handle:
            handle.write(raw_bytes)
            temp_path = Path(handle.name)
        def apply_review_round():
            worker_db = SessionLocal()
            try:
                # This route is not wrapped by progress.stream because its API
                # contract returns one JSON response. Start the same usage
                # accumulator explicitly so every author/critic request,
                # including a provider failure, joins the upload's durable
                # baseline and receives the persisted INR/cost receipt.
                with progress.capture_history(), openai_usage.track():
                    def apply_and_pause():
                        uploads.update_run_stage(
                            worker_db,
                            job_id,
                            "Applying reviewed Concept workbook",
                            progress_value=0.70,
                            owner_sub=user.sub,
                        )
                        progress.step(
                            "Applying reviewed Concept workbook",
                            value=0.70,
                        )
                        worker_job = uploads.get_job(
                            worker_db,
                            job_id,
                            owner_sub=user.sub,
                            module="build_concepts",
                        )
                        result = release_workbook_edits.apply_workbook_for_review(
                            worker_db,
                            worker_job,
                            lane=resolved,
                            workbook_path=temp_path,
                            owner_sub=user.sub,
                        )
                        worker_job = uploads.get_job(
                            worker_db,
                            job_id,
                            owner_sub=user.sub,
                            module="build_concepts",
                        )
                        workflow_state = release_svc.update_concept_review_state(
                            worker_db,
                            worker_job,
                            reviewed_lane=resolved,
                            corrected_at=datetime.now(timezone.utc).isoformat(),
                            corrected_filename=file.filename,
                            corrected_changed=bool(result.get("round_recorded")),
                        )
                        # A successful upload has finished this processing
                        # segment. Keep human review time out of active time
                        # while the same run waits for Build Master.
                        run_snapshot = uploads.pause_run_for_review(
                            worker_db,
                            job_id,
                            progress_value=0.70,
                            stage="Concept review complete; awaiting Master authoring",
                            owner_sub=user.sub,
                        )
                        progress.step(
                            "Concept review complete; awaiting Master authoring",
                            value=0.70,
                        )
                        result["concept_review"] = workflow_state
                        result["run_state"] = run_snapshot
                        result["review_required"] = workflow_state.get("status") != release_svc.CONCEPT_REVIEW_REVIEWED
                        result["job_id"] = int(job_id)
                        drive_checkpoints.schedule_checkpoint_backup(job_id)
                        return result

                    try:
                        return uploads.run_with_openai_usage(
                            worker_db,
                            job_id,
                            apply_and_pause,
                            owner_sub=user.sub,
                        )
                    except uploads.JobAlreadyRunningError:
                        raise
                    except Exception:
                        # ``run_with_openai_usage`` closes a failed active
                        # segment so its provider receipt and diagnostic are
                        # durable. A review upload remains retryable while
                        # the Concept gate is still open: reopen only the
                        # review waiting interval, then refresh its joined
                        # timing summary before surfacing the original error.
                        try:
                            failed_job = uploads.get_job(
                                worker_db,
                                job_id,
                                owner_sub=user.sub,
                                module="build_concepts",
                            )
                            workflow_state = release_svc.concept_review_state(
                                failed_job
                            )
                            if workflow_state and workflow_state.get("status") not in {
                                release_svc.CONCEPT_REVIEW_MASTER_READY,
                            }:
                                uploads.pause_run_for_review(
                                    worker_db,
                                    job_id,
                                    progress_value=0.70,
                                    stage="Concept review upload failed; awaiting retry",
                                    owner_sub=user.sub,
                                )
                                uploads.persist_current_openai_usage(
                                    worker_db,
                                    job_id,
                                    owner_sub=user.sub,
                                )
                        except Exception:  # pragma: no cover - preserve original
                            worker_db.rollback()
                        raise
            finally:
                worker_db.close()

        # Workbook parsing and validation can scan a large Concept file. Keep
        # it off FastAPI's event loop while preserving the process-local job
        # lock and the same transactional review round.
        return await run_in_threadpool(apply_review_round)
    except uploads.JobAlreadyRunningError as exc:
        raise HTTPException(409, str(exc)) from exc
    except release_workbook_edits.WorkbookEditError as exc:
        raise HTTPException(422, str(exc)) from exc
    except QuestionReviewRequestError as exc:
        raise HTTPException(
            502,
            "The question-review service could not complete the Post-Learning "
            "upload. This corrected Post file has not been applied. Previously "
            "accepted Concept files are preserved. The detailed failure is "
            "saved in this job's run log and diagnostic snapshot.",
        ) from exc
    except generation_recovery.NonResumableRunError as exc:
        raise HTTPException(409, str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        # Do not expose an unhelpful bare 500 or raw provider/SQL details.
        # A response can fail after the revision commit, so let the frontend
        # refresh the durable state instead of falsely declaring it unsaved.
        raise HTTPException(
            500,
            "Aegis could not finish the corrected-file upload response. "
            "Refresh this job to check which files were accepted before "
            "retrying. Check the run log and diagnostic snapshot for "
            "processing details.",
        ) from exc
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def _concept_review_master_endpoint(
    job_id: int,
    db: Session = Depends(get_db),
    user: auth.Principal = Depends(auth.require_user),
):
    """Resume the paused run and build both Masters from reviewed slots."""
    try:
        job = uploads.get_job(
            db, job_id, owner_sub=user.sub, module="build_concepts"
        )
        state = release_svc.concept_review_state(job)
        if not state:
            raise HTTPException(409, "this upload has no Concept review gate")
        if state.get("status") == release_svc.CONCEPT_REVIEW_MASTER_READY:
            return {
                "job_id": int(job_id),
                "concept_review": state,
                "review_required": False,
                "all_four_outputs_ready": True,
            }
        if uploads.is_job_running(job_id):
            raise HTTPException(
                409,
                "Master authoring is already running for this upload; wait "
                "for the active run to finish",
            )
    except uploads.UploadJobNotFound as exc:
        raise HTTPException(404, str(exc)) from exc

    def work():
        worker_db = SessionLocal()
        try:
            return uploads.run_with_openai_usage(
                worker_db,
                job_id,
                lambda: release_contract.build_review_masters(
                    worker_db, job_id, owner_sub=user.sub
                ),
                owner_sub=user.sub,
            )
        finally:
            drive_checkpoints.schedule_checkpoint_backup(job_id)
            worker_db.close()

    return progress.stream(
        work,
        title="Build Concepts — Master authoring from reviewed Concepts",
        journal_job_id=job_id,
        # Concept and Master stages share one run journal across the review
        # pause, so catch-up can replay the complete chronological history.
        continue_journal=True,
    )


def install(router) -> None:
    if getattr(router, "_RELEASE_API_CONTRACT_VERSION", 0) >= _CONTRACT_VERSION:
        return
    _replace_route(
        router,
        "/build-concepts/uploads/{job_id}/decisions/{decision_id}",
        "POST",
        _decision_endpoint,
    )
    _replace_route(
        router,
        "/build-concepts/post-learning/uploads/{job_id}/generate",
        "POST",
        _post_generate_endpoint,
    )
    router.add_api_route(
        "/uploads/{job_id}/concept-review/submit",
        _concept_review_upload_endpoint,
        methods=["POST"],
    )
    router.add_api_route(
        "/uploads/{job_id}/concept-review/master",
        _concept_review_master_endpoint,
        methods=["POST"],
    )
    router._RELEASE_API_CONTRACT_VERSION = _CONTRACT_VERSION

"""Patch only the user-facing Build Concepts upload routes.

The low-level generation service keeps its historical contracts for recovery
utilities and internal callers. API upload generation alone stages an
unattended release. Legacy paused jobs may still consume their already-saved
manual answer through the compatibility endpoint, while a released job cannot
be sent back into a selection flow.
"""
from __future__ import annotations

from fastapi import Depends, HTTPException
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session

from .. import schemas
from ..db import SessionLocal, get_db
from . import auth, drive_checkpoints, generation_recovery, progress, uploads
from . import build_concepts as svc
from . import build_concepts_release as release_svc
from . import build_concepts_release_contract as release_contract


_CONTRACT_VERSION = 2


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
    router._RELEASE_API_CONTRACT_VERSION = _CONTRACT_VERSION

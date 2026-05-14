from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from .. import models, schemas
from ..db import get_db
from ..services import concepts as concept_svc
from ..services import pipeline as pipeline_svc
from ..services import questions as question_svc

router = APIRouter(prefix="/pipeline", tags=["pipeline"])


@router.get("/stages", response_model=list[schemas.StageDescriptor])
def stages():
    return pipeline_svc.list_stages()


@router.post("/stages/{key}/run", response_model=schemas.PipelineRunOut)
def run_stage(key: str, request: schemas.StageRunRequest, db: Session = Depends(get_db)):
    try:
        run = pipeline_svc.run_stage(db, key, request)
    except KeyError as e:
        raise HTTPException(404, str(e))

    # Auto-load artifacts for downstream visibility
    try:
        outputs = run.outputs or {}
        if run.status == "succeeded":
            if path := outputs.get("concepts_xlsx"):
                from pathlib import Path
                concept_svc.import_excel(db, Path(path), is_pre_learning=False)
            if path := outputs.get("pre_learning_xlsx"):
                from pathlib import Path
                concept_svc.import_excel(db, Path(path), is_pre_learning=True)
            if path := outputs.get("bulk_upload_xlsx"):
                from pathlib import Path
                question_svc.import_excel(db, Path(path))
    except Exception as exc:  # noqa: BLE001
        run.detail = f"{run.detail} | post-import warning: {exc}"
        db.commit()
        db.refresh(run)
    return run


@router.get("/runs", response_model=list[schemas.PipelineRunOut])
def list_runs(stage: str | None = None, limit: int = 50, db: Session = Depends(get_db)):
    return pipeline_svc.list_runs(db, stage=stage, limit=limit)


@router.get("/runs/{run_id}", response_model=schemas.PipelineRunOut)
def get_run(run_id: int, db: Session = Depends(get_db)):
    run = db.get(models.PipelineRun, run_id)
    if not run:
        raise HTTPException(404)
    return run


@router.get("/runs/{run_id}/artifact/{name}")
def download_artifact(run_id: int, name: str, db: Session = Depends(get_db)):
    run = db.get(models.PipelineRun, run_id)
    if not run:
        raise HTTPException(404)
    path = pipeline_svc.get_artifact_path(run, name)
    if not path:
        raise HTTPException(404, f"Artifact {name} not found for run {run_id}")
    return FileResponse(path, filename=name)

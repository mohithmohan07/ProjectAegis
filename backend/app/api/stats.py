from collections import Counter

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import models, schemas
from ..db import get_db

router = APIRouter(prefix="/stats", tags=["stats"])


@router.get("", response_model=schemas.StatsOut)
def stats(db: Session = Depends(get_db)):
    questions = db.query(models.Question).all()
    runs = db.query(models.PipelineRun).all()
    return schemas.StatsOut(
        concepts=db.query(models.Concept).filter(models.Concept.is_pre_learning == 0).count(),
        pre_learning_concepts=db.query(models.Concept).filter(models.Concept.is_pre_learning == 1).count(),
        questions=len(questions),
        questions_by_sheet=dict(Counter(q.sheet_kind for q in questions)),
        questions_by_difficulty=dict(Counter(q.level_of_difficulty for q in questions)),
        runs=len(runs),
        runs_by_status=dict(Counter(r.status for r in runs)),
    )

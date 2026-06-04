"""Tagging (many-to-many) + import preview endpoints."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import schemas
from ..db import get_db
from ..services import tagging as svc

router = APIRouter(prefix="/tagging", tags=["tagging"])


@router.post("/questions/{question_id}/tag-to-concept")
def tag_question_to_concept(
    question_id: int, req: schemas.TagToConceptRequest, db: Session = Depends(get_db)
):
    try:
        return svc.tag_question_to_concept(db, question_id, req.concept_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/questions/{question_id}/tag-to-group")
def tag_question_to_group(
    question_id: int, req: schemas.TagToGroupRequest, db: Session = Depends(get_db)
):
    try:
        return svc.tag_question_to_group(db, question_id, req.group_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/concepts/{concept_id}/tag-to-topic")
def tag_concept_to_topic(
    concept_id: int, req: schemas.TagToTopicRequest, db: Session = Depends(get_db)
):
    try:
        return svc.tag_concept_to_topic(db, concept_id, req.topic_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/preview")
def preview(req: schemas.PreviewRequest, db: Session = Depends(get_db)):
    """Predict the CMS outcome (ADD / TAG / SKIP) per row for a prospective export."""
    return svc.preview(
        db, question_ids=req.question_ids or None, concept_ids=req.concept_ids or None
    )

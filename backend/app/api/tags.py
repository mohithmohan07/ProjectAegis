from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas
from ..db import get_db
from ..services import tagging

router = APIRouter(prefix="/tags", tags=["tags"])


@router.post("/suggest", response_model=schemas.TagSuggestion)
def suggest(req: schemas.TagRequest, db: Session = Depends(get_db)):
    return tagging.suggest(db, req.text)


@router.post("/apply/{question_id}", response_model=schemas.QuestionOut)
def apply_to_question(question_id: int, db: Session = Depends(get_db)):
    q = db.get(models.Question, question_id)
    if not q:
        raise HTTPException(404)
    suggestion = tagging.suggest(db, q.question)
    q.concept_id = suggestion.concept_id
    if suggestion.cognitive_skills and not q.cognitive_skills:
        q.cognitive_skills = suggestion.cognitive_skills
    if suggestion.level_of_difficulty and not q.level_of_difficulty:
        q.level_of_difficulty = suggestion.level_of_difficulty
    if suggestion.concept_id:
        concept = db.get(models.Concept, suggestion.concept_id)
        if concept:
            q.assessment_label = (concept.concept_id or concept.chapter_code or "")
    q.tagging_notes = (
        f"auto-tag confidence={suggestion.confidence}; path={suggestion.concept_path}"
    )
    db.commit()
    db.refresh(q)
    return q

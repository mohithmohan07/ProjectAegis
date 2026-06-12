from collections import Counter

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import bulk_import as bi
from .. import models, schemas
from ..config import has_mathpix, has_openai
from ..db import get_db
from ..services import directory, mmd

router = APIRouter(prefix="/directory", tags=["directory"])


@router.get("/tree")
def get_tree(db: Session = Depends(get_db)):
    """Board > Grade > Subject > Unit > Chapter hierarchy."""
    return directory.tree(db)


@router.get("/chapters/{chapter_id}")
def get_chapter(chapter_id: int, db: Session = Depends(get_db)):
    detail = directory.chapter_detail(db, chapter_id)
    if not detail:
        raise HTTPException(404, "chapter not found")
    return detail


@router.get("/concepts/{concept_id}", response_model=schemas.ConceptOut)
def get_concept(concept_id: int, db: Session = Depends(get_db)):
    concept = db.get(models.Concept, concept_id)
    if not concept:
        raise HTTPException(404, "concept not found")
    return concept


@router.get("/vocab", response_model=schemas.Vocab)
def get_vocab():
    """Controlled vocabularies for Blueprint settings and upload flows."""
    return schemas.Vocab(
        boards=bi.BOARDS,
        grades=bi.GRADES,
        question_types=bi.QUESTION_TYPES,
        cognitive_skills=bi.COGNITIVE_SKILLS,
        difficulty_levels=bi.DIFFICULTY_LEVELS,
        question_categories=bi.QUESTION_CATEGORIES,
        group_types=bi.GROUP_TYPES,
        upload_types=mmd.UPLOAD_TYPES,
        book_sources=bi.BOOK_SOURCES,
    )


@router.get("/stats", response_model=schemas.Stats)
def get_stats(db: Session = Depends(get_db)):
    questions = db.query(models.Question).all()
    return schemas.Stats(
        chapters=db.query(models.Chapter).count(),
        topics=db.query(models.Topic).count(),
        concepts=db.query(models.Concept).count(),
        groups=db.query(models.Group).count(),
        questions=len(questions),
        questions_by_sheet=dict(Counter(q.sheet_kind for q in questions)),
        sessions=db.query(models.AssessmentSession).count(),
        upload_jobs=db.query(models.UploadJob).count(),
        openai_live=has_openai(),
        mathpix_live=has_mathpix(),
    )

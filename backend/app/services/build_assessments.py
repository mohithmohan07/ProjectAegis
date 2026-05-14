"""Module 1: Build Assessments.

Two paths, exactly as specified:

  (a) From Concept Mapping — drill the directory to a chapter/topic/concept
      scope, stack one or more Blueprint batches (cognitive skill x difficulty x
      category x question type x count), then Generate. Question content always
      comes from the concept level, so chapter/topic scopes fan out to concepts.

  (b) From Upload — upload a PDF/text/image, convert to MMD, pick an upload type
      (and, for textbooks, extract-vs-create), choose where to deposit in the
      directory, then identify questions and fill the Bulk Import columns.

Both paths finish by running the post-generation pipeline (tagging -> column
mapping -> append-only write).
"""
from __future__ import annotations

from itertools import product
from pathlib import Path

from sqlalchemy.orm import Session

from .. import config, models
from . import directory, generation, mmd, post_generation

# A blueprint's difficulty selects which concept group a question lands in.
DIFFICULTY_TO_GROUP = {"Less": "Basic", "Moderate": "Intermediate", "High": "Advanced"}


# --------------------------------------------------------------------------- #
# Path A — From Concept Mapping
# --------------------------------------------------------------------------- #

def create_session(db: Session, scope_type: str, scope_ids: list[int]) -> models.AssessmentSession:
    if scope_type not in {"chapter", "topic", "concept"}:
        raise ValueError("scope_type must be chapter | topic | concept")
    if not directory.resolve_scope_concepts(db, scope_type, scope_ids):
        raise ValueError("scope selection resolves to no concepts")
    session = models.AssessmentSession(
        source="concept_mapping", scope_type=scope_type, scope_ids=scope_ids,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def add_batch(
    db: Session, session_id: int, *,
    cognitive_skills: list[str], difficulty_levels: list[str],
    categories: list[str], question_type: str, num_questions: int,
) -> models.BlueprintBatch:
    session = db.get(models.AssessmentSession, session_id)
    if not session:
        raise ValueError("session not found")
    if question_type not in {"objective", "subjective", "descriptive"}:
        raise ValueError("question_type must be objective | subjective | descriptive")
    batch = models.BlueprintBatch(
        session_id=session_id,
        cognitive_skills=cognitive_skills or ["Understanding"],
        difficulty_levels=difficulty_levels or ["Moderate"],
        categories=categories or ["Multiple Choice Question"],
        question_type=question_type,
        num_questions=max(int(num_questions), 1),
    )
    db.add(batch)
    db.commit()
    db.refresh(batch)
    return batch


def _group_for(concept: models.Concept, difficulty: str) -> models.Group:
    """Pick (or lazily create) the concept group a question of this difficulty lands in."""
    g_type = DIFFICULTY_TO_GROUP.get(difficulty, "Intermediate")
    for g in concept.groups:
        if g.group_type == g_type:
            return g
    group = models.Group(
        concept_id=concept.id, group_type=g_type,
        group_name=f"{concept.concept_title} — {g_type}",
        group_display_name=f"{concept.concept_title} — {g_type}",
        group_status="Active",
    )
    concept.groups.append(group)
    return group


def generate(db: Session, session_id: int) -> dict:
    """Generate questions for every concept x batch x (skill,difficulty,category) cell."""
    session = db.get(models.AssessmentSession, session_id)
    if not session:
        raise ValueError("session not found")
    if not session.batches:
        raise ValueError("add at least one blueprint batch before generating")

    concepts = directory.resolve_scope_concepts(db, session.scope_type, session.scope_ids)
    created_ids: list[int] = []
    # Per-concept running index keeps question labels unique & ordered.
    counters: dict[int, int] = {c.id: 1 for c in concepts}

    for concept in concepts:
        for batch in session.batches:
            for skill, difficulty, category in product(
                batch.cognitive_skills, batch.difficulty_levels, batch.categories
            ):
                records = generation.generate_questions_for_concept(
                    concept,
                    question_type=batch.question_type,
                    cognitive_skill=skill, difficulty=difficulty, category=category,
                    count=batch.num_questions, start_index=counters[concept.id],
                )
                counters[concept.id] += len(records)
                group = _group_for(concept, difficulty)
                for rec in records:
                    q = models.Question(group_id=group.id, **_question_kwargs(rec))
                    db.add(q)
                    db.flush()
                    created_ids.append(q.id)
    db.commit()

    pipeline = post_generation.run(db, created_ids)
    session.status = "generated"
    session.generated_question_ids = created_ids
    db.commit()
    return {"session_id": session_id, "created": len(created_ids), "pipeline": pipeline}


def _question_kwargs(rec: dict) -> dict:
    return {
        "sheet_kind": rec["sheet_kind"],
        "question_label": rec.get("question_label", ""),
        "question_category": rec.get("question_category", ""),
        "cognitive_skills": rec.get("cognitive_skills", ""),
        "question_source": rec.get("question_source", ""),
        "level_of_difficulty": rec.get("level_of_difficulty", ""),
        "math_keyboard": rec.get("math_keyboard", ""),
        "question": rec.get("question", ""),
        "marks": rec.get("marks", 1.0),
        "display_answer": rec.get("display_answer", ""),
        "answer_explanation": rec.get("answer_explanation", ""),
        "answers": rec.get("answers", []),
        "sub_questions": rec.get("sub_questions", []),
        "origin": rec.get("origin", "concept_mapping"),
    }


# --------------------------------------------------------------------------- #
# Path B — From Upload
# --------------------------------------------------------------------------- #

def create_upload_job(
    db: Session, *, upload_type: str, filename: str, raw_bytes: bytes,
) -> models.UploadJob:
    if upload_type not in mmd.UPLOAD_TYPES:
        raise ValueError(f"upload_type must be one of {mmd.UPLOAD_TYPES}")
    dest = config.UPLOAD_DIR / filename
    dest.write_bytes(raw_bytes)
    mmd_text = mmd.to_mmd(dest)
    job = models.UploadJob(
        module="build_assessments", upload_type=upload_type,
        filename=filename, mmd_text=mmd_text, status="converted",
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def set_textbook_mode(db: Session, job_id: int, mode: str) -> models.UploadJob:
    """For upload_type='textbook': extract existing Q&A, or create new questions."""
    job = db.get(models.UploadJob, job_id)
    if not job:
        raise ValueError("upload job not found")
    if mode not in {"extract", "create"}:
        raise ValueError("textbook mode must be extract | create")
    job.textbook_mode = mode
    db.commit()
    db.refresh(job)
    return job


def set_deposit(db: Session, job_id: int, scope_type: str, scope_ids: list[int]) -> models.UploadJob:
    """Choose where uploaded questions are deposited (chapter / topics / concepts)."""
    job = db.get(models.UploadJob, job_id)
    if not job:
        raise ValueError("upload job not found")
    if scope_type not in {"chapter", "topic", "concept"}:
        raise ValueError("scope_type must be chapter | topic | concept")
    if not directory.resolve_scope_concepts(db, scope_type, scope_ids):
        raise ValueError("deposit selection resolves to no concepts")
    job.deposit_scope_type = scope_type
    job.deposit_scope_ids = scope_ids
    job.status = "deposited"
    db.commit()
    db.refresh(job)
    return job


def generate_from_upload(db: Session, job_id: int, question_type: str = "objective") -> dict:
    """Identify questions from the uploaded MMD and deposit them in the chosen scope."""
    job = db.get(models.UploadJob, job_id)
    if not job:
        raise ValueError("upload job not found")
    if job.status != "deposited":
        raise ValueError("set a deposit scope before generating")

    concepts = directory.resolve_scope_concepts(db, job.deposit_scope_type, job.deposit_scope_ids)
    records = generation.identify_questions_from_mmd(
        job.mmd_text, upload_type=job.upload_type, question_type=question_type,
    )
    created_ids: list[int] = []
    counters: dict[int, int] = {c.id: 1 for c in concepts}
    # Round-robin the identified questions across the deposit concepts.
    for i, rec in enumerate(records):
        concept = concepts[i % len(concepts)]
        rec.setdefault("question_label", generation.question_label(concept, counters[concept.id]))
        counters[concept.id] += 1
        group = _group_for(concept, rec.get("level_of_difficulty", "Moderate"))
        q = models.Question(group_id=group.id, **_question_kwargs(rec))
        db.add(q)
        db.flush()
        created_ids.append(q.id)
    db.commit()

    pipeline = post_generation.run(db, created_ids)
    job.status = "generated"
    job.result_ids = created_ids
    job.detail = f"identified {len(records)} questions from {job.upload_type} upload"
    db.commit()
    return {"job_id": job_id, "created": len(created_ids), "pipeline": pipeline}

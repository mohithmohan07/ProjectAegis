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

import threading
from contextlib import contextmanager
from itertools import product
from pathlib import Path

from sqlalchemy.orm import Session

from .. import bulk_import as bi
from .. import config, models
from ..bulk_import import workbook_sync
from . import auth, directory, generation, mmd, post_generation, progress, uploads

# A blueprint's difficulty selects which concept group a question lands in.
DIFFICULTY_TO_GROUP = {"Less": "Basic", "Moderate": "Intermediate", "High": "Advanced"}


# --------------------------------------------------------------------------- #
# Path A — From Concept Mapping
# --------------------------------------------------------------------------- #

class AssessmentSessionNotFound(ValueError):
    """Raised when a session is absent or belongs to another principal."""


class AssessmentSessionAlreadyRunning(RuntimeError):
    """Raised when a second mutation targets an actively generating session."""


_session_locks: dict[int, threading.Lock] = {}
_session_locks_guard = threading.Lock()


def is_session_running(session_id: int | None) -> bool:
    if not session_id:
        return False
    with _session_locks_guard:
        lock = _session_locks.get(int(session_id))
        return bool(lock and lock.locked())


@contextmanager
def _exclusive_session_generation(session_id: int):
    with _session_locks_guard:
        lock = _session_locks.setdefault(int(session_id), threading.Lock())
    if not lock.acquire(blocking=False):
        raise AssessmentSessionAlreadyRunning(
            "generation is already running for this assessment session"
        )
    try:
        yield
    finally:
        lock.release()


def get_session(
    db: Session,
    session_id: int,
    *,
    owner_sub: str = auth.LOCAL_OWNER_SUB,
) -> models.AssessmentSession:
    session = db.query(models.AssessmentSession).filter(
        models.AssessmentSession.id == session_id,
        models.AssessmentSession.owner_sub == owner_sub,
        models.AssessmentSession.source == "concept_mapping",
    ).one_or_none()
    if session is None:
        # Missing and foreign rows intentionally look identical so session IDs
        # cannot be used to discover another user's private work.
        raise AssessmentSessionNotFound("session not found")
    return session


def create_session(
    db: Session,
    scope_type: str,
    scope_ids: list[int],
    *,
    owner_sub: str = auth.LOCAL_OWNER_SUB,
) -> models.AssessmentSession:
    if scope_type not in {"chapter", "topic", "concept"}:
        raise ValueError("scope_type must be chapter | topic | concept")
    if not directory.resolve_scope_concepts(db, scope_type, scope_ids):
        raise ValueError("scope selection resolves to no concepts")
    session = models.AssessmentSession(
        owner_sub=owner_sub,
        source="concept_mapping",
        scope_type=scope_type,
        scope_ids=scope_ids,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def add_batch(
    db: Session, session_id: int, *,
    cognitive_skills: list[str], difficulty_levels: list[str],
    categories: list[str], question_type: str, num_questions: int,
    appears_in: list[str] | None = None,
    owner_sub: str = auth.LOCAL_OWNER_SUB,
) -> models.BlueprintBatch:
    session = get_session(db, session_id, owner_sub=owner_sub)
    with _exclusive_session_generation(session.id):
        db.refresh(session)
        if session.status == "generated":
            raise ValueError(
                "this assessment session has already been generated; "
                "create a new session"
            )
        if question_type not in {"objective", "subjective", "descriptive"}:
            raise ValueError(
                "question_type must be objective | subjective | descriptive")
        purposes = [p for p in (appears_in or []) if p in bi.APPEARS_IN]
        batch = models.BlueprintBatch(
            session_id=session_id,
            # Old gerund forms normalize to standard action-verb values.
            cognitive_skills=[
                bi.normalize_cognitive_skills(s) for s in cognitive_skills
            ] or ["Understand"],
            difficulty_levels=[
                bi.normalize_difficulty(d) for d in difficulty_levels
            ] or ["Moderate"],
            categories=categories or ["Multiple Choice Question"],
            question_type=question_type,
            num_questions=max(int(num_questions), 1),
            appears_in=purposes,
        )
        db.add(batch)
        db.commit()
        db.refresh(batch)
        return batch


def _group_for(db: Session, concept: models.Concept, difficulty: str) -> models.Group:
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
    db.add(group)
    db.flush()
    concept.groups.append(group)
    return group


def generate(
    db: Session,
    session_id: int,
    *,
    owner_sub: str = auth.LOCAL_OWNER_SUB,
) -> dict:
    """Generate questions for every concept x batch x (skill,difficulty,category) cell."""
    session = get_session(db, session_id, owner_sub=owner_sub)
    with _exclusive_session_generation(session.id):
        db.refresh(session)
        if session.status == "generated":
            raise ValueError(
                "this assessment session has already been generated; "
                "create a new session"
            )
        return _generate_session(db, session)


def _generate_session(
    db: Session,
    session: models.AssessmentSession,
) -> dict:
    session_id = session.id
    owner_sub = session.owner_sub
    if not session.batches:
        raise ValueError("add at least one blueprint batch before generating")

    concepts = directory.resolve_scope_concepts(db, session.scope_type, session.scope_ids)
    # Generation can call an external model and therefore stays outside the
    # process-wide workbook lock. These indices are prompt seeds only; final
    # labels are reserved from freshly loaded database state under the shared
    # finalization lock below.
    prompt_indices: dict[int, int] = {c.id: 1 for c in concepts}
    generated_batches: list[tuple[int, str, list[dict]]] = []

    total_cells = sum(
        len(batch.cognitive_skills) * len(batch.difficulty_levels) * len(batch.categories)
        for batch in session.batches
    ) * max(len(concepts), 1)
    progress.log(
        f"Generating questions for {len(concepts)} concept(s) × "
        f"{len(session.batches)} batch(es) = {total_cells} blueprint cell(s).")
    done = 0
    for concept in concepts:
        for batch in session.batches:
            for skill, difficulty, category in product(
                batch.cognitive_skills, batch.difficulty_levels, batch.categories
            ):
                progress.step(
                    f"{concept.concept_title}: {difficulty}/{skill}/{category}",
                    value=done / max(total_cells, 1))
                records = generation.generate_questions_for_concept(
                    concept,
                    question_type=batch.question_type,
                    cognitive_skill=skill, difficulty=difficulty, category=category,
                    count=batch.num_questions,
                    start_index=prompt_indices[concept.id],
                    appears_in=", ".join(batch.appears_in or []),
                )
                prompt_indices[concept.id] += len(records)
                generated_batches.append((concept.id, difficulty, records))
                done += 1

    progress.step("Tagging & column mapping", value=0.9)
    # Refresh counter state and reserve final labels while holding the same
    # re-entrant lock used by the workbook writer. This keeps concurrent
    # sessions from allocating the same labels and prevents a database/workbook
    # divergence where the second workbook row would otherwise be skipped.
    with workbook_sync.output_workbook_lock():
        # End the read transaction used for prompt generation so this session
        # observes labels committed by an earlier finalizer before allocating.
        db.rollback()
        db.expire_all()
        session = get_session(db, session_id, owner_sub=owner_sub)
        if session.status == "generated":
            raise ValueError(
                "this assessment session has already been generated; "
                "create a new session"
            )
        refreshed_concepts = directory.resolve_scope_concepts(
            db, session.scope_type, session.scope_ids
        )
        concepts_by_id = {concept.id: concept for concept in refreshed_concepts}
        if any(
            concept_id not in concepts_by_id
            for concept_id, _difficulty, _records in generated_batches
        ):
            raise ValueError(
                "scope selection changed while questions were generated"
            )

        # Per-concept running index keeps question labels unique and ordered,
        # continuing after every question committed by earlier sessions.
        counters: dict[int, int] = {
            concept.id:
                sum(len(group.questions) for group in concept.groups) + 1
            for concept in refreshed_concepts
        }
        created_ids: list[int] = []
        for concept_id, difficulty, records in generated_batches:
            concept = concepts_by_id[concept_id]
            group = _group_for(db, concept, difficulty)
            for generated_record in records:
                record = dict(generated_record)
                record["question_label"] = generation.question_label(
                    concept, counters[concept_id]
                )
                counters[concept_id] += 1
                question = models.Question(
                    group_id=group.id, **_question_kwargs(record)
                )
                db.add(question)
                db.flush()
                created_ids.append(question.id)
        db.commit()

        pipeline = post_generation.run(db, created_ids)
        session.status = "generated"
        session.generated_question_ids = created_ids
        db.commit()

    # Quality review summary: deterministic checks + anti-monotony report.
    from . import assessment_prompts as ap
    created = db.query(models.Question).filter(models.Question.id.in_(created_ids)).all()
    problems: list[str] = []
    for q in created:
        for p in ap.review_question({
            "sheet_kind": q.sheet_kind, "question": q.question,
            "question_text": q.question_text, "cognitive_skills": q.cognitive_skills,
            "level_of_difficulty": q.level_of_difficulty, "marks": q.marks,
            "answers": q.answers,
        }):
            problems.append(f"{q.question_label}: {p}")
    monotony = ap.stem_monotony_report([q.question for q in created])
    progress.set_progress(1.0, label="Done")
    progress.log(f"Created {len(created_ids)} questions.", level="success")
    return {
        "session_id": session_id, "created": len(created_ids),
        "question_ids": created_ids,
        "pipeline": pipeline,
        "review": {"problems": problems[:50],
                   "monotony": {k: monotony[k] for k in
                                ("worst", "worst_count", "generic_ratio", "monotonous")}},
    }


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
        "question_text": rec.get("question_text", ""),
        "question_appears_in": rec.get("question_appears_in", ""),
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
    source_book: str = "",
    owner_sub: str | None = None,
) -> models.UploadJob:
    """Stage an uploaded file ONLY. Conversion to MMD is a separate step
    (``uploads.convert_job``) so a mistakenly-chosen file can be replaced first.
    """
    if upload_type not in mmd.UPLOAD_TYPES:
        raise ValueError(f"upload_type must be one of {mmd.UPLOAD_TYPES}")
    job = models.UploadJob(
        owner_sub=uploads.normalize_owner_sub(owner_sub),
        module="build_assessments", upload_type=upload_type,
        filename=Path(filename).name, mmd_text="", status="uploaded",
        source_book=source_book.strip(),
    )
    return uploads.persist_new_job(db, job, raw_bytes)


def set_textbook_mode(
    db: Session, job_id: int, mode: str, *,
    owner_sub: str | None = None,
) -> models.UploadJob:
    """For upload_type='textbook': extract existing Q&A, or create new questions."""
    job = uploads.get_job(
        db, job_id, owner_sub=owner_sub, module="build_assessments")
    if mode not in {"extract", "create"}:
        raise ValueError("textbook mode must be extract | create")
    with uploads.exclusive_job_operation(job.id):
        db.refresh(job)
        if job.upload_type != "textbook":
            raise ValueError("textbook mode is only valid for textbook uploads")
        if job.status not in {"uploaded", "converted", "deposited"}:
            raise ValueError(
                "cannot change textbook mode after generation; "
                "start a new upload"
            )
        job.textbook_mode = mode
        db.commit()
        db.refresh(job)
    return job


def set_deposit(
    db: Session, job_id: int, scope_type: str, scope_ids: list[int], *,
    owner_sub: str | None = None,
) -> models.UploadJob:
    """Choose where uploaded questions are deposited (chapter / topics / concepts)."""
    job = uploads.get_job(
        db, job_id, owner_sub=owner_sub, module="build_assessments")
    with uploads.exclusive_job_operation(job.id):
        db.refresh(job)
        if job.status not in {"converted", "deposited"} or not job.mmd_text:
            if job.status == "generated":
                raise ValueError(
                    "cannot change the deposit scope after generation; "
                    "start a new upload"
                )
            raise ValueError(
                "convert the uploaded document to MMD before setting a "
                "deposit scope"
            )
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


def generate_from_upload(
    db: Session,
    job_id: int,
    question_type: str = "auto",
    *,
    owner_sub: str | None = None,
) -> dict:
    """Identify questions from the uploaded MMD and deposit them in the chosen scope.

    ``question_type`` is ``auto`` (detect & absorb a mix of objective /
    subjective / descriptive) or one specific type to force.
    """
    if question_type not in {"auto", "objective", "subjective", "descriptive"}:
        raise ValueError(
            "question_type must be auto | objective | subjective | descriptive")
    job = uploads.get_job(
        db, job_id, owner_sub=owner_sub, module="build_assessments")
    if not job.mmd_text:
        raise ValueError("convert the uploaded document to MMD before generating")
    if job.status != "deposited":
        raise ValueError("set a deposit scope before generating")

    concepts = directory.resolve_scope_concepts(db, job.deposit_scope_type, job.deposit_scope_ids)
    progress.log(
        f"Generating questions from upload into {len(concepts)} concept(s).")
    records = generation.identify_questions_from_mmd(
        job.mmd_text, upload_type=job.upload_type, question_type=question_type,
        textbook_mode=job.textbook_mode,
    )
    progress.step("Depositing & tagging questions", value=0.85)

    # The duplicate query, database writes, and workbook publication are one
    # serialized transaction boundary.  Without this lock, two uploads can both
    # observe a question as absent and append duplicate rows to the database and
    # shared workbook.
    with workbook_sync.output_workbook_lock():
        db.expire_all()
        job = uploads.get_job(
            db, job_id, owner_sub=owner_sub, module="build_assessments"
        )
        if job.status != "deposited":
            raise ValueError("set a deposit scope before generating")
        concepts = directory.resolve_scope_concepts(
            db, job.deposit_scope_type, job.deposit_scope_ids
        )
        if not concepts:
            raise ValueError("deposit selection resolves to no concepts")

        # Cross-book duplicate check: existing question texts in the deposit
        # chapters. A duplicate is not re-added; its sources are merged instead.
        chapter_ids = {c.topic.chapter_id for c in concepts}
        existing_by_text: dict[str, models.Question] = {}
        for qq in (
            db.query(models.Question)
            .join(models.Group).join(models.Concept).join(models.Topic)
            .filter(models.Topic.chapter_id.in_(chapter_ids))
        ):
            norm = bi.normalize_question_text(qq.question)
            if norm:
                existing_by_text.setdefault(norm, qq)

        created_ids: list[int] = []
        merged_ids: list[int] = []
        counters: dict[int, int] = {
            c.id: sum(len(g.questions) for g in c.groups) + 1 for c in concepts
        }
        # Round-robin the identified questions across the deposit concepts.
        for i, rec in enumerate(records):
            if job.source_book:
                rec["question_source"] = job.source_book
            norm = bi.normalize_question_text(rec.get("question", ""))
            dup = existing_by_text.get(norm) if norm else None
            if dup is not None:
                dup.question_source = bi.merge_sources(
                    dup.question_source, rec.get("question_source", "")
                )
                merged_ids.append(dup.id)
                continue
            concept = concepts[i % len(concepts)]
            rec.setdefault(
                "question_label",
                generation.question_label(concept, counters[concept.id]),
            )
            counters[concept.id] += 1
            group = _group_for(
                db, concept, rec.get("level_of_difficulty", "Moderate")
            )
            q = models.Question(group_id=group.id, **_question_kwargs(rec))
            db.add(q)
            db.flush()
            if norm:
                existing_by_text[norm] = q
            created_ids.append(q.id)
        db.commit()

        # Run the pipeline over new questions AND source-merged duplicates so
        # the output workbook's question_source cells refresh in place.
        pipeline = post_generation.run(db, created_ids + merged_ids)
        job.status = "generated"
        job.result_ids = created_ids
        job.detail = (
            f"identified {len(records)} questions from {job.upload_type} upload "
            f"({len(created_ids)} new, {len(merged_ids)} duplicates "
            "source-merged)"
        )
        db.commit()

    progress.set_progress(1.0, label="Done")
    progress.log(
        f"Created {len(created_ids)} new questions "
        f"({len(merged_ids)} duplicates source-merged).", level="success")
    return {
        "job_id": job_id, "created": len(created_ids),
        "duplicates_merged": len(merged_ids),
        "question_ids": created_ids + merged_ids,
        "pipeline": pipeline,
    }

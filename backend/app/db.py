from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import DB_URL


class Base(DeclarativeBase):
    pass


engine = create_engine(DB_URL, connect_args={"check_same_thread": False} if DB_URL.startswith("sqlite") else {})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from . import models  # noqa: F401  ensure models register on Base
    Base.metadata.create_all(bind=engine)
    _ensure_columns()
    _backfill_and_normalize()


def _ensure_columns() -> None:
    """Lightweight additive migration for pre-existing SQLite databases."""
    if not DB_URL.startswith("sqlite"):
        return
    additions = [
        ("concepts", "parent_concept", "VARCHAR(255) DEFAULT ''"),
        ("concepts", "sources", "TEXT DEFAULT ''"),
        ("upload_jobs", "source_book", "VARCHAR(128) DEFAULT ''"),
        ("questions", "question_text", "TEXT DEFAULT ''"),
        ("blueprint_batches", "appears_in", "TEXT DEFAULT '[]'"),
    ]
    with engine.connect() as conn:
        for table, column, ddl in additions:
            cols = [r[1] for r in conn.exec_driver_sql(f"PRAGMA table_info({table})")]
            if cols and column not in cols:
                conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        conn.commit()


def _backfill_and_normalize() -> None:
    """Idempotent data migration: question_text backfill + standard values.

    - question_text: backfilled from the clean (plain-text) question; existing
      non-empty values are never overwritten.
    - cognitive_skills: gerund forms -> action-verb forms (Remembering->Remember).
    - question_appears_in: legacy 'Pre/Post-Worksheet/Test' -> comma list.
    - answers JSON: answer_type 'Words' -> 'Phrases'.
    - concept sources: legacy '; ' separators -> comma-separated.
    """
    from . import models
    from . import bulk_import as bi

    db = SessionLocal()
    try:
        changed = False
        for q in db.query(models.Question).all():
            if not q.question_text and q.question:
                q.question_text = bi.to_plain_text(q.question)
                changed = True
            norm_skills = bi.normalize_cognitive_skills(q.cognitive_skills)
            if norm_skills != (q.cognitive_skills or ""):
                q.cognitive_skills = norm_skills
                changed = True
            norm_app = bi.normalize_appears_in(q.question_appears_in)
            if norm_app != (q.question_appears_in or ""):
                q.question_appears_in = norm_app
                changed = True
            norm_diff = bi.normalize_difficulty(q.level_of_difficulty)
            if norm_diff != (q.level_of_difficulty or ""):
                q.level_of_difficulty = norm_diff
                changed = True
            if q.answers:
                new_answers = []
                dirty = False
                for a in q.answers:
                    a = dict(a)
                    at = bi.normalize_answer_type(a.get("answer_type", ""))
                    if at != a.get("answer_type", ""):
                        a["answer_type"] = at
                        dirty = True
                    new_answers.append(a)
                if dirty:
                    q.answers = new_answers
                    changed = True
        for c in db.query(models.Concept).filter(models.Concept.sources.like("%;%")):
            c.sources = bi.merge_sources(c.sources, "")
            changed = True
        if changed:
            db.commit()
    finally:
        db.close()

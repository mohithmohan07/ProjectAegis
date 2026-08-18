from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import DB_URL


class Base(DeclarativeBase):
    pass


engine = create_engine(DB_URL, connect_args={"check_same_thread": False} if DB_URL.startswith("sqlite") else {})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

if DB_URL.startswith("sqlite"):
    # Multi-user safety: WAL lets readers proceed during a write, and the busy
    # timeout makes a second writer WAIT for the lock instead of failing with
    # "database is locked" when several generation jobs commit concurrently.
    @event.listens_for(engine, "connect")
    def _sqlite_concurrency_pragmas(dbapi_conn, _record):  # noqa: ANN001
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()


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
        ("topics", "source_order", "INTEGER DEFAULT 0"),
        # Persisted identity (spec-step8 T4-1). Minted once by
        # ``services.identity``; the backfill below fills blanks only.
        ("topics", "machine_id", "VARCHAR(255) DEFAULT ''"),
        ("concepts", "machine_id", "VARCHAR(255) DEFAULT ''"),
        ("concepts", "parent_concept", "VARCHAR(255) DEFAULT ''"),
        ("concepts", "sources", "TEXT DEFAULT ''"),
        ("concepts", "source_order", "INTEGER DEFAULT 0"),
        ("assessment_sessions", "owner_sub",
         "VARCHAR(255) DEFAULT 'local:default'"),
        ("upload_jobs", "owner_sub", "VARCHAR(255) DEFAULT 'local:default'"),
        ("upload_jobs", "upload_storage_key", "VARCHAR(512) DEFAULT ''"),
        ("upload_jobs", "source_book", "VARCHAR(128) DEFAULT ''"),
        ("upload_jobs", "question_inventory", "TEXT DEFAULT '{}'"),
        ("upload_jobs", "generation_checkpoint", "TEXT DEFAULT '{}'"),
        ("upload_jobs", "generation_log", "TEXT DEFAULT '[]'"),
        ("upload_jobs", "openai_usage", "TEXT DEFAULT '{}'"),
        ("questions", "question_text", "TEXT DEFAULT ''"),
        ("blueprint_batches", "appears_in", "TEXT DEFAULT '[]'"),
        # MES release identity (spec §5.7); model-level only — the workbook's
        # positional columns are unchanged until the authoritative MES
        # template is inspected (spec §12/§23).
        ("questions", "answer_restriction", "VARCHAR(16) DEFAULT ''"),
        ("questions", "source_qid", "VARCHAR(64) DEFAULT ''"),
        ("questions", "blueprint_cell_id", "VARCHAR(128) DEFAULT ''"),
        ("questions", "source_paper_number", "VARCHAR(128) DEFAULT ''"),
        ("questions", "alternative_set_id", "VARCHAR(128) DEFAULT ''"),
        ("questions", "parent_question_id", "INTEGER DEFAULT NULL"),
        ("questions", "image_manifest", "TEXT DEFAULT '[]'"),
        ("questions", "source_evidence", "TEXT DEFAULT ''"),
        ("questions", "assessment_gist", "TEXT DEFAULT ''"),
        ("questions", "route_audit", "TEXT DEFAULT '{}'"),
        ("questions", "validation_status", "VARCHAR(32) DEFAULT ''"),
        ("questions", "validation_errors", "TEXT DEFAULT '[]'"),
        ("groups", "group_key", "VARCHAR(255) DEFAULT ''"),
        ("groups", "group_sequence", "INTEGER DEFAULT 0"),
    ]
    with engine.connect() as conn:
        for table, column, ddl in additions:
            cols = [r[1] for r in conn.exec_driver_sql(f"PRAGMA table_info({table})")]
            if cols and column not in cols:
                conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        tables = {
            row[0]
            for row in conn.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        if "upload_jobs" in tables:
            conn.exec_driver_sql(
                "UPDATE upload_jobs SET owner_sub = 'local:default' "
                "WHERE owner_sub IS NULL OR TRIM(owner_sub) = ''"
            )
            conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_upload_jobs_owner_sub "
                "ON upload_jobs(owner_sub)"
            )
        if "assessment_sessions" in tables:
            conn.exec_driver_sql(
                "UPDATE assessment_sessions SET owner_sub = 'local:default' "
                "WHERE owner_sub IS NULL OR TRIM(owner_sub) = ''"
            )
            conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS "
                "ix_assessment_sessions_owner_sub "
                "ON assessment_sessions(owner_sub)"
            )
        conn.commit()


def _backfill_and_normalize() -> None:
    """Idempotent data migration: question_text backfill + standard values.

    - question_text: backfilled from the clean (plain-text) question; existing
      non-empty values are never overwritten.
    - cognitive_skills: gerund forms -> action-verb forms (Remembering->Remember).
    - question_appears_in: legacy 'Pre/Post-Worksheet/Test' -> comma list.
    - answers JSON: answer_type 'Words' -> 'Phrases'.
    - concept sources: legacy '; ' separators -> comma-separated.
    - Topic/Concept machine_id: minted for BLANK columns only, in
      ``identity.source_order_key`` order (spec-step8 T4-8).
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
        if _backfill_machine_ids(db):
            changed = True
        if changed:
            db.commit()
    finally:
        db.close()


def _backfill_machine_ids(db) -> bool:
    """Give every EXISTING Topic/Concept row the id S4 made mandatory.

    T4-1 added the columns and said nothing about the rows already in the
    database; this is where they acquire one. It rides the contract this
    function already had — idempotent, and existing non-empty values are NEVER
    overwritten — which is exactly the property a minted-once identity needs.

    ``n``/``m`` are 1-based positions in ``identity.source_order_key`` order,
    ``n`` scoped to ``(chapter, pre_post_learning)`` and ``m`` to the topic.
    The enumeration walks EVERY sibling, including ones that already carry an
    id, so a blank row takes the position it actually holds rather than a
    position renumbered by its neighbours' luck. A legacy row with
    ``source_order == 0`` sorts after every positioned sibling, in creation-id
    order: two ``source_order == 0`` topics with ids 7 and 3 give T01 to id 3
    and T02 to id 7, and running this twice changes nothing.

    Left blank only on genuine impossibility (no chapter, hence no identity
    tuple). It is not guessed, and ``machine_id_for_*`` mints on first use.
    """
    from . import models
    from .services import identity

    def _has_blank(model) -> bool:
        """Is there ANY row still to mint?

        The enumeration below deliberately walks every sibling, including
        already-minted ones, so a blank row takes the position it actually
        holds — which means it cannot narrow its query. This one-row probe
        keeps that intact and still spares a settled database a full-table
        load of every concept's TEXT columns on every boot.
        """
        return db.query(model.id).filter(
            (model.machine_id == "") | (model.machine_id.is_(None))
        ).first() is not None

    changed = False
    topics_by_group: dict[tuple, list] = {}
    for topic in (db.query(models.Topic).all() if _has_blank(models.Topic)
                  else []):
        # Grouped by the LANE TOKEN, which is what actually goes into the id.
        # [measured] grouping on the raw ``pre_post_learning`` string put a
        # "Post" topic and a "post" topic in two groups that both number from
        # 1 while both resolving to ``PL``, so both took ``…_PL_T01`` — one
        # identity for two rows, hence one ``question_label`` for two
        # concepts, hence a silently skipped question (R4).
        topics_by_group.setdefault(
            (
                int(topic.chapter_id or 0),
                identity.lane_token(topic.pre_post_learning),
            ), [],
        ).append(topic)
    for (chapter_id, token), rows in sorted(
        topics_by_group.items(), key=lambda item: item[0],
    ):
        chapter = db.get(models.Chapter, chapter_id) if chapter_id else None
        if chapter is None:
            continue
        key = identity.chapter_key(chapter)
        ordered = sorted(rows, key=identity.source_order_key)
        # A slot an already-minted sibling holds is never handed out twice.
        taken = {
            str(topic.machine_id or "").strip()
            for topic in ordered
            if str(topic.machine_id or "").strip()
        }
        for position, topic in enumerate(ordered, start=1):
            if str(topic.machine_id or "").strip():
                continue
            topic.machine_id = identity.free_slot(
                lambda n: identity.compose_topic_machine_id(key, token, n),
                position,
                taken,
            )
            taken.add(topic.machine_id)
            changed = True

    concepts_by_topic: dict[int, list] = {}
    for concept in (db.query(models.Concept).all()
                    if _has_blank(models.Concept) else []):
        concepts_by_topic.setdefault(int(concept.topic_id or 0), []).append(concept)
    for topic_id, rows in sorted(concepts_by_topic.items()):
        topic = db.get(models.Topic, topic_id) if topic_id else None
        base = str(getattr(topic, "machine_id", "") or "").strip()
        if not base:
            continue
        ordered = sorted(rows, key=identity.source_order_key)
        taken = {
            str(concept.machine_id or "").strip()
            for concept in ordered
            if str(concept.machine_id or "").strip()
        }
        for position, concept in enumerate(ordered, start=1):
            if str(concept.machine_id or "").strip():
                continue
            concept.machine_id = identity.free_slot(
                lambda m: identity.compose_concept_machine_id(base, m),
                position,
                taken,
            )
            taken.add(concept.machine_id)
            changed = True
    return changed

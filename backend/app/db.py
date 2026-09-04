import json
import logging

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import DATA_DIR, DB_URL

log = logging.getLogger(__name__)

# spec-step8 T5-4/T5-5. The unique index on ``questions.question_label`` is
# PARTIAL and it is GATED on a scan.
#
#   * partial, because blank labels are legion and legitimate —
#     ``reader.py:369-370`` creates a ``Question`` from a row that carries a
#     question and no label, so the column's ``''`` default is reachable, and
#     [measured, sqlite 3.45.1] two ``''`` rows fail a plain unique index and
#     are accepted by ``WHERE question_label <> ''``.
#   * gated, because ``init_db`` runs inside the FastAPI lifespan
#     (``app/main.py:42``): a raise here takes the product down for the very
#     reviewer who has to repair the duplicates, and ``_backfill_and_normalize``
#     never runs either. A database that already carries duplicates BOOTS; the
#     finding is recorded and the index is simply not created on it.
#
# The scan is never a bare create wrapped in ``try/except`` — that would swallow
# the duplicates instead of naming them.
QUESTION_LABEL_INDEX = "ux_questions_question_label"
QUESTION_LABEL_DUPLICATE = "question_label_duplicate"
INTEGRITY_REPORT = DATA_DIR / "db_integrity" / "question_label_duplicates.json"

# The last scan's verdict, in-process. The durable copy is ``INTEGRITY_REPORT``.
QUESTION_LABEL_INDEX_STATUS: dict = {}


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
        ("upload_jobs", "chapter_duration_minutes", "INTEGER DEFAULT 0"),
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
        # The converged release core (spec-step8 T2/S6): one immutable row
        # per LANE per run, on the table that already exists. A RENAME is
        # not expressible here — this helper emits only ADD COLUMN, so a
        # renamed table would be minted empty by ``create_all`` and every
        # published release and receipt would be orphaned. Adding columns
        # is expressible and free.
        ("assessment_releases", "lane", "VARCHAR(16) DEFAULT ''"),
        ("assessment_releases", "layout_id", "VARCHAR(64) DEFAULT ''"),
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
        if "assessment_releases" in tables:
            # ``lane`` is declared ``index=True`` on the model, which only
            # reaches databases ``create_all`` mints. The database that
            # matters is the MIGRATED one, and ``ADD COLUMN`` carries no
            # index — so without this line the deployed schema differs
            # permanently from the declared model. Same shape, and beside,
            # the two ``owner_sub`` indexes above.
            conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_assessment_releases_lane "
                "ON assessment_releases(lane)"
            )
        if "questions" in tables:
            _ensure_question_label_index(conn)
        conn.commit()


def _ensure_question_label_index(conn) -> None:
    """Scan first, create second, and never halt startup (T5-4).

    ``CREATE UNIQUE INDEX IF NOT EXISTS`` is the only route to a unique
    constraint here: ``_ensure_columns`` is additive ``ADD COLUMN`` only, there
    is no alembic and no migrations directory, and a ``UniqueConstraint`` on
    the model would only reach databases whose ``questions`` table does not yet
    exist — a green suite proving nothing about the databases that matter.

    "Never halt startup" is stated absolutely, so it is also held absolutely
    against the shape of the table, not only against its contents: a
    ``questions`` table without a ``question_label`` column makes the scan
    itself raise ``OperationalError`` straight out of the FastAPI lifespan.
    ``question_label`` is an original ``NOT NULL`` model column and is not in
    the ``additions`` list, so no database the product has written has that
    shape — which is exactly why an unchecked assumption about it would be
    discovered by a reviewer who can no longer boot the app to look. The column
    is checked, not assumed; that is the same PRAGMA the ADD COLUMN loop above
    already runs, and it is mechanics, not judgment.
    """
    columns = {
        row[1] for row in conn.exec_driver_sql("PRAGMA table_info(questions)")
    }
    if "question_label" not in columns:
        QUESTION_LABEL_INDEX_STATUS.clear()
        QUESTION_LABEL_INDEX_STATUS.update({
            "code": "question_label_column_absent",
            "index": QUESTION_LABEL_INDEX,
            "created": False,
            "duplicates": [],
        })
        log.warning(
            "questions.question_label is absent; %s not created",
            QUESTION_LABEL_INDEX,
        )
        return
    duplicates = [
        {"question_label": row[0], "rows": int(row[1])}
        for row in conn.exec_driver_sql(
            "SELECT question_label, COUNT(*) FROM questions "
            "WHERE question_label IS NOT NULL AND question_label <> '' "
            "GROUP BY question_label HAVING COUNT(*) > 1 "
            "ORDER BY question_label"
        )
    ]
    if duplicates:
        # Recorded, not raised. Repair is a reviewer path (T5-5): these surface
        # as ``question_label_duplicate`` release-audit issues, and until they
        # are resolved the freeze-time check and the upload refusal carry the
        # guarantee on this database.
        _record_question_label_duplicates(duplicates)
        return
    conn.exec_driver_sql(
        f"CREATE UNIQUE INDEX IF NOT EXISTS {QUESTION_LABEL_INDEX} "
        "ON questions(question_label) WHERE question_label <> ''"
    )
    QUESTION_LABEL_INDEX_STATUS.clear()
    QUESTION_LABEL_INDEX_STATUS.update({
        "created": True, "duplicates": [],
    })
    try:
        INTEGRITY_REPORT.unlink(missing_ok=True)
    except OSError as exc:  # pragma: no cover - reporting, never the gate
        log.warning("could not clear %s: %s", INTEGRITY_REPORT, exc)


def _record_question_label_duplicates(duplicates: list[dict]) -> None:
    """Durably name what blocked the index, without stopping the app."""
    finding = {
        "code": QUESTION_LABEL_DUPLICATE,
        "index": QUESTION_LABEL_INDEX,
        "created": False,
        "duplicates": duplicates,
        "detail": (
            "duplicate non-blank question_label values are present, so the "
            "unique index was not created on this database; resolve them "
            "through the release-audit surface and restart"
        ),
    }
    QUESTION_LABEL_INDEX_STATUS.clear()
    QUESTION_LABEL_INDEX_STATUS.update(finding)
    log.warning(
        "%s: %s duplicate question_label values; %s not created",
        QUESTION_LABEL_DUPLICATE, len(duplicates), QUESTION_LABEL_INDEX,
    )
    try:
        INTEGRITY_REPORT.parent.mkdir(parents=True, exist_ok=True)
        INTEGRITY_REPORT.write_text(
            json.dumps(finding, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
    except OSError as exc:  # pragma: no cover - reporting, never the gate
        log.warning("could not write %s: %s", INTEGRITY_REPORT, exc)


def _backfill_and_normalize() -> None:
    """Idempotent data migration: question_text backfill + standard values.

    - question_text: backfilled from the clean (plain-text) question; existing
      non-empty values are never overwritten.
    - cognitive_skills: gerund forms -> action-verb forms (Remembering->Remember).
    - question_appears_in: legacy 'Pre/Post-Worksheet/Test' -> pipe list.
    - answers JSON: answer_type 'Words' -> 'Phrases' (the canonical enum;
      the workbook cell projects the lane literal, contract v2.0 §22-§24).
    - every stored multi-value list (chapter pre/post topics, topic
      related_topics, concept keywords/digicards/related_concepts/sources,
      group related_digicards): legacy comma/semicolon -> " | " (§16).
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
        # Contract v2.0 §16: every stored multi-value list carries the
        # exact " | " delimiter. A pre-v2.0 row holds a comma/semicolon
        # list; it is re-delimited ONCE here (the reading side of the
        # migration allowance) so no export ever has to guess whether a
        # comma is a separator or content. Rosters of identified titles
        # (§15: every item closes with its "(id)" tag) are split by that
        # grammar, so a comma inside a title survives the migration.
        roster_columns = {
            "pre_topics", "post_topics", "related_topics", "related_concepts",
        }
        for model, columns in (
            (models.Chapter, ("pre_topics", "post_topics")),
            (models.Topic, ("related_topics",)),
            (models.Concept, (
                "keywords", "digicards", "related_concepts", "sources",
            )),
            (models.Group, ("related_digicards",)),
        ):
            for row in db.query(model).all():
                for column in columns:
                    value = str(getattr(row, column, "") or "")
                    if "|" in value or not any(ch in value for ch in ",;"):
                        continue
                    splitter = (
                        bi.split_roster if column in roster_columns
                        else bi.split_multi
                    )
                    setattr(row, column, bi.join_multi(splitter(value)))
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

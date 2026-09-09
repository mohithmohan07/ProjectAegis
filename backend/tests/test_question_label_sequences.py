"""Issued question numbers survive deletion and concurrent generation.

These tests use independent SQLite connections, including a recreated engine,
so a process-local cache or a live-question MAX scan cannot satisfy them.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app import db as app_db
from app import models
from app.services import identity


def _engine(path):
    engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )

    @event.listens_for(engine, "connect")
    def configure_sqlite(connection, _record):
        cursor = connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


@pytest.fixture
def sequence_database(tmp_path):
    path = tmp_path / "issued-labels.db"
    engine = _engine(path)
    app_db.Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    yield engine, factory, path
    engine.dispose()


def _group(db, *, base="SEQUENCE_TEST"):
    chapter = models.Chapter(chapter_code="SEQ", chapter_title="Original title")
    topic = models.Topic(chapter=chapter, topic_title="Recorded topic", machine_id="SEQ_T01")
    concept = models.Concept(
        topic=topic, concept_title="Recorded concept", machine_id=base,
    )
    group = models.Group(concept=concept, group_type="Basic")
    db.add(group)
    db.flush()
    return group


def _question(group, label):
    return models.Question(
        group=group, question_label=label, question="Recorded question",
    )


def _release(*labels, version=1):
    return models.AssessmentRelease(
        release_uid=f"sequence-test-{version}", version=version,
        state="materialized",
        payload={"candidates": [{"question_label": label} for label in labels]},
    )


def test_peeking_an_unused_family_is_read_only(sequence_database):
    _engine_, sessions, _path = sequence_database
    with sessions() as db:
        assert identity.next_label_index(db, "NEW") == 1
        assert identity.next_label_index(db, "NEW") == 1
        assert db.query(models.QuestionLabelSequence).count() == 0
        assert not db.new


def test_committed_reservations_survive_engine_restart_without_live_questions(
    sequence_database,
):
    engine, sessions, path = sequence_database
    with sessions() as db:
        assert identity.reserve_label_indices(db, {"SOURCE": 3, "": 2}) == {
            "SOURCE": 1, "": 1,
        }
        db.commit()
        assert db.query(models.Question).count() == 0
    engine.dispose()

    restarted_engine = _engine(path)
    try:
        restarted_sessions = sessionmaker(bind=restarted_engine, autoflush=False)
        with restarted_sessions() as db:
            assert identity.next_label_index(db, "SOURCE") == 4
            assert identity.next_label_index(db, "") == 3
            assert identity.reserve_label_indices(db, {"SOURCE": 2, "": 1}) == {
                "SOURCE": 4, "": 3,
            }
            db.commit()
        with restarted_sessions() as db:
            assert identity.next_label_index(db, "SOURCE") == 6
            assert identity.next_label_index(db, "") == 4
    finally:
        restarted_engine.dispose()


@pytest.mark.parametrize("reservation_key", [None, "rolled-back-master"])
def test_reservation_rollback_does_not_commit_unrelated_changes(sequence_database, reservation_key):
    _engine_, sessions, _path = sequence_database
    with sessions() as db:
        chapter = _group(db).concept.topic.chapter
        db.commit()
        chapter_id = chapter.id

        chapter.chapter_title = "Uncommitted edit"
        db.flush()
        assert identity.reserve_label_indices(
            db, {"ROLLBACK": 4}, reservation_key=reservation_key,
        ) == {"ROLLBACK": 1}
        with sessions() as observer:
            assert observer.get(models.Chapter, chapter_id).chapter_title == "Original title"
            assert identity.next_label_index(observer, "ROLLBACK") == 1
            assert observer.query(models.QuestionLabelReservation).count() == 0
        db.rollback()

    with sessions() as db:
        assert db.get(models.Chapter, chapter_id).chapter_title == "Original title"
        assert identity.reserve_label_indices(
            db, {"ROLLBACK": 2}, reservation_key=reservation_key,
        ) == {"ROLLBACK": 1}
        db.commit()
        assert identity.next_label_index(db, "ROLLBACK") == 3


def test_receipt_replay_survives_restart_without_advancing_numbers(sequence_database):
    engine, sessions, path = sequence_database
    counts = {"MASTER_A": 3, "MASTER_B": 2}
    with sessions() as db:
        assert identity.reserve_label_indices(
            db, counts, reservation_key="accepted-master-one",
        ) == {"MASTER_A": 1, "MASTER_B": 1}
        db.commit()
    engine.dispose()

    restarted_engine = _engine(path)
    try:
        restarted_sessions = sessionmaker(bind=restarted_engine, autoflush=False)
        with restarted_sessions() as db:
            # JSON member order is not a new accepted generation run.
            assert identity.reserve_label_indices(
                db, {"MASTER_B": 2, "MASTER_A": 3},
                reservation_key="accepted-master-one",
            ) == {"MASTER_A": 1, "MASTER_B": 1}
            db.commit()
            receipt = db.get(models.QuestionLabelReservation, "accepted-master-one")
            assert receipt.counts == counts
            assert receipt.starts == {"MASTER_A": 1, "MASTER_B": 1}
            assert identity.next_label_index(db, "MASTER_A") == 4
            assert identity.next_label_index(db, "MASTER_B") == 3
            assert identity.reserve_label_indices(
                db, counts, reservation_key="accepted-master-two",
            ) == {"MASTER_A": 4, "MASTER_B": 3}
            db.commit()
            assert db.query(models.QuestionLabelReservation).count() == 2
    finally:
        restarted_engine.dispose()


def test_reusing_a_receipt_key_with_different_counts_is_rejected(sequence_database):
    _engine_, sessions, _path = sequence_database
    with sessions() as db:
        identity.reserve_label_indices(db, {"IMMUTABLE": 2}, reservation_key="same-key")
        db.commit()
        with pytest.raises(ValueError):
            identity.reserve_label_indices(db, {"IMMUTABLE": 3}, reservation_key="same-key")
        db.rollback()
    with sessions() as db:
        assert identity.next_label_index(db, "IMMUTABLE") == 3
        receipt = db.get(models.QuestionLabelReservation, "same-key")
        assert receipt.counts == {"IMMUTABLE": 2}
        assert receipt.starts == {"IMMUTABLE": 1}


def test_concurrent_replays_reserve_one_range_for_one_receipt(sequence_database):
    _engine_, sessions, _path = sequence_database
    ready = Barrier(4)
    counts = {"REPLAY_A": 2, "REPLAY_B": 3}

    def allocate(_worker):
        with sessions() as db:
            db.connection()
            ready.wait(timeout=10)
            starts = identity.reserve_label_indices(
                db, counts, reservation_key="shared-accepted-master",
            )
            db.commit()
            return starts

    with ThreadPoolExecutor(max_workers=4) as pool:
        allocations = list(pool.map(allocate, range(4)))
    assert allocations == [{"REPLAY_A": 1, "REPLAY_B": 1}] * 4
    with sessions() as db:
        assert db.query(models.QuestionLabelReservation).count() == 1
        assert identity.next_label_index(db, "REPLAY_A") == 3
        assert identity.next_label_index(db, "REPLAY_B") == 4
        assert identity.reserve_label_indices(
            db, counts, reservation_key="different-accepted-master",
        ) == {"REPLAY_A": 3, "REPLAY_B": 4}
        db.commit()


def test_master_reservation_commits_before_provider_work_and_survives_caller_rollback(
    sequence_database,
):
    from app.services.assessment_release_run import _reserve_master_labels

    _engine_, sessions, _path = sequence_database
    with sessions() as caller:
        chapter = _group(caller).concept.topic.chapter
        caller.commit()
        chapter_id = chapter.id
        assert caller.get(models.Chapter, chapter_id).chapter_title == "Original title"
        assert _reserve_master_labels(
            caller, {"MASTER": 2}, reservation_key="provider-bound-master",
        ) == {"MASTER": 1}

        def independent_writer():
            with sessions() as writer:
                writer.get(models.Chapter, chapter_id).chapter_title = "Other run committed"
                writer.commit()

        # The caller stays open as it does during a potentially long provider
        # call. An unrelated writer must not wait for that work to finish.
        with ThreadPoolExecutor(max_workers=1) as pool:
            write = pool.submit(independent_writer)
            try:
                write.result(timeout=5)
            finally:
                caller.rollback()

    with sessions() as db:
        assert db.get(models.Chapter, chapter_id).chapter_title == "Other run committed"
        assert identity.next_label_index(db, "MASTER") == 3
        assert _reserve_master_labels(
            db, {"MASTER": 2}, reservation_key="provider-bound-master",
        ) == {"MASTER": 1}
        assert identity.next_label_index(db, "MASTER") == 3


def test_master_reservation_refuses_pending_writes_without_committing_them(sequence_database):
    from app.services.assessment_release_run import ReleaseRunError, _reserve_master_labels

    _engine_, sessions, _path = sequence_database
    with sessions() as caller:
        chapter = _group(caller).concept.topic.chapter
        caller.commit()
        chapter_id = chapter.id
        chapter.chapter_title = "Uncommitted caller edit"
        caller.flush()
        with pytest.raises(ReleaseRunError, match="commit or roll back"):
            _reserve_master_labels(
                caller, {"MASTER": 2}, reservation_key="refused-master",
            )
        caller.rollback()
    with sessions() as db:
        assert db.get(models.Chapter, chapter_id).chapter_title == "Original title"
        assert identity.next_label_index(db, "MASTER") == 1
        assert db.query(models.QuestionLabelReservation).count() == 0


@pytest.mark.parametrize("already_issued", [0, 7])
def test_concurrent_sessions_reserve_disjoint_ranges(sequence_database, already_issued):
    _engine_, sessions, _path = sequence_database
    base = "CONCURRENT_%_FAMILY"
    if already_issued:
        with sessions() as db:
            identity.reserve_label_indices(db, {base: already_issued})
            db.commit()

    counts = [2, 3, 4, 5]
    ready = Barrier(len(counts))

    def allocate(count):
        with sessions() as db:
            # Establish separate DB connections before simultaneous allocation.
            db.connection()
            ready.wait(timeout=10)
            start = identity.reserve_label_indices(db, {base: count})[base]
            db.commit()
            return list(range(start, start + count))

    with ThreadPoolExecutor(max_workers=len(counts)) as pool:
        ranges = list(pool.map(allocate, counts))

    issued = [index for allocation in ranges for index in allocation]
    assert len(issued) == len(set(issued)) == sum(counts)
    assert sorted(issued) == list(range(already_issued + 1, already_issued + sum(counts) + 1))
    with sessions() as db:
        assert identity.next_label_index(db, base) == already_issued + sum(counts) + 1


@pytest.mark.parametrize("deletion", ["highest", "all_questions", "concept"])
def test_directly_inserted_question_numbers_survive_deletion(sequence_database, deletion):
    _engine_, sessions, _path = sequence_database
    with sessions() as db:
        group = _group(db, base="DELETED")
        first = _question(group, "DELETED Q01")
        highest = _question(group, "DELETED Q02")
        db.add_all([first, highest])
        db.commit()

        if deletion == "highest":
            db.delete(highest)
        elif deletion == "all_questions":
            db.delete(first)
            db.delete(highest)
        else:
            db.delete(group.concept)
        db.commit()

    with sessions() as db:
        assert identity.next_label_index(db, "DELETED") == 3
        assert identity.reserve_label_indices(db, {"DELETED": 1}) == {"DELETED": 3}
        db.commit()


def test_question_label_updates_retain_both_old_and_new_family_history(sequence_database):
    _engine_, sessions, _path = sequence_database
    with sessions() as db:
        question = _question(_group(db), "OLD_FAMILY Q08")
        db.add(question)
        db.commit()
        question.question_label = "NEW_FAMILY Q12"
        db.commit()
        db.delete(question)
        db.commit()

    with sessions() as db:
        assert identity.next_label_index(db, "OLD_FAMILY") == 9
        assert identity.next_label_index(db, "NEW_FAMILY") == 13


@pytest.mark.parametrize("storage", ["payload", "concept_snapshot"])
def test_staged_release_labels_are_retained_before_database_publication(sequence_database, storage):
    _engine_, sessions, _path = sequence_database
    with sessions() as db:
        release = _release()
        setattr(release, storage, {"candidates": [
            {"question_label": "STAGED Q09"}, {"question_label": " Q04"},
        ]})
        db.add(release)
        db.commit()
        assert db.query(models.Question).count() == 0
        assert identity.next_label_index(db, "STAGED") == 10
        assert identity.next_label_index(db, "") == 5

        # A replacement snapshot must not retire the counter for its old labels.
        setattr(release, storage, {"candidates": [{"question_label": "STAGED Q13"}]})
        db.commit()
        db.delete(release)
        db.commit()

    with sessions() as db:
        assert db.query(models.Question).count() == 0
        assert db.query(models.AssessmentRelease).count() == 0
        assert identity.next_label_index(db, "STAGED") == 14
        assert identity.next_label_index(db, "") == 5


def test_lower_imported_numbers_do_not_reduce_the_issued_range(sequence_database):
    _engine_, sessions, _path = sequence_database
    with sessions() as db:
        assert identity.reserve_label_indices(db, {"IMPORT": 20}) == {"IMPORT": 1}
        db.commit()
        question = _question(_group(db), "IMPORT Q03")
        db.add(question)
        db.commit()
        db.delete(question)
        db.commit()
        assert identity.next_label_index(db, "IMPORT") == 21


def test_pending_imports_advance_reservations_in_the_same_transaction(sequence_database):
    _engine_, sessions, _path = sequence_database
    with sessions() as db:
        group = _group(db)
        db.commit()
        db.add(_question(group, "PENDING Q07"))
        assert identity.reserve_label_indices(db, {"PENDING": 3}) == {"PENDING": 8}
        db.commit()
    with sessions() as db:
        assert identity.next_label_index(db, "PENDING") == 11
        assert db.scalar(select(models.Question.question_label)) == "PENDING Q07"


def test_blank_legacy_and_sql_wildcard_families_remain_distinct(sequence_database):
    _engine_, sessions, _path = sequence_database
    labels = [
        " Q02", "LEGACY_PREFIX Q017", "FAMILY_% Q05", "FAMILY_AX Q99",
        "", "unstructured-legacy-991", "FAMILY_% Qunknown",
    ]
    with sessions() as db:
        group = _group(db)
        db.add_all([_question(group, label) for label in labels])
        db.commit()
        assert db.scalars(select(models.Question.question_label).order_by(models.Question.id)).all() == labels
        assert identity.next_label_index(db, "") == 3
        assert identity.next_label_index(db, "LEGACY_PREFIX") == 18
        assert identity.next_label_index(db, "FAMILY_%") == 6
        assert identity.next_label_index(db, "FAMILY_AX") == 100
        assert identity.next_label_index(db, "UNSEEN") == 1
        assert db.query(models.QuestionLabelSequence).count() == 4


def test_last_integer_can_be_issued_once_without_overflow_reuse(sequence_database):
    from app.services.question_label_sequences import MAX_LABEL_INDEX, QuestionLabelExhausted

    _engine_, sessions, _path = sequence_database
    with sessions() as db:
        db.add(_question(_group(db), f"LIMIT Q{MAX_LABEL_INDEX - 1}"))
        db.commit()
        assert identity.reserve_label_indices(db, {"LIMIT": 1}) == {"LIMIT": MAX_LABEL_INDEX}
        db.commit()

    for _attempt in range(2):
        with sessions() as db:
            with pytest.raises(QuestionLabelExhausted, match="question label range exhausted"):
                identity.reserve_label_indices(db, {"LIMIT": 1})
            db.rollback()
            last_issued = db.get(models.QuestionLabelSequence, "LIMIT").last_issued
            assert type(last_issued) is int
            assert last_issued == MAX_LABEL_INDEX


def test_oversized_imported_label_remains_verbatim_and_bootable_but_exhausts_family(
    sequence_database, monkeypatch, tmp_path,
):
    from app.services.question_label_sequences import MAX_LABEL_INDEX, QuestionLabelExhausted

    engine, sessions, path = sequence_database
    label = f"OVERSIZED Q{10 ** 30}"
    with sessions() as db:
        question = _question(_group(db), label)
        db.add(question)
        db.commit()
        question_id = question.id

    monkeypatch.setattr(app_db, "engine", engine)
    monkeypatch.setattr(app_db, "SessionLocal", sessions)
    monkeypatch.setattr(app_db, "DB_URL", f"sqlite:///{path}")
    monkeypatch.setattr(app_db, "INTEGRITY_REPORT", tmp_path / "integrity.json")
    monkeypatch.setattr(app_db, "QUESTION_LABEL_INDEX_STATUS", {})
    app_db.init_db()

    with sessions() as db:
        question = db.get(models.Question, question_id)
        assert question.question_label == label
        assert db.get(models.QuestionLabelSequence, "OVERSIZED").last_issued == MAX_LABEL_INDEX
        with pytest.raises(QuestionLabelExhausted, match="question label range exhausted"):
            identity.reserve_label_indices(db, {"OVERSIZED": 1})
        db.rollback()
        db.delete(question)
        db.commit()

    with sessions() as db:
        with pytest.raises(QuestionLabelExhausted, match="question label range exhausted"):
            identity.reserve_label_indices(db, {"OVERSIZED": 1})


def test_startup_backfills_live_and_retained_release_labels_without_renumbering(
    sequence_database, monkeypatch, tmp_path,
):
    engine, sessions, path = sequence_database
    with sessions() as db:
        group_id = _group(db).id
        db.commit()

    # Core inserts deliberately bypass mapper hooks, reproducing old databases.
    live_labels = ["MIGRATED Q05", "LEGACY Q011", " Q03", "opaque-old-label"]
    with engine.begin() as connection:
        connection.execute(models.Question.__table__.insert(), [
            {"group_id": group_id, "question_label": label, "question": "Existing question"}
            for label in live_labels
        ])
        connection.execute(models.AssessmentRelease.__table__.insert(), {
            "release_uid": "pre-counter-snapshot", "state": "superseded",
            "payload": {"candidates": [
                {"question_label": "MIGRATED Q12"},
                {"question_label": "RELEASE_ONLY Q21"},
                {"question_label": " Q08"},
                {"question_label": "opaque-release-label"},
                {"question_label": ""},
            ]},
            "concept_snapshot": {"candidates": [{"question_label": "SNAPSHOT_ONLY Q31"}]},
        })
    models.QuestionLabelSequence.__table__.drop(bind=engine)
    monkeypatch.setattr(app_db, "engine", engine)
    monkeypatch.setattr(app_db, "SessionLocal", sessions)
    monkeypatch.setattr(app_db, "DB_URL", f"sqlite:///{path}")
    monkeypatch.setattr(app_db, "INTEGRITY_REPORT", tmp_path / "integrity.json")
    monkeypatch.setattr(app_db, "QUESTION_LABEL_INDEX_STATUS", {})

    app_db.init_db()
    with sessions() as db:
        assert db.scalars(select(models.Question.question_label).order_by(models.Question.id)).all() == live_labels
        assert identity.next_label_index(db, "MIGRATED") == 13
        assert identity.next_label_index(db, "LEGACY") == 12
        assert identity.next_label_index(db, "") == 9
        assert identity.next_label_index(db, "RELEASE_ONLY") == 22
        assert identity.next_label_index(db, "SNAPSHOT_ONLY") == 32
        # A real migration must keep the reconstructed history after all of its
        # source rows disappear, including snapshots from superseded releases.
        db.query(models.Question).delete(synchronize_session=False)
        db.query(models.AssessmentRelease).delete(synchronize_session=False)
        db.commit()

    app_db.init_db()
    with sessions() as db:
        assert identity.next_label_index(db, "MIGRATED") == 13
        assert identity.next_label_index(db, "LEGACY") == 12
        assert identity.next_label_index(db, "") == 9
        assert identity.next_label_index(db, "RELEASE_ONLY") == 22
        assert identity.next_label_index(db, "SNAPSHOT_ONLY") == 32

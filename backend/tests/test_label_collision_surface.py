"""Step 8 slice S5 — the label-collision surface (spec T5-1..T5-4, R4/R5).

The defect this file pins: ``assessment_release_service`` used to skip a
candidate whose ``question_label`` already existed with a bare ``continue`` and
no flag, so a learner's question disappeared between the release and the
database and nothing anywhere recorded it. [measured, before the fix] two
releases sharing one label uploaded with ``questions_created: 0``, the question
count unchanged, ``payload_errors == []``, ``readiness == "ready"`` and no note
on the release — the exact R4 loss.

Four parts, and two of them are ATOMIC: the skip-vs-refuse branch is only
correct because the label family it compares is minted by the one minter S4
left, and because a legitimate RE-publication of the same release is told apart
from a genuine collision by ``route_audit.release_uid``. Split them and the
refuse branch fires on every second act.
"""
from __future__ import annotations

import copy
import json
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import db as app_db
from app import models
from app.services import assessment_release as rel
from app.services import assessment_release_service as svc
from app.services import generation, identity
from tests.conftest import stream_result
from tests.test_mes_release_lifecycle import (
    OWNER, _chapter_concept, _payload,
)


# --------------------------------------------------------------------------- #
# Helpers — one release, one label, published and downloadable.
# --------------------------------------------------------------------------- #

def _published(db, *, label, question, mutate=None):
    concept = _chapter_concept(db)
    concept_key = f"db:{concept.id}"
    machine_id = svc._machine_id({
        "concept_key": concept_key,
        "concept_title": concept.concept_title,
    })
    payload = _payload(
        concept_key, machine_id, concept.concept_display_name, label=label)
    candidate = payload["candidates"][0]
    candidate["candidate_id"] = f"CAND-{uuid.uuid4().hex[:8]}"
    candidate["question"] = question
    candidate["question_text"] = question
    if mutate:
        mutate(payload)
    release = svc.create_release(
        db, chapter_id=concept.topic.chapter_id, payload=payload,
        owner_sub=OWNER)
    return release, payload


def _fresh_concept(db):
    """A concept of this file's own, so a shared fixture database cannot make
    a label-numbering assertion depend on test order."""
    chapter = _chapter_concept(db).topic.chapter
    token = uuid.uuid4().hex[:8]
    topic = models.Topic(
        chapter_id=chapter.id,
        topic_title=f"S5 topic {token}",
        topic_display_name=f"S5 topic {token}",
        pre_post_learning="Post",
        topic_description="A topic authored by the S5 regressions.",
    )
    concept = models.Concept(
        topic=topic,
        concept_title=f"S5 concept {token}",
        concept_display_name=f"S5 concept {token}",
        parent_concept="",
        concept_details="Teaching content for the S5 regressions.",
        keywords="s5, labels",
        sources="fixture",
    )
    db.add(concept)
    db.add(models.Group(
        concept=concept,
        group_type="Basic",
        group_key="",
        group_name=f"{concept.concept_title} — Basic",
        group_display_name=f"{concept.concept_title} — Basic",
    ))
    db.commit()
    db.refresh(concept)
    return concept


def _reopen_for_a_second_act(db, release):
    """Rule G's second publication act on the SAME release.

    The (uid, version, master hash) idempotency key short-circuits a repeat
    before the label loop is ever reached, so it is cleared deliberately —
    otherwise this test would prove only that the outer guard works, and the
    inner skip-vs-refuse branch would never execute.
    """
    release.publication = {
        key: value
        for key, value in dict(release.publication or {}).items()
        if key not in {"uploaded_key", "uploaded_result"}
    }
    release.state = "ready_for_upload"
    db.commit()


# --------------------------------------------------------------------------- #
# T5-1 — detection at freeze, and the transport it reaches.
# --------------------------------------------------------------------------- #

def test_two_candidates_sharing_a_label_block_the_upload_and_still_download(
    db, client,
):
    label = f"DUPQ {uuid.uuid4().hex[:8]}"

    def twin(payload):
        twin_candidate = copy.deepcopy(payload["candidates"][0])
        twin_candidate["candidate_id"] = "CAND-twin"
        twin_candidate["question"] = "A different question, same label."
        twin_candidate["question_text"] = twin_candidate["question"]
        payload["candidates"].append(twin_candidate)

    release, _ = _published(
        db, label=label, question="The first question.", mutate=twin)

    # Seen where the collision is created, not where it is fatal.
    assert f"duplicate question_label {label!r}" in (
        release.diagnostics["payload_errors"])

    published = svc.publish_release(db, release)
    assert published.diagnostics["readiness"] == svc.BLOCKED

    # Nothing ever blocks a download (T9's principle).
    for artifact in ("concepts.xlsx", "master.xlsx"):
        response = client.get(
            f"/build-assessments/releases/{published.id}/{artifact}")
        assert response.status_code == 200
        assert response.content[:2] == b"PK"

    before = db.query(models.Question).count()
    with pytest.raises(svc.UploadRefused, match="blocked for database upload"):
        svc.upload_master_to_database(db, published, owner_sub=OWNER)
    assert db.query(models.Question).count() == before


def test_duplicate_question_labels_ignores_blanks_and_names_each_label_once():
    labels = rel.duplicate_question_labels([
        {"question_label": ""},
        {"question_label": ""},
        {"question_label": "X Q01"},
        {"question_label": "X Q01"},
        {"question_label": "X Q01"},
        {"question_label": "X Q02"},
    ])
    assert labels == ["X Q01"]


# --------------------------------------------------------------------------- #
# T5-2 — skip-vs-refuse, keyed on route_audit.release_uid.
# --------------------------------------------------------------------------- #

def test_a_colliding_label_refuses_the_upload_instead_of_dropping_the_question(
    db,
):
    label = f"COLQ {uuid.uuid4().hex[:8]}"
    first, _ = _published(db, label=label, question="The FIRST question?")
    svc.upload_master_to_database(
        db, svc.publish_release(db, first), owner_sub=OWNER)

    before = db.query(models.Question).count()
    second, _ = _published(db, label=label, question="The SECOND question?")
    second = svc.publish_release(db, second)
    with pytest.raises(
        svc.UploadRefused, match="already published under different content",
    ):
        svc.upload_master_to_database(db, second, owner_sub=OWNER)

    # The rollback is the point: nothing was written, and — the R4 clause —
    # nothing was dropped either. The second question is still in its release,
    # still downloadable, waiting for a reviewer.
    assert db.query(models.Question).count() == before
    rows = db.query(models.Question).filter(
        models.Question.question_label == label).all()
    assert [row.question for row in rows] == ["The FIRST question?"]
    assert second.state == "ready_for_upload"


def test_republishing_the_same_release_skips_with_a_recorded_note(db):
    label = f"REPUBQ {uuid.uuid4().hex[:8]}"
    release, _ = _published(db, label=label, question="Only one question?")
    published = svc.publish_release(db, release)
    first = svc.upload_master_to_database(db, published, owner_sub=OWNER)
    assert first["questions_created"] == 1
    assert first["labels_reissued"] == []

    _reopen_for_a_second_act(db, published)
    second = svc.upload_master_to_database(db, published, owner_sub=OWNER)

    # Rule G's idempotent second act still skips — but never unflagged.
    assert second["questions_created"] == 0
    assert second["labels_reissued"] == [label]
    assert db.query(models.Question).filter(
        models.Question.question_label == label).count() == 1
    notes = published.diagnostics["release_notes"]
    assert [note["code"] for note in notes] == [svc.QUESTION_LABEL_REISSUED]
    assert notes[0]["question_labels"] == [label]
    assert notes[0]["release_uid"] == published.release_uid


def test_a_generated_pre_question_with_no_source_qid_republishes_idempotently(
    db,
):
    """The case a source-identity key could not express.

    A generated pre-learning question is permitted to carry no source atom at
    all, so it stores ``source_qid == ""``; two of them are indistinguishable
    by source. ``route_audit.release_uid`` is the identity the insert already
    writes, and it separates "this release, again" from "a different release".
    """
    label = f"PREGENQ {uuid.uuid4().hex[:8]}"

    def generated(payload):
        candidate = payload["candidates"][0]
        candidate["source_atom_ids"] = []
        candidate["source_policy"] = "generate"
        candidate["origin"] = svc.QUESTION_ORIGIN_PRE_LEARNING
        payload["source_atoms"] = []

    release, _ = _published(
        db, label=label, question="What do you already know?",
        mutate=generated)
    published = svc.publish_release(db, release)
    assert published.diagnostics["readiness"] != svc.BLOCKED
    assert svc.upload_master_to_database(
        db, published, owner_sub=OWNER)["questions_created"] == 1

    row = db.query(models.Question).filter(
        models.Question.question_label == label).one()
    assert row.source_qid == ""  # the key Cluster C wanted does not exist
    assert row.origin == svc.QUESTION_ORIGIN_PRE_LEARNING

    _reopen_for_a_second_act(db, published)
    again = svc.upload_master_to_database(db, published, owner_sub=OWNER)
    assert again["questions_created"] == 0
    assert again["labels_reissued"] == [label]
    assert db.query(models.Question).filter(
        models.Question.question_label == label).count() == 1


# --------------------------------------------------------------------------- #
# T5-3 — the max-scan, shared by both lanes.
# --------------------------------------------------------------------------- #

def _labels_for(db, concept):
    base = identity.machine_id_for_concept(concept)
    prefix = identity.label_family_prefix(base)
    return base, sorted(
        question.question_label
        for group in concept.groups
        for question in group.questions
        if str(question.question_label or "").startswith(prefix)
    )


def _generate(client, concept_id, *, count):
    return _generate_over(client, [concept_id], count=count)


def _generate_over(client, concept_ids, *, count):
    session = client.post("/build-assessments/sessions", json={
        "scope_type": "concept", "scope_ids": list(concept_ids),
    }).json()
    client.post(f"/build-assessments/sessions/{session['id']}/batches", json={
        "cognitive_skills": ["Remembering"],
        "difficulty_levels": ["Less"],
        "categories": ["Multiple Choice Question"],
        "question_type": "objective",
        "num_questions": count,
    })
    return stream_result(
        client.post(f"/build-assessments/sessions/{session['id']}/generate"))


def test_deleting_a_question_does_not_reissue_its_number(db, client):
    concept = _fresh_concept(db)
    concept_id = concept.id
    _generate(client, concept_id, count=3)
    db.expire_all()
    concept = db.get(models.Concept, concept_id)
    base, labels = _labels_for(db, concept)
    assert labels == [f"{base} Q0{n}" for n in (1, 2, 3)]

    # R5's live case: a question is deleted after its number was issued.
    doomed = db.query(models.Question).filter(
        models.Question.question_label == f"{base} Q02").one()
    db.delete(doomed)
    db.commit()

    _generate(client, concept_id, count=1)
    db.expire_all()
    concept = db.get(models.Concept, concept_id)
    _, after = _labels_for(db, concept)

    # ``sum(len(group.questions)) + 1`` counts 2 live rows and hands back Q03 —
    # a number ALREADY on a live, published question. The max-scan continues
    # past the highest number the family ever took.
    assert after == [f"{base} Q01", f"{base} Q03", f"{base} Q04"]
    assert len(after) == len(set(after))
    assert identity.next_label_index(db, base) == 5


def test_a_legacy_label_family_does_not_share_a_scan_with_the_minted_one(db):
    concept = _fresh_concept(db)
    concept_id = concept.id
    base = identity.machine_id_for_concept(concept)
    db.commit()
    group = concept.groups[0]

    # A grandfathered row: minted under the old convention, never migrated.
    legacy_base = "10CBMA_OLD"
    db.add(models.Question(
        group_id=group.id, sheet_kind="objective",
        question_label=f"{legacy_base} Q07",
        question="A grandfathered question.",
    ))
    db.commit()
    db.expire_all()
    concept = db.get(models.Concept, concept_id)

    # The new family starts at 1: the legacy label is a different family, so
    # it is neither continued nor reassigned.
    assert identity.next_label_index(db, base) == 1
    assert identity.next_label_index(db, legacy_base) == 8

    db.add(models.Question(
        group_id=group.id, sheet_kind="objective",
        question_label=generation.question_label(concept, 1),
        question="A minted question.",
    ))
    db.commit()
    db.expire_all()
    concept = db.get(models.Concept, concept_id)
    assert identity.next_label_index(db, base) == 2
    assert db.query(models.Question).filter(
        models.Question.question_label == f"{legacy_base} Q07").count() == 1

    # And it is said out loud, naming BOTH prefixes.
    note = identity.legacy_label_family_for_concept(concept, base)
    assert note["code"] == identity.LEGACY_LABEL_FAMILY
    assert note["minted_prefix"] == f"{base} Q"
    assert note["legacy_prefixes"] == [f"{legacy_base} Q"]
    assert note["prefixes"] == [f"{legacy_base} Q", f"{base} Q"]


def test_the_max_scan_does_not_leak_across_machine_ids_through_like(db):
    """``_`` is a LIKE wildcard and a machine id is full of them."""
    concept = _fresh_concept(db)
    group = concept.groups[0]
    # Differs from the base below only where an underscore sits, so a LIKE
    # pattern that does not escape ``_`` matches it and the scan continues a
    # family that is not its own.
    db.add(models.Question(
        group_id=group.id, sheet_kind="objective",
        question_label="10CBMA-X-h8-PL-T01-C03 Q42",
        question="Another concept's question.",
    ))
    db.commit()
    assert identity.next_label_index(db, "10CBMA_X_h8_PL_T01_C03") == 1


# --------------------------------------------------------------------------- #
# T5-4 — the partial unique index. Three hard cases, each executed.
# --------------------------------------------------------------------------- #

def _isolated_db(tmp_path, monkeypatch, name="isolated.db"):
    path = tmp_path / name
    engine = create_engine(f"sqlite:///{path}")
    monkeypatch.setattr(app_db, "engine", engine)
    monkeypatch.setattr(app_db, "DB_URL", f"sqlite:///{path}")
    monkeypatch.setattr(
        app_db, "SessionLocal",
        sessionmaker(bind=engine, autoflush=False, autocommit=False))
    monkeypatch.setattr(
        app_db, "INTEGRITY_REPORT", tmp_path / "integrity.json")
    monkeypatch.setattr(app_db, "QUESTION_LABEL_INDEX_STATUS", {})
    return engine


def _indexes(engine):
    with engine.connect() as conn:
        return {
            row[0] for row in conn.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type = 'index'")
        }


def _insert_label(engine, label):
    """One question row on the isolated database, through the ORM so the
    model's own column defaults apply."""
    session = sessionmaker(bind=engine)()
    try:
        session.add(models.Question(
            group_id=1, sheet_kind="objective", question="q",
            question_label=label,
        ))
        session.commit()
    finally:
        session.close()


def test_unique_index_is_created_on_a_clean_database(tmp_path, monkeypatch):
    engine = _isolated_db(tmp_path, monkeypatch)
    app_db.init_db()

    assert app_db.QUESTION_LABEL_INDEX in _indexes(engine)
    assert app_db.QUESTION_LABEL_INDEX_STATUS["created"] is True
    assert not (tmp_path / "integrity.json").exists()

    # It is a real constraint, not a decoration.
    _insert_label(engine, "SOME_ID Q01")
    with pytest.raises(Exception) as caught:
        _insert_label(engine, "SOME_ID Q01")
    assert "UNIQUE" in str(caught.value).upper()


def test_startup_survives_a_database_that_already_carries_duplicate_labels(
    tmp_path, monkeypatch,
):
    engine = _isolated_db(tmp_path, monkeypatch, name="dirty.db")
    app_db.Base.metadata.create_all(bind=engine)
    _insert_label(engine, "SOME_ID Q01")
    _insert_label(engine, "SOME_ID Q01")

    # init_db runs inside the FastAPI lifespan (app/main.py:42): a raise here
    # takes the product down for the very reviewer who must repair the rows,
    # and _backfill_and_normalize never runs either.
    app_db.init_db()

    assert app_db.QUESTION_LABEL_INDEX not in _indexes(engine)
    status = app_db.QUESTION_LABEL_INDEX_STATUS
    assert status["code"] == app_db.QUESTION_LABEL_DUPLICATE
    assert status["created"] is False
    assert status["duplicates"] == [
        {"question_label": "SOME_ID Q01", "rows": 2}]
    # Durably, so the finding survives the process that found it.
    recorded = json.loads(
        (tmp_path / "integrity.json").read_text(encoding="utf-8"))
    assert recorded["duplicates"] == status["duplicates"]

    # The app still works on that database: the write is still refused, by the
    # freeze-time check and the upload branch rather than by the index.
    _insert_label(engine, "SOME_ID Q01")  # not blocked by an absent index


def test_blank_labels_do_not_block_the_partial_index(tmp_path, monkeypatch):
    engine = _isolated_db(tmp_path, monkeypatch, name="blank.db")
    app_db.Base.metadata.create_all(bind=engine)
    for _ in range(3):
        _insert_label(engine, "")

    app_db.init_db()

    # Blank labels are legion (reader.py:369-370 creates a Question from a row
    # that carries a question and no label) and are not collisions with each
    # other. A plain unique index would have refused them.
    assert app_db.QUESTION_LABEL_INDEX in _indexes(engine)
    assert app_db.QUESTION_LABEL_INDEX_STATUS["created"] is True
    _insert_label(engine, "")
    with engine.connect() as conn:
        blanks = conn.exec_driver_sql(
            "SELECT COUNT(*) FROM questions WHERE question_label = ''"
        ).scalar()
    assert blanks == 4


# --------------------------------------------------------------------------- #
# The repair pass over S5's two audits. Each test below fails without the
# change it names, and each was reproduced on the shipped code first.
# --------------------------------------------------------------------------- #

def test_the_second_act_the_product_reaches_skips_quietly(db):
    """Rule G's second act as a REVIEWER performs it, with no test seam.

    ``_reopen_for_a_second_act`` clears the outer idempotency key by hand,
    which is honest but is not the route a person takes. The route a person
    takes is ``create_release(..., supersedes=v1)``: same ``release_uid``,
    version 2, the same labels. That is the only product path that reaches the
    skip branch at all, so it is the one that has to be pinned — without this,
    the branch is covered only through a door the product does not have.
    """
    label = f"SUPQ {uuid.uuid4().hex[:8]}"
    first, payload = _published(db, label=label, question="One question?")
    v1 = svc.publish_release(db, first)
    assert svc.upload_master_to_database(
        db, v1, owner_sub=OWNER)["questions_created"] == 1

    v2 = svc.create_release(
        db,
        chapter_id=_chapter_concept(db).topic.chapter_id,
        payload=copy.deepcopy(payload),
        owner_sub=OWNER,
        supersedes=v1,
    )
    assert v2.release_uid == v1.release_uid
    assert v2.version == v1.version + 1

    published = svc.publish_release(db, v2)
    again = svc.upload_master_to_database(db, published, owner_sub=OWNER)
    assert again["questions_created"] == 0
    assert again["labels_reissued"] == [label]
    assert db.query(models.Question).filter(
        models.Question.question_label == label).count() == 1
    assert [note["code"] for note in published.diagnostics["release_notes"]] \
        == [svc.QUESTION_LABEL_REISSUED]


def test_a_second_act_is_not_refused_by_a_foreign_row_that_scans_first(db):
    """The row THIS release owns wins, whatever order the scan returns.

    A database already carrying one label twice is exactly the database
    ``db._ensure_question_label_index`` declines to index rather than crash on,
    and it is the database a reviewer is in the middle of repairing. A
    first-row-wins lookup made the skip-vs-refuse verdict depend on row order
    there: with the foreign row sorting first, a LEGITIMATE re-publication of
    the owning release compared against the wrong row and refused.
    """
    from sqlalchemy import func, text

    label = f"DIRTYQ {uuid.uuid4().hex[:8]}"
    release, _ = _published(db, label=label, question="The owned question?")
    published = svc.publish_release(db, release)
    assert svc.upload_master_to_database(
        db, published, owner_sub=OWNER)["questions_created"] == 1
    owned = db.query(models.Question).filter(
        models.Question.question_label == label).one()

    db.execute(text(f"DROP INDEX IF EXISTS {app_db.QUESTION_LABEL_INDEX}"))
    db.commit()
    # An explicit id below every other row, so the unordered scan genuinely
    # returns the foreign row first rather than by luck.
    floor = int(db.query(func.min(models.Question.id)).scalar() or 1)
    foreign = models.Question(
        id=floor - 1,
        group_id=owned.group_id,
        sheet_kind="objective",
        question_label=label,
        question="A foreign row squatting on the same label.",
        route_audit={"release_uid": "SOMEONE-ELSES-RELEASE", "version": 1},
    )
    db.add(foreign)
    db.commit()
    try:
        _reopen_for_a_second_act(db, published)
        again = svc.upload_master_to_database(db, published, owner_sub=OWNER)
        assert again["questions_created"] == 0
        assert again["labels_reissued"] == [label]
    finally:
        db.delete(foreign)
        db.commit()
        db.execute(text(
            f"CREATE UNIQUE INDEX IF NOT EXISTS {app_db.QUESTION_LABEL_INDEX} "
            "ON questions(question_label) WHERE question_label <> ''"))
        db.commit()


def test_a_snapshot_frozen_before_the_freeze_check_refuses_by_name(db):
    """The one collision T5-1 cannot cover, because it predates T5-1.

    A release frozen before ``duplicate_question_labels`` existed carries
    ``payload_errors`` computed without it, so a snapshot holding one label
    twice reaches the upload loop with readiness READY.

    [measured] what followed depended on the database. WITH the partial index,
    the second insert raised and the generic ``except Exception`` turned it
    into ``upload failed and was rolled back (IntegrityError: UNIQUE constraint
    failed…)`` — safe, but naming a driver error instead of the label. WITHOUT
    the index — the database ``db._ensure_question_label_index`` declines to
    index rather than crash on, the one a reviewer is repairing — the upload
    SUCCEEDED with ``questions_created: 2`` and committed TWO rows under one
    ``question_label``. That is the T9-1 B1 identity corruption the publication
    act exists to block, written to the database.
    """
    label = f"OLDDUPQ {uuid.uuid4().hex[:8]}"

    def twin(payload):
        twin_candidate = copy.deepcopy(payload["candidates"][0])
        twin_candidate["candidate_id"] = "CAND-old-twin"
        twin_candidate["question"] = "A different question, same label."
        twin_candidate["question_text"] = twin_candidate["question"]
        payload["candidates"].append(twin_candidate)

    release, _ = _published(
        db, label=label, question="The first question.", mutate=twin)
    published = svc.publish_release(db, release)

    # Exactly the pre-T5-1 artifact: the snapshot is untouched (so its hash
    # still matches), only the freeze-time FINDING is absent.
    diagnostics = dict(published.diagnostics or {})
    diagnostics["payload_errors"] = [
        error for error in diagnostics.get("payload_errors") or []
        if "duplicate question_label" not in error
    ]
    diagnostics["readiness"] = svc.READY
    published.diagnostics = diagnostics
    published.state = "ready_for_upload"
    db.commit()

    from sqlalchemy import text

    before = db.query(models.Question).count()
    with pytest.raises(
        svc.UploadRefused, match="carry the same question label",
    ):
        svc.upload_master_to_database(db, published, owner_sub=OWNER)
    assert db.query(models.Question).count() == before

    # And on the database the index is WITHHELD from, where nothing else would
    # have stopped it. This is the branch that mattered: without the check the
    # upload returned questions_created == 2 and committed both rows.
    db.execute(text(f"DROP INDEX IF EXISTS {app_db.QUESTION_LABEL_INDEX}"))
    db.commit()
    try:
        with pytest.raises(
            svc.UploadRefused, match="carry the same question label",
        ):
            svc.upload_master_to_database(db, published, owner_sub=OWNER)
        assert db.query(models.Question).filter(
            models.Question.question_label == label).count() == 0
        assert db.query(models.Question).count() == before
    finally:
        db.execute(text(
            f"CREATE UNIQUE INDEX IF NOT EXISTS {app_db.QUESTION_LABEL_INDEX} "
            "ON questions(question_label) WHERE question_label <> ''"))
        db.commit()


def test_the_reissued_note_is_recorded_once_however_many_second_acts(db):
    """A record, not a tally. Every field of the note is constant for one
    release row, so appending a byte-identical copy per act grows the row
    without adding anything a reviewer can read."""
    label = f"NOTEQ {uuid.uuid4().hex[:8]}"
    release, _ = _published(db, label=label, question="Only one question?")
    published = svc.publish_release(db, release)
    svc.upload_master_to_database(db, published, owner_sub=OWNER)
    for _ in range(3):
        _reopen_for_a_second_act(db, published)
        svc.upload_master_to_database(db, published, owner_sub=OWNER)

    notes = [
        note for note in published.diagnostics["release_notes"]
        if note["code"] == svc.QUESTION_LABEL_REISSUED
    ]
    assert len(notes) == 1
    assert notes[0]["question_labels"] == [label]


def test_the_release_issues_projection_carries_the_reissued_note(db, client):
    """A durable note on no read path is not a record.

    The POST that performs the upload returns ``labels_reissued`` to whoever
    is standing there; this is how anyone reading the release LATER learns
    which labels the second act skipped.
    """
    label = f"PROJQ {uuid.uuid4().hex[:8]}"
    release, _ = _published(db, label=label, question="Only one question?")
    published = svc.publish_release(db, release)
    svc.upload_master_to_database(db, published, owner_sub=OWNER)
    _reopen_for_a_second_act(db, published)
    svc.upload_master_to_database(db, published, owner_sub=OWNER)

    body = client.get(
        f"/build-assessments/releases/{published.id}/issues").json()
    assert [note["code"] for note in body["release_notes"]] == [
        svc.QUESTION_LABEL_REISSUED]
    assert body["release_notes"][0]["question_labels"] == [label]


# --------------------------------------------------------------------------- #
# The max-scan: the blank family, the shared base, and the open residue.
# --------------------------------------------------------------------------- #

def test_a_blank_label_base_is_scanned_not_restarted_at_one(
    tmp_path, monkeypatch,
):
    """``machine_id_for_concept`` returns ``""`` for a concept whose topic has
    no resolvable chapter — genuine impossibility, left blank and surfaced by
    the caller, never guessed — and ``question_label`` then composes ``" Q##"``.

    Short-circuiting a blank base to 1 restarted that family on every run, so
    the second run re-minted ``" Q01"``, the partial unique index raised, and
    the ``IntegrityError`` escaped ``_generate_session``'s uncontained
    ``db.commit()``: the whole batch of generated questions lost with no
    record. A mid-run halt Q13 forbids, and an R4 loss inside the slice that
    exists to close one. The blank family is a family; it is scanned.
    """
    engine = _isolated_db(tmp_path, monkeypatch, name="blankbase.db")
    app_db.init_db()
    session = sessionmaker(bind=engine)()
    try:
        assert identity.label_family_prefix("") == identity.LABEL_INFIX
        assert generation.question_label(None, 1) == " Q01"
        assert identity.next_label_index(session, "") == 1

        _insert_label(engine, " Q01")
        assert identity.next_label_index(session, "") == 2
        assert generation.question_label(None, 2) == " Q02"
        # The index is present and real on this database, so a restart would
        # have raised here rather than merely renumbering.
        _insert_label(engine, " Q02")
        assert identity.next_label_index(session, "") == 3
    finally:
        session.close()


def test_two_concepts_sharing_one_label_base_do_not_mint_one_label_twice(
    db, client,
):
    """The counter is keyed by the BASE, not by the concept id.

    The label family IS the base, so two concepts that resolve to one base
    must share one counter. Two concepts DO share a base whenever both carry a
    blank ``machine_id``, and on a database that already carries a duplicate
    persisted ``machine_id`` — the T9-1 B1 defect the publication act blocks
    and which the generation run must therefore survive rather than crash on.
    Keyed per concept, both counters started at the same number and the second
    insert met the unique index inside a single run.
    """
    first = _fresh_concept(db)
    second = _fresh_concept(db)
    shared = f"S5SHARED{uuid.uuid4().hex[:8]}"
    first.machine_id = shared
    second.machine_id = shared
    db.commit()

    _generate_over(client, [first.id, second.id], count=1)
    db.expire_all()

    prefix = identity.label_family_prefix(shared)
    minted = sorted(
        str(row.question_label)
        for row in db.query(models.Question).all()
        if str(row.question_label or "").startswith(prefix)
    )
    assert minted == [f"{shared} Q01", f"{shared} Q02"]
    assert identity.next_label_index(db, shared) == 3

    # The manufactured corruption stays scoped to THIS test: since S11 the
    # publication act refuses a chapter carrying a duplicate persisted
    # ``machine_id`` (T9-1 B1 — the very gate the docstring cites), so the
    # shared fixture chapter must not keep the defect once the label
    # property is proven.
    db.get(models.Concept, second.id).machine_id = f"{shared}B"
    db.commit()


@pytest.mark.xfail(
    strict=True,
    reason=(
        "OPEN RESIDUE, named rather than implied. T5-3's max-scan reads LIVE "
        "rows, so it reissues the TOP of a range: delete the highest label of "
        "a family and the next mint takes that number back. R5 is therefore "
        "held for every interior deletion and NOT for the top of a range. "
        "Closing it needs a durable record of retired labels — the uploaded "
        "release snapshots are the obvious one — which no slice up to S5 "
        "builds; it belongs with the converged publication (S10) and the "
        "release-audit surface (step 9). This case is pinned so that "
        "test_deleting_a_question_does_not_reissue_its_number, which deletes "
        "an INTERIOR row, cannot be read as the general guarantee."
    ),
)
def test_deleting_the_highest_label_of_a_family_does_not_reissue_its_number(
    db, client,
):
    concept = _fresh_concept(db)
    concept_id = concept.id
    _generate(client, concept_id, count=2)
    db.expire_all()
    concept = db.get(models.Concept, concept_id)
    base, labels = _labels_for(db, concept)
    assert labels == [f"{base} Q01", f"{base} Q02"]

    doomed = db.query(models.Question).filter(
        models.Question.question_label == f"{base} Q02").one()
    db.delete(doomed)
    db.commit()

    _generate(client, concept_id, count=1)
    db.expire_all()
    concept = db.get(models.Concept, concept_id)
    _, after = _labels_for(db, concept)
    assert after == [f"{base} Q01", f"{base} Q03"]


# --------------------------------------------------------------------------- #
# T5-4 — "never halt startup" held against the table's SHAPE, not only its
# contents.
# --------------------------------------------------------------------------- #

def test_the_index_scan_survives_a_questions_table_with_no_label_column(
    tmp_path, monkeypatch,
):
    """The constraint is stated absolutely, so it is tested absolutely.

    ``question_label`` is an original ``NOT NULL`` model column and is not in
    the ``additions`` list, so no database the product has written lacks it —
    which is precisely why an unchecked assumption about it would be
    discovered by a reviewer who can no longer boot the app to look. The
    column is checked, not assumed.
    """
    engine = _isolated_db(tmp_path, monkeypatch, name="noshape.db")
    app_db.Base.metadata.create_all(bind=engine)
    with engine.connect() as conn:
        conn.exec_driver_sql(
            f"DROP INDEX IF EXISTS {app_db.QUESTION_LABEL_INDEX}")
        conn.exec_driver_sql("DROP INDEX IF EXISTS ix_questions_question_label")
        conn.exec_driver_sql(
            "ALTER TABLE questions DROP COLUMN question_label")
        conn.commit()

    app_db._ensure_columns()

    assert app_db.QUESTION_LABEL_INDEX not in _indexes(engine)
    status = app_db.QUESTION_LABEL_INDEX_STATUS
    assert status["created"] is False
    assert status["code"] == "question_label_column_absent"

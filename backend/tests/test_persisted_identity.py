"""S4 — identity is MINTED ONCE and PERSISTED; it is never re-derived.

Before this slice a concept's machine id came off the RELEASE HASH
(``assessment_release_snapshot``: ``f"REL{release_sha[:12].upper()}C###"``), so
editing one character of any staged content re-minted every id — and with it
every ``group_key`` and every ``question_label`` built off it. §6:523 requires
labels "unique and stable forever"; those two cannot both be true, which is why
"stable forever" is a property of STORAGE (P-C1) and not of any formula.

The four legs of the ONE round trip land together, and each has a test here:
the writer EXPORTS the tag into the cell, publication STORES it where
``source_order`` is already assigned, the question-label MINTER becomes the
single source of ``<ConceptID> Q##``, and the reader RESTORES it from the
stripped title tag on import.
"""
from __future__ import annotations

import copy
import subprocess
import sys
from pathlib import Path

import openpyxl

from app import bulk_import as bi
from app import models
from app.bulk_import import reader, writer
from app.db import _backfill_and_normalize
from app.services import assessment_grouping as grouping
from app.services import assessment_release_snapshot as release_snapshot
from app.services import build_concepts_release
from app.services import build_concepts_release_files as release_files
from app.services import build_concepts_release_publication as publication
from app.services import directory, generation, identity

OWNER = "local:default"


# --------------------------------------------------------------------------- #
# Fixtures — plain rows, no model call anywhere in this file.
# --------------------------------------------------------------------------- #

def _chapter(db, *, title="Light and Shadows", grade="06", board="CBSE",
             subject="Science", code="06CBSC_Light"):
    chapter = models.Chapter(
        chapter_code=code, board=board, grade=grade, subject=subject,
        unit=f"{subject} Unit", chapter_title=title,
        chapter_display_name=title, chapter_description="",
        chapter_duration="",
    )
    db.add(chapter)
    db.flush()
    return chapter


def _topic(db, chapter, title, *, lane="Post", source_order=0):
    topic = models.Topic(
        chapter_id=chapter.id, topic_title=title, topic_display_name=title,
        pre_post_learning=lane, topic_description="",
        source_order=source_order,
    )
    db.add(topic)
    db.flush()
    return topic


def _concept(db, topic, title, *, details="Details.", source_order=0):
    concept = models.Concept(
        topic_id=topic.id, concept_title=title, concept_display_name=title,
        concept_details=details, source_order=source_order,
    )
    db.add(concept)
    db.flush()
    return concept


def _job(db, chapter, records) -> models.UploadJob:
    inventory = {"items": [{
        "qid": "QINV-0001", "source_kind": "exercise",
        "source_label": "Exercise 1(1)", "raw_task": "Name one shadow.",
    }]}
    job = models.UploadJob(
        owner_sub=OWNER, module="build_concepts", upload_type="textbook",
        filename="identity.mmd", mmd_text="# Chapter\n\nExercise 1. Name one.",
        status="generated", deposit_scope_type="chapter",
        deposit_scope_ids=[chapter.id], question_inventory=inventory,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    build_concepts_release.stage_release(
        db, job, target_chapter_id=chapter.id, records=list(records),
        inventory=inventory, reason="S4 identity fixture",
    )
    return job


# --------------------------------------------------------------------------- #
# 1. Persistence, not derivation
# --------------------------------------------------------------------------- #

def test_identity_is_persisted_not_rederived(db):
    """A rename may not move the id, and the cell shows the new name beside it.

    §7:577 permits rename/merge/split/re-tag. No formula over a title survives
    that, which is exactly why the id is stored rather than computed.
    """
    chapter = _chapter(db, title="Persisted Identity Chapter",
                       code="06CBSC_Persisted")
    topic = _topic(db, chapter, "Shadows", source_order=1)
    concept = _concept(db, topic, "Umbra", source_order=1)
    db.commit()

    minted = identity.machine_id_for_concept(concept)
    db.commit()
    assert minted
    assert concept.machine_id == minted

    # Move EVERY input any live or retired formula ever read: the concept
    # title (the old ``generation.question_label`` slugged it), the chapter
    # title (the chapter key slugs it and hashes it), and the row's position
    # (a topic inserted ahead of it). A derived id moves on any one of these.
    concept.concept_title = "Umbra and Penumbra, Renamed Entirely"
    concept.concept_display_name = concept.concept_title
    concept.concept_details = "Rewritten teaching content."
    chapter.chapter_title = "Persisted Identity Chapter, Retitled"
    chapter.chapter_display_name = chapter.chapter_title
    inserted = _topic(db, chapter, "Inserted Ahead", source_order=1)
    topic.source_order = 2
    _concept(db, topic, "Inserted Concept Ahead", source_order=1)
    concept.source_order = 2
    db.commit()

    assert identity.machine_id_for_topic(inserted) != topic.machine_id
    assert identity.machine_id_for_concept(concept) == minted
    db.commit()
    assert concept.machine_id == minted
    assert writer._concept_field_value(
        concept, topic, "concept_title",
        include_group_columns=False,
    ) == f"Umbra and Penumbra, Renamed Entirely ({minted})"


def test_release_hash_no_longer_reaches_identity(db):
    """Flip one character of staged content: the seal moves, the ids do not."""
    chapter = _chapter(db, title="Hash Free Identity",
                       code="06CBSC_HashFree")
    db.commit()
    records = [
        {"topic": "Shapes", "concept_title": "Solid Shapes",
         "concept_details": "Solid shapes occupy space.", "keywords": "solid"},
        {"topic": "Shapes", "concept_title": "Plane Shapes",
         "concept_details": "Plane shapes are flat.", "keywords": "plane"},
    ]
    job = _job(db, chapter, records)

    before = build_concepts_release.release_payload(job)
    snapshot_before = release_snapshot.build(db, job, before)
    sha_before = release_snapshot.source_release_sha256(before)

    edited = copy.deepcopy(records)
    edited[0]["concept_details"] = "Solid shapes occupy spacE."
    build_concepts_release.stage_release(
        db, job, target_chapter_id=chapter.id, records=edited,
        inventory=job.question_inventory, reason="one character",
    )
    after = build_concepts_release.release_payload(job)
    snapshot_after = release_snapshot.build(db, job, after)
    sha_after = release_snapshot.source_release_sha256(after)

    assert sha_after != sha_before, "the seal must still cover the content"

    def _ids(snapshot):
        return [
            row["concept_machine_id"]
            for topic in snapshot["snapshot"]["topics"]
            for row in topic["concepts"]
        ]

    ids_before, ids_after = _ids(snapshot_before), _ids(snapshot_after)
    assert ids_before == ids_after
    assert all(ids_before)
    # The two identities built off it move with it, so pinning the base pins
    # the whole family (D2/Q15 keeps the space separator).
    assert [grouping.group_key_for(i, "Basic", 1) for i in ids_before] == [
        grouping.group_key_for(i, "Basic", 1) for i in ids_after]
    assert [f"{i} Q01" for i in ids_before] == [
        f"{i} Q01" for i in ids_after]


# --------------------------------------------------------------------------- #
# 2. Uniqueness, alphabet and column width
# --------------------------------------------------------------------------- #

def test_two_chapters_whose_titles_share_no_ascii_mint_distinct_ids(db):
    """Balbharati prints in Marathi; a name-derived id is not unique for it.

    [measured, before S4] ``directory.concept_tag`` gave BOTH of these
    ``06MSSC_X_PL_X`` because ``_underscore_slug`` keeps only ``[A-Za-z0-9]+``
    and falls back to ``"X"``. ``h8`` is what carries the uniqueness now.
    """
    minted = []
    for title in ("सजीवांची वैशिष्ट्ये", "पदार्थ आणि पदार्थांचे गुणधर्म"):
        chapter = _chapter(db, title=title, board="Maharashtra", grade="06",
                           subject="Science", code="06MSSC_X")
        topic = _topic(db, chapter, "वाढ आणि प्रजनन", source_order=1)
        concept = _concept(db, topic, "वाढ", source_order=1)
        db.commit()
        minted.append(identity.machine_id_for_concept(concept))
    db.commit()

    assert len(set(minted)) == 2, minted
    assert all("_X_" in value for value in minted), (
        "the readable slug is expected to collapse; the hash is what "
        "separates the two chapters"
    )


def test_every_minted_tag_round_trips_strip_title_tag(db):
    """A tag that does not round-trip is a tag the importer cannot match.

    ``bi._TITLE_TAG_RE`` is ``[A-Za-z0-9]+(?:_[A-Za-z0-9]+)+`` and is consumed
    at every import/deposit/read-back boundary; [measured] a single hyphen
    makes ``strip_title_tag`` return the string unchanged.
    """
    corpus = [
        ("Light and Shadows", "Umbra"),
        ("सजीवांची वैशिष्ट्ये", "वाढ आणि प्रजनन"),
        ("三角形", "内角"),
        ("", ""),
        ("Types of Angles and Their Measurement in Plane Geometry",
         "Complete Angle"),
    ]
    for chapter_title, concept_title in corpus:
        chapter = _chapter(db, title=chapter_title, code="06CBSC_RT")
        topic = _topic(db, chapter, concept_title or "Topic", source_order=1)
        concept = _concept(db, topic, concept_title or "Concept",
                           source_order=1)
        db.commit()
        for value in (identity.machine_id_for_topic(topic),
                      identity.machine_id_for_concept(concept)):
            assert value
            cell = identity.titled(concept_title or "Name", value)
            assert bi.strip_title_tag(cell) == (concept_title or "Name")
            assert identity.title_tag(cell) == value
        db.commit()


def test_two_topics_sharing_a_concept_name_get_distinct_ids(db):
    chapter = _chapter(db, title="Shared Names", code="06CBSC_Shared")
    first = _topic(db, chapter, "Reflection", source_order=1)
    second = _topic(db, chapter, "Refraction", source_order=2)
    a = _concept(db, first, "Normal Line", source_order=1)
    b = _concept(db, second, "Normal Line", source_order=1)
    db.commit()

    assert identity.machine_id_for_concept(a) != (
        identity.machine_id_for_concept(b))
    db.commit()
    assert a.machine_id.endswith("_T01_C01")
    assert b.machine_id.endswith("_T02_C01")


def test_question_label_stays_within_its_column(db):
    """``models.Question.question_label`` is ``String(128)``.

    [measured] sqlite silently ignores VARCHAR length, so an overrun is
    invisible today and fatal on any Postgres move. The map's name-bearing
    option produced a 130-character label on the longest real gold row.
    """
    chapter = _chapter(
        db,
        title="Types of Angles and Their Measurement in Plane Geometry",
        board="Maharashtra", grade="06", subject="Social Science",
        code="06MSSS_Types",
    )
    topic = _topic(db, chapter, "Complete and Reflex Angles", lane="Pre",
                   source_order=99)
    concept = _concept(db, topic, "The Complete Angle of Three Hundred and "
                       "Sixty Degrees", source_order=99)
    db.commit()
    label = generation.question_label(concept, 99)
    db.commit()
    column = models.Question.__table__.c.question_label.type.length
    assert column == 128
    assert len(label) <= column, (label, len(label))
    assert len(concept.machine_id) <= (
        models.Concept.__table__.c.machine_id.type.length)


# --------------------------------------------------------------------------- #
# 3. Grade normalisation (T4-2)
# --------------------------------------------------------------------------- #

def test_grade_string_variants_mint_one_identity(db):
    """[measured, before S4] one chapter minted six grade families.

    ``code_prefix`` yielded ``6CBSC`` / ``06CBSC`` / ``VICBSC`` / ``' 6 CBSC'``
    for one class.
    """
    keys = set()
    for raw in ("6", "06", "VI", " 6 ", "६"):
        chapter = models.Chapter(
            chapter_code="c", board="CBSE", grade=raw, subject="Science",
            chapter_title="Light",
        )
        chapter.id = 4242
        keys.add(identity.chapter_key(chapter))
        assert directory.code_prefix("CBSE", raw, "Science") == "06CBSC"
        assert identity.grade_review_flag(chapter) == ""
    assert len(keys) == 1, keys


def test_kg_and_nursery_do_not_collapse_into_one_family(db):
    """Normalising KG and Nursery to ``00`` is CLAUDE.md:48-50's failure mode.

    ``text_normalize.normalize_grade`` returns ``''`` for both. Stamping
    ``00`` would put the two youngest cohorts in one identity family — and
    lower grades are exactly where volume-blind shortcuts bite hardest.
    """
    keys = {}
    for raw in ("KG", "Nursery", "Class VI"):
        chapter = models.Chapter(
            chapter_code="c", board="CBSE", grade=raw, subject="Science",
            chapter_title="Light",
        )
        chapter.id = 4242
        keys[raw] = identity.chapter_key(chapter)
        flag = identity.grade_review_flag(chapter)
        assert "did not parse" in flag and repr(raw) in flag, flag
        assert not identity.chapter_key(chapter).startswith("00"), raw
    assert len(set(keys.values())) == 3, keys


# --------------------------------------------------------------------------- #
# 4. One minter (T4-7)
# --------------------------------------------------------------------------- #

def test_question_label_is_minted_by_exactly_one_function(db):
    chapter = _chapter(db, title="One Minter", code="06CBSC_OneMinter")
    topic = _topic(db, chapter, "Sound", source_order=1)
    concept = _concept(db, topic, "Pitch", source_order=1)
    db.commit()

    assert generation.question_label(concept, 3) == (
        f"{identity.machine_id_for_concept(concept)} Q03")
    db.commit()
    assert generation.question_label(concept, 3) == f"{concept.machine_id} Q03"
    assert not hasattr(generation, "_topic_index")
    from app.services import build_assessments
    assert not hasattr(build_assessments, "_legacy_machine_id")


def test_group_key_reads_the_persisted_machine_id(db):
    """``Group.group_key`` is persisted, so a re-derived base splits families.

    [measured, before S4] ``tagging.py`` and ``build_assessments`` both fed
    ``question_label(concept, 1).rsplit(" Q", 1)[0]`` into ``group_key_for``.
    """
    from app.services import tagging

    chapter = _chapter(db, title="Group Keys", code="06CBSC_GroupKeys")
    topic = _topic(db, chapter, "Heat", source_order=1)
    concept = _concept(db, topic, "Conduction", source_order=1)
    db.commit()

    group = tagging._group_of_type(db, concept, "Basic")
    db.commit()
    assert group.group_key == grouping.group_key_for(
        concept.machine_id, "Basic", 1)

    concept.concept_title = "Conduction, Renamed"
    db.commit()
    second = tagging._group_of_type(db, concept, "Intermediate")
    db.commit()
    assert second.group_key == grouping.group_key_for(
        concept.machine_id, "Intermediate", 1)
    assert second.group_key.startswith(f"({group.group_key[1:].split(')')[0]})")


# --------------------------------------------------------------------------- #
# 5. The backfill (T4-8)
# --------------------------------------------------------------------------- #

def test_a_legacy_row_with_source_order_zero_backfills_deterministically(db):
    """Two ``source_order == 0`` topics, ids 7 and 3: T01 is id 3, T02 is id 7.

    A legacy row nobody positioned sorts after every positioned sibling, in
    creation-id order — derived from storage, never from content.
    """
    chapter = _chapter(db, title="Backfill Chapter", code="06CBSC_Backfill")
    later = _topic(db, chapter, "Created First", source_order=0)
    earlier = _topic(db, chapter, "Created Second", source_order=0)
    db.commit()
    assert earlier.id > later.id
    assert later.machine_id == "" and earlier.machine_id == ""

    _backfill_and_normalize()
    db.expire_all()
    assert later.machine_id.endswith("_PL_T01")
    assert earlier.machine_id.endswith("_PL_T02")

    frozen = (later.machine_id, earlier.machine_id)
    _backfill_and_normalize()
    db.expire_all()
    assert (later.machine_id, earlier.machine_id) == frozen, (
        "the backfill is idempotent and never overwrites"
    )


def test_a_positioned_row_sorts_before_every_source_order_zero_row(db):
    chapter = _chapter(db, title="Ordering Chapter", code="06CBSC_Ordering")
    legacy = _topic(db, chapter, "Legacy Unpositioned", source_order=0)
    positioned = _topic(db, chapter, "Positioned Last", source_order=1)
    db.commit()
    assert positioned.id > legacy.id

    assert identity.source_order_key(positioned) < (
        identity.source_order_key(legacy))
    _backfill_and_normalize()
    db.expire_all()
    assert positioned.machine_id.endswith("_PL_T01")
    assert legacy.machine_id.endswith("_PL_T02")


# --------------------------------------------------------------------------- #
# 6. The module graph (T4-9(b))
# --------------------------------------------------------------------------- #

def test_identity_module_imports_no_bulk_import_writer():
    """The cheapest possible pin against the cycle being reintroduced.

    ``bulk_import.writer`` CALLS ``services.identity``; if ``identity`` also
    reached into the writer for the ordering key the two would be a direct
    two-module cycle. The direction is one-way, and a fresh subprocess is the
    only honest way to assert it (an in-process check sees every module the
    test session already imported).
    """
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-c",
         "import sys; import app.services.identity as m; "
         "print('app.bulk_import.writer' in sys.modules)"],
        cwd=str(root), capture_output=True, text=True, check=True,
    )
    assert result.stdout.strip() == "False", result.stdout + result.stderr


def test_source_order_key_has_exactly_one_definition():
    assert not hasattr(writer, "_source_order_key"), (
        "no alias is left behind: an alias is a second name for one "
        "identity, and that is how four topic identities happened"
    )
    assert release_files.identity.source_order_key is (
        identity.source_order_key)
    assert identity.source_order_key(
        models.Topic(source_order=0, id=7)) == (10**9, 7)
    assert identity.source_order_key(
        models.Topic(source_order=4, id=7)) == (4, 7)


# --------------------------------------------------------------------------- #
# 7. The round trip through the workbook (legs a and d)
# --------------------------------------------------------------------------- #

def _one_row_workbook(tmp_path, name, *, topic_cell, concept_cell,
                      chapter_cell):
    """A canonical-layout workbook carrying exactly one concept row.

    Built through the layout registry, by NAME, exactly as S2 requires the
    reader to read it — the fixture cannot drift from the header the reader
    identifies against.
    """
    from app.bulk_import import layouts

    layout = layouts.layout("canonical-current")
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for kind, sheet_layout in layout.sheets.items():
        ws = wb.create_sheet(sheet_layout.sheet_name)
        ws.append([band.label for band in sheet_layout.bands])
        ws.append(list(sheet_layout.fields))
        if kind != "objective":
            continue
        row = [""] * len(sheet_layout.fields)
        for block, field, value in (
            ("chapter", "chapter_title", chapter_cell),
            ("chapter", "chapter_display_name", "Round Trip"),
            ("topic", "topic_title", topic_cell),
            ("topic", "topic_display_name", "Reflection"),
            ("topic", "pre_post_learning", "Post"),
            ("concept", "concept_title", concept_cell),
            ("concept", "concept_display_name", "Mirror"),
        ):
            index = sheet_layout.column(block, field)
            assert index is not None, (block, field)
            row[index] = value
        ws.append(row)
    path = tmp_path / name
    wb.save(path)
    wb.close()
    return path


def test_import_restores_the_minted_id_from_the_title_tag(db, tmp_path):
    """The endpoint that STRIPPED the tag now keeps it.

    Without this leg the one authenticated POST S2 hardened is also the one
    that erases the identity S4 mints.
    """
    topic_id = "06CBSC_RoundTrip_aa11bb22_PL_T01"
    concept_id = f"{topic_id}_C01"
    path = _one_row_workbook(
        tmp_path, "round_trip.xlsx",
        topic_cell=f"Topic 01: Reflection ({topic_id})",
        concept_cell=f"Mirror ({concept_id})",
        chapter_cell="Round Trip (06_Science_CBSE)",
    )
    counts = reader.import_workbook(db, path)
    db.commit()

    topic = db.query(models.Topic).filter_by(machine_id=topic_id).one()
    concept = db.query(models.Concept).filter_by(machine_id=concept_id).one()
    assert topic.topic_title == "Reflection"
    assert concept.concept_title == "Mirror"
    assert not [i for i in counts["issues"] if "machine id" in i]
    # And the id survives a second read of the same file unchanged.
    reader.import_workbook(db, path)
    db.commit()
    assert concept.machine_id == concept_id


def test_an_imported_id_never_overwrites_a_persisted_one(db, tmp_path):
    """An uploaded file may not re-key a published row; the disagreement is
    recorded and the row still imports (R4)."""
    topic_id = "06CBSC_Conflict_cc33dd44_PL_T01"
    concept_id = f"{topic_id}_C01"
    path = _one_row_workbook(
        tmp_path, "conflict_one.xlsx",
        topic_cell=f"Topic 01: Reflection ({topic_id})",
        concept_cell=f"Mirror ({concept_id})",
        chapter_cell="Conflict (06_Science_CBSE)",
    )
    reader.import_workbook(db, path)
    db.commit()
    concept = db.query(models.Concept).filter_by(machine_id=concept_id).one()

    other = "06CBSC_Conflict_cc33dd44_PL_T09_C09"
    path2 = _one_row_workbook(
        tmp_path, "conflict_two.xlsx",
        topic_cell=f"Topic 01: Reflection ({topic_id})",
        concept_cell=f"Mirror ({other})",
        chapter_cell="Conflict (06_Science_CBSE)",
    )
    counts = reader.import_workbook(db, path2)
    db.commit()

    assert concept.machine_id == concept_id, "the persisted row's id wins"
    conflicts = [i for i in counts["issues"] if "machine_id_conflict" in i]
    assert len(conflicts) == 1, counts["issues"]
    assert other in conflicts[0] and concept_id in conflicts[0]
    assert db.query(models.Concept).filter_by(machine_id=other).first() is None


def test_a_workbook_with_no_tag_imports_with_a_note_and_mints_on_first_use(
    db, tmp_path,
):
    path = _one_row_workbook(
        tmp_path, "tagless.xlsx",
        topic_cell="Topic 01: Reflection",
        concept_cell="Mirror",
        chapter_cell="Tagless (06_Science_CBSE)",
    )
    counts = reader.import_workbook(db, path)
    db.commit()

    concept = (
        db.query(models.Concept).join(models.Topic).join(models.Chapter)
        .filter(models.Chapter.chapter_title == "Tagless")
        .filter(models.Concept.concept_title == "Mirror").one()
    )
    assert concept.machine_id == ""
    notes = [
        issue for issue in counts["issues"]
        if "imported_without_machine_id" in issue
    ]
    assert [n for n in notes if n.startswith("concept ")], counts["issues"]
    assert [n for n in notes if n.startswith("topic ")], counts["issues"]

    minted = identity.machine_id_for_concept(concept)
    db.commit()
    assert minted and concept.machine_id == minted
    assert generation.question_label(concept, 1) == f"{minted} Q01"


# --------------------------------------------------------------------------- #
# 8. Leg b — publication stores the id where source_order is assigned (T4-5)
# --------------------------------------------------------------------------- #

def test_source_order_agrees_between_export_and_publication(db):
    """[measured, before S4] the export set ``source_order`` to the
    CHAPTER-WIDE record index while publication used the PER-TOPIC position,
    so any ``C##`` read off it meant two different things on the two sides of
    one publication."""
    chapter = _chapter(db, title="Agreeing Order", code="06CBSC_Agree")
    db.commit()
    records = [
        {"topic": "Alpha", "concept_title": "A One",
         "concept_details": "First concept of Alpha."},
        {"topic": "Alpha", "concept_title": "A Two",
         "concept_details": "Second concept of Alpha."},
        {"topic": "Beta", "concept_title": "B One",
         "concept_details": "First concept of Beta."},
    ]
    job = _job(db, chapter, records)
    payload = build_concepts_release.release_payload(job)

    _t_chapter, staged, _records, _defects = (
        release_files.transient_release_hierarchy(db, job, payload=payload))
    staged_orders = [c.source_order for c in staged]
    staged_ids = [c.machine_id for c in staged]
    assert staged_orders == [1, 2, 1], staged_orders

    publication.upload_release_to_database(db, job.id, owner_sub=OWNER)
    db.commit()
    db.expire_all()

    published = {
        (c.topic.topic_title, c.concept_title): c
        for topic in db.get(models.Chapter, chapter.id).topics
        for c in topic.concepts
    }
    assert [
        published[("Alpha", "A One")].source_order,
        published[("Alpha", "A Two")].source_order,
        published[("Beta", "B One")].source_order,
    ] == staged_orders
    assert [
        published[("Alpha", "A One")].machine_id,
        published[("Alpha", "A Two")].machine_id,
        published[("Beta", "B One")].machine_id,
    ] == staged_ids, (
        "the staged id and the published id are the same string, or the "
        "workbook carries a tag the importer cannot match"
    )
    assert len(set(staged_ids)) == 3


def test_a_published_id_is_not_re_keyed_by_a_second_publication(db):
    chapter = _chapter(db, title="Republished", code="06CBSC_Republish")
    db.commit()
    records = [{"topic": "Alpha", "concept_title": "A One",
                "concept_details": "First concept of Alpha."}]
    job = _job(db, chapter, records)
    publication.upload_release_to_database(db, job.id, owner_sub=OWNER)
    db.commit()
    db.expire_all()
    concept = (
        db.query(models.Concept).join(models.Topic)
        .filter(models.Topic.chapter_id == chapter.id).one()
    )
    first = concept.machine_id
    assert first

    second_job = _job(db, chapter, [
        {"topic": "Zeroth", "concept_title": "Z One",
         "concept_details": "A topic that now precedes Alpha."},
        {"topic": "Alpha", "concept_title": "A One",
         "concept_details": "First concept of Alpha, edited."},
    ])
    publication.upload_release_to_database(db, second_job.id, owner_sub=OWNER)
    db.commit()
    db.expire_all()
    assert concept.machine_id == first, (
        "§6:523's 'stable forever' is a property of storage: a republication "
        "that renumbers the topology may not re-key a published concept"
    )



# --------------------------------------------------------------------------- #
# 9. ONE ROW, ONE IDENTITY — the property everything above rests on.
#
# A position is where a row sits TODAY; an id is what it was minted as ONCE.
# Composing an id straight off the position hands the SAME string to two rows
# the moment anything is inserted ahead of a minted sibling, and two rows with
# one machine_id is two concepts with one ``question_label`` —
# ``assessment_release_service:612`` then skips the second concept's questions
# with no flag, which is the exact R4 loss ``generation._topic_index``'s
# docstring existed to prevent and this slice deleted that docstring over.
# --------------------------------------------------------------------------- #

def _ids_in(db, chapter) -> list[str]:
    topics = db.query(models.Topic).filter_by(chapter_id=chapter.id).all()
    concepts = [c for t in topics for c in t.concepts]
    return (
        [t.machine_id for t in topics] + [c.machine_id for c in concepts]
    )


def test_a_second_publication_that_prepends_a_topic_mints_no_duplicate(db):
    """[measured, before the fix] two ordinary ``upload_release_to_database``
    calls on one chapter, the second prepending one topic, gave BOTH topics
    ``…_PL_T01``, both concepts ``…_PL_T01_C01`` and both questions
    ``…_PL_T01_C01 Q01``."""
    chapter = _chapter(db, title="Duplicate Guard", code="06CBSC_DupGuard")
    db.commit()
    publication.upload_release_to_database(
        db,
        _job(db, chapter, [{"topic": "Alpha", "concept_title": "A One",
                            "concept_details": "First concept of Alpha."}]).id,
        owner_sub=OWNER,
    )
    db.commit()
    publication.upload_release_to_database(
        db,
        _job(db, chapter, [
            {"topic": "Zeroth", "concept_title": "Z One",
             "concept_details": "A topic that now precedes Alpha."},
            {"topic": "Alpha", "concept_title": "A One",
             "concept_details": "First concept of Alpha, edited."},
        ]).id,
        owner_sub=OWNER,
    )
    db.commit()
    db.expire_all()

    ids = _ids_in(db, chapter)
    assert len(ids) == len(set(ids)), f"duplicate machine ids: {ids}"
    assert all(ids), "no row may be left without an identity"
    labels = [
        generation.question_label(concept, 1)
        for topic in db.query(models.Topic).filter_by(
            chapter_id=chapter.id).all()
        for concept in topic.concepts
    ]
    assert len(labels) == len(set(labels)), labels


def test_a_row_inserted_ahead_of_a_minted_sibling_takes_a_free_slot(db):
    """The same defect on the direct ``machine_id_for_*`` path."""
    chapter = _chapter(db, title="Free Slot", code="06CBSC_FreeSlot")
    first = _topic(db, chapter, "Alpha", source_order=1)
    db.flush()
    db.refresh(chapter)
    minted_first = identity.machine_id_for_topic(first)
    concept_first = _concept(db, first, "A One", source_order=1)
    db.flush()
    identity.machine_id_for_concept(concept_first)
    db.commit()

    first.source_order = 2
    inserted = _topic(db, chapter, "Zeroth", source_order=1)
    db.flush()
    db.refresh(chapter)
    minted_inserted = identity.machine_id_for_topic(inserted)
    db.commit()

    assert minted_first.endswith("_PL_T01")
    assert minted_inserted != minted_first
    # And a second concept under the SAME topic, inserted ahead of C01.
    concept_first.source_order = 2
    second = _concept(db, first, "A Zero", source_order=1)
    db.flush()
    db.refresh(first)
    assert identity.machine_id_for_concept(second) != concept_first.machine_id


def test_lane_case_variants_do_not_mint_one_identity_for_two_topics(db):
    """``reader`` takes ``pre_post_learning`` VERBATIM from a workbook cell
    (``reader.py``: ``top.get("pre_post_learning", "") or "Post"``) and already
    records a ``topic_lane_conflict`` for this state, so "Post" and "post" in
    one chapter is reachable through the endpoint S2 hardened. [measured,
    before the fix] the two landed in two position groups that both numbered
    from 1 while both resolving to ``PL``: one identity, two rows."""
    chapter = _chapter(db, title="Lane Case", code="06CBSC_LaneCase")
    upper = _topic(db, chapter, "Cells", lane="Post", source_order=1)
    lower = _topic(db, chapter, "Tissues", lane="post", source_order=2)
    db.flush()
    db.refresh(chapter)
    assert identity.machine_id_for_topic(upper) != (
        identity.machine_id_for_topic(lower))

    # And through the startup backfill, which grouped on the raw string too.
    third = _topic(db, chapter, "Organs", lane="POST", source_order=3)
    db.commit()
    _backfill_and_normalize()
    db.expire_all()
    ids = [upper.machine_id, lower.machine_id, third.machine_id]
    assert all(ids) and len(set(ids)) == 3, ids


def test_a_chapter_level_legacy_tag_is_not_restored_as_an_identity(db):
    """The owner's own reference workbook is a duplicate-identity generator
    under an unparsed restore.

    ``directory.topic_tag`` is CHAPTER-level — one tag on every topic of the
    chapter — and that is what the committed reference books carry.
    [measured, before the fix] ``grade6_science.xlsx`` imported 8 topics onto
    2 distinct machine ids, silently: 6 duplicates, no note.
    """
    from app.bulk_import import layouts

    path = layouts.REFERENCE_WORKBOOK_PATH.parent / "grade6_science.xlsx"
    before = {topic.id for topic in db.query(models.Topic).all()}
    counts = reader.import_workbook(db, path)
    db.commit()
    notes = [
        issue for issue in counts["issues"]
        if "(imported_without_machine_id)" in issue
    ]
    assert notes, "a rejected tag is RECORDED, never silently dropped"

    imported = [
        topic for topic in db.query(models.Topic).all()
        if topic.id not in before
    ]
    assert imported
    for topic in imported:
        identity.machine_id_for_topic(topic)
    db.flush()
    minted = [topic.machine_id for topic in imported]
    for topic in imported:
        for concept in topic.concepts:
            minted.append(identity.machine_id_for_concept(concept))
    db.commit()
    assert all(minted), "every row is minted on first use"
    assert len(minted) == len(set(minted)), "one row, one identity"


def test_an_uploaded_id_another_row_already_holds_is_refused_and_recorded(
    db, tmp_path,
):
    """One file, two chapters would otherwise hand both rows one id."""
    topic_id = "06CBSC_Claimed_1234abcd_PL_T01"
    concept_id = f"{topic_id}_C01"
    first = _one_row_workbook(
        tmp_path, "claimed_one.xlsx",
        topic_cell=f"Topic 01: Reflection ({topic_id})",
        concept_cell=f"Mirror ({concept_id})",
        chapter_cell="Claim One (06_Science_CBSE)",
    )
    second = _one_row_workbook(
        tmp_path, "claimed_two.xlsx",
        topic_cell=f"Topic 01: Refraction ({topic_id})",
        concept_cell=f"Lens ({concept_id})",
        chapter_cell="Claim Two (06_Science_CBSE)",
    )
    reader.import_workbook(db, first)
    db.commit()
    counts = reader.import_workbook(db, second)
    db.commit()

    assert [
        issue for issue in counts["issues"]
        if "(machine_id_already_claimed)" in issue
    ], counts["issues"]
    held = db.query(models.Topic).filter_by(machine_id=topic_id).all()
    assert len(held) == 1
    second_chapter = db.query(models.Chapter).filter_by(
        chapter_title="Claim Two").one()
    assert db.query(models.Topic).filter_by(
        chapter_id=second_chapter.id).one().machine_id == ""


# --------------------------------------------------------------------------- #
# 10. The export leg STORES what it exports.
# --------------------------------------------------------------------------- #

def test_the_export_route_persists_the_id_it_minted(db, client):
    """[measured, before the fix] ``GET /data/export/concepts`` minted the id
    on the ORM object and ``get_db`` closed the session without committing, so
    one concept exported two different ids across two downloads that straddled
    a reorder while the column stayed blank throughout."""
    chapter = _chapter(db, title="Export Persist", code="06CBSC_ExportP")
    topic = _topic(db, chapter, "Reflection", source_order=1)
    concept = _concept(db, topic, "Mirror", source_order=1)
    db.commit()
    concept_id, topic_id, chapter_id = concept.id, topic.id, chapter.id

    first = client.get(f"/data/export/concepts?ids={concept_id}")
    assert first.status_code == 200
    db.expire_all()
    stored = db.get(models.Concept, concept_id).machine_id
    assert stored, "the leg that exports the tag is the leg that stores it"

    db.get(models.Topic, topic_id).source_order = 2
    db.add(models.Topic(
        chapter_id=chapter_id, topic_title="Refraction",
        pre_post_learning="Post", source_order=1))
    db.commit()
    second = client.get(f"/data/export/concepts?ids={concept_id}")
    assert second.status_code == 200
    db.expire_all()
    assert db.get(models.Concept, concept_id).machine_id == stored


# --------------------------------------------------------------------------- #
# 11. Nothing a learner would see is silently lost or silently truncated.
# --------------------------------------------------------------------------- #

def test_a_name_that_ends_in_a_parenthetical_survives_the_round_trip():
    """[measured, before the fix] ``titled('Ohm Law (V_I_R)', id)`` returned
    ``'Ohm Law (id)'``, which re-imports as ``'Ohm Law'`` — a learner-visible
    name silently changed by a round trip (R4), and a loss the pre-S4 writer
    did not have because it composed ``f"{title} ({tag})"`` without stripping.
    """
    minted = "06CBSC_Circuits_1234abcd_PL_T01_C01"
    cell = identity.titled("Ohm Law (V_I_R)", minted)
    assert bi.strip_title_tag(cell) == "Ohm Law (V_I_R)"
    assert identity.title_tag(cell) == minted
    # Idempotent for a value this module already composed.
    assert identity.titled(cell, minted) == cell
    # And a re-tag replaces the minted tag rather than stacking a second one.
    other = "06CBSC_Circuits_1234abcd_PL_T01_C02"
    assert identity.titled(cell, other) == f"Ohm Law (V_I_R) ({other})"


def test_an_unparsable_grade_token_cannot_overrun_the_label_column(db):
    """``normalized_grade``'s raw-token fallback was the ONE unbounded segment
    of an id, and it lands inside ``Question.question_label``'s
    ``String(128)``."""
    long_grade = "Class VI Semester Two Section B Academic Year 2026 2027"
    token, fell_back = directory.normalized_grade(long_grade)
    assert fell_back and len(token) <= 16
    chapter = _chapter(
        db, title="A Very Long Chapter Title Indeed", grade=long_grade,
        code="06CBSC_LongGrade")
    topic = _topic(db, chapter, "T" * 200, source_order=1)
    concept = _concept(db, topic, "C" * 200, source_order=1)
    db.flush()
    db.refresh(chapter)
    label = generation.question_label(concept, 3)
    assert len(label) <= 128, len(label)
    assert identity.grade_review_flag(chapter), "the fallback is RECORDED"


def test_identity_notes_cannot_crowd_out_the_rest_of_the_ledger(
    db, tmp_path, monkeypatch,
):
    """``reader._flag`` caps ``issues`` at 200. A wholly tagless legacy book
    raises up to two identity notes per distinct name, so without a budget of
    their own they would truncate the placement and mangled-row issues raised
    later in the same row loop — the ledger S2 built."""
    monkeypatch.setattr(reader, "_MAX_IDENTITY_NOTES", 1)
    path = _one_row_workbook(
        tmp_path, "budget.xlsx",
        topic_cell="Topic 01: Reflection",
        concept_cell="Mirror",
        chapter_cell="Budget (06_Science_CBSE)",
    )
    counts = reader.import_workbook(db, path)
    db.commit()
    notes = [
        issue for issue in counts["issues"]
        if "(imported_without_machine_id)" in issue
    ]
    truncated = [
        issue for issue in counts["issues"]
        if "(identity_notes_truncated)" in issue
    ]
    assert len(notes) == 1 and truncated, counts["issues"]


def test_the_grade_fallback_is_recorded_on_the_release_summary(db):
    """T4-2: "record the fallback as a review flag on the release"."""
    chapter = _chapter(db, title="Kindergarten Shapes", grade="Nursery",
                       code="NurseryCB_Shapes")
    db.commit()
    job = _job(db, chapter, [{"topic": "Shapes", "concept_title": "Circle",
                              "concept_details": "A round shape."}])
    publication.upload_release_to_database(db, job.id, owner_sub=OWNER)
    db.commit()
    db.refresh(job)
    summary = (
        build_concepts_release.release_payload(job).get("summary") or {}
    )
    flags = summary.get("identity_review_flags") or []
    assert any("grade_token_not_normalised" in flag for flag in flags), summary

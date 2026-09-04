"""Spec-step8 B4/B2/F1: identity survives projection; the profile is threaded.

B4: the projection layer was the one place concept identity died. writer.py
composed ``Title (machine_id)`` cells while ``assessment_workbook`` shipped
the bare title, so a Master File's own roster disagreed with the cells it
named, and a re-import found no tag, recorded ``imported_without_machine_id``
and re-minted — collapsing two same-titled concepts under two topics into one
identity. Three audits missed it because every existing renderer test builds
snapshots BY HAND (one pre-bakes the tag into fixture titles), so this suite
drives a PRODUCTION snapshot builder end to end: mint → snapshot → render →
read-back → download bytes → re-import → the original persisted ids return.
"""
from __future__ import annotations

import pytest

from app import models
from app.bulk_import import assessment_workbook as aw
from app.bulk_import import reader
from app.bulk_import import writer
from app.services import assessment_blueprint
from app.services import assessment_marking
from app.services import assessment_materialization
from app.services import assessment_profile
from app.services import assessment_release as rel
from app.services import assessment_release_service as release_service
from app.services import assessment_release_snapshot as release_snapshot
from app.services import build_concepts_release as release
from app.services import identity


def _two_topic_chapter(db):
    """A chapter whose code matches what the reader will re-derive.

    The re-import half of the round trip resolves the chapter by the code
    the workbook text derives, not by the DB row — a mismatched code makes
    the import build a parallel chapter and the assertions vacuous (the
    integrity audit measured exactly that). ``06CBSC_ProjectionId`` is what
    ``directory.derive_chapter_meta`` yields for this board/grade/subject,
    publication and title (contract v2.0 §18: the run names its
    publication, so the complete human tag decides the code).
    """
    chapter = models.Chapter(
        chapter_code="06CBSC_ProjectionId", board="CBSE", grade="06",
        subject="Science", unit="Unit", chapter_title="Projection Identity",
        chapter_display_name="Projection Identity",
        chapter_duration="40 minutes",
    )
    db.add(chapter)
    db.commit()
    return chapter


def _staged_two_culmination_job(db, chapter):
    """A staged POST release: two topics, each one questionless concept
    titled 'Culmination' — the exact B4 trigger, through the production
    staging path (stage_release -> release_payload -> release_snapshot.build).
    """
    job = models.UploadJob(
        owner_sub="test-owner",
        module="build_concepts",
        upload_type="textbook",
        filename="ch.mmd",
        mmd_text="# Chapter\n\nLight and sound.",
        status="generated",
        learning_kind="post",
        source_book="NCERT",
        deposit_scope_type="chapter",
        deposit_scope_ids=[chapter.id],
        question_inventory={"items": []},
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    release.stage_release(
        db,
        job,
        target_chapter_id=chapter.id,
        records=[
            {
                "topic": "Light",
                "concept_title": "Culmination",
                "concept_details": "Description: ties Light together.",
                "keywords": "light",
            },
            {
                "topic": "Sound",
                "concept_title": "Culmination",
                "concept_details": "Description: ties Sound together.",
                "keywords": "sound",
            },
        ],
        inventory={"items": []},
        reason="B4 projection-identity fixture",
    )
    db.refresh(job)
    return job


def _culmination_rows(db, chapter):
    return (
        db.query(models.Concept).join(models.Topic)
        .filter(models.Topic.chapter_id == chapter.id)
        .filter(models.Concept.concept_title == "Culmination")
        .order_by(models.Topic.source_order, models.Topic.id)
        .all()
    )


def _cleanup_chapter(db, chapter_code):
    # The session DB is shared suite-wide; a leaked grade-06 chapter with
    # concepts re-aims the first_chapter fixture for every later consumer
    # (integrity audit F3). Remove everything this file created.
    for chapter in (
        db.query(models.Chapter).filter_by(chapter_code=chapter_code).all()
    ):
        for topic in list(chapter.topics):
            db.delete(topic)
        db.delete(chapter)
    db.commit()


def test_two_same_titled_concepts_round_trip_their_persisted_ids(
    db, tmp_path,
):
    chapter = _two_topic_chapter(db)
    try:
        job = _staged_two_culmination_job(db, chapter)

        # THE LIVE PRODUCTION BUILDER (assessment_release_snapshot.build) —
        # the one create_release actually uses; snapshot_from_chapter is a
        # dormant branch and proves nothing about production.
        bridge = release_snapshot.build(db, job, release.release_payload(job))
        snapshot = bridge["snapshot"]
        assert bridge["defects"] == []
        # On the staged path identity is COMPOSED onto the transient rows
        # (build_concepts_release_files:549-603) and carried by the
        # snapshot; DB rows do not exist until a school's re-import
        # creates them.
        minted = [
            str(c.get("concept_machine_id") or "")
            for t in snapshot["topics"] for c in t["concepts"]
            if c.get("concept_title") == "Culmination"
        ]
        assert len(minted) == 2 and all(minted) and minted[0] != minted[1], (
            "the snapshot must carry two distinct topic-scoped ids"
        )

        output = aw.build_dual_output(snapshot, None)
        assert output["manifest"]["read_back"]["concepts_errors"] == []
        assert output["manifest"]["read_back"]["master_errors"] == []

        # The Master's own cells carry the composed identity: the two
        # same-titled tail rows are DISTINCT, and each row's roster names
        # its concept exactly as the title cell reads.
        parsed = aw.parse_workbook(output["master_xlsx"])
        rows = parsed["sheets"]["Objective"]["rows"]
        titles = [str(row.get("concept_title") or "") for row in rows]
        assert identity.titled("Culmination", minted[0]) in titles
        assert identity.titled("Culmination", minted[1]) in titles
        assert len(set(titles)) == len(titles)
        for row in rows:
            assert str(row.get("topic_concept_labels") or "") == (
                str(row.get("concept_title") or "")
            ), "the roster must read exactly as the title cell it names"

        # Download bytes -> BLANK the stored ids -> re-import through the
        # production reader. Only now does restoration actually drive: a
        # row that still holds its id makes _restore_machine_id return
        # before any tag is read (the audit proved the earlier version of
        # this test passed against the pre-fix renderer).
        path = tmp_path / "master.xlsx"
        path.write_bytes(output["master_xlsx"])
        # No DB topic/concept rows exist yet — the import is the act that
        # creates them, exactly as a school's re-import does. Restoration
        # must come from the composed cells.
        assert _culmination_rows(db, chapter) == []

        counts = reader.import_workbook(db, path)
        identity_issues = [
            issue for issue in (counts.get("issues") or [])
            if "machine id" in str(issue)
        ]
        assert identity_issues == [], identity_issues
        assert (
            db.query(models.Chapter)
            .filter_by(chapter_code="06CBSC_ProjectionId").count() == 1
        ), "the import must land on the SAME chapter, not build a twin"
        restored = [row.machine_id for row in _culmination_rows(db, chapter)]
        assert set(restored) == set(minted), (
            "re-import must restore the SAME persisted identities"
        )
        restored_topics = sorted(
            row.machine_id for row in db.query(models.Topic)
            .filter(models.Topic.chapter_id == chapter.id).all()
        )
        assert all(restored_topics), "topic ids must restore from their cells"
    finally:
        _cleanup_chapter(db, "06CBSC_ProjectionId")


def test_an_id_less_title_with_a_baked_minted_tag_stays_verbatim():
    """The gold-safety pin, on the shape that DISCRIMINATES.

    ``identity.titled(title, "")`` STRIPS a minted-grammar tag, so an
    unguarded composition would silently rewrite an id-less snapshot's
    title — the exact loss the ``if machine_id`` guard exists to prevent.
    Driven through ``_bands_record`` (the projection point), not the
    helper alone.
    """
    baked = "Culmination (06CBSC_ProjectionId_f2ffe0d9_PL_T01_C05)"
    record = aw._bands_record({
        "chapter": {},
        "topic": {"topic_title": "Light"},
        "concept": {"concept_title": baked, "concept_machine_id": ""},
    })
    assert record["concept_title"] == baked
    assert record["topic_title"] == "Light"


def test_topic_title_composer_is_idempotent_and_uses_the_persisted_id():
    persisted = "06CBSC_Mechanics_ab12cd34_PL_T07"
    expected = f"Topic 02: Reflection ({persisted})"

    assert identity.topic_title_cell("Reflection", persisted, 2) == expected
    assert identity.topic_title_cell(expected, persisted, 2) == expected
    assert identity.topic_title_cell(
        "Topic 99: Reflection (06CBSC_Mechanics_PL)", persisted, 2,
    ) == expected
    assert identity.topic_title_cell(
        "Reflection (06CBSC_Mechanics_PL)", persisted, 2,
    ) == expected
    assert identity.topic_title_cell(
        "Ohm Law (V_I_R)", persisted, 2,
    ) == f"Topic 02: Ohm Law (V_I_R) ({persisted})"
    assert identity.topic_title_cell(
        "Programming Language (PL)", persisted, 2,
    ) == f"Topic 02: Programming Language (PL) ({persisted})"
    assert identity.topic_title_cell(
        "Topic 01: Programming Language (PL)", persisted, 2,
    ) == f"Topic 02: Programming Language (PL) ({persisted})"
    assert identity.topic_title_cell(
        "Value (V_PL)", persisted, 2,
    ) == f"Topic 02: Value (V_PL) ({persisted})"
    assert identity.topic_title_cell(
        "Topic 01: Value (V_PL)", persisted, 2,
    ) == f"Topic 02: Value (V_PL) ({persisted})"

    # An id-less legacy snapshot is gold input: neither its historical display
    # ordinal nor its historical tag may be rewritten without a persisted id.
    legacy = "Topic 09: Legacy (06CBSC_Mechanics_PL)"
    assert identity.topic_title_cell(legacy, "", 1) == legacy

    chapter = models.Chapter(chapter_title="Mechanics")
    topic = models.Topic(
        topic_title="Topic 99: Reflection (06CBSC_Mechanics_PL)",
        topic_display_name="Reflection",
        pre_post_learning="Post",
        source_order=1,
        machine_id=persisted,
    )
    chapter.topics.append(topic)
    assert writer.composed_topic_title(topic) == expected.replace(
        "Topic 02:", "Topic 01:",
    )

    transient = models.Topic(
        topic_title="Legacy transient",
        topic_display_name="Legacy transient",
        pre_post_learning="Post",
        source_order=2,
    )
    chapter.topics.append(transient)
    assert writer.composed_topic_title(transient) == (
        "Topic 02: Legacy transient"
    )


def test_concept_and_master_share_snapshot_ordered_per_lane_topic_titles():
    specs = (
        ("Post", "Post first", "06CBSC_Mixed_ab12cd34_PL_T09"),
        (
            "Pre", "Topic 88: Pre first (06CBSC_Mixed_PrL)",
            "06CBSC_Mixed_ab12cd34_PrL_T04",
        ),
        ("post", "Post second", "06CBSC_Mixed_ab12cd34_PL_T03"),
        ("pre-learning", "Pre second", "06CBSC_Mixed_ab12cd34_PrL_T08"),
    )
    topics = []
    expected = []
    lane_counts = {"PL": 0, "PrL": 0}
    for index, (lane, title, machine_id) in enumerate(specs, start=1):
        lane_token = identity.lane_token(lane)
        lane_counts[lane_token] += 1
        ordinal = lane_counts[lane_token]
        readable = "Pre first" if "Pre first" in title else title
        expected.append(
            f"Topic {ordinal:02d}: {readable} ({machine_id})"
        )
        topics.append({
            "topic_title": title,
            "topic_machine_id": machine_id,
            "topic_display_name": readable,
            "pre_post_learning": lane,
            "topic_concept_labels": "",
            "related_topics": "",
            "topic_description": "",
            "concepts": [{
                "concept_key": f"K{index}",
                "concept_title": f"Concept {index}",
                "concept_machine_id": f"{machine_id}_C01",
                "concept_display_name": f"Concept {index}",
                "concept_details": f"Description: concept {index}.",
            }],
        })
    snapshot = {
        "chapter": {"chapter_title": "Mixed lanes"},
        "topics": topics,
        "groups": [],
        "candidates": [],
    }

    concept_rows = aw.parse_workbook(
        aw.render_concept_file(snapshot)
    )["sheets"]["Objective"]["rows"]
    master_bytes, _issues = aw.render_master_file(snapshot)
    master_rows = aw.parse_workbook(
        master_bytes
    )["sheets"]["Objective"]["rows"]

    assert [row["topic_title"] for row in concept_rows] == expected
    assert [row["topic_title"] for row in master_rows] == expected
    assert len({identity.title_tag(value) for value in expected}) == 4


def test_snapshot_and_bulk_writer_share_legacy_zero_source_order(monkeypatch):
    chapter = models.Chapter(
        chapter_code="06CBSC_ORDER",
        board="CBSE",
        grade="06",
        subject="Science",
        unit="Science",
        chapter_title="Ordering",
        chapter_display_name="Ordering",
    )
    positioned_topic = models.Topic(
        topic_title="Positioned",
        topic_display_name="Positioned",
        pre_post_learning="Post",
        source_order=1,
        machine_id="06CBSC_Ordering_ab12cd34_PL_T01",
    )
    legacy_topic = models.Topic(
        topic_title="Legacy zero",
        topic_display_name="Legacy zero",
        pre_post_learning="Post",
        source_order=0,
        machine_id="06CBSC_Ordering_ab12cd34_PL_T02",
    )
    chapter.topics.extend([legacy_topic, positioned_topic])
    positioned_concept = models.Concept(
        concept_title="Positioned concept",
        concept_display_name="Positioned concept",
        concept_details="Description: positioned.",
        machine_id=f"{positioned_topic.machine_id}_C01",
    )
    legacy_concept = models.Concept(
        concept_title="Legacy concept",
        concept_display_name="Legacy concept",
        concept_details="Description: legacy.",
        machine_id=f"{legacy_topic.machine_id}_C01",
    )
    positioned_topic.concepts.append(positioned_concept)
    legacy_topic.concepts.append(legacy_concept)
    concepts = [legacy_concept, positioned_concept]
    records = [
        {"topic": concept.topic.topic_title,
         "concept_title": concept.concept_title}
        for concept in concepts
    ]

    def transient_hierarchy(*_args, **_kwargs):
        return chapter, concepts, records, []

    monkeypatch.setattr(
        release_snapshot.build_concepts_release_files,
        "transient_release_hierarchy",
        transient_hierarchy,
    )
    bridge = release_snapshot.build(
        None,
        models.UploadJob(),
        {"source_document_hash": "sha256:ordering", "records": records},
    )
    snapshot = bridge["snapshot"]

    expected = [
        writer.composed_topic_title(positioned_topic),
        writer.composed_topic_title(legacy_topic),
    ]
    assert expected == [
        f"Topic 01: Positioned ({positioned_topic.machine_id})",
        f"Topic 02: Legacy zero ({legacy_topic.machine_id})",
    ]
    assert [topic["topic_title"] for topic in snapshot["topics"]] == [
        "Positioned", "Legacy zero",
    ]

    concept_rows = aw.parse_workbook(
        aw.render_concept_file(snapshot)
    )["sheets"]["Objective"]["rows"]
    master_bytes, _issues = aw.render_master_file(snapshot)
    master_rows = aw.parse_workbook(
        master_bytes
    )["sheets"]["Objective"]["rows"]
    assert [row["topic_title"] for row in concept_rows] == expected
    assert [row["topic_title"] for row in master_rows] == expected


def test_direct_snapshot_matches_writer_order_for_lane_less_legacy_rows(db):
    chapter = models.Chapter(
        chapter_code="06CBSC_DIRECTORDER",
        board="CBSE",
        grade="06",
        subject="Science",
        unit="Science",
        chapter_title="Direct Ordering",
        chapter_display_name="Direct Ordering",
    )
    positioned_topic = models.Topic(
        chapter=chapter,
        topic_title="Positioned topic",
        topic_display_name="Positioned topic",
        pre_post_learning="",
        source_order=1,
    )
    legacy_topic = models.Topic(
        chapter=chapter,
        topic_title="Legacy topic",
        topic_display_name="Legacy topic",
        pre_post_learning="",
        source_order=0,
    )
    positioned_concept = models.Concept(
        topic=positioned_topic,
        concept_title="Positioned concept",
        concept_display_name="Positioned concept",
        concept_details="Description: positioned.",
        source_order=1,
    )
    legacy_concept = models.Concept(
        topic=positioned_topic,
        concept_title="Legacy concept",
        concept_display_name="Legacy concept",
        concept_details="Description: legacy.",
        source_order=0,
    )
    other_concept = models.Concept(
        topic=legacy_topic,
        concept_title="Other concept",
        concept_display_name="Other concept",
        concept_details="Description: other.",
        source_order=0,
    )
    db.add_all([
        chapter, positioned_topic, legacy_topic, positioned_concept,
        legacy_concept, other_concept,
    ])
    db.commit()
    try:
        snapshot = release_service.snapshot_from_chapter(
            db, chapter.id, {},
        )
        ordered_topics = sorted(
            chapter.topics, key=identity.source_order_key,
        )
        assert [topic["topic_title"] for topic in snapshot["topics"]] == [
            topic.topic_title for topic in ordered_topics
        ] == ["Positioned topic", "Legacy topic"]
        assert [
            writer._topic_number(topic) for topic in ordered_topics
        ] == [1, 2]

        first_concepts = snapshot["topics"][0]["concepts"]
        assert [
            concept["concept_title"] for concept in first_concepts
        ] == [
            concept.concept_title
            for concept in sorted(
                positioned_topic.concepts, key=identity.source_order_key,
            )
        ] == ["Positioned concept", "Legacy concept"]
    finally:
        _cleanup_chapter(db, "06CBSC_DIRECTORDER")


_WIDENED = {
    "name": "widened-test",
    "sheet_kinds": ("objective", "descriptive", "subjective"),
}
_NARROWED = {
    "name": "narrowed-test",
    "sheet_kinds": ("objective", "descriptive"),
}


def test_strict_blueprint_validation_honors_the_run_profile():
    cell = {
        "cell_id": "C1", "sheet_kind": "subjective",
        "question_category": "Long Answer", "count": 1, "marks": 5,
        "cognitive_skill": "Understanding", "difficulty": "Moderate",
    }
    # The widened run profile accepts its own kind outright — no exception
    # of any class (the audit caught an earlier version whose fixture used
    # the wrong field names, so the "acceptance" leg silently raised).
    assessment_blueprint.validate_cells(
        [dict(cell)], strict_profile=True, profile=_WIDENED,
    )
    # …which an explicitly narrowed profile refuses BY NAME, in the module's
    # own refusal class. The default profile now renders Subjective end to end.
    with pytest.raises(assessment_blueprint.BlueprintError, match="sheet_kind"):
        assessment_blueprint.validate_cells(
            [dict(cell)], strict_profile=True, profile=_NARROWED,
        )


def test_materialization_validation_honors_the_run_profile():
    cell = {"cell_id": "C1", "sheet_kind": "subjective", "marks": 5}
    meta = {"board": "CBSE", "grade": "6", "subject": "Science"}
    candidate_id = assessment_materialization._validate_obligation(
        None, cell, meta, _WIDENED,
    )
    assert candidate_id
    with pytest.raises(
        assessment_materialization.MaterializationError, match="sheet_kind",
    ):
        assessment_materialization._validate_obligation(
            None, cell, meta, _NARROWED,
        )


def test_marking_validation_honors_the_run_profile():
    candidate = {
        "candidate_id": "C1", "blueprint_cell_id": "B1",
        "sheet_kind": "subjective", "question_category": "Long Answer",
        "cognitive_skill": "Understanding", "difficulty": "Moderate",
        "marks": 5,
    }
    cell = {
        "cell_id": "B1", "sheet_kind": "subjective",
        "question_category": "Long Answer",
        "cognitive_skill": "Understanding", "difficulty": "Moderate",
        "marks": 5,
    }
    # The widened run profile passes the sheet_kind gate (later checks in
    # the pair contract may still fire; sheet_kind must not be one of them)…
    # Only MarkingError is tolerable: the pre-fix two-argument signature
    # raises TypeError, which must propagate and fail this test.
    try:
        assessment_marking._prepare_pair((candidate, cell), 1, _WIDENED)
    except assessment_marking.MarkingError as exc:
        assert "sheet_kind" not in str(exc), (
            f"widened profile rejected its own sheet_kind: {exc}"
        )
    # …which an explicitly narrowed profile refuses by name.
    with pytest.raises(assessment_marking.MarkingError, match="sheet_kind"):
        assessment_marking._prepare_pair(
            (candidate, cell), 1, _NARROWED,
        )


def test_the_run_profile_is_threaded_at_every_production_call_site():
    """B2 is a THREADING fix; the leaf tests above cannot see the wiring.

    Pins the four call sites by source: reverting any ``profile=profile``
    argument goes red here. Brittle to renames by design — a rename must
    re-point the pin, not lose it.
    """
    import inspect

    from app.services import assessment_release_run as run_mod

    # Anchored per enclosing function — a bare module-wide count was
    # satisfied by pre-existing occurrences and proved nothing.
    for fn, needle in (
        (run_mod._bind_explicit_cells, "profile=profile"),
        (run_mod._bind_generated_cells, "profile=profile"),
        (run_mod.run_release_for_job, "materialize_candidates("),
        (run_mod.run_release_for_job, "decide_markings("),
    ):
        source = inspect.getsource(fn)
        assert needle in source
    body = inspect.getsource(run_mod.run_release_for_job)
    for call in ("materialize_candidates(", "decide_markings("):
        # A fixed window past the call token: the kwargs of both call
        # sites sit well inside it, and nested parens make brace-matching
        # by index wrong.
        segment = body[body.index(call):body.index(call) + 400]
        assert "profile=profile" in segment, (
            f"{call} does not receive the run profile"
        )


def test_the_read_back_blanks_by_the_profile_not_by_two_hardcoded_names(db):
    """F1 — the fourth C10 site: a profile that un-blanks a field must not
    fail its own read-back on the value it just rendered."""
    chapter = _two_topic_chapter(db)
    try:
        chapter.chapter_duration = "45"
        db.commit()
        job = _staged_two_culmination_job(db, chapter)
        snapshot = release_snapshot.build(
            db, job, release.release_payload(job))["snapshot"]
        unblanked = {"name": "unblanked-test", "forced_blank_fields": ()}
        output = aw.build_dual_output(snapshot, unblanked)
        assert output["manifest"]["read_back"]["master_errors"] == [], (
            "the renderer shipped the value; the read-back must accept it"
        )
        # The negative control: a parsed workbook CARRYING the value must
        # fail a profile that explicitly force-blanks chapter_duration.
        parsed = aw.parse_workbook(output["master_xlsx"])
        blanking_profile = {
            "name": "blanked-test",
            "forced_blank_fields": ("chapter_duration",),
        }
        errors = aw.validate_master_file(
            parsed, snapshot, blanking_profile,
        )
        assert any("chapter_duration must be blank" in e for e in errors)
    finally:
        _cleanup_chapter(db, "06CBSC_ProjectionId")

def _tiny_snapshot():
    """A hand-built snapshot for renderer/validator MECHANICS tests only.

    (The B4 end-to-end above uses the production builder; these four pin
    single mechanisms where hand-built inputs are the point.)
    """
    return {
        "chapter": {"chapter_title": "Mechanics"},
        "topics": [{
            "topic_title": "Light",
            "topic_machine_id": "07CBSC_Mechanics_ab12cd34_PL_T01",
            "topic_concept_labels": identity.titled(
                "Culmination", "07CBSC_Mechanics_ab12cd34_PL_T01_C01"),
            "concepts": [{
                "concept_key": "K1",
                "concept_machine_id": "07CBSC_Mechanics_ab12cd34_PL_T01_C01",
                "concept_title": "Culmination",
                "concept_display_name": "Culmination",
                "concept_details": "Description: d.",
            }],
        }],
        "groups": [],
        "candidates": [],
    }


def test_topic_cells_have_a_read_back_twin():
    """B4 repair round: the composed topic cell shipped unverified while
    the concept cell had three twins."""
    snapshot = _tiny_snapshot()
    output = aw.build_dual_output(snapshot, None)
    assert output["manifest"]["read_back"]["concepts_errors"] == []
    assert output["manifest"]["read_back"]["master_errors"] == []

    for sheet_fn, key in (
        (aw.render_concept_file(snapshot, None), "concept"),
        (output["master_xlsx"], "master"),
    ):
        parsed = aw.parse_workbook(sheet_fn)
        for row in parsed["sheets"]["Objective"]["rows"]:
            row["topic_title"] = "Light"  # the BARE title: a mangled cell
        if key == "concept":
            errors = aw.validate_concept_file(parsed, snapshot, None)
            assert any("topic title cells differ" in e for e in errors)
        else:
            errors = aw.validate_master_file(parsed, snapshot, None)
            assert any("topic_title does not match" in e for e in errors)


def test_the_staging_shape_gate_measures_the_composed_title():
    """A title inside the cell cap bare but over it composed must be a
    staged render_shape_overflow finding, or the DB write is allowed for a
    workbook whose cell the renderer truncates."""
    snapshot = _tiny_snapshot()
    concept = snapshot["topics"][0]["concepts"][0]
    concept["concept_title"] = "T" * (aw.CELL_LIMIT - 5)
    findings = rel.unresolved_question_homes(snapshot, None)
    assert any(
        f.get("code") == "render_shape_overflow"
        and "concept_title" in str(f)
        for f in findings
    ), findings


def test_a_replaced_baked_tag_is_recorded_not_silent():
    """R4: identity.titled replaces a minted-shaped baked tag with the
    persisted id; the loser must land in the issues ledger (the reader
    records the mirror case as machine_id_conflict)."""
    snapshot = _tiny_snapshot()
    concept = snapshot["topics"][0]["concepts"][0]
    concept["concept_title"] = (
        "Culmination (07CBSC_Mechanics_ab12cd34_PL_T01_C05)")
    _bytes, issues = aw.render_master_file(snapshot, None)
    replacements = issues["identity_tag_replacements"]
    assert len(replacements) == 1
    assert replacements[0]["carried_tag"] == (
        "07CBSC_Mechanics_ab12cd34_PL_T01_C05")
    assert replacements[0]["applied_machine_id"] == (
        "07CBSC_Mechanics_ab12cd34_PL_T01_C01")


def test_the_questionless_ledger_names_the_rendered_cell():
    """A ledger entry must name a row by a string a cell contains."""
    snapshot = _tiny_snapshot()
    output = aw.build_dual_output(snapshot, None)
    parsed = aw.parse_workbook(output["master_xlsx"])
    titles = {
        str(row.get("concept_title") or "")
        for row in parsed["sheets"]["Objective"]["rows"]
    }
    ledger = output["manifest"]["issues"]["questionless_concepts"]
    assert ledger and all(
        entry["rendered_concept_title"] in titles for entry in ledger
    )

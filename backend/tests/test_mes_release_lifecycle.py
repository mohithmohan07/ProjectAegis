"""MES PR 6 — release lifecycle: atomic dual publication and explicit upload
(spec §13, §16).
"""
from __future__ import annotations

import copy
import json
import uuid
from pathlib import Path

import pytest

from app import config, models
from app.bulk_import import assessment_workbook as mp
from app.services import assessment_grouping as ag
from app.services import assessment_release_service as svc

OWNER = "local:default"


def _chapter_concept(db) -> models.Concept:
    concept = (
        db.query(models.Concept)
        .join(models.Topic)
        .order_by(models.Concept.id)
        .first()
    )
    assert concept is not None, "fixture tree has no concepts"
    return concept


def _payload(
    concept_key: str, concept_machine_id: str, concept_name: str, *,
    label: str, flags=None,
) -> dict:
    groups = []
    for shell in ag.required_shells(
        concept_key, concept_machine_id, concept_name,
    ):
        shell = dict(shell)
        shell["concept_key"] = concept_key
        groups.append(shell)
    candidate = {
        "candidate_id": f"CAND-{label}",
        "question_label": label,
        "source_atom_ids": ["QINV-0001"],
        "blueprint_cell_id": "CELL-rel1",
        "sheet_kind": "objective",
        "question_category": "Multiple Choice Question",
        "cognitive_skill": "Remember",
        "difficulty": "Less",
        "marks": 1.0,
        "question_duration": 2.0,
        "math_keyboard": "",
        "question_appears_in": "Pre/Post-Worksheet/Test",
        "answer_restriction": "Specific",
        "restriction_reason": "The authored answer space is bounded.",
        "question": "Which of these is a solid?",
        "question_text": "Which of these is a solid?",
        "answers": [
            {"answer_type": "Phrases", "answer_content": "Cube",
             "correct_answer": "1", "answer_weightage": "1"},
            {"answer_type": "Phrases", "answer_content": "Circle",
             "correct_answer": "0", "answer_weightage": "0"},
        ],
        "sub_questions": [],
        "answer_explanation": "A cube is a three-dimensional solid.",
        "concept_key": concept_key,
        "group_key": f"({concept_machine_id}) BG01",
        "flags": list(flags or []),
    }
    return {"groups": groups, "candidates": [candidate]}


def _fresh_release(db, *, flags=None, mutate=None):
    concept = _chapter_concept(db)
    concept_key = f"db:{concept.id}"
    concept_name = concept.concept_display_name
    concept_machine_id = svc._machine_id({
        "concept_key": concept_key,
        "concept_title": concept.concept_title,
    })
    label = f"RELQ {uuid.uuid4().hex[:10]}"
    payload = _payload(
        concept_key, concept_machine_id, concept_name,
        label=label, flags=flags)
    if mutate:
        mutate(payload)
    release = svc.create_release(
        db,
        chapter_id=concept.topic.chapter_id,
        payload=payload,
        owner_sub=OWNER,
    )
    return release, payload, label


def test_publish_exposes_both_files_atomically(db, client):
    release, _payload_, _label = _fresh_release(db)
    assert release.state == "materialized"

    published = svc.publish_release(db, release)
    assert published.state == "ready_for_upload"
    assert published.diagnostics["readiness"] == svc.READY
    directory = Path(published.publication["directory"])
    assert (directory / svc.CONCEPTS_FILENAME).is_file()
    assert (directory / svc.MASTER_FILENAME).is_file()
    manifest = json.loads(
        (directory / svc.MANIFEST_FILENAME).read_text(encoding="utf-8"))
    assert manifest["concept_snapshot_sha256"] == (
        published.concept_snapshot_sha256)
    assert manifest["readiness"] == svc.READY

    summary = client.get(
        f"/build-assessments/releases/{published.id}").json()
    assert summary["published"] is True
    assert summary["readiness"] == svc.READY
    for artifact in ("concepts.xlsx", "master.xlsx"):
        response = client.get(
            f"/build-assessments/releases/{published.id}/{artifact}")
        assert response.status_code == 200
        assert response.content[:2] == b"PK"


def test_release_snapshot_replaces_stale_visible_names_but_keeps_group_keys(db):
    def stale_names(payload):
        for group in payload["groups"]:
            group["group_name"] = group["group_key"]
            group["group_display_name"] = group["group_key"]

    release, payload, _ = _fresh_release(db, mutate=stale_names)
    concept = _chapter_concept(db)
    expected = concept.concept_display_name
    assert all(
        group["group_name"] == group["group_display_name"]
        == f"{expected} — {group['group_type']}"
        for group in release.concept_snapshot["groups"]
        if group["concept_key"] == f"db:{concept.id}"
    )
    assert {
        group["group_key"]
        for group in release.concept_snapshot["groups"]
        if group["concept_key"] == f"db:{concept.id}"
    } == {group["group_key"] for group in payload["groups"]}


def test_a_second_variant_group_does_not_replace_the_required_first_shell(db):
    def second_basic_only(payload):
        basic = next(
            group for group in payload["groups"]
            if group["group_type"] == "Basic")
        basic["group_key"] = basic["group_key"].removesuffix("01") + "02"
        basic["group_sequence"] = 2
        payload["candidates"][0]["group_key"] = basic["group_key"]

    release, payload, _ = _fresh_release(db, mutate=second_basic_only)
    keys = {
        group["group_key"] for group in release.concept_snapshot["groups"]
        if group["concept_key"] == release.payload["groups"][0]["concept_key"]
    }
    second_key = next(
        group["group_key"] for group in payload["groups"]
        if group["group_type"] == "Basic")
    required_key = second_key.removesuffix("02") + "01"
    assert keys == {
        *(group["group_key"] for group in payload["groups"]),
        required_key,
    }


@pytest.mark.parametrize(
    ("field", "value"),
    (("group_type", "Advanced"), ("group_sequence", 2)),
)
def test_malformed_first_shell_cannot_suppress_the_required_shell(
    db, field, value,
):
    def corrupt_required_shell(payload):
        basic = next(
            group for group in payload["groups"]
            if group["group_type"] == "Basic")
        basic[field] = value

    with pytest.raises(ag.GroupingError, match="inconsistent"):
        _fresh_release(db, mutate=corrupt_required_shell)


def test_duplicate_group_key_is_named_and_publication_fails_closed(db):
    def reuse_key_across_tiers(payload):
        basic = next(
            group for group in payload["groups"]
            if group["group_type"] == "Basic")
        duplicate = copy.deepcopy(basic)
        duplicate["group_type"] = "Advanced"
        duplicate["group_name"] = "stale"
        duplicate["group_display_name"] = "stale"
        payload["groups"].append(duplicate)

    release, payload, _ = _fresh_release(
        db, mutate=reuse_key_across_tiers)
    duplicate_key = payload["groups"][-1]["group_key"]
    assert f"duplicate group_key {duplicate_key!r}" in (
        release.diagnostics["payload_errors"])

    with pytest.raises(mp.WorkbookRenderError, match="duplicate group_key"):
        svc.publish_release(db, release)
    assert release.state == "materialized"
    assert not release.publication


def test_shell_completion_never_parses_a_tagged_title_as_a_friendly_name():
    snapshot = {
        "topics": [{"concepts": [{
            "concept_key": "db:1",
            "concept_title": "Solid Shapes (MACHINE-C1)",
            "concept_display_name": "",
        }]}],
        "groups": [],
    }
    with pytest.raises(ag.GroupingError, match="explicit concept"):
        svc._complete_required_shells(snapshot)


def test_unpublished_release_serves_no_artifacts(db, client):
    release, _, _ = _fresh_release(db)
    response = client.get(
        f"/build-assessments/releases/{release.id}/master.xlsx")
    assert response.status_code == 404


def test_staging_debris_is_recovered_and_never_served():
    root = config.DATA_DIR / "assessment_releases" / "REL-crashed"
    staging = root / "v1.staging"
    staging.mkdir(parents=True, exist_ok=True)
    (staging / svc.CONCEPTS_FILENAME).write_bytes(b"partial")
    removed = svc.recover_incomplete_publications()
    assert any("REL-crashed" in path for path in removed)
    assert not staging.exists()


def test_publication_lease_refuses_a_concurrent_publisher(db):
    release, _, _ = _fresh_release(db)
    target = (
        config.DATA_DIR / "assessment_releases" / release.release_uid
        / f"v{release.version}.staging")
    target.mkdir(parents=True, exist_ok=True)
    with pytest.raises(svc.UploadRefused, match="already being published"):
        svc.publish_release(db, release)
    target.rmdir()


def test_flagged_content_publishes_with_warnings_and_still_uploads(db):
    release, _, _ = _fresh_release(db, flags=["unresolved_author_critic"])
    published = svc.publish_release(db, release)
    assert published.state == "validated_with_flags"
    assert published.diagnostics["readiness"] == svc.RELEASED_WITH_WARNINGS
    result = svc.upload_master_to_database(db, published, owner_sub=OWNER)
    assert result["questions_created"] == 1
    assert published.state == "uploaded"


def test_staged_master_waits_for_exact_output01_publication(db):
    chapter = _chapter_concept(db).topic.chapter
    token = uuid.uuid4().hex[:12]
    concept_key = f"release:{token}:0001"
    machine_id = f"REL{token.upper()}C001"
    concept_name = f"Staged concept {token}"
    topic_title = f"Staged topic {token}"
    label = f"STAGEDQ {token}"
    payload = _payload(
        concept_key,
        machine_id,
        concept_name,
        label=label,
    )
    payload["concept_snapshot"] = {
        "source_concept_release_sha256": "a" * 64,
        "target_chapter_id": chapter.id,
        "concept_provenance": [{
            "concept_key": concept_key,
            "release_row_identity": {"position": 1},
        }],
        "chapter": {
            "chapter_title": chapter.chapter_title,
            "chapter_display_name": chapter.chapter_display_name,
            "pre_topics": chapter.pre_topics,
            "post_topics": chapter.post_topics,
            "chapter_description": chapter.chapter_description,
        },
        "topics": [{
            "topic_title": topic_title,
            "topic_display_name": topic_title,
            "pre_post_learning": "Post",
            "topic_concept_labels": "",
            "related_topics": "",
            "topic_description": "A staged topic description.",
            "concepts": [{
                "concept_key": concept_key,
                "concept_machine_id": machine_id,
                "concept_title": concept_name,
                "concept_display_name": concept_name,
                "parent_concept": "",
                "concept_details": "Exact staged teaching content.",
                "keywords": "staged, exact",
                "related_concepts": "",
                "digicards": "",
                "concept_source": "fixture",
            }],
        }],
    }
    release = svc.create_release(
        db,
        chapter_id=chapter.id,
        payload=payload,
        owner_sub=OWNER,
    )
    published = svc.publish_release(db, release)

    with pytest.raises(svc.UploadRefused, match="Output-01 identity"):
        svc.upload_master_to_database(db, published, owner_sub=OWNER)
    assert published.state == "ready_for_upload"

    topic = models.Topic(
        chapter_id=chapter.id,
        topic_title=topic_title,
        topic_display_name=topic_title,
        pre_post_learning="Post",
        topic_description="A staged topic description.",
    )
    concept = models.Concept(
        topic=topic,
        concept_title=concept_name,
        concept_display_name=concept_name,
        parent_concept="",
        concept_details="Exact staged teaching content.",
        keywords="staged, exact",
        sources="fixture",
    )
    db.add(concept)
    for tier in ag.TIER_CODES:
        db.add(models.Group(
            concept=concept,
            group_type=tier,
            group_key="",
            group_name=f"{concept_name} — {tier}",
            group_display_name=f"{concept_name} — {tier}",
            group_status="Active",
        ))
    db.commit()

    result = svc.upload_master_to_database(
        db, published, owner_sub=OWNER
    )
    assert result["questions_created"] == 1
    assert result["groups_created"] == 0
    assert db.query(models.Group).filter_by(concept_id=concept.id).count() == 3
    assert db.query(models.Question).filter_by(
        question_label=label
    ).one().group.concept_id == concept.id


def test_unresolved_placement_blocks_upload_but_keeps_downloads(db, client):
    def orphan(payload):
        payload["candidates"][0]["group_key"] = "(unknown) BG01"

    release, _, _ = _fresh_release(db, mutate=orphan)
    published = svc.publish_release(db, release)
    assert published.diagnostics["readiness"] == svc.BLOCKED
    with pytest.raises(svc.UploadRefused, match="blocked"):
        svc.upload_master_to_database(db, published, owner_sub=OWNER)
    response = client.get(
        f"/build-assessments/releases/{published.id}/master.xlsx")
    assert response.status_code == 200  # downloads survive a block


def test_upload_is_idempotent_and_hash_guarded(db):
    release, _, label = _fresh_release(db)
    published = svc.publish_release(db, release)
    first = svc.upload_master_to_database(db, published, owner_sub=OWNER)
    assert first["questions_created"] == 1
    question = db.query(models.Question).filter_by(
        question_label=label).one()
    assert question.answer_restriction == "Specific"
    assert question.blueprint_cell_id == "CELL-rel1"
    assert question.origin == "assessment_release"
    assert question.group.group_key.endswith("BG01")
    concept_name = question.group.concept.concept_display_name
    assert question.group.group_name == question.group.group_display_name == (
        f"{concept_name} — Basic")

    again = svc.upload_master_to_database(db, published, owner_sub=OWNER)
    assert again == first
    assert db.query(models.Question).filter_by(
        question_label=label).count() == 1

    # Tampering with the served artifact refuses any further upload path.
    directory = Path(published.publication["directory"])
    master = directory / svc.MASTER_FILENAME
    master.write_bytes(master.read_bytes() + b"tampered")
    published.publication = {
        k: v for k, v in published.publication.items()
        if k not in {"uploaded_key", "uploaded_result"}
    }
    db.commit()
    with pytest.raises(svc.UploadRefused, match="no longer matches"):
        svc.upload_master_to_database(db, published, owner_sub=OWNER)


def test_upload_refreshes_an_existing_group_without_replacing_its_identity(db):
    release, _, _ = _fresh_release(db)
    published = svc.publish_release(db, release)
    basic = next(
        group for group in published.concept_snapshot["groups"]
        if group["group_type"] == "Basic"
    )
    concept_id = int(str(basic["concept_key"]).removeprefix("db:"))
    record = db.query(models.Group).filter_by(
        concept_id=concept_id,
        group_type="Basic",
        group_key=basic["group_key"],
    ).one_or_none()
    if record is None:
        record = models.Group(
            concept_id=concept_id,
            group_type="Basic",
            group_key=basic["group_key"],
        )
        db.add(record)
        db.flush()
    record.group_name = basic["group_key"]
    record.group_display_name = basic["group_key"]
    record.group_sequence = 99
    record.group_status = "Inactive"
    db.commit()
    existing_id = record.id

    svc.upload_master_to_database(db, published, owner_sub=OWNER)

    db.refresh(record)
    assert record.id == existing_id
    assert record.group_key == basic["group_key"]
    assert record.group_name == record.group_display_name == (
        basic["group_name"])
    assert record.group_sequence == basic["group_sequence"]
    assert record.group_status == basic["group_status"]


def test_upload_rejects_a_cross_home_group_key_collision_and_rolls_back(db):
    token = uuid.uuid4().hex

    def unique_late_group(payload):
        advanced = next(
            group for group in payload["groups"]
            if group["group_type"] == "Advanced")
        advanced["group_key"] = f"(COLLISION-{token}) AG02"
        advanced["group_sequence"] = 2

    release, _, label = _fresh_release(db, mutate=unique_late_group)
    published = svc.publish_release(db, release)
    advanced = next(
        group for group in published.concept_snapshot["groups"]
        if group["group_key"] == f"(COLLISION-{token}) AG02")
    home_id = int(str(advanced["concept_key"]).removeprefix("db:"))
    other_concept = db.query(models.Concept).filter(
        models.Concept.id != home_id).order_by(models.Concept.id).first()
    assert other_concept is not None
    collision = models.Group(
        concept_id=other_concept.id,
        group_type="Basic",
        group_key=advanced["group_key"],
        group_sequence=1,
        group_name="Existing friendly name",
        group_display_name="Existing friendly name",
        group_status="Active",
    )
    db.add(collision)
    db.commit()
    group_count = db.query(models.Group).count()

    with pytest.raises(svc.UploadRefused, match="already belongs"):
        svc.upload_master_to_database(db, published, owner_sub=OWNER)

    assert db.query(models.Group).count() == group_count
    assert db.query(models.Question).filter_by(
        question_label=label).count() == 0
    db.refresh(published)
    assert published.state == "ready_for_upload"
    assert "uploaded_key" not in (published.publication or {})

    db.delete(collision)
    db.commit()


def test_new_version_supersedes_the_old(db):
    release, payload, _ = _fresh_release(db)
    concept = _chapter_concept(db)
    next_version = svc.create_release(
        db,
        chapter_id=concept.topic.chapter_id,
        payload=copy.deepcopy(payload),
        owner_sub=OWNER,
        supersedes=release,
    )
    assert next_version.release_uid == release.release_uid
    assert next_version.version == release.version + 1
    assert release.state == "superseded"
    assert release.superseded_at is not None


def test_generation_never_calls_the_upload_action():
    source = Path(
        "app/services/build_assessments.py").read_text(encoding="utf-8")
    concepts_source = Path(
        "app/services/build_concepts.py").read_text(encoding="utf-8")
    for text in (source, concepts_source):
        assert "upload_master_to_database" not in text

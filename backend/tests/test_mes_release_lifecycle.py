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


def _payload(concept_key: str, *, label: str, flags=None) -> dict:
    machine = "RELTESTC"
    groups = []
    for shell in ag.required_shells(concept_key, machine):
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
        "question_appears_in": "Pre/Post-Worksheet/Test",
        "answer_restriction": "Specific",
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
        "group_key": f"({machine}) BG01",
        "flags": list(flags or []),
    }
    return {"groups": groups, "candidates": [candidate]}


def _fresh_release(db, *, flags=None, mutate=None):
    concept = _chapter_concept(db)
    concept_key = f"db:{concept.id}"
    label = f"RELQ {uuid.uuid4().hex[:10]}"
    payload = _payload(concept_key, label=label, flags=flags)
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

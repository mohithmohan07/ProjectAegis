"""Q41 reviewed Post-bank handoff into the assessment Master route."""
from __future__ import annotations

from app.services import assessment_release_snapshot as snapshot
from app.services import assessment_routing as routing
from app.services import assessment_source_inventory as source_inventory
from app.services import build_concepts_release

from tests.test_assessment_release_run import (
    OWNER,
    _authorities,
    _chapter_with_concepts,
    _decision_context,
    _make_job,
)


def _target(index: int = 1, *, title: str = "Target concept", topic: str = "Topic B"):
    return {
        "concept_row_index": index,
        "concept_title": title,
        "topic": topic,
        "type_id": "TYPE-2",
        "type_title": "Target type",
        "type_definition": "The accepted Type definition.",
        "case_id": "CASE-2",
        "case_definition": "The accepted Case definition.",
        "preserve_source_dependencies": True,
    }


def test_reviewed_source_atom_preserves_frozen_wording_and_actual_route_target():
    target = _target()
    inventory = {
        "items": [{
            "qid": "Q-KEEP",
            "raw_task": "Old wording",
            "normalized_task": "Old wording",
            "polished_task": "Old wording",
            "source_task_polishing_policy": "source-task-format-2026-09-09-v1",
            "frozen_task_text": "Edited frozen wording",
            "_aegis_reviewed_target": target,
        }],
        "reviewed_source_questions": {
            "version": "reviewed-source-questions-1",
            "original_ids": ["Q-KEEP", "Q-OMIT"],
            "reviewed_ids": ["Q-KEEP"],
            "omitted": ["Q-OMIT"],
        },
        "type_case_rows": [{
            "row_kind": "example", "type_id": "TYPE-2", "case_id": "CASE-2",
            "example_qid": "Q-KEEP",
        }],
    }
    built = source_inventory.build_source_atoms(
        inventory, source_document_hash="sha256:source"
    )
    assert [atom["source_qid"] for atom in built["atoms"]] == ["Q-KEEP"]
    atom = built["atoms"][0]
    assert atom["normalized_public_text"] == "Edited frozen wording"
    assert atom["reviewed_target"] == target
    assert atom["route_evidence"]["reviewed_target"] == target
    assert source_inventory.has_reviewed_source_bank(inventory)


def test_reviewed_target_joins_exact_staged_record_and_rejects_title_fallback():
    concepts = [
        {"concept_key": "K0", "source_record_index": 0,
         "topic_title": "Topic A", "concept_title": "Target concept"},
        {"concept_key": "K1", "source_record_index": 1,
         "topic_title": "Topic B", "concept_title": "Target concept"},
    ]
    key, error = snapshot.reviewed_target_concept_key(_target(), concepts)
    assert key == "K1"
    assert error == ""

    stale = _target(title="Different title")
    key, error = snapshot.reviewed_target_concept_key(stale, concepts)
    assert key == ""
    assert "disagrees" in error


def test_reviewed_target_route_is_mechanical_and_never_calls_provider():
    target = _target(index=1)
    calls = []

    result = routing.route_candidate(
        {
            "candidate_id": "C-KEEP",
            "reviewed_target": target,
            "blueprint_concept_key": "K1",
        },
        [
            {"concept_key": "K0"},
            {"concept_key": "K1"},
        ],
        meta={}, envelope_sha256="e" * 64,
        provider=lambda payload: calls.append(payload) or {},
    )
    assert result["concept_key"] == "K1"
    assert result["basis"] == "reviewed_target"
    assert calls == []


def test_stale_reviewed_target_is_unplaced_without_semantic_fallback():
    calls = []
    result = routing.route_candidate(
        {
            "candidate_id": "C-STALE",
            "reviewed_target": _target(title="Missing concept"),
            "reviewed_target_error": "reviewed target does not join",
        },
        [{"concept_key": "K0"}, {"concept_key": "K1"}],
        meta={}, envelope_sha256="e" * 64,
        provider=lambda payload: calls.append(payload) or {},
    )
    assert result["concept_key"] is None
    assert result["flags"] == ["reviewed_target_unresolved"]
    assert calls == []


def test_empty_reviewed_bank_is_distinct_from_historical_empty_inventory():
    reviewed = {
        "items": [],
        "reviewed_source_questions": {
            "version": "reviewed-source-questions-1",
            "original_ids": ["Q-OMIT"], "reviewed_ids": [], "omitted": ["Q-OMIT"],
        },
    }
    assert source_inventory.has_reviewed_source_bank(reviewed)
    assert source_inventory.build_source_atoms(
        reviewed, source_document_hash="sha256:source"
    )["atoms"] == []
    assert not source_inventory.has_reviewed_source_bank({"items": []})


def test_reviewed_inventory_target_reaches_the_actual_master_concept(db):
    chapter = _chapter_with_concepts(db)
    job = _make_job(db, chapter)
    payload = build_concepts_release.release_payload(job, lane="post")
    inventory = dict(payload["question_task_inventory"])
    items = [dict(item) for item in inventory["items"]]
    items[0]["_aegis_reviewed_target"] = _target(
        index=1, title="Plane Shapes", topic="Shapes"
    )
    inventory["items"] = items
    inventory["reviewed_source_questions"] = {
        "version": "reviewed-source-questions-1",
        "original_ids": ["QINV-0001", "QINV-0002"],
        "reviewed_ids": ["QINV-0001", "QINV-0002"],
        "omitted": [],
    }
    payload["question_task_inventory"] = inventory
    durable = dict(job.question_inventory or {})
    durable[build_concepts_release.RELEASE_KEY] = payload
    job.question_inventory = durable
    db.commit()

    calls = {}
    authorities, _ = _authorities(db, chapter, calls=calls)
    release = __import__(
        "app.services.assessment_release_run", fromlist=["run_release_for_job"]
    ).run_release_for_job(
        db, job.id, owner_sub=OWNER, authorities=authorities,
        **_decision_context(),
    )
    candidate = next(
        row for row in release.payload["candidates"]
        if row.get("source_qid") == "QINV-0001"
    )
    target_concept = next(
        row for topic in release.concept_snapshot["topics"]
        for row in topic.get("concepts") or []
        if row.get("concept_title") == "Plane Shapes"
    )
    assert candidate["concept_key"] == target_concept["concept_key"]
    assert candidate["_aegis_assessment_route"]["basis"] == "reviewed_target"
    # The other source question still needs the ordinary semantic route; the
    # accepted reviewed target itself never enters that provider path.
    assert all(
        row["candidate"].get("source_qid") != "QINV-0001"
        for row in calls.get("route", [])
    )


def test_intentionally_empty_reviewed_post_bank_runs_without_question_authority(db):
    chapter = _chapter_with_concepts(db)
    job = _make_job(db, chapter)
    payload = build_concepts_release.release_payload(job, lane="post")
    inventory = dict(payload["question_task_inventory"])
    inventory["items"] = []
    inventory["reviewed_source_questions"] = {
        "version": "reviewed-source-questions-1",
        "original_ids": ["QINV-0001", "QINV-0002"],
        "reviewed_ids": [],
        "omitted": ["QINV-0001", "QINV-0002"],
    }
    payload["question_task_inventory"] = inventory
    durable = dict(job.question_inventory or {})
    durable[build_concepts_release.RELEASE_KEY] = payload
    job.question_inventory = durable
    db.commit()

    calls = {}
    authorities, _ = _authorities(db, chapter, calls=calls)
    release = __import__(
        "app.services.assessment_release_run", fromlist=["run_release_for_job"]
    ).run_release_for_job(
        db, job.id, owner_sub=OWNER, authorities=authorities,
        **_decision_context(),
    )
    assert release.payload["source_atoms"] == []
    assert release.payload["candidates"] == []
    assert "cells" not in calls
    assert "materialize" not in calls
    assert "route" not in calls

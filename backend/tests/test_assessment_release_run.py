"""End-to-end orchestration: one generated job -> two published files.

Every semantic stage is driven by scripted (author, critic) pairs, so the
complete pipeline — atoms, cell classification, materialization, routing,
level verdicts, clustering, descriptions, QA, release, atomic publication —
runs offline exactly as wired for live use.
"""
from __future__ import annotations

import copy
import json
from collections import Counter
from pathlib import Path

import pytest

from app import models
from app.bulk_import import assessment_workbook as aw
from app.services import assessment_release_run as run
from app.services import assessment_release_service as svc
from app.services.phase3 import kernel

OWNER = "local:default"
ENVELOPE_SHA256 = "e" * 64


def _accepting_critic(system, user):
    payload = json.loads(user)
    return {"verdict": "accept",
            "proposal_sha256": payload["proposal_sha256"], "feedback": []}


def _decision_context(store=None):
    return {
        "envelope_sha256": ENVELOPE_SHA256,
        "decision_store": store or kernel.DecisionStore(),
    }


def _chapter_with_concepts(db):
    concept = (
        db.query(models.Concept).join(models.Topic)
        .order_by(models.Concept.id).first()
    )
    assert concept is not None
    return concept.topic.chapter


def _make_job(db, chapter) -> models.UploadJob:
    job = models.UploadJob(
        owner_sub=OWNER,
        module="build_concepts",
        upload_type="textbook",
        filename="ch.mmd",
        mmd_text="# Chapter\n\nExercise 1. Which of these is a solid?",
        status="generated",
        deposit_scope_type="chapter",
        deposit_scope_ids=[chapter.id],
        question_inventory={
            "items": [
                {
                    "qid": "QINV-0001",
                    "source_kind": "exercise",
                    "source_label": "Exercise 1(1)",
                    "raw_task": "Which of these is a solid?",
                    "options": ["Cube", "Circle"],
                },
                {
                    "qid": "QINV-0002",
                    "source_kind": "exercise",
                    "source_label": "Exercise 1(2)",
                    "raw_task": "Explain why a cube is a solid.",
                },
            ],
        },
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def _authorities(db, chapter, *, calls=None, qa_payloads=None):
    """Scripted authority pairs for every stage."""
    calls = calls if calls is not None else {}
    qa_payloads = qa_payloads if qa_payloads is not None else []
    first_concept = sorted(
        (c for t in chapter.topics for c in t.concepts),
        key=lambda c: c.id)[0]

    def record(stage, payload):
        calls.setdefault(stage, []).append(copy.deepcopy(payload))

    def cell_author(system, user):
        atom = json.loads(user.split("\n\nYOUR PREVIOUS")[0])["source_atom"]
        objective = bool(atom.get("options"))
        return {
            "sheet_kind": "objective" if objective else "descriptive",
            "question_category": (
                "Multiple Choice Question" if objective else "Long Answer"),
            "cognitive_skill": "Remember" if objective else "Understand",
            "difficulty": "Less" if objective else "Moderate",
            "marks": 1 if objective else 3,
            "reason": "scripted",
        }

    def materialize_author(system, user):
        payload = json.loads(user.split("\n\nYOUR PREVIOUS")[0])
        cell = payload["blueprint_cell"]
        atom = payload["source_atom"]
        if cell["sheet_kind"] == "objective":
            return {
                "question": atom["normalized_public_text"],
                "answer_restriction": "Specific",
                "restriction_reason": "one closed choice",
                "display_answer": "",
                "answers": [
                    {"answer_type": "Phrases", "answer_content": "Cube",
                     "correct_answer": "Yes", "answer_weightage": "1"},
                    {"answer_type": "Phrases", "answer_content": "Circle",
                     "correct_answer": "No", "answer_weightage": "0"},
                ],
                "sub_questions": [],
                "answer_explanation": "A cube is three-dimensional.",
                "requires_visual": False,
            }
        return {
            "question": atom["normalized_public_text"],
            "answer_restriction": "Open",
            "restriction_reason": "several valid explanations",
            "display_answer": "A cube occupies space in three dimensions.",
            "answers": [
                {"answer_type": "Phrases", "answer_weightage": "3",
                 "answer_content": "three dimensions named and justified"},
            ],
            "sub_questions": [],
            "answer_explanation": "",
            "requires_visual": False,
        }

    def router(system, user):
        return {"concept_id": first_concept.id,
                "evidence": "teaches solids", "reason": "scripted",
                "confidence": "high"}

    def level_author(payload):
        record("level", payload)
        return {
            "candidate_id": payload["candidate"]["candidate_id"],
            # Deliberately contrary to both scripted blueprint difficulties:
            # the recorded level verdict, not difficulty, owns the tier.
            "tier": "Advanced",
            "rationale": "the complete assessment evidence supports it",
        }

    def cluster_author(payload):
        record("cluster", payload)
        return {
            "concept_key": payload["concept"]["concept_key"],
            "tier": payload["tier"],
            "families": [
                {
                    "existing_group_key": "",
                    "family": f"scripted variant {index}",
                    "member_candidate_ids": [candidate["candidate_id"]],
                }
                for index, candidate in enumerate(
                    payload["candidates"], start=1
                )
            ],
            "rationale": "the recorded families preserve distinct variants",
        }

    def describe_author(payload):
        record("describe", payload)
        return {
            "group_key": payload["group"]["group_key"],
            "description": (
                "Responding to a solid-shape question by identifying or "
                "explaining three-dimensional form."
            ),
            "rationale": "the wording states how and what is assessed",
        }

    def qa_reviewer(payload):
        record("qa", payload)
        qa_payloads.append(copy.deepcopy(payload))
        return {"group_key": payload["group"]["group_key"], "flags": []}

    def verified_critic(payload):
        record("critic", payload)
        return {"verdict": "verified", "confidence": 1.0, "issues": []}

    return {
        "cells": (cell_author, _accepting_critic),
        "materialize": (materialize_author, _accepting_critic),
        "route": (router, _accepting_critic),
        "level": (level_author, verified_critic),
        "cluster": (cluster_author, verified_critic),
        "describe": (describe_author, verified_critic),
        "qa": (qa_reviewer, verified_critic),
    }, first_concept


def test_full_pipeline_publishes_a_ready_release(db):
    chapter = _chapter_with_concepts(db)
    job = _make_job(db, chapter)
    calls = {}
    qa_payloads = []
    authorities, first_concept = _authorities(
        db, chapter, calls=calls, qa_payloads=qa_payloads)

    release = run.run_release_for_job(
        db, job.id, owner_sub=OWNER, authorities=authorities,
        **_decision_context())

    assert release.state in {"ready_for_upload", "validated_with_flags"}
    assert (release.diagnostics or {}).get("readiness") == svc.READY
    directory = Path(release.publication["directory"])
    master = aw.parse_workbook(
        (directory / svc.MASTER_FILENAME).read_bytes())

    objective_rows = [
        r for r in master["sheets"]["Objective"]["rows"]
        if r.get("question_label")]
    descriptive_rows = [
        r for r in master["sheets"]["Descriptive"]["rows"]
        if r.get("question_label")]
    assert len(objective_rows) == 1
    assert len(descriptive_rows) == 1
    assert master["sheets"]["Subjective"]["rows"] == []

    q = objective_rows[0]
    assert q["question_appears_in"] == "Pre/Post-Worksheet/Test"
    assert q["answer_restriction"] == "Specific"
    assert str(q["correct_answer_1"]) == "Yes"
    # Labels mint from the concept machine identity in source order.
    assert q["question_label"].endswith("Q01")
    assert descriptive_rows[0]["question_label"].endswith(
        ("Q01", "Q02"))
    # Both questions carry the model-authored Advanced tier even though their
    # blueprint difficulties are Less and Moderate. Two authored variant
    # families occupy that tier; the remaining required shells stay NA.
    group_rows = [
        r for r in master["sheets"]["Objective"]["rows"]
        if r.get("group_name") and not r.get("question_label")]
    assert any(r["group_description"] == "NA" for r in group_rows)
    assert q["group_description"].startswith(
        "Responding to a solid-shape question")

    payload = release.payload
    assert len(payload["source_atoms"]) == 2
    assert len(payload["blueprint_cells"]) == 2
    assert all(p["group_key"] for p in payload["placements"])
    # Everything belongs to the first concept's home.
    assert all(
        p["concept_id"] == first_concept.id
        for p in payload["placements"])

    assert {
        (
            candidate["difficulty"],
            candidate["_aegis_assessment_level_verdict"]["tier"],
        )
        for candidate in payload["candidates"]
    } == {("Less", "Advanced"), ("Moderate", "Advanced")}
    assert all(
        candidate["question"] == candidate["question_text"]
        for candidate in payload["candidates"]
    )
    assert {
        candidate["question"] for candidate in payload["candidates"]
    } == {
        "Which of these is a solid?",
        "Explain why a cube is a solid.",
    }

    occupied = [
        group for group in payload["groups"]
        if group.get("member_candidate_ids")
    ]
    assert len(occupied) == 2
    candidate_ids = [
        candidate["candidate_id"] for candidate in payload["candidates"]
    ]
    grouped_ids = [
        candidate_id
        for group in occupied
        for candidate_id in group["member_candidate_ids"]
    ]
    assert Counter(grouped_ids) == Counter(candidate_ids)
    assert all(group["group_type"] == "Advanced" for group in occupied)
    assert all(
        group["group_name"] == group["group_display_name"]
        == f"{first_concept.concept_display_name} — Advanced"
        for group in occupied
    )
    for candidate in payload["candidates"]:
        home = next(
            group for group in occupied
            if candidate["candidate_id"] in group["member_candidate_ids"]
        )
        assert candidate["group_key"] == home["group_key"]
        authority = candidate[
            "_aegis_assessment_level_verdict"]["authority"]
        assert authority["decision_key"]
        assert authority["policy_version"] == "assessment-level-1"
        assert "created_at" not in authority
        assert "provider" not in authority
    for group in occupied:
        assert group["_aegis_assessment_variant_cluster"]["authority"][
            "policy_version"] == "assessment-variant-cluster-1"
        assert group["_aegis_assessment_group_description"]["authority"][
            "policy_version"] == "assessment-group-description-1"
        assert group["_aegis_assessment_group_quality"]["authority"][
            "policy_version"] == "assessment-group-quality-1"

    sibling_map = {
        payload["group"]["group_key"]: {
            sibling["group"]["group_key"]
            for sibling in payload["sibling_groups"]
        }
        for payload in qa_payloads
    }
    occupied_keys = {group["group_key"] for group in occupied}
    assert set(sibling_map) == occupied_keys
    assert all(
        siblings == occupied_keys - {group_key}
        for group_key, siblings in sibling_map.items()
    )


def test_multi_member_family_keeps_each_candidate_exactly_once(db):
    chapter = _chapter_with_concepts(db)
    job = _make_job(db, chapter)
    authorities, _ = _authorities(db, chapter)

    def joined_family(payload):
        return {
            "concept_key": payload["concept"]["concept_key"],
            "tier": payload["tier"],
            "families": [{
                "existing_group_key": "",
                "family": "recorded joined family",
                "member_candidate_ids": [
                    candidate["candidate_id"]
                    for candidate in payload["candidates"]
                ],
            }],
            "rationale": "the recorded verdict treats both as variants",
        }

    authorities["cluster"] = (
        joined_family, authorities["cluster"][1])
    release = run.run_release_for_job(
        db,
        job.id,
        owner_sub=OWNER,
        authorities=authorities,
        **_decision_context(),
    )

    candidate_ids = [
        candidate["candidate_id"]
        for candidate in release.payload["candidates"]
    ]
    occupied = [
        group for group in release.payload["groups"]
        if group.get("member_candidate_ids")
    ]
    assert len(occupied) == 1
    assert Counter(occupied[0]["member_candidate_ids"]) == Counter(
        candidate_ids)
    assert all(
        candidate["group_key"] == occupied[0]["group_key"]
        for candidate in release.payload["candidates"]
    )


def test_internal_group_id_cannot_reach_visible_description(db):
    chapter = _chapter_with_concepts(db)
    job = _make_job(db, chapter)
    authorities, _ = _authorities(db, chapter)
    fixer_calls = []

    def leaking_description(payload):
        group_key = payload["group"]["group_key"]
        return {
            "group_key": group_key,
            "description": f"Assessment family {group_key}",
            "rationale": "Leaks the supplied machine identity on purpose.",
        }

    def description_fixer(payload):
        assert payload["contract"]["kind"] == "assessment.group_description"
        fixer_calls.append(copy.deepcopy(payload))
        return {
            "group_key": payload["original_payload"]["group"]["group_key"],
            "description": (
                "Identifying or explaining three-dimensional solid form."
            ),
            "rationale": "The visible wording contains no machine identity.",
        }

    authorities["describe"] = (
        leaking_description, authorities["describe"][1])
    authorities["fixer"] = (description_fixer,)
    release = run.run_release_for_job(
        db,
        job.id,
        owner_sub=OWNER,
        authorities=authorities,
        **_decision_context(),
    )

    occupied = [
        group for group in release.payload["groups"]
        if group.get("member_candidate_ids")
    ]
    assert len(fixer_calls) == len(occupied)
    assert all(
        group["_aegis_assessment_group_description"]["authority"]["fixer"]
        for group in occupied
    )
    directory = Path(release.publication["directory"])
    master = aw.parse_workbook(
        (directory / svc.MASTER_FILENAME).read_bytes())
    visible_descriptions = {
        str(row.get("group_description") or "")
        for sheet in master["sheets"].values()
        for row in sheet["rows"]
    }
    for group in occupied:
        assert all(
            group["group_key"] not in description
            for description in visible_descriptions
        )


def test_grouping_decisions_replay_without_provider_calls(db, tmp_path):
    chapter = _chapter_with_concepts(db)
    job = _make_job(db, chapter)
    calls = {}
    authorities, _ = _authorities(db, chapter, calls=calls)
    store_directory = tmp_path / "phase3-decisions"

    first = run.run_release_for_job(
        db,
        job.id,
        owner_sub=OWNER,
        authorities=authorities,
        **_decision_context(kernel.DecisionStore(store_directory)),
    )
    expected_counts = {
        "level": 2,
        "cluster": 1,
        "describe": 2,
        "qa": 2,
        "critic": 7,
    }
    assert {key: len(calls.get(key, [])) for key in expected_counts} == (
        expected_counts
    )
    first_audits = {
        candidate["candidate_id"]: copy.deepcopy(
            candidate["_aegis_assessment_level_verdict"]
        )
        for candidate in first.payload["candidates"]
    }
    first_text = {
        candidate["candidate_id"]: (
            candidate["question"], candidate["question_text"]
        )
        for candidate in first.payload["candidates"]
    }
    levels_path = tmp_path / "source.phase3-assessment-levels.json"
    groups_path = tmp_path / "source.phase3-assessment-groups.json"
    assert levels_path.is_file()
    assert groups_path.is_file()
    snapshot_bytes = (levels_path.read_bytes(), groups_path.read_bytes())
    assert b"created_at" not in snapshot_bytes[0] + snapshot_bytes[1]

    second = run.run_release_for_job(
        db,
        job.id,
        owner_sub=OWNER,
        authorities=authorities,
        **_decision_context(kernel.DecisionStore(store_directory)),
    )

    assert {key: len(calls.get(key, [])) for key in expected_counts} == (
        expected_counts
    )
    assert snapshot_bytes == (
        levels_path.read_bytes(), groups_path.read_bytes())
    assert {
        candidate["candidate_id"]: candidate[
            "_aegis_assessment_level_verdict"]
        for candidate in second.payload["candidates"]
    } == first_audits
    assert {
        candidate["candidate_id"]: (
            candidate["question"], candidate["question_text"]
        )
        for candidate in second.payload["candidates"]
    } == first_text


def test_unroutable_candidate_blocks_upload_but_publishes(db):
    chapter = _chapter_with_concepts(db)
    job = _make_job(db, chapter)
    authorities, _ = _authorities(db, chapter)

    def rejecting_critic(system, user):
        payload = json.loads(user)
        return {"verdict": "reject",
                "proposal_sha256": payload["proposal_sha256"],
                "feedback": ["the concept does not teach this"]}

    authorities["route"] = (authorities["route"][0], rejecting_critic)
    release = run.run_release_for_job(
        db, job.id, owner_sub=OWNER, authorities=authorities,
        **_decision_context())

    assert (release.diagnostics or {}).get("readiness") == svc.BLOCKED
    directory = Path(release.publication["directory"])
    assert (directory / svc.MASTER_FILENAME).is_file()  # downloads survive
    with pytest.raises(svc.UploadRefused, match="blocked"):
        svc.upload_master_to_database(db, release, owner_sub=OWNER)


def test_job_without_inventory_is_refused(db):
    chapter = _chapter_with_concepts(db)
    job = models.UploadJob(
        owner_sub=OWNER, module="build_concepts", upload_type="textbook",
        filename="x.mmd", mmd_text="#", status="generated",
        deposit_scope_type="chapter", deposit_scope_ids=[chapter.id],
        question_inventory={},
    )
    db.add(job)
    db.commit()
    with pytest.raises(run.ReleaseRunError, match="no question/task"):
        run.run_release_for_job(db, job.id, owner_sub=OWNER)


def test_zero_loss_across_the_whole_run(db):
    """Every inventory QID reaches the release payload exactly once."""
    chapter = _chapter_with_concepts(db)
    job = _make_job(db, chapter)
    authorities, _ = _authorities(db, chapter)
    release = run.run_release_for_job(
        db, job.id, owner_sub=OWNER, authorities=authorities,
        **_decision_context())
    payload = release.payload
    inventory_qids = {"QINV-0001", "QINV-0002"}
    assert {a["source_qid"] for a in payload["source_atoms"]} == (
        inventory_qids)
    covered = {
        qid
        for candidate in payload["candidates"]
        for qid in candidate.get("source_atom_ids") or []
    }
    assert covered == inventory_qids
    candidate_ids = [
        candidate["candidate_id"] for candidate in payload["candidates"]
    ]
    grouped_ids = [
        candidate_id
        for group in payload["groups"]
        for candidate_id in group.get("member_candidate_ids") or []
    ]
    assert Counter(grouped_ids) == Counter(candidate_ids)

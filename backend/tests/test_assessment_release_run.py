"""End-to-end orchestration: one generated job -> two published files.

Every semantic stage is driven by scripted (author, critic) pairs, so the
complete pipeline — atoms, cell classification, materialization, routing,
tiering, clustering, descriptions, QA, release, atomic publication — runs
offline exactly as wired for live use.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import models
from app.bulk_import import assessment_workbook as aw
from app.services import assessment_release_run as run
from app.services import assessment_release_service as svc

OWNER = "local:default"


def _accepting_critic(system, user):
    payload = json.loads(user)
    return {"verdict": "accept",
            "proposal_sha256": payload["proposal_sha256"], "feedback": []}


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


def _authorities(db, chapter):
    """Scripted authority pairs for every stage."""
    first_concept = sorted(
        (c for t in chapter.topics for c in t.concepts),
        key=lambda c: c.id)[0]

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

    def cluster_author(system, user):
        payload = json.loads(user.split("\n\nYOUR PREVIOUS")[0])
        return {"families": [{
            "existing_group_key": "",
            "family": "scripted family",
            "member_candidate_ids": [
                c["candidate_id"] for c in payload["candidates"]],
        }]}

    def describe_author(system, user):
        return {"description": (
            "Identification of solid shapes from everyday objects.")}

    def qa_reviewer(system, user):
        return {"flags": []}

    return {
        "cells": (cell_author, _accepting_critic),
        "materialize": (materialize_author, _accepting_critic),
        "route": (router, _accepting_critic),
        "cluster": (cluster_author, _accepting_critic),
        "describe": (describe_author, _accepting_critic),
        "qa": (qa_reviewer,),
    }, first_concept


def test_full_pipeline_publishes_a_ready_release(db):
    chapter = _chapter_with_concepts(db)
    job = _make_job(db, chapter)
    authorities, first_concept = _authorities(db, chapter)

    release = run.run_release_for_job(
        db, job.id, owner_sub=OWNER, authorities=authorities)

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
    # Groups: both questions routed to one concept, two tiers occupied,
    # descriptions authored; the concept's remaining shell stays NA.
    group_rows = [
        r for r in master["sheets"]["Objective"]["rows"]
        if r.get("group_name") and not r.get("question_label")]
    assert any(r["group_description"] == "NA" for r in group_rows)
    assert q["group_description"].startswith("Identification of solid")

    payload = release.payload
    assert len(payload["source_atoms"]) == 2
    assert len(payload["blueprint_cells"]) == 2
    assert all(p["group_key"] for p in payload["placements"])
    # Everything belongs to the first concept's home.
    assert all(
        p["concept_id"] == first_concept.id
        for p in payload["placements"])


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
        db, job.id, owner_sub=OWNER, authorities=authorities)

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
        db, job.id, owner_sub=OWNER, authorities=authorities)
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

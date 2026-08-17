"""End-to-end orchestration: one generated job -> two published files.

Every semantic stage is driven by scripted (author, critic) pairs, so the
complete pipeline — atoms, cell classification, semantic materialization,
answer restriction, marking, routing, level verdicts, clustering,
descriptions, QA, release, atomic publication — runs offline exactly as wired
for live use.
"""
from __future__ import annotations

import copy
from collections import Counter
from pathlib import Path

import pytest

from app import models
from app.bulk_import import assessment_workbook as aw
from app.services import assessment_release as rel
from app.services import assessment_master_refiner
from app.services import assessment_release_run as run
from app.services import assessment_release_snapshot as release_snapshot
from app.services import assessment_release_service as svc
from app.services import build_concepts_release
from app.services import release_refiner
from app.services.phase3 import kernel

OWNER = "local:default"
ENVELOPE_SHA256 = "e" * 64


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
    inventory = {
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
    }
    job = models.UploadJob(
        owner_sub=OWNER,
        module="build_concepts",
        upload_type="textbook",
        filename="ch.mmd",
        mmd_text="# Chapter\n\nExercise 1. Which of these is a solid?",
        status="generated",
        deposit_scope_type="chapter",
        deposit_scope_ids=[chapter.id],
        question_inventory=inventory,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    build_concepts_release.stage_release(
        db,
        job,
        target_chapter_id=chapter.id,
        records=[
            {
                "topic": "Shapes",
                "concept_title": "Solid Shapes",
                "concept_details": (
                    "Solid shapes occupy space in three dimensions; cubes "
                    "are examples."
                ),
                "keywords": "solid, cube, three dimensions",
            },
            {
                "topic": "Shapes",
                "concept_title": "Plane Shapes",
                "concept_details": (
                    "Plane shapes are flat, two-dimensional figures."
                ),
                "keywords": "plane, flat, two dimensions",
            },
        ],
        inventory=inventory,
        reason="recorded Output-01 fixture",
    )
    return job


def _authorities(db, chapter, *, calls=None, qa_payloads=None):
    """Scripted authority pairs for every stage."""
    calls = calls if calls is not None else {}
    qa_payloads = qa_payloads if qa_payloads is not None else []
    def record(stage, payload):
        calls.setdefault(stage, []).append(copy.deepcopy(payload))

    def cell_author(payload):
        record("cells", payload)
        atom = payload["source_atom"]
        objective = bool(atom.get("options"))
        return {
            "source_qid": atom["source_qid"],
            "sheet_kind": "objective" if objective else "descriptive",
            "question_category": (
                "Multiple Choice Question" if objective else "Long Answer"),
            "cognitive_skill": "Remember" if objective else "Understand",
            "difficulty": "Less" if objective else "Moderate",
            "marks": 1 if objective else 3,
            "rationale": "scripted from the complete source atom",
        }

    def materialize_author(payload):
        record("materialize", payload)
        cell = payload["blueprint_cell"]
        atom = payload["source_atom"]
        if cell["sheet_kind"] == "objective":
            return {
                "candidate_id": payload["candidate_id"],
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
                "rationale": "preserves the source question and answer",
            }
        return {
            "candidate_id": payload["candidate_id"],
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
            "rationale": "preserves the constructed-response obligation",
        }

    def answer_restriction_author(payload):
        record("answer_restriction", payload)
        candidate = payload["candidate"]
        objective = candidate["sheet_kind"] == "objective"
        return {
            "candidate_id": candidate["candidate_id"],
            "answer_restriction": "Specific" if objective else "Open",
            "restriction_reason": (
                "The response has one bounded correct option."
                if objective else
                "Materially different complete explanations can earn credit."
            ),
            "answer_space_contract": (
                "Select the one correct option."
                if objective else
                "Explain the defining property with valid supporting wording."
            ),
            "required_elements": [
                "the correct solid" if objective else "three dimensions"
            ],
            "accepted_variations": [
                "equivalent wording"
            ],
            "evidence": "The complete question and semantic rubric decide it.",
            "rationale": "Scripted evidence-bound answer-space verdict.",
            "review_required": False,
            "review_reason": "",
        }

    def marking_author(payload):
        record("marking", payload)
        candidate = payload["candidate"]
        cell = payload["blueprint_evidence"]["explicit_blueprint_cell"]
        answers = copy.deepcopy(candidate["answers"])
        sub_questions = copy.deepcopy(candidate["sub_questions"])
        if cell["sheet_kind"] == "objective":
            for answer in answers:
                answer["answer_weightage"] = (
                    cell["marks"]
                    if rel.is_correct_option(answer.get("correct_answer"))
                    else 0
                )
            duration = 2
            keyboard = ""
        else:
            assert len(answers) == 1
            answers[0]["answer_weightage"] = cell["marks"]
            duration = 5
            keyboard = "No"
        return {
            "candidate_id": candidate["candidate_id"],
            "question": candidate["question"],
            "question_text": candidate["question_text"],
            "answers": answers,
            "sub_questions": sub_questions,
            "question_duration": duration,
            "math_keyboard": keyboard,
            "rationale": "The explicit cell owns the complete decomposition.",
        }

    def router(payload):
        record("route", payload)
        return {
            "candidate_id": payload["candidate"]["candidate_id"],
            "concept_key": payload["candidate_concepts"][0]["concept_key"],
            "evidence": "the released description teaches solid shapes",
            "rationale": "the assessment concerns cubes as solid shapes",
        }

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

    def refiner_author(payload):
        record("refiner", payload)
        unit_kind = payload["unit_kind"]
        refined = copy.deepcopy(payload[unit_kind])
        if unit_kind == "candidate":
            if refined["sheet_kind"] == "objective":
                refined["answers"][0]["answer_content"] = "A cube"
                refined["answer_explanation"] = (
                    "A cube occupies space in three dimensions."
                )
            else:
                refined["display_answer"] = (
                    "A cube occupies space in all three dimensions."
                )
                refined["answers"][0]["answer_content"] = (
                    "Names and justifies all three dimensions."
                )
        else:
            refined["semantic_description"] = (
                str(refined.get("semantic_description") or "").rstrip(".")
                + ", expressed with precise grade-level wording."
            )
        return {
            "record_kind": unit_kind,
            "row_ref": payload["row_ref"],
            "record": refined,
            "rationale": "Polishes final prose without revisiting identity.",
        }

    def verified_critic(payload):
        record("critic", payload)
        return {"verdict": "verified", "confidence": 1.0, "issues": []}

    return {
        "cells": (cell_author, verified_critic),
        "materialize": (materialize_author, verified_critic),
        "answer_restriction": (
            answer_restriction_author, verified_critic
        ),
        "marking": (marking_author, verified_critic),
        "route": (router, verified_critic),
        "level": (level_author, verified_critic),
        "cluster": (cluster_author, verified_critic),
        "describe": (describe_author, verified_critic),
        "qa": (qa_reviewer, verified_critic),
        "refiner": (refiner_author, verified_critic),
    }, "Solid Shapes"


def test_full_pipeline_publishes_a_ready_release(db):
    chapter = _chapter_with_concepts(db)
    job = _make_job(db, chapter)
    calls = {}
    qa_payloads = []
    authorities, first_concept_name = _authorities(
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
    assert q["answer_content_1"] == "A cube"
    assert q["question_duration"] == 2
    assert str(q["correct_answer_1"]) == "Yes"
    # Labels mint from the concept machine identity in source order.
    assert q["question_label"].endswith("Q01")
    assert descriptive_rows[0]["question_label"].endswith(
        ("Q01", "Q02"))
    assert descriptive_rows[0]["answer_restriction"] == "Open"
    assert descriptive_rows[0]["question_duration"] == 5
    assert descriptive_rows[0]["math_keyboard"] == "No"
    assert descriptive_rows[0]["display_answer"] == (
        "A cube occupies space in all three dimensions."
    )
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
    # Everything belongs to the first immutable release concept's home.
    assert all(
        p["concept_id"] == p["concept_key"]
        and str(p["concept_key"]).startswith("release:")
        for p in payload["placements"])
    assert payload["source_concept_release_sha256"]
    assert payload["concept_snapshot"]["source_concept_release_sha256"] == (
        payload["source_concept_release_sha256"]
    )

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
        == f"{first_concept_name} — Advanced"
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
        assert candidate["_aegis_assessment_cell_verdict"]["authority"][
            "policy_version"
        ] == "assessment-cell-1"
        assert candidate["_aegis_assessment_materialization"]["authority"][
            "policy_version"
        ] == "assessment-materialize-2"
        restriction_authority = candidate[
            "_aegis_assessment_answer_restriction"
        ]["authority"]
        assert restriction_authority["policy_version"].startswith(
            "assessment-answer-restriction-2;"
        )
        assert candidate["_aegis_assessment_answer_restriction"][
            "registry"
        ]["registry_id"] == "registry-v2.0"
        assert candidate["_aegis_assessment_marking"]["authority"][
            "policy_version"
        ] == "assessment-marking-3"
        assert candidate["_aegis_assessment_marking"][
            "blueprint_authority"
        ]["decomposition_authority"] == "api_per_item_verdict"
        assert candidate["_aegis_assessment_master_refinement"][
            "policy_version"
        ] == "assessment-master-refiner-candidate-1"
        assert candidate["_aegis_assessment_route"]["authority"][
            "policy_version"
        ] == "assessment-route-1"
    for group in occupied:
        assert group["_aegis_assessment_variant_cluster"]["authority"][
            "policy_version"] == "assessment-variant-cluster-1"
        assert group["_aegis_assessment_group_description"]["authority"][
            "policy_version"] == "assessment-group-description-1"
        assert group["_aegis_assessment_group_quality"]["authority"][
            "policy_version"] == "assessment-group-quality-1"
        assert group["_aegis_assessment_master_refinement"][
            "policy_version"
        ] == "assessment-master-refiner-group-1"
        assert group["semantic_description"].endswith(
            "precise grade-level wording."
        )

    assert payload["refinements"]["output_kind"] == "assessment_master"
    assert payload["refinements"]["changes"]

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


def test_output02_is_stable_after_every_mutable_job_and_chapter_input_changes(
    db,
):
    chapter = _chapter_with_concepts(db)
    job = _make_job(db, chapter)
    staged = build_concepts_release.release_payload(job)
    assert staged is not None
    bridge_before = release_snapshot.build(db, job, staged)
    bridge_bytes = rel.canonical_json(bridge_before).encode("utf-8")

    calls = {}
    authorities, _ = _authorities(db, chapter, calls=calls)
    store = kernel.DecisionStore()
    first = run.run_release_for_job(
        db,
        job.id,
        owner_sub=OWNER,
        authorities=authorities,
        **_decision_context(store),
    )
    first_call_counts = {
        stage: len(payloads) for stage, payloads in calls.items()
    }

    mutable_concept = sorted(
        (concept for topic in chapter.topics for concept in topic.concepts),
        key=lambda concept: concept.id,
    )[0]
    original_concept = (
        mutable_concept.concept_title,
        mutable_concept.concept_display_name,
        mutable_concept.concept_details,
    )
    original_job = {
        "learning_kind": job.learning_kind,
        "source_book": job.source_book,
        "filename": job.filename,
        "mmd_text": job.mmd_text,
        "question_inventory": copy.deepcopy(job.question_inventory),
    }
    original_chapter = {
        field: getattr(chapter, field)
        for field in (
            "board",
            "grade",
            "subject",
            "unit",
            "chapter_title",
            "chapter_code",
            "chapter_display_name",
            "chapter_description",
            "chapter_duration",
            "pre_topics",
            "post_topics",
        )
    }
    mutable_concept.concept_title = "MUTATED DATABASE CONCEPT"
    mutable_concept.concept_display_name = "MUTATED DATABASE DISPLAY"
    mutable_concept.concept_details = "MUTATED DATABASE TEACHING CONTENT"
    durable_inventory = copy.deepcopy(job.question_inventory or {})
    durable_inventory["items"] = [{
        "qid": "QINV-MUTATED",
        "source_kind": "exercise",
        "raw_task": "A mutable question that must never enter Output 02.",
    }]
    job.question_inventory = durable_inventory
    job.learning_kind = "pre"
    job.source_book = "MUTATED SOURCE BOOK"
    job.filename = "mutated-source.mmd"
    job.mmd_text = "# Mutated source that was never staged"
    chapter.board = "MUTATED BOARD"
    chapter.grade = "MUTATED GRADE"
    chapter.subject = "MUTATED SUBJECT"
    chapter.unit = "MUTATED UNIT"
    chapter.chapter_title = "MUTATED CHAPTER"
    chapter.chapter_code = "MUTATED-CODE"
    chapter.chapter_display_name = "MUTATED DISPLAY"
    chapter.chapter_description = "MUTATED DESCRIPTION"
    chapter.chapter_duration = "999 minutes"
    chapter.pre_topics = "MUTATED PRE TOPICS"
    chapter.post_topics = "MUTATED POST TOPICS"
    db.commit()

    try:
        staged_after = build_concepts_release.release_payload(job)
        assert staged_after is not None
        bridge_after = release_snapshot.build(db, job, staged_after)
        assert rel.canonical_json(bridge_after).encode("utf-8") == bridge_bytes

        second = run.run_release_for_job(
            db,
            job.id,
            owner_sub=OWNER,
            authorities=authorities,
            **_decision_context(store),
        )
        assert {
            stage: len(payloads) for stage, payloads in calls.items()
        } == first_call_counts
        assert rel.canonical_json(second.payload) == rel.canonical_json(
            first.payload
        )
        assert second.payload["source_concept_release_sha256"] == (
            first.payload["source_concept_release_sha256"]
        )
        assert second.payload["source_atoms"] == first.payload["source_atoms"]
        assert all(
            atom["source_document_hash"]
            == staged["source_document_hash"]
            for atom in second.payload["source_atoms"]
        )
    finally:
        (
            mutable_concept.concept_title,
            mutable_concept.concept_display_name,
            mutable_concept.concept_details,
        ) = original_concept
        for field, value in original_job.items():
            setattr(job, field, value)
        for field, value in original_chapter.items():
            setattr(chapter, field, value)
        db.commit()

    assert {
        candidate["question"] for candidate in second.payload["candidates"]
    } == {
        "Which of these is a solid?",
        "Explain why a cube is a solid.",
    }
    routed_concepts = calls["route"][0]["candidate_concepts"]
    assert [row["concept_title"] for row in routed_concepts] == [
        "Solid Shapes",
        "Plane Shapes",
    ]
    assert all(
        "MUTATED DATABASE" not in str(row)
        for row in routed_concepts
    )


def test_route_provider_receives_complete_staged_type_case_and_analysis_evidence(
    db,
):
    chapter = _chapter_with_concepts(db)
    job = _make_job(db, chapter)
    payload = build_concepts_release.release_payload(job)
    assert payload is not None

    first_record = payload["records"][0]
    first_record["concept_details"] = (
        "Description: Solid-shape teaching. // Types: Type 01: Visual "
        "classification — identify a solid from its defining properties. "
        "Case 01: Use a supplied cube. Example: Which of these is a solid? "
        "// Misconception/ Error Analysis: A learner may confuse a square "
        "with a cube."
    )
    first_record["_aegis_release_type_case_routes"] = [{
        "type_id": "TYPE-0001",
        "type_definition": "Identify a solid from defining properties.",
        "case_id": "CASE-0001",
        "case_definition": "Use a supplied cube.",
        "source_question_id": "QINV-0001",
        "source_start": 400,
    }]
    first_record["_aegis_analysis_allotments"] = [{
        "analysis_id": "LA-0001",
        "analysis": "Learners may confuse two- and three-dimensional form.",
        "source_order": 7,
    }]
    first_record["_phase32_source_order"] = 3
    payload["mined_types"] = {
        "placement_certifications": {
            "hosts": {
                "QINV-0001": {
                    "basis": "critic_verified",
                    "concept": "Solid Shapes",
                    "page_hint": 12,
                },
            },
        },
        "types": [{
            "type_id": "TYPE-0001",
            "type_title": "Visual classification",
            "type_definition": "Identify a solid from defining properties.",
            "case_prompts": [{
                "case_id": "CASE-0001",
                "case_definition": "Use a supplied cube.",
                "source_question_ids": ["QINV-0001"],
                "source_end": 500,
            }],
        }],
    }
    payload["type_case_rows"] = [{
        "row_kind": "case",
        "type_id": "TYPE-0001",
        "type_title": "Visual classification",
        "type_definition": "Identify a solid from defining properties.",
        "case_id": "CASE-0001",
        "case_definition": "Use a supplied cube.",
        "qids": ["QINV-0001"],
        "example_number": 1,
        "row_number": 9,
    }]
    durable = copy.deepcopy(job.question_inventory or {})
    durable[build_concepts_release.RELEASE_KEY] = payload
    job.question_inventory = durable
    db.commit()

    calls = {}
    authorities, _ = _authorities(db, chapter, calls=calls)
    released = run.run_release_for_job(
        db,
        job.id,
        owner_sub=OWNER,
        authorities=authorities,
        **_decision_context(),
    )

    route_payload = next(
        row for row in calls["route"]
        if row["candidate"]["source_atom_ids"] == ["QINV-0001"]
    )
    concept = route_payload["candidate_concepts"][0]
    assert "Types: Type 01" in concept["teaching_description"]
    assert "Misconception/ Error Analysis" in concept["concept_details"]
    assert concept["released_record"][
        "_aegis_release_type_case_routes"
    ][0]["case_definition"] == "Use a supplied cube."
    assert concept["released_record"][
        "_aegis_analysis_allotments"
    ][0]["analysis_id"] == "LA-0001"

    route_evidence = route_payload["candidate"]["route_evidence"]
    assert route_evidence["basis"] == "critic_verified"
    assert route_evidence["mined_types"][0]["type_id"] == "TYPE-0001"
    assert route_evidence["type_case_rows"][0]["case_id"] == "CASE-0001"
    assert released.payload["source_concept_release_sha256"] == (
        release_snapshot.source_release_sha256(payload)
    )

    forbidden = {
        "bbox", "example_number", "page", "page_hint", "position",
        "printer_page", "row", "row_index", "row_number", "source_end",
        "source_order", "source_page", "source_paper_number", "source_start",
        "_phase32_segment_order", "_phase32_source_order",
    }

    def evidence_keys(value):
        if isinstance(value, dict):
            return set(value) | {
                key
                for nested in value.values()
                for key in evidence_keys(nested)
            }
        if isinstance(value, list):
            return {
                key for nested in value for key in evidence_keys(nested)
            }
        return set()

    assert not forbidden.intersection(evidence_keys(concept))
    assert not forbidden.intersection(evidence_keys(route_evidence))


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
        "cells": 2,
        "materialize": 2,
        "answer_restriction": 2,
        "marking": 2,
        "route": 2,
        "level": 2,
        "cluster": 1,
        "describe": 2,
        "qa": 2,
        "refiner": 4,
        "critic": 21,
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
    snapshot_paths = [
        tmp_path / filename
        for filename in (
            "source.phase3-assessment-cells.json",
            "source.phase3-assessment-materializations.json",
            "source.phase3-assessment-answer-restrictions.json",
            "source.phase3-assessment-markings.json",
            "source.phase3-assessment-routes.json",
            "source.phase3-assessment-levels.json",
            "source.phase3-assessment-groups.json",
            "source.phase3-assessment-master-refinements.json",
        )
    ]
    assert all(path.is_file() for path in snapshot_paths)
    snapshot_bytes = tuple(path.read_bytes() for path in snapshot_paths)
    assert b"created_at" not in b"".join(snapshot_bytes)

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
    assert snapshot_bytes == tuple(
        path.read_bytes() for path in snapshot_paths
    )
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


def test_route_critic_dissent_publishes_with_review_warning(db):
    chapter = _chapter_with_concepts(db)
    job = _make_job(db, chapter)
    authorities, _ = _authorities(db, chapter)

    def dissenting_critic(_payload):
        return {
            "verdict": "dissent",
            "confidence": 0.9,
            "issues": ["the selected concept deserves reviewer attention"],
        }

    authorities["route"] = (authorities["route"][0], dissenting_critic)
    release = run.run_release_for_job(
        db, job.id, owner_sub=OWNER, authorities=authorities,
        **_decision_context())

    assert (release.diagnostics or {}).get("readiness") == (
        svc.RELEASED_WITH_WARNINGS
    )
    assert all(
        placement.get("concept_key")
        for placement in release.payload["placements"]
    )
    assert all(
        "assessment_route_review" in candidate.get("flags", [])
        for candidate in release.payload["candidates"]
    )
    directory = Path(release.publication["directory"])
    assert (directory / svc.MASTER_FILENAME).is_file()  # downloads survive


def test_master_refiner_delegate_failure_stages_unrefined_rows_with_warning(
    db, monkeypatch,
):
    chapter = _chapter_with_concepts(db)
    job = _make_job(db, chapter)
    authorities, _ = _authorities(db, chapter)

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("assessment Refiner import/delegation unavailable")

    monkeypatch.setattr(
        assessment_master_refiner, "refine_master", unavailable
    )
    release = run.run_release_for_job(
        db,
        job.id,
        owner_sub=OWNER,
        authorities=authorities,
        **_decision_context(),
    )

    assert (release.diagnostics or {}).get("readiness") == (
        svc.RELEASED_WITH_WARNINGS
    )
    assert release.payload["refinements"]["policy_version"] == (
        "assessment-master-refiner-1"
    )
    assert release.payload["refinements"]["changes"] == []
    for record in [
        *release.payload["candidates"],
        *release.payload["groups"],
    ]:
        assert "assessment_master_refiner_review" in record["flags"]
        audit = record["_aegis_assessment_master_refinement"]
        assert audit["status"] == "unavailable"
        assert audit["review_flags"]
    assert {
        candidate["question"] for candidate in release.payload["candidates"]
    } == {
        "Which of these is a solid?",
        "Explain why a cube is a solid.",
    }


def test_refiner_dissent_keeps_diff_when_required_empty_group_has_no_audit(
    db, monkeypatch,
):
    chapter = _chapter_with_concepts(db)
    job = _make_job(db, chapter)
    authorities, _ = _authorities(db, chapter)

    def dissent(_payload):
        return {
            "verdict": "dissent",
            "confidence": 0.8,
            "issues": ["Review final prose."],
        }

    authorities["refiner"] = (authorities["refiner"][0], dissent)
    real_refine = release_refiner.refine_release

    def include_empty_required_group(records, **kwargs):
        payload = copy.deepcopy(records[0])
        occupied = payload["groups"][0]
        machine_prefix = str(occupied["group_key"]).split(")", 1)[0] + ")"
        concept_name = str(occupied["group_name"]).split(" — ", 1)[0]
        payload["groups"].append({
            "group_key": f"{machine_prefix} BG01",
            "concept_id": occupied["concept_id"],
            "concept_key": occupied["concept_key"],
            "group_type": "Basic",
            "group_sequence": 1,
            "group_name": f"{concept_name} — Basic",
            "group_display_name": f"{concept_name} — Basic",
            "family": "",
            "semantic_description": "NA",
            "member_candidate_ids": [],
            "group_status": "Active",
            "flags": [],
        })
        return real_refine([payload], **kwargs)

    monkeypatch.setattr(
        release_refiner, "refine_release", include_empty_required_group
    )
    release = run.run_release_for_job(
        db,
        job.id,
        owner_sub=OWNER,
        authorities=authorities,
        **_decision_context(),
    )

    diff = release.payload["refinements"]
    assert diff["changes"]
    assert any(
        change["after"] == "A cube" for change in diff["changes"]
    )
    empty = next(
        group for group in release.payload["groups"]
        if not group.get("member_candidate_ids")
    )
    assert "_aegis_assessment_master_refinement" not in empty
    assert "assessment_master_refiner_review" not in empty["flags"]
    assert (release.diagnostics or {}).get("readiness") == (
        svc.RELEASED_WITH_WARNINGS
    )


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
    build_concepts_release.stage_release(
        db,
        job,
        target_chapter_id=chapter.id,
        records=[{
            "topic": "Shapes",
            "concept_title": "Solid Shapes",
            "concept_details": "Solid shapes occupy three dimensions.",
        }],
        inventory={},
        reason="empty inventory fixture",
    )
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


def test_explicit_subjective_cell_is_rejected_before_materialization():
    atom = {"source_qid": "QINV-0001"}
    subjective_cell = {
        "cell_id": "CELL-subjective",
        "sheet_kind": "subjective",
        "question_category": "Short Answer",
        "cognitive_skill": "Understand",
        "difficulty": "Moderate",
        "marks": 2,
        "count": 1,
        "appears_in": ["Pre/Post-Worksheet/Test"],
        "source_policy": "reuse",
    }

    with pytest.raises(ValueError, match="sheet_kind"):
        run._bind_explicit_cells(
            [atom],
            [subjective_cell],
            profile={
                "appears_in": "Pre/Post-Worksheet/Test",
                "allow_subjective_rows": True,
            },
            concept_keys=set(),
        )

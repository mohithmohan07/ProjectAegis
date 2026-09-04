import copy
import io

import pytest

from app import models
from app.services import (
    build_concepts,
    canonical_source_phase2 as phase2,
    canonical_source_phase3 as phase3,
    grounding_certificate,
    openai_usage,
    placement_policy,
)
from app.services.phase3 import reground as p3_reground
from tests.conftest import convert_concept_upload, stream_events, stream_result


def _use_specific_dry_learner_analysis(monkeypatch):
    # The deterministic learner-analysis fallbacks are deleted (filler is
    # never synthesized); dry rows simply carry whatever analysis their
    # fixtures author. Kept as a no-op seam so callers stay explicit.
    del monkeypatch


def test_a_filename_is_never_borrowed_as_the_publication():
    """Contract v2.0 §18 retired the filename fallback: the source label is
    the run's PUBLICATION (``job.source_book``) and nothing else — a job
    whose publication is unknown ships the cell blank (a read-back blocker)
    rather than borrowing a filename, delimited or not.
    """
    job = models.UploadJob(filename="History, Grade 10; Part 1.pdf")

    assert build_concepts._job_source_label(job) == ""

    job.source_book = "  NCERT History Part 1  "
    assert build_concepts._job_source_label(job) == "NCERT History Part 1"


def test_post_learning_creates_concepts(client, db, first_chapter, monkeypatch):
    _use_specific_dry_learner_analysis(monkeypatch)
    files = {"file": ("notes.txt", io.BytesIO(
        b"## Trigonometry Basics\nSine ratio: opposite over hypotenuse\n"
        b"Cosine ratio: adjacent over hypotenuse"
    ), "text/plain")}
    job = client.post("/build-concepts/post-learning/uploads", files=files).json()
    assert job["learning_kind"] == "post"
    assert job["status"] == "uploaded"  # upload stages only

    convert_concept_upload(client, job["id"])
    result = stream_result(client.post(
        f"/build-concepts/post-learning/uploads/{job['id']}/generate",
        json={"target_chapter_id": first_chapter["id"]}))
    assert result["concepts_created"] >= 2
    assert result["rows_appended"] >= 2
    db.expire_all()
    concepts = (
        db.query(models.Concept)
        .filter(models.Concept.id.in_(result["concept_ids"]))
        .all()
    )
    assert concepts
    assert {concept.sources for concept in concepts} == {"notes"}


def test_post_learning_groups_concepts_under_one_topic(
    client, db, first_chapter, monkeypatch,
):
    """Concepts sharing a topic name must share ONE Topic row (no duplicates)."""
    _use_specific_dry_learner_analysis(monkeypatch)
    files = {"file": ("grouping.txt", io.BytesIO(
        b"## Grouping Topic 9912\nGrouping concept alpha 9912\n"
        b"Grouping concept beta 9912\nGrouping concept gamma 9912"
    ), "text/plain")}
    job = client.post("/build-concepts/post-learning/uploads", files=files).json()
    convert_concept_upload(client, job["id"])
    result = stream_result(client.post(
        f"/build-concepts/post-learning/uploads/{job['id']}/generate",
        json={"target_chapter_id": first_chapter["id"]}))
    assert result["concepts_created"] == 4

    import app.models as models
    topics = (
        db.query(models.Topic)
        .filter_by(chapter_id=first_chapter["id"], topic_title="Grouping Topic 9912")
        .all()
    )
    assert len(topics) == 1
    assert len(topics[0].concepts) == 4
    assert sum(c.concept_title.startswith("Culmination -") for c in topics[0].concepts) == 1


def test_post_learning_failure_persists_and_resumes_type_checkpoint(
    db, first_chapter, monkeypatch,
):
    job = models.UploadJob(
        module="build_concepts",
        upload_type="document",
        learning_kind="post",
        filename="checkpoint.mmd",
        mmd_text="## Topic\nSource body",
        status="converted",
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    checkpoint = build_concepts.generation._make_concept_checkpoint(
        "pre_type_assignment",
        records=[{"topic": "T", "concept_title": "C"}],
        question_task_inventory={
            "items": [{
                "qid": "QINV-0001",
                "raw_task": (
                    "Explain how the source supports the stated conclusion."
                ),
            }],
            "stats": {"total_inventory_items": 1},
        },
        mined_types={"types": [{"type_id": "TYPE-0001"}]},
        method_row_snapshot=[],
    )

    def fail_after_checkpoint(*args, checkpoint_callback=None, **kwargs):
        assert checkpoint_callback is not None
        checkpoint_callback(checkpoint)
        # A later automatic checkpoint sees the same cumulative run tracker;
        # it must update the checkpoint without billing these calls again.
        checkpoint_callback(checkpoint)
        raise RuntimeError("type embedding failed: unassigned TYPE-0001")

    monkeypatch.setattr(
        build_concepts.generation, "concepts_from_mmd", fail_after_checkpoint)
    with openai_usage.track() as run_usage:
        run_usage.add(
            model="gpt-5.4-mini-2026-03-17",
            input_tokens=100,
            cached_input_tokens=40,
            output_tokens=20,
        )
        with pytest.raises(RuntimeError, match="unassigned TYPE-0001"):
            build_concepts.generate_post_learning(
                db, job.id, first_chapter["id"])

    db.expire_all()
    saved = db.get(models.UploadJob, job.id)
    assert saved.generation_checkpoint["stage"] == "pre_type_assignment"
    assert saved.question_inventory["items"][0]["qid"] == "QINV-0001"
    assert saved.openai_usage["request_count"] == 1
    assert saved.openai_usage["total_tokens"] == 120

    def resume_from_checkpoint(*args, resume_checkpoint=None, **kwargs):
        assert resume_checkpoint["stage"] == "pre_type_assignment"
        return [{
            "topic": "T",
            "parent_concept": "P",
            "concept_title": "C",
            "concept_details": "Description: complete",
            "keywords": "",
        }]

    monkeypatch.setattr(
        build_concepts.generation, "concepts_from_mmd",
        resume_from_checkpoint,
    )
    monkeypatch.setattr(
        build_concepts, "_deposit_concepts", lambda *a, **kw: ([], []))
    monkeypatch.setattr(
        build_concepts.writer,
        "append_concepts",
        lambda *a, **kw: {
            "written": 0, "sources_updated": 0,
        },
    )
    monkeypatch.setattr(
        build_concepts, "_publish_staged_workbook", lambda *a, **kw: None)
    result = build_concepts.generate_post_learning(
        db, job.id, first_chapter["id"])
    assert result["concepts_created"] == 0
    db.expire_all()
    assert db.get(models.UploadJob, job.id).generation_checkpoint == {}


def test_checkpoint_without_inventory_does_not_erase_saved_inventory(
    db, first_chapter, monkeypatch,
):
    job = models.UploadJob(
        module="build_concepts",
        upload_type="document",
        learning_kind="post",
        filename="inventory-preservation.mmd",
        mmd_text="## Topic\nSource body",
        status="converted",
        question_inventory={
            "items": [{"qid": "QINV-KEEP"}],
            "stats": {"total_inventory_items": 1},
            "mined_types": [{"type_id": "TYPE-KEEP"}],
        },
    )
    db.add(job)
    db.commit()

    def fail_after_skeleton(*args, checkpoint_callback=None, **kwargs):
        checkpoint_callback(
            build_concepts.generation._make_concept_checkpoint(
                "skeleton_complete",
                records=[{"topic": "T", "concept_title": "C"}],
            )
        )
        raise RuntimeError("later stage failed")

    monkeypatch.setattr(
        build_concepts.generation,
        "concepts_from_mmd",
        fail_after_skeleton,
    )

    with pytest.raises(RuntimeError, match="later stage failed"):
        build_concepts.generate_post_learning(
            db, job.id, first_chapter["id"])

    db.expire_all()
    saved = db.get(models.UploadJob, job.id)
    assert saved.question_inventory["items"][0]["qid"] == "QINV-KEEP"
    assert saved.question_inventory["mined_types"][0]["type_id"] == "TYPE-KEEP"


def test_source_topic_review_checkpoint_clears_stale_inventory_atomically(
    db, first_chapter, monkeypatch,
):
    job = models.UploadJob(
        module="build_concepts",
        upload_type="document",
        learning_kind="post",
        filename="source-topic-review.mmd",
        mmd_text="## 1. Topic One\nBody\n## 2. Topic Two\nBody",
        status="converted",
        question_inventory={
            "items": [{"qid": "QINV-STALE"}],
            "stats": {"total_inventory_items": 1},
            "mined_types": [{"type_id": "TYPE-STALE"}],
        },
    )
    db.add(job)
    db.commit()
    checkpoint = build_concepts.generation._make_concept_checkpoint(
        "source_topic_review",
        records=[{"topic": "Topic One", "concept_title": "Concept One"}],
        source_topic_recovery={},
        skeleton_method_row_snapshot=[],
    )

    def pause_after_review(*args, checkpoint_callback=None, **kwargs):
        assert checkpoint_callback is not None
        checkpoint_callback(checkpoint)
        raise RuntimeError("pause after source-topic review")

    monkeypatch.setattr(
        build_concepts.generation,
        "concepts_from_mmd",
        pause_after_review,
    )

    with pytest.raises(RuntimeError, match="pause after source-topic review"):
        build_concepts.generate_post_learning(
            db, job.id, first_chapter["id"])

    db.expire_all()
    saved = db.get(models.UploadJob, job.id)
    assert saved.generation_checkpoint["stage"] == "source_topic_review"
    assert saved.question_inventory == {}


def test_store_inventory_preserves_placement_certifications_after_success():
    job = models.UploadJob()
    ledger = {
        "version": 1,
        "hosts": {
            "QINV-0001": {
                "topic": "Methods",
                "topic_key": "methods",
                "concept": "Method Beta",
                "concept_key": "method beta",
                "is_culmination": False,
                "basis": "type_host_review",
            },
        },
    }

    build_concepts._store_inventory(job, {
        "question_task_inventory": {
            "items": [{"qid": "QINV-0001"}],
            "stats": {"total_inventory_items": 1},
        },
        "mined_types": {
            "types": [{"type_id": "TYPE-0001"}],
            build_concepts.generation._PLACEMENT_CERTIFICATIONS_KEY: ledger,
        },
    })

    assert job.question_inventory["mined_types"] == [{
        "type_id": "TYPE-0001",
    }]
    assert job.question_inventory[
        build_concepts.generation._PLACEMENT_CERTIFICATIONS_KEY
    ] == ledger
    assert job.question_inventory[
        build_concepts.generation._PLACEMENT_CERTIFICATIONS_KEY
    ] is not ledger


def test_deposit_canonicalizes_bare_tex_restored_from_inventory(
    db,
    first_chapter,
    monkeypatch,
):
    chapter = db.get(models.Chapter, first_chapter["id"])
    task = r"Calculate \frac{1}{2}+\frac{1}{3} and explain the method."
    inventory = {"items": [{
        "qid": "QINV-0001",
        "source_kind": "exercise",
        "source_label": "Exercise 1",
        "topic_hint": "Fractions",
        "raw_task": task,
        "normalized_task": task,
    }]}
    records = [{
        "topic": "Fractions",
        "parent_concept": "Operations",
        "concept_title": "Adding Fractions",
        "concept_details": (
            "Description: Add fractions by expressing them with a common "
            "denominator. Achieving Mastery: Explaining why the common "
            "denominator preserves value. // "
            "Misconception/ Error Analysis: Misconceptions: Learners may add "
            "denominators directly.; Error Analysis: Learners may change the "
            "denominator without scaling the numerator."
        ),
        "keywords": "fractions, denominator",
    }]
    validated: list[list[dict]] = []

    def validate_final(current, **_kwargs):
        validated.append(copy.deepcopy(current))
        assert all(
            build_concepts.generation.kr.rich_text_issues(
                row["concept_details"]
            ) == []
            for row in current
        )
        assert any(
            r"[Katex] \frac{1}{2}+\frac{1}{3} [/Katex]"
            in row["concept_details"]
            for row in current
        )

    monkeypatch.setattr(
        build_concepts.generation,
        "_validate_final_or_raise",
        validate_final,
    )
    monkeypatch.setattr(
        build_concepts.concept_validator,
        "validate_concept_rows",
        lambda *_args, **_kwargs: {
            "ok": True,
            "errors": [],
            "summary": {"warnings": 0},
        },
    )

    created, merged = build_concepts._deposit_concepts(
        db,
        chapter,
        records,
        "Post",
        "NCERT Grade 8",
        inventory=inventory,
        mined_types={"types": []},
        source_text=task,
    )

    assert created
    assert merged == []
    assert validated


def test_post_learning_discard_control_durably_clears_only_final_checkpoint(
    db, first_chapter, monkeypatch,
):
    job = models.UploadJob(
        module="build_concepts",
        upload_type="document",
        learning_kind="post",
        filename="discard-final.mmd",
        mmd_text="## Topic\nSource body",
        status="converted",
    )
    db.add(job)
    db.commit()

    final = build_concepts.generation._make_concept_checkpoint(
        "final_content_ready",
        records=[{"topic": "T", "concept_title": "C"}],
        question_task_inventory={"items": [], "stats": {}},
        mined_types={"types": []},
        method_row_snapshot=[],
    )

    def fail_after_discard(*args, checkpoint_callback=None, **kwargs):
        assert checkpoint_callback is not None
        checkpoint_callback(final)
        checkpoint_callback({
            "checkpoint_action": "discard_stage",
            "stage": "final_content_ready",
            "reason": "strict validation failed",
        })
        raise RuntimeError("fallback generation failed")

    monkeypatch.setattr(
        build_concepts.generation,
        "concepts_from_mmd",
        fail_after_discard,
    )

    with pytest.raises(RuntimeError, match="fallback generation failed"):
        build_concepts.generate_post_learning(
            db, job.id, first_chapter["id"])

    db.expire_all()
    saved = db.get(models.UploadJob, job.id)
    assert saved.generation_checkpoint == {}
    assert "Discarded invalid generation checkpoint" in saved.detail


def test_post_learning_api_discards_invalid_final_and_completes_retry_without_api(
    client, db, first_chapter, monkeypatch,
):
    source = "# T\nA short source section."
    job = models.UploadJob(
        module="build_concepts",
        upload_type="document",
        learning_kind="post",
        filename="api-discard-final.mmd",
        mmd_text=source,
        status="converted",
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    chapter = db.get(models.Chapter, first_chapter["id"])

    details = (
        "Description: A complete concept description."
        "\nAchieving Mastery: Applying the concept correctly. // "
        "Misconception/ Error Analysis: Misconceptions: Students may believe "
        "every condition is optional.; Error Analysis: Students may omit a "
        "required condition."
    )
    culmination = {
        "topic": "T",
        "parent_concept": "Culmination",
        "concept_title": "Culmination - T",
        "concept_details": (
            "Description: Recap the topic. // Types: Type 01: Mixed reasoning "
            "Case 01: Connect the ideas Example: Combine the listed concepts "
            "to solve a mixed review task."
        ),
        "keywords": "",
    }

    def records(title):
        return [{
            "topic": "T",
            "parent_concept": "P",
            "concept_title": title,
            "concept_details": details,
            "keywords": "",
        }, copy.deepcopy(culmination)]

    common = {
        "question_task_inventory": {"items": [], "stats": {}},
        "mined_types": {"types": []},
        "method_row_snapshot": [],
    }
    prior = build_concepts.generation._make_concept_checkpoint(
        "post_type_assignment",
        records=records("Prior-stage concept"),
        **common,
    )
    stale_final = build_concepts.generation._make_concept_checkpoint(
        "final_content_ready",
        records=records("Rejected final concept"),
        **common,
    )
    checkpoint_args = {
        "fingerprint": build_concepts._generation_checkpoint_fingerprint(
            job, chapter),
        "target_identity": build_concepts._generation_target_identity(chapter),
        "target_chapter_id": chapter.id,
    }
    history = build_concepts._merge_generation_checkpoint_history(
        {}, prior, **checkpoint_args)
    job.generation_checkpoint = build_concepts._merge_generation_checkpoint_history(
        history, stale_final, **checkpoint_args)
    db.commit()

    # Phase 3 never resumes a concept checkpoint on a deterministic source guess.
    # Seed the independently verified source graph so this regression can remain
    # focused on durable concept-checkpoint recovery without another model call.
    canonical = phase2.compile_phase2_source(
        source,
        source_filename=job.filename,
        consumer_module="build_concepts",
    ).canonical
    metadata = {
        "board": chapter.board,
        "grade": chapter.grade,
        "subject": chapter.subject,
        "unit": chapter.unit,
        "chapter_title": chapter.chapter_title,
        "chapter_id": chapter.id,
        "chapter_code": chapter.chapter_code,
        "learning_kind": "Post",
    }

    def classify_source(payload):
        return {
            "sections": [
                {
                    "section_id": row["section_id"],
                    "role": row["baseline_role"],
                    "parent_section_id": "",
                    "confidence": 0.999,
                    "evidence": ["verified checkpoint regression source"],
                }
                for row in payload["sections"]
            ]
        }

    graph, graph_report = phase3.compile_semantic_graph(
        canonical,
        source_text=source,
        metadata=metadata,
        hierarchy_provider=classify_source,
        critic_provider=lambda _payload: {
            "verdict": "verified",
            "confidence": 0.999,
            "repairs": [],
            "issues": [],
        },
    )
    assert graph_report["classification_mode"] == "api_classified_and_verified"

    def use_verified_source_graph(**kwargs):
        assert kwargs["verify_semantics"] is True
        assert kwargs["metadata"] == metadata
        return graph

    monkeypatch.setattr(
        phase3, "prepare_generation_graph", use_verified_source_graph
    )
    monkeypatch.setattr(
        build_concepts.generation.config,
        "use_live_generation",
        lambda: True,
    )
    monkeypatch.setattr(
        build_concepts.generation,
        "_openai_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("checkpoint recovery must not call OpenAI")
        ),
    )
    def prepare_grounded(current, **_kwargs):
        grounded = copy.deepcopy(current)
        source_blocks = [
            block for block in graph.get("blocks") or []
            if isinstance(block, dict)
            and str(block.get("block_id") or "")
            and str(block.get("topic_id") or "")
            and str(block.get("kind") or "") not in {
                "heading", "layout", "navigation",
            }
        ]
        assert source_blocks
        teaching_order = placement_policy.seal_teaching_order(
            graph,
            source_contract_hash=str(graph["source_contract_hash"]),
        )
        allowed = set()
        for index, row in enumerate(grounded, start=1):
            source_block = source_blocks[min(index - 1, len(source_blocks) - 1)]
            block_id = str(source_block["block_id"])
            topic_id = str(source_block["topic_id"])
            allowed.add(block_id)
            row["_semantic_topic_id"] = topic_id
            row["_source_block_ids"] = [block_id]
            row["_source_grounding_contract"] = (
                "api-verified-source-block-ids"
            )
            row["_source_grounding_version"] = "checkpoint-test-1"
            if not str(row.get("concept_title") or "").startswith(
                "Culmination -"
            ):
                claim_id = f"CHECKPOINT-PLACEMENT-{index:04d}#1"
                relation = placement_policy.TopicRelationship(
                    claim_id=claim_id,
                    topic_id=topic_id,
                    relationship_type=(
                        placement_policy.RelationshipType.CORE_TEACHING
                    ),
                    necessity=True,
                    evidence_block_ids=(block_id,),
                    provider_reason="Test fixture exact source placement.",
                    critic_verdict="accepted",
                )
                claim = placement_policy.AtomicClaim(
                    claim_id=claim_id,
                    normalized_claim=grounding_certificate.source_claim(row),
                    source_location_topic_id=topic_id,
                    origin_claim_ids=(claim_id,),
                )
                placement = placement_policy.compute_placement(
                    claim, [relation], teaching_order
                )
                contract = {
                    "certified": True,
                    "policy_version": placement_policy.POLICY_VERSION,
                    "teaching_order_sha256": teaching_order.sha256,
                    "claim_id": claim_id,
                    "normalized_claim": claim.normalized_claim,
                    "origin_claim_sha256": (
                        placement_policy.claim_text_sha256(
                            claim.normalized_claim
                        )
                    ),
                    "source_location_topic_id": topic_id,
                    "owner_topic_id": placement.owner_topic_id,
                    "required_topic_ids": list(
                        placement.required_topic_ids
                    ),
                    "prerequisite_topic_ids": [],
                    "reference_edges": [],
                    "illustration_topic_ids": [],
                    "topic_relationships": [
                        placement_policy.relationship_audit_row(relation)
                    ],
                    "origin_claim_ids": [claim_id],
                    "split_group_id": "",
                    "protected_source_items": [],
                    "split_attestation": {},
                }
                contract["placement_certificate_sha256"] = (
                    placement_policy.placement_contract_sha256(contract)
                )
                row[grounding_certificate.PLACEMENT_CONTRACT_FIELD] = contract
        grounding_certificate.seal_records(
            grounded,
            source_contract_hash=str(graph["source_contract_hash"]),
            semantic_topology_sha256=(
                grounding_certificate.semantic_topology_sha256(graph)
            ),
            allowed_block_ids=allowed,
        )
        return grounded

    monkeypatch.setattr(
        build_concepts.generation,
        "_prepare_final_concept_content",
        prepare_grounded,
    )
    # This regression isolates fallback from an uncertified terminal
    # checkpoint.  The preceding stage now receives a latest-boundary
    # re-ground whenever deterministic final formatting changes its sealed
    # claim; emulate that independently verified pass without a provider call.
    monkeypatch.setattr(
        p3_reground,
        "reground_rows",
        lambda current, _drifted, **_kwargs: prepare_grounded(current),
    )
    validations = []

    def validate(current, **_kwargs):
        validations.append([row["concept_title"] for row in current])
        raise RuntimeError("stop after certified prior checkpoint was restored")

    monkeypatch.setattr(
        build_concepts.generation, "_validate_final_or_raise", validate)
    monkeypatch.setattr(
        build_concepts.drive_checkpoints,
        "schedule_checkpoint_backup",
        lambda *_args, **_kwargs: None,
    )

    events = stream_events(client.post(
        f"/build-concepts/post-learning/uploads/{job.id}/generate",
        json={"target_chapter_id": chapter.id},
    ))

    # A final checkpoint without the new payload/evidence certificate is
    # incompatible and is never loaded as a candidate. Resume begins at the
    # preceding certified stage and re-runs final grounding.
    assert len(validations) == 1
    assert validations[0][0] == "Prior-stage concept"
    assert validations[0][1].startswith("Culmination -")
    assert any(
        event.get("type") == "log"
        and "Persisted compatible checkpoint fallback" in event.get("message", "")
        for event in events
    )
    assert any(
        event.get("type") == "error"
        and "stop after certified prior checkpoint was restored"
        in event.get("message", "")
        for event in events
    )

    db.expire_all()
    saved = db.get(models.UploadJob, job.id)
    assert saved.generation_checkpoint["stage"] == "post_type_assignment"
    assert [
        entry["stage"]
        for entry in saved.generation_checkpoint["checkpoints"]
    ] == ["post_type_assignment"]
    assert saved.openai_usage.get("request_count", 0) == 0

    status = client.get(f"/build-concepts/uploads/{job.id}").json()
    assert status["checkpoint_stage"] == "post_type_assignment"
    assert status["checkpoint_progress"] == 0.91

    accepted = []

    def accept(current, **_kwargs):
        accepted.append([row["concept_title"] for row in current])
        grounding_certificate.seal_records(
            current,
            source_contract_hash=str(graph["source_contract_hash"]),
            semantic_topology_sha256=(
                grounding_certificate.semantic_topology_sha256(graph)
            ),
            allowed_block_ids={
                block_id
                for row in current
                for block_id in row.get("_source_block_ids") or []
            },
        )
        return {"ok": True, "errors": [], "summary": {}}

    deposited = []

    def deposit(_db, *, records, **_kwargs):
        deposited.append([row["concept_title"] for row in records])
        return [], [], {
            "written": 0,
            "sources_updated": 0,
            "grounding_certificate": copy.deepcopy(
                _kwargs["final_grounding_certificate"]
            ),
        }

    monkeypatch.setattr(
        build_concepts.generation, "_validate_final_or_raise", accept)
    monkeypatch.setattr(
        build_concepts, "_deposit_and_publish_concepts", deposit)

    result = stream_result(client.post(
        f"/build-concepts/post-learning/uploads/{job.id}/generate",
        json={"target_chapter_id": chapter.id},
    ))

    assert result["job_id"] == job.id
    assert accepted[0][0] == "Prior-stage concept"
    assert deposited[0][0] == "Prior-stage concept"
    db.expire_all()
    completed = db.get(models.UploadJob, job.id)
    assert completed.status == "generated"
    assert completed.generation_checkpoint == {}
    assert completed.openai_usage.get("request_count", 0) == 0

    completed_status = client.get(
        f"/build-concepts/uploads/{job.id}").json()
    assert completed_status["checkpoint_available"] is False
    assert completed_status["checkpoint_stage"] == ""
    assert completed_status["checkpoint_progress"] == 0.0


def test_post_learning_preserves_invalid_checkpoint_and_requires_start_over(
    db, first_chapter, monkeypatch,
):
    job = models.UploadJob(
        module="build_concepts",
        upload_type="document",
        learning_kind="post",
        filename="invalid-checkpoint.mmd",
        mmd_text="## Topic\nSource body",
        status="converted",
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    chapter = db.get(models.Chapter, first_chapter["id"])
    job.generation_checkpoint = {
        "fingerprint": build_concepts._generation_checkpoint_fingerprint(
            job, chapter),
        "target_chapter_id": chapter.id,
        "schema_version": 999,
        "stage": "pre_type_assignment",
    }
    db.commit()

    original = dict(job.generation_checkpoint)
    monkeypatch.setattr(
        build_concepts.generation,
        "concepts_from_mmd",
        lambda *a, **kw: (_ for _ in ()).throw(
            AssertionError("mismatched checkpoint must stop generation")),
    )

    with pytest.raises(ValueError, match="has been preserved"):
        build_concepts.generate_post_learning(db, job.id, chapter.id)

    db.expire_all()
    assert db.get(models.UploadJob, job.id).generation_checkpoint == original


def test_checkpoint_target_identity_survives_chapter_id_changes(
    db, first_chapter,
):
    original = db.get(models.Chapter, first_chapter["id"])
    rebuilt = models.Chapter(
        chapter_code=original.chapter_code,
        board=original.board,
        grade=original.grade,
        subject=original.subject,
        unit=original.unit,
        chapter_title=original.chapter_title,
        chapter_display_name=original.chapter_display_name,
    )
    job = models.UploadJob(
        module="build_concepts",
        upload_type="document",
        learning_kind="post",
        filename="portable.mmd",
        mmd_text="## Stable source\nBody",
        status="converted",
    )
    db.add_all([rebuilt, job])
    db.commit()
    db.refresh(rebuilt)
    db.refresh(job)
    checkpoint = build_concepts.generation._make_concept_checkpoint(
        "pre_type_assignment",
        records=[{"concept_title": "C"}],
        question_task_inventory={"items": [], "stats": {}},
        mined_types={"types": []},
        method_row_snapshot=[],
    )
    envelope = build_concepts._merge_generation_checkpoint_history(
        {},
        checkpoint,
        fingerprint=build_concepts._generation_checkpoint_fingerprint(
            job, original),
        target_identity=build_concepts._generation_target_identity(original),
        target_chapter_id=original.id,
    )

    assert envelope["checkpoints"] == [checkpoint]
    assert rebuilt.id != original.id
    assert build_concepts._checkpoint_matches_generation(
        envelope,
        job=job,
        chapter=rebuilt,
    )


def test_post_learning_wrong_chapter_preserves_checkpoint(
    db, first_chapter, monkeypatch,
):
    original = db.get(models.Chapter, first_chapter["id"])
    wrong_target = models.Chapter(
        chapter_code=f"{original.chapter_code}-WRONG",
        board=original.board,
        grade=original.grade,
        subject=original.subject,
        unit=original.unit,
        chapter_title=f"{original.chapter_title} Wrong Target",
        chapter_display_name=f"{original.chapter_title} Wrong Target",
    )
    job = models.UploadJob(
        module="build_concepts",
        upload_type="document",
        learning_kind="post",
        filename="wrong-target.mmd",
        mmd_text="## Stable source\nBody",
        status="converted",
    )
    db.add_all([wrong_target, job])
    db.commit()
    checkpoint = build_concepts.generation._make_concept_checkpoint(
        "pre_type_assignment",
        records=[{"concept_title": "C"}],
        question_task_inventory={"items": [], "stats": {}},
        mined_types={"types": []},
        method_row_snapshot=[],
    )
    job.generation_checkpoint = build_concepts._merge_generation_checkpoint_history(
        {},
        checkpoint,
        fingerprint=build_concepts._generation_checkpoint_fingerprint(
            job, original),
        target_identity=build_concepts._generation_target_identity(original),
        target_chapter_id=original.id,
    )
    db.commit()
    expected = copy.deepcopy(job.generation_checkpoint)
    monkeypatch.setattr(
        build_concepts.generation,
        "concepts_from_mmd",
        lambda *a, **kw: (_ for _ in ()).throw(
            AssertionError("wrong target must stop before generation")),
    )

    with pytest.raises(ValueError, match="selected chapter or converted source"):
        build_concepts.generate_post_learning(db, job.id, wrong_target.id)

    db.expire_all()
    assert db.get(models.UploadJob, job.id).generation_checkpoint == expected


def test_upload_workbook_failure_rolls_back_new_concepts(
    db, first_chapter, monkeypatch, tmp_path,
):
    learning_kind = "post"
    marker = f"Atomic Workbook Failure {learning_kind.title()} 73419"
    job = models.UploadJob(
        module="build_concepts",
        upload_type="document",
        learning_kind=learning_kind,
        filename=f"{learning_kind}-atomic.mmd",
        mmd_text="## Atomic source\nBody",
        status="converted",
    )
    db.add(job)
    db.commit()
    chapter = db.get(models.Chapter, first_chapter["id"])
    normal = {
        "topic": f"{marker} Topic",
        "parent_concept": marker,
        "concept_title": marker,
        "concept_details": (
            "Description: Learners apply a complete, source-grounded "
            "procedure accurately.\nAchieving Mastery: Carrying out the "
            "full stated procedure without skipping a required step. // "
            "Misconception/ Error Analysis: "
            "Misconceptions: Students may believe every procedure uses the "
            "same sequence of steps.; Error Analysis: Students may omit a "
            "required step while applying the stated procedure."
        ),
        "keywords": "",
        # Q1: the row carries the analysis section, so it models an
        # allotted row (assemble-stamped marker).
        "_aegis_analysis_allotments": ["LA-0001", "LA-0002"],
    }
    post_records = [
        normal,
        {
            "topic": f"{marker} Topic",
            "parent_concept": "Culmination",
            "concept_title": f"Culmination - {marker}",
            "concept_details": f"Description: Recap of {marker}.",
            "keywords": "",
        },
    ]
    monkeypatch.setattr(
        build_concepts.config,
        "BULK_IMPORT_OUTPUT",
        tmp_path / "bulk_import_output.xlsx",
    )
    monkeypatch.setattr(
        build_concepts,
        "_chapter_meta_summary",
        lambda _chapter, *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        build_concepts.generation,
        "concepts_from_mmd",
        lambda *a, **kw: post_records,
    )
    monkeypatch.setattr(
        build_concepts.writer,
        "append_concepts",
        lambda *a, **kw: (_ for _ in ()).throw(
            RuntimeError("workbook write failed")),
    )

    with pytest.raises(RuntimeError, match="workbook write failed"):
        build_concepts.generate_post_learning(db, job.id, chapter.id)

    db.expire_all()
    assert (
        db.query(models.Concept)
        .filter(models.Concept.concept_title == marker)
        .count()
        == 0
    )


def test_checkpoint_history_falls_back_from_unknown_newer_stage():
    compatible = build_concepts.generation._make_concept_checkpoint(
        "pre_type_assignment",
        records=[{"concept_title": "C"}],
        question_task_inventory={"items": [], "stats": {}},
        mined_types={"types": []},
        method_row_snapshot=[],
    )
    envelope = {
        "schema_version": build_concepts.generation._CONCEPT_CHECKPOINT_SCHEMA,
        "checkpoint_format": (
            build_concepts.generation._CONCEPT_CHECKPOINT_FORMAT),
        "checkpoints": [
            compatible,
            {
                "schema_version": (
                    build_concepts.generation._CONCEPT_CHECKPOINT_SCHEMA),
                "stage": "future_incompatible_stage",
                "stage_schema_version": 99,
                "records": [{"concept_title": "future"}],
            },
        ],
    }

    restored = (
        build_concepts.generation._newest_compatible_concept_checkpoint(
            envelope)
    )

    assert restored["stage"] == "pre_type_assignment"


def test_post_learning_persists_compatible_mirror_before_fallback_can_fail(
    db, first_chapter, monkeypatch,
):
    job = models.UploadJob(
        module="build_concepts",
        upload_type="document",
        learning_kind="post",
        filename="stale-98-mirror.mmd",
        mmd_text="## Topic\nSource body",
        status="converted",
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    chapter = db.get(models.Chapter, first_chapter["id"])
    common = {
        "records": [{"topic": "T", "concept_title": "Compatible prior"}],
        "question_task_inventory": {"items": [], "stats": {}},
        "mined_types": {"types": []},
        "method_row_snapshot": [],
    }
    compatible = build_concepts.generation._make_concept_checkpoint(
        "pre_type_assignment", **common)
    incompatible_final = build_concepts.generation._make_concept_checkpoint(
        "final_content_ready", **common)
    incompatible_final["stage_schema_version"] = 1
    checkpoint_args = {
        "fingerprint": build_concepts._generation_checkpoint_fingerprint(
            job, chapter),
        "target_identity": build_concepts._generation_target_identity(chapter),
        "target_chapter_id": chapter.id,
    }
    envelope = build_concepts._merge_generation_checkpoint_history(
        {}, compatible, **checkpoint_args)
    job.generation_checkpoint = (
        build_concepts._merge_generation_checkpoint_history(
            envelope, incompatible_final, **checkpoint_args)
    )
    db.commit()
    assert job.generation_checkpoint["stage"] == "final_content_ready"
    assert job.generation_checkpoint["progress"] == 0.98
    received: list[dict] = []

    def stop_before_checkpoint(*_args, resume_checkpoint=None, **_kwargs):
        received.append(copy.deepcopy(resume_checkpoint))
        raise RuntimeError("stop before the first replacement checkpoint")

    monkeypatch.setattr(
        build_concepts.generation,
        "concepts_from_mmd",
        stop_before_checkpoint,
    )
    monkeypatch.setattr(
        build_concepts.drive_checkpoints,
        "schedule_checkpoint_backup",
        lambda *_args, **_kwargs: None,
    )

    with pytest.raises(
        RuntimeError,
        match="stop before the first replacement checkpoint",
    ):
        build_concepts.generate_post_learning(db, job.id, chapter.id)

    assert received
    assert received[0]["stage"] == "pre_type_assignment"
    assert [
        entry["stage"] for entry in received[0]["checkpoints"]
    ] == ["pre_type_assignment"]
    db.expire_all()
    saved = db.get(models.UploadJob, job.id)
    assert saved.generation_checkpoint["stage"] == "pre_type_assignment"
    assert saved.generation_checkpoint["progress"] == 0.81
    assert [
        entry["stage"]
        for entry in saved.generation_checkpoint["checkpoints"]
    ] == ["pre_type_assignment"]


def test_checkpoint_history_discard_control_removes_stage_and_mirrors_fallback():
    common = {
        "records": [{"concept_title": "C"}],
        "question_task_inventory": {"items": [], "stats": {}},
        "mined_types": {"types": []},
        "method_row_snapshot": [],
    }
    post_assignment = build_concepts.generation._make_concept_checkpoint(
        "post_type_assignment", **common)
    final = build_concepts.generation._make_concept_checkpoint(
        "final_content_ready", **common)
    kwargs = {
        "fingerprint": "stable-fingerprint",
        "target_identity": {"chapter_title": "chapter"},
        "target_chapter_id": 7,
    }
    envelope = build_concepts._merge_generation_checkpoint_history(
        {}, post_assignment, **kwargs)
    envelope = build_concepts._merge_generation_checkpoint_history(
        envelope, final, **kwargs)

    discarded = build_concepts._merge_generation_checkpoint_history(
        envelope,
        {
            "checkpoint_action": "discard_stage",
            "stage": "final_content_ready",
            "reason": "strict validation failed",
        },
        **kwargs,
    )

    assert [
        entry["stage"] for entry in discarded["checkpoints"]
    ] == ["post_type_assignment"]
    assert discarded["stage"] == "post_type_assignment"
    assert discarded["progress"] == post_assignment["progress"]
    assert all(
        "checkpoint_action" not in entry
        for entry in discarded["checkpoints"]
    )


def test_checkpoint_history_discard_only_stage_clears_durable_envelope():
    final = build_concepts.generation._make_concept_checkpoint(
        "final_content_ready",
        records=[{"concept_title": "C"}],
        question_task_inventory={"items": [], "stats": {}},
        mined_types={"types": []},
        method_row_snapshot=[],
    )
    kwargs = {
        "fingerprint": "stable-fingerprint",
        "target_identity": {"chapter_title": "chapter"},
        "target_chapter_id": 7,
    }
    envelope = build_concepts._merge_generation_checkpoint_history(
        {}, final, **kwargs)

    assert build_concepts._merge_generation_checkpoint_history(
        envelope,
        {
            "checkpoint_action": "discard_stage",
            "stage": "final_content_ready",
        },
        **kwargs,
    ) == {}


def test_inventory_csv_download(client, db, first_chapter, monkeypatch):
    """The stored Question / Task Inventory downloads as an audit CSV."""
    _use_specific_dry_learner_analysis(monkeypatch)
    files = {"file": ("inv.txt", io.BytesIO(
        b"## Inventory Topic 7731\nInventory concept alpha 7731"
    ), "text/plain")}
    job = client.post("/build-concepts/post-learning/uploads", files=files).json()
    convert_concept_upload(client, job["id"])
    stream_result(client.post(
        f"/build-concepts/post-learning/uploads/{job['id']}/generate",
        json={"target_chapter_id": first_chapter["id"]}))

    # Dry mode produces no inventory, so simulate what a live run stores.
    import app.models as models
    job_row = db.get(models.UploadJob, job["id"])
    job_row.question_inventory = {
        "items": [
            {"qid": "QINV-0001", "order_index": 1, "source_kind": "exercise",
             "source_label": "Exercise 1.1 Q1", "topic_hint": "T",
             "raw_task": "Simplify, p^9 ÷ p^3.", "normalized_task": "Divide powers.",
             "requires_visual": False, "requires_context": False,
             "content_objects": {"variables": ["p"]}},
            {"qid": "QINV-0002", "order_index": 2, "source_kind": "mcq",
             "source_label": "Q2", "topic_hint": "T",
             "raw_task": "Pick the value of 2^3.", "normalized_task": "Evaluate a power.",
             "requires_visual": False, "requires_context": False,
             "content_objects": {}},
        ],
        "stats": {"total_inventory_items": 2},
        "mined_types": [
            {"type_id": "TYPE-0001", "type_title": "Dividing Powers with the Same Base",
             "source_question_ids": ["QINV-0001"], "case_prompts": []},
        ],
    }
    db.commit()

    resp = client.get(f"/build-concepts/uploads/{job['id']}/inventory.csv")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "attachment" in resp.headers["content-disposition"]

    import csv
    import io as _io
    rows = list(csv.DictReader(_io.StringIO(resp.text)))
    assert len(rows) == 2
    by_qid = {r["qid"]: r for r in rows}
    assert by_qid["QINV-0001"]["raw_task"] == "Simplify, p^9 ÷ p^3."
    assert by_qid["QINV-0001"]["classified"] == "yes"
    assert by_qid["QINV-0001"]["mined_type_ids"] == "TYPE-0001"
    assert "Dividing Powers" in by_qid["QINV-0001"]["mined_type_titles"]
    # Unclassified items are visible at a glance in the audit CSV.
    assert by_qid["QINV-0002"]["classified"] == "no"
    assert by_qid["QINV-0002"]["mined_type_ids"] == ""


def test_inventory_csv_missing_returns_404(client):
    files = {"file": ("noinv.txt", io.BytesIO(b"## X\nY"), "text/plain")}
    job = client.post("/build-concepts/post-learning/uploads", files=files).json()
    resp = client.get(f"/build-concepts/uploads/{job['id']}/inventory.csv")
    assert resp.status_code == 404


def test_the_legacy_pre_learning_routes_are_gone(client, first_chapter):
    """The three legacy pre-learning API surfaces no longer exist.

    Restructure step 7 retired the upload lane, its generate lane, and the
    "derive from existing Post Learning" lane. FastAPI answers an unrouted
    path with 404, so a 404 here is the machinery's absence, not a rejected
    request.
    """
    files = {"file": ("doc.txt", io.BytesIO(b"## Foundations\nBody"), "text/plain")}
    assert client.post(
        "/build-concepts/pre-learning/uploads", files=files,
    ).status_code == 404
    assert client.post(
        "/build-concepts/pre-learning/uploads/1/generate",
        json={"target_chapter_id": first_chapter["id"]},
    ).status_code == 404
    assert client.post(
        "/build-concepts/pre-learning/from-existing",
        json={"chapter_ids": [first_chapter["id"]]},
    ).status_code == 404

"""Durable human intervention at the Phase 3 source-graph boundary."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import models
from app.db import SessionLocal
from app.services import (
    auth,
    build_concepts,
    canonical_source_phase22 as phase22,
    canonical_source_phase2 as phase2,
    canonical_source_phase3 as phase3,
    checkpoints,
    generation,
    openai_usage,
    semantic_recovery,
    uploads,
)


def _malformed_source(count: int = 1) -> str:
    paragraphs = "\n\n".join(
        f"Source item {index} has $value{index}+1"
        for index in range(1, count + 1)
    )
    return f"# Review Chapter\n\n## Verified Topic\n\n{paragraphs}\n"


def _verified_graph_and_pages(
    count: int = 1,
) -> tuple[str, dict, dict, dict]:
    source = _malformed_source(count)
    page_bundle = {
        "schema_version": "1.1.0",
        "pdf_sha256": "verified-pdf",
        "pages": [{
            "page_id": "PAGE-0001",
            "page_number": 1,
            "blocks": [{
                "reading_order": index,
                "kind": "paragraph",
                "text": f"Source item {index} has value{index}+1",
                "confidence": 0.999,
            } for index in range(1, count + 1)],
        }],
    }
    canonical = phase2.compile_phase2_source(
        source,
        source_filename="review.pdf",
        consumer_module="build_concepts",
    ).canonical

    def classify(payload: dict) -> dict:
        return {
            "sections": [{
                "section_id": row["section_id"],
                "role": row["baseline_role"],
                "parent_section_id": "",
                "confidence": 0.999,
                "evidence": ["verified test hierarchy"],
            } for row in payload["sections"]]
        }

    graph, _report = phase3.compile_semantic_graph(
        canonical,
        source_text=source,
        metadata={
            "board": "CBSE",
            "grade": "10",
            "subject": "History",
            "chapter_title": "Review Chapter",
            "learning_kind": "Post",
        },
        page_bundle=page_bundle,
        hierarchy_provider=classify,
        critic_provider=lambda _payload: {
            "verdict": "verified",
            "confidence": 0.999,
            "repairs": [],
            "issues": [],
        },
    )
    return source, canonical, graph, page_bundle


def _diagnostic(payload: dict, *, source_path: Path | None) -> dict:
    del source_path
    source_text = str((payload.get("issue") or {}).get("source_text") or "")
    expected_number = next(
        (
            value for value in range(1, 100)
            if f"item {value} " in source_text
        ),
        1,
    )
    candidates = payload.get("candidates") or []
    recommended = next(
        row["target_id"]
        for row in candidates
        if f"item {expected_number} " in str(row.get("visible_text") or "")
    )
    return {
        "question": (
            "Which verified PDF block contains this exact source item?"
        ),
        "diagnosis": (
            "The converted paragraph contains an unclosed raw math delimiter."
        ),
        "recommended_target_id": recommended,
        "candidate_reasons": [{
            "target_id": row["target_id"],
            "reason": "Bounded verified-PDF candidate.",
        } for row in candidates],
    }


def _legacy_metadata_source_review() -> tuple[str, dict, dict, dict, dict]:
    source = """<!-- source_origin: gpt-pdf-to-acsd -->
<!-- compiler_version: gpt-pdf-to-acsd-2 -->

# Review Chapter

## Verified Topic

Visible canonical source.
"""
    metadata = {
        "board": "CBSE",
        "grade": "10",
        "subject": "History",
        "chapter_title": "Review Chapter",
        "learning_kind": "Post",
    }
    canonical = phase2.compile_phase2_source(
        source,
        source_filename="review.mmd",
        consumer_module="build_concepts",
    ).canonical

    def classify(payload: dict) -> dict:
        return {
            "sections": [{
                "section_id": row["section_id"],
                "role": row["baseline_role"],
                "parent_section_id": "",
                "confidence": 0.999,
                "evidence": ["verified test hierarchy"],
            } for row in payload["sections"]]
        }

    graph, _report = phase3.compile_semantic_graph(
        canonical,
        source_text=source,
        metadata=metadata,
        hierarchy_provider=classify,
        critic_provider=lambda _payload: {
            "verdict": "verified",
            "confidence": 0.999,
            "repairs": [],
            "issues": [],
        },
    )
    metadata_block = next(
        row
        for row in canonical["blocks"]
        if "source_origin" in str(row.get("raw_text") or "")
    )
    legacy_source = phase3.render_semantic_source(
        graph,
        canonical,
        strip_machine_metadata=False,
    )
    graph["semantic_source_sha256"] = phase3._sha256_text(legacy_source)
    errors = phase3.validate_graph(
        graph,
        canonical=canonical,
        semantic_source=legacy_source,
    )
    rich_error = next(
        row for row in errors
        if row.get("code") == "semantic_source_rich_text"
    )
    rich_error["block_ids"] = [metadata_block["block_id"]]
    graph["issues"] = [
        row for row in graph.get("issues") or []
        if row.get("severity") != "error"
    ] + errors
    graph["status"] = "failed"
    state = phase3._build_source_review_state(
        graph,
        canonical=canonical,
        page_bundle=None,
        source_path=None,
        block_id=metadata_block["block_id"],
        allow_diagnostic_call=False,
    )
    graph[phase3._SOURCE_REVIEW_KEY] = state
    pending = phase3._source_review_pending(
        graph,
        state,
        canonical=canonical,
    )
    return source, metadata, canonical, graph, pending


def test_legacy_metadata_source_review_migrates_without_model_retry(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    source, metadata, canonical, graph, pending = (
        _legacy_metadata_source_review()
    )

    assert phase3.machine_metadata_source_review_is_obsolete(
        graph,
        canonical=canonical,
        decision_id=pending["decision_id"],
        context_hash=pending["context_hash"],
    )
    migrated = phase3._compatible_source_review_graph(
        graph,
        canonical=canonical,
        metadata=metadata,
        page_bundle=None,
    )

    assert migrated is not None
    assert migrated["status"] == "ready"
    assert phase3._SOURCE_REVIEW_KEY not in migrated
    assert not any(
        row.get("severity") == "error"
        for row in migrated.get("issues") or []
    )
    semantic = phase3.render_semantic_source(migrated, canonical)
    assert "source_origin" not in semantic
    assert migrated["semantic_source_sha256"] == phase3._sha256_text(
        semantic)
    assert phase3.machine_metadata_migration_seal_valid(
        migrated,
        canonical=canonical,
    )

    legacy_source = phase3.render_semantic_source(
        graph,
        canonical,
        strip_machine_metadata=False,
    )
    phase3.write_artifacts(
        tmp_path,
        graph=graph,
        report=phase3._updated_graph_report(graph),
        semantic_source=legacy_source,
    )
    monkeypatch.setattr(phase3, "semantic_api_enabled", lambda: True)
    monkeypatch.setattr(
        phase3,
        "_classify_hierarchy_via_openai",
        lambda *_args, **_kwargs: pytest.fail(
            "legacy metadata migration must not repeat hierarchy calls"),
    )
    monkeypatch.setattr(
        phase3,
        "_critic_hierarchy_via_openai",
        lambda *_args, **_kwargs: pytest.fail(
            "legacy metadata migration must not repeat critic calls"),
    )
    monkeypatch.setattr(
        phase3,
        "_diagnose_source_review_via_openai",
        lambda *_args, **_kwargs: pytest.fail(
            "legacy metadata migration must not repeat diagnosis calls"),
    )

    prepared = phase3.prepare_generation_graph(
        canonical=canonical,
        source_text=source,
        metadata=metadata,
        source_path=None,
        artifact_dir=tmp_path,
        verify_semantics=True,
        resume_review_graph=graph,
    )

    assert prepared["status"] == "ready"
    assert phase3.machine_metadata_migration_seal_valid(
        prepared,
        canonical=canonical,
    )
    saved_graph = json.loads(
        (tmp_path / phase3.GRAPH_FILENAME).read_text(encoding="utf-8")
    )
    saved_semantic = (
        tmp_path / phase3.SEMANTIC_SOURCE_FILENAME
    ).read_text(encoding="utf-8")
    assert saved_graph["semantic_source_sha256"] == phase3._sha256_text(
        saved_semantic)
    assert "source_origin" not in saved_semantic

    # The persisted graph must be independently sufficient even when the
    # concept checkpoint no longer carries a source-review copy.
    phase3.write_artifacts(
        tmp_path,
        graph=graph,
        report=phase3._updated_graph_report(graph),
        semantic_source=legacy_source,
    )
    artifact_only = phase3.prepare_generation_graph(
        canonical=canonical,
        source_text=source,
        metadata=metadata,
        source_path=None,
        artifact_dir=tmp_path,
        verify_semantics=True,
        resume_review_graph=None,
    )
    assert artifact_only["status"] == "ready"
    assert phase3.machine_metadata_migration_seal_valid(
        artifact_only,
        canonical=canonical,
    )


def test_saved_metadata_only_pause_is_retired_before_generation(
    db,
    first_chapter,
    monkeypatch: pytest.MonkeyPatch,
):
    source, metadata, canonical, graph, pending = (
        _legacy_metadata_source_review()
    )
    chapter = db.get(models.Chapter, first_chapter["id"])
    job = models.UploadJob(
        owner_sub=auth.LOCAL_OWNER_SUB,
        module="build_concepts",
        upload_type="document",
        learning_kind="post",
        filename="review.mmd",
        mmd_text=source,
        status="converted",
        question_inventory={"items": [], "stats": {}, "mined_types": []},
    )
    db.add(job)
    db.flush()
    stage = generation._make_concept_checkpoint(
        "source_graph_review",
        records=[],
        source_review_graph=copy.deepcopy(graph),
        source_review_context_hash=pending["context_hash"],
        source_review_decision_id=pending["decision_id"],
    )
    job.generation_checkpoint = (
        build_concepts._merge_generation_checkpoint_history(
            {},
            stage,
            fingerprint=build_concepts._generation_checkpoint_fingerprint(
                job, chapter),
            target_identity=build_concepts._generation_target_identity(
                chapter),
            target_chapter_id=chapter.id,
        )
    )
    late_stage = generation._make_concept_checkpoint(
        "pre_type_assignment",
        records=[{
            "topic": "Verified Topic",
            "parent_concept": "Review Chapter",
            "concept_title": "Visible canonical source",
            "concept_details": "Description: Verified source concept.",
            "keywords": "verified, source",
        }],
        question_task_inventory={"items": [], "stats": {}},
        mined_types={"types": []},
        method_row_snapshot=[],
    )
    job.generation_checkpoint = (
        build_concepts._merge_generation_checkpoint_history(
            job.generation_checkpoint,
            late_stage,
            fingerprint=job.generation_checkpoint["fingerprint"],
            target_identity=job.generation_checkpoint["target_identity"],
            target_chapter_id=chapter.id,
        )
    )
    db.commit()
    monkeypatch.setattr(
        build_concepts.drive_checkpoints,
        "schedule_checkpoint_backup",
        lambda *_args, **_kwargs: None,
    )
    build_concepts._persist_pending_human_decision(
        db,
        job,
        pending,
        fingerprint=job.generation_checkpoint["fingerprint"],
        target_chapter_id=chapter.id,
        owner_sub=auth.LOCAL_OWNER_SUB,
    )
    checkpoint = copy.deepcopy(job.generation_checkpoint)
    monkeypatch.setattr(
        generation,
        "_openai_json",
        lambda *_args, **_kwargs: pytest.fail(
            "metadata migration must not call a model"),
    )

    result = build_concepts._existing_human_decision_pause(
        db,
        job,
        checkpoint,
    )

    assert result is None
    db.refresh(job)
    ledger = job.generation_checkpoint["human_decisions"]
    assert ledger["pending"] is None
    assert ledger["deferred_assignment_unit_ids"] == []
    assert ledger["resolutions"] == []
    newest = generation._newest_compatible_concept_checkpoint(
        job.generation_checkpoint)
    assert newest["stage"] == "pre_type_assignment"
    saved_source_stage = generation._newest_compatible_concept_checkpoint(
        job.generation_checkpoint,
        allowed_stages={"source_graph_review"},
    )
    assert saved_source_stage["source_review_graph"] == graph

    migrated = phase3._compatible_source_review_graph(
        graph,
        canonical=canonical,
        metadata=metadata,
        page_bundle=None,
    )
    assert phase3.machine_metadata_migration_seal_valid(
        migrated,
        canonical=canonical,
    )
    refreshed_source_stage = generation._make_concept_checkpoint(
        "source_graph_review",
        records=[],
        source_review_graph=migrated,
        source_review_context_hash=pending["context_hash"],
        source_review_decision_id=pending["decision_id"],
        source_review_metadata_sanitization_applied=True,
    )
    preserved = build_concepts._merge_generation_checkpoint_history(
        job.generation_checkpoint,
        refreshed_source_stage,
        fingerprint=job.generation_checkpoint["fingerprint"],
        target_identity=job.generation_checkpoint["target_identity"],
        target_chapter_id=chapter.id,
    )
    assert generation._newest_compatible_concept_checkpoint(
        preserved)["stage"] == "pre_type_assignment"
    assert {
        row["stage"] for row in preserved["checkpoints"]
    } >= {"source_graph_review", "pre_type_assignment"}

    # A concurrent user answer must win over stale automatic retirement. The
    # conditional checkpoint update may not erase the newly committed audit.
    build_concepts._persist_pending_human_decision(
        db,
        job,
        pending,
        fingerprint=job.generation_checkpoint["fingerprint"],
        target_chapter_id=chapter.id,
        owner_sub=auth.LOCAL_OWNER_SUB,
    )
    stale_checkpoint = copy.deepcopy(job.generation_checkpoint)

    def resolve_concurrently(*_args, **_kwargs):
        with SessionLocal() as concurrent_db:
            build_concepts.record_human_semantic_decision(
                concurrent_db,
                job.id,
                pending["decision_id"],
                choice="replace_source",
                owner_sub=auth.LOCAL_OWNER_SUB,
            )
        return True

    monkeypatch.setattr(
        phase3,
        "machine_metadata_source_review_is_obsolete",
        resolve_concurrently,
    )
    concurrent_result = build_concepts._existing_human_decision_pause(
        db,
        job,
        stale_checkpoint,
    )

    assert concurrent_result is None
    db.refresh(job)
    concurrent_ledger = job.generation_checkpoint["human_decisions"]
    assert concurrent_ledger["pending"] is None
    assert len(concurrent_ledger["resolutions"]) == 1
    assert concurrent_ledger["resolutions"][0]["choice"] == "replace_source"


def test_nonregex_rich_text_failure_pauses_with_candidate_and_resolves(
    monkeypatch: pytest.MonkeyPatch,
):
    _source, canonical, graph, pages = _verified_graph_and_pages()
    assert graph["status"] == "failed"
    assert graph["issues"] == [{
        "severity": "error",
        "code": "semantic_source_rich_text",
        "block_ids": ["BLK-00005"],
        "message": (
            "Phase 3 semantic source has non-canonical rich text: "
            "raw_math_delimiter"
        ),
    }]
    original_identity = {
        key: copy.deepcopy(graph.get(key))
        for key in ("source_contract_hash", "topics", "tasks", "figures")
    }
    diagnostic_calls = 0

    def diagnose(payload: dict, *, source_path: Path | None) -> dict:
        nonlocal diagnostic_calls
        diagnostic_calls += 1
        return _diagnostic(payload, source_path=source_path)

    monkeypatch.setattr(phase3, "semantic_api_enabled", lambda: True)
    monkeypatch.setattr(
        phase3, "_diagnose_source_review_via_openai", diagnose)

    with pytest.raises(semantic_recovery.HumanDecisionRequired) as paused:
        phase3._source_review_graph_or_raise(
            graph,
            canonical=canonical,
            page_bundle=pages,
            source_path=None,
        )

    pending = paused.value.pending_decision
    assert pending["kind"] == "phase3_source_graph_review"
    assert pending["diagnosis"]
    assert pending["decision_question"]
    assert pending["item"]["unit_id"] == "BLK-00005"
    assert pending["item"]["type_id"] == "semantic_source_rich_text"
    assert pending["candidates"]
    assert {
        row["choice"] for row in pending["options"]
    } >= {"accept_recommended", "select_candidate", "custom_instruction"}
    assert any(
        row["label"] == "semantic_source_rich_text"
        and "raw_math_delimiter" in row["text"]
        for row in pending["evidence"]
    )
    assert diagnostic_calls == 1

    resolution = {
        **copy.deepcopy(pending),
        "choice": "accept_recommended",
        "target_id": next(
            row["target_id"]
            for row in pending["options"]
            if row["choice"] == "accept_recommended"
        ),
    }
    with phase3.human_source_resolution_context([resolution]):
        repaired = phase3._source_review_graph_or_raise(
            graph,
            canonical=canonical,
            page_bundle=pages,
            source_path=None,
        )

    assert repaired["status"] == "ready"
    assert diagnostic_calls == 1
    assert "$value1" not in phase3.render_semantic_source(
        repaired, canonical)
    assert not phase3.validate_graph(
        repaired,
        canonical=canonical,
        semantic_source=phase3.render_semantic_source(repaired, canonical),
    )
    assert {
        key: repaired.get(key) for key in original_identity
    } == original_identity


def test_source_resolution_requires_exact_decision_and_context(
    monkeypatch: pytest.MonkeyPatch,
):
    _source, canonical, graph, pages = _verified_graph_and_pages()
    calls = 0

    def diagnose(payload: dict, *, source_path: Path | None) -> dict:
        nonlocal calls
        calls += 1
        return _diagnostic(payload, source_path=source_path)

    monkeypatch.setattr(phase3, "semantic_api_enabled", lambda: True)
    monkeypatch.setattr(
        phase3, "_diagnose_source_review_via_openai", diagnose)
    with pytest.raises(semantic_recovery.HumanDecisionRequired) as first:
        phase3._source_review_graph_or_raise(
            graph,
            canonical=canonical,
            page_bundle=pages,
            source_path=None,
        )
    pending = first.value.pending_decision
    target_id = next(
        row["target_id"]
        for row in pending["options"]
        if row["choice"] == "accept_recommended"
    )
    mismatches = [
        {
            **copy.deepcopy(pending),
            "context_hash": "f" * 64,
            "choice": "accept_recommended",
            "target_id": target_id,
        },
        {
            **copy.deepcopy(pending),
            "decision_id": "phase3-source-" + "f" * 24,
            "choice": "accept_recommended",
            "target_id": target_id,
        },
    ]
    for resolution in mismatches:
        with phase3.human_source_resolution_context([resolution]):
            with pytest.raises(
                semantic_recovery.HumanDecisionRequired
            ) as repeated:
                phase3._source_review_graph_or_raise(
                    graph,
                    canonical=canonical,
                    page_bundle=pages,
                    source_path=None,
                )
        assert repeated.value.decision_id == pending["decision_id"]
    assert calls == 1


def test_prepare_turns_exact_generic_rich_text_failure_into_one_decision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    source, canonical, _graph, pages = _verified_graph_and_pages()
    source_path = tmp_path / "review.pdf"
    source_path.write_bytes(b"%PDF-review")
    calls = {"hierarchy": 0, "critic": 0, "diagnostic": 0}

    def classify(payload: dict) -> dict:
        calls["hierarchy"] += 1
        return {
            "sections": [{
                "section_id": row["section_id"],
                "role": row["baseline_role"],
                "parent_section_id": "",
                "confidence": 0.999,
                "evidence": ["verified test hierarchy"],
            } for row in payload["sections"]]
        }

    def critic(_payload: dict) -> dict:
        calls["critic"] += 1
        return {
            "verdict": "verified",
            "confidence": 0.999,
            "repairs": [],
            "issues": [],
        }

    def diagnose(payload: dict, *, source_path: Path | None) -> dict:
        calls["diagnostic"] += 1
        return _diagnostic(payload, source_path=source_path)

    monkeypatch.setattr(phase3, "semantic_api_enabled", lambda: True)
    monkeypatch.setattr(phase3, "_classify_hierarchy_via_openai", classify)
    monkeypatch.setattr(phase3, "_critic_hierarchy_via_openai", critic)
    monkeypatch.setattr(
        phase3, "load_page_evidence", lambda *_args, **_kwargs: pages)
    monkeypatch.setattr(
        phase3, "_diagnose_source_review_via_openai", diagnose)

    with pytest.raises(semantic_recovery.HumanDecisionRequired) as paused:
        phase3.prepare_generation_graph(
            canonical=canonical,
            source_text=source,
            metadata={
                "board": "CBSE",
                "grade": "10",
                "subject": "History",
                "chapter_title": "Review Chapter",
                "learning_kind": "Post",
            },
            source_path=source_path,
            artifact_dir=tmp_path,
            verify_semantics=True,
        )

    pending = paused.value.pending_decision
    assert calls == {"hierarchy": 1, "critic": 1, "diagnostic": 1}
    assert pending["item"]["unit_id"] == "BLK-00005"
    assert pending["item"]["type_id"] == "semantic_source_rich_text"
    assert pending["candidates"]
    persisted = json.loads(
        (tmp_path / phase3.GRAPH_FILENAME).read_text(encoding="utf-8"))
    assert persisted["status"] == "failed"
    assert persisted["issues"][0]["block_ids"] == ["BLK-00005"]
    assert persisted[phase3._SOURCE_REVIEW_KEY]["decision_id"] == (
        pending["decision_id"])


def test_three_source_discrepancies_advance_without_rebuilding_hierarchy(
    monkeypatch: pytest.MonkeyPatch,
):
    _source, canonical, graph, pages = _verified_graph_and_pages(3)
    expected_blocks = ["BLK-00005", "BLK-00007", "BLK-00009"]
    assert graph["issues"][0]["block_ids"] == expected_blocks
    monkeypatch.setattr(phase3, "semantic_api_enabled", lambda: True)
    monkeypatch.setattr(
        phase3, "_diagnose_source_review_via_openai", _diagnostic)

    with pytest.raises(semantic_recovery.HumanDecisionRequired) as first:
        phase3._source_review_graph_or_raise(
            graph,
            canonical=canonical,
            page_bundle=pages,
            source_path=None,
        )
    pending = first.value.pending_decision
    pending_ids = [pending["item"]["unit_id"]]
    repaired = None
    for index, expected_block in enumerate(expected_blocks):
        assert pending["item"]["unit_id"] == expected_block
        target_id = next(
            row["target_id"]
            for row in pending["options"]
            if row["choice"] == "accept_recommended"
        )
        with phase3.human_source_resolution_context([{
            **copy.deepcopy(pending),
            "choice": "accept_recommended",
            "target_id": target_id,
        }]):
            if index < len(expected_blocks) - 1:
                with pytest.raises(
                    semantic_recovery.HumanDecisionRequired
                ) as paused:
                    phase3._source_review_graph_or_raise(
                        graph,
                        canonical=canonical,
                        page_bundle=pages,
                        source_path=None,
                    )
                pending = paused.value.pending_decision
                pending_ids.append(pending["item"]["unit_id"])
            else:
                repaired = phase3._source_review_graph_or_raise(
                    graph,
                    canonical=canonical,
                    page_bundle=pages,
                    source_path=None,
                )
    # The first two pauses were captured in-loop; obtain the first identity
    # from the applied repair ledger and prove canonical source order.
    applied = [
        row["block_id"] for row in repaired["source_fusion_repairs"]
        if row.get("resolved_via") == "human_decision"
    ]
    assert applied == expected_blocks
    assert pending_ids == expected_blocks
    assert repaired is not None
    assert repaired["status"] == "ready"


def test_custom_instruction_is_interpreted_once_then_repauses_actionably(
    monkeypatch: pytest.MonkeyPatch,
):
    _source, canonical, graph, pages = _verified_graph_and_pages()
    monkeypatch.setattr(phase3, "semantic_api_enabled", lambda: True)
    monkeypatch.setattr(
        phase3, "_diagnose_source_review_via_openai", _diagnostic)
    with pytest.raises(semantic_recovery.HumanDecisionRequired) as first:
        phase3._source_review_graph_or_raise(
            graph,
            canonical=canonical,
            page_bundle=pages,
            source_path=None,
        )
    pending = first.value.pending_decision
    calls = 0

    def interpret(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return {
            "decision": "needs_clarification",
            "selected_target_id": "NONE",
            "question": "Choose the exact verified PDF block directly.",
            "reason": "The instruction did not identify one supplied ID.",
        }

    monkeypatch.setattr(
        phase3, "_interpret_custom_source_instruction_via_openai", interpret)
    resolution = {
        **copy.deepcopy(pending),
        "choice": "custom_instruction",
        "instruction": "Use whichever one looks right.",
    }
    with phase3.human_source_resolution_context([resolution]):
        with pytest.raises(semantic_recovery.HumanDecisionRequired) as second:
            phase3._source_review_graph_or_raise(
                graph,
                canonical=canonical,
                page_bundle=pages,
                source_path=None,
            )
    next_pending = second.value.pending_decision
    choices = {row["choice"] for row in next_pending["options"]}
    assert calls == 1
    assert "custom_instruction" not in choices
    assert choices >= {"select_candidate", "replace_source"}
    assert next_pending["decision_id"] != pending["decision_id"]

    # Re-rendering the same unresolved state cannot re-run the interpreter.
    with phase3.human_source_resolution_context([resolution]):
        with pytest.raises(semantic_recovery.HumanDecisionRequired) as repeated:
            phase3._source_review_graph_or_raise(
                graph,
                canonical=canonical,
                page_bundle=pages,
                source_path=None,
            )
    assert calls == 1
    assert repeated.value.decision_id == next_pending["decision_id"]


def test_custom_provider_failure_does_not_consume_instruction(
    monkeypatch: pytest.MonkeyPatch,
):
    _source, canonical, graph, pages = _verified_graph_and_pages()
    monkeypatch.setattr(phase3, "semantic_api_enabled", lambda: True)
    monkeypatch.setattr(
        phase3, "_diagnose_source_review_via_openai", _diagnostic)
    with pytest.raises(semantic_recovery.HumanDecisionRequired) as first:
        phase3._source_review_graph_or_raise(
            graph,
            canonical=canonical,
            page_bundle=pages,
            source_path=None,
        )
    pending = first.value.pending_decision

    def provider_failure(*_args, **_kwargs):
        raise ConnectionError("provider unavailable")

    monkeypatch.setattr(
        phase3,
        "_interpret_custom_source_instruction_via_openai",
        provider_failure,
    )
    resolution = {
        **copy.deepcopy(pending),
        "choice": "custom_instruction",
        "instruction": "Use the paragraph with the exact equation.",
    }
    with phase3.human_source_resolution_context([resolution]):
        with pytest.raises(RuntimeError, match="was not consumed"):
            phase3._source_review_graph_or_raise(
                graph,
                canonical=canonical,
                page_bundle=pages,
                source_path=None,
            )
    state = graph[phase3._SOURCE_REVIEW_KEY]
    assert state["decision_id"] == pending["decision_id"]
    assert state["custom_interpretation_used"] is False
    assert "custom_instruction" in {
        row["choice"]
        for row in phase3._source_review_pending(
            graph, state, canonical=canonical)["options"]
    }


def test_resume_uses_checkpoint_graph_without_hierarchy_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    source, canonical, graph, pages = _verified_graph_and_pages()
    source_path = tmp_path / "review.pdf"
    source_path.write_bytes(b"%PDF-review")
    metadata = {
        "board": "CBSE",
        "grade": "10",
        "subject": "History",
        "chapter_title": "Review Chapter",
        "learning_kind": "Post",
    }
    monkeypatch.setattr(phase3, "semantic_api_enabled", lambda: True)
    monkeypatch.setattr(
        phase3, "_diagnose_source_review_via_openai", _diagnostic)
    monkeypatch.setattr(
        phase3, "load_page_evidence", lambda *_args, **_kwargs: pages)
    with pytest.raises(semantic_recovery.HumanDecisionRequired) as paused:
        phase3._source_review_graph_or_raise(
            graph,
            canonical=canonical,
            page_bundle=pages,
            source_path=None,
        )
    pending = paused.value.pending_decision
    phase3.write_artifacts(
        tmp_path,
        graph=graph,
        report=phase3._updated_graph_report(graph),
        semantic_source=phase3.render_semantic_source(graph, canonical),
        page_bundle=pages,
    )

    def unexpected(*_args, **_kwargs):
        raise AssertionError("resume must not rebuild the verified hierarchy")

    monkeypatch.setattr(
        phase3, "_classify_hierarchy_via_openai", unexpected)
    monkeypatch.setattr(
        phase3, "_critic_hierarchy_via_openai", unexpected)
    target_id = next(
        row["target_id"]
        for row in pending["options"]
        if row["choice"] == "accept_recommended"
    )
    with phase3.human_source_resolution_context([{
        **copy.deepcopy(pending),
        "choice": "accept_recommended",
        "target_id": target_id,
    }]):
        resumed = phase3.prepare_generation_graph(
            canonical=canonical,
            source_text=source,
            metadata=metadata,
            source_path=source_path,
            artifact_dir=tmp_path,
            verify_semantics=True,
            resume_review_graph=copy.deepcopy(graph),
        )

    assert resumed["status"] == "ready"


def test_ready_source_checkpoint_reuses_graph_and_rejects_pdf_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    source, canonical, graph, pages = _verified_graph_and_pages()
    metadata = {
        "board": "CBSE",
        "grade": "10",
        "subject": "History",
        "chapter_title": "Review Chapter",
        "learning_kind": "Post",
    }
    monkeypatch.setattr(phase3, "semantic_api_enabled", lambda: True)
    monkeypatch.setattr(
        phase3, "_diagnose_source_review_via_openai", _diagnostic)
    with pytest.raises(semantic_recovery.HumanDecisionRequired) as paused:
        phase3._source_review_graph_or_raise(
            graph,
            canonical=canonical,
            page_bundle=pages,
            source_path=None,
        )
    pending = paused.value.pending_decision
    target_id = next(
        row["target_id"]
        for row in pending["options"]
        if row["choice"] == "accept_recommended"
    )
    with phase3.human_source_resolution_context([{
        **copy.deepcopy(pending),
        "choice": "accept_recommended",
        "target_id": target_id,
    }]):
        ready = phase3._source_review_graph_or_raise(
            graph,
            canonical=canonical,
            page_bundle=pages,
            source_path=None,
        )
    source_path = tmp_path / "review.pdf"
    source_path.write_bytes(b"%PDF-review")

    def unexpected(*_args, **_kwargs):
        raise AssertionError("ready source checkpoint must not replay providers")

    monkeypatch.setattr(
        phase3, "_classify_hierarchy_via_openai", unexpected)
    monkeypatch.setattr(
        phase3, "_critic_hierarchy_via_openai", unexpected)
    monkeypatch.setattr(
        phase3, "load_page_evidence", lambda *_args, **_kwargs: pages)
    artifact_dir = tmp_path / "missing-artifacts"
    artifact_dir.mkdir()
    restored = phase3.prepare_generation_graph(
        canonical=canonical,
        source_text=source,
        metadata=metadata,
        source_path=source_path,
        artifact_dir=artifact_dir,
        verify_semantics=True,
        resume_review_graph=copy.deepcopy(ready),
    )
    assert restored["status"] == "ready"

    drifted_pages = copy.deepcopy(pages)
    drifted_pages["pdf_sha256"] = "different-pdf"
    monkeypatch.setattr(
        phase3,
        "load_page_evidence",
        lambda *_args, **_kwargs: drifted_pages,
    )
    with pytest.raises(ValueError, match="no model retry was started"):
        phase3.prepare_generation_graph(
            canonical=canonical,
            source_text=source,
            metadata=metadata,
            source_path=source_path,
            artifact_dir=tmp_path / "still-missing",
            verify_semantics=True,
            resume_review_graph=copy.deepcopy(ready),
        )


def test_ready_cached_graph_without_override_rejects_pdf_drift_before_models(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    source = (
        "# Review Chapter\n\n"
        "## Verified Topic\n\n"
        "Source item 1 has value1+1.\n"
    )
    metadata = {
        "board": "CBSE",
        "grade": "10",
        "subject": "History",
        "chapter_title": "Review Chapter",
        "learning_kind": "Post",
    }
    pages = {
        "schema_version": "1.1.0",
        "pdf_sha256": "original-pdf",
        "pages": [{
            "page_id": "PAGE-0001",
            "page_number": 1,
            "blocks": [{
                "reading_order": 1,
                "kind": "paragraph",
                "text": "Source item 1 has value1+1.",
                "confidence": 0.999,
            }],
        }],
    }
    canonical = phase2.compile_phase2_source(
        source,
        source_filename="review.pdf",
        consumer_module="build_concepts",
    ).canonical

    def classify(payload: dict) -> dict:
        return {
            "sections": [{
                "section_id": row["section_id"],
                "role": row["baseline_role"],
                "parent_section_id": "",
                "confidence": 0.999,
                "evidence": ["verified test hierarchy"],
            } for row in payload["sections"]]
        }

    graph, report = phase3.compile_semantic_graph(
        canonical,
        source_text=source,
        metadata=metadata,
        page_bundle=pages,
        hierarchy_provider=classify,
        critic_provider=lambda _payload: {
            "verdict": "verified",
            "confidence": 0.999,
            "repairs": [],
            "issues": [],
        },
    )
    assert graph["status"] == "ready"
    semantic_source = phase3.render_semantic_source(graph, canonical)
    phase3.write_artifacts(
        tmp_path,
        graph=graph,
        report=report,
        semantic_source=semantic_source,
        page_bundle=pages,
    )
    source_path = tmp_path / "review.pdf"
    source_path.write_bytes(b"%PDF-drifted")
    drifted_pages = copy.deepcopy(pages)
    drifted_pages["pdf_sha256"] = "different-pdf"
    monkeypatch.setattr(
        phase3,
        "load_page_evidence",
        lambda *_args, **_kwargs: drifted_pages,
    )

    def unexpected(*_args, **_kwargs):
        raise AssertionError("PDF drift must stop before hierarchy providers")

    monkeypatch.setattr(
        phase3, "_classify_hierarchy_via_openai", unexpected)
    monkeypatch.setattr(
        phase3, "_critic_hierarchy_via_openai", unexpected)
    with pytest.raises(ValueError, match="no model retry was started"):
        phase3.prepare_generation_graph(
            canonical=canonical,
            source_text=source,
            metadata=metadata,
            source_path=source_path,
            artifact_dir=tmp_path,
            verify_semantics=True,
        )


def test_integrity_failures_are_not_human_overridable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    source = "# Chapter\n\n## Topic\n\nValid source text.\n"
    canonical = phase2.compile_phase2_source(
        source,
        source_filename="source.mmd",
        consumer_module="build_concepts",
    ).canonical
    graph, report = phase3.compile_semantic_graph(
        canonical,
        source_text=source,
        metadata={"chapter_title": "Chapter"},
    )
    graph["blocks"][0]["topic_id"] = "TOPIC-INVENTED"
    semantic = phase3.render_semantic_source(graph, canonical)
    graph["semantic_source_sha256"] = phase3._sha256_text(semantic)
    graph["issues"] = phase3.validate_graph(
        graph, canonical=canonical, semantic_source=semantic)
    graph["status"] = "failed"
    report = phase3._updated_graph_report(graph, report)
    assert not phase3._human_reviewable_source_graph(
        graph, canonical=canonical)

    monkeypatch.setattr(
        phase3,
        "compile_semantic_graph",
        lambda *_args, **_kwargs: (
            copy.deepcopy(graph), copy.deepcopy(report)),
    )
    monkeypatch.setattr(
        phase3,
        "reconcile_source_anomalies",
        lambda value, **_kwargs: value,
    )
    monkeypatch.setattr(
        phase3,
        "_source_review_graph_or_raise",
        lambda *_args, **_kwargs: pytest.fail(
            "identity corruption must not enter human override"),
    )
    with pytest.raises(
        ValueError, match="not safe for concept generation"
    ):
        phase3.prepare_generation_graph(
            canonical=canonical,
            source_text=source,
            metadata={"chapter_title": "Chapter"},
            source_path=None,
            artifact_dir=tmp_path,
            verify_semantics=True,
        )


def test_contract_emits_early_source_checkpoint_before_pause(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    source = "# Review Chapter\n\n## Topic\n\nSource text.\n"
    canonical = phase2.compile_phase2_source(
        source,
        source_filename="review.pdf",
        consumer_module="build_concepts",
    ).canonical
    context_hash = "a" * 64
    pending = {
        "decision_id": "phase3-source-" + "a" * 24,
        "context_hash": context_hash,
    }
    graph = {
        "source_contract_hash": phase3.source_contract_hash(canonical),
        "semantic_context_hash": "b" * 64,
        phase3._SOURCE_REVIEW_KEY: {
            "version": phase3._SOURCE_REVIEW_VERSION,
            **pending,
        },
    }
    session = {
        "job_id": 91,
        "job": SimpleNamespace(id=91),
        "canonical": canonical,
        "source_path": None,
        "artifact_dir": tmp_path,
        "raw_source": source,
        "graph": None,
    }

    def pause(**_kwargs):
        session["graph"] = copy.deepcopy(graph)
        raise semantic_recovery.HumanDecisionRequired(pending)

    monkeypatch.setattr(phase3, "prepare_generation_graph", pause)
    saved: list[dict] = []
    with phase2.activate(canonical), phase3.activate_session(session):
        with pytest.raises(semantic_recovery.HumanDecisionRequired):
            generation.concepts_from_mmd(
                source,
                subject="History",
                board="CBSE",
                grade="10",
                chapter_title="Review Chapter",
                learning_kind="Post",
                live=False,
                checkpoint_callback=saved.append,
            )

    assert len(saved) == 1
    assert saved[0]["stage"] == "source_graph_review"
    assert saved[0]["source_review_context_hash"] == context_hash
    assert saved[0]["source_review_graph"] == graph
    assert generation._compatible_concept_checkpoint_entry(saved[0])
    assert "source_graph_review" not in generation._POST_CONCEPT_CHECKPOINT_STAGES


def test_source_review_history_does_not_regress_later_checkpoint_progress(
    db,
    first_chapter,
):
    chapter = db.get(models.Chapter, first_chapter["id"])
    job = models.UploadJob(
        owner_sub=auth.LOCAL_OWNER_SUB,
        module="build_concepts",
        upload_type="document",
        learning_kind="post",
        filename="source.mmd",
        mmd_text="# Chapter\nSource",
        status="converted",
    )
    db.add(job)
    db.flush()
    args = {
        "fingerprint": build_concepts._generation_checkpoint_fingerprint(
            job, chapter),
        "target_identity": build_concepts._generation_target_identity(chapter),
        "target_chapter_id": chapter.id,
    }
    later = generation._make_concept_checkpoint(
        "pre_type_assignment",
        records=[],
        question_task_inventory={"items": [], "stats": {}},
        mined_types={"types": []},
        method_row_snapshot=[],
    )
    durable = build_concepts._merge_generation_checkpoint_history(
        {}, later, **args)
    source_review = generation._make_concept_checkpoint(
        "source_graph_review",
        records=[],
        source_review_graph={
            "source_contract_hash": "source-contract",
            "semantic_context_hash": "semantic-context",
        },
        source_review_context_hash="d" * 64,
    )
    durable = build_concepts._merge_generation_checkpoint_history(
        durable, source_review, **args)

    assert durable["stage"] == "pre_type_assignment"
    assert durable["progress"] == pytest.approx(0.81)
    assert {
        row["stage"] for row in durable["checkpoints"]
    } >= {"pre_type_assignment", "source_graph_review"}

    ready_source_review = generation._make_concept_checkpoint(
        "source_graph_review",
        records=[],
        source_review_graph={
            "status": "ready",
            "source_contract_hash": "source-contract",
            "semantic_context_hash": "semantic-context",
        },
        source_review_context_hash="d" * 64,
        source_review_resolution_applied=True,
    )
    durable = build_concepts._merge_generation_checkpoint_history(
        durable, ready_source_review, **args)
    assert durable["stage"] == "source_graph_review"
    assert durable["progress"] == pytest.approx(0.05)
    assert [
        row["stage"] for row in durable["checkpoints"]
    ] == ["source_graph_review"]


def test_pending_source_review_preserves_usage_and_repeat_generate_is_free(
    db,
    first_chapter,
    monkeypatch: pytest.MonkeyPatch,
):
    # Billing preservation across a saved pause is an attended-mode contract.
    # Unattended runs cannot answer a source-replacement decision, so they end
    # the run instead of parking it (see test_no_manual_review_invariant.py).
    monkeypatch.setenv("AEGIS_UNATTENDED_COMPLETION", "0")
    chapter = db.get(models.Chapter, first_chapter["id"])
    job = models.UploadJob(
        owner_sub=auth.LOCAL_OWNER_SUB,
        module="build_concepts",
        upload_type="document",
        learning_kind="post",
        filename="usage-source.mmd",
        mmd_text="# Review Chapter\n\nSource requiring review.",
        status="converted",
        question_inventory={"items": [], "stats": {}, "mined_types": []},
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    monkeypatch.setattr(
        build_concepts.drive_checkpoints,
        "schedule_checkpoint_backup",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        build_concepts.config, "use_live_generation", lambda: True)
    generation_calls = 0
    active_usage: openai_usage.UsageAccumulator | None = None
    context_hash = "b" * 64

    def pause_after_one_billable_diagnosis(
        *_args,
        checkpoint_callback=None,
        **_kwargs,
    ):
        nonlocal generation_calls
        generation_calls += 1
        assert checkpoint_callback is not None
        assert active_usage is not None
        active_usage.add(
            model="gpt-5.4-mini-2026-03-17",
            input_tokens=100,
            cached_input_tokens=20,
            cache_write_tokens=30,
            output_tokens=40,
            reasoning_tokens=10,
            total_tokens=140,
            estimated_cost_usd="0.25",
        )
        checkpoint_callback(generation._make_concept_checkpoint(
            "source_graph_review",
            records=[],
            source_review_graph={
                "status": "review_required",
                "source_contract_hash": "source-contract",
                "semantic_context_hash": "semantic-context",
            },
            source_review_context_hash=context_hash,
            source_review_decision_id=(
                "phase3-source-" + context_hash[:24]),
        ))
        raise semantic_recovery.HumanDecisionRequired({
            "decision_id": "phase3-source-" + context_hash[:24],
            "context_hash": context_hash,
            "kind": "phase3_source_graph_review",
            "phase": "3",
            "conflict": "Verified source evidence requires a decision.",
            "diagnosis": "The source block cannot be certified automatically.",
            "decision_question": "How should this source block be handled?",
            "item": {"unit_id": "BLK-0001"},
            "candidates": [],
            "evidence": [],
            "options": [{
                "choice": "replace_source",
                "label": "Replace or correct the source file",
            }],
        })

    monkeypatch.setattr(
        build_concepts.generation,
        "concepts_from_mmd",
        pause_after_one_billable_diagnosis,
    )
    with openai_usage.track() as first_usage:
        active_usage = first_usage
        first = uploads.run_with_openai_usage(
            db,
            job.id,
            lambda: build_concepts.generate_post_learning(
                db,
                job.id,
                chapter.id,
                owner_sub=auth.LOCAL_OWNER_SUB,
            ),
            owner_sub=auth.LOCAL_OWNER_SUB,
        )
    expected = {
        "request_count": 1,
        "input_tokens": 100,
        "cached_input_tokens": 20,
        "cache_write_tokens": 30,
        "output_tokens": 40,
        "reasoning_tokens": 10,
        "total_tokens": 140,
        "estimated_cost_usd": 0.25,
    }
    assert first["status"] == "awaiting_decision"
    assert {
        key: first["pending_decision"]["cumulative_usage"][key]
        for key in expected
    } == expected
    assert {
        key: first["openai_usage"][key] for key in expected
    } == expected
    db.refresh(job)
    durable_before = copy.deepcopy(job.openai_usage)

    with openai_usage.track() as repeated_usage:
        active_usage = repeated_usage
        repeated = uploads.run_with_openai_usage(
            db,
            job.id,
            lambda: build_concepts.generate_post_learning(
                db,
                job.id,
                chapter.id,
                owner_sub=auth.LOCAL_OWNER_SUB,
            ),
            owner_sub=auth.LOCAL_OWNER_SUB,
        )
    assert repeated["status"] == "awaiting_decision"
    assert generation_calls == 1
    assert repeated_usage.summary()["request_count"] == 0
    assert {
        key: repeated["pending_decision"]["cumulative_usage"][key]
        for key in expected
    } == expected
    assert {
        key: repeated["openai_usage"][key] for key in expected
    } == expected
    db.refresh(job)
    assert job.openai_usage == durable_before


def test_source_candidate_submission_is_exact_api_free_and_nonportable(
    db,
    first_chapter,
    monkeypatch: pytest.MonkeyPatch,
):
    chapter = db.get(models.Chapter, first_chapter["id"])
    job = models.UploadJob(
        owner_sub=auth.LOCAL_OWNER_SUB,
        module="build_concepts",
        upload_type="document",
        learning_kind="post",
        filename="source.mmd",
        mmd_text="# Chapter\nSource",
        status="converted",
        question_inventory={"items": [], "stats": {}, "mined_types": []},
    )
    db.add(job)
    db.flush()
    stage = generation._make_concept_checkpoint(
        "source_graph_review",
        records=[],
        source_review_graph={
            "source_contract_hash": "source-contract",
            "semantic_context_hash": "semantic-context",
        },
        source_review_context_hash="c" * 64,
    )
    job.generation_checkpoint = (
        build_concepts._merge_generation_checkpoint_history(
            {},
            stage,
            fingerprint=build_concepts._generation_checkpoint_fingerprint(
                job, chapter),
            target_identity=build_concepts._generation_target_identity(chapter),
            target_chapter_id=chapter.id,
        )
    )
    db.commit()
    monkeypatch.setattr(
        build_concepts.drive_checkpoints,
        "schedule_checkpoint_backup",
        lambda *_args, **_kwargs: None,
    )
    pending = build_concepts._persist_pending_human_decision(
        db,
        job,
        {
            "decision_id": "phase3-source-" + "c" * 24,
            "context_hash": "c" * 64,
            "kind": "phase3_source_graph_review",
            "phase": "3",
            "conflict": "Choose verified evidence.",
            "diagnosis": "A source block is malformed.",
            "decision_question": "Which verified block should be used?",
            "item": {"unit_id": "BLK-0001"},
            "candidates": [{
                "target_id": "PAGE-0001:0001",
                "concept_id": "PAGE-0001:0001",
                "title": "PDF page 1",
            }, {
                "target_id": "PAGE-0001:0002",
                "concept_id": "PAGE-0001:0002",
                "title": "PDF page 1 alternate",
            }],
            "evidence": [],
            "options": [{
                "choice": "accept_recommended",
                "label": "Use recommended evidence",
                "target_id": "PAGE-0001:0001",
                "recommended": True,
            }, {
                "choice": "select_candidate",
                "label": "Choose verified evidence",
            }],
        },
        fingerprint=job.generation_checkpoint["fingerprint"],
        target_chapter_id=chapter.id,
        owner_sub=auth.LOCAL_OWNER_SUB,
    )
    assert pending["checkpoint_progress"] == pytest.approx(0.05)
    monkeypatch.setattr(
        generation,
        "_openai_json",
        lambda *_args, **_kwargs: pytest.fail(
            "decision submission must make zero model calls"),
    )

    with pytest.raises(ValueError, match="recommended candidate"):
        build_concepts.record_human_semantic_decision(
            db,
            job.id,
            pending["decision_id"],
            choice="accept_recommended",
            target_id="PAGE-0001:0002",
            owner_sub=auth.LOCAL_OWNER_SUB,
        )
    with pytest.raises(ValueError, match="pending decision candidates"):
        build_concepts.record_human_semantic_decision(
            db,
            job.id,
            pending["decision_id"],
            choice="select_candidate",
            target_id="PAGE-INVENTED:9999",
            owner_sub=auth.LOCAL_OWNER_SUB,
        )
    recorded = build_concepts.record_human_semantic_decision(
        db,
        job.id,
        pending["decision_id"],
        choice="select_candidate",
        target_id="PAGE-0001:0001",
        owner_sub=auth.LOCAL_OWNER_SUB,
    )
    assert recorded["status"] == "decision_recorded"
    assert recorded["resume_required"] is True
    assert recorded["resolved_decision"]["target_id"] == "PAGE-0001:0001"

    with pytest.raises(ValueError, match="cannot be exported"):
        checkpoints.export_bundle(
            db, job.id, owner_sub=auth.LOCAL_OWNER_SUB)

    replacement_pending = build_concepts._persist_pending_human_decision(
        db,
        job,
        {
            "decision_id": "phase3-source-" + "e" * 24,
            "context_hash": "e" * 64,
            "kind": "phase3_source_graph_review",
            "phase": "3",
            "conflict": "The source must be replaced.",
            "decision_question": "Replace this source?",
            "item": {"unit_id": "BLK-0001"},
            "candidates": [],
            "evidence": [],
            "options": [{
                "choice": "replace_source",
                "label": "Replace or correct the source",
            }],
        },
        fingerprint=job.generation_checkpoint["fingerprint"],
        target_chapter_id=chapter.id,
        owner_sub=auth.LOCAL_OWNER_SUB,
    )
    replacement = build_concepts.record_human_semantic_decision(
        db,
        job.id,
        replacement_pending["decision_id"],
        choice="replace_source",
        owner_sub=auth.LOCAL_OWNER_SUB,
    )
    assert replacement["resume_required"] is False
    db.refresh(job)
    before = copy.deepcopy(
        job.generation_checkpoint["human_decisions"]["resolutions"])
    monkeypatch.setattr(
        build_concepts.generation,
        "concepts_from_mmd",
        lambda *_args, **_kwargs: pytest.fail(
            "terminal source replacement must stop before generation"),
    )
    for _ in range(2):
        with pytest.raises(ValueError, match="Source replacement is required"):
            build_concepts.generate_post_learning(
                db,
                job.id,
                chapter.id,
                owner_sub=auth.LOCAL_OWNER_SUB,
            )
    db.refresh(job)
    assert job.generation_checkpoint["human_decisions"]["resolutions"] == before


def test_human_intervention_multimodal_call_is_single_attempt(
    monkeypatch: pytest.MonkeyPatch,
):
    import openai

    calls = 0

    class CompletionAPI:
        def create(self, **_kwargs):
            nonlocal calls
            calls += 1
            raise RuntimeError("malformed provider response")

    class FakeOpenAI:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=CompletionAPI())

    class Gate:
        def release(self):
            return None

    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)
    monkeypatch.setattr(generation, "_get_openai_gate", Gate)
    monkeypatch.setattr(
        generation,
        "_acquire_openai_slot",
        lambda *_args, **_kwargs: None,
    )
    with pytest.raises(RuntimeError, match="single attempt failed"):
        phase22._openai_multimodal_json(
            system="Diagnose only.",
            prompt="One bounded discrepancy.",
            pages=[],
            response_schema={
                "name": "single_attempt",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": False,
                },
            },
            single_attempt=True,
        )
    assert calls == 1

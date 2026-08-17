"""Installed Phase 3 contract and semantic-source integration tests."""
from __future__ import annotations

import copy
from pathlib import Path
from types import SimpleNamespace

from app.services import canonical_source_phase2 as phase2
from app.services import canonical_source_phase3 as phase3
from app.services import build_concepts, generation, uploads


DATA = Path(__file__).parents[1] / "data" / "Testing"


def _source() -> str:
    return (DATA / "RNE.mmd").read_text(encoding="utf-8")


def _canonical(source: str) -> dict:
    return phase2.compile_phase2_source(
        source,
        source_filename="RNE.pdf",
        consumer_module="build_concepts",
    ).canonical


def _metadata() -> dict[str, str]:
    return {
        "board": "CBSE",
        "grade": "10",
        "subject": "History",
        "unit": "Modern World History",
        "chapter_title": "The Rise of Nationalism in Europe",
        "chapter_id": "1759",
        "chapter_code": "10CBSS_TheRiseOfNat",
        "learning_kind": "Post",
    }


def test_contract_is_installed_after_phase2_contracts():
    assert phase2._PHASE3_CONTRACT_VERSION == 2
    assert generation._PHASE3_SEMANTIC_GRAPH_VERSION == 2


def test_installed_inventory_wrapper_attaches_graph_ids(monkeypatch):
    source = _source()
    canonical = _canonical(source)
    graph, _report = phase3.compile_semantic_graph(
        canonical,
        source_text=source,
        metadata=_metadata(),
    )
    monkeypatch.setattr(
        generation,
        "_assign_chapter_wide_inventory_topics_via_api",
        lambda *, inventory, **_kwargs: inventory,
    )

    with phase2.activate(canonical), phase3.activate(graph):
        inventory = generation._extract_question_task_inventory_via_api(
            meta=_metadata(), sections=[], records=[]
        )

    # Never-split: 26 whole parent items, no dotted leaf fragments.
    assert len(inventory["items"]) == 26
    assert inventory["source_contract"]["parent_task_count"] == 26
    assert not any("." in str(item["qid"]) for item in inventory["items"])
    assert all(item.get("_semantic_topic_id") for item in inventory["items"])
    assert all(
        item.get("_semantic_source_task_id") for item in inventory["items"]
    )
    section_two = [
        item for item in inventory["items"]
        if item.get("_semantic_topic_id") == "TOPIC-0002"
    ]
    assert section_two
    assert all(item.get("_semantic_source_task_id") for item in section_two)
    assert all(item.get("_semantic_graph_contract") == graph["source_contract_hash"] for item in section_two)

    by_qid = {item["qid"]: item for item in inventory["items"]}
    assert by_qid["QINV-0011"]["_semantic_source_task_id"] == "TASK-00011"
    assert by_qid["QINV-0016"]["_semantic_source_task_id"] == "TASK-00016"


def test_leaf_routes_override_parent_topic_without_mutating_shared_graph():
    source = _source()
    canonical = _canonical(source)
    graph, _report = phase3.compile_semantic_graph(
        canonical,
        source_text=source,
        metadata=_metadata(),
    )
    graph_before = copy.deepcopy(graph)
    parent = next(
        task for task in graph["tasks"] if task["qid"] == "QINV-0016"
    )
    topic_by_id = {
        topic["topic_id"]: topic for topic in graph["topics"]
    }
    routed_topic_ids = ["TOPIC-0002", "TOPIC-0004"]
    inventory = {
        "items": [
            {
                "qid": f"QINV-0016.{index}",
                "parent_qid": "QINV-0016",
                "_topic_scope": "chapter",
                "_chapter_wide_task": True,
                "topic_hint": topic_by_id[topic_id]["title"],
            }
            for index, topic_id in enumerate(routed_topic_ids, start=1)
        ],
    }

    annotated = phase3.annotate_inventory(inventory, graph)

    assert [
        item["_semantic_topic_id"] for item in annotated["items"]
    ] == routed_topic_ids
    assert {
        item["_semantic_source_task_id"] for item in annotated["items"]
    } == {parent["task_id"]}
    assert graph == graph_before


def test_reusable_type_uses_case_routes_without_becoming_cross_topic_synthesis():
    source = _source()
    canonical = _canonical(source)
    graph, _report = phase3.compile_semantic_graph(
        canonical,
        source_text=source,
        metadata=_metadata(),
    )
    topic_by_id = {
        topic["topic_id"]: topic for topic in graph["topics"]
    }
    mined = {
        "types": [{
            "type_id": "TYPE-SHORT-NOTE",
            "type_title": "Writing a concise historical note",
            "placement_scope": "normal",
            "source_question_ids": ["QINV-0016.1", "QINV-0016.2"],
            "case_prompts": [
                {
                    "case_id": "CASE-MAZZINI",
                    "topic_match_hint": topic_by_id["TOPIC-0002"]["title"],
                    "placement_scope": "normal",
                    "examples": [{
                        "source_question_id": "QINV-0016.1",
                        "example_prompt": "Write a note on Giuseppe Mazzini.",
                    }],
                },
                {
                    "case_id": "CASE-CAVOUR",
                    "topic_match_hint": topic_by_id["TOPIC-0004"]["title"],
                    "placement_scope": "normal",
                    "examples": [{
                        "source_question_id": "QINV-0016.2",
                        "example_prompt": "Write a note on Count Cavour.",
                    }],
                },
            ],
        }],
    }

    annotated = phase3.annotate_mined_types(mined, graph)
    reusable = annotated["types"][0]

    assert reusable["_semantic_topic_ids"] == ["TOPIC-0002", "TOPIC-0004"]
    assert reusable["_semantic_scope"] == "multi_topic_reuse"
    assert reusable["placement_scope"] == "normal"
    assert [
        case["_semantic_topic_id"] for case in reusable["case_prompts"]
    ] == ["TOPIC-0002", "TOPIC-0004"]
    assert all(
        case["placement_scope"] == "normal"
        for case in reusable["case_prompts"]
    )

    units = phase3.annotate_assignment_units(
        generation._expand_mined_types_to_assignment_units(
            annotated["types"]
        ),
        graph,
    )
    assert [unit["_semantic_topic_id"] for unit in units] == [
        "TOPIC-0002", "TOPIC-0004",
    ]
    assert all(unit["placement_scope"] == "normal" for unit in units)


def test_only_one_integrated_case_is_cross_topic_synthesis():
    source = _source()
    canonical = _canonical(source)
    graph, _report = phase3.compile_semantic_graph(
        canonical,
        source_text=source,
        metadata=_metadata(),
    )
    mined = {
        "types": [{
            "type_id": "TYPE-COMPARISON",
            "type_title": "Comparing nation-building pathways",
            "source_question_ids": ["QINV-0003", "QINV-0006"],
            "case_prompts": [{
                "case_id": "CASE-CROSS-TOPIC",
                "placement_scope": "cross_topic_synthesis",
                "examples": [
                    {
                        "source_question_id": "QINV-0003",
                        "example_prompt": "Analyse List's economic argument.",
                    },
                    {
                        "source_question_id": "QINV-0006",
                        "example_prompt": "Explain culture's nationalist role.",
                    },
                ],
            }],
        }],
    }

    annotated = phase3.annotate_mined_types(mined, graph)
    mtype = annotated["types"][0]
    case = mtype["case_prompts"][0]

    assert len(case["_semantic_topic_ids"]) > 1
    assert case["_semantic_scope"] == "cross_topic_synthesis"
    assert case["placement_scope"] == "cross_topic_synthesis"
    assert mtype["_semantic_scope"] == "multi_topic_reuse"
    assert mtype["placement_scope"] == "cross_topic_synthesis"


def test_concepts_wrapper_activates_verified_graph_and_semantic_source(
    tmp_path, monkeypatch
):
    source = _source()
    canonical = _canonical(source)
    graph, _report = phase3.compile_semantic_graph(
        canonical,
        source_text=source,
        metadata=_metadata(),
    )
    semantic = phase3.render_semantic_source(graph, canonical)
    job = SimpleNamespace(
        id=92,
        mmd_text=source,
        filename="RNE.pdf",
        module="build_concepts",
    )
    session = {
        "job_id": job.id,
        "job": job,
        "canonical": canonical,
        "source_path": None,
        "artifact_dir": tmp_path,
        "raw_source": source,
        "graph": None,
    }
    observed: dict[str, object] = {}

    def prepare_generation_graph(**kwargs):
        observed["verify_semantics"] = kwargs["verify_semantics"]
        observed["metadata"] = copy.deepcopy(kwargs["metadata"])
        return graph

    monkeypatch.setattr(phase3, "prepare_generation_graph", prepare_generation_graph)
    monkeypatch.setattr(phase3, "render_semantic_source", lambda *_args: semantic)

    with phase2.activate(canonical), phase3.activate_session(session):
        records = generation.concepts_from_mmd(
            source,
            **_metadata(),
            live=False,
            artifacts={},
        )

    assert records
    assert observed["verify_semantics"] is True
    # The wrapper forwards the Architect's instruction identity into the
    # graph metadata (docs/aegis-restructure.md §8.1); empty when the caller
    # assembled no set, keeping legacy graph identities unchanged.
    assert observed["metadata"] == {
        **_metadata(), "instruction_set_sha256": "",
    }
    assert session["graph"] is graph


def test_metadata_sanitization_preserves_later_paid_checkpoint(
    tmp_path,
    monkeypatch,
):
    source = """<!-- source_origin: gpt-pdf-to-acsd -->
<!-- compiler_version: gpt-pdf-to-acsd-2 -->

# Review Chapter

## Verified Topic

Visible canonical source.
"""
    canonical = phase2.compile_phase2_source(
        source,
        source_filename="review.mmd",
        consumer_module="build_concepts",
    ).canonical
    metadata = {
        "board": "CBSE",
        "grade": "10",
        "subject": "History",
        "unit": "",
        "chapter_title": "Review Chapter",
        "chapter_id": "1759",
        "chapter_code": "",
        "learning_kind": "Post",
    }
    legacy_graph, _report = phase3.compile_semantic_graph(
        canonical,
        source_text=source,
        metadata=metadata,
    )
    legacy_source = phase3.render_semantic_source(
        legacy_graph,
        canonical,
        strip_machine_metadata=False,
    )
    legacy_graph["semantic_source_sha256"] = phase3._sha256_text(
        legacy_source)
    legacy_graph["status"] = "failed"
    legacy_graph["issues"] = [{
        "severity": "error",
        "code": "semantic_source_rich_text",
        "block_ids": ["BLK-00001"],
        "message": "Legacy compiler metadata was read as raw_latex.",
    }]
    migrated, _semantic = phase3._migrate_machine_metadata_semantic_source(
        legacy_graph,
        canonical=canonical,
    )
    assert phase3.machine_metadata_migration_seal_valid(
        migrated,
        canonical=canonical,
    )

    context_hash = "a" * 64
    source_stage = generation._make_concept_checkpoint(
        "source_graph_review",
        records=[],
        source_review_graph=copy.deepcopy(legacy_graph),
        source_review_context_hash=context_hash,
        source_review_decision_id="phase3-source-" + context_hash[:24],
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
    fingerprint = "f" * 64
    target_identity = {
        "board": "CBSE",
        "grade": "10",
        "subject": "History",
        "unit": "",
        "chapter_code": "",
        "chapter_title": "Review Chapter",
    }
    checkpoint = build_concepts._merge_generation_checkpoint_history(
        {},
        source_stage,
        fingerprint=fingerprint,
        target_identity=target_identity,
        target_chapter_id=1759,
    )
    checkpoint = build_concepts._merge_generation_checkpoint_history(
        checkpoint,
        late_stage,
        fingerprint=fingerprint,
        target_identity=target_identity,
        target_chapter_id=1759,
    )
    callbacks: list[dict] = []
    job = SimpleNamespace(
        id=193,
        mmd_text=source,
        filename="review.mmd",
        module="build_concepts",
    )
    session = {
        "job_id": job.id,
        "job": job,
        "canonical": canonical,
        "source_path": None,
        "artifact_dir": tmp_path,
        "raw_source": source,
        "graph": None,
    }
    monkeypatch.setattr(
        phase3,
        "prepare_generation_graph",
        lambda **_kwargs: copy.deepcopy(migrated),
    )

    with phase2.activate(canonical), phase3.activate_session(session):
        records = generation.concepts_from_mmd(
            source,
            **metadata,
            live=False,
            artifacts={},
            resume_checkpoint=checkpoint,
            checkpoint_callback=callbacks.append,
        )

    assert records
    assert len(callbacks) == 1
    assert callbacks[0]["source_review_metadata_sanitization_applied"] is True
    assert not callbacks[0].get("source_review_resolution_applied")
    preserved = build_concepts._merge_generation_checkpoint_history(
        checkpoint,
        callbacks[0],
        fingerprint=fingerprint,
        target_identity=target_identity,
        target_chapter_id=1759,
    )
    assert generation._newest_compatible_concept_checkpoint(
        preserved)["stage"] == "pre_type_assignment"
    assert {
        row["stage"] for row in preserved["checkpoints"]
    } >= {"source_graph_review", "pre_type_assignment"}


def test_phase3_artifact_manifest_and_download_fallthrough(tmp_path, monkeypatch):
    graph = {
        "schema_name": phase3.SCHEMA_NAME,
        "schema_version": phase3.SCHEMA_VERSION,
        "compiler_version": phase3.COMPILER_VERSION,
        "phase": phase3.PHASE,
        "status": "ready",
        "classification_mode": "api_classified_and_verified",
        "source_contract_hash": "source-contract",
        "semantic_context_hash": "semantic-context",
        "semantic_source_sha256": "semantic-source",
        "topics": [],
        "subtopics": [],
        "blocks": [],
        "tasks": [],
        "figures": [],
        "images": [],
        "math": [],
        "issues": [],
    }
    report = {
        "status": "ready",
        "schema_version": phase3.SCHEMA_VERSION,
        "compiler_version": phase3.COMPILER_VERSION,
        "source_contract_hash": "source-contract",
        "semantic_context_hash": "semantic-context",
        "semantic_source_sha256": "semantic-source",
        "classification_mode": "api_classified_and_verified",
        "summary": {"topics": 0, "tasks": 0},
    }
    phase3.write_artifacts(
        tmp_path,
        graph=graph,
        report=report,
        semantic_source="# Semantic source\n",
    )
    manifest = phase3.artifact_manifest(tmp_path)
    assert manifest["available"] is True
    assert {item["kind"] for item in manifest["files"]} >= {
        "semantic_graph", "semantic_graph_report", "semantic_source"
    }

    monkeypatch.setattr(uploads, "source_artifact_directory", lambda _job_id: tmp_path)
    job = SimpleNamespace(id=55)
    path, spec = uploads.source_artifact_download(job, "semantic_graph")
    assert path == tmp_path / phase3.GRAPH_FILENAME
    assert spec["label"] == "Phase 3 semantic source graph"

"""Installed Phase 3 contract and semantic-source integration tests."""
from __future__ import annotations

import copy
from pathlib import Path
from types import SimpleNamespace

from app.services import canonical_source_phase2 as phase2
from app.services import canonical_source_phase3 as phase3
from app.services import generation, uploads


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
    assert phase2._PHASE3_CONTRACT_VERSION == 1
    assert generation._PHASE3_SEMANTIC_GRAPH_VERSION == 1


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

    assert len(inventory["items"]) == 26
    section_two = [
        item for item in inventory["items"]
        if item.get("_semantic_topic_id") == "TOPIC-0002"
    ]
    assert section_two
    assert all(item.get("_semantic_source_task_id") for item in section_two)
    assert all(item.get("_semantic_graph_contract") == graph["source_contract_hash"] for item in section_two)


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
    assert observed["metadata"] == _metadata()
    assert session["graph"] is graph


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

"""Installed Phase 3 contract and artifact integration tests."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from app.services import canonical_source_phase2 as phase2
from app.services import canonical_source_phase3 as phase3
from app.services import canonical_source_phase3_contract as contract
from app.services import generation, uploads


def _source() -> str:
    return Path("data/Testing/RNE.mmd").read_text(encoding="utf-8")


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
    }


def test_contract_is_installed_after_phase2_contracts():
    assert phase2._PHASE3_CONTRACT_VERSION == 1
    assert generation._PHASE3_SEMANTIC_GRAPH_VERSION == 1
    assert uploads._CANONICAL_SOURCE_PHASE3_VERSION == 1


def test_prepare_job_graph_writes_optional_audit_artifacts(tmp_path, monkeypatch):
    source = _source()
    canonical = _canonical(source)
    job = SimpleNamespace(id=91, mmd_text=source, filename="RNE.pdf")
    monkeypatch.setattr(uploads, "source_artifact_directory", lambda _job_id: tmp_path)

    with phase2.activate(canonical):
        graph = contract.prepare_job_graph(job, metadata=_metadata())

    graph_path = tmp_path / contract.OPTIONAL_ARTIFACT_SPECS["semantic_graph"]["filename"]
    report_path = tmp_path / contract.OPTIONAL_ARTIFACT_SPECS["semantic_graph_report"]["filename"]
    assert graph_path.exists()
    assert report_path.exists()
    assert json.loads(graph_path.read_text(encoding="utf-8"))["graph_sha256"] == graph["graph_sha256"]
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "passed"
    assert report["summary"]["topics"] == 6
    assert report["summary"]["tasks"] == 26

    files, manifest_report = contract.optional_artifact_manifest(tmp_path)
    assert {item["kind"] for item in files} == {
        "semantic_graph", "semantic_graph_report"
    }
    assert manifest_report["graph_sha256"] == graph["graph_sha256"]


def test_installed_inventory_wrapper_attaches_graph_ids_without_api(monkeypatch):
    source = _source()
    canonical = _canonical(source)
    graph = phase3.build_semantic_graph(canonical, source, metadata=_metadata())
    monkeypatch.setattr(
        generation,
        "_assign_chapter_wide_inventory_topics_via_api",
        lambda *, inventory, **_kwargs: inventory,
    )

    with phase2.activate(canonical), phase3.activate(graph):
        inventory = generation._extract_question_task_inventory_via_api(
            meta=_metadata(), sections=[], records=[]
        )

    assert inventory["semantic_graph"]["graph_sha256"] == graph["graph_sha256"]
    assert len(inventory["items"]) == 26
    section_two = [
        item for item in inventory["items"]
        if item.get("_semantic_topic_id") == "TOPIC-0002"
    ]
    assert section_two
    assert all(item.get("_semantic_source_block_ids") for item in section_two)


def test_concepts_wrapper_builds_graph_for_active_upload_job(
    tmp_path, monkeypatch
):
    source = _source()
    canonical = _canonical(source)
    job = SimpleNamespace(
        id=92, mmd_text=source, filename="RNE.pdf", module="build_concepts"
    )
    monkeypatch.setattr(
        uploads, "source_artifact_directory", lambda _job_id: tmp_path
    )

    with phase2.activate(canonical), contract.activate_job(job):
        records = generation.concepts_from_mmd(
            source,
            **_metadata(),
            learning_kind="Post",
            live=False,
            artifacts={},
        )

    assert records
    graph_path = (
        tmp_path
        / contract.OPTIONAL_ARTIFACT_SPECS["semantic_graph"]["filename"]
    )
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    assert graph["metadata"]["chapter_id"] == "1759"
    assert graph["subject_adapter"] == "social_science"


def test_optional_artifact_download_falls_through_to_phase3(tmp_path, monkeypatch):
    graph_file = tmp_path / contract.OPTIONAL_ARTIFACT_SPECS["semantic_graph"]["filename"]
    graph_file.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(uploads, "source_artifact_directory", lambda _job_id: tmp_path)
    job = SimpleNamespace(id=55)

    path, spec = uploads.source_artifact_download(job, "semantic_graph")

    assert path == graph_file
    assert spec["label"] == "Aegis Phase 3 semantic source graph"

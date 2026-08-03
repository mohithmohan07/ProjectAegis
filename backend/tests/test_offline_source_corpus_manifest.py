"""Offline source-precondition manifest for checked-in textbook MMD fixtures.

This is deliberately a three-source corpus and does not claim live end-to-end
acceptance. Adding another textbook fixture must update this manifest so
pre-production coverage cannot be overstated.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from app.services import canonical_source_phase2 as phase2
from app.services import canonical_source_phase3 as phase3


DATA = Path(__file__).parents[1] / "data" / "Testing"

CORPUS = {
    "RNE.mmd": {
        "raw_sha256": (
            "f7a1d03c61977077a7d37a08528caac780bc61700778ba53565fb2bb07c20c59"
        ),
        "source_contract_hash": (
            "dab217e3aae76d47c7f0cc23f7740512bfb7f2e084facc6e1b7bb57d4012ddf5"
        ),
        "metadata": {
            "subject": "History",
            "chapter_title": "The Rise of Nationalism in Europe",
        },
        "topics": 6,
        "tasks": 26,
        "blocks": 341,
        "status": "ready",
        "issues": {},
    },
    "jemh105 (1).mmd": {
        "raw_sha256": (
            "0f5b7f6498607a9e54206c17f0eaeceff00033ee0038e6ab48fb91430e4e0349"
        ),
        "source_contract_hash": (
            "bb917fbea5b7d8e096a3c2f6d3ee88d36d20c28bd8b2fee3c8f10b86ab09aef9"
        ),
        "metadata": {
            "subject": "Mathematics",
            "chapter_title": "Arithmetic Progressions",
        },
        "topics": 4,
        "tasks": 65,
        "blocks": 331,
        "status": "ready",
        "issues": {},
    },
    "Class 10 Chapter 5 Electricity.mmd": {
        "raw_sha256": (
            "86e6c6a0c42932d01c51d8fcad82974d4b1c8440ad4d6853b53fe09e0aaacdcc"
        ),
        "source_contract_hash": (
            "8329a81e92acee45d7d49e92cd6fa7702132083c20e7acef9abe28b9337e8030"
        ),
        "metadata": {
            "subject": "Science",
            "chapter_title": "Electricity",
        },
        "topics": 8,
        "tasks": 60,
        "blocks": 375,
        "status": "review_required",
        "issues": {
            "converter_semantic_markup_requires_pdf_reconciliation": [
                "BLK-00061"
            ],
        },
    },
}


def test_manifest_names_every_checked_in_textbook_mmd_and_locks_identity():
    checked_in = {path.name for path in DATA.glob("*.mmd")}

    assert checked_in == set(CORPUS)
    assert len(CORPUS) == 3
    assert len({row["raw_sha256"] for row in CORPUS.values()}) == len(CORPUS)
    for filename, expected in CORPUS.items():
        raw = (DATA / filename).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == expected["raw_sha256"]


@pytest.mark.parametrize("filename", CORPUS)
def test_corpus_rebuilds_exact_grounding_preconditions(filename: str):
    expected = CORPUS[filename]
    source = (DATA / filename).read_text(encoding="utf-8")
    compiled = phase2.compile_phase2_source(
        source,
        source_filename=filename,
        consumer_module="build_concepts",
    )
    graph, _report = phase3.compile_semantic_graph(
        compiled.canonical,
        source_text=source,
        metadata=expected["metadata"],
    )

    assert compiled.canonical["phase2_inventory_ready"] is True
    assert compiled.report["phase2_issues"] == []
    assert graph["source_contract_hash"] == expected["source_contract_hash"]
    assert graph["source_contract_hash"] == phase3.source_contract_hash(
        compiled.canonical
    )
    assert len(graph["topics"]) == expected["topics"]
    assert len(graph["tasks"]) == expected["tasks"]
    assert len(graph["blocks"]) == expected["blocks"]
    assert graph["status"] == expected["status"]

    canonical_block_ids = {
        row["block_id"] for row in compiled.canonical["blocks"]
    }
    graph_block_ids = {row["block_id"] for row in graph["blocks"]}
    assert graph_block_ids == canonical_block_ids

    task_ids = {row["task_id"] for row in graph["tasks"]}
    block_task_ids = {
        task_id
        for row in graph["blocks"]
        for task_id in row.get("task_ids") or []
    }
    assert block_task_ids == task_ids
    assert all(row.get("topic_id") for row in graph["tasks"])

    issues = {
        row["code"]: row.get("block_ids") or []
        for row in graph["issues"]
        if row.get("severity") == "error"
    }
    assert issues == expected["issues"]

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
            "6ee17db639ef680984215487f0e74ee2a335da08c836eae9e5f8489312dc1891"
        ),
        "metadata": {
            "subject": "History",
            "chapter_title": "The Rise of Nationalism in Europe",
        },
        "topics": 6,
        "tasks": 26,
        "blocks": 341,
        "status": "ready",
        "phase2_table_repairs": {},
        "issues": {},
    },
    "jemh105 (1).mmd": {
        "raw_sha256": (
            "0f5b7f6498607a9e54206c17f0eaeceff00033ee0038e6ab48fb91430e4e0349"
        ),
        "source_contract_hash": (
            "75679bc60f7a3f6d38d4ecf715ddb35aea9ee1439bb3fd3cea7930423d293670"
        ),
        "metadata": {
            "subject": "Mathematics",
            "chapter_title": "Arithmetic Progressions",
        },
        "topics": 5,
        "tasks": 65,
        "blocks": 331,
        "status": "ready",
        "phase2_table_repairs": {"QINV-0015": ["katex_row_spacing", "raw_latex"]},
        "issues": {},
    },
    "Class 10 Chapter 5 Electricity.mmd": {
        "raw_sha256": (
            "86e6c6a0c42932d01c51d8fcad82974d4b1c8440ad4d6853b53fe09e0aaacdcc"
        ),
        "source_contract_hash": (
            "86677f1237615fdf03e488e512ee36267f3da559d8d4350b082895eda8c53b6f"
        ),
        "metadata": {
            "subject": "Science",
            "chapter_title": "Electricity",
        },
        "topics": 8,
        "tasks": 60,
        "blocks": 375,
        "status": "failed",
        "phase2_table_repairs": {
            "QINV-0009": ["raw_latex"], "QINV-0049": ["katex_row_spacing"],
        },
        "issues": {
            "converter_semantic_markup_requires_pdf_reconciliation": [
                "BLK-00061"
            ],
            "semantic_source_rich_text": [
                "BLK-00025", "BLK-00027", "BLK-00050", "BLK-00052",
                "BLK-00061", "BLK-00063", "BLK-00082", "BLK-00084",
                "BLK-00110", "BLK-00112", "BLK-00114", "BLK-00116",
                "BLK-00120", "BLK-00200", "BLK-00202", "BLK-00218",
                "BLK-00220", "BLK-00222", "BLK-00224", "BLK-00226",
                "BLK-00228", "BLK-00268", "BLK-00270", "BLK-00333",
                "BLK-00335", "BLK-00339", "BLK-00341", "BLK-00350",
                "BLK-00352",
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

    # Lossless table transport now exposes historic incomplete task-table
    # boundaries instead of accepting their earlier flattened rendering.
    expected_repairs = expected["phase2_table_repairs"]
    assert compiled.canonical["phase2_inventory_ready"] is (not expected_repairs)
    assert all(issue["code"] == "phase2_task_rich_text_invalid" for issue in compiled.report["phase2_issues"])
    assert {issue["qid"]: issue["rich_text_issues"] for issue in compiled.report["phase2_issues"]} == expected_repairs
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

"""Representative real-source acceptance gates for Phase 2 ACSD."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services import canonical_source_phase2 as phase2
from app.services import generation


DATA = Path(__file__).parents[1] / "data" / "Testing"


@pytest.mark.parametrize(
    ("filename", "expected_tasks"),
    [
        ("RNE.mmd", 26),
        ("jemh105 (1).mmd", 65),
        ("Class 10 Chapter 5 Electricity.mmd", 60),
    ],
)
def test_phase2_source_critical_ledgers_are_ready_and_source_ordered(
    filename: str,
    expected_tasks: int,
):
    source = (DATA / filename).read_text(encoding="utf-8")

    compiled = phase2.compile_phase2_source(
        source,
        source_filename=filename,
        consumer_module="build_concepts",
    )

    canonical = compiled.canonical
    tasks = canonical["tasks"]
    assert canonical["phase2_inventory_ready"] is True, (
        filename,
        compiled.report.get("phase2_issues"),
    )
    assert compiled.report["phase2_issues"] == []
    assert len(tasks) == expected_tasks, (
        filename,
        len(tasks),
        [task.get("source_label") for task in tasks],
    )
    assert [task["qid"] for task in tasks] == [
        f"QINV-{index:04d}"
        for index in range(1, expected_tasks + 1)
    ]
    assert [task["source_start"] for task in tasks] == sorted(
        task["source_start"] for task in tasks
    )
    assert len({task["identity_key"] for task in tasks}) == expected_tasks

    inventory = phase2.inventory_from_canonical(canonical)
    assert inventory["source_contract"]["mode"] == (
        phase2.SOURCE_CONTRACT_MODE
    )
    assert not generation._invalid_inventory_items(inventory)

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
def test_phase2_source_critical_ledgers_preserve_order_and_surface_table_repairs(
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
    # These historical parser prompts lost the table environment boundaries
    # upstream. The current formatter retains their cells/layout evidence and
    # exposes repair defects rather than declaring flattened prose ready.
    expected_repairs = {
        "RNE.mmd": {},
        "jemh105 (1).mmd": {"QINV-0015": ["katex_row_spacing", "raw_latex"]},
        "Class 10 Chapter 5 Electricity.mmd": {
            "QINV-0009": ["raw_latex"], "QINV-0049": ["katex_row_spacing"],
        },
    }[filename]
    assert canonical["phase2_inventory_ready"] is (not expected_repairs)
    issues = compiled.report["phase2_issues"]
    assert all(issue["code"] == "phase2_task_rich_text_invalid" for issue in issues)
    assert {issue["qid"]: issue["rich_text_issues"] for issue in issues} == expected_repairs
    for task in tasks:
        if task["qid"] in expected_repairs:
            assert " & " in task["display_prompt"]
            assert r"\\" in task["display_prompt"]
            assert "Table row" not in task["display_prompt"]
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

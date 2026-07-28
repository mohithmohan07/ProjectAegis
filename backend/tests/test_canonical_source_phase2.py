"""Phase 2 ACSD source-critical Build Concepts contract regressions."""
from __future__ import annotations

import copy
from pathlib import Path
from types import SimpleNamespace

from app.services import canonical_source_phase2 as phase2
from app.services import generation


DATA = Path(__file__).parents[1] / "data" / "Testing"


def _source_with_tasks() -> str:
    return r"""# Ordered Chapter

## First Topic

A worked relation is $a^2+b^2=c^2$.

Questions

1. Calculate the missing side when the other two sides are 3 and 4.

## Last Topic

Activity

Observe the final topic and state one conclusion.
"""


def test_phase2_rne_inventory_is_source_ordered_and_byte_deterministic():
    source = (DATA / "RNE.mmd").read_text(encoding="utf-8")

    first = phase2.compile_phase2_source(
        source,
        source_filename="RNE.mmd",
        consumer_module="build_concepts",
    )
    second = phase2.compile_phase2_source(
        source,
        source_filename="RNE.mmd",
        consumer_module="build_concepts",
    )

    assert first.canonical == second.canonical
    assert first.aegis_mmd == second.aegis_mmd
    assert first.report == second.report
    assert first.canonical["used_for_generation"] is True
    assert first.canonical["shadow_mode"] is False
    assert first.canonical["generation_usage"]["mode"] == "source-critical"
    assert first.canonical["source_contract"]["mode"] == (
        phase2.SOURCE_CONTRACT_MODE
    )

    tasks = first.canonical["tasks"]
    assert len(tasks) == 26
    assert [task["qid"] for task in tasks] == [
        f"QINV-{index:04d}" for index in range(1, 27)
    ]
    assert [task["source_start"] for task in tasks] == sorted(
        task["source_start"] for task in tasks
    )
    assert len({task["identity_key"] for task in tasks}) == 26

    inventory = phase2.inventory_from_canonical(first.canonical)
    assert [item["qid"] for item in inventory["items"]] == [
        f"QINV-{index:04d}" for index in range(1, 27)
    ]
    assert inventory["source_contract"]["mode"] == (
        phase2.SOURCE_CONTRACT_MODE
    )
    assert not generation._invalid_inventory_items(inventory)


def test_phase2_inventory_preserves_display_math_and_source_identity():
    compiled = phase2.compile_phase2_source(
        _source_with_tasks(),
        source_filename="ordered.mmd",
        consumer_module="build_concepts",
    )
    inventory = phase2.inventory_from_canonical(compiled.canonical)

    assert compiled.canonical["document"]["source_sha256"] == (
        inventory["source_contract"]["source_sha256"]
    )
    assert any(
        "[Katex]" in block.get("display_text", "")
        for block in compiled.canonical["blocks"]
    )
    assert all(item["_acsd_identity_key"] for item in inventory["items"])
    assert all(
        item["_acsd_display_prompt"] == item["normalized_task"]
        for item in inventory["items"]
    )


def test_active_generation_inventory_bypasses_model_extraction(monkeypatch):
    compiled = phase2.compile_phase2_source(
        _source_with_tasks(),
        source_filename="ordered.mmd",
        consumer_module="build_concepts",
    )

    monkeypatch.setattr(
        generation,
        "_assign_chapter_wide_inventory_topics_via_api",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("no chapter-wide tasks should require an API call")
        ),
    )
    monkeypatch.setattr(
        generation,
        "_openai_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("inventory extraction must not call OpenAI")
        ),
    )

    with phase2.activate(compiled.canonical):
        inventory = generation._extract_question_task_inventory_via_api(
            meta={},
            sections=generation.parse_mmd_sections(_source_with_tasks()),
            records=[],
        )

    assert inventory["source_contract"]["mode"] == (
        phase2.SOURCE_CONTRACT_MODE
    )
    assert [item["qid"] for item in inventory["items"]] == [
        f"QINV-{index:04d}"
        for index in range(1, len(inventory["items"]) + 1)
    ]


def test_phase2_gate_fails_closed_for_unresolved_visual():
    compiled = phase2.compile_phase2_source(
        _source_with_tasks(),
        source_filename="ordered.mmd",
        consumer_module="build_concepts",
    )
    canonical = copy.deepcopy(compiled.canonical)
    canonical["tasks"][0]["requires_visual"] = True
    canonical["tasks"][0]["image_urls"] = []
    canonical["tasks"][0]["figure_refs"] = []
    canonical["tasks"][0]["unresolved_figure_reference_ids"] = ["99"]

    issues = phase2.phase2_inventory_issues(canonical, {"issues": []})

    assert {item["code"] for item in issues} >= {
        "phase2_unresolved_figure_reference",
        "phase2_required_visual_missing",
    }


def test_legacy_inventory_checkpoints_rewind_to_pre_inventory_boundary():
    description = generation._make_concept_checkpoint(
        "description_method_snapshot",
        records=[{"concept_title": "A"}],
    )
    old_inventory = generation._make_concept_checkpoint(
        "question_inventory",
        records=[{"concept_title": "A"}],
        question_task_inventory={"items": [{"qid": "QINV-0001"}]},
    )
    old_types = generation._make_concept_checkpoint(
        generation._CONCEPT_CHECKPOINT_STAGE,
        records=[{"concept_title": "A"}],
        question_task_inventory={"items": [{"qid": "QINV-0001"}]},
        mined_types={"types": []},
    )
    job = SimpleNamespace(
        generation_checkpoint={
            "schema_version": generation._CONCEPT_CHECKPOINT_SCHEMA,
            "checkpoint_format": generation._CONCEPT_CHECKPOINT_FORMAT,
            "fingerprint": "f" * 64,
            "target_identity": {},
            "target_chapter_id": 1,
            "stage": old_types["stage"],
            "stage_order": old_types["stage_order"],
            "stage_schema_version": old_types["stage_schema_version"],
            "stage_label": old_types["stage_label"],
            "saved_at": old_types["saved_at"],
            "progress": old_types["progress"],
            "checkpoints": [description, old_inventory, old_types],
        },
        detail="",
    )
    db = SimpleNamespace(commits=0)
    db.commit = lambda: setattr(db, "commits", db.commits + 1)

    changed = phase2.prune_legacy_inventory_checkpoints(db, job)

    assert changed is True
    assert db.commits == 1
    assert job.generation_checkpoint["stage"] == "description_method_snapshot"
    assert [
        entry["stage"] for entry in job.generation_checkpoint["checkpoints"]
    ] == ["description_method_snapshot"]


def test_phase2_checkpoint_inventory_is_retained():
    inventory = generation._make_concept_checkpoint(
        "question_inventory",
        records=[{"concept_title": "A"}],
        question_task_inventory={
            "items": [{"qid": "QINV-0001"}],
            "source_contract": {"mode": phase2.SOURCE_CONTRACT_MODE},
        },
    )
    job = SimpleNamespace(
        generation_checkpoint={
            "stage": inventory["stage"],
            "stage_order": inventory["stage_order"],
            "stage_schema_version": inventory["stage_schema_version"],
            "stage_label": inventory["stage_label"],
            "saved_at": inventory["saved_at"],
            "progress": inventory["progress"],
            "checkpoints": [inventory],
        },
        detail="",
    )
    db = SimpleNamespace(commit=lambda: (_ for _ in ()).throw(
        AssertionError("valid Phase 2 checkpoint must not be rewritten")
    ))

    assert phase2.prune_legacy_inventory_checkpoints(db, job) is False

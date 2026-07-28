"""Phase 2 ACSD source-critical Build Concepts contract regressions."""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from types import SimpleNamespace

from app.services import canonical_source_phase2 as phase2
from app.services import generation


DATA = Path(__file__).parents[1] / "data" / "Testing"


def _source_with_tasks() -> str:
    return r"""# Ordered Chapter

## First Topic

A worked relation is $a^2+b^2=c^2$.

## Last Topic

The final topic remains last.
"""


def _compiled_with_one_task():
    compiled = phase2.compile_phase2_source(
        _source_with_tasks(),
        source_filename="ordered.mmd",
        consumer_module="build_concepts",
    )
    canonical = copy.deepcopy(compiled.canonical)
    section = canonical["sections"][-1]
    prompt = "Calculate the missing side when the other two sides are 3 and 4."
    identity = hashlib.sha256(
        f"{section['section_id']}\u241f{prompt}".encode("utf-8")
    ).hexdigest()
    canonical["tasks"] = [{
        "task_id": "TASK-00001",
        "qid": "QINV-0001",
        "order": 1,
        "order_index": 1,
        "source_kind": "exercise",
        "source_label": "Question 1",
        "parent_source_label": "Questions",
        "topic_hint": "Last Topic",
        "raw_prompt": prompt,
        "display_prompt": prompt,
        "identity_key": identity,
        "section_id": section["section_id"],
        "source_start": section["source_start"],
        "source_end": section["source_start"] + len(prompt),
        "source_section_index": section["order"] - 1,
        "source_position": 0,
        "source_location_confidence": "exact",
        "chapter_wide": False,
        "activity_origin": False,
        "requires_visual": False,
        "image_urls": [],
        "figure_refs": [],
        "explicit_figure_reference_ids": [],
        "unresolved_figure_reference_ids": [],
        "ambiguous_figure_reference_ids": [],
        "display_overrides": [],
        "canonical_source_mode": phase2.SOURCE_CONTRACT_MODE,
    }]
    canonical["statistics"]["tasks"] = 1
    return compiled, canonical


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
    assert first.canonical["phase2_inventory_ready"] is True
    assert first.report["phase2_issues"] == []
    assert "<!-- schema_version: 1.1.0 -->" in first.aegis_mmd
    assert (
        "<!-- compiler_version: phase-2-source-critical-1 -->"
        in first.aegis_mmd
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
    _compiled, canonical = _compiled_with_one_task()
    inventory = phase2.inventory_from_canonical(canonical)

    assert canonical["document"]["source_sha256"] == (
        inventory["source_contract"]["source_sha256"]
    )
    assert any(
        "[Katex]" in block.get("display_text", "")
        for block in canonical["blocks"]
    )
    assert inventory["items"]
    assert all(item["_acsd_identity_key"] for item in inventory["items"])
    assert all(
        item["_acsd_display_prompt"] == item["normalized_task"]
        for item in inventory["items"]
    )


def test_active_generation_inventory_bypasses_model_extraction(monkeypatch):
    _compiled, canonical = _compiled_with_one_task()

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

    with phase2.activate(canonical):
        inventory = generation._extract_question_task_inventory_via_api(
            meta={},
            sections=generation.parse_mmd_sections(_source_with_tasks()),
            records=[],
        )

    assert inventory["source_contract"]["mode"] == (
        phase2.SOURCE_CONTRACT_MODE
    )
    assert [item["qid"] for item in inventory["items"]] == ["QINV-0001"]


def test_phase2_gate_fails_closed_for_unresolved_visual():
    _compiled, canonical = _compiled_with_one_task()
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


def test_sole_legacy_terminal_checkpoint_is_retained_for_in_place_refresh():
    old_types = generation._make_concept_checkpoint(
        generation._CONCEPT_CHECKPOINT_STAGE,
        records=[{"concept_title": "A"}],
        question_task_inventory={"items": [{"qid": "QINV-0001"}]},
        mined_types={"types": []},
    )
    job = SimpleNamespace(
        generation_checkpoint={
            "stage": old_types["stage"],
            "stage_order": old_types["stage_order"],
            "stage_schema_version": old_types["stage_schema_version"],
            "stage_label": old_types["stage_label"],
            "saved_at": old_types["saved_at"],
            "progress": old_types["progress"],
            "checkpoints": [old_types],
        },
        detail="",
    )
    db = SimpleNamespace(commit=lambda: (_ for _ in ()).throw(
        AssertionError("sole terminal recovery point must not be destroyed")
    ))

    assert phase2.prune_legacy_inventory_checkpoints(db, job) is False


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

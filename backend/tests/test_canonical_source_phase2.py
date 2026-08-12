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
    # Pinned to the constant, not a literal: bumping the compiler is a
    # deliberate act that must invalidate artifacts, not edit a test.
    assert (
        f"<!-- compiler_version: {phase2.COMPILER_VERSION} -->"
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
    expected_inventory_qids = [
        *[f"QINV-{index:04d}" for index in range(1, 11)],
        "QINV-0011.1",
        "QINV-0011.2",
        *[f"QINV-{index:04d}" for index in range(12, 16)],
        *[f"QINV-0016.{index}" for index in range(1, 6)],
        *[f"QINV-{index:04d}" for index in range(17, 27)],
    ]
    assert [item["qid"] for item in inventory["items"]] == expected_inventory_qids
    assert inventory["source_contract"]["task_count"] == 26
    assert inventory["source_contract"]["parent_task_count"] == 26
    assert inventory["source_contract"]["inventory_item_count"] == 31
    assert inventory["source_contract"]["mode"] == (
        phase2.SOURCE_CONTRACT_MODE
    )
    assert not generation._invalid_inventory_items(inventory)


def test_rne_independent_subparts_are_leaf_cases_but_dependent_parts_stay_atomic():
    source = (DATA / "RNE.mmd").read_text(encoding="utf-8")
    compiled = phase2.compile_phase2_source(
        source,
        source_filename="RNE.mmd",
        consumer_module="build_concepts",
    )
    canonical = compiled.canonical
    parents = {task["qid"]: task for task in canonical["tasks"]}

    note_parent = parents["QINV-0016"]
    note_leaves = note_parent["leaf_cases"]
    assert [leaf["qid"] for leaf in note_leaves] == [
        f"QINV-0016.{index}" for index in range(1, 6)
    ]
    assert {leaf["parent_qid"] for leaf in note_leaves} == {"QINV-0016"}
    assert [leaf["subpart_label"] for leaf in note_leaves] == [
        "a)", "b)", "c)", "d)", "e)",
    ]
    assert len({leaf["identity_key"] for leaf in note_leaves}) == 5
    assert [leaf["raw_prompt"] for leaf in note_leaves] == [
        "a) Guiseppe Mazzini",
        "b) Count Camillo de Cavour",
        "c) The Greek war of independence",
        "d) Frankfurt parliament",
        "e) The role of women in nationalist struggles",
    ]

    # These two role perspectives depend on one shared counterfactual and one
    # shared Germania banner. They must remain one canonical answer unit.
    dependent = parents["QINV-0015"]
    assert "(a) as a man" in dependent["raw_prompt"]
    assert "(b) as a woman" in dependent["raw_prompt"]
    assert not dependent.get("leaf_cases")


def test_never_split_inventory_keeps_multi_part_questions_whole(monkeypatch):
    """Reviewer rule under the rewrite: sub-questions stay with their
    question. The ledger keeps its leaf cases, but the inventory
    materializes the parent task as ONE item carrying the full wording."""
    source = (DATA / "RNE.mmd").read_text(encoding="utf-8")
    compiled = phase2.compile_phase2_source(
        source,
        source_filename="RNE.mmd",
        consumer_module="build_concepts",
    )
    canonical = compiled.canonical

    monkeypatch.setenv("AEGIS_PHASE3_REWRITE", "1")
    inventory = phase2.inventory_from_canonical(canonical)
    by_qid = {
        str(item.get("qid")): item for item in inventory["items"]
    }
    assert "QINV-0016" in by_qid
    assert not any("." in qid for qid in by_qid)
    whole = by_qid["QINV-0016"]
    for part in (
        "a) Guiseppe Mazzini",
        "e) The role of women in nationalist struggles",
    ):
        assert part in whole["raw_task"]

    monkeypatch.delenv("AEGIS_PHASE3_REWRITE", raising=False)
    legacy = phase2.inventory_from_canonical(canonical)
    legacy_qids = {str(item.get("qid")) for item in legacy["items"]}
    assert "QINV-0016.1" in legacy_qids and "QINV-0016" not in legacy_qids


def test_rne_reference_taxonomy_is_11_reusable_types_and_31_routable_cases():
    """Lock the intended method taxonomy without pinning Cases to one host.

    The same reusable method may occur under unrelated chapter concepts.  In
    particular all five short-note leaves share one Type identity, while each
    leaf retains an independent route for its own historical subject.
    """
    source = (DATA / "RNE.mmd").read_text(encoding="utf-8")
    compiled = phase2.compile_phase2_source(
        source,
        source_filename="RNE.mmd",
        consumer_module="build_concepts",
    )
    inventory = phase2.inventory_from_canonical(compiled.canonical)
    inventory_qids = [item["qid"] for item in inventory["items"]]

    reusable_groups = [
        # Visual-source interpretation.
        ["QINV-0001", "QINV-0005", "QINV-0010", "QINV-0012", "QINV-0013", "QINV-0014"],
        # Written-source argument/viewpoint interpretation.
        ["QINV-0002", "QINV-0003", "QINV-0007", "QINV-0009"],
        # Historical map interpretation, including both Fig. 14 leaves.
        ["QINV-0004", "QINV-0011.1", "QINV-0011.2"],
        # Historical role/perspective writing.
        ["QINV-0008", "QINV-0015"],
        # Cultural or symbolic contribution/significance.
        ["QINV-0006", "QINV-0018", "QINV-0022"],
        # Measures/reforms, purpose and effects.
        ["QINV-0017", "QINV-0020"],
        # Concise note on a person, event, institution or social role.
        [f"QINV-0016.{index}" for index in range(1, 6)],
        # Nation-building pathways.
        ["QINV-0019", "QINV-0023", "QINV-0024"],
        # Multidimensional movement/ideology explanation.
        ["QINV-0021"],
        # Interacting causes of nationalist conflict.
        ["QINV-0025"],
        # Comparative evidence project.
        ["QINV-0026"],
    ]
    assert len(reusable_groups) == 11
    assert sorted(qid for group in reusable_groups for qid in group) == sorted(
        inventory_qids
    )

    short_note_routes = {
        "QINV-0016.1": ("The Revolutionaries", "Giuseppe Mazzini"),
        "QINV-0016.2": ("Italy Unified", "Count Camillo de Cavour"),
        "QINV-0016.3": ("The Age of Revolutions: 1830–1848", "Greek independence"),
        "QINV-0016.4": ("The Revolution of the Liberals", "Frankfurt Parliament"),
        "QINV-0016.5": ("The Revolution of the Liberals", "Women in nationalist struggles"),
    }
    types = []
    for type_index, group in enumerate(reusable_groups, start=1):
        cases = []
        for case_index, qid in enumerate(group, start=1):
            topic, concept = short_note_routes.get(
                qid,
                (f"Verified route for {qid}", f"Verified concept for {qid}"),
            )
            cases.append({
                "case_id": f"CASE-{type_index:02d}-{case_index:02d}",
                "case_title": concept,
                "topic_match_hint": topic,
                "concept_match_hint": concept,
                "is_activity": qid == "QINV-0026",
                "examples": [{
                    "source_question_id": qid,
                    "example_prompt": next(
                        generation._inventory_task_text(item)
                        for item in inventory["items"]
                        if item["qid"] == qid
                    ),
                }],
            })
        types.append({
            "type_id": f"TYPE-{type_index:04d}",
            "type_title": f"Reusable method {type_index}",
            "type_description": f"Reference reusable method {type_index}.",
            "task_pattern": f"Apply reusable method {type_index}.",
            "source_question_ids": list(group),
            "case_prompts": cases,
        })

    units = generation._expand_mined_types_to_assignment_units(types)
    assert len(units) == 31
    assert len({unit["_origin_type_id"] for unit in units}) == 11
    assert {
        qid
        for unit in units
        for qid in unit["source_question_ids"]
    } == set(inventory_qids)
    assert any(unit["source_question_ids"] == ["QINV-0011.2"] for unit in units)

    short_note_units = [
        unit for unit in units
        if unit["source_question_ids"][0].startswith("QINV-0016.")
    ]
    assert len(short_note_units) == 5
    assert {unit["_origin_type_id"] for unit in short_note_units} == {"TYPE-0007"}
    assert len({unit["type_id"] for unit in short_note_units}) == 5
    assert {
        unit["topic_match_hint"] for unit in short_note_units
    } == {topic for topic, _concept in short_note_routes.values()}


def test_rne_second_fig14_activity_prompt_is_a_visual_leaf_of_qinv_0011():
    source = (DATA / "RNE.mmd").read_text(encoding="utf-8")
    compiled = phase2.compile_phase2_source(
        source,
        source_filename="RNE.mmd",
        consumer_module="build_concepts",
    )
    parent = next(
        task for task in compiled.canonical["tasks"]
        if task["qid"] == "QINV-0011"
    )
    assert len(parent["leaf_cases"]) == 2
    second = parent["leaf_cases"][1]
    assert second["qid"] == "QINV-0011.2"
    assert second["parent_qid"] == "QINV-0011"
    assert second["source_block_id"] == "BLK-00260"
    assert second["explicit_figure_reference_ids"] == ["14(b)"]
    assert second["figure_refs"] == ["FIG-00016"]
    assert second["image_urls"] == [
        "https://cdn.mathpix.com/cropped/6607f4a6-cb7c-4963-a6ea-e5e36dc69d32-19.jpg?height=837&width=744&top_left_y=1273&top_left_x=1149"
    ]

    inventory = phase2.inventory_from_canonical(compiled.canonical)
    item = next(row for row in inventory["items"] if row["qid"] == second["qid"])
    assert item["parent_qid"] == "QINV-0011"
    assert "Examine Fig. 14(b)." in generation._inventory_task_text(item)
    assert second["image_urls"][0] in generation._inventory_task_text(item)


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

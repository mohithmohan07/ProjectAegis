"""Regression coverage for the Phase 2.1 source-hardening contract."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from app.services import canonical_source
from app.services import canonical_source_phase2 as phase2
from app.services import canonical_source_phase21 as phase21
from app.services import canonical_source_phase21_render as phase21_render
from app.services import canonical_source_phase21_structure as phase21_structure
from app.services import generation

DATA = Path(__file__).parents[1] / "data" / "Testing"


def _rne_source() -> str:
    return (DATA / "RNE.mmd").read_text(encoding="utf-8")


def _compile(source: str):
    return phase2.compile_phase2_source(
        source,
        source_filename="RNE.mmd",
        consumer_module="build_concepts",
    )


def _task(canonical: dict, needle: str) -> dict:
    matches = [
        item for item in canonical["tasks"]
        if needle.casefold() in str(item.get("raw_prompt") or "").casefold()
    ]
    assert len(matches) == 1, (needle, len(matches))
    return matches[0]


def test_complete_rne_is_phase21_ready_with_six_sections_and_26_tasks():
    compiled = _compile(_rne_source())
    canonical = compiled.canonical

    assert canonical["phase21_hardening"]["version"] == "2.1.3"
    assert canonical["phase21_hardening"]["compiler"] == (
        "phase-2.1-source-leaf-inventory-2"
    )
    assert canonical["phase21_issues"] == []
    assert compiled.report["phase2_issues"] == []
    assert canonical["phase2_inventory_ready"] is True
    assert len(canonical["tasks"]) == 26
    assert [task["qid"] for task in canonical["tasks"]] == [
        f"QINV-{index:04d}" for index in range(1, 27)
    ]
    main_numbers, _subsections = (
        phase21_structure.numbered_heading_inventory(canonical)
    )
    assert sorted(main_numbers) == [1, 2, 3, 4, 5, 6]
    assert "AEGIS CANONICAL SOURCE PHASE 2.1" in compiled.aegis_mmd


def test_only_certified_note_lists_split_and_each_leaf_inherits_context_and_visual():
    canonical = {
        "tasks": [{
            "task_id": "TASK-00001",
            "qid": "QINV-0001",
            "identity_key": "parent-identity",
            "raw_prompt": (
                "Write short notes on Fig. 7: "
                "a) Giuseppe Mazzini b) Count Camillo de Cavour"
            ),
            "display_prompt": (
                "Write short notes on Fig. 7: "
                "a) Giuseppe Mazzini b) Count Camillo de Cavour"
            ),
            "shared_context": "Use the supplied biographical source excerpt.",
            "source_start": 10,
            "source_end": 100,
        }],
        "figures": [{
            "figure_id": "FIG-00001",
            "reference_ids": ["7"],
            "image_urls": ["https://example.test/fig-7.png"],
        }],
    }

    assert phase21_structure.materialize_task_leaf_cases(canonical) == 2
    leaves = canonical["tasks"][0]["leaf_cases"]
    assert [leaf["qid"] for leaf in leaves] == ["QINV-0001.1", "QINV-0001.2"]
    assert all(
        "Use the supplied biographical source excerpt." in leaf["shared_context"]
        for leaf in leaves
    )
    assert all("Write short notes on Fig. 7:" in leaf["shared_context"] for leaf in leaves)
    assert all(leaf["figure_refs"] == ["FIG-00001"] for leaf in leaves)
    assert all(leaf["explicit_figure_reference_ids"] == ["7"] for leaf in leaves)

    dependent = {
        "tasks": [{
            "task_id": "TASK-00002",
            "qid": "QINV-0002",
            "identity_key": "dependent-identity",
            "raw_prompt": (
                "Answer the following: a) identify the claim "
                "b) justify that same claim from the passage"
            ),
            "source_start": 0,
            "source_end": 80,
        }],
        "figures": [],
    }
    assert phase21_structure.materialize_task_leaf_cases(dependent) == 1
    assert not dependent["tasks"][0].get("leaf_cases")


def test_folded_distinct_visual_prompts_are_two_cases_with_all_questions():
    source = _rne_source()
    first = (
        "Look at Fig. 14(a). Do you think that the people living in any of "
        "these regions thought of themselves as Italians?"
    )
    second = (
        "Examine Fig. 14(b). Which was the first region to become a part of "
        "unified Italy? Which was the last region to join? In which year did "
        "the largest number of states join?"
    )
    assert f"{first}\n\n{second}" in source

    for separator in ("\n", " "):
        compiled = _compile(
            source.replace(f"{first}\n\n{second}", f"{first}{separator}{second}", 1)
        )
        inventory = phase2.inventory_from_canonical(compiled.canonical)
        parent = next(
            task for task in compiled.canonical["tasks"]
            if task["qid"] == "QINV-0011"
        )

        assert compiled.canonical["phase2_inventory_ready"] is True
        assert len(inventory["items"]) == 31
        assert [leaf["qid"] for leaf in parent["leaf_cases"]] == [
            "QINV-0011.1", "QINV-0011.2",
        ]
        assert parent["leaf_cases"][0]["raw_prompt"] == first
        assert parent["leaf_cases"][1]["raw_prompt"] == second
        assert parent["leaf_cases"][0]["figure_refs"] == ["FIG-00015"]
        assert parent["leaf_cases"][1]["figure_refs"] == ["FIG-00016"]
        assert parent["leaf_cases"][1]["raw_prompt"].count("?") == 3
        assert all(
            leaf["decomposition"] == "phase21_distinct_visual_task_clusters"
            for leaf in parent["leaf_cases"]
        )


def test_visual_cluster_guard_does_not_split_same_figure_or_comparative_tasks():
    standard_figures = [
        {
            "figure_id": "FIG-A",
            "reference_ids": ["14(a)"],
            "image_urls": ["https://example.test/14-a.png"],
        },
        {
            "figure_id": "FIG-B",
            "reference_ids": ["14(b)", "14(a)"],
            "image_urls": ["https://example.test/14-b.png"],
        },
    ]

    def materialized(prompt: str, figures: list[dict] | None = None) -> dict:
        canonical = {
            "tasks": [{
                "task_id": "TASK-00001",
                "qid": "QINV-0001",
                "identity_key": "visual-parent",
                "raw_prompt": prompt,
                "display_prompt": prompt,
                "source_start": 0,
                "source_end": len(prompt),
            }],
            "figures": figures or standard_figures,
        }
        phase21_structure.materialize_task_leaf_cases(canonical)
        return canonical["tasks"][0]

    same_figure = materialized(
        "Look at Fig. 14(a). Identify the region. "
        "Examine Fig. 14(a). Explain that same region."
    )
    comparative = materialized(
        "Look at Fig. 14(a). Identify the regions. "
        "Examine Fig. 14(b). Compare it with Fig. 14(a)."
    )
    generic = materialized(
        "Look at Fig. 14(a) and Fig. 14(b). Compare both maps."
    )
    narrative = materialized(
        "The source first asks us to examine Fig. 14(a). It then describes "
        "why historians examine Fig. 14(b) for chronology."
    )
    same_figure_aliases = materialized(
        "Look at Fig. 14(a). Identify the region. "
        "Examine Fig. 15(a). Explain the year.",
        figures=[{
            "figure_id": "FIG-ALIASED",
            "reference_ids": ["14(a)", "15(a)"],
            "image_urls": ["https://example.test/aliased.png"],
        }],
    )
    ambiguous = materialized(
        "Look at Fig. 14(a). Identify the region. "
        "Examine Fig. 14(b). Explain the year.",
        figures=[
            *standard_figures,
            {
                "figure_id": "FIG-B-DUPLICATE",
                "reference_ids": ["14(b)"],
                "image_urls": ["https://example.test/14-b-duplicate.png"],
            },
        ],
    )

    assert not same_figure.get("leaf_cases")
    assert not comparative.get("leaf_cases")
    assert not generic.get("leaf_cases")
    assert not narrative.get("leaf_cases")
    assert not same_figure_aliases.get("leaf_cases")
    assert not ambiguous.get("leaf_cases")


def test_phase21_loader_rebuilds_a_self_consistent_but_incomplete_leaf_artifact(
    tmp_path: Path,
    monkeypatch,
):
    source = _rne_source()
    first = (
        "Look at Fig. 14(a). Do you think that the people living in any of "
        "these regions thought of themselves as Italians?"
    )
    second = (
        "Examine Fig. 14(b). Which was the first region to become a part of "
        "unified Italy? Which was the last region to join? In which year did "
        "the largest number of states join?"
    )
    folded = source.replace(f"{first}\n\n{second}", f"{first} {second}", 1)
    artifact_dir = tmp_path / "artifacts"
    phase2.write_phase2_artifacts(
        artifact_dir,
        mmd_text=folded,
        source_filename="RNE.mmd",
        consumer_module="build_concepts",
    )
    canonical_path = (
        artifact_dir / canonical_source.ARTIFACT_SPECS["canonical_json"]["filename"]
    )
    report_path = artifact_dir / canonical_source.ARTIFACT_SPECS["report"]["filename"]
    canonical = json.loads(canonical_path.read_text(encoding="utf-8"))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    parent = next(
        task for task in canonical["tasks"] if task["qid"] == "QINV-0011"
    )
    parent["leaf_cases"] = parent["leaf_cases"][:1]
    parent["inventory_leaf_count"] = 1
    for container in (
        canonical["phase21_hardening"], canonical["source_contract"],
    ):
        container["parent_task_count"] = 26
        container["decomposed_parent_task_count"] = 2
        container["inventory_item_count"] = 30
    canonical["statistics"]["parent_tasks"] = 26
    canonical["statistics"]["decomposed_parent_tasks"] = 2
    canonical["statistics"]["inventory_leaf_tasks"] = 30
    report["phase21_hardening"] = dict(canonical["phase21_hardening"])
    report["summary"]["inventory_items"] = 30
    canonical_path.write_text(json.dumps(canonical), encoding="utf-8")
    report_path.write_text(json.dumps(report), encoding="utf-8")

    from app.services import uploads

    monkeypatch.setattr(
        uploads, "source_artifact_directory", lambda _job_id: artifact_dir
    )
    loaded, loaded_report = phase2._load_or_refresh_for_job(SimpleNamespace(
        id=312,
        filename="RNE.mmd",
        mmd_text=folded,
    ))

    assert phase21.hardening_artifact_valid(loaded, loaded_report)
    assert len(phase2.inventory_from_canonical(loaded)["items"]) == 31
    loaded_parent = next(
        task for task in loaded["tasks"] if task["qid"] == "QINV-0011"
    )
    assert [leaf["qid"] for leaf in loaded_parent["leaf_cases"]] == [
        "QINV-0011.1", "QINV-0011.2",
    ]


def test_all_inventory_bearing_checkpoint_stages_reject_previous_versions():
    expected_versions = {
        "question_inventory": 3,
        generation._TYPE_TAXONOMY_CHECKPOINT_STAGE: 3,
        generation._CONCEPT_CHECKPOINT_STAGE: 3,
        "post_type_assignment": 5,
        "final_content_ready": 6,
    }

    assert {
        stage: generation._CONCEPT_CHECKPOINT_STAGES[stage]["version"]
        for stage in expected_versions
    } == expected_versions
    for stage, current_version in expected_versions.items():
        stale = {
            "schema_version": generation._CONCEPT_CHECKPOINT_SCHEMA,
            "stage_schema_version": current_version - 1,
            "stage": stage,
        }
        assert not generation._compatible_concept_checkpoint_entry(stale)


def test_plain_text_discuss_cue_recovers_renan_without_reordering():
    source = _rne_source()
    old = (
        "\\section*{Discuss}\n\n"
        "Summarise the attributes of a nation, as Renan understands them. "
        "Why, in his view, are nations important?"
    )
    new = (
        "Discuss\n"
        "Summarise the attributes of a nation, as Renan understands them. "
        "Why, in his view, are nations important?"
    )
    assert old in source

    compiled = _compile(source.replace(old, new, 1))

    assert compiled.canonical["phase2_inventory_ready"] is True
    assert len(compiled.canonical["tasks"]) == 26
    renan = _task(compiled.canonical, "Summarise the attributes of a nation")
    assert renan["source_location_confidence"] == "phase21_plain_task_cue"
    assert renan["raw_prompt"].startswith("Summarise the attributes")


def test_mathpix_missing_section_and_club_question_fail_before_generation():
    source = _rne_source()
    section_two = "\\section*{2 The Making of Nationalism in Europe}\n\n"
    club = (
        "\\section*{Discuss}\n\n"
        "What is the caricaturist trying to depict?\n\n"
    )
    assert section_two in source and club in source
    corrupted = source.replace(section_two, "", 1).replace(club, "", 1)

    compiled = _compile(corrupted)
    codes = {item["code"] for item in compiled.report["phase2_issues"]}

    assert compiled.canonical["phase2_inventory_ready"] is False
    assert len(compiled.canonical["tasks"]) == 25
    assert {
        "phase21_missing_numbered_parent_section",
        "phase21_numbered_section_gap",
        "phase21_orphan_task_figure",
    }.issubset(codes)
    map_task = _task(compiled.canonical, "Plot on a map of Europe")
    assert map_task["display_figure_refs"] == []
    assert map_task["display_image_urls"] == []
    assert not any(
        "What is the caricaturist" in task["raw_prompt"]
        for task in compiled.canonical["tasks"]
    )


def test_rne_task_boundaries_drop_glossary_and_narrative_tails():
    compiled = _compile(_rne_source())

    list_task = _task(compiled.canonical, "political ends that List hopes")
    assert list_task["raw_prompt"] == (
        "Describe the political ends that List hopes to achieve through "
        "economic measures."
    )
    assert "New words" not in list_task["raw_prompt"]
    women = _task(compiled.canonical, "positions on the question of women's rights")
    assert "New words" not in women["raw_prompt"]
    assert "Ideology -" not in women["display_prompt"]


def test_visual_repairs_are_person_surname_only_and_context_is_linked():
    canonical = _compile(_rne_source()).canonical

    hubner = _task(canonical, "Hübner be referring")
    assert hubner["raw_figure_reference_ids"] == ["17"]
    assert hubner["display_figure_reference_ids"] == ["18"]
    assert len(hubner["raw_image_urls"]) == 1
    assert len(hubner["display_image_urls"]) == 1
    assert hubner["raw_image_urls"] != hubner["display_image_urls"]
    assert "Fig. 18" in hubner["display_prompt"]

    frankfurt = _task(canonical, "citizen of Frankfurt in March 1848")
    assert frankfurt["raw_figure_reference_ids"] == ["10"]
    assert frankfurt["display_figure_reference_ids"] == ["10"]
    assert "Fig. 10" in frankfurt["display_prompt"]
    assert "Fig. 19" not in frankfurt["display_prompt"]
    assert "Frankfurt parliament" in frankfurt["display_prompt"]

    club = _task(canonical, "caricaturist trying to depict")
    assert club["display_figure_reference_ids"] == []
    assert len(club["display_figure_refs"]) == 1
    assert "Fig. 6" in club["display_prompt"]

    box = _task(canonical, "chart in Box 3")
    assert box["requires_context"] is True
    assert box["shared_context_block_ids"]
    assert "Broken chains | Being freed" in box["shared_context"]


def test_phase21_expands_one_reusable_type_into_case_assignment_units():
    canonical = {
        "phase21_hardening": {"version": phase21.HARDENING_VERSION},
        "tasks": [
            {"qid": "QINV-0001", "topic_hint": "Visualising the Nation"},
            {"qid": "QINV-0002", "topic_hint": "Visualising the Nation"},
        ],
    }
    mined = [{
        "type_id": "TYPE-0001",
        "type_title": "Interpreting National Allegories",
        "topic_match_hint": "Visualising the Nation",
        "source_question_ids": ["QINV-0001", "QINV-0002"],
        "case_prompts": [
            {
                "case_id": "CASE-0001",
                "examples": [{"source_question_id": "QINV-0001"}],
            },
            {
                "case_id": "CASE-0002",
                "examples": [{"source_question_id": "QINV-0002"}],
            },
        ],
    }]

    with phase2.activate(canonical):
        units = generation._expand_mined_types_to_assignment_units(mined)

    assert len(units) == 2
    assert [unit["_origin_type_id"] for unit in units] == [
        "TYPE-0001", "TYPE-0001",
    ]
    assert [unit["source_question_ids"] for unit in units] == [
        ["QINV-0001"], ["QINV-0002"],
    ]
    assert all(len(unit["case_prompts"]) == 1 for unit in units)


def test_taxonomy_restore_replaces_question_fragment_titles_losslessly():
    first = "Interpret the symbolism in the first national allegory."
    second = "Explain the historical event represented in the second allegory."
    inventory = {"items": [
        {
            "qid": "QINV-0001",
            "source_kind": "checkpoint_question",
            "raw_task": first,
            "normalized_task": first,
        },
        {
            "qid": "QINV-0002",
            "source_kind": "checkpoint_question",
            "raw_task": second,
            "normalized_task": second,
        },
    ]}
    body = (
        "Type 01: Completing the First National Allegory "
        f"Case 01: Source question Example 01: {first} "
        "Type 02: Explaining What Historical Event "
        f"Case 01: Source question Example 01: {second}"
    )
    records = [{
        "topic": "Visualising the Nation",
        "parent_concept": "National Allegories",
        "concept_title": "Germania as National Allegory",
        "concept_details": generation._inject_types(
            "Description: Germania personifies the German nation.", body
        ),
        "keywords": "Germania",
    }]
    mined = {"types": [{
        "type_id": "TYPE-0001",
        "type_title": "Interpreting National Allegories",
        "type_description": (
            "Relate visual symbols to the historical context represented"
        ),
        "topic_match_hint": "Visualising the Nation",
        "source_question_ids": ["QINV-0001", "QINV-0002"],
        "case_prompts": [
            {
                "case_id": "CASE-0001",
                "case_title": "Interpreting symbolic attributes",
                "examples": [{
                    "source_question_id": "QINV-0001",
                    "example_prompt": first,
                }],
            },
            {
                "case_id": "CASE-0002",
                "case_title": "Connecting allegory to historical events",
                "examples": [{
                    "source_question_id": "QINV-0002",
                    "example_prompt": second,
                }],
            },
        ],
    }]}

    restored = phase21_render.restore_mined_type_taxonomy(
        generation, records, inventory, mined
    )
    details = restored[0]["concept_details"]

    assert details.count("Interpreting National Allegories") == 1
    assert "Completing the First National Allegory" not in details
    assert "Explaining What Historical Event" not in details
    assert first in details and second in details


def test_final_normalization_preserves_global_type_and_case_numbers_across_hosts():
    first = "Write a short note on Giuseppe Mazzini."
    second = "Write a short note on Count Camillo de Cavour."
    inventory = {"items": [
        {
            "qid": "QINV-0016.1",
            "source_kind": "short_answer",
            "raw_task": first,
            "normalized_task": first,
        },
        {
            "qid": "QINV-0016.2",
            "source_kind": "short_answer",
            "raw_task": second,
            "normalized_task": second,
        },
    ]}
    mined = {"types": [{
        "type_id": "TYPE-SHORT-NOTE",
        "type_title": "Writing a concise note on a historical figure",
        "type_description": "Summarise a figure's role and significance.",
        "source_question_ids": ["QINV-0016.1", "QINV-0016.2"],
        "case_prompts": [
            {
                "case_id": "CASE-MAZZINI",
                "case_title": "Revolutionary organiser",
                "topic_match_hint": "The Revolutionaries",
                "examples": [{
                    "source_question_id": "QINV-0016.1",
                    "example_prompt": first,
                }],
            },
            {
                "case_id": "CASE-CAVOUR",
                "case_title": "Diplomatic architect of unification",
                "topic_match_hint": "Italy Unified",
                "examples": [{
                    "source_question_id": "QINV-0016.2",
                    "example_prompt": second,
                }],
            },
        ],
    }]}
    records = [
        {
            "topic": "The Revolutionaries",
            "parent_concept": "Revolutionary nationalism",
            "concept_title": "Giuseppe Mazzini and Young Italy",
            "concept_details": generation._inject_types(
                "Description: Mazzini organised revolutionary nationalism.",
                "Type 01: Writing a concise note Case 01: Mazzini "
                f"Example 01: {first}",
            ),
            "keywords": "Mazzini",
        },
        {
            "topic": "Italy Unified",
            "parent_concept": "Italian unification",
            "concept_title": "Cavour and Piedmont-Sardinia",
            "concept_details": generation._inject_types(
                "Description: Cavour used diplomacy to advance unification.",
                "Type 02: Writing a concise note Case 02: Cavour "
                f"Example 01: {second}",
            ),
            "keywords": "Cavour",
        },
    ]

    normalized = phase21_render.normalize_final_records(
        generation, records, inventory, mined
    )

    assert "Type 01:" in normalized[0]["concept_details"]
    assert "Case 01: Revolutionary organiser" in normalized[0][
        "concept_details"
    ]
    assert "Type 01:" in normalized[1]["concept_details"]
    assert "Case 02: Diplomatic architect" in normalized[1][
        "concept_details"
    ]
    assert "Type 02:" not in normalized[1]["concept_details"]
    assert all("_origin_type_id" not in record for record in normalized)


def test_activity_hub_cleanup_removes_repeated_labels():
    assert phase21_render.clean_activity_hub_content(
        "Activity — Activity — Describe the source visual."
    ) == "Activity — Describe the source visual."
    assert phase21_render.clean_activity_hub_content(
        "Project — Project — Project Collect examples."
    ) == "Project — Collect examples."

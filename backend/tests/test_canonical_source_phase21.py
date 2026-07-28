"""Regression coverage for the Phase 2.1 source-hardening contract."""
from __future__ import annotations

from pathlib import Path

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

    assert canonical["phase21_hardening"]["version"] == "2.1.0"
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


def test_phase21_keeps_one_consolidated_type_as_one_assignment_unit():
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

    assert len(units) == 1
    assert units[0]["type_id"] == "TYPE-0001"
    assert len(units[0]["case_prompts"]) == 2
    assert units[0]["source_question_ids"] == ["QINV-0001", "QINV-0002"]


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


def test_activity_hub_cleanup_removes_repeated_labels():
    assert phase21_render.clean_activity_hub_content(
        "Activity — Activity — Describe the source visual."
    ) == "Activity — Describe the source visual."
    assert phase21_render.clean_activity_hub_content(
        "Project — Project — Project Collect examples."
    ) == "Project — Collect examples."

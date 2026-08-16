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


def test_no_deterministic_enumeration_split_ever_a_task_left_whole_stays_whole():
    """§3 purge, item 4D: the enumeration-stem splitter is gone. Lettered
    note lists that the model never partitioned stay ONE atomic task — the
    wording is not lost, it lives whole in the parent prompt."""
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

    assert phase21_structure.materialize_task_leaf_cases(canonical) == 1
    parent = canonical["tasks"][0]
    assert not parent.get("leaf_cases")
    assert "a) Giuseppe Mazzini" in parent["raw_prompt"]
    assert "b) Count Camillo de Cavour" in parent["raw_prompt"]

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


def test_folded_distinct_visual_prompts_ship_whole_with_every_question_and_visual():
    """§3 purge, item 4D: the deterministic visual-cluster splitter is gone.

    When Mathpix folds the two Fig. 14 prompts into ONE source block, no
    model verdict partitioned that block, so it stays ONE whole inventory
    item — and the whole item must carry BOTH prompts' wording and BOTH
    map visuals (never lose a learner question)."""
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
        assert len(inventory["items"]) == 26
        # No deterministic split: the model never partitioned the folded
        # block, so it has no leaf cases at all.
        assert not parent.get("leaf_cases")
        item = next(
            row for row in inventory["items"] if row["qid"] == "QINV-0011"
        )
        text = generation._inventory_task_text(item)
        assert first in text
        assert "Examine Fig. 14(b)." in text
        assert second.split("Examine Fig. 14(b). ", 1)[1] in text
        # Both map panels stay owned by the whole item.
        assert parent["figure_refs"] == ["FIG-00015", "FIG-00016"]
        assert len(item["image_urls"]) == 2


def test_phase21_loader_rebuilds_a_self_consistent_but_incomplete_leaf_artifact(
    tmp_path: Path,
    monkeypatch,
):
    source = _rne_source()
    artifact_dir = tmp_path / "artifacts"
    phase2.write_phase2_artifacts(
        artifact_dir,
        mmd_text=source,
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
    # Drop the second follow-up leaf but reseal every count so the artifact
    # looks internally consistent while silently missing a learner question.
    parent["leaf_cases"] = parent["leaf_cases"][:1]
    parent["inventory_leaf_count"] = 1
    for container in (
        canonical["phase21_hardening"], canonical["source_contract"],
    ):
        container["parent_task_count"] = 26
        container["decomposed_parent_task_count"] = 1
        container["inventory_item_count"] = 26
    canonical["statistics"]["parent_tasks"] = 26
    canonical["statistics"]["decomposed_parent_tasks"] = 1
    canonical["statistics"]["inventory_leaf_tasks"] = 26
    report["phase21_hardening"] = dict(canonical["phase21_hardening"])
    report["summary"]["inventory_items"] = 26
    canonical_path.write_text(json.dumps(canonical), encoding="utf-8")
    report_path.write_text(json.dumps(report), encoding="utf-8")

    from app.services import uploads

    monkeypatch.setattr(
        uploads, "source_artifact_directory", lambda _job_id: artifact_dir
    )
    loaded, loaded_report = phase2._load_or_refresh_for_job(SimpleNamespace(
        id=312,
        filename="RNE.mmd",
        mmd_text=source,
    ))

    assert phase21.hardening_artifact_valid(loaded, loaded_report)
    # Never-split: whole parents only — the rebuild is visible in the
    # ledger's restored leaf_cases, not as extra inventory items.
    assert len(phase2.inventory_from_canonical(loaded)["items"]) == 26
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
        "post_type_assignment": 6,
        "final_content_ready": 7,
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


def test_plain_text_discuss_cue_recovers_renan_flagged_and_neutral():
    """§3 purge, item 4A: the cue STRING no longer decides anything — the
    recovery is lossless and the task ships with a neutral kind and an
    explicit not-model-ruled flag."""
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
    # The visible cue is preserved verbatim; the kind is neutral + flagged.
    assert renan["source_label"] == "Discuss"
    assert renan["source_kind"] == "checkpoint_question"
    assert renan["chapter_wide"] is False
    assert renan["activity_origin"] is False
    assert renan["source_kind_ruling"] == "not_model_ruled_flagged"
    assert any("ruled" in flag for flag in renan["review_flags"])


def test_plain_activity_and_project_cues_ship_neutral_flagged_not_keyword_kinds():
    """The retired vocabulary made 'Activity'/'Project' decide source_kind,
    activity_origin and chapter_wide. Now every plain-cue recovery ships the
    whole block verbatim with the neutral kind, flagged."""
    canonical = {
        "tasks": [],
        "sections": [{"section_id": "SEC-0001", "order": 1, "source_start": 0}],
        "blocks": [
            {
                "kind": "paragraph",
                "block_id": "BLK-00001",
                "section_id": "SEC-0001",
                "source_start": 100,
                "source_end": 220,
                "raw_text": (
                    "Activity\n"
                    "Collect pictures of national symbols and paste them in "
                    "your scrapbook.\nDiscuss what each symbol stands for."
                ),
            },
            {
                "kind": "paragraph",
                "block_id": "BLK-00002",
                "section_id": "SEC-0001",
                "source_start": 300,
                "source_end": 380,
                "raw_text": (
                    "Project\n"
                    "Prepare a wall chart on the unification of Germany."
                ),
            },
        ],
    }

    assert phase21_structure.recover_plain_task_cues(canonical) == 2
    activity, project = canonical["tasks"]

    assert activity["source_label"] == "Activity"
    assert project["source_label"] == "Project"
    for task in (activity, project):
        assert task["source_kind"] == "checkpoint_question"
        assert task["activity_origin"] is False
        assert task["chapter_wide"] is False
        assert task["source_kind_ruling"] == "not_model_ruled_flagged"
        assert task["review_flags"]
    # Lossless: the whole block body ships verbatim, not a trimmed span.
    assert activity["raw_prompt"] == (
        "Collect pictures of national symbols and paste them in "
        "your scrapbook.\nDiscuss what each symbol stands for."
    )
    assert project["raw_prompt"] == (
        "Prepare a wall chart on the unification of Germany."
    )


def test_cue_coverage_and_followup_regions_stand_down_under_an_outline():
    """§3 purge, items 4A/4B: under a model-judged chapter outline the
    keyword cue machinery must not offer a competing answer — the outline's
    ruled-task audit is the coverage authority."""
    canonical = {
        "chapter_outline": {"version": "chapter-outline-7"},
        "tasks": [],
        "sections": [{"section_id": "SEC-0001", "order": 1, "source_start": 0}],
        "blocks": [{
            "kind": "paragraph",
            "block_id": "BLK-00001",
            "section_id": "SEC-0001",
            "source_start": 100,
            "source_end": 200,
            "raw_text": "Discuss\nWhy do rivers flood in the plains?",
        }],
    }

    # No keyword-driven follow-up region may open under an outline.
    assert phase21_structure.recover_followup_task_prompts(canonical) == 0
    # The deterministic cue-coverage ledger stands down (no blocking issue
    # for the unowned cue block) — the outline audit owns coverage there.
    codes = {
        issue["code"]
        for issue in phase21_structure.task_boundary_issues(canonical)
    }
    assert "phase21_task_cue_coverage_mismatch" not in codes

    # And the block is still not lost: recovery ships it flagged.
    assert phase21_structure.recover_plain_task_cues(canonical) == 1
    recovered = canonical["tasks"][0]
    assert recovered["source_kind_ruling"] == "not_model_ruled_flagged"
    assert any("outline" in flag for flag in recovered["review_flags"])

    without_outline = {
        key: value for key, value in canonical.items()
        if key != "chapter_outline"
    }
    without_outline["tasks"] = []
    codes = {
        issue["code"]
        for issue in phase21_structure.task_boundary_issues(without_outline)
    }
    assert "phase21_task_cue_coverage_mismatch" in codes


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


def test_trim_never_rewrites_from_a_block_shared_by_several_tasks():
    # An exercise list is one block holding many numbered questions. Using
    # its prompt to "repair" any single task collapses distinct questions
    # into duplicate identities and silently loses the rest — the guard is
    # positional bookkeeping, independent of any task-kind vocabulary.
    block_text = (
        "1. In which of the following lists is a pattern present ?\n"
        "2. Which of the following are APs ?"
    )
    block_start = 100
    block_end = block_start + len(block_text)
    canonical = {
        "blocks": [{
            "block_id": "BLK-00001",
            "kind": "list",
            "raw_text": block_text,
            "source_start": block_start,
            "source_end": block_end,
        }],
        "tasks": [
            {
                "task_id": "TASK-00001",
                "source_kind": "intext_question",
                "raw_prompt": (
                    "In which of the following lists is a pattern present ?"
                ),
                "source_start": block_start,
                "source_end": block_start + 58,
                "source_location_confidence": "parser_position_estimate",
            },
            {
                "task_id": "TASK-00002",
                "source_kind": "intext_question",
                "raw_prompt": "Which of the following are APs ?",
                "source_start": block_start + 58,
                "source_end": block_end,
                "source_location_confidence": "parser_position_estimate",
            },
        ],
    }
    before = [task["raw_prompt"] for task in canonical["tasks"]]

    assert phase21_structure.trim_task_boundaries(canonical) == 0

    assert [task["raw_prompt"] for task in canonical["tasks"]] == before
    assert not any(
        task.get("phase21_boundary_repairs") for task in canonical["tasks"]
    )


def test_trim_leaves_exactly_anchored_prompts_alone_unless_markup_polluted():
    # A prompt found verbatim in the source already has its true boundaries;
    # shape triggers (containment, length) must not rewrite it. Only layout
    # markup contamination — a wire-format fact — justifies a repair.
    inner = "Which term of the AP: 3, 8, 13, 18, ... is 78 ?"
    prompt = inner + " A narrative sentence the parser kept on purpose."
    canonical = {
        "blocks": [{
            "block_id": "BLK-00001",
            "kind": "paragraph",
            "raw_text": inner,
            "source_start": 200,
            "source_end": 200 + len(inner),
        }],
        "tasks": [{
            "task_id": "TASK-00001",
            "source_kind": "intext_question",
            "raw_prompt": prompt,
            "source_start": 200,
            "source_end": 200 + len(prompt),
            "source_location_confidence": "exact_text_match",
        }],
    }

    assert phase21_structure.trim_task_boundaries(canonical) == 0
    assert canonical["tasks"][0]["raw_prompt"] == prompt
    assert canonical["tasks"][0]["source_location_confidence"] == (
        "exact_text_match"
    )


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


def test_final_normalization_keeps_authored_types_and_renumbers_cases_per_host():
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

    # Under the rewrite, normalize_final_records never rebuilds Types from
    # the mined taxonomy: the Assemble pass's per-question rendering is
    # authoritative, so the authored Type text (global Type numbers and
    # Case titles included) survives verbatim; only Case numbering is
    # renumbered continuously within each host, plus cleanup.
    assert "Type 01:" in normalized[0]["concept_details"]
    assert "Case 01: Mazzini" in normalized[0]["concept_details"]
    assert "Revolutionary organiser" not in normalized[0]["concept_details"]
    assert "Type 02:" in normalized[1]["concept_details"]
    assert "Case 01: Cavour" in normalized[1]["concept_details"]
    assert "Case 02:" not in normalized[1]["concept_details"]
    assert all("_origin_type_id" not in record for record in normalized)


def test_activity_hub_cleanup_removes_repeated_labels():
    assert phase21_render.clean_activity_hub_content(
        "Activity — Activity — Describe the source visual."
    ) == "Activity — Describe the source visual."
    assert phase21_render.clean_activity_hub_content(
        "Project — Project — Project Collect examples."
    ) == "Project — Collect examples."

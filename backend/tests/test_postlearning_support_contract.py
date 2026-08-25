"""Regressions for literary support-block transport into Post-Learning."""
from __future__ import annotations

import copy
import json

import pytest

from app.services import build_concepts_release
from app.services import postlearning_formation_contract as post
from app.services import postlearning_support_contract as support
from app.services.phase3 import envelope, polish, runner


def _concept(
    concept_id: str,
    title: str,
    role: str,
    blocks: list[str],
    *,
    qids: list[str] | None = None,
):
    return {
        "plan_concept_id": concept_id,
        "display_name": title,
        "semantic_role": role,
        "facets": [title.casefold()],
        "source_block_ids": list(blocks),
        "task_qids": list(qids or []),
        "achieving_mastery": f"Explain {title.casefold()} with source support.",
        "rationale": f"{title} is one teachable unit in the literary plan.",
    }


def _plan(*, overlap: bool = False) -> dict:
    threaded = [
        {
            "block_id": "BLK-WARM",
            "destination_plan_concept_id": "PC-1",
            "skill": "personal response before reading",
            "rationale": "The cue activates the opening stanza's new-start idea.",
        },
        {
            "block_id": "BLK-DEVICE",
            "destination_plan_concept_id": "PC-1",
            "skill": "noticing the quoted sound example in context",
            "rationale": (
                "The sourcebook quotation comes from the opening stanza and "
                "must remain reachable there as a whole box."
            ),
        },
    ]
    if overlap:
        threaded.append({
            "block_id": "BLK-TEACHER",
            "destination_plan_concept_id": "PC-3",
            "skill": "teacher guidance",
            "rationale": "Deliberate invalid overlap for the regression.",
        })
    return {
        "topics": [
            {
                "plan_topic_id": "PT-1",
                "display_name": "Stanza 1 - A shared new beginning",
                "evidence_block_ids": ["BLK-VERSE"],
                "concepts": [
                    _concept(
                        "PC-1",
                        "A shared new-school beginning",
                        "ordinary",
                        ["BLK-VERSE"],
                        qids=["Q-WARM"],
                    ),
                    _concept(
                        "PC-2",
                        "The opening stanza as one invitation",
                        "stanza_culmination",
                        ["BLK-VERSE"],
                    ),
                ],
            },
            {
                "plan_topic_id": "PT-2",
                "display_name": "Detailed Analysis of 'A Small New Start'",
                "evidence_block_ids": ["BLK-DEVICE", "BLK-QUESTION"],
                "concepts": [
                    _concept(
                        "PC-3",
                        "Language & Literary Devices",
                        "detailed_analysis",
                        ["BLK-DEVICE"],
                        qids=["Q-DEVICE"],
                    ),
                    _concept(
                        "PC-4",
                        "The poem as a whole",
                        "chapter_culmination",
                        ["BLK-VERSE", "BLK-DEVICE"],
                    ),
                ],
            },
        ],
        "threaded_components": threaded,
        "non_teaching_block_ids": ["BLK-TEACHER"],
        "notes": "",
    }


def _raw_env(*, overlap: bool = False) -> dict:
    plan = _plan(overlap=overlap)
    graph = {
        "source_contract_hash": "support-source-contract",
        "metadata": {"language_topology_plan": json.dumps(plan)},
        "topics": [
            {
                "topic_id": "TOPIC-0001",
                "plan_topic_id": "PT-1",
                "title": "Stanza 1 - A shared new beginning",
            },
            {
                "topic_id": "TOPIC-0002",
                "plan_topic_id": "PT-2",
                "title": "Detailed Analysis of 'A Small New Start'",
            },
        ],
        "subtopics": [],
        "blocks": [
            {"block_id": "BLK-WARM", "topic_id": "TOPIC-0001", "kind": "paragraph"},
            {"block_id": "BLK-VERSE", "topic_id": "TOPIC-0001", "kind": "paragraph"},
            {"block_id": "BLK-DEVICE", "topic_id": "TOPIC-0002", "kind": "paragraph"},
            {"block_id": "BLK-QUESTION", "topic_id": "TOPIC-0002", "kind": "list"},
            {"block_id": "BLK-TEACHER", "topic_id": "TOPIC-0002", "kind": "paragraph"},
        ],
    }
    canonical = {
        "blocks": [
            {
                "block_id": "BLK-WARM",
                "display_text": (
                    "Think and write.\nNew things I want to learn this year."
                ),
                "task_ids": ["TASK-WARM"],
            },
            {
                "block_id": "BLK-VERSE",
                "display_text": (
                    "A new school year and a brand new start,\n"
                    "A year in which we will all take part."
                ),
            },
            {
                "block_id": "BLK-DEVICE",
                "display_text": (
                    "Alliteration is a figure of speech where the same "
                    "initial consonant sound is repeated in nearby words.\n"
                    "Example: A year in which we will all take part."
                ),
            },
            {
                "block_id": "BLK-QUESTION",
                "display_text": "Find more examples of Alliteration from the poem.",
                "task_ids": ["TASK-DEVICE"],
            },
            {
                "block_id": "BLK-TEACHER",
                "display_text": "For the Facilitator: explain with other poems.",
            },
        ],
    }
    inventory = {
        "items": [
            {
                "qid": "Q-WARM",
                "_acsd_task_id": "TASK-WARM",
                "source_kind": "checkpoint_question",
                "normalized_task": (
                    "Think and write. New things I want to learn this year."
                ),
                "_activity_origin": False,
            },
            {
                "qid": "Q-DEVICE",
                "_acsd_task_id": "TASK-DEVICE",
                "source_kind": "checkpoint_question",
                "normalized_task": (
                    "Find more examples of Alliteration from the poem."
                ),
                "_activity_origin": False,
            },
        ],
    }
    raw_build = getattr(
        envelope.build,
        "_aegis_postlearning_support_original",
        envelope.build,
    )
    return raw_build(
        graph=graph,
        canonical=canonical,
        skeleton_rows=[{
            "topic": "Stanza 1 - A shared new beginning",
            "parent_concept": "Warm-up",
            "concept_title": "Setting personal learning goals",
            "concept_details": "Description: A generic support-row draft.",
            "keywords": "warm-up",
            "_semantic_topic_id": "TOPIC-0001",
        }],
        inventory=inventory,
        mined_types={"types": []},
        metadata={
            "board": "Maharashtra",
            "grade": "06",
            "subject": "English",
            "unit": "POEM",
            "chapter_title": "A Small New Start",
            "language_topology_plan": json.dumps(plan),
        },
    )


def _row_by_plan_id(rows, plan_id):
    return next(
        row for row in rows
        if (
            (row.get(post.PLAN_IDENTITY_FIELD) or {}).get("plan_concept_id")
            == plan_id
        )
    )


def test_prepare_envelope_promotes_task_support_without_losing_question_identity():
    prepared = support.prepare_envelope(_raw_env())
    assert envelope.validate(prepared) == prepared
    assert len(prepared["skeleton_rows"]) == 4

    items = {
        item["qid"]: item for item in prepared["inventory"]["items"]
    }
    assert items["Q-WARM"]["_activity_origin"] is True
    assert items["Q-DEVICE"]["_activity_origin"] is False
    assert set(items) == {"Q-WARM", "Q-DEVICE"}
    assert prepared["metadata"]["post_language_support_materialization"] == {
        "version": support.SUPPORT_VERSION,
        "threaded_block_count": 2,
        "destination_concept_count": 1,
        "promoted_hub_qids": ["Q-WARM"],
        "non_teaching_block_count": 1,
    }
    # Re-entry is byte-shape stable: the stored envelope and the runner wrapper
    # must compute the same sealed identity.
    assert support.prepare_envelope(prepared) == prepared


def test_attach_support_carries_whole_blocks_to_the_decided_concept_once():
    prepared = support.prepare_envelope(_raw_env())
    first = support.attach_support(prepared, prepared["skeleton_rows"])
    second = support.attach_support(prepared, first)
    assert second == first

    opening = _row_by_plan_id(first, "PC-1")
    details = opening["concept_details"]
    assert details.count("Activity/Info Hub:") == 1
    assert "Think and write.\nNew things I want to learn this year." in details
    assert (
        "Alliteration is a figure of speech where the same initial "
        "consonant sound is repeated in nearby words."
    ) in details
    assert [
        row["block_id"] for row in opening[support.SUPPORT_FIELD]
    ] == ["BLK-WARM", "BLK-DEVICE"]

    whole = _row_by_plan_id(first, "PC-4")
    assert whole[support.NON_TEACHING_FIELD] == [{
        "block_id": "BLK-TEACHER",
        "text": "For the Facilitator: explain with other poems.",
    }]


def test_one_source_occurrence_cannot_be_threaded_and_non_teaching():
    with pytest.raises(ValueError, match="both threaded support and non-teaching"):
        support.prepare_envelope(_raw_env(overlap=True))


def test_install_wraps_build_run_and_polish_once_and_keeps_audit_private():
    support.install()
    build_once = envelope.build
    run_once = runner.run
    polish_once = polish.polish
    support.install()

    assert envelope.build is build_once
    assert runner.run is run_once
    assert polish.polish is polish_once
    assert getattr(envelope.build, "_aegis_postlearning_support_build", False)
    assert getattr(runner.run, "_aegis_postlearning_support_runner", False)
    assert getattr(polish.polish, "_aegis_postlearning_support_polish", False)
    assert support.SUPPORT_AUDIT_FIELDS.issubset(
        build_concepts_release._RELEASE_AUDIT_FIELDS
    )

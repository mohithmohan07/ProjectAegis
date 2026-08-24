"""Literary topology grain follows the source/grade, never stanza arithmetic."""
from __future__ import annotations

from app.services import canonical_source_phase2 as phase2
from app.services import language_topology as topology
from app.services import language_topology_grade_contract as contract


POEM = """# A Small New Start

We enter school with plans to make,
And learn together for learning's sake.

We try new work and help our friends,
And keep on going until it ends.

# Poetic Device

Alliteration repeats an initial consonant sound.

# Task

Find one example of alliteration.
"""
WORK = "A Small New Start"


def _canonical():
    return phase2.compile_phase2_source(
        POEM,
        source_filename="grade6-poem.mmd",
        consumer_module="grade_contract_test",
    ).canonical


def _instruction_set():
    return {
        "instruction_set_sha256": "b" * 64,
        "slots": {
            "language_mode": {
                "mode": "poem",
                "rationale": "The source is stanza-structured verse.",
            },
            "subject_topology_guidance": (
                "Grade Six: teach the short poem as a coherent new-school "
                "experience, then consolidate its sound device."
            ),
            "grade_band_vocabulary": (
                "Use clear Grade Six explanations and combine closely related "
                "ideas rather than over-fragmenting them."
            ),
            "board_publication_conventions": (
                "Warm-up, poem reading, Poetic Device and task labels are "
                "sourcebook structure, not automatic teaching-topic boundaries."
            ),
            "chapter_cautions": [],
        },
    }


def test_live_contract_removes_stanza_and_line_pair_structure_quotas():
    contract.install()

    author = topology._AUTHOR_SYSTEM.casefold()
    critic = topology._CRITIC_SYSTEM.casefold()

    assert topology.LANGUAGE_ADAPTER_VERSION == "language-topology-2"
    assert "one topic per stanza" not in author
    assert "one concept per pair of lines" not in author
    assert "a stanza does not automatically become a topic" in author
    assert "never target a number of topics or concepts" in author
    assert "grade" in author and "sourcebook" in author
    assert "false granularity" in critic
    assert "target count" in critic


def test_mechanical_contract_accepts_grouped_poem_units_without_one_topic_per_stanza():
    contract.install()
    canonical = _canonical()
    blocks = topology._content_blocks(canonical)
    tasks = topology._task_payloads(canonical)
    block_ids = [str(block.get("block_id") or "") for block in blocks]
    task_qids = [task["qid"] for task in tasks]

    # Several source pieces (two verse paragraphs, the device explanation and
    # the task area) are deliberately taught under ONE coherent Grade-6 topic.
    # The only second topic is the required chapter-wide Detailed Analysis.
    plan = {
        "topics": [
            {
                "plan_topic_id": "PT-1",
                "display_name": "Beginning Grade Six with confidence",
                "evidence_block_ids": block_ids,
                "concepts": [
                    {
                        "plan_concept_id": "PC-1",
                        "display_name": "A shared new-school beginning",
                        "semantic_role": "ordinary",
                        "facets": ["meaning", "confidence", "teamwork"],
                        "source_block_ids": block_ids,
                        "task_qids": task_qids,
                        "achieving_mastery": (
                            "Explain the poem's shared beginning and identify "
                            "the taught sound pattern."
                        ),
                        "rationale": (
                            "The short Grade-6 source develops one coherent "
                            "learning idea, so its neighbouring pieces are "
                            "taught together."
                        ),
                    },
                ],
            },
            {
                "plan_topic_id": "PT-2",
                "display_name": topology.detailed_analysis_title(WORK),
                "evidence_block_ids": [],
                "concepts": [
                    {
                        "plan_concept_id": "PC-2",
                        "display_name": "Theme / Central Idea",
                        "semantic_role": "detailed_analysis",
                        "facets": ["theme"],
                        "source_block_ids": [],
                        "task_qids": [],
                        "achieving_mastery": "State the central idea clearly.",
                        "rationale": "Whole-work synthesis.",
                    },
                    {
                        "plan_concept_id": "PC-3",
                        "display_name": "Whole-poem culmination",
                        "semantic_role": "chapter_culmination",
                        "facets": ["synthesis"],
                        "source_block_ids": [],
                        "task_qids": [],
                        "achieving_mastery": (
                            "Connect the poem's meaning and sound device in a "
                            "single explanation."
                        ),
                        "rationale": "Consolidates the chapter without repetition.",
                    },
                ],
            },
        ],
        "threaded_components": [],
        "non_teaching_block_ids": [],
        "notes": "",
    }

    assert topology.plan_defects(
        plan,
        blocks,
        tasks,
        work_name=WORK,
    ) == []


def test_adapter_version_rekeys_old_stanza_authored_plan_identity(monkeypatch):
    contract.install()
    canonical = _canonical()
    instruction_set = _instruction_set()
    new_key = topology._decision_key(
        canonical,
        instruction_set["instruction_set_sha256"],
        "poem",
        work_name=WORK,
    )

    monkeypatch.setattr(topology, "LANGUAGE_ADAPTER_VERSION", "language-topology-1")
    old_key = topology._decision_key(
        canonical,
        instruction_set["instruction_set_sha256"],
        "poem",
        work_name=WORK,
    )

    assert new_key != old_key

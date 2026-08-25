"""Literary topology follows the sourcebook reading unit without line arithmetic."""
from __future__ import annotations

import importlib

from app.services import canonical_source_phase2 as phase2
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


def _topology():
    """Resolve the current service module after any reload/re-import."""
    module = importlib.import_module("app.services.language_topology")
    contract.install()
    return module


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
                "Grade Six: read each stanza as its own meaning unit and "
                "finish with whole-poem analysis."
            ),
            "grade_band_vocabulary": (
                "Use clear Grade Six explanations without fragmenting one "
                "meaning-bearing pair of lines."
            ),
            "board_publication_conventions": (
                "Warm-up, read-and-recite, Poetic Device and task labels are "
                "supporting sourcebook blocks, not automatic concepts."
            ),
            "chapter_cautions": [],
        },
    }


def _concept(
    concept_id: str,
    display_name: str,
    role: str,
    block_ids: list[str],
    *,
    task_qids: list[str] | None = None,
    facets: list[str] | None = None,
    mastery: str,
    rationale: str,
):
    return {
        "plan_concept_id": concept_id,
        "display_name": display_name,
        "semantic_role": role,
        "facets": list(facets or []),
        "source_block_ids": list(block_ids),
        "task_qids": list(task_qids or []),
        "achieving_mastery": mastery,
        "rationale": rationale,
    }


def _stanza_plan(topology, canonical):
    blocks = topology._content_blocks(canonical)
    tasks = topology._task_payloads(canonical)

    def ids_with(*needles: str) -> list[str]:
        return [
            str(block.get("block_id") or "")
            for block in blocks
            if any(
                needle in str(block.get("raw_text") or "")
                for needle in needles
            )
        ]

    first_stanza = ids_with(
        "We enter school with plans to make",
        "A Small New Start",
    )
    second_stanza = ids_with("We try new work and help our friends")
    used_local = set(first_stanza) | set(second_stanza)
    analysis_evidence = [
        str(block.get("block_id") or "")
        for block in blocks
        if str(block.get("block_id") or "") not in used_local
    ]
    verse = list(dict.fromkeys([*first_stanza, *second_stanza]))
    task_qids = [task["qid"] for task in tasks]

    return {
        "topics": [
            {
                "plan_topic_id": "PT-1",
                "display_name": "Stanza 1 - Plans for a shared beginning",
                "evidence_block_ids": first_stanza,
                "concepts": [
                    _concept(
                        "PC-1",
                        "Entering school with plans to learn together",
                        "ordinary",
                        first_stanza,
                        facets=["meaning", "shared learning"],
                        mastery=(
                            "Explain how the opening pair joins personal plans "
                            "with learning together."
                        ),
                        rationale=(
                            "The two adjacent lines carry one complete idea "
                            "about beginning school with shared purpose."
                        ),
                    ),
                    _concept(
                        "PC-2",
                        "The opening stanza as one invitation",
                        "stanza_culmination",
                        first_stanza,
                        facets=["stanza synthesis"],
                        mastery=(
                            "Connect the stanza's meaning and paired end sounds "
                            "in one explanation."
                        ),
                        rationale="The stanza closes one coherent opening appeal.",
                    ),
                ],
            },
            {
                "plan_topic_id": "PT-2",
                "display_name": "Stanza 2 - Effort that continues together",
                "evidence_block_ids": second_stanza,
                "concepts": [
                    _concept(
                        "PC-3",
                        "Trying new work and helping friends",
                        "ordinary",
                        second_stanza,
                        facets=["effort", "cooperation"],
                        mastery=(
                            "Explain how effort and helping others sustain the "
                            "poem's new beginning."
                        ),
                        rationale=(
                            "The second pair of lines forms a distinct stanza "
                            "about persistence and mutual help."
                        ),
                    ),
                    _concept(
                        "PC-4",
                        "The second stanza as a promise to continue",
                        "stanza_culmination",
                        second_stanza,
                        facets=["stanza synthesis"],
                        mastery=(
                            "Show how action, cooperation and rhyme make the "
                            "stanza feel resolved."
                        ),
                        rationale="The stanza completes its own teaching movement.",
                    ),
                ],
            },
            {
                "plan_topic_id": "PT-3",
                "display_name": topology.detailed_analysis_title(WORK),
                "evidence_block_ids": analysis_evidence,
                "concepts": [
                    _concept(
                        "PC-5", "Theme / Central Idea", "detailed_analysis",
                        verse, facets=["theme"],
                        mastery="State the poem's central idea with support.",
                        rationale="Whole-poem thematic synthesis.",
                    ),
                    _concept(
                        "PC-6", "Plot / Development of Ideas",
                        "detailed_analysis", verse, facets=["development"],
                        mastery=(
                            "Trace the movement from beginning school to "
                            "sustained shared effort."
                        ),
                        rationale=(
                            "For this non-narrative poem the lens examines idea "
                            "progression rather than inventing a plot."
                        ),
                    ),
                    _concept(
                        "PC-7", "Characterisation / Speaker",
                        "detailed_analysis", verse, facets=["speaker"],
                        mastery="Describe the inclusive speaking voice.",
                        rationale="The poem has a shared voice rather than a cast.",
                    ),
                    _concept(
                        "PC-8", "Setting & Atmosphere", "detailed_analysis",
                        verse, facets=["school setting", "atmosphere"],
                        mastery="Explain the hopeful school atmosphere.",
                        rationale="The school beginning shapes the mood.",
                    ),
                    _concept(
                        "PC-9", "Language & Literary Devices",
                        "detailed_analysis", [*verse, *analysis_evidence],
                        task_qids=task_qids,
                        facets=["alliteration", "rhyme"],
                        mastery=(
                            "Identify the taught sound device and explain how "
                            "sound supports meaning."
                        ),
                        rationale=(
                            "The source's device explanation and task belong in "
                            "the whole-work language analysis."
                        ),
                    ),
                    _concept(
                        "PC-10", "Culmination: the poem as a whole",
                        "chapter_culmination", verse,
                        facets=["whole-work synthesis"],
                        mastery=(
                            "Connect theme, speaker, atmosphere and sound in one "
                            "whole-poem explanation."
                        ),
                        rationale="Final synthesis without repeating the lenses.",
                    ),
                ],
            },
        ],
        "threaded_components": [],
        "non_teaching_block_ids": [],
        "notes": "",
    }


def test_live_api_author_uses_sourcebook_faithful_contract(monkeypatch, tmp_path):
    """Exercise the real model seam; no local stanza parser authors the plan."""
    topology = _topology()
    canonical = _canonical()
    plan = _stanza_plan(topology, canonical)
    systems: list[str] = []

    def fake_provider(
        *, system, prompt, schema, purpose="source_adjudication", max_tokens=None,
    ):
        systems.append(system)
        if system == contract.CRITIC_SYSTEM:
            return {"verdict": "concur", "dissents": []}
        assert system == contract.AUTHOR_SYSTEM
        return plan

    monkeypatch.setattr(topology, "_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(topology, "_provider_ready", lambda: True)
    monkeypatch.setattr(topology, "_call_provider", fake_provider)

    sealed = topology.author_language_plan(
        canonical,
        instruction_set=_instruction_set(),
        work_name=WORK,
    )

    assert sealed["adapter_version"] == contract.LANGUAGE_ADAPTER_VERSION
    assert sealed["plan"] == plan
    assert [topic["display_name"] for topic in sealed["plan"]["topics"]] == [
        "Stanza 1 - Plans for a shared beginning",
        "Stanza 2 - Effort that continues together",
        topology.detailed_analysis_title(WORK),
    ]
    assert systems == [contract.AUTHOR_SYSTEM, contract.CRITIC_SYSTEM]


def test_mechanical_contract_accepts_stanza_topics_and_topic_culminations():
    topology = _topology()
    canonical = _canonical()
    plan = _stanza_plan(topology, canonical)

    assert "topic_culmination" in topology.SEMANTIC_ROLES
    assert topology.plan_defects(
        plan,
        topology._content_blocks(canonical),
        topology._task_payloads(canonical),
        work_name=WORK,
    ) == []


def test_prompt_binds_sourcebook_structure_without_physical_line_arithmetic():
    _topology()
    prompt = " ".join(contract.AUTHOR_SYSTEM.split())

    assert "Each stanza you identify becomes one Topic" in prompt
    assert "never iterate physical lines two at a time" in prompt
    assert "Warm-up" in prompt
    assert "DETAILED ANALYSIS" in prompt
    assert "threaded_components" in prompt
    assert "non_teaching_block_ids" in prompt
    assert "target count" in prompt


def test_adapter_version_rekeys_retired_grouped_stanza_plan_identity(monkeypatch):
    topology = _topology()
    canonical = _canonical()
    instruction_set = _instruction_set()
    new_key = topology._decision_key(
        canonical,
        instruction_set["instruction_set_sha256"],
        "poem",
        work_name=WORK,
    )

    monkeypatch.setattr(
        topology, "LANGUAGE_ADAPTER_VERSION", "language-topology-2"
    )
    old_key = topology._decision_key(
        canonical,
        instruction_set["instruction_set_sha256"],
        "poem",
        work_name=WORK,
    )

    assert new_key != old_key

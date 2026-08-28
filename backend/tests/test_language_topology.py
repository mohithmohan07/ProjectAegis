"""Step 11 — language-mode topology adapter regressions (lean build mode)."""
from __future__ import annotations

import json

import pytest

from app.services import canonical_source_phase2 as phase2
from app.services import language_topology as lt
from app.services.phase3 import prompts as p3_prompts


POEM = """# The Brook

I come from haunts of coot and hern,
I make a sudden sally.

# Exercises

Discuss
What does the brook say?
"""

WORK = "The Brook"


def _canonical():
    return phase2.compile_phase2_source(
        POEM, source_filename="brook.mmd", consumer_module="lt_tests"
    ).canonical


def _instruction_set(mode="poem"):
    return {
        "instruction_set_sha256": "a" * 64,
        "slots": {
            "language_mode": {"mode": mode, "rationale": "stanza form"},
            "subject_topology_guidance": "",
            "grade_band_vocabulary": "",
            "board_publication_conventions": "",
            "chapter_cautions": [],
        },
    }


def _valid_plan(canonical):
    blocks = lt._content_blocks(canonical)
    ids = [b["block_id"] for b in blocks]
    tasks = lt._task_payloads(canonical)
    return {
        "topics": [
            {
                "plan_topic_id": "PT-1",
                "display_name": "Stanza 1 — the brook sets out",
                "evidence_block_ids": ids[:-1],
                "concepts": [
                    {
                        "plan_concept_id": "PC-1",
                        "display_name": "Courage",
                        "semantic_role": "ordinary",
                        "facets": ["literal", "metaphorical"],
                        "source_block_ids": ids[:2],
                        "task_qids": [t["qid"] for t in tasks],
                        "achieving_mastery": "Explain both readings aloud.",
                        "rationale": "The pair carries one meaning.",
                    },
                    {
                        "plan_concept_id": "PC-2",
                        "display_name": "Stanza together",
                        "semantic_role": "stanza_culmination",
                        "facets": ["rhyme"],
                        "source_block_ids": ids[:2],
                        "task_qids": [],
                        "achieving_mastery": "Read the stanza as one voice.",
                        "rationale": "Elements together.",
                    },
                ],
            },
            {
                "plan_topic_id": "PT-2",
                "display_name": lt.detailed_analysis_title(WORK),
                "evidence_block_ids": [ids[-1]],
                "concepts": [
                    {
                        "plan_concept_id": "PC-3",
                        "display_name": "Theme / Central Idea",
                        "semantic_role": "detailed_analysis",
                        "facets": ["theme"],
                        "source_block_ids": [ids[-1]],
                        "task_qids": [],
                        "achieving_mastery": "State the theme in one line.",
                        "rationale": "Chapter synthesis.",
                    },
                    {
                        "plan_concept_id": "PC-4",
                        "display_name": "Culmination",
                        "semantic_role": "chapter_culmination",
                        "facets": ["synthesis"],
                        "source_block_ids": [ids[-1]],
                        "task_qids": [],
                        "achieving_mastery": "Connect every stanza's arc.",
                        "rationale": "Whole-work reading.",
                    },
                ],
            },
        ],
        "threaded_components": [],
        "non_teaching_block_ids": [],
        "notes": "",
    }


def _script(canonical, *, plan=None, critic=None, fail_first=False):
    calls = {
        "author": 0,
        "correction": 0,
        "fixer": 0,
        "critic": 0,
        "correction_systems": [],
        "fixer_systems": [],
    }
    good = plan or _valid_plan(canonical)

    def call(*, system, prompt, schema, purpose="source_adjudication",
             max_tokens=None, model=None):
        if system == lt._CRITIC_SYSTEM:
            calls["critic"] += 1
            return critic or {"verdict": "concur", "dissents": []}
        if system == lt._fixer_system():
            calls["fixer"] += 1
            calls["fixer_systems"].append(system)
            return json.loads(json.dumps(good))
        if system == lt._correction_system():
            calls["correction"] += 1
            calls["correction_systems"].append(system)
            if fail_first:
                broken = json.loads(json.dumps(good))
                broken["topics"][0]["concepts"][0]["achieving_mastery"] = ""
                return broken
            return json.loads(json.dumps(good))
        calls["author"] += 1
        if fail_first:
            broken = json.loads(json.dumps(good))
            del broken["topics"][0]
            return broken
        return json.loads(json.dumps(good))

    return call, calls


def test_poem_plan_seals_with_roles_and_accounting(monkeypatch, tmp_path):
    monkeypatch.setattr(lt, "_CACHE_DIR", tmp_path / "cache")
    canonical = _canonical()
    call, calls = _script(canonical)
    monkeypatch.setattr(lt, "_call_provider", call)
    monkeypatch.setattr(lt, "_provider_ready", lambda: True)
    sealed = lt.author_language_plan(
        canonical, instruction_set=_instruction_set(), work_name=WORK
    )
    assert sealed["status"] == "sealed"
    assert sealed["mode"] == "poem"
    assert sealed["plan_sha256"]
    roles = [
        c["semantic_role"]
        for t in sealed["plan"]["topics"] for c in t["concepts"]
    ]
    assert "stanza_culmination" in roles
    assert "detailed_analysis" in roles and "chapter_culmination" in roles
    assert calls["author"] == 1 and calls["critic"] == 1
    # The slot text renders the plan body for the envelope suffix.
    slot = lt.plan_slot_text(sealed)
    assert "PT-1" in slot and "Detailed Analysis" in slot


def test_invalid_plan_corrects_then_fixer_completes_flagged(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(lt, "_CACHE_DIR", tmp_path / "cache")
    canonical = _canonical()
    call, calls = _script(canonical, fail_first=True)
    monkeypatch.setattr(lt, "_call_provider", call)
    monkeypatch.setattr(lt, "_provider_ready", lambda: True)
    sealed = lt.author_language_plan(
        canonical, instruction_set=_instruction_set(), work_name=WORK
    )
    assert calls["correction"] == 1 and calls["fixer"] == 1
    assert calls["correction_systems"][0].startswith(lt._AUTHOR_SYSTEM)
    assert calls["fixer_systems"][0].startswith(lt._AUTHOR_SYSTEM)
    policy = " ".join(lt.SOURCE_FAITHFUL_PROSE_ROUTING_POLICY.split())
    assert policy in " ".join(calls["correction_systems"][0].split())
    assert policy in " ".join(calls["fixer_systems"][0].split())
    assert sealed["fixer_decision"] is not None
    assert any("Fixer" in flag for flag in sealed["review_flags"])
    assert sealed["correction_history"]


def test_critic_dissent_is_a_flag_never_a_gate(monkeypatch, tmp_path):
    monkeypatch.setattr(lt, "_CACHE_DIR", tmp_path / "cache")
    canonical = _canonical()
    call, _ = _script(canonical, critic={
        "verdict": "dissent",
        "dissents": [{"target": "PC-1", "reason": "unit reads too wide"}],
    })
    monkeypatch.setattr(lt, "_call_provider", call)
    monkeypatch.setattr(lt, "_provider_ready", lambda: True)
    sealed = lt.author_language_plan(
        canonical, instruction_set=_instruction_set(), work_name=WORK
    )
    assert sealed["status"] == "sealed"
    assert any("critic dissent" in flag for flag in sealed["review_flags"])


def test_artifact_replay_makes_zero_calls(monkeypatch, tmp_path):
    monkeypatch.setattr(lt, "_CACHE_DIR", tmp_path / "cache")
    canonical = _canonical()
    call, _ = _script(canonical)
    monkeypatch.setattr(lt, "_call_provider", call)
    monkeypatch.setattr(lt, "_provider_ready", lambda: True)
    first = lt.ensure_language_plan(
        canonical,
        instruction_set=_instruction_set(),
        work_name=WORK,
        artifact_dir=tmp_path / "artifacts",
    )
    assert not first.get("replayed")

    def explode(**kwargs):  # pragma: no cover
        raise AssertionError("replay must not call the provider")

    monkeypatch.setattr(lt, "_call_provider", explode)
    second = lt.ensure_language_plan(
        canonical,
        instruction_set=_instruction_set(),
        work_name=WORK,
        artifact_dir=tmp_path / "artifacts",
    )
    assert second["replayed"] is True
    assert second["plan_sha256"] == first["plan_sha256"]


def test_provider_down_is_a_named_halt_never_a_fallback(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(lt, "_CACHE_DIR", tmp_path / "cache")
    canonical = _canonical()
    monkeypatch.setattr(lt, "_provider_ready", lambda: False)
    with pytest.raises(lt.LanguagePlanError) as err:
        lt.author_language_plan(
            canonical, instruction_set=_instruction_set(), work_name=WORK
        )
    assert "no live provider" in str(err.value)


def test_expository_mode_never_reaches_the_adapter(monkeypatch, tmp_path):
    monkeypatch.setattr(lt, "_CACHE_DIR", tmp_path / "cache")
    canonical = _canonical()
    with pytest.raises(lt.LanguagePlanError):
        lt.author_language_plan(
            canonical,
            instruction_set=_instruction_set(mode="expository"),
            work_name=WORK,
        )


def test_final_topic_template_is_mechanical(monkeypatch, tmp_path):
    canonical = _canonical()
    plan = _valid_plan(canonical)
    plan["topics"][-1]["display_name"] = "Detailed Analysis"
    defects = lt.plan_defects(
        plan,
        lt._content_blocks(canonical),
        lt._task_payloads(canonical),
        work_name=WORK,
    )
    assert any("final topic display name" in d for d in defects)


def test_unaccounted_block_is_a_defect(monkeypatch):
    canonical = _canonical()
    plan = _valid_plan(canonical)
    plan["topics"][0]["evidence_block_ids"] = (
        plan["topics"][0]["evidence_block_ids"][1:]
    )
    plan["topics"][0]["concepts"][0]["source_block_ids"] = []
    plan["topics"][0]["concepts"][1]["source_block_ids"] = []
    defects = lt.plan_defects(
        plan,
        lt._content_blocks(canonical),
        lt._task_payloads(canonical),
        work_name=WORK,
    )
    assert any("unaccounted source blocks" in d for d in defects)


def test_plan_slot_rides_the_phase3_suffix():
    suffix = p3_prompts.instruction_rules_suffix({
        "metadata": {"instruction_slots": {
            "subject_topology_guidance": "",
            "grade_band_vocabulary": "",
            lt.PLAN_SLOT_KEY: '{"topics": []}',
        }},
    })
    assert "Language topology plan" in suffix
    # And its absence keeps the payload byte-identical (no re-keying of
    # existing expository runs).
    assert p3_prompts.instruction_rules_suffix({
        "metadata": {"instruction_slots": {
            "subject_topology_guidance": "",
            "grade_band_vocabulary": "",
        }},
    }) == ""


def test_source_faithful_prose_routing_policy_reaches_every_decision_prompt():
    policy = " ".join(lt.SOURCE_FAITHFUL_PROSE_ROUTING_POLICY.split())

    assert "For short prose" in policy
    assert "Never re-parent it to a later plot event" in policy
    assert "may remain its own source-aligned Topic or concept" in policy
    assert "opening_pre_reading" in policy
    for system in (
        lt._AUTHOR_SYSTEM,
        lt._correction_system(),
        lt._fixer_system(),
    ):
        assert policy in " ".join(system.split())


def test_threaded_support_requires_an_explicit_placement_context():
    canonical = _canonical()
    plan = _valid_plan(canonical)
    block_id = lt._content_blocks(canonical)[0]["block_id"]
    plan["threaded_components"] = [{
        "block_id": block_id,
        "destination_plan_concept_id": "PC-1",
        "skill": "prepare to read",
        "rationale": "The opening prompt activates prior experience.",
    }]

    defects = lt.plan_defects(
        plan,
        lt._content_blocks(canonical),
        lt._task_payloads(canonical),
        work_name=WORK,
    )
    assert any("invalid placement_context ''" in defect for defect in defects)

    plan["threaded_components"][0][
        "placement_context"
    ] = "opening_pre_reading"
    defects = lt.plan_defects(
        plan,
        lt._content_blocks(canonical),
        lt._task_payloads(canonical),
        work_name=WORK,
    )
    assert not any("placement_context" in defect for defect in defects)

    threaded_schema = lt.plan_schema()["schema"]["properties"][
        "threaded_components"
    ]["items"]
    assert "placement_context" in threaded_schema["required"]
    assert threaded_schema["properties"]["placement_context"]["enum"] == list(
        lt.THREADING_PLACEMENT_CONTEXTS
    )


def test_opening_support_cannot_be_reparented_to_a_later_concept():
    canonical = _canonical()
    plan = _valid_plan(canonical)
    block_id = lt._content_blocks(canonical)[0]["block_id"]
    plan["threaded_components"] = [{
        "block_id": block_id,
        "destination_plan_concept_id": "PC-2",
        "placement_context": "opening_pre_reading",
        "skill": "activate prior experience",
        "rationale": "A deliberately contradictory late destination.",
    }]

    defects = lt.plan_defects(
        plan,
        lt._content_blocks(canonical),
        lt._task_payloads(canonical),
        work_name=WORK,
    )

    assert any(
        "opening support must route to the first plan concept" in defect
        for defect in defects
    ), defects


def test_one_source_block_requires_exactly_one_threading_verdict():
    canonical = _canonical()
    plan = _valid_plan(canonical)
    block_id = lt._content_blocks(canonical)[0]["block_id"]
    row = {
        "block_id": block_id,
        "destination_plan_concept_id": "PC-1",
        "placement_context": "contextual_support",
        "skill": "retain source context",
        "rationale": "The source occurrence stays whole.",
    }
    plan["threaded_components"] = [row, {**row, "skill": "conflicting copy"}]

    defects = lt.plan_defects(
        plan,
        lt._content_blocks(canonical),
        lt._task_payloads(canonical),
        work_name=WORK,
    )

    assert any("repeats source block" in defect for defect in defects)


def test_author_receives_ordered_source_positions_for_placement_judgment():
    canonical = _canonical()
    blocks = lt._content_blocks(canonical)
    request = json.loads(lt._author_request(
        blocks,
        lt._task_payloads(canonical),
        mode="prose",
        rationale="The Architect recorded narrative prose.",
        slots=_instruction_set(mode="prose")["slots"],
        work_name=WORK,
    ))

    assert [row["source_order"] for row in request["blocks"]] == list(
        range(1, len(blocks) + 1)
    )
    assert [row["source_start"] for row in request["blocks"]] == [
        int(block.get("source_start") or 0) for block in blocks
    ]


# ---------------------------------------------------------------------------
# Audit F2 resolution: the plan's topics ARE the graph's topics
# ---------------------------------------------------------------------------

def _plan_metadata(plan):
    return {
        "board": "ICSE", "grade": "10", "subject": "English",
        "chapter_title": WORK,
        "language_topology_plan": json.dumps(plan, ensure_ascii=False),
    }


def test_plan_topics_materialize_as_graph_topics(monkeypatch, tmp_path):
    from app.services import canonical_source_phase3 as phase3

    canonical = _canonical()
    plan = _valid_plan(canonical)

    def exploding_hierarchy(_payload):  # pragma: no cover
        raise AssertionError(
            "the plan owns the topics; the hierarchy call is double spend"
        )

    graph, _report = phase3.compile_semantic_graph(
        canonical,
        source_text=POEM,
        metadata=_plan_metadata(plan),
        hierarchy_provider=exploding_hierarchy,
    )
    titles = [row["title"] for row in graph["topics"]]
    assert titles == [
        "Stanza 1 — the brook sets out",
        lt.detailed_analysis_title(WORK),
    ]
    assert [row["plan_topic_id"] for row in graph["topics"]] == [
        "PT-1", "PT-2",
    ]
    assert all(
        row["source"] == "language_topology_plan" for row in graph["topics"]
    )
    assert [row["topic_id"] for row in graph["topics"]] == [
        "TOPIC-0001", "TOPIC-0002",
    ]
    # Spans partition: every position resolves to exactly one topic.
    starts = [row["source_start"] for row in graph["topics"]]
    assert starts == sorted(starts)
    assert graph["topics"][0]["source_end"] == (
        graph["topics"][1]["source_start"]
    )
    assert graph["topics"][-1]["source_end"] == len(POEM)


def test_without_a_plan_the_graph_topics_are_untouched():
    from app.services import canonical_source_phase3 as phase3

    canonical = _canonical()
    graph, _report = phase3.compile_semantic_graph(
        canonical,
        source_text=POEM,
        metadata={"chapter_title": WORK, "subject": "English"},
    )
    assert all(
        row.get("source") != "language_topology_plan"
        for row in graph["topics"]
    )


def test_an_unreadable_plan_slot_fails_closed():
    from app.services import canonical_source_phase3 as phase3

    canonical = _canonical()
    with pytest.raises(ValueError, match="not readable JSON"):
        phase3.compile_semantic_graph(
            canonical,
            source_text=POEM,
            metadata={"language_topology_plan": "{not json"},
        )

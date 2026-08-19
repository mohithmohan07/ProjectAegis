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
    calls = {"author": 0, "correction": 0, "fixer": 0, "critic": 0}
    good = plan or _valid_plan(canonical)

    def call(*, system, prompt, schema, purpose="source_adjudication",
             max_tokens=None):
        if system == lt._CRITIC_SYSTEM:
            calls["critic"] += 1
            return critic or {"verdict": "concur", "dissents": []}
        if system == lt._FIXER_SYSTEM:
            calls["fixer"] += 1
            return json.loads(json.dumps(good))
        if system == lt._CORRECTION_SYSTEM:
            calls["correction"] += 1
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

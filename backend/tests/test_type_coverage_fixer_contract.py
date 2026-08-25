"""Residual Type coverage is a Fixer judgment, never a semantic regex fallback."""
from __future__ import annotations

import copy

from app.services import generation as g
from app.services import type_coverage_fixer_contract as contract
from app.services.phase3 import fixer as fixer_mod
from app.services.phase3 import kernel


def _inventory():
    return {
        "items": [{
            "qid": "QINV-0001",
            "source_kind": "checkpoint_question",
            "source_label": "Task",
            "topic_hint": "Poetic Device",
            "raw_task": "Find more examples of Alliteration from the poem.",
            "normalized_task": "Find more examples of Alliteration from the poem.",
            "shared_context": (
                "Alliteration repeats the same initial consonant sound in "
                "nearby words."
            ),
            "requires_context": True,
            "requires_visual": False,
            "image_urls": [],
            "content_objects": {},
        }],
        "stats": {"total_inventory_items": 1},
        "source_contract": {
            "mode": "acsd-phase2-source-critical",
            "source_sha256": "a" * 64,
        },
    }


def _valid_fixer(_payload):
    # Deliberately put incorrect public Example prose in the model response.
    # The existing focused-delta validator must restore the canonical rendered
    # inventory wording before anything can ship.
    return {
        "types": [{
            "type_id": "TYPE-FIXER-0001",
            "type_title": "Recognising Alliteration in a Poem",
            "type_description": (
                "Identify repeated initial consonant sounds in nearby words."
            ),
            "task_pattern": (
                "Given a poem, find additional examples of alliteration."
            ),
            "source_question_ids": ["QINV-0001"],
            "case_prompts": [{
                "case_id": "CASE-FIXER-0001",
                "case_title": "Finding additional alliteration examples",
                "examples": [{
                    "source_question_id": "QINV-0001",
                    "example_prompt": "MODEL MUST NOT OWN THIS WORDING",
                }],
            }],
        }],
        "rationale": (
            "The learner applies the taught sound pattern to locate another "
            "instance in the poem."
        ),
    }


def test_residual_coverage_uses_recorded_fixer_and_restores_source_wording(
    monkeypatch,
):
    contract.install()
    store = kernel.DecisionStore()
    monkeypatch.setattr(fixer_mod, "default_provider", lambda: _valid_fixer)
    monkeypatch.setattr(g, "_phase3_fixer_store", lambda: store)

    inventory = _inventory()
    recovered, count = g._append_deterministic_type_fallbacks(
        [], missed_items=inventory["items"], inventory=inventory,
    )

    assert count == 1
    assert len(recovered) == 1
    assert recovered[0]["source_question_ids"] == ["QINV-0001"]
    example = g._case_examples(recovered[0]["case_prompts"][0])[0]
    assert example["source_question_id"] == "QINV-0001"
    assert example["example_prompt"] == g._inventory_task_text(
        inventory["items"][0]
    )
    assert "MODEL MUST NOT OWN THIS WORDING" not in example["example_prompt"]
    assert "Find more examples of Alliteration from the poem." in (
        example["example_prompt"]
    )
    assert any(
        "fixer: residual Type coverage" in flag
        for flag in recovered[0].get("review_flags") or []
    )
    audit = recovered[0].get("_fixer_type_coverage") or {}
    assert audit["qids"] == ["QINV-0001"]
    assert audit["decision_key"]
    assert len(store.keys()) == 1


def test_fixer_provenance_follows_qid_through_semantic_type_merge():
    before = _valid_fixer({})["types"]
    before[0]["_fixer_type_coverage"] = {
        "decision_key": "decision-123",
        "qids": ["QINV-0001"],
        "rationale": "Residual coverage needed one recorded judgment.",
    }
    after = copy.deepcopy(before)
    after[0].pop("_fixer_type_coverage")
    after[0]["type_title"] = "Merged reusable sound-pattern task"

    carried = contract._carry_fixer_provenance(before, after)

    assert carried[0]["_fixer_type_coverage"]["decision_key"] == "decision-123"
    assert carried[0]["_fixer_type_coverage"]["qids"] == ["QINV-0001"]
    assert carried[0]["_fixer_type_coverage_history"] == [
        carried[0]["_fixer_type_coverage"]
    ]
    assert any(
        "provenance retained after semantic Type consolidation" in flag
        for flag in carried[0].get("review_flags") or []
    )


def test_no_live_fixer_never_falls_back_to_deterministic_semantics(monkeypatch):
    contract.install()
    monkeypatch.setattr(fixer_mod, "default_provider", lambda: None)

    inventory = _inventory()
    recovered, count = g._append_deterministic_type_fallbacks(
        [], missed_items=inventory["items"], inventory=inventory,
    )

    assert recovered == []
    assert count == 0


def test_fixer_must_cover_every_residual_qid_exactly_once(monkeypatch):
    contract.install()
    calls = {"n": 0}

    def incomplete(_payload):
        calls["n"] += 1
        return {"types": [], "rationale": "I did not classify the QID."}

    monkeypatch.setattr(fixer_mod, "default_provider", lambda: incomplete)
    monkeypatch.setattr(g, "_phase3_fixer_store", lambda: kernel.DecisionStore())

    inventory = _inventory()
    recovered, count = g._append_deterministic_type_fallbacks(
        [], missed_items=inventory["items"], inventory=inventory,
    )

    assert recovered == []
    assert count == 0
    assert calls["n"] == kernel.MAX_ATTEMPTS

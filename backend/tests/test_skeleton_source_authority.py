from __future__ import annotations

import copy
import json

import pytest

from app.services import generation as g


def _source_plan():
    return {"topics": [{
        "topic_id": "TOP-1", "display_name": "Unnumbered Poem",
        "concepts": [{"concept_id": "C-1", "display_name": "Returning Together"}],
    }]}


def _row():
    return {
        "topic": "Unnumbered Poem", "parent_concept": "Returning Together",
        "concept_title": "Returning Together",
        "concept_details": "Description: Opening context belongs here.\nA final limiting condition.",
        "keywords": "", "source_evidence": "BLK-1",
    }


def test_plan_and_full_skeleton_reach_author_and_independent_critic(monkeypatch):
    plan = _source_plan()
    row = _row()
    chunks = [{"text": "The complete source poem.", "sections": []}]
    meta = {"subject": "English", "instruction_slots": {
        "language_topology_plan": json.dumps({"plan": plan}),
    }}
    calls = []

    def provider(system, user, **kwargs):
        calls.append((system, user))
        assert json.dumps(plan, ensure_ascii=False) in user
        if "audit a concept-skeleton extraction" in system:
            assert row["concept_details"] in json.loads(
                user.split("EXTRACTED SKELETON (complete rows, in order):\n", 1)[1]
            )[0]["concept_details"]
            return {"coverage": "complete", "grain": "sound", "reason": "Grounded"}
        assert "use ONLY these as topics" not in user
        return {"rows": [{
            "topic": row["topic"], "parent_concept": row["parent_concept"],
            "concept": row["concept_title"], "concept_description": row["concept_details"],
            "keywords": "", "source_evidence": "BLK-1",
        }]}

    monkeypatch.setattr(g, "_openai_json", provider)
    monkeypatch.setattr(g, "_repair_records_via_api", lambda rows, **kwargs: rows)
    result = g._extract_skeleton_via_api(chunks, meta=meta)
    assert len(calls) == 2  # existing author and independent critic only
    assert result[0]["topic"] == "Unnumbered Poem"
    assert result[0]["concept_details"] == row["concept_details"]


@pytest.mark.parametrize("changed", ["plan", "prompt", "legacy"])
def test_skeleton_chunk_resume_rejects_changed_instruction_identity(monkeypatch, changed):
    chunks = [{"text": "Source with one teaching objective.", "sections": []}]
    meta = {"subject": "English", "language_topology_plan": _source_plan()}
    saved = {
        "chunk_index": 1, "chunk_count": 1,
        "chunk_sha256": g._chunk_checkpoint_sha256(chunks[0]),
        "skeleton_contract_sha256": g._skeleton_contract_sha256(meta),
        "records": [_row()],
    }
    if changed == "plan":
        meta = copy.deepcopy(meta)
        meta["language_topology_plan"]["topics"][0]["concepts"][0]["display_name"] = (
            "A different accepted objective"
        )
    elif changed == "prompt":
        previous_get = g.prompts.get_text
        monkeypatch.setattr(g.prompts, "get_text", lambda key: (
            previous_get(key) + "\nA changed author instruction."
            if key == "concepts.skeleton.system" else previous_get(key)
        ))
    else:
        saved.pop("skeleton_contract_sha256")
    calls = []

    def provider(system, user, **kwargs):
        calls.append(user)
        if "audit a concept-skeleton extraction" in system:
            return {"coverage": "complete", "grain": "sound", "reason": ""}
        return {"rows": [{
            "topic": "Unnumbered Poem", "parent_concept": "P",
            "concept": "Freshly reviewed", "concept_description": "Description: Fresh teaching.",
            "keywords": "", "source_evidence": "BLK-1",
        }]}

    monkeypatch.setattr(g, "_openai_json", provider)
    monkeypatch.setattr(g, "_repair_records_via_api", lambda rows, **kwargs: rows)
    checkpoints = []
    result = g._extract_skeleton_via_api(
        chunks, meta=meta, resume_chunks=[saved], checkpoint_callback=checkpoints.append,
    )
    assert len(calls) == 2
    assert result[0]["concept_title"] == "Freshly reviewed"
    assert checkpoints[-1]["completed_chunks"][0]["skeleton_contract_sha256"] == (
        g._skeleton_contract_sha256(meta)
    )

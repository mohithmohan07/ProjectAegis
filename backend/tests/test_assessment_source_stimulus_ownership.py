"""Question-owned table/figure evidence survives atomization and parent folding."""
from __future__ import annotations

import copy
import json

import pytest

from app.services import assessment_materialization as materialization
from app.services import assessment_source_inventory as inventory
from app.services import assessment_visual_evidence as visual
from app.services.phase3 import kernel
from tests.test_assessment_visual_evidence import _pinned
from tests.test_mes_materialization import _cell, _descriptive_response, _objective_response


TABLE = (
    r"[Katex] \begin{array}{|c|c|} \hline \text{Solid} & \text{Faces} \\\hline "
    r"\text{Cube} & 6 \\\hline \end{array} [/Katex]"
)


def _item(url):
    return {
        "qid": "QINV-0001", "raw_task": "Read the table and name the solid.",
        "shared_context": "The table belongs to this question.",
        "tables": [{"table_id": "TAB-1", "rendered_text": TABLE,
                    "cells": [[{"text": "Cube", "image_url": url}]]}],
        "content_objects": {"tables": [{"table_id": "TAB-1", "text": TABLE}]},
        "image_manifest": [{"url": url, "table_id": "TAB-1", "cell": [1, 1]}],
        "image_urls": [url], "image_assets": [{"url": url, "alt": "Source solid"}],
        "source_context": {"table_owner_qid": "QINV-0001", "text": TABLE},
        "requires_visual": True, "requires_context": True,
        "options": [{"answer_content": "Cube", "metadata": {"source_label": "a"}}],
    }


def test_atom_copies_complete_nested_stimulus_without_aliasing(monkeypatch, tmp_path):
    url, _ = _pinned(monkeypatch, tmp_path)
    item = _item(url)
    atom = inventory.source_atom_from_item(item, source_document_hash="document")
    for field in ("tables", "content_objects", "image_manifest", "image_urls", "source_context", "requires_visual", "requires_context", "options"):
        assert atom[field] == item[field]
    atom["tables"][0]["cells"][0][0]["text"] = "changed"
    atom["options"][0]["metadata"]["source_label"] = "changed"
    assert item["tables"][0]["cells"][0][0]["text"] == "Cube"
    assert item["options"][0]["metadata"]["source_label"] == "a"


def test_declared_asset_keeps_full_table_metadata_without_image_urls():
    asset = {"url": "https://example.org/table.jpg", "alt": "Complete source table",
             "table_id": "TAB-2", "relationships": [{"owner_qid": "QINV-0001"}]}
    item = {"qid": "QINV-0001", "raw_task": "Read the table.", "assets": [asset]}
    atom = inventory.source_atom_from_item(item, source_document_hash="document")
    assert atom["assets"] == [asset]
    atom["assets"][0]["relationships"][0]["owner_qid"] = "changed"
    assert asset["relationships"][0]["owner_qid"] == "QINV-0001"


def test_mapping_options_retain_cell_images_and_values_in_author_payload():
    options = {"a": {"text": "Cube", "image_url": "https://example.org/cube.jpg"},
               "b": {"text": "Cone", "table": {"rows": [["Cone", 1]]}}}
    atom = inventory.source_atom_from_item(
        {"qid": "QINV-0001", "raw_task": "Choose the solid.", "options": options},
        source_document_hash="document",
    )
    request = materialization._decision_payload(atom, _cell(), candidate_id="C1", meta={}, context={}, descriptive_answer_capacity=30)
    assert request["source_atom"]["options"] == options
    assert request["source_wording_authority"]["supporting_source"]["options"] == options
    atom["options"]["a"]["text"] = "changed"
    assert options["a"]["text"] == "Cube"


@pytest.mark.parametrize("multipart", [False, True])
def test_owned_stimulus_reaches_author_critic_and_serialized_candidate(monkeypatch, tmp_path, multipart):
    url, _ = _pinned(monkeypatch, tmp_path)
    item = _item(url)
    child = inventory.source_atom_from_item(item, source_document_hash="document")
    if multipart:
        child.update(source_qid="QINV-0001.1", parent_qid="QINV-0001", subpart="1")
        parent = {"source_qid": "QINV-0001", "raw_text": "Answer the following parts.", "assets": []}
        atoms, folded = inventory.partition_compound_parents([parent, child])
        assert len(atoms) == 1
        assert folded[0]["subpart_qids"] == ["QINV-0001.1"]
        atom = atoms[0]
        assert atom["raw_text"] == parent["raw_text"]
        assert atom["compound_subparts"] == [child]
    else:
        atom = child

    seen = []
    question = "Read the supplied table.<br>" + TABLE + '<br>[img src="' + url + '" alt="Source solid"]'

    def author(payload):
        seen.append(copy.deepcopy(payload))
        assert visual.image_inputs(payload), "The owned figure must attach as real pixels"
        evidence = payload["source_atom"]
        owned = evidence["compound_subparts"][0] if multipart else evidence
        assert owned["tables"] == item["tables"]
        assert owned["image_manifest"] == item["image_manifest"]
        if multipart:
            return _descriptive_response(payload, question=question, answers=[], sub_questions=[{
                "text": "Name the solid.", "keywords": [{"answer_type": "Phrases", "keyword": "Cube"}],
            }])
        return _objective_response(payload, question=question)

    def critic(payload):
        assert visual.image_inputs(payload)
        assert payload["source_atom"] == seen[0]["source_atom"]
        return {"verdict": "verified", "confidence": 1.0, "issues": []}

    cell = _cell(sheet_kind="descriptive", question_category="Long Answer Type (4 Marks)", marks=4) if multipart else _cell()
    candidate = materialization.materialize_candidate(
        atom, cell, meta={"subject": "Mathematics", "grade": "6"}, context={},
        envelope_sha256="e" * 64, provider=author, critic=critic, store=kernel.DecisionStore(),
    )
    snapshot = json.loads(json.dumps(candidate))
    assert snapshot["question"] == question
    assert snapshot["question_text"] == question
    assert TABLE in snapshot["question"] and url in snapshot["question"]
    if multipart:
        assert snapshot["compound_subparts"] == [child]
        assert len(snapshot["sub_questions"]) == 1
    else:
        assert snapshot["tables"] == item["tables"]
        assert snapshot["content_objects"] == item["content_objects"]


def test_nested_subparts_keep_their_exact_owner_and_do_not_mutate_inputs():
    atoms = [
        {"source_qid": "P", "raw_text": "Parent"},
        {"source_qid": "C", "parent_qid": "P", "raw_text": "Child"},
        {"source_qid": "G", "parent_qid": "C", "tables": [{"text": TABLE}]},
    ]
    original = copy.deepcopy(atoms)
    kept, _ = inventory.partition_compound_parents(atoms)
    assert len(kept) == 1
    assert kept[0]["compound_subparts"][0]["compound_subparts"][0]["tables"] == atoms[2]["tables"]
    kept[0]["compound_subparts"][0]["compound_subparts"][0]["tables"][0]["text"] = "changed"
    assert atoms == original


def test_cyclic_recorded_parent_link_is_named_instead_of_discarding_every_atom():
    with pytest.raises(inventory.SourceInventoryError, match="cyclic compound-parent"):
        inventory.partition_compound_parents([
            {"source_qid": "A", "parent_qid": "B"},
            {"source_qid": "B", "parent_qid": "A"},
        ])


@pytest.mark.parametrize("stage", ["claim", "dedup"])
def test_set_judges_and_critics_receive_complete_owned_stimulus(monkeypatch, tmp_path, stage):
    from app.services import assessment_dedup, assessment_prelearning_claim

    url, _ = _pinned(monkeypatch, tmp_path)
    child = inventory.source_atom_from_item(_item(url), source_document_hash="document")
    child.update(source_qid="QINV-0001.1", parent_qid="QINV-0001")
    raw_atoms = [
        {"source_qid": "QINV-0001", "raw_text": "Use the child's table."}, child,
        {"source_qid": "QINV-0002", "raw_text": "Use another table."},
    ]
    # The real pipeline claims before folding; dedup sees the folded parent.
    atoms = raw_atoms if stage == "claim" else inventory.partition_compound_parents(raw_atoms)[0]
    original = copy.deepcopy(atoms)
    seen = []

    def inspect(payload):
        rows = payload["source_questions" if stage == "claim" else "questions"]
        evidence = rows[0]["source_evidence"]
        assert evidence["raw_text"] == atoms[0]["raw_text"]
        if stage == "claim":
            child_evidence = rows[1]["source_evidence"]
            assert child_evidence["parent_qid"] == "QINV-0001"
            assert child_evidence["tables"] == child["tables"]
            assert rows[1]["source_qid"] == "QINV-0001.1"
        else:
            assert evidence["compound_subparts"] == [child]
        assert visual.image_inputs(payload)
        seen.append(copy.deepcopy(evidence))

    def author(payload):
        inspect(payload)
        return {"claimed" if stage == "claim" else "duplicate_sets": [], "confidence": 1.0, "rationale": "The tasks retain distinct demands."}

    def critic(payload):
        inspect(payload)
        return {"verdict": "concur", "confidence": 1.0, "issues": []}

    run = assessment_prelearning_claim.decide_pre_learning_claims if stage == "claim" else assessment_dedup.decide_source_duplicates
    kept, removed = run(atoms, meta={}, envelope_sha256="s" * 64, provider=author, critic=critic, store=kernel.DecisionStore())
    assert len(seen) == 2 and seen[0] == seen[1]
    assert not removed
    assert [atom["source_qid"] for atom in kept] == [atom["source_qid"] for atom in atoms]
    assert atoms == original

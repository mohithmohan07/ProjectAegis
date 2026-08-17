"""Container projections (app/services/containers.py) — §4 Phase 1.2.

Containers are non-destructive projections over recorded model verdicts:
inputs are never mutated, a block keeps every relationship it has (dual
roles are listed, never resolved away), and furniture is listed with what
it said (R4), from both source lanes.
"""
from __future__ import annotations

import copy
import json

from app.services import containers


def _census() -> dict:
    return {
        "census": [
            {"kind": "heading", "label": "Light", "page": 1},
            {"kind": "prose", "label": "", "page": 1},
            {"kind": "activity", "label": "Activity 11.1", "page": 2},
            {"kind": "info_hub", "label": "Do you know?", "page": 2},
            {"kind": "figure", "label": "Fig. 2", "page": 2},
            {"kind": "question", "label": "Checkpoint", "page": 3},
        ],
        "dropped_furniture": ["SCIENCE 6", "Page 14"],
    }


def _canonical() -> dict:
    return {
        "blocks": [
            {"block_id": "BLK-0001", "kind": "paragraph",
             "display_text": "Light travels in straight lines."},
            {"block_id": "BLK-0002", "kind": "figure",
             "raw_text": (
                 "![Fig. 2 - A pinhole camera.]"
                 "(https://cdn.example.com/pinhole.jpg)\n"
             )},
            {"block_id": "BLK-0003", "kind": "figure",
             "raw_text": (
                 '[img src="https://cdn.example.com/mirror.jpg" '
                 'alt="Fig. 3 - Plane mirror."]'
             )},
            {"block_id": "BLK-0004", "kind": "figure",
             "display_text": None},
        ],
        "dropped_furniture": ["NATIONALISM IN EUROPE", "23"],
    }


def _inventory() -> dict:
    return {
        "items": [
            {"qid": "QINV-0001", "source_kind": "info_hub",
             "raw_task": "Do you know? ...", "image_urls": []},
            {"qid": "QINV-0002", "source_kind": "checkpoint_question",
             "_activity_origin": True,
             "raw_task": "Observe and answer.",
             "image_urls": ["https://cdn.example.com/mirror.jpg"]},
            {"qid": "QINV-0003", "source_kind": "exercise",
             "raw_task": "Name the phenomenon.", "image_urls": []},
        ],
    }


def test_projections_are_non_destructive_and_never_mutate_inputs():
    census, canonical, inventory = _census(), _canonical(), _inventory()
    before = (
        copy.deepcopy(census),
        copy.deepcopy(canonical),
        copy.deepcopy(inventory),
    )
    projections = containers.container_projections(
        census=census, canonical=canonical, inventory=inventory
    )
    assert (census, canonical, inventory) == before

    # Container 01: the model's prose/heading verdicts + unclaimed figures.
    refs_01 = [row["ref"] for row in projections["container_01"]["census_rows"]]
    assert refs_01 == ["census:0000", "census:0001"]
    assert projections["container_01"]["figure_block_ids"] == [
        "BLK-0002", "BLK-0004",
    ]
    # Container 02: pooled hub items + activity/info_hub census verdicts +
    # the figures those items claim.
    assert projections["container_02"]["inventory_qids"] == [
        "QINV-0001", "QINV-0002",
    ]
    assert [
        row["kind"] for row in projections["container_02"]["census_rows"]
    ] == ["activity", "info_hub"]
    assert projections["container_02"]["figure_block_ids"] == ["BLK-0003"]
    # Container 03: every question identity, wherever it appears.
    assert projections["container_03"]["inventory_qids"] == [
        "QINV-0001", "QINV-0002", "QINV-0003",
    ]


def test_dual_participation_is_listed_never_resolved():
    projections = containers.container_projections(
        census=_census(), canonical=_canonical(), inventory=_inventory()
    )
    # The _activity_origin item and the info hub live in Container 02 AND
    # keep their Container-03 QID identity — sorting deletes nothing.
    assert projections["dual_roles"] == ["QINV-0001", "QINV-0002"]
    for qid in projections["dual_roles"]:
        assert qid in projections["container_02"]["inventory_qids"]
        assert qid in projections["container_03"]["inventory_qids"]


def test_furniture_lists_both_lanes_verbatim_never_a_count():
    projections = containers.container_projections(
        census=_census(), canonical=_canonical(), inventory=_inventory()
    )
    assert projections["furniture"] == {
        "chapter_reading": ["SCIENCE 6", "Page 14"],
        "acsd": ["NATIONALISM IN EUROPE", "23"],
    }


def test_figure_block_evidence_parses_urls_captions_and_claims():
    rows = containers.figure_block_evidence(_canonical(), _inventory())
    by_id = {row["block_id"]: row for row in rows}
    assert set(by_id) == {"BLK-0002", "BLK-0003", "BLK-0004"}
    assert by_id["BLK-0002"]["image_urls"] == [
        "https://cdn.example.com/pinhole.jpg"
    ]
    assert by_id["BLK-0002"]["unclaimed_image_urls"] == [
        "https://cdn.example.com/pinhole.jpg"
    ]
    assert by_id["BLK-0002"]["caption"] == "Fig. 2 - A pinhole camera"
    assert by_id["BLK-0003"]["claimed_by_qids"] == ["QINV-0002"]
    assert by_id["BLK-0003"]["unclaimed_image_urls"] == []
    # A figure block with no extractable markup carries no image evidence.
    assert by_id["BLK-0004"]["image_urls"] == []


def test_persist_and_load_roundtrip(tmp_path):
    projections = containers.persist_containers(
        tmp_path,
        census=_census(), canonical=_canonical(), inventory=_inventory(),
    )
    path = tmp_path / containers.CONTAINERS_FILENAME
    assert path.is_file()
    assert json.loads(path.read_text(encoding="utf-8")) == projections
    assert containers.load_containers(tmp_path) == projections
    assert containers.load_containers(tmp_path / "missing") is None


def test_store_containers_persists_beside_the_instruction_set(
    tmp_path, monkeypatch,
):
    from types import SimpleNamespace

    from app.services import build_concepts, canonical_source_phase2, uploads

    monkeypatch.setattr(
        canonical_source_phase2, "active_canonical", lambda: _canonical()
    )
    monkeypatch.setattr(
        uploads, "source_artifact_directory",
        lambda job_id: tmp_path / f"job-{job_id}",
        raising=False,
    )
    build_concepts._store_containers(
        SimpleNamespace(id=42),
        {
            "chapter_reading": _census(),
            "question_task_inventory": _inventory(),
        },
    )
    stored = containers.load_containers(tmp_path / "job-42")
    assert stored is not None
    assert stored["furniture"]["chapter_reading"] == ["SCIENCE 6", "Page 14"]
    assert stored["furniture"]["acsd"] == ["NATIONALISM IN EUROPE", "23"]
    assert stored["container_03"]["inventory_qids"] == [
        "QINV-0001", "QINV-0002", "QINV-0003",
    ]

"""The accepted Concept API sequence reaches Post Master source cells/rows."""
from __future__ import annotations

import copy
from pathlib import Path

import pytest

from app.bulk_import import assessment_workbook as workbook
from app.services import assessment_teaching_order as order
from app.services import assessment_release_run as run
from app.services import assessment_release_service as service
from app.services import build_concepts_release
from app.services import generation_quality_policy as quality
from tests import test_assessment_release_run as pipeline


def _release():
    return {quality.KEY: quality.VERSION, "records": [
        {"concept_title": "First taught capability", "_aegis_release_qids": ["Q9", "Q2"]},
        {"concept_title": "Later capability", "_aegis_release_qids": ["Q1"]},
    ]}


def test_atom_projection_uses_only_recorded_api_sequence_and_keeps_unknowns_once():
    atoms = [{"source_qid": qid, "raw_text": "original " + qid} for qid in ["Q1", "QX", "Q2", "Q9", "QY"]]
    original = copy.deepcopy(atoms)
    result, receipt = order.project_atoms(atoms, _release())
    assert [atom["source_qid"] for atom in result] == ["Q9", "Q2", "Q1", "QX", "QY"]
    assert receipt["unmapped_qids"] == ["QX", "QY"]
    assert result[1][order.AUDIT_FIELD]["concept_question_index"] == 1
    assert all(atom["raw_text"] == "original " + atom["source_qid"] for atom in result)
    assert all("for review" in " ".join(atom["flags"]) for atom in result[-2:])
    assert atoms == original


def test_legacy_atom_order_is_unchanged():
    atoms = [{"source_qid": "Q1"}, {"source_qid": "Q9"}]
    release = _release()
    release.pop(quality.KEY)
    assert order.project_atoms(atoms, release) == (atoms, {})


def test_ambiguous_order_identity_cannot_be_resolved_by_first_match():
    release = _release()
    release["records"][1]["_aegis_release_qids"].append("Q9")
    with pytest.raises(order.TeachingOrderError, match="repeats Q9"):
        order.project_atoms([{"source_qid": "Q9"}], release)


def test_accepted_concept_order_reaches_source_atoms_candidates_and_both_rendered_outputs(db):
    chapter = pipeline._chapter_with_concepts(db)
    job = pipeline._make_job(db, chapter)
    durable = copy.deepcopy(job.question_inventory)
    staged = durable[build_concepts_release.RELEASE_KEY]
    staged[quality.KEY] = quality.VERSION
    staged["records"][0]["_aegis_release_qids"] = ["QINV-0002", "QINV-0001"]
    staged["records"][1]["_aegis_release_qids"] = []
    # Two source MCQs on the SAME sheet expose any silent lexical/source-ID
    # resorting. The semantic test providers preserve both distinct asks.
    second = staged["question_task_inventory"]["items"][1]
    second["raw_task"] = "Which named shape occupies space in three dimensions?"
    second["options"] = ["Cube", "Circle"]
    job.question_inventory = durable
    db.commit()
    calls = {}
    authorities, _ = pipeline._authorities(db, chapter, calls=calls)
    release = run.run_release_for_job(
        db, job.id, owner_sub=pipeline.OWNER, authorities=authorities,
        **pipeline._decision_context(),
    )
    expected_qids = ["QINV-0002", "QINV-0001"]
    assert [atom["source_qid"] for atom in release.payload["source_atoms"]] == expected_qids
    assert [row["source_atom_ids"][0] for row in release.payload["candidates"]] == expected_qids
    assert release.payload["source_teaching_order"]["accepted_atom_qids"] == expected_qids
    assert [row[order.AUDIT_FIELD]["ordinal"] for row in release.payload["candidates"]] == [0, 1]
    expected_text = [second["raw_task"], "Which of these is a solid?"]
    assert [row["question"] for row in release.payload["candidates"]] == expected_text
    directory = Path(release.publication["directory"])
    master = workbook.parse_workbook((directory / service.MASTER_FILENAME).read_bytes())
    questions = [row for row in master["sheets"]["Objective"]["rows"] if row.get("question_label")]
    assert [row["question"] for row in questions] == expected_text
    assert [row["question_text"] for row in questions] == [
        stem + "<br>a) Cube<br>b) Circle" for stem in expected_text
    ]
    concept_file = workbook.parse_workbook((directory / service.CONCEPTS_FILENAME).read_bytes())
    concept_rows = concept_file["sheets"]["Objective"]["rows"]
    assert [row["concept_display_name"] for row in concept_rows] == ["Solid Shapes", "Plane Shapes"]

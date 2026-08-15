"""MES PR 2 — lossless source inventory and exact blueprint compiler.

Spec Stage 2 (bidirectional QID ledger, provenance preserved) and Stage 3
(explicit validated cells, exact totals, obligations), plus the retirement
of live round-robin placement and the implicit Cartesian generation loop.
"""
from __future__ import annotations

import pytest

from app import models
from app.services import assessment_blueprint as bp
from app.services import assessment_source_inventory as si
from app.services import build_assessments


# --------------------------------------------------------------------------- #
# Source inventory (Stage 2)
# --------------------------------------------------------------------------- #

def _inventory() -> dict:
    return {
        "items": [
            {
                "qid": "QINV-0001",
                "source_kind": "exercise",
                "source_label": "Exercise 1(2)",
                "topic_hint": "Solid Shapes",
                "page_hint": 8,
                "raw_task": "Look at the figure and name the solid.",
                "polished_task": (
                    "The illustration provided shows a solid. Name it."),
                "raw_solution_or_answer": "",
                "shared_context": "",
                "options": [],
                "image_urls": ["https://x/source-assets/fig1.png"],
                "image_assets": [{
                    "url": "https://x/source-assets/fig1.png",
                    "alt": "a cone beside a cube",
                    "sha256": "abc123",
                    "source_page": 8,
                }],
            },
            {
                "qid": "QINV-0022.4",
                "parent_qid": "QINV-0022",
                "source_kind": "exercise_question",
                "source_label": "Exercise 1(2) clue 4",
                "raw_task": "Fourth clue subquestion.",
                "options": ["a", "b"],
            },
        ],
        "mined_types": {
            "placement_certifications": {
                "hosts": {
                    "QINV-0001": {"basis": "critic_verified",
                                  "concept": "Solid Shapes"},
                },
            },
        },
    }


def test_source_atoms_preserve_provenance_and_ledger():
    built = si.build_source_atoms(
        _inventory(), source_document_hash="sha256:doc")
    atoms = built["atoms"]
    assert [a["source_qid"] for a in atoms] == ["QINV-0001", "QINV-0022.4"]
    assert built["ledger"] == {"QINV-0001": 0, "QINV-0022.4": 1}

    first = atoms[0]
    assert first["source_document_hash"] == "sha256:doc"
    assert first["source_paper_number"] == "Exercise 1(2)"
    assert first["page"] == 8
    # Public wording prefers the polished form; raw text is preserved beside it.
    assert first["normalized_public_text"].startswith("The illustration")
    assert first["raw_text"] == "Look at the figure and name the solid."
    # Ordered assets carry url, alt, sha, page, and order.
    assert first["assets"] == [{
        "source_page": 8, "bbox": None, "sha256": "abc123",
        "url": "https://x/source-assets/fig1.png",
        "alt": "a cone beside a cube", "order": 1,
    }]
    # Type/Case route evidence rides along verbatim.
    assert first["route_evidence"]["basis"] == "critic_verified"

    subpart = atoms[1]
    assert subpart["parent_qid"] == "QINV-0022"
    assert subpart["subpart"] == "4"
    assert subpart["options"] == ["a", "b"]


def test_source_atoms_fail_closed_on_identity_loss():
    with pytest.raises(si.SourceInventoryError, match="without a QID"):
        si.build_source_atoms({"items": [{"raw_task": "orphan"}]})
    with pytest.raises(si.SourceInventoryError, match="duplicate"):
        si.build_source_atoms({"items": [
            {"qid": "QINV-0001", "raw_task": "a"},
            {"qid": "QINV-0001", "raw_task": "b"},
        ]})


def test_source_atoms_hash_is_stable():
    a = si.build_source_atoms(_inventory(), source_document_hash="sha256:doc")
    b = si.build_source_atoms(_inventory(), source_document_hash="sha256:doc")
    assert a["sha256"] == b["sha256"]


# --------------------------------------------------------------------------- #
# Blueprint compiler (Stage 3)
# --------------------------------------------------------------------------- #

class _Batch:
    def __init__(self, **kw):
        self.cognitive_skills = kw.get("cognitive_skills", ["Understand"])
        self.difficulty_levels = kw.get("difficulty_levels", ["Moderate"])
        self.categories = kw.get("categories", ["Multiple Choice Question"])
        self.question_type = kw.get("question_type", "objective")
        self.num_questions = kw.get("num_questions", 2)
        self.appears_in = kw.get("appears_in", ["Worksheet"])


class _Concept:
    def __init__(self, concept_id):
        self.id = concept_id


def test_batches_compile_to_exact_stamped_cells():
    batches = [_Batch(
        cognitive_skills=["Remember", "Apply"],
        difficulty_levels=["Less", "High"],
        categories=["Multiple Choice Question"],
        num_questions=3,
    )]
    concepts = [_Concept(11), _Concept(12)]
    cells = bp.compile_cells_from_batches(
        batches, concepts=concepts, default_marks={"objective": 1.0})
    # 2 skills x 2 difficulties x 1 category x 2 concepts = 8 explicit cells.
    assert len(cells) == 8
    assert len({c["cell_id"] for c in cells}) == 8
    bp.validate_cells(cells)
    exact = bp.totals(cells)
    assert exact["total_questions"] == 24
    assert exact["total_marks"] == 24.0
    assert exact["by_difficulty"] == {"Less": 12, "High": 12}
    assert exact["by_cognitive_skill"] == {"Remember": 12, "Apply": 12}
    # One obligation per required question instance, cell-scoped.
    obligations = bp.obligations(cells)
    assert len(obligations) == 24
    assert obligations[0].endswith("#1")
    # Same inputs -> same cell ids (stable identity for caching/audit).
    again = bp.compile_cells_from_batches(
        batches, concepts=concepts, default_marks={"objective": 1.0})
    assert [c["cell_id"] for c in again] == [c["cell_id"] for c in cells]


def test_blueprint_validation_names_defects():
    cells = bp.compile_cells_from_batches(
        [_Batch(question_type="bogus")], concepts=[_Concept(1)])
    with pytest.raises(bp.BlueprintError, match="sheet_kind"):
        bp.validate_cells(cells)


def test_mes_profile_rejects_subjective_cells():
    cells = bp.compile_cells_from_batches(
        [_Batch(question_type="subjective")], concepts=[_Concept(1)],
        default_marks={"subjective": 3.0}, mes_profile=True)
    with pytest.raises(bp.BlueprintError, match="sheet_kind"):
        bp.validate_cells(cells, mes_profile=True)
    # The same cells are legal for the legacy concept-mapping path.
    bp.validate_cells(cells)


# --------------------------------------------------------------------------- #
# Round-robin retirement (upload path)
# --------------------------------------------------------------------------- #

def test_sole_scope_placement_is_mechanical():
    records = [{"question": "Q1"}, {"question": "Q2"}]
    positions, basis = build_assessments._route_uploaded_questions(
        records, [_Concept(7)])
    assert positions == [0, 0]
    assert basis == "sole_scope_concept"


def test_live_routing_is_a_model_judgment(monkeypatch):
    monkeypatch.setattr(
        build_assessments.config, "use_live_generation", lambda: True)
    calls = {}

    def fake_openai(system, user, **kw):
        calls["system"] = system
        # Deliberately NOT round-robin: both questions belong to concept 22.
        return {"placements": [
            {"index": 0, "concept_id": 22},
            {"index": 1, "concept_id": 22},
        ]}

    monkeypatch.setattr(
        build_assessments.generation, "_openai_json", fake_openai)
    concepts = [_ModelConcept(21, "Tangents"), _ModelConcept(22, "Chords")]
    positions, basis = build_assessments._route_uploaded_questions(
        [{"question": "Q1"}, {"question": "Q2"}], concepts)
    assert positions == [1, 1]
    assert basis == "api_router_v0"
    assert "never by position" in calls["system"]


def test_live_routing_fails_closed_on_partial_placement(monkeypatch):
    monkeypatch.setattr(
        build_assessments.config, "use_live_generation", lambda: True)
    monkeypatch.setattr(
        build_assessments.generation, "_openai_json",
        lambda *a, **kw: {"placements": [{"index": 0, "concept_id": 21}]})
    concepts = [_ModelConcept(21, "Tangents"), _ModelConcept(22, "Chords")]
    with pytest.raises(RuntimeError, match="stopping instead of guessing"):
        build_assessments._route_uploaded_questions(
            [{"question": "Q1"}, {"question": "Q2"}], concepts)


class _ModelConcept:
    def __init__(self, concept_id, title):
        self.id = concept_id
        self.concept_title = title
        self.concept_details = f"Description: teaches {title}."


# --------------------------------------------------------------------------- #
# Cartesian retirement (concept-mapping path)
# --------------------------------------------------------------------------- #

def test_generated_questions_are_stamped_with_their_cell(client, first_concept):
    session = client.post("/build-assessments/sessions", json={
        "scope_type": "concept", "scope_ids": [first_concept["id"]],
    }).json()
    client.post(f"/build-assessments/sessions/{session['id']}/batches", json={
        "cognitive_skills": ["Remember"], "difficulty_levels": ["Less"],
        "categories": ["Multiple Choice Question"],
        "question_type": "objective", "num_questions": 1,
    })
    from tests.conftest import stream_result

    result = stream_result(client.post(
        f"/build-assessments/sessions/{session['id']}/generate"))
    assert result["created"] == 1
    from app.db import SessionLocal

    db = SessionLocal()
    try:
        question = db.get(models.Question, result["question_ids"][0])
        assert question.blueprint_cell_id.startswith("CELL-")
    finally:
        db.close()


def test_the_generation_loop_no_longer_uses_itertools_product():
    from pathlib import Path

    source = Path(build_assessments.__file__).read_text(encoding="utf-8")
    assert "from itertools import product" not in source
    # The only modulo placement left is the labeled dry-run fixture inside
    # the router; live paths never reach it.
    assert source.count("% len(concepts)") == 1
    assert "dry_fixture" in source

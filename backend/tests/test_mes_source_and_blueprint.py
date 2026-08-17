"""MES PR 2 — lossless source inventory and exact blueprint compiler.

Spec Stage 2 (bidirectional QID ledger, provenance preserved) and Stage 3
(explicit validated cells, exact totals, obligations), plus the retirement
of live round-robin placement and the implicit Cartesian generation loop.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import models
from app.services import assessment_blueprint as bp
from app.services import assessment_source_inventory as si
from app.services import build_assessments
from app.services.phase3 import kernel


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


@pytest.mark.parametrize(
    "invalid_marks",
    [
        pytest.param(float("nan"), id="nan"),
        pytest.param(float("inf"), id="positive-infinity"),
        pytest.param(float("-inf"), id="negative-infinity"),
    ],
)
def test_blueprint_validation_rejects_nonfinite_marks(invalid_marks: float):
    cells = bp.compile_cells_from_batches(
        [_Batch()], concepts=[_Concept(1)],
        default_marks={"objective": 1.0},
    )
    cells[0]["marks"] = invalid_marks

    with pytest.raises(
        bp.BlueprintError, match="marks must be finite and positive"
    ):
        bp.validate_cells(cells)


def test_strict_profile_rejects_subjective_cells():
    cells = bp.compile_cells_from_batches(
        [_Batch(question_type="subjective")], concepts=[_Concept(1)],
        default_marks={"subjective": 3.0}, strict_profile=True)
    with pytest.raises(bp.BlueprintError, match="sheet_kind"):
        bp.validate_cells(cells, strict_profile=True)
    # The same cells are legal for the legacy concept-mapping path.
    bp.validate_cells(cells)


# --------------------------------------------------------------------------- #
# Round-robin retirement (upload path)
# --------------------------------------------------------------------------- #

def test_sole_scope_placement_is_mechanical(tmp_path):
    records = [{"question": "Q1"}, {"question": "Q2"}]
    forbidden = lambda _payload: pytest.fail("mechanical route spent authority")
    placements = build_assessments._route_uploaded_questions(
        records,
        [_ModelConcept(7, "Tangents")],
        candidate_ids=["UP-1", "UP-2"],
        meta={"subject": "Mathematics"},
        envelope_sha256="envelope",
        source_concept_release_sha256="concept-release",
        store=kernel.DecisionStore(tmp_path / "decisions"),
        provider=forbidden,
        critic=forbidden,
        fixer=forbidden,
    )
    assert [placement["concept_key"] for placement in placements] == [
        "db:7", "db:7"]
    assert all(placement["basis"] == "sole_candidate" for placement in placements)


def test_multi_concept_routing_is_a_kernel_judgment(tmp_path):
    calls = []

    def provider(payload):
        calls.append(payload)
        # Deliberately NOT round-robin: both questions belong to concept 22.
        return {
            "candidate_id": payload["candidate"]["candidate_id"],
            "concept_key": "db:22",
            "evidence": "The complete Chords description teaches this.",
            "rationale": "Both candidates assess the same released concept.",
        }

    critic = lambda _payload: {
        "verdict": "verified", "confidence": 1.0, "issues": []}
    concepts = [_ModelConcept(21, "Tangents"), _ModelConcept(22, "Chords")]
    placements = build_assessments._route_uploaded_questions(
        [
            {"question": "Q1", "question_text": "Full evidence one"},
            {"question": "Q2", "question_text": "Full evidence two"},
        ],
        concepts,
        candidate_ids=["UP-1", "UP-2"],
        meta={"subject": "Mathematics"},
        envelope_sha256="envelope",
        source_concept_release_sha256="concept-release",
        store=kernel.DecisionStore(tmp_path / "decisions"),
        provider=provider,
        critic=critic,
    )
    assert [placement["concept_key"] for placement in placements] == [
        "db:22", "db:22"]
    assert all(placement["basis"] == "api_router" for placement in placements)
    assert [call["candidate"]["question_text"] for call in calls] == [
        "Full evidence one", "Full evidence two"]


def test_multi_concept_routing_requires_sealed_concept_snapshot(tmp_path):
    concepts = [_ModelConcept(21, "Tangents"), _ModelConcept(22, "Chords")]
    with pytest.raises(ValueError, match="sealed source concept release hash"):
        build_assessments._route_uploaded_questions(
            [{"question": "Q1"}],
            concepts,
            candidate_ids=["UP-1"],
            meta={"subject": "Mathematics"},
            envelope_sha256="envelope",
            source_concept_release_sha256="",
            store=kernel.DecisionStore(tmp_path / "decisions"),
            provider=lambda payload: payload,
        )


class _ModelConcept:
    def __init__(self, concept_id, title):
        self.id = concept_id
        self.concept_title = title
        self.concept_display_name = title
        self.concept_details = f"Description: teaches {title}."
        chapter = SimpleNamespace(
            chapter_code="10CBMA_GEO",
            chapter_title="Geometry",
            subject="Mathematics",
            board="CBSE",
            grade="10",
            topics=[],
        )
        topic = SimpleNamespace(
            id=1,
            topic_title="Circles",
            chapter=chapter,
        )
        chapter.topics = [topic]
        self.topic = topic


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
    assert "% len(concepts)" not in source
    assert "dry_fixture" not in source
    assert "assessment_routing.route_candidates" in source

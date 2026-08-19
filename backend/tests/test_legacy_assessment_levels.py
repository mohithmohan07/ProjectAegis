"""Legacy Build Assessments consumes recorded levels without local defaults."""
from __future__ import annotations

import copy
from pathlib import Path

import pytest

from app import models
from app.services import assessment_grouping as grouping
from app.services import build_assessments
from app.services import identity
from app.services import post_generation
from app.services.phase3 import kernel


ENVELOPE_SHA256 = "d" * 64
PRODUCTION_LEVEL_AUTHORITIES = build_assessments._legacy_level_authorities


def _new_concept(db, *, display_name: str = "Recorded level concept"):
    topic = db.query(models.Topic).order_by(models.Topic.id).first()
    assert topic is not None
    concept = models.Concept(
        topic_id=topic.id,
        concept_title="(RECORDED-LEVEL) Internal title",
        concept_display_name=display_name,
        concept_details="Description: evidence for a recorded level verdict.",
    )
    db.add(concept)
    db.flush()
    return concept


def _record() -> dict:
    return {
        "sheet_kind": "descriptive",
        "question_category": "Long Answer",
        "cognitive_skills": "Understand",
        "level_of_difficulty": "Less",
        "question": "Explain the recorded concept.",
        "question_text": "Explain the recorded concept.",
        "marks": 3,
        "display_answer": "A complete explanation.",
        "answers": [
            {
                "answer_type": "Phrases",
                "answer_content": "Explains the concept.",
                "answer_weightage": "3",
            }
        ],
        "sub_questions": [],
        "answer_explanation": "",
    }


def test_group_selection_accepts_only_a_recorded_tier(db) -> None:
    concept = _new_concept(db)

    with pytest.raises(ValueError, match="recorded assessment tier"):
        build_assessments._group_for_recorded_tier(db, concept, "Less")

    group = build_assessments._group_for_recorded_tier(
        db, concept, "Advanced"
    )
    assert group.group_type == "Advanced"
    assert group.group_name == group.group_display_name == (
        "Recorded level concept — Advanced"
    )
    assert group.group_key == grouping.group_key_for(
        identity.machine_id_for_concept(concept), "Advanced", 1
    )
    assert group.group_sequence == 1
    db.rollback()


def test_group_selection_never_chooses_first_same_tier_variant(db) -> None:
    concept = _new_concept(db)
    for sequence in (1, 2):
        db.add(models.Group(
            concept_id=concept.id,
            group_type="Basic",
            group_key=grouping.group_key_for(
                identity.machine_id_for_concept(concept),
                "Basic",
                sequence,
            ),
            group_sequence=sequence,
            group_name="Recorded level concept — Basic",
            group_display_name="Recorded level concept — Basic",
        ))
    db.flush()
    db.refresh(concept)

    with pytest.raises(ValueError, match="multiple Basic variant groups"):
        build_assessments._group_for_recorded_tier(db, concept, "Basic")
    db.rollback()


def test_group_selection_records_missing_legacy_machine_identity(db) -> None:
    concept = _new_concept(db)
    legacy = models.Group(
        concept_id=concept.id,
        group_type="Intermediate",
        group_key="",
        group_sequence=0,
        group_name="Old visible value",
        group_display_name="Old visible value",
    )
    db.add(legacy)
    db.flush()
    db.refresh(concept)

    selected = build_assessments._group_for_recorded_tier(
        db, concept, "Intermediate"
    )

    assert selected.id == legacy.id
    assert selected.group_key == grouping.group_key_for(
        identity.machine_id_for_concept(concept), "Intermediate", 1
    )
    assert selected.group_sequence == 1
    assert selected.group_name == selected.group_display_name == (
        "Recorded level concept — Intermediate"
    )
    db.rollback()


def test_group_selection_requires_explicit_concept_display_name(db) -> None:
    concept = _new_concept(db, display_name="")

    with pytest.raises(grouping.GroupingError, match="display name"):
        build_assessments._group_for_recorded_tier(db, concept, "Basic")
    db.rollback()


def test_legacy_level_uses_recorded_kernel_verdict_not_difficulty(db) -> None:
    concept = _new_concept(db)
    provider_payloads = []
    critic_payloads = []

    def provider(payload: dict) -> dict:
        provider_payloads.append(copy.deepcopy(payload))
        return {
            "candidate_id": payload["candidate"]["candidate_id"],
            "tier": "Advanced",
            "rationale": "The complete response contract supports this tier.",
        }

    def critic(payload: dict) -> dict:
        critic_payloads.append(copy.deepcopy(payload))
        return {"verdict": "verified", "confidence": 1.0, "issues": []}

    record = _record()
    tiers = build_assessments._recorded_tiers(
        [("LEGACY-1", record, concept)],
        envelope_sha256=ENVELOPE_SHA256,
        store=kernel.DecisionStore(),
        provider=provider,
        critic=critic,
    )

    assert record["level_of_difficulty"] == "Less"
    assert tiers == {"LEGACY-1": "Advanced"}
    assert len(provider_payloads) == len(critic_payloads) == 1
    assert "difficulty" not in provider_payloads[0]["candidate"]
    assert "cognitive_skill" not in provider_payloads[0]["candidate"]
    db.rollback()


def test_legacy_level_replays_without_authority_calls(db, tmp_path) -> None:
    concept = _new_concept(db)
    store = kernel.DecisionStore(tmp_path / "decisions")
    calls = {"provider": 0, "critic": 0}

    def provider(payload: dict) -> dict:
        calls["provider"] += 1
        return {
            "candidate_id": payload["candidate"]["candidate_id"],
            "tier": "Basic",
            "rationale": "Recorded fixture verdict.",
        }

    def critic(_payload: dict) -> dict:
        calls["critic"] += 1
        return {"verdict": "verified", "confidence": 1.0, "issues": []}

    unit = [("LEGACY-REPLAY", _record(), concept)]
    first = build_assessments._recorded_tiers(
        unit,
        envelope_sha256=ENVELOPE_SHA256,
        store=store,
        provider=provider,
        critic=critic,
    )

    def forbidden(_payload: dict) -> dict:
        raise AssertionError("cached level decision called an authority")

    second = build_assessments._recorded_tiers(
        unit,
        envelope_sha256=ENVELOPE_SHA256,
        store=store,
        provider=forbidden,
        critic=forbidden,
        fixer=forbidden,
    )

    assert first == second == {"LEGACY-REPLAY": "Basic"}
    assert calls == {"provider": 1, "critic": 1}
    db.rollback()


def test_production_legacy_levels_have_no_local_authority() -> None:
    assert PRODUCTION_LEVEL_AUTHORITIES() == (None, None, None)

    source = Path(build_assessments.__file__).read_text(encoding="utf-8")
    assert "DIFFICULTY_TO_GROUP" not in source
    assert "def _group_for(" not in source
    assert "_dry_recorded_level" not in source
    assert '] or ["Understand"]' not in source
    assert '] or ["Moderate"]' not in source
    assert 'categories=categories or ["Multiple Choice Question"]' not in source
    assert 'kind="assessment.legacy_cell_contract"' in source
    assert (
        '_LEGACY_CELL_MARK_POLICY_VERSION = '
        '"assessment-legacy-cell-contract-2"'
    ) in source
    assert 'kind="assessment.cell"' not in source


def test_legacy_cell_contract_redecides_under_its_distinct_kind(db) -> None:
    concept = _new_concept(db)
    cell = {
        "cell_id": "CELL-LEGACY-CONTRACT",
        "sheet_kind": "objective",
        "question_category": "Multiple Choice Question",
        "cognitive_skill": "Understand",
        "difficulty": "Moderate",
        "count": 1,
        "appears_in": ["Pre/Post-Worksheet/Test"],
        "concept_id": concept.id,
        "source_policy": "generate",
    }
    meta = {"subject": "Science", "grade": "6"}
    blueprint_cell = {
        key: cell.get(key)
        for key in (
            "cell_id", "sheet_kind", "question_category",
            "cognitive_skill", "difficulty", "count", "appears_in",
            "concept_id", "source_policy",
        )
    }
    payload = {
        "stage": "assessment.legacy-cell-mark",
        "rules": build_assessments._LEGACY_CELL_MARK_SYSTEM,
        "critic_rules": build_assessments._LEGACY_CELL_MARK_CRITIC_SYSTEM,
        "metadata": dict(meta),
        "blueprint_cell": blueprint_cell,
        "concept": build_assessments._legacy_concept(concept),
    }
    response = {
        "cell_id": cell["cell_id"],
        "marks": 1,
        "question_duration": 2,
        "math_keyboard": "",
        "rationale": "Recorded fixture cell contract.",
    }
    store = kernel.DecisionStore()
    old_calls = []

    def old_provider(_request: dict) -> dict:
        old_calls.append(1)
        return dict(response)

    old_decision = kernel.decide(
        kind="assessment.cell",
        unit_id=cell["cell_id"],
        envelope_sha256=ENVELOPE_SHA256,
        payload=payload,
        provider=old_provider,
        checker=build_assessments._legacy_cell_mark_checker(
            cell["cell_id"], cell["sheet_kind"]
        ),
        store=store,
        policy_version="assessment-legacy-cell-contract-1",
    )
    assert old_calls == [1]

    new_calls = []

    def new_provider(_request: dict) -> dict:
        new_calls.append(1)
        return dict(response)

    decided = build_assessments._recorded_cell_marks(
        [cell],
        concepts_by_id={concept.id: concept},
        meta=meta,
        envelope_sha256=ENVELOPE_SHA256,
        store=store,
        provider=new_provider,
    )

    assert new_calls == [1]
    assert len(store.keys()) == 2
    authority = decided[cell["cell_id"]]["authority"]
    assert authority["policy_version"] == "assessment-legacy-cell-contract-2"
    assert authority["decision_key"] != old_decision["key"]
    new_record = store.get(authority["decision_key"])
    assert new_record is not None
    assert new_record["kind"] == "assessment.legacy_cell_contract"
    db.rollback()


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("cognitive_skills", [], "cognitive skill"),
        ("difficulty_levels", [], "difficulty level"),
        ("categories", [], "question category"),
        ("num_questions", 0, "at least 1"),
    ],
)
def test_legacy_blueprint_refuses_unselected_axes(
    db, field: str, value, message: str,
) -> None:
    concept = db.query(models.Concept).order_by(models.Concept.id).first()
    assert concept is not None
    session = build_assessments.create_session(db, "concept", [concept.id])
    arguments = {
        "cognitive_skills": ["Understand"],
        "difficulty_levels": ["Moderate"],
        "categories": ["Long Answer"],
        "question_type": "descriptive",
        "num_questions": 1,
    }
    arguments[field] = value

    with pytest.raises(ValueError, match=message):
        build_assessments.add_batch(db, session.id, **arguments)


@pytest.mark.parametrize(
    ("skills", "difficulty", "expected"),
    [
        ("", "Less", "cognitive-skill"),
        ("Understand", "", "difficulty"),
    ],
)
def test_post_generation_refuses_missing_semantic_verdicts(
    db, skills: str, difficulty: str, expected: str,
) -> None:
    question = models.Question(
        question_label="LEGACY-MISSING",
        cognitive_skills=skills,
        level_of_difficulty=difficulty,
    )

    with pytest.raises(ValueError, match=expected):
        post_generation.column_mapping(db, [question])


def test_post_generation_source_has_no_semantic_blank_defaults() -> None:
    source = Path(post_generation.__file__).read_text(encoding="utf-8")
    assert 'normalize_cognitive_skills(q.cognitive_skills) or "Understand"' not in source
    assert 'q.level_of_difficulty = "Moderate"' not in source


@pytest.mark.parametrize(
    ("sheet_kind", "duration", "math_keyboard", "message"),
    [
        ("objective", 0.0, "", "finite positive duration"),
        ("subjective", 1.0, "", "math-keyboard verdict"),
    ],
)
def test_post_generation_refuses_missing_recorded_runtime_contract(
    db, sheet_kind: str, duration: float, math_keyboard: str, message: str,
) -> None:
    question = models.Question(
        question_label="LEGACY-RUNTIME-CONTRACT",
        sheet_kind=sheet_kind,
        cognitive_skills="Understand",
        level_of_difficulty="Moderate",
        question_duration=duration,
        math_keyboard=math_keyboard,
    )

    with pytest.raises(ValueError, match=message):
        post_generation.column_mapping(db, [question])


def test_question_kwargs_requires_recorded_marks_and_duration() -> None:
    base = {
        "sheet_kind": "objective",
        "question_category": "Multiple Choice Question",
        "cognitive_skills": "Understand",
        "level_of_difficulty": "Moderate",
        "marks": 1,
        "question_duration": 2,
        "math_keyboard": "",
    }
    for missing in ("marks", "question_duration"):
        record = dict(base)
        record.pop(missing)
        with pytest.raises(ValueError, match=missing):
            build_assessments._question_kwargs(record)

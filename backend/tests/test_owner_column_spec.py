"""Owner column rules survive authoring, freeze, and XLSX read-back."""
from __future__ import annotations

import copy
from decimal import Decimal

import pytest

from app.bulk_import import assessment_workbook as aw
from app.bulk_import import writer
from app.services import assessment_materialization as materialize
from app.services import assessment_marking as marking
from app.services import assessment_profile as profiles
from app.services import assessment_release as release
from app.services import assessment_release_service as service
from app.services import column_spec
from app.services.phase3 import kernel
from tests.test_mes_materialization import _atom, _cell, _objective_response
from tests.test_assessment_master_refiner import _payload
from tests.test_assessment_release_run import _chapter_with_concepts


def _profile(subject="English"):
    return profiles.resolve_for_metadata(None, {"subject": subject, "grade": "06"})


def test_subject_adapter_is_bound_to_metadata_and_legacy_profiles_stay_legacy():
    english = _profile()
    assert english[column_spec.POLICY_KEY]["subject_adapter"] == "english"
    assert profiles.resolve_for_metadata(english, {}) == english
    legacy = copy.deepcopy(english)
    legacy.pop(column_spec.POLICY_KEY)
    assert column_spec.POLICY_KEY not in profiles.resolve_for_metadata(legacy, {})
    assert _profile("Science")[column_spec.POLICY_KEY]["subject_adapter"] == "universal"
    assert _profile("Mathematics")[column_spec.POLICY_KEY]["subject_adapter"] == "universal"
    with pytest.raises(ValueError, match="cannot retarget"):
        profiles.resolve_for_metadata(english, {"subject": "Mathematics"})


@pytest.mark.parametrize("subject", ["English", "Mathematics", "Science", "History", "Geography"])
def test_materialization_retries_wrong_prefix_and_receives_current_rules(subject):
    calls = []

    def author(request):
        calls.append(request)
        prefix = "b) " if len(calls) == 1 else "a) "
        return _objective_response(request, answer_explanation=prefix + "Cone. It has a curved face and an apex.")

    result = materialize.materialize_candidate(
        _atom(), _cell(), meta={"subject": subject, "grade": "06"},
        envelope_sha256="e" * 64, provider=author, store=kernel.DecisionStore(),
    )
    assert len(calls) == 2
    assert calls[0]["column_spec_policy"]["version"] == column_spec.VERSION
    assert calls[0]["rubric_tag_policy"]["tags"] == (list(column_spec.ENGLISH_TAGS) if subject == "English" else [])
    assert result["answer_explanation"].startswith("a) Cone.")


@pytest.mark.parametrize("value,valid", [
    ("0.5", True), ("1.5", True), ("2.50", True), ("1E+50", True),
    ("0.05", False), ("1.25", False), ("-0.5", False), ("0", False),
    ("NaN", False), ("Infinity", False),
])
def test_half_mark_rule_handles_exact_decimal_values(value, valid):
    assert column_spec.half_step(Decimal(value)) is valid


def test_marking_accepts_two_half_step_awards_but_never_wrong_arithmetic():
    response = {"answers": [{"answer_weightage": "2.5"}, {"answer_weightage": "1.5"}], "sub_questions": []}
    assert marking._weight_defects(response, kind="descriptive", total_marks=Decimal(4), marks_rule={}, half_step=True) == []
    assert marking._weight_defects(response, kind="descriptive", total_marks=Decimal(4), marks_rule={}, half_step=False)
    response["answers"][1]["answer_weightage"] = "1"
    assert any("sum exactly" in error for error in marking._weight_defects(
        response, kind="descriptive", total_marks=Decimal(4), marks_rule={}, half_step=True,
    ))


def test_new_english_tags_are_criterion_only_and_legacy_tags_remain_versioned():
    assert not release.malformed_rubric_tag("[creativity]: Uses an original, relevant ending.", tags_required=True, allowed_tags=column_spec.ENGLISH_TAGS)
    assert release.malformed_rubric_tag("[creative]: Uses an original, relevant ending.", tags_required=True, allowed_tags=column_spec.ENGLISH_TAGS)
    assert not release.malformed_rubric_tag("[creativity]: Uses an original, relevant ending.", tags_required=True)
    assert release.malformed_rubric_tag("[creative]: Uses an original ending.", tags_required=False)
    assert release.rubric_tag_leaks("[creative]: An ending for the learner.")


def _english_snapshot(subject="English"):
    payload = _payload()
    for group in payload["groups"]:
        group["group_name"] = group["group_display_name"] = group["group_key"]
    for candidate in payload["candidates"]:
        if candidate["sheet_kind"] == "objective":
            candidate["answer_explanation"] = "a) " + candidate["answer_explanation"]
        for child in candidate.get("sub_questions", []):
            for criterion in child["keywords"]:
                if subject == "English":
                    criterion["keyword"] = "[content]: " + criterion["keyword"]
    snapshot = service.snapshot_from_staged_release(payload)
    for topic in snapshot["topics"]:
        for concept in topic["concepts"]:
            concept["keywords"] = "shape | dimension | comparison"
    return snapshot


@pytest.mark.parametrize("subject", ["English", "Mathematics", "Science", "History", "Geography"])
def test_both_snapshot_exports_use_universal_keywords_and_master_detects_corruption(subject):
    snapshot = _english_snapshot(subject)
    profile = _profile(subject)
    concept = aw.parse_workbook(aw.render_concept_file(snapshot, profile))
    master_bytes, manifest = aw.render_master_file(snapshot, profile)
    master = aw.parse_workbook(master_bytes)
    assert aw.validate_concept_file(concept, snapshot, profile) == []
    assert aw.validate_master_file(master, snapshot, profile, group_provenance=manifest["group_provenance"]) == []
    assert concept["sheets"]["Objective"]["rows"][0]["keywords"] == "shape, dimension, comparison"
    row = master["sheets"]["Objective"]["rows"][0]
    assert row["keywords"] == "shape, dimension, comparison"
    assert row["answer_explanation"].startswith("a) Cube.")
    assert isinstance(row["answer_weightage_1"], (int, float))
    assert row["correct_answer_1"] == "Yes"
    concept["sheets"]["Objective"]["rows"][0]["keywords"] = "shape | dimension"
    assert any("keywords must use comma-space" in error for error in aw.validate_concept_file(concept, snapshot, profile))
    row["keywords"] = "shape | dimension"
    assert any("keywords must use comma-space" in error for error in aw.validate_master_file(master, snapshot, profile))
    row["answer_explanation"] = "b) Cube. The wrong label must not pass."
    assert any("option label" in error for error in aw.validate_master_file(
        master, snapshot, profile, group_provenance=manifest["group_provenance"],
    ))


@pytest.mark.parametrize("subject", ["English", "Science", "History", "Geography"])
def test_db_concept_export_has_same_keyword_projection(db, subject):
    chapter = _chapter_with_concepts(db)
    concepts = [concept for topic in chapter.topics for concept in topic.concepts]
    original_subject = chapter.subject
    original_keywords = {concept.id: concept.keywords for concept in concepts}
    try:
        chapter.subject = subject
        for concept in concepts:
            concept.keywords = "decision | responsibility | self-help"
        db.commit()
        parsed = aw.parse_workbook(writer.write_concepts_workbook(
            db, [concept.id for concept in concepts], layout_id=writer.CONCEPT_FILE_LAYOUT_ID,
        ))
        assert {row["keywords"] for row in parsed["sheets"]["Objective"]["rows"]} == {"decision, responsibility, self-help"}
    finally:
        chapter.subject = original_subject
        for concept in concepts:
            concept.keywords = original_keywords[concept.id]
        db.commit()


def test_keyboard_is_response_driven_and_closed_lane_restriction_is_checked_at_freeze():
    snapshot = _english_snapshot()
    profile = _profile()
    objective, descriptive = snapshot["candidates"]
    objective["answer_restriction"] = "Open"
    assert "objective answer_restriction must be Specific" in release.validate_candidate(objective, profile)
    descriptive["math_keyboard"] = "Yes"
    assert not any("math_keyboard" in error for error in release.validate_candidate(descriptive, profile))
    descriptive["math_keyboard"] = "Maybe"
    assert any("math_keyboard" in error for error in release.validate_candidate(descriptive, profile))


def test_completing_subject_binds_english_policy_without_upgrading_legacy():
    partial = profiles.resolve_for_metadata(None, {"grade": "06"})
    completed = profiles.resolve_for_metadata(partial, {"subject": "English"})
    policy = column_spec.from_profile(completed)
    assert policy["subject_adapter"] == "english"
    assert policy["rubric_tags"] == list(column_spec.ENGLISH_TAGS)
    legacy = copy.deepcopy(partial)
    legacy.pop(column_spec.POLICY_KEY)
    assert column_spec.from_profile(profiles.resolve_for_metadata(legacy, {"subject": "English"})) == {}


@pytest.mark.parametrize("subject", ["Mathematics", "Science", "History", "Geography", "Physics", "Hindi"])
def test_non_english_runs_share_column_rules_but_reject_rubric_tags(subject):
    profile = _profile(subject)
    policy = column_spec.from_profile(profile)
    assert policy["keywords_separator"] == ", "
    assert policy["objective_explanation_prefix"] == "option_label_and_answer"
    assert policy["rubric_half_step"] is True
    assert policy["math_keyboard"] == "response_requirement"
    assert policy["rubric_tags"] == []

    candidate = _english_snapshot(subject)["candidates"][1]
    # _profile supplies no board, so this is the generic format vocabulary.
    candidate["question_category"] = "Long Answer"
    criteria = candidate["sub_questions"][0]["keywords"]
    criteria[0]["weightage"], criteria[1]["weightage"] = "1.5", "0.5"
    assert release.validate_candidate(candidate, profile) == []
    criteria[0]["keyword"] = "[content]: " + criteria[0]["keyword"]
    assert any("tag" in error for error in release.validate_candidate(candidate, profile))


@pytest.mark.parametrize("subject", ["English", "Mathematics", "Science", "History"])
def test_frozen_v1_column_snapshot_is_not_upgraded_on_resume(subject):
    profile = _profile(subject)
    old_policy = {
        "version": "owner-column-spec-2026-09-08",
        "subject_adapter": "english" if subject == "English" else "mathematics" if subject == "Mathematics" else "existing",
        "keywords_separator": ", " if subject == "English" else " | ",
        "objective_explanation_prefix": "option_label_and_answer" if subject == "English" else "answer",
        "rubric_tags": ["content", "language", "creative", "evidence"] if subject == "English" else [],
        "rubric_half_step": subject in {"English", "Mathematics"},
        "math_keyboard": "No" if subject == "English" else "response_requirement",
        "multipart_parent_projection": "ordered_child_union",
        "generated_question_source": "UpSchool DB",
        "source_question_source": "run_publication",
    }
    profile[column_spec.POLICY_KEY] = copy.deepcopy(old_policy)
    resumed = profiles.resolve_for_metadata(profile, {"subject": subject, "grade": "06"})
    assert column_spec.from_profile(resumed) == old_policy
    metadata = column_spec.bind_metadata({"subject": subject, "grade": "06"}, resumed)
    assert column_spec.from_metadata(metadata) == old_policy
    assert profiles.rubric_tag_policy(metadata)["tags"] == old_policy["rubric_tags"]

    candidate = _english_snapshot(subject)["candidates"][0]
    if subject != "English":
        candidate["answer_explanation"] = candidate["answer_explanation"][3:]
    assert release.validate_candidate(candidate, resumed) == []
    rendered = aw.parse_workbook(aw.render_concept_file(_english_snapshot(subject), resumed))
    expected = "shape, dimension, comparison" if subject == "English" else "shape | dimension | comparison"
    assert rendered["sheets"]["Objective"]["rows"][0]["keywords"] == expected


def test_frozen_v1_keeps_its_creative_tag_and_fixed_english_keyboard():
    profile = _profile()
    policy = profile[column_spec.POLICY_KEY]
    policy.update({
        "version": "owner-column-spec-2026-09-08",
        "rubric_tags": ["content", "language", "creative", "evidence"],
        "math_keyboard": "No",
    })
    candidate = _english_snapshot()["candidates"][1]
    candidate["question_category"] = "Long Answer"
    criterion = candidate["sub_questions"][0]["keywords"][0]
    criterion["keyword"] = "[creative]: Supplies an original example."
    assert release.validate_candidate(candidate, profile) == []
    criterion["keyword"] = "[creativity]: Supplies an original example."
    assert any("tag" in error for error in release.validate_candidate(candidate, profile))
    candidate["math_keyboard"] = "Yes"
    assert any("carried column policy" in error for error in release.validate_candidate(candidate, profile))


@pytest.mark.parametrize("text,valid", [
    ("a) Cat. It names an animal.", True),
    ("a) Cat — it names an animal.", True),
    ("A) Cat. It names an animal.", False),
    ("a) Cats. This changes the answer.", False),
    ("a) cat. This changes the exact spelling.", False),
])
def test_explanation_requires_lowercase_label_and_complete_exact_answer(text, valid):
    answers = [{"answer_type": "Phrases", "answer_content": "Cat", "correct_answer": "Yes"}]
    assert bool(release.objective_explanation_defects(answers, text, include_option_label=True)) is not valid


def test_direct_item_review_keeps_frozen_legacy_policy():
    from app.services import assessment_item_review as review

    profile = _profile()
    profile.pop(column_spec.POLICY_KEY)
    captured = []

    def reviewer(payload):
        captured.append(payload)
        return {"verdict": "verified", "confidence": 1.0, "issues": []}

    review.review_items(
        [(_english_snapshot()["candidates"][0], _cell(), _atom())],
        meta={"subject": "English", "grade": "06"}, profile=profile,
        envelope_sha256="e" * 64, provider=reviewer, store=kernel.DecisionStore(),
    )
    assert captured and captured[0]["column_spec_policy"] == {}


def test_legacy_live_generator_stamps_generated_source(db, monkeypatch):
    from app.services import generation

    chapter = _chapter_with_concepts(db)
    concept = next(concept for topic in chapter.topics for concept in topic.concepts)
    monkeypatch.setattr(generation, "_openai_json", lambda *args, **kwargs: {
        "questions": [{"question": "Which shape is a solid?", "question_text": "Which shape is a solid?\na) Cube\nb) Circle", "answer_explanation": "Cube. It occupies space.", "answers": [
            {"answer_type": "Words", "answer_content": "Cube", "correct_answer": "Yes", "answer_weightage": 1},
            {"answer_type": "Words", "answer_content": "Circle", "correct_answer": "No", "answer_weightage": 0},
        ]}]
    })
    rows = generation._live_questions_for_concept(
        concept, question_type="objective", cognitive_skill="Remember",
        difficulty="Less", category="MCQ", count=1, start_index=1,
        marks=1, question_duration=1, math_keyboard="No",
    )
    assert rows[0]["question_source"] == "UpSchool DB"
    assert rows[0]["question_text"].endswith("b) Circle")


def test_master_refiner_accepts_current_english_tag_and_rejects_old_registry():
    from app.services import assessment_master_refiner as refiner

    original = _english_snapshot()["candidates"][1]
    original["sub_questions"][0]["keywords"][0]["keyword"] = "[creativity]: Supplies an original example."
    checker = refiner._response_checker(
        unit_kind="candidate", unit_id=original["candidate_id"], original=original, profile=_profile(),
    )
    response = {"record_kind": "candidate", "row_ref": original["candidate_id"], "record": copy.deepcopy(original), "rationale": "The recorded wording is already clear."}
    assert checker(response) == []
    response["record"]["sub_questions"][0]["keywords"][0]["keyword"] = "[creative]: Supplies an original example."
    assert any("allowed functional tag" in error for error in checker(response))

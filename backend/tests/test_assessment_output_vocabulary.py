"""Output labels are fixed values while all semantic axes remain API-owned."""
from __future__ import annotations

import copy

import pytest

from app.services import assessment_cells as cells
from app.services import assessment_grouping as grouping
from app.services import assessment_output_vocabulary as vocabulary
from app.services import assessment_profile as profile
from app.services.phase3 import kernel


@pytest.mark.parametrize("subject", ["English", "Mathematics", "Science", "History"])
def test_fresh_categories_use_one_blank_spelling_without_changing_rules(subject):
    metadata = {"board": "MSBSHSE", "grade": "06", "subject": subject}
    legacy = profile.assessment_format_policy(metadata=metadata)
    current = profile.resolve_for_metadata(None, metadata)
    policy = profile.assessment_format_policy(current)
    assert policy["output_vocabulary_version"] == vocabulary.VERSION
    for sheet, categories in legacy["formats_by_sheet"].items():
        for label, original_rule in categories.items():
            target = vocabulary.CATEGORY_ALIASES.get(label, label)
            assert policy["formats_by_sheet"][sheet][target] == original_rule
    assert "Fill in the Blanks" not in str(policy["formats_by_sheet"])


def test_frozen_profile_reentry_keeps_its_exact_old_categories():
    metadata = {"board": "MSBSHSE", "grade": "06", "subject": "English"}
    legacy = profile.resolve(None)
    legacy["_resolved_metadata"] = metadata
    before = profile.assessment_format_policy(legacy)
    rerun = profile.resolve_for_metadata(legacy, metadata)
    assert vocabulary.POLICY_KEY not in rerun
    assert profile.assessment_format_policy(rerun) == before
    assert profile.output_question_category("Fill in the Blanks", rerun) == "Fill in the Blanks"


def test_current_run_freezes_labels_and_rules_against_later_profile_edits():
    metadata = {"board": "Unlisted board", "grade": "7", "subject": "Science"}
    frozen = profile.resolve_for_metadata(None, metadata)
    expected = profile.assessment_format_policy(frozen)
    changed = copy.deepcopy(frozen)
    changed["assessment_format"] = {"policy_id": "new", "formats_by_sheet": {}}
    changed[vocabulary.POLICY_KEY]["category_aliases"]["Fill in the Blanks"] = "Renamed"
    assert profile.assessment_format_policy(changed) == expected


def test_generic_calibration_is_explicit_and_does_not_import_a_local_matrix():
    current = profile.resolve_for_metadata(None, {"subject": "Biology", "grade": "8"})
    policy = profile.assessment_format_policy(current)
    assert policy["calibration"]["mode"] == "generic_api_calibrated"
    assert policy["formats_by_sheet"]["descriptive"]["Long Answer"] == {}
    assert profile.question_duration_minutes(
        current, sheet_kind="descriptive", question_category="Long Answer",
        difficulty="Moderate", marks=3,
    ) is None


def test_aliases_do_not_guess_meaning_or_erase_mark_tagged_categories():
    current = profile.resolve_for_metadata(None, {"subject": "Mathematics"})
    assert profile.output_question_category("Fill in the Blanks", current) == "Fill in the blanks"
    for value in ["MCQ", "fill in the blanks", "Fill in the blank", "Short Answer Type (2 Marks)",
                  "Short Answer Type (3 Marks)", "Rearrange the following words", ""]:
        assert profile.output_question_category(value, current) == value


def test_category_checker_uses_canonical_labels_without_local_classification():
    current = profile.resolve_for_metadata(None, {"subject": "Science"})
    check = cells._verdict_checker(
        "source_qid", "Q1", ("objective", "subjective", "descriptive"),
        profile.assessment_format_policy(current),
    )
    response = {"source_qid": "Q1", "sheet_kind": "subjective", "question_category": "Fill in the blanks",
                "selection_mode": "", "cognitive_skill": "Understand", "difficulty": "Moderate", "marks": 2,
                "rationale": "The item requires a supplied term."}
    assert check(response) == []
    assert any("question_category" in issue for issue in check({**response, "question_category": "FIB"}))


@pytest.mark.parametrize("tier", ["basic", " Basic ", "Easy", "Moderate", "Advanced group", ""])
def test_group_label_gate_rejects_aliases_instead_of_reclassifying(tier):
    issues = grouping._level_checker("C1")({"candidate_id": "C1", "tier": tier, "rationale": "Evidence"})
    assert any("tier must" in issue for issue in issues)


def test_level_remains_independent_of_blueprint_difficulty_and_receives_visual_evidence(tmp_path):
    seen = []
    def author(payload):
        seen.append(copy.deepcopy(payload))
        return {"candidate_id": "C1", "tier": "Advanced", "rationale": "Independent transfer demand"}
    def critic(payload):
        assert "visual_evidence" in payload
        return {"verdict": "verified", "confidence": 0.8, "issues": []}
    output = grouping.decide_levels(
        [{"candidate": {"candidate_id": "C1", "difficulty": "Less", "question": "Explain the scene.",
                        "question_text": '[img src="https://example.test/figure.jpg" alt="Scene"]'},
          "concept": {"concept_key": "C", "teaching_description": "Interpret source evidence."}}],
        meta={"grade": "8", "subject": "History"}, envelope_sha256="source-hash",
        provider=author, critic=critic, store=kernel.DecisionStore(tmp_path / "decisions"),
    )
    assert output[0]["tier"] == "Advanced"
    assert "difficulty" not in seen[0]["candidate"]
    assert seen[0]["allowed_tier_labels"] == ["Basic", "Intermediate", "Advanced"]
    assert seen[0]["visual_evidence"]["images"][0]["state"] == "unavailable"
    assert any("assessment_visual_evidence_unavailable" in flag for flag in output[0]["flags"])


@pytest.mark.parametrize("name", ["_live_level", "_live_level_critic", "_live_cluster", "_live_cluster_critic",
                                  "_live_description", "_live_description_critic"])
def test_every_group_author_and_critic_transports_actual_images(monkeypatch, name):
    from app.services import generation
    observed = {}
    monkeypatch.setattr(grouping.visual_evidence, "image_inputs", lambda payload: ["data:image/jpeg;base64,fixture"])
    def call(*args, **kwargs):
        observed.update(kwargs)
        return {}
    monkeypatch.setattr(generation, "_openai_json", call)
    getattr(grouping, name)({"visual_evidence": {"images": []}})
    assert observed["image_urls"] == ["data:image/jpeg;base64,fixture"]


def test_approved_column_interpretations_are_explicit_in_new_run_policy():
    from app.services import column_spec
    current = profile.resolve_for_metadata(None, {"subject": "Science"})
    policy = column_spec.from_profile(current)
    assert policy["subjective_blank"] == {
        "placeholder": "a", "stored_question_token": "$$a$$",
        "learner_text": "____", "subsequent_placeholders": "b through t in order",
    }
    assert policy["objective_option_weights"] == {"correct": "accepted item marks", "distractors": 0}
    assert policy["identity_projection"]["display_names"] == "plain names"
    assert policy["post_topics"] == "Post topic roster"


def test_pre_refiner_receives_only_mapped_prerequisites_not_chapter_questions(monkeypatch):
    from types import SimpleNamespace
    from app.services import build_concepts_release as release
    from app.services import release_refiner
    observed = {}
    def refine(records, **kwargs):
        observed.update(kwargs)
        return records, {}, []
    monkeypatch.setattr(release_refiner, "refine_release", refine)
    monkeypatch.setattr(release, "_instruction_set_summary", lambda job: {})
    monkeypatch.setattr(release_refiner, "decision_store_for_job", lambda job_id: None)
    row = {"_pre_concept_id": "PRC-1", "_aegis_pre_prerequisites": [
        {"prerequisite_id": "PR-1", "text": "Use a number line to compare integers."}
    ]}
    job = SimpleNamespace(id=42, deposit_scope_ids=[1], source_book="Book",
                          mmd_text="Current chapter question that must not travel.", question_inventory={"items": ["QINV-1"]})
    release._refine_pre_records(SimpleNamespace(get=lambda *args: None), job, {"rows": [row]})
    assert observed["metadata"]["prerequisite_evidence"] == [
        {"pre_concept_id": "PRC-1", "prerequisites": row["_aegis_pre_prerequisites"]}
    ]
    assert observed["metadata"]["source_text"] == ""
    assert observed["metadata"]["inventory"] == {}
    assert "Current chapter question" not in str(observed)


@pytest.mark.parametrize("category", ["Fill in the Blanks", "Fill in the blanks"])
def test_strict_import_accepts_both_exact_blank_labels_and_keeps_mark_contract(monkeypatch, category):
    from app.bulk_import import reader
    monkeypatch.setattr(reader, "_row_chapter_meta", lambda *args: {
        "board": "MSBSHSE", "grade": "06", "subject": "English",
    })
    question = {"question_category": category, "marks": 4,
                "question_duration": 5, "level_of_difficulty": "Moderate"}
    before = copy.deepcopy(question)
    kwargs = {"sheet_layout": None, "row": (), "kind": "subjective",
              "answers": [], "row_label": "Q1"}
    assert reader._strict_assessment_policy_issues(question=question, **kwargs) == []
    assert question == before
    wrong_duration = reader._strict_assessment_policy_issues(
        question={**question, "question_duration": 4}, **kwargs,
    )
    assert any("does not match policy duration 5" in issue for issue in wrong_duration)
    invalid = reader._strict_assessment_policy_issues(question={**question, "marks": 3}, **kwargs)
    assert any("marks 3 must be one of" in issue for issue in invalid)
    unknown = reader._strict_assessment_policy_issues(question={**question, "question_category": "FIB"}, **kwargs)
    assert any("not permitted" in issue for issue in unknown)


def _direct_blank_payload():
    cell = {"cell_id": "CELL-1", "sheet_kind": "subjective", "question_category": "Fill in the Blanks",
            "cognitive_skill": "Remember", "difficulty": "Moderate", "marks": 4,
            "count": 1, "appears_in": "Pre/Post-Worksheet/Test", "source_policy": "generate"}
    candidate = {**cell, "candidate_id": "C1", "blueprint_cell_id": "CELL-1", "source_atom_ids": [],
                 "question": "The opposite of noisy is $$a$$.", "question_text": "The opposite of noisy is $$a$$.",
                 "question_duration": 5, "answer_restriction": "Specific", "restriction_reason": "Bounded word target.",
                 "math_keyboard": "No", "sub_questions": [], "answers": [
                     {"answer_type": "Phrases", "answer_content": "quiet", "answer_display": "Yes",
                      "answer_weightage": 4, "placeholder": "a"}
                 ]}
    return {"blueprint_cells": [cell], "candidates": [candidate]}


def test_direct_freeze_and_workbook_project_the_same_labels_without_mutating_input():
    from app.bulk_import import assessment_workbook as workbook
    from app.services import assessment_release as release
    current = profile.resolve_for_metadata(None, {"board": "MSBSHSE", "grade": "06", "subject": "English"})
    payload = _direct_blank_payload()
    # Direct caller bypassed runner projection and has one current alias
    # beside a historical label of the SAME declared category.
    payload["candidates"][0]["question_category"] = "Fill in the blanks"
    payload["assessment_format_policy"] = profile.assessment_format_policy(current)
    before = copy.deepcopy(payload)
    result = release.freeze_payload(payload, current)
    assert result["errors"] == []
    assert payload == before
    assert result["hashes"]["payload"] == release.sha256_json(before)
    record = workbook._question_record(payload["candidates"][0], "Subjective", current)
    assert record["question_category"] == "Fill in the blanks"
    assert record["placeholder_1"] == "a"
    assert "$$a$$" in record["question"]
    assert "____" in record["question_text"]
    bad = copy.deepcopy(payload)
    bad["candidates"][0]["question_duration"] = 4
    assert any("policy duration 5" in error for error in release.freeze_payload(bad, current)["errors"])


def test_direct_fresh_validation_cannot_bypass_category_enum_by_omitting_payload_policy():
    from app.services import assessment_release as release
    current = profile.resolve_for_metadata(None, {"subject": "Science"})
    payload = _direct_blank_payload()
    for row in [*payload["blueprint_cells"], *payload["candidates"]]:
        row["question_category"] = "FIB"
    assert any("question_category" in error for error in release.validate_blueprint_cell(payload["blueprint_cells"][0], current))
    assert any("question_category" in error for error in release.validate_candidate(payload["candidates"][0], current))
    assert any("not permitted" in error for error in release.freeze_payload(payload, current)["errors"])


def test_direct_frozen_legacy_release_retains_legacy_label_and_policy():
    from app.bulk_import import assessment_workbook as workbook
    from app.services import assessment_release as release
    metadata = {"board": "MSBSHSE", "grade": "06", "subject": "English"}
    frozen = profile.resolve(None)
    frozen["_resolved_metadata"] = metadata
    payload = _direct_blank_payload()
    payload["assessment_format_policy"] = profile.assessment_format_policy(frozen)
    before = copy.deepcopy(payload)
    assert release.freeze_payload(payload, frozen)["errors"] == []
    assert workbook._question_record(payload["candidates"][0], "Subjective", frozen)["question_category"] == "Fill in the Blanks"
    assert payload == before

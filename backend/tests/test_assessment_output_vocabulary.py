"""Output labels are fixed values while all semantic axes remain API-owned."""
from __future__ import annotations

import copy

import pytest

from app.services import assessment_cells as cells
from app.services import assessment_grouping as grouping
from app.services import assessment_output_vocabulary as vocabulary
from app.services import assessment_profile as profile
from app.services.phase3 import kernel


# Independently transcribed from the supplied CMS workbook, in sheet order.
# The category cells ending in a tab/space have only boundary whitespace removed.
APPROVED_CATEGORIES = (
    "Match the following Questions", "Case Based Questions", "Composition Writing",
    "Assertion & Reasons Type", "Sentence Transformation", "Extract based on Map Survey",
    "Numerical/application based", "Long Answer Type (5 Marks)", "Locating and Plotting on map",
    "Choose the ODD one Out", "Passage based questions", "Long Answer Type (4 Marks)",
    "Short Answer Type (2 Marks)", "True or False", "Error correction",
    "Identifying the following", "Fill in the Blanks", "Rearrange the following words",
    "Extract based question", "Long Answer Type (6 Marks)", "Short Answer Type (3 Marks)",
    "Multiple Choice Question", "Very Short Answer Questions", "Name the following",
    "Read the Explanations and give structure", "Reading Comprehension",
)
APPROVED_SOURCES = (
    "UpSchool DB", "Selina", "NCERT", "NON - NCERT", "Oswaal", "K State (Extra)",
    "Seed to Plant", "Balbharati", "RS Aggarwal",
)
APPROVED_SKILLS = ("Remember", "Understand", "Apply", "Analyse", "Evaluate", "Create")


def test_current_snapshot_matches_every_supplied_value_and_no_extra_value():
    policy = vocabulary.snapshot()
    assert policy["question_categories"] == list(APPROVED_CATEGORIES)
    assert policy["question_sources"] == list(APPROVED_SOURCES)
    assert policy["cognitive_skills"] == list(APPROVED_SKILLS)
    assert policy["category_aliases"] == {}
    assert vocabulary.is_current(policy)
    policy["question_categories"].append("Invented category")
    assert vocabulary.snapshot()["question_categories"] == list(APPROVED_CATEGORIES)


@pytest.mark.parametrize("subject", ["English", "Mathematics", "Science", "History"])
def test_fresh_categories_offer_the_complete_approved_catalogue_and_retain_rules(subject):
    metadata = {"board": "MSBSHSE", "grade": "06", "subject": subject}
    legacy = profile.assessment_format_policy(metadata=metadata)
    current = profile.resolve_for_metadata(None, metadata)
    policy = profile.assessment_format_policy(current)
    assert policy["output_vocabulary_version"] == vocabulary.VERSION
    for sheet, categories in legacy["formats_by_sheet"].items():
        for label, original_rule in categories.items():
            target = vocabulary.FORMAT_CATEGORY_ALIASES.get(label, label)
            if target in APPROVED_CATEGORIES and (target != "True or False" or sheet == "subjective"):
                assert policy["formats_by_sheet"][sheet][target] == original_rule
    for sheet, categories in policy["formats_by_sheet"].items():
        expected = (
            set(APPROVED_CATEGORIES)
            if legacy["policy_id"] == "generic-cms"
            else {
                vocabulary.FORMAT_CATEGORY_ALIASES.get(label, label)
                for label in legacy["formats_by_sheet"][sheet]
            } & set(APPROVED_CATEGORIES)
        )
        assert set(categories) == expected - ({"True or False"} if sheet != "subjective" else set())
    assert "Fill in the blanks" not in str(policy["formats_by_sheet"])
    assert "Long Answer" not in policy["formats_by_sheet"]["descriptive"]


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
    assert profile.assessment_format_policy(changed) == expected


def test_generic_calibration_is_explicit_and_does_not_import_a_local_matrix():
    current = profile.resolve_for_metadata(None, {"subject": "Biology", "grade": "8"})
    policy = profile.assessment_format_policy(current)
    assert policy["calibration"]["mode"] == "retained_exact_rules_else_api_calibrated"
    assert policy["formats_by_sheet"]["descriptive"]["Reading Comprehension"] == {}
    assert profile.question_duration_minutes(
        current, sheet_kind="descriptive", question_category="Reading Comprehension",
        difficulty="Moderate", marks=3,
    ) is None


def test_aliases_do_not_guess_meaning_or_erase_mark_tagged_categories():
    current = profile.resolve_for_metadata(None, {"subject": "Mathematics"})
    for value in ["Fill in the Blanks", "Fill in the blanks", "True/False", "Assertion & Reasons",
                  "MCQ", "fill in the blanks", "Fill in the blank", "Short Answer Type (2 Marks)",
                  "Short Answer Type (3 Marks)", "Rearrange the following words", ""]:
        assert profile.output_question_category(value, current) == value


def test_category_checker_uses_canonical_labels_without_local_classification():
    current = profile.resolve_for_metadata(None, {"subject": "Science"})
    check = cells._verdict_checker(
        "source_qid", "Q1", ("objective", "subjective", "descriptive"),
        profile.assessment_format_policy(current),
    )
    response = {"source_qid": "Q1", "sheet_kind": "subjective", "question_category": "Fill in the Blanks",
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
                 "question_source": "UpSchool DB",
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
    payload["assessment_format_policy"] = profile.assessment_format_policy(current)
    before = copy.deepcopy(payload)
    result = release.freeze_payload(payload, current)
    assert result["errors"] == []
    assert payload == before
    assert result["hashes"]["payload"] == release.sha256_json(before)
    record = workbook._question_record(payload["candidates"][0], "Subjective", current)
    assert record["question_category"] == "Fill in the Blanks"
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


def test_frozen_v1_replays_its_recorded_aliases_without_adopting_v2():
    metadata = {"subject": "Science", "grade": "8"}
    frozen = profile.resolve(None)
    frozen["_resolved_metadata"] = metadata
    frozen[vocabulary.POLICY_KEY] = {
        "version": "assessment-output-vocabulary-2026-09-08-v1",
        "category_aliases": {
            "Fill in the Blanks": "Fill in the blanks",
            "Assertion & Reasons Type": "Assertion & Reasons",
            "True or False": "True/False",
            "Extract Based Question": "Extract Based Questions",
        },
        "group_labels": ["Basic", "Intermediate", "Advanced"],
        "classification_owner": "model_or_explicit_blueprint",
        "tier_owner": "model",
    }
    before = copy.deepcopy(frozen)
    replay = profile.resolve_for_metadata(frozen, metadata)
    assert replay[vocabulary.POLICY_KEY] == before[vocabulary.POLICY_KEY]
    assert not vocabulary.is_current(replay[vocabulary.POLICY_KEY])
    assert profile.output_question_category("Fill in the Blanks", replay) == "Fill in the blanks"
    assert profile.output_question_category("True or False", replay) == "True/False"
    assert "Long Answer" in profile.question_categories(replay)["descriptive"]
    assert vocabulary.field_errors(
        {"question_category": "Long Answer", "cognitive_skill": "Legacy skill", "question_source": "Old publisher"},
        replay[vocabulary.POLICY_KEY], include_source=True,
    ) == []
    assert frozen == before


@pytest.mark.parametrize("field,bad_value", [
    ("question_category", "Fill in the blanks"),
    ("question_category", "Long Answer"),
    ("question_category", "True/False"),
    ("question_category", "Fill in the Blanks "),
    ("cognitive_skill", "Analyze"),
    ("cognitive_skill", "Remembering"),
    ("cognitive_skill", "Remember | Understand"),
    ("question_source", "NCERT "),
    ("question_source", "UpSchool"),
    ("question_source", "NON-NCERT"),
    ("question_source", "NCERT | Selina"),
    ("question_source", "Unlisted publication"),
])
def test_closed_fields_reject_synonyms_combined_values_and_whitespace(field, bad_value):
    policy = vocabulary.snapshot()
    record = {"question_category": "Fill in the Blanks", "cognitive_skill": "Remember",
              "question_source": "NCERT", "sheet_kind": "subjective"}
    assert vocabulary.field_errors(record, policy, include_source=True) == []
    record[field] = bad_value
    before = copy.deepcopy(record)
    errors = vocabulary.field_errors(record, policy, include_source=True)
    assert any(error.startswith(field + " ") for error in errors)
    assert record == before


@pytest.mark.parametrize("sheet_kind", ["objective", "descriptive"])
def test_true_or_false_requires_subjective_even_with_valid_approved_spelling(sheet_kind):
    current = profile.resolve_for_metadata(None, {"subject": "Science"})
    record = {"question_category": "True or False", "cognitive_skill": "Remember", "sheet_kind": sheet_kind}
    assert any("subjective" in error for error in vocabulary.field_errors(
        record, current[vocabulary.POLICY_KEY],
    ))


@pytest.mark.parametrize("source", APPROVED_SOURCES)
def test_workbook_preserves_each_exact_approved_source(source):
    from app.bulk_import import assessment_workbook as workbook
    current = profile.resolve_for_metadata(None, {"subject": "Science"})
    candidate = _direct_blank_payload()["candidates"][0]
    candidate["source_policy"] = "source"
    candidate["question_source"] = source
    before = copy.deepcopy(candidate)
    defects = []
    record = workbook._question_record(candidate, "Subjective", current,
                                       source_book=source, vocabulary_defects=defects)
    assert record["question_source"] == source
    assert defects == []
    assert candidate == before


def test_current_workbook_never_leaks_rejected_values_and_retains_their_evidence():
    from app.bulk_import import assessment_workbook as workbook
    current = profile.resolve_for_metadata(None, {"subject": "Science"})
    candidate = _direct_blank_payload()["candidates"][0]
    candidate.update(question_category="Fill in the blanks", cognitive_skill="Analyze", question_source="Unknown publisher")
    before = copy.deepcopy(candidate)
    defects = []
    record = workbook._question_record(candidate, "Subjective", current, vocabulary_defects=defects)
    assert record["question_category"] == record["cognitive_skills"] == record["question_source"] == ""
    assert {issue["field"]: issue["original_value"] for issue in defects} == {
        "question_category": "Fill in the blanks", "cognitive_skills": "Analyze", "question_source": "Unknown publisher",
    }
    assert all(issue["code"] == "output_vocabulary_invalid" for issue in defects)
    assert record["question"] == before["question"]
    assert candidate == before


def test_approved_source_cannot_be_substituted_for_the_frozen_publication():
    from app.bulk_import import assessment_workbook as workbook
    current = profile.resolve_for_metadata(None, {"subject": "Science"})
    candidate = _direct_blank_payload()["candidates"][0]
    candidate.update(source_policy="source", question_source="Selina")
    defects = []
    record = workbook._question_record(candidate, "Subjective", current,
                                       source_book="NCERT", vocabulary_defects=defects)
    assert record["question_source"] == ""
    assert any(issue["code"] == "question_source_provenance_mismatch"
               and issue["original_value"] == "Selina" and issue["expected_source"] == "NCERT"
               for issue in defects)
    assert candidate["question_source"] == "Selina"


@pytest.mark.parametrize("field,bad_value", [
    ("question_category", "Long Answer"), ("cognitive_skill", "Analyze"),
    ("question_source", "Unlisted publication"),
])
def test_release_gate_rejects_controlled_fields_on_parent_and_multipart_child(field, bad_value):
    from app.services import assessment_release as release
    current = profile.resolve_for_metadata(None, {"subject": "Science"})
    candidate = _direct_blank_payload()["candidates"][0]
    candidate["sub_questions"] = [{"text": "Name one example.", field: bad_value}]
    before = copy.deepcopy(candidate)
    errors = release.output_vocabulary_errors(candidate, current, include_source=True)
    assert any("sub_questions[1]" in error and field in error for error in errors)
    assert candidate == before
    candidate["sub_questions"] = [{"text": "Name one example."}]
    assert release.output_vocabulary_errors(candidate, current, include_source=True) == []
    candidate[field] = bad_value
    assert any(error.startswith(field + " ") for error in release.output_vocabulary_errors(
        candidate, current, include_source=True,
    ))


def test_freeze_cannot_bypass_source_and_skill_contract_by_omitting_payload_policy():
    from app.services import assessment_release as release
    current = profile.resolve_for_metadata(None, {"subject": "Science"})
    payload = _direct_blank_payload()
    payload["candidates"][0].update(cognitive_skill="Analyze", question_source="Unlisted publication")
    before = copy.deepcopy(payload)
    result = release.freeze_payload(payload, current)
    assert any("cognitive_skill" in error for error in result["errors"])
    assert any("question_source" in error for error in result["errors"])
    assert result["hashes"]["payload"] == release.sha256_json(before)
    assert payload == before


def _parsed_master_with_current_vocabulary():
    from app.bulk_import import assessment_workbook as workbook
    from tests.test_mes_dual_output import _snapshot
    snapshot = _snapshot()
    # Keep this existing marking/layout corpus unchanged; apply the new
    # vocabulary independently to test the actual XLSX serialization boundary.
    current = profile.resolve(None)
    current[vocabulary.POLICY_KEY] = vocabulary.snapshot()
    data, issues = workbook.render_master_file(snapshot, current)
    parsed = workbook.parse_workbook(data)
    assert workbook.validate_master_file(parsed, snapshot, current,
        group_provenance=issues["group_provenance"]) == []
    return current, snapshot, parsed, issues


@pytest.mark.parametrize("field,bad_value", [
    ("question_category", "MCQ"), ("cognitive_skills", "Remembering"),
    ("question_source", "Unlisted publication"),
])
def test_workbook_readback_refuses_controlled_cell_tampering(field, bad_value):
    from app.bulk_import import assessment_workbook as workbook
    current, snapshot, parsed, issues = _parsed_master_with_current_vocabulary()
    row = next(row for row in parsed["sheets"]["Objective"]["rows"] if row["question_label"])
    row[field] = bad_value
    errors = workbook.validate_master_file(parsed, snapshot, current,
        group_provenance=issues["group_provenance"])
    assert any(field in error and "approved" in error for error in errors)


def test_workbook_readback_checks_unprojected_multipart_child_taxonomy():
    from app.bulk_import import assessment_workbook as workbook
    current, snapshot, parsed, issues = _parsed_master_with_current_vocabulary()
    parent = next(candidate for candidate in snapshot["candidates"] if candidate["sheet_kind"] == "descriptive")
    parent["sub_questions"][0]["cognitive_skill"] = "Analyze"
    errors = workbook.validate_master_file(parsed, snapshot, current,
        group_provenance=issues["group_provenance"])
    assert any("sub_questions[1]" in error and "cognitive_skill" in error for error in errors)


@pytest.mark.parametrize("gate", ["payload", "readback"])
def test_fixer_acceptance_cannot_waive_output_vocabulary_blocker(gate):
    from types import SimpleNamespace
    from app.services import assessment_release_service as service
    defect = "question_source must be one exact approved value (got 'Unknown')"
    release = SimpleNamespace(
        diagnostics={"payload_errors": [defect] if gate == "payload" else []},
        payload={"candidates": [{"flags": [], "authority": {"fixer": True},
                                 "fixer_accepted_codes": ["output_vocabulary_invalid"]}]},
    )
    manifest = {"read_back": {"concepts_errors": [], "master_errors": [defect] if gate == "readback" else []}}
    assert service._readiness(release, manifest) == service.BLOCKED


@pytest.mark.parametrize("identity_field", ["source_qid", "pre_question_id"])
def test_cell_provider_schema_exposes_only_supplied_values_and_rejects_aliases(identity_field):
    from pydantic import ValidationError
    from app.services.response_schemas import assessment_cell_schema
    schema = assessment_cell_schema(
        identity_field=identity_field, identity_value="Q1",
        sheet_kinds=("objective", "subjective", "descriptive"),
        question_categories=APPROVED_CATEGORIES, cognitive_skills=APPROVED_SKILLS,
    )
    contract = schema.json_schema()["schema"]
    assert contract["additionalProperties"] is False
    assert set(contract["required"]) == set(contract["properties"])
    assert contract["properties"]["question_category"]["enum"] == list(APPROVED_CATEGORIES)
    assert contract["properties"]["cognitive_skill"]["enum"] == list(APPROVED_SKILLS)
    response = {identity_field: "Q1", "sheet_kind": "subjective", "question_category": "Fill in the Blanks",
                "cognitive_skill": "Understand", "difficulty": "Moderate", "marks": 1.0,
                "selection_mode": "", "rationale": "The response supplies a missing word."}
    schema.validate_response(response)
    for changes in ({"question_category": "Fill in the blanks"}, {"cognitive_skill": "Analyze"},
                    {identity_field: "Q2"}, {"invented_field": "value"}):
        with pytest.raises(ValidationError):
            schema.validate_response({**response, **changes})


def test_generated_and_source_cell_checkers_enforce_the_same_frozen_vocabulary():
    current = profile.resolve_for_metadata(None, {"subject": "Science"})
    policy = current[vocabulary.POLICY_KEY]
    formats = profile.assessment_format_policy(current)
    response = {"sheet_kind": "subjective", "question_category": "Fill in the Blanks",
                "cognitive_skill": "Understand", "difficulty": "Moderate", "marks": 1,
                "selection_mode": "", "rationale": "The response supplies a missing word."}
    for field, checker in (
        ("source_qid", cells._cell_checker("Q1", profile.sheet_kinds(current), formats, vocabulary=policy)),
        ("pre_question_id", cells._generated_cell_checker("Q1", profile.sheet_kinds(current), formats, vocabulary=policy)),
    ):
        valid = {**response, field: "Q1"}
        assert checker(valid) == []
        for changed in ({"question_category": "Fill in the blanks"}, {"cognitive_skill": "Analyze"}):
            assert checker({**valid, **changed})


@pytest.mark.parametrize("policy_field,record_field,unapproved", [
    ("question_categories", "question_category", "Invented category"),
    ("question_sources", "question_source", "Invented publisher"),
    ("cognitive_skills", "cognitive_skill", "Invented skill"),
])
def test_known_current_version_cannot_be_used_to_smuggle_extra_allowed_values(policy_field, record_field, unapproved):
    policy = vocabulary.snapshot()
    policy[policy_field].append(unapproved)
    record = {"question_category": "Fill in the Blanks", "cognitive_skill": "Remember", "question_source": "NCERT"}
    record[record_field] = unapproved
    errors = vocabulary.field_errors(record, policy, include_source=True)
    assert any("output_vocabulary" in error for error in errors)
    assert any(error.startswith(record_field + " ") for error in errors)
    with pytest.raises(ValueError, match="output_vocabulary"):
        vocabulary.instruction(policy)
    with pytest.raises(ValueError, match="output_vocabulary"):
        vocabulary.format_policy({"policy_id": "generic-cms", "formats_by_sheet": {}}, policy)


@pytest.mark.parametrize("tamper", ["old_version", "extra_category"])
def test_fresh_profile_cannot_reuse_a_stale_or_widened_cached_format_policy(tamper):
    metadata = {"subject": "Science", "grade": "8"}
    current = profile.resolve_for_metadata(None, metadata)
    cached = current[vocabulary.FORMAT_SNAPSHOT_KEY]["policy"]
    if tamper == "old_version":
        cached["output_vocabulary_version"] = "assessment-output-vocabulary-2026-09-08-v1"
    else:
        cached["formats_by_sheet"]["descriptive"]["Invented category"] = {}
    with pytest.raises(ValueError):
        profile.assessment_format_policy(current)
    with pytest.raises(ValueError):
        profile.resolve_for_metadata(current, metadata)


def test_direct_database_export_enforces_only_the_question_carried_vocabulary():
    from types import SimpleNamespace
    from app.bulk_import import writer
    question = SimpleNamespace(
        id=1, question_label="Q1", sheet_kind="subjective",
        question_category="Fill in the blanks", cognitive_skills="Analyze",
        question_source="Old publisher", sub_questions=[], route_audit={},
    )
    legacy = writer._question_taxonomy_projection(question)
    assert legacy["question_category"] == "Fill in the blanks"
    assert legacy["cognitive_skills"] == "Analyze"
    assert legacy["question_source"] == "Old publisher"
    question.route_audit[vocabulary.POLICY_KEY] = vocabulary.snapshot()
    current = writer._question_taxonomy_projection(question)
    assert current["question_category"] == current["cognitive_skills"] == current["question_source"] == ""
    assert len(current["_taxonomy_output_defects"]) == 3
    assert question.question_source == "Old publisher"


def test_blueprint_ui_receives_only_approved_category_choices(client):
    response = client.get("/directory/vocab")
    assert response.status_code == 200
    wire = response.json()
    assert wire["cognitive_skills"] == list(APPROVED_SKILLS)
    assert set().union(*(set(values) for values in wire["question_categories"].values())) == set(APPROVED_CATEGORIES)
    assert all(set(values) <= set(APPROVED_CATEGORIES) for values in wire["question_categories"].values())
    assert "True or False" not in wire["question_categories"]["objective"]


def test_post_generation_cannot_hide_current_taxonomy_errors_with_legacy_aliases():
    from types import SimpleNamespace
    from app.services import post_generation
    from app.bulk_import import writer
    question = SimpleNamespace(
        id=1, question_label="Q1", sheet_kind="subjective",
        question_category="Fill in the blanks", cognitive_skills="Remembering",
        question_source="", sub_questions=[],
        route_audit={vocabulary.POLICY_KEY: vocabulary.snapshot()},
        level_of_difficulty="Moderate", question_duration=1, math_keyboard="No",
        question_appears_in="Post", question="Supply a term.", question_text="Supply a term.",
        group=SimpleNamespace(concept=SimpleNamespace(sources="NCERT")),
    )
    post_generation.column_mapping(SimpleNamespace(commit=lambda: None), [question])
    assert question.cognitive_skills == "Remembering"
    assert question.question_source == ""
    rendered = writer._question_taxonomy_projection(question)
    assert rendered["question_category"] == rendered["cognitive_skills"] == rendered["question_source"] == ""
    assert len(rendered["_taxonomy_output_defects"]) == 3

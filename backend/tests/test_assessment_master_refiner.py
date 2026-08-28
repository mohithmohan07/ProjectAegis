"""Slice 5: adversarial contract tests for the assessment Master Refiner.

These tests intentionally exercise the production release validators and
XLSX render/read-back path.  The Refiner may improve only answer/rubric prose
and occupied-group descriptions; every assessment identity remains exact and
any failure leaves the authored unit in place with a review warning.
"""
from __future__ import annotations

import copy
from collections import Counter
from typing import Callable

import pytest

from app.bulk_import import assessment_workbook as aw
from app.services import assessment_master_refiner as refiner
from app.services import assessment_profile
from app.services import assessment_release as rel
from app.services import assessment_release_service as release_service
from app.services.phase3 import kernel


ENVELOPE_SHA256 = "e" * 64
CONCEPT_KEY = "release:test:0001"
MACHINE_ID = "06MSMA_T01_Shapes"
OBJECTIVE_ID = "CAND-OBJECTIVE"
DESCRIPTIVE_ID = "CAND-DESCRIPTIVE"
SUBJECTIVE_ID = "CAND-SUBJECTIVE"
BASIC_GROUP = f"({MACHINE_ID}) BG01"
INTERMEDIATE_GROUP = f"({MACHINE_ID}) IG01"
ADVANCED_GROUP = f"({MACHINE_ID}) AG01"

_METADATA = {
    "board": "MSBSHSE",
    "grade": "6",
    "subject": "Mathematics",
    "unit": "Geometry",
    "chapter_title": "Three-Dimensional Shapes",
    "chapter_code": "06MSMA01",
    "profile": assessment_profile.DEFAULT_PROFILE,
}

_OBJECTIVE_EXPLANATION = (
    "QINV-0001 identifies the cube beside "
    "[img src=\"https://assets.example/cube.png\" alt=\"cube\"] while "
    "[Katex]x^2[/Katex] is notation; see "
    "https://example.edu/wiki/Function_(mathematics)"
)
_DESCRIPTIVE_DISPLAY = (
    "A square is flat and a cube occupies space. QINV-0002 "
    "[img src=\"https://assets.example/square-cube.png\" "
    "alt=\"square and cube\"] [Katex]a^3[/Katex] "
    "https://example.edu/dimensions"
)


def _payload() -> dict:
    """A complete, mechanically valid staged assessment release."""

    chapter = {
        "chapter_title": (
            "Three-Dimensional Shapes "
            "(06_Mathematics_MSBSHSE_Balbharati)"
        ),
        "chapter_display_name": "Three-Dimensional Shapes",
        "pre_topics": "",
        "post_topics": "Dimensions",
        "chapter_description": "Flat and solid shapes.",
    }
    concept = {
        "concept_key": CONCEPT_KEY,
        "concept_machine_id": MACHINE_ID,
        "concept_title": f"Shape dimensions ({MACHINE_ID})",
        "concept_display_name": "Shape dimensions",
        "parent_concept": "",
        "concept_details": "Description: Flat and solid shapes.",
        "keywords": "flat; solid; dimensions",
        "related_concepts": "",
        "digicards": "",
        "concept_source": "Balbharati",
    }
    concept_snapshot = {
        "source_concept_release_sha256": "s" * 64,
        "target_chapter_id": 1,
        "concept_provenance": [{
            "concept_key": CONCEPT_KEY,
            "release_row_identity": {"position": 1},
        }],
        "chapter": chapter,
        "topics": [{
            "topic_title": f"Dimensions ({MACHINE_ID}_PL)",
            "topic_display_name": "Dimensions",
            "pre_post_learning": "Post",
            "topic_concept_labels": "",
            "related_topics": "",
            "topic_description": "Dimensions distinguish shapes.",
            "concepts": [concept],
        }],
    }
    source_atoms = [
        {
            "source_qid": "QINV-0001",
            "source_document_hash": "d" * 64,
            "source_kind": "exercise",
            "raw_text": "Which of these is a solid?",
            "normalized_public_text": "Which of these is a solid?",
            "assets": [{
                "url": "https://assets.example/cube.png",
                "alt": "cube",
            }],
        },
        {
            "source_qid": "QINV-0002",
            "source_document_hash": "d" * 64,
            "source_kind": "exercise",
            "raw_text": "Explain how a square differs from a cube.",
            "normalized_public_text": (
                "Explain how a square differs from a cube."
            ),
            "assets": [{
                "url": "https://assets.example/square-cube.png",
                "alt": "square and cube",
            }],
        },
    ]
    cells = [
        {
            "cell_id": "CELL-OBJECTIVE",
            "sheet_kind": "objective",
            "question_category": "Multiple Choice Question",
            "cognitive_skill": "Remember",
            "difficulty": "Less",
            "marks": 1.0,
            "count": 1,
            "appears_in": "Pre/Post-Worksheet/Test",
            "source_policy": "recorded fixture",
        },
        {
            "cell_id": "CELL-DESCRIPTIVE",
            "sheet_kind": "descriptive",
            "question_category": "Long Answer Type (4 Marks)",
            "cognitive_skill": "Understand",
            "difficulty": "Moderate",
            "marks": 4.0,
            "count": 1,
            "appears_in": "Pre/Post-Worksheet/Test",
            "source_policy": "recorded fixture",
        },
    ]
    candidates = [
        {
            "candidate_id": OBJECTIVE_ID,
            "question_label": f"{MACHINE_ID} Q01",
            "source_atom_ids": ["QINV-0001"],
            "blueprint_cell_id": "CELL-OBJECTIVE",
            "sheet_kind": "objective",
            "question_category": "Multiple Choice Question",
            "cognitive_skill": "Remember",
            "difficulty": "Less",
            "marks": 1.0,
            "question_duration": 1.0,
            "math_keyboard": "",
            "question_appears_in": "Pre/Post-Worksheet/Test",
            "answer_restriction": "Specific",
            "restriction_reason": "Only one option is a solid.",
            "question": "Which of these is a solid?",
            "question_text": "Which of these is a solid?",
            "display_answer": "",
            "answers": [
                {
                    "answer_type": "Phrases",
                    "answer_content": "Cube",
                    "correct_answer": "Yes",
                    "answer_weightage": "1",
                },
                {
                    "answer_type": "Phrases",
                    "answer_content": "Circle",
                    "correct_answer": "No",
                    "answer_weightage": "0",
                },
            ],
            "sub_questions": [],
            "answer_explanation": _OBJECTIVE_EXPLANATION,
            "assets": copy.deepcopy(source_atoms[0]["assets"]),
            "concept_id": CONCEPT_KEY,
            "concept_key": CONCEPT_KEY,
            "group_key": BASIC_GROUP,
            "flags": [],
        },
        {
            "candidate_id": DESCRIPTIVE_ID,
            "question_label": f"{MACHINE_ID} Q02",
            "source_atom_ids": ["QINV-0002"],
            "blueprint_cell_id": "CELL-DESCRIPTIVE",
            "sheet_kind": "descriptive",
            "question_category": "Long Answer Type (4 Marks)",
            "cognitive_skill": "Understand",
            "difficulty": "Moderate",
            "marks": 4.0,
            "question_duration": 6.0,
            "math_keyboard": "No",
            "question_appears_in": "Pre/Post-Worksheet/Test",
            "answer_restriction": "Open",
            "restriction_reason": "Equivalent explanations are valid.",
            "question": "Explain how a square differs from a cube.",
            "question_text": "Explain how a square differs from a cube.",
            "display_answer": _DESCRIPTIVE_DISPLAY,
            # Multipart scoring lives only in the subquestion keyword
            # rubrics. Main answer blocks would duplicate the same marks.
            "answers": [],
            "sub_questions": [
                {
                    "text": "State the dimensions of a square.",
                    "marks": "2",
                    "keywords": [{
                        "answer_type": "Phrases",
                        "weightage": "2",
                        "keyword": "[content]: two dimensions",
                    }],
                },
                {
                    "text": "State the dimensions of a cube.",
                    "marks": "2",
                    "keywords": [{
                        "answer_type": "Phrases",
                        "weightage": "2",
                        "keyword": "[content]: three dimensions",
                    }],
                },
            ],
            "answer_explanation": "Compare flat extent with occupied space.",
            "assets": copy.deepcopy(source_atoms[1]["assets"]),
            "concept_id": CONCEPT_KEY,
            "concept_key": CONCEPT_KEY,
            "group_key": INTERMEDIATE_GROUP,
            "flags": [],
        },
    ]
    groups = [
        {
            "group_key": BASIC_GROUP,
            "concept_id": CONCEPT_KEY,
            "concept_key": CONCEPT_KEY,
            "group_type": "Basic",
            "group_sequence": 1,
            "group_name": "Shape dimensions — Basic",
            "group_display_name": "Shape dimensions — Basic",
            "family": "solid recognition",
            "semantic_fingerprint": "f" * 64,
            "semantic_description": "Recognising a solid from named shapes.",
            "member_candidate_ids": [OBJECTIVE_ID],
            "group_status": "Active",
            "flags": [],
            "authority": {"decision_key": "group-basic"},
        },
        {
            "group_key": INTERMEDIATE_GROUP,
            "concept_id": CONCEPT_KEY,
            "concept_key": CONCEPT_KEY,
            "group_type": "Intermediate",
            "group_sequence": 1,
            "group_name": "Shape dimensions — Intermediate",
            "group_display_name": "Shape dimensions — Intermediate",
            "family": "dimension comparison",
            "semantic_fingerprint": "g" * 64,
            "semantic_description": "Explaining the dimensions of shapes.",
            "member_candidate_ids": [DESCRIPTIVE_ID],
            "group_status": "Active",
            "flags": [],
            "authority": {"decision_key": "group-intermediate"},
        },
    ]
    placements = [
        {
            "candidate_id": OBJECTIVE_ID,
            "concept_id": CONCEPT_KEY,
            "concept_key": CONCEPT_KEY,
            "group_key": BASIC_GROUP,
            "evidence": "The item assesses solid recognition.",
            "secondary_placements": [],
        },
        {
            "candidate_id": DESCRIPTIVE_ID,
            "concept_id": CONCEPT_KEY,
            "concept_key": CONCEPT_KEY,
            "group_key": INTERMEDIATE_GROUP,
            "evidence": "The item assesses dimensional comparison.",
            "secondary_placements": [],
        },
    ]
    return {
        "source_concept_release_sha256": "s" * 64,
        "concept_snapshot": concept_snapshot,
        "source_atoms": source_atoms,
        "blueprint_cells": cells,
        "candidates": candidates,
        "groups": groups,
        "placements": placements,
    }


def _payload_with_subjective() -> dict:
    payload = _payload()
    source_qid = "QINV-0003"
    payload["source_atoms"].append({
        "source_qid": source_qid,
        "source_document_hash": "d" * 64,
        "source_kind": "exercise",
        "raw_text": "Complete: A cube is a ____ shape.",
        "normalized_public_text": "Complete: A cube is a ____ shape.",
        "assets": [],
    })
    payload["blueprint_cells"].append({
        "cell_id": "CELL-SUBJECTIVE",
        "sheet_kind": "subjective",
        "question_category": "Fill in the blanks",
        "cognitive_skill": "Remember",
        "difficulty": "Less",
        "marks": 1.0,
        "count": 1,
        "appears_in": "Pre/Post-Worksheet/Test",
        "source_policy": "recorded fixture",
    })
    payload["candidates"].append({
        "candidate_id": SUBJECTIVE_ID,
        "question_label": f"{MACHINE_ID} Q03",
        "source_atom_ids": [source_qid],
        "blueprint_cell_id": "CELL-SUBJECTIVE",
        "sheet_kind": "subjective",
        "question_category": "Fill in the blanks",
        "cognitive_skill": "Remember",
        "difficulty": "Less",
        "marks": 1.0,
        "question_duration": 1.0,
        "math_keyboard": "No",
        "question_appears_in": "Pre/Post-Worksheet/Test",
        "answer_restriction": "Specific",
        "restriction_reason": "The source sentence has one expected word.",
        "question": "Complete: A cube is a $$a$$ shape.",
        "question_text": "Complete: A cube is a $$a$$ shape.",
        "display_answer": "",
        "answers": [{
            "answer_type": "Phrases",
            "answer_content": "solid",
            "answer_display": "solid",
            "answer_weightage": "1",
            "placeholder": "a",
        }],
        "sub_questions": [],
        "answer_explanation": "A cube occupies three-dimensional space.",
        "assets": [],
        "concept_id": CONCEPT_KEY,
        "concept_key": CONCEPT_KEY,
        "group_key": BASIC_GROUP,
        "flags": [],
    })
    payload["groups"][0]["member_candidate_ids"].append(SUBJECTIVE_ID)
    payload["placements"].append({
        "candidate_id": SUBJECTIVE_ID,
        "concept_id": CONCEPT_KEY,
        "concept_key": CONCEPT_KEY,
        "group_key": BASIC_GROUP,
        "evidence": "The item assesses recognition of a solid shape.",
        "secondary_placements": [],
    })
    return payload


def _verified_critic(_payload: dict) -> dict:
    return {"verdict": "verified", "confidence": 1.0, "issues": []}


def _proposal(
    request: dict,
    mutate: Callable[[dict, dict], None] | None = None,
    *,
    rationale: str = "Polished the released prose without changing meaning.",
) -> dict:
    kind = request["unit_kind"]
    record = copy.deepcopy(request[kind])
    if mutate is not None:
        mutate(record, request)
    return {
        "record_kind": kind,
        "row_ref": request["row_ref"],
        "record": record,
        "rationale": rationale,
    }


def _refine(
    payload: dict,
    provider,
    *,
    critic=_verified_critic,
    store: kernel.DecisionStore | None = None,
    fixer=None,
):
    return refiner.refine_master(
        [payload],
        metadata=_METADATA,
        provider=provider,
        critic=critic,
        store=store or kernel.DecisionStore(),
        envelope_sha256=ENVELOPE_SHA256,
        fixer=fixer,
    )


def _unit(payload: dict, unit_id: str) -> dict:
    for candidate in payload["candidates"]:
        if candidate.get("candidate_id") == unit_id:
            return candidate
    for group in payload["groups"]:
        if group.get("group_key") == unit_id:
            return group
    raise AssertionError(f"missing unit {unit_id}")


def _without_refiner_bookkeeping(payload: dict) -> dict:
    cleaned = copy.deepcopy(payload)
    cleaned.pop("refinements", None)
    for collection in ("candidates", "groups"):
        for record in cleaned[collection]:
            record.pop(refiner.AUDIT_FIELD, None)
            record["flags"] = [
                flag for flag in record.get("flags") or []
                if flag != refiner.WARNING
            ]
    return cleaned


def _rendered_rows(payload: dict) -> dict[str, dict]:
    snapshot = release_service.snapshot_from_staged_release(payload)
    profile = assessment_profile.resolve_for_metadata(
        _METADATA["profile"], _METADATA,
    )
    built = aw.build_dual_output(snapshot, profile)
    assert built["valid"], built["manifest"]["read_back"]
    parsed = aw.parse_workbook(built["master_xlsx"])
    return {
        str(row.get("question_label") or ""): row
        for sheet in aw.sheet_for_kind().values()
        for row in parsed["sheets"][sheet]["rows"]
        if str(row.get("question_label") or "")
    }


def test_fixture_exercises_the_real_release_and_workbook_contracts():
    payload = _payload()
    assert rel.freeze_payload(payload)["errors"] == []
    rows = _rendered_rows(payload)
    assert list(rows) == [
        f"{MACHINE_ID} Q01",
        f"{MACHINE_ID} Q02",
    ]


def test_subjective_placeholder_and_answer_column_round_trip_through_refiner():
    payload = _payload_with_subjective()
    profile = assessment_profile.resolve_for_metadata(
        _METADATA["profile"], _METADATA,
    )
    assert rel.freeze_payload(payload, profile)["errors"] == []

    def provider(request):
        def polish(record, _request):
            if record.get("candidate_id") == SUBJECTIVE_ID:
                record["answers"][0]["answer_content"] = "Solid"

        return _proposal(request, polish)

    records, diff, flags = _refine(payload, provider)
    assert flags == []
    assert any(
        change["unit_id"] == SUBJECTIVE_ID for change in diff["changes"]
    )
    subjective = _unit(records[0], SUBJECTIVE_ID)
    assert subjective[refiner.AUDIT_FIELD]["status"] == "applied"
    row = _rendered_rows(records[0])[f"{MACHINE_ID} Q03"]
    assert row["question"] == "Complete: A cube is a $$a$$ shape."
    assert row["placeholder_1"] == "a"
    assert row["answer_1"] == "Solid"
    assert "answer_content_1" not in row


def test_empty_required_group_shell_is_omitted_and_recorded():
    payload = _payload()
    snapshot = release_service.snapshot_from_staged_release(payload)
    built = aw.build_dual_output(snapshot, _METADATA["profile"])
    assert built["valid"], built["manifest"]["read_back"]
    assert built["manifest"]["issues"]["omitted_empty_group_shells"] == [
        ADVANCED_GROUP
    ]

    state = refiner._validation_state(
        payload, assessment_profile.resolve(_METADATA["profile"])
    )
    assert state["errors"] == []
    assert ADVANCED_GROUP not in state["group_rows"]


def test_refiner_checker_uses_exclusive_multipart_scoring_contract():
    multipart = copy.deepcopy(_payload()["candidates"][1])
    checker = refiner._response_checker(
        unit_kind="candidate",
        unit_id=DESCRIPTIVE_ID,
        original=multipart,
    )
    valid_response = {
        "record_kind": "candidate",
        "row_ref": DESCRIPTIVE_ID,
        "record": multipart,
        "rationale": "The multipart rubric is already clear.",
    }
    assert checker(valid_response) == []

    duplicated = copy.deepcopy(multipart)
    duplicated["answers"] = [{
        "answer_type": "Phrases",
        "answer_content": "Duplicated main rubric.",
        "answer_weightage": "4",
    }]
    duplicate_checker = refiner._response_checker(
        unit_kind="candidate",
        unit_id=DESCRIPTIVE_ID,
        original=duplicated,
    )
    duplicate_response = {**valid_response, "record": duplicated}
    assert any(
        "subquestion keyword rubrics exclusively" in defect
        for defect in duplicate_checker(duplicate_response)
    )

    single_part = copy.deepcopy(multipart)
    single_part["sub_questions"] = []
    single_part["answers"] = [{
        "answer_type": "Phrases",
        "answer_content": "One undivided rubric block.",
        "answer_weightage": "4",
    }]
    single_checker = refiner._response_checker(
        unit_kind="candidate",
        unit_id=DESCRIPTIVE_ID,
        original=single_part,
    )
    single_response = {**valid_response, "record": single_part}
    assert any(
        "single-part 4-mark descriptive" in defect
        for defect in single_checker(single_response)
    )


def test_candidate_and_group_prose_refinements_land_and_read_back_exactly():
    original = _payload()
    input_payload = copy.deepcopy(original)
    calls = []

    def provider(request):
        calls.append(request["row_ref"])

        def polish(record, _request):
            if record.get("candidate_id") == OBJECTIVE_ID:
                record["answers"][0]["answer_content"] = "A cube"
                record["answer_explanation"] = (
                    _OBJECTIVE_EXPLANATION.replace(
                        "identifies the cube", "clearly identifies the cube"
                    )
                )
            elif record.get("candidate_id") == DESCRIPTIVE_ID:
                record["display_answer"] = _DESCRIPTIVE_DISPLAY.replace(
                    "A square is flat", "The square is flat"
                )
                record["sub_questions"][0]["keywords"][0]["keyword"] = (
                    "[content]: two spatial dimensions"
                )
            elif record.get("group_key") == BASIC_GROUP:
                record["semantic_description"] = (
                    "Recognising three-dimensional solids from named shapes."
                )
            elif record.get("group_key") == INTERMEDIATE_GROUP:
                record["semantic_description"] = (
                    "Explaining how two- and three-dimensional shapes differ."
                )

        return _proposal(request, polish)

    records, diff, flags = _refine(input_payload, provider)
    assert input_payload == original  # the caller's release is never mutated
    assert len(records) == 1
    refined = records[0]
    # Two waves with a barrier between them: every candidate decision
    # completes before any group decision starts. WITHIN a wave the units
    # run on the decision-worker pool, so their relative order is thread
    # scheduling, not contract — asserting it made this test flake under
    # full-suite load.
    assert sorted(calls[:2]) == sorted([OBJECTIVE_ID, DESCRIPTIVE_ID])
    assert sorted(calls[2:]) == sorted([BASIC_GROUP, INTERMEDIATE_GROUP])
    assert flags == []
    assert diff["policy_version"] == refiner.MASTER_REFINER_POLICY_VERSION
    assert diff["output_kind"] == "assessment_master"
    assert refined["refinements"] == diff
    assert [change["unit_id"] for change in diff["changes"]] == [
        OBJECTIVE_ID,
        OBJECTIVE_ID,
        DESCRIPTIVE_ID,
        DESCRIPTIVE_ID,
        BASIC_GROUP,
        INTERMEDIATE_GROUP,
    ]
    assert all(
        _unit(refined, unit_id)[refiner.AUDIT_FIELD]["status"] == "applied"
        for unit_id in (
            OBJECTIVE_ID,
            DESCRIPTIVE_ID,
            BASIC_GROUP,
            INTERMEDIATE_GROUP,
        )
    )

    rows = _rendered_rows(refined)
    objective = rows[f"{MACHINE_ID} Q01"]
    descriptive = rows[f"{MACHINE_ID} Q02"]
    assert objective["question"] == original["candidates"][0]["question"]
    # The rendered Objective question_text is the stem plus the lettered
    # options (owner ruling, 2026-08-21); the refiner still may not touch
    # the underlying stem, which the ``question`` equality above pins.
    assert objective["question_text"].startswith(
        original["candidates"][0]["question_text"]
    )
    assert "a) A cube" in objective["question_text"]
    assert objective["answer_content_1"] == "A cube"
    assert objective["answer_explanation"] == refined["candidates"][0][
        "answer_explanation"
    ]
    assert descriptive["display_answer"] == refined["candidates"][1][
        "display_answer"
    ]
    assert descriptive["question_text"] == (
        original["candidates"][1]["question_text"]
    )
    assert descriptive["sq1_keyword_1"] == (
        "[content]: two spatial dimensions"
    )


def _question_drift(record, _request):
    record["question"] = "Which shape is solid?"


def _question_text_drift(record, _request):
    record["question_text"] = "Which shape is solid?"


def _candidate_identity_drift(record, _request):
    record["candidate_id"] = "CAND-REPLACEMENT"


def _numeric_type_drift(record, _request):
    record["marks"] = 1


def _answer_weight_type_drift(record, _request):
    record["answers"][0]["answer_weightage"] = 1


def _correct_option_drift(record, _request):
    record["answers"][0]["correct_answer"] = "No"


def _restriction_drift(record, _request):
    record["answer_restriction"] = "Open"


def _asset_drift(record, _request):
    record["assets"][0]["url"] = "https://assets.example/replacement.png"


def _decomposition_drift(record, _request):
    record["sub_questions"].append({
        "text": "Added by the Refiner",
        "marks": "1",
        "keywords": [],
    })


def _keyword_weight_drift(record, _request):
    record["sub_questions"][0]["keywords"][0]["weightage"] = "1"


def _group_identity_type_drift(record, _request):
    record["group_sequence"] = 1.0


def _membership_drift(record, _request):
    record["member_candidate_ids"] = [DESCRIPTIVE_ID]


def _qid_token_drift(record, _request):
    record["answer_explanation"] = record["answer_explanation"].replace(
        "QINV-0001", "QINV-0099"
    )


def _url_token_drift(record, _request):
    record["answer_explanation"] = record["answer_explanation"].replace(
        "https://example.edu/wiki/Function_(mathematics)",
        "https://example.edu/wiki/Function_(mathematics",
    )


def _image_token_drift(record, _request):
    record["answer_explanation"] = record["answer_explanation"].replace(
        "alt=\"cube\"", "alt=\"solid cube\""
    )


def _katex_token_drift(record, _request):
    record["answer_explanation"] = record["answer_explanation"].replace(
        "[Katex]x^2[/Katex]", "[Katex]x^3[/Katex]"
    )


def _token_path_migration(record, _request):
    token = "https://example.edu/wiki/Function_(mathematics)"
    record["answer_explanation"] = record["answer_explanation"].replace(
        token, ""
    )
    record["answers"][0]["answer_content"] += " " + token


def _erase_answer_prose(record, _request):
    record["answers"][0]["answer_content"] = "   "


def _erase_answer_with_format_character(record, _request):
    record["answers"][0]["answer_content"] = "\u200b"


def _erase_group_with_nonspacing_mark(record, _request):
    record["semantic_description"] = "\u0301"


def _erase_answer_with_invisible_fillers(record, _request):
    record["answers"][0]["answer_content"] = "\u2800\u115f\u1160\u3164\uffa0"


@pytest.mark.parametrize(
    ("unit_id", "mutator"),
    [
        pytest.param(OBJECTIVE_ID, _question_drift, id="question"),
        pytest.param(OBJECTIVE_ID, _question_text_drift, id="question-text"),
        pytest.param(
            OBJECTIVE_ID, _candidate_identity_drift, id="candidate-id"
        ),
        pytest.param(OBJECTIVE_ID, _numeric_type_drift, id="numeric-type"),
        pytest.param(
            OBJECTIVE_ID, _answer_weight_type_drift, id="answer-weight-type"
        ),
        pytest.param(
            OBJECTIVE_ID, _correct_option_drift, id="correct-option"
        ),
        pytest.param(OBJECTIVE_ID, _restriction_drift, id="restriction"),
        pytest.param(OBJECTIVE_ID, _asset_drift, id="asset"),
        pytest.param(
            DESCRIPTIVE_ID, _decomposition_drift, id="subquestion-structure"
        ),
        pytest.param(
            DESCRIPTIVE_ID, _keyword_weight_drift, id="keyword-weight"
        ),
        pytest.param(
            BASIC_GROUP, _group_identity_type_drift, id="group-type-only"
        ),
        pytest.param(BASIC_GROUP, _membership_drift, id="membership"),
        pytest.param(OBJECTIVE_ID, _qid_token_drift, id="qid-token"),
        pytest.param(OBJECTIVE_ID, _url_token_drift, id="url-token"),
        pytest.param(OBJECTIVE_ID, _image_token_drift, id="image-token"),
        pytest.param(OBJECTIVE_ID, _katex_token_drift, id="katex-token"),
        pytest.param(
            OBJECTIVE_ID, _token_path_migration, id="token-path-migration"
        ),
        pytest.param(
            OBJECTIVE_ID, _erase_answer_prose, id="erased-answer-prose"
        ),
        pytest.param(
            OBJECTIVE_ID,
            _erase_answer_with_format_character,
            id="zero-width-answer-prose",
        ),
        pytest.param(
            BASIC_GROUP,
            _erase_group_with_nonspacing_mark,
            id="nonspacing-group-prose",
        ),
        pytest.param(
            OBJECTIVE_ID,
            _erase_answer_with_invisible_fillers,
            id="invisible-filler-answer-prose",
        ),
    ],
)
def test_locked_identity_or_token_drift_discards_the_whole_unit_proposal(
    unit_id,
    mutator,
):
    original = _payload()

    def provider(request):
        chosen = mutator if request["row_ref"] == unit_id else None
        return _proposal(request, chosen)

    records, diff, flags = _refine(copy.deepcopy(original), provider)
    refined = records[0]
    assert _without_refiner_bookkeeping(refined) == original
    assert diff["changes"] == []
    unit = _unit(refined, unit_id)
    assert refiner.WARNING in unit["flags"]
    assert unit[refiner.AUDIT_FIELD]["status"] in {
        "unavailable",
        "rolled_back",
    }
    assert flags


def test_critic_dissent_is_advisory_and_the_valid_refinement_still_ships():
    def provider(request):
        def polish(record, _request):
            if record.get("candidate_id") == OBJECTIVE_ID:
                record["answers"][0]["answer_content"] = "A solid cube"

        return _proposal(request, polish)

    def dissenting_critic(_request):
        return {
            "verdict": "dissent",
            "confidence": 0.51,
            "issues": ["A reviewer should inspect the tone."],
        }

    records, diff, flags = _refine(
        _payload(), provider, critic=dissenting_critic
    )
    refined = records[0]
    objective = _unit(refined, OBJECTIVE_ID)
    assert objective["answers"][0]["answer_content"] == "A solid cube"
    assert any(
        change["unit_id"] == OBJECTIVE_ID for change in diff["changes"]
    )
    assert refiner.WARNING in objective["flags"]
    assert objective[refiner.AUDIT_FIELD]["review_flags"]
    assert flags


def test_provider_failure_is_per_unit_nonblocking_and_keeps_authored_rows():
    original = _payload()
    calls = []

    def unavailable(request):
        calls.append(request["row_ref"])
        raise RuntimeError("provider offline")

    records, diff, flags = _refine(copy.deepcopy(original), unavailable)
    refined = records[0]
    assert _without_refiner_bookkeeping(refined) == original
    assert diff["changes"] == []
    # Two waves with a barrier between them: every candidate decision
    # completes before any group decision starts. WITHIN a wave the units
    # run on the decision-worker pool, so their relative order is thread
    # scheduling, not contract — asserting it made this test flake under
    # full-suite load.
    assert sorted(calls[:2]) == sorted([OBJECTIVE_ID, DESCRIPTIVE_ID])
    assert sorted(calls[2:]) == sorted([BASIC_GROUP, INTERMEDIATE_GROUP])
    for record in [*refined["candidates"], *refined["groups"]]:
        assert refiner.WARNING in record["flags"]
        assert record[refiner.AUDIT_FIELD]["status"] == "unavailable"
    assert flags


def test_invalid_baseline_is_zero_spend_and_stages_every_unit_unrefined():
    original = _payload()
    original["candidates"][0]["answers"][0]["answer_weightage"] = "0"

    def forbidden(_request):
        raise AssertionError("an invalid baseline must not spend")

    records, diff, flags = _refine(original, forbidden)
    refined = records[0]
    assert _without_refiner_bookkeeping(refined) == original
    assert diff["changes"] == []
    assert flags
    for record in [*refined["candidates"], *refined["groups"]]:
        assert refiner.WARNING in record["flags"]
        assert record[refiner.AUDIT_FIELD]["status"] == "unavailable"


def test_fixer_uses_the_same_contract_and_its_valid_decision_ships_flagged():
    provider_calls = Counter()
    fixer_calls = []

    def provider(request):
        provider_calls[request["row_ref"]] += 1
        if request["row_ref"] == OBJECTIVE_ID:
            return {"record_kind": "candidate", "row_ref": OBJECTIVE_ID}
        return _proposal(request)

    def fixer(request):
        original_request = request["original_payload"]
        fixer_calls.append(original_request["row_ref"])

        def polish(record, _request):
            record["answers"][0]["answer_content"] = "The cube"

        return _proposal(original_request, polish, rationale="Fixer repair.")

    records, diff, flags = _refine(
        _payload(), provider, fixer=fixer
    )
    objective = _unit(records[0], OBJECTIVE_ID)
    assert provider_calls[OBJECTIVE_ID] == kernel.MAX_ATTEMPTS
    assert fixer_calls == [OBJECTIVE_ID]
    assert objective["answers"][0]["answer_content"] == "The cube"
    assert objective[refiner.AUDIT_FIELD]["fixer"] is True
    assert refiner.WARNING in objective["flags"]
    assert any(
        change["unit_id"] == OBJECTIVE_ID for change in diff["changes"]
    )
    assert flags


def test_renderer_exception_rolls_back_but_retains_fixer_decision_authority():
    store = kernel.DecisionStore()

    def provider(request):
        if request["row_ref"] == OBJECTIVE_ID:
            return {"record_kind": "candidate", "row_ref": OBJECTIVE_ID}
        return _proposal(request)

    def fixer(request):
        original_request = request["original_payload"]

        def too_large(record, _request):
            record["answers"][0]["answer_content"] = "x" * 40_000

        return _proposal(original_request, too_large, rationale="Fixer prose.")

    def dissent(_request):
        return {
            "verdict": "dissent",
            "confidence": 0.8,
            "issues": ["Review the Fixer-authored prose."],
        }

    original = _payload()
    records, diff, flags = _refine(
        copy.deepcopy(original),
        provider,
        critic=dissent,
        store=store,
        fixer=fixer,
    )
    objective = _unit(records[0], OBJECTIVE_ID)
    assert objective["answers"][0]["answer_content"] == (
        original["candidates"][0]["answers"][0]["answer_content"]
    )
    audit = objective[refiner.AUDIT_FIELD]
    assert audit["status"] == "rolled_back"
    assert audit["decision_key"] in store.keys()
    assert audit["fixer"] is True
    assert any("fixer:" in flag for flag in audit["review_flags"])
    assert any("critic:" in flag for flag in audit["review_flags"])
    assert diff["changes"] == []
    assert refiner.WARNING in objective["flags"]
    assert flags


def test_decide_once_replay_makes_zero_author_critic_or_fixer_calls():
    store = kernel.DecisionStore()
    calls = Counter()

    def provider(request):
        calls["provider"] += 1

        def polish(record, _request):
            if record.get("candidate_id") == OBJECTIVE_ID:
                record["answers"][0]["answer_content"] = "A cube"

        return _proposal(request, polish)

    def critic(_request):
        calls["critic"] += 1
        return _verified_critic(_request)

    def fixer(_request):
        calls["fixer"] += 1
        raise AssertionError("a valid first decision must not use the Fixer")

    first = _refine(
        _payload(), provider, critic=critic, store=store, fixer=fixer
    )
    first_counts = calls.copy()
    assert first_counts == {"provider": 4, "critic": 4}
    assert len(store.keys()) == 4

    def forbidden(_request):
        raise AssertionError("replay attempted external spend")

    second = _refine(
        _payload(), forbidden, critic=forbidden, store=store, fixer=forbidden
    )
    assert second == first
    assert calls == first_counts


def test_envelope_and_bound_identity_changes_rekey_without_overwriting():
    store = kernel.DecisionStore()

    def provider(request):
        return _proposal(request)

    _refine(_payload(), provider, store=store)
    first_keys = set(store.keys())
    assert len(first_keys) == 4

    changed = _payload()
    changed["candidates"][0]["assets"][0]["alt"] = "solid cube"
    _refine(changed, provider, store=store)
    after_identity = set(store.keys())
    assert first_keys < after_identity

    refiner.refine_master(
        [_payload()],
        metadata=_METADATA,
        provider=provider,
        critic=_verified_critic,
        store=store,
        envelope_sha256="f" * 64,
    )
    assert after_identity < set(store.keys())


def test_model_evidence_recursively_excludes_printer_positions():
    payload = _payload()
    payload["candidates"][0]["source_page"] = 12
    payload["candidates"][0]["source_position"] = 13
    payload["candidates"][0]["answers"][0]["bbox"] = [1, 2, 3, 4]
    payload["candidates"][0]["answers"][0]["_source_position"] = 14
    payload["groups"][0]["row_number"] = 9
    payload["groups"][0]["reading_order"] = 10
    metadata = {
        **_METADATA,
        "printer_page": 44,
        "_source_position": 45,
    }
    requests = []

    def provider(request):
        requests.append(copy.deepcopy(request))
        return _proposal(request)

    records, _diff, _flags = refiner.refine_master(
        [payload],
        metadata=metadata,
        provider=provider,
        critic=_verified_critic,
        store=kernel.DecisionStore(),
        envelope_sha256=ENVELOPE_SHA256,
    )
    assert len(requests) == 4

    def assert_no_positions(value):
        if isinstance(value, dict):
            assert not set(value) & refiner._PRINT_POSITION_FIELDS
            for nested in value.values():
                assert_no_positions(nested)
        elif isinstance(value, list):
            for nested in value:
                assert_no_positions(nested)

    for request in requests:
        assert_no_positions(request)
    assert records[0]["candidates"][0]["source_page"] == 12
    assert records[0]["candidates"][0]["source_position"] == 13
    assert records[0]["candidates"][0]["answers"][0]["bbox"] == [1, 2, 3, 4]
    assert records[0]["candidates"][0]["answers"][0]["_source_position"] == 14
    assert records[0]["groups"][0]["row_number"] == 9
    assert records[0]["groups"][0]["reading_order"] == 10


def test_real_xlsx_readback_rollback_is_isolated_and_order_is_preserved():
    """Excel normalises carriage returns; exact read-back must catch it."""

    original = _payload()
    order = []
    normalised_by_xlsx = "polished\rprose"

    def provider(request):
        order.append(request["row_ref"])

        def polish(record, _request):
            if record.get("candidate_id") == OBJECTIVE_ID:
                record["answers"][0]["answer_content"] = normalised_by_xlsx
            elif record.get("candidate_id") == DESCRIPTIVE_ID:
                record["sub_questions"][0]["keywords"][0]["keyword"] = (
                    "[content]: two-dimensional extent"
                )
            elif record.get("group_key") == BASIC_GROUP:
                record["semantic_description"] = normalised_by_xlsx
            elif record.get("group_key") == INTERMEDIATE_GROUP:
                record["semantic_description"] = (
                    "Explaining the dimensional contrast between shapes."
                )

        return _proposal(request, polish)

    records, diff, flags = _refine(copy.deepcopy(original), provider)
    refined = records[0]
    # Wave barrier only: candidates complete before groups start; order
    # WITHIN a wave is pool scheduling, not contract (same reasoning as
    # the provider-failure test above).
    assert sorted(order[:2]) == sorted([OBJECTIVE_ID, DESCRIPTIVE_ID])
    assert sorted(order[2:]) == sorted([BASIC_GROUP, INTERMEDIATE_GROUP])
    assert _unit(refined, OBJECTIVE_ID)["answers"][0]["answer_content"] == (
        original["candidates"][0]["answers"][0]["answer_content"]
    )
    assert _unit(refined, BASIC_GROUP)["semantic_description"] == (
        original["groups"][0]["semantic_description"]
    )
    assert _unit(refined, DESCRIPTIVE_ID)["sub_questions"][0][
        "keywords"
    ][0]["keyword"] == "[content]: two-dimensional extent"
    assert _unit(refined, INTERMEDIATE_GROUP)["semantic_description"] == (
        "Explaining the dimensional contrast between shapes."
    )
    assert [change["unit_id"] for change in diff["changes"]] == [
        DESCRIPTIVE_ID,
        INTERMEDIATE_GROUP,
    ]
    for unit_id in (OBJECTIVE_ID, BASIC_GROUP):
        unit = _unit(refined, unit_id)
        assert refiner.WARNING in unit["flags"]
        assert unit[refiner.AUDIT_FIELD]["status"] == "rolled_back"
    assert flags


def test_candidate_refiner_checker_keeps_declared_answer_medium_pure():
    original = _payload()["candidates"][0]
    proposal = copy.deepcopy(original)
    proposal["answers"][0]["answer_content"] = "[Katex] Cube [/Katex]"
    checker = refiner._response_checker(
        unit_kind="candidate",
        unit_id=OBJECTIVE_ID,
        original=original,
    )

    defects = checker({
        "record_kind": "candidate",
        "row_ref": OBJECTIVE_ID,
        "record": proposal,
        "rationale": "Polish attempted.",
    })

    assert any("phrases_katex" in defect for defect in defects)


def test_candidate_refiner_cannot_reintroduce_labels_or_malformed_tags():
    objective = _payload()["candidates"][0]
    labelled = copy.deepcopy(objective)
    labelled["answers"][0]["answer_content"] = "a) Cube"
    objective_checker = refiner._response_checker(
        unit_kind="candidate",
        unit_id=OBJECTIVE_ID,
        original=objective,
    )
    objective_defects = objective_checker({
        "record_kind": "candidate",
        "row_ref": OBJECTIVE_ID,
        "record": labelled,
        "rationale": "Polish attempted.",
    })
    assert any("repeats its letter label" in defect for defect in objective_defects)

    descriptive = copy.deepcopy(_payload()["candidates"][1])
    descriptive["sub_questions"] = []
    descriptive["answers"] = [{
        "answer_type": "Phrases",
        "answer_content": "[content]: correct comparison",
        "answer_weightage": "2",
    }, {
        "answer_type": "Phrases",
        "answer_content": "[method]: valid reasoning",
        "answer_weightage": "2",
    }]
    malformed = copy.deepcopy(descriptive)
    malformed["answers"][0]["answer_content"] = "[content] missing colon"
    descriptive_checker = refiner._response_checker(
        unit_kind="candidate",
        unit_id=DESCRIPTIVE_ID,
        original=descriptive,
    )
    descriptive_defects = descriptive_checker({
        "record_kind": "candidate",
        "row_ref": DESCRIPTIVE_ID,
        "record": malformed,
        "rationale": "Polish attempted.",
    })
    assert any(
        "without its required colon" in defect
        for defect in descriptive_defects
    )

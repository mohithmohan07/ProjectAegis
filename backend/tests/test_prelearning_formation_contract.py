"""Job 79: final Pre authority and duplicate-row recovery are model decisions."""
from __future__ import annotations

import copy

from app.services import prelearning_formation_contract as contract
from app.services.phase3 import envelope, kernel, prelearn, settle


def _env() -> dict:
    return {
        "envelope_sha256": "e" * 64,
        "metadata": {
            "subject": "English",
            "board": "Maharashtra",
            "grade": "06",
            "unit": "POEM",
            "chapter_title": "The School Bell Rings Again...",
            "instruction_slots": {
                "grade_band_vocabulary": "Use accessible Grade Six English.",
                "subject_topology_guidance": (
                    "Keep poem meaning, vocabulary, response tasks and poetic "
                    "device analysis distinct."
                ),
            },
        },
        "canonical": {
            "blocks": [
                {
                    "block_id": "BLK-TEACH",
                    "kind": "paragraph",
                    "display_text": (
                        "Alliteration repeats the same initial consonant sound."
                    ),
                },
                {
                    "block_id": "BLK-SOUND",
                    "kind": "paragraph",
                    "display_text": (
                        "Find more examples of alliteration from the poem."
                    ),
                },
                {
                    "block_id": "BLK-WARMUP",
                    "kind": "paragraph",
                    "display_text": "New things I want to learn this year.",
                },
                {
                    "block_id": "BLK-POEM",
                    "kind": "paragraph",
                    "display_text": "A new school year and a brand new start.",
                },
                {
                    "block_id": "BLK-RECITE",
                    "kind": "paragraph",
                    "display_text": "Read and recite.",
                },
                {
                    "block_id": "BLK-EFFORT",
                    "kind": "paragraph",
                    "display_text": "Our motto should be, Yes, we can!",
                },
            ],
        },
        "inventory": {
            "items": [
                {
                    "qid": "QINV-MCQ",
                    "source_kind": "checkpoint_question",
                    "raw_task": "Tick the correct answer.",
                }
            ],
        },
        "graph": {
            "topics": [
                {"topic_id": "TOPIC-1", "title": "Meaning and Progression"}
            ],
        },
    }


def _row(title: str, block_id: str, origin: str, *, details: str) -> dict:
    return {
        "topic": "Meaning and Progression",
        "parent_concept": "Meaning and Progression",
        "concept_title": title,
        "concept_details": details,
        "keywords": "school, learning",
        "_semantic_topic_id": "TOPIC-1",
        "_phase32_origin_concept_id": origin,
        "_phase32_segment_order": 1,
        "_source_block_ids": [block_id],
        "_semantic_subtopic_ids": ["SUB-1"],
        "_source_grounding_contract": "api-verified-source-block-ids",
    }


def test_final_prerequisite_adjudication_disposes_chapter_teaching_and_directions(
    monkeypatch,
):
    monkeypatch.setattr(envelope, "validate", lambda value: copy.deepcopy(value))
    merged = {
        "captures": {
            "settle": [{
                "prerequisite_id": "PR-0001",
                "text": "Understand alliteration.",
                "evidence": ["BLK-TEACH"],
                "rationale": "The poetic-device section uses it.",
            }],
            "host": [{
                "prerequisite_id": "PR-0001",
                "text": "Know how to tick one correct answer.",
                "evidence": ["QINV-MCQ"],
                "rationale": "The task uses a tick response.",
            }],
            "analyse": [{
                "prerequisite_id": "PR-0001",
                "text": "Hear the first consonant sound in a word.",
                "evidence": ["BLK-SOUND"],
                "rationale": "The alliteration explanation assumes sound position.",
            }],
        },
        "prerequisites": [{
            "prerequisite_id": "PR-0001",
            "text": "Alliteration and question-answering basics.",
            "captures": [
                "settle:PR-0001", "host:PR-0001", "analyse:PR-0001"
            ],
            "rationale": "Broad merge suggestion.",
        }],
        "review_flags": {},
        "stage_flags": {},
    }
    calls: list[dict] = []

    def provider(request):
        calls.append(request)
        return {
            "prerequisites": [{
                "prerequisite_id": "PR-0001",
                "text": "Recognise the initial consonant sound in a word.",
                "captures": ["analyse:PR-0001"],
                "rationale": (
                    "The chapter teaches alliteration but assumes awareness of "
                    "where a consonant sound occurs in a word."
                ),
            }],
            "dispositions": [
                {
                    "disposition_id": "PD-0001",
                    "classification": "chapter_taught",
                    "captures": ["settle:PR-0001"],
                    "rationale": "The chapter itself defines alliteration.",
                },
                {
                    "disposition_id": "PD-0002",
                    "classification": "instruction_supplied",
                    "captures": ["host:PR-0001"],
                    "rationale": "The printed direction supplies the response form.",
                },
            ],
        }

    result = contract.adjudicate_prerequisites(
        _env(), merged, provider=provider, store=kernel.DecisionStore()
    )

    assert len(calls) == 1
    assert calls[0]["stage"] == "prelearn.adjudicate"
    assert [row["text"] for row in result["prerequisites"]] == [
        "Recognise the initial consonant sound in a word."
    ]
    assert [row["classification"] for row in result["dispositions"]] == [
        "chapter_taught", "instruction_supplied"
    ]
    accounted = {
        ref
        for row in [*result["prerequisites"], *result["dispositions"]]
        for ref in row["captures"]
    }
    assert accounted == {
        "settle:PR-0001", "host:PR-0001", "analyse:PR-0001"
    }


def test_prerequisite_adjudication_contract_is_exact_once():
    checker = contract._prerequisite_checker({"settle:PR-0001", "host:PR-0001"})
    defects = checker({
        "prerequisites": [{
            "prerequisite_id": "PR-0001",
            "text": "One idea",
            "captures": ["settle:PR-0001"],
            "rationale": "Supported.",
        }],
        "dispositions": [{
            "disposition_id": "PD-0001",
            "classification": "chapter_taught",
            "captures": ["settle:PR-0001"],
            "rationale": "Duplicate accounting.",
        }],
    })
    assert any("more than once" in defect for defect in defects)
    assert any("unaccounted capture_ref" in defect for defect in defects)


def test_duplicate_activity_rows_are_reconciled_through_model_seam(
    monkeypatch,
):
    monkeypatch.setattr(envelope, "validate", lambda value: copy.deepcopy(value))
    duplicate_title = "A New School Year Brings a Learning Journey"
    rows = [
        _row(
            duplicate_title,
            "BLK-WARMUP",
            "ORIGIN-WARMUP",
            details=(
                "Description: Learners write goals for the new year.\n"
                "Achieving Mastery: Write learning goals."
            ),
        ),
        _row(
            duplicate_title,
            "BLK-POEM",
            "ORIGIN-POEM",
            details=(
                "Description: The poem moves from a fresh start to Grade Six "
                "learning.\nAchieving Mastery: Explain that progression."
            ),
        ),
        _row(
            "A Classroom Clan Says Yes, We Can!",
            "BLK-EFFORT",
            "ORIGIN-EFFORT",
            details=(
                "Description: The closing lines turn belonging into effort.\n"
                "Achieving Mastery: Explain the shared motto."
            ),
        ),
        _row(
            duplicate_title,
            "BLK-RECITE",
            "ORIGIN-RECITE",
            details=(
                "Description: Learners read and recite the poem.\n"
                "Achieving Mastery: Recite the poem."
            ),
        ),
        {
            "topic": "Meaning and Progression",
            "parent_concept": "Culmination",
            "concept_title": "Culmination - old members",
            "concept_details": "Description: Old consolidation.",
            "keywords": "old members",
            "_semantic_topic_id": "TOPIC-1",
            "_source_block_ids": ["BLK-POEM"],
            "_source_grounding_contract": (
                "derived-from-verified-topic-concepts"
            ),
        },
    ]
    identity_calls: list[dict] = []
    culmination_calls: list[dict] = []

    def identity_provider(request):
        identity_calls.append(request)
        return {
            "groups": [{
                "group_id": "DUP-0001",
                "concepts": [{
                    "source_row_ids": ["ROW-0002"],
                    "concept_title": duplicate_title,
                    "parent_concept": "From a fresh start to new learning",
                    "description": (
                        "The poem presents the new year as a shared beginning "
                        "and an exciting Grade Six learning journey."
                    ),
                    "achieving_mastery": (
                        "Explain how the poem moves from a fresh start to new "
                        "Grade Six learning."
                    ),
                    "keywords": "new school year, Grade Six, learning journey",
                    "rationale": "ROW-0002 contains the substantive teaching.",
                }],
                "dispositions": [
                    {
                        "classification": "activity_support",
                        "source_row_ids": ["ROW-0001"],
                        "attach_to_concept_title": duplicate_title,
                        "rationale": "The warm-up applies the concept personally.",
                    },
                    {
                        "classification": "activity_support",
                        "source_row_ids": ["ROW-0004"],
                        "attach_to_concept_title": duplicate_title,
                        "rationale": "Recitation is an activity supporting meaning.",
                    },
                ],
                "rationale": (
                    "One substantial concept remains; the other rows are "
                    "supporting activities."
                ),
            }],
        }

    def culmination_provider(request):
        culmination_calls.append(request)
        return {
            "topics": [{
                "topic_id": "TOPIC-1",
                "consolidation": (
                    "Learners connect the poem's fresh beginning with the "
                    "classroom's shared confidence and sustained effort."
                ),
                "rationale": "The paragraph combines both final concepts.",
            }],
        }

    fixed = contract.reconcile_duplicate_rows(
        _env(),
        rows,
        provider=identity_provider,
        culmination_provider=culmination_provider,
        store=kernel.DecisionStore(),
    )

    assert len(identity_calls) == 1
    assert len(culmination_calls) == 1
    assert contract._duplicate_groups(fixed) == []
    concept = next(row for row in fixed if row["concept_title"] == duplicate_title)
    assert set(concept["_source_block_ids"]) == {
        "BLK-WARMUP", "BLK-POEM", "BLK-RECITE"
    }
    assert len(concept["_phase32_nonconcept_support"]) == 2
    assert not any(
        row["concept_title"] == duplicate_title
        for row in fixed
        if row is not concept
    )
    culmination = next(
        row for row in fixed if row["parent_concept"] == "Culmination"
    )
    assert duplicate_title in culmination["concept_title"]
    assert "A Classroom Clan" in culmination["concept_title"]
    assert "shared confidence" in culmination["concept_details"]


def test_unique_rows_do_not_spend_a_reconciliation_call(monkeypatch):
    monkeypatch.setattr(envelope, "validate", lambda value: copy.deepcopy(value))
    rows = [
        _row(
            "One",
            "BLK-POEM",
            "ORIGIN-1",
            details="Description: One.\nAchieving Mastery: Explain one.",
        ),
        _row(
            "Two",
            "BLK-EFFORT",
            "ORIGIN-2",
            details="Description: Two.\nAchieving Mastery: Explain two.",
        ),
    ]

    def must_not_run(_request):  # pragma: no cover
        raise AssertionError("no duplicate means no model reconciliation")

    assert contract.reconcile_duplicate_rows(
        _env(), rows, provider=must_not_run, store=kernel.DecisionStore()
    ) == rows


def test_install_is_reload_safe(monkeypatch):
    contract.install()
    assert getattr(prelearn.merge, "_FORMATION_CONTRACT_WRAPPER", False)
    assert getattr(settle.settle, "_ROW_IDENTITY_CONTRACT_WRAPPER", False)

    # Simulate a module reload replacing the exported functions while leaving
    # private contract markers in the module dictionary.
    monkeypatch.setattr(prelearn, "merge", prelearn._FORMATION_ORIGINAL_MERGE)
    monkeypatch.setattr(settle, "settle", settle._ROW_IDENTITY_ORIGINAL_SETTLE)
    contract.install()

    assert getattr(prelearn.merge, "_FORMATION_CONTRACT_WRAPPER", False)
    assert getattr(settle.settle, "_ROW_IDENTITY_CONTRACT_WRAPPER", False)

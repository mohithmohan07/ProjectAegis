"""Regression coverage for the Phase 3 universal semantic source graph."""
from __future__ import annotations

import copy
from pathlib import Path

import pytest

from app.services import canonical_source_phase2 as phase2
from app.services import canonical_source_phase3 as phase3


@pytest.fixture(scope="module")
def rne_source() -> str:
    return Path("data/Testing/RNE.mmd").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def rne_canonical(rne_source: str) -> dict:
    return phase2.compile_phase2_source(
        rne_source,
        source_filename="RNE.mmd",
        consumer_module="build_concepts",
    ).canonical


@pytest.fixture(scope="module")
def rne_graph(rne_source: str, rne_canonical: dict) -> dict:
    return phase3.build_semantic_graph(
        rne_canonical,
        rne_source,
        metadata={
            "board": "CBSE",
            "grade": "10",
            "subject": "History",
            "unit": "Modern World History",
            "chapter_title": "The Rise of Nationalism in Europe",
            "chapter_code": "10CBSS_TheRiseOfNat",
        },
    )


def test_rne_graph_has_six_main_topics_and_26_stable_qids(rne_graph: dict):
    assert rne_graph["status"] == "passed"
    assert [topic["number"] for topic in rne_graph["topics"]] == [1, 2, 3, 4, 5, 6]
    assert [topic["topic_id"] for topic in rne_graph["topics"]] == [
        f"TOPIC-{index:04d}" for index in range(1, 7)
    ]
    assert len(rne_graph["tasks"]) == 26
    assert [task["qid"] for task in rne_graph["tasks"]] == [
        f"QINV-{index:04d}" for index in range(1, 27)
    ]


def test_rne_section_two_subtopics_have_stable_parent_identity(rne_graph: dict):
    section_two = next(
        topic for topic in rne_graph["topics"]
        if topic["title"] == "The Making of Nationalism in Europe"
    )
    aristocracy = next(
        subtopic for subtopic in rne_graph["subtopics"]
        if subtopic["title"] == "The Aristocracy and the New Middle Class"
    )

    assert section_two["topic_id"] == "TOPIC-0002"
    assert aristocracy["topic_id"] == section_two["topic_id"]
    assert aristocracy["subtopic_id"] == "SUBTOPIC-0002-01"


def test_qid_scope_ignores_stale_visible_topic_hint(
    rne_source: str, rne_canonical: dict,
):
    canonical = copy.deepcopy(rne_canonical)
    target = next(
        task for task in canonical["tasks"]
        if "political ends that List hopes" in task.get("raw_prompt", "")
    )
    target["topic_hint"] = "1 The Aristocracy and the New Middle Class"

    graph = phase3.build_semantic_graph(
        canonical,
        rne_source,
        metadata={"subject": "History", "chapter_title": "The Rise of Nationalism in Europe"},
    )
    scoped = next(task for task in graph["tasks"] if task["qid"] == target["qid"])

    assert scoped["topic_id"] == "TOPIC-0002"
    assert scoped["legacy_topic_hint"] == "1 The Aristocracy and the New Middle Class"


def test_mined_type_scope_is_derived_from_qids_not_topic_match_hint(rne_graph: dict):
    qid = next(
        task["qid"] for task in rne_graph["tasks"]
        if "political ends that List hopes" in task["exact_text"]
    )
    mined = {
        "types": [{
            "type_id": "TYPE-0003",
            "topic_match_hint": "1 The Aristocracy and the New Middle Class",
            "source_question_ids": [qid],
            "case_prompts": [],
        }]
    }

    annotated = phase3.annotate_mined_types(mined, rne_graph)
    result = annotated["types"][0]

    assert result["canonical_topic_ids"] == ["TOPIC-0002"]
    assert result["placement_scope"] == "normal"
    assert result["topic_match_hint"] == "1 The Aristocracy and the New Middle Class"


def test_case_level_scope_is_preserved_for_cross_topic_type(rne_graph: dict):
    qid_two = next(
        task["qid"] for task in rne_graph["tasks"]
        if task["topic_id"] == "TOPIC-0002" and not task["chapter_wide"]
    )
    qid_three = next(
        task["qid"] for task in rne_graph["tasks"]
        if task["topic_id"] == "TOPIC-0003" and not task["chapter_wide"]
    )
    mined = {
        "types": [{
            "type_id": "TYPE-0099",
            "source_question_ids": [qid_two, qid_three],
            "case_prompts": [
                {"case_id": "CASE-0001", "examples": [{"source_question_id": qid_two}]},
                {"case_id": "CASE-0002", "examples": [{"source_question_id": qid_three}]},
            ],
        }]
    }

    result = phase3.annotate_mined_types(mined, rne_graph)["types"][0]

    assert result["canonical_topic_ids"] == ["TOPIC-0002", "TOPIC-0003"]
    assert result["placement_scope"] == "cross_topic_synthesis"
    assert result["case_prompts"][0]["canonical_topic_id"] == "TOPIC-0002"
    assert result["case_prompts"][1]["canonical_topic_id"] == "TOPIC-0003"


def test_chapter_wide_review_task_stays_chapter_scoped(rne_graph: dict):
    qid = next(task["qid"] for task in rne_graph["tasks"] if task["chapter_wide"])

    scope = phase3.scope_for_qids([qid], rne_graph)

    assert scope["canonical_topic_ids"] == []
    assert scope["placement_scope"] == "chapter"


def test_inventory_annotation_keeps_source_block_provenance(rne_graph: dict):
    task = next(item for item in rne_graph["tasks"] if item["source_block_ids"])
    inventory = {"items": [{"qid": task["qid"], "raw_task": task["exact_text"]}]}

    annotated = phase3.annotate_inventory(inventory, rne_graph)
    item = annotated["items"][0]

    assert item["_semantic_topic_id"] == task["topic_id"]
    assert item["_semantic_source_block_ids"] == task["source_block_ids"]
    assert annotated["semantic_graph"]["graph_sha256"] == rne_graph["graph_sha256"]


@pytest.mark.parametrize(
    ("subject", "expected"),
    [
        ("Computer Science", "computer_science"),
        ("Artificial Intelligence", "computer_science"),
        ("Social Science", "social_science"),
        ("Life Science", "life_science"),
        ("Environmental Science", "environmental_science"),
        ("Electronics", "electronics"),
        ("Physics", "physical_science"),
        ("Mathematics", "mathematics"),
        ("Commercial Studies", "commerce"),
        ("English Literature", "language_literature"),
        ("Yoga", "health_physical_education"),
        ("Science", "general_science"),
    ],
)
def test_subject_adapter_uses_specialized_match_before_generic_science(
    subject: str, expected: str,
):
    assert phase3.subject_adapter(subject) == expected


def test_headingless_source_receives_one_stable_topic():
    source = (
        "Numbers can be arranged in a repeating pattern.\n\n"
        "Describe the next two values in the pattern.\n"
    )
    canonical = phase2.compile_phase2_source(
        source,
        source_filename="headingless.mmd",
        consumer_module="build_concepts",
    ).canonical

    graph = phase3.build_semantic_graph(
        canonical,
        source,
        metadata={"subject": "Mathematics", "chapter_title": "Number Patterns"},
    )

    assert graph["status"] == "passed"
    assert len(graph["topics"]) == 1
    assert graph["topics"][0]["topic_id"] == "TOPIC-0001"
    assert graph["topics"][0]["title"] == "Number Patterns"


def test_non_latin_heading_and_source_order_are_preserved():
    source = (
        "\\section*{1 ಸಂಖ್ಯೆಗಳ ಮಾದರಿ}\n\n"
        "ಸಂಖ್ಯೆಗಳು ಒಂದು ಕ್ರಮವನ್ನು ಅನುಸರಿಸುತ್ತವೆ.\n\n"
        "\\section*{2 ಅನ್ವಯ}\n\n"
        "ಮುಂದಿನ ಸಂಖ್ಯೆಯನ್ನು ಗುರುತಿಸಿ.\n"
    )
    canonical = phase2.compile_phase2_source(
        source,
        source_filename="kannada.mmd",
        consumer_module="build_concepts",
    ).canonical

    graph = phase3.build_semantic_graph(
        canonical,
        source,
        metadata={"subject": "Mathematics", "chapter_title": "ಸಂಖ್ಯೆಗಳ ಮಾದರಿ"},
    )

    assert [topic["title"] for topic in graph["topics"]] == [
        "ಸಂಖ್ಯೆಗಳ ಮಾದರಿ", "ಅನ್ವಯ"
    ]
    assert [topic["source_start"] for topic in graph["topics"]] == sorted(
        topic["source_start"] for topic in graph["topics"]
    )


def test_graph_cache_hash_changes_when_target_metadata_changes(
    rne_canonical: dict,
):
    first = phase3.source_contract_sha256(
        rne_canonical, {"subject": "History", "grade": "10"}
    )
    second = phase3.source_contract_sha256(
        rne_canonical, {"subject": "History", "grade": "9"}
    )

    assert first != second


def test_graph_validation_rejects_unknown_task_block(rne_graph: dict):
    graph = copy.deepcopy(rne_graph)
    graph["tasks"][0]["source_block_ids"] = ["BLK-DOES-NOT-EXIST"]

    report = phase3.validate_graph(graph)

    assert any(error["code"] == "phase3_unknown_task_block" for error in report["errors"])


def test_topic_packets_are_source_ordered_and_id_constrained(rne_graph: dict):
    packets = phase3.topic_packets(rne_graph)

    assert [packet["topic_id"] for packet in packets] == [
        f"TOPIC-{index:04d}" for index in range(1, 7)
    ]
    assert all(packet["contract"]["source_order_locked"] for packet in packets)
    assert all(
        set(packet["contract"]["allowed_block_ids"])
        == {block["block_id"] for block in packet["blocks"]}
        for packet in packets
    )

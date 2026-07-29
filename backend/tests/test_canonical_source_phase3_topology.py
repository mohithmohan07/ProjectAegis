"""Regression coverage for graph-authoritative topic and Type placement."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services import canonical_source_phase2 as phase2
from app.services import canonical_source_phase3 as phase3
from app.services import canonical_source_phase3_topology as topology
from app.services import generation


@pytest.fixture(scope="module")
def rne_source() -> str:
    return Path("data/Testing/RNE.mmd").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def rne_graph(rne_source: str) -> dict:
    canonical = phase2.compile_phase2_source(
        rne_source,
        source_filename="RNE.pdf",
        consumer_module="build_concepts",
    ).canonical
    return phase3.build_semantic_graph(
        canonical,
        rne_source,
        metadata={
            "board": "CBSE",
            "grade": "10",
            "subject": "History",
            "unit": "Modern World History",
            "chapter_title": "The Rise of Nationalism in Europe",
        },
    )


def test_topology_contract_is_installed_after_semantic_graph_contract():
    assert generation._PHASE3_SEMANTIC_GRAPH_VERSION == 1
    assert generation._PHASE3_TOPOLOGY_CONTRACT_VERSION == 1


def test_graph_topic_excerpts_restore_section_two_as_main_topic(rne_graph: dict):
    excerpts = topology.source_topic_excerpts(rne_graph)

    assert [item["topic"] for item in excerpts] == [
        "The French Revolution and the Idea of the Nation",
        "The Making of Nationalism in Europe",
        "The Age of Revolutions: 1830-1848",
        "The Making of Germany and Italy",
        "Visualising the Nation",
        "Nationalism and Imperialism",
    ]
    section_two = excerpts[1]
    assert section_two["topic_id"] == "TOPIC-0002"
    assert "The Aristocracy and the New Middle Class" in section_two["excerpt"]
    assert "political ends that List hopes" in section_two["excerpt"]
    assert section_two["source_order_locked"] is True


def test_installed_topology_uses_graph_topics_not_parser_headings(rne_graph: dict):
    legacy_sections = [{
        "heading": "The French Revolution and the Idea of the Nation",
        "body": "legacy parser only found one topic",
        "heading_level": 1,
        "heading_numbered": True,
        "heading_number_prefix": "1",
    }]

    with phase3.activate(rne_graph):
        headings = generation._topic_headings(legacy_sections)
        excerpts = generation._group_source_topic_excerpts(legacy_sections)

    assert headings == topology.graph_topic_titles(rne_graph)
    assert len(headings) == 6
    assert excerpts[1]["topic"] == "The Making of Nationalism in Europe"


def test_concept_subtopic_label_resolves_to_main_topic_identity(rne_graph: dict):
    records = [{
        "topic": "2.1 The Aristocracy and the New Middle Class",
        "parent_concept": "Economic Nationalism",
        "concept_title": "Political Aims of the Zollverein",
        "concept_details": "Description: Economic measures supported national unity.",
        "keywords": "",
    }]

    result = topology.annotate_concept_topics(records, rne_graph)[0]

    assert result["topic"] == "The Making of Nationalism in Europe"
    assert result["_semantic_topic_id"] == "TOPIC-0002"
    assert result["_semantic_subtopic_ids"] == ["SUBTOPIC-0002-01"]
    assert result["_legacy_topic"] == "2.1 The Aristocracy and the New Middle Class"


def test_case_assignment_unit_uses_its_own_qid_derived_topic(rne_graph: dict):
    qid_two = next(
        task["qid"] for task in rne_graph["tasks"]
        if task["topic_id"] == "TOPIC-0002" and not task["chapter_wide"]
    )
    qid_three = next(
        task["qid"] for task in rne_graph["tasks"]
        if task["topic_id"] == "TOPIC-0003" and not task["chapter_wide"]
    )
    mined = topology.canonicalize_mined_type_topics({
        "types": [{
            "type_id": "TYPE-0100",
            "topic_match_hint": "stale publisher heading",
            "source_question_ids": [qid_two, qid_three],
            "case_prompts": [
                {
                    "case_id": "CASE-0001",
                    "examples": [{"source_question_id": qid_two}],
                },
                {
                    "case_id": "CASE-0002",
                    "examples": [{"source_question_id": qid_three}],
                },
            ],
        }],
    }, rne_graph)
    raw_type = mined["types"][0]
    units = []
    for case in raw_type["case_prompts"]:
        unit = dict(raw_type)
        unit["case_prompts"] = [case]
        units.append(unit)

    scoped = topology.canonicalize_assignment_units(units, rne_graph)

    assert scoped[0]["topic_match_hint"] == "The Making of Nationalism in Europe"
    assert scoped[0]["_semantic_source_topic_id"] == "TOPIC-0002"
    assert scoped[1]["topic_match_hint"] == "The Age of Revolutions: 1830-1848"
    assert scoped[1]["_semantic_source_topic_id"] == "TOPIC-0003"


def test_exact_rne_type_allocation_uses_qid_scope_not_stale_heading(
    rne_graph: dict, monkeypatch,
):
    list_task = next(
        task for task in rne_graph["tasks"]
        if "political ends that List hopes" in task["exact_text"]
    )
    calls = []

    def fake_openai(_system, user, **_kwargs):
        calls.append(user)
        if "CURRENT CASE-SCOPED ASSIGNMENTS TO REVIEW" in user:
            return {"assignments": [{
                "type_id": "TYPE-0003",
                "concept_id": "CONCEPT-0001",
            }]}
        assert '"topic_match_hint": "The Making of Nationalism in Europe"' in user
        assert (
            '"_legacy_topic_match_hint": '
            '"1 The Aristocracy and the New Middle Class"'
        ) in user
        return {"assignments": [{
            "concept_id": "CONCEPT-0001",
            "type_ids": ["TYPE-0003"],
        }]}

    monkeypatch.setattr(generation, "_openai_json", fake_openai)
    records = [{
        "topic": "2.1 The Aristocracy and the New Middle Class",
        "parent_concept": "Economic Nationalism",
        "concept_title": "Political Aims of Economic Nationalism",
        "concept_details": (
            "Description: Economic integration can forge national unity.\n"
            "Achieving Mastery: Explain how economic measures support political aims."
        ),
        "keywords": "",
    }]
    mined = {"types": [{
        "type_id": "TYPE-0003",
        "type_title": "Interpreting Economic Measures as National Unification",
        "type_description": "Relate economic integration to political nation-building.",
        "topic_match_hint": "1 The Aristocracy and the New Middle Class",
        "source_question_ids": [list_task["qid"]],
        "case_prompts": [{
            "case_id": "CASE-0001",
            "case_title": "Infer political aims from an economic source",
            "examples": [{
                "source_question_id": list_task["qid"],
                "example_prompt": list_task["display_prompt"],
            }],
        }],
    }]}

    with phase3.activate(rne_graph):
        result = generation._assign_mined_types_via_api(
            records,
            meta=generation._metadata(subject="History"),
            mined_types=mined,
            max_attempts=1,
        )

    assert calls
    assert result[0]["topic"] == "The Making of Nationalism in Europe"
    assert result[0]["_semantic_topic_id"] == "TOPIC-0002"
    assert "Interpreting Economic Measures as National Unification" in result[0]["concept_details"]
    assert mined["types"][0]["topic_match_hint"] == "The Making of Nationalism in Europe"
    assert mined["types"][0]["_legacy_topic_match_hint"] == (
        "1 The Aristocracy and the New Middle Class"
    )


def test_unknown_topic_is_not_silently_guessed(rne_graph: dict):
    record = {
        "topic": "Publisher-specific mystery heading",
        "concept_title": "Mystery",
        "concept_details": "Description: unknown source placement.",
    }

    result = topology.annotate_concept_topics([record], rne_graph)[0]

    assert result["topic"] == "Publisher-specific mystery heading"
    assert "_semantic_topic_id" not in result

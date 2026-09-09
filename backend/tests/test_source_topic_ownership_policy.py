"""Source names may change; source identities/content and recorded decisions do not."""
from __future__ import annotations

import copy
import json

import pytest

from app.services import canonical_source_phase2 as phase2
from app.services import canonical_source_phase3 as phase3
from app.services import canonical_source_phase34_structured_output_contract as batches
from app.services import generation, source_topic_policy as policy


SOURCE = """# National Visions

## 1 Introduction

Sorrieu's print presents an imagined procession of nations.

## 2 Citizenship

Citizens share a political identity and rights.

## 3 Summary

Citizenship connects rights and political identity.

## Exercises

1. Explain how citizenship connects rights and identity.
"""


def _graph(*, current=True, nested_owner=False):
    canonical = phase2.compile_phase2_source(
        SOURCE, source_filename="source.mmd", consumer_module="ownership_test",
    ).canonical

    def author(payload):
        by_title = {row["title"]: row["section_id"] for row in payload["sections"]}
        rows = []
        for section in payload["sections"]:
            title = section["title"]
            role, name, parent = {
                "National Visions": ("chapter_heading", "", ""),
                "Introduction": ("main_topic", "Sorrieu's Vision of Nations", ""),
                "Citizenship": ("main_topic", "Citizenship", ""),
                "Summary": ("content_heading", "", by_title[
                    "Introduction" if nested_owner else "Citizenship"
                ]),
                "Exercises": ("exercise", "", by_title[
                    "Summary" if nested_owner else "Citizenship"
                ]),
            }[title]
            rows.append({
                "section_id": section["section_id"], "role": role,
                "parent_section_id": parent, "confidence": 1.0,
                "evidence": ["Source teaching and exact section identity."],
                "topic_display_name": name,
            })
        return {"sections": rows}

    metadata = {"chapter_title": "National Visions", "subject": "History"}
    if current:
        metadata["source_topic_policy_version"] = policy.SOURCE_TOPIC_POLICY_VERSION
    graph, _ = phase3.compile_semantic_graph(
        canonical, source_text=SOURCE, metadata=metadata,
        hierarchy_provider=author,
        critic_provider=lambda _: {
            "verdict": "verified", "confidence": 1.0, "repairs": [], "issues": [],
        },
    )
    return canonical, graph


def test_semantic_rename_and_demoted_numbered_summary_preserve_all_source():
    canonical, graph = _graph()
    assert [row["title"] for row in graph["topics"]] == [
        "Sorrieu's Vision of Nations", "Citizenship",
    ]
    assert [row["block_id"] for row in graph["blocks"]] == [
        row["block_id"] for row in canonical["blocks"]
    ]
    assert [row["qid"] for row in graph["tasks"]] == [
        row["qid"] for row in canonical["tasks"]
    ]
    summary = next(row for row in graph["sections"] if row["title"] == "Summary")
    assert summary["role"] == "content_heading"
    assert summary["topic_id"] == graph["topics"][1]["topic_id"]
    assert next(row for row in graph["sections"] if row["title"] == "Introduction")[
        "topic_display_name"
    ] == "Sorrieu's Vision of Nations"
    rendered = phase3.render_semantic_source(graph, canonical)
    for text in [
        "Sorrieu's print presents an imagined procession of nations.",
        "Citizenship connects rights and political identity.",
        "Explain how citizenship connects rights and identity.",
    ]:
        assert text in rendered
    assert phase3.validate_graph(graph, canonical=canonical, semantic_source=rendered) == []


def test_legacy_numbered_policy_retains_its_original_names_and_roles():
    canonical, graph = _graph(current=False)
    assert [row["title"] for row in graph["topics"]] == [
        "Introduction", "Citizenship", "Summary",
    ]
    assert phase3._numbered_main_topic_mismatches(graph, canonical=canonical) == []


def test_changed_display_name_is_rejected_against_recorded_api_decision():
    canonical, graph = _graph()
    graph["topics"][0]["title"] = "Different Teaching"
    errors = phase3.validate_graph(
        graph, canonical=canonical,
        semantic_source=phase3.render_semantic_source(graph, canonical),
    )
    assert "source_topic_decision_mismatch" in {row["code"] for row in errors}


def test_nested_recorded_parent_ids_outrank_physical_position():
    # This fixture is an opaque author verdict; the compiler must transport it
    # through the intermediate content section without re-judging its wording.
    _, graph = _graph(nested_owner=True)
    exercise = next(row for row in graph["sections"] if row["title"] == "Exercises")
    assert exercise["topic_id"] == graph["topics"][0]["topic_id"]
    assert exercise["topic_id"] != graph["topics"][-1]["topic_id"]


def test_source_policy_reaches_hierarchy_author_critic_and_rekeys_cache(monkeypatch):
    payload = {
        "metadata": {"source_topic_policy_version": policy.SOURCE_TOPIC_POLICY_VERSION},
        "sections": [{
            "section_id": "SEC-1", "title": "Introduction", "level": 1,
            "source_order": 1, "source_start": 0, "baseline_role": "content_heading",
            "excerpt": "Sorrieu's print presents a vision of nations.",
        }], "verified_pdf_headings": [],
    }
    calls = []

    def provider(**kwargs):
        calls.append(kwargs)
        assert policy.SOURCE_TOPIC_POLICY in kwargs["system"]
        data = json.loads(kwargs["prompt"])
        assert data["source_topic_policy_sha256"] == phase3._sha256_text(
            policy.SOURCE_TOPIC_POLICY + policy.HIERARCHY_TOPIC_INSTRUCTION
        )
        props = kwargs["response_schema"]["schema"]["properties"]
        if "sections" in props:
            assert "topic_display_name" in props["sections"]["items"]["required"]
            return {"sections": [{
                "section_id": "SEC-1", "role": "main_topic", "parent_section_id": "",
                "confidence": 1.0, "evidence": ["Print analysis"],
                "topic_display_name": "Sorrieu's Vision of Nations",
            }]}
        assert data["proposed_hierarchy"][0]["topic_display_name"] == "Sorrieu's Vision of Nations"
        return {"verdict": "verified", "confidence": 1.0, "repairs": [], "issues": []}

    monkeypatch.setattr(batches.phase22, "_openai_multimodal_json", provider)
    monkeypatch.setattr(batches, "_read_cache", lambda: {})
    monkeypatch.setattr(batches, "_write_cache_entry", lambda *_: None)
    response = batches._classify_hierarchy_batched(payload)
    batches._critic_hierarchy_batched({**payload, "proposed_hierarchy": response["sections"]})
    assert len(calls) == 2
    current = batches._classification_payload_for_batch(payload, ["SEC-1"])
    legacy = copy.deepcopy(current)
    legacy["metadata"].pop("source_topic_policy_version")
    assert batches._cache_key(kind="classifier", payload=current, target_ids=["SEC-1"]) != (
        batches._cache_key(kind="classifier", payload=legacy, target_ids=["SEC-1"])
    )


@pytest.mark.parametrize("key", [
    "concepts.skeleton.system", "concepts.canonicalize.system",
    "concepts.type_mining.system", "concepts.chapter_wide_task_topics.system",
])
def test_source_ownership_policy_is_in_active_author_instructions(key):
    assert policy.SOURCE_TOPIC_POLICY in generation.prompts.get_text(key)

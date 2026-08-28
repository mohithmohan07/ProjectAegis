"""Regressions for sourcebook-faithful Post-Learning formation."""
from __future__ import annotations

import copy
import json

import pytest

from app.services import build_concepts_release
from app.services import postlearning_formation_contract as post
from app.services.phase3 import envelope, kernel, runner, settle


def _concept(
    concept_id: str,
    display_name: str,
    role: str,
    blocks: list[str],
    *,
    qids: list[str] | None = None,
    mastery: str,
    rationale: str,
):
    return {
        "plan_concept_id": concept_id,
        "display_name": display_name,
        "semantic_role": role,
        "facets": [display_name.casefold()],
        "source_block_ids": list(blocks),
        "task_qids": list(qids or []),
        "achieving_mastery": mastery,
        "rationale": rationale,
    }


def _plan() -> dict:
    return {
        "topics": [
            {
                "plan_topic_id": "PT-1",
                "display_name": "Stanza 1 - A new beginning",
                "evidence_block_ids": ["BLK-1"],
                "concepts": [
                    _concept(
                        "PC-1",
                        "A shared new-school beginning",
                        "ordinary",
                        ["BLK-1"],
                        qids=["Q-1"],
                        mastery="Explain the stanza's invitation to begin together.",
                        rationale="The opening lines carry one shared beginning.",
                    ),
                    _concept(
                        "PC-2",
                        "The opening stanza as one invitation",
                        "stanza_culmination",
                        ["BLK-1"],
                        mastery="Connect the stanza's meaning and sound as a whole.",
                        rationale="The stanza closes one coherent appeal.",
                    ),
                ],
            },
            {
                "plan_topic_id": "PT-2",
                "display_name": "Detailed Analysis of 'A Small New Start'",
                "evidence_block_ids": ["BLK-2", "BLK-3"],
                "concepts": [
                    _concept(
                        "PC-3",
                        "Language & Literary Devices",
                        "detailed_analysis",
                        ["BLK-2"],
                        qids=["Q-2"],
                        mastery="Identify the sound device and explain its effect.",
                        rationale="The device is analysed across the work.",
                    ),
                    _concept(
                        "PC-4",
                        "The poem as a whole",
                        "chapter_culmination",
                        ["BLK-1", "BLK-2"],
                        mastery="Connect the poem's meaning and sound in one account.",
                        rationale="The final concept synthesises the work.",
                    ),
                ],
            },
        ],
        "threaded_components": [],
        "non_teaching_block_ids": ["BLK-3"],
        "notes": "",
    }


def _env(plan: dict | None = None, inventory: dict | None = None) -> dict:
    plan = plan if plan is not None else _plan()
    graph = {
        "source_contract_hash": "source-contract-1",
        "metadata": {
            "language_topology_plan": json.dumps(plan),
        },
        "topics": [
            {
                "topic_id": "TOPIC-0001",
                "plan_topic_id": "PT-1",
                "title": "Stanza 1 - A new beginning",
            },
            {
                "topic_id": "TOPIC-0002",
                "plan_topic_id": "PT-2",
                "title": "Detailed Analysis of 'A Small New Start'",
            },
        ],
        "subtopics": [],
        "blocks": [
            {"block_id": "BLK-1", "topic_id": "TOPIC-0001", "kind": "paragraph"},
            {"block_id": "BLK-2", "topic_id": "TOPIC-0002", "kind": "paragraph"},
            {"block_id": "BLK-3", "topic_id": "TOPIC-0002", "kind": "paragraph"},
        ],
    }
    canonical = {
        "blocks": [
            {"block_id": "BLK-1", "display_text": "The opening stanza."},
            {"block_id": "BLK-2", "display_text": "Alliteration explanation."},
            {"block_id": "BLK-3", "display_text": "For the facilitator."},
        ],
    }
    if inventory is None:
        inventory = {
            "items": [
                {"qid": "Q-1", "normalized_task": "Explain the opening stanza."},
                {"qid": "Q-2", "normalized_task": "Find an example of alliteration."},
            ],
        }
    return envelope.build(
        graph=graph,
        canonical=canonical,
        skeleton_rows=[
            {
                "topic": "Stanza 1 - A new beginning",
                "parent_concept": "Warm-up",
                "concept_title": "Setting personal learning goals",
                "concept_details": "Description: A warm-up task.",
                "keywords": "warm-up",
                "_semantic_topic_id": "TOPIC-0001",
            },
        ],
        inventory=inventory,
        mined_types={"types": []},
        metadata={
            "board": "Maharashtra",
            "grade": "06",
            "subject": "English",
            "unit": "POEM",
            "chapter_title": "A Small New Start",
            "language_topology_plan": json.dumps(plan),
        },
    )


def _settled_clean(env: dict) -> list[dict]:
    entries = post.plan_entries(env)
    rows: list[dict] = []
    normal_index = 0
    for entry in entries:
        if entry["culmination"]:
            row = {
                "topic": entry["topic_title"],
                "parent_concept": "Culmination",
                "concept_title": entry["wire_title"],
                "concept_details": "Description: A model-authored synthesis.",
                "keywords": "synthesis",
                "_semantic_topic_id": entry["topic_id"],
                "_source_block_ids": list(entry["source_block_ids"]),
                "_source_grounding_contract": "derived-from-verified-topic-concepts",
            }
        else:
            normal_index += 1
            row = {
                "topic": entry["topic_title"],
                "parent_concept": entry["topic_title"],
                "concept_title": "A draft title that Settle may refine",
                "concept_details": (
                    "Description: A grounded teaching paragraph.\n"
                    "Achieving Mastery: A draft capability."
                ),
                "keywords": "draft",
                "_semantic_topic_id": entry["topic_id"],
                "_source_block_ids": list(entry["source_block_ids"]),
                "_source_grounding_contract": "api-verified-source-block-ids",
                "_phase32_origin_concept_id": (
                    f"TOPOLOGY-CONCEPT-{normal_index:04d}"
                ),
            }
        rows.append(row)
    return rows


def test_materialization_replaces_generic_support_rows_with_plan_concepts():
    original = _env()
    materialized = post.materialize_envelope(original)
    entries = post.plan_entries(materialized)

    assert envelope.validate(materialized) == materialized
    assert len(materialized["skeleton_rows"]) == len(entries) == 4
    assert [row["concept_title"] for row in materialized["skeleton_rows"]] == [
        "A shared new-school beginning",
        "Culmination: The opening stanza as one invitation",
        "Language & Literary Devices",
        "Culmination: The poem as a whole",
    ]
    assert all(
        row["concept_title"] != "Setting personal learning goals"
        for row in materialized["skeleton_rows"]
    )
    assert materialized["metadata"]["post_language_plan_materialization"] == {
        "version": post.MATERIALIZATION_VERSION,
        "prior_skeleton_row_count": 1,
        "plan_topic_count": 2,
        "plan_concept_count": 4,
    }
    assert [
        row[post.SEMANTIC_ROLE_FIELD]
        for row in materialized["skeleton_rows"]
    ] == [
        "ordinary", "stanza_culmination", "detailed_analysis",
        "chapter_culmination",
    ]


def test_only_a_sealed_plan_culmination_survives_a_one_concept_topic():
    planned = {
        "concept_title": "Culmination: Planned stanza close",
        post.PLAN_IDENTITY_FIELD: {
            "plan_topic_id": "PT-1",
            "plan_concept_id": "PC-2",
        },
        post.SEMANTIC_ROLE_FIELD: "stanza_culmination",
    }
    unplanned = {"concept_title": "Culmination - Generic recap"}
    duplicate = {
        **planned,
        "concept_title": "Culmination: Duplicate planned close",
        post.PLAN_IDENTITY_FIELD: {
            "plan_topic_id": "PT-1",
            "plan_concept_id": "PC-EXTRA",
        },
    }

    assert settle._culminations_to_author(
        [unplanned], normal_concept_count=1,
    ) == []
    assert settle._culminations_to_author(
        [planned], normal_concept_count=0,
    ) == []
    assert settle._culminations_to_author(
        [unplanned, planned, duplicate], normal_concept_count=1,
    ) == [planned]
    # The shared multi-concept rule still permits one ordinary authored recap.
    assert settle._culminations_to_author(
        [unplanned], normal_concept_count=2,
    ) == [unplanned]


def test_settle_keeps_planned_one_concept_culminations_without_conformance():
    """Job 81: each stanza plan intentionally carries one concept + close.

    Settle must author those recorded closes directly. Dropping them would
    force the language-plan seam to spend a chapter-wide conformance decision
    merely to recreate rows that were already present in the sealed plan.
    """
    env = post.materialize_envelope(_env())
    conformance_calls = {"n": 0}

    def topology(request):
        return {
            "decisions": [
                {
                    "concept_id": row["concept_id"],
                    "decision": "keep",
                    "confidence": 0.999,
                    "reason": "The recorded plan concept is singular.",
                    "segments": [{
                        "concept_title": row["concept_title"],
                        "parent_concept": row["parent_concept"],
                        "concept_details": row["concept_details"],
                        "keywords": row["keywords"],
                    }],
                }
                for row in request["concepts"]
            ],
        }

    def grounding(request):
        block_id = request["source_blocks"][0]["block_id"]
        return {
            "concepts": [
                {
                    "concept_id": row["concept_id"],
                    "source_block_ids": [block_id],
                    "reference_block_ids": [],
                    "confidence": 0.999,
                    "reason": "The topic block directly teaches the claim.",
                }
                for row in request["concepts"]
            ],
        }

    def author(request):
        response = {
            "rows": [
                {
                    "concept_id": row["concept_id"],
                    "concept_description": (
                        "The source presents one coherent literary idea and "
                        "develops its meaning through precise language, "
                        "context, and effect. Learners connect the important "
                        "detail to the speaker's purpose, explain how the "
                        "wording shapes understanding, and support their "
                        "interpretation with the named stanza evidence."
                    ),
                    "achieving_mastery": (
                        "Learners can explain the source-grounded idea for "
                        + row["concept_id"] + "."
                    ),
                }
                for row in request["concepts"]
            ],
        }
        if request.get("culminations"):
            response["culminations"] = [
                {
                    "concept_id": row["concept_id"],
                    "consolidation": (
                        "Together the stanza's meaning and literary form "
                        "create one complete invitation, allowing learners "
                        "to explain both its message and its effect."
                    ),
                }
                for row in request["culminations"]
            ]
        return response

    def critic(_request):
        return {"verdict": "verified", "confidence": 0.999, "issues": []}

    def forbidden_conformance(_request):
        conformance_calls["n"] += 1
        raise AssertionError("planned rows must align without conformance")

    rows = settle.settle(
        env,
        topology_provider=topology,
        grounding_provider=grounding,
        analysis_provider=author,
        critic=critic,
        store=kernel.DecisionStore(),
        post_plan_provider=forbidden_conformance,
    )

    assert conformance_calls["n"] == 0
    assert len(rows) == 4
    assert [row[post.PLAN_IDENTITY_FIELD]["plan_concept_id"] for row in rows] == [
        "PC-1", "PC-2", "PC-3", "PC-4",
    ]
    assert [row[post.SEMANTIC_ROLE_FIELD] for row in rows] == [
        "ordinary", "stanza_culmination", "detailed_analysis",
        "chapter_culmination",
    ]


def test_clean_conformance_stamps_plan_identity_without_a_model_call():
    env = post.materialize_envelope(_env())
    calls = {"n": 0}

    def forbidden(_request):
        calls["n"] += 1
        raise AssertionError("the clean one-to-one path must not call a provider")

    rows = post.conform_rows(
        env,
        _settled_clean(env),
        provider=forbidden,
        critic=lambda _request: {},
        store=kernel.DecisionStore(),
        allow_live=False,
    )

    assert calls["n"] == 0
    assert [
        row[post.PLAN_IDENTITY_FIELD]["plan_concept_id"] for row in rows
    ] == ["PC-1", "PC-2", "PC-3", "PC-4"]
    assert [row[post.SEMANTIC_ROLE_FIELD] for row in rows] == [
        "ordinary", "stanza_culmination", "detailed_analysis",
        "chapter_culmination",
    ]
    assert all("Achieving Mastery:" in row["concept_details"] for row in rows)
    assert rows[0][post.PLANNED_QIDS_FIELD] == ["Q-1"]
    assert rows[2][post.PLANNED_QIDS_FIELD] == ["Q-2"]


def test_drift_uses_one_replayable_model_decision_and_accounts_every_row():
    env = post.materialize_envelope(_env())
    split_rows = [
        {
            "topic": "Stanza 1 - A new beginning",
            "parent_concept": "Opening",
            "concept_title": "Opening meaning part one",
            "concept_details": "Description: First draft fragment.",
            "keywords": "opening",
            "_semantic_topic_id": "TOPIC-0001",
            "_source_block_ids": ["BLK-1"],
            "_source_grounding_contract": "api-verified-source-block-ids",
            "_phase32_origin_concept_id": "TOPOLOGY-CONCEPT-0001",
            "review_flags": ["first draft needs reconciliation"],
        },
        {
            "topic": "Stanza 1 - A new beginning",
            "parent_concept": "Opening",
            "concept_title": "Opening meaning part two",
            "concept_details": "Description: Second draft fragment.",
            "keywords": "shared beginning",
            "_semantic_topic_id": "TOPIC-0001",
            "_source_block_ids": ["BLK-1"],
            "_source_grounding_contract": "api-verified-source-block-ids",
            "_phase32_origin_concept_id": "TOPOLOGY-CONCEPT-0001",
            "review_flags": ["second draft needs reconciliation"],
        },
    ]
    calls = {"n": 0}

    def provider(request):
        calls["n"] += 1
        plan_rows = request["plan_concepts"]
        returned = []
        for index, entry in enumerate(plan_rows):
            returned.append({
                "plan_concept_id": entry["plan_concept_id"],
                "concept_title": entry["wire_title"],
                "parent_concept": (
                    "Culmination"
                    if "culmination" in entry["semantic_role"]
                    else "Literary understanding"
                ),
                "description": (
                    "The two drafts are reconciled into the one complete "
                    "teaching idea required by the plan."
                    if index == 0
                    else "A complete Grade Six teaching explanation."
                ),
                "achieving_mastery": entry["achieving_mastery"],
                "keywords": ", ".join(entry["facets"]),
                "source_row_ids": (
                    ["SETTLED-0001", "SETTLED-0002"] if index == 0 else []
                ),
                "rationale": "Conformed exactly to the recorded plan identity.",
            })
        return {"concepts": returned}

    def critic(_request):
        return {"verdict": "verified", "confidence": 0.999, "issues": []}

    store = kernel.DecisionStore()
    first = post.conform_rows(
        env,
        split_rows,
        provider=provider,
        critic=critic,
        store=store,
        allow_live=False,
    )
    second = post.conform_rows(
        env,
        split_rows,
        provider=provider,
        critic=critic,
        store=store,
        allow_live=False,
    )

    assert calls["n"] == 1
    assert second == first
    assert len(first) == 4
    assert first[0]["_phase32_origin_concept_ids"] == [
        "TOPOLOGY-CONCEPT-0001"
    ]
    assert {
        "first draft needs reconciliation",
        "second draft needs reconciliation",
    }.issubset(set(first[0]["review_flags"]))
    assert first[1][post.SEMANTIC_ROLE_FIELD] == "stanza_culmination"
    assert first[3][post.SEMANTIC_ROLE_FIELD] == "chapter_culmination"


def test_install_is_reload_safe_and_release_fields_are_private_audit():
    post.install()
    run_once = runner.run
    settle_once = settle.settle
    post.install()

    assert runner.run is run_once
    assert settle.settle is settle_once
    assert getattr(runner.run, "_aegis_postlearning_plan_runner", False)
    assert getattr(settle.settle, "_aegis_postlearning_plan_settle", False)
    assert post.POST_LANGUAGE_AUDIT_FIELDS.issubset(
        build_concepts_release._RELEASE_AUDIT_FIELDS
    )


_SPLIT_ITEMS = [
    {
        "qid": "Q-1.1",
        "parent_qid": "Q-1",
        "normalized_task": "Explain the opening stanza part (i).",
    },
    {
        "qid": "Q-1.2",
        "parent_qid": "Q-1",
        "normalized_task": "Explain the opening stanza part (ii).",
    },
    {
        "qid": "Q-1.3",
        "parent_qid": "Q-1",
        "normalized_task": "Explain the opening stanza part (iii).",
    },
    {"qid": "Q-2", "normalized_task": "Find an example of alliteration."},
]


def test_plan_parent_qid_expands_to_recorded_split_children():
    env = _env(inventory={"items": copy.deepcopy(_SPLIT_ITEMS)})

    entries = post.plan_entries(env)

    assert entries[0]["task_qids"] == ["Q-1.1", "Q-1.2", "Q-1.3"]
    assert entries[0]["task_qid_expansions"] == {
        "Q-1": ["Q-1.1", "Q-1.2", "Q-1.3"],
    }
    assert entries[2]["task_qids"] == ["Q-2"]
    assert entries[2]["task_qid_expansions"] == {}

    materialized = post.materialize_envelope(env)
    assert materialized["skeleton_rows"][0][post.PLANNED_QIDS_FIELD] == [
        "Q-1.1", "Q-1.2", "Q-1.3",
    ]


def test_plan_task_without_item_or_recorded_children_still_raises():
    # Envelope build already materializes the plan, so construction itself
    # must fail: an unrecorded task reference is still a named defect.
    with pytest.raises(ValueError, match=r"names unknown task\(s\): Q-1"):
        post.plan_entries(_env(inventory={
            "items": [
                {
                    "qid": "Q-2",
                    "normalized_task": "Find an example of alliteration.",
                },
            ],
        }))


def test_expanded_children_remain_owned_by_exactly_one_concept():
    plan = _plan()
    plan["topics"][1]["concepts"][0]["task_qids"] = ["Q-1.2", "Q-2"]

    with pytest.raises(ValueError, match="more than one concept"):
        post.plan_entries(_env(
            plan=plan, inventory={"items": copy.deepcopy(_SPLIT_ITEMS)}
        ))


def test_parent_and_own_child_named_together_resolve_each_child_once():
    plan = _plan()
    plan["topics"][0]["concepts"][0]["task_qids"] = ["Q-1", "Q-1.2"]
    env = _env(plan=plan, inventory={"items": copy.deepcopy(_SPLIT_ITEMS)})

    entries = post.plan_entries(env)

    assert entries[0]["task_qids"] == ["Q-1.1", "Q-1.2", "Q-1.3"]


def test_direct_inventory_match_is_preferred_over_expansion():
    items = copy.deepcopy(_SPLIT_ITEMS) + [
        {"qid": "Q-1", "normalized_task": "Explain the opening stanza."},
    ]
    env = _env(inventory={"items": items})

    entries = post.plan_entries(env)

    assert entries[0]["task_qids"] == ["Q-1"]
    assert entries[0]["task_qid_expansions"] == {}

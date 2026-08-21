"""Concept-generation prompts must require rich Types classification."""
from pathlib import Path

import pytest

from app.services import generation as g


def test_concepts_system_requires_numeric_types_guidance():
    system = g._concepts_system("Mathematics")
    assert "Extract ONLY a clean teachable concept skeleton" in system
    assert "No Types" in system
    assert "no culmination rows" in system
    assert "parent_concept" in system
    # Numeric zero-padded labels (Type 01:/Case 01:), not descriptive labels.
    types_system = g.prompts.get_text("concepts.types_assign.system")
    assert "Type 01:" in types_system and "Case 01:" in types_system
    assert "Example 01:" in types_system
    assert "One Type = one distinct reusable assessment/task pattern" in types_system
    assert "Infer patterns from the actual action" in types_system
    assert "Misconception is REQUIRED" not in system
    assert "description-only editor" in g.prompts.get_text("concepts.description_refine.system")
    canonicalize = g.prompts.get_text("concepts.canonicalize.system")
    assert "compact teacher-facing chapter map" in canonicalize
    assert "Do not over-merge" in canonicalize
    assert "Types-only classifier" in g.prompts.get_text("concepts.types_assign.system")
    assert "source_evidence" in system
    assert "must not be written to workbook" in system


def test_split_prompt_contracts_are_separated():
    skeleton = g.prompts.get_text("concepts.skeleton.system")
    description = g.prompts.get_text("concepts.description_refine.system")
    types = g.prompts.get_text("concepts.types_assign.system")
    repair = g.prompts.get_text("concepts.repair.system")
    assert "No Types" in skeleton and "no culmination rows" in skeleton
    assert "Do not include Types" in description
    assert "Preserve Description and any existing Activity/Info Hub exactly" in types
    # Types run after the culmination pass; culminations may receive mixed Types.
    assert "Culmination rows may receive Types" in types
    assert "Activity/Info Hub" in types
    assert "Preserve valid fields, including parent_concept, Types" in repair
    hub = g.prompts.get_text("concepts.activity_hub.system")
    assert "UNIVERSAL" in hub
    assert "Activity/Info Hub" in hub
    assert "Ohm's" not in hub and "Belgium" not in hub and "Vetal" not in hub


def test_the_description_prompt_requires_one_combined_analysis_section():
    # Step 7 retired the pre-learning derivation prompt, so only the Post
    # half of this contract survives; the assertions are unchanged.
    post = g.prompts.get_text("concepts.description_refine.system")

    normalized = " ".join(post.split())
    assert "Misconception/ Error Analysis:" in normalized
    assert "Misconceptions:" in normalized
    assert "Error Analysis:" in normalized
    assert "commonly held incorrect belief" in normalized
    assert "procedural, computational, representational, or reasoning mistake" in normalized
    assert "both labelled" in normalized.lower()
    assert "Never emit separate top-level" in normalized
    assert "learner explicitly" in normalized

    types = g.prompts.get_text("concepts.types_assign.system")
    assert "When an Example refers to one or more figures" in types
    assert '[img src="https://..." alt="..."]' in types


def test_universal_question_task_inventory_and_type_mining_prompts():
    inventory = g.prompts.get_text("concepts.question_task_inventory.system")
    mining = g.prompts.get_text("concepts.type_mining.system")
    delta = g.prompts.get_text("concepts.type_mining_delta.system")
    mining_contract = " ".join(mining.split())
    delta_contract = " ".join(delta.split())
    assert "Question / Task Inventory" in inventory
    assert "content_objects" in inventory
    assert "math_objects" not in inventory
    assert "grammar_task" in inventory and "map_task" in inventory
    assert "coding_task" in inventory and "experiment_task" in inventory
    assert "type_title" in mining and "subject_skill_hint" in mining
    assert "Grammar Transformation" in mining
    assert "Code Tracing" in mining
    assert "Map Skill" in mining
    assert "Type is a reusable assessment/task pattern" in mining
    # Coverage must be inclusive, never strict: no inventory item may be dropped.
    assert "COVERAGE IS MANDATORY" in mining
    assert "NEVER skip an item" in mining
    assert "A missed question is a defect" in mining
    assert "EXAMPLES CARRY THE FULL SOURCE QUESTION" in mining
    assert "Do not shorten or truncate source questions" in mining
    # Cases are defined sub-types; examples carry the full questions.
    assert "CASE WORDING" in mining
    assert "case_title DEFINES the sub-type" in mining
    assert "checkpoint" in mining.lower()
    assert "[img src=" in mining
    assert "![" not in mining
    # Types must be properly defined (precise wording + definition).
    assert "TYPE WORDING" in mining
    assert "precise, self-explanatory pattern name" in mining
    assert "type_description must DEFINE the pattern" in mining
    # Reusable Type identity is chapter-wide; each Case owns one exact route.
    assert "Type owns only the reusable answering method" in mining_contract
    assert "Every Case owns its own concept/topic route" in mining_contract
    assert "direct formula calculations" in mining_contract
    assert "contextual/real-life modeling or applications" in mining_contract
    assert "incremental delta" in delta
    assert "never return an already classified question" in delta
    assert "complete source task" in delta
    assert "existing Type from another topic" in delta_contract
    assert "Emit a new Case with its own exact" in delta_contract
    embedding = g.prompts.get_text("concepts.type_embedding.system")
    embedding_contract = " ".join(embedding.split())
    assert "concept_id" in embedding and "type_ids" in embedding
    assert "every provided type_id MUST be assigned".lower() in embedding.lower()
    assert "already-constrained source topic" in embedding_contract
    assert "most granular level" in embedding_contract
    assert (
        "application, modeling, procedure, or worked-method concept"
        in embedding_contract
    )
    assert "Formula overlap is not concept identity" in embedding_contract
    # Culmination rows are part of the assignment payload.
    assert "is_culmination" in embedding
    assert "cross_topic_synthesis" in mining
    assert "later source topic" in embedding.lower()


def test_has_meaningful_types():
    assert g._has_meaningful_types(
        "Description: d // Types: Type 01: Direct Case 01: Find x Case 02: Solve y "
        "// Misconception: m"
    )
    assert not g._has_meaningful_types("Description: d // Misconception: m")
    assert not g._has_meaningful_types("Description: d // Types:  // Misconception: m")


def test_inject_types():
    base = "Description: def // Misconception: err"
    out = g._inject_types(base, "Type 01: Direct Case 01: Find x Case 02: Solve y")
    assert "Types: Type 01: Direct Case 01: Find x" in out
    assert "Misconception: err" in out


def test_merge_types_from_fallback():
    before = [{
        "topic": "T", "concept_title": "C",
        "concept_details": (
            "Description: d // Types: Type 01: Old Case 01: a Case 02: b // Misconception: m"
        ),
        "keywords": "",
    }]
    after = [{
        "topic": "T", "concept_title": "C",
        "concept_details": "Description: d // Misconception: m",
        "keywords": "",
    }]
    out = g._merge_types_from_fallback(after, before)
    assert g._has_meaningful_types(out[0]["concept_details"])


def test_canonicalize_uses_compact_skeleton_not_mmd(monkeypatch):
    captured = {}

    def fake_openai(system, user, **kw):
        captured["user"] = user
        return {"rows": [{
            "topic": "T", "concept": "C",
            "concept_description": (
                "Description: d // Misconceptions: Students may believe every "
                "equivalent representation must use identical notation."
            ),
            "keywords": "k",
        }]}

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    records = [{
        "topic": "T",
        "concept_title": "C",
        "concept_details": (
            "Description: d // Misconceptions: Students may believe every "
            "equivalent representation must use identical notation."
        ),
        "keywords": "",
    }]
    g._consolidate_concepts_via_api(records, subject="Math", mmd_text="# Chapter\nExercise problems here.")
    assert "Draft skeleton map" in captured["user"]
    assert "Exercise problems here" not in captured["user"]


def test_mined_type_body_includes_definition():
    body, n = g._mined_type_to_body({
        "type_title": "Dividing Powers with the Same Base",
        "type_description": "Given a quotient of powers with one base, apply "
                            "a^m ÷ a^n = a^(m-n) to simplify.",
        "case_prompts": [{"case_prompt": "Simplify p^9 ÷ p^3"}],
    }, 0)
    assert n == 1
    assert body.startswith("Type 01: Dividing Powers with the Same Base — ")
    assert "apply a^m ÷ a^n = a^(m-n) to simplify" in body
    assert "Case 01: Given the complete source context, simplifying" in body
    assert "Example 01: Simplify p^9 ÷ p^3" in body
    assert "Case 01: Simplify p^9 ÷ p^3" not in body
    # A definition identical to the title is not repeated.
    body2, _ = g._mined_type_to_body({
        "type_title": "Adding Numbers",
        "type_description": "Adding numbers.",
        "case_prompts": [{"case_prompt": "Find 2+3"}],
    }, 0)
    assert body2.startswith(
        "Type 01: Adding Numbers\nCase 01: Given the complete")
    assert body2.endswith("Example 01: Find 2+3")


def test_mined_type_body_includes_all_cases():
    body, n = g._mined_type_to_body({
        "type_title": "Solving Linear Equations",
        "case_prompts": [
            {"case_prompt": f"Solve equation {i}"} for i in range(1, 9)
        ],
    }, 0)
    assert n == 1
    assert body.count("Case ") == 8
    assert body.count("Example 01:") == 8
    assert "Example 01: Solve equation 1" in body
    assert "Example 01: Solve equation 8" in body


def test_mined_type_body_numbers_multiple_examples_within_one_defined_case():
    body, n = g._mined_type_to_body({
        "type_title": "Solving Linear Equations",
        "case_prompts": [{
            "case_title": (
                "Given a linear equation with one unknown, isolate the "
                "unknown using inverse operations"
            ),
            "examples": [
                {"example_prompt": "Solve 3x + 2 = 14."},
                {"example_prompt": "Solve 5y - 7 = 18."},
            ],
        }],
    }, 0)

    assert n == 1
    assert body.count("Case 01:") == 1
    assert "Example 01: Solve 3x + 2 = 14." in body
    assert "Example 02: Solve 5y - 7 = 18." in body


def test_type_cases_backfill_full_source_questions_from_inventory():
    inventory = {"items": [{
        "qid": "QINV-0001",
        "normalized_task": (
            "In triangle ABC, DE is parallel to BC and AD = 3 cm, DB = 2 cm, "
            "AE = 4.5 cm. Find EC with full reasoning."
        ),
        "requires_context": False,
    }]}
    types = [{
        "type_id": "TYPE-0001",
        "type_title": "Using BPT to Find an Unknown Segment",
        "source_question_ids": ["QINV-0001"],
        "case_prompts": [{
            "source_question_id": "QINV-0001",
            "case_prompt": "Find EC",
        }],
    }]
    out = g._backfill_type_cases_from_inventory(types, inventory)
    case = out[0]["case_prompts"][0]
    prompt = g._case_examples(case)[0]["example_prompt"]
    assert "AD = 3 cm" in prompt
    assert "Find EC with full reasoning" in prompt
    assert "case_prompt" not in case
    assert case["case_title"].startswith("Given the complete source context")


def test_type_cases_restore_authoritative_source_for_every_qid():
    image_url = "https://cdn.mathpix.com/cropped/source.jpg"
    inventory = {"items": [
        {
            "qid": "QINV-0006",
            "raw_task": (
                "Plot on a map of Europe the changes drawn up by the "
                "Vienna Congress."
            ),
        },
        {
            "qid": "QINV-0010",
            "raw_task": "Why was it unjust to deny women political rights?",
            "image_urls": [image_url],
        },
        {
            "qid": "QINV-0018",
            "raw_task": "Identify the attributes and interpret the painting.",
            "shared_context": (
                "Use the chart of symbols: broken chains, crown, sword, "
                "tricolour, and rays of the rising sun."
            ),
            "requires_context": True,
        },
    ]}
    paraphrases = {
        "QINV-0006": (
            "Plot on a map of Europe the territorial changes drawn up by the "
            "Vienna Congress."
        ),
        "QINV-0010": "Why was it unjust to deny women political rights?",
        "QINV-0018": "Identify the attributes and interpret the painting.",
    }
    types = [{
        "type_id": "TYPE-0001",
        "type_title": "Interpreting source tasks",
        "source_question_ids": list(paraphrases),
        "case_prompts": [
            {
                "case_id": f"CASE-{index:04d}",
                "case_title": "Complete the supplied source task",
                "examples": [{
                    "source_question_id": qid,
                    "example_prompt": prompt,
                }],
            }
            for index, (qid, prompt) in enumerate(paraphrases.items(), start=1)
        ],
    }]

    restored = g._backfill_type_cases_from_inventory(types, inventory)
    examples = [
        example
        for case in restored[0]["case_prompts"]
        for example in g._case_examples(case)
    ]
    expected_by_qid = {
        item["qid"]: g._inventory_task_text(item)
        for item in inventory["items"]
    }
    assert {
        example["source_question_id"]: example["example_prompt"]
        for example in examples
    } == expected_by_qid

    body, _ = g._mined_type_to_body(restored[0], 0)
    records = [{"concept_details": f"Types: {body}"}]
    assert g._rendered_inventory_coverage_defects(records, inventory) == {
        "missing": [],
        "duplicate": [],
    }


def test_mine_types_merges_focused_delta_for_only_missed_inventory(monkeypatch):
    calls = {"n": 0}
    task_one = "Use the first complete source task with every stated condition."
    task_two = "Use the second complete source task with every stated condition."

    def fake_openai(system, user, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"types": [{
                "type_id": "TYPE-0001", "type_title": "Pattern One",
                "type_description": "Immutable authored description.",
                "task_pattern": "Complete a source task under its conditions.",
                "topic_match_hint": "Topic A",
                "authored_marker": "preserve-me",
                "source_question_ids": ["QINV-0001"],
                "case_prompts": [{
                    "case_id": "CASE-0001",
                    "case_title": "First defined case",
                    "examples": [{
                        "source_question_id": "QINV-0001",
                        "example_prompt": task_one,
                    }],
                }],
            }]}
        assert "incremental delta" in system
        assert "MISSED INVENTORY ITEMS" in user
        assert "COMPACT EXISTING TYPE METADATA" in user
        assert "QINV-0002" in user
        assert "QINV-0001" not in user
        assert task_one not in user
        assert "Pattern One" in user
        assert "COMPLETE corrected" not in user
        return {"types": [{
            "type_id": "TYPE-0001",
            "source_question_ids": ["QINV-0002"],
            "case_prompts": [{
                "case_id": "NEW-CASE-0002",
                "case_title": "Second defined case",
                "examples": [{
                    "source_question_id": "QINV-0002",
                    "example_prompt": task_two,
                }],
            }],
        }]}

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    inventory = {"items": [
        {"qid": "QINV-0001", "topic_hint": "Topic A", "raw_task": task_one},
        {"qid": "QINV-0002", "topic_hint": "Topic A", "raw_task": task_two},
    ], "stats": {}}
    mined = g._mine_types_from_inventory_via_api(
        meta=g._metadata(subject="Math"), inventory=inventory)

    assert calls["n"] == 2
    assert len(mined["types"]) == 1
    merged = mined["types"][0]
    assert merged["authored_marker"] == "preserve-me"
    assert merged["type_description"] == "Immutable authored description."
    assert merged["source_question_ids"] == ["QINV-0001", "QINV-0002"]
    examples = [
        example
        for case in merged["case_prompts"]
        for example in g._case_examples(case)
    ]
    assert [(example["source_question_id"], example["example_prompt"])
            for example in examples] == [
        ("QINV-0001", task_one),
        ("QINV-0002", task_two),
    ]
    assert not g._uncovered_inventory_items(inventory, mined["types"])
    assert not g._duplicate_inventory_assignments(inventory, mined["types"])


def test_mine_types_keeps_delta_guards_and_restores_authoritative_source(
    monkeypatch,
):
    calls = {"n": 0}
    task_one = "Complete source task one without omitting any stated condition."
    task_two = "Complete source task two without omitting any stated condition."
    shared_context = "Use the labelled construction shown in Figure 2."
    image_url = "https://cdn.mathpix.com/cropped/focused-delta-2.png"
    authoritative_task_two = (
        f"{shared_context} {task_two} ![]({image_url})"
    )

    def fake_openai(system, user, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"types": [{
                "type_id": "TYPE-0001", "type_title": "Pattern One",
                "type_description": "GPT-authored metadata must remain.",
                "topic_match_hint": "Topic A",
                "source_question_ids": ["QINV-0001"],
                "case_prompts": [{
                    "case_id": "CASE-0001",
                    "case_title": "Original defined case",
                    "examples": [{
                        "source_question_id": "QINV-0001",
                        "example_prompt": task_one,
                    }],
                }],
            }]}
        if calls["n"] == 2:
            return {"types": [{
                "type_id": "TYPE-0001",
                "source_question_ids": ["QINV-0002"],
                "case_prompts": [{
                    "case_id": "NEW-CASE-0002",
                    "case_title": "Malformed legacy case",
                    "source_question_id": "QINV-0002",
                    "case_prompt": "shortened",
                }],
            }]}
        if calls["n"] == 3:
            return {"types": [{
                "type_id": "TYPE-0001",
                "source_question_ids": ["QINV-0001"],
                "case_prompts": [{
                    "case_id": "NEW-CASE-0003",
                    "case_title": "Extraneous already-classified case",
                    "examples": [{
                        "source_question_id": "QINV-0001",
                        "example_prompt": task_one,
                    }],
                }],
            }]}
        return {"types": [{
            "type_id": "TYPE-0001",
            "source_question_ids": ["QINV-0002"],
            "case_prompts": [{
                "case_id": "NEW-CASE-0004",
                "case_title": "Valid source-owned case",
                "examples": [{
                    "source_question_id": "QINV-0002",
                    "example_prompt": "shortened",
                }],
            }],
        }]}

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    inventory = {"items": [
        {
            "qid": "QINV-0001", "source_kind": "exercise",
            "topic_hint": "Topic A", "raw_task": task_one,
        },
        {
            "qid": "QINV-0002", "source_kind": "diagram_task",
            "topic_hint": "Topic A", "raw_task": task_two,
            "shared_context": shared_context,
            "requires_context": True,
            "image_urls": [image_url],
        },
    ], "stats": {}}
    mined = g._mine_types_from_inventory_via_api(
        meta=g._metadata(subject="Math"),
        inventory=inventory,
        max_focused_attempts=3,
    )

    # The malformed legacy shape and existing-qid claim were both rejected;
    # only the structurally valid delta was merged.
    assert calls["n"] == 4
    assert len(mined["types"]) == 1
    authored = mined["types"][0]
    assert authored["type_title"] == "Pattern One"
    assert authored["type_description"] == "GPT-authored metadata must remain."
    assert len(authored["case_prompts"]) == 2
    examples = [
        example
        for case in authored["case_prompts"]
        for example in g._case_examples(case)
    ]
    assert [
        (example["source_question_id"], example["example_prompt"])
        for example in examples
    ] == [
        ("QINV-0001", task_one),
        ("QINV-0002", g._inventory_task_text(inventory["items"][1])),
    ]
    assert all(
        example["example_prompt"] != "shortened" for example in examples)
    assert not g._uncovered_inventory_items(inventory, mined["types"])
    assert not g._duplicate_inventory_assignments(inventory, mined["types"])


def test_normalize_mined_types_recovers_live_nested_type_schema():
    inventory = {"items": [
        {"qid": "QINV-0001", "topic_hint": "Introduction", "raw_task": "one"},
        {"qid": "QINV-0002", "topic_hint": "Arithmetic Progressions", "raw_task": "two"},
        {"qid": "QINV-0003", "topic_hint": "nth Term of an AP", "raw_task": "three"},
        {"qid": "QINV-0004", "topic_hint": "Sum of First n Terms of an AP", "raw_task": "four"},
    ], "stats": {}}

    def mined_type(index, qid):
        return {
            "type_id": f"TYPE-{index:04d}",
            "type_title": f"Pattern {index}",
            "source_question_ids": [qid],
            "case_prompts": [{
                "case_title": f"Case {index}",
                "examples": [{
                    "source_question_id": qid,
                    "example_prompt": f"task {index}",
                }],
            }],
        }

    # /tmp/ap-live-fixed.json had this exact schema drift: each later Type
    # appeared as an entry in the preceding Type's case_prompts list.
    nested = [mined_type(1, "QINV-0001")]
    parent = nested[0]
    for index in range(2, 5):
        child = mined_type(index, f"QINV-{index:04d}")
        parent["case_prompts"].append(child)
        parent = child

    normalized = g._normalize_mined_type_candidate(nested, inventory)

    assert len(normalized) == 4
    assert not g._uncovered_inventory_items(inventory, normalized)
    assert not g._duplicate_inventory_assignments(inventory, normalized)
    assert all(
        not any(
            isinstance(case, dict) and case.get("type_id")
            for case in mined_type["case_prompts"]
        )
        for mined_type in normalized
    )


def test_mine_types_keeps_monotonic_broad_repairs_then_uses_delta(monkeypatch):
    inventory = {"items": [
        {"qid": f"QINV-{index:04d}", "topic_hint": "T", "raw_task": f"task {index}"}
        for index in range(1, 4)
    ], "stats": {}}

    def mined_type(type_id, qids, title="Reusable pattern"):
        return {
            "type_id": type_id,
            "type_title": title,
            "topic_match_hint": "T",
            "source_question_ids": qids,
            "case_prompts": [{
                "case_id": f"CASE-{type_id[-4:]}",
                "case_title": "Defined case",
                "examples": [{
                    "source_question_id": qid,
                    "example_prompt": f"task {int(qid[-4:])}",
                } for qid in qids],
            }],
        }

    responses = [
        [
            mined_type("TYPE-0001", ["QINV-0001", "QINV-0002"]),
            mined_type("TYPE-0002", ["QINV-0001"], "Duplicate pattern"),
        ],
        [mined_type("TYPE-0001", ["QINV-0001"])],
        [mined_type("TYPE-0001", ["QINV-0001", "QINV-0002"])],
    ]
    calls = {"n": 0}

    def fake_openai(system, user, **kwargs):
        index = calls["n"]
        calls["n"] += 1
        if index < 3:
            if index:
                assert "COMPLETE corrected" in user
            if index == 2:
                # The rejected response must not poison the next repair context.
                assert "QINV-0002" in user
            return {"types": responses[index]}
        assert "incremental delta" in system
        assert "QINV-0003" in user
        assert "QINV-0001" not in user
        assert "QINV-0002" not in user
        return {"types": [{
            "type_id": "TYPE-0001",
            "source_question_ids": ["QINV-0003"],
            "case_prompts": [{
                "case_id": "NEW-CASE-0002",
                "case_title": "Focused missing-item case",
                "examples": [{
                    "source_question_id": "QINV-0003",
                    "example_prompt": "task 3",
                }],
            }],
        }]}

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    mined = g._mine_types_from_inventory_via_api(
        meta=g._metadata(subject="Mathematics"),
        inventory=inventory,
        max_coverage_attempts=2,
    )

    assert calls["n"] == 4
    assert len(mined["types"]) == 1
    assert mined["types"][0]["type_title"] == "Reusable pattern"
    assert not g._uncovered_inventory_items(inventory, mined["types"])
    assert not g._duplicate_inventory_assignments(inventory, mined["types"])


def test_mine_types_retains_hard_gate_for_unrecoverable_empty_task(monkeypatch):
    calls = {"n": 0}

    def fake_openai(system, user, **kwargs):
        calls["n"] += 1
        return {"types": []}

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    with pytest.raises(
        RuntimeError, match=r"1 unclassified.*0 duplicate",
    ):
        g._mine_types_from_inventory_via_api(
            meta=g._metadata(subject="Mathematics"),
            inventory={"items": [{
                "qid": "QINV-0001",
                "source_kind": "exercise",
                "topic_hint": "Topic A",
                "raw_task": "",
                "normalized_task": "",
            }], "stats": {}},
            max_focused_attempts=1,
        )

    assert calls["n"] == 2


def test_single_item_fallback_preserves_source_image_and_topic():
    image_url = "https://cdn.mathpix.com/cropped/diagram-42.png"
    source_task = (
        "Study the construction in Figure 4.2 and determine the requested "
        "length, using every labelled value."
    )
    item = {
        "qid": "QINV-0042",
        "source_kind": "diagram_task",
        "topic_hint": "Geometric Constructions",
        "raw_task": source_task + "\nSolution: The length is 8 cm.",
        "normalized_task": "shortened task",
        "raw_solution_or_answer": "The length is 8 cm.",
        "image_urls": [image_url],
    }
    inventory = {"items": [item], "stats": {}}

    normalized, added = g._append_deterministic_type_fallbacks(
        [], missed_items=[item], inventory=inventory)

    assert added == 1
    assert len(normalized) == 1
    fallback = normalized[0]
    assert fallback["topic_match_hint"] == "Geometric Constructions"
    assert fallback["type_title"] == "Interpreting a Diagram to Complete a Task"
    assert fallback["case_prompts"][0]["case_title"] == (
        "Diagram-dependent task with its referenced visual and complete ask")
    example = g._case_examples(fallback["case_prompts"][0])[0]
    assert example == {
        "source_question_id": "QINV-0042",
        "example_prompt": (
            f'{source_task} [img src="{image_url}" alt="Fig. 4.2"]'
        ),
    }
    assert "The length is 8 cm." not in example["example_prompt"]


@pytest.mark.parametrize(
    "marker",
    [
        "Solution. The length is 8 cm.",
        "Sol. The length is 8 cm.",
        "Ans. The length is 8 cm.",
        "Answer — The length is 8 cm.",
    ],
)
def test_inventory_strips_common_solution_marker_variants(marker):
    prompt = "Find the missing length in the triangle."

    cleaned = g._sanitize_inventory_item({
        "source_kind": "worked_example",
        "raw_task": f"{prompt}\n{marker}",
        "normalized_task": f"{prompt}\n{marker}",
    })

    assert cleaned["raw_task"] == prompt
    assert cleaned["normalized_task"] == prompt
    assert cleaned["raw_solution_or_answer"] == ""


def test_exact_once_duplicate_backstop_prunes_all_duplicate_shapes():
    inventory = {"items": [
        {
            "qid": "QINV-0001",
            "topic_hint": "Topic A",
            "raw_task": "Full source question one with every stated condition.",
        },
        {
            "qid": "QINV-0002",
            "topic_hint": "Topic A",
            "raw_task": "Full source question two with every stated condition.",
        },
        {
            "qid": "QINV-0003",
            "topic_hint": "Topic B",
            "raw_task": "Full source question three with every stated condition.",
        },
    ], "stats": {}}
    types = [
        {
            "type_id": "TYPE-0001",
            "type_title": "Wrong-topic first placement",
            "topic_match_hint": "Topic B",
            "source_question_ids": ["QINV-0001", "QINV-0003"],
            "case_prompts": [{
                "case_title": "Mixed duplicate and unique examples",
                "examples": [
                    {
                        "source_question_id": "QINV-0001",
                        "example_prompt": inventory["items"][0]["raw_task"],
                        "marker": "wrong-topic-first",
                    },
                    {
                        "source_question_id": "QINV-0003",
                        "example_prompt": inventory["items"][2]["raw_task"],
                    },
                ],
            }],
        },
        {
            "type_id": "TYPE-0002",
            "type_title": "Matching-topic retained placement",
            "topic_match_hint": "Topic A",
            "source_question_ids": ["QINV-0001", "QINV-0002"],
            "case_prompts": [
                {
                    "case_title": "Duplicate examples within one Case",
                    "examples": [
                        {
                            "source_question_id": "QINV-0001",
                            "example_prompt": "Shortened question one.",
                            "marker": "matching-first",
                        },
                        {
                            "source_question_id": "QINV-0001",
                            "example_prompt": inventory["items"][0]["raw_task"],
                            "marker": "matching-second",
                        },
                        {
                            "source_question_id": "QINV-0002",
                            "example_prompt": inventory["items"][1]["raw_task"],
                        },
                    ],
                },
                {
                    "case_title": "Duplicate-only Case",
                    "examples": [{
                        "source_question_id": "QINV-0001",
                        "example_prompt": inventory["items"][0]["raw_task"],
                        "marker": "matching-later-case",
                    }],
                },
            ],
        },
        {
            "type_id": "TYPE-0003",
            "type_title": "Legacy-only duplicate Type",
            "topic_match_hint": "Topic A",
            "source_question_ids": ["QINV-0001"],
            "case_prompts": [
                {
                    "case_title": "Legacy duplicate",
                    "source_question_id": "QINV-0001",
                    "case_prompt": inventory["items"][0]["raw_task"],
                },
                {
                    "case_title": "Model-emitted empty Case",
                    "examples": [],
                },
            ],
        },
    ]

    out, removed = g._apply_exact_once_duplicate_backstop(types, inventory)

    assert removed == 4
    assert len(out) == 2
    assert not g._uncovered_inventory_items(inventory, out)
    assert not g._duplicate_inventory_assignments(inventory, out)
    assert all(count == 1 for count in g._inventory_assignment_counts(out).values())
    assert not any(
        item["type_title"] == "Legacy-only duplicate Type" for item in out)

    matching = next(
        item for item in out
        if item["type_title"] == "Matching-topic retained placement")
    retained = [
        example
        for case in matching["case_prompts"]
        for example in g._case_examples(case)
        if example.get("source_question_id") == "QINV-0001"
    ]
    assert len(retained) == 1
    assert retained[0]["marker"] == "matching-first"
    assert retained[0]["example_prompt"] == inventory["items"][0]["raw_task"]
    assert len(matching["case_prompts"]) == 1

    wrong_topic = next(
        item for item in out
        if item["type_title"] == "Wrong-topic first placement")
    assert wrong_topic["source_question_ids"] == ["QINV-0003"]
    assert all(
        example.get("source_question_id") != "QINV-0001"
        for case in wrong_topic["case_prompts"]
        for example in g._case_examples(case)
    )
    prompts_by_qid = {
        example["source_question_id"]: example["example_prompt"]
        for item in out
        for case in item["case_prompts"]
        for example in g._case_examples(case)
    }
    assert prompts_by_qid == {
        item["qid"]: item["raw_task"] for item in inventory["items"]
    }


def test_exact_once_duplicate_backstop_backfills_trace_only_ids():
    inventory = {"items": [
        {
            "qid": "QINV-0001",
            "topic_hint": "Topic A",
            "raw_task": "Complete first source question, copied without shortening.",
        },
        {
            "qid": "QINV-0002",
            "topic_hint": "Topic B",
            "raw_task": "Complete second source question, copied without shortening.",
        },
    ], "stats": {}}
    types = [
        {
            "type_id": "TYPE-0001",
            "type_title": "Wrong-topic trace",
            "topic_match_hint": "Topic B",
            "source_question_ids": ["QINV-0001", "QINV-0002"],
            "case_prompts": [],
        },
        {
            "type_id": "TYPE-0002",
            "type_title": "Matching-topic trace",
            "topic_match_hint": "Topic A",
            "source_question_ids": ["QINV-0001"],
            "case_prompts": [],
        },
    ]

    out, removed = g._apply_exact_once_duplicate_backstop(types, inventory)

    assert removed == 1
    assert not g._uncovered_inventory_items(inventory, out)
    assert not g._duplicate_inventory_assignments(inventory, out)
    matching = next(
        item for item in out if item["type_title"] == "Matching-topic trace")
    examples = [
        example
        for case in matching["case_prompts"]
        for example in g._case_examples(case)
    ]
    assert examples == [{
        "source_question_id": "QINV-0001",
        "example_prompt": inventory["items"][0]["raw_task"],
    }]
    wrong_topic = next(
        item for item in out if item["type_title"] == "Wrong-topic trace")
    assert wrong_topic["source_question_ids"] == ["QINV-0002"]


def test_mine_types_uses_duplicate_backstop_only_after_repairs(monkeypatch):
    inventory = {"items": [{
        "qid": "QINV-0001", "topic_hint": "T", "raw_task": "Question one",
    }], "stats": {}}
    duplicate = [
        {
            "type_id": f"TYPE-{index:04d}",
            "type_title": f"Pattern {index}",
            "source_question_ids": ["QINV-0001"],
            "case_prompts": [{
                "case_title": f"Case {index}",
                "examples": [{
                    "source_question_id": "QINV-0001",
                    "example_prompt": "Question one",
                }],
            }],
        }
        for index in range(1, 3)
    ]
    calls = {"n": 0}

    def fake_openai(system, user, **kwargs):
        calls["n"] += 1
        return {"types": duplicate}

    logs = []
    monkeypatch.setattr(g, "_openai_json", fake_openai)
    monkeypatch.setattr(
        g.progress, "log",
        lambda message, **kwargs: logs.append((message, kwargs)),
    )
    mined = g._mine_types_from_inventory_via_api(
        meta=g._metadata(subject="Mathematics"),
        inventory=inventory,
        max_coverage_attempts=2,
    )

    assert calls["n"] == 3
    assert len(mined["types"]) == 1
    assert not g._uncovered_inventory_items(inventory, mined["types"])
    assert not g._duplicate_inventory_assignments(inventory, mined["types"])
    assert any(
        "exact-once duplicate backstop removed 1 duplicate placement" in message
        for message, _ in logs
    )


def test_pipeline_builds_culminations_before_types(monkeypatch):
    # This test stubs the WHOLE extraction function, which the Q19 early
    # inventory track legitimately bypasses — pin the sequential path.
    monkeypatch.setenv("AEGIS_SOURCE_CHUNK_WORKERS", "1")
    monkeypatch.setattr(g.config, "use_live_generation", lambda: True)
    order: list[str] = []

    monkeypatch.setattr(g, "_extract_skeleton_via_api", lambda chunks, **kw: [
        {"topic": "T", "parent_concept": "P", "concept_title": "C",
         "concept_details": "Description: d", "keywords": ""},
    ])
    monkeypatch.setattr(g, "_consolidate_concepts_via_api", lambda records, **kw: records)
    monkeypatch.setattr(g, "_ensure_mastery_lines_via_api", lambda records, **kw: records)
    monkeypatch.setattr(
        g, "_extract_question_task_inventory_via_api", lambda **kw: g._empty_inventory())
    monkeypatch.setattr(
        g, "_mine_types_from_inventory_via_api", lambda **kw: {"types": []})

    culmination_output: list[str] = []

    def fake_culminations(records, **kw):
        order.append("culmination")
        # This topic teaches one concept, so the authored culmination pass
        # produces no culmination row (nothing is synthesized from code).
        built = [dict(r) for r in records]
        culmination_output[:] = [r["concept_title"] for r in built]
        return built

    def fake_final_content(records, **kw):
        # Type allocation now happens entirely inside the rewritten Phase 3
        # behind this seam, after the 81% boundary.
        order.append("types")
        # The Phase 3 finalizer must observe the culmination pass's exact
        # output. (This topic teaches one concept, so that output carries no
        # culmination row — a culmination consolidates several concepts.)
        assert [r["concept_title"] for r in records] == culmination_output
        return records

    monkeypatch.setattr(g, "_build_culminations_via_api", fake_culminations)
    monkeypatch.setattr(g, "_prepare_final_concept_content", fake_final_content)
    monkeypatch.setattr(g, "_repair_records_via_api", lambda records, **kw: records)
    monkeypatch.setattr(
        g, "_ensure_mastery_lines_via_api", lambda records, **kw: records)
    monkeypatch.setattr(
        g, "_validate_final_or_raise",
        lambda records, **kw: {"ok": True, "errors": [], "summary": {}})
    checkpoints = []
    g.concepts_from_mmd(
        "## T\nbody",
        subject="Mathematics",
        checkpoint_callback=checkpoints.append,
    )
    assert order == ["culmination", "types"]
    assert [checkpoint["stage"] for checkpoint in checkpoints] == [
        "skeleton_complete",
        "canonical_skeleton",
        "description_method_snapshot",
        "question_inventory",
        "type_taxonomy_ready",
        "pre_type_assignment",
        # The former 91% checkpoint claimed allocation before topology was
        # final. The next durable artifact is now the validated 98% map.
        "final_content_ready",
    ]
    pre_type_checkpoint = next(
        checkpoint
        for checkpoint in checkpoints
        if checkpoint["stage"] == "pre_type_assignment"
    )
    # The checkpoint captured after the culmination pass carries exactly what
    # that pass produced.
    assert [
        row["concept_title"] for row in pre_type_checkpoint["records"]
    ] == culmination_output


def test_pipeline_resume_checkpoint_skips_expensive_gpt_stages(monkeypatch):
    monkeypatch.setattr(g.config, "use_live_generation", lambda: True)
    monkeypatch.setattr(
        g,
        "_openai_json",
        lambda *a, **kw: (_ for _ in ()).throw(
            AssertionError("checkpoint resume must not invoke an unmocked API pass")),
    )
    monkeypatch.setattr(
        g,
        "_extract_skeleton_via_api",
        lambda *a, **kw: (_ for _ in ()).throw(
            AssertionError("skeleton extraction must not rerun")),
    )
    assigned = []

    def fake_final_content(records, **kw):
        # The rewritten Phase 3 owns everything after the 81% boundary; the
        # resume must reach it without replaying any pre-81% GPT stage.
        assigned.append([dict(row) for row in records])
        return records

    monkeypatch.setattr(g, "_prepare_final_concept_content", fake_final_content)
    monkeypatch.setattr(g, "_repair_records_via_api", lambda records, **kw: records)
    monkeypatch.setattr(
        g, "_validate_final_or_raise",
        lambda records, **kw: {"ok": True, "errors": [], "summary": {}},
    )
    checkpoint = g._make_concept_checkpoint(
        "pre_type_assignment",
        records=[
            {
                "topic": "T",
                "parent_concept": "P",
                "concept_title": "C",
                "concept_details": (
                    "Description: d // Misconception/ Error Analysis: "
                    "Misconceptions: Students may believe the checkpoint "
                    "concept applies outside its stated topic.; Error Analysis: "
                    "Students may omit the topic condition when applying the "
                    "checkpoint concept."
                ),
                "keywords": "",
            },
            {
                "topic": "T",
                "parent_concept": "Culmination",
                "concept_title": "Culmination - C",
                "concept_details": "Description: Recap",
                "keywords": "",
            },
        ],
        question_task_inventory={
            "items": [],
            "stats": {"total_inventory_items": 0},
        },
        mined_types={"types": []},
        method_row_snapshot=[],
    )
    callbacks = []
    out = g.concepts_from_mmd(
        "## T\nbody",
        subject="Mathematics",
        resume_checkpoint=checkpoint,
        checkpoint_callback=callbacks.append,
    )
    assert assigned
    assert out
    assert [checkpoint["stage"] for checkpoint in callbacks] == [
        "final_content_ready",
    ]


def test_skeleton_chunk_checkpoint_resumes_after_completed_chunks(monkeypatch):
    chunks = [
        {"text": "First chunk source.", "sections": []},
        {"text": "Second chunk source.", "sections": []},
        {"text": "Third chunk source.", "sections": []},
    ]

    def saved_chunk(index, title):
        return {
            "chunk_index": index,
            "chunk_count": len(chunks),
            "chunk_sha256": g._chunk_checkpoint_sha256(chunks[index - 1]),
            "records": [{
                "topic": "T",
                "parent_concept": "P",
                "concept_title": title,
                "concept_details": f"Description: {title}",
                "keywords": "",
            }],
        }

    calls = []

    def fake_openai(_system, user, **_kwargs):
        if "audit a concept-skeleton extraction" in _system:
            # The per-chunk audit verdict has its own regressions; a restored
            # chunk must not re-spend it, so only chunk 3 is audited here.
            assert "Third chunk source." in user
            return {"coverage": "complete", "grain": "sound", "reason": ""}
        calls.append(user)
        assert "Chunk 3 of 3" in user
        return {"rows": [{
            "topic": "T",
            "parent_concept": "P",
            "concept": "Third",
            "concept_description": "Description: Third",
            "keywords": "",
            "source_evidence": "",
        }]}

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    monkeypatch.setattr(
        g, "_repair_records_via_api", lambda records, **kwargs: records)
    checkpoints = []

    records = g._extract_skeleton_via_api(
        chunks,
        meta=g._metadata(subject="Science"),
        resume_chunks=[
            saved_chunk(1, "First"),
            saved_chunk(2, "Second"),
        ],
        checkpoint_callback=checkpoints.append,
    )

    assert len(calls) == 1
    assert {record["concept_title"] for record in records} == {
        "First", "Second", "Third",
    }
    assert checkpoints[-1]["stage"] == "skeleton_chunks"
    assert len(checkpoints[-1]["completed_chunks"]) == 3


def test_post_type_checkpoint_reallocates_on_final_topology(
    monkeypatch,
):
    monkeypatch.setattr(g.config, "use_live_generation", lambda: True)
    allocations: list[list[str]] = []

    def finalize_after_freeze(records, **_kwargs):
        # Allocation lives in the rewritten Phase 3 behind this seam. It
        # re-decides Types from the sealed envelope (Assemble re-renders
        # allocation from Host decisions), so the stale saved "Types:" text
        # in the resumed rows is input evidence only and never ships as-is.
        allocations.append([
            record["concept_title"] for record in records
        ])
        return records

    monkeypatch.setattr(
        g, "_prepare_final_concept_content", finalize_after_freeze)
    monkeypatch.setattr(
        g, "_repair_records_via_api", lambda records, **kwargs: records)
    monkeypatch.setattr(
        g, "_ensure_mastery_lines_via_api",
        lambda records, **kwargs: records,
    )
    monkeypatch.setattr(
        g, "_validate_final_or_raise",
        lambda records, **kwargs: {
            "ok": True,
            "errors": [],
            "summary": {},
        },
    )
    checkpoint = {
        "schema_version": g._CONCEPT_CHECKPOINT_SCHEMA,
        "stage_schema_version": (
            g._CONCEPT_CHECKPOINT_STAGES["post_type_assignment"]["version"]
        ),
        "stage": "post_type_assignment",
        "records": [
            {
                "topic": "T",
                "parent_concept": "P",
                "concept_title": "C",
                "concept_details": (
                    "Description: A complete concept description. // "
                    "Types: Type 01: Stale allocation Case 01: Stale case "
                    "Example 01: Stale source task. // Error Analysis: "
                    "Students may omit a required step."
                ),
                "keywords": "",
            },
            {
                "topic": "T",
                "parent_concept": "Culmination",
                "concept_title": "Culmination - C",
                "concept_details": "Description: Recap of C.",
                "keywords": "",
            },
        ],
        "question_task_inventory": {"items": [], "stats": {}},
        "mined_types": {"types": []},
        "method_row_snapshot": [],
        "progress": 0.91,
    }
    callbacks = []

    records = g.concepts_from_mmd(
        "## T\nbody",
        subject="Science",
        resume_checkpoint=checkpoint,
        checkpoint_callback=callbacks.append,
    )

    assert records
    # A resumed 91% artifact cannot skip reallocation: the Phase 3 finalizer
    # runs exactly once over the saved topology (only a final_content_ready
    # checkpoint bypasses it), and the next durable artifact is the 98% map.
    assert allocations == [["C", "Culmination - C"]]
    assert [item["stage"] for item in callbacks] == [
        "final_content_ready",
    ]


def test_post_type_checkpoint_reassigns_when_anchor_refresh_adds_uncertified_qid(
    monkeypatch,
):
    records = [{
        "topic": "T",
        "parent_concept": "P",
        "concept_title": "Current Relationship",
        "concept_details": (
            "Description: Relate the supplied quantities in a supported "
            "calculation. // Types: Type 01: Stale saved Type "
            "Case 01: A stale saved case Example 01: Stale saved question."
        ),
        "keywords": "",
    }]
    checkpoint = g._make_concept_checkpoint(
        "post_type_assignment",
        records=records,
        question_task_inventory={"items": [], "stats": {}},
        mined_types={"types": []},
        method_row_snapshot=[],
    )
    refreshed_inventory = {"items": [{
        "qid": "QINV-0001",
        "source_kind": "exercise",
        "topic_hint": "T",
        "raw_task": (
            "Calculate the requested quantity from the supplied values and "
            "explain the substitution."
        ),
    }], "stats": {"total_inventory_items": 1}}
    reconciled_mined = {"types": [{
        "type_id": "TYPE-0001",
        "type_title": "Applying the Current Relationship",
        "topic_match_hint": "T",
        "source_question_ids": ["QINV-0001"],
        "case_prompts": [],
    }]}
    monkeypatch.setattr(
        g,
        "_refresh_inventory_from_source_anchors",
        lambda *_args, **_kwargs: refreshed_inventory,
    )
    monkeypatch.setattr(
        g,
        "_reconcile_resumed_mined_types",
        lambda *_args, **_kwargs: reconciled_mined,
    )
    # The demoted checkpoint replays the granularity gate; the fragmentation
    # question is a model verdict, stubbed healthy so the reassignment path
    # under test proceeds.
    monkeypatch.setattr(
        g.type_granularity_decision,
        "fragmentation_verdict",
        lambda *_args, **_kwargs: {
            "fragmented": False,
            "rationale": "A single reusable method.",
            "review_flags": [],
        },
    )
    assignments: list[bool] = []
    monkeypatch.setattr(
        g,
        "_prepare_final_concept_content",
        lambda current, **kwargs: assignments.append(True) or current,
    )
    emitted: list[dict] = []

    out, inventory, mined, _snapshot = (
        g._run_live_concept_pre_final_stages(
            "## T\nSource body",
            subject="Science",
            board="CBSE",
            chapter_title="Chapter",
            chunks=[],
            sections=[],
            method_anchors=[],
            headings=[],
            source_topic_excerpts=[],
            allow_chapter_title_topic=False,
            meta={},
            artifacts={},
            resume_checkpoint=checkpoint,
            checkpoint_callback=emitted.append,
        )
    )

    assert out
    # Inventory semantics are refreshed now, but allocation remains deferred
    # until the final concept topology is available.
    assert assignments == []
    assert "Stale saved Type" not in out[0]["concept_details"]
    assert inventory == refreshed_inventory
    assert mined["_topology_allocation_contract"]["state"] == "deferred"
    assert not g._placement_certification_contract_complete(mined, inventory)
    assert emitted == []


def test_final_content_checkpoint_skips_semantic_api_repair(monkeypatch):
    monkeypatch.setattr(g.config, "use_live_generation", lambda: True)
    monkeypatch.setattr(
        g,
        "_prepare_final_concept_content",
        lambda *a, **kw: (_ for _ in ()).throw(
            AssertionError("final semantic/API repair must not rerun")),
    )
    monkeypatch.setattr(
        g,
        "_openai_json",
        lambda *a, **kw: (_ for _ in ()).throw(
            AssertionError("final checkpoint resume must not call the API")),
    )
    validated = []
    monkeypatch.setattr(
        g,
        "_validate_final_or_raise",
        lambda records, **kw: validated.append(records) or {
            "ok": True,
            "errors": [],
            "summary": {},
        },
    )
    checkpoint = g._make_concept_checkpoint(
        "final_content_ready",
        records=[{
            "topic": "T",
            "parent_concept": "P",
            "concept_title": "C",
            "concept_details": (
                "Description: A complete concept description. // "
                "Misconception/ Error Analysis: Misconceptions: Learners "
                "may believe every step is optional.; Error Analysis: "
                "Students may omit a required step."
            ),
            "keywords": "",
            # Q1: the row carries the analysis section, so it models an
            # allotted row (assemble-stamped marker).
            "_aegis_analysis_allotments": ["LA-0001", "LA-0002"],
        }],
        question_task_inventory={"items": [], "stats": {}},
        mined_types={"types": []},
        method_row_snapshot=[],
    )
    callbacks = []

    records = g.concepts_from_mmd(
        "## T\nbody",
        subject="Science",
        resume_checkpoint=checkpoint,
        checkpoint_callback=callbacks.append,
    )

    assert records
    assert validated
    # The semantic/API finalizer remains skipped. No deterministic content
    # is synthesized any more (no mastery template, no recap stamp), so the
    # resumed terminal payload is byte-identical and is not re-persisted.
    assert callbacks == []


def test_no_pre_learning_derivation_machinery_survives():
    """The legacy pre-learning derivation lane is gone from app/.

    Its two entry points, its two registry prompts, its three checkpoint
    stages, and its normalized-title exclusion all went with restructure
    step 7. Two of the deleted lines were raises — including one that
    killed a run whenever the exclusion emptied the map, which is exactly
    the volume-derived halt on a thin chapter that CLAUDE.md Rule 1
    forbids. Phase 03 captures prerequisites during the run instead.
    """
    app_dir = Path(__file__).resolve().parents[1] / "app"
    tokens = (
        "pre_learning_from_rows",
        "pre_learning_from_concepts",
        "prelearning.system",
        "prelearning.auditor",
        "_exclude_current_chapter_concepts",
        "_flatten_pre_topics",
        "_prelearning_system",
        "pre_derivation_draft",
        "pre_derivation_audited",
        "pre_learner_analysis",
        "live pre-learning derivation returned no",
    )
    # prompts.RETIRED_PROMPT_KEYS names the two retired prompt keys on
    # purpose, so a stranded operator override is pruned instead of left
    # unreachable in DATA_DIR/prompt_overrides.json. That is a tombstone,
    # not machinery.
    tombstones = {app_dir / "services" / "prompts.py"}
    hits = sorted(
        f"{path.name}:{token}"
        for path in app_dir.rglob("*.py")
        if path not in tombstones
        for token in tokens
        if token in path.read_text(encoding="utf-8")
    )
    assert hits == []
    # The POST 81% checkpoint boundary is NOT pre-learning and must survive.
    assert g._CONCEPT_CHECKPOINT_STAGE == "pre_type_assignment"
    assert "pre_type_assignment" in g._CONCEPT_CHECKPOINT_STAGES

"""Concept-generation prompts must require rich Types classification."""
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
    assert "One Type = one distinct reusable subject-appropriate assessment/task pattern" in types_system
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
    assert "Preserve Description exactly" in types
    # Types run after the culmination pass; culminations may receive mixed Types.
    assert "Culmination rows may receive Types" in types
    assert "Preserve valid fields, including parent_concept, Types" in repair


def test_universal_question_task_inventory_and_type_mining_prompts():
    inventory = g.prompts.get_text("concepts.question_task_inventory.system")
    mining = g.prompts.get_text("concepts.type_mining.system")
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
    assert "cdn.mathpix.com" in mining
    # Types must be properly defined (precise wording + definition).
    assert "TYPE WORDING" in mining
    assert "precise, self-explanatory pattern name" in mining
    assert "type_description must DEFINE the pattern" in mining
    embedding = g.prompts.get_text("concepts.type_embedding.system")
    assert "concept_id" in embedding and "type_ids" in embedding
    assert "every provided type_id MUST be assigned".lower() in embedding.lower()
    # Culmination rows are part of the assignment payload.
    assert "is_culmination" in embedding


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
                "Description: d // Misconception: m"
            ),
            "keywords": "k",
        }]}

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    records = [{"topic": "T", "concept_title": "C", "concept_details": "Description: d // Misconception: m", "keywords": ""}]
    g._consolidate_concepts_via_api(records, subject="Math", mmd_text="# Chapter\nExercise problems here.")
    assert "Draft skeleton map" in captured["user"]
    assert "Exercise problems here" not in captured["user"]


def test_refine_descriptions_via_api_strips_existing_types(monkeypatch):
    captured = {}

    def fake_openai(system, user, **kw):
        captured["system"] = system
        captured["user"] = user
        return {"rows": [{
            "topic": "T", "concept": "C",
            "concept_description": (
                "Description: A clear source-grounded description for lesson planning. "
                "It states what the concept means and when it is used. // "
                "Misconception: Students may reverse the operation."
            ),
            "keywords": "k",
        }]}

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    records = [{
        "topic": "T", "concept_title": "C",
        "concept_details": (
            "Description: weak // Types: Type 01: Evaluation Case 01: Find x Case 02: Find y "
            "// Misconception: Students may reverse the operation."
        ),
        "keywords": "k",
    }]
    out = g._refine_descriptions_via_api(records, subject="Math", mmd_text="# Chapter\nConcept source.")
    assert "description-only editor" in captured["system"]
    assert "RELEVANT SOURCE TEXT" in captured["user"]
    assert "clear source-grounded description" in out[0]["concept_details"]
    # The description pass is not allowed to carry Types in the staged architecture.
    assert "Types:" not in out[0]["concept_details"]


def test_assign_types_uses_pure_api_id_assignment(monkeypatch):
    captured = {}

    def fake_openai(system, user, **kw):
        captured["system"] = system
        captured["user"] = user
        # Pure-API assignment: map every type_id to a concept_id (exact IDs only).
        return {"assignments": [
            {"concept_id": "CONCEPT-0001", "type_ids": ["TYPE-0001"]},
            {"concept_id": "CONCEPT-0002", "type_ids": ["TYPE-0002"]},
        ]}

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    # Types run after the culmination pass, so the records include a
    # culmination row (last within its topic).
    records = [
        {"topic": "T", "parent_concept": "P", "concept_title": "Adding Numbers",
         "concept_details": "Description: add // Misconception: m", "keywords": ""},
        {"topic": "T", "parent_concept": "P", "concept_title": "Dividing Powers",
         "concept_details": "Description: divide", "keywords": ""},
        {"topic": "T", "parent_concept": "Culmination",
         "concept_title": "Culmination - Adding Numbers and Dividing Powers",
         "concept_details": "Description: Recap", "keywords": ""},
    ]
    mined = {"types": [
        {"type_id": "TYPE-0001", "type_title": "Adding Given Numbers",
         "case_prompts": [{"case_prompt": "Find the sum of 2 and 3 using addition."}]},
        {"type_id": "TYPE-0002", "type_title": "Dividing Powers with the Same Base",
         "case_prompts": [{"case_prompt": "Simplify p^9 divided by p^3 using exponent laws."}]},
    ]}
    out = g._assign_types_via_api(
        records, subject="Math", mmd_text="# Chapter\nsrc",
        question_task_inventory={"items": []}, mined_types=mined)
    assert "Assign every mined Type" in captured["system"]
    assert "CONCEPT-0001" in captured["user"] and "TYPE-0002" in captured["user"]
    # Every mined Type landed on its assigned concept (joined by exact IDs).
    assert g._has_meaningful_types(out[0]["concept_details"])
    assert "Adding Given Numbers" in out[0]["concept_details"]
    assert g._has_meaningful_types(out[1]["concept_details"])
    assert "Dividing Powers with the Same Base" in out[1]["concept_details"]


def test_assign_mined_types_retries_until_all_covered(monkeypatch):
    calls = {"n": 0}

    def fake_openai(system, user, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            # First attempt only assigns one of the two Types.
            return {"assignments": [{"concept_id": "CONCEPT-0001", "type_ids": ["TYPE-0001"]}]}
        # Retry with the missing type_id assigns the rest.
        return {"assignments": [{"concept_id": "CONCEPT-0002", "type_ids": ["TYPE-0002"]}]}

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    records = [
        {"topic": "T", "parent_concept": "P", "concept_title": "Concept One",
         "concept_details": "Description: one", "keywords": ""},
        {"topic": "T", "parent_concept": "P", "concept_title": "Concept Two",
         "concept_details": "Description: two", "keywords": ""},
    ]
    mined = {"types": [
        {"type_id": "TYPE-0001", "type_title": "Pattern One",
         "case_prompts": [{"case_prompt": "Apply pattern one to solve the given classroom task."}]},
        {"type_id": "TYPE-0002", "type_title": "Pattern Two",
         "case_prompts": [{"case_prompt": "Apply pattern two to solve the given classroom task."}]},
    ]}
    out = g._assign_mined_types_via_api(records, meta=g._metadata(subject="Math"), mined_types=mined)
    assert calls["n"] >= 2  # retried because the first attempt missed a type_id
    assert g._has_meaningful_types(out[0]["concept_details"])
    assert g._has_meaningful_types(out[1]["concept_details"])
    assert "Pattern One" in out[0]["concept_details"]
    assert "Pattern Two" in out[1]["concept_details"]


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
    assert "Case 01: Simplify p^9 ÷ p^3" in body
    # A definition identical to the title is not repeated.
    body2, _ = g._mined_type_to_body({
        "type_title": "Adding Numbers",
        "type_description": "Adding numbers.",
        "case_prompts": [{"case_prompt": "Find 2+3"}],
    }, 0)
    assert body2 == "Type 01: Adding Numbers Case 01: Find 2+3"


def test_mined_type_body_includes_all_cases():
    body, n = g._mined_type_to_body({
        "type_title": "Solving Linear Equations",
        "case_prompts": [
            {"case_prompt": f"Solve equation {i}"} for i in range(1, 9)
        ],
    }, 0)
    assert n == 1
    assert "Case 01: Solve equation 1" in body
    assert "Case 08: Solve equation 8" in body


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
    prompt = out[0]["case_prompts"][0]["case_prompt"]
    assert "AD = 3 cm" in prompt
    assert "Find EC with full reasoning" in prompt


def test_mine_types_retries_uncovered_inventory_items(monkeypatch):
    calls = {"n": 0}

    def fake_openai(system, user, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            # First mining pass only classifies one of the two items.
            return {"types": [{
                "type_id": "TYPE-0001", "type_title": "Pattern One",
                "source_question_ids": ["QINV-0001"],
                "case_prompts": [{"case_prompt": "do one", "source_question_id": "QINV-0001"}],
            }]}
        # Coverage retry must receive defects and return a complete corrected list.
        assert "COVERAGE DEFECTS TO FIX" in user
        assert "COMPLETE corrected" in user
        assert "QINV-0002" in user
        return {"types": [
            {
                "type_id": "TYPE-0001", "type_title": "Pattern One",
                "source_question_ids": ["QINV-0001"],
                "case_prompts": [{"case_prompt": "do one", "source_question_id": "QINV-0001"}],
            },
            {
                "type_id": "TYPE-0002", "type_title": "Pattern Two",
                "source_question_ids": ["QINV-0002"],
                "case_prompts": [{"case_prompt": "do two", "source_question_id": "QINV-0002"}],
            },
        ]}

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    inventory = {"items": [
        {"qid": "QINV-0001", "normalized_task": "one"},
        {"qid": "QINV-0002", "normalized_task": "two"},
    ], "stats": {}}
    mined = g._mine_types_from_inventory_via_api(
        meta=g._metadata(subject="Math"), inventory=inventory)
    assert calls["n"] == 2
    assert {t["type_id"] for t in mined["types"]} == {"TYPE-0001", "TYPE-0002"}
    assert not g._uncovered_inventory_items(inventory, mined["types"])


def test_mine_types_coverage_merges_into_existing_type(monkeypatch):
    calls = {"n": 0}

    def fake_openai(system, user, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"types": [{
                "type_id": "TYPE-0001", "type_title": "Pattern One",
                "source_question_ids": ["QINV-0001"],
                "case_prompts": [{"case_prompt": "do one", "source_question_id": "QINV-0001"}],
            }]}
        # The retry returns the COMPLETE corrected Type with both questions.
        return {"types": [{
            "type_id": "TYPE-0001", "type_title": "Pattern One",
            "source_question_ids": ["QINV-0001", "QINV-0002"],
            "case_prompts": [
                {"case_prompt": "do one", "source_question_id": "QINV-0001"},
                {"case_prompt": "do two", "source_question_id": "QINV-0002"},
            ],
        }]}

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    inventory = {"items": [
        {"qid": "QINV-0001", "normalized_task": "one"},
        {"qid": "QINV-0002", "normalized_task": "two"},
    ], "stats": {}}
    mined = g._mine_types_from_inventory_via_api(
        meta=g._metadata(subject="Math"), inventory=inventory)
    assert len(mined["types"]) == 1
    merged = mined["types"][0]
    assert set(merged["source_question_ids"]) == {"QINV-0001", "QINV-0002"}
    assert len(merged["case_prompts"]) == 2
    assert {c["case_prompt"] for c in merged["case_prompts"]} == {"one", "two"}
    assert not g._uncovered_inventory_items(inventory, mined["types"])


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


def test_mine_types_recovers_after_regressive_coverage_candidate(monkeypatch):
    inventory = {"items": [
        {"qid": f"QINV-{index:04d}", "topic_hint": "T", "raw_task": f"task {index}"}
        for index in range(1, 4)
    ], "stats": {}}

    def mined_type(qids):
        return {
            "type_id": "TYPE-0001",
            "type_title": "Reusable pattern",
            "source_question_ids": qids,
            "case_prompts": [{
                "case_title": "Defined case",
                "examples": [{
                    "source_question_id": qid,
                    "example_prompt": f"task {int(qid[-4:])}",
                } for qid in qids],
            }],
        }

    responses = [
        [mined_type(["QINV-0001", "QINV-0002"])],
        [mined_type(["QINV-0001"])],
        [mined_type(["QINV-0001", "QINV-0002", "QINV-0003"])],
    ]
    calls = {"n": 0}

    def fake_openai(system, user, **kwargs):
        index = calls["n"]
        calls["n"] += 1
        if index == 2:
            # The rejected response must not poison the next repair context.
            assert "QINV-0002" in user
        return {"types": responses[index]}

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    mined = g._mine_types_from_inventory_via_api(
        meta=g._metadata(subject="Mathematics"),
        inventory=inventory,
        max_coverage_attempts=2,
    )

    assert calls["n"] == 3
    assert not g._uncovered_inventory_items(inventory, mined["types"])
    assert not g._duplicate_inventory_assignments(inventory, mined["types"])


def test_mine_types_hard_fails_after_live_coverage_degradation(monkeypatch):
    topics = (
        ["Introduction"] * 2
        + ["Arithmetic Progressions"] * 7
        + ["nth Term of an AP"] * 31
        + ["Sum of First n Terms of an AP"] * 37
    )
    inventory = {"items": [
        {
            "qid": f"QINV-{index:04d}",
            "topic_hint": topic,
            "raw_task": f"Complete source task {index} with all stated conditions.",
        }
        for index, topic in enumerate(topics, start=1)
    ], "stats": {}}

    def mined_type(type_id, title, first, last):
        qids = [f"QINV-{index:04d}" for index in range(first, last + 1)]
        return {
            "type_id": type_id,
            "type_title": title,
            "source_question_ids": qids,
            "case_prompts": [{
                "case_title": f"Defined case for {title}",
                "examples": [
                    {
                        "source_question_id": qid,
                        "example_prompt": (
                            f"Complete source task {int(qid[-4:])} "
                            "with all stated conditions."
                        ),
                    }
                    for qid in qids
                ],
            }],
        }

    initial = [
        mined_type("TYPE-0001", "Initial pattern 1", 1, 2),
        mined_type("TYPE-0002", "Initial pattern 2", 3, 9),
        mined_type("TYPE-0003", "Initial pattern 3", 10, 24),
        mined_type("TYPE-0004", "Initial pattern 4", 25, 40),
        mined_type("TYPE-0005", "Initial pattern 5", 41, 74),
    ]
    catastrophic = [mined_type(
        "TYPE-0001", "Catastrophically truncated correction", 1, 2)]
    calls = {"n": 0}

    def fake_openai(system, user, **kwargs):
        calls["n"] += 1
        return {"types": initial if calls["n"] == 1 else catastrophic}

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    with pytest.raises(
        RuntimeError, match=r"3 unclassified.*0 duplicate",
    ):
        g._mine_types_from_inventory_via_api(
            meta=g._metadata(subject="Mathematics"),
            inventory=inventory,
            max_coverage_attempts=4,
        )

    assert calls["n"] == 5


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


def test_assign_mined_types_can_place_types_on_culminations(monkeypatch):
    captured = {}

    def fake_openai(system, user, **kw):
        captured["user"] = user
        return {"assignments": [
            {"concept_id": "CONCEPT-0001", "type_ids": ["TYPE-0001"]},
            {"concept_id": "CONCEPT-0002", "type_ids": ["TYPE-0002"]},
        ]}

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    records = [
        {"topic": "T", "parent_concept": "P", "concept_title": "Normal Concept",
         "concept_details": "Description: d", "keywords": ""},
        {"topic": "T", "parent_concept": "Culmination",
         "concept_title": "Culmination - Normal Concept",
         "concept_details": "Description: Recap", "keywords": ""},
    ]
    mined = {"types": [
        {"type_id": "TYPE-0001", "type_title": "Single Concept Pattern",
         "case_prompts": [{"case_prompt": "do it"}]},
        {"type_id": "TYPE-0002", "type_title": "Mixed Multi-Concept Pattern",
         "case_prompts": [{"case_prompt": "combine ideas"}]},
    ]}
    out = g._assign_mined_types_via_api(
        records, meta=g._metadata(subject="Math"), mined_types=mined)
    # Culmination rows are part of the assignment payload...
    assert '"is_culmination": true' in captured["user"]
    # ...and can receive mixed/synthesis Types.
    assert "Mixed Multi-Concept Pattern" in out[1]["concept_details"]
    assert "Single Concept Pattern" in out[0]["concept_details"]


def test_pipeline_builds_culminations_before_types(monkeypatch):
    monkeypatch.setattr(g.config, "use_live_generation", lambda: True)
    order: list[str] = []

    monkeypatch.setattr(g, "_extract_skeleton_via_api", lambda chunks, **kw: [
        {"topic": "T", "parent_concept": "P", "concept_title": "C",
         "concept_details": "Description: d", "keywords": ""},
    ])
    monkeypatch.setattr(g, "_consolidate_concepts_via_api", lambda records, **kw: records)
    monkeypatch.setattr(g, "_refine_descriptions_via_api", lambda records, **kw: records)
    monkeypatch.setattr(g, "_ensure_mastery_lines_via_api", lambda records, **kw: records)
    monkeypatch.setattr(
        g, "_extract_question_task_inventory_via_api", lambda **kw: g._empty_inventory())
    monkeypatch.setattr(
        g, "_mine_types_from_inventory_via_api", lambda **kw: {"types": []})

    def fake_culminations(records, **kw):
        order.append("culmination")
        return g._ensure_culmination_rows(records)

    def fake_types(records, **kw):
        order.append("types")
        # Culminations must already exist when the Types pass runs.
        assert any(
            r["concept_title"].startswith("Culmination -") for r in records)
        return records

    monkeypatch.setattr(g, "_build_culminations_via_api", fake_culminations)
    monkeypatch.setattr(g, "_assign_types_via_api", fake_types)
    monkeypatch.setattr(g, "_repair_records_via_api", lambda records, **kw: records)
    monkeypatch.setattr(
        g, "_validate_final_or_raise",
        lambda records, **kw: {"ok": True, "errors": [], "summary": {}})
    g.concepts_from_mmd("## T\nbody", subject="Mathematics")
    assert order == ["culmination", "types"]


def test_concepts_pipeline_runs_types_assign(monkeypatch):
    monkeypatch.setattr(g.config, "use_live_generation", lambda: True)
    calls = []

    def fake_openai(system, user, **kw):
        calls.append(system[:40])
        if "description-only" in system.lower():
            return {"rows": [{
                "topic": "Algebra", "concept": "Linear equations",
                "concept_description": (
                    "Description: Linear equations use inverse operations to isolate the variable "
                    "while preserving equality. This supports solving one-step and two-step forms "
                    "from the source material."
                ),
                "keywords": "linear",
            }]}
        if "Types-only" in system:
            return {"rows": [{
                "topic": "Algebra", "concept": "Linear equations",
                "concept_description": (
                    "Description: altered by model // "
                    "Types: Type 01: One-step Case 01: Solve x+2=5 by subtracting 2 from both sides. "
                    "Case 02: Solve x-3=1 by adding 3 to both sides. "
                    "Type 02: Two-step Case 01: Solve 2x+1=7 by undoing addition and multiplication. "
                    "Case 02: Solve 3x-2=4 by undoing subtraction and multiplication. "
                    "// Misconception: wrong inverse op"
                ),
                "keywords": "linear",
            }]}
        if "Build culmination" in system:
            return {"rows": []}
        return {"rows": [{
            "topic": "Algebra", "concept": "Linear equations",
            "concept_description": "Description: solve ax+b=c // Misconception: wrong inverse op",
            "keywords": "linear",
        }]}

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    records = g.concepts_from_mmd("## Algebra\nSolve linear equations.", subject="Mathematics")
    assert any("description-only" in c.lower() for c in calls)
    assert any("Types-only" in c for c in calls)
    assert "preserving equality" in records[0]["concept_details"]
    assert "altered by model" not in records[0]["concept_details"]
    assert g._has_meaningful_types(records[0]["concept_details"])
    assert sum(r["concept_title"].startswith("Culmination -") for r in records) == 1


def test_pre_learning_excludes_exact_current_concepts():
    current = [{
        "topic": "Algebra",
        "parent_concept": "Linear Equations",
        "concept_title": "Solving One-Step Equations",
        "concept_details": "Description: current chapter content",
        "keywords": "",
    }]
    pre = g.pre_learning_from_rows(
        current, subject="Mathematics", grade="07", board="CBSE",
        chapter_title="Linear Equations", live=False)
    titles = {r["concept_title"] for r in pre}
    assert "Solving One-Step Equations" not in titles
    assert all(r.get("parent_concept") for r in pre)

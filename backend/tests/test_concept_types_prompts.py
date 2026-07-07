"""Concept-generation prompts must require rich Types classification."""
from app.services import concept_map_v2 as cmv2
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
    assert "CASE PROMPTS CARRY THE FULL SOURCE QUESTION" in mining
    assert "Do not shorten source questions" in mining
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
         "case_prompts": [{"case_prompt": "Find 2+3"}]},
        {"type_id": "TYPE-0002", "type_title": "Dividing Powers with the Same Base",
         "case_prompts": [{"case_prompt": "Simplify p^9 ÷ p^3"}]},
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
         "case_prompts": [{"case_prompt": "do one"}]},
        {"type_id": "TYPE-0002", "type_title": "Pattern Two",
         "case_prompts": [{"case_prompt": "do two"}]},
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
        # Coverage retry must receive the missed item and classify it.
        assert "UNCLASSIFIED INVENTORY ITEMS" in user
        assert "QINV-0002" in user
        return {"types": [{
            "type_id": "TYPE-0002", "type_title": "Pattern Two",
            "source_question_ids": ["QINV-0002"],
            "case_prompts": [{"case_prompt": "do two", "source_question_id": "QINV-0002"}],
        }]}

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
        # The retry classifies the missed item into the EXISTING Type.
        return {"types": [{
            "type_id": "TYPE-0001", "type_title": "Pattern One",
            "source_question_ids": ["QINV-0002"],
            "case_prompts": [{"case_prompt": "do two", "source_question_id": "QINV-0002"}],
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
    """V2 runs inventory extraction then one master concept-map prompt."""
    monkeypatch.setattr(g.config, "use_live_generation", lambda: True)
    order: list[str] = []

    def fake_inventory(**kw):
        order.append("inventory")
        return g._empty_inventory()

    def fake_v2(cfg, chapter_text, inventory, **kw):
        order.append("v2_master")
        meta, rows = cmv2.build_output_rows(cfg, [
            cmv2.ConceptWithTypes(
                concept_id="CONCEPT-0001",
                topic=cfg.expected_topics[0],
                parent_concept="Core",
                concept_title="Sample Concept",
                description_body="A teachable idea from the source.",
                mastery="Applying the idea correctly.",
                keywords=["sample"],
                types=[],
            ),
        ])
        return meta, rows, []

    monkeypatch.setattr(g, "_extract_question_task_inventory_via_api", fake_inventory)
    monkeypatch.setattr(cmv2, "generate_concept_map_v2", fake_v2)
    monkeypatch.setattr(
        g, "_topic_headings",
        lambda sections: ["Similar Figures"])
    g.concepts_from_mmd(
        "## Similar Figures\nbody", subject="Mathematics", source_book="NCERT")
    assert order == ["inventory", "v2_master"]


def test_concepts_pipeline_runs_types_assign(monkeypatch):
    """Live post-learning uses Concept Map V2 master prompt output."""
    monkeypatch.setattr(g.config, "use_live_generation", lambda: True)

    def fake_v2(cfg, chapter_text, inventory, **kw):
        concept = cmv2.ConceptWithTypes(
            concept_id="CONCEPT-0001",
            topic="Algebra",
            parent_concept="Linear Equations",
            concept_title="Linear equations",
            description_body=(
                "Linear equations use inverse operations to isolate the variable "
                "while preserving equality."
            ),
            mastery="Solving one-step and two-step linear equations.",
            misconception="Students may apply the wrong inverse operation.",
            keywords=["linear", "equations"],
            types=[
                cmv2.MinedType(
                    type_id="TYPE-0001",
                    type_title="One-step Linear Equations",
                    source_question_ids=["QINV-0001"],
                    cases=[
                        cmv2.MinedCase(
                            case_id="CASE-0001",
                            source_question_id="QINV-0001",
                            case_prompt="Solve the equation x + 2 = 5 for x.",
                        ),
                    ],
                ),
            ],
        )
        meta, rows = cmv2.build_output_rows(cfg, [concept])
        return meta, rows, [concept]

    monkeypatch.setattr(cmv2, "generate_concept_map_v2", fake_v2)
    monkeypatch.setattr(
        g, "_extract_question_task_inventory_via_api",
        lambda **kw: {"items": [{"qid": "QINV-0001", "normalized_task": "x+2=5"}],
                      "stats": {}})
    monkeypatch.setattr(g, "_topic_headings", lambda sections: ["Algebra"])
    records = g.concepts_from_mmd("## Algebra\nSolve linear equations.", subject="Mathematics")
    assert "preserving equality" in records[0]["concept_details"]
    assert "Type 01: One-step Linear Equations" in records[0]["concept_details"]
    assert records[0]["topic"] == "Algebra"


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

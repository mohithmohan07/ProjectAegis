"""Concept-generation prompts must require rich Types classification."""
import json

import pytest

from app.services import generation as g


def _type_embedding_request(user: str) -> tuple[list[dict], list[dict]]:
    concepts_text, types_text = user.split(
        "\n\nMINED TYPE ASSIGNMENT UNITS "
        "(every type_id MUST be assigned):\n", 1)
    concepts = json.loads(concepts_text.rsplit("\n", 1)[-1])["concepts"]
    types = json.loads(types_text)["types"]
    return concepts, types


def test_concepts_system_requires_numeric_types_guidance():
    system = g._concepts_system("Mathematics")
    assert "Extract ONLY a clean teachable concept skeleton" in system
    assert "No Types" in system
    assert "no culmination rows" in system
    assert "parent_concept" in system
    # Numeric zero-padded labels (Type 01:/Case 01:), not descriptive labels.
    types_system = g.prompts.get_text("concepts.types_assign.system")
    assert "Type 01:" in types_system and "Case 01:" in types_system
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
    assert "Preserve Description exactly" in types
    # Types run after the culmination pass; culminations may receive mixed Types.
    assert "Culmination rows may receive Types" in types
    assert "Preserve valid fields, including parent_concept, Types" in repair


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
    assert "cdn.mathpix.com" in mining
    # Types must be properly defined (precise wording + definition).
    assert "TYPE WORDING" in mining
    assert "precise, self-explanatory pattern name" in mining
    assert "type_description must DEFINE the pattern" in mining
    # concept_match_hint is Type-level: Cases for distinct concept rows cannot
    # be hidden inside one broad formula-sharing Type.
    assert "same single granular concept" in mining_contract
    assert "concept_match_hint is Type-level" in mining_contract
    assert "direct formula calculations" in mining_contract
    assert "contextual/real-life modeling or applications" in mining_contract
    assert "incremental delta" in delta
    assert "never return an already classified question" in delta
    assert "complete source task" in delta
    assert "same granular concept as every existing Case" in delta_contract
    assert "concept_match_hint applies to the whole Type" in delta_contract
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


def test_case_scoped_embedding_splits_formula_and_real_life_cases(monkeypatch):
    calls = []

    def fake_openai(system, user, **kw):
        concepts, units = _type_embedding_request(user)
        calls.append((concepts, units))
        assert "case-scoped assignment unit" in system
        assert len(units) == 2
        direct = next(
            unit for unit in units
            if unit["case_prompts"][0]["case_id"] == "CASE-DIRECT"
        )
        real_life = next(
            unit for unit in units
            if unit["case_prompts"][0]["case_id"] == "CASE-REAL-LIFE"
        )
        assert direct["type_id"] == "TYPE-0001::CASE-DIRECT::0001"
        assert real_life["type_id"] == "TYPE-0001::CASE-REAL-LIFE::0002"
        assert direct["source_question_ids"] == ["QINV-0001"]
        assert real_life["source_question_ids"] == [
            "QINV-0002", "QINV-0003"]
        assert len(direct["case_prompts"]) == len(real_life["case_prompts"]) == 1
        assert all(
            unit["type_title"]
            == "Finding Terms and Indices Using the Nth Term of an AP"
            and unit["type_description"]
            == "Use AP term information to find a term or index."
            and unit["topic_match_hint"] == "nth Term of an AP"
            and unit["is_activity"] is False
            for unit in units
        )
        formula_cid = next(
            row["concept_id"] for row in concepts
            if row["concept"] == "Derive and Apply the Nth-term Formula"
        )
        real_life_cid = next(
            row["concept_id"] for row in concepts
            if row["concept"] == "Model Real Situations with Arithmetic Progressions"
        )
        return {"assignments": [
            {"concept_id": formula_cid, "type_ids": [direct["type_id"]]},
            {"concept_id": real_life_cid, "type_ids": [real_life["type_id"]]},
        ]}

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    records = [
        {"topic": "nth Term of an AP", "parent_concept": "Formula",
         "concept_title": "Derive and Apply the Nth-term Formula",
         "concept_details": "Description: formula", "keywords": ""},
        {"topic": "nth Term of an AP", "parent_concept": "Applications",
         "concept_title": "Model Real Situations with Arithmetic Progressions",
         "concept_details": "Description: applications", "keywords": ""},
        {"topic": "nth Term of an AP", "parent_concept": "Culmination",
         "concept_title": "Culmination - Nth-term Formula and Applications",
         "concept_details": "Description: Recap", "keywords": ""},
    ]
    direct_example = "Find the 20th term of the AP 3, 7, 11, ..."
    salary_example = (
        "A salary starts at ₹8000 and increases by ₹500 yearly. "
        "Find the salary in the fifth year."
    )
    flower_example = (
        "A flower bed has 23 roses in the first row, then 21, 19, and so on, "
        "with 5 in the last row. Find the number of rows."
    )
    mined = {"types": [{
        "type_id": "TYPE-0001",
        "type_title": "Finding Terms and Indices Using the Nth Term of an AP",
        "type_description": "Use AP term information to find a term or index.",
        "topic_match_hint": "nth Term of an AP",
        "source_question_ids": ["QINV-0001", "QINV-0002", "QINV-0003"],
        "case_prompts": [
            {
                "case_id": "CASE-DIRECT",
                "case_title": "Find a specified term from a numerical AP",
                "examples": [{
                    "source_question_id": "QINV-0001",
                    "example_prompt": direct_example,
                }],
            },
            {
                "case_id": "CASE-REAL-LIFE",
                "case_title": "Model a salary or flower-row pattern as an AP",
                "examples": [
                    {"source_question_id": "QINV-0002",
                     "example_prompt": salary_example},
                    {"source_question_id": "QINV-0003",
                     "example_prompt": flower_example},
                ],
            },
        ],
        "is_activity": False,
    }]}

    out = g._assign_mined_types_via_api(
        records, meta=g._metadata(subject="Mathematics"), mined_types=mined)

    assert len(calls) == 1
    formula_details = out[0]["concept_details"]
    real_life_details = out[1]["concept_details"]
    assert direct_example in formula_details
    assert salary_example not in formula_details
    assert flower_example not in formula_details
    assert salary_example in real_life_details
    assert flower_example in real_life_details
    assert direct_example not in real_life_details
    assert "Types:" not in out[2]["concept_details"]
    assert not g._mined_type_topic_violations(out, mined)


def test_case_scoped_activity_units_keep_flag_and_reach_culmination(monkeypatch):
    def fake_openai(system, user, **kw):
        concepts, units = _type_embedding_request(user)
        assert len(units) == 2
        assert all(unit["is_activity"] is True for unit in units)
        culmination = next(row for row in concepts if row["is_culmination"])
        return {"assignments": [{
            "concept_id": culmination["concept_id"],
            "type_ids": [unit["type_id"] for unit in units],
        }]}

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    records = [
        {"topic": "Electricity", "parent_concept": "Circuits",
         "concept_title": "Measure Current in a Circuit",
         "concept_details": "Description: current", "keywords": ""},
        {"topic": "Electricity", "parent_concept": "Culmination",
         "concept_title": "Culmination - Electric Circuits",
         "concept_details": "Description: Recap", "keywords": ""},
    ]
    mined = {"types": [{
        "type_id": "TYPE-ACTIVITY",
        "type_title": "Investigating Electric Circuits",
        "topic_match_hint": "Electricity",
        "source_question_ids": ["QINV-0001", "QINV-0002"],
        "case_prompts": [
            {"case_id": "CASE-CURRENT", "case_title": "Observe current",
             "examples": [{"source_question_id": "QINV-0001",
                           "example_prompt": "Measure current as cells are added."}]},
            {"case_id": "CASE-VOLTAGE", "case_title": "Observe voltage",
             "examples": [{"source_question_id": "QINV-0002",
                           "example_prompt": "Measure voltage across the wire."}]},
        ],
        "is_activity": True,
    }]}

    out = g._assign_mined_types_via_api(
        records, meta=g._metadata(subject="Physics"), mined_types=mined)

    assert "Types:" not in out[0]["concept_details"]
    assert "Measure current as cells are added." in out[1]["concept_details"]
    assert "Measure voltage across the wire." in out[1]["concept_details"]


def test_single_case_embedding_keeps_original_type_id(monkeypatch):
    def fake_openai(system, user, **kw):
        _concepts, units = _type_embedding_request(user)
        assert len(units) == 1
        assert units[0]["type_id"] == "TYPE-0001"
        assert units[0]["source_question_ids"] == [
            "QINV-0001", "QINV-0002"]
        assert len(units[0]["case_prompts"]) == 1
        return {"assignments": [{
            "concept_id": "CONCEPT-0001",
            "type_ids": ["TYPE-0001"],
        }]}

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    records = [{
        "topic": "T", "parent_concept": "P", "concept_title": "Formula",
        "concept_details": "Description: formula", "keywords": "",
    }]
    mined = {"types": [{
        "type_id": "TYPE-0001",
        "type_title": "Apply One Formula",
        "source_question_ids": ["QINV-0001", "QINV-0002"],
        "case_prompts": [{
            "case_id": "CASE-0001",
            "case_title": "Apply the formula to supplied values",
            "examples": [
                {"source_question_id": "QINV-0001",
                 "example_prompt": "Apply the formula to the first values."},
                {"source_question_id": "QINV-0002",
                 "example_prompt": "Apply the formula to the second values."},
            ],
        }],
    }]}

    out = g._assign_mined_types_via_api(
        records, meta=g._metadata(subject="Math"), mined_types=mined)

    assert "Apply the formula to the first values." in out[0]["concept_details"]
    assert "Apply the formula to the second values." in out[0]["concept_details"]


def test_case_scoped_embedding_hard_fails_on_qid_duplication_or_loss(monkeypatch):
    calls = {"count": 0}

    def fake_openai(*args, **kwargs):
        calls["count"] += 1
        return {"assignments": []}

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    records = [{
        "topic": "T", "parent_concept": "P", "concept_title": "Formula",
        "concept_details": "Description: formula", "keywords": "",
    }]
    mined = {"types": [{
        "type_id": "TYPE-0001",
        "type_title": "Malformed Multi-case Type",
        "source_question_ids": ["QINV-0001", "QINV-0002"],
        "case_prompts": [
            {"case_id": "CASE-0001", "case_title": "First",
             "examples": [{"source_question_id": "QINV-0001",
                           "example_prompt": "First question."}]},
            {"case_id": "CASE-0002", "case_title": "Second",
             "examples": [{"source_question_id": "QINV-0001",
                           "example_prompt": "Duplicated first question."}]},
        ],
    }]}

    with pytest.raises(
        RuntimeError, match=r"assignment-unit qid invariant.*QINV-000[12]",
    ):
        g._assign_mined_types_via_api(
            records, meta=g._metadata(subject="Math"), mined_types=mined)

    assert calls["count"] == 0


def test_scoped_type_embedding_groups_topics_and_excludes_other_concepts(monkeypatch):
    calls = []

    def fake_openai(system, user, **kw):
        concepts, types = _type_embedding_request(user)
        calls.append((concepts, types))
        assert len({row["topic"] for row in concepts}) == 1
        allowed = {row["concept_id"] for row in concepts}
        assert all(set(item["allowed_concept_ids"]) == allowed for item in types)
        assert any(row["is_culmination"] for row in concepts)
        target = next(row for row in concepts if not row["is_culmination"])
        return {"assignments": [{
            "concept_id": target["concept_id"],
            "type_ids": [item["type_id"] for item in types],
        }]}

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    records = [
        {"topic": "Topic A", "parent_concept": "P", "concept_title": "Alpha",
         "concept_details": "Description: alpha", "keywords": ""},
        {"topic": "Topic A", "parent_concept": "Culmination",
         "concept_title": "Culmination - Alpha",
         "concept_details": "Description: recap alpha", "keywords": ""},
        {"topic": "Topic B", "parent_concept": "P", "concept_title": "Beta",
         "concept_details": "Description: beta", "keywords": ""},
        {"topic": "Topic B", "parent_concept": "Culmination",
         "concept_title": "Culmination - Beta",
         "concept_details": "Description: recap beta", "keywords": ""},
    ]
    mined = {"types": [
        {"type_id": "TYPE-0001", "type_title": "Alpha Pattern",
         "topic_match_hint": "Topic A",
         "case_prompts": [{"case_prompt": "Apply alpha."}]},
        {"type_id": "TYPE-0002", "type_title": "Beta Pattern",
         "topic_match_hint": "Topic B",
         "case_prompts": [{"case_prompt": "Apply beta."}]},
    ]}

    out = g._assign_mined_types_via_api(
        records, meta=g._metadata(subject="Math"), mined_types=mined)

    assert len(calls) == 2
    payload_by_topic = {concepts[0]["topic"]: concepts for concepts, _ in calls}
    assert {row["concept_id"] for row in payload_by_topic["Topic A"]} == {
        "CONCEPT-0001", "CONCEPT-0002"}
    assert {row["concept_id"] for row in payload_by_topic["Topic B"]} == {
        "CONCEPT-0003", "CONCEPT-0004"}
    assert "Alpha Pattern" in out[0]["concept_details"]
    assert "Beta Pattern" in out[2]["concept_details"]


def test_scoped_type_embedding_retries_with_same_candidates_and_lands_ids_once(
    monkeypatch,
):
    calls = []

    def fake_openai(system, user, **kw):
        concepts, types = _type_embedding_request(user)
        calls.append((concepts, types))
        if len(calls) == 1:
            return {"assignments": [
                {"concept_id": "CONCEPT-0001",
                 "type_ids": ["TYPE-0001", "TYPE-0001"]},
                {"concept_id": "CONCEPT-0002", "type_ids": ["TYPE-0001"]},
            ]}
        return {"assignments": [{
            "concept_id": "CONCEPT-0002", "type_ids": ["TYPE-0002"],
        }]}

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    records = [
        {"topic": "Topic A", "parent_concept": "P", "concept_title": "Alpha",
         "concept_details": "Description: alpha", "keywords": ""},
        {"topic": "Topic A", "parent_concept": "P", "concept_title": "Alpha Two",
         "concept_details": "Description: alpha two", "keywords": ""},
        {"topic": "Topic B", "parent_concept": "P", "concept_title": "Beta",
         "concept_details": "Description: beta", "keywords": ""},
    ]
    mined = {"types": [
        {"type_id": "TYPE-0001", "type_title": "First Alpha Pattern",
         "topic_match_hint": "Topic A",
         "case_prompts": [{"case_prompt": "Apply first alpha."}]},
        {"type_id": "TYPE-0002", "type_title": "Second Alpha Pattern",
         "topic_match_hint": "Topic A",
         "case_prompts": [{"case_prompt": "Apply second alpha."}]},
    ]}

    out = g._assign_mined_types_via_api(
        records, meta=g._metadata(subject="Math"), mined_types=mined,
        max_attempts=2)

    assert len(calls) == 2
    assert [
        {row["concept_id"] for row in concepts} for concepts, _ in calls
    ] == [{"CONCEPT-0001", "CONCEPT-0002"}] * 2
    assert [[item["type_id"] for item in types] for _, types in calls] == [
        ["TYPE-0001", "TYPE-0002"], ["TYPE-0002"]]
    details = " ".join(row["concept_details"] for row in out)
    assert details.count("First Alpha Pattern") == 1
    assert details.count("Second Alpha Pattern") == 1


def test_scoped_type_embedding_empty_candidates_fail_before_api(monkeypatch):
    calls = {"count": 0}

    def fake_openai(system, user, **kw):
        calls["count"] += 1
        return {"assignments": []}

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    records = [
        {"topic": "Topic A", "parent_concept": "P", "concept_title": "Alpha",
         "concept_details": "Description: alpha", "keywords": ""},
    ]
    mined = {"types": [{
        "type_id": "TYPE-0008", "type_title": "Missing Topic Pattern",
        "topic_match_hint": "Unmatched $ n $ Topic",
        "case_prompts": [{"case_prompt": "Apply the missing topic."}],
    }]}

    with pytest.raises(
        RuntimeError,
        match=r"TYPE-0008.*Unmatched \$ n \$ Topic.*normaliz",
    ):
        g._assign_mined_types_via_api(
            records, meta=g._metadata(subject="Math"), mined_types=mined)

    assert calls["count"] == 0


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
        ("QINV-0002", authoritative_task_two),
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


def test_single_item_fallback_preserves_source_image_topic_and_embeds(monkeypatch):
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
        "example_prompt": f"{source_task} ![]({image_url})",
    }
    assert "The length is 8 cm." not in example["example_prompt"]

    records = [
        {
            "topic": "Triangles",
            "parent_concept": "Triangles",
            "concept_title": "Triangle Properties",
            "concept_details": "Description: Triangle properties.",
            "keywords": "",
        },
        {
            "topic": "Geometric Constructions",
            "parent_concept": "Constructions",
            "concept_title": "Constructing Similar Triangles",
            "concept_details": "Description: Construct similar triangles.",
            "keywords": "",
        },
    ]

    def fake_openai(system, user, **kwargs):
        concepts, types = _type_embedding_request(user)
        assert [concept["concept_id"] for concept in concepts] == ["CONCEPT-0002"]
        assert types[0]["topic_match_hint"] == "Geometric Constructions"
        assert types[0]["type_title"] == fallback["type_title"]
        return {
            "assignments": [{
                "concept_id": "CONCEPT-0002",
                "type_ids": [fallback["type_id"]],
            }],
        }

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    embedded = g._assign_mined_types_via_api(
        records,
        meta=g._metadata(subject="Mathematics"),
        mined_types={"types": normalized},
    )

    assert fallback["type_title"] not in embedded[0]["concept_details"]
    assert fallback["type_title"] in embedded[1]["concept_details"]
    assert source_task in embedded[1]["concept_details"]
    assert image_url in embedded[1]["concept_details"]


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

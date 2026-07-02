"""Concept-generation prompts must require rich Types classification."""
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
    assert "Merge duplicates" in g.prompts.get_text("concepts.canonicalize.system")
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
    assert "Culmination rows are not handled here" in types
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


def test_assign_types_via_api(monkeypatch):
    captured = {}

    def fake_openai(system, user, **kw):
        captured["system"] = system
        captured["user"] = user
        return {"rows": [{
            "topic": "T", "concept": "C",
            "concept_description": (
                "Description: d // Types: Type 01: Evaluation Case 01: Find 2+3 "
                "Case 02: Find 5×2 // Misconception: m"
            ),
            "keywords": "k",
        }]}

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    records = [{"topic": "T", "concept_title": "C",
                "concept_details": "Description: d // Misconception: m", "keywords": ""}]
    inventory = {"items": [{"qid": "QINV-0001", "normalized_task": "Find 2+3", "content_objects": {"numbers": ["2", "3"]}}]}
    mined = {"types": [{"type_id": "TYPE-0001", "type_title": "Adding Given Numbers", "source_question_ids": ["QINV-0001"]}]}
    out = g._assign_types_via_api(
        records,
        subject="Math",
        mmd_text="# Chapter\nEx 1.1",
        question_task_inventory=inventory,
        mined_types=mined,
    )
    assert "Types-only classifier" in captured["system"]
    assert "QUESTION / TASK INVENTORY" in captured["user"]
    assert "MINED REUSABLE TYPES" in captured["user"]
    assert "QINV-0001" in captured["user"]
    assert g._has_meaningful_types(out[0]["concept_details"])


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
                    "Types: Type 01: One-step Case 01: Solve x+2=5 Case 02: Solve x-3=1 "
                    "Type 02: Two-step Case 01: Solve 2x+1=7 Case 02: Solve 3x-2=4 "
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

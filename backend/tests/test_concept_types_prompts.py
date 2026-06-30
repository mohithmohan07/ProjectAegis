"""Concept-generation prompts must require rich Types classification."""
from app.services import generation as g


def test_concepts_system_requires_numeric_types_guidance():
    system = g._concepts_system("Mathematics")
    assert "Then include Types ONLY IF" in system
    assert "Types classify EVERY distinct question" in system
    # Numeric zero-padded labels (Type 01:/Case 01:), not descriptive labels.
    assert "Type 01:" in system and "Case 01:" in system
    assert "Type 01: Evaluating numerical exponential expressions" in system
    assert "Misconception ONLY IF" in system
    assert "Misconception is REQUIRED" not in system
    assert "description-only editor" in g.prompts.get_text("concepts.description_refine")
    assert "preserve and enrich, never strip" in g.prompts.get_text("concepts.consolidate")
    assert "Types-only classifier" in g.prompts.get_text("concepts.types_assign")


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


def test_consolidate_accepts_mmd_for_types(monkeypatch):
    captured = {}

    def fake_openai(system, user, **kw):
        captured["user"] = user
        return {"rows": [{
            "topic": "T", "concept": "C",
            "concept_description": (
                "Description: d // Types: Direct evaluation — Case: Find x "
                "// Misconception: m"
            ),
            "keywords": "k",
        }]}

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    records = [{"topic": "T", "concept_title": "C", "concept_details": "Description: d // Misconception: m", "keywords": ""}]
    g._consolidate_concepts_via_api(records, subject="Math", mmd_text="# Chapter\nExercise problems here.")
    assert "CHAPTER SOURCE" in captured["user"]
    assert "Exercise problems here" in captured["user"]


def test_refine_descriptions_via_api_preserves_existing_types(monkeypatch):
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
    assert "CHAPTER SOURCE" in captured["user"]
    assert "clear source-grounded description" in out[0]["concept_details"]
    # The description pass may not drop a pre-existing Types section.
    assert "Types: Type 01: Evaluation Case 01: Find x" in out[0]["concept_details"]


def test_assign_types_via_api(monkeypatch):
    captured = {}

    def fake_openai(system, user, **kw):
        captured["system"] = system
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
    out = g._assign_types_via_api(records, subject="Math", mmd_text="# Chapter\nEx 1.1")
    assert "Types-only classifier" in captured["system"]
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
                    "Description: solve ax+b=c // "
                    "Types: Type 01: One-step Case 01: Solve x+2=5 Case 02: Solve x-3=1 "
                    "Type 02: Two-step Case 01: Solve 2x+1=7 Case 02: Solve 3x-2=4 "
                    "// Misconception: wrong inverse op"
                ),
                "keywords": "linear",
            }]}
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
    assert g._has_meaningful_types(records[0]["concept_details"])

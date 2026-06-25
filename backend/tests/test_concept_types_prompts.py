"""Concept-generation prompts must require rich Types classification."""
from app.services import generation as g


def test_concepts_system_requires_numeric_types_guidance():
    system = g._concepts_system("Mathematics")
    assert "Types are REQUIRED" in system
    assert "Types classify EVERY distinct question" in system
    # Numeric zero-padded labels (Type 01:/Case 01:), not descriptive labels.
    assert "Type 01:" in system and "Case 01:" in system
    assert "Type 01: Evaluating numerical exponential expressions" in system
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
    assert any("Types-only" in c for c in calls)
    assert g._has_meaningful_types(records[0]["concept_details"])

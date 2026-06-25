"""Concept-generation prompts must require rich Types classification."""
from app.services import generation as g


def test_concepts_system_requires_types_guidance():
    system = g._concepts_system("Mathematics")
    assert "Types are REQUIRED" in system
    assert "Types classify EVERY distinct question" in system
    assert "Evaluating numerical exponential expressions" in system
    assert "preserve and enrich, never strip" in g.prompts.get_text("concepts.consolidate")


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

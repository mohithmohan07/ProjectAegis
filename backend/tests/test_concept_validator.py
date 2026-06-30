from app.services import concept_validator as cv
from app.services import generation as g


def _rec(title, details="Description: clear teachable description here", topic="T", parent="P"):
    return {
        "topic": topic,
        "parent_concept": parent,
        "concept_title": title,
        "concept_details": details,
        "keywords": "k",
    }


def test_validator_detects_repeated_sibling_openers():
    report = cv.validate_concept_rows([
        _rec("Structure and Function of X"),
        _rec("Structure and Function of Y"),
    ])
    assert any(e["code"] == "repeated_sibling_opener" for e in report["errors"])


def test_repair_loop_merges_repaired_rows(monkeypatch):
    records = [
        _rec("Structure and Function of X"),
        _rec("Structure and Function of Y"),
    ]

    def fake_openai(system, user, **kw):
        return {"rows": [
            {"topic": "T", "parent_concept": "P", "concept": "X Structure",
             "concept_description": "Description: clear teachable description here",
             "keywords": "k"},
            {"topic": "T", "parent_concept": "P", "concept": "Y Function",
             "concept_description": "Description: clear teachable description here",
             "keywords": "k"},
        ]}

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    out = g._repair_records_via_api(records, meta=g._metadata(subject="Science"), stage="final")
    assert {r["concept_title"] for r in out} == {"X Structure", "Y Function"}


def test_validator_rejects_source_artifacts_and_bad_names():
    report = cv.validate_concept_rows([
        _rec("Basic Concepts"),
        _rec("Useful Concept", "Description: See Example 19, Fig 2, Ex 1.1 and page 9 for details"),
    ])
    codes = {e["code"] for e in report["errors"]}
    assert "forbidden_name" in codes
    assert "source_artifact" in codes


def test_validator_rejects_empty_sections_and_bad_types():
    report = cv.validate_concept_rows([
        _rec("Empty Types", "Description: useful enough description // Types:"),
        _rec("Empty Misconception", "Description: useful enough description // Misconception:"),
        _rec("Case Without Type", "Description: useful enough description // Types: Case 01: Solve x"),
        _rec("Type Without Case", "Description: useful enough description // Types: Type 01: Solve"),
    ])
    codes = {e["code"] for e in report["errors"]}
    assert {"empty_types", "empty_misconception", "case_without_type", "type_without_case"} <= codes


def test_validator_rejects_culmination_before_culmination_pass():
    report = cv.validate_concept_rows([
        _rec("Culmination - Early", "Description: Recap // Types: Type 01: Mix Case 01: combine", parent="Culmination"),
    ], require_culmination=False, allow_culmination=False)
    assert any(e["code"] == "culmination_too_early" for e in report["errors"])


def test_validator_requires_one_culmination_last_per_topic():
    report = cv.validate_concept_rows([
        _rec("Skill A"),
        _rec("Culmination - Skill A", "Description: Recap // Types: Type 01: Mix Case 01: combine", parent="Culmination"),
    ], require_culmination=True)
    assert report["ok"]

    bad = cv.validate_concept_rows([
        _rec("Culmination - Skill A", "Description: Recap // Types: Type 01: Mix Case 01: combine", parent="Culmination"),
        _rec("Skill A"),
    ], require_culmination=True)
    assert any(e["code"] == "culmination_order" for e in bad["errors"])

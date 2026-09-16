"""Structural formatting must not rewrite inventory-owned learner questions."""
import copy

import pytest

from app.services import generation as g
from app.services import concept_refiner as cr


@pytest.mark.parametrize("prompt", [
    "Write two sentences following this pattern. Example: She sings softly.",
    "Compare Type 12: direct use with Case 03: boundary reasoning.",
])
def test_final_placement_preserves_literal_structural_words_in_source(prompt):
    inventory = {"items": [{"qid": "QINV-ENGLISH", "raw_task": prompt}]}
    records = [{"topic": "Grammar", "concept_title": "Sentence Patterns",
                "_aegis_release_qids": ["QINV-ENGLISH"],
                "concept_details": "Description: Study sentence patterns. // Types: "
                "Type 09: Use a pattern Case 07: Compose sentences Example 04: " + prompt}]
    assert g._rendered_inventory_coverage_defects(records, inventory) == {"missing": [], "duplicate": []}
    result = g._enforce_rendered_inventory_coverage(copy.deepcopy(records), inventory)
    assert prompt in result[0]["concept_details"]
    assert "Type 01: Use a pattern Case 01: Compose sentences Example 01: " in result[0]["concept_details"]
    assert result[0]["_aegis_release_qids"] == ["QINV-ENGLISH"]
    assert g._rendered_inventory_coverage_defects(result, inventory) == {"missing": [], "duplicate": []}
    assert g._enforce_rendered_inventory_coverage(copy.deepcopy(result), inventory) == result


def test_moving_a_legacy_activity_keeps_other_source_examples_and_their_owners():
    quoted = "Write a short dialogue. Example: Ravi said, 'Good morning.'"
    activity = "Discuss the speaker's choice of words."
    inventory = {"items": [
        {"qid": "Q-QUOTE", "raw_task": quoted, "topic_hint": "Grammar"},
        {"qid": "Q-ACTIVITY", "raw_task": activity, "topic_hint": "Grammar", "_activity_origin": True},
    ]}
    rows = [
        {"topic": "Grammar", "concept_title": "Dialogue", "_aegis_release_qids": ["Q-QUOTE"],
         "concept_details": "Description: Dialogue. // Types: Type 01: Write dialogue "
         "Case 01: Use an example Example 01: " + quoted + " Case 02: Discuss words Example 01: " + activity},
        {"topic": "Grammar", "concept_title": "Word Choice", "_activity_hub_qids": ["Q-ACTIVITY"],
         "concept_details": "Description: Discuss words. // Activity/Info Hub: Activity: " + activity},
    ]
    result = g._enforce_rendered_inventory_coverage(copy.deepcopy(rows), inventory)
    assert g._rendered_inventory_coverage_defects(result, inventory) == {"missing": [], "duplicate": []}
    assert g._rendered_inventory_example_locations(result, inventory["items"][0]) == [0]
    assert g._rendered_inventory_example_locations(result, inventory["items"][1]) == [1]
    assert quoted in result[0]["concept_details"]
    assert result[0]["_aegis_release_qids"] == ["Q-QUOTE"]
    assert g._enforce_rendered_inventory_coverage(copy.deepcopy(result), inventory) == result


def test_cosmetic_quote_variants_keep_the_actual_rendered_bytes_and_type_identity():
    source = "Explain Case 12: the case in which words differ . . . For Example: she sings."
    rendered = "Explain Case 12: the case in which words differ... For Example: she sings."
    row = {"topic": "Grammar", "concept_title": "Rules", "_origin_type_id": "PAID-TYPE-ID",
           "concept_details": "Description: Rules. // Types: Type 42: Applying a rule "
           "Case 09: Interpret the source Example 15: " + rendered}
    result = cr.renumber_types_continuously([row], source_examples=[source], source_key=g._inventory_coverage_key)
    assert rendered in result[0]["concept_details"]
    assert "Type 01: Applying a rule Case 01: Interpret the source Example 01:" in result[0]["concept_details"]
    assert "AEGISSOURCEQUOTE" not in str(result)
    assert cr.renumber_types_continuously(copy.deepcopy(result), source_examples=[source], source_key=g._inventory_coverage_key) == result


def test_final_placement_still_fails_closed_and_names_the_exact_missing_identity(monkeypatch):
    prompt = "Write a dialogue between two friends."
    item = {"qid": "Q-LOST", "raw_task": prompt, "source_kind": "exercise", "source_label": "Exercise 4"}
    rows = [{"topic": "Dialogue", "concept_title": "Conversations", "_aegis_release_qids": ["Q-LOST"],
             "concept_details": "Description: Conversations. // Types: Type 01: Dialogue Case 01: Friends Example 01: " + prompt}]
    monkeypatch.setattr(g, "_align_activity_examples_with_hubs", lambda records, inventory: [
        {**records[0], "concept_details": "Description: Conversations."}])
    with pytest.raises(g.RenderedInventoryCoverageError, match="after final placement.*missing QIDs: Q-LOST") as caught:
        g._enforce_rendered_inventory_coverage(rows, {"items": [item]})
    diagnostic = caught.value.coverage_diagnostics
    assert diagnostic["stage"] == "final_placement"
    assert diagnostic["missing_qids"] == ["Q-LOST"]
    assert diagnostic["source_identities"][0]["before_placement_rows"] == [0]
    assert diagnostic["source_identities"][0]["rendered_rows"] == []
    assert diagnostic["source_identities"][0]["recorded_owner_rows"] == [0]
    assert prompt not in str(diagnostic)


def test_deposit_pipeline_round_trip_preserves_the_owned_source_question():
    from app.services import release_refiner

    prompt = "Write two sentences. Example: She sings softly."
    inventory = {"items": [{"qid": "Q-ROUNDTRIP", "raw_task": prompt}]}
    rows = [{"topic": "Grammar", "parent_concept": "Sentences", "concept_title": "Sentence Patterns",
             "_aegis_release_qids": ["Q-ROUNDTRIP"],
             "concept_details": "Description: Compose complete sentences.\nAchieving Mastery: Apply the sentence pattern."
             " // Types: Type 01: Use a pattern Case 01: Compose sentences Example 01: " + prompt,
             "keywords": "sentence"}]
    metadata = {"subject": "English", "inventory": inventory}
    result = release_refiner._deposit_deterministic_pipeline(copy.deepcopy(rows), metadata)
    assert prompt in result[0]["concept_details"]
    assert g._rendered_inventory_coverage_defects(result, inventory) == {"missing": [], "duplicate": []}
    assert release_refiner._deposit_deterministic_pipeline(copy.deepcopy(result), metadata) == result

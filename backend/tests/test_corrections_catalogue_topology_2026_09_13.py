"""The reviewer's corrections catalogue, second mechanical batch (Q67).

Topology, hub rendering and the assessment-cell lane: each test pins one
defect the reviewers corrected by hand, traced to its mechanism.
"""
from __future__ import annotations

import inspect
import json

from app.services import assessment_cells, generation, source_topic_policy
from app.services import canonical_source_phase3 as csp
from app.services.phase3 import prequestions, prompts


# --------------------------------------------------------------------------- #
# The language plan's order is the topic order
# --------------------------------------------------------------------------- #

def test_the_detailed_analysis_topic_stays_last_even_when_it_cites_the_opening_block():
    plan = {"topics": [
        {"plan_topic_id": "PT-1", "display_name": "Bholi's Childhood", "evidence_block_ids": ["B2"]},
        {"plan_topic_id": "PT-2", "display_name": "The Wedding Bargain", "evidence_block_ids": ["B3"]},
        {"plan_topic_id": "PT-3", "display_name": "Detailed Analysis of 'Bholi'",
         "evidence_block_ids": ["B1", "B3"]},
    ]}
    canonical = {"blocks": [
        {"block_id": "B1", "source_start": 0, "source_end": 40, "section_id": "S0"},
        {"block_id": "B2", "source_start": 100, "source_end": 300, "section_id": "S1"},
        {"block_id": "B3", "source_start": 400, "source_end": 600, "section_id": "S2"},
    ]}
    rows = csp._language_plan_topics({"language_topology_plan": json.dumps(plan)}, canonical)
    assert [(row["topic_id"], row["title"]) for row in rows] == [
        ("TOPIC-0001", "Bholi's Childhood"),
        ("TOPIC-0002", "The Wedding Bargain"),
        ("TOPIC-0003", "Detailed Analysis of 'Bholi'"),
    ]
    # The spans are still the plan's evidence, untouched by the ordering.
    assert rows[2]["source_start"] == 0 and rows[2]["source_end"] == 600


# --------------------------------------------------------------------------- #
# A lettered enumerator is apparatus, not the title
# --------------------------------------------------------------------------- #

def test_a_lettered_enumerator_printed_with_the_section_number_is_not_the_title():
    match = csp._NUMBER_PREFIX_RE.match("7.3.3 (a) Male Reproductive System")
    assert match.group("number") == "7.3.3"
    assert match.group("title") == "Male Reproductive System"
    match = csp._NUMBER_PREFIX_RE.match("7.3 Asexual Reproduction")
    assert (match.group("number"), match.group("title")) == ("7.3", "Asexual Reproduction")
    assert "lettered/roman enumerator" in source_topic_policy.HIERARCHY_TOPIC_INSTRUCTION
    assert "Title Case" in source_topic_policy.HIERARCHY_TOPIC_INSTRUCTION
    # The rule the instruction carried before is still there.
    assert "preserve a meaningful source heading verbatim" in source_topic_policy.HIERARCHY_TOPIC_INSTRUCTION


# --------------------------------------------------------------------------- #
# Culmination names are synthesis names
# --------------------------------------------------------------------------- #

def test_the_culmination_prompts_ask_for_a_synthesis_name_not_a_member_list():
    for key in ("concepts.culmination.system", "concepts.system"):
        text = generation.prompts.get_text(key)
        assert "Culmination - <A>, <B> and <C>" not in text, key
        assert "Culmination - <what" in text, key
        assert 'exact prefix' in text or 'prefix "Culmination - "' in text, key
    culmination = generation.prompts.get_text("concepts.culmination.system")
    assert "never leak a concept from an earlier/later topic" in culmination
    assert "one concept has nothing to consolidate" in culmination   # owner decision D8 stands


# --------------------------------------------------------------------------- #
# Activity/Info Hub rendering
# --------------------------------------------------------------------------- #

def test_a_bare_number_label_renders_as_an_activity_label():
    assert generation._activity_hub_marker({"source_label": "7.1", "raw_task": "Take a beaker of water."}) == "Activity 7.1"
    assert generation._activity_hub_marker({"source_label": "Activity 7.1", "raw_task": "Take a beaker."}) == "Activity 7.1"
    note = generation._compact_activity_hub_note({
        "source_label": "7.1", "raw_task": "Take a beaker of water and add sugar.", "source_kind": "activity",
    })
    assert note.startswith("Activity — Activity 7.1: Take a beaker")


def test_a_hub_note_is_punctuated_before_its_trailing_image_never_after():
    note = generation._compact_activity_hub_note({
        "source_label": "Activity 7.7",
        "raw_task": "Soak a few seeds and observe them ![Figure 7.9 Germination](https://assets.example.org/g.png) .",
        "image_urls": ["https://assets.example.org/g.png"],
        "source_kind": "activity",
    })
    assert "[img" in note
    assert " . " not in note and ". ." not in note
    assert not note.endswith("].")
    body = note.split("[img", 1)[0].rstrip()
    assert body.endswith("observe them.")


def test_public_task_text_closes_the_sentence_an_inline_image_interrupted():
    text = generation._inventory_task_text({
        "raw_task": "Observe the seeds carefully . Then record what you see.",
        "source_kind": "activity",
    })
    assert text.startswith("Observe the seeds carefully. Then record")
    kept = generation._inventory_task_text({"raw_task": "Wait ... then record.", "source_kind": "activity"})
    assert "..." in kept


# --------------------------------------------------------------------------- #
# The assessment-cell lane
# --------------------------------------------------------------------------- #

def test_the_cell_prompts_say_what_the_subjective_lane_means():
    for text in (assessment_cells.CELL_SYSTEM, assessment_cells.GENERATED_CELL_SYSTEM):
        assert "LANE MECHANICS" in text
        assert "catalogue availability, not evidence" in text
        # The rule they carried before is still there.
        assert "never abbreviate or paraphrase it" in text
    for text in (assessment_cells.CELL_CRITIC_SYSTEM, assessment_cells.GENERATED_CELL_CRITIC_SYSTEM):
        assert "constructed sentence rather than a slot value" in text
    assert assessment_cells.CELL_POLICY_VERSION.endswith("2026-09-13")
    assert assessment_cells.GENERATED_CELL_POLICY_VERSION.endswith("2026-09-13")


def test_the_pre_author_is_told_to_author_the_response_form():
    assert "response form you intend" in prompts.PREQUESTIONS_AUTHOR_SYSTEM
    assert "response form" in inspect.getsource(prequestions._author_system)

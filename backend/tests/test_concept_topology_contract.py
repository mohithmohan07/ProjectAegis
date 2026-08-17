"""Regression tests for topology-contract rich-text safety and checkpoint refresh."""
from __future__ import annotations

from app.services import concept_topology_contract as contract
from app.services import generation as g


def _row(title: str, *, details: str | None = None, topic: str = "T") -> dict:
    return {
        "topic": topic,
        "parent_concept": "P",
        "concept_title": title,
        "concept_details": details or (
            "Description: A substantive concept description.\n"
            "Achieving Mastery: Applying the concept correctly in a new task. // "
            "Misconception/ Error Analysis: Misconceptions: Students may believe "
            "the rule has no conditions.; Error Analysis: Students may omit a "
            "required condition while applying the rule."
        ),
        "keywords": "concept",
    }


def test_inventory_display_wraps_math_without_changing_identity():
    item = {
        "qid": "QINV-0001",
        "source_kind": "exercise",
        "raw_task": "Use (n^a)^b = n^(ab) and explain the exponent product.",
    }

    raw_public = g._TOPOLOGY_CONTRACT_ORIGINAL_INVENTORY_TEXT(item)
    rendered = g._inventory_task_text(item)

    assert "[Katex]" in rendered
    assert g.kr.rich_text_issues(rendered) == []
    assert g._inventory_coverage_key(rendered) == g._inventory_coverage_key(
        raw_public
    )


def test_canonical_row_repair_is_wrapper_only_and_normalizes_literal_newline():
    details = (
        r"Description: Compare grouped powers.\nAchieving Mastery: Verify "
        r"(n^a)^b = n^(ab). // Misconception/ Error Analysis: "
        r"Misconceptions: Students may believe the exponents are added.; "
        r"Error Analysis: Students may add the exponents while simplifying."
    )

    repaired = g._canonicalize_concept_rich_text(
        [_row("Grouped powers", details=details)]
    )
    output = repaired[0]["concept_details"]

    assert "\\nAchieving Mastery" not in output
    assert "\nAchieving Mastery" in output
    assert "[Katex]" in output
    assert g.kr.rich_text_issues(output) == []


def test_rich_text_diagnostic_names_exact_token_section_and_offset():
    details = (
        "Description: A valid historical explanation. // Types: "
        "Type 01: Source interpretation Case 01: Read the extract "
        r"Example 01: Discuss the command \item and its effect."
    )

    diagnostic = contract.rich_text_defect_detail(g, details)

    assert diagnostic["defect"] == "raw_latex"
    assert diagnostic["match"] == r"\item"
    assert diagnostic["section"].lower().startswith("type")
    assert diagnostic["offset"] == details.index(r"\item")
    assert r"\item" in diagnostic["context"]


def test_old_final_checkpoint_is_forced_through_new_freeze_boundary(monkeypatch):
    monkeypatch.setattr(
        g,
        "_TOPOLOGY_CONTRACT_ORIGINAL_REFRESH_REASONS",
        lambda *_args, **_kwargs: [],
    )
    legacy = {"records": [], "mined_types": {}}
    current = {
        "records": [],
        "mined_types": {
            "_topology_allocation_contract": {
                "version": 1,
                "state": "allocated_after_freeze",
            }
        },
    }

    assert "topology-frozen Type allocation" in " ".join(
        g._final_checkpoint_refresh_reasons(
            legacy, sections=[], source_topic_excerpts=[]
        )
    )
    assert g._final_checkpoint_refresh_reasons(
        current, sections=[], source_topic_excerpts=[]
    ) == []

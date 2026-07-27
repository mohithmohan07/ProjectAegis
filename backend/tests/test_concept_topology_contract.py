"""Regression tests for final-topology Type allocation and rich-text safety."""
from __future__ import annotations

import copy

import pytest

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


def _culmination(topic: str = "T") -> dict:
    return {
        "topic": topic,
        "parent_concept": "Culmination",
        "concept_title": "Culmination - Topic Integration",
        "concept_details": (
            "Description: Recap of the topic's connected concepts and methods."
        ),
        "keywords": "culmination, integration",
    }


def _prepare_kwargs(inventory: dict, mined: dict) -> dict:
    return {
        "subject": "Mathematics",
        "board": "CBSE",
        "chapter_title": "Power Play",
        "meta": g._metadata(
            subject="Mathematics",
            board="CBSE",
            grade="08",
            chapter_title="Power Play",
        ),
        "mmd_text": "# Power Play\nSource text.",
        "source_sections": [],
        "question_task_inventory": inventory,
        "mined_types": mined,
        "method_row_snapshot": {},
        "method_anchors": [],
        "headings": ["T"],
        "source_topic_excerpts": [{"topic": "T", "excerpt": "Source text."}],
        "refresh_chapter_wide_assignments": False,
    }


def _patch_post_assignment_noops(monkeypatch) -> None:
    monkeypatch.setattr(
        g, "_salvage_short_case_examples", lambda records, **_kwargs: records
    )
    monkeypatch.setattr(
        g, "_neutralize_unrepaired_rows", lambda records, **_kwargs: records
    )
    monkeypatch.setattr(
        g,
        "_enforce_rendered_inventory_coverage",
        lambda records, *_args, **_kwargs: records,
    )
    monkeypatch.setattr(
        g,
        "_normalize_activity_hubs_from_inventory",
        lambda records, *_args, **_kwargs: records,
    )
    monkeypatch.setattr(
        g,
        "_disambiguate_certified_split_type_cases",
        lambda records, *_args, **_kwargs: records,
    )
    monkeypatch.setattr(
        g.cr, "renumber_types_continuously", lambda records: records
    )
    monkeypatch.setattr(g, "_canonicalize_concept_rich_text", lambda records: records)
    monkeypatch.setattr(
        g,
        "_repair_final_rich_text_via_api",
        lambda records, **_kwargs: (records, False),
    )
    monkeypatch.setattr(
        g,
        "_reconcile_explicit_figure_images",
        lambda records, _sections: (records, 0),
    )
    monkeypatch.setattr(
        g, "_validate_final_or_raise", lambda records, **_kwargs: {"records": records}
    )
    monkeypatch.setattr(g.progress, "step", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(g.progress, "log", lambda *_args, **_kwargs: None)


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


def test_initial_assignment_boundary_defers_and_strips_stale_types(monkeypatch):
    called = []

    def forbidden(*_args, **_kwargs):
        called.append(1)
        raise AssertionError("initial Type assignment must be deferred")

    monkeypatch.setattr(g, "_TOPOLOGY_CONTRACT_ORIGINAL_ASSIGN_TYPES", forbidden)
    records = [
        _row(
            "Final concept",
            details=(
                "Description: Final concept. // Types: Type 01: Stale pattern "
                "Case 01: Stale case Example 01: Stale question."
            ),
        )
    ]

    token = contract._DEFER_INITIAL_ALLOCATION.set(True)
    try:
        deferred = g._assign_types_via_api(
            records,
            subject="Mathematics",
            mmd_text="source",
            question_task_inventory={"items": []},
            mined_types={"types": []},
        )
    finally:
        contract._DEFER_INITIAL_ALLOCATION.reset(token)

    assert called == []
    assert "Types:" not in deferred[0]["concept_details"]


def test_prepare_allocates_once_after_final_topology_exists(monkeypatch):
    _patch_post_assignment_noops(monkeypatch)
    calls: list[tuple[str, ...]] = []

    def finalise_topology(records, **kwargs):
        assert kwargs["question_task_inventory"] == {"items": [], "stats": {}}
        assert kwargs["mined_types"] == {"types": []}
        assert all("Types:" not in row["concept_details"] for row in records)
        return [_row("Final normal concept"), _culmination()]

    def assign(records, **_kwargs):
        calls.append(tuple(row["concept_title"] for row in records))
        out = copy.deepcopy(records)
        out[0]["concept_details"] += (
            " // Types: Type 01: Applying the final concept "
            "Case 01: A source task is supplied Example 01: Solve the source task."
        )
        return out

    monkeypatch.setattr(g, "_TOPOLOGY_CONTRACT_ORIGINAL_PREPARE", finalise_topology)
    monkeypatch.setattr(g, "_TOPOLOGY_CONTRACT_ORIGINAL_ASSIGN_TYPES", assign)
    monkeypatch.setattr(
        g,
        "_TOPOLOGY_CONTRACT_ORIGINAL_POPULATE_HUBS",
        lambda records, *_args, **_kwargs: records,
    )
    monkeypatch.setattr(
        g, "_TOPOLOGY_CONTRACT_ORIGINAL_CERT_COMPLETE", lambda *_args: True
    )

    inventory = {"items": [], "stats": {}}
    mined = {"types": [{"type_id": "TYPE-0001"}]}
    output = g._prepare_final_concept_content(
        [
            _row(
                "Draft normal concept",
                details=(
                    "Description: Draft. // Types: Type 01: Stale pattern "
                    "Case 01: Stale case Example 01: Stale question."
                ),
            )
        ],
        **_prepare_kwargs(inventory, mined),
    )

    assert calls == [
        ("Final normal concept", "Culmination - Topic Integration")
    ]
    assert "Applying the final concept" in output[0]["concept_details"]
    assert output[-1]["concept_title"].startswith("Culmination -")
    assert mined["_topology_allocation_contract"] == {
        "version": 1,
        "state": "allocated_after_freeze",
        "row_count": 2,
    }


def test_post_freeze_operation_cannot_change_concept_topology(monkeypatch):
    _patch_post_assignment_noops(monkeypatch)

    monkeypatch.setattr(
        g,
        "_TOPOLOGY_CONTRACT_ORIGINAL_PREPARE",
        lambda records, **_kwargs: [_row("Final concept"), _culmination()],
    )
    monkeypatch.setattr(
        g,
        "_TOPOLOGY_CONTRACT_ORIGINAL_ASSIGN_TYPES",
        lambda records, **_kwargs: copy.deepcopy(records),
    )
    monkeypatch.setattr(
        g,
        "_TOPOLOGY_CONTRACT_ORIGINAL_POPULATE_HUBS",
        lambda records, *_args, **_kwargs: records,
    )
    monkeypatch.setattr(
        g, "_TOPOLOGY_CONTRACT_ORIGINAL_CERT_COMPLETE", lambda *_args: True
    )

    def illegal_change(records, *_args, **_kwargs):
        changed = copy.deepcopy(records)
        changed[0]["concept_title"] = "Renamed after allocation"
        return changed

    monkeypatch.setattr(g, "_normalize_activity_hubs_from_inventory", illegal_change)

    with pytest.raises(RuntimeError, match="changed frozen concept topology"):
        g._prepare_final_concept_content(
            [_row("Draft")],
            **_prepare_kwargs(
                {"items": [], "stats": {}}, {"types": []}
            ),
        )


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

"""Regression coverage for ACSD terminal visual-display projection."""
from __future__ import annotations

from app.services import canonical_source_phase2 as phase2
from app.services import (
    canonical_source_phase310_terminal_figure_inventory_convergence_contract as phase310,
)
from app.services import (
    canonical_source_phase311_acsd_visual_display_projection_contract as phase311,
)
from app.services import generation as g

FIG14A_URL = (
    "https://cdn.mathpix.com/cropped/history-19.jpg?"
    "height=1042&width=991&top_left_y=1179&top_left_x=119"
)
FIG14B_URL = (
    "https://cdn.mathpix.com/cropped/history-19.jpg?"
    "height=837&width=744&top_left_y=1273&top_left_x=1149"
)
FIG14A_CAPTION = "Fig. 14(a) - Italian states before unification, 1858."
FIG14B_CAPTION = (
    "Fig. 14(b) - Italy after unification. The map shows the year in which "
    "different regions become part of a unified Italy."
)
QUESTION = (
    "Look at Fig. 14(a). Do you think that the people living in any of these "
    "regions thought of themselves as Italians? Examine Fig. 14(b). Which was "
    "the first region to become a part of unified Italy? Which was the last "
    "region to join? In which year did the largest number of states join?"
)


def _source() -> str:
    return rf"""
\section*{{Italy Unified}}
\begin{{figure}}
\includegraphics[max width=\textwidth]{{{FIG14A_URL}}}
\captionsetup{{labelformat=empty}}
\caption{{{FIG14A_CAPTION}}}
\end{{figure}}

\section*{{Activity}}
{QUESTION}

\begin{{figure}}
\includegraphics[max width=\textwidth]{{{FIG14B_URL}}}
\captionsetup{{labelformat=empty}}
\caption{{{FIG14B_CAPTION}}}
\end{{figure}}
"""


def _inventory(*, stale_single_panel: bool = False) -> dict:
    display = QUESTION
    if stale_single_panel:
        display += f' [img src="{FIG14A_URL}" alt="{FIG14A_CAPTION}"]'
    return {
        "items": [{
            "qid": "QINV-0011",
            "source_kind": "checkpoint_question",
            "source_label": "Activity",
            "topic_hint": "The Making of Germany and Italy",
            "raw_task": QUESTION,
            "normalized_task": QUESTION,
            "_activity_origin": True,
            "_acsd_source_contract": phase2.SOURCE_CONTRACT_MODE,
            "_acsd_display_prompt": display,
        }],
        "stats": {},
    }


def _record() -> dict:
    return {
        "topic": "The Making of Germany and Italy",
        "parent_concept": "Italian unification",
        "concept_title": "Reading the Maps of Italian Unification",
        "concept_details": (
            "Description: Comparative maps show the territorial sequence of "
            "Italian unification.\n"
            "Achieving Mastery: The learner compares both maps and identifies "
            "the sequence of territorial accession. // Types: Type 01: Reading "
            "paired historical maps Case 01: Compare Italy before and after "
            f"unification Example 01: {QUESTION} // Misconception/ Error "
            "Analysis: Misconceptions: The learner may believe Italy was already "
            "a single state before unification.; Error Analysis: The learner may "
            "read only one map and omit the chronology shown by the other."
        ),
        "keywords": "Italy, unification, historical maps",
    }


def test_acsd_multi_panel_projection_matches_terminal_example_exactly():
    records = [_record()]
    inventory = _inventory()
    item = inventory["items"][0]
    immutable_display = item["_acsd_display_prompt"]

    assert FIG14A_URL not in g._inventory_task_text(item)
    assert FIG14B_URL not in g._inventory_task_text(item)

    result = phase310._converge_terminal_figure_inventory(
        g,
        records,
        inventory,
        {},
        source_text=_source(),
        stage="deposit",
    )

    assert result["projected_qids"] == ["QINV-0011"]
    assert result["repaired_examples"] == 1
    assert result["defects"] == {"missing": [], "duplicate": []}
    assert item["_acsd_display_prompt"] == immutable_display

    rendered_inventory = g._inventory_task_text(item)
    assert rendered_inventory.count(FIG14A_URL) == 1
    assert rendered_inventory.count(FIG14B_URL) == 1
    assert ", visual 2" in rendered_inventory
    assert item[phase311._DISPLAY_KEY]["version"] == phase311._DISPLAY_VERSION

    rendered_example = g._rendered_type_examples(records)[0]
    assert g._inventory_coverage_key(rendered_example) == (
        g._inventory_coverage_key(rendered_inventory)
    )


def test_acsd_projection_replaces_stale_single_panel_display_losslessly():
    records = [_record()]
    inventory = _inventory(stale_single_panel=True)
    item = inventory["items"][0]
    original_acsd_display = item["_acsd_display_prompt"]

    result = phase310._converge_terminal_figure_inventory(
        g,
        records,
        inventory,
        {},
        source_text=_source(),
        stage="deposit",
    )

    assert result["defects"] == {"missing": [], "duplicate": []}
    public_display = g._inventory_task_text(item)
    assert public_display.count(FIG14A_URL) == 1
    assert public_display.count(FIG14B_URL) == 1
    assert item["_acsd_display_prompt"] == original_acsd_display
    assert records[0]["concept_details"].count(FIG14A_URL) == 1
    assert records[0]["concept_details"].count(FIG14B_URL) == 1


def test_projection_override_is_idempotent_and_does_not_rewrite_source_text():
    records = [_record()]
    inventory = _inventory()
    item = inventory["items"][0]
    raw = item["raw_task"]
    normalized = item["normalized_task"]

    first = phase310._converge_terminal_figure_inventory(
        g,
        records,
        inventory,
        {},
        source_text=_source(),
        stage="final",
    )
    first_display = g._inventory_task_text(item)
    first_record = records[0]["concept_details"]
    second = phase310._converge_terminal_figure_inventory(
        g,
        records,
        inventory,
        {},
        source_text=_source(),
        stage="deposit",
    )

    assert first["defects"] == {"missing": [], "duplicate": []}
    assert second["defects"] == {"missing": [], "duplicate": []}
    assert second["projected_qids"] == []
    assert second["repaired_examples"] == 0
    assert g._inventory_task_text(item) == first_display
    assert records[0]["concept_details"] == first_record
    assert item["raw_task"] == raw
    assert item["normalized_task"] == normalized

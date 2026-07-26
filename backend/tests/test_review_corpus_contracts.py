"""Deterministic ledgers for the three source chapters used in review."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from app.services import generation as g


DATA = Path(__file__).parents[1] / "data" / "Testing"


@pytest.mark.parametrize(
    ("filename", "expected_topics", "expected_counts"),
    [
        (
            "jemh105 (1).mmd",
            [
                "Introduction",
                "Arithmetic Progressions",
                "nth Term of an AP",
                "Sum of First n Terms of an AP",
            ],
            {"worked_example": 16, "exercise": 49},
        ),
        (
            "Class 10 Chapter 5 Electricity.mmd",
            [
                "ELECTRIC CURRENT AND CIRCUIT",
                "ELECTRIC POTENTIAL AND POTENTIAL DIFFERENCE",
                "CIRCUIT DIAGRAM",
                "OHM'S LAW",
                "FACTORS ON WHICH THE RESISTANCE OF A CONDUCTOR DEPENDS",
                "RESISTANCE OF A SYSTEM OF RESISTORS",
                "HEATING EFFECT OF ELECTRIC CURRENT",
                "ELECTRIC POWER",
            ],
            {
                "worked_example": 13,
                "intext_question": 23,
                "checkpoint_question": 6,
                "exercise": 18,
            },
        ),
        (
            "RNE.mmd",
            [
                "The French Revolution and the Idea of the Nation",
                "The Making of Nationalism in Europe",
                "The Age of Revolutions: 1830-1848",
                "The Making of Germany and Italy",
                "Visualising the Nation",
                "Nationalism and Imperialism",
            ],
            {
                "checkpoint_question": 14,
                "activity": 2,
                "exercise": 10,
            },
        ),
    ],
)
def test_review_source_task_ledgers_are_stable(
    filename, expected_topics, expected_counts,
):
    source = (DATA / filename).read_text(encoding="utf-8")
    sections = g.parse_mmd_sections(source)
    anchors = g._source_task_anchors(sections)

    assert g._topic_headings(sections) == expected_topics
    assert dict(Counter(
        str(anchor.get("source_kind") or "") for anchor in anchors
    )) == expected_counts
    assert len(anchors) == sum(expected_counts.values())
    assert all(
        str(anchor.get("topic_hint") or "").strip()
        or anchor.get("_topic_scope") == "chapter"
        for anchor in anchors
    )
    assert len({
        (
            str(anchor.get("source_label") or "").strip(),
            g._inventory_task_match_key(anchor),
        )
        for anchor in anchors
    }) == len(anchors)


def test_rne_chapter_final_review_units_remain_grouped_and_complete():
    source = (DATA / "RNE.mmd").read_text(encoding="utf-8")
    anchors = g._source_task_anchors(g.parse_mmd_sections(source))
    chapter_final = [
        anchor for anchor in anchors
        if anchor.get("_topic_scope") == "chapter"
    ]

    assert Counter(
        str(anchor.get("source_kind") or "")
        for anchor in chapter_final
    ) == {"exercise": 10, "activity": 1}
    assert len(chapter_final) == 11
    assert all(
        not (anchor.get("subpart_label") or "").strip()
        for anchor in chapter_final
    )
    assert any(
        all(f"{letter})" in anchor["raw_task"] for letter in "abcde")
        for anchor in chapter_final
    )

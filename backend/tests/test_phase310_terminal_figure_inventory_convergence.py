"""Regression coverage for terminal Figure and exact-inventory convergence."""
from __future__ import annotations

from app.services import (
    canonical_source_phase310_terminal_figure_inventory_convergence_contract as phase310,
)
from app.services import generation as g


FIG10_URL = "https://cdn.example.test/history/fig-10.jpg"
STALE_URL = "https://cdn.example.test/history/fig-19.jpg"
QUESTION = (
    "Look once more at Fig. 10. Imagine you were a citizen of Frankfurt in "
    "March 1848 and were present during the proceedings of the parliament."
)


def _source() -> str:
    return rf"""
\section*{{The Age of Revolutions: 1830-1848}}
\begin{{figure}}
\includegraphics[max width=\textwidth]{{{FIG10_URL}}}
\captionsetup{{labelformat=empty}}
\caption{{Fig. 10 - The Frankfurt parliament in the Church of St Paul.}}
\end{{figure}}

\section*{{Activity}}
{QUESTION}
"""


def _inventory(*, stale: bool = False, figure_number: str = "10") -> dict:
    question = QUESTION.replace("Fig. 10", f"Fig. {figure_number}")
    item = {
        "qid": "QINV-0021",
        "source_kind": "checkpoint_question",
        "source_label": "Discuss",
        "topic_hint": "The Age of Revolutions: 1830-1848",
        "raw_task": question,
        "normalized_task": question,
    }
    if stale:
        item.update({
            "image_urls": [STALE_URL],
            "_image_captions": {STALE_URL: "Fig. 19 - Germania"},
            "_figure_images_resolved": True,
            "requires_visual": True,
        })
    return {"items": [item], "stats": {}}


def _record(*, stale: bool = False, figure_number: str = "10") -> dict:
    question = QUESTION.replace("Fig. 10", f"Fig. {figure_number}")
    if stale:
        question += f' [img src="{STALE_URL}" alt="Fig. 19 - Germania"]'
    return {
        "topic": "The Age of Revolutions: 1830-1848",
        "parent_concept": "Revolutionary nationalism",
        "concept_title": "Frankfurt Parliament and National Representation",
        "concept_details": (
            "Description: The Frankfurt Parliament expressed constitutional "
            "nationalist aspirations.\n"
            "Achieving Mastery: The learner interprets the political setting of "
            "the Frankfurt Parliament. // Types: Type 01: Interpreting a named "
            "historical Figure Case 01: Connect a source visual to its setting "
            f"Example 01: {question} // Misconception/ Error Analysis: "
            "Misconceptions: The learner may believe the parliament was an "
            "elected government with executive power.; Error Analysis: The "
            "learner may describe the image without linking it to constitutional "
            "nationalism."
        ),
        "keywords": "Frankfurt Parliament, nationalism, representation",
    }


def test_terminal_figure_projection_keeps_exact_qid_coverage():
    records = [_record()]
    inventory = _inventory()
    raw_before = inventory["items"][0]["raw_task"]

    result = phase310._converge_terminal_figure_inventory(
        g,
        records,
        inventory,
        {},
        source_text=_source(),
        stage="deposit",
    )

    assert result["projected_qids"] == ["QINV-0021"]
    assert result["repaired_examples"] == 1
    assert result["defects"] == {"missing": [], "duplicate": []}
    assert inventory["items"][0]["raw_task"] == raw_before
    assert inventory["items"][0]["image_urls"] == [FIG10_URL]
    assert inventory["items"][0]["_figure_images_resolved"] is True
    assert FIG10_URL in g._inventory_task_text(inventory["items"][0])
    assert FIG10_URL in records[0]["concept_details"]

    rendered = g._rendered_type_examples(records)
    assert len(rendered) == 1
    assert g._inventory_coverage_key(rendered[0]) == g._inventory_coverage_key(
        g._inventory_task_text(inventory["items"][0])
    )


def test_stale_visual_is_replaced_in_both_inventory_and_example():
    records = [_record(stale=True)]
    inventory = _inventory(stale=True)

    result = phase310._converge_terminal_figure_inventory(
        g,
        records,
        inventory,
        {},
        source_text=_source(),
        stage="deposit",
    )

    assert result["defects"] == {"missing": [], "duplicate": []}
    assert inventory["items"][0]["image_urls"] == [FIG10_URL]
    assert STALE_URL not in g._inventory_task_text(inventory["items"][0])
    assert STALE_URL not in records[0]["concept_details"]
    assert records[0]["concept_details"].count(FIG10_URL) == 1


def test_unresolved_figure_is_not_invented_or_silently_reassigned():
    inventory = _inventory(figure_number="99")
    item = inventory["items"][0]
    raw_before = item["raw_task"]

    projected = phase310._project_inventory_figures(
        g,
        inventory,
        source_text=_source(),
    )

    assert projected == []
    assert item["raw_task"] == raw_before
    assert item.get("image_urls") in (None, [])
    assert phase310._PROJECTION_KEY not in item


def test_terminal_convergence_is_idempotent():
    records = [_record()]
    inventory = _inventory()

    first = phase310._converge_terminal_figure_inventory(
        g,
        records,
        inventory,
        {},
        source_text=_source(),
        stage="final",
    )
    snapshot = records[0]["concept_details"]
    second = phase310._converge_terminal_figure_inventory(
        g,
        records,
        inventory,
        {},
        source_text=_source(),
        stage="deposit",
    )

    assert first["defects"] == {"missing": [], "duplicate": []}
    assert second["projected_qids"] == []
    assert second["repaired_examples"] == 0
    assert second["defects"] == {"missing": [], "duplicate": []}
    assert records[0]["concept_details"] == snapshot


def test_one_bounded_retry_repairs_inner_terminal_visual_drift():
    records = [_record()]
    inventory = _inventory()
    calls: list[int] = []

    def legacy_inner_validator(rows, *_args, **_kwargs):
        calls.append(1)
        if len(calls) == 1:
            # Reproduce a legacy inner presentation pass that drops the image
            # after the outer preflight but before exact coverage is checked.
            rows[0]["concept_details"] = g._RENDERED_IMAGE_TAG_RE.sub(
                " ", rows[0]["concept_details"]
            )
            raise RuntimeError(
                "deposit validation failed: exact_inventory_coverage; 1 missing"
            )
        assert g._rendered_inventory_coverage_defects(
            rows,
            inventory,
        ) == {"missing": [], "duplicate": []}
        assert FIG10_URL in rows[0]["concept_details"]
        return "validated"

    result = phase310._validate_with_terminal_figure_inventory_convergence(
        g,
        legacy_inner_validator,
        records,
        (),
        {
            "stage": "deposit",
            "inventory": inventory,
            "mined_types": {},
            "source_text": _source(),
        },
    )

    assert result == "validated"
    assert calls == [1, 1]
    assert g._rendered_inventory_coverage_defects(
        records,
        inventory,
    ) == {"missing": [], "duplicate": []}

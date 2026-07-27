"""Regression coverage for terminal chapter-wide Figure reconciliation."""
from __future__ import annotations

from types import SimpleNamespace

from app.services import generation as g
from app.services import terminal_figure_contract as contract


FIG10_URL = (
    "https://cdn.mathpix.com/cropped/"
    "6607f4a6-cb7c-4963-a6ea-e5e36dc69d32-16.jpg"
    "?height=1328&width=1775&top_left_y=242&top_left_x=110"
)
FIG19_URL = (
    "https://cdn.mathpix.com/cropped/"
    "6607f4a6-cb7c-4963-a6ea-e5e36dc69d32-23.jpg"
    "?height=1509&width=1204&top_left_y=244&top_left_x=119"
)


def _distant_figure_source() -> str:
    filler = "Nationalist visual culture developed through public symbols.\n" * 40
    return rf"""
\section*{{The Age of Revolutions: 1830-1848}}
\begin{{figure}}
\includegraphics[max width=\textwidth]{{{FIG10_URL}}}
\captionsetup{{labelformat=empty}}
\caption{{Fig. 10 - The Frankfurt parliament in the Church of St Paul.
Contemporary colour print. Notice the women in the upper left gallery.}}
\end{{figure}}

{filler}

\section*{{Visualising the Nation}}
\begin{{figure}}
\includegraphics[max width=\textwidth]{{{FIG19_URL}}}
\captionsetup{{labelformat=empty}}
\caption{{Fig. 19 - Germania guarding the Rhine, Lorenz Clasen.}}
\end{{figure}}

\section*{{Activity}}
Look once more at Fig. 10. Imagine you were a citizen of Frankfurt in March
1848 and were present during the proceedings of the parliament.
"""


def _record_without_image() -> dict:
    return {
        "topic": "Visualising the Nation",
        "parent_concept": "National Allegories",
        "concept_title": "Female Allegories of National Identity",
        "concept_details": (
            "Description: Artists represented nations through female allegories.\n"
            "Achieving Mastery: Interpreting national symbols using their "
            "historical setting. // Types: Type 01: Interpreting allegorical "
            "images — Connect a named source visual to its political setting "
            "Case 01: Relating the Frankfurt parliament to Germania "
            "Example 01: Look once more at Fig. 10. Imagine you were a citizen "
            "of Frankfurt in March 1848 and were present during the proceedings "
            "of the parliament. How would you relate to the banner of Germania "
            "hanging from the ceiling? // Misconception/ Error Analysis: "
            "Misconceptions: Students may believe every Germania image depicts "
            "the same historical moment.; Error Analysis: Students may omit the "
            "Frankfurt parliament context while interpreting the banner."
        ),
        "keywords": "Germania, Frankfurt parliament, allegory",
    }


def test_terminal_reconciliation_resolves_distant_explicit_figure_by_id():
    records = [_record_without_image()]

    repaired = g._reconcile_terminal_figure_images(
        records,
        _distant_figure_source(),
    )

    assert repaired == 1
    details = records[0]["concept_details"]
    assert f'[img src="{FIG10_URL}" ' in details
    assert 'alt="Fig. 10 - The Frankfurt parliament in the Church of St Paul.' in details
    assert FIG19_URL not in details

    # The terminal repair is deterministic and idempotent.
    assert g._reconcile_terminal_figure_images(
        records,
        _distant_figure_source(),
    ) == 0


def test_validation_wrapper_reconciles_before_original_terminal_gate():
    calls: list[tuple[str, str]] = []
    logs: list[str] = []

    def reconcile(records, sections):
        assert sections == [{"heading": "source"}]
        updated = [dict(row) for row in records]
        updated[0]["concept_details"] += " [img src=\"https://example.org/10.jpg\" alt=\"Fig. 10\"]"
        calls.append(("reconcile", updated[0]["concept_details"]))
        return updated, 1

    def original_validate(records, **kwargs):
        calls.append(("validate", records[0]["concept_details"]))
        return {"ok": True, "errors": [], "stage": kwargs.get("stage")}

    fake = SimpleNamespace(
        _validate_final_or_raise=original_validate,
        parse_mmd_sections=lambda _source: [{"heading": "source"}],
        _reconcile_explicit_figure_images=reconcile,
        progress=SimpleNamespace(log=lambda message, **_kwargs: logs.append(message)),
    )

    contract.install(fake)
    rows = [{"concept_details": "Example 01: Look at Fig. 10."}]
    report = fake._validate_final_or_raise(
        rows,
        stage="post-freeze allocation",
        source_text="chapter MMD",
    )

    assert report["ok"] is True
    assert [name for name, _value in calls] == ["reconcile", "validate"]
    assert "https://example.org/10.jpg" in calls[-1][1]
    assert "https://example.org/10.jpg" in rows[0]["concept_details"]
    assert any("chapter-wide MMD registry" in message for message in logs)


def test_nonterminal_validation_does_not_mutate_figure_content():
    reconcile_calls = 0

    def reconcile(records, sections):
        nonlocal reconcile_calls
        reconcile_calls += 1
        return records, 0

    fake = SimpleNamespace(
        _validate_final_or_raise=lambda records, **kwargs: {"ok": True},
        parse_mmd_sections=lambda _source: [],
        _reconcile_explicit_figure_images=reconcile,
        progress=SimpleNamespace(log=lambda *_args, **_kwargs: None),
    )
    contract.install(fake)

    fake._validate_final_or_raise(
        [{"concept_details": "Description: draft"}],
        stage="description",
        source_text="chapter MMD",
    )

    assert reconcile_calls == 0

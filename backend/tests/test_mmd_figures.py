"""Tests for Mathpix figure inlining in MMD text."""
from __future__ import annotations

from app.services.mmd_figures import (
    FigureEntry,
    build_figure_map,
    enrich_mmd_with_figures,
    inline_figure_references,
)

_CDN = "https://cdn.mathpix.com/cropped/sample-fig.jpg?height=100&width=200"


def _sample_lines() -> dict:
    """Mathpix lines.json shape with linked diagram + figure_label."""
    diagram_id = "diag-1"
    label_id = "label-1"
    return {
        "pages": [
            {
                "page": 1,
                "lines": [
                    {
                        "id": diagram_id,
                        "type": "diagram",
                        "text": "",
                        "text_display": (
                            f"\\begin{{figure}}\n"
                            f"\\includegraphics[width=\\textwidth]{{{_CDN}}}\n"
                            f"\\caption{{Fig. 11.6 A simple electric circuit}}\n"
                            f"\\end{{figure}}"
                        ),
                        "selected_labels": [label_id],
                    },
                    {
                        "id": label_id,
                        "type": "figure_label",
                        "text": "Fig. 11.6 A simple electric circuit",
                        "text_display": "",
                    },
                ],
            }
        ]
    }


def test_build_figure_map_from_selected_labels():
    fig_map = build_figure_map(_sample_lines())
    assert "11.6" in fig_map
    assert fig_map["11.6"].url == _CDN
    assert "simple electric circuit" in fig_map["11.6"].caption


def test_inline_figure_reference_in_prose():
    fig_map = build_figure_map(_sample_lines())
    mmd = "Connect them as shown in Fig. 11.6 to measure current."
    out = inline_figure_references(mmd, fig_map)
    assert "Fig. 11.6 to measure" not in out
    assert f"![Fig. 11.6 A simple electric circuit]({_CDN})" in out
    assert "Connect them as shown in" in out


def test_inline_figure_reference_case_insensitive():
    fig_map = build_figure_map(_sample_lines())
    mmd = "See figure 11.6 for the setup."
    out = inline_figure_references(mmd, fig_map)
    assert "figure 11.6" not in out.lower()
    assert _CDN in out


def test_does_not_replace_unknown_figure():
    fig_map = build_figure_map(_sample_lines())
    mmd = "Refer to Fig. 99.9 which is missing."
    out = inline_figure_references(mmd, fig_map)
    assert out == mmd


def test_does_not_replace_inside_existing_markdown_image():
    fig_map = build_figure_map(_sample_lines())
    mmd = f"Already embedded: ![Fig. 11.6 alt]({_CDN})"
    out = inline_figure_references(mmd, fig_map)
    assert out == mmd


def test_enrich_mmd_with_figures_end_to_end():
    mmd = (
        "Activity\n\n"
        "Take a nichrome wire and connect as shown in Fig. 11.6.\n\n"
        f"![]({_CDN})\n"
    )
    out = enrich_mmd_with_figures(mmd, _sample_lines())
    assert out.count(_CDN) >= 2  # block image + inlined reference
    assert "as shown in ![Fig. 11.6 A simple electric circuit]" in out


def test_figure_entry_markdown_escapes_brackets():
    entry = FigureEntry(number="1", url=_CDN, caption="Fig [A]")
    assert entry.markdown() == f"![Fig \\[A\\]]({_CDN})"

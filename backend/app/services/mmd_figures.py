"""Inline Mathpix figure crops for bare Fig./Figure references in MMD text."""
from __future__ import annotations

import sys
from pathlib import Path

_src = Path(__file__).resolve().parents[2] / "aegis_pipeline" / "create_workbooks" / "src"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

from figure_inline import (  # noqa: E402
    FigureEntry,
    build_figure_map,
    enrich_mmd_with_figures,
    figure_markdown_index_from_mmd,
    inline_figure_references,
    inline_figures_in_text,
)

__all__ = [
    "FigureEntry",
    "build_figure_map",
    "enrich_mmd_with_figures",
    "figure_markdown_index_from_mmd",
    "inline_figure_references",
    "inline_figures_in_text",
]

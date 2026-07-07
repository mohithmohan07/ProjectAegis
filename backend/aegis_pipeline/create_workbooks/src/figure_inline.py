"""Inline Mathpix figure crops for bare Fig./Figure references in MMD text.

Mathpix PDF conversion produces:
  - MMD with optional ``![](https://cdn.mathpix.com/cropped/...)`` blocks
  - ``lines.json`` linking ``figure_label`` captions to ``diagram`` / ``chart``
    visuals via ``selected_labels``

This module maps caption numbers (e.g. ``11.6``) to image URLs and replaces
prose references like ``Fig. 11.6`` with inline markdown images so downstream
LLM prompts see the actual figure, not a dangling label.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# Caption / label text: "Fig. 11.6", "Figure 1:", "FIGURE 2.3"
_FIG_NUM_RE = re.compile(
    r"\bfig(?:ure)?\.?\s*(\d+(?:\.\d+)*)",
    re.IGNORECASE,
)

# Prose reference to replace: "Fig. 11.6", "Figure 11.6"
_FIG_REF_RE = re.compile(
    r"\b(Fig(?:ure)?\.?\s*\d+(?:\.\d+)*)",
    re.IGNORECASE,
)

_CDN_URL_RE = re.compile(
    r"https://cdn\.mathpix\.com/cropped/[^\s\}\)\"']+",
    re.IGNORECASE,
)
_INCLUDEGRAPHICS_RE = re.compile(
    r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}",
)
_LATEX_CAPTION_RE = re.compile(
    r"\\caption\{([^}]*)\}",
    re.IGNORECASE,
)
_MARKDOWN_IMG_RE = re.compile(
    r"!\[[^\]]*\]\((https://cdn\.mathpix\.com/cropped/[^)]+)\)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class FigureEntry:
    """A figure number mapped to its Mathpix crop URL and caption."""

    number: str
    url: str
    caption: str = ""

    @property
    def alt_text(self) -> str:
        if self.caption:
            return self.caption.strip()
        return f"Fig. {self.number}"

    def markdown(self) -> str:
        alt = (
            self.alt_text.replace("\\", "\\\\")
            .replace("[", "\\[")
            .replace("]", "\\]")
        )
        return f"![{alt}]({self.url})"


def _extract_image_url(text: str) -> str:
    if not text:
        return ""
    m = _MARKDOWN_IMG_RE.search(text)
    if m:
        return m.group(1)
    m = _INCLUDEGRAPHICS_RE.search(text)
    if m:
        url = m.group(1).strip()
        if url.startswith("http"):
            return url
    m = _CDN_URL_RE.search(text)
    return m.group(0) if m else ""


def _parse_fig_numbers(text: str) -> list[str]:
    if not text:
        return []
    return [m.group(1) for m in _FIG_NUM_RE.finditer(text)]


def _normalize_fig_num(num: str) -> str:
    """Normalize figure numbers for lookup (``11.06`` -> ``11.6``)."""
    parts = num.split(".")
    if len(parts) == 1:
        return str(int(parts[0])) if parts[0].isdigit() else num
    head, tail = parts[0], parts[1]
    if head.isdigit() and tail.isdigit():
        return f"{int(head)}.{int(tail)}"
    return num


def _index_lines(lines_data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for page in lines_data.get("pages") or []:
        for line in page.get("lines") or []:
            lid = line.get("id")
            if lid:
                by_id[lid] = line
    return by_id


def build_figure_map(lines_data: dict[str, Any]) -> dict[str, FigureEntry]:
    """Build ``fig_number -> FigureEntry`` from Mathpix ``lines.json``."""
    by_id = _index_lines(lines_data)
    fig_map: dict[str, FigureEntry] = {}

    def _register(num: str, url: str, caption: str) -> None:
        if not num or not url:
            return
        key = _normalize_fig_num(num)
        entry = FigureEntry(number=key, url=url, caption=caption.strip())
        existing = fig_map.get(key)
        if existing is None or (caption and len(caption) > len(existing.caption)):
            fig_map[key] = entry

    for line in by_id.values():
        ltype = (line.get("type") or "").lower()
        if ltype not in {"diagram", "chart", "table"}:
            continue
        url = _extract_image_url(line.get("text_display") or "")
        if not url:
            continue

        display = line.get("text_display") or ""
        cap_m = _LATEX_CAPTION_RE.search(display)
        display_caption = cap_m.group(1).strip() if cap_m else ""

        for label_id in line.get("selected_labels") or []:
            label_line = by_id.get(label_id)
            if not label_line:
                continue
            label_text = (label_line.get("text") or "").strip()
            for num in _parse_fig_numbers(label_text):
                _register(num, url, label_text or display_caption)

        for num in _parse_fig_numbers(display_caption):
            _register(num, url, display_caption)

    for line in by_id.values():
        if (line.get("type") or "").lower() != "figure_label":
            continue
        label_text = (line.get("text") or "").strip()
        nums = _parse_fig_numbers(label_text)
        if not nums:
            continue
        parent = by_id.get(line.get("parent_id") or "")
        url = _extract_image_url((parent or {}).get("text_display") or "")
        if url:
            for num in nums:
                _register(num, url, label_text)

    return fig_map


def _lookup(fig_map: dict[str, FigureEntry], ref_text: str) -> FigureEntry | None:
    nums = _parse_fig_numbers(ref_text)
    if not nums:
        return None
    key = _normalize_fig_num(nums[0])
    return fig_map.get(key)


def _inside_markdown_image(text: str, start: int) -> bool:
    """True when ``start`` falls inside an existing ``![...](...)`` span."""
    before = text[:start]
    if before.rfind("![") > before.rfind("]("):
        return True
    return False


def inline_figure_references(
    mmd: str,
    fig_map: dict[str, FigureEntry],
    *,
    also_from_mmd: bool = True,
) -> str:
    """Replace bare ``Fig. X.Y`` references with inline ``![caption](url)``."""
    if not mmd or not fig_map:
        return mmd

    if also_from_mmd:
        enriched = dict(fig_map)
        for m in _MARKDOWN_IMG_RE.finditer(mmd):
            alt = m.group(0)[2 : m.group(0).index("](")]
            url = m.group(1)
            for num in _parse_fig_numbers(alt):
                key = _normalize_fig_num(num)
                if key not in enriched:
                    enriched[key] = FigureEntry(number=key, url=url, caption=alt)
        fig_map = enriched

    def _repl(match: re.Match[str]) -> str:
        if _inside_markdown_image(mmd, match.start()):
            return match.group(0)
        entry = _lookup(fig_map, match.group(1))
        if entry is None:
            return match.group(0)
        return entry.markdown()

    return _FIG_REF_RE.sub(_repl, mmd)


def enrich_mmd_with_figures(mmd: str, lines_data: dict[str, Any] | None) -> str:
    """Build figure map from lines.json and inline references in MMD."""
    if not lines_data:
        return mmd
    fig_map = build_figure_map(lines_data)
    return inline_figure_references(mmd, fig_map)

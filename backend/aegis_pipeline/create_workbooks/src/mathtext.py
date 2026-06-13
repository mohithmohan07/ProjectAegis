"""Render plain-text mathematics into ReportLab markup (and a canvas-safe
flattened form).

The model writes maths in plain unicode ("x² + 5x + 6 = 0", "√((x₂−x₁)²)").
Two problems:
  • Subscripts (₀–₉) and many superscripts (⁰, ⁴–⁹) are missing from Arial, so
    they render as tofu boxes (the "x□ − x□" the user saw).
  • Even when present, real <super>/<sub> typesetting reads far better.

`math_markup` → for Paragraph flowables: returns escaped text with proper
                <super>/<sub> tags (also keeps our <b>/<br/> markers).
`math_flatten` → for canvas-drawn diagrams (which can't use markup): converts
                 superscripts to ^N and subscripts to plain digits so nothing
                 turns into tofu.
"""
from __future__ import annotations

import re
from html import escape

from reportlab.pdfbase.pdfmetrics import stringWidth

_SUP = {
    "⁰": "0", "¹": "1", "²": "2", "³": "3", "⁴": "4", "⁵": "5", "⁶": "6",
    "⁷": "7", "⁸": "8", "⁹": "9", "⁺": "+", "⁻": "-", "⁼": "=", "⁽": "(",
    "⁾": ")", "ⁿ": "n", "ⁱ": "i",
}
_SUB = {
    "₀": "0", "₁": "1", "₂": "2", "₃": "3", "₄": "4", "₅": "5", "₆": "6",
    "₇": "7", "₈": "8", "₉": "9", "₊": "+", "₋": "-", "₌": "=", "₍": "(",
    "₎": ")",
}


# Compound SI units the model sometimes writes with a plain hyphen exponent
# ("m s-1", "km h-1", "kg m-3") instead of a proper superscript.
_UNIT_EXP = re.compile(r"(?<=[A-Za-z])\s(s|h|min|m|N|g|mol|Hz|Pa|J|W|K)-(\d)\b")


def _normalize_sci(text) -> str:
    """Make scientific notation consistent before any rendering:
      • fraction slash (U+2044) → ordinary solidus
      • simple fractions → vulgar glyphs (½ ¼ ¾) for a clean look
      • compound-unit exponents written with a hyphen → caret form (so they
        become real superscripts): "m s-1" → "m s^-1".
    """
    s = str(text or "")
    s = s.replace("\u2044", "/")
    s = re.sub(r"(?<!\d)1/2(?!\d)", "\u00bd", s)
    s = re.sub(r"(?<!\d)1/4(?!\d)", "\u00bc", s)
    s = re.sub(r"(?<!\d)3/4(?!\d)", "\u00be", s)
    s = _UNIT_EXP.sub(r" \1^-\2", s)
    return s


def _segment(text: str):
    """Split text into ('text'|'sup'|'sub', chunk) runs."""
    segs: list[tuple[str, str]] = []
    buf: list[str] = []
    mode = "text"

    def flush(new_mode: str) -> None:
        nonlocal buf, mode
        if buf:
            segs.append((mode, "".join(buf)))
            buf = []
        mode = new_mode

    for ch in text:
        if ch in _SUP:
            if mode != "sup":
                flush("sup")
            buf.append(_SUP[ch])
        elif ch in _SUB:
            if mode != "sub":
                flush("sub")
            buf.append(_SUB[ch])
        else:
            if mode != "text":
                flush("text")
            buf.append(ch)
    flush("text")
    return segs


def _apply_caret_underscore(s: str) -> str:
    """Convert ^N / ^{...} and _N / _{...} to markup (s is already escaped)."""
    s = re.sub(r"\^\{([^}]+)\}", r"<super>\1</super>", s)
    s = re.sub(r"\^([0-9n+\-]+)", r"<super>\1</super>", s)
    s = re.sub(r"(?<=\w)_\{([^}]+)\}", r"<sub>\1</sub>", s)
    s = re.sub(r"(?<=\w)_([0-9]+)", r"<sub>\1</sub>", s)
    return s


def _light_markup(text: str) -> str:
    """Turn common inline markers into ReportLab tags before HTML escaping."""
    s = str(text or "")
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<i>\1</i>", s)
    return s


def _restore_allowed_tags(s: str) -> str:
    """Re-enable a small set of inline tags after html.escape."""
    s = s.replace("&lt;b&gt;", "<b>").replace("&lt;/b&gt;", "</b>")
    s = s.replace("&lt;i&gt;", "<i>").replace("&lt;/i&gt;", "</i>")
    s = s.replace("&lt;br/&gt;", "<br/>").replace("&lt;br&gt;", "<br/>")
    s = s.replace("&lt;super&gt;", "<super>").replace("&lt;/super&gt;", "</super>")
    s = s.replace("&lt;sub&gt;", "<sub>").replace("&lt;/sub&gt;", "</sub>")
    return s


def math_markup(text) -> str:
    out: list[str] = []
    for mode, chunk in _segment(_normalize_sci(text)):
        if mode == "sup":
            out.append(f"<super>{escape(chunk)}</super>")
        elif mode == "sub":
            out.append(f"<sub>{escape(chunk)}</sub>")
        else:
            s = _light_markup(chunk)
            s = escape(s)
            s = _restore_allowed_tags(s)
            s = _apply_caret_underscore(s)
            s = s.replace("\n", "<br/>")
            out.append(s)
    return "".join(out)


def math_flatten(text) -> str:
    """Canvas-safe: superscripts → ^N, subscripts → plain digits."""
    out: list[str] = []
    for mode, chunk in _segment(_normalize_sci(text)):
        if mode == "sup":
            out.append("^" + chunk)
        elif mode == "sub":
            out.append(chunk)
        else:
            s = re.sub(r"\^\{([^}]+)\}", r"^\1", chunk)
            s = re.sub(r"_\{([^}]+)\}", r"\1", s)
            s = re.sub(r"(?<=\w)_([0-9]+)", r"\1", s)
            out.append(s)
    return "".join(out)


# ----------------------------------------------------------------------------
# Canvas rich-text: draw real super/subscripts directly on a ReportLab canvas.
#
# Paragraph flowables get proper <super>/<sub> via math_markup, but diagrams
# (flowcharts, trees, cycles, …) draw onto the canvas with drawString, which
# cannot use markup — so exponents used to leak through as "mv^2" or collapse
# to "mv2". `draw_rich` parses the same notation and renders it correctly.
# ----------------------------------------------------------------------------

_SUP_SHIFT = 0.34   # fraction of font size to raise a superscript
_SUB_SHIFT = 0.16   # fraction of font size to lower a subscript
_SMALL = 0.72       # super/subscript font scale


def _rich_runs(text) -> list[tuple[str, str]]:
    """Parse text into ('n'|'sup'|'sub', chunk) runs, understanding unicode
    super/subscripts, ^N / ^{...} and _N / _{...}."""
    s = _normalize_sci(text)
    runs: list[tuple[str, str]] = []
    buf: list[str] = []
    i, n = 0, len(s)

    def flush() -> None:
        if buf:
            runs.append(("n", "".join(buf)))
            buf.clear()

    while i < n:
        ch = s[i]
        if ch in _SUP:
            flush()
            j = i
            while j < n and s[j] in _SUP:
                j += 1
            runs.append(("sup", "".join(_SUP[c] for c in s[i:j])))
            i = j
            continue
        if ch in _SUB:
            flush()
            j = i
            while j < n and s[j] in _SUB:
                j += 1
            runs.append(("sub", "".join(_SUB[c] for c in s[i:j])))
            i = j
            continue
        if ch == "^":
            m = re.match(r"\^\{([^}]+)\}", s[i:]) or re.match(r"\^(-?[0-9n]+)", s[i:])
            if m:
                flush()
                runs.append(("sup", m.group(1)))
                i += m.end()
                continue
        if ch == "_" and buf and buf[-1].isalnum():
            m = re.match(r"_\{([^}]+)\}", s[i:]) or re.match(r"_(-?[0-9]+)", s[i:])
            if m:
                flush()
                runs.append(("sub", m.group(1)))
                i += m.end()
                continue
        buf.append(ch)
        i += 1
    flush()
    return runs


def rich_width(text, font_name: str, font_size: float) -> float:
    total = 0.0
    for kind, chunk in _rich_runs(text):
        fs = font_size * _SMALL if kind in ("sup", "sub") else font_size
        total += stringWidth(chunk, font_name, fs)
    return total


def draw_rich(canvas, x: float, y: float, text, font_name: str, font_size: float,
              color, align: str = "left") -> float:
    """Draw text with real super/subscripts. `align` is left|center|right.
    Returns the x position after the last glyph."""
    runs = _rich_runs(text)
    if align in ("center", "right"):
        total = rich_width(text, font_name, font_size)
        x = x - total / 2.0 if align == "center" else x - total
    if color is not None:
        canvas.setFillColor(color)
    for kind, chunk in runs:
        if kind == "sup":
            fs = font_size * _SMALL
            canvas.setFont(font_name, fs)
            canvas.drawString(x, y + font_size * _SUP_SHIFT, chunk)
            x += stringWidth(chunk, font_name, fs)
        elif kind == "sub":
            fs = font_size * _SMALL
            canvas.setFont(font_name, fs)
            canvas.drawString(x, y - font_size * _SUB_SHIFT, chunk)
            x += stringWidth(chunk, font_name, fs)
        else:
            canvas.setFont(font_name, font_size)
            canvas.drawString(x, y, chunk)
            x += stringWidth(chunk, font_name, font_size)
    return x


def rich_wrap(text, max_width: float, font_name: str, font_size: float,
              max_lines: int | None = None) -> list[str]:
    """Word-wrap text to `max_width`, measuring with rich (super/subscript-aware)
    widths and returning the ORIGINAL substrings (so draw_rich can render them)."""
    s = _normalize_sci(text).strip()
    if not s:
        return []
    words = s.split()
    lines: list[str] = []
    current = ""
    for word in words:
        trial = (current + " " + word).strip() if current else word
        if rich_width(trial, font_name, font_size) <= max_width:
            current = trial
        else:
            if current:
                lines.append(current)
            # Hard-break a single over-long word.
            if rich_width(word, font_name, font_size) > max_width:
                cut = word
                while cut and rich_width(cut + "\u2026", font_name, font_size) > max_width:
                    cut = cut[:-1]
                lines.append((cut + "\u2026") if cut else word[:1])
                current = ""
            else:
                current = word
    if current:
        lines.append(current)
    if max_lines and len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while last and rich_width(last + "\u2026", font_name, font_size) > max_width:
            last = last[:-1]
        lines[-1] = (last + "\u2026") if last else lines[-1]
    return lines

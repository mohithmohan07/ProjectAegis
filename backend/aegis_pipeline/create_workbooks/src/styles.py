"""Palette + ParagraphStyle definitions. Keep the typography calm and readable.

A4: 595.276 x 841.89 pt. Margin 18mm = 51.024 pt. Content width = 174mm.
"""
from __future__ import annotations

import os
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from config import DEFAULT_CONFIG

MARGIN = DEFAULT_CONFIG["margin_mm"] * mm
CONTENT_W = DEFAULT_CONFIG["content_width_mm"] * mm


# Register a Unicode-capable font so Sanskrit/Hindi diacritics (Ā, ī, ṣ, etc.)
# and other non-ASCII characters render correctly. We prefer DejaVu Sans
# (bundled with many Python installs); fall back to Windows Arial / Calibri.
def _register_unicode_font() -> tuple[str, str, str]:
    """Return (regular, bold, italic) PostScript names of a registered font."""
    candidates: list[tuple[str, str, str, str]] = []
    win_fonts = Path(r"C:\Windows\Fonts")
    if win_fonts.exists():
        candidates.append(("Arial", "arial.ttf", "arialbd.ttf", "ariali.ttf"))
        candidates.append(("Calibri", "calibri.ttf", "calibrib.ttf", "calibrii.ttf"))
        candidates.append(("Segoe UI", "segoeui.ttf", "segoeuib.ttf", "segoeuii.ttf"))
    for family, reg, bld, ita in candidates:
        try:
            base = win_fonts / reg
            if not base.exists():
                continue
            pdfmetrics.registerFont(TTFont(family, str(base)))
            bold_path = win_fonts / bld
            italic_path = win_fonts / ita
            bold_name = f"{family}-Bold" if bold_path.exists() else family
            italic_name = f"{family}-Italic" if italic_path.exists() else family
            if bold_path.exists():
                pdfmetrics.registerFont(TTFont(bold_name, str(bold_path)))
            if italic_path.exists():
                pdfmetrics.registerFont(TTFont(italic_name, str(italic_path)))
            from reportlab.pdfbase.pdfmetrics import registerFontFamily
            registerFontFamily(family, normal=family, bold=bold_name, italic=italic_name,
                                boldItalic=bold_name)
            return family, bold_name, italic_name
        except Exception:
            continue
    return "Helvetica", "Helvetica-Bold", "Helvetica-Oblique"


FONT_REGULAR, FONT_BOLD, FONT_ITALIC = _register_unicode_font()

PALETTE = {
    "ink": colors.HexColor("#1F2933"),
    "muted": colors.HexColor("#52606D"),
    "subhead_on_pale": colors.HexColor("#0B5566"),   # strong secondary text on mint/teal_pale boxes
    "subhead_on_dark": colors.HexColor("#E8F7F9"),   # secondary text on teal_dark / violet headers
    "text_on_gold": colors.HexColor("#1F2933"),      # badge label on gold TIP boxes
    "teal_dark": colors.HexColor("#0B5566"),
    "teal": colors.HexColor("#138896"),
    "teal_pale": colors.HexColor("#E8F4F5"),
    "mint": colors.HexColor("#F2FAFA"),
    "coral": colors.HexColor("#D9544A"),
    "coral_pale": colors.HexColor("#FCEDEB"),
    "gold": colors.HexColor("#C99A2E"),
    "gold_pale": colors.HexColor("#FBF3D6"),
    "violet": colors.HexColor("#5A4B8A"),
    "violet_pale": colors.HexColor("#EFEBF6"),
    "line": colors.HexColor("#C9D6D9"),
    "line_soft": colors.HexColor("#E1E8EA"),
    "white": colors.white,
}


def canvas_text_on_fill(fill) -> tuple:
    """Return (label_color, detail_color) for text drawn on a solid fill."""
    light_fills = {PALETTE["gold"], PALETTE["coral"]}
    if fill in light_fills:
        return PALETTE["ink"], PALETTE["teal_dark"]
    return PALETTE["white"], PALETTE["subhead_on_dark"]


def build_styles() -> dict:
    base = getSampleStyleSheet()
    ink = PALETTE["ink"]
    muted = PALETTE["muted"]
    teal_dark = PALETTE["teal_dark"]

    return {
        # Cover & section heads
        "chapter_meta": ParagraphStyle("chapter_meta", parent=base["Normal"],
                                        fontName=FONT_REGULAR, fontSize=9, textColor=muted, leading=11),
        "chapter_number": ParagraphStyle("chapter_number", parent=base["Normal"],
                                          fontName=FONT_BOLD, fontSize=10, textColor=PALETTE["teal"],
                                          spaceAfter=2),
        "chapter_title": ParagraphStyle("chapter_title", parent=base["Heading1"],
                                         fontName=FONT_BOLD, fontSize=24, leading=28,
                                         textColor=ink, spaceAfter=6),
        "chapter_summary": ParagraphStyle("chapter_summary", parent=base["Normal"],
                                           fontName=FONT_REGULAR, fontSize=10.5, leading=14,
                                           textColor=ink, alignment=TA_JUSTIFY, spaceAfter=8),

        "page_section": ParagraphStyle("page_section", parent=base["Normal"],
                                        fontName=FONT_BOLD, fontSize=13, textColor=teal_dark,
                                        spaceBefore=4, spaceAfter=6, keepWithNext=1),
        "page_section_label": ParagraphStyle("page_section_label", parent=base["Normal"],
                                              fontName=FONT_BOLD, fontSize=8, textColor=PALETTE["teal"],
                                              spaceAfter=2),

        # Section topic header
        "section_kicker": ParagraphStyle("section_kicker", parent=base["Normal"],
                                          fontName=FONT_BOLD, fontSize=8, textColor=PALETTE["white"],
                                          alignment=TA_LEFT, leading=10),
        "section_title": ParagraphStyle("section_title", parent=base["Normal"],
                                         fontName=FONT_BOLD, fontSize=14, textColor=PALETTE["white"],
                                         alignment=TA_LEFT, leading=17, wordWrap="LTR"),
        "section_overview": ParagraphStyle("section_overview", parent=base["Normal"],
                                            fontName=FONT_ITALIC, fontSize=10, leading=13,
                                            textColor=ink, alignment=TA_JUSTIFY, spaceAfter=6,
                                            wordWrap="LTR"),

        # Block heading
        "block_title": ParagraphStyle("block_title", parent=base["Normal"],
                                       fontName=FONT_BOLD, fontSize=10.5, textColor=teal_dark,
                                       spaceAfter=3, keepWithNext=1),

        # Body text
        "body": ParagraphStyle("body", parent=base["Normal"],
                                fontName=FONT_REGULAR, fontSize=9.5, leading=12.5,
                                textColor=ink, alignment=TA_JUSTIFY, spaceAfter=4),
        "body_small": ParagraphStyle("body_small", parent=base["Normal"],
                                      fontName=FONT_REGULAR, fontSize=8.5, leading=11,
                                      textColor=ink, alignment=TA_JUSTIFY),
        "bullet": ParagraphStyle("bullet", parent=base["Normal"],
                                  fontName=FONT_REGULAR, fontSize=9.5, leading=12.5,
                                  textColor=ink, leftIndent=12, bulletIndent=2,
                                  spaceAfter=2, alignment=TA_JUSTIFY),

        # Table cells
        "table_header": ParagraphStyle("table_header", parent=base["Normal"],
                                        fontName=FONT_BOLD, fontSize=8.5, leading=10.5,
                                        textColor=PALETTE["white"], alignment=TA_CENTER),
        "table_cell": ParagraphStyle("table_cell", parent=base["Normal"],
                                      fontName=FONT_REGULAR, fontSize=8.5, leading=10.5,
                                      textColor=ink, wordWrap="LTR"),
        "table_cell_bold": ParagraphStyle("table_cell_bold", parent=base["Normal"],
                                           fontName=FONT_BOLD, fontSize=8.5, leading=10.5,
                                           textColor=teal_dark),

        # Glossary cards
        "glossary_term": ParagraphStyle("glossary_term", parent=base["Normal"],
                                         fontName=FONT_BOLD, fontSize=8.5, leading=10,
                                         textColor=teal_dark, spaceAfter=2),
        "glossary_def": ParagraphStyle("glossary_def", parent=base["Normal"],
                                        fontName=FONT_REGULAR, fontSize=7.5, leading=9.5,
                                        textColor=ink),

        # Callouts & misc
        "callout_label": ParagraphStyle("callout_label", parent=base["Normal"],
                                         fontName=FONT_BOLD, fontSize=8, leading=10,
                                         textColor=PALETTE["white"], alignment=TA_CENTER),
        "callout_label_dark": ParagraphStyle("callout_label_dark", parent=base["Normal"],
                                              fontName=FONT_BOLD, fontSize=8, leading=10,
                                              textColor=PALETTE["text_on_gold"], alignment=TA_CENTER),
        "callout_text": ParagraphStyle("callout_text", parent=base["Normal"],
                                        fontName=FONT_REGULAR, fontSize=9, leading=12,
                                        textColor=ink, alignment=TA_LEFT),
        "callout_title": ParagraphStyle("callout_title", parent=base["Normal"],
                                         fontName=FONT_BOLD, fontSize=9, leading=11,
                                         textColor=teal_dark, spaceAfter=2),

        "muted_small": ParagraphStyle("muted_small", parent=base["Normal"],
                                       fontName=FONT_REGULAR, fontSize=8, leading=10,
                                       textColor=muted),

        # Worked example
        "we_label": ParagraphStyle("we_label", parent=base["Normal"],
                                    fontName=FONT_BOLD, fontSize=8.5, leading=10.5,
                                    textColor=PALETTE["violet"]),

        # Topic range subtitle
        "topic_range": ParagraphStyle("topic_range", parent=base["Normal"],
                                       fontName=FONT_ITALIC, fontSize=8.5, leading=10.5,
                                       textColor=PALETTE["subhead_on_dark"], alignment=TA_LEFT),

        # Secondary line inside tinted boxes (overview strip, callout body headings)
        "box_subhead": ParagraphStyle("box_subhead", parent=base["Normal"],
                                      fontName=FONT_BOLD, fontSize=9, leading=11,
                                      textColor=PALETTE["subhead_on_pale"], spaceAfter=2),

        # Excerpt block (verses/prose passages)
        "excerpt_text": ParagraphStyle("excerpt_text", parent=base["Normal"],
                                        fontName=FONT_ITALIC, fontSize=10, leading=13.5,
                                        textColor=ink, alignment=TA_LEFT, leftIndent=10,
                                        spaceAfter=4),
        "excerpt_verse": ParagraphStyle("excerpt_verse", parent=base["Normal"],
                                         fontName=FONT_ITALIC, fontSize=10, leading=14.5,
                                         textColor=ink, alignment=TA_LEFT, leftIndent=12,
                                         spaceAfter=4),
        "excerpt_ref": ParagraphStyle("excerpt_ref", parent=base["Normal"],
                                       fontName=FONT_ITALIC, fontSize=8, leading=10,
                                       textColor=teal_dark, alignment=TA_LEFT),
        "excerpt_explanation": ParagraphStyle("excerpt_explanation", parent=base["Normal"],
                                               fontName=FONT_REGULAR, fontSize=9, leading=12,
                                               textColor=ink, alignment=TA_JUSTIFY, spaceBefore=2),

        # Problem set
        "problem_set_kicker": ParagraphStyle("problem_set_kicker", parent=base["Normal"],
                                              fontName=FONT_BOLD, fontSize=8,
                                              textColor=PALETTE["white"], alignment=TA_LEFT, leading=10),
        "problem_set_title": ParagraphStyle("problem_set_title", parent=base["Normal"],
                                             fontName=FONT_BOLD, fontSize=11.5,
                                             textColor=PALETTE["white"], alignment=TA_LEFT, leading=14),
        "problem_set_approach": ParagraphStyle("problem_set_approach", parent=base["Normal"],
                                                fontName=FONT_REGULAR, fontSize=9, leading=12,
                                                textColor=ink, alignment=TA_JUSTIFY, spaceAfter=4),
        "problem_label": ParagraphStyle("problem_label", parent=base["Normal"],
                                         fontName=FONT_BOLD, fontSize=8.5, leading=10.5,
                                         textColor=PALETTE["violet"]),
        "problem_answer": ParagraphStyle("problem_answer", parent=base["Normal"],
                                          fontName=FONT_BOLD, fontSize=9.5, leading=12.5,
                                          textColor=PALETTE["teal_dark"], alignment=TA_LEFT,
                                          spaceBefore=2),
        "solution_line": ParagraphStyle("solution_line", parent=base["Normal"],
                                         fontName=FONT_REGULAR, fontSize=9.5, leading=13,
                                         textColor=ink, alignment=TA_LEFT, leftIndent=10,
                                         spaceAfter=1),
        "example_statement": ParagraphStyle("example_statement", parent=base["Normal"],
                                            fontName=FONT_REGULAR, fontSize=9.5, leading=13,
                                            textColor=ink, alignment=TA_LEFT, spaceAfter=2),

        # English: Think-and-Respond questions with model answers
        "qa_kind": ParagraphStyle("qa_kind", parent=base["Normal"],
                                  fontName=FONT_BOLD, fontSize=7, leading=8.5,
                                  textColor=PALETTE["white"], alignment=TA_CENTER),
        "qa_question": ParagraphStyle("qa_question", parent=base["Normal"],
                                      fontName=FONT_BOLD, fontSize=9.5, leading=12.5,
                                      textColor=PALETTE["ink"], alignment=TA_LEFT,
                                      spaceAfter=2),
        "qa_answer": ParagraphStyle("qa_answer", parent=base["Normal"],
                                    fontName=FONT_REGULAR, fontSize=9.5, leading=13,
                                    textColor=ink, alignment=TA_JUSTIFY, leftIndent=10,
                                    spaceAfter=1),

        # English: Prose/Poem part divider band
        "part_kicker": ParagraphStyle("part_kicker", parent=base["Normal"],
                                      fontName=FONT_BOLD, fontSize=9, textColor=PALETTE["white"],
                                      alignment=TA_LEFT, leading=11),
        "part_title": ParagraphStyle("part_title", parent=base["Normal"],
                                     fontName=FONT_BOLD, fontSize=16, textColor=PALETTE["white"],
                                     alignment=TA_LEFT, leading=19),

        # Chapter Map (structure-first overview): numbered topic cards + gist
        "map_badge": ParagraphStyle("map_badge", parent=base["Normal"],
                                    fontName=FONT_BOLD, fontSize=12, leading=14,
                                    textColor=PALETTE["white"], alignment=TA_CENTER),
        "map_title": ParagraphStyle("map_title", parent=base["Normal"],
                                    fontName=FONT_BOLD, fontSize=10, leading=12,
                                    textColor=teal_dark, spaceAfter=2, wordWrap="LTR"),
        "map_gist": ParagraphStyle("map_gist", parent=base["Normal"],
                                   fontName=FONT_REGULAR, fontSize=8.5, leading=10.5,
                                   textColor=ink, alignment=TA_LEFT, wordWrap="LTR"),
        "revision_cell": ParagraphStyle("revision_cell", parent=base["Normal"],
                                        fontName=FONT_REGULAR, fontSize=8, leading=10,
                                        textColor=ink, wordWrap="LTR"),
        "revision_period": ParagraphStyle("revision_period", parent=base["Normal"],
                                          fontName=FONT_ITALIC, fontSize=8.5, leading=10,
                                          textColor=muted, spaceAfter=2),
        "revision_event_body": ParagraphStyle("revision_event_body", parent=base["Normal"],
                                              fontName=FONT_BOLD, fontSize=9.5, leading=12.5,
                                              textColor=PALETTE["white"], alignment=TA_JUSTIFY,
                                              wordWrap="LTR"),
        "revision_subhead": ParagraphStyle("revision_subhead", parent=base["Normal"],
                                           fontName=FONT_BOLD, fontSize=9.5, leading=11,
                                           textColor=teal_dark, spaceBefore=2, spaceAfter=2),
        "revision_body": ParagraphStyle("revision_body", parent=base["Normal"],
                                        fontName=FONT_REGULAR, fontSize=9, leading=12,
                                        textColor=ink, alignment=TA_JUSTIFY, spaceAfter=2,
                                        wordWrap="LTR"),
        "map_arrow": ParagraphStyle("map_arrow", parent=base["Normal"],
                                    fontName=FONT_BOLD, fontSize=9, leading=10,
                                    textColor=PALETTE["teal"], alignment=TA_CENTER,
                                    spaceBefore=1, spaceAfter=1),
    }

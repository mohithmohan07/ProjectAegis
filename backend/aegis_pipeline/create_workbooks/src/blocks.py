"""Render each schema Block into ReportLab flowables.

Each renderer returns a list of flowables and is responsible for keeping its
output on a single A4 column. Long tables, problem-sets and bullet lists are
allowed to split across pages naturally.
"""
from __future__ import annotations

import re

from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import CondPageBreak, HRFlowable, KeepTogether, Paragraph, Spacer, Table, TableStyle

from diagrams import Cycle, Pyramid, Timeline, Tree, Venn
from flowchart import Flowchart
from mathtext import math_flatten, math_markup
from schema import Activity, Block, Chapter, EventRevisionItem, GlossaryItem, Topic
from styles import CONTENT_W, PALETTE
from reportlab.pdfbase.pdfmetrics import stringWidth
from styles import FONT_BOLD, FONT_REGULAR


def p(text: str, style) -> Paragraph:
    # math_markup escapes the text and turns plain-text maths (x², x₁, ^2, _1)
    # into proper <super>/<sub> markup, while preserving our <b>/<i>/<br/> markers.
    return Paragraph(math_markup(text), style)


# ========================================================================
# Chapter-level pieces
# ========================================================================

def chapter_cover(chapter: Chapter, styles) -> list:
    return [
        p(f"CHAPTER {int(chapter.chapter_number):02d}", styles["chapter_number"]),
        p(chapter.chapter_title, styles["chapter_title"]),
        p(f"{chapter.subject.upper()}  ·  {chapter.grade.upper()}", styles["chapter_meta"]),
        HRFlowable(width="100%", thickness=4, color=PALETTE["teal"], spaceAfter=10),
        p(chapter.summary, styles["chapter_summary"]) if chapter.summary else Spacer(0, 0),
    ]


def study_strategy_block(items: list[str], styles) -> list:
    if not items:
        return []
    rows = [[p(str(i + 1), styles["table_cell_bold"]), p(item, styles["body"])]
            for i, item in enumerate(items)]
    return [
        p("HOW TO USE THIS WORKBOOK", styles["page_section_label"]),
        p("Study Strategy", styles["page_section"]),
        _data_table(["#", "Strategy"], rows, styles, [12 * mm, CONTENT_W - 12 * mm]),
        Spacer(1, 8),
    ]


def _first_sentence(text: str, limit: int = 110) -> str:
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    if not t:
        return ""
    s = re.split(r"(?<=[.!?])\s+", t)[0].strip()
    if len(s) > limit:
        s = s[:limit].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"
    return s


def chapter_map_block(chapter: Chapter, styles) -> list:
    """Structure-first map: numbered topic cards with a one-line gist each."""
    topics = chapter.topics
    if not topics:
        return []
    badge_w = 11 * mm
    text_w = CONTENT_W - badge_w
    out: list = [
        p("THE BIG PICTURE", styles["page_section_label"]),
        p("Chapter Map", styles["page_section"]),
        Spacer(1, 4),
    ]
    n = len(topics)
    for i, t in enumerate(topics, start=1):
        gist = _first_sentence(t.overview, 110)
        title_p = p(t.title, styles["map_title"])
        gist_p = p(gist, styles["map_gist"]) if gist else Spacer(0, 0)
        if gist:
            card = Table(
                [
                    [p(f"{i:02d}", styles["map_badge"]), title_p],
                    ["", gist_p],
                ],
                colWidths=[badge_w, text_w],
            )
            card.setStyle(
                TableStyle(
                    [
                        ("SPAN", (0, 0), (0, 1)),
                        ("BACKGROUND", (0, 0), (0, 1), PALETTE["teal"]),
                        ("BACKGROUND", (1, 0), (1, 1), PALETTE["teal_pale"]),
                        ("VALIGN", (0, 0), (0, 1), "MIDDLE"),
                        ("ALIGN", (0, 0), (0, 1), "CENTER"),
                        ("VALIGN", (1, 0), (1, -1), "TOP"),
                        ("LEFTPADDING", (0, 0), (0, 1), 2),
                        ("RIGHTPADDING", (0, 0), (0, 1), 2),
                        ("LEFTPADDING", (1, 0), (1, -1), 8),
                        ("RIGHTPADDING", (1, 0), (1, -1), 8),
                        ("TOPPADDING", (0, 0), (-1, -1), 5),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                        ("BOX", (0, 0), (-1, -1), 0.4, PALETTE["line_soft"]),
                        ("LINEBELOW", (1, 0), (1, 0), 0.25, PALETTE["line_soft"]),
                    ]
                )
            )
        else:
            card = Table(
                [[p(f"{i:02d}", styles["map_badge"]), title_p]],
                colWidths=[badge_w, text_w],
            )
            card.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (0, 0), PALETTE["teal"]),
                        ("BACKGROUND", (1, 0), (1, 0), PALETTE["teal_pale"]),
                        ("VALIGN", (0, 0), (0, 0), "MIDDLE"),
                        ("ALIGN", (0, 0), (0, 0), "CENTER"),
                        ("VALIGN", (1, 0), (1, 0), "TOP"),
                        ("LEFTPADDING", (0, 0), (0, 0), 2),
                        ("RIGHTPADDING", (0, 0), (0, 0), 2),
                        ("LEFTPADDING", (1, 0), (1, 0), 8),
                        ("RIGHTPADDING", (1, 0), (1, 0), 8),
                        ("TOPPADDING", (0, 0), (-1, -1), 5),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                        ("BOX", (0, 0), (-1, -1), 0.4, PALETTE["line_soft"]),
                    ]
                )
            )
        out.append(card)
        if i < n:
            out.append(p("\u25bc", styles["map_arrow"]))
    out.append(Spacer(1, 8))
    return out


def _revision_event_box(text: str, styles) -> Table:
    band = Table(
        [
            [p("EVENT", styles["section_kicker"])],
            [p(text, styles["revision_event_body"])],
        ],
        colWidths=[CONTENT_W],
    )
    band.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), PALETTE["teal_dark"]),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (0, 0), 5),
                ("BOTTOMPADDING", (0, -1), (0, -1), 7),
                ("TOPPADDING", (0, 1), (0, 1), 0),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    return band


def event_revision_block(items: list[EventRevisionItem], styles) -> list:
    """Grouped revision blocks: causes explained, event highlighted, effects explained."""
    if not items:
        return []
    out: list = [
        p("REVISION", styles["page_section_label"]),
        p("Events at a Glance", styles["page_section"]),
        Spacer(1, 4),
    ]
    for idx, it in enumerate(items):
        if idx > 0:
            out.append(Spacer(1, 10))
        head = [p(it.title, styles["block_title"])]
        if it.period:
            head.append(p(it.period, styles["revision_period"]))
        out.append(KeepTogether(head))
        out.append(Spacer(1, 3))
        out.append(_revision_event_box(it.event, styles))
        out.append(Spacer(1, 4))
        if it.causes:
            out.append(p("Causes", styles["revision_subhead"]))
            out.append(p(it.causes, styles["revision_body"]))
        if it.effects:
            out.append(p("Effects", styles["revision_subhead"]))
            out.append(p(it.effects, styles["revision_body"]))
    out.append(Spacer(1, 8))
    return out


def contents_block(chapter: Chapter, styles) -> list:
    if not chapter.topics:
        return []
    # Topic name only — the source range is intentionally omitted here to keep
    # the contents list short and scannable.
    rows = []
    for i, t in enumerate(chapter.topics, start=1):
        rows.append([p(f"{i:02d}", styles["table_cell_bold"]), p(t.title, styles["table_cell"])])
    return [
        p("INSIDE THIS CHAPTER", styles["page_section_label"]),
        p("Contents", styles["page_section"]),
        _data_table(["#", "Topic"], rows, styles, [14 * mm, CONTENT_W - 14 * mm]),
        Spacer(1, 8),
    ]


def glossary_block(items: list[GlossaryItem], styles) -> list:
    if not items:
        return []
    story = [
        p("VOCABULARY YOU WILL MEET", styles["page_section_label"]),
        p("Glossary", styles["page_section"]),
    ]
    cols = 3
    col_w = (CONTENT_W - 6) / cols
    grid: list[list] = []
    for i in range(0, len(items), cols):
        row = []
        for it in items[i: i + cols]:
            inner = Table(
                [
                    [p(it.term, styles["glossary_term"])],
                    [p(it.definition, styles["glossary_def"])],
                ],
                colWidths=[col_w - 8],
            )
            inner.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, -1), PALETTE["mint"]),
                        ("BOX", (0, 0), (-1, -1), 0.4, PALETTE["line"]),
                        ("LINEBELOW", (0, 0), (-1, 0), 0.5, PALETTE["line"]),
                        ("LEFTPADDING", (0, 0), (-1, -1), 6),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                        ("TOPPADDING", (0, 0), (-1, -1), 4),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ]
                )
            )
            row.append(inner)
        while len(row) < cols:
            row.append("")
        grid.append(row)
    table = Table(grid, colWidths=[col_w] * cols, hAlign="LEFT", spaceAfter=8)
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.append(table)
    return story


# ========================================================================
# Topic
# ========================================================================

def topic_header(idx: int, topic: Topic, styles, subject: str = "") -> list:
    # Kicker + title (+ optional range) live in ONE cell so the banner is a
    # single table row. A multi-row banner could be split by ReportLab at a page
    # boundary, leaving a "TOPIC NN" widow that rendered the header twice.
    inner: list = [
        p(f"TOPIC {idx:02d}", styles["section_kicker"]),
        p(topic.title, styles["section_title"]),
    ]
    # The source "range" (from where to where) is only meaningful for English,
    # where the user wants the conceptual span called out. Elsewhere it reads as
    # noise, so it is suppressed.
    if topic.range and subject == "English":
        inner.append(p(topic.range, styles["topic_range"]))
    header = Table([[inner]], colWidths=[CONTENT_W])
    header.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), PALETTE["teal_dark"]),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    parts: list = [header, Spacer(1, 6)]
    if topic.overview:
        parts.append(_overview_strip(topic.overview, styles))
        parts.append(Spacer(1, 6))
    return parts


def render_block(block: Block, styles) -> list:
    if block.type == "paragraph":
        return _paragraph(block, styles)
    if block.type == "bullets":
        return _bullets(block, styles)
    if block.type == "table":
        return _table_block(block, styles)
    if block.type == "flowchart":
        return _flowchart_block(block, styles)
    if block.type == "definitions":
        return _definitions(block, styles)
    if block.type == "callout":
        return _callout(block, styles)
    if block.type == "worked_example":
        return _worked_example(block, styles)
    if block.type == "problem_set":
        return _problem_set(block, styles)
    if block.type == "excerpt":
        return _excerpt(block, styles)
    if block.type == "qa":
        return _qa(block, styles)
    if block.type == "timeline":
        return _diagram(block, styles, Timeline(block.data.get("events", [])))
    if block.type == "pyramid":
        return _diagram(block, styles,
                        Pyramid(block.data.get("levels", []),
                                direction=block.data.get("direction", "up")))
    if block.type == "venn":
        return _diagram(block, styles, Venn(block.data), framed=False)
    if block.type == "cycle":
        return _diagram(block, styles, Cycle(block.data.get("steps", [])), tint="mint")
    if block.type == "tree":
        return _diagram(block, styles, Tree(block.data.get("root", {})), framed=False)
    return []


# ========================================================================
# Individual block renderers
# ========================================================================

def _paragraph(block: Block, styles) -> list:
    out: list = []
    if block.title:
        out.append(p(block.title, styles["block_title"]))
    text = block.data.get("text", "")
    paragraphs = [t.strip() for t in text.split("\n\n") if t.strip()] or [text]
    for para in paragraphs:
        out.append(p(para, styles["body"]))
    out.append(Spacer(1, 4))
    return out


def _bullets(block: Block, styles) -> list:
    items = block.data.get("items", [])
    if not items:
        return []
    out: list = []
    if block.title:
        out.append(p(block.title, styles["block_title"]))
    for item in items:
        out.append(Paragraph("<bullet>•</bullet> " + math_markup(item), styles["bullet"]))
    out.append(Spacer(1, 4))
    return out


def _table_block(block: Block, styles) -> list:
    cols = block.data.get("columns", [])
    rows = block.data.get("rows", [])
    if not cols or not rows:
        return []
    out: list = []
    if block.title:
        out.append(p(block.title, styles["block_title"]))
    header = [p(c, styles["table_header"]) for c in cols]
    body_rows = []
    str_rows = []
    for row in rows:
        normalised = [str(c) for c in (list(row) + [""] * len(cols))[: len(cols)]]
        str_rows.append(normalised)
        body_rows.append([p(cell, styles["table_cell"]) for cell in normalised])
    col_widths = _natural_col_widths([str(c) for c in cols], str_rows)
    out.append(_data_table_raw([header] + body_rows, col_widths))
    out.append(Spacer(1, 4))
    return out


def _flowchart_block(block: Block, styles) -> list:
    steps = block.data.get("steps", [])
    if len(steps) < 2:
        return []
    orientation = block.data.get("orientation", "vertical")
    flow = Flowchart(steps, orientation=orientation)
    wrap = Table([[flow]], colWidths=[CONTENT_W])
    wrap.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), PALETTE["mint"]),
                ("BOX", (0, 0), (-1, -1), 0.4, PALETTE["line_soft"]),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ]
        )
    )
    out: list = []
    if block.title:
        out.append(p(block.title, styles["block_title"]))
    out.append(wrap)
    out.append(Spacer(1, 4))
    return out


def _definitions(block: Block, styles) -> list:
    items = block.data.get("items", [])
    if not items:
        return []
    rows = [
        [p(it.get("term", ""), styles["table_cell_bold"]),
         p(it.get("definition", ""), styles["table_cell"])]
        for it in items
    ]
    out: list = []
    if block.title:
        out.append(p(block.title, styles["block_title"]))
    out.append(_data_table(["Term", "Definition"], rows, styles,
                            [40 * mm, CONTENT_W - 40 * mm]))
    out.append(Spacer(1, 4))
    return out


def _callout(block: Block, styles) -> list:
    tone = block.data.get("tone", block.data.get("style", "note"))
    palette_map = {
        "exam_alert": ("EXAM ALERT", PALETTE["coral"], PALETTE["coral_pale"]),
        "warning": ("WARNING", PALETTE["coral"], PALETTE["coral_pale"]),
        "tip": ("TIP", PALETTE["gold"], PALETTE["gold_pale"]),
        "note": ("NOTE", PALETTE["teal"], PALETTE["teal_pale"]),
        "example": ("EXAMPLE", PALETTE["violet"], PALETTE["violet_pale"]),
        "formula": ("FORMULA", PALETTE["violet"], PALETTE["violet_pale"]),
    }
    label, accent, bg = palette_map.get(tone, palette_map["note"])
    label_style = styles["callout_label_dark"] if tone == "tip" else styles["callout_label"]
    badge = Table([[p(label, label_style)]], colWidths=[24 * mm])
    badge.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), accent),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    body_cells = []
    if block.title:
        body_cells.append([p(block.title, styles["callout_title"])])
    body_cells.append([p(block.data.get("text", ""), styles["callout_text"])])
    body = Table(body_cells, colWidths=[CONTENT_W - 28 * mm])
    body.setStyle(
        TableStyle(
            [
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    outer = Table([[badge, body]], colWidths=[28 * mm, CONTENT_W - 28 * mm])
    outer.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (1, 0), (1, 0), bg),
                ("BACKGROUND", (0, 0), (0, 0), bg),
                ("LINEABOVE", (0, 0), (-1, -1), 0.5, accent),
                ("LINEBELOW", (0, 0), (-1, -1), 0.5, accent),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    return [outer, Spacer(1, 5)]


def _solution_panel(idx: int | None, statement: str, steps: list, answer: str, styles) -> Table:
    """One worked example: statement, then the solution as plain lines (no
    'STEP N' labels), then the answer. Grouped in a left-bordered panel."""
    inner: list = []
    label = f"<b>Example {idx}.</b> " if idx else ""
    if statement:
        inner.append(p(f"{label}{statement}", styles["example_statement"]))
    elif label:
        inner.append(p(label, styles["example_statement"]))
    for step in steps:
        inner.append(p(step, styles["solution_line"]))
    if answer:
        inner.append(p(f"<b>Answer:</b> {answer}", styles["problem_answer"]))
    panel = Table([[inner]], colWidths=[CONTENT_W])
    panel.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), PALETTE["violet_pale"]),
                ("LINEBEFORE", (0, 0), (0, -1), 2.2, PALETTE["violet"]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    return panel


def _approach_flow(approach_steps: list, styles) -> list:
    """Render an approach as a (preferably horizontal) flowchart strip."""
    steps = []
    for s in approach_steps:
        if isinstance(s, dict):
            steps.append({"label": s.get("label", ""), "detail": s.get("detail", "")})
        elif str(s).strip():
            steps.append({"label": str(s), "detail": ""})
    if len(steps) < 2:
        return []
    orientation = "horizontal" if len(steps) <= 4 else "vertical"
    flow = Flowchart(steps, orientation=orientation)
    wrap = Table([[flow]], colWidths=[CONTENT_W])
    wrap.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), PALETTE["mint"]),
                ("BOX", (0, 0), (-1, -1), 0.4, PALETTE["line_soft"]),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ]
        )
    )
    return [p("How to approach", styles["block_title"]), wrap, Spacer(1, 4)]


def _worked_example(block: Block, styles) -> list:
    statement = block.data.get("statement", "")
    steps = block.data.get("steps", [])
    answer = block.data.get("answer", "")
    if not (statement or steps or answer):
        return []
    out: list = []
    if block.title:
        out.append(p(block.title, styles["block_title"]))
    out.append(_solution_panel(None, statement, steps, answer, styles))
    out.append(Spacer(1, 5))
    return out


def _problem_set(block: Block, styles) -> list:
    problems = block.data.get("problems", [])
    if not problems:
        return []
    type_name = block.data.get("type_name") or block.title

    out: list = []
    # Clean type heading (the type name IS the heading — no "Exercise 1.1").
    banner = Table([[p(type_name, styles["problem_set_title"])]], colWidths=[CONTENT_W])
    banner.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), PALETTE["violet"]),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    out.append(banner)
    out.append(Spacer(1, 5))

    # Approach flowchart for the type (how to tackle any problem of this kind).
    approach_steps = block.data.get("approach_steps") or []
    out.extend(_approach_flow(approach_steps, styles))

    for i, prob in enumerate(problems, start=1):
        out.append(_solution_panel(i, prob.get("statement", ""),
                                   prob.get("steps", []), prob.get("answer", ""), styles))
        out.append(Spacer(1, 4))
    return out


def _excerpt(block: Block, styles) -> list:
    text = block.data.get("text", "")
    if not text:
        return []
    reference = block.data.get("reference", "")
    kind = block.data.get("kind", "prose")
    explanation = block.data.get("explanation", "")

    cells = []
    if block.title:
        cells.append([p(block.title, styles["block_title"])])
    style = styles["excerpt_verse"] if kind == "verse" else styles["excerpt_text"]
    cells.append([p(text, style)])
    if reference:
        cells.append([p(f"— {reference}", styles["excerpt_ref"])])
    if explanation:
        cells.append([p(explanation, styles["excerpt_explanation"])])

    outer = Table(cells, colWidths=[CONTENT_W])
    outer.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), PALETTE["gold_pale"]),
                ("LINEBEFORE", (0, 0), (0, -1), 2.5, PALETTE["gold"]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    return [outer, Spacer(1, 5)]


_QA_KIND_COLOR = {
    "critical": "coral",
    "analytical": "teal",
    "creative": "violet",
}


def _qa(block: Block, styles) -> list:
    items = block.data.get("items", [])
    items = [it for it in items if isinstance(it, dict) and it.get("question")]
    if not items:
        return []
    out: list = [p(block.title or "Think and Respond", styles["block_title"])]
    for it in items:
        kind = str(it.get("kind", "")).strip().lower()
        accent = _QA_KIND_COLOR.get(kind, "teal")
        label = kind.upper() if kind else "QUESTION"
        pill = Table([[p(label, styles["qa_kind"])]], colWidths=[20 * mm])
        pill.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), PALETTE[accent]),
                    ("LEFTPADDING", (0, 0), (-1, -1), 3),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                    ("TOPPADDING", (0, 0), (-1, -1), 2),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ]
            )
        )
        q_row = Table(
            [[pill, p(it.get("question", ""), styles["qa_question"])]],
            colWidths=[22 * mm, CONTENT_W - 22 * mm],
        )
        q_row.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (0, 0), 0),
                    ("LEFTPADDING", (1, 0), (1, 0), 6),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 1),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
                ]
            )
        )
        cells = [[q_row]]
        answer = it.get("answer", "")
        if answer:
            cells.append([p(f"<b>Answer:</b> {answer}", styles["qa_answer"])])
        panel = Table(cells, colWidths=[CONTENT_W])
        panel.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), PALETTE["mint"]),
                    ("LINEBEFORE", (0, 0), (0, -1), 2.0, PALETTE[accent]),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        out.append(KeepTogether([panel, Spacer(1, 4)]))
    out.append(Spacer(1, 2))
    return out


def part_divider_block(part: str, styles) -> list:
    """A bold banner that separates the Prose and Poem halves of a Unit."""
    part = (part or "").strip()
    if not part:
        return []
    # Split "Prose — Title" / "Poem — Title" into a kicker + title.
    m = re.split(r"\s[—–-]\s", part, maxsplit=1)
    kicker = m[0].strip().upper() if m else part.upper()
    title = m[1].strip() if len(m) > 1 else ""
    accent = "violet" if kicker.startswith("POEM") else "teal_dark"
    rows = [[p(kicker, styles["part_kicker"])]]
    if title:
        rows.append([p(title, styles["part_title"])])
    band = Table(rows, colWidths=[CONTENT_W])
    band.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), PALETTE[accent]),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                ("TOPPADDING", (0, 0), (0, 0), 9),
                ("BOTTOMPADDING", (0, -1), (0, -1), 10),
                ("TOPPADDING", (0, 1), (0, 1), 0),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    return [Spacer(1, 4), band, Spacer(1, 8)]


def _diagram(block: Block, styles, flowable, *, framed: bool = True,
             tint: str = "mint") -> list:
    """Wrap a custom diagram flowable with an optional title and soft frame."""
    out: list = []
    if block.title:
        out.append(p(block.title, styles["block_title"]))
    if framed:
        wrap = Table([[flowable]], colWidths=[CONTENT_W])
        wrap.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), PALETTE[tint]),
                    ("BOX", (0, 0), (-1, -1), 0.4, PALETTE["line_soft"]),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                    ("TOPPADDING", (0, 0), (-1, -1), 8),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ]
            )
        )
        out.append(wrap)
    else:
        out.append(flowable)
    out.append(Spacer(1, 6))
    return out


# ========================================================================
# Activity (now rendered inline within a topic)
# ========================================================================

def chapter_mindmap_block(chapter: Chapter, styles) -> list:
    """Integrated chapter overview — rendered as one or more compact tree diagrams."""
    raw = chapter.chapter_mindmap or {}
    root = raw.get("root") if isinstance(raw, dict) else None
    if not root:
        return []

    def _count_nodes(node: dict) -> int:
        total = 1
        for ch in node.get("children") or []:
            total += _count_nodes(ch)
        return total

    header = [
        p("BIG PICTURE", styles["page_section_label"]),
        p("Chapter Mind Map", styles["page_section"]),
        Spacer(1, 6),
    ]
    children = root.get("children") or []
    if _count_nodes(root) <= 22 and len(children) <= 5:
        return header + _diagram(
            Block("tree", "", {"root": root}),
            styles,
            Tree(root, max_depth=5, compact=True),
            framed=False,
        ) + [Spacer(1, 8)]

    # Large maps: one compact subtree per top-level branch so nothing overflows a page.
    out = header
    root_label = root.get("label", chapter.chapter_title)
    root_detail = root.get("detail", "")
    banner_rows = [[p(root_label, styles["block_title"])]]
    if root_detail:
        banner_rows.append([p(root_detail, styles["body_small"])])
    banner = Table(banner_rows, colWidths=[CONTENT_W])
    banner.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), PALETTE["teal_dark"]),
                ("TEXTCOLOR", (0, 0), (-1, -1), PALETTE["white"]),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    out.append(banner)
    out.append(Spacer(1, 6))
    for branch in children:
        if not isinstance(branch, dict) or not branch.get("label"):
            continue
        out.append(CondPageBreak(55 * mm))
        sub_root = {
            "label": branch.get("label", ""),
            "detail": branch.get("detail", ""),
            "children": branch.get("children") or [],
        }
        out.extend(
            _diagram(
                Block("tree", "", {"root": sub_root}),
                styles,
                Tree(sub_root, max_depth=4, compact=True),
                framed=False,
            )
        )
    out.append(Spacer(1, 8))
    return out


def render_activity(activity: Activity, styles) -> list:
    if not activity.title:
        return []
    header_rows = [[p("ACTIVITY", styles["section_kicker"])],
                   [p(activity.title, styles["section_title"])]]
    header = Table(header_rows, colWidths=[CONTENT_W])
    header.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), PALETTE["coral"]),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, -1), (-1, -1), 4),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    parts: list = [header, Spacer(1, 4)]
    if activity.aim:
        parts.append(p(f"<b>Aim:</b> {activity.aim}", styles["body"]))
    if activity.materials:
        parts.append(p(f"<b>Materials:</b> {', '.join(activity.materials)}", styles["body_small"]))
    if activity.procedure:
        rows = []
        for row in activity.procedure:
            detail = row.get("detail", "")
            why = str(row.get("why", "")).strip()
            why = re.sub(r"^\*\*Why this step:\*\*\s*", "", why, flags=re.I)
            why = re.sub(r"^Why this step:\s*", "", why, flags=re.I)
            rows.append([
                p(row.get("step", ""), styles["table_cell_bold"]),
                p(detail, styles["table_cell"]),
            ])
            if why:
                rows.append([
                    p("", styles["table_cell"]),
                    p(f"<b>Why this step:</b> {why}", styles["table_cell"]),
                ])
        col_w = [12 * mm, CONTENT_W - 12 * mm]
        parts.append(Spacer(1, 3))
        parts.append(_data_table(["Step", "What to do"], rows, styles, col_w))
    if activity.observation:
        parts.append(Spacer(1, 3))
        parts.append(p(f"<b>Observation:</b> {activity.observation}", styles["body"]))
    if activity.inference:
        parts.append(p(f"<b>Inference:</b> {activity.inference}", styles["body"]))
    parts.append(Spacer(1, 8))
    return parts


def quick_recap_block(items: list[str], styles) -> list:
    if not items:
        return []
    rows = [[p(str(i + 1), styles["table_cell_bold"]), p(item, styles["body"])]
            for i, item in enumerate(items)]
    return [
        p("BEFORE THE EXAM", styles["page_section_label"]),
        p("Quick Recap", styles["page_section"]),
        _data_table(["#", "Key fact"], rows, styles, [10 * mm, CONTENT_W - 10 * mm]),
    ]


# ========================================================================
# Helpers
# ========================================================================

def _overview_strip(text: str, styles) -> Table:
    table = Table([[p(text, styles["section_overview"])]], colWidths=[CONTENT_W])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), PALETTE["mint"]),
                ("LINEBEFORE", (0, 0), (0, -1), 2.5, PALETTE["teal"]),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    return table


def _equal_widths(n: int) -> list:
    return [CONTENT_W / n] * n


def _natural_col_widths(cols: list[str], str_rows: list[list[str]], *,
                        min_w: float = 14 * mm, max_w: float = 80 * mm,
                        pad: float = 11) -> list:
    """Size columns to their content so short tables stay compact instead of
    being stretched across the whole page (which left big empty gaps)."""
    n = len(cols)
    widths = []
    for j in range(n):
        header_w = stringWidth(math_flatten(cols[j]), FONT_BOLD, 8.5)
        cell_w = 0.0
        for row in str_rows:
            cell = row[j] if j < len(row) else ""
            cell_w = max(cell_w, stringWidth(math_flatten(cell), FONT_REGULAR, 8.5))
        col = max(header_w, cell_w) + pad
        widths.append(max(min_w, min(col, max_w)))
    total = sum(widths)
    if total > CONTENT_W:
        scale = CONTENT_W / total
        widths = [w * scale for w in widths]
    return widths


def _data_table(headers: list, rows: list, styles, col_widths: list | None = None) -> Table:
    header_row = [p(str(h), styles["table_header"]) for h in headers]
    body = []
    for row in rows:
        if row and isinstance(row[0], Paragraph):
            body.append(row)
        else:
            body.append([p(str(cell), styles["table_cell"]) for cell in row])
    return _data_table_raw([header_row] + body, col_widths or _equal_widths(len(headers)))


def _data_table_raw(data: list[list], col_widths: list) -> Table:
    table = Table(data, colWidths=col_widths, repeatRows=1, splitByRow=True, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), PALETTE["teal"]),
                ("TEXTCOLOR", (0, 0), (-1, 0), PALETTE["white"]),
                ("LINEBELOW", (0, 0), (-1, 0), 0.7, PALETTE["teal_dark"]),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [PALETTE["mint"], PALETTE["white"]]),
                ("BOX", (0, 0), (-1, -1), 0.4, PALETTE["line"]),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, PALETTE["line_soft"]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table

"""Assemble a Chapter into a paginated A4 PDF."""
from __future__ import annotations

from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import CondPageBreak, KeepTogether, PageBreak, SimpleDocTemplate, Spacer

import blocks
from schema import Chapter
from styles import FONT_REGULAR, MARGIN, PALETTE, build_styles


class WorkbookDocument:
    def __init__(self, config: dict) -> None:
        self.config = config
        self.styles = build_styles()
        self.margin = config.get("margin_mm", 18) * mm

    def build(self, chapter: Chapter, output_pdf: str) -> None:
        path = Path(output_pdf)
        path.parent.mkdir(parents=True, exist_ok=True)
        doc = SimpleDocTemplate(
            str(path),
            pagesize=A4,
            leftMargin=self.margin,
            rightMargin=self.margin,
            topMargin=self.margin,
            bottomMargin=self.margin,
            title=chapter.chapter_title,
            author="Workbook Generator",
        )
        doc.build(self._story(chapter), onFirstPage=self._draw_decoration, onLaterPages=self._draw_decoration)

    def _story(self, chapter: Chapter) -> list:
        story: list = []
        story.extend(blocks.chapter_cover(chapter, self.styles))
        story.extend(blocks.study_strategy_block(chapter.study_strategy, self.styles))
        # Social Science learns best structure-first: a Chapter Map (topics + gist
        # in order) replaces the plain contents list so the student builds the
        # whole picture before studying each topic in depth.
        if chapter.subject == "Social Science":
            story.extend(blocks.chapter_map_block(chapter, self.styles))
        else:
            story.extend(blocks.contents_block(chapter, self.styles))
        story.append(PageBreak())

        story.extend(blocks.glossary_block(chapter.glossary, self.styles))
        story.append(PageBreak())

        # English Units can hold more than one piece (a prose + a poem). When
        # there are ≥2 distinct parts, divide them with a bold banner. This is an
        # ENGLISH-only device: other subjects occasionally emit stray `part`
        # values (e.g. a Social Science topic tagged "Topic 01"), which must
        # never be turned into divider banners stacked above the topic header.
        distinct_parts = [pt for pt in dict.fromkeys(
            (t.part or "").strip() for t in chapter.topics) if pt]
        use_part_dividers = chapter.subject == "English" and len(distinct_parts) >= 2
        current_part = None

        for idx, topic in enumerate(chapter.topics, start=1):
            # Continuous flow (no forced page break per topic) so short topics
            # don't leave half-empty pages. We keep the header + overview
            # together and nudge to a new page only if very little room remains,
            # which prevents an orphaned topic header at the foot of a page.
            if use_part_dividers and (topic.part or "").strip() != current_part:
                current_part = (topic.part or "").strip()
                if current_part:
                    story.append(CondPageBreak(90 * mm))
                    story.extend(blocks.part_divider_block(current_part, self.styles))
            elif idx > 1:
                story.append(Spacer(1, 12))
            story.append(CondPageBreak(70 * mm))
            header_flow = blocks.topic_header(idx, topic, self.styles, chapter.subject)
            story.append(KeepTogether(header_flow))
            for block in topic.blocks:
                story.extend(blocks.render_block(block, self.styles))
            for activity in topic.activities:
                story.extend(blocks.render_activity(activity, self.styles))

        if chapter.subject == "Social Science" and chapter.event_revision:
            story.append(CondPageBreak(80 * mm))
            story.extend(blocks.event_revision_block(chapter.event_revision, self.styles))

        if chapter.quick_recap:
            story.append(CondPageBreak(60 * mm))
            story.extend(blocks.quick_recap_block(chapter.quick_recap, self.styles))

        return story

    def _draw_decoration(self, canvas, doc) -> None:
        canvas.saveState()
        # Top accent bar
        canvas.setFillColor(PALETTE["teal"])
        canvas.rect(0, A4[1] - 6, A4[0], 6, stroke=0, fill=1)
        # Footer: chapter title only — NO page numbers. This workbook is meant
        # to be merged into a larger compilation, so page numbers (and anything
        # else position-dependent) are intentionally omitted.
        canvas.setFillColor(PALETTE["muted"])
        canvas.setFont(FONT_REGULAR, 7.5)
        canvas.drawString(self.margin, 10 * mm, doc.title)
        canvas.setStrokeColor(PALETTE["line_soft"])
        canvas.setLineWidth(0.4)
        canvas.line(self.margin, 12 * mm, A4[0] - self.margin, 12 * mm)
        canvas.restoreState()

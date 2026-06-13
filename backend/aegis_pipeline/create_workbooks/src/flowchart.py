"""Custom ReportLab Flowable that draws a clean flowchart.

Supports two orientations:
  • "vertical"   – boxes stacked top→down with downward arrows (default; good
                   for longer procedures).
  • "horizontal" – boxes left→right with rightward arrows (good for a short
                   "how to approach this" strip, e.g. above a problem type).

Each step is a rounded rectangle with a small "STEP N" badge, a bold label and
an optional detail line. Maths in labels/details is flattened (x₁ → x1,
x² → x^2) so nothing renders as a tofu box on the canvas.
"""
from __future__ import annotations

from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.platypus import Flowable

from mathtext import draw_rich, rich_wrap
from styles import CONTENT_W, PALETTE, FONT_REGULAR, FONT_BOLD


class Flowchart(Flowable):
    BOX_RADIUS = 2.6 * mm
    PADDING_X = 5 * mm
    PADDING_Y = 3 * mm
    GAP = 5 * mm
    GAP_H = 7 * mm
    BADGE_W = 14 * mm
    BADGE_H = 4.6 * mm
    LABEL_FONT = (FONT_BOLD, 9.5)
    DETAIL_FONT = (FONT_REGULAR, 8.0)
    BADGE_FONT = (FONT_BOLD, 6.5)
    LABEL_LEADING = 12.0
    DETAIL_LEADING = 10.0
    LABEL_DETAIL_GAP = 2.0 * mm
    BADGE_LABEL_GAP = 2.0 * mm
    BOX_WIDTH_RATIO = 0.78

    def __init__(self, steps: list[dict[str, str]], width: float = CONTENT_W,
                 orientation: str = "vertical") -> None:
        super().__init__()
        self.steps = [
            {"label": s.get("label", ""), "detail": s.get("detail", "")}
            for s in steps[:6]
        ]
        self.orientation = "horizontal" if orientation == "horizontal" and len(self.steps) <= 4 else "vertical"
        self.width = width
        self._compute_layout()

    def wrap(self, available_w: float, available_h: float):
        target = min(available_w, CONTENT_W)
        if abs(target - self.width) > 0.5:
            self.width = target
            self._compute_layout()
        return self.width, self.height

    def _wrap_text(self, text: str, max_width: float, font_name: str, font_size: float) -> list[str]:
        return rich_wrap(text, max_width, font_name, font_size)

    # ---- layout dispatch ----

    def _compute_layout(self) -> None:
        if self.orientation == "horizontal":
            self._compute_horizontal()
        else:
            self._compute_vertical()

    def draw(self) -> None:
        if self.orientation == "horizontal":
            self._draw_horizontal()
        else:
            self._draw_vertical()

    # ---- vertical ----

    def _compute_vertical(self) -> None:
        self.box_width = self.width * self.BOX_WIDTH_RATIO
        text_width = self.box_width - 2 * self.PADDING_X
        self._wrapped: list[tuple[list[str], list[str]]] = []
        self.box_heights: list[float] = []
        for step in self.steps:
            label_lines = self._wrap_text(step.get("label", ""), text_width, *self.LABEL_FONT)[:2]
            detail_lines = self._wrap_text(step.get("detail", ""), text_width, *self.DETAIL_FONT)[:2]
            inner = self.BADGE_H + self.BADGE_LABEL_GAP + len(label_lines) * self.LABEL_LEADING
            if detail_lines:
                inner += self.LABEL_DETAIL_GAP + len(detail_lines) * self.DETAIL_LEADING
            box_h = inner + 2 * self.PADDING_Y
            self.box_heights.append(box_h)
            self._wrapped.append((label_lines, detail_lines))
        self.height = sum(self.box_heights) + self.GAP * max(0, len(self.steps) - 1)

    def _draw_vertical(self) -> None:
        c = self.canv
        cx = self.width / 2
        x_box = (self.width - self.box_width) / 2
        box_top = self.height
        for i, (step, bh, (label_lines, detail_lines)) in enumerate(
            zip(self.steps, self.box_heights, self._wrapped)
        ):
            box_bottom = box_top - bh
            c.setFillColor(PALETTE["teal_pale"])
            c.setStrokeColor(PALETTE["teal"])
            c.setLineWidth(0.8)
            c.roundRect(x_box, box_bottom, self.box_width, bh, self.BOX_RADIUS, fill=1, stroke=1)
            cursor = box_top - self.PADDING_Y
            self._badge(c, x_box + self.PADDING_X / 2, cursor - self.BADGE_H, i + 1)
            cursor = cursor - self.BADGE_H - self.BADGE_LABEL_GAP
            for line in label_lines:
                cursor -= self.LABEL_LEADING
                draw_rich(c, cx, cursor + 3, line, self.LABEL_FONT[0], self.LABEL_FONT[1],
                          PALETTE["ink"], align="center")
            if detail_lines:
                cursor -= self.LABEL_DETAIL_GAP
                for line in detail_lines:
                    cursor -= self.DETAIL_LEADING
                    draw_rich(c, cx, cursor + 2.5, line, self.DETAIL_FONT[0], self.DETAIL_FONT[1],
                              PALETTE["subhead_on_pale"], align="center")
            if i < len(self.steps) - 1:
                next_top = box_bottom - self.GAP
                c.setStrokeColor(PALETTE["teal"])
                c.setLineWidth(1.0)
                c.line(cx, box_bottom, cx, next_top + 2.2 * mm)
                c.setFillColor(PALETTE["teal"])
                pth = c.beginPath()
                pth.moveTo(cx, next_top)
                pth.lineTo(cx - 1.7 * mm, next_top + 2.4 * mm)
                pth.lineTo(cx + 1.7 * mm, next_top + 2.4 * mm)
                pth.close()
                c.drawPath(pth, fill=1, stroke=0)
            box_top -= bh + self.GAP

    # ---- horizontal ----

    def _compute_horizontal(self) -> None:
        n = max(1, len(self.steps))
        self.box_width = (self.width - self.GAP_H * (n - 1)) / n
        text_width = self.box_width - 2 * 3 * mm
        self._wrapped = []
        max_h = 0.0
        for step in self.steps:
            label_lines = self._wrap_text(step.get("label", ""), text_width, *self.LABEL_FONT)[:3]
            detail_lines = self._wrap_text(step.get("detail", ""), text_width, *self.DETAIL_FONT)[:3]
            inner = self.BADGE_H + self.BADGE_LABEL_GAP + len(label_lines) * self.LABEL_LEADING
            if detail_lines:
                inner += self.LABEL_DETAIL_GAP + len(detail_lines) * self.DETAIL_LEADING
            self._wrapped.append((label_lines, detail_lines))
            max_h = max(max_h, inner + 2 * self.PADDING_Y)
        self.box_height = max_h
        self.height = max_h

    def _draw_horizontal(self) -> None:
        c = self.canv
        n = len(self.steps)
        bh = self.box_height
        for i, (label_lines, detail_lines) in enumerate(self._wrapped):
            x_box = i * (self.box_width + self.GAP_H)
            cx = x_box + self.box_width / 2
            c.setFillColor(PALETTE["teal_pale"])
            c.setStrokeColor(PALETTE["teal"])
            c.setLineWidth(0.8)
            c.roundRect(x_box, 0, self.box_width, bh, self.BOX_RADIUS, fill=1, stroke=1)
            cursor = bh - self.PADDING_Y
            self._badge(c, x_box + 3 * mm / 2, cursor - self.BADGE_H, i + 1)
            cursor = cursor - self.BADGE_H - self.BADGE_LABEL_GAP
            for line in label_lines:
                cursor -= self.LABEL_LEADING
                draw_rich(c, cx, cursor + 3, line, self.LABEL_FONT[0], self.LABEL_FONT[1],
                          PALETTE["ink"], align="center")
            if detail_lines:
                cursor -= self.LABEL_DETAIL_GAP
                for line in detail_lines:
                    cursor -= self.DETAIL_LEADING
                    draw_rich(c, cx, cursor + 2.5, line, self.DETAIL_FONT[0], self.DETAIL_FONT[1],
                              PALETTE["subhead_on_pale"], align="center")
            if i < n - 1:
                ax = x_box + self.box_width
                ay = bh / 2
                c.setStrokeColor(PALETTE["teal"])
                c.setLineWidth(1.0)
                c.line(ax, ay, ax + self.GAP_H - 2.2 * mm, ay)
                c.setFillColor(PALETTE["teal"])
                pth = c.beginPath()
                pth.moveTo(ax + self.GAP_H, ay)
                pth.lineTo(ax + self.GAP_H - 2.4 * mm, ay + 1.7 * mm)
                pth.lineTo(ax + self.GAP_H - 2.4 * mm, ay - 1.7 * mm)
                pth.close()
                c.drawPath(pth, fill=1, stroke=0)

    # ---- shared ----

    def _badge(self, c, x: float, y: float, n: int) -> None:
        c.setFillColor(PALETTE["teal"])
        c.setStrokeColor(PALETTE["teal"])
        c.roundRect(x, y, self.BADGE_W, self.BADGE_H, 1.4 * mm, fill=1, stroke=0)
        c.setFillColor(PALETTE["white"])
        c.setFont(*self.BADGE_FONT)
        c.drawCentredString(x + self.BADGE_W / 2, y + (self.BADGE_H - self.BADGE_FONT[1]) / 2 + 1.2, f"STEP {n}")
